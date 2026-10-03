"""Closed native inventory reads, isolated from all existing client defaults.

No public route composes this client yet. Its caller must establish all three
Core profiles plus upstream authority, and revalidate the frozen authority at
each dispatch/return. Only states and entity-registry inventory can be read.
"""

import asyncio
from contextlib import asynccontextmanager
import json
import time
import threading

import aiohttp
import httpx
from httpx_sse import EventSource

from ..dashboard_analysis import contracts as c
from ..observability import METRICS
from ..request_context import current_telemetry


class CollectionBudget:
    """One finite shared budget, including upstream discovery/authentication."""

    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.bytes = 0
        self.requests = 0
        self.exhausted = False

    def remaining(self):
        remaining = c.COLLECTION_SECONDS - (self.clock() - self.started)
        if remaining <= 0:
            self.exhausted = True
            raise c.AnalysisError("timeout")
        if self.exhausted:
            raise c.AnalysisError("response_limit")
        return min(c.READ_SECONDS, remaining)

    def charge(self, amount):
        if type(amount) is not int or amount < 0:
            raise c.AnalysisError("invalid_arguments")
        self.remaining()
        self.bytes += amount
        if self.bytes > c.TOTAL_BYTES:
            self.exhausted = True
            raise c.AnalysisError("response_limit")


async def no_retry(request, handler):
    # Converting the specific exceptions also prevents aiohttp's idempotent
    # persistent-connection retry. Redirect following is disabled per request.
    try:
        response = await handler(request)
        # ws_connect does not expose allow_redirects. Refuse the response in
        # middleware before aiohttp can follow it, including during upgrade.
        if response.status in (401, 403):
            response.close()
            raise c.AnalysisError("access_denied")
        if 300 <= response.status < 400:
            response.close()
            raise c.AnalysisError("source_unavailable")
        return response
    except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError):
        raise c.AnalysisError("source_unavailable") from None


class DashboardAnalysisClient:
    def __init__(self, settings):
        self._api_url = settings.api_url
        self._ws_url = settings.websocket_url
        self._token = settings.ha_token
        self._read_timeout = min(c.READ_SECONDS, settings.ha_timeout_seconds)

    @asynccontextmanager
    async def collection(self, version, authorize, budget):
        peer = InventoryPeer(self, version, authorize, budget)
        try:
            yield peer
        finally:
            await peer.close()


class InventoryPeer:
    def __init__(self, client, version, authorize, budget):
        self.client, self.version, self.authorize, self.budget = client, version, authorize, budget
        self.session = self.websocket = None
        self.reads = set()

    @asynccontextmanager
    async def attempt(self, kind):
        self.authorize()
        seconds = min(self.budget.remaining(), self.client._read_timeout)
        self.budget.requests += 1
        telemetry = current_telemetry()
        started, timed_out = time.perf_counter(), False
        if telemetry:
            telemetry.begin_ha_attempt(started)
        try:
            async with asyncio.timeout(seconds):
                yield
            self.authorize()
        except TimeoutError:
            timed_out = True
            raise c.AnalysisError("timeout") from None
        except (aiohttp.ClientError, OSError):
            raise c.AnalysisError("source_unavailable") from None
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            METRICS.record_ha(elapsed, timeout=timed_out)
            if telemetry:
                telemetry.ha_duration_ms += elapsed
                telemetry.finish_ha_attempt(time.perf_counter())
                telemetry.timeout_occurred |= timed_out
                telemetry.endpoint_categories.add("dashboard_analysis_" + kind)

    def _session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.client._read_timeout,
                                             ceil_threshold=float("inf")),
                auto_decompress=False, middlewares=(no_retry,),
                headers={"Accept-Encoding": "identity"}, trust_env=False)
        return self.session

    async def receive(self, maximum, *, auth=False):
        frame = await self.websocket.receive()
        if frame.type != aiohttp.WSMsgType.TEXT:
            raise c.AnalysisError("malformed_response")
        # Bound characters before UTF-8 allocation, bytes before JSON decoding.
        if len(frame.data) > maximum:
            raise c.AnalysisError("response_limit")
        try:
            raw = frame.data.encode("utf-8")
        except UnicodeError:
            raise c.AnalysisError("malformed_response") from None
        self.budget.charge(len(raw))
        result = await c.worker(c.parse, raw, maximum=maximum,
                                depth=4 if auth else c.INVENTORY_DEPTH + 1)
        if type(result) is not dict:
            raise c.AnalysisError("malformed_response")
        return result

    async def _registry(self):
        async with self.attempt("registry"):
            self.websocket = await self._session().ws_connect(
                self.client._ws_url, max_msg_size=c.INVENTORY_BYTES + 1,
                compress=0, autoping=False,
                timeout=aiohttp.ClientWSTimeout(ws_receive=self.client._read_timeout, ws_close=1))
            greeting = await self.receive(c.AUTH_BYTES, auth=True)
            if greeting.get("type") != "auth_required":
                raise c.AnalysisError("malformed_response")
            if greeting.get("ha_version") != self.version:
                raise c.AnalysisError("authority_drift")
            await self.websocket.send_json({"type": "auth", "access_token": self.client._token})
            authenticated = await self.receive(c.AUTH_BYTES, auth=True)
            if authenticated.get("type") != "auth_ok":
                raise c.AnalysisError("access_denied")
            if authenticated.get("ha_version") != self.version:
                raise c.AnalysisError("authority_drift")
            self.authorize()
            await self.websocket.send_json({"id": 1, "type": "config/entity_registry/list"})
            response = await self.receive(c.INVENTORY_BYTES)
            if (response.get("type") != "result" or type(response.get("id")) is not int
                    or response["id"] != 1 or type(response.get("success")) is not bool):
                raise c.AnalysisError("malformed_response")
            if response["success"] is not True:
                # An unclassified rejection might be an authority failure; it
                # cannot become partial-success evidence or a claimed denial.
                error = response.get("error")
                denied = type(error) is dict and error.get("code") == "unauthorized"
                raise c.AnalysisError("access_denied" if denied else "source_rejected")
            if "result" not in response or "error" in response:
                raise c.AnalysisError("malformed_response")
            return response["result"]

    async def _states(self):
        async with self.attempt("states"):
            async with self._session().get(
                self.client._api_url + "/states", allow_redirects=False,
                headers={"Authorization": "Bearer " + self.client._token}) as response:
                if response.status in (401, 403):
                    raise c.AnalysisError("access_denied")
                if response.status != 200:
                    raise c.AnalysisError("source_unavailable")
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise c.AnalysisError("malformed_response")
                size, chunks = 0, []
                async for chunk in response.content.iter_chunked(16_384):
                    self.budget.charge(len(chunk))
                    size += len(chunk)
                    if size > c.INVENTORY_BYTES:
                        raise c.AnalysisError("response_limit")
                    chunks.append(chunk)
                return await c.worker(c.parse, b"".join(chunks))

    async def read(self, kind):
        if kind not in {"states", "registry"} or kind in self.reads:
            raise c.AnalysisError("invalid_arguments")
        self.reads.add(kind)
        return await (self._states() if kind == "states" else self._registry())

    async def close(self):
        try:
            if self.websocket is not None:
                await self.websocket.close()
        finally:
            if self.session is not None:
                await self.session.close()


class _McpBytes(httpx.AsyncByteStream):
    """Charge raw bytes before HTTP/SSE text or JSON decoding."""

    def __init__(self, stream, budget, maximum):
        self.stream, self.budget, self.maximum = stream, budget, maximum
        self.used = 0

    async def __aiter__(self):
        async for chunk in self.stream:
            self.budget.charge(len(chunk))
            self.used += len(chunk)
            if self.used > self.maximum:
                raise c.AnalysisError("response_limit")
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class _AnalysisMcpHttp(httpx.AsyncBaseTransport):
    """Private application class using only public HTTPX/SDK extension APIs.

    The SDK may schedule background GETs; none reach the delegate. POST SSE is
    bounded and reduced to one strict JSON-RPC response before the SDK sees it,
    so incomplete streams cannot enter its resumption/reconnection path.
    """

    def __init__(self, url, arguments, budget, stopped, owner_call, authorize, deadline):
        self.url, self.arguments, self.budget = httpx.URL(url), arguments, budget
        self.stopped, self.owner_call, self.authorize = stopped, owner_call, authorize
        self.deadline = deadline
        self.delegate = httpx.AsyncHTTPTransport(retries=0)
        self.counts = {}
        self.failure = None

    async def aclose(self):
        await self.delegate.aclose()

    async def handle_async_request(self, request):
        try:
            return await self._request(request)
        except Exception as error:
            self.failure = error
            raise

    async def _request(self, request):
        from .mcp import DashboardTransportError, REQUIRED_DASHBOARD_TOOL, MAX_TOOL_CATALOG_PAGES

        if request.url != self.url:
            raise DashboardTransportError("endpoint_rejected", retryable=False)
        if request.method == "GET":
            # Synthetic local refusal: no background subscription or resume I/O.
            return httpx.Response(405, request=request)
        if time.monotonic() >= self.deadline:
            raise DashboardTransportError("timeout")
        if self.stopped.is_set() and request.method != "DELETE":
            raise asyncio.CancelledError()
        if request.method == "DELETE":
            operation, maximum, message = "session_cleanup", c.AUTH_BYTES, None
        elif request.method == "POST":
            message = c.parse(request.content, maximum=c.AUTH_BYTES, depth=8)
            if type(message) is not dict:
                raise DashboardTransportError("prohibited_argument", retryable=False)
            operation = message.get("method")
            if operation not in {"initialize", "notifications/initialized", "tools/list", "tools/call"}:
                raise DashboardTransportError("prohibited_argument", retryable=False)
            if operation == "tools/call" and message.get("params") != {
                "name": REQUIRED_DASHBOARD_TOOL, "arguments": self.arguments,
            }:
                raise DashboardTransportError("prohibited_argument", retryable=False)
            maximum = c.DASHBOARD_BYTES if operation in {"tools/list", "tools/call"} else c.AUTH_BYTES
        else:
            raise DashboardTransportError("prohibited_argument", retryable=False)
        self.counts[operation] = self.counts.get(operation, 0) + 1
        if self.counts[operation] > (MAX_TOOL_CATALOG_PAGES if operation == "tools/list" else 1):
            raise DashboardTransportError("prohibited_argument", retryable=False)
        if operation != "session_cleanup":
            await self.owner_call(self.authorize)
            if self.stopped.is_set():
                raise asyncio.CancelledError()
        self.budget.remaining()
        self.budget.requests += 1
        request.headers["Accept-Encoding"] = "identity"
        response = await self.delegate.handle_async_request(request)
        response.stream = _McpBytes(response.stream, self.budget, maximum)
        try:
            if response.status_code in (401, 403):
                raise DashboardTransportError("authentication_failed", retryable=False)
            if 300 <= response.status_code < 400:
                raise DashboardTransportError("endpoint_rejected", retryable=False)
            if response.status_code >= 400:
                raise DashboardTransportError("upstream_error")
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise DashboardTransportError("invalid_response", retryable=False)
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if operation in {"notifications/initialized", "session_cleanup"}:
                raw = await response.aread()
                return httpx.Response(response.status_code, headers=response.headers,
                                      content=raw, request=request)
            if content_type == "application/json":
                raw = await response.aread()
                decoded = c.parse(raw, maximum=maximum, depth=64)
            elif content_type == "text/event-stream":
                decoded = None
                async for event in EventSource(response).aiter_sse():
                    if not event.data:
                        continue
                    if event.event != "message":
                        raise DashboardTransportError("protocol_error", retryable=False)
                    decoded = c.parse(event.data.encode("utf-8"), maximum=maximum, depth=64)
                    break
                if decoded is None:
                    raise DashboardTransportError("protocol_error", retryable=False)
                raw = c.canonical(decoded)
                if len(raw) > maximum:
                    raise c.AnalysisError("response_limit")
            else:
                raise DashboardTransportError("protocol_error", retryable=False)
            if (type(decoded) is not dict or decoded.get("jsonrpc") != "2.0"
                    or type(decoded.get("id")) is not type(message.get("id"))
                    or decoded.get("id") != message.get("id")
                    or ("result" in decoded) == ("error" in decoded)
                    or "method" in decoded):
                raise DashboardTransportError("protocol_error", retryable=False)
            headers = dict(response.headers)
            headers["content-type"] = "application/json"
            headers.pop("content-length", None)
            return httpx.Response(200, headers=headers, content=raw, request=request)
        finally:
            await response.aclose()


async def bounded_mcp_read(transport, arguments, capability_validator, *, authorize, budget):
    """Run bounded SDK decoding on an owned worker; authority stays on its loop."""
    from datetime import timedelta
    from mcp.client.streamable_http import streamablehttp_client
    from ..mcp_sdk_compatibility import ReviewedProtocolClientSession
    from .mcp import (DashboardTransportError, McpDashboardHandshake, McpDashboardRead,
                      REQUIRED_DASHBOARD_TOOL, MAX_UPSTREAM_CONTENT_CHARS,
                      _classified_transport_error, _iter_exceptions)

    owner = asyncio.get_running_loop()
    stopped = threading.Event()
    authorize()
    seconds = min(budget.remaining(), transport._timeout.total_seconds())
    deadline = time.monotonic() + seconds

    def admit_call():
        authorize()
        telemetry = current_telemetry()
        if telemetry is not None and not telemetry.authorize_core_dispatch():
            raise DashboardTransportError("connection_failed", retryable=False)

    async def on_owner(function, *args):
        async def invoke():
            if stopped.is_set():
                raise asyncio.CancelledError()
            return function(*args)
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(invoke(), owner))

    def run():
        async def exchange():
            started = time.perf_counter()
            fence = _AnalysisMcpHttp(transport._url, arguments, budget, stopped, on_owner, authorize, deadline)
            def factory(headers=None, timeout=None, auth=None):
                if auth is not None:
                    raise DashboardTransportError("prohibited_argument", retryable=False)
                return httpx.AsyncClient(transport=fence, headers=headers, timeout=timeout,
                                        trust_env=False, follow_redirects=False)
            async with asyncio.timeout_at(deadline):
                async with streamablehttp_client(transport._url, timeout=seconds,
                        sse_read_timeout=seconds, httpx_client_factory=factory,
                        terminate_on_close=True) as (read, write, _):
                    async with ReviewedProtocolClientSession(read, write,
                            read_timeout_seconds=timedelta(seconds=seconds),
                            client_info=transport._client_info) as session:
                        initialized = await session.initialize()
                        tools = await transport._list_all_tools(session)
                        handshake = McpDashboardHandshake(str(initialized.protocolVersion),
                            str(initialized.serverInfo.name), str(initialized.serverInfo.version),
                            tuple(tools), round((time.perf_counter() - started) * 1000, 3))
                        await on_owner(capability_validator, handshake)
                        await on_owner(admit_call)
                        call_started = time.perf_counter()
                        result = await session.call_tool(REQUIRED_DASHBOARD_TOOL, arguments,
                            read_timeout_seconds=timedelta(seconds=seconds))
                        encoded = result.model_dump(mode="json", by_alias=True, exclude_none=True)
                        # Preserve the pre-existing stricter content-character bound.
                        if len(json.dumps(encoded, default=str)) > MAX_UPSTREAM_CONTENT_CHARS:
                            raise DashboardTransportError("response_too_large", retryable=False)
                        result = McpDashboardRead(handshake, encoded,
                            round((time.perf_counter() - call_started) * 1000, 3))
                if fence.failure is not None:
                    raise fence.failure
                if time.monotonic() >= deadline:
                    raise DashboardTransportError("timeout")
                return result
        try:
            return asyncio.run(exchange())
        except BaseException as error:
            # SDK task groups must not demote an authority/limit failure into a
            # transient provider error. All exposed reasons remain fixed.
            for leaf in _iter_exceptions(error):
                if isinstance(leaf, c.AnalysisError):
                    raise leaf from None
            if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            raise _classified_transport_error(error) from None

    task = asyncio.create_task(asyncio.to_thread(run))
    try:
        result = await asyncio.shield(task)
    except asyncio.CancelledError:
        stopped.set()
        # The worker's finite timeout owns all SDK I/O and closes it before a
        # collector can release its single in-flight capacity slot.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if task.done() and not task.cancelled():
            task.exception()
        raise
    authorize()
    return result
