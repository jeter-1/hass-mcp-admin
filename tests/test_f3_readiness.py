"""F3 request admission: real Linux stores, synthetic writers, no live peers."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
from dataclasses import asdict
import gc
import json
import threading
import time
import unittest
from unittest.mock import Mock, patch

from tests import test_f3_orphan_child_recovery as history_fixtures
from tests import test_beta24_pre_rc_hardening as gateway_fixtures
from ha_mcp_engineering import application


def application_gateway(runtime, *, reconciled=True):
    """Exercise the real composition callback without configuring live clients."""
    app = gateway_fixtures.RecordingApp()
    with ExitStack() as stack:
        for name in (
            'CORE_READMISSION', 'UPSTREAM_OPERATIONAL_BACKUP',
            'UPSTREAM_OPERATIONAL_LIFECYCLE', 'UPSTREAM_DASHBOARD', 'GOVERNANCE',
            'DEPENDENCY_ANALYSIS', 'RELIABILITY_ANALYSIS', 'CHANGE_IMPACT_ANALYSIS',
            'CONFIGURATION_INTEGRITY_ANALYSIS', 'INCIDENT_CORRELATION',
            'HANDOFF_GENERATION', 'UPSTREAM_READ_GATEWAY', 'FAN_OPERATIONS',
            'POWER_OPERATIONS', 'HEALTH',
        ):
            stack.enter_context(patch.object(application, name))
        stack.enter_context(patch('ha_mcp_engineering.audit_baseline.capture_runtime.AUTOMATION_BASELINE_CAPTURE'))
        stack.enter_context(patch('ha_mcp_engineering.integration_inspection.runtime.INTEGRATION_INSPECTION'))
        server = stack.enter_context(patch.object(application, 'get_registered_server'))
        server.return_value.streamable_http_app.return_value = app
        governance = application.GOVERNANCE
        governance.require.return_value.f3_runtime = runtime
        gateway = application.create_application(gateway_fixtures.settings(
            'unused', audit_enabled=False, rate_limit_burst=10000,
            upstream_dashboard_mcp_url='http://127.0.0.1:18086/synthetic-only/mcp',
        ))
    # The composition's late-bound facade remains replaced only for each call.
    gateway._test_governance = governance
    if reconciled:
        gateway.mark_initial_catalog_reconciled()
    return gateway, app


class ReadinessHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = history_fixtures.OrphanChildRecoveryTests()
        await self.fixture.asyncSetUp()
        self.runtime = self.fixture.runtime

    async def asyncTearDown(self):
        await self.fixture.asyncTearDown()

    async def test_routine_readiness_never_traverses_retained_history(self):
        for count in (0, 12, 200, 1023):
            with self.subTest(history=count):
                prepared = time.perf_counter()
                self.fixture._populate_terminal_history(task_count=count, declarations_per_task=1)
                preparation_seconds = time.perf_counter() - prepared
                gateway, app = application_gateway(self.runtime)
                for reconciled in (True, False):
                    gateway, app = application_gateway(self.runtime, reconciled=reconciled)
                    for contention in (False, True):
                        with ExitStack() as stack:
                            stack.enter_context(patch.object(application, 'GOVERNANCE', gateway._test_governance))
                            counts = {}
                            for name in ('list', 'all_declarations', 'manifest_for_task', '_raw_envelope'):
                                counts[name] = stack.enter_context(patch.object(
                                    self.runtime.children, name, wraps=getattr(self.runtime.children, name)))
                            counts['public_history'] = stack.enter_context(patch.object(
                                self.fixture.service.task_repository, 'list',
                                wraps=self.fixture.service.task_repository.list))
                            worker = stack.enter_context(patch('asyncio.to_thread', side_effect=AssertionError('readiness enqueued work')))
                            observation = await self._measure_requests(gateway, contention=contention)
                        print(json.dumps({
                            'phase': 'routine_readiness', 'history': count,
                            'preparation_seconds': preparation_seconds,
                            'catalog_reconciled': reconciled, 'contention': contention,
                            'operations': {name: call.call_count for name, call in counts.items()},
                            **observation,
                        }), flush=True)
                        self.assertEqual({name: call.call_count for name, call in counts.items()},
                                         {name: 0 for name in counts})
                        worker.assert_not_called()
                        self.assertEqual(set(observation['http_statuses']), {200 if reconciled else 503})
                        self.assertLess(observation['maximum_thread_cpu_gap_seconds'], .25,
                                        'Routine calls exhausted the loop-thread CPU budget.')
                    self.assertEqual(app.calls, 40 if reconciled else 0)
                self.assertFalse(any(call[0] == 'write' for call in self.fixture.gateway.calls))

    async def _measure_requests(self, gateway, *, contention):
        gaps, cpu_gaps, gc_events = [], [], []
        loop_thread = threading.get_ident()
        gc_started = {}
        gc_state = {'enabled': gc.isenabled(), 'thresholds': gc.get_threshold()}
        def observe_gc(phase, info):
            thread = threading.get_ident()
            if phase == 'start':
                gc_started[thread] = (time.perf_counter(), time.thread_time())
            elif thread in gc_started:
                wall, cpu = gc_started.pop(thread)
                if len(gc_events) < 64:
                    gc_events.append({'generation': info['generation'],
                                      'event_loop_thread': thread == loop_thread,
                                      'wall_seconds': time.perf_counter() - wall,
                                      'thread_cpu_seconds': time.thread_time() - cpu})
        async def heartbeat():
            last, last_cpu = time.perf_counter(), time.thread_time()
            while True:
                await asyncio.sleep(.001)
                now, cpu = time.perf_counter(), time.thread_time()
                gaps.append(now - last)
                cpu_gaps.append(cpu - last_cpu)
                last, last_cpu = now, cpu
        stop = threading.Event()
        def contend():
            while not stop.is_set():
                deadline = time.perf_counter() + .005
                while time.perf_counter() < deadline:
                    pass
                stop.wait(.001)
        thread = threading.Thread(target=contend) if contention else None
        pulse = asyncio.create_task(heartbeat())
        await asyncio.sleep(0)
        gc.callbacks.append(observe_gc)
        started, cpu = time.perf_counter(), time.thread_time()
        try:
            if thread:
                thread.start()
            async def one(index):
                gateway.catalog_readiness_state()
                status, _ = await gateway_fixtures.CatalogReadinessBarrierTests.request(
                    gateway, '/ready' if index % 2 else f'/{gateway_fixtures.SECRET}/mcp')
                await asyncio.sleep(0)
                return status
            if contention:
                statuses = await asyncio.gather(*(one(i) for i in range(40)))
            else:
                statuses = [await one(i) for i in range(40)]
            await asyncio.sleep(.005)
        finally:
            stop.set()
            if thread:
                thread.join(timeout=1)
                self.assertFalse(thread.is_alive())
            gc.callbacks.remove(observe_gc)
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)
        return {'http_statuses': sorted(set(statuses)),
                'wall_seconds': time.perf_counter() - started,
                'thread_cpu_seconds': time.thread_time() - cpu,
                'maximum_loop_gap_seconds': max(gaps),
                'maximum_thread_cpu_gap_seconds': max(cpu_gaps),
                'gc_state': gc_state, 'gc_events': gc_events}



class ReadinessLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = history_fixtures.OrphanChildRecoveryTests()
        await self.fixture.asyncSetUp()
        self.runtime = self.fixture.runtime
        self.service = self.fixture.service

    async def asyncTearDown(self):
        await self.fixture.asyncTearDown()

    async def _request(self, path='/ready', *, reconciled=True):
        gateway, app = application_gateway(self.runtime, reconciled=reconciled)
        with patch.object(application, 'GOVERNANCE', gateway._test_governance):
            status, body = await gateway_fixtures.CatalogReadinessBarrierTests.request(gateway, path)
        return status, json.loads(body), app.calls

    async def _settled_child(self):
        created = await self.fixture.create_automation_plan()
        await self.fixture.approve(created)
        applied = await self.service.apply(created['plan_id'], created['plan_hash'])
        self.assertEqual(applied['task_state'], 'succeeded_verified')
        declaration = self.runtime.children.declarations_for_task(applied['task_id'])[0]
        return created, applied, self.runtime.children._path(declaration['child_id'])

    async def test_pending_failed_initialization_then_success_is_truthful(self):
        from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration
        from tests.test_f3_runtime_integration import _ExactFakeConfigurationGateway, _provider_identity
        self.runtime = F3RuntimeIntegration(
            service=self.service, storage_root=str(self.fixture.root / 'plans'),
            configuration_gateway=_ExactFakeConfigurationGateway(self.fixture.gateway),
            backup_gateway=None, lifecycle_gateway=None,
            provider_identity_reader=_provider_identity, retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        status, state, _ = await self._request()
        self.assertEqual(status, 503)
        self.assertFalse(state['f3_execution_ready'])
        ticks = iter(range(1000))
        with patch.object(self.runtime, '_recovery_monotonic', side_effect=lambda: next(ticks) * 10):
            await self.runtime.recover_once('startup')
        self.assertEqual((await self._request())[0], 503)
        with patch.object(self.runtime.children, 'health', side_effect=RuntimeError('synthetic-private-configuration')):
            with self.assertRaises(RuntimeError):
                await self.runtime.recover_once('startup')
        status, state, _ = await self._request()
        self.assertEqual(status, 503)
        self.assertEqual(state['f3_readiness_status'], 'faulted')
        self.assertNotIn('synthetic-private-configuration', json.dumps(state))
        await self.runtime.recover_once('startup')
        self.assertEqual((await self._request())[0], 200)
        await self._settled_child()

    async def test_late_corruption_blocks_execution_but_keeps_authenticated_reads(self):
        from ha_mcp_engineering.errors import GovernanceError
        from ha_mcp_engineering.f3.persistence import ExecutionStorageError
        created, applied, path = await self._settled_child()
        original = path.read_bytes()
        path.write_bytes(b'{synthetic corrupted child')
        with self.assertRaises(ExecutionStorageError):
            self.runtime.health()
        status, state, _ = await self._request()
        self.assertEqual(status, 200)
        self.assertTrue(state['ready'])
        self.assertFalse(state['f3_execution_ready'])
        self.assertEqual(state['status'], 'ready_f3_execution_unavailable')
        status, _, calls = await self._request(f'/{gateway_fixtures.SECRET}/mcp')
        self.assertEqual((status, calls), (200, 1))
        with self.assertRaises(ExecutionStorageError):
            self.runtime.decorate_task(self.service.task_repository.get(applied['task_id']))
        # A different, exact approved plan cannot consume its approval or dispatch.
        from tests.test_dev14_configuration_plans import PROPOSED_SCRIPT
        candidate = await self.service.create_configuration_plan(
            title='Synthetic independent script update', description='Readiness recovery',
            operations=[{'operation_id': 'script_update', 'resource_type': 'script',
                         'action': 'update', 'target_id': 'set_hvac_comfort',
                         'depends_on': [], 'proposed_config': PROPOSED_SCRIPT}],
        )
        await self.fixture.approve(candidate)
        before = asdict(self.service._load(candidate['plan_id']).approval)
        writes = sum(call[0] == 'write' for call in self.fixture.gateway.calls)
        with self.assertRaises(GovernanceError):
            await self.service.apply(candidate['plan_id'], candidate['plan_hash'])
        self.assertEqual(asdict(self.service._load(candidate['plan_id']).approval), before)
        self.assertEqual(sum(call[0] == 'write' for call in self.fixture.gateway.calls), writes)
        # Even a pass too small to visit this history must check the known fault.
        with patch.object(self.runtime, '_recovery_monotonic', side_effect=lambda: time.monotonic() * 1e8):
            with self.assertRaises(ExecutionStorageError):
                await self.runtime.recover_once('test')
        self.assertFalse(self.runtime.readiness_state()['execution_ready'])
        path.write_bytes(original)
        self.runtime.health()  # A successful diagnostic read cannot clear the fault.
        self.assertFalse(self.runtime.readiness_state()['execution_ready'])
        await self.runtime.recover_once('test')
        self.assertTrue(self.runtime.readiness_state()['execution_ready'])
        result = await self.service.apply(candidate['plan_id'], candidate['plan_hash'])
        self.assertEqual(result['task_state'], 'succeeded_verified')
        await self.service.apply(candidate['plan_id'], candidate['plan_hash'])
        self.assertEqual(sum(call[0] == 'write' for call in self.fixture.gateway.calls), writes + 1)

    async def test_cancelled_recovery_and_dead_supervisor_are_visible(self):
        entered = asyncio.Event()
        async def blocked(_trigger):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(self.runtime, '_recover_once', side_effect=blocked):
            supervisor = asyncio.create_task(self.runtime.supervise())
            await entered.wait()
            supervisor.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await supervisor
        state = self.runtime.readiness_state()
        self.assertTrue(state['request_ready'])
        self.assertFalse(state['execution_ready'])
        self.assertIn('supervisor_stopped', state['faults'])
        await self.runtime.recover_once('test')
        self.assertFalse(self.runtime.readiness_state()['execution_ready'])
        # Only a live replacement supervisor restores supervisor availability.
        supervisor = asyncio.create_task(self.runtime.supervise())
        await asyncio.sleep(0)
        self.assertTrue(self.runtime.readiness_state()['execution_ready'])
        supervisor.cancel()
        await asyncio.gather(supervisor, return_exceptions=True)

    async def test_incomplete_pass_cannot_clear_failed_recovery(self):
        with patch.object(self.runtime, '_recover_once', side_effect=RuntimeError('synthetic-private')):
            with self.assertRaises(RuntimeError):
                await self.runtime.recover_once('test')
        self.assertFalse(self.runtime.readiness_state()['execution_ready'])
        ticks = iter(range(1000))
        with patch.object(self.runtime, '_recovery_monotonic', side_effect=lambda: next(ticks) * 10):
            await self.runtime.recover_once('test')
        self.assertFalse(self.runtime.readiness_state()['execution_ready'])
        await self.runtime.recover_once('test')
        self.assertTrue(self.runtime.readiness_state()['execution_ready'])

    async def test_failed_durable_write_is_not_cleared_by_read_audit(self):
        from ha_mcp_engineering.f3.persistence import ExecutionStorageError
        from ha_mcp_engineering.errors import GovernanceError
        created = await self.fixture.create_automation_plan()
        await self.fixture.approve(created)
        with patch.object(self.runtime.children, '_atomic_write', side_effect=ExecutionStorageError('synthetic write failure')):
            with self.assertRaises(GovernanceError):
                await self.service.apply(created['plan_id'], created['plan_hash'])
        await self.runtime.recover_once('test')
        self.assertEqual(self.runtime.readiness_state()['faults'], ['execution_storage'])
        self.assertTrue(self.runtime.readiness_state()['request_ready'])
        self.assertFalse(self.runtime.readiness_state()['execution_ready'])
        self.assertFalse(any(call[0] == 'write' for call in self.fixture.gateway.calls))

    async def test_child_retry_fault_survives_backoff_until_exact_recovery(self):
        from datetime import datetime
        _, declaration = await self.fixture._preintent_active_child('readiness_retry')
        with patch.object(self.runtime, '_execute_child', side_effect=RuntimeError('synthetic-private')):
            await self.runtime.recover_once('test')
        self.assertEqual(self.runtime.readiness_state()['faults'], ['recovery_child'])
        await self.runtime.recover_once('test')
        self.assertFalse(self.runtime.readiness_state()['execution_ready'])
        retry_at = datetime.fromisoformat(self.runtime.children.runtime(declaration['child_id'])['next_eligible_at'])
        self.service.now = lambda: retry_at
        await self.runtime.recover_once('test')
        self.assertTrue(self.runtime.readiness_state()['execution_ready'])
        self.assertEqual(self.runtime.children.get(declaration['child_id']).dispatch_count, 1)
        await self.runtime.recover_once('test')
        self.assertEqual(self.runtime.children.get(declaration['child_id']).dispatch_count, 1)

    async def test_concurrent_fault_is_not_cleared_by_older_recovery(self):
        entered, finish = asyncio.Event(), asyncio.Event()
        original = self.runtime._recover_once
        async def suspended(trigger):
            entered.set()
            await finish.wait()
            return await original(trigger)
        with patch.object(self.runtime, '_recover_once', side_effect=suspended):
            pass_task = asyncio.create_task(self.runtime.recover_once('test'))
            await entered.wait()
            supervisor = self.runtime._supervisor_task
            collision = await self.runtime.recover_once('periodic')
            self.assertEqual(collision['processed'], 0)
            self.assertIs(self.runtime._supervisor_task, supervisor)
            with patch.object(self.runtime.children, 'health', side_effect=RuntimeError('synthetic-private')):
                with self.assertRaises(RuntimeError):
                    self.runtime.health()
            statuses = await asyncio.gather(*(self._request() for _ in range(8)))
            self.assertTrue(all(status == 200 and not state['f3_execution_ready'] for status, state, _ in statuses))
            finish.set()
            await pass_task
        self.assertFalse(self.runtime.readiness_state()['execution_ready'])
        await self.runtime.recover_once('test')
        self.assertTrue(self.runtime.readiness_state()['execution_ready'])

    async def test_fault_during_preflight_prevents_approval_and_provider_write(self):
        created = await self.fixture.create_automation_plan()
        await self.fixture.approve(created)
        approval = asdict(self.service._load(created['plan_id']).approval)
        entered, finish = asyncio.Event(), asyncio.Event()
        original = self.fixture.gateway.validate_all
        async def suspended():
            entered.set()
            await finish.wait()
            return await original()
        with patch.object(self.fixture.gateway, 'validate_all', side_effect=suspended):
            applying = asyncio.create_task(self.service.apply(created['plan_id'], created['plan_hash']))
            await asyncio.wait_for(entered.wait(), 10)
            with patch.object(self.runtime.children, 'health', side_effect=RuntimeError('synthetic-private')):
                with self.assertRaises(RuntimeError):
                    self.runtime.health()
            self.assertFalse((await self._request())[1]['f3_execution_ready'])
            finish.set()
            result = await asyncio.gather(applying, return_exceptions=True)
        self.assertEqual(asdict(self.service._load(created['plan_id']).approval), approval)
        self.assertFalse(any(call[0] == 'write' for call in self.fixture.gateway.calls))
        self.assertFalse(self.runtime.readiness_state()['execution_ready'])
        self.assertEqual(len(result), 1)

    async def test_gateway_security_and_catalog_barriers_remain_in_force_under_fault(self):
        with patch.object(self.runtime.children, 'health', side_effect=RuntimeError('synthetic-private')):
            with self.assertRaises(RuntimeError):
                self.runtime.health()
        gateway, app = application_gateway(self.runtime)
        with patch.object(application, 'GOVERNANCE', gateway._test_governance):
            status, _ = await gateway_fixtures.CatalogReadinessBarrierTests.request(gateway, '/unauthenticated/mcp')
            self.assertEqual(status, 404)
            gateway.global_bucket.tokens = 0
            status, _ = await gateway_fixtures.CatalogReadinessBarrierTests.request(gateway, f'/{gateway_fixtures.SECRET}/mcp')
            self.assertEqual(status, 429)
            messages = []
            async def send(message):
                messages.append(message)
            await gateway({'type': 'http', 'method': 'GET', 'path': '/ready',
                           'headers': [(b'host', b'attacker.invalid')], 'client': ('127.0.0.1', 1)},
                          Mock(), send)
            self.assertEqual(messages[0]['status'], 421)
        self.assertEqual(app.calls, 0)
        status, state, calls = await self._request(reconciled=False)
        self.assertEqual((status, calls), (503, 0))
        self.assertEqual(state['status'], 'initial_reconciliation_pending')


    async def test_projection_failure_requires_that_projection_to_succeed(self):
        _, applied, _ = await self._settled_child()
        task = self.service.task_repository.get(applied['task_id'])
        with patch.object(self.runtime, '_decorate_task', side_effect=RuntimeError('synthetic-private')):
            with self.assertRaises(RuntimeError):
                self.runtime.decorate_task(task)
            with self.assertRaises(RuntimeError):
                await self.runtime.recover_once('test')
        self.assertFalse(self.runtime.readiness_state()['execution_ready'])
        await self.runtime.recover_once('test')
        self.assertTrue(self.runtime.readiness_state()['execution_ready'])

    async def test_fault_after_intent_stops_provider_without_reissuing_dispatch(self):
        from ha_mcp_engineering.f3.executor import SharedOperationExecutor
        created = await self.fixture.create_automation_plan()
        await self.fixture.approve(created)
        original = SharedOperationExecutor._inject
        def fault(executor, stage):
            if stage == 'after_durable_intent_before_provider_invocation':
                with patch.object(self.runtime.children, 'health', side_effect=RuntimeError('synthetic-private')):
                    with self.assertRaises(RuntimeError):
                        self.runtime.health()
            return original(executor, stage)
        with patch.object(SharedOperationExecutor, '_inject', fault):
            result = await self.service.apply(created['plan_id'], created['plan_hash'])
        child = self.runtime.children.declarations_for_task(result['task_id'])[0]
        self.assertEqual(self.runtime.children.get(child['child_id']).dispatch_count, 1)
        self.assertFalse(any(call[0] == 'write' for call in self.fixture.gateway.calls))
        await self.runtime.recover_once('test')
        self.assertEqual(self.runtime.children.get(child['child_id']).dispatch_count, 1)
        self.assertFalse(any(call[0] == 'write' for call in self.fixture.gateway.calls))

if __name__ == '__main__':
    unittest.main()
