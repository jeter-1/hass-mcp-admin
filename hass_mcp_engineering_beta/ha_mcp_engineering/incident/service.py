"""Facilitator service for bounded, read-only incident correlation."""

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
import re
import secrets
import time
from typing import Any
import uuid

from ..dependency.extraction import valid_entity_id
from ..errors import ErrorCode, GovernanceError, InvalidRequestError
from ..facilitation import DetailLevel
from ..observability import METRICS
from ..providers import EvidenceRequest, ProviderCapability, ProviderFailureCategory
from ..request_context import current_telemetry
from .models import (
    CAUSAL_STATUSES,
    CONFIDENCE_LEVELS,
    EVENT_TYPES,
    FINAL_ASSESSMENTS,
    SEVERITIES,
    IncidentAnalysisOutput,
    IncidentEvidenceBundle,
    stable_id,
)
from .rules import correlate


MAX_PAGE_LIMIT = 100
MAX_RELATED_ENTITIES = 20
DETAIL_RESULT_CAPS = {"summary": 50, "standard": 30, "evidence": 20}
PAGINATION_SNAPSHOT_TTL_SECONDS = 300.0
MAX_PAGINATION_SNAPSHOTS = 16
AUTOMATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@dataclass
class _IncidentSnapshot:
    expires_at: float
    query_fingerprint: str
    evidence_fingerprint: str
    index_requested: bool
    index_generation: int
    index_fingerprint: str
    analysis_timestamp: str
    incident_id: str
    detail_level: str
    data_base: dict[str, Any]
    hypotheses: tuple[dict[str, Any], ...]
    evidence_by_id: dict[str, dict[str, Any]]
    events: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    metadata: dict[str, Any]
    source_partial: bool


class _IncidentSnapshotStore:
    def __init__(self):
        self._values: OrderedDict[str, _IncidentSnapshot] = OrderedDict()

    def put(self, value: _IncidentSnapshot) -> str:
        self._purge()
        snapshot_id = uuid.uuid4().hex
        self._values[snapshot_id] = value
        while len(self._values) > MAX_PAGINATION_SNAPSHOTS:
            self._values.popitem(last=False)
        return snapshot_id

    def get(self, snapshot_id: str) -> tuple[_IncidentSnapshot | None, str | None]:
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
        for key in [key for key, value in self._values.items() if value.expires_at <= now]:
            self._values.pop(key, None)


class IncidentCorrelationService:
    def __init__(self, provider, *, timeout_seconds: float = 60.0, clock=None, cursor_key: bytes | None = None):
        self.provider = provider
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 120.0))
        self.clock = clock or _utc_now
        self.cursor_key = cursor_key or secrets.token_bytes(32)
        self.pagination_snapshots = _IncidentSnapshotStore()

    async def analyze(self, **values) -> IncidentAnalysisOutput:
        started = time.perf_counter()
        METRICS.record_incident_request()
        cursor = values.get("cursor") if isinstance(values.get("cursor"), str) else ""
        try:
            validated = _validate_inputs(**values)
        except InvalidRequestError:
            if cursor:
                METRICS.record_incident_cursor_continuation()
                METRICS.record_incident_cursor_event("invalid_cursor")
            else:
                METRICS.record_incident_failure("request_validation")
            raise

        query_fingerprint = _query_fingerprint(validated)
        if validated["cursor"]:
            return self._continue_snapshot(
                cursor=validated["cursor"],
                query_fingerprint=query_fingerprint,
                limit=validated["limit"],
                started=started,
            )

        analysis_timestamp = _analysis_timestamp(self.clock)
        try:
            result = await asyncio.wait_for(
                self.provider.fetch(
                    EvidenceRequest(
                        capability=ProviderCapability.INCIDENT_CORRELATION,
                        query={**validated, "analysis_timestamp": analysis_timestamp},
                        max_evidence=MAX_PAGE_LIMIT,
                        detail_level=DetailLevel(validated["detail_level"]),
                    )
                ),
                timeout=self.timeout_seconds,
            )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            METRICS.record_provider_result("engineering", "failed")
            METRICS.record_incident_failure("provider_timeout")
            raise GovernanceError(ErrorCode.PROVIDER_TIMEOUT) from exc

        if not result.succeeded or not isinstance(result.data, IncidentEvidenceBundle):
            METRICS.record_provider_result(result.provider_id, result.completeness.value)
            category = result.failure.category.value if result.failure else "provider_error"
            METRICS.record_incident_failure(category)
            code = ErrorCode.PROVIDER_TIMEOUT if result.failure and result.failure.category == ProviderFailureCategory.TIMEOUT else ErrorCode.ANALYSIS_UNAVAILABLE
            raise GovernanceError(code)

        bundle = result.data
        METRICS.record_provider_result(result.provider_id, result.completeness.value)
        missing_evidence, coverage_limitations = _evidence_coverage_semantics(bundle)
        hypotheses, cluster_count = correlate(
            bundle.events,
            correlation_window_minutes=validated["correlation_window_minutes"],
            missing_evidence=missing_evidence,
            coverage_limitations=coverage_limitations,
        )
        evidence_fingerprint = bundle.evidence_fingerprint()
        incident_id = stable_id("incident", analysis_timestamp, query_fingerprint, evidence_fingerprint)
        confidence_counts = Counter(item.confidence for item in hypotheses)
        severity_counts = Counter(item.severity for item in hypotheses)
        causal_counts = Counter(item.causal_status for item in hypotheses)
        event_counts = Counter(item.event_type for item in bundle.events)
        unique_entities = {item.entity_id for item in bundle.events if item.entity_id}
        unique_automations = {item.automation_id for item in bundle.events if item.automation_id}
        manual_review = any(item.manual_review_required for item in hypotheses)
        final_assessment = _final_assessment(hypotheses, bundle.source_partial)
        effective_limit, clamp_reason = _effective_limit(validated["limit"], validated["detail_level"])
        page = hypotheses[:effective_limit]
        has_more = len(page) < len(hypotheses)
        source_partial = bundle.source_partial or bundle.evidence_truncated or bundle.timeline_truncated
        partial = source_partial or has_more
        coverage = [item.public() for item in bundle.coverage]
        page_references = {ref for item in page for ref in (*item.supporting_evidence_reference_ids, *item.contradicting_evidence_reference_ids)}
        public_evidence = [bundle.evidence[ref].public() for ref in sorted(page_references) if ref in bundle.evidence][:100]
        public_events = []
        if validated["detail_level"] == "evidence":
            public_events = [item.public() for item in bundle.events if page_references.intersection(item.evidence_reference_ids)][:100]
        warnings = list(dict.fromkeys([*result.warnings, *[warning for item in bundle.coverage for warning in item.warnings]]))[:20]
        snapshot_warnings = tuple(warnings)
        if has_more:
            warnings.append("Hypotheses were paginated; continue with the returned cursor.")
        if bundle.evidence_truncated:
            warnings.append("Bounded evidence-reference retention was reached.")
            METRICS.record_incident_evidence_truncation()
        if bundle.timeline_truncated:
            warnings.append("Bounded normalized-event retention was reached.")
            METRICS.record_incident_timeline_truncation()

        data = {
            "analysis_timestamp": analysis_timestamp,
            "incident_id": incident_id,
            "final_assessment": final_assessment,
            "result_status": "partial" if partial else "success",
            "time_window": {
                "lookback_hours": validated["lookback_hours"],
                "correlation_window_minutes": validated["correlation_window_minutes"],
                "relationship_bands": {"immediate_seconds": 30, "near_seconds": 120, "contextual_max_minutes": validated["correlation_window_minutes"]},
            },
            "focus": bundle.focus,
            "hypothesis_count": len(hypotheses),
            "hypotheses_by_confidence": {key: confidence_counts.get(key, 0) for key in CONFIDENCE_LEVELS},
            "hypotheses_by_severity": {key: severity_counts.get(key, 0) for key in SEVERITIES},
            "hypotheses_by_causal_status": {key: causal_counts.get(key, 0) for key in CAUSAL_STATUSES},
            "correlated_event_count": len(bundle.events),
            "events_by_type": {key: event_counts.get(key, 0) for key in EVENT_TYPES},
            "unique_entity_count": len(unique_entities),
            "unique_automation_count": len(unique_automations),
            "manual_review_required": manual_review,
            "hypotheses": [item.public() for item in page],
            "timeline_summary": _timeline_summary(bundle.events, cluster_count),
            "normalized_events": public_events,
            "evidence_references": public_evidence,
            "source_coverage_matrix": coverage,
            "counter_semantics": {
                "request_count": "first_pages_plus_cursor_continuations",
                "terminal_counts": "new_analyses_only",
                "hypothesis_and_event_aggregates": "whole_new_analysis_once",
                "unique_counts": "sum_of_per_analysis_unique_values",
                "cursor_failures_are_failed_new_analyses": False,
                "source_failures": "actual_failed_sources_or_source_operations_only",
                "coverage_limitations": "successful_but_incomplete_or_unsupported_evidence",
            },
            "pagination": {
                "requested_limit": validated["limit"], "effective_limit": effective_limit,
                "maximum_limit": MAX_PAGE_LIMIT, "effective_payload_cap": DETAIL_RESULT_CAPS[validated["detail_level"]],
                "clamped": effective_limit != validated["limit"], "clamp_reason": clamp_reason,
                "returned": len(page), "total": len(hypotheses), "has_more": has_more, "next_cursor": None,
                "snapshot_ttl_seconds": int(PAGINATION_SNAPSHOT_TTL_SECONDS),
                "snapshot_is_result_cache": False,
            },
            "index_and_cache_provenance": {**bundle.index, "pagination_snapshot_is_result_cache": False, "general_result_cache_supported": False},
            "timing_details": _timing_details(started, bundle),
            "explicit_limitations": _limitations(),
        }
        metadata = {
            "routing": {
                "lifecycle_status": "beta_native", "classification": "engineering_native", "provider": "engineering",
                "policy": "bounded_incident_correlation_read", "access": "read", "fallback_occurred": False,
                "standard_ha_mcp_coverage": "unavailable",
            },
            "source_coverage": coverage,
        }

        if has_more:
            data_base = copy.deepcopy(data)
            for key in ("hypotheses", "normalized_events", "evidence_references", "pagination", "timing_details"):
                data_base.pop(key, None)
            index_requested = bool(bundle.index.get("requested"))
            index_generation = int(bundle.index.get("generation") or 0)
            index_fingerprint = str(bundle.index.get("fingerprint") or "")
            if index_requested and not _active_index_matches(self.provider, index_generation, index_fingerprint):
                METRICS.record_incident_failure("index_changed_before_snapshot_commit")
                raise GovernanceError(ErrorCode.ANALYSIS_UNAVAILABLE, details=_details("cursor", "index_changed_before_snapshot_commit"))
            snapshot_id = self.pagination_snapshots.put(_IncidentSnapshot(
                expires_at=time.monotonic() + PAGINATION_SNAPSHOT_TTL_SECONDS,
                query_fingerprint=query_fingerprint, evidence_fingerprint=evidence_fingerprint,
                index_requested=index_requested, index_generation=index_generation, index_fingerprint=index_fingerprint,
                analysis_timestamp=analysis_timestamp, incident_id=incident_id, detail_level=validated["detail_level"],
                data_base=data_base, hypotheses=tuple(item.public() for item in hypotheses),
                evidence_by_id={key: item.public() for key, item in bundle.evidence.items()},
                events=tuple(item.public() for item in bundle.events), warnings=snapshot_warnings,
                metadata=copy.deepcopy(metadata), source_partial=source_partial,
            ))
            data["pagination"]["next_cursor"] = self._encode_cursor(
                snapshot_id=snapshot_id, query_fingerprint=query_fingerprint, evidence_fingerprint=evidence_fingerprint,
                analysis_timestamp=analysis_timestamp, incident_id=incident_id, index_requested=index_requested,
                index_generation=index_generation, index_fingerprint=index_fingerprint, offset=len(page),
            )

        source_failure_count = _source_failure_count(bundle.coverage)
        METRICS.record_incident_terminal(
            partial=partial, hypothesis_count=len(hypotheses), confidence_counts=confidence_counts,
            severity_counts=severity_counts, causal_counts=causal_counts, correlated_event_count=len(bundle.events),
            event_counts=event_counts, unique_entity_count=len(unique_entities), unique_automation_count=len(unique_automations),
            manual_review_required=manual_review, source_failures=source_failure_count,
            index_cache_hit=bool(bundle.index.get("requested") and bundle.index.get("cache_hit")),
            index_requested=bool(bundle.index.get("requested")), analysis_timestamp=analysis_timestamp,
        )
        _set_audit_summary(
            final_assessment,
            len(hypotheses),
            len(bundle.events),
            not source_partial,
            "partial" if partial else "success",
            source_failure_count=source_failure_count,
            coverage_limitation_count=len(coverage_limitations),
        )
        return IncidentAnalysisOutput(data=data, warnings=warnings, metadata=metadata, partial=partial)

    def _continue_snapshot(self, *, cursor: str, query_fingerprint: str, limit: int, started: float) -> IncidentAnalysisOutput:
        METRICS.record_incident_cursor_continuation()
        try:
            payload = self._decode_cursor(cursor)
        except GovernanceError as exc:
            METRICS.record_incident_cursor_event(exc.code.value)
            raise
        snapshot, snapshot_error = self.pagination_snapshots.get(str(payload["snapshot_id"]))
        if snapshot is None:
            METRICS.record_incident_cursor_event("stale_cursor")
            raise GovernanceError(ErrorCode.STALE_CURSOR, details=_details("cursor", snapshot_error or "snapshot_unavailable"))
        if (
            payload.get("query_fingerprint") != query_fingerprint
            or snapshot.query_fingerprint != query_fingerprint
            or payload.get("evidence_fingerprint") != snapshot.evidence_fingerprint
            or payload.get("analysis_timestamp") != snapshot.analysis_timestamp
            or payload.get("incident_id") != snapshot.incident_id
            or bool(payload.get("index_requested")) != snapshot.index_requested
            or int(payload.get("index_generation", 0)) != snapshot.index_generation
            or payload.get("index_fingerprint") != snapshot.index_fingerprint
        ):
            METRICS.record_incident_cursor_event("stale_cursor")
            raise GovernanceError(ErrorCode.STALE_CURSOR, details=_details("cursor", "query_or_snapshot_binding_changed"))
        if snapshot.index_requested and not _active_index_matches(self.provider, snapshot.index_generation, snapshot.index_fingerprint):
            METRICS.record_incident_cursor_event("stale_cursor")
            raise GovernanceError(ErrorCode.STALE_CURSOR, details=_details("cursor", "active_index_replaced_or_invalidated"))
        offset = int(payload["offset"])
        if offset < 0 or offset > len(snapshot.hypotheses):
            METRICS.record_incident_cursor_event("invalid_cursor")
            raise GovernanceError(ErrorCode.INVALID_CURSOR, details=_details("cursor", "offset_out_of_range"))
        effective_limit, clamp_reason = _effective_limit(limit, snapshot.detail_level)
        page = snapshot.hypotheses[offset : offset + effective_limit]
        refs = {ref for item in page for key in ("supporting_evidence_reference_ids", "contradicting_evidence_reference_ids") for ref in item.get(key, ())}
        evidence = [snapshot.evidence_by_id[ref] for ref in sorted(refs) if ref in snapshot.evidence_by_id][:100]
        events = [item for item in snapshot.events if refs.intersection(item.get("evidence_reference_ids", ()))][:100] if snapshot.detail_level == "evidence" else []
        next_offset = offset + len(page)
        has_more = next_offset < len(snapshot.hypotheses)
        next_cursor = self._encode_cursor(
            snapshot_id=str(payload["snapshot_id"]), query_fingerprint=query_fingerprint,
            evidence_fingerprint=snapshot.evidence_fingerprint, analysis_timestamp=snapshot.analysis_timestamp,
            incident_id=snapshot.incident_id, index_requested=snapshot.index_requested,
            index_generation=snapshot.index_generation, index_fingerprint=snapshot.index_fingerprint,
            offset=next_offset,
        ) if has_more else None
        if not has_more:
            self.pagination_snapshots.remove(str(payload["snapshot_id"]))
        partial = snapshot.source_partial or has_more
        warnings = list(snapshot.warnings)
        if has_more:
            warnings.append("Hypotheses were paginated; continue with the returned cursor.")
        data = copy.deepcopy(snapshot.data_base)
        data.update({
            "result_status": "partial" if partial else "success",
            "hypotheses": list(page), "normalized_events": events, "evidence_references": evidence,
            "pagination": {
                "requested_limit": limit, "effective_limit": effective_limit, "maximum_limit": MAX_PAGE_LIMIT,
                "effective_payload_cap": DETAIL_RESULT_CAPS[snapshot.detail_level], "clamped": limit != effective_limit,
                "clamp_reason": clamp_reason, "returned": len(page), "total": len(snapshot.hypotheses),
                "has_more": has_more, "next_cursor": next_cursor, "source": "bounded_sanitized_pagination_snapshot",
                "snapshot_ttl_seconds": int(PAGINATION_SNAPSHOT_TTL_SECONDS), "snapshot_is_result_cache": False,
            },
            "timing_details": _timing_details(started, None),
        })
        return IncidentAnalysisOutput(data=data, warnings=warnings, metadata=copy.deepcopy(snapshot.metadata), partial=partial)

    def _encode_cursor(self, **values) -> str:
        payload = json.dumps({
            "snapshot_id": str(values["snapshot_id"]), "query_fingerprint": str(values["query_fingerprint"]),
            "evidence_fingerprint": str(values["evidence_fingerprint"]), "analysis_timestamp": str(values["analysis_timestamp"]),
            "incident_id": str(values["incident_id"]), "index_requested": bool(values["index_requested"]),
            "index_generation": int(values["index_generation"]), "index_fingerprint": str(values["index_fingerprint"]),
            "offset": int(values["offset"]),
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(self.cursor_key, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + base64.urlsafe_b64encode(signature).decode().rstrip("=")

    def _decode_cursor(self, cursor: str) -> dict[str, Any]:
        try:
            payload_part, signature_part = cursor.split(".", 1)
            payload = base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4))
            signature = base64.urlsafe_b64decode(signature_part + "=" * (-len(signature_part) % 4))
            if (
                base64.urlsafe_b64encode(payload).decode().rstrip("=") != payload_part
                or base64.urlsafe_b64encode(signature).decode().rstrip("=") != signature_part
            ):
                raise ValueError("cursor encoding is not canonical")
            if not hmac.compare_digest(signature, hmac.new(self.cursor_key, payload, hashlib.sha256).digest()):
                raise ValueError("cursor signature mismatch")
            value = json.loads(payload.decode("utf-8"))
            required = {"snapshot_id", "query_fingerprint", "evidence_fingerprint", "analysis_timestamp", "incident_id", "index_requested", "index_generation", "index_fingerprint", "offset"}
            if not isinstance(value, dict) or not required.issubset(value):
                raise ValueError("cursor fields are missing")
            value["offset"] = int(value["offset"])
            return value
        except Exception as exc:
            raise GovernanceError(ErrorCode.INVALID_CURSOR, details=_details("cursor", "integrity_or_format_invalid")) from exc


def _validate_inputs(**values) -> dict[str, Any]:
    focus = str(values.get("focus_entity_id") or "").strip()
    automation_id = str(values.get("automation_id") or "").strip()
    cursor = values.get("cursor")
    if not isinstance(cursor, str):
        raise InvalidRequestError(details=_details("cursor", "opaque_string_required"))
    if not focus and not automation_id:
        raise InvalidRequestError(details=_details("focus_entity_id", "focus_entity_or_automation_required"))
    if focus and (focus != focus.lower() or not valid_entity_id(focus)):
        raise InvalidRequestError(details=_details("focus_entity_id", "canonical_entity_id_required"))
    if automation_id and not AUTOMATION_ID_PATTERN.fullmatch(automation_id):
        raise InvalidRequestError(details=_details("automation_id", "internal_automation_id_required"))
    related = values.get("related_entity_ids")
    if related is None:
        related = []
    if not isinstance(related, (list, tuple)):
        raise InvalidRequestError(details=_details("related_entity_ids", "array_required"))
    if len(related) > MAX_RELATED_ENTITIES:
        raise InvalidRequestError(details=_details("related_entity_ids", "maximum_20"))
    normalized = []
    for item in related:
        if not isinstance(item, str) or not item or item != item.lower() or not valid_entity_id(item):
            raise InvalidRequestError(details=_details("related_entity_ids", "canonical_nonempty_entity_ids_required"))
        if item == focus or item in normalized:
            raise InvalidRequestError(details=_details("related_entity_ids", "duplicates_not_allowed"))
        normalized.append(item)
    bounded = {
        "lookback_hours": (1, 168), "correlation_window_minutes": (1, 60),
        "trace_limit": (1, 50), "limit": (1, 100),
    }
    numbers = {}
    for field, (minimum, maximum) in bounded.items():
        try:
            value = int(values.get(field))
        except (TypeError, ValueError) as exc:
            raise InvalidRequestError(details=_details(field, "integer_required")) from exc
        if not minimum <= value <= maximum:
            raise InvalidRequestError(details=_details(field, f"range_{minimum}_to_{maximum}"))
        numbers[field] = value
    detail = values.get("detail_level")
    if detail not in {item.value for item in DetailLevel}:
        raise InvalidRequestError(details=_details("detail_level", "unsupported_value"))
    flags = {}
    for field in ("include_dependency_context", "include_integrity_context", "include_reliability_context", "refresh_index"):
        if not isinstance(values.get(field), bool):
            raise InvalidRequestError(details=_details(field, "boolean_required"))
        flags[field] = values[field]
    if cursor and flags["refresh_index"]:
        raise InvalidRequestError(details=_details("refresh_index", "first_page_only_when_cursor_absent"))
    return {
        "focus_entity_id": focus, "automation_id": automation_id, "related_entity_ids": normalized,
        **numbers, **flags, "detail_level": detail, "cursor": cursor,
    }


def _details(field: str, reason: str) -> dict[str, str]:
    return {"field": field, "reason": reason, "operation": "incident_correlation"}


def _query_fingerprint(values: dict[str, Any]) -> str:
    payload = {key: value for key, value in values.items() if key not in {"cursor", "limit", "refresh_index"}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _effective_limit(requested: int, detail_level: str):
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


def _active_index_matches(provider, generation, fingerprint) -> bool:
    try:
        identity = provider.active_index_identity()
        return bool(identity.get("valid") and int(identity.get("generation", 0)) == generation and str(identity.get("fingerprint") or "") == fingerprint)
    except Exception:
        return False


def _timeline_summary(events, cluster_count):
    timestamps = sorted(item.timestamp for item in events if item.timestamp)
    return {
        "earliest_evidence_timestamp": timestamps[0] if timestamps else None,
        "latest_evidence_timestamp": timestamps[-1] if timestamps else None,
        "normalized_event_count": len(events), "incident_cluster_count": cluster_count,
        "major_event_types": sorted({item.event_type for item in events})[:20],
        "evidence_contains_gaps": any(item.timestamp is None for item in events),
        "timestamps_missing": sum(item.timestamp is None for item in events),
        "clock_order_ambiguity": any(item.timestamp is None for item in events),
        "clock_skew_tolerance_seconds": 0,
    }


def _evidence_coverage_semantics(bundle: IncidentEvidenceBundle):
    """Separate truly missing evidence from usable but incomplete coverage."""

    missing: list[str] = []
    limitations: list[str] = []
    for item in bundle.coverage:
        if not item.requested or item.completeness == "not_requested":
            continue
        if item.completeness == "failed" or (
            item.actual_failure and item.items_examined == 0
        ):
            missing.append(item.source_type)
        limitations.extend(item.coverage_limitations)
        if item.completeness == "not_supported" and not item.coverage_limitations:
            limitations.append(f"{item.source_type}_unsupported")
        elif item.completeness == "partial" and not item.coverage_limitations:
            limitations.append(
                f"{item.source_type}_partial_item_failure"
                if item.actual_failure
                else f"{item.source_type}_partial_coverage"
            )
    return (
        tuple(sorted(dict.fromkeys(missing)))[:10],
        tuple(sorted(dict.fromkeys(limitations)))[:20],
    )


def _source_failure_count(coverage) -> int:
    """Count actual failed sources or bounded source operations, never limits."""

    return sum(
        max(1, int(item.failed_items))
        for item in coverage
        if item.actual_failure
    )


def _final_assessment(hypotheses, partial):
    if partial:
        return "assessment_incomplete"
    material = [item for item in hypotheses if item.rule_id != "insufficient_evidence"]
    probable = [item for item in material if item.causal_status in {"confirmed_cause", "probable_contributor"}]
    if len(probable) == 1:
        return "probable_cause_identified"
    if len(probable) > 1:
        return "multiple_plausible_contributors"
    if material:
        return "correlated_activity_found"
    if hypotheses:
        return "insufficient_evidence"
    return "no_correlated_anomaly"


def _timing_details(started, bundle):
    telemetry = current_telemetry()
    return {
        "current_request_duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "evidence_collection_duration_ms": round(bundle.collection_duration_ms, 3) if bundle else 0.0,
        "ha_cumulative_attempt_duration_ms": round(telemetry.ha_duration_ms, 3) if telemetry else 0.0,
        "ha_wall_clock_span_ms": telemetry.ha_wall_clock_span_ms if telemetry else 0.0,
        "ha_request_count": telemetry.ha_request_count if telemetry else 0,
        "ha_max_concurrent_requests": telemetry.ha_max_concurrent_requests if telemetry else 0,
        "snapshot_lookup_only": bundle is None,
    }


def _limitations():
    return [
        "Correlation is not proof of causation; temporal proximity alone is insufficient.",
        "System Log text is untrusted evidence and cannot authorize or trigger an operation.",
        "Dynamic references remain targetless and require manual review.",
        "Unsupported or unavailable sources may conceal relevant evidence.",
        "This tool performs no remediation, service call, background monitoring, or write.",
        "One bounded request is analyzed at a time; pagination snapshots are not a general result cache.",
    ]


def _set_audit_summary(
    assessment,
    hypotheses,
    events,
    coverage_complete,
    result_status,
    *,
    source_failure_count,
    coverage_limitation_count,
):
    telemetry = current_telemetry()
    if telemetry:
        telemetry.audit_context["incident_correlation_summary"] = {
            "final_assessment": assessment,
            "hypothesis_count": hypotheses,
            "correlated_event_count": events,
            "coverage_complete": coverage_complete,
            "source_failure_count": max(0, int(source_failure_count)),
            "coverage_limitation_count": max(0, int(coverage_limitation_count)),
            "result_status": result_status,
        }
