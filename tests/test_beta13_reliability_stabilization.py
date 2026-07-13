"""Synthetic regressions for the Beta 13 reliability stabilization contracts."""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.observability import METRICS  # noqa: E402
from ha_mcp_engineering.errors import AutomationNotFoundError, InvalidRequestError  # noqa: E402
from ha_mcp_engineering.request_context import RequestTelemetry, begin_request, end_request  # noqa: E402
from ha_mcp_engineering.reliability.models import ReliabilityEvidenceBundle, ReliabilitySourceCoverage  # noqa: E402
from ha_mcp_engineering.reliability.provider import (  # noqa: E402
    DirectHaReliabilityProvider, _correlation_bases, _normalize_trace,
)
from ha_mcp_engineering.reliability.rules import build_root_cause_groups, evaluate_rules  # noqa: E402
from ha_mcp_engineering.reliability.service import AutomationReliabilityAnalysisService  # noqa: E402
from ha_mcp_engineering.reliability.timestamps import normalize_timestamp, observation_window  # noqa: E402
from ha_mcp_engineering.providers import ProviderCapability, ProviderCompleteness, ProviderCoverage, ProviderResult  # noqa: E402


def source(source_type, completeness="complete", **kwargs):
    return ReliabilitySourceCoverage(
        source_type=source_type, provider="direct_ha_api", provider_capability=source_type,
        completeness=completeness, items_examined=kwargs.pop("items_examined", 0),
        failed_items=kwargs.pop("failed_items", 0), duration_ms=1.0, **kwargs,
    )


def evidence_bundle(*, traces=None, logs=None, coverage=None):
    return ReliabilityEvidenceBundle(
        automation_id="beta13_fixture",
        automation={"entity_id": "automation.beta13_fixture", "state": "on"},
        configuration={"alias": "Synthetic fixture", "trigger": [], "action": []},
        configuration_fingerprint="synthetic-fingerprint",
        blueprint=None,
        blueprint_path=None,
        references=[],
        dynamic_references=[],
        traces=traces if traces is not None else [{"run_id": "ok", "timestamp": "2026-07-12T00:00:00Z"}],
        system_log_entries=logs or [],
        coverage=coverage or [source("automation_traces"), source("system_log")],
    )


def failure_traces(order=(0, 1), *, step="action/0", error="synthetic service failure", dependency="light.beta13"):
    times = ["2026-07-12T01:00:00Z", "2026-07-12T03:00:00+00:00"]
    return [{
        "run_id": f"run-{index}", "timestamp": times[index], "started_at": times[index],
        "failure_step": step, "last_step": step, "error": error, "action_error": True,
        "affected_dependency": dependency,
    } for index in order]


class TimestampContractTests(unittest.TestCase):
    def test_normalizes_iso_epoch_and_rejects_naive_or_malformed(self):
        self.assertEqual(normalize_timestamp("2026-07-12T01:00:00+00:00"), "2026-07-12T01:00:00.000000Z")
        self.assertEqual(normalize_timestamp(0), "1970-01-01T00:00:00.000000Z")
        self.assertIsNone(normalize_timestamp("2026-07-12 01:00:00"))
        self.assertIsNone(normalize_timestamp("not-a-timestamp"))

    def test_observation_window_is_order_independent(self):
        expected = ("2026-07-12T01:00:00.000000Z", "2026-07-12T03:00:00.000000Z")
        self.assertEqual(observation_window(failure_traces((0, 1))), expected)
        self.assertEqual(observation_window(failure_traces((1, 0))), expected)
        mixed = failure_traces((1, 0)) + [{"run_id": "missing"}, {"timestamp": "bad"}]
        self.assertEqual(observation_window(mixed), expected)

    def test_equal_missing_and_interval_timestamps_are_deterministic(self):
        values = [{"run_id": "b", "timestamp": "2026-07-12T01:00:00Z"},
                  {"run_id": "a", "started_at": "2026-07-12T01:00:00+00:00", "finished_at": "2026-07-12T02:00:00Z"},
                  {"run_id": "missing"}]
        self.assertEqual(observation_window(values), ("2026-07-12T01:00:00.000000Z", "2026-07-12T01:00:00.000000Z"))


class CorrelationContractTests(unittest.IsolatedAsyncioTestCase):
    def test_generic_near_time_and_substrings_do_not_correlate(self):
        values = _correlation_bases(
            "error doing job: task exception was never retrieved light.office_tv_lights",
            automation_entity_id="automation.office", internal_id="office",
            failed_dependencies={"light.office"}, trace_signatures=set(),
        )
        self.assertEqual(values, ())

    def test_exact_automation_and_dependency_identifiers_correlate(self):
        text = "automation.beta13_fixture failed while reading light.beta13"
        values = _correlation_bases(
            text, automation_entity_id="automation.beta13_fixture", internal_id="beta13_fixture",
            failed_dependencies={"light.beta13"}, trace_signatures=set(),
        )
        self.assertEqual(values, ("automation_entity_id_exact", "failed_dependency_entity_id_exact"))

    def test_matching_service_error_signature_requires_both_parts(self):
        args = dict(automation_entity_id="", internal_id="", failed_dependencies=set(),
                    trace_signatures={("light.turn_on", "synthetic service failure")})
        self.assertEqual(_correlation_bases("light.turn_on synthetic service failure", **args), ("trace_service_error_signature",))
        self.assertEqual(_correlation_bases("light.turn_on unrelated failure", **args), ())

    async def test_log_snapshot_is_sanitized_bounded_and_retention_unknown(self):
        secret = "synthetic-secret-for-beta13"

        class WebSocket:
            async def command(self, payload):
                self.payload = payload
                return [{"timestamp": 1783821600, "message": [f"automation.beta13_fixture token={secret}"]}]

        provider = DirectHaReliabilityProvider(object(), WebSocket(), secret=secret)
        logs, coverage = await provider._collect_system_log(
            {"entity_id": "automation.beta13_fixture"}, "beta13_fixture", [], [], 168
        )
        encoded = json.dumps(logs)
        self.assertNotIn(secret, encoded)
        self.assertIn("[REDACTED:token]", encoded)
        self.assertTrue(logs[0]["timestamp"].endswith("Z"))
        self.assertEqual(coverage.completeness, "partial")
        self.assertEqual(coverage.snapshot_completeness, "complete")
        self.assertEqual(coverage.retention_coverage, "unknown")
        self.assertFalse(coverage.affects_result_status)
        self.assertEqual(coverage.requested_lookback_hours, 168)


class RootCauseContractTests(unittest.TestCase):
    def test_overlapping_trace_and_action_findings_share_one_root_cause(self):
        findings = evaluate_rules(evidence_bundle(traces=failure_traces((1, 0))))
        repeated = [item for item in findings if item.rule_id in {"repeated_trace_failure", "repeated_action_error"}]
        self.assertEqual(len(repeated), 2)
        self.assertEqual(len({item.root_cause_group_id for item in repeated}), 1)
        self.assertEqual({item.root_cause_relationship for item in repeated}, {"primary", "supporting"})
        group = next(group for group in build_root_cause_groups(findings) if len(group.member_finding_ids) == 2)
        self.assertEqual(group.unique_occurrence_count, 2)
        self.assertLessEqual(group.first_observed, group.last_observed)

    def test_unrelated_failures_remain_separate_groups(self):
        traces = failure_traces() + failure_traces(step="action/1", error="different failure", dependency="switch.beta13")
        repeated = [item for item in evaluate_rules(evidence_bundle(traces=traces)) if item.rule_id == "repeated_trace_failure"]
        self.assertEqual(len(repeated), 2)
        self.assertEqual(len({item.root_cause_group_id for item in repeated}), 2)

    def test_mixed_system_log_order_has_chronological_window(self):
        logs = [
            {"identity": "new", "timestamp": 1783825200, "summary": "bounded", "correlation_basis": ("automation_entity_id_exact",)},
            {"identity": "old", "timestamp": "2026-07-12T01:00:00Z", "summary": "bounded", "correlation_basis": ("automation_entity_id_exact",)},
        ]
        finding = next(item for item in evaluate_rules(evidence_bundle(logs=logs)) if item.rule_id == "correlated_system_log_error")
        self.assertLessEqual(finding.first_observed, finding.last_observed)


class FakeProvider:
    provider_id = "engineering"

    def __init__(self, bundle):
        self.bundle = bundle
        self.calls = []

    async def fetch(self, request):
        self.calls.append(request)
        return ProviderResult(
            provider_id="engineering", capability=ProviderCapability.RELIABILITY_ANALYSIS,
            completeness=ProviderCompleteness.PARTIAL if self.bundle.partial else ProviderCompleteness.COMPLETE,
            coverage=ProviderCoverage(len(self.bundle.coverage), len(self.bundle.coverage)), data=self.bundle,
        )


class RaisingProvider:
    async def fetch(self, _request):
        raise AutomationNotFoundError()


class ServiceAndMetricsContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        METRICS.reset()

    async def test_log_retention_limitation_does_not_discard_independent_finding(self):
        coverage = [source("automation_traces"), source(
            "system_log", "partial", affects_result_status=False, snapshot_completeness="complete",
            retention_coverage="unknown", requested_lookback_hours=168,
            warnings=["System Log retention coverage is unknown."],
        )]
        output = await AutomationReliabilityAnalysisService(FakeProvider(
            evidence_bundle(traces=failure_traces(), coverage=coverage)
        )).analyze(automation_id="beta13_fixture")
        self.assertFalse(output.partial)
        self.assertEqual(output.data["result_status"], "success")
        self.assertEqual(output.data["system_log_coverage"]["retention_coverage"], "unknown")
        self.assertEqual(output.data["unique_root_cause_count"], 1)
        self.assertEqual(len(output.data["findings"]), 2)

    async def test_summary_returns_count_not_root_cause_payload(self):
        output = await AutomationReliabilityAnalysisService(FakeProvider(
            evidence_bundle(traces=failure_traces())
        )).analyze(automation_id="beta13_fixture", detail_level="summary")
        self.assertEqual(output.data["unique_root_cause_count"], 1)
        self.assertEqual(output.data["root_cause_groups"], [])

    async def test_pagination_does_not_double_count_finding_or_root_cause_metrics(self):
        traces = failure_traces() + failure_traces(step="action/1", error="different", dependency="switch.beta13")
        service = AutomationReliabilityAnalysisService(FakeProvider(evidence_bundle(traces=traces)))
        first = await service.analyze(automation_id="beta13_fixture", limit=1)
        before = METRICS.snapshot()["automation_reliability_analysis"]
        await service.analyze(automation_id="beta13_fixture", limit=1, cursor=first.data["pagination"]["next_cursor"])
        after = METRICS.snapshot()["automation_reliability_analysis"]
        self.assertEqual(len(service.provider.calls), 1)
        self.assertEqual(before["finding_counts_by_severity"], after["finding_counts_by_severity"])
        self.assertEqual(before["root_cause_counts_by_severity"], after["root_cause_counts_by_severity"])

    async def test_cache_and_timing_metadata_are_truthful(self):
        telemetry, token = begin_request("beta13-timing-request")
        try:
            start1 = 10.0
            telemetry.begin_ha_attempt(start1)
            telemetry.begin_ha_attempt(start1 + 0.01)
            telemetry.ha_duration_ms = 150.0
            telemetry.finish_ha_attempt(start1 + 0.11)
            telemetry.finish_ha_attempt(start1 + 0.12)
            output = await AutomationReliabilityAnalysisService(FakeProvider(evidence_bundle())).analyze(automation_id="beta13_fixture")
        finally:
            end_request(token)
        timing = output.data["timing_details"]
        self.assertEqual(timing["home_assistant_cumulative_attempt_ms"], 150.0)
        self.assertEqual(timing["home_assistant_wall_clock_span_ms"], 120.0)
        self.assertTrue(timing["provider_operations_concurrent"])
        self.assertEqual(output.data["cache"]["status"], "not_configured")
        health = METRICS.snapshot()["automation_reliability_analysis"]
        self.assertFalse(health["cache_supported"])
        self.assertFalse(health["cache_counters_active"])

    def test_zero_upstream_telemetry_is_unambiguous(self):
        telemetry = RequestTelemetry("beta13-zero-upstream")
        self.assertEqual(telemetry.ha_request_count, 0)
        self.assertEqual(telemetry.ha_duration_ms, 0.0)
        self.assertEqual(telemetry.ha_wall_clock_span_ms, 0.0)

    def test_sequential_attempt_span_is_not_cumulative_effort(self):
        telemetry = RequestTelemetry("beta13-sequential-upstream")
        telemetry.begin_ha_attempt(10.0)
        telemetry.finish_ha_attempt(10.05)
        telemetry.begin_ha_attempt(10.10)
        telemetry.finish_ha_attempt(10.20)
        telemetry.ha_duration_ms = 150.0
        self.assertEqual(telemetry.ha_request_count, 2)
        self.assertEqual(telemetry.ha_max_concurrent_requests, 1)
        self.assertEqual(telemetry.ha_wall_clock_span_ms, 200.0)
        self.assertEqual(telemetry.ha_duration_ms, 150.0)

    async def test_invalid_and_missing_ids_each_record_one_terminal_failure(self):
        telemetry, token = begin_request("beta13-validation-request")
        try:
            with self.assertRaises(InvalidRequestError):
                await AutomationReliabilityAnalysisService(FakeProvider(evidence_bundle())).analyze(
                    automation_id="../invalid"
                )
            self.assertEqual(telemetry.ha_request_count, 0)
            self.assertEqual(telemetry.ha_duration_ms, 0.0)
        finally:
            end_request(token)
        self.assertEqual(METRICS.snapshot()["automation_reliability_analysis"]["failed_count"], 1)

        METRICS.reset()
        with self.assertRaises(AutomationNotFoundError):
            await AutomationReliabilityAnalysisService(RaisingProvider()).analyze(
                automation_id="beta13_missing"
            )
        metrics = METRICS.snapshot()["automation_reliability_analysis"]
        self.assertEqual(metrics["request_count"], 1)
        self.assertEqual(metrics["failed_count"], 1)


class TraceSanitizationTests(unittest.TestCase):
    def test_trace_normalization_uses_stable_interval_and_bounded_structure(self):
        value = _normalize_trace(
            {"run_id": "r1", "timestamp": 1783821600, "last_action": "2026-07-12T02:00:00Z", "last_step": "action/0"},
            {"trace": {"action/0": [{"service": "light.turn_on", "entity_id": "light.beta13", "error": "Synthetic failure"}]}},
        )
        self.assertTrue(value["started_at"].endswith("Z"))
        self.assertTrue(value["finished_at"].endswith("Z"))
        self.assertEqual(value["services"], ["light.turn_on"])
        self.assertEqual(value["affected_dependency"], "light.beta13")


if __name__ == "__main__":
    unittest.main()
