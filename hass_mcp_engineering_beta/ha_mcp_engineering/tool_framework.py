"""Structured response and failure handling for incrementally migrated tools."""

from __future__ import annotations

import inspect
import time
from typing import Any, Awaitable, Callable

from .errors import map_exception
from .models import FailureResponse, SuccessResponse, Timing
from .observability import METRICS
from .request_context import current_request_id, current_telemetry


def timing_since(started: float) -> Timing:
    telemetry = current_telemetry()
    tool_ms = round((time.perf_counter() - started) * 1000, 3)
    return Timing(
        total_ms=telemetry.total_duration_ms if telemetry else tool_ms,
        tool_ms=tool_ms,
        home_assistant_ms=round(telemetry.ha_duration_ms, 3) if telemetry else 0.0,
        retry_count=telemetry.retry_count if telemetry else 0,
        timeout_occurred=telemetry.timeout_occurred if telemetry else False,
    )


async def run_structured(
    operation: str,
    summary: str,
    action: Callable[[], Any | Awaitable[Any]],
    *,
    warnings: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    response_limit: int = 60_000,
) -> str:
    started = time.perf_counter()
    try:
        result = action()
        if inspect.isawaitable(result):
            result = await result
        return SuccessResponse(
            operation=operation,
            summary=summary,
            data=result,
            warnings=warnings or [],
            metadata=metadata or {},
            timing=timing_since(started),
            request_id=current_request_id(),
        ).to_json(response_limit)
    except Exception as exc:
        code, message, retryable, details = map_exception(exc)
        METRICS.record_error(code.value)
        telemetry = current_telemetry()
        if telemetry:
            telemetry.error_code = code.value
        return FailureResponse(
            operation=operation,
            error=type(exc).__name__,
            error_code=code.value,
            message=message,
            details=details,
            retryable=retryable,
            warnings=warnings or [],
            metadata=metadata or {},
            timing=timing_since(started),
            request_id=current_request_id(),
        ).to_json(response_limit)
