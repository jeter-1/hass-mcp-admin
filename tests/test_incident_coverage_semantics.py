import asyncio
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.dependency.models import (  # noqa: E402
    DependencyFinding,
    DependencyIndexSnapshot,
    SourceCoverageItem,
)
from ha_mcp_engineering.incident.normalization import deduplicate_and_sort, event  # noqa: E402
from ha_mcp_engineering.incident.provider import DirectHaIncidentProvider  # noqa: E402
from ha_mcp_engineering.incident.service import IncidentCorrelationService  # noqa: E402
from ha_mcp_engineering.observability import METRICS  # noqa: E402
from ha_mcp_engineering.providers import EvidenceRequest, ProviderCapability  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.source_coverage import normalize_coverage  # noqa: E402
from tests.test_incident_correlation import (  # noqa: E402
    ANALYSIS_TIME,
    FakeProvider,
    FakeRestClient,
    FakeWebSocketClient,
    NeverReliabilityProvider,
    analyze,
    make_bundle,
    rich_bundle,
)


UNSUPPORTED_TYPES = ("script", "scene", "group", "template", "dashboard")


def dependency_snapshot(*, failed_items=0):
    finding = DependencyFinding(
        evidence_id="ev_dependency",
        target_entity_id="sensor.focus",
        source_type="automation",
        source_id="auto-1",
        source_entity_id="automation.example",
        source_name="Example",
        relation="condition_entity",
        config_path="$.condition[0].entity_id",
    )
    coverage = [
        SourceCoverageItem(
            "automation",
            "direct_ha_api",
            "automation_config",
            "partial" if failed_items else "complete",
            1,
            failed_items,
            ["One automation configuration could not be read."] if failed_items else [],
        ),
        SourceCoverageItem("blueprint", "direct_ha_api", "blueprint_source", "complete", 0, 0),
    ]
    coverage.extend(
        SourceCoverageItem(
            source_type,
            "none",
            f"{source_type}_configuration",
            "unavailable",
            0,
            0,
            [f"Reliable {source_type} configuration access is not available."],
        )
        for source_type in UNSUPPORTED_TYPES
    )
    return DependencyIndexSnapshot(
        fingerprint="index-partial",
        generation=7,
        built_at_monotonic=1.0,
        built_at="2026-07-21T11:59:00Z",
        findings=(finding,),
        dynamic_references=(),
        target_metadata={},
        coverage=tuple(coverage),
        build_duration_ms=4.0,
    )


class SnapshotIndex:
    def __init__(self, *, snapshot=None, error=None):
        self.snapshot = snapshot or dependency_snapshot()
        self.error = error
        self.calls = []

    async def get(self, *, refresh=False):
        self.calls.append(refresh)
        if self.error:
            raise self.error
        return self.snapshot, bool(refresh), 2.5

    def active_identity(self):
        return {
            "valid": True,
            "generation": self.snapshot.generation,
            "fingerprint": self.snapshot.fingerprint,
        }


def provider_query(**overrides):
    values = {
        "analysis_timestamp": "2026-07-21T12:00:00Z",
        "focus_entity_id": "sensor.focus",
        "automation_id": "",
        "related_entity_ids": [],
        "lookback_hours": 24,
        "trace_limit": 10,
        "include_dependency_context": True,
        "include_integrity_context": False,
        "include_reliability_context": False,
        "refresh_index": True,
    }
    values.update(overrides)
    return values


def fetch(index):
    provider = DirectHaIncidentProvider(
        index,
        FakeRestClient(),
        FakeWebSocketClient(),
        NeverReliabilityProvider(),
    )
    result = asyncio.run(provider.fetch(EvidenceRequest(
        capability=ProviderCapability.INCIDENT_CORRELATION,
        query=provider_query(),
    )))
    return provider, result


def partial_incident_bundle():
    value = rich_bundle()
    dependency = event(
        value.evidence,
        source_type="dependency_index",
        source_object="auto-1",
        event_type="dependency_relationship",
        summary="The automation has an exact dependency on sensor.focus.",
        timestamp="2026-07-21T11:50:00Z",
        entity_id="sensor.focus",
        automation_id="auto-1",
        confidence="exact",
        coverage_status="partial",
    )
    value.events = deduplicate_and_sort([*value.events, dependency])
    coverage = next(item for item in value.coverage if item.source_type == "dependency_index")
    coverage.completeness = "partial"
    coverage.required_for_assessment = True
    coverage.items_examined = 725
    coverage.failed_items = 0
    coverage.failure_category = None
    coverage.coverage_limitations = ["dependency_index_unsupported_source_types"]
    coverage.warnings = [
        f"Reliable {source_type} configuration access is not available."
        for source_type in UNSUPPORTED_TYPES
    ]
    return value


class CoverageNormalizationTests(unittest.TestCase):
    def test_successful_partial_coverage_has_no_failure_category(self):
        value = normalize_coverage(
            source_type="dependency_index",
            completeness="partial",
            requested=True,
            required=True,
            items_examined=725,
            failed_items=0,
            limitation_ids=["dependency_index_unsupported_source_types"],
        )
        self.assertEqual(value.completeness, "partial")
        self.assertFalse(value.assessment_complete)
        self.assertEqual(value.failed_items, 0)
        self.assertIsNone(value.failure_category)
        self.assertFalse(value.actual_failure)

    def test_failure_timeout_item_failure_unsupported_and_not_requested_are_distinct(self):
        failed = normalize_coverage(
            source_type="dependency_index", completeness="failed", requested=True,
            required=True, failed_items=1, failure_category="provider_upstream_error",
        )
        timeout = normalize_coverage(
            source_type="dependency_index", completeness="failed", requested=True,
            required=True, failed_items=1, failure_category="provider_timeout",
        )
        item = normalize_coverage(
            source_type="dependency_index", completeness="partial", requested=True,
            required=True, items_examined=1, failed_items=1,
        )
        unsupported = normalize_coverage(
            source_type="script", completeness="unavailable", requested=True,
            required=True, unsupported=True,
        )
        omitted = normalize_coverage(
            source_type="dependency_index", completeness="failed", requested=False,
            required=False, failed_items=9, failure_category="provider_upstream_error",
        )
        self.assertEqual(failed.failure_category, "provider_upstream_error")
        self.assertEqual(timeout.failure_category, "provider_timeout")
        self.assertEqual(item.failure_category, "item_read_failure")
        self.assertEqual(item.completeness, "partial")
        self.assertEqual(unsupported.completeness, "not_supported")
        self.assertIsNone(unsupported.failure_category)
        self.assertEqual(omitted.completeness, "not_requested")
        self.assertIsNone(omitted.failure_category)


class DependencyCoverageAdapterTests(unittest.TestCase):
    def setUp(self):
        METRICS.reset()

    def test_successful_partial_index_remains_usable_without_provider_failure(self):
        _provider, result = fetch(SnapshotIndex())
        bundle = result.data
        coverage = next(item for item in bundle.coverage if item.source_type == "dependency_index")
        self.assertEqual(coverage.completeness, "partial")
        self.assertFalse(coverage.assessment_complete)
        self.assertEqual(coverage.items_examined, 1)
        self.assertEqual(coverage.failed_items, 0)
        self.assertIsNone(coverage.failure_category)
        self.assertIn("dependency_index_unsupported_source_types", coverage.coverage_limitations)
        self.assertEqual(coverage.warnings, sorted(coverage.warnings))
        self.assertTrue(all(
            source_type in " ".join(coverage.warnings)
            for source_type in UNSUPPORTED_TYPES
        ))
        self.assertEqual(len([item for item in bundle.events if item.source_type == "dependency_index"]), 1)
        routing = METRICS.snapshot()["provider_routing"]
        self.assertEqual(routing["failures_by_provider"].get("engineering", 0), 0)
        self.assertGreater(routing["partial_results"], 0)

    def test_actual_index_failure_is_failed_and_fabricates_no_event(self):
        _provider, result = fetch(SnapshotIndex(error=RuntimeError("index failed")))
        coverage = next(item for item in result.data.coverage if item.source_type == "dependency_index")
        self.assertEqual(coverage.completeness, "failed")
        self.assertEqual(coverage.failure_category, "provider_upstream_error")
        self.assertEqual(coverage.failed_items, 1)
        self.assertFalse(any(item.source_type == "dependency_index" for item in result.data.events))
        self.assertEqual(
            METRICS.snapshot()["provider_routing"]["failures_by_provider"]["engineering"],
            1,
        )

    def test_actual_index_failure_is_missing_evidence_and_one_source_failure(self):
        index = SnapshotIndex(error=RuntimeError("index failed"))
        provider = DirectHaIncidentProvider(
            index,
            FakeRestClient(),
            FakeWebSocketClient(),
            NeverReliabilityProvider(),
        )
        service = IncidentCorrelationService(
            provider,
            clock=lambda: datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc),
        )
        output = asyncio.run(service.analyze(
            focus_entity_id="sensor.focus",
            automation_id="",
            related_entity_ids=[],
            lookback_hours=24,
            correlation_window_minutes=10,
            trace_limit=10,
            include_dependency_context=True,
            include_integrity_context=False,
            include_reliability_context=False,
            detail_level="standard",
            limit=20,
            cursor="",
            refresh_index=True,
        ))
        self.assertEqual(output.data["result_status"], "partial")
        health = METRICS.snapshot()
        self.assertEqual(health["incident_correlation"]["source_failures"], 1)
        self.assertEqual(
            health["provider_routing"]["failures_by_provider"]["engineering"],
            1,
        )

    def test_index_timeout_uses_timeout_category(self):
        _provider, result = fetch(SnapshotIndex(error=TimeoutError("timed out")))
        coverage = next(item for item in result.data.coverage if item.source_type == "dependency_index")
        self.assertEqual(coverage.completeness, "failed")
        self.assertEqual(coverage.failure_category, "provider_timeout")

    def test_item_failure_retains_usable_dependency_evidence(self):
        _provider, result = fetch(SnapshotIndex(snapshot=dependency_snapshot(failed_items=1)))
        coverage = next(item for item in result.data.coverage if item.source_type == "dependency_index")
        self.assertEqual(coverage.completeness, "partial")
        self.assertEqual(coverage.failed_items, 1)
        self.assertEqual(coverage.failure_category, "item_read_failure")
        self.assertTrue(any(item.source_type == "dependency_index" for item in result.data.events))


class IncidentCoverageContractTests(unittest.TestCase):
    def setUp(self):
        METRICS.reset()

    def service(self, bundle):
        return IncidentCorrelationService(
            FakeProvider(bundle),
            clock=lambda: ANALYSIS_TIME,
            cursor_key=b"c" * 32,
        )

    def test_partial_dependency_evidence_is_a_limitation_not_missing(self):
        output = analyze(self.service(partial_incident_bundle()), limit=100)
        dependency = next(
            item for item in output.data["source_coverage_matrix"]
            if item["source_type"] == "dependency_index"
        )
        self.assertEqual(dependency["completeness"], "partial")
        self.assertFalse(dependency["assessment_complete"])
        self.assertEqual(dependency["failed_items"], 0)
        self.assertIsNone(dependency["failure_category"])
        for hypothesis in output.data["hypotheses"]:
            self.assertNotIn("dependency_index", hypothesis.get("missing_evidence", []))
            self.assertIn(
                "dependency_index_unsupported_source_types",
                hypothesis.get("coverage_limitations", []),
            )
        refs = {
            reference
            for hypothesis in output.data["hypotheses"]
            for reference in hypothesis.get("supporting_evidence_reference_ids", [])
        }
        self.assertTrue(any(
            reference.reference_id in refs
            for reference in partial_incident_bundle().evidence.values()
            if reference.source_type == "dependency_index"
        ))
        health = METRICS.snapshot()
        self.assertEqual(health["incident_correlation"]["source_failures"], 0)
        self.assertEqual(health["incident_correlation"]["partial_count"], 1)
        self.assertEqual(
            health["provider_routing"]["failures_by_provider"].get("engineering", 0),
            0,
        )

    def test_actual_failed_source_increments_source_failure_once(self):
        value = make_bundle(rich_bundle().events, rich_bundle().evidence, partial=True)
        output = analyze(self.service(value))
        self.assertEqual(output.data["result_status"], "partial")
        self.assertEqual(METRICS.snapshot()["incident_correlation"]["source_failures"], 1)
        self.assertTrue(any(
            "entity_history" in hypothesis.get("missing_evidence", [])
            for hypothesis in output.data["hypotheses"]
        ))

    def test_failed_dependency_index_is_missing_not_a_coverage_only_limit(self):
        value = rich_bundle()
        dependency = next(item for item in value.coverage if item.source_type == "dependency_index")
        dependency.completeness = "failed"
        dependency.required_for_assessment = True
        dependency.items_examined = 0
        dependency.failed_items = 1
        dependency.failure_category = "provider_upstream_error"
        output = analyze(self.service(value), limit=100)
        self.assertTrue(any(
            "dependency_index" in hypothesis.get("missing_evidence", [])
            for hypothesis in output.data["hypotheses"]
        ))

    def test_no_dependency_context_has_no_index_penalty(self):
        value = rich_bundle()
        dependency = next(item for item in value.coverage if item.source_type == "dependency_index")
        dependency.completeness = "not_requested"
        dependency.requested = False
        dependency.required_for_assessment = False
        dependency.provider = "none"
        value.index = {"requested": False, "generation": None, "fingerprint": None, "cache_hit": False}
        output = analyze(
            self.service(value),
            include_dependency_context=False,
            include_integrity_context=False,
            include_reliability_context=False,
            refresh_index=False,
        )
        for hypothesis in output.data["hypotheses"]:
            self.assertNotIn("dependency_index", hypothesis.get("missing_evidence", []))
            self.assertFalse(any(
                item.startswith("dependency_index_")
                for item in hypothesis.get("coverage_limitations", [])
            ))

    def test_corrected_coverage_is_frozen_across_multiple_cursor_pages(self):
        service = self.service(partial_incident_bundle())
        first = analyze(service)
        cursor = first.data["pagination"]["next_cursor"]
        coverage = first.data["source_coverage_matrix"]
        incident_id = first.data["incident_id"]
        timestamp = first.data["analysis_timestamp"]
        totals = first.data["hypothesis_count"]
        pages = 1
        while cursor:
            output = analyze(service, cursor=cursor, refresh_index=False)
            pages += 1
            self.assertEqual(output.data["incident_id"], incident_id)
            self.assertEqual(output.data["analysis_timestamp"], timestamp)
            self.assertEqual(output.data["hypothesis_count"], totals)
            self.assertEqual(output.data["source_coverage_matrix"], coverage)
            for hypothesis in output.data["hypotheses"]:
                self.assertNotIn("dependency_index", hypothesis.get("missing_evidence", []))
                self.assertIn(
                    "dependency_index_unsupported_source_types",
                    hypothesis.get("coverage_limitations", []),
                )
            cursor = output.data["pagination"]["next_cursor"]
        self.assertGreaterEqual(pages, 3)
        self.assertEqual(len(service.provider.calls), 1)
        health = METRICS.snapshot()["incident_correlation"]
        self.assertEqual(health["partial_count"], 1)
        self.assertEqual(health["source_failures"], 0)
        self.assertEqual(health["cursor_continuations"], pages - 1)

    def test_audit_summary_contains_counts_not_warning_payloads(self):
        telemetry, token = begin_request("beta20-audit-coverage")
        try:
            analyze(self.service(partial_incident_bundle()), limit=100)
            summary = telemetry.audit_context["incident_correlation_summary"]
        finally:
            end_request(token)
        self.assertEqual(summary["source_failure_count"], 0)
        self.assertGreater(summary["coverage_limitation_count"], 0)
        self.assertNotIn("warnings", summary)
        self.assertNotIn("source_coverage", summary)


if __name__ == "__main__":
    unittest.main()
