"""HAMCP-188: inbound MCP contract re-verification for the pinned SDK bump.

The gateway attributes only JSON-object bodies, so JSON-RPC batch refusal is
owned by the SDK transport; these tests prove a batch never dispatches in
either transport mode while the equivalent single request still does.

Run unchanged on the previous source and pin (mcp 1.28.1), exactly four tests
in this module fail: the two stateful session-retention checks (a batch opening
leaves an orphan session; refused openings consume capacity), the explicit
version-pin assertion in the bounds test, and the cross-origin redirect
negative control. Only the first two are behavioral proof of the CVE fix; the
pin assertion is an expectation, and the redirect check pins a separate 1.30.0
narrowing.
"""

import asyncio
from datetime import timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest

import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.clients.mcp import (  # noqa: E402
    _classified_transport_error,
    _iter_exceptions,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.mcp_sdk_compatibility import (  # noqa: E402
    MAX_STATEFUL_MCP_SESSIONS,
    PINNED_MCP_SDK_VERSION,
    REVIEWED_UPSTREAM_PROTOCOL_VERSION,
    install_catalog_generation_gate,
)
from ha_mcp_engineering.mcp_server import (  # noqa: E402
    READMISSION_SESSION_IDLE_TIMEOUT_SECONDS,
    create_mcp_server,
)
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402
from tests.same_thread_asgi_client import SameThreadAsgiTestClient  # noqa: E402

SECRET = "synthetic-hamcp188-secret"
HEADERS = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
}
GATEWAY_POST_LIMIT_BYTES = 2_000_000


def _initialize(request_id, version="2025-03-26"):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": version,
            "capabilities": {},
            "clientInfo": {"name": "synthetic-hamcp188", "version": "1.0.0"},
        },
    }


def _call(request_id, name):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": {}},
    }


def _data(response):
    for line in response.text.replace("\r", "").splitlines():
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))
    try:
        return response.json()
    except ValueError:
        return None


class _Harness:
    def __init__(self, directory, *, stateful):
        self.dispatched = {"static_read": 0, "dynamic_read": 0}
        settings = Settings(
            ha_url="http://synthetic-ha.invalid",
            ha_token="synthetic-token",
            access_secret=SECRET,
            port=8100,
            audit_path=str(Path(directory) / "audit.jsonl"),
            rate_limit_per_minute=10_000,
            rate_limit_burst=1_000,
            destructive_services=frozenset(),
            ha_mcp_release_registry_enabled=stateful,
            ha_mcp_release_registry_public_key=("A" * 44) if stateful else None,
        )
        self.server = create_mcp_server(settings)

        @self.server.tool(name="static_read")
        async def static_read() -> str:
            self.dispatched["static_read"] += 1
            return "ok"

        @self.server.tool(name="dynamic_read")
        async def dynamic_read() -> str:
            self.dispatched["dynamic_read"] += 1
            return "ok"

        install_catalog_generation_gate(
            self.server, lambda: (1, ("dynamic_read",))
        )
        self.gateway = AuthenticatedMcpGateway(
            self.server.streamable_http_app(),
            settings,
            AuditLogger(settings.audit_path, SECRET),
        )

    def client(self):
        return SameThreadAsgiTestClient(
            self.gateway,
            lifespan_app=self.gateway.app,
            base_url="http://127.0.0.1:8100",
        )

    def active_sessions(self):
        return self.server.session_manager.engineering_session_capacity_snapshot()[
            "active_session_count"
        ]


def _post(client, body, *, session_id=None, path=f"/{SECRET}/mcp"):
    headers = dict(HEADERS)
    if session_id is not None:
        headers["mcp-session-id"] = session_id
    return client.post(path, content=json.dumps(body), headers=headers)


def _delete(client, session_id):
    return client.request(
        "DELETE",
        f"/{SECRET}/mcp",
        headers={"accept": HEADERS["accept"], "mcp-session-id": session_id},
    )


class JsonRpcBatchRefusalTests(unittest.TestCase):
    def assert_batch_refused(self, response):
        self.assertEqual(response.status_code, 400)
        payload = _data(response)
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["error"]["code"], types.INVALID_PARAMS)

    def test_stateless_batch_never_dispatches_while_single_call_does(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = _Harness(directory, stateful=False)
            self.assertTrue(harness.server.settings.stateless_http)
            with harness.client() as client:
                single = _post(client, _call(1, "static_read"))
                self.assertEqual(single.status_code, 200)
                self.assertFalse(_data(single)["result"]["isError"])
                self.assertEqual(harness.dispatched["static_read"], 1)

                for batch in (
                    [_call(2, "static_read")],
                    [_call(3, "static_read"), _call(4, "dynamic_read")],
                    [_initialize(5), _call(6, "static_read")],
                ):
                    with self.subTest(batch=[item["method"] for item in batch]):
                        self.assert_batch_refused(_post(client, batch))
                self.assertEqual(
                    harness.dispatched, {"static_read": 1, "dynamic_read": 0}
                )

    def test_stateful_batch_never_dispatches_while_single_call_does(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = _Harness(directory, stateful=True)
            self.assertFalse(harness.server.settings.stateless_http)
            with harness.client() as client:
                opened = _post(client, _initialize(1))
                self.assertEqual(opened.status_code, 200)
                session_id = opened.headers["mcp-session-id"]
                _post(
                    client,
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    session_id=session_id,
                )
                _post(
                    client,
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    session_id=session_id,
                )
                single = _post(client, _call(3, "dynamic_read"), session_id=session_id)
                self.assertFalse(_data(single)["result"]["isError"])
                self.assertEqual(harness.dispatched["dynamic_read"], 1)

                for batch in (
                    [_call(4, "dynamic_read")],
                    [_call(5, "static_read"), _call(6, "dynamic_read")],
                ):
                    with self.subTest(batch=len(batch)):
                        self.assert_batch_refused(
                            _post(client, batch, session_id=session_id)
                        )
                self.assertEqual(
                    harness.dispatched, {"static_read": 0, "dynamic_read": 1}
                )

                # A batch opening request is refused and no longer leaves a
                # registered session behind (the CVE-2026-59951 correction).
                self.assertEqual(harness.active_sessions(), 1)
                self.assert_batch_refused(
                    _post(client, [_initialize(7), _call(8, "static_read")])
                )
                self.assertEqual(harness.active_sessions(), 1)
                self.assertEqual(harness.dispatched["static_read"], 0)

                wrong = _post(
                    client,
                    [_call(9, "dynamic_read")],
                    session_id=session_id,
                    path="/synthetic-wrong-credential/mcp",
                )
                self.assertEqual(wrong.status_code, 404)
                self.assertEqual(harness.dispatched["dynamic_read"], 1)
                self.assertEqual(_delete(client, session_id).status_code, 200)


class ProtocolNegotiationTests(unittest.TestCase):
    def test_sdk_protocol_constants_are_unchanged(self):
        self.assertEqual(REVIEWED_UPSTREAM_PROTOCOL_VERSION, "2025-03-26")
        self.assertEqual(
            list(SUPPORTED_PROTOCOL_VERSIONS),
            ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"],
        )
        self.assertEqual(types.LATEST_PROTOCOL_VERSION, "2025-11-25")
        self.assertEqual(types.DEFAULT_NEGOTIATED_VERSION, "2025-03-26")

    def test_inbound_negotiation_is_unchanged_in_both_modes(self):
        for stateful in (False, True):
            with self.subTest(stateful=stateful), tempfile.TemporaryDirectory() as directory:
                harness = _Harness(directory, stateful=stateful)
                with harness.client() as client:
                    for requested, negotiated in (
                        ("2025-03-26", "2025-03-26"),
                        ("2024-11-05", "2024-11-05"),
                        ("1999-01-01", types.LATEST_PROTOCOL_VERSION),
                    ):
                        response = _post(client, _initialize(1, requested))
                        self.assertEqual(response.status_code, 200)
                        result = _data(response)["result"]
                        self.assertEqual(result["protocolVersion"], negotiated)
                        self.assertFalse(
                            result["capabilities"]
                            .get("tools", {})
                            .get("listChanged", False)
                        )
                        if stateful:
                            self.assertEqual(
                                _delete(
                                    client, response.headers["mcp-session-id"]
                                ).status_code,
                                200,
                            )
                        else:
                            self.assertNotIn("mcp-session-id", response.headers)


class StatefulSessionReclamationTests(unittest.TestCase):
    def test_engineering_bounds_remain_tighter_than_sdk_defaults(self):
        self.assertEqual(PINNED_MCP_SDK_VERSION, "1.30.0")
        with tempfile.TemporaryDirectory() as directory:
            harness = _Harness(directory, stateful=True)
            manager = harness.server.session_manager
            self.assertEqual(
                manager.session_idle_timeout, READMISSION_SESSION_IDLE_TIMEOUT_SECONDS
            )
            self.assertEqual(READMISSION_SESSION_IDLE_TIMEOUT_SECONDS, 1_800.0)
            self.assertEqual(MAX_STATEFUL_MCP_SESSIONS, 1_024)
            self.assertLess(MAX_STATEFUL_MCP_SESSIONS, manager.max_sessions)
            self.assertLess(GATEWAY_POST_LIMIT_BYTES, manager.max_request_body_size)

    def test_refused_opening_requests_do_not_consume_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = _Harness(directory, stateful=True)
            with harness.client() as client:
                for body in (
                    _call(1, "static_read"),
                    {"jsonrpc": "2.0", "id": 2},
                    [_initialize(3)],
                ):
                    response = _post(client, body)
                    self.assertGreaterEqual(response.status_code, 400)
                    self.assertEqual(harness.active_sessions(), 0)
                malformed = client.post(
                    f"/{SECRET}/mcp", content=b"{not json", headers=HEADERS
                )
                self.assertEqual(malformed.status_code, 400)
                self.assertEqual(harness.active_sessions(), 0)
                self.assertEqual(harness.dispatched["static_read"], 0)

                opened = _post(client, _initialize(4))
                self.assertEqual(opened.status_code, 200)
                self.assertEqual(harness.active_sessions(), 1)
                session_id = opened.headers["mcp-session-id"]
                self.assertEqual(_delete(client, session_id).status_code, 200)
                self.assertEqual(harness.active_sessions(), 0)
                after_delete = _post(
                    client, _call(5, "static_read"), session_id=session_id
                )
                self.assertEqual(after_delete.status_code, 404)
                self.assertEqual(harness.dispatched["static_read"], 0)

    def test_idle_session_is_reclaimed_and_its_id_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = _Harness(directory, stateful=True)
            harness.server.session_manager.session_idle_timeout = 0.2
            with harness.client() as client:
                opened = _post(client, _initialize(1))
                session_id = opened.headers["mcp-session-id"]
                self.assertEqual(harness.active_sessions(), 1)
                client.loop.run_until_complete(asyncio.sleep(0.8))
                self.assertEqual(harness.active_sessions(), 0)
                expired = _post(client, _call(2, "static_read"), session_id=session_id)
                self.assertEqual(expired.status_code, 404)
                self.assertEqual(harness.dispatched["static_read"], 0)

    def test_oversized_post_is_refused_by_gateway_before_sdk(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = _Harness(directory, stateful=True)
            with harness.client() as client:
                body = _call(1, "static_read")
                body["params"]["arguments"] = {"pad": "x" * GATEWAY_POST_LIMIT_BYTES}
                response = _post(client, body)
                self.assertEqual(response.status_code, 413)
                self.assertEqual(response.json()["error"], "RequestTooLarge")
                self.assertEqual(harness.active_sessions(), 0)
                self.assertEqual(harness.dispatched["static_read"], 0)


class _RedirectingUpstream(httpx.AsyncBaseTransport):
    """Synthetic upstream whose endpoint redirects; records every hit."""

    def __init__(self, location):
        self.location = location
        self.hits = []

    async def handle_async_request(self, request):
        await request.aread()
        self.hits.append((request.method, str(request.url)))
        if str(request.url) == "http://upstream.invalid/mcp":
            return httpx.Response(307, headers={"location": self.location})
        return httpx.Response(500)


class UpstreamClientRedirectTests(unittest.IsolatedAsyncioTestCase):
    async def initialize_through(self, upstream):
        def factory(headers=None, timeout=None, auth=None):
            # 1.28.1's default client factory followed every redirect.
            return httpx.AsyncClient(
                transport=upstream,
                headers=headers,
                timeout=timeout,
                follow_redirects=True,
            )

        with self.assertRaises(BaseException) as raised:
            async with streamablehttp_client(
                "http://upstream.invalid/mcp",
                timeout=2,
                sse_read_timeout=2,
                terminate_on_close=True,
                httpx_client_factory=factory,
            ) as (read_stream, write_stream, _session_id):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=2),
                ) as session:
                    await session.initialize()
        return raised.exception

    async def test_cross_origin_redirect_is_not_followed(self):
        upstream = _RedirectingUpstream("http://other.invalid/mcp")
        exc = await self.initialize_through(upstream)
        self.assertEqual(upstream.hits, [("POST", "http://upstream.invalid/mcp")])
        leaves = list(_iter_exceptions(exc))
        self.assertTrue(
            any(
                isinstance(leaf, httpx.HTTPStatusError)
                and leaf.response.status_code == 307
                for leaf in leaves
            )
        )
        self.assertEqual(_classified_transport_error(exc).category, "upstream_error")

    async def test_same_origin_method_preserving_redirect_is_followed(self):
        upstream = _RedirectingUpstream("http://upstream.invalid/mcp/")
        exc = await self.initialize_through(upstream)
        self.assertEqual(
            upstream.hits,
            [
                ("POST", "http://upstream.invalid/mcp"),
                ("POST", "http://upstream.invalid/mcp/"),
            ],
        )
        self.assertEqual(_classified_transport_error(exc).category, "upstream_error")


if __name__ == "__main__":
    unittest.main()
