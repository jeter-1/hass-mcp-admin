from __future__ import annotations

import json
import importlib.metadata
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_SCRIPT = ROOT / "scripts" / "codex-context.py"
PROMOTION_SCRIPT = ROOT / "scripts" / "promote_next_release.py"
EVIDENCE_SCRIPT = ROOT / "scripts" / "pr-evidence.py"
CHECK_SCRIPT = ROOT / "scripts" / "check.ps1"
SHARD_SCRIPT = ROOT / "scripts" / "unit_test_shards.py"
LOCAL_SHARD_SCRIPT = ROOT / "scripts" / "local_unit_test_shards.py"
REPOSITORY_CODE_SENTINEL = "FIXTURE_REPOSITORY_CODE_EXECUTED"
# HAMCP-458: check.ps1 compares the interpreter with tests/requirements.lock,
# so the fixture lock pins what the test interpreter has installed.
FIXTURE_PYYAML_VERSION = importlib.metadata.version("PyYAML")
FIXTURE_LOCK = f"pyyaml=={FIXTURE_PYYAML_VERSION} \\\n    --hash=sha256:{'0' * 64}\n"
# A field a fake answer leaves out, as distinct from one it reports as null.
ABSENT = object()


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

    def set_engineering_version(self, version: str) -> None:
        self.write("hass_mcp_engineering_beta/config.yaml", f'version: "{version}"\n')
        self.write(
            "hass_mcp_engineering_beta/ha_mcp_engineering/version.py",
            f'SERVER_VERSION = "{version}"\n',
        )
        self.write(
            "scripts/validate_addon_metadata.py",
            f'BETA_VERSION = "{version}"\n',
        )
        self.write(
            "README.md",
            f"The development stage is now hardened as `{version}`.\n",
        )

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
        self.write("hass_mcp_admin/retired-addon-config.yaml", 'version: "1.1.2"\n')
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
        # HAMCP-290: the retired delegated reads are read from routes.py.
        self.write(
            "hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/routes.py",
            'RETIRED_DELEGATED_READ_TOOLS: tuple[str, ...] = ("ha_get_hacs_info",)\n',
        )
        self.write(
            "scripts/validate_addon_metadata.py",
            'BETA_VERSION = "2.0.0-rc2-dev1"\n',
        )
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
        self.write(
            "docs/RC2DEV1_RELEASE_NOTES.md",
            "# 2.0.0-rc2-dev1 release\n\nVersion: `2.0.0-rc2-dev1`\n",
        )
        self.write(
            "docs/RC2DEV1_ACCEPTANCE.md",
            "# 2.0.0-rc2-dev1 acceptance\n\nVersion: `2.0.0-rc2-dev1`\n",
        )
        self.write(
            "docs/RC2_RELEASE_NOTES.md",
            "# 2.0.0-rc.2 release\n\nVersion: `2.0.0-rc.2`\n",
        )
        self.write(
            "docs/RC2_ACCEPTANCE.md",
            "# 2.0.0-rc.2 acceptance\n\nVersion: `2.0.0-rc.2`\n",
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
            f"""import json
from pathlib import Path
import subprocess
import sys

print({REPOSITORY_CODE_SENTINEL!r})
# HAMCP-356: record the arguments, and move a reference mid-run on request.
Path('.artifacts').mkdir(exist_ok=True)
Path('.artifacts/fixture-metadata-argv.json').write_text(json.dumps(sys.argv[1:]))
if Path('.fixture-move-ref').exists():
    moved_ref = Path('.fixture-move-ref').read_text().strip()
    subprocess.run(['git', 'update-ref', moved_ref, 'HEAD'], check=True)
if Path('.fixture-native-failure').exists():
    raise SystemExit(19)
if Path('.fixture-mutate-worktree').exists():
    Path('post-validation-change.txt').write_text('changed during validation\\n')
print('fixture metadata validation passed')
""",
        )
        self.write("hass_mcp_admin/__init__.py", "")
        self.write("hass_mcp_admin/example.py", "VALUE = 1\n")
        self.write("hass_mcp_admin/retired-addon-config.yaml", 'version: "1.1.2"\n')
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
        self.write("tests/requirements.lock", FIXTURE_LOCK)
        # Inputs for the opt-in -Parallel path: the real shard modules, a
        # two-general-shard plan, and one real-time measurement.
        self.write("scripts/unit_test_shards.py", SHARD_SCRIPT.read_text(encoding="utf-8"))
        self.write("scripts/local_unit_test_shards.py", LOCAL_SHARD_SCRIPT.read_text(encoding="utf-8"))
        self.write(
            "ci/unit-test-shards.json",
            json.dumps({"schema_version": 1, "general_shards": 2, "default_module_seconds": 1,
                        "module_seconds": {}}) + "\n",
        )
        self.write("tests/support/__init__.py", "")
        self.write("tests/support/real_time.py", (ROOT / "tests" / "support" / "real_time.py").read_text(encoding="utf-8"))
        self.write(
            "tests/test_shard_mix.py",
            """import os
import unittest

from support.real_time import real_time_measurement


class MixTests(unittest.TestCase):
    def run(self, result=None):
        if os.path.exists(".fixture-unstarted") and self._testMethodName == "test_plain":
            print("fixture-unstarted-diagnostic " + self.id())
            return result
        return super().run(result)

    def test_plain(self):
        self.assertFalse(os.path.exists(".fixture-test-failure"), "synthetic fixture test failure")

    @unittest.skip("synthetic skip")
    def test_skipped(self):
        raise AssertionError("not reached")


class MeasuredTests(unittest.TestCase):
    @real_time_measurement("synthetic latency bound")
    def test_measured(self):
        self.assertTrue(True)
""",
        )
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
        base_ref: str | None = "origin/main",
        python_executable: str | None = sys.executable,
        authorized_paths: tuple[str, ...] = (),
        parallel: str | None = None,
        reuse_ci_run: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        # None omits -PythonExecutable or -BaseRef so check.ps1 uses its
        # defaults. Only tests with a controlled PATH (no real gh) may omit
        # -BaseRef.
        arguments = [executable, "-NoProfile", "-NonInteractive"]
        if os.name == "nt":
            arguments.extend(("-ExecutionPolicy", "Bypass"))
        invocation = [
            "&",
            self.powershell_literal(str(self.root / "scripts" / "check.ps1")),
            "-Tier",
            self.powershell_literal(tier),
        ]
        if python_executable is not None:
            invocation.extend(("-PythonExecutable", self.powershell_literal(python_executable)))
        if base_ref is not None:
            invocation.extend(("-BaseRef", self.powershell_literal(base_ref)))
        if tier == "Fast":
            invocation.extend(("-TestTarget", "'tests.test_smoke'"))
        if authorized_paths:
            path_array = ", ".join(
                self.powershell_literal(path) for path in authorized_paths
            )
            invocation.extend(("-AuthorizedProtectedPath", f"@({path_array})"))
        if parallel is not None:
            invocation.extend(("-Parallel", parallel))
        if reuse_ci_run is not None:
            invocation.extend(("-ReuseCiRun", self.powershell_literal(reuse_ci_run)))
        arguments.extend(("-Command", " ".join(invocation)))
        return run(arguments, cwd=self.root, env=env, check=False)


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

    def isolated_context(
        self,
        version: str,
        documents: dict[str, str] | None = None,
        *,
        output_format: str = "json",
    ) -> str:
        fixture = RepositoryFixture()
        try:
            fixture.set_engineering_version(version)
            for relative, text in (documents or {}).items():
                fixture.write(relative, text)
            return run(
                [
                    sys.executable,
                    str(CONTEXT_SCRIPT),
                    "--repo-root",
                    str(fixture.root),
                    "--format",
                    output_format,
                ],
                cwd=fixture.root,
            ).stdout
        finally:
            fixture.close()

    @staticmethod
    def version_document(title: str, version: str) -> str:
        return f"# {version} {title}\n\nVersion: `{version}`\n"

    def test_json_is_valid_derives_counts_and_excludes_controlled_secrets(self):
        env = dict(os.environ)
        env["SUPERVISOR_TOKEN"] = "synthetic-context-secret-value"
        env["GITHUB_TOKEN"] = "synthetic-github-secret-value"
        output = self.context(env=env).stdout
        payload = json.loads(output)
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["repository"]["identity"], "example/context-fixture")
        self.assertEqual(payload["versions"]["engineering"], "2.0.0-rc2-dev1")
        self.assertEqual(payload["documents"]["resolution_status"], "exact")
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

    def test_context_counts_only_the_compiled_blueprint_read_projection(self):
        fixture = RepositoryFixture()
        self.addCleanup(fixture.close)
        runtime = "hass_mcp_engineering_beta/ha_mcp_engineering/"
        # HAMCP-437 I2a-D: the shipped 9.2.0 policy (ANSWER 0755-01 item 5).
        policy = json.loads((ROOT / runtime / "upstream_tool_policy_9_2_0.json").read_text())
        adapter_path = runtime + "providers/upstream_blueprint.py"
        adapter_source = (ROOT / adapter_path).read_text()
        fixture.write(runtime + "upstream_tool_policy.json", json.dumps(policy))

        def count():
            result = run([sys.executable, str(CONTEXT_SCRIPT), "--repo-root",
                          str(fixture.root), "--format", "json"], cwd=fixture.root)
            return json.loads(result.stdout)["tool_counts"]["expected_delegated_reads"]

        # HAMCP-437 I2a-D: the 9.2.0 policy file has 23 automatic_read
        # entries; it classifies the retired ha_get_hacs_info unsupported, so
        # none is subtracted, and the fixture has no platform-read adapter.
        # The ha-mcp-9.2.0-blueprint-list-get-v1 binding (9.2.0, 51e3aac7,
        # 6a09a0f2) matches the ha_manage_blueprints entry, adding one.
        self.assertEqual(count(), 23)  # Policy data without compiled code is not a wrapper.
        fixture.write(adapter_path, adapter_source + "\nraise RuntimeError('must not execute')\n")
        self.assertEqual(count(), 24)
        blueprint = next(item for item in policy["tools"]
                         if item["upstream_name"] == "ha_manage_blueprints")
        self.assertEqual(blueprint["classification"], "mixed_or_requires_wrapper")
        blueprint["argument_restrictions"] = ["uncompiled-wrapper"]
        fixture.write(runtime + "upstream_tool_policy.json", json.dumps(policy))
        self.assertEqual(count(), 23)

    def test_context_subtracts_retired_delegated_reads_from_source(self):
        # HAMCP-290: a retired name is subtracted only while it is an automatic
        # read; an unreadable retired-read literal makes the totals unknown.
        fixture = RepositoryFixture()
        self.addCleanup(fixture.close)
        runtime = "hass_mcp_engineering_beta/ha_mcp_engineering/"

        def counts(tools):
            fixture.write(runtime + "upstream_tool_policy.json", json.dumps({
                "reviewed_upstream_version": "1.2.3",
                "reviewed_stock_catalog_tool_count": len(tools),
                "tools": tools,
            }))
            result = run([sys.executable, str(CONTEXT_SCRIPT), "--repo-root",
                          str(fixture.root), "--format", "json"], cwd=fixture.root)
            return json.loads(result.stdout)

        hacs = {"upstream_name": "ha_get_hacs_info", "classification": "automatic_read"}
        state = {"upstream_name": "ha_get_state", "classification": "automatic_read"}
        payload = counts([hacs, state])["tool_counts"]
        self.assertEqual(payload["expected_delegated_reads"], 1)
        self.assertEqual(payload["expected_connector_total"], 3)
        payload = counts([dict(hacs, classification="persistent_write"), state])["tool_counts"]
        self.assertEqual(payload["expected_delegated_reads"], 1)
        self.assertEqual(payload["expected_connector_total"], 3)
        fixture.write(runtime + "ha_core_readmission/routes.py",
                      "RETIRED_DELEGATED_READ_TOOLS = tuple(sorted(NAMES))\n")
        payload = counts([hacs, state])
        self.assertEqual(payload["tool_counts"]["expected_delegated_reads"], "unknown")
        self.assertEqual(payload["tool_counts"]["expected_connector_total"], "unknown")
        self.assertIn("retired_delegated_reads",
                      {item["field"] for item in payload["unknowns"]})

    def test_tool_counts_use_newest_exact_reviewed_policy_not_legacy_default(self):
        fixture = RepositoryFixture()
        try:
            policy_root = (
                "hass_mcp_engineering_beta/ha_mcp_engineering/"
            )
            fixture.write(
                policy_root + "upstream_tool_policy_8_4_1.json",
                json.dumps(
                    {
                        "reviewed_upstream_version": "8.4.1",
                        "reviewed_stock_catalog_tool_count": 3,
                        "tools": [
                            {"classification": "automatic_read"},
                            {"classification": "automatic_read"},
                            {"classification": "held_for_canary"},
                        ],
                    }
                ),
            )
            fixture.write(
                policy_root + "upstream_release_registry.json",
                json.dumps(
                    {
                        "default_version": "1.2.3",
                        "releases": [
                            {
                                "approval_status": "reviewed",
                                "release_tag": "v1.2.3",
                                "policy_resource": "upstream_tool_policy.json",
                            },
                            {
                                "approval_status": "reviewed",
                                "release_tag": "v8.4.1",
                                "policy_resource": (
                                    "upstream_tool_policy_8_4_1.json"
                                ),
                            },
                            {
                                "approval_status": "pending",
                                "release_tag": "v9.0.0",
                                "policy_resource": "unreviewed.json",
                            },
                        ],
                    }
                ),
            )
            payload = json.loads(
                run(
                    [
                        sys.executable,
                        str(CONTEXT_SCRIPT),
                        "--repo-root",
                        str(fixture.root),
                        "--format",
                        "json",
                    ],
                    cwd=fixture.root,
                ).stdout
            )
            counts = payload["tool_counts"]
            self.assertEqual(counts["reviewed_upstream_version"], "8.4.1")
            self.assertEqual(counts["reviewed_stock_catalog"], 3)
            self.assertEqual(counts["expected_delegated_reads"], 2)
            self.assertEqual(counts["expected_connector_total"], 4)
            self.assertEqual(
                payload["sources"]["upstream_policy"],
                policy_root + "upstream_tool_policy_8_4_1.json",
            )
        finally:
            fixture.close()

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
        for label in (
            "Resolution status",
            "Active release notes",
            "Active acceptance document",
            "Historical references (informational only)",
            "Limitations",
        ):
            self.assertIn(label, output)
        self.assertNotIn("Active release and acceptance documents", output)

    def test_exact_development_rc_pair_is_active(self):
        version = "2.0.0-rc2-dev12"
        payload = json.loads(
            self.isolated_context(
                version,
                {
                    "docs/RC2DEV12_RELEASE_NOTES.md": self.version_document(
                        "Release", version
                    ),
                    "docs/RC2DEV12_ACCEPTANCE.md": self.version_document(
                        "Acceptance", version
                    ),
                },
            )
        )
        documents = payload["documents"]
        self.assertEqual(documents["resolution_status"], "exact")
        self.assertEqual(
            documents["active_release_notes"],
            "docs/RC2DEV12_RELEASE_NOTES.md",
        )
        self.assertEqual(
            documents["active_acceptance_document"],
            "docs/RC2DEV12_ACCEPTANCE.md",
        )

    def test_exact_semantic_beta_pair_is_active(self):
        version = "2.1.0-beta.1"
        payload = json.loads(
            self.isolated_context(
                version,
                {
                    "docs/V2_1_0_BETA1_RELEASE_NOTES.md": (
                        self.version_document("2.1A Dev1 release", version)
                    ),
                    "docs/V2_1_0_BETA1_ACCEPTANCE.md": (
                        self.version_document("2.1A Dev1 acceptance", version)
                    ),
                },
            )
        )
        self.assertEqual(payload["versions"]["release_stage"], "beta 1")
        self.assertEqual(payload["documents"]["resolution_status"], "exact")
        self.assertEqual(
            payload["documents"]["active_release_notes"],
            "docs/V2_1_0_BETA1_RELEASE_NOTES.md",
        )
        self.assertEqual(
            payload["documents"]["active_acceptance_document"],
            "docs/V2_1_0_BETA1_ACCEPTANCE.md",
        )

    def test_staged_release_has_separate_exact_promotion_authority(self):
        current = "2.0.0-rc2-dev12"
        staged = "2.0.0-rc2-dev13"
        payload = json.loads(
            self.isolated_context(
                current,
                {
                    ".release/next-version": staged + "\n",
                    "docs/RC2DEV12_RELEASE_NOTES.md": (
                        f"# {current} immutable historical release\n\n"
                        f"Version: `{current}`\nHistorical only; cannot authorize.\n"
                    ),
                    "docs/RC2DEV12_ACCEPTANCE.md": (
                        f"# {current} rejected acceptance\n\n"
                        f"Version: `{current}`\nHistorical only; cannot authorize.\n"
                    ),
                    "docs/RC2DEV13_RELEASE_NOTES.md": self.version_document(
                        "release notes", staged
                    ),
                    "docs/RC2DEV13_ACCEPTANCE.md": self.version_document(
                        "acceptance", staged
                    ),
                },
            )
        )
        self.assertEqual(payload["versions"]["engineering"], current)
        self.assertEqual(payload["versions"]["staged"], staged)
        self.assertEqual(payload["documents"]["resolution_status"], "missing")
        self.assertEqual(
            payload["documents"]["active_acceptance_document"], "unknown"
        )
        staged_release = payload["staged_release"]
        self.assertEqual(staged_release["version"], staged)
        self.assertEqual(staged_release["declaration"], ".release/next-version")
        self.assertEqual(staged_release["documents"]["resolution_status"], "exact")
        self.assertEqual(
            staged_release["documents"]["active_acceptance_document"],
            "docs/RC2DEV13_ACCEPTANCE.md",
        )
        self.assertIn(
            "docs/RC2DEV12_ACCEPTANCE.md",
            staged_release["documents"]["historical_references"],
        )
        self.assertIn(
            "docs/RC2DEV12_RELEASE_NOTES.md",
            staged_release["documents"]["historical_references"],
        )

    def test_context_remains_consistent_after_candidate_is_applied(self):
        current = "2.2.0-beta.58"
        candidate = "2.2.0-rc.1"
        fixture = RepositoryFixture()
        try:
            fixture.set_engineering_version(current)
            fixture.write(".release/next-version", candidate + "\n")
            fixture.write(
                "docs/V2_2_0_RC1_RELEASE_NOTES.md",
                self.version_document("release notes", candidate),
            )
            fixture.write(
                "docs/V2_2_0_RC1_ACCEPTANCE.md",
                self.version_document("acceptance", candidate),
            )
            fixture.write(
                "README.md",
                "Beta 58 is immutable release history. "
                "RC1 is its corrective release target.\n",
            )

            spec = importlib.util.spec_from_file_location(
                "_promotion_context_regression", PROMOTION_SCRIPT
            )
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            promotion = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(promotion)
            self.assertEqual(
                promotion.apply_candidate(fixture.root),
                (current, candidate),
            )

            payload = json.loads(
                run(
                    [
                        sys.executable,
                        str(CONTEXT_SCRIPT),
                        "--repo-root",
                        str(fixture.root),
                        "--format",
                        "json",
                    ],
                    cwd=fixture.root,
                ).stdout
            )
            self.assertEqual(payload["versions"]["engineering"], candidate)
            self.assertEqual(payload["versions"]["staged"], "unknown")
            self.assertEqual(payload["documents"]["resolution_status"], "exact")
            self.assertEqual(
                payload["documents"]["active_release_notes"],
                "docs/V2_2_0_RC1_RELEASE_NOTES.md",
            )
            self.assertEqual(
                payload["documents"]["active_acceptance_document"],
                "docs/V2_2_0_RC1_ACCEPTANCE.md",
            )
            self.assertEqual(payload["staged_release"]["version"], "unknown")
            self.assertEqual(payload["staged_release"]["declaration"], "unknown")
            self.assertEqual(payload["inconsistencies"], [])
        finally:
            fixture.close()

    def test_missing_development_rc_pair_fails_closed(self):
        documents = json.loads(self.isolated_context("2.0.0-rc2-dev12"))[
            "documents"
        ]
        self.assertEqual(documents["resolution_status"], "missing")
        self.assertEqual(documents["active_release_notes"], "unknown")
        self.assertEqual(documents["active_acceptance_document"], "unknown")

    def test_partial_development_rc_pair_does_not_fill_from_history(self):
        version = "2.0.0-rc2-dev12"
        documents = json.loads(
            self.isolated_context(
                version,
                {
                    "docs/RC2DEV12_RELEASE_NOTES.md": self.version_document(
                        "Release", version
                    )
                },
            )
        )["documents"]
        self.assertEqual(documents["resolution_status"], "partial")
        self.assertEqual(
            documents["active_release_notes"],
            "docs/RC2DEV12_RELEASE_NOTES.md",
        )
        self.assertEqual(documents["active_acceptance_document"], "unknown")

    def test_exact_final_rc_pair_uses_established_convention(self):
        documents = json.loads(self.isolated_context("2.0.0-rc.2"))["documents"]
        self.assertEqual(documents["resolution_status"], "exact")
        self.assertEqual(documents["active_release_notes"], "docs/RC2_RELEASE_NOTES.md")
        self.assertEqual(
            documents["active_acceptance_document"],
            "docs/RC2_ACCEPTANCE.md",
        )

    def test_missing_final_rc_pair_does_not_promote_rc3a_documents(self):
        documents = json.loads(
            self.isolated_context(
                "2.0.0-rc.3",
                {
                    "docs/RC3A_RELEASE_NOTES.md": self.version_document(
                        "RC3A release", "2.0.0-rc2-dev3"
                    ),
                    "docs/RC3A_ACCEPTANCE.md": self.version_document(
                        "RC3A acceptance", "2.0.0-rc2-dev3"
                    ),
                },
            )
        )["documents"]
        self.assertEqual(documents["resolution_status"], "missing")
        self.assertEqual(documents["active_release_notes"], "unknown")
        self.assertEqual(documents["active_acceptance_document"], "unknown")
        self.assertFalse(
            any("RC3A" in path for path in documents["historical_references"])
        )

    def test_stable_version_form_uses_exact_versioned_document_convention(self):
        version = "2.0.0"
        payload = json.loads(
            self.isolated_context(
                version,
                {
                    "docs/V2_0_0_RELEASE_NOTES.md": self.version_document(
                        "2.0.0 release", version
                    ),
                    "docs/V2_0_0_ACCEPTANCE.md": self.version_document(
                        "2.0.0 acceptance", version
                    ),
                },
            )
        )
        self.assertEqual(payload["versions"]["release_stage"], "stable")
        self.assertEqual(payload["documents"]["resolution_status"], "exact")
        self.assertEqual(
            payload["documents"]["active_release_notes"],
            "docs/V2_0_0_RELEASE_NOTES.md",
        )
        self.assertEqual(
            payload["documents"]["active_acceptance_document"],
            "docs/V2_0_0_ACCEPTANCE.md",
        )

    def test_unversioned_stable_documents_do_not_gain_authority(self):
        documents = json.loads(
            self.isolated_context(
                "1.1.2",
                {
                    "docs/STABLE_RELEASE_NOTES.md": self.version_document(
                        "Stable release", "1.1.2"
                    ),
                    "docs/STABLE_ACCEPTANCE.md": self.version_document(
                        "Stable acceptance", "1.1.2"
                    ),
                },
            )
        )["documents"]
        self.assertEqual(documents["resolution_status"], "missing")
        self.assertEqual(documents["active_release_notes"], "unknown")
        self.assertEqual(documents["active_acceptance_document"], "unknown")
        self.assertEqual(documents["historical_references"], [])

    def test_unsupported_version_format_fails_closed(self):
        payload = json.loads(self.isolated_context("2.0.0-preview1"))
        self.assertEqual(payload["versions"]["release_stage"], "unknown")
        self.assertEqual(payload["documents"]["resolution_status"], "unsupported")
        self.assertEqual(payload["documents"]["active_release_notes"], "unknown")
        self.assertEqual(
            payload["documents"]["active_acceptance_document"],
            "unknown",
        )

    def test_release_notes_and_acceptance_remain_separate_fields(self):
        documents = json.loads(self.context().stdout)["documents"]
        self.assertIsInstance(documents, dict)
        self.assertNotEqual(
            documents["active_release_notes"],
            documents["active_acceptance_document"],
        )
        self.assertNotIn("active_documents", documents)

    def test_older_documents_are_historical_only(self):
        version = "2.0.0-rc2-dev12"
        documents = json.loads(
            self.isolated_context(
                version,
                {
                    "docs/RC2DEV12_RELEASE_NOTES.md": self.version_document(
                        "Release", version
                    ),
                    "docs/RC2DEV12_ACCEPTANCE.md": self.version_document(
                        "Acceptance", version
                    ),
                },
            )
        )["documents"]
        self.assertIn(
            "docs/RC2DEV1_ACCEPTANCE.md",
            documents["historical_references"],
        )
        self.assertNotIn(
            documents["active_acceptance_document"],
            documents["historical_references"],
        )
        self.assertTrue(
            any(
                "informational only" in limitation
                and "cannot authorize" in limitation
                for limitation in documents["limitations"]
            )
        )

    def test_missing_exact_acceptance_is_unknown_with_stop_limitation(self):
        version = "2.0.0-rc2-dev12"
        documents = json.loads(
            self.isolated_context(
                version,
                {
                    "docs/RC2DEV12_RELEASE_NOTES.md": self.version_document(
                        "Release", version
                    )
                },
            )
        )["documents"]
        self.assertEqual(documents["active_acceptance_document"], "unknown")
        joined = " ".join(documents["limitations"])
        self.assertIn("stop release or deployment work", joined)
        self.assertIn("Never substitute release notes or a historical reference", joined)

    def test_document_core_mismatch_never_becomes_active(self):
        documents = json.loads(self.isolated_context("3.0.0-rc2-dev1"))[
            "documents"
        ]
        self.assertEqual(documents["resolution_status"], "missing")
        self.assertEqual(documents["active_release_notes"], "unknown")
        self.assertEqual(documents["active_acceptance_document"], "unknown")

    def test_bounded_negative_mentions_do_not_establish_authority(self):
        version = "2.0.0-rc2-dev12"
        negative = (
            "# RC2dev12 document\n\n"
            f"This document is not authority for `{version}`.\n"
        )
        documents = json.loads(
            self.isolated_context(
                version,
                {
                    "docs/RC2DEV12_RELEASE_NOTES.md": negative,
                    "docs/RC2DEV12_ACCEPTANCE.md": negative,
                },
            )
        )["documents"]
        self.assertEqual(documents["resolution_status"], "missing")
        self.assertEqual(documents["active_release_notes"], "unknown")
        self.assertEqual(documents["active_acceptance_document"], "unknown")

    def test_development_heading_prefix_collision_is_not_exact(self):
        version = "2.0.0-rc2-dev1"
        wrong_heading = (
            "# RC2dev10 document\n\n"
            f"This historical comparison mentions `{version}`.\n"
        )
        documents = json.loads(
            self.isolated_context(
                version,
                {
                    "docs/RC2DEV1_RELEASE_NOTES.md": wrong_heading,
                    "docs/RC2DEV1_ACCEPTANCE.md": wrong_heading,
                },
            )
        )["documents"]
        self.assertEqual(documents["resolution_status"], "missing")
        self.assertEqual(documents["active_release_notes"], "unknown")
        self.assertEqual(documents["active_acceptance_document"], "unknown")

    def test_development_heading_suffix_is_not_exact(self):
        version = "2.0.0-rc2-dev1"
        extended_heading = (
            "# RC2dev1-draft document\n\n"
            f"This historical comparison mentions `{version}`.\n"
        )
        documents = json.loads(
            self.isolated_context(
                version,
                {
                    "docs/RC2DEV1_RELEASE_NOTES.md": extended_heading,
                    "docs/RC2DEV1_ACCEPTANCE.md": extended_heading,
                },
            )
        )["documents"]
        self.assertEqual(documents["resolution_status"], "missing")
        self.assertEqual(documents["active_release_notes"], "unknown")
        self.assertEqual(documents["active_acceptance_document"], "unknown")

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
            "Snapshot repository Git identity",
            "Verify base reference",
            "Bind clean Evidence snapshot",
            "Verify protected-path scope",
            "Bound scratch Git discovery",
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
            "Recheck repository Git identity",
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

    def test_passed_evidence_requires_scratch_and_identity_guards(self):
        for name in ("Snapshot repository Git identity", "Bound scratch Git discovery",
                     "Recheck repository Git identity"):
            with self.subTest(missing=name):
                payload = self.validation_payload()
                payload["steps"] = [step for step in payload["steps"] if step["name"] != name]
                self.write_validation(payload)
                text = self.generate().read_text(encoding="utf-8")
                self.assertIn("incomplete passed Evidence step contract", text)
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

    REUSE_RUN_ID = "123456789"

    def reused_payload(self) -> dict:
        # HAMCP-365: the record check.ps1 -ReuseCiRun writes after a passed reuse.
        payload = self.validation_payload()
        for step in payload["steps"]:
            if step["name"] == "Run complete unittest suite":
                step["name"] = "Reuse CI unittest shard results"
                step["command"] = "fixture: Reuse CI unittest shard results"
                step["note"] = "skipped_tests=1; reused CI run 123456789 attempt 2; no unit test ran locally"
        payload["coverage"][0]["status"] = "executed_locally_except_unit_suite"
        payload["coverage"].insert(
            1, {"check": "unit_test_suite", "status": "reused_from_ci", "evidence": "fixture coverage"}
        )
        payload["unit_test_execution"] = {
            "mode": "reused_ci",
            "sharded": True,
            "executed": False,
            "run_id": self.REUSE_RUN_ID,
            "result": "passed",
            "run_attempt": 2,
            "head_sha": payload["head_sha"],
            "source_tree": git(self.fixture.root, "rev-parse", "HEAD^{tree}"),
        }
        return payload

    def test_reused_unit_test_results_are_claimed_only_as_reused(self):
        self.write_validation(self.validation_payload())
        self.assertNotIn("Unit tests:", self.generate().read_text(encoding="utf-8"))
        payload = self.reused_payload()
        self.write_validation(payload)
        text = self.generate().read_text(encoding="utf-8")
        self.assertIn("Overall local status: **passed**", text)
        tree = payload["unit_test_execution"]["source_tree"]
        self.assertIn(
            "Unit tests: **reused from CI run `123456789` attempt `2`** for tree "
            f"`{tree}`; no unit test ran locally.\n",
            text,
        )
        self.assertIn("full_local_gate: **executed_locally_except_unit_suite**", text)
        self.assertIn("unit_test_suite: **reused_from_ci**", text)
        self.assertIn("| Reuse CI unittest shard results |", text)
        self.assertNotIn("Run complete unittest suite", text)

    def test_inconsistent_unit_test_reuse_is_not_claimed(self):
        def edit_execution(**changes):
            def apply(payload):
                payload["unit_test_execution"].update(changes)
            return apply

        def rename_step(payload):
            for step in payload["steps"]:
                if step["name"] == "Reuse CI unittest shard results":
                    step["name"] = "Run complete unittest suite"

        def drop_declaration(payload):
            del payload["unit_test_execution"]

        def drop_unit_row(payload):
            del payload["coverage"][1]

        base_sha = git(self.fixture.root, "rev-parse", "origin/main^{commit}")
        cases = (
            ("inconsistent unit-test reuse declaration", "a local unit-test run beside the reuse", rename_step),
            ("inconsistent unit-test reuse declaration", "executed", edit_execution(executed=True)),
            ("inconsistent unit-test reuse declaration", "executed null", edit_execution(executed=None)),
            ("inconsistent unit-test reuse declaration", "integer run id", edit_execution(run_id=123456789)),
            ("inconsistent unit-test reuse declaration", "run id with a newline", edit_execution(run_id="123456789\n")),
            ("inconsistent unit-test reuse declaration", "leading zero", edit_execution(run_id="0123")),
            ("inconsistent unit-test reuse declaration", "passed with a refused reuse", edit_execution(result="failed")),
            ("inconsistent unit-test reuse declaration", "reuse step without a declaration", drop_declaration),
            ("not bound to this head and its tree", "another head", edit_execution(head_sha=base_sha)),
            ("not bound to this head and its tree", "another tree", edit_execution(source_tree="0" * 40)),
            ("not bound to this head and its tree", "no tree", edit_execution(source_tree=None)),
            ("incomplete passed Evidence coverage contract", "no unit-test row", drop_unit_row),
        )
        for reason, name, change in cases:
            with self.subTest(name):
                payload = self.reused_payload()
                change(payload)
                self.write_validation(payload)
                text = self.generate().read_text(encoding="utf-8")
                self.assertIn(reason, text)
                self.assertIn("no local test result is claimed", text)
                self.assertNotIn("Overall local status: **passed**", text)
                self.assertNotIn("Unit tests:", text)
        # A plain record cannot borrow the reuse coverage either.
        payload = self.validation_payload()
        payload["coverage"] = self.reused_payload()["coverage"]
        self.write_validation(payload)
        text = self.generate().read_text(encoding="utf-8")
        self.assertIn("incomplete passed Evidence coverage contract", text)
        self.assertNotIn("Overall local status: **passed**", text)

    def test_failed_reuse_records_stay_visible_and_name_what_ran(self):
        def failed(step_name, result, unit_status):
            payload = self.reused_payload()
            payload["overall_status"] = "failed"
            for step in payload["steps"]:
                if step["name"] == step_name:
                    step.update(status="failed", exit_code=1, note="fixture failure")
            payload["coverage"][0]["status"] = "failed_locally"
            payload["coverage"][1]["status"] = unit_status
            payload["unit_test_execution"]["result"] = result
            return payload

        refused = failed("Reuse CI unittest shard results", "failed", "not_reused")
        self.write_validation(refused)
        text = self.generate().read_text(encoding="utf-8")
        self.assertIn("Overall local status: **failed**", text)
        self.assertIn(
            "Unit tests: **not run** - CI run `123456789` was not reused and the unit suite was not "
            "executed locally.\n",
            text,
        )
        self.assertNotIn("no local test result is claimed", text)

        elsewhere = failed("Check Git whitespace", "passed", "reused_from_ci")
        self.write_validation(elsewhere)
        text = self.generate().read_text(encoding="utf-8")
        self.assertIn("Overall local status: **failed**", text)
        self.assertIn("Unit tests: **reused from CI run `123456789` attempt `2`**", text)

        for name, payload in (
            ("refused reuse claimed as reused", failed("Reuse CI unittest shard results", "failed", "reused_from_ci")),
            ("passed reuse claimed as not reused", failed("Check Git whitespace", "passed", "not_reused")),
        ):
            with self.subTest(name):
                self.write_validation(payload)
                text = self.generate().read_text(encoding="utf-8")
                self.assertIn("inconsistent failed Evidence coverage contract", text)
                self.assertIn("no local test result is claimed", text)


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

    def test_changed_text_whitespace_skips_only_an_unset_whitespace_attribute(self):
        # HAMCP-371 D4. The file stays untracked, so the Git whitespace steps
        # never see it and only the changed-text scan decides.
        relative = "docs/hash-bound.txt"
        self.fixture.write(relative, "bound line \n")
        cases = (
            ("-whitespace", 0),
            (None, 1),
            ("-text", 1),
        )
        for attribute, expected in cases:
            with self.subTest(attribute=attribute):
                self.fixture.write(
                    ".gitattributes",
                    f"{relative} {attribute}\n" if attribute else "",
                )
                result = self.fixture.run_check(self.powershell, "Fast")
                output = self.output(result)
                self.assertEqual(result.returncode, expected, output)
                if expected:
                    self.assertIn(f"Trailing whitespace found: {relative}:1", output)
                    self.assertNotIn("Fast validation passed", output)
                else:
                    self.assertIn("Changed text files contain no trailing whitespace.", output)
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

    def test_full_secret_scan_reads_a_safe_tracked_dotfile(self):
        hidden_path = ".safe-dotfile"
        self.fixture.write(hidden_path, "safe fixture content\n")
        self.fixture.commit("fixture safe dotfile")
        self.assertEqual(
            git(self.fixture.root, "ls-files", "--", hidden_path),
            hidden_path,
        )
        result = self.fixture.run_check(self.powershell, "Full")
        output = self.output(result)
        self.assertEqual(result.returncode, 0, output)
        self.assertIn(
            "No bounded high-confidence credential pattern was found.",
            output,
        )

    def test_full_secret_scan_rejects_a_tracked_dotfile_with_a_secret(self):
        hidden_path = ".credential-fixture"
        synthetic_token = "github_" + "pat_" + "A" * 24
        self.fixture.write(hidden_path, synthetic_token + "\n")
        self.fixture.commit("fixture credential dotfile")
        self.assertEqual(
            git(self.fixture.root, "ls-files", "--", hidden_path),
            hidden_path,
        )
        result = self.fixture.run_check(self.powershell, "Full")
        output = self.output(result)
        self.assertEqual(result.returncode, 1, output)
        self.assertIn(
            f"Potential credential pattern found in: {hidden_path}",
            output,
        )
        self.assertNotIn(synthetic_token, output)
        self.assertNotIn("Full validation passed", output)

    def test_full_secret_scan_accepts_the_adr012_filename(self):
        # HAMCP-305: the case-insensitive scan read RISK-AND-ELEVATED-APPROVAL
        # in this filename as an sk- key.
        self.fixture.write(
            "docs/adr012-reference.md",
            "See docs/architecture/ADR-012-POLICY-RISK-AND-ELEVATED-APPROVAL.md\n"
            "and [ADR-012](#adr-012-policy-risk-and-elevated-approval).\n",
        )
        self.fixture.commit("fixture ADR-012 reference")
        result = self.fixture.run_check(self.powershell, "Full")
        output = self.output(result)
        self.assertEqual(result.returncode, 0, output)
        self.assertIn(
            "No bounded high-confidence credential pattern was found.",
            output,
        )

    def test_full_secret_scan_still_rejects_an_sk_key(self):
        synthetic_key = "sk-" + "proj-" + "HAMCP305fixture" + "0" * 24
        self.fixture.write(
            "docs/adr012-reference.md",
            "See docs/architecture/ADR-012-POLICY-RISK-AND-ELEVATED-APPROVAL.md\n",
        )
        self.fixture.write("docs/key-fixture.md", f"token = {synthetic_key}\n")
        self.fixture.commit("fixture sk key")
        result = self.fixture.run_check(self.powershell, "Full")
        output = self.output(result)
        self.assertEqual(result.returncode, 1, output)
        self.assertIn(
            "Potential credential pattern found in: docs/key-fixture.md",
            output,
        )
        self.assertNotIn("adr012-reference.md", output)
        self.assertNotIn(synthetic_key, output)
        self.assertNotIn("Full validation passed", output)

    def sk_pattern(self):
        text = CHECK_SCRIPT.read_text(encoding="utf-8")
        block = text[text.index("function Test-SecretPatterns"):]
        block = block[:block.index("$matchedFiles")]
        patterns = [
            line.strip().rstrip(",")[1:-1]
            for line in block.splitlines()
            if line.strip().startswith("'") and "sk-" in line
        ]
        self.assertEqual(len(patterns), 1, block)
        return patterns[0]

    @staticmethod
    def sk_pattern_table():
        key = "sk-" + "HAMCP305" + "fixture" + "0123456789"
        return {
            # A key at a token boundary, in the shapes keys are issued in.
            key: True,
            f"token={key}": True,
            f'"{key}"': True,
            f"Authorization: {key}": True,
            "sk-" + "proj-" + "abc" * 6 + "9": True,
            "sk-" + "ant-api03-" + "x" * 16 + "Q": True,
            "sk-" + "a1" * 10: True,
            "sk-" + "A" * 20: True,
            # HAMCP-305 false positives and other near misses.
            "ADR-012-POLICY-RISK-AND-ELEVATED-APPROVAL.md": False,
            "adr-012-policy-risk-and-elevated-approval": False,
            "SK-" + "HAMCP305" + "FIXTURE" + "0123456789": False,
            "Sk-" + "HAMCP305" + "fixture" + "0123456789": False,
            "disk-" + "HAMCP305" + "fixture" + "0123456789": False,
            "task-" + "and-elevated-approval-2": False,
            "sk-" + "and-elevated-approval-policy": False,
            "sk-" + "a1" * 9 + "a": False,
        }

    def test_sk_pattern_table(self):
        # The exact pattern from check.ps1 under PowerShell's own -match,
        # which is case-insensitive unless the pattern says otherwise.
        matches = self.sk_pattern_table()
        samples = list(matches)
        payload = json.dumps({"pattern": self.sk_pattern(), "samples": samples})
        script = (
            "$in = [Console]::In.ReadToEnd() | ConvertFrom-Json; "
            "foreach ($s in $in.samples) { "
            "if ($s -match $in.pattern) { 'True' } else { 'False' } }"
        )
        result = subprocess.run(
            [self.powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            input=payload,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = result.stdout.split()
        self.assertEqual(len(observed), len(samples), result.stdout)
        for sample, outcome in zip(samples, observed):
            with self.subTest(sample=sample):
                self.assertEqual(outcome == "True", matches[sample])

    def test_sk_pattern_table_in_python_re(self):
        # The committed-file scan tests (tests/hamcp162_synthetic.py) compile
        # the check.ps1 patterns with Python re.IGNORECASE. A .NET-only
        # construct such as a global (?-i) does not parse there.
        pattern = re.compile(self.sk_pattern(), re.IGNORECASE)
        for sample, expected in self.sk_pattern_table().items():
            with self.subTest(sample=sample):
                self.assertEqual(bool(pattern.search(sample)), expected)

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
            ".github/codex/review-schema.json",
            ".coderabbit.yaml",
        )
        self.fixture.write(protected_paths[0], "VALUE = 2\n")
        self.fixture.write(
            protected_paths[1],
            "name: Updated fixture CI\n"
            "on: [push]\n"
            "jobs:\n"
            "  validate:\n"
            "    runs-on: ubuntu-latest\n",
        )
        self.fixture.write(protected_paths[2], "{}\n")
        self.fixture.write(protected_paths[3], "reviews: {}\n")
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

    def evidence_payload(self) -> dict:
        return json.loads(
            (self.fixture.root / ".artifacts" / "validation.json").read_text(encoding="utf-8")
        )

    @staticmethod
    def unittest_step(payload: dict) -> dict:
        return next(step for step in payload["steps"] if step["name"] == "Run complete unittest suite")

    def test_default_evidence_is_unchanged_single_process_and_recorded(self):
        result = self.fixture.run_check(self.powershell, "Evidence")
        output = self.output(result)
        self.assertEqual(result.returncode, 0, output)
        payload = self.evidence_payload()
        self.assertEqual(
            payload["unit_test_execution"],
            {"mode": "single_process", "sharded": False, "workers": 1, "executed": True},
        )
        step = self.unittest_step(payload)
        self.assertTrue(step["command"].endswith(" -m unittest discover -s tests -v"), step["command"])
        self.assertEqual((step["status"], step["test_count"], step["note"]), ("passed", 4, "skipped_tests=1"))
        self.assertNotIn("local_unit_test_shards", output)
        self.assertFalse((self.fixture.root / ".artifacts" / "unit-test-shards").exists())
        summary = (self.fixture.root / ".artifacts" / "validation.md").read_text(encoding="utf-8")
        self.assertIn("- Unit test execution: single process\n", summary)

    def test_parallel_evidence_binds_the_snapshot_and_matches_single_process_totals(self):
        serial = self.fixture.run_check(self.powershell, "Evidence")
        self.assertEqual(serial.returncode, 0, self.output(serial))
        serial_payload = self.evidence_payload()
        result = self.fixture.run_check(self.powershell, "Evidence", parallel="2")
        output = self.output(result)
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("Evidence validation passed", output)
        payload = self.evidence_payload()
        head = git(self.fixture.root, "rev-parse", "HEAD")
        self.assertEqual((payload["head_sha"], payload["working_tree"]), (head, "clean"))
        self.assertEqual(payload["base_sha"], serial_payload["base_sha"])
        self.assertEqual(payload["schema_version"], 2)
        execution = payload["unit_test_execution"]
        self.assertEqual(
            {key: execution[key] for key in ("mode", "sharded", "workers", "executed", "result")},
            {"mode": "sharded", "sharded": True, "workers": 2, "executed": True, "result": "passed"},
        )
        self.assertEqual(
            (execution["shard_count"], execution["general_shards"], execution["serial_shards"]), (3, 2, [2])
        )
        self.assertEqual(execution["source_sha"], head)
        self.assertEqual((execution["discovered_count"], execution["tests_run"], execution["skipped"]), (4, 4, 1))
        self.assertRegex(execution["run_id"], r"^[1-9][0-9]{18}$")
        summary_file = self.fixture.root / execution["summary"]
        self.assertEqual(json.loads(summary_file.read_text(encoding="utf-8"))["run_id"], execution["run_id"])
        step, serial_step = self.unittest_step(payload), self.unittest_step(serial_payload)
        self.assertIn("local_unit_test_shards.py run --workers 2 --output-dir", step["command"])
        self.assertEqual(step["test_count"], serial_step["test_count"])
        self.assertTrue(step["note"].startswith(serial_step["note"] + "; sharded workers=2"), step["note"])
        self.assertEqual(
            [s["name"] for s in payload["steps"]], [s["name"] for s in serial_payload["steps"]]
        )
        markdown = (self.fixture.root / ".artifacts" / "validation.md").read_text(encoding="utf-8")
        self.assertIn("- Unit test execution: sharded, 2 worker(s), 3 shards", markdown)
        # The bounded PR-draft generator still trusts the sharded receipt.
        draft = self.fixture.root / ".artifacts" / "pr.md"
        run([sys.executable, str(self.fixture.root / "scripts" / "pr-evidence.py"), "--repo-root",
             str(self.fixture.root), "--base", "origin/main", "--head", "HEAD", "--output", str(draft)],
            cwd=self.fixture.root)
        text = draft.read_text(encoding="utf-8")
        self.assertNotIn("not accepted", text)
        self.assertIn("Overall local status: **passed**", text)

    def test_parallel_test_failure_fails_the_tier_with_the_shard_output(self):
        self.fixture.write(".fixture-test-failure", "fail\n")
        result = self.fixture.run_check(self.powershell, "Full", parallel="2")
        output = self.output(result)
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("Sharded unittest run FAILED", output)
        self.assertIn("synthetic fixture test failure", output)
        self.assertRegex(output, r"----- shard \d output")
        self.assertIn("Sharded unittest step failed", output)
        self.assertNotIn("Full validation passed", output)

    def test_parallel_completeness_only_failure_names_the_test_and_shows_its_shard(self):
        # Every worker exits 0 with an OK summary, but one assigned test never started.
        self.fixture.write(".fixture-unstarted", "skip run\n")
        result = self.fixture.run_check(self.powershell, "Full", parallel="2")
        output = self.output(result)
        self.assertNotEqual(result.returncode, 0, output)
        self.assertRegex(
            output,
            r"Completeness check FAILED \(completeness_refused:shard_execution_incomplete_or_extra\): "
            r"shard\(s\) 2 missing: test_shard_mix\.MixTests\.test_plain",
        )
        self.assertIn("----- shard 2 output (completeness_missing) -----", output)
        self.assertIn("fixture-unstarted-diagnostic test_shard_mix.MixTests.test_plain", output)
        self.assertNotRegex(output, r"(?m)^Shard \d+ \([a-z_]+\): passed")
        self.assertNotIn("Completeness proven", output)
        self.assertIn("sharded summary result is failed", output)
        self.assertNotIn("Full validation passed", output)

    def test_parallel_evidence_failure_is_recorded_as_failed(self):
        self.fixture.write(".fixture-test-failure", "fail\n")
        self.fixture.commit("fixture failing test")
        result = self.fixture.run_check(self.powershell, "Evidence", parallel="3")
        self.assertNotEqual(result.returncode, 0, self.output(result))
        payload = self.evidence_payload()
        self.assertEqual(payload["overall_status"], "failed")
        step = self.unittest_step(payload)
        self.assertEqual(step["status"], "failed")
        self.assertNotEqual(step["exit_code"], 0)
        self.assertEqual(step["test_count"], "unknown")
        execution = payload["unit_test_execution"]
        self.assertEqual((execution["result"], execution["workers"], execution["tests_run"]), ("failed", 3, None))

    def test_parallel_never_accepts_a_helper_without_a_proven_summary(self):
        forged_summary = (
            "import json, sys\n"
            "from pathlib import Path\n"
            "out = Path(sys.argv[sys.argv.index('--output-dir') + 1])\n"
            "out.mkdir(parents=True, exist_ok=True)\n"
            "(out / 'summary.json').write_text(json.dumps({'result': RESULT, 'mode': 'sharded', 'workers': WORKERS,"
            " 'tests_run': 4, 'skipped': 1, 'discovered_count': 4, 'shard_count': 3, 'general_shards': 2,"
            " 'serial_shards': [2], 'run_id': '1000000000000000000', 'source_sha': 'f' * 40,"
            " 'discovered_sha256': 'x', 'plan_sha256': 'y'}))\n"
            "print('Ran 4 tests in 0.1s')\nprint('OK')\n"
        )
        helpers = {
            "crashed after printing a passing unittest summary": (
                "print('Ran 4 tests in 0.1s')\nprint('OK')\nraise SystemExit(0)\n", "no sharded summary was written"),
            "nonzero exit beside a passing summary": (
                forged_summary.replace("RESULT", "'passed'").replace("WORKERS", "2") + "raise SystemExit(3)\n",
                "sharded run exited 3"),
            "failed summary with exit 0": (
                forged_summary.replace("RESULT", "'failed'").replace("WORKERS", "2"), "sharded summary result is failed"),
            "summary for another worker count": (
                forged_summary.replace("RESULT", "'passed'").replace("WORKERS", "4"),
                "does not match the requested 2 worker(s)"),
        }
        for name, (body, message) in helpers.items():
            with self.subTest(name):
                self.fixture.write("scripts/local_unit_test_shards.py", body)
                result = self.fixture.run_check(self.powershell, "Full", parallel="2")
                output = self.output(result)
                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn(message, output)
                self.assertNotIn("Full validation passed", output)

    def test_parallel_evidence_refuses_a_proof_bound_to_another_head(self):
        body = (
            "import json, sys\n"
            "from pathlib import Path\n"
            "out = Path(sys.argv[sys.argv.index('--output-dir') + 1])\n"
            "out.mkdir(parents=True, exist_ok=True)\n"
            "(out / 'summary.json').write_text(json.dumps({'result': 'passed', 'mode': 'sharded', 'workers': 2,"
            " 'tests_run': 4, 'skipped': 1, 'discovered_count': 4, 'shard_count': 3, 'general_shards': 2,"
            " 'serial_shards': [2], 'run_id': '1000000000000000000', 'source_sha': 'f' * 40,"
            " 'discovered_sha256': 'x', 'plan_sha256': 'y'}))\n"
        )
        self.fixture.write("scripts/local_unit_test_shards.py", body)
        self.fixture.commit("fixture foreign proof")
        result = self.fixture.run_check(self.powershell, "Evidence", parallel="2")
        output = self.output(result)
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("not the Evidence head", output)
        self.assertEqual(self.evidence_payload()["overall_status"], "failed")

    def test_parallel_is_rejected_on_fast_and_outside_the_worker_bound(self):
        cases = (("Fast", "2", "-Parallel is supported only with -Tier Full or -Tier Evidence"),
                 ("Full", "5", "Parallel"), ("Full", "0", "Parallel"))
        for tier, workers, message in cases:
            with self.subTest(tier=tier, workers=workers):
                result = self.fixture.run_check(self.powershell, tier, parallel=workers)
                output = self.output(result)
                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn(message, output)
                self.assertNotIn(REPOSITORY_CODE_SENTINEL, output)
                self.assertNotIn("validation passed", output)

    # HAMCP-365: -ReuseCiRun. A forged scripts/reuse_ci_unit_shards.py stands
    # in for the real helper, so nothing here reads from GitHub. Its
    # FIXTURE_REUSE_MODE selects the summary it writes.
    REUSE_RUN_ID = "123456789"
    REUSE_REPOSITORY = "fixture-owner/fixture-repo"
    REUSE_STEP = "Reuse CI unittest shard results"
    REUSE_HELPER = """import json
import os
import subprocess
import sys
from pathlib import Path

argv = sys.argv[1:]
Path(".artifacts").mkdir(exist_ok=True)
Path(".artifacts/fixture-reuse-argv.json").write_text(json.dumps(argv))
mode = os.environ.get("FIXTURE_REUSE_MODE", "passed")


def value(name):
    return argv[argv.index(name) + 1]


def rev(name):
    return subprocess.run(["git", "rev-parse", name], check=True, capture_output=True, text=True).stdout.strip()


summary = {
    "schema_version": 1, "mode": "reused_ci", "result": "passed", "problems": [],
    "repository": value("--repository"), "run_id": int(value("--run-id")), "run_attempt": 2,
    "run_url": "https://github.com/fixture/actions/runs/1/attempts/2", "event": "pull_request",
    "head_sha": rev("HEAD"), "source_tree": rev("HEAD^{tree}"), "ci_source_sha": "c" * 40,
    "shard_count": 11, "general_shards": 10, "serial_shards": [10],
    "discovered_count": 7, "discovered_sha256": "d" * 64, "plan_sha256": "e" * 64,
    "outcomes": {"success": 6, "skipped": 1}, "executed_tests": 7, "skipped": 1,
    "python_versions": ["3.12.14"], "artifacts": [],
}
if mode == "foreign-head":
    summary["head_sha"] = rev("origin/main")
elif mode == "foreign-tree":
    summary["source_tree"] = rev("origin/main^{tree}")
elif mode == "other-run":
    summary["run_id"] += 1
elif mode == "other-repository":
    summary["repository"] = "fixture-owner/other-repo"
elif mode == "upper-case-head":
    summary["head_sha"] = summary["head_sha"].upper()
elif mode == "failed-result":
    summary.update(result="failed", problems=["fixture refusal"])
elif mode == "missing-field":
    del summary["executed_tests"]
elif mode == "short-totals":
    summary["executed_tests"] = 6
if mode != "no-summary":
    output = Path(value("--output-dir"))
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(summary))
print("Summary: fixture reuse " + mode)
raise SystemExit(3 if mode == "nonzero-exit" else 0)
"""
    # Any unit-suite run imports this module and leaves the marker behind.
    REUSE_TRIPWIRE = """from pathlib import Path

Path(".artifacts").mkdir(exist_ok=True)
Path(".artifacts/fixture-unit-suite-imported").write_text("imported")
"""

    def prepare_reuse(self, *, origin: bool = True) -> None:
        self.fixture.write("scripts/reuse_ci_unit_shards.py", self.REUSE_HELPER)
        self.fixture.write("tests/test_reuse_tripwire.py", self.REUSE_TRIPWIRE)
        self.fixture.commit("fixture reuse helper")
        if origin:
            git(self.fixture.root, "config", "remote.origin.url", f"https://github.com/{self.REUSE_REPOSITORY}.git")

    def run_reuse(self, mode: str = "passed") -> subprocess.CompletedProcess[str]:
        # Each run starts without a receipt, record or marker from an earlier one.
        artifacts = self.fixture.root / ".artifacts"
        shutil.rmtree(artifacts / "unit-test-shards", ignore_errors=True)
        for name in ("validation.json", "fixture-reuse-argv.json", "fixture-unit-suite-imported"):
            (artifacts / name).unlink(missing_ok=True)
        environment = dict(os.environ, FIXTURE_REUSE_MODE=mode)
        if os.name != "nt":
            # check.ps1 reads nothing from GitHub itself: a gh first on PATH is never run.
            if not hasattr(self, "gh_log"):
                temporary = tempfile.TemporaryDirectory(prefix="codex-reuse-gh-")
                self.addCleanup(temporary.cleanup)
                gh = Path(temporary.name) / "gh"
                gh.write_text('#!/bin/sh\necho "$@" >> "$FIXTURE_GH_LOG"\nexit 1\n', encoding="utf-8")
                gh.chmod(0o755)
                self.gh_log = Path(temporary.name) / "gh.log"
            environment["PATH"] = str(self.gh_log.parent) + os.pathsep + environment.get("PATH", "")
            environment["FIXTURE_GH_LOG"] = str(self.gh_log)
        return self.fixture.run_check(self.powershell, "Evidence", reuse_ci_run=self.REUSE_RUN_ID, env=environment)

    def reuse_step(self, payload: dict) -> dict:
        return next(step for step in payload["steps"] if step["name"] == self.REUSE_STEP)

    def pr_evidence_draft(self) -> str:
        draft = self.fixture.root / ".artifacts" / "pr.md"
        run([sys.executable, str(self.fixture.root / "scripts" / "pr-evidence.py"), "--repo-root",
             str(self.fixture.root), "--base", "origin/main", "--head", "HEAD", "--output", str(draft)],
            cwd=self.fixture.root)
        return draft.read_text(encoding="utf-8")

    def test_reuse_evidence_records_a_bound_ci_result_and_runs_no_unit_test(self):
        self.prepare_reuse()
        result = self.run_reuse()
        output = self.output(result)
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("Evidence validation passed", output)
        self.assertNotRegex(output, r"Ran \d+ tests? in")
        artifacts = self.fixture.root / ".artifacts"
        self.assertFalse((artifacts / "fixture-unit-suite-imported").exists())
        if os.name != "nt":
            self.assertFalse(self.gh_log.exists())
        self.assertEqual(
            json.loads((artifacts / "fixture-reuse-argv.json").read_text(encoding="utf-8")),
            ["--repository", self.REUSE_REPOSITORY, "--run-id", self.REUSE_RUN_ID,
             "--output-dir", ".artifacts/unit-test-shards/latest", "--replace-output"],
        )
        payload = self.evidence_payload()
        head = git(self.fixture.root, "rev-parse", "HEAD")
        tree = git(self.fixture.root, "rev-parse", "HEAD^{tree}")
        execution = payload["unit_test_execution"]
        expected = {"mode": "reused_ci", "sharded": True, "executed": False, "run_id": self.REUSE_RUN_ID,
                    "result": "passed", "run_attempt": 2, "head_sha": head, "source_tree": tree,
                    "shard_count": 11, "executed_tests": 7, "skipped": 1,
                    "summary": ".artifacts/unit-test-shards/latest/summary.json"}
        self.assertEqual({key: execution[key] for key in expected}, expected)
        self.assertNotIn("Run complete unittest suite", [step["name"] for step in payload["steps"]])
        step = self.reuse_step(payload)
        self.assertEqual((step["status"], step["exit_code"], step["test_count"]), ("passed", 0, 7))
        self.assertEqual(
            step["note"],
            f"skipped_tests=1; reused CI run {self.REUSE_RUN_ID} attempt 2 shards=11 python=3.12.14; "
            "no unit test ran locally",
        )
        self.assertEqual(
            [(item["check"], item["status"]) for item in payload["coverage"][:2]],
            [("full_local_gate", "executed_locally_except_unit_suite"), ("unit_test_suite", "reused_from_ci")],
        )
        markdown = (artifacts / "validation.md").read_text(encoding="utf-8")
        self.assertIn(
            f"- Unit test execution: reused from CI run {self.REUSE_RUN_ID} attempt 2 (11 shards, tree {tree}); "
            "not executed locally; result passed\n",
            markdown,
        )
        text = self.pr_evidence_draft()
        self.assertNotIn("not accepted", text)
        self.assertIn("Overall local status: **passed**", text)
        self.assertIn(
            f"Unit tests: **reused from CI run `{self.REUSE_RUN_ID}` attempt `2`** for tree `{tree}`; "
            "no unit test ran locally.\n",
            text,
        )
        self.assertNotIn("Run complete unittest suite", text)
        # The tripwire is live: a real unit-suite run imports it.
        run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=self.fixture.root)
        self.assertTrue((artifacts / "fixture-unit-suite-imported").exists())

    def test_reuse_evidence_fails_closed_on_every_unproven_result(self):
        self.prepare_reuse()
        unbound = f"reuse summary is not bound to CI run {self.REUSE_RUN_ID}, the Evidence head and its tree"
        cases = {
            "foreign-head": unbound,
            "foreign-tree": unbound,
            "other-run": unbound,
            "other-repository": unbound,
            # PowerShell -ne ignores case; the binding compares exactly, as pr-evidence.py does.
            "upper-case-head": unbound,
            "failed-result": "reuse summary result is failed",
            "nonzero-exit": "reuse helper exited 3",
            "no-summary": "no reuse summary was written",
            "missing-field": "'executed_tests'",
            "short-totals": "reuse summary has no proven totals for every discovered test",
        }
        for mode, message in cases.items():
            with self.subTest(mode):
                result = self.run_reuse(mode)
                output = self.output(result)
                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn(f"Summary: fixture reuse {mode}", output)
                self.assertIn(message, output)
                self.assertNotIn("Evidence validation passed", output)
                self.assertFalse((self.fixture.root / ".artifacts" / "fixture-unit-suite-imported").exists())
                payload = self.evidence_payload()
                self.assertEqual(payload["overall_status"], "failed")
                step = self.reuse_step(payload)
                self.assertEqual(step["status"], "failed")
                self.assertNotEqual(step["exit_code"], 0)
                self.assertEqual(step["test_count"], "unknown")
                self.assertIn(message, step["note"])
                execution = payload["unit_test_execution"]
                self.assertEqual(
                    (execution["mode"], execution["executed"], execution["result"]), ("reused_ci", False, "failed")
                )
                self.assertEqual(
                    [(item["check"], item["status"]) for item in payload["coverage"][:2]],
                    [("full_local_gate", "failed_locally"), ("unit_test_suite", "not_reused")],
                )
                if message == unbound:
                    # Binding is checked before anything is copied from the summary.
                    self.assertNotIn("run_attempt", execution)
                if mode == "failed-result":
                    text = self.pr_evidence_draft()
                    self.assertIn("Overall local status: **failed**", text)
                    self.assertIn(
                        f"Unit tests: **not run** - CI run `{self.REUSE_RUN_ID}` was not reused and the unit "
                        "suite was not executed locally.\n",
                        text,
                    )

    def test_reuse_evidence_without_a_github_origin_runs_no_helper(self):
        self.prepare_reuse(origin=False)
        result = self.run_reuse()
        output = self.output(result)
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("origin does not name a github.com OWNER/NAME repository; nothing was read from GitHub", output)
        self.assertFalse((self.fixture.root / ".artifacts" / "fixture-reuse-argv.json").exists())
        payload = self.evidence_payload()
        step = self.reuse_step(payload)
        self.assertEqual(
            (step["status"], step["exit_code"], step["command"]),
            ("failed", 127, "scripts/reuse_ci_unit_shards.py (not run)"),
        )
        self.assertEqual(payload["unit_test_execution"]["result"], "failed")
        self.assertEqual(payload["coverage"][1]["status"], "not_reused")

    def test_reuse_is_rejected_outside_evidence_beside_parallel_and_for_a_malformed_run_id(self):
        cases = [
            ("Fast", self.REUSE_RUN_ID, None, "-ReuseCiRun is supported only with -Tier Evidence."),
            ("Full", self.REUSE_RUN_ID, None, "-ReuseCiRun is supported only with -Tier Evidence."),
            ("Evidence", self.REUSE_RUN_ID, "2", "Use either -ReuseCiRun or -Parallel, not both."),
        ]
        # An empty value is refused too, so it can never fall back to a local run.
        cases += [
            ("Evidence", value, None, "parameter 'ReuseCiRun'")
            for value in ("", "0", "0123", "12a", "1" + "0" * 20, "12\n")
        ]
        for tier, run_id, parallel, message in cases:
            with self.subTest(tier=tier, run_id=run_id, parallel=parallel):
                result = self.fixture.run_check(self.powershell, tier, parallel=parallel, reuse_ci_run=run_id)
                output = self.output(result)
                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn(message, output)
                self.assertNotIn(REPOSITORY_CODE_SENTINEL, output)
                self.assertNotIn("validation passed", output)


@unittest.skipIf(os.name == "nt", "the fake gh and python helpers are POSIX shell scripts")
class CheckScriptDefaultsTests(unittest.TestCase):
    """HAMCP-356: base-reference detection and the worktree venv default.

    Every run uses a PATH that holds only git and the fakes a test writes, so
    the real gh and any real python on PATH are never consulted.

    HAMCP-358: the pull-request lookup names the repository of the fixture's
    ``origin``, which is a github.com URL nothing ever contacts.

    HAMCP-458: with no detected base the run is refused, never compared with
    origin/main by default; and the interpreter must match
    tests/requirements.lock before any repository code runs.
    """

    BRANCH = "agent/HAMCP-1-fixture"
    REPOSITORY = "fixture-owner/fixture-repo"
    GH_FIELDS = "--json number,state,baseRefName,headRefName,isCrossRepository,url"
    GH_CALL = f"pr view {BRANCH} --repo github.com/{REPOSITORY} {GH_FIELDS}"
    NOT_GITHUB = "origin is not a github.com repository URL this script can name"

    @classmethod
    def setUpClass(cls) -> None:
        cls.powershell = shutil.which("pwsh") or shutil.which("powershell")
        cls.git = shutil.which("git")
        cls.sleep = shutil.which("sleep")

    def setUp(self) -> None:
        if not self.powershell or not self.git:
            self.skipTest("PowerShell or git is unavailable")
        self.fixture = CheckScriptRepositoryFixture()
        self.bin = self.fixture.root.parent / (self.fixture.root.name + "-bin")
        self.bin.mkdir()
        self.addCleanup(shutil.rmtree, self.bin, True)
        (self.bin / "git").symlink_to(self.git)
        self.gh_log = self.bin / "gh.log"
        self.gh_environment_log = self.bin / "gh-environment.log"
        self.set_origin(f"https://github.com/{self.REPOSITORY}.git")
        # A release base that already carries a protected change. Compared
        # with origin/main the branch changes a protected path; compared with
        # its release base it does not.
        self.fixture.write("hass_mcp_admin/example.py", "VALUE = 2\n")
        self.fixture.commit("fixture release change")
        git(self.fixture.root, "update-ref", "refs/remotes/origin/release/9.9.0", "HEAD")
        git(self.fixture.root, "checkout", "-q", "-b", self.BRANCH)
        self.fixture.write("docs.md", "branch change\n")
        self.fixture.commit("fixture branch change")

    def tearDown(self) -> None:
        if hasattr(self, "fixture"):
            self.fixture.close()

    def environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PATH"] = str(self.bin)
        return env

    def write_executable(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def write_gh(self, *, stdout: str = "", exit_code: int = 0, stderr: str = "", sleep: bool = False) -> None:
        script = "#!/bin/sh\n" + f"printf '%s\\n' \"$*\" >> '{self.gh_log}'\n"
        script += f"printf '%s\\n' \"${{GH_REPO:-unset}}\" >> '{self.gh_environment_log}'\n"
        if sleep:
            script += f"exec '{self.sleep}' 30\n"
        script += f"printf '%s' '{stdout}'\n"
        if stderr:
            script += f"printf '%s\\n' '{stderr}' >&2\n"
        script += f"exit {exit_code}\n"
        self.write_executable(self.bin / "gh", script)

    def write_pull_request(self, **overrides) -> None:
        answer = {"number": 42, "state": "OPEN", "baseRefName": "release/9.9.0",
                  "headRefName": self.BRANCH, "isCrossRepository": False,
                  "url": f"https://github.com/{self.REPOSITORY}/pull/42"}
        answer.update(overrides)
        self.write_gh(stdout=json.dumps({name: value for name, value in answer.items() if value is not ABSENT}))

    def set_origin(self, url: str | None) -> None:
        # Only the configured URL is read; no test contacts it.
        if url is None:
            run(["git", "config", "--unset-all", "remote.origin.url"], cwd=self.fixture.root, check=False)
        else:
            git(self.fixture.root, "config", "remote.origin.url", url)

    def gh_calls(self) -> list[str]:
        if not self.gh_log.exists():
            return []
        return self.gh_log.read_text(encoding="utf-8").splitlines()

    def check(self, tier: str = "Fast", **arguments) -> tuple[int, str]:
        arguments.setdefault("base_ref", None)
        result = self.fixture.run_check(self.powershell, tier, env=self.environment(), **arguments)
        return result.returncode, result.stdout + result.stderr

    def assert_compared_with_main(self, code: int, output: str) -> None:
        self.assertNotEqual(code, 0, output)
        self.assertIn("hass_mcp_admin/example.py", output)
        self.assertIn("AuthorizedProtectedPath", output)
        self.assertNotIn(REPOSITORY_CODE_SENTINEL, output)

    def assert_base_refused(self, code: int, output: str, reason: str) -> None:
        # HAMCP-458: with no detected base, check.ps1 refuses; it no longer
        # compares with origin/main by default.
        self.assertNotEqual(code, 0, output)
        self.assertIn(f"No pull-request base was detected because {reason}", output)
        self.assertIn("Refusing to choose a base: pass -BaseRef with the pull request's base", output)
        self.assertIn("Validation stopped during safe preflight", output)
        self.assertNotIn("Base reference origin/main", output)
        self.assertNotIn("Verified base commit", output)
        self.assertNotIn(REPOSITORY_CODE_SENTINEL, output)

    def test_explicit_base_ref_wins_and_gh_is_not_consulted(self):
        self.write_pull_request(baseRefName="main")
        code, output = self.check(base_ref="origin/release/9.9.0")
        self.assertEqual(code, 0, output)
        self.assertIn("Base reference origin/release/9.9.0 (explicit -BaseRef).", output)
        self.assertEqual(self.gh_calls(), [])
        code, output = self.check(base_ref="origin/main")
        self.assert_compared_with_main(code, output)
        self.assertEqual(self.gh_calls(), [])

    def test_empty_explicit_base_ref_is_refused(self):
        code, output = self.check(base_ref="")
        self.assertNotEqual(code, 0, output)
        self.assertIn("-BaseRef must name a Git reference", output)
        self.assertNotIn(REPOSITORY_CODE_SENTINEL, output)
        self.assertEqual(self.gh_calls(), [])

    def test_pull_request_release_base_is_detected_for_the_protected_path_preflight(self):
        self.write_pull_request()
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertIn(f"Base reference origin/release/9.9.0 (detected: base of pull request #42 in {self.REPOSITORY}; "
                      "pass -BaseRef to override).", output)
        self.assertIn("Fast validation passed", output)
        self.assertEqual(self.gh_calls(), [self.GH_CALL])

    def test_pull_request_lookup_ignores_an_inherited_gh_repo(self):
        # HAMCP-358: GH_REPO would point gh at another repository, whose pull
        # request for a branch of this name would supply the base.
        self.write_pull_request()
        env = self.environment()
        env["GH_REPO"] = "other-owner/other-repo"
        result = self.fixture.run_check(self.powershell, "Fast", env=env, base_ref=None)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn(f"detected: base of pull request #42 in {self.REPOSITORY};", output)
        self.assertEqual(self.gh_calls(), [self.GH_CALL])
        self.assertEqual(self.gh_environment_log.read_text(encoding="utf-8").splitlines(), ["unset"])
        self.assertNotIn("other-owner", output)

    def test_answer_for_another_repository_fails_closed(self):
        ours = f"https://github.com/{self.REPOSITORY}/pull/42"
        cases = (
            ("https://github.com/other-owner/other-repo/pull/42", "which is not a pull request of"),
            (f"https://github.com/{self.REPOSITORY}-fork/pull/42", "which is not a pull request of"),
            (f"https://github.com/{self.REPOSITORY}/pull/43", "which is not a pull request of"),
            (ours + "/files", "which is not a pull request of"),
            (ours.replace("github.com", "github.com.example.invalid"), "which is not a pull request of"),
            (ours.replace("https://", "http://"), "which is not a pull request of"),
            ("", "which is not a pull request of"),
            (7, "which is not a pull request of"),
            (None, "which is not a pull request of"),
            ([ours], "which is not a pull request of"),
            (ABSENT, "did not report url"),
        )
        for url, message in cases:
            with self.subTest(url=url):
                self.write_pull_request(url=url)
                code, output = self.check()
                self.assertNotEqual(code, 0, output)
                self.assertIn(message, output)
                self.assertIn("refusing to choose a base. Pass -BaseRef.", output)
                self.assertIn("Validation stopped during safe preflight", output)
                self.assertNotIn("Base reference origin/release/9.9.0", output)
                self.assertNotIn(REPOSITORY_CODE_SENTINEL, output)

    def test_github_origin_forms_name_the_same_repository(self):
        for url in (f"https://github.com/{self.REPOSITORY}",
                    f"https://github.com/{self.REPOSITORY}/",
                    f"git@github.com:{self.REPOSITORY}.git",
                    f"ssh://git@github.com/{self.REPOSITORY}.git"):
            with self.subTest(url=url):
                self.gh_log.unlink(missing_ok=True)
                self.set_origin(url)
                self.write_pull_request()
                code, output = self.check()
                self.assertEqual(code, 0, output)
                self.assertIn(f"detected: base of pull request #42 in {self.REPOSITORY};", output)
                self.assertEqual(self.gh_calls(), [self.GH_CALL])

    def test_origin_that_is_not_a_github_repository_skips_gh(self):
        marker = "fixture-origin-userinfo"
        for url in (None,
                    str(self.bin / "no-such-remote"),
                    f"https://gitlab.com/{self.REPOSITORY}.git",
                    f"https://github.com.example.invalid/{self.REPOSITORY}.git",
                    f"https://{marker}@github.com/{self.REPOSITORY}.git",
                    f"https://github.com/{self.REPOSITORY}/extra",
                    "https://github.com/fixture-owner/..",
                    "https://github.com/fixture-owner/fixture repo",
                    f"https://github.com/{self.REPOSITORY}.git --repo other-owner/other-repo"):
            with self.subTest(url=url):
                self.set_origin(url)
                self.write_pull_request()
                code, output = self.check()
                self.assert_base_refused(code, output, f"{self.NOT_GITHUB}; the branch has no upstream")
                self.assertEqual(self.gh_calls(), [])
                self.assertNotIn(marker, output)

    def test_detected_base_is_the_recorded_evidence_base(self):
        self.write_pull_request()
        code, output = self.check("Evidence")
        self.assertEqual(code, 0, output)
        payload = json.loads((self.fixture.root / ".artifacts" / "validation.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["base_ref"], "origin/release/9.9.0")
        self.assertEqual(payload["base_sha"], git(self.fixture.root, "rev-parse", "origin/release/9.9.0"))
        self.assertEqual(payload["overall_status"], "passed")

    def test_missing_gh_and_no_upstream_is_refused(self):
        code, output = self.check()
        self.assert_base_refused(code, output, "gh is not on PATH; the branch has no upstream")

    def test_unauthenticated_gh_is_refused_without_printing_gh_output(self):
        self.write_gh(exit_code=4, stdout="fixture-gh-stdout", stderr="fixture-gh-stderr-detail")
        code, output = self.check()
        self.assert_base_refused(code, output, f"gh pr view {self.BRANCH} failed with exit code 4")
        self.assertNotIn("fixture-gh-stderr-detail", output)
        self.assertNotIn("fixture-gh-stdout", output)
        self.assertEqual(len(self.gh_calls()), 1)

    def test_gh_that_does_not_answer_is_abandoned_and_refused(self):
        if not self.sleep:
            self.skipTest("sleep is unavailable")
        self.write_gh(sleep=True)
        code, output = self.check()
        self.assert_base_refused(code, output, "gh pr view did not answer within 10 s")

    def test_closed_or_merged_pull_request_is_not_used(self):
        for state in ("MERGED", "CLOSED"):
            with self.subTest(state=state):
                self.write_pull_request(state=state)
                code, output = self.check()
                self.assert_base_refused(code, output, f"the pull request for {self.BRANCH} is not open")

    def test_pull_request_base_that_is_not_fetched_fails_closed(self):
        self.write_pull_request(baseRefName="release/8.0.0")
        code, output = self.check()
        self.assertNotEqual(code, 0, output)
        self.assertIn(f"The base of pull request #42 in {self.REPOSITORY} is origin/release/8.0.0, "
                      "which is not available locally. "
                      "Fetch it (git fetch origin) or pass -BaseRef.", output)
        self.assertIn("Validation stopped during safe preflight", output)
        self.assertNotIn(REPOSITORY_CODE_SENTINEL, output)

    def test_inconsistent_or_malformed_gh_answers_fail_closed(self):
        cases = (
            ({"headRefName": "agent/other"}, "which is not this repository's branch"),
            ({"isCrossRepository": True}, "which is not this repository's branch"),
            ({"isCrossRepository": "false"}, "which is not this repository's branch"),
            ({"baseRefName": "-release"}, "unusable base branch"),
            ({"baseRefName": "release/../main"}, "unusable base branch"),
            ({"baseRefName": "main;x"}, "unusable base branch"),
            ({"baseRefName": 7}, "unusable base branch"),
            ({"number": "42"}, "invalid pull-request number"),
            ({"number": 0}, "invalid pull-request number"),
            ({"state": 1}, "unusable pull-request state"),
        )
        for override, message in cases:
            with self.subTest(override=override):
                self.write_pull_request(**override)
                code, output = self.check()
                self.assertNotEqual(code, 0, output)
                self.assertIn(message, output)
                self.assertIn("refusing to choose a base. Pass -BaseRef.", output)
                self.assertIn("Validation stopped during safe preflight", output)
                self.assertNotIn(REPOSITORY_CODE_SENTINEL, output)
        for stdout, message in (("not json", "not JSON"), ("{}", "did not report number"),
                                ("[]", "did not report number"), ("{" + " " * 5000 + "}", "more than 4096")):
            with self.subTest(stdout=stdout[:12]):
                self.write_gh(stdout=stdout)
                code, output = self.check()
                self.assertNotEqual(code, 0, output)
                self.assertIn(message, output)
                self.assertNotIn(REPOSITORY_CODE_SENTINEL, output)

    def set_upstream(self, upstream: str) -> None:
        # A local remote definition so git can record tracking; nothing is fetched.
        self.set_origin(str(self.bin / "no-such-remote"))
        git(self.fixture.root, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
        git(self.fixture.root, "branch", "--set-upstream-to", upstream)

    def test_release_upstream_is_the_offline_fallback(self):
        self.set_upstream("origin/release/9.9.0")
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertIn(f"Base reference origin/release/9.9.0 (detected: upstream of branch {self.BRANCH}; "
                      "pass -BaseRef to override).", output)

    def test_own_or_unrelated_upstream_is_ignored(self):
        own = f"refs/remotes/origin/{self.BRANCH}"
        git(self.fixture.root, "update-ref", own, "HEAD")
        self.set_upstream(f"origin/{self.BRANCH}")
        code, output = self.check()
        self.assert_base_refused(code, output, f"gh is not on PATH; the branch upstream origin/{self.BRANCH} "
                                               "is not origin/main or an origin/release/* branch")
        git(self.fixture.root, "checkout", "-q", "-b", "release/9.9.0")
        self.set_upstream("origin/release/9.9.0")
        code, output = self.check()
        self.assert_base_refused(code, output, "gh is not on PATH; the branch upstream origin/release/9.9.0 "
                                               "is the branch's own remote copy")

    def test_detached_head_is_refused_without_consulting_gh(self):
        self.write_pull_request()
        git(self.fixture.root, "checkout", "-q", "--detach")
        code, output = self.check()
        self.assert_base_refused(code, output, "HEAD is detached")
        self.assertEqual(self.gh_calls(), [])

    def venv_python(self) -> Path:
        return self.fixture.root / ".artifacts" / "venv" / "bin" / "python"

    @unittest.skipUnless(sys.version_info[:2] == (3, 12), "the venv default requires Python 3.12")
    def test_worktree_venv_python_is_used_when_python_is_absent(self):
        self.write_executable(self.venv_python(), f"#!/bin/sh\nexec '{sys.executable}' \"$@\"\n")
        code, output = self.check(python_executable=None, base_ref="origin/release/9.9.0")
        self.assertEqual(code, 0, output)
        self.assertIn("No -PythonExecutable and no python on PATH: using the worktree venv Python 3.12 at "
                      f"{self.venv_python()}.", output)
        self.assertIn(REPOSITORY_CODE_SENTINEL, output)
        self.assertIn("Fast validation passed", output)

    def test_worktree_venv_with_another_python_version_is_refused(self):
        for answer, observed in (("3.11", "is Python 3.11, not Python 3.12"),
                                 ("3.12.1", "did not report its version"),
                                 ("", "did not report its version")):
            with self.subTest(answer=answer):
                self.write_executable(self.venv_python(), f"#!/bin/sh\nprintf '%s\\n' '{answer}'\n")
                code, output = self.check(python_executable=None, base_ref="origin/release/9.9.0")
                self.assertNotEqual(code, 0, output)
                self.assertIn(observed, output)
                self.assertIn("Rebuild .artifacts/venv with Python 3.12 or provide -PythonExecutable.", output)
                self.assertNotIn(REPOSITORY_CODE_SENTINEL, output)
        self.write_executable(self.venv_python(), "#!/bin/sh\nexit 3\n")
        code, output = self.check(python_executable=None, base_ref="origin/release/9.9.0")
        self.assertNotEqual(code, 0, output)
        self.assertIn("did not report its version", output)

    def test_missing_worktree_venv_is_reported(self):
        code, output = self.check(python_executable=None, base_ref="origin/release/9.9.0")
        self.assertNotEqual(code, 0, output)
        self.assertIn("Python was not found on PATH and the worktree venv", output)
        self.assertIn("Provide -PythonExecutable", output)
        self.assertNotIn(REPOSITORY_CODE_SENTINEL, output)

    def test_explicit_or_path_python_wins_over_the_venv(self):
        self.write_executable(self.venv_python(), "#!/bin/sh\nprintf '%s\\n' 3.11\n")
        code, output = self.check(base_ref="origin/release/9.9.0")
        self.assertEqual(code, 0, output)
        self.assertNotIn("worktree venv", output)
        self.write_executable(self.bin / "python", f"#!/bin/sh\nexec '{sys.executable}' \"$@\"\n")
        code, output = self.check(python_executable=None, base_ref="origin/release/9.9.0")
        self.assertEqual(code, 0, output)
        self.assertNotIn("worktree venv", output)

    LOCK_MISMATCH = "does not match tests/requirements.lock"
    LOCK_REBUILD = ("Rebuild .artifacts/venv from the lock (docs/CODEX_WORKFLOW.md, Sandbox-local scratch) "
                    "or pass -PythonExecutable with a matching environment.")

    def write_lock(self, text: str | None) -> None:
        path = self.fixture.root / "tests" / "requirements.lock"
        if text is None:
            path.unlink()
        else:
            path.write_text(text, encoding="utf-8", newline="\n")

    def assert_lock_refused(self, code: int, output: str, message: str) -> None:
        self.assertNotEqual(code, 0, output)
        self.assertIn(message, output)
        # Refused with the interpreter, before any step or repository code.
        self.assertIn("Validation stopped during safe preflight", output)
        self.assertNotIn("Verify base reference", output)
        self.assertNotIn(REPOSITORY_CODE_SENTINEL, output)

    def test_environment_that_matches_the_lock_passes(self):
        for lock in (FIXTURE_LOCK,
                     f"# comment\n\nPyYAML=={FIXTURE_PYYAML_VERSION}\n",
                     FIXTURE_LOCK.replace("pyyaml==", "PYYAML==")):
            with self.subTest(lock=lock):
                self.write_lock(lock)
                code, output = self.check(base_ref="origin/release/9.9.0")
                self.assertEqual(code, 0, output)
                self.assertIn(f"The Python environment at {sys.executable} matches all 1 pins in "
                              "tests/requirements.lock.", output)
                self.assertIn(REPOSITORY_CODE_SENTINEL, output)
                self.assertIn("Fast validation passed", output)

    def test_environment_that_differs_from_the_lock_is_refused(self):
        cases = (
            ("pyyaml==0.0.1 \\\n    --hash=sha256:" + "0" * 64 + "\n",
             f"(1 of 1 pins differ): pyyaml==0.0.1 (installed {FIXTURE_PYYAML_VERSION})."),
            (FIXTURE_LOCK + "fixture-absent-distribution==1.0\n",
             "(1 of 2 pins differ): fixture-absent-distribution==1.0 (not installed)."),
        )
        for lock, message in cases:
            with self.subTest(message=message):
                self.write_lock(lock)
                code, output = self.check(base_ref="origin/release/9.9.0")
                self.assert_lock_refused(code, output, f"The Python environment at {sys.executable} "
                                                       f"{self.LOCK_MISMATCH} {message}")
                self.assertIn(self.LOCK_REBUILD, output)

    def test_many_differences_are_bounded_in_the_message(self):
        self.write_lock("".join(f"fixture-absent-{number:02d}==1.0\n" for number in range(12)))
        code, output = self.check(base_ref="origin/release/9.9.0")
        self.assert_lock_refused(code, output, f"{self.LOCK_MISMATCH} (12 of 12 pins differ): "
                                               "fixture-absent-00==1.0 (not installed);")
        self.assertIn("fixture-absent-09==1.0 (not installed); and 2 more.", output)
        self.assertNotIn("fixture-absent-10", output)

    def test_missing_or_unusable_lock_is_refused(self):
        cases = (
            (None, "tests/requirements.lock does not exist; refusing to validate without the locked test "
                   "environment."),
            ("pyyaml>=6\n", "tests/requirements.lock line 1 is not a name==version pin; refusing"),
            ("-r other.txt\n", "tests/requirements.lock line 1 is not a name==version pin; refusing"),
            (f"pyyaml=={FIXTURE_PYYAML_VERSION} ; python_version >= '3'\n",
             "tests/requirements.lock line 1 is not a name==version pin; refusing"),
            (f"pyyaml=={FIXTURE_PYYAML_VERSION}\nPyYAML=={FIXTURE_PYYAML_VERSION}\n",
             "tests/requirements.lock pins pyyaml more than once (line 2); refusing"),
            ("", "tests/requirements.lock pins no distributions; refusing"),
            ("# only comments\n\n    --hash=sha256:" + "0" * 64 + "\n",
             "tests/requirements.lock pins no distributions; refusing"),
        )
        for lock, message in cases:
            with self.subTest(lock=lock):
                self.write_lock(lock)
                code, output = self.check(base_ref="origin/release/9.9.0")
                self.assert_lock_refused(code, output, message)

    def test_interpreter_that_cannot_list_its_distributions_is_refused(self):
        fake = self.bin / "fake-python"
        for script in ("printf '%s\\n' 'not json'\n",
                       "printf '%s\\n' '[]'\n",
                       "printf '%s\\n' '{}' '{}'\n",
                       "printf '%s\\n' '{\"pyyaml\": \"6\"}'\nexit 3\n",
                       "exit 0\n"):
            with self.subTest(script=script):
                self.write_executable(fake, "#!/bin/sh\n" + script)
                code, output = self.check(base_ref="origin/release/9.9.0", python_executable=str(fake))
                self.assert_lock_refused(code, output, f"{fake} did not list its installed distributions; "
                                                       "refusing to validate without the locked test environment.")
        code, output = self.check(base_ref="origin/release/9.9.0", python_executable=str(self.bin / "absent"))
        self.assert_lock_refused(code, output, "did not list its installed distributions")

    def test_unreadable_installed_version_is_refused(self):
        fake = self.bin / "fake-python"
        for answer in ('{"pyyaml": 7}', '{"pyyaml": null}', '{"pyyaml": "6.0.2; rm -rf /"}'):
            with self.subTest(answer=answer):
                self.write_executable(fake, f"#!/bin/sh\nprintf '%s\\n' '{answer}'\n")
                code, output = self.check(base_ref="origin/release/9.9.0", python_executable=str(fake))
                self.assert_lock_refused(code, output, f"pyyaml=={FIXTURE_PYYAML_VERSION} (installed with an "
                                                       "unreadable version)")
                self.assertNotIn("rm -rf", output)

    def test_pythonpath_shadow_cannot_change_the_lock_answer(self):
        # R1-2: the interpreter lists its distributions isolated (-I), so a dist-info directory on PYTHONPATH can
        # neither supply a missing pin nor replace the installed version of a pinned one. Without -I every case
        # below gives the opposite answer.
        absent = "fixture-shadow-distribution"
        cases = (
            ("missing pin", (absent, "1.0"), FIXTURE_LOCK + f"{absent}==1.0\n",
             f"(1 of 2 pins differ): {absent}==1.0 (not installed)."),
            ("replaced version", ("PyYAML", "0.0.1"), "pyyaml==0.0.1\n",
             f"(1 of 1 pins differ): pyyaml==0.0.1 (installed {FIXTURE_PYYAML_VERSION})."),
            ("installed version kept", ("PyYAML", "0.0.1"), FIXTURE_LOCK, None),
        )
        for label, (name, version), lock, message in cases:
            with self.subTest(label):
                shadow = self.bin / ("shadow-" + label.replace(" ", "-"))
                info = shadow / f"{name.replace('-', '_')}-{version}.dist-info"
                info.mkdir(parents=True)
                (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
                                               encoding="utf-8")
                self.write_lock(lock)
                env = self.environment()
                env["PYTHONPATH"] = str(shadow)
                result = self.fixture.run_check(self.powershell, "Fast", env=env, base_ref="origin/release/9.9.0")
                code, output = result.returncode, result.stdout + result.stderr
                if message is None:
                    self.assertEqual(code, 0, output)
                    self.assertIn(f"The Python environment at {sys.executable} matches all 1 pins in "
                                  "tests/requirements.lock.", output)
                else:
                    self.assert_lock_refused(code, output, f"The Python environment at {sys.executable} "
                                                           f"{self.LOCK_MISMATCH} {message}")


class CheckScriptPinnedBaseTests(unittest.TestCase):
    """HAMCP-356: one pinned base commit, and a separate version-check base.

    The pull-request base is resolved to a commit once, at the start, and
    every comparison in the run uses that commit, so a fetch elsewhere that
    moves the reference cannot change the result. The deployment-metadata
    version check compares against origin/main, as CI does, never against a
    release base where item pull requests share the declared version.
    """

    BRANCH = "agent/HAMCP-1-fixture"
    RELEASE = "origin/release/9.9.0"

    @classmethod
    def setUpClass(cls) -> None:
        cls.powershell = shutil.which("pwsh") or shutil.which("powershell")

    def setUp(self) -> None:
        if not self.powershell:
            self.skipTest("PowerShell is unavailable")
        self.fixture = CheckScriptRepositoryFixture()
        self.main_sha = git(self.fixture.root, "rev-parse", "origin/main^{commit}")
        self.fixture.write("release.md", "release change\n")
        self.fixture.commit("fixture release change")
        git(self.fixture.root, "update-ref", "refs/remotes/" + self.RELEASE, "HEAD")
        self.release_sha = git(self.fixture.root, "rev-parse", "HEAD^{commit}")
        git(self.fixture.root, "checkout", "-q", "-b", self.BRANCH)
        self.fixture.write("docs.md", "branch change\n")

    def tearDown(self) -> None:
        if hasattr(self, "fixture"):
            self.fixture.close()

    def check(self, tier: str, base_ref: str = RELEASE) -> tuple[int, str]:
        result = self.fixture.run_check(self.powershell, tier, base_ref=base_ref)
        return result.returncode, result.stdout + result.stderr

    def metadata_arguments(self) -> list[str]:
        path = self.fixture.root / ".artifacts" / "fixture-metadata-argv.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def validation(self) -> dict:
        path = self.fixture.root / ".artifacts" / "validation.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_version_check_uses_origin_main_when_the_preflight_base_is_a_release(self):
        self.fixture.commit("fixture branch change")
        code, output = self.check("Evidence")
        self.assertEqual(code, 0, output)
        self.assertNotEqual(self.main_sha, self.release_sha)
        arguments = self.metadata_arguments()
        self.assertEqual(arguments[arguments.index("--base-ref") + 1], self.main_sha)
        self.assertIn(f"Verified base commit {self.RELEASE} at {self.release_sha}.", output)
        self.assertIn(f"Version-check base origin/main at {self.main_sha}", output)
        payload = self.validation()
        self.assertEqual(payload["overall_status"], "passed")
        self.assertEqual(payload["base_ref"], self.RELEASE)
        self.assertEqual(payload["base_sha"], self.release_sha)
        self.assertEqual(payload["version_check_base_ref"], "origin/main")
        self.assertEqual(payload["version_check_base_sha"], self.main_sha)

    def test_version_check_base_stays_origin_main_with_an_explicit_main_base(self):
        self.fixture.commit("fixture branch change")
        code, output = self.check("Full", base_ref="origin/main")
        self.assertEqual(code, 0, output)
        arguments = self.metadata_arguments()
        self.assertEqual(arguments[arguments.index("--base-ref") + 1], self.main_sha)

    def test_base_commit_is_pinned_when_the_reference_moves_mid_run(self):
        # The metadata step moves the release reference to HEAD while the run
        # is in progress, as a fetch in another worktree would.
        self.fixture.write(".fixture-move-ref", "refs/remotes/" + self.RELEASE + "\n")
        self.fixture.commit("fixture branch change that moves the base mid-run")
        head_sha = git(self.fixture.root, "rev-parse", "HEAD^{commit}")
        code, output = self.check("Evidence")
        self.assertEqual(code, 0, output)
        self.assertEqual(git(self.fixture.root, "rev-parse", self.RELEASE + "^{commit}"), head_sha)
        self.assertIn(f"{self.RELEASE} moved to {head_sha} during the run", output)
        self.assertIn(f"used the pinned base {self.release_sha}", output)
        self.assertNotIn("The Evidence base moved during validation", output)
        payload = self.validation()
        self.assertEqual(payload["overall_status"], "passed")
        self.assertEqual(payload["base_sha"], self.release_sha)
        steps = {step["name"]: step for step in payload["steps"]}
        self.assertEqual(steps["Recheck Evidence snapshot"]["status"], "passed")
        self.assertIn(self.release_sha, steps["Check committed Git whitespace"]["command"])

    def test_unresolvable_origin_main_is_refused_before_repository_code(self):
        self.fixture.commit("fixture branch change")
        git(self.fixture.root, "update-ref", "-d", "refs/remotes/origin/main")
        for tier in ("Fast", "Full", "Evidence"):
            with self.subTest(tier=tier):
                code, output = self.check(tier)
                self.assertNotEqual(code, 0, output)
                self.assertIn("The version-check base origin/main does not resolve to a commit", output)
                self.assertIn("refusing to validate deployment metadata against another base", output)
                self.assertNotIn(REPOSITORY_CODE_SENTINEL, output)


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
            "take precedence on conflicts when they are in the task context",
            normalized_root,
        )
        self.assertIn("Full gate with validation evidence", root)
        self.assertNotIn("Full gate with PR evidence", root)
        self.assertNotIn("open the first path", root.lower())
        self.assertIn("active_acceptance_document", root)
        self.assertIn("resolution_status", root)
        self.assertIn("never substitute a historical reference", normalized_root)
        runtime = paths[1].read_text(encoding="utf-8")
        self.assertIn("security-sensitive", runtime)
        self.assertIn("Keep routing fail-closed", runtime)
        self.assertIn("negative tests", runtime)
        tests = paths[2].read_text(encoding="utf-8")
        self.assertIn("deterministic, offline fixtures", tests)
        self.assertIn("success and failure paths", tests)
        self.assertIn("negative reachability", tests)
        workflows = paths[3].read_text(encoding="utf-8")
        self.assertIn("minimum GitHub permissions", workflows)
        self.assertIn("secret boundaries explicit", workflows)
        self.assertIn("pin security-sensitive actions or images", workflows)
        self.assertIn("immutably where supported", workflows)
        self.assertIn("must not publish", workflows)
        self.assertIn(
            "hidden merge, tag, package, release, or deployment side effects",
            workflows,
        )
        for path in paths[1:]:
            nested = " ".join(path.read_text(encoding="utf-8").lower().split())
            self.assertIn("this file takes precedence if guidance conflicts", nested)

    def test_root_maps_subtree_instruction_discovery(self):
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        lowered = normalized.lower()
        self.assertIn("## Subtree Instructions", text)
        self.assertIn(
            "from the project root through the session's current working directory",
            normalized,
        )
        self.assertIn(
            "must not assume that nested instruction files were loaded merely because it "
            "later reads or edits files in those subtrees",
            normalized,
        )
        for subtree, instruction_file in (
            ("hass_mcp_engineering_beta/**", "hass_mcp_engineering_beta/AGENTS.md"),
            ("tests/**", "tests/AGENTS.md"),
            (".github/workflows/**", ".github/workflows/AGENTS.md"),
        ):
            self.assertIn(f"`{subtree}`: `{instruction_file}`", normalized)
        self.assertIn(
            "Before inspecting, editing, reviewing, or testing files under these paths",
            normalized,
        )
        self.assertIn("explicitly read and follow", lowered)
        self.assertIn("takes precedence when guidance conflicts", lowered)
        self.assertIn("nonconflicting root instructions continue to apply", lowered)

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
        self.assertIn("separate bounded PR-draft generator", text)
        self.assertNotIn("Full gate with PR evidence", text)
        self.assertNotIn("does not define a supported project action schema", lowered)
        self.assertNotIn("did not establish a supported project action schema", lowered)
        self.assertIn("docs/CODEX_WORKFLOW.md", (ROOT / "README.md").read_text(encoding="utf-8"))

    def test_workflow_documents_nested_instruction_discovery(self):
        text = (ROOT / "docs" / "CODEX_WORKFLOW.md").read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        lowered = normalized.lower()
        keyboard = text.split("## Keyboard Workflow", 1)[1].split(
            "## GitHub Authentication Readiness", 1
        )[0]
        normalized_keyboard = " ".join(keyboard.split())
        remote = text.split("## Remote and Mobile Workflow", 1)[1].split(
            "## Authorization Profiles", 1
        )[0]
        normalized_remote = " ".join(remote.split())
        self.assertIn("## Instruction Discovery", text)
        self.assertIn(
            "from the project root through the session's current working directory",
            normalized,
        )
        self.assertIn("Opening a session at the repository root reliably loads", normalized)
        self.assertIn(
            "does not automatically load a nested instruction file merely because it "
            "later reads or edits a file in that subtree",
            normalized,
        )
        self.assertIn("current working directory inside a subtree", normalized)
        self.assertIn(
            "normal discovery chain from the project root to that current directory",
            normalized,
        )
        self.assertIn("Do not rely on file-edit location alone", normalized)
        for surface in (
            "Desktop keyboard work",
            "CLI or IDE work",
            "connected-host Remote or mobile work",
            "Codex cloud work",
        ):
            self.assertIn(surface, normalized)
        self.assertIn(
            "Before working in Engineering runtime, tests, or workflow files, read the "
            "corresponding nested `AGENTS.md`",
            normalized_keyboard,
        )
        self.assertIn(
            "Do not authorize remote work in a specialized subtree until the applicable "
            "nested instruction file has been read into the task context",
            normalized_remote,
        )
        for unsupported_claim in (
            "automatically loads every nested `agents.md`",
            "automatically loads all nested `agents.md`",
            "loads nested instructions when it edits a file",
        ):
            self.assertNotIn(unsupported_claim, lowered)

    def test_workflow_documents_github_authentication_readiness(self):
        text = (ROOT / "docs" / "CODEX_WORKFLOW.md").read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        for phrase in (
            "## GitHub Authentication Readiness",
            "gh auth status",
            "git fetch --prune origin",
            "gh repo view jeter-1/hass-mcp-admin",
            "authenticate interactively on the trusted execution host",
            "Never print or export GitHub tokens",
            "read or dump Git credential-store contents",
            "read or copy SSH private keys",
            "does not inherit the connected host's GitHub CLI login",
            "connected-host readiness and Codex-cloud authorization are distinct checks",
        ):
            self.assertIn(phrase, normalized)
        self.assertIn(
            "Missing or expired authentication is an environment-readiness failure",
            normalized,
        )
        self.assertIn("token in a prompt or script", normalized)
        self.assertIn("not add credentials or authentication automation", normalized)
        self.assertIn("Record the actual execution host and OS", normalized)

    def test_release_guidance_requires_exact_acceptance_authority(self):
        text = (ROOT / "docs" / "CODEX_WORKFLOW.md").read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        self.assertGreaterEqual(text.count("active_acceptance_document"), 3)
        self.assertIn("Missing exact acceptance authority is a stop condition", normalized)
        self.assertIn("Historical references cannot authorize current acceptance", normalized)
        self.assertIn("release notes are not acceptance instructions", normalized)
        self.assertIn("staged_release.documents", text)
        self.assertIn(
            "staged_release.documents.active_acceptance_document", text
        )

    def test_rc2dev13_authority_and_failed_dev12_history_are_explicit(self):
        release = (ROOT / "docs" / "RC2DEV13_RELEASE_NOTES.md").read_text(
            encoding="utf-8"
        )
        acceptance = (ROOT / "docs" / "RC2DEV13_ACCEPTANCE.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("2.0.0-rc2-dev13", release)
        self.assertIn("2.0.0-rc2-dev13", acceptance)
        self.assertIn("RC2dev12 is immutable failed history", release)
        self.assertIn("Do not retag", release)
        self.assertIn("full Home Assistant host reboot", acceptance)
        self.assertIn("without an Engineering restart", acceptance)
        self.assertIn("`metadata.completeness: partial`", acceptance)
        self.assertIn("Never retag", acceptance)
        self.assertIn("acceptance has not yet passed", acceptance)
        historical_release = (
            ROOT / "docs" / "RC2DEV12_RELEASE_NOTES.md"
        ).read_text(encoding="utf-8")
        historical_acceptance = (
            ROOT / "docs" / "RC2DEV12_ACCEPTANCE.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Historical only; cannot authorize", historical_release)
        self.assertIn("RC2dev12 was not accepted", historical_acceptance)

        spec = importlib.util.spec_from_file_location(
            "_codex_context_rc2dev13_authority", CONTEXT_SCRIPT
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        context_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(context_module)
        documents = context_module.resolve_documents(ROOT, "2.0.0-rc2-dev13")

        self.assertEqual(documents["resolution_status"], "exact")
        self.assertEqual(
            documents["active_release_notes"], "docs/RC2DEV13_RELEASE_NOTES.md"
        )
        self.assertEqual(
            documents["active_acceptance_document"],
            "docs/RC2DEV13_ACCEPTANCE.md",
        )
        historical = documents["historical_references"]
        self.assertIn("docs/RC2DEV12_ACCEPTANCE.md", historical)
        self.assertIn("docs/RC2DEV12_RELEASE_NOTES.md", historical)

    def test_rc2dev14_has_an_exact_active_document_pair(self):
        release = (ROOT / "docs" / "RC2DEV14_RELEASE_NOTES.md").read_text(
            encoding="utf-8"
        )
        acceptance = (ROOT / "docs" / "RC2DEV14_ACCEPTANCE.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Version: `2.0.0-rc2-dev14`", release)
        self.assertIn("Version: `2.0.0-rc2-dev14`", acceptance)
        self.assertIn("not published, deployed, or accepted", release)
        self.assertIn("not published", acceptance)

        spec = importlib.util.spec_from_file_location(
            "_codex_context_rc2dev14_authority", CONTEXT_SCRIPT
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        context_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(context_module)
        documents = context_module.resolve_documents(ROOT, "2.0.0-rc2-dev14")

        self.assertEqual(documents["resolution_status"], "exact")
        self.assertEqual(
            documents["active_release_notes"], "docs/RC2DEV14_RELEASE_NOTES.md"
        )
        self.assertEqual(
            documents["active_acceptance_document"],
            "docs/RC2DEV14_ACCEPTANCE.md",
        )
        self.assertIn(
            "docs/RC2DEV13_ACCEPTANCE.md",
            documents["historical_references"],
        )

    def test_rc2dev15_has_an_exact_active_document_pair(self):
        release = (ROOT / "docs" / "RC2DEV15_RELEASE_NOTES.md").read_text(
            encoding="utf-8"
        )
        acceptance = (ROOT / "docs" / "RC2DEV15_ACCEPTANCE.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Version: `2.0.0-rc2-dev15`", release)
        self.assertIn("Version: `2.0.0-rc2-dev15`", acceptance)
        self.assertIn("not published, deployed, or accepted", release)
        self.assertIn("not published", acceptance)

        spec = importlib.util.spec_from_file_location(
            "_codex_context_rc2dev15_authority", CONTEXT_SCRIPT
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        context_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(context_module)
        documents = context_module.resolve_documents(ROOT, "2.0.0-rc2-dev15")

        self.assertEqual(documents["resolution_status"], "exact")
        self.assertEqual(
            documents["active_release_notes"], "docs/RC2DEV15_RELEASE_NOTES.md"
        )
        self.assertEqual(
            documents["active_acceptance_document"],
            "docs/RC2DEV15_ACCEPTANCE.md",
        )
        self.assertIn(
            "docs/RC2DEV14_ACCEPTANCE.md",
            documents["historical_references"],
        )

    def test_rc2dev16_has_an_exact_active_document_pair(self):
        release = (ROOT / "docs" / "RC2DEV16_RELEASE_NOTES.md").read_text(
            encoding="utf-8"
        )
        acceptance = (ROOT / "docs" / "RC2DEV16_ACCEPTANCE.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Version: `2.0.0-rc2-dev16`", release)
        self.assertIn("Version: `2.0.0-rc2-dev16`", acceptance)
        self.assertIn("not published, deployed, or accepted", release)
        self.assertIn("not published", acceptance)

        spec = importlib.util.spec_from_file_location(
            "_codex_context_rc2dev16_authority", CONTEXT_SCRIPT
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        context_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(context_module)
        documents = context_module.resolve_documents(ROOT, "2.0.0-rc2-dev16")

        self.assertEqual(documents["resolution_status"], "exact")
        self.assertEqual(
            documents["active_release_notes"], "docs/RC2DEV16_RELEASE_NOTES.md"
        )
        self.assertEqual(
            documents["active_acceptance_document"],
            "docs/RC2DEV16_ACCEPTANCE.md",
        )
        self.assertIn(
            "docs/RC2DEV15_ACCEPTANCE.md",
            documents["historical_references"],
        )


class DeploymentChecklistTests(unittest.TestCase):
    def test_stale_fixed_count_and_beta_checklist_are_replaced_by_context(self):
        text = (ROOT / "scripts" / "deploy-beta.ps1").read_text(encoding="utf-8")
        self.assertNotIn("exactly 38", text)
        self.assertNotIn("Beta 25 handoff", text)
        self.assertIn("codex-context.py --format markdown", text)
        self.assertIn("document resolution status is exact", text)
        self.assertIn("active_acceptance_document is known", text)
        self.assertIn("Stop when exact acceptance authority is missing", text)
        self.assertIn("Never substitute a historical reference", text)
        self.assertNotIn("active release and acceptance documents", text)


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
                base_ref="HEAD",
                paths={"hass_mcp_engineering_beta/AGENTS.md"},
            )
        finally:
            sys.modules.pop(spec.name, None)
        self.assertFalse(report.beta_changed)
        self.assertFalse(report.production_changed)


if __name__ == "__main__":
    unittest.main()
