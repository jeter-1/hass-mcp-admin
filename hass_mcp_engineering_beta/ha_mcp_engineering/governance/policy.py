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


POLICY_VERSION = "f2-v1"

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
    PhysicalConsequence.DIRECT: 2,
    PhysicalConsequence.SAFETY_CRITICAL: 3,
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
    }
)


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


def _configuration_operation_policy(
    operation: ConfigurationOperation,
) -> OperationPolicyClassification:
    triggers = _risk_triggers(operation)
    risk_delta = _risk_delta(operation.risk.level)
    reasons = {"supported_configuration_change"}
    consequence = PhysicalConsequence.NONE

    if operation.resource_type == "helper":
        consequence = PhysicalConsequence.INDIRECT
        reasons.add("helper_change_indirect_consequence")
    if triggers & _SAFETY_CRITICAL_TRIGGERS:
        consequence = PhysicalConsequence.SAFETY_CRITICAL
        reasons.add("safety_critical_effect_not_reviewed")
    elif triggers & _DIRECT_CONSEQUENCE_TRIGGERS or any(
        "Physical-device action detected" in reason
        for reason in operation.risk.reasons
    ):
        consequence = PhysicalConsequence.DIRECT
        reasons.add("direct_physical_consequence")

    if triggers & _UNCLASSIFIABLE_TRIGGERS or any(
        "could not be bounded structurally" in warning
        for warning in operation.risk.warnings
    ):
        return OperationPolicyClassification(
            ApprovalPolicyClass.PROHIBITED,
            risk_delta,
            consequence,
            tuple(sorted({*reasons, "unknown_policy_classification"})),
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
            _configuration_operation_policy(operation)
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
        return (_configuration_operation_policy(synthetic),)
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


def evaluate_change_policy(plan: ChangePlan) -> ChangePolicyDecision:
    classification = aggregate_policy_classifications(
        _single_plan_policy(plan)
    )
    required = {
        ApprovalPolicyClass.STANDARD_ADMIN: (
            ApprovalActionKind.PLAN_APPROVAL,
        ),
        ApprovalPolicyClass.ELEVATED_ADMIN: (
            ApprovalActionKind.PLAN_APPROVAL,
            ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT,
        ),
        ApprovalPolicyClass.PROHIBITED: (),
    }[classification.policy_class]
    subject_hash = stable_hash(policy_subject_payload(plan))
    decision_payload = {
        "policy_version": POLICY_VERSION,
        "policy_class": classification.policy_class.value,
        "risk_delta": classification.risk_delta.value,
        "physical_consequence": classification.physical_consequence.value,
        "reason_codes": list(classification.reason_codes),
        "required_acknowledgements": [item.value for item in required],
        "policy_subject_hash": subject_hash,
    }
    return ChangePolicyDecision(
        policy_version=POLICY_VERSION,
        policy_class=classification.policy_class,
        risk_delta=classification.risk_delta,
        physical_consequence=classification.physical_consequence,
        reason_codes=classification.reason_codes,
        required_acknowledgements=required,
        policy_subject_hash=subject_hash,
        policy_decision_hash=stable_hash(decision_payload),
    )


def policy_snapshot_matches(plan: ChangePlan) -> bool:
    return bool(
        plan.policy_decision is not None
        and plan.policy_decision == evaluate_change_policy(plan)
    )
