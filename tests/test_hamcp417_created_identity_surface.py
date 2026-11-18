"""HAMCP-417 (E049): get_execution_task and get_change_plan show created_identity.

The HAMCP-298 capture (``docs/HAMCP_329_298_SCENE_CREATE_AND_CREATE_ROLLBACK_DESIGN.md``
§3.2.2) stores each verified create's identity in a sidecar keyed by the
child execution.  These tests prove the read surfaces now report the
recorded outcome and the assigned ids, read-only: no Home Assistant request,
no change to the persisted task, child or sidecar record, and no identity
from a record that is not bound to the child's own plan and operation.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.governance import (  # noqa: E402
    created_object_identity as identity_model,
)

from tests.hamcp329_298_fixtures import (  # noqa: E402
    CreatedObjectServiceTestCase,
    EVENING,
    EVENING_ENTITY,
    HELPER_PROFILE,
    boolean_config,
    helper_operation,
)


PUBLIC_FIELDS = {
    "resource_type",
    "resource_target",
    "storage_object_id",
    "entity_id",
    "registry_entry_id",
    "registry_unique_id",
}
INTERNAL_FIELDS = {
    "model",
    "registry_domain",
    "registry_platform",
    "registry_created_at",
    "registry_modified_at",
    "delete_argument",
    "created_fingerprint",
    "capture_fingerprint",
}


class CreatedIdentitySurfaceTests(CreatedObjectServiceTestCase):
    def surfaces(self, plan_id: str) -> tuple[list[dict], list[dict]]:
        """(task f3_children, plan execution_task.created_identities)."""

        task = self.service.get_execution_task(self.task_id(plan_id))
        plan = self.service.get_plan(plan_id)
        return task["f3_children"], plan["execution_task"]["created_identities"]

    def persisted(self, plan_id: str) -> tuple[str, list[bytes]]:
        task = self.service.task_repository.get_for_plan(plan_id)
        files = sorted(self.runtime.created_identities.root.glob("*.json"))
        return (
            json.dumps(task.to_dict(), sort_keys=True),
            [path.read_bytes() for path in files],
        )

    def assert_public_identity(self, shown: dict, stored: dict) -> None:
        self.assertEqual(set(shown), PUBLIC_FIELDS)
        self.assertFalse(INTERNAL_FIELDS & set(shown))
        self.assertEqual(shown, {key: stored[key] for key in PUBLIC_FIELDS})

    async def test_captured_helper_identity_is_shown_on_task_and_plan(self):
        created = await self.created_helper("input_boolean", boolean_config())
        (stored,) = self.identity_values(created["plan_id"])
        self.assertEqual(stored["outcome"], identity_model.CAPTURED, stored)
        mark = self.mark()
        children, rows = self.surfaces(created["plan_id"])
        self.assertEqual(self.reads_since(mark), [])
        (child,) = children
        self.assertEqual(child["created_identity_capture"], "captured")
        self.assert_public_identity(child["created_identity"], stored["identity"])
        self.assertEqual(
            child["created_identity"]["entity_id"], "input_boolean.hvac_override"
        )
        self.assertEqual(
            child["created_identity"]["storage_object_id"], "hvac_override"
        )
        self.assertEqual(
            rows,
            [
                {
                    "operation_id": "create_helper",
                    "child_execution_id": child["child_execution_id"],
                    "created_identity_capture": "captured",
                    "created_identity": child["created_identity"],
                }
            ],
        )

    async def test_captured_scene_identity_reports_the_core_assigned_entity(self):
        created = await self.created_scene()
        (stored,) = self.identity_values(created["plan_id"])
        (child,), (row,) = self.surfaces(created["plan_id"])
        self.assertEqual(child["created_identity_capture"], "captured")
        self.assert_public_identity(child["created_identity"], stored["identity"])
        self.assertEqual(child["created_identity"]["entity_id"], EVENING_ENTITY)
        self.assertEqual(child["created_identity"]["resource_target"], EVENING)
        self.assertEqual(row["created_identity"], child["created_identity"])

    async def test_uncaptured_outcome_is_shown_without_an_identity(self):
        self.core.withdraw(HELPER_PROFILE)
        created = await self.created_helper("input_boolean")
        (child,), (row,) = self.surfaces(created["plan_id"])
        self.assertEqual(child["created_identity_capture"], "capability_unadmitted")
        self.assertIsNone(child["created_identity"])
        self.assertEqual(row["created_identity_capture"], "capability_unadmitted")
        self.assertIsNone(row["created_identity"])

    async def test_update_child_shows_no_capture_and_no_plan_row(self):
        self.add("automation", "consumer")
        proposed = deepcopy(self.config_rest.configs["automation"]["consumer"])
        proposed["description"] = "edited before the create"
        created = await self.applied(
            await self.configuration_plan(
                {
                    "operation_id": "update_consumer",
                    "resource_type": "automation",
                    "action": "update",
                    "target_id": "consumer",
                    "depends_on": [],
                    "proposed_config": proposed,
                },
                helper_operation(
                    "input_boolean", boolean_config(), depends_on=["update_consumer"]
                ),
            )
        )
        children, rows = self.surfaces(created["plan_id"])
        by_operation = {child["operation_id"]: child for child in children}
        self.assertIsNone(by_operation["update_consumer"]["created_identity_capture"])
        self.assertIsNone(by_operation["update_consumer"]["created_identity"])
        self.assertEqual(
            by_operation["create_helper"]["created_identity_capture"], "captured"
        )
        self.assertEqual([row["operation_id"] for row in rows], ["create_helper"])

    async def test_reading_the_surfaces_changes_no_persisted_record(self):
        created = await self.created_helper("input_boolean")
        before = self.persisted(created["plan_id"])
        self.surfaces(created["plan_id"])
        self.surfaces(created["plan_id"])
        self.assertEqual(self.persisted(created["plan_id"]), before)
        # The durable schema-1 child summary keeps its closed shape.
        task = self.service.task_repository.get_for_plan(created["plan_id"])
        for item in task.verification_summary["children"]:
            self.assertNotIn("created_identity", item)
            self.assertNotIn("created_identity_capture", item)

    async def test_record_bound_to_another_plan_or_operation_is_not_shown(self):
        created = await self.created_helper("input_boolean")
        (stored,) = self.identity_values(created["plan_id"])
        path = self.runtime.created_identities._path(stored["child_id"])
        for field in ("plan_id", "operation_id"):
            with self.subTest(field=field):
                forged = {**deepcopy(stored), field: "f" * 32}
                forged["record_hash"] = identity_model.record_hash(forged)
                path.write_text(json.dumps(forged), encoding="utf-8")
                self.assertIsNotNone(
                    self.runtime.created_identities.get(stored["child_id"])
                )
                (child,), rows = self.surfaces(created["plan_id"])
                self.assertIsNone(child["created_identity_capture"])
                self.assertIsNone(child["created_identity"])
                self.assertEqual(rows, [])

    async def test_unreadable_sidecar_shows_nothing_and_the_read_still_succeeds(self):
        created = await self.created_helper("input_boolean")
        (stored,) = self.identity_values(created["plan_id"])
        path = self.runtime.created_identities._path(stored["child_id"])
        for content in ("{not json", json.dumps({**stored, "record_hash": "0" * 64})):
            with self.subTest(content=content[:12]):
                path.write_text(content, encoding="utf-8")
                mark = self.mark()
                (child,), rows = self.surfaces(created["plan_id"])
                self.assertEqual(self.reads_since(mark), [])
                self.assertIsNone(child["created_identity_capture"])
                self.assertIsNone(child["created_identity"])
                self.assertEqual(rows, [])

    async def test_unapplied_create_shows_no_capture(self):
        plan = await self.configuration_plan(
            helper_operation("input_boolean", boolean_config())
        )
        await self.approve(plan)
        shown = self.service.get_plan(plan["plan_id"])
        self.assertNotIn("created_identities", shown["execution_task"])
        self.assertIsNone(shown["execution_task"]["task_id"])


if __name__ == "__main__":
    unittest.main()
