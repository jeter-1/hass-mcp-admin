"""HAMCP-371 P3 (amended): Core 2026.11.0 authority from the committed seed, offline.

Engineering's CoreReleaseRegistry loads the seed as the lane delivers it, with a
fetcher that always fails; no network, container or Home Assistant is used.  The
tests skip when the registry blob is not in the local object store.
"""

from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "docs/evidence/hamcp371/sr/p3_core_authority.py"
SPEC = importlib.util.spec_from_file_location("hamcp371_p3_core_authority", SCRIPT)
p3 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p3)
HAVE_BLOB = subprocess.run(
    ["git", "-C", str(ROOT), "cat-file", "-e", p3.harness.CORE_SEED_BLOB],
    capture_output=True).returncode == 0


@unittest.skipUnless(HAVE_BLOB, "the registry blob is not in the local object store")
class P3CoreAuthorityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_committed_seed_authorizes_2026_11_0_through_sequence_18(self):
        result = p3.p3(ROOT, self.workdir)
        self.assertEqual(result["result"], "PASS", result["failures"])
        for reading in (result["at_load"], result["after_failed_refresh"]):
            self.assertEqual(reading["sequence"], 18)
            self.assertEqual(reading["snapshot"]["cache_status"], "valid")
            self.assertFalse(reading["surface_denied"])
            self.assertTrue(reading["entry"]["image_index_digest"].startswith("sha256:c6c1ca99"))
        self.assertIs(result["refresh_returned"], False)
        self.assertEqual(result["after_failed_refresh"]["snapshot"]["last_failure_reason"],
                         "registry_unavailable")
        self.assertEqual(result["files_after_refresh"], result["files_delivered"])

    def test_a_seed_without_its_witness_fails(self):
        deliver = p3.lane_data

        def without_witness(workdir, seed):
            data = deliver(workdir, seed)
            (data / p3.harness.CORE_SEED_FILES[1]).unlink()
            return data

        with mock.patch.object(p3, "lane_data", without_witness):
            result = p3.p3(ROOT, self.workdir)
        self.assertEqual(result["result"], "FAIL")
        self.assertIn("at load: cache_status is invalid", result["failures"])
        self.assertTrue(result["at_load"]["surface_denied"])

    def test_the_fetcher_never_returns_bytes(self):
        fetcher = p3.NoTransport()
        with self.assertRaises(p3.aiohttp.ClientConnectionError):
            p3.asyncio.run(fetcher("https://example.invalid/", 1))
        self.assertEqual(len(fetcher.calls), 1)


if __name__ == "__main__":
    unittest.main()
