"""Disposable loopback native peers and pre-decode limits; no household access."""

import asyncio
import json
from pathlib import Path
import sys
import subprocess
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.clients.dashboard_analysis import CollectionBudget, DashboardAnalysisClient
from ha_mcp_engineering.dashboard_analysis import contracts as c
from ha_mcp_engineering.dashboard_analysis.provider import project_inventory


class NativeTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.states_raw = b'[{"entity_id":"sensor.test","state":"unavailable"}]'
        self.status = 200
        self.encoding = "identity"
        self.delay = 0
        self.websocket_status = 200
        self.registry = [{"entity_id": "sensor.test", "disabled_by": None}]
        self.registry_error = False
        self.registry_error_code = "unauthorized"
        self.greeting = {"type": "auth_required", "ha_version": "2026.9.4"}
        self.auth = {"type": "auth_ok", "ha_version": "2026.9.4"}
        self.authorized = True
        self.authority_checks = 0
        app = web.Application()
        app.router.add_get('/api/states', self.states)
        app.router.add_get('/api/websocket', self.websocket)
        app.router.add_get('/forbidden', self.forbidden)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.client = DashboardAnalysisClient(SimpleNamespace(
            api_url=f'http://127.0.0.1:{port}/api', websocket_url=f'http://127.0.0.1:{port}/api/websocket',
            ha_token='synthetic-offline-token', ha_timeout_seconds=2))
        self.budget = CollectionBudget()

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def states(self, request):
        self.calls.append("states")
        await asyncio.sleep(self.delay)
        return web.Response(body=self.states_raw, status=self.status,
                            headers={"Content-Encoding": self.encoding, "Location": "/forbidden"})

    async def websocket(self, request):
        self.calls.append("websocket")
        if self.websocket_status != 200:
            return web.Response(status=self.websocket_status,
                                headers={"Location": "/forbidden"})
        ws = web.WebSocketResponse(compress=False)
        await ws.prepare(request)
        await ws.send_json(self.greeting)
        async for msg in ws:
            if msg.type.name != "TEXT":
                break
            incoming = json.loads(msg.data)
            if incoming.get("type") == "auth":
                self.calls.append("auth")
                await ws.send_json(self.auth)
            else:
                self.calls.append(incoming)
                if self.registry_error:
                    await ws.send_json({"id": incoming["id"], "type": "result", "success": False,
                                        "error": {"code": self.registry_error_code, "message": "synthetic-secret"}})
                else:
                    await ws.send_json({"id": incoming["id"], "type": "result", "success": True,
                                        "result": self.registry})
        return ws

    async def forbidden(self, request):
        self.calls.append("forbidden_redirect")
        return web.Response(status=403)

    def authorize(self):
        self.authority_checks += 1
        if not self.authorized:
            raise c.AnalysisError("authority_unavailable")

    async def test_exact_two_reads_and_closed_command_shape(self):
        async with self.client.collection("2026.9.4", self.authorize, self.budget) as peer:
            states = await peer.read("states")
            registry = await peer.read("registry")
        self.assertEqual(states[0]["state"], "unavailable")
        self.assertEqual(registry, self.registry)
        self.assertEqual(self.calls, ["states", "websocket", "auth", {"id": 1, "type": "config/entity_registry/list"}])
        self.assertTrue(peer.session.closed)
        self.assertTrue(peer.websocket.closed)
        self.assertEqual(self.budget.requests, 2)
        self.assertGreaterEqual(self.authority_checks, 5)

    async def test_unknown_repeated_and_unavailable_authority_never_dispatch(self):
        async with self.client.collection("2026.9.4", self.authorize, self.budget) as peer:
            with self.assertRaises(c.AnalysisError):
                await peer.read("services")
            self.authorized = False
            with self.assertRaises(c.AnalysisError):
                await peer.read("states")
            self.authorized = True
            with self.assertRaises(c.AnalysisError):
                await peer.read("states")
        self.assertEqual(self.calls, [])

    async def test_oversize_http_refuses_before_decode(self):
        self.states_raw = b' ' * 1025
        with patch.object(c, 'INVENTORY_BYTES', 1024), patch.object(c, 'parse') as parser:
            async with self.client.collection("2026.9.4", self.authorize, self.budget) as peer:
                with self.assertRaises(c.AnalysisError) as error:
                    await peer.read("states")
        self.assertEqual(error.exception.reason, "response_limit")
        parser.assert_not_called()
        self.assertEqual(self.calls, ["states"])

    async def test_oversize_auth_refuses_before_decode(self):
        self.greeting['extra'] = 'x' * 1025
        with patch.object(c, 'AUTH_BYTES', 1024), patch.object(c, 'parse') as parser:
            async with self.client.collection("2026.9.4", self.authorize, self.budget) as peer:
                with self.assertRaises(c.AnalysisError):
                    await peer.read("registry")
        parser.assert_not_called()
        self.assertEqual(self.calls, ["websocket"])

    async def test_access_denial_not_partial_or_reflected(self):
        self.registry_error = True
        async with self.client.collection("2026.9.4", self.authorize, self.budget) as peer:
            with self.assertRaises(c.AnalysisError) as error:
                await peer.read("registry")
        self.assertEqual(error.exception.reason, "access_denied")
        self.assertNotIn("synthetic-secret", str(error.exception))

    async def test_http_access_denial(self):
        for status in (401, 403):
            self.status = status
            async with self.client.collection("2026.9.4", self.authorize, CollectionBudget()) as peer:
                with self.assertRaises(c.AnalysisError) as error:
                    await peer.read("states")
            self.assertEqual(error.exception.reason, "access_denied")

    async def test_websocket_upgrade_access_denial_aborts(self):
        for status in (401, 403):
            self.websocket_status = status
            before = len(self.calls)
            async with self.client.collection("2026.9.4", self.authorize, CollectionBudget()) as peer:
                with self.assertRaises(c.AnalysisError) as error:
                    await peer.read("registry")
            self.assertEqual(error.exception.reason, "access_denied")
            self.assertFalse(error.exception.retryable)
            self.assertEqual(self.calls[before:], ["websocket"])
            self.assertTrue(peer.session.closed)

    async def test_websocket_redirect_is_never_followed(self):
        for status in (301, 302, 303, 307, 308):
            self.websocket_status = status
            before = len(self.calls)
            async with self.client.collection("2026.9.4", self.authorize, CollectionBudget()) as peer:
                with self.assertRaises(c.AnalysisError) as error:
                    await peer.read("registry")
            self.assertEqual(error.exception.reason, "source_unavailable")
            self.assertEqual(self.calls[before:], ["websocket"])
            self.assertTrue(peer.session.closed)

    async def test_unclassified_rejection_is_not_claimed_access_denial(self):
        self.registry_error = True
        self.registry_error_code = 'synthetic-unknown-rejection'
        async with self.client.collection("2026.9.4", self.authorize, self.budget) as peer:
            with self.assertRaises(c.AnalysisError) as error:
                await peer.read("registry")
        self.assertEqual(error.exception.reason, "source_rejected")
        self.assertFalse(error.exception.retryable)
        self.assertNotIn(self.registry_error_code, str(error.exception))

    async def test_redirect_and_error_have_one_attempt(self):
        for status in (302, 503):
            self.status = status
            before = len(self.calls)
            async with self.client.collection("2026.9.4", self.authorize, CollectionBudget()) as peer:
                with self.assertRaises(c.AnalysisError):
                    await peer.read("states")
            self.assertEqual(len(self.calls), before + 1)

    async def test_compression_is_refused(self):
        self.encoding = "gzip"
        async with self.client.collection("2026.9.4", self.authorize, self.budget) as peer:
            with self.assertRaises(c.AnalysisError) as error:
                await peer.read("states")
        self.assertEqual(error.exception.reason, "malformed_response")

    async def test_duplicate_and_nonfinite_wire_values_refuse(self):
        for raw in (b'[{"entity_id":"sensor.test","entity_id":"sensor.other"}]', b'[NaN]'):
            self.states_raw = raw
            async with self.client.collection("2026.9.4", self.authorize, CollectionBudget()) as peer:
                with self.assertRaises(c.AnalysisError):
                    await peer.read("states")

    async def test_version_drift_prevents_registry_command(self):
        self.greeting['ha_version'] = '2026.9.5'
        async with self.client.collection("2026.9.4", self.authorize, self.budget) as peer:
            with self.assertRaises(c.AnalysisError) as error:
                await peer.read("registry")
        self.assertEqual(error.exception.reason, "authority_drift")
        self.assertEqual(self.calls, ["websocket"])

    async def test_deadline_no_retry_and_closes(self):
        self.delay = .1
        self.client._read_timeout = .02
        async with self.client.collection("2026.9.4", self.authorize, self.budget) as peer:
            with self.assertRaises(c.AnalysisError) as error:
                await peer.read("states")
        self.assertEqual(error.exception.reason, "timeout")
        self.assertEqual(self.calls, ["states"])
        self.assertTrue(peer.session.closed)

    async def test_authority_change_before_return_refuses(self):
        original = self.authorize
        def retire():
            if self.authority_checks == 1:
                self.authorized = False
            original()
        async with self.client.collection("2026.9.4", retire, self.budget) as peer:
            with self.assertRaises(c.AnalysisError):
                await peer.read("states")
        self.assertEqual(self.calls, ["states"])

    async def test_maximum_native_path_responsiveness(self):
        # Keep full-suite heap/GC history out of this measurement. The child
        # preserves natural GC and reports both wall and loop-thread CPU gaps.
        root = Path(__file__).resolve().parents[1]
        command = [sys.executable, '-I', '-B', '-c',
                   'import sys,unittest; sys.path.insert(0,sys.argv.pop(1)); unittest.main(module=None)',
                   str(root / 'tests'),
                   'test_dashboard_analysis_transport.NativeTransportTests._maximum_native_path', '-v']
        result = await asyncio.to_thread(subprocess.run, command, cwd=root, capture_output=True,
                                         text=True, timeout=30, check=False)
        print(result.stdout, end='', flush=True)
        for line in result.stderr.splitlines():
            print(line.replace('Ran ', 'Isolated child summary: ', 1) if line.startswith('Ran ') else line,
                  file=sys.stderr, flush=True)
        self.assertEqual(result.returncode, 0, 'Native inventory responsiveness child failed.')

    async def _maximum_native_path(self):
        import gc
        import threading
        raw = c.canonical([{'entity_id': f'sensor.sample{i}', 'state': 'unknown'}
                           for i in range(c.INVENTORY_ENTRIES)])
        self.states_raw = raw + b' ' * (c.INVENTORY_BYTES - len(raw))
        original_gc = (gc.isenabled(), gc.get_threshold())
        for contention in (False, True):
            gaps, cpu_gaps, events, starts = [], [], [], {}
            loop_thread = threading.get_ident()
            async def heartbeat():
                last, last_cpu = time.perf_counter(), time.thread_time()
                while True:
                    await asyncio.sleep(.005)
                    now, cpu = time.perf_counter(), time.thread_time()
                    gaps.append(now - last)
                    cpu_gaps.append(cpu - last_cpu)
                    last, last_cpu = now, cpu
            def observe_gc(phase, info):
                if info['generation'] != 2:
                    return
                thread = threading.get_ident()
                if phase == 'start':
                    starts[thread] = (time.perf_counter(), time.thread_time())
                elif thread in starts and len(events) < 64:
                    wall, cpu = starts.pop(thread)
                    events.append({'event_loop_thread': thread == loop_thread,
                                   'wall_seconds': time.perf_counter() - wall,
                                   'thread_cpu_seconds': time.thread_time() - cpu})
            pulse = asyncio.create_task(heartbeat())
            stress = asyncio.create_task(asyncio.to_thread(sum, range(4_000_000))) if contention else None
            gc.callbacks.append(observe_gc)
            try:
                await asyncio.sleep(0)
                async with self.client.collection('2026.9.4', self.authorize, CollectionBudget()) as peer:
                    decoded = await peer.read('states')
                    projection = await c.worker(project_inventory, 'states', decoded)
                if stress:
                    await stress
                await asyncio.sleep(.01)
            finally:
                gc.callbacks.remove(observe_gc)
                pulse.cancel()
                await asyncio.gather(pulse, return_exceptions=True)
                if stress:
                    await stress
            print(json.dumps({'fixture': 'native_dashboard_inventory_4MiB_10000',
                'controlled_contention': contention, 'maximum_loop_gap_seconds': max(gaps),
                'maximum_thread_cpu_gap_seconds': max(cpu_gaps), 'major_gc_events': events,
                'gc_enabled': original_gc[0], 'gc_thresholds': original_gc[1],
                'wall_bound_seconds': .25, 'thread_cpu_bound_seconds': .1}), flush=True)
            self.assertEqual(len(projection.records), c.INVENTORY_ENTRIES)
            self.assertTrue(projection.complete)
            self.assertLess(max(gaps), .25)
            self.assertLess(max(cpu_gaps), .1)
            self.assertEqual((gc.isenabled(), gc.get_threshold()), original_gc)


class BudgetTests(unittest.TestCase):
    def test_aggregate_bytes_and_collection_deadline(self):
        now = [0]
        budget = CollectionBudget(clock=lambda: now[0])
        budget.charge(c.TOTAL_BYTES)
        with self.assertRaises(c.AnalysisError):
            budget.charge(1)
        now[0] = 40
        with self.assertRaises(c.AnalysisError):
            budget.remaining()


class UpstreamTransportTests(unittest.IsolatedAsyncioTestCase):
    """Real locked SDK against one disposable MCP-shaped loopback peer."""

    async def asyncSetUp(self):
        import threading
        from ha_mcp_engineering.clients.mcp import McpDashboardTransport
        self.owner = threading.get_ident()
        self.calls, self.authority_threads, self.validator_threads = [], [], []
        self.arguments = {'url_path': 'synthetic', 'list_only': False,
                          'force_reload': True, 'include_screenshot': False}
        self.available = True
        self.reject = None
        self.mode = 'json'
        self.call_raw = None
        self.init_padding = ''
        self.catalog_count = 1
        self.catalog_padding = ''
        self.catalog_pages = 0
        self.repeat_cursor = False
        self.failure_at, self.failure_status = None, 200
        self.drop_at = None
        self.delay_at, self.delay = None, 0
        self.entered = asyncio.Event()
        self.retire_after_call = False
        app = web.Application()
        app.router.add_route('*', '/mcp', self.handle)
        app.router.add_route('*', '/forbidden', self.forbidden)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.transport = McpDashboardTransport(f'http://127.0.0.1:{port}/mcp',
                                               timeout_seconds=2, client_version='synthetic')
        self.budget = CollectionBudget()

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def forbidden(self, request):
        self.calls.append('forbidden')
        return web.Response(status=403)

    async def handle(self, request):
        if request.method != 'POST':
            self.calls.append(request.method)
            return web.Response(status=204 if request.method == 'DELETE' else 405)
        message = await request.json()
        method = message['method']
        self.calls.append(method)
        if method == self.drop_at:
            request.transport.close()
            return web.Response()
        if self.delay_at == method:
            self.entered.set()
            await asyncio.sleep(self.delay)
        if method == self.failure_at:
            return web.Response(status=self.failure_status, text='synthetic-private-error',
                                headers={'Location': '/forbidden'})
        if method == 'notifications/initialized':
            return web.Response(status=202)
        if method == 'initialize':
            value = {'protocolVersion': '2025-03-26', 'capabilities': {'tools': {}},
                     'serverInfo': {'name': 'synthetic-ha-mcp', 'version': '0.0.0'},
                     'instructions': self.init_padding}
        elif method == 'tools/list':
            value = {'tools': [{'name': 'ha_config_get_dashboard' if i == 0 else f'synthetic_{i}',
                                'description': self.catalog_padding,
                                'inputSchema': {'type': 'object', 'properties': {}}}
                               for i in range(self.catalog_count)]}
            if self.repeat_cursor:
                value['nextCursor'] = 'synthetic-repeat'
            if self.calls.count('tools/list') < self.catalog_pages:
                value['nextCursor'] = str(self.calls.count('tools/list'))
        elif method == 'tools/call':
            self.assertEqual(message['params'], {'name': 'ha_config_get_dashboard', 'arguments': self.arguments})
            value = {'content': [{'type': 'text', 'text': '{}'}], 'isError': False}
            if self.retire_after_call:
                self.available = False
        else:
            self.fail('Unexpected upstream method')
        raw = self.call_raw if method == 'tools/call' and self.call_raw is not None else c.canonical({
            'jsonrpc': '2.0', 'id': message['id'], 'result': value})
        headers = {'mcp-session-id': 'synthetic-analysis-session'}
        if method == 'tools/call' and self.mode != 'json':
            body = (b'id: synthetic-resume\ndata:\n\n' if self.mode == 'incomplete_sse'
                    else b'event: message\ndata: ' + raw + b'\n\n')
            return web.Response(body=body, content_type='text/event-stream', headers=headers)
        return web.Response(body=raw, content_type='application/json', headers=headers)

    def authorize(self):
        import threading
        self.authority_threads.append(threading.get_ident())
        if not self.available:
            raise c.AnalysisError('authority_unavailable')

    def validate(self, handshake):
        import threading
        from ha_mcp_engineering.clients.mcp import DashboardTransportError
        self.validator_threads.append(threading.get_ident())
        self.assertEqual(handshake.protocol_version, '2025-03-26')
        if self.reject:
            raise DashboardTransportError(self.reject, retryable=False)

    async def call(self, arguments=None):
        return await self.transport.execute_analysis_read(
            self.arguments if arguments is None else arguments,
            self.validate, authorize=self.authorize, budget=self.budget)

    async def test_json_and_sse_success_one_call_no_get_and_owner_thread_admission(self):
        for mode in ('json', 'sse'):
            self.mode = mode
            self.calls.clear()
            self.budget = CollectionBudget()
            result = await self.call()
            self.assertFalse(result.call_result['isError'])
            self.assertEqual(self.calls, ['initialize', 'notifications/initialized', 'tools/list', 'tools/call', 'DELETE'])
            self.assertEqual(self.budget.requests, 5)
            self.assertEqual(set(self.authority_threads), {self.owner})
            self.assertEqual(set(self.validator_threads), {self.owner})
            self.assertGreater(self.budget.bytes, 0)

    async def test_wrong_selector_or_unavailable_authority_zero_wire_requests(self):
        from ha_mcp_engineering.clients.mcp import DashboardTransportError
        for arguments in ({'list_only': True, 'include_screenshot': False},
                          dict(self.arguments, force_reload=False),
                          dict(self.arguments, include_screenshot=True),
                          dict(self.arguments, url_path='../synthetic')):
            with self.assertRaises(DashboardTransportError):
                await self.call(arguments)
        self.available = False
        with self.assertRaises(c.AnalysisError):
            await self.call()
        self.assertEqual(self.calls, [])

    async def test_admission_refusal_no_repeat_or_dashboard_dispatch(self):
        from ha_mcp_engineering.clients.mcp import DashboardTransportError
        self.reject = 'upstream_attestation_missing'
        with self.assertRaises(DashboardTransportError) as error:
            await self.call()
        self.assertEqual(error.exception.category, self.reject)
        self.assertEqual(self.calls.count('initialize'), 1)
        self.assertEqual(self.calls.count('tools/list'), 1)
        self.assertNotIn('tools/call', self.calls)
        self.assertNotIn('GET', self.calls)

    async def test_oversize_json_and_sse_refuse_before_decoding_on_worker(self):
        import threading
        original = c.parse
        parsed = []
        def observe(raw, **kwargs):
            parsed.append((len(raw), threading.get_ident()))
            return original(raw, **kwargs)
        self.call_raw = b' ' * 1025
        for mode in ('json', 'sse'):
            self.mode = mode
            self.budget = CollectionBudget()
            with patch.object(c, 'DASHBOARD_BYTES', 1024), patch.object(c, 'parse', observe):
                with self.assertRaises(c.AnalysisError) as error:
                    await self.call()
            self.assertEqual(error.exception.reason, 'response_limit')
        self.assertTrue(parsed)
        self.assertTrue(all(size <= 1024 and thread != self.owner for size, thread in parsed))

    async def test_auth_and_aggregate_bounds(self):
        self.init_padding = 'x' * 2048
        with patch.object(c, 'AUTH_BYTES', 1024), self.assertRaises(c.AnalysisError) as error:
            await self.call()
        self.assertEqual(error.exception.reason, 'response_limit')
        self.assertEqual(self.calls, ['initialize'])
        self.init_padding = ''
        self.calls.clear()
        self.budget = CollectionBudget()
        self.budget.bytes = c.TOTAL_BYTES - 10
        with self.assertRaises(c.AnalysisError):
            await self.call()
        self.assertEqual(self.calls, ['initialize'])

    async def test_redirect_auth_and_server_failure_no_retry_or_reflection(self):
        from ha_mcp_engineering.clients.mcp import DashboardTransportError
        self.failure_at = 'tools/call'
        for status, category in ((302, 'endpoint_rejected'), (401, 'authentication_failed'),
                                 (403, 'authentication_failed'), (503, 'upstream_error')):
            self.failure_status = status
            self.calls.clear()
            self.budget = CollectionBudget()
            with self.assertRaises(DashboardTransportError) as error:
                await self.call()
            self.assertEqual(error.exception.category, category)
            self.assertEqual(self.calls.count('tools/call'), 1)
            self.assertNotIn('GET', self.calls)
            self.assertNotIn('forbidden', self.calls)
            self.assertNotIn('synthetic-private-error', str(error.exception))

    async def test_duplicate_keys_nonfinite_and_incomplete_sse_refuse(self):
        from ha_mcp_engineering.clients.mcp import DashboardTransportError
        for raw in (b'{"jsonrpc":"2.0","id":2,"id":2,"result":{}}', b'{"x":NaN}'):
            self.call_raw = raw
            self.budget = CollectionBudget()
            with self.assertRaises(c.AnalysisError) as error:
                await self.call()
            self.assertEqual(error.exception.reason, 'malformed_response')
        self.call_raw = None
        self.mode = 'incomplete_sse'
        self.calls.clear()
        self.budget = CollectionBudget()
        with self.assertRaises(DashboardTransportError) as error:
            await self.call()
        self.assertEqual(error.exception.category, 'protocol_error')
        self.assertEqual(self.calls.count('tools/call'), 1)
        self.assertNotIn('GET', self.calls)

    async def test_disconnect_never_repeats_application_call(self):
        from ha_mcp_engineering.clients.mcp import DashboardTransportError
        self.drop_at = 'tools/call'
        with self.assertRaises(DashboardTransportError):
            await self.call()
        self.assertEqual(self.calls.count('tools/call'), 1)
        self.assertEqual(self.calls.count('initialize'), 1)
        self.assertNotIn('GET', self.calls)

    async def test_rpc_rejection_and_identity_contradiction_do_not_reflect(self):
        import io
        import logging
        from ha_mcp_engineering.clients.mcp import DashboardTransportError
        secret = 'SYNTHETIC_PRIVATE_RPC_MESSAGE'
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        previous = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            for message in ({'jsonrpc': '2.0', 'id': 2,
                             'error': {'code': -32603, 'message': secret}},
                            {'jsonrpc': '2.0', 'id': 999, 'result': {'private': secret}}):
                self.call_raw = c.canonical(message)
                self.budget = CollectionBudget()
                with self.assertRaises(DashboardTransportError) as error:
                    await self.call()
                self.assertNotIn(secret, str(error.exception))
                self.assertNotIn(secret, repr(error.exception.evidence_details()))
        finally:
            root.removeHandler(handler)
            root.setLevel(previous)
        self.assertNotIn(secret, stream.getvalue())

    async def test_catalog_descriptor_limit_and_cursor_cycle_prevent_call(self):
        from ha_mcp_engineering.clients.mcp import DashboardTransportError
        self.catalog_count = 501
        with self.assertRaises(DashboardTransportError):
            await self.call()
        self.assertNotIn('tools/call', self.calls)
        self.catalog_count = 1
        self.repeat_cursor = True
        self.calls.clear()
        self.budget = CollectionBudget()
        with self.assertRaises(DashboardTransportError):
            await self.call()
        self.assertEqual(self.calls.count('tools/list'), 2)
        self.assertNotIn('tools/call', self.calls)

    async def test_catalog_page_limit_and_read_deadline(self):
        from datetime import timedelta
        from ha_mcp_engineering.clients.mcp import DashboardTransportError
        self.catalog_pages = 21
        with self.assertRaises(DashboardTransportError):
            await self.call()
        self.assertEqual(self.calls.count('tools/list'), 20)
        self.assertNotIn('tools/call', self.calls)
        self.catalog_pages = 0
        self.calls.clear()
        self.budget = CollectionBudget()
        self.transport._timeout = timedelta(seconds=.08)
        self.delay_at, self.delay = 'tools/call', .2
        with self.assertRaises(DashboardTransportError) as error:
            await self.call()
        self.assertEqual(error.exception.category, 'timeout')
        self.assertEqual(self.calls.count('tools/call'), 1)
        self.assertNotIn('GET', self.calls)

    async def test_authority_retirement_after_response_aborts(self):
        self.retire_after_call = True
        with self.assertRaises(c.AnalysisError) as error:
            await self.call()
        self.assertEqual(error.exception.reason, 'authority_unavailable')
        self.assertEqual(self.calls.count('tools/call'), 1)

    async def test_cancelled_discovery_drains_worker_without_later_call(self):
        self.delay_at, self.delay = 'tools/list', .1
        task = asyncio.create_task(self.call())
        await asyncio.wait_for(self.entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        self.assertNotIn('tools/call', self.calls)
        before = list(self.calls)
        await asyncio.sleep(.1)
        self.assertEqual(self.calls, before)

    async def test_maximum_upstream_transport_responsiveness(self):
        root = Path(__file__).resolve().parents[1]
        command = [sys.executable, '-I', '-B', '-c',
                   'import sys,unittest; sys.path.insert(0,sys.argv.pop(1)); unittest.main(module=None)',
                   str(root / 'tests'),
                   'test_dashboard_analysis_transport.UpstreamTransportTests._maximum_upstream_path', '-v']
        result = await asyncio.to_thread(subprocess.run, command, cwd=root, capture_output=True,
                                         text=True, timeout=30, check=False)
        print(result.stdout, end='', flush=True)
        for line in result.stderr.splitlines():
            print(line.replace('Ran ', 'Isolated child summary: ', 1) if line.startswith('Ran ') else line,
                  file=sys.stderr, flush=True)
        self.assertEqual(result.returncode, 0, 'Upstream transport responsiveness child failed.')

    async def _maximum_upstream_path(self):
        import gc
        import threading
        raw = c.canonical({'jsonrpc': '2.0', 'id': 2, 'result': {
            'content': [{'type': 'text', 'text': '{}'}], 'isError': False}})
        self.call_raw = raw + b' ' * (c.DASHBOARD_BYTES - len(raw))
        self.catalog_count = 500
        self.catalog_padding = 'x' * 3500
        original_gc = (gc.isenabled(), gc.get_threshold())
        for contention in (False, True):
            gaps, cpu_gaps, events, starts = [], [], [], {}
            loop_thread = threading.get_ident()
            async def heartbeat():
                last, last_cpu = time.perf_counter(), time.thread_time()
                while True:
                    await asyncio.sleep(.005)
                    now, cpu = time.perf_counter(), time.thread_time()
                    gaps.append(now - last)
                    cpu_gaps.append(cpu - last_cpu)
                    last, last_cpu = now, cpu
            def observe_gc(phase, info):
                if info['generation'] != 2:
                    return
                thread = threading.get_ident()
                if phase == 'start':
                    starts[thread] = (time.perf_counter(), time.thread_time())
                elif thread in starts and len(events) < 64:
                    wall, cpu = starts.pop(thread)
                    events.append({'event_loop_thread': thread == loop_thread,
                        'wall_seconds': time.perf_counter() - wall,
                        'thread_cpu_seconds': time.thread_time() - cpu})
            pulse = asyncio.create_task(heartbeat())
            stress = asyncio.create_task(asyncio.to_thread(sum, range(4_000_000))) if contention else None
            gc.callbacks.append(observe_gc)
            try:
                await asyncio.sleep(0)
                self.budget = CollectionBudget()
                result = await self.call()
                if stress:
                    await stress
                await asyncio.sleep(.01)
            finally:
                gc.callbacks.remove(observe_gc)
                pulse.cancel()
                await asyncio.gather(pulse, return_exceptions=True)
                if stress:
                    await stress
            print(json.dumps({'fixture': 'upstream_dashboard_2MiB_500_descriptors',
                'controlled_contention': contention, 'maximum_loop_gap_seconds': max(gaps),
                'maximum_thread_cpu_gap_seconds': max(cpu_gaps), 'major_gc_events': events,
                'gc_enabled': original_gc[0], 'gc_thresholds': original_gc[1],
                'wall_bound_seconds': .25, 'thread_cpu_bound_seconds': .1}), flush=True)
            self.assertFalse(result.call_result['isError'])
            self.assertEqual(len(result.handshake.tools), 500)
            self.assertLess(max(gaps), .25)
            self.assertLess(max(cpu_gaps), .1)
            self.assertEqual((gc.isenabled(), gc.get_threshold()), original_gc)


if __name__ == '__main__':
    unittest.main()
