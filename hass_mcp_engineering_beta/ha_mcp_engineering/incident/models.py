"""Bounded incident-correlation evidence and result contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any

from ..source_coverage import assessment_complete


EVENT_TYPES = (
    "automation_triggered",
    "automation_condition_failed",
    "automation_action_failed",
    "automation_completed",
    "service_call_observed",
    "state_changed",
    "entity_became_unavailable",
    "entity_recovered",
    "system_warning",
    "system_error",
    "dependency_relationship",
    "integrity_finding",
    "reliability_finding",
    "dynamic_reference_uncertainty",
)

CORRELATION_RULES = (
    "trace_failure_with_unavailable_dependency",
    "trace_failure_with_missing_reference",
    "service_call_followed_by_state_change",
    "unexpected_state_change_with_automation_activity",
    "repeated_trace_failure_pattern",
    "integration_error_with_related_entities",
    "shared_dependency_failure",
    "configuration_integrity_contributor",
    "dynamic_reference_uncertainty",
    "recovery_after_dependency_restoration",
    "conflicting_evidence",
    "insufficient_evidence",
)

CONFIDENCE_LEVELS = ("confirmed", "high", "medium", "low", "insufficient")
CAUSAL_STATUSES = (
    "confirmed_cause",
    "probable_contributor",
    "possible_contributor",
    "correlated_condition",
    "contradictory_evidence",
    "insufficient_evidence",
)
SEVERITIES = ("high", "medium", "low", "info")
FINAL_ASSESSMENTS = (
    "probable_cause_identified",
    "multiple_plausible_contributors",
    "correlated_activity_found",
    "no_correlated_anomaly",
    "insufficient_evidence",
    "assessment_incomplete",
)


def stable_id(prefix: str, *parts: Any) -> str:
    encoded = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class IncidentEvidenceReference:
    reference_id: str
    source_type: str
    source_object: str
    summary: str
    confidence: str
    coverage_status: str
    timestamp: str | None = None
    time_range: tuple[str | None, str | None] | None = None

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        if self.time_range:
            value["time_range"] = list(self.time_range)
        return {key: item for key, item in value.items() if item not in (None, "", (), [])}


@dataclass(frozen=True)
class IncidentEvent:
    event_id: str
    timestamp: str | None
    event_type: str
    source_type: str
    summary: str
    evidence_reference_ids: tuple[str, ...]
    entity_id: str | None = None
    automation_id: str | None = None
    run_id: str | None = None
    integration_domain: str | None = None
    severity: str = "info"
    original_timestamp: str | None = None
    cluster_key: str | None = field(default=None, repr=False, compare=False)

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("cluster_key", None)
        value["evidence_reference_ids"] = list(self.evidence_reference_ids)
        return {key: item for key, item in value.items() if item not in (None, "", (), [])}


@dataclass(frozen=True)
class IncidentHypothesis:
    hypothesis_id: str
    rule_id: str
    title: str
    confidence: str
    severity: str
    causal_status: str
    explanation: str
    supporting_evidence_reference_ids: tuple[str, ...]
    contradicting_evidence_reference_ids: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    coverage_limitations: tuple[str, ...] = ()
    affected_entity_ids: tuple[str, ...] = ()
    automation_ids: tuple[str, ...] = ()
    first_observed: str | None = None
    last_observed: str | None = None
    manual_review_required: bool = True
    rank: int = 0

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        for key in (
            "supporting_evidence_reference_ids",
            "contradicting_evidence_reference_ids",
            "missing_evidence",
            "coverage_limitations",
            "affected_entity_ids",
            "automation_ids",
        ):
            value[key] = list(value[key])
        return {key: item for key, item in value.items() if item not in (None, "", (), [])}


@dataclass
class IncidentSourceCoverage:
    source_type: str
    provider: str
    provider_capability: str
    completeness: str
    requested: bool
    required_for_assessment: bool
    items_examined: int = 0
    failed_items: int = 0
    warnings: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    cached_provenance: bool = False
    failure_category: str | None = None
    upstream_attempted: bool = False
    coverage_limitations: list[str] = field(default_factory=list)

    @property
    def assessment_complete(self) -> bool:
        return assessment_complete(
            completeness=self.completeness,
            required=self.required_for_assessment,
        )

    @property
    def actual_failure(self) -> bool:
        return bool(self.failure_category or self.failed_items)

    def public(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "provider": self.provider,
            "provider_capability": self.provider_capability,
            "completeness": self.completeness,
            "requested": self.requested,
            "required_for_assessment": self.required_for_assessment,
            "assessment_complete": self.assessment_complete,
            "items_examined": max(0, int(self.items_examined)),
            "failed_items": max(0, int(self.failed_items)),
            "warnings": [str(item)[:240] for item in self.warnings[:10]],
            "duration_ms": round(max(0.0, float(self.duration_ms)), 3),
            "cached_provenance": self.cached_provenance,
            "failure_category": self.failure_category,
            "upstream_attempted": self.upstream_attempted,
            "coverage_limitations": [
                str(item)[:128]
                for item in sorted(dict.fromkeys(self.coverage_limitations))[:10]
            ],
        }


@dataclass
class IncidentEvidenceBundle:
    focus: dict[str, Any]
    events: list[IncidentEvent]
    evidence: dict[str, IncidentEvidenceReference]
    coverage: list[IncidentSourceCoverage]
    index: dict[str, Any]
    reliability_findings: list[dict[str, Any]] = field(default_factory=list)
    integrity_findings: list[dict[str, Any]] = field(default_factory=list)
    collection_duration_ms: float = 0.0
    evidence_truncated: bool = False
    timeline_truncated: bool = False

    @property
    def source_partial(self) -> bool:
        return any(not item.assessment_complete for item in self.coverage)

    def evidence_fingerprint(self) -> str:
        payload = {
            "focus": self.focus,
            "events": [(item.event_id, item.timestamp, item.event_type) for item in self.events],
            "coverage": [
                (
                    item.source_type,
                    item.completeness,
                    item.items_examined,
                    item.failed_items,
                    item.failure_category,
                    tuple(sorted(item.coverage_limitations)),
                )
                for item in self.coverage
            ],
            "index": {
                "generation": self.index.get("generation"),
                "fingerprint": self.index.get("fingerprint"),
            },
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()


@dataclass
class IncidentAnalysisOutput:
    data: dict[str, Any]
    warnings: list[str]
    metadata: dict[str, Any]
    partial: bool = False
