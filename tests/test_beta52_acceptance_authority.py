"""Bind Beta 52 acceptance to its final reviewed source authority."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = ROOT / "docs" / "V2_2_0_BETA52_ACCEPTANCE.md"
SHIPPED_TAG = "v2.2.0-beta.52"
SOURCE_AUTHORITY_PATHS = (
    "hass_mcp_engineering_beta/ha_mcp_engineering/dependency",
    "hass_mcp_engineering_beta/ha_mcp_engineering/governance/helper_dependency.py",
    "tests/fixtures/dependency/hamcp089_beta51_label_target_scope_replay_v1.json",
    "tests/support/replay_hamcp089_beta51_label_scope.py",
    "tests/test_beta52_helper_label_target_scope.py",
)


class Beta52AcceptanceAuthorityTests(unittest.TestCase):
    def test_acceptance_commit_resolves_to_exact_shipped_source(self):
        text = ACCEPTANCE.read_text(encoding="utf-8")
        match = re.search(
            r"replay correction source authority is exact commit\s+`([0-9a-f]{40})`",
            text,
        )
        self.assertIsNotNone(match)
        source_commit = match.group(1)
        resolved = subprocess.run(
            ["git", "rev-parse", f"{source_commit}^{{commit}}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(source_commit, resolved)
        comparison = subprocess.run(
            [
                "git",
                "diff",
                "--exit-code",
                source_commit,
                SHIPPED_TAG,
                "--",
                *SOURCE_AUTHORITY_PATHS,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            0,
            comparison.returncode,
            comparison.stdout + comparison.stderr,
        )


if __name__ == "__main__":
    unittest.main()
