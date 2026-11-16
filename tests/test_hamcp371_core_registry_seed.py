"""HAMCP-371 seeding ruling (a): the lane's Core cache is refresh()'s own offline output.

Engineering's CoreReleaseRegistry runs here with a local fetcher; no network,
container or Home Assistant is used.  The registry blob is read from the local
object store and the tests skip when registry commit 917bfa2a is not present.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "docs/evidence/hamcp371/sr/core_registry_seed.py"
SPEC = importlib.util.spec_from_file_location("hamcp371_core_registry_seed", SCRIPT)
seed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seed)
sys.path.insert(0, str(ROOT / "scripts"))
import hamcp371_live_replay_local as sr  # noqa: E402

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
HAVE_REGISTRY = subprocess.run(
    ["git", "-C", str(ROOT), "cat-file", "-e", f"{seed.REGISTRY_COMMIT}^{{commit}}"],
    capture_output=True).returncode == 0


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


@unittest.skipUnless(HAVE_REGISTRY, "registry commit 917bfa2a is not in the local object store")
class CoreRegistrySeedTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".artifacts/tmp").mkdir(parents=True, exist_ok=True)
        self._tmp = tempfile.TemporaryDirectory(dir=ROOT / ".artifacts/tmp")
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name)

    def test_the_fetcher_serves_exactly_the_registry_blob(self):
        blob = seed.registry_blob()
        self.assertEqual(git_blob_id(blob), seed.BLOB_ID)
        self.assertEqual(hashlib.sha256(blob).hexdigest(), seed.BLOB_SHA256)
        cited = subprocess.run(["git", "-C", str(ROOT), "show",
                                f"{seed.REGISTRY_COMMIT}:{seed.REGISTRY_PATH}"],
                               capture_output=True, check=True).stdout
        self.assertEqual(blob, cited)
        fetcher = seed.LocalFetcher(blob)
        self.assertEqual(asyncio.run(fetcher(seed.CORE_REGISTRY_URL, len(blob))), blob)
        with self.assertRaisesRegex(seed.SeedError, "unexpected URL"):
            asyncio.run(fetcher("https://example.invalid/registry.json", len(blob)))
        with self.assertRaisesRegex(seed.SeedError, "fetch limit"):
            asyncio.run(fetcher(seed.CORE_REGISTRY_URL, len(blob) - 1))

    def test_the_key_is_the_cited_committed_file(self):
        key_file = ROOT / seed.KEY_FILE
        self.assertEqual(seed.KEY_FILE, "docs/evidence/hamcp222/core-public-key.txt")
        self.assertEqual(seed.public_key(), key_file.read_text(encoding="utf-8").strip())
        record = json.loads((ROOT / seed.SEED_DIR / seed.RECORD).read_text(encoding="utf-8"))
        self.assertEqual(record["public_key"], {
            "file": seed.KEY_FILE, "sha256": hashlib.sha256(key_file.read_bytes()).hexdigest()})
        self.assertEqual(sr.engineering_options()["ha_core_release_registry_public_key"],
                         seed.public_key())

    def test_a_fresh_refresh_reproduces_the_committed_seed(self):
        result = seed.refresh_once(ROOT, self.work, now=lambda: NOW)
        committed = seed.committed()
        self.assertEqual(result["files"], committed["files"])
        record = seed.record_for(ROOT, result)
        self.assertEqual(record["files"], committed["record"]["files"])
        self.assertEqual(record["registry"], committed["record"]["registry"])
        self.assertEqual(record["lane_paths"], {
            "ha-core-release-registry-cache.json": "/data/ha-core-release-registry-cache.json",
            "ha-core-release-registry-cache.json.lifecycle":
                "/data/ha-core-release-registry-cache.json.lifecycle"})
        authority = result["authority"]
        self.assertEqual((authority["sequence"], authority["cache_status"],
                          authority["surface_denied"], authority["positive_authority_current"]),
                         (18, "valid", False, True))
        self.assertEqual(result["fetch"]["url"], seed.CORE_REGISTRY_URL)
        self.assertEqual(committed["record"]["known_difference"],
                         "same verification and write path, local transport instead of GitHub")

    def test_the_files_come_only_from_engineerings_write_path(self):
        writes = []
        original = seed.CoreReleaseRegistry._write_cache

        def counted(registry, journal):
            writes.append(journal.content_digest)
            return original(registry, journal)
        (self.work / "a").mkdir()
        with mock.patch.object(seed.CoreReleaseRegistry, "_write_cache", counted):
            seed.refresh_once(ROOT, self.work / "a", now=lambda: NOW)
        self.assertEqual(len(writes), 1)

        async def accepted_without_writing(registry):
            # Fetches like refresh() but writes nothing: the script must not fill the gap.
            await registry._fetcher(registry._binding.url, 32 * 1024 * 1024)
            return True
        (self.work / "b").mkdir()
        with mock.patch.object(seed.CoreReleaseRegistry, "refresh", accepted_without_writing):
            with self.assertRaisesRegex(seed.SeedError, r"left \[\]"):
                seed.refresh_once(ROOT, self.work / "b", now=lambda: NOW)

    def test_a_registry_that_does_not_verify_stops_without_a_seed(self):
        blob = bytearray(seed.registry_blob())
        index = blob.index(b'"sequence"')
        blob[index + 1] = ord("S")
        with mock.patch.object(seed, "registry_blob", return_value=bytes(blob)):
            with self.assertRaisesRegex(seed.SeedError, "did not accept"):
                seed.refresh_once(ROOT, self.work, now=lambda: NOW)
        self.assertFalse((self.work / "data" / seed.CACHE_NAME).exists())

    def test_the_committed_files_load_as_a_valid_cache_at_the_lane_names(self):
        committed = seed.committed()
        for name, data in committed["files"].items():
            self.assertEqual(committed["record"]["files"][name]["bytes"], len(data))
        data = self.work / "data"
        data.mkdir()
        for name, body in committed["files"].items():
            (data / name).write_bytes(body)

        async def unavailable(url, maximum):
            raise OSError("no network in this test")
        registry = seed.CoreReleaseRegistry(
            enabled=True, public_key=seed.public_key(), cache_path=data / seed.CACHE_NAME,
            fetcher=unavailable, now=lambda: NOW)
        snapshot = registry.snapshot()
        self.assertEqual((snapshot["cache_status"], snapshot["sequence"],
                          snapshot["surface_denied"]), ("valid", 18, False))
        self.assertIs(asyncio.run(registry.refresh()), False)
        snapshot = registry.snapshot()
        self.assertEqual((snapshot["cache_status"], snapshot["sequence"],
                          snapshot["surface_denied"]), ("valid", 18, False))
        self.assertEqual(sorted(p.name for p in data.iterdir()), sorted(seed.SEED_FILES))
        self.assertEqual((data / seed.WITNESS_NAME).read_bytes(),
                         committed["files"][seed.WITNESS_NAME])


if __name__ == "__main__":
    unittest.main()
