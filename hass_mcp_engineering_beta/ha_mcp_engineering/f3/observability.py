"""Isolated bounded metrics and event interfaces for F3-A."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import threading
from typing import Any

from .models import EVIDENCE_PATTERN, validate_identifier, validate_lock_key


LOCK_COUNTERS = frozenset(
    {
        "acquisition_attempts",
        "acquisitions",
        "conflicts",
        "wait_timeouts",
        "renewals",
        "renewal_failures",
        "releases",
        "release_failures",
        "stale_recoveries",
        "corrupted_records",
        "fencing_rejections",
    }
)

EXECUTOR_COUNTERS = frozenset(
    {
        "executions_started",
        "preflight_rejections",
        "preflight_noop_successes",
        "durable_intents_committed",
        "durable_intent_failures",
        "dispatch_attempts",
        "confirmed_dispatch_failures",
        "indeterminate_dispatches",
        "observations",
        "verification_successes",
        "verification_mismatches",
        "manual_review_transitions",
        "duplicate_execution_preventions",
        "blind_redispatch_preventions",
        "cancellations",
    }
)

MAX_EVENT_COUNT = 256
MAX_EVENT_FIELDS = 16
MAX_EVENT_TEXT = 128


class CounterSnapshot:
    """Thread-safe process-local counters with a closed key vocabulary."""

    def __init__(self, allowed: frozenset[str]):
        self._allowed = allowed
        self._values = {name: 0 for name in sorted(allowed)}
        self._lock = threading.Lock()

    def increment(self, name: str, amount: int = 1) -> None:
        if name not in self._allowed or amount < 0:
            raise ValueError("counter update is invalid")
        with self._lock:
            self._values[name] += amount

    def snapshot(self, **gauges: int) -> dict[str, int]:
        if any(
            not isinstance(value, int) or value < 0
            for value in gauges.values()
        ):
            raise ValueError("counter gauge is invalid")
        with self._lock:
            result = dict(self._values)
        result.update(gauges)
        return result


class LockMetrics(CounterSnapshot):
    def __init__(self) -> None:
        super().__init__(LOCK_COUNTERS)


class ExecutorMetrics(CounterSnapshot):
    def __init__(self) -> None:
        super().__init__(EXECUTOR_COUNTERS)


def _safe_event_value(key: str, value: object) -> object:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if not isinstance(value, str):
        return type(value).__name__[:MAX_EVENT_TEXT]
    if key == "lock_key":
        return validate_lock_key(value)
    if key in {
        "task_id",
        "plan_id",
        "attempt_id",
        "owner_id",
        "operation_id",
        "request_id",
    }:
        return validate_identifier(value, field_name=key)
    if key.endswith("_code") or key in {"event_type", "outcome", "phase"}:
        if not EVIDENCE_PATTERN.fullmatch(value):
            raise ValueError("event classification is invalid")
        return value
    raise ValueError("event field is not allowlisted")


class BoundedEventRecorder:
    """Testable internal event sink that rejects arbitrary provider content."""

    _allowed_fields = frozenset(
        {
            "event_type",
            "task_id",
            "plan_id",
            "attempt_id",
            "owner_id",
            "operation_id",
            "request_id",
            "lock_key",
            "reason_code",
            "outcome",
            "phase",
            "generation",
            "dispatch_count",
            "observation_count",
            "verification_count",
            "stale_recovery",
            "conflict_hold",
        }
    )

    def __init__(self, *, max_events: int = MAX_EVENT_COUNT):
        if not 1 <= max_events <= MAX_EVENT_COUNT:
            raise ValueError("event bound is invalid")
        self._max_events = max_events
        self._events: list[dict[str, object]] = []
        self._lock = threading.Lock()

    def emit(self, event: dict[str, object]) -> None:
        if not isinstance(event, dict) or not event:
            raise ValueError("event must be a non-empty object")
        if len(event) > MAX_EVENT_FIELDS or set(event) - self._allowed_fields:
            raise ValueError("event fields are not bounded")
        safe = {
            key: _safe_event_value(key, value)
            for key, value in sorted(event.items())
        }
        with self._lock:
            if len(self._events) == self._max_events:
                self._events.pop(0)
            self._events.append(safe)

    def snapshot(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            return tuple(deepcopy(self._events))


EventSink = Callable[[dict[str, object]], None]


def null_event_sink(event: dict[str, object]) -> None:
    """Default sink intentionally retains no potentially sensitive evidence."""
