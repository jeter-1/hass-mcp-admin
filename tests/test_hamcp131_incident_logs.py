"""HAMCP-131 incident-window log reads. Offline: loopback peer, synthetic data.

The peer (``incident_logs_journal_sim``) follows the pinned Supervisor
2026.09.1 log response shape and journal-gatewayd v257.9 ``Range``
arithmetic; see its docstring and ``docs/HAMCP_131_INCIDENT_LOGS.md``.
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
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))

from incident_logs_journal_sim import SupervisorSim, ms, start  # noqa: E402

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    CORE_INDEPENDENT_STATIC_TOOLS, CORE_INDEPENDENT_SUPERVISOR_ONLY,
)
from ha_mcp_engineering.incident_logs import contracts as c  # noqa: E402
from ha_mcp_engineering.incident_logs import service as service_module  # noqa: E402
from ha_mcp_engineering.incident_logs.cursor import CursorCodec  # noqa: E402
from ha_mcp_engineering.incident_logs.service import IncidentLogService  # noqa: E402
from ha_mcp_engineering.incident_logs.transport import JournalTransport  # noqa: E402
from ha_mcp_engineering.mcp_sdk_compatibility import McpSdkToolRegistry  # noqa: E402
from ha_mcp_engineering.mcp_server import create_mcp_server  # noqa: E402
from ha_mcp_engineering.providers.routing import (  # noqa: E402
    incident_logs_policy_allows_read, routing_for_tool,
)
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.tools import incident_logs as tool_module  # noqa: E402
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402

from tests.same_thread_asgi_client import SameThreadAsgiTestClient  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
# Synthetic, unmistakable credentials (assembled so no committed literal is
# secret-shaped).
SUPERVISOR_TOKEN = "synthetic-supervisor-" + "credential-131"
HA_TOKEN = "synthetic-ha-" + "credential-131"
ACCESS_SECRET = "synthetic-access-" + "secret-131"
NOW = ms("2026-10-19T13:30:00.000")
CORE = "/core/logs"
SELF = "/addons/self/logs"


def core_line(text: str, level: str, ts_ms: int) -> str:
    stamp = service_module.datetime.fromtimestamp(ts_ms / 1000, service_module.timezone.utc)
    return f"{stamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} {level} (MainThread) [synthetic.component] {text}"


def fill(journal, first: str, count: int, step_ms: int, label: str, level: str = "INFO",
         **kwargs):
    base = ms(first)
    return [journal.add(base + i * step_ms, core_line(f"{label} event {i}", level, base + i * step_ms),
                        **kwargs)
            for i in range(count)]


def message_ids(page: dict) -> list[str]:
    return [entry["source"] + "|" + entry["message"].split("] ", 1)[1] for entry in page["entries"]]


class IncidentLogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sim = SupervisorSim(SUPERVISOR_TOKEN)
        self.runner, self.url = await start(self.sim)
        self.addAsyncCleanup(self.runner.cleanup)
        self.clock = [NOW / 1000]
        self.service = self.make_service()

    def make_service(self, token: str = SUPERVISOR_TOKEN) -> IncidentLogService:
        transport = JournalTransport(token_source=lambda: token, timeout_seconds=2.0, base_url=self.url)
        codec = CursorCodec("incident_logs", key=b"k" * 32, clock=lambda: self.clock[0])
        return IncidentLogService(transport, known_secrets=(HA_TOKEN, ACCESS_SECRET), codec=codec,
                                  clock=lambda: NOW)

    async def read(self, service=None, caller="synthetic_authenticated_caller", **arguments):
        telemetry, token = begin_request()
        telemetry.caller_id = caller
        try:
            return await (service or self.service).read(arguments)
        finally:
            end_request(token)

    async def read_all(self, **arguments) -> list[dict]:
        pages = [await self.read(**arguments)]
        while pages[-1]["next_cursor"]:
            self.assertLess(len(pages), 200)
            pages.append(await self.read(**arguments, cursor=pages[-1]["next_cursor"]))
        return pages

    def expected(self, sources: list[str], start_ms: int, end_ms: int) -> list[str]:
        rows = []
        for position, source in enumerate(sources):
            path = CORE if source == "core" else SELF
            for entry in self.sim.journal(path).entries:
                if start_ms <= entry.ts_ms < end_ms:
                    rows.append((entry.ts_ms, position, entry.seq,
                                 source + "|" + entry.message.split("] ", 1)[1]))
        return [row[3] for row in sorted(rows)]

    def source(self, page: dict, key: str) -> dict:
        return next(row for row in page["sources"] if row["source"] == key)

    # -- motivating incident ------------------------------------------------

    async def test_motivating_incident_window_across_sources_with_coverage(self):
        core = self.sim.journal(CORE)
        base = ms("2026-10-19T11:00:00.000")
        for i in range(240):
            ts = base + i * 30_000
            core.add(ts, core_line(f"heartbeat {i}", "INFO", ts))
            if i == 122:
                failure = ts + 2_123
                core.add(failure, core_line(
                    "Thermostat — Apply Setpoints: Error executing script. Error for call_service "
                    "at pos 1: climate.mudroom_thermostat returned 503 Service Unavailable",
                    "ERROR", failure))
                core.add(failure, "Traceback (most recent call last):")
        fill(self.sim.journal(SELF), "2026-10-19T11:58:00.000", 10, 30_000, "self")
        page = await self.read(sources=["core", "supervisor", "addon:self", "addon:zwave_js"],
                               start="2026-10-19T11:55:00Z", end="2026-10-19T12:05:00Z",
                               min_level="error")
        self.assertEqual(page["returned"], 2)
        entry, traceback = page["entries"]
        self.assertEqual(entry["ts"], "2026-10-19T12:01:02.123Z")
        self.assertEqual((entry["source"], entry["process"], entry["level"], entry["level_basis"]),
                         ("core", "homeassistant[77]", "error", "parsed"))
        self.assertIn("climate.mudroom_thermostat returned 503", entry["message"])
        core_row = self.source(page, "core")
        self.assertEqual(core_row["status"], "complete")
        self.assertEqual(core_row["reason"], "end_reached")
        self.assertEqual(core_row["start_location"], "exact")
        self.assertEqual(core_row["earliest_available"], "2026-10-19T11:00:00.000Z")
        self.assertEqual(core_row["latest_available"], "2026-10-19T12:59:30.000Z")
        self.assertFalse(core_row["retention_edge_reached"])
        # The traceback line is its own journal entry: it inherits ERROR.
        self.assertEqual((traceback["level"], traceback["level_basis"]), ("error", "inherited"))
        self.assertEqual(core_row["skipped_by_filter"], 20)
        self.assertEqual(core_row["entries_returned"], 2)
        for key, pattern in (("supervisor", r"^/supervisor/.+$"), ("addon:zwave_js", r"^/addons/[^/]+/(?!security).+$")):
            row = self.source(page, key)
            self.assertEqual((row["status"], row["reason"], row["requests"], row["route"]),
                             ("unavailable", "authority_not_granted", 0, None))
            self.assertEqual(row["required_route_pattern"], pattern)
            self.assertIn(f"source_unavailable:{key}", page["omissions"])
        self.assertEqual([row["source"] for row in page["sources"]],
                         ["core", "supervisor", "addon:self", "addon:zwave_js"])
        self.assertTrue(all(path.split("?")[0] in (CORE, SELF) for _, path, _ in self.sim.calls))
        self.assertTrue(all(method == "GET" for method, _, _ in self.sim.calls))
        self.assertFalse(page["complete"])

    async def test_inherited_level_on_unparsed_entry(self):
        journal = self.sim.journal(CORE)
        journal.add(ms("2026-10-19T12:00:00.000"), core_line("first", "ERROR", ms("2026-10-19T12:00:00.000")))
        journal.add(ms("2026-10-19T12:00:00.000"), "  File \"synthetic.py\", line 1")
        journal.add(ms("2026-10-19T12:00:01.000"), core_line("later", "INFO", ms("2026-10-19T12:00:01.000")))
        page = await self.read(sources=["core"], start="2026-10-19T11:59:00Z", end="2026-10-19T12:01:00Z")
        self.assertEqual([(e["level"], e["level_basis"]) for e in page["entries"]],
                         [("error", "parsed"), ("error", "inherited"), ("info", "parsed")])

    # -- window edges --------------------------------------------------------

    async def test_window_edges_start_inclusive_end_exclusive(self):
        journal = self.sim.journal(CORE)
        for text in ("11:59:59.999", "12:00:00.000", "12:00:00.001", "12:09:59.999", "12:10:00.000",
                     "12:10:00.001"):
            ts = ms(f"2026-10-19T{text}")
            journal.add(ts, core_line(text, "INFO", ts))
        page = await self.read(sources=["core"], start="2026-10-19T12:00:00Z", end="2026-10-19T12:10:00Z")
        self.assertEqual([e["ts"][11:23] for e in page["entries"]],
                         ["12:00:00.000", "12:00:00.001", "12:09:59.999"])
        self.assertEqual(self.source(page, "core")["reason"], "end_reached")
        self.assertTrue(page["complete"])
        self.assertIsNone(page["next_cursor"])

    async def test_window_bound_and_argument_refusals_send_nothing(self):
        cases = [
            {"sources": ["core"], "start": "2026-10-19T06:00:00Z", "end": "2026-10-19T12:00:00.001Z"},
            {"sources": ["core"], "start": "2026-10-19T12:00:00Z", "end": "2026-10-19T12:00:00Z"},
            {"sources": ["core"], "start": "2026-10-19T12:00:00"},
            {"sources": ["core"], "start": "2026-10-19T12:00:00.0001Z"},
            {"sources": ["core"], "start": "2026-10-19T13:00:00Z", "end": "2026-10-19T13:40:00Z"},
            {"sources": ["core"] * 2, "start": "2026-10-19T12:00:00Z"},
            {"sources": ["core", "supervisor", "host", "addon:self", "addon:a"], "start": "2026-10-19T12:00:00Z"},
            {"sources": ["core"], "start": "2026-10-19T12:00:00Z", "limit": 201},
            {"sources": ["core"], "start": "2026-10-19T12:00:00Z", "limit": True},
            {"sources": ["core"], "start": "2026-10-19T12:00:00Z", "min_level": "warn"},
            {"sources": ["core"], "start": "2026-10-19T12:00:00Z", "text": "x" * 129},
            {"sources": ["core"], "start": "2026-10-19T12:00:00Z", "text": "a\nb"},
            {"sources": ["core"], "start": "2026-10-19T12:00:00Z", "regex": ".*"},
        ]
        self.assertEqual(c.MAX_WINDOW_SECONDS, 21600)
        for arguments in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(c.IncidentLogsError) as caught:
                    await self.read(**arguments)
                self.assertEqual(caught.exception.reason, "invalid_arguments")
        self.assertEqual(self.sim.calls, [])
        # Exactly six hours is accepted.
        await self.read(sources=["core"], start="2026-10-19T06:00:00Z", end="2026-10-19T12:00:00Z")

    async def test_retention_edge_and_empty_and_after_tail(self):
        fill(self.sim.journal(CORE), "2026-10-19T12:00:00.000", 5, 1000, "core")
        page = await self.read(sources=["core", "addon:self"], start="2026-10-19T11:00:00Z",
                               end="2026-10-19T12:30:00Z")
        row = self.source(page, "core")
        self.assertEqual((row["status"], row["start_location"], row["retention_edge_reached"]),
                         ("partial", "retention_edge", True))
        self.assertEqual(row["gaps"], [{"from": "2026-10-19T11:00:00.000Z", "to": "2026-10-19T12:00:00.000Z",
                                        "reason": "before_retention_edge"}])
        self.assertIn("before_retention_edge:core", page["omissions"])
        self.assertEqual(page["entries"][0]["ts"], "2026-10-19T12:00:00.000Z")
        self.assertEqual(page["returned"], 5)
        empty = self.source(page, "addon:self")
        self.assertEqual((empty["status"], empty["reason"], empty["requests"]), ("complete", "empty_journal", 1))
        later = await self.read(sources=["core"], start="2026-10-19T12:10:00Z", end="2026-10-19T12:20:00Z")
        self.assertEqual(self.source(later, "core")["reason"], "window_after_tail")
        self.assertEqual(later["returned"], 0)
        self.assertEqual(later["limitations"][-1], "An empty result never proves an event did not occur.")

    async def test_retention_edge_head_returned_once_across_pages(self):
        fill(self.sim.journal(CORE), "2026-10-19T12:00:00.000", 9, 1000, "core")
        pages = await self.read_all(sources=["core"], start="2026-10-19T11:00:00Z",
                                    end="2026-10-19T12:30:00Z", limit=2)
        ids = [i for page in pages for i in message_ids(page)]
        self.assertEqual(ids, self.expected(["core"], ms("2026-10-19T11:00:00.000"), ms("2026-10-19T12:30:00.000")))

    async def test_oldest_entry_exactly_at_start_is_returned_at_the_retention_edge(self):
        # Entries in that millisecond may already be vacuumed, so the edge
        # is disclosed; the retained head itself is in the window.
        journal = self.sim.journal(CORE)
        for _ in range(3):
            journal.add(ms("2026-10-19T12:00:00.000"), core_line("at start", "INFO", ms("2026-10-19T12:00:00.000")))
        fill(journal, "2026-10-19T12:00:01.000", 4, 1000, "core")
        page = await self.read(sources=["core"], start="2026-10-19T12:00:00Z", end="2026-10-19T12:30:00Z")
        row = self.source(page, "core")
        self.assertEqual((row["start_location"], row["retention_edge_reached"]), ("retention_edge", True))
        self.assertEqual(message_ids(page),
                         self.expected(["core"], ms("2026-10-19T12:00:00.000"), ms("2026-10-19T12:30:00.000")))
        self.assertEqual(page["returned"], 7)
        # Review L1: no fictitious zero-length gap at the window start.
        self.assertEqual(row["gaps"], [])
        self.assertFalse([reason for reason in page["omissions"] if reason.startswith("before_retention_edge")])
        self.assertIn("source_partial:core", page["omissions"])

    async def test_tail_probe_returns_the_last_two_entries_as_pinned(self):
        # Review L2: a cursorless ``entries=:-1:2`` is seek_tail then
        # previous_skip(2) (journal-gatewayd.c L590, L179): the last two.
        journal = self.sim.journal(CORE)
        fill(journal, "2026-10-19T11:00:00.000", 5, 1000, "old")
        self.assertEqual([entry.seq for entry in journal.select("", -1, 2)],
                         [entry.seq for entry in journal.entries[-2:]])
        self.assertEqual([entry.seq for entry in journal.select("", -3, 1)], [journal.entries[-4].seq])
        recent = ms("2026-10-19T12:00:05.000")
        journal.add(recent, core_line("only in window", "INFO", recent))
        page = await self.read(sources=["core"], start="2026-10-19T12:00:00Z", end="2026-10-19T12:30:00Z")
        # The second-to-last entry precedes the start, so the tail probe
        # locates it exactly: tail, head and one forward page.
        self.assertEqual(message_ids(page), ["core|only in window"])
        row = self.source(page, "core")
        self.assertEqual((row["start_location"], row["requests"], row["gaps"]), ("exact", 3, []))
        self.assertEqual(self.sim.calls[0][2].get("Range"), "entries=:-1:2")

    async def test_tail_entry_exactly_at_start_is_not_consumed(self):
        # The tail probe's entry at the start is in the window, and so is an
        # earlier entry in the same millisecond.
        journal = self.sim.journal(CORE)
        for text in ("11:59:59.000", "12:00:00.000", "12:00:00.000"):
            ts = ms(f"2026-10-19T{text}")
            journal.add(ts, core_line(text, "INFO", ts))
        page = await self.read(sources=["core"], start="2026-10-19T12:00:00Z", end="2026-10-19T12:30:00Z")
        self.assertEqual([e["ts"][11:23] for e in page["entries"]], ["12:00:00.000", "12:00:00.000"])
        self.assertEqual(self.source(page, "core")["start_location"], "exact")
        self.assertTrue(page["complete"])

    async def test_large_journal_locates_exact_start_within_budget(self):
        fill(self.sim.journal(CORE), "2026-10-19T09:00:00.000", 6000, 1000, "core")
        page = await self.read(sources=["core"], start="2026-10-19T10:00:00.500Z",
                               end="2026-10-19T10:10:00Z", limit=5)
        self.assertEqual(page["entries"][0]["ts"], "2026-10-19T10:00:01.000Z")
        row = self.source(page, "core")
        self.assertEqual(row["start_location"], "exact")
        self.assertLessEqual(row["requests"], c.MAX_REQUESTS_PER_SOURCE)
        # Bisection stops within one page of the start.
        self.assertLess(row["outside_window"], c.PAGE_ENTRIES)

    async def test_start_beyond_probe_reach_is_a_disclosed_gap(self):
        fill(self.sim.journal(CORE), "2026-10-19T09:00:00.000", 400, 1000, "core")
        with patch.object(c, "PROBE_DISTANCES", (4, 8)):
            page = await self.read(sources=["core"], start="2026-10-19T09:00:30Z", end="2026-10-19T10:00:00Z")
        row = self.source(page, "core")
        self.assertEqual((row["status"], row["start_location"]), ("partial", "not_located"))
        self.assertEqual(row["gaps"][0]["reason"], "start_not_located")
        self.assertIn("start_not_located:core", page["omissions"])

    # -- pagination ---------------------------------------------------------

    async def test_pagination_identical_timestamps_no_duplicates_or_skips(self):
        core, own = self.sim.journal(CORE), self.sim.journal(SELF)
        base = ms("2026-10-19T12:00:00.000")
        for i in range(130):
            ts = base + (i // 7) * 1000  # runs of seven identical timestamps
            core.add(ts, core_line(f"core event {i}", "INFO", ts))
            if i % 3 == 0:
                own.add(ts, core_line(f"self event {i}", "INFO", ts))
        with patch.object(c, "PAGE_ENTRIES", 6):
            pages = await self.read_all(sources=["core", "addon:self"], start="2026-10-19T12:00:00Z",
                                        end="2026-10-19T12:00:15Z", limit=5)
        ids = [i for page in pages for i in message_ids(page)]
        self.assertEqual(ids, self.expected(["core", "addon:self"], base, base + 15_000))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(page["returned"] <= 5 for page in pages))
        self.assertFalse(pages[-1]["has_more"])

    async def test_pagination_with_filter_counts_skips(self):
        core = self.sim.journal(CORE)
        base = ms("2026-10-19T12:00:00.000")
        for i in range(60):
            core.add(base + i, core_line(f"core event {i}", "WARNING" if i % 4 == 0 else "INFO", base + i))
        pages = await self.read_all(sources=["core"], start="2026-10-19T12:00:00Z",
                                    end="2026-10-19T12:01:00Z", min_level="warning", limit=4)
        ids = [i for page in pages for i in message_ids(page)]
        self.assertEqual(ids, [f"core|core event {i}" for i in range(0, 60, 4)])
        self.assertEqual(sum(self.source(p, "core")["skipped_by_filter"] for p in pages), 45)

    async def test_text_filter_is_literal(self):
        core = self.sim.journal(CORE)
        base = ms("2026-10-19T12:00:00.000")
        core.add(base, core_line("value a.c here", "INFO", base))
        core.add(base + 1, core_line("value abc here", "INFO", base + 1))
        page = await self.read(sources=["core"], start="2026-10-19T12:00:00Z", end="2026-10-19T12:01:00Z",
                               text="A.C")
        self.assertEqual(message_ids(page), ["core|value a.c here"])
        self.assertTrue(page["filters"]["text_filter_applied"])

    async def test_text_filter_cannot_probe_a_redacted_secret(self):
        # The filter reads the sanitized text, so a fragment of a secret
        # never matches the entry that held it.
        core = self.sim.journal(CORE)
        base = ms("2026-10-19T12:00:00.000")
        for offset, secret in enumerate((HA_TOKEN, ACCESS_SECRET, SUPERVISOR_TOKEN)):
            core.add(base + offset, core_line(f"leaked {secret} here", "INFO", base + offset))
        for secret in (HA_TOKEN, ACCESS_SECRET, SUPERVISOR_TOKEN):
            with self.subTest(secret=secret[:12]):
                page = await self.read(sources=["core"], start="2026-10-19T12:00:00Z",
                                       end="2026-10-19T12:01:00Z", text=secret[-14:])
                self.assertEqual(page["entries"], [])
                self.assertEqual(self.source(page, "core")["skipped_by_filter"], 3)

    async def test_vacuum_between_pages_fails_closed_as_stale(self):
        fill(self.sim.journal(CORE), "2026-10-19T12:00:00.000", 30, 1000, "core")
        first = await self.read(sources=["core"], start="2026-10-19T12:00:00Z", end="2026-10-19T12:10:00Z",
                                limit=10)
        self.sim.journal(CORE).vacuum(15)
        with self.assertRaises(GovernanceError) as caught:
            await self.read(sources=["core"], start="2026-10-19T12:00:00Z", end="2026-10-19T12:10:00Z",
                            limit=10, cursor=first["next_cursor"])
        self.assertEqual(caught.exception.code, ErrorCode.STALE_CURSOR)
        self.assertEqual(caught.exception.details["reason"], "journal_continuity_broken")

    async def test_vacuum_inside_a_call_fails_the_source_with_a_gap(self):
        fill(self.sim.journal(CORE), "2026-10-19T12:00:00.000", 40, 1000, "core")
        with patch.object(c, "PAGE_ENTRIES", 5):
            calls = len(self.sim.calls)
            # Locate takes one tail and one head probe; vacuum before page two.
            self.sim.mutate_before[calls + 4] = lambda sim: sim.journal(CORE).vacuum(20)
            page = await self.read(sources=["core"], start="2026-10-19T11:00:00Z", end="2026-10-19T12:30:00Z",
                                   limit=50)
        row = self.source(page, "core")
        self.assertEqual((row["status"], row["reason"]), ("failed", "continuity_broken"))
        self.assertEqual(row["gaps"][-1]["reason"], "unread_after_failure")
        self.assertIn("source_failed:core", page["omissions"])
        self.assertFalse(page["complete"])
        # The first page holds the unread head plus PAGE_ENTRIES entries.
        self.assertEqual(page["returned"], 6)

    async def test_cursor_integrity_query_binding_and_expiry(self):
        fill(self.sim.journal(CORE), "2026-10-19T12:00:00.000", 30, 1000, "core")
        arguments = {"sources": ["core"], "start": "2026-10-19T12:00:00Z", "end": "2026-10-19T12:10:00Z",
                     "limit": 10}
        first = await self.read(**arguments)
        cursor = first["next_cursor"]
        payload, signature = cursor.split(".")
        tampered = base64.urlsafe_b64encode(
            base64.urlsafe_b64decode(payload + "==").replace(b'"v":1', b'"v":2')
        ).decode().rstrip("=") + "." + signature
        calls = len(self.sim.calls)
        for bad, code, reason in (
            (tampered, ErrorCode.INVALID_CURSOR, "integrity_or_format_invalid"),
            ("not-a-cursor", ErrorCode.INVALID_CURSOR, "integrity_or_format_invalid"),
        ):
            with self.assertRaises(GovernanceError) as caught:
                await self.read(**arguments, cursor=bad)
            self.assertEqual((caught.exception.code, caught.exception.details["reason"]), (code, reason))
        with self.assertRaises(GovernanceError) as caught:
            await self.read(**dict(arguments, min_level="error"), cursor=cursor)
        self.assertEqual(caught.exception.details["reason"], "query_mismatch")
        with self.assertRaises(GovernanceError) as caught:
            await self.read(**dict(arguments, sources=["core", "addon:self"]), cursor=cursor)
        self.assertEqual(caught.exception.details["reason"], "query_mismatch")
        self.clock[0] += 3601
        with self.assertRaises(GovernanceError) as caught:
            await self.read(**arguments, cursor=cursor)
        self.assertEqual((caught.exception.code, caught.exception.details["reason"]),
                         (ErrorCode.STALE_CURSOR, "cursor_expired"))
        self.assertEqual(len(self.sim.calls), calls)
        # Another process (another key) cannot use it.
        other = IncidentLogService(self.service.transport, codec=CursorCodec("incident_logs", key=b"o" * 32))
        with self.assertRaises(GovernanceError):
            await self.read(service=other, **arguments, cursor=cursor)
        decoded = json.loads(base64.urlsafe_b64decode(payload + "=="))
        self.assertNotIn("core event", json.dumps(decoded))

    async def test_defaulted_end_is_fixed_by_the_cursor(self):
        fill(self.sim.journal(CORE), "2026-10-19T13:00:00.000", 30, 1000, "core")
        arguments = {"sources": ["core"], "start": "2026-10-19T12:59:00Z", "limit": 10}
        pages = await self.read_all(**arguments)
        self.assertEqual({page["window"]["end"] for page in pages}, {"2026-10-19T13:30:00.000Z"})
        self.assertTrue(all(page["window"]["end_defaulted"] for page in pages))
        self.assertEqual(sum(page["returned"] for page in pages), 30)

    # -- unavailability -----------------------------------------------------

    async def test_http_refusals_are_reported_per_source(self):
        fill(self.sim.journal(CORE), "2026-10-19T12:00:00.000", 3, 1000, "core")
        for status, reason in ((401, "authority_refused"), (403, "authority_refused"),
                               (404, "route_or_addon_unavailable"), (502, "supervisor_http_error")):
            with self.subTest(status=status):
                self.sim.status = {SELF: status}
                page = await self.read(sources=["core", "addon:self"], start="2026-10-19T12:00:00Z",
                                       end="2026-10-19T12:10:00Z")
                row = self.source(page, "addon:self")
                self.assertEqual((row["status"], row["reason"], row["http_status"]),
                                 ("unavailable", reason, status))
                self.assertEqual(page["returned"], 3)
                self.assertIn("source_unavailable:addon:self", page["omissions"])
                self.assertNotIn("synthetic refusal", json.dumps(page))

    async def test_all_statically_unavailable_reports_each_source_with_zero_requests(self):
        # Review M5: per-source unavailability naming the required role, never
        # a generic sources_unavailable refusal.
        page = await self.read(sources=["supervisor", "host", "addon:zwave_js"], start="2026-10-19T12:00:00Z",
                               end="2026-10-19T12:10:00Z")
        self.assertEqual(self.sim.calls, [])
        self.assertEqual((page["entries"], page["returned"], page["requests"]), ([], 0, 0))
        self.assertEqual((page["has_more"], page["next_cursor"]), (False, None))
        self.assertEqual((page["complete"], page["completeness"]), (False, "partial"))
        self.assertEqual([row["source"] for row in page["sources"]], ["supervisor", "host", "addon:zwave_js"])
        for row, pattern in zip(page["sources"], (r"^/supervisor/.+$", r"^/host/.+$",
                                                  r"^/addons/[^/]+/(?!security).+$")):
            self.assertEqual(row, {
                "source": row["source"], "status": "unavailable", "reason": "authority_not_granted",
                "detail": c.AUTHORITY_DETAIL, "required_role": "manager",
                "required_route_pattern": pattern, "route": None, "requests": 0,
            })
            self.assertIn(f"source_unavailable:{row['source']}", page["omissions"])

    async def test_stopped_addon_logs_remain_readable_and_wrong_shape_fails(self):
        # A stopped add-on's journal persists; the endpoint still serves it.
        fill(self.sim.journal(SELF), "2026-10-19T12:00:00.000", 2, 1000, "self")
        page = await self.read(sources=["addon:self"], start="2026-10-19T12:00:00Z", end="2026-10-19T12:10:00Z")
        self.assertEqual(page["returned"], 2)
        self.sim.content_type = "text/html"
        page = await self.read(sources=["addon:self"], start="2026-10-19T12:00:00Z", end="2026-10-19T12:10:00Z")
        self.assertEqual(self.source(page, "addon:self")["reason"], "representation_invalid")
        self.assertEqual(page["returned"], 0)

    async def test_anonymous_or_missing_credential_is_refused_before_requests(self):
        with self.assertRaises(c.IncidentLogsError) as caught:
            await self.read(caller="anonymous", sources=["core"], start="2026-10-19T12:00:00Z")
        self.assertEqual(caught.exception.reason, "authority_unavailable")
        with self.assertRaises(c.IncidentLogsError):
            await self.read(service=self.make_service(token=""), sources=["core"], start="2026-10-19T12:00:00Z")
        self.assertEqual(self.sim.calls, [])

    async def test_slug_traversal_is_refused_with_zero_requests(self):
        for token in ("addon:../core", "addon:..", "addon:.", "addon:a/b", "addon:%2e%2e", "addon:a%2fb",
                      "addon:", "addon:A", "addon:a b", "addon:a?x=1", "addon:a#b", "addon:\\x",
                      "addon:" + "a" * 65, "Core", "addons:x", "addon:self/../../core"):
            # A readable source beside the token: the refusal must come from
            # the slug guard, not from "nothing readable".
            for sources in ([token], ["core", token]):
                with self.subTest(token=token, sources=sources):
                    with self.assertRaises(c.IncidentLogsError) as caught:
                        await self.read(sources=sources, start="2026-10-19T12:00:00Z")
                    self.assertEqual(caught.exception.reason, "invalid_arguments")
        self.assertEqual(self.sim.calls, [])

    # -- secrets ------------------------------------------------------------

    async def test_planted_secrets_never_reach_any_output_field(self):
        bearer = "synthetic-bearer-" + "value-131"
        password = "synthetic-pass" + "word-131"
        webhook = "synthetic-webhook-" + "id-131"
        url_token = "synthetic-url-" + "token-131"
        planted = [
            f"Authorization: Bearer {bearer}",
            f"connecting with password={password}",
            f"POST https://example.invalid/api/webhook/{webhook}",
            f"fetch https://user:{password}@example.invalid/x?access_token={url_token}",
            f"supervisor token {SUPERVISOR_TOKEN}",
            f"token={HA_TOKEN}",
            ACCESS_SECRET,
        ]
        own = self.sim.journal(SELF)
        base = ms("2026-10-19T12:00:00.000")
        for i, text in enumerate(planted):
            own.add(base + i * 10, core_line(text, "ERROR", base + i * 10), identifier="synthetic_addon")
        own.add(base + 100, f"continuation start\napi_key: {bearer}", identifier="synthetic_addon")
        own.add(base + 200, core_line("x" * 1990 + f" password={password}", "ERROR", base + 200),
                identifier="synthetic_addon")
        fill(self.sim.journal(CORE), "2026-10-19T12:00:00.000", 3, 50, "core")
        secrets = (bearer, password, webhook, url_token, SUPERVISOR_TOKEN, HA_TOKEN, ACCESS_SECRET)
        pages = await self.read_all(sources=["core", "addon:self"], start="2026-10-19T12:00:00Z",
                                    end="2026-10-19T12:10:00Z", limit=3)
        for page in pages:
            rendered = json.dumps(page)
            for secret in secrets:
                self.assertNotIn(secret, rendered)
            if page["next_cursor"]:
                body = base64.urlsafe_b64decode(page["next_cursor"].split(".")[0] + "==").decode()
                for secret in secrets:
                    self.assertNotIn(secret, body)
        entries = [e for page in pages for e in page["entries"]]
        self.assertEqual(len(entries), 12)
        planted_rows = [e for e in entries if "core event" not in e["message"]]
        # The long entry is cut at the character bound before its secret.
        self.assertEqual([e.get("message_truncated", False) for e in planted_rows], [False] * 8 + [True])
        self.assertTrue(all("[REDACTED:" in e["message"] for e in planted_rows[:8]))
        self.assertTrue(any("redacted_by_policy" in page["omissions"] for page in pages))
        # The filter only sees sanitized text, so it cannot probe a secret.
        probe = await self.read(sources=["addon:self"], start="2026-10-19T12:00:00Z",
                                end="2026-10-19T12:10:00Z", text=bearer[:30])
        self.assertEqual(probe["returned"], 0)

    async def test_long_message_truncation_keeps_markers_whole(self):
        base = ms("2026-10-19T12:00:00.000")
        text = "y" * 1985 + " password=" + "synthetic-long-" + "secret-131"
        self.sim.journal(CORE).add(base, core_line(text, "INFO", base))
        page = await self.read(sources=["core"], start="2026-10-19T12:00:00Z", end="2026-10-19T12:10:00Z")
        entry = page["entries"][0]
        self.assertTrue(entry["message_truncated"])
        self.assertLessEqual(len(entry["message"]), c.MAX_MESSAGE_CHARS)
        opener = entry["message"].rfind("[REDACTED:")
        self.assertTrue(opener == -1 or "]" in entry["message"][opener:])
        self.assertIn("message_characters_limit", page["omissions"])

    async def test_response_byte_ceiling_shrinks_the_page(self):
        base = ms("2026-10-19T12:00:00.000")
        for i in range(60):
            self.sim.journal(CORE).add(base + i, core_line(f"{i} " + "z" * 1900, "INFO", base + i))
        pages = await self.read_all(sources=["core"], start="2026-10-19T12:00:00Z",
                                    end="2026-10-19T12:10:00Z", limit=60)
        self.assertIn("response_bytes_limit", pages[0]["omissions"])
        self.assertLess(pages[0]["returned"], 60)
        for page in pages:
            self.assertLessEqual(len(json.dumps(page, separators=(",", ":"))), c.RESPONSE_BUDGET)
        self.assertEqual(sum(page["returned"] for page in pages), 60)

    # -- single exit ----------------------------------------------------------

    async def test_sanitizers_run_only_in_the_single_exit(self):
        callers = []
        real_lines, real_data = service_module.sanitize_log_lines, service_module.sanitize_untrusted_data

        strict = []

        def spy(real):
            def wrapper(*args, **kwargs):
                callers.append(inspect.stack()[1].function)
                result = real(*args, **kwargs)
                if real is real_data:
                    strict.append((args[0], result))
                return result
            return wrapper

        fill(self.sim.journal(CORE), "2026-10-19T12:00:00.000", 20, 1000, "core")
        with patch.object(service_module, "sanitize_log_lines", spy(real_lines)), \
                patch.object(service_module, "sanitize_untrusted_data", spy(real_data)):
            page = await self.read(sources=["core", "addon:self", "supervisor"], start="2026-10-19T12:00:00Z",
                                   end="2026-10-19T12:10:00Z", limit=5)
        self.assertTrue(callers)
        self.assertEqual(set(callers), {"_finish"})
        # The strict pass sees only complete envelopes (never a single entry
        # before the merge), and its output is returned unchanged.
        self.assertTrue(strict)
        for argument, _ in strict:
            self.assertEqual(set(argument) & {"entries", "sources", "next_cursor", "omissions"},
                             {"entries", "sources", "next_cursor", "omissions"})
        self.assertIs(page, strict[-1][1].value)
        self.assertEqual(len(page["entries"]), 5)
        self.assertIsNotNone(page["next_cursor"])
        tree = ast.parse((PACKAGE / "incident_logs" / "service.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                if names & {"sanitize_log_lines", "sanitize_untrusted_data"}:
                    self.assertEqual(node.name, "_finish")
        for module in ("transport.py", "journal.py", "contracts.py", "cursor.py"):
            tree = ast.parse((PACKAGE / "incident_logs" / module).read_text(encoding="utf-8"))
            imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                        for alias in node.names}
            self.assertFalse({name for name in imported if "sanitiz" in name}, module)

    async def test_exit_pass_carries_known_secrets_and_its_redaction_makes_partial(self):
        # The line sanitizer already removes exact known secrets, so these two
        # exit properties are pinned directly rather than by planted text.
        real = service_module.sanitize_untrusted_data
        seen = []

        def spy(*args, **kwargs):
            seen.append(tuple(kwargs.get("known_secrets", ())))
            return real(*args, **kwargs)

        fill(self.sim.journal(CORE), "2026-10-19T11:59:59.000", 3, 1000, "core")
        fill(self.sim.journal(CORE), "2026-10-19T12:10:01.000", 1, 1000, "core")
        arguments = {"sources": ["core"], "start": "2026-10-19T12:00:00Z", "end": "2026-10-19T12:10:00Z"}
        with patch.object(service_module, "sanitize_untrusted_data", spy):
            page = await self.read(**arguments)
        self.assertTrue(page["complete"])
        self.assertTrue(seen)
        for secrets in seen:
            self.assertTrue({HA_TOKEN, ACCESS_SECRET, SUPERVISOR_TOKEN} <= set(secrets))

        def redacting(*args, **kwargs):
            return replace(real(*args, **kwargs), redacted_field_count=1)

        with patch.object(service_module, "sanitize_untrusted_data", redacting):
            page = await self.read(**arguments)
        self.assertFalse(page["complete"])
        self.assertIn("redacted_by_policy", page["omissions"])

    # -- static read-only and registration ----------------------------------

    def test_read_only_static_surface(self):
        files = sorted((PACKAGE / "incident_logs").glob("*.py")) + [PACKAGE / "tools" / "incident_logs.py"]
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    self.assertNotIn(node.attr, {"post", "put", "patch", "delete", "request", "ws_connect",
                                                 "read_text", "read_bytes", "unlink", "write"}, path.name)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"open", "eval", "exec"}, path.name)
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "rest_path":
                    self.assertIn(node.args[0].value, {"/core/logs", "/addons/self/logs"})
        source = (PACKAGE / "incident_logs" / "transport.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("session.get("), 1)
        self.assertIn("allow_redirects=False", source)

    def test_routing_core_gate_and_catalog(self):
        decision = routing_for_tool("get_incident_logs")
        self.assertEqual(decision.preferred_provider, "supervisor_incident_logs")
        self.assertEqual(decision.fallback_providers, ())
        self.assertTrue(incident_logs_policy_allows_read())
        independence = CORE_INDEPENDENT_STATIC_TOOLS["get_incident_logs"]
        self.assertEqual(independence.basis, CORE_INDEPENDENT_SUPERVISOR_ONLY)
        from ha_mcp_engineering.capabilities import BETA_NATIVE_CAPABILITIES
        entry = next(item for item in BETA_NATIVE_CAPABILITIES if item["tool"] == "get_incident_logs")
        self.assertEqual((entry["risk"], entry["fallback"], entry["direct_write_allowed"]), ("read", "none", False))
        from ha_mcp_engineering.tools import registry
        tool = registry._SDK_TOOLS.get("get_incident_logs")
        self.assertTrue(tool.annotations.readOnlyHint)
        self.assertFalse(tool.annotations.destructiveHint)
        self.assertFalse(tool.parameters["additionalProperties"])
        self.assertEqual(tool.parameters["required"], ["sources", "start"])

    def test_policy_refuses_when_routing_drifts(self):
        from ha_mcp_engineering.providers import routing
        drifted = replace(routing_for_tool("get_incident_logs"), fallback_providers=("direct_ha_api",))
        with patch.object(routing, "routing_for_tool", return_value=drifted):
            self.assertFalse(routing.incident_logs_policy_allows_read())


class IncidentLogToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sim = SupervisorSim(SUPERVISOR_TOKEN)
        self.runner, url = await start(self.sim)
        self.addAsyncCleanup(self.runner.cleanup)
        self.settings = Settings(
            ha_url="http://127.0.0.1:9", ha_token=HA_TOKEN, access_secret=ACCESS_SECRET, port=8100,
            audit_path="unused-synthetic-audit.jsonl", rate_limit_per_minute=120, rate_limit_burst=20,
            destructive_services=frozenset(), ha_timeout_seconds=1.0,
        )
        runtime = service_module.IncidentLogsRuntime()
        runtime.service = IncidentLogService(
            JournalTransport(token_source=lambda: SUPERVISOR_TOKEN, timeout_seconds=1.0, base_url=url),
            known_secrets=(HA_TOKEN, ACCESS_SECRET),
        )
        for patcher in (patch.object(tool_module, "INCIDENT_LOGS", runtime),
                        patch.object(tool_module, "SETTINGS", self.settings)):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def call(self, arguments: dict) -> dict:
        telemetry, token = begin_request()
        telemetry.caller_id = "synthetic_authenticated_caller"
        try:
            tool = tool_module.registered_tool()
            return json.loads(await tool.run(arguments))
        finally:
            end_request(token)

    async def test_tool_success_metadata_and_raw_refusal(self):
        recent = service_module.now_ms() - 60_000
        self.sim.journal(CORE).add(recent, core_line("synthetic recent", "INFO", recent))
        start_text = c.format_ms(recent - 1000)
        result = await self.call({"sources": ["core", "supervisor"], "start": start_text})
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["returned"], 1)
        self.assertEqual(result["metadata"]["provider"], "supervisor_incident_logs")
        self.assertFalse(result["metadata"]["fallback_occurred"])
        self.assertEqual(result["metadata"]["completeness"], "partial")
        calls = len(self.sim.calls)
        for bad in ({"sources": ["addon:../core"], "start": start_text},
                    {"sources": ["core"], "start": start_text, "extra": 1},
                    {"sources": ["core"], "start": start_text, "limit": "5"}):
            refused = await self.call(bad)
            self.assertFalse(refused["success"])
            self.assertNotIn("../core", json.dumps(refused))
        self.assertEqual(len(self.sim.calls), calls)

    async def test_policy_drift_refuses_before_any_request(self):
        with patch.object(tool_module, "incident_logs_policy_allows_read", return_value=False):
            result = await self.call({"sources": ["core"], "start": c.format_ms(service_module.now_ms() - 60_000)})
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "provider_prohibited")
        self.assertEqual(self.sim.calls, [])


class RefusingCore:
    """A Core runtime that grants no lease and records every request."""

    def __init__(self):
        self.acquired = []

    def acquire(self, *args, **kwargs):
        self.acquired.append((args, kwargs))
        return None


class IncidentLogGatewayStackTests(unittest.TestCase):
    """Review M4/M5 reproductions through the production MCP stack.

    The production tool object is served by a real FastMCP server behind the
    real authenticated gateway and audit logger; the journal transport fails
    the test if any request is attempted.
    """

    WINDOW = {"start": "2026-10-20T10:00:00+00:00", "end": "2026-10-20T11:00:00+00:00"}

    def call(self, arguments):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            configured = Settings(
                ha_url="http://synthetic-ha.invalid", ha_token=HA_TOKEN, access_secret=ACCESS_SECRET,
                port=8100, audit_path=str(Path(directory) / "audit.jsonl"),
                governance_path=str(Path(directory) / "governance"),
                rate_limit_per_minute=1000, rate_limit_burst=100, destructive_services=frozenset(),
            )
            server = create_mcp_server(configured)
            tools = McpSdkToolRegistry(server)
            tools.replace({**tools.snapshot(), "get_incident_logs": tool_module.registered_tool()})
            inner = server.streamable_http_app()
            core = RefusingCore()
            gateway = AuthenticatedMcpGateway(inner, configured, AuditLogger(configured.audit_path, ACCESS_SECRET),
                                              core_runtime=core)
            transport = JournalTransport(token_source=lambda: SUPERVISOR_TOKEN, timeout_seconds=1.0,
                                         base_url="http://127.0.0.1:9")
            pages = []

            async def page(*args, **kwargs):
                pages.append((args, kwargs))
                raise AssertionError("no journal request is allowed")

            transport.page = page
            runtime = service_module.IncidentLogsRuntime()
            runtime.service = IncidentLogService(transport, known_secrets=(HA_TOKEN, ACCESS_SECRET))
            client = SameThreadAsgiTestClient(gateway, lifespan_app=inner, base_url="http://127.0.0.1:8100")
            with patch.object(tool_module, "INCIDENT_LOGS", runtime), \
                    patch.object(tool_module, "SETTINGS", configured), client:
                response = client.post("/" + ACCESS_SECRET + "/mcp",
                                       headers={"accept": "application/json, text/event-stream"},
                                       json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                             "params": {"name": "get_incident_logs", "arguments": arguments}})
            audit_path = Path(configured.audit_path)
            records = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()] \
                if audit_path.exists() else []
        self.assertEqual(response.status_code, 200)
        self.assertEqual(pages, [])
        self.assertEqual(client.pending_task_count, 0)
        body = json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: ")))
        rendered = json.loads(body["result"]["content"][0]["text"])
        tool_records = [record for record in records if record.get("event") == "tool_call"]
        self.assertEqual(len(tool_records), 1, records)
        return rendered, tool_records[0], response.text

    def test_wrong_typed_filters_are_bounded_audited_refusals(self):
        # Review M4: min_level [] / {} reached ``in LEVEL_RANK`` and escaped
        # as TypeError from both the tool and the gateway's audit builder.
        wrong = [("min_level", []), ("min_level", {}), ("min_level", 1), ("min_level", True),
                 ("min_level", ["error"]), ("text", []), ("text", {}), ("limit", []), ("limit", "5"),
                 ("cursor", {}), ("end", []), ("start", {}), ("sources", {}), ("sources", [[]]),
                 ("sources", [{}]), ("sources", "core")]
        for key, value in wrong:
            with self.subTest(key=key, value=value):
                arguments = {"sources": ["core"], **self.WINDOW, key: value}
                rendered, record, raw = self.call(arguments)
                self.assertFalse(rendered["success"])
                self.assertEqual(rendered["error_code"], "invalid_request")
                self.assertEqual(rendered["metadata"]["provider"], "none")
                self.assertNotIn("TypeError", raw)
                self.assertNotIn("unhashable", raw)
                self.assertEqual((record["tool_name"], record["result_status"], record["error_code"]),
                                 ("get_incident_logs", "failure", "invalid_request"))
                self.assertEqual(record["parameters"]["sources"], None)
                self.assertEqual(record["parameters"]["min_level"], None)

    def test_only_unauthorized_sources_report_authority_not_granted_per_source(self):
        # Review M5: per-source unavailability with the required role, not a
        # generic sources_unavailable refusal; zero journal requests.
        rendered, record, _ = self.call({"sources": ["supervisor", "host", "addon:zwave_js"], **self.WINDOW})
        self.assertTrue(rendered["success"])
        data = rendered["data"]
        self.assertEqual((data["returned"], data["requests"], data["has_more"], data["completeness"]),
                         (0, 0, False, "partial"))
        self.assertEqual([(row["source"], row["status"], row["reason"], row["required_role"], row["requests"])
                          for row in data["sources"]],
                         [(key, "unavailable", "authority_not_granted", "manager", 0)
                          for key in ("supervisor", "host", "addon:zwave_js")])
        self.assertEqual(rendered["metadata"]["completeness"], "partial")
        self.assertEqual(rendered["metadata"]["upstream_calls"], 0)
        self.assertEqual((record["result_status"], record["error_code"]), ("partial", None))
        self.assertEqual(record["analysis_summary"]["source_outcomes"],
                         [{"source": key, "status": "unavailable", "reason": "authority_not_granted"}
                          for key in ("supervisor", "host", "addon:zwave_js")])
        self.assertEqual(record["analysis_summary"]["upstream_calls"], 0)


if __name__ == "__main__":
    unittest.main()
