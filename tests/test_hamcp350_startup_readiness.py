"""HAMCP-350: bounded startup wait for the first ha-mcp reconcile.

Real MCP JSON-RPC runs through ``AuthenticatedMcpGateway`` into a fresh
FastMCP streamable-http app carrying every registered Engineering tool. The
ha-mcp upstream is a fake transport that fails discovery with the categories
the real client raises for a refused connection (``connection_failed``) and a
404 (``endpoint_rejected``) until the test lets it recover. No socket, live
Home Assistant or deployed endpoint is used.
"""

import asyncio
import dataclasses
from contextlib import AsyncExitStack, asynccontextmanager
import json
import logging
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from mcp.server.fastmcp import FastMCP

from tests.support.process_isolation import restore_after_class

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering import application  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.capabilities import (  # noqa: E402
    DELEGATED_TOOLS_UNAVAILABLE_NOTICE,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.health import HEALTH  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402
from ha_mcp_engineering.tools import compatibility, registered_tools  # noqa: E402
from ha_mcp_engineering.tools.registry import get_registered_server  # noqa: E402
from tests.test_readonly_upstream_gateway import (  # noqa: E402
    SequencedDiscoveryTransport,
    catalog_tool,
    policy,
    policy_entry,
)


SECRET = "synthetic-hamcp350-access-secret"
DELEGATED = "ha_get_state"
NATIVE_CALLS = (
    ("server_info", {"check_ha": False}),
    ("get_server_health", {"check_ha": False}),
    ("list_areas", {}),
)
MCP_HEADERS = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
}


def _settings(directory: str) -> Settings:
    return Settings(
        ha_url="http://synthetic-ha.invalid",
        ha_token="synthetic-token",
        access_secret=SECRET,
        port=8100,
        audit_path=str(Path(directory) / "audit.jsonl"),
        audit_enabled=False,
        rate_limit_per_minute=120,
        rate_limit_burst=1_000,
        destructive_services=frozenset(),
        governance_path=str(Path(directory) / "governance"),
        prewarm_enabled=False,
        upstream_dashboard_mcp_url=(
            "http://upstream:9583/synthetic-upstream-secret/mcp"
        ),
    )


def _parse_sse(response) -> dict:
    line = next(
        row
        for row in response.text.replace("\r", "").splitlines()
        if row.startswith("data: ")
    )
    return json.loads(line.removeprefix("data: "))


class StartupReadinessDeadlineTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        restore_after_class(cls)

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.server = FastMCP(
            "hamcp350-startup-readiness-test",
            host="127.0.0.1",
            port=8100,
            streamable_http_path="/mcp",
            stateless_http=True,
        )
        for tool in registered_tools(get_registered_server()).values():
            self.server.tool(
                name=tool.name,
                description=tool.description,
                annotations=tool.annotations,
            )(tool.fn)
        self.native_names = set(registered_tools(self.server))
        self.settings = _settings(self.directory.name)
        self.gateway = AuthenticatedMcpGateway(
            self.server.streamable_http_app(),
            self.settings,
            AuditLogger(self.settings.audit_path, SECRET, enabled=False),
            require_initial_catalog_reconciliation=True,
        )
        self.request_id = 0

    @asynccontextmanager
    async def serving(self):
        # The SDK session manager's task group must be entered and left in the
        # same task, so each test body owns the lifespan.
        async with AsyncExitStack() as stack:
            lifespan = self.gateway.app.router.lifespan_context(
                self.gateway.app
            )
            await stack.enter_async_context(lifespan)
            self.client = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.gateway),
                    base_url="http://127.0.0.1:8100",
                )
            )
            yield

    def configure_upstream(self, failure_category: str):
        self.transport = SequencedDiscoveryTransport(
            [catalog_tool(DELEGATED)], [failure_category] * 3
        )
        self.upstream = UpstreamReadGateway()
        self.upstream.configure(
            self.settings,
            transport=self.transport,
            policy=policy(policy_entry(DELEGATED)),
            admission_validator=lambda _catalog: None,
        )
        self.recover = asyncio.Event()
        self.retry_delays = []

        async def controlled_sleep(delay):
            self.retry_delays.append(delay)
            await self.recover.wait()

        original = self.upstream.reconcile_until_initialized

        async def reconcile(server):
            return await original(
                server, retry_delays=(1.0, 2.0), sleep=controlled_sleep
            )

        self.supervised = []

        async def supervise(server, *, initial_snapshot=None):
            self.supervised.append(initial_snapshot)

        # Start from an unconfigured health registry: the process-global HEALTH
        # may still hold an earlier class's governance and operation runtimes
        # whose storage directories that class already removed, which made
        # these tests depend on which test class ran before them.
        for field in dataclasses.fields(HEALTH):
            self.enterContext(patch.object(HEALTH, field.name, field.default))
        self.enterContext(
            patch.object(self.upstream, "reconcile_until_initialized", reconcile)
        )
        self.enterContext(
            patch.object(self.upstream, "supervise_reconciliation", supervise)
        )
        self.enterContext(
            patch.object(application, "UPSTREAM_READ_GATEWAY", self.upstream)
        )
        self.enterContext(
            patch.object(
                application, "get_registered_server", return_value=self.server
            )
        )
        self.enterContext(
            patch.object(HEALTH, "upstream_read_gateway", self.upstream)
        )
        self.ws = AsyncMock(
            return_value=[{"area_id": "kitchen", "name": "Kitchen"}]
        )
        self.enterContext(
            patch.object(compatibility.WEBSOCKET_CLIENT, "command", self.ws)
        )

    async def post(self, method, params=None):
        self.request_id += 1
        body = {"jsonrpc": "2.0", "id": self.request_id, "method": method}
        if params is not None:
            body["params"] = params
        return await self.client.post(
            f"/{SECRET}/mcp", json=body, headers=MCP_HEADERS
        )

    async def initialize(self):
        return await self.post(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "hamcp350-test", "version": "1"},
            },
        )

    async def call(self, name, arguments):
        response = await self.post(
            "tools/call", {"name": name, "arguments": arguments}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return _parse_sse(response)

    async def listed_names(self):
        response = await self.post("tools/list", {})
        self.assertEqual(response.status_code, 200, response.text)
        return {tool["name"] for tool in _parse_sse(response)["result"]["tools"]}

    async def ready(self):
        response = await self.client.get("/ready")
        return response.status_code, response.json()

    async def wait_for(self, predicate):
        for _ in range(2_000):
            if predicate():
                return
            await asyncio.sleep(0.005)
        self.fail("condition not reached")

    async def run_outage_then_recovery(self, failure_category):
        self.configure_upstream(failure_category)
        async with self.serving():
            await self._outage_then_recovery()

    async def _outage_then_recovery(self):
        listener_started = asyncio.Event()
        supervisor = asyncio.create_task(
            application._supervise_upstream_reconciliation(
                self.gateway,
                listener_started=listener_started,
                deadline_seconds=0.05,
            )
        )
        self.addCleanup(supervisor.cancel)

        # Before the listener starts the clock does not run; the barrier holds.
        await asyncio.sleep(0.1)
        self.assertEqual((await self.initialize()).status_code, 503)
        self.assertFalse(
            self.gateway.catalog_readiness_state()["accepting_mcp_requests"]
        )

        listener_started.set()
        await self.wait_for(
            lambda: self.gateway.catalog_readiness_state()[
                "accepting_mcp_requests"
            ]
        )
        self.assertFalse(supervisor.done())
        self.assertGreaterEqual(self.transport.discovery_calls, 1)
        self.assertEqual(self.retry_delays, [1.0])

        # Engineering-native tools are served while ha-mcp is unavailable.
        initialized = await self.initialize()
        self.assertEqual(initialized.status_code, 200, initialized.text)
        self.assertEqual(
            _parse_sse(initialized)["result"]["serverInfo"]["name"],
            self.server.name,
        )
        listed = await self.listed_names()
        self.assertEqual(listed, self.native_names)
        self.assertNotIn(DELEGATED, listed)
        payloads = {}
        for name, arguments in NATIVE_CALLS:
            result = (await self.call(name, arguments))["result"]
            self.assertFalse(result.get("isError"), result)
            payloads[name] = result["content"][0]["text"]
        self.assertIn("Kitchen", payloads["list_areas"])
        for name in ("server_info", "get_server_health"):
            rendered = json.loads(payloads[name])
            self.assertTrue(rendered["success"], rendered)
            status = rendered["data"]["delegated_tools"]
            self.assertEqual(
                status,
                {
                    "configured": True,
                    "admitted": False,
                    "admitted_tool_count": 0,
                    "reconciliation_status": "waiting",
                    "notice": DELEGATED_TOOLS_UNAVAILABLE_NOTICE,
                },
                name,
            )
            self.assertIn("new MCP client session", status["notice"])
            self.assertNotIn("synthetic-upstream-secret", payloads[name])

        # A delegated tool is refused and never reaches the upstream.
        refused = (await self.call(DELEGATED, {"entity_id": "sun.sun"}))
        self.assertTrue(
            refused.get("error") or refused["result"].get("isError"), refused
        )
        self.assertEqual(self.transport.attempts, [])
        self.assertEqual(self.transport.calls, [])

        # /ready stays pending until the first reconcile returns.
        status, body = await self.ready()
        self.assertEqual(status, 503)
        self.assertEqual(body["status"], "initial_reconciliation_pending")
        self.assertTrue(body["accepting_mcp_requests"])
        self.assertTrue(body["initial_reconciliation_deadline_elapsed"])

        # ha-mcp recovers; the same reconcile admits the delegated tool.
        self.transport.outcomes.clear()
        self.recover.set()
        await asyncio.wait_for(supervisor, 10)
        self.assertEqual(len(self.supervised), 1)
        self.assertEqual(
            self.supervised[0]["reconciliation_status"], "admitted"
        )
        status, body = await self.ready()
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "ready")
        self.assertIn(DELEGATED, await self.listed_names())
        info = json.loads(
            (await self.call("server_info", {"check_ha": False}))["result"][
                "content"
            ][0]["text"]
        )
        self.assertEqual(
            info["data"]["delegated_tools"],
            {
                "configured": True,
                "admitted": True,
                "admitted_tool_count": 1,
                "reconciliation_status": "admitted",
                "notice": None,
            },
        )
        self.assertEqual(self.transport.attempts, [])

    async def test_connection_refused_serves_natives_then_admits(self):
        await self.run_outage_then_recovery("connection_failed")

    async def test_endpoint_404_serves_natives_then_admits(self):
        await self.run_outage_then_recovery("endpoint_rejected")

    async def test_prompt_reconcile_keeps_barrier_and_never_marks_deadline(self):
        self.configure_upstream("connection_failed")
        self.transport.outcomes.clear()
        listener_started = asyncio.Event()
        listener_started.set()
        await application._supervise_upstream_reconciliation(
            self.gateway, listener_started=listener_started, deadline_seconds=5
        )
        state = self.gateway.catalog_readiness_state()
        self.assertTrue(state["ready"])
        self.assertFalse(state["initial_reconciliation_deadline_elapsed"])
        async with self.serving():
            self.assertEqual((await self.initialize()).status_code, 200)
            self.assertIn(DELEGATED, await self.listed_names())

    async def test_secret_path_and_rate_limit_still_apply_after_deadline(self):
        self.gateway.mark_initial_catalog_deadline_elapsed()
        async with self.serving():
            response = await self.client.post(
                "/not-the-secret/mcp", json={}, headers=MCP_HEADERS
            )
            self.assertEqual(response.status_code, 404)
            self.assertEqual((await self.initialize()).status_code, 200)
            self.gateway.global_bucket.tokens = 0
            self.assertEqual((await self.initialize()).status_code, 429)


class SupervisorDeadlineOrderTests(unittest.IsolatedAsyncioTestCase):
    """Order of the readiness marks around the bounded wait."""

    class Gateway:
        initial_catalog_reconciliation_required = True

        def __init__(self, order):
            self.order = order

        def mark_initial_catalog_deadline_elapsed(self):
            self.order.append("deadline")

        def mark_initial_catalog_reconciled(self):
            self.order.append("ready")

    async def run_supervisor(self, reconcile, *, deadline=0.01, started=True):
        order = []
        supervised = []

        async def wrapped(server):
            return await reconcile(order)

        async def supervise(_server, *, initial_snapshot=None):
            order.append("supervise")
            supervised.append(initial_snapshot)

        listener_started = asyncio.Event()
        if started:
            listener_started.set()
        self.enterContext(patch.object(
            application.UPSTREAM_READ_GATEWAY,
            "reconcile_until_initialized",
            side_effect=wrapped,
        ))
        self.enterContext(patch.object(
            application.UPSTREAM_READ_GATEWAY,
            "supervise_reconciliation",
            side_effect=supervise,
        ))
        self.enterContext(patch.object(application, "get_registered_server"))
        task = asyncio.create_task(
            application._supervise_upstream_reconciliation(
                self.Gateway(order),
                listener_started=listener_started,
                deadline_seconds=deadline,
            )
        )
        self.addCleanup(task.cancel)
        return order, supervised, task

    async def test_slow_reconcile_marks_deadline_then_ready(self):
        release = asyncio.Event()

        async def reconcile(order):
            order.append("reconcile")
            await release.wait()
            return {"configured": True, "reconciliation_status": "admitted"}

        with self.assertLogs("ha_mcp_engineering.application", "INFO") as logs:
            order, supervised, task = await self.run_supervisor(reconcile)
            for _ in range(200):
                if "deadline" in order:
                    break
                await asyncio.sleep(0.005)
            self.assertEqual(order, ["reconcile", "deadline"])
            release.set()
            await asyncio.wait_for(task, 5)
        self.assertEqual(order, ["reconcile", "deadline", "ready", "supervise"])
        self.assertEqual(supervised[0]["reconciliation_status"], "admitted")
        events = [record.event for record in logs.records]
        self.assertIn("initial_reconciliation_deadline_elapsed", events)
        phase = [r for r in logs.records if r.event == "startup_phase_timing"]
        self.assertEqual(len(phase), 1)
        self.assertEqual(phase[0].safe_context["phase"], "first_reconcile")
        self.assertEqual(phase[0].safe_context["outcome"], "completed")
        self.assertTrue(phase[0].safe_context["readiness_deadline_elapsed"])
        self.assertEqual(
            phase[0].safe_context["reconciliation_status"], "admitted"
        )

    async def test_clock_waits_for_listener_start(self):
        async def reconcile(order):
            order.append("reconcile")
            await asyncio.Event().wait()

        order, _, task = await self.run_supervisor(reconcile, started=False)
        await asyncio.sleep(0.1)
        self.assertEqual(order, ["reconcile"])
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_failed_reconcile_after_deadline_never_marks_ready(self):
        async def reconcile(order):
            order.append("reconcile")
            await asyncio.sleep(0.05)
            raise RuntimeError("synthetic reconciliation failure")

        order, supervised, task = await self.run_supervisor(reconcile)
        with self.assertRaisesRegex(RuntimeError, "synthetic reconciliation"):
            await asyncio.wait_for(task, 5)
        self.assertEqual(order, ["reconcile", "deadline"])
        self.assertEqual(supervised, [])

    async def test_cancellation_cancels_the_pending_reconcile(self):
        cancelled = asyncio.Event()

        async def reconcile(order):
            order.append("reconcile")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        with self.assertLogs("ha_mcp_engineering.application", "INFO") as logs:
            order, _, task = await self.run_supervisor(reconcile)
            for _ in range(200):
                if "deadline" in order:
                    break
                await asyncio.sleep(0.005)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(cancelled.is_set())
        self.assertNotIn("ready", order)
        phase = [r for r in logs.records if r.event == "startup_phase_timing"]
        self.assertEqual(phase[0].safe_context["outcome"], "cancelled")
        self.assertEqual(phase[0].levelno, logging.WARNING)


if __name__ == "__main__":
    unittest.main()
