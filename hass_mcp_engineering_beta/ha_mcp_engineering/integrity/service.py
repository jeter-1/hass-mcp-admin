"""Facilitator service for bounded global configuration-integrity analysis."""

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

from ..dependency.models import SOURCE_TYPES
from ..errors import ErrorCode, GovernanceError, InvalidRequestError
from ..facilitation import DetailLevel
from ..observability import METRICS
from ..providers import (
    EvidenceRequest,
    ProviderCapability,
    ProviderFailureCategory,
)
from ..request_context import current_telemetry
from .models import (
    FINDING_TYPES,
    SEVERITIES,
    IntegrityAnalysisOutput,
    IntegrityEvidenceBundle,
)
from .rules import classify_integrity


MAX_PAGE_LIMIT = 100
MAX_ANALYSIS_FINDINGS = 2_000
DETAIL_RESULT_CAPS = {"summary": 50, "standard": 30, "evidence": 20}
PAGINATION_SNAPSHOT_TTL_SECONDS = 300.0
MAX_PAGINATION_SNAPSHOTS = 16


@dataclass
class _IntegritySnapshot:
    expires_at: float
    query_fingerprint: str
    evidence_fingerprint: str
    index_generation: int
    index_fingerprint: str
    analysis_timestamp: str
    detail_level: str
    data_base: dict[str, Any]
    findings: tuple[dict[str, Any], ...]
    evidence_by_id: dict[str, dict[str, Any]]
    warnings: tuple[str, ...]
    metadata: dict[str, Any]
    source_partial: bool


class _IntegritySnapshotStore:
    def __init__(self):
        self._values: OrderedDict[str, _IntegritySnapshot] = OrderedDict()

    def put(self, value: _IntegritySnapshot) -> str:
        self._purge()
        snapshot_id = uuid.uuid4().hex
        self._values[snapshot_id] = value
        while len(self._values) > MAX_PAGINATION_SNAPSHOTS:
            self._values.popitem(last=False)
        return snapshot_id

    def get(self, snapshot_id: str) -> tuple[_IntegritySnapshot | None, str | None]:
        value = self._values.get(snapshot_id)
        if value is None:
            self._purge()
            return None, "snapshot_unavailable"
        if value.expires_at <= time.monotonic():
            self._values.pop(snapshot_id, None)
            self._purge()
            return None, "snapshot_expired"
        self._values.move_to_end(snapshot_id)
        self._purge()
        return value, None

    def remove(self, snapshot_id: str) -> None:
        self._values.pop(snapshot_id, None)

    def _purge(self) -> None:
        now = time.monotonic()
        for key in [
            key for key, value in self._values.items() if value.expires_at <= now
        ]:
            self._values.pop(key, None)


class ConfigurationIntegrityAnalysisService:
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
        self.pagination_snapshots = _IntegritySnapshotStore()

    async def analyze(
        self,
        *,
        source_types: list[str] | None = None,
        finding_types: list[str] | None = None,
        include_orphan_candidates: bool = True,
        detail_level: str = "standard",
        limit: int = 20,
        cursor: str = "",
        refresh_index: bool = False,
    ) -> IntegrityAnalysisOutput:
        started = time.perf_counter()
        METRICS.record_integrity_analysis_request()
        try:
            validated = _validate_inputs(
                source_types=source_types,
                finding_types=finding_types,
                include_orphan_candidates=include_orphan_candidates,
                detail_level=detail_level,
                limit=limit,
                cursor=cursor,
                refresh_index=refresh_index,
            )
        except InvalidRequestError:
            if cursor:
                METRICS.record_integrity_cursor_continuation()
                METRICS.record_integrity_cursor_event("invalid_cursor")
            else:
                METRICS.record_integrity_analysis_failure("request_validation")
            raise

        query_fingerprint = _query_fingerprint(
            validated["source_types"],
            validated["finding_types"],
            validated["include_orphan_candidates"],
            validated["detail_level"],
        )
        if cursor:
            return self._continue_snapshot(
                cursor=cursor,
                query_fingerprint=query_fingerprint,
                limit=validated["limit"],
                started=started,
            )

        analysis_timestamp = _analysis_timestamp(self.clock)
        try:
            result = await asyncio.wait_for(
                self.provider.fetch(
                    EvidenceRequest(
                        capability=(
                            ProviderCapability.CONFIGURATION_INTEGRITY_ANALYSIS
                        ),
                        query={
                            **validated,
                            "analysis_timestamp": analysis_timestamp,
                        },
                        max_evidence=MAX_PAGE_LIMIT,
                        detail_level=DetailLevel(validated["detail_level"]),
                    )
                ),
                timeout=self.timeout_seconds,
            )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            METRICS.record_provider_result("engineering", "failed", dispatched=True)
            METRICS.record_integrity_analysis_failure("provider_timeout")
            raise GovernanceError(ErrorCode.PROVIDER_TIMEOUT) from exc
        except GovernanceError as exc:
            METRICS.record_provider_result("engineering", "failed", dispatched=True)
            METRICS.record_integrity_analysis_failure(exc.code.value)
            raise

        if not result.succeeded or not isinstance(
            result.data, IntegrityEvidenceBundle
        ):
            METRICS.record_provider_result(
                result.provider_id, result.completeness.value, dispatched=True
            )
            category = (
                result.failure.category.value
                if result.failure
                else "provider_error"
            )
            METRICS.record_integrity_analysis_failure(category)
            code = (
                ErrorCode.PROVIDER_TIMEOUT
                if result.failure
                and result.failure.category == ProviderFailureCategory.TIMEOUT
                else ErrorCode.ANALYSIS_UNAVAILABLE
            )
            raise GovernanceError(code)

        bundle = result.data
        METRICS.record_provider_result(
            result.provider_id, result.completeness.value, dispatched=True
        )
        try:
            findings, evidence_by_model, rule_warnings = classify_integrity(
                bundle,
                finding_types=validated["finding_types"],
                include_orphan_candidates=validated[
                    "include_orphan_candidates"
                ],
            )
        except Exception as exc:
            METRICS.record_integrity_analysis_failure("internal_server_error")
            raise GovernanceError(ErrorCode.INTERNAL_SERVER_ERROR) from exc

        analysis_truncated = len(findings) > MAX_ANALYSIS_FINDINGS
        if analysis_truncated:
            findings = findings[:MAX_ANALYSIS_FINDINGS]

        severity_counts = Counter(item.severity for item in findings)
        type_counts = Counter(item.finding_type for item in findings)
        source_counts = Counter(
            item.source_type for item in findings if item.source_type
        )
        unique_sources = {
            (item.source_type, item.source_id)
            for item in findings
            if item.source_type and item.source_id
        }
        unique_targets = {
            item.target_entity_id for item in findings if item.target_entity_id
        }
        orphan_count = type_counts.get("orphan_registry_candidate", 0)
        in_scope_dynamic_count = len(bundle.dynamic_references)
        unresolved_count = type_counts.get("unresolved_dynamic_reference", 0)
        manual_review_required = bool(findings) or in_scope_dynamic_count > 0
        coverage = [item.public() for item in bundle.coverage]
        source_failures = sum(
            item.failed_items
            for item in bundle.coverage
            if item.required_for_assessment and not item.assessment_complete
        )
        coverage_incomplete = bundle.source_partial or analysis_truncated
        assessment = (
            "review_required"
            if manual_review_required
            else "assessment_incomplete"
            if coverage_incomplete
            else "no_confirmed_integrity_findings"
        )
        effective_limit, clamp_reason = _effective_limit(
            validated["limit"], validated["detail_level"]
        )
        page = findings[:effective_limit]
        has_more = len(page) < len(findings)
        if analysis_truncated:
            METRICS.record_integrity_truncation()
        source_partial = coverage_incomplete or analysis_truncated
        partial = source_partial or has_more
        include_evidence = validated["detail_level"] != "summary"
        page_reference_ids = {
            reference for item in page for reference in item.evidence_references
        }
        public_evidence = [
            evidence_by_model[reference].public(
                detail_level=validated["detail_level"]
            )
            for reference in sorted(page_reference_ids)
            if reference in evidence_by_model
        ][:100]
        warnings = list(
            dict.fromkeys(
                [
                    *result.warnings,
                    *rule_warnings,
                    *[
                        warning
                        for item in bundle.coverage
                        for warning in item.warnings
                    ],
                ]
            )
        )[:20]
        if analysis_truncated:
            warnings.append(
                "The bounded whole-analysis finding cap was reached; assessment is incomplete."
            )
        snapshot_warnings = tuple(warnings)
        if has_more:
            warnings.append(
                "Findings were paginated; continue with the returned cursor."
            )

        data = {
            "analysis_timestamp": analysis_timestamp,
            "final_assessment": assessment,
            "result_status": "partial" if partial else "success",
            "finding_count": len(findings),
            "findings_by_severity": {
                severity: severity_counts.get(severity, 0)
                for severity in SEVERITIES
            },
            "findings_by_type": {
                finding_type: type_counts.get(finding_type, 0)
                for finding_type in FINDING_TYPES
            },
            "findings_by_source_type": dict(sorted(source_counts.items())),
            "unique_source_object_count": len(unique_sources),
            "unique_target_entity_count": len(unique_targets),
            "unique_orphan_candidate_count": orphan_count,
            "unresolved_dynamic_reference_count": in_scope_dynamic_count,
            "manual_review_required": manual_review_required,
            "findings": [
                item.public(include_evidence=include_evidence) for item in page
            ],
            "evidence_references": public_evidence,
            "source_coverage_matrix": coverage,
            "dynamic_reference_summary": {
                "unresolved_in_requested_scope_count": in_scope_dynamic_count,
                "reported_finding_count": unresolved_count,
                "outside_requested_scope_count": (
                    bundle.dynamic_outside_requested_scope_count
                ),
                "manual_review_required": in_scope_dynamic_count > 0,
                "review_items": _dynamic_review_items(bundle),
                "target_entity_ids_inferred": False,
            },
            "counter_semantics": {
                "finding_count": "whole_bounded_analysis_findings",
                "unique_source_object_count": "unique_source_type_and_source_id_pairs_with_findings",
                "unique_target_entity_count": "unique_confirmed_target_ids_across_reference_and_candidate_findings",
                "unique_orphan_candidate_count": "registry_candidates_only",
                "pagination_changes_whole_analysis_totals": False,
            },
            "pagination": {
                "requested_limit": validated["limit"],
                "effective_limit": effective_limit,
                "maximum_limit": MAX_PAGE_LIMIT,
                "effective_payload_cap": DETAIL_RESULT_CAPS[
                    validated["detail_level"]
                ],
                "clamped": effective_limit != validated["limit"],
                "clamp_reason": clamp_reason,
                "returned": len(page),
                "total": len(findings),
                "has_more": has_more,
                "next_cursor": None,
            },
            "index_and_cache_provenance": {
                "index_fingerprint": str(bundle.index.get("fingerprint") or ""),
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
                "pagination_snapshot_ttl_seconds": int(
                    PAGINATION_SNAPSHOT_TTL_SECONDS
                ),
                "pagination_snapshot_is_result_cache": False,
                "general_result_cache_supported": False,
            },
            "timing_details": _timing_details(started, bundle=bundle),
            "explicit_limitations": _limitations(),
        }
        metadata = {
            "routing": {
                "lifecycle_status": "beta_native",
                "classification": "engineering_native",
                "provider": "engineering",
                "policy": "global_configuration_integrity_read",
                "access": "read",
                "fallback_occurred": False,
                "standard_ha_mcp_coverage": "unavailable",
            },
            "source_coverage": coverage,
        }

        all_findings_public = tuple(
            item.public(include_evidence=include_evidence) for item in findings
        )
        all_evidence = {
            reference_id: item.public(detail_level=validated["detail_level"])
            for reference_id, item in evidence_by_model.items()
        }
        if has_more:
            data_base = copy.deepcopy(data)
            for key in ("findings", "evidence_references", "pagination", "timing_details"):
                data_base.pop(key, None)
            evidence_fingerprint = bundle.evidence_fingerprint()
            index_generation = int(bundle.index.get("generation", 0))
            index_fingerprint = str(bundle.index.get("fingerprint") or "")
            active_index = _active_index_identity(self.provider)
            if (
                not active_index
                or not active_index.get("valid")
                or int(active_index.get("generation", 0)) != index_generation
                or str(active_index.get("fingerprint") or "")
                != index_fingerprint
            ):
                METRICS.record_integrity_analysis_failure(
                    "index_changed_before_snapshot_commit"
                )
                raise GovernanceError(
                    ErrorCode.ANALYSIS_UNAVAILABLE,
                    details=_details(
                        "cursor", "index_changed_before_snapshot_commit"
                    ),
                )
            snapshot_id = self.pagination_snapshots.put(
                _IntegritySnapshot(
                    expires_at=time.monotonic()
                    + PAGINATION_SNAPSHOT_TTL_SECONDS,
                    query_fingerprint=query_fingerprint,
                    evidence_fingerprint=evidence_fingerprint,
                    index_generation=index_generation,
                    index_fingerprint=index_fingerprint,
                    analysis_timestamp=analysis_timestamp,
                    detail_level=validated["detail_level"],
                    data_base=data_base,
                    findings=all_findings_public,
                    evidence_by_id=all_evidence,
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
                index_generation=index_generation,
                index_fingerprint=index_fingerprint,
                offset=len(page),
            )

        METRICS.record_integrity_analysis_terminal(
            partial=partial,
            severity_counts=severity_counts,
            type_counts=type_counts,
            source_counts=source_counts,
            finding_count=len(findings),
            unique_source_object_count=len(unique_sources),
            unique_target_entity_count=len(unique_targets),
            orphan_candidate_count=orphan_count,
            unresolved_dynamic_reference_count=in_scope_dynamic_count,
            manual_review_required=manual_review_required,
            source_failures=source_failures,
            index_cache_hit=bool(bundle.index.get("cache_hit")),
            analysis_timestamp=analysis_timestamp,
        )
        _set_audit_summary(
            assessment=assessment,
            finding_count=len(findings),
            unique_source_object_count=len(unique_sources),
            unique_target_entity_count=len(unique_targets),
            orphan_candidate_count=orphan_count,
            unresolved_dynamic_reference_count=in_scope_dynamic_count,
            coverage_complete=bundle.required_coverage_complete,
        )
        return IntegrityAnalysisOutput(
            data=data, warnings=warnings, metadata=metadata, partial=partial
        )

    def _continue_snapshot(
        self,
        *,
        cursor: str,
        query_fingerprint: str,
        limit: int,
        started: float,
    ) -> IntegrityAnalysisOutput:
        METRICS.record_integrity_cursor_continuation()
        try:
            payload = self._decode_cursor(cursor)
        except GovernanceError as exc:
            METRICS.record_integrity_cursor_event(exc.code.value)
            raise
        snapshot, snapshot_error = self.pagination_snapshots.get(
            str(payload["snapshot_id"])
        )
        if snapshot is None:
            METRICS.record_integrity_cursor_event("stale_cursor")
            raise GovernanceError(
                ErrorCode.STALE_CURSOR,
                details=_details(
                    "cursor", snapshot_error or "snapshot_unavailable"
                ),
            )
        if (
            payload.get("query_fingerprint") != query_fingerprint
            or snapshot.query_fingerprint != query_fingerprint
            or payload.get("evidence_fingerprint")
            != snapshot.evidence_fingerprint
            or payload.get("analysis_timestamp") != snapshot.analysis_timestamp
            or int(payload.get("index_generation", 0))
            != snapshot.index_generation
            or payload.get("index_fingerprint") != snapshot.index_fingerprint
        ):
            METRICS.record_integrity_cursor_event("stale_cursor")
            raise GovernanceError(
                ErrorCode.STALE_CURSOR,
                details=_details("cursor", "query_or_snapshot_binding_changed"),
            )
        active_index = _active_index_identity(self.provider)
        if (
            not active_index
            or not active_index.get("valid")
            or int(active_index.get("generation", 0))
            != snapshot.index_generation
            or str(active_index.get("fingerprint") or "")
            != snapshot.index_fingerprint
        ):
            METRICS.record_integrity_cursor_event("stale_cursor")
            raise GovernanceError(
                ErrorCode.STALE_CURSOR,
                details=_details(
                    "cursor", "active_index_replaced_or_invalidated"
                ),
            )
        offset = int(payload["offset"])
        if offset < 0 or offset > len(snapshot.findings):
            METRICS.record_integrity_cursor_event("invalid_cursor")
            raise GovernanceError(
                ErrorCode.INVALID_CURSOR,
                details=_details("cursor", "offset_out_of_range"),
            )
        effective_limit, clamp_reason = _effective_limit(
            limit, snapshot.detail_level
        )
        page = snapshot.findings[offset : offset + effective_limit]
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
                index_generation=snapshot.index_generation,
                index_fingerprint=snapshot.index_fingerprint,
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
                "evidence_references": evidence,
                "pagination": {
                    "requested_limit": limit,
                    "effective_limit": effective_limit,
                    "maximum_limit": MAX_PAGE_LIMIT,
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
        return IntegrityAnalysisOutput(
            data=data,
            warnings=warnings,
            metadata=copy.deepcopy(snapshot.metadata),
            partial=partial,
        )

    def _encode_cursor(self, **values) -> str:
        payload = json.dumps(
            {
                "snapshot_id": str(values["snapshot_id"]),
                "query_fingerprint": str(values["query_fingerprint"]),
                "evidence_fingerprint": str(values["evidence_fingerprint"]),
                "analysis_timestamp": str(values["analysis_timestamp"]),
                "index_generation": int(values["index_generation"]),
                "index_fingerprint": str(values["index_fingerprint"]),
                "offset": int(values["offset"]),
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
            if (
                base64.urlsafe_b64encode(payload).decode().rstrip("=") != payload_part
                or base64.urlsafe_b64encode(signature).decode().rstrip("=") != signature_part
            ):
                raise ValueError("cursor encoding is not canonical")
            expected = hmac.new(
                self.cursor_key, payload, hashlib.sha256
            ).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("cursor signature mismatch")
            value = json.loads(payload.decode("utf-8"))
            required = {
                "snapshot_id",
                "query_fingerprint",
                "evidence_fingerprint",
                "analysis_timestamp",
                "index_generation",
                "index_fingerprint",
                "offset",
            }
            if not isinstance(value, dict) or not required.issubset(value):
                raise ValueError("cursor fields are missing")
            value["offset"] = int(value["offset"])
            return value
        except Exception as exc:
            raise GovernanceError(
                ErrorCode.INVALID_CURSOR,
                details=_details("cursor", "integrity_or_format_invalid"),
            ) from exc


def _validate_inputs(**values) -> dict[str, Any]:
    operation = "configuration_integrity_analysis"
    raw_sources = values.get("source_types")
    source_types = _validate_enum_array(
        "source_types", raw_sources, SOURCE_TYPES, operation
    )
    raw_findings = values.get("finding_types")
    finding_types = _validate_enum_array(
        "finding_types", raw_findings, FINDING_TYPES, operation
    )
    if not isinstance(values.get("include_orphan_candidates"), bool):
        raise InvalidRequestError(
            details=_details(
                "include_orphan_candidates", "boolean_required"
            )
        )
    detail_level = values.get("detail_level")
    if detail_level not in {item.value for item in DetailLevel}:
        raise InvalidRequestError(
            details={
                **_details("detail_level", "unsupported_value"),
                "value": str(detail_level)[:64],
            }
        )
    try:
        limit = int(values.get("limit"))
    except (TypeError, ValueError) as exc:
        raise InvalidRequestError(
            details=_details("limit", "integer_required")
        ) from exc
    if not 1 <= limit <= MAX_PAGE_LIMIT:
        raise InvalidRequestError(
            details={
                **_details("limit", "range_1_to_100"),
                "value": limit,
            }
        )
    cursor = values.get("cursor")
    if not isinstance(cursor, str):
        raise InvalidRequestError(
            details=_details("cursor", "opaque_string_required")
        )
    if cursor and bool(values.get("refresh_index")):
        raise InvalidRequestError(
            details=_details(
                "refresh_index", "first_page_only_when_cursor_absent"
            )
        )
    return {
        "source_types": source_types,
        "finding_types": finding_types,
        "include_orphan_candidates": values["include_orphan_candidates"],
        "detail_level": detail_level,
        "limit": limit,
        "cursor": cursor,
        "refresh_index": bool(values.get("refresh_index")),
    }


def _validate_enum_array(field, value, supported, operation):
    if value is None or value == []:
        return list(supported)
    if not isinstance(value, (list, tuple)):
        raise InvalidRequestError(
            details={"field": field, "reason": "array_required", "operation": operation}
        )
    if len(value) > len(supported):
        raise InvalidRequestError(
            details={
                "field": field,
                "reason": "too_many_values",
                "operation": operation,
            }
        )
    for item in value:
        if not isinstance(item, str) or item not in supported:
            raise InvalidRequestError(
                details={
                    "field": field,
                    "reason": "unsupported_value",
                    "value": str(item)[:64],
                    "operation": operation,
                }
            )
    return list(dict.fromkeys(value))


def _details(field: str, reason: str) -> dict[str, str]:
    return {
        "field": field,
        "reason": reason,
        "operation": "configuration_integrity_analysis",
    }


def _query_fingerprint(*parts) -> str:
    return hashlib.sha256(
        json.dumps(
            parts, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    ).hexdigest()


def _effective_limit(requested: int, detail_level: str) -> tuple[int, str | None]:
    maximum = DETAIL_RESULT_CAPS[detail_level]
    effective = min(max(1, int(requested)), MAX_PAGE_LIMIT, maximum)
    return effective, "detail_level_payload_cap" if requested > maximum else None


def _analysis_timestamp(clock) -> str:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise GovernanceError(ErrorCode.INTERNAL_SERVER_ERROR)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _active_index_identity(provider) -> dict[str, object] | None:
    reader = getattr(provider, "active_index_identity", None)
    if not callable(reader):
        return None
    try:
        value = reader()
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _dynamic_review_items(bundle: IntegrityEvidenceBundle) -> list[dict[str, Any]]:
    return [
        {
            "source_type": str(item.get("source_type") or "unknown")[:64],
            "source_id": str(item.get("source_id") or "unknown")[:128],
            **(
                {"source_entity_id": str(item["source_entity_id"])[:128]}
                if item.get("source_entity_id")
                else {}
            ),
            **(
                {"source_name": str(item["source_name"])[:160]}
                if item.get("source_name")
                else {}
            ),
            "configuration_path": str(
                item.get("config_path") or "unknown"
            )[:256],
            "confidence": "limited",
            "manual_review_required": True,
        }
        for item in bundle.dynamic_references[:100]
    ]


def _timing_details(started: float, *, bundle: IntegrityEvidenceBundle | None):
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
            float(bundle.evidence_collection_duration_ms if bundle else 0.0),
            3,
        ),
        "home_assistant_cumulative_attempt_ms": (
            round(telemetry.ha_duration_ms, 3) if telemetry else 0.0
        ),
        "home_assistant_wall_clock_span_ms": (
            telemetry.ha_wall_clock_span_ms if telemetry else 0.0
        ),
        "home_assistant_request_count": (
            telemetry.ha_request_count if telemetry else 0
        ),
        "maximum_concurrent_home_assistant_requests": (
            telemetry.ha_max_concurrent_requests if telemetry else 0
        ),
        "upstream_attempted": bool(telemetry and telemetry.ha_request_count > 0),
    }


def _limitations() -> list[str]:
    return [
        "No registry candidate is automatically safe to delete.",
        "Registry absence from the state machine is not sufficient proof of obsolescence.",
        "Unsupported or unrequested configuration sources may still consume an entity.",
        "External systems and integrations may reference entity IDs outside inspected Home Assistant configuration.",
        "Dynamic templates may conceal active use and never produce an invented target entity ID.",
        "User-disabled and integration-disabled entities may be intentionally retained.",
        "Home Assistant may recreate an entity after an integration reload.",
        "Static YAML, packages, and unsupported custom integration configuration are not claimed as inspected.",
        "This operation is read-only and never generates deletion commands or cleanup plans.",
    ]


def _set_audit_summary(**values) -> None:
    telemetry = current_telemetry()
    if telemetry is not None:
        telemetry.audit_context = {
            "assessment": str(values["assessment"])[:64],
            "finding_count": max(0, int(values["finding_count"])),
            "unique_source_object_count": max(
                0, int(values["unique_source_object_count"])
            ),
            "unique_target_entity_count": max(
                0, int(values["unique_target_entity_count"])
            ),
            "orphan_candidate_count": max(
                0, int(values["orphan_candidate_count"])
            ),
            "unresolved_dynamic_reference_count": max(
                0, int(values["unresolved_dynamic_reference_count"])
            ),
            "coverage_complete": bool(values["coverage_complete"]),
        }
