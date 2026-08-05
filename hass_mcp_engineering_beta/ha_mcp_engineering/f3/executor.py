"""Shared runtime-inert executor for the frozen F3 adapter lifecycle."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import time
from typing import Any, Callable

from .contracts import ApprovalConsumptionRecorder
from .locks import (
    DurableLockError,
    DurableLockStore,
    LockConflict,
    LockLeaseExpired,
    LockOwnershipError,
    LockWaitCancelled,
    LockWaitTimeout,
    StaleRecoveryAction,
    StaleRecoveryDecision,
    normalize_lock_requests,
)
from .models import (
    F3_ADAPTER_CONTRACT_MODEL,
    NORMALIZED_OUTCOME_TO_TASK_STATE,
    TERMINAL_OUTCOMES,
    ExecutionIdentity,
    ExecutionRecord,
    ExecutorResult,
    ExecutorTiming,
    LockHandle,
    LockOwner,
    LockTiming,
    LockToken,
    bounded_codes,
    bounded_diagnostics,
    enum_value,
    parse_timestamp,
    timestamp,
    utc_now,
    validate_identifier,
    validate_sha256,
)
from .observability import EventSink, ExecutorMetrics, null_event_sink
from .persistence import (
    BlindRedispatchProhibited,
    DuplicateExecutionActive,
    DurableExecutionRepository,
    ExecutionClaim,
    ExecutionStorageError,
)


class OperationExecutorError(RuntimeError):
    pass


class PreparedOperationInvalid(OperationExecutorError):
    pass


class AdapterContractViolation(OperationExecutorError):
    pass


class PreIntentRetryRequired(OperationExecutorError):
    """The same F3 execution must retry without provider invocation."""

    def __init__(self, diagnostic_code: str):
        super().__init__("F3 pre-intent persistence requires idempotent retry")
        self.diagnostic_code = diagnostic_code


class SimulatedProcessLoss(BaseException):
    """Test-only fault signal deliberately not caught by executor recovery."""


FaultHook = Callable[[str], None]


@dataclass(frozen=True)
class InternalRecoveryContext:
    dispatch_intent_recorded: bool
    provider_invocation_may_have_occurred: bool
    provider_response_received: bool
    prior_observation_attempts: int
    prior_verification_attempts: int
    post_dispatch_deadline: str | None


class _LeaseRenewer:
    """Renew held lock generations while adapter work is in flight."""

    def __init__(
        self,
        *,
        store: DurableLockStore,
        handle: LockHandle,
        now: Callable[[], datetime],
    ):
        self.store = store
        self.handle = handle
        self.now = now
        self.error: DurableLockError | None = None
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("lease renewer already started")
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.handle.timing.renewal_interval_seconds,
                )
            except TimeoutError:
                try:
                    self.handle = self.store.renew(
                        self.handle, now=self.now()
                    )
                except DurableLockError as exc:
                    self.error = exc
                    return

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task


class SharedOperationExecutor:
    """Execute one exact prepared operation without generic forwarding.

    The caller retains authorization authority and supplies an idempotent
    durable approval-consumption callback.  This core sequences that callback
    only after adapter-specific preflight and held durable locks, and before a
    committed durable intent permits the adapter's one reviewed mutating
    provider operation.
    """

    def __init__(
        self,
        *,
        lock_store: DurableLockStore,
        execution_repository: DurableExecutionRepository,
        lock_timing: LockTiming,
        executor_timing: ExecutorTiming,
        metrics: ExecutorMetrics | None = None,
        event_sink: EventSink | None = None,
        now: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Any] = asyncio.sleep,
        fault_hook: FaultHook | None = None,
    ):
        lock_timing.validate()
        executor_timing.validate()
        self.lock_store = lock_store
        self.execution_repository = execution_repository
        self.lock_timing = lock_timing
        self.executor_timing = executor_timing
        self.metrics = metrics or execution_repository.metrics
        self.event_sink = event_sink or null_event_sink
        self.now = now
        self.monotonic = monotonic
        self.sleep = sleep
        self._fault_hook = fault_hook
        self._task_locks: dict[str, asyncio.Lock] = {}

    def _inject(self, stage: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(stage)

    @staticmethod
    def _capabilities(adapter: object) -> object:
        try:
            return getattr(adapter, "capabilities")
        except AttributeError as exc:
            raise PreparedOperationInvalid(
                "adapter capability descriptor is missing"
            ) from exc

    @classmethod
    def validate_prepared_operation(
        cls, adapter: object, prepared: object
    ) -> None:
        capabilities = cls._capabilities(adapter)
        try:
            contract_model = str(getattr(prepared, "contract_model"))
            adapter_id = str(getattr(prepared, "adapter_id"))
            operation = str(getattr(prepared, "operation"))
            target = getattr(prepared, "target")
            target_type = str(getattr(target, "target_type"))
            target_id = str(getattr(target, "target_id"))
            capability_model = str(getattr(capabilities, "contract_model"))
            capability_adapter = str(getattr(capabilities, "adapter_id"))
            supported_operations = tuple(
                str(item)
                for item in getattr(capabilities, "supported_operations")
            )
            rollback_supported = bool(
                getattr(capabilities, "rollback_supported")
            )
            rollback_available = bool(
                getattr(prepared, "rollback_available")
            )
            expected_effects = tuple(
                str(item) for item in getattr(prepared, "expected_effects")
            )
        except (AttributeError, TypeError) as exc:
            raise PreparedOperationInvalid(
                "prepared operation fields are incomplete"
            ) from exc
        if (
            contract_model != F3_ADAPTER_CONTRACT_MODEL
            or capability_model != F3_ADAPTER_CONTRACT_MODEL
            or adapter_id != capability_adapter
            or operation not in supported_operations
            or rollback_available != rollback_supported
        ):
            raise PreparedOperationInvalid(
                "prepared operation contradicts adapter capabilities"
            )
        try:
            validate_identifier(adapter_id, field_name="adapter_id")
            validate_identifier(operation, field_name="operation")
            validate_identifier(target_type, field_name="target_type")
            validate_identifier(target_id, field_name="target_id")
            for field in (
                "current_state_fingerprint",
                "normalized_proposed_hash",
                "prepared_operation_hash",
                "policy_decision_hash",
                "approval_bundle_hash",
                "verification_contract_hash",
            ):
                validate_sha256(getattr(prepared, field), field_name=field)
            validate_identifier(
                getattr(prepared, "verification_contract_model"),
                field_name="verification_contract_model",
            )
            if not expected_effects:
                raise ValueError("expected effects are empty")
            bounded_codes(expected_effects)
        except (AttributeError, TypeError, ValueError) as exc:
            raise PreparedOperationInvalid(
                "prepared operation evidence is invalid"
            ) from exc

    @staticmethod
    def _owner(identity: ExecutionIdentity, prepared: object) -> LockOwner:
        return LockOwner(
            owner_id=identity.owner_id,
            task_id=identity.task_id,
            plan_id=identity.plan_id,
            operation_id=str(getattr(prepared, "operation")),
            attempt_id=identity.attempt_id,
        )

    def _cancelled(self, task_id: str) -> bool:
        try:
            record = self.execution_repository.get(task_id)
        except ExecutionStorageError:
            return True
        return bool(
            record is not None
            and record.normalized_outcome == "cancelled_pre_dispatch"
        )

    @staticmethod
    def _diagnostic_codes(value: object) -> tuple[str, ...]:
        return bounded_diagnostics(getattr(value, "diagnostic_codes", ()))

    @staticmethod
    def _result(
        record: ExecutionRecord,
        *,
        duplicate: bool = False,
        extra_codes: tuple[str, ...] = (),
    ) -> ExecutorResult:
        outcome = record.normalized_outcome
        if outcome is None:
            outcome = "dispatch_indeterminate" if record.dispatch_intent else "observing"
        task_state = (
            NORMALIZED_OUTCOME_TO_TASK_STATE[outcome]
            if not record.terminal
            else record.task_state
        )
        result = ExecutorResult(
            task_id=record.execution_identity().task_id,
            attempt_id=record.execution_identity().attempt_id,
            outcome=outcome,
            task_state=task_state,
            terminal=record.terminal,
            dispatch_intent_recorded=record.dispatch_intent is not None,
            dispatch_count=record.dispatch_count,
            provider_response_received=record.provider_response_received,
            observation_required=(
                record.dispatch_intent is not None and not record.terminal
            ),
            redispatch_prohibited=record.dispatch_intent is not None,
            lock_keys=tuple(
                sorted(
                    {str(item["key"]) for item in record.lock_tokens},
                    key=lambda item: item.encode("utf-8"),
                )
            ),
            diagnostic_codes=bounded_diagnostics(extra_codes),
            duplicate_execution=duplicate,
        )
        result.validate()
        return result

    async def execute(
        self,
        *,
        adapter: object,
        prepared: object,
        identity: ExecutionIdentity,
        approval_consumption: ApprovalConsumptionRecorder,
    ) -> ExecutorResult:
        identity.validate()
        self.validate_prepared_operation(adapter, prepared)
        local_lock = self._task_locks.setdefault(
            identity.task_id, asyncio.Lock()
        )
        async with local_lock:
            return await self._execute_locked(
                adapter=adapter,
                prepared=prepared,
                identity=identity,
                approval_consumption=approval_consumption,
            )

    async def _execute_locked(
        self,
        *,
        adapter: object,
        prepared: object,
        identity: ExecutionIdentity,
        approval_consumption: ApprovalConsumptionRecorder,
    ) -> ExecutorResult:
        self.metrics.increment("executions_started")
        try:
            claim = self.execution_repository.claim(
                identity=identity,
                prepared=prepared,
                timing=self.executor_timing,
                now=self.now(),
            )
        except DuplicateExecutionActive as exc:
            self.metrics.increment("duplicate_execution_preventions")
            self.event_sink(
                {
                    "event_type": "duplicate_execution_prevented",
                    "task_id": identity.task_id,
                    "attempt_id": identity.attempt_id,
                    "owner_id": identity.owner_id,
                }
            )
            return self._result(
                exc.record,
                duplicate=True,
                extra_codes=("active_execution_exists",),
            )
        record = claim.record
        if record.terminal:
            await self._settle_terminal_locks(record)
            return self._result(record, duplicate=not claim.created)
        if record.dispatch_intent is not None:
            self.metrics.increment("blind_redispatch_preventions")
            return await self._recover_claimed(
                adapter=adapter,
                prepared=prepared,
                claim=claim,
                identity=identity,
            )

        handle: LockHandle | None = None
        try:
            self._inject("before_lock_acquisition")
            if not claim.created:
                self._release_expired_pre_dispatch_locks(
                    identity=identity,
                    prepared=prepared,
                )
            requests = normalize_lock_requests(
                getattr(adapter, "lock_requests")(prepared)
            )
            handle = await self.lock_store.acquire(
                requests,
                owner=self._owner(identity, prepared),
                timing=self.lock_timing,
                now=self.now,
                monotonic=self.monotonic,
                sleep=self.sleep,
                cancelled=lambda: self._cancelled(identity.task_id),
            )
            self.execution_repository.record_locks(
                identity.task_id,
                owner_id=identity.owner_id,
                claim_generation=claim.claim_generation,
                handle=handle,
                now=self.now(),
            )
            self._inject("after_lock_acquisition_before_preflight")
        except (LockConflict, LockWaitTimeout) as exc:
            record = self.execution_repository.terminalize_pre_dispatch(
                identity.task_id,
                owner_id=identity.owner_id,
                claim_generation=claim.claim_generation,
                outcome="lock_conflict",
                diagnostic_codes=(
                    "wait_timeout" if isinstance(exc, LockWaitTimeout) else "lock_conflict",
                ),
                now=self.now(),
            )
            return self._result(record)
        except LockWaitCancelled:
            self.execution_repository.cancel(identity.task_id, now=self.now())
            record = self.execution_repository.get(identity.task_id)
            if record is None:
                raise OperationExecutorError("cancelled execution record is missing")
            return self._result(record)
        except (DurableLockError, ValueError) as exc:
            record = self._terminal_pre_dispatch(
                claim,
                identity,
                outcome="failed_pre_dispatch",
                code="lock_storage_failure",
            )
            return self._result(record)

        try:
            preflight = await getattr(adapter, "preflight")(
                prepared, acquired_locks=requests
            )
            preflight_outcome = self._validate_preflight(prepared, preflight)
            if preflight_outcome is not None:
                self.metrics.increment("preflight_rejections")
                record = self.execution_repository.terminalize_pre_dispatch(
                    identity.task_id,
                    owner_id=identity.owner_id,
                    claim_generation=claim.claim_generation,
                    outcome=preflight_outcome,
                    diagnostic_codes=self._diagnostic_codes(preflight),
                    now=self.now(),
                )
                self.lock_store.release(handle)
                return self._result(record)
            self.execution_repository.record_preflight(
                identity.task_id,
                owner_id=identity.owner_id,
                claim_generation=claim.claim_generation,
                now=self.now(),
            )
            self._inject("after_preflight_before_durable_intent")
        except SimulatedProcessLoss:
            raise
        except Exception:
            record = self._terminal_pre_dispatch(
                claim,
                identity,
                outcome="provider_unavailable_pre_dispatch",
                code="preflight_failed",
            )
            self._release_safely(handle)
            return self._result(record)

        dispatch_metric_recorded = False
        irreversible_boundary_invoked = False
        approval_consumption_started = False
        approval_consumption_succeeded = False

        async def before_dispatch() -> None:
            nonlocal irreversible_boundary_invoked
            nonlocal approval_consumption_started
            nonlocal approval_consumption_succeeded
            nonlocal dispatch_metric_recorded
            if irreversible_boundary_invoked:
                raise AdapterContractViolation(
                    "irreversible dispatch callback was invoked more than once"
                )
            irreversible_boundary_invoked = True
            current = self.execution_repository.get(identity.task_id)
            if current is None:
                raise ExecutionStorageError("execution record disappeared")
            if current.terminal:
                raise ExecutionStorageError(
                    "terminal execution cannot consume approval"
                )
            self.lock_store.validate_handle(handle, now=self.now())
            approval_consumption_started = True
            await approval_consumption()
            approval_consumption_succeeded = True
            self._inject(
                "after_approval_consumption_before_durable_intent"
            )
            self.execution_repository.commit_dispatch_intent(
                identity.task_id,
                owner_id=identity.owner_id,
                claim_generation=claim.claim_generation,
                request_id=identity.request_id,
                provider_operation=str(getattr(preflight, "provider_operation")),
                provider_arguments_hash=str(
                    getattr(preflight, "provider_arguments_hash")
                ),
                timing=self.executor_timing,
                now=self.now(),
            )
            self.metrics.increment("durable_intents_committed")
            self.metrics.increment("dispatch_attempts")
            dispatch_metric_recorded = True
            self._inject("after_durable_intent_before_provider_invocation")

        renewer = _LeaseRenewer(
            store=self.lock_store,
            handle=handle,
            now=self.now,
        )
        renewer.start()
        try:
            dispatch = await getattr(adapter, "dispatch")(
                prepared, preflight, before_dispatch=before_dispatch
            )
            self._validate_dispatch(dispatch)
            persisted = self.execution_repository.get(identity.task_id)
            if persisted is None or persisted.dispatch_intent is None:
                raise AdapterContractViolation(
                    "adapter returned without committing durable intent"
                )
            outcome = enum_value(getattr(dispatch, "outcome"))
            response_received = bool(
                getattr(dispatch, "provider_response_received")
            )
            record = self.execution_repository.record_dispatch_result(
                identity.task_id,
                owner_id=identity.owner_id,
                claim_generation=claim.claim_generation,
                outcome=outcome,
                provider_response_received=response_received,
                diagnostic_codes=self._diagnostic_codes(dispatch),
                now=self.now(),
            )
            if outcome == "dispatch_failed_confirmed":
                self.metrics.increment("confirmed_dispatch_failures")
                self._release_safely(handle)
                return self._result(record)
            self._inject("after_provider_response_before_observation")
            return await self._observe_and_verify(
                adapter=adapter,
                prepared=prepared,
                claim=claim,
                identity=identity,
                handle=handle,
                dispatch=dispatch,
                recovering=False,
            )
        except SimulatedProcessLoss:
            raise
        except Exception:
            persisted = self.execution_repository.get(identity.task_id)
            if persisted is None:
                self._release_safely(handle)
                raise OperationExecutorError("execution record disappeared")
            if persisted.dispatch_intent is None:
                if approval_consumption_succeeded:
                    self.metrics.increment("durable_intent_failures")
                if persisted.terminal:
                    self._release_safely(handle)
                    return self._result(persisted)
                code = (
                    "approval_consumed_intent_not_recorded"
                    if approval_consumption_succeeded
                    else "approval_consumption_failed"
                    if approval_consumption_started
                    else "adapter_failed_before_irreversible_boundary"
                )
                if not approval_consumption_started:
                    record = self._terminal_pre_dispatch(
                        claim,
                        identity,
                        outcome="failed_pre_dispatch",
                        code=code,
                    )
                    self._release_safely(handle)
                    return self._result(record)
                self.execution_repository.record_pre_intent_retry(
                    identity.task_id,
                    owner_id=identity.owner_id,
                    claim_generation=claim.claim_generation,
                    diagnostic_code=code,
                    now=self.now(),
                )
                self._release_safely(handle)
                raise PreIntentRetryRequired(code)
            if not dispatch_metric_recorded:
                self.metrics.increment("durable_intents_committed")
                self.metrics.increment("dispatch_attempts")
            self.metrics.increment("indeterminate_dispatches")
            if not persisted.terminal:
                persisted = self.execution_repository.record_dispatch_result(
                    identity.task_id,
                    owner_id=identity.owner_id,
                    claim_generation=claim.claim_generation,
                    outcome="dispatch_indeterminate",
                    provider_response_received=False,
                    diagnostic_codes=("provider_result_uncertain",),
                    now=self.now(),
                )
            return await self._observe_and_verify(
                adapter=adapter,
                prepared=prepared,
                claim=claim,
                identity=identity,
                handle=handle,
                dispatch=None,
                recovering=True,
            )
        finally:
            await renewer.stop()

    def _validate_preflight(
        self, prepared: object, preflight: object
    ) -> str | None:
        try:
            eligible = getattr(preflight, "eligible")
            outcome_value = getattr(preflight, "outcome")
            outcome = enum_value(outcome_value) if outcome_value is not None else None
        except (AttributeError, ValueError) as exc:
            raise AdapterContractViolation("preflight result is invalid") from exc
        if eligible is not True:
            if outcome not in {
                "preflight_rejected",
                "provider_unavailable_pre_dispatch",
                "failed_pre_dispatch",
            }:
                raise AdapterContractViolation(
                    "ineligible preflight has an invalid outcome"
                )
            return outcome
        if outcome is not None:
            raise AdapterContractViolation("eligible preflight has an outcome")
        target = getattr(preflight, "confirmed_target", None)
        prepared_target = getattr(prepared, "target")
        if (
            target is None
            or getattr(target, "target_type", None)
            != getattr(prepared_target, "target_type")
            or getattr(target, "target_id", None)
            != getattr(prepared_target, "target_id")
        ):
            raise AdapterContractViolation("preflight target identity changed")
        try:
            validate_identifier(
                getattr(preflight, "provider_contract"),
                field_name="provider_contract",
            )
            validate_identifier(
                getattr(preflight, "provider_operation"),
                field_name="provider_operation",
            )
            validate_sha256(
                getattr(preflight, "provider_arguments_hash"),
                field_name="provider_arguments_hash",
            )
            validate_sha256(
                getattr(preflight, "evidence_hash"),
                field_name="evidence_hash",
            )
            self._diagnostic_codes(preflight)
            bounded_diagnostics(getattr(preflight, "mismatch_fields", ()))
        except (AttributeError, TypeError, ValueError) as exc:
            raise AdapterContractViolation(
                "eligible preflight evidence is invalid"
            ) from exc
        return None

    @staticmethod
    def _validate_dispatch(dispatch: object) -> None:
        try:
            outcome = enum_value(getattr(dispatch, "outcome"))
            intent = getattr(dispatch, "dispatch_intent_recorded")
            count = getattr(dispatch, "mutating_invocation_count")
            may_have_dispatched = getattr(dispatch, "may_have_dispatched")
            response_received = getattr(dispatch, "provider_response_received")
        except (AttributeError, ValueError) as exc:
            raise AdapterContractViolation("dispatch result is invalid") from exc
        if outcome not in {
            "dispatch_failed_confirmed",
            "dispatch_indeterminate",
            "observing",
        }:
            raise AdapterContractViolation("dispatch outcome is invalid")
        if intent is not True or count != 1 or may_have_dispatched is not True:
            raise AdapterContractViolation(
                "dispatch result contradicts the durable one-dispatch boundary"
            )
        if not isinstance(response_received, bool):
            raise AdapterContractViolation("dispatch response truth is invalid")

    async def _recover_claimed(
        self,
        *,
        adapter: object,
        prepared: object,
        claim: ExecutionClaim,
        identity: ExecutionIdentity,
    ) -> ExecutorResult:
        record = claim.record
        if record.dispatch_intent is None:
            raise OperationExecutorError("recovery requires durable intent")
        handle = self._recovery_handle(
            record=record,
            claim=claim,
            identity=identity,
            prepared=prepared,
        )
        if handle is None:
            yielded = self.execution_repository.yield_claim(
                identity.task_id,
                owner_id=identity.owner_id,
                claim_generation=claim.claim_generation,
                now=self.now(),
            )
            return self._result(
                yielded,
                duplicate=True,
                extra_codes=("recovery_waiting_for_lock_lease",),
            )
        return await self._observe_and_verify(
            adapter=adapter,
            prepared=prepared,
            claim=claim,
            identity=identity,
            handle=handle,
            dispatch=None,
            recovering=True,
        )

    def _recovery_handle(
        self,
        *,
        record: ExecutionRecord,
        claim: ExecutionClaim,
        identity: ExecutionIdentity,
        prepared: object,
    ) -> LockHandle | None:
        lock_records = {
            (item.key, item.generation): item
            for item in self.lock_store.records()
        }
        durable_tokens = tuple(
            LockToken(
                key=str(item["key"]),
                generation=int(item["generation"]),
                mode=str(item["mode"]),
            )
            for item in record.lock_tokens
        )
        if not durable_tokens:
            return None
        selected = []
        for token in durable_tokens:
            candidate = lock_records.get((token.key, token.generation))
            if candidate is None:
                return None
            if candidate.conflict_hold:
                return None
            selected.append(candidate)
        old_owner_ids = {item.owner_id for item in selected}
        if len(old_owner_ids) != 1:
            raise OperationExecutorError("durable lock owner set is contradictory")
        old_owner = LockOwner(
            owner_id=next(iter(old_owner_ids)),
            task_id=selected[0].task_id,
            plan_id=selected[0].plan_id,
            operation_id=selected[0].operation_id,
            attempt_id=selected[0].attempt_id,
        )
        old_handle = LockHandle(
            owner=old_owner,
            tokens=durable_tokens,
            acquired_at=min(item.acquired_at for item in selected),
            lease_expires_at=min(
                selected,
                key=lambda item: parse_timestamp(
                    item.lease_expires_at, field_name="lease_expires_at"
                ),
            ).lease_expires_at,
            timing=self.lock_timing,
        )
        try:
            self.lock_store.validate_handle(old_handle, now=self.now())
            if old_owner.owner_id != identity.owner_id:
                return None
            return old_handle
        except LockLeaseExpired:
            pass
        except LockOwnershipError:
            return None
        decisions = {
            (item.key, item.generation): StaleRecoveryDecision(
                action=StaleRecoveryAction.TRANSFER_FOR_OBSERVATION,
                reason_code="process_reconstruction_observation",
            )
            for item in selected
        }
        transfer_owner = self._owner(identity, prepared)
        recovery = self.lock_store.recover_expired(
            decisions,
            transfer_owner=transfer_owner,
            transfer_timing=self.lock_timing,
            now=self.now(),
        )
        handle = recovery.transferred_handle
        if handle is None:
            return None
        self.execution_repository.replace_recovery_locks(
            identity.task_id,
            owner_id=identity.owner_id,
            claim_generation=claim.claim_generation,
            handle=handle,
            now=self.now(),
        )
        return handle

    def _release_expired_pre_dispatch_locks(
        self,
        *,
        identity: ExecutionIdentity,
        prepared: object,
    ) -> None:
        """Release only the exact expired attempt's pre-intent lock set."""
        decisions: dict[tuple[str, int], StaleRecoveryDecision] = {}
        for record in self.lock_store.expired_records(now=self.now()):
            if (
                record.task_id == identity.task_id
                and record.plan_id == identity.plan_id
                and record.operation_id == str(getattr(prepared, "operation"))
                and record.attempt_id == identity.attempt_id
            ):
                decisions[(record.key, record.generation)] = StaleRecoveryDecision(
                    action=StaleRecoveryAction.RELEASE,
                    reason_code="pre_dispatch_process_reconstruction",
                )
        if decisions:
            self.lock_store.recover_expired(decisions, now=self.now())

    async def _observe_and_verify(
        self,
        *,
        adapter: object,
        prepared: object,
        claim: ExecutionClaim,
        identity: ExecutionIdentity,
        handle: LockHandle,
        dispatch: object | None,
        recovering: bool,
    ) -> ExecutorResult:
        try:
            self.lock_store.validate_handle(handle, now=self.now())
        except DurableLockError:
            return self._manual_review(
                claim,
                identity,
                handle,
                reason_code="lock_ownership_uncertain",
            )
        record = self.execution_repository.get(identity.task_id)
        if record is None or record.dispatch_intent is None:
            raise OperationExecutorError("observation record is missing durable intent")
        if self._deadline_expired(record):
            return self._manual_review(
                claim,
                identity,
                handle,
                reason_code="post_dispatch_evidence_deadline_expired",
            )
        try:
            if recovering:
                context = InternalRecoveryContext(
                    dispatch_intent_recorded=True,
                    provider_invocation_may_have_occurred=True,
                    provider_response_received=record.provider_response_received,
                    prior_observation_attempts=record.observation_attempts,
                    prior_verification_attempts=record.verification_attempts,
                    post_dispatch_deadline=str(
                        record.dispatch_intent["evidence_deadline"]
                    ),
                )
                observation = await getattr(adapter, "recover")(
                    prepared, context=context
                )
            else:
                observation = await getattr(adapter, "observe")(
                    prepared, dispatch
                )
            self._validate_observation(observation)
            self.metrics.increment("observations")
            observation_outcome = enum_value(getattr(observation, "outcome"))
            record = self.execution_repository.record_observation(
                identity.task_id,
                owner_id=identity.owner_id,
                claim_generation=claim.claim_generation,
                outcome=observation_outcome,
                diagnostic_codes=self._diagnostic_codes(observation),
                now=self.now(),
            )
            self._inject("after_observation_before_verification")
            if observation_outcome == "manual_review_required":
                return self._manual_review(
                    claim,
                    identity,
                    handle,
                    reason_code="adapter_recovery_requires_manual_review",
                )
            if getattr(observation, "observation_complete") is not True:
                if (
                    record.observation_attempts
                    >= self.executor_timing.max_observation_attempts
                    or self._deadline_expired(record)
                ):
                    return self._manual_review(
                        claim,
                        identity,
                        handle,
                        reason_code="observation_evidence_exhausted",
                    )
                renewal_failure = self._renewal_failure_result(
                    claim, identity, handle
                )
                if renewal_failure is not None:
                    return renewal_failure
                return self._yield_and_result(record, claim, identity)
        except SimulatedProcessLoss:
            raise
        except Exception:
            record = self.execution_repository.get(identity.task_id)
            if record is None:
                raise OperationExecutorError("observation record disappeared")
            if (
                record.observation_attempts
                >= self.executor_timing.max_observation_attempts
                or self._deadline_expired(record)
            ):
                return self._manual_review(
                    claim,
                    identity,
                    handle,
                    reason_code="observation_failed_exhausted",
                )
            record = self.execution_repository.record_observation(
                identity.task_id,
                owner_id=identity.owner_id,
                claim_generation=claim.claim_generation,
                outcome="observing",
                diagnostic_codes=("observation_failed",),
                now=self.now(),
            )
            renewal_failure = self._renewal_failure_result(
                claim, identity, handle
            )
            if renewal_failure is not None:
                return renewal_failure
            return self._yield_and_result(record, claim, identity)

        try:
            verification = await getattr(adapter, "verify")(
                prepared, observation
            )
            outcome, terminal = self._validate_verification(verification)
            evidence: dict[str, Any] = {}
            for field in (
                "evidence_hash",
                "resulting_state_fingerprint",
                "manual_review_reason_code",
            ):
                value = getattr(verification, field, None)
                if value is not None:
                    evidence[field] = value
            mismatch_fields = getattr(verification, "mismatch_fields", ())
            if mismatch_fields:
                evidence["mismatch_fields"] = tuple(mismatch_fields)
            record = self.execution_repository.record_verification(
                identity.task_id,
                owner_id=identity.owner_id,
                claim_generation=claim.claim_generation,
                outcome=outcome,
                terminal=terminal,
                diagnostic_codes=(
                    "exact_readback_verified"
                    if outcome == "succeeded_verified"
                    else "exact_readback_not_verified",
                ),
                evidence=evidence,
                now=self.now(),
            )
            if outcome == "succeeded_verified":
                self.metrics.increment("verification_successes")
            elif outcome == "verification_mismatch":
                self.metrics.increment("verification_mismatches")
            self._inject("after_verified_result_before_lock_release")
            if terminal:
                if outcome == "manual_review_required":
                    self.metrics.increment("manual_review_transitions")
                    self.lock_store.promote_to_conflict_hold(
                        handle, reason_code="manual_review_unresolved_dispatch"
                    )
                else:
                    self._release_safely(handle)
            else:
                renewal_failure = self._renewal_failure_result(
                    claim, identity, handle
                )
                if renewal_failure is not None:
                    return renewal_failure
                return self._yield_and_result(record, claim, identity)
            return self._result(record)
        except SimulatedProcessLoss:
            raise
        except Exception:
            record = self.execution_repository.get(identity.task_id)
            if record is None:
                raise OperationExecutorError("verification record disappeared")
            if (
                record.verification_attempts
                >= self.executor_timing.max_verification_attempts
                or self._deadline_expired(record)
            ):
                return self._manual_review(
                    claim,
                    identity,
                    handle,
                    reason_code="verification_failed_exhausted",
                )
            record = self.execution_repository.record_verification(
                identity.task_id,
                owner_id=identity.owner_id,
                claim_generation=claim.claim_generation,
                outcome="observing",
                terminal=False,
                diagnostic_codes=("verification_failed",),
                now=self.now(),
            )
            renewal_failure = self._renewal_failure_result(
                claim, identity, handle
            )
            if renewal_failure is not None:
                return renewal_failure
            return self._yield_and_result(record, claim, identity)

    @staticmethod
    def _validate_observation(observation: object) -> None:
        try:
            outcome = enum_value(getattr(observation, "outcome"))
            attempt_count = getattr(observation, "attempt_count")
            complete = getattr(observation, "observation_complete")
        except (AttributeError, ValueError) as exc:
            raise AdapterContractViolation("observation result is invalid") from exc
        if outcome not in {
            "observing",
            "verification_mismatch",
            "manual_review_required",
        }:
            raise AdapterContractViolation("observation outcome is invalid")
        if not isinstance(attempt_count, int) or attempt_count < 1:
            raise AdapterContractViolation("observation attempt is invalid")
        if not isinstance(complete, bool):
            raise AdapterContractViolation("observation completion is invalid")
        evidence_hash = getattr(observation, "evidence_hash", None)
        if evidence_hash is not None:
            validate_sha256(evidence_hash, field_name="evidence_hash")
        bounded_diagnostics(getattr(observation, "mismatch_fields", ()))
        bounded_diagnostics(getattr(observation, "diagnostic_codes", ()))

    @staticmethod
    def _validate_verification(verification: object) -> tuple[str, bool]:
        try:
            outcome = enum_value(getattr(verification, "outcome"))
            attempt_count = getattr(verification, "attempt_count")
        except (AttributeError, ValueError) as exc:
            raise AdapterContractViolation("verification result is invalid") from exc
        if outcome not in {
            "observing",
            "verification_mismatch",
            "succeeded_verified",
            "failed_post_dispatch",
            "manual_review_required",
        }:
            raise AdapterContractViolation("verification outcome is invalid")
        if not isinstance(attempt_count, int) or attempt_count < 1:
            raise AdapterContractViolation("verification attempt is invalid")
        terminal = outcome != "observing"
        if outcome == "succeeded_verified" and getattr(verification, "verified") is not True:
            raise AdapterContractViolation("verified success lacks exact proof")
        if outcome == "verification_mismatch" and getattr(verification, "verified") is not False:
            raise AdapterContractViolation("verification mismatch lacks exact proof")
        for field in ("evidence_hash", "resulting_state_fingerprint"):
            value = getattr(verification, field, None)
            if value is not None:
                validate_sha256(value, field_name=field)
        bounded_diagnostics(getattr(verification, "mismatch_fields", ()))
        reason = getattr(verification, "manual_review_reason_code", None)
        if outcome == "manual_review_required":
            if not bounded_diagnostics((reason,)):
                raise AdapterContractViolation("manual review reason is invalid")
        return outcome, terminal

    def _deadline_expired(self, record: ExecutionRecord) -> bool:
        if record.dispatch_intent is None:
            return False
        deadline = parse_timestamp(
            record.dispatch_intent["evidence_deadline"],
            field_name="evidence_deadline",
        )
        return self.now() >= deadline

    def _manual_review(
        self,
        claim: ExecutionClaim,
        identity: ExecutionIdentity,
        handle: LockHandle,
        *,
        reason_code: str,
    ) -> ExecutorResult:
        record = self.execution_repository.record_verification(
            identity.task_id,
            owner_id=identity.owner_id,
            claim_generation=claim.claim_generation,
            outcome="manual_review_required",
            terminal=True,
            diagnostic_codes=(reason_code,),
            evidence={"manual_review_reason_code": reason_code},
            now=self.now(),
        )
        self.metrics.increment("manual_review_transitions")
        try:
            self.lock_store.promote_to_conflict_hold(
                handle, reason_code="manual_review_unresolved_dispatch"
            )
        except DurableLockError:
            # A stale or fenced record still fails closed: it remains present
            # until task-aware stale recovery explicitly creates the hold.
            pass
        return self._result(record)

    def _terminal_pre_dispatch(
        self,
        claim: ExecutionClaim,
        identity: ExecutionIdentity,
        *,
        outcome: str,
        code: str,
    ) -> ExecutionRecord:
        return self.execution_repository.terminalize_pre_dispatch(
            identity.task_id,
            owner_id=identity.owner_id,
            claim_generation=claim.claim_generation,
            outcome=outcome,
            diagnostic_codes=(code,),
            now=self.now(),
        )

    def _yield_and_result(
        self,
        record: ExecutionRecord,
        claim: ExecutionClaim,
        identity: ExecutionIdentity,
    ) -> ExecutorResult:
        yielded = self.execution_repository.yield_claim(
            identity.task_id,
            owner_id=identity.owner_id,
            claim_generation=claim.claim_generation,
            now=self.now(),
        )
        return self._result(yielded)

    def _renewal_failure_result(
        self,
        claim: ExecutionClaim,
        identity: ExecutionIdentity,
        handle: LockHandle,
    ) -> ExecutorResult | None:
        try:
            self.lock_store.renew(handle, now=self.now())
            return None
        except DurableLockError:
            return self._manual_review(
                claim,
                identity,
                handle,
                reason_code="lock_renewal_failed",
            )

    def _release_safely(self, handle: LockHandle) -> None:
        try:
            self.lock_store.release(handle)
        except DurableLockError:
            pass

    async def _settle_terminal_locks(self, record: ExecutionRecord) -> None:
        if not record.lock_tokens:
            return
        if record.normalized_outcome == "manual_review_required":
            return
        lock_records = {
            (item.key, item.generation): item
            for item in self.lock_store.records()
        }
        selected = []
        tokens = []
        for item in record.lock_tokens:
            token = LockToken(
                key=str(item["key"]),
                generation=int(item["generation"]),
                mode=str(item["mode"]),
            )
            candidate = lock_records.get((token.key, token.generation))
            if candidate is None or candidate.conflict_hold:
                return
            selected.append(candidate)
            tokens.append(token)
        owner_ids = {item.owner_id for item in selected}
        if len(owner_ids) != 1:
            return
        handle = LockHandle(
            owner=LockOwner(
                owner_id=next(iter(owner_ids)),
                task_id=selected[0].task_id,
                plan_id=selected[0].plan_id,
                operation_id=selected[0].operation_id,
                attempt_id=selected[0].attempt_id,
            ),
            tokens=tuple(tokens),
            acquired_at=min(item.acquired_at for item in selected),
            lease_expires_at=min(
                selected,
                key=lambda item: parse_timestamp(
                    item.lease_expires_at, field_name="lease_expires_at"
                ),
            ).lease_expires_at,
            timing=self.lock_timing,
        )
        self._release_safely(handle)

    async def cancel(self, task_id: str) -> bool:
        validate_identifier(task_id, field_name="task_id")
        record = self.execution_repository.get(task_id)
        cancelled = self.execution_repository.cancel(task_id, now=self.now())
        if not cancelled or record is None or record.dispatch_intent is not None:
            return cancelled
        terminal = self.execution_repository.get(task_id)
        if terminal is not None:
            await self._settle_terminal_locks(terminal)
        return True
