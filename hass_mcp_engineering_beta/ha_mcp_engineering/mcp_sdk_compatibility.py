"""Fail-closed compatibility boundary for the pinned MCP SDK.

The SDK does not yet expose every registry operation required for transactional
dynamic read admission. Keep the reviewed private integration in this module so
an SDK shape or protocol-default change cannot silently change the served tool
catalog or broaden the reviewed upstream contract.
"""

from __future__ import annotations

from contextvars import ContextVar
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
import logging
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping
from weakref import WeakKeyDictionary

import anyio
from mcp import ClientSession, types
from mcp.server.lowlevel.server import request_ctx
from mcp.server.fastmcp.tools.base import Tool
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from starlette.responses import JSONResponse


PINNED_MCP_SDK_VERSION = "1.28.1"
REVIEWED_UPSTREAM_PROTOCOL_VERSION = "2025-03-26"
_COMPATIBILITY_ERROR_MESSAGE = (
    "The pinned MCP SDK compatibility contract is unavailable; startup is blocked."
)
MAX_CATALOG_GENERATION_SESSIONS = 1_024
MAX_STATEFUL_MCP_SESSIONS = 1_024
CatalogGenerationSnapshot = Callable[[], tuple[int | None, tuple[str, ...]]]
_listed_catalog_generation: ContextVar[int | None] = ContextVar(
    "listed_catalog_generation",
    default=None,
)
_SESSION_ID = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32}(?![0-9A-Fa-f])")
_SDK_SESSION_LOGGERS = (
    "mcp.server.streamable_http_manager",
    "mcp.server.streamable_http",
)
_LOGGER = logging.getLogger(__name__)


class _SessionIdRedactionFilter(logging.Filter):
    """Remove opaque MCP session IDs from pinned-SDK log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _SESSION_ID.sub(
            "[redacted-session]",
            record.getMessage(),
        )
        record.args = ()
        return True


_SESSION_LOG_FILTER = _SessionIdRedactionFilter()


def current_listed_catalog_generation() -> int | None:
    """Return the inbound session's list-bound generation for this call."""

    return _listed_catalog_generation.get()


def install_bounded_stateful_session_manager(server: Any) -> None:
    """Bound pinned-SDK sessions before allocation and redact their logs."""

    _require_pinned_sdk_version()
    manager = getattr(server, "session_manager", None)
    instances = getattr(manager, "_server_instances", None)
    original = getattr(manager, "handle_request", None)
    if not isinstance(instances, dict) or not callable(original):
        raise McpSdkCompatibilityError()
    if getattr(manager, "_engineering_session_bound_installed", False):
        return

    for name in _SDK_SESSION_LOGGERS:
        logger = logging.getLogger(name)
        if _SESSION_LOG_FILTER not in logger.filters:
            logger.addFilter(_SESSION_LOG_FILTER)

    admission_lock = anyio.Lock()

    async def bounded_handle_request(
        scope: Any,
        receive: Any,
        send: Any,
    ) -> None:
        headers = dict(scope.get("headers", ()))
        if b"mcp-session-id" not in headers:
            async with admission_lock:
                current = getattr(manager, "_server_instances", None)
                if not isinstance(current, dict):
                    raise McpSdkCompatibilityError()
                if len(current) >= MAX_STATEFUL_MCP_SESSIONS:
                    _LOGGER.warning(
                        "Stateful MCP session capacity exhausted."
                    )
                    response = JSONResponse(
                        {
                            "jsonrpc": "2.0",
                            "id": "server-error",
                            "error": {
                                "code": -32000,
                                "message": "Stateful session capacity exhausted",
                            },
                        },
                        status_code=503,
                        headers={"Retry-After": "30"},
                    )
                    await response(scope, receive, send)
                    return
                await original(scope, receive, send)
                return
        raw_session_id = headers.get(b"mcp-session-id")
        current = getattr(manager, "_server_instances", None)
        session_id = (
            raw_session_id.decode("ascii", errors="ignore")
            if isinstance(raw_session_id, bytes)
            else None
        )
        transport = (
            current.get(session_id)
            if isinstance(current, dict) and session_id
            else None
        )
        await original(scope, receive, send)
        if (
            scope.get("method") == "DELETE"
            and transport is not None
            and getattr(transport, "is_terminated", False)
        ):
            current = getattr(manager, "_server_instances", None)
            owners = getattr(manager, "_session_owners", None)
            if isinstance(current, dict) and current.get(session_id) is transport:
                current.pop(session_id, None)
            if isinstance(owners, dict):
                owners.pop(session_id, None)

    def capacity_snapshot() -> dict[str, int | str | None]:
        current = getattr(manager, "_server_instances", None)
        count = len(current) if isinstance(current, dict) else 0
        return {
            "active_session_count": min(count, MAX_STATEFUL_MCP_SESSIONS),
            "active_session_limit": MAX_STATEFUL_MCP_SESSIONS,
            "capacity_reason": (
                "stateful_session_capacity_exhausted"
                if count >= MAX_STATEFUL_MCP_SESSIONS
                else None
            ),
        }

    manager.handle_request = bounded_handle_request
    manager.engineering_session_capacity_snapshot = capacity_snapshot
    manager._engineering_session_bound_installed = True


class McpSdkCompatibilityError(RuntimeError):
    """Bounded fail-closed error for an unsupported MCP SDK contract."""

    def __init__(self) -> None:
        super().__init__(_COMPATIBILITY_ERROR_MESSAGE)


@lru_cache(maxsize=1)
def _require_pinned_sdk_version() -> None:
    """Resolve and admit the exact SDK version once per process."""

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
    if result.protocolVersion not in SUPPORTED_PROTOCOL_VERSIONS:
        raise RuntimeError(
            "The upstream MCP server returned an unsupported protocol version."
        )
    # MCP 1.28.1 backs its public get_server_capabilities() accessor with this
    # state. The adapter owns this narrowly reviewed private SDK contact so the
    # exact-protocol initialization preserves the normal ClientSession contract.
    session._server_capabilities = result.capabilities
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
        """Return a read-only mapping snapshot containing shared Tool objects."""

        return MappingProxyType(dict(self._validated_tools()))

    def get(self, name: str) -> Tool | None:
        """Return one exactly named tool from a validated registry."""

        return self._validated_tools().get(name)

    def replace(self, tools: Mapping[str, Tool]) -> None:
        """Transactionally replace the registry with a fully validated copy."""

        replacement = dict(tools)
        self._validate_mapping(replacement)
        original = self._validated_tools()
        try:
            self._manager._tools = replacement
            if self._validated_tools() is not replacement:
                raise McpSdkCompatibilityError()
        except Exception:
            try:
                self._manager._tools = original
                if self._validated_tools() is not original:
                    raise McpSdkCompatibilityError()
            except Exception:
                raise McpSdkCompatibilityError() from None
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


class _CatalogGenerationGate:
    """Bind dynamic reads to the generation listed by each inbound session."""

    def __init__(self, snapshot: CatalogGenerationSnapshot) -> None:
        self._snapshot = snapshot
        self._listed: WeakKeyDictionary[Any, int] = WeakKeyDictionary()

    def update(self, snapshot: CatalogGenerationSnapshot) -> None:
        self._snapshot = snapshot

    @staticmethod
    def _session() -> Any | None:
        try:
            return request_ctx.get().session
        except (LookupError, AttributeError):
            return None

    async def list_tools(self, original: Any, request: Any) -> Any:
        before = self._snapshot()
        result = await original(request)
        after = self._snapshot()
        session = self._session()
        generation = after[0]
        if session is not None and generation is not None and before == after:
            try:
                if (
                    session not in self._listed
                    and len(self._listed) >= MAX_CATALOG_GENERATION_SESSIONS
                ):
                    return result
                self._listed[session] = generation
            except TypeError:
                pass
        return result

    async def call_tool(self, original: Any, request: Any) -> Any:
        generation, dynamic_names = self._snapshot()
        name = getattr(getattr(request, "params", None), "name", None)
        if name in dynamic_names:
            session = self._session()
            listed_generation = None
            if session is not None:
                try:
                    listed_generation = self._listed.get(session)
                except TypeError:
                    listed_generation = None
            if generation is None or listed_generation != generation:
                return types.ServerResult(
                    types.CallToolResult(
                        content=[
                            types.TextContent(
                                type="text",
                                text=(
                                    "The delegated read catalog changed; "
                                    "reconnect or list tools again."
                                ),
                            )
                        ],
                        isError=True,
                    )
                )
            token = _listed_catalog_generation.set(listed_generation)
            try:
                return await original(request)
            finally:
                _listed_catalog_generation.reset(token)
        return await original(request)


def install_catalog_generation_gate(
    server: Any,
    snapshot: CatalogGenerationSnapshot,
) -> None:
    """Install the pinned-SDK inbound re-list boundary exactly once."""

    _require_pinned_sdk_version()
    low_level = getattr(server, "_mcp_server", None)
    handlers = getattr(low_level, "request_handlers", None)
    if not isinstance(handlers, dict):
        raise McpSdkCompatibilityError()
    existing = getattr(low_level, "_engineering_catalog_generation_gate", None)
    if isinstance(existing, _CatalogGenerationGate):
        existing.update(snapshot)
        return
    list_handler = handlers.get(types.ListToolsRequest)
    call_handler = handlers.get(types.CallToolRequest)
    if not callable(list_handler) or not callable(call_handler):
        raise McpSdkCompatibilityError()
    gate = _CatalogGenerationGate(snapshot)

    async def gated_list(request: Any) -> Any:
        return await gate.list_tools(list_handler, request)

    async def gated_call(request: Any) -> Any:
        return await gate.call_tool(call_handler, request)

    handlers[types.ListToolsRequest] = gated_list
    handlers[types.CallToolRequest] = gated_call
    setattr(low_level, "_engineering_catalog_generation_gate", gate)


def registered_tools(server: Any) -> Mapping[str, Tool]:
    """Expose a validated read-only registry snapshot to source and tests."""

    return McpSdkToolRegistry(server).snapshot()
