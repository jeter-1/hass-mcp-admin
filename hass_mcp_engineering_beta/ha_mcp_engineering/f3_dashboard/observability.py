"""Isolated bounded counters/events; no central health integration."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import threading
from typing import Any

from .constants import MAX_EVENT_CODES, MAX_OBSERVABILITY_EVENTS, OBSERVABILITY_MODEL


COUNTER_NAMES = frozenset(
    {
        "planning.preread_attempts",
        "planning.preread_failures",
        "planning.non_storage_rejections",
        "planning.known_upstream_compatibility_rejections",
        "planning.patch_validation_failures",
        "planning.broad_subtree_rejections",
        "planning.risk_review_flags",
        "planning.plans_created",
        "provider.admission_attempts",
        "provider.admission_failures",
        "provider.dispatch_attempts",
        "provider.responses_received",
        "provider.responses_lost",
        "provider.failures",
        "provider.fallback_count",
        "verification.rereads",
        "verification.exact_matches",
        "verification.mismatch_outcomes",
        "verification.ambiguous_outcomes",
        "verification.manual_review_transitions",
        "verification.untouched_field_preservation_failures",
        "atomicity.atomic_mechanism_selected",
        "atomicity.writer_exclusion_failures",
        "atomicity.stale_preflight_rejections",
        "atomicity.atomicity_gate_rejections",
    }
)


@dataclass(frozen=True)
class DashboardEvent:
    timestamp: str
    counter: str
    target_hash: str | None
    codes: tuple[str, ...]


class DashboardWriteObservability:
    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()
        self._events: deque[DashboardEvent] = deque(maxlen=MAX_OBSERVABILITY_EVENTS)
        self._lock = threading.Lock()

    def record(
        self,
        counter: str,
        *,
        target: str | None = None,
        codes: tuple[str, ...] = (),
    ) -> None:
        if counter not in COUNTER_NAMES:
            raise ValueError("Unknown dashboard observability counter")
        bounded_codes = tuple(
            code[:96]
            for code in codes[:MAX_EVENT_CODES]
            if isinstance(code, str) and code
        )
        target_hash = (
            hashlib.sha256(target.encode("utf-8")).hexdigest() if target else None
        )
        event = DashboardEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            counter=counter,
            target_hash=target_hash,
            codes=bounded_codes,
        )
        with self._lock:
            self._counts[counter] += 1
            self._events.append(event)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counts = {name: self._counts[name] for name in sorted(COUNTER_NAMES)}
            events = [
                {
                    "timestamp": event.timestamp,
                    "counter": event.counter,
                    "target_hash": event.target_hash,
                    "codes": list(event.codes),
                }
                for event in self._events
            ]
        return {
            "model": OBSERVABILITY_MODEL,
            "counts": counts,
            "events": events,
            "raw_dashboard_content_exposed": False,
            "fallback_count": counts["provider.fallback_count"],
        }
