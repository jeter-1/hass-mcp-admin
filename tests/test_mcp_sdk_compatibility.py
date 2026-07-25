import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import anyio
from mcp import ClientSession
from mcp import types
from mcp.server.fastmcp import FastMCP


ROOT = Path(__file__).resolve().parents[1]
ENGINEERING_PACKAGE = ROOT / "hass_mcp_engineering_beta"

import sys

sys.path.insert(0, str(ENGINEERING_PACKAGE))

from ha_mcp_engineering.mcp_sdk_compatibility import (  # noqa: E402
    McpSdkCompatibilityError,
    McpSdkToolRegistry,
    PINNED_MCP_SDK_VERSION,
    REVIEWED_UPSTREAM_PROTOCOL_VERSION,
    ReviewedProtocolClientSession,
    _require_pinned_sdk_version,
    initialize_reviewed_upstream_session,
    registered_tools,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.mcp_server import create_mcp_server  # noqa: E402


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
    def setUp(self):
        _require_pinned_sdk_version.cache_clear()

    def tearDown(self):
        _require_pinned_sdk_version.cache_clear()

    def test_pinned_real_sdk_shape_is_admitted(self):
        server = server_with_tools()
        registry = McpSdkToolRegistry(server)

        self.assertEqual(registry.sdk_version, PINNED_MCP_SDK_VERSION)
        self.assertEqual(set(registry.snapshot()), {"alpha", "alpha_extended"})
        self.assertEqual(set(registered_tools(server)), {"alpha", "alpha_extended"})

    def test_snapshot_mapping_is_read_only_and_tool_objects_are_shared(self):
        registry = McpSdkToolRegistry(server_with_tools())
        snapshot = registry.snapshot()

        with self.assertRaises(TypeError):
            snapshot["extra"] = snapshot["alpha"]  # type: ignore[index]
        with self.assertRaises(TypeError):
            del snapshot["alpha"]  # type: ignore[misc]
        self.assertIs(snapshot["alpha"], registry.get("alpha"))

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

    def test_post_assignment_verification_failure_restores_original_mapping(self):
        registry = McpSdkToolRegistry(server_with_tools())
        before = registry.snapshot()
        original = registry._manager._tools
        replacement = {"alpha": before["alpha"]}

        with (
            patch.object(
                registry,
                "_validated_tools",
                side_effect=[original, dict(replacement), original],
            ),
            self.assertRaises(McpSdkCompatibilityError),
        ):
            registry.replace(replacement)

        after = registry.snapshot()
        self.assertIs(registry._manager._tools, original)
        self.assertEqual(set(after), {"alpha", "alpha_extended"})
        self.assertIs(after["alpha"], before["alpha"])
        self.assertIs(after["alpha_extended"], before["alpha_extended"])

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
                    "^The pinned MCP SDK compatibility contract is unavailable; "
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

    def test_sdk_version_check_is_cached_after_success(self):
        with patch(
            "ha_mcp_engineering.mcp_sdk_compatibility.version",
            return_value=PINNED_MCP_SDK_VERSION,
        ) as distribution_version:
            McpSdkToolRegistry(server_with_tools())
            McpSdkToolRegistry(server_with_tools())

        distribution_version.assert_called_once_with("mcp")

    def test_outbound_initialization_preserves_reviewed_protocol_and_state(self):
        events = []
        client_info = types.Implementation(
            name="engineering-compatibility-test",
            version="2.0.1-rc1-dev2",
        )
        expected = types.InitializeResult(
            protocolVersion=REVIEWED_UPSTREAM_PROTOCOL_VERSION,
            capabilities=types.ServerCapabilities(),
            serverInfo=types.Implementation(name="ha-mcp", version="7.14.1"),
        )

        class Session:
            async def send_request(self, request, result_type):
                self.assert_request = request
                self.assert_result_type = result_type
                events.append("initialize")
                return expected

            async def send_notification(self, notification):
                self.assert_notification = notification
                events.append("initialized")

        session = Session()
        session._server_capabilities = None
        result = asyncio.run(
            initialize_reviewed_upstream_session(session, client_info)
        )

        params = session.assert_request.root.params
        self.assertEqual(
            params.protocolVersion,
            REVIEWED_UPSTREAM_PROTOCOL_VERSION,
        )
        self.assertEqual(params.clientInfo, client_info)
        self.assertEqual(params.capabilities, types.ClientCapabilities())
        self.assertIs(
            session.assert_result_type,
            types.InitializeResult,
        )
        self.assertIsInstance(
            session.assert_notification.root,
            types.InitializedNotification,
        )
        self.assertIs(result, expected)
        self.assertIs(session._server_capabilities, expected.capabilities)
        self.assertEqual(events, ["initialize", "initialized"])

    def test_unsupported_protocol_does_not_complete_initialization(self):
        events = []
        client_info = types.Implementation(
            name="engineering-compatibility-test",
            version="2.0.1-rc1-dev2",
        )

        class Session:
            _server_capabilities = None

            async def send_request(self, _request, _result_type):
                return types.InitializeResult(
                    protocolVersion="1900-01-01",
                    capabilities=types.ServerCapabilities(),
                    serverInfo=types.Implementation(
                        name="ha-mcp",
                        version="7.14.1",
                    ),
                )

            async def send_notification(self, _notification):
                events.append("initialized")

        session = Session()
        with self.assertRaisesRegex(
            RuntimeError,
            "^The upstream MCP server returned an unsupported protocol version\\.$",
        ):
            asyncio.run(
                initialize_reviewed_upstream_session(session, client_info)
            )
        self.assertIsNone(session._server_capabilities)
        self.assertEqual(events, [])

    def test_reviewed_session_preserves_normal_public_initialization_state(self):
        async def exercise(session_type):
            read_send, read_receive = anyio.create_memory_object_stream(1)
            write_send, write_receive = anyio.create_memory_object_stream(1)
            client_info = types.Implementation(
                name="engineering-compatibility-test",
                version="2.0.1-rc1-dev2",
            )
            expected = types.InitializeResult(
                protocolVersion=REVIEWED_UPSTREAM_PROTOCOL_VERSION,
                capabilities=types.ServerCapabilities(
                    tools=types.ToolsCapability(listChanged=True)
                ),
                serverInfo=types.Implementation(
                    name="ha-mcp",
                    version="7.14.1",
                ),
            )
            session = session_type(
                read_receive,
                write_send,
                client_info=client_info,
            )
            session.send_request = AsyncMock(return_value=expected)
            session.send_notification = AsyncMock()
            try:
                result = await session.initialize()
                return (
                    result,
                    session.get_server_capabilities(),
                    session.send_request.call_args,
                    session.send_notification.call_args,
                )
            finally:
                await read_send.aclose()
                await read_receive.aclose()
                await write_send.aclose()
                await write_receive.aclose()

        async def compare():
            return await asyncio.gather(
                exercise(ClientSession),
                exercise(ReviewedProtocolClientSession),
            )

        normal, reviewed = asyncio.run(compare())
        normal_result, normal_capabilities, _, normal_notification = normal
        reviewed_result, reviewed_capabilities, reviewed_request, reviewed_notification = (
            reviewed
        )

        self.assertEqual(
            reviewed_result.protocolVersion,
            normal_result.protocolVersion,
        )
        self.assertEqual(reviewed_result.serverInfo, normal_result.serverInfo)
        self.assertEqual(reviewed_capabilities, normal_capabilities)
        self.assertEqual(
            reviewed_capabilities,
            reviewed_result.capabilities,
        )
        self.assertEqual(
            reviewed_request.args[0].root.params.protocolVersion,
            REVIEWED_UPSTREAM_PROTOCOL_VERSION,
        )
        self.assertIsInstance(
            normal_notification.args[0].root,
            types.InitializedNotification,
        )
        self.assertIsInstance(
            reviewed_notification.args[0].root,
            types.InitializedNotification,
        )

    def test_production_fastmcp_settings_do_not_assume_loopback_protection(self):
        server = create_mcp_server(
            Settings(
                ha_url="http://synthetic-ha.invalid",
                ha_token="synthetic-token",
                access_secret="synthetic-access-secret-1234",
                port=8100,
                audit_path="/tmp/synthetic-audit.jsonl",
                rate_limit_per_minute=120,
                rate_limit_burst=25,
                destructive_services=frozenset(),
            )
        )

        self.assertEqual(server.settings.host, "0.0.0.0")
        self.assertIsNone(server.settings.transport_security)

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
