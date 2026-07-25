"""Fail-closed compatibility boundary for the pinned MCP SDK tool registry.

The SDK does not yet expose every registry operation required for transactional
dynamic read admission. Keep the reviewed private integration in this module so
an SDK shape change is detected at startup instead of silently changing the
served tool catalog.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from types import MappingProxyType
from typing import Any, Mapping

from mcp.server.fastmcp.tools.base import Tool


PINNED_MCP_SDK_VERSION = "1.28.1"
_COMPATIBILITY_ERROR_MESSAGE = (
    "The pinned MCP SDK registry contract is unavailable; startup is blocked."
)


class McpSdkCompatibilityError(RuntimeError):
    """Bounded fail-closed error for an unsupported MCP SDK registry shape."""

    def __init__(self) -> None:
        super().__init__(_COMPATIBILITY_ERROR_MESSAGE)


class McpSdkToolRegistry:
    """Reviewed adapter around the pinned FastMCP tool-registry structure."""

    def __init__(self, server: Any) -> None:
        try:
            sdk_version = version("mcp")
        except PackageNotFoundError:
            raise McpSdkCompatibilityError() from None
        if sdk_version != PINNED_MCP_SDK_VERSION:
            raise McpSdkCompatibilityError()

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
