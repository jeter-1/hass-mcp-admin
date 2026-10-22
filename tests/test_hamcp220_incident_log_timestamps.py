"""HAMCP-220: incident-log entry times from the journal realtime, not text-as-UTC.

Offline: loopback peer and synthetic data. Supervisor 2026.10.3 (#7372)
renders the verbose prefix in its own zone without an offset; the pinned
2026.09.1 renders UTC. ``X-First-Cursor`` (the first entry's ``__CURSOR``)
carries that entry's ``__REALTIME_TIMESTAMP`` as ``t=`` (systemd v257.9
``sd-journal.c`` L1252-1258). See ``docs/HAMCP_220_INCIDENT_LOG_TIMESTAMPS.md``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))

from incident_logs_journal_sim import SupervisorSim, ms, start  # noqa: E402

from ha_mcp_engineering.incident_logs import contracts as c  # noqa: E402
from ha_mcp_engineering.incident_logs import timebasis as tb  # noqa: E402
from ha_mcp_engineering.incident_logs.cursor import CursorCodec  # noqa: E402
from ha_mcp_engineering.incident_logs.journal import detect_level, parse_page  # noqa: E402
from ha_mcp_engineering.incident_logs.service import IncidentLogService  # noqa: E402
from ha_mcp_engineering.incident_logs.transport import JournalTransport, PageFailure, build_page  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402


SUPERVISOR_TOKEN = "synthetic-supervisor-" + "credential-220"
CORE = "/core/logs"
SELF = "/addons/self/logs"
CHICAGO = "America/Chicago"
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
                    now: str = "2026-10-22T12:34:48.000"):
        self.sim = SupervisorSim(SUPERVISOR_TOKEN, render=render, zone=supervisor_zone)
        self.runner, url = await start(self.sim)
        self.addAsyncCleanup(self.runner.cleanup)
        self.zone = [addon_zone]
        transport = JournalTransport(token_source=lambda: SUPERVISOR_TOKEN, timeout_seconds=2.0, base_url=url)
        now_ms = ms(now)
        self.service = IncidentLogService(transport, codec=CursorCodec("incident_logs", key=b"k" * 32),
                                          clock=lambda: now_ms, zone_source=lambda: self.zone[0])

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
            "time_zone_source": c.TIME_ZONE_SOURCE, "time_zone_status": "loaded", "uncertain_entries": 0,
        })
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
        await self.setup(now="2026-11-01T09:00:00.000")
        self.sim.journal(CORE).add(ms("2026-11-01T05:00:00.000"), core_text("earlier"))
        self.add(CORE, "2026-11-01T05:45:00.000", 31, 300_000, "core")
        page = await self.read(sources=["core"], start="2026-11-01T05:30:00Z", end="2026-11-01T08:30:00Z")
        self.assertTrue(page["complete"], page["omissions"])
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
        # The pinned UTC rendering of the same hour is exact throughout.
        await self.setup(render="utc", now="2026-11-01T09:00:00.000")
        self.sim.journal(CORE).add(ms("2026-11-01T05:00:00.000"), core_text("earlier"))
        self.add(CORE, "2026-11-01T05:45:00.000", 31, 300_000, "core")
        page = await self.read(sources=["core"], start="2026-11-01T05:30:00Z", end="2026-11-01T08:30:00Z")
        self.assertEqual(page["time_basis"]["uncertain_entries"], 0)
        self.assertTrue(page["complete"])

    async def test_uncertain_entry_at_a_window_edge_makes_the_read_partial(self):
        await self.setup(now="2026-11-01T09:00:00.000")
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
        # A window clear of the repeated hour is complete.
        page = await self.read(sources=["core"], start="2026-11-01T08:00:00Z", end="2026-11-01T08:30:00Z")
        self.assertTrue(page["complete"])

    async def test_window_after_tail_only_when_every_reading_is_before_start(self):
        await self.setup(now="2026-11-01T09:00:00.000")
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
        for zone in (None, "", "Mars/Olympus_Mons", ":America/Chicago"):
            with self.subTest(zone=zone):
                await self.setup(addon_zone=zone)
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

    async def test_time_zone_lost_mid_continuation_fails_the_source(self):
        await self.setup()
        self.add(CORE, "2026-10-22T12:00:00.000", 30, 1000, "core")
        arguments = {"sources": ["core"], "start": "2026-10-22T12:00:00Z", "end": "2026-10-22T12:01:00Z",
                     "limit": 10}
        first = await self.read(**arguments)
        self.assertEqual(first["returned"], 10)
        calls = len(self.sim.calls)
        self.zone[0] = None
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
        page = await self.read(sources=["core"], start="2026-10-22T12:00:00Z", end="2026-10-22T12:00:10Z")
        row = self.source(page, "core")
        self.assertEqual((row["status"], row["reason"], page["returned"], page["complete"]),
                         ("unavailable", "timestamp_basis_mismatch", 0, False))

    async def test_explicit_offset_rendering_end_to_end(self):
        await self.setup(render="offset")
        self.add(CORE, "2026-10-22T12:00:00.000", 10, 1000, "core")
        page = await self.read(sources=["core"], start="2026-10-22T12:00:03Z", end="2026-10-22T12:00:06Z")
        self.assertEqual([e["ts"] for e in page["entries"]],
                         ["2026-10-22T12:00:03.000Z", "2026-10-22T12:00:04.000Z", "2026-10-22T12:00:05.000Z"])
        self.assertEqual({e["time_zone"] for e in page["entries"]}, {"-05:00"})
        self.assertTrue(page["complete"])


if __name__ == "__main__":
    unittest.main()
