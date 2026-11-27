"""HAMCP-492: a compact integrity page holds at most 24000 characters.

The compact budget binds only for detail_level compact and only when it is
below the response limit; otherwise the response limit binds as before. All
values are synthetic (the HAMCP-473 fixture).
"""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.integrity.service import (
    COMPACT_PAGE_BUDGET_CHARS,
    OVER_COMPACT_PAGE_BUDGET,
)
from tests import test_configuration_integrity_analysis as fixtures
from tests.test_hamcp473_integrity_paging import make_service, read

TOTAL = 900 + 200 + 82 + 40 + 60


class CompactBudgetTests(unittest.IsolatedAsyncioTestCase):
    def test_the_budget_is_24000(self):
        self.assertEqual(COMPACT_PAGE_BUDGET_CHARS, 24_000)

    async def test_a_response_limit_at_or_below_the_budget_binds_instead(self):
        for budget in (20_000, COMPACT_PAGE_BUDGET_CHARS):
            with self.subTest(budget=budget):
                service, _ = make_service()
                raw, response = await read(service, budget=budget)
                self.assertTrue(response["success"], response.get("error_code"))
                self.assertLessEqual(len(raw.encode("utf-8")), budget)
                pagination = response["data"]["pagination"]
                self.assertEqual(pagination["clamp_reason"], "response_size_limit")
                self.assertTrue(pagination["has_more"])

    async def test_a_response_limit_just_above_the_budget_is_bound_by_the_budget(self):
        service, _ = make_service()
        raw, response = await read(service, budget=COMPACT_PAGE_BUDGET_CHARS + 1)
        self.assertLessEqual(len(raw.encode("utf-8")), COMPACT_PAGE_BUDGET_CHARS)
        self.assertEqual(response["data"]["pagination"]["clamp_reason"], "compact_page_budget")

    async def test_other_detail_levels_keep_the_response_limit(self):
        for detail_level in ("summary", "standard", "evidence"):
            with self.subTest(detail_level=detail_level):
                service, _ = make_service()
                raw, response = await read(service, detail_level=detail_level, limit=100)
                self.assertTrue(response["success"], response.get("error_code"))
                self.assertLessEqual(len(raw.encode("utf-8")), 60_000)
                self.assertNotEqual(response["data"]["pagination"]["clamp_reason"], "compact_page_budget")

    async def test_limit_one_gives_whole_analysis_counts_and_coverage(self):
        service, _ = make_service()
        _, full = await read(service)
        service, _ = make_service()
        raw, response = await read(service, limit=1)
        self.assertLess(len(raw.encode("utf-8")), COMPACT_PAGE_BUDGET_CHARS)
        data = response["data"]
        self.assertEqual(len(data["findings"]), 1)
        self.assertEqual(data["finding_count"], TOTAL)
        self.assertEqual(data["findings_by_severity"], {"high": 900, "medium": 260, "low": 82, "info": 40})
        self.assertEqual(data["pagination"]["total"], TOTAL)
        self.assertIsNone(data["pagination"]["clamp_reason"])
        for key in ("finding_count", "findings_by_severity", "source_coverage_matrix",
                    "dynamic_reference_summary"):
            self.assertEqual(data[key], full["data"][key], key)

    async def test_the_internal_verdict_is_falsy_and_never_public(self):
        self.assertFalse(OVER_COMPACT_PAGE_BUDGET)
        self.assertEqual(repr(OVER_COMPACT_PAGE_BUDGET), "OVER_COMPACT_PAGE_BUDGET")
        service, _ = make_service()
        raw, _ = await read(service)
        self.assertNotIn("OVER_COMPACT", raw)

    async def test_the_reason_names_the_bound_of_the_smallest_failing_probe(self):
        cases = {
            "budget only": (lambda size: OVER_COMPACT_PAGE_BUDGET if size > 18_000 else True,
                            "compact_page_budget"),
            "limit only": (lambda size: size <= 18_000, "response_size_limit"),
            # Large pages fail the limit, mid-sized ones the budget: the
            # smallest failure decides.
            "both": (lambda size: (size <= 30_000 and OVER_COMPACT_PAGE_BUDGET) if size > 18_000 else True,
                     "compact_page_budget"),
        }
        for name, (verdict, reason) in cases.items():
            with self.subTest(case=name):
                service, _ = make_service()

                def page_fits(output, verdict=verdict):
                    return verdict(len(json.dumps(output.data, separators=(",", ":"))))

                output = await service.analyze(
                    source_types=list(fixtures.SOURCE_TYPES), include_orphan_candidates=False,
                    detail_level="compact", limit=100, page_fits=page_fits,
                )
                pagination = output.data["pagination"]
                self.assertEqual(pagination["clamp_reason"], reason)
                self.assertGreater(pagination["returned"], 1)
                self.assertLess(pagination["returned"], 100)


if __name__ == "__main__":
    unittest.main()
