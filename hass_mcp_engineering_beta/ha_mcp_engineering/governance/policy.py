"""Deterministic F2 policy classification for immutable change plans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .models import (
    ApprovalActionKind,
    ApprovalPolicyClass,
    ChangeOperation,
    ChangePlan,
    ChangePolicyDecision,
    ConfigurationOperation,
    PhysicalConsequence,
    RiskDelta,
    RiskLevel,
)
from .normalize import stable_hash
from .risk import SAFETY_CRITICAL_SERVICES


POLICY_VERSION = "f2-v2"
COMPATIBLE_POLICY_VERSIONS = frozenset({"f2-v1", POLICY_VERSION})

_RISK_RANK = {
    RiskDelta.NONE: 0,
    RiskDelta.LOW: 1,
    RiskDelta.MODERATE: 2,
    RiskDelta.HIGH: 3,
    RiskDelta.CRITICAL: 4,
}
_CONSEQUENCE_RANK = {
    PhysicalConsequence.NONE: 0,
    PhysicalConsequence.INDIRECT: 1,
    PhysicalConsequence.UNKNOWN: 2,
    PhysicalConsequence.DIRECT: 3,
    PhysicalConsequence.SAFETY_CRITICAL: 4,
}
_POLICY_RANK = {
    ApprovalPolicyClass.STANDARD_ADMIN: 0,
    ApprovalPolicyClass.ELEVATED_ADMIN: 1,
    ApprovalPolicyClass.PROHIBITED: 2,
}

_SAFETY_CRITICAL_TRIGGERS = frozenset(
    {
        "sensitive_entity_domain",
        "sensitive_blueprint_input",
        "garage_cover_target",
        "safety_critical_service",
        "water_control_target",
    }
)
_DIRECT_CONSEQUENCE_TRIGGERS = frozenset(
    {
        "high_risk_service",
        "unrestricted_target",
        "large_target_set",
    }
)
_UNCLASSIFIABLE_TRIGGERS = frozenset(
    {
        "unresolved_dynamic_service",
        "unresolved_dynamic_target",
        "unresolved_action_structure",
    }
)

_REVIEWED_CONDITION_FAMILIES = frozenset(
    {
        "and",
        "device",
        "not",
        "numeric_state",
        "or",
        "state",
        "sun",
        "template",
        "time",
        "trigger",
        "zone",
    }
)
_CONDITION_CONTROL_FAMILIES = frozenset({"and", "not", "or"})
_EXECUTABLE_CONDITION_KEYS = frozenset(
    {
        "action",
        "actions",
        "choose",
        "else",
        "parallel",
        "repeat",
        "sequence",
        "service",
        "then",
    }
)
_NON_BEHAVIORAL_AUTOMATION_KEYS = frozenset({"alias", "description"})


@dataclass(frozen=True)
class OperationPolicyClassification:
    policy_class: ApprovalPolicyClass
    risk_delta: RiskDelta
    physical_consequence: PhysicalConsequence
    reason_codes: tuple[str, ...]


def _risk_delta(level: RiskLevel) -> RiskDelta:
    return {
        RiskLevel.LOW: RiskDelta.LOW,
        RiskLevel.MEDIUM: RiskDelta.MODERATE,
        RiskLevel.HIGH: RiskDelta.HIGH,
    }[level]


def _risk_triggers(operation: ConfigurationOperation) -> set[str]:
    return {
        str(item.get("trigger"))
        for item in operation.risk.evidence
        if isinstance(item, dict) and isinstance(item.get("trigger"), str)
    }


def _risk_services(operation: ConfigurationOperation) -> set[str]:
    return {
        str(item.get("service"))
        for item in operation.risk.evidence
        if isinstance(item, dict)
        and item.get("trigger") in {
            "high_risk_service",
            "safety_critical_service",
        }
        and isinstance(item.get("service"), str)
        and item.get("service") in SAFETY_CRITICAL_SERVICES
    }


def _exact_existing_automation_update(
    operation: ConfigurationOperation,
) -> bool:
    """Recognize the first exact typed configuration-write authority.

    Action targets inside the stored automation may remain semantically
    uncertain. They are not the configuration provider's dispatch target:
    that target is the exact existing automation bound by normalized before
    and after configurations, optimistic state, validation, and a typed
    writer/readback contract.
    """

    return bool(
        operation.resource_type == "automation"
        and operation.action == "update"
        and isinstance(operation.current_config, dict)
        and isinstance(operation.proposed_config, dict)
        and isinstance(operation.normalized_current_config, dict)
        and isinstance(operation.normalized_proposed_config, dict)
        and isinstance(operation.target_id, str)
        and bool(operation.target_id)
        and isinstance(operation.current_state_fingerprint, str)
        and len(operation.current_state_fingerprint) == 64
        and isinstance(operation.proposed_config_hash, str)
        and len(operation.proposed_config_hash) == 64
        and operation.normalization_version >= 1
        and operation.validation_results.get("valid") is True
        and operation.risk.apply_allowed
    )


def _exact_existing_dashboard_update(plan: ChangePlan) -> bool:
    """Recognize a fresh typed dashboard write with complete execution authority.

    Dashboard action semantics remain consequence evidence. They do not alter
    the exact provider target, compiled full configuration, optimistic
    baseline, lock graph, one-dispatch ownership, or authoritative reread.
    """

    operational = plan.operational
    baseline = (
        operational.baseline
        if operational is not None
        and isinstance(operational.baseline, dict)
        else {}
    )
    capability = (
        operational.provider_capability_evidence
        if operational is not None
        and isinstance(operational.provider_capability_evidence, dict)
        else {}
    )
    identity_value = baseline.get("dashboard_operational_identity")
    proposal = plan.proposed_config.get("dashboard_update")
    projection = (
        proposal.get("approval_projection")
        if isinstance(proposal, dict)
        else None
    )
    try:
        from ..f3_dashboard.approval_projection import (
            validate_dashboard_approval_projection,
        )
        from ..f3_dashboard.identity import operational_identity_from_mapping

        identity = operational_identity_from_mapping(identity_value)
        validate_dashboard_approval_projection(
            projection,
            expected_preread_sha256=baseline.get(
                "current_engineering_sha256"
            ),
            expected_patch_sha256=baseline.get("canonical_patch_sha256"),
            expected_resulting_sha256=baseline.get(
                "resulting_engineering_sha256"
            ),
        )
    except (TypeError, ValueError):
        return False
    return bool(
        plan.operation == ChangeOperation.UPDATE_DASHBOARD
        and plan.contract_version == 3
        and plan.target.target_type == "dashboard"
        and isinstance(plan.target.target_id, str)
        and bool(plan.target.target_id)
        and plan.risk.apply_allowed
        and plan.validation_results.get("valid") is True
        and plan.validation_results.get("storage_mode_confirmed") is True
        and plan.validation_results.get("exact_provider_contract_admitted")
        is True
        and plan.validation_results.get("approval_projection_complete") is True
        and operational is not None
        and operational.family == "dashboard_update"
        and operational.operation == ChangeOperation.UPDATE_DASHBOARD.value
        and operational.requested_name == plan.target.target_id
        and operational.provider == "upstream_dashboard"
        and capability.get("tool") == "ha_config_set_dashboard"
        and baseline.get("storage_mode_confirmed") is True
        and baseline.get("non_atomic") is True
        and identity.target_url_path == plan.target.target_id
        and identity.authority.compatibility_entry
        == capability.get("compatibility_entry")
        and identity.authority.setter_contract_hash
        == capability.get("provider_contract_hash")
        and identity.evidence_hash
        == baseline.get("dashboard_provider_identity_hash")
        and isinstance(baseline.get("approval_projection_sha256"), str)
        and isinstance(projection, dict)
        and isinstance(projection.get("binding"), dict)
        and projection["binding"].get("projection_sha256")
        == baseline["approval_projection_sha256"]
    )


def _contains_executable_condition_key(value: Any) -> bool:
    if isinstance(value, dict):
        if _EXECUTABLE_CONDITION_KEYS.intersection(value):
            return True
        return any(
            _contains_executable_condition_key(item)
            for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_executable_condition_key(item) for item in value)
    return False


def _reviewed_condition_guard(value: Any, *, depth: int = 0) -> bool:
    """Recognize one bounded condition that can only narrow top-level AND.

    This is deliberately a policy proof, not a replacement for Home
    Assistant configuration validation.  Unknown condition families,
    disabled guards, action-like directives, and excessively nested boolean
    structures are excluded from the retained-effect exception.
    """

    if depth > 4 or not isinstance(value, dict):
        return False
    family = value.get("condition")
    if (
        not isinstance(family, str)
        or family not in _REVIEWED_CONDITION_FAMILIES
        or value.get("enabled", True) is not True
        or _contains_executable_condition_key(value)
    ):
        return False
    if family not in _CONDITION_CONTROL_FAMILIES:
        return True
    children = value.get("conditions")
    return bool(
        isinstance(children, list)
        and children
        and all(
            _reviewed_condition_guard(child, depth=depth + 1)
            for child in children
        )
    )


def _retained_safety_critical_effect_is_non_risk_increasing(
    operation: ConfigurationOperation,
    *,
    triggers: set[str],
    safety_critical_services: set[str],
) -> bool:
    """Prove the narrow reviewed-existing-effect policy exception.

    The exact normalized action/control-flow graph and every other behavioral
    top-level field must be unchanged.  The only behavioral delta permitted is
    appending one or more reviewed conditions to Home Assistant's top-level
    conjunctive condition list.  Existing conditions remain an exact prefix,
    so the proposal cannot make the retained effect run in any state where it
    did not already run.  The proof permits strict narrowing or behavioral
    neutrality; it does not prove that an appended guard is effective in any
    reachable runtime state.
    """

    if (
        operation.resource_type != "automation"
        or operation.action != "update"
        or not isinstance(operation.normalized_current_config, dict)
        or not isinstance(operation.normalized_proposed_config, dict)
        or operation.risk.warnings
        or triggers.intersection(_UNCLASSIFIABLE_TRIGGERS)
        or not (
            triggers.intersection(_SAFETY_CRITICAL_TRIGGERS)
            or safety_critical_services
        )
    ):
        return False

    current = operation.normalized_current_config
    proposed = operation.normalized_proposed_config
    if "use_blueprint" in current or "use_blueprint" in proposed:
        return False
    if (
        not isinstance(current.get("action"), list)
        or not current["action"]
        or current.get("action") != proposed.get("action")
    ):
        return False

    ignored = {*_NON_BEHAVIORAL_AUTOMATION_KEYS, "condition"}
    for key in set(current).union(proposed).difference(ignored):
        if (
            (key in current) != (key in proposed)
            or current.get(key) != proposed.get(key)
        ):
            return False

    current_conditions = current.get("condition", [])
    proposed_conditions = proposed.get("condition", [])
    if (
        not isinstance(current_conditions, list)
        or not isinstance(proposed_conditions, list)
        or len(proposed_conditions) <= len(current_conditions)
        or proposed_conditions[: len(current_conditions)]
        != current_conditions
    ):
        return False
    return all(
        _reviewed_condition_guard(value)
        for value in proposed_conditions[len(current_conditions) :]
    )


def configuration_operation_policy(
    operation: ConfigurationOperation,
    *,
    owner_authoritative: bool = True,
) -> OperationPolicyClassification:
    """Classify one operation for both policy and approval review."""
    triggers = _risk_triggers(operation)
    safety_critical_services = _risk_services(operation)
    risk_delta = _risk_delta(operation.risk.level)
    reasons = {"supported_configuration_change"}
    consequence = PhysicalConsequence.NONE
    exact_automation_update = bool(
        owner_authoritative
        and _exact_existing_automation_update(operation)
    )

    if _retained_safety_critical_effect_is_non_risk_increasing(
        operation,
        triggers=triggers,
        safety_critical_services=safety_critical_services,
    ):
        return OperationPolicyClassification(
            ApprovalPolicyClass.ELEVATED_ADMIN,
            RiskDelta.MODERATE,
            PhysicalConsequence.SAFETY_CRITICAL,
            (
                "retained_safety_critical_effect",
                "non_risk_increasing_condition_guard_added",
                "safety_critical_effect_requires_elevated_review",
                "supported_configuration_change",
            ),
        )

    if operation.resource_type == "helper":
        consequence = PhysicalConsequence.INDIRECT
        reasons.add("helper_change_indirect_consequence")
    if (
        triggers & _SAFETY_CRITICAL_TRIGGERS
        or safety_critical_services
    ):
        consequence = PhysicalConsequence.SAFETY_CRITICAL
        reasons.add("safety_critical_effect_not_reviewed")
        if safety_critical_services:
            reasons.add("safety_critical_service_prohibited")
    elif triggers & _DIRECT_CONSEQUENCE_TRIGGERS or any(
        "Physical-device action detected" in reason
        for reason in operation.risk.reasons
    ):
        consequence = PhysicalConsequence.DIRECT
        reasons.add("direct_physical_consequence")

    consequence_uncertain = bool(
        triggers & _UNCLASSIFIABLE_TRIGGERS
        or any(
            "could not be bounded structurally" in warning
            for warning in operation.risk.warnings
        )
    )
    if consequence_uncertain and exact_automation_update:
        return OperationPolicyClassification(
            ApprovalPolicyClass.ELEVATED_ADMIN,
            max(risk_delta, RiskDelta.HIGH, key=lambda item: _RISK_RANK[item]),
            (
                consequence
                if consequence is not PhysicalConsequence.NONE
                else PhysicalConsequence.UNKNOWN
            ),
            tuple(
                sorted(
                    {
                        *reasons,
                        "automation_consequence_semantics_incomplete",
                        "exact_existing_automation_update",
                        "owner_decision_required",
                    }
                )
            ),
        )
    if consequence_uncertain:
        return OperationPolicyClassification(
            ApprovalPolicyClass.PROHIBITED,
            risk_delta,
            consequence,
            tuple(sorted({*reasons, "unknown_policy_classification"})),
        )
    if (
        consequence == PhysicalConsequence.SAFETY_CRITICAL
        and exact_automation_update
    ):
        return OperationPolicyClassification(
            ApprovalPolicyClass.ELEVATED_ADMIN,
            max(risk_delta, RiskDelta.HIGH, key=lambda item: _RISK_RANK[item]),
            consequence,
            tuple(
                sorted(
                    {
                        *reasons,
                        "exact_existing_automation_update",
                        "owner_decision_required",
                    }
                )
            ),
        )
    if consequence == PhysicalConsequence.SAFETY_CRITICAL:
        return OperationPolicyClassification(
            ApprovalPolicyClass.PROHIBITED,
            risk_delta,
            consequence,
            tuple(sorted(reasons)),
        )
    if risk_delta == RiskDelta.CRITICAL:
        return OperationPolicyClassification(
            ApprovalPolicyClass.PROHIBITED,
            risk_delta,
            consequence,
            tuple(sorted({*reasons, "critical_risk_delta"})),
        )
    if (
        risk_delta == RiskDelta.HIGH
        or consequence == PhysicalConsequence.DIRECT
    ):
        reasons.add(
            "high_risk_delta"
            if risk_delta == RiskDelta.HIGH
            else "direct_consequence_requires_elevation"
        )
        return OperationPolicyClassification(
            ApprovalPolicyClass.ELEVATED_ADMIN,
            risk_delta,
            consequence,
            tuple(sorted(reasons)),
        )
    reasons.add("standard_configuration_policy")
    return OperationPolicyClassification(
        ApprovalPolicyClass.STANDARD_ADMIN,
        risk_delta,
        consequence,
        tuple(sorted(reasons)),
    )


def _single_plan_policy(
    plan: ChangePlan,
    *,
    owner_authoritative: bool = True,
) -> tuple[OperationPolicyClassification, ...]:
    if plan.operation == ChangeOperation.CREATE_FULL_BACKUP:
        return (
            OperationPolicyClassification(
                ApprovalPolicyClass.STANDARD_ADMIN,
                RiskDelta.MODERATE,
                PhysicalConsequence.INDIRECT,
                ("full_backup_standard_policy",),
            ),
        )
    if plan.operation == ChangeOperation.CONTROLLED_RELOAD:
        return (
            OperationPolicyClassification(
                ApprovalPolicyClass.STANDARD_ADMIN,
                RiskDelta.MODERATE,
                PhysicalConsequence.INDIRECT,
                ("controlled_reload_standard_policy",),
            ),
        )
    if plan.operation == ChangeOperation.RESTART_ADDON:
        return (
            OperationPolicyClassification(
                ApprovalPolicyClass.ELEVATED_ADMIN,
                RiskDelta.HIGH,
                PhysicalConsequence.INDIRECT,
                ("addon_restart_elevated_policy",),
            ),
        )
    if plan.operation == ChangeOperation.RESTART_HOME_ASSISTANT:
        return (
            OperationPolicyClassification(
                ApprovalPolicyClass.ELEVATED_ADMIN,
                RiskDelta.HIGH,
                PhysicalConsequence.INDIRECT,
                ("home_assistant_restart_elevated_policy",),
            ),
        )
    if plan.operation == ChangeOperation.SET_INPUT_BOOLEAN_STATE:
        baseline = (
            plan.operational.baseline
            if plan.operational is not None
            and isinstance(plan.operational.baseline, dict)
            else {}
        )
        dependency = baseline.get("dependency_risk")
        if not isinstance(dependency, dict):
            return (
                OperationPolicyClassification(
                    ApprovalPolicyClass.PROHIBITED,
                    RiskDelta.CRITICAL,
                    PhysicalConsequence.NONE,
                    ("helper_dependency_evidence_missing",),
                ),
            )
        complete = (
            dependency.get("consequence_evidence_complete") is True
            if owner_authoritative
            else dependency.get("evidence_complete") is True
        )
        eligible = (
            dependency.get("execution_contract_complete") is True
            if owner_authoritative
            else dependency.get("execution_eligible") is True
        )
        precision = str(
            dependency.get(
                "semantic_precision",
                "exact" if complete else "coverage_failure",
            )
        )
        evidence_reason = (
            "helper_dependency_bounded_semantic_opacity"
            if precision == "bounded_opaque"
            else (
                "helper_dependency_coverage_failure"
                if not complete
                else "helper_dependency_evidence_complete"
            )
        )
        consequence = dependency.get("physical_consequence")
        if eligible and consequence == "none":
            return (
                OperationPolicyClassification(
                    ApprovalPolicyClass.STANDARD_ADMIN,
                    RiskDelta.LOW,
                    PhysicalConsequence.NONE,
                    (
                        "exact_input_boolean_state_standard_policy",
                        evidence_reason,
                        "no_consequential_dependency_detected",
                    ),
                ),
            )
        if eligible:
            return (
                OperationPolicyClassification(
                    ApprovalPolicyClass.ELEVATED_ADMIN,
                    RiskDelta.HIGH,
                    (
                        PhysicalConsequence.SAFETY_CRITICAL
                        if consequence == "safety_critical"
                        else PhysicalConsequence.DIRECT
                        if consequence == "direct"
                        else (
                            PhysicalConsequence.UNKNOWN
                            if owner_authoritative
                            else PhysicalConsequence.INDIRECT
                        )
                    ),
                    (
                        (
                            "consequential_helper_dependency_detected"
                            if consequence in {"direct", "safety_critical"}
                            else "unknown_helper_dependency_effect"
                        ),
                        "exact_input_boolean_state_elevated_policy",
                        evidence_reason,
                    ),
                ),
            )
        if not owner_authoritative and precision == "bounded_opaque":
            return (
                OperationPolicyClassification(
                    ApprovalPolicyClass.ELEVATED_ADMIN,
                    RiskDelta.HIGH,
                    (
                        PhysicalConsequence.SAFETY_CRITICAL
                        if consequence == "safety_critical"
                        else PhysicalConsequence.DIRECT
                        if consequence == "direct"
                        else PhysicalConsequence.INDIRECT
                    ),
                    (
                        "exact_input_boolean_state_elevated_policy",
                        "helper_dependency_bounded_semantic_opacity",
                        "helper_dependency_execution_ineligible",
                    ),
                ),
            )
        if not owner_authoritative:
            return (
                OperationPolicyClassification(
                    ApprovalPolicyClass.ELEVATED_ADMIN,
                    RiskDelta.HIGH,
                    PhysicalConsequence.INDIRECT,
                    (
                        "exact_input_boolean_state_elevated_policy",
                        "helper_dependency_coverage_failure",
                        "low_risk_not_established",
                    ),
                ),
            )
        return (
            OperationPolicyClassification(
                ApprovalPolicyClass.PROHIBITED,
                RiskDelta.CRITICAL,
                (
                    PhysicalConsequence.SAFETY_CRITICAL
                    if consequence == "safety_critical"
                    else PhysicalConsequence.DIRECT
                    if consequence == "direct"
                    else PhysicalConsequence.UNKNOWN
                ),
                (
                    "helper_execution_contract_incomplete",
                    "technical_execution_authority_required",
                ),
            ),
        )
    if plan.operation == ChangeOperation.UPDATE_DASHBOARD:
        elevated = plan.risk.level is RiskLevel.HIGH
        return (
            OperationPolicyClassification(
                (
                    ApprovalPolicyClass.ELEVATED_ADMIN
                    if elevated
                    else ApprovalPolicyClass.STANDARD_ADMIN
                ),
                RiskDelta.HIGH if elevated else RiskDelta.MODERATE,
                PhysicalConsequence.NONE,
                (
                    "dashboard_action_change_elevated_policy"
                    if elevated
                    else "dashboard_update_standard_policy",
                    "operator_accepted_non_atomic_dashboard_save",
                ),
            ),
        )
    if plan.operation == ChangeOperation.CONFIGURATION_PLAN:
        if not plan.operations:
            return (
                OperationPolicyClassification(
                    ApprovalPolicyClass.PROHIBITED,
                    RiskDelta.CRITICAL,
                    PhysicalConsequence.NONE,
                    ("unknown_policy_classification",),
                ),
            )
        return tuple(
            configuration_operation_policy(
                operation,
                owner_authoritative=owner_authoritative,
            )
            for operation in plan.operations
        )
    if plan.operation in {
        ChangeOperation.CREATE_AUTOMATION,
        ChangeOperation.UPDATE_AUTOMATION,
    }:
        synthetic = ConfigurationOperation(
            operation_id="legacy_automation_operation",
            order=0,
            depends_on=[],
            resource_type="automation",
            action=(
                "create"
                if plan.operation == ChangeOperation.CREATE_AUTOMATION
                else "update"
            ),
            target_id=plan.target_id,
            helper_type=None,
            proposed_config=plan.proposed_config,
            current_config=plan.current_config,
            normalized_proposed_config=plan.normalized_proposed_config,
            normalized_current_config=plan.normalized_current_config,
            current_state_fingerprint=plan.current_state_fingerprint,
            proposed_config_hash=plan.proposed_config_hash,
            normalization_version=plan.normalization_version,
            risk=plan.risk,
            warnings=plan.warnings,
        )
        return (
            configuration_operation_policy(
                synthetic,
                # ADR-022 intentionally authorizes only the current typed
                # configuration-plan route. The legacy contract-v1 route
                # retains its historical consequence-policy boundary.
                owner_authoritative=False,
            ),
        )
    return (
        OperationPolicyClassification(
            ApprovalPolicyClass.PROHIBITED,
            RiskDelta.CRITICAL,
            PhysicalConsequence.NONE,
            ("unknown_policy_classification",),
        ),
    )


def aggregate_policy_classifications(
    values: Iterable[OperationPolicyClassification],
) -> OperationPolicyClassification:
    classifications = tuple(values)
    if not classifications:
        return OperationPolicyClassification(
            ApprovalPolicyClass.PROHIBITED,
            RiskDelta.CRITICAL,
            PhysicalConsequence.NONE,
            ("unknown_policy_classification",),
        )
    return OperationPolicyClassification(
        max(classifications, key=lambda item: _POLICY_RANK[item.policy_class]).policy_class,
        max(classifications, key=lambda item: _RISK_RANK[item.risk_delta]).risk_delta,
        max(
            classifications,
            key=lambda item: _CONSEQUENCE_RANK[item.physical_consequence],
        ).physical_consequence,
        tuple(
            sorted(
                {
                    reason
                    for classification in classifications
                    for reason in classification.reason_codes
                }
            )
        ),
    )


def policy_subject_payload(plan: ChangePlan) -> dict[str, Any]:
    """Return the immutable normalized authority evaluated by F2 policy."""

    operations = [
        {
            "operation_id": operation.operation_id,
            "order": operation.order,
            "depends_on": list(operation.depends_on),
            "resource_type": operation.resource_type,
            "helper_type": operation.helper_type,
            "action": operation.action,
            "target_id": operation.target_id,
            "current_state_fingerprint": operation.current_state_fingerprint,
            "proposed_config_hash": operation.proposed_config_hash,
            "raw_proposed_config_hash": stable_hash(operation.proposed_config),
            "normalized_proposed_config_hash": stable_hash(
                operation.normalized_proposed_config
            ),
            "normalization_version": operation.normalization_version,
            "risk_level": operation.risk.level.value,
            "risk_apply_allowed": operation.risk.apply_allowed,
            "risk_reasons": sorted(set(operation.risk.reasons)),
            "risk_evidence": sorted(
                (
                    dict(item)
                    for item in operation.risk.evidence
                    if isinstance(item, dict)
                ),
                key=lambda item: stable_hash(item),
            ),
            "risk_warnings": sorted(set(operation.risk.warnings)),
            **(
                {
                    "semantic_projection_hash": (
                        operation.semantic_projection_hash
                    )
                }
                if operation.semantic_projection_hash is not None
                else {}
            ),
        }
        for operation in sorted(plan.operations, key=lambda item: item.order)
    ]
    operational = plan.operational
    return {
        "contract_version": plan.contract_version,
        "plan_family": plan.plan_family,
        "plan_id": plan.plan_id,
        "plan_version": plan.plan_version,
        "created_at": plan.created_at,
        "expires_at": plan.expires_at,
        "title": plan.title,
        "description": plan.description,
        "requested_by": plan.requested_by,
        "operation": plan.operation.value,
        "target": {
            "target_type": plan.target_type,
            "target_id": plan.target_id,
        },
        "current_state_fingerprint": plan.current_state_fingerprint,
        "proposed_config_hash": plan.proposed_config_hash,
        "raw_proposed_config_hash": stable_hash(plan.proposed_config),
        "normalized_proposed_config_hash": stable_hash(
            plan.normalized_proposed_config
        ),
        "normalization_version": plan.normalization_version,
        "risk": {
            "level": plan.risk.level.value,
            "apply_allowed": plan.risk.apply_allowed,
            "reasons": sorted(set(plan.risk.reasons)),
            "evidence": sorted(
                (
                    dict(item)
                    for item in plan.risk.evidence
                    if isinstance(item, dict)
                ),
                key=lambda item: stable_hash(item),
            ),
            "warnings": sorted(set(plan.risk.warnings)),
        },
        "operations": operations,
        "operational": (
            {
                "schema_version": operational.schema_version,
                "family": operational.family,
                "operation": operational.operation,
                "requested_name": operational.requested_name,
                "provider": operational.provider,
                "provider_capability_evidence": (
                    operational.provider_capability_evidence
                ),
                "expected_effects": operational.expected_effects,
                "preconditions": operational.preconditions,
                "verification_contract": operational.verification_contract,
                "baseline": operational.baseline,
                "limitations": operational.limitations,
                "rollback_available": operational.rollback_available,
            }
            if operational is not None
            else None
        ),
    }


def _evaluate_change_policy_version(
    plan: ChangePlan,
    *,
    policy_version: str,
    owner_authoritative: bool,
) -> ChangePolicyDecision:
    classification = aggregate_policy_classifications(
        _single_plan_policy(
            plan,
            owner_authoritative=owner_authoritative,
        )
    )
    dependency_binding = (
        plan.operational.baseline.get("dependency_risk")
        if plan.operation == ChangeOperation.SET_INPUT_BOOLEAN_STATE
        and plan.operational is not None
        and isinstance(plan.operational.baseline, dict)
        else None
    )
    exact_owner_authoritative = bool(
        owner_authoritative
        and (
            (
                isinstance(dependency_binding, dict)
                and dependency_binding.get("execution_contract_complete")
                is True
            )
            or (
                plan.operation == ChangeOperation.CONFIGURATION_PLAN
                and bool(plan.operations)
                and all(
                    _exact_existing_automation_update(operation)
                    for operation in plan.operations
                )
            )
            or _exact_existing_dashboard_update(plan)
        )
    )
    required = {
        ApprovalPolicyClass.STANDARD_ADMIN: (
            ApprovalActionKind.PLAN_APPROVAL,
        ),
        ApprovalPolicyClass.ELEVATED_ADMIN: (
            (ApprovalActionKind.PLAN_APPROVAL,)
            if exact_owner_authoritative
            else (
                ApprovalActionKind.PLAN_APPROVAL,
                ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT,
            )
        ),
        ApprovalPolicyClass.PROHIBITED: (),
    }[classification.policy_class]
    subject_hash = stable_hash(policy_subject_payload(plan))
    decision_payload = {
        "policy_version": policy_version,
        "policy_class": classification.policy_class.value,
        "risk_delta": classification.risk_delta.value,
        "physical_consequence": classification.physical_consequence.value,
        "reason_codes": list(classification.reason_codes),
        "required_acknowledgements": [item.value for item in required],
        "policy_subject_hash": subject_hash,
    }
    return ChangePolicyDecision(
        policy_version=policy_version,
        policy_class=classification.policy_class,
        risk_delta=classification.risk_delta,
        physical_consequence=classification.physical_consequence,
        reason_codes=classification.reason_codes,
        required_acknowledgements=required,
        policy_subject_hash=subject_hash,
        policy_decision_hash=stable_hash(decision_payload),
    )


def evaluate_change_policy(plan: ChangePlan) -> ChangePolicyDecision:
    """Evaluate a fresh plan under current owner-authoritative policy."""

    return _evaluate_change_policy_version(
        plan,
        policy_version=POLICY_VERSION,
        owner_authoritative=True,
    )


def persisted_f2_v1_policy_snapshot_matches(plan: ChangePlan) -> bool:
    """Recognize an exact Beta 53 F2 snapshot without granting authority."""

    return bool(
        plan.policy_decision is not None
        and plan.policy_decision.policy_version == "f2-v1"
        and plan.policy_decision
        == _evaluate_change_policy_version(
            plan,
            policy_version="f2-v1",
            owner_authoritative=False,
        )
    )


def policy_snapshot_matches(plan: ChangePlan) -> bool:
    return bool(
        plan.policy_decision is not None
        and plan.policy_decision == evaluate_change_policy(plan)
    )
