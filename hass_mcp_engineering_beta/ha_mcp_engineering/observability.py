"""In-memory, secret-free operational metrics for the beta server."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
import statistics
import time


@dataclass
class RuntimeMetrics:
    started: float = field(default_factory=time.monotonic)
    transport_request_count: int = 0
    mcp_operation_count: int = 0
    tool_call_count: int = 0
    retry_count: int = 0
    timeout_count: int = 0
    mcp_operation_latencies: deque[float] = field(default_factory=lambda: deque(maxlen=100))
    tool_latencies: deque[float] = field(default_factory=lambda: deque(maxlen=100))
    ha_latencies: deque[float] = field(default_factory=lambda: deque(maxlen=100))
    operation_methods: Counter = field(default_factory=Counter)
    errors: Counter = field(default_factory=Counter)
    provider_requests: Counter = field(default_factory=Counter)
    provider_successes: Counter = field(default_factory=Counter)
    provider_failures: Counter = field(default_factory=Counter)
    provider_partial_results: int = 0
    fallback_attempts: int = 0
    fallback_successes: int = 0
    prohibited_fallback_attempts: int = 0
    evidence_truncation_count: int = 0

    def record_transport_completion(self) -> None:
        self.transport_request_count += 1

    def record_mcp_operation(self, duration_ms: float, method: str) -> None:
        self.mcp_operation_count += 1
        self.mcp_operation_latencies.append(duration_ms)
        self.operation_methods[method] += 1

    def record_tool_call(self) -> None:
        self.tool_call_count += 1

    def record_tool_completion(self, duration_ms: float) -> None:
        self.tool_latencies.append(duration_ms)

    def record_ha(self, duration_ms: float, *, retries: int = 0, timeout: bool = False) -> None:
        self.ha_latencies.append(duration_ms)
        self.retry_count += retries
        self.timeout_count += int(timeout)

    def record_error(self, code: str) -> None:
        self.errors[code] += 1

    def record_provider_result(self, provider_id: str, completeness: str) -> None:
        provider = provider_id if provider_id in {"engineering", "standard_ha_mcp", "direct_ha_api", "policy", "none"} else "other"
        self.provider_requests[provider] += 1
        if completeness == "complete":
            self.provider_successes[provider] += 1
        elif completeness == "partial":
            self.provider_successes[provider] += 1
            self.provider_partial_results += 1
        else:
            self.provider_failures[provider] += 1

    def record_fallback_attempt(self) -> None:
        self.fallback_attempts += 1

    def record_fallback_success(self) -> None:
        self.fallback_successes += 1

    def record_prohibited_fallback(self) -> None:
        self.prohibited_fallback_attempts += 1

    def record_evidence_truncation(self) -> None:
        self.evidence_truncation_count += 1

    def reset(self) -> None:
        """Deterministically reset in-memory metrics without replacing the registry."""
        self.started = time.monotonic()
        self.transport_request_count = 0
        self.mcp_operation_count = 0
        self.tool_call_count = 0
        self.retry_count = 0
        self.timeout_count = 0
        self.mcp_operation_latencies.clear()
        self.tool_latencies.clear()
        self.ha_latencies.clear()
        self.operation_methods.clear()
        self.errors.clear()
        self.provider_requests.clear()
        self.provider_successes.clear()
        self.provider_failures.clear()
        self.provider_partial_results = 0
        self.fallback_attempts = 0
        self.fallback_successes = 0
        self.prohibited_fallback_attempts = 0
        self.evidence_truncation_count = 0

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
            "transport_request_count": self.transport_request_count,
            "mcp_operation_count": self.mcp_operation_count,
            "tool_call_count": self.tool_call_count,
            "retry_count": self.retry_count,
            "timeout_count": self.timeout_count,
            "mcp_operation_latency": self._summary(self.mcp_operation_latencies),
            "tool_latency": self._summary(self.tool_latencies),
            "home_assistant_latency": self._summary(self.ha_latencies),
            "mcp_operation_methods": dict(self.operation_methods),
            "recent_error_counts": dict(self.errors),
            "provider_routing": {
                "requests_by_provider": dict(self.provider_requests),
                "successful_requests_by_provider": dict(self.provider_successes),
                "failures_by_provider": dict(self.provider_failures),
                "partial_results": self.provider_partial_results,
                "fallback_attempts": self.fallback_attempts,
                "fallback_successes": self.fallback_successes,
                "prohibited_fallback_attempts": self.prohibited_fallback_attempts,
                "evidence_truncation_count": self.evidence_truncation_count,
            },
        }


METRICS = RuntimeMetrics()
