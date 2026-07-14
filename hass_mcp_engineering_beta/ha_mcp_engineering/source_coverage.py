"""Shared source-coverage semantics for Engineering analysis services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


ACTUAL_FAILURE_CATEGORIES = frozenset(
    {
        "provider_upstream_error",
        "provider_timeout",
        "item_read_failure",
        "authentication_failure",
        "response_validation_failure",
        "provider_invalid_response",
    }
)


@dataclass(frozen=True)
class NormalizedCoverage:
    """Normalized coverage state without conflating limits and failures."""

    completeness: str
    assessment_complete: bool
    failed_items: int
    failure_category: str | None
    coverage_limitations: tuple[str, ...]
    actual_failure: bool


def assessment_complete(*, completeness: str, required: bool) -> bool:
    """Return whether the requested assessment has sufficient source coverage."""

    return not required or completeness in {"complete", "not_requested"}


def normalize_coverage(
    *,
    source_type: str,
    completeness: str,
    requested: bool,
    required: bool,
    items_examined: int = 0,
    failed_items: int = 0,
    failure_category: str | None = None,
    unsupported: bool = False,
    retention_limited: bool = False,
    limitation_ids: Iterable[str] = (),
) -> NormalizedCoverage:
    """Normalize one bounded coverage record.

    Unsupported, truncated, retained-window, and otherwise incomplete successful
    evidence are limitations. Failure categories are reserved for attempted work
    that actually failed.
    """

    source = str(source_type).strip().lower()[:64] or "unknown_source"
    status = str(completeness).strip().lower()
    examined = max(0, int(items_examined))
    failed = max(0, int(failed_items))
    category = str(failure_category).strip() if failure_category else None
    category = category if category in ACTUAL_FAILURE_CATEGORIES else None

    if not requested:
        return NormalizedCoverage("not_requested", True, 0, None, (), False)

    actual_failure = bool(category or failed)
    limitations = [str(item)[:128] for item in limitation_ids if str(item).strip()]

    if unsupported and not actual_failure:
        status = "not_supported"
        limitations.append(f"{source}_unsupported")
    elif actual_failure:
        if category is None:
            category = "item_read_failure" if examined else "provider_upstream_error"
        status = "partial" if examined or status == "partial" else "failed"
        if status == "partial":
            limitations.append(f"{source}_partial_item_failure")
    elif status in {"unsupported", "unavailable", "not_supported"}:
        status = "not_supported"
        limitations.append(f"{source}_unsupported")
    elif status == "partial":
        if retention_limited:
            limitations.append(f"{source}_retention_limited")
        elif not limitations:
            limitations.append(f"{source}_partial_coverage")
        category = None
        failed = 0
    else:
        status = "complete"
        category = None
        failed = 0

    bounded_limitations = tuple(sorted(dict.fromkeys(limitations)))[:10]
    return NormalizedCoverage(
        completeness=status,
        assessment_complete=assessment_complete(completeness=status, required=required),
        failed_items=failed,
        failure_category=category,
        coverage_limitations=bounded_limitations,
        actual_failure=actual_failure,
    )
