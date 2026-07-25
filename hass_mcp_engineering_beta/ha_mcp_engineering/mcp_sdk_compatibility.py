"""Fail-closed compatibility boundary for the pinned MCP SDK.

The SDK does not yet expose every registry operation required for transactional
dynamic read admission. Keep the reviewed private integration in this module so
an SDK shape or protocol-default change cannot silently change the served tool
catalog or broaden the reviewed upstream contract.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from types import MappingProxyType
from typing import Any, Mapping

from mcp import ClientSession, types
from mcp.server.fastmcp.tools.base import Tool


PINNED_MCP_SDK_VERSION = "1.28.1"
REVIEWED_UPSTREAM_PROTOCOL_VERSION = "2025-03-26"
_COMPATIBILITY_ERROR_MESSAGE = (
    "The pinned MCP SDK compatibility contract is unavailable; startup is blocked."
)


class McpSdkCompatibilityError(RuntimeError):
    """Bounded fail-closed error for an unsupported MCP SDK contract."""

    def __init__(self) -> None:
        super().__init__(_COMPATIBILITY_ERROR_MESSAGE)


def _require_pinned_sdk_version() -> None:
    try:
        sdk_version = version("mcp")
    except PackageNotFoundError:
        raise McpSdkCompatibilityError() from None
    if sdk_version != PINNED_MCP_SDK_VERSION:
        raise McpSdkCompatibilityError()


async def initialize_reviewed_upstream_session(
    session: Any,
    client_info: types.Implementation,
) -> types.InitializeResult:
    """Initialize one outbound session with the reviewed upstream protocol.

    MCP 1.28.1 defaults to a newer protocol than the exact ha-mcp 7.14.1
    contract reviewed by Engineering. Use the SDK's public request surface to
    retain that reviewed protocol instead of broadening admission.
    """

    _require_pinned_sdk_version()
    result = await session.send_request(
        types.ClientRequest(
            types.InitializeRequest(
                params=types.InitializeRequestParams(
                    protocolVersion=REVIEWED_UPSTREAM_PROTOCOL_VERSION,
                    capabilities=types.ClientCapabilities(),
                    clientInfo=client_info,
                ),
            )
        ),
        types.InitializeResult,
    )
    await session.send_notification(
        types.ClientNotification(types.InitializedNotification())
    )
    return result


class ReviewedProtocolClientSession(ClientSession):
    """ClientSession that preserves the exact reviewed upstream protocol."""

    def __init__(self, *args: Any, client_info: types.Implementation, **kwargs: Any):
        _require_pinned_sdk_version()
        self.__engineering_client_info = client_info
        super().__init__(*args, client_info=client_info, **kwargs)

    async def initialize(self) -> types.InitializeResult:
        return await initialize_reviewed_upstream_session(
            self,
            self.__engineering_client_info,
        )


class McpSdkToolRegistry:
    """Reviewed adapter around the pinned FastMCP tool-registry structure."""

    def __init__(self, server: Any) -> None:
        _require_pinned_sdk_version()

        manager = getattr(server, "_tool_manager", None)
        if manager is None:
            raise McpSdkCompatibilityError()
        self._manager = manager
        self._validated_tools()

    @property
    def sdk_version(self) -> str:
        """Return the exact SDK version admitted by this boundary."""

        return PINNED_MCP_SDK_VERSION

    def snapshot(self) -> Mapping[str, Tool]:
        """Return an immutable snapshot after revalidating the SDK shape."""

        return MappingProxyType(dict(self._validated_tools()))

    def get(self, name: str) -> Tool | None:
        """Return one exactly named tool from a validated registry."""

        return self._validated_tools().get(name)

    def replace(self, tools: Mapping[str, Tool]) -> None:
        """Atomically replace the registry with a fully validated copy."""

        replacement = dict(tools)
        self._validate_mapping(replacement)
        try:
            self._manager._tools = replacement
        except Exception:
            raise McpSdkCompatibilityError() from None
        if self._validated_tools() is not replacement:
            raise McpSdkCompatibilityError()

    def remove_exact(self, name: str) -> bool:
        """Remove only an exact tool name using copy-on-write replacement."""

        if not isinstance(name, str):
            raise McpSdkCompatibilityError()
        replacement = dict(self._validated_tools())
        if name not in replacement:
            return False
        replacement.pop(name)
        self.replace(replacement)
        return True

    def _validated_tools(self) -> dict[str, Tool]:
        tools = getattr(self._manager, "_tools", None)
        if not isinstance(tools, dict):
            raise McpSdkCompatibilityError()
        self._validate_mapping(tools)
        return tools

    @staticmethod
    def _validate_mapping(tools: dict[Any, Any]) -> None:
        for name, tool in tools.items():
            if (
                not isinstance(name, str)
                or not isinstance(tool, Tool)
                or tool.name != name
            ):
                raise McpSdkCompatibilityError()


def registered_tools(server: Any) -> Mapping[str, Tool]:
    """Expose a validated immutable registry snapshot to source and tests."""

    return McpSdkToolRegistry(server).snapshot()
