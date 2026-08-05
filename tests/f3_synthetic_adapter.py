"""Deterministic test-only adapters for the isolated F3 executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ha_mcp_engineering.f3.contracts import (
    F3_ADAPTER_CONTRACT_MODEL,
    AdapterCapabilityDescriptor,
    DispatchResult,
    LockMode,
    LockRequest,
    LockScope,
    NormalizedOperationOutcome,
    ObservationResult,
    OperationTarget,
    PreflightResult,
    PreparedOperation,
    VerificationResult,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64
HASH_F = "f" * 64


class SyntheticProviderError(RuntimeError):
    pass


class SyntheticResponseLost(SyntheticProviderError):
    pass


class SyntheticProcessLoss(BaseException):
    pass


@dataclass
class SyntheticApprovalRecorder:
    """Idempotent caller-owned approval recorder with no external effects."""

    failures_remaining: int = 0
    invocations: int = 0
    consumptions: int = 0
    consumed: bool = False

    async def __call__(self) -> None:
        self.invocations += 1
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise SyntheticProviderError("synthetic approval persistence failure")
        if not self.consumed:
            self.consumed = True
            self.consumptions += 1


@dataclass
class SyntheticCounters:
    prepare_invocations: int = 0
    preflight_invocations: int = 0
    dispatch_invocations: int = 0
    observation_invocations: int = 0
    verification_invocations: int = 0
    simulated_mutations: int = 0
    recovery_invocations: int = 0
    rollback_prepare_invocations: int = 0


@dataclass(frozen=True)
class SyntheticBehavior:
    preflight: str = "success"
    dispatch: str = "success"
    observation: str = "success"
    verification: str = "success"
    recovery_supported: bool = True
    observations_before_complete: int = 0


def prepared_dashboard_operation(
    *,
    url_path: str = "overview",
) -> PreparedOperation:
    return PreparedOperation(
        contract_model=F3_ADAPTER_CONTRACT_MODEL,
        adapter_id="synthetic_dashboard",
        operation="update_dashboard",
        target=OperationTarget("dashboard", url_path),
        current_state_fingerprint=HASH_A,
        normalized_proposed_hash=HASH_B,
        prepared_operation_hash=HASH_C,
        risk_level="high",
        policy_decision_hash=HASH_D,
        approval_bundle_hash=HASH_E,
        expected_effects=("dashboard_configuration_changed",),
        verification_contract_model="synthetic_exact_readback_v1",
        verification_contract_hash=HASH_F,
        rollback_available=False,
    )


class SyntheticOperationAdapter:
    """Closed synthetic operation; no network, filesystem, or real provider."""

    def __init__(self, behavior: SyntheticBehavior = SyntheticBehavior()):
        self.behavior = behavior
        self.counters = SyntheticCounters()
        self.state = "before"
        self.capabilities = AdapterCapabilityDescriptor(
            adapter_id="synthetic_dashboard",
            contract_model=F3_ADAPTER_CONTRACT_MODEL,
            operation_family="synthetic_dashboard_configuration",
            supported_operations=("update_dashboard",),
            rollback_supported=False,
            readback_recovery_supported=behavior.recovery_supported,
            exact_provider_contract_required=True,
        )

    async def prepare(self, proposal: Any) -> PreparedOperation:
        self.counters.prepare_invocations += 1
        if isinstance(proposal, PreparedOperation):
            return proposal
        return prepared_dashboard_operation(url_path=str(proposal))

    def lock_requests(
        self, operation: PreparedOperation
    ) -> tuple[LockRequest, ...]:
        return (
            LockRequest(
                key=f"dashboard:{operation.target.target_id}",
                scopes=(LockScope.RESOURCE,),
                mode=LockMode.EXCLUSIVE,
                reason_codes=("dashboard_target_mutation",),
            ),
            LockRequest(
                key="home_assistant:core",
                scopes=(LockScope.RESOURCE,),
                mode=LockMode.SHARED,
                reason_codes=("home_assistant_availability_dependency",),
            ),
            LockRequest(
                key="addon:ha_mcp",
                scopes=(LockScope.PROVIDER,),
                mode=LockMode.SHARED,
                reason_codes=("upstream_provider_dependency",),
            ),
        )

    async def preflight(
        self,
        operation: PreparedOperation,
        *,
        acquired_locks: tuple[LockRequest, ...],
    ) -> PreflightResult:
        self.counters.preflight_invocations += 1
        if not acquired_locks:
            raise SyntheticProviderError("synthetic locks missing")
        if self.behavior.preflight == "raise":
            raise SyntheticProviderError("synthetic provider unavailable")
        if self.behavior.preflight == "stale":
            return PreflightResult(
                eligible=False,
                outcome=NormalizedOperationOutcome.PREFLIGHT_REJECTED,
                confirmed_target=operation.target,
                observed_state_fingerprint=HASH_B,
                provider_contract=None,
                provider_operation=None,
                provider_arguments_hash=None,
                evidence_hash=HASH_A,
                diagnostic_codes=("stale_target_state",),
                mismatch_fields=("state_fingerprint",),
            )
        if self.behavior.preflight == "unavailable":
            return PreflightResult(
                eligible=False,
                outcome=(
                    NormalizedOperationOutcome.PROVIDER_UNAVAILABLE_PRE_DISPATCH
                ),
                confirmed_target=operation.target,
                observed_state_fingerprint=HASH_A,
                provider_contract=None,
                provider_operation=None,
                provider_arguments_hash=None,
                evidence_hash=HASH_A,
                diagnostic_codes=("provider_unavailable",),
            )
        return PreflightResult(
            eligible=True,
            outcome=None,
            confirmed_target=operation.target,
            observed_state_fingerprint=operation.current_state_fingerprint,
            provider_contract="synthetic_provider_v1",
            provider_operation="synthetic_dashboard_update",
            provider_arguments_hash=HASH_B,
            evidence_hash=HASH_C,
        )

    async def dispatch(
        self,
        operation: PreparedOperation,
        preflight: PreflightResult,
        *,
        before_dispatch,
    ) -> DispatchResult:
        if self.behavior.dispatch == "raise_before_intent":
            raise SyntheticProviderError("synthetic failure before intent")
        await before_dispatch()
        self.counters.dispatch_invocations += 1
        if self.counters.dispatch_invocations > 1:
            raise AssertionError("synthetic adapter dispatched more than once")
        if self.behavior.dispatch == "confirmed_failure":
            return DispatchResult(
                outcome=NormalizedOperationOutcome.DISPATCH_FAILED_CONFIRMED,
                dispatch_intent_recorded=True,
                mutating_invocation_count=1,
                may_have_dispatched=True,
                provider_response_received=True,
                diagnostic_codes=("provider_rejected_without_effect",),
            )
        if self.behavior.dispatch == "raise_before_effect":
            raise SyntheticProviderError("synthetic failure before effect")
        self.state = "after"
        self.counters.simulated_mutations += 1
        if self.counters.simulated_mutations > 1:
            raise AssertionError("synthetic adapter mutated more than once")
        if self.behavior.dispatch in {
            "response_lost_after_effect",
            "raise_after_effect",
        }:
            raise SyntheticResponseLost("synthetic response was lost")
        if self.behavior.dispatch == "malformed_evidence":
            return DispatchResult(
                outcome=NormalizedOperationOutcome.OBSERVING,
                dispatch_intent_recorded=False,
                mutating_invocation_count=2,
                may_have_dispatched=False,
                provider_response_received=True,
                diagnostic_codes=("malformed_provider_evidence",),
            )
        return DispatchResult(
            outcome=NormalizedOperationOutcome.OBSERVING,
            dispatch_intent_recorded=True,
            mutating_invocation_count=1,
            may_have_dispatched=True,
            provider_response_received=True,
            provider_operation_id="synthetic-operation-1",
            response_evidence_hash=HASH_D,
        )

    def _observation(self) -> ObservationResult:
        count = self.counters.observation_invocations
        if self.behavior.observation == "raise":
            raise SyntheticProviderError("synthetic observation failed")
        if self.behavior.observation == "process_loss":
            raise SyntheticProcessLoss()
        if self.behavior.observation == "malformed":
            return ObservationResult(
                outcome=NormalizedOperationOutcome.OBSERVING,
                attempt_count=count,
                observation_complete=True,
                provider_reachable=True,
                target_reachable=True,
                readback_state_fingerprint=HASH_B,
                intended_result_observed=True,
                evidence_hash="not-a-digest",
            )
        if (
            self.behavior.observation == "eventual"
            and count <= self.behavior.observations_before_complete
        ):
            return ObservationResult(
                outcome=NormalizedOperationOutcome.OBSERVING,
                attempt_count=count,
                observation_complete=False,
                provider_reachable=True,
                target_reachable=True,
                readback_state_fingerprint=None,
                intended_result_observed=None,
                diagnostic_codes=("readback_pending",),
            )
        if self.behavior.observation == "mismatch" or self.state != "after":
            return ObservationResult(
                outcome=NormalizedOperationOutcome.VERIFICATION_MISMATCH,
                attempt_count=count,
                observation_complete=True,
                provider_reachable=True,
                target_reachable=True,
                readback_state_fingerprint=HASH_A,
                intended_result_observed=False,
                mismatch_fields=("synthetic_state",),
                evidence_hash=HASH_E,
            )
        return ObservationResult(
            outcome=NormalizedOperationOutcome.OBSERVING,
            attempt_count=count,
            observation_complete=True,
            provider_reachable=True,
            target_reachable=True,
            readback_state_fingerprint=HASH_B,
            intended_result_observed=True,
            evidence_hash=HASH_E,
        )

    async def observe(
        self,
        operation: PreparedOperation,
        dispatch: DispatchResult | None,
    ) -> ObservationResult:
        self.counters.observation_invocations += 1
        return self._observation()

    async def verify(
        self,
        operation: PreparedOperation,
        observation: ObservationResult,
    ) -> VerificationResult:
        self.counters.verification_invocations += 1
        count = self.counters.verification_invocations
        if self.behavior.verification == "raise":
            raise SyntheticProviderError("synthetic verification failed")
        if self.behavior.verification == "process_loss":
            raise SyntheticProcessLoss()
        if self.behavior.verification == "observing":
            return VerificationResult(
                outcome=NormalizedOperationOutcome.OBSERVING,
                attempt_count=count,
                verified=None,
                resulting_state_fingerprint=None,
                evidence_hash=HASH_F,
            )
        if self.behavior.verification == "manual":
            return VerificationResult(
                outcome=NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED,
                attempt_count=count,
                verified=None,
                resulting_state_fingerprint=None,
                evidence_hash=HASH_F,
                manual_review_reason_code="synthetic_manual_review",
            )
        if (
            self.behavior.verification == "mismatch"
            or observation.intended_result_observed is False
        ):
            return VerificationResult(
                outcome=NormalizedOperationOutcome.VERIFICATION_MISMATCH,
                attempt_count=count,
                verified=False,
                resulting_state_fingerprint=HASH_A,
                mismatch_fields=("synthetic_state",),
                evidence_hash=HASH_F,
            )
        return VerificationResult(
            outcome=NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
            attempt_count=count,
            verified=True,
            resulting_state_fingerprint=HASH_B,
            evidence_hash=HASH_F,
        )

    async def recover(
        self,
        operation: PreparedOperation,
        *,
        context,
    ) -> ObservationResult:
        self.counters.recovery_invocations += 1
        if not self.behavior.recovery_supported:
            return ObservationResult(
                outcome=NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED,
                attempt_count=self.counters.recovery_invocations,
                observation_complete=False,
                provider_reachable=None,
                target_reachable=None,
                readback_state_fingerprint=None,
                intended_result_observed=None,
                diagnostic_codes=("recovery_unsupported",),
            )
        self.counters.observation_invocations += 1
        return self._observation()

    async def prepare_rollback(
        self,
        operation: PreparedOperation,
        *,
        expected_current_fingerprint: str,
    ) -> PreparedOperation | None:
        self.counters.rollback_prepare_invocations += 1
        return None
