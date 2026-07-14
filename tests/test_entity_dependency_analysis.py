import asyncio
import copy
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.dependency.extraction import (  # noqa: E402
    extract_document,
    resolve_blueprint_roles,
    valid_entity_id,
)
from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.models import (  # noqa: E402
    DependencyFinding,
    DependencyScanResult,
    DynamicReference,
    SourceCoverageItem,
    evidence_id,
)
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    DependencySourceProvider,
    DirectHaDependencyProvider,
)
from ha_mcp_engineering.dependency.service import EntityDependencyAnalysisService  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError, InvalidRequestError  # noqa: E402
from ha_mcp_engineering.observability import METRICS  # noqa: E402
from ha_mcp_engineering.providers import EvidenceRequest, ProviderCapability  # noqa: E402
from ha_mcp_engineering.tools import get_registered_server  # noqa: E402
from ha_mcp_engineering.tools import compatibility  # noqa: E402
from unittest.mock import AsyncMock, patch


TARGET = "sensor.example"


class FakeProvider(DependencySourceProvider):
    provider_id = "direct_ha_api"
    capabilities = frozenset({ProviderCapability.DEPENDENCY_ANALYSIS})

    def __init__(self, scan_result, failure=None):
        self.scan_result = scan_result
        self.failure = failure
        self.scan_count = 0

    @property
    def available(self):
        return self.failure is None

    async def scan(self):
        self.scan_count += 1
        if self.failure:
            raise self.failure
        return copy.deepcopy(self.scan_result)

    async def fetch(self, request: EvidenceRequest):
        raise NotImplementedError


def coverage(automation="complete", blueprint="complete", other="unavailable"):
    values = [
        SourceCoverageItem("automation", "direct_ha_api", "automation_config", automation),
        SourceCoverageItem("blueprint", "direct_ha_api", "blueprint_source", blueprint),
    ]
    for source in ("script", "scene", "group", "template", "dashboard"):
        values.append(SourceCoverageItem(source, "none", f"{source}_configuration", other))
    values.append(SourceCoverageItem(
        "entity_metadata",
        "direct_ha_api",
        "current_entity_state",
        "complete",
        fallback_occurred=False,
        policy="transitional_direct exact administrative read",
    ))
    return values


def finding(target=TARGET, relation="trigger", path="$.trigger[0].entity_id", source_id="auto-1", source_type="automation", source_entity_id="automation.one"):
    return DependencyFinding(
        evidence_id=evidence_id(target, relation, path, source_id),
        target_entity_id=target,
        source_type=source_type,
        source_id=source_id,
        source_entity_id=source_entity_id,
        source_name="Example automation",
        relation=relation,
        config_path=path,
    )


def scan(findings=None, metadata=None, dynamic=None, coverage_items=None):
    return DependencyScanResult(
        findings or [], dynamic or [], metadata or {}, coverage_items or coverage()
    )


class ExtractionTests(unittest.TestCase):
    def test_entity_validation_and_no_substring_collision(self):
        self.assertTrue(valid_entity_id("sensor.removed_sensor"))
        self.assertFalse(valid_entity_id("sensor"))
        self.assertFalse(valid_entity_id("sensor.bad value"))
        config = {"trigger": [{"platform": "state", "entity_id": "light.kitchen_table"}]}
        findings, _ = extract_document(source_type="automation", source_id="a", config=config)
        self.assertNotIn("light.kitchen", {item.target_entity_id for item in findings})

    def test_nested_roles_and_multiple_roles_are_preserved(self):
        config = {
            "trigger": [{"platform": "state", "entity_id": TARGET}, {"platform": "numeric_state", "entity_id": TARGET}],
            "condition": [{"condition": "state", "entity_id": TARGET}],
            "action": [
                {"service": "light.turn_on", "target": {"entity_id": [TARGET, "light.other"]}},
                {"service": "homeassistant.update_entity", "data": {"entity_id": TARGET}},
                {"choose": [{"conditions": [{"entity_id": TARGET}], "sequence": [{"if": [{"entity_id": TARGET}], "then": []}]}]},
                {"repeat": {"while": [{"entity_id": TARGET}], "sequence": []}},
                {"parallel": [{"sequence": [{"wait_for_trigger": [{"platform": "state", "entity_id": TARGET}]}]}]},
            ],
        }
        findings, _ = extract_document(source_type="automation", source_id="nested", config=config)
        roles = {item.relation for item in findings if item.target_entity_id == TARGET}
        self.assertTrue({"trigger", "condition", "service_target", "action_data", "choose_condition", "if_condition", "repeat_condition", "wait_for_trigger"}.issubset(roles))
        self.assertEqual(len({item.evidence_id for item in findings}), len(findings))

    def test_free_text_fields_do_not_create_dependencies(self):
        config = {
            "alias": TARGET,
            "description": f"mentions {TARGET}",
            "trigger": [],
            "action": [
                {"service": "notify.example", "data": {"message": TARGET, "title": TARGET}},
                {"service": "logbook.log", "data": {"message": TARGET}},
            ],
        }
        findings, _ = extract_document(source_type="automation", source_id="free", config=config)
        self.assertEqual(findings, [])

    def test_template_literal_forms_and_dynamic_references(self):
        templates = [
            "{{ states('sensor.example') }}",
            "{{ states.sensor.example.state }}",
            "{{ is_state('sensor.example', 'on') }}",
            "{{ state_attr('sensor.example', 'value') }}",
            "{{ expand('sensor.example') | list }}",
            "{{ [states('sensor.example'), 'sensor.example'] }}",
        ]
        config = {"condition": [{"condition": "template", "value_template": value} for value in templates]}
        config["condition"].extend([
            {"condition": "template", "value_template": "{{ states('sensor.' ~ room) }}"},
            {"condition": "template", "value_template": "{{ states(variable_entity) }}"},
        ])
        findings, dynamic = extract_document(source_type="automation", source_id="templates", config=config)
        self.assertEqual({item.target_entity_id for item in findings}, {TARGET})
        self.assertGreaterEqual(len(dynamic), 2)
        self.assertTrue(all(len(item.excerpt or "") <= 254 for item in dynamic))

    def test_blueprint_inputs_and_resolved_roles(self):
        config = {"use_blueprint": {"path": "example/test.yaml", "input": {"motion": [TARGET, "sensor.other"], "webhook_secret": "private.value"}}}
        findings, _ = extract_document(source_type="automation", source_id="blueprint-auto", config=config)
        inputs = [item for item in findings if item.relation == "blueprint_input"]
        self.assertEqual({item.target_entity_id for item in inputs}, {TARGET, "sensor.other"})
        self.assertNotIn("private.value", json.dumps([item.public() for item in findings]))
        blueprint = {"trigger": [{"platform": "state", "entity_id": {"__blueprint_input__": "motion"}}]}
        resolved = resolve_blueprint_roles(findings, blueprint, source_id="blueprint-auto")
        self.assertTrue(all(item.relation == "blueprint_resolved_role" for item in resolved))
        self.assertTrue(all("trigger" in item.config_path for item in resolved))

    def test_secret_values_are_not_retained(self):
        secret = "not-a-real-secret-value"
        config = {"condition": [{"condition": "template", "value_template": f"{{{{ states(variable) }}}} {secret}"}]}
        _, dynamic = extract_document(source_type="automation", source_id="secret", source_name=secret, config=config, secret=secret)
        self.assertNotIn(secret, json.dumps([item.__dict__ for item in dynamic]))


class IndexAndAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        METRICS.reset()

    def service(self, result, ttl=300):
        provider = FakeProvider(result)
        return EntityDependencyAnalysisService(DependencyIndex(provider, ttl_seconds=ttl)), provider

    async def test_existing_and_valid_missing_entities(self):
        service, _ = self.service(scan([finding()], {TARGET: {"entity_id": TARGET, "entity_exists": True, "domain": "sensor"}}))
        existing = await service.analyze(entity_id=TARGET)
        self.assertTrue(existing.data["target"]["entity_exists"])
        missing_service, _ = self.service(scan([finding("sensor.removed")]))
        missing = await missing_service.analyze(entity_id="sensor.removed")
        self.assertTrue(missing.data["target"]["entity_exists"] is False)
        self.assertTrue(missing.data["overview"]["possible_stale_reference"])
        self.assertEqual(missing.data["assessment"]["rename_or_removal_status"], "not_safe")

    async def test_malformed_entity_and_domain_only_are_invalid(self):
        service, _ = self.service(scan())
        for value in ("sensor", "bad value", "sensor."):
            with self.subTest(value=value), self.assertRaises(InvalidRequestError):
                await service.analyze(entity_id=value)
        normalized = await service.analyze(entity_id="  SENSOR.EXAMPLE  ")
        self.assertEqual(normalized.data["target"]["entity_id"], TARGET)

    async def test_partial_coverage_produces_cautious_assessment(self):
        partial = coverage(automation="partial")
        partial[0].failed_item_count = 1
        partial[0].warnings = ["One automation failed."]
        service, _ = self.service(scan(coverage_items=partial))
        output = await service.analyze(entity_id=TARGET)
        self.assertTrue(output.partial)
        self.assertEqual(output.data["assessment"]["rename_or_removal_status"], "unknown_due_to_incomplete_coverage")
        self.assertIn("One automation failed.", output.warnings)

    async def test_complete_requested_coverage_can_report_no_references(self):
        service, _ = self.service(scan(coverage_items=coverage(other="complete")))
        output = await service.analyze(entity_id=TARGET, source_types=["automation", "blueprint"])
        self.assertFalse(output.partial)
        self.assertEqual(output.data["assessment"]["rename_or_removal_status"], "no_references_detected_within_coverage")
        statuses = {item["source_type"]: item["completeness"] for item in output.data["source_coverage"]}
        self.assertEqual(statuses["dashboard"], "not_requested")

    async def test_pagination_is_stable_without_duplicates_and_cursor_errors(self):
        items = [finding(relation="condition", path=f"$.condition[{index}].entity_id", source_id=f"a-{index}") for index in range(5)]
        service, provider = self.service(scan(items))
        first = await service.analyze(entity_id=TARGET, detail_level="standard", limit=2)
        cursor = first.data["pagination"]["next_cursor"]
        second = await service.analyze(entity_id=TARGET, detail_level="standard", limit=2, cursor=cursor)
        first_ids = {item["evidence_id"] for item in first.data["findings"]}
        second_ids = {item["evidence_id"] for item in second.data["findings"]}
        self.assertFalse(first_ids & second_ids)
        self.assertEqual(provider.scan_count, 1)
        with self.assertRaises(GovernanceError) as invalid:
            await service.analyze(entity_id=TARGET, detail_level="standard", limit=2, cursor="invalid")
        self.assertEqual(invalid.exception.code, ErrorCode.INVALID_CURSOR)
        with self.assertRaises(GovernanceError) as stale:
            await service.analyze(entity_id=TARGET, detail_level="standard", limit=2, cursor=cursor, refresh_index=True)
        self.assertEqual(stale.exception.code, ErrorCode.STALE_CURSOR)

    async def test_cache_hit_refresh_ttl_and_invalidation(self):
        service, provider = self.service(scan([finding()]), ttl=60)
        first = await service.analyze(entity_id=TARGET)
        second = await service.analyze(entity_id=TARGET)
        self.assertFalse(first.data["index"]["cache_hit"])
        self.assertTrue(second.data["index"]["cache_hit"])
        service.index.invalidate()
        await service.analyze(entity_id=TARGET)
        await service.analyze(entity_id=TARGET, refresh_index=True)
        service.index.snapshot.built_at_monotonic -= 61
        await service.analyze(entity_id=TARGET)
        self.assertEqual(provider.scan_count, 4)
        metrics = METRICS.snapshot()["dependency_analysis"]
        self.assertGreaterEqual(metrics["index_cache_hits"], 1)
        self.assertGreaterEqual(metrics["index_invalidations"], 1)
        self.assertNotIn(TARGET, json.dumps(metrics))

    async def test_requested_summary_limit_is_honored_and_explicit(self):
        many = [
            finding(path=f"$.action[{index}].target.entity_id", source_id=f"auto-{index}")
            for index in range(30)
        ]
        service, _ = self.service(scan(many))
        output = await service.analyze(entity_id=TARGET, detail_level="summary", limit=20)
        pagination = output.data["pagination"]
        self.assertEqual(len(output.data["findings"]), 20)
        self.assertEqual(pagination["requested_limit"], 20)
        self.assertEqual(pagination["effective_limit"], 20)
        self.assertEqual(pagination["maximum_limit"], 100)
        self.assertFalse(pagination["clamped"])
        self.assertIsNone(pagination["clamp_reason"])

    async def test_cache_hit_separates_current_and_provenance_timing(self):
        timed_coverage = coverage()
        timed_coverage[0].duration_ms = 33_000.0
        service, _ = self.service(scan([finding()], coverage_items=timed_coverage))
        first = await service.analyze(entity_id=TARGET)
        second = await service.analyze(entity_id=TARGET)
        first_source = first.data["source_coverage"][0]
        cached_source = second.data["source_coverage"][0]
        self.assertFalse(first_source["cached_provenance"])
        self.assertEqual(first_source["duration_ms"], 33_000.0)
        self.assertTrue(cached_source["cached_provenance"])
        self.assertEqual(cached_source["duration_ms"], 0.0)
        self.assertEqual(cached_source["index_build_duration_ms"], 33_000.0)
        self.assertGreaterEqual(first.data["index"]["original_build_duration_ms"], 0.0)
        self.assertLess(second.data["index"]["current_request_duration_ms"], 33_000.0)
        self.assertLess(second.data["index"]["lookup_duration_ms"], 33_000.0)

    async def test_dependency_counter_semantics_are_stable_on_cache_hits(self):
        dynamic = [DynamicReference("dyn-1", "automation", "a", "$.action", "Dynamic target")]
        many = [
            finding(path=f"$.action[{index}].target.entity_id", source_id=f"auto-{index}")
            for index in range(25)
        ]
        service, _ = self.service(scan(many, dynamic=dynamic))
        await service.analyze(entity_id=TARGET, limit=20)
        await service.analyze(entity_id=TARGET, limit=20)
        metrics = METRICS.snapshot()["dependency_analysis"]
        self.assertEqual(metrics["current_index_unresolved_dynamic_reference_count"], 1)
        self.assertEqual(metrics["findings_truncation_event_count"], 2)
        self.assertEqual(
            metrics["counter_semantics"]["current_index_unresolved_dynamic_reference_count"],
            "current_index_state",
        )

    async def test_indirect_group_chain_is_bounded_and_no_action_causality(self):
        membership = finding(source_type="group", source_id="g", source_entity_id="group.example", relation="group_member", path="$.entities")
        inbound = finding(target="group.example", source_id="uses-group", relation="condition", path="$.condition.entity_id")
        action = finding(target=TARGET, source_id="action-writer", source_entity_id="automation.writer", relation="action_target", path="$.action.target")
        service, _ = self.service(scan([membership, inbound, action], coverage_items=coverage(other="complete")))
        output = await service.analyze(entity_id=TARGET, include_indirect=True, max_depth=2, source_types=["automation", "group"])
        indirect = [item for item in output.data["findings"] if not item["direct"]]
        self.assertEqual(len(indirect), 1)
        self.assertTrue(indirect[0]["evidence_path"])
        self.assertNotIn("automation.writer", json.dumps(indirect))

    async def test_summary_and_evidence_outputs_are_bounded(self):
        huge = [finding(path=f"$.action[{index}].target.entity_id", source_id=f"auto-{index}") for index in range(500)]
        service, _ = self.service(scan(huge))
        summary = await service.analyze(entity_id=TARGET, detail_level="summary", limit=100)
        evidence = await service.analyze(entity_id=TARGET, detail_level="evidence", limit=100)
        self.assertEqual(len(summary.data["findings"]), 100)
        self.assertLess(len(json.dumps(summary.data)), 60_000)
        self.assertLess(len(json.dumps(evidence.data)), 60_000)
        self.assertNotIn("proposed_config", json.dumps(summary.data))

    async def test_provider_failure_is_visible(self):
        provider = FakeProvider(scan(), failure=RuntimeError("raw private provider error"))
        service = EntityDependencyAnalysisService(DependencyIndex(provider))
        with self.assertRaises(GovernanceError) as raised:
            await service.analyze(entity_id=TARGET)
        self.assertEqual(raised.exception.code, ErrorCode.ANALYSIS_UNAVAILABLE)
        self.assertNotIn("raw private", raised.exception.safe_message)


class DirectProviderTests(unittest.IsolatedAsyncioTestCase):
    class Rest:
        def __init__(self):
            self.config_calls = 0

        async def request(self, method, path):
            if path == "/states":
                return [
                    {"entity_id": TARGET, "state": "on", "attributes": {"friendly_name": "Target"}},
                    {"entity_id": "automation.good", "state": "on", "attributes": {"id": "good", "friendly_name": "Good"}},
                    {"entity_id": "automation.bad", "state": "on", "attributes": {"id": "bad", "friendly_name": "Bad"}},
                ]
            self.config_calls += 1
            if path.endswith("/bad"):
                raise RuntimeError("bounded fake failure")
            return {
                "trigger": [{"platform": "state", "entity_id": TARGET}],
                "action": [],
                "use_blueprint": {"path": "missing/test.yaml", "input": {"motion": TARGET}},
            }

    class WebSocket:
        async def command(self, payload):
            return [{"entity_id": TARGET, "platform": "test", "device_id": "device-1"}]

    async def test_bounded_concurrent_automation_scan_is_partial_on_one_failure(self):
        rest = self.Rest()
        provider = DirectHaDependencyProvider(rest, self.WebSocket(), secret="not-a-real-secret", concurrency=2)
        result = await provider.scan()
        statuses = {item.source_type: item for item in result.coverage}
        self.assertEqual(statuses["automation"].completeness, "partial")
        self.assertEqual(statuses["automation"].failed_item_count, 1)
        self.assertEqual(statuses["blueprint"].completeness, "partial")
        self.assertTrue(any(item.relation == "blueprint_input" for item in result.findings))
        self.assertEqual(statuses["dashboard"].completeness, "unavailable")
        self.assertTrue(result.target_metadata[TARGET]["entity_exists"])


class ToolContractTests(unittest.TestCase):
    def test_tool_is_registered_once_and_schema_is_exact(self):
        tools = get_registered_server()._tool_manager.list_tools()
        matches = [tool for tool in tools if tool.name == "entity_dependency_analysis"]
        self.assertEqual(len(tools), 38)
        self.assertEqual(len(matches), 1)
        schema = matches[0].parameters
        self.assertEqual(
            set(schema["properties"]),
            {"entity_id", "detail_level", "include_indirect", "max_depth", "source_types", "limit", "cursor", "refresh_index"},
        )
        self.assertEqual(schema["required"], ["entity_id"])
        self.assertEqual(schema["properties"]["detail_level"]["default"], "summary")
        self.assertEqual(schema["properties"]["limit"]["default"], 50)
        self.assertEqual(schema["properties"]["max_depth"]["default"], 2)
        self.assertEqual(schema["properties"]["max_depth"]["minimum"], 1)
        self.assertEqual(schema["properties"]["max_depth"]["maximum"], 3)
        self.assertEqual(schema["properties"]["limit"]["maximum"], 100)
        self.assertEqual(
            set(schema["properties"]["detail_level"]["enum"]),
            {"summary", "standard", "evidence"},
        )

    def test_direct_upsert_invalidates_but_prohibited_and_delegated_writes_do_not(self):
        async def exercise():
            with patch.object(compatibility, "rest", new=AsyncMock(side_effect=[{"ok": True}, {"trigger": [], "action": []}])), patch.object(
                compatibility.DEPENDENCY_ANALYSIS, "invalidate"
            ) as invalidate:
                await compatibility.upsert_automation("test", '{"trigger": [], "action": []}')
                invalidate.assert_called_once()
            with patch.object(compatibility, "rest", new=AsyncMock(return_value={"ok": True})), patch.object(
                compatibility.DEPENDENCY_ANALYSIS, "invalidate"
            ) as invalidate:
                deleted = json.loads(await compatibility.delete_automation("test", confirm=True))
                reloaded = json.loads(await compatibility.reload_domain("automation"))
                self.assertEqual(deleted["error_code"], "provider_prohibited")
                self.assertEqual(reloaded["error_code"], "provider_unavailable")
                invalidate.assert_not_called()
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
