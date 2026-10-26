"""HAMCP-234 journal usability: rule ids, calibration counts, container levels, the description.

Offline: the HAMCP-224 real-format fixture journals, read through the same
``libsystemd.so.0`` binding, with entries rewritten in memory. No live Home
Assistant, no host journal. Every credential-shaped payload is assembled at
run time (from the HAMCP-132 and PR #294 suites' builders or by
concatenation), so this file holds no secret-shaped literal. Nothing here
changes what is withheld: every decision is still ``credentials.screen``'s.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))
sys.path.insert(0, str(TESTS.parent / "hass_mcp_engineering_beta"))

import test_hamcp224_host_journal as author  # noqa: E402
import test_hamcp224_review1_corrections as review1  # noqa: E402
import test_hamcp224_review2_corrections as review2  # noqa: E402

from ha_mcp_engineering.configuration_definitions import credentials  # noqa: E402
from ha_mcp_engineering.incident_logs import container_levels as cl  # noqa: E402
from ha_mcp_engineering.incident_logs import contracts as c  # noqa: E402
from ha_mcp_engineering.incident_logs import service as service_module  # noqa: E402
from ha_mcp_engineering.sanitization import sanitize_untrusted_data  # noqa: E402

hj = author.hj
NEEDLE = "SYNTHETIC" + "calibration234"
KNOWN = "zq" + "xv" + "wk" + "jm"
BENIGN = "Synthetic ordinary line " + "4471-calibration"


def deciding_cases() -> dict[str, tuple[bytes, bool]]:
    """``rule id -> (raw field, truncated)``: a field each rule is the deciding rule for."""

    def b(text: str) -> tuple[bytes, bool]:
        return text.encode(), False

    at, colon = "@", ":"
    cases = {
        credentials.RULE_INVISIBLE: b("a​b " + NEEDLE),
        credentials.RULE_CANONICAL_REFUSED: b("50% done %41 " + NEEDLE),
        "known_secret": b("value " + KNOWN),
        "known_secret.squashed": b("value " + KNOWN[:4] + " " + KNOWN[4:]),
        "credential_url": b("https://u" + colon + NEEDLE + at + "example.invalid/x"),
        "credential_url.squashed": b("see web" + colon + " //" + NEEDLE + at + "example.invalid"),
        "connection_uri": b("mqtt" + "://" + NEEDLE + ".example.invalid"),
        "connection_uri.squashed": b("mqtt" + colon + " //" + NEEDLE + ".example.invalid"),
        "user_pass_host": b("u" + colon + NEEDLE + at + "example.invalid"),
        "user_pass_host.squashed": b("u" + colon + " " + NEEDLE + at + "example.invalid"),
        "password_field": b("pass" + "wd =" + NEEDLE),
        "password_field.squashed": b("pass wd" + "=" + NEEDLE),
        "marker_collision": b("see " + credentials.WITHHELD_MARKER),
        "marker_collision.squashed": b("see [withheld" + colon + " credential_text]"),
        credentials.RULE_PASSWORD_OPTION: b("mosquitto_pub -" + "P " + NEEDLE),
        "value_shape:labelled_field": b("ps" + "k" + colon + " " + NEEDLE),
        "value_shape:labelled_field.squashed": b("ps k" + colon + " " + NEEDLE),
        hj.RULE_FIELD_TOO_LONG: ((NEEDLE + " ").encode() * 4, True),
        hj.RULE_NOT_UTF8: (NEEDLE.encode() + b"\xff", False),
        hj.RULE_UNVERIFIABLE_ESCAPE: b("a\x1b[pb " + NEEDLE),
    }
    words = {"pw_option": "--pw", "api_key": "api key", "private_key": "private key"}
    split = {"pw_option": "- -pw", "api_key": "a pi key", "private_key": "pri vate key"}
    for name in ("bearer", "password", "passwd", "pwd", "pw_option", "authorization", "api_key", "secret",
                 "token", "private_key"):
        word = words.get(name, name)
        cases["labelled_keyword:" + name] = b("the " + word + " " + NEEDLE)
        cases["labelled_keyword:" + name + ".squashed"] = b("the " + split.get(name, word[:2] + " " + word[2:])
                                                             + " " + NEEDLE)
    return cases


# A keyword rule always decides these first (``bearer``, ``private key``):
# they are only ever counted in ``matched_by_rule``.
MATCHING_ONLY = {
    "value_shape:bearer_token": ("Bear" + "er " + NEEDLE, "labelled_keyword:bearer"),
    "value_shape:bearer_token.squashed": ("Be ar" + "er " + NEEDLE, "labelled_keyword:bearer.squashed"),
    "pem_header": ("-----BEGIN RSA PRIV" + "ATE KEY-----", "labelled_keyword:private_key"),
    "pem_header.squashed": ("BEGIN RSA PRI VATE " + "KEY", "labelled_keyword:private_key.squashed"),
}


class RuleIdTests(unittest.TestCase):
    """A: every withheld field names its rule, a fixed id; the decision is unchanged."""

    def test_each_rule_produces_its_id(self):
        for rule, (raw, truncated) in deciding_cases().items():
            with self.subTest(rule=rule):
                text, omission, got = hj.screen_text_rule(raw, (KNOWN,), truncated)
                self.assertEqual(got, rule)
                self.assertEqual((text, omission), hj.screen_text(raw, (KNOWN,), truncated))
                self.assertIn(text, review1.MARKERS)
                self.assertEqual(hj.screen_text_rules(raw, (KNOWN,), truncated)[0], rule)
        # The password-option scan bound is the shared screen's (wider than a journal field).
        _marker, rule = credentials.screen_rule("a " * (credentials.MAX_OPTION_SCAN // 2 + 1), (), labelled=True)
        self.assertEqual(rule, credentials.RULE_PASSWORD_OPTION_SCAN_LIMIT)

    def test_the_matching_only_rules_are_counted_beside_their_keyword(self):
        for rule, (text, decided) in MATCHING_ONLY.items():
            with self.subTest(rule=rule):
                self.assertEqual(hj.screen_text_rule(text.encode(), ())[2], decided)
                self.assertIn(rule, hj.screen_text_rules(text.encode(), ()))

    def test_every_rule_id_is_covered_and_fixed(self):
        covered = set(deciding_cases()) | set(MATCHING_ONLY) | {credentials.RULE_PASSWORD_OPTION_SCAN_LIMIT}
        self.assertEqual(covered, set(hj.RULE_IDS))
        self.assertEqual(len(hj.RULE_IDS), 45)
        self.assertNotIn(hj.RULE_UNLISTED, hj.RULE_IDS)
        for rule in sorted(hj.RULE_IDS | {hj.RULE_UNLISTED}):
            with self.subTest(rule=rule):
                # No digit (beyond the fixed "utf8"): an id never carries a length, an offset or a count.
                self.assertRegex(rule.replace("utf8", "utf"), r"\A[a-z_:.]{3,48}\Z")
                result = sanitize_untrusted_data({"rule": rule, "rules": {"message": rule}})
                self.assertFalse(result.failed_closed)
                self.assertEqual(result.redacted_field_count, 0)
                self.assertEqual(result.value, {"rule": rule, "rules": {"message": rule}})

    def test_the_rule_never_changes_the_decision(self):
        secrets = (author.KNOWN_SECRET, review1.t.HA_TOKEN, review1.t.ACCESS_SECRET)
        inputs = [(raw[:hj.MAX_FIELD_BYTES], len(raw) > hj.MAX_FIELD_BYTES)
                  for _label, raw, _needle in review1.credential_cases()]
        inputs += list(deciding_cases().values())
        inputs += [(text.encode(), False) for text in (BENIGN, "\x1b[31mordinary\x1b[0m", "token", "", "a\nb")]
        for raw, truncated in inputs:
            with self.subTest(raw=raw[:80]):
                text, omission, rule = hj.screen_text_rule(raw, secrets, truncated)
                self.assertEqual((text, omission), hj.screen_text(raw, secrets, truncated))
                self.assertEqual(rule is None, omission is None)
                rules = hj.screen_text_rules(raw, secrets, truncated)
                self.assertEqual(bool(rules), omission is not None)
                self.assertTrue(set(rules) <= hj.RULE_IDS, rules)
                if rule is not None:
                    self.assertIn(rule, hj.RULE_IDS)
                    self.assertEqual(rules[0], rule)
        # The shared screen's own decision and its rule agree, labelled or not.
        for label, raw, _needle in review1.credential_cases():
            value = raw.decode("utf-8", "surrogatepass")
            for labelled in (False, True):
                with self.subTest(case=label, labelled=labelled):
                    marker, rule = credentials.screen_rule(value, secrets, labelled=labelled)
                    self.assertEqual(marker, credentials.screen(value, secrets, labelled=labelled))
                    self.assertEqual(rule is None, marker is None)
                    self.assertEqual(credentials.screen_rules(value, secrets, labelled=labelled)[:1],
                                     () if rule is None else (rule,))

    def test_no_rule_id_carries_the_withheld_text(self):
        for label, raw, needle in review1.credential_cases():
            _text, omission, rule = hj.screen_text_rule(raw[:hj.MAX_FIELD_BYTES], (),
                                                        len(raw) > hj.MAX_FIELD_BYTES)
            with self.subTest(case=label):
                self.assertIsNotNone(omission)
                self.assertIn(rule, hj.RULE_IDS)
                if needle:
                    self.assertNotIn(needle.casefold(), rule)


class RowRuleTests(review1.Harness):
    """A, through the read: ``withheld_rules`` on withheld entries only."""

    async def test_a_withheld_message_and_field_name_their_rules(self):
        page = await self.planted(("pass" + "word=" + NEEDLE).encode())
        [entry] = page["entries"]
        self.assertEqual(entry["withheld_rules"], {"message": "password_field"})
        self.assertNotIn(NEEDLE, json.dumps(page))
        page = await self.planted(("api" + "_key=" + NEEDLE).encode(), "SYSLOG_IDENTIFIER", "host:kernel")
        [entry] = page["entries"]
        self.assertEqual(entry["withheld_rules"], {"process": "labelled_keyword:api_key"})
        self.assertNotIn(NEEDLE, json.dumps(page))

    async def test_shown_entries_carry_no_rules(self):
        page = await self.helper.read(sources=list(author.ALL_JOURNAL_SOURCES[:4]))
        for entry in page["entries"]:
            self.assertEqual("withheld_rules" in entry, "withheld" in entry, entry)

    async def test_planted_secret_sweep_every_case_names_a_listed_rule(self):
        for label, raw, needle in review1.credential_cases():
            for field, source, key in review2.FIELDS if label.startswith("delta.forms") else review2.FIELDS[:1]:
                with self.subTest(case=label, field=field):
                    page = await self.planted(raw, field, source)
                    [entry] = page["entries"]
                    self.assertIn(entry["withheld_rules"][key], hj.RULE_IDS)
                    text = json.dumps(page, ensure_ascii=False)
                    if needle:
                        self.assertNotIn(needle, text)
                    self.assertNotIn(str(len(raw)), "".join(entry["withheld_rules"].values()))


ROW_KEYS = {"source", "status", "reason", "reader", "start_location", "retention_edge_reached", "scanned_from",
            "scanned_to", "finished", "scanned", "shown", "withheld_entries", "withheld_messages",
            "withheld_fields", "withheld_by_rule", "matched_by_rule", "match_rejected", "entries_unreadable",
            "gaps"}
ENVELOPE_KEYS = {"schema", "mode", "window", "sources", "rule_ids", "truncated", "omissions", "complete",
                 "completeness", "requests", "journal_field_bytes", "bounds", "pins", "limitations"}
ISO = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")


class Rewriting:
    """The fixture reader, with one source's entries replaced by ``(message, priority)`` pairs."""

    def __init__(self, journal: str, key: str, entries: list[tuple[bytes, bytes]]):
        self.real = author.reader(journal)
        self.key, self.entries = key, entries

    def read(self, plans, digest, deadline):
        result = self.real.read(plans, digest, deadline)
        scan = result.scans.get(self.key)
        if scan is None:
            return result
        base = next(record for record in scan.records if record.rejected is None)
        scan.records = [dataclasses.replace(base, rt_us=base.rt_us + n * author.SECOND_US,
                                            cursor_digest=digest("hamcp234", n.to_bytes(2, "big")),
                                            message=message, priority=priority, message_truncated=False)
                        for n, (message, priority) in enumerate(self.entries)]
        return result


class CalibrationTests(review1.Harness):
    """B: counts only, the same screen and window as a read, and nothing else."""

    async def calibrate(self, service=None, **arguments) -> dict:
        return await self.helper.read(service=service, calibration=True, **arguments)

    def assert_counts_only(self, page: dict, sources: list[str]) -> None:
        self.assertEqual(set(page), ENVELOPE_KEYS)
        self.assertEqual((page["mode"], page["truncated"]), ("calibration", False))
        self.assertEqual(page["rule_ids"], sorted(hj.RULE_IDS))
        self.assertEqual(page["limitations"], list(service_module.CALIBRATION_LIMITATIONS))
        self.assertEqual(page["pins"], {"host_journal_library": hj.LIBSYSTEMD_PIN,
                                        "host_identifiers": hj.IDENTIFIERS_PIN})
        self.assertTrue(all(type(value) in (int, float) for value in page["bounds"].values()))
        self.assertEqual([row["source"] for row in page["sources"]], sources)
        for omission in page["omissions"]:
            self.assertTrue(any(omission.endswith(":" + key) for key in sources), omission)
        for row in page["sources"]:
            self.assertTrue(set(row) - ROW_KEYS <= {"detail", "errno"}, set(row))
            self.assertTrue(ROW_KEYS <= set(row))
            self.assertEqual(row["reader"], hj.READER)
            self.assertIn(row["status"], {"complete", "partial", "failed", "unavailable"})
            for name in ("scanned_from", "scanned_to"):
                self.assertTrue(row[name] is None or ISO.match(row[name]), row[name])
            for name in ("scanned", "shown", "withheld_entries", "withheld_messages", "match_rejected",
                         "entries_unreadable"):
                self.assertIs(type(row[name]), int)
            self.assertTrue(set(row["withheld_fields"]) <= {"process", "kernel_subsystem", "kernel_device"})
            for name in ("withheld_by_rule", "matched_by_rule"):
                self.assertTrue(set(row[name]) <= hj.RULE_IDS | {hj.RULE_UNLISTED}, row[name])
                self.assertTrue(all(type(count) is int for count in row[name].values()))
            for gap in row["gaps"]:
                self.assertTrue(set(gap) <= {"from", "to", "reason"})
            if "detail" in row:
                self.assertIn(row["detail"], hj.UNAVAILABLE_DETAIL.values())

    async def test_the_fixture_journal_calibrates_to_counts_only(self):
        sources = list(author.ALL_JOURNAL_SOURCES)
        pages = [await self.calibrate(sources=sources[n:n + c.MAX_SOURCES])
                 for n in range(0, len(sources), c.MAX_SOURCES)]
        for page in pages:
            self.assert_counts_only(page, [row["source"] for row in page["sources"]])
        self.assertEqual([row["source"] for page in pages for row in page["sources"]], sources)
        text = json.dumps(pages, ensure_ascii=False)
        for value in author.NEVER_RETURNED + (author.KNOWN_SECRET,):
            self.assertNotIn(value, text)
        # No message or process of the normal read reaches the calibration.
        reads = [await self.helper.read_all(sources=[key], limit=7) for key in sources]
        shown = {value for pages in reads for p in pages for entry in p["entries"] if "withheld" not in entry
                 for value in (entry["message"], entry["process"]) if value}
        self.assertTrue(shown)
        for value in shown:
            if len(value) >= 8 and not any(value in key for key in sources):
                self.assertNotIn(value, text)

    async def test_counts_match_the_normal_read(self):
        sources = list(author.ALL_JOURNAL_SOURCES)
        rows = [row for n in range(0, len(sources), c.MAX_SOURCES)
                for row in (await self.calibrate(sources=sources[n:n + c.MAX_SOURCES]))["sources"]]
        for row in rows:
            with self.subTest(source=row["source"]):
                pages = await self.helper.read_all(sources=[row["source"]], limit=7)
                entries = [entry for p in pages for entry in p["entries"]]
                withheld = [entry for entry in entries if "withheld" in entry]
                self.assertEqual(row["scanned"], len(entries))
                self.assertEqual((row["shown"], row["withheld_entries"]),
                                 (len(entries) - len(withheld), len(withheld)))
                self.assertEqual(row["withheld_messages"],
                                 sum(1 for entry in withheld if "message" in entry["withheld_rules"]))
                deciding: dict[str, int] = {}
                for entry in withheld:
                    for rule in entry["withheld_rules"].values():
                        deciding[rule] = deciding.get(rule, 0) + 1
                self.assertEqual(row["withheld_by_rule"], dict(sorted(deciding.items())))
                last = pages[-1]["sources"][0]
                self.assertEqual((row["match_rejected"], row["entries_unreadable"]),
                                 (sum(p["sources"][0]["match_rejected"] for p in pages),
                                  sum(p["sources"][0]["entries_unreadable"] for p in pages)))
                self.assertEqual(row["start_location"], pages[0]["sources"][0]["start_location"])
                # Withheld entries never make a calibration partial; rejected or unreadable entries do.
                self.assertEqual(row["status"] == "complete", last["finished"]
                                 and not row["match_rejected"] and not row["entries_unreadable"]
                                 and not row["gaps"])
                self.assertEqual((row["reason"], row["finished"]), (last["reason"], last["finished"]))

    async def test_no_text_under_any_input(self):
        inputs = [(label, raw, needle) for label, raw, needle in review1.credential_cases()]
        inputs += [(rule, raw, NEEDLE) for rule, (raw, truncated) in deciding_cases().items() if not truncated]
        inputs += [("benign", BENIGN.encode(), BENIGN), ("coloured", ("\x1b[31m" + BENIGN).encode(), BENIGN),
                   ("not_utf8", BENIGN.encode() + b"\xff", BENIGN)]
        for label, raw, needle in inputs:
            for field, source, _key in review2.FIELDS[:2]:
                with self.subTest(case=label, field=field):
                    library = review1.NativeFieldOverlay(field, raw)
                    service = self.service(author.reader(self.helper.journal, library=lambda: library))
                    start = 7 if source == "host:kernel" else 0
                    page = await self.calibrate(service=service, sources=[source], start=author.iso(start),
                                                end=author.iso(start + 1))
                    self.assert_counts_only(page, [source])
                    text = json.dumps(page, ensure_ascii=False)
                    if needle:
                        self.assertNotIn(needle, text)
                    row = page["sources"][0]
                    self.assertEqual(row["scanned"], 1)
                    held = row["withheld_entries"] == 1
                    normal = await self.helper.read(service=service, sources=[source], start=author.iso(start),
                                                    end=author.iso(start + 1))
                    self.assertEqual(held, "withheld" in normal["entries"][0])
                    if held:
                        self.assertEqual(row["withheld_by_rule"],
                                         {rule: 1 for rule in normal["entries"][0]["withheld_rules"].values()})

    async def test_rewritten_entries_count_by_rule(self):
        entries = [(BENIGN.encode(), b"6"), (("pass" + "word=" + NEEDLE).encode(), b"3"),
                   (("Bear" + "er " + NEEDLE).encode(), b"3"), (b"a\x1b[pb", b"3"), (b"\xff", b"3")]
        service = self.service(Rewriting(self.helper.journal, "supervisor", entries))
        page = await self.calibrate(service=service, sources=["supervisor"])
        self.assert_counts_only(page, ["supervisor"])
        row = page["sources"][0]
        self.assertEqual((row["scanned"], row["shown"], row["withheld_entries"], row["withheld_messages"]),
                         (5, 1, 4, 4))
        self.assertEqual(row["withheld_by_rule"], {"labelled_keyword:bearer": 1, "password_field": 1,
                                                   "not_utf8": 1, "unverifiable_escape": 1})
        # Every rule that matched is counted, not only the deciding one.
        self.assertEqual(row["matched_by_rule"]["value_shape:bearer_token"], 1)
        self.assertEqual(row["matched_by_rule"]["labelled_keyword:password"], 1)
        self.assertNotIn(NEEDLE, json.dumps(page))
        self.assertNotIn(BENIGN, json.dumps(page))
        # Withheld entries alone never make a calibration partial.
        self.assertEqual((row["status"], page["complete"]), ("complete", True))

    async def test_calibration_arguments_are_refused_outside_their_contract(self):
        base = {"sources": ["supervisor"], "start": author.iso(0), "end": author.iso(1)}
        cases = [{"calibration": "true"}, {"calibration": 1}, {"calibration": None},
                 {"calibration": True, "sources": ["core"]}, {"calibration": True, "sources": ["addon:self"]},
                 {"calibration": True, "sources": ["supervisor", "core"]},
                 {"calibration": True, "min_level": "warning"}, {"calibration": True, "text": "x"},
                 {"calibration": True, "cursor": "x" * 40}]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(c.IncidentLogsError) as caught:
                    await self.helper.read(**{**base, **arguments})
                self.assertEqual(caught.exception.reason, "invalid_arguments")
        # False is an ordinary read.
        page = await self.helper.read(**base, calibration=False)
        self.assertIn("entries", page)

    async def test_any_sanitizer_change_fails_closed(self):
        real = service_module.sanitize_untrusted_data

        def changing(value, **kwargs):
            result = real(value, **kwargs)
            if value.get("mode") == "calibration":
                return dataclasses.replace(result, redacted_field_count=1)
            return result
        with patch.object(service_module, "sanitize_untrusted_data", changing):
            with self.assertRaises(c.IncidentLogsError) as caught:
                await self.calibrate(sources=["supervisor"])
        self.assertEqual(caught.exception.reason, "sources_unavailable")


SUPERVISOR_09 = "2026-10-19 12:00:01.234 INFO (MainThread) [supervisor.core] Supervisor is up and running"
SUPERVISOR_10 = ("2026-10-19 12:00:01.234+02:00 WARNING (Thread-3 (_worker)) [supervisor.docker.manager] "
                 "Can't update image")
MATTER_ANSI = ("\x1b[2m2026-10-19 12:00:01.234 " + "INFO".ljust(6) + "\x1b[0m \x1b[1m"
               + "MatterServer".ljust(20) + "\x1b[0m Matter server started")
FORMATS = {
    SUPERVISOR_09: "info",
    SUPERVISOR_10: "warning",
    "2026-10-19 12:00:01.234 ERROR (SyncWorker_0) [supervisor.addons.addon] Add-on failed": "error",
    "2026-10-19 12:00:01.234 CRITICAL (MainThread) [supervisor.core] Fatal": "critical",
    "2026-10-19 12:00:01.234 DEBUG (MainThread) [supervisor.api] Request": "debug",
    "2026-10-19 12:00:01.234 " + "INFO".ljust(6) + " " + "MatterServer".ljust(20) + " started": "info",
    "2026-10-19 12:00:01.234 " + "NOTICE".ljust(6) + " " + "Node".ljust(20) + " up": "info",
    "2026-10-19 12:00:01.234 " + "WARN".ljust(6) + " " + "InteractionClient".ljust(20) + " retry": "warning",
    "2026-10-19 12:00:01.234 " + "ERROR".ljust(6) + " " + "MatterController".ljust(20) + " lost": "error",
    "2026-10-19 12:00:01.234 " + "FATAL".ljust(6) + " " + "Main".ljust(20) + " crashed": "critical",
    "2026-10-19 12:00:01.234 " + "DEBUG".ljust(6) + " " + "Transport".ljust(20) + " frame": "debug",
    "2026-10-19 12:00:01.234 WARN MatterServer plain format line": "warning",
    "WARNING:matter_server.server:Device unreachable": "warning",
    "INFO:root:started": "info",
    "2026-10-19 12:00:01,234 - zigbee2mqtt.bridge - ERROR - failed": "error",
    "[12:00:01] INFO: Starting Mosquitto": "info",
    "[12:00:01] WARNING: Option missing": "warning",
    "[12:00:01] FATAL: Cannot start": "critical",
    "[12:00:01] TRACE: step": "debug",
}
NO_FORMAT = (
    "CRITICAL: disk failed", "<2>kernel panic", "level=critical msg=down", "ERROR (MainThread) x",
    "Started 2026-10-19 12:00:01.234 ERROR (MainThread) [x] y", "info:root:lowercase",
    "2026-10-19 12:00:01.234 VERBOSE (MainThread) [supervisor] x", "2026-10-19 12:00:01.234 INFO",
    "ordinary line\n2026-10-19 12:00:01.234 ERROR (MainThread) [x] second line", "x" * 300 + SUPERVISOR_09,
    "[12:00:01]INFO: no space", "WARNING::empty name", "", "Traceback (most recent call last):",
)


class ContainerLevelTests(unittest.TestCase):
    """C: the level of a container line from its pinned text format, at the start of the line only."""

    def test_each_known_format_gives_its_level(self):
        for text, level in FORMATS.items():
            with self.subTest(text=text):
                self.assertEqual(cl.parse_level(text), level)

    def test_text_in_no_known_format_gives_none(self):
        for text in NO_FORMAT:
            with self.subTest(text=text[:60]):
                self.assertIsNone(cl.parse_level(text))

    def test_only_supervisor_and_addons_are_container_sources(self):
        self.assertTrue(cl.container_source("supervisor"))
        self.assertTrue(cl.container_source("addon:core_matter_server"))
        for key in sorted(key for key in hj.HOST_SOURCES if key.startswith("host")):
            self.assertFalse(cl.container_source(key), key)
        self.assertFalse(cl.container_source("core"))

    def test_the_level_decision_by_source_and_screen(self):
        level = service_module._journal_level
        self.assertEqual(level(True, b"3", SUPERVISOR_09), ("info", cl.BASIS))
        self.assertEqual(level(True, b"3", "no known format"), ("error", cl.PRIORITY_BASIS))
        self.assertEqual(level(True, b"3", None), ("error", cl.UNVERIFIED_BASIS))
        self.assertEqual(level(False, b"3", SUPERVISOR_09), ("error", hj.LEVEL_BASIS))
        self.assertEqual(level(False, b"3", None), ("error", hj.LEVEL_BASIS))


class ContainerLevelReadTests(review1.Harness):
    """C, through the read: stderr PRIORITY 3 container lines take their parsed level."""

    async def read_rewritten(self, key: str, entries: list[tuple[bytes, bytes]], **arguments) -> dict:
        service = self.service(Rewriting(self.helper.journal, key, entries))
        start = 7 if key == "host:kernel" else 0
        return await self.helper.read(service=service, sources=[key], start=author.iso(start), **arguments)

    async def test_stderr_info_lines_are_info(self):
        entries = [(SUPERVISOR_09.encode(), b"3"), (SUPERVISOR_10.encode(), b"3"),
                   (b"no known format here", b"3")]
        page = await self.read_rewritten("supervisor", entries)
        self.assertEqual([(entry["level"], entry["level_basis"]) for entry in page["entries"]],
                         [("info", "parsed_text"), ("warning", "parsed_text"), ("error", "journal_priority")])
        page = await self.read_rewritten("supervisor", entries, min_level="warning")
        self.assertEqual([entry["message"] for entry in page["entries"]], [SUPERVISOR_10, "no known format here"])
        self.assertTrue(page["complete"])

    async def test_an_addon_line_is_read_after_its_colour_codes_are_removed(self):
        page = await self.read_rewritten("addon:core_matter_server", [(MATTER_ANSI.encode(), b"3")])
        [entry] = page["entries"]
        self.assertEqual((entry["level"], entry["level_basis"]), ("info", "parsed_text"))
        self.assertNotIn("\x1b", entry["message"])

    async def test_host_and_kernel_sources_keep_their_priority(self):
        for key in ("host:os", "host:kernel"):
            with self.subTest(source=key):
                page = await self.read_rewritten(key, [(SUPERVISOR_09.encode(), b"3")])
                self.assertEqual((page["entries"][0]["level"], page["entries"][0]["level_basis"]),
                                 ("error", hj.LEVEL_BASIS))

    async def test_a_withheld_container_line_keeps_its_priority_unverified(self):
        secret_line = ("2026-10-19 12:00:01.234 INFO (MainThread) [supervisor.auth] pass" + "word=" + NEEDLE)
        for priority, level in ((b"3", "error"), (b"6", "info")):
            with self.subTest(priority=priority):
                page = await self.read_rewritten("supervisor", [(secret_line.encode(), priority)])
                [entry] = page["entries"]
                self.assertEqual((entry["level"], entry["level_basis"], entry["message"]),
                                 (level, "priority_unverified", hj.WITHHELD_MARKER))
                # Under min_level it is counted, never silently dropped.
                for min_level in ("debug", "warning", "critical"):
                    page = await self.read_rewritten("supervisor", [(secret_line.encode(), priority)],
                                                     min_level=min_level)
                    row = page["sources"][0]
                    self.assertEqual((page["returned"], row["withheld_level_filtered"], row["status"]),
                                     (0, 1, "partial"))
                    self.assertIn("withheld_level_filtered:supervisor", page["omissions"])
                    self.assertFalse(page["complete"])
                    self.assertNotIn(NEEDLE, json.dumps(page))

    async def test_a_withheld_host_line_keeps_its_priority_level_filter(self):
        # Host sources' PRIORITY is their level: the HAMCP-224 rule is unchanged.
        line = ("pass" + "word=" + NEEDLE).encode()
        page = await self.read_rewritten("host:os", [(line, b"3")], min_level="warning")
        self.assertEqual((page["returned"], page["sources"][0]["withheld_level_filtered"]), (1, 0))
        page = await self.read_rewritten("host:os", [(line, b"6")], min_level="warning")
        self.assertEqual((page["returned"], page["sources"][0]["withheld_level_filtered"]), (0, 1))


class DescriptionTests(unittest.TestCase):
    """D: journal sources need the add-on option journald and no manager role."""

    def test_the_description_and_limitations(self):
        from ha_mcp_engineering.tools import registry
        description = registry._SDK_TOOLS.get("get_incident_logs").description
        self.assertNotIn("need role manager", description)
        self.assertIn("no manager role", description)
        self.assertIn("journald", description)
        self.assertIn("calibration", description)
        self.assertIn("withheld_rules", description)
        tool = registry._SDK_TOOLS.get("get_incident_logs")
        self.assertEqual(tool.parameters["properties"]["calibration"].get("type"), "boolean")
        self.assertIs(tool.parameters["properties"]["calibration"].get("default"), False)
        limitations = " ".join(service_module.LIMITATIONS)
        self.assertIn("no manager role is needed", limitations)
        for word in ("parsed_text", "journal_priority", "priority_unverified", "withheld_level_filtered",
                     "withheld_rules"):
            self.assertIn(word, limitations)


if __name__ == "__main__":
    unittest.main()
