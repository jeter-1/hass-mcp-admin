"""Disposable loopback peers prove scoped no-retry/no-redirect read transport."""

import asyncio
from pathlib import Path
import sys
import unittest
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.clients.rest import HomeAssistantRestClient
from ha_mcp_engineering.clients.websocket import HomeAssistantWebSocketClient
from ha_mcp_engineering.clients.single_read import SINGLE_READ, MAX_READ_BYTES, session_options
from ha_mcp_engineering.configuration import Settings
from ha_mcp_engineering.errors import HomeAssistantUnavailableError


class ReverificationTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls, self.commands = [], []
        self.mode = "ok"
        async def handler(request):
            self.calls.append(request.path)
            if self.mode == "disconnect":
                request.transport.close()
                return web.Response()
            if self.mode == "redirect" and request.path != "/redirected":
                raise web.HTTPFound("/redirected")
            if self.mode == "oversize":
                return web.Response(body=b"x" * (MAX_READ_BYTES + 1))
            if request.path == "/api/websocket":
                ws = web.WebSocketResponse()
                await ws.prepare(request)
                await ws.send_json({"type": "auth_required"})
                await ws.receive_json()
                await ws.send_json({"type": "auth_ok"})
                command = await ws.receive_json()
                self.commands.append(command)
                if self.mode == "ws_oversize":
                    await ws.send_str("x" * (MAX_READ_BYTES + 1))
                else:
                    await ws.send_json({"id": 1, "type": "result", "success": True,
                                        "result": {"observed": True}})
                await ws.close()
                return ws
            return web.json_response({"observed": True})
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", handler)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.settings = Settings(ha_url=f"http://127.0.0.1:{port}",
            ha_token="synthetic-no-real-token", access_secret="synthetic-no-real-secret",
            port=8100, audit_path="unused-synthetic", rate_limit_per_minute=120,
            rate_limit_burst=20, destructive_services=frozenset(), ha_timeout_seconds=1.0)
        self.rest = HomeAssistantRestClient(self.settings)
        self.ws = HomeAssistantWebSocketClient(self.settings)
        self.token = SINGLE_READ.set(True)

    async def asyncTearDown(self):
        SINGLE_READ.reset(self.token)
        await self.runner.cleanup()

    async def test_success_one_request_and_one_ws_command(self):
        self.assertEqual(await self.rest.request("GET", "/config/automation/config/example"), {"observed": True})
        self.assertEqual(await self.ws.command({"type": "input_boolean/list"}), {"observed": True})
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.commands, [{"id": 1, "type": "input_boolean/list"}])

    async def test_lost_connection_is_not_retried(self):
        self.mode = "disconnect"
        with self.assertRaises(HomeAssistantUnavailableError):
            await self.rest.request("GET", "/config/automation/config/example")
        self.assertEqual(len(self.calls), 1)
        self.calls.clear()
        with self.assertRaises(HomeAssistantUnavailableError):
            await self.ws.command({"type": "input_boolean/list"})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.commands, [])

    async def test_redirect_never_reaches_second_target(self):
        self.mode = "redirect"
        with self.assertRaises(HomeAssistantUnavailableError):
            await self.rest.request("GET", "/config/automation/config/example")
        with self.assertRaises(HomeAssistantUnavailableError):
            await self.ws.command({"type": "input_boolean/list"})
        self.assertNotIn("/redirected", self.calls)
        self.assertEqual(len(self.calls), 2)

    async def test_bounded_download_and_websocket_frame(self):
        self.mode = "oversize"
        with self.assertRaises(HomeAssistantUnavailableError):
            await self.rest.request("GET", "/config/automation/config/example")
        self.mode = "ws_oversize"
        # The WS client maps an oversized frame/connection closure to its
        # existing typed API/transport failure, never an accepted configuration.
        with self.assertRaises(Exception):
            await asyncio.wait_for(self.ws.command({"type": "input_boolean/list"}), 2)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.commands), 1)

    async def test_scope_does_not_change_existing_transport(self):
        token = SINGLE_READ.set(False)
        try:
            self.assertEqual(session_options(), {})
            self.mode = "redirect"
            self.assertEqual(await self.rest.request("GET", "/config/automation/config/example"), {"observed": True})
            self.assertEqual(self.calls[-1], "/redirected")
        finally:
            SINGLE_READ.reset(token)
