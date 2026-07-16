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
    StandardHaMcpGateway,
    DIRECT_HA_READ_POLICIES,
    DIRECT_HA_TOOL_EXCEPTIONS,
    direct_ha_exception_for_tool,
    direct_ha_policy_for_tool,
    routing_for_tool,
)
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.tools import compatibility, get_registered_server  # noqa: E402


class CanonicalRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        METRICS.reset()
        self.previous = CANONICAL_DISPATCHER.standard_provider
        self.telemetry, self.token = begin_request("canonical-routing-request-123")

    async def asyncTearDown(self):
        CANONICAL_DISPATCHER.standard_provider = self.previous
        end_request(self.token)

    async def test_search_entities_routes_direct_when_standard_is_unavailable(self):
        gateway = StandardHaMcpGateway()
        self.assertFalse(gateway.available)
        CANONICAL_DISPATCHER.standard_provider = gateway
        states = [
            {
                "entity_id": "sensor.example",
                "state": "on",
                "attributes": {"friendly_name": "Example", "secret_attribute": "omit"},
            }
        ]
        with patch.object(gateway, "fetch", new=AsyncMock()) as standard, patch.object(
            compatibility, "rest", new=AsyncMock(return_value=states)
        ) as direct:
            payload = json.loads(await compatibility.search_entities("example"))
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["count"], 1)
        self.assertFalse(payload["data"]["truncated"])
        direct.assert_awaited_once_with("GET", "/states")
        standard.assert_not_awaited()
        self._assert_capability_truth(payload, "bounded_entity_state_search")
        metrics = METRICS.snapshot()["provider_routing"]
        self.assertEqual(metrics["requests_by_provider"]["direct_ha_api"], 1)
        self.assertEqual(metrics["successful_requests_by_provider"]["direct_ha_api"], 1)
        self.assertEqual(metrics["requests_by_provider"].get("standard_ha_mcp", 0), 0)
        self.assertEqual(metrics["fallback_attempts"], 0)
        self.assertEqual(metrics["prohibited_fallback_attempts"], 0)

    async def test_search_entities_filters_case_insensitively_orders_and_slims_results(self):
        states = [
            {
                "entity_id": "sensor.z_garage",
                "state": "open",
                "attributes": {"friendly_name": "Other", "arbitrary": "omit"},
            },
            {"entity_id": "cover.alpha", "state": "closed", "attributes": {"friendly_name": "Garage Door"}},
            {"entity_id": "light.unmatched", "state": "on", "attributes": {"friendly_name": "Kitchen"}},
            None,
            {"entity_id": 5, "attributes": {}},
            {"entity_id": "malformed", "attributes": {}},
        ]
        with patch.object(compatibility, "rest", new=AsyncMock(return_value=states)) as direct:
            payload = json.loads(await compatibility.search_entities("  GARAGE  "))
        self.assertEqual(
            [item["entity_id"] for item in payload["data"]["results"]],
            ["cover.alpha", "sensor.z_garage"],
        )
        for result in payload["data"]["results"]:
            self.assertEqual(set(result), {"entity_id", "state", "friendly_name"})
        self.assertNotIn("arbitrary", json.dumps(payload["data"]))
        direct.assert_awaited_once_with("GET", "/states")

    async def test_search_entities_applies_exact_domain_with_empty_query(self):
        states = [
            {"entity_id": "cover.garage", "state": "open", "attributes": {}},
            {"entity_id": "covering.garage", "state": "open", "attributes": {}},
            {"entity_id": "sensor.garage", "state": "open", "attributes": {}},
        ]
        with patch.object(compatibility, "rest", new=AsyncMock(return_value=states)):
            payload = json.loads(await compatibility.search_entities("", "  COVER  "))
        self.assertEqual(payload["data"]["count"], 1)
        self.assertEqual(payload["data"]["results"][0]["entity_id"], "cover.garage")
        self.assertFalse(payload["data"]["truncated"])

    async def test_search_entities_no_matches_is_complete(self):
        states = [{"entity_id": "sensor.example", "state": "on", "attributes": {}}]
        with patch.object(compatibility, "rest", new=AsyncMock(return_value=states)):
            payload = json.loads(await compatibility.search_entities("does-not-exist"))
        self.assertEqual(payload["data"], {"count": 0, "results": [], "truncated": False})
        self.assertEqual(payload["metadata"]["source_coverage"][0]["completeness"], "complete")

    async def test_search_entities_limit_one_is_partial_without_provider_failure(self):
        states = [
            {"entity_id": "sensor.z", "state": "on", "attributes": {}},
            {"entity_id": "sensor.a", "state": "off", "attributes": {}},
        ]
        with patch.object(compatibility, "rest", new=AsyncMock(return_value=states)) as direct:
            payload = json.loads(await compatibility.search_entities(limit=1))
        self.assertEqual(payload["data"]["count"], 1)
        self.assertEqual(payload["data"]["results"][0]["entity_id"], "sensor.a")
        self.assertTrue(payload["data"]["truncated"])
        self.assertEqual(payload["metadata"]["source_coverage"][0]["completeness"], "partial")
        direct.assert_awaited_once_with("GET", "/states")
        metrics = METRICS.snapshot()["provider_routing"]
        self.assertEqual(metrics["requests_by_provider"]["direct_ha_api"], 1)
        self.assertEqual(metrics["successful_requests_by_provider"]["direct_ha_api"], 1)
        self.assertEqual(metrics["failures_by_provider"].get("direct_ha_api", 0), 0)
        self.assertEqual(metrics["partial_results"], 1)
        self.assertEqual(metrics["fallback_attempts"], 0)
        self.assertEqual(metrics["prohibited_fallback_attempts"], 0)

    async def test_search_entities_validation_precedes_ha_and_provider_accounting(self):
        before = METRICS.snapshot()["provider_routing"]
        invalid_inputs = (
            {"limit": 0},
            {"limit": 101},
            {"limit": True},
            {"domain": "sensor.bad"},
            {"domain": "sensor-bad"},
        )
        with patch.object(compatibility, "rest", new=AsyncMock()) as direct:
            for arguments in invalid_inputs:
                with self.subTest(arguments=arguments):
                    payload = json.loads(await compatibility.search_entities(**arguments))
                    self.assertFalse(payload["success"])
                    self.assertEqual(payload["error_code"], "invalid_request")
        direct.assert_not_awaited()
        after = METRICS.snapshot()["provider_routing"]
        self.assertEqual(after["requests_by_provider"], before["requests_by_provider"])
        self.assertEqual(after["successful_requests_by_provider"], before["successful_requests_by_provider"])
        self.assertEqual(after["failures_by_provider"], before["failures_by_provider"])

    async def test_search_entities_malformed_inventory_is_stable_provider_failure(self):
        with patch.object(compatibility, "rest", new=AsyncMock(return_value={"not": "a list"})) as direct:
            payload = json.loads(await compatibility.search_entities())
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "home_assistant_api_error")
        self.assertEqual(payload["metadata"]["routing"]["provider"], "direct_ha_api")
        direct.assert_awaited_once_with("GET", "/states")
        metrics = METRICS.snapshot()["provider_routing"]
        self.assertEqual(metrics["requests_by_provider"]["direct_ha_api"], 1)
        self.assertEqual(metrics["failures_by_provider"]["direct_ha_api"], 1)

    async def test_search_entities_upstream_failure_is_counted_once(self):
        with patch.object(
            compatibility,
            "rest",
            new=AsyncMock(side_effect=HomeAssistantUnavailableError()),
        ) as direct:
            payload = json.loads(await compatibility.search_entities("garage"))
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "home_assistant_unavailable")
        direct.assert_awaited_once_with("GET", "/states")
        metrics = METRICS.snapshot()["provider_routing"]
        self.assertEqual(metrics["requests_by_provider"]["direct_ha_api"], 1)
        self.assertEqual(metrics["failures_by_provider"]["direct_ha_api"], 1)
        self.assertEqual(metrics["fallback_attempts"], 0)
        self.assertEqual(metrics["prohibited_fallback_attempts"], 0)

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

    async def test_five_transitional_discovery_reads_increment_direct_counters_by_five(self):
        catalog = [{"domain": "light", "services": {"turn_on": {"fields": {}}}}]
        rest = AsyncMock(
            side_effect=[
                {"entity_id": "sensor.example", "state": "on", "attributes": {}},
                [{"entity_id": "sensor.example", "state": "on", "attributes": {}}],
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
                json.loads(await compatibility.search_entities("example")),
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
        self.assertEqual(metrics["requests_by_provider"]["direct_ha_api"], 5)
        self.assertEqual(metrics["successful_requests_by_provider"]["direct_ha_api"], 5)
        self.assertEqual(metrics["requests_by_provider"].get("standard_ha_mcp", 0), 0)

    async def test_direct_read_policy_does_not_expand_write_boundaries(self):
        phase3c_reads = {
            "search_entities",
            "get_entity",
            "list_areas",
            "search_services",
            "list_services",
        }
        self.assertTrue(phase3c_reads.issubset(DIRECT_HA_READ_POLICIES))
        self.assertEqual(
            set(DIRECT_HA_TOOL_EXCEPTIONS) - set(DIRECT_HA_READ_POLICIES),
            {"upsert_automation"},
        )
        self.assertEqual(DIRECT_HA_READ_POLICIES["get_error_log"]["access"], "read")
        self.assertEqual(
            DIRECT_HA_READ_POLICIES["search_entities"]["policy_id"],
            "bounded_entity_state_search",
        )
        self.assertTrue(direct_ha_exception_for_tool("search_entities", access="read"))
        self.assertFalse(direct_ha_exception_for_tool("search_entities", access="write"))
        self.assertTrue(direct_ha_exception_for_tool("get_entity", access="read"))
        self.assertFalse(direct_ha_exception_for_tool("get_entity", access="write"))
        self.assertFalse(direct_ha_exception_for_tool("upsert_automation"))
        self.assertIsNone(direct_ha_policy_for_tool("upsert_automation"))
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

    async def test_upsert_automation_is_compatibility_visible_but_fails_closed(self):
        with patch.object(compatibility, "rest", new=AsyncMock()) as direct:
            payload = json.loads(
                await compatibility.upsert_automation(
                    "safe_fixture",
                    json.dumps({"trigger": [], "action": []}),
                )
            )
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "provider_prohibited")
        self.assertFalse(payload["retryable"])
        self.assertEqual(payload["details"]["reason"], "governance_required")
        self.assertEqual(
            payload["details"]["required_workflow"],
            ["create_change_plan", "approve_change_plan", "apply_change_plan"],
        )
        self.assertFalse(payload["metadata"]["source_coverage"][0]["upstream_attempted"])
        direct.assert_not_awaited()
        metrics = METRICS.snapshot()["provider_routing"]
        self.assertEqual(metrics["requests_by_provider"], {})
        self.assertEqual(metrics["failures_by_provider"], {})

    async def test_malformed_upsert_payload_still_refuses_before_dispatch(self):
        with patch.object(compatibility, "rest", new=AsyncMock()) as direct:
            payload = json.loads(
                await compatibility.upsert_automation("safe_fixture", "{not-json")
            )
        self.assertEqual(payload["error_code"], "provider_prohibited")
        self.assertEqual(payload["details"]["reason"], "governance_required")
        direct.assert_not_awaited()
        metrics = METRICS.snapshot()["provider_routing"]
        self.assertEqual(metrics["requests_by_provider"], {})
        self.assertEqual(metrics["failures_by_provider"], {})

    async def test_direct_required_exception_is_attributed_to_direct_provider(self):
        with patch.object(compatibility, "rest", new=AsyncMock(return_value={"result": "valid", "errors": None})):
            payload = json.loads(await compatibility.check_config())
        self.assertTrue(payload["success"])
        self.assertEqual(payload["metadata"]["routing"]["classification"], "direct_ha_required")
        self.assertEqual(payload["metadata"]["routing"]["provider"], "direct_ha_api")

    async def test_bounded_routed_response_records_truncation(self):
        rendered = await CANONICAL_DISPATCHER.execute(
            "search_entities",
            AsyncMock(
                return_value={
                    "count": 1,
                    "results": [
                        {
                            "entity_id": "sensor.example",
                            "state": "on",
                            "friendly_name": "x" * 2000,
                        }
                    ],
                    "truncated": False,
                }
            ),
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
    def test_all_40_registered_tools_have_serializable_json_schemas(self):
        tools = get_registered_server()._tool_manager.list_tools()
        names = [tool.name for tool in tools]
        self.assertEqual(len(names), 40)
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("entity_dependency_analysis", names)
        self.assertIn("automation_reliability_analysis", names)
        self.assertIn("list_dashboards", names)
        self.assertIn("get_dashboard_config", names)
        for tool in tools:
            json.dumps(tool.parameters)


if __name__ == "__main__":
    unittest.main()
