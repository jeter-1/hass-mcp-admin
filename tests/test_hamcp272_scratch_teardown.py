"""Scratch ownership regressions; copied runners and synthetic journals only."""
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "hass_mcp_engineering_beta")]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp) / "copy"
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "tests").mkdir()
        (self.root / "ci").mkdir()
        for name in ("unit_test_shards.py", "local_unit_test_shards.py"):
            shutil.copyfile(ROOT / "scripts" / name, self.root / "scripts" / name)
        (self.root / "ci/unit-test-shards.json").write_text(json.dumps({
            "schema_version": 1, "general_shards": 1,
            "default_module_seconds": 1, "module_seconds": {}}))
        for args in (("init", "-q"), ("config", "user.name", "Synthetic test"),
                     ("config", "user.email", "synthetic@example.invalid"),
                     ("add", "."), ("commit", "-qm", "Synthetic source")):
            subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def run_case(self, body):
        (self.root / "tests/test_synthetic.py").write_text(
            "import tempfile, unittest, subprocess, sys\n"
            "class Synthetic(unittest.TestCase):\n"
            "    def test_scratch(self):\n" + "\n".join("        " + line for line in body.splitlines()) + "\n")
        result = subprocess.run([
            sys.executable, "scripts/unit_test_shards.py", "run", "--shard", "0",
            "--output", "manifest.json"], cwd=self.root,
            env={**os.environ, "GITHUB_RUN_ID": "1001", "GITHUB_RUN_ATTEMPT": "1"},
            capture_output=True, text=True, timeout=30)
        manifest = json.loads((self.root / "manifest.json").read_text())
        self.assertEqual(manifest["assigned"], ["test_synthetic.Synthetic.test_scratch"])
        self.assertEqual(list((self.root / ".artifacts/tmp").iterdir()), [])
        return result, manifest

    def test_clean_session_passes(self):
        result, manifest = self.run_case("with tempfile.TemporaryDirectory() as directory:\n    self.assertTrue(directory)")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(manifest["was_successful"])
        self.assertEqual(manifest["scratch_leaks"], [])

    def test_journal_leak_fails_session_and_names_creator(self):
        result, manifest = self.run_case("tempfile.mkdtemp(prefix='hamcp224-root-')")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(manifest["was_successful"])
        self.assertEqual(manifest["executed"][0][1], "success")
        self.assertIn("Scratch leak:", result.stderr)
        self.assertIn("hamcp224-root-", manifest["scratch_leaks"][0]["path"])
        self.assertIn("test_synthetic.py:", manifest["scratch_leaks"][0]["creator"])

    def test_other_scratch_leak_also_fails(self):
        result, manifest = self.run_case("tempfile.mkdtemp(prefix='hamcp181-beta8-downgrade-')")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(manifest["was_successful"])

    def test_subprocess_leak_fails(self):
        result, manifest = self.run_case(
            'subprocess.run([sys.executable, "-c", "import tempfile; tempfile.mkdtemp(prefix=\'hamcp224-root-\')"], check=True)')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(manifest["was_successful"])
        self.assertIn("unknown test", manifest["scratch_leaks"][0]["creator"])

    def test_failing_test_cleans_its_registered_scratch(self):
        result, manifest = self.run_case(
            "self.enterContext(tempfile.TemporaryDirectory(prefix='hamcp224-root-'))\nself.fail('mid-test failure')")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(manifest["executed"][0][1], "failure")
        self.assertEqual(manifest["scratch_leaks"], [])
        self.assertNotIn("Scratch leak:", result.stderr)


class JournalOwnershipTests(unittest.TestCase):
    def test_real_review_fixture_cleans_on_success_and_mid_test_failure(self):
        import test_pr313_independent_review as review
        class Failing(review.ReviewTests):
            async def test_midway(self):
                self.assertTrue(Path(self.helper.journal).is_dir())
                self.fail("synthetic mid-test failure")
        for case, failed in ((review.ReviewTests("test_expired_deadline_performs_no_field_reads"), False),
                             (Failing("test_midway"), True)):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as directory, \
                    patch.object(tempfile, "tempdir", directory):
                result = unittest.TextTestRunner(stream=io.StringIO()).run(case)
                self.assertEqual(len(result.failures), int(failed))
                self.assertEqual(result.errors, [])
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_journal_construction_failure_cleans_partial_root(self):
        import host_journal_support as journal
        with tempfile.TemporaryDirectory() as directory, patch.object(tempfile, "tempdir", directory), \
                patch.object(journal.gzip, "open", side_effect=OSError("synthetic decompression failure")):
            with self.assertRaisesRegex(OSError, "synthetic decompression"):
                journal.journal_root()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_downgrade_setup_failure_runs_module_cleanup(self):
        program = """
import importlib, pathlib, tempfile, unittest
from unittest.mock import patch
for name, commit_name in (("beta8", "BETA8_COMMIT"), ("beta36", "BETA36_COMMIT")):
    module = importlib.import_module("test_hamcp181_" + name + "_downgrade")
    def git(*args, **kwargs):
        return getattr(module, commit_name) if args[0] == "rev-parse" else None
    with tempfile.TemporaryDirectory() as directory, patch.object(tempfile, "tempdir", directory), \
            patch.object(module, "_git", git), patch.object(module, "verified_provenance", return_value={}), \
            patch.object(module.shutil, "which", return_value="git"):
        try:
            module.setUpModule()
        except AssertionError as exc:
            assert "git archive" in str(exc), str(exc)
        else:
            raise AssertionError("setup did not fail")
        assert list(pathlib.Path(directory).iterdir()), "fixture was never created"
        unittest.doModuleCleanups()
        assert not list(pathlib.Path(directory).iterdir()), "failed setup leaked scratch"
"""
        result = subprocess.run([sys.executable, "-c", program], cwd=ROOT,
                                env={**os.environ, "PYTHONPATH": str(ROOT / "tests")},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


class BoundedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.local = load("local_unit_test_shards")
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.output = self.root / ".artifacts/unit-test-shards/latest"

    def test_receipt_overwritten_after_success_and_failure(self):
        for fail in (False, True, False):
            try:
                with self.local.latest_output(self.root, self.output):
                    self.assertFalse(self.output.exists())
                    self.output.mkdir()
                    (self.output / "summary.json").write_text("bounded synthetic receipt")
                    if fail:
                        raise RuntimeError("synthetic run failure")
            except RuntimeError:
                self.assertTrue(fail)
            self.assertEqual(list(self.output.parent.iterdir()), [self.output])
            self.assertEqual(sorted(p.name for p in self.output.iterdir()), [".hamcp-unit-run", "summary.json"])

    def test_concurrent_writer_refused(self):
        with self.local.latest_output(self.root, self.output):
            with self.assertRaisesRegex(self.local.unit_test_shards.Refusal, "latest_output_in_use"):
                with self.local.latest_output(self.root, self.output):
                    self.fail("second writer admitted")

    def test_foreign_path_or_unowned_directory_preserved(self):
        foreign = self.root / "other"
        with self.assertRaisesRegex(self.local.unit_test_shards.Refusal, "fixed_latest"):
            with self.local.latest_output(self.root, foreign):
                self.fail("foreign path admitted")
        self.output.mkdir(parents=True)
        evidence = self.output / "keep"
        evidence.write_text("unrelated")
        with self.assertRaisesRegex(self.local.unit_test_shards.Refusal, "not_owned"):
            with self.local.latest_output(self.root, self.output):
                self.fail("unowned directory admitted")
        self.assertEqual(evidence.read_text(), "unrelated")
        self.assertFalse((self.output / ".hamcp-unit-run").exists())

    def test_symlink_is_preserved(self):
        self.output.parent.mkdir(parents=True)
        target = self.root / "unrelated"
        target.mkdir()
        self.output.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(self.local.unit_test_shards.Refusal, "fixed_latest"):
            with self.local.latest_output(self.root, self.output):
                self.fail("symlink admitted")
        self.assertTrue(target.is_dir())
