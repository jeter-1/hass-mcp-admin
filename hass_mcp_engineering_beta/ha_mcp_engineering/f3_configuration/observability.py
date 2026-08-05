"""Isolated bounded counters and events for F3 configuration adapters."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from threading import Lock
from typing import Protocol

from .models import bounded_codes


CONFIGURATION_METRICS = frozenset(
    {
        "preparations",
        "preflight_attempts",
        "stale_rejections",
        "absence_existence_rejections",
        "validation_failures",
        "lock_conflicts",
        "intents_committed",
        "intent_failures",
        "dispatch_attempts",
        "responses_received",
        "responses_lost",
        "readbacks",
        "verification_successes",
        "verification_mismatches",
        "manual_review_transitions",
        "recovery_attempts",
        "duplicate_execution_preventions",
        "blind_redispatch_preventions",
        "cancellations",
    }
)


@dataclass(frozen=True)
class ConfigurationAdapterEvent:
    """One safe event containing classifications and hashes only."""

    phase: str
    capability_identity: str
    resource_type: str
    action: str
    target_identity_hash: str
    outcome: str
    diagnostic_codes: tuple[str, ...] = ()


class ConfigurationEventSink(Protocol):
    def emit(self, event: ConfigurationAdapterEvent) -> None:
        ...


class NullConfigurationEventSink:
    def emit(self, event: ConfigurationAdapterEvent) -> None:
        del event


class InMemoryConfigurationEventSink:
    """Bounded deterministic sink intended for isolated tests and snapshots."""

    def __init__(self, *, maximum_events: int = 256) -> None:
        if not 1 <= maximum_events <= 1024:
            raise ValueError("event bound must be between 1 and 1024")
        self.maximum_events = maximum_events
        self._events: list[ConfigurationAdapterEvent] = []
        self._lock = Lock()

    def emit(self, event: ConfigurationAdapterEvent) -> None:
        safe = ConfigurationAdapterEvent(
            phase=_bounded_name(event.phase),
            capability_identity=_bounded_name(event.capability_identity),
            resource_type=_bounded_name(event.resource_type),
            action=_bounded_name(event.action),
            target_identity_hash=_required_hash(event.target_identity_hash),
            outcome=_bounded_name(event.outcome),
            diagnostic_codes=bounded_codes(event.diagnostic_codes),
        )
        with self._lock:
            if len(self._events) < self.maximum_events:
                self._events.append(safe)

    def snapshot(self) -> tuple[ConfigurationAdapterEvent, ...]:
        with self._lock:
            return tuple(self._events)


class ConfigurationAdapterMetrics:
    """Closed counter surface with no labels derived from configuration data."""

    def __init__(self) -> None:
        self._counts: Counter[tuple[str, str, str]] = Counter()
        self._lock = Lock()

    def increment(
        self, metric: str, *, resource_type: str, action: str
    ) -> None:
        if metric not in CONFIGURATION_METRICS:
            raise ValueError("configuration metric is not reviewed")
        resource = _bounded_name(resource_type)
        operation = _bounded_name(action)
        with self._lock:
            self._counts[(metric, resource, operation)] += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                f"{metric}:{resource}:{action}": value
                for (metric, resource, action), value in sorted(
                    self._counts.items()
                )
            }


def _bounded_name(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 96
        or value.lower() != value
        or not value[0].isalpha()
        or not value.replace("_", "a").isalnum()
    ):
        raise ValueError("event classification is not bounded")
    return value


def _required_hash(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("event target identity hash is invalid")
    return value
