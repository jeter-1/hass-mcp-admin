"""Baseline-derived old descriptors and one additive native read descriptor."""

import hashlib
import json
import unittest

from test_integration_inspection_contract import FIXTURES, c
from ha_mcp_engineering.tools.registry import ENGINEERING_STATIC_TOOL_COUNT, get_registered_server
from ha_mcp_engineering.ha_core_readmission.routes import static_tool_requirements
from ha_mcp_engineering.providers.routing import routing_for_tool, CapabilityRoute


# HAMCP-171 (2.4.1-beta.18) adds exactly one optional input to
# get_server_health and two description lines documenting it. Every other part
# of that descriptor must still equal the historical baseline, so strip only
# that reviewed property and text before comparing.
HAMCP171_DEEP_STORAGE_AUDIT = {"default": False, "title": "Deep Storage Audit", "type": "boolean"}
HAMCP171_DESCRIPTION_LINES = (
    "    Set deep_storage_audit=True to discard the in-memory safety verdicts and\n"
    "    re-validate every persisted plan record (slower; read-only).\n"
)


def _without_hamcp171_deep_storage_audit(case, descriptor):
    descriptor = json.loads(json.dumps(descriptor))
    properties = descriptor["inputSchema"]["properties"]
    case.assertEqual(properties.pop("deep_storage_audit"), HAMCP171_DEEP_STORAGE_AUDIT)
    case.assertEqual(descriptor["description"].count(HAMCP171_DESCRIPTION_LINES), 1)
    descriptor["description"] = descriptor["description"].replace(HAMCP171_DESCRIPTION_LINES, "")
    return descriptor


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_55_existing_descriptors_identical_exactly_one_added(self):
        expected = json.loads((FIXTURES / "beta10_catalog_baseline.json").read_text())["baseline_descriptors_sha256"]
        actual = {tool.name: tool.model_dump(mode="json", exclude_none=True) for tool in await get_registered_server().list_tools()}
        self.assertEqual(set(actual) - set(expected), {"get_integration_inspection", "capture_automation_baseline", "dashboard_integrity_analysis",
                                                          "create_entity_metadata_plan", "get_backup_inventory",
                                                          "get_recorder_settings", "get_unregistered_entities",
                                                          "get_configuration_definitions"})
        self.assertEqual(set(expected) - set(actual), set())
        actual["get_server_health"] = _without_hamcp171_deep_storage_audit(self, actual["get_server_health"])
        for name, digest in expected.items():
            self.assertEqual(hashlib.sha256(c.canonical(actual[name])).hexdigest(), digest, name)
        self.assertEqual(ENGINEERING_STATIC_TOOL_COUNT, 63)
        tool = actual["get_integration_inspection"]
        self.assertFalse(tool["inputSchema"]["additionalProperties"])
        self.assertEqual(set(tool["inputSchema"]["properties"]), {"alarm_entity_id", "integration", "limit", "cursor"})
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        self.assertFalse(tool["annotations"]["openWorldHint"])
        self.assertEqual(static_tool_requirements("get_integration_inspection"), c.CORE_REQUIREMENTS)
        route = routing_for_tool("get_integration_inspection")
        self.assertEqual(route.route, CapabilityRoute.ENGINEERING_NATIVE)
        self.assertEqual(route.preferred_provider, "engineering")
        self.assertEqual(route.fallback_providers, ())
