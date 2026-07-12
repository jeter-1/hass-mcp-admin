"""Bounded dependency graph, source coverage, and index snapshot models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any


SOURCE_TYPES = ("automation", "blueprint", "script", "scene", "group", "template", "dashboard")
COMPLETENESS_VALUES = {"complete", "partial", "unavailable", "unsupported", "not_requested"}


@dataclass(frozen=True)
class DependencyFinding:
    evidence_id: str
    target_entity_id: str
    source_type: str
    source_id: str
    source_entity_id: str | None
    source_name: str | None
    relation: str
    config_path: str
    direct: bool = True
    depth: int = 1
    confidence: str = "exact"
    match_type: str = "structured_exact"
    blueprint_path: str | None = None
    blueprint_input: str | None = None
    source_state: str | None = None
    evidence_summary: str = "Exact structured entity reference."
    excerpt: str | None = None
    evidence_path: tuple[str, ...] = ()

    def public(self, *, include_excerpt: bool = False) -> dict[str, Any]:
        value = asdict(self)
        value.pop("target_entity_id", None)
        if not include_excerpt:
            value.pop("excerpt", None)
        value["evidence_path"] = list(self.evidence_path)
        return {key: item for key, item in value.items() if item is not None and item != ()}


@dataclass(frozen=True)
class DynamicReference:
    evidence_id: str
    source_type: str
    source_id: str
    config_path: str
    warning: str
    excerpt: str | None = None


@dataclass
class SourceCoverageItem:
    source_type: str
    provider: str
    provider_capability: str
    completeness: str
    evidence_count: int = 0
    failed_item_count: int = 0
    warnings: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    fallback_occurred: bool = False
    policy: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "provider": self.provider,
            "provider_capability": self.provider_capability,
            "completeness": self.completeness,
            "evidence_count": self.evidence_count,
            "failed_item_count": self.failed_item_count,
            "warnings": self.warnings[:10],
            "duration_ms": round(max(0.0, self.duration_ms), 3),
            "fallback_occurred": self.fallback_occurred,
            "policy": self.policy,
        }


@dataclass
class DependencyScanResult:
    findings: list[DependencyFinding]
    dynamic_references: list[DynamicReference]
    target_metadata: dict[str, dict[str, Any]]
    coverage: list[SourceCoverageItem]


@dataclass
class DependencyIndexSnapshot:
    fingerprint: str
    generation: int
    built_at_monotonic: float
    built_at: str
    findings: tuple[DependencyFinding, ...]
    dynamic_references: tuple[DynamicReference, ...]
    target_metadata: dict[str, dict[str, Any]]
    coverage: tuple[SourceCoverageItem, ...]


def evidence_id(*parts: Any) -> str:
    encoded = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return "ev_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def snapshot_fingerprint(
    findings: list[DependencyFinding], coverage: list[SourceCoverageItem], generation: int
) -> str:
    payload = {
        "generation": generation,
        "findings": [
            (item.evidence_id, item.target_entity_id, item.relation, item.config_path)
            for item in findings
        ],
        "coverage": [(item.source_type, item.completeness, item.failed_item_count) for item in coverage],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
