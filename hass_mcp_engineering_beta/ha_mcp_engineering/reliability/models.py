"""Bounded contracts for single-automation reliability analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any


SEVERITIES = ("info", "low", "medium", "high", "critical")
CONFIDENCE_VALUES = ("exact", "high", "medium", "low")
STATUS_VALUES = ("confirmed", "probable", "possible", "evidence_gap")


def stable_id(prefix: str, *parts: Any) -> str:
    encoded = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class ReliabilityEvidenceReference:
    reference_id: str
    source_type: str
    source_id: str
    summary: str
    timestamp: str | None = None
    configuration_path: str | None = None
    trace_run_id: str | None = None
    trace_step: str | None = None
    interval: dict[str, str | None] | None = None
    correlation_basis: tuple[str, ...] = ()
    confidence: str | None = None

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        return {key: item for key, item in value.items() if item not in (None, "", (), [])}


@dataclass
class ReliabilitySourceCoverage:
    source_type: str
    provider: str
    provider_capability: str
    completeness: str
    items_examined: int = 0
    failed_items: int = 0
    duration_ms: float = 0.0
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)
    fallback_occurred: bool = False
    policy: str = "single_automation_reliability_read"
    affects_result_status: bool = True
    snapshot_completeness: str | None = None
    retention_coverage: str | None = None
    requested_lookback_hours: int | None = None

    def public(self) -> dict[str, Any]:
        value = {
            "source_type": self.source_type,
            "provider": self.provider,
            "provider_capability": self.provider_capability,
            "completeness": self.completeness,
            "items_examined": max(0, int(self.items_examined)),
            "failed_items": max(0, int(self.failed_items)),
            "duration_ms": round(max(0.0, self.duration_ms), 3),
            "truncated": bool(self.truncated),
            "warnings": self.warnings[:10],
            "fallback_occurred": bool(self.fallback_occurred),
            "policy": self.policy,
            "affects_result_status": self.affects_result_status,
        }
        if self.snapshot_completeness is not None:
            value["snapshot_completeness"] = self.snapshot_completeness
        if self.retention_coverage is not None:
            value["retention_coverage"] = self.retention_coverage
        if self.requested_lookback_hours is not None:
            value["requested_lookback_hours"] = self.requested_lookback_hours
        return value


@dataclass(frozen=True)
class ReliabilityFinding:
    finding_id: str
    rule_id: str
    title: str
    severity: str
    confidence: str
    status: str
    explanation: str
    automation_id: str
    automation_entity_id: str | None
    occurrence_count: int = 1
    configuration_path: str | None = None
    trace_step: str | None = None
    first_observed: str | None = None
    last_observed: str | None = None
    evidence_references: tuple[str, ...] = ()
    operational_impact: str = ""
    recommended_next_investigation: str = ""
    governed_change_required: bool = False
    root_cause_group_id: str | None = None
    related_finding_ids: tuple[str, ...] = ()
    root_cause_relationship: str = "primary"
    occurrence_ids: tuple[str, ...] = field(default=(), repr=False, compare=False)
    affected_dependency: str | None = None

    def public(self, *, include_evidence: bool = True) -> dict[str, Any]:
        value = asdict(self)
        value.pop("occurrence_ids", None)
        value["evidence_references"] = list(self.evidence_references) if include_evidence else []
        value["related_finding_ids"] = list(self.related_finding_ids)
        return {key: item for key, item in value.items() if item not in (None, "", (), [])}


@dataclass(frozen=True)
class ReliabilityRootCauseGroup:
    root_cause_group_id: str
    primary_finding_id: str
    member_finding_ids: tuple[str, ...]
    unique_occurrence_count: int
    highest_severity: str
    first_observed: str | None
    last_observed: str | None
    affected_step: str | None
    affected_dependency: str | None
    evidence_references: tuple[str, ...]

    def public(self, *, include_evidence: bool = True) -> dict[str, Any]:
        value = asdict(self)
        value["member_finding_ids"] = list(self.member_finding_ids)
        value["evidence_references"] = list(self.evidence_references) if include_evidence else []
        return {key: item for key, item in value.items() if item not in (None, "", (), [])}


@dataclass
class ReliabilityEvidenceBundle:
    automation_id: str
    automation: dict[str, Any]
    configuration: dict[str, Any]
    configuration_fingerprint: str
    blueprint: dict[str, Any] | None
    blueprint_path: str | None
    references: list[dict[str, Any]]
    dynamic_references: list[dict[str, Any]]
    traces: list[dict[str, Any]]
    system_log_entries: list[dict[str, Any]]
    coverage: list[ReliabilitySourceCoverage]
    evidence: dict[str, ReliabilityEvidenceReference] = field(default_factory=dict)

    @property
    def partial(self) -> bool:
        return any(
            item.affects_result_status
            and item.completeness not in {"complete", "not_requested"}
            for item in self.coverage
        )

    @property
    def has_coverage_limitations(self) -> bool:
        return any(
            item.completeness not in {"complete", "not_requested"}
            or item.retention_coverage in {"unknown", "partial"}
            for item in self.coverage
        )

    def evidence_fingerprint(self) -> str:
        payload = {
            "configuration": self.configuration_fingerprint,
            "automation": {
                "entity_id": self.automation.get("entity_id"),
                "state": self.automation.get("state"),
                "last_triggered": self.automation.get("last_triggered"),
            },
            "references": [
                (item.get("entity_id"), item.get("status"), item.get("config_path"))
                for item in self.references
            ],
            "traces": [
                (item.get("run_id"), item.get("timestamp"), item.get("last_step"), item.get("error"))
                for item in self.traces
            ],
            "system_log": [item.get("identity") for item in self.system_log_entries],
            "coverage": [(item.source_type, item.completeness, item.truncated) for item in self.coverage],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()


@dataclass
class ReliabilityAnalysisOutput:
    data: dict[str, Any]
    warnings: list[str]
    metadata: dict[str, Any]
    partial: bool = False
