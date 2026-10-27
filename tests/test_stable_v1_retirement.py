"""HAMCP-203: retired stable v1 is not discoverable by the Supervisor add-on store.

The exact Supervisor 2026.09.1 discovery fragments run offline against a
disposable tree mirroring this repository's tracked paths (what a Supervisor
clone checks out). This is not a booted Supervisor or an installed store.
"""

import asyncio
import contextlib
import errno
import hashlib
import json
import logging
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures/supervisor_store_discovery_2026_09_1"
RETIRED_DIR = "hass_mcp_admin"
RETIRED_CONFIG = f"{RETIRED_DIR}/retired-addon-config.yaml"
ENGINEERING_CONFIG = "hass_mcp_engineering_beta/config.yaml"


class _Vol:
    """Records the schema shape; validation semantics are not reimplemented."""

    REMOVE_EXTRA = "remove_extra"

    @staticmethod
    def Schema(mapping, extra=None):
        return SimpleNamespace(keys=set(mapping), extra=extra)

    Required = Optional = staticmethod(lambda key: key)
    Url = staticmethod(lambda: "url")


class ConfigurationFileError(Exception):
    pass


def exact_discovery():
    ns = dict(
        Path=Path, errno=errno, REPOSITORY_LOCAL="local", vol=_Vol,
        ATTR_NAME="name", ATTR_URL="url", ATTR_MAINTAINER="maintainer",
        ConfigurationFileError=ConfigurationFileError,
        _LOGGER=logging.getLogger("synthetic-supervisor"),
    )
    provenance = json.loads((FIXTURE / "provenance.json").read_text())
    assert provenance["commit"] == "40e3ee7640a3c44abe67f1a1397f39c3cd949806"
    for fragment in provenance["fragments"]:
        data = (FIXTURE / fragment["file"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != fragment["sha256"]:
            raise AssertionError("Supervisor discovery fragment changed")
        exec(compile("from __future__ import annotations\n" + data.decode(),
                     fragment["path"], "exec"), ns)
    return ns


def tracked_paths():
    output = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True,
    ).stdout.decode()
    return [path for path in output.split("\0") if path]


def mirror(root: Path, paths):
    for path in paths:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()


class StableV1RetirementTests(unittest.TestCase):
    def setUp(self):
        self.ns = exact_discovery()
        self.store = SimpleNamespace(sys_run_in_executor=self._executor)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        # Supervisor clones under a hash-named, non-dot directory.
        self.clones = Path("data") / "addons" / "git"
        # The exact rule drops any dot-prefixed path part, including the temp
        # root, so discovery runs on a path relative to the temporary root. A
        # scratch TMPDIR inside the worktree (.artifacts/tmp) then cannot leak
        # a dot-prefixed part into the rule.
        self.assertFalse([p for p in self.clones.parts if p.startswith(".")],
                         "discovery path contains a dot-prefixed part")

    @staticmethod
    async def _executor(func, *args):
        return func(*args)

    def discover(self, paths):
        clone = self.clones / f"clone{len(list((self.base / self.clones).glob('*')))}"
        mirror(self.base / clone, paths)
        with contextlib.chdir(self.base):
            found = asyncio.run(
                self.ns["_find_app_configs"](self.store, clone, "a1b2c3d4"))
        return sorted(path.relative_to(clone).as_posix() for path in found)

    def test_repository_discovers_only_engineering(self):
        paths = tracked_paths()
        for kept in (RETIRED_CONFIG, f"{RETIRED_DIR}/server.py"):
            self.assertTrue(kept in paths, f"{kept} is not tracked")
        self.assertEqual(self.discover(paths), [ENGINEERING_CONFIG])

    def test_discovery_rule_would_find_the_old_layout(self):
        # Positive control: the old name, or a plain archive/ move, is found.
        engineering = [ENGINEERING_CONFIG]
        self.assertEqual(
            self.discover(engineering + [f"{RETIRED_DIR}/config.yaml"]),
            sorted(engineering + [f"{RETIRED_DIR}/config.yaml"]))
        archived = f"archive/{RETIRED_DIR}/config.yaml"
        self.assertEqual(self.discover(engineering + [archived]),
                         sorted(engineering + [archived]))
        for retired_name in ("config.json", "config.yml", "deep/x/config.yaml"):
            path = f"{RETIRED_DIR}/{retired_name}"
            self.assertIn(path, self.discover([path]))

    def test_discovery_rule_ignores_nonmatching_and_excluded_paths(self):
        ignored = [
            RETIRED_CONFIG, f"{RETIRED_DIR}/config.yaml.retired",
            f".retired/{RETIRED_DIR}/config.yaml", f"{RETIRED_DIR}/rootfs/config.yaml",
        ]
        self.assertEqual(self.discover(ignored), [])

    def test_repository_metadata_cannot_list_addons(self):
        schema = self.ns["SCHEMA_REPOSITORY_CONFIG"]
        self.assertEqual(schema.keys, {"name", "url", "maintainer"})
        self.assertEqual(schema.extra, _Vol.REMOVE_EXTRA)
        metadata = yaml.safe_load((ROOT / "repository.yaml").read_text())
        self.assertIn("name", metadata)
        self.assertLessEqual(set(metadata), schema.keys)
        clone = self.base / self.clones / "metadata"
        mirror(clone, tracked_paths())
        found = self.ns["find_one_filetype"](
            clone, "repository", self.ns["FILE_SUFFIX_CONFIGURATION"])
        self.assertEqual(found, clone / "repository.yaml")

    def test_retired_code_is_kept_and_marked(self):
        config = yaml.safe_load((ROOT / RETIRED_CONFIG).read_text())
        self.assertEqual((config["slug"], config["version"]), ("hass_mcp_admin", "1.1.2"))
        self.assertFalse((ROOT / RETIRED_DIR / "config.yaml").exists())
        for name in ("server.py", "metadata.py", "Dockerfile", "requirements.txt"):
            self.assertTrue((ROOT / RETIRED_DIR / name).is_file(), name)
        notice = (ROOT / RETIRED_DIR / "README.md").read_text()
        for phrase in ("RETIRED", "2026-10-16", "hass_mcp_engineering_beta", "won't fix"):
            self.assertIn(phrase, notice)
        self.assertIn("RETIRED", (ROOT / RETIRED_CONFIG).read_text().splitlines()[0])

    def test_engineering_remains_a_valid_store_entry(self):
        config = yaml.safe_load((ROOT / ENGINEERING_CONFIG).read_text())
        self.assertEqual(config["slug"], "hass_mcp_engineering_beta")
        self.assertTrue({"name", "version", "slug", "description", "arch"} <= set(config))


if __name__ == "__main__":
    unittest.main()
