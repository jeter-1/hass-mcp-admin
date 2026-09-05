"""Bind the active Beta 58 documents to the materialized release state."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
ACCEPTANCE = ROOT / "docs" / "V2_2_0_BETA58_ACCEPTANCE.md"
RELEASE_NOTES = ROOT / "docs" / "V2_2_0_BETA58_RELEASE_NOTES.md"
VERSION = "2.2.0-beta.58"


class Beta58ReleaseMaterializationTruthTests(unittest.TestCase):
    def test_active_documents_describe_the_materialized_release(self) -> None:
        config = (BETA / "config.yaml").read_text(encoding="utf-8")
        version_module = (
            BETA / "ha_mcp_engineering" / "version.py"
        ).read_text(encoding="utf-8")
        self.assertRegex(config, rf'(?m)^version: "{re.escape(VERSION)}"$')
        self.assertRegex(
            version_module,
            rf'(?m)^SERVER_VERSION = "{re.escape(VERSION)}"$',
        )
        self.assertFalse((ROOT / ".release" / "next-version").exists())

        stale_claims = (
            "advertised Engineering release remains 2.2.0-beta.57",
            "advertised Engineering version remains 2.2.0-beta.57",
            "Beta 58 is the staged source candidate",
            "Beta 58 stages",
            "materialization is pending",
            "does not materialize",
            "staged source candidate",
        )
        required_claims = (
            "Beta 58 is the materialized source candidate",
            "advertised Engineering version is 2.2.0-beta.58",
            "stable remains 1.1.2",
            "`.release/next-version` was consumed",
            "Materialization has occurred",
            "Beta 57 is the prior and rollback release",
        )
        for path in (ACCEPTANCE, RELEASE_NOTES):
            text = path.read_text(encoding="utf-8")
            normalized = " ".join(text.split())
            for claim in required_claims:
                self.assertIn(
                    claim, normalized, f"{claim!r} missing from {path.name}"
                )
            for claim in stale_claims:
                self.assertNotIn(
                    claim, normalized, f"stale claim in {path.name}"
                )

    def test_documents_preserve_post_materialization_authority_boundaries(self) -> None:
        for path in (ACCEPTANCE, RELEASE_NOTES):
            text = path.read_text(encoding="utf-8")
            normalized = " ".join(text.split())
            for action in (
                "merge",
                "publication",
                "deployment",
                "production-key creation",
                "environment/secret configuration",
                "registry publication",
                "trust-anchor activation",
                "live updates",
            ):
                self.assertIn(
                    action, normalized, f"{action!r} missing from {path.name}"
                )
            self.assertIn(
                "did not merge, publish, deploy, restart anything or access a "
                "live system",
                normalized,
            )


if __name__ == "__main__":
    unittest.main()
