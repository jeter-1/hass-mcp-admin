"""HAMCP-298 rollback of governed automation and script creates.

Every path runs through the shipped service, the F3 runtime and the closed
HAMCP-155 ``ConfigurationDeleteGateway`` against the synthetic offline Core
store in ``tests/hamcp155_fixtures.py``.  The synthetic registry entries
carry ``created_at`` as Core 2026.11.0 entity registry entries do.  The
contract is ``docs/HAMCP_329_298_SCENE_CREATE_AND_CREATE_ROLLBACK_DESIGN.md``
§3.2-§3.4 and §6.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance import (  # noqa: E402
    created_object_identity as identity_model,
    created_object_rollback as rollback_model,
)
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    evaluate_change_policy,
)

import tests.hamcp155_fixtures as fixtures  # noqa: E402
from tests.hamcp155_fixtures import (  # noqa: E402
    ConfigurationDeleteServiceTestCase,
    automation_config,
    script_config,
)


def _with_created_at(original):
    def build(entity_id, **changes):
        changes.setdefault("created_at", 0.5)
        return original(entity_id, **changes)

    return build


class CreatedObjectRollbackTestCase(ConfigurationDeleteServiceTestCase):
    async def asyncSetUp(self) -> None:
        patcher = mock.patch.object(
            fixtures, "registry_entry", _with_created_at(fixtures.registry_entry)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        await super().asyncSetUp()

    async def create_plan(self, *targets: tuple[str, str]) -> dict:
        operations = []
        for index, (resource_type, target_id) in enumerate(targets):
            config = (
                automation_config(target_id)
                if resource_type == "automation"
                else script_config(target_id)
            )
            operations.append(
                {
                    "operation_id": f"create_{index + 1}",
                    "resource_type": resource_type,
                    "action": "create",
                    "target_id": target_id,
                    "proposed_config": config,
                    "depends_on": [f"create_{index}"] if index else [],
                }
            )
        return await self.service.create_configuration_plan(
            title="Synthetic create",
            description="Synthetic governed create",
            operations=operations,
        )

    async def created(self, *targets: tuple[str, str]) -> dict:
        created = await self.create_plan(*targets)
        await self.approve(created)
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        return created

    def identity_values(self, plan_id: str) -> list[dict]:
        task = self.service.task_repository.get_for_plan(plan_id)
        return [
            self.runtime.created_identities.get(item["child_id"])
            for item in self.runtime.children.declarations_for_task(task.task_id)
        ]

    def delete_operation(self, plan_id: str, index: int = 0):
        return self.service._load(plan_id).operations[index]

    async def refused(self, plan_id: str) -> GovernanceError:
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(plan_id)
        return raised.exception


class CaptureTests(CreatedObjectRollbackTestCase):
    async def test_verified_create_captures_the_created_identity(self):
        created = await self.created(("automation", "porch_light"))
        (value,) = self.identity_values(created["plan_id"])
        self.assertEqual(value["outcome"], identity_model.CAPTURED)
        identity = value["identity"]
        self.assertEqual(identity["entity_id"], "automation.porch_light")
        self.assertEqual(identity["registry_entry_id"], fixtures.registry_id("porch_light"))
        self.assertEqual(identity["registry_created_at"], "0.5")
        self.assertEqual(
            identity["delete_argument"],
            {"method": "DELETE", "path": "/api/config/automation/config/porch_light"},
        )
        self.assertEqual(
            value["resulting_state_fingerprint"], identity["created_fingerprint"]
        )

    async def test_unadmitted_delete_profile_captures_nothing_and_reads_nothing(self):
        self.core.withdraw()
        created = await self.create_plan(("script", "evening"))
        await self.approve(created)
        before = self.reads()
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        (value,) = self.identity_values(created["plan_id"])
        self.assertEqual(value["outcome"], identity_model.CAPABILITY_UNADMITTED)
        self.assertIsNone(value["identity"])
        del before

    async def test_entry_without_created_at_is_an_identity_mismatch(self):
        original = fixtures.registry_entry

        def without(entity_id, **changes):
            value = original(entity_id, **changes)
            value.pop("created_at", None)
            return value

        with mock.patch.object(fixtures, "registry_entry", without):
            created = await self.created(("automation", "no_created_at"))
        (value,) = self.identity_values(created["plan_id"])
        self.assertEqual(value["outcome"], identity_model.IDENTITY_MISMATCH)
        error = await self.refused(created["plan_id"])
        self.assertEqual(error.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
        self.assertEqual(
            error.details["operations_excluded"][0]["reason"],
            rollback_model.IDENTITY_NOT_CAPTURED,
        )

    async def test_first_capture_wins_and_tampered_store_is_not_captured(self):
        created = await self.created(("automation", "porch_light"))
        (value,) = self.identity_values(created["plan_id"])
        store = self.runtime.created_identities
        again = store.put(
            child_id=value["child_id"],
            plan_id=value["plan_id"],
            operation_id=value["operation_id"],
            resulting_state_fingerprint=value["resulting_state_fingerprint"],
            outcome=identity_model.READ_FAILED,
            identity=None,
        )
        self.assertEqual(again, value)
        path = store._path(value["child_id"])
        path.write_text(path.read_text().replace("porch_light", "porch_lamp"))
        self.assertIsNone(store.get(value["child_id"]))


class RollbackPlanningTests(CreatedObjectRollbackTestCase):
    async def assert_owned_rollback(self, resource_type: str, target_id: str):
        created = await self.created((resource_type, target_id))
        writes = len(self.config_rest.writes())
        rollback = await self.service.rollback_change(created["plan_id"])
        self.assertEqual(rollback["status"], "rollback_plan_created", rollback)
        self.assertFalse(rollback["provider_dispatch_occurred"])
        self.assertEqual(rollback["operations_to_delete"], ["create_1"])
        self.assertEqual(rollback["operations_excluded"], [])
        self.assertEqual(len(self.config_rest.writes()), writes, "planning never writes")
        operation = self.delete_operation(rollback["rollback_plan_id"])
        (value,) = rollback_model.markers(operation)
        self.assertEqual(value["ownership"], rollback_model.OWNED)
        self.assertEqual(value["basis"], "")
        self.assertEqual(value["source_plan_id"], created["plan_id"])
        self.assertTrue(rollback_model.valid_marker(value, operation))
        plan = self.service._load(rollback["rollback_plan_id"])
        self.assertNotEqual(plan.policy_decision.policy_class.value, "prohibited")
        delete_plan = self.service.get_plan(rollback["rollback_plan_id"])
        await self.approve(delete_plan)
        result = await self.apply(delete_plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            self.config_rest.deletes(),
            [("DELETE", f"/config/{resource_type}/config/{target_id}")],
        )
        self.assertNotIn(target_id, self.config_rest.configs[resource_type])
        # Re-requesting returns the same plan; nothing is planned twice.
        again = await self.service.rollback_change(created["plan_id"])
        self.assertEqual(again["rollback_plan_id"], rollback["rollback_plan_id"])

    async def test_owned_automation_create_rolls_back_by_governed_delete(self):
        await self.assert_owned_rollback("automation", "porch_light")

    async def test_owned_script_create_rolls_back_by_governed_delete(self):
        await self.assert_owned_rollback("script", "evening")

    async def test_projection_marks_a_captured_create_available(self):
        created = await self.created(("automation", "porch_light"))
        projected = self.service.get_plan(created["plan_id"])
        self.assertTrue(projected["rollback"]["available"], projected["rollback"])
        self.assertNotEqual(
            projected["rollback"].get("status"), "unavailable_for_create"
        )

    async def test_option_e_modified_at_only_is_included_elevated(self):
        created = await self.created(("automation", "porch_light"))
        self.ws.entries["automation.porch_light"]["modified_at"] = 7.25
        rollback = await self.service.rollback_change(created["plan_id"])
        self.assertEqual(rollback["status"], "rollback_plan_created", rollback)
        operation = self.delete_operation(rollback["rollback_plan_id"])
        (value,) = rollback_model.markers(operation)
        self.assertEqual(value["ownership"], rollback_model.MODIFIED_SINCE_CREATE)
        self.assertEqual(value["basis"], rollback_model.BASIS_MODIFIED_SINCE_CREATE)
        self.assertEqual(value["observed_modified_at"], "7.25")
        plan = self.service._load(rollback["rollback_plan_id"])
        self.assertEqual(plan.policy_decision.policy_class.value, "elevated_admin")
        self.assertTrue(
            any(rollback_model.E_DISCLOSURE in reason for reason in operation.risk.reasons)
        )
        projected = self.service.get_plan(rollback["rollback_plan_id"])
        child = projected["dry_run_results"]["operations"][0]["dry_run_results"]
        self.assertEqual(
            child["configuration_delete"]["created_object_rollback"]["statement"],
            rollback_model.E_DISCLOSURE,
        )
        # A single plan approval is not enough: the elevated acknowledgement
        # is a second, separate step.
        pending = self.service.approve(projected["plan_id"], projected["plan_hash"])
        _review, csrf = await self.service.issue_external_csrf(
            projected["plan_id"], pending["challenge_id"]
        )
        first = await self.service.decide_external_approval(
            plan_id=projected["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=projected["plan_hash"],
            approval_kind=pending["approval_kind"],
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal=fixtures.REVIEWER,
        )
        self.assertEqual(first["status"], "approval_pending", first)
        with self.assertRaises(GovernanceError):
            await self.apply(projected)
        self.assertEqual(self.config_rest.deletes(), [])

    async def test_option_e_child_applies_after_elevated_approval(self):
        created = await self.created(("script", "evening"))
        self.ws.entries["script.evening"]["modified_at"] = 9.0
        rollback = await self.service.rollback_change(created["plan_id"])
        delete_plan = self.service.get_plan(rollback["rollback_plan_id"])
        await self.approve(delete_plan)
        result = await self.apply(delete_plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            self.config_rest.deletes(), [("DELETE", "/config/script/config/evening")]
        )

    async def assert_refused_reason(self, plan_id: str, reason: str) -> None:
        writes = len(self.config_rest.writes())
        error = await self.refused(plan_id)
        self.assertEqual(error.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
        self.assertEqual(error.details["reason"], rollback_model.NO_ELIGIBLE_CHILD)
        self.assertEqual(error.details["operations_excluded"][0]["reason"], reason)
        self.assertFalse(error.details["provider_dispatch_occurred"])
        self.assertEqual(len(self.config_rest.writes()), writes)

    async def test_changed_created_at_is_refused(self):
        created = await self.created(("automation", "porch_light"))
        self.ws.entries["automation.porch_light"]["created_at"] = 3.0
        self.ws.entries["automation.porch_light"]["modified_at"] = 3.0
        await self.assert_refused_reason(
            created["plan_id"], rollback_model.IDENTITY_CHANGED
        )

    async def test_different_registry_entry_id_is_refused(self):
        created = await self.created(("automation", "porch_light"))
        self.ws.entries["automation.porch_light"]["id"] = "replacemententry"
        await self.assert_refused_reason(
            created["plan_id"], rollback_model.IDENTITY_CHANGED
        )

    async def test_changed_body_is_refused(self):
        created = await self.created(("automation", "porch_light"))
        self.config_rest.configs["automation"]["porch_light"]["alias"] = "Edited"
        await self.assert_refused_reason(created["plan_id"], rollback_model.CHANGED)

    async def test_already_absent_object_is_not_rolled_back(self):
        created = await self.created(("automation", "porch_light"))
        del self.config_rest.configs["automation"]["porch_light"]
        self.ws.entries.pop("automation.porch_light")
        await self.assert_refused_reason(
            created["plan_id"], rollback_model.ALREADY_ABSENT
        )

    async def test_missing_capture_is_refused_with_zero_reads(self):
        created = await self.created(("automation", "porch_light"))
        (value,) = self.identity_values(created["plan_id"])
        self.runtime.created_identities._path(value["child_id"]).unlink()
        before = self.reads()
        await self.assert_refused_reason(
            created["plan_id"], rollback_model.IDENTITY_NOT_CAPTURED
        )
        self.assertEqual(self.reads(), before, "no rollback-time capture")

    async def test_unadmitted_at_rollback_time_reads_nothing(self):
        created = await self.created(("automation", "porch_light"))
        self.core.withdraw()
        before = self.reads()
        error = await self.refused(created["plan_id"])
        self.assertEqual(error.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(self.reads(), before)
        self.assertIsNone(self.service._load(created["plan_id"]).rollback.request_id)

    async def test_prerequisite_rule_excludes_earlier_creates(self):
        created = await self.created(("automation", "first"), ("script", "second"))
        values = self.identity_values(created["plan_id"])
        second = next(item for item in values if item["operation_id"] == "create_2")
        self.runtime.created_identities._path(second["child_id"]).unlink()
        error = await self.refused(created["plan_id"])
        excluded = {
            item["operation_id"]: item for item in error.details["operations_excluded"]
        }
        self.assertEqual(
            excluded["create_2"]["reason"], rollback_model.IDENTITY_NOT_CAPTURED
        )
        self.assertEqual(
            excluded["create_1"]["reason"], rollback_model.PREREQUISITE_EXCLUDED
        )

    async def test_already_absent_later_create_does_not_block(self):
        created = await self.created(("automation", "first"), ("script", "second"))
        del self.config_rest.configs["script"]["second"]
        self.ws.entries.pop("script.second")
        rollback = await self.service.rollback_change(created["plan_id"])
        self.assertEqual(rollback["status"], "rollback_partial_plan_created", rollback)
        self.assertEqual(len(rollback["operations_to_delete"]), 1)
        self.assertEqual(
            rollback["operations_excluded"],
            [{"operation_id": "create_2", "reason": rollback_model.ALREADY_ABSENT}],
        )


class MixedPlanTests(CreatedObjectRollbackTestCase):
    """§3.4.4: one reverse plan; restores and deletes in strict reverse order."""

    ORIGINAL = None

    async def mixed(self, *, update_first: bool = False) -> dict:
        self.add("automation", "existing")
        self.ORIGINAL = deepcopy(self.config_rest.configs["automation"]["existing"])
        create = {
            "operation_id": "create_1",
            "resource_type": "automation",
            "action": "create",
            "target_id": "new_one",
            "proposed_config": automation_config("new_one"),
        }
        update = {
            "operation_id": "update_1",
            "resource_type": "automation",
            "action": "update",
            "target_id": "existing",
            "proposed_config": automation_config("existing", target="light.hall"),
        }
        first, second = (update, create) if update_first else (create, update)
        second["depends_on"] = [first["operation_id"]]
        created = await self.service.create_configuration_plan(
            title="Mixed", description="Mixed", operations=[first, second]
        )
        await self.approve(created)
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        return created

    def rollback_operations(self, rollback: dict) -> list:
        plan = self.service._load(rollback["rollback_plan_id"])
        return sorted(plan.operations, key=lambda item: item.order)

    async def test_restore_then_delete_in_strict_reverse_order(self):
        created = await self.mixed()
        writes = len(self.config_rest.writes())
        rollback = await self.service.rollback_change(created["plan_id"])
        self.assertEqual(rollback["status"], "rollback_plan_created", rollback)
        self.assertEqual(rollback["operations_to_delete"], ["create_1"])
        self.assertEqual(len(rollback["operations_to_restore"]), 1)
        self.assertEqual(rollback["operations_excluded"], [])
        self.assertEqual(len(self.config_rest.writes()), writes, "planning never writes")
        restore, delete = self.rollback_operations(rollback)
        self.assertEqual((restore.action, restore.target_id), ("update", "existing"))
        self.assertEqual(restore.proposed_config, self.ORIGINAL)
        self.assertEqual((delete.action, delete.target_id), ("delete", "new_one"))
        self.assertEqual(restore.depends_on, [])
        self.assertEqual(delete.depends_on, [])
        self.assertEqual(rollback_model.markers(restore), [])
        (value,) = rollback_model.markers(delete)
        self.assertEqual(value["source_plan_id"], created["plan_id"])
        plan = self.service._load(rollback["rollback_plan_id"])
        self.assertNotEqual(plan.policy_decision.policy_class.value, "prohibited")
        rollback_plan = self.service.get_plan(rollback["rollback_plan_id"])
        await self.approve(rollback_plan)
        result = await self.apply(rollback_plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            self.config_rest.writes()[writes:],
            [
                ("POST", "/config/automation/config/existing"),
                ("DELETE", "/config/automation/config/new_one"),
            ],
        )
        self.assertEqual(self.config_rest.configs["automation"]["existing"], self.ORIGINAL)
        self.assertNotIn("new_one", self.config_rest.configs["automation"])

    async def test_stale_prerequisite_restore_excludes_the_delete(self):
        created = await self.mixed()
        self.config_rest.configs["automation"]["existing"]["alias"] = "Edited"
        writes = len(self.config_rest.writes())
        gets = self.config_rest.config_gets.get(("automation", "new_one"), 0)
        error = await self.refused(created["plan_id"])
        self.assertEqual(error.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
        self.assertEqual(error.details["reason"], rollback_model.NO_ELIGIBLE_CHILD)
        excluded = {
            item["operation_id"]: item for item in error.details["operations_excluded"]
        }
        self.assertEqual(
            excluded["update_1"]["reason"], rollback_model.RESTORE_TARGET_CHANGED
        )
        self.assertEqual(
            excluded["create_1"]["reason"], rollback_model.PREREQUISITE_EXCLUDED
        )
        self.assertEqual(excluded["create_1"]["prerequisite"], "update_1")
        self.assertFalse(error.details["provider_dispatch_occurred"])
        self.assertEqual(len(self.config_rest.writes()), writes)
        self.assertEqual(
            self.config_rest.config_gets.get(("automation", "new_one"), 0),
            gets,
            "the excluded delete target is never read at rollback time",
        )

    async def test_unverified_prerequisite_restore_excludes_the_delete(self):
        created = await self.mixed()
        task = self.service.task_repository.get_for_plan(created["plan_id"])
        declarations = self.runtime.children.declarations_for_task(task.task_id)
        update_child = next(
            item["child_id"]
            for item in declarations
            if item["operation_id"] == "update_1"
        )
        original_get = self.runtime.children.get

        def unverified(child_id):
            record = original_get(child_id)
            if child_id == update_child and record is not None:
                record = deepcopy(record)
                record.evidence = {}
            return record

        before = self.reads()
        with mock.patch.object(self.runtime.children, "get", unverified):
            error = await self.refused(created["plan_id"])
        excluded = {
            item["operation_id"]: item for item in error.details["operations_excluded"]
        }
        self.assertEqual(
            excluded["update_1"]["reason"], rollback_model.RESTORE_NOT_VERIFIED
        )
        self.assertEqual(
            excluded["create_1"]["reason"], rollback_model.PREREQUISITE_EXCLUDED
        )
        self.assertEqual(self.reads(), before, "local exclusion reads nothing")

    async def test_excluded_delete_does_not_block_a_later_restore(self):
        created = await self.mixed(update_first=True)
        values = self.identity_values(created["plan_id"])
        (capture,) = [item for item in values if item is not None]
        self.runtime.created_identities._path(capture["child_id"]).unlink()
        rollback = await self.service.rollback_change(created["plan_id"])
        self.assertEqual(rollback["status"], "rollback_partial_plan_created", rollback)
        self.assertEqual(rollback.get("operations_to_delete", []), [])
        self.assertEqual(len(rollback["operations_to_restore"]), 1)
        self.assertEqual(
            rollback["operations_excluded"],
            [
                {
                    "operation_id": "create_1",
                    "reason": rollback_model.IDENTITY_NOT_CAPTURED,
                }
            ],
        )
        (restore,) = self.rollback_operations(rollback)
        self.assertEqual(restore.proposed_config, self.ORIGINAL)

    async def test_unadmitted_delete_is_excluded_and_reads_nothing_for_it(self):
        created = await self.mixed()
        self.core.withdraw()
        gets = dict(self.config_rest.config_gets)
        rollback = await self.service.rollback_change(created["plan_id"])
        self.assertEqual(rollback["status"], "rollback_partial_plan_created", rollback)
        self.assertEqual(len(rollback["operations_to_restore"]), 1)
        self.assertEqual(
            rollback["operations_excluded"],
            [
                {
                    "operation_id": "create_1",
                    "reason": "pending_signed_registry_admission",
                }
            ],
        )
        self.assertEqual(
            self.config_rest.config_gets.get(("automation", "new_one")),
            gets.get(("automation", "new_one")),
        )

    async def test_failed_restore_leaves_the_delete_unattempted(self):
        created = await self.mixed()
        rollback = await self.service.rollback_change(created["plan_id"])
        rollback_plan = self.service.get_plan(rollback["rollback_plan_id"])
        await self.approve(rollback_plan)
        original = self.config_rest.request

        async def failing(method, path, **kwargs):
            if method == "POST" and str(path) == "/config/automation/config/existing":
                self.config_rest.requests.append((method, str(path)))
                raise fixtures.HomeAssistantApiError(
                    details={"status": 500, "provider_response_received": True}
                )
            return await original(method, path, **kwargs)

        with mock.patch.object(self.config_rest, "request", failing):
            result = await self.apply(rollback_plan)
        self.assertNotEqual(result["task_state"], "succeeded_verified", result)
        restore, delete = self.child_records(result["task_id"])
        self.assertIsNotNone(restore)
        self.assertNotEqual(restore.normalized_outcome, "succeeded_verified")
        self.assertIsNone(delete, "the delete after a failed restore never starts")
        self.assertEqual(self.config_rest.deletes(), [])
        self.assertIn("new_one", self.config_rest.configs["automation"])
        task = self.service._load_task(result["task_id"])
        children = {
            item["operation_id"]: item
            for item in task.verification_summary["children"]
        }
        self.assertEqual(
            children[delete_id(rollback_plan)]["state"], "not_started", children
        )

    async def test_option_e_child_in_a_mixed_plan_is_the_only_elevated_child(self):
        created = await self.mixed()
        self.ws.entries["automation.new_one"]["modified_at"] = 9.0
        rollback = await self.service.rollback_change(created["plan_id"])
        self.assertEqual(rollback["status"], "rollback_plan_created", rollback)
        plan = self.service._load(rollback["rollback_plan_id"])
        self.assertEqual(plan.policy_decision.policy_class.value, "elevated_admin")
        restore, delete = self.rollback_operations(rollback)
        (value,) = rollback_model.markers(delete)
        self.assertEqual(value["basis"], rollback_model.BASIS_MODIFIED_SINCE_CREATE)
        self.assertEqual(rollback_model.markers(restore), [])
        pending = self.service.approve(rollback["rollback_plan_id"], rollback["plan_hash"])
        self.assertIn("challenge_id", pending, pending)
        rollback_plan = self.service.get_plan(rollback["rollback_plan_id"])
        await self.approve(rollback_plan)
        result = await self.apply(rollback_plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertNotIn("new_one", self.config_rest.configs["automation"])

    async def test_mixed_plan_without_markers_or_with_order_is_prohibited(self):
        created = await self.mixed()
        rollback = await self.service.rollback_change(created["plan_id"])
        plan = self.service._load(rollback["rollback_plan_id"])
        self.assertNotEqual(evaluate_change_policy(plan).policy_class.value, "prohibited")
        index = next(
            position
            for position, item in enumerate(plan.operations)
            if item.action == "delete"
        )
        unmarked = deepcopy(plan)
        unmarked.operations[index].risk.evidence = [
            item
            for item in unmarked.operations[index].risk.evidence
            if item.get("trigger") != rollback_model.POLICY_TRIGGER
        ]
        ordered = deepcopy(plan)
        restore = next(item for item in ordered.operations if item.action == "update")
        restore.depends_on = ["x"]
        created_child = deepcopy(plan)
        other = next(
            item for item in created_child.operations if item.action == "update"
        )
        other.action = "create"
        for name, forged in (
            ("unmarked", unmarked),
            ("ordered", ordered),
            ("create", created_child),
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    evaluate_change_policy(forged).policy_class.value, "prohibited"
                )

    async def test_restores_are_refused_outside_the_created_object_route(self):
        self.add("automation", "existing")
        operations = [
            {
                "operation_id": "restore_1",
                "resource_type": "automation",
                "action": "update",
                "target_id": "existing",
                "proposed_config": automation_config("existing"),
            }
        ]
        for caller_context in (None, {"source": "other"}):
            with self.subTest(caller_context=caller_context):
                with self.assertRaises(GovernanceError) as raised:
                    await self.service._create_configuration_plan(
                        title="x",
                        description="x",
                        operations=operations,
                        caller_context=caller_context,
                        _created_object_restores={"restore_1": "0" * 64},
                        _before_persist=lambda: None,
                    )
                self.assertEqual(
                    raised.exception.code, ErrorCode.CONFIGURATION_VALIDATION_FAILED
                )


def delete_id(plan: dict) -> str:
    return next(
        item["operation_id"] for item in plan["operations"] if item["action"] == "delete"
    )


class PolicyAndPreflightTests(CreatedObjectRollbackTestCase):
    async def rollback_plan(self) -> tuple[dict, dict]:
        created = await self.created(("automation", "porch_light"))
        rollback = await self.service.rollback_change(created["plan_id"])
        return created, rollback

    async def test_tampered_marker_is_prohibited(self):
        _created, rollback = await self.rollback_plan()
        plan = self.service._load(rollback["rollback_plan_id"])
        cases = {
            "ownership": "modified_since_create",
            "registry_entry_id": "other",
            "created_fingerprint": "0" * 64,
            "field": "delete_9",
            "extra": "x",
        }
        for key, value in cases.items():
            with self.subTest(key=key):
                forged = deepcopy(plan)
                (marker,) = rollback_model.markers(forged.operations[0])
                marker[key] = value
                decision = evaluate_change_policy(forged)
                self.assertEqual(decision.policy_class.value, "prohibited")
        doubled = deepcopy(plan)
        (marker,) = rollback_model.markers(doubled.operations[0])
        doubled.operations[0].risk.evidence.append(dict(marker))
        self.assertEqual(
            evaluate_change_policy(doubled).policy_class.value, "prohibited"
        )

    async def test_option_e_never_sets_safety_critical_by_itself(self):
        created = await self.created(("automation", "porch_light"))
        self.ws.entries["automation.porch_light"]["modified_at"] = 2.0
        rollback = await self.service.rollback_change(created["plan_id"])
        plan = self.service._load(rollback["rollback_plan_id"])
        owned = deepcopy(plan)
        (marker,) = rollback_model.markers(owned.operations[0])
        marker["observed_modified_at"] = marker["captured_modified_at"]
        marker["ownership"] = rollback_model.OWNED
        marker["basis"] = ""
        elevated = evaluate_change_policy(plan)
        baseline = evaluate_change_policy(owned)
        self.assertEqual(elevated.policy_class.value, "elevated_admin")
        # The ownership basis changes the class and the reasons only; the
        # physical consequence is the unchanged HAMCP-155 one, and no
        # safety-critical acknowledgement is added by it.
        self.assertEqual(elevated.physical_consequence, baseline.physical_consequence)
        self.assertIn(rollback_model.BASIS_MODIFIED_SINCE_CREATE, elevated.reason_codes)
        self.assertNotIn(rollback_model.BASIS_MODIFIED_SINCE_CREATE, baseline.reason_codes)
        self.assertNotIn(
            "safety_critical",
            {item.value for item in elevated.required_acknowledgements}
            - {item.value for item in baseline.required_acknowledgements},
        )
        self.assertFalse(
            any(
                item.value.startswith("safety")
                for item in elevated.required_acknowledgements
            )
        )

    async def test_marker_change_changes_the_plan_hash_binding(self):
        _created, rollback = await self.rollback_plan()
        plan = self.service._load(rollback["rollback_plan_id"])
        forged = deepcopy(plan)
        (marker,) = rollback_model.markers(forged.operations[0])
        marker["observed_entity_id"] = "automation.other"
        forged.policy_decision = evaluate_change_policy(forged)
        self.assertNotEqual(self.service.plan_hash(forged), rollback["plan_hash"])

    async def test_preflight_refuses_a_registry_change_after_planning(self):
        _created, rollback = await self.rollback_plan()
        delete_plan = self.service.get_plan(rollback["rollback_plan_id"])
        await self.approve(delete_plan)
        self.ws.entries["automation.porch_light"]["modified_at"] = 11.0
        result = await self.apply(delete_plan)
        self.assertNotEqual(result.get("task_state"), "succeeded_verified", result)
        self.assertEqual(self.config_rest.deletes(), [])
        self.assertIn("porch_light", self.config_rest.configs["automation"])


class ModelUnitTests(unittest.TestCase):
    def identity(self) -> dict:
        return identity_model.identity_from(
            "automation",
            "porch_light",
            {
                "platform": "automation",
                "unique_id": "porch_light",
                "entity_id": "automation.porch_light",
                "id": "reg1",
                "created_at": 1.5,
                "modified_at": 2.5,
            },
            "f" * 64,
        )

    def test_identity_correspondence_checks(self):
        identity = self.identity()
        self.assertEqual(identity_model.validate_identity(identity), identity)
        for changes in (
            {"platform": "script"},
            {"unique_id": "other"},
            {"entity_id": "script.porch_light"},
            {"id": ""},
            {"created_at": None},
            {"modified_at": True},
        ):
            with self.subTest(changes=changes):
                entry = {
                    "platform": "automation",
                    "unique_id": "porch_light",
                    "entity_id": "automation.porch_light",
                    "id": "reg1",
                    "created_at": 1.5,
                    "modified_at": 2.5,
                    **changes,
                }
                with self.assertRaises(identity_model.IdentityMismatch):
                    identity_model.identity_from("automation", "porch_light", entry, "f" * 64)
        forged = dict(identity, entity_id="automation.other")
        with self.assertRaises(identity_model.IdentityMismatch):
            identity_model.validate_identity(forged)

    def test_observed_ownership_classification(self):
        identity = self.identity()
        entry = {
            "platform": "automation",
            "unique_id": "porch_light",
            "entity_id": "automation.porch_light",
            "id": "reg1",
            "created_at": 1.5,
            "modified_at": 2.5,
        }
        observe = identity_model.observed_ownership
        self.assertEqual(observe(identity, entry, "f" * 64)["status"], "owned")
        self.assertEqual(
            observe(identity, dict(entry, modified_at=3.0), "f" * 64)["status"],
            "modified_since_create",
        )
        self.assertEqual(
            observe(identity, dict(entry, created_at=3.0), "f" * 64)["status"],
            "created_object_identity_changed",
        )
        self.assertEqual(
            observe(identity, dict(entry, id="reg2"), "f" * 64)["status"],
            "created_object_identity_changed",
        )
        self.assertEqual(
            observe(identity, entry, "e" * 64)["status"], "created_object_changed"
        )
        self.assertEqual(
            observe(identity, None, None)["status"], "created_object_already_absent"
        )


if __name__ == "__main__":
    unittest.main()
