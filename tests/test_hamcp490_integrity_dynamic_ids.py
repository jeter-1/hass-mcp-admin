"""HAMCP-490/491: dynamic-reference finding IDs and the dynamic count.

HAMCP-490: two dynamic references at one path of one source that differ (in
excerpt or upstream evidence ID) are two findings. Each now has its own
``finding_id``; before, both took the path-based ID, and compact rows, which
omit evidence, were byte-identical. Equal references still coalesce, and the
first finding at a path keeps the path-based ID.

HAMCP-491: ``unresolved_dynamic_reference_count`` is not filtered by
``severities``; ``counter_semantics`` now says so on every response.

Reuses the synthetic bundle and provider of the integrity analysis tests.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))

from tests.test_configuration_integrity_analysis import (  # noqa: E402
    ANALYSIS_TIME, FakeProvider, bundle, dynamic,
)
from ha_mcp_engineering.integrity.models import stable_id  # noqa: E402
from ha_mcp_engineering.integrity.rules import classify_integrity  # noqa: E402
from ha_mcp_engineering.integrity.service import (  # noqa: E402
    ConfigurationIntegrityAnalysisService,
)


ROOT = Path(__file__).resolve().parents[1]
PATH = "$.action[0].data"
SEMANTICS = "in_scope_dynamic_references_before_severity_filter"


def base_id(source_id="auto-1", path=PATH):
    return stable_id("integrity", "unresolved_dynamic_reference", "automation", source_id, path)


def at_one_path(count, *, distinct_ids=False):
    """``count`` references at one path, each with its own excerpt."""

    first = dynamic(path=PATH)
    values = [first]
    for index in range(1, count):
        value = dict(first, excerpt=f"{{{{ states(other_{index}) }}}}")
        if distinct_ids:
            value["evidence_id"] = f"dyn_other_{index}"
        values.append(value)
    return values


def dynamic_findings(values):
    findings, evidence, _ = classify_integrity(
        bundle(dynamics=values),
        finding_types=["unresolved_dynamic_reference"],
        include_orphan_candidates=False,
    )
    return [item for item in findings if item.finding_type == "unresolved_dynamic_reference"], evidence


async def analyze(values, **arguments):
    service = ConfigurationIntegrityAnalysisService(
        FakeProvider(bundle(dynamics=values)), clock=lambda: ANALYSIS_TIME,
    )
    return (await service.analyze(include_orphan_candidates=False, **arguments)).data


class FindingIdTests(unittest.IsolatedAsyncioTestCase):
    def test_distinct_evidence_at_one_path_gets_distinct_ids(self):
        for distinct_ids in (False, True):
            with self.subTest(distinct_upstream_ids=distinct_ids):
                findings, evidence = dynamic_findings(at_one_path(3, distinct_ids=distinct_ids))
                ids = [item.finding_id for item in findings]
                self.assertEqual(len(ids), 3)
                self.assertEqual(len(set(ids)), 3)
                # Each finding cites its own evidence record.
                references = [item.evidence_references[0] for item in findings]
                self.assertEqual(len(set(references)), 3)
                self.assertTrue(set(references) <= set(evidence))
                # The first deterministic entry keeps the path-based ID.
                self.assertIn(base_id(), ids)

    def test_a_lone_reference_keeps_the_path_based_id(self):
        findings, _ = dynamic_findings([dynamic(path=PATH)])
        self.assertEqual([item.finding_id for item in findings], [base_id()])

    def test_equal_references_still_coalesce(self):
        first = dynamic(path=PATH)
        findings, _ = dynamic_findings([first, dict(first), dict(first)])
        self.assertEqual([item.finding_id for item in findings], [base_id()])

    def test_ids_do_not_depend_on_input_order(self):
        values = at_one_path(4)
        forward, _ = dynamic_findings(values)
        backward, _ = dynamic_findings(list(reversed(values)))
        self.assertEqual(
            sorted((item.finding_id, item.evidence_references) for item in forward),
            sorted((item.finding_id, item.evidence_references) for item in backward),
        )

    def test_other_paths_and_sources_are_unchanged(self):
        values = at_one_path(2) + [
            dynamic(path="$.action[1]"), dynamic(source_id="auto-2", path=PATH),
        ]
        ids = {item.finding_id for item in dynamic_findings(values)[0]}
        self.assertIn(base_id(path="$.action[1]"), ids)
        self.assertIn(base_id(source_id="auto-2"), ids)
        self.assertEqual(len(ids), 4)

    async def test_compact_and_standard_rows_are_unique(self):
        first = dynamic(path=PATH)
        # Two that differ, plus an exact duplicate of the first (the HA Admin
        # shape: three references, two findings).
        values = [first, dict(first, excerpt="{{ states(other) }}"), dict(first)]
        for detail in ("standard", "compact"):
            with self.subTest(detail=detail):
                data = await analyze(values, detail_level=detail)
                rows = [item for item in data["findings"]
                        if item["finding_type"] == "unresolved_dynamic_reference"]
                self.assertEqual(len(rows), 2)
                self.assertEqual(len({row["finding_id"] for row in rows}), 2)
                self.assertEqual(len({json.dumps(row, sort_keys=True) for row in rows}), 2)

    async def test_a_compact_page_of_many_references_has_no_repeated_id(self):
        data = await analyze(at_one_path(40), detail_level="compact", limit=100)
        ids = [row["finding_id"] for row in data["findings"]]
        self.assertEqual(data["finding_count"], 40)
        self.assertEqual(len(ids), 40)
        self.assertEqual(len(set(ids)), 40)


class DynamicCountSemanticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_count_is_unfiltered_and_says_so(self):
        first = dynamic(path=PATH)
        values = [first, dict(first, excerpt="{{ states(other) }}"), dict(first)]
        data = await analyze(values, detail_level="compact", severities=["high"])
        self.assertEqual(data["finding_count"], 0)
        # Three references as read: before the filter and before the exact
        # duplicate coalesces.
        self.assertEqual(data["unresolved_dynamic_reference_count"], 3)
        summary = data["dynamic_reference_summary"]
        self.assertEqual(summary["unresolved_in_requested_scope_count"], 3)
        self.assertEqual(summary["reported_finding_count"], 0)
        self.assertEqual(data["counter_semantics"]["unresolved_dynamic_reference_count"], SEMANTICS)

    async def test_the_semantics_entry_is_present_without_a_filter(self):
        first = dynamic(path=PATH)
        data = await analyze([first, dict(first)], detail_level="standard")
        self.assertNotIn("severity_filter", data)
        self.assertEqual(data["unresolved_dynamic_reference_count"], 2)
        self.assertEqual(data["dynamic_reference_summary"]["reported_finding_count"], 1)
        self.assertEqual(data["counter_semantics"]["unresolved_dynamic_reference_count"], SEMANTICS)

    def test_the_guide_documents_the_unfiltered_count(self):
        text = (ROOT / "docs" / "CONFIGURATION_INTEGRITY_ANALYSIS.md").read_text(encoding="utf-8")
        self.assertIn(SEMANTICS, text)
        self.assertIn("HAMCP-491", text)
        self.assertIn("HAMCP-490", text)


if __name__ == "__main__":
    unittest.main()
