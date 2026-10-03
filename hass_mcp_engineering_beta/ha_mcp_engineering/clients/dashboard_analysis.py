"""Closed native inventory reads, isolated from all existing client defaults.

No public route composes this client yet. Its caller must establish all three
Core profiles plus upstream authority, and revalidate the frozen authority at
each dispatch/return. Only states and entity-registry inventory can be read.
"""

import asyncio
from contextlib import asynccontextmanager
import time

import aiohttp

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
        return await handler(request)
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
