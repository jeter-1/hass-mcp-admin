"""Versioned durable execution-task records and deterministic materialization."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from .models import ChangeOperation


TASK_SCHEMA_VERSION = 1
MAX_TASK_EVENTS = 512
MAX_PROVIDER_ATTEMPTS = 32
MAX_TASK_TEXT = 320


class ExecutionTaskState(str, Enum):
    CREATED = "created"
    PREFLIGHT = "preflight"
    DISPATCHING = "dispatching"
    OBSERVING = "observing"
    VERIFYING = "verifying"
    SUCCEEDED_VERIFIED = "succeeded_verified"
    FAILED_PRE_DISPATCH = "failed_pre_dispatch"
    FAILED_POST_DISPATCH = "failed_post_dispatch"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    CANCELLED_PRE_DISPATCH = "cancelled_pre_dispatch"
    # Reserved for later schema-compatible features. F1 exposes no transitions
    # to these states.
    WAITING_FOR_LOCK = "waiting_for_lock"
    COMPENSATING = "compensating"
    PARTIAL_APPLICATION = "partial_application"
    COMPENSATED = "compensated"
    SUPERSEDED = "superseded"


TERMINAL_TASK_STATES = frozenset(
    {
        ExecutionTaskState.SUCCEEDED_VERIFIED,
        ExecutionTaskState.FAILED_PRE_DISPATCH,
        ExecutionTaskState.FAILED_POST_DISPATCH,
        ExecutionTaskState.MANUAL_REVIEW_REQUIRED,
        ExecutionTaskState.CANCELLED_PRE_DISPATCH,
    }
)
RESERVED_TASK_STATES = frozenset(
    {
        ExecutionTaskState.WAITING_FOR_LOCK,
        ExecutionTaskState.COMPENSATING,
        ExecutionTaskState.PARTIAL_APPLICATION,
        ExecutionTaskState.COMPENSATED,
        ExecutionTaskState.SUPERSEDED,
    }
)
SINGLE_DISPATCH_OPERATIONS = frozenset(
    {
        ChangeOperation.CREATE_FULL_BACKUP.value,
        ChangeOperation.CONTROLLED_RELOAD.value,
        ChangeOperation.RESTART_ADDON.value,
        ChangeOperation.RESTART_HOME_ASSISTANT.value,
        ChangeOperation.SET_INPUT_BOOLEAN_STATE.value,
        ChangeOperation.UPDATE_DASHBOARD.value,
    }
)
TERMINAL_OBSERVATION_EVENTS = frozenset(
    {"duplicate_apply_prevented", "task_cancellation_rejected"}
)
ALLOWED_TASK_TRANSITIONS: dict[
    ExecutionTaskState, frozenset[ExecutionTaskState]
] = {
    ExecutionTaskState.CREATED: frozenset(
        {
            ExecutionTaskState.PREFLIGHT,
            ExecutionTaskState.FAILED_PRE_DISPATCH,
            ExecutionTaskState.CANCELLED_PRE_DISPATCH,
        }
    ),
    ExecutionTaskState.PREFLIGHT: frozenset(
        {
            ExecutionTaskState.DISPATCHING,
            ExecutionTaskState.SUCCEEDED_VERIFIED,
            ExecutionTaskState.FAILED_PRE_DISPATCH,
            ExecutionTaskState.CANCELLED_PRE_DISPATCH,
        }
    ),
    ExecutionTaskState.DISPATCHING: frozenset(
        {
            ExecutionTaskState.OBSERVING,
            ExecutionTaskState.VERIFYING,
            ExecutionTaskState.FAILED_POST_DISPATCH,
            ExecutionTaskState.MANUAL_REVIEW_REQUIRED,
        }
    ),
    ExecutionTaskState.OBSERVING: frozenset(
        {
            ExecutionTaskState.VERIFYING,
            ExecutionTaskState.SUCCEEDED_VERIFIED,
            ExecutionTaskState.FAILED_POST_DISPATCH,
            ExecutionTaskState.MANUAL_REVIEW_REQUIRED,
        }
    ),
    ExecutionTaskState.VERIFYING: frozenset(
        {
            ExecutionTaskState.OBSERVING,
            ExecutionTaskState.SUCCEEDED_VERIFIED,
            ExecutionTaskState.FAILED_POST_DISPATCH,
            ExecutionTaskState.MANUAL_REVIEW_REQUIRED,
        }
    ),
}


def parse_task_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("invalid execution-task timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("execution-task timestamps must be timezone-aware")
    return parsed


def _bounded(value: Any, *, depth: int = 0) -> Any:
    """Bound event evidence without persisting arbitrary provider payloads."""

    if depth > 4:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_TASK_TEXT]
    if isinstance(value, list):
        return [_bounded(item, depth=depth + 1) for item in value[:32]]
    if isinstance(value, dict):
        return {
            str(key)[:80]: _bounded(item, depth=depth + 1)
            for key, item in list(value.items())[:64]
        }
    return str(type(value).__name__)[:MAX_TASK_TEXT]


def _require_single_dispatch_history(
    *,
    operation: str,
    events: list["ExecutionTaskEvent"],
    provider_attempts: list[dict[str, Any]],
) -> None:
    """Keep irreversible operational tasks to one durable dispatch lineage."""

    if operation not in SINGLE_DISPATCH_OPERATIONS:
        return
    dispatch_lineages = sum(
        event.event_type == "dispatch_attempted" for event in events
    )
    if dispatch_lineages > 1 or len(provider_attempts) > 1:
        raise ValueError(
            "single-dispatch execution task has multiple provider attempts"
        )


@dataclass
class ExecutionTaskEvent:
    sequence: int
    event_type: str
    occurred_at: str
    state_before: str | None
    state_after: str
    changes: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "state_before": self.state_before,
            "state_after": self.state_after,
            "changes": _bounded(self.changes),
            "request_id": self.request_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExecutionTaskEvent":
        if not isinstance(value, dict):
            raise ValueError("invalid execution-task event")
        event = cls(
            sequence=int(value["sequence"]),
            event_type=str(value["event_type"]),
            occurred_at=str(value["occurred_at"]),
            state_before=(
                str(value["state_before"])
                if value.get("state_before") is not None
                else None
            ),
            state_after=str(value["state_after"]),
            changes=dict(value.get("changes") or {}),
            request_id=(
                str(value["request_id"])
                if value.get("request_id") is not None
                else None
            ),
        )
        parse_task_timestamp(event.occurred_at)
        if event.sequence < 1:
            raise ValueError("invalid execution-task event sequence")
        if len(event.event_type) > 96:
            raise ValueError("execution-task event type is too long")
        return event


@dataclass
class ExecutionTask:
    task_id: str
    task_schema_version: int
    plan_id: str
    plan_hash: str
    operation: str
    target: dict[str, Any]
    created_at: str
    updated_at: str
    started_at: str | None
    dispatched_at: str | None
    completed_at: str | None
    state: ExecutionTaskState
    terminal_outcome: str | None
    execution_request_id: str
    idempotency_key: str
    approval_reference: dict[str, Any]
    provider_attempts: list[dict[str, Any]]
    verification_summary: dict[str, Any]
    last_error: dict[str, Any] | None
    manual_review_reason: str | None
    maximum_post_dispatch_deadline: str | None
    legacy_projection: dict[str, Any]
    events: list[ExecutionTaskEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_schema_version": self.task_schema_version,
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
            "operation": self.operation,
            "target": _bounded(self.target),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "dispatched_at": self.dispatched_at,
            "completed_at": self.completed_at,
            "state": self.state.value,
            "terminal_outcome": self.terminal_outcome,
            "execution_request_id": self.execution_request_id,
            "idempotency_key": self.idempotency_key,
            "approval_reference": _bounded(self.approval_reference),
            "provider_attempts": _bounded(self.provider_attempts),
            "verification_summary": _bounded(self.verification_summary),
            "last_error": _bounded(self.last_error),
            "manual_review_reason": self.manual_review_reason,
            "maximum_post_dispatch_deadline": (
                self.maximum_post_dispatch_deadline
            ),
            "legacy_projection": _bounded(self.legacy_projection),
            "events": [event.to_dict() for event in self.events],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExecutionTask":
        if not isinstance(value, dict):
            raise ValueError("invalid execution-task record")
        task = cls(
            task_id=str(value["task_id"]),
            task_schema_version=int(value["task_schema_version"]),
            plan_id=str(value["plan_id"]),
            plan_hash=str(value["plan_hash"]),
            operation=str(value["operation"]),
            target=dict(value.get("target") or {}),
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
            started_at=(
                str(value["started_at"])
                if value.get("started_at") is not None
                else None
            ),
            dispatched_at=(
                str(value["dispatched_at"])
                if value.get("dispatched_at") is not None
                else None
            ),
            completed_at=(
                str(value["completed_at"])
                if value.get("completed_at") is not None
                else None
            ),
            state=ExecutionTaskState(str(value["state"])),
            terminal_outcome=(
                str(value["terminal_outcome"])
                if value.get("terminal_outcome") is not None
                else None
            ),
            execution_request_id=str(value["execution_request_id"]),
            idempotency_key=str(value["idempotency_key"]),
            approval_reference=dict(value.get("approval_reference") or {}),
            provider_attempts=[
                dict(item)
                for item in list(value.get("provider_attempts") or [])
            ],
            verification_summary=dict(
                value.get("verification_summary") or {}
            ),
            last_error=(
                dict(value["last_error"])
                if isinstance(value.get("last_error"), dict)
                else None
            ),
            manual_review_reason=(
                str(value["manual_review_reason"])
                if value.get("manual_review_reason") is not None
                else None
            ),
            maximum_post_dispatch_deadline=(
                str(value["maximum_post_dispatch_deadline"])
                if value.get("maximum_post_dispatch_deadline") is not None
                else None
            ),
            legacy_projection=dict(value.get("legacy_projection") or {}),
            events=[
                ExecutionTaskEvent.from_dict(item)
                for item in list(value.get("events") or [])
            ],
        )
        task.validate()
        return task

    def validate(self) -> None:
        if self.task_schema_version != TASK_SCHEMA_VERSION:
            raise ValueError("unsupported execution-task schema")
        if len(self.events) > MAX_TASK_EVENTS:
            raise ValueError("execution-task event limit exceeded")
        if len(self.provider_attempts) > MAX_PROVIDER_ATTEMPTS:
            raise ValueError("execution-task provider-attempt limit exceeded")
        _require_single_dispatch_history(
            operation=self.operation,
            events=self.events,
            provider_attempts=self.provider_attempts,
        )
        for timestamp in (
            self.created_at,
            self.updated_at,
            self.started_at,
            self.dispatched_at,
            self.completed_at,
            self.maximum_post_dispatch_deadline,
        ):
            if timestamp is not None:
                parse_task_timestamp(timestamp)
        if self.dispatched_at is not None:
            if self.maximum_post_dispatch_deadline is None:
                raise ValueError(
                    "dispatched execution task is missing its deadline"
                )
            if parse_task_timestamp(
                self.maximum_post_dispatch_deadline
            ) != parse_task_timestamp(self.dispatched_at) + timedelta(
                hours=24
            ):
                raise ValueError(
                    "execution-task post-dispatch deadline is inconsistent"
                )
            dispatch_events = [
                event
                for event in self.events
                if event.event_type == "dispatch_attempted"
            ]
            approval_events = [
                event
                for event in self.events
                if event.event_type == "approval_consumed"
            ]
            if (
                not dispatch_events
                or not approval_events
                or approval_events[0].sequence
                >= dispatch_events[0].sequence
                or self.approval_reference.get("approval_state")
                != "consumed"
                or not self.provider_attempts
                or dispatch_events[0].changes.get("dispatched_at")
                != self.dispatched_at
            ):
                raise ValueError(
                    "execution-task irreversible history is contradictory"
                )
            for index, attempt in enumerate(
                self.provider_attempts, start=1
            ):
                if (
                    attempt.get("attempt") != index
                    or not attempt.get("provider")
                ):
                    raise ValueError(
                        "execution-task provider attempt is invalid"
                    )
                parse_task_timestamp(attempt.get("attempted_at"))
        elif self.maximum_post_dispatch_deadline is not None:
            raise ValueError(
                "pre-dispatch execution task cannot have a deadline"
            )
        elif self.provider_attempts:
            raise ValueError(
                "pre-dispatch execution task has provider attempts"
            )
        if self.state in RESERVED_TASK_STATES:
            raise ValueError("reserved execution-task state is not reachable")
        if not self.events or self.events[0].event_type != "task_created":
            raise ValueError("execution-task creation event is missing")
        materialized = materialize_execution_task(self.events)
        comparable = self.to_dict()
        comparable.pop("events", None)
        if comparable != materialized:
            raise ValueError("execution-task materialization mismatch")

    def append_event(
        self,
        event_type: str,
        occurred_at: str,
        *,
        new_state: ExecutionTaskState | None = None,
        changes: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        parse_task_timestamp(occurred_at)
        target_state = new_state or self.state
        if target_state != self.state:
            allowed = ALLOWED_TASK_TRANSITIONS.get(self.state, frozenset())
            if target_state not in allowed:
                raise ValueError(
                    f"illegal execution-task transition: "
                    f"{self.state.value}->{target_state.value}"
                )
        elif (
            self.state in TERMINAL_TASK_STATES
            and event_type not in TERMINAL_OBSERVATION_EVENTS
        ):
            raise ValueError("terminal execution task cannot be changed")
        safe_changes = dict(_bounded(changes or {}))
        safe_changes["updated_at"] = occurred_at
        safe_changes["state"] = target_state.value
        event = ExecutionTaskEvent(
            sequence=len(self.events) + 1,
            event_type=event_type,
            occurred_at=occurred_at,
            state_before=self.state.value,
            state_after=target_state.value,
            changes=safe_changes,
            request_id=request_id,
        )
        _require_single_dispatch_history(
            operation=self.operation,
            events=[*self.events, event],
            provider_attempts=[
                dict(item)
                for item in list(
                    safe_changes.get(
                        "provider_attempts", self.provider_attempts
                    )
                    or []
                )
            ],
        )
        self.events.append(event)
        _apply_event_changes(self, safe_changes)


def _apply_event_changes(task: ExecutionTask, changes: dict[str, Any]) -> None:
    for key, value in changes.items():
        if key == "state":
            task.state = ExecutionTaskState(str(value))
        elif key == "provider_attempts":
            task.provider_attempts = [
                dict(item) for item in list(value or [])
            ]
        elif key in {
            "target",
            "approval_reference",
            "verification_summary",
            "legacy_projection",
        }:
            setattr(task, key, dict(value or {}))
        elif key == "last_error":
            task.last_error = dict(value) if isinstance(value, dict) else None
        elif hasattr(task, key) and key != "events":
            setattr(task, key, value)


def materialize_execution_task(
    events: list[ExecutionTaskEvent],
) -> dict[str, Any]:
    if not events:
        raise ValueError("execution-task event history is empty")
    record: dict[str, Any] = {}
    prior_state: str | None = None
    for index, event in enumerate(events, start=1):
        if event.sequence != index:
            raise ValueError("execution-task event sequence is not contiguous")
        parse_task_timestamp(event.occurred_at)
        if index == 1:
            if event.event_type != "task_created" or event.state_before is not None:
                raise ValueError("invalid execution-task creation event")
        elif event.state_before != prior_state:
            raise ValueError("execution-task event state chain is invalid")
        state_after = ExecutionTaskState(event.state_after)
        if state_after in RESERVED_TASK_STATES:
            raise ValueError("reserved execution-task state is not reachable")
        if prior_state is not None and state_after.value != prior_state:
            before = ExecutionTaskState(prior_state)
            if state_after not in ALLOWED_TASK_TRANSITIONS.get(
                before, frozenset()
            ):
                raise ValueError("illegal execution-task event transition")
        elif (
            prior_state is not None
            and ExecutionTaskState(prior_state) in TERMINAL_TASK_STATES
            and event.event_type not in TERMINAL_OBSERVATION_EVENTS
        ):
            raise ValueError("terminal execution-task event history changed")
        record.update(deepcopy(event.changes))
        record["state"] = state_after.value
        prior_state = state_after.value
    _require_single_dispatch_history(
        operation=str(record.get("operation") or ""),
        events=events,
        provider_attempts=[
            dict(item)
            for item in list(record.get("provider_attempts") or [])
        ],
    )
    return record


def new_execution_task(
    *,
    task_id: str,
    plan_id: str,
    plan_hash: str,
    operation: str,
    target: dict[str, Any],
    timestamp: str,
    execution_request_id: str,
    idempotency_key: str,
    approval_reference: dict[str, Any],
    legacy_projection: dict[str, Any],
) -> ExecutionTask:
    parse_task_timestamp(timestamp)
    initial = {
        "task_id": task_id,
        "task_schema_version": TASK_SCHEMA_VERSION,
        "plan_id": plan_id,
        "plan_hash": plan_hash,
        "operation": operation,
        "target": _bounded(target),
        "created_at": timestamp,
        "updated_at": timestamp,
        "started_at": None,
        "dispatched_at": None,
        "completed_at": None,
        "state": ExecutionTaskState.CREATED.value,
        "terminal_outcome": None,
        "execution_request_id": execution_request_id,
        "idempotency_key": idempotency_key,
        "approval_reference": _bounded(approval_reference),
        "provider_attempts": [],
        "verification_summary": {},
        "last_error": None,
        "manual_review_reason": None,
        "maximum_post_dispatch_deadline": None,
        "legacy_projection": _bounded(legacy_projection),
    }
    event = ExecutionTaskEvent(
        sequence=1,
        event_type="task_created",
        occurred_at=timestamp,
        state_before=None,
        state_after=ExecutionTaskState.CREATED.value,
        changes=initial,
        request_id=execution_request_id,
    )
    task = ExecutionTask.from_dict({**initial, "events": [event.to_dict()]})
    return task
