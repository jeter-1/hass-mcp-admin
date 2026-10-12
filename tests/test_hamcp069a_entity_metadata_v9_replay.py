"""HAMCP-069A-1 V9 end-to-end and replay tests.

Owner direction (2026-10-12): after an entity metadata plan ends as
``desired_state_already_present_not_dispatched`` (a) the plan is terminal,
(b) re-applying returns the same already-present result and never dispatches,
and (c) if the entity's state then changes, including back to the plan's
original pre-state, re-applying still never dispatches and the approval cannot
authorize a later write.  Every path runs through the shipped service, F3
runtime and registry gateway against a synthetic offline registry.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.entity_metadata import (  # noqa: E402
    ENTITY_METADATA_NOT_DISPATCHED_OUTCOME,
)
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalState,
    PlanStatus,
)

from tests.hamcp069a_entity_fixtures import (  # noqa: E402
    EntityMetadataServiceTestCase,
)


TARGET = "light.porch"
RESULT_TEXT = (
    "desired state already present; not dispatched. "
    "Engineering did not make this change."
)


class EntityMetadataV9Tests(EntityMetadataServiceTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.add_entity(TARGET)

    async def approved_rename(self) -> dict:
        created = await self.create(entity_id=TARGET, name="Front porch")
        self.assertEqual(created["status"], "awaiting_approval")
        await self.approve(created)
        return created

    async def already_present(self) -> tuple[dict, dict]:
        """Approve a rename, then let it land outside Engineering first."""

        created = await self.approved_rename()
        self.ws.set_external(TARGET, name="Front porch")
        return created, await self.apply(created)

    def events(self) -> list[str]:
        return [event["event"] for event in self.audit_events()]

    def assert_no_write_reached_home_assistant(self) -> None:
        self.assertEqual(self.ws.updates(), [])
        # V8: no Home Assistant configuration check either.
        self.assertEqual(self.rest.requests, [])

    def assert_terminal_already_present(self, result: dict, task_id: str) -> None:
        self.assertEqual(result["status"], "already_present")
        self.assertEqual(result["task_id"], task_id)
        self.assertIs(result["provider_dispatch_occurred"], False)
        self.assertIs(result["redispatch_performed"], False)
        self.assertEqual(result["task_state"], "succeeded_verified")

    async def test_already_present_outcome_is_distinct_in_result_and_audit(self):
        created, result = await self.already_present()

        # Client result: status, outcome code, dispatch flag and text.
        self.assert_terminal_already_present(result, result["task_id"])
        self.assertIs(result["task_reused"], False)
        self.assertEqual(result["outcome_code"], ENTITY_METADATA_NOT_DISPATCHED_OUTCOME)
        self.assertIs(result["dispatch_performed"], False)
        self.assertIs(result["changed_by_engineering"], False)
        self.assertEqual(result["result_text"], RESULT_TEXT)
        task = result["execution_task"]
        self.assertEqual(task["terminal_outcome"], ENTITY_METADATA_NOT_DISPATCHED_OUTCOME)
        self.assertIsNone(task["dispatched_at"])
        self.assertEqual(task["provider_attempts"], [])
        summary = task["verification_summary"]
        self.assertEqual(summary["outcome_code"], ENTITY_METADATA_NOT_DISPATCHED_OUTCOME)
        self.assertIs(summary["dispatch_performed"], False)
        self.assertEqual(summary["result_text"], RESULT_TEXT)
        self.assertEqual(
            [(child["operation_id"], child["dispatch_count"]) for child in summary["children"]],
            [("entity_metadata", 0)],
        )

        # Audit: completed without dispatch, child no-op verified, no dispatch.
        events = self.events()
        self.assertIn("f3_preflight_noop_verified", events)
        self.assertIn("task_completed_without_dispatch", events)
        self.assertNotIn("task_completed", events)
        self.assertNotIn("dispatch_attempted", events)
        completed = next(
            event for event in self.audit_events()
            if event["event"] == "task_completed_without_dispatch"
        )
        self.assertIs(completed["approval_consumed"], False)
        self.assertIs(completed["provider_dispatch_occurred"], False)
        self.assertEqual(completed["terminal_outcome"], ENTITY_METADATA_NOT_DISPATCHED_OUTCOME)
        noop = next(
            event for event in self.audit_events()
            if event["event"] == "f3_preflight_noop_verified"
        )
        self.assertIs(noop["dispatch_possible"], False)
        self.assertEqual(noop["evidence_references"]["dispatch_count"], 0)
        self.assertEqual(
            noop["evidence_references"]["reason_code"], "desired_state_already_reached"
        )
        self.assertEqual(
            noop["capability_identity"],
            "update_entity_registry_metadata_configuration",
        )

        # (a) The plan is terminal.  Its approval is recorded as granted but
        # never consumed: no write was authorized into Home Assistant.
        plan = self.repository.get(created["plan_id"])
        self.assertEqual(plan.status, PlanStatus.APPLIED)
        self.assertEqual(plan.execution_outcome, ENTITY_METADATA_NOT_DISPATCHED_OUTCOME)
        self.assertEqual(plan.approval.state, ApprovalState.APPROVED)
        self.assertIsNone(
            task["approval_reference"]["plan_approval"]["consumed_at"]
        )
        self.assert_no_write_reached_home_assistant()

    async def test_dispatched_apply_contrast(self):
        created = await self.approved_rename()
        result = await self.apply(created)

        self.assertEqual(result["status"], "applied")
        self.assertIs(result["provider_dispatch_occurred"], True)
        for field in ("outcome_code", "dispatch_performed", "changed_by_engineering", "result_text"):
            self.assertNotIn(field, result)
        task = result["execution_task"]
        self.assertEqual(task["terminal_outcome"], "succeeded_verified")
        self.assertEqual(
            [child["dispatch_count"] for child in task["verification_summary"]["children"]],
            [1],
        )
        self.assertNotIn("outcome_code", task["verification_summary"])
        events = self.events()
        self.assertIn("task_completed", events)
        self.assertNotIn("task_completed_without_dispatch", events)
        self.assertNotIn("f3_preflight_noop_verified", events)
        # Exactly one closed update carrying only the changed field.
        self.assertEqual(
            self.ws.updates(),
            [{"type": "config/entity_registry/update", "entity_id": TARGET, "name": "Front porch"}],
        )
        self.assertEqual(self.rest.requests, [])
        plan = self.repository.get(created["plan_id"])
        self.assertEqual(plan.execution_outcome, "succeeded_verified")
        self.assertEqual(plan.approval.state, ApprovalState.CONSUMED)

    async def test_replay_returns_the_same_terminal_result_and_never_dispatches(self):
        created, first = await self.already_present()

        for _ in range(2):
            replay = await self.apply(created)
            self.assert_terminal_already_present(replay, first["task_id"])
            self.assertIs(replay["task_reused"], True)
            self.assertEqual(replay["terminal_outcome"], ENTITY_METADATA_NOT_DISPATCHED_OUTCOME)
        self.assertEqual(self.events().count("duplicate_apply_prevented"), 2)
        self.assert_no_write_reached_home_assistant()

    async def test_replay_after_state_returns_to_pre_state_never_dispatches(self):
        created, first = await self.already_present()
        pre_state = self.repository.get(created["plan_id"]).operations[0].current_config
        self.assertIsNone(pre_state["name"])

        # (c) The entity goes back to the plan's original pre-state, so the
        # approved plan would again be a real change if it were re-executed.
        self.ws.set_external(TARGET, name=None)
        reverted = await self.apply(created)
        self.assert_terminal_already_present(reverted, first["task_id"])
        self.assertIs(reverted["task_reused"], True)

        # Any other later state: still the terminal result, still no dispatch.
        self.ws.set_external(TARGET, name="Somebody else's porch")
        other = await self.apply(created)
        self.assert_terminal_already_present(other, first["task_id"])

        # The terminal outcome survives a process restart.
        await self.restart_runtime()
        restarted = await self.apply(created)
        self.assert_terminal_already_present(restarted, first["task_id"])

        # And outlives the approval window.
        self.clock[0] += timedelta(days=1)
        expired = await self.apply(created)
        self.assertNotEqual(expired.get("status"), "applied")
        self.assertIs(expired.get("provider_dispatch_occurred"), False)

        self.assert_no_write_reached_home_assistant()
        self.assertEqual(self.ws.entries[TARGET]["name"], "Somebody else's porch")

    async def test_unconsumed_approval_cannot_be_reused_or_regranted(self):
        created, _ = await self.already_present()
        self.ws.set_external(TARGET, name=None)

        with self.assertRaises(GovernanceError) as raised:
            self.service.approve(created["plan_id"], created["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.APPROVAL_ALREADY_CONSUMED)
        replay = await self.apply(created)
        self.assertEqual(replay["status"], "already_present")
        self.assert_no_write_reached_home_assistant()

    async def test_rollback_of_a_not_dispatched_forward_is_refused(self):
        created, _ = await self.already_present()
        plans_before = {plan.plan_id for plan in self.repository.list()}

        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"], created["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
        self.assertEqual(raised.exception.details["reason"], "forward_not_dispatched")
        self.assertIs(raised.exception.details["provider_dispatch_occurred"], False)
        self.assertEqual({plan.plan_id for plan in self.repository.list()}, plans_before)
        self.assert_no_write_reached_home_assistant()


if __name__ == "__main__":
    unittest.main()
