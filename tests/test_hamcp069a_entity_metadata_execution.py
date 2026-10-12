"""HAMCP-069A-1 execution, authorization (V7), uncertainty and rollback tests.

Every path runs through the shipped service, F3 runtime and registry gateway
against the synthetic offline registry.  No test waits on a real clock.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import (  # noqa: E402
    ErrorCode,
    GovernanceError,
    HomeAssistantTimeoutError,
)
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalState,
    PlanStatus,
)

from tests.hamcp069a_entity_fixtures import (  # noqa: E402
    UPDATE,
    AuthorizationError,
    EntityMetadataServiceTestCase,
)


TARGET = "sensor.porch"
REGISTRY_GET = "config/entity_registry/get"


def unauthorized() -> AuthorizationError:
    """What the websocket client raises for an ``unauthorized`` result."""

    return AuthorizationError(details={"provider_response_received": True})


class ExecutionTestCase(EntityMetadataServiceTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.ws.labels = [
            {"label_id": "night", "name": "Night"},
            {"label_id": "outdoor", "name": "Outdoor"},
        ]
        self.add_entity(TARGET, labels=["night"], icon="mdi:old")

    async def approved(self, **arguments) -> dict:
        created = await self.create(entity_id=TARGET, **(arguments or {"name": "Porch sensor"}))
        await self.approve(created)
        return created

    def child_records(self, task_id: str) -> list:
        return [
            self.runtime.children.get(item["child_id"])
            for item in self.runtime.children.declarations_for_task(task_id)
        ]

    def child_codes(self, task_id: str) -> set[str]:
        return {
            code
            for record in self.child_records(task_id)
            for event in record.events
            for code in event["diagnostic_codes"]
        }

    async def assert_replay_refused(self, created: dict, task_id: str) -> None:
        """A terminal failed task is never re-executed by a second apply."""

        updates = len(self.ws.updates())
        with self.assertRaises(GovernanceError) as raised:
            await self.apply(created)
        self.assertEqual(raised.exception.code, ErrorCode.DUPLICATE_APPLY_ATTEMPT)
        self.assertEqual(raised.exception.details["task_id"], task_id)
        self.assertEqual(len(self.ws.updates()), updates)

    def fail_readback_after_update(self) -> None:
        websocket = self.ws
        answer = websocket.command

        async def command(message):
            if message.get("type") == REGISTRY_GET and websocket.updates():
                websocket.commands.append(dict(message))
                raise unauthorized()
            return await answer(message)

        websocket.command = command


class AuthorizationStageTests(ExecutionTestCase):
    """V7: a distinct, truthful outcome at each of the four stages."""

    async def test_planning_read_is_an_authorization_failure(self):
        self.ws.failures[REGISTRY_GET] = unauthorized()
        with self.assertRaises(GovernanceError) as raised:
            await self.create(entity_id=TARGET, name="Porch sensor")
        self.assertEqual(raised.exception.code, ErrorCode.AUTHORIZATION_FAILURE)
        details = raised.exception.details
        self.assertEqual(details["reason_code"], "entity_registry_permission_failure")
        self.assertIs(details["plan_created"], False)
        self.assertIs(details["provider_dispatch_occurred"], False)
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.ws.updates(), [])

    async def test_preflight_read_is_rejected_before_dispatch(self):
        created = await self.approved()
        self.ws.failures[REGISTRY_GET] = unauthorized()
        result = await self.apply(created)

        self.assertEqual(result["task_state"], "failed_pre_dispatch")
        self.assertIs(result["provider_dispatch_occurred"], False)
        (record,) = self.child_records(result["task_id"])
        self.assertEqual(record.normalized_outcome, "preflight_rejected")
        self.assertEqual(record.dispatch_count, 0)
        self.assertIn("permission_failure", self.child_codes(result["task_id"]))
        self.assertNotIn("provider_permission_denied", self.child_codes(result["task_id"]))
        self.assertEqual(self.ws.updates(), [])
        # The approval was not consumed: nothing was sent.
        plan = self.repository.get(created["plan_id"])
        self.assertEqual(plan.approval.state, ApprovalState.APPROVED)

    async def test_dispatch_refusal_is_confirmed_not_applied(self):
        created = await self.approved()
        self.ws.failures[UPDATE] = unauthorized()
        result = await self.apply(created)

        self.assertEqual(result["task_state"], "failed_post_dispatch")
        (record,) = self.child_records(result["task_id"])
        self.assertEqual(record.normalized_outcome, "dispatch_failed_confirmed")
        self.assertEqual(record.dispatch_count, 1)
        codes = self.child_codes(result["task_id"])
        self.assertIn("provider_permission_denied", codes)
        self.assertIn("provider_rejected_mutation", codes)
        self.assertNotIn("readback_permission_failure", codes)
        # One attempt, never repeated; the entry is unchanged.
        self.assertEqual(len(self.ws.updates()), 1)
        self.assertIsNone(self.ws.entries[TARGET]["name"])
        self.assertEqual(self.repository.get(created["plan_id"]).status, PlanStatus.FAILED)
        await self.assert_replay_refused(created, result["task_id"])

    async def test_readback_failure_keeps_the_task_observing(self):
        created = await self.approved()
        self.fail_readback_after_update()
        result = await self.apply(created)

        self.assertEqual(result["task_state"], "observing")
        self.assertIs(result["provider_dispatch_occurred"], True)
        (record,) = self.child_records(result["task_id"])
        self.assertEqual(record.normalized_outcome, "observing")
        self.assertFalse(record.terminal)
        codes = self.child_codes(result["task_id"])
        self.assertIn("readback_permission_failure", codes)
        self.assertNotIn("provider_permission_denied", codes)
        self.assertEqual(
            self.repository.get(created["plan_id"]).status, PlanStatus.VERIFICATION_REQUIRED
        )
        # Re-applying never redispatches the uncertain write.
        replay = await self.apply(created)
        self.assertIs(replay["task_reused"], True)
        self.assertIs(replay["redispatch_performed"], False)
        self.assertEqual(len(self.ws.updates()), 1)


class StaleAndUncertainTests(ExecutionTestCase):
    async def test_stale_entry_is_refused_before_dispatch(self):
        created = await self.approved()
        # An edit outside Engineering to a field the plan does not touch.
        self.ws.set_external(TARGET, icon="mdi:edited")
        result = await self.apply(created)

        self.assertEqual(result["task_state"], "failed_pre_dispatch")
        self.assertIs(result["provider_dispatch_occurred"], False)
        (record,) = self.child_records(result["task_id"])
        self.assertEqual(record.normalized_outcome, "preflight_rejected")
        # The shared configuration adapter's stale code (design §15).
        self.assertIn("stale_target_state", self.child_codes(result["task_id"]))
        self.assertEqual(self.ws.updates(), [])
        self.assertEqual(self.ws.entries[TARGET]["icon"], "mdi:edited")

    async def test_stale_reference_definition_is_refused_before_dispatch(self):
        created = await self.approved(add_labels=["outdoor"])
        self.ws.labels = [
            {"label_id": "night", "name": "Night"},
            {"label_id": "outdoor", "name": "Outside"},
        ]
        result = await self.apply(created)

        self.assertEqual(result["task_state"], "failed_pre_dispatch")
        self.assertIs(result["provider_dispatch_occurred"], False)
        (record,) = self.child_records(result["task_id"])
        self.assertEqual(record.normalized_outcome, "preflight_rejected")
        self.assertIn("stale_reference_definition", self.child_codes(result["task_id"]))
        self.assertEqual(self.ws.updates(), [])

    async def test_lost_response_unapplied_is_reconciled_by_readback_only(self):
        created = await self.approved()
        self.ws.failures[UPDATE] = HomeAssistantTimeoutError()
        result = await self.apply(created)

        self.assertIs(result["provider_dispatch_occurred"], True)
        self.assertEqual(result["task_state"], "failed_post_dispatch")
        (record,) = self.child_records(result["task_id"])
        self.assertEqual(record.normalized_outcome, "verification_mismatch")
        self.assertEqual(record.dispatch_count, 1)
        codes = self.child_codes(result["task_id"])
        self.assertIn("provider_response_indeterminate", codes)
        self.assertIn("exact_readback_not_verified", codes)
        self.assertEqual(len(self.ws.updates()), 1)
        self.assertIsNone(self.ws.entries[TARGET]["name"])
        await self.assert_replay_refused(created, result["task_id"])

    async def test_lost_response_applied_is_verified_by_readback_only(self):
        created = await self.approved()
        websocket = self.ws
        answer = websocket.command

        async def command(message):
            if message.get("type") == UPDATE:
                await answer(message)
                raise HomeAssistantTimeoutError()
            return await answer(message)

        websocket.command = command
        result = await self.apply(created)

        self.assertEqual(result["task_state"], "succeeded_verified")
        (record,) = self.child_records(result["task_id"])
        self.assertEqual(record.dispatch_count, 1)
        self.assertIn("provider_response_indeterminate", self.child_codes(result["task_id"]))
        self.assertEqual(len(self.ws.updates()), 1)
        self.assertEqual(self.ws.entries[TARGET]["name"], "Porch sensor")
        replay = await self.apply(created)
        self.assertIs(replay["task_reused"], True)
        self.assertEqual(len(self.ws.updates()), 1)


class RollbackTests(ExecutionTestCase):
    async def applied_forward(self) -> dict:
        created = await self.approved(
            name="Porch sensor", icon="mdi:new",
            remove_labels=["night"], add_labels=["outdoor"],
        )
        result = await self.apply(created)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(len(self.ws.updates()), 1)
        return created

    async def refused(self, created: dict, reason: str) -> dict:
        plans = len(self.repository.list())
        updates = len(self.ws.updates())
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"], created["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
        details = raised.exception.details
        self.assertEqual(details["reason"], reason)
        self.assertIs(details["provider_dispatch_occurred"], False)
        self.assertEqual(len(self.repository.list()), plans)
        self.assertEqual(len(self.ws.updates()), updates)
        return details

    async def test_rollback_restores_the_recorded_pre_state(self):
        created = await self.applied_forward()
        rollback = await self.service.rollback_change(created["plan_id"], created["plan_hash"])

        self.assertEqual(rollback["status"], "rollback_plan_created")
        self.assertIs(rollback["approval_required"], True)
        self.assertIs(rollback["provider_dispatch_occurred"], False)
        self.assertEqual(rollback["restored_fields"], ["icon", "labels", "name"])
        # Creating the reverse plan only reads.
        self.assertEqual(len(self.ws.updates()), 1)
        # Asking again returns the same reverse plan.
        again = await self.service.rollback_change(created["plan_id"], created["plan_hash"])
        self.assertEqual(again["rollback_plan_id"], rollback["rollback_plan_id"])
        self.assertEqual(again["plan_hash"], rollback["plan_hash"])

        reverse = {"plan_id": rollback["rollback_plan_id"], "plan_hash": rollback["plan_hash"]}
        await self.approve(reverse)
        result = await self.apply(reverse)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(
            self.ws.updates()[-1],
            {"type": UPDATE, "entity_id": TARGET, "icon": "mdi:old", "labels": ["night"], "name": None},
        )
        entry = self.ws.entries[TARGET]
        self.assertEqual((entry["name"], entry["icon"], entry["labels"]), (None, "mdi:old", ["night"]))

    async def test_rollback_after_a_later_edit_is_refused(self):
        created = await self.applied_forward()
        self.ws.set_external(TARGET, icon="mdi:someone-else")
        details = await self.refused(created, "entry_changed_since_apply")
        self.assertEqual(details["reason_code"], "entry_changed_since_apply")
        self.assertIs(details["plan_created"], False)

    async def test_rollback_with_a_deleted_prior_reference_is_refused(self):
        created = await self.applied_forward()
        self.ws.labels = [{"label_id": "outdoor", "name": "Outdoor"}]
        details = await self.refused(created, "rollback_reference_missing")
        self.assertEqual(details["references"], ["label:night"])

    async def test_rollback_of_a_failed_forward_is_refused(self):
        created = await self.approved()
        self.ws.failures[UPDATE] = unauthorized()
        await self.apply(created)
        await self.refused(created, "forward_not_verified")

    async def test_rollback_of_an_unapplied_forward_is_refused(self):
        created = await self.create(entity_id=TARGET, name="Porch sensor")
        await self.refused(created, "forward_not_verified")


if __name__ == "__main__":
    unittest.main()
