"""HAMCP-476 automation run counts over the bounded native logbook. Offline.

``FakeHome`` follows Home Assistant Core 2026.11.0: ``GET /api/logbook``
selects ``start < time_fired < end`` for the named entities and renders an
``automation_triggered`` row as ``{name, message, source, entity_id,
context_id, domain, when}`` (see ``automation_runs/contracts.py``).
Synthetic data and credentials only.
"""

from __future__ import annotations

import ast
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

import httpx  # noqa: E402

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.automation_runs import contracts as c  # noqa: E402
from ha_mcp_engineering.automation_runs import service as service_module  # noqa: E402
from ha_mcp_engineering.automation_runs.service import count_runs  # noqa: E402
from ha_mcp_engineering.clients import logbook  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    ErrorCode, GovernanceError, HomeAssistantApiError, HomeAssistantUnavailableError, InvalidRequestError,
)
from ha_mcp_engineering.ha_core_readmission.routes import STATIC_TOOL_CORE_REQUIREMENTS  # noqa: E402
from ha_mcp_engineering.providers.routing import (  # noqa: E402
    automation_run_counts_policy_allows_read, routing_for_tool,
)
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402
from ha_mcp_engineering.tools import automation_runs as tool_module  # noqa: E402

PACKAGE = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
NOW = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)
START = NOW - timedelta(hours=24)
HA_TOKEN = "synthetic-ha-token-hamcp476"
ACCESS_SECRET = "synthetic-access-secret-hamcp476-0123456789"


def run(entity_id, when, source="state of binary_sensor.synthetic_door"):
    return {"name": "Synthetic", "message": f"triggered by {source}", "source": source,
            "entity_id": entity_id, "context_id": "01SYNTHETIC", "domain": "automation",
            "when": when.isoformat()}


def state_row(entity_id, when):
    return {"entity_id": entity_id, "state": "on", "name": "Synthetic", "when": when.isoformat()}


def entry_row(entity_id, when):
    return {"entity_id": entity_id, "domain": "automation", "message": "turned on",
            "name": "Synthetic", "when": when.isoformat()}


def automations(count):
    return [f"automation.synthetic_{index:03d}" for index in range(count)]


def long_ids(count, length):
    """Valid automation IDs of exactly ``length`` characters."""

    return [f"automation.{'x' * (length - 15)}_{index:03d}" for index in range(count)]


ROW_KEYS = {"automation_id", "runs", "first_run", "last_run", "complete"}
# Logbook row material the reply must never carry (see ``run``).
FIXTURE_STRINGS = ("Synthetic", "01SYNTHETIC", "triggered by", "binary_sensor.synthetic_door", "context_id")


def compact(value):
    return json.dumps(value, separators=(",", ":"), default=str)


class FakeHome:
    """Synthetic Core: states and an exclusive-bound, entity-filtered logbook."""

    def __init__(self, rows=(), states=None, limit=None, inclusive=False):
        self.rows, self.states, self.limit, self.inclusive = list(rows), states, limit, inclusive
        self.calls, self.queries = [], []

    async def request(self, method, path, body=None, raw=False, expected_statuses=frozenset(), *,
                      logbook_read=False):
        assert method == "GET"
        self.calls.append(path)
        if path == "/states":
            assert not raw and not logbook_read
            if isinstance(self.states, BaseException):
                raise self.states
            return self.states
        assert raw and logbook_read
        split = urlsplit(path)
        assert split.path.startswith("/logbook/")
        query = parse_qs(split.query)
        assert set(query) == {"end_time", "entity"}
        lo = datetime.fromisoformat(unquote(split.path[len("/logbook/"):]))
        hi = datetime.fromisoformat(query["end_time"][0])
        entities = query["entity"][0].split(",")
        self.queries.append((lo, hi, entities))
        if self.limit is not None:
            outcome = self.limit(lo, hi, entities)
            if isinstance(outcome, BaseException):
                raise outcome
            if outcome:
                raise logbook.limit_error(outcome, 1)
        selected = []
        for row in self.rows:
            when = datetime.fromisoformat(row["when"])
            inside = lo <= when <= hi if self.inclusive else lo < when < hi
            if inside and row.get("entity_id") in entities:
                selected.append(row)
        return json.dumps(selected)


def checked(**arguments):
    return c.validate_arguments(arguments)


def by_id(result):
    return {row["automation_id"]: row for row in result["counts"]}


class Invariants:
    def assert_shape(self, result):
        # Partial is true exactly when partial_reasons is non-empty.
        self.assertEqual(result["complete"], not result["partial_reasons"])
        self.assertEqual(bool(result["uncovered"]), bool(result["partial_reasons"]))
        self.assertLessEqual(set(result["partial_reasons"]), c.CAUSES)
        uncovered = {automation_id for item in result["uncovered"] for automation_id in item["automation_ids"]}
        for row in result["counts"]:
            self.assertEqual(row["complete"], row["automation_id"] not in uncovered)
        causes = {interval["cause"] for item in result["uncovered"] for interval in item["intervals"]}
        self.assertEqual(causes, set(result["partial_reasons"]))
        self.assertEqual(result["interval_bounds"], "exclusive")
        self.assertEqual(result["counted_event"], "automation_triggered")
        self.assertLessEqual(result["requests"]["logbook_gets"], result["requests"]["max_logbook_gets"])
        self.assertFalse(logbook.LOGBOOK._active)

    async def count(self, home, clock=None, **arguments):
        extra = {} if clock is None else {"clock": clock}
        result = await count_runs(home, checked(**arguments), now=NOW, **extra)
        self.assert_shape(result)
        return result


class IdsModeTests(Invariants, unittest.IsolatedAsyncioTestCase):
    async def test_counts_in_request_order_and_unknown_id_counts_zero_with_a_note(self):
        a, b, unknown = "automation.synthetic_b", "automation.synthetic_a", "automation.synthetic_missing"
        rows = [run(a, NOW - timedelta(hours=3)), run(a, NOW - timedelta(hours=1)), run(b, NOW - timedelta(hours=2)),
                state_row(a, NOW - timedelta(hours=2)), entry_row(b, NOW - timedelta(minutes=5)),
                run("automation.synthetic_other", NOW - timedelta(hours=1))]
        home = FakeHome(rows)
        result = await self.count(home, automation_ids=[a, b, unknown])
        self.assertEqual(result["mode"], "automation_ids")
        self.assertEqual(result["ranking"], "request_order")
        self.assertEqual([row["automation_id"] for row in result["counts"]], [a, b, unknown])
        counts = by_id(result)
        self.assertEqual(counts[a]["runs"], 2)
        self.assertEqual(counts[a]["first_run"], (NOW - timedelta(hours=3)).isoformat())
        self.assertEqual(counts[a]["last_run"], (NOW - timedelta(hours=1)).isoformat())
        self.assertEqual(counts[b]["runs"], 1)
        self.assertEqual((counts[unknown]["runs"], counts[unknown]["first_run"], counts[unknown]["last_run"]),
                         (0, None, None))
        self.assertIn("automation_existence_not_checked", result["completeness_notes"])
        self.assertNotIn("automations_from_current_states", result["completeness_notes"])
        self.assertEqual(result["ignored_rows"], {"state_rows": 1, "other_rows": 1})
        self.assertTrue(result["complete"])
        self.assertEqual(result["requests"], {"logbook_gets": 1, "states_gets": 0,
                                              "max_logbook_gets": 8, "max_seconds": 60})
        self.assertEqual(len(home.calls), 1)
        self.assertEqual(home.queries, [(START, NOW, [a, b, unknown])])

    async def test_default_window_is_24_hours_and_bounds_are_exclusive(self):
        a = "automation.synthetic_a"
        home = FakeHome([run(a, START), run(a, START + timedelta(microseconds=1)),
                         run(a, NOW - timedelta(microseconds=1)), run(a, NOW)])
        result = await self.count(home, automation_ids=[a])
        self.assertEqual(result["requested_interval"], {"start": START.isoformat(), "end": NOW.isoformat()})
        self.assertFalse(result["effective_interval"]["end_clamped"])
        self.assertEqual(by_id(result)[a]["runs"], 2)

    async def test_explicit_window_and_future_end_is_clamped(self):
        a = "automation.synthetic_a"
        start = NOW - timedelta(hours=2)
        home = FakeHome([run(a, NOW - timedelta(hours=3)), run(a, NOW - timedelta(hours=1))])
        result = await self.count(home, automation_ids=[a], start_time=start.isoformat(),
                                  end_time=(NOW + timedelta(hours=1)).isoformat())
        self.assertTrue(result["effective_interval"]["end_clamped"])
        self.assertEqual(home.queries, [(start, NOW, [a])])
        self.assertEqual(by_id(result)[a]["runs"], 1)
        result = await self.count(FakeHome(), automation_ids=[a], hours=2)
        self.assertEqual(result["requested_interval"]["start"], start.isoformat())

    async def test_only_triggered_rows_count(self):
        a = "automation.synthetic_a"
        when = NOW - timedelta(hours=1)
        not_runs = [
            {**run(a, when), "message": "turned off"},
            {**run(a, when), "domain": "script"},
            {**run(a, when), "state": "on"},
            {key: value for key, value in run(a, when).items() if key != "source"},
            {**run(a, when), "message": 1},
            {**run(a, when), "message": "triggeredby x"},
        ]
        home = FakeHome([*not_runs, {**run(a, when), "message": "triggered"}])
        result = await self.count(home, automation_ids=[a])
        self.assertEqual(by_id(result)[a]["runs"], 1)
        self.assertEqual(sum(result["ignored_rows"].values()), len(not_runs))


class BisectTests(Invariants, unittest.IsolatedAsyncioTestCase):
    def rows_around(self, a, middle):
        offsets = (-2, -1, 0, 0, 1, 2)
        return [run(a, middle + timedelta(microseconds=offset)) for offset in offsets]

    async def test_split_at_the_middle_has_no_gap_or_double_count(self):
        a = "automation.synthetic_a"
        middle = START + (NOW - START) / 2
        rows = self.rows_around(a, middle) + [run(a, START + timedelta(hours=1)), run(a, NOW - timedelta(hours=1))]
        for inclusive in (False, True):
            with self.subTest(inclusive=inclusive):
                home = FakeHome(rows, limit=lambda lo, hi, ids: "record_count" if hi - lo > timedelta(hours=13)
                                else None, inclusive=inclusive)
                result = await self.count(home, automation_ids=[a])
                self.assertTrue(result["complete"])
                self.assertEqual(by_id(result)[a]["runs"], len(rows))
                self.assertEqual(result["requests"]["logbook_gets"], 3)
                self.assertEqual(home.queries[1][:2], (START, middle))
                # The later half starts 1 us early: Core's start bound is exclusive.
                self.assertEqual(home.queries[2][:2], (middle - timedelta(microseconds=1), NOW))

    async def test_nested_splits_count_every_row_once(self):
        a = "automation.synthetic_a"
        rows = [run(a, START + timedelta(minutes=17 * index, microseconds=index)) for index in range(1, 80)]
        rows += self.rows_around(a, START + timedelta(hours=6)) + self.rows_around(a, START + timedelta(hours=18))
        expected = sum(1 for row in rows if START < datetime.fromisoformat(row["when"]) < NOW)
        for inclusive in (False, True):
            with self.subTest(inclusive=inclusive):
                home = FakeHome(rows, limit=lambda lo, hi, ids: "structure_items" if hi - lo > timedelta(hours=7)
                                else None, inclusive=inclusive)
                result = await self.count(home, automation_ids=[a])
                self.assertTrue(result["complete"])
                self.assertEqual(by_id(result)[a]["runs"], expected)
                self.assertEqual(result["requests"]["logbook_gets"], 7)

    async def test_unsplittable_window_is_partial_with_the_uncovered_interval(self):
        a = "automation.synthetic_a"
        start = NOW - timedelta(minutes=3)
        home = FakeHome([run(a, NOW - timedelta(minutes=1))], limit=lambda *args: "response_bytes")
        result = await self.count(home, automation_ids=[a], start_time=start.isoformat(), end_time=NOW.isoformat())
        # 180 s splits once; 90 s halves are below two minimum splits.
        self.assertEqual(result["requests"]["logbook_gets"], 3)
        self.assertEqual(result["partial_reasons"], ["response_bytes"])
        self.assertEqual(result["uncovered"], [{"automation_ids": [a], "intervals": [
            {"start": start.isoformat(), "end": NOW.isoformat(), "cause": "response_bytes"}]}])
        self.assertEqual(by_id(result)[a]["runs"], 0)
        self.assertFalse(by_id(result)[a]["complete"])

    async def test_minimum_split_boundary(self):
        a = "automation.synthetic_a"
        for seconds, gets in ((119, 1), (120, 3)):
            with self.subTest(seconds=seconds):
                start = NOW - timedelta(seconds=seconds)
                home = FakeHome(limit=lambda *args: "record_count")
                result = await self.count(home, automation_ids=[a], start_time=start.isoformat(),
                                          end_time=NOW.isoformat())
                self.assertEqual(result["requests"]["logbook_gets"], gets)
                self.assertEqual(result["partial_reasons"], ["record_count"])

    async def test_a_limit_in_processing_also_splits(self):
        a = "automation.synthetic_a"
        rows = [run(a, START + timedelta(minutes=index)) for index in range(1, 30)]
        original = logbook.decode_records
        calls = []

        def decode(text):
            calls.append(text)
            if len(calls) == 1:
                raise logbook.limit_error("structure_items", logbook.MAX_ITEMS)
            return original(text)

        with patch.object(logbook, "decode_records", decode):
            result = await self.count(FakeHome(rows), automation_ids=[a])
        self.assertTrue(result["complete"])
        self.assertEqual(by_id(result)[a]["runs"], len(rows))
        self.assertEqual(result["requests"]["logbook_gets"], 3)


class BudgetTests(Invariants, unittest.IsolatedAsyncioTestCase):
    async def test_request_budget_leaves_queued_windows_uncovered(self):
        a = "automation.synthetic_a"
        home = FakeHome(limit=lambda *args: "response_bytes")
        result = await self.count(home, automation_ids=[a])
        self.assertEqual(result["requests"]["logbook_gets"], 8)
        self.assertEqual(len(home.queries), 8)
        self.assertEqual(result["partial_reasons"], ["request_budget"])
        # Adjacent same-cause intervals merge back into the whole window.
        self.assertEqual(result["uncovered"][0]["intervals"],
                         [{"start": START.isoformat(), "end": NOW.isoformat(), "cause": "request_budget"}])

    async def test_time_budget_before_a_get(self):
        a = "automation.synthetic_a"
        times = iter([0, 1, 61, 61])
        home = FakeHome(limit=lambda lo, hi, ids: "record_count" if hi - lo > timedelta(hours=13) else None)
        result = await self.count(home, clock=lambda: next(times), automation_ids=[a])
        self.assertEqual(result["requests"]["logbook_gets"], 1)
        self.assertEqual(result["partial_reasons"], ["time_budget"])
        self.assertEqual(result["uncovered"][0]["intervals"],
                         [{"start": START.isoformat(), "end": NOW.isoformat(), "cause": "time_budget"}])

    async def test_time_budget_during_a_get_cancels_it_and_releases_the_slot(self):
        a = "automation.synthetic_a"
        blocked = asyncio.Event()

        class Slow(FakeHome):
            async def request(self, *args, **kwargs):
                self.calls.append(args[1])
                await blocked.wait()

        times = iter([0, 59.95, 60.5])
        home = Slow()
        result = await self.count(home, clock=lambda: next(times), automation_ids=[a])
        self.assertEqual(result["requests"]["logbook_gets"], 1)
        self.assertEqual(result["partial_reasons"], ["time_budget"])
        self.assertEqual(by_id(result)[a]["runs"], 0)

    async def test_ids_and_all_mode_budgets_are_disclosed(self):
        self.assertEqual((c.DEFAULT_HOURS, c.IDS_MAX_GETS, c.IDS_MAX_SECONDS), (24, 8, 60))
        self.assertEqual((c.ALL_MAX_GETS, c.ALL_MAX_SECONDS, c.MAX_ALL_AUTOMATIONS), (32, 120, 200))
        result = await self.count(FakeHome(states=[]), all_automations=True)
        self.assertEqual(result["requests"], {"logbook_gets": 0, "states_gets": 1,
                                              "max_logbook_gets": 32, "max_seconds": 120})
        self.assertEqual(result["counts"], [])
        self.assertTrue(result["complete"])


class AllModeTests(Invariants, unittest.IsolatedAsyncioTestCase):
    def states(self, ids):
        other = [{"entity_id": "light.synthetic", "state": "on"}, {"entity_id": "script.synthetic", "state": "off"}]
        return [*other, *({"entity_id": automation_id, "state": "on", "attributes": {}}
                          for automation_id in reversed(ids))]

    async def test_every_automation_is_ranked_by_runs_then_id(self):
        ids = automations(23)
        runs_for = {ids[4]: 5, ids[17]: 5, ids[0]: 2, ids[22]: 7, ids[9]: 1}
        rows = [run(automation_id, NOW - timedelta(minutes=10 * index + 1))
                for automation_id, count in runs_for.items() for index in range(count)]
        home = FakeHome(rows, states=self.states(ids))
        result = await self.count(home, all_automations=True)
        self.assertEqual(result["mode"], "all_automations")
        self.assertEqual(result["ranking"], "runs_desc_then_automation_id")
        self.assertEqual(result["automation_count"], 23)
        order = [row["automation_id"] for row in result["counts"]]
        self.assertEqual(order[:5], [ids[22], ids[4], ids[17], ids[0], ids[9]])
        self.assertEqual(order[5:], sorted(set(ids) - set(runs_for)))
        self.assertEqual([row["runs"] for row in result["counts"][:5]], [7, 5, 5, 2, 1])
        self.assertIn("automations_from_current_states", result["completeness_notes"])
        self.assertNotIn("automation_existence_not_checked", result["completeness_notes"])
        self.assertNotIn("ranking_uses_lower_bound_counts", result["completeness_notes"])
        self.assertEqual(home.calls[0], "/states")
        self.assertEqual([len(query[2]) for query in home.queries], [10, 10, 3])
        self.assertEqual([entity for query in home.queries for entity in query[2]], ids)
        self.assertEqual(result["requests"]["logbook_gets"], 3)
        self.assertEqual(result["requests"]["states_gets"], 1)

    async def test_budget_exceeded_is_partial_names_the_uncovered_and_keeps_every_automation(self):
        ids = automations(115)
        heavy = ids[3]
        rows = [run(heavy, NOW - timedelta(minutes=index + 1)) for index in range(3)]
        rows += [run(ids[50], NOW - timedelta(hours=2)), run(ids[50], NOW - timedelta(hours=3))]
        home = FakeHome(rows, states=self.states(ids),
                        limit=lambda lo, hi, entities: "response_bytes" if heavy in entities else None)
        result = await self.count(home, all_automations=True)
        self.assertEqual(result["requests"]["logbook_gets"], 32)
        self.assertEqual(result["partial_reasons"], ["request_budget"])
        self.assertEqual(len(result["counts"]), 115)
        self.assertEqual({row["automation_id"] for row in result["counts"]}, set(ids))
        self.assertEqual([item["automation_ids"] for item in result["uncovered"]], [ids[:10]])
        self.assertIn("ranking_uses_lower_bound_counts", result["completeness_notes"])
        counts = by_id(result)
        self.assertEqual((counts[ids[50]]["runs"], counts[ids[50]]["complete"]), (2, True))
        self.assertEqual(result["counts"][0]["automation_id"], ids[50])
        self.assertFalse(counts[heavy]["complete"])
        # The eleven other batches were read before the heavy batch's splits.
        self.assertEqual(sum(1 for query in home.queries if heavy not in query[2]), 11)

    async def test_too_many_automations_is_refused_before_any_logbook_read(self):
        home = FakeHome(states=self.states(automations(201)))
        with self.assertRaises(InvalidRequestError) as error:
            await count_runs(home, checked(all_automations=True), now=NOW)
        self.assertEqual(error.exception.details, {"reason": "automation_count_exceeds_bound",
                                                   "automation_count": 201, "limit": 200})
        self.assertEqual(home.calls, ["/states"])
        self.assertFalse(logbook.LOGBOOK._active)
        result = await self.count(FakeHome(states=self.states(automations(200))), all_automations=True)
        self.assertEqual(result["automation_count"], 200)

    async def test_worst_case_bounds_a_heavy_partial_reply(self):
        # 200 IDs of 51 characters: the longest that still fit, 9 to a batch.
        # A 3-minute window: each limited batch splits once into two
        # unsplittable halves with different causes, so they stay apart.
        ids = long_ids(200, 51)
        start = NOW - timedelta(minutes=3)
        rows = [run(automation_id, NOW - timedelta(seconds=index % 170 + 1, microseconds=index + 1))
                for index, automation_id in enumerate(ids) for _ in range(3)]
        rows += [state_row(ids[1], NOW - timedelta(seconds=5)), entry_row(ids[2], NOW - timedelta(seconds=6))]
        limited = {ids[0], ids[100], ids[199]}

        def limit(lo, hi, entities):
            if not limited & set(entities):
                return None
            return "structure_items" if lo == start else "record_count"

        home = FakeHome(rows, states=self.states(ids), limit=limit)
        bounds = []
        original = service_module.worst_case_chars

        def spy(*args):
            bounds.append(original(*args))
            return bounds[-1]

        with patch.object(service_module, "worst_case_chars", side_effect=spy):
            result = await self.count(home, all_automations=True, hours=0.05)
        self.assertEqual(len(bounds), 1)
        self.assertLessEqual(bounds[0], c.MAX_RESPONSE_CHARS)
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["counts"]), 200)
        self.assertEqual(result["requests"]["logbook_gets"], 23 + 3 * 2)
        self.assertEqual(sum(len(item["intervals"]) for item in result["uncovered"]), 6)
        # 9 IDs to a batch; the last batch holds 2.
        self.assertEqual(sum(not row["complete"] for row in result["counts"]), 9 + 9 + 2)
        self.assertEqual(sum(row["runs"] for row in result["counts"] if row["complete"]), 3 * 180)
        self.assertLessEqual(len(compact(result)) + c.ENVELOPE_RESERVE_CHARS, bounds[0])

    async def test_an_oversized_set_is_refused_before_any_logbook_read(self):
        for length in (52, 120, 255):
            with self.subTest(length=length):
                home = FakeHome(states=self.states(long_ids(200, length)))
                with self.assertRaises(InvalidRequestError) as error:
                    await count_runs(home, checked(all_automations=True), now=NOW)
                self.assertEqual(error.exception.details, {"reason": "response_size_exceeds_bound",
                                                           "automation_count": 200, "limit_chars": 60_000})
                self.assertIn("automation_ids", str(error.exception))
                self.assertEqual(home.calls, ["/states"])
                self.assertFalse(logbook.LOGBOOK._active)
        # The house's ~130 short IDs, and 130 IDs of 113 characters, fit.
        for ids in (automations(130), long_ids(130, 113)):
            with self.subTest(length=len(ids[0])):
                result = await self.count(FakeHome(states=self.states(ids)), all_automations=True)
                self.assertEqual(result["automation_count"], 130)

    async def test_a_states_read_failure_is_an_error(self):
        good = {"entity_id": "automation.synthetic_a", "state": "on"}
        for states in ({"entity_id": "automation.synthetic_a"}, None, [good, "x"], [good, {"state": "on"}],
                       [good, {"entity_id": 3}], [good, {"entity_id": "automation.Synthetic-Bad"}],
                       HomeAssistantUnavailableError()):
            with self.subTest(states=states):
                home = FakeHome(states=states)
                with self.assertRaises((HomeAssistantApiError, HomeAssistantUnavailableError)):
                    await count_runs(home, checked(all_automations=True), now=NOW)
                self.assertEqual(home.calls, ["/states"])
                self.assertFalse(logbook.LOGBOOK._active)


class ErrorTests(Invariants, unittest.IsolatedAsyncioTestCase):
    async def assert_error(self, home, error_type, **arguments):
        with self.assertRaises(error_type) as error:
            await count_runs(home, checked(**arguments), now=NOW)
        self.assertFalse(logbook.LOGBOOK._active)
        return error.exception

    async def test_a_failed_get_after_a_success_is_an_error_not_partial(self):
        a = "automation.synthetic_a"
        seen = []

        def limit(lo, hi, ids):
            seen.append(lo)
            if len(seen) == 1:
                return "record_count"
            return HomeAssistantApiError("synthetic") if len(seen) == 3 else None

        await self.assert_error(FakeHome([run(a, NOW - timedelta(hours=1))], limit=limit), HomeAssistantApiError,
                                automation_ids=[a])

    async def test_depth_limit_and_unusable_rows_are_errors(self):
        a = "automation.synthetic_a"
        error = await self.assert_error(FakeHome(limit=lambda *args: "structure_depth"), GovernanceError,
                                        automation_ids=[a])
        self.assertEqual(error.details["reason"], "structure_depth")
        for bad in ({**run(a, NOW - timedelta(hours=1)), "when": None},
                    {**run(a, NOW - timedelta(hours=1)), "when": "2026-11-24T10:00:00"},
                    {**run(a, NOW - timedelta(hours=1)), "when": "not a time"}):
            with self.subTest(bad=bad):
                class Bad(FakeHome):
                    async def request(self, *args, **kwargs):
                        self.calls.append(args[1])
                        return json.dumps([bad])
                await self.assert_error(Bad(), HomeAssistantApiError, automation_ids=[a])

    async def test_a_busy_slot_is_refused_without_a_request(self):
        logbook.LOGBOOK.acquire()
        try:
            home = FakeHome()
            with self.assertRaises(GovernanceError) as error:
                await count_runs(home, checked(automation_ids=["automation.synthetic_a"]), now=NOW)
            self.assertEqual(error.exception.code, ErrorCode.LOGBOOK_BUSY)
            self.assertEqual(home.calls, [])
            self.assertTrue(logbook.LOGBOOK._active)
        finally:
            logbook.LOGBOOK.release()

    async def test_the_slot_is_held_for_the_whole_call(self):
        a = "automation.synthetic_a"
        observed = []

        def limit(lo, hi, ids):
            observed.append(logbook.LOGBOOK._active)
            return "record_count" if hi - lo > timedelta(hours=13) else None

        await self.count(FakeHome(limit=limit), automation_ids=[a])
        self.assertEqual(observed, [True, True, True])

    async def test_window_refusals_send_nothing_and_never_acquire(self):
        a = "automation.synthetic_a"
        for arguments in ({"start_time": NOW.isoformat()},
                          {"start_time": (NOW - timedelta(hours=2)).isoformat(), "hours": 2},
                          {"start_time": (NOW - timedelta(hours=1)).isoformat(),
                           "end_time": (NOW - timedelta(hours=2)).isoformat()},
                          {"start_time": (NOW - timedelta(hours=200)).isoformat()}):
            with self.subTest(arguments=arguments):
                home = FakeHome(states=[])
                with self.assertRaises(InvalidRequestError):
                    await count_runs(home, checked(automation_ids=[a], **arguments), now=NOW)
                self.assertEqual(home.calls, [])
                self.assertFalse(logbook.LOGBOOK._active)
        result = await self.count(FakeHome(), automation_ids=[a], hours=24,
                                  start_time=(NOW - timedelta(hours=2)).isoformat())
        self.assertTrue(result["complete"])


class ValidationTests(unittest.TestCase):
    def test_valid_shapes(self):
        ids = automations(10)
        self.assertEqual(c.validate_arguments({"automation_ids": ids})["automation_ids"], tuple(ids))
        self.assertEqual(c.validate_arguments({"all_automations": True})["mode"], "all_automations")
        self.assertEqual(c.validate_arguments({"automation_ids": ids[:1], "all_automations": False})["mode"],
                         "automation_ids")
        self.assertEqual(c.validate_arguments({"automation_ids": ids[:1], "hours": 1.5})["window_kind"], "hours")
        self.assertEqual(c.validate_arguments({"automation_ids": ids[:1]})["window_kind"], "default")

    def test_invalid_shapes(self):
        ok = ["automation.synthetic_a"]
        for arguments in (None, [], {}, {"all_automations": False}, {"automation_ids": []},
                          {"automation_ids": automations(11)}, {"automation_ids": "automation.synthetic_a"},
                          {"automation_ids": ["light.synthetic"]}, {"automation_ids": ["automation.Bad"]},
                          {"automation_ids": ok * 2}, {"automation_ids": ["automation." + "x" * 600]},
                          {"automation_ids": ok, "all_automations": True}, {"all_automations": 1},
                          {"automation_ids": ok, "hours": 0}, {"automation_ids": ok, "hours": 169},
                          {"automation_ids": ok, "hours": True}, {"automation_ids": ok, "hours": "1"},
                          {"automation_ids": ok, "hours": float("nan")},
                          {"automation_ids": ok, "start_time": "yesterday"},
                          {"automation_ids": ok, "end_time": 5}, {"automation_ids": ok, "extra": 1},
                          {"automation_ids": ok, "entity_ids": ok}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError) as error:
                c.validate_arguments(arguments)
            self.assertEqual(str(error.exception), c.INVALID_MESSAGE)

    def test_batches_keep_order_and_filter_bound(self):
        ids = automations(23)
        self.assertEqual([list(group) for group in c.batches(ids)], [ids[:10], ids[10:20], ids[20:]])
        long_ids = [f"automation.{'x' * 120}_{index}" for index in range(10)]
        groups = c.batches(long_ids)
        self.assertEqual([entity for group in groups for entity in group], long_ids)
        self.assertTrue(all(len(",".join(group)) <= logbook.MAX_FILTER_CHARS for group in groups))
        self.assertGreater(len(groups), 1)


def settings(**values):
    return Settings(**{**dict(ha_url="http://synthetic.invalid", ha_token=HA_TOKEN,
                              access_secret=ACCESS_SECRET, port=8100, audit_path="unused",
                              rate_limit_per_minute=1000, rate_limit_burst=1000,
                              destructive_services=frozenset()), **values})


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = datetime.now(timezone.utc)
        self.home = FakeHome([run("automation.synthetic_a", self.now - timedelta(hours=1))],
                             states=[{"entity_id": "automation.synthetic_a", "state": "on"}])
        for patcher in (patch.object(tool_module, "REST_CLIENT", self.home),
                        patch.object(tool_module, "SETTINGS", settings())):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def call(self, arguments):
        telemetry, token = begin_request()
        telemetry.caller_id = "synthetic_authenticated_caller"
        try:
            return json.loads(await tool_module.registered_tool().run(arguments))
        finally:
            end_request(token)

    async def test_success_metadata(self):
        for arguments in ({"automation_ids": ["automation.synthetic_a"]}, {"all_automations": True}):
            with self.subTest(arguments=arguments):
                result = await self.call(arguments)
                self.assertTrue(result["success"], result)
                self.assertEqual(result["data"]["counts"][0]["runs"], 1)
                self.assertEqual(result["metadata"]["provider"], "engineering")
                self.assertEqual(result["metadata"]["data_providers"], ["direct_ha_api"])
                self.assertFalse(result["metadata"]["fallback_occurred"])
                self.assertEqual(result["metadata"]["completeness"], "complete")

    async def test_partial_is_success_with_partial_metadata(self):
        self.home.limit = lambda *args: "response_bytes"
        result = await self.call({"automation_ids": ["automation.synthetic_a"]})
        self.assertTrue(result["success"])
        self.assertEqual(result["metadata"]["completeness"], "partial")
        self.assertEqual(result["data"]["partial_reasons"], ["request_budget"])

    async def test_success_is_whole_and_carries_no_logbook_material(self):
        for arguments in ({"automation_ids": ["automation.synthetic_a", "automation.synthetic_b"]},
                          {"all_automations": True}):
            with self.subTest(arguments=arguments):
                result = await self.call(arguments)
                self.assertTrue(result["success"], result)
                self.assertNotIn("response_completeness", result)
                self.assertEqual(len(result["data"]["counts"]), result["data"]["automation_count"])
                for row in result["data"]["counts"]:
                    self.assertEqual(set(row), ROW_KEYS)
                text = json.dumps(result)
                for needle in FIXTURE_STRINGS:
                    self.assertNotIn(needle, text)

    async def test_a_reply_near_the_bound_is_whole_and_the_envelope_fits_its_reserve(self):
        ids = long_ids(200, 51)
        self.home.rows = [run(automation_id, self.now - timedelta(minutes=index + 1, microseconds=1))
                          for index, automation_id in enumerate(ids)]
        self.home.states = [{"entity_id": automation_id, "state": "on"} for automation_id in ids]
        self.home.limit = lambda lo, hi, entities: "structure_items" if ids[0] in entities else None
        result = await self.call({"all_automations": True})
        self.assertTrue(result["success"], result.get("details"))
        self.assertEqual(result["metadata"]["completeness"], "partial")
        self.assertNotIn("response_completeness", result)
        self.assertEqual(len(result["data"]["counts"]), 200)
        self.assertLessEqual(len(compact(result)) - len(compact(result["data"])), c.ENVELOPE_RESERVE_CHARS)

    async def test_an_oversized_set_is_an_error_before_any_logbook_read(self):
        for length in (120, 255):
            with self.subTest(length=length):
                self.home.calls.clear()
                self.home.states = [{"entity_id": automation_id, "state": "on"}
                                    for automation_id in long_ids(200, length)]
                result = await self.call({"all_automations": True})
                self.assertFalse(result["success"])
                self.assertEqual(result["error_code"], ErrorCode.INVALID_REQUEST.value)
                self.assertEqual(result["details"]["reason"], "response_size_exceeds_bound")
                self.assertNotIn("data", result)
                self.assertEqual(result["metadata"]["completeness"], "failed")
                self.assertEqual(self.home.calls, ["/states"])
                self.assertFalse(logbook.LOGBOOK._active)

    async def test_a_cut_render_is_an_error_never_a_paged_success(self):
        # Defeat the pre-check: the binder then cuts the reply, which must
        # become an error rather than a paged or trimmed count list.
        self.home.states = [{"entity_id": automation_id, "state": "on"} for automation_id in long_ids(200, 255)]
        with patch.object(service_module, "worst_case_chars", return_value=0):
            result = await self.call({"all_automations": True})
        self.assertFalse(result["success"], result.get("response_completeness"))
        self.assertEqual(result["details"], {"reason": "response_size_exceeds_bound", "automation_count": 200,
                                             "limit_chars": 60_000})
        self.assertNotIn("data", result)
        self.assertNotIn("response_completeness", result)
        self.assertEqual(result["metadata"]["completeness"], "failed")
        self.assertGreater(len(self.home.calls), 1)
        self.assertFalse(logbook.LOGBOOK._active)

    def test_only_an_uncut_success_counts_as_whole(self):
        row = {"automation_id": "automation.synthetic_a", "runs": 0, "first_run": None, "last_run": None,
               "complete": True}
        whole = {"success": True, "data": {"automation_count": 2, "counts": [row, row], "uncovered": []}}
        self.assertTrue(tool_module._whole(whole))
        for cut in ({**whole, "response_completeness": {"truncated": True}},
                    {**whole, "data": {**whole["data"], "counts": [row]}},
                    {**whole, "data": {"automation_count": 2, "counts": [row, row]}},
                    {**whole, "data": {**whole["data"], "counts": None}},
                    {"success": True}):
            with self.subTest(cut=cut):
                self.assertFalse(tool_module._whole(cut))

    async def test_errors_are_never_partial(self):
        self.home.states = None
        result = await self.call({"all_automations": True})
        self.assertFalse(result["success"])
        self.assertNotIn("data", result)
        self.assertNotEqual(result.get("metadata", {}).get("completeness"), "partial")

    async def test_raw_refusal_sends_nothing_and_echoes_nothing(self):
        for bad in ({"automation_ids": ["automation.synthetic_secretish"], "extra": "synthetic-needle"},
                    {"automation_ids": ["../core"]}, {"automation_ids": ["automation.synthetic_a"], "hours": "1"},
                    {"all_automations": "true"}, {}):
            with self.subTest(bad=bad):
                result = await self.call(bad)
                self.assertFalse(result["success"])
                self.assertEqual(result["error_code"], ErrorCode.INVALID_REQUEST.value)
                self.assertEqual(result["metadata"]["provider"], "none")
                for needle in ("synthetic-needle", "../core", "secretish"):
                    self.assertNotIn(needle, json.dumps(result))
        self.assertEqual(self.home.calls, [])
        self.assertFalse(logbook.LOGBOOK._active)

    async def test_policy_drift_refuses_before_any_request(self):
        with patch.object(tool_module, "automation_run_counts_policy_allows_read", return_value=False):
            result = await self.call({"automation_ids": ["automation.synthetic_a"]})
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], ErrorCode.PROVIDER_PROHIBITED.value)
        self.assertEqual(self.home.calls, [])


class StaticTests(unittest.TestCase):
    def test_read_only_static_surface(self):
        files = sorted((PACKAGE / "automation_runs").glob("*.py")) + [PACKAGE / "tools" / "automation_runs.py"]
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    self.assertNotIn(node.attr, {"post", "put", "patch", "delete", "ws_connect", "call_service",
                                                 "execute", "read_text", "read_bytes", "unlink", "write"},
                                     path.name)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"open", "eval", "exec"}, path.name)
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertNotIn(node.value, {"POST", "PUT", "PATCH", "DELETE"}, path.name)
        source = (PACKAGE / "automation_runs" / "service.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("client.request("), 1)
        self.assertEqual(source.count('rest_path("/states")'), 1)
        self.assertEqual(source.count("logbook.fetch_text("), 1)

    def test_routing_core_gate_and_catalog(self):
        decision = routing_for_tool("get_automation_run_counts")
        self.assertEqual(decision.preferred_provider, "engineering")
        self.assertEqual(decision.fallback_providers, ())
        self.assertTrue(automation_run_counts_policy_allows_read())
        self.assertEqual(STATIC_TOOL_CORE_REQUIREMENTS["get_automation_run_counts"], ("core.basic_rest_read",))
        from ha_mcp_engineering.capabilities import BETA_NATIVE_CAPABILITIES
        entry = next(item for item in BETA_NATIVE_CAPABILITIES if item["tool"] == "get_automation_run_counts")
        self.assertEqual((entry["risk"], entry["fallback"], entry["direct_write_allowed"],
                          entry["data_providers"]), ("read", "none", False, ["direct_ha_api"]))
        from ha_mcp_engineering.tools import registry
        tool = registry._SDK_TOOLS.get("get_automation_run_counts")
        self.assertTrue(tool.annotations.readOnlyHint)
        self.assertFalse(tool.annotations.destructiveHint)
        self.assertFalse(tool.parameters["additionalProperties"])
        self.assertNotIn("required", tool.parameters)
        self.assertEqual(set(tool.parameters["properties"]), c.ALLOWED_ARGUMENTS)

    def test_policy_refuses_when_routing_drifts(self):
        from ha_mcp_engineering.providers import routing
        base = routing_for_tool("get_automation_run_counts")
        for drifted in (replace(base, fallback_providers=("direct_ha_api",)),
                        replace(base, preferred_provider="ha_mcp"),
                        replace(base, explicit_direct_fallback_allowed=True)):
            with self.subTest(drifted=drifted), patch.object(routing, "routing_for_tool", return_value=drifted):
                self.assertFalse(routing.automation_run_counts_policy_allows_read())


IDS_NEEDLE = "automation.synthetic_audit_needle"
TIME_NEEDLE = "2026-11-24T09:07:05+00:00"


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def post(self, arguments, app, audit_path):
        config = settings(audit_path=str(audit_path))
        core = SimpleNamespace(acquire=Mock(return_value=object()), consume=Mock(return_value=()),
                               revalidate=Mock(return_value=True), finish=Mock(return_value=True),
                               release=Mock(return_value=True))
        gateway = AuthenticatedMcpGateway(app, config, AuditLogger(str(audit_path), config.access_secret),
                                          core_runtime=core)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway),
                                     base_url="http://127.0.0.1:8100") as http:
            response = await http.post(f"/{config.access_secret}/mcp", json={
                "jsonrpc": "2.0", "id": "synthetic-request", "method": "tools/call",
                "params": {"name": "get_automation_run_counts", "arguments": arguments}})
        return response, core, audit_path.read_text() if audit_path.exists() else ""

    async def test_gateway_rejects_before_sdk_and_audit_excludes_invalid_material(self):
        app = Mock(side_effect=AssertionError("SDK must not be reached"))
        with tempfile.TemporaryDirectory() as directory:
            for index, bad in enumerate((
                    {"automation_ids": [IDS_NEEDLE], "synthetic_key": "synthetic-needle"},
                    {"automation_ids": [IDS_NEEDLE], "all_automations": True},
                    {"automation_ids": [IDS_NEEDLE], "start_time": "synthetic-needle"},
                    # Keys ending in _id are copied into resource_ids unless cleared.
                    {"automation_ids": [IDS_NEEDLE], "entity_id": "automation.synthetic_entity_id_needle"},
                    {"all_automations": True, "automation_id": "automation.synthetic_automation_id_needle"})):
                with self.subTest(bad=bad):
                    response, core, audit = await self.post(bad, app, Path(directory) / f"audit-{index}.jsonl")
                    self.assertIn("InvalidAutomationRunCountsRequest", response.text)
                    for needle in (IDS_NEEDLE, "synthetic-needle", "synthetic_key",
                                   "synthetic_entity_id_needle", "synthetic_automation_id_needle"):
                        self.assertNotIn(needle, response.text)
                        self.assertNotIn(needle, audit)
                    record = next(json.loads(row) for row in audit.splitlines()
                                  if json.loads(row).get("tool_name") == "get_automation_run_counts")
                    self.assertEqual(record["resource_ids"], {})
                    core.acquire.assert_not_called()
            app.assert_not_called()

    async def test_gateway_success_audit_has_no_ids_or_times(self):
        now = datetime.now(timezone.utc)
        end = (now - timedelta(hours=1)).replace(microsecond=0)
        home = FakeHome([run(IDS_NEEDLE, end - timedelta(minutes=5))])

        async def app(scope, receive, send):
            rpc = json.loads((await receive())["body"])
            rendered = await tool_module.registered_tool().run(rpc["params"]["arguments"])
            result = {"jsonrpc": "2.0", "id": rpc["id"],
                      "result": {"content": [{"type": "text", "text": rendered}], "isError": False}}
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": json.dumps(result).encode()})

        end_text = end.isoformat()
        with tempfile.TemporaryDirectory() as directory, patch.object(tool_module, "REST_CLIENT", home), \
                patch.object(tool_module, "SETTINGS", settings()):
            response, core, audit = await self.post(
                {"automation_ids": [IDS_NEEDLE, "automation.synthetic_b"], "hours": 2, "end_time": end_text},
                app, Path(directory) / "audit.jsonl")
        data = json.loads(response.json()["result"]["content"][0]["text"])
        self.assertTrue(data["success"], data)
        self.assertEqual(data["data"]["counts"][0]["runs"], 1)
        self.assertEqual([set(row) for row in data["data"]["counts"]], [ROW_KEYS, ROW_KEYS])
        for needle in FIXTURE_STRINGS:
            self.assertNotIn(needle, response.text)
        rows = [json.loads(row) for row in audit.splitlines()]
        record = next(row for row in rows if row.get("tool_name") == "get_automation_run_counts")
        self.assertEqual(record["resource_ids"], {})
        self.assertEqual(record["parameters"], {
            "profile": "automation-run-counts-read-v1", "mode": "automation_ids", "automation_id_count": 2,
            "window_kind": "hours", "end_time_present": True, "provider": "engineering",
            "data_providers": ["direct_ha_api"], "fallback": "none"})
        for needle in (IDS_NEEDLE, "automation.synthetic_b", end_text, end_text[:19]):
            self.assertNotIn(needle, audit)


if __name__ == "__main__":
    unittest.main()
