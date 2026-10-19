"""HAMCP-211: Engineering transport behavior pinned on aiohttp 3.14.4 / yarl 1.25.1.

Raw loopback HTTP and WebSocket peers with synthetic data only; no external
service, credential or live Home Assistant. Raw sockets are used so the peer
can emit exact header bytes and frame sequences a framework server would
normalize or refuse.
"""

import asyncio
import base64
from dataclasses import replace
import hashlib
from importlib import metadata
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest

import aiohttp
from yarl import URL

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.clients import paths
from ha_mcp_engineering.clients.paths import UnsafeRequestPathError, request_url, rest_path
from ha_mcp_engineering.clients.rest import HomeAssistantRestClient
from ha_mcp_engineering.clients.single_read import SINGLE_READ
from ha_mcp_engineering.clients.websocket import HomeAssistantWebSocketClient
from ha_mcp_engineering.errors import HomeAssistantApiError, HomeAssistantUnavailableError
from ha_mcp_engineering.integration_inspection import contracts as c
from ha_mcp_engineering.integration_inspection.models import InspectionError
from ha_mcp_engineering.request_context import begin_request, end_request
from tests.test_beta_observability import settings


START = "2026-09-26T12:00:00+00:00"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


async def _start(handle):
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _stop(server):
    server.close()
    await server.wait_closed()


class InstalledVersionTests(unittest.TestCase):
    def test_locked_versions_are_installed(self):
        # Version expectation only; the behavior tests below are the proof.
        self.assertEqual(metadata.version("aiohttp"), "3.14.4")
        self.assertEqual(metadata.version("yarl"), "1.25.1")


class ContentEncodingWhitespaceTests(unittest.IsolatedAsyncioTestCase):
    """3.14.4 strips header OWS in the C parser, matching the Python parser.

    3.14.3's compiled parser kept trailing whitespace, so ``identity `` was
    refused as an encoded body. Now it is identity; every real coding still
    fails closed through the unchanged Engineering predicate.
    """

    async def asyncSetUp(self):
        self.requests = 0
        self.encoding = None

        async def handle(reader, writer):
            try:
                await reader.readuntil(b"\r\n\r\n")
            except (asyncio.IncompleteReadError, ConnectionError):
                writer.close()
                return
            self.requests += 1
            header = b"" if self.encoding is None else b"Content-Encoding: " + self.encoding + b"\r\n"
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                         b"Content-Length: 2\r\nConnection: close\r\n" + header + b"\r\n[]")
            await writer.drain()
            writer.close()

        self.server, port = await _start(handle)
        self.port = port
        self.client = HomeAssistantRestClient(replace(
            settings("/tmp/synthetic-unused-hamcp211-audit"), ha_url=f"http://127.0.0.1:{port}"))

    async def asyncTearDown(self):
        await _stop(self.server)

    async def read(self):
        path = rest_path("/logbook/{}", START, kinds=("timestamp",))
        return await self.client.request("GET", path, raw=True, logbook_read=True)

    async def test_parser_strips_trailing_whitespace_from_header_values(self):
        self.encoding = b"identity \t"
        async with aiohttp.ClientSession(auto_decompress=False) as session:
            async with session.get(f"http://127.0.0.1:{self.port}/api/states") as response:
                self.assertEqual(response.headers["Content-Encoding"], "identity")
                self.assertEqual(await response.read(), b"[]")

    async def test_identity_forms_are_read(self):
        for encoding in (None, b"identity", b"Identity", b"identity \t", b"\tidentity "):
            self.encoding = encoding
            with self.subTest(encoding=encoding):
                self.assertEqual(await self.read(), "[]")

    async def test_every_real_coding_still_fails_closed_once(self):
        for encoding in (b"gzip", b"gzip ", b"GZIP\t", b"br", b"identity, gzip", b"gzip, identity"):
            self.encoding = encoding
            before = self.requests
            with self.subTest(encoding=encoding), self.assertRaises(HomeAssistantApiError) as error:
                await self.read()
            self.assertEqual(error.exception.details["endpoint_category"], "logbook")
            self.assertEqual(self.requests, before + 1)


class SingleAttemptTests(unittest.IsolatedAsyncioTestCase):
    """The 3.14.4 persistent-connection retry rewrite never replays a read."""

    async def asyncSetUp(self):
        self.requests = 0

        async def handle(reader, writer):
            try:
                await reader.readuntil(b"\r\n\r\n")
            except (asyncio.IncompleteReadError, ConnectionError):
                pass
            else:
                self.requests += 1
            # Drop every request without a response.
            writer.close()

        self.server, port = await _start(handle)
        self.client = HomeAssistantRestClient(replace(
            settings("/tmp/synthetic-unused-hamcp211-audit"), ha_url=f"http://127.0.0.1:{port}"))

    async def asyncTearDown(self):
        await _stop(self.server)

    async def test_logbook_read_disconnect_is_one_request(self):
        with self.assertRaises(HomeAssistantUnavailableError):
            await self.client.request(
                "GET", rest_path("/logbook/{}", START, kinds=("timestamp",)),
                raw=True, logbook_read=True)
        self.assertEqual(self.requests, 1)

    async def test_single_read_disconnect_is_one_request(self):
        token = SINGLE_READ.set(True)
        try:
            with self.assertRaises(HomeAssistantUnavailableError):
                await self.client.request("GET", rest_path("/states"))
        finally:
            SINGLE_READ.reset(token)
        self.assertEqual(self.requests, 1)


def _frame(opcode, payload, *, fin=True):
    first = (0x80 if fin else 0) | opcode
    size = len(payload)
    if size < 126:
        header = struct.pack("!BB", first, size)
    elif size < 65536:
        header = struct.pack("!BBH", first, 126, size)
    else:
        header = struct.pack("!BBQ", first, 127, size)
    return header + payload


async def _client_frame(reader):
    first, second = await reader.readexactly(2)
    size = second & 0x7F
    if size == 126:
        (size,) = struct.unpack("!H", await reader.readexactly(2))
    elif size == 127:
        (size,) = struct.unpack("!Q", await reader.readexactly(8))
    mask = await reader.readexactly(4) if second & 0x80 else b"\0\0\0\0"
    payload = await reader.readexactly(size)
    return first & 0x0F, bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


class WebSocketFrameBoundTests(unittest.IsolatedAsyncioTestCase):
    """The reader rewrite keeps the inclusive byte ceiling and fragment rules."""

    LIMIT = 256

    async def asyncSetUp(self):
        self.connections, self.commands = 0, []
        self.result = b""

        async def handle(reader, writer):
            self.connections += 1
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                key = next(line.split(b":", 1)[1].strip() for line in request.split(b"\r\n")
                           if line.lower().startswith(b"sec-websocket-key:"))
                accept = base64.b64encode(hashlib.sha1(key + WS_GUID.encode()).digest())
                writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                             b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")
                writer.write(_frame(1, b'{"type":"auth_required","ha_version":"2026.9.3"}'))
                await _client_frame(reader)
                writer.write(_frame(1, b'{"type":"auth_ok","ha_version":"2026.9.3"}'))
                opcode, command = await _client_frame(reader)
                if opcode == 1:
                    self.commands.append(command)
                writer.write(self.result)
                await writer.drain()
                await reader.read(65536)
            except (asyncio.IncompleteReadError, ConnectionError, StopIteration):
                pass
            finally:
                writer.close()

        self.server, port = await _start(handle)
        self.client = HomeAssistantWebSocketClient(SimpleNamespace(
            websocket_url=f"ws://127.0.0.1:{port}/api/websocket",
            ha_token="SYNTHETIC_HAMCP211_TOKEN"))
        self.telemetry, self.token = begin_request()
        self.telemetry.core_dispatch_authorizer = lambda: True

    async def asyncTearDown(self):
        end_request(self.token)
        await _stop(self.server)

    def body(self, size):
        prefix, suffix = b'{"id":1,"type":"result","success":true,"result":"', b'"}'
        return prefix + b"a" * (size - len(prefix) - len(suffix)) + suffix

    async def read(self):
        # frame_limit = remaining_bytes - 2 * AUTH_BYTES = LIMIT
        return await self.client.read_alarmo_inspection(
            c.ReadKind.MANIFEST, core_version="2026.9.3", remaining_seconds=2,
            remaining_bytes=2 * c.AUTH_BYTES + self.LIMIT)

    async def assert_reason(self, reason):
        with self.assertRaises(InspectionError) as error:
            await self.read()
        self.assertEqual(error.exception.reason, reason)
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.connections, 1)

    async def test_exact_limit_single_and_fragmented_are_read(self):
        body = self.body(self.LIMIT)
        for result in (_frame(1, body), _frame(1, body[:100], fin=False) + _frame(0, body[100:])):
            self.connections, self.commands = 0, []
            self.result = result
            value, consumed = await self.read()
            self.assertEqual(value, "a" * (self.LIMIT - len(self.body(0))))
            self.assertGreater(consumed, self.LIMIT)
            self.assertEqual(len(self.commands), 1)

    async def test_one_byte_over_limit_is_response_bytes(self):
        await self.assert_reason_for(_frame(1, self.body(self.LIMIT + 1)), "response_bytes")

    async def test_fragments_over_limit_are_response_bytes(self):
        body = self.body(self.LIMIT + 1)
        await self.assert_reason_for(
            _frame(1, body[:100], fin=False) + _frame(0, body[100:]), "response_bytes")

    async def test_new_data_frame_between_fragments_is_malformed(self):
        body = self.body(self.LIMIT)
        await self.assert_reason_for(
            _frame(1, body[:100], fin=False) + _frame(1, self.body(80)) + _frame(0, body[100:]),
            "malformed_response")

    async def test_control_frame_between_fragments_is_malformed(self):
        body = self.body(self.LIMIT)
        await self.assert_reason_for(
            _frame(1, body[:100], fin=False) + _frame(9, b"p") + _frame(0, body[100:]),
            "malformed_response")

    async def assert_reason_for(self, result, reason):
        self.result = result
        await self.assert_reason(reason)


class RouteQueryTests(unittest.TestCase):
    """yarl 1.25 drops empty query fields and decodes invalid UTF-8 to U+FFFD.

    Engineering never emits either form, and the encoding guard in
    ``request_url`` runs before the parsed-query comparison, so a route whose
    raw text carries them is refused even though the parsed query matches.
    """

    API = "http://supervisor/core/api"

    def test_yarl_query_parsing_facts_the_guard_order_depends_on(self):
        self.assertEqual(list(URL("http://h/p?&a=1&").query.items()), [("a", "1")])
        self.assertEqual(list(URL("http://h/p?a=%ff").query.items()), [("a", "�")])

    def test_golden_route_urls(self):
        cases = (
            (rest_path("/states/{}", "light.kitchen", kinds=("entity_id",)),
             self.API + "/states/light.kitchen"),
            (rest_path("/logbook/{}", START, kinds=("timestamp",),
                       query=[("end_time", "2026-09-29T12:00:00+00:00"), ("entity", "light.kitchen")]),
             self.API + "/logbook/2026-09-26T12:00:00%2B00:00"
             "?end_time=2026-09-29T12:00:00%2B00:00&entity=light.kitchen"),
            (rest_path("/history/period/{}", START, kinds=("timestamp",),
                       query=[("filter_entity_id", "a&b=c"), ("minimal_response", None), ("end_time", "")]),
             self.API + "/history/period/2026-09-26T12:00:00%2B00:00"
             "?filter_entity_id=a%26b%3Dc&minimal_response&end_time="),
            (rest_path("/config/automation/config/{}", "caf\u00e9 x"),
             self.API + "/config/automation/config/caf%C3%A9%20x"),
        )
        for route, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(str(request_url(self.API, route)), expected)

    def test_raw_empty_fields_and_invalid_utf8_are_refused_by_the_encoding_guard(self):
        forged = (
            ("/states?&a=1&", (("a", "1"),)),
            ("/states?a=1&&", (("a", "1"),)),
            ("/states?a=%ff", (("a", "�"),)),
        )
        for raw, query in forged:
            route = paths._issue(raw, "/states", (), (), query)
            # The parsed query alone would match on yarl 1.25.1.
            self.assertEqual(list(URL(self.API + raw).query.items()), list(query))
            with self.subTest(raw=raw), self.assertRaises(UnsafeRequestPathError) as error:
                request_url(self.API, route)
            self.assertEqual(error.exception.details["reason"], "route_mismatch")

    def test_invalid_utf8_segment_is_refused_before_any_url(self):
        for value in ("%ff", "%c3%28", "%25ff%2F"):
            with self.subTest(value=value), self.assertRaises(UnsafeRequestPathError):
                rest_path("/states/{}", value)


if __name__ == "__main__":
    unittest.main()
