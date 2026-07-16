"""Bounded streamable-HTTP MCP client for the dashboard read provider."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import json
import logging
import socket
import time
from typing import Any, Callable

from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client


REQUIRED_DASHBOARD_TOOL = "ha_config_get_dashboard"
ALLOWED_DASHBOARD_ARGUMENTS = frozenset(
    {"url_path", "list_only", "force_reload"}
)
MAX_TOOL_CATALOG_SIZE = 500
MAX_TOOL_CATALOG_PAGES = 20
MAX_UPSTREAM_CONTENT_CHARS = 2_000_000


class DashboardTransportError(RuntimeError):
    """Secret-free transport failure classified at the MCP boundary."""

    def __init__(self, category: str):
        super().__init__("The upstream dashboard MCP transport failed.")
        self.category = category


@dataclass(frozen=True)
class McpDashboardHandshake:
    protocol_version: str
    server_name: str
    server_version: str
    tools: tuple[dict[str, Any], ...]
    connection_latency_ms: float


@dataclass(frozen=True)
class McpDashboardRead:
    handshake: McpDashboardHandshake
    call_result: dict[str, Any]
    tool_call_latency_ms: float


CapabilityValidator = Callable[[McpDashboardHandshake], None]


class McpDashboardTransport:
    """Open one bounded MCP session and call only the dashboard read tool.

    The endpoint is never included in representations, logs, or exceptions.
    Each operation creates and closes a session, so a subsequent call naturally
    reconnects after an upstream restart.
    """

    def __init__(self, url: str, *, timeout_seconds: float, client_version: str):
        self._url = url
        # mcp 1.9.0 reads timedelta.seconds, which truncates fractional values.
        # Keep the lower bound at one complete second to avoid an unintended
        # zero-second connect deadline.
        self._timeout = timedelta(seconds=max(1.0, float(timeout_seconds)))
        self._client_info = types.Implementation(
            name="hass-mcp-engineering-dashboard",
            version=client_version,
        )
        self._silence_url_bearing_library_logs()

    def __repr__(self) -> str:
        return (
            "McpDashboardTransport("
            f"configured={bool(self._url)}, timeout_seconds={self._timeout.total_seconds()}"
            ")"
        )

    @staticmethod
    def _silence_url_bearing_library_logs() -> None:
        # mcp 1.9.0 logs the complete streamable-HTTP endpoint at INFO and can
        # include it in lower-level exception messages. The Engineering server
        # exposes its own bounded category metrics instead.
        for name in ("mcp.client.streamable_http", "httpx", "httpcore"):
            logger = logging.getLogger(name)
            logger.disabled = True
            logger.propagate = False

    async def discover(self) -> McpDashboardHandshake:
        result = await self._run(arguments=None, capability_validator=None)
        if not isinstance(result, McpDashboardHandshake):
            raise DashboardTransportError("internal_error")
        return result

    async def execute_dashboard_read(
        self,
        arguments: dict[str, Any],
        capability_validator: CapabilityValidator,
    ) -> McpDashboardRead:
        unknown = set(arguments) - ALLOWED_DASHBOARD_ARGUMENTS
        if unknown:
            raise DashboardTransportError("protocol_error")
        result = await self._run(
            arguments=dict(arguments),
            capability_validator=capability_validator,
        )
        if not isinstance(result, McpDashboardRead):
            raise DashboardTransportError("internal_error")
        return result

    async def _run(
        self,
        *,
        arguments: dict[str, Any] | None,
        capability_validator: CapabilityValidator | None,
    ) -> McpDashboardHandshake | McpDashboardRead:
        started = time.perf_counter()
        try:
            async with streamablehttp_client(
                self._url,
                timeout=self._timeout,
                sse_read_timeout=self._timeout,
                terminate_on_close=True,
            ) as (read_stream, write_stream, _get_session_id):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=self._timeout,
                    client_info=self._client_info,
                ) as session:
                    initialize = await session.initialize()
                    tools = await self._list_all_tools(session)
                    handshake = McpDashboardHandshake(
                        protocol_version=str(initialize.protocolVersion),
                        server_name=str(initialize.serverInfo.name),
                        server_version=str(initialize.serverInfo.version),
                        tools=tuple(tools),
                        connection_latency_ms=round(
                            (time.perf_counter() - started) * 1000, 3
                        ),
                    )
                    if arguments is None:
                        return handshake
                    capability_validator(handshake)
                    call_started = time.perf_counter()
                    call_result = await session.call_tool(
                        REQUIRED_DASHBOARD_TOOL,
                        arguments,
                        read_timeout_seconds=self._timeout,
                    )
                    encoded = call_result.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    )
                    if len(json.dumps(encoded, default=str)) > MAX_UPSTREAM_CONTENT_CHARS:
                        raise DashboardTransportError("response_too_large")
                    return McpDashboardRead(
                        handshake=handshake,
                        call_result=encoded,
                        tool_call_latency_ms=round(
                            (time.perf_counter() - call_started) * 1000, 3
                        ),
                    )
        except DashboardTransportError:
            raise
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            raise DashboardTransportError(
                _classify_transport_exception(exc)
            ) from None

    async def _list_all_tools(self, session: ClientSession) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _page in range(MAX_TOOL_CATALOG_PAGES):
            result = await session.list_tools(cursor)
            tools.extend(
                tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                for tool in result.tools
            )
            if len(tools) > MAX_TOOL_CATALOG_SIZE:
                raise DashboardTransportError("invalid_response")
            cursor = result.nextCursor
            if not cursor:
                return tools
            if cursor in seen_cursors:
                raise DashboardTransportError("protocol_error")
            seen_cursors.add(cursor)
        raise DashboardTransportError("invalid_response")


def _iter_exceptions(exc: BaseException):
    if isinstance(exc, BaseExceptionGroup):
        for child in exc.exceptions:
            yield from _iter_exceptions(child)
    else:
        yield exc


def _classify_transport_exception(exc: BaseException) -> str:
    leaves = tuple(_iter_exceptions(exc))
    for leaf in leaves:
        response = getattr(leaf, "response", None)
        status = getattr(response, "status_code", None)
        if status in {401, 403}:
            return "authentication_failed"
        if status == 404:
            return "endpoint_rejected"
    for leaf in leaves:
        error = getattr(leaf, "error", None)
        if (
            getattr(error, "code", None) == 32600
            and getattr(error, "message", None) == "Session terminated"
        ):
            # mcp 1.9.0 converts an HTTP 404 during a fresh streamable-HTTP
            # request into this synthetic MCP error and discards the status.
            return "endpoint_rejected"
    for leaf in leaves:
        name = type(leaf).__name__.lower()
        if isinstance(leaf, (asyncio.TimeoutError, TimeoutError)):
            continue
        if isinstance(leaf, (ConnectionError, socket.gaierror, OSError)) or any(
            term in name
            for term in (
                "connecterror",
                "connectionrefused",
                "networkerror",
                "gaierror",
                "nameorservice",
                "noroutetohost",
            )
        ):
            return "connection_failed"
    for leaf in leaves:
        if isinstance(leaf, (asyncio.TimeoutError, TimeoutError)):
            return "timeout"
        if "timeout" in type(leaf).__name__.lower():
            return "timeout"
        error = getattr(leaf, "error", None)
        if getattr(error, "code", None) == 408:
            return "timeout"
    for leaf in leaves:
        name = type(leaf).__name__.lower()
        if isinstance(leaf, (json.JSONDecodeError, UnicodeDecodeError)):
            return "invalid_response"
        if any(term in name for term in ("validationerror", "decodeerror")):
            return "invalid_response"
        if any(
            term in name
            for term in ("mcperror", "protocolerror", "remoteprotocolerror")
        ):
            return "protocol_error"
    for leaf in leaves:
        response = getattr(leaf, "response", None)
        if isinstance(getattr(response, "status_code", None), int):
            return "upstream_error"
        if "httperror" in type(leaf).__name__.lower():
            return "upstream_error"
    return "internal_error"
