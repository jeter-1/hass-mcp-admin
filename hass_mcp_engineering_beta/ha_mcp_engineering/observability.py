"""In-memory, secret-free operational metrics for the beta server."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
import statistics
import time


@dataclass
class RuntimeMetrics:
    started: float = field(default_factory=time.monotonic)
    request_count: int = 0
    tool_call_count: int = 0
    retry_count: int = 0
    timeout_count: int = 0
    request_latencies: deque[float] = field(default_factory=lambda: deque(maxlen=100))
    ha_latencies: deque[float] = field(default_factory=lambda: deque(maxlen=100))
    errors: Counter = field(default_factory=Counter)

    def record_request(self, duration_ms: float) -> None:
        self.request_count += 1
        self.request_latencies.append(duration_ms)

    def record_tool_call(self) -> None:
        self.tool_call_count += 1

    def record_ha(self, duration_ms: float, *, retries: int = 0, timeout: bool = False) -> None:
        self.ha_latencies.append(duration_ms)
        self.retry_count += retries
        self.timeout_count += int(timeout)

    def record_error(self, code: str) -> None:
        self.errors[code] += 1

    @staticmethod
    def _summary(values: deque[float]) -> dict[str, float | int | None]:
        if not values:
            return {"count": 0, "average_ms": None, "maximum_ms": None}
        return {
            "count": len(values),
            "average_ms": round(statistics.fmean(values), 3),
            "maximum_ms": round(max(values), 3),
        }

    def snapshot(self) -> dict:
        return {
            "uptime_seconds": round(time.monotonic() - self.started, 3),
            "request_count": self.request_count,
            "tool_call_count": self.tool_call_count,
            "retry_count": self.retry_count,
            "timeout_count": self.timeout_count,
            "request_latency": self._summary(self.request_latencies),
            "home_assistant_latency": self._summary(self.ha_latencies),
            "recent_error_counts": dict(self.errors),
        }


METRICS = RuntimeMetrics()
