"""Beta 36 exact-image fragmented self-identity boundary."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
import runpy
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "scripts" / "fake_ha_read_gateway_contract_server.py"
ACCEPTANCE_PATH = ROOT / "scripts" / "exact_image_read_gateway_acceptance.py"


class Beta36ExactImageIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = runpy.run_path(str(FIXTURE_PATH))

    def test_fixture_matches_sanitized_fragmented_live_contract(self):
        body = self.fixture["SELF_ADDON_INFO_BODY"]
        payload = json.loads(body)
        data = payload["data"]

        self.assertEqual(
            len(body), self.fixture["LIVE_SHAPED_SELF_ADDON_INFO_BYTES"]
        )
        self.assertEqual(len(body), 33_732)
        self.assertIsInstance(data["schema"], list)
        self.assertEqual(
            data["slug"], "df26dea6_hass_mcp_engineering_beta"
        )
        self.assertEqual(data["version"], "2.2.0-beta.36")
        self.assertIn("synthetic", json.dumps(data["options"]))

    def test_self_info_route_forces_more_than_one_transport_read(self):
        body = self.fixture["SELF_ADDON_INFO_BODY"]
        fragment_bytes = self.fixture["SELF_ADDON_INFO_FRAGMENT_BYTES"]
        handler_source = inspect.getsource(
            self.fixture["supervisor_self_info"]
        )

        self.assertEqual(fragment_bytes, 1024)
        self.assertGreater(len(body), fragment_bytes)
        self.assertIn("web.StreamResponse", handler_source)
        self.assertIn("await asyncio.sleep(0.05)", handler_source)

    def test_exact_image_acceptance_requires_fragmented_success(self):
        acceptance = ACCEPTANCE_PATH.read_text(encoding="utf-8")

        self.assertIn("supervisor_self_info_fragment_bytes", acceptance)
        self.assertIn("supervisor_self_info_fragment_count", acceptance)
        self.assertIn("fragmented_response_fully_consumed", acceptance)
        self.assertIn(
            '== "verified_supervisor_self_info"', acceptance
        )
        self.assertIn('notification_health.get("failed") == 0', acceptance)
        self.assertIn('notification_health.get("fallback_count") == 0', acceptance)


if __name__ == "__main__":
    unittest.main()
