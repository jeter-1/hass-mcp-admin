"""HAMCP-253: bounded metadata view of one exact governance record.

The view is built from persisted metadata only: identifiers, timestamps,
statuses, policy class, fixed failure categories and the order of event
names. It never contains proposed or current configuration, targets, notes,
principals, tokens or free-form error text. It reads the record exactly as
stored: it does not resolve lifecycle, expire, recover or save anything, and
it works on records that the plan and task tools refuse.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ..errors import ErrorCode, GovernanceError
from .models import ApprovalPolicyClass, ChangePlan, PlanStatus
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
UNRECOGNIZED = "unrecognized_value"
# Plans in these persisted states have already failed or been refused.
ACKNOWLEDGEABLE_PLAN_STATUSES = frozenset({
    PlanStatus.VALIDATION_FAILED,
    PlanStatus.FAILED,
    PlanStatus.VERIFICATION_FAILED,
    PlanStatus.ROLLBACK_FAILED,
    PlanStatus.REJECTED,
})
_APPROVAL_EVENT = re.compile(r"(approv|reject|challenge|supersed|prohibit)")


def _token(value: Any) -> str | None:
    if value is None:
        return None
    text = getattr(value, "value", value)
    return text if isinstance(text, str) and _TOKEN.fullmatch(text) else UNRECOGNIZED


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) and _TIMESTAMP.fullmatch(value) else UNRECOGNIZED


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


def _event_rows(events: list[tuple[Any, ...]], fields: tuple[str, ...]) -> dict[str, Any]:
    rows = [
        {"order": index, **{name: _token(value) for name, value in zip(fields, row)}}
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


def plan_view(service: Any, plan: ChangePlan) -> dict[str, Any]:
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
        policy_class = _token(decision.policy_class) if decision is not None else None
    except Exception:
        unreadable.append("policy_class")
    approval: dict[str, Any] = {}
    try:
        approval = {
            "state": _token(plan.approval.state),
            "approval_kind": _token(plan.approval.approval_kind),
            "bundle_state": _token(plan.approval.bundle_state),
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
    acknowledgeable = error is not None or plan.status in ACKNOWLEDGEABLE_PLAN_STATUSES or (
        terminal is True
        and decision is not None
        and decision.policy_class == ApprovalPolicyClass.PROHIBITED
    )
    event_rows = _event_rows(events, ("event", "result_status", "error_code"))
    view = {
        "inspection_model": INSPECTION_MODEL,
        "record_kind": "change_plan",
        "record_id": plan.plan_id,
        "readable": True,
        "record_type": _token(plan.operation),
        "plan_family": _token(plan.plan_family),
        "contract_version": plan.contract_version if type(plan.contract_version) is int else None,
        "status": _token(plan.status),
        "terminal": terminal,
        "created_at": _timestamp(plan.created_at),
        "updated_at": _timestamp(plan.updated_at),
        "expires_at": _timestamp(plan.expires_at),
        "policy_class": policy_class,
        "approval": approval,
        "failure": {
            "projection_failed": error is not None,
            "reason_code": _token(error or check_error),
            "category": category,
            "category_model": PROJECTION_FAILURE_CATEGORY_MODEL,
            "execution_outcome": _token(plan.execution_outcome),
        },
        **event_rows,
        "unreadable_fields": unreadable,
        "acknowledgeable": bool(acknowledgeable),
    }
    view["record_fingerprint"] = record_fingerprint(view)
    return view


def task_view(task: ExecutionTask) -> dict[str, Any]:
    unreadable: list[str] = []
    events: list[tuple[Any, ...]] = []
    try:
        events = [
            (event.event_type, event.state_before, event.state_after)
            for event in task.events
        ]
    except Exception:
        unreadable.append("events")
    event_rows = _event_rows(events, ("event", "state_before", "state_after"))
    manual_review = task.state == ExecutionTaskState.MANUAL_REVIEW_REQUIRED
    last_error = task.last_error if isinstance(task.last_error, dict) else {}
    view = {
        "inspection_model": INSPECTION_MODEL,
        "record_kind": "execution_task",
        "record_id": task.task_id,
        "readable": True,
        "record_type": _token(task.operation),
        "plan_id": task.plan_id if re.fullmatch(r"[a-f0-9]{32}", str(task.plan_id)) else UNRECOGNIZED,
        "task_schema_version": (
            task.task_schema_version if type(task.task_schema_version) is int else None
        ),
        "status": _token(task.state),
        "terminal_outcome": _token(task.terminal_outcome),
        "created_at": _timestamp(task.created_at),
        "updated_at": _timestamp(task.updated_at),
        "completed_at": _timestamp(task.completed_at),
        "policy_class": _token(
            task.approval_reference.get("policy_class")
            if isinstance(task.approval_reference, dict) else None
        ),
        "failure": {
            "manual_review_required": manual_review,
            "reason_code": _token(task.manual_review_reason),
            "last_error_category": _token(last_error.get("failure_category")),
        },
        **event_rows,
        "unreadable_fields": unreadable,
        "acknowledgeable": manual_review,
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
