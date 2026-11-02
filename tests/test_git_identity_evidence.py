"""HAMCP-216 R1: prevent scratch discovery and guard the entire Evidence run."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
from test_codex_workflow import CheckScriptRepositoryFixture  # noqa: E402


def load_shards():
    spec = importlib.util.spec_from_file_location("identity_boundary_shards", ROOT / "scripts/unit_test_shards.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScratchGitCeilingTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".artifacts/tmp"
        parent.mkdir(parents=True, exist_ok=True)
        self.base = Path(self.enterContext(tempfile.TemporaryDirectory(dir=parent)))
        self.root = self.base / "repository"
        self.root.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.git(self.root, "init", "-q")
        self.shards = load_shards()
        self.private = uuid.uuid4().hex

    def git(self, root, *args, env=None):
        return subprocess.run(["git", "-C", str(root), *args], env=env or self.env,
                              capture_output=True, timeout=15)

    def identity(self):
        result = self.git(self.root, "config", "--local", "--null", "--get-regexp", r"^user\.(name|email)$")
        self.assertIn(result.returncode, (0, 1))
        return result.stdout

    def test_hypothesis_without_ceiling_changes_only_the_disposable_enclosing_repo(self):
        child = self.root / ".artifacts/tmp/uninitialized"
        child.mkdir(parents=True)
        before = self.identity()
        result = self.git(child, "config", "user.name", self.private)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(self.identity() != before, "hypothesis did not reproduce")

    def test_session_blocks_uninitialized_scratch_including_the_parent_and_runner_area(self):
        runner = self.root / "runner-temp"
        runner.mkdir(parents=True)
        before = self.identity()
        with patch.dict(os.environ, {**self.env, "RUNNER_TEMP": str(runner)}, clear=True):
            with self.shards.scratch_session(self.root):
                session = Path(os.environ["TMPDIR"])
                with tempfile.TemporaryDirectory() as child:
                    for cwd in (Path(child), session, session.parent, runner):
                        with self.subTest(cwd=cwd.name):
                            result = self.git(cwd, "config", "user.name", self.private, env=dict(os.environ))
                            self.assertNotEqual(result.returncode, 0, "uninitialized scratch reached a repository")
                            self.assertTrue(self.identity() == before, "enclosing identity changed")

    def test_session_preserves_initialized_fixtures_root_reads_and_existing_ceilings(self):
        existing = str(self.base / "existing-boundary")
        with patch.dict(os.environ, {**self.env, "GIT_CEILING_DIRECTORIES": existing}, clear=True):
            with self.shards.scratch_session(self.root):
                env = dict(os.environ)
                self.assertIn(existing, env["GIT_CEILING_DIRECTORIES"].split(os.pathsep))
                before = self.identity()
                with tempfile.TemporaryDirectory() as directory:
                    own = Path(directory)
                    self.assertEqual(self.git(own, "init", "-q", env=env).returncode, 0)
                    self.assertEqual(self.git(own, "config", "user.name", self.private, env=env).returncode, 0)
                    self.assertTrue(self.identity() == before, "fixture affected enclosing identity")
                    self.assertEqual(self.git(own, "rev-parse", "--show-toplevel", env=env).stdout.decode().strip(), str(own))
                read = self.git(self.root, "rev-parse", "--show-toplevel", env=env)
                self.assertEqual(read.returncode, 0)
                self.assertEqual(read.stdout.decode().strip(), str(self.root))
            self.assertEqual(os.environ["GIT_CEILING_DIRECTORIES"], existing)

    def test_discovery_imports_receive_prevention_before_any_tests_run(self):
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_probe.py").write_text(
            "import subprocess, tempfile, unittest, uuid\n"
            "with tempfile.TemporaryDirectory() as directory:\n"
            "    result = subprocess.run(['git', 'config', 'user.name', uuid.uuid4().hex], cwd=directory, capture_output=True)\n"
            "    assert result.returncode != 0, 'discovery reached enclosing config'\n"
            "class Probe(unittest.TestCase):\n    def test_clean(self): pass\n")
        scripts = self.root / "scripts"
        scripts.mkdir()
        shutil.copyfile(ROOT / "scripts/unit_test_shards.py", scripts / "unit_test_shards.py")
        (self.root / "ci").mkdir()
        (self.root / "ci/unit-test-shards.json").write_text(json.dumps({
            "schema_version": 1, "general_shards": 1, "default_module_seconds": 1, "module_seconds": {}}))
        # The shard's provenance read needs a commit; synthetic identity is
        # command-scoped in this disposable fixture, never the developer repo.
        commit_env = {**self.env, "GIT_AUTHOR_NAME": self.private, "GIT_AUTHOR_EMAIL": self.private + "@example.invalid",
                      "GIT_COMMITTER_NAME": self.private, "GIT_COMMITTER_EMAIL": self.private + "@example.invalid"}
        self.assertEqual(self.git(self.root, "commit", "--allow-empty", "-qm", "fixture", env=commit_env).returncode, 0)
        before = self.identity()
        result = subprocess.run([sys.executable, str(scripts / "unit_test_shards.py"), "run", "--repo-root", str(self.root),
                                 "--shard", "0", "--output", str(self.base / "manifest.json")],
                                env={**self.env, "GITHUB_RUN_ID": "1001", "GITHUB_RUN_ATTEMPT": "1"},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.identity() == before, "discovery changed enclosing identity")


class EvidenceGitIdentityTests(unittest.TestCase):
    def setUp(self):
        self.powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not self.powershell:
            self.skipTest("PowerShell unavailable")
        parent = ROOT / ".artifacts/tmp"
        parent.mkdir(parents=True, exist_ok=True)
        # Keep even direct named-test invocation scratch inside .artifacts.
        self.enterContext(patch.object(tempfile, "tempdir", str(parent)))
        self.fixture = CheckScriptRepositoryFixture()
        self.addCleanup(self.fixture.close)
        self.private = uuid.uuid4().hex

    def check(self):
        result = self.fixture.run_check(self.powershell, "Evidence")
        output = result.stdout + result.stderr
        self.assertNotIn(self.private, output, "identity values must not be printed")
        payload = json.loads((self.fixture.root / ".artifacts/validation.json").read_text())
        return result, output, payload

    def inject_writer(self, scope, *, native_failure=False, exception=False):
        self.fixture.write("scripts/validate_addon_metadata.py",
                           "import subprocess\n"
                           f"subprocess.run(['git', 'config', '--{scope}', 'user.email', {self.private!r}], check=True, capture_output=True)\n"
                           + ("raise SystemExit(19)\n" if native_failure else ""))
        if exception:
            path = self.fixture.root / "scripts/check.ps1"
            source = path.read_text()
            marker = '        $yamlProgram = '
            self.assertEqual(source.count(marker), 1)
            source = source.replace(marker, '        throw "synthetic Evidence exception"\n' + marker)
            path.write_text(source)
        self.fixture.commit("synthetic identity writer")
        if scope == "worktree":
            subprocess.run(["git", "-C", str(self.fixture.root), "config", "extensions.worktreeConfig", "true"],
                           capture_output=True, check=True)
            linked = self.fixture.root / ".artifacts/linked"
            subprocess.run(["git", "-C", str(self.fixture.root), "worktree", "add", "-b", "fixture-linked", str(linked)],
                           capture_output=True, check=True)
            self.fixture.root = linked

    def assert_identity_failure(self, scope, **kwargs):
        self.inject_writer(scope, **kwargs)
        result, output, payload = self.check()
        self.assertNotEqual(result.returncode, 0, "Evidence silently accepted an identity mutation")
        self.assertIn(f"repository git identity changed during Evidence (scopes: {scope}); config NOT restored.", output)
        step = next(row for row in payload["steps"] if row["name"] == "Recheck repository Git identity")
        self.assertEqual(step["status"], "failed")
        self.assertEqual(payload["overall_status"], "failed")
        current = subprocess.run(["git", "-C", str(self.fixture.root), "config", "--" + scope, "--get", "user.email"], capture_output=True)
        self.assertTrue(current.stdout.decode().strip() == self.private, "guard unexpectedly repaired config")

    def test_clean_evidence_passes_and_records_both_snapshot_steps(self):
        result, output, payload = self.check()
        self.assertEqual(result.returncode, 0, output)
        for name in ("Snapshot repository Git identity", "Recheck repository Git identity"):
            self.assertEqual(next(row["status"] for row in payload["steps"] if row["name"] == name), "passed")

    def test_shared_local_write_in_non_unittest_step_fails_evidence(self):
        self.assert_identity_failure("local")

    def test_worktree_only_write_in_non_unittest_step_fails_evidence(self):
        self.assert_identity_failure("worktree")

    def test_native_failure_does_not_bypass_identity_comparison(self):
        self.assert_identity_failure("local", native_failure=True)

    def test_exception_does_not_bypass_final_identity_comparison(self):
        self.assert_identity_failure("worktree", exception=True)

    def test_evidence_child_cannot_discover_real_repo_from_uninitialized_scratch(self):
        self.fixture.write("scripts/validate_addon_metadata.py",
                           "import pathlib, subprocess, uuid\n"
                           "scratch = pathlib.Path('.artifacts/tmp/uninitialized')\n"
                           "scratch.mkdir(parents=True)\n"
                           "result = subprocess.run(['git', 'config', 'user.name', uuid.uuid4().hex], cwd=scratch, capture_output=True)\n"
                           "assert result.returncode != 0, 'scratch reached enclosing config'\n")
        self.fixture.commit("synthetic scratch discovery attempt")
        result, output, payload = self.check()
        self.assertEqual(result.returncode, 0, output)
        self.assertEqual(payload["overall_status"], "passed")


if __name__ == "__main__":
    unittest.main()
