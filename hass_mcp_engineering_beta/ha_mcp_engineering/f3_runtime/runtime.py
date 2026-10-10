"""Beta 20 activation of the accepted F3 executor and adapter families."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import threading
import time
import uuid
from typing import Any, Awaitable, Callable

from ..errors import ErrorCode, GovernanceError
from .health_scan import HealthScanFence
from ..governance.task_storage import TaskHealthSnapshotSuperseded
from ..f3.executor import PreIntentRetryRequired, SharedOperationExecutor
from ..f3.contracts import (
    LockMode,
    LockRequest,
    LockScope,
    NormalizedOperationOutcome,
    ObservationResult,
)
from ..f3.locks import (
    DurableLockStore,
    LockConflict,
    LockLeaseExpired,
    LockOwnershipError,
    LockStorageError,
    LockWaitCancelled,
    StaleRecoveryAction,
    StaleRecoveryDecision,
)
from ..f3.models import (
    EXECUTION_CLASS_POST_INTENT,
    EXECUTION_CLASS_PRE_INTENT,
    EXECUTION_CLASS_TERMINAL_POST_INTENT,
    EXECUTION_CLASS_TERMINAL_PRE_DISPATCH,
    EXECUTION_CLASS_VERIFIED_NO_DISPATCH,
    ExecutionIdentity,
    ExecutorTiming,
    LockHandle,
    LockOwner,
    LockTiming,
    LockToken,
    validate_identifier,
    validate_sha256,
)
from ..f3.persistence import (
    DuplicateExecutionActive,
    ExecutionRecordCorrupt,
    ExecutionStorageError,
)
from ..f3.operational_adapter import (
    OperationalAdministrationAdapter,
    validate_operational_executor_timing,
)
from ..f3.operational_models import (
    CAPABILITY_IDENTITIES,
    EVIDENCE_DEADLINE_SECONDS,
    OPERATIONAL_EVIDENCE_PROJECTION_MODEL,
    OPERATIONAL_PREPARED_AUTHORITY_MODEL,
    OperationalAuthoritySnapshot,
    OperationalEvidenceProjection,
    OperationalPreparationRequest,
)
from ..f3_configuration.adapter import ConfigurationOperationAdapter
from ..f3_configuration.gateway import ExistingConfigurationGatewayBridge
from ..f3_configuration.locks import lock_set_hash, resource_lock_key
from ..f3_configuration.migration import (
    proposal_from_configuration_operation,
    proposal_from_legacy_automation_plan,
)
from ..f3_configuration.sequence import prepare_configuration_sequence
from ..f3_dashboard.adapter import (
    CAPABILITY_ID as DASHBOARD_CAPABILITY_ID,
    DashboardPreparationRequest,
    DashboardUpdateAdapter,
)
from ..f3_dashboard.identity import operational_identity_from_mapping
from ..f3_dashboard.errors import DashboardFoundationError
from ..governance.models import (
    ApprovalState,
    ChangeOperation,
    PlanStatus,
    StepExecutionStatus,
)
from ..governance.helper_state import (
    HELPER_STATE_PROVIDER_SLUG,
    helper_state_provider_evidence,
)
from ..governance.normalize import stable_hash
from ..governance import configuration_rollback_eligibility as inverse_eligibility
from ..governance.policy import POLICY_VERSION
from ..governance.resources import resource_fingerprint
from ..governance.task_models import ExecutionTaskState, TERMINAL_TASK_STATES
from ..governance.task_storage import ExecutionTaskStorageError
from ..request_context import begin_request, current_telemetry, end_request
from .registry import ClosedAdapterRegistry
from .repository import (
    ACTIVE_RECOVERY_CHECKPOINT_LIMIT,
    ChildExecutionRepository,
    UnrecordedLockAuthorityError,
    MAX_F3_PUBLIC_TASKS,
    RECOVERY_DECLARATION_PAGE_SIZE,
    canonical_hash,
    child_declaration,
    deterministic_child_id,
)


F3_RUNTIME_MODEL = "f3-runtime-integration-v1"
F3_EXECUTION_AUTHORITY = "f3_child_sequence"
_INVERSE_LOCK_CAPABILITIES = {"update_automation_configuration", "update_script_configuration"}
PRODUCTION_LOCK_TIMING = LockTiming(120, 20, 0, 0.05)
RECOVERY_CADENCE_SECONDS = 30
RECOVERY_BATCH_SIZE = ACTIVE_RECOVERY_CHECKPOINT_LIMIT
RECOVERY_SWEEP_TIME_BUDGET_SECONDS = 5.0
ORPHAN_RECOVERY_SCAN_LIMIT = RECOVERY_DECLARATION_PAGE_SIZE
ORPHAN_RECONCILIATION_REASON = "orphaned_terminal_parent_recovery"
ORPHAN_RECONCILIATION_RESULT = "orphaned_pre_dispatch_child_reconciled"
PERSISTED_AUDIT_EVENT_MODEL = "f3-persisted-audit-event-v1"
_RECOVERY_MODE_CHECKPOINT = "checkpoint"
_RECOVERY_MODE_POST_INTENT = "post_intent"
_RECOVERY_MODE_PRE_INTENT = "pre_intent"
_RECOVERY_MODE_TERMINAL_PROJECTION = "terminal_projection"
_RECOVERY_READINESS: ContextVar[tuple[object, int] | None] = ContextVar(
    "f3_recovery_readiness", default=None
)
_ACTIVE_F3_CHILD: ContextVar[str | None] = ContextVar(
    "f3_active_child", default=None
)


class _CoreDispatchAuthorityGuard:
    """Bind one F3 dispatch to the exact current Core authority generation."""

    def __init__(self, runtime: Any, plan_created_at: str):
        self.runtime = runtime
        self.plan_created_at = plan_created_at

    async def acquire(
        self, prepared: object, preflight: object
    ) -> object | None:
        del preflight
        # A mutation may not rely on the periodic snapshot alone.  Re-run the
        # bounded two-observation contract after final adapter preflight and
        # before approval consumption; any Core drift retires the old lease.
        await self.runtime.reconcile_once("mutation_pre_dispatch")
        return self.runtime.acquire_f3(
            prepared,
            plan_created_at=self.plan_created_at,
        )

    def consume(self, authority: object) -> object | None:
        return self.runtime.consume(authority)

    def revalidate(self, authority: object, commits: object) -> bool:
        return self.runtime.revalidate(authority, commits)

    def release(self, authority: object) -> bool:
        return self.runtime.release(authority)

    def finish(self, commits: object) -> bool:
        return self.runtime.finish(commits)


def _persisted_audit_event_id(
    child_id: str, event: dict[str, Any]
) -> str:
    """Return the stable identity of one validated persisted F3 event."""

    validate_identifier(child_id, field_name="child_id")
    if (
        not isinstance(event, dict)
        or type(event.get("sequence")) is not int
        or event["sequence"] < 1
    ):
        raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
    value = {
        "model": PERSISTED_AUDIT_EVENT_MODEL,
        "child_id": child_id,
        "event_sequence": event["sequence"],
        "event": event,
    }
    try:
        canonical = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise GovernanceError(
            ErrorCode.EXECUTION_TASK_STORAGE_ERROR
        ) from exc
    return hashlib.sha256(canonical).hexdigest()


def _approval_bundle_hash(plan: Any) -> str:
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
            "elevated": None if elevated is None else {
                "action_kind": elevated.kind.value,
                "state": elevated.state.value,
                "principal": elevated.approver_principal,
                "bound_plan_hash": elevated.bound_plan_hash,
                "policy_decision_hash": elevated.policy_decision_hash,
            },
        }
    )


class RuntimeLockStore(DurableLockStore):
    """Apply child-declared selective hold policy at the core escalation hook."""

    def __init__(
        self,
        root: str,
        children: ChildExecutionRepository,
        *,
        event_sink: Callable[[dict[str, object]], None],
        now: Callable[[], datetime],
    ):
        super().__init__(root, event_sink=event_sink)
        self.children = children
        self.now = now
        self.integration = None

    def acquire_once(self, requests, *, owner, timing, now=None):
        requests = tuple(requests)
        if self.integration is not None and owner.operation_id in _INVERSE_LOCK_CAPABILITIES:
            try:
                transferred = self.integration._inverse_lock_acquire(
                    tuple(requests), owner=owner, timing=timing, now=now or self.now())
            except (LockOwnershipError, LockLeaseExpired) as exc:
                # Exact inverse refusal is not a durable-storage outage. Actual
                # LockStorageError still reaches the executor's fault handling.
                raise LockConflict(tuple(item.key for item in requests)) from exc
            if transferred is not None:
                return transferred
        return super().acquire_once(requests, owner=owner, timing=timing, now=now)

    def release(self, handle, *, pre_dispatch_cleanup=False):
        # Acquisition-failure cleanup already holds the child transaction and
        # has terminal zero-dispatch authority; do not re-enter that transaction.
        if (not pre_dispatch_cleanup and self.integration is not None
                and handle.owner.operation_id in _INVERSE_LOCK_CAPABILITIES
                and self.integration._retain_inverse_locks(handle.owner.task_id)):
            return ()
        return super().release(handle, pre_dispatch_cleanup=pre_dispatch_cleanup)

    def recover_expired(self, decisions, **kwargs):
        if self.integration is not None:
            for record in self.records():
                decision = decisions.get((record.key, record.generation))
                if (decision is not None and decision.action == StaleRecoveryAction.RELEASE
                        and record.operation_id in _INVERSE_LOCK_CAPABILITIES
                        and self.integration._retain_inverse_locks(record.task_id)):
                    raise LockOwnershipError("inverse sequence ownership cannot be released for reacquisition")
        return super().recover_expired(decisions, **kwargs)

    def promote_to_conflict_hold(self, handle, *, reason_code: str) -> None:
        declaration = self.children.declaration(handle.owner.task_id)
        held = self.promote_selective_conflict_hold(
            handle,
            retained_keys=declaration["selective_hold_keys"],
            reason_code=reason_code,
        )
        self.children.update_runtime(
            handle.owner.task_id,
            changes={
                "selective_hold_tokens": [
                    {"key": item.key, "generation": item.generation, "mode": item.mode}
                    for item in held
                ],
                "selective_hold_promoted_at": self.now().isoformat(),
                "selective_hold_reason": reason_code,
            },
        )


class _InverseAcquisitionRepository:
    """Preserve an exact inverse cancellation at atomic token publication.

    Reuse the existing lock writer with a guarded mutate_claimed boundary.
    Every other repository method delegates to the real repository unchanged.
    The guard runs after its durable read and owner/generation fence, before
    any write; it neither catches storage failures nor releases any locks.
    """

    record_locks = ChildExecutionRepository.record_locks

    def __init__(self, repository, declaration):
        self._repository = repository
        self._declaration = declaration

    def __getattr__(self, name):
        return getattr(self._repository, name)

    def mutate_claimed(self, task_id, *, owner_id, claim_generation, mutator):
        d = self._declaration

        def publish(record):
            if (
                task_id == d["child_id"]
                and record.identity == {
                    "task_id": d["child_id"], "plan_id": d["plan_id"],
                    "attempt_id": d["attempt_id"], "request_id": d["request_id"],
                    "owner_id": owner_id,
                }
                and record.prepared_operation_hash == d["prepared_operation_hash"]
                and record.operation == d["capability_id"]
                and record.target == {"target_type": d["target_type"], "target_id": d["target_id"]}
                and record.terminal
                and record.normalized_outcome == "cancelled_pre_dispatch"
                and record.dispatch_intent is None
                and record.dispatch_count == 0
            ):
                # Recovery may already have bound/settled the transferred set.
                # The executor's existing cancellation path preserves this
                # terminal result; sequence recovery owns exact lock settlement.
                raise LockWaitCancelled("inverse acquisition was cancelled before token publication")
            mutator(record)

        return self._repository.mutate_claimed(
            task_id, owner_id=owner_id, claim_generation=claim_generation,
            mutator=publish,
        )


class _SequenceLockAdapter:
    """Require the full immutable sequence lock union for every child attempt."""

    def __init__(self, adapter: Any, complete_requests: tuple[Any, ...]):
        self._adapter = adapter
        self._complete = complete_requests
        self.capabilities = adapter.capabilities

    def __getattr__(self, name: str) -> Any:
        return getattr(self._adapter, name)

    def lock_requests(self, _prepared: Any) -> tuple[Any, ...]:
        return self._complete

    async def preflight(self, prepared: Any, *, acquired_locks: tuple[Any, ...]):
        from ..f3.locks import normalize_lock_requests

        if normalize_lock_requests(acquired_locks) != normalize_lock_requests(self._complete):
            raise ValueError("complete configuration sequence locks are not held")
        return await self._adapter.preflight(
            prepared,
            acquired_locks=self._adapter.lock_requests(prepared),
        )


class _RetryBundleAdapter(_SequenceLockAdapter):
    """Whole-bundle checks inside the existing lock/authority envelope.

    Recovery exposes observation only; historical child verification does not
    establish current whole-bundle state. The last child's observation binds
    a separate full-bundle readback without adding a durable success schema.
    """

    def __init__(self, adapter, complete_requests, check, final_member,
                 success_code="retry_bundle_current_verified"):
        super().__init__(adapter, complete_requests)
        self._check = check
        self._final_member = final_member
        self._success_code = success_code

    async def preflight(self, prepared, *, acquired_locks):
        from ..f3.locks import normalize_lock_requests

        if normalize_lock_requests(acquired_locks) != normalize_lock_requests(self._complete):
            raise ValueError("complete configuration sequence locks are not held")
        reason = await self._check(False)
        if reason:
            return self._adapter._preflight_rejected(prepared, (reason,))
        # Preserve the adapter's final current-child validation/stale decision.
        return await super().preflight(prepared, acquired_locks=acquired_locks)

    async def _bundle_observation(self, observation):
        if not self._final_member or not observation.observation_complete:
            return observation
        reason = await self._check(True)
        code = reason or self._success_code
        return replace(
            observation,
            semantic_match=False if reason else observation.semantic_match,
            intended_result_observed=False if reason else observation.intended_result_observed,
            evidence_hash=stable_hash({"child": observation.evidence_hash, "bundle": code}),
            diagnostic_codes=(*observation.diagnostic_codes, code),
        )

    async def observe(self, prepared, dispatch):
        return await self._bundle_observation(await self._adapter.observe(prepared, dispatch))

    async def recover(self, prepared, *, context):
        return await self._bundle_observation(await self._adapter.recover(prepared, context=context))


class _LegacyConflictAdapter:
    """Refuse new dispatch while a conflicting historical task is active."""

    def __init__(self, adapter: Any, detector: Callable[[tuple[Any, ...]], bool]):
        self._adapter = adapter
        self._detector = detector
        self.capabilities = adapter.capabilities

    def __getattr__(self, name: str) -> Any:
        return getattr(self._adapter, name)

    async def preflight(self, prepared: Any, *, acquired_locks: tuple[Any, ...]):
        result = await self._adapter.preflight(
            prepared, acquired_locks=acquired_locks
        )
        if result.eligible and self._detector(acquired_locks):
            return replace(
                result,
                eligible=False,
                outcome=NormalizedOperationOutcome.PREFLIGHT_REJECTED,
                provider_contract=None,
                provider_operation=None,
                provider_arguments_hash=None,
                evidence_hash=stable_hash(
                    {"category": "legacy_active_task_conflict"}
                ),
                diagnostic_codes=("legacy_active_task_conflict",),
                mismatch_fields=(),
            )
        return result


class _ReadbackOnlyRecoveryAdapter:
    """Expose only observation and verification for a durable old intent."""

    def __init__(self, adapter: Any):
        self._adapter = adapter
        self.capabilities = adapter.capabilities

    async def recover(self, prepared: Any, *, context: Any):
        return await self._adapter.recover(prepared, context=context)

    async def observe(self, prepared: Any, dispatch: Any):
        return await self._adapter.observe(prepared, dispatch)

    async def verify(self, prepared: Any, observation: Any):
        return await self._adapter.verify(prepared, observation)


class _CoreVerificationAdapter:
    """Require current Core readback authority without enabling redispatch."""

    def __init__(self, adapter: Any, core_runtime: Any):
        self._adapter = adapter
        self._core_runtime = core_runtime
        self.capabilities = adapter.capabilities

    def __getattr__(self, name: str) -> Any:
        return getattr(self._adapter, name)

    @staticmethod
    def _held_observation() -> ObservationResult:
        return ObservationResult(
            outcome=NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED,
            attempt_count=1,
            observation_complete=False,
            provider_reachable=None,
            target_reachable=None,
            readback_state_fingerprint=None,
            intended_result_observed=None,
            evidence_hash=stable_hash(
                {"category": "core_verification_authority_unavailable"}
            ),
            diagnostic_codes=(
                "core_verification_authority_unavailable",
            ),
        )

    async def _observe_with_authority(
        self,
        prepared: Any,
        callback: Callable[[], Awaitable[Any]],
    ) -> Any:
        # Readback is authority-bearing evidence.  Refresh Core identity before
        # invoking the existing provider, but recovery remains observation-only.
        await self._core_runtime.reconcile_once("mutation_verification")
        authority = self._core_runtime.acquire_f3(prepared)
        if authority is None:
            return self._held_observation()
        commits = self._core_runtime.consume(authority)
        if commits is None:
            self._core_runtime.release(authority)
            return self._held_observation()
        telemetry = current_telemetry()
        request_token = None
        if telemetry is None:
            telemetry, request_token = begin_request()
        prior_authorizer = telemetry.core_dispatch_authorizer

        def authorize_core_dispatch() -> bool:
            if (
                prior_authorizer is not None
                and prior_authorizer() is not True
            ):
                return False
            return self._core_runtime.revalidate(authority, commits)

        telemetry.core_dispatch_authorizer = authorize_core_dispatch
        try:
            if not telemetry.authorize_core_dispatch():
                return self._held_observation()
            return await callback()
        finally:
            telemetry.core_dispatch_authorizer = lambda: False
            self._core_runtime.finish(commits)
            if request_token is not None:
                end_request(request_token)
            else:
                telemetry.core_dispatch_authorizer = prior_authorizer

    async def recover(self, prepared: Any, *, context: Any):
        return await self._observe_with_authority(
            prepared,
            lambda: self._adapter.recover(prepared, context=context),
        )

    async def observe(self, prepared: Any, dispatch: Any):
        return await self._observe_with_authority(
            prepared,
            lambda: self._adapter.observe(prepared, dispatch),
        )


class _AuditedDashboardGateway:
    """Emit bounded provider-boundary events without persisting payloads."""

    def __init__(self, delegate: Any, auditor: Callable[[str], None]):
        self.delegate = delegate
        self.auditor = auditor

    async def preread(self, *, url_path: str):
        self.auditor("provider_ha_config_get_dashboard")
        return await self.delegate.preread(url_path=url_path)

    async def best_practice_key(
        self, *, expected_provider_authority_evidence_hash: str
    ):
        self.auditor("provider_ha_get_skill_guide")
        return await self.delegate.best_practice_key(
            expected_provider_authority_evidence_hash=(
                expected_provider_authority_evidence_hash
            )
        )

    async def write(self, **arguments):
        self.auditor("provider_ha_config_set_dashboard")
        return await self.delegate.write(**arguments)


class _OperationalEvidenceReader:
    def __init__(self, runtime: "F3RuntimeIntegration"):
        self.runtime = runtime

    def read(self, operation: Any) -> OperationalEvidenceProjection:
        record = self.runtime.children.get(operation.child_execution_id)
        runtime = self.runtime.children.runtime(operation.child_execution_id)
        if record is None:
            return OperationalEvidenceProjection(
                source_model=OPERATIONAL_EVIDENCE_PROJECTION_MODEL,
                public_task_id=operation.public_task_id,
                child_execution_id=operation.child_execution_id,
                plan_id=operation.plan_id,
                dispatch_intent_recorded=False,
                dispatch_count=0,
                intent_committed_at=None,
                evidence_deadline=None,
                provider_response_received=False,
            )
        intent = record.dispatch_intent
        holds = tuple(
            item["key"] for item in runtime["selective_hold_tokens"]
        )
        reason = record.evidence.get("manual_review_reason_code")
        operation_evidence = runtime["operation_evidence"]
        return OperationalEvidenceProjection(
            source_model=OPERATIONAL_EVIDENCE_PROJECTION_MODEL,
            public_task_id=operation.public_task_id,
            child_execution_id=operation.child_execution_id,
            plan_id=operation.plan_id,
            dispatch_intent_recorded=intent is not None,
            dispatch_count=record.dispatch_count,
            intent_committed_at=(None if intent is None else intent["committed_at"]),
            evidence_deadline=(None if intent is None else intent["evidence_deadline"]),
            provider_response_received=record.provider_response_received,
            provider_operation_id=operation_evidence["provider_operation_id"],
            provider_backup_id=operation_evidence["provider_backup_id"],
            outage_observed=operation_evidence["outage_observed"],
            reconnect_observed=operation_evidence["reconnect_observed"],
            provider_readmission_observed=(
                operation_evidence["provider_readmission_observed"]
            ),
            observation_attempt_count=record.observation_attempts,
            verification_attempt_count=record.verification_attempts,
            restart_backoff_attempt_count=int(runtime["backoff_seconds"] > 0),
            next_eligible_observation_at=runtime["next_eligible_at"],
            manual_review_reason_code=reason,
            selective_hold_keys=holds,
            jsonl_authoritative=False,
        )


class _AuditedConfigurationGateway:
    """Central fixed bridge that adds no authority or provider arguments."""

    def __init__(self, delegate: Any, auditor: Callable[[str], None]):
        self.delegate = delegate
        self.auditor = auditor
        self.provider_admitted = delegate.provider_admitted

    async def read(self, resource_type, target_id):
        return await self.delegate.read(resource_type, target_id)

    async def validate_all(self):
        return await self.delegate.validate_all()

    async def create_target_absent(self, resource_type, target_id):
        return await self.delegate.create_target_absent(resource_type, target_id)

    async def write(self, action, resource_type, target_id, proposed_config):
        self.auditor("provider_invocation_started")
        try:
            result = await self.delegate.write(
                action, resource_type, target_id, proposed_config
            )
        except Exception as exc:
            details = getattr(exc, "details", {})
            response_received = bool(
                isinstance(details, dict)
                and details.get("provider_response_received") is True
            ) or getattr(exc, "mutation_dispatched", None) is False
            self.auditor(
                "provider_response_received"
                if response_received
                else "provider_response_lost"
            )
            raise
        self.auditor("provider_response_received")
        return result


class _AuditedHelperStateGateway:
    """Audit the one direct mutation boundary and bounded exact readbacks."""

    def __init__(self, delegate: Any, auditor: Callable[[str], None]):
        self.delegate = delegate
        self.auditor = auditor

    async def planning_evidence(self, entity_id: str):
        self.auditor("provider_readback_started")
        try:
            result = await self.delegate.planning_evidence(entity_id)
        except Exception:
            self.auditor("provider_readback_failed")
            raise
        self.auditor("provider_readback_received")
        return result

    async def read_state(self, entity_id: str):
        self.auditor("provider_readback_started")
        try:
            result = await self.delegate.read_state(entity_id)
        except Exception:
            self.auditor("provider_readback_failed")
            raise
        self.auditor("provider_readback_received")
        return result

    async def set_state(
        self, entity_id: str, desired_state: str, *, before_dispatch
    ):
        async def boundary():
            await before_dispatch()
            self.auditor("provider_invocation_started")

        try:
            result = await self.delegate.set_state(
                entity_id,
                desired_state,
                before_dispatch=boundary,
            )
        except Exception as exc:
            self.auditor(
                "provider_response_lost"
                if getattr(exc, "dispatched", False)
                else "provider_invocation_refused"
            )
            raise
        self.auditor("provider_response_received")
        return result


class _EvidenceCapturingBackupGateway:
    """Persist only C2's bounded backup identity evidence."""

    def __init__(
        self,
        delegate: Any,
        recorder: Callable[..., None],
        auditor: Callable[[str], None],
    ):
        self.delegate = delegate
        self.recorder = recorder
        self.auditor = auditor

    async def planning_evidence(self):
        return await self.delegate.planning_evidence()

    async def create_full_backup(self, name, *, before_dispatch):
        async def boundary():
            await before_dispatch()
            self.auditor("provider_invocation_started")

        try:
            result = await self.delegate.create_full_backup(
                name, before_dispatch=boundary
            )
        except Exception as exc:
            self.auditor(
                "provider_response_lost"
                if getattr(exc, "dispatched", False)
                else "provider_invocation_refused"
            )
            raise
        self.auditor("provider_response_received")
        self.recorder(
            provider_operation_id=getattr(result, "operation_id", None),
            provider_backup_id=getattr(result, "backup_id", None),
        )
        return result

    async def verify_full_backup(self, **kwargs):
        return await self.delegate.verify_full_backup(**kwargs)


class _EvidenceCapturingLifecycleGateway:
    """Persist bounded disruption/readmission facts before adapter projection."""

    def __init__(
        self,
        delegate: Any,
        recorder: Callable[..., None],
        auditor: Callable[[str], None],
    ):
        self.delegate = delegate
        self.recorder = recorder
        self.auditor = auditor

    async def _dispatch(self, call, *, before_dispatch):
        async def boundary():
            await before_dispatch()
            self.auditor("provider_invocation_started")

        try:
            result = await call(boundary)
        except Exception as exc:
            self.auditor(
                "provider_response_lost"
                if getattr(exc, "dispatched", False)
                else "provider_invocation_refused"
            )
            raise
        self.auditor("provider_response_received")
        return result

    async def planning_evidence(self, operation, target):
        return await self.delegate.planning_evidence(operation, target)

    async def dispatch_reload(self, target, *, before_dispatch):
        return await self._dispatch(
            lambda boundary: self.delegate.dispatch_reload(
                target, before_dispatch=boundary
            ),
            before_dispatch=before_dispatch,
        )

    async def dispatch_addon_restart(self, slug, *, before_dispatch):
        return await self._dispatch(
            lambda boundary: self.delegate.dispatch_addon_restart(
                slug, before_dispatch=boundary
            ),
            before_dispatch=before_dispatch,
        )

    async def dispatch_home_assistant_restart(self, *, before_dispatch):
        return await self._dispatch(
            lambda boundary: self.delegate.dispatch_home_assistant_restart(
                before_dispatch=boundary
            ),
            before_dispatch=before_dispatch,
        )

    async def verify_reload(self, target):
        return await self.delegate.verify_reload(target)

    async def verify_addon_restart(self, slug, **kwargs):
        result = await self.delegate.verify_addon_restart(slug, **kwargs)
        evidence = result.get("evidence") if isinstance(result, dict) else None
        if isinstance(evidence, dict):
            self.recorder(
                provider_readmission_observed=(
                    evidence.get("restart_proof") == "upstream_readmission"
                )
            )
        return result

    async def verify_home_assistant_restart(self, **kwargs):
        result = await self.delegate.verify_home_assistant_restart(**kwargs)
        evidence = result.get("evidence") if isinstance(result, dict) else None
        if isinstance(evidence, dict):
            reconnected = evidence.get("home_assistant_reconnected") is True
            self.recorder(
                outage_observed=evidence.get("outage_observed") is True,
                reconnect_observed=reconnected,
                provider_readmission_observed=(
                    reconnected
                    and (
                        evidence.get("post_restart_configuration_valid") is True
                        or isinstance(evidence.get("runtime_checks"), dict)
                    )
                ),
            )
        return result


class F3RuntimeIntegration:
    """Sole Beta 20 execution authority for all accepted covered routes."""

    def __init__(
        self,
        *,
        service: Any,
        storage_root: str,
        configuration_gateway: Any,
        backup_gateway: Any,
        lifecycle_gateway: Any,
        provider_identity_reader: Callable[[], Awaitable[dict[str, str]]],
        retention_days: int,
        helper_state_gateway: Any = None,
        dashboard_gateway: Any | None = None,
        core_runtime: Any | None = None,
    ):
        self.service = service
        self.core_runtime = core_runtime
        self.children = ChildExecutionRepository(
            storage_root, retention_days=retention_days,
            event_sink=self._emit_lock_cleanup_audit_event,
        )
        self.children.recover_initialization(service.task_repository)
        self.locks = RuntimeLockStore(
            storage_root,
            self.children,
            event_sink=self._emit_f3_audit_event,
            now=service.now,
        )
        self.locks.integration = self
        self._reconstruct_selective_holds()
        self._finish_pending_hold_releases()
        self.provider_identity_reader = provider_identity_reader
        config_gateway = _AuditedConfigurationGateway(
            ExistingConfigurationGatewayBridge(configuration_gateway),
            self._audit_provider_boundary,
        )
        configuration_adapters = {
            f"{action}_{resource}_configuration": ConfigurationOperationAdapter(
                resource, action, config_gateway, now=service.now
            )
            for resource in ("automation", "script", "input_boolean", "input_number")
            for action in ("create", "update")
        }
        self.evidence_reader = _OperationalEvidenceReader(self)
        captured_backup = (
            None
            if backup_gateway is None
            else _EvidenceCapturingBackupGateway(
                backup_gateway,
                self._record_operation_evidence,
                self._audit_provider_boundary,
            )
        )
        captured_lifecycle = (
            None
            if lifecycle_gateway is None
            else _EvidenceCapturingLifecycleGateway(
                lifecycle_gateway,
                self._record_operation_evidence,
                self._audit_provider_boundary,
            )
        )
        self.operational_adapter = OperationalAdministrationAdapter(
            backup_gateway=captured_backup,
            lifecycle_gateway=captured_lifecycle,
            helper_state_gateway=(
                _AuditedHelperStateGateway(
                    helper_state_gateway,
                    self._audit_provider_boundary,
                )
                if helper_state_gateway is not None
                else None
            ),
            helper_dependency_risk_reader=(
                service.helper_dependency_risk_reader
            ),
            evidence_reader=self.evidence_reader,
            authority_reader=self._operational_authority,
            now=service.now,
        )
        self.dashboard_adapter = DashboardUpdateAdapter(
            _AuditedDashboardGateway(
                dashboard_gateway, self._audit_provider_boundary
            )
            if dashboard_gateway is not None
            else None,
            service.dashboard_artifacts,
            now=service.now,
        )
        self.registry = ClosedAdapterRegistry.build(
            configuration_adapters=configuration_adapters,
            operational_adapter=self.operational_adapter,
            dashboard_adapter=self.dashboard_adapter,
        )
        self._configuration_adapters = configuration_adapters
        from ..governance.configuration_reverification import ConfigurationReverification
        self.configuration_reverification = ConfigurationReverification(self, storage_root)
        self._prepared_cache: dict[str, Any] = {}
        self._sequence_lock_cache: dict[str, tuple[Any, ...]] = {}
        # This process-local lifecycle is admission evidence, not a storage cache.
        # Deep health may run in a worker; publish transitions under one short lock.
        self._readiness_lock = threading.RLock()
        self._startup_complete = False
        self._readiness_faults: dict[str, int] = {}
        self._readiness_fault_categories: dict[str, int] = {}
        self._readiness_revision = 0
        self._recovery_running = False
        self._supervisor_task: asyncio.Task | None = None
        self._coordinator_initialized = False
        self._last_sweep_at: str | None = None
        self._next_sweep_at: str | None = None
        self._sweep_collisions = 0
        self._sweep_failures = 0
        self._approval_consumption_failures = 0
        self._fallback_count = 0
        self._recovery_monotonic = time.monotonic

    def readiness_state(self) -> dict[str, Any]:
        """Bounded lifecycle observation: no repositories, providers or scheduling."""
        with self._readiness_lock:
            supervisor_stopped = (
                self._supervisor_task is not None and self._supervisor_task.done()
            )
            faulted = bool(self._readiness_faults) or supervisor_stopped
            return {
                "request_ready": self._startup_complete,
                "execution_ready": self._startup_complete and not faulted,
                "status": "faulted" if faulted else (
                    "ready" if self._startup_complete else "initializing"
                ),
                "faults": sorted({
                    *self._readiness_fault_categories
                } | ({"supervisor_stopped"} if supervisor_stopped else set())),
            }

    def _readiness_fault(self, key: str) -> None:
        with self._readiness_lock:
            self._readiness_revision += 1
            if key not in self._readiness_faults:
                category = key.split(":", 1)[0]
                self._readiness_fault_categories[category] = (
                    self._readiness_fault_categories.get(category, 0) + 1
                )
            self._readiness_faults[key] = self._readiness_revision

    def _clear_readiness_fault(self, key: str, revision: int | None) -> None:
        with self._readiness_lock:
            if revision is not None and self._readiness_faults.get(key) == revision:
                del self._readiness_faults[key]
                category = key.split(":", 1)[0]
                self._readiness_fault_categories[category] -= 1
                if not self._readiness_fault_categories[category]:
                    del self._readiness_fault_categories[category]

    def _require_execution_ready(self) -> None:
        with self._readiness_lock:
            if self.readiness_state()["execution_ready"]:
                return
            recovery = _RECOVERY_READINESS.get()
            if (
                recovery is not None and recovery[0] is self
                and "execution_storage" not in self._readiness_faults
                and (self._supervisor_task is None or not self._supervisor_task.done())
                and all(
                    revision <= recovery[1] or (
                        key.startswith("recovery_child:")
                        and key != "recovery_child:" + str(_ACTIVE_F3_CHILD.get())
                    )
                    for key, revision in self._readiness_faults.items()
                )
            ):
                # Only an existing recovery operation in this validated pass.
                # Its exact plan, current authority, locks and intent checks
                # still decide dispatch. This never admits a new apply request.
                return
        raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)

    def _validate_readiness_storage(self) -> None:
        """Recovery-only audit, including history beyond the scheduler's page.

        Never called by readiness. Read every declaration/envelope even when a
        reconciliation response would truncate at 100 pending children. This
        establishes read integrity, not spare capacity or future write success.
        """
        self.children.health()
        self.children.active_recovery_checkpoint()
        self.children.active_recovery_cursor()
        self.children.recovery_cursor()
        self.locks.snapshot()
        self.service.task_repository.list()
        for declaration in self.children.all_declarations():
            self.children.get(declaration["child_id"])
            self.children.runtime(declaration["child_id"])
        self._health()
        with self._readiness_lock:
            task_ids = tuple(key.split(":", 1)[1] for key in self._readiness_faults
                             if key.startswith("task_projection:"))
        for task_id in task_ids:
            task = self.service.task_repository.get(task_id)
            if task is None:
                raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
            self._decorate_task(task)

    def _record_operation_evidence(self, **values: Any) -> None:
        child_id = _ACTIVE_F3_CHILD.get()
        if child_id is None:
            raise RuntimeError("operational evidence has no active child authority")
        runtime = self.children.runtime(child_id)
        existing = dict(runtime["operation_evidence"])
        for name in ("provider_operation_id", "provider_backup_id"):
            candidate = values.get(name)
            if candidate is not None:
                if not isinstance(candidate, str) or not candidate:
                    raise RuntimeError("operational identity evidence is invalid")
                if existing[name] not in {None, candidate}:
                    raise RuntimeError("operational identity evidence changed")
                existing[name] = candidate
        for name in (
            "outage_observed", "reconnect_observed",
            "provider_readmission_observed",
        ):
            if values.get(name) is True:
                existing[name] = True
        existing["evidence_hash"] = stable_hash(
            {name: value for name, value in existing.items() if name != "evidence_hash"}
        )
        self.children.update_runtime(
            child_id, changes={"operation_evidence": existing}
        )

    def _audit_provider_boundary(self, event_type: str) -> None:
        if event_type == "provider_invocation_started":
            self._require_execution_ready()
        child_id = _ACTIVE_F3_CHILD.get()
        if child_id is not None:
            self._emit_f3_audit_event(
                {"event_type": event_type, "task_id": child_id}
            )

    def _reconstruct_selective_holds(self) -> None:
        records = self.locks.records()
        for declaration in self.children.all_declarations():
            held = tuple(
                item for item in records
                if item.task_id == declaration["child_id"] and item.conflict_hold
            )
            runtime = self.children.runtime(declaration["child_id"])
            stored = runtime["selective_hold_tokens"]
            if held:
                if {item.key for item in held} != set(
                    declaration["selective_hold_keys"]
                ):
                    raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
                tokens = [
                    {"key": item.key, "generation": item.generation, "mode": item.mode}
                    for item in held
                ]
                if stored and stored != tokens:
                    raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
                if not stored:
                    self.children.update_runtime(
                        declaration["child_id"],
                        changes={
                            "selective_hold_tokens": tokens,
                            "selective_hold_promoted_at": held[0].last_renewed_at,
                            "selective_hold_reason": "reconstructed_conflict_hold",
                        },
                    )
            elif stored and runtime["hold_release_authority"] is None:
                parent = self.service.task_repository.get(
                    declaration["public_task_id"]
                )
                execution = self.children.get(declaration["child_id"])
                if not (
                    parent is not None
                    and self._is_terminal_zero_dispatch_parent(parent)
                    and execution is not None
                    and execution.dispatch_intent is None
                ):
                    raise GovernanceError(
                        ErrorCode.EXECUTION_TASK_STORAGE_ERROR
                    )
                # A process can die after a terminal-parent cancellation or
                # exact lock release but before clearing the runtime token
                # projection. Preserve that evidence for the bounded recovery
                # sweep instead of making startup permanently fail.

    def _hold_owner(
        self, declaration: dict[str, Any], record: Any, tokens: list[dict[str, Any]]
    ) -> LockOwner:
        lock_records = self.locks.records()
        owners = {
            item.owner_id for item in lock_records
            if any(
                item.key == token["key"]
                and item.generation == token["generation"]
                for token in tokens
            )
        }
        if len(owners) != 1:
            raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        identity = record.execution_identity()
        return LockOwner(
            owner_id=next(iter(owners)),
            task_id=declaration["child_id"],
            plan_id=declaration["plan_id"],
            operation_id=record.operation,
            attempt_id=identity.attempt_id,
        )

    def _finish_pending_hold_releases(self) -> None:
        """Complete a previously authorized release without provider access."""

        for declaration in self.children.all_declarations():
            runtime = self.children.runtime(declaration["child_id"])
            authority = runtime["hold_release_authority"]
            if authority is None:
                continue
            record = self.children.get(declaration["child_id"])
            if record is None:
                raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
            tokens = authority["tokens"]
            lock_records = self.locks.records()
            present = [
                token for token in tokens
                if any(
                    item.key == token["key"]
                    and item.generation == token["generation"]
                    and item.conflict_hold
                    for item in lock_records
                )
            ]
            if present and len(present) != len(tokens):
                raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
            if present:
                owner = self._hold_owner(declaration, record, tokens)
                self.locks.release_conflict_hold(
                    owner=owner,
                    tokens=tuple(
                        LockToken(item["key"], item["generation"], item["mode"])
                        for item in tokens
                    ),
                    reason_code=authority["reason_code"],
                )
            self.children.update_runtime(
                declaration["child_id"],
                changes={
                    "selective_hold_tokens": [],
                    "hold_release_authority": None,
                    "last_reconciliation_at": self.service.now().isoformat(),
                    "reconciliation_result": "hold_release_finalized",
                },
            )

    def _f3_audit_entry(
        self,
        event: dict[str, object],
        *,
        declaration: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Project one bounded core or lock event into an audit entry."""

        child_id = event.get("task_id")
        if not isinstance(child_id, str):
            return None
        if declaration is None:
            try:
                declaration = self.children.declaration(child_id)
            except Exception:
                return None
        event_type = str(event.get("event_type", "f3_event"))[:64]
        cleanup_codes: list[str] = []
        if event_type == "lock_failure_cleanup":
            raw_codes = event.get("diagnostic_codes")
            if isinstance(raw_codes, (list, tuple)):
                cleanup_codes = [code for code in raw_codes[:4]
                                 if isinstance(code, str) and code in {
                                     "lock_storage_failure", "lock_cleanup_not_acquired",
                                     "lock_cleanup_completed", "lock_cleanup_failed",
                                     "lock_terminalization_failed",
                                 }]
        return {
            "event": f"f3_{event_type}",
            **(
                {"audit_event_id": event["audit_event_id"]}
                if isinstance(event.get("audit_event_id"), str)
                else {}
            ),
            "request_id": declaration["request_id"],
            "access": "write",
            "operation_class": "f3_execution_lifecycle",
            "task_id": declaration["public_task_id"],
            "child_execution_id": child_id,
            "plan_id": declaration["plan_id"],
            "operation_id": declaration["operation_id"],
            "attempt_id": declaration["attempt_id"],
            "capability_identity": declaration["capability_id"],
            "target_type": declaration["target_type"],
            "target_id": declaration["target_id"],
            "outcome": event_type,
            "dispatch_possible": event_type in {
                "execution_started",
                "locks_acquired",
                "preflight_completed",
                "lock_renewed",
            },
            "evidence_references": {
                key: value
                for key, value in event.items()
                if key in {
                    "lock_key", "generation", "reason_code", "phase",
                    "dispatch_count", "observation_count", "verification_count",
                }
            },
            **({"diagnostic_codes": cleanup_codes} if cleanup_codes else {}),
            "result_status": "error" if event_type == "lock_failure_cleanup" else "success",
            "fallback_occurred": False,
            "fallback": "none",
        }

    def _emit_lock_cleanup_audit_event(self, event: dict[str, object]) -> bool:
        # This sole repository callback is emitted after its transaction exits.
        # Other repository events keep the existing durable replay path.
        if event.get("event_type") != "lock_failure_cleanup":
            return False
        return self._emit_f3_audit_event(event)

    def _emit_f3_audit_event(self, event: dict[str, object]) -> bool:
        """Project a bounded core or lock event into the existing audit sink."""

        safe = self._f3_audit_entry(event)
        audit = self.service.audit
        return safe is not None and audit is not None and audit.write(safe)

    def _audit_record_events(
        self, declaration: dict[str, Any], record: Any | None
    ) -> bool:
        if record is None:
            return True
        runtime = self.children.runtime(declaration["child_id"])
        start = int(runtime["audited_event_count"])
        if start > len(record.events):
            raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        audit = self.service.audit
        if audit is None:
            return True
        pending: list[dict[str, Any]] = []
        for item in record.events[start:]:
            diagnostics = tuple(item.get("diagnostic_codes") or ())
            audit_event_id = _persisted_audit_event_id(
                declaration["child_id"], item
            )
            safe = self._f3_audit_entry(
                {
                    "event_type": item["event_type"],
                    "task_id": declaration["child_id"],
                    "audit_event_id": audit_event_id,
                    "phase": record.state,
                    **(
                        {"reason_code": diagnostics[0]}
                        if diagnostics
                        else {}
                    ),
                    "dispatch_count": record.dispatch_count,
                    "observation_count": record.observation_attempts,
                    "verification_count": record.verification_attempts,
                },
                declaration=declaration,
            )
            if safe is None:
                break
            pending.append(safe)
        completed = start + audit.write_batch(pending)
        if start != completed:
            self.children.update_runtime(
                declaration["child_id"],
                changes={"audited_event_count": completed},
            )
        return completed == len(record.events)

    @staticmethod
    def is_covered_plan(plan: Any) -> bool:
        return (
            plan.contract_version in {1, 2}
            and plan.operation in {
                ChangeOperation.CREATE_AUTOMATION,
                ChangeOperation.UPDATE_AUTOMATION,
                ChangeOperation.CONFIGURATION_PLAN,
            }
        ) or (
            plan.contract_version == 3
            and plan.operation in {
                ChangeOperation.CREATE_FULL_BACKUP,
                ChangeOperation.CONTROLLED_RELOAD,
                ChangeOperation.RESTART_ADDON,
                ChangeOperation.RESTART_HOME_ASSISTANT,
                ChangeOperation.SET_INPUT_BOOLEAN_STATE,
                ChangeOperation.UPDATE_DASHBOARD,
            }
        )

    def should_route(self, plan: Any) -> bool:
        if not self.is_covered_plan(plan):
            return False
        existing = self.service.task_repository.get_for_plan(plan.plan_id)
        if existing is None:
            return True
        return existing.legacy_projection.get("execution_authority") == F3_EXECUTION_AUTHORITY

    def handle_legacy_apply(self, plan: Any, expected_plan_hash: str) -> dict[str, Any]:
        """Refuse new dispatch through a pre-Beta-20 execution authority."""

        task = self.service.task_repository.get_for_plan(plan.plan_id)
        if task is None or task.plan_hash != self.service.plan_hash(plan):
            raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        if not expected_plan_hash or expected_plan_hash != task.plan_hash:
            raise GovernanceError(ErrorCode.APPROVAL_HASH_MISMATCH)
        if task.state in TERMINAL_TASK_STATES:
            return self.service._terminal_task_apply_result(task, plan)
        self.service._record_task_event(
            task, "duplicate_apply_prevented", changes={}
        )
        raise GovernanceError(
            ErrorCode.DUPLICATE_APPLY_ATTEMPT,
            details={
                "task_id": task.task_id,
                "task_state": task.state.value,
                "execution_authority": "legacy_pre_beta20",
                "provider_dispatch_occurred": bool(task.dispatched_at),
                "required_action": (
                    "read_only_legacy_reconciliation"
                    if task.dispatched_at
                    else "create_new_plan"
                ),
            },
        )

    async def _provider_identity(self) -> dict[str, str]:
        value = await self.provider_identity_reader()
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("slug"), str)
            or not isinstance(value.get("evidence_hash"), str)
        ):
            raise GovernanceError(ErrorCode.OPERATIONAL_PROVIDER_UNAVAILABLE)
        return value

    @staticmethod
    def _dashboard_provider_identity(plan: Any) -> dict[str, str]:
        operational = getattr(plan, "operational", None)
        baseline = getattr(operational, "baseline", None)
        if not isinstance(baseline, dict):
            raise GovernanceError(ErrorCode.OPERATIONAL_PROVIDER_UNAVAILABLE)
        value = baseline.get("dashboard_operational_identity")
        if not isinstance(value, dict):
            raise GovernanceError(ErrorCode.OPERATIONAL_PROVIDER_UNAVAILABLE)
        try:
            identity = operational_identity_from_mapping(value)
        except ValueError:
            raise GovernanceError(
                ErrorCode.OPERATIONAL_PROVIDER_UNAVAILABLE,
                details={"reason": "dashboard_provider_identity_invalid"},
            ) from None
        return {
            "slug": identity.authority.provider_slug,
            "evidence_hash": identity.evidence_hash,
        }

    @staticmethod
    def _approved_copy(plan: Any) -> Any:
        value = deepcopy(plan)
        value.status = PlanStatus.APPROVED
        value.approval.state = ApprovalState.APPROVED
        value.approval.bundle_state = "fully_approved"
        if value.approval.elevated_risk_acknowledgement is not None:
            value.approval.elevated_risk_acknowledgement.state = ApprovalState.APPROVED
        return value

    def _historical_approval_witnesses(self, plan, task, declarations):
        """Check consumed history against its original grant without renewing it."""
        approval_hash = _approval_bundle_hash(self._approved_copy(plan))
        sequence_hash = task.legacy_projection.get("sequence_hash")
        if (plan.apply_request_id != task.execution_request_id
                or task.approval_reference != self.service._task_approval_reference(plan)
                or sequence_hash != self.children.manifest_for_task(task.task_id)["sequence_hash"]):
            raise GovernanceError(ErrorCode.APPROVAL_HASH_MISMATCH)
        expected = {
            "plan_hash": task.plan_hash,
            "policy_decision_hash": plan.approval.policy_decision_hash,
            "approval_bundle_hash": approval_hash,
            "public_task_id": task.task_id,
            "sequence_hash": sequence_hash,
            "consumed_at": plan.approval.consumed_at,
        }
        witnesses = []
        for declaration in declarations:
            witness = self.children.runtime(declaration["child_id"])["approval_consumption_reference"]
            if (declaration["approval_bundle_hash"] != approval_hash
                    or declaration["request_id"] != task.execution_request_id
                    or witness != expected):
                raise GovernanceError(ErrorCode.APPROVAL_HASH_MISMATCH)
            witnesses.append(witness)
        return witnesses

    async def _prepare(
        self,
        plan: Any,
        task: Any,
        approval_hash: str,
        provider_identity: dict[str, str] | None = None,
        *,
        readback_only: bool = False,
    ):
        plan_hash = self.service.plan_hash(plan)
        if plan.contract_version == 2:
            prepared = []
            for operation in sorted(plan.operations, key=lambda item: item.order):
                resource = operation.helper_type or operation.resource_type
                capability = f"{operation.action}_{resource}_configuration"
                adapter = self.registry.adapter(capability)
                proposal = proposal_from_configuration_operation(
                    plan,
                    operation,
                    task_id=task.task_id,
                    plan_hash=plan_hash,
                    approval_bundle_hash=approval_hash,
                    provider_admitted=True,
                    policy_snapshot_valid=True,
                )
                prepared.append(await adapter.prepare(proposal))
            sequence = prepare_configuration_sequence(prepared)
            inverse = inverse_eligibility.bound(plan)
            if inverse is not None:
                from ..f3_configuration.locks import normalize_lock_requests
                # Protect untouched members as well as selected inverse targets.
                requests = normalize_lock_requests((*sequence.lock_requests, *(
                    LockRequest(resource_lock_key(m["resource_type"], m["target_id"]),
                                (LockScope.RESOURCE,), LockMode.EXCLUSIVE,
                                ("hamcp135_inverse_complete_bundle",))
                    for m in inverse["source"]["members"]
                )))
                return tuple(prepared), requests, stable_hash({
                    "model": inverse_eligibility.MODEL,
                    "sequence": sequence.sequence_hash, "lock_set_hash": lock_set_hash(requests),
                })
            return tuple(prepared), sequence.lock_requests, sequence.sequence_hash
        if plan.contract_version == 1:
            capability = (
                "create_automation_configuration"
                if plan.operation == ChangeOperation.CREATE_AUTOMATION
                else "update_automation_configuration"
            )
            adapter = self.registry.adapter(capability)
            proposal = proposal_from_legacy_automation_plan(
                plan,
                task_id=task.task_id,
                plan_hash=plan_hash,
                approval_bundle_hash=approval_hash,
                provider_admitted=True,
                policy_snapshot_valid=True,
            )
            prepared = await adapter.prepare(proposal)
            sequence = prepare_configuration_sequence((prepared,))
            return (prepared,), sequence.lock_requests, sequence.sequence_hash

        if plan.operation is ChangeOperation.SET_INPUT_BOOLEAN_STATE:
            identity = {
                "slug": HELPER_STATE_PROVIDER_SLUG,
                "evidence_hash": stable_hash(
                    helper_state_provider_evidence()
                ),
            }
        elif plan.operation is ChangeOperation.UPDATE_DASHBOARD:
            identity = provider_identity or self._dashboard_provider_identity(
                plan
            )
        else:
            identity = provider_identity or await self._provider_identity()
        operation_id = plan.operation.value
        child_id = deterministic_child_id(task.task_id, plan.plan_id, operation_id, 0)
        if plan.operation is ChangeOperation.UPDATE_DASHBOARD:
            prepared = await self.dashboard_adapter.prepare(
                DashboardPreparationRequest(
                    plan=self._approved_copy(plan),
                    expected_plan_hash=plan_hash,
                    approval_bundle_hash=approval_hash,
                    public_task_id=task.task_id,
                    child_execution_id=child_id,
                    authoritative_provider_slug=identity["slug"],
                    provider_identity_evidence_hash=identity["evidence_hash"],
                )
            )
            requests = self.dashboard_adapter.lock_requests(prepared)
            sequence_model = "f3-dashboard-sequence-v1"
        else:
            proposal = OperationalPreparationRequest(
                plan=self._approved_copy(plan),
                expected_plan_hash=plan_hash,
                public_task_id=task.task_id,
                child_execution_id=child_id,
                authoritative_provider_slug=identity["slug"],
                provider_identity_evidence_hash=identity["evidence_hash"],
            )
            if readback_only:
                prepared = (
                    await self.operational_adapter.reconstruct_for_readback(
                        proposal
                    )
                )
                requests = (
                    self.operational_adapter.lock_requests_for_readback(
                        prepared
                    )
                )
            else:
                prepared = await self.operational_adapter.prepare(proposal)
                requests = self.operational_adapter.lock_requests(prepared)
            sequence_model = "f3-operational-sequence-v1"
        return (prepared,), requests, stable_hash(
            {
                "model": sequence_model,
                "plan_id": plan.plan_id,
                "public_task_id": task.task_id,
                "prepared_operation_hash": prepared.prepared_operation_hash,
                "lock_set_hash": canonical_hash([
                    {"key": item.key, "mode": item.mode.value}
                    for item in requests
                ]),
            }
        )

    @staticmethod
    def _mark_task_authority(task: Any, sequence_hash: str, child_ids: list[str]) -> None:
        marker = {
            **task.legacy_projection,
            "execution_authority": F3_EXECUTION_AUTHORITY,
            "f3_model": F3_RUNTIME_MODEL,
            "sequence_hash": sequence_hash,
            "child_execution_ids": child_ids,
        }
        task.legacy_projection = marker
        task.events[0].changes["legacy_projection"] = marker

    async def _initialize(self, plan: Any, expected_plan_hash: str):
        if (
            plan.policy_decision is None
            or plan.policy_decision.policy_version != POLICY_VERSION
        ):
            raise GovernanceError(ErrorCode.POLICY_SNAPSHOT_MISMATCH)
        calculated = self.service.plan_hash(plan)
        if (
            not expected_plan_hash
            or expected_plan_hash != calculated
            or not self.service._valid_external_approval(plan, "apply")
        ):
            raise GovernanceError(ErrorCode.APPROVAL_HASH_MISMATCH)
        task = self.service._create_task_for_plan(plan, calculated, persist=False)
        approval_hash = _approval_bundle_hash(plan)
        prepared, complete_requests, sequence_hash = await self._prepare(
            plan, task, approval_hash
        )
        declarations = []
        for ordinal, operation in enumerate(prepared):
            operation_id = getattr(operation, "operation_id", operation.operation)
            child_id = deterministic_child_id(task.task_id, plan.plan_id, operation_id, ordinal)
            attempt_id = (
                f"attempt-{ordinal}-{stable_hash({'child_id': child_id})[:24]}"
            )
            capability = getattr(operation, "capability_identity", None) or getattr(
                operation, "capability_id"
            )
            adapter = self.registry.adapter(capability)
            holds = getattr(operation, "selective_hold_keys", None) or (
                resource_lock_key(operation.resource_type, operation.target.target_id),
            )
            if any(inverse_eligibility.markers(op) for op in plan.operations):
                holds = tuple(item.key for item in complete_requests)
            declarations.append(
                child_declaration(
                    public_task_id=task.task_id,
                    plan_id=plan.plan_id,
                    plan_hash=calculated,
                    plan_contract_version=plan.contract_version,
                    operation_id=operation_id,
                    ordinal=ordinal,
                    dependency_ids=getattr(operation, "depends_on", ()),
                    adapter_id=operation.adapter_id,
                    capability_id=capability,
                    prepared_operation_hash=operation.prepared_operation_hash,
                    target_type=operation.target.target_type,
                    target_id=operation.target.target_id,
                    attempt_id=attempt_id,
                    request_id=task.execution_request_id,
                    idempotency_key=f"{task.idempotency_key}:{ordinal}",
                    complete_lock_request_hash=self._complete_lock_hash(
                        plan, tuple(complete_requests)
                    ),
                    approval_bundle_hash=approval_hash,
                    selective_hold_keys=holds,
                    provider_dependency_key=(
                        (
                            "home_assistant:core"
                            if plan.operation
                            is ChangeOperation.SET_INPUT_BOOLEAN_STATE
                            else f"addon:{operation.authoritative_provider_slug}"
                        )
                        if plan.contract_version == 3
                        else None
                    ),
                    provider_identity_evidence_hash=(
                        operation.provider_identity_evidence_hash
                        if plan.contract_version == 3
                        else None
                    ),
                )
            )
            self._prepared_cache[child_id] = operation
        child_ids = [item["child_id"] for item in declarations]
        self._mark_task_authority(task, sequence_hash, child_ids)
        self.children.initialize_task_sequence(
            task=task,
            task_repository=self.service.task_repository,
            declarations=declarations,
            sequence_hash=sequence_hash,
        )
        self._sequence_lock_cache[task.task_id] = tuple(complete_requests)
        self.service._task_audit(task, "execution_ownership_claimed", "success")
        return task, tuple(prepared), tuple(complete_requests)

    async def _load_prepared(
        self,
        plan: Any,
        task: Any,
        *,
        readback_child_id: str | None = None,
    ):
        declarations = self.children.declarations_for_task(task.task_id)
        if not declarations:
            raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        readback_only = readback_child_id is not None
        selected = None
        readback_record = None
        if readback_only:
            selected = next(
                (
                    item
                    for item in declarations
                    if item["child_id"] == readback_child_id
                ),
                None,
            )
            readback_record = (
                None
                if selected is None
                else self.children.get(selected["child_id"])
            )
            if (
                readback_record is None
                or readback_record.dispatch_intent is None
                or readback_record.dispatch_count != 1
            ):
                raise GovernanceError(ErrorCode.POLICY_SNAPSHOT_MISMATCH)
        approval_hash = declarations[0]["approval_bundle_hash"]
        provider_identity = None
        if plan.contract_version == 3:
            provider_key = declarations[0]["provider_dependency_key"]
            provider_identity = (
                {
                    "slug": HELPER_STATE_PROVIDER_SLUG,
                    "evidence_hash": stable_hash(
                        helper_state_provider_evidence()
                    ),
                }
                if plan.operation is ChangeOperation.SET_INPUT_BOOLEAN_STATE
                else self._dashboard_provider_identity(plan)
                if plan.operation is ChangeOperation.UPDATE_DASHBOARD
                else {
                    "slug": provider_key.split(":", 1)[1],
                    "evidence_hash": declarations[0][
                        "provider_identity_evidence_hash"
                    ],
                }
            )
            if declarations[0]["provider_identity_evidence_hash"] != (
                provider_identity["evidence_hash"]
            ):
                raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
            if all(
                (record := self.children.get(item["child_id"])) is None
                or record.dispatch_intent is None
                for item in declarations
            ):
                current_identity = (
                    provider_identity
                    if plan.operation
                    in {
                        ChangeOperation.SET_INPUT_BOOLEAN_STATE,
                        ChangeOperation.UPDATE_DASHBOARD,
                    }
                    else await self._provider_identity()
                )
                if current_identity != provider_identity:
                    raise GovernanceError(
                        ErrorCode.OPERATIONAL_PROVIDER_UNAVAILABLE,
                        details={"reason": "provider_lock_identity_changed"},
                    )
        prepared, requests, sequence_hash = await self._prepare(
            plan,
            task,
            approval_hash,
            provider_identity,
            readback_only=readback_only,
        )
        if sequence_hash != task.legacy_projection.get("sequence_hash"):
            raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        for declaration, operation in zip(declarations, prepared, strict=True):
            if declaration["prepared_operation_hash"] != operation.prepared_operation_hash:
                raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
            self._prepared_cache[declaration["child_id"]] = operation
        if readback_only:
            assert selected is not None and readback_record is not None
            operation = prepared[selected["operation_ordinal"]]
            self._require_readback_binding(
                plan=plan,
                declaration=selected,
                operation=operation,
                complete_requests=tuple(requests),
                record=readback_record,
            )
        self._sequence_lock_cache[task.task_id] = tuple(requests)
        return tuple(prepared), tuple(requests)

    @staticmethod
    def _complete_lock_hash(plan: Any, requests: tuple[Any, ...]) -> str:
        if plan.contract_version in {1, 2}:
            return lock_set_hash(requests)
        return canonical_hash(
            [
                {"key": item.key, "mode": item.mode.value}
                for item in requests
            ]
        )

    @staticmethod
    def _prepared_provider_binding(operation: Any) -> tuple[str, str]:
        provider_operation = getattr(operation, "provider_operation", None)
        provider_arguments_hash = getattr(
            operation, "provider_arguments_hash", None
        )
        descriptor = getattr(operation, "provider_descriptor", None)
        if descriptor is not None:
            provider_operation = getattr(descriptor, "operation", None)
            provider_arguments_hash = getattr(
                descriptor, "arguments_hash", None
            )
        try:
            validate_identifier(
                provider_operation, field_name="provider_operation"
            )
            validate_sha256(
                provider_arguments_hash,
                field_name="provider_arguments_hash",
            )
        except (TypeError, ValueError):
            raise GovernanceError(
                ErrorCode.POLICY_SNAPSHOT_MISMATCH
            ) from None
        return provider_operation, provider_arguments_hash

    def _require_readback_binding(
        self,
        *,
        plan: Any,
        declaration: dict[str, Any],
        operation: Any,
        complete_requests: tuple[Any, ...],
        record: Any,
    ) -> None:
        intent = record.dispatch_intent
        if intent is None or record.dispatch_count != 1:
            raise GovernanceError(ErrorCode.POLICY_SNAPSHOT_MISMATCH)
        provider_operation, provider_arguments_hash = (
            self._prepared_provider_binding(operation)
        )
        expected_tokens = tuple(
            sorted(
                (item.key, item.mode.value) for item in complete_requests
            )
        )

        def token_binding(tokens: Any) -> tuple[tuple[str, str], ...]:
            if not isinstance(tokens, list):
                raise GovernanceError(ErrorCode.POLICY_SNAPSHOT_MISMATCH)
            try:
                result = tuple(
                    sorted((item["key"], item["mode"]) for item in tokens)
                )
            except (KeyError, TypeError):
                raise GovernanceError(
                    ErrorCode.POLICY_SNAPSHOT_MISMATCH
                ) from None
            if len(result) != len(set(result)):
                raise GovernanceError(ErrorCode.POLICY_SNAPSHOT_MISMATCH)
            return result

        if (
            declaration["plan_contract_version"] != plan.contract_version
            or intent["request_id"] != declaration["request_id"]
            or record.identity["request_id"] != declaration["request_id"]
            or intent["provider_operation"] != provider_operation
            or intent["provider_arguments_hash"] != provider_arguments_hash
            or declaration["complete_lock_request_hash"]
            != self._complete_lock_hash(plan, complete_requests)
            or token_binding(record.lock_tokens) != expected_tokens
            or token_binding(intent["lock_tokens"]) != expected_tokens
        ):
            raise GovernanceError(ErrorCode.POLICY_SNAPSHOT_MISMATCH)

    def _validate_sequence_state(
        self, task: Any, *, health_data: dict[str, Any] | None = None
    ) -> tuple[tuple[dict[str, Any], ...], tuple[Any | None, ...]]:
        declarations = (
            self.children.declarations_for_task(task.task_id) if health_data is None
            else tuple(health_data["manifests"][task.task_id]["declarations"])
        )
        if (
            not declarations
            or [item["operation_ordinal"] for item in declarations]
            != list(range(len(declarations)))
            or len({item["operation_id"] for item in declarations})
            != len(declarations)
            or tuple(task.legacy_projection.get("child_execution_ids", ()))
            != tuple(item["child_id"] for item in declarations)
        ):
            raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        known: set[str] = set()
        records: list[Any | None] = []
        prior_all_verified = True
        active = 0
        for declaration in declarations:
            dependencies = set(declaration["operation_dependency_ids"])
            if not dependencies.issubset(known):
                raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
            record = (self.children.get(declaration["child_id"]) if health_data is None
                      else health_data["records"][declaration["child_id"]])
            if record is not None:
                record.execution_class()
                if not prior_all_verified:
                    raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
                if not record.terminal:
                    active += 1
                if (
                    record.normalized_outcome == "succeeded_verified"
                    and (
                        (
                            record.dispatch_intent is None
                            and not (
                                record.dispatch_count == 0
                                and record.preflight_completed
                                and any(
                                    event.get("event_type")
                                    == "preflight_noop_verified"
                                    for event in record.events
                                )
                            )
                        )
                        or (
                            record.dispatch_intent is not None
                            and record.dispatch_count != 1
                        )
                    )
                ):
                    raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
                prior_all_verified = (
                    record.terminal
                    and record.normalized_outcome == "succeeded_verified"
                )
            else:
                prior_all_verified = False
            records.append(record)
            known.add(declaration["operation_id"])
        if active > 1:
            raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        return declarations, tuple(records)

    @staticmethod
    def _merge_lock_mode(
        values: dict[str, LockMode], key: str, mode: LockMode
    ) -> None:
        if mode == LockMode.EXCLUSIVE or key not in values:
            values[key] = mode

    def _legacy_plan_lock_modes(
        self, plan: Any, provider_keys: tuple[str, ...]
    ) -> dict[str, LockMode]:
        values: dict[str, LockMode] = {}
        if plan.contract_version == 2:
            for operation in plan.operations:
                resource = operation.helper_type or operation.resource_type
                self._merge_lock_mode(
                    values,
                    resource_lock_key(resource, operation.target_id),
                    LockMode.EXCLUSIVE,
                )
                self._merge_lock_mode(
                    values, f"reload:{resource}", LockMode.SHARED
                )
            self._merge_lock_mode(
                values, "home_assistant:core", LockMode.SHARED
            )
            return values
        if plan.contract_version == 1 and plan.operation in {
            ChangeOperation.CREATE_AUTOMATION,
            ChangeOperation.UPDATE_AUTOMATION,
        }:
            self._merge_lock_mode(
                values,
                resource_lock_key("automation", plan.target_id),
                LockMode.EXCLUSIVE,
            )
            self._merge_lock_mode(
                values, "reload:automation", LockMode.SHARED
            )
            self._merge_lock_mode(
                values, "home_assistant:core", LockMode.SHARED
            )
            return values
        if plan.contract_version != 3:
            return values
        self._merge_lock_mode(
            values,
            "home_assistant:core",
            (
                LockMode.EXCLUSIVE
                if plan.operation == ChangeOperation.RESTART_HOME_ASSISTANT
                else LockMode.SHARED
            ),
        )
        for key in provider_keys:
            self._merge_lock_mode(values, key, LockMode.SHARED)
        if plan.operation == ChangeOperation.CREATE_FULL_BACKUP:
            self._merge_lock_mode(
                values, "backup:local_full_backup", LockMode.EXCLUSIVE
            )
        elif plan.operation == ChangeOperation.CONTROLLED_RELOAD:
            self._merge_lock_mode(
                values, f"reload:{plan.target_id}", LockMode.EXCLUSIVE
            )
        elif plan.operation == ChangeOperation.RESTART_ADDON:
            self._merge_lock_mode(
                values, f"addon:{plan.target_id}", LockMode.EXCLUSIVE
            )
        elif plan.operation == ChangeOperation.SET_INPUT_BOOLEAN_STATE:
            self._merge_lock_mode(
                values,
                resource_lock_key("input_boolean", plan.target_id),
                LockMode.EXCLUSIVE,
            )
            self._merge_lock_mode(
                values,
                "reload:input_boolean",
                LockMode.SHARED,
            )
        return values

    def _has_active_legacy_conflict(
        self, current_requests: tuple[Any, ...]
    ) -> bool:
        current = {item.key: item.mode for item in current_requests}
        provider_keys = tuple(
            item.key
            for item in current_requests
            if LockScope.PROVIDER in item.scopes
        )
        for task in self.service.task_repository.list():
            if (
                task.state in TERMINAL_TASK_STATES
                or task.legacy_projection.get("execution_authority")
                == F3_EXECUTION_AUTHORITY
            ):
                continue
            plan = self.service._load(task.plan_id)
            if not self.is_covered_plan(plan):
                continue
            legacy = self._legacy_plan_lock_modes(plan, provider_keys)
            if any(
                key in legacy
                and (
                    mode == LockMode.EXCLUSIVE
                    or legacy[key] == LockMode.EXCLUSIVE
                )
                for key, mode in current.items()
            ):
                self._emit_f3_audit_event(
                    {
                        "event_type": "legacy_active_task_conflict",
                        "task_id": _ACTIVE_F3_CHILD.get(),
                    }
                )
                return True
        return False

    async def _consume_approval(
        self, plan: Any, task: Any, declaration: dict[str, Any]
    ) -> None:
        self._require_execution_ready()
        authoritative = self.service._load(plan.plan_id)
        if self.service.plan_hash(authoritative) != declaration["plan_hash"]:
            raise GovernanceError(ErrorCode.APPROVAL_HASH_MISMATCH)
        reference = self.children.runtime(declaration["child_id"])[
            "approval_consumption_reference"
        ]
        if authoritative.approval.state == ApprovalState.CONSUMED:
            sibling_references = [
                self.children.runtime(item["child_id"])["approval_consumption_reference"]
                for item in self.children.declarations_for_task(task.task_id)
            ]
            valid = reference or next((item for item in sibling_references if item), None)
            if valid is None:
                # The plan/task projection is the durable idempotency witness if
                # the process died after consuming approval but before updating
                # the first child envelope.
                if (
                    authoritative.apply_request_id != task.execution_request_id
                    or task.legacy_projection.get("sequence_hash")
                    != self.children.manifest_for_task(task.task_id)["sequence_hash"]
                    or task.approval_reference.get("approval_state")
                    != ApprovalState.CONSUMED.value
                ):
                    raise GovernanceError(ErrorCode.APPROVAL_ALREADY_CONSUMED)
                valid = {
                    "plan_hash": declaration["plan_hash"],
                    "policy_decision_hash": authoritative.approval.policy_decision_hash,
                    "approval_bundle_hash": declaration["approval_bundle_hash"],
                    "public_task_id": task.task_id,
                    "sequence_hash": task.legacy_projection["sequence_hash"],
                    "consumed_at": authoritative.approval.consumed_at,
                }
            if valid.get("sequence_hash") != task.legacy_projection["sequence_hash"]:
                raise GovernanceError(ErrorCode.APPROVAL_ALREADY_CONSUMED)
            if reference is None:
                self.children.update_runtime(
                    declaration["child_id"],
                    changes={"approval_consumption_reference": valid},
                )
            self._ensure_public_approval_consumed(task, authoritative)
            return
        self.service._require_policy_snapshot(authoritative)
        self.service._require_dispatch_approval(authoritative)
        if _approval_bundle_hash(authoritative) != declaration["approval_bundle_hash"]:
            raise GovernanceError(ErrorCode.APPROVAL_HASH_MISMATCH)
        self.service._consume_approval_bundle(authoritative)
        authoritative.status = PlanStatus.APPLYING
        authoritative.execution_outcome = "dispatching"
        authoritative.apply_request_id = task.execution_request_id
        self.service._record(authoritative, "external_approval_consumed", "success")
        consumed = {
            "plan_hash": declaration["plan_hash"],
            "policy_decision_hash": authoritative.policy_decision.policy_decision_hash,
            "approval_bundle_hash": declaration["approval_bundle_hash"],
            "public_task_id": task.task_id,
            "sequence_hash": task.legacy_projection["sequence_hash"],
            "consumed_at": authoritative.approval.consumed_at,
        }
        self.children.update_runtime(
            declaration["child_id"],
            changes={"approval_consumption_reference": consumed},
        )
        self._ensure_public_approval_consumed(task, authoritative)

    async def _consume_approval_counted(
        self, plan: Any, task: Any, declaration: dict[str, Any]
    ) -> None:
        try:
            await self._consume_approval(plan, task, declaration)
        except (ExecutionStorageError, ExecutionTaskStorageError, LockStorageError):
            self._readiness_fault("execution_storage")
            self._approval_consumption_failures += 1
            raise
        except Exception:
            self._approval_consumption_failures += 1
            raise

    def _ensure_public_approval_consumed(self, task: Any, plan: Any) -> None:
        """Persist the schema-1 approval witness before durable F3 intent."""

        with self.children.public_projection_transaction():
            current = self.service._load_task(task.task_id)
            if current.approval_reference.get("approval_state") == "consumed":
                return
            self.service._record_task_event(
                current,
                "approval_consumed",
                changes={
                    "approval_reference": self.service._task_approval_reference(
                        plan
                    )
                },
            )

    def _executor(self, evidence_seconds: int) -> SharedOperationExecutor:
        return SharedOperationExecutor(
            lock_store=self.locks,
            execution_repository=self.children,
            lock_timing=PRODUCTION_LOCK_TIMING,
            executor_timing=ExecutorTiming(evidence_seconds, 120, 32, 32),
            event_sink=self._emit_f3_audit_event,
            now=self.service.now,
        )

    async def _retry_bundle_check(self, plan, task, current_order, final):
        from ..governance.configuration_eligibility import bound_proof
        from ..governance.resources import resource_fingerprint, resource_identity_matches

        proof = bound_proof(plan.operations)
        if proof is None:
            return "retry_bundle_proof_invalid"
        declarations = self.children.declarations_for_task(task.task_id)
        for index, operation in enumerate(plan.operations):
            if index < current_order:
                record = self.children.get(declarations[index]["child_id"])
                if record is None or not record.terminal or record.normalized_outcome != "succeeded_verified":
                    return "retry_bundle_prefix_unverified"
            proposed = final or index < current_order
            expected = operation.proposed_config_hash if proposed else operation.current_state_fingerprint
            adapter = self.registry.adapter(f"update_{operation.resource_type}_configuration")
            try:
                current = await adapter.gateway.read(operation.resource_type, operation.target_id)
                if current is None or not resource_identity_matches(operation.resource_type, operation.target_id, current):
                    return "retry_bundle_identity_mismatch"
                if resource_fingerprint(operation.resource_type, current) != expected:
                    return "retry_bundle_state_mismatch"
            except Exception:
                return "retry_bundle_read_unavailable"
        return None

    async def _execute_child(
        self,
        plan: Any,
        task: Any,
        declaration: dict[str, Any],
        prepared: Any,
        complete_requests: tuple[Any, ...],
        *,
        readback_only: bool = False,
    ):
        adapter = self.registry.adapter(declaration["capability_id"])
        record = self.children.get(declaration["child_id"])
        if (record is not None and not record.terminal and record.dispatch_intent is None
                and any(inverse_eligibility.markers(op) for op in plan.operations)
                and datetime.fromisoformat(record.claim_expires_at) <= self.service.now()
                and any(item.task_id == declaration["child_id"] for item in self.locks.records())):
            # A crash during acquisition/transfer is still zero-dispatch. Stop
            # this inverse without changing the old owner before tokenless
            # reconciliation can prove it. Never release and start a fresh set.
            self.children.cancel(declaration["child_id"], now=self.service.now(),
                diagnostic_codes=("inverse_interrupted_acquisition",),
                expected_claim_generation=record.claim_generation, require_expired_claim=True)
            record = self.children.get(declaration["child_id"])
        if readback_only:
            if (
                record is None
                or record.dispatch_intent is None
                or record.dispatch_count != 1
            ):
                raise GovernanceError(ErrorCode.POLICY_SNAPSHOT_MISMATCH)
            self._require_readback_binding(
                plan=plan,
                declaration=declaration,
                operation=prepared,
                complete_requests=complete_requests,
                record=record,
            )
        evidence_seconds = int(
            getattr(prepared, "evidence_deadline_seconds", 120)
        )
        executor = self._executor(evidence_seconds)
        if plan.contract_version in {1, 2}:
            from ..governance.configuration_eligibility import bound_proof, markers

            if plan.contract_version == 2 and any(inverse_eligibility.markers(op) for op in plan.operations):
                context = self._inverse_lock_context(declaration["child_id"])
                if context is None or declaration not in context[2]:
                    raise LockOwnershipError("inverse acquisition declaration changed")
                executor.execution_repository = _InverseAcquisitionRepository(
                    self.children, declaration)
                order = declaration["operation_ordinal"]
                adapter = _RetryBundleAdapter(
                    adapter, complete_requests,
                    lambda final: self._rollback_bundle_check(plan, task, order, final),
                    order == len(plan.operations) - 1,
                    success_code="garage_inverse_bundle_verified",
                )
            elif plan.contract_version == 2 and any(markers(op) for op in plan.operations):
                if bound_proof(plan.operations) is None:
                    raise GovernanceError(ErrorCode.POLICY_SNAPSHOT_MISMATCH)
                order = declaration["operation_ordinal"]
                adapter = _RetryBundleAdapter(
                    adapter, complete_requests,
                    lambda final: self._retry_bundle_check(plan, task, order, final),
                    order == len(plan.operations) - 1,
                )
            else:
                adapter = _SequenceLockAdapter(adapter, complete_requests)
        else:
            if getattr(prepared, "capability_id", None) == DASHBOARD_CAPABILITY_ID:
                if (
                    executor.executor_timing.post_dispatch_evidence_seconds
                    != prepared.evidence_deadline_seconds
                ):
                    raise GovernanceError(
                        ErrorCode.EXECUTION_TASK_STORAGE_ERROR
                    )
            else:
                validate_operational_executor_timing(executor, prepared)
        adapter = _LegacyConflictAdapter(
            adapter, self._has_active_legacy_conflict
        )
        if self.core_runtime is not None:
            adapter = _CoreVerificationAdapter(adapter, self.core_runtime)
        if readback_only:
            # Keep the reduced recovery protocol outermost so none of the
            # normal sequence/conflict wrappers can re-expose preflight or
            # dispatch after durable intent has consumed write authority.
            adapter = _ReadbackOnlyRecoveryAdapter(adapter)
        identity = ExecutionIdentity(
            task_id=declaration["child_id"],
            plan_id=plan.plan_id,
            attempt_id=declaration["attempt_id"],
            request_id=declaration["request_id"],
            owner_id=f"runtime-{uuid.uuid4().hex}",
        )
        token = _ACTIVE_F3_CHILD.set(declaration["child_id"])
        try:
            result = await executor.execute(
                adapter=adapter,
                prepared=prepared,
                identity=identity,
                approval_consumption=lambda: self._consume_approval_counted(
                    plan, task, declaration
                ),
                dispatch_authority=(
                    _CoreDispatchAuthorityGuard(
                        self.core_runtime, plan.created_at
                    )
                    if self.core_runtime is not None and not readback_only
                    else None
                ),
            )
            if "lock_storage_failure" in result.diagnostic_codes:
                self._readiness_fault("execution_storage")
            return result
        finally:
            _ACTIVE_F3_CHILD.reset(token)

    def _project_dispatch(self, task: Any, record: Any, declaration: dict[str, Any]) -> None:
        if record.dispatch_intent is None:
            return
        if any(
            attempt.get("child_execution_id") == declaration["child_id"]
            for attempt in task.provider_attempts
        ):
            return
        committed = record.dispatch_intent["committed_at"]
        attempts = [
            *task.provider_attempts,
            {
                "attempt": len(task.provider_attempts) + 1,
                "provider": declaration["adapter_id"],
                "attempted_at": committed,
                "response_received": record.provider_response_received,
                "child_execution_id": declaration["child_id"],
                "operation_id": declaration["operation_id"],
                "dispatch_count": 1,
            },
        ]
        changes = {"provider_attempts": attempts}
        if task.dispatched_at is None:
            changes.update(
                {
                    "dispatched_at": committed,
                    "maximum_post_dispatch_deadline": (
                        datetime.fromisoformat(committed) + timedelta(hours=24)
                    ).isoformat(),
                }
            )
        self.service._record_task_event(
            task,
            "dispatch_attempted",
            new_state=(
                ExecutionTaskState.DISPATCHING
                if task.state == ExecutionTaskState.PREFLIGHT
                else None
            ),
            changes=changes,
        )

    def _project(self, plan: Any, task: Any) -> None:
        with self.children.public_projection_transaction():
            current = self.service._load_task(task.task_id)
            if current.state not in TERMINAL_TASK_STATES:
                self._project_locked(plan, current)
        if any(inverse_eligibility.markers(op) for op in plan.operations):
            self._settle_inverse_sequence_locks(task)

    def _enter_public_preflight(self, task: Any) -> Any:
        """Idempotently project pre-intent authority into schema-1 state."""

        with self.children.public_projection_transaction():
            current = self.service._load_task(task.task_id)
            if current.state == ExecutionTaskState.CREATED:
                self.service._record_task_event(
                    current,
                    "preflight_started",
                    new_state=ExecutionTaskState.PREFLIGHT,
                    changes={"started_at": self.service._timestamp()},
                )
                current = self.service._load_task(task.task_id)
            return current

    def _project_locked(
        self, plan: Any, task: Any, *, require_audit_complete: bool = False
    ) -> None:
        terminal_recovery = task.state in TERMINAL_TASK_STATES

        def terminal_event(
            event_type: str, *, new_state: ExecutionTaskState,
            changes: dict[str, Any], result_status: str = "success",
        ) -> None:
            if task.state in TERMINAL_TASK_STATES:
                # Reconcile a known child fault without rewriting terminal
                # task history. Exact persisted projection must already agree;
                # only unfinished plan projection/audit may be retried.
                if task.state != new_state or any(
                    getattr(task, key) != value
                    for key, value in changes.items() if key != "completed_at"
                ):
                    raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
                return
            self.service._record_task_event(
                task, event_type, new_state=new_state, changes=changes,
                result_status=result_status,
            )

        declarations, records = self._validate_sequence_state(task)
        if terminal_recovery and not (
            all(record is not None and record.normalized_outcome == "succeeded_verified"
                for record in records)
            or any(record is not None and record.terminal
                   and record.normalized_outcome != "succeeded_verified"
                   for record in records)
        ):
            raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        task = self.service._load_task(task.task_id)
        for declaration, record in zip(declarations, records, strict=True):
            if record is not None:
                audited = self._audit_record_events(declaration, record)
                if require_audit_complete and not audited:
                    raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
                self._project_dispatch(task, record, declaration)
                task = self.service._load_task(task.task_id)
        outcomes = [None if item is None else item.normalized_outcome for item in records]
        completed = [
            declarations[index]["operation_id"]
            for index, outcome in enumerate(outcomes)
            if outcome == "succeeded_verified"
        ]
        child_projection = [
            {
                "child_execution_id": declaration["child_id"],
                "operation_id": declaration["operation_id"],
                "ordinal": declaration["operation_ordinal"],
                "state": "not_started" if record is None else record.state,
                "normalized_outcome": None if record is None else record.normalized_outcome,
                "dispatch_count": 0 if record is None else record.dispatch_count,
                "terminal": False if record is None else record.terminal,
            }
            for declaration, record in zip(declarations, records, strict=True)
        ]
        summary = {
            **task.verification_summary,
            "f3_model": F3_RUNTIME_MODEL,
            "sequence_hash": task.legacy_projection["sequence_hash"],
            "completed_operation_ids": completed,
            "children": child_projection,
        }
        if all(outcome == "succeeded_verified" for outcome in outcomes):
            if task.state == ExecutionTaskState.DISPATCHING:
                self.service._record_task_event(
                    task, "observation_completed", new_state=ExecutionTaskState.OBSERVING,
                    changes={"verification_summary": summary},
                )
                task = self.service._load_task(task.task_id)
            if task.state == ExecutionTaskState.OBSERVING:
                self.service._record_task_event(
                    task, "verification_started", new_state=ExecutionTaskState.VERIFYING,
                    changes={"verification_summary": summary},
                )
                task = self.service._load_task(task.task_id)
            terminal_event(
                "task_completed",
                new_state=ExecutionTaskState.SUCCEEDED_VERIFIED,
                changes={
                    "completed_at": self.service._timestamp(),
                    "terminal_outcome": "succeeded_verified",
                    "verification_summary": {**summary, "status": "verified"},
                },
            )
            plan.status = PlanStatus.APPLIED
            plan.applied_at = (
                task.completed_at if terminal_recovery else self.service._timestamp()
            )
            plan.execution_outcome = "succeeded_verified"
        else:
            active = next(
                (item for item in records if item is not None and not item.terminal), None
            )
            failed_index = next(
                (index for index, item in enumerate(records) if item is not None and item.terminal and item.normalized_outcome != "succeeded_verified"),
                None,
            )
            if active is not None:
                if not any(
                    item is not None and item.dispatch_intent is not None
                    for item in records
                ):
                    # Preflight progress is not evidence of dispatch. In
                    # particular, do not replace the approved plan status
                    # while its owner awaits Core authority before consuming
                    # approval. Terminal outcomes are still projected below.
                    return
                if task.state == ExecutionTaskState.DISPATCHING:
                    self.service._record_task_event(
                        task, "observation_pending", new_state=ExecutionTaskState.OBSERVING,
                        changes={"verification_summary": {**summary, "status": "pending"}},
                    )
                plan.status = PlanStatus.VERIFICATION_REQUIRED
                plan.execution_outcome = "observing"
            elif failed_index is not None:
                failed = records[failed_index]
                assert failed is not None
                if not completed and failed.dispatch_intent is None:
                    terminal_event(
                        "preflight_failed", new_state=ExecutionTaskState.FAILED_PRE_DISPATCH,
                        changes={
                            "completed_at": self.service._timestamp(),
                            "terminal_outcome": "failed_pre_dispatch",
                            "verification_summary": summary,
                        },
                        result_status="failure",
                    )
                    plan.status = PlanStatus.FAILED
                    plan.execution_outcome = "failed_pre_dispatch"
                elif not completed and failed.normalized_outcome != "manual_review_required":
                    if task.state == ExecutionTaskState.PREFLIGHT:
                        self.service._record_task_event(
                            task, "dispatch_history_recovered",
                            new_state=ExecutionTaskState.DISPATCHING,
                            changes={},
                        )
                        task = self.service._load_task(task.task_id)
                    terminal_event(
                        "task_failed_post_dispatch",
                        new_state=ExecutionTaskState.FAILED_POST_DISPATCH,
                        changes={
                            "completed_at": self.service._timestamp(),
                            "terminal_outcome": "failed_post_dispatch",
                            "verification_summary": {
                                **summary, "status": "failed_post_dispatch"
                            },
                        },
                        result_status="failure",
                    )
                    plan.status = PlanStatus.FAILED
                    plan.execution_outcome = "failed_post_dispatch"
                else:
                    if task.state == ExecutionTaskState.PREFLIGHT:
                        self.service._record_task_event(
                            task, "dispatch_history_recovered", new_state=ExecutionTaskState.DISPATCHING,
                            changes={},
                        )
                        task = self.service._load_task(task.task_id)
                    terminal_event(
                        "manual_review_required",
                        new_state=ExecutionTaskState.MANUAL_REVIEW_REQUIRED,
                        changes={
                            "completed_at": self.service._timestamp(),
                            "terminal_outcome": "manual_review_required",
                            "manual_review_reason": "f3_sequence_incomplete",
                            "verification_summary": {**summary, "status": "manual_review_required"},
                        },
                        result_status="partial",
                    )
                    plan.status = PlanStatus.VERIFICATION_FAILED
                    plan.execution_outcome = "manual_review_required"
        if plan.contract_version == 2:
            by_id = {item["operation_id"]: item for item in child_projection}
            for operation in plan.operations:
                child = by_id[operation.operation_id]
                if child["normalized_outcome"] == "succeeded_verified":
                    operation.execution_status = StepExecutionStatus.APPLIED_VERIFIED
                    record = self.children.get(child["child_execution_id"])
                    operation.post_apply_fingerprint = record.evidence.get(
                        "resulting_state_fingerprint"
                    )
                    operation.snapshot = operation.snapshot or None
                elif child["normalized_outcome"] is not None:
                    operation.execution_status = StepExecutionStatus.FAILED
        self.service._save(plan)

    async def apply(self, plan: Any, expected_plan_hash: str) -> dict[str, Any]:
        if not self.readiness_state()["execution_ready"]:
            raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        try:
            return await self._apply(plan, expected_plan_hash)
        except ExecutionRecordCorrupt:
            self._readiness_fault("storage_read")
            raise
        except (ExecutionStorageError, ExecutionTaskStorageError, LockStorageError):
            # A successful read audit cannot certify a failed durable write.
            # Keep this fault until runtime reconstruction; do not replay apply.
            self._readiness_fault("execution_storage")
            raise
        except GovernanceError as exc:
            if (exc.code == ErrorCode.EXECUTION_TASK_STORAGE_ERROR
                    and self.readiness_state()["execution_ready"]):
                self._readiness_fault("execution_storage")
            raise
        except DashboardFoundationError:
            # A deterministic per-plan/provider refusal is not evidence that
            # shared F3 execution storage failed. Preserve its exact failure.
            raise
        except Exception:
            # Adapter/provider refusals and unclassified failures are local to
            # this apply. Only the storage failures handled above establish a
            # persistent execution-storage fault. Preserve the original error;
            # do not turn a rejected plan into a restart-required global outage.
            raise

    async def _apply(self, plan: Any, expected_plan_hash: str) -> dict[str, Any]:
        existing = self.service.task_repository.get_for_plan(plan.plan_id)
        if existing is None:
            try:
                task, prepared, requests = await self._initialize(
                    plan, expected_plan_hash
                )
            except (ExecutionStorageError, ExecutionTaskStorageError) as exc:
                winner = self.service.task_repository.get_for_plan(plan.plan_id)
                if (
                    winner is None
                    or winner.plan_hash != self.service.plan_hash(plan)
                    or winner.legacy_projection.get("execution_authority")
                    != F3_EXECUTION_AUTHORITY
                ):
                    self._readiness_fault(
                        "storage_read" if isinstance(exc, ExecutionRecordCorrupt)
                        else "execution_storage"
                    )
                    raise GovernanceError(
                        ErrorCode.EXECUTION_TASK_STORAGE_ERROR
                    ) from None
                existing = winner
                task = winner
                prepared, requests = await self._load_prepared(plan, task)
        else:
            task = existing
            if task.plan_hash != self.service.plan_hash(plan):
                raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
            prepared, requests = await self._load_prepared(plan, task)
        task = self._enter_public_preflight(task)
        if task.state in TERMINAL_TASK_STATES:
            return self.service._terminal_task_apply_result(task, plan)
        self.service._active_task_ids_by_plan[plan.plan_id] = task.task_id
        joined_active_execution = False
        try:
            declarations, _records = self._validate_sequence_state(task)
            for declaration, operation in zip(declarations, prepared, strict=True):
                record = self.children.get(declaration["child_id"])
                if record is not None and record.terminal:
                    if record.normalized_outcome != "succeeded_verified":
                        break
                    continue
                result = await self._execute_child(
                    plan, task, declaration, operation, requests
                )
                if result.duplicate_execution:
                    self._emit_f3_audit_event(
                        {
                            "event_type": "duplicate_apply_prevented",
                            "task_id": declaration["child_id"],
                        }
                    )
                if result.duplicate_execution and not result.terminal:
                    joined_active_execution = True
                    break
                if not result.terminal or result.outcome != "succeeded_verified":
                    break
            plan = self.service._load(plan.plan_id)
            task = self.service._load_task(task.task_id)
            if not joined_active_execution:
                self._project(plan, task)
        except PreIntentRetryRequired as exc:
            self._readiness_fault("recovery_child:" + declaration["child_id"])
            raise GovernanceError(
                ErrorCode.EXECUTION_TASK_STORAGE_ERROR,
                details={"reason": exc.diagnostic_code},
            ) from None
        finally:
            self.service._active_task_ids_by_plan.pop(plan.plan_id, None)
        task = self.service._load_task(task.task_id)
        plan = self.service._load(plan.plan_id)
        return {
            "status": "applied" if task.state == ExecutionTaskState.SUCCEEDED_VERIFIED else "accepted",
            "task_id": task.task_id,
            "task_state": task.state.value,
            "task_reused": existing is not None,
            "provider_dispatch_occurred": bool(task.provider_attempts),
            "redispatch_performed": False,
            "execution_task": self.service._public_task(task),
            "plan": self.service._public(plan, include_configs=False),
        }

    def _operational_authority(self, operation: Any) -> OperationalAuthoritySnapshot:
        declaration = self.children.declaration(operation.child_execution_id)
        task = self.service.task_repository.get(operation.public_task_id)
        runtime = self.children.runtime(operation.child_execution_id)
        if task is None:
            raise ValueError("public task is missing")
        reference = runtime["approval_consumption_reference"]
        audit_state = (
            {} if self.service.audit is None else self.service.audit.state()
        )
        audit_healthy = bool(
            audit_state.get("target_configured")
            and audit_state.get("write_failures") == 0
        )
        return OperationalAuthoritySnapshot(
            plan_id=declaration["plan_id"],
            plan_hash=declaration["plan_hash"],
            public_task_id=declaration["public_task_id"],
            child_execution_id=declaration["child_id"],
            active_child_execution_id=declaration["child_id"],
            operation=operation.operation,
            target_type=declaration["target_type"],
            target_id=declaration["target_id"],
            prepared_authority_model=OPERATIONAL_PREPARED_AUTHORITY_MODEL,
            prepared_operation_hash=declaration["prepared_operation_hash"],
            policy_decision_hash=operation.policy_decision_hash,
            approval_bundle_hash=declaration["approval_bundle_hash"],
            authorization_evidence_status=(
                "valid" if reference is None or reference.get("plan_hash") == declaration["plan_hash"] else "invalid"
            ),
            elevated_acknowledgement_bound=(
                operation.policy_class != "elevated_admin"
                or (
                    "elevated_risk_acknowledgement"
                    not in (
                        task.approval_reference.get(
                            "required_acknowledgements"
                        )
                        or []
                    )
                )
                or task.approval_reference.get("same_principal_confirmed")
                is True
            ),
            governance_storage_status="healthy",
            audit_storage_status=("healthy" if audit_healthy else "unavailable"),
            execution_task_storage_status="healthy",
            f3_execution_storage_status="healthy",
            f3_lock_storage_status="healthy",
            restart_reconciliation_compatible=True,
        )

    async def cancel(self, task: Any) -> dict[str, Any]:
        with self.children.public_projection_transaction():
            current = self.service._load_task(task.task_id)
            return await self._cancel_locked(current)

    async def _cancel_locked(self, task: Any) -> dict[str, Any]:
        declarations = self.children.declarations_for_task(task.task_id)
        records = [self.children.get(item["child_id"]) for item in declarations]
        if task.state not in {
            ExecutionTaskState.CREATED,
            ExecutionTaskState.PREFLIGHT,
        } or any(
            item is not None and item.dispatch_intent is not None
            for item in records
        ):
            raise GovernanceError(ErrorCode.CANCELLATION_NOT_PERMITTED_AFTER_DISPATCH)
        plan = self.service._load(task.plan_id)
        prepared, _requests = await self._load_prepared(plan, task)
        # Only the first unresolved child can be active in an ordered sequence.
        # Materializing that exact child before cancellation closes the race in
        # which another process might otherwise create it after a public-only
        # cancellation record.
        for declaration, operation, record in zip(
            declarations, prepared, records, strict=True
        ):
            if record is not None and record.terminal:
                if record.normalized_outcome == "succeeded_verified":
                    continue
                break
            identity = ExecutionIdentity(
                task_id=declaration["child_id"],
                plan_id=plan.plan_id,
                attempt_id=declaration["attempt_id"],
                request_id=declaration["request_id"],
                owner_id=f"cancel-{uuid.uuid4().hex}",
            )
            if record is None:
                try:
                    self.children.claim(
                        identity=identity,
                        prepared=operation,
                        timing=ExecutorTiming(
                            int(getattr(operation, "evidence_deadline_seconds", 120)),
                            120,
                            32,
                            32,
                        ),
                        now=self.service.now(),
                    )
                except DuplicateExecutionActive:
                    pass
            if not self.children.cancel(
                declaration["child_id"], now=self.service.now()
            ):
                raise GovernanceError(
                    ErrorCode.CANCELLATION_NOT_PERMITTED_AFTER_DISPATCH
                )
            break
        self.service._record_task_event(
            task,
            "task_cancelled_pre_dispatch",
            new_state=ExecutionTaskState.CANCELLED_PRE_DISPATCH,
            changes={
                "completed_at": self.service._timestamp(),
                "terminal_outcome": "cancelled_pre_dispatch",
            },
        )
        return {
            "status": "cancelled_pre_dispatch",
            "provider_dispatch_occurred": False,
            "task": self.service._public_task(task),
        }

    def _inverse_lock_context(self, child_id, *, health_data=None):
        """Bounded durable authority; no callback/cache is a retention receipt."""
        declaration = (self.children.declaration(child_id) if health_data is None
                       else health_data["envelopes"][child_id]["declaration"])
        task_id = declaration["public_task_id"]
        if health_data is not None and task_id in health_data["inverse_contexts"]:
            return health_data["inverse_contexts"][task_id]
        if health_data is None or health_data.get("plans") is None:
            plan = self.service._load_for_projection(declaration["plan_id"])
        else:
            plan = health_data["plans"].get(declaration["plan_id"])
            if plan is None:
                raise LockOwnershipError("inverse source plan is missing")
            # Service collection has validated safety, but projection failures
            # must never grant an inverse lock-retention context.
            if plan.policy_decision is not None:
                self.service._require_projection_policy_snapshot(plan)
        if not any(inverse_eligibility.markers(op) for op in plan.operations):
            if health_data is not None:
                health_data["inverse_contexts"][task_id] = None
            return None
        proof = inverse_eligibility.bound(plan)
        task = (self.service._load_task(task_id) if health_data is None
                else health_data["parents"][task_id])
        if (proof is None or task.plan_hash != self.service.plan_hash(plan)
                or task.legacy_projection.get("execution_authority") != F3_EXECUTION_AUTHORITY):
            raise LockOwnershipError("inverse sequence binding unavailable")
        declarations, records = self._validate_sequence_state(task, health_data=health_data)
        manifest = (self.children.manifest_for_task(task.task_id) if health_data is None
                    else health_data["manifests"][task.task_id])
        if (len(declarations) != len(plan.operations)
                or manifest["sequence_hash"] != task.legacy_projection.get("sequence_hash")):
            raise LockOwnershipError("inverse sequence manifest changed")
        for op, d, record in zip(plan.operations, declarations, records, strict=True):
            if (d["plan_hash"] != task.plan_hash or d["plan_id"] != plan.plan_id
                    or d["public_task_id"] != task.task_id
                    or d["request_id"] != task.execution_request_id
                    or d["operation_id"] != op.operation_id
                    or d["operation_dependency_ids"] != op.depends_on
                    or d["target_type"] != op.resource_type or d["target_id"] != op.target_id
                    or d["capability_id"] != f"update_{op.resource_type}_configuration"):
                raise LockOwnershipError("inverse child declaration changed")
            if record is not None and (
                    record.identity["task_id"] != d["child_id"]
                    or record.identity["plan_id"] != plan.plan_id
                    or record.identity["attempt_id"] != d["attempt_id"]
                    or record.identity["request_id"] != d["request_id"]
                    or record.prepared_operation_hash != d["prepared_operation_hash"]
                    or record.operation != d["capability_id"]
                    or record.target != {"target_type": op.resource_type, "target_id": op.target_id}):
                raise LockOwnershipError("inverse child receipt changed")
        result = plan, task, declarations, records
        if health_data is not None:
            health_data["inverse_contexts"][task_id] = result
        return result

    def _retain_inverse_locks(self, child_id):
        context = self._inverse_lock_context(child_id)
        if context is None:
            return False
        _plan, _task, declarations, records = context
        index = next(i for i, d in enumerate(declarations) if d["child_id"] == child_id)
        record = records[index]
        if record is None:
            raise LockOwnershipError("inverse lock owner receipt unavailable")
        # Only durable terminal settlement allows release. An active successor
        # (including its token-persistence crash window) must keep ownership.
        if not record.terminal:
            return True
        if record.normalized_outcome != "succeeded_verified":
            return record.normalized_outcome == "manual_review_required"
        if index == len(records) - 1:
            return False
        successor = records[index + 1]
        return not (successor is not None and successor.terminal
                    and successor.dispatch_intent is None
                    and successor.normalized_outcome != "succeeded_verified")

    @staticmethod
    def _observed_lock_handle(records, timing):
        first = records[0]
        return LockHandle(
            LockOwner(first.owner_id, first.task_id, first.plan_id, first.operation_id, first.attempt_id),
            tuple(LockToken(item.key, item.generation, item.mode) for item in records),
            first.acquired_at, min(item.lease_expires_at for item in records), timing)

    def _inverse_lock_acquire(self, requests, *, owner, timing, now):
        candidate = self.service.repository.get(owner.plan_id)
        if candidate is None or not any(inverse_eligibility.markers(op) for op in candidate.operations):
            return None
        context = self._inverse_lock_context(owner.task_id)
        if context is None:
            return None
        plan, task, declarations, records = context
        index = next(i for i, d in enumerate(declarations) if d["child_id"] == owner.task_id)
        if index == 0:
            return None
        d, record = declarations[index], records[index]
        if (record is not None and record.terminal and record.dispatch_intent is None
                and record.normalized_outcome == "cancelled_pre_dispatch"):
            raise LockWaitCancelled("inverse successor was cancelled before transfer")
        if (record is None or record.terminal or record.dispatch_intent is not None
                or record.identity["owner_id"] != owner.owner_id
                or owner.plan_id != plan.plan_id or owner.operation_id != d["capability_id"]
                or owner.attempt_id != d["attempt_id"]
                or datetime.fromisoformat(record.claim_expires_at) <= now):
            raise LockOwnershipError("inverse successor has no exact live claim")
        previous = records[index - 1]
        previous_d = declarations[index - 1]
        if (previous is None or not previous.terminal
                or previous.normalized_outcome != "succeeded_verified"
                or previous.dispatch_intent is None or previous.dispatch_count != 1
                or previous.evidence.get("resulting_state_fingerprint")
                != plan.operations[index - 1].proposed_config_hash):
            raise LockOwnershipError("inverse predecessor is not durably verified")
        try:
            self.require_rollback_source(plan)
        except GovernanceError as exc:
            raise LockOwnershipError("inverse source evidence unavailable at transfer") from exc
        typed_requests = tuple(LockRequest(item.key, tuple(LockScope(s) for s in item.scopes),
                                          LockMode(item.mode), item.reason_codes) for item in requests)
        if (self._complete_lock_hash(plan, typed_requests) != d["complete_lock_request_hash"]
                or d["complete_lock_request_hash"] != previous_d["complete_lock_request_hash"]):
            raise LockOwnershipError("inverse complete lock contract changed")
        observed = tuple(item for item in self.locks.records()
                         if item.task_id == previous_d["child_id"])
        if not observed or any(not self._lock_record_matches_execution_authority(previous_d, previous, item)
                               for item in observed):
            raise LockOwnershipError("inverse predecessor ownership unavailable")
        handle = self._observed_lock_handle(observed, timing)
        if len(previous.lock_tokens) != len(handle.tokens):
            raise LockOwnershipError("inverse predecessor ownership incomplete")
        return self.locks.transfer_complete(requests, predecessor=handle,
            expected_records=observed, owner=owner, timing=timing, now=self.service.now,
            valid_until=datetime.fromisoformat(record.claim_expires_at))

    def _settle_inverse_sequence_locks(self, task):
        """Exact terminal cleanup, including a transferred but unrecorded set.

        No live/uncertain owner is released. Expired pre-intent ownership may
        terminate the partial inverse; it never becomes permission to reacquire.
        """
        declarations = self.children.declarations_for_task(task.task_id)
        if not declarations or self._inverse_lock_context(declarations[0]["child_id"]) is None:
            return
        all_locks = self.locks.records()
        for d in declarations:
            observed = tuple(item for item in all_locks if item.task_id == d["child_id"])
            if not observed or self._retain_inverse_locks(d["child_id"]):
                continue
            record = self.children.get(d["child_id"])
            if any(item.conflict_hold for item in observed):
                continue
            if record.lock_tokens:
                if (len(observed) != len(record.lock_tokens) or any(
                        not self._lock_record_matches_execution_authority(d, record, item) for item in observed)):
                    raise LockOwnershipError("inverse settlement ownership changed")
            else:
                # The existing unrecorded-set primitive binds exact acquisition
                # tokens before releasing. It requires expired zero-dispatch
                # authority and checks the immutable full lock hash itself.
                if (record.dispatch_intent is not None or not record.terminal
                        or any(datetime.fromisoformat(item.lease_expires_at) > self.service.now()
                               for item in observed)):
                    continue
                def settle(current_declaration, current_record, persist):
                    if current_declaration != d:
                        raise LockOwnershipError("inverse settlement declaration changed")
                    identity = current_record.execution_identity()
                    return self.locks.settle_unrecorded(
                        owner=LockOwner(identity.owner_id, identity.task_id, identity.plan_id,
                                        current_record.operation, identity.attempt_id),
                        expected_records=observed, complete_request_hash=d["complete_lock_request_hash"],
                        execution_created_at=current_record.created_at,
                        execution_updated_at=current_record.updated_at, persist_tokens=persist,
                        timing=PRODUCTION_LOCK_TIMING, now=self.service.now())
                self.children.settle_unrecorded_locks(d["child_id"], now=self.service.now(), settle=settle)
                continue
            self.locks.release(self._observed_lock_handle(observed, PRODUCTION_LOCK_TIMING))

    def _rollback_source_snapshot(self, source):
        try:
            return inverse_eligibility.source_snapshot(self, source)
        except (GovernanceError, ExecutionStorageError, ExecutionTaskStorageError,
                ValueError, KeyError, TypeError, AttributeError):
            # Missing/corrupt source evidence is not authority to repair a source
            # record or to manufacture a household-wide persistence fault.
            raise GovernanceError(
                ErrorCode.ROLLBACK_NOT_AVAILABLE,
                details={"reason": "garage_inverse_source_unverified"},
            ) from None

    def require_rollback_source(self, plan):
        proof = inverse_eligibility.bound(plan)
        if proof is None:
            raise GovernanceError(ErrorCode.POLICY_SNAPSHOT_MISMATCH)
        try:
            source = self.service.repository.get(proof["source"]["plan_id"])
            if (source is None or source.rollback.request_id != plan.plan_id
                    or self._rollback_source_snapshot(source) != proof["source"]):
                raise GovernanceError(ErrorCode.ROLLBACK_NOT_AVAILABLE)
        except Exception as exc:
            # Read-only source refusal. New plan/child/audit write failures are
            # outside this catch and retain their existing fail-closed behavior.
            raise GovernanceError(
                ErrorCode.ROLLBACK_NOT_AVAILABLE,
                details={"reason": "garage_inverse_source_binding_unavailable"},
            ) from exc
        return source, proof

    def _garage_inverse_result(self, plan):
        source, proof = self.require_rollback_source(plan)
        selected = proof["selected"]
        return {
            "status": "rollback_plan_created" if proof["complete"] else "rollback_partial_plan_created",
            "source_plan_id": source.plan_id, "rollback_plan_id": plan.plan_id,
            "approval_required": (plan.status == PlanStatus.AWAITING_APPROVAL
                                  and plan.approval.state in {ApprovalState.REQUIRED, ApprovalState.EXTERNAL_PENDING}),
            "plan_hash": self.service.plan_hash(plan),
            "operations_to_restore": [op.operation_id for op in plan.operations],
            "operations_excluded": [op.operation_id for i, op in enumerate(source.operations) if i not in selected],
            "provider_dispatch_occurred": False,
        }

    def persist_rollback_plan(self, plan, context):
        # The existing short public-projection transaction serializes only
        # durable plan/link issuance. No awaits, HA reads or dispatch under it.
        with self.children.public_projection_transaction():
            source = self.service.repository.get(context["snapshot"]["plan_id"])
            if source is None or self._rollback_source_snapshot(source) != context["snapshot"]:
                raise GovernanceError(ErrorCode.ROLLBACK_NOT_AVAILABLE)
            existing = self.service.repository.get(plan.plan_id)
            if existing is not None:
                proof = inverse_eligibility.bound(existing)
                if proof is None or proof["source"] != context["snapshot"]:
                    raise GovernanceError(ErrorCode.POLICY_SNAPSHOT_MISMATCH)
                plan = existing
            proof = inverse_eligibility.bound(plan)
            if source.rollback.request_id != plan.plan_id:
                source.rollback.available = proof["complete"]
                source.rollback.status = "governed_plan_created" if proof["complete"] else "partial_governed_plan_created"
                source.rollback.requested_at = self.service._timestamp()
                source.rollback.request_id = plan.plan_id
                self.service._record(source, "rollback_plan_created", "success")
            if existing is None:
                self.service._record(plan, "change_plan_created", "success")
            # Existing cached prohibited records remain byte-for-byte unchanged.
            return plan

    async def _create_garage_inverse(self, source, expected_plan_hash):
        if expected_plan_hash and expected_plan_hash != self.service.plan_hash(source):
            raise GovernanceError(ErrorCode.APPROVAL_HASH_MISMATCH)
        self._require_execution_ready()
        snapshot = self._rollback_source_snapshot(source)
        if snapshot["prefix"] == 0:
            raise GovernanceError(ErrorCode.ROLLBACK_NOT_AVAILABLE)
        task = self.service.task_repository.get(snapshot["task_id"])
        # Reconstruct with the unchanged shipped forward adapter/sequence
        # contract; declarations alone cannot assert a prepared payload hash.
        prepared, requests = await self._load_prepared(source, task)
        declarations = self.children.declarations_for_task(task.task_id)
        for i in range(snapshot["prefix"]):
            self._require_readback_binding(
                plan=source, declaration=declarations[i], operation=prepared[i],
                complete_requests=requests, record=self.children.get(declarations[i]["child_id"]),
            )
        plan_id = inverse_eligibility.proof_id(snapshot)
        existing = self.service.repository.get(plan_id)
        if existing is not None:
            if inverse_eligibility.bound(existing) is None:
                raise GovernanceError(ErrorCode.POLICY_SNAPSHOT_MISMATCH)
            # Recover the single plan-before-backlink crash without changing
            # approval, history, payload, expiration or execution identity.
            self.persist_rollback_plan(existing, {"snapshot": snapshot})
            return self._garage_inverse_result(existing)
        if source.rollback.request_id:
            cached = self.service.repository.get(source.rollback.request_id)
            if cached is None and source.rollback.request_id != plan_id:
                raise GovernanceError(ErrorCode.ROLLBACK_NOT_AVAILABLE)
            if cached is not None and cached.policy_decision.policy_class.value != "prohibited":
                # Never silently replace a usable historical partial attempt.
                return {
                    "status": "rollback_partial_plan_created",
                    "source_plan_id": source.plan_id, "rollback_plan_id": cached.plan_id,
                    "approval_required": cached.status != PlanStatus.APPLIED,
                    "plan_hash": self.service.plan_hash(cached),
                }
        from ..governance.resources import resource_identity_matches
        observed = []
        for op in source.operations:
            current = await self.service._read_configuration_resource(op.resource_type, op.target_id)
            if current is None or not resource_identity_matches(op.resource_type, op.target_id, current):
                raise GovernanceError(ErrorCode.ROLLBACK_NOT_AVAILABLE)
            observed.append(resource_fingerprint(op.resource_type, current))
        prefix = snapshot["prefix"]
        expected = [op.proposed_config_hash if i < prefix else op.current_state_fingerprint
                    for i, op in enumerate(source.operations)]
        complete = observed == expected
        selected = [i for i in reversed(range(prefix)) if complete or (
            i != 0 and observed[i] == source.operations[i].proposed_config_hash)]
        if not selected:
            raise GovernanceError(ErrorCode.ROLLBACK_NOT_AVAILABLE)
        operations = []
        for i in selected:
            op = source.operations[i]
            operations.append({
                "operation_id": f"restore_{i}_{op.operation_id}"[:64],
                "resource_type": op.resource_type, "action": "update", "target_id": op.target_id,
                "proposed_config": deepcopy(op.current_config),
                "depends_on": [] if not operations else [operations[-1]["operation_id"]],
            })
        context = {"plan_id": plan_id, "snapshot": snapshot, "observed": observed,
                   "selected": selected, "complete": complete}
        created = await self.service._create_configuration_plan(
            title="Restore exact verified garage configuration prefix",
            description=inverse_eligibility.DISCLOSURE,
            operations=operations, _rollback_context=context,
            caller_context={"rollback_source_plan_id": source.plan_id,
                            "rollback_model": inverse_eligibility.MODEL,
                            "excluded_operation_ids": [op.operation_id for i, op in enumerate(source.operations) if i not in selected]},
        )
        return self._garage_inverse_result(self.service._load(created["plan_id"]))

    async def _rollback_bundle_check(self, plan, task, current_order, final):
        try:
            source, proof = self.require_rollback_source(plan)
            source_task = self.service.task_repository.get(proof["source"]["task_id"])
            await self._load_prepared(source, source_task)
        except (GovernanceError, ExecutionStorageError, ExecutionTaskStorageError,
                ValueError, KeyError, TypeError, AttributeError):
            return "garage_inverse_source_unverified"
        selected = proof["selected"]
        declarations = self.children.declarations_for_task(task.task_id)
        expected = list(proof["observed"])
        for ordinal, index in enumerate(selected):
            if ordinal < current_order:
                record = self.children.get(declarations[ordinal]["child_id"])
                if (record is None or not record.terminal or record.normalized_outcome != "succeeded_verified"
                        or record.dispatch_intent is None or record.dispatch_count != 1):
                    return "garage_inverse_prefix_unverified"
            if final or ordinal < current_order:
                expected[index] = source.operations[index].current_state_fingerprint
        from ..governance.resources import resource_identity_matches
        for i, op in enumerate(source.operations):
            adapter = self.registry.adapter(f"update_{op.resource_type}_configuration")
            try:
                current = await adapter.gateway.read(op.resource_type, op.target_id)
                if current is None or not resource_identity_matches(op.resource_type, op.target_id, current):
                    return "garage_inverse_identity_mismatch"
                if resource_fingerprint(op.resource_type, current) != expected[i]:
                    return "garage_inverse_bundle_drift"
            except Exception:
                return "garage_inverse_read_unavailable"
        return None

    async def create_rollback_plan(
        self, source_plan: Any, expected_plan_hash: str
    ) -> dict[str, Any]:
        """Create a separately governed reverse update plan; never execute it."""

        from ..governance.configuration_eligibility import bound_proof

        forward = bound_proof(source_plan.operations)
        if forward is not None and forward["kind"] == "caller_owned_retry":
            return await self._create_garage_inverse(source_plan, expected_plan_hash)
        retry_source = forward is not None
        calculated = self.service.plan_hash(source_plan)
        if expected_plan_hash and expected_plan_hash != calculated:
            raise GovernanceError(ErrorCode.APPROVAL_HASH_MISMATCH)
        if source_plan.contract_version == 3:
            raise GovernanceError(ErrorCode.ROLLBACK_NOT_AVAILABLE)
        if source_plan.rollback.request_id:
            existing = self.service.repository.get(source_plan.rollback.request_id)
            if existing is not None:
                prohibited = retry_source and existing.policy_decision.policy_class.value == "prohibited"
                partial = retry_source and not prohibited and not source_plan.rollback.available
                return {
                    "status": ("rollback_unavailable" if prohibited else
                               "rollback_partial_plan_created" if partial else "rollback_plan_created"),
                    "source_plan_id": source_plan.plan_id,
                    "rollback_plan_id": existing.plan_id,
                    "approval_required": not prohibited and existing.status != PlanStatus.APPLIED,
                    "plan_hash": self.service.plan_hash(existing),
                }
        task = self.service.task_repository.get_for_plan(source_plan.plan_id)
        if task is None or task.state not in {
            ExecutionTaskState.SUCCEEDED_VERIFIED,
            ExecutionTaskState.MANUAL_REVIEW_REQUIRED,
            ExecutionTaskState.FAILED_POST_DISPATCH,
        }:
            raise GovernanceError(ErrorCode.ROLLBACK_NOT_AVAILABLE)
        operations: list[dict[str, Any]] = []
        excluded: list[str] = []
        if source_plan.contract_version == 2:
            declarations = self.children.declarations_for_task(task.task_id)
            records = {
                item["operation_id"]: self.children.get(item["child_id"])
                for item in declarations
            }
            for operation in reversed(source_plan.operations):
                record = records.get(operation.operation_id)
                resulting_fingerprint = (
                    record.evidence.get("resulting_state_fingerprint")
                    if record is not None
                    else operation.post_apply_fingerprint
                )
                previously_verified = (
                    record is not None
                    and record.normalized_outcome == "succeeded_verified"
                ) or (
                    record is None
                    and operation.execution_status
                    == StepExecutionStatus.APPLIED_VERIFIED
                )
                if (
                    operation.action != "update"
                    or operation.current_config is None
                    or not previously_verified
                    or resulting_fingerprint is None
                ):
                    excluded.append(operation.operation_id)
                    continue
                resource_type = operation.resource_type
                helper_type = operation.helper_type
                resolved_type = self.service._resolved_resource_type(
                    resource_type, helper_type
                )
                current = await self.service._read_configuration_resource(
                    resolved_type, operation.target_id
                )
                if resource_fingerprint(
                    resolved_type, current
                ) != resulting_fingerprint:
                    excluded.append(operation.operation_id)
                    continue
                suffix = stable_hash(
                    {"source_operation_id": operation.operation_id}
                )[:12]
                operation_id = f"rollback_{operation.operation_id[:42]}_{suffix}"
                operations.append(
                    {
                        "operation_id": operation_id,
                        "resource_type": resource_type,
                        "helper_type": helper_type,
                        "action": "update",
                        "target_id": operation.target_id,
                        "proposed_config": deepcopy(operation.current_config),
                        "depends_on": (
                            [operations[-1]["operation_id"]] if operations else []
                        ),
                    }
                )
        elif (
            source_plan.operation == ChangeOperation.UPDATE_AUTOMATION
            and source_plan.current_config is not None
        ):
            operations.append(
                {
                    "operation_id": "rollback_legacy_automation",
                    "resource_type": "automation",
                    "action": "update",
                    "target_id": source_plan.target_id,
                    "proposed_config": deepcopy(source_plan.current_config),
                    "depends_on": [],
                }
            )
        if not operations:
            raise GovernanceError(ErrorCode.ROLLBACK_NOT_AVAILABLE)
        # Remove the helper-only field for non-helper operations so the public
        # configuration schema remains byte-for-byte compatible.
        for operation in operations:
            if operation.get("resource_type") != "helper":
                operation.pop("helper_type", None)
        created = await self.service.create_configuration_plan(
            title=f"Rollback governed change {source_plan.plan_id}"[:160],
            description=(
                "Separately governed reverse update using exact prior configuration "
                "evidence from a verified F3 execution."
            ),
            operations=operations,
            caller_context={
                "rollback_source_plan_id": source_plan.plan_id,
                "rollback_model": "f3-governed-rollback-plan-v1",
                "excluded_operation_ids": excluded[:8],
            },
        )
        rollback_plan = self.service._load(created["plan_id"])
        prohibited = retry_source and rollback_plan.policy_decision.policy_class.value == "prohibited"
        partial = bool(retry_source and excluded)
        source_plan.rollback.available = not (prohibited or partial)
        source_plan.rollback.status = (
            "unavailable_policy_prohibited" if prohibited else
            "partial_governed_plan_created" if partial else "governed_plan_created"
        )
        source_plan.rollback.requested_at = self.service._timestamp()
        source_plan.rollback.request_id = rollback_plan.plan_id
        self.service._record(source_plan, "rollback_plan_created", "success")
        return {
            "status": ("rollback_unavailable" if prohibited else
                       "rollback_partial_plan_created" if partial else "rollback_plan_created"),
            "source_plan_id": source_plan.plan_id,
            "rollback_plan_id": rollback_plan.plan_id,
            "approval_required": not prohibited,
            "plan_hash": self.service.plan_hash(rollback_plan),
            "operations_to_restore": [item["operation_id"] for item in operations],
            "operations_excluded": excluded,
            "provider_dispatch_occurred": False,
        }

    def decorate_task(self, task: Any) -> dict[str, Any]:
        try:
            return self._decorate_task(task)
        except Exception:
            self._readiness_fault("task_projection:" + task.task_id)
            raise

    def _decorate_task(self, task: Any) -> dict[str, Any]:
        value = self.service._public_task(task)
        if task.legacy_projection.get("execution_authority") != F3_EXECUTION_AUTHORITY:
            return value
        rows = self._public_child_projection(task)
        value["f3_children"] = [item["detail"] for item in rows]
        if self._is_terminal_zero_dispatch_parent(task):
            # The schema-1 verification summary is historical durable
            # evidence. Normalize only its child rows from the same canonical
            # projection used by f3_children, leaving the parent's causal
            # state, terminal outcome, status, and last_error untouched.
            value["verification_summary"] = {
                **value["verification_summary"],
                "children": [item["summary"] for item in rows],
            }
        return value

    def _public_child_projection(self, task: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        lock_records = self.locks.records()
        for declaration in self.children.declarations_for_task(task.task_id):
            record = self.children.get(declaration["child_id"])
            runtime = self.children.runtime(declaration["child_id"])
            state, outcome = self._orphaned_child_state(task, record)
            dispatch_count = 0 if record is None else record.dispatch_count
            rows.append(
                {
                    "detail": {
                        "child_execution_id": declaration["child_id"],
                        "operation_id": declaration["operation_id"],
                        "ordinal": declaration["operation_ordinal"],
                        "prepared_operation_hash": declaration[
                            "prepared_operation_hash"
                        ],
                        "state": state,
                        "normalized_outcome": outcome,
                        "dispatch_count": dispatch_count,
                        "lock_recovery": self._lock_recovery_diagnostics(
                            declaration, record, runtime, lock_records
                        ),
                        "evidence_deadline": (
                            None
                            if record is None
                            or record.dispatch_intent is None
                            else record.dispatch_intent["evidence_deadline"]
                        ),
                        "selective_hold_keys": declaration[
                            "selective_hold_keys"
                        ],
                    },
                    "summary": {
                        "child_execution_id": declaration["child_id"],
                        "operation_id": declaration["operation_id"],
                        "ordinal": declaration["operation_ordinal"],
                        "state": state,
                        "normalized_outcome": outcome,
                        "dispatch_count": dispatch_count,
                        "terminal": state == "terminal",
                    },
                }
            )
        return rows

    def _lock_recovery_diagnostics(self, declaration, record, runtime, lock_records):
        """Bounded projection; never an authority to release or dispatch."""
        related = [item for item in lock_records if item.task_id == declaration["child_id"]]
        now = self.service.now()
        active = sum(item.conflict_hold or datetime.fromisoformat(item.lease_expires_at) > now
                     for item in related)
        missing = bool(related and record is not None and not record.lock_tokens)
        result = runtime["reconciliation_result"]
        category = {
            "unrecorded_locks_authority_unresolved": "ownership_proof_unavailable",
            "unrecorded_locks_storage_error": "storage_error",
            "unrecorded_locks_unexpected_error": "unexpected_error",
        }.get(result)
        return {
            "retained_lock_count": len(related), "active_lock_count": active,
            "expired_lock_count": len(related) - active,
            "token_persistence": "missing" if missing else (
                "persisted" if record is not None and record.lock_tokens else "none_recorded"),
            "waiting_for_claim_expiry": bool(missing and datetime.fromisoformat(record.claim_expires_at) > now),
            "last_reconciliation_at": runtime["last_reconciliation_at"],
            "last_failure_category": category,
            "requires_manual_intervention": category == "ownership_proof_unavailable",
            "next_retry_at": runtime["next_eligible_at"],
        }

    def reconciliation_items(self) -> list[dict[str, Any]]:
        try:
            return self._reconciliation_items()
        except Exception:
            self._readiness_fault("reconciliation")
            raise

    def _reconciliation_items(self) -> list[dict[str, Any]]:
        steps = self._reconciliation_steps()
        while True:
            try:
                next(steps)
            except StopIteration as complete:
                return complete.value

    def _reconciliation_steps(self, health_data=None):
        items = []
        lock_records = (self.locks.records() if health_data is None
                        else health_data["lock_records"])
        lock_task_ids = {item.task_id for item in lock_records}
        declarations = (
            (item for task in self.service.task_repository.list()
             if task.legacy_projection.get("execution_authority") == F3_EXECUTION_AUTHORITY
             for item in self.children.declarations_for_task(task.task_id))
            if health_data is None else
            (item for task in health_data["parents"].values()
             if task.legacy_projection.get("execution_authority") == F3_EXECUTION_AUTHORITY
             for item in health_data["manifests"].get(task.task_id, {}).get("declarations", ()))
        )
        for declaration in sorted(declarations, key=lambda item: (
            item["public_task_id"], item["operation_ordinal"]
        )):
            yield
            child_id = declaration["child_id"]
            record = (self.children.get(child_id) if health_data is None
                      else health_data["records"][child_id])
            runtime = (self.children.runtime(child_id) if health_data is None
                       else health_data["envelopes"][child_id]["runtime"])
            parent = (self.service.task_repository.get(declaration["public_task_id"])
                      if health_data is None else health_data["parents"][declaration["public_task_id"]])
            cleanup_pending = self._orphan_cleanup_pending(
                parent=parent,
                declaration=declaration,
                record=record,
                runtime=runtime,
                lock_records=lock_records,
            )
            if (
                record is not None
                and record.terminal
                and not runtime["selective_hold_tokens"]
                and not cleanup_pending
                and not (declaration["child_id"] in lock_task_ids
                         and self._inverse_lock_context(declaration["child_id"], health_data=health_data) is not None)
            ):
                continue
            if record is None and parent is not None:
                # A child that never started, under a terminal parent that
                # never dispatched, is not pending reconciliation work.
                projected_state, _ = self._orphaned_child_state(parent, record)
                if (
                    projected_state == "terminal"
                    and declaration["child_id"] not in lock_task_ids
                ):
                    continue
            items.append(
                {
                    "child_id": declaration["child_id"],
                    "public_task_id": declaration["public_task_id"],
                    "plan_id": declaration["plan_id"],
                    "operation_id": declaration["operation_id"],
                    "ordinal": declaration["operation_ordinal"],
                    "target": f"{declaration['target_type']}:{declaration['target_id']}",
                    "prepared_hash": declaration["prepared_operation_hash"],
                    "state": (
                        projected := self._orphaned_child_state(
                            parent, record
                        )
                        if parent is not None
                        else ("not_started", None)
                    )[0],
                    "normalized_outcome": projected[1],
                    "intent_timestamp": None if record is None or record.dispatch_intent is None else record.dispatch_intent["committed_at"],
                    "evidence_deadline": None if record is None or record.dispatch_intent is None else record.dispatch_intent["evidence_deadline"],
                    "dispatch_count": 0 if record is None else record.dispatch_count,
                    "provider_response_received": False if record is None else record.provider_response_received,
                    "observation_count": 0 if record is None else record.observation_attempts,
                    "verification_count": 0 if record is None else record.verification_attempts,
                    "selective_hold_keys": declaration["selective_hold_keys"],
                    "hold_tokens": runtime["selective_hold_tokens"],
                    "record_generation": runtime["record_generation"],
                    "reason_codes": [] if record is None else [
                        value
                        for event in record.events[-4:]
                        for value in event.get("diagnostic_codes", [])
                    ][:16],
                    "last_reconciliation_at": runtime["last_reconciliation_at"],
                    "reconciliation_result": runtime["reconciliation_result"],
                    "last_readback_summary": runtime["last_readback_summary"],
                    "lock_recovery": self._lock_recovery_diagnostics(
                        declaration, record, runtime, lock_records
                    ),
                }
            )
            if len(items) >= 100:
                break
        return items

    async def _read_only_reconciliation(
        self,
        *,
        plan: Any,
        task: Any,
        declaration: dict[str, Any],
        record: Any,
    ) -> dict[str, Any]:
        if record.dispatch_intent is None:
            raise GovernanceError(ErrorCode.EXECUTION_TASK_INVALID_STATE)
        prepared, _requests = await self._load_prepared(
            plan,
            task,
            readback_child_id=declaration["child_id"],
        )
        operation = prepared[declaration["operation_ordinal"]]
        adapter = self.registry.adapter(declaration["capability_id"])
        adapter = _ReadbackOnlyRecoveryAdapter(adapter)
        token = _ACTIVE_F3_CHILD.set(declaration["child_id"])
        try:
            observation = await adapter.observe(operation, None)
            verification = await adapter.verify(operation, observation)
        finally:
            _ACTIVE_F3_CHILD.reset(token)
        outcome = getattr(verification.outcome, "value", verification.outcome)
        observed_hash = (
            getattr(verification, "resulting_state_fingerprint", None)
            or getattr(verification, "evidence_hash", None)
        )
        summary = {
            "status": str(outcome),
            "verified": getattr(verification, "verified", None) is True,
            "observed_hash": observed_hash,
            "checked_at": self.service.now().isoformat(),
        }
        self.children.update_runtime(
            declaration["child_id"],
            changes={"last_readback_summary": summary},
        )
        return summary

    def _audit_reconciliation_action(
        self,
        *,
        task: Any,
        declaration: dict[str, Any],
        action: str,
        authorized_principal: str,
        result: str,
    ) -> None:
        if self.service.audit is not None:
            self.service.audit.write(
                {
                    "event": "f3_private_reconciliation_action",
                    "request_id": declaration["request_id"],
                    "access": "write",
                    "operation_class": "f3_private_reconciliation",
                    "task_id": task.task_id,
                    "child_execution_id": declaration["child_id"],
                    "plan_id": declaration["plan_id"],
                    "operation_id": declaration["operation_id"],
                    "capability_identity": declaration["capability_id"],
                    "target_type": declaration["target_type"],
                    "target_id": declaration["target_id"],
                    "action": action,
                    "authority_hash": stable_hash(
                        {"principal": authorized_principal}
                    ),
                    "outcome": result,
                    "result_status": "success",
                    "fallback_occurred": False,
                    "fallback": "none",
                }
            )

    async def reconcile_child(
        self,
        *,
        child_id: str,
        action: str,
        record_generation: int,
        prepared_hash: str,
        hold_generation_binding: str,
        authorized_principal: str,
    ) -> dict[str, Any]:
        allowed = {
            "rerun_observation",
            "rerun_verification",
            "retain_hold",
            "release_hold_after_verified_resolution",
            "close_manual_review_unresolved",
            "create_governed_rollback_plan",
        }
        if action not in allowed:
            raise GovernanceError(ErrorCode.INVALID_REQUEST)
        if (
            not isinstance(authorized_principal, str)
            or not 1 <= len(authorized_principal) <= 180
            or not authorized_principal.startswith("home_assistant_admin_ingress:")
        ):
            raise GovernanceError(ErrorCode.AUTHENTICATION_FAILURE)
        declaration = self.children.declaration(child_id)
        runtime = self.children.runtime(child_id)
        if (
            runtime["record_generation"] != record_generation
            or declaration["prepared_operation_hash"] != prepared_hash
        ):
            raise GovernanceError(ErrorCode.EXECUTION_TASK_INVALID_STATE)
        hold_tokens = runtime["selective_hold_tokens"]
        expected_hold_generation_binding = ",".join(
            f"{item['key']}:{item['generation']}" for item in hold_tokens
        )
        if expected_hold_generation_binding != hold_generation_binding:
            raise GovernanceError(ErrorCode.EXECUTION_TASK_INVALID_STATE)
        plan = self.service._load_for_projection(declaration["plan_id"])
        task = self.service._load_task(declaration["public_task_id"])
        record = self.children.get(child_id)
        historical_policy = bool(
            plan.policy_decision is not None
            and plan.policy_decision.policy_version != POLICY_VERSION
        )
        if historical_policy and (
            record is None
            or record.dispatch_intent is None
            or action == "create_governed_rollback_plan"
        ):
            # Historical policy can retain only readback/reconciliation for
            # an already durable intent. It can never initialize a child,
            # dispatch another operation, or create new rollback authority.
            raise GovernanceError(ErrorCode.POLICY_SNAPSHOT_MISMATCH)
        if action in {"rerun_observation", "rerun_verification"}:
            if record is None or record.dispatch_intent is None:
                raise GovernanceError(ErrorCode.EXECUTION_TASK_INVALID_STATE)
            if record.terminal:
                await self._read_only_reconciliation(
                    plan=plan,
                    task=task,
                    declaration=declaration,
                    record=record,
                )
            else:
                prepared, requests = await self._load_prepared(
                    plan,
                    task,
                    readback_child_id=child_id,
                )
                await self._execute_child(
                    plan,
                    task,
                    declaration,
                    prepared[declaration["operation_ordinal"]],
                    requests,
                    readback_only=True,
                )
                self._project(plan, task)
            outcome = "read_only_reconciliation_completed"
        elif action == "retain_hold":
            if not hold_tokens:
                raise GovernanceError(ErrorCode.EXECUTION_TASK_INVALID_STATE)
            outcome = "hold_retained"
        elif action == "release_hold_after_verified_resolution":
            if record is None or not hold_tokens:
                raise GovernanceError(ErrorCode.EXECUTION_TASK_INVALID_STATE)
            summary = await self._read_only_reconciliation(
                plan=plan,
                task=task,
                declaration=declaration,
                record=record,
            )
            if not summary["verified"]:
                raise GovernanceError(ErrorCode.EXECUTION_TASK_INVALID_STATE)
            lock_owner = self._hold_owner(declaration, record, hold_tokens)
            authority = {
                "authority_hash": stable_hash(
                    {"principal": authorized_principal}
                ),
                "authorized_at": self.service.now().isoformat(),
                "reason_code": "verified_resolution",
                "evidence_hash": summary["observed_hash"] or stable_hash(summary),
                "tokens": hold_tokens,
            }
            self.children.update_runtime(
                child_id, changes={"hold_release_authority": authority}
            )
            self.locks.release_conflict_hold(
                owner=lock_owner,
                tokens=tuple(
                    LockToken(item["key"], item["generation"], item["mode"])
                    for item in hold_tokens
                ),
                reason_code="verified_resolution",
            )
            self.children.update_runtime(
                child_id,
                changes={
                    "selective_hold_tokens": [],
                    "hold_release_authority": None,
                },
            )
            outcome = "hold_released_after_verified_resolution"
        elif action == "create_governed_rollback_plan":
            result = await self.create_rollback_plan(
                plan, self.service.plan_hash(plan)
            )
            outcome = "governed_rollback_plan_created"
            self._audit_reconciliation_action(
                task=task,
                declaration=declaration,
                action=action,
                authorized_principal=authorized_principal,
                result=outcome,
            )
            self.service._task_audit(task, outcome, "success")
            return result
        else:
            if record is None or record.normalized_outcome != "manual_review_required":
                raise GovernanceError(ErrorCode.EXECUTION_TASK_INVALID_STATE)
            outcome = "manual_review_closed_unresolved_hold_retained"
        now = self.service.now().isoformat()
        self.children.update_runtime(
            child_id,
            changes={
                "last_reconciliation_at": now,
                "reconciliation_result": outcome,
            },
        )
        self._audit_reconciliation_action(
            task=task,
            declaration=declaration,
            action=action,
            authorized_principal=authorized_principal,
            result=outcome,
        )
        self.service._task_audit(task, outcome, "success")
        return {"status": outcome, "child_id": child_id}

    def _health_fence_sources(self):
        """Describe storage reads without acquiring their initial tokens."""
        return (
            (ErrorCode.CHANGE_PLAN_STORAGE_ERROR, lambda: (
                self.service.repository.generation, self.service.repository._directory_token())),
            (ErrorCode.EXECUTION_TASK_STORAGE_ERROR, self.service.task_repository._directory_token),
            (ErrorCode.EXECUTION_TASK_STORAGE_ERROR, lambda: (
                self.children._health_generation, HealthScanFence.stamp(self.children.root))),
            (ErrorCode.EXECUTION_TASK_STORAGE_ERROR, lambda: (
                HealthScanFence.stamp(self.locks.root), HealthScanFence.stamp(self.locks.state_path))),
        )

    def _health_fence(self) -> HealthScanFence:
        return HealthScanFence(self._health_fence_sources())

    async def async_health(self, *, tasks=None, plans=None, fence=None) -> dict[str, Any]:
        """Owner-loop deep diagnostic; the collection is discarded after use."""
        parent_repository = self.service.task_repository
        corruption_before = parent_repository.corruption_count
        try:
            fence = fence or self._health_fence()
            if tasks is None:
                tasks = await parent_repository.collect_health(
                    checkpoint=fence.check, watch=fence.watch)
            task_generation = parent_repository.generation
            data = await self.children.collect_health(fence)
            data["plans"] = plans
            data["parents"] = {task.task_id: task for task in tasks}
            data["inverse_contexts"] = {}
            for task in tasks:
                await asyncio.sleep(0)
                fence.check()
                if (task.legacy_projection.get("execution_authority") == F3_EXECUTION_AUTHORITY
                        and task.task_id not in data["manifests"]):
                    raise ExecutionStorageError("F3 parent manifest is missing")
            fence.watch(self.locks.state_path)
            data["lock_records"] = self.locks.records()
            steps = self._reconciliation_steps(data)
            while True:
                await asyncio.sleep(0)
                fence.check()
                if parent_repository.generation != task_generation:
                    fence.superseded()
                try:
                    next(steps)
                except StopIteration as complete:
                    data["reconciliation"] = complete.value
                    break
            await fence.verify_files()
            if parent_repository.generation != task_generation:
                fence.superseded()
            records = [record for record in data["records"].values() if record is not None]
            data["materialized"] = records
            data["child_health"] = {
                "status": "healthy",
                "record_count": sum(len(m["declarations"]) for m in data["manifests"].values()),
                "materialized_execution_count": len(records),
                "nonterminal_execution_count": sum(not record.terminal for record in records),
                "manual_review_count": sum(record.normalized_outcome == "manual_review_required" for record in records),
            }
            now = datetime.now(timezone.utc)
            locks = data["lock_records"]
            active = sum(self.locks._record_active(record, now) for record in locks)
            data["lock_health"] = self.locks.metrics.snapshot(
                current_active_lock_count=active,
                current_conflict_hold_count=sum(record.conflict_hold for record in locks),
                current_expired_lease_count=len(locks) - active,
            )
            result = self._health(collected=data)
            fence.check()
            return result
        except TaskHealthSnapshotSuperseded:
            HealthScanFence.superseded()
        except GovernanceError as exc:
            if exc.details.get("reason") != "health_snapshot_superseded":
                self._readiness_fault("health")
            raise
        except Exception:
            # The owner may have quarantined a truly malformed parent. That
            # known corruption is not reclassified as its own directory race.
            if fence is not None and parent_repository.corruption_count == corruption_before:
                fence.check_after_failure()
            self._readiness_fault("health")
            raise

    def health(self) -> dict[str, Any]:
        try:
            return self._health()
        except Exception:
            self._readiness_fault("health")
            raise

    def _health(self, *, collected=None) -> dict[str, Any]:
        child = self.children.health() if collected is None else collected["child_health"]
        lock = self.locks.snapshot() if collected is None else collected["lock_health"]
        registry = self.registry.health()
        holds = int(lock.get("current_conflict_hold_count", 0))
        task_navigation = (
            self.service.task_repository.navigation_metrics(**({"validated": True} if collected is not None else {}))
        )
        reconciliation = (self.reconciliation_items() if collected is None
                          else collected["reconciliation"])
        pending_reconciliation = bool(reconciliation)
        manual_recovery = any(item["lock_recovery"]["requires_manual_intervention"]
                              for item in reconciliation)
        status = "manual_intervention_required" if holds or manual_recovery else (
            "recovering"
            if child["nonterminal_execution_count"] or pending_reconciliation
            else "ready"
        )
        readiness = self.readiness_state()
        if not readiness["execution_ready"]:
            if readiness["status"] != "faulted":
                status = "recovering"
            elif not (holds or manual_recovery):
                status = "unavailable"
        return {
            "f3_model": F3_RUNTIME_MODEL,
            "status": status,
            "execution_ready": readiness["execution_ready"],
            "readiness": readiness,
            "adapter_registry_status": registry["status"],
            "registered_adapter_count": registry["registered_adapter_count"],
            "activated_capability_count": registry["activated_capability_count"],
            "adapter_registry_sha256": registry["registry_sha256"],
            "dashboard_capability_count": registry["dashboard_capability_count"],
            "execution_ownership_status": "ready",
            "public_child_projection_status": "ready",
            "lock_store_status": "ready",
            "execution_store_status": child["status"],
            "recovery_coordinator_status": (
                "unavailable" if readiness["status"] == "faulted"
                else "ready" if self._coordinator_initialized else "recovering"
            ),
            "last_recovery_sweep_at": self._last_sweep_at,
            "next_recovery_sweep_at": self._next_sweep_at,
            "nonterminal_execution_count": child["nonterminal_execution_count"],
            "observing_count": sum(item.state == "observation" for item in (
                self.children.list() if collected is None else collected["materialized"]
            )),
            "manual_review_count": child["manual_review_count"],
            "legacy_task_count": task_navigation["legacy_task_count"],
            "legacy_active_task_count": task_navigation[
                "legacy_active_task_count"
            ],
            "legacy_migration_count": 0,
            "active_conflict_hold_count": holds,
            "active_normal_lock_count": max(
                0, int(lock.get("current_active_lock_count", 0)) - holds
            ),
            "corrupt_record_count": int(lock.get("corrupted_records", 0)),
            "approval_consumption_failure_count": (
                self._approval_consumption_failures
            ),
            "lock_conflict_count": int(lock.get("conflicts", 0)),
            "stale_lock_recovery_count": int(lock.get("stale_recoveries", 0)),
            "duplicate_execution_prevention_count": int(self.children.metrics.snapshot().get("duplicate_execution_preventions", 0)),
            "blind_redispatch_prevention_count": int(self.children.metrics.snapshot().get("blind_redispatch_preventions", 0)),
            "dispatch_intent_failure_count": int(self.children.metrics.snapshot().get("durable_intent_failures", 0)),
            "verification_mismatch_count": int(self.children.metrics.snapshot().get("verification_mismatches", 0)),
            "fallback_count": self._fallback_count,
            "recovery_single_flight_collisions": self._sweep_collisions,
            "recovery_failures": self._sweep_failures,
            "recovery_backlog": {
                "returned_count": len(reconciliation),
                "count_precision": "lower_bound" if len(reconciliation) == 100 else "exact",
                "retained_lock_count": int(lock.get("current_active_lock_count", 0)) + int(lock.get("current_expired_lease_count", 0)),
                "expired_lock_count": int(lock.get("current_expired_lease_count", 0)),
                "pending_children": [
                    {"child_id": item["child_id"], **item["lock_recovery"]}
                    for item in reconciliation[:16]
                ],
                "details_truncated": len(reconciliation) > 16,
                "collection_truncated": len(reconciliation) == 100,
            },
            "recovery_cadence_seconds": RECOVERY_CADENCE_SECONDS,
            "recovery_batch_size": RECOVERY_BATCH_SIZE,
            "lock_profile": {
                "lease_seconds": 120,
                "renewal_interval_seconds": 20,
                "wait_timeout_seconds": 0,
                "poll_interval_seconds": 0.05,
            },
        }

    @staticmethod
    def _is_terminal_zero_dispatch_parent(task: Any) -> bool:
        return (
            task.state in TERMINAL_TASK_STATES
            and not task.provider_attempts
            and not task.dispatched_at
        )

    @staticmethod
    def _orphaned_child_state(
        task: Any, record: Any
    ) -> tuple[str, str | None]:
        """Project a never-started child of a terminal, zero-dispatch parent.

        A child with no execution record has literally never started, and
        under a parent that reached a terminal state with no provider attempt
        it never will.  Reporting ``not_started`` there implies pending work
        that cannot exist, which is the parent/child contradiction this
        deficiency describes.  This projects durable facts; it mutates
        nothing.
        """

        if record is not None:
            return record.state, record.normalized_outcome
        if F3RuntimeIntegration._is_terminal_zero_dispatch_parent(task):
            return "terminal", "cancelled_pre_dispatch"
        return "not_started", None

    def _orphan_audit_pending(self, record: Any, runtime: dict[str, Any]) -> bool:
        if self.service.audit is None:
            return False
        start = int(runtime["audited_event_count"])
        if start > len(record.events):
            raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        return any(
            (item["event_type"] == "execution_cancelled"
             and ORPHAN_RECONCILIATION_REASON
             in tuple(item.get("diagnostic_codes") or ()))
            or self._is_unrecorded_binding_event(item)
            for item in record.events[start:]
        )

    @staticmethod
    def _is_unrecorded_binding_event(item: dict[str, Any]) -> bool:
        return (item["event_type"] in {"pre_dispatch_terminal", "execution_cancelled"}
                and "unrecorded_lock_tokens_bound"
                in tuple(item.get("diagnostic_codes") or ()))

    def _orphan_cleanup_pending(
        self,
        *,
        parent: Any | None,
        declaration: dict[str, Any],
        record: Any | None,
        runtime: dict[str, Any],
        lock_records: tuple[Any, ...],
    ) -> bool:
        if (
            parent is None
            or not self._is_terminal_zero_dispatch_parent(parent)
            or record is None
            or record.dispatch_intent is not None
        ):
            return False
        if not record.terminal:
            return True
        if self._related_lock_records(
            declaration, record, lock_records
        ) or runtime["selective_hold_tokens"]:
            return True
        return (
            (record.normalized_outcome == "cancelled_pre_dispatch"
             or any(self._is_unrecorded_binding_event(item)
                    for item in record.events))
            and (
                runtime["reconciliation_result"]
                != ORPHAN_RECONCILIATION_RESULT
                or self._orphan_audit_pending(record, runtime)
            )
        )

    def _active_recovery_candidates(
        self,
        *,
        now: datetime,
        sweep_started: float,
        monotonic: Callable[[], float] | None = None,
    ) -> dict[str, Any]:
        """Select active work independently from historical declaration scan.

        Navigation is filtered to exact F3 authority before it is bounded and
        remains non-authoritative. Every selected task and child is reloaded
        from its durable authority before it can become a candidate. A
        separate cursor is used only to make an ineligible prefix restart-fair;
        it never authorizes execution and is not advanced past discovered
        eligible work.
        """

        clock = monotonic or time.monotonic
        expected_cursor = self.children.active_recovery_cursor()
        task_ids = list(
            self.service.task_repository.f3_nonterminal_task_ids(
                limit=MAX_F3_PUBLIC_TASKS
            )
        )
        if expected_cursor is not None and task_ids:
            cursor_task = expected_cursor["public_task_id"]
            if cursor_task in task_ids:
                split = task_ids.index(cursor_task) + 1
                task_ids = task_ids[split:] + task_ids[:split]

        candidates: list[tuple[tuple[Any, ...], dict[str, Any], Any | None]] = []
        tasks_examined = 0
        manifests_read = 0
        declarations_examined = 0
        next_cursor = expected_cursor
        for public_task_id in task_ids:
            if (
                clock() - sweep_started
                >= RECOVERY_SWEEP_TIME_BUDGET_SECONDS
            ):
                break
            tasks_examined += 1
            next_cursor = self.children.active_recovery_cursor_for_task(
                public_task_id
            )
            public_task = self.service.task_repository.get(public_task_id)
            if public_task is None:
                raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
            if public_task.state in TERMINAL_TASK_STATES:
                continue
            if (
                public_task.legacy_projection.get("execution_authority")
                != F3_EXECUTION_AUTHORITY
            ):
                raise GovernanceError(
                    ErrorCode.EXECUTION_TASK_STORAGE_ERROR
                )
            task_declarations = self.children.declarations_for_task(
                public_task_id
            )
            if not task_declarations:
                raise GovernanceError(
                    ErrorCode.EXECUTION_TASK_STORAGE_ERROR
                )
            manifests_read += 1
            records = {
                item["operation_id"]: self.children.get(item["child_id"])
                for item in task_declarations
            }
            candidate: tuple[dict[str, Any], Any | None] | None = None
            successful_projection_anchor: (
                tuple[dict[str, Any], Any] | None
            ) = None
            sequence_complete_successfully = True
            for declaration in task_declarations:
                declarations_examined += 1
                record = records[declaration["operation_id"]]
                if record is not None and record.terminal:
                    self._active_recovery_mode(record)
                    if record.normalized_outcome == "succeeded_verified":
                        successful_projection_anchor = (
                            declaration,
                            record,
                        )
                        continue
                    sequence_complete_successfully = False
                    candidate = (declaration, record)
                    break
                sequence_complete_successfully = False
                dependencies = set(
                    declaration["operation_dependency_ids"]
                )
                if any(
                    records.get(dependency) is None
                    or records[dependency].normalized_outcome
                    != "succeeded_verified"
                    for dependency in dependencies
                ):
                    break
                candidate = (declaration, record)
                break
            if (
                candidate is None
                and sequence_complete_successfully
                and successful_projection_anchor is not None
            ):
                # The complete authoritative sequence is already successful,
                # but its nonterminal schema-1 parent still needs projection.
                # The final post-intent success is scheduling evidence only;
                # _project reloads and validates the complete sequence.
                candidate = successful_projection_anchor
            if candidate is None:
                continue
            declaration, record = candidate
            runtime = self.children.runtime(declaration["child_id"])
            if (
                runtime["next_eligible_at"] is not None
                and datetime.fromisoformat(runtime["next_eligible_at"]) > now
            ):
                continue
            self._active_recovery_mode(record)
            priority = self._active_recovery_priority(
                declaration, record, now=now
            )
            candidates.append((priority, declaration, record))

        candidates.sort(key=lambda item: item[0])
        return {
            "candidates": tuple(
                (declaration, record)
                for _priority, declaration, record in candidates
            ),
            "expected_cursor": expected_cursor,
            "next_cursor": next_cursor,
            "tasks_examined": tasks_examined,
            "manifest_reads": manifests_read,
            "declarations_examined": declarations_examined,
        }

    @staticmethod
    def _active_recovery_priority(
        declaration: dict[str, Any], record: Any | None, *, now: datetime
    ) -> tuple[Any, ...]:
        evidence_deadline = (
            None
            if record is None or record.dispatch_intent is None
            else datetime.fromisoformat(
                record.dispatch_intent["evidence_deadline"]
            )
        )
        return (
            0 if evidence_deadline is not None else 1,
            evidence_deadline or datetime.max.replace(tzinfo=now.tzinfo),
            declaration["public_task_id"].encode("utf-8"),
            declaration["operation_ordinal"],
            declaration["child_id"].encode("utf-8"),
        )

    @staticmethod
    def _active_recovery_mode(record: Any | None) -> str:
        """Classify validated child state without granting recovery authority."""

        if record is None:
            return _RECOVERY_MODE_PRE_INTENT
        try:
            execution_class = record.execution_class()
        except (KeyError, TypeError, ValueError) as exc:
            raise GovernanceError(
                ErrorCode.EXECUTION_TASK_STORAGE_ERROR
            ) from exc
        if execution_class in {
            EXECUTION_CLASS_TERMINAL_PRE_DISPATCH,
            EXECUTION_CLASS_VERIFIED_NO_DISPATCH,
            EXECUTION_CLASS_TERMINAL_POST_INTENT,
        }:
            return _RECOVERY_MODE_TERMINAL_PROJECTION
        if execution_class == EXECUTION_CLASS_POST_INTENT:
            return _RECOVERY_MODE_POST_INTENT
        if execution_class == EXECUTION_CLASS_PRE_INTENT:
            return _RECOVERY_MODE_PRE_INTENT
        raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)

    def _reload_active_candidate(
        self,
        reference: dict[str, Any],
        *,
        now: datetime,
        recovery_mode: str,
        allow_terminal_parent: bool = False,
    ) -> tuple[str, dict[str, Any] | None, Any | None]:
        """Reload authority and classify non-authoritative scheduling evidence.

        ``dismissed`` is reserved for authority that no longer applies.
        ``deferred`` retains still-applicable work that is inside its durable
        retry backoff.  Keeping those outcomes distinct prevents a failed
        public projection from being mistaken for a settled checkpoint.
        """

        public_task = self.service.task_repository.get(
            reference["public_task_id"]
        )
        if (
            public_task is None
            or (public_task.state in TERMINAL_TASK_STATES and not allow_terminal_parent)
            or public_task.legacy_projection.get("execution_authority")
            != F3_EXECUTION_AUTHORITY
        ):
            return "dismissed", None, None
        current = next(
            (
                item
                for item in self.children.declarations_for_task(
                    public_task.task_id
                )
                if item["child_id"] == reference["child_id"]
            ),
            None,
        )
        if current is None or any(
            current[name] != reference[name]
            for name in (
                "public_task_id",
                "child_id",
                "operation_id",
                "operation_ordinal",
                "attempt_id",
                "declaration_hash",
            )
        ):
            return "dismissed", None, None
        record = self.children.get(current["child_id"])
        current_mode = self._active_recovery_mode(record)
        allowed_modes = {
            _RECOVERY_MODE_CHECKPOINT: {
                _RECOVERY_MODE_POST_INTENT,
                _RECOVERY_MODE_TERMINAL_PROJECTION,
            },
            # A post-intent child may terminalize after selection. It was
            # checkpointed before this reload, so projection remains safe.
            _RECOVERY_MODE_POST_INTENT: {
                _RECOVERY_MODE_POST_INTENT,
                _RECOVERY_MODE_TERMINAL_PROJECTION,
            },
            _RECOVERY_MODE_TERMINAL_PROJECTION: {
                _RECOVERY_MODE_TERMINAL_PROJECTION,
            },
            _RECOVERY_MODE_PRE_INTENT: {
                _RECOVERY_MODE_PRE_INTENT,
            },
        }
        if recovery_mode not in allowed_modes:
            raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        if current_mode not in allowed_modes[recovery_mode]:
            return "dismissed", None, None
        if record is not None:
            identity = record.execution_identity()
            if (
                identity.task_id != current["child_id"]
                or identity.plan_id != current["plan_id"]
                or identity.attempt_id != current["attempt_id"]
                or record.adapter_id != current["adapter_id"]
                or record.operation
                not in {
                    current["operation_id"],
                    current["capability_id"],
                }
                or record.prepared_operation_hash
                != current["prepared_operation_hash"]
                or record.target
                != {
                    "target_type": current["target_type"],
                    "target_id": current["target_id"],
                }
            ):
                raise GovernanceError(
                    ErrorCode.EXECUTION_TASK_STORAGE_ERROR
                )
        runtime = self.children.runtime(current["child_id"])
        if (
            runtime["next_eligible_at"] is not None
            and datetime.fromisoformat(runtime["next_eligible_at"]) > now
        ):
            return "deferred", current, record
        return "eligible", current, record

    def _checkpointed_priority_candidates(
        self,
        checkpoint: dict[str, Any] | None,
        *,
        now: datetime,
        sweep_started: float,
        monotonic: Callable[[], float] | None = None,
    ) -> dict[str, Any]:
        clock = monotonic or time.monotonic
        candidates: list[
            tuple[tuple[Any, ...], dict[str, Any], Any]
        ] = []
        retained: list[dict[str, Any]] = []
        deferred: list[str] = []
        dismissed: list[str] = []
        references = tuple(
            () if checkpoint is None else checkpoint["candidates"]
        )
        examined = 0
        for reference in references:
            if (
                clock() - sweep_started
                >= RECOVERY_SWEEP_TIME_BUDGET_SECONDS
            ):
                break
            examined += 1
            disposition, declaration, record = self._reload_active_candidate(
                reference, now=now, recovery_mode=_RECOVERY_MODE_CHECKPOINT
            )
            if disposition == "dismissed":
                dismissed.append(reference["child_id"])
                continue
            if disposition == "deferred":
                assert declaration is not None
                retained.append(declaration)
                deferred.append(declaration["child_id"])
                continue
            assert declaration is not None and record is not None
            current_mode = self._active_recovery_mode(record)
            candidates.append(
                (
                    (
                        0
                        if current_mode == _RECOVERY_MODE_POST_INTENT
                        else 1,
                        *self._active_recovery_priority(
                            declaration, record, now=now
                        ),
                    ),
                    declaration,
                    record,
                )
            )
        candidates.sort(key=lambda item: item[0])
        return {
            "candidates": tuple(
                (declaration, record)
                for _priority, declaration, record in candidates
            ),
            "retained": (*retained, *references[examined:]),
            "deferred_child_ids": tuple(deferred),
            "dismissed_child_ids": tuple(dismissed),
            "examined": examined,
        }

    @staticmethod
    def _related_lock_records(
        declaration: dict[str, Any],
        record: Any,
        lock_records: tuple[Any, ...] | list[Any],
    ) -> list[Any]:
        token_identities = {
            (item["key"], item["generation"])
            for item in record.lock_tokens
        }
        return [
            item
            for item in lock_records
            if item.task_id == declaration["child_id"]
            or (item.key, item.generation) in token_identities
        ]

    @staticmethod
    def _lock_record_matches_execution_authority(
        declaration: dict[str, Any], record: Any, lock_record: Any
    ) -> bool:
        identity = record.execution_identity()
        expected_operation = (declaration["capability_id"]
                              if declaration["plan_contract_version"] in {1, 2}
                              else declaration["operation_id"])
        token_matches = [
            item
            for item in record.lock_tokens
            if item["key"] == lock_record.key
            and item["generation"] == lock_record.generation
            and item["mode"] == lock_record.mode
            and item["owner_id"] == lock_record.owner_id
        ]
        return (
            len(token_matches) == 1
            and identity.task_id == declaration["child_id"]
            and lock_record.task_id == declaration["child_id"]
            and identity.plan_id == declaration["plan_id"]
            and lock_record.plan_id == declaration["plan_id"]
            and record.operation == expected_operation
            and lock_record.operation_id == expected_operation
            and identity.attempt_id == declaration["attempt_id"]
            and lock_record.attempt_id == declaration["attempt_id"]
        )

    @staticmethod
    def _exact_lock_owner(
        declaration: dict[str, Any], record: Any, lock_records: list[Any]
    ) -> LockOwner:
        identity = record.execution_identity()
        owners = {
            (
                item.owner_id,
                item.task_id,
                item.plan_id,
                item.operation_id,
                item.attempt_id,
            )
            for item in lock_records
        }
        token_owners = {
            token["owner_id"]
            for token in record.lock_tokens
            if any(
                item.key == token["key"]
                and item.generation == token["generation"]
                for item in lock_records
            )
        }
        if len(token_owners) != 1:
            raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        expected = (
            next(iter(token_owners)),
            declaration["child_id"],
            declaration["plan_id"],
            record.operation,
            identity.attempt_id,
        )
        if owners != {expected}:
            raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        return LockOwner(
            owner_id=expected[0],
            task_id=expected[1],
            plan_id=expected[2],
            operation_id=expected[3],
            attempt_id=expected[4],
        )

    def _release_orphaned_child_locks(
        self, declaration: dict[str, Any], record: Any
    ) -> int:
        """Release only the exact pre-intent child's fenced generations."""

        if record.execution_class() not in {
            EXECUTION_CLASS_TERMINAL_PRE_DISPATCH,
            EXECUTION_CLASS_VERIFIED_NO_DISPATCH,
        }:
            raise GovernanceError(ErrorCode.EXECUTION_TASK_INVALID_STATE)
        child_id = declaration["child_id"]
        lock_records = self._related_lock_records(
            declaration, record, self.locks.records()
        )
        if not lock_records:
            return 0
        if not record.lock_tokens:
            parent = self.service.task_repository.get(declaration["public_task_id"])
            if (parent is None or not self._is_terminal_zero_dispatch_parent(parent)
                or parent.plan_id != declaration["plan_id"]
                or parent.plan_hash != declaration["plan_hash"]
                or parent.legacy_projection.get("execution_authority") != F3_EXECUTION_AUTHORITY
                or child_id not in parent.legacy_projection.get("child_execution_ids", ())):
                raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)

            def settle(current_declaration, current_record, persist):
                if current_declaration != declaration:
                    raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
                identity = current_record.execution_identity()
                return self.locks.settle_unrecorded(
                    owner=LockOwner(owner_id=identity.owner_id, task_id=identity.task_id,
                                    plan_id=identity.plan_id, operation_id=current_record.operation,
                                    attempt_id=identity.attempt_id),
                    expected_records=tuple(lock_records),
                    complete_request_hash=current_declaration["complete_lock_request_hash"],
                    execution_created_at=current_record.created_at,
                    execution_updated_at=current_record.updated_at,
                    persist_tokens=persist, timing=PRODUCTION_LOCK_TIMING,
                    now=self.service.now(),
                )

            return self.children.settle_unrecorded_locks(
                child_id, now=self.service.now(), settle=settle,
            )
        if any(
            not self._lock_record_matches_execution_authority(
                declaration, record, item
            )
            for item in lock_records
        ):
            # A different or later fenced generation is ambiguous authority.
            raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        owner = self._exact_lock_owner(declaration, record, lock_records)
        released = 0
        regular = sorted(
            (item for item in lock_records if not item.conflict_hold),
            key=lambda item: item.key.encode("utf-8"),
        )
        if regular:
            handle = LockHandle(
                owner=owner,
                tokens=tuple(
                    LockToken(item.key, item.generation, item.mode)
                    for item in regular
                ),
                acquired_at=regular[0].acquired_at,
                lease_expires_at=regular[0].lease_expires_at,
                timing=PRODUCTION_LOCK_TIMING,
            )
            self.locks.release(handle)
            released += len(regular)
        held = sorted(
            (item for item in lock_records if item.conflict_hold),
            key=lambda item: item.key.encode("utf-8"),
        )
        if held:
            self.locks.release_conflict_hold(
                owner=owner,
                tokens=tuple(
                    LockToken(item.key, item.generation, item.mode)
                    for item in held
                ),
                reason_code=ORPHAN_RECONCILIATION_REASON,
            )
            released += len(held)
        return released

    def _reconcile_orphaned_child(
        self, declaration: dict[str, Any], *, now: datetime
    ) -> tuple[bool, bool]:
        """Converge one exact orphan through restart-safe durable steps."""

        child_id = declaration["child_id"]
        record = self.children.get(child_id)
        if record is None or record.dispatch_intent is not None:
            return False, False
        tokenless_retained = bool(not record.lock_tokens and self._related_lock_records(
            declaration, record, self.locks.records()))
        if tokenless_retained and datetime.fromisoformat(record.claim_expires_at) > now:
            # Never cancel or reconstruct acquisition while its owner can run.
            return False, False
        terminalized = False
        if not record.terminal:
            if not self.children.cancel(
                child_id,
                now=now,
                diagnostic_codes=(ORPHAN_RECONCILIATION_REASON,),
                expected_claim_generation=record.claim_generation,
                require_expired_claim=tokenless_retained,
            ):
                return False, False
            terminalized = True
            record = self.children.get(child_id)
        if record is None or not record.terminal:
            raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)

        self._release_orphaned_child_locks(declaration, record)
        record = self.children.get(child_id)
        if record is None:
            raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        if self._related_lock_records(
            declaration, record, self.locks.records()
        ):
            raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)

        runtime = self.children.runtime(child_id)
        pending_audit_result = "orphaned_pre_dispatch_audit_pending"
        if (
            runtime["selective_hold_tokens"]
            or runtime["hold_release_authority"] is not None
            or runtime["selective_hold_promoted_at"] is not None
            or runtime["selective_hold_reason"] is not None
            or runtime["reconciliation_result"]
            not in {pending_audit_result, ORPHAN_RECONCILIATION_RESULT}
            or runtime["backoff_seconds"]
            or runtime["next_eligible_at"] is not None
        ):
            self.children.update_runtime(
                child_id,
                changes={
                    "selective_hold_tokens": [],
                    "hold_release_authority": None,
                    "selective_hold_promoted_at": None,
                    "selective_hold_reason": None,
                    "last_reconciliation_at": now.isoformat(),
                    "reconciliation_result": pending_audit_result,
                    "backoff_seconds": 0,
                    "next_eligible_at": None,
                },
            )

        audited = self.service.audit is None or self._audit_record_events(
            declaration, record
        )
        runtime = self.children.runtime(child_id)
        if audited and runtime["reconciliation_result"] != ORPHAN_RECONCILIATION_RESULT:
            self.children.update_runtime(
                child_id,
                changes={
                    "last_reconciliation_at": now.isoformat(),
                    "reconciliation_result": ORPHAN_RECONCILIATION_RESULT,
                },
            )
        return True, terminalized

    def _reconcile_terminal_child_fault(
        self, declaration: dict[str, Any], *, now: datetime
    ) -> None:
        """Finish exact terminal bookkeeping; never prepare, execute or release holds."""
        with self.children.public_projection_transaction():
            disposition, current, record = self._reload_active_candidate(
                declaration, now=now,
                recovery_mode=_RECOVERY_MODE_TERMINAL_PROJECTION,
                allow_terminal_parent=True,
            )
            if disposition != "eligible" or current is None or record is None:
                raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
            task = self.service._load_task(current["public_task_id"])
            plan = self.service._load_for_projection(current["plan_id"])
            if (task.state not in TERMINAL_TASK_STATES
                    or task.plan_id != plan.plan_id
                    or task.plan_hash != self.service.plan_hash(plan)):
                raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
            self._project_locked(plan, task, require_audit_complete=True)
            self.children.update_runtime(
                current["child_id"],
                changes={
                    "last_reconciliation_at": now.isoformat(),
                    "reconciliation_result": "transition_processed",
                    "backoff_seconds": 0,
                    "next_eligible_at": None,
                },
            )

    def _reconcile_orphaned_children(
        self,
        *,
        declarations: tuple[dict[str, Any], ...],
        now: datetime,
        sweep_started: float,
        transition_limit: int,
        start_cursor: dict[str, Any] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> dict[str, Any]:
        """Run a fair historical slice without skipping pending authority."""

        clock = monotonic or time.monotonic
        lock_records = self.locks.records()
        parents: dict[str, Any | None] = {}
        processed = 0
        terminalized = 0
        examined = 0
        next_cursor = start_cursor
        for declaration in declarations:
            if examined >= ORPHAN_RECOVERY_SCAN_LIMIT or (
                clock() - sweep_started
                >= RECOVERY_SWEEP_TIME_BUDGET_SECONDS
            ):
                break
            examined += 1
            public_task_id = declaration["public_task_id"]
            if public_task_id not in parents:
                parents[public_task_id] = self.service.task_repository.get(
                    public_task_id
                )
            record = self.children.get(declaration["child_id"])
            runtime = self.children.runtime(declaration["child_id"])
            cursor = self.children.recovery_cursor_for_declaration(
                declaration
            )
            if (
                runtime["next_eligible_at"] is not None
                and datetime.fromisoformat(runtime["next_eligible_at"]) > now
            ):
                next_cursor = cursor
                continue
            pending = self._orphan_cleanup_pending(
                parent=parents[public_task_id],
                declaration=declaration,
                record=record,
                runtime=runtime,
                lock_records=lock_records,
            )
            fault_key = "recovery_child:" + declaration["child_id"]
            with self._readiness_lock:
                fault_revision = self._readiness_faults.get(fault_key)
            parent = parents[public_task_id]
            # Preserve existing orphan cleanup when it still has work. All
            # other known terminal faults use exact projection reconciliation,
            # including verified no-ops and pre-dispatch terminal outcomes.
            terminal_fault = bool(
                fault_revision is not None
                and parent is not None and parent.state in TERMINAL_TASK_STATES
                and record is not None and record.terminal
                and not pending
            )
            if not pending and not terminal_fault:
                next_cursor = cursor
                continue
            # Do not advance past work that could not receive this sweep's
            # transition authority. It will be the first historical candidate
            # on the next sweep, rather than waiting for a namespace rotation.
            if (
                processed >= transition_limit
            ):
                break
            processed += 1
            try:
                if terminal_fault:
                    self._reconcile_terminal_child_fault(declaration, now=now)
                else:
                    _changed, child_terminalized = self._reconcile_orphaned_child(
                        declaration, now=now
                    )
                    terminalized += int(child_terminalized)
                self._clear_readiness_fault(fault_key, fault_revision)
            except Exception as exc:
                self._readiness_fault(fault_key)
                self._sweep_failures += 1
                runtime = self.children.runtime(declaration["child_id"])
                backoff = min(
                    max(5, int(runtime["backoff_seconds"]) * 2), 300
                )
                self.children.update_runtime(
                    declaration["child_id"],
                    changes={
                        "last_reconciliation_at": now.isoformat(),
                        "reconciliation_result": (
                            ("unrecorded_locks_authority_unresolved" if isinstance(exc, (UnrecordedLockAuthorityError, LockOwnershipError, GovernanceError))
                             else "unrecorded_locks_storage_error" if isinstance(exc, (ExecutionStorageError, LockStorageError))
                             else "unrecorded_locks_unexpected_error")
                            if record is not None and not record.lock_tokens else "bounded_retry"
                        ),
                        "backoff_seconds": backoff,
                        "next_eligible_at": (
                            now + timedelta(seconds=backoff)
                        ).isoformat(),
                    },
                )
            next_cursor = cursor
        return {
            "processed": processed,
            "terminalized": terminalized,
            "examined": examined,
            "next_cursor": next_cursor,
        }

    async def _recover_active_candidates(
        self,
        candidates: tuple[tuple[dict[str, Any], Any | None], ...],
        *,
        now: datetime,
        sweep_started: float,
        transition_limit: int,
        recovery_mode: str,
        monotonic: Callable[[], float] | None = None,
    ) -> dict[str, Any]:
        """Reload authority, then run active work inside recovery bounds."""

        clock = monotonic or time.monotonic
        processed = 0
        transitions = 0
        selected: list[str] = []
        settled: list[str] = []
        dismissed: list[str] = []
        retry: list[str] = []
        for reference, _selected_record in candidates:
            if transitions >= transition_limit or (
                clock() - sweep_started
                >= RECOVERY_SWEEP_TIME_BUDGET_SECONDS
            ):
                break
            disposition, declaration, _current_record = (
                self._reload_active_candidate(
                    reference,
                    now=now,
                    recovery_mode=recovery_mode,
                )
            )
            if disposition == "dismissed":
                dismissed.append(reference["child_id"])
                continue
            if disposition == "deferred":
                retry.append(reference["child_id"])
                continue
            assert declaration is not None
            runtime = self.children.runtime(declaration["child_id"])
            transitions += 1
            selected.append(declaration["child_id"])
            fault_key = "recovery_child:" + declaration["child_id"]
            with self._readiness_lock:
                fault_revision = self._readiness_faults.get(fault_key)
            try:
                joined_active_execution = False
                plan = self.service._load_for_projection(
                    declaration["plan_id"]
                )
                task = self.service._load_task(
                    declaration["public_task_id"]
                )
                record = self.children.get(declaration["child_id"])
                historical_policy = bool(
                    plan.policy_decision is not None
                    and plan.policy_decision.policy_version != POLICY_VERSION
                )
                if (
                    historical_policy
                    and self._active_recovery_mode(record)
                    not in {
                        _RECOVERY_MODE_POST_INTENT,
                        _RECOVERY_MODE_TERMINAL_PROJECTION,
                    }
                ):
                    # Old policy may retain only readback/reconciliation for
                    # a durable intent, or projection of an already terminal
                    # record. It can never initialize or dispatch pre-intent
                    # work under current authority.
                    raise GovernanceError(ErrorCode.POLICY_SNAPSHOT_MISMATCH)
                if record is not None and record.terminal:
                    # Terminal recovery is projection-only. Keeping this
                    # branch before preparation makes provider preparation
                    # and execution unreachable for both successful and
                    # non-success terminal outcomes.
                    pass
                else:
                    current_recovery_mode = self._active_recovery_mode(record)
                    post_intent_readback = bool(
                        current_recovery_mode == _RECOVERY_MODE_POST_INTENT
                    )
                    if post_intent_readback:
                        prepared, requests = await self._load_prepared(
                            plan,
                            task,
                            readback_child_id=declaration["child_id"],
                        )
                    else:
                        prepared, requests = await self._load_prepared(
                            plan, task
                        )
                    operation = prepared[declaration["operation_ordinal"]]
                    if record is None or record.dispatch_intent is None:
                        task = self._enter_public_preflight(task)
                    if post_intent_readback:
                        result = await self._execute_child(
                            plan,
                            task,
                            declaration,
                            operation,
                            requests,
                            readback_only=True,
                        )
                    else:
                        result = await self._execute_child(
                            plan,
                            task,
                            declaration,
                            operation,
                            requests,
                        )
                    if result.duplicate_execution:
                        self._sweep_collisions += 1
                        # Reusing an expired claim for readback is also marked
                        # duplicate. Only a live ownership collision defers
                        # projection; recovered progress must still publish.
                        joined_active_execution = (
                            not result.terminal
                            and "active_execution_exists" in result.diagnostic_codes
                        )
                if not joined_active_execution:
                    self._project(plan, task)
                processed += 1
                latest = self.children.get(declaration["child_id"])
                pending = latest is not None and not latest.terminal
                backoff = (
                    min(
                        max(30, int(runtime["backoff_seconds"]) * 2),
                        300,
                    )
                    if pending
                    else 0
                )
                self.children.update_runtime(
                    declaration["child_id"],
                    changes={
                        "last_reconciliation_at": now.isoformat(),
                        "reconciliation_result": (
                            "observation_pending"
                            if pending
                            else "transition_processed"
                        ),
                        "backoff_seconds": backoff,
                        "next_eligible_at": (
                            (now + timedelta(seconds=backoff)).isoformat()
                            if pending
                            else None
                        ),
                    },
                )
                settled.append(declaration["child_id"])
                self._clear_readiness_fault(fault_key, fault_revision)
            except Exception:
                self._readiness_fault(fault_key)
                self._sweep_failures += 1
                backoff = min(
                    max(5, int(runtime["backoff_seconds"]) * 2), 300
                )
                self.children.update_runtime(
                    declaration["child_id"],
                    changes={
                        "last_reconciliation_at": now.isoformat(),
                        "reconciliation_result": "bounded_retry",
                        "backoff_seconds": backoff,
                        "next_eligible_at": (
                            now + timedelta(seconds=backoff)
                        ).isoformat(),
                    },
                )
                retry.append(declaration["child_id"])
        return {
            "processed": processed,
            "transitions": transitions,
            "selected_child_ids": tuple(selected),
            "settled_child_ids": tuple(settled),
            "dismissed_child_ids": tuple(dismissed),
            "retry_child_ids": tuple(retry),
        }

    async def recover_once(self, trigger: str) -> dict[str, int]:
        with self._readiness_lock:
            if self._recovery_running:
                self._sweep_collisions += 1
                return {"processed": 0, "eligible_limit": RECOVERY_BATCH_SIZE}
            if trigger == "periodic":
                self._supervisor_task = asyncio.current_task()
            self._recovery_running = True
            faults = dict(self._readiness_faults)
            initial = not self._startup_complete
        try:
            # A scheduler page cannot clear corruption outside that page.
            with self._readiness_lock:
                revision = self._readiness_revision
            if initial or faults:
                self._validate_readiness_storage()
            token = _RECOVERY_READINESS.set((self, revision))
            try:
                result = await self._recover_once(trigger)
            finally:
                _RECOVERY_READINESS.reset(token)
            diagnostic_faults = {
                key: value for key, value in faults.items()
                if key in {"storage_read", "health", "reconciliation", "recovery_pass"}
                or key.startswith("task_projection:")
            }
            if initial or diagnostic_faults:
                self._validate_readiness_storage()
                for key, revision in diagnostic_faults.items():
                    if key != "recovery_pass" or self._last_sweep_complete:
                        self._clear_readiness_fault(key, revision)
            with self._readiness_lock:
                if not self._readiness_faults and self._last_sweep_complete:
                    self._startup_complete = True
            return result
        except BaseException:
            # Cancellation must be visible too; preserve the original exception.
            self._readiness_fault("recovery_pass")
            raise
        finally:
            with self._readiness_lock:
                self._recovery_running = False

    async def _recover_once(self, trigger: str) -> dict[str, int]:
        del trigger
        self._coordinator_initialized = True
        now = self.service.now()
        self._last_sweep_at = now.isoformat()
        self._next_sweep_at = (now + timedelta(seconds=RECOVERY_CADENCE_SECONDS)).isoformat()
        clock = self._recovery_monotonic
        sweep_started = clock()
        deadline_reached = lambda: (
            clock() - sweep_started
            >= RECOVERY_SWEEP_TIME_BUDGET_SECONDS
        )

        # A priority candidate durably checkpointed by a prior bounded
        # discovery sweep receives the first recovery opportunity. This
        # includes observation-only post-intent work and projection-only
        # terminal work. The checkpoint is navigation evidence only: every
        # authority surface is reloaded immediately before recovery.
        checkpoint_expected = self.children.active_recovery_checkpoint()
        checkpoint_selection = self._checkpointed_priority_candidates(
            checkpoint_expected,
            now=now,
            sweep_started=sweep_started,
            monotonic=clock,
        )
        checkpoint_candidates = checkpoint_selection["candidates"]
        checkpoint_recovery = await self._recover_active_candidates(
            checkpoint_candidates,
            now=now,
            sweep_started=sweep_started,
            transition_limit=RECOVERY_BATCH_SIZE,
            recovery_mode=_RECOVERY_MODE_CHECKPOINT,
            monotonic=clock,
        )
        checkpoint_handled_ids = {
            *checkpoint_selection["dismissed_child_ids"],
            *checkpoint_recovery["settled_child_ids"],
            *checkpoint_recovery["dismissed_child_ids"],
        }
        checkpoint_remaining = (
            *(
                declaration
                for declaration, _record in checkpoint_candidates
                if declaration["child_id"] not in checkpoint_handled_ids
            ),
            *checkpoint_selection["retained"],
        )
        checkpoint_current = (
            self.children.active_recovery_checkpoint_for_candidates(
                checkpoint_remaining
            )
        )
        if checkpoint_current != checkpoint_expected:
            self.children.replace_active_recovery_checkpoint(
                expected=checkpoint_expected,
                next_checkpoint=checkpoint_current,
            )

        # Active work is selected from authoritative nonterminal parent and
        # child state, never from the historical declaration cursor. Discovery
        # runs only after pending post-intent work and stops at the shared
        # deadline. Newly discovered post-intent work is checkpointed before
        # recovery so a budget boundary or crash cannot cause perpetual rescan.
        inactive_cursor = self.children.active_recovery_cursor()
        active_selection: dict[str, Any] = {
            "candidates": (),
            "expected_cursor": inactive_cursor,
            "next_cursor": inactive_cursor,
            "tasks_examined": 0,
            "manifest_reads": 0,
            "declarations_examined": 0,
        }
        remaining_priority_capacity = max(
            0,
            RECOVERY_BATCH_SIZE - checkpoint_recovery["transitions"],
        )
        if remaining_priority_capacity and not deadline_reached():
            active_selection = self._active_recovery_candidates(
                now=now,
                sweep_started=sweep_started,
                monotonic=clock,
            )
        active_candidates = active_selection["candidates"]
        post_intent_candidates = tuple(
            item
            for item in active_candidates
            if self._active_recovery_mode(item[1])
            == _RECOVERY_MODE_POST_INTENT
        )
        terminal_projection_candidates = tuple(
            item
            for item in active_candidates
            if self._active_recovery_mode(item[1])
            == _RECOVERY_MODE_TERMINAL_PROJECTION
        )
        pre_intent_candidates = tuple(
            item
            for item in active_candidates
            if self._active_recovery_mode(item[1])
            == _RECOVERY_MODE_PRE_INTENT
        )
        navigation = self.service.task_repository.navigation_metrics()
        has_terminal_history = (
            navigation["record_count"]
            > navigation["nonterminal_record_count"]
        )

        # Nonterminal possibly-dispatched work retains first priority. Exact
        # terminal projection then owns the remaining priority capacity and
        # is checkpointed before any public projection write.
        fresh_post_intent_candidates = post_intent_candidates[
            :remaining_priority_capacity
        ]
        projection_capacity = max(
            0,
            remaining_priority_capacity
            - len(fresh_post_intent_candidates),
        )
        fresh_projection_candidates = terminal_projection_candidates[
            :projection_capacity
        ]
        fresh_priority_candidates = (
            *fresh_post_intent_candidates,
            *fresh_projection_candidates,
        )
        # Deferred checkpoint entries are non-authoritative navigation. When
        # they fill the durable bound, evict only as many deferred references
        # as necessary to checkpoint newly eligible priority work. Evicted
        # children remain reachable through authoritative active discovery;
        # retry IDs also prevent the round-robin cursor from advancing past an
        # unsettled sweep. The original order makes eviction deterministic.
        checkpoint_composition_remaining = checkpoint_remaining
        overflow = max(
            0,
            len(checkpoint_composition_remaining)
            + len(fresh_priority_candidates)
            - ACTIVE_RECOVERY_CHECKPOINT_LIMIT,
        )
        if overflow:
            deferred_ids = set(
                checkpoint_selection["deferred_child_ids"]
            )
            evictable_ids = [
                declaration["child_id"]
                for declaration in reversed(
                    checkpoint_composition_remaining
                )
                if declaration["child_id"] in deferred_ids
            ]
            evicted_ids = set(evictable_ids[:overflow])
            checkpoint_composition_remaining = tuple(
                declaration
                for declaration in checkpoint_composition_remaining
                if declaration["child_id"] not in evicted_ids
            )
        fresh_checkpoint_capacity = max(
            0,
            ACTIVE_RECOVERY_CHECKPOINT_LIMIT
            - len(checkpoint_composition_remaining),
        )
        fresh_priority_candidates = fresh_priority_candidates[
            :fresh_checkpoint_capacity
        ]
        fresh_checkpoint = (
            self.children.active_recovery_checkpoint_for_candidates(
                (
                    *checkpoint_composition_remaining,
                    *(
                        declaration
                        for declaration, _record
                        in fresh_priority_candidates
                    ),
                )
            )
        )
        if fresh_checkpoint != checkpoint_current:
            self.children.replace_active_recovery_checkpoint(
                expected=checkpoint_current,
                next_checkpoint=fresh_checkpoint,
            )
            checkpoint_current = fresh_checkpoint

        # Possibly dispatched work owns the complete available batch before
        # historical cleanup receives a reservation. Recovery remains
        # observation/verification-only because _execute_child enforces the
        # durable-intent no-redispatch boundary.
        fresh_priority = await self._recover_active_candidates(
            fresh_priority_candidates,
            now=now,
            sweep_started=sweep_started,
            transition_limit=remaining_priority_capacity,
            recovery_mode=_RECOVERY_MODE_CHECKPOINT,
            monotonic=clock,
        )
        fresh_handled_ids = {
            *fresh_priority["settled_child_ids"],
            *fresh_priority["dismissed_child_ids"],
        }
        fresh_remaining = tuple(
            declaration
            for declaration, _record in fresh_priority_candidates
            if declaration["child_id"] not in fresh_handled_ids
        )
        next_checkpoint = (
            self.children.active_recovery_checkpoint_for_candidates(
                (*checkpoint_composition_remaining, *fresh_remaining)
            )
        )
        if next_checkpoint != checkpoint_current:
            self.children.replace_active_recovery_checkpoint(
                expected=checkpoint_current,
                next_checkpoint=next_checkpoint,
            )
            checkpoint_current = next_checkpoint
        priority_recovery = {
            "processed": (
                checkpoint_recovery["processed"]
                + fresh_priority["processed"]
            ),
            "transitions": (
                checkpoint_recovery["transitions"]
                + fresh_priority["transitions"]
            ),
            "selected_child_ids": (
                *checkpoint_recovery["selected_child_ids"],
                *fresh_priority["selected_child_ids"],
            ),
            "settled_child_ids": (
                *checkpoint_recovery["settled_child_ids"],
                *fresh_priority["settled_child_ids"],
            ),
            "dismissed_child_ids": (
                *checkpoint_selection["dismissed_child_ids"],
                *checkpoint_recovery["dismissed_child_ids"],
                *fresh_priority["dismissed_child_ids"],
            ),
            "retry_child_ids": (
                *checkpoint_selection["deferred_child_ids"],
                *checkpoint_recovery["retry_child_ids"],
                *fresh_priority["retry_child_ids"],
            ),
        }

        # The historical cursor is separate scheduling evidence for terminal
        # orphan discovery. It advances only through declarations safely
        # examined; an eligible orphan that lacks transition/time capacity is
        # deliberately left immediately before the persisted cursor.
        page = {
            "cursor": self.children.recovery_cursor(),
            "declarations": (),
            "manifest_reads": 0,
        }
        orphaned: dict[str, Any] = {
            "processed": 0,
            "terminalized": 0,
            "examined": 0,
            "next_cursor": page["cursor"],
        }
        remaining = max(
            0, RECOVERY_BATCH_SIZE - priority_recovery["transitions"]
        )
        historical_limit = (
            remaining
            if not pre_intent_candidates
            else min(1, remaining)
        )
        if (
            has_terminal_history
            and historical_limit
            and not deadline_reached()
        ):
            page = self.children.recovery_declaration_page(
                limit=RECOVERY_DECLARATION_PAGE_SIZE,
                should_stop=deadline_reached,
            )
            orphaned = self._reconcile_orphaned_children(
                declarations=page["declarations"],
                now=now,
                sweep_started=sweep_started,
                transition_limit=historical_limit,
                start_cursor=page["cursor"],
                monotonic=clock,
            )

        # Historical cleanup receives one bounded fairness slot ahead of
        # lower-priority pre-intent work when both classes exist. Any unused
        # slot returns to pre-intent recovery inside the same batch/time bound.
        pre_intent_limit = max(
            0,
            RECOVERY_BATCH_SIZE
            - priority_recovery["transitions"]
            - orphaned["processed"],
        )
        pre_intent = {
            "processed": 0,
            "transitions": 0,
            "selected_child_ids": (),
            "settled_child_ids": (),
            "dismissed_child_ids": (),
            "retry_child_ids": (),
        }
        if (
            pre_intent_candidates
            and pre_intent_limit
            and not deadline_reached()
        ):
            pre_intent = await self._recover_active_candidates(
                pre_intent_candidates,
                now=now,
                sweep_started=sweep_started,
                transition_limit=pre_intent_limit,
                recovery_mode=_RECOVERY_MODE_PRE_INTENT,
                monotonic=clock,
            )
        settled_ids = {
            *priority_recovery["settled_child_ids"],
            *pre_intent["settled_child_ids"],
        }

        stale_release_decisions = {}
        inverse_settled = set()
        for lock_record in (
            () if deadline_reached() else self.locks.expired_records(now=now)
        ):
            if deadline_reached():
                break
            try:
                declaration = self.children.declaration(lock_record.task_id)
            except Exception:
                self._readiness_fault("storage_read")
                continue
            context = self._inverse_lock_context(lock_record.task_id)
            if context is not None:
                _plan, inverse_task, _declarations, _records = context
                if inverse_task.task_id not in inverse_settled:
                    self._settle_inverse_sequence_locks(inverse_task)
                    inverse_settled.add(inverse_task.task_id)
                # Expiration is not fresh inverse admission. Only exact
                # sequence settlement above can release retained generations.
                continue
            execution = self.children.get(lock_record.task_id)
            execution_class = (
                None
                if execution is None
                else execution.execution_class()
            )
            if (
                execution is not None
                and execution_class
                in {
                    EXECUTION_CLASS_TERMINAL_PRE_DISPATCH,
                    EXECUTION_CLASS_VERIFIED_NO_DISPATCH,
                }
                and self._lock_record_matches_execution_authority(
                    declaration, execution, lock_record
                )
            ):
                stale_release_decisions[
                    (lock_record.key, lock_record.generation)
                ] = StaleRecoveryDecision(
                    StaleRecoveryAction.RELEASE,
                    "terminal_pre_dispatch_lock_release",
                )
        if stale_release_decisions and not deadline_reached():
            self.locks.recover_expired(
                stale_release_decisions, now=now
            )

        if orphaned["next_cursor"] != page["cursor"]:
            self.children.advance_recovery_cursor(
                expected=page["cursor"],
                next_cursor=orphaned["next_cursor"],
            )
        safely_navigated_ids = {
            *settled_ids,
            *priority_recovery["dismissed_child_ids"],
            *pre_intent["dismissed_child_ids"],
            *(
                item["child_id"]
                for item in (
                    ()
                    if checkpoint_current is None
                    else checkpoint_current["candidates"]
                )
            ),
        }
        unsettled_active = bool(
            priority_recovery["retry_child_ids"]
            or pre_intent["retry_child_ids"]
        )
        unprocessed_active = unsettled_active or any(
            declaration["child_id"] not in safely_navigated_ids
            for declaration, _record in active_candidates
        )
        if (
            not unprocessed_active
            and active_selection["next_cursor"]
            != active_selection["expected_cursor"]
        ):
            self.children.advance_active_recovery_cursor(
                expected=active_selection["expected_cursor"],
                next_cursor=active_selection["next_cursor"],
            )

        processed = (
            priority_recovery["processed"] + pre_intent["processed"]
        )
        active_transitions = (
            priority_recovery["transitions"] + pre_intent["transitions"]
        )
        self._last_sweep_complete = not deadline_reached()
        return {
            "processed": processed,
            "eligible_limit": RECOVERY_BATCH_SIZE,
            "orphaned_children_terminalized": (
                orphaned["terminalized"]
            ),
            "orphaned_children_processed": orphaned["processed"],
            "recovery_transitions": active_transitions + orphaned["processed"],
            "active_recovery_transitions": active_transitions,
            "active_tasks_examined": active_selection["tasks_examined"],
            "active_manifest_reads": active_selection["manifest_reads"],
            "active_declarations_examined": active_selection[
                "declarations_examined"
            ],
            "declarations_examined": orphaned["examined"],
            "manifest_reads": page["manifest_reads"],
        }

    async def supervise(self) -> None:
        self._supervisor_task = asyncio.current_task()
        await self.recover_once("startup")
        while True:
            await asyncio.sleep(RECOVERY_CADENCE_SECONDS)
            await self.recover_once("periodic")
