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
    dependency_analysis_requests: int = 0
    dependency_analysis_successes: int = 0
    dependency_analysis_partial: int = 0
    dependency_analysis_failures: int = 0
    dependency_index_builds: int = 0
    dependency_index_build_failures: int = 0
    dependency_cache_hits: int = 0
    dependency_cache_misses: int = 0
    dependency_index_invalidations: int = 0
    dependency_findings_truncated: int = 0
    dependency_current_unresolved_dynamic: int = 0
    dependency_index_source_count: int = 0
    dependency_index_edge_count: int = 0
    dependency_last_successful_build: str | None = None
    reliability_analysis_requests: int = 0
    reliability_analysis_successes: int = 0
    reliability_analysis_partial: int = 0
    reliability_analysis_failures: int = 0
    reliability_finding_counts: Counter = field(default_factory=Counter)
    reliability_traces_examined: int = 0
    reliability_referenced_entities_examined: int = 0
    reliability_source_failures: int = 0
    reliability_findings_truncated: int = 0
    reliability_last_successful_analysis: str | None = None
    reliability_last_failure_category: str | None = None

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

    def record_dependency_analysis_request(self) -> None:
        self.dependency_analysis_requests += 1

    def record_dependency_analysis_success(self) -> None:
        self.dependency_analysis_successes += 1

    def record_dependency_analysis_partial(self) -> None:
        self.dependency_analysis_partial += 1

    def record_dependency_analysis_failure(self) -> None:
        self.dependency_analysis_failures += 1

    def record_dependency_index_build(self) -> None:
        self.dependency_index_builds += 1

    def record_dependency_index_failure(self) -> None:
        self.dependency_index_build_failures += 1

    def record_dependency_cache_hit(self) -> None:
        self.dependency_cache_hits += 1

    def record_dependency_cache_miss(self) -> None:
        self.dependency_cache_misses += 1

    def record_dependency_invalidation(self) -> None:
        self.dependency_index_invalidations += 1

    def record_dependency_truncation(self) -> None:
        self.dependency_findings_truncated += 1
        self.record_evidence_truncation()

    def set_dependency_index_state(
        self,
        *,
        source_count: int,
        edge_count: int,
        unresolved_count: int,
        built_at: str,
    ) -> None:
        self.dependency_index_source_count = max(0, int(source_count))
        self.dependency_index_edge_count = max(0, int(edge_count))
        self.dependency_current_unresolved_dynamic = max(0, int(unresolved_count))
        self.dependency_last_successful_build = built_at

    def record_reliability_analysis_request(self) -> None:
        self.reliability_analysis_requests += 1

    def record_reliability_analysis_terminal(
        self,
        *,
        partial: bool,
        finding_counts,
        traces_examined: int,
        referenced_entities_examined: int,
        source_failures: int,
    ) -> None:
        if partial:
            self.reliability_analysis_partial += 1
        else:
            self.reliability_analysis_successes += 1
        self.reliability_finding_counts.update(finding_counts)
        self.reliability_traces_examined += max(0, int(traces_examined))
        self.reliability_referenced_entities_examined += max(0, int(referenced_entities_examined))
        self.reliability_source_failures += max(0, int(source_failures))
        from datetime import datetime, timezone
        self.reliability_last_successful_analysis = datetime.now(timezone.utc).isoformat()

    def record_reliability_analysis_failure(self, category: str) -> None:
        self.reliability_analysis_failures += 1
        self.reliability_last_failure_category = str(category)[:64]

    def record_reliability_truncation(self) -> None:
        self.reliability_findings_truncated += 1
        self.record_evidence_truncation()

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
        self.dependency_analysis_requests = 0
        self.dependency_analysis_successes = 0
        self.dependency_analysis_partial = 0
        self.dependency_analysis_failures = 0
        self.dependency_index_builds = 0
        self.dependency_index_build_failures = 0
        self.dependency_cache_hits = 0
        self.dependency_cache_misses = 0
        self.dependency_index_invalidations = 0
        self.dependency_findings_truncated = 0
        self.dependency_current_unresolved_dynamic = 0
        self.dependency_index_source_count = 0
        self.dependency_index_edge_count = 0
        self.dependency_last_successful_build = None
        self.reliability_analysis_requests = 0
        self.reliability_analysis_successes = 0
        self.reliability_analysis_partial = 0
        self.reliability_analysis_failures = 0
        self.reliability_finding_counts.clear()
        self.reliability_traces_examined = 0
        self.reliability_referenced_entities_examined = 0
        self.reliability_source_failures = 0
        self.reliability_findings_truncated = 0
        self.reliability_last_successful_analysis = None
        self.reliability_last_failure_category = None

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
            "dependency_analysis": {
                "request_count": self.dependency_analysis_requests,
                "successful_count": self.dependency_analysis_successes,
                "partial_count": self.dependency_analysis_partial,
                "failed_count": self.dependency_analysis_failures,
                "index_build_count": self.dependency_index_builds,
                "index_build_failures": self.dependency_index_build_failures,
                "index_cache_hits": self.dependency_cache_hits,
                "index_cache_misses": self.dependency_cache_misses,
                "index_invalidations": self.dependency_index_invalidations,
                "current_index_source_count": self.dependency_index_source_count,
                "current_index_edge_count": self.dependency_index_edge_count,
                "last_successful_index_build": self.dependency_last_successful_build,
                "findings_truncation_event_count": self.dependency_findings_truncated,
                "current_index_unresolved_dynamic_reference_count": self.dependency_current_unresolved_dynamic,
                "counter_semantics": {
                    "findings_truncation_event_count": "cumulative_process_events",
                    "current_index_unresolved_dynamic_reference_count": "current_index_state",
                },
            },
            "automation_reliability_analysis": {
                "request_count": self.reliability_analysis_requests,
                "successful_count": self.reliability_analysis_successes,
                "partial_count": self.reliability_analysis_partial,
                "failed_count": self.reliability_analysis_failures,
                "finding_counts_by_severity": dict(self.reliability_finding_counts),
                "traces_examined": self.reliability_traces_examined,
                "referenced_entities_examined": self.reliability_referenced_entities_examined,
                "source_failures": self.reliability_source_failures,
                "findings_truncated": self.reliability_findings_truncated,
                "cache_hits": 0,
                "cache_misses": 0,
                "last_successful_analysis_timestamp": self.reliability_last_successful_analysis,
                "last_failure_category": self.reliability_last_failure_category,
                "counter_semantics": "cumulative_process_events",
            },
        }


METRICS = RuntimeMetrics()
