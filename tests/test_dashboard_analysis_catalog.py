"""Public integration and provider composition; synthetic authority and peers."""

import asyncio
from dataclasses import replace
import gc
import json
import subprocess
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT / "tests"))
from ha_mcp_engineering.dashboard_analysis import contracts as c
from ha_mcp_engineering.dashboard_analysis.provider import DashboardAnalysisProvider
from ha_mcp_engineering.dashboard_analysis.service import DashboardAnalysisService
from ha_mcp_engineering.dashboard_analysis.runtime import DASHBOARD_ANALYSIS
from ha_mcp_engineering.clients.mcp import McpDashboardTransport
from ha_mcp_engineering.providers.upstream_dashboard import (
    UpstreamDashboardProvider, _analysis_configuration, _upstream_config_hash,
    _engineering_config_hash)
from ha_mcp_engineering.request_context import begin_request, end_request
from ha_mcp_engineering.tools import dashboard as dashboard_tools
from test_rc3a_dashboard_provider import settings, handshake, call_result, dashboard_tool
import test_dashboard_analysis_transport as transport_cases


class PublicBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_native_descriptors_are_identical_and_only_one_is_added(self):
        from ha_mcp_engineering.tools import get_registered_server
        baseline = json.loads((ROOT / "tests/fixtures/dashboard_analysis/native-c82-descriptors.json").read_text())
        current = {tool.name: tool.model_dump(by_alias=True, exclude_none=True)
                   for tool in await get_registered_server().list_tools()}
        self.assertEqual(set(current) - set(baseline["descriptors"]), {"dashboard_integrity_analysis"})
        self.assertEqual({name: current[name] for name in baseline["descriptors"]}, baseline["descriptors"])

    async def test_old_delegated_descriptors_are_identical(self):
        from mcp.server.fastmcp import FastMCP
        from test_beta58_ha_mcp_8_4_3_continuity import _GatewayTransport, _settings
        from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway
        from ha_mcp_engineering.upstream_tool_policy import load_reviewed_upstream_release_registry
        registry = load_reviewed_upstream_release_registry()
        release = registry.by_version["8.4.3"]
        capture = json.loads((ROOT / release.capture_resource).read_text())
        evidence = json.loads((ROOT / release.artifact_evidence_resource).read_text())
        by_name = {x["name"]: x for x in capture["tools"]}
        ordered = [by_name[n] for n in evidence["runtime_catalog"]["runtime_tool_order"]]
        gateway, server = UpstreamReadGateway(), FastMCP("synthetic-descriptor-preservation")
        with patch("ha_mcp_engineering.providers.upstream_read_gateway.replace_dynamic_upstream_capabilities"):
            gateway.configure(replace(_settings(""), ha_mcp_release_registry_enabled=False), transport=_GatewayTransport(ordered, version="8.4.3"),
                              release_registry=registry, signed_release_registry=None)
            await gateway.initialize(server)
        descriptors = {x.name: x.model_dump(by_alias=True, exclude_none=True) for x in await server.list_tools()}
        baseline = json.loads((ROOT / "tests/fixtures/dashboard_analysis/delegated-c82-descriptors.json").read_text())
        self.assertEqual(len(descriptors), 25)
        self.assertEqual(descriptors, baseline["descriptors"])

    async def test_schema_arguments_and_read_only_no_fallback_policy(self):
        from ha_mcp_engineering.ha_core_readmission.routes import static_tool_requirements
        from ha_mcp_engineering.providers.routing import routing_for_tool
        tool = dashboard_tools.registered_analysis_tool()
        self.assertEqual(set(tool.parameters["properties"]), {"url_path", "limit", "cursor"})
        self.assertEqual(tool.parameters["required"], ["url_path"])
        self.assertFalse(tool.parameters["additionalProperties"])
        self.assertTrue(tool.annotations.readOnlyHint)
        self.assertFalse(tool.annotations.destructiveHint)
        self.assertEqual(set(static_tool_requirements(tool.name, {})), set(c.REQUIREMENTS))
        route = routing_for_tool(tool.name)
        for arguments in (None, {"url_path": "synthetic", "limit": True},
                          {"url_path": "synthetic", "PRIVATE_KEY": "synthetic-private-value"}):
            with patch.object(DASHBOARD_ANALYSIS, "require", side_effect=AssertionError("no collection")):
                raw = await tool.run(arguments)
            self.assertFalse(json.loads(raw)["success"])
            self.assertNotIn("synthetic-private-value", raw)
            self.assertNotIn("PRIVATE_KEY", raw)
        self.assertEqual(route.preferred_provider, "engineering")
        self.assertEqual(route.fallback_providers, ())

    async def test_all_failure_codes_are_fixed_and_retry_guidance_is_preserved(self):
        from unittest.mock import AsyncMock
        for reason in (*c.REASONS, "synthetic-private-value"):
            failure = c.AnalysisError(reason)
            service = SimpleNamespace(analyze=AsyncMock(side_effect=failure))
            with patch.object(DASHBOARD_ANALYSIS, "require", return_value=service):
                raw = await dashboard_tools.registered_analysis_tool().run({"url_path": "synthetic"})
            response = json.loads(raw)
            self.assertFalse(response["success"])
            self.assertEqual(response["details"], {"reason": failure.reason})
            self.assertEqual(response["retryable"], failure.retryable)
            self.assertEqual(response["metadata"]["completeness"], "unavailable")
            self.assertNotIn("synthetic-private-value", raw)

    async def test_invalid_gateway_input_precedes_sdk_authority_and_value_free_audit(self):
        import httpx
        from unittest.mock import Mock
        from ha_mcp_engineering.routing import AuthenticatedMcpGateway
        from ha_mcp_engineering.audit import AuditLogger
        from ha_mcp_engineering.configuration import Settings
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "audit.jsonl"
            config = Settings(ha_url="http://synthetic.invalid", ha_token="SYNTHETIC_HA_TOKEN",
                access_secret="SYNTHETIC_ACCESS_TOKEN", port=8100, audit_path=str(audit),
                rate_limit_per_minute=1000, rate_limit_burst=1000, destructive_services=frozenset())
            app = Mock(side_effect=AssertionError("SDK must not run"))
            core = SimpleNamespace(acquire=Mock())
            gateway = AuthenticatedMcpGateway(app, config, AuditLogger(str(audit), config.access_secret), core_runtime=core)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url="http://127.0.0.1:8100") as client:
                response = await client.post("/SYNTHETIC_ACCESS_TOKEN/mcp", json={"jsonrpc": "2.0", "id": "synthetic-request",
                    "method": "tools/call", "params": {"name": "dashboard_integrity_analysis", "arguments": {
                    "url_path": "synthetic-private-value", "PRIVATE_KEY": "synthetic-private-value", "cursor": "synthetic-private-value"}}})
            self.assertIn("invalid_request", response.text)
            self.assertNotIn("synthetic-private-value", response.text + audit.read_text())
            self.assertNotIn("PRIVATE_KEY", audit.read_text())
            core.acquire.assert_not_called()
            app.assert_not_called()


class InnerPayloadTests(unittest.TestCase):
    def payload(self):
        config = {"views": [{"cards": [{"type": "button", "entity": "light.synthetic",
                    "name": "synthetic-private-value", "url": "https://synthetic.invalid"}]}]}
        return {"success": True, "url_path": "synthetic", "config": config,
                "config_hash": _upstream_config_hash(config)}

    def test_original_hashes_are_verified_before_projection(self):
        payload = self.payload()
        result = _analysis_configuration(call_result(payload), "synthetic")
        self.assertEqual(result["engineering_config_hash"], _engineering_config_hash(payload["config"]))
        self.assertEqual(result["config_hash"], payload["config_hash"])
        self.assertEqual(result["configuration"], payload["config"])

    def test_unpaired_unicode_is_permanent_malformed_source(self):
        payload = self.payload()
        payload["config"]["name"] = chr(0xD800)
        payload["config_hash"] = _upstream_config_hash(payload["config"])
        with self.assertRaises(c.AnalysisError) as error:
            _analysis_configuration(call_result(payload), "synthetic")
        self.assertEqual(error.exception.reason, "malformed_response")
        self.assertFalse(error.exception.retryable)

    def test_contradictions_are_permanent_and_never_reflected(self):
        cases = []
        for key, value in (("url_path", "synthetic-private-value"),
                           ("config_hash", "0" * 16), ("success", False), ("config", None)):
            value_payload = self.payload()
            value_payload[key] = value
            cases.append(call_result(value_payload))
        cases.extend([{"content": []}, {"content": [{"type": "image", "data": "synthetic-private-value"}]},
                      dict(call_result(self.payload()), isError=0),
                      dict(call_result(self.payload()), structuredContent={}),
                      dict(call_result(self.payload()), structuredContent=dict(self.payload(), success=1)),
                      {"content": [{"type": "text", "text": '{"success":true,"success":false}'}]},
                      {"content": [{"type": "text", "text": '{"success":NaN}'}]}])
        for result in cases:
            with self.subTest(result=result), self.assertRaises(c.AnalysisError) as error:
                _analysis_configuration(result, "synthetic")
            self.assertFalse(error.exception.retryable)
            self.assertNotIn("synthetic-private-value", str(error.exception))


class AssembledTests(unittest.IsolatedAsyncioTestCase):
    """Real locked SDK, real admission/hash/projection/page work, loopback only.

    Core authority is synthetic here; separate signed-Core cases exercise leases.
    No call below reaches an external host or an actual Home Assistant instance.
    """
    states = transport_cases.NativeTransportTests.states
    websocket = transport_cases.NativeTransportTests.websocket
    forbidden = transport_cases.NativeTransportTests.forbidden

    def add_routes(self, app):
        app.router.add_route("*", "/mcp", self.mcp)

    async def asyncSetUp(self):
        await transport_cases.NativeTransportTests.asyncSetUp(self)
        self.telemetry, self.context = begin_request()
        self.addCleanup(end_request, self.context)
        self.telemetry.caller_id = "synthetic-owner"
        self.telemetry.core_dispatch_authorizer = lambda: self.authorized
        self.generation = 1
        self.missing_profile = None
        self.authority_checks = []
        self.observation = SimpleNamespace(version="2026.9.4", fingerprint="sha256:" + "1" * 64)
        self.core = SimpleNamespace(route_status=self.route_status, current_observation=self.observation)
        self.configuration = {"views": [{"cards": [{"type": "entities", "entities": [
            "sensor.test", "sensor.only", "sensor.absent"]},
            {"type": "button", "entity": "input_text.synthetic",
             "name": "synthetic-private-value", "tap_action": {"action": "none"}}]}]}
        self.registry = [{"entity_id": "sensor.only", "disabled_by": None}]
        self.dashboard_payload = None
        self.descriptor = dashboard_tool()
        self.server_version = "7.13.0"
        self.retire_at = None
        self.mcp_calls = []
        self.settings = settings(response_size_limit=32768)
        port = self.site._server.sockets[0].getsockname()[1]
        self.transport = McpDashboardTransport(f"http://127.0.0.1:{port}/mcp",
                                                timeout_seconds=10, client_version="synthetic")
        self.upstream = UpstreamDashboardProvider()
        self.upstream.configure(self.settings, transport=self.transport)
        # Synthetic startup admission. Collection itself never refreshes it.
        self.upstream._validate_handshake(handshake())
        self.provider = DashboardAnalysisProvider(self.client, self.core, self.upstream,
                                                   known_secrets=("synthetic-private-value",))
        self.service = DashboardAnalysisService(self.provider, response_limit=32768,
                                                known_secrets=("synthetic-private-value",))

    async def asyncTearDown(self):
        await transport_cases.NativeTransportTests.asyncTearDown(self)

    def route_status(self, requirements):
        self.authority_checks.append(tuple(requirements))
        return {"available": self.authorized and self.missing_profile not in requirements,
                "generation": self.generation}

    async def mcp(self, request):
        if request.method != "POST":
            self.mcp_calls.append(request.method)
            return web.Response(status=204 if request.method == "DELETE" else 405)
        message = await request.json()
        method = message["method"]
        self.mcp_calls.append(method)
        if method == self.retire_at:
            self.generation += 1
        if method == "notifications/initialized":
            return web.Response(status=202)
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "ha-mcp", "version": self.server_version}}
        elif method == "tools/list":
            result = {"tools": [self.descriptor]}
        elif method == "tools/call":
            self.assertEqual(message["params"], {"name": "ha_config_get_dashboard",
                "arguments": {"url_path": "synthetic", "force_reload": True,
                              "include_screenshot": False, "list_only": False}})
            payload = self.dashboard_payload or {"success": True, "url_path": "synthetic",
                "config": self.configuration, "config_hash": _upstream_config_hash(self.configuration)}
            result = call_result(payload)
        else:
            self.fail("Unexpected MCP command")
        return web.Response(body=c.canonical({"jsonrpc": "2.0", "id": message["id"], "result": result}),
                            content_type="application/json",
                            headers={"mcp-session-id": "synthetic-analysis"})

    async def invoke(self, **arguments):
        with patch.object(DASHBOARD_ANALYSIS, "require", return_value=self.service), \
             patch.object(dashboard_tools, "SETTINGS", self.settings):
            return json.loads(await dashboard_tools.dashboard_integrity_analysis(
                url_path="synthetic", **arguments))

    async def test_useful_mixed_report_and_frozen_export(self):
        with patch.object(self.upstream, "refresh_capabilities", side_effect=AssertionError("refresh forbidden")), \
             patch.object(self.upstream._registry, "refresh", side_effect=AssertionError("refresh forbidden")), \
             patch.object(self.upstream, "_execute", side_effect=AssertionError("old retry path forbidden")):
            first = await self.invoke(limit=1)
            self.assertTrue(first["success"], first)
            data, items = first["data"], list(first["data"]["items"])
            calls = list(self.calls), list(self.mcp_calls)
            while data["pagination"]["next_cursor"]:
                response = await self.invoke(limit=100, cursor=data["pagination"]["next_cursor"])
                self.assertTrue(response["success"], response)
                self.assertLessEqual(len(c.canonical(response)), 32768)
                data = response["data"]
                self.assertEqual(data["header"], first["data"]["header"])
                items.extend(data["items"])
        self.assertEqual(calls, (self.calls, self.mcp_calls))
        references = {x["entity_id"]: x["availability"] for x in items if x["kind"] == "entity_reference"}
        self.assertEqual(references["sensor.test"], "unavailable")
        self.assertEqual(references["sensor.only"], "registry_only")
        self.assertEqual(references["sensor.absent"], "absent_from_observed_inventories")
        self.assertNotIn("synthetic-private-value", json.dumps(first))
        self.assertNotIn("synthetic", json.dumps(self.telemetry.audit_context))
        self.assertEqual(self.mcp_calls.count("tools/call"), 1)
        self.assertEqual(self.calls, ["states", "websocket", "auth",
                                    {"id": 1, "type": "config/entity_registry/list"}])
        self.assertTrue(all(x == c.REQUIREMENTS for x in self.authority_checks))
        self.assertEqual(data["header"]["transport"]["logical_reads"], 3)
        self.assertTrue(data["header"]["default_rules_applicable"])
        self.assertEqual(data["header"]["transport"]["retries"], 0)
        self.assertEqual(data["header"]["transport"]["native_auth_requests"], 1)
        self.assertEqual(data["header"]["transport"]["native_auth_frames"], 2)

    async def test_reviewed_8_4_3_dashboard_descriptor_uses_existing_admission(self):
        from ha_mcp_engineering.upstream_tool_policy import load_reviewed_upstream_release_registry
        release = load_reviewed_upstream_release_registry().by_version["8.4.3"]
        capture = json.loads((ROOT / release.capture_resource).read_text())
        self.descriptor = next(x for x in capture["tools"] if x["name"] == "ha_config_get_dashboard")
        self.server_version = "8.4.3"
        self.upstream._validate_handshake(handshake(tool=self.descriptor, version="8.4.3"))
        result = await self.invoke()
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["header"]["authority"]["upstream_version"], "8.4.3")
        self.assertEqual(self.mcp_calls.count("tools/call"), 1)

    async def test_failed_inventory_preserves_static_and_positive_evidence(self):
        self.status = 503
        result = await self.invoke(limit=100)
        self.assertTrue(result["success"], result)
        refs = {x["entity_id"]: x["availability"] for x in result["data"]["items"]
                if x["kind"] == "entity_reference"}
        self.assertEqual(refs["sensor.absent"], "unassessed")
        self.assertEqual(refs["sensor.only"], "registry_only")
        self.assertEqual(result["data"]["header"]["coverage"]["availability"], "partial")
        self.assertEqual(result["data"]["header"]["sources"][1]["failure"], "source_unavailable")
        self.assertEqual(self.calls.count("states"), 1)

    async def test_each_profile_and_upstream_admission_required_before_io(self):
        for profile in c.REQUIREMENTS:
            self.missing_profile = profile
            result = await self.invoke()
            self.assertFalse(result["success"])
        self.missing_profile = None
        self.upstream._state.capability_status = "unavailable"
        self.assertFalse((await self.invoke())["success"])
        self.assertEqual(self.calls + self.mcp_calls, [])
        self.assertEqual(self.service.snapshots, {})

    async def test_core_retirement_during_upstream_read_discards_snapshot(self):
        self.retire_at = "tools/call"
        result = await self.invoke()
        self.assertFalse(result["success"])
        self.assertEqual(self.service.snapshots, {})
        self.assertEqual(self.calls, [])
        self.assertEqual(self.mcp_calls.count("tools/call"), 1)

    async def test_schema_and_identity_drift_never_dispatch_dashboard(self):
        for change in ("schema", "version"):
            self.descriptor = dashboard_tool()
            self.server_version = "7.13.0"
            if change == "schema":
                self.descriptor["inputSchema"]["properties"]["force_reload"]["type"] = "string"
            else:
                self.server_version = "0.0.0"
            result = await self.invoke()
            self.assertFalse(result["success"])
            self.assertEqual(self.service.snapshots, {})
        self.assertNotIn("tools/call", self.mcp_calls)
        self.assertEqual(self.calls, [])

    async def test_access_hash_and_path_refusals_never_commit(self):
        for kind in ("access", "hash", "path"):
            self.status = 403 if kind == "access" else 200
            self.dashboard_payload = {"success": True, "url_path": "wrong" if kind == "path" else "synthetic",
                "config": self.configuration, "config_hash": "0" * 16 if kind == "hash" else
                _upstream_config_hash(self.configuration)}
            result = await self.invoke()
            self.assertFalse(result["success"])
            self.assertFalse(result["retryable"])
            self.assertEqual(self.service.snapshots, {})

    async def test_upstream_retirement_on_continuation_has_zero_io(self):
        first = await self.invoke(limit=1)
        self.assertTrue(first["success"], first)
        before = list(self.calls), list(self.mcp_calls)
        self.upstream._state.capability_status = "unavailable"
        result = await self.invoke(cursor=first["data"]["pagination"]["next_cursor"])
        self.assertFalse(result["success"])
        self.assertEqual(before, (self.calls, self.mcp_calls))

    async def test_upstream_retirement_after_states_prevents_registry_read(self):
        original = c.worker
        async def retire(function, *args, **kwargs):
            result = await original(function, *args, **kwargs)
            if function.__name__ == "project_inventory":
                self.upstream._state.capability_status = "unavailable"
            return result
        with patch.object(c, "worker", retire):
            result = await self.invoke()
        self.assertFalse(result["success"])
        self.assertEqual(self.calls, ["states"])
        self.assertEqual(self.service.snapshots, {})

    async def test_retirement_after_projection_and_after_page_serialization(self):
        original = c.worker
        for point in ("_freeze", "render_result"):
            async def retire(function, *args, **kwargs):
                result = await original(function, *args, **kwargs)
                if function.__name__ == point:
                    self.generation += 1
                return result
            with patch.object(c, "worker", retire):
                result = await self.invoke()
            self.assertFalse(result["success"])
            self.assertEqual(self.service.snapshots, {})

    async def test_full_assembled_responsiveness_with_cpu_and_gc_attribution(self):
        command = [sys.executable, "-I", "-B", "-c",
            "import sys,unittest; sys.path.insert(0,sys.argv.pop(1)); unittest.main(module=None)",
            str(ROOT / "tests"), "test_dashboard_analysis_catalog.AssembledTests._full_assembled_path", "-v"]
        result = await asyncio.to_thread(subprocess.run, command, cwd=ROOT, capture_output=True,
                                         text=True, timeout=40, check=False)
        print(result.stdout, end="", flush=True)
        for line in result.stderr.splitlines():
            print(line.replace("Ran ", "Isolated child summary: ", 1) if line.startswith("Ran ") else line,
                  file=sys.stderr, flush=True)
        self.assertEqual(result.returncode, 0, "Assembled analyzer responsiveness child failed.")

    async def _full_assembled_path(self):
        # Maximum native wire bytes and entry count; bounded dashboard just
        # below its 2 MiB JSON envelope, with both admission/hash passes real.
        values = [{"entity_id": f"sensor.sample{i}", "state": "unknown"}
                  for i in range(c.INVENTORY_ENTRIES)]
        raw = c.canonical(values)
        self.states_raw = raw + b" " * (c.INVENTORY_BYTES - len(raw))
        self.registry = [{"entity_id": x["entity_id"], "disabled_by": None, "opaque": "x" * 340} for x in values]
        self.registry_wire_bytes = c.INVENTORY_BYTES
        self.configuration = {"views": [{"cards": [{"type": "button", "entity": f"sensor.sample{i}"}
                                                   for i in range(300)]}],
                              "opaque": "x" * 1_950_000}
        owner, original_gc = threading.get_ident(), (gc.isenabled(), gc.get_threshold())
        for contention in (False, True):
            self.service.snapshots.clear()
            gaps, cpu_gaps, events, starts, stages = [], [], [], {}, {}
            original = c.worker
            async def worker(function, *args, **kwargs):
                def observed():
                    stages.setdefault(function.__name__, set()).add(threading.get_ident())
                    return function(*args, **kwargs)
                return await original(observed)
            async def heartbeat():
                last, last_cpu = time.perf_counter(), time.thread_time()
                while True:
                    await asyncio.sleep(.005)
                    now, cpu = time.perf_counter(), time.thread_time()
                    gaps.append(now - last)
                    cpu_gaps.append(cpu - last_cpu)
                    last, last_cpu = now, cpu
            def observe_gc(phase, info):
                if info["generation"] != 2:
                    return
                thread = threading.get_ident()
                if phase == "start":
                    starts[thread] = time.perf_counter(), time.thread_time()
                elif thread in starts and len(events) < 64:
                    wall, cpu = starts.pop(thread)
                    events.append({"event_loop_thread": thread == owner,
                        "wall_seconds": time.perf_counter() - wall,
                        "thread_cpu_seconds": time.thread_time() - cpu})
            pulse = asyncio.create_task(heartbeat())
            stress = asyncio.create_task(asyncio.to_thread(sum, range(4_000_000))) if contention else None
            gc.callbacks.append(observe_gc)
            try:
                await asyncio.sleep(0)
                with patch.object(c, "worker", worker):
                    result = await self.invoke(limit=100)
                if stress:
                    await stress
                await asyncio.sleep(.01)
            finally:
                gc.callbacks.remove(observe_gc)
                pulse.cancel()
                await asyncio.gather(pulse, return_exceptions=True)
                if stress:
                    await stress
            self.assertTrue(result["success"], result)
            for name in ("_analysis_handshake", "_analysis_configuration", "project_inventory",
                         "_freeze", "_prepare", "_page", "render_result"):
                self.assertIn(name, stages)
                self.assertNotIn(owner, stages[name])
            print(json.dumps({"fixture": "assembled_dashboard_admission_hash_scan_export",
                "controlled_contention": contention, "maximum_loop_gap_seconds": max(gaps),
                "maximum_thread_cpu_gap_seconds": max(cpu_gaps), "major_gc_events": events,
                "gc_enabled": original_gc[0], "gc_thresholds": original_gc[1],
                "wall_bound_seconds": .25, "thread_cpu_bound_seconds": .1}), flush=True)
            self.assertLess(max(gaps), .25)
            self.assertLess(max(cpu_gaps), .1)
            self.assertEqual((gc.isenabled(), gc.get_threshold()), original_gc)
            self.assertLessEqual(len(c.canonical(result)), c.PAGE_BYTES)

    async def test_real_signed_core_leases_require_all_three_existing_profiles(self):
        from core_registry_fixtures import CoreSigner, ProjectedCoreSource, core_entry
        from signed_registry_fixtures import NOW
        from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
        from ha_mcp_engineering.ha_core_readmission import CoreRuntime
        # Uncompiled synthetic version forces signed authority selection; no
        # existing compiled release can silently fill the omitted capability.
        version = '2026.9.99'
        self.greeting['ha_version'] = self.auth['ha_version'] = version
        for omitted in (*c.REQUIREMENTS, None):
            with self.subTest(omitted=omitted), tempfile.TemporaryDirectory() as directory:
                signer = CoreSigner()
                entry = core_entry(version)
                entry['capabilities'] = [item for item in entry['capabilities'] if item['capability_id'] != omitted]
                raw = signer.journal_raw(envelopes=[signer.raw(entries=[entry])])
                async def fetch(*_):
                    return raw
                registry = CoreReleaseRegistry(enabled=True, public_key=signer.public_key_base64,
                    cache_path=Path(directory)/'registry.json', fetcher=fetch, now=lambda: NOW)
                self.assertTrue(await registry.refresh())
                runtime = CoreRuntime()
                runtime.configure(SimpleNamespace(), source=ProjectedCoreSource(registry, version), release_registry=registry)
                await runtime.reconcile_once('synthetic-dashboard')
                lease = runtime.acquire(c.REQUIREMENTS)
                self.assertEqual(lease is not None, omitted is None)
                commits = runtime.consume(lease) if lease else None
                self.telemetry.core_dispatch_authorizer = lambda: bool(lease and runtime.revalidate(lease, commits))
                self.provider.core_runtime = runtime
                before = list(self.calls), list(self.mcp_calls)
                try:
                    result = await self.invoke()
                    self.assertEqual(result['success'], omitted is None, result)
                    if omitted is None:
                        self.assertFalse(result['data']['header']['default_rules_applicable'])
                        self.assertEqual(result['data']['header']['coverage']['controls'], 'partial')
                    else:
                        self.assertEqual(before, (self.calls, self.mcp_calls))
                finally:
                    if commits:
                        self.assertTrue(runtime.finish(commits))
                self.assertEqual(runtime.health_snapshot()['active_commit_count'], 0)
