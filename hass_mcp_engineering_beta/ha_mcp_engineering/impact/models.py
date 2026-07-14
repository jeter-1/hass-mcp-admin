"""Bounded contracts for single-entity change-impact analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any

from ..source_coverage import assessment_complete


OPERATIONS = ("rename_entity", "remove_entity", "disable_entity")
SEVERITIES = ("critical", "high", "medium", "low", "info")
CONFIDENCE_VALUES = ("exact", "high", "limited", "unknown")
COVERAGE_VALUES = {
    "complete",
    "partial",
    "unavailable",
    "not_requested",
    "not_supported",
}


def stable_id(prefix: str, *parts: Any) -> str:
    encoded = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


@dataclass
class ImpactSourceCoverage:
    source_type: str
    provider: str
    provider_capability: str
    completeness: str
    requested: bool = True
    required_for_assessment: bool = True
    items_examined: int = 0
    failed_items: int = 0
    warnings: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    truncated: bool = False
    fallback_occurred: bool = False
    policy: str = "single_entity_change_impact_read"
    snapshot_completeness: str | None = None
    retention_coverage: str | None = None
    cached_provenance: bool = False
    original_index_build_duration_ms: float | None = None

    @property
    def assessment_complete(self) -> bool:
        return assessment_complete(
            completeness=self.completeness,
            required=self.required_for_assessment,
        )

    def public(self) -> dict[str, Any]:
        value = {
            "source_type": self.source_type[:64],
            "provider": self.provider[:64],
            "provider_capability": self.provider_capability[:64],
            "completeness": self.completeness,
            "requested": bool(self.requested),
            "required_for_assessment": bool(self.required_for_assessment),
            "items_examined": max(0, int(self.items_examined)),
            "failed_items": max(0, int(self.failed_items)),
            "warnings": [str(item)[:240] for item in self.warnings[:10]],
            "duration_ms": round(max(0.0, float(self.duration_ms)), 3),
            "truncated": bool(self.truncated),
            "fallback_occurred": bool(self.fallback_occurred),
            "policy": self.policy,
            "assessment_complete": self.assessment_complete,
            "cached_provenance": bool(self.cached_provenance),
        }
        if self.snapshot_completeness is not None:
            value["snapshot_completeness"] = self.snapshot_completeness
        if self.retention_coverage is not None:
            value["retention_coverage"] = self.retention_coverage
        if self.original_index_build_duration_ms is not None:
            value["original_index_build_duration_ms"] = round(
                max(0.0, float(self.original_index_build_duration_ms)), 3
            )
        return value


@dataclass(frozen=True)
class ImpactEvidenceReference:
    reference_id: str
    source_type: str
    source_id: str
    evidence_kind: str
    summary: str
    affected_object_type: str
    affected_object_id: str
    confidence: str = "exact"
    configuration_paths: tuple[str, ...] = ()
    dependency_path: tuple[str, ...] = ()
    timestamp: str | None = None
    excerpt: str | None = None

    def public(self, *, detail_level: str = "standard") -> dict[str, Any]:
        value = asdict(self)
        if detail_level == "summary":
            return {
                "reference_id": self.reference_id,
                "source_type": self.source_type,
                "evidence_kind": self.evidence_kind,
                "summary": self.summary,
            }
        if detail_level != "evidence":
            value.pop("excerpt", None)
            value.pop("dependency_path", None)
            value["configuration_paths"] = list(self.configuration_paths[:10])
        else:
            value["configuration_paths"] = list(self.configuration_paths[:20])
            value["dependency_path"] = list(self.dependency_path[:10])
        return {
            key: item
            for key, item in value.items()
            if item not in (None, "", (), [])
        }


@dataclass(frozen=True)
class ImpactFinding:
    finding_id: str
    rule_id: str
    severity: str
    confidence: str
    impact_type: str
    affected_object_type: str
    affected_object_id: str
    direct: bool
    dependency_depth: int
    explanation: str
    consequence: str
    evidence_references: tuple[str, ...]
    remediation_required: bool
    manual_review_required: bool
    source_coverage: tuple[str, ...]

    def public(self, *, include_evidence: bool = True) -> dict[str, Any]:
        value = asdict(self)
        value["evidence_references"] = (
            list(self.evidence_references) if include_evidence else []
        )
        value["source_coverage"] = list(self.source_coverage)
        return value


@dataclass(frozen=True)
class ImpactAffectedObjectGroup:
    group_id: str
    affected_object_type: str
    affected_object_id: str
    consequence: str
    finding_ids: tuple[str, ...]
    evidence_references: tuple[str, ...]
    highest_severity: str
    direct: bool
    minimum_depth: int

    def public(self, *, include_evidence: bool = True) -> dict[str, Any]:
        value = asdict(self)
        value["finding_ids"] = list(self.finding_ids)
        value["evidence_references"] = (
            list(self.evidence_references) if include_evidence else []
        )
        return value


@dataclass
class ImpactEvidenceBundle:
    entity_id: str
    operation: str
    replacement_entity_id: str | None
    target: dict[str, Any]
    replacement_conflict: bool
    direct_dependencies: list[dict[str, Any]]
    indirect_dependencies: list[dict[str, Any]]
    dynamic_references: list[dict[str, Any]]
    recent_traces: list[dict[str, Any]]
    system_log_entries: list[dict[str, Any]]
    evidence: dict[str, ImpactEvidenceReference]
    coverage: list[ImpactSourceCoverage]
    index: dict[str, Any]
    evidence_collection_duration_ms: float
    confirmed_target_related_dynamic_count: int = 0
    unresolved_in_requested_scope_count: int = 0
    dynamic_outside_requested_scope_count: int = 0

    @property
    def required_coverage_complete(self) -> bool:
        return all(item.assessment_complete for item in self.coverage)

    @property
    def source_partial(self) -> bool:
        return not self.required_coverage_complete

    def evidence_fingerprint(self) -> str:
        payload = {
            "target": {
                "entity_id": self.entity_id,
                "state_status": self.target.get("state_status"),
                "registry_entry_exists": self.target.get("registry_entry_exists"),
                "disabled": self.target.get("disabled"),
                "device_id": self.target.get("device_id"),
                "area_id": self.target.get("area_id"),
            },
            "operation": self.operation,
            "replacement": self.replacement_entity_id,
            "replacement_conflict": self.replacement_conflict,
            "dynamic_references": {
                "confirmed_target_related": self.confirmed_target_related_dynamic_count,
                "unresolved_in_requested_scope": self.unresolved_in_requested_scope_count,
                "outside_requested_scope": self.dynamic_outside_requested_scope_count,
            },
            "evidence": sorted(self.evidence),
            "coverage": [
                (
                    item.source_type,
                    item.completeness,
                    item.required_for_assessment,
                    item.items_examined,
                    item.failed_items,
                    item.truncated,
                )
                for item in self.coverage
            ],
            "index": {
                "fingerprint": self.index.get("fingerprint"),
                "generation": self.index.get("generation"),
            },
        }
        return hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8")
        ).hexdigest()


@dataclass
class ImpactAnalysisOutput:
    data: dict[str, Any]
    warnings: list[str]
    metadata: dict[str, Any]
    partial: bool = False
