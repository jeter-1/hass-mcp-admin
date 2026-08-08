"""Beta 27 release staging and configuration-metadata invariants."""

from pathlib import Path
import re
import sys
import unittest

from awesomeversion import AwesomeVersion


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.governance.normalize import (  # noqa: E402
    AUTOMATION_NORMALIZATION_VERSION,
)
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    RESOURCE_NORMALIZATION_VERSION,
)


PRE_PROMOTION_VERSION = "2.2.0-beta.26"
BETA27_VERSION = "2.2.0-beta.27"
AUTHORITATIVE_VERSION_PATTERNS = {
    "add_on": (
        BETA_DIR / "config.yaml",
        re.compile(r'(?m)^version: "([^"]+)"$'),
    ),
    "runtime": (
        BETA_DIR / "ha_mcp_engineering" / "version.py",
        re.compile(r'(?m)^SERVER_VERSION = "([^"]+)"$'),
    ),
    "validator": (
        ROOT / "scripts" / "validate_addon_metadata.py",
        re.compile(r'(?m)^BETA_VERSION = "([^"]+)"$'),
    ),
}


class Beta27ReleaseBoundaryTests(unittest.TestCase):
    def require_published_phase(self, expected_version: str) -> dict[str, str]:
        versions: dict[str, str] = {}
        for authority, (path, pattern) in AUTHORITATIVE_VERSION_PATTERNS.items():
            matches = pattern.findall(path.read_text(encoding="utf-8"))
            self.assertEqual(len(matches), 1, authority)
            versions[authority] = matches[0]
        self.assertEqual(len(set(versions.values())), 1)
        actual_version = next(iter(versions.values()))
        if AwesomeVersion(actual_version) > AwesomeVersion(BETA27_VERSION):
            self.skipTest(
                "Beta 27 phase assertions do not apply after a later release"
            )
        self.assertIn(
            actual_version,
            (PRE_PROMOTION_VERSION, BETA27_VERSION),
        )
        if actual_version != expected_version:
            self.skipTest(
                f"{expected_version} assertions do not apply to "
                f"published phase {actual_version}"
            )
        return versions

    def test_beta27_is_staged_without_changing_published_versions(self):
        self.assertEqual(
            set(self.require_published_phase(PRE_PROMOTION_VERSION).values()),
            {PRE_PROMOTION_VERSION},
        )
        self.assertEqual(
            (ROOT / ".release" / "next-version")
            .read_text(encoding="utf-8")
            .strip(),
            BETA27_VERSION,
        )
        stable_config = (
            ROOT / "hass_mcp_admin" / "config.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn('version: "1.1.2"', stable_config)

    def test_beta27_generated_release_state_is_exact(self):
        self.assertEqual(
            set(self.require_published_phase(BETA27_VERSION).values()),
            {BETA27_VERSION},
        )
        self.assertFalse((ROOT / ".release" / "next-version").exists())
        stable_config = (
            ROOT / "hass_mcp_admin" / "config.yaml"
        ).read_text(encoding="utf-8")
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
