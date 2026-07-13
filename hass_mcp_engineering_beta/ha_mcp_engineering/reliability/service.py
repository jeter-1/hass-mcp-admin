"""Facilitator service for bounded single-automation reliability analysis."""

from __future__ import annotations

import asyncio
import base64
from collections import Counter, OrderedDict
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import time
import uuid

from ..errors import AutomationNotFoundError, ErrorCode, GovernanceError, InvalidRequestError
from ..facilitation import DetailLevel
from ..observability import METRICS
from ..providers import EvidenceRequest, ProviderCapability, ProviderCompleteness, ProviderFailureCategory
from ..request_context import current_telemetry
from .models import ReliabilityAnalysisOutput, ReliabilityEvidenceBundle
from .rules import build_root_cause_groups, evaluate_rules
from .timestamps import normalize_timestamp, parse_timestamp


AUTOMATION_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
MAX_FINDINGS = 100
PAGINATION_SNAPSHOT_TTL_SECONDS = 300.0
MAX_PAGINATION_SNAPSHOTS = 16


@dataclass
class _PaginationSnapshot:
    expires_at: float
    automation_id: str
    lookback_hours: int
    trace_limit: int
    detail_level: str
    fingerprint: str
    analysis_timestamp: str
    data_base: dict
    findings: tuple[dict, ...]
    root_cause_groups: tuple[dict, ...]
    evidence_by_id: dict[str, dict]
    warnings: tuple[str, ...]
    metadata: dict
    source_partial: bool


class _PaginationSnapshotStore:
    def __init__(self):
        self._values: OrderedDict[str, _PaginationSnapshot] = OrderedDict()

    def put(self, value: _PaginationSnapshot) -> str:
        self._purge()
        snapshot_id = uuid.uuid4().hex
        self._values[snapshot_id] = value
        while len(self._values) > MAX_PAGINATION_SNAPSHOTS:
            self._values.popitem(last=False)
        return snapshot_id

    def get(self, snapshot_id: str) -> _PaginationSnapshot | None:
        self._purge()
        value = self._values.get(snapshot_id)
        if value is not None:
            self._values.move_to_end(snapshot_id)
        return value

    def remove(self, snapshot_id: str) -> None:
        self._values.pop(snapshot_id, None)

    def _purge(self) -> None:
        now = time.monotonic()
        for key in [key for key, value in self._values.items() if value.expires_at <= now]:
            self._values.pop(key, None)


class AutomationReliabilityAnalysisService:
    def __init__(self, provider, *, timeout_seconds: float = 60.0, clock=None):
        self.provider = provider
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 120.0))
        self.clock = clock or _utc_now
        self.pagination_snapshots = _PaginationSnapshotStore()

    async def analyze(
        self,
        *,
        automation_id: str,
        lookback_hours: int = 168,
        trace_limit: int = 10,
        detail_level: str = "standard",
        limit: int = 20,
        cursor: str = "",
    ) -> ReliabilityAnalysisOutput:
        started = time.perf_counter()
        METRICS.record_reliability_analysis_request()
        normalized_id = str(automation_id).strip()
        if not AUTOMATION_ID.fullmatch(normalized_id) or "." in normalized_id and normalized_id.startswith("automation."):
            METRICS.record_reliability_analysis_failure("request_validation")
            raise InvalidRequestError(details={"operation": "automation_reliability_analysis"})
        if detail_level not in {item.value for item in DetailLevel}:
            METRICS.record_reliability_analysis_failure("request_validation")
            raise InvalidRequestError(details={"operation": "automation_reliability_analysis"})
        if not 1 <= int(lookback_hours) <= 720 or not 1 <= int(trace_limit) <= 50 or not 1 <= int(limit) <= MAX_FINDINGS:
            METRICS.record_reliability_analysis_failure("request_validation")
            raise InvalidRequestError(details={"operation": "automation_reliability_analysis"})

        try:
            analysis_instant = (
                _cursor_analysis_instant(cursor) if cursor else _required_clock_instant(self.clock)
            )
            analysis_timestamp = normalize_timestamp(analysis_instant)
            if analysis_timestamp is None:
                raise ValueError("analysis timestamp normalization failed")
        except GovernanceError:
            METRICS.record_reliability_analysis_failure("invalid_cursor")
            raise
        except Exception as exc:
            METRICS.record_reliability_analysis_failure("analysis_clock_failure")
            raise GovernanceError(ErrorCode.INTERNAL_SERVER_ERROR) from exc

        if cursor:
            return self._continue_snapshot(
                cursor=cursor,
                automation_id=normalized_id,
                lookback_hours=int(lookback_hours),
                trace_limit=int(trace_limit),
                detail_level=detail_level,
                limit=int(limit),
                analysis_timestamp=analysis_timestamp,
                started=started,
            )

        try:
            result = await asyncio.wait_for(
                self.provider.fetch(
                    EvidenceRequest(
                        capability=ProviderCapability.RELIABILITY_ANALYSIS,
                        query={
                            "automation_id": normalized_id,
                            "lookback_hours": int(lookback_hours),
                            "trace_limit": int(trace_limit),
                            "analysis_timestamp": analysis_timestamp,
                        },
                        max_evidence=MAX_FINDINGS,
                        detail_level=DetailLevel(detail_level),
                    )
                ),
                timeout=self.timeout_seconds,
            )
        except AutomationNotFoundError:
            METRICS.record_provider_result("engineering", "failed")
            METRICS.record_reliability_analysis_failure("automation_not_found")
            raise
        except (asyncio.TimeoutError, TimeoutError) as exc:
            METRICS.record_provider_result("engineering", "failed")
            METRICS.record_reliability_analysis_failure("provider_timeout")
            raise GovernanceError(ErrorCode.PROVIDER_TIMEOUT) from exc

        if not result.succeeded or not isinstance(result.data, ReliabilityEvidenceBundle):
            METRICS.record_provider_result(result.provider_id, result.completeness.value)
            category = result.failure.category.value if result.failure else "provider_error"
            METRICS.record_reliability_analysis_failure(category)
            code = ErrorCode.PROVIDER_TIMEOUT if result.failure and result.failure.category == ProviderFailureCategory.TIMEOUT else ErrorCode.ANALYSIS_UNAVAILABLE
            raise GovernanceError(code)

        bundle = result.data
        findings = evaluate_rules(bundle)
        trace_source = next(
            (item for item in bundle.coverage if item.source_type == "automation_traces"),
            None,
        )
        independent_findings = [
            item for item in findings
            if item.rule_id not in {"trace_evidence_unavailable", "no_recent_execution_evidence"}
        ]
        if (
            trace_source
            and trace_source.completeness in {"unavailable", "failed"}
            and not independent_findings
        ):
            category = trace_source.collection_state or "trace_source_unavailable"
            METRICS.record_provider_result(result.provider_id, "failed")
            METRICS.record_reliability_analysis_failure(category)
            code = (
                ErrorCode.PROVIDER_TIMEOUT
                if trace_source.collection_state == "timeout"
                else ErrorCode.ANALYSIS_UNAVAILABLE
            )
            raise GovernanceError(code)
        METRICS.record_provider_result(result.provider_id, result.completeness.value)
        fingerprint = _analysis_fingerprint(
            bundle.evidence_fingerprint(), analysis_timestamp
        )
        offset = 0
        effective_limit = min(max(1, int(limit)), MAX_FINDINGS)
        page = findings[offset : offset + effective_limit]
        has_more = offset + len(page) < len(findings)
        next_cursor = None
        partial = result.completeness == ProviderCompleteness.PARTIAL or bundle.partial or has_more
        if has_more:
            METRICS.record_reliability_truncation()

        include_evidence = detail_level != DetailLevel.SUMMARY.value
        root_cause_groups = build_root_cause_groups(findings)
        page_ids = {item.finding_id for item in page}
        page_groups = [group for group in root_cause_groups if page_ids.intersection(group.member_finding_ids)]
        evidence_ids = {reference for item in page for reference in item.evidence_references}
        evidence = [
            bundle.evidence[reference_id].public()
            for reference_id in sorted(evidence_ids)
            if reference_id in bundle.evidence
        ][:100]
        if detail_level == DetailLevel.SUMMARY.value:
            evidence = []
        elif detail_level == DetailLevel.STANDARD.value:
            evidence = [
                {key: value for key, value in item.items() if key in {"reference_id", "source_type", "summary", "timestamp"}}
                for item in evidence
            ]
        all_findings_public = tuple(
            item.public(include_evidence=include_evidence) for item in findings
        )
        all_groups_public = tuple(
            group.public(include_evidence=include_evidence)
            for group in root_cause_groups
        )
        all_evidence_ids = {
            reference for item in findings for reference in item.evidence_references
        }
        all_evidence = [
            bundle.evidence[reference_id].public()
            for reference_id in sorted(all_evidence_ids)
            if reference_id in bundle.evidence
        ][:100]
        if detail_level == DetailLevel.SUMMARY.value:
            all_evidence = []
        elif detail_level == DetailLevel.STANDARD.value:
            all_evidence = [
                {
                    key: value for key, value in item.items()
                    if key in {"reference_id", "source_type", "summary", "timestamp"}
                }
                for item in all_evidence
            ]
        evidence_by_id = {
            str(item.get("reference_id")): item
            for item in all_evidence if item.get("reference_id")
        }

        severity_counts = Counter(item.severity for item in findings)
        root_cause_severity_counts = Counter(item.highest_severity for item in root_cause_groups)
        coverage = [item.public() for item in bundle.coverage]
        source_failures = sum(item.failed_items for item in bundle.coverage)
        traces_examined = next((item.items_examined for item in bundle.coverage if item.source_type == "automation_traces"), 0)
        entities_examined = next((item.items_examined for item in bundle.coverage if item.source_type == "entity_state"), 0)
        METRICS.record_reliability_analysis_terminal(
            partial=partial,
            finding_counts=severity_counts,
            root_cause_counts=root_cause_severity_counts,
            aggregate_findings=offset == 0,
            traces_examined=traces_examined,
            referenced_entities_examined=entities_examined,
            source_failures=source_failures,
            analysis_timestamp=analysis_timestamp,
        )

        if partial:
            assessment = "partial_evidence"
        elif findings:
            assessment = "findings_present"
        else:
            assessment = "no_findings"
        warnings = list(dict.fromkeys(result.warnings + [
            warning for item in bundle.coverage for warning in item.warnings
        ]))[:20]
        snapshot_warnings = tuple(warnings)
        if has_more:
            warnings.append("Findings were paginated; use next_cursor for the next bounded page.")
        data = {
            "target": {
                "automation_id": normalized_id,
                "entity_id": bundle.automation.get("entity_id"),
                "friendly_name": bundle.automation.get("friendly_name"),
                "enabled": str(bundle.automation.get("state", "")).lower() != "off",
                "last_triggered": normalize_timestamp(bundle.automation.get("last_triggered")),
            },
            "analysis_timestamp": analysis_timestamp,
            "requested_lookback_hours": int(lookback_hours),
            "trace_limit": int(trace_limit),
            "detail_level": detail_level,
            "overall_assessment": assessment,
            "result_status": "partial" if partial else "success",
            "finding_counts_by_severity": {severity: severity_counts.get(severity, 0) for severity in ("info", "low", "medium", "high", "critical")},
            "unique_root_cause_count": len(root_cause_groups),
            "root_cause_counts_by_severity": {severity: root_cause_severity_counts.get(severity, 0) for severity in ("info", "low", "medium", "high", "critical")},
            "root_cause_groups": [] if detail_level == DetailLevel.SUMMARY.value else [group.public(include_evidence=include_evidence) for group in page_groups[:100]],
            "findings": [item.public(include_evidence=include_evidence) for item in page],
            "evidence_references": evidence,
            "configuration_fingerprint": bundle.configuration_fingerprint,
            "evidence_fingerprint": fingerprint,
            "evidence_source_coverage": coverage,
            "trace_coverage": next((item for item in coverage if item["source_type"] == "automation_traces"), {}),
            "entity_reference_coverage": next((item for item in coverage if item["source_type"] == "entity_state"), {}),
            "system_log_coverage": next((item for item in coverage if item["source_type"] == "system_log"), {}),
            "analysis_limitations": warnings,
            "pagination": {
                "requested_limit": int(limit),
                "effective_limit": effective_limit,
                "maximum_limit": MAX_FINDINGS,
                "returned": len(page),
                "total": len(findings),
                "has_more": has_more,
                "next_cursor": next_cursor,
                "clamped": int(limit) != effective_limit,
                "clamp_reason": "maximum_limit" if int(limit) > MAX_FINDINGS else None,
            },
            "analysis_duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "timing_details": _timing_details(started),
            "cache": {
                "supported": False,
                "status": "not_configured",
                "hit": None,
                "reason": "Every new analysis recollects evidence; only cursor continuation reuses a bounded sanitized snapshot.",
                "pagination_snapshot_supported": True,
                "pagination_snapshot_ttl_seconds": int(PAGINATION_SNAPSHOT_TTL_SECONDS),
                "pagination_snapshot_is_result_cache": False,
            },
        }
        metadata = {
            "routing": {
                "lifecycle_status": "beta_native",
                "classification": "engineering_native",
                "provider": "engineering",
                "fallback_occurred": False,
                "direct_access_policy": {
                    "policy_id": "single_automation_reliability_read",
                    "access": "read",
                    "scope": "one automation and bounded supporting evidence",
                },
            },
            "source_coverage": coverage,
        }
        if has_more:
            data_base = copy.deepcopy(data)
            for key in (
                "findings", "root_cause_groups", "evidence_references",
                "pagination", "analysis_duration_ms", "timing_details",
            ):
                data_base.pop(key, None)
            snapshot = _PaginationSnapshot(
                expires_at=time.monotonic() + PAGINATION_SNAPSHOT_TTL_SECONDS,
                automation_id=normalized_id,
                lookback_hours=int(lookback_hours),
                trace_limit=int(trace_limit),
                detail_level=detail_level,
                fingerprint=fingerprint,
                analysis_timestamp=analysis_timestamp,
                data_base=data_base,
                findings=all_findings_public,
                root_cause_groups=all_groups_public,
                evidence_by_id=evidence_by_id,
                warnings=snapshot_warnings,
                metadata=copy.deepcopy(metadata),
                source_partial=(
                    result.completeness == ProviderCompleteness.PARTIAL or bundle.partial
                ),
            )
            snapshot_id = self.pagination_snapshots.put(snapshot)
            next_cursor = _encode_cursor(
                fingerprint, offset + len(page), analysis_timestamp, snapshot_id
            )
            data["pagination"]["next_cursor"] = next_cursor
        return ReliabilityAnalysisOutput(data=data, warnings=warnings, metadata=metadata, partial=partial)

    def _continue_snapshot(
        self, *, cursor, automation_id, lookback_hours, trace_limit, detail_level,
        limit, analysis_timestamp, started,
    ) -> ReliabilityAnalysisOutput:
        payload = _cursor_payload(cursor)
        snapshot_id = str(payload.get("snapshot_id") or "")
        snapshot = self.pagination_snapshots.get(snapshot_id)
        if snapshot is None:
            METRICS.record_reliability_analysis_failure("stale_cursor")
            raise GovernanceError(ErrorCode.STALE_CURSOR)
        if (
            snapshot.automation_id != automation_id
            or snapshot.lookback_hours != lookback_hours
            or snapshot.trace_limit != trace_limit
            or snapshot.detail_level != detail_level
            or snapshot.analysis_timestamp != analysis_timestamp
        ):
            METRICS.record_reliability_analysis_failure("stale_cursor")
            raise GovernanceError(ErrorCode.STALE_CURSOR)
        try:
            offset = _decode_cursor(
                cursor, snapshot.fingerprint, analysis_timestamp, snapshot_id
            )
        except GovernanceError as exc:
            METRICS.record_reliability_analysis_failure(exc.code.value)
            raise
        if offset > len(snapshot.findings):
            METRICS.record_reliability_analysis_failure("invalid_cursor")
            raise GovernanceError(ErrorCode.INVALID_CURSOR)
        effective_limit = min(max(1, limit), MAX_FINDINGS)
        page = snapshot.findings[offset: offset + effective_limit]
        page_ids = {item["finding_id"] for item in page}
        groups = [
            group for group in snapshot.root_cause_groups
            if page_ids.intersection(group.get("member_finding_ids", ()))
        ]
        evidence_ids = {
            reference for item in page for reference in item.get("evidence_references", ())
        }
        evidence = [
            snapshot.evidence_by_id[reference]
            for reference in sorted(evidence_ids)
            if reference in snapshot.evidence_by_id
        ][:100]
        has_more = offset + len(page) < len(snapshot.findings)
        next_cursor = (
            _encode_cursor(
                snapshot.fingerprint,
                offset + len(page),
                analysis_timestamp,
                snapshot_id,
            ) if has_more else None
        )
        if not has_more:
            self.pagination_snapshots.remove(snapshot_id)
        partial = snapshot.source_partial or has_more
        warnings = list(snapshot.warnings)
        if has_more:
            warnings.append("Findings were paginated; use next_cursor for the next bounded page.")
        data = copy.deepcopy(snapshot.data_base)
        data.update({
            "result_status": "partial" if partial else "success",
            "overall_assessment": (
                "partial_evidence" if partial
                else "findings_present" if snapshot.findings
                else "no_findings"
            ),
            "findings": list(page),
            "root_cause_groups": groups,
            "evidence_references": evidence,
            "analysis_limitations": warnings,
            "pagination": {
                "requested_limit": limit,
                "effective_limit": effective_limit,
                "maximum_limit": MAX_FINDINGS,
                "returned": len(page),
                "total": len(snapshot.findings),
                "has_more": has_more,
                "next_cursor": next_cursor,
                "clamped": limit != effective_limit,
                "clamp_reason": "maximum_limit" if limit > MAX_FINDINGS else None,
                "source": "bounded_sanitized_pagination_snapshot",
            },
            "analysis_duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "timing_details": _timing_details(started),
        })
        METRICS.record_reliability_analysis_terminal(
            partial=partial,
            finding_counts={},
            root_cause_counts={},
            aggregate_findings=False,
            traces_examined=0,
            referenced_entities_examined=0,
            source_failures=0,
            analysis_timestamp=analysis_timestamp,
        )
        return ReliabilityAnalysisOutput(
            data=data,
            warnings=warnings,
            metadata=copy.deepcopy(snapshot.metadata),
            partial=partial,
        )


def _timing_details(started: float) -> dict:
    telemetry = current_telemetry()
    current_ms = round((time.perf_counter() - started) * 1000, 3)
    return {
        "current_request_wall_clock_ms": current_ms,
        "engineering_analysis_wall_clock_ms": current_ms,
        "home_assistant_cumulative_attempt_ms": round(telemetry.ha_duration_ms, 3) if telemetry else 0.0,
        "home_assistant_wall_clock_span_ms": telemetry.ha_wall_clock_span_ms if telemetry else 0.0,
        "home_assistant_request_count": telemetry.ha_request_count if telemetry else 0,
        "upstream_attempted": bool(telemetry and telemetry.ha_request_count > 0),
        "maximum_concurrent_home_assistant_requests": telemetry.ha_max_concurrent_requests if telemetry else 0,
        "provider_operations_concurrent": bool(telemetry and telemetry.ha_max_concurrent_requests > 1),
        "home_assistant_duration_semantics": "cumulative_attempt_effort_and_wall_clock_span_are_reported_separately",
    }


def _encode_cursor(
    fingerprint: str, offset: int, analysis_timestamp: str,
    snapshot_id: str | None = None,
) -> str:
    raw = json.dumps(
        {
            "fingerprint": fingerprint,
            "offset": offset,
            "analysis_timestamp": analysis_timestamp,
            "snapshot_id": snapshot_id,
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(
    cursor: str, fingerprint: str, analysis_timestamp: str,
    snapshot_id: str | None = None,
) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        offset = int(value["offset"])
        cursor_fingerprint = str(value["fingerprint"])
        cursor_timestamp = str(value["analysis_timestamp"])
        cursor_snapshot_id = value.get("snapshot_id")
        if offset < 0:
            raise ValueError
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GovernanceError(ErrorCode.INVALID_CURSOR) from exc
    if (
        cursor_fingerprint != fingerprint
        or cursor_timestamp != analysis_timestamp
        or cursor_snapshot_id != snapshot_id
    ):
        raise GovernanceError(ErrorCode.STALE_CURSOR)
    return offset


def _cursor_analysis_instant(cursor: str) -> datetime:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        instant = parse_timestamp(value["analysis_timestamp"])
        if instant is None:
            raise ValueError
        return instant
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GovernanceError(ErrorCode.INVALID_CURSOR) from exc


def _cursor_payload(cursor: str) -> dict:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GovernanceError(ErrorCode.INVALID_CURSOR) from exc


def _required_clock_instant(clock) -> datetime:
    instant = parse_timestamp(clock())
    if instant is None:
        raise ValueError("analysis clock must return a timezone-aware instant")
    return instant


def _analysis_fingerprint(evidence_fingerprint: str, analysis_timestamp: str) -> str:
    return hashlib.sha256(
        f"{evidence_fingerprint}:{analysis_timestamp}".encode("utf-8")
    ).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
