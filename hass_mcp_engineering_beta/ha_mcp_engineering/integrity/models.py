"""Bounded contracts for global configuration-integrity analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any


FINDING_TYPES = (
    "missing_entity_reference",
    "disabled_entity_reference",
    "registry_only_entity_reference",
    "orphan_registry_candidate",
    "unresolved_dynamic_reference",
)
SEVERITIES = ("high", "medium", "low", "info")
COVERAGE_VALUES = {
    "complete",
    "partial",
    "not_supported",
    "not_requested",
    "failed",
}


def stable_id(prefix: str, *parts: Any) -> str:
    encoded = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


@dataclass
class IntegritySourceCoverage:
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
    cached_provenance: bool = False
    original_index_build_duration_ms: float | None = None

    @property
    def assessment_complete(self) -> bool:
        return (
            not self.required_for_assessment
            or self.completeness in {"complete", "not_requested"}
        )

    def public(self) -> dict[str, Any]:
        value = {
            "source_type": self.source_type[:64],
            "provider": self.provider[:64],
            "provider_capability": self.provider_capability[:64],
            "completeness": self.completeness,
            "requested": bool(self.requested),
            "required_for_assessment": bool(self.required_for_assessment),
            "assessment_complete": self.assessment_complete,
            "items_examined": max(0, int(self.items_examined)),
            "failed_items": max(0, int(self.failed_items)),
            "warnings": [str(item)[:240] for item in self.warnings[:10]],
            "duration_ms": round(max(0.0, float(self.duration_ms)), 3),
            "cached_provenance": bool(self.cached_provenance),
        }
        if self.original_index_build_duration_ms is not None:
            value["original_index_build_duration_ms"] = round(
                max(0.0, float(self.original_index_build_duration_ms)), 3
            )
        return value


@dataclass(frozen=True)
class IntegrityEvidenceReference:
    reference_id: str
    evidence_kind: str
    summary: str
    confidence: str
    target_entity_id: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    source_entity_id: str | None = None
    source_name: str | None = None
    configuration_paths: tuple[str, ...] = ()
    registry_platform: str | None = None
    disabled_by: str | None = None
    excerpt: str | None = None

    def public(self, *, detail_level: str = "standard") -> dict[str, Any]:
        value = asdict(self)
        value["configuration_paths"] = list(self.configuration_paths[:20])
        if detail_level == "summary":
            return {
                "reference_id": self.reference_id,
                "evidence_kind": self.evidence_kind,
                "summary": self.summary,
                **(
                    {"source_type": self.source_type}
                    if self.source_type is not None
                    else {}
                ),
            }
        if detail_level != "evidence":
            value.pop("excerpt", None)
            value["configuration_paths"] = value["configuration_paths"][:10]
        return {
            key: item
            for key, item in value.items()
            if item not in (None, "", (), [])
        }


@dataclass(frozen=True)
class IntegrityFinding:
    finding_id: str
    rule_id: str
    finding_type: str
    severity: str
    confidence: str
    explanation: str
    consequence: str
    configuration_paths: tuple[str, ...]
    evidence_references: tuple[str, ...]
    manual_review_required: bool
    remediation_required: bool
    target_entity_id: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    source_entity_id: str | None = None
    source_name: str | None = None
    source_enabled: bool | None = None
    registry_platform: str | None = None
    disabled_by: str | None = None
    disabled_classification: str | None = None

    def public(self, *, include_evidence: bool = True) -> dict[str, Any]:
        value = asdict(self)
        value["configuration_paths"] = list(self.configuration_paths[:20])
        value["evidence_references"] = (
            list(self.evidence_references) if include_evidence else []
        )
        return {
            key: item
            for key, item in value.items()
            if item is not None and item not in ((), [])
        }


@dataclass
class IntegrityEvidenceBundle:
    exact_references: list[dict[str, Any]]
    dynamic_references: list[dict[str, Any]]
    current_states: dict[str, dict[str, Any]]
    entity_registry: dict[str, dict[str, Any]]
    states_available: bool
    registry_available: bool
    coverage: list[IntegritySourceCoverage]
    index: dict[str, Any]
    evidence_collection_duration_ms: float
    dynamic_outside_requested_scope_count: int = 0
    orphan_scope_complete: bool = False

    @property
    def required_coverage_complete(self) -> bool:
        return all(item.assessment_complete for item in self.coverage)

    @property
    def source_partial(self) -> bool:
        return not self.required_coverage_complete

    def evidence_fingerprint(self) -> str:
        payload = {
            "references": [
                (
                    item.get("evidence_id"),
                    item.get("target_entity_id"),
                    item.get("source_type"),
                    item.get("source_id"),
                    item.get("config_path"),
                )
                for item in self.exact_references
            ],
            "dynamic": [
                (
                    item.get("evidence_id"),
                    item.get("source_type"),
                    item.get("source_id"),
                    item.get("config_path"),
                )
                for item in self.dynamic_references
            ],
            "states": sorted(self.current_states),
            "registry": sorted(
                (
                    entity_id,
                    item.get("disabled_by"),
                    item.get("platform"),
                )
                for entity_id, item in self.entity_registry.items()
            ),
            "coverage": [
                (
                    item.source_type,
                    item.completeness,
                    item.requested,
                    item.failed_items,
                )
                for item in self.coverage
            ],
            "index": {
                "generation": self.index.get("generation"),
                "fingerprint": self.index.get("fingerprint"),
            },
        }
        return hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8")
        ).hexdigest()


@dataclass
class IntegrityAnalysisOutput:
    data: dict[str, Any]
    warnings: list[str]
    metadata: dict[str, Any]
    partial: bool = False
