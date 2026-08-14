"""Bounded dependency-risk evidence for exact input-boolean actions."""

from __future__ import annotations

from typing import Any

from ..dependency.index import DependencyIndex
from ..dependency.models import DependencyIndexSnapshot
from .models import ChangeRiskAssessment, RiskLevel
from .normalize import stable_hash


HELPER_DEPENDENCY_RISK_MODEL = "helper-dependency-risk-v1"
MAX_RELEVANT_AUTOMATIONS = 50
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
        "consequential_downstream_object_ids": [],
        "downstream_profiles": [],
        "unresolved_dynamic_reference_count": 0,
        "truncated": completeness == "truncated",
    }
    return {**material, "evidence_fingerprint": stable_hash(material)}


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
    dynamic_count = len(snapshot.dynamic_references)
    relevant_source_ids = sorted(sources)
    truncated = len(relevant_source_ids) > MAX_RELEVANT_AUTOMATIONS
    selected_source_ids = relevant_source_ids[:MAX_RELEVANT_AUTOMATIONS]
    coverage = {
        item.source_type: item.completeness
        for item in snapshot.coverage
        if item.source_type in {"automation", "blueprint"}
    }
    coverage_complete = all(
        coverage.get(source_type) == "complete"
        for source_type in ("automation", "blueprint")
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
        if profile is None:
            missing_profile = True
            identity = _safe_automation_identity(source_id, None)
            projected = {
                "automation_id": identity,
                "relationships": sorted(sources[source_id]),
                "physical_consequence": "unknown",
                "complete": False,
                "truncated": False,
                "action_domains": [],
                "services": [],
                "reason_codes": ["action_profile_unavailable"],
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
                "relationships": sorted(sources[source_id]),
                "physical_consequence": profile.physical_consequence,
                "complete": profile.complete,
                "truncated": profile.truncated,
                "action_domains": list(profile.action_domains),
                "services": list(profile.services),
                "reason_codes": list(profile.reason_codes),
                "profile_fingerprint": profile.evidence_fingerprint,
            }
            profile_incomplete = profile_incomplete or not profile.complete
            truncated = truncated or profile.truncated
            if _CONSEQUENCE_RANK.get(
                profile.physical_consequence, 1
            ) > _CONSEQUENCE_RANK.get(observed_consequence, 0):
                observed_consequence = profile.physical_consequence
        downstream_profiles.append(projected)

    evidence_complete = bool(
        coverage_complete
        and fresh
        and not dynamic_count
        and not missing_profile
        and not profile_incomplete
        and not truncated
    )
    if not evidence_complete and observed_consequence == "none":
        observed_consequence = "unknown"
    if truncated:
        completeness = "truncated"
    elif evidence_complete:
        completeness = "complete"
    elif any(value in {"unsupported", "unavailable"} for value in coverage.values()):
        completeness = "unsupported"
    elif not fresh:
        completeness = "stale"
    else:
        completeness = "partial"

    downstream_profiles.sort(key=lambda item: item["automation_id"])
    relevant_ids = [
        item["automation_id"] for item in downstream_profiles
    ]
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
        "consequential_downstream_object_ids": consequential_ids,
        "downstream_profiles": downstream_profiles,
        "unresolved_dynamic_reference_count": dynamic_count,
        "truncated": truncated,
    }
    return {**material, "evidence_fingerprint": stable_hash(material)}


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
