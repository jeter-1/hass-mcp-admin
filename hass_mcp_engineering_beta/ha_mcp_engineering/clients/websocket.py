"""Observable Home Assistant WebSocket transport with safe error mapping."""

import asyncio
import re
import time
from typing import Any

import aiohttp

from ..configuration import Settings
from ..errors import (
    AuthorizationError,
    ErrorCode,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ..observability import METRICS
from ..request_context import current_telemetry
from .single_read import SINGLE_READ, MAX_READ_BYTES, session_options

# HAMCP-209: the only commands a pipelined batch may carry. Each is an
# in-memory read of one loaded automation or (HAMCP-212) one loaded script;
# anything else is refused before dispatch.
BATCH_WINDOW = 16
# Two commands per source, bounded by the 1,000 automation and 1,000 script
# candidate limits of the dependency provider.
MAX_BATCH_COMMANDS = 4_096
_BATCH_COMMAND_KEYS = {
    "automation/config": frozenset({"type", "entity_id"}),
    "script/config": frozenset({"type", "entity_id"}),
    "search/related": frozenset({"type", "item_type", "item_id"}),
}
_AUTOMATION_ENTITY = re.compile(r"automation\.[a-z0-9_]{1,245}\Z", re.ASCII)
_SCRIPT_ENTITY = re.compile(r"script\.[a-z0-9_]{1,121}\Z", re.ASCII)
_BATCH_IDENTITY = {
    "automation/config": _AUTOMATION_ENTITY,
    "script/config": _SCRIPT_ENTITY,
}
_SEARCH_IDENTITY = {"automation": _AUTOMATION_ENTITY, "script": _SCRIPT_ENTITY}


def batch_command_allowed(payload: Any) -> bool:
    """Admit only the fixed loaded-automation and loaded-script read shapes."""

    if type(payload) is not dict:
        return False
    command_type = payload.get("type")
    keys = _BATCH_COMMAND_KEYS.get(command_type) if isinstance(command_type, str) else None
    if keys is None or frozenset(payload) != keys:
        return False
    if command_type == "search/related":
        item_type = payload.get("item_type")
        pattern = _SEARCH_IDENTITY.get(item_type) if isinstance(item_type, str) else None
        identity = payload.get("item_id")
    else:
        pattern = _BATCH_IDENTITY[command_type]
        identity = payload.get("entity_id")
    return pattern is not None and isinstance(identity, str) and pattern.fullmatch(identity) is not None


class HomeAssistantWebSocketClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def _category(payload: dict) -> str:
        command_type = payload.get("type")
        return str(command_type)[:128] if command_type else "websocket"

    @staticmethod
    def _error_details(
        category: str,
        *,
        status: int | None = None,
        provider_response_received: bool = False,
    ) -> dict:
        details = {"method": "WEBSOCKET", "endpoint_category": category}
        if status is not None:
            details["status"] = status
        if provider_response_received:
            details["provider_response_received"] = True
        return details

    def _record(self, started: float, category: str, *, timeout: bool = False) -> None:
        finished = time.perf_counter()
        duration = round((finished - started) * 1000, 3)
        METRICS.record_ha(duration, timeout=timeout)
        telemetry = current_telemetry()
        if telemetry:
            telemetry.ha_duration_ms += duration
            telemetry.finish_ha_attempt(finished)
            telemetry.timeout_occurred = telemetry.timeout_occurred or timeout
            telemetry.endpoint_categories.add(category)

    @staticmethod
    def _set_error(code: ErrorCode) -> None:
        telemetry = current_telemetry()
        if telemetry:
            telemetry.error_code = code.value

    async def read_alarmo_inspection(
        self, kind, *, core_version: str, remaining_seconds: float,
        remaining_bytes: int, entity_ids: tuple[str, ...] | None = None,
    ) -> tuple[Any, int]:
        """One closed, bounded read. No retry, redirects, subscription or fallback."""
        import json
        import math

        from ..integration_inspection.contracts import (
            AUTH_BYTES, COMMAND_SECONDS, FRAME_BYTES, MAX_FRAMES, command_payload,
        )
        from ..integration_inspection.models import InspectionError

        payload = command_payload(kind, entity_ids)
        if (not math.isfinite(remaining_seconds) or remaining_seconds <= 0
                or type(remaining_bytes) is not int or remaining_bytes <= 0):
            raise InspectionError("timeout" if remaining_seconds <= 0 else "response_bytes")
        deadline = min(COMMAND_SECONDS, remaining_seconds)
        # Reserve both bounded auth frames before choosing the result limit.
        # Exactly three frames are accepted; unsolicited events are rejected.
        if remaining_bytes <= 2 * AUTH_BYTES:
            raise InspectionError("response_bytes")
        frame_limit = min(FRAME_BYTES, remaining_bytes - 2 * AUTH_BYTES)
        telemetry = current_telemetry()

        def authorized():
            if (telemetry is None or telemetry.core_dispatch_authorizer is None
                    or not telemetry.authorize_core_dispatch()):
                raise InspectionError("authority_unavailable")

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise InspectionError("malformed_response")
                result[key] = value
            return result

        def finite(value):
            raise InspectionError("malformed_response")

        def float_value(value):
            parsed = float(value)
            if not math.isfinite(parsed):
                raise InspectionError("malformed_response")
            return parsed

        async def no_retry(request, handler):
            try:
                return await handler(request)
            except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError):
                raise InspectionError("source_unavailable") from None

        async def reject_redirect(*_args):
            raise InspectionError("source_unavailable")

        authorized()
        started = time.perf_counter()
        telemetry.begin_ha_attempt(started)
        trace = aiohttp.TraceConfig()
        trace.on_request_redirect.append(reject_redirect)
        consumed = 0
        frames = 0
        timed_out = False
        try:
            async with asyncio.timeout(deadline):
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=deadline), trust_env=False,
                    auto_decompress=False, middlewares=(no_retry,), trace_configs=[trace],
                ) as session:
                    async with session.ws_connect(
                        # aiohttp rejects at >= max_msg_size; keep the public
                        # byte ceiling inclusive and check it before decoding.
                        self.settings.websocket_url, max_msg_size=frame_limit + 1,
                        autoping=False, compress=0,
                        timeout=aiohttp.ClientWSTimeout(ws_receive=deadline, ws_close=min(1, deadline)),
                    ) as websocket:
                        async def receive(*, auth=False):
                            nonlocal frames, consumed
                            frames += 1
                            if frames > MAX_FRAMES:
                                raise InspectionError("malformed_response")
                            frame = await websocket.receive()
                            if (frame.type == aiohttp.WSMsgType.ERROR
                                    and isinstance(frame.data, aiohttp.WebSocketError)
                                    and frame.data.code == aiohttp.WSCloseCode.MESSAGE_TOO_BIG):
                                raise InspectionError("response_bytes")
                            if frame.type != aiohttp.WSMsgType.TEXT:
                                raise InspectionError("malformed_response")
                            size = len(frame.data.encode("utf-8"))
                            consumed += size
                            if size > (min(AUTH_BYTES, frame_limit) if auth else frame_limit) or consumed > remaining_bytes:
                                raise InspectionError("response_bytes")
                            try:
                                data = json.loads(frame.data, object_pairs_hook=pairs, parse_constant=finite, parse_float=float_value)
                            except (ValueError, TypeError, RecursionError):
                                raise InspectionError("malformed_response") from None
                            if type(data) is not dict:
                                raise InspectionError("malformed_response")
                            return data

                        greeting = await receive(auth=True)
                        if greeting.get("type") != "auth_required":
                            raise InspectionError("malformed_response")
                        await websocket.send_json({"type": "auth", "access_token": self.settings.ha_token})
                        auth = await receive(auth=True)
                        if auth.get("type") != "auth_ok":
                            raise InspectionError("access_denied", authority_lost=True)
                        if auth.get("ha_version") != core_version:
                            raise InspectionError("identity_drift")
                        authorized()
                        await websocket.send_json({"id": 1, **payload})
                        message = await receive()
                        if (message.get("type") != "result" or type(message.get("id")) is not int
                                or message["id"] != 1 or type(message.get("success")) is not bool):
                            raise InspectionError("malformed_response")
                        if message["success"] is False:
                            error = message.get("error")
                            code = error.get("code") if type(error) is dict else None
                            reason = ("access_denied" if code in ("unauthorized", "forbidden") else
                                      "command_unsupported" if code == "unknown_command" else
                                      "integration_not_installed" if code == "not_found" else "source_unavailable")
                            raise InspectionError(reason)
                        if "result" not in message or "error" in message:
                            raise InspectionError("malformed_response")
                        authorized()
                        return message["result"], consumed
        except InspectionError:
            raise
        except (asyncio.TimeoutError, TimeoutError):
            timed_out = True
            raise InspectionError("timeout") from None
        except aiohttp.WSServerHandshakeError as exc:
            raise InspectionError("access_denied" if exc.status in (401, 403) else "source_unavailable",
                                  authority_lost=exc.status in (401, 403)) from None
        except (aiohttp.ClientError, OSError, ValueError, TypeError, RecursionError):
            raise InspectionError("source_unavailable") from None
        finally:
            METRICS.inspection_response_bytes += consumed
            METRICS.inspection_timeout_count += int(timed_out)
            self._record(started, "alarmo_inspection_" + kind.value, timeout=timed_out)

    async def command(self, payload: dict) -> Any:
        category = self._category(payload)
        telemetry = current_telemetry()
        if telemetry is not None and not telemetry.authorize_core_dispatch():
            telemetry.error_code = ErrorCode.PROVIDER_UNAVAILABLE.value
            raise HomeAssistantUnavailableError(
                details=self._error_details(category)
            )
        started = time.perf_counter()
        if telemetry:
            telemetry.begin_ha_attempt(started)
        timeout = aiohttp.ClientTimeout(total=self.settings.ha_timeout_seconds)
        websocket_timeout = aiohttp.ClientWSTimeout(
            ws_receive=self.settings.ha_timeout_seconds,
            ws_close=self.settings.ha_timeout_seconds,
        )
        recorded = False

        def record(*, timeout=False):
            nonlocal recorded
            if not recorded:
                recorded = True
                self._record(started, category, timeout=timeout)

        try:
            async with aiohttp.ClientSession(timeout=timeout, **session_options()) as session:
                async with session.ws_connect(
                    self.settings.websocket_url,
                    timeout=websocket_timeout,
                    **({"max_msg_size": MAX_READ_BYTES} if SINGLE_READ.get() else {}),
                ) as websocket:
                    message = await websocket.receive_json()
                    if message.get("type") != "auth_required":
                        record()
                        self._set_error(ErrorCode.HA_API_ERROR)
                        raise HomeAssistantApiError(
                            details=self._error_details(category)
                        )
                    await websocket.send_json(
                        {"type": "auth", "access_token": self.settings.ha_token}
                    )
                    message = await websocket.receive_json()
                    if message.get("type") != "auth_ok":
                        record()
                        self._set_error(ErrorCode.AUTHORIZATION_FAILURE)
                        raise AuthorizationError(
                            details=self._error_details(category)
                        )
                    await websocket.send_json({"id": 1, **payload})
                    while True:
                        message = await websocket.receive_json()
                        if message.get("id") != 1 or message.get("type") != "result":
                            continue
                        record()
                        if message.get("success"):
                            return message.get("result")
                        error = message.get("error") or {}
                        error_code = str(error.get("code", "")).lower()
                        if error_code in {"unauthorized", "forbidden"}:
                            self._set_error(ErrorCode.AUTHORIZATION_FAILURE)
                            raise AuthorizationError(
                                details=self._error_details(
                                    category,
                                    provider_response_received=True,
                                )
                            )
                        status = 404 if error_code in {"404", "not_found"} else None
                        self._set_error(ErrorCode.HA_API_ERROR)
                        raise HomeAssistantApiError(
                            details=self._error_details(
                                category,
                                status=status,
                                provider_response_received=True,
                            )
                        )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            record(timeout=True)
            self._set_error(ErrorCode.HA_TIMEOUT)
            raise HomeAssistantTimeoutError(
                details=self._error_details(category)
            ) from exc
        except aiohttp.WSServerHandshakeError as exc:
            record()
            status = int(exc.status)
            if status in {401, 403}:
                self._set_error(ErrorCode.AUTHORIZATION_FAILURE)
                raise AuthorizationError(
                    details=self._error_details(category, status=status)
                ) from exc
            self._set_error(ErrorCode.HA_API_ERROR)
            raise HomeAssistantApiError(
                details=self._error_details(category, status=status)
            ) from exc
        except (aiohttp.ClientConnectionError, OSError) as exc:
            record()
            self._set_error(ErrorCode.HA_UNAVAILABLE)
            raise HomeAssistantUnavailableError(
                details=self._error_details(category)
            ) from exc
        finally:
            # Scoped transport refusals/cancellation may exit before a response.
            # Complete attempt timing once without altering normal call behavior.
            if SINGLE_READ.get():
                record()

    async def command_batch(self, payloads: list[dict]) -> list[tuple[bool, Any]]:
        """Pipeline fixed read-only commands over one authenticated connection.

        Returns one ``(success, result_or_error_code)`` pair per payload, in
        order. Per-command errors stay per command; transport, authentication,
        timeout or protocol failures raise and invalidate the whole batch.
        """

        category = "websocket_batch"
        payloads = list(payloads)
        if len(payloads) > MAX_BATCH_COMMANDS or not all(
            batch_command_allowed(payload) for payload in payloads
        ):
            raise HomeAssistantApiError(
                details={**self._error_details(category), "reason": "command_not_allowed"}
            )
        if not payloads:
            return []
        telemetry = current_telemetry()

        def authorize() -> None:
            if telemetry is not None and not telemetry.authorize_core_dispatch():
                telemetry.error_code = ErrorCode.PROVIDER_UNAVAILABLE.value
                raise HomeAssistantUnavailableError(
                    details=self._error_details(category)
                )

        authorize()
        started = time.perf_counter()
        if telemetry:
            telemetry.begin_ha_attempt(started)
        timeout = aiohttp.ClientTimeout(total=self.settings.ha_timeout_seconds)
        websocket_timeout = aiohttp.ClientWSTimeout(
            ws_receive=self.settings.ha_timeout_seconds,
            ws_close=self.settings.ha_timeout_seconds,
        )
        results: list[tuple[bool, Any] | None] = [None] * len(payloads)

        def malformed() -> HomeAssistantApiError:
            self._set_error(ErrorCode.HA_API_ERROR)
            return HomeAssistantApiError(
                details={
                    **self._error_details(category, provider_response_received=True),
                    "reason": "malformed_response",
                }
            )

        timed_out = False
        try:
            async with aiohttp.ClientSession(timeout=timeout, **session_options()) as session:
                async with session.ws_connect(
                    self.settings.websocket_url, timeout=websocket_timeout
                ) as websocket:
                    message = await websocket.receive_json()
                    if message.get("type") != "auth_required":
                        self._set_error(ErrorCode.HA_API_ERROR)
                        raise HomeAssistantApiError(details=self._error_details(category))
                    await websocket.send_json(
                        {"type": "auth", "access_token": self.settings.ha_token}
                    )
                    message = await websocket.receive_json()
                    if message.get("type") != "auth_ok":
                        self._set_error(ErrorCode.AUTHORIZATION_FAILURE)
                        raise AuthorizationError(details=self._error_details(category))
                    sent = 0
                    received = 0
                    while received < len(payloads):
                        while sent < len(payloads) and sent - received < BATCH_WINDOW:
                            # Authority may be withdrawn mid-batch; never send past it.
                            authorize()
                            await websocket.send_json({"id": sent + 1, **payloads[sent]})
                            sent += 1
                        message = await websocket.receive_json()
                        if type(message) is not dict:
                            raise malformed()
                        if message.get("type") != "result":
                            continue
                        message_id = message.get("id")
                        if (
                            type(message_id) is not int
                            or not 1 <= message_id <= sent
                            or results[message_id - 1] is not None
                        ):
                            raise malformed()
                        received += 1
                        if message.get("success") is True:
                            results[message_id - 1] = (True, message.get("result"))
                            continue
                        error = message.get("error")
                        error_code = (
                            str(error.get("code", "")).lower()[:64]
                            if isinstance(error, dict) else ""
                        )
                        if error_code in {"unauthorized", "forbidden"}:
                            self._set_error(ErrorCode.AUTHORIZATION_FAILURE)
                            raise AuthorizationError(
                                details=self._error_details(
                                    category, provider_response_received=True
                                )
                            )
                        results[message_id - 1] = (False, error_code or "error")
        except (asyncio.TimeoutError, TimeoutError) as exc:
            timed_out = True
            self._set_error(ErrorCode.HA_TIMEOUT)
            raise HomeAssistantTimeoutError(
                details=self._error_details(category)
            ) from exc
        except aiohttp.WSServerHandshakeError as exc:
            status = int(exc.status)
            if status in {401, 403}:
                self._set_error(ErrorCode.AUTHORIZATION_FAILURE)
                raise AuthorizationError(
                    details=self._error_details(category, status=status)
                ) from exc
            self._set_error(ErrorCode.HA_API_ERROR)
            raise HomeAssistantApiError(
                details=self._error_details(category, status=status)
            ) from exc
        except (aiohttp.ClientConnectionError, OSError) as exc:
            self._set_error(ErrorCode.HA_UNAVAILABLE)
            raise HomeAssistantUnavailableError(
                details=self._error_details(category)
            ) from exc
        except (TypeError, ValueError) as exc:
            raise malformed() from exc
        finally:
            self._record(started, category, timeout=timed_out)
        return [item for item in results if item is not None]
