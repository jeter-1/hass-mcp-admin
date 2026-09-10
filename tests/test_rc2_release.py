"""Current materialization and preserved RC2 document/compatibility invariants."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
VERSION = "2.2.0-rc.2"
CURRENT_VERSION = "2.2.0-rc.6"
ACCEPTANCE = ROOT / "docs" / "V2_2_0_RC2_ACCEPTANCE.md"
RELEASE_NOTES = ROOT / "docs" / "V2_2_0_RC2_RELEASE_NOTES.md"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


METADATA = _load_module(
    "rc2_validate_addon_metadata",
    ROOT / "scripts" / "validate_addon_metadata.py",
)

sys.path.insert(0, str(BETA))
from ha_mcp_engineering.ha_core_readmission import (  # noqa: E402
    CORE_CAPABILITY_PROFILES,
    SUPPORTED_CORE_RELEASES,
)


class Rc2ReleaseTests(unittest.TestCase):
    def test_current_release_is_materialized_and_documents_resolve_exactly(self):
        config = yaml.safe_load((BETA / "config.yaml").read_text(encoding="utf-8"))
        version_source = (
            BETA / "ha_mcp_engineering" / "version.py"
        ).read_text(encoding="utf-8")
        self.assertEqual(config["version"], CURRENT_VERSION)
        self.assertRegex(
            version_source,
            rf'(?m)^SERVER_VERSION = "{re.escape(CURRENT_VERSION)}"$',
        )
        self.assertEqual(METADATA.BETA_VERSION, CURRENT_VERSION)
        self.assertFalse((ROOT / ".release" / "next-version").exists())

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "codex-context.py"),
                "--repo-root",
                str(ROOT),
                "--format",
                "json",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)
        self.assertEqual(context["versions"]["engineering"], CURRENT_VERSION)
        self.assertEqual(context["versions"]["stable"], "1.1.2")
        self.assertEqual(context["versions"]["staged"], "unknown")
        self.assertEqual(context["documents"]["resolution_status"], "exact")
        self.assertEqual(
            context["documents"]["active_acceptance_document"],
            "docs/V2_2_0_RC6_ACCEPTANCE.md",
        )
        self.assertEqual(
            context["documents"]["active_release_notes"],
            "docs/V2_2_0_RC6_RELEASE_NOTES.md",
        )

    def test_documents_bind_release_and_preserved_catalog(self):
        for path in (ACCEPTANCE, RELEASE_NOTES):
            text = " ".join(path.read_text(encoding="utf-8").split())
            for statement in (
                "2.2.0-rc.2",
                "materialized source candidate",
                "`.release/next-version` has been consumed",
                "stable remains 1.1.2",
                "51",
                "25",
                "76",
                "ha_get_operation_status",
                "fallback",
            ):
                self.assertIn(statement, text, f"{statement!r} missing from {path.name}")
            self.assertNotIn("RC2 is staged", text)
            self.assertNotIn("advertised Engineering source is 2.2.0-rc.1", text)

    def test_acceptance_discloses_every_new_compiled_core_trust_authority(self):
        text = ACCEPTANCE.read_text(encoding="utf-8")
        heading = "## Compiled production trust-policy expansion"
        self.assertIn(heading, text)
        section = text.split(heading, 1)[1].split("\n## ", 1)[0]
        normalized_section = " ".join(section.split())
        newly_trusted = tuple(
            release
            for release in SUPPORTED_CORE_RELEASES
            if release[0].startswith("2026.9.")
        )
        self.assertEqual(
            tuple(version for version, _commit, _digest in newly_trusted),
            ("2026.9.0", "2026.9.1"),
        )
        for version, source_commit, image_digest in newly_trusted:
            with self.subTest(version=version):
                self.assertIn(version, section)
                self.assertIn(source_commit, section)
                self.assertIn(image_digest, section)
        self.assertIn(
            f"{len(CORE_CAPABILITY_PROFILES)} binary-owned capability profiles",
            normalized_section,
        )
        self.assertIn(
            "does not authorize an unlisted Core release",
            normalized_section,
        )

    def test_core_2026_9_ci_is_immutable_and_workflow_permissions_are_unchanged(self):
        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        )
        job = workflow["jobs"]["real-ha-contract-tests"]
        rows = {
            row["ha_version"]: row
            for row in job["strategy"]["matrix"]["include"]
        }
        for version in ("2026.9.0", "2026.9.1"):
            row = rows[version]
            self.assertIn(f":{version}@sha256:", row["ha_image"])
            self.assertIn(":8.4.3@sha256:", row["ha_mcp_image"])
            self.assertEqual(
                row["ha_mcp_source_commit"],
                "eac7a3aa7063432e9af17e7d7726040e909c7b8f",
            )
        self.assertNotIn("permissions", job)
        cleanup = next(
            step
            for step in job["steps"]
            if step.get("name") == "Sanitize and remove disposable Home Assistant"
        )
        self.assertEqual(cleanup["if"], "always()")


if __name__ == "__main__":
    unittest.main()
