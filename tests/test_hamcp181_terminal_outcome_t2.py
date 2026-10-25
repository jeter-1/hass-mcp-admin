"""HAMCP-181 PR2: one stored truthful outcome on every public surface (T2).

Design rev 4 section 6 (PR2) row for this module.  Three cases run through
the shipped service, the real F3 runtime and the real configuration gateways
against HAMCP-155's enforcing synthetic Core store (every REST request is
refused outside a Core-authority window, exactly as ``RestClient.request``
refuses it):

* (a) a successful dispatch;
* (b) a proven no-call after the durable intent (the executor's own
  ``after_durable_intent_before_provider_invocation`` boundary raises, so the
  provider call is never made);
* (c) an indeterminate dispatch (the request was made and its response was
  lost).

Each case is checked on every reader (S1-S8), every receipt budget, the
duplicate-apply and cancellation refusals, the task-lifecycle audit, the
``terminal_outcome`` filter and a restart, for a configuration delete and a
configuration update.  OD-6(ii): the send time is the provider-call start,
stored beside ``verification_summary.children`` with each child entry
unchanged, and never the intent commit time.  The no-blind-redispatch
invariant is asserted throughout: no case is ever sent twice.

Every provider, registry and clock is synthetic and offline.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from ha_mcp_engineering.errors import (  # noqa: E402
    ErrorCode,
    GovernanceError,
    HomeAssistantApiError,
)
from ha_mcp_engineering.f3.executor import SharedOperationExecutor  # noqa: E402
from ha_mcp_engineering.f3_runtime import dispatch_facts as df  # noqa: E402
from ha_mcp_engineering.governance.resources import resource_fingerprint  # noqa: E402
from ha_mcp_engineering.models.responses import SuccessResponse  # noqa: E402

from tests.core_authority_fakes import require_core_authority  # noqa: E402
from tests.hamcp155_fixtures import (  # noqa: E402
    CHECK_CONFIG,
    ConfigurationDeleteServiceTestCase,
    automation_config,
)

BUDGETS = (None, 2000, 1024)
INTENT_STAGE = "after_durable_intent_before_provider_invocation"
SEND_DELAY = timedelta(seconds=7)
# Facts every surface carries from the one shared projection.
FACTS = (
    "provider_dispatch_occurred",
    "dispatch_phase",
    "dispatched_at",
    "dispatched_at_basis",
    "changed_by_engineering",
    "outcome_code",
    "provider_call_not_sent",
)
# ``verification_summary.children`` entries keep their shipped shape.
CHILD_ENTRY_KEYS = {
    "child_execution_id",
    "operation_id",
    "ordinal",
    "state",
    "normalized_outcome",
    "dispatch_count",
    "terminal",
}
EXPECTED = {
    "a": {
        "state": "succeeded_verified",
        "terminal_outcome": "succeeded_verified",
        "plan_status": "applied",
        "verification": "verified",
        "rollback_available": True,
        "writes": 1,
        "facts": {
            "provider_dispatch_occurred": True,
            "dispatch_phase": df.PHASE_SENT_RESPONSE_RECEIVED,
            "dispatched_at_basis": df.DISPATCHED_AT_PROVIDER_CALL_START,
        },
    },
    "b": {
        "state": "failed_post_dispatch",
        "terminal_outcome": df.NOT_SENT_OUTCOME,
        "plan_status": "failed",
        "verification": "not_run",
        "rollback_available": False,
        "writes": 0,
        "facts": {
            "provider_dispatch_occurred": False,
            "dispatch_phase": df.PHASE_INTENT_RECORDED_NOT_SENT,
            "dispatched_at": None,
            "dispatched_at_basis": df.DISPATCHED_AT_CALL_NOT_SENT,
            "changed_by_engineering": False,
            "outcome_code": df.NOT_SENT_OUTCOME,
            "provider_call_not_sent": True,
        },
    },
    "c": {
        "state": "failed_post_dispatch",
        "terminal_outcome": "failed_post_dispatch",
        "plan_status": "failed",
        "verification": "not_run",
        "rollback_available": False,
        "writes": 1,
        "facts": {
            "provider_dispatch_occurred": True,
            "dispatch_phase": df.PHASE_SEND_UNCERTAIN,
            "dispatched_at": None,
            "dispatched_at_basis": df.DISPATCHED_AT_SEND_TIME_NOT_RECORDED,
        },
    },
}
# Wording that would contradict a proven no-call, or a send.
SEND_CLAIMS = ("may have been sent", "already dispatched", "was dispatched")


def receipt(data, limit, operation):
    response = SuccessResponse(operation=operation, summary="HAMCP-181 T2 receipt", data=data)
    rendered = response.to_json(limit) if limit else response.to_json()
    if limit:
        assert len(rendered) <= limit, (limit, len(rendered))
    return json.loads(rendered)["data"]


def subset(surface, keys):
    return {key: surface.get(key, "<absent>") for key in keys}


def unwrap(plan):
    return plan.get("plan", plan)


class T2Harness(ConfigurationDeleteServiceTestCase):
    """Drive one case and read every surface it reaches."""

    target = "porch_light"

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.add("automation", self.target)

    async def planned(self, family):
        if family == "delete":
            created = await self.create_one("automation", self.target)
        else:
            proposed = automation_config(self.target, target="light.hall")
            created = await self.service.create_configuration_plan(
                title="HAMCP-181 T2 update",
                description="Synthetic three-case agreement fixture",
                operations=[
                    {
                        "operation_id": "update_porch_light",
                        "resource_type": "automation",
                        "action": "update",
                        "target_id": self.target,
                        "depends_on": [],
                        "proposed_config": proposed,
                    }
                ],
            )
        await self.approve(created)
        return created

    def provider_writes(self):
        return self.config_rest.writes()

    async def run_case(self, case, family):
        created = await self.planned(family)
        original_inject = SharedOperationExecutor._inject
        self.intent_clock = self.clock[0]

        def inject(executor, stage):
            if stage == INTENT_STAGE:
                if case == "b":
                    raise RuntimeError("synthetic boundary failure after the intent")
                # The send happens strictly later than the intent commit.
                self.clock[0] = self.clock[0] + SEND_DELAY
            return original_inject(executor, stage)

        original_request = self.config_rest.request

        async def lose_response(method, path, **kwargs):
            # The configuration write leaves this process and its response is
            # lost before Core's answer arrives: nothing proves either way.
            if (method, str(path)) != CHECK_CONFIG and method == "POST":
                require_core_authority(self.config_rest, method, str(path))
                self.config_rest.requests.append((method, str(path)))
                raise HomeAssistantApiError(details={"provider_response_received": False})
            return await original_request(method, path, **kwargs)

        if case == "c" and family == "delete":
            self.config_rest.delete_failure = HomeAssistantApiError(
                details={"provider_response_received": False}
            )
        with mock.patch.object(SharedOperationExecutor, "_inject", inject):
            if case == "c" and family == "update":
                with mock.patch.object(self.config_rest, "request", lose_response):
                    result = await self.apply(created)
            else:
                result = await self.apply(created)
        return created, result

    # -- readers ---------------------------------------------------------

    def readers(self, created, result):
        plan = unwrap(self.service.get_plan(created["plan_id"]))
        [listed] = [
            item for item in self.service.list_plans(None, 10)["plans"]
            if item["plan_id"] == created["plan_id"]
        ]
        [row] = [
            item for item in self.service.list_execution_tasks()["tasks"]
            if item["task_id"] == result["task_id"]
        ]
        view = self.service.get_execution_task(result["task_id"])
        return plan, listed, row, view

    def assert_agreement(self, case, created, result):
        expected = EXPECTED[case]
        plan, listed, row, view = self.readers(created, result)
        reference = subset(view, FACTS)
        for key, value in expected["facts"].items():
            self.assertEqual(reference[key], value, (case, key, view))
        if case == "a":
            sent_at = (self.intent_clock + SEND_DELAY).isoformat()
            self.assertEqual(reference["dispatched_at"], sent_at)
            self.assertNotEqual(reference["dispatched_at"], self.intent_clock.isoformat())
        # S1-S7: every surface is the shared projection, value for value.
        task_surfaces = {
            "S2 apply.execution_task": (result["execution_task"], "state"),
            "S3 apply.plan.execution_task": (result["plan"]["execution_task"], "task_state"),
            "S4 get_change_plan.execution_task": (plan["execution_task"], "task_state"),
            "S5 list_change_plans.execution_task": (listed["execution_task"], "task_state"),
            "S6 list_execution_tasks": (row, "state"),
            "S7 get_execution_task": (view, "state"),
        }
        with self.subTest(case=case, surface="S1 apply"):
            self.assertEqual(subset(result, FACTS), reference)
            self.assertEqual(result["task_state"], expected["state"])
        for label, (surface, state_key) in task_surfaces.items():
            with self.subTest(case=case, surface=label):
                self.assertEqual(subset(surface, FACTS), reference)
                self.assertEqual(surface[state_key], expected["state"])
                self.assertEqual(surface["terminal_outcome"], expected["terminal_outcome"])
        # Plan level: the outcome, verification and rollback follow the task.
        for label, surface in (("apply.plan", result["plan"]), ("get_change_plan", plan),
                               ("list_change_plans", listed)):
            with self.subTest(case=case, plan_surface=label):
                self.assertEqual(surface["status"], expected["plan_status"])
                self.assertEqual(surface["execution_outcome"], expected["terminal_outcome"])
        summary = view["verification_summary"]
        for label, surface in (("apply.plan", result["plan"]), ("get_change_plan", plan)):
            with self.subTest(case=case, verification=label):
                self.assertEqual(surface["verification"]["status"], expected["verification"])
                self.assertEqual(
                    surface["verification"]["status"] == "verified",
                    summary["status"] == "verified",
                )
                self.assertIs(surface["rollback"]["available"], expected["rollback_available"])
        self.assertEqual(len(self.provider_writes()), expected["writes"])
        return plan, view

    def assert_receipts(self, case, result, plan, view):
        reference = subset(view, FACTS)
        for limit in BUDGETS:
            for operation, data in (("apply_change_plan", result),
                                    ("get_execution_task", view),
                                    ("get_change_plan", plan)):
                with self.subTest(case=case, limit=limit, operation=operation):
                    body = receipt(data, limit, operation)
                    if operation == "get_change_plan":
                        if "execution_outcome" in body:
                            self.assertEqual(body["execution_outcome"],
                                             EXPECTED[case]["terminal_outcome"])
                        # The smallest receipt lifts the task facts to the top.
                        body = body.get("execution_task", body)
                        self.assertEqual(body["task_id"], result["task_id"])
                        if limit == 1024:
                            self.assertEqual(body["dispatch_phase"], reference["dispatch_phase"])
                    else:
                        # The dispatch claim and the send time are never shed.
                        self.assertIn("provider_dispatch_occurred", body)
                        self.assertIn("dispatched_at", body)
                    for key in FACTS:
                        if key in body:
                            self.assertEqual(body[key], reference[key], key)
                    if "terminal_outcome" in body:
                        self.assertEqual(body["terminal_outcome"], EXPECTED[case]["terminal_outcome"])
                    if case == "b" and operation == "get_execution_task":
                        self.assertIn(df.NOT_SENT_OUTCOME, (
                            body.get("terminal_outcome"), body.get("outcome_code")))

    def assert_audit(self, case, view):
        entries = [json.loads(line) for line in self.audit_path.read_text().splitlines()]
        attempted = [entry for entry in entries if entry.get("event") == "dispatch_attempted"]
        terminal = [
            entry for entry in entries
            if entry.get("event") in {"task_completed", "task_failed_post_dispatch"}
        ]
        self.assertEqual(len(attempted), 1, attempted)
        self.assertEqual(len(terminal), 1, terminal)
        for entry in attempted + terminal:
            self.assertIs(entry["provider_dispatch_occurred"], view["provider_dispatch_occurred"])
            self.assertEqual(entry["dispatch_phase"], view["dispatch_phase"])
        self.assertEqual(terminal[0]["terminal_outcome"], EXPECTED[case]["terminal_outcome"])
        self.assertEqual(terminal[0]["task_state"], EXPECTED[case]["state"])

    def assert_filters(self, case, result):
        task_id = result["task_id"]

        def listed(**query):
            return {item["task_id"] for item in self.service.list_execution_tasks(**query)["tasks"]}

        self.assertIn(task_id, listed(terminal_outcome=EXPECTED[case]["terminal_outcome"]))
        self.assertIn(task_id, listed(state=EXPECTED[case]["state"]))
        if case == "b":
            # T2: the stored outcome is the discriminator; the state is unchanged.
            self.assertNotIn(task_id, listed(terminal_outcome="failed_post_dispatch"))

    async def assert_refusals(self, case, created, result):
        reference = subset(self.service.get_execution_task(result["task_id"]), FACTS)
        writes = len(self.provider_writes())
        try:
            replay = await self.apply(created)
        except GovernanceError as duplicate:
            self.assertEqual(duplicate.exception.code if hasattr(duplicate, "exception")
                             else duplicate.code, ErrorCode.DUPLICATE_APPLY_ATTEMPT)
            message = str(duplicate)
            details = duplicate.details
            for key in FACTS:
                if key in details:
                    self.assertEqual(details[key], reference[key], key)
            if case == "b":
                self.assertIn("the provider call was not sent", message)
            if case in {"a", "b"}:
                for claim in SEND_CLAIMS[:1]:
                    self.assertNotIn(claim, message)
        else:
            self.assertEqual(replay["task_id"], result["task_id"])
            self.assertEqual(subset(replay, FACTS), reference)
        with self.assertRaises(GovernanceError) as cancel:
            await self.service.cancel_execution_task(result["task_id"])
        if case in {"b", "c"}:
            self.assertEqual(cancel.exception.code,
                             ErrorCode.CANCELLATION_NOT_PERMITTED_AFTER_DISPATCH)
            self.assertEqual(
                str(cancel.exception),
                "An execution task cannot be cancelled after dispatch intent was recorded.",
            )
        if case == "b":
            for claim in SEND_CLAIMS:
                self.assertNotIn(claim, str(cancel.exception))
        # Never redispatched by a replay or a cancellation.
        self.assertEqual(len(self.provider_writes()), writes)

    async def assert_restart(self, case, result):
        before = self.service.get_execution_task(result["task_id"])
        await self.restart_runtime()
        await self.runtime.recover_once("restart")
        after = self.service.get_execution_task(result["task_id"])
        self.assertEqual(subset(after, FACTS), subset(before, FACTS))
        self.assertEqual(after["state"], before["state"])
        self.assertEqual(after["terminal_outcome"], before["terminal_outcome"])
        self.assertEqual(after["verification_summary"], before["verification_summary"])
        self.assertEqual(len(self.provider_writes()), EXPECTED[case]["writes"])

    def assert_send_time_storage(self, case, result):
        task = self.service.task_repository.get(result["task_id"])
        stored = task.verification_summary
        [child] = stored["children"]
        self.assertEqual(set(child), CHILD_ENTRY_KEYS)
        times = stored[df.SEND_TIME_SUMMARY_KEY]
        self.assertEqual(list(times), [child["child_execution_id"]])
        observed = (self.intent_clock + SEND_DELAY).isoformat()
        self.assertEqual(
            times[child["child_execution_id"]],
            None if case == "b" else observed,
        )

    async def check(self, case, family):
        created, result = await self.run_case(case, family)
        plan, view = self.assert_agreement(case, created, result)
        self.assert_receipts(case, result, plan, view)
        self.assert_audit(case, view)
        self.assert_filters(case, result)
        self.assert_send_time_storage(case, result)
        await self.assert_refusals(case, created, result)
        await self.assert_restart(case, result)
        return created, result


class DeleteAgreementTests(T2Harness):
    async def test_a_successful_delete(self):
        created, _result = await self.check("a", "delete")
        plan = unwrap(self.service.get_plan(created["plan_id"]))
        # HAMCP-214: the plan fingerprint is the readback's, and the desired
        # state of a delete is absence, never the delete intent's hash.
        absent = resource_fingerprint("automation", None)
        self.assertEqual(plan["post_apply_fingerprint"], absent)
        self.assertEqual(plan["verification"]["desired_fingerprint"], absent)
        self.assertEqual(plan["verification"]["actual_fingerprint"], absent)
        self.assertNotIn(self.target, self.config_rest.configs["automation"])

    async def test_b_proven_no_call_after_intent(self):
        await self.check("b", "delete")
        self.assertEqual(self.config_rest.deletes(), [])
        self.assertIn(self.target, self.config_rest.configs["automation"])
        counts = self.service.health_summary()["execution_tasks"]["dispatch_outcome_counts"]
        self.assertEqual(counts[df.NOT_SENT_OUTCOME], 1)

    async def test_c_indeterminate_delete(self):
        await self.check("c", "delete")
        self.assertEqual(len(self.config_rest.deletes()), 1)


class UpdateAgreementTests(T2Harness):
    async def test_a_successful_update(self):
        created, _result = await self.check("a", "update")
        plan = unwrap(self.service.get_plan(created["plan_id"]))
        self.assertEqual(plan["verification"]["desired_fingerprint"],
                         plan["verification"]["actual_fingerprint"])
        self.assertEqual(plan["post_apply_fingerprint"],
                         plan["verification"]["actual_fingerprint"])

    async def test_b_proven_no_call_after_intent(self):
        await self.check("b", "update")
        self.assertEqual(self.provider_writes(), [])

    async def test_c_indeterminate_update(self):
        await self.check("c", "update")


class RejectedDeleteTests(T2Harness):
    """P1: a 4xx DELETE was sent and rejected; it is never 'may have been sent'."""

    async def test_rejected_delete_is_sent_and_proven_unchanged(self):
        created = await self.planned("delete")
        self.config_rest.before_delete = lambda: self.config_rest.configs[
            "automation"
        ].pop(self.target)
        result = await self.apply(created)
        plan, listed, row, view = self.readers(created, result)
        reference = subset(view, FACTS)
        self.assertEqual(reference["dispatch_phase"], df.PHASE_SENT_REJECTED_NO_MUTATION)
        self.assertIs(reference["provider_dispatch_occurred"], True)
        self.assertIs(reference["changed_by_engineering"], False)
        self.assertEqual(reference["dispatched_at_basis"], df.DISPATCHED_AT_PROVIDER_CALL_START)
        for surface in (result, result["execution_task"], plan["execution_task"],
                        listed["execution_task"], row):
            self.assertEqual(subset(surface, FACTS), reference)
        with self.assertRaises(GovernanceError) as duplicate:
            await self.apply(created)
        self.assertNotIn("may have been sent", str(duplicate.exception))
        self.assertEqual(len(self.config_rest.deletes()), 1)


if __name__ == "__main__":
    unittest.main()
