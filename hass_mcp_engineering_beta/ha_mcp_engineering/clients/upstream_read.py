"""Bounded MCP transport for reviewed generic upstream read delegation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import json
import logging
import time
from typing import Any, Callable

from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client

from .mcp import (
    MAX_TOOL_CATALOG_PAGES,
    MAX_TOOL_CATALOG_SIZE,
    DashboardTransportError,
    _classify_transport_exception,
)


MAX_GENERIC_UPSTREAM_RESPONSE_BYTES = 1_000_000


@dataclass(frozen=True)
class McpReadCatalog:
    protocol_version: str
    server_name: str
    server_version: str
    tools: tuple[dict[str, Any], ...]
    connection_latency_ms: float


@dataclass(frozen=True)
class McpReadResult:
    protocol_version: str
    server_name: str
    server_version: str
    call_result: dict[str, Any]
    connection_latency_ms: float
    tool_call_latency_ms: float


IdentityValidator = Callable[[str, str, str], None]


class McpReadGatewayTransport:
    """Open bounded sessions without exposing the secret-bearing endpoint."""

    def __init__(self, url: str, *, timeout_seconds: float, client_version: str):
        self._url = url
        self._timeout = timedelta(seconds=max(1.0, float(timeout_seconds)))
        self._client_info = types.Implementation(
            name="hass-mcp-engineering-read-gateway",
            version=client_version,
        )
        for name in ("mcp.client.streamable_http", "httpx", "httpcore"):
            logger = logging.getLogger(name)
            logger.disabled = True
            logger.propagate = False

    def __repr__(self) -> str:
        return (
            "McpReadGatewayTransport("
            f"configured={bool(self._url)}, timeout_seconds={self._timeout.total_seconds()}"
            ")"
        )

    async def discover(self) -> McpReadCatalog:
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
                    return McpReadCatalog(
                        protocol_version=str(initialize.protocolVersion),
                        server_name=str(initialize.serverInfo.name),
                        server_version=str(initialize.serverInfo.version),
                        tools=tuple(tools),
                        connection_latency_ms=round(
                            (time.perf_counter() - started) * 1_000, 3
                        ),
                    )
        except DashboardTransportError:
            raise
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            raise DashboardTransportError(_classify_transport_exception(exc)) from None

    async def execute_read(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float,
        identity_validator: IdentityValidator,
    ) -> McpReadResult:
        started = time.perf_counter()
        timeout = timedelta(seconds=max(1.0, float(timeout_seconds)))
        try:
            async with streamablehttp_client(
                self._url,
                timeout=timeout,
                sse_read_timeout=timeout,
                terminate_on_close=True,
            ) as (read_stream, write_stream, _get_session_id):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timeout,
                    client_info=self._client_info,
                ) as session:
                    initialize = await session.initialize()
                    protocol = str(initialize.protocolVersion)
                    server_name = str(initialize.serverInfo.name)
                    server_version = str(initialize.serverInfo.version)
                    identity_validator(server_name, server_version, protocol)
                    connected = time.perf_counter()
                    result = await session.call_tool(
                        tool_name,
                        arguments,
                        read_timeout_seconds=timeout,
                    )
                    encoded = result.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    )
                    try:
                        size = len(
                            json.dumps(
                                encoded,
                                sort_keys=True,
                                separators=(",", ":"),
                                ensure_ascii=False,
                                allow_nan=False,
                            ).encode("utf-8")
                        )
                    except (TypeError, ValueError, OverflowError):
                        raise DashboardTransportError("invalid_response") from None
                    if size > MAX_GENERIC_UPSTREAM_RESPONSE_BYTES:
                        raise DashboardTransportError("response_too_large")
                    finished = time.perf_counter()
                    return McpReadResult(
                        protocol_version=protocol,
                        server_name=server_name,
                        server_version=server_version,
                        call_result=encoded,
                        connection_latency_ms=round((connected - started) * 1_000, 3),
                        tool_call_latency_ms=round((finished - connected) * 1_000, 3),
                    )
        except DashboardTransportError:
            raise
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            raise DashboardTransportError(_classify_transport_exception(exc)) from None

    @staticmethod
    async def _list_all_tools(session: ClientSession) -> list[dict[str, Any]]:
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
