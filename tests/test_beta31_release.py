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

    def test_beta31_is_staged_without_changing_published_versions(self):
        versions = self.authoritative_versions()
        self.assertEqual(versions, {PRE_PROMOTION_VERSION})
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

    def test_beta31_documents_resolve_exactly(self):
        release = ROOT / "docs" / "V2_2_0_BETA31_RELEASE_NOTES.md"
        acceptance = ROOT / "docs" / "V2_2_0_BETA31_ACCEPTANCE.md"
        self.assertTrue(release.is_file())
        self.assertTrue(acceptance.is_file())
        self.assertIn("512 KiB", release.read_text())
        self.assertIn("Fresh notification canary", acceptance.read_text())


if __name__ == "__main__":
    unittest.main()
