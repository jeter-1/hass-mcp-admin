"""HAMCP-531: governed undo of a storage helper update drops only ``id``.

Core's ``<helper>/list`` item carries the helper's storage ``id``, and the
F3 source operation captures that item as ``current_config``. The helper
validators accept no ``id`` field, so a reverse proposal copied verbatim
was refused at rollback planning. The reverse proposal now omits ``id`` for
a helper operation and nothing else (HAMCP-346 design section 5.7 item 1);
every other captured field is still validated. Two sites build that
proposal: ``_plan_fresh_rollback`` (a plan of updates only) and the restore
leg of ``_create_created_object_rollback`` (a plan that also creates, both
its restore-and-delete and its restore-only branches). All stores and
configuration peers are synthetic.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import GovernanceError  # noqa: E402
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3RuntimeIntegration,
)
from tests.hamcp329_298_fixtures import (  # noqa: E402
    CreatedObjectServiceTestCase,
    boolean_config,
    helper_operation,
    number_config,
)
from tests.test_dev14_configuration_plans import (  # noqa: E402
    ConfigurationPlanTestCase,
)
from tests.test_f3_runtime_integration import (  # noqa: E402
    _ExactFakeConfigurationGateway,
    _provider_identity,
)

BOOLEAN_TARGET = "input_boolean.synthetic_undo_flag"
BOOLEAN_BEFORE = {"id": "synthetic_undo_flag", "name": "Synthetic undo flag"}
BOOLEAN_PROPOSED = {"name": "Synthetic undo flag renamed", "icon": "mdi:flag"}

NUMBER_TARGET = "input_number.synthetic_undo_level"
NUMBER_BEFORE = {
    "id": "synthetic_undo_level",
    "name": "Synthetic undo level",
    "min": 0,
    "max": 10,
    "step": 1,
    "mode": "box",
}
NUMBER_PROPOSED = {
    "name": "Synthetic undo level",
    "min": 0,
    "max": 20,
    "step": 2,
    "mode": "slider",
}


def _writes(gateway):
    return [call for call in gateway.calls if call[0] == "write"]


class HelperUndoIdTests(ConfigurationPlanTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=_ExactFakeConfigurationGateway(self.gateway),
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=_provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")

    async def apply_update(self, helper_type, target_id, before, proposed):
        self.gateway.configs[(helper_type, target_id)] = copy.deepcopy(before)
        created = await self.service.create_configuration_plan(
            title=f"Synthetic {helper_type} update",
            description="HAMCP-531 synthetic undo fixture",
            operations=[
                {
                    "operation_id": "update_helper",
                    "resource_type": "helper",
                    "helper_type": helper_type,
                    "action": "update",
                    "target_id": target_id,
                    "depends_on": [],
                    "proposed_config": copy.deepcopy(proposed),
                }
            ],
        )
        await self.approve(created)
        applied = await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(applied["task_state"], "succeeded_verified")
        return self.service._load(created["plan_id"])

    async def assert_undo_drops_only_id(self, helper_type, target_id, before, proposed):
        source = await self.apply_update(helper_type, target_id, before, proposed)
        captured = source.operations[0].current_config
        # The captured pre-state is Core's list item, storage id included.
        self.assertEqual(captured, before)
        self.assertIn("id", captured)
        writes = len(_writes(self.gateway))

        rollback = await self.service.rollback_change(
            source.plan_id, self.service.plan_hash(source)
        )

        self.assertEqual(rollback["status"], "rollback_plan_created")
        self.assertTrue(rollback["approval_required"])
        self.assertEqual(rollback["operations_excluded"], [])
        reverse = self.service._load(rollback["rollback_plan_id"])
        self.assertEqual(len(reverse.operations), 1)
        operation = reverse.operations[0]
        self.assertEqual(operation.resource_type, "helper")
        self.assertEqual(operation.helper_type, helper_type)
        self.assertEqual(operation.action, "update")
        self.assertEqual(operation.target_id, target_id)
        self.assertNotIn("id", operation.proposed_config)
        self.assertEqual(
            operation.proposed_config,
            {key: value for key, value in before.items() if key != "id"},
        )
        # The source plan's captured pre-state is not altered.
        self.assertEqual(
            self.service._load(source.plan_id).operations[0].current_config, before
        )
        # Planning the undo writes nothing.
        self.assertEqual(len(_writes(self.gateway)), writes)
        return reverse

    async def test_input_boolean_undo_plan_has_no_id(self):
        await self.assert_undo_drops_only_id(
            "input_boolean", BOOLEAN_TARGET, BOOLEAN_BEFORE, BOOLEAN_PROPOSED
        )

    async def test_input_number_undo_plan_has_no_id(self):
        await self.assert_undo_drops_only_id(
            "input_number", NUMBER_TARGET, NUMBER_BEFORE, NUMBER_PROPOSED
        )

    async def test_input_boolean_undo_applies_and_restores_the_item(self):
        reverse = await self.assert_undo_drops_only_id(
            "input_boolean", BOOLEAN_TARGET, BOOLEAN_BEFORE, BOOLEAN_PROPOSED
        )
        reverse_hash = self.service.plan_hash(reverse)
        created = {"plan_id": reverse.plan_id, "plan_hash": reverse_hash}
        await self.approve(created)
        writes = len(_writes(self.gateway))

        applied = await self.service.apply(reverse.plan_id, reverse_hash)

        self.assertEqual(applied["task_state"], "succeeded_verified")
        self.assertEqual(
            _writes(self.gateway)[writes:],
            [("write", "update", "input_boolean", BOOLEAN_TARGET)],
        )
        self.assertEqual(
            self.gateway.configs[("input_boolean", BOOLEAN_TARGET)], BOOLEAN_BEFORE
        )

    async def test_input_number_undo_applies_and_restores_the_item(self):
        reverse = await self.assert_undo_drops_only_id(
            "input_number", NUMBER_TARGET, NUMBER_BEFORE, NUMBER_PROPOSED
        )
        reverse_hash = self.service.plan_hash(reverse)
        await self.approve({"plan_id": reverse.plan_id, "plan_hash": reverse_hash})
        writes = len(_writes(self.gateway))

        applied = await self.service.apply(reverse.plan_id, reverse_hash)

        self.assertEqual(applied["task_state"], "succeeded_verified")
        self.assertEqual(
            _writes(self.gateway)[writes:],
            [("write", "update", "input_number", NUMBER_TARGET)],
        )
        self.assertEqual(
            self.gateway.configs[("input_number", NUMBER_TARGET)], NUMBER_BEFORE
        )

    async def test_other_captured_unknown_field_still_refuses_by_name(self):
        # Only ``id`` is dropped: a captured field the validator does not
        # know (here a synthetic device_class) still fails closed, naming it.
        before = {**NUMBER_BEFORE, "device_class": "temperature"}
        source = await self.apply_update(
            "input_number", NUMBER_TARGET, before, NUMBER_PROPOSED
        )
        writes = len(_writes(self.gateway))

        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(
                source.plan_id, self.service.plan_hash(source)
            )

        self.assertEqual(
            raised.exception.details["validation_errors"],
            ["input_number config contains unsupported fields: device_class"],
        )
        self.assertEqual(len(_writes(self.gateway)), writes)
        # No reverse plan is stored.
        self.assertEqual(self.repository.navigation_plan_ids(), (source.plan_id,))

    async def test_automation_undo_keeps_its_captured_id(self):
        # ``id`` is a valid automation field; non-helper reverse proposals
        # stay the exact captured configuration.
        created = await self.create_automation_plan()
        await self.approve(created)
        await self.service.apply(created["plan_id"], created["plan_hash"])
        source = self.service._load(created["plan_id"])
        captured = source.operations[0].current_config
        self.assertIn("id", captured)

        rollback = await self.service.rollback_change(
            source.plan_id, self.service.plan_hash(source)
        )

        reverse = self.service._load(rollback["rollback_plan_id"])
        self.assertEqual(reverse.operations[0].resource_type, "automation")
        self.assertEqual(reverse.operations[0].proposed_config, captured)



class CreatedObjectRestoreIdTests(CreatedObjectServiceTestCase):
    """The restore leg of a rollback whose source plan also created a helper."""

    async def mixed_source(self, domain, existing, changed):
        object_id = existing["name"].lower().replace(" ", "_")
        self.ws.add_helper(domain, existing)
        update = helper_operation(
            domain, changed, operation_id="update_existing", action="update"
        )
        update["target_id"] = f"{domain}.{object_id}"
        created = await self.applied(
            await self.configuration_plan(
                helper_operation(domain, boolean_config("Synthetic new flag")
                                 if domain == "input_boolean"
                                 else number_config("Synthetic new level")),
                update,
            )
        )
        captured = self.service._load(created["plan_id"]).operations[1].current_config
        # The captured pre-state is Core's list item, storage id included.
        self.assertEqual(captured, {"id": object_id, **existing})
        return created, object_id, captured

    def assert_restore_drops_only_id(self, rollback, domain, object_id, captured):
        restores = [
            item
            for item in self.operations(rollback["rollback_plan_id"])
            if item.action == "update"
        ]
        self.assertEqual(len(restores), 1)
        restore = restores[0]
        self.assertEqual(
            (restore.resource_type, restore.helper_type, restore.target_id),
            ("helper", domain, f"{domain}.{object_id}"),
        )
        self.assertNotIn("id", restore.proposed_config)
        self.assertEqual(
            restore.proposed_config,
            {key: value for key, value in captured.items() if key != "id"},
        )

    async def assert_mixed_undo(self, domain, existing, changed):
        created, object_id, captured = await self.mixed_source(
            domain, existing, changed
        )
        mark = self.mark()

        rollback, plan = await self.rollback_plan(created)

        self.assertEqual(rollback["operations_to_delete"], ["create_helper"])
        self.assertEqual(len(rollback["operations_to_restore"]), 1)
        self.assertEqual(
            [
                (item.resource_type, item.action)
                for item in self.operations(rollback["rollback_plan_id"])
            ],
            [("helper", "update"), ("helper", "delete")],
        )
        self.assert_restore_drops_only_id(rollback, domain, object_id, captured)
        # The source plan's captured pre-state is not altered.
        self.assertEqual(
            self.service._load(created["plan_id"]).operations[1].current_config,
            captured,
        )
        # Planning the undo writes nothing.
        self.assertEqual(self.writes_since(mark), [])

        result = await self.apply(plan)

        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            self.ws.helper_commands("update")[-1],
            {
                "type": f"{domain}/update",
                f"{domain}_id": object_id,
                **existing,
            },
        )
        self.assertEqual(self.ws.helpers[domain][object_id], captured)
        self.assertEqual(sorted(self.ws.helpers[domain]), [object_id])

    async def test_input_boolean_restore_and_delete_undo_has_no_id(self):
        await self.assert_mixed_undo(
            "input_boolean",
            boolean_config("Synthetic existing flag"),
            {"name": "Synthetic existing flag", "icon": "mdi:flag"},
        )

    async def test_input_number_restore_and_delete_undo_has_no_id(self):
        await self.assert_mixed_undo(
            "input_number",
            number_config("Synthetic existing level"),
            {**number_config("Synthetic existing level"), "max": 40},
        )

    async def test_restore_only_undo_has_no_id(self):
        # The created helper is gone, so only the restore leg remains.
        created, object_id, captured = await self.mixed_source(
            "input_boolean",
            boolean_config("Synthetic existing flag"),
            {"name": "Synthetic existing flag", "icon": "mdi:flag"},
        )
        self.remove_helper("input_boolean", "synthetic_new_flag")
        mark = self.mark()

        rollback = await self.service.rollback_change(created["plan_id"])

        self.assertEqual(rollback["status"], "rollback_partial_plan_created", rollback)
        self.assertEqual(rollback["operations_to_delete"], [])
        self.assertEqual(
            rollback["operations_excluded"],
            [
                {
                    "operation_id": "create_helper",
                    "reason": "created_object_already_absent",
                }
            ],
        )
        self.assertEqual(
            [
                (item.resource_type, item.action)
                for item in self.operations(rollback["rollback_plan_id"])
            ],
            [("helper", "update")],
        )
        self.assert_restore_drops_only_id(
            rollback, "input_boolean", object_id, captured
        )
        self.assertEqual(self.writes_since(mark), [])

        result = await self.apply_rollback(rollback)

        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(self.ws.helpers["input_boolean"][object_id], captured)

    async def test_restore_leg_other_unknown_field_still_refuses_by_name(self):
        # Only ``id`` is dropped here too: a captured synthetic device_class
        # still fails closed, naming it, and nothing is written.
        existing = {**number_config("Synthetic existing level"),
                    "device_class": "temperature"}
        changed = number_config("Synthetic existing level")
        created, _object_id, _captured = await self.mixed_source(
            "input_number", existing, changed
        )
        before = len(self.writes())

        error = await self.refused(created["plan_id"])

        self.assertEqual(
            error.details["validation_errors"],
            ["input_number config contains unsupported fields: device_class"],
        )
        self.assertEqual(self.writes()[before:], [])


if __name__ == "__main__":
    unittest.main()
