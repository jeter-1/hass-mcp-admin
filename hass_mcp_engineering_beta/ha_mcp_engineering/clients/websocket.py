"""Observable Home Assistant WebSocket transport with safe error mapping."""

import asyncio
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
