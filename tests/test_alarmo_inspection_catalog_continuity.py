"""Baseline-derived old descriptors and one additive native read descriptor."""

import hashlib
import json
import unittest

from test_integration_inspection_contract import FIXTURES, c
from ha_mcp_engineering.tools.registry import ENGINEERING_STATIC_TOOL_COUNT, get_registered_server
from ha_mcp_engineering.ha_core_readmission.routes import static_tool_requirements
from ha_mcp_engineering.providers.routing import routing_for_tool, CapabilityRoute


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_55_existing_descriptors_identical_exactly_one_added(self):
        expected = json.loads((FIXTURES / "beta10_catalog_baseline.json").read_text())["baseline_descriptors_sha256"]
        actual = {tool.name: tool.model_dump(mode="json", exclude_none=True) for tool in await get_registered_server().list_tools()}
        self.assertEqual(set(actual) - set(expected), {"get_integration_inspection"})
        self.assertEqual(set(expected) - set(actual), set())
        for name, digest in expected.items():
            self.assertEqual(hashlib.sha256(c.canonical(actual[name])).hexdigest(), digest, name)
        self.assertEqual(ENGINEERING_STATIC_TOOL_COUNT, 56)
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
