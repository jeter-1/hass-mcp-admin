"""Beta 31 exact-image self-identity acceptance boundary."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"


class Beta31ExactImageIdentityTests(unittest.TestCase):
    def test_baked_runtime_exercises_large_self_info_notification(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        exact_acceptance = (
            ROOT / "scripts" / "exact_image_read_gateway_acceptance.py"
        ).read_text()
        fixture = (
            ROOT / "scripts" / "fake_ha_read_gateway_contract_server.py"
        ).read_text()
        self.assertIn("approval_notification_service", workflow)
        self.assertIn("SUPERVISOR_TOKEN", workflow)
        self.assertIn("inspect_approval_notification", exact_acceptance)
        self.assertIn("supervisor_self_info_payload_bytes", exact_acceptance)
        self.assertIn("SELF_ADDON_INFO_BODY", fixture)
        self.assertIn("approval_notification_calls", fixture)

    def test_one_resolver_instance_is_shared_by_both_consumers(self):
        runtime = (
            BETA_DIR
            / "ha_mcp_engineering"
            / "governance"
            / "runtime.py"
        ).read_text()
        self.assertEqual(
            runtime.count(
                "SupervisorSelfAddonIdentityResolver.from_settings(settings)"
            ),
            1,
        )
        self.assertEqual(runtime.count("self_addon_identity.resolve"), 2)


if __name__ == "__main__":
    unittest.main()
