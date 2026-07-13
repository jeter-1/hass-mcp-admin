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
    reliability_root_cause_counts: Counter = field(default_factory=Counter)
    reliability_traces_examined: int = 0
    reliability_referenced_entities_examined: int = 0
    reliability_source_failures: int = 0
    reliability_findings_truncated: int = 0
    reliability_last_successful_analysis: str | None = None
    reliability_last_failure_category: str | None = None
    impact_analysis_requests: int = 0
    impact_analysis_successes: int = 0
    impact_analysis_partial: int = 0
    impact_analysis_failures: int = 0
    impact_operations: Counter = field(default_factory=Counter)
    impact_findings_by_severity: Counter = field(default_factory=Counter)
    impact_finding_count: int = 0
    impact_findings_by_object_type: Counter = field(default_factory=Counter)
    impact_direct_findings: int = 0
    impact_indirect_findings: int = 0
    impact_unique_affected_object_count: int = 0
    impact_unique_affected_objects_by_type: Counter = field(default_factory=Counter)
    impact_unique_root_causes: int = 0
    impact_dynamic_review_events: int = 0
    impact_unresolved_dynamic_references: int = 0
    impact_source_failures: int = 0
    impact_findings_truncated: int = 0
    impact_cursor_continuations: int = 0
    impact_stale_cursor_events: int = 0
    impact_invalid_cursor_events: int = 0
    impact_last_cursor_failure_category: str | None = None
    impact_index_cache_hits: int = 0
    impact_index_cache_misses: int = 0
    impact_last_successful_analysis: str | None = None
    impact_last_failure_category: str | None = None
    integrity_analysis_requests: int = 0
    integrity_analysis_successes: int = 0
    integrity_analysis_partial: int = 0
    integrity_analysis_failures: int = 0
    integrity_finding_count: int = 0
    integrity_findings_by_severity: Counter = field(default_factory=Counter)
    integrity_findings_by_type: Counter = field(default_factory=Counter)
    integrity_findings_by_source_type: Counter = field(default_factory=Counter)
    integrity_unique_source_objects: int = 0
    integrity_unique_target_entities: int = 0
    integrity_orphan_candidates: int = 0
    integrity_unresolved_dynamic_references: int = 0
    integrity_manual_review_events: int = 0
    integrity_source_failures: int = 0
    integrity_finding_truncations: int = 0
    integrity_cursor_continuations: int = 0
    integrity_stale_cursor_events: int = 0
    integrity_invalid_cursor_events: int = 0
    integrity_last_cursor_failure_category: str | None = None
    integrity_index_cache_hits: int = 0
    integrity_index_cache_misses: int = 0
    integrity_last_successful_analysis: str | None = None
    integrity_last_failure_category: str | None = None
    incident_requests: int = 0
    incident_successes: int = 0
    incident_partial: int = 0
    incident_failures: int = 0
    incident_hypothesis_count: int = 0
    incident_hypotheses_by_confidence: Counter = field(default_factory=Counter)
    incident_hypotheses_by_severity: Counter = field(default_factory=Counter)
    incident_hypotheses_by_causal_status: Counter = field(default_factory=Counter)
    incident_event_count: int = 0
    incident_events_by_type: Counter = field(default_factory=Counter)
    incident_unique_entities: int = 0
    incident_unique_automations: int = 0
    incident_manual_review_events: int = 0
    incident_source_failures: int = 0
    incident_evidence_truncations: int = 0
    incident_timeline_truncations: int = 0
    incident_cursor_continuations: int = 0
    incident_stale_cursor_events: int = 0
    incident_invalid_cursor_events: int = 0
    incident_last_cursor_failure_category: str | None = None
    incident_index_cache_hits: int = 0
    incident_index_cache_misses: int = 0
    incident_last_successful_analysis: str | None = None
    incident_last_failure_category: str | None = None

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
        root_cause_counts,
        aggregate_findings: bool,
        traces_examined: int,
        referenced_entities_examined: int,
        source_failures: int,
        analysis_timestamp: str | None = None,
    ) -> None:
        if partial:
            self.reliability_analysis_partial += 1
        else:
            self.reliability_analysis_successes += 1
        if aggregate_findings:
            self.reliability_finding_counts.update(finding_counts)
            self.reliability_root_cause_counts.update(root_cause_counts)
            self.reliability_traces_examined += max(0, int(traces_examined))
            self.reliability_referenced_entities_examined += max(0, int(referenced_entities_examined))
            self.reliability_source_failures += max(0, int(source_failures))
        from datetime import datetime, timezone
        self.reliability_last_successful_analysis = (
            analysis_timestamp
            or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )

    def record_reliability_analysis_failure(self, category: str) -> None:
        self.reliability_analysis_failures += 1
        self.reliability_last_failure_category = str(category)[:64]

    def record_reliability_truncation(self) -> None:
        self.reliability_findings_truncated += 1
        self.record_evidence_truncation()

    def record_impact_analysis_request(self) -> None:
        self.impact_analysis_requests += 1

    def record_impact_analysis_terminal(
        self,
        *,
        partial: bool,
        operation: str,
        severity_counts,
        finding_object_counts,
        finding_count: int,
        unique_object_counts,
        unique_affected_object_count: int,
        direct_findings: int,
        indirect_findings: int,
        unique_root_causes: int,
        dynamic_review_required: bool,
        unresolved_dynamic_references: int,
        source_failures: int,
        index_cache_hit: bool,
        analysis_timestamp: str,
    ) -> None:
        if partial:
            self.impact_analysis_partial += 1
        else:
            self.impact_analysis_successes += 1
        normalized_operation = (
            operation
            if operation in {"rename_entity", "remove_entity", "disable_entity"}
            else "other"
        )
        self.impact_operations[normalized_operation] += 1
        self.impact_findings_by_severity.update(severity_counts)
        self.impact_finding_count += max(0, int(finding_count))
        self.impact_findings_by_object_type.update(finding_object_counts)
        self.impact_direct_findings += max(0, int(direct_findings))
        self.impact_indirect_findings += max(0, int(indirect_findings))
        self.impact_unique_affected_object_count += max(
            0, int(unique_affected_object_count)
        )
        self.impact_unique_affected_objects_by_type.update(unique_object_counts)
        self.impact_unique_root_causes += max(0, int(unique_root_causes))
        self.impact_dynamic_review_events += int(bool(dynamic_review_required))
        self.impact_unresolved_dynamic_references += max(
            0, int(unresolved_dynamic_references)
        )
        self.impact_source_failures += max(0, int(source_failures))
        if index_cache_hit:
            self.impact_index_cache_hits += 1
        else:
            self.impact_index_cache_misses += 1
        self.impact_last_successful_analysis = str(analysis_timestamp)[:64]

    def record_impact_analysis_failure(self, category: str) -> None:
        self.impact_analysis_failures += 1
        self.impact_last_failure_category = str(category)[:64]

    def record_impact_truncation(self) -> None:
        self.impact_findings_truncated += 1
        self.record_evidence_truncation()

    def record_impact_cursor_continuation(self) -> None:
        self.impact_cursor_continuations += 1

    def record_impact_cursor_event(self, category: str) -> None:
        if category == "stale_cursor":
            self.impact_stale_cursor_events += 1
        else:
            self.impact_invalid_cursor_events += 1
        self.impact_last_cursor_failure_category = str(category)[:64]

    def record_integrity_analysis_request(self) -> None:
        self.integrity_analysis_requests += 1

    def record_integrity_analysis_terminal(
        self,
        *,
        partial: bool,
        severity_counts,
        type_counts,
        source_counts,
        finding_count: int,
        unique_source_object_count: int,
        unique_target_entity_count: int,
        orphan_candidate_count: int,
        unresolved_dynamic_reference_count: int,
        manual_review_required: bool,
        source_failures: int,
        index_cache_hit: bool,
        analysis_timestamp: str,
    ) -> None:
        if partial:
            self.integrity_analysis_partial += 1
        else:
            self.integrity_analysis_successes += 1
        self.integrity_findings_by_severity.update(severity_counts)
        self.integrity_findings_by_type.update(type_counts)
        self.integrity_findings_by_source_type.update(source_counts)
        self.integrity_finding_count += max(0, int(finding_count))
        self.integrity_unique_source_objects += max(
            0, int(unique_source_object_count)
        )
        self.integrity_unique_target_entities += max(
            0, int(unique_target_entity_count)
        )
        self.integrity_orphan_candidates += max(
            0, int(orphan_candidate_count)
        )
        self.integrity_unresolved_dynamic_references += max(
            0, int(unresolved_dynamic_reference_count)
        )
        self.integrity_manual_review_events += int(bool(manual_review_required))
        self.integrity_source_failures += max(0, int(source_failures))
        if index_cache_hit:
            self.integrity_index_cache_hits += 1
        else:
            self.integrity_index_cache_misses += 1
        self.integrity_last_successful_analysis = str(analysis_timestamp)[:64]

    def record_integrity_analysis_failure(self, category: str) -> None:
        self.integrity_analysis_failures += 1
        self.integrity_last_failure_category = str(category)[:64]

    def record_integrity_truncation(self) -> None:
        self.integrity_finding_truncations += 1
        self.record_evidence_truncation()

    def record_integrity_cursor_continuation(self) -> None:
        self.integrity_cursor_continuations += 1

    def record_integrity_cursor_event(self, category: str) -> None:
        if category == "stale_cursor":
            self.integrity_stale_cursor_events += 1
        else:
            self.integrity_invalid_cursor_events += 1
        self.integrity_last_cursor_failure_category = str(category)[:64]

    def record_incident_request(self) -> None:
        self.incident_requests += 1

    def record_incident_terminal(
        self,
        *,
        partial: bool,
        hypothesis_count: int,
        confidence_counts,
        severity_counts,
        causal_counts,
        correlated_event_count: int,
        event_counts,
        unique_entity_count: int,
        unique_automation_count: int,
        manual_review_required: bool,
        source_failures: int,
        index_cache_hit: bool,
        index_requested: bool,
        analysis_timestamp: str,
    ) -> None:
        if partial:
            self.incident_partial += 1
        else:
            self.incident_successes += 1
        self.incident_hypothesis_count += max(0, int(hypothesis_count))
        self.incident_hypotheses_by_confidence.update(confidence_counts)
        self.incident_hypotheses_by_severity.update(severity_counts)
        self.incident_hypotheses_by_causal_status.update(causal_counts)
        self.incident_event_count += max(0, int(correlated_event_count))
        self.incident_events_by_type.update(event_counts)
        self.incident_unique_entities += max(0, int(unique_entity_count))
        self.incident_unique_automations += max(0, int(unique_automation_count))
        self.incident_manual_review_events += int(bool(manual_review_required))
        self.incident_source_failures += max(0, int(source_failures))
        if index_requested:
            if index_cache_hit:
                self.incident_index_cache_hits += 1
            else:
                self.incident_index_cache_misses += 1
        self.incident_last_successful_analysis = str(analysis_timestamp)[:64]

    def record_incident_failure(self, category: str) -> None:
        self.incident_failures += 1
        self.incident_last_failure_category = str(category)[:64]

    def record_incident_cursor_continuation(self) -> None:
        self.incident_cursor_continuations += 1

    def record_incident_cursor_event(self, category: str) -> None:
        if category == "stale_cursor":
            self.incident_stale_cursor_events += 1
        else:
            self.incident_invalid_cursor_events += 1
        self.incident_last_cursor_failure_category = str(category)[:64]

    def record_incident_evidence_truncation(self) -> None:
        self.incident_evidence_truncations += 1
        self.record_evidence_truncation()

    def record_incident_timeline_truncation(self) -> None:
        self.incident_timeline_truncations += 1
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
        self.reliability_root_cause_counts.clear()
        self.reliability_traces_examined = 0
        self.reliability_referenced_entities_examined = 0
        self.reliability_source_failures = 0
        self.reliability_findings_truncated = 0
        self.reliability_last_successful_analysis = None
        self.reliability_last_failure_category = None
        self.impact_analysis_requests = 0
        self.impact_analysis_successes = 0
        self.impact_analysis_partial = 0
        self.impact_analysis_failures = 0
        self.impact_operations.clear()
        self.impact_findings_by_severity.clear()
        self.impact_finding_count = 0
        self.impact_findings_by_object_type.clear()
        self.impact_direct_findings = 0
        self.impact_indirect_findings = 0
        self.impact_unique_affected_object_count = 0
        self.impact_unique_affected_objects_by_type.clear()
        self.impact_unique_root_causes = 0
        self.impact_dynamic_review_events = 0
        self.impact_unresolved_dynamic_references = 0
        self.impact_source_failures = 0
        self.impact_findings_truncated = 0
        self.impact_cursor_continuations = 0
        self.impact_stale_cursor_events = 0
        self.impact_invalid_cursor_events = 0
        self.impact_last_cursor_failure_category = None
        self.impact_index_cache_hits = 0
        self.impact_index_cache_misses = 0
        self.impact_last_successful_analysis = None
        self.impact_last_failure_category = None
        self.integrity_analysis_requests = 0
        self.integrity_analysis_successes = 0
        self.integrity_analysis_partial = 0
        self.integrity_analysis_failures = 0
        self.integrity_finding_count = 0
        self.integrity_findings_by_severity.clear()
        self.integrity_findings_by_type.clear()
        self.integrity_findings_by_source_type.clear()
        self.integrity_unique_source_objects = 0
        self.integrity_unique_target_entities = 0
        self.integrity_orphan_candidates = 0
        self.integrity_unresolved_dynamic_references = 0
        self.integrity_manual_review_events = 0
        self.integrity_source_failures = 0
        self.integrity_finding_truncations = 0
        self.integrity_cursor_continuations = 0
        self.integrity_stale_cursor_events = 0
        self.integrity_invalid_cursor_events = 0
        self.integrity_last_cursor_failure_category = None
        self.integrity_index_cache_hits = 0
        self.integrity_index_cache_misses = 0
        self.integrity_last_successful_analysis = None
        self.integrity_last_failure_category = None
        self.incident_requests = 0
        self.incident_successes = 0
        self.incident_partial = 0
        self.incident_failures = 0
        self.incident_hypothesis_count = 0
        self.incident_hypotheses_by_confidence.clear()
        self.incident_hypotheses_by_severity.clear()
        self.incident_hypotheses_by_causal_status.clear()
        self.incident_event_count = 0
        self.incident_events_by_type.clear()
        self.incident_unique_entities = 0
        self.incident_unique_automations = 0
        self.incident_manual_review_events = 0
        self.incident_source_failures = 0
        self.incident_evidence_truncations = 0
        self.incident_timeline_truncations = 0
        self.incident_cursor_continuations = 0
        self.incident_stale_cursor_events = 0
        self.incident_invalid_cursor_events = 0
        self.incident_last_cursor_failure_category = None
        self.incident_index_cache_hits = 0
        self.incident_index_cache_misses = 0
        self.incident_last_successful_analysis = None
        self.incident_last_failure_category = None

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
                "root_cause_counts_by_severity": dict(self.reliability_root_cause_counts),
                "traces_examined": self.reliability_traces_examined,
                "referenced_entities_examined": self.reliability_referenced_entities_examined,
                "source_failures": self.reliability_source_failures,
                "findings_truncated": self.reliability_findings_truncated,
                "cache_supported": False,
                "cache_counters_active": False,
                "cache_status": "not_configured",
                "cache_reason": "Reliability results are collected and evaluated for every request.",
                "last_successful_analysis_timestamp": self.reliability_last_successful_analysis,
                "last_failure_category": self.reliability_last_failure_category,
                "counter_semantics": "cumulative_process_events",
            },
            "change_impact_analysis": {
                "request_count": self.impact_analysis_requests,
                "successful_count": self.impact_analysis_successes,
                "partial_count": self.impact_analysis_partial,
                "failed_count": self.impact_analysis_failures,
                "operations_by_type": dict(self.impact_operations),
                "findings_by_severity": dict(self.impact_findings_by_severity),
                "finding_count": self.impact_finding_count,
                "findings_by_object_type": dict(
                    self.impact_findings_by_object_type
                ),
                "direct_finding_count": self.impact_direct_findings,
                "indirect_finding_count": self.impact_indirect_findings,
                "unique_affected_object_count": self.impact_unique_affected_object_count,
                "unique_affected_objects_by_type": dict(
                    self.impact_unique_affected_objects_by_type
                ),
                "unique_root_cause_count": self.impact_unique_root_causes,
                "dynamic_reference_review_event_count": self.impact_dynamic_review_events,
                "unresolved_dynamic_reference_count": self.impact_unresolved_dynamic_references,
                "direct_impacts": self.impact_direct_findings,
                "indirect_impacts": self.impact_indirect_findings,
                "affected_objects_by_type": dict(
                    self.impact_unique_affected_objects_by_type
                ),
                "unique_root_causes": self.impact_unique_root_causes,
                "dynamic_reference_review_events": self.impact_dynamic_review_events,
                "source_failures": self.impact_source_failures,
                "finding_truncation_events": self.impact_findings_truncated,
                "cursor_continuations": self.impact_cursor_continuations,
                "stale_cursor_events": self.impact_stale_cursor_events,
                "invalid_cursor_events": self.impact_invalid_cursor_events,
                "cursor_failure_count": self.impact_stale_cursor_events
                + self.impact_invalid_cursor_events,
                "last_cursor_failure_category": self.impact_last_cursor_failure_category,
                "index_cache_hits": self.impact_index_cache_hits,
                "index_cache_misses": self.impact_index_cache_misses,
                "last_successful_analysis_timestamp": self.impact_last_successful_analysis,
                "last_failure_category": self.impact_last_failure_category,
                "result_cache_supported": False,
                "counter_semantics": {
                    "request_count": "tool_invocations_including_cursor_continuations",
                    "terminal_outcomes_and_aggregates": "new_analyses_only",
                    "cursor_continuations": "bounded_snapshot_page_requests",
                    "finding_count": "cumulative_findings_from_new_analyses",
                    "findings_by_object_type": "cumulative_finding_observations_by_type",
                    "unique_affected_object_count": "sum_of_per_analysis_unique_object_type_and_identifier_pairs",
                    "unique_affected_objects_by_type": "sum_of_per_analysis_unique_identifiers_by_type",
                    "dynamic_reference_review_event_count": "new_analyses_requiring_dynamic_reference_review",
                    "unresolved_dynamic_reference_count": "cumulative_in_scope_dynamic_references_observed_by_new_analyses",
                    "cursor_failures_are_terminal_analysis_failures": False,
                    "deprecated_aliases": {
                        "direct_impacts": "direct_finding_count",
                        "indirect_impacts": "indirect_finding_count",
                        "affected_objects_by_type": "unique_affected_objects_by_type",
                        "unique_root_causes": "unique_root_cause_count",
                        "dynamic_reference_review_events": "dynamic_reference_review_event_count",
                    },
                },
            },
            "configuration_integrity_analysis": {
                "request_count": self.integrity_analysis_requests,
                "successful_count": self.integrity_analysis_successes,
                "partial_count": self.integrity_analysis_partial,
                "failed_count": self.integrity_analysis_failures,
                "finding_count": self.integrity_finding_count,
                "findings_by_severity": dict(self.integrity_findings_by_severity),
                "findings_by_type": dict(self.integrity_findings_by_type),
                "findings_by_source_type": dict(
                    self.integrity_findings_by_source_type
                ),
                "unique_source_object_count": self.integrity_unique_source_objects,
                "unique_target_entity_count": self.integrity_unique_target_entities,
                "orphan_candidate_count": self.integrity_orphan_candidates,
                "unresolved_dynamic_reference_count": self.integrity_unresolved_dynamic_references,
                "manual_review_event_count": self.integrity_manual_review_events,
                "source_failures": self.integrity_source_failures,
                "finding_truncation_events": self.integrity_finding_truncations,
                "cursor_continuations": self.integrity_cursor_continuations,
                "cursor_failure_count": self.integrity_stale_cursor_events
                + self.integrity_invalid_cursor_events,
                "stale_cursor_events": self.integrity_stale_cursor_events,
                "invalid_cursor_events": self.integrity_invalid_cursor_events,
                "last_cursor_failure_category": self.integrity_last_cursor_failure_category,
                "index_cache_hits": self.integrity_index_cache_hits,
                "index_cache_misses": self.integrity_index_cache_misses,
                "last_successful_analysis_timestamp": self.integrity_last_successful_analysis,
                "last_failure_category": self.integrity_last_failure_category,
                "result_cache_supported": False,
                "counter_semantics": {
                    "request_count": "tool_invocations_including_cursor_continuations",
                    "terminal_outcomes_and_aggregates": "new_analyses_only",
                    "finding_count": "cumulative_findings_from_new_analyses",
                    "unique_counts": "sums_of_per_analysis_unique_values",
                    "orphan_candidate_count": "cumulative_conservative_registry_candidates",
                    "unresolved_dynamic_reference_count": "cumulative_requested_scope_unresolved_references",
                    "manual_review_event_count": "new_analyses_requiring_manual_review",
                    "cursor_continuations": "bounded_snapshot_page_requests",
                    "cursor_failures_are_terminal_analysis_failures": False,
                    "pagination_snapshots_are_general_result_cache": False,
                },
            },
            "incident_correlation": {
                "request_count": self.incident_requests,
                "successful_count": self.incident_successes,
                "partial_count": self.incident_partial,
                "failed_count": self.incident_failures,
                "hypothesis_count": self.incident_hypothesis_count,
                "hypotheses_by_confidence": dict(self.incident_hypotheses_by_confidence),
                "hypotheses_by_severity": dict(self.incident_hypotheses_by_severity),
                "hypotheses_by_causal_status": dict(self.incident_hypotheses_by_causal_status),
                "correlated_event_count": self.incident_event_count,
                "events_by_type": dict(self.incident_events_by_type),
                "unique_entity_count": self.incident_unique_entities,
                "unique_automation_count": self.incident_unique_automations,
                "manual_review_event_count": self.incident_manual_review_events,
                "source_failures": self.incident_source_failures,
                "evidence_truncation_events": self.incident_evidence_truncations,
                "timeline_truncation_events": self.incident_timeline_truncations,
                "cursor_continuations": self.incident_cursor_continuations,
                "cursor_failure_count": self.incident_stale_cursor_events + self.incident_invalid_cursor_events,
                "stale_cursor_events": self.incident_stale_cursor_events,
                "invalid_cursor_events": self.incident_invalid_cursor_events,
                "last_cursor_failure_category": self.incident_last_cursor_failure_category,
                "index_cache_hits": self.incident_index_cache_hits,
                "index_cache_misses": self.incident_index_cache_misses,
                "last_successful_analysis_timestamp": self.incident_last_successful_analysis,
                "last_failure_category": self.incident_last_failure_category,
                "result_cache_supported": False,
                "counter_semantics": {
                    "request_count": "tool_invocations_including_cursor_continuations",
                    "terminal_outcomes_and_aggregates": "new_analyses_only",
                    "unique_counts": "sums_of_per_analysis_unique_values",
                    "cursor_continuations": "bounded_snapshot_page_requests",
                    "cursor_failures_are_terminal_analysis_failures": False,
                    "pagination_snapshots_are_general_result_cache": False,
                },
            },
        }


METRICS = RuntimeMetrics()
