"""Bounded dependency graph, source coverage, and index snapshot models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any

from ..sanitization import sanitize_untrusted_data


SOURCE_TYPES = ("automation", "blueprint", "script", "scene", "group", "template", "dashboard")
COMPLETENESS_VALUES = {"complete", "partial", "unavailable", "unsupported", "not_requested"}
OBLIGATION_OUTCOMES = frozenset(
    {
        "exact_dependency",
        "proven_target_exclusion",
        "proven_dependency_neutral",
        "bounded_semantic_opaque",
        "coverage_failure",
    }
)
OBLIGATION_LEDGER_MODEL = "whole-template-obligation-ledger-v1"
TARGET_SELECTOR_SCOPES = frozenset(
    {
        "closed_finite_candidates",
        "closed_entity_domains",
        "dependency_neutral",
        "target_capable",
        "coverage_failure",
    }
)
# Selector evidence is derived from configuration and template literals, so a
# single value can be arbitrarily long and can carry secret-bearing material.
# Every obligation is bounded per value and in aggregate before it exists, and
# an oversized or sanitized value is replaced by a deterministic digest so
# drift is still detectable without persisting the original bytes.
MAX_OBLIGATION_VALUE_BYTES = 128
MAX_OBLIGATION_EXACT_AGGREGATE_BYTES = 8_192
MAX_OBLIGATION_SELECTOR_AGGREGATE_BYTES = 2_048
MAX_OBLIGATION_DOMAIN_AGGREGATE_BYTES = 1_024
MAX_OBLIGATION_CONTEXT_AGGREGATE_BYTES = 1_024
MAX_OBLIGATION_TEXT_BYTES = 256


def _evidence_digest(value: str) -> str:
    """Return a deterministic stand-in that preserves drift detection."""

    encoded = value.encode("utf-8", errors="replace")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()[:32]


def _bounded_evidence_values(
    values: tuple[str, ...] | list[str],
    *,
    aggregate_bytes: int,
) -> tuple[tuple[str, ...], bool]:
    """Bound and sanitize one evidence value list.

    Returns the retained values and whether target-specific detail was lost,
    either because a value was oversized, because sanitization replaced
    secret-bearing material, or because the aggregate bound dropped values.
    """

    retained: list[str] = []
    total = 0
    detail_lost = False
    dropped: list[str] = []
    for index, value in enumerate(values):
        original = value if isinstance(value, str) else str(value)
        safe = sanitize_untrusted_data(original).value
        if not isinstance(safe, str) or safe != original:
            safe = _evidence_digest(original)
            detail_lost = True
        if len(safe.encode("utf-8")) > MAX_OBLIGATION_VALUE_BYTES:
            safe = _evidence_digest(original)
            detail_lost = True
        encoded = len(safe.encode("utf-8"))
        if total + encoded > aggregate_bytes:
            dropped = [
                item if isinstance(item, str) else str(item)
                for item in list(values)[index:]
            ]
            detail_lost = True
            break
        total += encoded
        retained.append(safe)
    if dropped:
        # One terminal digest keeps the dropped set observable for drift
        # detection instead of letting truncation look like absence.
        retained.append("omitted:" + _evidence_digest("".join(dropped)))
    return tuple(retained), detail_lost


def _bounded_evidence_text(value: str | None) -> tuple[str | None, bool]:
    """Bound and sanitize one optional evidence string."""

    if value is None:
        return None, False
    original = value if isinstance(value, str) else str(value)
    safe = sanitize_untrusted_data(original).value
    if not isinstance(safe, str) or safe != original:
        return _evidence_digest(original), True
    if len(safe.encode("utf-8")) > MAX_OBLIGATION_TEXT_BYTES:
        return _evidence_digest(original), True
    return safe, False


@dataclass(frozen=True)
class DependencyObligation:
    """One terminal whole-template dependency-analysis obligation.

    The index stores target-independent terminals.  An exact finite candidate
    set is projected as ``exact_dependency`` for a matching helper and as
    ``proven_target_exclusion`` for a non-member by the helper-risk service.
    Raw template bodies are never persisted.
    """

    evidence_id: str
    source_type: str
    source_id: str
    config_path: str
    relation: str
    outcome: str
    obligation_kind: str
    reason_code: str
    semantic_category: str
    semantic_registry_version: str
    semantic_registry_fingerprint: str
    expression_fingerprint: str
    configuration_fingerprint: str
    exact_entity_ids: tuple[str, ...] = ()
    possible_entity_domains: tuple[str, ...] | None = None
    literal_selectors: tuple[str, ...] = ()
    source_entity_id: str | None = None
    source_name: str | None = None
    source_state: str | None = None
    external_template_name: str | None = None
    context_provenance: tuple[str, ...] = ()
    limit_exceeded: bool = False
    lock_projection: str = "none"
    # Target-independent proof describing the complete selector universe for
    # this obligation.  This is deliberately separate from ``outcome``:
    # value/effect semantics can be opaque while the analyzer still proves
    # that the operation cannot select a particular helper.  ``None`` exists
    # only as a constructor compatibility default and is normalized here.
    target_selector_scope: str | None = None
    # Set when bounding or sanitization replaced or dropped evidence, so a
    # reader can distinguish "no such value" from "value not retained".
    evidence_bounded: bool = False

    @property
    def coverage_failure_authority(self) -> bool:
        """Return whether any terminal field carries failure authority."""

        return bool(
            self.outcome == "coverage_failure"
            or self.limit_exceeded
            or self.lock_projection == "coverage_failure"
            or self.target_selector_scope == "coverage_failure"
        )

    def _normalize_coverage_failure_authority(self) -> None:
        """Make failure authority monotonic across every projection field."""

        if not self.coverage_failure_authority:
            return
        object.__setattr__(self, "outcome", "coverage_failure")
        object.__setattr__(
            self, "target_selector_scope", "coverage_failure"
        )
        object.__setattr__(self, "lock_projection", "coverage_failure")

    def __post_init__(self) -> None:
        if self.outcome not in OBLIGATION_OUTCOMES:
            raise ValueError("dependency obligation outcome is invalid")
        if self.lock_projection not in {
            "none",
            "exact",
            "conservative",
            "coverage_failure",
        }:
            raise ValueError("dependency obligation lock projection is invalid")
        self._normalize_coverage_failure_authority()
        scope = self.target_selector_scope
        if scope is None:
            if self.outcome == "proven_dependency_neutral":
                scope = "dependency_neutral"
            elif self.outcome == "proven_target_exclusion":
                scope = (
                    "closed_entity_domains"
                    if self.possible_entity_domains is not None
                    else "closed_finite_candidates"
                )
            elif self.outcome == "exact_dependency":
                scope = (
                    "closed_finite_candidates"
                    if self.exact_entity_ids
                    else "closed_entity_domains"
                    if self.possible_entity_domains is not None
                    else "target_capable"
                )
            else:
                scope = "target_capable"
            object.__setattr__(self, "target_selector_scope", scope)
        if scope not in TARGET_SELECTOR_SCOPES:
            raise ValueError("dependency obligation target scope is invalid")
        self._bind_bounded_evidence()
        # Bounding and sanitization can introduce failure authority after the
        # initial constructor normalization. Reapply the same canonical rule
        # so no retained exact prefix can restore actionability.
        self._normalize_coverage_failure_authority()

    def _bind_bounded_evidence(self) -> None:
        """Bound and sanitize evidence, classifying conservatively on loss.

        Bounding happens here so that no obligation can exist with unbounded
        or secret-bearing selector evidence, whichever module created it.
        Losing target-specific detail removes the basis for an exact or
        proven-exclusion claim, so the obligation is reclassified rather than
        silently truncated.
        """

        exact, exact_lost = _bounded_evidence_values(
            self.exact_entity_ids,
            aggregate_bytes=MAX_OBLIGATION_EXACT_AGGREGATE_BYTES,
        )
        selectors, selector_lost = _bounded_evidence_values(
            self.literal_selectors,
            aggregate_bytes=MAX_OBLIGATION_SELECTOR_AGGREGATE_BYTES,
        )
        context, context_lost = _bounded_evidence_values(
            self.context_provenance,
            aggregate_bytes=MAX_OBLIGATION_CONTEXT_AGGREGATE_BYTES,
        )
        if self.possible_entity_domains is None:
            domains: tuple[str, ...] | None = None
            domain_lost = False
        else:
            domains, domain_lost = _bounded_evidence_values(
                self.possible_entity_domains,
                aggregate_bytes=MAX_OBLIGATION_DOMAIN_AGGREGATE_BYTES,
            )
        external, external_lost = _bounded_evidence_text(
            self.external_template_name
        )
        source_name, name_lost = _bounded_evidence_text(self.source_name)
        source_state, state_lost = _bounded_evidence_text(self.source_state)
        object.__setattr__(self, "exact_entity_ids", exact)
        object.__setattr__(self, "literal_selectors", selectors)
        object.__setattr__(self, "context_provenance", context)
        object.__setattr__(self, "possible_entity_domains", domains)
        object.__setattr__(self, "external_template_name", external)
        object.__setattr__(self, "source_name", source_name)
        object.__setattr__(self, "source_state", source_state)
        bounded = any(
            (
                exact_lost,
                selector_lost,
                context_lost,
                domain_lost,
                external_lost,
                name_lost,
                state_lost,
            )
        )
        if not bounded:
            return
        object.__setattr__(self, "evidence_bounded", True)
        # Target-specific detail is what an exact terminal or a proven
        # exclusion rests on. Without it the obligation is coverage-failed;
        # retained prefixes remain diagnostic and cannot narrow its lock.
        target_detail_lost = bool(
            exact_lost or selector_lost or domain_lost or context_lost
        )
        if target_detail_lost:
            object.__setattr__(self, "limit_exceeded", True)
            object.__setattr__(self, "outcome", "coverage_failure")
            object.__setattr__(
                self, "target_selector_scope", "coverage_failure"
            )
            object.__setattr__(
                self, "lock_projection", "coverage_failure"
            )


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
    analysis_complete: bool = True
    semantic_complete: bool = True
    presentation_truncated: bool = False
    processing_limit_exceeded: bool = False
    processing_limit_reason: str | None = None
    processing_observed_action_step_count: int = 0
    processing_action_step_limit: int = 0
    processing_action_depth_limit: int = 0
    processing_observed_effect_node_count: int = 0
    processing_effect_node_limit: int = 0
    processing_effect_depth_limit: int = 0
    processing_overflow_fingerprint: str | None = None
    action_domain_count: int = 0
    action_domains_fingerprint: str = ""
    service_count: int = 0
    services_fingerprint: str = ""
    reason_code_count: int = 0
    reason_codes_fingerprint: str = ""
    effect_target_count: int = 0
    effect_targets_fingerprint: str = ""
    effect_data_count: int = 0
    effect_data_fingerprint: str = ""


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
    # The legacy finding/dynamic-reference projections remain public
    # compatibility evidence and may be truncated independently of the
    # authoritative obligation ledger.  Keep the two coverage contracts
    # explicit so helper governance never mistakes compatibility-payload
    # truncation for lost ledger evidence (or the reverse).
    obligation_ledger_completeness: str | None = None
    obligation_ledger_failed_item_count: int = 0

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
            "obligation_ledger_completeness": (
                self.obligation_ledger_completeness
            ),
            "obligation_ledger_failed_item_count": max(
                0, int(self.obligation_ledger_failed_item_count)
            ),
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
    label_membership_complete: dict[str, bool] = field(
        default_factory=dict
    )
    label_membership_truncated: tuple[str, ...] = ()
    label_registry_complete: bool = False
    obligations: list[DependencyObligation] = field(default_factory=list)
    obligation_ledger_model: str | None = None
    # The running Home Assistant version observed during this scan, and how
    # that observation went.  The reviewed template semantics are only valid
    # for supported releases, so evidence must carry the version it was
    # produced against rather than leaving it unbound.
    home_assistant_version: str | None = None
    home_assistant_version_status: str = "unavailable"


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
    label_membership_complete: dict[str, bool] = field(
        default_factory=dict
    )
    label_membership_truncated: tuple[str, ...] = ()
    label_registry_complete: bool = False
    obligations: tuple[DependencyObligation, ...] = ()
    obligation_overflow_count: int = 0
    obligation_overflow_fingerprint: str | None = None
    obligation_ledger_model: str | None = None
    home_assistant_version: str | None = None
    home_assistant_version_status: str = "unavailable"
    # The source-read epoch in force when this snapshot's provider scan began.
    # A governed post-lock refresh is satisfied only by a snapshot whose read
    # began at or after the fence it opened.  It is deliberately excluded from
    # the snapshot fingerprint: it describes when evidence was read, not what
    # the evidence says, and approval binding must not churn on fences.
    source_epoch: int = 0


def obligation_material(item: DependencyObligation) -> dict[str, Any]:
    """Return bounded deterministic obligation material."""

    return {
        "evidence_id": item.evidence_id,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "source_entity_id": item.source_entity_id,
        "source_name": item.source_name,
        "source_state": item.source_state,
        "config_path": item.config_path,
        "relation": item.relation,
        "outcome": item.outcome,
        "obligation_kind": item.obligation_kind,
        "reason_code": item.reason_code,
        "semantic_category": item.semantic_category,
        "semantic_registry_version": item.semantic_registry_version,
        "semantic_registry_fingerprint": (
            item.semantic_registry_fingerprint
        ),
        "expression_fingerprint": item.expression_fingerprint,
        "configuration_fingerprint": item.configuration_fingerprint,
        "exact_entity_ids": list(item.exact_entity_ids),
        "possible_entity_domains": (
            list(item.possible_entity_domains)
            if item.possible_entity_domains is not None
            else None
        ),
        "literal_selectors": list(item.literal_selectors),
        "external_template_name": item.external_template_name,
        "context_provenance": list(item.context_provenance),
        "limit_exceeded": item.limit_exceeded,
        "lock_projection": item.lock_projection,
        "target_selector_scope": item.target_selector_scope,
        "evidence_bounded": item.evidence_bounded,
    }


def obligation_fingerprint(item: DependencyObligation) -> str:
    encoded = json.dumps(
        obligation_material(item), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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
    label_membership_complete: dict[str, bool] | None = None,
    label_membership_truncated: tuple[str, ...] = (),
    label_registry_complete: bool = False,
    obligations: list[DependencyObligation] | tuple[
        DependencyObligation, ...
    ] = (),
    obligation_overflow_count: int = 0,
    obligation_overflow_fingerprint: str | None = None,
    obligation_ledger_model: str | None = None,
    home_assistant_version: str | None = None,
    home_assistant_version_status: str = "unavailable",
) -> str:
    payload = {
        "generation": generation,
        "findings": [
            (item.evidence_id, item.target_entity_id, item.relation, item.config_path)
            for item in findings
        ],
        "coverage": [
            (
                item.source_type,
                item.completeness,
                item.failed_item_count,
                item.obligation_ledger_completeness,
                item.obligation_ledger_failed_item_count,
            )
            for item in coverage
        ],
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
            "selector_complete": dict(
                sorted((label_membership_complete or {}).items())
            ),
            "membership_fingerprints": dict(
                sorted((label_membership_fingerprints or {}).items())
            ),
            "truncated": sorted(label_membership_truncated),
        },
        "obligations": sorted(
            obligation_fingerprint(item) for item in obligations
        ),
        "obligation_overflow": {
            "count": max(0, int(obligation_overflow_count)),
            "fingerprint": obligation_overflow_fingerprint,
        },
        "obligation_ledger_model": obligation_ledger_model,
        # The reviewed semantics are version-scoped, so evidence produced
        # against a different running release is different evidence.
        "home_assistant_version": home_assistant_version,
        "home_assistant_version_status": home_assistant_version_status,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
