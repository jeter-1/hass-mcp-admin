"""Fixed read contract for HAMCP-131 continuable entity history.

History is read natively from Home Assistant Core (HAMCP-511):
``native.NativeHistoryReader`` sends one fixed
``GET /api/history/period`` request per slice window under the tool's own
Core lease. The delegated ``ha_get_history`` route is not used and is not a
fallback. Engineering builds every request argument: one entity, absolute
UTC bounds, minimal response, no attributes and significant changes only.

Core behavior relied on (pinned Home Assistant Core 2026.11.3, see
``native.py``): the recorder returns the state prevailing before the
requested start (reported at the start) followed by the rows with
``start < last_updated < end``. Minimal responses collapse consecutive
equal states except for ``native.NEED_ATTRIBUTE_DOMAINS``, whose
attribute-only updates read as repeated states. Offsets are stable only
over fixed absolute bounds.

Every value here is a disclosed bound.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any

from ..errors import ErrorCode, GovernanceError
from ..incident_logs.contracts import MAX_FUTURE_SKEW_SECONDS, format_ms, parse_timestamp_ms


SCHEMA = "history-slices-v1"
# The Core read the slices use (HAMCP-511), named as the provider's
# upstream tool in responses and audit.
UPSTREAM_TOOL = "core_history_period"
# The reviewed native reader representation; a reader reporting any other
# version is refused before a Core request.
NATIVE_VERSION = "core-rest-history-period-v1"
REVIEWED_VERSIONS = frozenset({NATIVE_VERSION})
# The closed failure reasons a reader may return for one slice read.
READ_FAILURES = frozenset({
    "representation_invalid", "provider_authority_changed", "core_read_timeout",
    "core_read_failed", "core_read_unavailable", "sanitization_failed",
})

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
# 0 to the cursor, one read per slice (review M1; one Core request returns a
# whole slice window). A page whose next continuation would need more than
# MAX_VERIFY_CALLS verification reads issues no cursor (omission
# continuation_verification_limit), so every issued cursor is verifiable
# within the bound. The at most 28 slices of a 7-day span need at most 28.
MAX_VERIFY_CALLS = 40
# Entries before a slice start: the recorder's start state plus rows inside
# the 1 ms overlap. More is not the pinned shape; the bound keeps every
# verification range within one bounded request.
MAX_ENTRIES_BEFORE_SLICE = 100
MAX_CURSOR_CHARS = 4096
MAX_STATE_CHARS = 255
RESPONSE_BUDGET = 44_000
CURSOR_TTL_SECONDS = 3600
# Read admission (HAMCP-527): per caller, one read runs and at most three
# wait; a fifth is refused at once. Globally two reads run, first come first
# served. A read that has not started within READ_WAIT_SECONDS of arrival is
# refused as busy (retryable); the bound stays under the 60 s per-request
# timeout of common MCP clients.
READS_PER_CALLER = 1
QUEUED_READS_PER_CALLER = 3
READS_GLOBAL = 2
READ_WAIT_SECONDS = 10
READ_BUSY_SCOPES = frozenset({"caller", "global"})

_ENTITY_ID = re.compile(r"[a-z0-9_]{1,64}\.[a-z0-9_]{1,190}\Z")
ALLOWED_ARGUMENTS = frozenset({"entity_id", "start", "end", "limit", "cursor"})
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class HistorySlicesError(GovernanceError):
    """Only a closed reason reaches error and audit serialization."""

    REASONS = frozenset({
        "invalid_arguments", "history_route_unavailable", "history_unavailable",
        "read_in_progress",
    })

    def __init__(self, reason: str, *, scope: str = "global"):
        if reason not in self.REASONS:
            reason = "history_unavailable"
        code = {
            "invalid_arguments": ErrorCode.INVALID_REQUEST,
            "history_route_unavailable": ErrorCode.PROVIDER_UNAVAILABLE,
        }.get(reason, ErrorCode.ANALYSIS_UNAVAILABLE)
        details: dict[str, Any] = {"reason": reason, "fallback_occurred": False}
        # HAMCP-413 (HAAC-E046): a concurrent read is a retryable busy
        # refusal, not an unavailable provider. HAMCP-513/527: the shared
        # read_busy (409) code, naming the exhausted scope (this caller's
        # read slots or the global ones).
        if reason == "read_in_progress":
            code = ErrorCode.READ_BUSY
            message = "Another history read is in progress; retry after it completes."
            details["scope"] = scope if scope in READ_BUSY_SCOPES else "global"
        else:
            message = None
        super().__init__(code, message, details=details)
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
