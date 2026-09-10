"""RC5-OBS-1: visible findings coalesce only identical evidence."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from tests import test_configuration_integrity_analysis as fixtures
from ha_mcp_engineering.integrity.rules import classify_integrity


class IntegrityCoalescingTests(unittest.IsolatedAsyncioTestCase):
    def classify(self, entries):
        return classify_integrity(fixtures.bundle(dynamics=entries),
                                  finding_types=["unresolved_dynamic_reference"],
                                  include_orphan_candidates=False)

    def test_identical_entries_coalesce(self):
        entry = fixtures.dynamic()
        findings, evidence, _ = self.classify([deepcopy(entry) for _ in range(8)])
        self.assertEqual(len(findings), 1)
        self.assertEqual(len(evidence), 1)
        self.assertTrue(findings[0].manual_review_required)

    def test_distinct_sources_paths_and_evidence_are_retained(self):
        base = fixtures.dynamic()
        variants = [
            base,
            fixtures.dynamic(source_id="other"),
            fixtures.dynamic(path="$.action[1]"),
            {**base, "warning": "Different meaningful warning"},
            {**base, "excerpt": "{{ different_expression }}"},
        ]
        findings, evidence, _ = self.classify(variants + [deepcopy(base)])
        self.assertEqual(len(findings), 5)
        refs = [evidence[f.evidence_references[0]] for f in findings]
        self.assertEqual(len(set(refs)), 5)
        self.assertTrue(any(r.summary == "Different meaningful warning" for r in refs))
        self.assertTrue(any(r.excerpt == "{{ different_expression }}" for r in refs))
        reversed_findings, reversed_evidence, _ = self.classify(list(reversed(variants)))
        self.assertEqual(findings, reversed_findings)
        self.assertEqual(evidence, reversed_evidence)

    async def test_totals_truncation_and_page_continuation_use_coalesced_findings(self):
        unique = [fixtures.dynamic(source_id=f"source-{i}") for i in range(5)]
        entries = [deepcopy(e) for e in unique for _ in range(9)]
        provider = fixtures.FakeProvider(fixtures.bundle(dynamics=entries,
                                                       unsupported=("dashboard",)))
        service = fixtures.ConfigurationIntegrityAnalysisService(provider)
        args = dict(finding_types=["unresolved_dynamic_reference"],
                    include_orphan_candidates=False, detail_level="evidence", limit=2)
        # Duplicates must disappear before the analysis cap, not merely per page.
        with patch("ha_mcp_engineering.integrity.service.MAX_ANALYSIS_FINDINGS", 5):
            page = await service.analyze(**args)
        pages = [page]
        while cursor := page.data["pagination"]["next_cursor"]:
            page = await service.analyze(**args, cursor=cursor)
            pages.append(page)
            self.assertLessEqual(len(pages), 3)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual([len(p.data["findings"]) for p in pages], [2, 2, 1])
        self.assertEqual({p.data["finding_count"] for p in pages}, {5})
        self.assertEqual({p.data["unresolved_dynamic_reference_count"] for p in pages}, {45})
        self.assertTrue(all(p.data["manual_review_required"] for p in pages))
        self.assertTrue(all(p.partial for p in pages))  # retained coverage gap
        self.assertEqual(len({f["source_id"] for p in pages for f in p.data["findings"]}), 5)
        self.assertEqual(pages[0].data["dynamic_reference_summary"]["unresolved_in_requested_scope_count"], 45)
        self.assertEqual(pages[0].data["dynamic_reference_summary"]["reported_finding_count"], 5)
