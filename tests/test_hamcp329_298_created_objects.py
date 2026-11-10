"""HAMCP-329/298 governed scene create and created-object rollback.

Every path runs through the shipped service, the F3 runtime and the closed
scene-create and created-object delete gateways against the synthetic
offline Core store in ``tests/hamcp329_298_fixtures.py``.  The contract is
``docs/HAMCP_329_298_SCENE_CREATE_AND_CREATE_ROLLBACK_DESIGN.md`` §3-§8.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance import (  # noqa: E402
    created_object_identity as identity_model,
)

from tests.hamcp329_298_fixtures import (  # noqa: E402
    CreatedObjectServiceTestCase,
    EVENING,
    EVENING_ENTITY,
    SCENE_PREFIX,
    boolean_config,
    number_config,
)


class SceneAcceptanceTests(CreatedObjectServiceTestCase):
    async def test_scene_create_is_verified_and_captured(self):
        created = await self.created_scene()
        self.assertEqual(self.config_rest.scene_posts[0][0], EVENING)
        (value,) = self.identity_values(created["plan_id"])
        self.assertEqual(value["outcome"], identity_model.CAPTURED, value)
        identity = value["identity"]
        self.assertEqual(identity["entity_id"], EVENING_ENTITY)
        self.assertEqual(
            identity["delete_argument"],
            {"method": "DELETE", "path": f"{SCENE_PREFIX}{EVENING}"},
        )

    async def test_scene_create_rollback_deletes_exactly_once(self):
        created = await self.created_scene()
        rollback = await self.service.rollback_change(created["plan_id"])
        result = await self.apply_rollback(rollback)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            self.config_rest.scene_deletes(), [("DELETE", f"{SCENE_PREFIX}{EVENING}")]
        )
        self.assertNotIn(EVENING, self.config_rest.scenes)
        self.assertNotIn(EVENING_ENTITY, self.ws.entries)


class HelperAcceptanceTests(CreatedObjectServiceTestCase):
    async def test_helper_create_rollback_uses_the_exact_object_id(self):
        for domain, config, object_id in (
            ("input_boolean", boolean_config(), "hvac_override"),
            ("input_number", number_config(), "hvac_target"),
        ):
            with self.subTest(domain=domain):
                created = await self.created_helper(domain, config)
                (value,) = self.identity_values(created["plan_id"])
                self.assertEqual(value["outcome"], identity_model.CAPTURED, value)
                self.assertEqual(
                    value["identity"]["delete_argument"],
                    {"type": f"{domain}/delete", f"{domain}_id": object_id},
                )
                rollback = await self.service.rollback_change(created["plan_id"])
                result = await self.apply_rollback(rollback)
                self.assertEqual(result["task_state"], "succeeded_verified", result)
                self.assertEqual(
                    [c for c in self.ws.helper_commands("delete") if c["type"].startswith(domain)],
                    [{"type": f"{domain}/delete", f"{domain}_id": object_id}],
                )
                self.assertEqual(self.ws.helpers[domain], {})
                self.assertNotIn(f"{domain}.{object_id}", self.ws.entries)


if __name__ == "__main__":
    unittest.main()
