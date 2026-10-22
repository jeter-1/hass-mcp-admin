"""HAMCP-220: incident-log entry times from the journal realtime, not text-as-UTC.

Offline: loopback peer and synthetic data. Supervisor 2026.10.3 (#7372)
renders the verbose prefix in its own zone without an offset; the pinned
2026.09.1 renders UTC. ``X-First-Cursor`` (the first entry's ``__CURSOR``)
carries that entry's ``__REALTIME_TIMESTAMP`` as ``t=`` (systemd v257.9
``sd-journal.c`` L1252-1258). See ``docs/HAMCP_220_INCIDENT_LOG_TIMESTAMPS.md``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import inspect
from pathlib import Path
import sys
import unittest

from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))

from incident_logs_journal_sim import SupervisorSim, ms, start  # noqa: E402

from ha_mcp_engineering.clients.rest import HomeAssistantRestClient  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.runtime import CoreRuntime  # noqa: E402
from ha_mcp_engineering.incident_logs import contracts as c  # noqa: E402
from ha_mcp_engineering.incident_logs import timebasis as tb  # noqa: E402
from ha_mcp_engineering.incident_logs.cursor import CursorCodec  # noqa: E402
from ha_mcp_engineering.incident_logs.journal import detect_level, parse_page  # noqa: E402
from ha_mcp_engineering.incident_logs.service import IncidentLogService  # noqa: E402
from ha_mcp_engineering.incident_logs.transport import JournalTransport, PageFailure, build_page  # noqa: E402
from ha_mcp_engineering.incident_logs.zone import REQUIREMENTS, HomeAssistantZoneReader  # noqa: E402
from ha_mcp_engineering.errors import GovernanceError  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, current_telemetry, end_request  # noqa: E402


SUPERVISOR_TOKEN = "synthetic-supervisor-" + "credential-220"
HA_TOKEN = "synthetic-ha-" + "credential-220"
CORE = "/core/logs"
SELF = "/addons/self/logs"
CHICAGO = "America/Chicago"
SAME_AS_SUPERVISOR = object()
# A real systemd v257.9 cursor shape (synthetic IDs).
CURSOR_FIELDS = "s=" + "0123456789abcdef" * 2 + ";i=2a;b=" + "feedface" * 4 + ";m=1f4"


def cursor_at(realtime_us: int | None) -> str:
    tail = "" if realtime_us is None else f";t={realtime_us:x}"
    return CURSOR_FIELDS + tail + ";x=9e3779b97f4a"


def line(stamp: str, message: str, process: str = "homeassistant[77]") -> str:
    return f"{stamp} synthetic-host {process}: {message}"


def core_text(message: str, level: str = "INFO") -> str:
    return f"{level} (MainThread) [synthetic.component] {message}"


def iso(value: int) -> str:
    return c.format_ms(value)


class TimeBasisUnitTests(unittest.TestCase):
    """Pure parsing against real formatter output."""

    def zone(self, name: str = CHICAGO) -> tb.Zone:
        return tb.load_zone(name)

    def test_zone_names_are_iana_only_and_never_default_to_utc(self):
        self.assertEqual(self.zone().name, CHICAGO)
        self.assertEqual(self.zone("UTC").name, "UTC")
        for bad in (None, "", " ", ":America/Chicago", "../etc/passwd", "/usr/share/zoneinfo/UTC",
                    "Mars/Olympus_Mons", "CST6CDT,M3.2.0,M11.1.0", "America/Chicago\n", 7,
                    "A" * 65):
            with self.subTest(bad=bad), self.assertRaises(tb.TimeZoneUnavailable):
                tb.load_zone(bad)

    def test_realtime_is_the_cursor_t_field_in_microseconds(self):
        self.assertEqual(tb.realtime_us(cursor_at(0x6431f5a1c0e5d)), 0x6431f5a1c0e5d)
        self.assertIsNone(tb.realtime_us(cursor_at(None)))
        self.assertIsNone(tb.realtime_us(None))
        self.assertIsNone(tb.realtime_us("t=" + "f" * 17))

    def test_fall_back_local_time_keeps_both_readings(self):
        # 2026-11-01 01:30 occurs at -05:00 (CDT) and again at -06:00 (CST).
        values, reason = tb.local_candidates(datetime(2026, 11, 1, 1, 30), self.zone().tz)
        self.assertEqual(reason, tb.AMBIGUOUS)
        self.assertEqual([iso(v) for v in values], ["2026-11-01T06:30:00.000Z", "2026-11-01T07:30:00.000Z"])

    def test_spring_forward_local_time_is_nonexistent(self):
        values, reason = tb.local_candidates(datetime(2026, 3, 8, 2, 30), self.zone().tz)
        self.assertEqual(reason, tb.NONEXISTENT)
        self.assertEqual([iso(v) for v in values], ["2026-03-08T07:30:00.000Z", "2026-03-08T08:30:00.000Z"])

    def test_ordinary_local_time_has_one_reading(self):
        values, reason = tb.local_candidates(datetime(2026, 10, 22, 7, 34, 40, 940000), self.zone().tz)
        self.assertEqual(([iso(v) for v in values], reason), (["2026-10-22T12:34:40.940Z"], None))

    def resolve(self, lines: list[str], realtime_us: int | None, zone: str = CHICAGO):
        body = ("\n".join(lines) + "\n").encode()
        return build_page(body, cursor_at(realtime_us), capped=False, requested=len(lines),
                          zone=self.zone(zone))

    def test_live_format_chicago_is_local_time_not_utc(self):
        # The live 2026-10-22 reproduction: text 07:34:40.940, written 12:34:40.940Z.
        realtime = ms("2026-10-22T12:34:40.940") * 1000 + 230
        page = self.resolve([line("2026-10-22 07:34:40.940", core_text("tail")),
                             line("2026-10-22 07:34:41.230", core_text("next"))], realtime)
        first, second = page.entries
        self.assertEqual((iso(first.ts_ms), first.basis, first.zone),
                         ("2026-10-22T12:34:40.940Z", tb.BASIS_REALTIME, "UTC"))
        self.assertEqual((iso(second.ts_ms), second.basis, second.zone, second.uncertain),
                         ("2026-10-22T12:34:41.230Z", tb.BASIS_TEXT_LOCAL, CHICAGO, None))
        self.assertEqual(page.basis, tb.PageBasis(True, (CHICAGO,)))

    def test_utc_configured_home_assistant(self):
        realtime = ms("2026-10-22T12:34:40.940") * 1000
        page = self.resolve([line("2026-10-22 12:34:40.940", "a"), line("2026-10-22 12:34:42.000", "b")],
                            realtime, zone="UTC")
        self.assertEqual([iso(e.ts_ms) for e in page.entries], ["2026-10-22T12:34:40.940Z", "2026-10-22T12:34:42.000Z"])
        self.assertEqual((page.entries[1].basis, page.entries[1].zone), (tb.BASIS_TEXT_LOCAL, "UTC"))
        self.assertEqual(page.basis.renderings, ("UTC",))

    def test_zone_east_of_utc_with_half_hour_offset(self):
        # Asia/Kolkata is +05:30: 18:04:40.940 local is 12:34:40.940Z.
        realtime = ms("2026-10-22T12:34:40.940") * 1000 + 999
        page = self.resolve([line("2026-10-22 18:04:40.940", "a"), line("2026-10-22 18:05:00.001", "b")],
                            realtime, zone="Asia/Kolkata")
        self.assertEqual([iso(e.ts_ms) for e in page.entries], ["2026-10-22T12:34:40.940Z", "2026-10-22T12:35:00.001Z"])
        self.assertEqual(page.entries[1].zone, "Asia/Kolkata")

    def test_pinned_utc_rendering_is_recognised_by_calibration(self):
        # Supervisor 2026.09.1 renders UTC even though Home Assistant is in Chicago.
        realtime = ms("2026-10-22T12:34:40.940") * 1000
        page = self.resolve([line("2026-10-22 12:34:40.940", "a"), line("2026-10-22 12:34:41.000", "b")], realtime)
        self.assertEqual((iso(page.entries[1].ts_ms), page.entries[1].basis, page.entries[1].zone),
                         ("2026-10-22T12:34:41.000Z", tb.BASIS_TEXT_LOCAL, "UTC"))
        self.assertEqual(page.basis.renderings, ("UTC",))

    def test_no_rendering_matches_the_realtime_fails_closed(self):
        # Rendered in Europe/Berlin (+02:00) but the add-on's zone is Chicago.
        realtime = ms("2026-10-22T12:34:40.940") * 1000
        with self.assertRaises(PageFailure) as caught:
            self.resolve([line("2026-10-22 14:34:40.940", "a")], realtime)
        self.assertEqual(caught.exception.reason, "timestamp_basis_mismatch")

    def test_ambiguous_and_nonexistent_entries_after_a_verified_first_entry(self):
        realtime = ms("2026-11-01T06:00:00.000") * 1000  # 01:00 CDT
        page = self.resolve([line("2026-11-01 01:00:00.000", "first"), line("2026-11-01 01:30:00.000", "fold")],
                            realtime)
        first, fold = page.entries
        self.assertEqual((first.basis, first.uncertain, iso(first.ts_ms)),
                         (tb.BASIS_REALTIME, None, "2026-11-01T06:00:00.000Z"))
        self.assertEqual((fold.basis, fold.uncertain, [iso(v) for v in fold.candidates]),
                         (tb.BASIS_UNCERTAIN, tb.AMBIGUOUS, ["2026-11-01T06:30:00.000Z", "2026-11-01T07:30:00.000Z"]))
        self.assertEqual((iso(fold.ts_ms), iso(fold.ts_hi)), ("2026-11-01T06:30:00.000Z", "2026-11-01T07:30:00.000Z"))
        realtime = ms("2026-03-08T07:59:59.000") * 1000  # 01:59:59 CST
        page = self.resolve([line("2026-03-08 01:59:59.000", "first"), line("2026-03-08 02:30:00.000", "gap")],
                            realtime)
        gap = page.entries[1]
        self.assertEqual((gap.basis, gap.uncertain, [iso(v) for v in gap.candidates]),
                         (tb.BASIS_UNCERTAIN, tb.NONEXISTENT, ["2026-03-08T07:30:00.000Z", "2026-03-08T08:30:00.000Z"]))

    def test_first_entry_in_a_repeated_hour_is_exact_from_realtime(self):
        # 01:30 CST (second occurrence): the text alone is ambiguous.
        realtime = ms("2026-11-01T07:30:00.000") * 1000 + 5
        page = self.resolve([line("2026-11-01 01:30:00.000", "first")], realtime)
        self.assertEqual((iso(page.entries[0].ts_ms), page.entries[0].basis, page.entries[0].uncertain),
                         ("2026-11-01T07:30:00.000Z", tb.BASIS_REALTIME, None))

    def test_without_realtime_text_is_read_in_the_zone_and_unverified(self):
        page = self.resolve([line("2026-10-22 07:34:40.940", "a"), line("2026-10-22 07:34:41.000", "b")], None)
        self.assertEqual([(iso(e.ts_ms), e.basis, e.zone) for e in page.entries],
                         [("2026-10-22T12:34:40.940Z", tb.BASIS_TEXT_LOCAL, CHICAGO),
                          ("2026-10-22T12:34:41.000Z", tb.BASIS_TEXT_LOCAL, CHICAGO)])
        self.assertEqual(page.basis, tb.PageBasis(False, (CHICAGO,)))

    def test_explicit_offset_form(self):
        realtime = ms("2026-10-22T12:34:40.940") * 1000
        page = self.resolve([line("2026-10-22 07:34:40.940-05:00", "a"), line("2026-10-22 07:34:41.500-05:00", "b")],
                            realtime)
        self.assertEqual([(iso(e.ts_ms), e.basis, e.zone) for e in page.entries],
                         [("2026-10-22T12:34:40.940Z", tb.BASIS_REALTIME, "UTC"),
                          ("2026-10-22T12:34:41.500Z", tb.BASIS_TEXT_OFFSET, "-05:00")])
        with self.assertRaises(PageFailure) as caught:
            self.resolve([line("2026-10-22 08:34:40.940-05:00", "a")], realtime)
        self.assertEqual(caught.exception.reason, "timestamp_basis_mismatch")
        with self.assertRaises(PageFailure) as caught:
            self.resolve([line("2026-10-22 07:34:40.940-05:00", "a"), line("2026-10-22 07:34:41.000", "b")],
                         realtime)
        self.assertEqual(caught.exception.reason, "timestamp_form_mixed")

    def test_parsing_never_assigns_a_time(self):
        entries = parse_page(line("2026-10-22 07:34:40.940", "a") + "\n")
        self.assertEqual((entries[0].ts_ms, entries[0].basis), (0, "unresolved"))

    def test_core_level_detected_with_or_without_offset(self):
        for stamp in ("2026-10-22 07:34:40.940", "2026-10-22 07:34:40.940-05:00", "2026-10-22 07:34:40.940+0530"):
            self.assertEqual(detect_level(f"{stamp} ERROR (MainThread) [x] boom"), "error", stamp)


class TimeBasisServiceTests(unittest.IsolatedAsyncioTestCase):
    """End to end over the loopback peer with real-format rendering."""

    async def setup(self, *, render: str = "local", supervisor_zone: str = CHICAGO, addon_zone=CHICAGO,
                    now: str = "2026-10-22T12:34:48.000", ha_zone=SAME_AS_SUPERVISOR):
        # ``ha_zone`` is Core's /api/config time_zone (by default the zone
        # Supervisor renders in); ``addon_zone`` is this container's TZ.
        self.sim = SupervisorSim(SUPERVISOR_TOKEN, render=render, zone=supervisor_zone)
        self.runner, url = await start(self.sim)
        self.addAsyncCleanup(self.runner.cleanup)
        self.zone = [addon_zone]
        self.ha_zone = [supervisor_zone if ha_zone is SAME_AS_SUPERVISOR else ha_zone]
        self.zone_reads = 0
        transport = JournalTransport(token_source=lambda: SUPERVISOR_TOKEN, timeout_seconds=2.0, base_url=url)
        now_ms = ms(now)

        async def zone_source():
            self.zone_reads += 1
            return self.ha_zone[0]

        self.service = IncidentLogService(transport, codec=CursorCodec("incident_logs", key=b"k" * 32),
                                          clock=lambda: now_ms, zone_source=zone_source,
                                          addon_zone_source=lambda: self.zone[0])

    async def read(self, **arguments) -> dict:
        telemetry, token = begin_request()
        telemetry.caller_id = "synthetic_authenticated_caller"
        try:
            return await self.service.read(arguments)
        finally:
            end_request(token)

    async def read_all(self, **arguments) -> list[dict]:
        pages = [await self.read(**arguments)]
        while pages[-1]["next_cursor"]:
            self.assertLess(len(pages), 50)
            pages.append(await self.read(**arguments, cursor=pages[-1]["next_cursor"]))
        return pages

    def add(self, path: str, at: str, count: int, step_ms: int, label: str, **kwargs) -> list:
        base = ms(at)
        return [self.sim.journal(path).add(base + i * step_ms, core_text(f"{label} {i}"), **kwargs)
                for i in range(count)]

    @staticmethod
    def source(page: dict, key: str) -> dict:
        return next(row for row in page["sources"] if row["source"] == key)

    @staticmethod
    def labels(pages: list[dict]) -> list[str]:
        return [entry["source"] + "|" + entry["message"].rsplit("] ", 1)[1]
                for page in pages for entry in page["entries"]]

    async def test_live_reproduction_recent_window_returns_its_entries(self):
        # 2026-10-22: Core and addon:self wrote through 12:34:41Z; the text says
        # 07:34 (America/Chicago). Read at 12:34:48Z, the 11:30Z-12:34Z window
        # must hold those entries, not report the window after the tail.
        await self.setup()
        for path in (CORE, SELF):  # Retained history before the window.
            self.sim.journal(path).add(ms("2026-10-22T11:00:00.000"), core_text("earlier"))
        core = self.add(CORE, "2026-10-22T11:30:00.000", 130, 30_000, "core")
        self.sim.journal(CORE).add(ms("2026-10-22T12:34:40.940"), core_text("core tail"))
        self.add(SELF, "2026-10-22T11:30:05.000", 30, 120_000, "self")
        self.sim.journal(SELF).add(ms("2026-10-22T12:34:41.230"), core_text("self tail"))
        self.assertTrue(self.sim.journal(CORE).entries[-1].text("local", CHICAGO).startswith("2026-10-22 07:34:40.940 "))
        pages = await self.read_all(sources=["core", "addon:self"], start="2026-10-22T11:30:00Z",
                                    end="2026-10-22T12:34:48Z", limit=50)
        labels = self.labels(pages)
        self.assertEqual(len(labels), 131 + 31)
        self.assertEqual(labels[-2:], ["core|core tail", "addon:self|self tail"])
        first = pages[0]
        for key, latest in (("core", "2026-10-22T12:34:40.940Z"), ("addon:self", "2026-10-22T12:34:41.230Z")):
            row = self.source(first, key)
            self.assertEqual(row["latest_available"], latest)
            self.assertNotEqual(row["reason"], "window_after_tail")
            self.assertEqual(row["timestamp_basis"]["text_rendering"], [CHICAGO])
            self.assertEqual(row["timestamp_basis"]["unverified_pages"], 0)
        self.assertTrue(all(page["complete"] or page["has_more"] for page in pages))
        self.assertTrue(pages[-1]["complete"])
        stamps = [entry["ts"] for page in pages for entry in page["entries"]]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(stamps[0], "2026-10-22T11:30:00.000Z")
        self.assertEqual(first["time_basis"], {
            "preferred": "journal_realtime_utc", "time_zone": CHICAGO,
            "time_zone_source": c.TIME_ZONE_SOURCE, "time_zone_status": "loaded", "time_zone_reads": 1,
            "addon_time_zone": CHICAGO, "addon_time_zone_source": c.ADDON_TIME_ZONE_SOURCE,
            "addon_time_zone_status": "agrees", "uncertain_entries": 0, "ordering_uncertain_entries": 0,
        })
        # Home Assistant's zone is read from Core once per call.
        self.assertEqual(self.zone_reads, len(pages))
        bases = {entry["ts_basis"] for page in pages for entry in page["entries"]}
        # Each page's first entry is its already-read anchor (exact from the
        # cursor realtime, which calibrated the page); returned rows are text.
        self.assertIn("text_local_tz", bases)
        self.assertLessEqual(bases, {"journal_realtime_utc", "text_local_tz"})
        for entry in (e for page in pages for e in page["entries"]):
            self.assertEqual(entry["time_zone"], "UTC" if entry["ts_basis"] == "journal_realtime_utc" else CHICAGO)
        self.assertEqual(core[0].ts_ms, ms("2026-10-22T11:30:00.000"))
        # The window shifted by the zone offset (the old workaround) is empty.
        shifted = await self.read(sources=["core"], start="2026-10-22T06:30:00Z", end="2026-10-22T07:34:48Z")
        self.assertEqual(shifted["returned"], 0)
        self.assertEqual(self.source(shifted, "core")["earliest_available"], "2026-10-22T11:00:00.000Z")

    async def test_utc_configured_home_assistant_end_to_end(self):
        await self.setup(supervisor_zone="UTC", addon_zone="UTC")
        self.add(CORE, "2026-10-22T12:00:00.000", 20, 1000, "core")
        page = await self.read(sources=["core"], start="2026-10-22T12:00:05Z", end="2026-10-22T12:00:10Z")
        self.assertEqual([e["ts"] for e in page["entries"]],
                         [f"2026-10-22T12:00:{s:02d}.000Z" for s in range(5, 10)])
        self.assertTrue(page["complete"])
        self.assertEqual({e["time_zone"] for e in page["entries"]}, {"UTC"})

    async def test_east_of_utc_end_to_end(self):
        await self.setup(supervisor_zone="Asia/Tokyo", addon_zone="Asia/Tokyo")
        self.add(CORE, "2026-10-22T12:00:00.000", 20, 1000, "core")
        page = await self.read(sources=["core"], start="2026-10-22T21:00:05+09:00", end="2026-10-22T12:00:10Z")
        self.assertEqual([e["ts"] for e in page["entries"]],
                         [f"2026-10-22T12:00:{s:02d}.000Z" for s in range(5, 10)])
        self.assertEqual({e["ts_basis"] for e in page["entries"]}, {"text_local_tz"})
        self.assertEqual({e["time_zone"] for e in page["entries"]}, {"Asia/Tokyo"})
        self.assertTrue(page["complete"])

    async def test_window_edges_are_utc_instants_not_local_wall_time(self):
        await self.setup(now="2026-10-22T14:00:00.000")
        self.sim.journal(CORE).add(ms("2026-10-22T06:30:00.000"), core_text("earlier"))
        for stamp, label in (("11:59:59.999", "before"), ("12:00:00.000", "first"),
                             ("12:59:59.999", "last"), ("13:00:00.000", "after")):
            self.sim.journal(CORE).add(ms("2026-10-22T" + stamp), core_text(label))
        for window in (("2026-10-22T12:00:00Z", "2026-10-22T13:00:00Z"),
                       ("2026-10-22T07:00:00-05:00", "2026-10-22T08:00:00-05:00")):
            page = await self.read(sources=["core"], start=window[0], end=window[1])
            self.assertEqual(self.labels([page]), ["core|first", "core|last"], window)
            self.assertTrue(page["complete"])
        # Local wall time misread as UTC selects nothing.
        page = await self.read(sources=["core"], start="2026-10-22T07:00:00Z", end="2026-10-22T08:00:00Z")
        self.assertEqual(page["returned"], 0)

    async def test_ordering_across_core_and_addon_self(self):
        await self.setup()
        self.add(CORE, "2026-10-22T12:00:00.000", 40, 1500, "core")
        self.add(SELF, "2026-10-22T12:00:00.000", 40, 1000, "self")
        pages = await self.read_all(sources=["core", "addon:self"], start="2026-10-22T12:00:00Z",
                                    end="2026-10-22T12:01:00Z", limit=7)
        rows = [(e["ts"], e["source"]) for page in pages for e in page["entries"]]
        expected = sorted(
            [(iso(ms("2026-10-22T12:00:00.000") + i * 1500), "core") for i in range(40)]
            + [(iso(ms("2026-10-22T12:00:00.000") + i * 1000), "addon:self") for i in range(40)],
            key=lambda row: (row[0], 0 if row[1] == "core" else 1))
        self.assertEqual(rows, expected)
        self.assertTrue(pages[-1]["complete"])

    async def test_fall_back_hour_entries_are_uncertain_with_both_readings(self):
        # 2026-11-01 05:45Z-08:15Z: local 00:45 CDT .. 01:59 CDT, 01:00 CST .. 02:15 CST.
        # No realtime in the cursor, so no entry can be resolved.
        await self.setup(now="2026-11-01T09:00:00.000")
        self.sim.cursor_realtime = False
        self.sim.journal(CORE).add(ms("2026-11-01T05:00:00.000"), core_text("earlier"))
        self.add(CORE, "2026-11-01T05:45:00.000", 31, 300_000, "core")
        page = await self.read(sources=["core"], start="2026-11-01T05:30:00Z", end="2026-11-01T08:30:00Z")
        self.assertFalse(page["complete"])
        self.assertIn("ordering_uncertain:core", page["omissions"])
        self.assertIn("timestamp_basis_unverified:core", page["omissions"])
        by_label = {e["message"].rsplit("] ", 1)[1]: e for e in page["entries"]}
        self.assertEqual(len(by_label), 31)
        fold = by_label["core 9"]  # 06:30Z, local 01:30 CDT
        self.assertEqual((fold["ts"], fold["ts_basis"], fold["time_zone"]), (None, "uncertain", CHICAGO))
        self.assertEqual(fold["timestamp_uncertain"], {
            "reason": "ambiguous_local_time",
            "candidates": ["2026-11-01T06:30:00.000Z", "2026-11-01T07:30:00.000Z"]})
        self.assertNotIn("window_membership_uncertain", fold)
        repeat = by_label["core 21"]  # 07:30Z, local 01:30 CST
        self.assertEqual(repeat["timestamp_uncertain"]["candidates"], fold["timestamp_uncertain"]["candidates"])
        certain = by_label["core 3"]  # 06:00Z is 01:00 CDT, also ambiguous; 05:55Z is not
        self.assertIsNone(certain["ts"])
        self.assertEqual(by_label["core 2"]["ts"], "2026-11-01T05:55:00.000Z")
        self.assertEqual(by_label["core 27"]["ts"], "2026-11-01T08:00:00.000Z")
        uncertain = [label for label, e in by_label.items() if e["ts"] is None]
        self.assertEqual(sorted(uncertain, key=lambda s: int(s.split()[1])), [f"core {i}" for i in range(3, 27)])
        self.assertEqual(page["time_basis"]["uncertain_entries"], 24)
        # Journal order: uncertain rows, and every row up to the latest
        # reading of one, are ordering_uncertain.
        self.assertEqual([e["message"].rsplit("] ", 1)[1] for e in page["entries"]],
                         [f"core {i}" for i in range(31)])
        self.assertEqual([i for i in range(31) if page["entries"][i].get("ordering_uncertain")],
                         list(range(3, 27)))
        self.assertEqual(page["time_basis"]["ordering_uncertain_entries"], 24)
        self.assertEqual(self.source(page, "core")["ordering_uncertain"], 24)
        self.assertEqual(self.source(page, "core")["timestamp_basis"]["unresolved_entries"], 24)
        # The pinned UTC rendering of the same hour is exact throughout.
        await self.setup(render="utc", now="2026-11-01T09:00:00.000")
        self.sim.journal(CORE).add(ms("2026-11-01T05:00:00.000"), core_text("earlier"))
        self.add(CORE, "2026-11-01T05:45:00.000", 31, 300_000, "core")
        page = await self.read(sources=["core"], start="2026-11-01T05:30:00Z", end="2026-11-01T08:30:00Z")
        self.assertEqual(page["time_basis"]["uncertain_entries"], 0)
        self.assertTrue(page["complete"])

    async def test_uncertain_entry_at_a_window_edge_makes_the_read_partial(self):
        await self.setup(now="2026-11-01T09:00:00.000")
        self.sim.cursor_realtime = False
        self.add(CORE, "2026-11-01T05:45:00.000", 31, 300_000, "core")
        # Starts at 07:00Z (01:00 CST): 06:00Z-06:55Z (01:xx CDT) may be inside.
        page = await self.read(sources=["core"], start="2026-11-01T07:00:00Z", end="2026-11-01T08:30:00Z")
        self.assertFalse(page["complete"])
        self.assertIn("timestamp_uncertain_at_window_edge:core", page["omissions"])
        edge = [e for e in page["entries"] if e.get("window_membership_uncertain")]
        self.assertTrue(edge)
        for entry in edge:
            candidates = entry["timestamp_uncertain"]["candidates"]
            self.assertTrue(candidates[0] < "2026-11-01T07:00:00.000Z" <= candidates[1], candidates)
        self.assertGreater(self.source(page, "core")["timestamp_uncertain_at_window_edge"], 0)
        labels = [e["message"].rsplit("] ", 1)[1] for e in page["entries"]]
        # Certainly-before entries are excluded; every possibly-inside one is kept.
        self.assertNotIn("core 2", labels)
        self.assertIn("core 9", labels)
        self.assertIn("core 30", labels)
        # A window clear of the repeated hour is complete once verified.
        self.sim.cursor_realtime = True
        page = await self.read(sources=["core"], start="2026-11-01T08:00:00Z", end="2026-11-01T08:30:00Z")
        self.assertTrue(page["complete"])

    async def test_window_after_tail_only_when_every_reading_is_before_start(self):
        await self.setup(now="2026-11-01T09:00:00.000")
        self.sim.cursor_realtime = False
        self.add(CORE, "2026-11-01T06:00:00.000", 2, 1_800_000, "core")  # 01:00 and 01:30 CDT
        page = await self.read(sources=["core"], start="2026-11-01T07:15:00Z", end="2026-11-01T08:00:00Z")
        row = self.source(page, "core")
        # The tail (01:30 local) may be 07:30Z: it is read, not "after tail".
        self.assertNotEqual(row["reason"], "window_after_tail")
        self.assertIsNone(row["latest_available"])
        self.assertEqual(row["timestamp_uncertain_bounds"]["latest_available"],
                         ["2026-11-01T06:30:00.000Z", "2026-11-01T07:30:00.000Z"])
        self.assertIn("timestamp_uncertain_at_window_edge:core", page["omissions"])
        self.assertEqual(self.labels([page]), ["core|core 1"])
        self.assertFalse(page["complete"])

    async def test_an_uncertain_tail_is_resolved_before_window_after_tail(self):
        await self.setup(now="2026-11-01T09:00:00.000")
        self.add(CORE, "2026-11-01T06:00:00.000", 2, 1_800_000, "core")  # 01:00 and 01:30 CDT
        page = await self.read(sources=["core"], start="2026-11-01T07:15:00Z", end="2026-11-01T08:00:00Z")
        row = self.source(page, "core")
        # The single-entry read gives 06:30Z exactly: every entry is before.
        self.assertEqual((row["reason"], row["latest_available"], page["returned"], page["requests"]),
                         ("window_after_tail", "2026-11-01T06:30:00.000Z", 0, 2))
        self.assertNotIn("timestamp_uncertain_bounds", row)
        self.assertEqual(row["timestamp_basis"]["resolved_entries"], 1)
        self.assertTrue(page["complete"])
        self.assertEqual(self.sim.calls[1][2]["Range"].split(":")[1:], ["1", "1"])
        # The same tail at 07:30Z (01:30 CST) is inside and returned exact.
        await self.setup(now="2026-11-01T09:00:00.000")
        self.sim.journal(CORE).add(ms("2026-11-01T06:00:00.000"), core_text("core 0"))
        self.sim.journal(CORE).add(ms("2026-11-01T07:30:00.000"), core_text("core 1"))
        page = await self.read(sources=["core"], start="2026-11-01T07:15:00Z", end="2026-11-01T08:00:00Z")
        self.assertEqual([(e["ts"], e["ts_basis"]) for e in page["entries"]],
                         [("2026-11-01T07:30:00.000Z", "journal_realtime_utc")])
        self.assertEqual(self.source(page, "core")["latest_available"], "2026-11-01T07:30:00.000Z")
        self.assertTrue(page["complete"], page["omissions"])

    async def test_spring_forward_nonexistent_time_without_realtime(self):
        # No t= in the cursor (fallback): text is read in the zone only. The
        # pinned UTC text "02:30" on 2026-03-08 does not exist in Chicago.
        await self.setup(render="utc", now="2026-03-08T12:00:00.000")
        self.sim.cursor_realtime = False
        self.add(CORE, "2026-03-08T02:00:00.000", 4, 900_000, "core")
        page = await self.read(sources=["core"], start="2026-03-08T03:00:00Z", end="2026-03-08T09:00:00Z")
        self.assertFalse(page["complete"])
        self.assertIn("timestamp_basis_unverified:core", page["omissions"])
        nonexistent = [e for e in page["entries"] if e["ts"] is None]
        self.assertTrue(nonexistent)
        for entry in nonexistent:
            self.assertEqual(entry["timestamp_uncertain"]["reason"], "nonexistent_local_time")
            self.assertEqual(len(entry["timestamp_uncertain"]["candidates"]), 2)

    async def test_without_realtime_the_read_is_partial_never_silently_complete(self):
        await self.setup()
        self.sim.cursor_realtime = False
        self.add(CORE, "2026-10-22T12:00:00.000", 10, 1000, "core")
        page = await self.read(sources=["core"], start="2026-10-22T12:00:00Z", end="2026-10-22T12:00:10Z")
        self.assertEqual(page["returned"], 10)
        self.assertEqual(page["entries"][0]["ts"], "2026-10-22T12:00:00.000Z")
        self.assertEqual({e["ts_basis"] for e in page["entries"]}, {"text_local_tz"})
        self.assertFalse(page["complete"])
        self.assertIn("timestamp_basis_unverified:core", page["omissions"])
        self.assertGreater(self.source(page, "core")["timestamp_basis"]["unverified_pages"], 0)

    async def test_time_zone_read_failure_is_partial_with_no_request(self):
        # Core's /api/config could not be read (None) or names no IANA zone;
        # the add-on's TZ (Chicago, agreeing) is never used in its place.
        for zone in (None, "", "Mars/Olympus_Mons", ":America/Chicago", 7):
            with self.subTest(zone=zone):
                await self.setup(ha_zone=zone)
                self.add(CORE, "2026-10-22T12:00:00.000", 10, 1000, "core")
                page = await self.read(sources=["core", "addon:self", "supervisor"],
                                       start="2026-10-22T12:00:00Z", end="2026-10-22T12:00:10Z")
                self.assertEqual(self.sim.calls, [])
                self.assertEqual((page["returned"], page["requests"], page["complete"], page["completeness"]),
                                 (0, 0, False, "partial"))
                self.assertIn("time_zone_unavailable", page["omissions"])
                self.assertEqual(page["time_basis"]["time_zone"], None)
                self.assertEqual(page["time_basis"]["time_zone_status"], "unavailable")
                for key in ("core", "addon:self"):
                    row = self.source(page, key)
                    self.assertEqual((row["status"], row["reason"], row["requests"]),
                                     ("unavailable", "time_zone_unavailable", 0))
                self.assertEqual(self.source(page, "supervisor")["reason"], "authority_not_granted")
                self.assertEqual(page["time_basis"]["addon_time_zone_status"], "not_read")
                self.assertEqual(self.zone_reads, 1)

    async def test_a_raising_zone_source_is_unavailable_never_utc(self):
        await self.setup(supervisor_zone="UTC", addon_zone="UTC")

        async def failing():
            raise TimeoutError("synthetic /api/config timeout")

        self.service.zone_source = failing
        self.add(CORE, "2026-10-22T12:00:00.000", 10, 1000, "core")
        page = await self.read(sources=["core"], start="2026-10-22T12:00:00Z", end="2026-10-22T12:00:10Z")
        self.assertEqual((self.sim.calls, page["returned"], page["complete"]), ([], 0, False))
        self.assertEqual(self.source(page, "core")["reason"], "time_zone_unavailable")
        self.assertIn("time_zone_unavailable", page["omissions"])

    async def test_no_zone_is_read_when_no_journal_source_is_readable(self):
        await self.setup()
        page = await self.read(sources=["supervisor"], start="2026-10-22T12:00:00Z", end="2026-10-22T12:00:10Z")
        self.assertEqual((self.zone_reads, self.sim.calls), (0, []))
        self.assertEqual(page["time_basis"]["time_zone_status"], "not_read")

    async def test_time_zone_lost_mid_continuation_fails_the_source(self):
        await self.setup()
        self.add(CORE, "2026-10-22T12:00:00.000", 30, 1000, "core")
        arguments = {"sources": ["core"], "start": "2026-10-22T12:00:00Z", "end": "2026-10-22T12:01:00Z",
                     "limit": 10}
        first = await self.read(**arguments)
        self.assertEqual(first["returned"], 10)
        calls = len(self.sim.calls)
        self.ha_zone[0] = None
        second = await self.read(**arguments, cursor=first["next_cursor"])
        self.assertEqual(len(self.sim.calls), calls)
        row = self.source(second, "core")
        self.assertEqual((row["reason"], second["returned"], second["complete"]),
                         ("time_zone_unavailable", 0, False))
        self.assertEqual(row["gaps"][-1]["reason"], "unread_after_failure")

    async def test_supervisor_zone_differs_from_the_addon_zone_fails_closed(self):
        # Home Assistant moved to Europe/Berlin; this add-on still has Chicago.
        await self.setup(supervisor_zone="Europe/Berlin")
        self.add(CORE, "2026-10-22T12:00:00.000", 10, 1000, "core")
        page = await self.read(sources=["core", "addon:self"], start="2026-10-22T12:00:00Z",
                               end="2026-10-22T12:00:10Z")
        self.assertEqual(self.sim.calls, [])
        for key in ("core", "addon:self"):
            row = self.source(page, key)
            self.assertEqual((row["status"], row["reason"], row["requests"]),
                             ("unavailable", "timestamp_basis_mismatch", 0))
            self.assertEqual(row["zone_mismatch"], {
                "reason": "addon_time_zone_offset_differs", "home_assistant_time_zone": "Europe/Berlin",
                "addon_time_zone": CHICAGO, "offsets_first_differ_at": "2026-10-22T12:00:00.000Z"})
            self.assertIn(f"zone_mismatch:{key}", page["omissions"])
        self.assertEqual((page["returned"], page["complete"], page["completeness"]), (0, False, "partial"))
        self.assertEqual((page["time_basis"]["time_zone"], page["time_basis"]["addon_time_zone_status"]),
                         ("Europe/Berlin", "differs"))

    async def test_explicit_offset_rendering_end_to_end(self):
        await self.setup(render="offset")
        self.add(CORE, "2026-10-22T12:00:00.000", 10, 1000, "core")
        page = await self.read(sources=["core"], start="2026-10-22T12:00:03Z", end="2026-10-22T12:00:06Z")
        self.assertEqual([e["ts"] for e in page["entries"]],
                         ["2026-10-22T12:00:03.000Z", "2026-10-22T12:00:04.000Z", "2026-10-22T12:00:05.000Z"])
        self.assertEqual({e["time_zone"] for e in page["entries"]}, {"-05:00"})
        self.assertTrue(page["complete"])


    # Review 1 H1: Home Assistant's zone (Core /api/config) and the add-on TZ.

    async def test_home_assistant_zone_is_used_when_the_addon_tz_is_stale(self):
        # Review 1 H1 reproduction shape: Home Assistant and Supervisor are in
        # Panama, the add-on container still has Chicago. In October their
        # offsets agree (-05:00), so the window reads and is exact.
        await self.setup(supervisor_zone="America/Panama", addon_zone=CHICAGO, now="2026-10-22T13:00:00.000")
        self.sim.journal(CORE).add(ms("2026-10-22T11:00:00.000"), core_text("older"))
        self.add(CORE, "2026-10-22T12:00:00.000", 5, 1000, "core")
        page = await self.read(sources=["core"], start="2026-10-22T12:00:00Z", end="2026-10-22T12:01:00Z")
        self.assertEqual([e["ts"] for e in page["entries"]],
                         [f"2026-10-22T12:00:0{i}.000Z" for i in range(5)])
        self.assertEqual({e["time_zone"] for e in page["entries"] if e["ts_basis"] == "text_local_tz"},
                         {"America/Panama"})
        self.assertEqual((page["time_basis"]["time_zone"], page["time_basis"]["addon_time_zone_status"]),
                         ("America/Panama", "same_offsets_in_window"))
        self.assertTrue(page["complete"], page["omissions"])
        # Across Chicago's fall-back the offsets differ: refused, not read.
        await self.setup(supervisor_zone="America/Panama", addon_zone=CHICAGO, now="2026-11-01T10:00:00.000")
        self.sim.journal(CORE).add(ms("2026-11-01T08:10:00.000"), core_text("inside"))
        page = await self.read(sources=["core"], start="2026-11-01T06:00:00Z", end="2026-11-01T08:30:00Z")
        self.assertEqual((self.sim.calls, page["returned"], page["complete"]), ([], 0, False))
        self.assertEqual(self.source(page, "core")["zone_mismatch"]["offsets_first_differ_at"],
                         "2026-11-01T07:00:00.000Z")

    async def test_zones_that_agree_except_across_a_dst_transition(self):
        # America/Phoenix is -07:00 all year; America/Denver is -07:00 in
        # winter and -06:00 in summer (spring forward 2027-03-14T09:00Z).
        await self.setup(supervisor_zone="America/Denver", addon_zone="America/Phoenix",
                         now="2027-03-14T12:00:00.000")
        self.sim.journal(CORE).add(ms("2027-03-14T07:00:00.000"), core_text("older"))
        self.add(CORE, "2027-03-14T08:00:00.000", 6, 600_000, "winter")  # 08:00Z-08:50Z
        self.add(CORE, "2027-03-14T09:00:00.000", 3, 600_000, "summer")
        winter = await self.read(sources=["core"], start="2027-03-14T08:00:00Z", end="2027-03-14T09:00:00Z")
        self.assertEqual(self.labels([winter]), [f"core|winter {i}" for i in range(6)])
        self.assertEqual(winter["time_basis"]["addon_time_zone_status"], "same_offsets_in_window")
        self.assertTrue(winter["complete"], winter["omissions"])
        calls = len(self.sim.calls)
        across = await self.read(sources=["core"], start="2027-03-14T08:00:00Z",
                                 end="2027-03-14T09:00:00.001Z")
        self.assertEqual(len(self.sim.calls), calls)
        row = self.source(across, "core")
        self.assertEqual((row["reason"], across["returned"], across["complete"]),
                         ("timestamp_basis_mismatch", 0, False))
        self.assertEqual(row["zone_mismatch"], {
            "reason": "addon_time_zone_offset_differs", "home_assistant_time_zone": "America/Denver",
            "addon_time_zone": "America/Phoenix", "offsets_first_differ_at": "2027-03-14T09:00:00.000Z"})
        self.assertIn("zone_mismatch:core", across["omissions"])

    async def test_home_assistant_zone_change_at_runtime(self):
        await self.setup()
        self.add(CORE, "2026-10-22T12:00:00.000", 30, 1000, "core")
        arguments = {"sources": ["core"], "start": "2026-10-22T12:00:00Z", "end": "2026-10-22T12:01:00Z",
                     "limit": 10}
        first = await self.read(**arguments)
        self.assertEqual((first["returned"], first["time_basis"]["time_zone"]), (10, CHICAGO))
        # Home Assistant moves to Berlin; Supervisor follows; the add-on's TZ
        # is unchanged until it restarts. The continuation reads nothing.
        self.ha_zone[0], self.sim.zone = "Europe/Berlin", "Europe/Berlin"
        calls = len(self.sim.calls)
        second = await self.read(**arguments, cursor=first["next_cursor"])
        self.assertEqual(len(self.sim.calls), calls)
        row = self.source(second, "core")
        self.assertEqual((row["reason"], second["returned"], second["complete"]),
                         ("timestamp_basis_mismatch", 0, False))
        self.assertEqual(row["zone_mismatch"]["reason"], "addon_time_zone_offset_differs")
        self.assertEqual(row["gaps"][-1]["reason"], "unread_after_failure")
        # After the add-on restarts with Berlin, the old cursor's anchor text
        # (rendered in Chicago) no longer matches: restart pagination.
        self.zone[0] = "Europe/Berlin"
        with self.assertRaises(GovernanceError) as caught:
            await self.read(**arguments, cursor=first["next_cursor"])
        self.assertEqual(caught.exception.details["reason"], "journal_continuity_broken")
        fresh = await self.read_all(**arguments)
        self.assertEqual([e["ts"] for p in fresh for e in p["entries"]],
                         [f"2026-10-22T12:00:{i:02d}.000Z" for i in range(30)])
        self.assertEqual({e["time_zone"] for p in fresh for e in p["entries"] if e["ts_basis"] == "text_local_tz"},
                         {"Europe/Berlin"})
        self.assertTrue(fresh[-1]["complete"], fresh[-1]["omissions"])
        # Before Supervisor re-renders, Core's new zone disagrees with the
        # text: the first-entry realtime check refuses the source.
        self.sim.zone = CHICAGO
        stale = await self.read(**arguments)
        row = self.source(stale, "core")
        self.assertEqual((row["reason"], stale["returned"], stale["complete"]),
                         ("timestamp_basis_mismatch", 0, False))
        self.assertEqual(row["zone_mismatch"], {
            "reason": "first_entry_realtime_disagrees", "home_assistant_time_zone": "Europe/Berlin",
            "addon_time_zone": "Europe/Berlin"})

    async def test_rendering_one_hour_from_home_assistants_zone_is_refused(self):
        # Home Assistant and the add-on agree on Chicago; Supervisor renders
        # New York (one hour east). Only the first-entry check can see it.
        await self.setup(supervisor_zone="America/New_York", ha_zone=CHICAGO, addon_zone=CHICAGO)
        self.add(CORE, "2026-10-22T12:00:00.000", 4, 1000, "row")
        page = await self.read(sources=["core"], start="2026-10-22T12:00:00Z", end="2026-10-22T12:01:00Z")
        row = self.source(page, "core")
        self.assertEqual((row["reason"], page["returned"], page["complete"]),
                         ("timestamp_basis_mismatch", 0, False))
        self.assertEqual(row["zone_mismatch"]["reason"], "first_entry_realtime_disagrees")
        self.assertEqual(page["time_basis"]["addon_time_zone_status"], "agrees")
        self.assertIn("zone_mismatch:core", page["omissions"])

    async def test_missing_addon_tz_is_disclosed_and_partial(self):
        for addon in (None, "", "Mars/Olympus_Mons"):
            with self.subTest(addon=addon):
                await self.setup(addon_zone=addon)
                self.add(CORE, "2026-10-22T12:00:00.000", 5, 1000, "core")
                page = await self.read(sources=["core"], start="2026-10-22T12:00:00Z",
                                       end="2026-10-22T12:00:10Z")
                # Read in Home Assistant's zone and confirmed by the realtime,
                # but the cross-check could not run.
                self.assertEqual(page["returned"], 5)
                self.assertEqual(page["time_basis"]["addon_time_zone_status"], "unavailable")
                self.assertIn("addon_time_zone_unavailable", page["omissions"])
                self.assertFalse(page["complete"])

    # Review 1 M1: entries around an unresolved local time keep journal order.

    async def test_fold_entries_are_resolved_exactly_within_the_budget(self):
        await self.setup(now="2026-11-01T09:00:00.000")
        for path in (CORE, SELF):
            self.sim.journal(path).add(ms("2026-11-01T05:00:00.000"), core_text("older"))
        for at in ("06:10", "06:50", "07:10", "07:50"):
            self.sim.journal(CORE).add(ms(f"2026-11-01T{at}:00.000"), core_text(f"core {at}"))
        self.sim.journal(SELF).add(ms("2026-11-01T06:30:00.000"), core_text("self 06:30"))
        self.sim.journal(SELF).add(ms("2026-11-01T07:30:00.000"), core_text("self 07:30"))
        pages = await self.read_all(sources=["core", "addon:self"], start="2026-11-01T05:30:00Z",
                                    end="2026-11-01T08:30:00Z", limit=2)
        self.assertEqual([e["message"].rsplit("] ", 1)[1] for p in pages for e in p["entries"]],
                         ["core 06:10", "self 06:30", "core 06:50", "core 07:10", "self 07:30", "core 07:50"])
        self.assertEqual({e["ts_basis"] for p in pages for e in p["entries"]}, {"journal_realtime_utc"})
        self.assertFalse(any(e.get("ordering_uncertain") for p in pages for e in p["entries"]))
        self.assertTrue(pages[-1]["complete"], pages[-1]["omissions"])
        self.assertTrue(all(p["complete"] or p["has_more"] for p in pages))
        resolved = sum(self.source(p, key)["timestamp_basis"]["resolved_entries"]
                       for p in pages for key in ("core", "addon:self"))
        self.assertGreaterEqual(resolved, 6)

    async def test_fold_beyond_the_budget_keeps_journal_order_and_is_partial(self):
        await self.setup(now="2026-11-01T09:00:00.000")
        self.sim.journal(CORE).add(ms("2026-11-01T05:00:00.000"), core_text("earlier"))
        self.add(CORE, "2026-11-01T05:45:00.000", 31, 300_000, "core")
        page = await self.read(sources=["core"], start="2026-11-01T05:30:00Z", end="2026-11-01T08:30:00Z")
        labels = [e["message"].rsplit("] ", 1)[1] for e in page["entries"]]
        self.assertEqual(labels, [f"core {i}" for i in range(31)])
        self.assertLessEqual(page["requests"], c.MAX_REQUESTS_PER_SOURCE)
        basis = self.source(page, "core")["timestamp_basis"]
        self.assertEqual(basis["resolved_entries"] + basis["unresolved_entries"], 24)
        self.assertGreater(basis["resolved_entries"], 0)
        self.assertGreater(basis["unresolved_entries"], 0)
        for entry in page["entries"]:
            if entry["ts"] is None:
                self.assertTrue(entry["ordering_uncertain"], entry)
            elif entry["ts_basis"] == "journal_realtime_utc" and entry["ts"] < "2026-11-01T07:00:00.000Z":
                self.assertNotIn("ordering_uncertain", entry)
        self.assertEqual(page["time_basis"]["ordering_uncertain_entries"], basis["unresolved_entries"])
        self.assertFalse(page["complete"])
        self.assertIn("ordering_uncertain:core", page["omissions"])

    def assert_never_misordered(self, pages: list[dict]) -> None:
        """Across pages, a row not marked ordering_uncertain is after every earlier reading."""

        bound = None
        for entry in (e for page in pages for e in page["entries"]):
            uncertain = entry.get("timestamp_uncertain")
            latest = uncertain["candidates"][-1] if uncertain else entry["ts"]
            if not entry.get("ordering_uncertain"):
                self.assertIsNotNone(entry["ts"], entry)
                if bound is not None:
                    self.assertGreater(entry["ts"], bound, entry)
            bound = latest if bound is None else max(bound, latest)

    async def test_multi_page_fall_back_with_two_sources(self):
        # Review 1 M1: no realtime, so nothing is resolved. Both journals
        # write through the repeated hour; pages of 3.
        await self.setup(now="2026-11-01T09:00:00.000")
        self.sim.cursor_realtime = False
        for path in (CORE, SELF):
            self.sim.journal(path).add(ms("2026-11-01T05:00:00.000"), core_text("older"))
        self.add(CORE, "2026-11-01T06:40:00.000", 8, 300_000, "core")   # 06:40Z-07:15Z
        self.add(SELF, "2026-11-01T06:52:30.000", 4, 300_000, "self")   # 06:52:30Z-07:07:30Z
        pages = await self.read_all(sources=["core", "addon:self"], start="2026-11-01T06:00:00Z",
                                    end="2026-11-01T08:30:00Z", limit=3)
        self.assertGreater(len(pages), 3)
        rows = [(e["source"], e["message"].rsplit("] ", 1)[1]) for p in pages for e in p["entries"]]
        self.assertEqual(sorted(rows), sorted([("core", f"core {i}") for i in range(8)]
                                              + [("addon:self", f"self {i}") for i in range(4)]))
        for key, label, count in (("core", "core", 8), ("addon:self", "self", 4)):
            self.assertEqual([m for s, m in rows if s == key], [f"{label} {i}" for i in range(count)])
        self.assert_never_misordered(pages)
        for page in pages:
            flagged = [e for e in page["entries"] if e.get("ordering_uncertain")]
            if flagged:
                self.assertFalse(page["complete"])
                self.assertTrue(any(r.startswith("ordering_uncertain:") for r in page["omissions"]))
        # Each uncertain row is flagged, on whichever page it lands.
        self.assertTrue(all(e.get("ordering_uncertain") for p in pages for e in p["entries"]
                            if e["ts"] is None))
        self.assertFalse(any(p["complete"] for p in pages))

    async def test_long_fall_back_through_two_sources_drains_in_journal_order(self):
        # Review 1 M1: both journals write through the whole repeated hour,
        # interleaved; the budget resolves some fold entries and leaves the
        # rest uncertain. A source's merge position is the latest reading
        # so far, carried in the cursor: a later page starts no earlier, so
        # every page advances and every entry is returned once.
        await self.setup(now="2026-11-01T12:00:00.000")
        for path in (CORE, SELF):
            self.sim.journal(path).add(ms("2026-11-01T05:00:00.000"), core_text("older"))
        self.add(CORE, "2026-11-01T06:01:00.000", 40, 180_000, "core")   # 06:01Z-07:58Z
        self.add(SELF, "2026-11-01T06:02:00.000", 40, 180_000, "self")   # 06:02Z-07:59Z
        pages = await self.read_all(sources=["core", "addon:self"], start="2026-11-01T05:30:00Z",
                                    end="2026-11-01T11:30:00Z", limit=4)
        for page in pages[:-1]:
            self.assertTrue(page["entries"], page["omissions"])
        rows = [(e["source"], e["message"].rsplit("] ", 1)[1]) for p in pages for e in p["entries"]]
        for key, label in (("core", "core"), ("addon:self", "self")):
            self.assertEqual([m for s, m in rows if s == key], [f"{label} {i}" for i in range(40)])
        self.assertTrue(any(e["ts"] is None for p in pages for e in p["entries"]))
        self.assertTrue(any(e["ts_basis"] == "journal_realtime_utc" for p in pages for e in p["entries"]))
        self.assert_never_misordered(pages)
        for page in pages:
            if any(e.get("ordering_uncertain") for e in page["entries"]):
                self.assertFalse(page["complete"])

    async def test_resolution_reads_keep_a_page_for_the_other_source(self):
        # Review 1 M1: Core is read first and its fold entries could use the
        # rest of the call's budget; addon:self has spent its share locating
        # its start in a long journal. One request is kept back, so
        # addon:self reads its first page and the merge advances.
        await self.setup(now="2026-11-01T12:00:00.000")
        self.sim.journal(CORE).add(ms("2026-11-01T05:00:00.000"), core_text("older"))
        self.add(CORE, "2026-11-01T06:00:00.000", 60, 30_000, "core")
        self.add(SELF, "2026-11-01T05:20:00.000", 600, 1000, "early")
        self.add(SELF, "2026-11-01T05:30:00.000", 3600, 500, "self")
        page = await self.read(sources=["core", "addon:self"], start="2026-11-01T05:30:00Z",
                               end="2026-11-01T11:30:00Z", limit=50)
        self.assertEqual(page["requests"], c.MAX_REQUESTS_PER_CALL)
        self.assertGreater(self.source(page, "core")["timestamp_basis"]["resolved_entries"], 0)
        self.assertEqual([e["message"].rsplit("] ", 1)[1] for e in page["entries"]],
                         [f"self {i}" for i in range(50)])
        self.assertTrue(page["has_more"])

    async def test_ordering_uncertainty_carries_into_the_next_page(self):
        # Core's fold row cannot be resolved (no realtime) and ends page 1;
        # addon:self's 07:10Z row is resolved exactly and opens page 2. It is
        # before the fold row's latest reading (07:30Z), so it is not ordered
        # either; 08:10Z is after every reading and is.
        await self.setup(now="2026-11-01T09:00:00.000")
        self.sim.no_realtime_paths.add(CORE)
        self.sim.journal(CORE).add(ms("2026-11-01T05:00:00.000"), core_text("older"))
        self.sim.journal(CORE).add(ms("2026-11-01T06:30:00.000"), core_text("fold"))   # 06:30Z or 07:30Z
        self.sim.journal(SELF).add(ms("2026-11-01T05:00:00.000"), core_text("older"))
        self.sim.journal(SELF).add(ms("2026-11-01T07:10:00.000"), core_text("resolved"))  # 01:10 CST
        self.sim.journal(SELF).add(ms("2026-11-01T08:10:00.000"), core_text("after"))
        pages = await self.read_all(sources=["core", "addon:self"], start="2026-11-01T06:00:00Z",
                                    end="2026-11-01T08:30:00Z", limit=1)
        rows = [(e["message"].rsplit("] ", 1)[1], e["ts"], bool(e.get("ordering_uncertain")))
                for p in pages for e in p["entries"]]
        self.assertEqual(rows, [("fold", None, True), ("resolved", "2026-11-01T07:10:00.000Z", True),
                                ("after", "2026-11-01T08:10:00.000Z", False)])
        self.assertEqual([len(p["entries"]) for p in pages], [1, 1, 1])
        self.assertIn("ordering_uncertain:addon:self", pages[1]["omissions"])
        self.assertFalse(pages[1]["complete"])
        self.assert_never_misordered(pages)

    async def test_an_unresolvable_single_entry_read_fails_closed(self):
        # The single-entry read must be calibrated by its own realtime.
        await self.setup(now="2026-11-01T09:00:00.000")
        self.add(CORE, "2026-11-01T06:00:00.000", 2, 1_800_000, "core")
        self.sim.mutate_before[2] = lambda sim: setattr(sim, "cursor_realtime", False)
        page = await self.read(sources=["core"], start="2026-11-01T07:15:00Z", end="2026-11-01T08:00:00Z")
        row = self.source(page, "core")
        self.assertEqual((row["reason"], page["returned"], page["complete"]),
                         ("timestamp_basis_mismatch", 0, False))
        self.assertEqual(row["zone_mismatch"]["reason"], "entry_realtime_disagrees")


class RecordingCore:
    """The HAMCP-200 Core runtime surface the zone reader uses, recorded."""

    def __init__(self, *, grant=True, commit=True, valid=True):
        self.grant, self.commit, self.valid = grant, commit, valid
        self.calls: list[tuple] = []

    def acquire(self, capability_ids, **kwargs):
        self.calls.append(("acquire", capability_ids, kwargs))
        return "authority" if self.grant else None

    def consume(self, authority):
        self.calls.append(("consume", authority))
        return ("commit",) if self.commit else None

    def release(self, authority):
        self.calls.append(("release", authority))
        return True

    def revalidate(self, authority, commits):
        self.calls.append(("revalidate", authority, commits))
        return self.valid() if callable(self.valid) else self.valid

    def finish(self, commits):
        self.calls.append(("finish", commits))
        return True

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]


class ZoneReaderTests(unittest.IsolatedAsyncioTestCase):
    """``HomeAssistantZoneReader`` over the real REST client and a loopback Core."""

    async def asyncSetUp(self):
        self.requests: list[tuple[str, str, str | None]] = []
        self.payload: object = {"time_zone": CHICAGO, "version": "2026.10.3"}
        self.status = 200
        self.delay = 0.0

        async def config(request: web.Request) -> web.StreamResponse:
            self.requests.append((request.method, request.path, request.headers.get("Authorization")))
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.status != 200:
                return web.Response(status=self.status)
            return web.json_response(self.payload)

        app = web.Application()
        app.router.add_route("*", "/api/{tail:.*}", config)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.addAsyncCleanup(self.runner.cleanup)
        port = site._server.sockets[0].getsockname()[1]
        self.client = HomeAssistantRestClient(Settings(
            ha_url=f"http://127.0.0.1:{port}", ha_token=HA_TOKEN, access_secret="synthetic-access-220",
            port=8100, audit_path="unused-synthetic-audit.jsonl", rate_limit_per_minute=120,
            rate_limit_burst=20, destructive_services=frozenset(), ha_timeout_seconds=2.0,
        ))

    async def test_reads_time_zone_under_its_own_committed_basic_rest_read(self):
        core = RecordingCore()
        self.assertEqual(await HomeAssistantZoneReader(self.client, core)(), CHICAGO)
        self.assertEqual(self.requests, [("GET", "/api/config", f"Bearer {HA_TOKEN}")])
        self.assertEqual(REQUIREMENTS, ("core.basic_rest_read",))
        self.assertEqual(core.calls[0], ("acquire", ("core.basic_rest_read",), {}))
        # Revalidated at dispatch and again before the value is used.
        self.assertEqual(core.names(), ["acquire", "consume", "revalidate", "revalidate", "finish"])

    async def test_refused_or_uncommitted_authority_makes_no_request(self):
        for core, names in ((RecordingCore(grant=False), ["acquire"]),
                            (RecordingCore(commit=False), ["acquire", "consume", "release"])):
            with self.subTest(names=names):
                self.assertIsNone(await HomeAssistantZoneReader(self.client, core)())
                self.assertEqual((self.requests, core.names()), ([], names))
        for client, core in ((None, RecordingCore()), (self.client, None)):
            self.assertIsNone(await HomeAssistantZoneReader(client, core)())
        self.assertIsNone(await HomeAssistantZoneReader(self.client, RecordingCore())(0))
        self.assertEqual(self.requests, [])

    async def test_revalidation_failure_refuses_dispatch_or_the_value(self):
        core = RecordingCore(valid=False)
        self.assertIsNone(await HomeAssistantZoneReader(self.client, core)())
        self.assertEqual(self.requests, [])
        self.assertEqual(core.names(), ["acquire", "consume", "revalidate", "finish"])
        # Valid at dispatch, lost before the value is used.
        answers = iter((True, False))
        core = RecordingCore(valid=lambda: next(answers))
        self.assertIsNone(await HomeAssistantZoneReader(self.client, core)())
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(core.names(), ["acquire", "consume", "revalidate", "revalidate", "finish"])

    async def test_failures_and_malformed_payloads_are_none(self):
        cases = [("status", 500), ("status", 401), ("payload", []), ("payload", "UTC"),
                 ("payload", {}), ("payload", {"time_zone": 7}), ("payload", {"time_zone": None})]
        for attribute, value in cases:
            with self.subTest(attribute=attribute, value=value):
                await self.asyncSetUp()
                setattr(self, attribute, value)
                core = RecordingCore()
                self.assertIsNone(await HomeAssistantZoneReader(self.client, core)())
                self.assertEqual(core.names()[-1], "finish")

    async def test_timeout_is_bounded_and_none(self):
        self.delay = 1.0
        core = RecordingCore()
        loop = asyncio.get_running_loop()
        started = loop.time()
        self.assertIsNone(await HomeAssistantZoneReader(self.client, core)(0.2))
        self.assertLess(loop.time() - started, 0.9)
        self.assertEqual(core.names()[-1], "finish")

    async def test_authorizer_is_chained_and_restored(self):
        telemetry, token = begin_request()
        try:
            def prior():
                return True

            telemetry.core_dispatch_authorizer, telemetry.error_code = prior, None
            self.assertEqual(await HomeAssistantZoneReader(self.client, RecordingCore())(), CHICAGO)
            self.assertIs(telemetry.core_dispatch_authorizer, prior)
            # A prior authorizer that refuses also refuses the zone read, and
            # the refusal does not become the caller's error code.
            telemetry.core_dispatch_authorizer = lambda: False
            self.assertIsNone(await HomeAssistantZoneReader(self.client, RecordingCore())())
            self.assertEqual(len(self.requests), 1)
            self.assertIsNone(telemetry.error_code)
            self.status = 500
            telemetry.core_dispatch_authorizer = None
            self.assertIsNone(await HomeAssistantZoneReader(self.client, RecordingCore())())
            self.assertIsNone(telemetry.error_code)
            self.assertIsNone(telemetry.core_dispatch_authorizer)
        finally:
            end_request(token)

    async def test_without_request_telemetry_the_reader_installs_its_own(self):
        self.assertIsNone(current_telemetry())
        core = RecordingCore(valid=False)
        self.assertIsNone(await HomeAssistantZoneReader(self.client, core)())
        # The transport's no-telemetry default (authorize) was never relied on.
        self.assertEqual(self.requests, [])
        self.assertIsNone(current_telemetry())
        self.assertEqual(await HomeAssistantZoneReader(self.client, RecordingCore())(), CHICAGO)

    def test_recording_core_matches_the_runtime_surface(self):
        def positional(function) -> list[str]:
            return [p.name for p in inspect.signature(function).parameters.values()
                    if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD]

        for name in ("acquire", "consume", "release", "revalidate", "finish"):
            self.assertEqual(positional(getattr(RecordingCore, name)), positional(getattr(CoreRuntime, name)),
                             name)


class TimeBasisHelperTests(unittest.TestCase):
    def test_first_offset_difference(self):
        denver, phoenix = tb.load_zone("America/Denver"), tb.load_zone("America/Phoenix")
        sample = c.ZONE_SAMPLE_SECONDS * 1000
        spring = ms("2027-03-14T09:00:00.000")
        self.assertIsNone(tb.first_offset_difference(denver, phoenix, spring - 3_600_000, spring, sample))
        self.assertEqual(tb.first_offset_difference(denver, phoenix, spring - 3_600_000, spring + 1, sample),
                         spring)
        self.assertEqual(tb.first_offset_difference(denver, phoenix, spring + 5, spring + 10, sample), spring + 5)
        chicago, panama = tb.load_zone(CHICAGO), tb.load_zone("America/Panama")
        fall = ms("2026-11-01T07:00:00.000")
        self.assertIsNone(tb.first_offset_difference(chicago, panama, fall - 86_400_000, fall, sample))
        self.assertEqual(tb.first_offset_difference(chicago, panama, fall - 86_400_000, fall + 86_400_000,
                                                    sample), fall)
        self.assertIsNone(tb.first_offset_difference(chicago, chicago, fall - 3_600_000, fall + 3_600_000, sample))
        # A transition between two samples is still found exactly.
        self.assertEqual(tb.first_offset_difference(denver, phoenix, spring - 7_200_000, spring + 7_200_000,
                                                    3_600_000 * 3), spring)


if __name__ == "__main__":
    unittest.main()
