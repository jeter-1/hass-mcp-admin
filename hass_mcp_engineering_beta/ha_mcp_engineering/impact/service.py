"""Facilitator service for bounded single-entity change-impact analysis."""

from __future__ import annotations

import asyncio
import base64
from collections import Counter, OrderedDict
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import secrets
import time
from typing import Any
import uuid

from ..dependency.extraction import valid_entity_id
from ..dependency.models import SOURCE_TYPES
from ..errors import EntityNotFoundError, ErrorCode, GovernanceError, InvalidRequestError
from ..facilitation import DetailLevel
from ..observability import METRICS
from ..providers import (
    EvidenceRequest,
    ProviderCapability,
    ProviderCompleteness,
    ProviderFailureCategory,
)
from ..reliability.timestamps import normalize_timestamp, parse_timestamp
from ..request_context import current_telemetry
from .models import ImpactAnalysisOutput, ImpactEvidenceBundle, OPERATIONS, SEVERITIES
from .rules import (
    build_impact_groups,
    evaluate_impact_rules,
    final_assessment,
    remediation_checklist,
)


MAX_FINDINGS = 100
DETAIL_RESULT_CAPS = {"summary": 50, "standard": 30, "evidence": 20}
MAX_SOURCE_TYPES = len(SOURCE_TYPES)
PAGINATION_SNAPSHOT_TTL_SECONDS = 300.0
MAX_PAGINATION_SNAPSHOTS = 16


@dataclass
class _PaginationSnapshot:
    expires_at: float
    query_fingerprint: str
    evidence_fingerprint: str
    analysis_timestamp: str
    detail_level: str
    data_base: dict[str, Any]
    findings: tuple[dict[str, Any], ...]
    groups: tuple[dict[str, Any], ...]
    evidence_by_id: dict[str, dict[str, Any]]
    warnings: tuple[str, ...]
    metadata: dict[str, Any]
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


class ChangeImpactAnalysisService:
    def __init__(
        self,
        provider,
        *,
        timeout_seconds: float = 60.0,
        clock=None,
        cursor_key: bytes | None = None,
    ):
        self.provider = provider
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 120.0))
        self.clock = clock or _utc_now
        self.cursor_key = cursor_key or secrets.token_bytes(32)
        self.pagination_snapshots = _PaginationSnapshotStore()

    async def analyze(
        self,
        *,
        entity_id: str,
        operation: str,
        replacement_entity_id: str | None = None,
        include_indirect: bool = True,
        max_depth: int = 2,
        source_types: list[str] | None = None,
        detail_level: str = "standard",
        limit: int = 20,
        cursor: str = "",
        refresh_index: bool = False,
    ) -> ImpactAnalysisOutput:
        started = time.perf_counter()
        METRICS.record_impact_analysis_request()
        try:
            validated = _validate_inputs(
                entity_id=entity_id,
                operation=operation,
                replacement_entity_id=replacement_entity_id,
                include_indirect=include_indirect,
                max_depth=max_depth,
                source_types=source_types,
                detail_level=detail_level,
                limit=limit,
                refresh_index=refresh_index,
            )
        except InvalidRequestError:
            METRICS.record_impact_analysis_failure("request_validation")
            raise

        query_fingerprint = _query_fingerprint(
            validated["entity_id"],
            validated["operation"],
            validated["replacement_entity_id"],
            validated["include_indirect"],
            validated["max_depth"],
            validated["source_types"],
            validated["detail_level"],
            validated["refresh_index"],
        )
        if cursor:
            return self._continue_snapshot(
                cursor=cursor,
                query_fingerprint=query_fingerprint,
                limit=validated["limit"],
                started=started,
            )

        try:
            analysis_instant = _required_clock_instant(self.clock)
            analysis_timestamp = normalize_timestamp(analysis_instant)
            if analysis_timestamp is None:
                raise ValueError("analysis timestamp normalization failed")
        except Exception as exc:
            METRICS.record_impact_analysis_failure("analysis_clock_failure")
            raise GovernanceError(ErrorCode.INTERNAL_SERVER_ERROR) from exc

        try:
            result = await asyncio.wait_for(
                self.provider.fetch(
                    EvidenceRequest(
                        capability=ProviderCapability.IMPACT_ANALYSIS,
                        query={
                            **validated,
                            "analysis_timestamp": analysis_timestamp,
                        },
                        max_evidence=MAX_FINDINGS,
                        detail_level=DetailLevel(validated["detail_level"]),
                    )
                ),
                timeout=self.timeout_seconds,
            )
        except EntityNotFoundError:
            METRICS.record_provider_result("engineering", "failed")
            METRICS.record_impact_analysis_failure("entity_not_found")
            raise
        except (asyncio.TimeoutError, TimeoutError) as exc:
            METRICS.record_provider_result("engineering", "failed")
            METRICS.record_impact_analysis_failure("provider_timeout")
            raise GovernanceError(ErrorCode.PROVIDER_TIMEOUT) from exc
        except GovernanceError as exc:
            METRICS.record_provider_result("engineering", "failed")
            METRICS.record_impact_analysis_failure(exc.code.value)
            raise

        if not result.succeeded or not isinstance(result.data, ImpactEvidenceBundle):
            METRICS.record_provider_result(result.provider_id, result.completeness.value)
            category = (
                result.failure.category.value if result.failure else "provider_error"
            )
            METRICS.record_impact_analysis_failure(category)
            code = (
                ErrorCode.PROVIDER_TIMEOUT
                if result.failure
                and result.failure.category == ProviderFailureCategory.TIMEOUT
                else ErrorCode.ANALYSIS_UNAVAILABLE
            )
            raise GovernanceError(code)

        bundle = result.data
        METRICS.record_provider_result(result.provider_id, result.completeness.value)
        try:
            findings = evaluate_impact_rules(bundle)
            _verify_evidence_invariant(findings, bundle)
            groups = build_impact_groups(findings)
            assessment = final_assessment(findings, bundle)
        except GovernanceError as exc:
            METRICS.record_impact_analysis_failure(exc.code.value)
            raise
        except Exception as exc:
            METRICS.record_impact_analysis_failure("internal_server_error")
            raise GovernanceError(ErrorCode.INTERNAL_SERVER_ERROR) from exc
        effective_limit, clamp_reason = _effective_limit(
            validated["limit"], validated["detail_level"]
        )
        page = findings[:effective_limit]
        has_more = len(page) < len(findings)
        if has_more:
            METRICS.record_impact_truncation()

        include_evidence = validated["detail_level"] != "summary"
        page_ids = {item.finding_id for item in page}
        page_groups = [
            item for item in groups if page_ids.intersection(item.finding_ids)
        ]
        page_reference_ids = {
            reference for item in page for reference in item.evidence_references
        }
        evidence = _public_evidence(
            bundle,
            page_reference_ids,
            detail_level=validated["detail_level"],
        )
        coverage = [item.public() for item in bundle.coverage]
        required_source_failures = sum(
            item.failed_items
            for item in bundle.coverage
            if item.required_for_assessment and not item.assessment_complete
        )
        source_partial = bundle.source_partial
        partial = source_partial or has_more
        warnings = list(
            dict.fromkeys(
                [
                    *result.warnings,
                    *[
                        warning
                        for item in bundle.coverage
                        for warning in item.warnings
                    ],
                ]
            )
        )[:20]
        snapshot_warnings = tuple(warnings)
        if has_more:
            warnings.append(
                "Findings were paginated; continue with the returned cursor."
            )

        severity_counts = Counter(item.severity for item in findings)
        object_counts = Counter(item.affected_object_type for item in findings)
        direct_count = sum(item.direct for item in findings)
        indirect_count = len(findings) - direct_count
        unique_affected = {
            (item.affected_object_type, item.affected_object_id) for item in findings
        }
        dynamic_count = sum(
            item.rule_id == "unresolved_dynamic_reference" for item in findings
        )
        METRICS.record_impact_analysis_terminal(
            partial=partial,
            operation=validated["operation"],
            severity_counts=severity_counts,
            object_counts=object_counts,
            direct_impacts=direct_count,
            indirect_impacts=indirect_count,
            unique_root_causes=len(groups),
            dynamic_review_events=dynamic_count,
            source_failures=required_source_failures,
            index_cache_hit=bool(bundle.index.get("cache_hit")),
            analysis_timestamp=analysis_timestamp,
        )

        data = {
            "target_entity_summary": bundle.target,
            "requested_operation": validated["operation"],
            "replacement_entity_id": validated["replacement_entity_id"],
            "analysis_timestamp": analysis_timestamp,
            "final_assessment": assessment,
            "result_status": "partial" if partial else "success",
            "severity_totals": {
                severity: severity_counts.get(severity, 0)
                for severity in SEVERITIES
            },
            "direct_impact_count": direct_count,
            "indirect_impact_count": indirect_count,
            "affected_object_count": len(unique_affected),
            "affected_object_totals": dict(sorted(object_counts.items())),
            "unique_root_cause_count": len(groups),
            "finding_count": len(findings),
            "findings": [
                item.public(include_evidence=include_evidence) for item in page
            ],
            "affected_object_groups": [
                item.public(include_evidence=include_evidence)
                for item in page_groups[:100]
            ],
            "advisory_remediation_checklist": remediation_checklist(
                validated["operation"], findings
            ),
            "evidence_references": evidence,
            "source_coverage_matrix": coverage,
            "dynamic_reference_summary": {
                "count": dynamic_count,
                "manual_review_required": dynamic_count > 0,
            },
            "pagination": {
                "requested_limit": validated["limit"],
                "effective_limit": effective_limit,
                "maximum_limit": MAX_FINDINGS,
                "effective_payload_cap": DETAIL_RESULT_CAPS[
                    validated["detail_level"]
                ],
                "clamped": validated["limit"] != effective_limit,
                "clamp_reason": clamp_reason,
                "returned": len(page),
                "total": len(findings),
                "has_more": has_more,
                "next_cursor": None,
            },
            "index_and_cache_provenance": {
                "index_fingerprint": str(bundle.index.get("fingerprint", ""))[:16],
                "index_generation": bundle.index.get("generation"),
                "index_built_at": bundle.index.get("built_at"),
                "index_cache_hit": bool(bundle.index.get("cache_hit")),
                "index_refreshed": bool(bundle.index.get("refreshed")),
                "dependency_index_lookup_duration_ms": bundle.index.get(
                    "lookup_duration_ms", 0.0
                ),
                "current_index_build_duration_ms": bundle.index.get(
                    "current_index_build_duration_ms", 0.0
                ),
                "original_index_build_duration_ms": bundle.index.get(
                    "original_build_duration_ms", 0.0
                ),
                "pagination_snapshot_supported": True,
                "pagination_snapshot_ttl_seconds": int(
                    PAGINATION_SNAPSHOT_TTL_SECONDS
                ),
                "pagination_snapshot_is_result_cache": False,
                "general_result_cache_supported": False,
            },
            "timing_details": _timing_details(
                started,
                bundle=bundle,
            ),
            "explicit_limitations": _limitations(bundle),
        }
        metadata = {
            "routing": {
                "lifecycle_status": "beta_native",
                "classification": "engineering_native",
                "provider": "engineering",
                "policy": "single_entity_change_impact_read",
                "access": "read",
                "fallback_occurred": False,
                "standard_ha_mcp_coverage": "unavailable",
            },
            "source_coverage": coverage,
        }

        all_findings_public = tuple(
            item.public(include_evidence=include_evidence) for item in findings
        )
        all_groups_public = tuple(
            item.public(include_evidence=include_evidence) for item in groups
        )
        all_evidence = _public_evidence(
            bundle,
            set(bundle.evidence),
            detail_level=validated["detail_level"],
        )
        evidence_by_id = {
            str(item.get("reference_id")): item
            for item in all_evidence
            if item.get("reference_id")
        }
        if has_more:
            data_base = copy.deepcopy(data)
            for key in (
                "findings",
                "affected_object_groups",
                "evidence_references",
                "pagination",
                "timing_details",
            ):
                data_base.pop(key, None)
            evidence_fingerprint = bundle.evidence_fingerprint()
            snapshot_id = self.pagination_snapshots.put(
                _PaginationSnapshot(
                    expires_at=time.monotonic()
                    + PAGINATION_SNAPSHOT_TTL_SECONDS,
                    query_fingerprint=query_fingerprint,
                    evidence_fingerprint=evidence_fingerprint,
                    analysis_timestamp=analysis_timestamp,
                    detail_level=validated["detail_level"],
                    data_base=data_base,
                    findings=all_findings_public,
                    groups=all_groups_public,
                    evidence_by_id=evidence_by_id,
                    warnings=snapshot_warnings,
                    metadata=copy.deepcopy(metadata),
                    source_partial=source_partial,
                )
            )
            data["pagination"]["next_cursor"] = self._encode_cursor(
                snapshot_id=snapshot_id,
                query_fingerprint=query_fingerprint,
                evidence_fingerprint=evidence_fingerprint,
                analysis_timestamp=analysis_timestamp,
                offset=len(page),
            )

        _set_audit_summary(
            operation=validated["operation"],
            assessment=assessment,
            finding_count=len(findings),
            affected_object_count=len(unique_affected),
            coverage_complete=bundle.required_coverage_complete,
        )
        return ImpactAnalysisOutput(
            data=data,
            warnings=warnings,
            metadata=metadata,
            partial=partial,
        )

    def _continue_snapshot(
        self,
        *,
        cursor: str,
        query_fingerprint: str,
        limit: int,
        started: float,
    ) -> ImpactAnalysisOutput:
        METRICS.record_impact_cursor_continuation()
        try:
            payload = self._decode_cursor(cursor)
        except GovernanceError as exc:
            METRICS.record_impact_cursor_event(exc.code.value)
            raise
        snapshot = self.pagination_snapshots.get(str(payload["snapshot_id"]))
        if snapshot is None:
            METRICS.record_impact_cursor_event("stale_cursor")
            raise GovernanceError(ErrorCode.STALE_CURSOR)
        if (
            payload.get("query_fingerprint") != query_fingerprint
            or snapshot.query_fingerprint != query_fingerprint
            or payload.get("evidence_fingerprint") != snapshot.evidence_fingerprint
            or payload.get("analysis_timestamp") != snapshot.analysis_timestamp
        ):
            METRICS.record_impact_cursor_event("stale_cursor")
            raise GovernanceError(ErrorCode.STALE_CURSOR)
        offset = int(payload["offset"])
        if offset < 0 or offset > len(snapshot.findings):
            METRICS.record_impact_cursor_event("invalid_cursor")
            raise GovernanceError(ErrorCode.INVALID_CURSOR)
        effective_limit, clamp_reason = _effective_limit(
            limit, snapshot.detail_level
        )
        page = snapshot.findings[offset : offset + effective_limit]
        page_ids = {item["finding_id"] for item in page}
        groups = [
            group
            for group in snapshot.groups
            if page_ids.intersection(group.get("finding_ids", ()))
        ]
        references = {
            reference
            for item in page
            for reference in item.get("evidence_references", ())
        }
        evidence = [
            snapshot.evidence_by_id[reference]
            for reference in sorted(references)
            if reference in snapshot.evidence_by_id
        ][:100]
        next_offset = offset + len(page)
        has_more = next_offset < len(snapshot.findings)
        next_cursor = (
            self._encode_cursor(
                snapshot_id=str(payload["snapshot_id"]),
                query_fingerprint=query_fingerprint,
                evidence_fingerprint=snapshot.evidence_fingerprint,
                analysis_timestamp=snapshot.analysis_timestamp,
                offset=next_offset,
            )
            if has_more
            else None
        )
        if not has_more:
            self.pagination_snapshots.remove(str(payload["snapshot_id"]))
        partial = snapshot.source_partial or has_more
        warnings = list(snapshot.warnings)
        if has_more:
            warnings.append(
                "Findings were paginated; continue with the returned cursor."
            )
        data = copy.deepcopy(snapshot.data_base)
        data.update(
            {
                "result_status": "partial" if partial else "success",
                "findings": list(page),
                "affected_object_groups": groups,
                "evidence_references": evidence,
                "pagination": {
                    "requested_limit": limit,
                    "effective_limit": effective_limit,
                    "maximum_limit": MAX_FINDINGS,
                    "effective_payload_cap": DETAIL_RESULT_CAPS[
                        snapshot.detail_level
                    ],
                    "clamped": limit != effective_limit,
                    "clamp_reason": clamp_reason,
                    "returned": len(page),
                    "total": len(snapshot.findings),
                    "has_more": has_more,
                    "next_cursor": next_cursor,
                    "source": "bounded_sanitized_pagination_snapshot",
                },
                "timing_details": _timing_details(started, bundle=None),
            }
        )
        return ImpactAnalysisOutput(
            data=data,
            warnings=warnings,
            metadata=copy.deepcopy(snapshot.metadata),
            partial=partial,
        )

    def _encode_cursor(
        self,
        *,
        snapshot_id,
        query_fingerprint,
        evidence_fingerprint,
        analysis_timestamp,
        offset,
    ) -> str:
        payload = json.dumps(
            {
                "snapshot_id": snapshot_id,
                "query_fingerprint": query_fingerprint,
                "evidence_fingerprint": evidence_fingerprint,
                "analysis_timestamp": analysis_timestamp,
                "offset": int(offset),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = hmac.new(self.cursor_key, payload, hashlib.sha256).digest()
        return (
            base64.urlsafe_b64encode(payload).decode().rstrip("=")
            + "."
            + base64.urlsafe_b64encode(signature).decode().rstrip("=")
        )

    def _decode_cursor(self, cursor: str) -> dict[str, Any]:
        try:
            payload_part, signature_part = cursor.split(".", 1)
            payload = base64.urlsafe_b64decode(
                payload_part + "=" * (-len(payload_part) % 4)
            )
            signature = base64.urlsafe_b64decode(
                signature_part + "=" * (-len(signature_part) % 4)
            )
            expected = hmac.new(self.cursor_key, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("cursor signature mismatch")
            value = json.loads(payload.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("cursor payload is not an object")
            if not all(
                key in value
                for key in (
                    "snapshot_id",
                    "query_fingerprint",
                    "evidence_fingerprint",
                    "analysis_timestamp",
                    "offset",
                )
            ):
                raise ValueError("cursor fields are missing")
            value["offset"] = int(value["offset"])
            return value
        except Exception as exc:
            raise GovernanceError(ErrorCode.INVALID_CURSOR) from exc


def _validate_inputs(**values) -> dict[str, Any]:
    entity_id = values["entity_id"]
    if (
        not isinstance(entity_id, str)
        or entity_id != entity_id.strip()
        or entity_id != entity_id.lower()
        or not valid_entity_id(entity_id)
    ):
        raise InvalidRequestError(details={"operation": "change_impact_analysis"})
    operation = str(values["operation"])
    if operation not in OPERATIONS:
        raise InvalidRequestError(details={"operation": "change_impact_analysis"})
    replacement_raw = values.get("replacement_entity_id")
    replacement = replacement_raw if replacement_raw not in (None, "") else None
    if operation == "rename_entity":
        if (
            not isinstance(replacement, str)
            or replacement != replacement.strip()
            or replacement != replacement.lower()
            or not valid_entity_id(replacement)
            or replacement == entity_id
        ):
            raise InvalidRequestError(details={"operation": "change_impact_analysis"})
    elif replacement is not None:
        raise InvalidRequestError(details={"operation": "change_impact_analysis"})
    try:
        max_depth = int(values["max_depth"])
        limit = int(values["limit"])
    except (TypeError, ValueError) as exc:
        raise InvalidRequestError(
            details={"operation": "change_impact_analysis"}
        ) from exc
    if not 1 <= max_depth <= 3 or limit < 1:
        raise InvalidRequestError(details={"operation": "change_impact_analysis"})
    detail_level = str(values["detail_level"])
    if detail_level not in {item.value for item in DetailLevel}:
        raise InvalidRequestError(details={"operation": "change_impact_analysis"})
    raw_sources = values.get("source_types")
    requested = list(dict.fromkeys(raw_sources or SOURCE_TYPES))
    if (
        len(requested) > MAX_SOURCE_TYPES
        or any(item not in SOURCE_TYPES for item in requested)
    ):
        raise InvalidRequestError(details={"operation": "change_impact_analysis"})
    return {
        "entity_id": entity_id,
        "operation": operation,
        "replacement_entity_id": replacement,
        "include_indirect": bool(values["include_indirect"]),
        "max_depth": max_depth,
        "source_types": requested,
        "detail_level": detail_level,
        "limit": limit,
        "refresh_index": bool(values["refresh_index"]),
    }


def _public_evidence(bundle, reference_ids, *, detail_level):
    if detail_level == "summary":
        return []
    return [
        bundle.evidence[reference].public(detail_level=detail_level)
        for reference in sorted(reference_ids)
        if reference in bundle.evidence
    ][:100]


def _verify_evidence_invariant(findings, bundle):
    missing = {
        reference
        for finding in findings
        for reference in finding.evidence_references
        if reference not in bundle.evidence
    }
    if missing:
        raise GovernanceError(ErrorCode.INTERNAL_SERVER_ERROR)


def _query_fingerprint(*parts):
    return hashlib.sha256(
        json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _effective_limit(requested: int, detail_level: str) -> tuple[int, str | None]:
    maximum = DETAIL_RESULT_CAPS[detail_level]
    effective = min(max(1, int(requested)), MAX_FINDINGS, maximum)
    if int(requested) > MAX_FINDINGS:
        reason = "maximum_limit"
    elif int(requested) > maximum:
        reason = "detail_level_payload_cap"
    else:
        reason = None
    return effective, reason


def _required_clock_instant(clock) -> datetime:
    instant = parse_timestamp(clock())
    if instant is None:
        raise ValueError("analysis clock must return a timezone-aware instant")
    return instant


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timing_details(started: float, *, bundle: ImpactEvidenceBundle | None):
    telemetry = current_telemetry()
    current_ms = round((time.perf_counter() - started) * 1000, 3)
    index = bundle.index if bundle else {}
    return {
        "current_request_wall_clock_ms": current_ms,
        "engineering_analysis_wall_clock_ms": current_ms,
        "dependency_index_cache_lookup_ms": round(
            float(index.get("lookup_duration_ms", 0.0)), 3
        ),
        "current_index_build_duration_ms": round(
            float(index.get("current_index_build_duration_ms", 0.0)), 3
        ),
        "original_index_build_duration_ms": round(
            float(index.get("original_build_duration_ms", 0.0)), 3
        ),
        "evidence_collection_wall_clock_ms": round(
            float(bundle.evidence_collection_duration_ms if bundle else 0.0), 3
        ),
        "home_assistant_cumulative_attempt_ms": round(
            telemetry.ha_duration_ms, 3
        )
        if telemetry
        else 0.0,
        "home_assistant_wall_clock_span_ms": telemetry.ha_wall_clock_span_ms
        if telemetry
        else 0.0,
        "home_assistant_request_count": telemetry.ha_request_count
        if telemetry
        else 0,
        "maximum_concurrent_home_assistant_requests": telemetry.ha_max_concurrent_requests
        if telemetry
        else 0,
        "provider_operations_concurrent": bool(
            telemetry and telemetry.ha_max_concurrent_requests > 1
        ),
        "upstream_attempted": bool(telemetry and telemetry.ha_request_count > 0),
        "home_assistant_duration_semantics": "cumulative_attempt_effort_and_wall_clock_span_are_reported_separately",
    }


def _limitations(bundle: ImpactEvidenceBundle) -> list[str]:
    values = [
        "This read-only analysis reports known evidence and never proves that an entity is safe to change solely because no reference was found.",
        "Static YAML/packages, custom integration configuration, and unsupported source types are not claimed as inspected.",
        "Dynamic templates may conceal relationships that static analysis cannot resolve.",
        "System Log and automation traces have bounded retention and are auxiliary runtime evidence.",
        "Home Assistant reference rewriting is not assumed for any source type.",
    ]
    if bundle.source_partial:
        values.append(
            "Required source coverage is incomplete; the clean complete-coverage assessment is unavailable."
        )
    return values[:10]


def _set_audit_summary(**values) -> None:
    telemetry = current_telemetry()
    if telemetry is not None:
        telemetry.audit_context = {
            "operation": str(values["operation"])[:32],
            "assessment": str(values["assessment"])[:64],
            "finding_count": max(0, int(values["finding_count"])),
            "affected_object_count": max(0, int(values["affected_object_count"])),
            "coverage_complete": bool(values["coverage_complete"]),
        }
