"""HAMCP-495: a batch item partial through lifecycle coverage says why.

The single-item analysis is partial when its lifecycle inspection is
incomplete: a structure it could not resolve (a gap category) or an
inspection bound it reached (a limiting reason). The batch digest carried
only ``lifecycle.coverage_complete=false``, and ``source_limitations`` lists
evidence sources, not the lifecycle inspection, so such an item was partial
with no stated reason. The digest now names the gap categories and limiting
reasons, and a warning points to them. Offline, synthetic fixtures only.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT / "tests"))

from ha_mcp_engineering.reliability import batch as batch_module  # noqa: E402
from ha_mcp_engineering.reliability.batch import ReliabilityBatchAnalyzer  # noqa: E402
from ha_mcp_engineering.reliability.runtime import RELIABILITY_ANALYSIS  # noqa: E402
from ha_mcp_engineering.tools.analysis import automation_reliability_analysis  # noqa: E402

from test_automation_reliability_analysis import bundle  # noqa: E402
from test_hamcp229_reliability_batch import PerIdProvider, ids, service_for  # noqa: E402

COMPLETE = {"trigger": [], "action": [{"delay": 5}]}
CALLED_SCRIPT = {"trigger": [], "action": [{"action": "script.synthetic"}]}
UNRESOLVED = {"trigger": [], "action": [
    {"choose": "synthetic"},
    {"condition": "template", "value_template": "{{ synthetic }}"},
]}
LONG_SCALAR = {"trigger": [], "action": [{"delay": "x" * 2500}]}


def analyze(outcomes, automation_ids, **arguments):
    provider = PerIdProvider({key: bundle(config=value) for key, value in outcomes.items()})
    return asyncio.run(ReliabilityBatchAnalyzer().analyze(
        service_for(provider), automation_ids=automation_ids, **arguments,
    ))


class LifecycleReasonTests(unittest.TestCase):
    def items(self, output):
        return {row["automation_id"]: row for row in output.data["items"]}

    def test_a_lifecycle_partial_item_names_its_gap_categories(self):
        output = analyze({"script": CALLED_SCRIPT, "unresolved": UNRESOLVED}, ["script", "unresolved"])
        items = self.items(output)
        self.assertEqual(items["script"]["result_status"], "partial")
        self.assertEqual(items["script"]["lifecycle"], {
            "hazards_detected": False, "coverage_complete": False,
            "limiting_reasons": [], "gap_categories": ["called_script_not_inspected"],
        })
        self.assertEqual(items["unresolved"]["lifecycle"]["gap_categories"],
                         ["condition_unresolved", "malformed_choose"])
        # No evidence source is limited, so source_limitations cannot explain it.
        self.assertEqual(output.data["source_limitations"], [])
        self.assertIn(batch_module.LIFECYCLE_COVERAGE_WARNING, output.warnings)
        self.assertEqual(output.data["batch_result_status"], "partial")

    def test_a_reached_inspection_bound_is_a_limiting_reason(self):
        items = self.items(analyze({"long": LONG_SCALAR}, ["long"]))
        self.assertEqual(items["long"]["result_status"], "partial")
        self.assertEqual(items["long"]["lifecycle"]["limiting_reasons"], ["scalar_length"])
        self.assertEqual(items["long"]["lifecycle"]["gap_categories"], [])

    def test_a_complete_item_names_nothing_and_adds_no_warning(self):
        output = analyze({"done": COMPLETE}, ["done"])
        lifecycle = self.items(output)["done"]["lifecycle"]
        self.assertTrue(lifecycle["coverage_complete"])
        self.assertEqual((lifecycle["limiting_reasons"], lifecycle["gap_categories"]), ([], []))
        self.assertEqual(output.data["batch_result_status"], "success")
        self.assertNotIn(batch_module.LIFECYCLE_COVERAGE_WARNING, output.warnings)

    def test_the_names_match_the_single_tool(self):
        provider = PerIdProvider(default=bundle(config=UNRESOLVED))
        with patch.object(RELIABILITY_ANALYSIS, "service", service_for(provider)):
            single = json.loads(asyncio.run(automation_reliability_analysis(
                automation_id="parity", lookback_hours=24, trace_limit=5, detail_level="summary",
            )))
            batch = asyncio.run(ReliabilityBatchAnalyzer().analyze(
                service_for(provider), automation_ids=["parity"], lookback_hours=24, trace_limit=5,
            ))
        lifecycle = single["data"]["lifecycle_analysis"]
        digest = batch.data["items"][0]["lifecycle"]
        self.assertEqual(digest["gap_categories"], sorted(lifecycle["coverage"]["gap_counts"]))
        self.assertEqual(digest["limiting_reasons"], lifecycle["limiting_reasons"])

    def test_the_reduced_shape_keeps_the_reason(self):
        digest = self.items(analyze({"script": CALLED_SCRIPT}, ["script"]))["script"]
        self.assertEqual(batch_module._reduced_digest(digest)["lifecycle"], digest["lifecycle"])

    def test_names_are_bounded_strings(self):
        names = {f"synthetic_{index:02d}": 1 for index in range(40)}
        names.update({"": 1, "y" * 65: 1})
        bounded = batch_module._lifecycle_names(names)
        self.assertEqual(len(bounded), batch_module._MAX_LIFECYCLE_NAMES)
        self.assertEqual(bounded, sorted(bounded))
        self.assertTrue(all(0 < len(name) <= batch_module._MAX_LIFECYCLE_NAME_CHARS for name in bounded))
        self.assertEqual(batch_module._lifecycle_names(["b", "a", "b", 3, None]), ["a", "b"])
        # Within the count bound, an empty or over-long name is still dropped.
        limit = batch_module._MAX_LIFECYCLE_NAME_CHARS
        self.assertEqual(
            batch_module._lifecycle_names({"a" * (limit + 1): 1, "": 1, "scalar_length": 1}),
            ["scalar_length"],
        )
        self.assertEqual(batch_module._lifecycle_names(["a" * limit]), ["a" * limit])
        for value in (None, "scalar_length", 5, {"x"}):
            with self.subTest(value=value):
                self.assertEqual(batch_module._lifecycle_names(value), [])

    def test_the_warning_is_charged_at_the_exact_bound(self):
        automation_ids = ids(3)
        outcomes = dict.fromkeys(automation_ids, CALLED_SCRIPT)
        full = analyze(outcomes, automation_ids)
        self.assertIn(batch_module.LIFECYCLE_COVERAGE_WARNING, full.warnings)
        digests = full.data["items"]
        fixed = (
            batch_module.ENVELOPE_RESERVE_CHARS
            + sum(batch_module._fixed_item_cost(item) for item in automation_ids)
            + batch_module._rendered_cost({"source_limitations": [], "root_cause_detail": "reduced"}, 1)
            + batch_module._rendered_cost(batch_module.LIFECYCLE_COVERAGE_WARNING, 2)
        )
        compact = sum(len(batch_module._compact(item)) for item in digests)
        at_bound = analyze(outcomes, automation_ids, response_limit=fixed + compact)
        self.assertEqual([row["status"] for row in at_bound.data["items"]], ["analyzed"] * 3)
        below = analyze(outcomes, automation_ids, response_limit=fixed + compact - 1)
        self.assertEqual(
            [(row["status"], row["reason"]) for row in below.data["items"]],
            [("analyzed", None)] * 2 + [("skipped_budget", "output_budget")],
        )


if __name__ == "__main__":
    unittest.main()
