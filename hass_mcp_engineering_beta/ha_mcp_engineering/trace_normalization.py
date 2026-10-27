"""Shared, sanitized Home Assistant automation trace-list normalization."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Awaitable, Callable

from .configuration_definitions import credentials
from .reliability.timestamps import normalize_timestamp, parse_timestamp
from .sanitization import (
    ReferenceMarkerRegistry,
    SanitizationResult,
    redaction_summary,
    sanitize_untrusted_data,
)


MAX_TRACE_HEADERS = 100
TRACE_STRING_LIMIT = 2_000
# HAMCP-225 review 1 H1: deeper nesting than this is not screened; the
# subtree is withheld whole and the result fails closed.
TRACE_SCREEN_DEPTH = 64
# Home Assistant's own identifiers and names (owner decision, HAMCP-225
# correction batch 2, consistent with HAMCP-226): a string directly under one
# of these keys, or in a list directly under one, is screened in the shared
# screen's default (shape) mode, so ``rotate_api_token`` or ``Token refresh``
# is shown while a credential URL or a known secret is still withheld. Every
# other string, and every key, keeps the labelled forms.
IDENTITY_KEYS = frozenset({
    "automation_id", "script_id", "item_id", "run_id", "id", "alias", "entity_id",
    "friendly_name",
})
SCREEN_CATEGORIES = {
    credentials.WITHHELD_MARKER: "credential_text",
    credentials.UNVERIFIABLE_MARKER: "unverifiable_text",
}


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
    not_triggered: bool = False

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
        result, known_secrets=known_secrets, max_string=TRACE_STRING_LIMIT
    )
    if not isinstance(sanitation.value, list):
        raise TypeError("sanitized trace list response is invalid")
    return normalize_trace_list(
        sanitation.value,
        sanitization_failed_closed=sanitation.failed_closed,
    )


def sanitize_trace_detail(
    detail: Any,
    *,
    known_secrets: tuple[str, ...] = (),
    registry: ReferenceMarkerRegistry | None = None,
) -> SanitizationResult:
    """The one HAMCP-225 exit for a Core ``trace/get`` result (O2).

    Every automation trace read that leaves Engineering or reaches the
    snapshot store passes here once; nothing is added to the value after it.

    Review 1 H1 (architect direction): first the shared credential screen
    HAMCP-132 and HAMCP-224 use (``credentials.screen`` with its labelled
    forms), on every complete original string, dictionary keys included, at
    every depth. A string it withholds is replaced whole by its marker; a
    key it withholds drops its entry, key and value. Only then the strict
    sanitizer runs, and truncation comes last, after the text was cleared.
    There is no trace-specific rule. Home Assistant's own identifiers and
    names (``IDENTITY_KEYS``) use the screen's default mode instead, at
    every pass, so capture, the stored record and every response agree.
    """

    screened, withheld, categories, failed = screen_trace_value(detail, known_secrets)
    result = sanitize_untrusted_data(
        screened,
        known_secrets=known_secrets,
        max_string=TRACE_STRING_LIMIT,
        registry=registry,
    )
    if not withheld and not failed:
        return result
    return replace(
        result,
        redacted_field_count=result.redacted_field_count + withheld,
        redaction_categories=tuple(sorted({*result.redaction_categories, *categories})),
        failed_closed=result.failed_closed or failed,
    )


def screen_trace_value(
    value: Any, known_secrets: tuple[str, ...] = ()
) -> tuple[Any, int, set[str], bool]:
    """``(screened copy, strings and entries withheld, categories, failed closed)``.

    The shared screen alone, without the strict sanitizer: every string and
    every dictionary key, compared whole and as originally given. A string
    under an ``IDENTITY_KEYS`` key (or in a list directly under one) is an
    identifier or name and is read in the default mode; the rest labelled.
    """

    withheld = 0
    categories: set[str] = set()
    failed = False
    decided: dict[tuple[str, bool], str | None] = {}

    def marker(text: str, labelled: bool = True) -> str | None:
        if (text, labelled) not in decided:
            decided[(text, labelled)] = credentials.screen(text, known_secrets, labelled=labelled)
        return decided[(text, labelled)]

    def walk(item: Any, depth: int, identity: bool = False) -> Any:
        nonlocal withheld, failed
        if depth > TRACE_SCREEN_DEPTH and type(item) in (dict, list, tuple):
            withheld += 1
            failed = True
            categories.add(SCREEN_CATEGORIES[credentials.UNVERIFIABLE_MARKER])
            return credentials.UNVERIFIABLE_MARKER
        if type(item) is str:
            found = marker(item, not identity)
            if found is None:
                return item
            withheld += 1
            categories.add(SCREEN_CATEGORIES[found])
            return found
        if type(item) is dict:
            kept = {}
            for key, child in item.items():
                found = marker(key if type(key) is str else str(key))
                if found is not None:
                    withheld += 1
                    categories.add(SCREEN_CATEGORIES[found])
                    continue
                kept[key] = walk(child, depth + 1, type(key) is str and key in IDENTITY_KEYS)
            return kept
        if type(item) in (list, tuple):
            return [walk(child, depth + 1, identity and type(child) is str) for child in item]
        return item

    return walk(value, 0), withheld, categories, failed


def trace_sanitization_partial(result: SanitizationResult) -> bool:
    """Withheld, shortened, colliding or failed content is never complete."""

    return bool(
        result.redaction_applied
        or result.truncated_field_count
        or result.failed_closed
        or result.marker_collision_count
    )


def trace_sanitization_evidence(result: SanitizationResult) -> dict[str, Any]:
    """Bounded counts and category names only, never values."""

    summary = redaction_summary(result) or {
        "redacted_field_count": 0,
        "categories": [],
        "category_count": 0,
        "marker_collision_count": result.marker_collision_count,
    }
    return {
        **summary,
        "truncated_field_count": result.truncated_field_count,
        "failed_closed": result.failed_closed,
    }


def trace_sanitization_warning(result: SanitizationResult) -> str:
    """One fixed warning naming the sanitizer's categories, never values."""

    categories = sorted(result.redaction_categories)
    reasons = []
    if categories:
        reasons.append("withheld " + ", ".join(categories))
    if result.truncated_field_count:
        reasons.append(f"shortened {result.truncated_field_count} string(s) to {TRACE_STRING_LIMIT} characters")
    if result.failed_closed:
        reasons.append("failed closed on one or more fields")
    return "Trace content is partial: Engineering " + "; ".join(reasons or ["withheld content"]) + "."


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
            not_triggered=item.get("not_triggered") is True,
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
