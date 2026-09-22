"""Read-only compatibility for exact historical F2 policy snapshots.

Policy decisions are immutable authority snapshots, but the original
``f2-v1`` identifier did not change when the retained-effect rule changed.
The original transition matcher and the separate nonexecution matcher recognize
only source-reviewed terminal records. This is a projection compatibility
boundary; it never authorizes approval, task creation, or provider dispatch.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
import hmac
import re

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
    RiskLevel,
)
from .normalize import normalize_automation, stable_hash, state_fingerprint
from .policy import (
    evaluate_change_policy,
    persisted_f2_v1_policy_snapshot_matches,
    policy_snapshot_matches,
    policy_subject_payload,
)
from .storage import is_terminal_plan
from .validation import validate_automation


HISTORICAL_POLICY_PROJECTION_MODEL = (
    "beta34-historical-policy-projection-v1"
)
HISTORICAL_POLICY_PROJECTION_PROFILES = (
    "beta32_retained_effect_prohibited",
    "beta33_initial_retained_effect_reason",
    "beta38_expired_incomplete_helper",
    "f2_v1_validation_failed_automation",
    "f2_v2_validation_failed_automation",
)
BETA38_TERMINAL_SOURCE_COMMIT = "171e4769faef9a188e0a57cfa024f401f6313e1a"
BETA4_TERMINAL_SOURCE_COMMIT = "c07bc54ae0cf9d52a3bd205f2a24de704f7b8296"
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
    policy_version="f2-v2",
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


def _configuration_plan_hash(plan: ChangePlan) -> str:
    """Recompute the accepted contract-v2 approval binding."""

    immutable_operations = []
    for operation in sorted(plan.operations, key=lambda item: item.order):
        immutable_operation = {
            "operation_id": operation.operation_id,
            "order": operation.order,
            "depends_on": list(operation.depends_on),
            "resource_type": operation.resource_type,
            "helper_type": operation.helper_type,
            "action": operation.action,
            "target_id": operation.target_id,
            "current_state_fingerprint": (
                operation.current_state_fingerprint
            ),
            "proposed_config_hash": operation.proposed_config_hash,
            "raw_proposed_config_hash": stable_hash(
                operation.proposed_config
            ),
            "normalized_proposed_config_hash": stable_hash(
                operation.normalized_proposed_config
            ),
            "normalization_version": operation.normalization_version,
            "risk_level": operation.risk.level.value,
            "risk_apply_allowed": operation.risk.apply_allowed,
        }
        if operation.semantic_projection_hash is not None:
            immutable_operation["semantic_projection_hash"] = (
                operation.semantic_projection_hash
            )
        immutable_operations.append(immutable_operation)
    immutable: dict[str, object] = {
        "contract_version": plan.contract_version,
        "plan_id": plan.plan_id,
        "plan_version": plan.plan_version,
        "operation": plan.operation.value,
        "target_type": plan.target_type,
        "target_id": plan.target_id,
        "expires_at": plan.expires_at,
        "operations": immutable_operations,
        "risk_level": plan.risk.level.value,
        "risk_apply_allowed": plan.risk.apply_allowed,
        "approval_kind": plan.approval.approval_kind,
        "approval_authority_version": plan.approval.authority_version,
    }
    if plan.policy_decision is not None:
        immutable["policy_decision"] = plan.policy_decision.to_dict()
    return stable_hash(immutable)


def _top_level_authority_is_absent(plan: ChangePlan) -> bool:
    approval = plan.approval
    return bool(
        approval.channel is None
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
    )


def _approval_decision_binding_matches(plan: ChangePlan) -> bool:
    decision = plan.policy_decision
    approval = plan.approval
    return bool(
        decision is not None
        and approval.authority_version == 3
        and approval.approval_kind == "apply"
        and approval.policy_decision_hash
        == decision.policy_decision_hash
        and approval.policy_class == decision.policy_class.value
    )


def _invalidated_elevated_acknowledgement_matches(plan: ChangePlan) -> bool:
    acknowledgement = plan.approval.elevated_risk_acknowledgement
    return bool(
        acknowledgement is not None
        and acknowledgement.kind
        is ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT
        and acknowledgement.authority_version == 3
        and acknowledgement.state is ApprovalState.INVALIDATED
        and acknowledgement.bound_plan_hash is None
        and acknowledgement.policy_decision_hash is None
        and acknowledgement.policy_class is None
        and acknowledgement.risk_delta is None
        and acknowledgement.physical_consequence is None
        and acknowledgement.challenge_id is None
        and acknowledgement.challenge_requested_at is None
        and acknowledgement.challenge_expires_at is None
        and acknowledgement.granted_at is None
        and acknowledgement.approver_principal is None
        and acknowledgement.consumed_at is None
        and acknowledgement.csrf_digest is None
        and acknowledgement.csrf_issued_at is None
    )


def _consumed_elevated_bundle_matches(plan: ChangePlan) -> bool:
    decision = plan.policy_decision
    approval = plan.approval
    acknowledgement = approval.elevated_risk_acknowledgement
    if decision is None or acknowledgement is None:
        return False
    expected_plan_hash = _configuration_plan_hash(plan)
    return bool(
        plan.status is PlanStatus.APPLIED
        and approval.state is ApprovalState.CONSUMED
        and approval.bundle_state == "consumed"
        and approval.channel == "home_assistant_ingress"
        and approval.bound_plan_hash == expected_plan_hash
        and bool(approval.challenge_id)
        and bool(approval.challenge_requested_at)
        and bool(approval.challenge_expires_at)
        and approval.challenge_plan_version == plan.plan_version
        and approval.challenge_target_type == plan.target_type
        and approval.challenge_target_id == plan.target_id
        and approval.challenge_operation == plan.operation.value
        and approval.challenge_risk_level == plan.risk.level.value
        and bool(approval.approver_principal)
        and bool(approval.approved_at)
        and bool(approval.approval_expires_at)
        and bool(approval.consumed_at)
        and approval.principal_separation_enforced is True
        and approval.same_principal_confirmed is True
        and approval.csrf_digest is None
        and approval.csrf_issued_at is None
        and acknowledgement.kind
        is ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT
        and acknowledgement.authority_version == 3
        and acknowledgement.state is ApprovalState.CONSUMED
        and acknowledgement.bound_plan_hash == expected_plan_hash
        and acknowledgement.policy_decision_hash
        == decision.policy_decision_hash
        and acknowledgement.policy_class == decision.policy_class.value
        and acknowledgement.risk_delta == decision.risk_delta.value
        and acknowledgement.physical_consequence
        == decision.physical_consequence.value
        and bool(acknowledgement.challenge_id)
        and bool(acknowledgement.challenge_requested_at)
        and bool(acknowledgement.challenge_expires_at)
        and bool(acknowledgement.granted_at)
        and acknowledgement.approver_principal
        == approval.approver_principal
        and bool(acknowledgement.consumed_at)
        and acknowledgement.csrf_digest is None
        and acknowledgement.csrf_issued_at is None
    )


def persisted_historical_approval_integrity_matches(
    plan: ChangePlan,
) -> bool:
    """Validate the exact authority state of a reviewed historical shape."""

    decision = plan.policy_decision
    if decision is None or not _approval_decision_binding_matches(plan):
        return False
    stored_shape = _decision_shape(decision)
    approval = plan.approval
    if stored_shape == _BETA32_RETAINED_EFFECT_SHAPE:
        if plan.status is PlanStatus.AWAITING_APPROVAL:
            expected_state = (ApprovalState.REQUIRED, "prohibited")
        elif plan.status in {PlanStatus.SUPERSEDED, PlanStatus.EXPIRED}:
            expected_state = (ApprovalState.INVALIDATED, "invalidated")
        else:
            return False
        return bool(
            (approval.state, approval.bundle_state) == expected_state
            and _top_level_authority_is_absent(plan)
            and approval.elevated_risk_acknowledgement is None
        )
    if stored_shape != _BETA33_INITIAL_RETAINED_EFFECT_SHAPE:
        return False
    if plan.status is PlanStatus.SUPERSEDED:
        return bool(
            approval.state is ApprovalState.INVALIDATED
            and approval.bundle_state == "invalidated"
            and _top_level_authority_is_absent(plan)
            and _invalidated_elevated_acknowledgement_matches(plan)
        )
    return _consumed_elevated_bundle_matches(plan)


def _legacy_authority_is_inert(plan: ChangePlan) -> bool:
    return bool(
        _approval_decision_binding_matches(plan)
        and _top_level_authority_is_absent(plan)
        and plan.approval.elevated_risk_acknowledgement is None
    )


def _execution_and_rollback_are_inert(plan: ChangePlan) -> bool:
    """Reject execution evidence from a reviewed non-applied profile."""

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
        or not _execution_and_rollback_are_inert(plan)
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


def _historical_policy_projection_candidate(
    plan: ChangePlan,
    *,
    require_approval_integrity: bool,
) -> HistoricalPolicyProjectionMatch | None:
    decision = plan.policy_decision
    if (
        decision is None
        or not is_terminal_plan(plan)
        or not (
            _is_exact_configuration_transition_plan(plan)
            or _is_exact_legacy_transition_plan(plan)
        )
        or not persisted_policy_snapshot_integrity_matches(plan)
        or (
            require_approval_integrity
            and not persisted_historical_approval_integrity_matches(plan)
        )
        or _decision_shape(evaluate_change_policy(plan))
        != _CURRENT_RETAINED_EFFECT_SHAPE
    ):
        return None

    stored_shape = _decision_shape(decision)
    if (
        plan.contract_version == 2
        and plan.status is not PlanStatus.APPLIED
        and not _execution_and_rollback_are_inert(plan)
    ):
        return None
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


def historical_policy_projection_has_only_approval_mismatch(
    plan: ChangePlan,
) -> bool:
    """Identify a reviewed snapshot rejected only by approval integrity."""

    return bool(
        _historical_policy_projection_candidate(
            plan,
            require_approval_integrity=False,
        )
        is not None
        and not persisted_historical_approval_integrity_matches(plan)
    )


def historical_policy_projection_match(
    plan: ChangePlan,
) -> HistoricalPolicyProjectionMatch | None:
    """Recognize an exact, terminal, source-reviewed transition snapshot.

    Current evaluation is used only to prove that the immutable subject still
    belongs to the narrow corrected retained-effect policy family.  The
    historical decision and approval bundle must independently retain exact
    integrity.  Active records deliberately do not qualify.
    """

    return _historical_policy_projection_candidate(
        plan,
        require_approval_integrity=True,
    )


def _terminal_chronology_matches(plan: ChangePlan, *, expired: bool) -> bool:
    """Check writer chronology without equating record dates with provenance."""
    try:
        created, updated, expires = (
            datetime.fromisoformat(value)
            for value in (plan.created_at, plan.updated_at, plan.expires_at)
        )
        events = [
            datetime.fromisoformat(event.timestamp) for event in plan.events
        ]
        return bool(
            all(
                value.tzinfo is not None
                for value in (created, updated, expires, *events)
            )
            and created < expires
            and created <= events[0] <= events[-1] <= updated
            and events == sorted(events)
            and (events[-1] >= expires if expired else updated < expires)
        )
    except (ValueError, TypeError, IndexError):
        return False


def _expired_incomplete_helper_matches(plan: ChangePlan) -> bool:
    operational = plan.operational
    expected = _DecisionShape(
        "f2-v1",
        ApprovalPolicyClass.ELEVATED_ADMIN,
        RiskDelta.HIGH,
        PhysicalConsequence.INDIRECT,
        (
            "exact_input_boolean_state_elevated_policy",
            "helper_dependency_evidence_incomplete",
            "low_risk_not_established",
        ),
        (
            ApprovalActionKind.PLAN_APPROVAL,
            ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT,
        ),
    )
    if (
        plan.contract_version != 3
        or plan.plan_family != "operational_administration"
        or plan.operation is not ChangeOperation.SET_INPUT_BOOLEAN_STATE
        or plan.status is not PlanStatus.EXPIRED
        or plan.normalization_version != 1
        or plan.target_type != "input_boolean"
        or re.fullmatch(r"input_boolean\.[a-z0-9_]+", plan.target_id) is None
        or _decision_shape(plan.policy_decision) != expected
        or plan.approval.state is not ApprovalState.INVALIDATED
        or plan.approval.bundle_state != "invalidated"
        or not _invalidated_elevated_acknowledgement_matches(plan)
        or operational is None
        or operational.schema_version != 1
        or operational.family != plan.plan_family
        or operational.operation != plan.operation.value
        or operational.requested_name not in {"on", "off"}
        or operational.provider != "direct_home_assistant_state"
        or operational.rollback_available
        or operational.final_outcome is not None
        or plan.execution_outcome != "not_applied"
        or plan.rollback.status != "unavailable"
    ):
        return False
    baseline = operational.baseline
    dependency = baseline.get("dependency_risk")
    if not isinstance(dependency, dict):
        return False
    desired = {"state": operational.requested_name}
    provider = operational.provider_capability_evidence
    return bool(
        dependency.get("model") == "helper-dependency-risk-v2"
        and dependency.get("entity_id") == plan.target_id
        and dependency.get("evidence_complete") is False
        and dependency.get("execution_eligible") is False
        and baseline.get("entity_id") == plan.target_id
        and baseline.get("state") in {"on", "off"}
        and plan.current_config
        == plan.normalized_current_config
        == {"state": baseline["state"]}
        and plan.proposed_config == plan.normalized_proposed_config == desired
        and plan.current_state_fingerprint == stable_hash(baseline)
        and plan.proposed_config_hash == stable_hash(
            {
                "operation": plan.operation.value,
                "entity_id": plan.target_id,
                "desired_state": operational.requested_name,
            }
        )
        and provider.get("provider") == operational.provider
        and provider.get("provider_contract_model")
        == "direct-ha-exact-input-boolean-v1"
        and provider.get("fallback") == "none"
        and provider.get("fallback_occurred") is False
        and operational.dispatch == {
            "attempt_count": 0,
            "attempted_at": None,
            "dispatched": False,
            "provider_response_received": False,
            "request_id": None,
        }
        and asdict(operational.verification) == {
            "contract_version": 1,
            "status": "not_run",
            "attempt_count": 0,
            "checked_at": None,
            "operation_completed": None,
            "inventory_readable": None,
            "archive_integrity_validated": False,
            "mismatch_fields": [],
            "evidence": {},
        }
        and plan.validation_results.get("valid") is True
        and plan.validation_results.get("planning_write_performed") is False
        and plan.validation_results.get("dependency_evidence_complete") is False
        and plan.dry_run_results.get("provider_dispatch_occurred") is False
        and plan.dry_run_results.get("rollback_available") is False
        and tuple((e.event, e.result_status, e.error_code) for e in plan.events) == (
            ("set_input_boolean_state_plan_created", "success", None),
            ("change_plan_expired", "rejected", "change_plan_expired"),
        )
        and _terminal_chronology_matches(plan, expired=True)
    )


def _validation_failed_automation_matches(plan: ChangePlan) -> bool:
    if (
        plan.contract_version != 1
        or plan.plan_family != "configuration_change"
        or plan.operation is not ChangeOperation.UPDATE_AUTOMATION
        or plan.status is not PlanStatus.VALIDATION_FAILED
        or plan.normalization_version != 3
        or plan.target_type != "automation"
        or not plan.target_id
        or plan.operational is not None
        or plan.current_config is not None
        or plan.normalized_current_config is not None
        or plan.current_state_fingerprint != state_fingerprint(None)
        or plan.approval.state is not ApprovalState.REQUIRED
        or plan.approval.bundle_state != "prohibited"
        or plan.approval.elevated_risk_acknowledgement is not None
        or plan.execution_outcome is not None
        or plan.rollback.status != "not_yet_available"
        or plan.normalized_proposed_config
        != normalize_automation(plan.proposed_config)
        or plan.proposed_config_hash != stable_hash(plan.normalized_proposed_config)
    ):
        return False
    decision = plan.policy_decision
    expected = replace(
        _BETA32_RETAINED_EFFECT_SHAPE, policy_version=decision.policy_version
    )
    if (
        decision.policy_version not in {"f2-v1", "f2-v2"}
        or _decision_shape(decision) != expected
        or not (
            policy_snapshot_matches(plan)
            or persisted_f2_v1_policy_snapshot_matches(plan)
        )
    ):
        return False
    valid, errors, _ = validate_automation(plan.target_id, plan.proposed_config)
    return bool(
        not valid
        and plan.validation_results == {"valid": False, "errors": errors}
        and tuple((e.event, e.result_status, e.error_code) for e in plan.events) == (
            (
                "change_plan_validation_failed",
                "failure",
                "automation_validation_failed",
            ),
        )
        and _terminal_chronology_matches(plan, expired=False)
    )


def terminal_nonexecution_projection_match(
    plan: ChangePlan,
) -> HistoricalPolicyProjectionMatch | None:
    """Recognize only the three diagnosed, never-executed terminal shapes.

    Separate from historical_policy_projection_match: that older predicate is
    also used by shared prohibited-plan validation. This predicate may be used
    only at read projection/accounting boundaries. The service must additionally
    establish task absence before accepting a read; task storage errors are fatal.
    """
    if (
        plan.policy_decision is None
        or plan.plan_version != 1
        or plan.operations
        or plan.risk.apply_allowed
        or plan.risk.level is not RiskLevel.HIGH
        or plan.rollback.available
        or not persisted_policy_snapshot_integrity_matches(plan)
        or not _approval_decision_binding_matches(plan)
        or not _top_level_authority_is_absent(plan)
        or not _execution_and_rollback_are_inert(plan)
        or any(
            event.operation_id is not None
            or event.operation_order is not None
            or event.resource_type is not None
            or event.resource_id is not None
            for event in plan.events
        )
    ):
        return None
    if _expired_incomplete_helper_matches(plan):
        profile = "beta38_expired_incomplete_helper"
        source = BETA38_TERMINAL_SOURCE_COMMIT
    elif _validation_failed_automation_matches(plan):
        legacy = plan.policy_decision.policy_version == "f2-v1"
        profile = (
            "f2_v1_validation_failed_automation"
            if legacy else "f2_v2_validation_failed_automation"
        )
        source = (
            BETA38_TERMINAL_SOURCE_COMMIT
            if legacy else BETA4_TERMINAL_SOURCE_COMMIT
        )
    else:
        return None
    return HistoricalPolicyProjectionMatch(
        HISTORICAL_POLICY_PROJECTION_MODEL, profile, source
    )
