"""Beta 32 staged-release and notification-scope boundary."""

from pathlib import Path
import re
import sys
import unittest

from awesomeversion import AwesomeVersion


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.capabilities import BETA_NATIVE_CAPABILITIES  # noqa: E402


PRE_PROMOTION_VERSION = "2.2.0-beta.31"
BETA32_VERSION = "2.2.0-beta.32"


class Beta32ReleaseBoundaryTests(unittest.TestCase):
    def authoritative_versions(self) -> set[str]:
        patterns = (
            (
                BETA_DIR / "config.yaml",
                r'(?m)^version: "([^"]+)"$',
            ),
            (
                BETA_DIR / "ha_mcp_engineering" / "version.py",
                r'(?m)^SERVER_VERSION = "([^"]+)"$',
            ),
            (
                ROOT / "scripts" / "validate_addon_metadata.py",
                r'(?m)^BETA_VERSION = "([^"]+)"$',
            ),
        )
        versions = set()
        for path, pattern in patterns:
            matches = re.findall(pattern, path.read_text(encoding="utf-8"))
            self.assertEqual(len(matches), 1, str(path))
            versions.add(matches[0])
        return versions

    def require_release_phase(self, expected_version: str) -> set[str]:
        versions = self.authoritative_versions()
        self.assertEqual(len(versions), 1)
        actual_version = next(iter(versions))
        if AwesomeVersion(actual_version) > AwesomeVersion(BETA32_VERSION):
            self.skipTest(
                "Beta 32 phase assertions do not apply after a later release"
            )
        self.assertIn(actual_version, (PRE_PROMOTION_VERSION, BETA32_VERSION))
        if actual_version != expected_version:
            self.skipTest(
                f"{expected_version} assertions do not apply to "
                f"release phase {actual_version}"
            )
        return versions

    def assert_beta32_documents_resolve_exactly(self) -> None:
        release = ROOT / "docs" / "V2_2_0_BETA32_RELEASE_NOTES.md"
        acceptance = ROOT / "docs" / "V2_2_0_BETA32_ACCEPTANCE.md"
        self.assertTrue(release.is_file())
        self.assertTrue(acceptance.is_file())
        self.assertIn("clickAction", release.read_text(encoding="utf-8"))
        self.assertIn(
            "Fresh notification canary",
            acceptance.read_text(encoding="utf-8"),
        )

    def test_beta32_is_staged_without_changing_published_versions(self):
        self.assertEqual(
            self.require_release_phase(PRE_PROMOTION_VERSION),
            {PRE_PROMOTION_VERSION},
        )
        marker = ROOT / ".release" / "next-version"
        self.assertEqual(marker.read_text().strip(), BETA32_VERSION)
        self.assertLess(
            AwesomeVersion(PRE_PROMOTION_VERSION),
            AwesomeVersion(BETA32_VERSION),
        )
        self.assertIn(
            'version: "1.1.2"',
            (ROOT / "hass_mcp_admin" / "config.yaml").read_text(),
        )
        self.assert_beta32_documents_resolve_exactly()

    def test_beta32_generated_release_state_is_exact(self):
        marker = ROOT / ".release" / "next-version"
        if marker.exists() and AwesomeVersion(
            marker.read_text().strip()
        ) > AwesomeVersion(BETA32_VERSION):
            self.skipTest(
                "Beta 32 generated-state assertions do not apply while a "
                "later release is staged"
            )
        self.assertEqual(
            self.require_release_phase(BETA32_VERSION),
            {BETA32_VERSION},
        )
        self.assertFalse(marker.exists())
        self.assertIn(
            'version: "1.1.2"',
            (ROOT / "hass_mcp_admin" / "config.yaml").read_text(),
        )
        self.assert_beta32_documents_resolve_exactly()

    def test_scope_adds_no_tool_or_provider_fallback(self):
        self.assertEqual(len(BETA_NATIVE_CAPABILITIES), 25)
        source = (
            BETA_DIR
            / "ha_mcp_engineering"
            / "governance"
            / "approval_notifications.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"clickAction": review_url', source)
        self.assertIn('"fallback": "none"', source)
        self.assertNotIn("authenticationRequired", source)
        self.assertNotIn("call_service", source)


if __name__ == "__main__":
    unittest.main()
