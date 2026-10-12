"""HAMCP-069A-1 boundary tests: policy prohibition, route closure, strict tool.

The entity metadata update is reachable only through
``create_entity_metadata_plan``.  A plan that does not carry exactly the
planner's single classified operation is prohibited, the public configuration
route refuses the resource type, and the published tool is closed.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalPolicyClass,
)
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    evaluate_change_policy,
)
from ha_mcp_engineering.governance.runtime import GOVERNANCE  # noqa: E402
from ha_mcp_engineering.tools.governance import (  # noqa: E402
    ENTITY_METADATA_INPUT_SCHEMA,
    registered_entity_metadata_tool,
)

from tests.hamcp069a_entity_fixtures import (  # noqa: E402
    EntityMetadataServiceTestCase,
)


INCOMPLETE = "entity_metadata_execution_contract_incomplete"


class PolicyProhibitionTests(EntityMetadataServiceTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.add_entity("light.porch")
        self.add_entity("light.hall")

    async def planned(self, entity_id: str = "light.porch"):
        created = await self.create(entity_id=entity_id, name="Renamed")
        plan = self.repository.get(created["plan_id"])
        self.assertEqual(
            plan.policy_decision.policy_class, ApprovalPolicyClass.STANDARD_ADMIN
        )
        return plan

    def assert_prohibited(self, plan) -> None:
        decision = evaluate_change_policy(plan)
        self.assertEqual(decision.policy_class, ApprovalPolicyClass.PROHIBITED)
        self.assertIn(INCOMPLETE, decision.reason_codes)

    async def test_mixed_multi_operation_plan_is_prohibited(self):
        plan = await self.planned()
        other = (await self.planned("light.hall")).operations[0]
        mixed = deepcopy(plan)
        mixed.operations = [plan.operations[0], replace(other, operation_id="second")]
        self.assert_prohibited(mixed)

    async def test_missing_or_duplicated_policy_marker_is_prohibited(self):
        plan = await self.planned()
        operation = plan.operations[0]
        evidence = list(operation.risk.evidence)
        for changed in ([], evidence + evidence):
            incomplete = deepcopy(plan)
            incomplete.operations = [
                replace(operation, risk=replace(operation.risk, evidence=tuple(changed)))
            ]
            self.assert_prohibited(incomplete)

    async def test_inconsistent_marker_is_prohibited(self):
        plan = await self.planned()
        operation = plan.operations[0]
        for change in (
            {"policy": "elevated"},
            {"physical_consequence": "not_a_consequence"},
            {"basis": "e1_label_on_actuator"},
        ):
            evidence = tuple(
                {**item, **change} if isinstance(item, dict) and "trigger" in item else item
                for item in operation.risk.evidence
            )
            tampered = deepcopy(plan)
            tampered.operations = [
                replace(operation, risk=replace(operation.risk, evidence=evidence))
            ]
            self.assert_prohibited(tampered)

    async def test_non_update_action_is_prohibited(self):
        plan = await self.planned()
        tampered = deepcopy(plan)
        tampered.operations = [replace(plan.operations[0], action="create")]
        self.assert_prohibited(tampered)

    async def test_public_configuration_route_refuses_the_resource_type(self):
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_configuration_plan(
                title="Rename",
                description="Generic route",
                operations=[
                    {
                        "operation_id": "entity_metadata",
                        "resource_type": "entity_registry_metadata",
                        "action": "update",
                        "target_id": "light.porch",
                        "proposed_config": {"name": "Renamed"},
                    }
                ],
            )
        self.assertEqual(raised.exception.code, ErrorCode.CONFIGURATION_VALIDATION_FAILED)
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.ws.commands, [])


class StrictToolTests(EntityMetadataServiceTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.add_entity("light.porch", name="Old porch", icon="mdi:lamp")
        self.tool = registered_entity_metadata_tool()

    async def run_tool(self, arguments: dict) -> dict:
        with patch.object(GOVERNANCE, "service", self.service):
            rendered = await self.tool.run(arguments)
        return json.loads(rendered)

    def test_published_schema_is_closed(self):
        schema = self.tool.parameters
        self.assertIs(schema, ENTITY_METADATA_INPUT_SCHEMA)
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(schema["required"], ["entity_id"])
        self.assertEqual(
            set(schema["properties"]),
            {
                "entity_id", "name", "icon", "aliases", "area_id", "hidden",
                "add_labels", "remove_labels", "categories", "expiration_minutes",
            },
        )
        for rejected in ("create_labels", "new_entity_id", "disabled_by", "expose_to"):
            self.assertNotIn(rejected, schema["properties"])
        # A nullable field has no default, so an absent field never becomes null.
        for name in ("name", "icon", "area_id"):
            self.assertNotIn("default", schema["properties"][name])

    async def test_unknown_key_is_refused_before_any_read(self):
        for key in ("create_labels", "new_entity_id", "unexpected"):
            self.ws.commands.clear()
            result = await self.run_tool({"entity_id": "light.porch", key: ["x"]})
            self.assertIs(result["success"], False)
            self.assertIn("entity_metadata_field_not_supported", json.dumps(result))
            self.assertEqual(self.ws.commands, [])
            self.assertEqual(self.rest.requests, [])
        self.assertEqual(self.repository.list(), [])

    async def test_explicit_null_is_preserved_and_absent_fields_stay_absent(self):
        result = await self.run_tool({"entity_id": "light.porch", "name": None})
        self.assertIs(result["success"], True)
        self.assertEqual(result["data"]["status"], "awaiting_approval")
        (plan,) = self.repository.list()
        operation = plan.operations[0]
        self.assertIsNone(operation.proposed_config["name"])
        # icon was not supplied, so it is unchanged rather than cleared.
        self.assertEqual(operation.proposed_config["icon"], "mdi:lamp")
        await self.approve({"plan_id": plan.plan_id, "plan_hash": self.service.plan_hash(plan)})
        await self.service.apply(plan.plan_id, self.service.plan_hash(plan))
        self.assertEqual(
            self.ws.updates(),
            [{"type": "config/entity_registry/update", "entity_id": "light.porch", "name": None}],
        )


if __name__ == "__main__":
    unittest.main()
