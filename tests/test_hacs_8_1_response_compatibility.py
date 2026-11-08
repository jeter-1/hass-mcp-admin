from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.capabilities import (  # noqa: E402
    replace_dynamic_upstream_capabilities,
)
from ha_mcp_engineering.providers.operational_backup import (  # noqa: E402
    REQUIRED_TOOL as BACKUP_REQUIRED_TOOL,
)
from ha_mcp_engineering.providers.operational_lifecycle import (  # noqa: E402
    ADDON_ACTION_TOOL,
    ADDON_READ_TOOL,
    HA_RESTART_TOOL,
    RELOAD_TOOL,
)
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    ALLOWED_UPSTREAM_TOOLS as DASHBOARD_ALLOWED_UPSTREAM_TOOLS,
)
from ha_mcp_engineering.tools import registered_tools  # noqa: E402

from tests.test_readonly_upstream_gateway import (  # noqa: E402
    FakeTransport,
    catalog_tool,
    initialize,
    policy_entry,
    schema,
)


HACS_READ_TOOL = "ha_get_hacs_info"
HACS_WRITE_TOOL = "ha_manage_hacs"
HACS_DATA = {
    "query": "mushroom",
    "installed_only": True,
    "count": 1,
    "results": [{"name": "Mushroom", "installed": True}],
}
HACS_METADATA = {
    "home_assistant_timezone": "UTC",
    "timestamp_format": "ISO 8601 (UTC)",
}


def _hacs_descriptor(name=HACS_READ_TOOL, *, write=False):
    descriptor = catalog_tool(name)
    descriptor["annotations"]["openWorldHint"] = True
    if write:
        descriptor["annotations"].update(
            {
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            }
        )
    return descriptor


def _call_result(payload):
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        ],
        "structuredContent": payload,
        "isError": False,
    }


class HacsReadRetirementTests(unittest.IsolatedAsyncioTestCase):
    """HAMCP-290: the delegated ha_get_hacs_info read is retired.

    Josh retired the delegated read on 2026-11-08 in favour of the
    Engineering-owned ``get_marketplace_inventory``.  The former 8.0/8.1
    response-envelope compatibility cases (and the reviewed HACS envelope
    model they exercised) were removed with it; every reviewed release now
    withholds the read before registration and never dispatches it.
    """

    def tearDown(self):
        replace_dynamic_upstream_capabilities((), {})

    async def test_retired_hacs_read_is_never_exposed_or_dispatched(self):
        payload = {
            "success": True,
            "data": HACS_DATA,
            "metadata": HACS_METADATA,
        }
        for version in ("8.0.0", "8.1.0"):
            with self.subTest(version=version):
                entry = policy_entry(HACS_READ_TOOL, open_world=True)
                descriptor = _hacs_descriptor()
                transport = FakeTransport(
                    [descriptor],
                    version=version,
                    result=_call_result(payload),
                )
                gateway, server, _ = await initialize(
                    [entry],
                    [descriptor],
                    transport=transport,
                    version=version,
                    reviewed_version=version,
                )
                self.assertIsNone(registered_tools(server).get(HACS_READ_TOOL))
                self.assertEqual(transport.calls, [])
                health = gateway.health_snapshot()
                self.assertEqual(health["dynamically_exposed_count"], 0)
                self.assertEqual(health["core_withheld_read_count"], 1)
                self.assertEqual(
                    health["core_withheld_tools"],
                    [
                        {
                            "tool": HACS_READ_TOOL,
                            "disposition": "retired_delegated_read",
                            "reason_codes": ["delegated_read_retired"],
                        }
                    ],
                )
                self.assertEqual(health["fallback_count"], 0)

    async def test_unknown_8_1_patch_version_is_rejected_before_dispatch(self):
        entry = policy_entry(HACS_READ_TOOL, open_world=True)
        descriptor = _hacs_descriptor()
        transport = FakeTransport(
            [descriptor],
            version="8.1.1",
            result=_call_result(
                {
                    "success": True,
                    "data": HACS_DATA,
                    "metadata": HACS_METADATA,
                }
            ),
        )

        gateway, server, _ = await initialize(
            [entry],
            [descriptor],
            transport=transport,
            reviewed_version="8.1.0",
        )

        self.assertIsNone(registered_tools(server).get(HACS_READ_TOOL))
        self.assertEqual(transport.calls, [])
        health = gateway.health_snapshot()
        self.assertEqual(
            health["last_discovery_failure_category"],
            "upstream_version_mismatch",
        )
        self.assertEqual(health["fallback_count"], 0)

    async def test_8_1_hacs_shaped_payload_on_another_tool_is_unchanged(self):
        # HAMCP-290: with the HACS envelope model removed, no tool rewrites
        # a top-level-success payload; it passes through unchanged.
        other_tool = "ha_get_state"
        payload = {
            "success": True,
            "data": HACS_DATA,
            "metadata": HACS_METADATA,
        }
        entry = policy_entry(other_tool)
        descriptor = catalog_tool(other_tool)
        transport = FakeTransport(
            [descriptor],
            version="8.1.0",
            result=_call_result(payload),
        )
        gateway, server, _ = await initialize(
            [entry],
            [descriptor],
            transport=transport,
            reviewed_version="8.1.0",
        )

        value = json.loads(
            await registered_tools(server).get(other_tool).run(
                {"entity_id": "sun.sun"}
            )
        )

        self.assertTrue(value["success"])
        self.assertEqual(value["data"], payload)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(gateway.health_snapshot()["fallback_count"], 0)

    async def test_8_1_hacs_wrong_protocol_is_rejected_before_dispatch(self):
        entry = policy_entry(HACS_READ_TOOL, open_world=True)
        descriptor = _hacs_descriptor()
        transport = FakeTransport(
            [descriptor],
            version="8.1.0",
            result=_call_result(
                {
                    "success": True,
                    "data": HACS_DATA,
                    "metadata": HACS_METADATA,
                }
            ),
        )
        transport.catalog = replace(
            transport.catalog,
            protocol_version="2025-06-18",
        )

        gateway, server, _ = await initialize(
            [entry],
            [descriptor],
            transport=transport,
            reviewed_version="8.1.0",
        )

        self.assertIsNone(registered_tools(server).get(HACS_READ_TOOL))
        self.assertEqual(transport.calls, [])
        health = gateway.health_snapshot()
        self.assertEqual(
            health["last_discovery_failure_category"],
            "unsupported_protocol_version",
        )
        self.assertEqual(health["fallback_count"], 0)


class HacsRemoveNegativeReachabilityTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        replace_dynamic_upstream_capabilities((), {})

    async def test_persistent_write_hacs_remove_has_no_gateway_route(self):
        read_entry = policy_entry(HACS_READ_TOOL, open_world=True)
        remove_schema = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["download", "add_repository", "remove"],
                },
                "repository_id": {"type": ["string", "null"]},
            },
            "required": ["action"],
            "additionalProperties": False,
        }
        write_entry = policy_entry(
            HACS_WRITE_TOOL,
            "persistent_write",
            reviewed_schema=remove_schema,
            open_world=True,
        )
        read_descriptor = _hacs_descriptor()
        write_descriptor = _hacs_descriptor(HACS_WRITE_TOOL, write=True)
        write_descriptor["inputSchema"] = remove_schema
        transport = FakeTransport([read_descriptor, write_descriptor])
        gateway, server, _ = await initialize(
            [read_entry, write_entry],
            [read_descriptor, write_descriptor],
            transport=transport,
        )

        # HAMCP-290: the HACS read is a retired delegated read, so neither
        # half of the HACS surface is reachable through the gateway.
        self.assertIsNone(registered_tools(server).get(HACS_READ_TOOL))
        self.assertIsNone(registered_tools(server).get(HACS_WRITE_TOOL))
        value = json.loads(
            await gateway.execute(
                exposed_name=HACS_WRITE_TOOL,
                arguments={
                    "action": "remove",
                    "repository_id": "synthetic-owner/synthetic-repo",
                },
                reviewed_schema=remove_schema,
                policy_entry=write_entry,
                admission_generation=-1,
                contract_fingerprint="not-admitted",
            )
        )

        self.assertEqual(value["error_code"], "provider_prohibited")
        self.assertEqual(value["details"]["failure_category"], "prohibited_delegation")
        self.assertEqual(transport.attempts, [])
        self.assertEqual(transport.calls, [])
        health = gateway.health_snapshot()
        self.assertEqual(
            health["blocked_classification_counts"]["persistent_write"],
            1,
        )
        self.assertEqual(health["fallback_count"], 0)

    def test_exact_8_1_policy_classifies_the_entire_hacs_tool_as_write(self):
        policy_path = (
            BETA
            / "ha_mcp_engineering"
            / "upstream_tool_policy_8_1_0.json"
        )
        reviewed_tools = {
            item["upstream_name"]: item
            for item in json.loads(policy_path.read_text(encoding="utf-8"))[
                "tools"
            ]
        }

        self.assertEqual(
            reviewed_tools[HACS_WRITE_TOOL]["classification"],
            "persistent_write",
        )
        self.assertEqual(
            reviewed_tools[HACS_READ_TOOL]["classification"],
            "automatic_read",
        )

    def test_exact_hacs_write_descriptor_delta_is_fully_accounted(self):
        evidence_root = (
            ROOT / "docs" / "evidence" / "upstream-read-compatibility"
        )

        def reviewed_tool(version):
            capture = json.loads(
                (evidence_root / f"ha-mcp-{version}.json").read_text(
                    encoding="utf-8"
                )
            )
            return next(
                item
                for item in capture["tools"]
                if item["name"] == HACS_WRITE_TOOL
            )

        old = reviewed_tool("8.0.0")
        new = reviewed_tool("8.1.0")

        self.assertNotEqual(old["description"], new["description"])
        self.assertIn('action="remove"', new["description"])
        self.assertEqual(old["annotations"], new["annotations"])
        self.assertEqual(old["outputSchema"], new["outputSchema"])
        self.assertEqual(
            old["inputSchema"]["properties"]["action"]["enum"],
            ["download", "add_repository"],
        )
        self.assertEqual(
            new["inputSchema"]["properties"]["action"]["enum"],
            ["download", "add_repository", "remove"],
        )
        self.assertFalse(new["inputSchema"]["additionalProperties"])

        old_schema = deepcopy(old["inputSchema"])
        new_schema = deepcopy(new["inputSchema"])
        for schema_value in (old_schema, new_schema):
            schema_value["properties"]["action"].pop("description")
            schema_value["properties"]["repository_id"].pop(
                "description"
            )
        old_schema["properties"]["action"]["enum"] = new_schema[
            "properties"
        ]["action"]["enum"]
        self.assertEqual(old_schema, new_schema)

    def test_hacs_write_is_absent_from_every_special_provider_route(self):
        special_provider_tools = {
            BACKUP_REQUIRED_TOOL,
            RELOAD_TOOL,
            ADDON_ACTION_TOOL,
            ADDON_READ_TOOL,
            HA_RESTART_TOOL,
            *DASHBOARD_ALLOWED_UPSTREAM_TOOLS,
        }
        self.assertNotIn(HACS_WRITE_TOOL, special_provider_tools)

        runtime_root = BETA / "ha_mcp_engineering"
        runtime_references = sorted(
            path.relative_to(runtime_root).as_posix()
            for path in runtime_root.rglob("*.py")
            if HACS_WRITE_TOOL in path.read_text(encoding="utf-8")
        )
        self.assertEqual(runtime_references, [])


if __name__ == "__main__":
    unittest.main()
