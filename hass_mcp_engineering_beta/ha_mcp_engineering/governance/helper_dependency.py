"""Bounded dependency-risk evidence for exact input-boolean actions."""

from __future__ import annotations

import re
from typing import Any

from ..dependency.index import DependencyIndex
from ..dependency.models import (
    DependencyObligation,
    DependencyIndexSnapshot,
    OBLIGATION_LEDGER_MODEL,
    dynamic_reference_fingerprint,
    obligation_fingerprint,
)
from .models import ChangeRiskAssessment, RiskLevel
from ..dependency.semantic_registry import (
    supported_home_assistant_versions,
)
from .normalize import stable_hash


HELPER_DEPENDENCY_RISK_MODEL = "helper-dependency-risk-v6"
# Compatibility: persisted bindings from these models stay readable, remain
# projectable for review, and keep readback-first recovery available.  Being
# readable is not authority to execute.
HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS = frozenset(
    {
        "helper-dependency-risk-v2",
        "helper-dependency-risk-v3",
        "helper-dependency-risk-v4",
        "helper-dependency-risk-v5",
        HELPER_DEPENDENCY_RISK_MODEL,
    }
)
# Execution authority: only current-model evidence may authorize approval,
# lock projection, or dispatch.  An older compatible model describes a
# superseded dependency question and requires an explicit replan; it must
# never be presented as directly executable.
HELPER_DEPENDENCY_RISK_EXECUTION_MODELS = frozenset(
    {HELPER_DEPENDENCY_RISK_MODEL}
)
MAX_RELEVANT_AUTOMATIONS = 50
MAX_RELEVANT_OBLIGATIONS = 256
MAX_RELEVANT_DYNAMIC_REFERENCES = 32
MAX_DYNAMIC_REFERENCES_EVALUATED = 64
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
        "coverage_complete": False,
        "semantic_precision": "coverage_failure",
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
        "unresolved_dynamic_reference_count": 0,
        "unreadable_automation_count": 0,
        "unreadable_automation_ids": [],
        "unreadable_automation_fingerprint": stable_hash([]),
        "dynamic_reference_overflow_count": 0,
        "dynamic_reference_overflow_fingerprint": None,
        "dynamic_evaluation_overflow_count": 0,
        "dynamic_evaluation_overflow_fingerprint": None,
        "dynamic_resolution_reason_codes": [],
        "opaque_obligation_count": 0,
        "coverage_failure_count": 1,
        "obligation_evidence": [],
        "obligation_overflow_count": 0,
        "obligation_overflow_fingerprint": None,
        "dependency_lock_projection": {
            "exact_helper_dependency": True,
            "conservative_helper_dependency": True,
            "automation_resource_ids": [],
            "custom_template_reload": False,
        },
        "truncated": completeness == "truncated",
    }
    return {
        **material,
        "unrelated_dynamic_reference_count": 0,
        "evidence_fingerprint": stable_hash(material),
    }


def _obligation_targets_helper(
    item: DependencyObligation, entity_id: str
) -> str:
    """Project one target-independent terminal to one exact helper.

    The obligation ledger is authoritative for semantic coverage.  A finite
    exact-dependency candidate set and a non-``None`` domain set are complete
    target-selection evidence by that contract.  Missing candidate/domain
    proof remains opaque; it is never treated as an exclusion merely because
    the target was not observed.
    """

    # Retained candidate material can accompany a failed or clipped analysis.
    # Such material is diagnostic only and can never restore target authority.
    if item.outcome == "coverage_failure" or item.limit_exceeded:
        return "coverage_failure"
    if (
        item.obligation_kind == "structured_entity_reference"
        and item.relation in _NON_CAUSAL_RELATIONS
    ):
        # A literal action target/data value describes the downstream effect;
        # it is not evidence that changing the helper can reach the action.
        # Template reads in those same configuration paths remain causal and
        # must continue through the ledger below.
        return "proven_dependency_neutral"
    if (
        item.outcome == "exact_dependency"
        and entity_id in item.exact_entity_ids
    ):
        return "exact_dependency"
    # ``exact_dependency`` with candidates represents a complete finite set.
    # A non-member therefore has an attributable exclusion proof.  Candidates
    # retained on an opaque obligation are only partial hints and cannot do so.
    if item.outcome == "exact_dependency" and item.exact_entity_ids:
        return "proven_target_exclusion"
    if item.outcome == "proven_dependency_neutral":
        return "proven_dependency_neutral"
    if item.outcome == "proven_target_exclusion":
        return "proven_target_exclusion"
    if item.outcome == "exact_dependency":
        domains = item.possible_entity_domains
        if domains and "input_boolean" not in domains:
            return "proven_target_exclusion"
        # A proven domain that contains the target domain bounds the potential
        # automation set but cannot prove membership for one exact helper.
        # This is semantic opacity, not missing inventory coverage.
        if domains and "input_boolean" in domains:
            return "bounded_semantic_opaque"
        # An alleged exact terminal without a candidate or domain proof is not
        # exact evidence and cannot be made reviewable safely.
        return "coverage_failure"
    # Candidate IDs and domains retained on an opaque terminal are diagnostic
    # hints only.  Authoritative exclusion must be produced by the analyzer;
    # the risk layer never reconstructs a proof discarded upstream.
    return "bounded_semantic_opaque"


def _project_obligation(
    item: DependencyObligation, *, target_outcome: str
) -> dict[str, Any]:
    projected = {
        "evidence_id": item.evidence_id,
        "source_object_id": _safe_automation_identity(
            item.source_id, item.source_entity_id
        ),
        "configuration_path": str(item.config_path)[:256],
        "relation": item.relation,
        "ledger_outcome": item.outcome,
        "target_outcome": target_outcome,
        "obligation_kind": item.obligation_kind,
        "reason_code": item.reason_code,
        "semantic_category": item.semantic_category,
        "semantic_registry_version": item.semantic_registry_version,
        "semantic_registry_fingerprint": (
            item.semantic_registry_fingerprint
        ),
        "expression_fingerprint": item.expression_fingerprint,
        "configuration_fingerprint": item.configuration_fingerprint,
        "candidate_entity_ids": list(item.exact_entity_ids),
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
    }
    # Helper approvals bind semantic/configuration identity, not display labels
    # or the automation's enabled state.  The index-level ledger fingerprint
    # may include that diagnostic metadata, but irrelevant presentation changes
    # must not invalidate an otherwise identical helper approval.
    projected["obligation_fingerprint"] = stable_hash(projected)
    projected["target_projection_fingerprint"] = stable_hash(
        projected
    )
    return projected


def _non_relevant_obligation_material(
    projected: dict[str, Any],
) -> dict[str, Any]:
    """Return target-exclusion semantics without unrelated config bytes.

    A complete automation configuration fingerprint is material for an exact
    or opaque dependency because its downstream effect is approval-bound.  It
    is deliberately not material for an obligation already proven irrelevant
    to the exact helper: changing an unrelated notification message must not
    stale that helper approval.  If the edit introduces the helper, the new
    target outcome moves into the relevant projection and changes the binding.
    """

    return {
        key: value
        for key, value in projected.items()
        if key
        not in {
            "configuration_fingerprint",
            "evidence_id",
            "obligation_fingerprint",
            "target_projection_fingerprint",
        }
    }


def _project_downstream_profile(
    *,
    source_id: str,
    relationships: set[str],
    profile: Any,
) -> tuple[dict[str, Any], str | None]:
    resource_id = _safe_automation_resource_identity(
        source_id,
        profile.source_entity_id if profile is not None else None,
    )
    identity = _safe_automation_identity(
        source_id,
        profile.source_entity_id if profile is not None else None,
    )
    if profile is None:
        return (
            {
                "automation_id": identity,
                "automation_resource_id": resource_id,
                "relationships": sorted(relationships),
                "physical_consequence": "unknown",
                "complete": False,
                "analysis_complete": False,
                "semantic_complete": False,
                "presentation_truncated": False,
                "processing_limit_exceeded": False,
                "processing_limit_reason": "action_profile_unavailable",
                "processing_observed_action_step_count": 0,
                "processing_action_step_limit": 0,
                "processing_action_depth_limit": 0,
                "processing_observed_effect_node_count": 0,
                "processing_effect_node_limit": 0,
                "processing_effect_depth_limit": 0,
                "processing_overflow_fingerprint": None,
                "truncated": False,
                "action_domains": [],
                "action_domain_count": 0,
                "action_domains_fingerprint": stable_hash([]),
                "services": [],
                "service_count": 0,
                "services_fingerprint": stable_hash([]),
                "reason_codes": ["action_profile_unavailable"],
                "reason_code_count": 1,
                "reason_codes_fingerprint": stable_hash(
                    ["action_profile_unavailable"]
                ),
                "effect_projection_model": "unavailable",
                "effect_targets": [],
                "effect_target_count": 0,
                "effect_targets_fingerprint": stable_hash([]),
                "effect_data": [],
                "effect_data_count": 0,
                "effect_data_fingerprint": stable_hash([]),
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
            },
            "action_profile_unavailable",
        )
    projected = {
        "automation_id": identity,
        "automation_resource_id": resource_id,
        "relationships": sorted(relationships),
        "physical_consequence": profile.physical_consequence,
        "complete": profile.complete,
        "analysis_complete": profile.analysis_complete,
        "semantic_complete": profile.semantic_complete,
        "presentation_truncated": profile.presentation_truncated,
        "processing_limit_exceeded": profile.processing_limit_exceeded,
        "processing_limit_reason": profile.processing_limit_reason,
        "processing_observed_action_step_count": (
            profile.processing_observed_action_step_count
        ),
        "processing_action_step_limit": profile.processing_action_step_limit,
        "processing_action_depth_limit": profile.processing_action_depth_limit,
        "processing_observed_effect_node_count": (
            profile.processing_observed_effect_node_count
        ),
        "processing_effect_node_limit": profile.processing_effect_node_limit,
        "processing_effect_depth_limit": profile.processing_effect_depth_limit,
        "processing_overflow_fingerprint": (
            profile.processing_overflow_fingerprint
        ),
        "truncated": profile.truncated,
        "action_domains": list(profile.action_domains),
        "action_domain_count": profile.action_domain_count,
        "action_domains_fingerprint": profile.action_domains_fingerprint,
        "services": list(profile.services),
        "service_count": profile.service_count,
        "services_fingerprint": profile.services_fingerprint,
        "reason_codes": list(profile.reason_codes),
        "reason_code_count": profile.reason_code_count,
        "reason_codes_fingerprint": profile.reason_codes_fingerprint,
        "effect_projection_model": profile.effect_projection_model,
        "effect_targets": list(profile.effect_targets),
        "effect_target_count": profile.effect_target_count,
        "effect_targets_fingerprint": profile.effect_targets_fingerprint,
        "effect_data": list(profile.effect_data),
        "effect_data_count": profile.effect_data_count,
        "effect_data_fingerprint": profile.effect_data_fingerprint,
        "effect_structure_fingerprint": (
            profile.effect_structure_fingerprint
        ),
        "effect_projection_fingerprint": (
            profile.effect_projection_fingerprint
        ),
        "effect_projection_clipped": profile.effect_projection_clipped,
        "profile_fingerprint": profile.evidence_fingerprint,
    }
    if resource_id is None:
        return projected, "automation_lock_identity_unavailable"
    if profile.processing_limit_exceeded or not profile.analysis_complete:
        return projected, "action_profile_processing_limit_exceeded"
    if not profile.semantic_complete:
        return projected, "action_profile_semantic_incomplete"
    if not profile.complete:
        return projected, (
            "action_profile_truncated"
            if profile.truncated
            else "action_profile_incomplete"
        )
    return projected, None


def _build_obligation_binding(
    snapshot: DependencyIndexSnapshot,
    *,
    entity_id: str,
    index_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Build the target-specific governance contract from one shared ledger."""

    automation_obligations = sorted(
        (
            item
            for item in snapshot.obligations
            if item.source_type in {"automation", "blueprint"}
        ),
        key=lambda item: obligation_fingerprint(item),
    )
    projected_pairs = [
        (
            _project_obligation(
                item,
                target_outcome=_obligation_targets_helper(item, entity_id),
            ),
            item,
        )
        for item in automation_obligations
    ]
    projected_pairs.sort(
        key=lambda pair: (
            pair[0]["target_projection_fingerprint"],
            pair[1].source_id,
            pair[1].config_path,
        )
    )
    projected = [item for item, _ in projected_pairs]
    relevant_pairs = [
        (item, obligation)
        for item, obligation in projected_pairs
        if item["target_outcome"]
        not in {"proven_target_exclusion", "proven_dependency_neutral"}
    ]
    non_relevant_pairs = [
        (item, obligation)
        for item, obligation in projected_pairs
        if item["target_outcome"]
        in {"proven_target_exclusion", "proven_dependency_neutral"}
    ]
    overflow_count = max(
        0, int(getattr(snapshot, "obligation_overflow_count", 0) or 0)
    )
    overflow_fingerprint = getattr(
        snapshot, "obligation_overflow_fingerprint", None
    )
    if overflow_count and not isinstance(overflow_fingerprint, str):
        overflow_fingerprint = stable_hash(
            {"count": overflow_count, "fingerprint": "unavailable"}
        )

    coverage_items = {
        item.source_type: item for item in snapshot.coverage
    }
    automation_coverage = coverage_items.get("automation")
    blueprint_coverage = coverage_items.get("blueprint")
    fresh = bool(
        index_metadata.get("freshness") == "current"
        and index_metadata.get("evidence_stale") is not True
        and index_metadata.get("invalidated") is not True
    )
    coverage_reasons: set[str] = set()
    # B39-136-R3b: the reviewed semantics only bind to a supported release.
    admission = home_assistant_version_admission(snapshot)
    if not admission["admitted"]:
        coverage_reasons.add(str(admission["reason_code"]))
    if snapshot.obligation_ledger_model not in {
        None,
        OBLIGATION_LEDGER_MODEL,
    }:
        coverage_reasons.add("obligation_ledger_model_unsupported")
    if not fresh:
        coverage_reasons.add("dependency_index_not_fresh")
    authoritative_automation_completeness = (
        automation_coverage.obligation_ledger_completeness
        if automation_coverage is not None
        and snapshot.obligation_ledger_model == OBLIGATION_LEDGER_MODEL
        and automation_coverage.obligation_ledger_completeness is not None
        else automation_coverage.completeness
        if automation_coverage is not None
        else None
    )
    authoritative_automation_failures = (
        automation_coverage.obligation_ledger_failed_item_count
        if automation_coverage is not None
        and snapshot.obligation_ledger_model == OBLIGATION_LEDGER_MODEL
        and automation_coverage.obligation_ledger_completeness is not None
        else automation_coverage.failed_item_count
        if automation_coverage is not None
        else 0
    )
    if (
        automation_coverage is None
        or authoritative_automation_completeness != "complete"
        or authoritative_automation_failures
    ):
        coverage_reasons.add("automation_inventory_incomplete")
    authoritative_blueprint_completeness = (
        blueprint_coverage.obligation_ledger_completeness
        if blueprint_coverage is not None
        and snapshot.obligation_ledger_model == OBLIGATION_LEDGER_MODEL
        and blueprint_coverage.obligation_ledger_completeness is not None
        else blueprint_coverage.completeness
        if blueprint_coverage is not None
        else None
    )
    authoritative_blueprint_failures = (
        blueprint_coverage.obligation_ledger_failed_item_count
        if blueprint_coverage is not None
        and snapshot.obligation_ledger_model == OBLIGATION_LEDGER_MODEL
        and blueprint_coverage.obligation_ledger_completeness is not None
        else blueprint_coverage.failed_item_count
        if blueprint_coverage is not None
        else 0
    )
    if (
        blueprint_coverage is None
        or authoritative_blueprint_completeness != "complete"
        or authoritative_blueprint_failures
    ):
        coverage_reasons.add("blueprint_inventory_incomplete")
    if snapshot.automation_read_failures:
        coverage_reasons.add("automation_configuration_read_failure")
    if overflow_count:
        coverage_reasons.add("obligation_ledger_truncated")
    if len(relevant_pairs) > MAX_RELEVANT_OBLIGATIONS:
        coverage_reasons.add("obligation_projection_limit_exceeded")
    if any(item["target_outcome"] == "coverage_failure" for item in projected):
        coverage_reasons.add("obligation_coverage_failure")
    if snapshot.obligation_ledger_model != OBLIGATION_LEDGER_MODEL and any(
        "exceeded the bounded index payload" in warning
        for item in snapshot.coverage
        for warning in item.warnings
    ):
        coverage_reasons.add("dependency_index_payload_truncated")

    relevant: dict[str, set[str]] = _causal_automation_sources(
        snapshot, entity_id
    )
    exact_count = 0
    opaque_count = 0
    external_template_names: set[str] = set()
    external_template_opacity_count = 0
    for source_item, obligation in projected_pairs:
        outcome = source_item["target_outcome"]
        if outcome == "exact_dependency":
            exact_count += 1
            relevant.setdefault(obligation.source_id, set()).add(
                obligation.relation or "template_exact_dependency"
            )
        elif outcome == "bounded_semantic_opaque":
            opaque_count += 1
            relevant.setdefault(obligation.source_id, set()).add(
                "bounded_semantic_opaque"
            )
            if obligation.external_template_name:
                external_template_names.add(
                    obligation.external_template_name
                )
            if (
                obligation.external_template_name
                or obligation.obligation_kind.startswith(
                    "external_template_"
                )
            ):
                external_template_opacity_count += 1

    if len(relevant) > MAX_RELEVANT_AUTOMATIONS:
        coverage_reasons.add("relevant_automation_limit_exceeded")
    profiles_by_source = {
        item.source_id: item
        for item in snapshot.automation_action_profiles
    }
    downstream_profiles: list[dict[str, Any]] = []
    observed_consequence = "none"
    for source_id in sorted(relevant)[:MAX_RELEVANT_AUTOMATIONS]:
        profile, failure = _project_downstream_profile(
            source_id=source_id,
            relationships=relevant[source_id],
            profile=profiles_by_source.get(source_id),
        )
        downstream_profiles.append(profile)
        if failure:
            coverage_reasons.add(failure)
        consequence = str(profile["physical_consequence"])
        if _CONSEQUENCE_RANK.get(consequence, 1) > _CONSEQUENCE_RANK.get(
            observed_consequence, 0
        ):
            observed_consequence = consequence

    # Bounded but incomplete effect semantics are transparent elevated evidence,
    # not an inventory failure.  Missing, clipped, or unlockable profiles are
    # coverage failures because their effect/lock scope cannot be bounded.
    coverage_complete = not coverage_reasons
    if coverage_complete and opaque_count:
        semantic_precision = "bounded_opaque"
    elif coverage_complete:
        semantic_precision = "exact"
    else:
        semantic_precision = "coverage_failure"
    execution_eligible = bool(coverage_complete and not opaque_count)
    evidence_complete = bool(coverage_complete and not opaque_count)
    if not coverage_complete and observed_consequence == "none":
        observed_consequence = "unknown"

    resource_ids = sorted(
        {
            item["automation_resource_id"]
            for item in downstream_profiles
            if isinstance(item.get("automation_resource_id"), str)
        },
        key=lambda item: item.encode("utf-8"),
    )
    lock_projection = {
        "exact_helper_dependency": True,
        # Every helper execution holds this shared guard so an automation
        # mutation that newly introduces opaque dependency semantics cannot
        # race final preflight and dispatch.
        "conservative_helper_dependency": True,
        "automation_resource_ids": resource_ids,
        "custom_template_reload": bool(external_template_opacity_count),
    }
    retained_obligations = [
        item
        for item, _ in relevant_pairs[:MAX_RELEVANT_OBLIGATIONS]
    ]
    non_relevant_fingerprints = sorted(
        stable_hash(_non_relevant_obligation_material(item))
        for item, _ in non_relevant_pairs
    )
    non_relevant_fingerprint = stable_hash(non_relevant_fingerprints)
    downstream_profiles.sort(key=lambda item: item["automation_id"])
    consequential_ids = [
        item["automation_id"]
        for item in downstream_profiles
        if item["physical_consequence"] in {"direct", "safety_critical"}
    ]
    material = {
        "model": HELPER_DEPENDENCY_RISK_MODEL,
        "obligation_ledger_model": (
            snapshot.obligation_ledger_model or OBLIGATION_LEDGER_MODEL
        ),
        "entity_id": entity_id,
        "completeness": (
            "complete" if coverage_complete else "failed"
        ),
        "coverage_complete": coverage_complete,
        "semantic_precision": semantic_precision,
        "evidence_complete": evidence_complete,
        "execution_eligible": execution_eligible,
        "physical_consequence": observed_consequence,
        "relevant_downstream_object_ids": [
            item["automation_id"] for item in downstream_profiles
        ],
        "downstream_automation_resource_ids": resource_ids,
        "consequential_downstream_object_ids": consequential_ids,
        "downstream_profiles": downstream_profiles,
        "exact_dependency_obligation_count": exact_count,
        "opaque_obligation_count": opaque_count,
        "coverage_failure_count": len(coverage_reasons),
        "coverage_failure_reason_codes": sorted(coverage_reasons),
        "obligation_evidence": retained_obligations,
        "retained_obligation_count": len(retained_obligations),
        "non_relevant_obligation_count": len(non_relevant_pairs),
        "proven_target_exclusion_obligation_count": sum(
            item["target_outcome"] == "proven_target_exclusion"
            for item, _ in non_relevant_pairs
        ),
        "proven_dependency_neutral_obligation_count": sum(
            item["target_outcome"] == "proven_dependency_neutral"
            for item, _ in non_relevant_pairs
        ),
        "non_relevant_obligation_fingerprint": (
            non_relevant_fingerprint
        ),
        "non_relevant_obligations_compacted": bool(non_relevant_pairs),
        "obligation_overflow_count": overflow_count,
        "obligation_overflow_fingerprint": overflow_fingerprint,
        "semantic_registry_versions": sorted(
            {item.semantic_registry_version for item in automation_obligations}
        ),
        "semantic_registry_fingerprints": sorted(
            {
                item.semantic_registry_fingerprint
                for item in automation_obligations
            }
        ),
        "external_template_names": sorted(external_template_names),
        "external_template_opacity_count": (
            external_template_opacity_count
        ),
        "dependency_lock_projection": lock_projection,
        **_version_admission_material(admission),
        "truncated": bool(
            overflow_count or len(relevant_pairs) > MAX_RELEVANT_OBLIGATIONS
        ),
    }
    return {
        **material,
        # Retain additive legacy projections for existing clients and tests.
        "target_relevant_dynamic_reference_count": opaque_count,
        "target_relevant_dynamic_reference_fingerprints": [
            item["target_projection_fingerprint"]
            for item in retained_obligations
            if item["target_outcome"] == "bounded_semantic_opaque"
        ],
        "resolved_dynamic_reference_evidence": [],
        "resolved_target_dynamic_reference_count": exact_count,
        "unresolved_dynamic_reference_count": opaque_count,
        "unrelated_dynamic_reference_count": sum(
            item["target_outcome"] == "proven_target_exclusion"
            for item, _ in non_relevant_pairs
        ),
        "unreadable_automation_count": len(snapshot.automation_read_failures),
        "unreadable_automation_ids": sorted(
            {
                _safe_automation_identity(item.source_id, item.source_entity_id)
                for item in snapshot.automation_read_failures
            }
        ),
        "unreadable_automation_fingerprint": stable_hash(
            [
                (item.source_id, item.source_entity_id, item.reason_code)
                for item in snapshot.automation_read_failures
            ]
        ),
        "dynamic_reference_overflow_count": 0,
        "dynamic_reference_overflow_fingerprint": None,
        "dynamic_evaluation_overflow_count": 0,
        "dynamic_evaluation_overflow_fingerprint": None,
        "dynamic_resolution_reason_codes": [],
        "coverage_diagnostics": [
            {
                "source_type": item.source_type,
                "completeness": item.completeness,
                "failed_item_count": item.failed_item_count,
                "warning_count": len(item.warnings),
                "obligation_ledger_completeness": (
                    item.obligation_ledger_completeness
                ),
                "obligation_ledger_failed_item_count": (
                    item.obligation_ledger_failed_item_count
                ),
            }
            for item in snapshot.coverage
            if item.source_type in {"automation", "blueprint"}
        ],
        "evidence_fingerprint": stable_hash(material),
    }


# B39-136-R3b: reason codes for the runtime version admission gate.  Three
# situations are distinguishable because they call for different operator
# action: upgrade/downgrade Home Assistant, restore connectivity, or
# investigate a malformed response.
VERSION_UNSUPPORTED_REASON = "home_assistant_version_unsupported"
VERSION_UNAVAILABLE_REASON = "home_assistant_version_unavailable"
VERSION_UNREADABLE_REASON = "home_assistant_version_unreadable"


def home_assistant_version_admission(
    snapshot: DependencyIndexSnapshot,
) -> dict[str, Any]:
    """Admit or refuse the reviewed semantics for the connected release.

    The reviewed template semantics are asserted valid for a fixed set of
    Home Assistant releases.  Evidence produced against any other release -
    or against an instance whose version could not be read - is not
    execution-authoritative, so this gate fails closed on every negative
    case.  It is an additional necessary condition layered on the R2 source
    fence and the R5 compatibility/execution split, never a replacement for
    either.
    """

    supported = supported_home_assistant_versions()
    observed = getattr(snapshot, "home_assistant_version", None)
    status = str(
        getattr(snapshot, "home_assistant_version_status", "unavailable")
    )
    if status == "observed":
        if isinstance(observed, str) and observed:
            admitted = observed in supported
            reason = None if admitted else VERSION_UNSUPPORTED_REASON
        else:
            # Claimed observed but carries no version: the field is the
            # problem, not connectivity.
            admitted, reason = False, VERSION_UNREADABLE_REASON
    elif status == "unreadable":
        admitted, reason = False, VERSION_UNREADABLE_REASON
    else:
        admitted, reason = False, VERSION_UNAVAILABLE_REASON
    return {
        "admitted": admitted,
        "observed_version": observed if status == "observed" else None,
        "observation_status": status,
        "supported_versions": list(supported),
        "reason_code": reason,
    }


def _version_admission_material(admission: dict[str, Any]) -> dict[str, Any]:
    """Return the admission facts bound into evidence and its fingerprint."""

    return {
        "home_assistant_version_admitted": bool(admission["admitted"]),
        "home_assistant_version_observed": admission["observed_version"],
        "home_assistant_version_observation_status": (
            admission["observation_status"]
        ),
        "home_assistant_supported_versions": list(
            admission["supported_versions"]
        ),
    }


def build_helper_dependency_risk_binding(
    snapshot: DependencyIndexSnapshot,
    *,
    entity_id: str,
    index_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Build one normalized, bounded, target-specific approval binding."""

    if (
        snapshot.obligation_ledger_model is not None
        or snapshot.obligations
        or getattr(snapshot, "obligation_overflow_count", 0)
    ):
        return _build_obligation_binding(
            snapshot,
            entity_id=entity_id,
            index_metadata=index_metadata,
        )

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
    retained_dynamic_evidence: list[dict[str, Any]] = []
    omitted_dynamic_evidence: list[dict[str, Any]] = []
    retained_candidate_count = 0
    for item in resolved_dynamic_evidence:
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
    resolved_dynamic_evidence = retained_dynamic_evidence
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
    # B39-136-R3b: the legacy projection is reached for pre-ledger snapshots
    # and still claims the current model, so it takes the same admission gate.
    # No binding path may escape it.
    legacy_admission = home_assistant_version_admission(snapshot)
    evidence_complete = bool(
        legacy_admission["admitted"]
        and fresh
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
        "coverage_complete": evidence_complete,
        "semantic_precision": (
            "exact" if evidence_complete else "coverage_failure"
        ),
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
        "opaque_obligation_count": 0,
        "coverage_failure_count": 0 if evidence_complete else 1,
        "coverage_failure_reason_codes": (
            []
            if evidence_complete
            else sorted(
                {"legacy_evidence_incomplete"}
                | (
                    set()
                    if legacy_admission["admitted"]
                    else {str(legacy_admission["reason_code"])}
                )
            )
        ),
        "obligation_evidence": [],
        "obligation_overflow_count": 0,
        "obligation_overflow_fingerprint": None,
        "semantic_registry_versions": [],
        "semantic_registry_fingerprints": [],
        "external_template_names": [],
        "dependency_lock_projection": {
            "exact_helper_dependency": True,
            "conservative_helper_dependency": True,
            "automation_resource_ids": resource_ids,
            "custom_template_reload": False,
        },
        **_version_admission_material(legacy_admission),
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
        self,
        entity_id: str,
        *,
        refresh: bool = True,
        fenced: bool = False,
    ) -> dict[str, Any]:
        """Read target-specific risk, optionally behind a governed fence.

        ``fenced`` is used by the post-lock preflight, which runs only after
        the complete lock set is held.  It opens a source-read fence and
        accepts only evidence from a scan that started after it, so a build
        that began before the lock cannot satisfy the final check.
        """

        fence: int | None = None
        try:
            if fenced:
                fence = self.index.open_source_fence(
                    "governed_helper_preflight"
                )
            snapshot, rebuilt, lookup_duration_ms = await self.index.get(
                refresh=refresh, min_source_epoch=fence
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
                "source_epoch": snapshot.source_epoch,
                "fenced": fence is not None,
                # B39-136-R3b: the gate's decision travels with the same
                # provenance R2 and R5 already populate, so a refusal is
                # visible rather than an internal silence.
                "home_assistant_version": binding.get(
                    "home_assistant_version_observed"
                ),
                "home_assistant_version_status": binding.get(
                    "home_assistant_version_observation_status"
                ),
                "home_assistant_version_admitted": binding.get(
                    "home_assistant_version_admitted"
                ),
                "lookup_duration_ms": round(lookup_duration_ms, 3),
                "fallback": "none",
                "fallback_occurred": False,
            },
        }


async def read_runtime_helper_dependency_risk(
    entity_id: str, *, refresh: bool = True, fenced: bool = False
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
        entity_id, refresh=refresh, fenced=fenced
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
    eligible = binding.get("execution_eligible") is True
    precision = str(binding.get("semantic_precision", "exact"))
    consequence = binding.get("physical_consequence")
    if eligible and consequence == "none":
        level = RiskLevel.LOW
        reasons = [
            (
                "Bounded opaque dependency evidence found only proven-benign downstream effects."
                if precision == "bounded_opaque"
                else "Complete bounded dependency evidence found no consequential downstream automation path."
            ),
        ]
        warnings: list[str] = (
            [
                "Semantic opacity remains approval-bound and requires conservative dependency locks."
            ]
            if precision == "bounded_opaque"
            else []
        )
    elif eligible:
        level = RiskLevel.HIGH
        reasons = [
            (
                "Bounded opaque dependency evidence includes consequential or unknown downstream effects."
                if precision == "bounded_opaque"
                else "Bounded dependency evidence found a materially consequential downstream automation path."
            ),
        ]
        warnings = (
            [
                "Semantic opacity is explicit and requires elevated acknowledgement plus conservative locks."
            ]
            if precision == "bounded_opaque"
            else []
        )
    else:
        level = RiskLevel.HIGH
        codes = set(binding.get("coverage_failure_reason_codes") or [])
        observed_version = binding.get("home_assistant_version_observed")
        supported_versions = (
            binding.get("home_assistant_supported_versions") or []
        )
        supported_text = ", ".join(str(item) for item in supported_versions)
        if precision == "bounded_opaque" and not codes:
            reasons = [
                "Target-capable dependency semantics remain unresolved, so the helper plan is not execution-eligible.",
            ]
            warnings = [
                "Resolve the opaque dependency evidence and create a fresh plan before approval or dispatch.",
            ]
        elif VERSION_UNSUPPORTED_REASON in codes:
            # State both sides plainly: the operator needs to know which
            # release is running and which the semantics were reviewed for.
            reasons = [
                "Home Assistant "
                f"{observed_version} is running, and the reviewed template "
                f"semantics cover only {supported_text}."
            ]
            warnings = [
                "Dependency evidence is not execution-authoritative on an "
                "unsupported Home Assistant release. No change was attempted.",
            ]
        elif VERSION_UNREADABLE_REASON in codes:
            reasons = [
                "The Home Assistant version could not be determined from the "
                "configuration response, so the reviewed template semantics "
                "cannot be bound to the connected instance.",
            ]
            warnings = [
                "No change was attempted. Reviewed releases are "
                f"{supported_text}.",
            ]
        elif VERSION_UNAVAILABLE_REASON in codes:
            reasons = [
                "The connected Home Assistant version could not be read, so "
                "the reviewed template semantics cannot be bound to the "
                "instance.",
            ]
            warnings = [
                "No change was attempted. Restore Home Assistant "
                f"connectivity and retry. Reviewed releases are "
                f"{supported_text}.",
            ]
        else:
            reasons = [
                "Dependency evidence coverage failed, so execution scope cannot be bounded.",
            ]
            warnings = [
                "Fresh bounded dependency coverage is required before approval or dispatch.",
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
            "coverage_complete": bool(
                binding.get("coverage_complete", complete)
            ),
            "semantic_precision": precision,
            "opaque_obligation_count": int(
                binding.get("opaque_obligation_count", 0) or 0
            ),
            "coverage_failure_count": int(
                binding.get("coverage_failure_count", 0) or 0
            ),
            "physical_consequence": str(consequence),
            "evidence_fingerprint": str(
                binding.get("evidence_fingerprint")
            ),
            "generation": str(provenance.get("generation", "unknown")),
            "index_fingerprint": str(
                provenance.get("fingerprint", "unknown")
            ),
        },
        {
            "field": "home_assistant_version",
            "trigger": "reviewed_semantics_version_admission",
            "observed_version": str(
                binding.get("home_assistant_version_observed")
            ),
            "observation_status": str(
                binding.get("home_assistant_version_observation_status")
            ),
            "admitted": bool(
                binding.get("home_assistant_version_admitted")
            ),
            "supported_versions": [
                str(item)
                for item in (
                    binding.get("home_assistant_supported_versions") or []
                )
            ],
        },
    ]
    return ChangeRiskAssessment(
        level=level,
        reasons=reasons,
        apply_allowed=eligible,
        evidence=risk_evidence,
        warnings=warnings,
    )


__all__ = [
    "HELPER_DEPENDENCY_RISK_MODEL",
    "HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS",
    "HELPER_DEPENDENCY_RISK_EXECUTION_MODELS",
    "VERSION_UNAVAILABLE_REASON",
    "VERSION_UNREADABLE_REASON",
    "VERSION_UNSUPPORTED_REASON",
    "home_assistant_version_admission",
    "HelperDependencyRiskService",
    "build_helper_dependency_risk_binding",
    "helper_dependency_risk_assessment",
    "read_runtime_helper_dependency_risk",
]
