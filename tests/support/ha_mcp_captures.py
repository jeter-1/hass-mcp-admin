"""Committed ha-mcp catalog captures and synthetic read transports (HAMCP-437).

Moved from the retired ``test_upstream_8_5_compatibility`` and
``test_upstream_8_6_compatibility`` modules so their importers keep working.
``capture`` takes an explicit version: callers name the catalog they read.
The transports never contact an endpoint; results come from committed fixtures
or from the caller.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from mcp.server.fastmcp import FastMCP  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from support.ha_mcp_920_receipts import receipt  # noqa: E402
from test_ha_mcp_production_readmission import _GatewayTransport, _settings  # noqa: E402

EVIDENCE = ROOT / "docs/evidence/upstream-read-compatibility"


def capture(version: str) -> dict:
    """The committed catalog capture of ``version`` in reviewed runtime order."""

    data = json.loads((EVIDENCE / f"ha-mcp-{version}.json").read_text())
    review = json.loads((EVIDENCE / f"ha-mcp-{version}-contract-review.json").read_text())
    by = {t["name"]: t for t in data["tools"]}
    data["tools"] = [by[n] for n in review["runtime_catalog"]["runtime_tool_order"]]
    return data


def _results(version: str) -> dict:
    return json.loads((ROOT / f"tests/fixtures/ha_mcp_{version.replace('.', '_')}/read-results.json").read_text())


class GatewayTransport(_GatewayTransport):
    """8.5.0 catalog; every read returns one committed 8.5.0 result."""

    def __init__(self, tools):
        super().__init__(tools, version="8.5.0")
        self.sent = []
        self.result = _results("8.5.0")["standalone-call-05.json"]["result"]

    async def execute_read(self, tool, arguments, **kwargs):
        exchange = await super().execute_read(tool, arguments, **kwargs)
        self.sent.append((tool, deepcopy(arguments)))
        return replace(exchange, call_result=deepcopy(self.result))


class Transport860(_GatewayTransport):
    """Committed 8.6.0 standalone results per tool, an override, or cancellation."""

    def __init__(self, tools, version="8.6.0"):
        super().__init__(tools, version=version)
        self.sent = []
        self.records = {r["upstream_name"]: r for r in _results("8.6.0")["standalone"]}
        self.override = None
        self.cancel = False

    async def execute_read(self, tool, arguments, **kwargs):
        exchange = await super().execute_read(tool, arguments, **kwargs)
        self.sent.append((tool, deepcopy(arguments)))
        if self.cancel:
            raise asyncio.CancelledError()
        raw = self.override if self.override is not None else self.records[tool]["result"]
        return replace(exchange, call_result=deepcopy(raw))


async def gateway_860(tools=None, version="8.6.0", protocol="2025-03-26"):
    """An initialized read gateway over ``Transport860``; returns (gateway, transport)."""

    transport = Transport860(capture("8.6.0")["tools"] if tools is None else tools, version)
    transport.catalog = replace(transport.catalog, protocol_version=protocol)
    gateway = UpstreamReadGateway()
    gateway.configure(replace(_settings("synthetic-no-key"), ha_mcp_release_registry_enabled=False),
                      transport=transport)
    await gateway.initialize(FastMCP("synthetic-860"))
    return gateway, transport


RECEIPTS_920 = {
    "ha_config_get_scene": "r05-scene",
    "ha_eval_template": "r10-template",
    "ha_get_blueprint": "r12-blueprints",
    "ha_get_skill_guide": "r18-skill-guide",
    "ha_get_state": "r19-state",
    "ha_manage_blueprints": "r12-blueprints",
    "ha_search": "r25-search",
}


def arguments_920(tool: str) -> dict:
    """The public arguments of the committed 9.2.0 receipt for ``tool``."""

    return receipt(RECEIPTS_920[tool])[0]


class ReceiptTransport920(_GatewayTransport):
    """9.2.0 catalog; each read returns its committed receipt, an override, or cancels."""

    def __init__(self, tools, version="9.2.0"):
        super().__init__(tools, version=version)
        self.sent = []
        self.override = None
        self.cancel = False

    async def execute_read(self, tool, arguments, **kwargs):
        exchange = await super().execute_read(tool, arguments, **kwargs)
        self.sent.append((tool, deepcopy(arguments)))
        if self.cancel:
            raise asyncio.CancelledError()
        raw = self.override if self.override is not None else receipt(RECEIPTS_920[tool])[1]
        return replace(exchange, call_result=deepcopy(raw))


async def gateway_920(tools=None, version="9.2.0", protocol=None, core_runtime=None, url=None):
    """An initialized compiled 9.2.0 read gateway; returns (gateway, transport).

    ``url`` replaces the synthetic upstream URL. The default URL's host is
    ``upstream``, so its derived redaction also masks Engineering's own
    ``upstream_*`` result keys; a read whose completeness is under test passes
    a URL whose parts occur in no result.
    """

    transport = ReceiptTransport920(capture("9.2.0")["tools"] if tools is None else tools, version)
    if protocol is not None:
        transport.catalog = replace(transport.catalog, protocol_version=protocol)
    gateway = UpstreamReadGateway()
    extra = {} if core_runtime is None else {"core_runtime": core_runtime}
    settings = replace(_settings("synthetic-no-key"), ha_mcp_release_registry_enabled=False)
    if url is not None:
        settings = replace(settings, upstream_dashboard_mcp_url=url)
    gateway.configure(settings, transport=transport, **extra)
    await gateway.initialize(FastMCP("synthetic-920"))
    return gateway, transport


async def call(gateway, name, args, request_id="synthetic-860"):
    """Run one registered public read; returns (decoded result, telemetry)."""

    telemetry, token = begin_request(request_id)
    try:
        result = json.loads(await gateway._registered_tool_registry.snapshot()[name].run(args))
        return result, telemetry
    finally:
        end_request(token)
