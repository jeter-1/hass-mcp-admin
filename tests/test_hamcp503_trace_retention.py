"""HAMCP-503 TB-1: reliability trace coverage accounts for Core's trace rings.

Core 2026.11.3 keeps the newest ``trace.stored_traces`` run traces, and
separately that many not-triggered traces, per automation (default 5,
``trace/const.py:19``, ``trace/util.py:79-96``), evicting the oldest first.
A full ring whose oldest trace starts after the lookback cutoff may have
dropped runs inside the lookback, so the trace source must not report
complete coverage.
"""

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.reliability.provider import (  # noqa: E402
    CORE_DEFAULT_STORED_TRACES, DirectHaReliabilityProvider, _stored_traces_cap,
)
from ha_mcp_engineering.reliability.service import AutomationReliabilityAnalysisService  # noqa: E402
from ha_mcp_engineering.reliability.batch import _coverage_limitation_reasons  # noqa: E402


UTC = timezone.utc
ANALYSIS_INSTANT = datetime(2026, 7, 13, 3, 0, tzinfo=UTC)
CUTOFF = ANALYSIS_INSTANT - timedelta(hours=168)
AUTOMATION_ID = "1774197661044"
ENTITY_ID = "automation.retention_test"


def header(run_id, started, *, not_triggered=False):
    value = {
        "run_id": run_id,
        "timestamp": {"start": started.isoformat(), "finish": started.isoformat()},
        "state": "stopped",
        "last_step": "action/0",
    }
    if not_triggered:
        value["not_triggered"] = True
    return value


def runs(count, *, newest=ANALYSIS_INSTANT - timedelta(hours=1), step=timedelta(hours=12),
         prefix="run", not_triggered=False):
    return [
        header(f"{prefix}-{index}", newest - step * index, not_triggered=not_triggered)
        for index in range(count)
    ]


def configuration(**trace):
    value = {
        "alias": "Retention Test",
        "mode": "single",
        "trigger": [{"platform": "state", "entity_id": "sensor.retention"}],
        "action": [{"service": "light.turn_on", "target": {"entity_id": "light.retention"}}],
    }
    if trace:
        value["trace"] = trace["trace"]
    return value


class Rest:
    def __init__(self, config):
        self.config = config
        self.calls = []

    async def request(self, method, path):
        self.calls.append((method, path))
        if path == f"/config/automation/config/{AUTOMATION_ID}":
            return copy.deepcopy(self.config)
        if path == f"/states/{ENTITY_ID}":
            return {"entity_id": ENTITY_ID, "state": "on",
                    "attributes": {"id": AUTOMATION_ID, "friendly_name": "Retention Test"}}
        if path in {"/states/sensor.retention", "/states/light.retention"}:
            return {"entity_id": path.removeprefix("/states/"), "state": "on", "attributes": {}}
        raise AssertionError(f"unexpected REST call: {method} {path}")


class WebSocket:
    def __init__(self, listing):
        self.listing = listing
        self.calls = []

    async def command(self, payload):
        self.calls.append(copy.deepcopy(payload))
        if payload["type"] == "config/entity_registry/list":
            return [{"entity_id": ENTITY_ID, "unique_id": AUTOMATION_ID, "disabled_by": None}]
        if payload["type"] == "trace/list":
            return copy.deepcopy(self.listing)
        if payload["type"] == "trace/get":
            return {"trace": {"action/0": [{"result": {"result": True}}]}}
        if payload["type"] == "system_log/list":
            return []
        raise AssertionError(payload)


def core_ring(listing, cap):
    """What Core 2026.11.3 returns: the newest ``cap`` traces of each ring."""

    def newest(items):
        return sorted(items, key=lambda item: item["timestamp"]["start"])[-cap:] if cap else []

    return (newest([item for item in listing if not item.get("not_triggered")])
            + newest([item for item in listing if item.get("not_triggered")]))


class CollectTraces(unittest.IsolatedAsyncioTestCase):
    async def collect(self, listing, *, stored_traces=CORE_DEFAULT_STORED_TRACES, limit=10):
        provider = DirectHaReliabilityProvider(object(), WebSocket(listing))
        return await provider._collect_traces(
            AUTOMATION_ID, 168, limit, ANALYSIS_INSTANT, stored_traces=stored_traces
        )


class EvictionRegressionTests(CollectTraces):
    async def test_six_runs_inside_lookback_core_keeps_five_is_partial(self):
        executed = runs(6)
        listing = core_ring(executed, CORE_DEFAULT_STORED_TRACES)
        self.assertEqual(len(listing), 5)
        traces, coverage = await self.collect(listing)
        self.assertEqual(len(traces), 5)
        self.assertEqual(coverage.completeness, "partial")
        self.assertEqual(coverage.collection_state, "retention_limited")
        self.assertEqual(coverage.retention_coverage, "partial")
        self.assertFalse(coverage.trustworthy_empty)
        self.assertEqual(coverage.counts["stored_traces_cap"], 5)
        self.assertEqual(coverage.counts["runs_ring_returned"], 5)
        self.assertEqual(coverage.counts["not_triggered_ring_returned"], 0)
        self.assertEqual(coverage.counts["rings_full_inside_lookback"], 1)
        self.assertTrue(any("trace.stored_traces" in item for item in coverage.warnings))
        self.assertLessEqual(len(coverage.public()["counts"]), 20)

    async def test_analysis_of_evicted_lookback_is_partial_not_success(self):
        listing = core_ring(runs(6), CORE_DEFAULT_STORED_TRACES)
        rest, websocket = Rest(configuration()), WebSocket(listing)
        service = AutomationReliabilityAnalysisService(
            DirectHaReliabilityProvider(rest, websocket), clock=lambda: ANALYSIS_INSTANT
        )
        output = await service.analyze(automation_id=AUTOMATION_ID)
        self.assertTrue(output.partial)
        self.assertEqual(output.data["result_status"], "partial")
        trace_source = next(
            item for item in output.data["evidence_source_coverage"]
            if item["source_type"] == "automation_traces"
        )
        self.assertEqual(trace_source["collection_state"], "retention_limited")
        self.assertEqual(trace_source["retention_coverage"], "partial")
        self.assertTrue(all(method == "GET" for method, _path in rest.calls))
        self.assertEqual(
            sum(path == f"/config/automation/config/{AUTOMATION_ID}" for _m, path in rest.calls), 1
        )
        self.assertFalse(any(
            payload["type"].startswith(("call_service", "automation/trigger"))
            for payload in websocket.calls
        ))

    async def test_batch_names_the_retention_reasons(self):
        _traces, limited = await self.collect(core_ring(runs(6), CORE_DEFAULT_STORED_TRACES))
        self.assertIn("retention_partial", _coverage_limitation_reasons(limited.public()))
        _traces, unknown = await self.collect(runs(2), stored_traces=None)
        self.assertIn("retention_unknown", _coverage_limitation_reasons(unknown.public()))


class RingBoundaryTests(CollectTraces):
    async def test_ring_below_cap_stays_complete(self):
        _traces, coverage = await self.collect(runs(4))
        self.assertEqual(coverage.completeness, "complete")
        self.assertEqual(coverage.collection_state, "complete")
        self.assertIsNone(coverage.retention_coverage)
        self.assertEqual(coverage.counts["rings_full_inside_lookback"], 0)

    async def test_full_ring_reaching_the_cutoff_stays_complete(self):
        for oldest in (CUTOFF, CUTOFF - timedelta(microseconds=1), CUTOFF - timedelta(days=3)):
            with self.subTest(oldest=oldest):
                listing = runs(4) + [header("oldest", oldest)]
                _traces, coverage = await self.collect(listing)
                self.assertEqual(coverage.completeness, "complete")
                self.assertIsNone(coverage.retention_coverage)

    async def test_full_ring_oldest_just_after_cutoff_is_partial(self):
        listing = runs(4) + [header("oldest", CUTOFF + timedelta(microseconds=1))]
        _traces, coverage = await self.collect(listing)
        self.assertEqual(coverage.collection_state, "retention_limited")

    async def test_rings_are_counted_separately(self):
        # Eight traces, but each ring holds four, below the cap of five.
        listing = runs(4) + runs(4, prefix="nt", not_triggered=True)
        _traces, coverage = await self.collect(listing)
        self.assertEqual(coverage.completeness, "complete")
        self.assertEqual(coverage.counts["runs_ring_returned"], 4)
        self.assertEqual(coverage.counts["not_triggered_ring_returned"], 4)

    async def test_full_not_triggered_ring_is_partial(self):
        listing = runs(2) + runs(5, prefix="nt", not_triggered=True)
        _traces, coverage = await self.collect(listing)
        self.assertEqual(coverage.collection_state, "retention_limited")
        self.assertEqual(coverage.counts["rings_full_inside_lookback"], 1)

    async def test_configured_cap_moves_the_boundary(self):
        for cap, count, state in ((10, 6, "complete"), (10, 10, "retention_limited"),
                                  (2, 2, "retention_limited"), (2, 1, "complete")):
            with self.subTest(cap=cap, count=count):
                _traces, coverage = await self.collect(runs(count), stored_traces=cap)
                self.assertEqual(coverage.collection_state, state)
                self.assertEqual(coverage.counts["stored_traces_cap"], cap)

    async def test_existing_loss_states_keep_precedence(self):
        _traces, coverage = await self.collect(runs(6), limit=2)
        self.assertEqual(coverage.collection_state, "truncated")
        self.assertEqual(coverage.retention_coverage, "partial")
        malformed = runs(5) + [{"run_id": "bad", "timestamp": {"start": "bad"}}]
        _traces, coverage = await self.collect(malformed)
        self.assertEqual(coverage.collection_state, "malformed_entries")
        self.assertEqual(coverage.completeness, "partial")


class DisabledAndUnknownRetentionTests(CollectTraces):
    async def test_cap_zero_empty_list_is_not_trustworthy_empty(self):
        traces, coverage = await self.collect([], stored_traces=0)
        self.assertEqual(traces, [])
        self.assertEqual(coverage.completeness, "partial")
        self.assertEqual(coverage.collection_state, "retention_disabled")
        self.assertEqual(coverage.retention_coverage, "partial")
        self.assertFalse(coverage.trustworthy_empty)
        self.assertEqual(coverage.counts["stored_traces_cap"], 0)

    async def test_cap_zero_analysis_reports_a_gap_not_no_execution(self):
        rest = Rest(configuration(trace={"stored_traces": 0}))
        service = AutomationReliabilityAnalysisService(
            DirectHaReliabilityProvider(rest, WebSocket([])), clock=lambda: ANALYSIS_INSTANT
        )
        output = await service.analyze(automation_id=AUTOMATION_ID)
        rules = {item["rule_id"] for item in output.data["findings"]}
        self.assertIn("trace_evidence_unavailable", rules)
        self.assertNotIn("no_recent_execution_evidence", rules)
        self.assertEqual(output.data["result_status"], "partial")

    async def test_default_cap_empty_list_stays_trustworthy_empty(self):
        traces, coverage = await self.collect([])
        self.assertEqual(traces, [])
        self.assertEqual(coverage.completeness, "complete")
        self.assertEqual(coverage.collection_state, "zero_upstream")
        self.assertTrue(coverage.trustworthy_empty)

    async def test_unknown_cap_is_partial_with_unknown_retention(self):
        for listing in ([], runs(1)):
            with self.subTest(count=len(listing)):
                _traces, coverage = await self.collect(listing, stored_traces=None)
                self.assertEqual(coverage.completeness, "partial")
                self.assertEqual(coverage.collection_state, "retention_unknown")
                self.assertEqual(coverage.retention_coverage, "unknown")
                self.assertFalse(coverage.trustworthy_empty)
                self.assertNotIn("stored_traces_cap", coverage.counts)


class StoredTracesCapTests(unittest.IsolatedAsyncioTestCase):
    def test_cap_follows_core_validation(self):
        cases = (
            ({}, 5),
            ({"trace": {}}, 5),
            ({"trace": {"stored_traces": 0}}, 0),
            ({"trace": {"stored_traces": 12}}, 12),
            ({"trace": {"stored_traces": "10"}}, 10),
            ({"trace": {"stored_traces": 7.9}}, 7),
            ({"trace": {"stored_traces": True}}, 1),
        )
        for config, expected in cases:
            with self.subTest(config=config):
                self.assertEqual(_stored_traces_cap(config), expected)

    def test_unreadable_cap_is_none(self):
        for config in (
            None, [], {"trace": None}, {"trace": []}, {"trace": "5"},
            {"trace": {"stored_traces": None}}, {"trace": {"stored_traces": -1}},
            {"trace": {"stored_traces": "-1"}}, {"trace": {"stored_traces": "1.5"}},
            {"trace": {"stored_traces": "abc"}}, {"trace": {"stored_traces": [5]}},
            {"trace": {"stored_traces": {"value": 5}}},
            {"trace": {"stored_traces": float("inf")}},
            {"trace": {"stored_traces": float("nan")}},
        ):
            with self.subTest(config=config):
                self.assertIsNone(_stored_traces_cap(config))

    async def test_collect_reads_the_cap_from_the_fetched_configuration(self):
        for trace, count, state, cap in (
            ({"stored_traces": 10}, 6, "complete", 10),
            ({"stored_traces": "10"}, 6, "complete", 10),
            ({"stored_traces": 2}, 2, "retention_limited", 2),
            ({"stored_traces": "x"}, 1, "retention_unknown", None),
        ):
            with self.subTest(trace=trace):
                rest = Rest(configuration(trace=trace))
                provider = DirectHaReliabilityProvider(rest, WebSocket(runs(count)))
                bundle = await provider.collect(
                    automation_id=AUTOMATION_ID, lookback_hours=168, trace_limit=10,
                    analysis_instant=ANALYSIS_INSTANT,
                )
                coverage = next(
                    item for item in bundle.coverage if item.source_type == "automation_traces"
                )
                self.assertEqual(coverage.collection_state, state)
                self.assertEqual(coverage.counts.get("stored_traces_cap"), cap)
                self.assertEqual(
                    sum(path == f"/config/automation/config/{AUTOMATION_ID}"
                        for _method, path in rest.calls), 1
                )


if __name__ == "__main__":
    unittest.main()
