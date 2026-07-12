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

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        return {key: item for key, item in value.items() if item not in (None, "")}


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

    def public(self) -> dict[str, Any]:
        return {
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
        }


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

    def public(self, *, include_evidence: bool = True) -> dict[str, Any]:
        value = asdict(self)
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
        return any(item.completeness not in {"complete", "not_requested"} for item in self.coverage)

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

