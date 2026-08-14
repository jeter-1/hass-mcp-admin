"""Isolated bounded operational metrics and event interfaces for F3-C2."""

from __future__ import annotations

from collections import Counter, deque
from copy import deepcopy
import re
import threading
from typing import Any

from .operational_models import SUPPORTED_OPERATIONS


COMMON_COUNTERS = frozenset(
    {
        "preparations",
        "preflight_attempts",
        "preflight_rejections",
        "provider_admission_failures",
        "lock_conflicts",
        "stale_state_failures",
        "intents_committed",
        "intent_failures",
        "dispatch_attempts",
        "confirmed_dispatch_failures",
        "indeterminate_dispatches",
        "responses_received",
        "responses_lost",
        "observations",
        "verification_successes",
        "verification_mismatches",
        "manual_review_transitions",
        "duplicate_execution_preventions",
        "blind_redispatch_preventions",
        "cancellations",
        "reconciliations",
        "fallbacks",
    }
)

OPERATION_COUNTERS = {
    "create_full_backup": frozenset(
        {"inventory_reads", "new_backup_detections", "ambiguous_backup_outcomes"}
    ),
    "controlled_reload": frozenset(
        {"configuration_checks", "service_checks", "domain_inventory_reads"}
    ),
    "restart_addon": frozenset(
        {
            "identity_bindings",
            "legacy_response_models",
            "structured_response_models",
            "reconnect_observations",
            "readmission_observations",
        }
    ),
    "restart_home_assistant": frozenset(
        {
            "cheap_eligibility_checks",
            "expensive_probes",
            "expensive_probes_avoided",
            "backoff_events",
            "evidence_deadline_expirations",
            "terminal_reconciliations",
        }
    ),
    "set_input_boolean_state": frozenset({"state_reads"}),
}

_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,95}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
MAX_EVENTS = 256
MAX_EVENT_FIELDS = 12


class OperationalMetrics:
    """Thread-safe per-operation counters with a closed vocabulary."""

    def __init__(self) -> None:
        self._values = {
            operation: Counter(
                {
                    name: 0
                    for name in sorted(
                        COMMON_COUNTERS | OPERATION_COUNTERS[operation]
                    )
                }
            )
            for operation in SUPPORTED_OPERATIONS
        }
        self._lock = threading.Lock()

    def increment(self, operation: str, name: str, amount: int = 1) -> None:
        if operation not in self._values:
            raise ValueError("unknown operational metric operation")
        if name not in self._values[operation] or type(amount) is not int or amount < 0:
            raise ValueError("unknown or invalid operational metric")
        with self._lock:
            self._values[operation][name] += amount

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {
                operation: dict(sorted(values.items()))
                for operation, values in sorted(self._values.items())
            }


class OperationalEventRecorder:
    """Retain only closed classifications, identifiers, counts, and hashes."""

    _allowed_fields = frozenset(
        {
            "event_type",
            "operation",
            "capability_id",
            "task_id",
            "plan_id",
            "target_type",
            "reason_code",
            "outcome",
            "response_contract_model",
            "dispatch_count",
            "observation_count",
            "verification_count",
        }
    )

    def __init__(self, *, max_events: int = MAX_EVENTS) -> None:
        if not 1 <= max_events <= MAX_EVENTS:
            raise ValueError("operational event bound is invalid")
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._lock = threading.Lock()

    @staticmethod
    def _safe_value(key: str, value: Any) -> str | int:
        if key.endswith("_count"):
            if type(value) is not int or value < 0:
                raise ValueError("operational event count is invalid")
            return value
        if not isinstance(value, str):
            raise ValueError("operational event text is invalid")
        pattern = _SAFE_ID if key in {"task_id", "plan_id"} else _SAFE_CODE
        if not pattern.fullmatch(value):
            raise ValueError("operational event classification is invalid")
        return value

    def emit(self, event: dict[str, Any]) -> None:
        if (
            not isinstance(event, dict)
            or not event
            or len(event) > MAX_EVENT_FIELDS
            or set(event) - self._allowed_fields
        ):
            raise ValueError("operational event fields are not bounded")
        safe = {
            key: self._safe_value(key, value)
            for key, value in sorted(event.items())
        }
        with self._lock:
            self._events.append(safe)

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(deepcopy(tuple(self._events)))


def null_operational_event_sink(_event: dict[str, Any]) -> None:
    """Default sink retains no provider-derived evidence."""
