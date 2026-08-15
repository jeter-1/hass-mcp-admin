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
    source_entity_id: str | None = None
    source_name: str | None = None
    source_state: str | None = None
    possible_entity_domains: tuple[str, ...] | None = None
    possible_entity_ids: tuple[str, ...] = ()
    literal_label_selectors: tuple[str, ...] = ()
    candidate_resolution_kind: str = "unresolved"
    candidate_resolution_complete: bool = False
    candidate_resolution_limit_exceeded: bool = False
    reference_kind: str = "dynamic_entity_selector"
    entity_selector_present: bool = True


@dataclass(frozen=True)
class AutomationReadFailure:
    """Bounded identity for an automation whose configuration was unreadable."""

    source_id: str
    source_entity_id: str | None
    reason_code: str


@dataclass(frozen=True)
class AutomationActionRiskProfile:
    """Bounded normalized action consequence for one automation source."""

    source_id: str
    source_entity_id: str | None
    risk_level: str
    physical_consequence: str
    complete: bool
    truncated: bool
    action_domains: tuple[str, ...]
    services: tuple[str, ...]
    reason_codes: tuple[str, ...]
    effect_projection_model: str
    effect_targets: tuple[str, ...]
    effect_data: tuple[str, ...]
    effect_structure_fingerprint: str
    effect_projection_fingerprint: str
    effect_projection_clipped: bool
    evidence_fingerprint: str


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
    index_build_duration_ms: float | None = None
    cached_provenance: bool = False

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
            "index_build_duration_ms": (
                round(max(0.0, self.index_build_duration_ms), 3)
                if self.index_build_duration_ms is not None
                else None
            ),
            "cached_provenance": self.cached_provenance,
            "fallback_occurred": self.fallback_occurred,
            "policy": self.policy,
        }


@dataclass
class DependencyScanResult:
    findings: list[DependencyFinding]
    dynamic_references: list[DynamicReference]
    target_metadata: dict[str, dict[str, Any]]
    coverage: list[SourceCoverageItem]
    profile: dict[str, Any] = field(default_factory=dict)
    automation_action_profiles: list[AutomationActionRiskProfile] = field(
        default_factory=list
    )
    automation_read_failures: list[AutomationReadFailure] = field(
        default_factory=list
    )
    label_memberships: dict[str, tuple[str, ...]] = field(
        default_factory=dict
    )
    label_membership_fingerprints: dict[str, str] = field(
        default_factory=dict
    )
    label_membership_truncated: tuple[str, ...] = ()
    label_registry_complete: bool = False


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
    build_duration_ms: float = 0.0
    build_profile: dict[str, Any] = field(default_factory=dict)
    automation_action_profiles: tuple[AutomationActionRiskProfile, ...] = ()
    automation_read_failures: tuple[AutomationReadFailure, ...] = ()
    dynamic_reference_overflow_count: int = 0
    dynamic_reference_overflow_fingerprint: str | None = None
    label_memberships: dict[str, tuple[str, ...]] = field(
        default_factory=dict
    )
    label_membership_fingerprints: dict[str, str] = field(
        default_factory=dict
    )
    label_membership_truncated: tuple[str, ...] = ()
    label_registry_complete: bool = False


def evidence_id(*parts: Any) -> str:
    encoded = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return "ev_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def dynamic_reference_material(item: DynamicReference) -> dict[str, Any]:
    """Return bounded deterministic identity without exposing raw templates."""

    excerpt_fingerprint = (
        hashlib.sha256(item.excerpt.encode("utf-8")).hexdigest()
        if isinstance(item.excerpt, str)
        else None
    )
    return {
        "evidence_id": item.evidence_id,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "source_entity_id": item.source_entity_id,
        "config_path": item.config_path,
        "warning": item.warning,
        "possible_entity_domains": (
            list(item.possible_entity_domains)
            if isinstance(item.possible_entity_domains, tuple)
            else None
        ),
        "possible_entity_ids": list(item.possible_entity_ids),
        "literal_label_selectors": list(
            item.literal_label_selectors
        ),
        "candidate_resolution_kind": item.candidate_resolution_kind,
        "candidate_resolution_complete": (
            item.candidate_resolution_complete
        ),
        "candidate_resolution_limit_exceeded": (
            item.candidate_resolution_limit_exceeded
        ),
        "reference_kind": item.reference_kind,
        "entity_selector_present": item.entity_selector_present,
        "excerpt_fingerprint": excerpt_fingerprint,
    }


def dynamic_reference_fingerprint(item: DynamicReference) -> str:
    encoded = json.dumps(
        dynamic_reference_material(item),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def snapshot_fingerprint(
    findings: list[DependencyFinding],
    coverage: list[SourceCoverageItem],
    generation: int,
    automation_action_profiles: list[AutomationActionRiskProfile] | tuple[
        AutomationActionRiskProfile, ...
    ] = (),
    automation_read_failures: list[AutomationReadFailure] | tuple[
        AutomationReadFailure, ...
    ] = (),
    dynamic_references: list[DynamicReference] | tuple[
        DynamicReference, ...
    ] = (),
    dynamic_reference_overflow_count: int = 0,
    dynamic_reference_overflow_fingerprint: str | None = None,
    label_membership_fingerprints: dict[str, str] | None = None,
    label_membership_truncated: tuple[str, ...] = (),
    label_registry_complete: bool = False,
) -> str:
    payload = {
        "generation": generation,
        "findings": [
            (item.evidence_id, item.target_entity_id, item.relation, item.config_path)
            for item in findings
        ],
        "coverage": [(item.source_type, item.completeness, item.failed_item_count) for item in coverage],
        "automation_action_profiles": [
            (
                item.source_id,
                item.source_entity_id,
                item.evidence_fingerprint,
                item.complete,
                item.truncated,
            )
            for item in automation_action_profiles
        ],
        "automation_read_failures": [
            (item.source_id, item.source_entity_id, item.reason_code)
            for item in automation_read_failures
        ],
        "dynamic_references": sorted(
            dynamic_reference_fingerprint(item)
            for item in dynamic_references
        ),
        "dynamic_reference_overflow": {
            "count": max(0, int(dynamic_reference_overflow_count)),
            "fingerprint": dynamic_reference_overflow_fingerprint,
        },
        "label_resolution": {
            "complete": bool(label_registry_complete),
            "membership_fingerprints": dict(
                sorted((label_membership_fingerprints or {}).items())
            ),
            "truncated": sorted(label_membership_truncated),
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
