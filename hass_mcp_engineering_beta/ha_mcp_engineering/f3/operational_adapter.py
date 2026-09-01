"""Runtime-inert F3 adapter for governed operational administration."""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from ha_mcp_engineering.f3.contracts import (
    AdapterCapabilityDescriptor,
    DispatchIntentRecorder,
    DispatchResult,
    F3_ADAPTER_CONTRACT_MODEL,
    NormalizedOperationOutcome,
    ObservationResult,
    OperationTarget,
    PreflightResult,
    RecoveryContext,
    VerificationResult,
)
from ha_mcp_engineering.f3.locks import (
    normalize_lock_requests as normalize_durable_lock_requests,
)

from ..governance.models import (
    ApprovalActionKind,
    ApprovalState,
    ChangePlan,
    PlanStatus,
)
from ..governance.helper_dependency import (
    read_runtime_helper_dependency_risk,
)
from .operational_locks import OperationalLockSetCalculator
from .operational_models import (
    CAPABILITY_IDENTITIES,
    CONTROLLED_RELOAD,
    CREATE_FULL_BACKUP,
    EXPECTED_EFFECT_CODES,
    OPERATIONAL_ADAPTER_ID,
    OPERATIONAL_PLAN_CONTRACT_VERSION,
    OPERATIONAL_PREPARED_AUTHORITY_MODEL,
    PROVIDER_CONTRACT_MODELS,
    PROVIDER_OPERATIONS,
    RESTART_ADDON,
    RESTART_HOME_ASSISTANT,
    SET_INPUT_BOOLEAN_STATE,
    SUPPORTED_OPERATIONS,
    TARGET_CLASSES,
    TARGET_TYPES,
    VERIFICATION_MODELS,
    OperationalAuthoritySnapshot,
    OperationalEvidenceProjection,
    OperationalEvidenceReader,
    OperationalPreparationRequest,
    PreparedOperationalOperation,
    canonical_json,
    operational_escalation_policy,
    operational_policy_expectation_is_valid,
    provider_arguments,
    recompute_operational_prepared_hash,
    stable_hash,
    validate_prepared_operational_authority,
)
from .operational_observability import (
    OperationalEventRecorder,
    OperationalMetrics,
)
from .operational_strategies import OperationalStrategy, default_strategies


AuthorityReader = Callable[
    [PreparedOperationalOperation],
    OperationalAuthoritySnapshot | Awaitable[OperationalAuthoritySnapshot],
]
Now = Callable[[], datetime]


class OperationalAdapterError(RuntimeError):
    """Bounded adapter error that never includes provider content."""

    def __init__(self, category: str) -> None:
        super().__init__("The F3 operational adapter rejected the request.")
        self.category = category


def _enum_value(value: Any) -> str:
    candidate = getattr(value, "value", value)
    return candidate if isinstance(candidate, str) else ""


def _parse_aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def _approval_bundle_hash(plan: ChangePlan) -> str:
    approval = plan.approval
    elevated = approval.elevated_risk_acknowledgement
    return stable_hash(
        {
            "authority_version": approval.authority_version,
            "channel": approval.channel,
            "approver_principal": approval.approver_principal,
            "principal_separation_enforced": approval.principal_separation_enforced,
            "bound_plan_hash": approval.bound_plan_hash,
            "approval_kind": approval.approval_kind,
            "approval_expires_at": approval.approval_expires_at,
            "policy_decision_hash": approval.policy_decision_hash,
            "policy_class": approval.policy_class,
            "bundle_state": approval.bundle_state,
            "same_principal_confirmed": approval.same_principal_confirmed,
            "elevated": (
                {
                    "action_kind": _enum_value(elevated.kind),
                    "state": _enum_value(elevated.state),
                    "principal": elevated.approver_principal,
                    "bound_plan_hash": elevated.bound_plan_hash,
                    "policy_decision_hash": elevated.policy_decision_hash,
                }
                if elevated is not None
                else None
            ),
        }
    )


def _validate_prepared(
    operation: PreparedOperationalOperation,
) -> None:
    try:
        validate_prepared_operational_authority(operation)
    except (AttributeError, TypeError, ValueError):
        raise OperationalAdapterError("prepared_operation_integrity") from None


def validate_operational_executor_timing(
    executor: Any,
    prepared: PreparedOperationalOperation,
) -> None:
    """Require exact operation timing before the executor claims a child."""

    _validate_prepared(prepared)
    try:
        duration = executor.executor_timing.post_dispatch_evidence_seconds
    except AttributeError:
        raise OperationalAdapterError(
            "executor_evidence_deadline_mismatch"
        ) from None
    if (
        type(duration) is not int
        or duration != prepared.evidence_deadline_seconds
    ):
        raise OperationalAdapterError("executor_evidence_deadline_mismatch")


class OperationalAdministrationAdapter:
    """One closed adapter delegating to exact operational strategies.

    The merged executor owns claims, durable locks, approval consumption,
    intent, duplicate handling, cancellation, and reconstruction cadence.  The
    injected evidence reader is read-only and must be backed by that same F3
    child record when F3-D activates this package.
    """

    capabilities = AdapterCapabilityDescriptor(
        adapter_id=OPERATIONAL_ADAPTER_ID,
        contract_model=F3_ADAPTER_CONTRACT_MODEL,
        operation_family="operational_administration",
        supported_operations=SUPPORTED_OPERATIONS,
        rollback_supported=False,
        readback_recovery_supported=True,
        exact_provider_contract_required=True,
    )

    def __init__(
        self,
        *,
        backup_gateway: Any,
        lifecycle_gateway: Any,
        evidence_reader: OperationalEvidenceReader,
        authority_reader: AuthorityReader,
        helper_state_gateway: Any = None,
        helper_dependency_risk_reader: Callable[..., Awaitable[
            dict[str, Any]
        ]]
        | None = None,
        metrics: OperationalMetrics | None = None,
        events: OperationalEventRecorder | None = None,
        lock_calculator: OperationalLockSetCalculator | None = None,
        strategies: dict[str, OperationalStrategy] | None = None,
        now: Now = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.metrics = metrics or OperationalMetrics()
        self.events = events or OperationalEventRecorder()
        self.evidence_reader = evidence_reader
        self.authority_reader = authority_reader
        self.lock_calculator = lock_calculator or OperationalLockSetCalculator()
        self.now = now
        self.strategies = strategies or default_strategies(
            backup_gateway=backup_gateway,
            lifecycle_gateway=lifecycle_gateway,
            helper_state_gateway=helper_state_gateway,
            helper_dependency_risk_reader=(
                helper_dependency_risk_reader
                if helper_dependency_risk_reader is not None
                else read_runtime_helper_dependency_risk
            ),
            metrics=self.metrics,
        )
        if tuple(sorted(self.strategies)) != tuple(sorted(SUPPORTED_OPERATIONS)):
            raise ValueError("operational strategy set is incomplete")
        if any(key != value.operation for key, value in self.strategies.items()):
            raise ValueError("operational strategy identity is invalid")
        self.capability_descriptors = {
            name: strategy.capability
            for name, strategy in sorted(self.strategies.items())
        }

    def _strategy(self, operation: str) -> OperationalStrategy:
        try:
            strategy = self.strategies[operation]
        except KeyError as exc:
            raise OperationalAdapterError("unknown_operation") from exc
        if strategy.capability.capability_id != CAPABILITY_IDENTITIES[operation]:
            raise OperationalAdapterError("unknown_capability_identity")
        strategy.capability.validate()
        return strategy

    async def prepare(
        self, proposal: OperationalPreparationRequest
    ) -> PreparedOperationalOperation:
        proposal.validate()
        plan = proposal.plan
        if not isinstance(plan, ChangePlan):
            raise OperationalAdapterError("invalid_plan")
        operation = _enum_value(plan.operation)
        strategy = self._strategy(operation)
        operational = plan.operational
        if (
            operational is None
            or plan.contract_version != OPERATIONAL_PLAN_CONTRACT_VERSION
            or operational.schema_version != 1
            or operational.family != "operational_administration"
            or operational.operation != operation
            or plan.plan_family != "operational_administration"
        ):
            raise OperationalAdapterError("operational_plan_contract_mismatch")
        # New execution preparation requires still-available bound authority.
        # A consumed public approval can only reconstruct its existing durable
        # child record and cannot authorize preparation of another attempt.
        if plan.status is not PlanStatus.APPROVED:
            raise OperationalAdapterError("plan_not_approved")
        if plan.approval.state is not ApprovalState.APPROVED:
            raise OperationalAdapterError("approval_not_available")
        if (
            plan.approval.bound_plan_hash != proposal.expected_plan_hash
            or plan.policy_decision is None
            or plan.approval.policy_decision_hash
            != plan.policy_decision.policy_decision_hash
        ):
            raise OperationalAdapterError("approval_hash_mismatch")
        expected_target_type = TARGET_TYPES[operation]
        if (
            plan.target_type != expected_target_type
            or (
                operation == CREATE_FULL_BACKUP
                and plan.target_id != "local_full_backup"
            )
            or (
                operation == RESTART_HOME_ASSISTANT
                and plan.target_id != "core"
            )
            or (
                operation == SET_INPUT_BOOLEAN_STATE
                and plan.target_type != "input_boolean"
            )
        ):
            raise OperationalAdapterError("target_identity_mismatch")
        policy = plan.policy_decision
        policy_values = (
            _enum_value(policy.policy_class),
            _enum_value(policy.risk_delta),
            _enum_value(policy.physical_consequence),
        )
        if not operational_policy_expectation_is_valid(
            operation,
            policy_values,
            _enum_value(plan.risk.level),
        ):
            raise OperationalAdapterError("policy_snapshot_mismatch")
        if operational.provider != strategy.capability.provider:
            raise OperationalAdapterError("provider_identity_mismatch")
        elevated = plan.approval.elevated_risk_acknowledgement
        acknowledgement_required = (
            ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT
            in policy.required_acknowledgements
        )
        if acknowledgement_required and not (
            elevated is not None
            and elevated.state is ApprovalState.APPROVED
            and elevated.bound_plan_hash == proposal.expected_plan_hash
            and elevated.policy_decision_hash == policy.policy_decision_hash
        ):
            raise OperationalAdapterError("elevated_authorization_not_bound")
        try:
            expires = _parse_aware(plan.expires_at)
        except (TypeError, ValueError):
            raise OperationalAdapterError("plan_expiration_invalid") from None
        if expires <= self.now().astimezone(timezone.utc):
            raise OperationalAdapterError("plan_expired")

        arguments = provider_arguments(
            operation, plan.target_id, operational.requested_name
        )
        arguments_json = canonical_json(arguments)
        provider_json = canonical_json(operational.provider_capability_evidence)
        baseline_json = canonical_json(operational.baseline)
        verification_json = canonical_json(operational.verification_contract)
        hold_keys, evidence_seconds = operational_escalation_policy(
            operation, plan.target_id
        )
        approval_hash = _approval_bundle_hash(plan)
        prepared = PreparedOperationalOperation(
            contract_model=F3_ADAPTER_CONTRACT_MODEL,
            adapter_id=OPERATIONAL_ADAPTER_ID,
            operation=operation,
            target=OperationTarget(plan.target_type, plan.target_id),
            current_state_fingerprint=plan.current_state_fingerprint,
            normalized_proposed_hash=plan.proposed_config_hash,
            prepared_operation_hash="0" * 64,
            risk_level=_enum_value(plan.risk.level),
            policy_decision_hash=policy.policy_decision_hash,
            approval_bundle_hash=approval_hash,
            expected_effects=EXPECTED_EFFECT_CODES[operation],
            verification_contract_model=VERIFICATION_MODELS[operation],
            verification_contract_hash=stable_hash(verification_json),
            rollback_available=False,
            capability_id=strategy.capability.capability_id,
            target_class=TARGET_CLASSES[operation],
            plan_id=plan.plan_id,
            plan_hash=proposal.expected_plan_hash,
            plan_contract_version=plan.contract_version,
            public_task_id=proposal.public_task_id,
            child_execution_id=proposal.child_execution_id,
            plan_expires_at=plan.expires_at,
            requested_name=operational.requested_name,
            provider_id=operational.provider,
            provider_contract_model=PROVIDER_CONTRACT_MODELS[operation],
            provider_operation=PROVIDER_OPERATIONS[operation],
            provider_arguments_json=arguments_json,
            provider_arguments_hash=stable_hash(arguments_json),
            provider_evidence_json=provider_json,
            baseline_json=baseline_json,
            authoritative_provider_slug=proposal.authoritative_provider_slug,
            provider_identity_evidence_hash=(
                proposal.provider_identity_evidence_hash
            ),
            policy_class=policy_values[0],
            risk_delta=policy_values[1],
            physical_consequence=policy_values[2],
            expected_effect_descriptions=tuple(operational.expected_effects),
            warnings=tuple(plan.warnings),
            limitations=tuple(operational.limitations),
            verification_contract_json=verification_json,
            evidence_deadline_class=strategy.capability.evidence_deadline_class,
            evidence_deadline_seconds=evidence_seconds,
            selective_hold_keys=hold_keys,
        )
        prepared = replace(
            prepared,
            prepared_operation_hash=recompute_operational_prepared_hash(
                prepared
            ),
        )
        _validate_prepared(prepared)
        self.metrics.increment(operation, "preparations")
        self.events.emit(
            {
                "event_type": "operation_prepared",
                "operation": operation,
                "capability_id": prepared.capability_id,
                "task_id": prepared.public_task_id,
                "plan_id": prepared.plan_id,
                "target_type": prepared.target.target_type,
            }
        )
        return prepared

    def lock_requests(
        self, operation: PreparedOperationalOperation
    ) -> tuple[Any, ...]:
        _validate_prepared(operation)
        self._strategy(operation.operation)
        return self.lock_calculator.calculate(operation)

    async def _read_authority(
        self, operation: PreparedOperationalOperation
    ) -> OperationalAuthoritySnapshot:
        result = self.authority_reader(operation)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, OperationalAuthoritySnapshot):
            raise OperationalAdapterError("authority_snapshot_invalid")
        return result

    async def _read_evidence(
        self, operation: PreparedOperationalOperation
    ) -> OperationalEvidenceProjection:
        result = self.evidence_reader.read(operation)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, OperationalEvidenceProjection):
            raise OperationalAdapterError("recovery_evidence_corrupt")
        try:
            result.validate(operation)
        except (TypeError, ValueError):
            raise OperationalAdapterError("recovery_evidence_corrupt") from None
        return result

    @staticmethod
    def _authority_mismatches(
        prepared: PreparedOperationalOperation,
        authority: OperationalAuthoritySnapshot,
    ) -> tuple[str, ...]:
        pairs = {
            "plan_identity": (authority.plan_id, prepared.plan_id),
            "plan_hash": (authority.plan_hash, prepared.plan_hash),
            "public_task_identity": (
                authority.public_task_id,
                prepared.public_task_id,
            ),
            "child_execution_identity": (
                authority.child_execution_id,
                prepared.child_execution_id,
            ),
            "active_child_execution_identity": (
                authority.active_child_execution_id,
                prepared.child_execution_id,
            ),
            "operation_identity": (authority.operation, prepared.operation),
            "target_type": (authority.target_type, prepared.target.target_type),
            "target_identity": (authority.target_id, prepared.target.target_id),
            "policy_snapshot": (
                authority.policy_decision_hash,
                prepared.policy_decision_hash,
            ),
            "approval_bundle": (
                authority.approval_bundle_hash,
                prepared.approval_bundle_hash,
            ),
        }
        mismatches = [name for name, values in pairs.items() if values[0] != values[1]]
        if (
            authority.prepared_authority_model
            != OPERATIONAL_PREPARED_AUTHORITY_MODEL
            or authority.prepared_operation_hash
            != prepared.prepared_operation_hash
        ):
            mismatches.append("prepared_operation_authority")
        if authority.authorization_evidence_status != "valid":
            mismatches.append("authorization_evidence")
        if (
            prepared.policy_class == "elevated_admin"
            and not authority.elevated_acknowledgement_bound
        ):
            mismatches.append("elevated_acknowledgement_binding")
        for field, value in (
            ("execution_task_storage", authority.execution_task_storage_status),
            ("f3_execution_storage", authority.f3_execution_storage_status),
            ("f3_lock_storage", authority.f3_lock_storage_status),
        ):
            if value != "healthy":
                mismatches.append(field)
        if prepared.operation == RESTART_HOME_ASSISTANT:
            if authority.governance_storage_status != "healthy":
                mismatches.append("governance_storage")
            if authority.audit_storage_status != "healthy":
                mismatches.append("audit_storage")
            if not authority.restart_reconciliation_compatible:
                mismatches.append("restart_reconciliation")
        return tuple(sorted(set(mismatches)))

    async def preflight(
        self,
        operation: PreparedOperationalOperation,
        *,
        acquired_locks: tuple[Any, ...],
    ) -> PreflightResult:
        _validate_prepared(operation)
        strategy = self._strategy(operation.operation)
        self.metrics.increment(operation.operation, "preflight_attempts")
        expected_locks = self.lock_requests(operation)
        try:
            acquired = normalize_durable_lock_requests(acquired_locks)
            expected = normalize_durable_lock_requests(expected_locks)
        except (TypeError, ValueError):
            acquired = ()
            expected = ("invalid",)
        if acquired != expected:
            self.metrics.increment(operation.operation, "preflight_rejections")
            self.metrics.increment(operation.operation, "lock_conflicts")
            return self._preflight_rejection(
                operation,
                outcome=NormalizedOperationOutcome.LOCK_CONFLICT,
                code="complete_lock_set_not_held",
                mismatch_fields=("lock_set",),
            )
        try:
            authority = await self._read_authority(operation)
        except OperationalAdapterError as exc:
            return self._preflight_rejection(
                operation,
                outcome=NormalizedOperationOutcome.FAILED_PRE_DISPATCH,
                code=exc.category,
            )
        mismatches = list(self._authority_mismatches(operation, authority))
        try:
            if _parse_aware(operation.plan_expires_at) <= self.now().astimezone(
                timezone.utc
            ):
                mismatches.append("plan_expiration")
        except (TypeError, ValueError):
            mismatches.append("plan_expiration")
        if mismatches:
            category = (
                "prepared_operation_authority"
                if "prepared_operation_authority" in mismatches
                else "authority_preflight_rejected"
            )
            return self._preflight_rejection(
                operation,
                outcome=NormalizedOperationOutcome.PREFLIGHT_REJECTED,
                code=category,
                mismatch_fields=tuple(sorted(set(mismatches))),
            )
        eligible, category, mismatch_fields, fresh = await strategy.preflight_evidence(
            operation
        )
        if not eligible:
            if category == "already_desired" and isinstance(fresh, dict):
                fresh_baseline = fresh.get("baseline")
                if isinstance(fresh_baseline, dict):
                    evidence_hash = stable_hash(
                        {
                            "provider": fresh.get("provider"),
                            "baseline": fresh_baseline,
                            "desired_state": operation.requested_name,
                        }
                    )
                    return PreflightResult(
                        eligible=False,
                        outcome=NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
                        confirmed_target=operation.target,
                        observed_state_fingerprint=stable_hash(fresh_baseline),
                        provider_contract=operation.provider_contract_model,
                        provider_operation=operation.provider_operation,
                        provider_arguments_hash=operation.provider_arguments_hash,
                        evidence_hash=evidence_hash,
                        diagnostic_codes=("desired_state_already_reached",),
                    )
            if category in {
                "provider_unavailable",
                "provider_timeout",
                "exact_contract_mismatch",
                "unsupported_protocol_version",
            }:
                self.metrics.increment(
                    operation.operation, "provider_admission_failures"
                )
            if category == "stale_baseline":
                self.metrics.increment(operation.operation, "stale_state_failures")
            return self._preflight_rejection(
                operation,
                outcome=(
                    NormalizedOperationOutcome.PROVIDER_UNAVAILABLE_PRE_DISPATCH
                    if category in {"provider_unavailable", "provider_timeout"}
                    else NormalizedOperationOutcome.PREFLIGHT_REJECTED
                ),
                code=category,
                mismatch_fields=mismatch_fields,
            )
        assert isinstance(fresh, dict)
        fresh_baseline = fresh.get("baseline")
        observed = (
            stable_hash(fresh_baseline)
            if isinstance(fresh_baseline, dict)
            else operation.current_state_fingerprint
        )
        return PreflightResult(
            eligible=True,
            outcome=None,
            confirmed_target=operation.target,
            observed_state_fingerprint=observed,
            provider_contract=operation.provider_contract_model,
            provider_operation=operation.provider_operation,
            provider_arguments_hash=operation.provider_arguments_hash,
            evidence_hash=stable_hash(
                {
                    "authority": {
                        "plan_id": authority.plan_id,
                        "public_task_id": authority.public_task_id,
                        "child_execution_id": authority.child_execution_id,
                        "policy_decision_hash": authority.policy_decision_hash,
                        "approval_bundle_hash": authority.approval_bundle_hash,
                    },
                    "provider": fresh.get("provider"),
                    "baseline": fresh_baseline,
                }
            ),
            diagnostic_codes=("final_locked_preflight_complete",),
        )

    def _preflight_rejection(
        self,
        operation: PreparedOperationalOperation,
        *,
        outcome: NormalizedOperationOutcome,
        code: str,
        mismatch_fields: tuple[str, ...] = (),
    ) -> PreflightResult:
        self.metrics.increment(operation.operation, "preflight_rejections")
        return PreflightResult(
            eligible=False,
            outcome=outcome,
            confirmed_target=operation.target,
            observed_state_fingerprint=None,
            provider_contract=None,
            provider_operation=None,
            provider_arguments_hash=None,
            evidence_hash=stable_hash(
                {"category": code, "mismatch_fields": list(mismatch_fields)}
            ),
            diagnostic_codes=(code,),
            mismatch_fields=mismatch_fields,
        )

    async def dispatch(
        self,
        operation: PreparedOperationalOperation,
        preflight: PreflightResult,
        *,
        before_dispatch: DispatchIntentRecorder,
    ) -> DispatchResult:
        _validate_prepared(operation)
        if (
            preflight.eligible is not True
            or preflight.confirmed_target != operation.target
            or preflight.provider_operation != operation.provider_operation
            or preflight.provider_arguments_hash != operation.provider_arguments_hash
        ):
            raise OperationalAdapterError("invalid_dispatch_preflight")
        strategy = self._strategy(operation.operation)
        # The reviewed gateway owns the narrow final-boundary callback.  C2
        # performs no probe, policy decision, evidence write, or other callback
        # after it succeeds and before the provider's sole network mutation.
        result = await strategy.dispatch(
            operation, before_dispatch=before_dispatch
        )
        self.metrics.increment(operation.operation, "intents_committed")
        self.metrics.increment(operation.operation, "dispatch_attempts")
        if result.response_received:
            self.metrics.increment(operation.operation, "responses_received")
        else:
            self.metrics.increment(operation.operation, "responses_lost")
        if result.confirmed_failure:
            self.metrics.increment(
                operation.operation, "confirmed_dispatch_failures"
            )
            outcome = NormalizedOperationOutcome.DISPATCH_FAILED_CONFIRMED
        elif not result.response_received:
            self.metrics.increment(operation.operation, "indeterminate_dispatches")
            outcome = NormalizedOperationOutcome.DISPATCH_INDETERMINATE
        else:
            outcome = NormalizedOperationOutcome.OBSERVING
        return DispatchResult(
            outcome=outcome,
            dispatch_intent_recorded=True,
            mutating_invocation_count=1,
            may_have_dispatched=True,
            provider_response_received=result.response_received,
            provider_operation_id=result.provider_operation_id,
            response_evidence_hash=result.response_evidence_hash,
            diagnostic_codes=(result.diagnostic_code,),
        )

    async def _observe(
        self,
        operation: PreparedOperationalOperation,
        *,
        provider_response_received: bool,
        recovering: bool,
        minimum_attempt: int = 1,
    ) -> ObservationResult:
        try:
            evidence = await self._read_evidence(operation)
        except OperationalAdapterError:
            return self._manual_observation(
                operation,
                attempt_count=minimum_attempt,
                reason="recovery_evidence_corrupt",
            )
        if not evidence.dispatch_intent_recorded or evidence.dispatch_count != 1:
            return self._manual_observation(
                operation,
                attempt_count=max(
                    minimum_attempt, evidence.observation_attempt_count + 1
                ),
                reason="dispatch_lineage_missing",
            )
        attempt = max(
            minimum_attempt, evidence.observation_attempt_count + 1
        )
        strategy = self._strategy(operation.operation)
        observation = await strategy.observe(
            operation,
            provider_response_received=(
                provider_response_received
                or evidence.provider_response_received
            ),
            recovering=recovering,
            evidence=evidence,
        )
        self.metrics.increment(operation.operation, "observations")
        status = observation.status
        if status == "pending" and self._deadline_expired(
            evidence.evidence_deadline
        ):
            status = "manual_review"
            self.metrics.increment(
                operation.operation, "manual_review_transitions"
            )
        if status == "verified":
            outcome = NormalizedOperationOutcome.OBSERVING
            complete = True
            intended: bool | None = True
        elif status == "failed":
            outcome = NormalizedOperationOutcome.VERIFICATION_MISMATCH
            complete = True
            intended = False
        elif status == "manual_review":
            outcome = NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED
            complete = False
            intended = None
        else:
            outcome = NormalizedOperationOutcome.OBSERVING
            complete = False
            intended = None
        return ObservationResult(
            outcome=outcome,
            attempt_count=attempt,
            observation_complete=complete,
            provider_reachable=observation.provider_reachable,
            target_reachable=observation.target_reachable,
            readback_state_fingerprint=observation.evidence_hash,
            intended_result_observed=intended,
            mismatch_fields=observation.mismatch_fields,
            evidence_hash=observation.evidence_hash,
            diagnostic_codes=(
                "evidence_deadline_expired"
                if status == "manual_review"
                else observation.diagnostic_codes[0]
            ,),
        )

    async def observe(
        self,
        operation: PreparedOperationalOperation,
        dispatch: DispatchResult | None,
    ) -> ObservationResult:
        _validate_prepared(operation)
        return await self._observe(
            operation,
            provider_response_received=(
                dispatch.provider_response_received
                if dispatch is not None
                else False
            ),
            recovering=False,
        )

    async def verify(
        self,
        operation: PreparedOperationalOperation,
        observation: ObservationResult,
    ) -> VerificationResult:
        _validate_prepared(operation)
        outcome_value = _enum_value(observation.outcome)
        if (
            outcome_value == NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED.value
        ):
            outcome = NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED
            verified: bool | None = None
            reason = "operational_evidence_unavailable"
        elif observation.observation_complete and (
            observation.intended_result_observed is True
        ):
            self.metrics.increment(operation.operation, "verification_successes")
            outcome = NormalizedOperationOutcome.SUCCEEDED_VERIFIED
            verified = True
            reason = None
        elif observation.observation_complete and (
            observation.intended_result_observed is False
        ):
            self.metrics.increment(operation.operation, "verification_mismatches")
            outcome = NormalizedOperationOutcome.VERIFICATION_MISMATCH
            verified = False
            reason = None
        else:
            outcome = NormalizedOperationOutcome.OBSERVING
            verified = None
            reason = None
        return VerificationResult(
            outcome=outcome,
            attempt_count=observation.attempt_count,
            verified=verified,
            resulting_state_fingerprint=observation.readback_state_fingerprint,
            mismatch_fields=observation.mismatch_fields,
            evidence_hash=observation.evidence_hash,
            manual_review_reason_code=reason,
        )

    async def recover(
        self,
        operation: PreparedOperationalOperation,
        *,
        context: RecoveryContext,
    ) -> ObservationResult:
        _validate_prepared(operation)
        # The merged executor currently supplies its validated internal
        # recovery view structurally.  Normalize it immediately into the sole
        # shipped canonical contract object before using any field.
        try:
            canonical_context = RecoveryContext(
                dispatch_intent_recorded=context.dispatch_intent_recorded,
                provider_invocation_may_have_occurred=(
                    context.provider_invocation_may_have_occurred
                ),
                provider_response_received=context.provider_response_received,
                prior_observation_attempts=context.prior_observation_attempts,
                prior_verification_attempts=context.prior_verification_attempts,
                post_dispatch_deadline=context.post_dispatch_deadline,
            )
        except (AttributeError, TypeError):
            raise OperationalAdapterError("recovery_context_invalid")
        if (
            not canonical_context.dispatch_intent_recorded
            or not canonical_context.provider_invocation_may_have_occurred
        ):
            raise OperationalAdapterError("recovery_without_dispatch_intent")
        self.metrics.increment(operation.operation, "reconciliations")
        self.metrics.increment(
            operation.operation, "blind_redispatch_preventions"
        )
        try:
            evidence = await self._read_evidence(operation)
        except OperationalAdapterError:
            return self._manual_observation(
                operation,
                attempt_count=max(
                    1, canonical_context.prior_observation_attempts + 1
                ),
                reason="recovery_evidence_corrupt",
            )
        try:
            recovery_deadline_matches = (
                canonical_context.post_dispatch_deadline is not None
                and evidence.evidence_deadline is not None
                and _parse_aware(canonical_context.post_dispatch_deadline)
                == _parse_aware(evidence.evidence_deadline)
            )
        except (TypeError, ValueError):
            recovery_deadline_matches = False
        if (
            evidence.dispatch_count != 1
            or not evidence.dispatch_intent_recorded
            or not recovery_deadline_matches
        ):
            return self._manual_observation(
                operation,
                attempt_count=max(
                    1, canonical_context.prior_observation_attempts + 1
                ),
                reason="recovery_dispatch_lineage_invalid",
            )
        return await self._observe(
            operation,
            provider_response_received=(
                canonical_context.provider_response_received
                or evidence.provider_response_received
            ),
            recovering=True,
            minimum_attempt=max(
                1, canonical_context.prior_observation_attempts + 1
            ),
        )

    def _manual_observation(
        self,
        operation: PreparedOperationalOperation,
        *,
        attempt_count: int,
        reason: str,
    ) -> ObservationResult:
        self.metrics.increment(operation.operation, "manual_review_transitions")
        return ObservationResult(
            outcome=NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED,
            attempt_count=attempt_count,
            observation_complete=False,
            provider_reachable=None,
            target_reachable=None,
            readback_state_fingerprint=None,
            intended_result_observed=None,
            evidence_hash=stable_hash({"reason": reason}),
            diagnostic_codes=(reason,),
        )

    def _deadline_expired(self, value: str | None) -> bool:
        if value is None:
            return False
        try:
            return self.now().astimezone(timezone.utc) >= _parse_aware(value)
        except (TypeError, ValueError):
            return True

    async def prepare_rollback(
        self,
        operation: PreparedOperationalOperation,
        *,
        expected_current_fingerprint: str,
    ) -> None:
        _validate_prepared(operation)
        del expected_current_fingerprint
        return None


def validate_execution_binding(
    prepared: PreparedOperationalOperation, identity: Any
) -> None:
    """Bind the future child identity to one approved public task and plan."""

    _validate_prepared(prepared)
    if (
        getattr(identity, "task_id", None) != prepared.child_execution_id
        or getattr(identity, "plan_id", None) != prepared.plan_id
    ):
        raise OperationalAdapterError("execution_identity_mismatch")


async def execute_operational(
    executor: Any,
    *,
    adapter: OperationalAdministrationAdapter,
    prepared: PreparedOperationalOperation,
    identity: Any,
    approval_consumption: Callable[[], Awaitable[None]],
) -> Any:
    """Narrow binding helper; the merged executor remains sole authority."""

    _validate_prepared(prepared)
    validate_execution_binding(prepared, identity)
    validate_operational_executor_timing(executor, prepared)
    return await executor.execute(
        adapter=adapter,
        prepared=prepared,
        identity=identity,
        approval_consumption=approval_consumption,
    )
