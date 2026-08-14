"""Bounded dependency-risk evidence for exact input-boolean actions."""

from __future__ import annotations

import re
from typing import Any

from ..dependency.index import DependencyIndex
from ..dependency.models import DependencyIndexSnapshot
from .models import ChangeRiskAssessment, RiskLevel
from .normalize import stable_hash


HELPER_DEPENDENCY_RISK_MODEL = "helper-dependency-risk-v2"
MAX_RELEVANT_AUTOMATIONS = 50
MAX_RELEVANT_DYNAMIC_REFERENCES = 32
MAX_UNREADABLE_AUTOMATIONS = 50
_AUTOMATION_RESOURCE_ID = re.compile(
    r"^[a-z0-9][a-z0-9_.-]{0,255}$"
)
_CONSEQUENCE_RANK = {
    "none": 0,
    "unknown": 1,
    "direct": 2,
    "safety_critical": 3,
}
_NON_CAUSAL_RELATIONS = frozenset(
    {"action_data", "action_target", "service_target"}
)


def _safe_automation_identity(
    source_id: str, source_entity_id: str | None
) -> str:
    if (
        isinstance(source_entity_id, str)
        and source_entity_id.startswith("automation.")
        and len(source_entity_id) <= 255
        and source_entity_id == source_entity_id.strip().lower()
    ):
        return source_entity_id
    return "automation_source_" + stable_hash(str(source_id))[:16]


def _safe_automation_resource_identity(
    source_id: str, source_entity_id: str | None
) -> str | None:
    candidate = source_id
    if source_id == source_entity_id and source_id.startswith("automation."):
        candidate = source_id.removeprefix("automation.")
    if (
        isinstance(candidate, str)
        and candidate == candidate.strip().lower()
        and _AUTOMATION_RESOURCE_ID.fullmatch(candidate)
    ):
        return candidate
    return None


def _dynamic_reference_is_target_relevant(
    item: Any,
    *,
    source_ids: set[str],
    entity_id: str,
) -> bool:
    if item.source_type != "automation":
        return False
    if item.source_id in source_ids:
        return True
    excerpt = item.excerpt.lower() if isinstance(item.excerpt, str) else ""
    if entity_id in excerpt:
        return True
    possible_domains = getattr(item, "possible_entity_domains", None)
    if (
        isinstance(possible_domains, tuple)
        and possible_domains
        and "input_boolean" not in possible_domains
    ):
        return False
    # An unresolved automation reference is target-relevant unless extraction
    # proved that every possible entity belongs to another exact domain.
    return True


def _bounded_dynamic_fingerprints(items: list[Any]) -> tuple[list[str], bool]:
    values = sorted(
        {
            stable_hash(
                {
                    "source_id": item.source_id,
                    "source_entity_id": item.source_entity_id,
                    "config_path": item.config_path,
                    "warning": item.warning,
                    "excerpt": item.excerpt,
                    "possible_entity_domains": list(
                        item.possible_entity_domains
                    )
                    if isinstance(item.possible_entity_domains, tuple)
                    else None,
                }
            )
            for item in items
        }
    )
    if len(values) <= MAX_RELEVANT_DYNAMIC_REFERENCES:
        return values, False
    retained = values[: MAX_RELEVANT_DYNAMIC_REFERENCES - 1]
    retained.append("overflow_sha256:" + stable_hash(values))
    return retained, True


def _bounded_unreadable_automation_evidence(
    snapshot: DependencyIndexSnapshot,
    coverage_failed_count: int,
) -> tuple[list[str], int, str, bool]:
    projected = sorted(
        {
            _safe_automation_identity(
                item.source_id, item.source_entity_id
            )
            for item in snapshot.automation_read_failures
        },
        key=lambda item: item.encode("utf-8"),
    )
    count = max(coverage_failed_count, len(projected))
    fingerprint = stable_hash(
        {
            "count": count,
            "failures": [
                {
                    "automation_id": _safe_automation_identity(
                        item.source_id, item.source_entity_id
                    ),
                    "reason_code": item.reason_code,
                }
                for item in sorted(
                    snapshot.automation_read_failures,
                    key=lambda candidate: (
                        candidate.source_entity_id or "",
                        candidate.source_id,
                        candidate.reason_code,
                    ),
                )
            ],
        }
    )
    clipped = len(projected) > MAX_UNREADABLE_AUTOMATIONS
    if clipped:
        projected = [
            *projected[: MAX_UNREADABLE_AUTOMATIONS - 1],
            "overflow_sha256:" + fingerprint,
        ]
    elif count and not projected:
        projected = ["unidentified_sha256:" + fingerprint]
    return projected, count, fingerprint, clipped


def _causal_automation_sources(
    snapshot: DependencyIndexSnapshot, entity_id: str
) -> dict[str, set[str]]:
    findings = [
        item
        for item in snapshot.findings
        if item.target_entity_id == entity_id
        and item.source_type == "automation"
    ]
    resolved_blueprints = {
        item.source_id
        for item in findings
        if item.relation == "blueprint_resolved_role"
    }
    sources: dict[str, set[str]] = {}
    for item in findings:
        if item.relation in _NON_CAUSAL_RELATIONS:
            continue
        if (
            item.relation == "blueprint_input"
            and item.source_id in resolved_blueprints
        ):
            continue
        if item.relation == "blueprint_resolved_role" and not any(
            marker in item.config_path
            for marker in (
                "condition",
                "trigger",
                "wait_for_trigger",
            )
        ):
            continue
        sources.setdefault(item.source_id, set()).add(item.relation)
    return sources


def _failed_binding(entity_id: str, completeness: str) -> dict[str, Any]:
    material = {
        "model": HELPER_DEPENDENCY_RISK_MODEL,
        "entity_id": entity_id,
        "completeness": completeness,
        "evidence_complete": False,
        "execution_eligible": False,
        "physical_consequence": "unknown",
        "relevant_downstream_object_ids": [],
        "downstream_automation_resource_ids": [],
        "consequential_downstream_object_ids": [],
        "downstream_profiles": [],
        "target_relevant_dynamic_reference_count": 0,
        "target_relevant_dynamic_reference_fingerprints": [],
        "unresolved_dynamic_reference_count": 0,
        "unreadable_automation_count": 0,
        "unreadable_automation_ids": [],
        "unreadable_automation_fingerprint": stable_hash([]),
        "truncated": completeness == "truncated",
    }
    return {
        **material,
        "unrelated_dynamic_reference_count": 0,
        "evidence_fingerprint": stable_hash(material),
    }


def build_helper_dependency_risk_binding(
    snapshot: DependencyIndexSnapshot,
    *,
    entity_id: str,
    index_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Build one normalized, bounded, target-specific approval binding."""

    sources = _causal_automation_sources(snapshot, entity_id)
    profiles_by_source = {
        item.source_id: item
        for item in snapshot.automation_action_profiles
    }
    relevant_source_ids = sorted(sources)
    truncated = len(relevant_source_ids) > MAX_RELEVANT_AUTOMATIONS
    selected_source_ids = relevant_source_ids[:MAX_RELEVANT_AUTOMATIONS]
    relevant_dynamic = [
        item
        for item in snapshot.dynamic_references
        if _dynamic_reference_is_target_relevant(
            item,
            source_ids=set(relevant_source_ids),
            entity_id=entity_id,
        )
    ]
    dynamic_fingerprints, dynamic_truncated = (
        _bounded_dynamic_fingerprints(relevant_dynamic)
    )
    truncated = truncated or dynamic_truncated
    automation_coverage = next(
        (
            item
            for item in snapshot.coverage
            if item.source_type == "automation"
        ),
        None,
    )
    automation_source_unavailable = bool(
        automation_coverage is None
        or automation_coverage.completeness
        in {"unavailable", "unsupported"}
    )
    coverage_failed_count = (
        automation_coverage.failed_item_count
        if automation_coverage is not None
        else 0
    )
    (
        unreadable_automation_ids,
        unreadable_automation_count,
        unreadable_automation_fingerprint,
        unreadable_automations_clipped,
    ) = _bounded_unreadable_automation_evidence(
        snapshot, coverage_failed_count
    )
    automation_read_uncertainty = unreadable_automation_count > 0
    index_payload_truncated = any(
        "exceeded the bounded index payload" in warning
        for item in snapshot.coverage
        for warning in item.warnings
    )
    fresh = (
        index_metadata.get("freshness") == "current"
        and index_metadata.get("evidence_stale") is not True
        and index_metadata.get("invalidated") is not True
    )

    downstream_profiles: list[dict[str, Any]] = []
    missing_profile = False
    profile_incomplete = False
    observed_consequence = "none"
    for source_id in selected_source_ids:
        profile = profiles_by_source.get(source_id)
        resource_id = _safe_automation_resource_identity(
            source_id,
            profile.source_entity_id if profile is not None else None,
        )
        if profile is None:
            missing_profile = True
            identity = _safe_automation_identity(source_id, None)
            projected = {
                "automation_id": identity,
                "automation_resource_id": resource_id,
                "relationships": sorted(sources[source_id]),
                "physical_consequence": "unknown",
                "complete": False,
                "truncated": False,
                "action_domains": [],
                "services": [],
                "reason_codes": ["action_profile_unavailable"],
                "effect_projection_model": "unavailable",
                "effect_targets": [],
                "effect_data": [],
                "effect_structure_fingerprint": stable_hash(
                    {"source_id": source_id, "structure": "unavailable"}
                ),
                "effect_projection_fingerprint": stable_hash(
                    {"source_id": source_id, "effect": "unavailable"}
                ),
                "effect_projection_clipped": False,
                "profile_fingerprint": stable_hash(
                    {"source_id": source_id, "missing": True}
                ),
            }
        else:
            identity = _safe_automation_identity(
                profile.source_id, profile.source_entity_id
            )
            projected = {
                "automation_id": identity,
                "automation_resource_id": resource_id,
                "relationships": sorted(sources[source_id]),
                "physical_consequence": profile.physical_consequence,
                "complete": profile.complete,
                "truncated": profile.truncated,
                "action_domains": list(profile.action_domains),
                "services": list(profile.services),
                "reason_codes": list(profile.reason_codes),
                "effect_projection_model": (
                    profile.effect_projection_model
                ),
                "effect_targets": list(profile.effect_targets),
                "effect_data": list(profile.effect_data),
                "effect_structure_fingerprint": (
                    profile.effect_structure_fingerprint
                ),
                "effect_projection_fingerprint": (
                    profile.effect_projection_fingerprint
                ),
                "effect_projection_clipped": (
                    profile.effect_projection_clipped
                ),
                "profile_fingerprint": profile.evidence_fingerprint,
            }
            profile_incomplete = bool(
                profile_incomplete
                or not profile.complete
                or resource_id is None
            )
            truncated = truncated or profile.truncated
            if _CONSEQUENCE_RANK.get(
                profile.physical_consequence, 1
            ) > _CONSEQUENCE_RANK.get(observed_consequence, 0):
                observed_consequence = profile.physical_consequence
        downstream_profiles.append(projected)

    truncated = truncated or unreadable_automations_clipped
    evidence_complete = bool(
        fresh
        and not relevant_dynamic
        and not missing_profile
        and not profile_incomplete
        and not truncated
        and not automation_source_unavailable
        and not automation_read_uncertainty
        and not index_payload_truncated
    )
    if not evidence_complete and observed_consequence == "none":
        observed_consequence = "unknown"
    if truncated:
        completeness = "truncated"
    elif evidence_complete:
        completeness = "complete"
    elif automation_source_unavailable:
        completeness = "unsupported"
    elif not fresh:
        completeness = "stale"
    else:
        completeness = "partial"

    downstream_profiles.sort(key=lambda item: item["automation_id"])
    relevant_ids = [
        item["automation_id"] for item in downstream_profiles
    ]
    resource_ids = sorted(
        {
            item["automation_resource_id"]
            for item in downstream_profiles
            if isinstance(item.get("automation_resource_id"), str)
        }
    )
    consequential_ids = [
        item["automation_id"]
        for item in downstream_profiles
        if item["physical_consequence"]
        in {"direct", "safety_critical"}
    ]
    material = {
        "model": HELPER_DEPENDENCY_RISK_MODEL,
        "entity_id": entity_id,
        "completeness": completeness,
        "evidence_complete": evidence_complete,
        "execution_eligible": evidence_complete,
        "physical_consequence": observed_consequence,
        "relevant_downstream_object_ids": relevant_ids,
        "downstream_automation_resource_ids": resource_ids,
        "consequential_downstream_object_ids": consequential_ids,
        "downstream_profiles": downstream_profiles,
        "target_relevant_dynamic_reference_count": len(
            relevant_dynamic
        ),
        "target_relevant_dynamic_reference_fingerprints": (
            dynamic_fingerprints
        ),
        "unresolved_dynamic_reference_count": len(relevant_dynamic),
        "unreadable_automation_count": unreadable_automation_count,
        "unreadable_automation_ids": unreadable_automation_ids,
        "unreadable_automation_fingerprint": (
            unreadable_automation_fingerprint
        ),
        "truncated": truncated,
    }
    return {
        **material,
        "unrelated_dynamic_reference_count": max(
            0, len(snapshot.dynamic_references) - len(relevant_dynamic)
        ),
        "coverage_diagnostics": [
            {
                "source_type": item.source_type,
                "completeness": item.completeness,
                "failed_item_count": item.failed_item_count,
                "warning_count": len(item.warnings),
            }
            for item in snapshot.coverage
            if item.source_type in {"automation", "blueprint"}
        ],
        "evidence_fingerprint": stable_hash(material),
    }


class HelperDependencyRiskService:
    """Read target-specific risk from the shared dependency index."""

    def __init__(self, index: DependencyIndex):
        self.index = index

    async def assess(
        self, entity_id: str, *, refresh: bool = True
    ) -> dict[str, Any]:
        try:
            snapshot, rebuilt, lookup_duration_ms = await self.index.get(
                refresh=refresh
            )
            metadata = self.index.evidence_metadata(snapshot)
        except Exception as exc:
            return {
                "binding": _failed_binding(entity_id, "failed"),
                "provenance": {
                    "provider": "dependency_index",
                    "completeness": "failed",
                    "failure_category": type(exc).__name__[:64],
                    "fallback": "none",
                    "fallback_occurred": False,
                },
            }
        binding = build_helper_dependency_risk_binding(
            snapshot,
            entity_id=entity_id,
            index_metadata=metadata,
        )
        return {
            "binding": binding,
            "provenance": {
                "provider": "dependency_index",
                "completeness": binding["completeness"],
                "generation": snapshot.generation,
                "fingerprint": snapshot.fingerprint,
                "built_at": snapshot.built_at,
                "freshness": metadata.get("freshness"),
                "evidence_age_seconds": metadata.get(
                    "evidence_age_seconds"
                ),
                "refreshed": rebuilt,
                "lookup_duration_ms": round(lookup_duration_ms, 3),
                "fallback": "none",
                "fallback_occurred": False,
            },
        }


async def read_runtime_helper_dependency_risk(
    entity_id: str, *, refresh: bool = True
) -> dict[str, Any]:
    from ..dependency import DEPENDENCY_ANALYSIS

    try:
        index = DEPENDENCY_ANALYSIS.require().index
    except Exception as exc:
        return {
            "binding": _failed_binding(entity_id, "failed"),
            "provenance": {
                "provider": "dependency_index",
                "completeness": "failed",
                "failure_category": type(exc).__name__[:64],
                "fallback": "none",
                "fallback_occurred": False,
            },
        }
    return await HelperDependencyRiskService(index).assess(
        entity_id, refresh=refresh
    )


def helper_dependency_risk_assessment(
    evidence: dict[str, Any],
) -> ChangeRiskAssessment:
    binding = evidence.get("binding")
    provenance = evidence.get("provenance")
    if not isinstance(binding, dict) or not isinstance(provenance, dict):
        binding = _failed_binding("input_boolean.unknown", "failed")
        provenance = {
            "provider": "dependency_index",
            "completeness": "failed",
        }
    complete = binding.get("evidence_complete") is True
    consequence = binding.get("physical_consequence")
    if complete and consequence == "none":
        level = RiskLevel.LOW
        reasons = [
            "Complete bounded dependency evidence found no consequential downstream automation path.",
        ]
        warnings: list[str] = []
    elif complete:
        level = RiskLevel.HIGH
        reasons = [
            "Bounded dependency evidence found a materially consequential downstream automation path.",
        ]
        warnings = []
    else:
        level = RiskLevel.HIGH
        reasons = [
            "Dependency evidence is incomplete, so low risk cannot be concluded.",
        ]
        warnings = [
            "Fresh complete dependency evidence is required before dispatch.",
        ]
    risk_evidence = [
        {
            "field": "operation",
            "trigger": "exact_input_boolean_state",
        },
        {
            "field": "dependency_index",
            "trigger": "helper_dependency_risk_evidence",
            "completeness": str(binding.get("completeness")),
            "physical_consequence": str(consequence),
            "evidence_fingerprint": str(
                binding.get("evidence_fingerprint")
            ),
            "generation": str(provenance.get("generation", "unknown")),
            "index_fingerprint": str(
                provenance.get("fingerprint", "unknown")
            ),
        },
    ]
    return ChangeRiskAssessment(
        level=level,
        reasons=reasons,
        apply_allowed=complete,
        evidence=risk_evidence,
        warnings=warnings,
    )


__all__ = [
    "HELPER_DEPENDENCY_RISK_MODEL",
    "HelperDependencyRiskService",
    "build_helper_dependency_risk_binding",
    "helper_dependency_risk_assessment",
    "read_runtime_helper_dependency_risk",
]
