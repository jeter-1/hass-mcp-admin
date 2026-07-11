"""Observable Home Assistant REST transport with safe error mapping."""

import asyncio
import json
import time
from typing import Any

import aiohttp

from ..configuration import Settings
from ..errors import (
    ErrorCode,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ..observability import METRICS
from ..request_context import current_telemetry


def endpoint_category(path: str) -> str:
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return "root"
    if segments[0] == "config" and len(segments) > 1:
        return f"config/{segments[1]}"
    return segments[0]


class HomeAssistantRestClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _record(self, started: float, category: str, *, timeout: bool = False) -> float:
        duration = round((time.perf_counter() - started) * 1000, 3)
        METRICS.record_ha(duration, timeout=timeout)
        telemetry = current_telemetry()
        if telemetry:
            telemetry.ha_duration_ms += duration
            telemetry.timeout_occurred = telemetry.timeout_occurred or timeout
            telemetry.endpoint_categories.add(category)
        return duration

    async def request(self, method: str, path: str, body: Any = None, raw: bool = False) -> Any:
        headers = {
            "Authorization": f"Bearer {self.settings.ha_token}",
            "Content-Type": "application/json",
        }
        category = endpoint_category(path)
        started = time.perf_counter()
        timeout = aiohttp.ClientTimeout(total=self.settings.ha_timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method, f"{self.settings.api_url}{path}", headers=headers, json=body
                ) as response:
                    text = await response.text()
                    self._record(started, category)
                    if response.status >= 400:
                        raise HomeAssistantApiError(
                            details={
                                "status": response.status,
                                "method": method,
                                "endpoint_category": category,
                            }
                        )
                    if raw:
                        return text
                    try:
                        return json.loads(text) if text else None
                    except json.JSONDecodeError:
                        return text
        except (asyncio.TimeoutError, TimeoutError) as exc:
            self._record(started, category, timeout=True)
            METRICS.record_error(ErrorCode.HA_TIMEOUT.value)
            raise HomeAssistantTimeoutError(
                details={"method": method, "endpoint_category": category}
            ) from exc
        except (aiohttp.ClientConnectionError, OSError) as exc:
            self._record(started, category)
            METRICS.record_error(ErrorCode.HA_UNAVAILABLE.value)
            raise HomeAssistantUnavailableError(
                details={"method": method, "endpoint_category": category}
            ) from exc
