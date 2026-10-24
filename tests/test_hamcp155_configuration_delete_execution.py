"""HAMCP-155 governed deletion: planning, execution, readback and rollback.

Every path runs through the shipped service, the F3 runtime and the closed
``ConfigurationDeleteGateway`` against the synthetic offline Core store in
``tests/hamcp155_fixtures.py``.  The Core double admits the runtime-only
``core.configuration_delete_operation`` profile here; the inactive
(unadmitted) behaviour is covered in ``test_hamcp155_configuration_delete_admission``.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import (  # noqa: E402
    ErrorCode,
    GovernanceError,
    HomeAssistantApiError,
)

from tests.hamcp069b_fixtures import (  # noqa: E402
    automation_profile,
    exposure_snapshot,
    finding,
)
from ha_mcp_engineering.f3_configuration.delete import (  # noqa: E402
    ConfigurationDeleteAdapter,
)
from tests.hamcp155_fixtures import (  # noqa: E402
    ConfigurationDeleteServiceTestCase,
    automation_config,
    registry_id,
    script_config,
)


def delete_path(resource_type: str, target_id: str) -> tuple[str, str]:
    return ("DELETE", f"/config/{resource_type}/config/{target_id}")


def disclosure(created: dict, index: int = 0) -> dict:
    return created["dry_run_results"]["operations"][index]["dry_run_results"][
        "configuration_delete"
    ]


class EndToEndTests(ConfigurationDeleteServiceTestCase):
    async def assert_delete_then_recreate(self, resource_type, target_id, pre_image):
        created = await self.create_one(resource_type, target_id)
        self.assertEqual(self.config_rest.writes(), [], "planning never writes")
        await self.approve(created)
        result = await self.apply(created)
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            self.config_rest.deletes(), [delete_path(resource_type, target_id)]
        )
        entity_id = f"{resource_type}.{target_id}"
        # Every read the readback claims is true of the store.
        self.assertNotIn(target_id, self.config_rest.configs[resource_type])
        self.assertNotIn(entity_id, self.ws.entries)
        self.assertNotIn(entity_id, self.config_rest.states)
        self.assertIn("exact_readback_verified", self.child_codes(result["task_id"]))

        rollback = await self.service.rollback_change(created["plan_id"])
        self.assertEqual(rollback["status"], "rollback_plan_created", rollback)
        self.assertFalse(rollback["provider_dispatch_occurred"])
        self.assertEqual(rollback["operations_excluded"], [])
        self.assertEqual(len(self.config_rest.writes()), 1, "rollback never runs itself")
        recreate = self.service.get_plan(rollback["rollback_plan_id"])
        self.assertEqual(recreate["plan_hash"], rollback["plan_hash"])
        (operation,) = recreate["dry_run_results"]["operations"]
        self.assertEqual(operation["action"], "create")
        self.assertEqual(operation["target_id"], target_id)
        await self.approve(recreate)
        restored = await self.apply(recreate)
        self.assertEqual(restored["task_state"], "succeeded_verified", restored)
        # The recreate matches the captured pre-image exactly, and Core
        # restored the deleted registry entry (same registry id).
        self.assertEqual(self.config_rest.configs[resource_type][target_id], pre_image)
        self.assertEqual(self.ws.entries[entity_id]["id"], registry_id(target_id))

    async def test_delete_automation_then_recreate_exactly(self):
        pre_image = automation_config("porch_light")
        self.add("automation", "porch_light", pre_image)
        await self.assert_delete_then_recreate("automation", "porch_light", pre_image)

    async def test_delete_script_then_recreate_exactly(self):
        pre_image = script_config("evening")
        self.add("script", "evening", pre_image)
        await self.assert_delete_then_recreate("script", "evening", pre_image)

    async def test_ordered_multi_delete(self):
        self.add("automation", "a")
        self.add("script", "b")
        self.add("automation", "c")
        created = await self.create(
            deletions=[
                {"resource_type": "automation", "target_id": "a"},
                {"resource_type": "script", "target_id": "b"},
                {"resource_type": "automation", "target_id": "c"},
            ]
        )
        self.assertEqual(created["title"], "Delete 3 automation/scripts")
        await self.approve(created)
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            self.config_rest.deletes(),
            [
                delete_path("automation", "a"),
                delete_path("script", "b"),
                delete_path("automation", "c"),
            ],
        )
        self.assertEqual(self.config_rest.writes(), self.config_rest.deletes())


class StaleStateTests(ConfigurationDeleteServiceTestCase):
    async def assert_stale(self, created, code):
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertIn(code, self.child_codes(result["task_id"]))
        self.assertEqual(self.config_rest.writes(), [])
        return result

    async def test_drift_immediately_before_delete_refuses_as_stale(self):
        """Lesson 2: the target drifts after preflight, before the DELETE."""

        self.add("automation", "a")
        created = await self.approved("automation", "a")
        gets = self.config_rest.config_gets.get(("automation", "a"), 0)

        def drift(resource_type, target_id, ordinal):
            # The preflight read is answered; the change lands before the
            # dispatch-time comparison.
            if ordinal == gets + 1:
                self.config_rest.configs["automation"]["a"]["mode"] = "restart"

        self.config_rest.after_config_get = drift
        await self.assert_stale(created, "stale_target_state")
        self.assertEqual(
            self.config_rest.config_gets[("automation", "a")],
            gets + 2,
            "preflight read, then the dispatch-time drift read",
        )

    async def test_every_dispatch_refusal_code_is_stored(self):
        # The executor keeps the adapter's whole pre-intent refusal, not only
        # its first code.
        self.add("automation", "a")
        created = await self.approved("automation", "a")

        async def two_codes(adapter, operation):
            return ("stale_target_state", "delete_target_missing")

        with mock.patch.object(
            ConfigurationDeleteAdapter, "_predispatch_refusal", two_codes
        ):
            result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        codes = self.child_codes(result["task_id"])
        self.assertIn("stale_target_state", codes)
        self.assertIn("delete_target_missing", codes)
        self.assertEqual(self.config_rest.writes(), [])

    async def test_changed_pre_image_is_stale(self):
        self.add("automation", "a")
        created = await self.approved("automation", "a")
        self.config_rest.configs["automation"]["a"]["mode"] = "queued"
        await self.assert_stale(created, "stale_target_state")

    async def test_already_absent_is_stale_never_verified(self):
        self.add("automation", "a")
        created = await self.approved("automation", "a")
        del self.config_rest.configs["automation"]["a"]
        result = await self.assert_stale(created, "delete_target_missing")
        self.assertNotIn("exact_readback_verified", self.child_codes(result["task_id"]))
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)

    async def test_registry_entry_changed_is_stale(self):
        self.add("automation", "a")
        created = await self.approved("automation", "a")
        self.ws.entries["automation.a"]["id"] = "replacement"
        await self.assert_stale(created, "registry_entry_changed")

    async def test_consumer_evidence_changed_is_stale(self):
        self.add("script", "s")
        created = await self.approved("script", "s")
        self.snapshot = exposure_snapshot(
            findings=(finding("script.s", "caller", relation="action_entity"),),
            profiles=(automation_profile("caller", "none"),),
        )
        await self.assert_stale(created, "dependency_evidence_changed")


class TruthfulOutcomeTests(ConfigurationDeleteServiceTestCase):
    async def test_rejected_delete_is_reported_rejected(self):
        self.add("automation", "a")
        created = await self.approved("automation", "a")
        # Removed by someone else after the drift check: Core answers 400.
        self.config_rest.before_delete = lambda: self.config_rest.configs[
            "automation"
        ].pop("a")
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_post_dispatch", result)
        (record,) = self.child_records(result["task_id"])
        self.assertEqual(record.normalized_outcome, "dispatch_failed_confirmed")
        self.assertEqual(record.dispatch_count, 1)
        codes = self.child_codes(result["task_id"])
        self.assertIn("configuration_delete_rejected", codes)
        self.assertNotIn("exact_readback_verified", codes)
        self.assertEqual(len(self.config_rest.deletes()), 1)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)

    async def test_uncertain_delete_not_applied_is_a_mismatch(self):
        self.add("automation", "a")
        created = await self.approved("automation", "a")
        self.config_rest.delete_failure = HomeAssistantApiError(
            details={"provider_response_received": False}
        )
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_post_dispatch", result)
        self.assertTrue(result["provider_dispatch_occurred"])
        (record,) = self.child_records(result["task_id"])
        self.assertEqual(record.normalized_outcome, "verification_mismatch")
        codes = self.child_codes(result["task_id"])
        self.assertTrue(
            {"provider_response_indeterminate", "config_still_present", "prior_state_observed"}
            <= codes,
            codes,
        )
        self.assertEqual(len(self.config_rest.deletes()), 1, "never sent twice")

    async def test_uncertain_delete_that_applied_is_verified_by_readback(self):
        self.add("automation", "a")
        created = await self.approved("automation", "a")

        def lost_response():
            del self.config_rest.configs["automation"]["a"]
            self.ws.entries.pop("automation.a")
            self.config_rest.states.discard("automation.a")
            raise HomeAssistantApiError(details={"provider_response_received": False})

        self.config_rest.before_delete = lost_response
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertIn("provider_response_indeterminate", self.child_codes(result["task_id"]))
        self.assertEqual(len(self.config_rest.deletes()), 1)

    async def test_runtime_removal_not_observed_is_manual_review(self):
        self.add("script", "s")
        created = await self.approved("script", "s")
        self.config_rest.delete_hook_removes_entry = False
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "manual_review_required", result)
        codes = self.child_codes(result["task_id"])
        self.assertIn("config_removed_runtime_unverified", codes)
        self.assertNotIn("exact_readback_verified", codes)
        # Bounded: six polls, five waits, one DELETE.
        self.assertEqual(len(self.poll_sleeps), 5)
        self.assertEqual(len(self.config_rest.deletes()), 1)

    async def test_lingering_registry_entry_alone_is_manual_review(self):
        # The state is gone; only the registry entry stays. Absence needs both.
        self.add("script", "s")
        created = await self.approved("script", "s")

        def restore_entry(resource_type, target_id):
            entry = self.config_rest.deleted_entries.pop((resource_type, target_id))
            self.config_rest.websocket.entries[entry["entity_id"]] = entry

        self.config_rest.after_delete = restore_entry
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "manual_review_required", result)
        codes = self.child_codes(result["task_id"])
        self.assertIn("config_removed_runtime_unverified", codes)
        (record,) = self.child_records(result["task_id"])
        self.assertEqual(record.evidence.get("mismatch_fields"), ["registry_entry_present"])
        self.assertNotIn("exact_readback_verified", codes)
        self.assertEqual(len(self.config_rest.deletes()), 1)

    async def test_lingering_state_alone_is_manual_review(self):
        # The registry entry is gone; only the state stays.
        self.add("script", "s")
        created = await self.approved("script", "s")
        self.config_rest.after_delete = (
            lambda resource_type, target_id: self.config_rest.states.add(
                f"{resource_type}.{target_id}"
            )
        )
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "manual_review_required", result)
        codes = self.child_codes(result["task_id"])
        self.assertIn("config_removed_runtime_unverified", codes)
        (record,) = self.child_records(result["task_id"])
        self.assertEqual(record.evidence.get("mismatch_fields"), ["entity_state_present"])
        self.assertNotIn("exact_readback_verified", codes)
        self.assertEqual(len(self.config_rest.deletes()), 1)

    async def test_delete_answered_ok_but_still_present_is_a_mismatch(self):
        self.add("script", "s")
        created = await self.approved("script", "s")
        self.config_rest.ignore_deletes = True
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_post_dispatch", result)
        (record,) = self.child_records(result["task_id"])
        self.assertEqual(record.normalized_outcome, "verification_mismatch")
        self.assertIn("config_still_present", self.child_codes(result["task_id"]))

    async def test_partial_plan_stops_on_first_failure(self):
        for target_id in ("a", "b", "c"):
            self.add("automation", target_id)
        created = await self.create(
            deletions=[
                {"resource_type": "automation", "target_id": target_id}
                for target_id in ("a", "b", "c")
            ]
        )
        await self.approve(created)

        def reject_b():
            if self.config_rest.deletes()[-1] == delete_path("automation", "b"):
                del self.config_rest.configs["automation"]["b"]

        self.config_rest.before_delete = reject_b
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "manual_review_required", result)
        first, second, third = self.child_records(result["task_id"])
        self.assertEqual(first.normalized_outcome, "succeeded_verified")
        self.assertEqual(second.normalized_outcome, "dispatch_failed_confirmed")
        self.assertIsNone(third, "the unattempted child was never started")
        self.assertEqual(
            self.config_rest.deletes(),
            [delete_path("automation", "a"), delete_path("automation", "b")],
        )
        self.assertIn("c", self.config_rest.configs["automation"])
        rollback = await self.service.rollback_change(created["plan_id"])
        self.assertEqual(rollback["status"], "rollback_partial_plan_created")
        self.assertEqual(len(rollback["operations_to_restore"]), 1)
        self.assertEqual(sorted(rollback["operations_excluded"]), ["delete_2", "delete_3"])

    async def test_rollback_skips_a_target_that_was_recreated(self):
        self.add("automation", "a")
        created = await self.approved("automation", "a")
        await self.apply(created)
        self.add("automation", "a", automation_config("a", target="light.other"))
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
        self.assertEqual(
            raised.exception.details.get("reason"), "no_verified_absent_deletion"
        )


class ApprovalAndReachabilityTests(ConfigurationDeleteServiceTestCase):
    async def test_apply_before_approval_refuses_with_no_write(self):
        self.add("automation", "a")
        created = await self.create_one("automation", "a")
        with self.assertRaises(GovernanceError):
            await self.apply(created)
        self.assertEqual(self.config_rest.writes(), [])
        self.assertIn("a", self.config_rest.configs["automation"])

    async def test_generic_write_never_deletes(self):
        self.add("automation", "a")
        with self.assertRaises(Exception):
            await self.resource_gateway.write("delete", "automation", "a", {})
        self.assertEqual(self.config_rest.deletes(), [])

    async def test_consumers_are_disclosed_and_unresolved_ones_elevate(self):
        self.add("script", "s")
        self.snapshot = exposure_snapshot(
            findings=(finding("script.s", "caller", relation="action_entity"),),
        )
        created = await self.create_one("script", "s")
        self.assertEqual(created["policy_decision"]["policy_class"], "elevated_admin")
        details = disclosure(created)
        self.assertEqual(details["consumers"]["count"], 1)
        self.assertEqual(
            details["consumers"]["items"][0]["source_entity_id"], "automation.caller"
        )
        self.assertEqual(
            details["consumers"]["coverage_unavailable"],
            ["dashboards", "groups", "scenes", "templates"],
        )
        self.assertIn(
            "Deleting this breaks automation automation.caller (action_entity).",
            details["consequence_statements"],
        )
        await self.approve(created)
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)


if __name__ == "__main__":
    unittest.main()
