"""Read-only compatibility for exact historical F2 policy snapshots.

Policy decisions are immutable authority snapshots, but the original
``f2-v1`` identifier did not change when the retained-effect rule changed.
This module recognizes only the two source-reviewed transition decisions and
only when the plan is already terminal.  It is a projection compatibility
boundary; it never authorizes approval, task creation, or provider dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
import hmac

from .models import (
    ApprovalActionKind,
    ApprovalPolicyClass,
    ApprovalState,
    ChangeOperation,
    ChangePlan,
    ChangePolicyDecision,
    PhysicalConsequence,
    PlanStatus,
    RiskDelta,
)
from .normalize import stable_hash
from .policy import evaluate_change_policy, policy_subject_payload
from .storage import is_terminal_plan


HISTORICAL_POLICY_PROJECTION_MODEL = (
    "beta34-historical-policy-projection-v1"
)
HISTORICAL_POLICY_PROJECTION_PROFILES = (
    "beta32_retained_effect_prohibited",
    "beta33_initial_retained_effect_reason",
)
BETA32_POLICY_SOURCE_COMMIT = (
    "f9d660499a05edef6af7fd9a590d7827b5983e3a"
)
BETA33_INITIAL_POLICY_SOURCE_COMMIT = (
    "5b149b04cb12ee42abf19fc6a37ec2017c8bb0bf"
)


@dataclass(frozen=True)
class HistoricalPolicyProjectionMatch:
    """Bounded internal evidence for one accepted historical snapshot."""

    model: str
    profile: str
    source_commit: str


@dataclass(frozen=True)
class _DecisionShape:
    policy_version: str
    policy_class: ApprovalPolicyClass
    risk_delta: RiskDelta
    physical_consequence: PhysicalConsequence
    reason_codes: tuple[str, ...]
    required_acknowledgements: tuple[ApprovalActionKind, ...]


_CURRENT_RETAINED_EFFECT_SHAPE = _DecisionShape(
    policy_version="f2-v1",
    policy_class=ApprovalPolicyClass.ELEVATED_ADMIN,
    risk_delta=RiskDelta.MODERATE,
    physical_consequence=PhysicalConsequence.SAFETY_CRITICAL,
    reason_codes=(
        "non_risk_increasing_condition_guard_added",
        "retained_safety_critical_effect",
        "safety_critical_effect_requires_elevated_review",
        "supported_configuration_change",
    ),
    required_acknowledgements=(
        ApprovalActionKind.PLAN_APPROVAL,
        ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT,
    ),
)

_BETA32_RETAINED_EFFECT_SHAPE = _DecisionShape(
    policy_version="f2-v1",
    policy_class=ApprovalPolicyClass.PROHIBITED,
    risk_delta=RiskDelta.HIGH,
    physical_consequence=PhysicalConsequence.SAFETY_CRITICAL,
    reason_codes=(
        "safety_critical_effect_not_reviewed",
        "supported_configuration_change",
    ),
    required_acknowledgements=(),
)

_BETA33_INITIAL_RETAINED_EFFECT_SHAPE = _DecisionShape(
    policy_version="f2-v1",
    policy_class=ApprovalPolicyClass.ELEVATED_ADMIN,
    risk_delta=RiskDelta.MODERATE,
    physical_consequence=PhysicalConsequence.SAFETY_CRITICAL,
    reason_codes=(
        "retained_safety_critical_effect",
        "risk_reducing_condition_guard_added",
        "safety_critical_effect_requires_elevated_review",
        "supported_configuration_change",
    ),
    required_acknowledgements=(
        ApprovalActionKind.PLAN_APPROVAL,
        ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT,
    ),
)

_HISTORICAL_PROFILES = (
    (
        HISTORICAL_POLICY_PROJECTION_PROFILES[0],
        BETA32_POLICY_SOURCE_COMMIT,
        _BETA32_RETAINED_EFFECT_SHAPE,
    ),
    (
        HISTORICAL_POLICY_PROJECTION_PROFILES[1],
        BETA33_INITIAL_POLICY_SOURCE_COMMIT,
        _BETA33_INITIAL_RETAINED_EFFECT_SHAPE,
    ),
)


def _decision_shape(decision: ChangePolicyDecision) -> _DecisionShape:
    return _DecisionShape(
        policy_version=decision.policy_version,
        policy_class=decision.policy_class,
        risk_delta=decision.risk_delta,
        physical_consequence=decision.physical_consequence,
        reason_codes=decision.reason_codes,
        required_acknowledgements=decision.required_acknowledgements,
    )


def _decision_payload(decision: ChangePolicyDecision) -> dict[str, object]:
    return {
        "policy_version": decision.policy_version,
        "policy_class": decision.policy_class.value,
        "risk_delta": decision.risk_delta.value,
        "physical_consequence": decision.physical_consequence.value,
        "reason_codes": list(decision.reason_codes),
        "required_acknowledgements": [
            item.value for item in decision.required_acknowledgements
        ],
        "policy_subject_hash": decision.policy_subject_hash,
    }


def persisted_policy_snapshot_integrity_matches(plan: ChangePlan) -> bool:
    """Validate stored subject and decision hashes without current policy."""

    decision = plan.policy_decision
    if decision is None:
        return False
    expected_subject_hash = stable_hash(policy_subject_payload(plan))
    expected_decision_hash = stable_hash(_decision_payload(decision))
    return bool(
        hmac.compare_digest(
            decision.policy_subject_hash, expected_subject_hash
        )
        and hmac.compare_digest(
            decision.policy_decision_hash, expected_decision_hash
        )
    )


def _is_exact_configuration_transition_plan(plan: ChangePlan) -> bool:
    if (
        plan.contract_version != 2
        or plan.plan_family != "configuration_change"
        or plan.operation is not ChangeOperation.CONFIGURATION_PLAN
        or plan.target_type != "configuration_plan"
        or plan.target_id != plan.plan_id
        or len(plan.operations) != 1
        or plan.operational is not None
    ):
        return False
    operation = plan.operations[0]
    return bool(
        operation.order == 0
        and not operation.depends_on
        and operation.resource_type == "automation"
        and operation.helper_type is None
        and operation.action == "update"
        and bool(operation.target_id)
        and isinstance(operation.current_config, dict)
        and isinstance(operation.proposed_config, dict)
        and isinstance(operation.normalized_current_config, dict)
        and isinstance(operation.normalized_proposed_config, dict)
    )


def _legacy_authority_is_inert(plan: ChangePlan) -> bool:
    approval = plan.approval
    return bool(
        approval.authority_version == 3
        and approval.approval_kind == "apply"
        and approval.policy_decision_hash
        == plan.policy_decision.policy_decision_hash
        and approval.policy_class
        == plan.policy_decision.policy_class.value
        and approval.channel is None
        and approval.approver_principal is None
        and approval.principal_separation_enforced is None
        and approval.approved_at is None
        and approval.approving_caller_id is None
        and approval.approval_note is None
        and approval.bound_plan_hash is None
        and approval.consumed_at is None
        and approval.approval_expires_at is None
        and approval.challenge_id is None
        and approval.challenge_requested_at is None
        and approval.challenge_expires_at is None
        and approval.challenge_plan_version is None
        and approval.challenge_target_type is None
        and approval.challenge_target_id is None
        and approval.challenge_operation is None
        and approval.challenge_risk_level is None
        and approval.request_note is None
        and approval.csrf_digest is None
        and approval.csrf_issued_at is None
        and approval.same_principal_confirmed is None
        and approval.elevated_risk_acknowledgement is None
    )


def _legacy_execution_is_inert(plan: ChangePlan) -> bool:
    verification = plan.verification
    rollback = plan.rollback
    return bool(
        plan.applied_at is None
        and plan.apply_request_id is None
        and plan.post_apply_fingerprint is None
        and plan.snapshot is None
        and plan.failure_information is None
        and verification.status == "not_run"
        and verification.checked_at is None
        and verification.desired_fingerprint is None
        and verification.actual_fingerprint is None
        and verification.config_check_status is None
        and not verification.mismatch_fields
        and verification.duration_ms is None
        and plan.configuration_check_status in {None, "not_run"}
        and rollback.requested_at is None
        and rollback.approved_at is None
        and rollback.rolled_back_at is None
        and rollback.request_id is None
        and rollback.expected_current_fingerprint is None
        and rollback.failure_code is None
        and plan.execution_outcome in {None, "not_started", "not_applied"}
    )


def _legacy_event_sequence_is_reviewed(plan: ChangePlan) -> bool:
    observed = tuple(
        (event.event, event.result_status, event.error_code)
        for event in plan.events
    )
    return observed in {
        (("change_plan_created", "success", None),),
        (
            ("change_plan_created", "success", None),
            ("change_plan_expired", "rejected", "change_plan_expired"),
        ),
        (
            ("change_plan_created", "success", None),
            ("policy_approval_rejected", "rejected", "prohibited_change"),
            ("change_apply_rejected", "rejected", "prohibited_change"),
            ("change_plan_expired", "rejected", "change_plan_expired"),
        ),
    }


def _is_exact_legacy_transition_plan(plan: ChangePlan) -> bool:
    """Recognize only source-reviewed contract-v1 retained-effect records."""

    decision = plan.policy_decision
    if (
        decision is None
        or plan.contract_version != 1
        or plan.plan_version != 1
        or plan.operation is not ChangeOperation.UPDATE_AUTOMATION
        or plan.target_type != "automation"
        or not plan.target_id
        or plan.target_id == plan.plan_id
        or plan.operations
        or plan.operational is not None
        or not isinstance(plan.current_config, dict)
        or not isinstance(plan.proposed_config, dict)
        or not isinstance(plan.normalized_current_config, dict)
        or not isinstance(plan.normalized_proposed_config, dict)
        or plan.risk.apply_allowed
        or not _legacy_authority_is_inert(plan)
        or not _legacy_execution_is_inert(plan)
        or not _legacy_event_sequence_is_reviewed(plan)
    ):
        return False

    approval = plan.approval
    return bool(
        (
            plan.status is PlanStatus.AWAITING_APPROVAL
            and approval.state is ApprovalState.REQUIRED
            and approval.bundle_state == "prohibited"
            and len(plan.events) == 1
        )
        or (
            plan.status is PlanStatus.EXPIRED
            and approval.state is ApprovalState.INVALIDATED
            and approval.bundle_state == "invalidated"
            and any(
                event.event == "change_plan_expired"
                for event in plan.events
            )
        )
    )


def historical_policy_projection_match(
    plan: ChangePlan,
) -> HistoricalPolicyProjectionMatch | None:
    """Recognize an exact, terminal, source-reviewed transition snapshot.

    Current evaluation is used only to prove that the immutable subject still
    belongs to the narrow corrected retained-effect policy family.  The
    historical decision itself must independently retain exact subject and
    decision hashes.  Active records deliberately do not qualify.
    """

    decision = plan.policy_decision
    if (
        decision is None
        or not is_terminal_plan(plan)
        or not (
            _is_exact_configuration_transition_plan(plan)
            or _is_exact_legacy_transition_plan(plan)
        )
        or not persisted_policy_snapshot_integrity_matches(plan)
        or _decision_shape(evaluate_change_policy(plan))
        != _CURRENT_RETAINED_EFFECT_SHAPE
    ):
        return None

    stored_shape = _decision_shape(decision)
    if (
        plan.contract_version == 1
        and stored_shape != _BETA32_RETAINED_EFFECT_SHAPE
    ):
        return None
    for profile, source_commit, expected_shape in _HISTORICAL_PROFILES:
        if stored_shape == expected_shape:
            return HistoricalPolicyProjectionMatch(
                model=HISTORICAL_POLICY_PROJECTION_MODEL,
                profile=profile,
                source_commit=source_commit,
            )
    return None
