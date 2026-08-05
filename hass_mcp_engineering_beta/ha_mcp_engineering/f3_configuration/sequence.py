"""Pure ordered-plan conformance models; not an executor or lock manager."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ha_mcp_engineering.f3.contracts import LockRequest

from ..governance.normalize import stable_hash
from .locks import complete_configuration_lock_set, lock_set_hash
from .models import PreparedConfigurationOperation


class SequenceStepState(str, Enum):
    NOT_STARTED = "not_started"
    INTENT_COMMITTED = "intent_committed"
    OBSERVING = "observing"
    SUCCEEDED_VERIFIED = "succeeded_verified"
    FAILED_PRE_DISPATCH = "failed_pre_dispatch"
    FAILED_POST_DISPATCH = "failed_post_dispatch"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    CANCELLED_PRE_DISPATCH = "cancelled_pre_dispatch"
    LATER_OPERATIONS_UNDISPATCHED = "later_operations_undispatched"
    BLOCKED_BY_PRIOR_OUTCOME = "blocked_by_prior_outcome"


class SequenceNextAction(str, Enum):
    NOT_STARTED = "not_started"
    OBSERVE = "observe"
    COMPLETE = "complete"
    STOP = "stop"


@dataclass(frozen=True)
class ConfigurationChildExecutionDescriptor:
    """Deterministic future child identity; never a persisted F3 execution."""

    public_task_id: str
    plan_id: str
    operation_id: str
    attempt_id: str
    order: int
    prepared_operation_hash: str
    descriptor_hash: str


@dataclass(frozen=True)
class PreparedConfigurationSequence:
    plan_id: str
    task_id: str
    operations: tuple[PreparedConfigurationOperation, ...]
    child_descriptors: tuple[ConfigurationChildExecutionDescriptor, ...]
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
    dispatch_authorized: bool = False


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
    undispatched_operation_ids: tuple[str, ...]


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
    seen_targets: set[tuple[str, str]] = set()
    for index, operation in enumerate(ordered):
        if operation.order != index:
            raise ValueError("configuration operation order is not canonical")
        if operation.operation_id in seen:
            raise ValueError("configuration operation IDs must be unique")
        if len(operation.depends_on) != len(set(operation.depends_on)) or any(
            dependency not in seen for dependency in operation.depends_on
        ):
            raise ValueError("dependencies must name unique earlier operations")
        target = (
            operation.target.target_type,
            operation.target.target_id,
        )
        if target in seen_targets:
            raise ValueError("configuration sequence targets must be unique")
        seen.add(operation.operation_id)
        seen_targets.add(target)
    child_descriptors = tuple(
        _child_descriptor(operation) for operation in ordered
    )
    if len({item.attempt_id for item in child_descriptors}) != len(ordered):
        raise ValueError("configuration child attempt identities are not unique")
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
            "child_descriptor_hashes": [
                item.descriptor_hash for item in child_descriptors
            ],
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
        child_descriptors=child_descriptors,
        lock_requests=requests,
        lock_set_hash=locks_hash,
        sequence_hash=sequence_hash,
    )


def _child_descriptor(
    operation: PreparedConfigurationOperation,
) -> ConfigurationChildExecutionDescriptor:
    identity_payload = {
        "model": "f3-configuration-child-execution-v1",
        "public_task_id": operation.task_id,
        "plan_id": operation.plan_id,
        "operation_id": operation.operation_id,
        "order": operation.order,
        "prepared_operation_hash": operation.prepared_operation_hash,
    }
    attempt_id = (
        f"attempt-{operation.order}-{stable_hash(identity_payload)[:24]}"
    )
    descriptor_hash = stable_hash(
        {**identity_payload, "attempt_id": attempt_id}
    )
    return ConfigurationChildExecutionDescriptor(
        public_task_id=operation.task_id,
        plan_id=operation.plan_id,
        operation_id=operation.operation_id,
        attempt_id=attempt_id,
        order=operation.order,
        prepared_operation_hash=operation.prepared_operation_hash,
        descriptor_hash=descriptor_hash,
    )


def single_operation_child_descriptor(
    sequence: PreparedConfigurationSequence,
) -> ConfigurationChildExecutionDescriptor:
    """Return the only F3-A-compatible child or reject a multi-write claim."""

    if len(sequence.child_descriptors) != 1:
        raise ValueError(
            "a multi-operation plan cannot use one F3 execution record"
        )
    return sequence.child_descriptors[0]


def recover_sequence_position(
    sequence: PreparedConfigurationSequence,
    states: tuple[SequenceStepState, ...] | list[SequenceStepState],
) -> SequenceRecoveryDecision:
    """Describe position only; never authorize dispatch after reconstruction."""

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
            if values[candidate.order]
            in {
                SequenceStepState.NOT_STARTED,
                SequenceStepState.LATER_OPERATIONS_UNDISPATCHED,
                SequenceStepState.BLOCKED_BY_PRIOR_OUTCOME,
            }
        )
        if state == SequenceStepState.SUCCEEDED_VERIFIED:
            completed.append(operation.operation_id)
            continue
        if state == SequenceStepState.NOT_STARTED:
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
                    False,
                )
            return SequenceRecoveryDecision(
                SequenceNextAction.NOT_STARTED,
                operation.operation_id,
                None,
                tuple(completed),
                later,
                False,
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
                False,
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
            False,
        )
    return SequenceRecoveryDecision(
        SequenceNextAction.COMPLETE,
        None,
        "succeeded_verified",
        tuple(completed),
        (),
        True,
        False,
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
    """Allow task cancellation only before any child can have dispatched."""

    values = tuple(states)
    if len(values) != len(sequence.operations):
        raise ValueError("sequence state count is inconsistent")
    completed = tuple(
        operation.operation_id
        for operation, state in zip(sequence.operations, values, strict=True)
        if state == SequenceStepState.SUCCEEDED_VERIFIED
    )
    undispatched = tuple(
        operation.operation_id
        for operation, state in zip(sequence.operations, values, strict=True)
        if state
        in {
            SequenceStepState.NOT_STARTED,
            SequenceStepState.LATER_OPERATIONS_UNDISPATCHED,
            SequenceStepState.BLOCKED_BY_PRIOR_OUTCOME,
        }
    )
    if any(state != SequenceStepState.NOT_STARTED for state in values):
        return CancellationDecision(
            False,
            "cancellation_rejected_after_possible_dispatch",
            completed,
            (),
            undispatched,
        )
    return CancellationDecision(
        True,
        "cancelled_pre_dispatch",
        (),
        undispatched,
        (),
    )
