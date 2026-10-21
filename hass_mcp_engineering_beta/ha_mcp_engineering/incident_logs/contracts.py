"""Fixed read contract for the HAMCP-131 incident-window log read.

Sources (verified against the pinned Supervisor 2026.09.1, commit
40e3ee7640a3c44abe67f1a1397f39c3cd949806, and systemd-journal-gatewayd
v257.9; see ``docs/HAMCP_131_INCIDENT_LOGS.md``):

- ``/core/logs``: ``APIHost.advanced_logs_handler`` for the ``homeassistant``
  identifier. Role ``homeassistant`` admits ``^/core/.+``.
- ``/addons/self/logs``: the same handler for the calling add-on's
  identifiers, admitted for every add-on by ``api_bypass``
  ``^/addons/self/(?!security|update)[^/]+$``.
- ``/supervisor/logs``, ``/host/logs`` and ``/addons/<slug>/logs`` need role
  ``manager``. Engineering runs as ``homeassistant`` and this read adds no
  permission, so they are reported unavailable with that exact reason and
  no request is sent.

Every value here is a disclosed bound.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from types import MappingProxyType
from typing import Any

from ..clients.paths import UnsafeRequestPathError, path_segment
from ..errors import ErrorCode, GovernanceError


SCHEMA = "incident-logs-v1"
SUPERVISOR_PIN = "2026.09.1 (40e3ee7640a3c44abe67f1a1397f39c3cd949806)"
GATEWAYD_PIN = "systemd v257.9 journal-gatewayd"

MAX_SOURCES = 4
MAX_WINDOW_SECONDS = 6 * 3600
MAX_FUTURE_SKEW_SECONDS = 120
DEFAULT_LIMIT = 100
MAX_LIMIT = 200
MAX_TEXT_FILTER = 128
MAX_CURSOR_CHARS = 4096
MAX_SLUG = 64

# Journal paging.
PAGE_ENTRIES = 100
MIN_PAGE_ENTRIES = 4
PROBE_DISTANCES = (256, 1024, 4096, 16384, 65536, 262144)
BISECTION_STEPS = 8
MAX_REQUESTS_PER_SOURCE = 16
MAX_REQUESTS_PER_CALL = 24
MAX_DOWNLOAD_BYTES = 256 * 1024
MAX_CALL_DOWNLOAD_BYTES = 1024 * 1024
REQUEST_DEADLINE_SECONDS = 10.0
CALL_DEADLINE_SECONDS = 25.0
CHUNK_BYTES = 8 * 1024
# With a text or level filter, fetching stops once this many in-window
# entries are eligible (the filter itself only runs at the single exit).
MAX_FILTER_SCAN_ENTRIES = 2000

# Output.
MAX_MESSAGE_CHARS = 2000
RESPONSE_BUDGET = 44_000

LEVELS = ("debug", "info", "warning", "error", "critical")
LEVEL_RANK = MappingProxyType({level: index for index, level in enumerate(LEVELS)})

SOURCE_CORE = "core"
SOURCE_SELF = "addon:self"
# Readable source -> fixed route template. Nothing else is ever requested.
READABLE_ROUTES = MappingProxyType({
    SOURCE_CORE: "/core/logs",
    SOURCE_SELF: "/addons/self/logs",
})
# Pinned Supervisor role patterns that refuse role ``homeassistant``.
MANAGER_ROUTE_PATTERNS = MappingProxyType({
    "supervisor": r"^/supervisor/.+$",
    "host": r"^/host/.+$",
    "addon": r"^/addons/[^/]+/(?!security).+$",
})
AUTHORITY_NOT_GRANTED = "authority_not_granted"
AUTHORITY_DETAIL = (
    "requires Supervisor role manager; Engineering runs with hassio_role "
    "homeassistant and this read adds no permission"
)

_SOURCE_TOKEN = re.compile(r"(?:core|supervisor|host)\Z")
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
# ISO 8601 with an explicit offset and at most millisecond precision: journal
# timestamps carry milliseconds, so a finer bound would be ambiguous.
_TIMESTAMP = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?(Z|[+-]\d{2}:\d{2})\Z"
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")

ALLOWED_ARGUMENTS = frozenset({"sources", "start", "end", "min_level", "text", "limit", "cursor"})


class IncidentLogsError(GovernanceError):
    """Only a closed reason reaches error and audit serialization."""

    REASONS = frozenset({
        "authority_unavailable", "sources_unavailable", "invalid_arguments",
        "read_in_progress",
    })

    def __init__(self, reason: str):
        if reason not in self.REASONS:
            reason = "sources_unavailable"
        code = {
            "authority_unavailable": ErrorCode.PROVIDER_UNAVAILABLE,
            "read_in_progress": ErrorCode.PROVIDER_UNAVAILABLE,
            "invalid_arguments": ErrorCode.INVALID_REQUEST,
        }.get(reason, ErrorCode.ANALYSIS_UNAVAILABLE)
        super().__init__(code, details={"reason": reason, "fallback_occurred": False})
        self.reason = reason


def cursor_error(stale: bool, reason: str) -> GovernanceError:
    return GovernanceError(
        ErrorCode.STALE_CURSOR if stale else ErrorCode.INVALID_CURSOR,
        details={"feature": "incident_logs", "reason": reason},
    )


def parse_timestamp_ms(value: Any) -> int:
    """Return UTC epoch milliseconds for a strict ISO 8601 timestamp."""

    if type(value) is not str or len(value) > 40:
        raise ValueError("Invalid incident log arguments.")
    match = _TIMESTAMP.fullmatch(value)
    if not match:
        raise ValueError("Invalid incident log arguments.")
    year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
    fraction = int((match.group(7) or "").ljust(3, "0"))
    zone = match.group(8)
    try:
        offset = timezone.utc if zone == "Z" else timezone(
            (1 if zone[0] == "+" else -1)
            * timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6]))
        )
        parsed = datetime(year, month, day, hour, minute, second, tzinfo=offset)
    except ValueError:
        raise ValueError("Invalid incident log arguments.") from None
    return int(parsed.timestamp()) * 1000 + fraction


def format_ms(value: int) -> str:
    moment = datetime.fromtimestamp(value / 1000, timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value % 1000:03d}Z"


def parse_source(token: Any) -> tuple[str, str | None]:
    """Return ``(source_key, slug)``; a slug must be one safe path segment."""

    if type(token) is not str or not token or len(token) > 6 + MAX_SLUG:
        raise ValueError("Invalid incident log arguments.")
    if _SOURCE_TOKEN.fullmatch(token):
        return token, None
    if not token.startswith("addon:"):
        raise ValueError("Invalid incident log arguments.")
    slug = token[len("addon:"):]
    try:
        # HAMCP-200: the same single-segment guard every route value uses,
        # before any request, session or credential exists.
        path_segment(slug, field="sources")
    except UnsafeRequestPathError:
        raise ValueError("Invalid incident log arguments.") from None
    if slug != "self" and not _SLUG.fullmatch(slug):
        raise ValueError("Invalid incident log arguments.")
    return f"addon:{slug}", slug


def validate_arguments(arguments: Any, *, now_ms: int | None = None) -> dict[str, Any]:
    """Validate raw caller arguments before the SDK can coerce them."""

    if not isinstance(arguments, dict) or not set(arguments) <= ALLOWED_ARGUMENTS:
        raise ValueError("Invalid incident log arguments.")
    sources = arguments.get("sources")
    if type(sources) is not list or not 1 <= len(sources) <= MAX_SOURCES:
        raise ValueError("Invalid incident log arguments.")
    keys = [parse_source(token)[0] for token in sources]
    if len(set(keys)) != len(keys):
        raise ValueError("Invalid incident log arguments.")
    if "start" not in arguments:
        raise ValueError("Invalid incident log arguments.")
    start_ms = parse_timestamp_ms(arguments["start"])
    end = arguments.get("end")
    if now_ms is None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    end_ms = now_ms if end is None else parse_timestamp_ms(end)
    # A continuation with a defaulted end uses the end its cursor fixed; the
    # service checks that window after the cursor is verified.
    if not (end is None and arguments.get("cursor") is not None):
        check_window(start_ms, end_ms)
    if end_ms > now_ms + MAX_FUTURE_SKEW_SECONDS * 1000:
        raise ValueError("Invalid incident log arguments.")
    min_level = arguments.get("min_level")
    if min_level is not None and min_level not in LEVEL_RANK:
        raise ValueError("Invalid incident log arguments.")
    text = arguments.get("text")
    if text is not None and (
        type(text) is not str or not text.strip() or len(text) > MAX_TEXT_FILTER
        or _CONTROL.search(text)
    ):
        raise ValueError("Invalid incident log arguments.")
    limit = arguments.get("limit", DEFAULT_LIMIT)
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError("Invalid incident log arguments.")
    cursor = arguments.get("cursor")
    if cursor is not None and (
        type(cursor) is not str or not cursor or len(cursor) > MAX_CURSOR_CHARS
    ):
        raise ValueError("Invalid incident log arguments.")
    return {
        "sources": tuple(keys), "start_ms": start_ms, "end_ms": end_ms,
        "end_defaulted": end is None, "min_level": min_level, "text": text,
        "limit": limit, "cursor": cursor,
    }


def check_window(start_ms: int, end_ms: int) -> None:
    if end_ms <= start_ms or end_ms - start_ms > MAX_WINDOW_SECONDS * 1000:
        raise ValueError("Invalid incident log arguments.")


def unavailable_reason(source_key: str) -> tuple[str, str] | None:
    """The static authority refusal for a source, or ``None`` if readable."""

    if source_key in READABLE_ROUTES:
        return None
    family = "addon" if source_key.startswith("addon:") else source_key
    return AUTHORITY_NOT_GRANTED, MANAGER_ROUTE_PATTERNS[family]
