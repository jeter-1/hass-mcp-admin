import asyncio
from collections import Counter
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.observability import METRICS  # noqa: E402
from ha_mcp_engineering.errors import HomeAssistantUnavailableError  # noqa: E402
from ha_mcp_engineering.providers import (  # noqa: E402
    CANONICAL_DISPATCHER,
    EngineeringEvidenceProvider,
    ProviderCompleteness,
    ProviderCoverage,
    ProviderError,
    ProviderFailureCategory,
    ProviderResult,
    StandardHaMcpGateway,
    DIRECT_HA_READ_POLICIES,
    direct_ha_policy_for_tool,
    routing_for_tool,
)
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.tools import compatibility, get_registered_server  # noqa: E402


class FakeStandardProvider(EngineeringEvidenceProvider):
    provider_id = "standard_ha_mcp"
    capabilities = frozenset()

    def __init__(self, *, data=None, completeness=ProviderCompleteness.COMPLETE, failure=None):
        self.data = data
        self.completeness = completeness
        self.failure = failure
        self.requests = []

    @property
    def available(self):
        return self.completeness != ProviderCompleteness.UNAVAILABLE

    async def fetch(self, request):
        self.requests.append(request)
        return ProviderResult(
            provider_id=self.provider_id,
            capability=request.capability,
            completeness=self.completeness,
            failure=self.failure,
            coverage=ProviderCoverage(1, 1 if self.completeness == ProviderCompleteness.COMPLETE else 0),
            data=self.data,
        )


class CanonicalRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        METRICS.reset()
        self.previous = CANONICAL_DISPATCHER.standard_provider
        self.telemetry, self.token = begin_request("canonical-routing-request-123")

    async def asyncTearDown(self):
        CANONICAL_DISPATCHER.standard_provider = self.previous
        end_request(self.token)

    async def test_remaining_standard_preferred_tool_uses_available_provider(self):
        provider = FakeStandardProvider(data={"count": 1, "results": [{"entity_id": "sensor.example"}]})
        CANONICAL_DISPATCHER.standard_provider = provider
        with patch.object(compatibility, "rest", new=AsyncMock()) as direct:
            payload = json.loads(await compatibility.search_entities("example"))
        self.assertTrue(payload["success"])
        self.assertEqual(payload["metadata"]["routing"]["provider"], "standard_ha_mcp")
        self.assertEqual(provider.requests[0].query["operation"], "search_entities")
        direct.assert_not_awaited()
        metrics = METRICS.snapshot()["provider_routing"]
        self.assertEqual(metrics["requests_by_provider"]["standard_ha_mcp"], 1)
        self.assertEqual(metrics["successful_requests_by_provider"]["standard_ha_mcp"], 1)

    async def test_remaining_standard_preferred_unavailable_never_falls_back(self):
        CANONICAL_DISPATCHER.standard_provider = StandardHaMcpGateway()
        before = Counter(METRICS.snapshot()["provider_routing"]["failures_by_provider"])
        with patch.object(compatibility, "rest", new=AsyncMock()) as direct:
            payload = json.loads(await compatibility.search_entities("example"))
        self.assertFalse(payload["success"])
        self.assertEqual(payload["operation"], "search_entities")
        self.assertEqual(payload["error_code"], "provider_unavailable")
        self.assertFalse(payload["retryable"])
        self.assertEqual(payload["metadata"]["routing"]["provider"], "standard_ha_mcp")
        self.assertEqual(payload["metadata"]["source_coverage"][0]["completeness"], "unavailable")
        self.assertIn("timing", payload)
        self.assertEqual(payload["request_id"], "canonical-routing-request-123")
        direct.assert_not_awaited()
        metrics = METRICS.snapshot()["provider_routing"]
        self.assertEqual(metrics["failures_by_provider"]["standard_ha_mcp"] - before["standard_ha_mcp"], 1)
        self.assertEqual(metrics["prohibited_fallback_attempts"], 1)

    async def test_transitional_tool_uses_direct_provider_and_updates_counters(self):
        states = [{"entity_id": "automation.example", "state": "on", "attributes": {"id": "example", "friendly_name": "Example"}}]
        with patch.object(compatibility, "rest", new=AsyncMock(return_value=states)):
            payload = json.loads(await compatibility.list_automations())
        self.assertTrue(payload["success"])
        self.assertEqual(payload["operation"], "list_automations")
        self.assertEqual(payload["metadata"]["routing"]["classification"], "transitional_direct")
        self.assertEqual(payload["metadata"]["routing"]["provider"], "direct_ha_api")
        self.assertEqual(payload["data"]["count"], 1)
        metrics = METRICS.snapshot()["provider_routing"]
        self.assertEqual(metrics["requests_by_provider"]["direct_ha_api"], 1)
        self.assertEqual(metrics["successful_requests_by_provider"]["direct_ha_api"], 1)

    async def test_get_entity_uses_exact_direct_read_policy(self):
        entity = {"entity_id": "sensor.example", "state": "on", "attributes": {"unit": "safe"}}
        with patch.object(compatibility, "rest", new=AsyncMock(return_value=entity)) as direct:
            payload = json.loads(await compatibility.get_entity("sensor.example"))
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"], entity)
        direct.assert_awaited_once_with("GET", "/states/sensor.example")
        self._assert_capability_truth(payload, "exact_entity_state_read")

    async def test_get_entity_rejects_noncanonical_path_input(self):
        with patch.object(compatibility, "rest", new=AsyncMock()) as direct:
            payload = json.loads(await compatibility.get_entity("sensor.example/../config"))
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "invalid_request")
        direct.assert_not_awaited()

    async def test_list_areas_uses_complete_registry_direct_policy(self):
        areas = [{"area_id": "kitchen", "name": "Kitchen"}]
        with patch.object(compatibility, "ws_command", new=AsyncMock(return_value=areas)) as direct:
            payload = json.loads(await compatibility.list_areas())
        self.assertEqual(payload["data"], areas)
        direct.assert_awaited_once_with({"type": "config/area_registry/list"})
        self._assert_capability_truth(payload, "complete_area_registry_read")

    async def test_search_services_is_bounded_and_direct(self):
        catalog = [{"domain": "light", "services": {f"action_{i}": {"description": "match"} for i in range(120)}}]
        with patch.object(compatibility, "rest", new=AsyncMock(return_value=catalog)):
            payload = json.loads(await compatibility.search_services("light", limit=200))
        self.assertEqual(payload["data"]["count"], 100)
        self.assertEqual(payload["data"]["effective_limit"], 100)
        self.assertTrue(payload["data"]["truncated"])
        self._assert_capability_truth(payload, "bounded_service_catalog_search", "partial")

    async def test_list_services_is_safely_bounded_and_direct(self):
        catalog = [{"domain": "light", "services": {f"action_{i}": {"fields": {"x": {}}} for i in range(75)}}]
        with patch.object(compatibility, "rest", new=AsyncMock(return_value=catalog)):
            payload = json.loads(await compatibility.list_services("light"))
        self.assertEqual(payload["data"]["returned_service_count"], 50)
        self.assertEqual(payload["data"]["total_service_count"], 75)
        self.assertTrue(payload["data"]["truncated"])
        self._assert_capability_truth(payload, "bounded_service_schema_read", "partial")

    async def test_direct_read_failure_is_counted_and_never_mislabeled(self):
        before = Counter(METRICS.snapshot()["provider_routing"]["failures_by_provider"])
        with patch.object(
            compatibility,
            "rest",
            new=AsyncMock(side_effect=HomeAssistantUnavailableError()),
        ):
            payload = json.loads(await compatibility.get_entity("sensor.example"))
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "home_assistant_unavailable")
        self.assertEqual(payload["metadata"]["routing"]["provider"], "direct_ha_api")
        self.assertEqual(
            payload["metadata"]["source_coverage"][0]["provider_capability"],
            "current_entity_state",
        )
        metrics = METRICS.snapshot()["provider_routing"]
        self.assertEqual(metrics["failures_by_provider"]["direct_ha_api"] - before["direct_ha_api"], 1)
        self.assertNotIn("standard_ha_mcp", payload["metadata"]["routing"]["provider"])

    async def test_four_administrative_reads_increment_direct_counters_by_four(self):
        catalog = [{"domain": "light", "services": {"turn_on": {"fields": {}}}}]
        rest = AsyncMock(
            side_effect=[
                {"entity_id": "sensor.example", "state": "on", "attributes": {}},
                catalog,
                catalog,
            ]
        )
        with patch.object(compatibility, "rest", new=rest), patch.object(
            compatibility,
            "ws_command",
            new=AsyncMock(return_value=[{"area_id": "kitchen", "name": "Kitchen"}]),
        ):
            results = [
                json.loads(await compatibility.get_entity("sensor.example")),
                json.loads(await compatibility.list_areas()),
                json.loads(await compatibility.search_services("light.turn_on")),
                json.loads(await compatibility.list_services("light")),
            ]
        self.assertTrue(all(item["success"] for item in results))
        self.assertEqual(
            {item["metadata"]["routing"]["provider"] for item in results},
            {"direct_ha_api"},
        )
        metrics = METRICS.snapshot()["provider_routing"]
        self.assertEqual(metrics["requests_by_provider"]["direct_ha_api"], 4)
        self.assertEqual(metrics["successful_requests_by_provider"]["direct_ha_api"], 4)
        self.assertEqual(metrics["requests_by_provider"].get("standard_ha_mcp", 0), 0)

    async def test_direct_read_policy_does_not_expand_write_boundaries(self):
        phase3c_reads = {"get_entity", "list_areas", "search_services", "list_services"}
        self.assertTrue(phase3c_reads.issubset(DIRECT_HA_READ_POLICIES))
        self.assertEqual(
            set(DIRECT_HA_READ_POLICIES) - phase3c_reads,
            {"get_error_log"},
        )
        self.assertEqual(DIRECT_HA_READ_POLICIES["get_error_log"]["access"], "read")
        for name in ("call_service", "delete_automation", "reload_domain"):
            with self.subTest(name=name):
                self.assertIsNone(direct_ha_policy_for_tool(name))
                self.assertNotEqual(routing_for_tool(name).preferred_provider, "direct_ha_api")
        with patch.object(compatibility, "rest", new=AsyncMock()) as direct:
            service = json.loads(await compatibility.call_service("light", "turn_on", "{}"))
            reload_result = json.loads(await compatibility.reload_domain("automation"))
            deletion = json.loads(await compatibility.delete_automation("fixture", confirm=True))
        self.assertEqual(service["error_code"], "provider_unavailable")
        self.assertEqual(reload_result["error_code"], "provider_unavailable")
        self.assertEqual(deletion["error_code"], "provider_prohibited")
        direct.assert_not_awaited()

    async def test_direct_required_exception_is_attributed_to_direct_provider(self):
        with patch.object(compatibility, "rest", new=AsyncMock(return_value={"result": "valid", "errors": None})):
            payload = json.loads(await compatibility.check_config())
        self.assertTrue(payload["success"])
        self.assertEqual(payload["metadata"]["routing"]["classification"], "direct_ha_required")
        self.assertEqual(payload["metadata"]["routing"]["provider"], "direct_ha_api")

    async def test_partial_standard_result_is_enveloped_and_counted(self):
        provider = FakeStandardProvider(
            data={"results": []},
            completeness=ProviderCompleteness.PARTIAL,
        )
        CANONICAL_DISPATCHER.standard_provider = provider
        payload = json.loads(await compatibility.search_entities())
        self.assertTrue(payload["success"])
        self.assertEqual(payload["metadata"]["source_coverage"][0]["completeness"], "partial")
        self.assertEqual(METRICS.snapshot()["provider_routing"]["partial_results"], 1)

    async def test_provider_timeout_is_stable_and_retryable(self):
        class TimeoutProvider(FakeStandardProvider):
            async def fetch(self, request):
                raise TimeoutError

        CANONICAL_DISPATCHER.standard_provider = TimeoutProvider()
        payload = json.loads(await compatibility.search_entities("light"))
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "provider_timeout")
        self.assertTrue(payload["retryable"])

    async def test_bounded_routed_response_records_truncation(self):
        CANONICAL_DISPATCHER.standard_provider = FakeStandardProvider(
            data={"services": ["x" * 2000]}
        )
        rendered = await CANONICAL_DISPATCHER.execute(
            "search_entities",
            AsyncMock(),
            arguments={},
            response_limit=300,
        )
        self.assertIn("... [truncated at 300 chars]", rendered)
        self.assertEqual(
            METRICS.snapshot()["provider_routing"]["evidence_truncation_count"],
            1,
        )

    def _assert_capability_truth(self, payload, policy_id, completeness="complete"):
        self.assertTrue(payload["success"])
        routing = payload["metadata"]["routing"]
        self.assertEqual(routing["lifecycle_status"], "transitional")
        self.assertEqual(routing["classification"], "transitional_direct")
        self.assertEqual(routing["provider"], "direct_ha_api")
        self.assertFalse(routing["fallback_occurred"])
        self.assertEqual(routing["direct_access_policy"]["policy_id"], policy_id)
        coverage = payload["metadata"]["source_coverage"][0]
        self.assertEqual(coverage["provider"], "direct_ha_api")
        self.assertEqual(coverage["completeness"], completeness)
        metrics = METRICS.snapshot()["provider_routing"]
        self.assertGreaterEqual(metrics["requests_by_provider"]["direct_ha_api"], 1)
        self.assertGreaterEqual(metrics["successful_requests_by_provider"]["direct_ha_api"], 1)
        self.assertEqual(metrics["successful_requests_by_provider"].get("standard_ha_mcp", 0), 0)


class ToolListSerializationTests(unittest.TestCase):
    def test_all_37_registered_tools_have_serializable_json_schemas(self):
        tools = get_registered_server()._tool_manager.list_tools()
        names = [tool.name for tool in tools]
        self.assertEqual(len(names), 37)
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("entity_dependency_analysis", names)
        self.assertIn("automation_reliability_analysis", names)
        for tool in tools:
            json.dumps(tool.parameters)


if __name__ == "__main__":
    unittest.main()
