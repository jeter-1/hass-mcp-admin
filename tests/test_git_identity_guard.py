"""HAMCP-216: actual Git config leaks fail without touching the developer repo."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
from support.process_isolation import GitIdentityGuard  # noqa: E402


class GitIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "repository"
        self.root.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.git("init", "-q", "-b", "main")
        self.git("-c", "user.name=Synthetic fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "--allow-empty", "-qm", "Synthetic baseline")
        self.git("config", "extensions.worktreeConfig", "true")
        self.worktree = self.base / "worktree"
        self.git("worktree", "add", "--quiet", "-b", "synthetic-work", str(self.worktree))

    def git(self, *args, root=None):
        return subprocess.run(["git", "-C", str(root or self.root), *args], env=self.env,
                              capture_output=True, text=True, check=True).stdout.strip()

    def guard(self, root=None):
        guard = GitIdentityGuard(root or self.worktree)
        self.assertIsNone(guard.check(None))
        return guard

    def test_clean_main_and_worktree_configs_pass(self):
        for root in (self.root, self.worktree):
            with self.subTest(root=root):
                guard = self.guard(root)
                self.assertEqual(set(guard.baseline), {"local", "worktree"})
                self.assertIsNone(guard.check("clean_module"))

    def test_shared_local_identity_write_fails_and_is_not_restored(self):
        for key in ("user.name", "user.email"):
            with self.subTest(key=key):
                guard = self.guard()
                self.git("config", "--local", "--add", key, "synthetic-private-value")
                message = guard.check("test_local_leaker")
                self.assertIsInstance(message, str)
                self.assertIn("test_local_leaker", message)
                self.assertIn("scopes: local", message)
                self.assertNotIn("synthetic-private-value", message)
                self.assertEqual(self.git("config", "--local", "--get", key), "synthetic-private-value")
                self.assertIsNone(guard.check("innocent_following_module"))

    def test_worktree_identity_write_fails_even_when_shared_config_is_clean(self):
        guard = self.guard()
        self.git("config", "--worktree", "user.name", "synthetic-worktree", root=self.worktree)
        message = guard.check("test_worktree_leaker")
        self.assertIsInstance(message, str)
        self.assertIn("scopes: worktree", message)
        self.assertIn("test_worktree_leaker", message)

    def test_dormant_worktree_identity_and_duplicate_local_values_are_checked(self):
        self.git("config", "--unset", "extensions.worktreeConfig")
        guard = self.guard()
        self.git("config", "--file", str(guard.paths["worktree"]), "user.email", "synthetic@example.invalid")
        message = guard.check("dormant_writer")
        self.assertIsInstance(message, str)
        self.assertIn("scopes: worktree", message)
        self.git("config", "--local", "--add", "user.name", "first")
        guard.check("initial_writer")
        self.git("config", "--local", "--add", "user.name", "second")
        message = guard.check("duplicate_writer")
        self.assertIsInstance(message, str)
        self.assertIn("scopes: local", message)

    def test_included_identity_is_compared_without_reporting_values(self):
        included = self.base / "included.config"
        self.git("config", "--file", str(included), "user.email", "before@example.invalid")
        self.git("config", "--local", "include.path", str(included))
        guard = self.guard()
        self.git("config", "--file", str(included), "user.email", "after@example.invalid")
        message = guard.check("included_writer")
        self.assertIsInstance(message, str)
        self.assertIn("scopes: local", message)
        self.assertNotIn("@example.invalid", message)

    def test_unrelated_keys_and_temporary_repository_identity_are_allowed(self):
        guard = self.guard()
        self.git("config", "--local", "synthetic.unrelated", "value")
        other = self.base / "other"
        other.mkdir()
        self.git("init", "-q", root=other)
        self.git("config", "user.name", "fixture", root=other)
        self.assertIsNone(guard.check("safe_fixture"))

    def test_invalid_config_fails_closed_without_echoing_contents(self):
        guard = self.guard()
        guard.paths["worktree"].write_text("[synthetic-private-invalid\n")
        message = guard.check("broken_writer")
        self.assertIsInstance(message, str)
        self.assertIn("could not be verified", message)
        self.assertNotIn("synthetic-private", message)

    def test_source_only_copy_has_no_config_to_guard(self):
        copy = self.base / "source-only"
        copy.mkdir()
        guard = self.guard(copy)
        self.assertEqual(guard.baseline, {})
        self.assertIsNone(guard.check("clean_copy"))

    def run_suite(self, scope=None, fixture="test"):
        support = self.worktree / "tests" / "support"
        if support.parent.exists():
            shutil.rmtree(support.parent)
        support.mkdir(parents=True)
        (support / "__init__.py").write_text("")
        shutil.copyfile(ROOT / "tests/support/process_isolation.py", support / "process_isolation.py")
        (support.parent / "test_a_clean.py").write_text(
            "import unittest\nfrom support import process_isolation\n"
            "class Clean(unittest.TestCase):\n def test_clean(self): pass\n")
        action = (f"subprocess.run(['git', 'config', '--{scope}', 'user.name', 'synthetic-private-value'], check=True)"
                  if scope else "pass")
        body = "import subprocess, unittest\n"
        if fixture == "setup":
            body += f"def setUpModule():\n {action}\n"
        if fixture == "teardown":
            body += f"def tearDownModule():\n {action}\n"
        body += "class Leak(unittest.TestCase):\n def test_write(self):\n  " + (action if fixture == "test" else "pass") + "\n"
        (support.parent / "test_b_identity_writer.py").write_text(body)
        result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                                cwd=self.worktree, env=self.env, capture_output=True, text=True, timeout=20)
        return result

    def test_clean_real_git_suite_passes(self):
        result = self.run_suite()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 2 tests", result.stderr)
        self.assertNotIn("repository Git identity", result.stderr)

    def test_actual_config_writer_fails_suite_naming_its_module(self):
        for scope, fixture in (("local", "test"), ("worktree", "test"),
                               ("local", "setup"), ("worktree", "teardown")):
            with self.subTest(scope=scope, fixture=fixture):
                # A fresh suite process snapshots the existing scratch state.
                self.git("config", "--local", "user.name", "baseline")
                self.git("config", "--worktree", "user.name", "baseline", root=self.worktree)
                result = self.run_suite(scope, fixture)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("ERROR: tearDownModule (test_b_identity_writer)", result.stderr)
                self.assertIn(f"scopes: {scope}", result.stderr)
                self.assertIn("FAILED (errors=1)", result.stderr)
                self.assertNotIn("synthetic-private-value", result.stderr)


if __name__ == "__main__":
    unittest.main()
