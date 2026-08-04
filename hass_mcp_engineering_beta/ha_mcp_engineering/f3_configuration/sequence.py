"""Pure ordered-plan conformance models; not an executor or lock manager."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from f3_contracts.operation_adapter import LockRequest

from ..governance.normalize import stable_hash
from .locks import complete_configuration_lock_set, lock_set_hash
from .models import PreparedConfigurationOperation


class SequenceStepState(str, Enum):
    PENDING = "pending"
    INTENT_COMMITTED = "intent_committed"
    OBSERVING = "observing"
    SUCCEEDED_VERIFIED = "succeeded_verified"
    FAILED_PRE_DISPATCH = "failed_pre_dispatch"
    FAILED_POST_DISPATCH = "failed_post_dispatch"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    CANCELLED_PRE_DISPATCH = "cancelled_pre_dispatch"


class SequenceNextAction(str, Enum):
    DISPATCH = "dispatch"
    OBSERVE = "observe"
    COMPLETE = "complete"
    STOP = "stop"


@dataclass(frozen=True)
class PreparedConfigurationSequence:
    plan_id: str
    task_id: str
    operations: tuple[PreparedConfigurationOperation, ...]
    lock_requests: tuple[LockRequest, ...]
    lock_set_hash: str
    sequence_hash: str


@dataclass(frozen=True)
class SequenceRecoveryDecision:
    action: SequenceNextAction
    operation_id: str | None
    terminal_outcome: str | None
    completed_operation_ids: tuple[str, ...]
    undispatched_operation_ids: tuple[str, ...]
    redispatch_prohibited: bool


@dataclass(frozen=True)
class DuplicateExecutionSnapshot:
    task_id: str
    plan_id: str
    active: bool
    terminal: bool
    dispatch_count: int


@dataclass(frozen=True)
class DuplicateExecutionDecision:
    reuse_existing_task: bool
    acquire_locks: bool
    create_task: bool
    dispatch_permitted: bool
    result: str


@dataclass(frozen=True)
class CancellationDecision:
    accepted: bool
    outcome: str
    completed_operation_ids: tuple[str, ...]
    cancelled_operation_ids: tuple[str, ...]


def prepare_configuration_sequence(
    operations: tuple[PreparedConfigurationOperation, ...]
    | list[PreparedConfigurationOperation],
) -> PreparedConfigurationSequence:
    """Bind caller order, dependencies, operations, and the complete lock set."""

    ordered = tuple(operations)
    if not 1 <= len(ordered) <= 8:
        raise ValueError("configuration sequence must contain 1 to 8 operations")
    plan_ids = {operation.plan_id for operation in ordered}
    task_ids = {operation.task_id for operation in ordered}
    if len(plan_ids) != 1 or len(task_ids) != 1:
        raise ValueError("configuration sequence identity is inconsistent")
    seen: set[str] = set()
    for index, operation in enumerate(ordered):
        if operation.order != index:
            raise ValueError("configuration operation order is not canonical")
        if operation.operation_id in seen:
            raise ValueError("configuration operation IDs must be unique")
        if len(operation.depends_on) != len(set(operation.depends_on)) or any(
            dependency not in seen for dependency in operation.depends_on
        ):
            raise ValueError("dependencies must name unique earlier operations")
        seen.add(operation.operation_id)
    requests = complete_configuration_lock_set(ordered)
    locks_hash = lock_set_hash(requests)
    sequence_hash = stable_hash(
        {
            "model": "f3-configuration-sequence-v1",
            "plan_id": ordered[0].plan_id,
            "task_id": ordered[0].task_id,
            "operation_hashes": [
                operation.prepared_operation_hash for operation in ordered
            ],
            "operation_ids": [operation.operation_id for operation in ordered],
            "dependencies": [
                list(operation.depends_on) for operation in ordered
            ],
            "lock_set_hash": locks_hash,
        }
    )
    return PreparedConfigurationSequence(
        plan_id=ordered[0].plan_id,
        task_id=ordered[0].task_id,
        operations=ordered,
        lock_requests=requests,
        lock_set_hash=locks_hash,
        sequence_hash=sequence_hash,
    )


def recover_sequence_position(
    sequence: PreparedConfigurationSequence,
    states: tuple[SequenceStepState, ...] | list[SequenceStepState],
) -> SequenceRecoveryDecision:
    """Choose only dispatch of untouched work or observation of intent work."""

    values = tuple(states)
    if len(values) != len(sequence.operations):
        raise ValueError("sequence state count is inconsistent")
    completed: list[str] = []
    for index, (operation, state) in enumerate(
        zip(sequence.operations, values, strict=True)
    ):
        later = tuple(
            candidate.operation_id
            for candidate in sequence.operations[index + 1 :]
            if values[candidate.order] == SequenceStepState.PENDING
        )
        if state == SequenceStepState.SUCCEEDED_VERIFIED:
            completed.append(operation.operation_id)
            continue
        if state == SequenceStepState.PENDING:
            dependencies_met = all(
                dependency in completed for dependency in operation.depends_on
            )
            if not dependencies_met:
                return SequenceRecoveryDecision(
                    SequenceNextAction.STOP,
                    operation.operation_id,
                    "partial_application" if completed else "failed_pre_dispatch",
                    tuple(completed),
                    (operation.operation_id, *later),
                    False,
                )
            return SequenceRecoveryDecision(
                SequenceNextAction.DISPATCH,
                operation.operation_id,
                None,
                tuple(completed),
                later,
                False,
            )
        if state in {
            SequenceStepState.INTENT_COMMITTED,
            SequenceStepState.OBSERVING,
        }:
            return SequenceRecoveryDecision(
                SequenceNextAction.OBSERVE,
                operation.operation_id,
                None,
                tuple(completed),
                later,
                True,
            )
        return SequenceRecoveryDecision(
            SequenceNextAction.STOP,
            operation.operation_id,
            (
                "partial_application"
                if completed
                else state.value
            ),
            tuple(completed),
            later,
            state not in {
                SequenceStepState.FAILED_PRE_DISPATCH,
                SequenceStepState.CANCELLED_PRE_DISPATCH,
            },
        )
    return SequenceRecoveryDecision(
        SequenceNextAction.COMPLETE,
        None,
        "succeeded_verified",
        tuple(completed),
        (),
        True,
    )


def classify_duplicate_execution(
    existing: DuplicateExecutionSnapshot,
    *,
    requested_task_id: str,
    requested_plan_id: str,
) -> DuplicateExecutionDecision:
    """Require exact task/plan identity and reuse its durable result."""

    if (
        existing.task_id != requested_task_id
        or existing.plan_id != requested_plan_id
        or existing.dispatch_count not in {0, 1}
    ):
        raise ValueError("duplicate execution identity is corrupted")
    if existing.active == existing.terminal:
        raise ValueError("duplicate execution lifecycle is contradictory")
    return DuplicateExecutionDecision(
        reuse_existing_task=True,
        acquire_locks=False,
        create_task=False,
        dispatch_permitted=False,
        result="return_terminal_task" if existing.terminal else "join_active_task",
    )


def cancellation_decision(
    sequence: PreparedConfigurationSequence,
    states: tuple[SequenceStepState, ...] | list[SequenceStepState],
) -> CancellationDecision:
    """Cancel only undispatched steps and preserve all verified work."""

    values = tuple(states)
    if len(values) != len(sequence.operations):
        raise ValueError("sequence state count is inconsistent")
    if any(
        state in {
            SequenceStepState.INTENT_COMMITTED,
            SequenceStepState.OBSERVING,
        }
        for state in values
    ):
        return CancellationDecision(False, "cancellation_rejected_after_intent", (), ())
    completed = tuple(
        operation.operation_id
        for operation, state in zip(sequence.operations, values, strict=True)
        if state == SequenceStepState.SUCCEEDED_VERIFIED
    )
    cancelled = tuple(
        operation.operation_id
        for operation, state in zip(sequence.operations, values, strict=True)
        if state == SequenceStepState.PENDING
    )
    return CancellationDecision(
        True,
        "cancelled_pre_dispatch",
        completed,
        cancelled,
    )
