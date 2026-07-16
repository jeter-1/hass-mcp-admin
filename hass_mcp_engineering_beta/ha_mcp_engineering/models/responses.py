"""Typed, JSON-serializable response contracts for migrated beta tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Any

from ..request_context import current_request_id

MAX_CHARS = 60_000


@dataclass
class Timing:
    total_ms: float
    tool_ms: float | None = None
    home_assistant_ms: float | None = None
    home_assistant_cumulative_attempt_ms: float | None = None
    home_assistant_wall_clock_span_ms: float | None = None
    home_assistant_request_count: int = 0
    upstream_attempted: bool = False
    upstream_ms: float = 0.0
    upstream_wall_clock_span_ms: float = 0.0
    upstream_request_count: int = 0
    provider_operations_concurrent: bool = False
    retry_count: int = 0
    timeout_occurred: bool = False


@dataclass
class SuccessResponse:
    operation: str
    summary: str
    data: Any = None
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    timing: Timing | dict[str, Any] = field(default_factory=lambda: Timing(total_ms=0.0))
    request_id: str = field(default_factory=current_request_id)
    success: bool = field(default=True, init=False)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, limit: int = MAX_CHARS) -> str:
        return dump_json(self.as_dict(), limit=limit)


@dataclass
class FailureResponse:
    operation: str
    error: str
    error_code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    timing: Timing | dict[str, Any] = field(default_factory=lambda: Timing(total_ms=0.0))
    request_id: str = field(default_factory=current_request_id)
    success: bool = field(default=False, init=False)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, limit: int = MAX_CHARS) -> str:
        return dump_json(self.as_dict(), limit=limit)


# Compatibility alias retained for code that imported the scaffold model.
EngineeringResponse = SuccessResponse


def dump_json(data: Any, limit: int = MAX_CHARS) -> str:
    output = json.dumps(data, indent=2, default=str)
    if len(output) > limit:
        return output[:limit] + f"\n... [truncated at {limit} chars]"
    return output
