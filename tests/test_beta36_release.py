"""Beta 36 staged-release and approval-notification scope boundary."""

from pathlib import Path
import re
import sys
import unittest

from awesomeversion import AwesomeVersion


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.capabilities import (  # noqa: E402
    BETA_NATIVE_CAPABILITIES,
    CAPABILITIES,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    APPROVAL_AUTHORITY_VERSION,
)
from ha_mcp_engineering.governance.task_models import (  # noqa: E402
    TASK_SCHEMA_VERSION,
)
from ha_mcp_engineering.providers.supervisor_self import (  # noqa: E402
    MAX_SELF_INFO_BYTES,
)


PRE_PROMOTION_VERSION = "2.2.0-beta.35"
BETA36_VERSION = "2.2.0-beta.36"


class Beta36ReleaseBoundaryTests(unittest.TestCase):
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
            matches = re.findall(
                pattern, path.read_text(encoding="utf-8")
            )
            self.assertEqual(len(matches), 1, str(path))
            versions.add(matches[0])
        return versions

    def require_release_phase(self, expected_version: str) -> None:
        versions = self.authoritative_versions()
        self.assertEqual(len(versions), 1)
        actual_version = next(iter(versions))
        if AwesomeVersion(actual_version) > AwesomeVersion(BETA36_VERSION):
            self.skipTest(
                "Beta 36 phase assertions do not apply after a later release"
            )
        self.assertIn(
            actual_version, (PRE_PROMOTION_VERSION, BETA36_VERSION)
        )
        if actual_version != expected_version:
            self.skipTest(
                f"{expected_version} assertions do not apply to "
                f"release phase {actual_version}"
            )

    def assert_documents_resolve_exactly(self) -> None:
        release = (
            ROOT / "docs" / "V2_2_0_BETA36_RELEASE_NOTES.md"
        ).read_text(encoding="utf-8")
        acceptance = (
            ROOT / "docs" / "V2_2_0_BETA36_ACCEPTANCE.md"
        ).read_text(encoding="utf-8")

        for text in (release, acceptance):
            self.assertIn("2.2.0-beta.36", text)
            self.assertIn("/addons/self/info", text)
            self.assertIn("512 KiB", text)
            self.assertIn("fallback", text)
        self.assertIn("33,732-byte", release)
        self.assertIn("1,024-byte fragments", release)
        self.assertIn("IOS_LIVE_ACCEPTANCE_NOT_EXECUTED", acceptance)

    def test_beta36_is_staged_without_changing_published_versions(self):
        self.require_release_phase(PRE_PROMOTION_VERSION)
        marker = ROOT / ".release" / "next-version"

        self.assertEqual(marker.read_text().strip(), BETA36_VERSION)
        self.assertLess(
            AwesomeVersion(PRE_PROMOTION_VERSION),
            AwesomeVersion(BETA36_VERSION),
        )
        self.assert_documents_resolve_exactly()

    def test_beta36_generated_release_state_is_exact(self):
        marker = ROOT / ".release" / "next-version"
        if marker.exists() and AwesomeVersion(
            marker.read_text().strip()
        ) > AwesomeVersion(BETA36_VERSION):
            self.skipTest(
                "Beta 36 generated-state assertions do not apply while a "
                "later release is staged"
            )
        self.require_release_phase(BETA36_VERSION)

        self.assertFalse(marker.exists())
        self.assert_documents_resolve_exactly()

    def test_scope_preserves_authority_tools_and_bounded_identity(self):
        notification_source = (
            BETA_DIR
            / "ha_mcp_engineering"
            / "governance"
            / "approval_notifications.py"
        ).read_text(encoding="utf-8")
        identity_source = (
            BETA_DIR
            / "ha_mcp_engineering"
            / "providers"
            / "supervisor_self.py"
        ).read_text(encoding="utf-8")

        self.assertEqual(len(CAPABILITIES), 25)
        self.assertEqual(len(BETA_NATIVE_CAPABILITIES), 26)
        self.assertEqual(TASK_SCHEMA_VERSION, 1)
        self.assertEqual(APPROVAL_AUTHORITY_VERSION, 3)
        self.assertEqual(MAX_SELF_INFO_BYTES, 512 * 1024)
        self.assertIn('"uri": review_path', notification_source)
        self.assertIn('"fallback": "none"', notification_source)
        self.assertNotIn("authenticationRequired", notification_source)
        self.assertIn("/addons/self/info", identity_source)
        self.assertNotIn("manifest", identity_source.lower())
        self.assertIn(
            'version: "1.1.2"',
            (ROOT / "hass_mcp_admin" / "config.yaml").read_text(),
        )


if __name__ == "__main__":
    unittest.main()
