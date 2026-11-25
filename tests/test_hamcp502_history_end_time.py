"""HAMCP-502: a history or logbook window over 24 hours returns its full span.

Without ``end_time``, Core 2026.11.3 ends a ``/history/period`` window one day
after its start (``homeassistant/components/history/__init__.py``) and a
``/logbook`` window ``period`` days after it, one day by default
(``homeassistant/components/logbook/rest_api.py``). get_history and the
incident provider sent only a start, so a 168 hour read came back as its first
24 hours while reading complete. The fake below applies those Core defaults:
a read that omits ``end_time`` is truncated, as it was against the house.

The audit batch adds three neighbours: full history reads turn off Core's
significant-changes filter (TB-4), the incident reliability row follows a
partial bundle (TB-2), and a full system log ring is flagged (TB-5).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qsl, unquote


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.incident.provider import (  # noqa: E402
    DirectHaIncidentProvider,
    _reliability_context_coverage,
)
from ha_mcp_engineering.reliability.models import (  # noqa: E402
    ReliabilityEvidenceBundle,
    ReliabilitySourceCoverage,
)
from ha_mcp_engineering.tools import compatibility  # noqa: E402


ANALYSIS_TIME = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
ROW_INTERVAL = timedelta(hours=2)


def _parse(path):
    route, _, query = str(path).partition("?")
    start = datetime.fromisoformat(unquote(route.rsplit("/", 1)[1]))
    return route, start, dict(parse_qsl(query, keep_blank_values=True))


class CoreWindowFake:
    """Synthetic Core REST reads that apply Core 2026.11.3's window defaults."""

    def __init__(self, last, span=timedelta(hours=200)):
        self.calls = []
        self.times = []
        moment = last
        while moment >= last - span:
            self.times.insert(0, moment)
            moment -= ROW_INTERVAL

    def window(self, route, start, query):
        if "end_time" in query:
            return start, datetime.fromisoformat(query["end_time"])
        if route.startswith("/logbook/"):
            return start, start + timedelta(days=int(query.get("period", 1)))
        return start, start + timedelta(days=1)

    async def request(self, method, path, **kwargs):
        self.calls.append((method, str(path)))
        if method != "GET":
            raise AssertionError("a history read attempted a write")
        route, start, query = _parse(path)
        start, end = self.window(route, start, query)
        inside = [moment for moment in self.times if start <= moment <= end]
        if route.startswith("/history/period/"):
            entity_id = query["filter_entity_id"]
            return [[{"entity_id": entity_id, "state": str(index), "last_changed": moment.isoformat()}
                     for index, moment in enumerate(inside)]]
        if route.startswith("/logbook/"):
            return [{"entity_id": query["entity"], "when": moment.isoformat(), "message": "changed"}
                    for moment in inside]
        raise AssertionError(f"unexpected REST path {route}")


class TheFakeAppliesCoreDefaults(unittest.TestCase):
    """The regression tests below mean something only if this holds."""

    def test_a_read_without_end_time_is_cut_to_one_day(self):
        fake = CoreWindowFake(ANALYSIS_TIME)
        start = (ANALYSIS_TIME - timedelta(hours=168)).isoformat()
        history = asyncio.run(fake.request("GET", f"/history/period/{start}?filter_entity_id=sensor.a"))
        logbook = asyncio.run(fake.request("GET", f"/logbook/{start}?entity=sensor.a"))
        for times in (
            [datetime.fromisoformat(row["last_changed"]) for row in history[0]],
            [datetime.fromisoformat(row["when"]) for row in logbook],
        ):
            self.assertEqual(max(times) - min(times), timedelta(hours=24))


class GetHistoryWindow(unittest.IsolatedAsyncioTestCase):
    async def read(self, hours):
        fake = CoreWindowFake(datetime.now(timezone.utc))
        with patch.object(compatibility, "rest", new=AsyncMock(side_effect=fake.request)) as rest:
            payload = json.loads(await compatibility.get_history("sensor.synthetic", hours=hours))
        return payload, fake, rest

    @staticmethod
    def rows(payload):
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        return data[0]

    async def test_windows_over_24_hours_return_the_full_span(self):
        for hours in (25, 48, 168):
            with self.subTest(hours=hours):
                before = datetime.now(timezone.utc)
                payload, fake, _ = await self.read(hours)
                after = datetime.now(timezone.utc)
                (method, path), = fake.calls
                route, start, query = _parse(path)
                end = datetime.fromisoformat(query["end_time"])
                self.assertEqual(route.rsplit("/", 1)[0], "/history/period")
                self.assertEqual(end - start, timedelta(hours=hours))
                self.assertTrue(before <= end <= after)
                self.assertEqual(query["filter_entity_id"], "sensor.synthetic")
                times = [datetime.fromisoformat(row["last_changed"]) for row in self.rows(payload)]
                # Rows every two hours: the full window, not the first 24 hours.
                self.assertLessEqual(min(times) - start, ROW_INTERVAL)
                self.assertLessEqual(end - max(times), ROW_INTERVAL)
                self.assertGreater(max(times) - min(times), timedelta(hours=hours) - 2 * ROW_INTERVAL)

    async def test_the_default_and_short_windows_keep_their_span(self):
        for hours in (1, 24):
            with self.subTest(hours=hours):
                payload, fake, _ = await self.read(hours)
                _route, start, query = _parse(fake.calls[0][1])
                self.assertEqual(datetime.fromisoformat(query["end_time"]) - start, timedelta(hours=hours))
                self.assertTrue(self.rows(payload))

    async def test_minimal_reads_send_significant_only_and_full_reads_turn_it_off(self):
        _payload, fake, _ = await self.read(48)
        query = _parse(fake.calls[0][1])[2]
        self.assertEqual(
            set(query),
            {"end_time", "filter_entity_id", "minimal_response", "no_attributes", "significant_changes_only"},
        )
        self.assertEqual(query["significant_changes_only"], "1")
        fake = CoreWindowFake(datetime.now(timezone.utc))
        with patch.object(compatibility, "rest", new=AsyncMock(side_effect=fake.request)):
            await compatibility.get_history("sensor.synthetic", hours=48, minimal=False)
        query = _parse(fake.calls[0][1])[2]
        self.assertEqual(set(query), {"end_time", "filter_entity_id", "significant_changes_only"})
        self.assertEqual(query["significant_changes_only"], "0")

    async def test_over_168_hours_still_refuses_with_no_request(self):
        payload, fake, rest = await self.read(168.5)
        self.assertIn("exceeds the 168h", json.dumps(payload))
        self.assertEqual(fake.calls, [])
        rest.assert_not_awaited()


class SignificantChangesFake:
    """Core 2026.11.3 history: attribute-only changes are dropped unless
    ``significant_changes_only`` is "0" (``history/__init__.py``)."""

    def __init__(self):
        self.calls = []
        now = datetime.now(timezone.utc)
        self.changed = (now - timedelta(hours=3)).isoformat()
        self.updated = (now - timedelta(hours=1)).isoformat()

    async def request(self, method, path, **kwargs):
        self.calls.append((method, str(path)))
        _route, _start, query = _parse(path)
        rows = [{"entity_id": query["filter_entity_id"], "state": "on", "attributes": {"brightness": 10},
                 "last_changed": self.changed, "last_updated": self.changed}]
        if query.get("significant_changes_only", "1") == "0":
            rows.append({"entity_id": query["filter_entity_id"], "state": "on", "attributes": {"brightness": 200},
                         "last_changed": self.changed, "last_updated": self.updated})
        return [rows]


class FullHistoryAttributeChanges(unittest.IsolatedAsyncioTestCase):
    async def read(self, minimal):
        fake = SignificantChangesFake()
        with patch.object(compatibility, "rest", new=AsyncMock(side_effect=fake.request)):
            payload = json.loads(await compatibility.get_history("light.synthetic", hours=6, minimal=minimal))
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        return data[0], fake

    async def test_the_fake_drops_attribute_only_changes_by_default(self):
        rows = await SignificantChangesFake().request("GET", "/history/period/2026-11-25T00:00:00+00:00?filter_entity_id=light.a")
        self.assertEqual(len(rows[0]), 1)

    async def test_a_full_read_returns_an_attribute_only_change(self):
        rows, fake = await self.read(False)
        self.assertEqual([row["attributes"]["brightness"] for row in rows], [10, 200])
        self.assertIn(fake.updated, [row["last_updated"] for row in rows])

    async def test_a_minimal_read_asks_for_significant_changes_only(self):
        rows, fake = await self.read(True)
        self.assertEqual(_parse(fake.calls[0][1])[2]["significant_changes_only"], "1")
        self.assertEqual(len(rows), 1)


class IncidentActivityWindow(unittest.TestCase):
    def collect(self, lookback_hours):
        fake = CoreWindowFake(ANALYSIS_TIME)

        async def request(method, path, **kwargs):
            if str(path) == "/states":
                fake.calls.append((method, str(path)))
                return [{"entity_id": "sensor.focus", "state": "on",
                         "last_changed": ANALYSIS_TIME.isoformat()}]
            return await fake.request(method, path, **kwargs)

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
                raise AssertionError("reliability provider should not be requested")

        provider = DirectHaIncidentProvider(
            Index(), rest, WebSocket(), Reliability(), secret="not-a-secret", ha_token="not-a-token"
        )
        bundle = asyncio.run(provider.collect({
            "analysis_timestamp": ANALYSIS_TIME.isoformat().replace("+00:00", "Z"),
            "focus_entity_id": "sensor.focus",
            "automation_id": "",
            "related_entity_ids": [],
            "lookback_hours": lookback_hours,
            "trace_limit": 10,
            "include_dependency_context": False,
            "include_integrity_context": False,
            "include_reliability_context": False,
            "refresh_index": False,
        }))
        return bundle, fake

    def test_history_and_logbook_carry_the_analysis_instant_as_end_time(self):
        for lookback in (1, 24, 48, 168):
            with self.subTest(lookback=lookback):
                _bundle, fake = self.collect(lookback)
                reads = [_parse(path) for _method, path in fake.calls if path != "/states"]
                self.assertEqual(
                    sorted(route.rsplit("/", 1)[0] for route, _start, _query in reads),
                    ["/history/period", "/logbook"],
                )
                for _route, start, query in reads:
                    self.assertEqual(datetime.fromisoformat(query["end_time"]), ANALYSIS_TIME)
                    self.assertEqual(ANALYSIS_TIME - start, timedelta(hours=lookback))

    def test_a_168_hour_lookback_returns_history_and_logbook_across_the_window(self):
        bundle, _fake = self.collect(168)
        start = ANALYSIS_TIME - timedelta(hours=168)
        for source in ("history", "logbook"):
            with self.subTest(source=source):
                times = [datetime.fromisoformat(item.timestamp.replace("Z", "+00:00"))
                         for item in bundle.events if item.source_type == source and item.timestamp]
                self.assertTrue(times)
                self.assertLessEqual(min(times) - start, ROW_INTERVAL)
                self.assertLessEqual(ANALYSIS_TIME - max(times), ROW_INTERVAL)
                # Before HAMCP-502 these events stopped 24 hours after the start.
                self.assertGreater(max(times) - min(times), timedelta(hours=166))

    def test_history_reads_significant_changes_only_and_the_row_says_so(self):
        bundle, fake = self.collect(48)
        history = [_parse(path)[2] for _method, path in fake.calls if "/history/period/" in path]
        self.assertEqual([query["significant_changes_only"] for query in history], ["1"])
        row = next(item for item in bundle.coverage if item.source_type == "entity_history")
        self.assertIn("significant_changes_only=1", " ".join(row.warnings))


def _trace_coverage(completeness="complete", collection_state="complete"):
    return ReliabilitySourceCoverage(
        "automation_traces", "direct_ha_api", "automation_trace", completeness, 5,
        collection_state=collection_state,
    )


def _reliability_bundle(*coverage):
    return ReliabilityEvidenceBundle(
        automation_id="synthetic", automation={}, configuration={}, configuration_fingerprint="",
        blueprint=None, blueprint_path=None, references=[], dynamic_references=[], traces=[],
        system_log_entries=[], coverage=list(coverage),
    )


class ReliabilityContextCoverage(unittest.TestCase):
    """TB-2: the automation_reliability row follows the child bundle."""

    def test_a_missing_bundle_stays_failed(self):
        row = _reliability_context_coverage(None, 0)
        self.assertEqual((row.completeness, row.failure_category, row.failed_items),
                         ("failed", "provider_upstream_error", 1))

    def test_a_complete_bundle_reads_complete(self):
        bundle = _reliability_bundle(
            ReliabilitySourceCoverage("automation_config", "direct_ha_api", "automation_config", "complete", 1),
            _trace_coverage(),
        )
        row = _reliability_context_coverage(bundle, 2)
        self.assertEqual((row.completeness, row.coverage_limitations, row.warnings), ("complete", [], []))

    def test_a_partial_child_makes_the_row_partial_with_its_limitation(self):
        bundle = _reliability_bundle(
            ReliabilitySourceCoverage("automation_config", "direct_ha_api", "automation_config", "complete", 1),
            _trace_coverage("partial", "truncated"),
        )
        self.assertTrue(bundle.partial)
        row = _reliability_context_coverage(bundle, 0)
        self.assertEqual(row.completeness, "partial")
        self.assertIn("automation_traces_partial_coverage", row.coverage_limitations)
        self.assertIn("automation_traces_truncated", row.coverage_limitations)
        self.assertIsNone(row.failure_category)
        self.assertIn("automation_traces_truncated", " ".join(row.warnings))

    def test_a_failed_child_is_named(self):
        bundle = _reliability_bundle(
            ReliabilitySourceCoverage("system_log", "direct_ha_api", "error_log_read", "failed", 0, 1),
            _trace_coverage(),
        )
        row = _reliability_context_coverage(bundle, 0)
        self.assertEqual(row.completeness, "partial")
        self.assertIn("system_log_failed", row.coverage_limitations)

    def test_an_incomplete_trace_state_alone_makes_the_row_partial(self):
        bundle = _reliability_bundle(_trace_coverage("complete", "retention_short"))
        self.assertFalse(bundle.partial)
        row = _reliability_context_coverage(bundle, 0)
        self.assertEqual(row.completeness, "partial")
        self.assertIn("automation_traces_retention_short", row.coverage_limitations)

    def test_a_child_that_does_not_affect_the_result_is_ignored(self):
        side = ReliabilitySourceCoverage("blueprint", "direct_ha_api", "blueprint_read", "partial", 0,
                                         affects_result_status=False)
        row = _reliability_context_coverage(_reliability_bundle(side, _trace_coverage()), 0)
        self.assertEqual(row.completeness, "complete")


def _log_entries(count):
    return [{"name": "synthetic", "message": [f"entry {index}"], "level": "ERROR",
             "source": ["synthetic.py", 1], "timestamp": 1.0, "exception": "", "count": 1,
             "first_occurred": 1.0} for index in range(count)]


class ErrorLogRetention(unittest.IsolatedAsyncioTestCase):
    """TB-5: a full Core system_log ring may have evicted older entries."""

    async def read(self, count):
        with patch.object(compatibility, "ws_command", new=AsyncMock(return_value=_log_entries(count))):
            return json.loads(await compatibility.get_error_log(tail_lines=200))["data"]

    async def test_a_full_default_ring_reports_retention_unknown(self):
        for count in (50, 60):
            with self.subTest(count=count):
                data = await self.read(count)
                self.assertEqual(data["returned_entry_count"], count)
                self.assertTrue(data["truncated"])
                self.assertEqual(data["truncation_reasons"], ["system_log_retention_unknown"])

    async def test_a_ring_below_the_default_reads_whole(self):
        for count in (10, 49):
            with self.subTest(count=count):
                data = await self.read(count)
                self.assertFalse(data["truncated"])
                self.assertEqual(data["truncation_reasons"], [])



if __name__ == "__main__":
    unittest.main()
