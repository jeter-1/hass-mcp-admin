"""HAMCP-508: a bounded result says it is bounded.

The silent-incompleteness audit (SI-1 to SI-7) found reads that cut rows at an
Engineering bound and still read complete. Each case below fills a source one
past its bound, checks that the output names what was left out, and checks the
exact bound stays complete. Output keys are additive only.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.models import (  # noqa: E402
    DependencyFinding,
    DependencyScanResult,
    DynamicReference,
    SourceCoverageItem,
    evidence_id,
)
from ha_mcp_engineering.dependency.provider import DependencySourceProvider  # noqa: E402
from ha_mcp_engineering.dependency.service import EntityDependencyAnalysisService  # noqa: E402
from ha_mcp_engineering.incident import provider as incident_provider  # noqa: E402
from ha_mcp_engineering.incident.models import IncidentSourceCoverage  # noqa: E402
from ha_mcp_engineering.incident.provider import DirectHaIncidentProvider  # noqa: E402
from ha_mcp_engineering.providers import EvidenceRequest, ProviderCapability  # noqa: E402
from ha_mcp_engineering.reliability.models import (  # noqa: E402
    ReliabilityEvidenceBundle,
    ReliabilitySourceCoverage,
)
from ha_mcp_engineering.tools import compatibility  # noqa: E402


# ----- SI-1: the list tools ------------------------------------------------


def _states(count):
    return [{"entity_id": f"automation.a{index:03d}", "state": "on",
             "attributes": {"friendly_name": f"A {index}", "id": str(index)}} for index in range(count)]


def _devices(count):
    return [{"id": f"d{index}", "name": f"Device {index}", "manufacturer": "Acme", "model": "M"}
            for index in range(count)]


def _registry(count):
    return [{"entity_id": f"sensor.s{index:03d}", "platform": "test"} for index in range(count)]


class ListToolTotals(unittest.IsolatedAsyncioTestCase):
    async def call(self, tool, rows, **kwargs):
        name = "rest" if tool == "list_automations" else "ws_command"
        with patch.object(compatibility, name, new=AsyncMock(return_value=rows)):
            payload = json.loads(await getattr(compatibility, tool)(**kwargs))
        return payload.get("data", payload)

    async def test_a_default_page_past_100_says_it_is_truncated(self):
        for tool, rows, key in (
            ("list_automations", _states(101), "automations"),
            ("list_devices", _devices(101), "devices"),
            ("list_entity_registry", _registry(101), "entities"),
        ):
            with self.subTest(tool=tool):
                data = await self.call(tool, rows)
                # The default page is unchanged.
                self.assertEqual((data["count"], len(data[key])), (100, 100))
                self.assertEqual((data["returned"], data["total"], data["truncated"]), (100, 101, True))

    async def test_a_page_that_holds_every_match_is_not_truncated(self):
        for tool, rows in (
            ("list_automations", _states(100)),
            ("list_devices", _devices(100)),
            ("list_entity_registry", _registry(100)),
        ):
            with self.subTest(tool=tool):
                data = await self.call(tool, rows)
                self.assertEqual((data["returned"], data["total"], data["truncated"]), (100, 100, False))

    async def test_total_counts_filtered_matches_only(self):
        data = await self.call("list_automations", _states(30), query="a01", limit=5)
        # a010..a019 match.
        self.assertEqual((data["returned"], data["total"], data["truncated"]), (5, 10, True))

    async def test_device_total_counts_filtered_matches_only(self):
        data = await self.call("list_devices", _devices(30), query="Device 1", limit=5)
        # Device 1 and Device 10..19 match.
        self.assertEqual((data["returned"], data["total"], data["truncated"]), (5, 11, True))

    async def test_entity_registry_total_counts_filtered_matches_only(self):
        data = await self.call("list_entity_registry", _registry(30), query="s01", limit=5)
        # sensor.s010..s019 match.
        self.assertEqual((data["returned"], data["total"], data["truncated"]), (5, 10, True))


# ----- SI-2 and SI-6: the incident provider ---------------------------------


ANALYSIS_TIME = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def _history(count, malformed=0):
    rows = [{"entity_id": "sensor.focus", "state": str(index),
             "last_changed": (ANALYSIS_TIME - timedelta(minutes=count - index)).isoformat()}
            for index in range(count)]
    return ["not a record"] * malformed + rows


def _logbook(count):
    return [{"entity_id": "sensor.focus", "name": "Focus", "message": f"changed to {index}",
             "when": (ANALYSIS_TIME - timedelta(minutes=count - index)).isoformat()}
            for index in range(count)]


class _Finding:
    def __init__(self, index):
        self.index = index

    def public(self):
        return {"finding_id": f"f{self.index}", "title": f"Finding {self.index}", "severity": "low",
                "confidence": "medium"}


class IncidentBounds(unittest.TestCase):
    def collect(self, *, history=(), logbook=(), traces=None, findings=0):
        async def request(method, path, **kwargs):
            path = str(path)
            if path == "/states":
                return [{"entity_id": "sensor.focus", "state": "on", "last_changed": ANALYSIS_TIME.isoformat()}]
            if path.startswith("/history/period"):
                return [list(history)]
            if path.startswith("/logbook"):
                return list(logbook)
            raise AssertionError(f"unexpected read {path}")

        rest = type("Rest", (), {"request": staticmethod(request)})()

        class WebSocket:
            async def command(self, payload):
                if payload.get("type") == "config/entity_registry/list":
                    return [{"entity_id": "sensor.focus", "platform": "test"}]
                if payload.get("type") == "system_log/list":
                    return []
                raise AssertionError(f"unsupported WebSocket command {payload}")

        class Index:
            async def get(self, *, refresh=False):
                raise AssertionError("dependency index should not be requested")

            def active_identity(self):
                return {"valid": False, "generation": 0, "fingerprint": ""}

        class Reliability:
            async def collect(self, **values):
                return ReliabilityEvidenceBundle(
                    automation_id="synthetic", automation={}, configuration={}, configuration_fingerprint="",
                    blueprint=None, blueprint_path=None, references=[], dynamic_references=[],
                    traces=list(traces or []), system_log_entries=[],
                    coverage=[
                        ReliabilitySourceCoverage("automation_config", "direct_ha_api", "automation_config",
                                                  "complete", 1),
                        ReliabilitySourceCoverage("automation_traces", "direct_ha_api", "automation_trace",
                                                  "complete", len(traces or []), collection_state="complete"),
                    ],
                )

        provider = DirectHaIncidentProvider(
            Index(), rest, WebSocket(), Reliability(), secret="not-a-secret", ha_token="not-a-token"
        )
        automation = traces is not None or findings > 0
        query = {
            "analysis_timestamp": ANALYSIS_TIME.isoformat().replace("+00:00", "Z"),
            "focus_entity_id": "sensor.focus",
            "automation_id": "synthetic" if automation else "",
            "related_entity_ids": [],
            "lookback_hours": 24,
            "trace_limit": 10,
            "include_dependency_context": False,
            "include_integrity_context": False,
            "include_reliability_context": findings > 0,
            "refresh_index": False,
        }
        with patch.object(incident_provider, "evaluate_rules", lambda bundle: [_Finding(i) for i in range(findings)]):
            return asyncio.run(provider.collect(query))

    @staticmethod
    def row(bundle, source_type):
        return next(item for item in bundle.coverage if item.source_type == source_type)

    def test_history_and_logbook_one_past_the_bound_are_partial_with_the_count(self):
        bundle = self.collect(history=_history(501), logbook=_logbook(301))
        for source, limitation, available in (
            ("entity_history", "entity_history_row_limit_exceeded", 501),
            ("logbook", "logbook_row_limit_exceeded", 301),
        ):
            with self.subTest(source=source):
                row = self.row(bundle, source)
                self.assertEqual(row.completeness, "partial")
                self.assertEqual(row.items_withheld, 1)
                self.assertIn(limitation, row.coverage_limitations)
                self.assertIn(f"1 of {available}", row.warnings[0])
                public = row.public()
                self.assertEqual(public["items_withheld"], 1)
                self.assertFalse(public["assessment_complete"])
        self.assertTrue(bundle.source_partial)

    def test_history_and_logbook_at_the_bound_stay_complete(self):
        bundle = self.collect(history=_history(500), logbook=_logbook(300))
        for source in ("entity_history", "logbook"):
            with self.subTest(source=source):
                row = self.row(bundle, source)
                self.assertEqual((row.completeness, row.items_withheld, row.items_malformed), ("complete", 0, 0))

    def test_malformed_history_rows_are_counted_and_make_the_row_partial(self):
        bundle = self.collect(history=_history(10, malformed=3), logbook=_logbook(5))
        row = self.row(bundle, "entity_history")
        self.assertEqual((row.completeness, row.items_malformed, row.items_withheld), ("partial", 3, 0))
        self.assertIn("entity_history_malformed_rows", row.coverage_limitations)
        self.assertEqual(row.public()["items_malformed"], 3)

    def test_trace_service_calls_past_20_are_partial_with_kept_and_available(self):
        trace = {"run_id": "r1", "started_at": ANALYSIS_TIME.isoformat(),
                 "services": [f"light.turn_on_{index}" for index in range(21)]}
        bundle = self.collect(traces=[trace])
        row = self.row(bundle, "automation_traces")
        self.assertEqual((row.completeness, row.items_withheld), ("partial", 1))
        self.assertIn("automation_traces_service_limit_exceeded", row.coverage_limitations)
        self.assertIn("1 of 21", row.warnings[0])
        self.assertIn("20 kept", row.warnings[0])
        services = [item for item in bundle.events if item.event_type == "service_call_observed"]
        self.assertEqual(len(services), 20)
        # The trace row is required, so its gap makes the whole bundle partial.
        self.assertFalse(row.public()["assessment_complete"])
        self.assertTrue(bundle.source_partial)

    def test_trace_service_calls_at_20_stay_complete(self):
        trace = {"run_id": "r1", "started_at": ANALYSIS_TIME.isoformat(),
                 "services": [f"light.turn_on_{index}" for index in range(20)]}
        row = self.row(self.collect(traces=[trace]), "automation_traces")
        self.assertEqual((row.completeness, row.items_withheld), ("complete", 0))

    def test_reliability_findings_past_100_are_partial_with_kept_and_available(self):
        bundle = self.collect(traces=[], findings=101)
        row = self.row(bundle, "automation_reliability")
        self.assertEqual((row.completeness, row.items_withheld), ("partial", 1))
        self.assertIn("automation_reliability_finding_limit_exceeded", row.coverage_limitations)
        self.assertIn("1 of 101", row.warnings[0])
        self.assertIn("100 kept", row.warnings[0])
        # The reliability row is optional context: alone it does not make the bundle partial.
        self.assertFalse(bundle.source_partial)

    def test_reliability_findings_at_100_stay_complete(self):
        row = self.row(self.collect(traces=[], findings=100), "automation_reliability")
        self.assertEqual((row.completeness, row.items_withheld), ("complete", 0))


# ----- SI-7: coverage public lists ------------------------------------------


class CoveragePublicOmissions(unittest.TestCase):
    def test_lists_past_ten_name_how_many_were_omitted(self):
        row = IncidentSourceCoverage(
            "logbook", "direct_ha_api", "logbook", "partial", True, True, 1,
            warnings=[f"warning {index}" for index in range(13)],
            coverage_limitations=[f"limit_{index:02d}" for index in range(12)] + ["limit_00"],
        )
        public = row.public()
        self.assertEqual(len(public["warnings"]), 10)
        self.assertEqual(public["warnings_omitted"], 3)
        self.assertEqual(len(public["coverage_limitations"]), 10)
        # Duplicates are not counted as omitted.
        self.assertEqual(public["coverage_limitations_omitted"], 2)

    def test_short_lists_omit_nothing(self):
        public = IncidentSourceCoverage(
            "logbook", "direct_ha_api", "logbook", "complete", True, True, 1, warnings=["one"],
        ).public()
        self.assertEqual((public["warnings_omitted"], public["coverage_limitations_omitted"]), (0, 0))


# ----- SI-3 and SI-4: dependency analysis -----------------------------------


TARGET = "sensor.example"


class FakeProvider(DependencySourceProvider):
    provider_id = "direct_ha_api"
    capabilities = frozenset({ProviderCapability.DEPENDENCY_ANALYSIS})

    def __init__(self, result):
        self.result = result

    @property
    def available(self):
        return True

    async def scan(self):
        return self.result

    async def fetch(self, request: EvidenceRequest):
        raise NotImplementedError


def _coverage():
    values = [SourceCoverageItem(source, "direct_ha_api", f"{source}_configuration", "complete")
              for source in ("automation", "blueprint", "script", "scene", "group", "template", "dashboard")]
    values.append(SourceCoverageItem("entity_metadata", "direct_ha_api", "current_entity_state", "complete",
                                     fallback_occurred=False, policy="transitional_direct exact administrative read"))
    return values


def _finding(target, source_id, *, source_type="automation", source_entity_id=None, relation="trigger",
             path="$.trigger[0].entity_id"):
    return DependencyFinding(
        evidence_id=evidence_id(target, relation, path, source_id),
        target_entity_id=target, source_type=source_type, source_id=source_id,
        source_entity_id=source_entity_id or f"automation.{source_id}", source_name=source_id,
        relation=relation, config_path=path,
    )


def _dynamic(count):
    return [DynamicReference(f"dyn-{index:03d}", "automation", f"a{index:03d}", "$.action",
                             "Dynamic target could not be resolved statically.") for index in range(count)]


class DependencyBounds(unittest.IsolatedAsyncioTestCase):
    async def analyze(self, findings=(), dynamic=(), **kwargs):
        result = DependencyScanResult(list(findings), list(dynamic), {}, _coverage())
        service = EntityDependencyAnalysisService(DependencyIndex(FakeProvider(result)))
        output = await service.analyze(entity_id=TARGET, **kwargs)
        return {**output.data, "warnings": output.warnings, "partial": output.partial}

    @staticmethod
    def through_group(count):
        # Scenes use the group. Automation and script scope would make any
        # indirect walk partial through the script call graph, hiding the bound.
        membership = _finding(TARGET, "g", source_type="group", source_entity_id="group.example",
                              relation="group_member", path="$.entities")
        return [membership] + [
            _finding("group.example", f"uses{index:03d}", source_type="scene",
                     source_entity_id=f"scene.uses{index:03d}", relation="scene_entity", path="$.entities")
            for index in range(count)
        ]

    async def walk(self, count):
        return await self.analyze(self.through_group(count), include_indirect=True, max_depth=2, limit=20,
                                  source_types=["group", "scene"])

    async def test_an_indirect_walk_past_500_is_partial_and_names_the_gap(self):
        data = await self.walk(501)
        overview = data["overview"]
        self.assertEqual(overview["indirect_reference_count"], 500)
        self.assertEqual(overview["indirect_reference_gaps"], ["indirect_dependency_limit_exceeded"])
        self.assertFalse(overview["coverage_complete"])
        self.assertFalse(data["pagination"]["total_complete"])
        self.assertIn("indirect_dependency_limit_exceeded", " ".join(data["warnings"]))

    async def test_an_indirect_walk_that_stops_at_500_with_nodes_queued_names_the_gap(self):
        data = await self.walk(500)
        overview = data["overview"]
        self.assertEqual(overview["indirect_reference_count"], 500)
        self.assertEqual(overview["indirect_reference_gaps"], ["indirect_dependency_limit_exceeded"])
        self.assertFalse(overview["coverage_complete"])
        self.assertFalse(data["pagination"]["total_complete"])

    async def test_an_indirect_walk_inside_the_bound_is_complete(self):
        data = await self.walk(499)
        self.assertEqual(data["overview"]["indirect_reference_count"], 499)
        self.assertEqual(data["overview"]["indirect_reference_gaps"], [])
        self.assertTrue(data["overview"]["coverage_complete"])
        self.assertTrue(data["pagination"]["total_complete"])

    async def test_dynamic_references_past_ten_are_counted_before_the_slice(self):
        data = await self.analyze(dynamic=_dynamic(11), source_types=["automation"])
        overview = data["overview"]
        self.assertEqual(overview["unresolved_dynamic_reference_count"], 11)
        self.assertEqual(overview["unresolved_dynamic_reference_withheld"], 1)
        self.assertFalse(overview["coverage_complete"])
        self.assertEqual(sum("could not be resolved statically" in item for item in data["warnings"]), 10)
        self.assertIn("unresolved_dynamic_reference_limit_exceeded", " ".join(data["warnings"]))

    async def test_ten_dynamic_references_withhold_none(self):
        overview = (await self.analyze(dynamic=_dynamic(10), source_types=["automation"]))["overview"]
        self.assertEqual((overview["unresolved_dynamic_reference_count"],
                          overview["unresolved_dynamic_reference_withheld"]), (10, 0))
        self.assertTrue(overview["coverage_complete"])


if __name__ == "__main__":
    unittest.main()
