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


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from tests.hamcp155_fixtures import (  # noqa: E402
    ConfigurationDeleteServiceTestCase,
    automation_config,
)


class EndToEndTests(ConfigurationDeleteServiceTestCase):
    async def test_delete_automation_verified_by_absence_readback(self):
        self.add("automation", "porch_light")
        pre_image = automation_config("porch_light")
        created = await self.create_one("automation", "porch_light")
        self.assertEqual(self.config_rest.writes(), [])
        await self.approve(created)
        result = await self.apply(created)
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            self.config_rest.deletes(),
            [("DELETE", "/config/automation/config/porch_light")],
        )
        self.assertNotIn("porch_light", self.config_rest.configs["automation"])
        self.assertNotIn("automation.porch_light", self.ws.entries)
        del pre_image


if __name__ == "__main__":
    unittest.main()
