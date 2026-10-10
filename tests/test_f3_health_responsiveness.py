"""Cooperative deep health using real writers and disposable synthetic history."""
from __future__ import annotations

import asyncio
from collections import Counter
import gc
import json
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

from tests import test_f3_orphan_child_recovery as fixtures
from tests import test_hamcp135_governed_rollback as inverse_fixtures
from ha_mcp_engineering.errors import ErrorCode, GovernanceError
from ha_mcp_engineering.f3_runtime.health_scan import HealthScanFence
from ha_mcp_engineering.f3_runtime import repository as child_storage
from ha_mcp_engineering.governance.task_storage import ExecutionTaskStorageError


class ChildCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixtures.OrphanChildRecoveryTests()
        await self.fixture.asyncSetUp()
        self.runtime = self.fixture.runtime
        self.children = self.runtime.children

    async def asyncTearDown(self):
        await self.fixture.asyncTearDown()
        self.fixture.doCleanups()

    def fence(self):
        return HealthScanFence(((ErrorCode.EXECUTION_TASK_STORAGE_ERROR,
                                lambda: HealthScanFence.stamp(self.children.root)),))

    async def test_reads_each_parent_envelope_and_manifest_once_and_yields_throughout(self):
        self.fixture._populate_terminal_history(task_count=200, declarations_per_task=3)
        parents = self.fixture.service.task_repository
        before_parents = parents.records_deserialized
        counts = Counter()
        boundary_ticks = []
        ticks = []
        wall_gaps, cpu_gaps, gc_events = [], [], []
        owner = threading.get_ident()
        gc_started = {}
        async def pulse():
            wall, cpu = time.perf_counter(), time.thread_time()
            while True:
                await asyncio.sleep(0)
                now, current_cpu = time.perf_counter(), time.thread_time()
                ticks.append(now)
                wall_gaps.append(now - wall)
                cpu_gaps.append(current_cpu - cpu)
                wall, cpu = now, current_cpu
        def observe_gc(phase, info):
            thread = threading.get_ident()
            if phase == 'start':
                gc_started[thread] = (time.perf_counter(), time.thread_time())
            elif thread in gc_started:
                wall, cpu = gc_started.pop(thread)
                if len(gc_events) < 64:
                    gc_events.append({'generation': info['generation'],
                                      'event_loop_thread': thread == owner,
                                      'wall_seconds': time.perf_counter() - wall,
                                      'thread_cpu_seconds': time.thread_time() - cpu})
        original = Path.read_text
        def read(path, *args, **kwargs):
            if path.parent in {self.children.root, parents.root} and path.suffix == '.json':
                counts[path.name] += 1
                boundary_ticks.append(len(ticks))
            return original(path, *args, **kwargs)
        heartbeat = asyncio.create_task(pulse())
        await asyncio.sleep(0)
        gc.callbacks.append(observe_gc)
        started, cpu = time.perf_counter(), time.thread_time()
        try:
            with patch.object(Path, 'read_text', read):
                summary = await self.fixture.service.async_health_summary()
                data = summary["f3"]
        finally:
            gc.callbacks.remove(observe_gc)
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        self.assertEqual(data['nonterminal_execution_count'], 0)
        self.assertEqual(parents.records_deserialized - before_parents, 200)
        self.assertEqual(set(counts.values()), {1})
        self.assertEqual(sum(counts.values()), 1000)
        self.assertTrue(all(right > left for left, right in zip(boundary_ticks, boundary_ticks[1:])))
        self.assertFalse(any(call[0] == 'write' for call in self.fixture.gateway.calls))
        print('HEALTH_MEASUREMENT ' + json.dumps({
            'fixture_tasks': 200, 'children_per_parent': 3,
            'parent_reads': parents.records_deserialized - before_parents,
            'total_record_reads': sum(counts.values()), 'heartbeat_ticks': len(ticks),
            'wall_seconds': time.perf_counter() - started,
            'thread_cpu_seconds': time.thread_time() - cpu,
            'maximum_loop_gap_seconds': max(wall_gaps),
            'maximum_thread_cpu_gap_seconds': max(cpu_gaps),
            'gc_state': {'enabled': gc.isenabled(), 'thresholds': gc.get_threshold()},
            'gc_events': gc_events,
        }, sort_keys=True))

    async def test_complete_f3_projection_matches_sync_and_reads_parents_once(self):
        self.fixture._populate_terminal_history(task_count=12, declarations_per_task=4)
        self.fixture._populate_legacy_nonterminal_prefix(task_count=3)
        expected = self.runtime.health()
        parents = self.fixture.service.task_repository
        before = parents.records_deserialized
        with patch.object(parents, "get", wraps=parents.get) as get:
            actual = await self.runtime.async_health()
        self.assertEqual(actual, expected)
        self.assertEqual(parents.records_deserialized - before, 15)
        self.assertEqual(get.call_count, 0)
        self.assertEqual(actual["legacy_task_count"], 3)
        self.assertEqual(actual["legacy_active_task_count"], 3)

    async def test_damaged_task_navigation_is_rebuilt_from_one_collection(self):
        self.fixture._populate_terminal_history(task_count=12, declarations_per_task=2)
        parents = self.fixture.service.task_repository
        parents._ordered_keys.clear()
        before = parents.records_deserialized
        await self.runtime.async_health()
        self.assertEqual(parents.records_deserialized - before, 12)
        self.assertEqual(parents.navigation_metrics()["record_count"], 12)
        self.assertEqual(len(parents._ordered_keys), 12)

    async def test_current_child_corruption_faults_but_concurrent_update_does_not(self):
        self.fixture._populate_terminal_history(task_count=3, declarations_per_task=2)
        entered = asyncio.Event()
        original = self.children.collect_health
        async def paused(fence):
            entered.set()
            await asyncio.sleep(0)
            return await original(fence)
        readiness = self.runtime.readiness_state()
        with patch.object(self.children, "collect_health", side_effect=paused):
            scan = asyncio.create_task(self.runtime.async_health())
            await entered.wait()
            task = self.fixture.service.task_repository.list()[0]
            self.fixture.service.task_repository.save(task)
            with self.assertRaises(GovernanceError) as error:
                await scan
        self.assertEqual(error.exception.details, {"reason": "health_snapshot_superseded"})
        self.assertEqual(self.runtime.readiness_state(), readiness)
        await self.runtime.async_health()
        next(self.children.root.glob("*.child.json")).write_text("{")
        with self.assertRaises(child_storage.ExecutionRecordCorrupt):
            await self.runtime.async_health()
        self.assertFalse(self.runtime.readiness_state()["execution_ready"])

    async def test_public_lock_race_refuses_without_fault_and_fresh_scan_sees_hold(self):
        task, declarations = await self.fixture._build_live_orphan()
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.children.collect_health
        async def paused(fence):
            entered.set()
            await release.wait()
            return await original(fence)
        before = self.runtime.readiness_state()
        with patch.object(self.children, "collect_health", side_effect=paused):
            pending = asyncio.create_task(self.fixture.service.async_health_summary())
            await entered.wait()
            self.fixture._hold_child_lock(declarations[0], conflict_hold=True)
            release.set()
            with self.assertRaises(GovernanceError) as error:
                await pending
        self.assertEqual(error.exception.details, {"reason": "health_snapshot_superseded"})
        self.assertEqual(self.runtime.readiness_state(), before)
        fresh = await self.fixture.service.async_health_summary()
        self.assertGreater(fresh["f3"]["active_conflict_hold_count"], 0)

    async def test_unavailable_fence_does_not_mask_original_storage_failure(self):
        self.fixture._populate_terminal_history(task_count=1, declarations_per_task=1)
        parents = self.fixture.service.task_repository
        original = HealthScanFence.check
        failed = False
        def unavailable_after_failure(fence):
            if failed:
                raise OSError("synthetic unavailable snapshot namespace")
            return original(fence)
        def parent_failure(*args, **kwargs):
            nonlocal failed
            failed = True
            raise ExecutionTaskStorageError("synthetic parent read failure")
        with patch.object(HealthScanFence, "check", unavailable_after_failure), patch.object(
            parents, "_load", side_effect=parent_failure
        ):
            with self.assertRaises(GovernanceError) as error:
                await self.fixture.service.async_health_summary()
        self.assertEqual(error.exception.code, ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        self.assertNotEqual(error.exception.details.get("reason"), "health_snapshot_superseded")
        self.assertIn("health", self.runtime.readiness_state()["faults"])
        self.assertIsNone(self.fixture.service._health_cache_key)

    async def test_child_read_failure_with_unavailable_fence_faults_readiness(self):
        fence = self.runtime._health_fence()
        with patch.object(self.children, "collect_health", side_effect=fixtures.ExecutionStorageError("synthetic read failure")), patch.object(
            fence, "check", side_effect=OSError("synthetic fence failure")
        ):
            with self.assertRaises(fixtures.ExecutionStorageError):
                await self.runtime.async_health(tasks=[], fence=fence)
        self.assertIn("health", self.runtime.readiness_state()["faults"])

    async def test_child_update_after_file_check_with_coalesced_directory_is_refused(self):
        self.fixture._populate_terminal_history(task_count=2, declarations_per_task=2)
        root = self.children.root
        stamp = HealthScanFence.stamp
        directory_stamp = stamp(root)
        observations, chosen = {}, []
        checked = asyncio.Event()
        def coalesced(path):
            if path == root:
                return directory_stamp
            value = stamp(path)
            if path.name.endswith(".child.json"):
                observations[path] = observations.get(path, 0) + 1
                if observations[path] == 2 and not chosen:
                    chosen.append(path)
                    checked.set()
            return value
        async def writer():
            await checked.wait()
            child_id = json.loads(chosen[0].read_text())["declaration"]["child_id"]
            self.children.update_runtime(child_id, changes={"backoff_seconds": 1})
        before = self.runtime.readiness_state()
        mutation = asyncio.create_task(writer())
        try:
            with patch.object(HealthScanFence, "stamp", staticmethod(coalesced)):
                with self.assertRaises(GovernanceError) as error:
                    await self.fixture.service.async_health_summary()
            await mutation
        finally:
            mutation.cancel()
            await asyncio.gather(mutation, return_exceptions=True)
        self.assertEqual(error.exception.details, {"reason": "health_snapshot_superseded"})
        self.assertEqual(self.runtime.readiness_state(), before)
        await self.fixture.service.async_health_summary()

    async def test_public_parent_directory_failure_keeps_task_storage_attribution(self):
        import os
        parents = self.fixture.service.task_repository
        original = os.scandir
        def unreadable(path):
            if Path(path) == parents.root:
                raise PermissionError("synthetic task-directory failure")
            return original(path)
        with patch.object(os, "scandir", side_effect=unreadable):
            with self.assertRaises(GovernanceError) as error:
                await self.fixture.service.async_health_summary()
        self.assertEqual(error.exception.code, ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        self.assertNotEqual(error.exception.details.get("reason"), "health_snapshot_superseded")
        self.assertIn("health", self.runtime.readiness_state()["faults"])
        self.assertIsNone(self.fixture.service._health_cache_key)

    async def test_empty_collection_matches_sync_counts(self):
        fence = self.fence()
        data = await self.children.collect_health(fence)
        await fence.verify_files()
        self.assertEqual(data, {'envelopes': {}, 'records': {}, 'manifests': {}})
        self.assertEqual(self.children.health()['record_count'], 0)

    async def test_orphan_with_materialized_child_and_conflict_hold_matches_sync(self):
        task, declarations = await self.fixture._build_live_orphan()
        declaration = declarations[0]
        self.fixture._hold_child_lock(declaration, conflict_hold=True)
        expected = self.runtime.health()
        before_calls = list(self.fixture.gateway.calls)
        actual = await self.runtime.async_health()
        self.assertEqual(actual, expected)
        self.assertGreater(actual['nonterminal_execution_count'], 0)
        self.assertGreater(actual['active_conflict_hold_count'], 0)
        self.assertGreater(actual['recovery_backlog']['returned_count'], 0)
        self.assertEqual(list(self.fixture.gateway.calls), before_calls)

    async def test_missing_manifest_child_and_noncanonical_duplicate_are_corrupt(self):
        self.fixture._populate_terminal_history(task_count=2, declarations_per_task=2)
        path = next(self.children.root.glob('*.child.json'))
        body = path.read_bytes()
        path.unlink()
        with self.assertRaises(child_storage.ExecutionRecordCorrupt):
            await self.children.collect_health(self.fence())
        path.write_bytes(body)
        duplicate = path.with_name('duplicate.child.json')
        duplicate.write_bytes(body)
        with self.assertRaises(child_storage.ExecutionRecordCorrupt):
            await self.children.collect_health(self.fence())

    async def test_parent_corruption_is_not_masked_as_its_quarantine_race(self):
        self.fixture._populate_terminal_history(task_count=2, declarations_per_task=2)
        parents = self.fixture.service.task_repository
        next(parents.root.glob('*.json')).write_text('{')
        before = parents.corruption_count
        with self.assertRaises(ExecutionTaskStorageError):
            await self.runtime.async_health()
        self.assertEqual(parents.corruption_count, before + 1)
        self.assertIn('health', self.runtime.readiness_state()['faults'])

    async def test_in_place_child_movement_is_detected_by_final_file_fence(self):
        self.fixture._populate_terminal_history(task_count=2, declarations_per_task=2)
        fence = self.fence()
        await self.children.collect_health(fence)
        path = next(self.children.root.glob('*.child.json'))
        path.write_text(path.read_text() + ' ')
        # Directory identity is unchanged by an in-place file edit.
        fence.check()
        with self.assertRaises(GovernanceError) as error:
            await fence.verify_files()
        self.assertEqual(error.exception.details, {'reason': 'health_snapshot_superseded'})

    async def test_current_corruption_and_namespace_limits_refuse(self):
        self.fixture._populate_terminal_history(task_count=2, declarations_per_task=2)
        with patch.object(child_storage, 'MAX_F3_PUBLIC_TASKS', 1):
            with self.assertRaises(fixtures.ExecutionStorageError):
                await self.children.collect_health(self.fence())
        path = next(self.children.root.glob('*.child.json'))
        path.write_text('{')
        with self.assertRaises(child_storage.ExecutionRecordCorrupt):
            await self.children.collect_health(self.fence())

    async def test_atomic_update_during_collection_is_superseded(self):
        self.fixture._populate_terminal_history(task_count=4, declarations_per_task=2)
        original = self.children._envelope_at
        observed = asyncio.Event()
        child = []
        def read(*args, **kwargs):
            envelope = original(*args, **kwargs)
            if not child:
                child.append(envelope['declaration']['child_id'])
                observed.set()
            return envelope
        async def update():
            await observed.wait()
            self.children.update_runtime(child[0], changes={'backoff_seconds': 1})
        writer = asyncio.create_task(update())
        readiness = self.runtime.readiness_state()
        fence = self.fence()
        with patch.object(self.children, '_envelope_at', side_effect=read):
            with self.assertRaises(GovernanceError) as error:
                await self.children.collect_health(fence)
                await fence.verify_files()
        await writer
        self.assertEqual(error.exception.details, {'reason': 'health_snapshot_superseded'})
        self.assertEqual(self.runtime.readiness_state(), readiness)
        await self.children.collect_health(self.fence())

    async def test_repeated_cancellation_leaves_no_worker_or_partial_publication(self):
        self.fixture._populate_terminal_history(task_count=12, declarations_per_task=2)
        for _ in range(3):
            read_count = 0
            original = self.children._envelope_at
            def read(*args, **kwargs):
                nonlocal read_count
                read_count += 1
                return original(*args, **kwargs)
            with patch.object(self.children, '_envelope_at', side_effect=read):
                scan = asyncio.create_task(self.children.collect_health(self.fence()))
                while read_count == 0:
                    await asyncio.sleep(0)
                scan.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await scan
                stopped = read_count
                for _ in range(3):
                    await asyncio.sleep(0)
                self.assertEqual(read_count, stopped)
        data = await self.children.collect_health(self.fence())
        self.assertEqual(len(data['envelopes']), 24)


class InverseCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_inverse_retained_union_is_visible_without_reads_per_child_or_dispatch(self):
        fixture = inverse_fixtures.RollbackTests()
        await fixture.asyncSetUp()
        try:
            source = await fixture.seed()
            _, request = await fixture.rollback(source)
            await fixture.approve(request)
            runtime = fixture.runtime
            execute = runtime._execute_child
            paused, resume = asyncio.Event(), asyncio.Event()
            async def boundary(plan, task, declaration, *args, **kwargs):
                if declaration['operation_ordinal'] == 1:
                    paused.set()
                    await resume.wait()
                return await execute(plan, task, declaration, *args, **kwargs)
            with patch.object(runtime, '_execute_child', side_effect=boundary):
                applying = asyncio.create_task(fixture.apply_request(request))
                try:
                    await asyncio.wait_for(paused.wait(), 30)
                    expected = runtime.health()
                    calls = list(fixture.gateway.calls)
                    parents = fixture.service.task_repository
                    with patch.object(parents, 'get', wraps=parents.get) as read:
                        actual = (await fixture.service.async_health_summary())["f3"]
                    self.assertEqual(actual, expected)
                    self.assertEqual(read.call_count, 0)
                    self.assertEqual(calls, fixture.gateway.calls)
                    self.assertGreater(actual['recovery_backlog']['retained_lock_count'], 0)
                    self.assertGreater(actual['recovery_backlog']['returned_count'], 0)
                finally:
                    resume.set()
                    result = await applying
            self.assertEqual(result['task_state'], 'succeeded_verified')
            self.assertEqual(len(fixture.writes()), 5)
        finally:
            await fixture.asyncTearDown()
            fixture.doCleanups()
