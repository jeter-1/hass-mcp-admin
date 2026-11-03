"""HAMCP-243: short sockets, leak reporting/cleanup, and R318-01 collisions."""

import ast
import io
import os
from pathlib import Path
import shutil
import socket
import stat
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
