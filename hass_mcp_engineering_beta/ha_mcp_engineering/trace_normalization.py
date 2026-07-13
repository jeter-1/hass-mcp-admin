"""Shared, sanitized Home Assistant automation trace-list normalization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable

from .reliability.timestamps import normalize_timestamp, parse_timestamp
from .sanitization import sanitize_untrusted_data


MAX_TRACE_HEADERS = 100


@dataclass(frozen=True)
class NormalizedAutomationTraceHeader:
    run_id: str
    started_at: str
    started_instant: datetime
    finished_at: str | None = None
    state: str | None = None
    script_execution: str | None = None
    last_step: str | None = None
    error: Any = None

    def public(self) -> dict[str, Any]:
        """Return the stable legacy-compatible trace-list shape."""

        return {
            "run_id": self.run_id,
            "timestamp": self.started_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "state": self.state,
            "script_execution": self.script_execution,
            "last_step": self.last_step,
            "error": self.error,
        }


@dataclass(frozen=True)
class NormalizedTraceList:
    headers: tuple[NormalizedAutomationTraceHeader, ...]
    upstream_runs_returned: int
    runs_considered: int
    runs_parsed_successfully: int
    malformed_entries: int
    missing_start_entries: int
    malformed_start_entries: int
    malformed_finish_entries: int
    duplicate_run_ids: int
    source_truncated: bool
    sanitization_failed_closed: bool
    warnings: tuple[str, ...] = ()


async def fetch_normalized_trace_list(
    command: Callable[[dict[str, Any]], Awaitable[Any]],
    automation_id: str,
    *,
    known_secrets: tuple[str, ...] = (),
) -> NormalizedTraceList:
    """Use the one trace/list transport payload and normalization pipeline."""

    result = await command(
        {"type": "trace/list", "domain": "automation", "item_id": automation_id}
    )
    if not isinstance(result, list):
        raise TypeError("trace list response is invalid")
    sanitation = sanitize_untrusted_data(
        result, known_secrets=known_secrets, max_string=2_000
    )
    if not isinstance(sanitation.value, list):
        raise TypeError("sanitized trace list response is invalid")
    return normalize_trace_list(
        sanitation.value,
        sanitization_failed_closed=sanitation.failed_closed,
    )


def normalize_trace_list(
    safe_result: list[Any], *, sanitization_failed_closed: bool = False
) -> NormalizedTraceList:
    """Normalize bounded headers without inventing missing source timestamps."""

    upstream_count = len(safe_result)
    bounded = safe_result[:MAX_TRACE_HEADERS]
    malformed = missing_start = malformed_start = malformed_finish = duplicates = 0
    by_run_id: dict[str, NormalizedAutomationTraceHeader] = {}
    warnings: list[str] = []

    for item in bounded:
        if not isinstance(item, dict):
            malformed += 1
            continue
        run_id = str(item.get("run_id") or "").strip()[:128]
        if not run_id:
            malformed += 1
            continue

        start_value, finish_value = _timestamp_candidates(item)
        if start_value in (None, ""):
            missing_start += 1
            malformed += 1
            continue
        started = parse_timestamp(start_value)
        if started is None:
            malformed_start += 1
            malformed += 1
            continue
        finished_at = None
        if finish_value not in (None, ""):
            finished_at = normalize_timestamp(finish_value)
            if finished_at is None:
                malformed_finish += 1
                malformed += 1

        header = NormalizedAutomationTraceHeader(
            run_id=run_id,
            started_at=normalize_timestamp(started) or "",
            started_instant=started,
            finished_at=finished_at,
            state=_bounded_text(item.get("state"), 64),
            script_execution=_bounded_text(item.get("script_execution"), 128),
            last_step=_bounded_text(item.get("last_step"), 160),
            error=item.get("error"),
        )
        existing = by_run_id.get(run_id)
        if existing is not None:
            duplicates += 1
            if (header.started_instant, header.run_id) <= (
                existing.started_instant,
                existing.run_id,
            ):
                continue
        by_run_id[run_id] = header

    source_truncated = upstream_count > MAX_TRACE_HEADERS
    if source_truncated:
        warnings.append(
            f"Trace-list normalization was capped at {MAX_TRACE_HEADERS} upstream entries."
        )
    if malformed:
        warnings.append(
            f"{malformed} trace header field or entry failed bounded timestamp normalization."
        )
    if duplicates:
        warnings.append(f"{duplicates} duplicate trace run ID(s) were deduplicated.")
    if sanitization_failed_closed:
        warnings.append("One or more trace-list fields failed closed during sanitization.")

    headers = tuple(
        sorted(
            by_run_id.values(),
            key=lambda item: (item.started_instant, item.run_id),
            reverse=True,
        )
    )
    return NormalizedTraceList(
        headers=headers,
        upstream_runs_returned=upstream_count,
        runs_considered=len(bounded),
        runs_parsed_successfully=len(headers),
        malformed_entries=malformed,
        missing_start_entries=missing_start,
        malformed_start_entries=malformed_start,
        malformed_finish_entries=malformed_finish,
        duplicate_run_ids=duplicates,
        source_truncated=source_truncated,
        sanitization_failed_closed=sanitization_failed_closed,
        warnings=tuple(warnings[:10]),
    )


def _timestamp_candidates(item: dict[str, Any]) -> tuple[Any, Any]:
    timestamp = item.get("timestamp")
    if isinstance(timestamp, dict):
        start = _first_present(
            timestamp.get("started_at"), timestamp.get("start"), timestamp.get("started")
        )
        finish = _first_present(
            timestamp.get("finished_at"), timestamp.get("finish"), timestamp.get("finished")
        )
    else:
        start = _first_present(timestamp, item.get("started_at"), item.get("start"))
        finish = None
    finish = _first_present(
        finish, item.get("finished_at"), item.get("finish"), item.get("last_action")
    )
    return start, finish


def _first_present(*values: Any) -> Any:
    return next((value for value in values if value not in (None, "")), None)


def _bounded_text(value: Any, limit: int) -> str | None:
    if value in (None, ""):
        return None
    return str(value)[:limit]
