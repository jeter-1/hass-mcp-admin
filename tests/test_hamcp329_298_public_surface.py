"""HAMCP-329/298 design §4: the public surface of the created-object profiles.

No tool is added.  ``create_scene_configuration_plan`` gains one optional input
(``action``); every existing update request validates and plans exactly as
before.  The public delete tool and ``rollback_change`` keep their input
schemas, so no public request shape names a scene or helper delete.  Health and
the catalog report the two dormant profiles from the shared admission snapshot,
each independently of the scene update capability.

Every runtime here is a local stub or the synthetic HAMCP-071 store.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from ha_mcp_engineering.capabilities import (  # noqa: E402
    CORE_ADMISSION_NOT_EVALUATED,
    build_capability_catalog,
    capability_for_tool,
)
from ha_mcp_engineering.errors import ErrorCode  # noqa: E402
from ha_mcp_engineering.governance import created_object_delete  # noqa: E402
from ha_mcp_engineering.governance import scene_configuration as contract  # noqa: E402
from ha_mcp_engineering.governance import scene_configuration_planning as planning  # noqa: E402
from ha_mcp_engineering.governance.service import ChangeGovernanceService  # noqa: E402
from ha_mcp_engineering.governance.storage import ChangePlanRepository  # noqa: E402
from ha_mcp_engineering.health import HEALTH  # noqa: E402
from ha_mcp_engineering.tools import compatibility  # noqa: E402
from ha_mcp_engineering.tools import governance as governance_tools  # noqa: E402

from tests.hamcp071_fixtures import LAMP, LOUNGE, LOUNGE_ENTITY, STRIP, SceneServiceTestCase  # noqa: E402
from tests.hamcp298_descriptor_changes import HAMCP298_NEW  # noqa: E402

SCENE_TOOL = "create_scene_configuration_plan"
DELETE_TOOL = "create_configuration_delete_plan"
SCENE = "scene_create_delete_provider"
HELPER = "created_helper_delete_provider"
UPDATE = "scene_configuration_provider"
PENDING = created_object_delete.PENDING_ADMISSION_REASON
# sha256 of the canonical JSON of each published input schema at the
# release/2.6.0 base (the scene schema before ``action`` was added).
# Pinned, not derived: a change to either schema must be made here on purpose.
SCENE_SCHEMA_WITHOUT_ACTION_SHA256 = (
    "2cb9988965d0e010ed742d8883cee94c62618588675e96b039e07b3111ee2f72"
)
DELETE_SCHEMA_SHA256 = (
    "a4ebc119354e40524019b729c1a9fa7cd2951ac9e7a254ea63cfca86c7608454"
)


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class SceneToolSchemaTests(unittest.TestCase):
    def test_action_is_the_only_added_input(self):
        schema = deepcopy(governance_tools.SCENE_CONFIGURATION_INPUT_SCHEMA)
        action = schema["properties"].pop("action")
        self.assertEqual(digest(schema), SCENE_SCHEMA_WITHOUT_ACTION_SHA256)
        self.assertEqual(
            action, {"type": "string", "enum": ["update", "create"], "default": "update"}
        )
        self.assertEqual(tuple(action["enum"]), planning.ACTIONS)
        # Optional: an existing request needs no new field.
        self.assertEqual(schema["required"], ["config_id"])
        self.assertIs(schema["additionalProperties"], False)

    def test_the_served_tool_publishes_exactly_that_schema(self):
        tool = governance_tools.registered_scene_configuration_tool()
        self.assertIs(tool.parameters, governance_tools.SCENE_CONFIGURATION_INPUT_SCHEMA)
        signature = inspect.signature(governance_tools.create_scene_configuration_plan)
        self.assertEqual(
            set(signature.parameters),
            set(governance_tools.SCENE_CONFIGURATION_INPUT_SCHEMA["properties"]),
        )
        self.assertEqual(signature.parameters["action"].default, "update")
        self.assertEqual(
            set(planning.ALLOWED_INPUT_FIELDS),
            set(governance_tools.SCENE_CONFIGURATION_INPUT_SCHEMA["properties"]),
        )

    def test_the_scene_schema_names_no_delete(self):
        text = json.dumps(governance_tools.SCENE_CONFIGURATION_INPUT_SCHEMA)
        self.assertNotIn("delete", text)
        self.assertNotIn(created_object_delete.ACTION, planning.ACTIONS)


class UnchangedSchemaTests(unittest.TestCase):
    """No public request shape names a scene or helper delete."""

    def test_the_public_delete_schema_is_unchanged(self):
        schema = governance_tools.CONFIGURATION_DELETE_INPUT_SCHEMA
        self.assertEqual(digest(schema), DELETE_SCHEMA_SHA256)
        item = schema["properties"]["deletions"]["items"]
        self.assertEqual(item["properties"]["resource_type"]["enum"], ["automation", "script"])
        self.assertIs(item["additionalProperties"], False)
        self.assertIs(
            governance_tools.registered_configuration_delete_tool().parameters, schema
        )

    def test_the_public_delete_catalog_entry_is_unchanged(self):
        entry = capability_for_tool(DELETE_TOOL)
        self.assertEqual(entry["resource_types"], ["automation", "script"])
        self.assertEqual(entry["core_capabilities"], ["core.configuration_delete_operation"])

    def test_rollback_change_takes_only_a_plan_and_its_hash(self):
        signature = inspect.signature(governance_tools.rollback_change)
        self.assertEqual(list(signature.parameters), ["plan_id", "expected_plan_hash"])

    def test_no_tool_is_added_for_scene_or_delete(self):
        names = {tool.__name__ for tool in governance_tools.GOVERNANCE_TOOLS}
        self.assertEqual({name for name in names if "delete" in name}, {DELETE_TOOL})
        self.assertEqual({name for name in names if "scene" in name}, {SCENE_TOOL})
        self.assertEqual(
            {name for name in names if "helper" in name},
            {"create_helper_state_plan", "create_helper_options_plan"},
        )


class DescriptorTests(unittest.TestCase):
    def test_rollback_change_describes_created_object_rollback(self):
        doc = governance_tools.rollback_change.__doc__
        self.assertEqual(doc.count(HAMCP298_NEW), 1)
        flat = " ".join(doc.split())
        self.assertIn("helper or scene create rolls back the same way once a signed registry admits its delete", flat)
        self.assertIn("one plan in strict reverse order", flat)
        self.assertIn("Operational rollback remains unavailable.", flat)
        self.assertNotIn("creates and operational rollback remain unavailable", flat)

    def test_the_scene_tool_describes_create_and_its_separate_admission(self):
        flat = " ".join(governance_tools.create_scene_configuration_plan.__doc__.split())
        self.assertIn('action "create" (default "update")', flat)
        self.assertIn("name and entities are required", flat)
        self.assertIn("entity_id and null values are refused", flat)
        self.assertIn("Creation is admitted separately from updates", flat)
        self.assertEqual(flat.count("pending_signed_registry_admission"), 2)
        self.assertIn(contract.VERIFICATION_SCOPE_STATEMENT, flat)


class ServedToolTests(unittest.IsolatedAsyncioTestCase):
    """The served tool hands the planner the caller's exact argument object."""

    async def run_tool(self, arguments):
        service = mock.Mock()
        service.sensitive_values = ()
        service.create_scene_configuration_plan = mock.AsyncMock(return_value={})
        with mock.patch.object(governance_tools.GOVERNANCE, "require", return_value=service):
            rendered = await governance_tools.registered_scene_configuration_tool().run(
                arguments
            )
        return service.create_scene_configuration_plan, json.loads(rendered)

    async def test_action_reaches_the_planner_unchanged(self):
        for arguments in (
            {"config_id": "1700000000777", "action": "create", "name": "Evening",
             "entities": {LAMP: {"state": "on"}}},
            {"config_id": LOUNGE, "action": "update", "name": "Requested"},
            {"config_id": LOUNGE, "name": "Requested"},
        ):
            with self.subTest(action=arguments.get("action")):
                planner, result = await self.run_tool(deepcopy(arguments))
                self.assertTrue(result["success"], result)
                # No default is materialized: an omitted action stays omitted.
                planner.assert_awaited_once_with(arguments)

    async def test_a_secret_shaped_action_is_refused_before_the_planner(self):
        token = "eyJhbGciOiJIUzI1NiJ9." + "a" * 24 + "." + "b" * 24
        planner, result = await self.run_tool({"config_id": LOUNGE, "action": token, "name": "x"})
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], ErrorCode.INVALID_REQUEST.value)
        self.assertEqual(
            result["details"], {"reason": governance_tools.IDENTIFIER_REFUSAL_REASON}
        )
        planner.assert_not_awaited()
        self.assertNotIn(token, json.dumps(result))


UPDATE_REQUESTS = (
    {"config_id": LOUNGE, "name": "Requested"},
    {"config_id": LOUNGE, "entity_id": LOUNGE_ENTITY, "icon": "mdi:sofa"},
    {"config_id": LOUNGE, "icon": None},
    {"config_id": LOUNGE, "entities": {LAMP: {"state": "off"}, STRIP: None}},
    {"config_id": LOUNGE, "name": "Requested", "icon": "mdi:sofa",
     "entities": {LAMP: {"state": "on", "brightness": 40}}, "expiration_minutes": 5},
)
REFUSED_UPDATE_REQUESTS = (
    {"config_id": LOUNGE},
    {"config_id": "not a config id", "name": "x"},
    {"config_id": LOUNGE, "entity_id": "light.not_a_scene", "name": "x"},
    {"config_id": LOUNGE, "name": ""},
    {"config_id": LOUNGE, "icon": "no-prefix"},
    {"config_id": LOUNGE, "entities": {}},
    {"config_id": LOUNGE, "entities": {"scene.other": {"state": "on"}}},
    {"config_id": LOUNGE, "name": "x", "expiration_minutes": 4},
    {"config_id": LOUNGE, "name": "x", "unknown": 1},
)


def refusal_of(arguments):
    try:
        planning.validate_request(arguments)
    except planning.Refusal as refusal:
        return refusal.reason_code, refusal.kind, refusal.details
    raise AssertionError(f"accepted: {arguments}")


class UpdateValidationRegressionTests(unittest.TestCase):
    """Design §4: under ``update`` every existing request validates identically."""

    def test_an_explicit_update_validates_as_the_omitted_action(self):
        for arguments in UPDATE_REQUESTS:
            with self.subTest(fields=sorted(arguments)):
                omitted = planning.validate_request(deepcopy(arguments))
                explicit = planning.validate_request({**deepcopy(arguments), "action": "update"})
                self.assertEqual(explicit, omitted)
                self.assertEqual(omitted.action, "update")
                self.assertIsNone(omitted.restore_config)

    def test_an_explicit_update_is_refused_as_the_omitted_action(self):
        for arguments in REFUSED_UPDATE_REQUESTS:
            with self.subTest(fields=sorted(arguments)):
                self.assertEqual(
                    refusal_of({**deepcopy(arguments), "action": "update"}),
                    refusal_of(deepcopy(arguments)),
                )

    def test_update_only_values_stay_refused_under_create(self):
        # ``entity_id`` and a null (removal) value belong to updates only.
        base = {"config_id": "1700000000777", "action": "create", "name": "Evening",
                "entities": {LAMP: {"state": "on"}}}
        for change, reason in (
            ({"entity_id": "scene.evening"}, planning.FIELD_NOT_SUPPORTED),
            ({"icon": None}, planning.INVALID_VALUE),
            ({"entities": {LAMP: None}}, planning.INVALID_VALUE),
        ):
            with self.subTest(change=sorted(change)):
                self.assertEqual(refusal_of({**base, **change})[0], reason)
        for missing in ("name", "entities"):
            with self.subTest(missing=missing):
                request = {key: value for key, value in base.items() if key != missing}
                code, _kind, details = refusal_of(request)
                self.assertEqual(code, "scene_configuration_create_fields_required")
                self.assertEqual(details["fields"], [missing])


# Everything of a plan result that does not derive from its random plan id.
PLAN_INVARIANTS = (
    "title", "description", "operation", "operations", "dry_run_results", "risk",
    "rollback", "proposed_config_hash", "current_state_fingerprint",
    "validation_results", "warnings", "contract_version", "next_required_operation",
)


class UpdatePlanRegressionTests(SceneServiceTestCase):
    """The plan of an explicit ``update`` is the plan of the omitted action."""

    async def test_an_explicit_update_plans_exactly_the_same_change(self):
        for arguments in UPDATE_REQUESTS:
            with self.subTest(fields=sorted(arguments)):
                omitted = await self.create(**deepcopy(arguments))
                explicit = await self.create(**deepcopy(arguments), action="update")
                self.assertNotEqual(omitted["plan_id"], explicit["plan_id"])
                for key in PLAN_INVARIANTS:
                    self.assertEqual(explicit[key], omitted[key], key)
                stored = self.service._load(explicit["plan_id"])
                self.assertEqual(
                    [item.action for item in stored.operations], ["update"]
                )
        self.assertEqual(self.writes(), [])

    async def test_an_explicit_update_applies_and_verifies_as_before(self):
        created = await self.approved(config_id=LOUNGE, name="Requested", action="update")
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(self.scene_rest.config(LOUNGE)["name"], "Requested")
        self.assert_never_sent()


class _Core:
    """Answers ``route_status`` from one local admitted set and generation."""

    def __init__(self, admitted=(), generation=5):
        self.admitted = set(admitted)
        self.generation = generation

    def route_status(self, capability_ids, **_):
        return {
            "available": bool(capability_ids)
            and all(item in self.admitted for item in capability_ids),
            "generation": self.generation,
        }

    def __getattr__(self, name):
        raise AssertionError(f"admission display used core_runtime.{name}")


class AdmissionSurfaceTests(unittest.TestCase):
    """Design §4: ``core_admission`` reports the two new capabilities."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.service = ChangeGovernanceService(
            ChangePlanRepository(Path(temp.name)), object()
        )

    def views(self, admitted=()):
        self.service.f3_runtime = SimpleNamespace(
            health=lambda: {}, core_runtime=_Core(admitted)
        )
        operational = self.service.health_summary()["operational_administration"]
        with mock.patch.object(HEALTH, "governance", SimpleNamespace(service=self.service)):
            result = json.loads(
                asyncio.run(compatibility.list_capabilities(detail_section="beta_native"))
            )
        self.assertTrue(result["success"], result)
        by_tool = {item["tool"]: item for item in result["data"]["beta_native"]}
        return operational, by_tool

    def statuses(self, admitted=()):
        operational, by_tool = self.views(admitted)
        scene_tool = by_tool[SCENE_TOOL]
        rollback = by_tool["rollback_change"]
        return {
            "health": {
                key: operational[key]["core_admission"] for key in (UPDATE, SCENE, HELPER)
            },
            "catalog": {
                UPDATE: scene_tool["core_admission"],
                SCENE: scene_tool["create"]["core_admission"],
                "scene_delete": rollback["created_scene_delete"]["core_admission"],
                HELPER: rollback["created_helper_delete"]["core_admission"],
            },
        }

    def test_each_profile_is_reported_on_its_own(self):
        update_pending = contract.PENDING_ADMISSION_REASON
        for admitted, expected in (
            ((), (update_pending, PENDING, PENDING)),
            ((contract.PROFILE,), ("admitted", PENDING, PENDING)),
            ((created_object_delete.SCENE_PROFILE,), (update_pending, "admitted", PENDING)),
            ((created_object_delete.HELPER_PROFILE,), (update_pending, PENDING, "admitted")),
            (
                (contract.PROFILE, created_object_delete.SCENE_PROFILE,
                 created_object_delete.HELPER_PROFILE),
                ("admitted", "admitted", "admitted"),
            ),
        ):
            with self.subTest(admitted=admitted):
                update, scene, helper = expected
                views = self.statuses(admitted)
                self.assertEqual(
                    views["health"], {UPDATE: update, SCENE: scene, HELPER: helper}
                )
                self.assertEqual(
                    views["catalog"],
                    {UPDATE: update, SCENE: scene, "scene_delete": scene, HELPER: helper},
                )

    def test_without_a_runtime_both_are_pending(self):
        self.service.f3_runtime = None
        snapshot = self.service.core_admission_snapshot()
        self.assertEqual(snapshot["status"], "no_admitted_generation")
        self.assertEqual(snapshot["providers"][SCENE], PENDING)
        self.assertEqual(snapshot["providers"][HELPER], PENDING)

    def test_the_new_groups_add_no_catalog_tool(self):
        self.service.f3_runtime = SimpleNamespace(
            health=lambda: {}, core_runtime=_Core(())
        )
        snapshot = self.service.core_admission_snapshot()
        self.assertNotIn("", snapshot["tools"])
        surfaces = {
            key: (tool, profiles, pending)
            for key, tool, profiles, pending in self.service.CORE_ADMISSION_SURFACES
        }
        self.assertEqual(
            surfaces[SCENE], ("", (created_object_delete.SCENE_PROFILE,), PENDING)
        )
        self.assertEqual(
            surfaces[HELPER], ("", (created_object_delete.HELPER_PROFILE,), PENDING)
        )
        # The scene update tool still projects only the update capability.
        self.assertEqual(surfaces[UPDATE][:2], (SCENE_TOOL, (contract.PROFILE,)))
        self.assertEqual(snapshot["tools"][SCENE_TOOL], contract.PENDING_ADMISSION_REASON)

    def test_health_states_the_profile_and_its_reachability(self):
        operational, _ = self.views()
        scene = operational[SCENE]
        self.assertEqual(scene["core_capability"], created_object_delete.SCENE_PROFILE)
        self.assertEqual(
            scene["provider_contract"], "compiled-core-scene-create-delete-operation-v1"
        )
        self.assertEqual(scene["create"], "create_scene_configuration_plan_action_create")
        self.assertEqual(scene["delete"], "created_object_rollback_only")
        helper = operational[HELPER]
        self.assertEqual(helper["core_capability"], created_object_delete.HELPER_PROFILE)
        self.assertEqual(
            helper["provider_contract"], "compiled-core-created-helper-delete-operation-v1"
        )
        self.assertEqual(helper["resource_types"], ["input_boolean", "input_number"])
        self.assertEqual(helper["delete"], "created_object_rollback_only")
        for block in (scene, helper):
            # No gateway is composed in this bare service.
            self.assertIs(block["configured"], False)
            self.assertEqual(block["operational_status"], "unavailable")
            self.assertEqual(
                (block["fallback"], block["fallback_count"], block["fallback_policy"]),
                ("none", 0, "none"),
            )


class StaticCatalogTests(unittest.TestCase):
    def test_the_scene_tool_describes_create_and_never_delete(self):
        entry = capability_for_tool(SCENE_TOOL)
        self.assertIs(entry["create_supported"], True)
        self.assertIs(entry["delete_supported"], False)
        # The tool-level capability stays the update capability.
        self.assertEqual(entry["core_capabilities"], [contract.PROFILE])
        create = entry["create"]
        self.assertEqual(create["action"], contract.CREATE_ACTION)
        self.assertEqual(create["core_capabilities"], [created_object_delete.SCENE_PROFILE])
        self.assertEqual(create["core_admission_requirement"], "signed_core_registry_admission")
        self.assertEqual(create["core_admission_provider"], SCENE)
        self.assertEqual(create["core_admission"], CORE_ADMISSION_NOT_EVALUATED)
        self.assertEqual(create["required_fields"], ["name", "entities"])
        self.assertEqual(create["refused_fields"], ["entity_id"])
        self.assertEqual(create["loaded_state"], "not_verified")

    def test_rollback_change_names_the_two_delete_capabilities(self):
        entry = capability_for_tool("rollback_change")
        for block, provider, profile, types in (
            ("created_scene_delete", SCENE, created_object_delete.SCENE_PROFILE,
             ["scene_configuration"]),
            ("created_helper_delete", HELPER, created_object_delete.HELPER_PROFILE,
             list(created_object_delete.HELPER_TYPES)),
        ):
            with self.subTest(block=block):
                value = entry[block]
                self.assertEqual(value["core_capabilities"], [profile])
                self.assertEqual(value["resource_types"], types)
                self.assertEqual(value["reachability"], "created_object_rollback_only")
                self.assertEqual(value["core_admission_provider"], provider)
                self.assertEqual(value["core_admission"], CORE_ADMISSION_NOT_EVALUATED)
                self.assertEqual(
                    value["core_admission_requirement"], "signed_core_registry_admission"
                )

    def test_the_overlay_never_mutates_the_static_blocks(self):
        snapshot = {
            "status": "current",
            "generation": 4,
            "providers": {SCENE: "admitted", HELPER: "admitted"},
            "tools": {},
        }
        current = build_capability_catalog(core_admission=snapshot)
        by_tool = {item["tool"]: item for item in current["beta_native"]}
        self.assertEqual(by_tool[SCENE_TOOL]["create"]["core_admission"], "admitted")
        self.assertEqual(
            by_tool["rollback_change"]["created_helper_delete"]["core_admission"], "admitted"
        )
        # Only the nested blocks moved; the tool-level status was not in the snapshot.
        self.assertEqual(by_tool[SCENE_TOOL]["core_admission"], CORE_ADMISSION_NOT_EVALUATED)
        plain = {item["tool"]: item for item in build_capability_catalog()["beta_native"]}
        for tool, block in (
            (SCENE_TOOL, "create"),
            ("rollback_change", "created_scene_delete"),
            ("rollback_change", "created_helper_delete"),
        ):
            self.assertEqual(plain[tool][block]["core_admission"], CORE_ADMISSION_NOT_EVALUATED)
            self.assertEqual(
                capability_for_tool(tool)[block]["core_admission"],
                CORE_ADMISSION_NOT_EVALUATED,
            )

    def test_a_snapshot_without_the_groups_leaves_them_not_evaluated(self):
        for snapshot in (
            {"status": "current", "generation": 4, "providers": {}, "tools": {}},
            {"status": "current", "generation": 4, "tools": {}},
        ):
            current = build_capability_catalog(core_admission=snapshot)
            by_tool = {item["tool"]: item for item in current["beta_native"]}
            self.assertEqual(
                by_tool[SCENE_TOOL]["create"]["core_admission"], CORE_ADMISSION_NOT_EVALUATED
            )


if __name__ == "__main__":
    unittest.main()
