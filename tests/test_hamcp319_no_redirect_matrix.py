"""HAMCP-319 design section 8.1 item 1, part 2: the SDK-driven matrix rows.

Part 1 (``test_hamcp319_no_redirect``) runs every request row Engineering
emits on its own. This module adds the rows the pinned SDK (mcp 1.30.0) only
emits when a session asks for them, and the one-by-one guard, budget,
structure and latch tests:

- ``notifications/cancelled`` and GET resumption (``Last-Event-ID`` of a
  resumption token) on every read and dashboard client. The real site, the
  real no-redirect factory and the real SDK transport run; only the SDK
  session class at each site is a subclass that, before its first
  ``tools/list`` page, sends the SDK's own ``CancelledNotification`` or a
  resumption request (``ClientMessageMetadata(resumption_token=...)``).
- The analysis session (S-d) rows that part 1 leaves out: ``cancelled`` (the
  fence refuses it before send), the GET stream, GET resumption and SSE
  reconnection (the fence answers a local 405 or bounds the POST stream, so
  the origin receives zero GETs and no latch is set).
- The section 8.1 guard, budget, structure, latch and analysis fence latch
  tests, the operator message test and the 8.5 ``legacy`` behavior change.

Peers are loopback servers with separate origin and destination counters; no
network, Docker or Home Assistant. Secrets and canaries are synthetic.
"""

from __future__ import annotations

import asyncio
import json
import contextvars
from contextlib import ExitStack, contextmanager
from dataclasses import replace
import sys
import time
import traceback
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from mcp import types  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.shared._httpx_utils import (  # noqa: E402
    create_mcp_http_client,
    stream_within_origin,
)
from mcp.shared.message import ClientMessageMetadata  # noqa: E402

import test_hamcp319_no_redirect as part1  # noqa: E402
from ha_mcp_engineering import mcp_sdk_compatibility  # noqa: E402
from ha_mcp_engineering.clients import dashboard_analysis as analysis_module  # noqa: E402
from ha_mcp_engineering.clients import ha_mcp_endpoint as endpoint_module  # noqa: E402
from ha_mcp_engineering.clients import mcp as mcp_module  # noqa: E402
from ha_mcp_engineering.clients import upstream_read as upstream_read_module  # noqa: E402
from ha_mcp_engineering.clients.dashboard_analysis import CollectionBudget  # noqa: E402
from ha_mcp_engineering.clients.ha_mcp_endpoint import (  # noqa: E402
    OPERATOR_MESSAGES,
    UPSTREAM_ENDPOINT_REJECTED,
    UPSTREAM_REDIRECT_OPERATOR_MESSAGE,
    UPSTREAM_REDIRECT_REFUSED,
    NoRedirectBinding,
    UpstreamEndpointRefused,
    endpoint_snapshot,
    ha_mcp_no_redirect_client_factory,
)
from ha_mcp_engineering.clients.mcp import DashboardTransportError  # noqa: E402
from ha_mcp_engineering.clients.upstream_read import McpReadGatewayTransport  # noqa: E402
from ha_mcp_engineering.errors import DashboardProviderError  # noqa: E402
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    UpstreamDashboardProvider,
)
from ha_mcp_engineering.providers import upstream_read_gateway as gateway_module  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from support.ha_mcp_redirect_peer import (  # noqa: E402
    ALL_CANARIES,
    GET_POST_STATUSES,
    LOCATION_CANARY,
    LOCATION_KINDS,
    Injection,
    RedirectPeer,
    peer_configurations,
)

CLIENTS = part1.CLIENTS
DEADLINE_SECONDS = part1.DEADLINE_SECONDS
DASHBOARD_TOOLS = part1.DASHBOARD_TOOLS
READ_TOOLS = part1.READ_TOOLS
PEER_SLUGS = part1.PEER_SLUGS
REQUEST_SLUGS = part1.REQUEST_SLUGS
SAME_ORIGIN_KINDS = tuple(kind for kind in LOCATION_KINDS if kind != "cross_origin")
GET_TYPES = ("get_stream", "get_resumption", "get_reconnect")
# Request rows the SDK emits only when the session asks (section 8.1 item 1).
SDK_DRIVEN = ("notifications/cancelled", "get_resumption")
# Analysis (S-d) rows part 1 does not cover.
ANALYSIS_EXTRA = ("notifications/cancelled",) + GET_TYPES
RESUMPTION_TOKEN = "resume-token-1"
# The exact fixed operator text (section 3.3); pinned here so a change to the
# constant, or any interpolation into it, fails a test.
EXPECTED_OPERATOR_MESSAGE = (
    "The ha-mcp endpoint answered with an HTTP redirect. Engineering does not "
    "follow redirects on ha-mcp connections. Set the upstream_dashboard_mcp_url "
    "option to the exact ha-mcp endpoint URL that answers without a redirect, "
    "then restart Engineering."
)

# -- recording binding ------------------------------------------------------

_CELL_BINDINGS: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "hamcp319_cell_bindings", default=None
)


class RecordingBinding(NoRedirectBinding):
    """Behavior-identical binding that records who set the latch, and how.

    ``refuse_callers`` names the code object of every ``refuse_redirect``
    caller; ``hook_redirects`` counts response-hook invocations on a 3xx.
    """

    def __init__(self, snapshot):
        super().__init__(snapshot)
        self.refuse_callers: list[tuple[str, str]] = []
        self.latch_writes = 0
        self.hook_calls = 0
        self.hook_redirects = 0
        self.request_hook_calls = 0
        cell = _CELL_BINDINGS.get()
        if cell is not None:
            cell.append(self)

    def refuse_redirect(self, phase: str = "operation"):
        caller = sys._getframe(1).f_code.co_qualname
        self.refuse_callers.append((caller, phase))
        before = self.latch_code
        error = super().refuse_redirect(phase)
        if before is None and self.latch_code is not None:
            self.latch_writes += 1
        return error

    async def request_hook(self, request):
        self.request_hook_calls += 1
        return await super().request_hook(request)

    async def response_hook(self, response):
        self.hook_calls += 1
        if 300 <= response.status_code < 400:
            self.hook_redirects += 1
        return await super().response_hook(response)


@contextmanager
def recording_bindings():
    with ExitStack() as stack:
        # S-c and S-d import the binding from ha_mcp_endpoint at call time;
        # S-a and S-b hold the module-level name in upstream_read.
        stack.enter_context(patch.object(endpoint_module, "NoRedirectBinding", RecordingBinding))
        stack.enter_context(
            patch.object(upstream_read_module, "NoRedirectBinding", RecordingBinding)
        )
        yield


# -- SDK-driven request injection --------------------------------------------


async def _send_sdk_request(session, request_type: str) -> None:
    if request_type == "notifications/cancelled":
        await session.send_notification(
            types.ClientNotification(
                types.CancelledNotification(
                    params=types.CancelledNotificationParams(
                        requestId=0, reason="synthetic"
                    )
                )
            )
        )
        return
    if request_type == "get_resumption":
        await session.send_request(
            types.ClientRequest(types.ListToolsRequest()),
            types.ListToolsResult,
            metadata=ClientMessageMetadata(resumption_token=RESUMPTION_TOKEN),
        )
        return
    raise ValueError(request_type)


def _injecting_session(base, request_type: str):
    class InjectingSession(base):
        """The site's session class; before its first list page it asks the
        SDK for ``request_type`` through the SDK's own public API."""

        async def list_tools(self, cursor=None, **kwargs):
            if not getattr(self, "_hamcp319_sent", False):
                self._hamcp319_sent = True
                await _send_sdk_request(self, request_type)
            return await super().list_tools(cursor, **kwargs)

    InjectingSession.__name__ = f"Injecting{base.__name__}"
    return InjectingSession


@contextmanager
def sdk_request_at_sites(request_type: str):
    if request_type not in SDK_DRIVEN:
        yield
        return
    with ExitStack() as stack:
        for module, name in (
            (upstream_read_module, "ClientSession"),  # S-a, S-b
            (mcp_module, "ClientSession"),  # S-c
            (mcp_sdk_compatibility, "ReviewedProtocolClientSession"),  # S-d
        ):
            stack.enter_context(
                patch.object(
                    module, name, _injecting_session(getattr(module, name), request_type)
                )
            )
        yield


# -- cells ---------------------------------------------------------------------


async def run_sdk_cell(client, request_type, status, location_kind, version, form):
    builder, kind, _requests, _full = CLIENTS[client]
    bindings: list[RecordingBinding] = []
    _CELL_BINDINGS.set(bindings)
    peer = RedirectPeer(
        version=version,
        form=form,
        inject=Injection(request_type, status, location_kind),
        sse_call=request_type == "get_reconnect",
        hold_list_until_get=False,
        tool_names=DASHBOARD_TOOLS if kind != "read" else READ_TOOLS,
    )
    drive_type = "get_reconnect" if request_type == "get_reconnect" else "discover"
    with peer.serve():
        location = peer.location_for(location_kind)
        transport = builder(peer.url)
        result = error = None
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            result = await asyncio.wait_for(
                part1._drive(client, transport, kind, drive_type),
                DEADLINE_SECONDS + 5,
            )
        except Exception as exc:  # noqa: BLE001 - the cell records the outcome
            error = exc
        finally:
            closer = getattr(transport, "aclose", None)
            if callable(closer):
                try:
                    await closer()
                except Exception:  # noqa: BLE001
                    pass
        elapsed = loop.time() - started
    return SimpleNamespace(
        client=client,
        kind=kind,
        request_type=request_type,
        injected_type=request_type,
        status=status,
        location_kind=location_kind,
        location=location,
        version=version,
        form=form,
        url=peer.url,
        result=result,
        error=error,
        elapsed=elapsed,
        counters=peer.counters,
        injected=peer.counters.injected,
        bindings=bindings,
    )


def _displayed_sinks(error, result) -> list[str]:
    """What any formatter or logger renders: str, repr, args and the full
    traceback text, which honors ``raise ... from None``.

    The analysis read raises its fixed category ``from None``; the suppressed
    ``__context__`` (an SDK ``HTTPStatusError`` for a resumption GET that the
    fence answered with its local 405) is never rendered and is not a sink.
    """

    values = [repr(result), str(result)]
    if error is not None:
        values.extend((repr(error), str(error), repr(getattr(error, "args", ()))))
        values.append("".join(traceback.format_exception(error)))
        cause = error.__cause__
        while cause is not None:
            values.extend((repr(cause), str(cause)))
            cause = cause.__cause__
    return values


def _list_pages(counters) -> int:
    return counters.origin["tools/list:first"] + counters.origin["tools/list:cursor"]


def _origin_gets(counters) -> int:
    return sum(counters.origin[name] for name in GET_TYPES)


class _MatrixAssertions(part1._NoRedirectAssertions):
    def assert_sdk_cell(self, cell, logs):
        """Read and dashboard clients: the SDK-driven request is refused."""

        self.assert_cell(cell, logs)
        label = f"{cell.client}/{cell.request_type}/{cell.status}/{cell.location_kind}"
        with self.subTest(cell=label, check="sdk-driven"):
            # Both requests are sent after initialize and before any list page.
            self.assertEqual(cell.counters.origin["initialize"], 1)
            self.assertEqual(_list_pages(cell.counters), 0)
            self.assertEqual(cell.counters.origin["tools/call"], 0)
            latched = [b for b in cell.bindings if b.latched]
            self.assertEqual(len(latched), 1)
            self.assertEqual(latched[0].latch_code, UPSTREAM_REDIRECT_REFUSED)
            self.assertEqual(latched[0].latch_phase, "operation")
            self.assertEqual(latched[0].latch_writes, 1)

    def assert_analysis_extra_cell(self, cell, logs):
        """S-d: cancelled is refused before send; no GET ever reaches the origin."""

        counters = cell.counters
        label = f"analysis/{cell.request_type}/{cell.status}/{cell.location_kind}"
        with self.subTest(cell=label):
            self.assertEqual(cell.injected, 0, "a request reached the redirecting row")
            self.assertEqual(counters.destination_total(), 0, dict(counters.destination))
            self.assertEqual(_origin_gets(counters), 0)
            self.assertEqual(counters.origin["notifications/cancelled"], 0)
            self.assertEqual(len(cell.bindings), 1)
            binding = cell.bindings[0]
            self.assertIsNone(binding.latch_code)
            self.assertEqual(binding.refuse_callers, [])
            self.assertEqual(binding.hook_redirects, 0)
            self.assertLessEqual(counters.origin["delete"], 1)
            if cell.request_type == "get_stream":
                # The SDK's background GET is answered by the local 405; the
                # read itself is useful.
                self.assertIsNone(cell.error, part1._category(cell.error))
                self.assertIsNotNone(cell.result)
                self.assertEqual(counters.executions, 1)
                self.assertEqual(counters.origin["tools/call"], 1)
            else:
                self.assertIsInstance(cell.error, DashboardTransportError)
                self.assertNotEqual(cell.error.category, UPSTREAM_REDIRECT_REFUSED)
                self.assertFalse(cell.error.retryable, cell.error.category)
                self.assertLess(cell.elapsed, DEADLINE_SECONDS)
                self.assertEqual(counters.executions, 0)
                if cell.request_type == "get_reconnect":
                    # The bounded POST stream ends without a response: the
                    # SDK never reaches its reconnection GET.
                    self.assertEqual(cell.error.category, "protocol_error")
                    self.assertEqual(counters.origin["tools/call"], 1)
                else:
                    self.assertEqual(_list_pages(counters), 0)
                    self.assertEqual(counters.origin["tools/call"], 0)
            if cell.error is not None and not cell.error.__suppress_context__:
                self.assertIsNone(cell.error.__context__)
            self.assert_no_canary(
                _displayed_sinks(cell.error, cell.result) + logs,
                url=cell.url,
                location=cell.location,
            )


def _kinds_for(client, status):
    if CLIENTS[client][3]:
        return LOCATION_KINDS
    return (LOCATION_KINDS[GET_POST_STATUSES.index(status) % len(LOCATION_KINDS)],)


def _sdk_matrix_test(client, request_type, status, version, form):
    kinds = _kinds_for(client, status)
    analysis = CLIENTS[client][1] == "analysis"

    def test(self):
        async def scenario():
            if client == "gateway_retained":
                return [
                    await run_sdk_cell(client, request_type, status, kind, version, form)
                    for kind in kinds
                ]
            return await asyncio.gather(
                *(
                    run_sdk_cell(client, request_type, status, kind, version, form)
                    for kind in kinds
                )
            )

        with part1.capture_all_logs() as capture, recording_bindings(), sdk_request_at_sites(
            request_type
        ):
            cells = asyncio.run(scenario())
        for cell in cells:
            if analysis:
                self.assert_analysis_extra_cell(cell, capture.lines)
            else:
                self.assert_sdk_cell(cell, capture.lines)

    test.__doc__ = f"{client}: a {status} on {request_type} ({version} {form})."
    return test


class NoRedirectSdkDrivenMatrix(_MatrixAssertions, unittest.TestCase):
    """Section 8.1 item 1: cancelled, GET resumption and analysis GET rows."""


for _client, (_builder, _kind, _requests, _full) in CLIENTS.items():
    _rows = ANALYSIS_EXTRA if _kind == "analysis" else SDK_DRIVEN
    for _request_type in _rows:
        for _status in GET_POST_STATUSES:
            for _version, _form in peer_configurations():
                setattr(
                    NoRedirectSdkDrivenMatrix,
                    (
                        f"test_no_redirect_{_client}_{REQUEST_SLUGS[_request_type]}_"
                        f"{_status}_{PEER_SLUGS[(_version, _form)]}"
                    ),
                    _sdk_matrix_test(_client, _request_type, _status, _version, _form),
                )


# -- guard, budget, structure, latch ------------------------------------------


def _initialize_body() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "synthetic", "version": "0"},
        },
    }


def _headers() -> dict:
    return {"Accept": "application/json, text/event-stream"}


class NoRedirectGuardBudgetStructure(part1._NoRedirectAssertions, unittest.TestCase):
    """Section 8.1 item 1: guard, budget and structure, each on its own."""

    def test_no_redirect_guard_refuses_with_default_budget(self):
        """The response hook alone refuses a same-origin 3xx at max_redirects 20."""

        async def unit(kind, status):
            peer = RedirectPeer(
                version="8.5.0",
                form="legacy",
                inject=Injection("initialize", status, kind),
            )
            with peer.serve():
                binding = NoRedirectBinding(endpoint_snapshot(peer.url))
                client = ha_mcp_no_redirect_client_factory(binding)(headers=_headers())
                client.max_redirects = 20  # the httpx default budget
                error = None
                try:
                    async with client:
                        async with stream_within_origin(
                            client, "POST", peer.url, json=_initialize_body()
                        ):
                            pass
                except Exception as exc:  # noqa: BLE001
                    error = exc
            return peer, binding, error

        async def site(kind, status):
            real = upstream_read_module.ha_mcp_no_redirect_client_factory

            def budget_20(binding, **kwargs):
                factory = real(binding, **kwargs)

                def build(*args, **inner):
                    client = factory(*args, **inner)
                    client.max_redirects = 20
                    return client

                return build

            peer = RedirectPeer(
                version="9.1.0",
                form="strict_full",
                inject=Injection("initialize", status, kind),
                tool_names=READ_TOOLS,
            )
            with peer.serve(), patch.object(
                upstream_read_module, "ha_mcp_no_redirect_client_factory", budget_20
            ):
                transport = part1._gateway_transport(peer.url, retained=False)
                error = None
                try:
                    await transport.discover()
                except Exception as exc:  # noqa: BLE001
                    error = exc
            return peer, error

        for kind in SAME_ORIGIN_KINDS:
            for status in (307, 308):
                with self.subTest(path="unit", kind=kind, status=status):
                    peer, binding, error = asyncio.run(unit(kind, status))
                    self.assertEqual(peer.counters.injected, 1)
                    self.assertIsInstance(error, UpstreamEndpointRefused)
                    self.assertEqual(error.category, UPSTREAM_REDIRECT_REFUSED)
                    self.assertEqual(binding.latch_code, UPSTREAM_REDIRECT_REFUSED)
                    self.assertEqual(peer.counters.origin["initialize"], 1)
                    self.assertEqual(peer.counters.destination_total(), 0)
                with self.subTest(path="S-a", kind=kind, status=status):
                    peer, error = asyncio.run(site(kind, status))
                    self.assertEqual(peer.counters.injected, 1)
                    self.assertIsInstance(error, DashboardTransportError)
                    self.assertEqual(error.category, UPSTREAM_REDIRECT_REFUSED)
                    self.assertEqual(peer.counters.origin["initialize"], 1)
                    self.assertEqual(peer.counters.destination_total(), 0)
                    self.assertEqual(_list_pages(peer.counters), 0)

    def test_no_redirect_budget_zero_yields_unfollowed(self):
        """Hooks bypassed: max_redirects 0 alone makes the SDK yield the 3xx."""

        async def scenario(kind, status, method):
            request_type = "initialize" if method == "POST" else "get_stream"
            peer = RedirectPeer(
                version="9.1.0",
                form="strict_full",
                inject=Injection(request_type, status, kind),
            )
            with peer.serve():
                binding = NoRedirectBinding(endpoint_snapshot(peer.url))
                client = ha_mcp_no_redirect_client_factory(binding)(headers=_headers())
                client.event_hooks = {"request": [], "response": []}
                kwargs = {"json": _initialize_body()} if method == "POST" else {}
                async with client:
                    async with stream_within_origin(
                        client, method, peer.url, **kwargs
                    ) as response:
                        yielded = response.status_code
                        location = response.headers.get("location", "")
            return peer, binding, yielded, location

        for kind in SAME_ORIGIN_KINDS:
            for status in (307, 308):
                for method in ("POST", "GET"):
                    with self.subTest(kind=kind, status=status, method=method):
                        peer, binding, yielded, location = asyncio.run(
                            scenario(kind, status, method)
                        )
                        self.assertEqual(yielded, status)
                        self.assertIn(LOCATION_CANARY, location)
                        self.assertEqual(peer.counters.injected, 1)
                        self.assertEqual(peer.counters.origin_total(), 1)
                        self.assertEqual(
                            peer.counters.destination_total(),
                            0,
                            dict(peer.counters.destination),
                        )
                        self.assertIsNone(binding.latch_code)

    def test_no_redirect_factory_settings(self):
        """Every consumer's client: no redirects, both hooks, settings unchanged."""

        records = []
        real = endpoint_module.ha_mcp_no_redirect_client_factory

        def recording(binding, **kwargs):
            factory = real(binding, **kwargs)

            def build(headers=None, timeout=None, auth=None):
                client = factory(headers=headers, timeout=timeout, auth=auth)
                records.append(
                    SimpleNamespace(
                        binding=binding,
                        kwargs=kwargs,
                        client=client,
                        sdk_default=create_mcp_http_client(
                            headers=headers, timeout=timeout, auth=auth
                        ),
                    )
                )
                return client

            build.ha_mcp_no_redirect_binding = binding
            return build

        async def consumer(client_name, version, form):
            builder, kind, _r, _f = CLIENTS[client_name]
            peer = RedirectPeer(
                version=version,
                form=form,
                tool_names=DASHBOARD_TOOLS if kind != "read" else READ_TOOLS,
            )
            with peer.serve():
                transport = builder(peer.url)
                try:
                    await part1._drive(client_name, transport, kind, "tools/call")
                finally:
                    closer = getattr(transport, "aclose", None)
                    if callable(closer):
                        await closer()
            return peer

        for client_name, (_b, kind, _r, _f) in CLIENTS.items():
            for version, form in peer_configurations():
                records.clear()
                with self.subTest(client=client_name, version=version, form=form):
                    with patch.object(
                        endpoint_module, "ha_mcp_no_redirect_client_factory", recording
                    ), patch.object(
                        upstream_read_module,
                        "ha_mcp_no_redirect_client_factory",
                        recording,
                    ):
                        peer = asyncio.run(consumer(client_name, version, form))
                    self.assertEqual(peer.counters.destination_total(), 0)
                    self.assertGreaterEqual(len(records), 1)
                    for record in records:
                        client, default = record.client, record.sdk_default
                        self.assertEqual(client.max_redirects, 0)
                        self.assertIs(client.follow_redirects, False)
                        self.assertEqual(
                            client.event_hooks["request"], [record.binding.request_hook]
                        )
                        self.assertEqual(
                            client.event_hooks["response"], [record.binding.response_hook]
                        )
                        self.assertTrue(record.binding.factory_installed)
                        self.assertTrue(record.binding.client_created)
                        self.assertEqual(client.timeout, default.timeout)
                        for key, value in default.headers.items():
                            if key.lower() in {"user-agent"}:
                                continue
                            self.assertEqual(client.headers.get(key), value)
                        if kind == "analysis":
                            self.assertIs(client.trust_env, False)
                            self.assertIsInstance(
                                client._transport, analysis_module._AnalysisMcpHttp
                            )
                            self.assertIs(client._transport.binding, record.binding)
                            self.assertEqual(
                                record.kwargs,
                                {
                                    "transport": client._transport,
                                    "trust_env": False,
                                    "refuse_auth": True,
                                },
                            )
                        else:
                            # The SDK defaults: environment trust and the
                            # default pooled transport, no extra arguments.
                            self.assertEqual(record.kwargs, {})
                            self.assertIs(client.trust_env, default.trust_env)
                            self.assertIs(client.trust_env, True)
                            self.assertIs(type(client._transport), type(default._transport))
                    asyncio.run(_aclose_all(records))


async def _aclose_all(records):
    for record in records:
        await record.client.aclose()
        await record.sdk_default.aclose()


class NoRedirectLatch(part1._NoRedirectAssertions, unittest.TestCase):
    """Section 8.1 item 1: the latch classifies promptly."""

    SITE_DEADLINE = 20.0

    def test_no_redirect_latch_classifies_promptly(self):
        async def site_cell(client, status, version, form):
            builder, kind, _r, _f = CLIENTS[client]
            bindings: list[RecordingBinding] = []
            _CELL_BINDINGS.set(bindings)
            peer = RedirectPeer(
                version=version,
                form=form,
                inject=Injection("notifications/initialized", status, "same_path_slash"),
                tool_names=DASHBOARD_TOOLS if kind != "read" else READ_TOOLS,
            )
            with peer.serve():
                transport = builder(peer.url)
                started = time.monotonic()
                error = None
                try:
                    await asyncio.wait_for(
                        part1._drive(client, transport, kind, "discover"),
                        self.SITE_DEADLINE * 2,
                    )
                except Exception as exc:  # noqa: BLE001
                    error = exc
                finally:
                    closer = getattr(transport, "aclose", None)
                    if callable(closer):
                        await closer()
                elapsed = time.monotonic() - started
            return peer, bindings, error, elapsed

        for client in ("gateway_isolated", "gateway_retained", "dashboard", "fan"):
            for status in (307, 308):
                for version, form in peer_configurations():
                    with self.subTest(client=client, status=status, version=version, form=form):
                        with recording_bindings(), part1.capture_all_logs() as capture:
                            peer, bindings, error, elapsed = asyncio.run(
                                site_cell(client, status, version, form)
                            )
                        self.assertEqual(peer.counters.injected, 1)
                        self.assertIsInstance(error, DashboardTransportError)
                        self.assertEqual(error.category, UPSTREAM_REDIRECT_REFUSED)
                        self.assertFalse(error.retryable)
                        # Well before the operation deadline (30 s here).
                        self.assertLess(elapsed, 5.0)
                        self.assertEqual(peer.counters.origin["notifications/initialized"], 1)
                        self.assertEqual(_list_pages(peer.counters), 0)
                        self.assertEqual(peer.counters.origin["tools/call"], 0)
                        self.assertEqual(peer.counters.destination_total(), 0)
                        latched = [b for b in bindings if b.latched]
                        self.assertEqual(len(latched), 1)
                        self.assertEqual(latched[0].latch_writes, 1)
                        self.assertEqual(latched[0].refuse_callers[0], (
                            "NoRedirectBinding.response_hook", "operation"))
                        self.assert_no_canary(
                            part1._sinks(error, None) + capture.lines, url=peer.url
                        )

    def test_no_redirect_latch_hooks_after_latch(self):
        """After the latch: POST and GET refused before send, one DELETE allowed."""

        url = "http://127.0.0.1:9/private_synthetic/catalog/full?credential=synthetic"

        async def scenario():
            binding = NoRedirectBinding(endpoint_snapshot(url))
            bound = httpx.URL(url)
            outcomes = []
            for method in ("POST", "GET", "DELETE"):
                await binding.request_hook(httpx.Request(method, bound))
            outcomes.append(binding.delete_used)
            binding.delete_used = False
            redirect = httpx.Response(307, request=httpx.Request("POST", bound),
                                      headers={"Location": url + "/"})
            with self.assertRaises(UpstreamEndpointRefused) as raised:
                await binding.response_hook(redirect)
            outcomes.append(raised.exception.category)
            for method in ("POST", "GET"):
                with self.assertRaises(UpstreamEndpointRefused) as refused:
                    await binding.request_hook(httpx.Request(method, bound))
                outcomes.append((method, refused.exception.category))
            await binding.request_hook(httpx.Request("DELETE", bound))
            outcomes.append(("DELETE", "allowed"))
            with self.assertRaises(UpstreamEndpointRefused) as second:
                await binding.request_hook(httpx.Request("DELETE", bound))
            outcomes.append(("DELETE2", second.exception.category))
            with self.assertRaises(UpstreamEndpointRefused) as other:
                await binding.request_hook(httpx.Request("DELETE", url + "/"))
            outcomes.append(("other", other.exception.category))
            return binding, outcomes

        binding, outcomes = asyncio.run(scenario())
        self.assertEqual(
            outcomes,
            [
                True,
                UPSTREAM_REDIRECT_REFUSED,
                ("POST", UPSTREAM_REDIRECT_REFUSED),
                ("GET", UPSTREAM_REDIRECT_REFUSED),
                ("DELETE", "allowed"),
                ("DELETE2", UPSTREAM_REDIRECT_REFUSED),
                ("other", UPSTREAM_ENDPOINT_REJECTED),
            ],
        )
        self.assertEqual((binding.latch_code, binding.latch_phase),
                         (UPSTREAM_REDIRECT_REFUSED, "operation"))
        # Classification consults the latch before the exception's shape.
        for exc in (
            RuntimeError("synthetic"),
            asyncio.CancelledError(),
            BaseExceptionGroup("synthetic", [ConnectionError("synthetic")]),
            DashboardTransportError("timeout"),
        ):
            with self.subTest(exception=type(exc).__name__):
                mapped = McpReadGatewayTransport._map_operation_error(
                    exc, binding, caller_owns_task=False
                )
                self.assertIsInstance(mapped, UpstreamEndpointRefused)
                self.assertEqual(mapped.category, UPSTREAM_REDIRECT_REFUSED)
        self.assertEqual(binding.unfinished_error().category, UPSTREAM_REDIRECT_REFUSED)


class NoRedirectLatchLinearization(part1._NoRedirectAssertions, unittest.TestCase):
    """Codex F2 (PR #406 verdict 6731785026): an operation-phase redirect
    refusal latched before the step's outcome is recorded wins, even when the
    step succeeded and both the step and the latch are ready together. A
    cleanup-phase refusal never flips an outcome already recorded.

    Deterministic: ordering comes from events and the event loop's FIFO
    scheduling, never from sleeps or retries.
    """

    URL = "http://127.0.0.1:9/private_synthetic/catalog/full?credential=synthetic"

    def _race(self, step_factory, *, binding=None):
        async def scenario():
            bound = binding or NoRedirectBinding(endpoint_snapshot(self.URL))
            completed = []

            async def step():
                value = await step_factory(bound)
                completed.append(value)
                return value

            try:
                outcome = ("result", await bound.run_until_latched(step()))
            except UpstreamEndpointRefused as exc:
                outcome = ("refused", exc)
            return bound, completed, outcome

        return asyncio.run(scenario())

    def assert_operation_refused(self, binding, completed, outcome):
        # The step itself succeeded: the latch, not a step failure, decides.
        self.assertEqual(completed, ["synthetic-result"])
        self.assertEqual(outcome[0], "refused")
        self.assertEqual(outcome[1].category, UPSTREAM_REDIRECT_REFUSED)
        self.assertFalse(outcome[1].retryable)
        # Write uncertainty is preserved: the refusal never claims the call
        # was not attempted.
        self.assertTrue(outcome[1].tool_call_attempted)
        self.assertEqual((binding.latch_code, binding.latch_phase),
                         (UPSTREAM_REDIRECT_REFUSED, "operation"))
        self.assertIsNone(binding.step_failure)

    def test_operation_latch_set_inside_succeeding_step_wins(self):
        async def latch_then_succeed(binding):
            binding.refuse_redirect("operation")
            return "synthetic-result"

        self.assert_operation_refused(*self._race(latch_then_succeed))

    def test_operation_latch_and_step_both_ready_latch_wins(self):
        # The step schedules the latch with call_soon and returns at once, so
        # the step's completion and the latch land in the same loop pass
        # before run_until_latched resumes: both are ready.
        async def schedule_latch_then_succeed(binding):
            asyncio.get_running_loop().call_soon(binding.refuse_redirect, "operation")
            return "synthetic-result"

        self.assert_operation_refused(*self._race(schedule_latch_then_succeed))

    def test_operation_latch_woken_together_with_step_latch_wins(self):
        # The step waits on the same latch event as the waiter; one set()
        # wakes both, so asyncio.wait returns with task and waiter done.
        async def complete_on_latch(binding):
            event = binding._latch_event()
            asyncio.get_running_loop().call_soon(binding.refuse_redirect, "operation")
            await event.wait()
            return "synthetic-result"

        self.assert_operation_refused(*self._race(complete_on_latch))

    def test_cleanup_latch_does_not_flip_a_succeeding_step(self):
        async def cleanup_latch_then_succeed(binding):
            binding.refuse_redirect("cleanup")
            return "synthetic-result"

        binding, completed, outcome = self._race(cleanup_latch_then_succeed)
        self.assertEqual(completed, ["synthetic-result"])
        self.assertEqual(outcome, ("result", "synthetic-result"))
        self.assertEqual(binding.latch_phase, "cleanup")

    def test_latch_after_recorded_outcome_keeps_the_outcome(self):
        async def scenario():
            binding = NoRedirectBinding(endpoint_snapshot(self.URL))

            async def step():
                return "synthetic-result"

            recorded = await binding.run_until_latched(step())
            # A cleanup redirect after the outcome was recorded (the DELETE).
            binding.refuse_redirect("cleanup")
            return binding, recorded

        binding, recorded = asyncio.run(scenario())
        self.assertEqual(recorded, "synthetic-result")
        self.assertEqual(binding.latch_phase, "cleanup")

    def test_failing_step_with_operation_latch_still_refused(self):
        async def latch_then_fail(binding):
            binding.refuse_redirect("operation")
            raise RuntimeError("synthetic step failure")

        binding, completed, outcome = self._race(latch_then_fail)
        self.assertEqual(completed, [])
        self.assertEqual(outcome[0], "refused")
        self.assertEqual(outcome[1].category, UPSTREAM_REDIRECT_REFUSED)
        self.assertIsInstance(binding.step_failure, RuntimeError)

    def test_caller_cancellation_still_propagates(self):
        async def scenario():
            binding = NoRedirectBinding(endpoint_snapshot(self.URL))
            started = asyncio.Event()

            async def step():
                started.set()
                await asyncio.Event().wait()

            runner = asyncio.ensure_future(binding.run_until_latched(step()))
            await started.wait()
            runner.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await runner
            return binding

        binding = asyncio.run(scenario())
        self.assertFalse(binding.latched)

    def test_public_get_redirect_concurrent_with_tool_result_refused(self):
        """Public gateway tool: a GET-stream 307 and the valid tools/call result
        arrive together. The refusal wins in the result and in health, and
        nothing reaches the redirect destination."""

        import threading

        from test_hamcp129_platform_reads import call
        from test_hamcp319_no_redirect_useful import NoRedirectUseful, reviewed_catalog

        holds: list[str] = []

        class ConcurrentGetPeer(RedirectPeer):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.active = False
                self.call_ready = threading.Event()
                self.release_get = threading.Event()

            def _origin_handler(self):  # noqa: N805 - mirrors the base factory
                original = super()._origin_handler()
                peer = self

                class Handler(original):
                    def _send(self, status, body=b"", headers=None, content_type=None):
                        if self.command == "GET" and peer.active:
                            # Bounded event wait, not a timing retry: the GET
                            # is answered only after the client holds the tool
                            # result (release_get, set by BothReadyBinding).
                            if not peer.release_get.wait(DEADLINE_SECONDS):
                                return super()._send(status, body, headers, content_type)
                            return super()._send(
                                307, headers={"Location": peer.destination_base + "/synthetic"})
                        return super()._send(status, body, headers, content_type)

                    def _json(self, request_id, result, headers=None):
                        if isinstance(result, dict) and "structuredContent" in result:
                            peer.call_ready.set()
                        return super()._json(request_id, result, headers)

                return Handler

        class BothReadyBinding(NoRedirectBinding):
            """Once the tool-call step holds its result, releases the peer's
            GET 307 and completes the step on the same latch event the race's
            waiter uses: one set() makes the step and the latch ready together
            before the outcome is recorded (deterministic, no sleeps)."""

            peer: ConcurrentGetPeer | None = None

            async def run_until_latched(self, awaitable):
                peer = type(self).peer

                async def held():
                    value = await awaitable
                    if peer is not None and peer.active and peer.call_ready.is_set():
                        event = self._latch_event()
                        peer.release_get.set()
                        await asyncio.wait_for(event.wait(), DEADLINE_SECONDS)
                        holds.append(self.latch_phase)
                    return value

                return await super().run_until_latched(held())

        async def one(version):
            case = NoRedirectUseful()._delegated_read_case(version)
            peer = ConcurrentGetPeer(
                version=version,
                form="strict_full" if version == "9.1.0" else "legacy",
                tools=tuple(reviewed_catalog(version)),
                call_handler=lambda _name, _arguments: case.structured,
            )
            BothReadyBinding.peer = peer
            with peer.serve(), patch.object(upstream_read_module, "NoRedirectBinding", BothReadyBinding):
                gateway = UpstreamReadGateway()
                gateway.configure(
                    replace(part1._settings(peer.url), ha_mcp_release_registry_enabled=False))
                await asyncio.wait_for(gateway.initialize(FastMCP("x")), DEADLINE_SECONDS + 5)
                peer.active = True
                result = await asyncio.wait_for(
                    call(gateway, case.public_tool, case.public_arguments), DEADLINE_SECONDS * 2)
                health = gateway.health_snapshot()
            return peer, result, health

        for version in ("8.5.0", "9.1.0"):
            with self.subTest(version=version):
                holds.clear()
                with part1.capture_all_logs() as capture:
                    peer, result, health = asyncio.run(one(version))
                # The hold engaged: the latch was set before the step's outcome.
                self.assertEqual(holds, ["operation"])
                self.assertEqual(peer.counters.executions, 1)
                self.assertFalse(result["success"])
                self.assertEqual(result["details"]["failure_category"], UPSTREAM_REDIRECT_REFUSED)
                self.assertIs(result["retryable"], False)
                # The call was dispatched: the read is reported attempted.
                self.assertTrue(result["metadata"]["upstream_dispatch_occurred"])
                self.assertFalse(result["metadata"]["fallback_occurred"])
                self.assertNotIn("data", result)
                self.assertEqual(health["last_failure_category"], UPSTREAM_REDIRECT_REFUSED)
                self.assertEqual(peer.counters.destination_total(), 0)
                self.assert_no_canary(capture.lines + [json.dumps(result)], url=peer.url)


# -- the analysis fence (S-d) ---------------------------------------------------


async def run_analysis(peer_kwargs, *, deadline=DEADLINE_SECONDS):
    """One real analysis read: provider transport, _bounded_mcp_read, fence."""

    bindings: list[RecordingBinding] = []
    _CELL_BINDINGS.set(bindings)
    telemetry, token = begin_request("synthetic-analysis-request")
    peer = RedirectPeer(tool_names=DASHBOARD_TOOLS, **peer_kwargs)
    with peer.serve():
        transport = part1._dashboard_transport(peer.url)
        started = time.monotonic()
        result = error = None
        try:
            result = await transport.execute_analysis_read(
                dict(part1.DASHBOARD_ARGUMENTS),
                lambda _handshake: None,
                authorize=lambda: None,
                budget=CollectionBudget(),
            )
        except Exception as exc:  # noqa: BLE001
            error = exc
        finally:
            elapsed = time.monotonic() - started
            end_request(token)
    return SimpleNamespace(
        peer=peer,
        url=peer.url,
        bindings=bindings,
        telemetry=telemetry,
        result=result,
        error=error,
        elapsed=elapsed,
    )


class NoRedirectAnalysisFence(part1._NoRedirectAssertions, unittest.TestCase):
    """Section 8.1 item 1: analysis fence latch [R3-C2] and analysis rows."""

    def _assert_fence_latched(self, run, phase):
        self.assertEqual(len(run.bindings), 1)
        binding = run.bindings[0]
        self.assertEqual(binding.latch_code, UPSTREAM_REDIRECT_REFUSED)
        self.assertEqual(binding.latch_phase, phase)
        # Set exactly once, by the fence's 3xx branch; the response hook never
        # saw a redirect (the fence raised before handing one back).
        self.assertEqual(binding.latch_writes, 1)
        self.assertEqual(binding.refuse_callers, [("_AnalysisMcpHttp._request", phase)])
        self.assertEqual(binding.hook_redirects, 0)

    def _assert_balanced(self, run):
        self.assertEqual(run.telemetry.upstream_active_requests, 0)
        self.assertEqual(run.telemetry.upstream_request_count, 1)

    def _initialized_latch(self, status):
        for version, form in peer_configurations():
            for kind in LOCATION_KINDS:
                for delete_status in (200, 500):
                    with self.subTest(version=version, form=form, kind=kind,
                                      delete_status=delete_status):
                        with recording_bindings(), part1.capture_all_logs() as capture:
                            run = asyncio.run(run_analysis(dict(
                                version=version, form=form,
                                inject=Injection("notifications/initialized", status, kind),
                                delete_status=delete_status,
                            )))
                        counters = run.peer.counters
                        self.assertEqual(counters.injected, 1)
                        self.assertIsInstance(run.error, DashboardTransportError)
                        self.assertEqual(run.error.category, UPSTREAM_REDIRECT_REFUSED)
                        self.assertNotIn(run.error.category, {
                            "timeout", "protocol_error", "endpoint_rejected"})
                        self.assertFalse(run.error.retryable)
                        self.assertLess(run.elapsed, 5.0)
                        self._assert_fence_latched(run, "operation")
                        self.assertEqual(counters.origin["initialize"], 1)
                        self.assertEqual(counters.origin["notifications/initialized"], 1)
                        self.assertEqual(_list_pages(counters), 0)
                        self.assertEqual(counters.origin["tools/call"], 0)
                        self.assertEqual(_origin_gets(counters), 0)
                        self.assertLessEqual(counters.origin["delete"], 1)
                        self.assertEqual(counters.destination_total(), 0)
                        self.assertEqual(counters.executions, 0)
                        self._assert_balanced(run)
                        self.assertEqual(
                            OPERATOR_MESSAGES[run.error.category],
                            UPSTREAM_REDIRECT_OPERATOR_MESSAGE,
                        )
                        self.assert_no_canary(
                            part1._sinks(run.error, run.result) + capture.lines,
                            url=run.url, location=run.peer.location_for(kind),
                        )

    def test_no_redirect_analysis_initialized_latch_307(self):
        self._initialized_latch(307)

    def test_no_redirect_analysis_initialized_latch_308(self):
        self._initialized_latch(308)

    def test_no_redirect_analysis_rows_latched_by_fence(self):
        """initialize, list pages, tools/call and the DELETE cleanup."""

        rows = ("initialize", "tools/list:first", "tools/list:cursor", "tools/call", "delete")
        for request_type in rows:
            statuses = (307, 308) if request_type == "delete" else GET_POST_STATUSES
            for status in statuses:
                for version, form in peer_configurations():
                    kind = LOCATION_KINDS[statuses.index(status) % len(LOCATION_KINDS)]
                    with self.subTest(row=request_type, status=status, version=version, form=form):
                        with recording_bindings(), part1.capture_all_logs() as capture:
                            run = asyncio.run(run_analysis(dict(
                                version=version, form=form,
                                inject=Injection(request_type, status, kind),
                            )))
                        counters = run.peer.counters
                        self.assertEqual(counters.injected, 1)
                        self.assertIsInstance(run.error, DashboardTransportError)
                        self.assertEqual(run.error.category, UPSTREAM_REDIRECT_REFUSED)
                        self.assertFalse(run.error.retryable)
                        self.assertLess(run.elapsed, 5.0)
                        phase = "cleanup" if request_type == "delete" else "operation"
                        self._assert_fence_latched(run, phase)
                        self.assertEqual(counters.origin[request_type], 1)
                        self.assertEqual(counters.destination_total(), 0)
                        self.assertEqual(_origin_gets(counters), 0)
                        self.assertLessEqual(counters.origin["delete"], 1)
                        self._assert_balanced(run)
                        if request_type == "initialize":
                            self.assertEqual(counters.origin["notifications/initialized"], 0)
                        if request_type in {"initialize", "tools/list:first"}:
                            self.assertEqual(counters.origin["tools/list:cursor"], 0)
                        if request_type != "delete":
                            # tools/call: the redirected call is not executed.
                            if request_type != "tools/call":
                                self.assertEqual(counters.origin["tools/call"], 0)
                            self.assertEqual(counters.executions, 0)
                        else:
                            # The call ran; its cleanup redirect still fails
                            # the read closed, with no second DELETE.
                            self.assertEqual(counters.executions, 1)
                        self.assert_no_canary(
                            part1._sinks(run.error, run.result) + capture.lines,
                            url=run.url, location=run.peer.location_for(kind),
                        )

    def test_analysis_useful_no_redirect(self):
        for version, form in peer_configurations():
            with self.subTest(version=version, form=form):
                with recording_bindings(), part1.capture_all_logs() as capture:
                    run = asyncio.run(run_analysis(dict(version=version, form=form)))
                counters = run.peer.counters
                self.assertIsNone(run.error, part1._category(run.error))
                self.assertEqual(run.result.call_result["structuredContent"], {"ok": True})
                self.assertEqual(
                    tuple(tool["name"] for tool in run.result.handshake.tools),
                    DASHBOARD_TOOLS,
                )
                self.assertEqual(run.result.handshake.server_version, version)
                self.assertEqual(len(run.bindings), 1)
                self.assertIsNone(run.bindings[0].latch_code)
                self.assertEqual(run.bindings[0].refuse_callers, [])
                self.assertEqual(counters.origin["initialize"], 1)
                self.assertEqual(counters.origin["notifications/initialized"], 1)
                self.assertEqual(counters.origin["tools/list:first"], 1)
                self.assertEqual(counters.origin["tools/list:cursor"], 1)
                self.assertEqual(counters.origin["tools/call"], 1)
                self.assertEqual(counters.origin["delete"], 1)
                self.assertEqual(_origin_gets(counters), 0)
                self.assertEqual(counters.executions, 1)
                self.assertEqual(counters.destination_total(), 0)
                self._assert_balanced(run)
                self.assert_no_canary(capture.lines, url=run.url)

    def test_no_redirect_analysis_client_from_no_redirect_factory(self):
        """Section 3.3 analysis row: the session's client comes from the shared
        no-redirect factory ("the same hooks and max_redirects=0").

        The fence's own 3xx branch would refuse a redirect even on a plain
        client, so the latch alone cannot show which client carried the
        session. The binding's diagnostics can: the factory recorded building
        the client, and both hooks saw every exchange that reached the origin.
        """

        for version, form in peer_configurations():
            for inject in (None, Injection("notifications/initialized", 307, LOCATION_KINDS[0])):
                label = "useful" if inject is None else "latched"
                with self.subTest(version=version, form=form, run=label):
                    kwargs = dict(version=version, form=form)
                    if inject is not None:
                        kwargs["inject"] = inject
                    with recording_bindings():
                        run = asyncio.run(run_analysis(kwargs))
                    counters = run.peer.counters
                    self.assertEqual(len(run.bindings), 1)
                    binding = run.bindings[0]
                    self.assertTrue(binding.factory_installed)
                    self.assertTrue(binding.client_created, "client not built by the factory")
                    origin = counters.origin_total()
                    self.assertGreater(origin, 0)
                    # The request hook saw every request that reached the
                    # origin; the response hook every non-3xx answer the fence
                    # handed back (a 3xx never leaves the fence).
                    self.assertGreaterEqual(binding.request_hook_calls, origin)
                    self.assertGreaterEqual(binding.hook_calls, origin - counters.injected)
                    self.assertEqual(binding.hook_redirects, 0)
                    self.assertEqual(binding.delete_used, counters.origin["delete"] == 1)
                    if inject is None:
                        self.assertIsNone(run.error, part1._category(run.error))
                        self.assertIsNone(binding.latch_code)
                        self.assertEqual(counters.origin["delete"], 1)
                    else:
                        self.assertEqual(run.error.category, UPSTREAM_REDIRECT_REFUSED)
                        self._assert_fence_latched(run, "operation")
                    self.assertEqual(counters.destination_total(), 0)


class NoRedirectAnalysisProviderReason(part1._NoRedirectAssertions, unittest.TestCase):
    """Architect ruling 2026-11-07, item 4: a refused redirect on the analysis
    transport reaches the analysis surface as its own reason code,
    upstream_redirect_refused, set through the binding latch (the fence's 3xx
    branch calls binding.refuse_redirect), never authority_unavailable."""

    _assert_fence_latched = NoRedirectAnalysisFence._assert_fence_latched

    def test_analysis_redirect_reports_upstream_redirect_refused_via_latch(self):
        from ha_mcp_engineering.dashboard_analysis import contracts as analysis_contracts
        from ha_mcp_engineering.errors import ErrorCode, map_exception

        async def analysis(peer_kwargs):
            bindings: list[RecordingBinding] = []
            _CELL_BINDINGS.set(bindings)
            _telemetry, token = begin_request("synthetic-analysis-reason")
            peer = RedirectPeer(tool_names=DASHBOARD_TOOLS, **peer_kwargs)
            error = None
            with peer.serve():
                provider = UpstreamDashboardProvider()
                provider.configure(part1._settings(peer.url))
                try:
                    with patch.object(UpstreamDashboardProvider, "analysis_authority",
                                      lambda _self: ("synthetic-authority",)):
                        await provider.get_analysis_configuration(
                            url_path=part1.DASHBOARD_ARGUMENTS["url_path"],
                            authorize=lambda: None, budget=CollectionBudget())
                except Exception as exc:  # noqa: BLE001
                    error = exc
                finally:
                    end_request(token)
            return SimpleNamespace(peer=peer, url=peer.url, bindings=bindings, error=error)

        # Rows before admission and the cleanup. tools/call follows the
        # provider's admission of the handshake, which this harness does not
        # model (synthetic authority); its fence row is covered at transport
        # level by test_no_redirect_analysis_rows_latched_by_fence.
        rows = (("initialize", "operation"), ("notifications/initialized", "operation"),
                ("tools/list:first", "operation"), ("delete", "cleanup"))
        for version, form in peer_configurations():
            for request_type, phase in rows:
                for status in (307, 308):
                    with self.subTest(version=version, form=form, row=request_type,
                                      status=status):
                        with recording_bindings(), part1.capture_all_logs() as capture:
                            run = asyncio.run(analysis(dict(
                                version=version, form=form,
                                inject=Injection(request_type, status, LOCATION_KINDS[0]))))
                        self.assertIsInstance(run.error, analysis_contracts.AnalysisError)
                        self.assertEqual(run.error.reason, UPSTREAM_REDIRECT_REFUSED)
                        self.assertNotEqual(run.error.reason, "authority_unavailable")
                        self.assertEqual(run.error.code, ErrorCode.PROVIDER_UNAVAILABLE)
                        self.assertFalse(run.error.retryable)
                        self.assertEqual(run.error.details, {
                            "reason": UPSTREAM_REDIRECT_REFUSED,
                            "operator_remediation": UPSTREAM_REDIRECT_OPERATOR_MESSAGE})
                        _code, _message, retryable, details = map_exception(run.error)
                        self.assertFalse(retryable)
                        self.assertEqual(details["reason"], UPSTREAM_REDIRECT_REFUSED)
                        # Through the latch: set once, by the fence's 3xx branch.
                        self._assert_fence_latched(run, phase)
                        self.assertEqual(run.peer.counters.destination_total(), 0)
                        self.assert_no_canary(
                            part1._sinks(run.error, None) + capture.lines,
                            url=run.url, location=run.peer.location_for(LOCATION_KINDS[0]))

    def test_analysis_redirect_reason_requires_the_latch(self):
        """Negative: without the latch the same 3xx is not reported as a
        refused redirect; the reason is the latch's, not a local rename."""

        from ha_mcp_engineering.dashboard_analysis import contracts as analysis_contracts

        original = NoRedirectBinding.refuse_redirect

        def unlatched(self, phase="operation"):
            return DashboardTransportError("protocol_error", retryable=False)

        async def analysis(peer_kwargs):
            _CELL_BINDINGS.set([])
            _telemetry, token = begin_request("synthetic-analysis-unlatched")
            peer = RedirectPeer(tool_names=DASHBOARD_TOOLS, **peer_kwargs)
            error = None
            with peer.serve():
                provider = UpstreamDashboardProvider()
                provider.configure(part1._settings(peer.url))
                try:
                    with patch.object(UpstreamDashboardProvider, "analysis_authority",
                                      lambda _self: ("synthetic-authority",)):
                        await provider.get_analysis_configuration(
                            url_path=part1.DASHBOARD_ARGUMENTS["url_path"],
                            authorize=lambda: None, budget=CollectionBudget())
                except Exception as exc:  # noqa: BLE001
                    error = exc
                finally:
                    end_request(token)
            return error

        version, form = peer_configurations()[0]
        with patch.object(NoRedirectBinding, "refuse_redirect", unlatched):
            error = asyncio.run(analysis(dict(
                version=version, form=form,
                inject=Injection("notifications/initialized", 307, LOCATION_KINDS[0]))))
        self.assertIs(NoRedirectBinding.refuse_redirect, original)
        self.assertIsInstance(error, analysis_contracts.AnalysisError)
        self.assertNotEqual(error.reason, UPSTREAM_REDIRECT_REFUSED)
        self.assertNotIn("operator_remediation", error.details)


# -- operator message and the 8.5 legacy behavior change -------------------------


class NoRedirectOperatorAndLegacy(part1._NoRedirectAssertions, unittest.TestCase):
    def test_redirect_refusal_operator_message_fixed(self):
        """The fixed text, exactly, at every surface; never the URL or Location."""

        self.assertEqual(UPSTREAM_REDIRECT_OPERATOR_MESSAGE, EXPECTED_OPERATOR_MESSAGE)
        self.assertEqual(OPERATOR_MESSAGES[UPSTREAM_REDIRECT_REFUSED],
                         UPSTREAM_REDIRECT_OPERATOR_MESSAGE)
        for canary in ALL_CANARIES + ("http", "127.0.0.1", "Location", "307", "308"):
            self.assertNotIn(canary, UPSTREAM_REDIRECT_OPERATOR_MESSAGE)

        async def dashboard(version, form, status, kind):
            peer = RedirectPeer(version=version, form=form,
                                inject=Injection("initialize", status, kind),
                                tool_names=DASHBOARD_TOOLS)
            with peer.serve():
                provider = UpstreamDashboardProvider()
                provider.configure(part1._settings(peer.url))
                error = None
                try:
                    await provider.refresh_capabilities()
                except DashboardProviderError as exc:
                    error = exc
                health = provider.health_snapshot()
            return peer, error, health

        async def gateway(version, form, status, kind):
            peer = RedirectPeer(version=version, form=form,
                                inject=Injection("initialize", status, kind),
                                tool_names=READ_TOOLS)
            with peer.serve():
                provider = UpstreamReadGateway()
                provider.configure(part1._settings(peer.url))
                server = FastMCP("hamcp319-no-redirect")
                outcome = None
                try:
                    outcome = await provider.initialize(server)
                except Exception as exc:  # noqa: BLE001
                    outcome = exc
                health = provider.health_snapshot()
                projection = provider.health_projection()
                capability_text = repr(provider._dynamic_capabilities)
            return peer, outcome, health, projection, capability_text

        for version, form in peer_configurations():
            for status in GET_POST_STATUSES:
                kind = LOCATION_KINDS[GET_POST_STATUSES.index(status)]
                with self.subTest(surface="dashboard", version=version, form=form, status=status):
                    with part1.capture_all_logs() as capture:
                        peer, error, health = asyncio.run(dashboard(version, form, status, kind))
                    self.assertEqual(peer.counters.injected, 1)
                    self.assertIsInstance(error, DashboardProviderError)
                    self.assertEqual(error.details["failure_category"], UPSTREAM_REDIRECT_REFUSED)
                    self.assertEqual(error.details["operator_remediation"],
                                     UPSTREAM_REDIRECT_OPERATOR_MESSAGE)
                    self.assertEqual(health["operator_remediation"],
                                     UPSTREAM_REDIRECT_OPERATOR_MESSAGE)
                    self.assertEqual(peer.counters.destination_total(), 0)
                    self.assert_no_canary(
                        [repr(error), str(error), repr(error.details), repr(health)]
                        + capture.lines,
                        url=peer.url, location=peer.location_for(kind),
                    )
                with self.subTest(surface="gateway", version=version, form=form, status=status):
                    with part1.capture_all_logs() as capture:
                        peer, outcome, health, projection, capability_text = asyncio.run(
                            gateway(version, form, status, kind))
                    self.assertEqual(peer.counters.injected, 1)
                    self.assertEqual(health["last_discovery_failure_category"],
                                     UPSTREAM_REDIRECT_REFUSED)
                    self.assertEqual(health["recommended_action"],
                                     UPSTREAM_REDIRECT_OPERATOR_MESSAGE)
                    self.assertEqual(projection["operator_remediation"],
                                     UPSTREAM_REDIRECT_OPERATOR_MESSAGE)
                    self.assertEqual(
                        gateway_module._safe_failure_message(UPSTREAM_REDIRECT_REFUSED),
                        UPSTREAM_REDIRECT_OPERATOR_MESSAGE,
                    )
                    self.assertEqual(peer.counters.destination_total(), 0)
                    self.assertEqual(_list_pages(peer.counters), 0)
                    self.assert_no_canary(
                        [repr(outcome), str(outcome), repr(health), repr(projection),
                         capability_text] + capture.lines,
                        url=peer.url, location=peer.location_for(kind),
                    )

    def test_85_legacy_value_redirect_refused(self):
        """[JD-DCL1] An 8.5 legacy value that today's SDK default would follow
        through a same-origin 307 is refused, with zero destination requests."""

        async def cell(client, kind, request_type):
            builder, client_kind, _r, _f = CLIENTS[client]
            peer = RedirectPeer(version="8.5.0", form="legacy",
                                inject=Injection(request_type, 307, kind),
                                tool_names=DASHBOARD_TOOLS if client_kind != "read" else READ_TOOLS)
            with peer.serve():
                transport = builder(peer.url)
                error = result = None
                try:
                    result = await part1._drive(client, transport, client_kind, request_type)
                except Exception as exc:  # noqa: BLE001
                    error = exc
                finally:
                    closer = getattr(transport, "aclose", None)
                    if callable(closer):
                        await closer()
            return peer, result, error

        # Control: the SDK default factory follows exactly this redirect.
        async def sdk_default(kind):
            peer = RedirectPeer(version="8.5.0", form="legacy",
                                inject=Injection("initialize", 307, kind))
            with peer.serve():
                client = create_mcp_http_client(headers=_headers())
                async with client:
                    async with stream_within_origin(
                        client, "POST", peer.url, json=_initialize_body()
                    ) as response:
                        status = response.status_code
            return peer, status

        for kind in SAME_ORIGIN_KINDS:
            with self.subTest(control="sdk_default", kind=kind):
                peer, status = asyncio.run(sdk_default(kind))
                self.assertEqual(peer.counters.injected, 1)
                self.assertEqual(peer.counters.destination_total(), 1)
            for client in CLIENTS:
                for request_type in ("initialize", "tools/list:first"):
                    with self.subTest(client=client, kind=kind, row=request_type):
                        with part1.capture_all_logs() as capture:
                            peer, result, error = asyncio.run(cell(client, kind, request_type))
                        self.assertEqual(peer.counters.injected, 1)
                        self.assertIsInstance(error, DashboardTransportError)
                        self.assertEqual(error.category, UPSTREAM_REDIRECT_REFUSED)
                        self.assertEqual(peer.counters.destination_total(), 0)
                        self.assertEqual(peer.counters.executions, 0)
                        self.assert_no_canary(
                            part1._sinks(error, result) + capture.lines,
                            url=peer.url, location=peer.location_for(kind),
                        )


if __name__ == "__main__":
    unittest.main()
