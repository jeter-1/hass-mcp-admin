"""Stable internal contracts for generated Engineering handoffs."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

HANDOFF_TYPES = ("system_status", "focused_review", "incident", "change")
HANDOFF_STATUSES = ("ready", "ready_with_open_items", "blocked", "incomplete")
FINAL_ASSESSMENTS = (
    "operationally_stable", "stable_with_open_items", "change_pending",
    "change_verified", "change_failed", "incident_unresolved",
    "investigation_required", "no_material_findings", "assessment_incomplete",
)
SECTIONS = (
    "current_state", "completed_work", "confirmed_findings", "inferences",
    "risks", "open_questions", "outstanding_work", "recommended_next_steps",
    "known_limitations", "authorization_boundaries",
)
STATEMENT_TYPES = ("fact", "inference", "recommendation", "limitation")
ITEM_STATUSES = (
    "current", "completed", "verified", "open", "pending", "blocked",
    "failed", "rolled_back", "unknown", "not_applicable",
)
SEVERITIES = ("high", "medium", "low", "info")
CONFIDENCE_LEVELS = ("confirmed", "high", "medium", "low", "insufficient")
AUTHORIZATION_TYPES = (
    "none", "manual_review", "governed_change_plan",
    "explicit_runtime_write_approval", "external_action",
)
RECOMMENDATION_CATEGORIES = (
    "read_only_investigation", "manual_review", "documentation",
    "monitoring_review", "governed_change_candidate", "external_follow_up",
)


def stable_id(prefix: str, *parts: Any) -> str:
    value = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class HandoffEvidenceReference:
    reference_id: str
    source_type: str
    source_object: str
    summary: str
    confidence: str = "confirmed"
    coverage_status: str = "complete"
    timestamp: str | None = None
    time_range: tuple[str | None, str | None] | None = None

    def public(self) -> dict[str, Any]:
        result = {
            "reference_id": self.reference_id,
            "source_type": self.source_type,
            "source_object": self.source_object,
            "summary": self.summary[:500],
            "confidence": self.confidence,
            "coverage_status": self.coverage_status,
        }
        if self.timestamp:
            result["timestamp"] = self.timestamp
        if self.time_range:
            result["time_range"] = list(self.time_range)
        return result


@dataclass(frozen=True)
class HandoffItem:
    item_id: str
    section: str
    statement_type: str
    title: str
    summary: str
    status: str
    severity: str
    confidence: str
    affected_entity_ids: tuple[str, ...] = ()
    automation_ids: tuple[str, ...] = ()
    change_plan_ids: tuple[str, ...] = ()
    supporting_evidence_reference_ids: tuple[str, ...] = ()
    contradicting_evidence_reference_ids: tuple[str, ...] = ()
    coverage_limitations: tuple[str, ...] = ()
    manual_review_required: bool = False
    requires_authorization: bool = False
    authorization_type: str = "none"
    recommendation_category: str | None = None
    timestamp: str | None = None

    def public(self) -> dict[str, Any]:
        value = {
            "item_id": self.item_id, "section": self.section,
            "statement_type": self.statement_type, "title": self.title[:200],
            "summary": self.summary[:800], "status": self.status,
            "severity": self.severity, "confidence": self.confidence,
            "affected_entity_ids": list(self.affected_entity_ids),
            "automation_ids": list(self.automation_ids),
            "change_plan_ids": list(self.change_plan_ids),
            "supporting_evidence_reference_ids": list(self.supporting_evidence_reference_ids),
            "contradicting_evidence_reference_ids": list(self.contradicting_evidence_reference_ids),
            "coverage_limitations": list(self.coverage_limitations),
            "manual_review_required": self.manual_review_required,
            "requires_authorization": self.requires_authorization,
            "authorization_type": self.authorization_type,
        }
        if self.recommendation_category:
            value["recommendation_category"] = self.recommendation_category
        if self.timestamp:
            value["timestamp"] = self.timestamp
        return value


@dataclass
class HandoffEvidenceBundle:
    scope: dict[str, Any]
    items: list[HandoffItem]
    evidence: dict[str, HandoffEvidenceReference]
    coverage: list[Any]
    index: dict[str, Any]
    source_payloads: dict[str, Any] = field(default_factory=dict)
    collection_duration_ms: float = 0.0
    item_truncated: bool = False
    markdown_truncated: bool = False

    @property
    def source_partial(self) -> bool:
        return any(not item.assessment_complete for item in self.coverage)

    def fingerprint(self) -> str:
        payload = {
            "scope": self.scope,
            "items": [item.item_id for item in self.items],
            "evidence": sorted(self.evidence),
            "coverage": [
                (item.source_type, item.completeness, item.failed_items,
                 item.failure_category, tuple(item.coverage_limitations))
                for item in self.coverage
            ],
            "index": (self.index.get("generation"), self.index.get("fingerprint")),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class HandoffGenerationOutput:
    data: dict[str, Any]
    warnings: list[str]
    metadata: dict[str, Any]
    partial: bool = False
