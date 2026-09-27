"""Frozen continuation, revocation, byte packing and collection concurrency."""

import asyncio
import copy
import json
import time
import unittest
from unittest.mock import patch

from test_integration_inspection_contract import TARGET, fixture, setup_service
from ha_mcp_engineering.integration_inspection import contracts as c
from ha_mcp_engineering.integration_inspection.models import InspectionError
from ha_mcp_engineering.integration_inspection.service import references
from ha_mcp_engineering.request_context import end_request


class PaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_pages_and_replay_preserve_fingerprint_no_recollection(self):
        service, client, _, _, token = setup_service()
        self.addCleanup(end_request, token)
        first = await service.inspect(alarm_entity_id=TARGET, limit=1)
        report, rows, hashes = first, [], set()
        while True:
            rows.extend(report["records"])
            hashes.add(report["pagination"]["snapshot_fingerprint"])
            refs = set(references(report["records"]))
            evidence = {(v["source_id"], v["pointer"]) for v in report["evidence_entries"]}
            self.assertEqual(refs, evidence)
            if not report["pagination"]["has_more"]:
                break
            cursor = report["pagination"]["next_cursor"]
            report = await service.inspect(alarm_entity_id=TARGET, limit=1, cursor=cursor)
        self.assertEqual(len(rows), first["pagination"]["total_retained_records"])
        self.assertEqual(len(hashes), 1)
        replay = await service.inspect(alarm_entity_id=TARGET, limit=1, cursor=first["pagination"]["next_cursor"])
        self.assertEqual(replay["records"], rows[1:2])
        self.assertEqual(len(client.calls), 9)

    async def test_cursor_binding_tampering_expiry_and_revocation(self):
        now = [0]
        service, client, core, telemetry, token = setup_service(clock=lambda: now[0])
        self.addCleanup(end_request, token)
        first = await service.inspect(alarm_entity_id=TARGET, limit=1)
        cursor = first["pagination"]["next_cursor"]
        for values in ({"limit": 2}, {"alarm_entity_id": "alarm_control_panel.synthetic_0"}, {"cursor": "tampered"}):
            with self.assertRaises(InspectionError):
                await service.inspect(**{**dict(alarm_entity_id=TARGET, limit=1, cursor=cursor), **values})
        telemetry.caller_id = "other_synthetic_caller"
        with self.assertRaises(InspectionError):
            await service.inspect(alarm_entity_id=TARGET, limit=1, cursor=cursor)
        telemetry.caller_id = "synthetic_authenticated_caller"
        core.available = False
        with self.assertRaises(InspectionError) as found:
            await service.inspect(alarm_entity_id=TARGET, limit=1, cursor=cursor)
        self.assertEqual(found.exception.reason, "authority_unavailable")
        core.available = True
        now[0] = c.SNAPSHOT_TTL
        with self.assertRaises(InspectionError) as found:
            await service.inspect(alarm_entity_id=TARGET, limit=1, cursor=cursor)
        self.assertEqual(found.exception.reason, "snapshot_expired")
        self.assertEqual(len(client.calls), 9)

    async def test_eviction_has_no_recollection(self):
        service, client, _, _, token = setup_service()
        self.addCleanup(end_request, token)
        first = await service.inspect(alarm_entity_id=TARGET, limit=1)
        for _ in range(8):
            await service.inspect(alarm_entity_id=TARGET, limit=1)
        self.assertEqual(len(service.snapshots), 8)
        self.assertLessEqual(sum(s.footprint for s in service.snapshots.values()), 16 * 1024 * 1024)
        with self.assertRaises(InspectionError):
            await service.inspect(alarm_entity_id=TARGET, limit=1, cursor=first["pagination"]["next_cursor"])
        self.assertEqual(len(client.calls), 81)

    async def test_byte_packing_never_drops_emitted_evidence_or_stalls(self):
        service, client, _, _, token = setup_service(limit=14_000)
        self.addCleanup(end_request, token)
        report = await service.inspect(alarm_entity_id=TARGET, limit=50)
        seen = set()
        while True:
            self.assertLessEqual(len(c.canonical(report)), 14_000 - 2048)
            offset = report["pagination"]["offset"]
            self.assertNotIn(offset, seen)
            seen.add(offset)
            self.assertEqual(set(references(report["records"])), {(e["source_id"], e["pointer"]) for e in report["evidence_entries"]})
            if not report["pagination"]["has_more"]:
                break
            report = await service.inspect(alarm_entity_id=TARGET, limit=50, cursor=report["pagination"]["next_cursor"])
        self.assertGreater(len(seen), 1)

    async def test_too_small_metadata_returns_fixed_failure(self):
        service, _, _, _, token = setup_service(limit=3000)
        self.addCleanup(end_request, token)
        with self.assertRaises(InspectionError) as found:
            await service.inspect(alarm_entity_id=TARGET)
        self.assertEqual(found.exception.reason, "output_budget_unavailable")

    async def test_snapshot_clipping_preserves_unknown_omissions_and_prior_reasons(self):
        service, _, _, _, token = setup_service()
        self.addCleanup(end_request, token)
        report = await service.provider.collect(TARGET)
        report["pagination"]["omitted_records"] = None
        report["truncation"] = {"occurred": True, "reasons": ["structural_budget"], "omitted_count": None}
        before = len(report["records"])
        with patch.object(c, "SNAPSHOT_BYTES", 24_000):
            _, snapshot = await service._retain(report, service._binding(TARGET, "alarmo", 25))
        frozen = json.loads(snapshot.encoded)
        self.assertLess(len(frozen["records"]), before)
        self.assertIsNone(frozen["pagination"]["omitted_records"])
        self.assertIsNone(frozen["truncation"]["omitted_count"])
        self.assertEqual(frozen["truncation"]["reasons"], ["output_bytes", "structural_budget"])
        self.assertLessEqual(snapshot.footprint, 24_000)

    async def test_oversized_whole_row_omitted_without_cursor_stall(self):
        service, _, _, _, token = setup_service(limit=12_000)
        self.addCleanup(end_request, token)
        first = await service.inspect(alarm_entity_id=TARGET, limit=50)
        member_count = first["membership"]["configured_members_retained"]
        page, seen, omissions = first, set(), []
        while True:
            self.assertNotIn(page["pagination"]["offset"], seen)
            seen.add(page["pagination"]["offset"])
            omissions.append(page["truncation"]["omitted_count"])
            self.assertEqual(page["membership"]["configured_members_retained"], member_count)
            if not page["pagination"]["has_more"]:
                break
            page = await service.inspect(alarm_entity_id=TARGET, limit=50, cursor=page["pagination"]["next_cursor"])
        self.assertTrue(any(count and count > 0 for count in omissions))

    async def test_two_collectors_third_rejected_and_cancellation_releases_capacity(self):
        service, client, _, _, token = setup_service()
        self.addCleanup(end_request, token)
        blocked = asyncio.Event()
        async def hold(kind, kwargs):
            await blocked.wait()
        client.hook = hold
        tasks = [asyncio.create_task(service.inspect(alarm_entity_id=TARGET)) for _ in range(2)]
        await asyncio.sleep(0)
        self.assertEqual(service.active, 2)
        with self.assertRaises(InspectionError) as found:
            await service.inspect(alarm_entity_id=TARGET)
        self.assertEqual(found.exception.reason, "capacity_busy")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.assertEqual(service.active, 0)
        self.assertEqual(service.snapshots, {})

    async def test_generation_change_after_read_is_not_published_or_cached(self):
        service, client, core, _, token = setup_service()
        self.addCleanup(end_request, token)
        async def drift(kind, kwargs):
            if kind is c.ReadKind.SENSORS:
                core.generation += 1
        client.hook = drift
        with self.assertRaises(InspectionError) as found:
            await service.inspect(alarm_entity_id=TARGET)
        self.assertEqual(found.exception.reason, "identity_drift")
        self.assertEqual(service.snapshots, {})
        self.assertEqual(len(client.calls), 5)

    async def test_output_omissions_are_frozen_before_first_page_and_replay(self):
        service, client, _, _, token = setup_service(limit=16_000)
        self.addCleanup(end_request, token)
        first = await service.inspect(alarm_entity_id=TARGET, limit=50)
        pages, cursors, records = [], [], []
        page = first
        while True:
            pages.append(page)
            records.extend(page["records"])
            self.assertEqual(page["assessment"], "partial")
            self.assertEqual(page["truncation"], {"occurred": True, "reasons": ["output_bytes"], "omitted_count": 1})
            self.assertEqual(page["pagination"]["omitted_records"], 1)
            self.assertEqual(page["pagination"]["total_retained_records"], 15)
            self.assertEqual(page["membership"]["configured_members_retained"], 4)
            for key in ("sources", "gaps", "membership"):
                self.assertEqual(page[key], first[key])
            self.assertEqual(page["pagination"]["snapshot_fingerprint"], first["pagination"]["snapshot_fingerprint"])
            self.assertLessEqual(len(c.canonical(page)), 16_000 - 2048)
            if not page["pagination"]["has_more"]:
                break
            cursors.append(page["pagination"]["next_cursor"])
            page = await service.inspect(alarm_entity_id=TARGET, limit=50, cursor=cursors[-1])
        self.assertEqual(len(records), 15)
        sensor_ids = [r["configured_entity_id"] for r in records if r["kind"] == "sensor"]
        self.assertNotIn("binary_sensor.synthetic_disabled", sensor_ids)
        self.assertIn("binary_sensor.synthetic_door", sensor_ids)  # Smaller rows after the omitted row survive.
        for cursor, original in zip(cursors, pages[1:]):
            replay = await service.inspect(alarm_entity_id=TARGET, limit=50, cursor=cursor)
            for key in ("records", "pagination", "truncation", "assessment", "sources", "gaps"):
                self.assertEqual(replay[key], original[key])
        self.assertEqual(len(client.calls), 9)

    async def test_maximum_report_fitting_is_responsive_and_preserves_counts(self):
        data = fixture()
        prototype = data["sensors"]["binary_sensor.synthetic_door"]
        identifiers = ["binary_sensor.synthetic_" + "a" * 96 + str(i).zfill(4) for i in range(c.MAX_SENSORS)]
        data["sensors"], data["sensor_groups"] = {}, {}
        data["entity_registry"] = {TARGET: {"entity_id": TARGET, "id": "synthetic_master_registry", "disabled_by": None}}
        for index, entity in enumerate(identifiers):
            group = "synthetic_group_" + str(index // 4)
            data["sensors"][entity] = {**copy.deepcopy(prototype), "entity_id": entity, "area": "0", "group": group}
            data["entity_registry"][entity] = {"entity_id": entity, "id": "synthetic_registry_" + str(index), "disabled_by": None}
        for index in range(c.MAX_GROUPS):
            group = "synthetic_group_" + str(index)
            data["sensor_groups"][group] = {"group_id": group, "entities": identifiers[4 * index:4 * index + 4], "timeout": 0, "event_count": 2}
        service, client, _, _, token = setup_service(data)
        self.addCleanup(end_request, token)
        gaps = []
        async def heartbeat():
            last = time.monotonic()
            while True:
                await asyncio.sleep(.01)
                now = time.monotonic()
                gaps.append(now - last)
                last = now
        ticker = asyncio.create_task(heartbeat())
        await asyncio.sleep(0)
        try:
            report = await service.inspect(alarm_entity_id=TARGET, limit=50)
            await asyncio.sleep(.02)
        finally:
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)
        self.assertLess(max(gaps), 2, "Fitting must not monopolize the event loop.")
        self.assertEqual(report["membership"]["configured_members_retained"], 512)
        self.assertEqual(report["assessment"], "partial")
        self.assertIn("output_bytes", report["truncation"]["reasons"])
        snapshot = next(iter(service.snapshots.values()))
        frozen = json.loads(snapshot.encoded)
        self.assertLessEqual(snapshot.footprint, c.SNAPSHOT_BYTES)
        self.assertEqual(len(frozen["records"]) + frozen["pagination"]["omitted_records"], 651)
        self.assertEqual(set(references(frozen["records"])), {(e["source_id"], e["pointer"]) for e in frozen["evidence_entries"]})
        self.assertEqual(len(client.calls), 9)
        self.assertEqual(service.active, 0)

    async def test_total_deadline_covers_retention(self):
        service, client, _, _, token = setup_service()
        self.addCleanup(end_request, token)
        retain = service._retain
        async def late_retention(*args):
            await asyncio.sleep(.2)
            return await retain(*args)
        with patch.object(c, "COLLECTION_SECONDS", .1), patch.object(service, "_retain", late_retention):
            with self.assertRaises(InspectionError) as found:
                await service.inspect(alarm_entity_id=TARGET)
        self.assertEqual(found.exception.reason, "timeout")
        self.assertEqual(service.active, 0)
        self.assertEqual(service.snapshots, {})
        self.assertEqual(service.cursors, {})
        self.assertEqual(len(client.calls), 9)

    async def test_deadline_after_packing_removes_unpublished_snapshot(self):
        service, client, _, _, token = setup_service()
        self.addCleanup(end_request, token)
        page = service._page
        def late_page(*args, **kwargs):
            result = page(*args, **kwargs)
            # Synchronous packing cannot return success after its deadline,
            # even before the event loop gets to deliver timeout cancellation.
            time.sleep(.12)
            return result
        with patch.object(c, "COLLECTION_SECONDS", .1), patch.object(service, "_page", late_page):
            with self.assertRaises(InspectionError) as found:
                await service.inspect(alarm_entity_id=TARGET)
        self.assertEqual(found.exception.reason, "timeout")
        self.assertEqual(service.active, 0)
        self.assertEqual(service.snapshots, {})
        self.assertEqual(service.cursors, {})
        self.assertEqual(len(client.calls), 9)

    async def test_cancellation_during_fitting_releases_capacity_without_snapshot(self):
        service, client, _, _, token = setup_service()
        self.addCleanup(end_request, token)
        entered = asyncio.Event()
        async def blocked_retention(*args):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(service, "_retain", blocked_retention):
            task = asyncio.create_task(service.inspect(alarm_entity_id=TARGET))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(service.active, 0)
        self.assertEqual(service.snapshots, {})
        self.assertEqual(len(client.calls), 9)
