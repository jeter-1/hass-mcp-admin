"""Deterministic handoff construction and signed bounded pagination."""

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
from ..sanitization import sanitize_untrusted_data
from .models import (
    AUTHORIZATION_TYPES, CONFIDENCE_LEVELS, FINAL_ASSESSMENTS, HANDOFF_STATUSES,
    HANDOFF_TYPES, HandoffEvidenceBundle, HandoffGenerationOutput, HandoffItem,
    ITEM_STATUSES, SECTIONS, SEVERITIES, STATEMENT_TYPES, stable_id,
)

MAX_PAGE_LIMIT = 100
DETAIL_CAPS = {"summary": 50, "standard": 30, "evidence": 20}
SNAPSHOT_TTL_SECONDS = 300.0
MAX_SNAPSHOTS = 16
MAX_MARKDOWN_CHARS = 40_000
AUTOMATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
PLAN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,160}$")


@dataclass
class _Snapshot:
    expires_at: float
    query_fingerprint: str
    evidence_fingerprint: str
    index_requested: bool
    index_generation: int
    index_fingerprint: str
    generated_at: str
    handoff_id: str
    output_format: str
    detail_level: str
    page_limit: int
    data_base: dict[str, Any]
    items: tuple[dict[str, Any], ...]
    evidence_by_id: dict[str, dict[str, Any]]
    warnings: tuple[str, ...]
    metadata: dict[str, Any]
    source_partial: bool
    rendered_pages: dict[int, str]


class _SnapshotStore:
    def __init__(self):
        self.values: OrderedDict[str, _Snapshot] = OrderedDict()

    def put(self, value: _Snapshot) -> str:
        self._purge()
        key = uuid.uuid4().hex
        self.values[key] = value
        while len(self.values) > MAX_SNAPSHOTS:
            self.values.popitem(last=False)
        return key

    def get(self, key: str):
        value = self.values.get(key)
        if value is None:
            self._purge()
            return None, "snapshot_unavailable"
        if value.expires_at <= time.monotonic():
            self.values.pop(key, None)
            self._purge()
            return None, "snapshot_expired"
        self.values.move_to_end(key)
        return value, None

    def remove(self, key: str):
        self.values.pop(key, None)

    def _purge(self):
        now = time.monotonic()
        for key in [key for key, value in self.values.items() if value.expires_at <= now]:
            self.values.pop(key, None)


class HandoffGenerationService:
    def __init__(self, provider, *, timeout_seconds=60.0, clock=None, cursor_key=None):
        self.provider = provider
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 120.0))
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.cursor_key = cursor_key or secrets.token_bytes(32)
        self.snapshots = _SnapshotStore()

    async def generate(self, **values) -> HandoffGenerationOutput:
        started = time.perf_counter()
        METRICS.record_handoff_request()
        cursor = values.get("cursor") if isinstance(values.get("cursor"), str) else ""
        try:
            query = _validate(values)
        except InvalidRequestError:
            if cursor:
                METRICS.record_handoff_cursor_continuation()
                METRICS.record_handoff_cursor_event("invalid_cursor")
            else:
                METRICS.record_handoff_failure("request_validation")
            raise
        fingerprint = _query_fingerprint(query)
        if query["cursor"]:
            return self._continue(query["cursor"], fingerprint, started)

        generated_at = _timestamp(self.clock)
        try:
            result = await asyncio.wait_for(
                self.provider.fetch(EvidenceRequest(
                    ProviderCapability.HANDOFF_GENERATION,
                    {**query, "generated_at": generated_at},
                    max_evidence=MAX_PAGE_LIMIT,
                    detail_level=DetailLevel(query["detail_level"]),
                )),
                timeout=self.timeout_seconds,
            )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            METRICS.record_provider_result("engineering", "failed")
            METRICS.record_handoff_failure("provider_timeout")
            raise GovernanceError(ErrorCode.PROVIDER_TIMEOUT) from exc
        if not result.succeeded or not isinstance(result.data, HandoffEvidenceBundle):
            METRICS.record_provider_result(result.provider_id, result.completeness.value)
            category = result.failure.category.value if result.failure else "provider_error"
            METRICS.record_handoff_failure(category)
            code = ErrorCode.PROVIDER_TIMEOUT if result.failure and result.failure.category == ProviderFailureCategory.TIMEOUT else ErrorCode.ANALYSIS_UNAVAILABLE
            raise GovernanceError(code)

        bundle = result.data
        METRICS.record_provider_result(result.provider_id, result.completeness.value)
        items = bundle.items
        evidence_fingerprint = bundle.fingerprint()
        handoff_id = stable_id("handoff", generated_at, fingerprint, evidence_fingerprint)
        title = query["title"] or _default_title(query, bundle)
        safe_title = sanitize_untrusted_data(
            title,
            known_secrets=(getattr(self.provider, "secret", ""), getattr(self.provider, "ha_token", "")),
            max_string=200,
        )
        title = str(safe_title.value) if not safe_title.failed_closed else "Engineering handoff"
        counts = _counts(items)
        source_failures = sum(max(1, int(item.failed_items)) for item in bundle.coverage if item.actual_failure)
        coverage_limitations = sum(bool(item.coverage_limitations) or item.completeness in {"partial", "not_supported"} for item in bundle.coverage if item.requested and not item.actual_failure)
        handoff_status, assessment = _status_and_assessment(query["handoff_type"], items, bundle.source_partial, source_failures, bundle.source_payloads)
        effective_limit = min(query["limit"], DETAIL_CAPS[query["detail_level"]])
        page = items[:effective_limit]
        has_more = len(page) < len(items)
        partial = bundle.source_partial or has_more or bundle.item_truncated
        result_status = "partial" if partial else "success"
        coverage = [item.public() for item in bundle.coverage]
        refs = {ref for item in page for ref in (*item.supporting_evidence_reference_ids, *item.contradicting_evidence_reference_ids)}
        public_evidence = [bundle.evidence[ref].public() for ref in sorted(refs) if ref in bundle.evidence][:100]
        warnings = list(dict.fromkeys([*result.warnings, *[warning for item in bundle.coverage for warning in item.warnings]]))[:20]
        if has_more:
            warnings.append("Handoff items were paginated; continue with the signed cursor.")
        if bundle.item_truncated:
            warnings.append("The bounded handoff item retention limit was reached.")
            METRICS.record_handoff_item_truncation()
        auth_boundaries = _authorization_boundaries(items)
        data = {
            "generated_at": generated_at,
            "handoff_id": handoff_id,
            "title": title,
            "handoff_type": query["handoff_type"],
            "handoff_status": handoff_status,
            "final_assessment": assessment,
            "result_status": result_status,
            "scope": bundle.scope,
            "executive_summary": _executive_summary(query["handoff_type"], handoff_status, assessment, counts, bundle.source_partial, auth_boundaries),
            "current_state_summary": _section_summary(items, "current_state"),
            "completed_work_summary": _section_summary(items, "completed_work"),
            "open_item_count": counts["open_item_count"],
            "risk_count": counts["risk_count"],
            "manual_review_required": counts["manual_review_required"],
            "item_count": len(items),
            "items_by_section": counts["by_section"],
            "items_by_statement_type": counts["by_statement"],
            "items_by_status": counts["by_status"],
            "items_by_severity": counts["by_severity"],
            "handoff_items": [item.public() for item in page],
            "authorization_boundaries": auth_boundaries,
            "source_coverage_matrix": coverage,
            "evidence_references": public_evidence,
            "counter_semantics": {
                "request_count": "first_pages_plus_cursor_continuations",
                "terminal_outcomes_and_item_aggregates": "new_handoffs_only",
                "cursor_failures_are_failed_new_handoffs": False,
                "source_failures": "actual_failed_sources_or_operations_only",
                "coverage_limitations": "successful_but_incomplete_or_unsupported_evidence",
            },
            "pagination": {
                "requested_limit": query["limit"], "effective_limit": effective_limit,
                "maximum_limit": MAX_PAGE_LIMIT, "detail_level_cap": DETAIL_CAPS[query["detail_level"]],
                "returned": len(page), "total": len(items), "has_more": has_more,
                "next_cursor": None, "snapshot_ttl_seconds": int(SNAPSHOT_TTL_SECONDS),
                "snapshot_is_result_cache": False,
            },
            "index_and_cache_provenance": {**bundle.index, "pagination_snapshot_is_result_cache": False, "general_result_cache_supported": False},
            "timing_details": _timing(started, bundle),
            "explicit_limitations": _limitations(),
        }
        if query["output_format"] in {"markdown", "both"}:
            data["rendered_markdown"] = _markdown(data)
        metadata = {
            "routing": {
                "lifecycle_status": "beta_native", "classification": "engineering_native",
                "provider": "engineering", "policy": "bounded_handoff_generation_read",
                "access": "read", "fallback_occurred": False,
                "standard_ha_mcp_coverage": "unavailable",
            },
            "source_coverage": coverage,
        }

        if has_more:
            index_requested = bool(bundle.index.get("requested"))
            index_generation = int(bundle.index.get("generation") or 0)
            index_fingerprint = str(bundle.index.get("fingerprint") or "")
            if index_requested and not _active_index_matches(self.provider, index_generation, index_fingerprint):
                METRICS.record_handoff_failure("index_changed_before_snapshot_commit")
                raise GovernanceError(ErrorCode.ANALYSIS_UNAVAILABLE)
            data_base = copy.deepcopy(data)
            for key in ("handoff_items", "evidence_references", "pagination", "timing_details", "rendered_markdown"):
                data_base.pop(key, None)
            public_items = tuple(item.public() for item in items)
            rendered_pages = {}
            if query["output_format"] in {"markdown", "both"}:
                for offset in range(0, len(public_items), effective_limit):
                    page_data = copy.deepcopy(data_base)
                    page_data["handoff_items"] = list(public_items[offset:offset + effective_limit])
                    page_refs = {
                        ref
                        for item in page_data["handoff_items"]
                        for key in ("supporting_evidence_reference_ids", "contradicting_evidence_reference_ids")
                        for ref in item.get(key, ())
                    }
                    page_data["evidence_references"] = [
                        bundle.evidence[ref].public()
                        for ref in sorted(page_refs)
                        if ref in bundle.evidence
                    ][:100]
                    page_data["pagination"] = {"returned": len(page_data["handoff_items"]), "total": len(public_items), "has_more": offset + effective_limit < len(public_items)}
                    rendered_pages[offset] = _markdown(page_data)
            snapshot_id = self.snapshots.put(_Snapshot(
                time.monotonic() + SNAPSHOT_TTL_SECONDS, fingerprint, evidence_fingerprint,
                index_requested, index_generation, index_fingerprint, generated_at, handoff_id,
                query["output_format"], query["detail_level"], effective_limit, data_base,
                public_items, {key: value.public() for key, value in bundle.evidence.items()},
                tuple(warnings[:-1] if has_more and warnings else warnings), copy.deepcopy(metadata),
                bundle.source_partial or bundle.item_truncated, rendered_pages,
            ))
            data["pagination"]["next_cursor"] = self._encode(snapshot_id, fingerprint, evidence_fingerprint, generated_at, handoff_id, query["output_format"], index_requested, index_generation, index_fingerprint, len(page))

        METRICS.record_handoff_terminal(
            partial=partial, counts=counts, source_failures=source_failures,
            coverage_limitations=coverage_limitations,
            index_requested=bool(bundle.index.get("requested")),
            index_cache_hit=bool(bundle.index.get("requested") and bundle.index.get("cache_hit")),
            generated_at=generated_at,
        )
        _audit(query, handoff_status, result_status, counts, source_failures, coverage_limitations)
        return HandoffGenerationOutput(data, warnings, metadata, partial)

    def _continue(self, cursor, fingerprint, started):
        METRICS.record_handoff_cursor_continuation()
        try:
            payload = self._decode(cursor)
        except GovernanceError as exc:
            METRICS.record_handoff_cursor_event(exc.code.value)
            raise
        snapshot, reason = self.snapshots.get(str(payload.get("snapshot_id") or ""))
        if snapshot is None:
            METRICS.record_handoff_cursor_event("stale_cursor")
            raise GovernanceError(ErrorCode.STALE_CURSOR, details=_details("cursor", reason or "snapshot_unavailable"))
        bindings = (
            payload.get("query_fingerprint") == fingerprint == snapshot.query_fingerprint,
            payload.get("evidence_fingerprint") == snapshot.evidence_fingerprint,
            payload.get("generated_at") == snapshot.generated_at,
            payload.get("handoff_id") == snapshot.handoff_id,
            payload.get("output_format") == snapshot.output_format,
            bool(payload.get("index_requested")) == snapshot.index_requested,
            int(payload.get("index_generation") or 0) == snapshot.index_generation,
            str(payload.get("index_fingerprint") or "") == snapshot.index_fingerprint,
        )
        if not all(bindings):
            METRICS.record_handoff_cursor_event("invalid_cursor")
            raise GovernanceError(ErrorCode.INVALID_CURSOR, details=_details("cursor", "snapshot_binding_mismatch"))
        if snapshot.index_requested and not _active_index_matches(self.provider, snapshot.index_generation, snapshot.index_fingerprint):
            METRICS.record_handoff_cursor_event("stale_cursor")
            raise GovernanceError(ErrorCode.STALE_CURSOR, details=_details("cursor", "active_index_replaced_or_invalidated"))
        offset = int(payload.get("offset", -1))
        if offset < 0 or offset > len(snapshot.items):
            METRICS.record_handoff_cursor_event("invalid_cursor")
            raise GovernanceError(ErrorCode.INVALID_CURSOR, details=_details("cursor", "offset_out_of_range"))
        page = snapshot.items[offset:offset + snapshot.page_limit]
        next_offset = offset + len(page)
        has_more = next_offset < len(snapshot.items)
        refs = {ref for item in page for key in ("supporting_evidence_reference_ids", "contradicting_evidence_reference_ids") for ref in item.get(key, ())}
        data = copy.deepcopy(snapshot.data_base)
        data["handoff_items"] = list(page)
        data["evidence_references"] = [snapshot.evidence_by_id[ref] for ref in sorted(refs) if ref in snapshot.evidence_by_id][:100]
        data["result_status"] = "partial" if snapshot.source_partial or has_more else "success"
        data["pagination"] = {
            "requested_limit": snapshot.page_limit, "effective_limit": snapshot.page_limit,
            "maximum_limit": MAX_PAGE_LIMIT, "detail_level_cap": DETAIL_CAPS[snapshot.detail_level],
            "returned": len(page), "total": len(snapshot.items), "has_more": has_more,
            "next_cursor": self._encode(str(payload["snapshot_id"]), fingerprint, snapshot.evidence_fingerprint, snapshot.generated_at, snapshot.handoff_id, snapshot.output_format, snapshot.index_requested, snapshot.index_generation, snapshot.index_fingerprint, next_offset) if has_more else None,
            "snapshot_ttl_seconds": int(SNAPSHOT_TTL_SECONDS), "snapshot_is_result_cache": False,
        }
        data["timing_details"] = {
            "current_request_duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "evidence_collection_duration_ms": 0.0, "ha_request_count": 0,
            "snapshot_lookup_only": True, "handoff_regenerated": False,
            "markdown_regenerated": False,
        }
        if snapshot.output_format in {"markdown", "both"}:
            data["rendered_markdown"] = snapshot.rendered_pages.get(offset, "")
        if not has_more:
            self.snapshots.remove(str(payload["snapshot_id"]))
        warnings = list(snapshot.warnings)
        if has_more:
            warnings.append("Handoff items were paginated; continue with the signed cursor.")
        return HandoffGenerationOutput(data, warnings, copy.deepcopy(snapshot.metadata), snapshot.source_partial or has_more)

    def _encode(self, snapshot_id, query_fingerprint, evidence_fingerprint, generated_at, handoff_id, output_format, index_requested, index_generation, index_fingerprint, offset):
        payload = {"v": 1, "snapshot_id": snapshot_id, "query_fingerprint": query_fingerprint, "evidence_fingerprint": evidence_fingerprint, "generated_at": generated_at, "handoff_id": handoff_id, "output_format": output_format, "index_requested": index_requested, "index_generation": index_generation, "index_fingerprint": index_fingerprint, "offset": offset}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(self.cursor_key, raw, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(raw + signature).decode().rstrip("=")

    def _decode(self, cursor):
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            raw = base64.urlsafe_b64decode(padded.encode())
            if len(raw) <= 32:
                raise ValueError
            body, signature = raw[:-32], raw[-32:]
            if not hmac.compare_digest(signature, hmac.new(self.cursor_key, body, hashlib.sha256).digest()):
                raise ValueError
            payload = json.loads(body.decode())
            if not isinstance(payload, dict) or payload.get("v") != 1:
                raise ValueError
            return payload
        except Exception as exc:
            raise GovernanceError(ErrorCode.INVALID_CURSOR, details=_details("cursor", "malformed_or_tampered")) from exc


def _validate(values):
    handoff_type = values.get("handoff_type")
    if handoff_type not in HANDOFF_TYPES:
        raise InvalidRequestError(details=_details("handoff_type", "unsupported_value"))
    title = values.get("title")
    if not isinstance(title, str) or len(title) > 200:
        raise InvalidRequestError(details=_details("title", "maximum_length_200"))
    entities = _list(values.get("focus_entity_ids"), "focus_entity_ids", valid_entity_id)
    automations = _list(values.get("automation_ids"), "automation_ids", lambda v: bool(AUTOMATION_ID_PATTERN.fullmatch(v)))
    plans = _list(values.get("change_plan_ids"), "change_plan_ids", lambda v: bool(PLAN_ID_PATTERN.fullmatch(v)))
    if handoff_type in {"focused_review", "incident"} and not (entities or automations):
        raise InvalidRequestError(details=_details("focus_entity_ids", f"{handoff_type}_requires_entity_or_automation"))
    if handoff_type == "change" and not plans:
        raise InvalidRequestError(details=_details("change_plan_ids", "change_handoff_requires_plan"))
    try:
        lookback = int(values.get("lookback_hours"))
        limit = int(values.get("limit"))
    except (TypeError, ValueError) as exc:
        raise InvalidRequestError(details=_details("lookback_hours", "integer_required")) from exc
    if not 1 <= lookback <= 720:
        raise InvalidRequestError(details=_details("lookback_hours", "range_1_to_720"))
    if not 1 <= limit <= 100:
        raise InvalidRequestError(details=_details("limit", "range_1_to_100"))
    detail = values.get("detail_level")
    if detail not in {"summary", "standard", "evidence"}:
        raise InvalidRequestError(details=_details("detail_level", "unsupported_value"))
    output = values.get("output_format")
    if output not in {"structured", "markdown", "both"}:
        raise InvalidRequestError(details=_details("output_format", "unsupported_value"))
    flags = {}
    for field in ("include_runtime_health", "include_governance_context", "include_dependency_context", "include_integrity_context", "include_reliability_context", "include_incident_context", "include_recommendations", "refresh_index"):
        if not isinstance(values.get(field), bool):
            raise InvalidRequestError(details=_details(field, "boolean_required"))
        flags[field] = values[field]
    cursor = values.get("cursor")
    if not isinstance(cursor, str) or len(cursor) > 8192:
        raise InvalidRequestError(details=_details("cursor", "invalid_value"))
    if cursor and flags["refresh_index"]:
        raise InvalidRequestError(details=_details("refresh_index", "first_page_only_when_cursor_absent"))
    return {"handoff_type": handoff_type, "title": title.strip(), "focus_entity_ids": entities, "automation_ids": automations, "change_plan_ids": plans, "lookback_hours": lookback, **flags, "detail_level": detail, "output_format": output, "limit": limit, "cursor": cursor}


def _list(value, field, validator):
    if not isinstance(value, list):
        raise InvalidRequestError(details=_details(field, "list_required"))
    if len(value) > 20:
        raise InvalidRequestError(details=_details(field, "maximum_items_20"))
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise InvalidRequestError(details=_details(field, "non_empty_string_required"))
        normalized = item.strip().lower() if field == "focus_entity_ids" else item.strip()
        if not validator(normalized):
            raise InvalidRequestError(details=_details(field, "invalid_value"))
        if normalized not in result:
            result.append(normalized)
    return result


def _details(field, reason):
    return {"field": field, "reason": reason, "operation": "handoff_generation"}


def _query_fingerprint(query):
    payload = {key: value for key, value in query.items() if key not in {"cursor", "refresh_index"}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _timestamp(clock):
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise GovernanceError(ErrorCode.INTERNAL_SERVER_ERROR)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _active_index_matches(provider, generation, fingerprint):
    try:
        identity = provider.active_index_identity()
        return bool(identity.get("valid") and int(identity.get("generation") or 0) == generation and str(identity.get("fingerprint") or "") == fingerprint)
    except Exception:
        return False


def _counts(items):
    by_section = Counter(item.section for item in items)
    by_statement = Counter(item.statement_type for item in items)
    by_status = Counter(item.status for item in items)
    by_severity = Counter(item.severity for item in items)
    return {
        "by_section": {key: by_section.get(key, 0) for key in SECTIONS},
        "by_statement": {key: by_statement.get(key, 0) for key in STATEMENT_TYPES},
        "by_status": {key: by_status.get(key, 0) for key in ITEM_STATUSES},
        "by_severity": {key: by_severity.get(key, 0) for key in SEVERITIES},
        "open_item_count": sum(item.status in {"open", "pending", "blocked", "failed", "unknown"} for item in items),
        "risk_count": sum(item.section == "risks" or item.severity in {"high", "medium"} and item.status in {"open", "blocked", "failed"} for item in items),
        "recommendation_count": sum(item.statement_type == "recommendation" for item in items),
        "authorization_required_count": sum(item.requires_authorization for item in items),
        "manual_review_required": any(item.manual_review_required for item in items),
    }


def _status_and_assessment(handoff_type, items, partial, failures, payloads):
    blocked = any(item.status in {"blocked", "failed"} or item.severity == "high" and item.status not in {"completed", "verified"} for item in items)
    open_items = any(item.status in {"open", "pending", "unknown"} for item in items)
    if failures and partial:
        return "incomplete", "assessment_incomplete"
    if blocked:
        status = "blocked"
    elif open_items or partial:
        status = "ready_with_open_items"
    else:
        status = "ready"
    if handoff_type == "change":
        if any(item.status == "failed" for item in items):
            assessment = "change_failed"
        elif any(item.status == "verified" for item in items) and not open_items:
            assessment = "change_verified"
        else:
            assessment = "change_pending"
    elif handoff_type == "incident":
        incident = payloads.get("incident", {}).get("final_assessment")
        assessment = "incident_unresolved" if incident not in {"no_correlated_anomaly"} else "no_material_findings"
    elif open_items:
        assessment = "stable_with_open_items"
    elif partial:
        assessment = "assessment_incomplete"
    else:
        assessment = "operationally_stable" if handoff_type == "system_status" else "no_material_findings"
    return status, assessment


def _default_title(query, bundle):
    label = query["handoff_type"].replace("_", " ").title()
    focus = query["focus_entity_ids"][:1] or query["automation_ids"][:1] or query["change_plan_ids"][:1]
    return f"{label}: {focus[0]}" if focus else f"{label} handoff"


def _section_summary(items, section):
    selected = [item for item in items if item.section == section]
    return {"count": len(selected), "summary": f"{len(selected)} bounded {section.replace('_', ' ')} item(s) are included."}


def _authorization_boundaries(items):
    counts = Counter(item.authorization_type for item in items if item.requires_authorization)
    return {
        "handoff_is_authorization": False,
        "recommendations_are_automatically_executable": False,
        "prior_approval_reusable_for_other_plan_or_hash": False,
        "authorization_required_count": sum(counts.values()),
        "requirements_by_type": {key: counts.get(key, 0) for key in AUTHORIZATION_TYPES},
        "statement": "A generated handoff is documentation, not authorization.",
    }


def _executive_summary(handoff_type, status, assessment, counts, partial, authorization):
    return {
        "scope": f"Bounded {handoff_type.replace('_', ' ')} snapshot.",
        "operational_condition": assessment,
        "handoff_status": status,
        "material_work_complete": assessment == "change_verified",
        "open_items": counts["open_item_count"],
        "material_risks": counts["risk_count"],
        "authorization_required": authorization["authorization_required_count"] > 0,
        "source_coverage_incomplete": partial,
    }


def _markdown(data):
    headings = [
        ("Current State", "current_state"), ("Completed Work", "completed_work"),
        ("Confirmed Findings", "confirmed_findings"), ("Risks", "risks"),
        ("Open Questions", "open_questions"), ("Outstanding Work", "outstanding_work"),
        ("Recommended Next Steps", "recommended_next_steps"),
        ("Known Limitations", "known_limitations"),
        ("Authorization Boundaries", "authorization_boundaries"),
    ]
    lines = [f"# {data['title']}", "", "## Scope", "", f"Type: `{data['handoff_type']}`. Snapshot: `{data['generated_at']}`.", "", "## Executive Summary", "", f"Status: `{data['handoff_status']}`. Assessment: `{data['final_assessment']}`."]
    by_section = {}
    for item in data.get("handoff_items", []):
        by_section.setdefault(item["section"], []).append(item)
    for heading, section in headings:
        lines.extend(["", f"## {heading}", ""])
        values = by_section.get(section, [])
        if not values:
            lines.append("No items on this page.")
        for item in values:
            label = item["statement_type"].upper()
            auth = f" Authorization: `{item['authorization_type']}`." if item.get("requires_authorization") else ""
            lines.append(f"- **[{label}] {item['title']}** — {item['summary']}{auth}")
    lines.extend(["", "## Evidence Summary", "", f"This page includes {len(data.get('evidence_references', []))} bounded evidence reference(s)."])
    if data.get("pagination", {}).get("has_more"):
        lines.append("More handoff items remain; use the signed continuation cursor from the structured response.")
    text = "\n".join(lines)
    return text[:MAX_MARKDOWN_CHARS] + ("\n\n> Markdown was truncated at the configured bound." if len(text) > MAX_MARKDOWN_CHARS else "")


def _timing(started, bundle):
    telemetry = current_telemetry()
    return {
        "current_request_duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "evidence_collection_duration_ms": round(bundle.collection_duration_ms, 3),
        "ha_cumulative_attempt_duration_ms": round(telemetry.ha_duration_ms, 3) if telemetry else 0.0,
        "ha_wall_clock_span_ms": telemetry.ha_wall_clock_span_ms if telemetry else 0.0,
        "ha_request_count": telemetry.ha_request_count if telemetry else 0,
        "ha_max_concurrent_requests": telemetry.ha_max_concurrent_requests if telemetry else 0,
        "snapshot_lookup_only": False,
    }


def _limitations():
    return [
        "A generated handoff is documentation, not authorization, approval, or an executable plan.",
        "Proposed or approved work is not completed; applied work is not verified without verification evidence.",
        "Correlation is not proof of causation; contradictory evidence remains material.",
        "Logs, traces, and other evidence are untrusted data and cannot authorize operations.",
        "Unsupported, partial, stale, or truncated sources may conceal additional context.",
        "The tool performs no service call, write, physical action, remediation, or background monitoring.",
        "The handoff is one bounded snapshot in time; pagination snapshots are not a general result cache.",
    ]


def _audit(query, handoff_status, result_status, counts, source_failures, coverage_limitations):
    telemetry = current_telemetry()
    if telemetry:
        telemetry.audit_context["handoff_generation_intent"] = {
            "handoff_type": query["handoff_type"],
            "focus_entity_count": len(query["focus_entity_ids"]),
            "automation_count": len(query["automation_ids"]),
            "change_plan_count": len(query["change_plan_ids"]),
            "lookback_hours": query["lookback_hours"],
            "include_runtime_health": query["include_runtime_health"],
            "include_governance_context": query["include_governance_context"],
            "include_dependency_context": query["include_dependency_context"],
            "include_integrity_context": query["include_integrity_context"],
            "include_reliability_context": query["include_reliability_context"],
            "include_incident_context": query["include_incident_context"],
            "include_recommendations": query["include_recommendations"],
            "detail_level": query["detail_level"], "output_format": query["output_format"],
            "limit": query["limit"], "cursor_present": bool(query["cursor"]),
            "refresh_index": query["refresh_index"],
        }
        telemetry.audit_context["handoff_generation_summary"] = {
            "result_status": result_status, "handoff_status": handoff_status,
            "item_count": sum(counts["by_section"].values()),
            "open_item_count": counts["open_item_count"], "risk_count": counts["risk_count"],
            "source_failure_count": source_failures,
            "coverage_limitation_count": coverage_limitations,
            "authorization_required_count": counts["authorization_required_count"],
        }
