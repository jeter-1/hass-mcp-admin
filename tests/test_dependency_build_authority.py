"""RC2-SR-1: shared builds own authority beyond the requesting MCP call.

The original failing diagnostic remains in the local RC2 acceptance evidence.
These positive regressions use production gateway, tool, runtime, index,
provider, Core coordinator, and REST/WebSocket authorization. Only network
responses and scheduling are synthetic; no Home Assistant is contacted.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from tests import test_ha_core_2026_9_integration as core_tests
from ha_mcp_engineering.audit import AuditLogger
from ha_mcp_engineering.clients.rest import HomeAssistantRestClient
from ha_mcp_engineering.clients.websocket import HomeAssistantWebSocketClient
from ha_mcp_engineering.dependency.runtime import DependencyAnalysisRuntime
from ha_mcp_engineering.errors import (
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ha_mcp_engineering.governance.helper_dependency import HelperDependencyRiskService
from ha_mcp_engineering.request_context import current_telemetry
from ha_mcp_engineering.routing import AuthenticatedMcpGateway
from ha_mcp_engineering.tools import analysis


class _Network:
    """Synthetic session boundary below both production transport authorizers."""

    def __init__(self):
        self.calls = []
        self.telemetry = []
        self.ws_connections = 0
        self.gates = {}
        self.entered = {}
        self.errors = {}
        self.after_read = None
        self.payloads = {
            "/config": {"version": "2026.8.1"},
            "/states": [
                {"entity_id": "automation.synthetic", "state": "on",
                 "attributes": {"id": "synthetic"}},
                {"entity_id": "input_boolean.synthetic", "state": "off",
                 "attributes": {}},
            ],
            "/config/automation/config/synthetic": {
                "id": "synthetic", "alias": "Synthetic dependency",
                "triggers": [], "conditions": [],
                "actions": [{"action": "input_boolean.turn_on",
                             "target": {"entity_id": "input_boolean.synthetic"}}],
            },
            "config/entity_registry/list": [],
        }

    def hold(self, key):
        self.gates[key] = asyncio.Event()
        self.entered[key] = asyncio.Event()

    def opened(self, key):
        assert key in self.payloads, key
        telemetry = current_telemetry()
        assert telemetry is not None
        assert telemetry.core_dispatch_authorizer is not None
        self.calls.append(key)
        self.telemetry.append(telemetry)

    async def read(self, key):
        if key in self.gates:
            self.entered[key].set()
            await self.gates[key].wait()
        if key in self.errors:
            raise self.errors[key]
        if self.after_read is not None:
            self.after_read(key)
        return self.payloads[key]

    def session(self, **_):
        network = self

        class Response:
            status = 200

            def __init__(self, key):
                self.key = key

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def text(self):
                return json.dumps(await network.read(self.key))

        class Socket:
            def __init__(self):
                self.receives = 0
                self.key = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def send_json(self, payload):
                if payload["type"] != "auth":
                    self.key = payload["type"]
                    network.opened(self.key)

            async def receive_json(self):
                self.receives += 1
                if self.receives == 1:
                    return {"type": "auth_required"}
                if self.receives == 2:
                    return {"type": "auth_ok"}
                return {"type": "result", "id": 1, "success": True,
                        "result": await network.read(self.key)}

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def request(self, method, url, **_):
                assert method == "GET"
                key = next((key for key in network.payloads
                            if key.startswith("/") and url.endswith(key)), None)
                network.opened(key)
                return Response(key)

            def ws_connect(self, *_args, **_kwargs):
                network.ws_connections += 1
                return Socket()

        return Session()


class DependencyBuildAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.core, self.source = await core_tests.Core20269RuntimeTests()._runtime(
            core_tests._snapshot("2026.8.1", evidence=core_tests._evidence())
        )
        self.configured = core_tests.settings()
        self.network = _Network()
        session_patch = patch(
            "ha_mcp_engineering.clients.rest.aiohttp.ClientSession",
            side_effect=self.network.session,
        )
        session_patch.start()
        self.addCleanup(session_patch.stop)
        self.runtime = DependencyAnalysisRuntime()
        self.runtime.configure(
            HomeAssistantRestClient(self.configured),
            HomeAssistantWebSocketClient(self.configured),
            core_runtime=self.core,
        )
        self.index = self.runtime.require().index
        runtime_patch = patch.object(analysis, "DEPENDENCY_ANALYSIS", self.runtime)
        runtime_patch.start()
        self.addCleanup(runtime_patch.stop)
        self.acquired = []
        self.contexts = []
        self.after_acquire = None
        acquire = self.core.acquire

        def record_acquire(*args, **kwargs):
            telemetry = current_telemetry()
            authority = acquire(*args, **kwargs)
            if authority is not None:
                self.acquired.append(authority)
            if telemetry is not None:
                self.contexts.append((telemetry, telemetry.core_dispatch_authorizer))
            if self.after_acquire is not None:
                self.after_acquire(authority)
            return authority

        self.spies = {}
        for name in ("acquire", "consume", "revalidate", "release", "finish"):
            kwargs = ({"side_effect": record_acquire} if name == "acquire"
                      else {"wraps": getattr(self.core, name)})
            spy_patch = patch.object(self.core, name, **kwargs)
            self.spies[name] = spy_patch.start()
            self.addCleanup(spy_patch.stop)

    async def asyncTearDown(self):
        await self.runtime.shutdown()
        self.assert_cleaned()

    async def wait_entered(self, key):
        await asyncio.wait_for(self.network.entered[key].wait(), 3)

    async def settle_build(self):
        return await asyncio.wait_for(asyncio.shield(self.index._build_task), 3)

    def assert_cleaned(self):
        health = self.core.health_snapshot()
        for key in ("issued_lease_count", "active_commit_count",
                    "capacity_exhaustion_count", "fallback_count"):
            self.assertEqual(health[key], 0, key)
        released = [call.args[0] for call in self.spies["release"].call_args_list]
        finished = [tuple(commit.lease for commit in call.args[0])
                    for call in self.spies["finish"].call_args_list]
        for authority in self.acquired:
            self.assertEqual(released.count(authority) + finished.count(authority.leases), 1)
        consumed = [call.args[0] for call in self.spies["consume"].call_args_list]
        for authority in self.acquired:
            self.assertLessEqual(consumed.count(authority), 1)
        before = {name: spy.call_count for name, spy in self.spies.items()}
        for telemetry, callback in self.contexts:
            self.assertFalse(telemetry.authorize_core_dispatch())
            if callback is not None and telemetry.tool_name == "dependency_index_build":
                self.assertFalse(callback())
                self.assertFalse(callback())
        self.assertEqual(before, {name: spy.call_count for name, spy in self.spies.items()})

    async def unrelated_authority(self):
        await self.core.reconcile_once("synthetic_unrelated_read")
        authority = self.core.acquire(("core.basic_rest_read",))
        self.assertIsNotNone(authority)
        commits = self.core.consume(authority)
        self.assertIsNotNone(commits)
        self.assertTrue(self.core.revalidate(authority, commits))
        self.assertTrue(self.core.finish(commits))
        self.assert_cleaned()

    async def request(self, *, refresh=False, cancel_before_build=False):
        contexts = self.contexts

        async def app(_scope, receive, send):
            await receive()
            telemetry = current_telemetry()
            telemetry.caller_id = "synthetic-owner"
            contexts.append((telemetry, telemetry.core_dispatch_authorizer))
            if cancel_before_build:
                asyncio.get_running_loop().call_soon(asyncio.current_task().cancel)
            rendered = await analysis.entity_dependency_analysis(
                "input_boolean.synthetic", source_types=["automation"],
                limit=5, refresh_index=refresh,
            )
            body = json.dumps({"jsonrpc": "2.0", "id": "core-route-test", "result": {
                "content": [{"type": "text", "text": rendered}], "isError": False,
            }}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body, "more_body": False})

        gateway = AuthenticatedMcpGateway(
            app, self.configured,
            AuditLogger("unused", self.configured.access_secret, enabled=False),
            core_runtime=self.core,
        )
        body = await core_tests.Core20269StaticRouteTests._call(
            gateway, "entity_dependency_analysis", {"entity_id": "input_boolean.synthetic"}
        )
        return json.loads(json.loads(body)["result"]["content"][0]["text"])

    async def seed(self):
        self.assertTrue((await self.request())["success"])
        self.assertEqual(self.index.generation, 1)
        self.assert_cleaned()

    async def test_initial_build_uses_separate_exact_authority_and_telemetry(self):
        result = await self.request()
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["overview"]["direct_reference_count"], 1)
        self.assertEqual(self.network.calls, ["/config", "/states",
                         "config/entity_registry/list", "/config/automation/config/synthetic"])
        telemetry = self.network.telemetry[0]
        self.assertTrue(all(item is telemetry for item in self.network.telemetry))
        self.assertEqual(telemetry.caller_id, "synthetic-owner")
        self.assertIn("parent_request_id", telemetry.audit_context)
        self.assertNotEqual(telemetry.request_id, telemetry.audit_context["parent_request_id"])
        self.assertEqual(self.acquired[-1].capability_ids, ("core.dependency_helper_planning",))
        self.assertGreaterEqual(self.spies["revalidate"].call_count, len(self.network.calls) + 1)
        self.assert_cleaned()
        await self.unrelated_authority()

    async def test_startup_prewarm_then_foreground_refresh(self):
        task = self.runtime.start_prewarm(startup_delay_seconds=0)
        self.assertTrue(await asyncio.wait_for(task, 3))
        self.assertEqual(self.index.generation, 1)
        self.assertEqual(self.runtime.health()["prewarm_state"], "complete")
        self.assert_cleaned()
        self.assertTrue((await self.request(refresh=True))["success"])
        self.assertEqual(self.index.generation, 2)
        self.assert_cleaned()

    async def test_soft_refresh_completes_after_originating_request_returns(self):
        await self.seed()
        self.index.snapshot.built_at_monotonic -= 601
        self.network.hold("/states")
        result = await self.request()
        self.assertTrue(result["success"])
        self.assertTrue(result["data"]["index"]["evidence_stale"])
        await self.wait_entered("/states")
        build = self.index._build_task
        self.assertFalse(build.done())
        self.assertEqual(self.core.health_snapshot()["active_commit_count"], 1)
        self.network.gates["/states"].set()
        await self.settle_build()
        self.assertEqual(self.index.generation, 2)
        self.assertTrue(self.index.active_identity()["current"])
        self.assert_cleaned()
        await self.unrelated_authority()

    async def test_initiator_cancellation_does_not_revoke_shared_waiters(self):
        self.network.hold("/states")
        initiator = asyncio.create_task(self.request())
        await self.wait_entered("/states")
        build = self.index._build_task
        waiters = [asyncio.create_task(self.request()) for _ in range(2)]
        initiator.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await initiator
        self.assertIs(self.index._build_task, build)
        self.assertFalse(build.cancelled())
        self.network.gates["/states"].set()
        results = await asyncio.gather(*waiters)
        self.assertTrue(all(result["success"] for result in results))
        self.assertEqual(self.network.calls.count("/config"), 1)
        self.assertEqual(self.index.generation, 1)
        self.assert_cleaned()
        await self.unrelated_authority()

    async def test_initiator_cancellation_before_build_begins(self):
        with self.assertRaises(asyncio.CancelledError):
            await self.request(cancel_before_build=True)
        await self.settle_build()
        self.assertEqual(self.index.generation, 1)
        self.assertTrue(self.index.active_identity()["current"])
        self.assert_cleaned()

    async def test_manager_shutdown_cancels_provider_read_once(self):
        self.network.hold("/states")
        waiter = asyncio.create_task(self.request())
        await self.wait_entered("/states")
        await self.runtime.shutdown()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertIsNone(self.index.snapshot)
        self.assertEqual(self.network.calls, ["/config", "/states"])
        self.assert_cleaned()
        await self.runtime.shutdown()
        with self.assertRaisesRegex(RuntimeError, "dependency_index_shutdown"):
            await self.index.get()
        self.assert_cleaned()
        await self.unrelated_authority()

    async def test_manager_cancellation_before_build_begins_issues_nothing(self):
        self.index._ensure_build(mode="initial")
        await self.runtime.shutdown()
        self.assertEqual(self.spies["acquire"].call_count, 0)
        self.assertEqual(self.network.calls, [])
        self.assertIsNone(self.index.snapshot)
        self.assert_cleaned()

    async def test_retirement_between_rest_reads_refuses_next_dispatch(self):
        self.network.after_read = lambda key: (
            self.core.request_reconciliation(connection_changed=True)
            if key == "/config" else None
        )
        result = await self.request()
        self.assertFalse(result["success"])
        self.assertEqual(self.network.calls, ["/config"])
        self.assertIsNone(self.index.snapshot)
        self.assertEqual(self.spies["consume"].call_count, 1)
        self.assert_cleaned()
        await self.unrelated_authority()

    async def test_retirement_before_websocket_refuses_its_transport(self):
        self.network.after_read = lambda key: (
            self.core.request_reconciliation(connection_changed=True)
            if key == "/states" else None
        )
        result = await self.request()
        self.assertFalse(result["success"])
        self.assertEqual(self.network.calls, ["/config", "/states"])
        self.assertEqual(self.network.ws_connections, 0)
        self.assertIsNone(self.index.snapshot)
        self.assert_cleaned()

    async def test_retirement_after_last_read_cannot_publish(self):
        self.network.after_read = lambda key: (
            self.core.request_reconciliation(connection_changed=True)
            if key == "/config/automation/config/synthetic" else None
        )
        result = await self.request()
        self.assertFalse(result["success"])
        self.assertIsNone(self.index.snapshot)
        self.assertEqual(self.index.generation, 0)
        self.assertFalse(self.index.active_identity()["current"])
        self.assert_cleaned()

    async def test_invalidation_during_scan_cannot_clear_later_invalidation(self):
        self.network.after_read = lambda key: (
            self.index.invalidate("synthetic_configuration_change")
            if key == "/states" else None
        )
        await self.request()
        self.assertTrue(self.index.invalidated)
        self.assertFalse(self.index.active_identity()["current"])
        self.assertFalse(self.index.active_identity()["valid"])
        self.network.after_read = None
        self.assertTrue((await self.request())["success"])
        self.assertTrue(self.index.active_identity()["current"])
        self.assert_cleaned()

    async def test_provider_connection_failure_cleans_up_without_retry(self):
        self.network.errors["/states"] = OSError("synthetic unavailable network")
        with self.assertRaises(HomeAssistantUnavailableError):
            await self.index.get()
        self.assertEqual(self.network.calls, ["/config", "/states"])
        self.assertIsNone(self.index.snapshot)
        self.assert_cleaned()
        await self.unrelated_authority()

    async def test_provider_timeout_cleans_up_without_retry(self):
        self.network.errors["/states"] = TimeoutError("synthetic transport timeout")
        with self.assertRaises(HomeAssistantTimeoutError):
            await self.index.get()
        self.assertIsNone(self.index.snapshot)
        self.assert_cleaned()
        await self.unrelated_authority()

    async def test_whole_build_deadline_cancels_stalled_read(self):
        self.network.hold("/states")
        with patch("ha_mcp_engineering.dependency.index.BUILD_TIMEOUT_SECONDS", 0.02):
            with self.assertRaises(TimeoutError):
                await self.index.get()
        self.assertEqual(self.index.health()["last_build_failure_category"], "TimeoutError")
        self.assertIsNone(self.index.snapshot)
        self.assert_cleaned()
        await self.unrelated_authority()

    async def test_unavailable_core_refuses_acquisition_without_provider_work(self):
        self.core.request_reconciliation(connection_changed=True)
        with self.assertRaises(HomeAssistantUnavailableError):
            await self.index.get()
        self.assertEqual(self.spies["acquire"].call_count, 1)
        self.assertEqual(self.spies["consume"].call_count, 0)
        self.assertEqual(self.network.calls, [])
        self.assertIsNone(self.index.snapshot)
        self.assert_cleaned()
        await self.unrelated_authority()

    async def test_missing_core_binding_never_uses_no_authorizer_default(self):
        self.runtime.bind_core_runtime(None)
        with self.assertRaises(HomeAssistantUnavailableError):
            await self.index.get()
        self.assertEqual(self.network.calls, [])
        self.assertIsNone(self.index.snapshot)
        self.assert_cleaned()

    async def test_retirement_after_acquisition_releases_unconsumed_lease_once(self):
        self.after_acquire = lambda _: self.core.request_reconciliation(connection_changed=True)
        with self.assertRaises(HomeAssistantUnavailableError):
            await self.index.get()
        self.assertEqual(self.spies["release"].call_count, 1)
        self.assertEqual(self.spies["finish"].call_count, 0)
        self.assertEqual(self.network.calls, [])
        self.assert_cleaned()
        self.after_acquire = None
        await self.unrelated_authority()

    async def test_pending_manager_cancellation_after_acquisition_releases_once(self):
        self.after_acquire = lambda _: asyncio.current_task().cancel()
        with self.assertRaises(asyncio.CancelledError):
            await self.index.get()
        self.assertEqual(self.spies["consume"].call_count, 0)
        self.assertEqual(self.spies["release"].call_count, 1)
        self.assertEqual(self.spies["finish"].call_count, 0)
        self.assertEqual(self.network.calls, [])
        self.assert_cleaned()
        self.after_acquire = None
        await self.unrelated_authority()

    async def test_retired_callback_cannot_reacquire_after_core_recovers(self):
        self.network.hold("/states")
        waiter = asyncio.create_task(self.index.get())
        await self.wait_entered("/states")
        telemetry, callback = self.contexts[-1]
        self.core.request_reconciliation(connection_changed=True)
        self.assertFalse(callback())
        await self.core.reconcile_once("synthetic_core_recovered")
        before = self.spies["acquire"].call_count
        self.assertFalse(callback())
        self.assertFalse(telemetry.authorize_core_dispatch())
        self.network.gates["/states"].set()
        with self.assertRaises(HomeAssistantUnavailableError):
            await waiter
        self.assertEqual(self.spies["acquire"].call_count, before)
        self.assertIsNone(self.index.snapshot)
        self.assertEqual(self.network.calls, ["/config", "/states"])
        self.assert_cleaned()
        await self.unrelated_authority()

    async def test_prefence_scan_cannot_satisfy_governed_postlock_freshness(self):
        self.network.hold("/states")
        early = asyncio.create_task(self.request())
        await self.wait_entered("/states")
        first = self.index._build_task
        governed = asyncio.create_task(HelperDependencyRiskService(self.index).assess(
            "input_boolean.synthetic", refresh=True, fenced=True
        ))
        for _ in range(4):
            await asyncio.sleep(0)
        self.assertFalse(governed.done())
        self.network.gates["/states"].set()
        self.assertTrue((await early)["success"])
        evidence = await governed
        self.assertIsNot(self.index._build_task, first)
        self.assertEqual(self.network.calls.count("/config"), 2)
        self.assertTrue(evidence["provenance"]["fenced"])
        self.assertGreaterEqual(evidence["provenance"]["source_epoch"], 1)
        self.assert_cleaned()

    async def test_stale_allowed_before_hard_expiry_but_failed_rebuild_not_substituted(self):
        await self.seed()
        self.index.snapshot.built_at_monotonic -= 601
        self.network.errors["/states"] = OSError("synthetic failed refresh")
        stale = await self.request()
        self.assertTrue(stale["success"])
        self.assertTrue(stale["data"]["index"]["evidence_stale"])
        with self.assertRaises(HomeAssistantUnavailableError):
            await self.settle_build()
        self.assertTrue(self.index.active_identity()["valid"])
        self.index.snapshot.built_at_monotonic -= 3000
        expired = await self.request()
        self.assertFalse(expired["success"])
        self.assertFalse(self.index.active_identity()["valid"])
        self.assertEqual(self.index.generation, 1)
        self.assert_cleaned()

    async def test_invalidation_refuses_old_evidence_when_rebuild_fails(self):
        await self.seed()
        self.index.invalidate("synthetic_configuration_change")
        self.network.errors["/states"] = OSError("synthetic failed refresh")
        result = await self.request()
        self.assertFalse(result["success"])
        self.assertFalse(self.index.active_identity()["valid"])
        self.assertEqual(self.index.generation, 1)
        self.assert_cleaned()


if __name__ == "__main__":
    unittest.main()
