"""Observable Home Assistant REST transport with safe error mapping."""

import asyncio
from dataclasses import dataclass
import json
import time
from typing import Any

import aiohttp

from ..configuration import Settings
from ..errors import (
    AutomationNotFoundError,
    EntityNotFoundError,
    ErrorCode,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ..observability import METRICS
from ..request_context import current_telemetry
from .single_read import SINGLE_READ, session_options, response_text
from . import logbook


@dataclass(frozen=True)
class ExpectedHttpStatus:
    """Internal marker for an explicitly expected upstream branch."""

    status: int


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
        finished = time.perf_counter()
        duration = round((finished - started) * 1000, 3)
        METRICS.record_ha(duration, timeout=timeout)
        telemetry = current_telemetry()
        if telemetry:
            telemetry.ha_duration_ms += duration
            telemetry.finish_ha_attempt(finished)
            telemetry.timeout_occurred = telemetry.timeout_occurred or timeout
            telemetry.endpoint_categories.add(category)
        return duration

    async def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        raw: bool = False,
        expected_statuses: frozenset[int] = frozenset(),
        *,
        logbook_read: bool = False,
    ) -> Any:
        if logbook_read and (method != "GET" or not path.startswith("/logbook/")
                             or body is not None or not raw or expected_statuses):
            raise ValueError("invalid_internal_logbook_request")
        headers = {
            "Authorization": f"Bearer {self.settings.ha_token}",
            "Content-Type": "application/json",
        }
        category = endpoint_category(path)
        telemetry = current_telemetry()
        if telemetry is not None and not telemetry.authorize_core_dispatch():
            telemetry.error_code = ErrorCode.PROVIDER_UNAVAILABLE.value
            raise HomeAssistantUnavailableError(
                details={
                    "method": method,
                    "endpoint_category": category,
                }
            )
        started = time.perf_counter()
        if telemetry:
            telemetry.begin_ha_attempt(started)
        timeout = aiohttp.ClientTimeout(total=(
            min(self.settings.ha_timeout_seconds, logbook.MAX_SECONDS)
            if logbook_read else self.settings.ha_timeout_seconds
        ))
        recorded = False

        def record(*, timeout=False):
            nonlocal recorded
            if not recorded:
                recorded = True
                self._record(started, category, timeout=timeout)

        try:
            options = ({"middlewares": (logbook.no_retry,), "auto_decompress": False,
                        "headers": {"Accept-Encoding": "identity"}}
                       if logbook_read else session_options())
            async with aiohttp.ClientSession(timeout=timeout, **options) as session:
                async with session.request(
                    method, f"{self.settings.api_url}{path}", headers=headers, json=body
                ) as response:
                    text = await logbook.read_body(response) if logbook_read else await response_text(response)
                    record()
                    if response.status in expected_statuses:
                        return ExpectedHttpStatus(response.status)
                    if response.status >= 400:
                        details = {
                            "status": response.status,
                            "method": method,
                            "endpoint_category": category,
                            "provider_response_received": True,
                        }
                        if response.status == 404 and path.startswith("/states/"):
                            error = EntityNotFoundError(details=details)
                        elif response.status == 404 and "/automation/" in path:
                            error = AutomationNotFoundError(details=details)
                        else:
                            error = HomeAssistantApiError(details=details)
                        telemetry = current_telemetry()
                        if telemetry:
                            telemetry.error_code = error.code.value
                        raise error
                    if raw:
                        return text
                    try:
                        return json.loads(text) if text else None
                    except json.JSONDecodeError:
                        return text
        except (asyncio.TimeoutError, TimeoutError) as exc:
            record(timeout=True)
            raise HomeAssistantTimeoutError(
                details={"method": method, "endpoint_category": category}
            ) from exc
        except (aiohttp.ClientConnectionError, OSError) as exc:
            record()
            raise HomeAssistantUnavailableError(
                details={"method": method, "endpoint_category": category}
            ) from exc
        except aiohttp.ClientPayloadError:
            if not logbook_read:
                raise
            raise logbook.invalid_response() from None
        finally:
            # Scoped transport refusals/cancellation may exit before a response.
            # Complete attempt timing once without altering normal call behavior.
            if SINGLE_READ.get() or logbook_read:
                record()
