"""Frozen synthetic projections: no HA collection or dashboard rule execution."""

import asyncio
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.dashboard_analysis import contracts as c
from ha_mcp_engineering.dashboard_analysis.models import FrozenReport
from ha_mcp_engineering.dashboard_analysis.service import DashboardAnalysisService
from ha_mcp_engineering.request_context import begin_request, end_request


class Collector:
    def __init__(self, count=103):
        self.calls = 0
        self.generation = 1
        self.available = True
        self.count = count
        self.hook = None

    def authority(self):
        if not self.available:
            raise c.AnalysisError("authority_unavailable")
        return (self.generation, "synthetic-upstream-authority")

    async def collect(self, path):
        self.calls += 1
        if self.hook:
            await self.hook()
        header = {"model": c.MODEL, "requested_path": path, "canonical_path": path,
                  "coverage": {"references": "partial", "availability": "unassessed", "controls": "partial"}}
        items = tuple(c.canonical({"kind": "entity_reference", "pointer": f"/views/0/cards/{i}/entity",
                                  "entity_id": "sensor.test", "availability": "unassessed"})
                      for i in range(self.count))
        return FrozenReport(c.canonical(header), items, self.authority(), "")


class PaginationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.telemetry, self.context = begin_request()
        self.telemetry.caller_id = "synthetic-owner"
        self.addCleanup(end_request, self.context)
        self.collector = Collector()
        self.time = 0
        self.service = DashboardAnalysisService(self.collector, response_limit=32768, clock=lambda: self.time)

    async def test_full_reconstruction_limit_change_and_zero_recollection(self):
        first = await self.service.analyze(url_path="home", limit=1)
        items = first["items"][:]
        page = first
        while page["pagination"]["next_cursor"]:
            page = await self.service.analyze(url_path="home", limit=100, cursor=page["pagination"]["next_cursor"])
            self.assertEqual(page["header"], first["header"])
            self.assertEqual(page["report_digest"], first["report_digest"])
            self.assertLessEqual(len(c.canonical(page)) + c.ENVELOPE_RESERVE, c.PAGE_BYTES)
            items.extend(page["items"])
        self.assertEqual(len(items), 103)
        self.assertEqual(len({item["pointer"] for item in items}), 103)
        self.assertEqual(self.collector.calls, 1)
        self.assertEqual(first["header"]["coverage"]["controls"], "partial")

    async def test_repeated_continuation_is_identical(self):
        first = await self.service.analyze(url_path="home", limit=1)
        token = first["pagination"]["next_cursor"]
        a = await self.service.analyze(url_path="home", cursor=token)
        self.time += 1
        b = await self.service.analyze(url_path="home", cursor=token)
        self.assertEqual(a, b)

    async def test_wrong_path_caller_and_tampering_have_zero_reads(self):
        first = await self.service.analyze(url_path="home", limit=1)
        cursor = first["pagination"]["next_cursor"]
        for path, caller, token in (("other", "synthetic-owner", cursor),
                                    ("home", "other", cursor),
                                    ("home", "synthetic-owner", cursor[:-1] + ("0" if cursor[-1] != "0" else "1"))):
            self.telemetry.caller_id = caller
            with self.assertRaises(c.AnalysisError):
                await self.service.analyze(url_path=path, cursor=token)
        self.assertEqual(self.collector.calls, 1)

    async def test_expiration_and_authority_retirement_refuse_without_reads(self):
        first = await self.service.analyze(url_path="home", limit=1)
        token = first["pagination"]["next_cursor"]
        self.collector.available = False
        with self.assertRaises(c.AnalysisError) as error:
            await self.service.analyze(url_path="home", cursor=token)
        self.assertEqual(error.exception.reason, "authority_unavailable")
        self.collector.available = True
        self.time = c.TTL_SECONDS
        with self.assertRaises(c.AnalysisError) as error:
            await self.service.analyze(url_path="home", cursor=token)
        self.assertEqual(error.exception.reason, "snapshot_expired")
        self.assertEqual(self.collector.calls, 1)

    async def test_drift_during_collection_never_commits(self):
        async def drift():
            self.collector.generation += 1
        self.collector.hook = drift
        with self.assertRaises(c.AnalysisError):
            await self.service.analyze(url_path="home")
        self.assertEqual(self.service.snapshots, {})
        self.assertFalse(self.service.active)

    async def test_drift_after_worker_never_returns_page(self):
        first = await self.service.analyze(url_path="home", limit=1)
        original = c.worker
        async def drift(function, *args, **kwargs):
            result = await original(function, *args, **kwargs)
            self.collector.generation += 1
            return result
        with patch.object(c, "worker", drift), self.assertRaises(c.AnalysisError):
            await self.service.analyze(url_path="home", cursor=first["pagination"]["next_cursor"])
        self.assertEqual(self.collector.calls, 1)

    async def test_two_live_snapshots_no_eviction(self):
        first = await self.service.analyze(url_path="home", limit=1)
        await self.service.analyze(url_path="other")
        with self.assertRaises(c.AnalysisError) as error:
            await self.service.analyze(url_path="third")
        self.assertEqual(error.exception.reason, "capacity_busy")
        self.assertEqual(self.collector.calls, 2)
        await self.service.analyze(url_path="home", cursor=first["pagination"]["next_cursor"])

    async def test_inflight_refused_and_cancelled_collection_drained(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def hold():
            entered.set()
            await release.wait()
        self.collector.hook = hold
        task = asyncio.create_task(self.service.analyze(url_path="home"))
        await entered.wait()
        with self.assertRaises(c.AnalysisError):
            await self.service.analyze(url_path="other")
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.service.active)
        self.assertEqual(self.service.snapshots, {})

    async def test_cancelled_pure_worker_retains_ownership_until_done(self):
        entered, release = threading.Event(), threading.Event()
        def hold():
            entered.set()
            release.wait(2)
        task = asyncio.create_task(c.worker(hold))
        async with asyncio.timeout(3):
            while not entered.is_set():
                if task.done():
                    await task
                await asyncio.sleep(.001)
        task.cancel()
        await asyncio.sleep(.01)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_continuation_shares_capacity_and_cancellation_drains_before_release(self):
        first = await self.service.analyze(url_path="home", limit=1)
        token = first["pagination"]["next_cursor"]
        entered, release = threading.Event(), threading.Event()
        original = self.service._page
        def hold(*args):
            entered.set()
            release.wait(2)
            return original(*args)
        with patch.object(self.service, "_page", hold):
            task = asyncio.create_task(self.service.analyze(url_path="home", cursor=token))
            try:
                async with asyncio.timeout(3):
                    while not entered.is_set():
                        await asyncio.sleep(.001)
                for arguments in ({"url_path": "other"}, {"url_path": "home", "cursor": token}):
                    with self.assertRaises(c.AnalysisError) as error:
                        await self.service.analyze(**arguments)
                    self.assertEqual(error.exception.reason, "capacity_busy")
                task.cancel()
                await asyncio.sleep(.01)
                self.assertTrue(self.service.active)
                self.assertFalse(task.done())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertFalse(self.service.active)
        self.assertEqual(self.collector.calls, 1)
        await self.service.analyze(url_path="home", cursor=token)

    async def test_final_render_owns_capacity_and_cancelled_first_export_leaves_no_snapshot(self):
        entered, release = threading.Event(), threading.Event()
        def render(_result):
            entered.set()
            release.wait(2)
            return "synthetic-rendered-page"
        task = asyncio.create_task(self.service.analyze(url_path="home", renderer=render))
        try:
            async with asyncio.timeout(3):
                while not entered.is_set():
                    await asyncio.sleep(.001)
            with self.assertRaises(c.AnalysisError) as error:
                await self.service.analyze(url_path="other")
            self.assertEqual(error.exception.reason, "capacity_busy")
            task.cancel()
            await asyncio.sleep(.01)
            self.assertTrue(self.service.active)
            self.assertFalse(task.done())
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(self.service.active)
        self.assertEqual(self.service.snapshots, {})
        await self.service.analyze(url_path="other")

    async def test_page_cap_cannot_drop_items_or_create_empty_continuation(self):
        self.service.page_bytes = 1100
        page = await self.service.analyze(url_path="home", limit=100)
        self.assertLess(page["pagination"]["returned"], 100)
        self.assertGreater(page["pagination"]["returned"], 0)
        self.assertEqual(page["pagination"]["retained_items"], 103)
        tiny = DashboardAnalysisService(self.collector, response_limit=1024)
        with self.assertRaises(c.AnalysisError):
            await tiny.analyze(url_path="home")
        self.assertEqual(tiny.snapshots, {})

    async def test_anonymous_and_invalid_arguments_never_collect(self):
        with self.assertRaises(c.AnalysisError):
            await self.service.analyze(url_path="home", limit=True)
        self.telemetry.caller_id = "anonymous"
        with self.assertRaises(c.AnalysisError):
            await self.service.analyze(url_path="home")
        self.assertEqual(self.collector.calls, 0)

    async def test_unsafe_report_projection_never_commits(self):
        original = self.collector.collect
        async def unsafe(path):
            report = await original(path)
            header = json.loads(report.header)
            header["raw_config"] = "synthetic-secret"
            return FrozenReport(c.canonical(header), report.items, report.authority, "")
        self.collector.collect = unsafe
        with self.assertRaises(c.AnalysisError) as error:
            await self.service.analyze(url_path="home")
        self.assertNotIn("synthetic-secret", str(error.exception))
        self.assertEqual(self.service.snapshots, {})


if __name__ == "__main__":
    unittest.main()
