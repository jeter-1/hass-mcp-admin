"""Synthetic script collection, admitted-route and execution-isolation regressions."""

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch
from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency import script_sources as scripts
from ha_mcp_engineering.dependency.index import DependencyIndex
from ha_mcp_engineering.dependency.provider import DirectHaDependencyProvider
from ha_mcp_engineering.dependency.service import EntityDependencyAnalysisService
from ha_mcp_engineering.errors import GovernanceError
from ha_mcp_engineering.governance.helper_dependency import build_helper_dependency_risk_binding
from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway
from ha_mcp_engineering.request_context import begin_request, end_request, current_telemetry
from ha_mcp_engineering.upstream_tool_policy import load_reviewed_upstream_release_registry
from tests import test_entity_dependency_analysis as dependency_fixtures
from tests.test_entity_dependency_analysis import FakeProvider, finding, scan
from tests.test_readonly_upstream_gateway import (
    CompositeCoreRuntime, FakeTransport, catalog_tool, initialize, policy_entry, schema, settings,
)


def registry(entity="script.renamed", key="original", **kwargs):
    return {"entity_id": entity, "unique_id": key, "platform": "script", **kwargs}


def config(target="light.fixture"):
    return {"sequence": [{"action": "light.turn_on", "target": {"entity_id": target}}]}


def response(key="original", body=None):
    return {"success": True, "data": {"success": True, "script_id": key, "config": config() if body is None else body},
            "metadata": {"provider": "upstream_read_gateway", "upstream_version": "8.5.0",
                         "completeness": "complete", "fallback_occurred": False}}


class Reader:
    provenance = "ha-mcp 8.5.0; synthetic reviewed contract; identity inventory direct_ha_api; no fallback"

    def __init__(self, values=None):
        self.active = True
        self.values = values or {}
        self.calls = []
        self.active_calls = 0
        self.maximum_calls = 0

    def current(self):
        return self.active

    async def read(self, entity):
        self.calls.append(entity)
        self.active_calls += 1
        self.maximum_calls = max(self.maximum_calls, self.active_calls)
        try:
            await asyncio.sleep(0)
            result = self.values.get(entity, response())
            if isinstance(result, Exception):
                raise result
            return result
        finally:
            self.active_calls -= 1


async def collect(reader=None, rows=None, states=None, **kwargs):
    reader = reader or Reader()
    return await scripts.collect_script_diagnostics(
        states or [], [registry()] if rows is None else rows,
        registry_complete=kwargs.get("registry_complete", True),
        reader_factory=kwargs.get("reader_factory", lambda: reader),
        secret="synthetic-secret", concurrency=kwargs.get("concurrency", 8),
    )


class ScriptCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_renamed_and_disabled_script_positive_path(self):
        reader = Reader()
        result = await collect(reader, rows=[registry(disabled_by="user")])
        self.assertEqual(reader.calls, ["script.renamed"])
        self.assertEqual(len(result.findings), 1)
        item = result.findings[0]
        self.assertEqual((item.source_id, item.source_entity_id, item.target_entity_id),
                         ("original", "script.renamed", "light.fixture"))
        self.assertEqual(item.config_path, "$.sequence[0].target.entity_id")
        self.assertEqual(result.coverage.provider, "upstream_read_gateway")
        self.assertIn("8.5.0", result.coverage.policy)
        self.assertEqual(result.coverage.completeness, "partial")
        self.assertFalse(result.coverage.fallback_occurred)

    async def test_nested_and_missing_target_references(self):
        body = {"sequence": [{"choose": [{"conditions": [{"condition": "state", "entity_id": "sensor.missing"}],
                  "sequence": [{"parallel": [{"sequence": [{"repeat": {"while": [{"condition": "state", "entity_id": "sensor.repeat"}],
                  "sequence": [{"wait_for_trigger": [{"trigger": "state", "entity_id": "sensor.wait"}]}]}}]}]}]}]}]}
        result = await collect(Reader({"script.renamed": response(body=body)}))
        self.assertEqual({x.target_entity_id for x in result.findings}, {"sensor.missing", "sensor.repeat", "sensor.wait"})

    async def test_empty_inventory_is_not_complete_absence(self):
        result = await collect(rows=[])
        self.assertEqual(result.findings, ())
        self.assertEqual(result.coverage.completeness, "partial")
        self.assertIn("does not enumerate", result.coverage.warnings[0])

    async def test_retirement_stops_queued_reads_and_withholds_completed_evidence(self):
        reader = Reader()
        original = reader.read

        async def retire_after_read(entity):
            result = await original(entity)
            reader.active = False
            return result

        reader.read = retire_after_read
        result = await collect(reader, rows=[registry(), registry("script.z", "z")], concurrency=1)
        self.assertEqual(reader.calls, ["script.renamed"])
        self.assertFalse(result.findings)
        self.assertEqual(result.coverage.completeness, "unavailable")
        self.assertEqual(reader.active_calls, 0)

    async def test_prose_and_service_names_are_not_entity_references(self):
        body = {"alias": "light.fixture", "sequence": [{"action": "script.some_service"},
                {"action": "notify.fixture", "data": {"message": "sensor.fixture"}}]}
        result = await collect(Reader({"script.renamed": response(body=body)}))
        self.assertFalse(result.findings)
        self.assertIn("transitive", result.coverage.warnings[1])

    async def test_templates_remain_parse_only_and_opaque(self):
        body = {"sequence": [{"action": "light.turn_on", "target": {"entity_id": "{{ states('input_text.target') }}"}}]}
        result = await collect(Reader({"script.renamed": response(body=body)}))
        self.assertTrue(result.dynamic_references)
        self.assertEqual(result.coverage.completeness, "partial")

    async def test_blueprint_inputs_do_not_claim_expansion(self):
        body = {"use_blueprint": {"path": "synthetic/script.yaml", "input": {"target": "light.fixture"}}}
        result = await collect(Reader({"script.renamed": response(body=body)}))
        self.assertTrue(result.findings)
        self.assertIn("script_blueprint_body_unresolved: 1", result.coverage.warnings)

    async def test_no_guess_for_state_only_wrong_platform_or_conflicting_mapping(self):
        cases = [[], [registry(platform="other")], [registry(), registry(key="different")],
                 [registry(key="../../wrong")], [registry(), registry(entity="script.other")]]
        for rows in cases:
            with self.subTest(rows=rows):
                reader = Reader()
                result = await collect(reader, rows=rows, states=[{"entity_id": "script.renamed"}])
                self.assertFalse(reader.calls)
                self.assertFalse(result.findings)
                self.assertGreater(result.coverage.failed_item_count, 0)

    async def test_identical_registry_rows_deduplicate(self):
        reader = Reader()
        result = await collect(reader, rows=[registry(), registry()])
        self.assertEqual(len(reader.calls), 1)
        self.assertEqual(len(result.findings), 1)

    async def test_identity_collision_outside_candidate_limit_is_still_ambiguous(self):
        reader = Reader()
        with patch.object(scripts, "MAX_SCRIPT_SOURCES", 1):
            result = await collect(reader, rows=[registry(), registry("script.z", "original")])
        self.assertFalse(reader.calls)
        self.assertFalse(result.findings)
        self.assertIn("script_identity_unavailable_or_ambiguous: 1", result.coverage.warnings)

    async def test_registry_loss_does_not_dispatch(self):
        reader = Reader()
        result = await collect(reader, registry_complete=False)
        self.assertFalse(reader.calls)
        self.assertIn("script_registry_incomplete: 1", result.coverage.warnings)

    async def test_identity_and_partial_response_failures_preserve_neighbor(self):
        invalid = [response(key="wrong"), {"success": False}, response(body={}),
                   {**response(), "metadata": {"completeness": "partial"}},
                   response(body={"sequence": "malformed"})]
        for bad in invalid:
            with self.subTest(bad=bad):
                reader = Reader({"script.bad": bad})
                result = await collect(reader, rows=[registry(), registry("script.bad", "bad")])
                self.assertEqual(len(result.findings), 1)
                self.assertEqual(result.coverage.completeness, "partial")
                self.assertGreater(result.coverage.failed_item_count, 0)

    async def test_errors_are_bounded_categories_not_raw_provider_text(self):
        for category in ("resource_not_found", "authentication_failed", "timeout", "connection_failed", "invalid_response", "sanitization_failed", "secret payload"):
            with self.subTest(category=category):
                reader = Reader({"script.renamed": {"success": False, "details": {"failure_category": category}}})
                result = await collect(reader)
                self.assertFalse(result.findings)
                self.assertNotIn("secret payload", str(result))
                self.assertEqual(result.coverage.completeness, "unavailable")

    async def test_provider_not_ready_and_factory_failure_are_local_gaps(self):
        for factory in (lambda: None, Mock(side_effect=RuntimeError("synthetic-private-detail"))):
            result = await collect(reader_factory=factory)
            self.assertFalse(result.findings)
            self.assertEqual(result.coverage.completeness, "unavailable")
            self.assertNotIn("private-detail", str(result))

    async def test_count_and_evidence_bounds_are_script_local(self):
        rows = [registry(f"script.s{i}", f"s{i}") for i in range(6)]
        reader = Reader({f"script.s{i}": response(key=f"s{i}") for i in range(6)})
        with patch.object(scripts, "MAX_SCRIPT_SOURCES", 3), patch.object(scripts, "MAX_SCRIPT_FINDINGS", 2):
            result = await collect(reader, rows=list(reversed(rows)))
        self.assertEqual(sorted(reader.calls), ["script.s0", "script.s1", "script.s2"])
        self.assertEqual(len(result.findings), 2)
        self.assertIn("script_inventory_limit_exceeded: 1", result.coverage.warnings)
        self.assertIn("script_diagnostic_evidence_limit_exceeded: 1", result.coverage.warnings)
        self.assertNotIn("Automation", str(result.coverage))

    async def test_timeout_and_cancellation_settle_workers(self):
        for cancel in (False, True):
            active = set()
            started = asyncio.Event()
            reader = Reader()
            async def slow(entity):
                active.add(entity)
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    active.remove(entity)
            reader.read = slow
            with patch.object(scripts, "SCRIPT_SCAN_SECONDS", 0.03):
                task = asyncio.create_task(collect(reader))
                await started.wait()
                if cancel:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    result = await task
                    self.assertIn("script_scan_timeout: 1", result.coverage.warnings)
            self.assertFalse(active)

    async def test_concurrency_is_capped_and_evidence_order_is_deterministic(self):
        rows = [registry(f"script.s{i}", f"s{i}") for i in range(20)]
        reader = Reader({f"script.s{i}": response(key=f"s{i}") for i in range(20)})
        first = await collect(reader, rows=rows, concurrency=3)
        second = await collect(reader, rows=list(reversed(rows)), concurrency=3)
        self.assertLessEqual(reader.maximum_calls, 3)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.findings, second.findings)


class ScriptIndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_provider_outage_preserves_automation_and_settles_prewarm(self):
        rest = dependency_fixtures.DirectProviderTests.Rest()
        provider = DirectHaDependencyProvider(rest, dependency_fixtures.DirectProviderTests.WebSocket(),
                                               script_reader_factory=lambda: None)
        index = DependencyIndex(provider)
        self.assertTrue(await index.prewarm(AsyncMock(return_value=True)))
        self.assertTrue(index.snapshot.findings)
        self.assertEqual(index.snapshot.script_diagnostics.coverage.completeness, "unavailable")
        self.assertEqual(index.snapshot.build_profile["script_diagnostics"]["read_attempts"], 0)
        result = await EntityDependencyAnalysisService(index).analyze(entity_id="light.fixture", source_types=["script"])
        self.assertTrue(result.partial)
        self.assertFalse(result.data["findings"])
        self.assertEqual(index.generation, 1)

    async def build(self, diagnostic):
        original = scan([finding(target="light.fixture")])
        original.script_diagnostics = diagnostic
        index = DependencyIndex(FakeProvider(original))
        await index.get()
        return index

    async def test_diagnostic_partition_preserves_helper_binding_and_shared_consumers(self):
        diagnostic = await collect()
        plain = await self.build(None)
        augmented = await self.build(diagnostic)
        a, b = plain.snapshot, augmented.snapshot
        self.assertEqual(a.fingerprint, b.fingerprint)
        self.assertEqual(a.findings, b.findings)
        self.assertEqual(a.obligations, b.obligations)
        self.assertEqual(a.coverage, b.coverage)
        metadata = {"freshness": "current", "evidence_stale": False, "invalidated": False}
        self.assertEqual(build_helper_dependency_risk_binding(a, entity_id="input_boolean.fixture", index_metadata=metadata),
                         build_helper_dependency_risk_binding(b, entity_id="input_boolean.fixture", index_metadata=metadata))
        result = await EntityDependencyAnalysisService(augmented).analyze(entity_id="light.fixture", source_types=["script"])
        self.assertEqual(result.data["overview"]["direct_reference_count"], 1)
        self.assertTrue(result.partial)
        self.assertFalse(result.data["overview"]["coverage_complete"])

    async def test_retirement_withholds_cached_findings_and_invalidates_cursor(self):
        reader = Reader({"script.other": response(key="other")})
        diagnostic = await collect(reader, rows=[registry(), registry("script.other", "other")])
        index = await self.build(diagnostic)
        service = EntityDependencyAnalysisService(index)
        first = await service.analyze(entity_id="light.fixture", source_types=["script"], limit=1)
        cursor = first.data["pagination"]["next_cursor"]
        self.assertTrue(cursor)
        second = await service.analyze(entity_id="light.fixture", source_types=["script"], limit=1, cursor=cursor)
        self.assertEqual(len(second.data["findings"]), 1)
        # FakeProvider deep-copies the reader's bound method; retire that captured reader.
        index.snapshot.script_diagnostics.authority_current.__self__.active = False
        with self.assertRaises(GovernanceError):
            await service.analyze(entity_id="light.fixture", source_types=["script"], limit=1, cursor=cursor)
        result = await service.analyze(entity_id="light.fixture", source_types=["script"])
        self.assertFalse(result.data["findings"])
        self.assertTrue(index.active_identity()["valid"])
        self.assertEqual(index.generation, 1)
        self.assertEqual(next(x for x in result.data["source_coverage"] if x["source_type"] == "script")["completeness"], "unavailable")

    async def test_blueprint_script_evidence_never_leaks_into_automation_query(self):
        diagnostic = await collect(Reader({"script.renamed": response(body={"use_blueprint": {"path": "synthetic.yaml", "input": {"target": "light.fixture"}}})}))
        index = await self.build(diagnostic)
        result = await EntityDependencyAnalysisService(index).analyze(entity_id="light.fixture", source_types=["automation"])
        self.assertEqual(result.data["overview"]["direct_reference_count"], 1)
        self.assertTrue(all(x["source_type"] == "automation" for x in result.data["findings"]))

    async def test_retirement_before_publication_cannot_publish_fresh_script_evidence(self):
        reader = Reader()
        diagnostic = await collect(reader)
        reader.active = False
        index = await self.build(diagnostic)
        self.assertFalse(index.snapshot.script_diagnostics.findings)
        self.assertEqual(index.snapshot.script_diagnostics.coverage.completeness, "unavailable")
        self.assertTrue(index.snapshot.findings)


class ScriptGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def gateway(self, payload=None, core=None):
        entry = policy_entry("ha_config_get_script", reviewed_schema=schema("script_id"))
        tool = catalog_tool("ha_config_get_script", reviewed_schema=schema("script_id"))
        transport = FakeTransport([tool], result={"structuredContent": payload or response()["data"], "isError": False})
        gateway, server, transport = await initialize([entry], [tool], transport=transport, core_runtime=core)
        return gateway, server, transport

    async def test_fixed_read_uses_existing_admission_and_audit(self):
        core = CompositeCoreRuntime()
        gateway, _server, transport = await self.gateway(core=core)
        audit = Mock()
        result = await gateway.script_dependency_reader(audit=audit).read("script.renamed")
        self.assertTrue(result["success"])
        self.assertEqual(transport.calls[0][:2], ("ha_config_get_script", {"script_id": "script.renamed"}))
        self.assertEqual(core.finish_calls, 1)
        self.assertEqual(audit.write.call_args.args[0]["provider"], "upstream_read_gateway")
        self.assertIsNone(current_telemetry())

    async def test_redacted_or_truncated_configuration_is_not_exact_evidence(self):
        for body in ({"sequence": [], "password": "synthetic-private-password"}, {"sequence": [], "description": "x" * 25000}):
            gateway, _server, _transport = await self.gateway(response(body=body)["data"])
            result = await gateway.script_dependency_reader().read("script.renamed")
            self.assertTrue(result.get("success") is False or result["metadata"]["completeness"] != "complete")
            self.assertNotIn("synthetic-private-password", json.dumps(result))

    async def test_catalog_drift_refuses_and_retires_capture(self):
        gateway, _server, transport = await self.gateway()
        reader = gateway.script_dependency_reader()
        altered = dict(transport.catalog.tools[0], inputSchema=schema("wrong"))
        transport.catalog = replace(transport.catalog, tools=(altered,))
        result = await reader.read("script.renamed")
        self.assertFalse(result["success"])
        self.assertFalse(transport.calls)
        self.assertFalse(reader.current())

    async def test_parent_authority_failure_and_invalid_ids_cannot_dispatch(self):
        gateway, _server, transport = await self.gateway()
        reader = gateway.script_dependency_reader()
        for identity in ("../script.foo", "script.foo/bar", "script.", "light.foo"):
            self.assertFalse((await reader.read(identity))["success"])
        telemetry, token = begin_request()
        telemetry.core_dispatch_authorizer = lambda: False
        try:
            self.assertFalse((await reader.read("script.renamed"))["success"])
            self.assertIs(current_telemetry(), telemetry)
        finally:
            end_request(token)
        self.assertFalse(transport.calls)

    async def test_core_retirement_withholds_cached_route(self):
        core = CompositeCoreRuntime()
        gateway, _server, transport = await self.gateway(core=core)
        reader = gateway.script_dependency_reader()
        core.active = False
        self.assertFalse(reader.current())
        self.assertFalse((await reader.read("script.renamed"))["success"])
        self.assertFalse(transport.calls)

    async def test_manager_retirement_during_catalog_negotiation_prevents_dispatch(self):
        core = CompositeCoreRuntime()
        gateway, _server, transport = await self.gateway(core=core)
        active = True
        original = transport.execute_read

        async def delayed(*args, **kwargs):
            nonlocal active
            active = False
            return await original(*args, **kwargs)

        transport.execute_read = delayed
        telemetry, token = begin_request()
        telemetry.core_dispatch_authorizer = lambda: active
        try:
            result = await gateway.script_dependency_reader().read("script.renamed")
            self.assertFalse(result["success"])
        finally:
            end_request(token)
        self.assertFalse(transport.calls)
        self.assertEqual(core.consume_calls, 0)
        self.assertEqual(core.release_calls, 1)

    async def test_retained_transport_cannot_dispatch_after_owner_cancellation(self):
        core = CompositeCoreRuntime()
        gateway, _server, transport = await self.gateway(core=core)
        started = asyncio.Event()
        original = transport.execute_read

        async def delayed(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # A retained transport worker tries to continue after cancellation.
                return await original(*args, **kwargs)

        transport.execute_read = delayed
        task = asyncio.create_task(gateway.script_dependency_reader().read("script.renamed"))
        await started.wait()
        task.cancel()
        result = await task
        self.assertFalse(result["success"])
        self.assertFalse(transport.calls)
        self.assertEqual(core.consume_calls, 0)
        self.assertEqual(core.release_calls, 1)

    async def test_unconfigured_gateway_never_supplies_a_reader(self):
        self.assertIsNone(UpstreamReadGateway().script_dependency_reader())

    async def test_actual_reviewed_850_catalog_and_contract_supply_internal_read(self):
        compiled = load_reviewed_upstream_release_registry()
        release = compiled.by_version["8.5.0"]
        capture = json.loads((ROOT / release.capture_resource).read_text())
        review = json.loads((ROOT / "docs/evidence/upstream-read-compatibility/ha-mcp-8.5.0-contract-review.json").read_text())
        by_name = {item["name"]: item for item in capture["tools"]}
        tools = [by_name[name] for name in review["runtime_catalog"]["runtime_tool_order"]]
        transport = FakeTransport(tools, version="8.5.0",
                                  result={"structuredContent": response()["data"], "isError": False})
        gateway = UpstreamReadGateway()
        gateway.configure(settings(), release_registry=compiled, transport=transport)
        await gateway.initialize(FastMCP("script-850-fixture"))
        reader = gateway.script_dependency_reader()
        self.assertIsNotNone(reader)
        result = await reader.read("script.renamed")
        self.assertTrue(result["success"])
        self.assertEqual(result["metadata"]["upstream_version"], "8.5.0")
        self.assertEqual([call[0] for call in transport.calls], ["ha_config_get_script"])

    async def test_audit_failure_restores_request_context_and_settles_core(self):
        core = CompositeCoreRuntime()
        gateway, _server, _transport = await self.gateway(core=core)
        audit = Mock()
        audit.write.side_effect = RuntimeError("synthetic audit failure")
        with self.assertRaises(RuntimeError):
            await gateway.script_dependency_reader(audit=audit).read("script.renamed")
        self.assertIsNone(current_telemetry())
        self.assertEqual(core.finish_calls, 1)


if __name__ == "__main__":
    unittest.main()
