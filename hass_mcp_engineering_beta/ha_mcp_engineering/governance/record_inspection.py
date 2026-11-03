"""HAMCP-253: bounded metadata view of one exact governance record.

The view is built from persisted metadata only: identifiers, timestamps,
statuses, policy class, fixed failure categories and the order of event
names. It never contains proposed or current configuration, targets, notes,
principals, tokens or free-form error text. It reads the record exactly as
stored: it does not resolve lifecycle, expire, recover or save anything, and
it works on records that the plan and task tools refuse.

Stored metadata is untrusted (R1-M2). The view names a record by the
validated requested identifier and reports a stored identifier only as a
fixed match or mismatch code. A field with a known closed vocabulary shows a
member of it or the fixed unrecognized marker. Any other field must be a
short token that the shared sanitizer, given the configured sensitive values,
leaves unchanged, or it is replaced by the same marker. Screening happens
before the view is fingerprinted or rendered.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ..errors import ErrorCode, GovernanceError
from ..sanitization import sanitize_untrusted_data
from .models import (
    ApprovalActionKind,
    ApprovalPolicyClass,
    ApprovalState,
    ChangeOperation,
    ChangePlan,
    PlanStatus,
)
from .projection_diagnostics import (
    PROJECTION_FAILURE_CATEGORY_MODEL,
    projection_failure_category,
)
from .storage import is_terminal_plan
from .task_models import ExecutionTask, ExecutionTaskState


INSPECTION_MODEL = "hamcp253-governance-record-inspection-v1"
MAX_INSPECTED_EVENTS = 64
MAX_HEALTH_ACKNOWLEDGEMENT_IDS = 8
_TOKEN = re.compile(r"^[a-z][a-z0-9_.-]{0,95}$")
_TIMESTAMP = re.compile(r"^[0-9T:.+\-Z ]{10,40}$")
_RECORD_ID = re.compile(r"^[a-f0-9]{32}$")
UNRECOGNIZED = "unrecognized_value"
STORED_IDENTITY_MATCH = "matches_request"
STORED_IDENTITY_MISMATCH = "stored_identifier_mismatch"
_OPERATIONS = frozenset(item.value for item in ChangeOperation)
_PLAN_STATUSES = frozenset(item.value for item in PlanStatus)
_TASK_STATES = frozenset(item.value for item in ExecutionTaskState)
_POLICY_CLASSES = frozenset(item.value for item in ApprovalPolicyClass)
_APPROVAL_STATES = frozenset(item.value for item in ApprovalState)
_APPROVAL_KINDS = frozenset(item.value for item in ApprovalActionKind)
_ERROR_CODES = frozenset(item.value for item in ErrorCode)
# Plans in these persisted states have already failed or been refused.
ACKNOWLEDGEABLE_PLAN_STATUSES = frozenset({
    PlanStatus.VALIDATION_FAILED,
    PlanStatus.FAILED,
    PlanStatus.VERIFICATION_FAILED,
    PlanStatus.ROLLBACK_FAILED,
    PlanStatus.REJECTED,
})
_APPROVAL_EVENT = re.compile(r"(approv|reject|challenge|supersed|prohibit)")


class _Screen:
    """Project one stored value as a fixed member, a screened token or marker."""

    def __init__(self, sensitive_values: tuple[str, ...]) -> None:
        self.sensitive_values = tuple(
            value for value in sensitive_values if isinstance(value, str) and value
        )

    def closed(self, value: Any, vocabulary: frozenset[str]) -> str | None:
        if value is None:
            return None
        text = getattr(value, "value", value)
        return text if isinstance(text, str) and text in vocabulary else UNRECOGNIZED

    def open(self, value: Any, pattern: re.Pattern[str] = _TOKEN) -> str | None:
        if value is None:
            return None
        text = getattr(value, "value", value)
        if not isinstance(text, str) or not pattern.fullmatch(text):
            return UNRECOGNIZED
        result = sanitize_untrusted_data(text, known_secrets=self.sensitive_values)
        if result.failed_closed or result.redaction_applied or result.value != text:
            return UNRECOGNIZED
        return text

    def timestamp(self, value: Any) -> str | None:
        return self.open(value, _TIMESTAMP)

    def record_id(self, value: Any) -> str | None:
        return self.open(value, _RECORD_ID)


def screened_record_id(value: Any, sensitive_values: tuple[str, ...]) -> str | None:
    """A stored identifier fit to show, or ``None`` when it is not."""

    shown = _Screen(sensitive_values).record_id(value)
    return None if shown in {None, UNRECOGNIZED} else shown


def _digest(rows: list[Any]) -> str:
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def record_fingerprint(view: dict[str, Any]) -> str:
    """Bind an acknowledgement to the exact persisted metadata it reviewed."""

    material = {
        key: value for key, value in view.items()
        if key not in {"record_fingerprint", "acknowledgement"}
    }
    return _digest([material])


def _event_rows(
    events: list[tuple[Any, ...]],
    fields: tuple[tuple[str, Any], ...],
) -> dict[str, Any]:
    rows = [
        {
            "order": index,
            **{name: project(value) for (name, project), value in zip(fields, row)},
        }
        for index, row in enumerate(events, 1)
    ]
    # Taken from every event, not only the displayed rows, with its own bound.
    approval = [
        row["event"] for row in rows
        if row["event"] and _APPROVAL_EVENT.search(row["event"])
    ]
    return {
        "approval_event_sequence": approval[:MAX_INSPECTED_EVENTS],
        "approval_event_count": len(approval),
        "approval_events_truncated": len(approval) > MAX_INSPECTED_EVENTS,
        "event_count": len(rows),
        "events": rows[:MAX_INSPECTED_EVENTS],
        "events_truncated": len(rows) > MAX_INSPECTED_EVENTS,
        # Covers every event, so truncation never hides a change.
        "event_sequence_digest": _digest(rows),
    }


def plan_view(service: Any, plan: ChangePlan, record_id: str) -> dict[str, Any]:
    """``record_id`` is the validated identifier the record was read by."""

    screen = _Screen(service.sensitive_values)
    identity = (
        STORED_IDENTITY_MATCH if plan.plan_id == record_id
        else STORED_IDENTITY_MISMATCH
    )
    unreadable: list[str] = []
    try:
        error = service._projection_failure_for_plan(plan)
        check_error = None
    except GovernanceError as exc:
        error, check_error = None, exc.code
    except Exception:
        error, check_error = None, None
        unreadable.append("projection_check")
    category = None
    if error is not None:
        category = projection_failure_category(plan, error)
    events: list[tuple[Any, ...]] = []
    try:
        events = [
            (event.event, event.result_status, event.error_code)
            for event in plan.events
        ]
    except Exception:
        unreadable.append("events")
    decision = plan.policy_decision
    policy_class = None
    try:
        policy_class = (
            screen.closed(decision.policy_class, _POLICY_CLASSES)
            if decision is not None else None
        )
    except Exception:
        unreadable.append("policy_class")
    approval: dict[str, Any] = {}
    try:
        approval = {
            "state": screen.closed(plan.approval.state, _APPROVAL_STATES),
            "approval_kind": screen.closed(
                plan.approval.approval_kind, _APPROVAL_KINDS
            ),
            "bundle_state": screen.open(plan.approval.bundle_state),
            "authority_version": (
                plan.approval.authority_version
                if type(plan.approval.authority_version) is int else None
            ),
            "consumed": plan.approval.consumed_at is not None,
        }
    except Exception:
        unreadable.append("approval")
    try:
        terminal = is_terminal_plan(plan)
    except Exception:
        terminal = None
        unreadable.append("terminal")
    acknowledgeable = identity == STORED_IDENTITY_MATCH and (
        error is not None or plan.status in ACKNOWLEDGEABLE_PLAN_STATUSES or (
            terminal is True
            and decision is not None
            and decision.policy_class == ApprovalPolicyClass.PROHIBITED
        )
    )
    event_rows = _event_rows(events, (
        ("event", screen.open),
        ("result_status", screen.open),
        ("error_code", screen.open),
    ))
    view = {
        "inspection_model": INSPECTION_MODEL,
        "record_kind": "change_plan",
        "record_id": record_id,
        "stored_identity": identity,
        "readable": True,
        "record_type": screen.closed(plan.operation, _OPERATIONS),
        "plan_family": screen.open(plan.plan_family),
        "contract_version": plan.contract_version if type(plan.contract_version) is int else None,
        "status": screen.closed(plan.status, _PLAN_STATUSES),
        "terminal": terminal,
        "created_at": screen.timestamp(plan.created_at),
        "updated_at": screen.timestamp(plan.updated_at),
        "expires_at": screen.timestamp(plan.expires_at),
        "policy_class": policy_class,
        "approval": approval,
        "failure": {
            "projection_failed": error is not None,
            "reason_code": screen.closed(error or check_error, _ERROR_CODES),
            "category": category,
            "category_model": PROJECTION_FAILURE_CATEGORY_MODEL,
            "execution_outcome": screen.open(plan.execution_outcome),
        },
        **event_rows,
        "unreadable_fields": unreadable,
        "acknowledgeable": bool(acknowledgeable),
    }
    view["record_fingerprint"] = record_fingerprint(view)
    return view


def task_view(
    task: ExecutionTask, record_id: str, *, sensitive_values: tuple[str, ...]
) -> dict[str, Any]:
    """``record_id`` is the validated identifier the record was read by."""

    screen = _Screen(sensitive_values)
    identity = (
        STORED_IDENTITY_MATCH if task.task_id == record_id
        else STORED_IDENTITY_MISMATCH
    )
    unreadable: list[str] = []
    events: list[tuple[Any, ...]] = []
    try:
        events = [
            (event.event_type, event.state_before, event.state_after)
            for event in task.events
        ]
    except Exception:
        unreadable.append("events")
    event_rows = _event_rows(events, (
        ("event", screen.open),
        ("state_before", lambda value: screen.closed(value, _TASK_STATES)),
        ("state_after", lambda value: screen.closed(value, _TASK_STATES)),
    ))
    manual_review = task.state == ExecutionTaskState.MANUAL_REVIEW_REQUIRED
    last_error = task.last_error if isinstance(task.last_error, dict) else {}
    view = {
        "inspection_model": INSPECTION_MODEL,
        "record_kind": "execution_task",
        "record_id": record_id,
        "stored_identity": identity,
        "readable": True,
        "record_type": screen.closed(task.operation, _OPERATIONS),
        "plan_id": screen.record_id(task.plan_id) or UNRECOGNIZED,
        "task_schema_version": (
            task.task_schema_version if type(task.task_schema_version) is int else None
        ),
        "status": screen.closed(task.state, _TASK_STATES),
        "terminal_outcome": screen.open(task.terminal_outcome),
        "created_at": screen.timestamp(task.created_at),
        "updated_at": screen.timestamp(task.updated_at),
        "completed_at": screen.timestamp(task.completed_at),
        "policy_class": screen.closed(
            task.approval_reference.get("policy_class")
            if isinstance(task.approval_reference, dict) else None,
            _POLICY_CLASSES,
        ),
        "failure": {
            "manual_review_required": manual_review,
            "reason_code": screen.open(task.manual_review_reason),
            "last_error_category": screen.open(last_error.get("failure_category")),
        },
        **event_rows,
        "unreadable_fields": unreadable,
        "acknowledgeable": bool(
            manual_review and identity == STORED_IDENTITY_MATCH
        ),
    }
    view["record_fingerprint"] = record_fingerprint(view)
    return view


def unreadable_view(record_kind: str, record_id: str, code: ErrorCode) -> dict[str, Any]:
    """A stored record that cannot be decoded: report it, never guess it."""

    return {
        "inspection_model": INSPECTION_MODEL,
        "record_kind": record_kind,
        "record_id": record_id,
        "readable": False,
        "unreadable_reason": code.value,
        "unreadable_fields": ["record"],
        "acknowledgeable": False,
        "record_fingerprint": None,
    }
