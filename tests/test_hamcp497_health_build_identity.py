"""HAMCP-497: get_server_health names the running build and the registry refresh.

``server_info`` reported ``build_sha`` and ``build_dirty`` but the health
snapshot did not, so a health check alone could not name the running image.
The release registry snapshot reported the refresh outcome but not when the
last refresh started or how often it runs.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))
sys.path.insert(0, str(ROOT / "tests"))

from ha_mcp_engineering import secret_paths  # noqa: E402
from ha_mcp_engineering.application import create_application  # noqa: E402
from ha_mcp_engineering.ha_core_readmission import CoreRuntime  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry  # noqa: E402
from ha_mcp_engineering.ha_mcp_readmission.registry import REFRESH_INTERVAL_SECONDS  # noqa: E402
from ha_mcp_engineering.health import HealthRegistry  # noqa: E402
from ha_mcp_engineering.tools import compatibility  # noqa: E402

from core_registry_fixtures import CoreSigner, NOW, ProjectedCoreSource  # noqa: E402
from test_beta_observability import settings  # noqa: E402


SHA = "0123456789abcdef0123456789abcdef01234567"


def run(coro):
    return asyncio.run(coro)


class HealthBuildIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        create_application(settings(str(Path(self.tmp.name) / "audit.jsonl")))

    def health(self):
        payload = json.loads(run(compatibility.get_server_health(check_ha=False)))
        self.assertTrue(payload["success"])
        return payload["data"]

    def test_health_reports_the_build_identity_server_info_reports(self):
        info = json.loads(run(compatibility.server_info(check_ha=False)))["data"]
        server = self.health()["server"]
        self.assertEqual(server["build_sha"], info["server"]["build_sha"])
        self.assertEqual(server["build_dirty"], info["server"]["build_dirty"])

    def test_the_running_build_values_flow_through(self):
        for sha, dirty in ((SHA, False), (SHA, True), ("unknown", "unknown")):
            with self.subTest(sha=sha, dirty=dirty), patch.object(
                compatibility, "BUILD_SHA", sha
            ), patch.object(compatibility, "BUILD_DIRTY", dirty):
                server = self.health()["server"]
                self.assertEqual(server["build_sha"], sha)
                self.assertEqual(server["build_dirty"], dirty)
                self.assertIs(type(server["build_dirty"]), type(dirty))

    def test_only_the_two_identity_fields_are_added(self):
        server = self.health()["server"]
        self.assertEqual(
            set(server), {"id", "name", "version", "build_sha", "build_dirty"}
        )
        self.assertNotIn("build_time", server)

    def test_the_shared_health_snapshot_is_not_changed(self):
        # The registry snapshot itself stays free of provenance (RC1); only
        # the get_server_health tool adds the build identity.
        snapshot = HealthRegistry().snapshot({"checked": False, "status": "not_checked"})
        self.assertNotIn("build_sha", snapshot["server"])
        self.assertIn("build_sha", self.health()["server"])
        self.assertNotIn("build_sha", HealthRegistry().snapshot(
            {"checked": False, "status": "not_checked"}
        )["server"])


class RegistryRefreshFieldTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / "core.json"
        self.signer = CoreSigner()
        self.clock = NOW
        self.raw = self.signer.journal_raw()

        async def fetch(_url, _maximum):
            if isinstance(self.raw, Exception):
                raise self.raw
            return self.raw

        self.registry = CoreReleaseRegistry(
            enabled=True, public_key=self.signer.public_key_base64,
            cache_path=self.cache, fetcher=fetch, now=lambda: self.clock,
        )

    async def test_no_refresh_yet_reports_none_and_the_interval(self):
        snapshot = self.registry.snapshot()
        self.assertIsNone(snapshot["last_refresh_at"])
        self.assertEqual(snapshot["refresh_interval_seconds"], REFRESH_INTERVAL_SECONDS)
        self.assertEqual(REFRESH_INTERVAL_SECONDS, 21_600.0)

    async def test_a_refresh_records_its_start_in_utc(self):
        # A non-UTC clock with a fraction of a second is reported in UTC,
        # to the second.
        self.clock = (NOW + timedelta(seconds=5, microseconds=750000)).astimezone(
            timezone(timedelta(hours=-6))
        )
        self.assertTrue(await self.registry.refresh())
        snapshot = self.registry.snapshot()
        expected = (NOW + timedelta(seconds=5)).astimezone(timezone.utc).replace(microsecond=0)
        self.assertEqual(snapshot["last_refresh_at"], expected.strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertTrue(snapshot["last_refresh_at"].endswith("Z"))
        self.assertEqual(snapshot["refresh_status"], "accepted")

    async def test_a_failed_refresh_still_records_the_attempt(self):
        self.raw = OSError("offline")
        self.assertFalse(await self.registry.refresh())
        snapshot = self.registry.snapshot()
        self.assertEqual(snapshot["last_refresh_at"], NOW.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertNotEqual(snapshot["refresh_status"], "accepted")

    async def test_a_later_refresh_moves_the_time(self):
        self.assertTrue(await self.registry.refresh())
        first = self.registry.snapshot()["last_refresh_at"]
        self.clock = NOW + timedelta(hours=6)
        await self.registry.refresh()
        second = self.registry.snapshot()["last_refresh_at"]
        self.assertNotEqual(first, second)
        self.assertEqual(
            datetime.strptime(second, "%Y-%m-%dT%H:%M:%SZ")
            - datetime.strptime(first, "%Y-%m-%dT%H:%M:%SZ"),
            timedelta(hours=6),
        )

    async def test_an_invalid_clock_reports_no_time(self):
        self.clock = datetime(2026, 11, 25, 9, 30)  # naive
        await self.registry.refresh()
        self.assertIsNone(self.registry.snapshot()["last_refresh_at"])

    async def test_a_disabled_registry_reports_neither(self):
        disabled = CoreReleaseRegistry(enabled=False, public_key="not-used", cache_path=self.cache)
        self.assertFalse(await disabled.refresh())
        snapshot = disabled.snapshot()
        self.assertIsNone(snapshot["last_refresh_at"])
        self.assertIsNone(snapshot["refresh_interval_seconds"])

    async def test_core_health_projection_carries_the_fields(self):
        runtime = CoreRuntime()
        runtime.configure(
            object(), source=ProjectedCoreSource(self.registry, "2026.9.2"),
            release_registry=self.registry,
        )
        await runtime.reconcile_once("startup")
        registry = runtime.health_projection()["release_registry"]
        self.assertEqual(registry["last_refresh_at"], NOW.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(registry["refresh_interval_seconds"], REFRESH_INTERVAL_SECONDS)


def tearDownModule():
    # HAMCP-288: create_application installs the process-wide secret-path
    # redaction; reset it so it does not reach another module in the same
    # test process.
    secret_paths.reset()


if __name__ == "__main__":
    unittest.main()
