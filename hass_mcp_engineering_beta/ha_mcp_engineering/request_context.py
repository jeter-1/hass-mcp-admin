"""Per-request correlation and safe telemetry context."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
import re
import time
import uuid

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


@dataclass
class RequestTelemetry:
    request_id: str
    started: float = field(default_factory=time.perf_counter)
    tool_name: str | None = None
    tool_started: float | None = None
    tool_duration_ms: float | None = None
    ha_duration_ms: float = 0.0
    ha_request_count: int = 0
    ha_active_requests: int = 0
    ha_max_concurrent_requests: int = 0
    ha_span_started: float | None = None
    ha_span_finished: float | None = None
    retry_count: int = 0
    timeout_occurred: bool = False
    response_status: int | None = None
    endpoint_categories: set[str] = field(default_factory=set)
    error_code: str | None = None
    result_status: str | None = None
    completeness: str | None = None
    caller_id: str = "anonymous"
    audit_context: dict[str, object] = field(default_factory=dict)

    @property
    def total_duration_ms(self) -> float:
        return round((time.perf_counter() - self.started) * 1000, 3)

    @property
    def ha_wall_clock_span_ms(self) -> float:
        if self.ha_span_started is None or self.ha_span_finished is None:
            return 0.0
        return round(max(0.0, self.ha_span_finished - self.ha_span_started) * 1000, 3)

    def begin_ha_attempt(self, started: float) -> None:
        self.ha_request_count += 1
        self.ha_active_requests += 1
        self.ha_max_concurrent_requests = max(self.ha_max_concurrent_requests, self.ha_active_requests)
        self.ha_span_started = started if self.ha_span_started is None else min(self.ha_span_started, started)

    def finish_ha_attempt(self, finished: float) -> None:
        self.ha_active_requests = max(0, self.ha_active_requests - 1)
        self.ha_span_finished = finished if self.ha_span_finished is None else max(self.ha_span_finished, finished)


_REQUEST: ContextVar[RequestTelemetry | None] = ContextVar("engineering_request", default=None)


def normalize_request_id(candidate: str | None) -> str:
    if candidate and REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex


def begin_request(candidate: str | None = None) -> tuple[RequestTelemetry, Token]:
    telemetry = RequestTelemetry(request_id=normalize_request_id(candidate))
    return telemetry, _REQUEST.set(telemetry)


def end_request(token: Token) -> None:
    _REQUEST.reset(token)


def current_telemetry() -> RequestTelemetry | None:
    return _REQUEST.get()


def current_request_id() -> str:
    telemetry = current_telemetry()
    return telemetry.request_id if telemetry else normalize_request_id(None)


def current_caller_id() -> str:
    telemetry = current_telemetry()
    return telemetry.caller_id if telemetry else "anonymous"
