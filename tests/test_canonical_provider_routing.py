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
from ha_mcp_engineering.providers import (  # noqa: E402
    CANONICAL_DISPATCHER,
    EngineeringEvidenceProvider,
    ProviderCompleteness,
    ProviderCoverage,
    ProviderError,
    ProviderFailureCategory,
    ProviderResult,
    StandardHaMcpGateway,
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

    async def test_delegated_tool_uses_available_standard_provider(self):
        provider = FakeStandardProvider(data={"entity_id": "sensor.example", "state": "on"})
        CANONICAL_DISPATCHER.standard_provider = provider
        with patch.object(compatibility, "rest", new=AsyncMock()) as direct:
            payload = json.loads(await compatibility.get_entity("sensor.example"))
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["state"], "on")
        self.assertEqual(payload["metadata"]["routing"]["provider"], "standard_ha_mcp")
        self.assertEqual(provider.requests[0].query["operation"], "get_entity")
        direct.assert_not_awaited()
        metrics = METRICS.snapshot()["provider_routing"]
        self.assertEqual(metrics["requests_by_provider"]["standard_ha_mcp"], 1)
        self.assertEqual(metrics["successful_requests_by_provider"]["standard_ha_mcp"], 1)

    async def test_delegated_unavailable_is_structured_and_never_direct(self):
        CANONICAL_DISPATCHER.standard_provider = StandardHaMcpGateway()
        before = Counter(METRICS.snapshot()["provider_routing"]["failures_by_provider"])
        with patch.object(compatibility, "rest", new=AsyncMock()) as direct:
            payload = json.loads(await compatibility.get_entity("sensor.example"))
        self.assertFalse(payload["success"])
        self.assertEqual(payload["operation"], "get_entity")
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

    async def test_direct_required_exception_is_attributed_to_direct_provider(self):
        with patch.object(compatibility, "rest", new=AsyncMock(return_value={"result": "valid", "errors": None})):
            payload = json.loads(await compatibility.check_config())
        self.assertTrue(payload["success"])
        self.assertEqual(payload["metadata"]["routing"]["classification"], "direct_ha_required")
        self.assertEqual(payload["metadata"]["routing"]["provider"], "direct_ha_api")

    async def test_partial_standard_result_is_enveloped_and_counted(self):
        provider = FakeStandardProvider(
            data={"areas": []},
            completeness=ProviderCompleteness.PARTIAL,
        )
        CANONICAL_DISPATCHER.standard_provider = provider
        payload = json.loads(await compatibility.list_areas())
        self.assertTrue(payload["success"])
        self.assertEqual(payload["metadata"]["source_coverage"][0]["completeness"], "partial")
        self.assertEqual(METRICS.snapshot()["provider_routing"]["partial_results"], 1)

    async def test_provider_timeout_is_stable_and_retryable(self):
        class TimeoutProvider(FakeStandardProvider):
            async def fetch(self, request):
                raise TimeoutError

        CANONICAL_DISPATCHER.standard_provider = TimeoutProvider()
        payload = json.loads(await compatibility.search_services("light"))
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "provider_timeout")
        self.assertTrue(payload["retryable"])

    async def test_bounded_routed_response_records_truncation(self):
        CANONICAL_DISPATCHER.standard_provider = FakeStandardProvider(
            data={"services": ["x" * 2000]}
        )
        rendered = await CANONICAL_DISPATCHER.execute(
            "list_services",
            AsyncMock(),
            arguments={},
            response_limit=300,
        )
        self.assertIn("... [truncated at 300 chars]", rendered)
        self.assertEqual(
            METRICS.snapshot()["provider_routing"]["evidence_truncation_count"],
            1,
        )


class ToolListSerializationTests(unittest.TestCase):
    def test_all_33_registered_tools_have_serializable_json_schemas(self):
        tools = get_registered_server()._tool_manager.list_tools()
        names = [tool.name for tool in tools]
        self.assertEqual(len(names), 33)
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("entity_dependency_analysis", names)
        for tool in tools:
            json.dumps(tool.parameters)


if __name__ == "__main__":
    unittest.main()
