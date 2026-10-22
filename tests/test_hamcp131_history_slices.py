"""HAMCP-131 continuable history over the sanitized delegated route. Offline.

``history_slices_sim`` follows the pinned Home Assistant 2026.10.1 recorder
and ha-mcp 8.5.0/8.6.0 ``ha_get_history`` semantics (see its docstring and
``docs/HAMCP_131_INCIDENT_LOGS.md``). Synthetic data and credentials only.
"""

from __future__ import annotations

import ast
import base64
from dataclasses import replace
import inspect
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from history_slices_sim import FakeReader, Recorder, history_result, us  # noqa: E402

from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.routes import STATIC_TOOL_CORE_REQUIREMENTS  # noqa: E402
from ha_mcp_engineering.history_slices import contracts as c  # noqa: E402
from ha_mcp_engineering.history_slices import service as service_module  # noqa: E402
from ha_mcp_engineering.history_slices.service import HistorySliceService  # noqa: E402
from ha_mcp_engineering.incident_logs.cursor import CursorCodec  # noqa: E402
from ha_mcp_engineering.providers.routing import (  # noqa: E402
    history_slices_policy_allows_read, routing_for_tool,
)
from ha_mcp_engineering.request_context import begin_request, current_telemetry, end_request  # noqa: E402
from ha_mcp_engineering.tools import history_slices as tool_module  # noqa: E402


PACKAGE = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
# Synthetic, unmistakable credentials (assembled so no committed literal is
# secret-shaped).
HA_TOKEN = "synthetic-ha-" + "credential-131"
ACCESS_SECRET = "synthetic-access-" + "secret-131"
NOW_MS = us("2026-10-19T13:30:00") // 1000
ENTITY = "climate.mudroom_thermostat"
START, END = "2026-10-19T00:00:00Z", "2026-10-19T12:00:00Z"
S0, S1, S2 = us("2026-10-19T00:00:00"), us("2026-10-19T06:00:00"), us("2026-10-19T12:00:00")
MS = 1000


def at(text: str) -> int:
    return us("2026-10-19T" + text)


def oracle(recorder: Recorder, start_us: int, end_us: int) -> list[tuple[int, str]]:
    """Every in-window state an uninterrupted per-slice read returns."""

    rows = []
    for index in range(c.slice_count(start_us, end_us)):
        lower, upper = c.slice_bounds(start_us, end_us, index)
        rows.extend(row for row in recorder.query(lower - c.OVERLAP_US, upper) if row[0] >= lower)
    return rows


def states(pages) -> list[tuple[int, str]]:
    return [(us(item["at"][:-6]), item["state"]) for page in pages for item in page["states"]]


def bound_us(text: str) -> int:
    return us(text[:-1] if text.endswith("Z") else text[:-6])


def coverage_violations(pages, expected) -> list[str]:
    """Every page's complete_until must be honest against the full read.

    Through the bound (inclusive or exclusive), every expected state has been
    returned on this or an earlier page, and no later page returns a state
    the bound already claimed (review M3).
    """

    problems = []
    for number, page in enumerate(pages):
        bound = page["coverage"]["complete_until"]
        if bound is None:
            continue
        limit = bound_us(bound["at"])
        claimed = (lambda ts: ts <= limit) if bound["inclusive"] else (lambda ts: ts < limit)
        so_far = states(pages[:number + 1])
        later = states(pages[number + 1:])
        if any(claimed(ts) for ts, _ in later):
            problems.append(f"page {number}: a later page returns a state at or before {bound}")
        if [row for row in expected if claimed(row[0])] != [row for row in so_far if claimed(row[0])]:
            problems.append(f"page {number}: {bound} claims states not yet returned")
    return problems


class HistorySliceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.recorder = Recorder()
        self.reader = FakeReader(self.recorder)
        self.clock = [NOW_MS / 1000]
        self.service = self.make_service()

    def make_service(self, factory=None) -> HistorySliceService:
        codec = CursorCodec("history_slices", key=b"h" * 32, clock=lambda: self.clock[0])
        return HistorySliceService(factory or (lambda: self.reader), known_secrets=(HA_TOKEN, ACCESS_SECRET),
                                   codec=codec, clock=lambda: NOW_MS)

    async def read(self, service=None, caller="synthetic_authenticated_caller", **arguments):
        arguments.setdefault("entity_id", ENTITY)
        arguments.setdefault("start", START)
        telemetry, token = begin_request()
        telemetry.caller_id = caller
        try:
            return await (service or self.service).read(arguments)
        finally:
            end_request(token)

    async def read_all(self, **arguments) -> list[dict]:
        arguments.setdefault("end", END)
        pages = [await self.read(**arguments)]
        while pages[-1]["next_cursor"]:
            self.assertLess(len(pages), 300)
            pages.append(await self.read(**arguments, cursor=pages[-1]["next_cursor"]))
        return pages

    def seed(self):
        self.recorder.add(at("00:00:00") - 3600 * 10**6, "heat")
        for text, state in (("01:00:00", "idle"), ("03:00:00", "heat"), ("05:59:59.999500", "idle"),
                            ("06:00:00", "heat"), ("07:30:00", "idle"), ("11:59:59.999999", "heat"),
                            ("12:00:00", "off")):
            self.recorder.add(at(text), state)

    # -- complete reads ---------------------------------------------------------

    async def test_motivating_window_reads_complete_with_coverage(self):
        self.seed()
        page = await self.read(end=END)
        self.assertEqual(page["schema"], "history-slices-v1")
        # The recorder reports its start state at the requested start, so
        # the time the state began is unknown (review L2).
        self.assertEqual(page["state_at_window_start"], {"since": None, "state": "heat"})
        self.assertEqual(states([page]), [(at("01:00:00"), "idle"), (at("03:00:00"), "heat"),
                                          (at("05:59:59.999500"), "idle"), (at("06:00:00"), "heat"),
                                          (at("07:30:00"), "idle"), (at("11:59:59.999999"), "heat")])
        self.assertEqual([item["slice"] for item in page["states"]], [0, 0, 0, 1, 1, 1])
        self.assertTrue(page["complete"])
        self.assertEqual(page["completeness"], "complete")
        self.assertFalse(page["has_more"])
        self.assertIsNone(page["next_cursor"])
        self.assertEqual(page["omissions"], [])
        self.assertEqual(page["coverage"]["complete_until"], {"at": "2026-10-19T12:00:00.000Z",
                                                              "inclusive": False})
        rows = page["coverage"]["slices"]
        self.assertEqual([(r["slice"], r["finished"], r["entries_before_slice"], r["returned"]) for r in rows],
                         [(0, True, 1, 3), (1, True, 2, 3)])
        self.assertEqual(rows[1]["requested_from"], "2026-10-19T05:59:59.999000+00:00")
        self.assertEqual(page["provider"]["route"], "upstream_read_gateway")
        self.assertEqual(page["provider"]["fallback"], "none")
        self.assertEqual(page["upstream_calls"], 2)
        # Engineering built every bound: one entity, absolute, 1 ms overlap.
        self.assertEqual([(call["start_time"], call["end_time"]) for call in self.recorder.calls],
                         [("2026-10-18T23:59:59.999000+00:00", "2026-10-19T06:00:00.000000+00:00"),
                          ("2026-10-19T05:59:59.999000+00:00", "2026-10-19T12:00:00.000000+00:00")])

    async def test_window_edges_start_inclusive_end_exclusive(self):
        self.recorder.add(at("00:00:00") - 1, "before")
        self.recorder.add(at("00:00:00"), "at_start")
        self.recorder.add(at("12:00:00") - 1, "last")
        self.recorder.add(at("12:00:00"), "at_end")
        page = await self.read(end=END)
        # A real change inside the 1 ms overlap keeps its own time.
        self.assertEqual(page["state_at_window_start"], {"since": "2026-10-18T23:59:59.999999+00:00",
                                                         "state": "before"})
        self.assertEqual(states([page]), [(at("00:00:00"), "at_start"), (at("12:00:00") - 1, "last")])

    async def test_fixture_matches_pinned_start_state_and_utc_rendering(self):
        # Review L2: HA 2026.10.1 reports the start state at the requested
        # start (models/state.py L56, L157) and ha-mcp 8.6.0 renders the
        # epoch timestamps in UTC (tools_history.py L54-69).
        self.recorder.add(S0 - 2 * 3600 * 10**6, "heat")
        result = self.recorder.upstream(ENTITY, "2026-10-18T23:59:59.999000+00:00",
                                         "2026-10-19T06:00:00+00:00", 0, 10)
        self.assertEqual(result["data"]["data"]["entities"][0]["states"],
                         [{"state": "heat", "last_changed": "2026-10-18T23:59:59.999000+00:00",
                           "last_updated": "2026-10-18T23:59:59.999000+00:00"}])
        page = await self.read(end=END)
        self.assertEqual(page["state_at_window_start"], {"since": None, "state": "heat"})
        # A real change inside the overlap is reported at its own time.
        self.recorder.add(S0 - 500, "cool")
        page = await self.read(end=END)
        self.assertEqual(page["state_at_window_start"], {"since": "2026-10-18T23:59:59.999500+00:00",
                                                         "state": "cool"})
        self.assertEqual(page["coverage"]["slices"][0]["entries_before_slice"], 2)
        self.assertEqual(page["states"], [])
        self.assertTrue(page["complete"])

    async def test_state_exactly_at_a_slice_boundary_is_returned_once(self):
        self.recorder.add(at("00:00:00") - 10, "a")
        self.recorder.add(S1, "b")
        self.recorder.add(S1 + 1, "c")
        pages = await self.read_all(limit=1)
        self.assertEqual(states(pages), [(S1, "b"), (S1 + 1, "c")])
        self.assertEqual([item["slice"] for page in pages for item in page["states"]], [1, 1])

    async def test_retention_edge_reports_no_window_start_state(self):
        self.recorder.add(at("02:00:00"), "first_retained")
        page = await self.read(end=END)
        self.assertIsNone(page["state_at_window_start"])
        self.assertEqual(states([page]), [(at("02:00:00"), "first_retained")])
        self.assertTrue(page["complete"])
        empty = Recorder()
        page = await self.read(service=self.make_service(lambda: FakeReader(empty)), end=END)
        self.assertEqual((page["states"], page["state_at_window_start"]), ([], None))
        self.assertTrue(page["complete"])
        self.assertIn("Empty output never proves a state change did not occur.", page["limitations"])

    # -- pagination continuity --------------------------------------------------

    async def test_pagination_with_identical_timestamps_has_no_duplicates_or_skips(self):
        self.recorder.add(S0 - 5, "start")
        for i in range(9):
            self.recorder.add(at("02:00:00"), f"same_{i}")
        for i in range(5):
            self.recorder.add(at("08:00:00") + i, f"later_{i}")
        self.recorder.add(at("09:00:00"), "tie_a")
        self.recorder.add(at("09:00:00"), "tie_b")
        expected = oracle(self.recorder, S0, S2)
        self.assertEqual(len(expected), 16)
        for limit in (1, 2, 3, 4, 7, 16, 100):
            with self.subTest(limit=limit):
                pages = await self.read_all(limit=limit)
                self.assertEqual(states(pages), expected)
                self.assertTrue(all(page["returned"] <= limit for page in pages))
                self.assertTrue(pages[-1]["complete"])
                self.assertEqual(pages[0]["state_at_window_start"]["state"], "start")
                self.assertTrue(all(page["state_at_window_start"] == "reported_on_first_page_only"
                                    for page in pages[1:]))
                self.assertTrue(all(page["coverage"]["continued"] for page in pages[1:]))
                for page in pages[:-1]:
                    self.assertIn("states_limit_reached", page["omissions"])
                    self.assertEqual(page["completeness"], "partial")
                self.assertEqual(coverage_violations(pages, expected), [])

    async def test_an_inclusive_bound_is_never_claimed_while_ties_remain(self):
        # Review M3 reproduction: three states share 02:00:00; a page that
        # ends inside the tie must not claim 02:00:00 complete.
        self.recorder.add(S0 - 5, "start")
        for i in range(3):
            self.recorder.add(at("02:00:00"), f"same_{i}")
        self.recorder.add(at("03:00:00"), "after")
        first = await self.read(end=END, limit=2)
        self.assertEqual(first["coverage"]["complete_until"],
                         {"at": "2026-10-19T02:00:00.000000+00:00", "inclusive": False})
        second = await self.read(end=END, limit=1, cursor=first["next_cursor"])
        self.assertEqual([item["state"] for item in second["states"]], ["same_2"])
        # The next unread state is later, so this tie is now complete.
        self.assertEqual(second["coverage"]["complete_until"],
                         {"at": "2026-10-19T02:00:00.000000+00:00", "inclusive": True})
        pages = [first, second]
        while pages[-1]["next_cursor"]:
            pages.append(await self.read(end=END, limit=1, cursor=pages[-1]["next_cursor"]))
        self.assertEqual(coverage_violations(pages, oracle(self.recorder, S0, S2)), [])

    async def test_the_cursor_carries_across_slices_and_reports_coverage_per_page(self):
        base = us("2026-10-17T00:00:00")
        self.recorder.add(base - 5, "s")
        for hour in range(0, 48, 3):
            self.recorder.add(base + hour * 3600 * 10**6 + 7, f"h{hour}")
        start, end = "2026-10-17T00:00:00Z", "2026-10-19T00:00:00Z"
        pages = await self.read_all(start=start, end=end, limit=3)
        self.assertEqual(states(pages), oracle(self.recorder, base, S0))
        self.assertEqual(pages[0]["window"]["slices"], 8)
        seen = sorted({row["slice"] for page in pages for row in page["coverage"]["slices"]})
        self.assertEqual(seen, list(range(8)))
        for page in pages[:-1]:
            bound = page["coverage"]["complete_until"]
            self.assertIsNotNone(bound)
        self.assertEqual(pages[-1]["coverage"]["complete_until"]["at"], "2026-10-19T00:00:00.000Z")

    async def test_empty_slices_are_bounded_by_the_call_cap_and_resume(self):
        start, end = "2026-10-12T00:00:00Z", "2026-10-19T00:00:00Z"
        self.recorder.add(us("2026-10-12T00:00:00") - 5, "steady")
        self.recorder.add(us("2026-10-18T23:00:00"), "late")
        first = await self.read(start=start, end=end, limit=100)
        self.assertEqual(first["upstream_calls"], c.MAX_UPSTREAM_CALLS)
        self.assertIn("upstream_call_limit", first["omissions"])
        self.assertTrue(first["truncated"])
        self.assertEqual(first["coverage"]["complete_until"]["at"], "2026-10-15T00:00:00.000Z")
        pages = [first]
        while pages[-1]["next_cursor"]:
            pages.append(await self.read(start=start, end=end, limit=100, cursor=pages[-1]["next_cursor"]))
        self.assertEqual(states(pages), [(us("2026-10-18T23:00:00"), "late")])
        # 28 slice reads plus one re-verification of the first page's
        # window-start range (review M1).
        self.assertEqual(sum(page["upstream_calls"] for page in pages), 29)
        self.assertEqual(sum(page["verification_calls"] for page in pages), 1)

    async def test_defaulted_end_is_fixed_by_the_cursor(self):
        self.recorder.add(us("2026-10-19T10:00:00"), "a")
        self.recorder.add(us("2026-10-19T10:00:01"), "b")
        first = await self.read(start="2026-10-19T08:00:00Z", limit=1)
        self.assertTrue(first["window"]["end_defaulted"])
        self.assertEqual(first["window"]["end"], "2026-10-19T13:30:00.000Z")
        self.recorder.add(us("2026-10-19T13:29:00"), "c")
        second = await self.read(start="2026-10-19T08:00:00Z", limit=5, cursor=first["next_cursor"])
        self.assertEqual(second["window"]["end"], "2026-10-19T13:30:00.000Z")
        self.assertEqual([s["state"] for s in second["states"]], ["b", "c"])

    # -- continuity breaks fail closed -----------------------------------------

    async def test_verification_read_failure_is_partial_and_resumes(self):
        self.seed()
        first = await self.read(end=END, limit=4)
        self.assertEqual([item["slice"] for item in first["states"]], [0, 0, 0, 1])
        self.reader.fail_at = {len(self.recorder.calls)}
        second = await self.read(end=END, limit=4, cursor=first["next_cursor"])
        self.assertEqual((second["returned"], second["verification_calls"], second["completeness"]),
                         (0, 1, "failed"))
        self.assertIn("upstream_transport_timeout", second["omissions"])
        self.assertTrue(second["has_more"])
        rest = await self.read_all(end=END, limit=4, cursor=second["next_cursor"])
        self.assertEqual(states([first, *rest]), oracle(self.recorder, S0, S2))
        self.assertEqual(rest[0]["verification_calls"], 1)

    async def test_more_entries_before_a_slice_than_pinned_is_representation_invalid(self):
        self.recorder.add(S0 - 5000, "a")
        self.recorder.add(S0 - 500, "b")
        self.recorder.add(at("01:00:00"), "c")
        with patch.object(c, "MAX_ENTRIES_BEFORE_SLICE", 1):
            page = await self.read(end=END)
        self.assertIn("representation_invalid", page["omissions"])
        self.assertIsNone(page["next_cursor"])
        self.assertEqual(page["returned"], 0)

    async def test_purge_between_pages_is_stale(self):
        self.seed()
        first = await self.read(end=END, limit=2)
        self.recorder.purge_before(at("02:00:00"))
        with self.assertRaises(GovernanceError) as raised:
            await self.read(end=END, limit=2, cursor=first["next_cursor"])
        self.assertEqual(raised.exception.code, ErrorCode.STALE_CURSOR)
        self.assertEqual(raised.exception.details["reason"], "history_continuity_broken")

    async def test_late_write_before_the_position_is_stale(self):
        self.seed()
        first = await self.read(end=END, limit=2)
        self.recorder.add(at("00:30:00"), "late")
        with self.assertRaises(GovernanceError) as raised:
            await self.read(end=END, limit=2, cursor=first["next_cursor"])
        self.assertEqual(raised.exception.code, ErrorCode.STALE_CURSOR)

    async def test_changed_slice_start_state_is_stale_at_a_slice_transition(self):
        self.seed()
        first = await self.read(end=END, limit=3)
        self.assertEqual(first["coverage"]["complete_until"]["at"], "2026-10-19T06:00:00.000Z")
        # The state that prevailed at the next slice start changes before it
        # is read: the slice-start check refuses rather than skip it.
        self.recorder.rows = [row for row in self.recorder.rows if row[0] != at("05:59:59.999500")]
        with self.assertRaises(GovernanceError) as raised:
            await self.read(end=END, limit=3, cursor=first["next_cursor"])
        self.assertEqual(raised.exception.code, ErrorCode.STALE_CURSOR)

    async def test_purge_of_the_slice_start_state_is_stale(self):
        self.recorder.add(S0 - 5, "s")
        self.recorder.add(at("01:00:00"), "a")
        first = await self.read(end=END, limit=1)
        self.assertEqual(first["coverage"]["complete_until"]["at"], "2026-10-19T06:00:00.000Z")
        self.recorder.rows = []
        with self.assertRaises(GovernanceError) as raised:
            await self.read(end=END, limit=1, cursor=first["next_cursor"])
        self.assertEqual(raised.exception.code, ErrorCode.STALE_CURSOR)

    async def test_break_after_the_first_check_is_partial_without_a_cursor(self):
        self.recorder.add(S0 - 5, "s")
        self.recorder.add(at("01:00:00"), "a")
        self.recorder.add(at("07:00:00"), "b")

        def purge(index):
            if index == 1:
                self.recorder.rows = [row for row in self.recorder.rows if row[0] != at("01:00:00")]
        self.reader.before_call = purge
        page = await self.read(end=END, limit=10)
        self.assertEqual(states([page]), [(at("01:00:00"), "a")])
        self.assertIn("continuity_broken", page["omissions"])
        self.assertFalse(page["has_more"])
        self.assertIsNone(page["next_cursor"])
        self.assertEqual(page["completeness"], "partial")
        self.assertFalse(page["complete"])

    async def test_provider_contract_change_is_stale(self):
        self.seed()
        first = await self.read(end=END, limit=2)
        self.reader.contract = "synthetic-contract-changed"
        with self.assertRaises(GovernanceError) as raised:
            await self.read(end=END, limit=2, cursor=first["next_cursor"])
        self.assertEqual((raised.exception.code, raised.exception.details["reason"]),
                         (ErrorCode.STALE_CURSOR, "provider_contract_changed"))
        self.assertEqual(len(self.recorder.calls), 1)

    async def test_cursor_integrity_query_binding_and_expiry(self):
        self.seed()
        first = await self.read(end=END, limit=2)
        cursor = first["next_cursor"]
        calls = len(self.recorder.calls)
        body, signature = cursor.split(".")
        tampered = body[:-2] + ("A" if body[-2] != "A" else "B") + body[-1] + "." + signature
        for arguments, code in (
            ({"end": END, "cursor": tampered}, ErrorCode.INVALID_CURSOR),
            ({"end": "2026-10-19T11:00:00Z", "cursor": cursor}, ErrorCode.INVALID_CURSOR),
            ({"end": END, "entity_id": "climate.other", "cursor": cursor}, ErrorCode.INVALID_CURSOR),
            ({"end": END, "start": "2026-10-19T00:00:01Z", "cursor": cursor}, ErrorCode.INVALID_CURSOR),
        ):
            with self.subTest(arguments=arguments), self.assertRaises(GovernanceError) as raised:
                await self.read(**arguments)
            self.assertEqual(raised.exception.code, code)
        other = self.make_service()
        other.codec = CursorCodec("history_slices", key=b"z" * 32, clock=lambda: self.clock[0])
        with self.assertRaises(GovernanceError) as raised:
            await self.read(service=other, end=END, cursor=cursor)
        self.assertEqual(raised.exception.code, ErrorCode.INVALID_CURSOR)
        self.clock[0] += c.CURSOR_TTL_SECONDS + 1
        with self.assertRaises(GovernanceError) as raised:
            await self.read(end=END, cursor=cursor)
        self.assertEqual(raised.exception.code, ErrorCode.STALE_CURSOR)
        self.assertEqual(len(self.recorder.calls), calls)

    # -- partial results ---------------------------------------------------------

    async def test_upstream_failure_is_partial_and_resumable(self):
        self.seed()
        self.reader.fail_at = {1}
        first = await self.read(end=END, limit=50)
        self.assertEqual(len(first["states"]), 3)
        self.assertIn("upstream_transport_timeout", first["omissions"])
        self.assertTrue(first["has_more"])
        self.assertEqual(first["completeness"], "partial")
        rest = await self.read(end=END, limit=50, cursor=first["next_cursor"])
        self.assertEqual(states([first, rest]), oracle(self.recorder, S0, S2))
        self.assertTrue(rest["complete"])

    async def test_failure_before_any_state_is_failed_with_a_cursor(self):
        self.seed()
        self.reader.fail_at = {0}
        page = await self.read(end=END)
        self.assertEqual(page["completeness"], "failed")
        self.assertEqual(page["states"], [])
        self.assertTrue(page["has_more"])
        retry = await self.read(end=END, cursor=page["next_cursor"])
        self.assertEqual(states([retry]), oracle(self.recorder, S0, S2))

    async def test_unpinned_shapes_are_representation_invalid_without_a_cursor(self):
        self.seed()

        def wrong_entity(result):
            result["data"]["data"]["entities"][0]["entity_id"] = "climate.other"
            return result

        def wrong_count(result):
            result["data"]["data"]["entities"][0]["count"] += 1
            return result

        def naive_time(result):
            for item in result["data"]["data"]["entities"][0]["states"]:
                item["last_updated"] = item["last_updated"][:-6]
            return result

        def descending(result):
            result["data"]["data"]["entities"][0]["states"].reverse()
            return result

        for transform in (wrong_entity, wrong_count, naive_time, descending, lambda result: ["synthetic"]):
            with self.subTest(transform=getattr(transform, "__name__", "list")):
                self.reader.transform = transform
                page = await self.read(end=END)
                self.assertEqual(page["states"], [])
                self.assertIn("representation_invalid", page["omissions"])
                self.assertIsNone(page["next_cursor"])
                self.assertEqual(page["completeness"], "failed")

    async def test_gateway_partial_is_disclosed(self):
        self.seed()

        def partial(result):
            result["metadata"]["completeness"] = "partial"
            return result
        self.reader.transform = partial
        page = await self.read(end=END)
        self.assertIn("gateway_redacted_or_truncated", page["omissions"])
        self.assertFalse(page["complete"])
        self.assertTrue(page["coverage"]["slices"][0]["gateway_redacted_or_truncated"])

    async def test_response_byte_ceiling_shrinks_the_page_and_resumes(self):
        self.recorder.add(S0 - 5, "s")
        for i in range(100):
            # Each non-ASCII character encodes as six JSON bytes.
            self.recorder.add(at("01:00:00") + i, f"{i:03d}" + "\u00e9" * 250)
        pages = await self.read_all(limit=100)
        self.assertIn("response_bytes_limit", pages[0]["omissions"])
        self.assertLess(pages[0]["returned"], 100)
        self.assertTrue(pages[0]["truncated"])
        for page in pages:
            self.assertLessEqual(len(json.dumps(page, separators=(",", ":"))), c.RESPONSE_BUDGET)
            self.assertTrue(all(len(item["state"]) <= c.MAX_STATE_CHARS for item in page["states"]))
        self.assertEqual([state[:3] for _, state in states(pages)], [f"{i:03d}" for i in range(100)])
        self.assertEqual(coverage_violations(pages, oracle(self.recorder, S0, S2)), [])

    async def test_a_byte_cut_inside_a_tie_does_not_claim_the_tied_timestamp(self):
        self.recorder.add(S0 - 5, "s")
        for i in range(100):
            self.recorder.add(at("01:00:00"), f"{i:03d}" + "\u00e9" * 250)
        pages = await self.read_all(limit=100)
        self.assertIn("response_bytes_limit", pages[0]["omissions"])
        self.assertEqual(pages[0]["coverage"]["complete_until"]["inclusive"], False)
        self.assertEqual(coverage_violations(pages, oracle(self.recorder, S0, S2)), [])

    # -- secrets ------------------------------------------------------------------

    async def test_planted_secrets_never_reach_any_output_field(self):
        bearer = "synthetic-bearer-" + "value-131"
        password = "synthetic-pass" + "word-131"
        url_token = "synthetic-url-" + "token-131"
        planted = [f"Bearer {bearer}", f"password={password}",
                   f"https://user:{password}@example.invalid/x?access_token={url_token}",
                   HA_TOKEN, f"token={ACCESS_SECRET}", "x" * 250 + f" password={password}"]
        self.recorder.add(S0 - 5, f"api_key: {bearer}")
        for i, text in enumerate(planted):
            self.recorder.add(at("01:00:00") + i * MS, text)
            self.recorder.add(at("07:00:00") + i * MS, text + " again")
        secrets = (bearer, password, url_token, HA_TOKEN, ACCESS_SECRET)
        pages = await self.read_all(limit=2)
        self.assertEqual(sum(page["returned"] for page in pages), 12)
        for page in pages:
            rendered = json.dumps(page)
            for secret in secrets:
                self.assertNotIn(secret, rendered)
            if page["next_cursor"]:
                body = base64.urlsafe_b64decode(page["next_cursor"].split(".")[0] + "==").decode()
                for secret in secrets:
                    self.assertNotIn(secret, body)
            for item in page["states"]:
                opener = item["state"].rfind("[REDACTED:")
                self.assertTrue(opener == -1 or "]" in item["state"][opener:])
        self.assertTrue(any("redacted_by_policy" in page["omissions"] for page in pages))
        self.assertNotIn(bearer, json.dumps(pages[0]["state_at_window_start"]))

    async def test_sanitizer_runs_only_in_the_single_exit(self):
        callers = []
        real = service_module.sanitize_untrusted_data

        strict = []

        def spy(*args, **kwargs):
            callers.append(inspect.stack()[1].function)
            result = real(*args, **kwargs)
            strict.append((args[0], result))
            return result

        self.seed()
        with patch.object(service_module, "sanitize_untrusted_data", spy):
            pages = await self.read_all(limit=2)
        self.assertTrue(callers)
        self.assertEqual(set(callers), {"_finish"})
        # Only complete envelopes reach the strict pass (never a state before
        # the merge), and each page is its output unchanged.
        for argument, _ in strict:
            self.assertEqual(set(argument) & {"states", "coverage", "next_cursor", "omissions"},
                             {"states", "coverage", "next_cursor", "omissions"})
        values = [result.value for _, result in strict]
        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertTrue(any(page is value for value in values))
        tree = ast.parse((PACKAGE / "history_slices" / "service.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                if "sanitize_untrusted_data" in names:
                    self.assertEqual(node.name, "_finish")
        tree = ast.parse((PACKAGE / "history_slices" / "contracts.py").read_text(encoding="utf-8"))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                    for alias in node.names}
        self.assertFalse({name for name in imported if "sanitiz" in name})

    async def test_exit_pass_carries_known_secrets_and_its_redaction_makes_partial(self):
        real = service_module.sanitize_untrusted_data
        seen = []

        def spy(*args, **kwargs):
            seen.append(tuple(kwargs.get("known_secrets", ())))
            return real(*args, **kwargs)

        self.seed()
        with patch.object(service_module, "sanitize_untrusted_data", spy):
            page = await self.read(end=END)
        self.assertTrue(page["complete"])
        self.assertTrue(seen)
        for secrets in seen:
            self.assertTrue({HA_TOKEN, ACCESS_SECRET} <= set(secrets))

        def redacting(*args, **kwargs):
            return replace(real(*args, **kwargs), redacted_field_count=1)

        with patch.object(service_module, "sanitize_untrusted_data", redacting):
            page = await self.read(end=END)
        self.assertFalse(page["complete"])
        self.assertIn("redacted_by_policy", page["omissions"])

    # -- refusals -----------------------------------------------------------------

    async def test_invalid_arguments_send_nothing(self):
        for arguments in (
            {"end": "2026-10-26T00:00:01Z"},                      # span over 7 days
            {"end": START},                                       # empty window
            {"end": END, "extra": 1},
            {"end": END, "entity_id": "../climate.x"},
            {"end": END, "entity_id": "Climate.Upper"},
            {"end": END, "entity_id": ["climate.a"]},
            {"end": END, "limit": 0},
            {"end": END, "limit": c.MAX_LIMIT + 1},
            {"end": END, "limit": "5"},
            {"end": END, "start": "2026-10-19T00:00:00"},         # no offset
            {"start": "2026-10-19T13:00:00Z", "end": "2026-10-19T15:00:00Z"},  # future end
            {"end": END, "cursor": ""},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(GovernanceError) as raised:
                await self.read(**arguments)
            self.assertEqual(raised.exception.details["reason"], "invalid_arguments")
        self.assertEqual(self.recorder.calls, [])

    async def test_route_unavailable_unreviewed_version_or_anonymous_sends_nothing(self):
        cases = (
            (lambda: None, "synthetic_authenticated_caller"),
            (lambda: FakeReader(self.recorder, version="8.4.3"), "synthetic_authenticated_caller"),
            (lambda: FakeReader(self.recorder, version="8.7.0"), "synthetic_authenticated_caller"),
            (lambda: self.reader, "anonymous"),
        )
        for factory, caller in cases:
            with self.subTest(caller=caller), self.assertRaises(GovernanceError) as raised:
                await self.read(service=self.make_service(factory), caller=caller, end=END)
            self.assertEqual(raised.exception.details["reason"], "history_route_unavailable")
        self.reader.active = False
        with self.assertRaises(GovernanceError) as raised:
            await self.read(end=END)
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(self.recorder.calls, [])
        with self.assertRaises(GovernanceError):
            service_module.HistorySlicesRuntime().require()

    async def test_route_retired_mid_read_is_partial_with_a_cursor(self):
        self.seed()

        def retire(index):
            if index == 1:
                self.reader.active = False
        self.reader.before_call = retire
        original = self.reader.read

        async def read(*args):
            result = await original(*args)
            if len(self.recorder.calls) == 1:
                self.reader.active = False
            return result
        self.reader.read = read
        page = await self.read(end=END)
        self.assertIn("provider_authority_changed", page["omissions"])
        self.assertTrue(page["has_more"])
        self.assertEqual(len(self.recorder.calls), 1)

    # -- static and registration ---------------------------------------------------

    def test_read_only_static_surface(self):
        files = sorted((PACKAGE / "history_slices").glob("*.py")) + [PACKAGE / "tools" / "history_slices.py"]
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    self.assertNotIn(node.attr, {"post", "put", "patch", "delete", "request", "ws_connect",
                                                 "call_service", "execute", "read_text", "read_bytes",
                                                 "unlink", "write"}, path.name)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"open", "eval", "exec"}, path.name)
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    modules = [node.module or ""] if isinstance(node, ast.ImportFrom) else \
                        [alias.name for alias in node.names]
                    for module in modules:
                        self.assertNotRegex(module, r"clients|aiohttp|httpx|websocket|requests", path.name)
        source = (PACKAGE / "history_slices" / "service.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("reader.read("), 1)

    def test_routing_core_gate_and_catalog(self):
        decision = routing_for_tool("get_history_slices")
        self.assertEqual(decision.preferred_provider, "engineering")
        self.assertEqual(decision.fallback_providers, ())
        self.assertTrue(history_slices_policy_allows_read())
        self.assertEqual(STATIC_TOOL_CORE_REQUIREMENTS["get_history_slices"], ("core.basic_rest_read",))
        from ha_mcp_engineering.capabilities import BETA_NATIVE_CAPABILITIES
        entry = next(item for item in BETA_NATIVE_CAPABILITIES if item["tool"] == "get_history_slices")
        self.assertEqual((entry["risk"], entry["fallback"], entry["direct_write_allowed"],
                          entry["data_providers"]), ("read", "none", False, ["upstream_read_gateway"]))
        from ha_mcp_engineering.tools import registry
        tool = registry._SDK_TOOLS.get("get_history_slices")
        self.assertTrue(tool.annotations.readOnlyHint)
        self.assertFalse(tool.annotations.destructiveHint)
        self.assertFalse(tool.parameters["additionalProperties"])
        self.assertEqual(tool.parameters["required"], ["entity_id", "start"])

    def test_policy_refuses_when_routing_drifts(self):
        from ha_mcp_engineering.providers import routing
        drifted = replace(routing_for_tool("get_history_slices"), fallback_providers=("direct_ha_api",))
        with patch.object(routing, "routing_for_tool", return_value=drifted):
            self.assertFalse(routing.history_slices_policy_allows_read())


class HistorySliceToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.recorder = Recorder()
        self.settings = Settings(
            ha_url="http://127.0.0.1:9", ha_token=HA_TOKEN, access_secret=ACCESS_SECRET, port=8100,
            audit_path="unused-synthetic-audit.jsonl", rate_limit_per_minute=120, rate_limit_burst=20,
            destructive_services=frozenset(), ha_timeout_seconds=1.0,
        )
        runtime = service_module.HistorySlicesRuntime()
        runtime.configure(self.settings, reader_factory=lambda: FakeReader(self.recorder))
        for patcher in (patch.object(tool_module, "HISTORY_SLICES", runtime),
                        patch.object(tool_module, "SETTINGS", self.settings)):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def call(self, arguments: dict) -> dict:
        telemetry, token = begin_request()
        telemetry.caller_id = "synthetic_authenticated_caller"
        try:
            return json.loads(await tool_module.registered_tool().run(arguments))
        finally:
            end_request(token)

    async def test_tool_success_metadata_and_raw_refusal(self):
        recent = service_module.now_ms() * 1000 - 60 * 10**6
        self.recorder.add(recent, "heat")
        start = c.format_public((recent - 10**6) // 1000 * 1000)
        result = await self.call({"entity_id": ENTITY, "start": start})
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["returned"], 1)
        self.assertEqual(result["metadata"]["provider"], "engineering")
        self.assertEqual(result["metadata"]["data_providers"], ["upstream_read_gateway"])
        self.assertFalse(result["metadata"]["fallback_occurred"])
        self.assertEqual(result["metadata"]["completeness"], "complete")
        calls = len(self.recorder.calls)
        for bad in ({"entity_id": "../core", "start": start},
                    {"entity_id": ENTITY, "start": start, "extra": 1},
                    {"entity_id": ENTITY, "start": start, "limit": "5"}):
            refused = await self.call(bad)
            self.assertFalse(refused["success"])
            self.assertNotIn("../core", json.dumps(refused))
        self.assertEqual(len(self.recorder.calls), calls)

    async def test_policy_drift_refuses_before_any_request(self):
        with patch.object(tool_module, "history_slices_policy_allows_read", return_value=False):
            result = await self.call({"entity_id": ENTITY,
                                      "start": c.format_public((service_module.now_ms() - 60_000) * 1000)})
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], ErrorCode.PROVIDER_PROHIBITED.value)
        self.assertEqual(self.recorder.calls, [])


try:
    import test_hamcp162_yaml_admission as yaml_admission  # noqa: E402
    import test_upstream_8_6_compatibility as compatibility_860  # noqa: E402
except ImportError:  # pragma: no cover - the modules are in this repository
    yaml_admission = compatibility_860 = None


class HistoryReaderGatewayTests(yaml_admission.Harness):
    """The gateway's fixed ``ha_get_history`` route (``history_reader``)."""

    async def history_gateway(self, version, recorder):
        tools = yaml_admission.captured("8.5.0") if version == "8.5.0" else compatibility_860.capture()["tools"]
        gateway, transport = await self.gateway(version, tools=tools)
        transport.history = recorder
        original = transport.execute_read

        async def execute_read(tool_name, arguments, **kwargs):
            if tool_name != "ha_get_history":
                return await original(tool_name, arguments, **kwargs)
            kwargs["catalog_validator"](transport.catalog)
            before = kwargs.get("before_dispatch")
            if before is not None:
                result = before()
                if inspect.isawaitable(result):
                    await result
            transport.sent.append((tool_name, dict(arguments)))
            upstream = recorder.upstream(arguments["entity_ids"], arguments["start_time"],
                                         arguments["end_time"], arguments["offset"], arguments["limit"])
            return yaml_admission.McpReadResult(
                protocol_version=transport.catalog.protocol_version,
                server_name=transport.catalog.server_name, server_version=transport.catalog.server_version,
                call_result=yaml_admission.ok(upstream["data"]),
                connection_latency_ms=1.0, tool_call_latency_ms=1.0,
            )
        transport.execute_read = execute_read
        return gateway, transport

    async def test_admitted_reader_sends_only_the_fixed_argument_shape(self):
        for version in ("8.5.0", "8.6.0"):
            with self.subTest(version=version):
                recorder = Recorder()
                recorder.add(S0 - 5, "heat")
                recorder.add(at("01:00:00"), "idle")
                recorder.add(at("07:00:00"), "heat")
                gateway, transport = await self.history_gateway(version, recorder)
                audit = Mock()
                reader = gateway.history_reader(audit=audit)
                self.assertIsNotNone(reader)
                self.assertEqual(reader.version, version)
                service = HistorySliceService(lambda: reader, known_secrets=(HA_TOKEN,))
                telemetry, token = begin_request()
                telemetry.caller_id = "synthetic_authenticated_caller"
                telemetry.core_dispatch_authorizer = lambda: True
                try:
                    page = await service.read({"entity_id": ENTITY, "start": START, "end": END, "limit": 1})
                    rest = await service.read({"entity_id": ENTITY, "start": START, "end": END,
                                               "cursor": page["next_cursor"]})
                    self.assertIs(current_telemetry(), telemetry)
                finally:
                    end_request(token)
                self.assertEqual(states([page, rest]), [(at("01:00:00"), "idle"), (at("07:00:00"), "heat")])
                self.assertEqual(page["provider"]["upstream_version"], version)
                sent = [arguments for name, arguments in transport.sent if name == "ha_get_history"]
                # Page 1 reads slice 0; page 2 re-verifies the slice 0 range
                # it returned (review M1), then reads slice 1.
                self.assertEqual(len(sent), 3)
                self.assertEqual(rest["verification_calls"], 1)
                for arguments in sent:
                    self.assertEqual(set(arguments), {"entity_ids", "start_time", "end_time", "limit", "offset",
                                                      "order", "source", "minimal_response",
                                                      "significant_changes_only"})
                    self.assertEqual((arguments["entity_ids"], arguments["order"], arguments["source"],
                                      arguments["minimal_response"]), (ENTITY, "asc", "history", True))
                events = [call.args[0] for call in audit.write.call_args_list]
                self.assertEqual([e["event"] for e in events], ["history_slice_read"] * 3)
                self.assertEqual({e["provider"] for e in events}, {"upstream_read_gateway"})
                self.assertFalse(any(e["fallback_occurred"] for e in events))
                self.assertIsNone(current_telemetry())

    async def gateway_read(self, service, **arguments):
        arguments = {"entity_id": ENTITY, "start": START, "end": END, **arguments}
        telemetry, token = begin_request()
        telemetry.caller_id = "synthetic_authenticated_caller"
        telemetry.core_dispatch_authorizer = lambda: True
        try:
            return await service.read(arguments)
        finally:
            end_request(token)

    async def continuity_case(self, recorder, change, *, limit):
        """Read one page, apply ``change`` to the recorder, continue once."""

        gateway, transport = await self.history_gateway("8.6.0", recorder)
        service = HistorySliceService(lambda: gateway.history_reader(audit=Mock()), known_secrets=(HA_TOKEN,))
        first = await self.gateway_read(service, limit=limit)
        self.assertTrue(first["has_more"])
        change()
        sent = len(transport.sent)
        with self.assertRaises(GovernanceError) as raised:
            await self.gateway_read(service, limit=limit, cursor=first["next_cursor"])
        self.assertEqual(raised.exception.code, ErrorCode.STALE_CURSOR)
        self.assertEqual(raised.exception.details["reason"], "history_continuity_broken")
        return first, transport.sent[sent:]

    @staticmethod
    def five_states(recorder):
        recorder.add(S0 - 5, "heat")
        for hour, state in ((1, "idle"), (2, "heat"), (3, "idle"), (4, "heat"), (5, "idle")):
            recorder.add(at(f"0{hour}:00:00"), state)

    async def test_in_place_change_inside_the_previous_page_is_stale(self):
        # Review M1 reproduction: 02:00 changes in place while the last
        # returned state (03:00, the old overlap anchor) and every offset
        # stay the same.
        recorder = Recorder()
        self.five_states(recorder)

        def change():
            recorder.rows = [(ts, "cool" if ts == at("02:00:00") else state) for ts, state in recorder.rows]
        first, sent = await self.continuity_case(recorder, change, limit=3)
        self.assertEqual([item["at"][11:19] for item in first["states"]], ["01:00:00", "02:00:00", "03:00:00"])
        # One read of the current slice, from the previous page's range start.
        self.assertEqual([arguments["offset"] for _, arguments in sent], [0])

    async def test_balanced_insert_and_delete_inside_the_previous_page_is_stale(self):
        recorder = Recorder()
        self.five_states(recorder)

        def change():
            recorder.rows = [row for row in recorder.rows if row[0] != at("02:00:00")]
            recorder.add(at("02:30:00"), "heat")
        await self.continuity_case(recorder, change, limit=3)

    async def test_change_in_a_finished_slice_the_previous_page_returned_is_stale(self):
        recorder = Recorder()
        self.five_states(recorder)
        recorder.add(at("07:00:00"), "heat")
        recorder.add(at("08:00:00"), "idle")

        def change():
            recorder.rows = [(ts, "cool" if ts == at("01:00:00") else state) for ts, state in recorder.rows]
        first, sent = await self.continuity_case(recorder, change, limit=6)
        self.assertEqual([item["slice"] for item in first["states"]], [0, 0, 0, 0, 0, 1])
        # The verification read of slice 0 is the one that fails.
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1]["start_time"], "2026-10-18T23:59:59.999000+00:00")

    async def test_unchanged_history_continues_through_the_gateway(self):
        recorder = Recorder()
        self.five_states(recorder)
        recorder.add(at("07:00:00"), "heat")
        recorder.add(at("08:00:00"), "idle")
        gateway, _ = await self.history_gateway("8.6.0", recorder)
        service = HistorySliceService(lambda: gateway.history_reader(audit=Mock()), known_secrets=(HA_TOKEN,))
        pages = [await self.gateway_read(service, limit=2)]
        while pages[-1]["next_cursor"]:
            pages.append(await self.gateway_read(service, limit=2, cursor=pages[-1]["next_cursor"]))
        self.assertEqual(states(pages), oracle(recorder, S0, S2))
        self.assertTrue(pages[-1]["complete"])

    async def test_reader_refuses_unvalidated_arguments_with_zero_dispatch(self):
        gateway, transport = await self.history_gateway("8.5.0", Recorder())
        reader = gateway.history_reader()
        good = c.format_us(S0)
        for arguments in (("../x", good, good, 0, 1), (ENTITY, "24h", good, 0, 1),
                          (ENTITY, good, "2026-10-19T00:00:00Z", 0, 1), (ENTITY, good, good, -1, 1),
                          (ENTITY, good, good, 0, 1001), (ENTITY, good, good, True, 1),
                          ([ENTITY], good, good, 0, 1)):
            with self.subTest(arguments=arguments):
                result = await reader.read(*arguments)
                self.assertEqual(result, {"success": False, "details": {"failure_category": "argument_validation"}})
        self.assertEqual([name for name, _ in transport.sent if name == "ha_get_history"], [])

    async def test_parent_authority_refusal_dispatches_nothing(self):
        gateway, transport = await self.history_gateway("8.5.0", Recorder())
        reader = gateway.history_reader()
        telemetry, token = begin_request()
        telemetry.core_dispatch_authorizer = lambda: False
        try:
            result = await reader.read(ENTITY, c.format_us(S0), c.format_us(S1), 0, 5)
        finally:
            end_request(token)
        self.assertFalse(result["success"])
        self.assertEqual([name for name, _ in transport.sent if name == "ha_get_history"], [])

    async def test_no_route_or_drift_supplies_no_reader(self):
        from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway
        self.assertIsNone(UpstreamReadGateway().history_reader())
        tools = [tool for tool in yaml_admission.captured("8.5.0") if tool["name"] != "ha_get_history"]
        gateway, transport = await self.gateway("8.5.0", tools=tools)
        self.assertIsNone(gateway.history_reader())
        gateway, transport = await self.history_gateway("8.5.0", Recorder())
        reader = gateway.history_reader()
        transport.set_tools(tools)
        from mcp.server.fastmcp import FastMCP
        await gateway.initialize(FastMCP("synthetic-hamcp131-reinitialize"))
        self.assertFalse(reader.current())
        result = await reader.read(ENTITY, c.format_us(S0), c.format_us(S1), 0, 5)
        self.assertFalse(result["success"])
        self.assertEqual([name for name, _ in transport.sent if name == "ha_get_history"], [])

    async def test_gateway_sanitizes_planted_secrets_before_the_service(self):
        recorder = Recorder()
        recorder.add(S0 - 5, "heat")
        recorder.add(at("01:00:00"), "token=" + HA_TOKEN)
        gateway, transport = await self.history_gateway("8.5.0", recorder)
        service = HistorySliceService(lambda: gateway.history_reader(), known_secrets=(HA_TOKEN, ACCESS_SECRET))
        telemetry, token = begin_request()
        telemetry.caller_id = "synthetic_authenticated_caller"
        telemetry.core_dispatch_authorizer = lambda: True
        try:
            page = await service.read({"entity_id": ENTITY, "start": START, "end": END})
        finally:
            end_request(token)
        self.assertNotIn(HA_TOKEN, json.dumps(page))
        self.assertEqual(page["returned"], 1)


if __name__ == "__main__":
    unittest.main()
