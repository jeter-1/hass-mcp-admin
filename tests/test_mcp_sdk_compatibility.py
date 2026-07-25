from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP


ROOT = Path(__file__).resolve().parents[1]
ENGINEERING_PACKAGE = ROOT / "hass_mcp_engineering_beta"

import sys

sys.path.insert(0, str(ENGINEERING_PACKAGE))

from ha_mcp_engineering.mcp_sdk_compatibility import (  # noqa: E402
    McpSdkCompatibilityError,
    McpSdkToolRegistry,
    PINNED_MCP_SDK_VERSION,
    registered_tools,
)


def server_with_tools() -> FastMCP:
    server = FastMCP("sdk-compatibility-test")

    @server.tool()
    async def alpha() -> str:
        return "alpha"

    @server.tool()
    async def alpha_extended() -> str:
        return "alpha-extended"

    return server


class McpSdkCompatibilityTests(unittest.TestCase):
    def test_pinned_real_sdk_shape_is_admitted(self):
        server = server_with_tools()
        registry = McpSdkToolRegistry(server)

        self.assertEqual(registry.sdk_version, PINNED_MCP_SDK_VERSION)
        self.assertEqual(set(registry.snapshot()), {"alpha", "alpha_extended"})
        self.assertEqual(set(registered_tools(server)), {"alpha", "alpha_extended"})

    def test_snapshot_is_immutable(self):
        snapshot = McpSdkToolRegistry(server_with_tools()).snapshot()

        with self.assertRaises(TypeError):
            snapshot["extra"] = snapshot["alpha"]  # type: ignore[index]

    def test_invalid_replacement_fails_before_mutation(self):
        registry = McpSdkToolRegistry(server_with_tools())
        before = registry.snapshot()

        with self.assertRaises(McpSdkCompatibilityError):
            registry.replace({"wrong-name": before["alpha"]})

        after = registry.snapshot()
        self.assertEqual(set(after), set(before))
        self.assertIs(after["alpha"], before["alpha"])

    def test_valid_replacement_is_published_as_one_complete_mapping(self):
        registry = McpSdkToolRegistry(server_with_tools())
        before = registry.snapshot()

        registry.replace({"alpha": before["alpha"]})

        after = registry.snapshot()
        self.assertEqual(set(after), {"alpha"})
        self.assertIs(after["alpha"], before["alpha"])

    def test_exact_removal_does_not_remove_prefixed_name(self):
        registry = McpSdkToolRegistry(server_with_tools())

        self.assertTrue(registry.remove_exact("alpha"))
        self.assertEqual(set(registry.snapshot()), {"alpha_extended"})
        self.assertFalse(registry.remove_exact("alpha"))
        self.assertEqual(set(registry.snapshot()), {"alpha_extended"})

    def test_missing_or_changed_private_shape_fails_closed(self):
        cases = (
            SimpleNamespace(),
            SimpleNamespace(_tool_manager=SimpleNamespace()),
            SimpleNamespace(_tool_manager=SimpleNamespace(_tools=[])),
            SimpleNamespace(
                _tool_manager=SimpleNamespace(_tools={"alpha": object()})
            ),
        )
        for server in cases:
            with self.subTest(server=server):
                with self.assertRaisesRegex(
                    McpSdkCompatibilityError,
                    "^The pinned MCP SDK registry contract is unavailable; "
                    "startup is blocked\\.$",
                ):
                    McpSdkToolRegistry(server)

    def test_unreviewed_sdk_version_fails_closed(self):
        with patch(
            "ha_mcp_engineering.mcp_sdk_compatibility.version",
            return_value="1.28.0",
        ):
            with self.assertRaises(McpSdkCompatibilityError):
                McpSdkToolRegistry(server_with_tools())

    def test_private_registry_access_is_isolated_to_adapter(self):
        package = ENGINEERING_PACKAGE / "ha_mcp_engineering"
        offenders = []
        for path in package.rglob("*.py"):
            if path.name == "mcp_sdk_compatibility.py":
                continue
            text = path.read_text(encoding="utf-8")
            if "_tool_manager" in text or "._tools" in text:
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
