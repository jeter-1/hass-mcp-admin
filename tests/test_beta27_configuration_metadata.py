"""Beta 27 release staging and configuration-metadata invariants."""

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.governance.normalize import (  # noqa: E402
    AUTOMATION_NORMALIZATION_VERSION,
)
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    RESOURCE_NORMALIZATION_VERSION,
)


class Beta27ReleaseBoundaryTests(unittest.TestCase):
    def test_beta27_is_staged_without_changing_published_versions(self):
        self.assertEqual(
            (ROOT / ".release" / "next-version")
            .read_text(encoding="utf-8")
            .strip(),
            "2.2.0-beta.27",
        )
        beta_config = (BETA_DIR / "config.yaml").read_text(
            encoding="utf-8"
        )
        beta_version = (
            BETA_DIR / "ha_mcp_engineering" / "version.py"
        ).read_text(encoding="utf-8")
        stable_config = (
            ROOT / "hass_mcp_admin" / "config.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn('version: "2.2.0-beta.26"', beta_config)
        self.assertIn('SERVER_VERSION = "2.2.0-beta.26"', beta_version)
        self.assertIn('version: "1.1.2"', stable_config)

    def test_metadata_semantics_have_explicit_contract_versions(self):
        self.assertEqual(AUTOMATION_NORMALIZATION_VERSION, 3)
        self.assertEqual(RESOURCE_NORMALIZATION_VERSION, 2)

    def test_release_and_acceptance_documents_resolve_exactly(self):
        release = ROOT / "docs" / "V2_2_0_BETA27_RELEASE_NOTES.md"
        acceptance = ROOT / "docs" / "V2_2_0_BETA27_ACCEPTANCE.md"
        self.assertTrue(release.is_file())
        self.assertTrue(acceptance.is_file())
        release_text = release.read_text(encoding="utf-8")
        acceptance_text = acceptance.read_text(encoding="utf-8")
        self.assertIn("read-only registry metadata", release_text)
        self.assertIn("provider rejection", release_text)
        self.assertIn("Outstanding plans", release_text)
        self.assertIn("Later deployment acceptance", acceptance_text)
        self.assertIn("failed Beta 26 plan", acceptance_text)


if __name__ == "__main__":
    unittest.main()
