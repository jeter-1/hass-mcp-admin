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
CHECK_SCRIPT = ROOT / "scripts" / "check.ps1"
REPOSITORY_CODE_SENTINEL = "FIXTURE_REPOSITORY_CODE_EXECUTED"


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
        self.write(
            ".gitignore",
            """.artifacts/
__pycache__/
*.pyc
""",
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


class CheckScriptRepositoryFixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="codex-check-test-")
        self.root = Path(self.temporary.name)
        self._populate()

    def close(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def commit(self, message: str) -> None:
        git(self.root, "add", ".")
        git(
            self.root,
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "user.name=Fixture",
            "commit",
            "-m",
            message,
        )

    def _populate(self) -> None:
        self.write("scripts/check.ps1", CHECK_SCRIPT.read_text(encoding="utf-8"))
        self.write("scripts/codex-context.py", "# Fixture context tool.\n")
        self.write("scripts/pr-evidence.py", EVIDENCE_SCRIPT.read_text(encoding="utf-8"))
        self.write(
            "scripts/validate_addon_metadata.py",
            f"""from pathlib import Path

print({REPOSITORY_CODE_SENTINEL!r})
if Path('.fixture-native-failure').exists():
    raise SystemExit(19)
if Path('.fixture-mutate-worktree').exists():
    Path('post-validation-change.txt').write_text('changed during validation\\n')
print('fixture metadata validation passed')
""",
        )
        self.write("hass_mcp_admin/__init__.py", "")
        self.write("hass_mcp_admin/example.py", "VALUE = 1\n")
        self.write("hass_mcp_admin/config.yaml", 'version: "1.1.2"\n')
        self.write("hass_mcp_engineering_beta/__init__.py", "")
        self.write("hass_mcp_engineering_beta/config.yaml", 'version: "2.0.0-test"\n')
        self.write("tests/__init__.py", "")
        self.write(
            "tests/test_smoke.py",
            f"""import unittest

print({REPOSITORY_CODE_SENTINEL!r})


class SmokeTests(unittest.TestCase):
    def test_fixture(self):
        self.assertTrue(True)
""",
        )
        self.write("tests/test_codex_workflow.py", "# Fixture workflow test module.\n")
        self.write(
            ".github/workflows/ci.yml",
            """name: Fixture CI
on: [push]
jobs:
  validate:
    runs-on: ubuntu-latest
""",
        )
        self.write("repository.yaml", "name: Fixture repository\n")
        self.write(
            ".gitignore",
            """.artifacts/
__pycache__/
*.pyc
""",
        )
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "core.autocrlf", "false")
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

    @staticmethod
    def powershell_literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def run_check(
        self,
        executable: str,
        tier: str,
        *,
        base_ref: str = "origin/main",
        authorized_paths: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        arguments = [executable, "-NoProfile", "-NonInteractive"]
        if os.name == "nt":
            arguments.extend(("-ExecutionPolicy", "Bypass"))
        invocation = [
            "&",
            self.powershell_literal(str(self.root / "scripts" / "check.ps1")),
            "-Tier",
            self.powershell_literal(tier),
            "-PythonExecutable",
            self.powershell_literal(sys.executable),
            "-BaseRef",
            self.powershell_literal(base_ref),
        ]
        if tier == "Fast":
            invocation.extend(("-TestTarget", "'tests.test_smoke'"))
        if authorized_paths:
            path_array = ", ".join(
                self.powershell_literal(path) for path in authorized_paths
            )
            invocation.extend(("-AuthorizedProtectedPath", f"@({path_array})"))
        arguments.extend(("-Command", " ".join(invocation)))
        return run(arguments, cwd=self.root, check=False)


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
        (self.fixture.root / "notes.txt").unlink(missing_ok=True)
        (self.fixture.root / "docs" / "pr.md").unlink(missing_ok=True)

    def validation_payload(self) -> dict[str, object]:
        step_names = (
            "Verify base reference",
            "Bind clean Evidence snapshot",
            "Verify protected-path scope",
            "Compile repository Python",
            "Run complete unittest suite",
            "Validate add-on metadata",
            "Parse repository YAML",
            "Check installed dependency consistency",
            "Scan for high-confidence secret patterns",
            "Parse PowerShell syntax",
            "Check Git whitespace",
            "Check staged Git whitespace",
            "Check committed Git whitespace",
            "Check changed text whitespace",
            "Recheck Evidence snapshot",
        )
        coverage = (
            ("full_local_gate", "executed_locally"),
            ("docker_image_build", "delegated_to_ci"),
            ("multiarchitecture_build", "delegated_to_ci"),
            ("disposable_home_assistant", "delegated_to_ci"),
            ("exact_image", "delegated_to_ci"),
            ("publication", "not_applicable"),
            ("provenance", "delegated_to_ci"),
            ("deployment", "not_applicable"),
        )
        return {
            "schema_version": 2,
            "tier": "Evidence",
            "repository_root": str(self.fixture.root.resolve()),
            "base_ref": "origin/main",
            "base_sha": git(
                self.fixture.root,
                "rev-parse",
                "origin/main^{commit}",
            ),
            "head_sha": git(self.fixture.root, "rev-parse", "HEAD^{commit}"),
            "working_tree": "clean",
            "overall_status": "passed",
            "authorized_protected_paths": [],
            "steps": [
                {
                    "name": name,
                    "command": f"fixture: {name}",
                    "status": "passed",
                    "exit_code": 0,
                    "test_count": 3 if name == "Run complete unittest suite" else "unknown",
                    "duration_seconds": 1.25,
                    "note": "skipped_tests=1" if name == "Run complete unittest suite" else "",
                }
                for name in step_names
            ],
            "coverage": [
                {
                    "check": check,
                    "status": status,
                    "evidence": "fixture coverage",
                }
                for check, status in coverage
            ],
        }

    def write_validation(self, payload: object) -> Path:
        evidence = self.fixture.root / ".artifacts" / "validation.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            json.dumps(payload),
            encoding="utf-8",
            newline="\n",
        )
        return evidence

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
        self.write_validation(self.validation_payload())
        text = self.generate().read_text(encoding="utf-8")
        self.assertIn("Overall local status: **passed**", text)
        self.assertIn("skipped_tests=1", text)
        self.assertIn("exact_image: **delegated_to_ci**", text)

    def test_matching_failed_validation_remains_visibly_failed(self):
        payload = self.validation_payload()
        payload["overall_status"] = "failed"
        payload["steps"] = [
            {
                "name": "unit tests",
                "command": "python -m unittest",
                "status": "failed",
                "exit_code": 1,
                "test_count": 1,
                "duration_seconds": 0.5,
                "note": "fixture failure",
            }
        ]
        assert isinstance(payload["coverage"], list)
        payload["coverage"][0]["status"] = "failed_locally"
        self.write_validation(payload)
        text = self.generate().read_text(encoding="utf-8")
        self.assertIn("Overall local status: **failed**", text)
        self.assertIn("fixture failure", text)
        self.assertNotIn("no local test result is claimed", text)

    def test_failed_validation_cannot_add_external_success_claims(self):
        payload = self.validation_payload()
        payload["overall_status"] = "failed"
        payload["steps"] = [
            {
                "name": "unit tests",
                "command": "python -m unittest",
                "status": "failed",
                "exit_code": 1,
            }
        ]
        assert isinstance(payload["coverage"], list)
        payload["coverage"][0]["status"] = "failed_locally"
        payload["coverage"][-1]["status"] = "passed"
        self.write_validation(payload)
        text = self.generate().read_text(encoding="utf-8")
        self.assertIn("inconsistent failed Evidence coverage contract", text)
        self.assertIn("no local test result is claimed", text)
        self.assertNotIn("deployment: **passed**", text)

    def test_stale_or_foreign_validation_context_is_not_claimed(self):
        base_sha = git(self.fixture.root, "rev-parse", "origin/main^{commit}")
        cases = (
            ("unsupported schema version", {"schema_version": 1}),
            ("wrong validation tier", {"tier": "Full"}),
            (
                "different repository worktree",
                {"repository_root": str((self.fixture.root / "foreign").resolve())},
            ),
            ("different base reference", {"base_ref": "main"}),
            ("different base commit", {"base_sha": "0" * 40}),
            ("different head commit", {"head_sha": base_sha}),
            (
                "working tree is not a bound clean snapshot",
                {"working_tree": "dirty"},
            ),
        )
        for reason, changes in cases:
            with self.subTest(reason=reason):
                payload = self.validation_payload()
                payload.update(changes)
                self.write_validation(payload)
                text = self.generate().read_text(encoding="utf-8")
                self.assertIn(reason, text)
                self.assertIn("no local test result is claimed", text)
                self.assertNotIn("Overall local status: **passed**", text)
                self.assertIn("CI-only status is unknown", text)

    def test_windows_path_case_for_same_repository_is_accepted(self):
        if os.name != "nt":
            self.skipTest("Windows path-casing behavior")
        payload = self.validation_payload()
        root = str(self.fixture.root.resolve())
        payload["repository_root"] = root[0].swapcase() + root[1:]
        self.write_validation(payload)
        text = self.generate().read_text(encoding="utf-8")
        self.assertIn("Overall local status: **passed**", text)

    def test_dirty_worktree_rejects_otherwise_matching_validation(self):
        self.write_validation(self.validation_payload())
        self.fixture.write("notes.txt", "changed after validation\n")
        text = self.generate().read_text(encoding="utf-8")
        self.assertIn("working tree is not a bound clean snapshot", text)
        self.assertNotIn("Overall local status: **passed**", text)

    def test_malformed_or_inconsistent_validation_is_not_claimed(self):
        evidence = self.fixture.root / ".artifacts" / "validation.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text("not json\n", encoding="utf-8")
        text = self.generate().read_text(encoding="utf-8")
        self.assertIn("Validation evidence is unreadable", text)
        self.assertNotIn("Overall local status: **passed**", text)

        self.write_validation([])
        text = self.generate().read_text(encoding="utf-8")
        self.assertIn("unsupported shape", text)
        self.assertNotIn("Overall local status: **passed**", text)

        payload = self.validation_payload()
        assert isinstance(payload["steps"], list)
        payload["steps"] = payload["steps"][:1]
        self.write_validation(payload)
        text = self.generate().read_text(encoding="utf-8")
        self.assertIn("incomplete passed Evidence step contract", text)
        self.assertNotIn("Overall local status: **passed**", text)

        payload = self.validation_payload()
        payload["steps"] = [
            {
                "name": "failed step",
                "command": "fixture",
                "status": "failed",
                "exit_code": 1,
            }
        ]
        self.write_validation(payload)
        text = self.generate().read_text(encoding="utf-8")
        self.assertIn("internally inconsistent step results", text)
        self.assertNotIn("Overall local status: **passed**", text)

    def test_unignored_in_repository_output_is_rejected_without_writing(self):
        self.write_validation(self.validation_payload())
        output = self.fixture.root / "docs" / "pr.md"
        result = run(
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
                "docs/pr.md",
            ],
            cwd=self.fixture.root,
            check=False,
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("must be ignored by Git", result.stderr)
        self.assertFalse(output.exists())
        self.assertEqual(git(self.fixture.root, "status", "--porcelain"), "")


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

    def test_fast_validation_routes_to_syntax_and_execution_tests(self):
        text = CHECK_SCRIPT.read_text(encoding="utf-8")
        for target in (
            "tests.test_codex_workflow.PowerShellValidationTests",
            "tests.test_codex_workflow.CheckScriptExecutionTests",
        ):
            self.assertGreaterEqual(text.count(target), 2, target)


class CheckScriptExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.powershell = shutil.which("pwsh") or shutil.which("powershell")

    def setUp(self) -> None:
        if not self.powershell:
            self.skipTest("PowerShell is unavailable")
        self.fixture = CheckScriptRepositoryFixture()

    def tearDown(self) -> None:
        if hasattr(self, "fixture"):
            self.fixture.close()

    @staticmethod
    def output(result: subprocess.CompletedProcess[str]) -> str:
        return result.stdout + result.stderr

    def test_fast_executes_the_tiny_fixture_suite(self):
        result = self.fixture.run_check(self.powershell, "Fast")
        output = self.output(result)
        self.assertEqual(result.returncode, 0, output)
        self.assertIn(REPOSITORY_CODE_SENTINEL, output)
        self.assertIn("Fast validation passed", output)

    def test_protected_path_is_rejected_before_repository_code_on_every_tier(self):
        for tier in ("Fast", "Full", "Evidence"):
            with self.subTest(tier=tier):
                fixture = CheckScriptRepositoryFixture()
                try:
                    fixture.write("hass_mcp_admin/example.py", "VALUE = 2\n")
                    if tier == "Evidence":
                        fixture.commit("fixture unauthorized protected change")
                    result = fixture.run_check(self.powershell, tier)
                    output = self.output(result)
                    self.assertNotEqual(result.returncode, 0, output)
                    self.assertIn("hass_mcp_admin/example.py", output)
                    self.assertIn("AuthorizedProtectedPath", output)
                    self.assertNotIn(REPOSITORY_CODE_SENTINEL, output)
                    if tier == "Evidence":
                        payload = json.loads(
                            (fixture.root / ".artifacts" / "validation.json").read_text(
                                encoding="utf-8"
                            )
                        )
                        self.assertEqual(payload["overall_status"], "failed")
                        self.assertEqual(payload["authorized_protected_paths"], [])
                        self.assertTrue(
                            any(
                                step["name"] == "Verify protected-path scope"
                                and step["status"] == "failed"
                                for step in payload["steps"]
                            ),
                            payload,
                        )
                finally:
                    fixture.close()

    def test_full_accepts_an_authorized_protected_directory_scope(self):
        self.fixture.write("hass_mcp_admin/nested/example.py", "VALUE = 2\n")
        result = self.fixture.run_check(
            self.powershell,
            "Full",
            authorized_paths=("hass_mcp_admin/",),
        )
        output = self.output(result)
        self.assertEqual(result.returncode, 0, output)
        self.assertIn(REPOSITORY_CODE_SENTINEL, output)
        self.assertIn("Full validation passed", output)

    def test_evidence_rejects_a_dirty_worktree_before_repository_code(self):
        self.fixture.write("docs/change.md", "uncommitted change\n")
        result = self.fixture.run_check(self.powershell, "Evidence")
        output = self.output(result)
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("Evidence requires a clean working tree", output)
        self.assertNotIn(REPOSITORY_CODE_SENTINEL, output)
        payload = json.loads(
            (self.fixture.root / ".artifacts" / "validation.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["working_tree"], "dirty")
        self.assertEqual(payload["overall_status"], "failed")

    def test_evidence_accepts_and_records_an_exact_authorized_protected_path(self):
        protected_path = "hass_mcp_admin/example.py"
        self.fixture.write(protected_path, "VALUE = 2\n")
        self.fixture.commit("fixture protected change")
        result = self.fixture.run_check(
            self.powershell,
            "Evidence",
            authorized_paths=(protected_path,),
        )
        output = self.output(result)
        self.assertEqual(result.returncode, 0, output)
        payload = json.loads(
            (self.fixture.root / ".artifacts" / "validation.json").read_text(
                encoding="utf-8"
            )
        )
        base_sha = git(self.fixture.root, "rev-parse", "origin/main^{commit}")
        head_sha = git(self.fixture.root, "rev-parse", "HEAD^{commit}")
        self.assertNotEqual(base_sha, head_sha)
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["tier"], "Evidence")
        self.assertEqual(payload["repository_root"], str(self.fixture.root.resolve()))
        self.assertEqual(payload["base_ref"], "origin/main")
        self.assertEqual(payload["base_sha"], base_sha)
        self.assertEqual(payload["head_sha"], head_sha)
        self.assertEqual(payload["working_tree"], "clean")
        self.assertEqual(payload["overall_status"], "passed")
        self.assertEqual(payload["authorized_protected_paths"], [protected_path])
        consumer = run(
            [
                sys.executable,
                str(self.fixture.root / "scripts" / "pr-evidence.py"),
                "--repo-root",
                str(self.fixture.root),
                "--base",
                "origin/main",
                "--head",
                "HEAD",
                "--output",
                ".artifacts/pr.md",
            ],
            cwd=self.fixture.root,
            check=False,
        )
        self.assertEqual(consumer.returncode, 0, consumer.stdout + consumer.stderr)
        draft = (self.fixture.root / ".artifacts" / "pr.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Overall local status: **passed**", draft)

    def test_evidence_accepts_and_records_multiple_authorized_paths(self):
        protected_paths = (
            "hass_mcp_admin/example.py",
            ".github/workflows/ci.yml",
        )
        self.fixture.write(protected_paths[0], "VALUE = 2\n")
        self.fixture.write(
            protected_paths[1],
            """name: Updated fixture CI
on: [push]
jobs:
  validate:
    runs-on: ubuntu-latest
""",
        )
        self.fixture.commit("fixture multiple protected changes")
        result = self.fixture.run_check(
            self.powershell,
            "Evidence",
            authorized_paths=protected_paths,
        )
        output = self.output(result)
        self.assertEqual(result.returncode, 0, output)
        payload = json.loads(
            (self.fixture.root / ".artifacts" / "validation.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(payload["overall_status"], "passed")
        self.assertEqual(
            payload["authorized_protected_paths"],
            list(protected_paths),
        )

    def test_nonmatching_authorization_is_rejected_as_unused(self):
        changed_path = "hass_mcp_admin/example.py"
        unused_path = "hass_mcp_admin/other.py"
        self.fixture.write(changed_path, "VALUE = 2\n")
        result = self.fixture.run_check(
            self.powershell,
            "Full",
            authorized_paths=(unused_path,),
        )
        output = self.output(result)
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn(changed_path, output)
        self.assertIn(unused_path, output)
        self.assertIn("unused", output.lower())
        self.assertNotIn(REPOSITORY_CODE_SENTINEL, output)

    def test_invalid_authorization_syntax_is_rejected_before_repository_code(self):
        for invalid_path in (
            "../hass_mcp_admin/example.py",
            "C:/hass_mcp_admin/example.py",
            "hass_mcp_admin/*.py",
        ):
            with self.subTest(invalid_path=invalid_path):
                fixture = CheckScriptRepositoryFixture()
                try:
                    fixture.write("hass_mcp_admin/example.py", "VALUE = 2\n")
                    result = fixture.run_check(
                        self.powershell,
                        "Fast",
                        authorized_paths=(invalid_path,),
                    )
                    output = self.output(result)
                    self.assertNotEqual(result.returncode, 0, output)
                    self.assertIn("Authorized protected paths", output)
                    self.assertNotIn(REPOSITORY_CODE_SENTINEL, output)
                finally:
                    fixture.close()

    def test_native_failure_exits_nonzero_and_overwrites_passing_evidence(self):
        self.fixture.write(".fixture-native-failure", "synthetic failure\n")
        self.fixture.commit("fixture native failure")
        evidence = self.fixture.root / ".artifacts" / "validation.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            json.dumps({"overall_status": "passed"}),
            encoding="utf-8",
            newline="\n",
        )
        result = self.fixture.run_check(self.powershell, "Evidence")
        output = self.output(result)
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn(REPOSITORY_CODE_SENTINEL, output)
        payload = json.loads(evidence.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["working_tree"], "clean")
        self.assertEqual(payload["overall_status"], "failed")
        steps = {step["name"]: step for step in payload["steps"]}
        self.assertEqual(
            steps["Validate add-on metadata"]["exit_code"],
            19,
        )
        self.assertEqual(steps["Validate add-on metadata"]["status"], "failed")
        self.assertEqual(steps["Parse repository YAML"]["status"], "passed")
        self.assertNotIn("Evidence validation passed", output)

    def test_evidence_fails_if_the_worktree_changes_during_validation(self):
        self.fixture.write(".fixture-mutate-worktree", "mutate during validation\n")
        self.fixture.commit("fixture worktree mutation")
        result = self.fixture.run_check(self.powershell, "Evidence")
        output = self.output(result)
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn(REPOSITORY_CODE_SENTINEL, output)
        self.assertIn("working tree changed during Evidence validation", output)
        payload = json.loads(
            (self.fixture.root / ".artifacts" / "validation.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(payload["overall_status"], "failed")
        self.assertEqual(payload["working_tree"], "dirty")
        steps = {step["name"]: step for step in payload["steps"]}
        self.assertEqual(steps["Recheck Evidence snapshot"]["status"], "failed")
        self.assertNotIn("Evidence validation passed", output)

    def test_invalid_base_fails_safely_on_every_tier(self):
        missing_base = "refs/remotes/origin/does-not-exist"
        for tier in ("Fast", "Full", "Evidence"):
            with self.subTest(tier=tier):
                fixture = CheckScriptRepositoryFixture()
                try:
                    evidence = fixture.root / ".artifacts" / "validation.json"
                    if tier == "Evidence":
                        evidence.parent.mkdir(parents=True, exist_ok=True)
                        evidence.write_text(
                            json.dumps({"overall_status": "passed"}),
                            encoding="utf-8",
                            newline="\n",
                        )
                    result = fixture.run_check(
                        self.powershell,
                        tier,
                        base_ref=missing_base,
                    )
                    output = self.output(result)
                    self.assertNotEqual(result.returncode, 0, output)
                    self.assertIn(missing_base, output)
                    self.assertNotIn(REPOSITORY_CODE_SENTINEL, output)
                    if tier == "Evidence":
                        payload = json.loads(evidence.read_text(encoding="utf-8"))
                        self.assertEqual(payload["schema_version"], 2)
                        self.assertIsNone(payload["base_sha"])
                        self.assertEqual(payload["overall_status"], "failed")
                finally:
                    fixture.close()


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
        normalized_root = " ".join(root.lower().split())
        self.assertIn(
            "if guidance conflicts, the closer file takes precedence",
            normalized_root,
        )
        runtime = paths[1].read_text(encoding="utf-8")
        self.assertIn("Keep routing fail-closed", runtime)
        self.assertIn("negative tests", runtime)
        tests = paths[2].read_text(encoding="utf-8")
        self.assertIn("deterministic, offline fixtures", tests)
        workflows = paths[3].read_text(encoding="utf-8")
        self.assertIn("minimum GitHub permissions", workflows)
        self.assertIn("must not publish", workflows)
        for path in paths[1:]:
            nested = " ".join(path.read_text(encoding="utf-8").lower().split())
            self.assertIn("this file takes precedence if guidance conflicts", nested)

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
        lowered = text.lower()
        self.assertIn("local environments", lowered)
        self.assertIn("actions", lowered)
        self.assertIn(".codex/", text)
        self.assertIn("-AuthorizedProtectedPath", text)
        self.assertIn("schema-v2 Evidence", text)
        self.assertIn("exact clean repository", text)
        self.assertNotIn("does not define a supported project action schema", lowered)
        self.assertNotIn("did not establish a supported project action schema", lowered)
        self.assertIn("docs/CODEX_WORKFLOW.md", (ROOT / "README.md").read_text(encoding="utf-8"))


class DeploymentChecklistTests(unittest.TestCase):
    def test_stale_fixed_count_and_beta_checklist_are_replaced_by_context(self):
        text = (ROOT / "scripts" / "deploy-beta.ps1").read_text(encoding="utf-8")
        self.assertNotIn("exactly 38", text)
        self.assertNotIn("Beta 25 handoff", text)
        self.assertIn("codex-context.py --format markdown", text)
        self.assertIn("active release and acceptance documents", text)


class ScopeBoundaryTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
