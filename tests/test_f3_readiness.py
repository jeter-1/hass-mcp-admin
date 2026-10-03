"""F3 request admission: real Linux stores, synthetic writers, no live peers."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import gc
import json
import threading
import time
import unittest
from unittest.mock import patch

from tests import test_f3_orphan_child_recovery as history_fixtures
from tests import test_beta24_pre_rc_hardening as gateway_fixtures
from ha_mcp_engineering import application
from tests.test_catalog_readiness import application_gateway


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


if __name__ == '__main__':
    unittest.main()
