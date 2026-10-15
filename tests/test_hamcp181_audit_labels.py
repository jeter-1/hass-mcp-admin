"""HAMCP-181 PR1 (design section 5.1): audit labels and child-audit facts.

* Configuration gateway rule: ``provider_response_received`` only when the
  exception proves a response; otherwise ``provider_result_uncertain``.
* Child and lifecycle audit facts per phase.
* The accepted operational/helper-state label limitation: the retained label
  for each of the four cases, shown next to the correct F3 phase.

All gateways, records and audit files are synthetic and offline.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

import tests.test_beta37_exact_helper_state as helper_fixtures  # noqa: E402
import tests.test_f3_runtime_integration as integration  # noqa: E402
from tests.test_hamcp181_dispatch_facts import (  # noqa: E402
    FakeRecord, already_present, not_sent, r_proven, r_unproven, sent_verified, uncertain,
)
from ha_mcp_engineering.f3_runtime import dispatch_facts as df  # noqa: E402
from ha_mcp_engineering.f3_runtime.runtime import _child_audit_facts  # noqa: E402
from ha_mcp_engineering.errors import GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.helper_state import HelperStateGatewayError  # noqa: E402
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    ConfigurationMutationNotDispatchedError,
)

logging.disable(logging.CRITICAL)

TARGET = ("automation", "apply_hvac_comfort")


def _rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def _events(rows, prefix):
    return [row for row in rows if row.get("event", "").startswith(prefix)]


class ConfigurationGatewayLabelTests(integration.ConfigurationPlanTestCase):
    """PR1 gateway-audit rule for the configuration gateway."""

    async def asyncSetUp(self):
        await integration.ConfigurationPlanTestCase.asyncSetUp(self)
        self.runtime = integration.F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=integration._ExactFakeConfigurationGateway(self.gateway),
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=integration._provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")

    async def apply_with(self, exception):
        created = await self.create_automation_plan()
        await self.approve(created)
        self.gateway.fail_write_target = TARGET
        self.gateway.fail_write_exception = exception
        result = await self.service.apply(created["plan_id"], created["plan_hash"])
        return result, _rows(self.audit_path)

    async def test_rest_4xx_p1_keeps_response_received(self):
        result, rows = await self.apply_with(ConfigurationMutationNotDispatchedError(details={
            "reason": "configuration_write_rejected",
            "provider_status": 400,
            "provider_response_received": True,
        }))
        labels = [row["event"] for row in _events(rows, "f3_provider_")]
        self.assertIn("f3_provider_response_received", labels)
        self.assertNotIn("f3_provider_result_uncertain", labels)
        child = _events(rows, "f3_dispatch_result_recorded")[-1]
        self.assertEqual(child["dispatch_phase"], df.PHASE_SENT_REJECTED_NO_MUTATION)
        self.assertIs(child["provider_dispatch_occurred"], True)
        self.assertIs(child["changed_by_engineering"], False)
        self.assertNotIn(df.PROVIDER_CALL_NOT_SENT, child)
        lifecycle = _events(rows, "task_failed_post_dispatch")[-1]
        self.assertIs(lifecycle["provider_dispatch_occurred"], True)
        # PR1 changes no status.
        self.assertEqual(result["task_state"], "failed_post_dispatch")
        self.assertEqual(result["outcome_code"], df.PROVIDER_REJECTED_MUTATION)
        # The rejected write was sent exactly once and never repeated.
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), 1)

    async def test_local_refusal_without_response_is_uncertain(self):
        # ``mutation_dispatched is False`` alone (b2-L shape) is not a response.
        result, rows = await self.apply_with(ConfigurationMutationNotDispatchedError(
            details={"reason": "closed_payload_violation"}))
        labels = [row["event"] for row in _events(rows, "f3_provider_")]
        self.assertIn("f3_provider_result_uncertain", labels)
        self.assertNotIn("f3_provider_response_received", labels)
        self.assertNotIn("f3_provider_response_lost", labels)
        child = _events(rows, "f3_dispatch_result_recorded")[-1]
        # PR1 has no producer proof code, so the phase is (c), never (b).
        self.assertEqual(child["dispatch_phase"], df.PHASE_SEND_UNCERTAIN)
        self.assertNotIn(df.PROVIDER_CALL_NOT_SENT, child)
        self.assertNotIn("changed_by_engineering", child)
        lifecycle = _events(rows, "task_failed_post_dispatch")[-1]
        self.assertIs(lifecycle["provider_dispatch_occurred"], True)
        self.assertEqual(result["task_state"], "failed_post_dispatch")
        self.assertIsNone(result.get("outcome_code"))

    async def test_lost_response_is_uncertain(self):
        created = await self.create_automation_plan()
        await self.approve(created)
        self.gateway.fail_after_write_target = TARGET
        await self.service.apply(created["plan_id"], created["plan_hash"])
        rows = _rows(self.audit_path)
        labels = [row["event"] for row in _events(rows, "f3_provider_")]
        self.assertIn("f3_provider_result_uncertain", labels)
        self.assertNotIn("f3_provider_response_received", labels)
        self.assertEqual(_events(rows, "f3_dispatch_result_recorded")[-1]["dispatch_phase"],
                         df.PHASE_SEND_UNCERTAIN)

    async def test_successful_write_keeps_response_received_and_sent_phase(self):
        created = await self.create_automation_plan()
        await self.approve(created)
        await self.service.apply(created["plan_id"], created["plan_hash"])
        rows = _rows(self.audit_path)
        self.assertIn("f3_provider_response_received",
                      [row["event"] for row in _events(rows, "f3_provider_")])
        child = _events(rows, "f3_dispatch_result_recorded")[-1]
        self.assertEqual(child["dispatch_phase"], df.PHASE_SENT_RESPONSE_RECEIVED)
        # The sent phase adds only the phase qualifier.
        for absent in ("changed_by_engineering", df.PROVIDER_CALL_NOT_SENT,
                       "dispatch_performed"):
            self.assertNotIn(absent, child)


class ChildAuditFactTests(unittest.TestCase):
    """Child audit values per phase (design section 5.1 "Child audit")."""

    def test_child_audit_values_per_phase(self):
        expected = {
            "already_present": (already_present(), "preflight_noop_verified",
                                {"dispatch_phase": df.PHASE_ALREADY_PRESENT}),
            "not_sent": (not_sent(), df.DISPATCH_RESULT_EVENT, {
                "dispatch_phase": df.PHASE_INTENT_RECORDED_NOT_SENT,
                df.PROVIDER_CALL_NOT_SENT: True, "dispatch_performed": False}),
            "r_proven": (r_proven(), df.DISPATCH_RESULT_EVENT, {
                "dispatch_phase": df.PHASE_SENT_REJECTED_NO_MUTATION,
                "provider_dispatch_occurred": True, "changed_by_engineering": False}),
            "r_unproven": (r_unproven(), df.DISPATCH_RESULT_EVENT, {
                "dispatch_phase": df.PHASE_SENT_FAILED_MUTATION_UNPROVEN,
                "provider_dispatch_occurred": True, "changed_by_engineering": None}),
            "uncertain": (uncertain(), df.DISPATCH_RESULT_EVENT,
                          {"dispatch_phase": df.PHASE_SEND_UNCERTAIN}),
            "sent": (sent_verified(), df.DISPATCH_RESULT_EVENT,
                     {"dispatch_phase": df.PHASE_SENT_RESPONSE_RECEIVED}),
        }
        for name, (record, event_type, value) in expected.items():
            with self.subTest(name):
                self.assertEqual(_child_audit_facts(event_type, df.child_facts(record)), value)

    def test_dashboard_b1_keeps_253_fields_only(self):
        record = FakeRecord(operation=df.DASHBOARD_CAPABILITY_ID,
                            outcome="dispatch_failed_confirmed",
                            codes=(df.SETTER_CALL_NOT_SENT,))
        self.assertEqual(
            _child_audit_facts(df.DISPATCH_RESULT_EVENT, df.child_facts(record)),
            {"dispatch_phase": df.PHASE_INTENT_RECORDED_NOT_SENT},
        )

    def test_other_events_are_not_backdated(self):
        for event_type in ("dispatch_intent_committed", "observation_recorded",
                           "verification_recorded", "preflight_completed"):
            self.assertEqual(_child_audit_facts(event_type, df.child_facts(r_proven())), {})
        # A no-op event on a record that is not (a) adds nothing.
        self.assertEqual(
            _child_audit_facts("preflight_noop_verified", df.child_facts(sent_verified())), {})


class _LimitationHelperGateway(helper_fixtures.FakeHelperStateGateway):
    async def set_state(self, entity_id, desired_state, *, before_dispatch):
        if self.mode == "pre_intent_refusal":
            raise HelperStateGatewayError("provider_unavailable")
        if self.mode == "post_intent_callback_failure":
            await before_dispatch()
            raise HelperStateGatewayError("before_dispatch_failed")
        return await super().set_state(entity_id, desired_state,
                                       before_dispatch=before_dispatch)


class OperationalLabelLimitationTests(unittest.IsolatedAsyncioTestCase):
    """Accepted PR1 limitation (design section 5.1, condition 3).

    The helper-state, backup and lifecycle gateways label by ``exc.dispatched``
    alone.  The label is retained in PR1; the F3 phase beside it is correct,
    and the label is never retry, redispatch or no-call authority.
    """

    _fixture = helper_fixtures.ExactHelperStateRuntimeTests
    asyncSetUp = _fixture.asyncSetUp
    asyncTearDown = _fixture.asyncTearDown
    grant = _fixture.grant
    create_and_grant = _fixture.create_and_grant

    async def run_mode(self, mode):
        self.helper.__class__ = _LimitationHelperGateway
        self.helper.mode = mode
        plan = await self.create_and_grant()
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        rows = _rows(Path(self.temp.name) / "audit.jsonl")
        labels = [row["event"] for row in _events(rows, "f3_provider_")
                  if "readback" not in row["event"]]
        child = _events(rows, "f3_dispatch_result_recorded")
        return plan, result, labels, (child[-1]["dispatch_phase"] if child else None)

    async def test_pre_intent_refusal(self):
        _, result, labels, child_phase = await self.run_mode("pre_intent_refusal")
        self.assertEqual(labels, ["f3_provider_invocation_refused"])
        self.assertIsNone(child_phase)
        self.assertEqual(result["dispatch_phase"], df.PHASE_PRE_INTENT)
        self.assertIs(result["provider_dispatch_occurred"], False)
        self.assertEqual(self.helper.dispatch_count, 0)

    async def test_post_intent_callback_failure_label_says_refused(self):
        # Retained mislabel: "refused" although the intent committed.
        plan, result, labels, child_phase = await self.run_mode(
            "post_intent_callback_failure")
        self.assertEqual(labels, ["f3_provider_invocation_started",
                                  "f3_provider_invocation_refused"])
        self.assertEqual(child_phase, df.PHASE_SEND_UNCERTAIN)
        self.assertEqual(result["dispatch_phase"], df.PHASE_SEND_UNCERTAIN)
        self.assertIs(result["provider_dispatch_occurred"], True)
        self.assertNotIn(df.PROVIDER_CALL_NOT_SENT, result)
        # The "refused" label is not retry or no-call authority: the stored
        # intent still blocks a second dispatch.
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertIn("dispatch intent", str(raised.exception))
        self.assertEqual(self.helper.dispatch_count, 0)

    async def test_confirmed_failure_with_response_label_says_lost(self):
        # Retained mislabel: "response lost" for a confirmed failure.
        _, result, labels, child_phase = await self.run_mode("confirmed_failure")
        self.assertEqual(labels, ["f3_provider_invocation_started",
                                  "f3_provider_response_lost"])
        self.assertEqual(child_phase, df.PHASE_SENT_FAILED_MUTATION_UNPROVEN)
        self.assertIsNone(result["changed_by_engineering"])
        self.assertNotIn("outcome_code", result)
        self.assertEqual(self.helper.dispatch_count, 1)

    async def test_lost_response(self):
        _, result, labels, child_phase = await self.run_mode("response_lost")
        self.assertEqual(labels, ["f3_provider_invocation_started",
                                  "f3_provider_response_lost"])
        self.assertEqual(child_phase, df.PHASE_SEND_UNCERTAIN)
        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertIs(result["provider_dispatch_occurred"], True)
        self.assertEqual(self.helper.dispatch_count, 1)


if __name__ == "__main__":
    unittest.main()
