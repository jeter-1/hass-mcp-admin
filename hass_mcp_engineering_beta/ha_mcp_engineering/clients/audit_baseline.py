"""Fixed, single-attempt native reads for an explicitly scoped baseline capture."""

import asyncio
from contextlib import asynccontextmanager
import time
from urllib.parse import quote

import aiohttp

from ..audit_baseline import capture_contracts as c
from ..observability import METRICS
from ..request_context import current_telemetry


async def no_retry(request, handler):
    try:
        response = await handler(request)
    except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError):
        raise c.CaptureError("source_unavailable") from None
    if 300 <= response.status < 400:
        response.close()
        raise c.CaptureError("source_unavailable")
    return response


class BaselineReadClient:
    def __init__(self, settings):
        # Freeze one origin/credential pair; never expose or hash these values.
        self._api_url = settings.api_url
        self._ws_url = settings.websocket_url
        self._token = settings.ha_token

    @asynccontextmanager
    async def capture(self, version, authorize):
        peer = BaselinePeer(self, version, authorize)
        try:
            await peer.open()
            yield peer
        finally:
            await peer.close()


class BaselinePeer:
    def __init__(self, client, version, authorize):
        self.client, self.version, self.authorize = client, version, authorize
        self.started = time.monotonic()
        self.consumed = self.requests = self.commands = 0
        self.session = self.websocket = None
        self.exhausted = False

    def remaining(self):
        seconds = c.COLLECTION_SECONDS - (time.monotonic() - self.started)
        if seconds <= 0:
            raise c.CaptureError("timeout")
        return min(c.READ_SECONDS, seconds)

    def charge(self, amount):
        self.consumed += amount
        if self.consumed > c.TOTAL_BYTES:
            self.exhausted = True
            raise c.CaptureError("response_limit")

    @asynccontextmanager
    async def attempt(self, category):
        self.authorize()
        if self.exhausted or self.consumed >= c.TOTAL_BYTES or self.requests >= c.MAX_RECORDS + 11:
            raise c.CaptureError("response_limit")
        self.requests += 1
        telemetry = current_telemetry()
        started, timed_out = time.perf_counter(), False
        if telemetry:
            telemetry.begin_ha_attempt(started)
        try:
            async with asyncio.timeout(self.remaining()):
                yield
            self.authorize()
        except (TimeoutError, asyncio.TimeoutError):
            timed_out = True
            raise c.CaptureError("timeout") from None
        except (aiohttp.ClientError, OSError):
            raise c.CaptureError("source_unavailable") from None
        finally:
            finished = time.perf_counter()
            duration = (finished - started) * 1000
            METRICS.record_ha(duration, timeout=timed_out)
            if telemetry:
                telemetry.ha_duration_ms += duration
                telemetry.finish_ha_attempt(finished)
                telemetry.timeout_occurred |= timed_out
                telemetry.endpoint_categories.add("automation_baseline_" + category)

    async def receive(self, maximum):
        frame = await self.websocket.receive()
        if frame.type != aiohttp.WSMsgType.TEXT:
            raise c.CaptureError("malformed_response")
        try:
            raw = frame.data.encode("utf-8")
        except UnicodeError:
            raise c.CaptureError("malformed_response") from None
        self.charge(len(raw))
        if len(raw) > maximum:
            raise c.CaptureError("response_limit")
        result = await c.worker(c.parse, raw)
        if type(result) is not dict:
            raise c.CaptureError("malformed_response")
        return result

    async def open(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=c.READ_SECONDS, ceil_threshold=float("inf")),
            middlewares=(no_retry,), auto_decompress=False,
            headers={"Accept-Encoding": "identity"})
        async with self.attempt("authentication"):
            self.websocket = await self.session.ws_connect(
                self.client._ws_url, max_msg_size=c.FRAME_BYTES + 1, compress=0, autoping=False,
                timeout=aiohttp.ClientWSTimeout(ws_receive=c.READ_SECONDS, ws_close=1))
            greeting = await self.receive(c.AUTH_BYTES)
            if greeting.get("type") != "auth_required":
                raise c.CaptureError("malformed_response")
            await self.websocket.send_json({"type": "auth", "access_token": self.client._token})
            response = await self.receive(c.AUTH_BYTES)
            if response.get("type") != "auth_ok":
                raise c.CaptureError("access_denied")
            if response.get("ha_version") != self.version:
                raise c.CaptureError("authority_drift")

    async def read(self, kind):
        if kind == "states":
            return await self._rest("/states", c.FRAME_BYTES, "states")
        if kind not in c.COMMANDS or self.commands >= 8:
            raise c.CaptureError("invalid_arguments")
        self.commands += 1
        async with self.attempt(kind):
            await self.websocket.send_json({"id": self.commands, **c.COMMANDS[kind]})
            message = await self.receive(c.FRAME_BYTES)
            if (message.get("type") != "result" or type(message.get("id")) is not int
                    or message["id"] != self.commands or type(message.get("success")) is not bool):
                raise c.CaptureError("malformed_response")
            if not message["success"]:
                raise c.CaptureError("source_unavailable")
            if "result" not in message or "error" in message:
                raise c.CaptureError("malformed_response")
            return message["result"]

    async def configuration(self, configuration_id):
        if (type(configuration_id) is not str or not c.ID.fullmatch(configuration_id)
                or configuration_id in {".", ".."}):
            raise c.CaptureError("invalid_arguments")
        # IDs containing ':' remain data in one path segment; no traversal or query.
        path = "/config/automation/config/" + quote(configuration_id, safe="")
        result = await self._rest(path, c.CONFIG_BYTES, "configuration")
        if type(result) is not dict:
            raise c.CaptureError("malformed_response")
        return result

    async def _rest(self, path, maximum, category):
        async with self.attempt(category):
            async with self.session.get(
                self.client._api_url + path, allow_redirects=False,
                headers={"Authorization": "Bearer " + self.client._token}) as response:
                if response.status in (401, 403):
                    raise c.CaptureError("access_denied")
                if response.status != 200:
                    raise c.CaptureError("configuration_unavailable" if category == "configuration"
                                         else "source_unavailable")
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise c.CaptureError("malformed_response")
                body, size = [], 0
                async for chunk in response.content.iter_chunked(16_384):
                    self.charge(len(chunk))
                    size += len(chunk)
                    if size > maximum:
                        raise c.CaptureError("response_limit")
                    body.append(chunk)
                result = await c.worker(c.parse, b"".join(body))
                return result

    async def close(self):
        try:
            if self.websocket is not None:
                await self.websocket.close()
        finally:
            if self.session is not None:
                await self.session.close()
