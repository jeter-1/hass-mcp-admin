"""HAMCP-437: dispatch-settlement guards re-pinned to ha-mcp 9.2.0.

Ported from the retired ``test_upstream_8_5_compatibility`` and
``test_upstream_8_6_compatibility`` modules. After a read is dispatched, a
timeout, a cancellation or an in-flight loss of authority settles the route
exactly once: no retry, no fallback, and no authority left held. The
dashboard-guide cancellation guard is in ``test_hamcp177_dashboard_write_admission``
(``test_cancellation_propagates_unchanged``). Successful
9.2.0 reads use the committed HAMCP-371 receipts; no endpoint is contacted.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from mcp.server.fastmcp import FastMCP  # noqa: E402
from ha_mcp_engineering.clients.mcp import DashboardTransportError  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from support import internal_search_page  # noqa: E402
from support.ha_mcp_920_receipts import receipt  # noqa: E402
from support.ha_mcp_captures import RECEIPTS_920, ReceiptTransport920, arguments_920, capture  # noqa: E402
import test_ha_mcp_production_readmission as readmission  # noqa: E402
from test_ha_mcp_production_readmission import (  # noqa: E402
    UpstreamSurface,
    _settings,
    _signed_entry_for,
)

VERSION = "9.2.0"
_arguments = arguments_920
_ReceiptTransport = ReceiptTransport920
# HAMCP-501 W6 (G1): the delegated ha_search is internal-only; its settlement
# guards run on the one route a native search sub-call page reaches.
PAGE = internal_search_page.PAGE_ARGUMENTS


async def _call(gateway, name, args, request_id="synthetic-920"):
    if internal_search_page.is_internal_only(name):
        return await internal_search_page.execute_page(
            gateway, name, args, caller=request_id, with_telemetry=True)
    telemetry, token = begin_request(request_id)
    try:
        result = json.loads(await gateway._registered_tool_registry.snapshot()[name].run(args))
        return result, telemetry
    finally:
        end_request(token)


class ReadSettlement920Tests(unittest.IsolatedAsyncioTestCase):
    """Compiled 9.2.0 read gateway without a signed registry."""

    async def asyncSetUp(self):
        self.transport = _ReceiptTransport(capture(VERSION)["tools"])
        self.gateway = UpstreamReadGateway()
        self.gateway.configure(
            internal_search_page.page_settings(
                replace(_settings("synthetic-no-key"), ha_mcp_release_registry_enabled=False)),
            transport=self.transport,
        )
        await self.gateway.initialize(FastMCP("synthetic-920"))

    async def test_cancellation_settles_route_and_next_read_still_works(self):
        g, t = self.gateway, self.transport
        t.cancel = True
        with self.assertRaises(asyncio.CancelledError):
            await _call(g, "ha_get_state", _arguments("ha_get_state"))
        t.cancel = False
        result, telemetry = await _call(g, "ha_get_state", _arguments("ha_get_state"))
        self.assertTrue(result["success"], result)
        self.assertFalse(result["metadata"]["fallback_occurred"])
        self.assertEqual(telemetry.upstream_active_requests, 0)
        self.assertEqual(len(t.sent), 2)

    async def test_search_public_arguments_never_reach_the_internal_route(self):
        # HAMCP-501 W6 (G1): not published, and the recorded public arguments
        # are not a sub-call page: refused before dispatch, nothing held.
        g, t = self.gateway, self.transport
        self.assertNotIn("ha_search", g._registered_tool_registry.snapshot())
        self.assertIn("ha_search", g._internal)
        result, telemetry = await _call(g, "ha_search", _arguments("ha_search"))
        self.assertEqual(result["details"]["failure_category"], "prohibited_delegation", result)
        self.assertEqual(t.calls, 0)
        self.assertEqual(telemetry.upstream_active_requests, 0)

    async def test_search_failure_cancel_and_timeout_settle_without_retry(self):
        g, t = self.gateway, self.transport
        args = PAGE
        for raw in (
            {"isError": True, "content": [{"type": "text", "text": "synthetic failure"}]},
            {"isError": False, "content": []},
            {"isError": False, "structuredContent": {"success": True, "huge": "x" * 100000}},
        ):
            with self.subTest(raw=str(raw)[:40]):
                t.override = raw
                result, telemetry = await _call(g, "ha_search", args)
                self.assertFalse(result["success"], result)
                self.assertFalse(result["metadata"]["fallback_occurred"])
                self.assertNotEqual(result["metadata"].get("completeness"), "complete")
                self.assertLessEqual(len(json.dumps(result).encode()), 60000)
                self.assertEqual(telemetry.upstream_active_requests, 0)
                self.assertEqual(telemetry.upstream_request_count, 1)
        t.cancel = True
        with self.assertRaises(asyncio.CancelledError):
            await _call(g, "ha_search", args)
        t.cancel = False
        original = t.execute_read

        async def timeout(*a, **kwargs):
            await original(*a, **kwargs)
            raise DashboardTransportError("timeout")

        t.execute_read = timeout
        result, telemetry = await _call(g, "ha_search", args)
        self.assertEqual(result["details"]["failure_category"], "timeout", result)
        self.assertEqual(telemetry.upstream_active_requests, 0)
        self.assertEqual(t.calls, 5)
        t.execute_read = original
        t.override = None
        result, telemetry = await _call(g, "ha_search", args)
        self.assertTrue(result["success"], result)
        self.assertEqual(telemetry.upstream_active_requests, 0)
        self.assertEqual(t.calls, 6)


class SignedSettlement920Tests(unittest.IsolatedAsyncioTestCase):
    """Signed-registry gateway admitted by the exact compiled 9.2.0 release."""

    setUp = readmission.HaMcp920SignedReuseTests.setUp
    _raw = readmission.HaMcp920SignedReuseTests._raw
    _registry = readmission.HaMcp920SignedReuseTests._registry
    _initialize = readmission.HaMcp920SignedReuseTests._initialize

    async def asyncSetUp(self):
        entry = _signed_entry_for(self.release, version=VERSION)
        # HAMCP-501 W6: a page whose content holds a part of the configured
        # endpoint fails closed (W4 D2), and the default test endpoint's host
        # occurs in Row 25's own key names; serve a non-colliding endpoint.
        with mock.patch.object(readmission, "_settings",
                               lambda key: internal_search_page.page_settings(_settings(key))):
            self.gateway, self.transport, snapshot = await self._initialize(
                raw=self._raw(entry=entry), version=VERSION
            )
        self.assertEqual(snapshot["admission_status"], "admitted_exact")
        self.original = self.transport.execute_read

    def _assert_settled(self):
        health = self.gateway.health_snapshot()["automatic_readmission"]
        self.assertEqual(health["issued_lease_count"], 0)
        self.assertEqual(health["active_commit_count"], 0)

    def _tool(self, name):
        return self.gateway._registered_tool_registry.snapshot()[name]

    async def _page(self, route=None):
        # HAMCP-501 W6 (G1): the internal-only ha_search route, as one page.
        return await internal_search_page.execute_page(self.gateway, route=route)

    async def test_timeout_after_dispatch_releases_authority_without_retry(self):
        async def timeout(*args, **kwargs):
            await self.original(*args, **kwargs)
            raise DashboardTransportError("timeout")

        self.transport.execute_read = timeout
        result = json.loads(await self._tool("ha_get_blueprint").run({}))
        self.assertEqual(result["details"]["failure_category"], "timeout", result)
        self.assertTrue(result["metadata"]["upstream_dispatch_occurred"])
        self.assertFalse(result["metadata"]["fallback_occurred"])
        self.assertEqual(self.transport.calls, 1)
        self._assert_settled()

    async def test_cancel_after_dispatch_releases_authority_without_retry(self):
        started = asyncio.Event()

        async def pending(*args, **kwargs):
            await self.original(*args, **kwargs)
            started.set()
            await asyncio.Event().wait()

        self.transport.execute_read = pending
        task = asyncio.create_task(self._tool("ha_get_blueprint").run({}))
        try:
            await asyncio.wait_for(started.wait(), 2)
            self.assertEqual(
                self.gateway.health_snapshot()["automatic_readmission"]["active_commit_count"], 1
            )
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.transport.calls, 1)
        self._assert_settled()

    async def test_search_authority_loss_before_commit_refuses_without_retry(self):
        g = self.gateway

        async def retire(*args, **kwargs):
            validator = kwargs["catalog_validator"]

            def retire_after_validation(catalog):
                validator(catalog)
                g._readmission_coordinator.retire_surface_authority(UpstreamSurface.HA_MCP)

            kwargs["catalog_validator"] = retire_after_validation
            return await self.original(*args, **kwargs)

        self.transport.execute_read = retire
        result = await self._page()
        self.assertFalse(result["success"], result)
        self.assertEqual(self.transport.calls, 0)
        self._assert_settled()

    async def test_committed_read_settles_when_authority_is_retired_in_flight(self):
        g = self.gateway
        self.transport.call_result = receipt(RECEIPTS_920["ha_search"])[1]

        async def retire(*args, **kwargs):
            result = await self.original(*args, **kwargs)
            self.assertEqual(g.health_snapshot()["automatic_readmission"]["active_commit_count"], 1)
            g._readmission_coordinator.retire_surface_authority(UpstreamSurface.HA_MCP)
            return result

        self.transport.execute_read = retire
        with g._lock:
            route = g._internal["ha_search"]
        result = await self._page(route)
        # Dispatch was already committed: settle it rather than retry or
        # report a false zero-I/O refusal.
        self.assertTrue(result["success"], result)
        self.assertEqual(self.transport.calls, 1)
        # The pinned route lost its authority: refused, nothing dispatched.
        self.assertFalse((await self._page(route))["success"])
        self.assertEqual(self.transport.calls, 1)
        self._assert_settled()


if __name__ == "__main__":
    unittest.main()
