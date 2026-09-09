"""RC3-PC-1/2: useful admission and paced recovery through production boundaries.

Registry inputs are synthetic variations of the existing Core 2026.9 fixture,
not newly captured household or historical persisted records. Core's reviewed
2026.9.0/2026.9.1 serializer preserves name/name_by_user as string-or-null display
values; the retained source diagnostic demonstrates the original failures.
Only network responses and the supervisor's retry clock are replaced here.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import aiohttp
from mcp.server.fastmcp import FastMCP

from tests import test_ha_core_2026_9_integration as core_tests
from tests import test_dependency_build_authority as dependency_tests
from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities
from ha_mcp_engineering.clients.rest import HomeAssistantRestClient
from ha_mcp_engineering.clients.websocket import HomeAssistantWebSocketClient
from ha_mcp_engineering.dependency.runtime import DependencyAnalysisRuntime
from ha_mcp_engineering.errors import HomeAssistantUnavailableError
from ha_mcp_engineering.ha_core_readmission import CoreRuntime
from ha_mcp_engineering.ha_core_readmission.device_registry import MAX_DEVICE_RECORD_BYTES
from ha_mcp_engineering.ha_core_readmission.source import AiohttpCoreSnapshotSource
from ha_mcp_engineering.mcp_sdk_compatibility import McpSdkToolRegistry
from ha_mcp_engineering.tools import (
    ENGINEERING_STATIC_TOOL_NAMES,
    get_registered_server,
    registered_tools,
)


DEVICE_CAPABILITIES = {
    "core.direct_device_registry_read",
    "core.delegated_device_effective_area",
    "core.dependency_helper_planning",
}


class _CoreNetwork:
    """Synthetic sessions below the real REST/WS probes and lifecycle watcher."""

    def __init__(self, version="2026.9.1"):
        self.version = version
        self.records = core_tests._fixture(core_tests.DEVICE_FIXTURE)["devices"]
        self.mode = "ok"
        self.sessions = []
        self.sockets = []
        self.monitors = []
        self.blocked = asyncio.Event()
        self.resume = asyncio.Event()

    def session(self, *, timeout, **_kwargs):
        network = self
        is_monitor = timeout.total is None

        class Response(core_tests._RawJsonResponse):
            async def __aenter__(self):
                if network.mode == "http":
                    self.status = 503
                elif network.mode == "connection":
                    raise aiohttp.ClientConnectionError("synthetic connection loss")
                elif network.mode == "timeout":
                    raise asyncio.TimeoutError
                elif network.mode == "blocked":
                    network.blocked.set()
                    await network.resume.wait()
                return self

        class LifecycleSocket:
            def __init__(self):
                self.sent = []
                self.receives = 0
                self.exits = 0
                self.disconnect = asyncio.Event()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                self.exits += 1

            async def send_json(self, value):
                self.sent.append(value)

            async def receive_json(self):
                self.receives += 1
                if self.receives == 1:
                    return {"type": "auth_required"}
                if network.mode == "monitor_auth":
                    return {"type": "auth_invalid"}
                if network.mode == "monitor_timeout":
                    raise asyncio.TimeoutError
                if network.mode == "monitor_blocked":
                    network.blocked.set()
                    await network.resume.wait()
                return {"type": "auth_ok", "ha_version": network.version}

            async def receive(self):
                if network.mode != "monitor_close":
                    await self.disconnect.wait()
                return {"type": "synthetic_connection_closed"}

        class Session:
            def __init__(self):
                self.exits = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                self.exits += 1

            def get(self, url, **_kwargs):
                if url.endswith("/config"):
                    return Response(json.dumps({"version": network.version}).encode())
                if url.endswith(("/states", "/services")):
                    return core_tests._RawJsonResponse(b"[]")
                raise AssertionError("unexpected synthetic REST probe")

            def post(self, url, **_kwargs):
                assert url.endswith("/config/core/check_config")
                return core_tests._RawJsonResponse(
                    b'{"result":"valid","errors":null,"warnings":null}'
                )

            def ws_connect(self, *_args, **_kwargs):
                if is_monitor:
                    socket = LifecycleSocket()
                    network.monitors.append(socket)
                else:
                    responses = core_tests._complete_probe_websocket(network.version).responses
                    if network.mode == "auth":
                        responses[1] = {"type": "auth_invalid"}
                    for response in responses:
                        if response.get("id") == 6:
                            response["result"] = network.records
                    # Preserve the raw JSON value boundary, including display text.
                    responses = json.loads(json.dumps(responses, allow_nan=False))
                    socket = core_tests._TrackingSequenceWebSocket(responses)
                network.sockets.append(socket)
                return socket

        session = Session()
        self.sessions.append(session)
        return session


class _RetryClock:
    """Advance a requested delay explicitly without real-time sleeps."""

    def __init__(self):
        self.waits = asyncio.Queue()
        self.delays = []

    async def sleep(self, delay):
        self.delays.append(delay)
        ready = asyncio.get_running_loop().create_future()
        self.waits.put_nowait(ready)
        await ready

    async def next_delay(self):
        return await asyncio.wait_for(self.waits.get(), 2)


class PostCoreRegressions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.network = _CoreNetwork()
        self.settings = core_tests.settings()
        self.core = CoreRuntime()
        self.core.configure(self.settings, source=AiohttpCoreSnapshotSource(self.settings))
        self.session_patch = patch(
            "ha_mcp_engineering.ha_core_readmission.source.aiohttp.ClientSession",
            side_effect=self.network.session,
        )
        self.session_patch.start()
        self.addCleanup(self.session_patch.stop)
        self.supervisor = None
        self.clock = _RetryClock()
        self.notifications = []
        self.core.register_reconciliation_listener(
            lambda: self.notifications.append(self.core.health_snapshot()["current_generation"])
        )

    async def asyncTearDown(self):
        if self.supervisor is not None and not self.supervisor.done():
            self.supervisor.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await self.supervisor
        monitor = self.core._connection_monitor_task
        if monitor is not None:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        for session in self.network.sessions:
            self.assertEqual(session.exits, 1)
        for socket in self.network.sockets:
            count = socket.exits if hasattr(socket, "exits") else socket.exit_calls
            self.assertEqual(count, 1)
        self.assert_settled()
        replace_dynamic_upstream_capabilities((), {})

    def assert_settled(self):
        health = self.core.health_snapshot()
        for field in ("issued_lease_count", "active_commit_count",
                      "capacity_exhaustion_count", "fallback_count"):
            self.assertEqual(health[field], 0, field)

    def assert_authority_round_trip(self, capability="core.basic_rest_read"):
        authority = self.core.acquire((capability,))
        self.assertIsNotNone(authority)
        commits = self.core.consume(authority)
        self.assertIsNotNone(commits)
        self.assertTrue(self.core.revalidate(authority, commits))
        self.assertTrue(self.core.finish(commits))
        self.assertFalse(self.core.finish(commits))
        self.assertFalse(self.core.revalidate(authority, commits))
        self.assertIsNone(self.core.consume(authority))
        self.assert_settled()

    async def until(self, predicate):
        async with asyncio.timeout(2):
            while not predicate():
                await asyncio.sleep(0)

    def start_supervisor(self):
        self.core.request_reconciliation()
        self.supervisor = asyncio.create_task(
            self.core.supervise(interval_seconds=300, sleep=self.clock.sleep)
        )

    async def test_display_values_preserve_authority_on_both_reviewed_versions(self):
        original = deepcopy(self.network.records)
        for version in ("2026.9.0", "2026.9.1"):
            for field in ("name", "name_by_user"):
                for value in (None, "Synthetic display", "", "N" * 129,
                              "界" * 200, "line one\nline two\t\u007f"):
                    with self.subTest(version=version, field=field, value=value):
                        self.network.version = version
                        self.network.records = deepcopy(original)
                        for record in self.network.records:
                            record[field] = value
                        before = deepcopy(self.network.records)
                        health = await self.core.reconcile_once("synthetic_display")
                        self.assertEqual(health["compatible_count"], 17)
                        self.assertEqual(self.network.records, before)
                        for capability in DEVICE_CAPABILITIES:
                            self.assert_authority_round_trip(capability)

    async def test_wrong_display_types_still_withhold_only_dependent_capabilities(self):
        original = deepcopy(self.network.records)
        for field in ("name", "name_by_user"):
            for value in (False, 0, 1.5, [], {}):
                with self.subTest(field=field, value=value):
                    self.network.records = deepcopy(original)
                    self.network.records[1][field] = value
                    health = await self.core.reconcile_once("synthetic_wrong_type")
                    self.assertEqual(health["compatible_count"], 14)
                    missing = {
                        p["capability_id"] for p in health["authority_profiles"]
                        if p["reason_code"] == "capability_evidence_missing"
                    }
                    self.assertEqual(missing, DEVICE_CAPABILITIES)
                    self.assert_authority_round_trip()

    async def test_display_correction_preserves_identity_parent_and_size_refusals(self):
        original = deepcopy(self.network.records)
        for field, value in (("id", ""), ("id", "I" * 129),
                             ("parent_device_id", "missing-parent"),
                             ("created_at", -1), ("modified_at", False),
                             ("name", "N" * MAX_DEVICE_RECORD_BYTES)):
            with self.subTest(field=field):
                self.network.records = deepcopy(original)
                self.network.records[1]["name_by_user"] = ""
                self.network.records[1][field] = value
                health = await self.core.reconcile_once("synthetic_invalid_registry")
                self.assertEqual(health["compatible_count"], 14)
                for capability in DEVICE_CAPABILITIES:
                    self.assertIsNone(self.core.acquire((capability,)))
                self.assert_authority_round_trip()

    async def test_display_text_does_not_admit_unreviewed_core_version(self):
        self.network.version = "2026.9.2"
        for record in self.network.records:
            record["name"] = ""
        await self.core.reconcile_once("synthetic_unreviewed_patch")
        for capability in DEVICE_CAPABILITIES:
            self.assertIsNone(self.core.acquire((capability,)))

    async def test_real_catalog_withdrawal_and_restoration_keep_exact_descriptors(self):
        await self.core.reconcile_once("startup")
        registry = core_tests.load_reviewed_upstream_release_registry()
        release = registry.by_version["8.4.3"]
        capture = core_tests._capture_for_release(release)
        review = core_tests._fixture(core_tests.ROOT / release.artifact_evidence_resource)
        by_name = {item["name"]: item for item in capture["tools"]}
        transport = core_tests.FakeTransport(
            [by_name[name] for name in review["runtime_catalog"]["runtime_tool_order"]],
            version="8.4.3",
        )
        gateway = core_tests.UpstreamReadGateway()
        gateway.configure(self.settings, transport=transport,
                          release_registry=registry, core_runtime=self.core)
        server = FastMCP("synthetic-rc3-postcore-catalog")
        static = registered_tools(get_registered_server())
        McpSdkToolRegistry(server).replace({name: static[name]
                                            for name in ENGINEERING_STATIC_TOOL_NAMES})
        self.assertEqual(len(registered_tools(server)), 51)
        await gateway.initialize(server)
        baseline = {tool.name: tool.model_dump(mode="json") for tool in await server.list_tools()}
        self.assertEqual(len(baseline), 76)

        self.network.records[0]["name"] = False
        await self.core.reconcile_once("synthetic_malformed_display")
        await gateway.initialize(server)
        self.assertEqual(len(registered_tools(server)), 71)
        self.assertEqual({x["tool"] for x in gateway.health_snapshot()["core_withheld_tools"]},
                         set(core_tests.DEVICE_DEPENDENT_DELEGATED_TOOLS))
        result = json.loads(await registered_tools(server)["ha_get_state"].run(
            {"entity_id": "sensor.synthetic"}))
        self.assertTrue(result["success"])

        self.network.records[0]["name"] = ""
        self.network.records[1]["name_by_user"] = "N" * 129
        await self.core.reconcile_once("synthetic_valid_display")
        await gateway.initialize(server)
        restored = {tool.name: tool.model_dump(mode="json") for tool in await server.list_tools()}
        self.assertEqual(restored, baseline)
        self.assertNotIn("ha_get_operation_status", restored)
        arguments = {
            "ha_get_device": {"device_id": "parent-garage"},
            "ha_get_entity": {"entity_id": "cover.synthetic_garage_door"},
            "ha_get_entity_exposure": {"entity_id": "cover.synthetic_garage_door"},
            "ha_get_overview": {},
            "ha_search": {"query": "synthetic"},
        }
        for name, args in arguments.items():
            result = json.loads(await registered_tools(server)[name].run(args))
            self.assertTrue(result["success"], result)
            self.assertEqual(transport.calls[-1][0], name)
        self.assertEqual(gateway.health_snapshot()["fallback_count"], 0)
        self.assert_authority_round_trip("core.dependency_helper_planning")

    async def test_fast_failures_are_paced_and_recover_with_fresh_monitor(self):
        for mode in ("http", "connection", "timeout", "auth", "monitor_auth",
                     "monitor_timeout", "monitor_close"):
            with self.subTest(mode=mode):
                try:
                    self.network.mode = mode
                    self.clock = _RetryClock()
                    self.start_supervisor()
                    first = await self.clock.next_delay()
                    before = self.core.health_snapshot()["counters"]["verification_attempts"]
                    notifications = len(self.notifications)
                    for _ in range(20):
                        self.core.request_reconciliation(connection_changed=True)
                        await asyncio.sleep(0)
                    self.assertEqual(self.core.health_snapshot()["counters"]["verification_attempts"], before)
                    self.assertEqual(len(self.notifications), notifications)
                    self.assertIsNone(self.core.acquire(("core.basic_rest_read",)))
                    self.assert_settled()
                    first.set_result(None)
                    second = await self.clock.next_delay()
                    self.assertEqual(self.core.health_snapshot()["counters"]["verification_attempts"], before + 1)
                    self.assertEqual(self.clock.delays[-2:], [300, 300])
                    self.network.mode = "ok"
                    old_monitors = len(self.network.monitors)
                    second.set_result(None)
                    await self.until(lambda: self.core.initialized)
                    self.assertEqual(self.core.current_observation.version, "2026.9.1")
                    self.assertGreater(len(self.network.monitors), old_monitors)
                    self.assertEqual(self.network.monitors[-1].sent[0]["type"], "auth")
                    self.assertEqual(self.core.health_snapshot()["compatible_count"], 17)
                    self.assert_authority_round_trip()
                finally:
                    if self.supervisor is not None:
                        self.supervisor.cancel()
                        await asyncio.gather(self.supervisor, return_exceptions=True)

    async def test_retirement_notifies_once_and_dependency_evidence_recovers(self):
        await self.core.reconcile_once("startup")
        dep_network = dependency_tests._Network()
        dep_network.payloads["/config"] = {"version": "2026.9.1"}
        dependency = DependencyAnalysisRuntime()
        dependency.configure(HomeAssistantRestClient(self.settings),
                             HomeAssistantWebSocketClient(self.settings),
                             core_runtime=self.core)
        self.addAsyncCleanup(dependency.shutdown)
        index = dependency.require().index

        async def dependency_read():
            with patch("ha_mcp_engineering.clients.rest.aiohttp.ClientSession",
                       side_effect=dep_network.session):
                return await index.get()

        original, _, _ = await dependency_read()
        self.assertTrue(index.active_identity()["current"])
        active = self.core.acquire(("core.basic_rest_read",))
        commits = self.core.consume(active)
        unused = self.core.acquire(("core.basic_rest_read",))
        self.assertIsNotNone(commits)
        self.assertIsNotNone(unused)
        notifications = len(self.notifications)
        self.network.mode = "http"
        self.start_supervisor()
        first = await self.clock.next_delay()
        self.assertEqual(len(self.notifications), notifications + 1)
        self.assertTrue(index.invalidated)
        epoch = index._source_epoch
        self.assertFalse(self.core.revalidate(active, commits))
        self.assertIsNone(self.core.consume(unused))
        self.assertTrue(self.core.finish(commits))
        self.assertFalse(self.core.finish(commits))
        self.assert_settled()
        calls = len(dep_network.calls)
        with self.assertRaises(HomeAssistantUnavailableError):
            await dependency_read()
        self.assertEqual(len(dep_network.calls), calls)
        self.assertFalse(index.active_identity()["current"])
        first.set_result(None)
        second = await self.clock.next_delay()
        self.assertEqual(index._source_epoch, epoch)
        self.assertEqual(len(self.notifications), notifications + 1)
        self.network.mode = "ok"
        second.set_result(None)
        await self.until(lambda: self.core.initialized)
        replacement, _, _ = await dependency_read()
        self.assertGreater(replacement.generation, original.generation)
        self.assertGreaterEqual(replacement.source_epoch, index._invalidation_epoch)
        self.assertTrue(index.active_identity()["current"])

        # Ordinary soft refresh still returns before its retained build finishes.
        index.snapshot.built_at_monotonic -= 601
        dep_network.hold("/states")
        with patch("ha_mcp_engineering.clients.rest.aiohttp.ClientSession",
                   side_effect=dep_network.session):
            stale, _, _ = await index.get()
            await asyncio.wait_for(dep_network.entered["/states"].wait(), 2)
            self.assertFalse(index._build_task.done())
            dep_network.gates["/states"].set()
            fresh = await asyncio.wait_for(asyncio.shield(index._build_task), 2)
        self.assertGreater(fresh.generation, stale.generation)
        self.assertTrue(index.active_identity()["current"])
        self.assertEqual(index.soft_ttl_seconds, 600)
        self.assertEqual(index.hard_ttl_seconds, 3600)
        for telemetry in dep_network.telemetry:
            self.assertFalse(telemetry.authorize_core_dispatch())
        self.assert_authority_round_trip()

    async def test_cancellation_during_retry_delay_stops_future_attempts(self):
        self.network.mode = "http"
        self.start_supervisor()
        delay = await self.clock.next_delay()
        before = len(self.network.sessions)
        self.supervisor.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await self.supervisor
        self.assertTrue(delay.cancelled())
        self.core.request_reconciliation(connection_changed=True)
        await asyncio.sleep(0)
        self.assertEqual(len(self.network.sessions), before)
        self.assertIsNone(self.core.acquire(("core.basic_rest_read",)))

    async def test_cancellation_during_probe_or_monitor_attachment_closes_sessions(self):
        for mode in ("blocked", "monitor_blocked"):
            with self.subTest(mode=mode):
                self.network.mode = mode
                self.network.blocked.clear()
                self.start_supervisor()
                await asyncio.wait_for(self.network.blocked.wait(), 2)
                self.supervisor.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await self.supervisor
                self.assertIsNone(self.core.acquire(("core.basic_rest_read",)))
                self.assert_settled()


if __name__ == "__main__":
    unittest.main()
