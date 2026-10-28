"""HAMCP-224 review 1 corrections: one screen, completeness, microsecond order, shared bounds.

Synthetic fixtures only; no live Home Assistant and no host journal. Every
credential-shaped payload is assembled at run time from the HAMCP-132 and
PR #294 suites' own builders, so this file holds no secret-shaped literal.
"""

from __future__ import annotations

import ctypes
import dataclasses
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
from urllib.parse import quote

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))
sys.path.insert(0, str(TESTS.parent / "hass_mcp_engineering_beta"))

import test_hamcp132_configuration_definitions as t  # noqa: E402
import test_hamcp224_host_journal as author  # noqa: E402
import test_pr294_corr2_extra as corr2  # noqa: E402
import test_pr294_delta_review as delta  # noqa: E402
import test_pr294_final32_review as final32  # noqa: E402

from ha_mcp_engineering.configuration_definitions import credentials  # noqa: E402
from ha_mcp_engineering.configuration_definitions.contracts import MAX_TEXT, MAX_TITLE  # noqa: E402
from ha_mcp_engineering.incident_logs import contracts as c  # noqa: E402
from ha_mcp_engineering.incident_logs.service import IncidentLogService  # noqa: E402

hj = author.hj
MARKERS = (hj.WITHHELD_MARKER, hj.UNVERIFIABLE_MARKER)


def credential_cases() -> list[tuple[str, bytes, str | None]]:
    """``(label, raw field bytes, needle)``: every credential case of the PR #294 suites.

    The texts are the ones those suites plant into configuration-definitions
    titles, YAML values and envelopes; ``needle`` is what must never reach
    the response (``None`` for a non-credential invisible-character case,
    which must be withheld all the same).
    """

    cases: list[tuple[str, bytes, str | None]] = []

    def add(label: str, text: str, needle: str | None) -> None:
        cases.append((label, text.encode("utf-8", "surrogatepass"), needle))

    # test_pr294_delta_review
    for label, text in delta.forms().items():
        add(f"delta.forms.{label}", text, t.DB_PASSWORD)
    for prefix in ("", "https://"):
        credential = prefix + t.DB_USER + ":" + t.DB_PASSWORD
        add(f"delta.title_truncation.{prefix or 'bare'}",
            "x" * (MAX_TITLE - len(credential) - 1) + " " + credential + "@synthetic.invalid", t.DB_PASSWORD)
        # The journal's own bound: the credential straddles the field cap.
        add(f"delta.field_truncation.{prefix or 'bare'}",
            "x" * (hj.MAX_FIELD_BYTES - len(credential) + 4) + " " + credential + "@synthetic.invalid",
            t.DB_PASSWORD)
    add("delta.confusable.ratio_division", t.db_url().replace(":", "∶").replace("/", "∕"), t.DB_PASSWORD)
    add("delta.confusable.cyrillic_o_password",
        "https://synthetic.invalid/?passwоrd=" + t.DB_PASSWORD, t.DB_PASSWORD)
    for name, value in (("lower", t.ACCESS_SECRET.lower()), ("upper", t.ACCESS_SECRET.upper()),
                        ("swapcase", t.ACCESS_SECRET.swapcase())):
        add(f"delta.known_secret.{name}", value, value)

    # test_pr294_corr2_extra
    for scheme in ("", "https://", "anything+driver://", "mysql://", "mqtt://"):
        for cut in ("middle", "beyond"):
            credential = scheme + "synthetic:" + corr2.NEEDLE
            padding = MAX_TITLE - len(credential) - 1 if cut == "middle" else MAX_TITLE
            add(f"corr2.title_truncation.{scheme or 'bare'}.{cut}",
                "x" * padding + " " + credential + "@synthetic.invalid", corr2.NEEDLE)
    for point in corr2.INVISIBLES:
        add(f"corr2.title_invisible.{point:#x}",
            "https://synthetic.invalid/?pass" + chr(point) + "word=" + corr2.NEEDLE, corr2.NEEDLE)
    for word in ("pαssword", "pаsswοrd", "passwоrd"):
        add(f"corr2.confusable.{word.encode('unicode_escape').decode()}",
            "https://synthetic.invalid/?" + word + "=" + corr2.NEEDLE, corr2.NEEDLE)
    for point in corr2.INVISIBLES:
        for encoded in (False, True):
            marker = quote(chr(point), safe="") if encoded else chr(point)
            add(f"corr2.query_invisible.{point:#x}.{'encoded' if encoded else 'raw'}",
                "https://synthetic.invalid/?pass" + marker + "word=" + corr2.NEEDLE, corr2.NEEDLE)
    for prefix in ("", "https://", "anything://"):
        value = prefix + "synthetic:" + corr2.NEEDLE
        add(f"corr2.string_bound.{prefix or 'bare'}",
            "x" * (MAX_TEXT - len(value) - 1) + " " + value + "@synthetic.invalid", corr2.NEEDLE)

    # test_pr294_final32_review
    for point in (9, 10, 13):
        for rounds in range(3):
            marker = chr(point)
            for _ in range(rounds):
                marker = quote(marker, safe="")
            add(f"final32.control_password.{point:#x}.{rounds}",
                "https://synthetic.invalid/?pass" + marker + "word=" + final32.NEEDLE, final32.NEEDLE)
    for point in (0x1F, 0x200E, 0xD800, 0xE123, 0x378, 0x2028, 0x2029, 0x034F, 0x115F, 0xFE0F):
        for rounds in range(3):
            text = "Probe" + chr(point) + "tail"
            for _ in range(rounds):
                text = quote(text, safe="", errors="surrogatepass")
            add(f"final32.invisible.{point:#x}.{rounds}", text, None)
    add("final32.uri", final32.URI, final32.NEEDLE)
    return cases


class NativeFieldOverlay:
    """One native field's value replaced; the real fixture traversal and cap kept."""

    def __init__(self, field: str, value: bytes):
        self.real = hj.load_library()
        self.field = field.encode()
        self.buffer = ctypes.create_string_buffer(self.field + b"=" + value)

    def __getattr__(self, name):
        return getattr(self.real, name)

    def get_data(self, handle, field, pointer, size):
        if field == self.field:
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(self.buffer)
            ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t))[0] = len(self.buffer.raw) - 1
            return 0
        return self.real.get_data(handle, field, pointer, size)


class Harness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.helper = author.JournalServiceTests()
        await self.helper.asyncSetUp()
        # The helper never runs, so its own cleanups would need an event loop
        # it does not have; remove its fixture root here.
        self.addCleanup(author.shutil.rmtree, self.helper.journal, True)

    def service(self, journal_reader) -> IncidentLogService:
        service = self.helper.make_service(journal_reader)
        # The configuration-definitions suites' known secrets, too.
        service.static_secrets = (author.KNOWN_SECRET, t.HA_TOKEN, t.ACCESS_SECRET)
        return service

    async def planted(self, value: bytes, field: str = "MESSAGE", source: str = "supervisor", **arguments):
        library = NativeFieldOverlay(field, value)
        service = self.service(author.reader(self.helper.journal, library=lambda: library))
        start = 7 if source == "host:kernel" else 0
        arguments = {"sources": [source], "start": author.iso(start), "end": author.iso(start + 1), **arguments}
        return await self.helper.read(service=service, **arguments)


class CrossPathCredentialTests(Harness):
    """HAMCP-224 H1/H2: every HAMCP-132 credential case, through the journal path."""

    def test_the_journal_screen_is_the_configuration_definitions_screen(self):
        # One screen: the reader holds no credential pattern of its own.
        source = (author.PACKAGE / "incident_logs" / "host_journal.py").read_text(encoding="utf-8")
        # HAMCP-234 step 2: the journal mode is the labelled forms plus the
        # contiguous ``user_pass_host``, options of the same shared screen.
        self.assertIn("credentials.screen(text, known_secrets, **_JOURNAL_SCREEN)", source)
        self.assertIn('_JOURNAL_SCREEN = MappingProxyType({"labelled": True, "contiguous_user_pass_host": True})',
                      source)
        for private in ("_JOURNAL_KEYS", "_KEY_FORM", "journal_credential_text", "credential_text(",
                        "unverifiable_text(", "squash("):
            self.assertNotIn(private, source)

    def test_every_case_is_withheld_by_the_reader_screen(self):
        secrets = (author.KNOWN_SECRET, t.HA_TOKEN, t.ACCESS_SECRET)
        for label, raw, _needle in credential_cases():
            truncated = len(raw) > hj.MAX_FIELD_BYTES
            with self.subTest(case=label):
                text, omission = hj.screen_text(raw[:hj.MAX_FIELD_BYTES], secrets, truncated)
                self.assertIn(text, MARKERS)
                self.assertIn(omission, (hj.WITHHELD_OMISSION, hj.UNVERIFIABLE_OMISSION))

    async def test_every_case_is_withheld_in_the_message_and_returned_fields(self):
        for field, source in (("MESSAGE", "supervisor"), ("SYSLOG_IDENTIFIER", "host:kernel"),
                              ("_KERNEL_SUBSYSTEM", "host:kernel"), ("_UDEV_SYSNAME", "host:kernel")):
            for label, raw, needle in credential_cases():
                with self.subTest(field=field, case=label):
                    page = await self.planted(raw, field, source)
                    rendered = json.dumps(page, ensure_ascii=False)
                    if needle is not None:
                        self.assertNotIn(needle.casefold(), rendered.casefold())
                    self.assertTrue(page["entries"])
                    self.assertTrue(all("withheld" in entry for entry in page["entries"]))
                    self.assertFalse(page["complete"])
                    self.assertEqual(page["sources"][0]["status"], "partial")

    async def test_ordinary_text_stays_returned_and_complete(self):
        for value in ("Kitchen 20 °C, Küche 😀\nUSB reconnected", "keyboard: usb 1-1",
                      "mosquitto -p 1883 -c /etc/mosquitto.conf"):
            with self.subTest(value=value):
                page = await self.planted(value.encode())
                self.assertEqual(page["entries"][0]["message"], value)
                self.assertTrue(page["complete"])


class CompletenessTests(Harness):
    """HAMCP-224 M1: anything withheld, returned or filtered out, makes the read partial."""

    def password_text(self) -> bytes:
        return ("pass" + "word=" + "synthetic" + "Q" * 8).encode()

    async def test_the_final_envelope_scan_names_each_withheld_entry(self):
        page = await self.planted(self.password_text())
        self.assertIn(credentials.WITHHELD_ITEM_PREFIX + "entries[0]", page["omissions"])
        self.assertIn(hj.WITHHELD_OMISSION, page["omissions"])
        self.assertFalse(page["complete"])
        self.assertEqual(page["completeness"], "partial")

    async def test_a_withheld_returned_field_is_reported(self):
        page = await self.planted(self.password_text(), "_UDEV_SYSNAME", "host:kernel")
        entry = page["entries"][0]
        self.assertEqual((entry["kernel_device"], entry["withheld"]), (hj.WITHHELD_MARKER, hj.WITHHELD_OMISSION))
        row = page["sources"][0]
        self.assertEqual((row["status"], row["withheld_fields"], row["withheld_messages"]), ("partial", 1, 0))
        self.assertIn(credentials.WITHHELD_ITEM_PREFIX + "entries[0]", page["omissions"])

    async def test_a_cut_returned_field_is_unverifiable(self):
        page = await self.planted(b"usb" * (hj.MAX_FIELD_BYTES // 3 + 1), "_KERNEL_SUBSYSTEM", "host:kernel")
        entry = page["entries"][0]
        self.assertEqual((entry["kernel_subsystem"], entry["withheld"]),
                         (hj.UNVERIFIABLE_MARKER, hj.UNVERIFIABLE_OMISSION))
        self.assertFalse(page["complete"])

    async def test_a_withheld_entry_the_filter_left_out_is_reported(self):
        page = await self.planted(self.password_text(), text="ordinary")
        row = page["sources"][0]
        self.assertEqual((page["returned"], row["skipped_by_filter"], row["withheld_not_searched"], row["status"]),
                         (0, 1, 1, "partial"))
        self.assertIn("withheld_not_searched:supervisor", page["omissions"])
        self.assertFalse(page["complete"])
        # Left out by its level (review 2 M1 residual): still disclosed, in
        # its own count, and the read is partial.
        page = await self.planted(self.password_text(), min_level="critical")
        row = page["sources"][0]
        self.assertEqual((page["returned"], row["withheld_not_searched"], row["withheld_level_filtered"],
                          row["status"]), (0, 0, 1, "partial"))
        self.assertIn("withheld_level_filtered:supervisor", page["omissions"])
        self.assertFalse(page["complete"])

    async def test_the_omission_list_is_bounded(self):
        real = author.reader(self.helper.journal)
        planted = self.password_text()

        class Planting:
            def read(self, plans, digest, deadline):
                result = real.read(plans, digest, deadline)
                for scan in result.scans.values():
                    scan.records = [dataclasses.replace(record, message=planted) if record.rejected is None
                                    else record for record in scan.records]
                return result
        sources = ["host", "supervisor", "addon:5c53de3b_esphome", "addon:core_mosquitto"]
        page = await self.helper.read(service=self.service(Planting()), sources=sources, limit=c.MAX_LIMIT)
        listed = [item for item in page["omissions"] if item.startswith(credentials.WITHHELD_ITEM_PREFIX)]
        self.assertLessEqual(len(listed), credentials.MAX_WITHHELD_ITEMS)
        if page["returned"] > credentials.MAX_WITHHELD_ITEMS:
            self.assertIn(f"{credentials.WITHHELD_ITEMS_NOT_LISTED}:"
                          f"{page['returned'] - credentials.MAX_WITHHELD_ITEMS}", page["omissions"])
        self.assertNotIn("synthetic" + "Q" * 8, json.dumps(page))


class MicrosecondOrderTests(Harness):
    """HAMCP-224 M2: the merge reads the full microsecond realtime."""

    def timing(self, deltas: dict[str, int]):
        real = author.reader(self.helper.journal)

        class Timing:
            def read(self, plans, digest, deadline):
                result = real.read(plans, digest, deadline)
                for source, delta_us in deltas.items():
                    scan = result.scans[source]
                    first = next(record for record in scan.records if record.rejected is None)
                    scan.records = [dataclasses.replace(first, rt_us=author.at(0) + delta_us)]
                    scan.finished, scan.clock_regressions = "tail_reached", 0
                return result
        return Timing()

    async def test_sub_millisecond_order_across_sources(self):
        for deltas, expected in (({"supervisor": 1900, "host:kernel": 1100}, ["host:kernel", "supervisor"]),
                                 ({"supervisor": 1100, "host:kernel": 1900}, ["supervisor", "host:kernel"]),
                                 ({"supervisor": 1999, "host:kernel": 1001}, ["host:kernel", "supervisor"])):
            with self.subTest(deltas=deltas):
                page = await self.helper.read(service=self.service(self.timing(deltas)),
                                              sources=["supervisor", "host:kernel"])
                self.assertEqual([entry["source"] for entry in page["entries"]], expected)
                # Both are shown at the same millisecond.
                self.assertEqual(len({entry["ts"] for entry in page["entries"]}), 1)

    async def test_equal_realtimes_follow_the_requested_source_order(self):
        for sources in (["supervisor", "host:kernel"], ["host:kernel", "supervisor"]):
            with self.subTest(sources=sources):
                timing = self.timing({"supervisor": 1500, "host:kernel": 1500})
                page = await self.helper.read(service=self.service(timing), sources=sources)
                self.assertEqual([entry["source"] for entry in page["entries"]], sources)

    def test_the_continuation_keeps_the_microsecond_key(self):
        from ha_mcp_engineering.incident_logs import service as service_module
        record = hj.Record(author.at(0) + 1234, "0" * 32)
        item = service_module.Item("", 0, "0" * 32, None, True, key=(author.at(0) + 1234) // 1000,
                                   key_us=author.at(0) + 1234, record=record)
        scan = service_module.Scan(key="host", position=0, journal=True, items=[item])
        state = IncidentLogService._journal_next_state(scan, 1)
        self.assertEqual(state["t"], author.at(0) + 1234)
        # A gateway item's microsecond key is its millisecond key, scaled.
        self.assertEqual(service_module.Item("c", 0, "d", None, True, key=5).key_us, 5000)


class ProbeABI:
    """The reviewer's oversized probe: every field at the field bound, every step counted."""

    def __init__(self):
        self.buffers, self.identifier, self.steps, self.fields = {}, b"app_x", 0, 0

    def flush_matches(self, handle):
        pass

    def add_disjunction(self, handle):
        return 0

    def add_match(self, handle, data, size):
        self.identifier = data.split(b"=", 1)[1]
        return 0

    def seek_head(self, handle):
        return 0

    def seek_tail(self, handle):
        return 0

    def seek_realtime_usec(self, handle, usec):
        return 0

    def next(self, handle):
        self.steps += 1
        return 1

    previous = next

    def get_realtime_usec(self, handle, pointer):
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint64))[0] = 1
        return 0

    def get_cursor(self, handle, pointer):
        self.cursor = ctypes.create_string_buffer(b"synthetic-cursor")
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(self.cursor)
        return 0

    def free(self, pointer):
        pass

    def get_data(self, handle, name, data, size):
        self.fields += 1
        value = (self.identifier if name == b"SYSLOG_IDENTIFIER" else b"journal" if name == b"_TRANSPORT"
                 else b"x" * hj.MAX_FIELD_BYTES)
        buffer = ctypes.create_string_buffer(name + b"=" + value)
        self.buffers[name] = buffer
        ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(buffer)
        ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t))[0] = len(buffer.raw) - 1
        return 0


class SharedBoundTests(unittest.TestCase):
    """HAMCP-224 M3: every step and every copy, edge probes included, within the shared bounds."""

    def run_probe(self, deadline: float, sources=("addon:a", "addon:b", "addon:c", "addon:d")):
        library, result = ProbeABI(), hj.JournalRead()
        session = hj._Session(library, None, lambda *parts: "0" * 32, deadline, result)
        for key in sources:
            scan = hj.SourceScan(key)
            result.scans[key] = scan
            session._scan(hj.Plan(key, 0, 10, 100), scan)
        return library, result

    def test_the_oversized_probe_stays_within_the_call_field_bytes(self):
        library, result = self.run_probe(time.monotonic() + 30)
        self.assertLessEqual(result.field_bytes, hj.MAX_CALL_FIELD_BYTES)
        self.assertTrue(all(scan.stopped == "field_bytes_limit" for scan in result.scans.values()))
        # Every native step is counted, the edge probes' included.
        self.assertEqual(library.steps, sum(scan.scanned + scan.resume_walked + scan.probed
                                            for scan in result.scans.values()))
        self.assertGreater(sum(scan.probed for scan in result.scans.values()), 0)

    def test_edge_probe_steps_count_toward_the_entry_bound(self):
        with patch.object(hj, "MAX_SCAN_ENTRIES", 5), patch.object(hj, "MAX_CALL_FIELD_BYTES", 1 << 40):
            library, result = self.run_probe(time.monotonic() + 30, ("addon:a",))
        scan = result.scans["addon:a"]
        self.assertEqual(scan.stopped, "scan_entries_limit")
        self.assertEqual(library.steps, 5)
        self.assertEqual(scan.scanned + scan.resume_walked + scan.probed, 5)

    def test_an_expired_deadline_takes_no_step_and_reads_no_field(self):
        library, result = self.run_probe(time.monotonic() - 1)
        self.assertEqual((library.steps, library.fields, result.field_bytes), (0, 0, 0))
        self.assertTrue(all(scan.stopped == "deadline_reached" for scan in result.scans.values()))

    def test_no_field_is_read_once_the_deadline_passes_mid_entry(self):
        library, result = ProbeABI(), hj.JournalRead()
        clock = iter(range(10_000))
        with patch.object(hj.time, "monotonic", lambda: next(clock)):
            session = hj._Session(library, None, lambda *parts: "0" * 32, 40, result)
            scan = hj.SourceScan("addon:a")
            result.scans["addon:a"] = scan
            session._scan(hj.Plan("addon:a", 0, 10, 100), scan)
        self.assertEqual(scan.stopped, "deadline_reached")
        # Each field read checks the deadline first: at most one read per tick.
        self.assertLessEqual(library.fields, 40)


class SharedBoundServiceTests(Harness):
    async def test_a_page_of_fields_at_the_field_bound_stays_within_the_response_budget(self):
        page = await self.planted(b"x" * hj.MAX_FIELD_BYTES, start=author.iso(0), end=author.iso(7000),
                                  limit=c.MAX_LIMIT)
        self.assertLessEqual(len(json.dumps(page, separators=(",", ":"), ensure_ascii=True)), c.RESPONSE_BUDGET)
        self.assertLessEqual(page["journal_field_bytes"], hj.MAX_CALL_FIELD_BYTES)
        self.assertTrue(all(len(entry["message"]) <= c.MAX_MESSAGE_CHARS + 64 for entry in page["entries"]))


if __name__ == "__main__":
    unittest.main()
