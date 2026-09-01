"""Bounded MCP transport for reviewed generic upstream read delegation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import json
import logging
import inspect
import time
import uuid
from typing import Any, Awaitable, Callable

from mcp import types
from mcp.client.streamable_http import streamablehttp_client

from ..mcp_sdk_compatibility import ReviewedProtocolClientSession as ClientSession
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
    session_id: str = ""
    catalog_complete: bool = True


@dataclass(frozen=True)
class McpReadResult:
    protocol_version: str
    server_name: str
    server_version: str
    call_result: dict[str, Any]
    connection_latency_ms: float
    tool_call_latency_ms: float


CatalogValidator = Callable[[McpReadCatalog], None]
BeforeDispatch = Callable[[], None | Awaitable[None]]
MAX_PENDING_TRANSPORT_OPERATIONS = 64


@dataclass
class _TransportOperation:
    kind: str
    future: asyncio.Future[Any]
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None
    timeout_seconds: float | None = None
    catalog_validator: CatalogValidator | None = None
    before_dispatch: BeforeDispatch | None = None


class BeforeDispatchFailure(RuntimeError):
    """Preserve a local persistence failure without classifying it as upstream."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__("The local pre-dispatch callback failed.")
        self.cause = cause


class CatalogValidationFailure(RuntimeError):
    """Preserve a typed local validator failure without exposing it broadly."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__("The local catalog validator failed.")
        self.cause = cause


class McpReadGatewayTransport:
    """Open bounded sessions without exposing the secret-bearing endpoint."""

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float,
        client_version: str,
        retain_session: bool = False,
    ):
        self._url = url
        self._timeout = timedelta(seconds=max(1.0, float(timeout_seconds)))
        self._client_info = types.Implementation(
            name="hass-mcp-engineering-read-gateway",
            version=client_version,
        )
        self._retain_session = bool(retain_session)
        self._operations: asyncio.Queue[_TransportOperation] | None = None
        self._worker: asyncio.Task[None] | None = None
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
        operation = self._new_operation("discover")
        return await self._submit(operation)

    async def execute_read(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float,
        catalog_validator: CatalogValidator,
        before_dispatch: BeforeDispatch | None = None,
    ) -> McpReadResult:
        operation = self._new_operation(
            "execute",
            tool_name=tool_name,
            arguments=dict(arguments),
            timeout_seconds=timeout_seconds,
            catalog_validator=catalog_validator,
            before_dispatch=before_dispatch,
        )
        return await self._submit(operation)

    async def aclose(self) -> None:
        """Close the retained upstream exchange in its owning worker task."""

        if self._worker is None or self._worker.done():
            return
        operation = self._new_operation("close")
        await self._submit(operation)
        await self._worker

    def _new_operation(
        self,
        kind: str,
        **values: Any,
    ) -> _TransportOperation:
        loop = asyncio.get_running_loop()
        return _TransportOperation(
            kind=kind,
            future=loop.create_future(),
            **values,
        )

    async def _submit(self, operation: _TransportOperation) -> Any:
        if self._operations is None:
            self._operations = asyncio.Queue(
                maxsize=MAX_PENDING_TRANSPORT_OPERATIONS
            )
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_worker())
        try:
            self._operations.put_nowait(operation)
        except asyncio.QueueFull:
            raise DashboardTransportError("provider_unavailable") from None
        return await operation.future

    async def _run_worker(self) -> None:
        operations = self._operations
        if operations is None:
            return
        pending: _TransportOperation | None = None
        while True:
            if pending is None:
                pending = await operations.get()
            if pending.kind == "close":
                if not pending.future.done():
                    pending.future.set_result(None)
                return
            started = time.perf_counter()
            timeout = timedelta(
                seconds=max(
                    1.0,
                    float(
                        pending.timeout_seconds
                        if pending.timeout_seconds is not None
                        else self._timeout.total_seconds()
                    ),
                )
            )
            try:
                async with streamablehttp_client(
                    self._url,
                    timeout=timeout,
                    sse_read_timeout=timeout,
                    terminate_on_close=True,
                ) as (read_stream, write_stream, get_session_id):
                    async with ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timeout,
                        client_info=self._client_info,
                    ) as session:
                        initialize = await session.initialize()
                        fallback_session_id = (
                            "ha-mcp-exchange-" + uuid.uuid4().hex
                        )
                        session_id = self._observed_session_id(
                            get_session_id, fallback_session_id
                        )
                        retain_connection = (
                            self._retain_session
                            and pending.kind == "discover"
                        )
                        while True:
                            current = pending
                            pending = None
                            if current.kind == "close":
                                if not current.future.done():
                                    current.future.set_result(None)
                                return
                            try:
                                result = await self._run_operation(
                                    current,
                                    session=session,
                                    initialize=initialize,
                                    get_session_id=get_session_id,
                                    session_id=session_id,
                                    fallback_session_id=fallback_session_id,
                                    started=time.perf_counter(),
                                )
                                if not current.future.done():
                                    current.future.set_result(result)
                            except BaseException as exc:
                                if isinstance(
                                    exc,
                                    (KeyboardInterrupt, SystemExit),
                                ):
                                    raise
                                mapped = self._map_operation_error(exc)
                                if not current.future.done():
                                    current.future.set_exception(mapped)
                                break
                            if not retain_connection:
                                break
                            pending = await operations.get()
                            retain_connection = True
            except asyncio.CancelledError:
                if pending is not None and not pending.future.done():
                    pending.future.cancel()
                raise
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                if pending is not None and not pending.future.done():
                    pending.future.set_exception(
                        self._map_operation_error(exc)
                    )
                pending = None

    async def _run_operation(
        self,
        operation: _TransportOperation,
        *,
        session: ClientSession,
        initialize: Any,
        get_session_id: Callable[[], str | None],
        session_id: str,
        fallback_session_id: str,
        started: float,
    ) -> McpReadCatalog | McpReadResult:
        if operation.future.cancelled():
            raise asyncio.CancelledError
        self._require_same_session(
            get_session_id, session_id, fallback_session_id
        )
        tools = await self._list_all_tools(session)
        self._require_same_session(
            get_session_id, session_id, fallback_session_id
        )
        catalog = McpReadCatalog(
            protocol_version=str(initialize.protocolVersion),
            server_name=str(initialize.serverInfo.name),
            server_version=str(initialize.serverInfo.version),
            tools=tuple(tools),
            connection_latency_ms=round(
                (time.perf_counter() - started) * 1_000, 3
            ),
            session_id=session_id,
            catalog_complete=True,
        )
        if operation.kind == "discover":
            return catalog
        if (
            operation.kind != "execute"
            or operation.tool_name is None
            or operation.arguments is None
            or operation.catalog_validator is None
        ):
            raise DashboardTransportError("internal_error")
        try:
            operation.catalog_validator(catalog)
        except DashboardTransportError:
            raise
        except BaseException as exc:
            if isinstance(
                exc,
                (asyncio.CancelledError, KeyboardInterrupt, SystemExit),
            ):
                raise
            raise CatalogValidationFailure(exc) from None
        if operation.future.cancelled():
            raise asyncio.CancelledError
        self._require_same_session(
            get_session_id, session_id, fallback_session_id
        )
        if operation.before_dispatch is not None:
            try:
                prepared = operation.before_dispatch()
                if inspect.isawaitable(prepared):
                    await prepared
            except BaseException as exc:
                if isinstance(
                    exc,
                    (asyncio.CancelledError, KeyboardInterrupt, SystemExit),
                ):
                    raise
                raise BeforeDispatchFailure(exc) from None
        self._require_same_session(
            get_session_id, session_id, fallback_session_id
        )
        connected = time.perf_counter()
        operation_timeout = timedelta(
            seconds=max(1.0, float(operation.timeout_seconds or 1.0))
        )
        result = await session.call_tool(
            operation.tool_name,
            operation.arguments,
            read_timeout_seconds=operation_timeout,
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
            protocol_version=catalog.protocol_version,
            server_name=catalog.server_name,
            server_version=catalog.server_version,
            call_result=encoded,
            connection_latency_ms=catalog.connection_latency_ms,
            tool_call_latency_ms=round(
                (finished - connected) * 1_000, 3
            ),
        )

    @staticmethod
    def _observed_session_id(
        get_session_id: Callable[[], str | None],
        fallback: str,
    ) -> str:
        value = get_session_id()
        if (
            value is None
            or not isinstance(value, str)
            or not 1 <= len(value) <= 512
        ):
            return fallback
        return value

    @classmethod
    def _require_same_session(
        cls,
        get_session_id: Callable[[], str | None],
        expected: str,
        fallback: str,
    ) -> None:
        if cls._observed_session_id(get_session_id, fallback) != expected:
            raise DashboardTransportError("protocol_error")

    @staticmethod
    def _map_operation_error(exc: BaseException) -> BaseException:
        if isinstance(
            exc,
            (
                DashboardTransportError,
                BeforeDispatchFailure,
                CatalogValidationFailure,
                asyncio.CancelledError,
            ),
        ):
            return exc
        return DashboardTransportError(_classify_transport_exception(exc))

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
