"""Fixed read contract for HAMCP-131 continuable entity history.

History is read only through the admitted, gateway-sanitized delegated
``ha_get_history`` route (never the native ``get_history`` REST tool).
Engineering builds every upstream argument: one entity, absolute UTC
bounds, ascending order, minimal response and a bounded offset/limit.

Upstream behavior relied on (see ``docs/HAMCP_131_INCIDENT_LOGS.md``):

- ha-mcp 8.5.0/8.6.0 ``_fetch_history`` (identical AST) sends one
  ``history/history_during_period`` call for the whole requested window,
  then slices ``[offset:offset+limit]`` client-side and reports
  ``total_count``; offsets are stable only over fixed absolute bounds.
- Home Assistant 2026.10.1 recorder history returns the state prevailing
  before ``start_time`` (``last_updated < start``, only when the database
  holds a state that old) followed by rows with
  ``start < last_updated < end``, collapsing consecutive equal states.

Every value here is a disclosed bound.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any

from ..errors import ErrorCode, GovernanceError
from ..incident_logs.contracts import MAX_FUTURE_SKEW_SECONDS, format_ms, parse_timestamp_ms


SCHEMA = "history-slices-v1"
UPSTREAM_TOOL = "ha_get_history"
# Versions whose history response shape was reviewed from source.
REVIEWED_VERSIONS = frozenset({"8.5.0", "8.6.0"})

MAX_SPAN_SECONDS = 7 * 24 * 3600
SLICE_SECONDS = 6 * 3600
# Each slice is requested from 1 ms before its start so a state recorded
# exactly at a slice boundary is inside one request (Core excludes a row
# whose last_updated equals the requested start from both the start state
# and the changes).
OVERLAP_US = 1000
DEFAULT_LIMIT = 50
MAX_LIMIT = 100
MAX_UPSTREAM_CALLS = 12
# A continued page re-reads ALL history the read has returned so far: every
# finished slice from offset 0 to its total and the current slice from offset
# 0 to the cursor, in chunks of VERIFY_CHUNK entries (review M1). A page whose
# next continuation would need more than MAX_VERIFY_CALLS verification reads
# issues no cursor (omission continuation_verification_limit), so every issued
# cursor is verifiable within the bound. 40 calls cover one call for each of
# the at most 28 slices plus 12 further chunks.
VERIFY_CHUNK = 100
MAX_VERIFY_CALLS = 40
# Entries before a slice start: the recorder's start state plus rows inside
# the 1 ms overlap. More is not the pinned shape; the bound keeps every
# verification range within one bounded request.
MAX_ENTRIES_BEFORE_SLICE = 100
MAX_CURSOR_CHARS = 4096
MAX_STATE_CHARS = 255
RESPONSE_BUDGET = 44_000
CURSOR_TTL_SECONDS = 3600

_ENTITY_ID = re.compile(r"[a-z0-9_]{1,64}\.[a-z0-9_]{1,190}\Z")
ALLOWED_ARGUMENTS = frozenset({"entity_id", "start", "end", "limit", "cursor"})
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class HistorySlicesError(GovernanceError):
    """Only a closed reason reaches error and audit serialization."""

    REASONS = frozenset({
        "invalid_arguments", "history_route_unavailable", "history_unavailable",
        "read_in_progress",
    })

    def __init__(self, reason: str):
        if reason not in self.REASONS:
            reason = "history_unavailable"
        code = {
            "invalid_arguments": ErrorCode.INVALID_REQUEST,
            "history_route_unavailable": ErrorCode.PROVIDER_UNAVAILABLE,
            "read_in_progress": ErrorCode.PROVIDER_UNAVAILABLE,
        }.get(reason, ErrorCode.ANALYSIS_UNAVAILABLE)
        super().__init__(code, details={"reason": reason, "fallback_occurred": False})
        self.reason = reason


def cursor_error(stale: bool, reason: str) -> GovernanceError:
    return GovernanceError(
        ErrorCode.STALE_CURSOR if stale else ErrorCode.INVALID_CURSOR,
        details={"feature": "history_slices", "reason": reason},
    )


def valid_entity_id(value: Any) -> bool:
    return type(value) is str and _ENTITY_ID.fullmatch(value) is not None


def check_span(start_us: int, end_us: int) -> None:
    if not 0 < end_us - start_us <= MAX_SPAN_SECONDS * 1_000_000:
        raise ValueError("Invalid history slice arguments.")


def validate_arguments(arguments: Any, *, now_ms: int | None = None) -> dict[str, Any]:
    """Validate raw caller arguments before the SDK can coerce them."""

    try:
        if not isinstance(arguments, dict) or not set(arguments) <= ALLOWED_ARGUMENTS:
            raise ValueError
        entity_id = arguments.get("entity_id")
        if not valid_entity_id(entity_id):
            raise ValueError
        if "start" not in arguments:
            raise ValueError
        start_us = parse_timestamp_ms(arguments["start"]) * 1000
        if now_ms is None:
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        end = arguments.get("end")
        end_us = now_ms * 1000 if end is None else parse_timestamp_ms(end) * 1000
        if not (end is None and arguments.get("cursor") is not None):
            check_span(start_us, end_us)
        if end_us > (now_ms + MAX_FUTURE_SKEW_SECONDS * 1000) * 1000:
            raise ValueError
        limit = arguments.get("limit", DEFAULT_LIMIT)
        if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
            raise ValueError
        cursor = arguments.get("cursor")
        if cursor is not None and (type(cursor) is not str or not 1 <= len(cursor) <= MAX_CURSOR_CHARS
                                   or not cursor.isascii()):
            raise ValueError
    except ValueError:
        raise ValueError("Invalid history slice arguments.") from None
    return {"entity_id": entity_id, "start_us": start_us, "end_us": end_us,
            "end_defaulted": end is None, "limit": limit, "cursor": cursor}


def slice_bounds(start_us: int, end_us: int, index: int) -> tuple[int, int]:
    lower = start_us + index * SLICE_SECONDS * 1_000_000
    return lower, min(end_us, lower + SLICE_SECONDS * 1_000_000)


def slice_count(start_us: int, end_us: int) -> int:
    span = SLICE_SECONDS * 1_000_000
    return -(-(end_us - start_us) // span)


def format_us(value: int) -> str:
    """Exact UTC ISO 8601 with microseconds (the upstream request form)."""

    moment = _EPOCH + timedelta(microseconds=value)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f") + "+00:00"


def format_public(value: int) -> str:
    return format_ms(value // 1000) if value % 1000 == 0 else format_us(value)


def parse_upstream_us(value: Any) -> int | None:
    """Exact microseconds for an offset-aware upstream ISO timestamp."""

    if type(value) is not str or len(value) > 40:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None
    return (moment - _EPOCH) // timedelta(microseconds=1)
