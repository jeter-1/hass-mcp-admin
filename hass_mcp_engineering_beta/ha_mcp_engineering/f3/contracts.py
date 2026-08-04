"""Canonical shipped F3 operation-adapter contract.

This module is the sole runtime definition of ``f3-operation-adapter-v1``.
It intentionally contains declarations only: importing it registers no tools,
providers, adapters, tasks, plans, or recovery routes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, TypeAlias, TypeVar


F3_ADAPTER_CONTRACT_MODEL = "f3-operation-adapter-v1"
F3_MAX_MUTATING_PROVIDER_INVOCATIONS_PER_OPERATION = 1


class OperationAdapterPhase(str, Enum):
    """Canonical phases implemented by every F3 operation adapter."""

    PLANNING = "planning"
    PREFLIGHT = "preflight"
    DISPATCH = "dispatch"
    OBSERVATION = "observation"
    VERIFICATION = "verification"
    RECOVERY = "recovery"
    ROLLBACK = "rollback"


class NormalizedOperationOutcome(str, Enum):
    """Adapter-neutral outcomes mapped to, but not stored over, task schema 1."""

    PREFLIGHT_REJECTED = "preflight_rejected"
    LOCK_CONFLICT = "lock_conflict"
    PROVIDER_UNAVAILABLE_PRE_DISPATCH = (
        "provider_unavailable_pre_dispatch"
    )
    DISPATCH_FAILED_CONFIRMED = "dispatch_failed_confirmed"
    DISPATCH_INDETERMINATE = "dispatch_indeterminate"
    OBSERVING = "observing"
    VERIFICATION_MISMATCH = "verification_mismatch"
    SUCCEEDED_VERIFIED = "succeeded_verified"
    FAILED_PRE_DISPATCH = "failed_pre_dispatch"
    FAILED_POST_DISPATCH = "failed_post_dispatch"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    CANCELLED_PRE_DISPATCH = "cancelled_pre_dispatch"


class LockScope(str, Enum):
    """Whether a lock protects a resource or a provider dependency."""

    RESOURCE = "resource"
    PROVIDER = "provider"


class LockMode(str, Enum):
    """Compatibility mode used by the future shared lock manager."""

    SHARED = "shared"
    EXCLUSIVE = "exclusive"


@dataclass(frozen=True)
class OperationTarget:
    """Exact canonical identity of one governed operation target."""

    target_type: str
    target_id: str


@dataclass(frozen=True)
class LockRequest:
    """One canonical, deterministically ordered lock requirement."""

    key: str
    scopes: tuple[LockScope, ...]
    mode: LockMode
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class AdapterCapabilityDescriptor:
    """Provider-specific capabilities exposed to the shared executor."""

    adapter_id: str
    contract_model: str
    operation_family: str
    supported_operations: tuple[str, ...]
    rollback_supported: bool
    readback_recovery_supported: bool
    exact_provider_contract_required: bool


@dataclass(frozen=True)
class PreparedOperation:
    """Hash-bound planning output consumed by preflight and execution."""

    contract_model: str
    adapter_id: str
    operation: str
    target: OperationTarget
    current_state_fingerprint: str
    normalized_proposed_hash: str
    prepared_operation_hash: str
    risk_level: str
    policy_decision_hash: str
    approval_bundle_hash: str
    expected_effects: tuple[str, ...]
    verification_contract_model: str
    verification_contract_hash: str
    rollback_available: bool


@dataclass(frozen=True)
class PreflightResult:
    """Bounded result produced before approval consumption or dispatch."""

    eligible: bool
    outcome: NormalizedOperationOutcome | None
    confirmed_target: OperationTarget | None
    observed_state_fingerprint: str | None
    provider_contract: str | None
    provider_operation: str | None
    provider_arguments_hash: str | None
    evidence_hash: str | None
    diagnostic_codes: tuple[str, ...] = ()
    mismatch_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class DispatchResult:
    """Truthful evidence about the single mutating-invocation boundary."""

    outcome: NormalizedOperationOutcome
    dispatch_intent_recorded: bool
    mutating_invocation_count: int
    may_have_dispatched: bool
    provider_response_received: bool
    provider_operation_id: str | None = None
    response_evidence_hash: str | None = None
    diagnostic_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObservationResult:
    """Bounded readback evidence used for verification or recovery."""

    outcome: NormalizedOperationOutcome
    attempt_count: int
    observation_complete: bool
    provider_reachable: bool | None
    target_reachable: bool | None
    readback_state_fingerprint: str | None
    intended_result_observed: bool | None
    mismatch_fields: tuple[str, ...] = ()
    evidence_hash: str | None = None
    diagnostic_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerificationResult:
    """Terminal or continuing comparison against the verification contract."""

    outcome: NormalizedOperationOutcome
    attempt_count: int
    verified: bool | None
    resulting_state_fingerprint: str | None
    mismatch_fields: tuple[str, ...] = ()
    evidence_hash: str | None = None
    manual_review_reason_code: str | None = None


@dataclass(frozen=True)
class RecoveryContext:
    """Durable evidence available after interruption or process restart."""

    dispatch_intent_recorded: bool
    provider_invocation_may_have_occurred: bool
    provider_response_received: bool
    prior_observation_attempts: int
    prior_verification_attempts: int
    post_dispatch_deadline: str | None


DispatchIntentRecorder: TypeAlias = Callable[[], Awaitable[None]]
ProposalT = TypeVar("ProposalT", contravariant=True)
PreparedT = TypeVar("PreparedT", bound=PreparedOperation)


class OperationAdapter(Protocol[ProposalT, PreparedT]):
    """Stable interface adapters implement without widening authority."""

    @property
    def capabilities(self) -> AdapterCapabilityDescriptor:
        """Return the adapter's fixed, reviewed capability declaration."""

        ...

    async def prepare(self, proposal: ProposalT) -> PreparedT:
        """Read current state and prepare one immutable governed operation."""

        ...

    def lock_requests(
        self, operation: PreparedT
    ) -> tuple[LockRequest, ...]:
        """Return the complete canonical lock set before acquisition."""

        ...

    async def preflight(
        self,
        operation: PreparedT,
        *,
        acquired_locks: tuple[LockRequest, ...],
    ) -> PreflightResult:
        """Revalidate admission, identity, stale state, and dispatch eligibility."""

        ...

    async def dispatch(
        self,
        operation: PreparedT,
        preflight: PreflightResult,
        *,
        before_dispatch: DispatchIntentRecorder,
    ) -> DispatchResult:
        """Record intent, then make at most one mutating provider invocation."""

        ...

    async def observe(
        self,
        operation: PreparedT,
        dispatch: DispatchResult | None,
    ) -> ObservationResult:
        """Read target state without dispatching the operation again."""

        ...

    async def verify(
        self,
        operation: PreparedT,
        observation: ObservationResult,
    ) -> VerificationResult:
        """Compare readback with the exact planned verification contract."""

        ...

    async def recover(
        self,
        operation: PreparedT,
        *,
        context: RecoveryContext,
    ) -> ObservationResult:
        """Resume by observation only after process or provider interruption."""

        ...

    async def prepare_rollback(
        self,
        operation: PreparedT,
        *,
        expected_current_fingerprint: str,
    ) -> PreparedT | None:
        """Prepare a separately governed rollback or fail when unsupported."""

        ...


__all__ = [
    "F3_ADAPTER_CONTRACT_MODEL",
    "F3_MAX_MUTATING_PROVIDER_INVOCATIONS_PER_OPERATION",
    "OperationAdapterPhase",
    "NormalizedOperationOutcome",
    "LockScope",
    "LockMode",
    "OperationTarget",
    "LockRequest",
    "AdapterCapabilityDescriptor",
    "PreparedOperation",
    "PreflightResult",
    "DispatchResult",
    "ObservationResult",
    "VerificationResult",
    "RecoveryContext",
    "DispatchIntentRecorder",
    "OperationAdapter",
]
