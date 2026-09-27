"""Frozen continuation, revocation, byte packing and collection concurrency."""

import asyncio
import json
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
            _, snapshot = service._retain(report, service._binding(TARGET, "alarmo", 25))
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
