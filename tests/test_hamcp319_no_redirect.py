"""HAMCP-319 design sections 3.2, 3.3, 8.1 items 1-2 and 8.2: no redirects.

Every ha-mcp connection of every client runs on one no-redirect transport. The
peers are disposable loopback HTTP servers with separate origin and
destination counters (``support.ha_mcp_redirect_peer``); no Docker, network or
Home Assistant access. All secrets and canaries are synthetic.

Matrix test names follow ``test_no_redirect_<client>_<request_type>_<status>_<peer>``.
For the site-canonical clients (gateway S-a and S-b, dashboard S-c, analysis
S-d) one test runs every ``Location`` kind; the clients that share S-a through
their own provider construction (fan, power, helper, backup, lifecycle) rotate
the ``Location`` kind by status. ``notifications/cancelled`` and GET
resumption are never emitted by Engineering under the pinned SDK (mcp 1.30.0):
those cells drive the SDK session directly with the same no-redirect factory.
"""

from __future__ import annotations

import ast
import asyncio
from contextlib import contextmanager
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from mcp import types  # noqa: E402
from mcp.client.session import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamablehttp_client  # noqa: E402
from mcp.shared.message import ClientMessageMetadata  # noqa: E402

from ha_mcp_engineering.clients import ha_mcp_endpoint as endpoint_module  # noqa: E402
from ha_mcp_engineering.clients.dashboard_analysis import CollectionBudget  # noqa: E402
from ha_mcp_engineering.clients.ha_mcp_endpoint import (  # noqa: E402
    ENDPOINT_REFUSAL_CODES,
    LEGACY,
    OPERATOR_MESSAGES,
    STRICT_FULL,
    UPSTREAM_CATALOG_MODE_UNADMITTED,
    UPSTREAM_CATALOG_SELECTOR_INVALID,
    UPSTREAM_ENDPOINT_REJECTED,
    UPSTREAM_REDIRECT_OPERATOR_MESSAGE,
    UPSTREAM_REDIRECT_REFUSED,
    NoRedirectBinding,
    UpstreamEndpointRefused,
    classify_endpoint_form,
    endpoint_snapshot,
    ha_mcp_no_redirect_client_factory,
    publish_endpoint_snapshot,
    require_selector_agreement,
    selector_refusal_code,
)
from ha_mcp_engineering.clients.mcp import (  # noqa: E402
    DashboardTransportError,
    McpDashboardTransport,
)
from ha_mcp_engineering.clients.upstream_read import McpReadGatewayTransport  # noqa: E402
from ha_mcp_engineering.configuration import (  # noqa: E402
    Settings,
    parse_upstream_dashboard_endpoint,
)
from ha_mcp_engineering.providers.operational_backup import (  # noqa: E402
    ReviewedOperationalBackupProvider,
)
from ha_mcp_engineering.providers.operational_lifecycle import (  # noqa: E402
    ReviewedOperationalLifecycleProvider,
)
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    UpstreamDashboardProvider,
)
from ha_mcp_engineering.providers.upstream_fan import FanProvider  # noqa: E402
from ha_mcp_engineering.providers.upstream_helper_value import HelperProvider  # noqa: E402
from ha_mcp_engineering.providers.upstream_power import PowerProvider  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from support.ha_mcp_redirect_peer import (  # noqa: E402
    ALL_CANARIES,
    DELETE_STATUSES,
    GET_POST_STATUSES,
    LOCATION_CANARY,
    LOCATION_KINDS,
    Injection,
    RedirectPeer,
    peer_configurations,
)

DEADLINE_SECONDS = 30.0
DASHBOARD_ARGUMENTS = {
    "url_path": "synthetic",
    "list_only": False,
    "force_reload": True,
    "include_screenshot": False,
}
DASHBOARD_TOOLS = ("ha_config_get_dashboard", "ha_synthetic_two")
READ_TOOLS = ("ha_synthetic_one", "ha_synthetic_two")
PEER_SLUGS = {
    ("8.5.0", "legacy"): "v85_legacy",
    ("8.5.0", "strict_full"): "v85_strict",
    ("9.1.0", "strict_full"): "v91_strict",
}
REQUEST_SLUGS = {
    "initialize": "initialize",
    "notifications/initialized": "initialized",
    "notifications/cancelled": "cancelled",
    "tools/list:first": "list_first",
    "tools/list:cursor": "list_cursor",
    "tools/call": "call",
    "get_stream": "get_stream",
    "get_resumption": "get_resumption",
    "get_reconnect": "get_reconnect",
    "retained_reconnect": "retained_reconnect",
    "delete": "delete",
}
SECRET_SETTINGS = ("synthetic-no-real-token", "synthetic-no-real-secret")


def _settings(url: str, **overrides) -> Settings:
    values = dict(
        ha_url="http://127.0.0.1:8123",
        ha_token=SECRET_SETTINGS[0],
        access_secret=SECRET_SETTINGS[1],
        port=8100,
        audit_path="unused-synthetic",
        rate_limit_per_minute=120,
        rate_limit_burst=20,
        destructive_services=frozenset(),
        ha_timeout_seconds=DEADLINE_SECONDS,
        upstream_dashboard_mcp_url=url,
    )
    values.update(overrides)
    return Settings(**values)


_AUTHORITY = SimpleNamespace(
    fan_provider_authority_token=object(),
    power_provider_authority_token=object(),
    helper_value_provider_authority_token=object(),
)


def _gateway_transport(url: str, *, retained: bool):
    gateway = UpstreamReadGateway()
    gateway.configure(_settings(url))
    transport = gateway._transport
    assert isinstance(transport, McpReadGatewayTransport)
    if not retained:
        assert transport._retain_session is False
        return transport
    # Retention follows ha_mcp_release_registry_enabled, which also needs a
    # signed trust anchor that has no place in an offline transport test. Build
    # the retained transport exactly as configure() does
    # (upstream_read_gateway.py McpReadGatewayTransport construction), over the
    # snapshot that configure() just published for this generation.
    retained_transport = McpReadGatewayTransport(
        publish_endpoint_snapshot(url),
        timeout_seconds=DEADLINE_SECONDS,
        client_version="2.4.2-beta.7",
        retain_session=True,
    )
    assert retained_transport._snapshot.generation == transport._snapshot.generation
    return retained_transport


def _dashboard_transport(url: str):
    provider = UpstreamDashboardProvider()
    provider.configure(_settings(url))
    transport = provider._transport
    assert isinstance(transport, McpDashboardTransport)
    return transport


def _typed_transport(cls):
    def build(url: str):
        transport = cls.configured(_settings(url), _AUTHORITY).transport
        assert isinstance(transport, McpReadGatewayTransport)
        return transport

    return build


def _operational_transport(cls):
    def build(url: str):
        provider = cls()
        provider.configure(_settings(url))
        transport = provider._transport
        assert isinstance(transport, McpReadGatewayTransport)
        return transport

    return build


READ_REQUESTS = (
    "initialize",
    "notifications/initialized",
    "tools/list:first",
    "tools/list:cursor",
    "tools/call",
    "get_stream",
    "get_reconnect",
    "delete",
)
ANALYSIS_REQUESTS = (
    "initialize",
    "notifications/initialized",
    "tools/list:first",
    "tools/list:cursor",
    "tools/call",
    "delete",
)

# name -> (builder, kind, request types, full Location cross)
CLIENTS = {
    "gateway_isolated": (
        lambda url: _gateway_transport(url, retained=False),
        "read",
        READ_REQUESTS,
        True,
    ),
    "gateway_retained": (
        lambda url: _gateway_transport(url, retained=True),
        "read",
        READ_REQUESTS + ("retained_reconnect",),
        True,
    ),
    "dashboard": (_dashboard_transport, "dashboard", READ_REQUESTS, True),
    "analysis": (_dashboard_transport, "analysis", ANALYSIS_REQUESTS, True),
    "fan": (_typed_transport(FanProvider), "read", READ_REQUESTS, False),
    "power": (_typed_transport(PowerProvider), "read", READ_REQUESTS, False),
    "helper": (_typed_transport(HelperProvider), "read", READ_REQUESTS, False),
    "backup": (
        _operational_transport(ReviewedOperationalBackupProvider),
        "read",
        READ_REQUESTS,
        False,
    ),
    "lifecycle": (
        _operational_transport(ReviewedOperationalLifecycleProvider),
        "read",
        READ_REQUESTS,
        False,
    ),
}


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
            if record.exc_info:
                self.lines.append(logging.Formatter().formatException(record.exc_info))
        except Exception:  # pragma: no cover - capture must never fail a test
            self.lines.append(repr(record.msg))


@contextmanager
def capture_all_logs():
    handler = _Capture()
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


def _category(error: BaseException | None) -> str | None:
    if error is None:
        return None
    return getattr(error, "category", None) or type(error).__name__


def _sinks(error: BaseException | None, result) -> list[str]:
    values = [repr(result), str(result)]
    if error is not None:
        values.extend((repr(error), str(error), repr(getattr(error, "args", ()))))
        cause = error.__cause__ or error.__context__
        while cause is not None:
            values.extend((repr(cause), str(cause)))
            cause = cause.__cause__ or cause.__context__
    return values


async def _drive(client: str, transport, kind: str, request_type: str):
    """Run the client operation that reaches ``request_type``."""

    validator = lambda _catalog: None  # noqa: E731 - admission is not under test
    if kind == "read":
        if request_type == "retained_reconnect":
            await transport.execute_read(
                READ_TOOLS[0],
                {},
                timeout_seconds=DEADLINE_SECONDS,
                catalog_validator=validator,
            )
            return await transport.discover()
        if request_type in {"tools/call", "get_reconnect", "delete"}:
            return await transport.execute_read(
                READ_TOOLS[0],
                {},
                timeout_seconds=DEADLINE_SECONDS,
                catalog_validator=validator,
            )
        return await transport.discover()
    if kind == "dashboard":
        return await transport.execute_dashboard_read(
            dict(DASHBOARD_ARGUMENTS), validator
        )
    return await transport.execute_analysis_read(
        dict(DASHBOARD_ARGUMENTS),
        validator,
        authorize=lambda: None,
        budget=CollectionBudget(),
    )


async def run_cell(client, request_type, status, location_kind, version, form):
    builder, kind, _requests, _full = CLIENTS[client]
    occurrence = 1
    injected_type = request_type
    if request_type == "retained_reconnect":
        injected_type, occurrence = "initialize", 2
    peer = RedirectPeer(
        version=version,
        form=form,
        inject=Injection(injected_type, status, location_kind, occurrence),
        sse_call=request_type == "get_reconnect",
        hold_list_until_get=request_type == "get_stream",
        tool_names=DASHBOARD_TOOLS if kind != "read" else READ_TOOLS,
    )
    with peer.serve():
        location = peer.location_for(location_kind)
        transport = builder(peer.url)
        result = error = None
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            result = await asyncio.wait_for(
                _drive(client, transport, kind, request_type),
                DEADLINE_SECONDS + 5,
            )
        except Exception as exc:  # noqa: BLE001 - the cell records the outcome
            error = exc
        finally:
            closer = getattr(transport, "aclose", None)
            if callable(closer):
                try:
                    await closer()
                except Exception:  # noqa: BLE001 - close after refusal
                    pass
        elapsed = loop.time() - started
    return SimpleNamespace(
        client=client,
        kind=kind,
        request_type=request_type,
        injected_type=injected_type,
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
    )


class _NoRedirectAssertions:
    def assert_no_canary(self, values, *, url=None, location=None):
        canaries = list(ALL_CANARIES)
        if url:
            canaries.append(url)
        if location:
            canaries.append(location)
        for value in values:
            for canary in canaries:
                self.assertNotIn(canary, value)

    def assert_cell(self, cell, logs):
        label = (
            f"{cell.client}/{cell.request_type}/{cell.status}/"
            f"{cell.location_kind}/{cell.version}/{cell.form}"
        )
        counters = cell.counters
        with self.subTest(cell=label):
            self.assertEqual(cell.injected, 1, "the redirect was never served")
            self.assertIn(LOCATION_CANARY, cell.location)
            for canary in ALL_CANARIES[:4]:
                self.assertIn(canary, cell.url)
            # Nothing follows the Location: zero requests of any method.
            self.assertEqual(counters.destination_total(), 0, dict(counters.destination))
            self.assertLessEqual(counters.origin["delete"], 1)
            phase_count = counters.origin[cell.injected_type]
            if cell.request_type == "retained_reconnect":
                self.assertEqual(phase_count, 2)
            else:
                self.assertEqual(phase_count, 1)
            if cell.request_type == "delete" and cell.kind != "analysis":
                # The recorded outcome survives a refused cleanup redirect.
                self.assertIsNone(cell.error, _category(cell.error))
                self.assertIsNotNone(cell.result)
                self.assertEqual(counters.executions, 1)
            else:
                self.assertIsNotNone(cell.error, "the redirect was not refused")
                self.assertIsInstance(cell.error, DashboardTransportError)
                self.assertEqual(cell.error.category, UPSTREAM_REDIRECT_REFUSED)
                self.assertFalse(cell.error.retryable)
                self.assertLess(cell.elapsed, DEADLINE_SECONDS)
                expected_executions = (
                    1
                    if cell.request_type in {"retained_reconnect", "delete"}
                    else 0
                )
                self.assertEqual(counters.executions, expected_executions)
            if cell.injected_type in {"initialize", "notifications/initialized"}:
                list_pages = (
                    counters.origin["tools/list:first"]
                    + counters.origin["tools/list:cursor"]
                )
                expected_pages = 2 if cell.request_type == "retained_reconnect" else 0
                self.assertEqual(list_pages, expected_pages)
            if cell.injected_type == "tools/list:first":
                self.assertEqual(counters.origin["tools/list:cursor"], 0)
                self.assertEqual(counters.origin["tools/call"], 0)
            if cell.injected_type == "tools/list:cursor":
                self.assertEqual(counters.origin["tools/call"], 0)
            if cell.kind == "analysis":
                gets = sum(
                    counters.origin[name]
                    for name in ("get_stream", "get_resumption", "get_reconnect")
                )
                self.assertEqual(gets, 0)
            self.assert_no_canary(
                _sinks(cell.error, cell.result) + logs,
                url=cell.url,
                location=cell.location,
            )


def _matrix_test(client, request_type, status, version, form):
    _builder, _kind, _requests, full = CLIENTS[client]
    if full:
        kinds = LOCATION_KINDS
    else:
        statuses = DELETE_STATUSES if request_type == "delete" else GET_POST_STATUSES
        kinds = (LOCATION_KINDS[statuses.index(status) % len(LOCATION_KINDS)],)

    def test(self):
        async def scenario():
            if client == "gateway_retained":
                # Each cell publishes its own generation, and a retained
                # transport refuses a stale one; run these cells one by one.
                return [
                    await run_cell(client, request_type, status, kind, version, form)
                    for kind in kinds
                ]
            return await asyncio.gather(
                *(
                    run_cell(client, request_type, status, kind, version, form)
                    for kind in kinds
                )
            )

        with capture_all_logs() as capture:
            cells = asyncio.run(scenario())
        for cell in cells:
            self.assert_cell(cell, capture.lines)

    test.__doc__ = (
        f"{client}: a {status} on {request_type} ({version} {form}) is refused."
    )
    return test


class NoRedirectMatrix(_NoRedirectAssertions, unittest.TestCase):
    """Section 8.1 item 1: every client, request type, status and peer."""


for _client, (_builder, _kind, _requests, _full) in CLIENTS.items():
    for _request_type in _requests:
        _statuses = DELETE_STATUSES if _request_type == "delete" else GET_POST_STATUSES
        for _status in _statuses:
            for _version, _form in peer_configurations():
                _name = (
                    f"test_no_redirect_{_client}_{REQUEST_SLUGS[_request_type]}_"
                    f"{_status}_{PEER_SLUGS[(_version, _form)]}"
                )
                setattr(
                    NoRedirectMatrix,
                    _name,
                    _matrix_test(_client, _request_type, _status, _version, _form),
                )
