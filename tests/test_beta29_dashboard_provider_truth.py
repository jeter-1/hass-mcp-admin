"""Beta 29 staging and dashboard-provider truthfulness invariants."""

from pathlib import Path
import re
import sys
import unittest

from awesomeversion import AwesomeVersion


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.capabilities import BETA_NATIVE_CAPABILITIES  # noqa: E402
from ha_mcp_engineering.f3_dashboard.planning import (  # noqa: E402
    KNOWN_HYPHENLESS_EXISTING_UPDATE_INCOMPATIBLE_RELEASES,
)
from ha_mcp_engineering.f3_dashboard.provider import EXACT_CONTRACTS  # noqa: E402


PRE_PROMOTION_VERSION = "2.2.0-beta.28"
BETA29_VERSION = "2.2.0-beta.29"
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


class Beta29ReleaseBoundaryTests(unittest.TestCase):
    def require_published_phase(self, expected_version: str) -> dict[str, str]:
        versions: dict[str, str] = {}
        for authority, (path, pattern) in AUTHORITATIVE_VERSION_PATTERNS.items():
            matches = pattern.findall(path.read_text(encoding="utf-8"))
            self.assertEqual(len(matches), 1, authority)
            versions[authority] = matches[0]
        self.assertEqual(len(set(versions.values())), 1)
        actual_version = next(iter(versions.values()))
        if AwesomeVersion(actual_version) > AwesomeVersion(BETA29_VERSION):
            self.skipTest(
                "Beta 29 phase assertions do not apply after a later release"
            )
        self.assertIn(actual_version, (PRE_PROMOTION_VERSION, BETA29_VERSION))
        if actual_version != expected_version:
            self.skipTest(
                f"{expected_version} assertions do not apply to "
                f"published phase {actual_version}"
            )
        return versions

    def test_beta29_is_staged_without_changing_published_versions(self):
        self.assertEqual(
            set(self.require_published_phase(PRE_PROMOTION_VERSION).values()),
            {PRE_PROMOTION_VERSION},
        )
        self.assertEqual(
            (ROOT / ".release" / "next-version")
            .read_text(encoding="utf-8")
            .strip(),
            BETA29_VERSION,
        )
        self.assertIn(
            'version: "1.1.2"',
            (ROOT / "hass_mcp_admin" / "config.yaml").read_text(
                encoding="utf-8"
            ),
        )

    def test_beta29_generated_release_state_is_exact(self):
        self.assertEqual(
            set(self.require_published_phase(BETA29_VERSION).values()),
            {BETA29_VERSION},
        )
        self.assertFalse((ROOT / ".release" / "next-version").exists())
        self.assertIn(
            'version: "1.1.2"',
            (ROOT / "hass_mcp_admin" / "config.yaml").read_text(
                encoding="utf-8"
            ),
        )

    def test_beta29_scope_preserves_provider_and_tool_policy(self):
        self.assertEqual(
            KNOWN_HYPHENLESS_EXISTING_UPDATE_INCOMPATIBLE_RELEASES,
            frozenset({"8.1.1"}),
        )
        self.assertEqual(
            EXACT_CONTRACTS["8.1.1"].compatibility_entry,
            "ha-mcp-v8.1.1-e1d76a6e",
        )
        self.assertEqual(len(BETA_NATIVE_CAPABILITIES), 25)
        dashboard = {
            item["tool"]: item for item in BETA_NATIVE_CAPABILITIES
        }["create_dashboard_update_plan"]
        self.assertEqual(dashboard["fallback"], "none")
        self.assertFalse(dashboard["direct_write_allowed"])

    def test_release_and_acceptance_documents_resolve_exactly(self):
        release = ROOT / "docs" / "V2_2_0_BETA29_RELEASE_NOTES.md"
        acceptance = ROOT / "docs" / "V2_2_0_BETA29_ACCEPTANCE.md"
        self.assertTrue(release.is_file())
        self.assertTrue(acceptance.is_file())
        release_text = release.read_text(encoding="utf-8")
        acceptance_text = acceptance.read_text(encoding="utf-8")
        self.assertIn("structured provider rejection", release_text)
        self.assertIn("8.1.1", release_text)
        self.assertIn("Hyphenated existing-dashboard canary", acceptance_text)
        self.assertIn("deployment is authorized", acceptance_text)


if __name__ == "__main__":
    unittest.main()
