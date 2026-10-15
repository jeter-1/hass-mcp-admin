"""HAMCP-181 PR1: every public surface reports one set of dispatch facts.

Design section 6 (PR1) row for this module:

* S16 staleness regression first: before PR1, a verified F3 reload left
  ``plan.operational.dispatch`` at ``dispatched:false`` / ``attempt_count:0``
  and ``operational.verification.status`` at ``not_run``.
* The Codex R2 probe (dashboard Core retirement after the saved intent) and
  the late setter-drift variant replayed through every public reader
  (S1-S13, S17, S20) at the full, compact (2,000) and minimal (1,024)
  receipt budgets and across a restart.
* #253 and V9 value identity (section 2.4.3), including the dashboard all-b1
  parent basis at one and three children and at every budget.
* 0-, 1- and 3-child receipts, each counter co-located with its basis.
* Operational phase (c) on every reader, including reconciliation and the
  admin page, and the post-intent cancel wording.
* Audit rider F-03: a duplicate apply after a pre-dispatch refusal makes no
  dispatch claim.

Every provider, registry and clock is synthetic and offline.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

import tests.test_f3_runtime_integration as integration  # noqa: E402
import tests.test_hamcp177_dashboard_write_admission as dashboard  # noqa: E402
from tests.hamcp069a_entity_fixtures import EntityMetadataServiceTestCase  # noqa: E402
import tests.test_beta37_exact_helper_state as helper_state  # noqa: E402
from tests.test_hamcp181_dispatch_facts import (  # noqa: E402
    INTENT_AT, FakeRecord, not_sent, r_proven, r_unproven, sent_verified, uncertain,
)
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.f3_runtime import dispatch_facts as df  # noqa: E402
from ha_mcp_engineering.governance.entity_metadata import (  # noqa: E402
    ENTITY_METADATA_NOT_DISPATCHED_OUTCOME,
)
from ha_mcp_engineering.governance.task_models import ExecutionTaskState  # noqa: E402
from ha_mcp_engineering.models.responses import SuccessResponse  # noqa: E402

logging.disable(logging.CRITICAL)

BUDGETS = (None, 2000, 1024)
# A counter is never shown without the basis that says what it counts
# (design section 2.4.3 item 6).
COUNTERS = {
    "provider_attempt_count": ("attempt_counts_basis",),
    "dispatch_count": ("attempt_counts_basis", "dispatch_count_basis"),
}
# The #253 no-call fields plus the PR1 additive phase and dispatched_at facts.
B1_FACTS = {
    **dashboard.NO_CALL_FIELDS,
    "dispatch_phase": df.PHASE_INTENT_RECORDED_NOT_SENT,
    "dispatched_at": None,
    "dispatched_at_basis": df.DISPATCHED_AT_CALL_NOT_SENT,
}
# The minimal receipt sheds the phase and the counter group, never these.
B1_MINIMAL_FACTS = {
    key: B1_FACTS[key]
    for key in (
        "provider_dispatch_occurred", "dispatch_performed", "setter_call_not_sent",
        "changed_by_engineering", "intent_recorded", "dispatched_at",
    )
}
DISPATCH_CLAIMS = ("already dispatched", "was dispatched", "after dispatch.")


def receipt(data, limit, operation="apply_change_plan"):
    response = SuccessResponse(operation=operation, summary="HAMCP-181 receipt", data=data)
    rendered = response.to_json(limit) if limit else response.to_json()
    if limit:
        assert len(rendered) <= limit, (limit, len(rendered))
    return json.loads(rendered)["data"]


def subset(surface, expected):
    return {key: surface.get(key, "<absent>") for key in expected}


class _CounterBasisMixin:
    def assert_counters_qualified(self, value, path="$"):
        """Every counter in ``value`` sits next to its basis."""

        if isinstance(value, dict):
            for counter, bases in COUNTERS.items():
                if counter in value:
                    self.assertTrue(
                        any(basis in value for basis in bases),
                        f"{path}.{counter} has no basis: {sorted(value)}",
                    )
            for key, item in value.items():
                self.assert_counters_qualified(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                self.assert_counters_qualified(item, f"{path}[{index}]")


class S16OperationalBlockTests(integration._OperationalActivationBase):
    """Section 7 item 6: the stale legacy block on F3 plans is projected truthfully."""

    asyncSetUp = integration.F3OperationalActivationTests.asyncSetUp

    async def _verified_reload(self):
        created = await self.service.create_reload_plan(reload_target="automation")
        plan = await self._grant(created)
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertEqual(self.lifecycle.dispatch_count, 1)
        return plan, result

    async def test_verified_reload_operational_dispatch_is_not_stale(self):
        plan, result = await self._verified_reload()
        public = self.service.get_plan(plan["plan_id"])
        public = public.get("plan", public)
        dispatch = public["operational"]["dispatch"]
        verification = public["operational"]["verification"]

        self.assertTrue(dispatch["dispatched"])
        self.assertEqual(dispatch["attempt_count"], 1)
        self.assertTrue(dispatch["provider_response_received"])
        self.assertEqual(dispatch["projection_source"], "f3_dispatch_facts")
        self.assertEqual(
            dispatch["dispatch_phase"], "sent_response_received"
        )
        self.assertEqual(dispatch["attempt_counts_basis"], "saved_dispatch_intent")
        self.assertEqual(verification["status"], "verified")
        self.assertEqual(
            verification["projection_source"], "execution_task.verification_summary"
        )
        # Every surface agrees with the execution-task projection.
        task = public["execution_task"]
        self.assertTrue(task["provider_dispatch_occurred"])
        self.assertEqual(task["dispatch_phase"], dispatch["dispatch_phase"])
        self.assertTrue(result["provider_dispatch_occurred"])
        self.assertEqual(result["dispatch_phase"], dispatch["dispatch_phase"])

    async def test_verified_reload_survives_restart_without_stale_block(self):
        plan, _ = await self._verified_reload()
        await self.runtime.recover_once("restart")
        public = self.service.get_plan(plan["plan_id"])
        public = public.get("plan", public)
        self.assertTrue(public["operational"]["dispatch"]["dispatched"])
        self.assertEqual(public["operational"]["verification"]["status"], "verified")

    async def test_stored_plan_operational_block_is_not_rewritten(self):
        # Display only: the stored legacy block keeps its persisted values.
        plan, _ = await self._verified_reload()
        stored = self.service._load(plan["plan_id"])
        self.assertFalse(stored.operational.dispatch.get("dispatched"))
        self.assertEqual(stored.operational.verification.status, "not_run")


class DashboardNotSentSurfaceTests(_CounterBasisMixin, dashboard.Harness):
    """The Codex R2 probe and the late-drift variant on every public reader."""

    async def core_retirement(self, name="plans"):
        peer = dashboard.DashboardPeer(dashboard.catalog(dashboard.EXTRAS))
        _p, service, runtime, created = await self.planned(peer, name)
        self.telemetry.core_dispatch_authorizer = self.authorizer

        async def retire(count):
            self.core_ok = False
        peer.hooks[dashboard.GUIDE] = retire
        result = await service.apply(created["plan_id"], created["plan_hash"])
        self.assertNotIn(dashboard.SETTER, peer.names())
        return peer, service, runtime, created, result

    async def late_drift(self, name):
        peer = dashboard.DashboardPeer(dashboard.catalog(dashboard.EXTRAS))
        _p, service, runtime, created = await self.planned(peer, name)
        peer.tools_after_guide = dashboard.drifted(
            dashboard.catalog(dashboard.EXTRAS), dashboard.SETTER, "inputSchema")
        result = await service.apply(created["plan_id"], created["plan_hash"])
        self.assertNotIn(dashboard.SETTER, peer.names())
        return peer, service, runtime, created, result

    def assert_readers(self, service, runtime, created, result, name):
        plan_id, task_id = created["plan_id"], result["task_id"]
        # S1 apply top level, S2 its execution_task, S3 its nested plan task.
        nested = result["plan"]["execution_task"]
        for label, surface in (("S1", result), ("S2", result["execution_task"]),
                               ("S3", nested)):
            with self.subTest(surface=label):
                self.assertEqual(subset(surface, B1_FACTS), B1_FACTS)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["task_state"], "failed_post_dispatch")
        # S4 plan reader.
        plan = service.get_plan(plan_id)
        plan = plan.get("plan", plan)
        self.assertEqual(subset(plan["execution_task"], B1_FACTS), B1_FACTS)
        # S5 plan list (the summary keeps the dispatch flag).
        [listed] = [item for item in service.list_plans(None, 10)["plans"]
                    if item["plan_id"] == plan_id]
        self.assertIs(listed["execution_task"]["provider_dispatch_occurred"], False)
        # S6 task list and S7 task reader.
        [row] = [item for item in service.list_execution_tasks()["tasks"]
                 if item["task_id"] == task_id]
        view = service.get_execution_task(task_id)
        for label, surface in (("S6", row), ("S7", view)):
            with self.subTest(surface=label):
                self.assertEqual(subset(surface, B1_FACTS), B1_FACTS)
                self.assertEqual(surface["state"], "failed_post_dispatch")
        # S8 observability reader.
        observed = service.get_plan_observability(plan_id)
        self.assertEqual(subset(observed["execution_task"], {
            "provider_dispatch_occurred": False}), {"provider_dispatch_occurred": False})
        # S20 nested counters keep their stored values and gain the basis.
        for label, surface in (("S2", result["execution_task"]), ("S7", view)):
            with self.subTest(nested=label):
                self.assertEqual(surface["provider_attempt_count"], 1)
                [attempt] = surface["provider_attempts"]
                self.assertEqual(attempt["dispatch_count"], 1)
                self.assertEqual(attempt["attempt_counts_basis"], df.BASIS_NO_SETTER_CALL)
                [summary_child] = surface["verification_summary"]["children"]
                self.assertEqual(summary_child["dispatch_count"], 1)
                self.assertEqual(summary_child["attempt_counts_basis"],
                                 df.BASIS_NO_SETTER_CALL)
                self.assert_counters_qualified(surface)
        [child_row] = view["f3_children"]
        self.assertEqual(child_row["dispatch_count"], 1)
        self.assertEqual(child_row["dispatch_count_basis"], df.BASIS_NO_SETTER_CALL)
        self.assertEqual(child_row["dispatch_phase"], df.PHASE_INTENT_RECORDED_NOT_SENT)
        self.assertEqual(child_row["dispatched_at_basis"], df.DISPATCHED_AT_CALL_NOT_SENT)
        # S17 health: one parent counted under its aggregate phase.
        counts = service.health_summary()["execution_tasks"]["dispatch_outcome_counts"]
        self.assertEqual(counts["failed_after_intent_not_dispatched"], 1)
        self.assertEqual(sum(counts.values()), 1)
        self.assertEqual(runtime.health()["dispatch_outcome_counts"][
            "failed_after_intent_not_dispatched"], 1)
        # S12 lifecycle audit and S13 child audit.
        entries = self.audit(name)
        lifecycle = [entry for entry in entries
                     if entry.get("event") in {"dispatch_attempted", "task_failed_post_dispatch"}]
        self.assertTrue(lifecycle)
        for entry in lifecycle:
            self.assertIs(entry["provider_dispatch_occurred"], False, entry)
            self.assertEqual(entry["dispatch_phase"], df.PHASE_INTENT_RECORDED_NOT_SENT)
        [child_audit] = [entry for entry in entries
                         if entry.get("outcome") == "dispatch_result_recorded"]
        self.assertIs(child_audit["setter_call_not_sent"], True)
        self.assertIs(child_audit["dispatch_performed"], False)
        self.assertEqual(child_audit["dispatch_phase"], df.PHASE_INTENT_RECORDED_NOT_SENT)
        # No surface claims a send anywhere in the rendered output.
        for surface in (result, view, row, plan["execution_task"]):
            self.assertNotIn('"provider_dispatch_occurred": true', json.dumps(surface))
        return view

    def assert_receipts(self, result, view):
        for limit in BUDGETS:
            for operation, data in (("apply_change_plan", result),
                                    ("get_execution_task", view)):
                with self.subTest(limit=limit, operation=operation):
                    body = receipt(data, limit, operation)
                    self.assert_counters_qualified(body)
                    if limit == 1024:
                        self.assertEqual(subset(body, B1_MINIMAL_FACTS), B1_MINIMAL_FACTS)
                        # The counter group is shed together, never split.
                        self.assertEqual("provider_attempt_count" in body,
                                         "attempt_counts_basis" in body)
                    else:
                        self.assertEqual(subset(body, B1_FACTS), B1_FACTS)
                    self.assertIs(body["provider_dispatch_occurred"], False)

    async def assert_refusals(self, service, created, result):
        # S10 duplicate apply: no repeat, phase-specific wording, same facts.
        with self.assertRaises(GovernanceError) as duplicate:
            await service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(duplicate.exception.code, ErrorCode.DUPLICATE_APPLY_ATTEMPT)
        self.assertIn("the provider call was not sent", str(duplicate.exception))
        self.assertEqual(subset(duplicate.exception.details, B1_FACTS), B1_FACTS)
        # S11 cancel: the intent was recorded, so the refusal says so.
        with self.assertRaises(GovernanceError) as cancel:
            await service.cancel_execution_task(result["task_id"])
        self.assertEqual(cancel.exception.code,
                         ErrorCode.CANCELLATION_NOT_PERMITTED_AFTER_DISPATCH)
        self.assertEqual(str(cancel.exception),
                         "An execution task cannot be cancelled after dispatch intent was recorded.")
        self.assertEqual(subset(cancel.exception.details, B1_FACTS), B1_FACTS)
        for message in (str(duplicate.exception), str(cancel.exception)):
            for claim in DISPATCH_CLAIMS:
                self.assertNotIn(claim, message)

    async def test_core_retirement_probe_reports_one_fact_set_on_every_surface(self):
        peer, service, runtime, created, result = await self.core_retirement()
        view = self.assert_readers(service, runtime, created, result, "plans")
        self.assert_receipts(result, view)
        await self.assert_refusals(service, created, result)
        self.assertNotIn(dashboard.SETTER, peer.names())
        self.assertEqual(peer.configuration["title"], "Before")

    async def test_late_setter_drift_reports_the_same_facts(self):
        peer, service, runtime, created, result = await self.late_drift("late")
        view = self.assert_readers(service, runtime, created, result, "late")
        self.assert_receipts(result, view)
        await self.assert_refusals(service, created, result)
        self.assertEqual(peer.configuration["title"], "Before")

    async def test_facts_are_identical_after_a_restart(self):
        peer, service, _runtime, created, result = await self.core_retirement()
        before = service.get_execution_task(result["task_id"])
        _provider, restarted, runtime = await self.stack(peer, "plans")
        after = restarted.get_execution_task(result["task_id"])
        self.assertEqual(subset(after, B1_FACTS), B1_FACTS)
        self.assertEqual(subset(after, B1_FACTS), subset(before, B1_FACTS))
        self.assertEqual(after["f3_children"], before["f3_children"])
        self.assertEqual(after["provider_attempts"], before["provider_attempts"])
        plan = restarted.get_plan(created["plan_id"])
        plan = plan.get("plan", plan)
        self.assertEqual(subset(plan["execution_task"], B1_FACTS), B1_FACTS)
        self.assertEqual(runtime.health()["dispatch_outcome_counts"][
            "failed_after_intent_not_dispatched"], 1)
        with self.assertRaises(GovernanceError) as duplicate:
            await restarted.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(subset(duplicate.exception.details, B1_FACTS), B1_FACTS)
        self.assertNotIn(dashboard.SETTER, peer.names())


class AllB1ParentReceiptTests(_CounterBasisMixin, unittest.TestCase):
    """Section 2.4.3: the #253 parent basis at one and three children, every budget."""

    @staticmethod
    def b1():
        return FakeRecord(operation=df.DASHBOARD_CAPABILITY_ID,
                          outcome="dispatch_failed_confirmed",
                          codes=(df.SETTER_CALL_NOT_SENT,))

    def apply_result(self, records):
        children = [df.child_facts(record, child_id=f"child-{index}",
                                   capability=df.DASHBOARD_CAPABILITY_ID)
                    for index, record in enumerate(records)]
        task = SimpleNamespace(dispatched_at=INTENT_AT)
        facts = df.dispatch_facts(task=task, children=children)
        attempts = [{"attempt": index + 1, "child_execution_id": f"child-{index}",
                     "dispatch_count": 1, "response_received": False}
                    for index in range(len(records))]
        execution_task = df.qualify_counters({
            "task_id": "task-b1", "state": "failed_post_dispatch",
            "terminal_outcome": "failed_post_dispatch",
            "provider_attempt_count": len(records),
            "provider_attempts": attempts,
            "verification_summary": {"status": "failed_post_dispatch", "children": [
                {"child_execution_id": f"child-{index}", "dispatch_count": 1}
                for index in range(len(records))]},
            **facts.public_fields(),
        }, facts)
        return {
            "status": "accepted", "task_id": "task-b1", "task_state": "failed_post_dispatch",
            "task_reused": False, "redispatch_performed": False,
            "plan_id": "plan-b1", "plan_hash": "b" * 64,
            "execution_task": execution_task, **facts.public_fields(),
        }

    def test_basis_is_kept_at_one_and_three_children_at_every_budget(self):
        for count in (1, 3):
            result = self.apply_result([self.b1() for _ in range(count)])
            for limit in BUDGETS:
                with self.subTest(children=count, limit=limit):
                    body = receipt(result, limit)
                    expected = B1_MINIMAL_FACTS if limit == 1024 else B1_FACTS
                    self.assertEqual(subset(body, expected), expected)
                    self.assert_counters_qualified(body)
                    if "attempt_counts_basis" in body:
                        self.assertEqual(body["attempt_counts_basis"], df.BASIS_NO_SETTER_CALL)

    def test_one_sent_child_removes_the_no_call_claim(self):
        result = self.apply_result([self.b1(), sent_verified(), self.b1()])
        for limit in BUDGETS:
            with self.subTest(limit=limit):
                body = receipt(result, limit)
                self.assertIs(body["provider_dispatch_occurred"], True)
                self.assertNotIn("setter_call_not_sent", body)
                self.assertNotEqual(body.get("attempt_counts_basis"), df.BASIS_NO_SETTER_CALL)


class MixedParentReceiptTests(_CounterBasisMixin, unittest.TestCase):
    """Section 2.4.3: a mixed parent follows the section 2.1 table at every budget."""

    def test_sent_r_proven_r_unproven_b_and_c_children(self):
        records = [sent_verified(), r_proven(), r_unproven(), not_sent(), uncertain()]
        children = [df.child_facts(record, child_id=f"child-{index}")
                    for index, record in enumerate(records)]
        self.assertEqual([child.phase for child in children], [
            df.PHASE_SENT_RESPONSE_RECEIVED, df.PHASE_SENT_REJECTED_NO_MUTATION,
            df.PHASE_SENT_FAILED_MUTATION_UNPROVEN, df.PHASE_INTENT_RECORDED_NOT_SENT,
            df.PHASE_SEND_UNCERTAIN])
        facts = df.dispatch_facts(task=SimpleNamespace(dispatched_at=INTENT_AT),
                                  children=children)
        execution_task = df.qualify_counters({
            "task_id": "task-mixed", "state": "manual_review_required",
            "terminal_outcome": "manual_review_required",
            "provider_attempt_count": len(records),
            "provider_attempts": [{"attempt": index + 1, "dispatch_count": 1}
                                  for index in range(len(records))],
            **facts.public_fields(),
        }, facts)
        result = {
            "status": "accepted", "task_id": "task-mixed",
            "task_state": "manual_review_required", "redispatch_performed": False,
            "plan_id": "plan-mixed", "plan_hash": "c" * 64,
            "execution_task": execution_task, **facts.public_fields(),
        }
        self.assertEqual(result["child_dispatch_phases"],
                         {child.phase: 1 for child in children})
        for limit in BUDGETS:
            with self.subTest(limit=limit):
                body = receipt(result, limit)
                self.assert_counters_qualified(body)
                self.assertIs(body["provider_dispatch_occurred"], True)
                # Neither a no-call, a no-change nor a rejection claim survives.
                for claim in ("setter_call_not_sent", df.PROVIDER_CALL_NOT_SENT,
                              "changed_by_engineering", "outcome_code", "result_text"):
                    self.assertNotIn(claim, body)
                if "attempt_counts_basis" in body:
                    self.assertEqual(body["attempt_counts_basis"], df.BASIS_SAVED_INTENT)
                if limit != 1024:
                    self.assertEqual(body["dispatch_phase"], df.PHASE_SEND_UNCERTAIN)
                    self.assertEqual(body["dispatched_at"], INTENT_AT)
                    self.assertEqual(body["dispatched_at_basis"],
                                     df.DISPATCHED_AT_INTENT_COMMIT_TIME)


class HelperStateNoOpStatusTests(helper_state.ExactHelperStateRuntimeTests):
    """Section 2.4.3 item 4: a generic (a) keeps ``status`` in PR1."""

    async def test_generic_no_op_status_is_unchanged_at_apply_and_replay(self):
        plan = await self.create_and_grant()
        self.helper.set_observed_state("on", self.clock)
        applied = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        replayed = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(self.helper.dispatch_count, 0)
        additive = {
            "provider_dispatch_occurred": False,
            "dispatch_phase": df.PHASE_ALREADY_PRESENT,
            "outcome_code": ENTITY_METADATA_NOT_DISPATCHED_OUTCOME,
            "changed_by_engineering": False,
            "intent_recorded": False,
            "dispatch_performed": False,
            "attempt_counts_basis": df.BASIS_NO_INTENT,
            "dispatched_at": None,
            "dispatched_at_basis": df.DISPATCHED_AT_NO_INTENT,
        }
        for label, result, status in (("apply", applied, "applied"),
                                      ("replay", replayed, "already_applied")):
            with self.subTest(surface=label):
                # PR1 keeps the generic status; PR2 owns "already_present".
                self.assertEqual(result["status"], status)
                self.assertEqual(result["task_state"], "succeeded_verified")
                task = self.service.get_execution_task(result["task_id"])
                self.assertEqual(task["state"], "succeeded_verified")
                self.assertEqual(task["terminal_outcome"], "succeeded_verified")
                self.assertEqual(subset(result, additive), additive)
                self.assertEqual(subset(task, additive), additive)
                self.assertIn("already present", result["result_text"])


for _name in [name for name in dir(helper_state.ExactHelperStateRuntimeTests)
              if name.startswith("test_")]:
    setattr(HelperStateNoOpStatusTests, _name, None)


class ZeroChildReceiptTests(_CounterBasisMixin, unittest.TestCase):
    """A parent whose child manifest cannot be established counts as one (c)."""

    def test_unestablished_manifest_is_uncertain_at_every_budget(self):
        for children in (None, []):
            facts = df.dispatch_facts(task=SimpleNamespace(dispatched_at=INTENT_AT),
                                      children=children)
            result = {
                "status": "accepted", "task_id": "task-0", "task_state": "observing",
                "plan_id": "plan-0", "plan_hash": "0" * 64, "redispatch_performed": False,
                "execution_task": df.qualify_counters({
                    "task_id": "task-0", "state": "observing",
                    "provider_attempt_count": 1,
                    "provider_attempts": [{"attempt": 1, "dispatch_count": 1}],
                    **facts.public_fields()}, facts),
                **facts.public_fields(),
            }
            for limit in BUDGETS:
                with self.subTest(children=children, limit=limit):
                    body = receipt(result, limit)
                    self.assertIs(body["provider_dispatch_occurred"], True)
                    self.assertNotIn("setter_call_not_sent", body)
                    self.assertNotIn(df.PROVIDER_CALL_NOT_SENT, body)
                    self.assert_counters_qualified(body)
                    if limit != 1024:
                        self.assertEqual(body["dispatch_phase"], df.PHASE_SEND_UNCERTAIN)
                        self.assertEqual(body["dispatched_at"], INTENT_AT)
                        self.assertEqual(body["dispatched_at_basis"],
                                         df.DISPATCHED_AT_INTENT_COMMIT_TIME)


class ConfigurationReceiptTests(_CounterBasisMixin, integration.F3ConfigurationActivationTests):
    """One- and three-child configuration receipts and the configuration F-03 case."""

    async def apply(self, created):
        await self.approve(created)
        return await self.service.apply(created["plan_id"], created["plan_hash"])

    def assert_sent_receipts(self, result, children):
        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["child_dispatch_phases"],
                         {df.PHASE_SENT_RESPONSE_RECEIVED: children})
        view = self.service.get_execution_task(result["task_id"])
        # Section 2.4.3: stored counters keep their values.
        self.assertEqual(view["provider_attempt_count"], children)
        self.assertEqual([row["dispatch_count"] for row in view["f3_children"]],
                         [1] * children)
        for limit in BUDGETS:
            for operation, data in (("apply_change_plan", result),
                                    ("get_execution_task", view)):
                with self.subTest(children=children, limit=limit, operation=operation):
                    body = receipt(data, limit, operation)
                    self.assert_counters_qualified(body)
                    self.assertIs(body["provider_dispatch_occurred"], True)
                    if limit is None:
                        self.assertEqual(body["dispatched_at_basis"],
                                         df.DISPATCHED_AT_INTENT_COMMIT_TIME)
                    # The full apply result keeps the counter on its task.
                    counted = body if "provider_attempt_count" in body else body["execution_task"]
                    self.assertEqual(counted["provider_attempt_count"], children)
                    self.assertEqual(counted["attempt_counts_basis"], df.BASIS_SAVED_INTENT)
                    self.assertEqual(counted["attempt_count_scope"], "parent_sum_of_child_intents")
                    self.assertEqual(body["attempt_counts_basis"], df.BASIS_SAVED_INTENT)
                    self.assertEqual(body["dispatch_phase"], df.PHASE_SENT_RESPONSE_RECEIVED)

    async def test_one_child_receipts(self):
        result = await self.apply(await self.create_automation_plan())
        self.assert_sent_receipts(result, 1)

    async def test_three_child_receipts(self):
        result = await self.apply(await self.create_hvac_plan())
        self.assert_sent_receipts(result, 3)

    async def test_configuration_duplicate_after_pre_dispatch_refusal_claims_no_dispatch(self):
        historical = await self.create_automation_plan()
        await self.approve(historical)
        legacy_task = self.service._create_task_for_plan(
            self.service._load(historical["plan_id"]), historical["plan_hash"])
        self.service._record_task_event(
            legacy_task, "preflight_started", new_state=ExecutionTaskState.PREFLIGHT,
            changes={"started_at": self.service._timestamp()})
        current = await self.create_automation_plan()
        result = await self.apply(current)
        self.assertEqual(result["task_state"], "failed_pre_dispatch")
        with self.assertRaises(GovernanceError) as duplicate:
            await self.service.apply(current["plan_id"], current["plan_hash"])
        F03Assertions.assert_no_dispatch_claim(self, duplicate.exception)
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), 0)


# The inherited activation tests already run in their own module.
for _name in [name for name in dir(integration.F3ConfigurationActivationTests)
              if name.startswith("test_")]:
    setattr(ConfigurationReceiptTests, _name, None)


class F03Assertions:
    @staticmethod
    def assert_no_dispatch_claim(case, error):
        case.assertEqual(error.code, ErrorCode.DUPLICATE_APPLY_ATTEMPT)
        message = str(error)
        case.assertEqual(
            message,
            "The operational apply already ended without a dispatch intent "
            "and will not be repeated.",
        )
        for claim in DISPATCH_CLAIMS + ("single dispatch intent", "recorded outcome"):
            case.assertNotIn(claim, message)
        details = error.details
        case.assertIs(details["provider_dispatch_occurred"], False)
        # Pre-intent objects carry the phase, not the detail fields (section 2.2).
        case.assertNotIn("intent_recorded", details)
        case.assertEqual(details["dispatch_phase"], df.PHASE_PRE_INTENT)
        case.assertEqual(details["attempt_counts_basis"], df.BASIS_NO_INTENT)
        case.assertIsNone(details["dispatched_at"])
        case.assertEqual(details["dispatched_at_basis"], df.DISPATCHED_AT_NO_INTENT)
        case.assertEqual(details["task_state"], "failed_pre_dispatch")


class OperationalSurfaceTests(_CounterBasisMixin, integration._OperationalActivationBase):
    """Operational phases on every reader, cancel wording and F-03."""

    asyncSetUp = integration.F3OperationalActivationTests.asyncSetUp

    async def granted(self, created):
        return await self._grant(created)

    def assert_phase_everywhere(self, result, phase, pdo):
        view = self.service.get_execution_task(result["task_id"])
        plan = self.service.get_plan(result["execution_task"]["plan_id"])
        plan = plan.get("plan", plan)
        surfaces = {"S1": result, "S2": result["execution_task"],
                    "S3": result["plan"]["execution_task"], "S4": plan["execution_task"],
                    "S7": view}
        for label, surface in surfaces.items():
            with self.subTest(surface=label):
                self.assertEqual(surface["dispatch_phase"], phase)
                self.assertIs(surface["provider_dispatch_occurred"], pdo)
        for row in view["f3_children"]:
            self.assertEqual(row["dispatch_phase"], phase)
        self.assert_counters_qualified(view)
        return view

    async def test_verified_reload_keeps_every_existing_value(self):
        plan = await self.granted(await self.service.create_reload_plan(reload_target="automation"))
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        view = self.assert_phase_everywhere(result, df.PHASE_SENT_RESPONSE_RECEIVED, True)
        # Section 2.4.3: status, state and counters are value-identical.
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertEqual(view["provider_attempt_count"], 1)
        self.assertEqual(view["f3_children"][0]["dispatch_count"], 1)
        self.assertEqual(self.lifecycle.dispatch_count, 1)
        # Section 2.2: a sent phase keeps today's fields; no detail fields.
        for detail in ("changed_by_engineering", "intent_recorded", "dispatch_performed"):
            self.assertNotIn(detail, result)
            self.assertNotIn(detail, view)
        self.assertEqual(view["dispatched_at"], view["dispatch_intent_recorded_at"])
        self.assertEqual(view["dispatched_at_basis"], df.DISPATCHED_AT_INTENT_COMMIT_TIME)

    async def test_lost_reload_response_is_uncertain_on_every_reader_and_reconciliation(self):
        self.lifecycle.mode = "ambiguous"
        plan = await self.granted(await self.service.create_reload_plan(reload_target="automation"))
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(result["task_state"], "observing")
        self.assert_phase_everywhere(result, df.PHASE_SEND_UNCERTAIN, True)
        # (c) adds no detail field and never claims a non-change.
        self.assertNotIn("changed_by_engineering", result)
        self.assertNotIn(df.PROVIDER_CALL_NOT_SENT, result)
        self.clock.advance(seconds=901)
        await self.runtime.recover_once("hamcp181_surface_sweep")
        view = self.service.get_execution_task(result["task_id"])
        self.assertEqual(view["state"], "manual_review_required")
        self.assertEqual(view["dispatch_phase"], df.PHASE_SEND_UNCERTAIN)
        # S19 reconciliation items carry the same phase and a qualified counter.
        [item] = self.runtime.reconciliation_items()
        self.assertEqual(item["dispatch_phase"], df.PHASE_SEND_UNCERTAIN)
        self.assertEqual(item["dispatch_count"], 1)
        self.assertEqual(item["attempt_counts_basis"], df.BASIS_SAVED_INTENT)
        # S18 admin page renders the qualified counter label and the phase.
        body = await self.render_admin_page(item["child_id"])
        self.assertIn("Dispatch intents", body)
        self.assertIn("Dispatch phase", body)
        self.assertIn(df.PHASE_SEND_UNCERTAIN, body)
        self.assertNotIn("Dispatch count", body)
        # S11 cancel names the recorded intent and carries the facts.
        with self.assertRaises(GovernanceError) as cancel:
            await self.service.cancel_execution_task(result["task_id"])
        self.assertEqual(str(cancel.exception),
                         "An execution task cannot be cancelled after dispatch intent was recorded.")
        self.assertEqual(cancel.exception.details["dispatch_phase"], df.PHASE_SEND_UNCERTAIN)
        self.assertEqual(self.lifecycle.dispatch_count, 1)

    async def render_admin_page(self, child_id):
        import httpx

        from ha_mcp_engineering.approval_web import create_approval_application

        app = create_approval_application(integration._IngressGovernanceStub(self.runtime))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("172.30.32.2", 12345)),
            base_url="http://f3-ingress.local",
            headers={"X-Ingress-Path": "/api/hassio_ingress/f3fixture",
                     "X-Remote-User-Id": "admin-fixture"},
        ) as client:
            response = await client.get(f"/f3/{child_id}")
        self.assertEqual(response.status_code, 200, response.text[:500])
        return response.text

    async def test_post_intent_cancel_wording_and_facts(self):
        plan = await self.granted(await self.service.create_home_assistant_restart_plan())
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(result["task_state"], "observing")
        with self.assertRaises(GovernanceError) as cancel:
            await self.service.cancel_execution_task(result["task_id"])
        self.assertEqual(cancel.exception.code,
                         ErrorCode.CANCELLATION_NOT_PERMITTED_AFTER_DISPATCH)
        self.assertEqual(str(cancel.exception),
                         "An execution task cannot be cancelled after dispatch intent was recorded.")
        self.assertIs(cancel.exception.details["provider_dispatch_occurred"], True)
        self.assertEqual(cancel.exception.details["dispatch_phase"],
                         df.PHASE_SENT_RESPONSE_RECEIVED)
        self.assertEqual(cancel.exception.details["attempt_counts_basis"],
                         df.BASIS_SAVED_INTENT)
        self.assertEqual(self.lifecycle.dispatch_count, 1)

    async def legacy_conflict(self):
        historical = await self.granted(
            await self.service.create_reload_plan(reload_target="automation"))
        legacy_task = self.service._create_task_for_plan(
            self.service._load(historical["plan_id"]), historical["plan_hash"])
        self.service._record_task_event(
            legacy_task, "preflight_started", new_state=ExecutionTaskState.PREFLIGHT,
            changes={"started_at": self.service._timestamp()})
        current = await self.granted(
            await self.service.create_reload_plan(reload_target="automation"))
        result = await self.service.apply(current["plan_id"], current["plan_hash"])
        self.assertEqual(result["task_state"], "failed_pre_dispatch")
        return current, result

    async def test_f03_duplicate_after_pre_dispatch_refusal_claims_no_dispatch(self):
        current, result = await self.legacy_conflict()
        self.assert_phase_everywhere(result, df.PHASE_PRE_INTENT, False)
        with self.assertRaises(GovernanceError) as duplicate:
            await self.service.apply(current["plan_id"], current["plan_hash"])
        F03Assertions.assert_no_dispatch_claim(self, duplicate.exception)
        # The same refusal after a restart keeps the same wording.
        await self.runtime.recover_once("restart")
        with self.assertRaises(GovernanceError) as again:
            await self.service.apply(current["plan_id"], current["plan_hash"])
        F03Assertions.assert_no_dispatch_claim(self, again.exception)
        self.assertEqual(self.lifecycle.dispatch_count, 0)
        # Its receipt at every budget makes no dispatch claim either.
        for limit in BUDGETS:
            with self.subTest(limit=limit):
                body = receipt(result, limit)
                self.assertIs(body["provider_dispatch_occurred"], False)
                self.assertNotIn("intent_recorded", body)
                self.assertIsNone(body["dispatched_at"])
                if limit != 1024:
                    self.assertEqual(body["dispatch_phase"], df.PHASE_PRE_INTENT)
                    self.assertEqual(body["dispatched_at_basis"], df.DISPATCHED_AT_NO_INTENT)

    async def test_cancel_after_pre_dispatch_refusal_names_no_intent(self):
        _current, result = await self.legacy_conflict()
        with self.assertRaises(GovernanceError) as cancel:
            await self.service.cancel_execution_task(result["task_id"])
        # The stored error code is unchanged; only the wording is truthful.
        self.assertEqual(cancel.exception.code,
                         ErrorCode.CANCELLATION_NOT_PERMITTED_AFTER_DISPATCH)
        self.assertEqual(
            str(cancel.exception),
            "The execution task can no longer be cancelled in its current state; "
            "no dispatch intent was recorded.",
        )
        self.assertEqual(cancel.exception.details["dispatch_phase"], df.PHASE_PRE_INTENT)
        self.assertEqual(self.lifecycle.dispatch_count, 0)


class V9ValueIdentityTests(_CounterBasisMixin, EntityMetadataServiceTestCase):
    """Section 2.4.3: the 069A V9 fields keep their values; PR1 only adds facts."""

    TARGET = "light.porch"
    TEXT = ("desired state already present; not dispatched. "
            "Engineering did not make this change.")

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.add_entity(self.TARGET)

    async def already_present(self):
        created = await self.create(entity_id=self.TARGET, name="Front porch")
        await self.approve(created)
        self.ws.set_external(self.TARGET, name="Front porch")
        return created, await self.apply(created)

    def assert_v9(self, surface):
        self.assertEqual(surface["outcome_code"], ENTITY_METADATA_NOT_DISPATCHED_OUTCOME)
        self.assertIs(surface["dispatch_performed"], False)
        self.assertEqual(surface["result_text"], self.TEXT)
        self.assertIs(surface["provider_dispatch_occurred"], False)
        self.assertIs(surface["changed_by_engineering"], False)
        self.assertEqual(surface["dispatch_phase"], df.PHASE_ALREADY_PRESENT)
        self.assertIs(surface["intent_recorded"], False)
        self.assertEqual(surface["attempt_counts_basis"], df.BASIS_NO_INTENT)

    async def test_v9_values_are_identical_and_facts_are_additive(self):
        created, result = await self.already_present()
        self.assertEqual(result["status"], "already_present")
        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assert_v9(result)
        task = result["execution_task"]
        self.assertEqual(task["terminal_outcome"], ENTITY_METADATA_NOT_DISPATCHED_OUTCOME)
        self.assertIsNone(task["dispatched_at"])
        self.assertEqual(task["dispatched_at_basis"], df.DISPATCHED_AT_NO_INTENT)
        self.assertEqual(task["provider_attempts"], [])
        summary = task["verification_summary"]
        self.assertEqual(summary["outcome_code"], ENTITY_METADATA_NOT_DISPATCHED_OUTCOME)
        self.assertEqual(summary["result_text"], self.TEXT)
        self.assertEqual([child["dispatch_count"] for child in summary["children"]], [0])
        view = self.service.get_execution_task(result["task_id"])
        self.assert_v9(view)
        self.assert_counters_qualified(view)
        replay = await self.apply(created)
        self.assertEqual(replay["status"], "already_present")
        self.assert_v9(replay)
        for limit in BUDGETS:
            with self.subTest(limit=limit):
                body = receipt(result, limit)
                self.assertIs(body["provider_dispatch_occurred"], False)
                self.assertIs(body["dispatch_performed"], False)
                self.assertEqual(body["outcome_code"], ENTITY_METADATA_NOT_DISPATCHED_OUTCOME)
                self.assert_counters_qualified(body)
        counts = self.service.health_summary()["execution_tasks"]["dispatch_outcome_counts"]
        self.assertEqual(counts["already_present_not_dispatched"], 1)
        self.assertEqual(self.ws.updates(), [])


if __name__ == "__main__":
    unittest.main()
