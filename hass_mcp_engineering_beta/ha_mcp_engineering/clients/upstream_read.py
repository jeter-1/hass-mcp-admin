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
    _iter_exceptions,
)
from .ha_mcp_endpoint import (
    UPSTREAM_ENDPOINT_REJECTED,
    EndpointSnapshot,
    NoRedirectBinding,
    UpstreamEndpointRefused,
    endpoint_snapshot,
    ha_mcp_no_redirect_client_factory,
    latched_or_none,
    require_selector_agreement,
    snapshot_is_current,
)


MAX_GENERIC_UPSTREAM_RESPONSE_BYTES = 1_000_000
# Bound on closing a retained worker of an earlier configuration generation.
STALE_WORKER_CLOSE_SECONDS = 2.0


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
        url: EndpointSnapshot | str,
        *,
        timeout_seconds: float,
        client_version: str,
        retain_session: bool = False,
    ):
        self._snapshot = endpoint_snapshot(url)
        self._url = self._snapshot.url
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

        if not self._retain_session:
            return
        if self._worker is None or self._worker.done():
            return
        operation = self._new_operation("close")
        await self._submit(operation)
        try:
            await self._worker
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise self._copy_operation_error(exc) from None

    def abandon(self) -> None:
        """Cancel the retained worker from any thread without awaiting it.

        Used when a reconfiguration replaces this transport. The worker's own
        cancellation path exits the SDK session; nothing is reused afterwards.
        """

        worker = self._worker
        if worker is None or worker.done():
            return
        try:
            worker.get_loop().call_soon_threadsafe(worker.cancel)
        except RuntimeError:
            # The owning loop is already closed; its tasks are gone with it.
            return

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
        if not self._retain_session:
            return await self._run_isolated(operation)
        if not snapshot_is_current(self._snapshot):
            # A retained worker from an earlier configuration generation is
            # closed before reuse; it never reconnects with a stale endpoint.
            await self._close_stale_worker()
            if operation.kind == "close":
                return None
            raise UpstreamEndpointRefused(
                UPSTREAM_ENDPOINT_REJECTED, tool_call_attempted=False
            ) from None
        if self._operations is None:
            self._operations = asyncio.Queue(
                maxsize=MAX_PENDING_TRANSPORT_OPERATIONS
            )
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_worker())
        worker = self._worker
        if worker is None:
            raise DashboardTransportError("provider_unavailable") from None
        try:
            self._operations.put_nowait(operation)
        except asyncio.QueueFull:
            raise DashboardTransportError("provider_unavailable") from None
        try:
            done, _pending = await asyncio.wait(
                (operation.future, worker),
                timeout=self._operation_budget_seconds(operation),
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            if not operation.future.done():
                operation.future.cancel()
            raise
        if not done and operation.future.done():
            done.add(operation.future)
        if not done and worker.done():
            done.add(worker)
        if not done:
            operation.future.cancel()
            raise DashboardTransportError("timeout") from None
        if operation.future in done:
            try:
                return operation.future.result()
            finally:
                if worker in done and not worker.cancelled():
                    worker.exception()
        if not operation.future.done():
            operation.future.cancel()
        try:
            worker.result()
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise self._copy_operation_error(exc) from None
        raise DashboardTransportError("provider_unavailable") from None

    async def _close_stale_worker(self) -> None:
        """Close a retained worker of an earlier generation, bounded.

        A close request wakes a worker that waits for work; cancellation ends
        an exchange in flight. Cancellation that lands while the SDK's own
        cancel scope is exiting can be absorbed, so it is repeated within a
        fixed bound. A worker that still survives refuses every later request
        itself (``_run_worker`` checks the generation before each session).
        """

        worker = self._worker
        if worker is None or worker.done():
            return
        operations = self._operations
        if operations is not None:
            try:
                operations.put_nowait(self._new_operation("close"))
            except asyncio.QueueFull:
                pass
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STALE_WORKER_CLOSE_SECONDS
        while not worker.done() and loop.time() < deadline:
            worker.cancel()
            await asyncio.wait((worker,), timeout=0.05)
        if worker.done() and not worker.cancelled():
            worker.exception()

    async def _run_isolated(self, operation: _TransportOperation) -> Any:
        """Preserve Beta 54's independent per-operation transport behavior."""

        started = time.perf_counter()
        timeout = timedelta(
            seconds=max(
                1.0,
                float(
                    operation.timeout_seconds
                    if operation.timeout_seconds is not None
                    else self._timeout.total_seconds()
                ),
            )
        )
        binding = NoRedirectBinding(self._snapshot)
        completed = False
        result: Any = None
        try:
            async with streamablehttp_client(
                self._url,
                timeout=timeout,
                sse_read_timeout=timeout,
                terminate_on_close=True,
                httpx_client_factory=ha_mcp_no_redirect_client_factory(binding),
            ) as (read_stream, write_stream, get_session_id):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timeout,
                    client_info=self._client_info,
                ) as session:
                    initialize = await binding.run_until_latched(
                        session.initialize()
                    )
                    fallback_session_id = (
                        "ha-mcp-exchange-" + uuid.uuid4().hex
                    )
                    session_id = self._observed_session_id(
                        get_session_id,
                        fallback_session_id,
                    )
                    result = await binding.run_until_latched(
                        self._run_operation(
                            operation,
                            session=session,
                            initialize=initialize,
                            get_session_id=get_session_id,
                            session_id=session_id,
                            fallback_session_id=fallback_session_id,
                            started=started,
                            binding=binding,
                        )
                    )
                    completed = True
            if not completed:
                # The SDK scopes exited normally without the step's outcome.
                raise binding.unfinished_error()
            return result
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise self._map_operation_error(exc, binding) from None

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
            if pending.future.cancelled():
                pending = None
                continue
            if not snapshot_is_current(self._snapshot):
                # A worker of an earlier configuration generation never opens
                # another session with its stale endpoint.
                if not pending.future.done():
                    pending.future.set_exception(
                        UpstreamEndpointRefused(
                            UPSTREAM_ENDPOINT_REJECTED, tool_call_attempted=False
                        )
                    )
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
            binding = NoRedirectBinding(self._snapshot)
            current: _TransportOperation | None = None
            try:
                async with streamablehttp_client(
                    self._url,
                    timeout=timeout,
                    sse_read_timeout=timeout,
                    terminate_on_close=True,
                    httpx_client_factory=ha_mcp_no_redirect_client_factory(
                        binding
                    ),
                ) as (read_stream, write_stream, get_session_id):
                    async with ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timeout,
                        client_info=self._client_info,
                    ) as session:
                        initialize = await binding.run_until_latched(
                            session.initialize()
                        )
                        fallback_session_id = (
                            "ha-mcp-exchange-" + uuid.uuid4().hex
                        )
                        session_id = self._observed_session_id(
                            get_session_id,
                            fallback_session_id,
                            require_observed=True,
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
                                result = await binding.run_until_latched(
                                    self._run_operation(
                                        current,
                                        session=session,
                                        initialize=initialize,
                                        get_session_id=get_session_id,
                                        session_id=session_id,
                                        fallback_session_id=(
                                            fallback_session_id
                                        ),
                                        started=time.perf_counter(),
                                        binding=binding,
                                    )
                                )
                                if not current.future.done():
                                    current.future.set_result(result)
                            except BaseException as exc:
                                if isinstance(
                                    exc,
                                    (KeyboardInterrupt, SystemExit),
                                ):
                                    raise
                                mapped = self._map_operation_error(
                                    exc, binding, caller_owns_task=False
                                )
                                if not current.future.done():
                                    current.future.set_exception(
                                        self._copy_operation_error(mapped)
                                    )
                                break
                            if not retain_connection:
                                break
                            pending = await operations.get()
                            retain_connection = True
                if current is not None and not current.future.done():
                    # The SDK scopes exited normally without the outcome.
                    current.future.set_exception(
                        self._copy_operation_error(
                            self._map_operation_error(
                                binding.unfinished_error(),
                                binding,
                                caller_owns_task=False,
                            )
                        )
                    )
                if pending is not None and not pending.future.done():
                    pending.future.set_exception(
                        self._copy_operation_error(
                            self._map_operation_error(
                                binding.unfinished_error(),
                                binding,
                                caller_owns_task=False,
                            )
                        )
                    )
                    pending = None
            except asyncio.CancelledError:
                if pending is not None and not pending.future.done():
                    latched = latched_or_none(binding)
                    if latched is not None:
                        pending.future.set_exception(
                            self._copy_operation_error(latched)
                        )
                    else:
                        pending.future.cancel()
                raise
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                if pending is not None and not pending.future.done():
                    pending.future.set_exception(
                        self._copy_operation_error(
                            self._map_operation_error(
                                exc, binding, caller_owns_task=False
                            )
                        )
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
        binding: NoRedirectBinding | None = None,
    ) -> McpReadCatalog | McpReadResult:
        if operation.future.cancelled():
            raise asyncio.CancelledError
        self._require_same_session(
            get_session_id,
            session_id,
            fallback_session_id,
            require_observed=self._retain_session,
        )
        # Version is known only from initialize; admission runs before any
        # list page, and again before every operation on a retained session.
        require_selector_agreement(
            str(initialize.serverInfo.version), self._snapshot, binding
        )
        tools = await self._list_all_tools(session)
        self._require_same_session(
            get_session_id,
            session_id,
            fallback_session_id,
            require_observed=self._retain_session,
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
            get_session_id,
            session_id,
            fallback_session_id,
            require_observed=self._retain_session,
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
            get_session_id,
            session_id,
            fallback_session_id,
            require_observed=self._retain_session,
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
        *,
        require_observed: bool = False,
    ) -> str:
        value = get_session_id()
        if value is None:
            if require_observed:
                raise DashboardTransportError("protocol_error")
            return fallback
        if not isinstance(value, str) or not 1 <= len(value) <= 512:
            raise DashboardTransportError("protocol_error")
        return value

    @classmethod
    def _require_same_session(
        cls,
        get_session_id: Callable[[], str | None],
        expected: str,
        fallback: str,
        *,
        require_observed: bool = False,
    ) -> None:
        if (
            cls._observed_session_id(
                get_session_id,
                fallback,
                require_observed=require_observed,
            )
            != expected
        ):
            raise DashboardTransportError("protocol_error")

    @staticmethod
    def _map_operation_error(
        exc: BaseException,
        binding: NoRedirectBinding | None = None,
        *,
        caller_owns_task: bool = True,
    ) -> BaseException:
        # The refusal latch is consulted before any exception-shape mapping,
        # including SDK task-group wrapping and latch-race cancellation.
        # The retained worker task is never the caller: its cancellation comes
        # from the SDK task group reacting to the latched refusal (or from a
        # replacement closing it), so the latch wins there.
        if caller_owns_task and isinstance(exc, asyncio.CancelledError):
            try:
                task = asyncio.current_task()
            except RuntimeError:
                # Called outside a running loop: no caller task can own it.
                task = None
            if task is not None and task.cancelling():
                # The caller's own cancellation always propagates unchanged.
                return exc
        latched = latched_or_none(binding)
        if latched is not None:
            return latched
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
        if isinstance(exc, BaseExceptionGroup):
            # The pinned SDK session task group wraps a body exception. A group
            # whose sole leaf is a local wrapper (raised before tools/call)
            # keeps it. Mixed or ambiguous groups keep transport semantics.
            leaves = tuple(_iter_exceptions(exc))
            if len(leaves) == 1 and isinstance(
                leaves[0], (BeforeDispatchFailure, CatalogValidationFailure)
            ):
                return leaves[0]
        return DashboardTransportError(_classify_transport_exception(exc))

    def _operation_budget_seconds(
        self,
        operation: _TransportOperation,
    ) -> float:
        return max(
            1.0,
            float(
                operation.timeout_seconds
                if operation.timeout_seconds is not None
                else self._timeout.total_seconds()
            ),
        )

    @classmethod
    def _copy_operation_error(cls, exc: BaseException) -> BaseException:
        """Return a fresh typed error for transfer across an asyncio future."""

        mapped = cls._map_operation_error(exc)
        if isinstance(mapped, DashboardTransportError):
            return DashboardTransportError(
                mapped.category,
                retryable=mapped.retryable,
                grouped_categories=mapped.grouped_categories,
                provider_response_received=(
                    mapped.provider_response_received
                ),
                http_response_received=mapped.http_response_received,
                failure_kind=mapped.failure_kind,
                http_status_class=mapped.http_status_class,
            )
        if isinstance(mapped, BeforeDispatchFailure):
            return BeforeDispatchFailure(mapped.cause)
        if isinstance(mapped, CatalogValidationFailure):
            return CatalogValidationFailure(mapped.cause)
        if isinstance(mapped, asyncio.CancelledError):
            return asyncio.CancelledError(*mapped.args)
        return DashboardTransportError("internal_error")

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
