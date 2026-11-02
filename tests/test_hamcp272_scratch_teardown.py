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
import time
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

    def test_reused_core_and_executor_fixtures_clean_when_owner_fails(self):
        import test_typed_fan
        import test_typed_power
        import test_hamcp189_authority_cancel
        async def fail_after_setup(case):
            case.assertTrue(list(Path(directory).iterdir()))
            case.fail("synthetic owner failure")
        for base in (test_typed_fan.CoreAuthorityTests, test_typed_power.PowerSignedAuthorityTests,
                     test_hamcp189_authority_cancel.UnmappedOperationRefusalTests):
            with self.subTest(fixture=base.__module__), tempfile.TemporaryDirectory() as directory, \
                    patch.object(tempfile, "tempdir", directory):
                probe = type("CleanupProbe", (base,), {"test_cleanup_probe": fail_after_setup})
                result = unittest.TextTestRunner(stream=io.StringIO()).run(probe("test_cleanup_probe"))
                self.assertEqual(len(result.failures), 1)
                self.assertEqual(result.errors, [])
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

    def write_lock(self, contents):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        lock = self.output.parent / "latest.lock"
        lock.write_text(contents)
        return lock

    def test_lock_records_owner_and_start_time(self):
        before = time.time()
        with self.local.latest_output(self.root, self.output):
            owner = json.loads((self.output.parent / "latest.lock").read_text())
            self.assertEqual(owner["pid"], os.getpid())
            self.assertGreaterEqual(owner["started_at"], before)
            self.assertLessEqual(owner["started_at"], time.time())
        self.assertFalse((self.output.parent / "latest.lock").exists())

    def test_live_pid_refuses_even_without_advisory_lock_and_when_old(self):
        lock = self.write_lock(json.dumps({"pid": os.getpid(), "started_at": 1}))
        os.utime(lock, (1, 1))
        with self.assertRaisesRegex(self.local.unit_test_shards.Refusal,
                                    r"latest_output_in_use; remove \.artifacts/unit-test-shards/latest.lock only if no Evidence run is active"):
            with self.local.latest_output(self.root, self.output):
                self.fail("live owner reclaimed")
        self.assertEqual(json.loads(lock.read_text())["pid"], os.getpid())

    def dead_pid(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=10)
        self.assertFalse(self.local.pid_alive(child.pid))
        return child.pid

    def test_dead_owner_reclaimed_with_warning(self):
        pid = self.dead_pid()
        lock = self.write_lock(json.dumps({"pid": pid, "started_at": 1}))
        warning = io.StringIO()
        try:
            with patch.object(sys, "stderr", warning), self.local.latest_output(self.root, self.output):
                self.assertEqual(json.loads(lock.read_text())["pid"], os.getpid())
        except self.local.unit_test_shards.Refusal as exc:
            self.fail(f"dead owner must be reclaimed: {exc}")
        self.assertIn(f"reclaiming stale latest.lock owner PID {pid}, started_at 1", warning.getvalue())
        self.assertFalse(lock.exists())

    def test_corrupt_metadata_refuses_until_one_hour_old(self):
        for contents in ("", "not json", "[]", '{"pid": 0, "started_at": 1}'):
            with self.subTest(contents=contents):
                lock = self.write_lock(contents)
                with self.assertRaisesRegex(self.local.unit_test_shards.Refusal, "latest_output_in_use"):
                    with self.local.latest_output(self.root, self.output):
                        self.fail("recent corrupt lock reclaimed")
                self.assertEqual(lock.read_text(), contents)
                old = time.time() - self.local.CORRUPT_LOCK_MAX_AGE - 1
                os.utime(lock, (old, old))
                warning = io.StringIO()
                with patch.object(sys, "stderr", warning), self.local.latest_output(self.root, self.output):
                    self.assertEqual(json.loads(lock.read_text())["pid"], os.getpid())
                self.assertIn("unknown (corrupt/unreadable metadata older than one hour)", warning.getvalue())
                self.assertFalse(lock.exists())

    def test_unreadable_metadata_uses_same_age_bound(self):
        for age, reclaims in ((0, False), (self.local.CORRUPT_LOCK_MAX_AGE + 1, True)):
            lock = self.write_lock("unreadable synthetic owner")
            os.utime(lock, (time.time() - age, time.time() - age))
            with patch.object(self.local.os, "read", side_effect=OSError("synthetic read failure")), \
                    patch.object(sys, "stderr", io.StringIO()) as warning:
                if reclaims:
                    with self.local.latest_output(self.root, self.output):
                        pass
                    self.assertIn("unknown", warning.getvalue())
                    self.assertFalse(lock.exists())
                else:
                    with self.assertRaisesRegex(self.local.unit_test_shards.Refusal, "latest_output_in_use"):
                        with self.local.latest_output(self.root, self.output):
                            self.fail("recent unreadable lock reclaimed")

    def test_stale_owner_does_not_authorize_removing_foreign_directory(self):
        lock = self.write_lock(json.dumps({"pid": self.dead_pid(), "started_at": 1}))
        self.output.mkdir()
        (self.output / "keep").write_text("foreign")
        with patch.object(sys, "stderr", io.StringIO()), \
                self.assertRaisesRegex(self.local.unit_test_shards.Refusal, "latest_output_not_owned"):
            with self.local.latest_output(self.root, self.output):
                self.fail("foreign directory reclaimed")
        self.assertEqual((self.output / "keep").read_text(), "foreign")
        self.assertFalse(lock.exists())

    def test_killed_writer_receipt_can_be_replaced(self):
        program = """
import os, pathlib, sys
sys.path.insert(0, sys.argv[1])
import local_unit_test_shards as local
root = pathlib.Path(sys.argv[2])
output = root / '.artifacts/unit-test-shards/latest'
with local.latest_output(root, output):
    local.prepare_layout(output)
    os._exit(7)
"""
        child = subprocess.run([sys.executable, "-c", program, str(ROOT / "scripts"), str(self.root)],
                               capture_output=True, text=True, timeout=10)
        self.assertEqual(child.returncode, 7, child.stderr)
        self.assertTrue((self.output / ".hamcp-unit-run").is_file())
        with patch.object(sys, "stderr", io.StringIO()) as warning, \
                self.local.latest_output(self.root, self.output):
            self.assertFalse(self.output.exists())
        self.assertIn("reclaiming stale latest.lock owner PID", warning.getvalue())
        self.assertFalse((self.output.parent / "latest.lock").exists())

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
