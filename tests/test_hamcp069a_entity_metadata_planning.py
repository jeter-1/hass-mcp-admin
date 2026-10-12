"""HAMCP-069A-1 planning, policy and refusal tests.

Every case runs through the shipped service, planner and registry gateway
against the synthetic offline registry.  Refusals are proven to leave the plan
store empty and to send no write to Home Assistant; authorized changes are
proven to plan, approve, dispatch one closed update and verify.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.entity_metadata_disclosure import (  # noqa: E402
    EntityMetadataEvidenceUnavailable,
)

from tests.hamcp069a_entity_fixtures import (  # noqa: E402
    EntityMetadataServiceTestCase,
    action_profile,
    dependency_snapshot,
)


class PlanningTestCase(EntityMetadataServiceTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.ws.labels = [
            {"label_id": "night", "name": "Night"},
            {"label_id": "outdoor", "name": "Outdoor"},
        ]
        self.ws.areas = [
            {"area_id": "porch", "name": "Porch"},
            {"area_id": "garage", "name": "Garage"},
        ]
        self.ws.categories = {"automation": [{"category_id": "c1", "name": "One"}]}

    async def refused(self, code: ErrorCode, reason: str, **arguments) -> dict:
        with self.assertRaises(GovernanceError) as raised:
            await self.create(**arguments)
        error = raised.exception
        self.assertEqual(error.code, code, error.details)
        self.assertEqual(error.details["reason_code"], reason)
        self.assertIs(error.details["plan_created"], False)
        self.assertIs(error.details["provider_dispatch_occurred"], False)
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.ws.updates(), [])
        return error.details

    async def approve_and_apply(self, created: dict) -> dict:
        await self.approve(created)
        return await self.apply(created)


class PolicyTests(PlanningTestCase):
    def policy(self, created: dict) -> tuple[str, str, list[str], list[str]]:
        decision = created["policy_decision"]
        basis = created["dry_run_results"]["entity_metadata"]["policy_basis"]
        return (
            decision["policy_class"],
            decision["physical_consequence"],
            basis["basis"],
            decision["required_acknowledgements"],
        )

    async def test_standard_name_icon_and_category_changes(self):
        self.add_entity("light.porch")
        created = await self.create(
            entity_id="light.porch", name="Front porch", icon="mdi:lamp",
            categories={"automation": "c1"},
        )
        self.assertEqual(
            self.policy(created),
            ("standard_admin", "none", [], ["plan_approval"]),
        )
        # Disclosure accompanies the standard name change (D4).
        disclosure = created["dry_run_results"]["entity_metadata"]["consumer_disclosure"]
        self.assertEqual(disclosure["assist_coverage"], "unavailable")
        self.assertIn("assist_note", disclosure)
        result = await self.approve_and_apply(created)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(
            self.ws.updates(),
            [{
                "type": "config/entity_registry/update",
                "entity_id": "light.porch",
                "name": "Front porch",
                "icon": "mdi:lamp",
                "categories": {"automation": "c1"},
            }],
        )

    async def test_e1_actuator_label_change_is_elevated_never_prohibited(self):
        self.add_entity("lock.front_door")
        created = await self.create(entity_id="lock.front_door", add_labels=["night"])
        self.assertEqual(
            self.policy(created),
            (
                "elevated_admin",
                "safety_critical",
                ["e1_actuator_membership_change"],
                ["plan_approval", "elevated_risk_acknowledgement"],
            ),
        )
        self.assertEqual(created["status"], "awaiting_approval")
        self.assertEqual(self.snapshot_reads, [False])
        # Useful authorized success: the elevated plan applies once approved.
        result = await self.approve_and_apply(created)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.ws.entries["lock.front_door"]["labels"], ["night"])
        self.assertEqual(self.snapshot_reads, [False, True])

    async def test_e1_area_change_on_a_non_safety_actuator_is_indirect(self):
        self.add_entity("light.porch")
        created = await self.create(entity_id="light.porch", area_id="porch", name="Porch light")
        self.assertEqual(
            self.policy(created)[:3],
            ("elevated_admin", "indirect", ["e1_actuator_membership_change"]),
        )

    async def test_label_change_on_a_sensor_is_standard_with_coverage_gaps(self):
        self.add_entity("sensor.porch_temperature")
        created = await self.create(entity_id="sensor.porch_temperature", add_labels=["outdoor"])
        self.assertEqual(self.policy(created)[:3], ("standard_admin", "none", []))
        disclosure = created["dry_run_results"]["entity_metadata"]["consumer_disclosure"]
        self.assertIs(disclosure["membership_changed"], True)
        self.assertIs(disclosure["coverage_complete"], False)
        self.assertIn(
            "trigger_and_condition_label_and_area_selectors_not_indexed",
            disclosure["coverage_gaps"],
        )

    async def test_e2_indexed_physical_consumer_elevates_a_sensor_label_change(self):
        self.add_entity("sensor.porch_temperature")
        self.snapshot = dependency_snapshot(profiles=(
            action_profile("unlock_at_night", target="label_id:night", consequence="safety_critical"),
        ))
        created = await self.create(entity_id="sensor.porch_temperature", add_labels=["night"])
        self.assertEqual(
            self.policy(created)[:3],
            ("elevated_admin", "safety_critical", ["e2_indexed_physical_consumer"]),
        )
        disclosure = created["dry_run_results"]["entity_metadata"]["consumer_disclosure"]
        self.assertEqual(disclosure["consumer_count"], 1)
        self.assertEqual(disclosure["consumers"][0]["source_id"], "unlock_at_night")
        self.assertEqual(disclosure["consumers"][0]["reference"], "label:night")

    async def test_d4_alias_changes_on_safety_entities_are_elevated(self):
        cases = {
            "lock.front_door": ({}, "safety_critical"),
            "alarm_control_panel.house": ({}, "safety_critical"),
            "cover.garage_door": ({"original_device_class": "garage"}, "indirect"),
            "cover.side_gate": ({"device_class": "gate"}, "indirect"),
            "cover.patio_door": ({"original_device_class": "door"}, "indirect"),
        }
        for entity_id, (changes, consequence) in cases.items():
            with self.subTest(entity_id=entity_id):
                self.add_entity(entity_id, **changes)
                created = await self.create(entity_id=entity_id, aliases=["Front"])
                self.assertEqual(
                    self.policy(created),
                    (
                        "elevated_admin",
                        consequence,
                        ["d4_safety_alias_change"],
                        ["plan_approval", "elevated_risk_acknowledgement"],
                    ),
                )

    async def test_alias_and_name_changes_elsewhere_are_standard(self):
        cases = {
            "cover.living_room_blind": {"original_device_class": "blind"},
            "light.porch": {},
            "lock.front_door": {},  # a name change, not an alias change
        }
        for entity_id, changes in cases.items():
            with self.subTest(entity_id=entity_id):
                self.add_entity(entity_id, **changes)
                field = {"name": "Renamed"} if entity_id.startswith("lock.") else {"aliases": ["Front"]}
                created = await self.create(entity_id=entity_id, **field)
                self.assertEqual(self.policy(created)[:3], ("standard_admin", "none", []))


class NoOpAndPartialTests(PlanningTestCase):
    async def test_already_current_request_is_a_no_change_result_without_a_plan(self):
        self.add_entity("light.porch", name="Porch", labels=["night"])
        result = await self.create(entity_id="light.porch", name="Porch", add_labels=["night"])
        self.assertEqual(result["status"], "no_change")
        self.assertEqual(result["outcome"], "already_in_desired_state")
        self.assertIs(result["plan_created"], False)
        self.assertIs(result["verified"], True)
        self.assertEqual(sorted(result["already_satisfied"]), ["add_labels:night", "name"])
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.ws.updates(), [])

    async def test_v4_hidden_true_on_integration_hidden_entity_is_refused_no_op(self):
        self.add_entity("light.porch", hidden_by="integration")
        result = await self.create(entity_id="light.porch", hidden=True)
        self.assertEqual(result["status"], "no_change")
        self.assertEqual(result["outcome"], "refused_no_op")
        self.assertIs(result["plan_created"], False)
        self.assertIs(result["verified"], False)
        self.assertEqual(result["reason_code"], "entity_metadata_already_hidden_by_integration")
        self.assertEqual(result["reason"], "already hidden by the integration (hidden_by=integration)")
        self.assertEqual(result["already_satisfied"], [])
        self.assertEqual(self.repository.list(), [])

    async def test_v4_partial_request_proceeds_without_hidden(self):
        self.add_entity("light.porch", hidden_by="integration")
        created = await self.create(entity_id="light.porch", hidden=True, icon="mdi:lamp")
        self.assertEqual(created["status"], "awaiting_approval")
        projection = created["dry_run_results"]["entity_metadata"]
        self.assertEqual(set(projection["fields"]), {"icon"})
        self.assertEqual(
            [item["reason_code"] for item in projection["refused_no_op"]],
            ["entity_metadata_already_hidden_by_integration"],
        )
        result = await self.approve_and_apply(created)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(
            self.ws.updates(),
            [{"type": "config/entity_registry/update", "entity_id": "light.porch", "icon": "mdi:lamp"}],
        )
        self.assertEqual(self.ws.entries["light.porch"]["hidden_by"], "integration")

    async def test_hidden_false_on_integration_hidden_entity_is_refused(self):
        self.add_entity("light.porch", hidden_by="integration")
        await self.refused(
            ErrorCode.INVALID_REQUEST, "entity_metadata_integration_hidden",
            entity_id="light.porch", hidden=False,
        )


class RefusalTests(PlanningTestCase):
    async def test_every_rejected_field_is_refused_before_any_read(self):
        self.add_entity("light.porch")
        for field, value in (
            ("new_entity_id", "light.other"),
            ("disabled_by", "user"),
            ("enabled", False),
            ("expose_to", ["conversation"]),
            ("should_expose", True),
            ("options", {}),
            ("options_domain", "conversation"),
            ("device_class", "door"),
            ("device_id", "abc"),
            ("entity_ids", ["light.porch"]),
            ("targets", ["light.porch"]),
            ("create_labels", [{"name": "Night"}]),
            ("unknown_key", 1),
        ):
            with self.subTest(field=field):
                self.ws.commands.clear()
                details = await self.refused(
                    ErrorCode.INVALID_REQUEST, "entity_metadata_field_not_supported",
                    entity_id="light.porch", name="x", **{field: value},
                )
                self.assertEqual(details["fields"], [field])
                self.assertEqual(self.ws.commands, [])
                self.assertEqual(self.rest.requests, [])

    async def test_missing_entity_and_non_registry_entity(self):
        await self.refused(
            ErrorCode.ENTITY_NOT_FOUND, "entity_not_found",
            entity_id="light.missing", name="x",
        )
        # A state without a registry entry: present, but not registry managed.
        original = self.rest.request

        async def state_exists(method, path, **kwargs):
            if path == "/states/light.yaml_only":
                self.rest.requests.append((method, path))
                return {"entity_id": "light.yaml_only", "state": "on", "attributes": {}}
            return await original(method, path, **kwargs)

        self.rest.request = state_exists
        await self.refused(
            ErrorCode.ENTITY_NOT_FOUND, "entity_not_registry_managed",
            entity_id="light.yaml_only", name="x",
        )

    async def test_unknown_references_are_refused(self):
        self.add_entity("light.porch")
        for arguments, reference in (
            ({"add_labels": ["missing"]}, "label:missing"),
            ({"area_id": "attic", "name": "x"}, "area:attic"),
            ({"categories": {"automation": "c9"}}, "category:automation:c9"),
        ):
            with self.subTest(reference=reference):
                details = await self.refused(
                    ErrorCode.INVALID_REQUEST, "entity_metadata_reference_not_found",
                    entity_id="light.porch", **arguments,
                )
                self.assertEqual(details["references"], [reference])

    async def test_area_change_on_a_nameless_device_entity_requires_its_own_name(self):
        self.add_entity("sensor.porch", device_id="dev1", original_name=None)
        await self.refused(
            ErrorCode.INVALID_REQUEST, "entity_metadata_area_requires_own_name",
            entity_id="sensor.porch", area_id="porch",
        )
        created = await self.create(entity_id="sensor.porch", area_id="porch", name="Porch sensor")
        self.assertEqual(created["status"], "awaiting_approval")

    async def test_missing_snapshot_for_a_membership_change_is_refused(self):
        self.add_entity("sensor.porch")
        # Regression: a reader that answers with no snapshot is the same
        # bounded refusal as a reader that fails, never an unhandled error.
        self.snapshot = None
        await self.refused(
            ErrorCode.HA_UNAVAILABLE, "dependency_snapshot_unavailable",
            entity_id="sensor.porch", add_labels=["night"],
        )

        async def failing_reader(*, fenced: bool):
            raise EntityMetadataEvidenceUnavailable("dependency_snapshot_unavailable")

        self.service.entity_metadata_snapshot_reader = failing_reader
        await self.refused(
            ErrorCode.HA_UNAVAILABLE, "dependency_snapshot_unavailable",
            entity_id="sensor.porch", add_labels=["night"],
        )
        # A change without membership needs no snapshot.
        created = await self.create(entity_id="sensor.porch", name="Porch")
        self.assertEqual(created["status"], "awaiting_approval")

    async def test_label_overlap_and_empty_requests_are_refused(self):
        self.add_entity("light.porch")
        await self.refused(
            ErrorCode.INVALID_REQUEST, "entity_metadata_label_overlap",
            entity_id="light.porch", add_labels=["night"], remove_labels=["night"],
        )
        await self.refused(
            ErrorCode.INVALID_REQUEST, "entity_metadata_no_fields",
            entity_id="light.porch",
        )

    async def test_provider_unavailable_is_refused(self):
        self.service.entity_metadata_gateway = None
        await self.refused(
            ErrorCode.HA_UNAVAILABLE, "entity_metadata_provider_unavailable",
            entity_id="light.porch", name="x",
        )
        self.assertEqual(self.ws.commands, [])


if __name__ == "__main__":
    unittest.main()
