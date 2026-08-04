"""Runtime-inert F3 adapter for governed operational administration."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from ..governance.models import ApprovalState, ChangePlan, PlanStatus
from .operational_locks import (
    OperationalLockSetCalculator,
    exact_manual_review_hold,
)
from .operational_models import (
    CAPABILITY_IDENTITIES,
    CONTROLLED_RELOAD,
    CREATE_FULL_BACKUP,
    EXPECTED_EFFECT_CODES,
    F3_ADAPTER_CONTRACT_MODEL,
    OPERATIONAL_ADAPTER_ID,
    OPERATIONAL_PLAN_CONTRACT_VERSION,
    OPERATIONAL_PROVIDER_CONTRACT_MODEL,
    POLICY_EXPECTATIONS,
    PROVIDER_OPERATIONS,
    RESTART_ADDON,
    RESTART_HOME_ASSISTANT,
    SUPPORTED_OPERATIONS,
    TARGET_CLASSES,
    TARGET_TYPES,
    VERIFICATION_MODELS,
    AdapterCapabilityDescriptor,
    DispatchResult,
    ObservationResult,
    OperationTarget,
    OperationalAuthoritySnapshot,
    OperationalPreparationRequest,
    PreflightResult,
    PreparedOperationalOperation,
    VerificationResult,
    canonical_json,
    provider_arguments,
    stable_hash,
)
from .operational_observability import (
    OperationalEventRecorder,
    OperationalMetrics,
)
from .operational_strategies import (
    OperationalRecoveryLedger,
    OperationalStrategy,
    default_strategies,
)


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


def _lock_signature(values: tuple[Any, ...]) -> tuple[tuple[Any, ...], ...]:
    result = []
    for value in values:
        result.append(
            (
                str(getattr(value, "key")),
                tuple(str(item) for item in getattr(value, "scopes")),
                _enum_value(getattr(value, "mode")),
                tuple(str(item) for item in getattr(value, "reason_codes")),
            )
        )
    return tuple(result)


class OperationalAdministrationAdapter:
    """One shared adapter with four exact operation-specific strategies."""

    capabilities = AdapterCapabilityDescriptor()

    def __init__(
        self,
        *,
        backup_gateway: Any,
        lifecycle_gateway: Any,
        recovery_ledger: OperationalRecoveryLedger,
        authority_reader: AuthorityReader,
        metrics: OperationalMetrics | None = None,
        events: OperationalEventRecorder | None = None,
        lock_calculator: OperationalLockSetCalculator | None = None,
        strategies: dict[str, OperationalStrategy] | None = None,
        now: Now = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.metrics = metrics or OperationalMetrics()
        self.events = events or OperationalEventRecorder()
        self.recovery_ledger = recovery_ledger
        self.authority_reader = authority_reader
        self.lock_calculator = lock_calculator or OperationalLockSetCalculator()
        self.now = now
        self.strategies = strategies or default_strategies(
            backup_gateway=backup_gateway,
            lifecycle_gateway=lifecycle_gateway,
            ledger=recovery_ledger,
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
        if plan.status not in {PlanStatus.APPROVED, PlanStatus.APPLYING}:
            raise OperationalAdapterError("plan_not_approved")
        if plan.approval.state not in {ApprovalState.APPROVED, ApprovalState.CONSUMED}:
            raise OperationalAdapterError("approval_not_available")
        if (
            plan.approval.bound_plan_hash != proposal.expected_plan_hash
            or plan.approval.policy_decision_hash
            != (
                plan.policy_decision.policy_decision_hash
                if plan.policy_decision is not None
                else None
            )
        ):
            raise OperationalAdapterError("approval_hash_mismatch")
        if plan.policy_decision is None:
            raise OperationalAdapterError("policy_snapshot_missing")
        expected_target_type = TARGET_TYPES[operation]
        if (
            plan.target_type != expected_target_type
            or (operation == CREATE_FULL_BACKUP and plan.target_id != "local_full_backup")
            or (operation == RESTART_HOME_ASSISTANT and plan.target_id != "core")
        ):
            raise OperationalAdapterError("target_identity_mismatch")
        policy = plan.policy_decision
        policy_values = (
            _enum_value(policy.policy_class),
            _enum_value(policy.risk_delta),
            _enum_value(policy.physical_consequence),
        )
        if policy_values != POLICY_EXPECTATIONS[operation]:
            raise OperationalAdapterError("policy_snapshot_mismatch")
        if operational.provider != strategy.capability.provider:
            raise OperationalAdapterError("provider_identity_mismatch")
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
        hold_keys, hold_seconds = exact_manual_review_hold(
            operation, plan.target_id
        )
        prepared_payload = {
            "contract_model": F3_ADAPTER_CONTRACT_MODEL,
            "adapter_id": OPERATIONAL_ADAPTER_ID,
            "capability_id": strategy.capability.capability_id,
            "operation": operation,
            "target": {
                "target_type": plan.target_type,
                "target_id": plan.target_id,
            },
            "plan_id": plan.plan_id,
            "plan_hash": proposal.expected_plan_hash,
            "task_id": proposal.task_id,
            "current_state_fingerprint": plan.current_state_fingerprint,
            "normalized_proposed_hash": plan.proposed_config_hash,
            "policy_decision_hash": policy.policy_decision_hash,
            "approval_bundle_hash": _approval_bundle_hash(plan),
            "provider": operational.provider,
            "provider_evidence": operational.provider_capability_evidence,
            "provider_operation": PROVIDER_OPERATIONS[operation],
            "provider_arguments": arguments,
            "provider_slug": proposal.authoritative_provider_slug,
            "provider_identity_evidence_hash": proposal.provider_identity_evidence_hash,
            "verification_contract": operational.verification_contract,
            "rollback_available": operational.rollback_available,
            "manual_review_hold_keys": hold_keys,
        }
        prepared = PreparedOperationalOperation(
            contract_model=F3_ADAPTER_CONTRACT_MODEL,
            adapter_id=OPERATIONAL_ADAPTER_ID,
            operation=operation,
            target=OperationTarget(plan.target_type, plan.target_id),
            current_state_fingerprint=plan.current_state_fingerprint,
            normalized_proposed_hash=plan.proposed_config_hash,
            prepared_operation_hash=stable_hash(prepared_payload),
            risk_level=_enum_value(plan.risk.level),
            policy_decision_hash=policy.policy_decision_hash,
            approval_bundle_hash=_approval_bundle_hash(plan),
            expected_effects=EXPECTED_EFFECT_CODES[operation],
            verification_contract_model=VERIFICATION_MODELS[operation],
            verification_contract_hash=stable_hash(verification_json),
            rollback_available=False,
            capability_id=strategy.capability.capability_id,
            target_class=TARGET_CLASSES[operation],
            plan_id=plan.plan_id,
            plan_hash=proposal.expected_plan_hash,
            task_id=proposal.task_id,
            plan_expires_at=plan.expires_at,
            requested_name=operational.requested_name,
            provider_id=operational.provider,
            provider_contract_model=OPERATIONAL_PROVIDER_CONTRACT_MODEL,
            provider_operation=PROVIDER_OPERATIONS[operation],
            provider_arguments_json=arguments_json,
            provider_arguments_hash=stable_hash(arguments_json),
            provider_evidence_json=provider_json,
            baseline_json=baseline_json,
            authoritative_provider_slug=proposal.authoritative_provider_slug,
            provider_identity_evidence_hash=proposal.provider_identity_evidence_hash,
            policy_class=policy_values[0],
            risk_delta=policy_values[1],
            physical_consequence=policy_values[2],
            expected_effect_descriptions=tuple(operational.expected_effects),
            warnings=tuple(plan.warnings),
            limitations=tuple(operational.limitations),
            verification_contract_json=verification_json,
            evidence_deadline_class=strategy.capability.evidence_deadline_class,
            manual_review_hold_keys=hold_keys,
            manual_review_hold_max_seconds=hold_seconds,
        )
        prepared.validate()
        self.metrics.increment(operation, "preparations")
        self.events.emit(
            {
                "event_type": "operation_prepared",
                "operation": operation,
                "capability_id": prepared.capability_id,
                "task_id": prepared.task_id,
                "plan_id": prepared.plan_id,
                "target_type": prepared.target.target_type,
            }
        )
        return prepared

    def lock_requests(
        self, operation: PreparedOperationalOperation
    ) -> tuple[Any, ...]:
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

    @staticmethod
    def _authority_mismatches(
        prepared: PreparedOperationalOperation,
        authority: OperationalAuthoritySnapshot,
    ) -> tuple[str, ...]:
        mismatches = []
        pairs = {
            "plan_identity": (authority.plan_id, prepared.plan_id),
            "plan_hash": (authority.plan_hash, prepared.plan_hash),
            "task_identity": (authority.task_id, prepared.task_id),
            "active_task_identity": (authority.active_task_id, prepared.task_id),
            "operation_identity": (authority.operation, prepared.operation),
            "target_type": (authority.target_type, prepared.target.target_type),
            "target_identity": (authority.target_id, prepared.target.target_id),
            "policy_snapshot": (
                authority.policy_decision_hash,
                prepared.policy_decision_hash,
            ),
        }
        mismatches.extend(name for name, values in pairs.items() if values[0] != values[1])
        if not authority.approval_consumed:
            mismatches.append("approval_consumption")
        if (
            prepared.policy_class == "elevated_admin"
            and not authority.elevated_acknowledgement_consumed
        ):
            mismatches.append("elevated_acknowledgement")
        if authority.conflicting_execution_active:
            mismatches.append("conflicting_execution")
        if authority.execution_task_storage_status != "healthy":
            mismatches.append("execution_task_storage")
        if prepared.operation == RESTART_HOME_ASSISTANT:
            if authority.governance_storage_status != "healthy":
                mismatches.append("governance_storage")
            if authority.audit_storage_status != "healthy":
                mismatches.append("audit_storage")
        return tuple(sorted(set(mismatches)))

    async def preflight(
        self,
        operation: PreparedOperationalOperation,
        *,
        acquired_locks: tuple[Any, ...],
    ) -> PreflightResult:
        operation.validate()
        strategy = self._strategy(operation.operation)
        self.metrics.increment(operation.operation, "preflight_attempts")
        expected_locks = self.lock_requests(operation)
        if _lock_signature(acquired_locks) != _lock_signature(expected_locks):
            self.metrics.increment(operation.operation, "preflight_rejections")
            return PreflightResult(
                eligible=False,
                outcome="preflight_rejected",
                confirmed_target=operation.target,
                observed_state_fingerprint=None,
                provider_contract=None,
                provider_operation=None,
                provider_arguments_hash=None,
                evidence_hash=stable_hash({"reason": "lock_set_mismatch"}),
                diagnostic_codes=("lock_set_mismatch",),
                mismatch_fields=("lock_set",),
            )
        try:
            authority = await self._read_authority(operation)
        except OperationalAdapterError as exc:
            self.metrics.increment(operation.operation, "preflight_rejections")
            return PreflightResult(
                eligible=False,
                outcome="failed_pre_dispatch",
                confirmed_target=operation.target,
                observed_state_fingerprint=None,
                provider_contract=None,
                provider_operation=None,
                provider_arguments_hash=None,
                evidence_hash=stable_hash({"category": exc.category}),
                diagnostic_codes=("authority_unavailable",),
            )
        mismatches = list(self._authority_mismatches(operation, authority))
        try:
            if _parse_aware(operation.plan_expires_at) <= self.now().astimezone(timezone.utc):
                mismatches.append("plan_expiration")
        except (TypeError, ValueError):
            mismatches.append("plan_expiration")
        if mismatches:
            self.metrics.increment(operation.operation, "preflight_rejections")
            if "conflicting_execution" in mismatches:
                self.metrics.increment(operation.operation, "lock_conflicts")
            return PreflightResult(
                eligible=False,
                outcome="preflight_rejected",
                confirmed_target=operation.target,
                observed_state_fingerprint=None,
                provider_contract=None,
                provider_operation=None,
                provider_arguments_hash=None,
                evidence_hash=stable_hash({"mismatch_fields": sorted(mismatches)}),
                diagnostic_codes=("authority_preflight_rejected",),
                mismatch_fields=tuple(sorted(set(mismatches))),
            )
        eligible, category, mismatch_fields, fresh = await strategy.preflight_evidence(
            operation
        )
        if not eligible:
            self.metrics.increment(operation.operation, "preflight_rejections")
            if category in {
                "provider_unavailable",
                "provider_timeout",
                "exact_contract_mismatch",
                "unsupported_protocol_version",
            }:
                self.metrics.increment(operation.operation, "provider_admission_failures")
            if category == "stale_baseline":
                self.metrics.increment(operation.operation, "stale_state_failures")
            outcome = (
                "provider_unavailable_pre_dispatch"
                if category in {"provider_unavailable", "provider_timeout"}
                else "preflight_rejected"
            )
            return PreflightResult(
                eligible=False,
                outcome=outcome,
                confirmed_target=operation.target,
                observed_state_fingerprint=None,
                provider_contract=None,
                provider_operation=None,
                provider_arguments_hash=None,
                evidence_hash=stable_hash(
                    {"category": category, "mismatch_fields": list(mismatch_fields)}
                ),
                diagnostic_codes=(category,),
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
                        "task_id": authority.task_id,
                        "policy_decision_hash": authority.policy_decision_hash,
                    },
                    "provider": fresh.get("provider"),
                    "baseline": fresh_baseline,
                }
            ),
        )

    async def dispatch(
        self,
        operation: PreparedOperationalOperation,
        preflight: PreflightResult,
        *,
        before_dispatch: Callable[[], Awaitable[None]],
    ) -> DispatchResult:
        if (
            preflight.eligible is not True
            or preflight.provider_operation != operation.provider_operation
            or preflight.provider_arguments_hash != operation.provider_arguments_hash
        ):
            raise OperationalAdapterError("invalid_dispatch_preflight")
        strategy = self._strategy(operation.operation)
        callback_count = 0

        async def guarded_before_dispatch() -> None:
            nonlocal callback_count
            if callback_count:
                raise OperationalAdapterError("duplicate_dispatch_intent")
            await before_dispatch()
            callback_count = 1
            committed_at = self.now().astimezone(timezone.utc)
            ledger_values: dict[str, Any] = {
                "apply_started_at": committed_at.isoformat(),
                "provider_response_received": False,
                "dispatch_count": 1,
            }
            if operation.operation == RESTART_HOME_ASSISTANT:
                ledger_values["outage_observation_deadline"] = (
                    committed_at + timedelta(seconds=180)
                ).isoformat()
            self.recovery_ledger.merge(operation.task_id, ledger_values)
            self.metrics.increment(operation.operation, "intents_committed")
            self.metrics.increment(operation.operation, "dispatch_attempts")

        result = await strategy.dispatch(
            operation, before_dispatch=guarded_before_dispatch
        )
        if callback_count != 1:
            raise OperationalAdapterError("dispatch_without_durable_intent")
        self.recovery_ledger.merge(
            operation.task_id,
            {
                "provider_response_received": result.response_received,
                **(
                    {"provider_operation_id": result.provider_operation_id}
                    if result.provider_operation_id is not None
                    else {}
                ),
                **(
                    {"provider_backup_id": result.provider_backup_id}
                    if result.provider_backup_id is not None
                    else {}
                ),
            },
        )
        if result.response_received:
            self.metrics.increment(operation.operation, "responses_received")
        else:
            self.metrics.increment(operation.operation, "responses_lost")
        if result.confirmed_failure:
            self.metrics.increment(operation.operation, "confirmed_dispatch_failures")
            outcome = "dispatch_failed_confirmed"
        elif not result.response_received:
            self.metrics.increment(operation.operation, "indeterminate_dispatches")
            outcome = "dispatch_indeterminate"
        else:
            outcome = "observing"
        return DispatchResult(
            outcome=outcome,
            dispatch_intent_recorded=True,
            mutating_invocation_count=1,
            may_have_dispatched=True,
            provider_response_received=result.response_received,
            provider_operation_id=result.provider_operation_id,
            response_evidence_hash=result.response_evidence_hash,
            diagnostic_codes=(result.diagnostic_code,),
            provider_backup_id=result.provider_backup_id,
        )

    def _next_ledger_count(self, task_id: str, field: str) -> int:
        durable = self.recovery_ledger.load(task_id)
        prior = durable.get(field, 0)
        if type(prior) is not int or prior < 0:
            raise OperationalAdapterError("recovery_evidence_corrupt")
        current = prior + 1
        self.recovery_ledger.merge(task_id, {field: current})
        return current

    async def _observe(
        self,
        operation: PreparedOperationalOperation,
        *,
        provider_response_received: bool,
        recovering: bool,
    ) -> ObservationResult:
        strategy = self._strategy(operation.operation)
        attempt = self._next_ledger_count(operation.task_id, "observation_count")
        observation = await strategy.observe(
            operation,
            provider_response_received=provider_response_received,
            recovering=recovering,
        )
        self.metrics.increment(operation.operation, "observations")
        status = observation.status
        if status == "verified":
            outcome = "observing"
            complete = True
            intended = True
        elif status == "failed":
            outcome = "verification_mismatch"
            complete = True
            intended = False
        elif status == "manual_review":
            outcome = "manual_review_required"
            complete = False
            intended = None
            self.metrics.increment(operation.operation, "manual_review_transitions")
        else:
            outcome = "observing"
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
            diagnostic_codes=observation.diagnostic_codes,
            verification_status=status,
        )

    async def observe(
        self,
        operation: PreparedOperationalOperation,
        dispatch: DispatchResult | None,
    ) -> ObservationResult:
        durable = self.recovery_ledger.load(operation.task_id)
        received = (
            dispatch.provider_response_received
            if dispatch is not None
            else durable.get("provider_response_received") is True
        )
        return await self._observe(
            operation,
            provider_response_received=received,
            recovering=False,
        )

    async def verify(
        self,
        operation: PreparedOperationalOperation,
        observation: ObservationResult,
    ) -> VerificationResult:
        attempt = self._next_ledger_count(operation.task_id, "verification_count")
        if observation.verification_status == "verified":
            self.metrics.increment(operation.operation, "verification_successes")
            outcome = "succeeded_verified"
            verified: bool | None = True
            reason = None
        elif observation.verification_status == "failed":
            self.metrics.increment(operation.operation, "verification_mismatches")
            outcome = "verification_mismatch"
            verified = False
            reason = None
        elif observation.verification_status == "manual_review":
            self.metrics.increment(operation.operation, "manual_review_transitions")
            outcome = "manual_review_required"
            verified = None
            reason = "operational_evidence_unavailable"
        else:
            outcome = "observing"
            verified = None
            reason = None
        return VerificationResult(
            outcome=outcome,
            attempt_count=attempt,
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
        context: Any,
    ) -> ObservationResult:
        if (
            getattr(context, "dispatch_intent_recorded", None) is not True
            or getattr(context, "provider_invocation_may_have_occurred", None)
            is not True
        ):
            raise OperationalAdapterError("recovery_without_dispatch_intent")
        durable = self.recovery_ledger.load(operation.task_id)
        if durable.get("dispatch_count") != 1:
            raise OperationalAdapterError("recovery_dispatch_lineage_invalid")
        self.metrics.increment(operation.operation, "reconciliations")
        self.metrics.increment(operation.operation, "blind_redispatch_preventions")
        return await self._observe(
            operation,
            provider_response_received=(
                getattr(context, "provider_response_received", False) is True
                or durable.get("provider_response_received") is True
            ),
            recovering=True,
        )

    async def prepare_rollback(
        self,
        operation: PreparedOperationalOperation,
        *,
        expected_current_fingerprint: str,
    ) -> None:
        operation.validate()
        return None


def validate_execution_binding(
    prepared: PreparedOperationalOperation, identity: Any
) -> None:
    """Bind F3-A execution identity to the approved prepared operation."""

    prepared.validate()
    if (
        getattr(identity, "task_id", None) != prepared.task_id
        or getattr(identity, "plan_id", None) != prepared.plan_id
    ):
        raise OperationalAdapterError("execution_identity_mismatch")


async def execute_operational(
    executor: Any,
    *,
    adapter: OperationalAdministrationAdapter,
    prepared: PreparedOperationalOperation,
    identity: Any,
) -> Any:
    """Narrow binding helper; F3-A remains the only executor and intent owner."""

    validate_execution_binding(prepared, identity)
    return await executor.execute(
        adapter=adapter,
        prepared=prepared,
        identity=identity,
    )
