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
        replay = await service.inspect(alarm_entity_id=TARGET, limit=1, cursor=cursor)
        self.assertEqual(replay["pagination"]["snapshot_fingerprint"], first["pagination"]["snapshot_fingerprint"])
        self.assertEqual(len(service.snapshots), 1)
        core.available = False
        with self.assertRaises(InspectionError) as found:
            await service.inspect(alarm_entity_id=TARGET, limit=1, cursor=cursor)
        self.assertEqual(found.exception.reason, "authority_unavailable")
        self.assertEqual(service.snapshots, {})
        self.assertEqual(service.cursors, {})
        core.available = True
        with self.assertRaises(InspectionError) as found:
            await service.inspect(alarm_entity_id=TARGET, limit=1, cursor=cursor)
        self.assertEqual(found.exception.reason, "invalid_cursor")
        self.assertEqual(len(client.calls), 9)
        fresh = await service.inspect(alarm_entity_id=TARGET, limit=1)
        cursor = fresh["pagination"]["next_cursor"]
        now[0] = c.SNAPSHOT_TTL
        with self.assertRaises(InspectionError) as found:
            await service.inspect(alarm_entity_id=TARGET, limit=1, cursor=cursor)
        self.assertEqual(found.exception.reason, "snapshot_expired")
        self.assertEqual(len(client.calls), 18)

    async def test_generation_change_and_authentication_loss_clear_old_snapshots(self):
        for mode in ("generation", "authentication"):
            with self.subTest(mode=mode):
                service, client, core, _, token = setup_service()
                try:
                    first = await service.inspect(alarm_entity_id=TARGET, limit=1)
                    self.assertTrue(service.snapshots)
                    if mode == "generation":
                        core.generation += 1
                        arguments = {"cursor": first["pagination"]["next_cursor"]}
                    else:
                        client.values["manifest"] = InspectionError("access_denied", authority_lost=True)
                        arguments = {}
                    with self.assertRaises(InspectionError) as found:
                        await service.inspect(alarm_entity_id=TARGET, limit=1, **arguments)
                    self.assertEqual(found.exception.reason, "invalid_cursor" if mode == "generation" else "access_denied")
                    self.assertEqual(service.snapshots, {})
                    self.assertEqual(service.cursors, {})
                    self.assertEqual(service.active, 0)
                    self.assertEqual(len(client.calls), 9 if mode == "generation" else 10)
                finally:
                    end_request(token)

    async def test_concurrent_authority_loss_fences_pending_captures_and_allows_recovery(self):
        for stage in ("before_fitting", "during_fitting", "before_publication"):
            for recovery_before_resume in (False, True):
                with self.subTest(stage=stage, recovery_before_resume=recovery_before_resume):
                    service, client, _, _, token = setup_service()
                    entered, release = asyncio.Event(), asyncio.Event()
                    collector = None
                    try:
                        warm = await service.inspect(alarm_entity_id=TARGET, limit=1)
                        manifest = client.values["manifest"]
                        retain, sleep = service._retain, asyncio.sleep

                        async def pause():
                            entered.set()
                            await release.wait()

                        async def held_retain(report, binding):
                            if asyncio.current_task() is collector and stage == "before_fitting":
                                await pause()
                            return await retain(report, binding)

                        async def held_sleep(delay, *args, **kwargs):
                            # Suspend at actual cooperative boundaries: fitting
                            # before insertion, or the first yield after insertion.
                            if asyncio.current_task() is collector and not entered.is_set():
                                if stage == "during_fitting" or (stage == "before_publication" and len(service.snapshots) == 2):
                                    await pause()
                            return await sleep(delay, *args, **kwargs)

                        with patch.object(service, "_retain", held_retain), patch("asyncio.sleep", held_sleep):
                            collector = asyncio.create_task(service.inspect(alarm_entity_id=TARGET, limit=1))
                            await asyncio.wait_for(entered.wait(), 2)
                            self.assertEqual(len(client.calls), 18)
                            client.values["manifest"] = InspectionError("access_denied", authority_lost=True)
                            with self.assertRaises(InspectionError) as denied:
                                await service.inspect(alarm_entity_id=TARGET, limit=1)
                            self.assertEqual(denied.exception.reason, "access_denied")
                            self.assertEqual(service.snapshots, {})
                            self.assertEqual(service.cursors, {})
                            self.assertEqual(service.active, 1)
                            with self.assertRaises(InspectionError) as expired:
                                await service.inspect(alarm_entity_id=TARGET, limit=1, cursor=warm["pagination"]["next_cursor"])
                            self.assertEqual(expired.exception.reason, "invalid_cursor")
                            self.assertEqual(len(client.calls), 19)

                            recovered = None
                            if recovery_before_resume:
                                client.values["manifest"] = manifest
                                recovered = await service.inspect(alarm_entity_id=TARGET, limit=1)
                                self.assertEqual(recovered["membership"]["configured_members_retained"], 4)
                            calls_before_resume = len(client.calls)
                            release.set()
                            with self.assertRaises(InspectionError) as stale:
                                await collector
                            self.assertEqual(stale.exception.reason, "authority_unavailable")
                            self.assertEqual(len(client.calls), calls_before_resume)
                            self.assertEqual(service.active, 0)
                            if recovered is None:
                                self.assertEqual(service.snapshots, {})
                                self.assertEqual(service.cursors, {})
                                client.values["manifest"] = manifest
                                recovered = await service.inspect(alarm_entity_id=TARGET, limit=1)
                            self.assertEqual(len(service.snapshots), 1)
                            continued = await service.inspect(alarm_entity_id=TARGET, limit=1,
                                cursor=recovered["pagination"]["next_cursor"])
                            self.assertEqual(continued["pagination"]["snapshot_fingerprint"],
                                             recovered["pagination"]["snapshot_fingerprint"])
                            self.assertEqual(continued["membership"]["configured_members_retained"], 4)
                            self.assertEqual(len(client.calls), 28)
                    finally:
                        release.set()
                        if collector is not None:
                            if not collector.done():
                                collector.cancel()
                            await asyncio.gather(collector, return_exceptions=True)
                        end_request(token)

    async def test_authority_replacement_preserves_new_snapshot_after_stale_capture(self):
        for warm_cache in (False, True):
            for stage in ("before_fitting", "during_fitting", "before_publication"):
                for replacement in ("generation", "version", "both"):
                    with self.subTest(warm_cache=warm_cache, stage=stage, replacement=replacement):
                        service, client, core, _, token = setup_service()
                        entered, release = asyncio.Event(), asyncio.Event()
                        collector = None
                        try:
                            warm = await service.inspect(alarm_entity_id=TARGET, limit=1) if warm_cache else None
                            initial_epoch = service.invalidation_generation
                            retain, sleep = service._retain, asyncio.sleep

                            async def pause():
                                entered.set()
                                await release.wait()

                            async def held_retain(report, binding):
                                if asyncio.current_task() is collector and stage == "before_fitting":
                                    await pause()
                                return await retain(report, binding)

                            async def held_sleep(delay, *args, **kwargs):
                                if asyncio.current_task() is collector and not entered.is_set():
                                    if stage == "during_fitting" or (stage == "before_publication"
                                            and len(service.snapshots) == int(warm_cache) + 1):
                                        await pause()
                                return await sleep(delay, *args, **kwargs)

                            with patch.object(service, "_retain", held_retain), patch("asyncio.sleep", held_sleep):
                                collector = asyncio.create_task(service.inspect(alarm_entity_id=TARGET, limit=1))
                                await asyncio.wait_for(entered.wait(), 2)
                                self.assertEqual(len(client.calls), 9 * (int(warm_cache) + 1))
                                if replacement in ("generation", "both"):
                                    core.generation += 1
                                if replacement in ("version", "both"):
                                    # Synthetic admitted authority; not a new real-Core contract.
                                    core.current_observation.version = "2026.9.2"
                                recovered = await service.inspect(alarm_entity_id=TARGET, limit=1)
                                self.assertEqual(recovered["membership"]["configured_members_retained"], 4)
                                self.assertEqual(service.invalidation_generation, initial_epoch + 1)
                                snapshots_before = tuple(service.snapshots)
                                cursors_before = dict(service.cursors)
                                self.assertEqual(len(snapshots_before), 1)
                                self.assertTrue(cursors_before)
                                self.assertEqual(service.active, 1)
                                expected_calls = 9 * (int(warm_cache) + 2)
                                self.assertEqual(len(client.calls), expected_calls)

                                release.set()
                                with self.assertRaises(InspectionError) as stale:
                                    await collector
                                self.assertEqual(stale.exception.reason, "authority_unavailable")
                                self.assertEqual(service.active, 0)
                                self.assertEqual(tuple(service.snapshots), snapshots_before)
                                self.assertEqual(service.cursors, cursors_before)
                                if warm is not None:
                                    with self.assertRaises(InspectionError) as old:
                                        await service.inspect(alarm_entity_id=TARGET, limit=1,
                                            cursor=warm["pagination"]["next_cursor"])
                                    self.assertEqual(old.exception.reason, "invalid_cursor")
                                continued = await service.inspect(alarm_entity_id=TARGET, limit=1,
                                    cursor=recovered["pagination"]["next_cursor"])
                                self.assertEqual(continued["pagination"]["snapshot_fingerprint"],
                                                 recovered["pagination"]["snapshot_fingerprint"])
                                self.assertEqual(continued["pagination"]["offset"], 1)
                                self.assertTrue(continued["records"])
                                self.assertEqual(continued["membership"]["configured_members_retained"], 4)
                                self.assertEqual(len(client.calls), expected_calls)
                                self.assertEqual(service.invalidation_generation, initial_epoch + 1)
                        finally:
                            release.set()
                            if collector is not None:
                                if not collector.done():
                                    collector.cancel()
                                await asyncio.gather(collector, return_exceptions=True)
                            end_request(token)

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
        collect = service.provider.collect
        async def deterministic_collect(target):
            report = await collect(target)
            # This is an exact-byte packing fixture, not a latency test. Timing
            # digit widths must not move the borderline row across its budget.
            for source in report["sources"]:
                source["duration_ms"] = 0.0
            return report
        service.provider.collect = deterministic_collect
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
