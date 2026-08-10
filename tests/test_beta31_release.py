"""Beta 31 staged-release boundary."""

from pathlib import Path
import re
import unittest

from awesomeversion import AwesomeVersion


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
PRE_PROMOTION_VERSION = "2.2.0-beta.30"
BETA31_VERSION = "2.2.0-beta.31"


class Beta31ReleaseBoundaryTests(unittest.TestCase):
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
        if AwesomeVersion(actual_version) > AwesomeVersion(BETA31_VERSION):
            self.skipTest(
                "Beta 31 phase assertions do not apply after a later release"
            )
        self.assertIn(actual_version, (PRE_PROMOTION_VERSION, BETA31_VERSION))
        if actual_version != expected_version:
            self.skipTest(
                f"{expected_version} assertions do not apply to "
                f"release phase {actual_version}"
            )
        return versions

    def assert_beta31_documents_resolve_exactly(self) -> None:
        release = ROOT / "docs" / "V2_2_0_BETA31_RELEASE_NOTES.md"
        acceptance = ROOT / "docs" / "V2_2_0_BETA31_ACCEPTANCE.md"
        self.assertTrue(release.is_file())
        self.assertTrue(acceptance.is_file())
        self.assertIn("512 KiB", release.read_text())
        self.assertIn("Fresh notification canary", acceptance.read_text())

    def test_beta31_is_staged_without_changing_published_versions(self):
        self.assertEqual(
            self.require_release_phase(PRE_PROMOTION_VERSION),
            {PRE_PROMOTION_VERSION},
        )
        marker = ROOT / ".release" / "next-version"
        self.assertEqual(marker.read_text().strip(), BETA31_VERSION)
        self.assertLess(
            AwesomeVersion(PRE_PROMOTION_VERSION),
            AwesomeVersion(BETA31_VERSION),
        )
        self.assertIn(
            'version: "1.1.2"',
            (ROOT / "hass_mcp_admin" / "config.yaml").read_text(),
        )
        self.assert_beta31_documents_resolve_exactly()

    def test_beta31_generated_release_state_is_exact(self):
        self.assertEqual(
            self.require_release_phase(BETA31_VERSION),
            {BETA31_VERSION},
        )
        self.assertFalse((ROOT / ".release" / "next-version").exists())
        self.assertIn(
            'version: "1.1.2"',
            (ROOT / "hass_mcp_admin" / "config.yaml").read_text(),
        )
        self.assert_beta31_documents_resolve_exactly()

    def test_beta31_documents_resolve_exactly(self):
        self.assert_beta31_documents_resolve_exactly()


if __name__ == "__main__":
    unittest.main()
