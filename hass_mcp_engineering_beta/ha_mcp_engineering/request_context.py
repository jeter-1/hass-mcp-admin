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
    retry_count: int = 0
    timeout_occurred: bool = False
    response_status: int | None = None
    endpoint_categories: set[str] = field(default_factory=set)
    error_code: str | None = None
    caller_id: str = "anonymous"

    @property
    def total_duration_ms(self) -> float:
        return round((time.perf_counter() - self.started) * 1000, 3)


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
