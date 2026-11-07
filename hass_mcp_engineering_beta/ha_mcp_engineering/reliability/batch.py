"""HAMCP-229: bounded multi-automation reliability analysis.

Every item runs through the unchanged single-automation evidence path
(``AutomationReliabilityAnalysisService.analyze``) against the same reviewed
reliability provider. This module adds no Home Assistant read, command,
service call or write: it only schedules, bounds, and summarizes repeated
single-item analyses. There is no provider selection and no fallback.

The bounds below are implementation defaults pending Josh (design note Q4).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import time
from typing import Any, Callable

from ..errors import AutomationNotFoundError, ErrorCode, GovernanceError, InvalidRequestError
from ..clients.paths import INVALID_IDENTIFIER_MESSAGE
from .models import ReliabilityAnalysisOutput
from .service import AUTOMATION_ID, AutomationReliabilityAnalysisService, _required_clock_instant
from .timestamps import normalize_timestamp


OPERATION = "automation_reliability_batch_analysis"
POLICY_ID = "bounded_automation_set_reliability_read"
MAX_BATCH_ITEMS = 20
BATCH_SECONDS = 45.0
START_RESERVE_SECONDS = 5.0
ITEM_CONCURRENCY = 2
MAX_BATCH_TRACE_LIMIT = 10
DEFAULT_BATCH_TRACE_LIMIT = 5
MAX_BATCH_LOOKBACK_HOURS = 720
DEFAULT_BATCH_LOOKBACK_HOURS = 168
MAX_ROOT_CAUSE_SUMMARIES = 3
MAX_BATCH_RESPONSE_CHARS = 60_000
# Characters kept back from the response bound for everything that is not an
# analyzed digest: the request echo, coverage counts, metadata, warnings,
# timing and the response wrapper. Per-item rows and ID lists are charged
# separately (``_fixed_item_cost``) so every requested ID always fits.
ENVELOPE_RESERVE_CHARS = 10_000
# The single-item findings bound; digests summarize every finding, so the
# per-item page never paginates for batches of ordinary automations.
_ITEM_FINDINGS_LIMIT = 100

FAILURE_REASONS = (
    "automation_not_found",
    "provider_timeout",
    "analysis_unavailable",
    "time_budget_exceeded",
    "internal_error",
)
SKIP_REASONS = ("time_budget", "output_budget")


def _compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def validate_batch_request(automation_ids, lookback_hours, trace_limit) -> tuple[list[str], list[str]]:
    """``(unique ids in request order, duplicates removed)`` or refuse whole.

    Runs before any Home Assistant read. Exact duplicates collapse to their
    first occurrence and are reported, not refused.
    """

    def refuse(reason: str, **extra) -> InvalidRequestError:
        return InvalidRequestError(
            INVALID_IDENTIFIER_MESSAGE if reason == "invalid_identifier" else None,
            details={"operation": OPERATION, "reason": reason, **extra},
        )

    if not isinstance(automation_ids, (list, tuple)):
        raise refuse("invalid_automation_ids", field="automation_ids")
    if not automation_ids:
        raise refuse("empty_automation_ids", field="automation_ids")
    if len(automation_ids) > MAX_BATCH_ITEMS:
        raise refuse(
            "too_many_automation_ids", field="automation_ids",
            maximum=MAX_BATCH_ITEMS, received=len(automation_ids),
        )
    invalid = [
        index for index, value in enumerate(automation_ids)
        if not isinstance(value, str) or not AUTOMATION_ID.fullmatch(value.strip())
    ]
    if invalid:
        raise refuse("invalid_identifier", field="automation_ids", invalid_indexes=invalid)
    for field, value, maximum in (
        ("lookback_hours", lookback_hours, MAX_BATCH_LOOKBACK_HOURS),
        ("trace_limit", trace_limit, MAX_BATCH_TRACE_LIMIT),
    ):
        if type(value) is not int or not 1 <= value <= maximum:
            raise refuse("out_of_range", field=field, minimum=1, maximum=maximum)
    unique: list[str] = []
    duplicates: list[str] = []
    for value in automation_ids:
        normalized = value.strip()
        if normalized in unique:
            if normalized not in duplicates:
                duplicates.append(normalized)
        else:
            unique.append(normalized)
    return unique, duplicates


@dataclass
class _ItemOutcome:
    automation_id: str
    status: str
    reason: str | None = None
    error_code: str | None = None
    digest: dict | None = None
    result_status: str | None = None


def _digest(automation_id: str, output: ReliabilityAnalysisOutput) -> dict:
    data = output.data
    lifecycle = data.get("lifecycle_analysis") or {}
    return {
        "automation_id": automation_id,
        "status": "analyzed",
        "reason": None,
        "target": data.get("target"),
        "overall_assessment": data.get("overall_assessment"),
        "result_status": data.get("result_status"),
        "finding_counts_by_severity": data.get("finding_counts_by_severity"),
        "unique_root_cause_count": data.get("unique_root_cause_count"),
        "root_cause_summaries": [
            {
                key: group.get(key)
                for key in (
                    "root_cause_group_id", "title", "affected_dependency",
                    "severity", "reference_count",
                )
            }
            for group in (data.get("root_cause_groups") or [])[:MAX_ROOT_CAUSE_SUMMARIES]
        ],
        "lifecycle": {
            "hazards_detected": lifecycle.get("hazards_detected"),
            "coverage_complete": lifecycle.get("coverage_complete"),
        },
        "source_completeness": {
            item.get("source_type"): item.get("completeness")
            for item in data.get("evidence_source_coverage") or []
        },
        "configuration_fingerprint": data.get("configuration_fingerprint"),
        "evidence_fingerprint": data.get("evidence_fingerprint"),
    }


def _row(outcome: _ItemOutcome) -> dict:
    if outcome.digest is not None:
        return outcome.digest
    row = {"automation_id": outcome.automation_id, "status": outcome.status, "reason": outcome.reason}
    if outcome.error_code:
        row["error_code"] = outcome.error_code
    return row


def _fixed_item_cost(automation_id: str) -> int:
    """Characters always reserved for one item, whatever its outcome.

    Covers the longest non-digest row plus the ID's appearances in
    ``remaining_automation_ids`` and ``request.duplicates_removed``.
    """

    longest = {
        "automation_id": automation_id, "status": "skipped_budget",
        "reason": "time_budget_exceeded", "error_code": "internal_server_error",
    }
    return len(_compact(longest)) + 2 * (len(_compact(automation_id)) + 1) + 16


def _failure_reason(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, AutomationNotFoundError):
        return "automation_not_found", ErrorCode.AUTOMATION_NOT_FOUND.value
    if isinstance(exc, GovernanceError):
        if exc.code == ErrorCode.PROVIDER_TIMEOUT:
            return "provider_timeout", exc.code.value
        if exc.code == ErrorCode.ANALYSIS_UNAVAILABLE:
            return "analysis_unavailable", exc.code.value
    return "internal_error", ErrorCode.INTERNAL_SERVER_ERROR.value


class ReliabilityBatchAnalyzer:
    """One process-wide bounded batch at a time over the single-item service."""

    def __init__(
        self,
        *,
        max_items: int = MAX_BATCH_ITEMS,
        batch_seconds: float = BATCH_SECONDS,
        start_reserve_seconds: float = START_RESERVE_SECONDS,
        concurrency: int = ITEM_CONCURRENCY,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.max_items = max_items
        self.batch_seconds = float(batch_seconds)
        self.start_reserve_seconds = float(start_reserve_seconds)
        self.concurrency = max(1, int(concurrency))
        self.monotonic = monotonic
        self._in_flight = False

    @property
    def in_flight(self) -> bool:
        return self._in_flight

    async def analyze(
        self,
        service: AutomationReliabilityAnalysisService,
        *,
        automation_ids,
        lookback_hours: int = DEFAULT_BATCH_LOOKBACK_HOURS,
        trace_limit: int = DEFAULT_BATCH_TRACE_LIMIT,
        response_limit: int = MAX_BATCH_RESPONSE_CHARS,
    ) -> ReliabilityAnalysisOutput:
        unique, duplicates = validate_batch_request(automation_ids, lookback_hours, trace_limit)
        if self._in_flight:
            raise GovernanceError(
                ErrorCode.RATE_LIMIT_EXCEEDED,
                "Another automation reliability batch is in progress; retry after it completes.",
                details={"operation": OPERATION, "reason": "batch_in_progress"},
            )
        # Check-and-set with no await between them: one event loop serves the
        # tools, so a concurrent caller observes the flag before any read.
        self._in_flight = True
        try:
            return await self._run(
                service, unique=unique, duplicates=duplicates,
                requested=len(automation_ids), lookback_hours=lookback_hours,
                trace_limit=trace_limit, response_limit=response_limit,
            )
        finally:
            self._in_flight = False

    async def _run(
        self, service, *, unique, duplicates, requested, lookback_hours,
        trace_limit, response_limit,
    ) -> ReliabilityAnalysisOutput:
        started = time.perf_counter()
        try:
            instant = _required_clock_instant(service.clock)
            analysis_timestamp = normalize_timestamp(instant)
            if analysis_timestamp is None:
                raise ValueError("analysis timestamp normalization failed")
        except Exception as exc:
            raise GovernanceError(ErrorCode.INTERNAL_SERVER_ERROR) from exc
        # The same reviewed provider and the unchanged single-item analysis,
        # pinned to one analysis instant so every item shares one window.
        item_service = AutomationReliabilityAnalysisService(
            service.provider, timeout_seconds=service.timeout_seconds,
            clock=lambda: instant,
        )
        deadline = self.monotonic() + self.batch_seconds
        outcomes: list[_ItemOutcome | None] = [None] * len(unique)
        next_index = 0

        async def run_item(automation_id: str) -> _ItemOutcome:
            remaining = deadline - self.monotonic()
            if remaining < self.start_reserve_seconds:
                return _ItemOutcome(automation_id, "skipped_budget", "time_budget")
            try:
                output = await asyncio.wait_for(
                    item_service.analyze(
                        automation_id=automation_id,
                        lookback_hours=lookback_hours,
                        trace_limit=trace_limit,
                        detail_level="summary",
                        limit=_ITEM_FINDINGS_LIMIT,
                    ),
                    timeout=remaining,
                )
            except (asyncio.TimeoutError, TimeoutError):
                # The single-item service converts its own provider timeout to
                # PROVIDER_TIMEOUT, so a bare timeout here is the batch budget.
                return _ItemOutcome(
                    automation_id, "failed", "time_budget_exceeded",
                    ErrorCode.PROVIDER_TIMEOUT.value,
                )
            except Exception as exc:  # per-item isolation; never aborts others
                reason, code = _failure_reason(exc)
                return _ItemOutcome(automation_id, "failed", reason, code)
            digest = _digest(automation_id, output)
            return _ItemOutcome(
                automation_id, "analyzed", None, None, digest, digest["result_status"],
            )

        async def worker() -> None:
            nonlocal next_index
            while next_index < len(unique):
                index = next_index
                next_index += 1
                outcomes[index] = await run_item(unique[index])

        await asyncio.gather(*(worker() for _ in range(min(self.concurrency, len(unique)))))
        final = [item for item in outcomes if item is not None]
        self._apply_output_budget(final, response_limit)
        return self._assemble(
            final, unique=unique, duplicates=duplicates, requested=requested,
            lookback_hours=lookback_hours, trace_limit=trace_limit,
            analysis_timestamp=analysis_timestamp, started=started,
            response_limit=response_limit,
        )

    @staticmethod
    def _apply_output_budget(outcomes: list[_ItemOutcome], response_limit: int) -> None:
        available = int(response_limit) - ENVELOPE_RESERVE_CHARS - sum(
            _fixed_item_cost(item.automation_id) for item in outcomes
        )
        overflowed = False
        for item in outcomes:
            if item.digest is None:
                continue
            cost = len(_compact(item.digest))
            if overflowed or cost > available:
                overflowed = True
                item.status, item.reason = "skipped_budget", "output_budget"
                item.digest, item.result_status = None, None
                continue
            available -= cost

    def _assemble(
        self, outcomes, *, unique, duplicates, requested, lookback_hours,
        trace_limit, analysis_timestamp, started, response_limit,
    ) -> ReliabilityAnalysisOutput:
        analyzed = [item for item in outcomes if item.status == "analyzed"]
        complete = [item for item in analyzed if item.result_status == "success"]
        failed = [item for item in outcomes if item.status == "failed"]
        skipped = [item for item in outcomes if item.status == "skipped_budget"]
        remaining = [
            item.automation_id for item in outcomes
            if item.status == "skipped_budget"
            or (item.status == "failed" and item.reason == "time_budget_exceeded")
        ]
        if analyzed and len(complete) == len(outcomes):
            batch_status = "success"
        elif not analyzed:
            batch_status = "failed"
        else:
            batch_status = "partial"
        warnings: list[str] = []
        if duplicates:
            warnings.append("Duplicate automation IDs were collapsed to their first occurrence.")
        if remaining:
            warnings.append(
                "Some automations were not analyzed within the batch bounds; resubmit "
                "remaining_automation_ids to cover them."
            )
        if failed:
            warnings.append(
                "Some automations failed analysis; each failure is isolated to its item."
            )
        if len(analyzed) != len(complete):
            warnings.append(
                "Some analyses have partial evidence; use automation_reliability_analysis "
                "for that automation's findings and coverage."
            )
        data = {
            "analysis_timestamp": analysis_timestamp,
            "request": {
                "requested_count": requested,
                "automation_ids": list(unique),
                "duplicates_removed": list(duplicates),
                "lookback_hours": lookback_hours,
                "trace_limit": trace_limit,
                "selector": "explicit_automation_ids",
            },
            "bounds": {
                "max_items": self.max_items,
                "batch_seconds": self.batch_seconds,
                "start_reserve_seconds": self.start_reserve_seconds,
                "item_concurrency": self.concurrency,
                "max_trace_limit": MAX_BATCH_TRACE_LIMIT,
                "response_limit_chars": int(response_limit),
            },
            "batch_result_status": batch_status,
            "coverage": {
                "requested": requested,
                "deduplicated": len(unique),
                "analyzed_complete": len(complete),
                "analyzed_partial": len(analyzed) - len(complete),
                "failed": len(failed),
                "skipped_budget": len(skipped),
                "remaining_automation_ids": remaining,
            },
            "items": [_row(item) for item in outcomes],
            "drill_down": "automation_reliability_analysis",
            "analysis_duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        metadata = {
            "routing": {
                "lifecycle_status": "beta_native",
                "classification": "engineering_native",
                "provider": "engineering",
                "fallback_occurred": False,
                "direct_access_policy": {
                    "policy_id": POLICY_ID,
                    "access": "read",
                    "scope": (
                        f"at most {self.max_items} automations, each under "
                        "single_automation_reliability_read bounds"
                    ),
                },
            },
        }
        return ReliabilityAnalysisOutput(
            data=data, warnings=warnings, metadata=metadata,
            partial=batch_status != "success",
        )


RELIABILITY_BATCH = ReliabilityBatchAnalyzer()
