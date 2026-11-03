"""HAMCP-243: short sockets, leak reporting/cleanup, and R318-01 collisions."""

import ast
import io
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.support import unix_socket
from tests import test_f3_packaging_boundaries as closure
from tests import test_hamcp272_scratch_teardown as teardown


class SocketPathTests(unittest.TestCase):
    def test_short_unique_private_paths_ignore_deep_tempdir(self):
        owner = unittest.TestCase()
        self.addCleanup(owner.doCleanups)
        with patch.object(tempfile, "tempdir", "/not-used/" + "x" * 160):
            paths = [Path(unix_socket.socket_path(owner)) for _ in range(2)]
        self.assertNotEqual(paths[0].parent, paths[1].parent)
        for path in paths:
            self.assertEqual(path.parent.parent, Path("/tmp"))
            self.assertTrue(path.parent.name.startswith("hamcp-s-"))
            self.assertLessEqual(len(os.fsencode(path)), 107)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            self.assertEqual(path.parent.stat().st_uid, os.getuid())
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(path))
            finally:
                listener.close()
        owner.doCleanups()
        self.assertTrue(all(not path.parent.exists() for path in paths))

    def test_length_limit_is_bytes_and_reserves_the_nul(self):
        unix_socket.assert_socket_path_length("x" * 107)
        for path, size in (("x" * 108, 108), ("é" * 54, 108), ("x" * 200, 200)):
            with self.subTest(size=size, path=path), self.assertRaisesRegex(
                    AssertionError, f"AF_UNIX socket path is {size} bytes; maximum is 107"):
                unix_socket.assert_socket_path_length(path)

    def test_cleanup_runs_on_test_failure_and_length_validation_failure(self):
        for invalid_length in (False, True):
            directories = []
            real_mkdtemp = tempfile.mkdtemp
            def observe(*args, **kwargs):
                name = real_mkdtemp(*args, **kwargs)
                directories.append(Path(name))
                return name
            class Failing(unittest.TestCase):
                def runTest(self):
                    unix_socket.socket_path(self)
                    self.fail("synthetic test failure")
            try:
                with self.subTest(invalid_length=invalid_length), \
                        patch.object(tempfile, "mkdtemp", observe), \
                        patch.object(unix_socket, "MAX_SOCKET_PATH_BYTES", 1 if invalid_length else 107):
                    result = unittest.TextTestRunner(stream=io.StringIO()).run(Failing())
                    self.assertEqual(len(result.failures), 1)
                    self.assertEqual(result.errors, [])
                    self.assertIn("AF_UNIX socket path" if invalid_length else "synthetic test failure",
                                  result.failures[0][1])
                    self.assertEqual(len(directories), 1)
                    self.assertFalse(directories[0].exists(), "test cleanup leaked the socket directory")
            finally:
                # Keep the regression itself clean even when testing a broken helper.
                for directory in directories:
                    if directory.exists():
                        shutil.rmtree(directory)


class CopiedShardTests(unittest.TestCase):
    def fixture(self):
        fixture = teardown.GuardTests("test_clean_session_passes")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        support = fixture.root / "tests/support"
        support.mkdir()
        shutil.copyfile(Path(unix_socket.__file__), support / "unix_socket.py")
        return fixture

    def test_socket_cleanup_passes_in_the_real_shard_guard(self):
        fixture = self.fixture()
        result, manifest = fixture.run_case(
            "from support.unix_socket import socket_path\n"
            "from pathlib import Path\n"
            "path = socket_path(self)\nself.assertTrue(Path(path).parent.is_dir())")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(manifest["scratch_leaks"], [])

    def test_socket_leak_fails_shard_and_is_removed_without_touching_foreign_scratch(self):
        fixture = self.fixture()
        with tempfile.TemporaryDirectory(prefix="hamcp-s-", dir="/tmp") as foreign:
            result, manifest = fixture.run_case(
                "from support.unix_socket import socket_path\n"
                "self.addCleanup = lambda *args, **kwargs: None\n"
                "socket_path(self)")
            self.assertTrue(Path(foreign).is_dir())
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(manifest["was_successful"])
        self.assertEqual(manifest["executed"][0][1], "success")
        self.assertEqual(len(manifest["scratch_leaks"]), 1)
        leak = manifest["scratch_leaks"][0]
        self.assertTrue(leak["path"].startswith("/tmp/hamcp-s-"))
        self.assertIn("unix_socket.py:", leak["creator"])
        self.assertIn("Scratch leak:", result.stderr)
        self.assertFalse(Path(leak["path"]).exists(), "guard left its tracked socket directory behind")

    def test_unremovable_path_still_fails_copied_shard_after_other_cleanup(self):
        fixture = self.fixture()
        external = fixture.root / ".artifacts/external"
        external.mkdir(parents=True)
        foreign = external / "foreign"
        foreign.mkdir()
        (foreign / "keep").write_text("foreign scratch")
        (fixture.root / "tests/test_synthetic.py").write_text(
            "import tempfile, unittest\nfrom pathlib import Path\n"
            "class Synthetic(unittest.TestCase):\n"
            "    def test_scratch(self):\n"
            f"        outer = Path({str(external)!r})\n"
            "        blocked = Path(tempfile.mkdtemp(prefix='a-blocked-', dir=outer))\n"
            "        blocked.rmdir()\n"
            "        blocked.symlink_to(outer / 'foreign', target_is_directory=True)\n"
            "        tempfile.mkdtemp(prefix='z-removable-', dir=outer)\n")
        result = subprocess.run(
            [sys.executable, "scripts/unit_test_shards.py", "run", "--shard", "0",
             "--output", "manifest.json"], cwd=fixture.root,
            env={**os.environ, "GITHUB_RUN_ID": "1001", "GITHUB_RUN_ATTEMPT": "1"},
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(list(external.glob("z-removable-*")), [], "later scratch was not removed")
        self.assertEqual((foreign / "keep").read_text(), "foreign scratch")
        self.assertIn("Scratch leak:", result.stderr)
        self.assertIn("Scratch cleanup error:", result.stderr)
        self.assertIn("Scratch cleanup failed:", result.stderr)


class CleanupFailureTests(unittest.TestCase):
    def test_every_path_attempted_errors_reported_and_original_exception_chained(self):
        shards = teardown.load("unit_test_shards")
        with tempfile.TemporaryDirectory() as temporary:
            outer = Path(temporary)
            foreign = outer / "foreign"
            foreign.mkdir()
            sentinel = foreign / "keep"
            sentinel.write_text("foreign scratch")
            original = RuntimeError("synthetic original shard exception")
            for body_fails in (False, True):
                with self.subTest(body_fails=body_fails):
                    blocked = []
                    removable = []
                    output = io.StringIO()
                    caught = None
                    try:
                        with patch.object(shards.sys, "stderr", output), shards.scratch_session(outer / "repo") as state:
                            # All four allocations are external to this session's
                            # deep scratch, but remain under the test's .artifacts.
                            for prefix in ("a-blocked-", "b-blocked-"):
                                path = Path(tempfile.mkdtemp(prefix=prefix, dir=outer))
                                path.rmdir()
                                path.symlink_to(foreign, target_is_directory=True)
                                blocked.append(path)
                            for prefix in ("c-removable-", "d-removable-"):
                                removable.append(Path(tempfile.mkdtemp(prefix=prefix, dir=outer)))
                            if body_fails:
                                raise original
                    except Exception as exc:
                        caught = exc
                    self.assertTrue(all(not path.exists() for path in removable),
                                    "cleanup stopped before attempting remaining eligible paths")
                    self.assertIsInstance(caught, OSError, "cleanup errors must fail the shard with an aggregate")
                    self.assertEqual(sentinel.read_text(), "foreign scratch")
                    self.assertEqual({row["path"] for row in state["leftovers"]},
                                     {str(path) for path in blocked + removable})
                    for path in blocked:
                        self.assertTrue(path.is_symlink())
                        self.assertIn(str(path), str(caught))
                        self.assertIn(f"Scratch cleanup error: {path}: OSError:", output.getvalue())
                    self.assertIn("Scratch cleanup failed:", str(caught))
                    self.assertIs(caught.__cause__, original if body_fails else None)
                    for path in blocked + removable:
                        self.assertIn(f"Scratch leak: {path} created by", output.getvalue())
                    for path in blocked:
                        path.unlink()


class PrefixCollisionTests(unittest.TestCase):
    def guard(self, entries, *, prefix, isolated=None, exec_prefix=None):
        # Execute the actual closure guard, stopping before expensive imports.
        tree = ast.parse(closure._IMPORT_CLOSURE_SCRIPT)
        start = next(i for i, node in enumerate(tree.body)
                     if isinstance(node, ast.Assign) and node.targets[0].id == "forbidden_root")
        end = next(i for i, node in enumerate(tree.body)
                   if isinstance(node, ast.Import) and node.names[0].name == "ha_mcp_engineering")
        code = compile(ast.Module(body=tree.body[start:end], type_ignores=[]), "closure-guard", "exec")
        scratch = closure.ROOT / ".artifacts"
        system = SimpleNamespace(
            argv=["probe", str(closure.ROOT), str(isolated or scratch / "isolated")],
            prefix=str(prefix), exec_prefix=str(exec_prefix or scratch / "venv"),
            path=[str(entry) for entry in entries])
        exec(code, {"Path": Path, "sys": system})

    def test_repository_source_cannot_be_exempted_by_any_prefix_input(self):
        for bad in (closure.ROOT, closure.ROOT.parent, closure.BETA_DIR,
                    closure.RUNTIME, closure.ROOT / ".artifacts-lookalike"):
            for field in ("prefix", "exec_prefix", "isolated"):
                with self.subTest(bad=bad, field=field), self.assertRaisesRegex(
                        AssertionError, "repository path leaked into sys.path"):
                    args = {"prefix": closure.ROOT / ".artifacts/venv", field: bad}
                    leaked = closure.BETA_DIR if bad in (closure.ROOT, closure.ROOT.parent) else bad
                    self.guard([leaked], **args)

    def test_scratch_descendants_work_but_sibling_prefixes_do_not(self):
        scratch = closure.ROOT / ".artifacts"
        prefix = scratch / "venv"
        isolated = scratch / "isolated"
        self.guard([prefix / "lib", isolated / "app"], prefix=prefix, isolated=isolated)
        for outside in (scratch / "venv-sibling/lib", scratch / "isolated-sibling/app", closure.BETA_DIR):
            with self.subTest(outside=outside), self.assertRaisesRegex(
                    AssertionError, "repository path leaked into sys.path"):
                self.guard([outside], prefix=prefix, isolated=isolated)

    def test_resolved_alias_to_beta_cannot_be_exempted(self):
        with tempfile.TemporaryDirectory() as directory:
            alias = Path(directory) / "alias"
            alias.symlink_to(closure.BETA_DIR, target_is_directory=True)
            with self.assertRaisesRegex(AssertionError, "repository path leaked into sys.path"):
                self.guard([alias], prefix=alias)
