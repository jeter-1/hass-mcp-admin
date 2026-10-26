"""HAMCP-224 host journal sources. Offline: real-format fixture journals.

The journal files under ``tests/fixtures/hamcp224_journal`` were written by
systemd-journal-remote 257.13 from synthetic export streams (see
``generate.py`` and ``provenance.json``) and are read here through the same
``libsystemd.so.0`` binding the add-on uses. No Docker, no live host, no
live Home Assistant; every secret is synthetic and carries ``PLANTED``.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))

from host_journal_support import FIXTURES, MACHINE_ID, T0_MS, T0_US, journal_root, reader  # noqa: E402
from incident_logs_journal_sim import SupervisorSim, start  # noqa: E402

from ha_mcp_engineering.errors import GovernanceError  # noqa: E402
from ha_mcp_engineering.incident_logs import contracts as c  # noqa: E402
from ha_mcp_engineering.incident_logs import host_journal as hj  # noqa: E402
from ha_mcp_engineering.incident_logs import service as service_module  # noqa: E402
from ha_mcp_engineering.incident_logs.cursor import CursorCodec  # noqa: E402
from ha_mcp_engineering.incident_logs.service import IncidentLogService  # noqa: E402
from ha_mcp_engineering.incident_logs.transport import JournalTransport  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
SUPERVISOR_TOKEN = "synthetic-supervisor-" + "credential-224"
KNOWN_SECRET = "synthetic-known-" + "secret-224"
SECOND_US = 1_000_000
CODEC_KEY = b"j" * 32
# Values the fixtures plant on entries in fields that are never read.
NEVER_RETURNED = ("PLANTED", "planted-unit", "planted.c", "ghcr.io", "2001-01-01", "0123456789ab",
                  "/dev/planted", "fc2e22bc", "4242", "CONTAINER_TAG", "_HOSTNAME", "_CMDLINE")
ALL_JOURNAL_SOURCES = ("supervisor", "host", "host:kernel", "host:usb", "host:network", "host:storage",
                       "host:os", "addon:core_mosquitto", "addon:45df7312_zigbee2mqtt",
                       "addon:5c53de3b_esphome", "addon:core_matter_server")


def at(seconds: float) -> int:
    return T0_US + int(seconds * SECOND_US)


def iso(seconds: float) -> str:
    return c.format_ms(T0_MS + int(seconds * 1000))


def digest(label: str, data: bytes) -> str:
    return CursorCodec("incident_logs", key=CODEC_KEY).digest(label, data)


class RecordingLibrary:
    """The bound library, recording every field name read and symbol used."""

    def __init__(self, library):
        self._library = library
        self.fields: list[str] = []
        self.calls: set[str] = set()

    def __getattr__(self, name):
        function = getattr(self._library, name)
        self.calls.add(name)
        if name != "get_data":
            return function

        def get_data(handle, field, data, size):
            self.fields.append(field.decode("ascii"))
            return function(handle, field, data, size)
        return get_data


class Roots:
    def root(self, *args, **kwargs) -> str:
        path = journal_root(*args, **kwargs)
        self.addCleanup(shutil.rmtree, path, True)
        return path


class HostJournalReaderTests(Roots, unittest.TestCase):
    def read(self, root, plans, mountinfo="mountinfo-on", deadline=None, **kwargs) -> hj.JournalRead:
        deadline = deadline if deadline is not None else hj.time.monotonic() + 30
        return reader(root, mountinfo, **kwargs).read(plans, digest, deadline)

    def plan(self, key, start=0, end=7200, target=1000, resume=None) -> hj.Plan:
        return hj.Plan(key, at(start), at(end), target, resume)

    def messages(self, scan: hj.SourceScan) -> list[bytes]:
        return [record.message for record in scan.records if record.rejected is None]

    def test_both_fixture_variants_read_the_addon_from_its_container_only(self):
        for variant in ("uncompressed", "zstd"):
            with self.subTest(variant=variant):
                read = self.read(self.root(variant), [self.plan("addon:core_mosquitto")])
                self.assertIsNone(read.unavailable)
                self.assertEqual((read.cutoff_from_us, read.cutoff_to_us), (at(0), at(3604)))
                scan = read.scans["addon:core_mosquitto"]
                self.assertEqual(scan.finished, "tail_reached")
                self.assertEqual({record.identifier for record in scan.records if record.rejected is None},
                                 {b"app_core_mosquitto", b"addon_core_mosquitto"})
                messages = self.messages(scan)
                self.assertIn(b"1792490402: Client ha-mcp-legacy connected", messages)
                # Spoofs (stdout transport; another container name) are rejected,
                # the near-miss slug is never matched.
                self.assertEqual(scan.rejected, 2)
                self.assertEqual([record.rt_us for record in scan.records if record.rejected],
                                 [at(5), at(6)])
                self.assertFalse(any(b"spoofed" in message or b"near-miss" in message for message in messages))
                self.assertEqual(scan.clock_regressions, 1)
                self.assertEqual((scan.earliest_us, scan.latest_us), (at(1.123456), at(3603)))

    def test_runtime_and_persistent_journals_merge_in_one_order(self):
        read = self.read(self.root(), [self.plan("supervisor")])
        self.assertEqual([record.rt_us for record in read.scans["supervisor"].records],
                         [at(0), at(22), at(3600), at(3604)])
        boots = [record.boot_ref for record in read.scans["supervisor"].records]
        self.assertEqual(len(set(boots)), 2)
        self.assertTrue(all(len(boot) == 12 for boot in boots))
        self.assertNotIn(b"b007", b"".join(boot.encode() for boot in boots))
        only_runtime = self.read(self.root(persistent=False), [self.plan("supervisor")])
        self.assertEqual([record.rt_us for record in only_runtime.scans["supervisor"].records],
                         [at(3600), at(3604)])

    def test_host_categories_follow_their_pinned_matches(self):
        keys = ("host", "host:kernel", "host:usb", "host:network", "host:storage", "host:os")
        read = self.read(self.root(), [self.plan(key) for key in keys])
        seconds = {key: [(record.rt_us - T0_US) // SECOND_US for record in read.scans[key].records]
                   for key in keys}
        self.assertEqual(seconds["host:kernel"], [7, 8, 9, 27, 3601])
        self.assertEqual(seconds["host:usb"], [7, 8, 18, 3601])
        self.assertEqual(seconds["host:network"], [10, 11, 29])
        self.assertEqual(seconds["host:storage"], [9, 26])
        self.assertEqual(seconds["host:os"], [16, 17, 26])
        # sshd is not in the Supervisor's host set; Core and add-ons never are.
        self.assertEqual(seconds["host"], [7, 8, 9, 10, 11, 16, 17, 18, 26, 27, 29, 3601])
        usb = read.scans["host:usb"].records[0]
        self.assertEqual((usb.kernel_subsystem, usb.kernel_device), (b"usb", b"1-1.2"))
        self.assertEqual(len(hj.HOST_IDENTIFIERS), 52)
        self.assertNotIn("sshd", hj.HOST_IDENTIFIERS)

    def test_only_allowlisted_fields_and_bound_symbols_are_used(self):
        recording = []

        def library():
            recording.append(RecordingLibrary(hj.load_library()))
            return recording[-1]
        self.read(self.root(), [self.plan(key) for key in ALL_JOURNAL_SOURCES], library=library)
        self.assertTrue(set(recording[0].fields) <= set(hj.READ_FIELDS), set(recording[0].fields))
        self.assertIn("MESSAGE", recording[0].fields)
        bound = {name[len("sd_journal_"):] for name in hj.BOUND_SYMBOLS} | {"free"}
        self.assertTrue(recording[0].calls <= bound, recording[0].calls - bound)

    def test_continuation_resumes_after_the_digested_entry_even_within_one_microsecond(self):
        root = self.root()
        first = self.read(root, [self.plan("addon:5c53de3b_esphome", start=27, target=1)])
        record = first.scans["addon:5c53de3b_esphome"].records[0]
        self.assertEqual(record.message, b"same microsecond one")
        resumed = []
        position = (record.rt_us, record.cursor_digest)
        for _ in range(3):
            scan = self.read(root, [self.plan("addon:5c53de3b_esphome", start=27, target=1,
                                              resume=position)]).scans["addon:5c53de3b_esphome"]
            if not scan.records:
                self.assertEqual(scan.finished, "tail_reached")
                break
            resumed.append(scan.records[0].message)
            position = (scan.records[0].rt_us, scan.records[0].cursor_digest)
            self.assertGreater(scan.resume_walked, 0)
        self.assertEqual(resumed, [b"same microsecond two", b"same microsecond three"])
        # The digest is keyed: the raw cursor is never the position.
        self.assertRegex(record.cursor_digest, r"\A[0-9a-f]{32}\Z")

    def test_an_unknown_digest_is_stale(self):
        scan = self.read(self.root(), [self.plan("addon:5c53de3b_esphome",
                                                 resume=(at(28), "0" * 32))]).scans["addon:5c53de3b_esphome"]
        self.assertTrue(scan.stale)
        self.assertEqual(scan.records, [])

    def test_end_is_the_first_accepted_entry_at_or_after_it(self):
        scan = self.read(self.root(), [self.plan("addon:core_mosquitto", end=12)]).scans["addon:core_mosquitto"]
        self.assertEqual(scan.finished, "end_reached")
        self.assertEqual([record.rt_us for record in scan.records if record.rejected is None],
                         [at(1.123456), at(2), at(3)])

    def test_field_cap_truncates_the_message_and_marks_it(self):
        scan = self.read(self.root(), [self.plan("addon:5c53de3b_esphome", start=19, end=20)]) \
            .scans["addon:5c53de3b_esphome"]
        record = scan.records[0]
        self.assertTrue(record.message_truncated)
        self.assertEqual(len(record.message), hj.MAX_FIELD_BYTES)
        with patch.object(hj, "MAX_FIELD_BYTES", 32):
            record = self.read(self.root(), [self.plan("addon:5c53de3b_esphome", start=20, end=21)]) \
                .scans["addon:5c53de3b_esphome"].records[0]
        self.assertEqual((record.message, record.message_truncated, record.partial_line),
                         (b"partial docker line", False, True))

    def test_each_bound_stops_the_scan_with_its_reason(self):
        root = self.root()
        with patch.object(hj, "MAX_SCAN_ENTRIES", 3):
            scan = self.read(root, [self.plan("host")]).scans["host"]
        self.assertEqual((scan.stopped, scan.finished, scan.scanned), ("scan_entries_limit", None, 3))
        with patch.object(hj, "MAX_CALL_FIELD_BYTES", 200):
            read = self.read(root, [self.plan("host")])
        self.assertEqual(read.scans["host"].stopped, "field_bytes_limit")
        self.assertLess(read.field_bytes, 200 + 4 * (hj.MAX_FIELD_BYTES + 32))
        scan = self.read(root, [self.plan("host")], deadline=hj.time.monotonic() - 1).scans["host"]
        self.assertEqual((scan.stopped, scan.records), ("deadline_reached", []))
        # The resume walk counts toward the scan bound.
        first = self.read(root, [self.plan("addon:5c53de3b_esphome", start=27, target=1)])
        record = first.scans["addon:5c53de3b_esphome"].records[0]
        with patch.object(hj, "MAX_SCAN_ENTRIES", 0):
            scan = self.read(root, [self.plan("addon:5c53de3b_esphome",
                                              resume=(record.rt_us, record.cursor_digest))]) \
                .scans["addon:5c53de3b_esphome"]
        self.assertTrue(scan.stale)
        with patch.object(hj, "MAX_SCAN_ENTRIES", 1):
            scan = self.read(root, [self.plan("addon:5c53de3b_esphome",
                                              resume=(record.rt_us, record.cursor_digest))]) \
                .scans["addon:5c53de3b_esphome"]
        # One walked entry found the position and spent the whole bound.
        self.assertEqual((scan.stale, scan.resume_walked, scan.scanned, scan.stopped, scan.records),
                         (False, 1, 0, "scan_entries_limit", []))

    def test_unavailability_comes_from_the_mount_table_and_the_open(self):
        root = self.root()
        plans = [self.plan("host")]
        for mountinfo, reason in (("mountinfo-off", "journald_not_enabled"),
                                  ("mountinfo-incomplete", "journal_mount_incomplete"),
                                  ("mountinfo-not-read-only", "journal_mount_not_read_only"),
                                  ("mountinfo-remounted-rw", "journal_mount_not_read_only"),
                                  ("mountinfo-escaped", "journal_mount_incomplete"),
                                  ("mountinfo-missing", "journal_unreadable")):
            with self.subTest(mountinfo=mountinfo):
                read = self.read(root, plans, mountinfo)
                self.assertEqual((read.unavailable, read.scans), (reason, {}))
        self.assertEqual(self.read(root, plans, library=lambda: None).unavailable, "journal_reader_unavailable")
        missing = self.read(os.path.join(root, "absent"), plans)
        self.assertEqual((missing.unavailable, missing.errno_name), ("journal_unreadable", "ENOENT"))
        empty = self.read(self.root(persistent=False, runtime=False), plans)
        self.assertEqual(empty.unavailable, "journal_empty_or_unsupported")
        # A symlinked mount directory is never followed.
        linked = self.root()
        target = tempfile.mkdtemp(prefix="hamcp224-elsewhere-")
        self.addCleanup(shutil.rmtree, target, True)
        shutil.rmtree(os.path.join(linked, "run/log/journal"))
        os.symlink(target, os.path.join(linked, "run/log/journal"))
        read = self.read(linked, plans)
        self.assertEqual(read.unavailable, "journal_unreadable")
        self.assertIn(read.errno_name, {"ENOTDIR", "ELOOP"})

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-0 directory")
    def test_an_unreadable_mount_directory_reports_its_errno(self):
        root = self.root()
        directory = os.path.join(root, "var/log/journal")
        os.chmod(directory, 0)
        self.addCleanup(os.chmod, directory, 0o755)
        read = self.read(root, [self.plan("host")])
        self.assertEqual((read.unavailable, read.errno_name), ("journal_unreadable", "EACCES"))

    def test_mount_table_parsing(self):
        table = (b"1 0 0:1 / / rw - overlay overlay rw\n"
                 b"2 1 0:2 / /var/log/journal ro,relatime - ext4 /dev/sda8 rw\n"
                 b"3 1 0:3 / /run/log/journal\\040x ro - tmpfs tmpfs rw\n"
                 b"short line\n")
        mounts = hj.mount_options(table)
        self.assertEqual(mounts["/var/log/journal"], frozenset({"ro", "relatime"}))
        self.assertIn("/run/log/journal x", mounts)
        self.assertEqual(hj.mount_state(table), "journal_mount_incomplete")
        self.assertIsNone(hj.mount_state((FIXTURES / "mountinfo-on").read_bytes()))

    def test_addon_match_requires_container_transport_and_equal_names(self):
        match = hj.source_match("addon:core_mosquitto")
        good = {"SYSLOG_IDENTIFIER": "app_core_mosquitto", "CONTAINER_NAME": "app_core_mosquitto",
                "_TRANSPORT": "journal"}
        self.assertTrue(hj.accepts(match, good))
        for change in ({"CONTAINER_NAME": "addon_core_mosquitto"}, {"CONTAINER_NAME": None},
                       {"_TRANSPORT": "stdout"}, {"SYSLOG_IDENTIFIER": "app_core_mosquitto_extra",
                                                  "CONTAINER_NAME": "app_core_mosquitto_extra"}):
            self.assertFalse(hj.accepts(match, {**good, **change}), change)
        self.assertFalse(c.is_journal_source("addon:self"))
        self.assertFalse(c.is_journal_source("core"))
        self.assertTrue(all(c.is_journal_source(key) for key in ALL_JOURNAL_SOURCES))


class ScreenTextTests(unittest.TestCase):
    def test_withholds_the_whole_message_for_credential_forms(self):
        for text in ("wlan0: psk=abc", "network_key: [1, 2]", "wpa_passphrase = x",
                     "mosquitto_sub -u mqtt -P hunter2", "mosquitto_pub --pw=hunter2",
                     "-----BEGIN OPENSSH PRIVATE KEY-----", "-----BEGIN RSA PRIVATE KEY-----",
                     "mqtt://user:secretvalue@host:1883", f"token {KNOWN_SECRET} seen",
                     "wрa_psk=abc", "p s k = abc",
                     # Confusables inside the key itself: caught only once folded.
                     "p\u0455k=abc", "network_k\u0435y: abc"):
            with self.subTest(text=text):
                self.assertEqual(hj.screen_text(text.encode(), (KNOWN_SECRET,)),
                                 (hj.WITHHELD_MARKER, hj.WITHHELD_OMISSION))

    def test_keeps_ordinary_lines_and_strips_terminal_escapes(self):
        for raw, expected in ((b"\x1b[32mSupervisor is up\x1b[0m", "Supervisor is up"),
                              (b"mosquitto -p 1883 -c /etc/mosquitto.conf", "mosquitto -p 1883 -c /etc/mosquitto.conf"),
                              (b"keyboard: usb 1-1", "keyboard: usb 1-1"),
                              (b"\xff bad utf-8", "� bad utf-8")):
            self.assertEqual(hj.screen_text(raw, ()), (expected, None))

    def test_invisible_characters_are_unverifiable(self):
        self.assertEqual(hj.screen_text("ps​k=abc".encode(), ()),
                         (hj.UNVERIFIABLE_MARKER, hj.UNVERIFIABLE_OMISSION))

    def test_levels_come_from_priority_only(self):
        self.assertEqual([hj.LEVELS_BY_PRIORITY.get(str(n).encode()) for n in range(8)],
                         ["critical", "critical", "critical", "error", "warning", "info", "info", "debug"])
        self.assertIsNone(hj.LEVELS_BY_PRIORITY.get(None))
        self.assertIsNone(hj.LEVELS_BY_PRIORITY.get(b"8"))


class JournalServiceTests(Roots, unittest.IsolatedAsyncioTestCase):
    VARIANT = "uncompressed"

    async def asyncSetUp(self):
        self.journal = self.root(self.VARIANT)
        self.service = self.make_service(reader(self.journal))

    def make_service(self, journal_reader, transport=None) -> IncidentLogService:
        transport = transport or JournalTransport(token_source=lambda: SUPERVISOR_TOKEN, timeout_seconds=1.0,
                                                  base_url="http://127.0.0.1:9")
        codec = CursorCodec("incident_logs", key=CODEC_KEY, clock=lambda: T0_MS / 1000 + 7200)
        return IncidentLogService(transport, known_secrets=(KNOWN_SECRET,), codec=codec,
                                  clock=lambda: T0_MS + 7_200_000, zone_source=lambda: "UTC",
                                  addon_zone_source=lambda: "UTC", journal_reader=journal_reader)

    async def read(self, service=None, **arguments) -> dict:
        arguments.setdefault("start", iso(0))
        arguments.setdefault("end", iso(7000))
        telemetry, token = begin_request()
        telemetry.caller_id = "synthetic_authenticated_caller"
        try:
            return await (service or self.service).read(arguments)
        finally:
            end_request(token)

    async def read_all(self, **arguments) -> list[dict]:
        pages = [await self.read(**arguments)]
        while pages[-1]["next_cursor"]:
            self.assertLess(len(pages), 100)
            pages.append(await self.read(**arguments, cursor=pages[-1]["next_cursor"]))
        return pages

    def row(self, page, key) -> dict:
        return next(row for row in page["sources"] if row["source"] == key)

    def ids(self, pages) -> list[tuple]:
        return [(entry["ts"], entry["source"], entry["message"]) for page in pages for entry in page["entries"]]

    async def test_addon_source_returns_its_container_lines_with_journal_times(self):
        page = await self.read(sources=["addon:core_mosquitto"])
        self.assertEqual(page["requests"], 0)
        messages = [entry["message"] for entry in page["entries"]]
        self.assertEqual(messages[:3], ["1792490401: New connection from 172.30.32.1:54012 on port 1883.",
                                        "1792490402: Client ha-mcp-legacy connected",
                                        "Error: Connection refused"])
        first = page["entries"][0]
        self.assertEqual((first["ts"], first["ts_basis"], first["time_zone"], first["process"], first["level"],
                          first["level_basis"]),
                         (iso(1.123), "journal_realtime_utc", "UTC", "app_core_mosquitto", "info",
                          "journal_priority"))
        self.assertRegex(first["boot_ref"], r"\A[0-9a-f]{12}\Z")
        row = self.row(page, "addon:core_mosquitto")
        self.assertEqual((row["status"], row["reader"], row["route"], row["requests"], row["match_rejected"],
                          row["withheld_messages"], row["start_location"]),
                         ("partial", "host_journal", None, 0, 2, 2, "exact"))
        self.assertIn("journal_match_rejected:addon:core_mosquitto", page["omissions"])
        self.assertIn("credential_text_withheld", page["omissions"])
        self.assertFalse(page["complete"])
        self.assertNotIn("spoofed", json.dumps(page))
        self.assertNotIn("near-miss", json.dumps(page))
        self.assertEqual(page["time_basis"]["host_journal"], "journal_realtime_utc")
        self.assertEqual(page["time_basis"]["time_zone_status"], "not_read")

    async def test_no_planted_value_or_unlisted_field_is_ever_returned(self):
        for variant in ("uncompressed", "zstd"):
            service = self.make_service(reader(self.root(variant)))
            for group in (ALL_JOURNAL_SOURCES[:4], ALL_JOURNAL_SOURCES[4:8], ALL_JOURNAL_SOURCES[8:]):
                with self.subTest(variant=variant, sources=group):
                    pages = await self.read_all(service=service, sources=list(group), limit=7)
                    text = json.dumps(pages)
                    for value in NEVER_RETURNED:
                        self.assertNotIn(value, text)
                    for page in pages:
                        for entry in page["entries"]:
                            self.assertTrue(set(entry) <= {
                                "ts", "ts_basis", "time_zone", "source", "process", "level", "level_basis",
                                "message", "message_truncated", "withheld", "kernel_subsystem",
                                "kernel_device", "boot_ref", "partial_line", "ordering_uncertain",
                            }, set(entry))

    async def test_credential_messages_are_withheld_whole_and_never_searched(self):
        page = await self.read(sources=["host:network", "supervisor", "addon:45df7312_zigbee2mqtt"])
        withheld = [entry for entry in page["entries"] if "withheld" in entry]
        self.assertEqual([(entry["source"], entry["message"], entry["withheld"]) for entry in withheld],
                         [("host:network", hj.WITHHELD_MARKER, "credential_text_withheld"),
                          ("addon:45df7312_zigbee2mqtt", hj.WITHHELD_MARKER, "credential_text_withheld"),
                          ("addon:45df7312_zigbee2mqtt", hj.WITHHELD_MARKER, "credential_text_withheld"),
                          ("supervisor", hj.WITHHELD_MARKER, "credential_text_withheld")])
        self.assertEqual(self.row(page, "supervisor")["withheld_messages"], 1)
        for text in ("planted-wifi", "planted-uri", "planted-zigbee", "planted-option", "network_key", "psk",
                     "password"):
            filtered = await self.read(sources=["host:network", "supervisor", "addon:45df7312_zigbee2mqtt"],
                                       text=text)
            self.assertEqual(filtered["returned"], 0, text)

    async def test_levels_text_filter_and_kernel_fields(self):
        page = await self.read(sources=["supervisor", "host:usb"], min_level="error")
        self.assertEqual([(entry["source"], entry["level"]) for entry in page["entries"]],
                         [("supervisor", "error")])
        page = await self.read(sources=["host:usb"], text="usb disconnect")
        self.assertEqual([(entry["ts"], entry["kernel_subsystem"], entry["kernel_device"])
                          for entry in page["entries"]],
                         [(iso(7), "usb", "1-1.2"), (iso(3601), "usb", "1-1.2")])
        page = await self.read(sources=["addon:5c53de3b_esphome"], start=iso(19), end=iso(22))
        no_priority = page["entries"][-1]
        self.assertEqual((no_priority["message"], no_priority["level"], no_priority["level_basis"]),
                         ("line without priority", None, None))
        self.assertTrue(page["entries"][0]["message_truncated"])
        self.assertTrue(page["entries"][1]["partial_line"])

    async def test_pages_continue_without_loss_or_repeat(self):
        sources = ["host", "supervisor", "addon:5c53de3b_esphome", "addon:core_mosquitto"]
        whole = await self.read(sources=sources, limit=200)
        self.assertFalse(whole["has_more"])
        for limit in (1, 2, 5):
            with self.subTest(limit=limit):
                pages = await self.read_all(sources=sources, limit=limit)
                self.assertEqual(self.ids(pages), self.ids([whole]))
                self.assertTrue(all(page["requests"] == 0 for page in pages))
                self.assertGreater(len(pages), 1)

    async def test_cursor_carries_no_raw_journal_cursor(self):
        page = await self.read(sources=["addon:5c53de3b_esphome"], limit=1)
        payload = CursorCodec("incident_logs", key=CODEC_KEY, clock=lambda: T0_MS / 1000 + 7200) \
            .decode(page["next_cursor"])
        state = payload["s"]["addon:5c53de3b_esphome"]
        self.assertEqual(set(state), {"jt", "jd", "t"})
        self.assertRegex(state["jd"], r"\A[0-9a-f]{32}\Z")
        text = json.dumps(payload)
        self.assertNotIn("b0070000", text)
        self.assertNotIn("s=", text)

    async def test_a_vanished_entry_breaks_continuity(self):
        page = await self.read(sources=["addon:core_mosquitto"], limit=1)
        moved = self.make_service(reader(self.root(persistent=False)))
        with self.assertRaises(GovernanceError) as caught:
            await self.read(service=moved, sources=["addon:core_mosquitto"], limit=1, cursor=page["next_cursor"])
        self.assertEqual(caught.exception.details["reason"], "journal_continuity_broken")

    async def test_a_signed_state_with_a_malformed_digest_is_refused(self):
        page = await self.read(sources=["addon:5c53de3b_esphome"], limit=1)
        codec = self.service.codec
        for digest in ("zz", "0" * 31, "0" * 32 + "\n", "A" * 32, 7):
            payload = codec.decode(page["next_cursor"])
            payload["s"]["addon:5c53de3b_esphome"]["jd"] = digest
            with self.assertRaises(GovernanceError) as caught:
                await self.read(sources=["addon:5c53de3b_esphome"], limit=1, cursor=codec.encode(payload))
            self.assertEqual(caught.exception.details["reason"], "integrity_or_format_invalid", digest)

    async def test_clock_regression_is_disclosed_and_ordering_uncertain(self):
        page = await self.read(sources=["addon:core_mosquitto"], start=iso(3000))
        self.assertEqual([entry["message"] for entry in page["entries"]],
                         ["1792494002: after reboot", "1792493999: after clock step back",
                          "1792494003: clock recovered"])
        self.assertEqual([bool(entry.get("ordering_uncertain")) for entry in page["entries"]],
                         [False, True, False])
        row = self.row(page, "addon:core_mosquitto")
        self.assertEqual((row["clock_regression"], row["clock_regressions"], row["ordering_uncertain"]),
                         (True, 1, 1))
        self.assertIn("ordering_uncertain:addon:core_mosquitto", page["omissions"])

    async def test_window_before_retention_is_a_gap_never_complete(self):
        page = await self.read(sources=["supervisor"], start=iso(-600), end=iso(10))
        row = self.row(page, "supervisor")
        self.assertEqual((row["start_location"], row["retention_edge_reached"], row["status"]),
                         ("retention_edge", True, "partial"))
        self.assertEqual(row["gaps"], [{"from": iso(-600), "to": iso(0), "reason": "before_retention_edge"}])
        self.assertIn("before_retention_edge:supervisor", page["omissions"])

    async def test_a_clean_window_is_complete(self):
        page = await self.read(sources=["host:os"], start=iso(15), end=iso(20))
        self.assertEqual([entry["message"] for entry in page["entries"]],
                         ["Booted into slot B", 'level=info msg="Loading containers: done."'])
        self.assertTrue(page["complete"])
        row = self.row(page, "host:os")
        self.assertEqual((row["status"], row["reason"], row["entries_scanned"]), ("complete", "end_reached", 3))

    async def test_unavailable_journal_is_never_reported_empty_or_complete(self):
        cases = (("mountinfo-off", "journald_not_enabled"), ("mountinfo-incomplete", "journal_mount_incomplete"),
                 ("mountinfo-not-read-only", "journal_mount_not_read_only"))
        for mountinfo, reason in cases:
            service = self.make_service(reader(self.journal, mountinfo))
            page = await self.read(service=service, sources=["host", "addon:core_mosquitto"])
            self.assertEqual([(row["status"], row["reason"], row["detail"]) for row in page["sources"]],
                             [("unavailable", reason, hj.UNAVAILABLE_DETAIL[reason])] * 2)
            self.assertEqual((page["returned"], page["complete"], page["has_more"]), (0, False, False))
            self.assertIn("source_unavailable:host", page["omissions"])
        service = self.make_service(reader(self.root(persistent=False, runtime=False)))
        page = await self.read(service=service, sources=["host"])
        self.assertEqual(self.row(page, "host")["reason"], "journal_empty_or_unsupported")
        self.assertFalse(page["complete"])
        service = self.make_service(reader(os.path.join(self.journal, "absent")))
        row = self.row(await self.read(service=service, sources=["host"]), "host")
        self.assertEqual((row["reason"], row["errno"]), ("journal_unreadable", "ENOENT"))

    async def test_a_reader_exception_fails_closed_per_source(self):
        class Exploding:
            def read(self, *args):
                raise RuntimeError("synthetic failure PLANTED-EXCEPTION")
        page = await self.read(service=self.make_service(Exploding()), sources=["host", "supervisor"])
        self.assertEqual([(row["status"], row["reason"]) for row in page["sources"]],
                         [("unavailable", "journal_read_failed")] * 2)
        self.assertNotIn("PLANTED", json.dumps(page))

    async def test_token_forms_the_screen_keeps_are_redacted_by_the_shared_sanitizer(self):
        # Bearer and JWT tokens are not whole-message credential forms (design
        # §7 step 2); the shared line sanitizer at the same exit redacts them.
        real = reader(self.journal)
        planted = (b"auth retry Authorization: Bearer PLANTEDbearer0123456789abcdef "
                   b"jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJQTEFOVEVEIn0.PLANTEDsignature01")

        class Planting:
            def read(self, plans, digest, deadline):
                result = real.read(plans, digest, deadline)
                records = result.scans["supervisor"].records
                first = next(n for n, record in enumerate(records) if record.rejected is None)
                records[first] = dataclasses.replace(records[first], message=planted)
                return result
        page = await self.read(service=self.make_service(Planting()), sources=["supervisor"])
        [entry] = [entry for entry in page["entries"] if entry["message"].startswith("auth retry")]
        self.assertIn("[REDACTED:token]", entry["message"])
        self.assertNotIn("withheld", entry)
        self.assertNotIn("PLANTED", json.dumps(page))
        self.assertIn("redacted_by_policy", page["omissions"])
        self.assertFalse(page["complete"])

    async def test_rejected_entries_are_counted_and_never_returned(self):
        real = reader(self.journal)
        whole = await self.read(sources=["supervisor"])

        class Rejecting:
            def read(self, plans, digest, deadline):
                result = real.read(plans, digest, deadline)
                records = result.scans["supervisor"].records
                accepted = [n for n, record in enumerate(records) if record.rejected is None]
                for n, reason in zip(accepted[:2], ("match_rejected", "entry_unreadable")):
                    records[n] = dataclasses.replace(records[n], rejected=reason,
                                                     message=b"PLANTED-REJECTED never returned")
                return result
        page = await self.read(service=self.make_service(Rejecting()), sources=["supervisor"])
        self.assertEqual(page["returned"], whole["returned"] - 2)
        self.assertEqual([entry["message"] for entry in page["entries"]],
                         [entry["message"] for entry in whole["entries"]][2:])
        row = self.row(page, "supervisor")
        self.assertEqual((row["status"], row["match_rejected"], row["entries_unreadable"]), ("partial", 1, 1))
        self.assertIn("journal_match_rejected:supervisor", page["omissions"])
        self.assertNotIn("PLANTED", json.dumps(page))

    async def test_an_unreadable_entry_alone_makes_the_source_partial(self):
        real = reader(self.journal)

        class Unreadable:
            def read(self, plans, digest, deadline):
                result = real.read(plans, digest, deadline)
                records = result.scans["host:os"].records
                first = next(n for n, record in enumerate(records) if record.rejected is None)
                records[first] = dataclasses.replace(records[first], rejected="entry_unreadable")
                return result
        # The window test_a_clean_window_is_complete proves complete.
        page = await self.read(service=self.make_service(Unreadable()), sources=["host:os"],
                               start=iso(15), end=iso(20))
        row = self.row(page, "host:os")
        self.assertEqual((row["status"], row["match_rejected"], row["entries_unreadable"]), ("partial", 0, 1))
        self.assertIn("journal_entry_unreadable:host:os", page["omissions"])
        self.assertFalse(page["complete"])
        self.assertEqual(page["returned"], 1)

    async def test_scan_bound_is_disclosed_and_resumes(self):
        with patch.object(hj, "MAX_SCAN_ENTRIES", 4):
            pages = await self.read_all(sources=["host"])
        self.assertIn("scan_entries_limit:host", pages[0]["omissions"])
        self.assertEqual(self.row(pages[0], "host")["reason"], "scan_entries_limit")
        self.assertTrue(pages[0]["has_more"])
        whole = await self.read(sources=["host"])
        self.assertEqual(self.ids(pages), self.ids([whole]))

    async def test_the_journal_read_runs_in_one_worker_thread_with_the_call_deadline(self):
        seen = []
        real = reader(self.journal)

        class Watching:
            def read(self, plans, digest, deadline):
                seen.append((threading.current_thread() is threading.main_thread(), plans, deadline))
                return real.read(plans, digest, deadline)
        page = await self.read(service=self.make_service(Watching()), sources=["host", "supervisor"])
        self.assertGreater(page["returned"], 0)
        [(on_main_thread, plans, deadline)] = seen
        self.assertFalse(on_main_thread)
        self.assertEqual([plan.key for plan in plans], ["host", "supervisor"])
        # The reader stops a full second before the call deadline, leaving the exit time to finish.
        self.assertLess(deadline - hj.time.monotonic(), c.CALL_DEADLINE_SECONDS - 1.0)


class ZstdJournalServiceTests(JournalServiceTests):
    VARIANT = "zstd"


class MixedSourceTests(Roots, unittest.IsolatedAsyncioTestCase):
    """A gateway source and a journal source merged on one timeline."""

    async def asyncSetUp(self):
        self.sim = SupervisorSim(SUPERVISOR_TOKEN, render="utc", zone="UTC")
        self.runner, self.url = await start(self.sim)
        self.addAsyncCleanup(self.runner.cleanup)

    async def test_core_and_supervisor_journal_entries_interleave_by_time(self):
        core = self.sim.journal("/core/logs")
        for second in (5, 23, 3602):
            ts = T0_MS + second * 1000
            stamp = service_module.datetime.fromtimestamp(ts / 1000, service_module.timezone.utc)
            core.add(ts, f"{stamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} INFO (MainThread) "
                         f"[synthetic.component] core event {second}")
        transport = JournalTransport(token_source=lambda: SUPERVISOR_TOKEN, timeout_seconds=2.0, base_url=self.url)
        service = JournalServiceTests.make_service(self, reader(self.root()), transport)
        pages = []
        cursor = None
        while True:
            arguments = {"sources": ["core", "supervisor"], "start": iso(0), "end": iso(7000), "limit": 2}
            if cursor:
                arguments["cursor"] = cursor
            page = await JournalServiceTests.read(self, service=service, **arguments)
            pages.append(page)
            cursor = page["next_cursor"]
            if not cursor:
                break
            self.assertLess(len(pages), 20)
        order = [(entry["source"], entry["ts"]) for page in pages for entry in page["entries"]]
        self.assertEqual(order, [("supervisor", iso(0)), ("core", iso(5)), ("supervisor", iso(22)),
                                 ("core", iso(23)), ("supervisor", iso(3600)), ("core", iso(3602)),
                                 ("supervisor", iso(3604))])
        self.assertEqual(JournalServiceTests.row(self, pages[0], "supervisor")["requests"], 0)
        self.assertGreater(JournalServiceTests.row(self, pages[0], "core")["requests"], 0)


    async def test_a_journal_failure_stays_failed_on_later_pages(self):
        core = self.sim.journal("/core/logs")
        for second in (5, 23, 3602):
            ts = T0_MS + second * 1000
            stamp = service_module.datetime.fromtimestamp(ts / 1000, service_module.timezone.utc)
            core.add(ts, f"{stamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} INFO (MainThread) "
                         f"[synthetic.component] core event {second}")
        calls = []

        class Exploding:
            def read(self, *args):
                calls.append(args)
                raise RuntimeError("synthetic failure PLANTED-EXCEPTION")
        transport = JournalTransport(token_source=lambda: SUPERVISOR_TOKEN, timeout_seconds=2.0, base_url=self.url)
        service = JournalServiceTests.make_service(self, Exploding(), transport)
        arguments = {"sources": ["core", "supervisor"], "start": iso(0), "end": iso(7000), "limit": 2}
        first = await JournalServiceTests.read(self, service=service, **arguments)
        self.assertEqual(JournalServiceTests.row(self, first, "supervisor")["reason"], "journal_read_failed")
        self.assertTrue(first["next_cursor"])
        second = await JournalServiceTests.read(self, service=service, **arguments, cursor=first["next_cursor"])
        row = JournalServiceTests.row(self, second, "supervisor")
        self.assertEqual((row["status"], row["reason"]), ("unavailable", "failed_in_earlier_page"))
        self.assertFalse(second["complete"])
        self.assertEqual(len(calls), 1)

class StaticBoundaryTests(unittest.TestCase):
    FORBIDDEN_SYMBOLS = ("sd_journal_enumerate", "sd_journal_restart_data", "sd_journal_query_unique",
                         "sd_journal_enumerate_unique", "sd_journal_enumerate_fields", "sd_journal_seek_cursor",
                         "sd_journal_test_cursor", "sd_journal_get_fd", "sd_journal_has_runtime_files",
                         "sd_journal_has_persistent_files", "sd_journal_send", "sd_journal_print",
                         "sd_journal_stream_fd", "sd_journal_set_data_threshold", "sd_journal_open_files",
                         "sd_journal_open_namespace", "sd_journal_process", "sd_journal_wait")

    def package_files(self):
        return sorted(PACKAGE.rglob("*.py"))

    def test_ctypes_and_raw_opens_live_only_in_the_journal_reader(self):
        users = {}
        for path in self.package_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [alias.name for alias in node.names] + [getattr(node, "module", None) or ""]
                    if any(name == "ctypes" or name.startswith("ctypes.") for name in names):
                        users.setdefault("ctypes", set()).add(path.relative_to(PACKAGE).as_posix())
        self.assertEqual(users, {"ctypes": {"incident_logs/host_journal.py"}})
        source = (PACKAGE / "incident_logs" / "host_journal.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        opens = [ast.unparse(node) for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and ast.unparse(node.func) == "os.open"]
        self.assertEqual(opens, ["os.open(path, MOUNTINFO_FLAGS)", "os.open(self.root, OPEN_FLAGS)",
                                 "os.open(point.lstrip('/'), OPEN_FLAGS, dir_fd=root)"])
        for name in ("os.write", "os.unlink", "os.remove", "os.rename", "os.mkdir", "os.chmod", "subprocess",
                     "O_WRONLY", "O_RDWR", "O_CREAT"):
            self.assertNotIn(name, source)
        # The descriptor stays ours (no SD_JOURNAL_TAKE_DIRECTORY_FD); OS_ROOT only.
        self.assertEqual(hj.SD_JOURNAL_OS_ROOT, 1 << 4)
        self.assertIn("lib.open_directory_fd(ctypes.byref(handle), root, SD_JOURNAL_OS_ROOT)", source)
        self.assertEqual(hj.OPEN_FLAGS, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)

    def test_symbol_and_field_allowlists_are_pinned(self):
        self.assertEqual(hj.BOUND_SYMBOLS, (
            "sd_journal_open_directory_fd", "sd_journal_close",
            "sd_journal_add_match", "sd_journal_add_disjunction", "sd_journal_flush_matches",
            "sd_journal_seek_realtime_usec", "sd_journal_seek_head", "sd_journal_seek_tail",
            "sd_journal_next", "sd_journal_previous",
            "sd_journal_get_realtime_usec", "sd_journal_get_cursor", "sd_journal_get_data",
            "sd_journal_get_cutoff_realtime_usec",
        ))
        self.assertEqual(hj.READ_FIELDS, (
            "MESSAGE", "PRIORITY", "SYSLOG_IDENTIFIER", "CONTAINER_NAME", "_TRANSPORT",
            "_KERNEL_SUBSYSTEM", "_UDEV_SYSNAME", "_BOOT_ID", "CONTAINER_PARTIAL_MESSAGE",
        ))
        source = (PACKAGE / "incident_logs" / "host_journal.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        field_reads = {node.args[0].value for node in ast.walk(tree)
                       if isinstance(node, ast.Call) and ast.unparse(node.func) == "self._field"
                       and isinstance(node.args[0], ast.Constant)}
        self.assertTrue(field_reads <= set(hj.READ_FIELDS), field_reads)
        for path in self.package_files():
            text = path.read_text(encoding="utf-8")
            for symbol in self.FORBIDDEN_SYMBOLS:
                self.assertNotRegex(text, symbol + r"\b", path.name)
        match_fields = {name for key in hj.HOST_SOURCES for term in hj.HOST_MATCHES[key] for name, _ in term}
        match_fields |= set(hj.source_match("addon:x").fields)
        self.assertTrue(match_fields <= set(hj.READ_FIELDS))

    def test_screening_is_called_only_at_the_single_exit(self):
        tree = ast.parse((PACKAGE / "incident_logs" / "service.py").read_text(encoding="utf-8"))
        callers = set()
        for function in ast.walk(tree):
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(function):
                    if isinstance(node, ast.Call) and ast.unparse(node.func) == "hj.screen_text":
                        callers.add(function.name)
        self.assertEqual(callers, {"_journal_screen", "_journal_views"})
        finish = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_finish")
        called = {ast.unparse(node.func) for node in ast.walk(finish) if isinstance(node, ast.Call)}
        self.assertTrue({"self._journal_screen", "self._journal_views", "sanitize_log_lines"} <= called)

    def test_config_enables_journald_and_keeps_the_homeassistant_role(self):
        lines = (ROOT / "hass_mcp_engineering_beta" / "config.yaml").read_text(encoding="utf-8").splitlines()
        self.assertIn("journald: true", lines)
        self.assertIn("hassio_role: homeassistant", lines)
        for absent in ("hassio_role: manager", "hassio_role: admin", "full_access: true", "privileged:"):
            self.assertFalse(any(line.startswith(absent) for line in lines), absent)

    def test_the_image_library_pin_matches_the_build_inputs(self):
        provenance = json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))
        self.assertIn("libsystemd0 257.13-1~deb13u1", provenance["packages"])
        self.assertTrue(hj.LIBSYSTEMD_PIN.startswith("libsystemd0 257.13-1~deb13u1"))
        dockerfile = (ROOT / "hass_mcp_engineering_beta" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(provenance["base_image"].split("@", 1)[1], dockerfile)
        self.assertEqual(provenance["machine_id_directory"], MACHINE_ID)


if __name__ == "__main__":
    unittest.main()
