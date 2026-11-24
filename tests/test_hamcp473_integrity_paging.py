"""HAMCP-473: a client can walk every integrity finding under the response limit.

All values are synthetic. The fixture mixes the four severities, exact
references, unresolved dynamic references and orphan registry candidates.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.tools import analysis
from ha_mcp_engineering.integrity.service import (
    COMPACT_FINDING_KEYS,
    ConfigurationIntegrityAnalysisService,
)
from tests import test_configuration_integrity_analysis as fixtures

BUDGET = 60_000
SECRET = "synthetic-provider-secret-hamcp473"


def mixed_bundle(*, high=900, medium=200, low=82, info=40, dynamic=60, unsupported=()):
    """Synthetic evidence giving ``high + medium + low + info + dynamic`` findings."""

    refs, states, registry = [], {}, {}
    for i in range(high):
        refs.append(fixtures.reference(f"sensor.gone_{i}", source_id=f"auto_{i % 300}",
                                       path=f"$.condition[{i % 9}].entity_id"))
    for i in range(medium):
        refs.append(fixtures.reference(f"sensor.gone_off_{i}", source_id=f"off_{i % 50}",
                                       source_state="off", path=f"$.action[{i % 5}].target.entity_id"))
    for i in range(low):
        # Enabled consumers of an "unknown" state are low; registry-only
        # entities with no consumer would also be low orphan candidates.
        states[f"sensor.unknown_{i}"] = {"state": "unknown"}
        refs.append(fixtures.reference(f"sensor.unknown_{i}", source_id=f"low_{i % 20}"))
    for i in range(info):
        states[f"sensor.quiet_{i}"] = {"state": "unknown"}
        refs.append(fixtures.reference(f"sensor.quiet_{i}", source_id=f"idle_{i % 10}", source_state="off"))
    dynamics = [fixtures.dynamic(source_id=f"dyn_{i}", path=f"$.action[{i}]") for i in range(dynamic)]
    return fixtures.bundle(references=refs, dynamics=dynamics, states=states, registry=registry,
                           unsupported=unsupported)


def make_service(value=None):
    provider = fixtures.FakeProvider(value or mixed_bundle())
    service = ConfigurationIntegrityAnalysisService(
        provider, clock=lambda: fixtures.ANALYSIS_TIME,
        cursor_key=b"synthetic-hamcp473-paging-key-00",
    )
    return service, provider


async def read(service, *, budget=BUDGET, access_secret="", **arguments):
    arguments = {"source_types": list(fixtures.SOURCE_TYPES), "include_orphan_candidates": False,
                 "detail_level": "compact", "limit": 100, "cursor": "", **arguments}
    with patch.object(analysis, "CONFIGURATION_INTEGRITY_ANALYSIS", SimpleNamespace(require=lambda: service)), \
            patch.object(analysis, "SETTINGS", SimpleNamespace(response_size_limit=budget, access_secret=access_secret)):
        raw = await analysis.configuration_integrity_analysis(**arguments)
    return raw, json.loads(raw)


class CompactWalkTests(unittest.IsolatedAsyncioTestCase):
    async def walk(self, service, **arguments):
        cursor, pages = "", []
        for _ in range(200):
            raw, response = await read(service, cursor=cursor, **arguments)
            self.assertLessEqual(len(raw.encode("utf-8")), BUDGET)
            self.assertTrue(response["success"], response.get("error_code"))
            self.assertNotIn("response_completeness", response)
            pages.append(response["data"])
            cursor = response["data"]["pagination"]["next_cursor"]
            if cursor is None:
                return pages
        self.fail("The walk did not terminate")

    async def test_full_walk_returns_every_finding_exactly_once_under_the_limit(self):
        service, provider = make_service()
        original = deepcopy(provider.value)
        pages = await self.walk(service)
        findings = [item for page in pages for item in page["findings"]]
        total = 900 + 200 + 82 + 40 + 60
        self.assertEqual(len(findings), total)
        self.assertEqual(len({item["finding_id"] for item in findings}), total)
        self.assertTrue(all(page["finding_count"] == total for page in pages))
        self.assertEqual(sum(page["pagination"]["returned"] for page in pages), total)
        # Practical paging: every page but the last is full.
        self.assertTrue(all(page["pagination"]["returned"] == 100 for page in pages[:-1]))
        self.assertEqual(len(pages), 13)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.value, original)
        self.assertEqual(len(service.pagination_snapshots._values), 0)
        self.assertEqual(pages[0]["findings_by_severity"], {"high": 900, "medium": 260, "low": 82, "info": 40})
        self.assertEqual({page["result_status"] for page in pages[:-1]}, {"partial"})
        self.assertEqual(pages[-1]["result_status"], "success")

    async def test_compact_records_carry_no_evidence_text_or_review_items(self):
        service, _ = make_service()
        raw, response = await read(service)
        data = response["data"]
        self.assertEqual(data["evidence_references"], [])
        for item in data["findings"]:
            # The one key a compact record adds is the count of its paths.
            self.assertLessEqual(set(item), {*COMPACT_FINDING_KEYS, "configuration_path_count"})
            self.assertNotIn("evidence_references", item)
            self.assertLessEqual(len(item.get("configuration_paths", [])), 3)
            if "configuration_paths" in item:
                self.assertGreaterEqual(item["configuration_path_count"], len(item["configuration_paths"]))
        summary = data["dynamic_reference_summary"]
        self.assertIs(summary["review_items_omitted"], True)
        self.assertNotIn("review_items", summary)
        self.assertEqual(summary["unresolved_in_requested_scope_count"], 60)
        for text in ("Exact static reference.", "sensor.example", "An enabled automation"):
            self.assertNotIn(text, raw)

    async def test_a_compact_finding_counts_the_paths_it_leaves_out(self):
        value = mixed_bundle(high=0, medium=0, low=0, info=0, dynamic=0)
        value.exact_references.extend(
            fixtures.reference("sensor.gone_paths", source_id="auto_paths", path=f"$.branch[{i}].entity_id")
            for i in range(6)
        )
        responses = {}
        for detail_level in ("standard", "compact"):
            service, _ = make_service(deepcopy(value))
            _, response = await read(service, detail_level=detail_level)
            [finding] = response["data"]["findings"]
            responses[detail_level] = finding
        self.assertEqual(len(responses["standard"]["configuration_paths"]), 6)
        self.assertNotIn("configuration_path_count", responses["standard"])
        compact = responses["compact"]
        self.assertEqual(compact["configuration_paths"], responses["standard"]["configuration_paths"][:3])
        self.assertEqual(compact["configuration_path_count"], 6)

    async def test_standard_detail_is_unchanged_by_the_new_inputs(self):
        outputs = []
        for arguments in ({}, {"severities": []}, {"severities": ["high", "medium", "low", "info"]}):
            service, _ = make_service()
            _, response = await read(service, detail_level="standard", limit=20, **arguments)
            data = response["data"]
            outputs.append((data["findings"], data["evidence_references"], data["finding_count"],
                            data["dynamic_reference_summary"]))
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0], outputs[2])
        self.assertIn("review_items", outputs[0][3])
        self.assertTrue(all("explanation" in item for item in outputs[0][0]))
        self.assertTrue(outputs[0][1])

    async def test_secret_in_a_compact_record_is_still_withheld(self):
        value = mixed_bundle(high=3, medium=0, low=0, info=0, dynamic=0)
        value.exact_references[0]["source_id"] = SECRET
        service, _ = make_service(value)
        raw, response = await read(service, access_secret=SECRET)
        self.assertTrue(response["success"])
        self.assertNotIn(SECRET, raw)
        self.assertTrue(any("final credential screen" in item for item in response["warnings"]))
        self.assertEqual(response["data"]["pagination"]["returned"], 3)


class SeverityFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_severity_walk_returns_exactly_its_findings(self):
        expected = {"high": 900, "medium": 260, "low": 82, "info": 40}
        seen = set()
        for severity, count in expected.items():
            with self.subTest(severity=severity):
                service, provider = make_service()
                pages = await CompactWalkTests.walk(self, service, severities=[severity])
                findings = [item for page in pages for item in page["findings"]]
                self.assertEqual(len(findings), count)
                self.assertEqual({item["severity"] for item in findings}, {severity})
                self.assertEqual(pages[0]["finding_count"], count)
                self.assertEqual(pages[0]["findings_by_severity"][severity], count)
                self.assertEqual(sum(pages[0]["findings_by_severity"].values()), count)
                self.assertEqual(len(provider.calls), 1)
                seen.update(item["finding_id"] for item in findings)
        self.assertEqual(len(seen), sum(expected.values()))

    async def test_filter_applies_before_the_whole_analysis_cap(self):
        value = mixed_bundle(high=10, medium=2_050, low=0, info=0, dynamic=0)
        service, _ = make_service(value)
        _, unfiltered = await read(service, limit=1)
        self.assertEqual(unfiltered["data"]["finding_count"], 2_000)
        self.assertEqual(unfiltered["data"]["final_assessment"], "review_required")
        service, _ = make_service(value)
        pages = await CompactWalkTests.walk(self, service, severities=["high"])
        self.assertEqual([item["severity"] for page in pages for item in page["findings"]], ["high"] * 10)
        self.assertEqual(pages[-1]["result_status"], "success")
        self.assertEqual(pages[0]["severity_filter"]["excluded_by_severity"],
                         {"medium": 2_050, "low": 0, "info": 0})
        self.assertEqual(pages[0]["severity_filter"]["truncated_selected_count"], 0)

    async def test_the_cap_on_a_selected_severity_is_counted(self):
        value = mixed_bundle(high=2_050, medium=10, low=0, info=0, dynamic=0)
        service, _ = make_service(value)
        _, response = await read(service, limit=1, severities=["high"])
        data = response["data"]
        self.assertEqual(data["finding_count"], 2_000)
        self.assertEqual(data["result_status"], "partial")
        self.assertEqual(data["final_assessment"], "review_required")
        disclosed = data["severity_filter"]
        self.assertEqual(disclosed["excluded_finding_count"], 10)
        self.assertEqual(disclosed["excluded_by_severity"], {"medium": 10, "low": 0, "info": 0})
        self.assertEqual(disclosed["truncated_selected_count"], 50)
        # The three counts reconcile to the classified total.
        self.assertEqual(
            data["finding_count"] + disclosed["excluded_finding_count"] + disclosed["truncated_selected_count"],
            2_060,
        )
        self.assertTrue(any("finding cap was reached" in item for item in response["warnings"]))

    async def test_filter_that_hides_findings_keeps_the_assessment_and_discloses_it(self):
        value = mixed_bundle(high=5, medium=0, low=0, info=0, dynamic=0)
        service, _ = make_service(value)
        pages = await CompactWalkTests.walk(self, service, severities=["info"])
        self.assertEqual(len(pages), 1)
        data = pages[0]
        self.assertEqual(data["findings"], [])
        self.assertEqual(data["finding_count"], 0)
        self.assertEqual(data["final_assessment"], "review_required")
        self.assertIs(data["manual_review_required"], True)
        self.assertEqual(data["severity_filter"], {
            "severities": ["info"], "excluded_finding_count": 5,
            "excluded_by_severity": {"high": 5, "medium": 0, "low": 0},
            "truncated_selected_count": 0,
            "assessment_includes_excluded_findings": True,
        })
        # The zeros describe the selected severities only, and say so.
        self.assertEqual(data["findings_by_severity"]["high"], 0)
        self.assertEqual(data["counter_semantics"]["finding_count"],
                         "selected_severity_findings_within_whole_bounded_analysis")
        self.assertEqual(data["counter_semantics"]["findings_by_severity"], "selected_severities_only")

    async def test_severity_filter_disclosure_follows_every_page_and_only_a_narrowed_query(self):
        service, _ = make_service()
        pages = await CompactWalkTests.walk(self, service, severities=["high", "low"])
        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertEqual(page["severity_filter"]["excluded_finding_count"], 260 + 40)
        for arguments in ({}, {"severities": ["info", "low", "medium", "high"]}):
            service, _ = make_service()
            _, response = await read(service, **arguments)
            self.assertNotIn("severity_filter", response["data"])
            self.assertEqual(response["data"]["counter_semantics"]["finding_count"],
                             "whole_bounded_analysis_findings")
            self.assertNotIn("findings_by_severity", response["data"]["counter_semantics"])

    async def test_cursor_is_bound_to_the_filter_and_detail_level(self):
        for change in ({"severities": []}, {"severities": ["medium"]}, {"detail_level": "standard"},
                       {"finding_types": ["missing_entity_reference"]}):
            with self.subTest(change=change):
                service, provider = make_service()
                _, first = await read(service, severities=["high"])
                cursor = first["data"]["pagination"]["next_cursor"]
                arguments = {"severities": ["high"], **change}
                _, result = await read(service, cursor=cursor, **arguments)
                self.assertFalse(result["success"])
                self.assertEqual(result["error_code"], "stale_cursor")
                self.assertEqual(len(provider.calls), 1)
                _, resumed = await read(service, cursor=cursor, severities=["high"])
                self.assertTrue(resumed["success"])

    async def test_unsupported_severity_and_detail_values_are_refused_before_io(self):
        for arguments in ({"severities": ["critical"]}, {"severities": "high"},
                          {"severities": ["high"] * 5}, {"detail_level": "tiny"}):
            with self.subTest(arguments=arguments):
                service, provider = make_service()
                _, result = await read(service, **arguments)
                self.assertFalse(result["success"])
                self.assertEqual(result["error_code"], "invalid_request")
                self.assertEqual(provider.calls, [])

    async def test_partial_coverage_keeps_every_compact_page_partial(self):
        value = mixed_bundle(high=150, medium=0, low=0, info=0, dynamic=0, unsupported=("dashboard",))
        service, _ = make_service(value)
        pages = await CompactWalkTests.walk(self, service)
        self.assertEqual(sum(len(page["findings"]) for page in pages), 150)
        self.assertEqual({page["result_status"] for page in pages}, {"partial"})


class PageFillTests(unittest.IsolatedAsyncioTestCase):
    async def test_oversized_page_returns_the_largest_page_that_fits(self):
        service, _ = make_service()
        probes = []

        def page_fits(output):
            size = len(json.dumps(output.data, separators=(",", ":")))
            probes.append((len(output.data["findings"]), size <= 20_000))
            return size <= 20_000

        output = await service.analyze(
            source_types=list(fixtures.SOURCE_TYPES), include_orphan_candidates=False,
            detail_level="compact", limit=100, page_fits=page_fits,
        )
        returned = output.data["pagination"]["returned"]
        self.assertGreater(returned, 1)
        self.assertLess(returned, 100)
        self.assertIn((returned, True), probes)
        self.assertIn((returned + 1, False), probes)
        self.assertEqual(output.data["pagination"]["clamp_reason"], "response_size_limit")
        self.assertTrue(output.data["pagination"]["has_more"])
        # A bounded search: the full page, then at most seven further probes.
        self.assertLessEqual(len([p for p in probes if p[0] != 100]), 7)


if __name__ == "__main__":
    unittest.main()
