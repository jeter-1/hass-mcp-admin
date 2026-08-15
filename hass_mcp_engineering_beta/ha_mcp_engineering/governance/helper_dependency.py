"""Bounded dependency-risk evidence for exact input-boolean actions."""

from __future__ import annotations

import re
from typing import Any

from ..dependency.index import DependencyIndex
from ..dependency.models import (
    DependencyIndexSnapshot,
    dynamic_reference_fingerprint,
)
from .models import ChangeRiskAssessment, RiskLevel
from .normalize import stable_hash


HELPER_DEPENDENCY_RISK_MODEL = "helper-dependency-risk-v2"
MAX_RELEVANT_AUTOMATIONS = 50
MAX_RELEVANT_DYNAMIC_REFERENCES = 32
MAX_DYNAMIC_REFERENCES_EVALUATED = 64
MAX_NON_ENTITY_DYNAMIC_REFERENCES = 64
MAX_RESOLVED_CANDIDATES_PER_EXPRESSION = 128
MAX_TOTAL_RESOLVED_CANDIDATES = 512
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


def _resolved_dynamic_reference_evidence(
    item: Any,
    *,
    snapshot: DependencyIndexSnapshot,
    entity_id: str,
) -> dict[str, Any]:
    direct_candidates = sorted(
        {
            value
            for value in getattr(item, "possible_entity_ids", ())
            if isinstance(value, str)
        },
        key=lambda value: value.encode("utf-8"),
    )
    labels = sorted(
        {
            value
            for value in getattr(
                item, "literal_label_selectors", ()
            )
            if isinstance(value, str)
        },
        key=lambda value: value.encode("utf-8"),
    )
    possible_domains = getattr(item, "possible_entity_domains", None)
    exact_domains = (
        sorted(set(possible_domains))
        if isinstance(possible_domains, tuple)
        else []
    )
    complete = bool(
        getattr(item, "candidate_resolution_complete", False)
    )
    limit_exceeded = bool(
        getattr(
            item, "candidate_resolution_limit_exceeded", False
        )
    )
    reference_kind = str(
        getattr(item, "reference_kind", "dynamic_entity_selector")
    )
    claimed_entity_selector_present = bool(
        getattr(item, "entity_selector_present", True)
    )
    proven_non_entity_template = bool(
        not claimed_entity_selector_present
        and reference_kind == "ordinary_dynamic_template"
        and not direct_candidates
        and not labels
        and not exact_domains
        and complete
        and not limit_exceeded
    )
    entity_selector_present = not proven_non_entity_template
    if not entity_selector_present:
        candidates: list[str] = []
        target_membership = "not_applicable"
        reason_codes: list[str] = []
        label_fingerprints: dict[str, str] = {}
        expression_fingerprint = dynamic_reference_fingerprint(item)
        configuration_path = str(item.config_path)
        if len(configuration_path.encode("utf-8")) > 256:
            configuration_path = (
                "oversized_sha256:" + stable_hash(configuration_path)
            )
        evidence = {
            "source_object_id": _safe_automation_identity(
                item.source_id, item.source_entity_id
            ),
            "configuration_path": configuration_path,
            "expression_fingerprint": expression_fingerprint,
            "reference_kind": reference_kind,
            "entity_selector_present": False,
            "resolution_kind": str(
                getattr(
                    item,
                    "candidate_resolution_kind",
                    "ordinary_dynamic_template",
                )
            ),
            "candidate_entity_ids": candidates,
            "candidate_set_fingerprint": stable_hash(candidates),
            "explicit_candidate_fingerprint": stable_hash([]),
            "literal_label_selectors": [],
            "label_membership_fingerprints": label_fingerprints,
            "possible_entity_domains": [],
            "target_membership": target_membership,
            "complete": True,
            "truncated": False,
            "reason_codes": reason_codes,
        }
        return {
            **evidence,
            "evidence_fingerprint": stable_hash(evidence),
            "source_id": item.source_id,
        }
    reason_codes: list[str] = []
    label_fingerprints: dict[str, str] = {}
    label_candidates: set[str] = set()
    truncated_labels = set(snapshot.label_membership_truncated)
    if labels:
        if not snapshot.label_registry_complete:
            complete = False
            reason_codes.append("label_registry_evidence_incomplete")
        for selector in labels:
            membership = snapshot.label_memberships.get(selector)
            fingerprint = snapshot.label_membership_fingerprints.get(
                selector
            )
            if (
                membership is None
                or not isinstance(fingerprint, str)
                or selector in truncated_labels
            ):
                complete = False
                reason_codes.append(
                    "label_membership_evidence_incomplete"
                )
                continue
            label_candidates.update(membership)
            label_fingerprints[selector] = fingerprint

    candidates = sorted(
        set(direct_candidates).union(label_candidates),
        key=lambda value: value.encode("utf-8"),
    )
    if len(candidates) > MAX_RESOLVED_CANDIDATES_PER_EXPRESSION:
        candidates = candidates[
            :MAX_RESOLVED_CANDIDATES_PER_EXPRESSION
        ]
        complete = False
        limit_exceeded = True
        reason_codes.append("dynamic_reference_resolution_limit_exceeded")
    if limit_exceeded:
        complete = False
        reason_codes.append("dynamic_reference_resolution_limit_exceeded")

    # Preserve the Beta 37 exact-domain proof for its narrowly reviewed shape,
    # including historical in-memory test fixtures that predate the additive
    # candidate fields.
    proven_other_domain = bool(
        not limit_exceeded
        and exact_domains
        and "input_boolean" not in exact_domains
    )
    if proven_other_domain and not labels:
        complete = True
    if complete and candidates:
        target_membership = (
            "included" if entity_id in candidates else "excluded"
        )
    elif complete and proven_other_domain:
        target_membership = "excluded"
    else:
        target_membership = "unresolved"
        reason_codes.append("dynamic_reference_target_unresolved")

    expression_fingerprint = dynamic_reference_fingerprint(item)
    configuration_path = str(item.config_path)
    if len(configuration_path.encode("utf-8")) > 256:
        configuration_path = (
            "oversized_sha256:" + stable_hash(configuration_path)
        )
    evidence = {
        "source_object_id": _safe_automation_identity(
            item.source_id, item.source_entity_id
        ),
        "configuration_path": configuration_path,
        "expression_fingerprint": expression_fingerprint,
        "reference_kind": "dynamic_entity_selector",
        "entity_selector_present": True,
        "resolution_kind": str(
            getattr(item, "candidate_resolution_kind", "unresolved")
        ),
        "candidate_entity_ids": candidates,
        "candidate_set_fingerprint": stable_hash(candidates),
        "explicit_candidate_fingerprint": stable_hash(
            direct_candidates
        ),
        "literal_label_selectors": labels,
        "label_membership_fingerprints": dict(
            sorted(label_fingerprints.items())
        ),
        "possible_entity_domains": exact_domains,
        "target_membership": target_membership,
        "complete": complete,
        "truncated": limit_exceeded,
        "reason_codes": sorted(set(reason_codes)),
    }
    return {
        **evidence,
        "evidence_fingerprint": stable_hash(evidence),
        "source_id": item.source_id,
    }


def _bounded_dynamic_fingerprints(
    items: list[dict[str, Any]],
) -> tuple[list[str], bool]:
    values = sorted(
        {
            str(item["evidence_fingerprint"])
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
        "resolved_dynamic_reference_evidence": [],
        "resolved_target_dynamic_reference_count": 0,
        "non_entity_dynamic_reference_count": 0,
        "non_entity_dynamic_reference_fingerprints": [],
        "non_entity_dynamic_evaluation_overflow_count": 0,
        "non_entity_dynamic_evaluation_overflow_fingerprint": None,
        "unresolved_dynamic_reference_count": 0,
        "unreadable_automation_count": 0,
        "unreadable_automation_ids": [],
        "unreadable_automation_fingerprint": stable_hash([]),
        "dynamic_reference_overflow_count": 0,
        "dynamic_reference_overflow_fingerprint": None,
        "dynamic_evaluation_overflow_count": 0,
        "dynamic_evaluation_overflow_fingerprint": None,
        "dynamic_resolution_reason_codes": [],
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
    dynamic_reference_overflow_count = max(
        0,
        int(
            getattr(
                snapshot, "dynamic_reference_overflow_count", 0
            )
            or 0
        ),
    )
    dynamic_reference_overflow_fingerprint = getattr(
        snapshot, "dynamic_reference_overflow_fingerprint", None
    )
    if dynamic_reference_overflow_count and not isinstance(
        dynamic_reference_overflow_fingerprint, str
    ):
        dynamic_reference_overflow_fingerprint = stable_hash(
            {
                "count": dynamic_reference_overflow_count,
                "state": "fingerprint_unavailable",
            }
        )
    resolved_dynamic_evidence = sorted(
        (
            _resolved_dynamic_reference_evidence(
                item, snapshot=snapshot, entity_id=entity_id
            )
            for item in snapshot.dynamic_references
            if item.source_type == "automation"
        ),
        key=lambda item: item["evidence_fingerprint"],
    )
    non_entity_dynamic_evidence = [
        item
        for item in resolved_dynamic_evidence
        if item["entity_selector_present"] is False
    ]
    selector_dynamic_evidence = [
        item
        for item in resolved_dynamic_evidence
        if item["entity_selector_present"] is True
    ]
    retained_dynamic_evidence: list[dict[str, Any]] = []
    omitted_dynamic_evidence: list[dict[str, Any]] = []
    retained_candidate_count = 0
    for item in selector_dynamic_evidence:
        candidate_count = len(item["candidate_entity_ids"])
        if (
            len(retained_dynamic_evidence)
            >= MAX_DYNAMIC_REFERENCES_EVALUATED
            or retained_candidate_count + candidate_count
            > MAX_TOTAL_RESOLVED_CANDIDATES
        ):
            omitted_dynamic_evidence.append(item)
            continue
        retained_dynamic_evidence.append(item)
        retained_candidate_count += candidate_count
    dynamic_evaluation_overflow = len(omitted_dynamic_evidence)
    dynamic_evaluation_overflow_fingerprint = (
        stable_hash(
            [
                item["evidence_fingerprint"]
                for item in omitted_dynamic_evidence
            ]
        )
        if dynamic_evaluation_overflow
        else None
    )
    retained_non_entity_dynamic_evidence = (
        non_entity_dynamic_evidence[:MAX_NON_ENTITY_DYNAMIC_REFERENCES]
    )
    omitted_non_entity_dynamic_evidence = (
        non_entity_dynamic_evidence[MAX_NON_ENTITY_DYNAMIC_REFERENCES:]
    )
    non_entity_dynamic_overflow = len(
        omitted_non_entity_dynamic_evidence
    )
    non_entity_dynamic_overflow_fingerprint = (
        stable_hash(
            [
                item["evidence_fingerprint"]
                for item in omitted_non_entity_dynamic_evidence
            ]
        )
        if non_entity_dynamic_overflow
        else None
    )
    non_entity_dynamic_fingerprints, _ = _bounded_dynamic_fingerprints(
        non_entity_dynamic_evidence
    )
    resolved_dynamic_evidence = sorted(
        [
            *retained_dynamic_evidence,
            *retained_non_entity_dynamic_evidence,
        ],
        key=lambda item: item["evidence_fingerprint"],
    )
    for item in resolved_dynamic_evidence:
        if item["target_membership"] == "included":
            sources.setdefault(item["source_id"], set()).add(
                "template_dynamic_candidate"
            )
    relevant_source_ids = sorted(sources)
    truncated = bool(
        len(relevant_source_ids) > MAX_RELEVANT_AUTOMATIONS
        or dynamic_reference_overflow_count
        or dynamic_evaluation_overflow
    )
    selected_source_ids = relevant_source_ids[:MAX_RELEVANT_AUTOMATIONS]
    relevant_dynamic = [
        item
        for item in resolved_dynamic_evidence
        if item["target_membership"] == "unresolved"
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
        "resolved_dynamic_reference_evidence": [
            {
                key: value
                for key, value in item.items()
                if key != "source_id"
            }
            for item in resolved_dynamic_evidence
        ],
        "resolved_target_dynamic_reference_count": sum(
            item["target_membership"] == "included"
            for item in resolved_dynamic_evidence
        ),
        "non_entity_dynamic_reference_count": len(
            non_entity_dynamic_evidence
        ),
        "non_entity_dynamic_reference_fingerprints": (
            non_entity_dynamic_fingerprints
        ),
        "non_entity_dynamic_evaluation_overflow_count": (
            non_entity_dynamic_overflow
        ),
        "non_entity_dynamic_evaluation_overflow_fingerprint": (
            non_entity_dynamic_overflow_fingerprint
        ),
        "unresolved_dynamic_reference_count": len(relevant_dynamic),
        "unreadable_automation_count": unreadable_automation_count,
        "unreadable_automation_ids": unreadable_automation_ids,
        "unreadable_automation_fingerprint": (
            unreadable_automation_fingerprint
        ),
        "dynamic_reference_overflow_count": (
            dynamic_reference_overflow_count
        ),
        "dynamic_reference_overflow_fingerprint": (
            dynamic_reference_overflow_fingerprint
        ),
        "dynamic_evaluation_overflow_count": (
            dynamic_evaluation_overflow
        ),
        "dynamic_evaluation_overflow_fingerprint": (
            dynamic_evaluation_overflow_fingerprint
        ),
        "dynamic_resolution_reason_codes": (
            ["dynamic_reference_resolution_limit_exceeded"]
            if dynamic_evaluation_overflow
            or any(
                item["truncated"]
                for item in resolved_dynamic_evidence
            )
            else []
        ),
        "truncated": truncated,
    }
    return {
        **material,
        "unrelated_dynamic_reference_count": max(
            0,
            sum(
                item["target_membership"] == "excluded"
                for item in resolved_dynamic_evidence
            ),
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
