"""Offline cold-health scheduling, concurrency and execution preservation."""
from __future__ import annotations

import asyncio
import copy
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tests import test_governance as fixtures
from tests import test_f3_runtime_integration as f3_fixtures
from tests import test_beta34_historical_policy_projection as historical_fixtures
from tests.test_dev14_configuration_plans import PROPOSED_AUTOMATION
from ha_mcp_engineering.errors import ErrorCode, GovernanceError
from ha_mcp_engineering.governance import service as service_module
from ha_mcp_engineering.governance.models import PlanStatus
from ha_mcp_engineering.governance.runtime import GovernanceRuntime
from ha_mcp_engineering.governance.storage import ChangePlanRepository, ChangePlanStorageError
from ha_mcp_engineering.governance.task_storage import ExecutionTaskStorageError
from ha_mcp_engineering.health import HealthRegistry


class AsyncHealthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixtures.GovernanceTestCase()
        await self.fixture.asyncSetUp()
        self.service = self.fixture.service
        self.repository = self.fixture.repository
        self.entered = threading.Event()
        self.release = threading.Event()
        self.thread_ids = []
        original = service_module._validate_health_records

        def controlled_validation(plans, sensitive_values):
            self.thread_ids.append(threading.get_ident())
            self.entered.set()
            if not self.release.wait(3):
                raise AssertionError("owner loop did not release synthetic validation")
            return original(plans, sensitive_values)

        self.validation = controlled_validation

    async def asyncTearDown(self):
        self.release.set()
        job = self.service._health_validation_job
        if job is not None:
            try:
                await job
            except GovernanceError:
                pass
        await self.fixture.asyncTearDown()

    async def wait_for_worker(self):
        async def wait():
            while not self.entered.is_set():
                await asyncio.sleep(0.001)
        await asyncio.wait_for(wait(), 2)

    async def test_concurrent_reads_serialize_complete_collections(self):
        await self.fixture.update_plan()
        rebuilds = self.service._health_cache_rebuild_count
        with patch.object(service_module, "_validate_health_records", side_effect=self.validation) as validation:
            first = asyncio.create_task(self.service.async_health_summary())
            await self.wait_for_worker()
            second = asyncio.create_task(self.service.async_health_summary())
            progress = []
            for i in range(4):
                await asyncio.sleep(0)
                progress.append(i)
            self.assertFalse(first.done())
            self.assertFalse(second.done())
            self.assertEqual(progress, [0, 1, 2, 3])
            self.assertEqual(validation.call_count, 1)
            self.release.set()
            cold, warm = await asyncio.gather(first, second)
        self.assertNotEqual(self.thread_ids, [threading.get_ident()])
        self.assertEqual(cold["total_plans"], 1)
        self.assertEqual(warm["plans_requiring_approval"], 1)
        self.assertEqual(self.service._health_cache_rebuild_count, rebuilds + 2)
        self.assertEqual(warm["plan_store_scaling"]["hot_paths"]["governance_health"]["plan_records_deserialized"], 1)
        self.assertEqual(self.fixture.gateway.write_calls, 0)

    async def test_cancel_does_not_write_expiry_or_queue_more_workers(self):
        created = await self.fixture.update_plan(expiration_minutes=5)
        self.fixture.clock.advance(minutes=6)
        before = self.repository._path(created["plan_id"]).read_bytes()
        with patch.object(service_module, "_validate_health_records", side_effect=self.validation) as validation:
            first = asyncio.create_task(self.service.async_health_summary())
            await self.wait_for_worker()
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            second = asyncio.create_task(self.service.async_health_summary())
            for _ in range(4):
                await asyncio.sleep(0)
            second.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await second
            self.assertEqual(validation.call_count, 1)
            self.release.set()
            await self.service._health_validation_job
        self.assertEqual(self.repository._path(created["plan_id"]).read_bytes(), before)
        self.assertIsNone(self.service._health_cache_key)
        self.assertEqual((await self.service.async_health_summary())["expired_plans"], 1)
        persisted = self.repository._path(created["plan_id"]).read_bytes()
        await self.service.async_health_summary()
        self.assertEqual(self.repository._path(created["plan_id"]).read_bytes(), persisted)

    async def test_changed_plan_is_not_overwritten_or_published_as_current(self):
        for external in (False, True):
            with self.subTest(external=external):
                self.entered.clear()
                self.release.clear()
                created = await self.fixture.update_plan(expiration_minutes=5)
                self.fixture.clock.advance(minutes=6)
                writer = ChangePlanRepository(self.repository.root) if external else self.repository
                with patch.object(service_module, "_validate_health_records", side_effect=self.validation):
                    pending = asyncio.create_task(self.service.async_health_summary())
                    await self.wait_for_worker()
                    plan = writer.get(created["plan_id"])
                    plan.status = PlanStatus.REJECTED
                    writer.save(plan)
                    self.release.set()
                    with self.assertRaises(GovernanceError) as error:
                        await pending
                self.assertEqual(error.exception.code, ErrorCode.CHANGE_PLAN_STORAGE_ERROR)
                self.assertEqual(error.exception.details, {"reason": "health_snapshot_superseded"})
                self.assertEqual(writer.get(created["plan_id"]).status, PlanStatus.REJECTED)
                self.assertIsNone(self.service._health_cache_key)
        self.assertEqual((await self.service.async_health_summary())["rejected_plans"], 2)

    async def test_task_change_during_validation_refuses_mixed_projection(self):
        await self.fixture.update_plan()
        with patch.object(service_module, "_validate_health_records", side_effect=self.validation):
            pending = asyncio.create_task(self.service.async_health_summary())
            await self.wait_for_worker()
            self.service.task_repository.rebuild_navigation_index()
            self.release.set()
            with self.assertRaises(GovernanceError) as error:
                await pending
        self.assertEqual(error.exception.code, ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        self.assertEqual(error.exception.details, {"reason": "health_snapshot_superseded"})
        self.assertIsNone(self.service._health_cache_key)
        self.assertEqual((await self.service.async_health_summary())["total_plans"], 1)

    async def test_owner_approval_during_worker_survives_discarded_snapshot(self):
        created = await self.fixture.update_plan()
        with patch.object(service_module, "_validate_health_records", side_effect=self.validation):
            pending = asyncio.create_task(self.service.async_health_summary())
            await self.wait_for_worker()
            await self.fixture.externally_approve(created["plan_id"], created["plan_hash"])
            self.release.set()
            with self.assertRaises(GovernanceError):
                await pending
        health = await self.service.async_health_summary()
        self.assertEqual(health["externally_approved_plans"], 1)
        self.assertEqual(health["plans_requiring_approval"], 0)
        self.assertEqual(self.fixture.gateway.write_calls, 0)

    async def test_expiry_is_owner_thread_only_and_persisted_once(self):
        created = await self.fixture.update_plan(expiration_minutes=5)
        owner = threading.get_ident()
        writes = []
        original = self.repository.save
        def save(plan):
            writes.append(threading.get_ident())
            return original(plan)
        with patch.object(service_module, "_validate_health_records", side_effect=self.validation), patch.object(self.repository, "save", side_effect=save):
            pending = asyncio.create_task(self.service.async_health_summary())
            await self.wait_for_worker()
            self.fixture.clock.advance(minutes=6)
            self.release.set()
            health = await pending
            self.assertEqual(health["expired_plans"], 1)
            count = len(writes)
            await self.service.async_health_summary()
        self.assertGreater(count, 0)
        self.assertEqual(writes, [owner] * count)
        self.assertEqual(self.repository.get(created["plan_id"]).status, PlanStatus.EXPIRED)

    async def test_crossed_deadline_during_projection_is_not_cached_past_expiry(self):
        created = await self.fixture.update_plan(expiration_minutes=5)
        original = self.service._resolve_lifecycle
        def resolve(plan):
            result = original(plan)
            self.fixture.clock.advance(minutes=6)
            return result
        with patch.object(self.service, "_resolve_lifecycle", side_effect=resolve):
            await self.service.async_health_summary()
        self.assertEqual((await self.service.async_health_summary())["expired_plans"], 1)
        self.assertEqual(self.repository.get(created["plan_id"]).status, PlanStatus.EXPIRED)

    async def test_live_unavailability_and_locks_remain_visible_after_yield(self):
        await self.fixture.update_plan()
        state = {"status": "ready", "active_locks": 0}
        live = AsyncMock(side_effect=lambda **kwargs: dict(state))
        self.service.f3_runtime = SimpleNamespace(
            async_health=live, _health_fence_sources=lambda: ())
        with patch.object(service_module, "_validate_health_records", side_effect=self.validation):
            pending = asyncio.create_task(self.service.async_health_summary())
            await self.wait_for_worker()
            state.update(status="not_ready", active_locks=1)
            self.release.set()
            result = await pending
        self.assertEqual(result["f3"], state)
        live.assert_called_once()
        self.assertEqual(self.fixture.gateway.write_calls, 0)

    async def test_approval_change_invalidates_then_warm_reads_remain_stable(self):
        created = await self.fixture.update_plan()
        initial = await self.service.async_health_summary()
        self.assertEqual(initial["plans_requiring_approval"], 1)
        await self.fixture.externally_approve(created["plan_id"], created["plan_hash"])
        approved = await self.service.async_health_summary()
        self.assertEqual(approved["externally_approved_plans"], 1)
        generations = (self.repository.generation, self.service.task_repository.generation)
        approved["execution_tasks"]["verified_successes"] = 999
        for _ in range(2):
            warm = await self.service.async_health_summary()
            self.assertEqual(warm["execution_tasks"]["verified_successes"], 0)
            self.assertEqual(warm["plan_store_scaling"]["hot_paths"]["governance_health"]["plan_records_deserialized"], 1)
            self.assertEqual(generations, (self.repository.generation, self.service.task_repository.generation))

    async def test_history_errors_are_not_cached_as_healthy(self):
        await self.fixture.update_plan()
        with patch.object(self.repository, "collect_health", side_effect=ChangePlanStorageError("synthetic")):
            with self.assertRaises(GovernanceError) as error:
                await self.service.async_health_summary()
            self.assertNotEqual(error.exception.details.get("reason"), "health_snapshot_superseded")
        self.assertIsNone(self.service._health_cache_key)
        with patch.object(self.service.task_repository, "collect_health", side_effect=ExecutionTaskStorageError("synthetic")) as listing:
            for _ in range(2):
                with self.assertRaises(GovernanceError) as error:
                    await self.service.async_health_summary()
                self.assertEqual(error.exception.code, ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
                self.assertIsNone(self.service._health_cache_key)
            self.assertEqual(listing.call_count, 2)
        self.assertEqual((await self.service.async_health_summary())["execution_tasks"]["storage_status"], "healthy")

    async def test_worker_failure_cannot_publish_or_reuse_old_ready_cache(self):
        await self.fixture.update_plan()
        with patch.object(service_module, "_validate_health_records", side_effect=GovernanceError(ErrorCode.CHANGE_PLAN_STORAGE_ERROR)):
            for _ in range(2):
                with self.assertRaises(GovernanceError):
                    await self.service.async_health_summary()
                self.assertIsNone(self.service._health_cache_key)
        self.assertEqual((await self.service.async_health_summary())["total_plans"], 1)

    async def test_abandoned_unexpected_failure_does_not_poison_fresh_reader(self):
        await self.fixture.update_plan()
        original = service_module._validate_health_records
        calls = []

        def first_fails(plans, sensitive_values):
            calls.append(threading.get_ident())
            if len(calls) == 1:
                self.entered.set()
                if not self.release.wait(3):
                    raise AssertionError("synthetic abandoned worker was not released")
                raise RuntimeError("synthetic private failure must not reach fresh reader")
            return original(plans, sensitive_values)

        with patch.object(service_module, "_validate_health_records", side_effect=first_fails):
            abandoned = asyncio.create_task(self.service.async_health_summary())
            await self.wait_for_worker()
            abandoned.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await abandoned
            fresh = asyncio.create_task(self.service.async_health_summary())
            await asyncio.sleep(0)
            self.assertEqual(len(calls), 1)
            self.assertFalse(fresh.done())
            self.release.set()
            health = await fresh
        self.assertEqual(health["total_plans"], 1)
        self.assertEqual(len(calls), 2)
        self.assertIsNone(self.service._health_validation_job)
        self.assertEqual(self.fixture.gateway.write_calls, 0)

    async def test_current_unexpected_failure_still_propagates_without_cache(self):
        await self.fixture.update_plan()
        with patch.object(service_module, "_validate_health_records", side_effect=RuntimeError("synthetic")):
            with self.assertRaises(RuntimeError):
                await self.service.async_health_summary()
        self.assertIsNone(self.service._health_cache_key)
        self.assertEqual((await self.service.async_health_summary())["total_plans"], 1)

    async def test_cancellation_while_draining_keeps_single_job(self):
        await self.fixture.update_plan()
        with patch.object(service_module, "_validate_health_records", side_effect=self.validation) as validation:
            first = asyncio.create_task(self.service.async_health_summary())
            await self.wait_for_worker()
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            original_job = self.service._health_validation_job
            second = asyncio.create_task(self.service.async_health_summary())
            await asyncio.sleep(0)
            second.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await second
            self.assertIs(self.service._health_validation_job, original_job)
            self.assertFalse(original_job.done())
            self.assertEqual(validation.call_count, 1)
            self.release.set()
            self.assertEqual((await self.service.async_health_summary())["total_plans"], 1)
            self.assertEqual(validation.call_count, 2)

    async def test_phase_measurements_are_per_call_and_do_not_survive_sync_read(self):
        await self.fixture.update_plan()
        cold = await self.service.async_health_summary()
        phases = cold["plan_store_scaling"]["hot_paths"]["governance_health"]["phase_elapsed_ms"]
        self.assertEqual(set(phases), {
            "reader_wait_ms", "abandoned_worker_wait_ms", "snapshot_ms",
            "validation_wait_ms", "worker_elapsed_ms", "projection_ms",
            "assembly_ms", "overlay_ms",
        })
        self.assertTrue(all(isinstance(value, float) and value >= 0 for value in phases.values()))
        self.assertGreater(phases["validation_wait_ms"], 0)
        self.assertLessEqual(phases["worker_elapsed_ms"], phases["validation_wait_ms"] + 0.001)
        warm = await self.service.async_health_summary()
        warm_phases = warm["plan_store_scaling"]["hot_paths"]["governance_health"]["phase_elapsed_ms"]
        for field in ("validation_wait_ms", "worker_elapsed_ms", "projection_ms", "assembly_ms"):
            self.assertGreater(warm_phases[field], 0)
        sync = self.service.health_summary()
        self.assertNotIn("phase_elapsed_ms", sync["plan_store_scaling"]["hot_paths"]["governance_health"])

    async def test_real_cpu_scanner_permits_owner_loop_progress(self):
        fixture = f3_fixtures.F3ConfigurationActivationTests()
        await fixture.asyncSetUp()
        try:
            for index in range(32):
                await fixture.service.create_configuration_plan(
                    title=f"Synthetic CPU history {index}", description="Offline scanner workload",
                    operations=[{
                        "operation_id": "update", "resource_type": "automation",
                        "action": "update", "target_id": "apply_hvac_comfort",
                        "depends_on": [], "proposed_config": copy.deepcopy(PROPOSED_AUTOMATION),
                    }],
                )
            original = service_module._validate_health_records
            interval = []
            ticks = []
            done = asyncio.Event()

            def measured(plans, sensitive_values):
                interval.append(time.monotonic())
                try:
                    return original(plans, sensitive_values)
                finally:
                    interval.append(time.monotonic())

            async def heartbeat():
                while not done.is_set():
                    ticks.append(time.monotonic())
                    await asyncio.sleep(0.001)

            ticker = asyncio.create_task(heartbeat())
            try:
                await asyncio.sleep(0)
                with patch.object(service_module, "_validate_health_records", side_effect=measured):
                    result = await fixture.service.async_health_summary()
            finally:
                done.set()
                await ticker
            self.assertEqual(result["total_plans"], 32)
            self.assertTrue(any(interval[0] < tick < interval[1] for tick in ticks))
            self.assertGreater(result["plan_store_scaling"]["hot_paths"]["governance_health"]["phase_elapsed_ms"]["worker_elapsed_ms"], 0)
            self.assertEqual(sum(call[0] == "write" for call in fixture.gateway.calls), 0)
        finally:
            await fixture.asyncTearDown()

    async def test_unconfigured_runtime_and_envelope_preserve_shape(self):
        runtime = GovernanceRuntime()
        self.assertEqual(await runtime.async_health_summary(), runtime.health_summary())
        registry = HealthRegistry(governance=runtime)
        async_result = await registry.async_snapshot({"status": "not_checked"})
        sync_result = registry.snapshot({"status": "not_checked"})
        self.assertEqual(async_result["governance"], sync_result["governance"])
        self.assertEqual(set(async_result), set(sync_result))

    async def test_envelope_reads_authority_after_async_history(self):
        state = {"generation": 1, "disposition": "admitted"}
        runtime = GovernanceRuntime()
        runtime.service = self.service
        registry = HealthRegistry(governance=runtime, core_readmission=SimpleNamespace(health_projection=lambda: dict(state)))
        await self.fixture.update_plan()
        with patch.object(service_module, "_validate_health_records", side_effect=self.validation):
            pending = asyncio.create_task(registry.async_snapshot({"status": "not_checked"}))
            await self.wait_for_worker()
            state.update(generation=2, disposition="unavailable")
            self.release.set()
            result = await pending
        self.assertEqual(result["home_assistant_core_authority"], state)

    async def test_public_no_probe_tool_uses_async_path_without_provider_calls(self):
        from ha_mcp_engineering.tools import compatibility
        runtime = GovernanceRuntime()
        runtime.service = self.service
        registry = HealthRegistry(governance=runtime)
        await self.fixture.update_plan()
        with (
            patch.object(compatibility, "HEALTH", registry),
            patch.object(compatibility, "rest", AsyncMock()) as rest,
            patch.object(compatibility, "ws_command", AsyncMock()) as websocket,
            patch.object(service_module, "_validate_health_records", side_effect=self.validation),
        ):
            pending = asyncio.create_task(compatibility.get_server_health(check_ha=False))
            await self.wait_for_worker()
            self.assertFalse(pending.done())
            self.release.set()
            response = json.loads(await pending)
        self.assertTrue(response["success"])
        self.assertEqual(response["data"]["governance"]["total_plans"], 1)
        rest.assert_not_awaited()
        websocket.assert_not_awaited()

    async def test_public_race_reason_survives_without_provider_probe_or_record_content(self):
        from ha_mcp_engineering.tools import compatibility
        runtime = GovernanceRuntime()
        runtime.service = self.service
        registry = HealthRegistry(governance=runtime)
        created = await self.fixture.update_plan()
        with (
            patch.object(compatibility, "HEALTH", registry),
            patch.object(compatibility, "rest", AsyncMock()) as rest,
            patch.object(compatibility, "ws_command", AsyncMock()) as websocket,
            patch.object(service_module, "_validate_health_records", side_effect=self.validation),
        ):
            pending = asyncio.create_task(compatibility.get_server_health(check_ha=False))
            await self.wait_for_worker()
            await self.fixture.externally_approve(created["plan_id"], created["plan_hash"])
            self.release.set()
            response = json.loads(await pending)
        self.assertFalse(response["success"])
        self.assertEqual(response["error_code"], ErrorCode.CHANGE_PLAN_STORAGE_ERROR.value)
        self.assertEqual(response["details"], {"reason": "health_snapshot_superseded"})
        self.assertNotIn(created["plan_id"], json.dumps(response))
        rest.assert_not_awaited()
        websocket.assert_not_awaited()


class AsyncF3HealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_historical_read_projection_preserves_bytes_and_no_authority(self):
        fixture = historical_fixtures.HistoricalPolicyProjectionTests()
        await fixture.asyncSetUp()
        try:
            fixture.repository.rebuild_navigation_index()
            health = await fixture.service.async_health_summary()
            self.assertEqual(health["total_plans"], 2)
            self.assertEqual(health["projection_failure_count"], 0)
            self.assertEqual(health["historical_policy_snapshot_compatibility"]["compatible_count"], 2)
            for plan in fixture._plans():
                with self.assertRaises(GovernanceError):
                    await fixture.service.apply(plan.plan_id, fixture.service.plan_hash(plan))
            fixture._assert_persisted_bytes_unchanged()
        finally:
            await fixture.asyncTearDown()

    async def test_current_writer_history_then_fresh_approved_apply_and_duplicate(self):
        fixture = f3_fixtures.F3ConfigurationActivationTests()
        await fixture.asyncSetUp()
        try:
            service = fixture.service
            for i in range(3):
                config = copy.deepcopy(PROPOSED_AUTOMATION)
                config["description"] = f"Synthetic cold rebuild {i}"
                created = await service.create_configuration_plan(
                    title="Cold health test", description="Offline synthetic writer",
                    operations=[{"operation_id": "update", "resource_type": "automation", "action": "update", "target_id": "apply_hvac_comfort", "depends_on": [], "proposed_config": config}],
                )
                await fixture.approve(created)
                before = await service.async_health_summary()
                self.assertEqual(before["externally_approved_plans"], 1)
                applied = await service.apply(created["plan_id"], created["plan_hash"])
                self.assertEqual(applied["task_state"], "succeeded_verified")
                after = await service.async_health_summary()
                self.assertEqual(after["execution_tasks"]["verified_successes"], i + 1)
            calls = list(fixture.gateway.calls)
            await service.apply(created["plan_id"], created["plan_hash"])
            self.assertFalse(any(call[0] == "write" for call in fixture.gateway.calls[len(calls):]))
            self.assertEqual((await service.async_health_summary())["execution_tasks"]["verified_successes"], 3)
            # A persisted unsafe current-writer record still refuses health.
            plan = fixture.repository.get(created["plan_id"])
            plan.description = "http://synthetic.invalid/mcp/synthetic-private-path"
            fixture.repository.save(plan)
            with self.assertRaises(GovernanceError) as error:
                await service.async_health_summary()
            self.assertEqual(error.exception.code, ErrorCode.CHANGE_PLAN_STORAGE_ERROR)
            self.assertIsNone(service._health_cache_key)
        finally:
            await fixture.asyncTearDown()


class CooperativePlanCollectionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = AsyncHealthTests.asyncSetUp
    asyncTearDown = AsyncHealthTests.asyncTearDown

    async def test_cold_damaged_index_yields_between_each_exact_plan_read(self):
        from pathlib import Path
        for _ in range(32):
            await self.fixture.update_plan()
        self.repository._ordered_keys.clear()
        reads, ticks, boundaries = [], [], []
        original = Path.read_text
        async def pulse():
            while True:
                await asyncio.sleep(0)
                ticks.append(None)
        def read(path, *args, **kwargs):
            if path.parent == self.repository.root and path.suffix == ".json":
                reads.append(path)
                boundaries.append(len(ticks))
            return original(path, *args, **kwargs)
        pulse_task = asyncio.create_task(pulse())
        try:
            with patch.object(Path, "read_text", read), patch.object(
                self.repository, "_scan_and_rebuild", side_effect=AssertionError("hidden synchronous scan")
            ):
                result = await self.service.async_health_summary()
        finally:
            pulse_task.cancel()
            await asyncio.gather(pulse_task, return_exceptions=True)
        self.assertEqual(result["total_plans"], 32)
        self.assertEqual(len(reads), 32)
        self.assertEqual(len(set(reads)), 32)
        self.assertTrue(all(b > a for a, b in zip(boundaries, boundaries[1:])))
        self.assertEqual(len(self.repository._ordered_keys), 32)
        self.assertEqual(result["plan_store_scaling"]["hot_paths"]["governance_health"]["plan_records_deserialized"], 32)
        self.assertEqual(self.fixture.gateway.write_calls, 0)

    async def test_external_plan_replacement_during_collection_is_local_refusal(self):
        created = await self.fixture.update_plan()
        writer = ChangePlanRepository(self.repository.root)
        plan = writer.get(created["plan_id"])
        entered = asyncio.Event()
        original = self.repository._load
        def read(*args, **kwargs):
            result = original(*args, **kwargs)
            entered.set()
            return result
        with patch.object(self.repository, "_load", side_effect=read):
            pending = asyncio.create_task(self.service.async_health_summary())
            await entered.wait()
            plan.status = PlanStatus.REJECTED
            writer.save(plan)
            with self.assertRaises(GovernanceError) as error:
                await pending
        self.assertEqual(error.exception.details, {"reason": "health_snapshot_superseded"})
        self.assertIsNone(self.service._health_cache_key)
        self.assertEqual((await self.service.async_health_summary())["rejected_plans"], 1)

    async def test_cancellation_during_index_rebuild_discards_partial_navigation(self):
        for _ in range(4):
            await self.fixture.update_plan()
        entries = dict(self.repository._entries)
        self.repository._ordered_keys.clear()
        generation = self.repository.generation
        entered = asyncio.Event()
        original = self.repository._load
        def read(*args, **kwargs):
            value = original(*args, **kwargs)
            entered.set()
            return value
        for _ in range(3):
            entered.clear()
            with patch.object(self.repository, "_load", side_effect=read) as reads:
                pending = asyncio.create_task(self.service.async_health_summary())
                await entered.wait()
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await pending
                count = reads.call_count
                await asyncio.sleep(0)
                self.assertEqual(reads.call_count, count)
            self.assertEqual(self.repository.generation, generation)
            self.assertEqual(self.repository._entries, entries)
            self.assertEqual(self.repository._ordered_keys, [])
            self.assertIsNone(self.service._health_validation_job)
        self.assertEqual((await self.service.async_health_summary())["total_plans"], 4)

    async def test_warm_current_corruption_and_duplicate_identity_are_not_hidden(self):
        created = await self.fixture.update_plan()
        await self.service.async_health_summary()
        path = self.repository._path(created["plan_id"])
        body = path.read_bytes()
        duplicate = path.with_name("0" * 32 + ".json")
        duplicate.write_bytes(body)
        with self.assertRaises(GovernanceError):
            await self.service.async_health_summary()
        self.assertIsNone(self.service._health_cache_key)
        duplicate.unlink()
        path.write_text("{")
        before = self.repository.corruption_count
        with self.assertRaises(GovernanceError) as error:
            await self.service.async_health_summary()
        self.assertNotEqual(error.exception.details.get("reason"), "health_snapshot_superseded")
        self.assertEqual(self.repository.corruption_count, before + 1)
        self.assertTrue(tuple(self.repository.quarantine.glob("*.corrupt")))
        result = await self.service.async_health_summary()
        self.assertEqual(result["storage_corruption_count"], before + 1)

    async def test_in_place_plan_update_during_validation_refuses_before_expiry_save(self):
        created = await self.fixture.update_plan(expiration_minutes=5)
        path = self.repository._path(created["plan_id"])
        with patch.object(service_module, "_validate_health_records", side_effect=self.validation):
            pending = asyncio.create_task(self.service.async_health_summary())
            await AsyncHealthTests.wait_for_worker(self)
            path.write_text(path.read_text() + " ")
            before = path.read_bytes()
            self.fixture.clock.advance(minutes=6)
            self.release.set()
            with self.assertRaises(GovernanceError) as error:
                await pending
        self.assertEqual(error.exception.details, {"reason": "health_snapshot_superseded"})
        self.assertEqual(error.exception.code, ErrorCode.CHANGE_PLAN_STORAGE_ERROR)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((await self.service.async_health_summary())["expired_plans"], 1)
