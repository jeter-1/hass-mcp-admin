"""Read-only conversion of existing plans into F3-C1 proposals."""

from __future__ import annotations

from typing import Any

from ..governance.models import (
    ChangeOperation,
    ChangePlan,
    ConfigurationOperation,
)
from ..governance.normalize import stable_hash
from .models import ConfigurationOperationProposal


def proposal_from_configuration_operation(
    plan: ChangePlan,
    operation: ConfigurationOperation,
    *,
    task_id: str,
    plan_hash: str,
    approval_bundle_hash: str,
    approval_consumed: bool,
    provider_admitted: bool,
    policy_snapshot_valid: bool,
) -> ConfigurationOperationProposal:
    """Consume contract-v2 fields without changing planning semantics."""

    if plan.contract_version != 2 or plan.operation != (
        ChangeOperation.CONFIGURATION_PLAN
    ):
        raise ValueError("a contract-v2 configuration plan is required")
    matching = [
        item
        for item in plan.operations
        if item.operation_id == operation.operation_id
    ]
    if len(matching) != 1 or matching[0] is not operation:
        raise ValueError("operation is not the exact plan member")
    resource_type = (
        operation.helper_type
        if operation.resource_type == "helper"
        else operation.resource_type
    )
    if resource_type not in {
        "automation",
        "script",
        "input_boolean",
        "input_number",
    }:
        raise ValueError("configuration resource type is not reviewed")
    if plan.policy_decision is None:
        raise ValueError("configuration plan has no F2 policy decision")
    return ConfigurationOperationProposal.from_configs(
        plan_id=plan.plan_id,
        plan_hash=plan_hash,
        plan_contract_version=plan.contract_version,
        task_id=task_id,
        operation_id=operation.operation_id,
        order=operation.order,
        depends_on=tuple(operation.depends_on),
        resource_type=resource_type,
        action=operation.action,
        target_id=operation.target_id,
        current_config=operation.current_config,
        proposed_config=operation.proposed_config,
        current_state_fingerprint=operation.current_state_fingerprint,
        proposed_config_hash=operation.proposed_config_hash,
        normalization_version=operation.normalization_version,
        risk_level=operation.risk.level.value,
        risk_evidence_hash=_risk_evidence_hash(operation.risk),
        policy_class=plan.policy_decision.policy_class.value,
        policy_decision_hash=plan.policy_decision.policy_decision_hash,
        approval_bundle_hash=approval_bundle_hash,
        plan_expires_at=plan.expires_at,
        approval_consumed=approval_consumed,
        policy_snapshot_valid=policy_snapshot_valid,
        provider_admitted=provider_admitted,
        rollback_available=False,
    )


def proposal_from_legacy_automation_plan(
    plan: ChangePlan,
    *,
    task_id: str,
    plan_hash: str,
    approval_bundle_hash: str,
    approval_consumed: bool,
    provider_admitted: bool,
    policy_snapshot_valid: bool,
) -> ConfigurationOperationProposal:
    """Represent the exact contract-v1 automation compatibility boundary."""

    if plan.contract_version != 1 or plan.operation not in {
        ChangeOperation.CREATE_AUTOMATION,
        ChangeOperation.UPDATE_AUTOMATION,
    }:
        raise ValueError("a legacy automation plan is required")
    if plan.policy_decision is None:
        raise ValueError("legacy plan has no F2 policy decision")
    action = (
        "create"
        if plan.operation == ChangeOperation.CREATE_AUTOMATION
        else "update"
    )
    return ConfigurationOperationProposal.from_configs(
        plan_id=plan.plan_id,
        plan_hash=plan_hash,
        plan_contract_version=1,
        task_id=task_id,
        operation_id="legacy_automation_operation",
        order=0,
        depends_on=(),
        resource_type="automation",
        action=action,
        target_id=plan.target_id,
        current_config=plan.current_config,
        proposed_config=plan.proposed_config,
        current_state_fingerprint=plan.current_state_fingerprint,
        proposed_config_hash=plan.proposed_config_hash,
        normalization_version=plan.normalization_version,
        risk_level=plan.risk.level.value,
        risk_evidence_hash=_risk_evidence_hash(plan.risk),
        policy_class=plan.policy_decision.policy_class.value,
        policy_decision_hash=plan.policy_decision.policy_decision_hash,
        approval_bundle_hash=approval_bundle_hash,
        plan_expires_at=plan.expires_at,
        approval_consumed=approval_consumed,
        policy_snapshot_valid=policy_snapshot_valid,
        provider_admitted=provider_admitted,
        rollback_available=(action == "update"),
    )


def _risk_evidence_hash(risk: Any) -> str:
    return stable_hash(
        {
            "level": risk.level.value,
            "reasons": list(risk.reasons),
            "apply_allowed": risk.apply_allowed,
            "evidence": list(risk.evidence),
            "warnings": list(risk.warnings),
        }
    )
