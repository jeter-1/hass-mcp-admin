"""Beta 28 release staging and governed dashboard-update invariants."""

from pathlib import Path
import re
import sys
import unittest

from awesomeversion import AwesomeVersion


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.capabilities import BETA_NATIVE_CAPABILITIES  # noqa: E402
from ha_mcp_engineering.f3_dashboard.adapter import (  # noqa: E402
    OPERATOR_POLICY,
    PROVIDER_CONTRACT_MODEL,
)
from ha_mcp_engineering.f3_dashboard.provider import EXACT_CONTRACTS  # noqa: E402
from ha_mcp_engineering.governance.models import ChangeOperation  # noqa: E402


PRE_PROMOTION_VERSION = "2.2.0-beta.27"
BETA28_VERSION = "2.2.0-beta.28"
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


class Beta28ReleaseBoundaryTests(unittest.TestCase):
    def require_published_phase(self, expected_version: str) -> dict[str, str]:
        versions: dict[str, str] = {}
        for authority, (path, pattern) in AUTHORITATIVE_VERSION_PATTERNS.items():
            matches = pattern.findall(path.read_text(encoding="utf-8"))
            self.assertEqual(len(matches), 1, authority)
            versions[authority] = matches[0]
        self.assertEqual(len(set(versions.values())), 1)
        actual_version = next(iter(versions.values()))
        if AwesomeVersion(actual_version) > AwesomeVersion(BETA28_VERSION):
            self.skipTest(
                "Beta 28 phase assertions do not apply after a later release"
            )
        self.assertIn(
            actual_version,
            (PRE_PROMOTION_VERSION, BETA28_VERSION),
        )
        if actual_version != expected_version:
            self.skipTest(
                f"{expected_version} assertions do not apply to "
                f"published phase {actual_version}"
            )
        return versions

    def test_beta28_is_staged_without_changing_published_versions(self):
        self.assertEqual(
            set(self.require_published_phase(PRE_PROMOTION_VERSION).values()),
            {PRE_PROMOTION_VERSION},
        )
        self.assertEqual(
            (ROOT / ".release" / "next-version")
            .read_text(encoding="utf-8")
            .strip(),
            BETA28_VERSION,
        )
        self.assertIn(
            'version: "1.1.2"',
            (ROOT / "hass_mcp_admin" / "config.yaml").read_text(
                encoding="utf-8"
            ),
        )

    def test_beta28_generated_release_state_is_exact(self):
        self.assertEqual(
            set(self.require_published_phase(BETA28_VERSION).values()),
            {BETA28_VERSION},
        )
        self.assertFalse((ROOT / ".release" / "next-version").exists())
        self.assertIn(
            'version: "1.1.2"',
            (ROOT / "hass_mcp_admin" / "config.yaml").read_text(
                encoding="utf-8"
            ),
        )

    def test_dashboard_update_release_contract_is_exact(self):
        native = {item["tool"]: item for item in BETA_NATIVE_CAPABILITIES}
        capability = native["create_dashboard_update_plan"]
        self.assertEqual(capability["operation_class"], "proposal")
        self.assertEqual(capability["fallback"], "none")
        self.assertFalse(capability["direct_write_allowed"])
        self.assertTrue(capability["non_atomic"])
        self.assertEqual(
            ChangeOperation.UPDATE_DASHBOARD.value,
            "update_dashboard",
        )
        self.assertEqual(
            PROVIDER_CONTRACT_MODEL,
            "ha-mcp-dashboard-full-result-update-v1",
        )
        self.assertEqual(
            OPERATOR_POLICY,
            "bounded_dashboard_update_non_atomic_v1",
        )
        contract = EXACT_CONTRACTS["8.1.1"]
        self.assertEqual(
            contract.compatibility_entry,
            "ha-mcp-v8.1.1-e1d76a6e",
        )
        self.assertEqual(contract.policy_classification, "persistent_write")

    def test_release_and_acceptance_documents_resolve_exactly(self):
        release = ROOT / "docs" / "V2_2_0_BETA28_RELEASE_NOTES.md"
        acceptance = ROOT / "docs" / "V2_2_0_BETA28_ACCEPTANCE.md"
        self.assertTrue(release.is_file())
        self.assertTrue(acceptance.is_file())
        release_text = release.read_text(encoding="utf-8")
        acceptance_text = acceptance.read_text(encoding="utf-8")
        self.assertIn("operator_accepted_non_atomic", release_text)
        self.assertIn("proposal-only", release_text)
        self.assertIn("Later deployment acceptance", acceptance_text)
        self.assertIn("readback-only", acceptance_text)


if __name__ == "__main__":
    unittest.main()
