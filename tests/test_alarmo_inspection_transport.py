"""Actual loopback WebSocket peers, synthetic credentials, no external services."""

import asyncio
import json
from types import SimpleNamespace
import unittest

from aiohttp import web
from test_integration_inspection_contract import c
from ha_mcp_engineering.clients.websocket import HomeAssistantWebSocketClient
from ha_mcp_engineering.integration_inspection.models import InspectionError
from ha_mcp_engineering.request_context import begin_request, end_request


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ledger, self.connections = [], 0
        self.frame = '{"id":1,"type":"result","success":true,"result":{"domain":"alarmo","version":"1.10.19"}}'
        self.auth_version = "2026.9.3"
        self.after_auth = None
        self.hold = False
        self.drop = False
        self.redirect = False
        self.auth_frame = None
        async def handler(request):
            self.connections += 1
            if self.drop:
                request.transport.close()
                return web.Response()
            if self.redirect:
                return web.Response(status=302, headers={"Location": "/forbidden"})
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_str(self.auth_frame or '{"type":"auth_required","ha_version":"2026.9.3"}')
            try:
                await ws.receive_json()
                if self.after_auth:
                    self.after_auth()
                await ws.send_json({"type": "auth_ok", "ha_version": self.auth_version})
                command = await ws.receive()
                if command.type == web.WSMsgType.TEXT:
                    self.ledger.append(json.loads(command.data))
                    if not self.hold:
                        await ws.send_str(self.frame)
                await ws.receive()
            except (ConnectionError, TypeError, RuntimeError):
                pass
            finally:
                await ws.close()
            return ws
        app = web.Application()
        app.router.add_get("/{path:.*}", handler)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.settings = SimpleNamespace(websocket_url=f"ws://127.0.0.1:{port}/api/websocket", ha_token="SYNTHETIC_INSPECTION_TOKEN")
        self.client = HomeAssistantWebSocketClient(self.settings)
        self.telemetry, self.token = begin_request()
        self.telemetry.core_dispatch_authorizer = lambda: True

    async def asyncTearDown(self):
        end_request(self.token)
        await self.runner.cleanup()

    async def read(self, kind=c.ReadKind.MANIFEST, **changes):
        return await self.client.read_alarmo_inspection(kind,
            **{"core_version": "2026.9.3", "remaining_seconds": 1, "remaining_bytes": c.COLLECTION_BYTES, **changes})

    async def test_exact_fixed_success_one_dispatch_and_timing(self):
        value, size = await self.read()
        self.assertEqual(value, {"domain": "alarmo", "version": "1.10.19"})
        self.assertEqual(self.ledger, [{"id": 1, "type": "manifest/get", "integration": "alarmo"}])
        self.assertEqual(self.connections, 1)
        self.assertGreater(size, len(self.frame))
        self.assertEqual(self.telemetry.ha_request_count, 1)
        self.assertEqual(self.telemetry.ha_active_requests, 0)

    async def test_duplicate_keys_nonfinite_and_wrong_envelopes(self):
        cases = [
            '{"type":"result","id":1,"success":true,"result":{"x":1,"x":2}}',
            '{"type":"result","id":1,"success":true,"result":NaN}',
            '{"type":"result","id":1,"success":true,"result":1e999}',
            '{"type":"result","id":true,"success":true,"result":{}}',
            '{"type":"result","id":2,"success":true,"result":{}}',
            '{"type":"event","id":1,"event":{}}',
            '{"type":"result","id":1,"success":1,"result":{}}',
            '{"type":"result","id":1,"success":true}',
        ]
        for frame in cases:
            self.frame = frame
            before = self.connections
            with self.subTest(frame=frame), self.assertRaises(InspectionError) as found:
                await self.read()
            self.assertEqual(found.exception.reason, "malformed_response")
            self.assertEqual(self.connections, before + 1)

    async def test_fixed_error_mapping_never_reflects_text(self):
        for code, reason in (("forbidden", "access_denied"), ("unknown_command", "command_unsupported"), ("SYNTHETIC_SECRET", "source_unavailable")):
            self.frame = json.dumps({"id": 1, "type": "result", "success": False,
                                    "error": {"code": code, "message": "SYNTHETIC_PRIVATE_ERROR"}})
            with self.assertRaises(InspectionError) as found:
                await self.read()
            self.assertEqual(found.exception.reason, reason)
            self.assertNotIn("SYNTHETIC_PRIVATE_ERROR", str(found.exception))

    async def test_version_or_authority_drift_after_auth_zero_application_commands(self):
        self.auth_version = "2026.9.99"
        with self.assertRaises(InspectionError) as found:
            await self.read()
        self.assertEqual(found.exception.reason, "identity_drift")
        self.assertEqual(self.ledger, [])
        self.auth_version = "2026.9.3"
        self.after_auth = lambda: setattr(self.telemetry, "core_dispatch_authorizer", lambda: False)
        with self.assertRaises(InspectionError) as found:
            await self.read()
        self.assertEqual(found.exception.reason, "authority_unavailable")
        self.assertEqual(self.ledger, [])

    async def test_result_authority_revocation_does_not_publish(self):
        count = [0]
        def authorize():
            count[0] += 1
            return count[0] < 3
        self.telemetry.core_dispatch_authorizer = authorize
        with self.assertRaises(InspectionError) as found:
            await self.read()
        self.assertEqual(found.exception.reason, "authority_unavailable")
        self.assertEqual(len(self.ledger), 1)

    async def test_oversized_auth_result_and_aggregate_budget_fail(self):
        self.auth_frame = json.dumps({"type": "auth_required", "junk": "x" * c.AUTH_BYTES})
        with self.assertRaises(InspectionError):
            await self.read()
        self.assertEqual(self.ledger, [])
        self.auth_frame = None
        self.frame = '{"id":1,"type":"result","success":true,"result":"' + "x" * c.FRAME_BYTES + '"}'
        with self.assertRaises(InspectionError):
            await self.read()
        self.frame = '{"id":1,"type":"result","success":true,"result":{}}'
        with self.assertRaises(InspectionError):
            await self.read(remaining_bytes=70)

    async def test_exact_result_and_auth_byte_boundaries_then_plus_one(self):
        prefix, suffix = '{"id":1,"type":"result","success":true,"result":"', '"}'
        self.frame = prefix + "x" * (c.FRAME_BYTES - len(prefix + suffix)) + suffix
        value, consumed = await self.read()
        self.assertEqual(len(value), c.FRAME_BYTES - len(prefix + suffix))
        self.assertLessEqual(consumed, c.FRAME_BYTES + 2 * c.AUTH_BYTES)
        self.frame = self.frame[:-2] + 'x"}'
        with self.assertRaises(InspectionError) as found:
            await self.read()
        self.assertEqual(found.exception.reason, "response_bytes")
        self.frame = '{"id":1,"type":"result","success":true,"result":{}}'
        prefix = '{"type":"auth_required","padding":"'
        self.auth_frame = prefix + "x" * (c.AUTH_BYTES - len(prefix + suffix)) + suffix
        await self.read()
        before = len(self.ledger)
        self.auth_frame = self.auth_frame[:-2] + 'x"}'
        with self.assertRaises(InspectionError) as found:
            await self.read()
        self.assertEqual(found.exception.reason, "response_bytes")
        self.assertEqual(len(self.ledger), before)

    async def test_timeout_cancel_drop_and_redirect_never_retry(self):
        self.hold = True
        with self.assertRaises(InspectionError) as found:
            await self.read(remaining_seconds=0.02)
        self.assertEqual(found.exception.reason, "timeout")
        task = asyncio.create_task(self.read())
        await asyncio.sleep(0.02)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.hold = False
        self.drop = True
        before = self.connections
        with self.assertRaises(InspectionError):
            await self.read()
        self.assertEqual(self.connections, before + 1)
        self.drop = False
        self.redirect = True
        before = self.connections
        with self.assertRaises(InspectionError):
            await self.read()
        self.assertEqual(self.connections, before + 1)

    async def test_no_request_authority_zero_connections(self):
        self.telemetry.core_dispatch_authorizer = None
        with self.assertRaises(InspectionError):
            await self.read()
        self.assertEqual(self.connections, 0)
