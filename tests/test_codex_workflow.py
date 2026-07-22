from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_SCRIPT = ROOT / "scripts" / "codex-context.py"
EVIDENCE_SCRIPT = ROOT / "scripts" / "pr-evidence.py"


def run(
    arguments: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        arguments,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if check and result.returncode:
        raise AssertionError(
            f"Command failed ({result.returncode}): {arguments!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def git(cwd: Path, *arguments: str) -> str:
    return run(["git", *arguments], cwd=cwd).stdout.strip()


class RepositoryFixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="codex-context-test-")
        self.root = Path(self.temporary.name)
        self._populate()

    def close(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def _populate(self) -> None:
        self.write(
            "AGENTS.md",
            """# Fixture Instructions

## Local Commands

- Context: `python scripts/codex-context.py --format json`

## Frozen or Protected Paths

- `hass_mcp_admin/` - stable

## Prohibited Actions

- Access production systems
""",
        )
        self.write("hass_mcp_admin/config.yaml", 'version: "1.1.2"\n')
        self.write("hass_mcp_engineering_beta/config.yaml", 'version: "2.0.0-rc2-dev1"\n')
        self.write(
            "hass_mcp_engineering_beta/ha_mcp_engineering/capabilities.py",
            """CAPABILITIES = ({"tool": "one"},)
BETA_NATIVE_CAPABILITIES = ({"tool": "two"},)
PLANNED_CAPABILITIES = ()
""",
        )
        self.write(
            "hass_mcp_engineering_beta/ha_mcp_engineering/upstream_tool_policy.json",
            json.dumps(
                {
                    "reviewed_upstream_version": "1.2.3",
                    "reviewed_stock_catalog_tool_count": 2,
                    "tools": [
                        {"classification": "automatic_read"},
                        {"classification": "persistent_write"},
                    ],
                }
            ),
        )
        self.write(
            "hass_mcp_engineering_beta/ha_mcp_engineering/version.py",
            'SERVER_VERSION = "2.0.0-rc2-dev1"\n',
        )
        self.write(
            "scripts/validate_addon_metadata.py",
            'BETA_VERSION = "2.0.0-rc2-dev1"\n',
        )
        self.write("docs/RC2DEV1_RELEASE_NOTES.md", "# Release\n")
        self.write("docs/RC2DEV1_ACCEPTANCE.md", "# Acceptance\n")
        self.write("docs/RC2_RELEASE_NOTES.md", "# RC2 Release\n")
        self.write("docs/RC2_ACCEPTANCE.md", "# RC2 Acceptance\n")
        self.write(
            ".github/workflows/ci.yml",
            """name: CI
jobs:
  validate:
    runs-on: ubuntu-latest
""",
        )
        self.write(
            "README.md",
            "The development stage is now hardened as `2.0.0-rc2-dev1`.\n",
        )
        git(self.root, "init", "-b", "main")
        git(
            self.root,
            "remote",
            "add",
            "origin",
            "https://fixture-remote-secret@example.invalid/example/context-fixture.git",
        )
        git(self.root, "add", ".")
        git(
            self.root,
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "user.name=Fixture",
            "commit",
            "-m",
            "fixture baseline",
        )
        git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")


class ContextToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = RepositoryFixture()
        cls.policy_path = (
            cls.fixture.root
            / "hass_mcp_engineering_beta"
            / "ha_mcp_engineering"
            / "upstream_tool_policy.json"
        )
        cls.policy_text = cls.policy_path.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close()

    def tearDown(self) -> None:
        self.policy_path.write_text(self.policy_text, encoding="utf-8", newline="\n")
        (self.fixture.root / "notes.txt").unlink(missing_ok=True)

    def context(self, output_format: str = "json", *, env=None) -> subprocess.CompletedProcess[str]:
        return run(
            [
                sys.executable,
                str(CONTEXT_SCRIPT),
                "--repo-root",
                str(self.fixture.root),
                "--format",
                output_format,
            ],
            cwd=self.fixture.root,
            env=env,
        )

    def test_json_is_valid_derives_counts_and_excludes_controlled_secrets(self):
        env = dict(os.environ)
        env["SUPERVISOR_TOKEN"] = "synthetic-context-secret-value"
        env["GITHUB_TOKEN"] = "synthetic-github-secret-value"
        output = self.context(env=env).stdout
        payload = json.loads(output)
        self.assertEqual(payload["repository"]["identity"], "example/context-fixture")
        self.assertEqual(payload["versions"]["engineering"], "2.0.0-rc2-dev1")
        counts = payload["tool_counts"]
        self.assertEqual(
            counts["static_registered"],
            counts["canonical"] + counts["engineering_native"],
        )
        self.assertEqual(
            counts["expected_connector_total"],
            counts["static_registered"] + counts["expected_delegated_reads"],
        )
        for forbidden in (
            "SUPERVISOR_TOKEN",
            "GITHUB_TOKEN",
            "synthetic-context-secret-value",
            "synthetic-github-secret-value",
            "fixture-remote-secret",
        ):
            self.assertNotIn(forbidden, output)

    def test_markdown_contains_required_major_sections(self):
        output = self.context("markdown").stdout
        for heading in (
            "## Repository State",
            "## Version and Release Context",
            "## Tool Count Expectations",
            "## Validation and Boundaries",
            "## Inconsistencies and Unknowns",
        ):
            self.assertIn(heading, output)

    def test_unknown_values_and_dirty_git_state_are_reported_honestly(self):
        self.policy_path.unlink()
        self.fixture.write("notes.txt", "untracked\n")
        payload = json.loads(self.context().stdout)
        self.assertEqual(payload["tool_counts"]["reviewed_upstream_version"], "unknown")
        fields = {item["field"] for item in payload["unknowns"]}
        self.assertIn("upstream_policy", fields)
        self.assertIn("reviewed_upstream_version", fields)
        repository = payload["repository"]
        self.assertEqual(repository["branch"], "main")
        self.assertEqual(repository["head_sha"], repository["origin_main_sha"])
        self.assertEqual(repository["working_tree"], "dirty")
        self.assertIn("notes.txt", repository["changed_paths"])


class PrEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = RepositoryFixture()
        git(cls.fixture.root, "switch", "-c", "feature/evidence")
        cls.fixture.write("docs/change.md", "change\n")
        git(cls.fixture.root, "add", "docs/change.md")
        git(
            cls.fixture.root,
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "user.name=Fixture",
            "commit",
            "-m",
            "fixture change",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close()

    def setUp(self) -> None:
        for relative in (".artifacts", "work"):
            path = self.fixture.root / relative
            if path.exists():
                shutil.rmtree(path)

    def generate(self, output: str = ".artifacts/pr.md") -> Path:
        run(
            [
                sys.executable,
                str(EVIDENCE_SCRIPT),
                "--repo-root",
                str(self.fixture.root),
                "--base",
                "origin/main",
                "--head",
                "HEAD",
                "--output",
                output,
            ],
            cwd=self.fixture.root,
        )
        return self.fixture.root / output

    def test_missing_validation_is_honest_and_generated_evidence_is_bounded(self):
        for number in range(250):
            self.fixture.write(f"work/path-{number:03d}.md", "bounded\n")
        text = self.generate().read_text(encoding="utf-8")
        self.assertIn("Validation evidence is missing", text)
        self.assertIn("CI-only status is unknown", text)
        self.assertNotIn("Overall local status: **passed**", text)
        self.assertIn("`docs/change.md`", text)
        self.assertLessEqual(len(text), 60_000)
        self.assertIn("File list truncated after 200 paths", text)

    def test_validation_evidence_is_consumed_without_losing_skips(self):
        evidence = self.fixture.root / ".artifacts" / "validation.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "overall_status": "passed",
                    "steps": [
                        {
                            "name": "unit tests",
                            "command": "python -m unittest",
                            "status": "passed",
                            "exit_code": 0,
                            "test_count": 3,
                            "duration_seconds": 1.25,
                            "note": "skipped_tests=1",
                        }
                    ],
                    "coverage": [
                        {
                            "check": "exact_image",
                            "status": "delegated_to_ci",
                            "evidence": "workflow job",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        text = self.generate().read_text(encoding="utf-8")
        self.assertIn("Overall local status: **passed**", text)
        self.assertIn("skipped_tests=1", text)
        self.assertIn("exact_image: **delegated_to_ci**", text)


class PowerShellValidationTests(unittest.TestCase):
    def test_all_repository_powershell_parses(self):
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if not executable:
            self.skipTest("PowerShell is unavailable")
        for path in sorted((ROOT / "scripts").glob("*.ps1")):
            escaped = str(path).replace("'", "''")
            result = run(
                [
                    executable,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    f"[scriptblock]::Create([IO.File]::ReadAllText('{escaped}')) | Out-Null",
                ],
                cwd=ROOT,
                check=False,
            )
            self.assertEqual(result.returncode, 0, f"{path}: {result.stderr}")


class InstructionFileTests(unittest.TestCase):
    def test_instruction_files_exist_with_required_safety_boundaries(self):
        paths = (
            ROOT / "AGENTS.md",
            ROOT / "hass_mcp_engineering_beta" / "AGENTS.md",
            ROOT / "tests" / "AGENTS.md",
            ROOT / ".github" / "workflows" / "AGENTS.md",
        )
        for path in paths:
            self.assertTrue(path.is_file(), path)
        root = paths[0].read_text(encoding="utf-8")
        for phrase in (
            "GitHub `main` is the software source of truth",
            "Stable v1.1.2",
            "No live Home Assistant",
            "## Code Review Rules",
            "newly reachable writes",
            "workflow permission expansion",
            "tests that prove only success",
            "## Completion Contract",
        ):
            self.assertIn(phrase, root)
        runtime = paths[1].read_text(encoding="utf-8")
        self.assertIn("Keep routing fail-closed", runtime)
        self.assertIn("negative tests", runtime)
        tests = paths[2].read_text(encoding="utf-8")
        self.assertIn("deterministic, offline fixtures", tests)
        workflows = paths[3].read_text(encoding="utf-8")
        self.assertIn("minimum GitHub permissions", workflows)
        self.assertIn("must not publish", workflows)

    def test_workflow_document_and_readme_link_exist(self):
        workflow = ROOT / "docs" / "CODEX_WORKFLOW.md"
        self.assertTrue(workflow.is_file())
        text = workflow.read_text(encoding="utf-8")
        for phrase in (
            "## Keyboard Workflow",
            "## Remote and Mobile Workflow",
            "## Authorization Profiles",
            "### Implementation",
            "### Independent Review",
            "### Corrective Follow-up",
            "### Remote Implementation",
            "### Release Preparation",
        ):
            self.assertIn(phrase, text)
        self.assertIn("docs/CODEX_WORKFLOW.md", (ROOT / "README.md").read_text(encoding="utf-8"))


class DeploymentChecklistTests(unittest.TestCase):
    def test_stale_fixed_count_and_beta_checklist_are_replaced_by_context(self):
        text = (ROOT / "scripts" / "deploy-beta.ps1").read_text(encoding="utf-8")
        self.assertNotIn("exactly 38", text)
        self.assertNotIn("Beta 25 handoff", text)
        self.assertIn("codex-context.py --format markdown", text)
        self.assertIn("active release and acceptance documents", text)


class ScopeBoundaryTests(unittest.TestCase):
    @staticmethod
    def changed_paths() -> set[str]:
        commands = (
            ("diff", "--name-only", "origin/main...HEAD"),
            ("diff", "--name-only"),
            ("diff", "--cached", "--name-only"),
            ("ls-files", "--others", "--exclude-standard"),
        )
        paths: set[str] = set()
        for arguments in commands:
            paths.update(
                line.strip().replace("\\", "/")
                for line in git(ROOT, *arguments).splitlines()
                if line.strip()
            )
        return paths

    def test_stable_v1_files_are_unchanged(self):
        stable = sorted(
            path for path in self.changed_paths() if path.startswith("hass_mcp_admin/")
        )
        self.assertEqual(stable, [])

    def test_engineering_agents_file_is_not_a_release_version_change(self):
        path = ROOT / "scripts" / "validate_addon_metadata.py"
        spec = importlib.util.spec_from_file_location("codex_metadata_validator", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
            report = module.validate_repository(
                ROOT,
                base_ref="origin/main",
                paths={"hass_mcp_engineering_beta/AGENTS.md"},
            )
        finally:
            sys.modules.pop(spec.name, None)
        self.assertFalse(report.beta_changed)
        self.assertEqual(report.beta_version, report.compared_version)

    def test_runtime_schema_registration_and_provider_routes_are_unchanged(self):
        runtime_prefix = "hass_mcp_engineering_beta/ha_mcp_engineering/"
        runtime = sorted(
            path for path in self.changed_paths() if path.startswith(runtime_prefix)
        )
        self.assertEqual(runtime, [])

    def test_workflow_permissions_and_release_metadata_are_unchanged(self):
        paths = self.changed_paths()
        workflow_yaml = sorted(
            path
            for path in paths
            if path.startswith(".github/workflows/")
            and path.lower().endswith((".yml", ".yaml"))
        )
        release = sorted(
            path
            for path in paths
            if path.startswith(".release/")
            or path
            in {
                "repository.yaml",
                "hass_mcp_engineering_beta/config.yaml",
                "hass_mcp_engineering_beta/Dockerfile",
            }
        )
        self.assertEqual(workflow_yaml, [])
        self.assertEqual(release, [])


if __name__ == "__main__":
    unittest.main()
