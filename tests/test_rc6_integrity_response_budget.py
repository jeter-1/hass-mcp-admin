"""Same-snapshot findings remain retrievable at the public response boundary."""
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
    ConfigurationIntegrityAnalysisService,
    MAX_PAGINATION_SNAPSHOTS,
    PAGINATION_SNAPSHOT_TTL_SECONDS,
)
from tests import test_configuration_integrity_analysis as fixtures


class IntegrityResponseBudgetTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self, count=31, unicode=False):
        refs = [fixtures.reference(
            f"sensor.synthetic_missing_{i}",
            source_id=f"synthetic_{i}_" + "s" * 75,
            path="$.condition" + ".synthetic_nested" * 24 + f"[{i}].entity_id",
        ) for i in range(count)]
        for ref in refs:
            ref["source_name"] = "Synthetic source " + ("雪😀" if unicode else "n") * 110
            ref["evidence_summary"] = "Synthetic evidence " + "e" * 220
            ref["excerpt"] = "Synthetic excerpt " + "x" * 220
        provider = fixtures.FakeProvider(fixtures.bundle(references=refs))
        service = ConfigurationIntegrityAnalysisService(
            provider, clock=lambda: fixtures.ANALYSIS_TIME,
            cursor_key=b"synthetic-response-budget-key-00",
        )
        return service, provider

    async def read(self, service, *, limit, cursor="", budget=60000):
        with patch.object(analysis, "CONFIGURATION_INTEGRITY_ANALYSIS", SimpleNamespace(require=lambda: service)), patch.object(analysis, "SETTINGS", SimpleNamespace(response_size_limit=budget)):
            raw = await analysis.configuration_integrity_analysis(
                source_types=list(fixtures.SOURCE_TYPES),
                finding_types=["missing_entity_reference"],
                include_orphan_candidates=False, detail_level="standard",
                refresh_index=False, limit=limit, cursor=cursor,
            )
        self.assertLessEqual(len(raw), budget)
        self.assertLessEqual(len(raw.encode("utf-8")), budget)
        return json.loads(raw)

    async def collect(self, *, count=31, first_limit=1, unicode=False):
        service, provider = self.make_service(count, unicode)
        original = deepcopy(provider.value)
        cursor, limit, findings, identities = "", first_limit, [], set()
        for _ in range(100):
            response = await self.read(service, limit=limit, cursor=cursor)
            self.assertTrue(response["success"], response.get("error_code"))
            if response.get("response_completeness", {}).get("truncated"):
                # Accept a recoverable receipt, not only adaptive complete pages.
                # Retrying a read must retain the same snapshot and offset.
                hints = response["response_completeness"].get("retrieval", [])
                for hint in hints:
                    if hint.get("tool") == "configuration_integrity_analysis":
                        cursor = hint.get("arguments", {}).get("cursor", cursor)
                self.assertTrue(cursor, "Omitted first-page findings need a recoverable cursor")
                self.assertGreater(limit, 1, "A minimum page must make progress")
                limit = max(1, limit // 2)
                continue
            data = response["data"]
            index = data["index_and_cache_provenance"]
            identities.add((data["analysis_timestamp"], index["index_generation"], index["index_fingerprint"]))
            self.assertEqual(data["finding_count"], count)
            self.assertEqual(data["pagination"]["total"], count)
            self.assertEqual(data["pagination"]["returned"], len(data["findings"]))
            findings.extend(data["findings"])
            cursor = data["pagination"]["next_cursor"]
            if cursor is None:
                self.assertFalse(data["pagination"]["has_more"])
                break
            self.assertTrue(data["pagination"]["has_more"])
            limit = 30
        else:
            self.fail("Pagination failed to terminate")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(identities), 1)
        self.assertEqual(len(findings), count)
        self.assertEqual(len({f["finding_id"] for f in findings}), count)
        self.assertEqual({f["target_entity_id"] for f in findings}, {f"sensor.synthetic_missing_{i}" for i in range(count)})
        self.assertEqual(provider.value, original)
        self.assertEqual(len(service.pagination_snapshots._values), 0)

    async def test_all_31_findings_after_oversized_final_page(self):
        await self.collect()

    async def test_oversized_first_and_intermediate_pages(self):
        await self.collect(count=61, first_limit=30)

    async def test_unicode_pages_obey_character_and_utf8_bounds(self):
        await self.collect(unicode=True, first_limit=30)

    async def test_first_page_that_would_otherwise_be_final(self):
        await self.collect(count=30, first_limit=30)

    async def test_small_complete_page_stays_complete(self):
        service, provider = self.make_service(1)
        result = await self.read(service, limit=1)
        self.assertTrue(result["success"])
        self.assertNotIn("response_completeness", result)
        self.assertEqual(result["data"]["pagination"]["returned"], 1)
        self.assertIsNone(result["data"]["pagination"]["next_cursor"])
        self.assertEqual(len(provider.calls), 1)

    async def test_adjusted_cursor_still_refuses_drift_tampering_and_expiry(self):
        for failure in ("drift", "tamper", "expiry"):
            with self.subTest(failure=failure):
                service, provider = self.make_service()
                first = await self.read(service, limit=30)
                cursor = first["data"]["pagination"]["next_cursor"]
                if failure == "drift":
                    provider.identity["generation"] += 1
                elif failure == "tamper":
                    cursor = "A" + cursor[1:]
                else:
                    for snapshot in service.pagination_snapshots._values.values():
                        snapshot.expires_at = 0
                result = await self.read(service, limit=10, cursor=cursor)
                self.assertFalse(result["success"])
                self.assertIn(result["error_code"], {"stale_cursor", "invalid_cursor"})
                self.assertEqual(len(provider.calls), 1)

    async def test_unfit_single_page_refuses_without_retiring_snapshot(self):
        service, provider = self.make_service()
        first = await self.read(service, limit=1)
        cursor = first["data"]["pagination"]["next_cursor"]
        result = await self.read(service, limit=30, cursor=cursor, budget=1024)
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "analysis_unavailable")
        self.assertEqual(len(service.pagination_snapshots._values), 1)
        recovered = await self.read(service, limit=1, cursor=cursor)
        self.assertTrue(recovered["success"])
        self.assertEqual(len(provider.calls), 1)

    def test_cache_bounds_are_unchanged(self):
        self.assertEqual(MAX_PAGINATION_SNAPSHOTS, 16)
        self.assertEqual(PAGINATION_SNAPSHOT_TTL_SECONDS, 300.0)
