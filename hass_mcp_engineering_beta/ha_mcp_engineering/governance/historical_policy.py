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
    ChangeOperation,
    ChangePlan,
    ChangePolicyDecision,
    PhysicalConsequence,
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


def _is_exact_transition_plan(plan: ChangePlan) -> bool:
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
        or not _is_exact_transition_plan(plan)
        or not persisted_policy_snapshot_integrity_matches(plan)
        or _decision_shape(evaluate_change_policy(plan))
        != _CURRENT_RETAINED_EFFECT_SHAPE
    ):
        return None

    stored_shape = _decision_shape(decision)
    for profile, source_commit, expected_shape in _HISTORICAL_PROFILES:
        if stored_shape == expected_shape:
            return HistoricalPolicyProjectionMatch(
                model=HISTORICAL_POLICY_PROJECTION_MODEL,
                profile=profile,
                source_commit=source_commit,
            )
    return None
