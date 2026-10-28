"""HAMCP-234 step 2: the journal's ``user_pass_host`` as one contiguous token, and calibration reachability.

Owner decision (Josh, 2026-10-27), from the beta.42 live calibration of
``addon:core_matter_server`` (37 of 40 lines withheld, every one by
``user_pass_host.squashed``): in the journal path only, ``user_pass_host``
is read in the normal form as one contiguous token and not in the squashed
form. Every other rule is unchanged in both forms; the configuration
definitions (HAMCP-132) and trace (HAMCP-225) paths are unchanged.

Live-shaped tests come first. Offline: the HAMCP-224 fixture journals with
entries rewritten in memory, the production MCP application and the
authenticated gateway in process. Every credential-shaped payload is
assembled at run time, so this file holds no secret-shaped literal.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))
sys.path.insert(0, str(TESTS.parent / "hass_mcp_engineering_beta"))

import hamcp234_step2_corpus as corpus  # noqa: E402
import test_hamcp131_incident_logs as gateway_tests  # noqa: E402
import test_hamcp224_host_journal as author  # noqa: E402
import test_hamcp224_review1_corrections as review1  # noqa: E402
import test_hamcp234_journal_usability as step1  # noqa: E402

from ha_mcp_engineering.configuration_definitions import credentials  # noqa: E402
from ha_mcp_engineering.trace_normalization import screen_trace_value  # noqa: E402

hj = author.hj
NEEDLE = "SYNTHETIC" + "relax234"
KNOWN = "zq" + "xv" + "wk" + "jm"
MATTER = "addon:core_matter_server"
AT, COLON = "@", ":"


def contiguous_credentials() -> dict[str, tuple[str, set[str]]]:
    """``label -> (suffix, deciding rules)``: a real contiguous credential to append to a line."""

    return {
        "scheme_user_pass_host": (" https://u" + COLON + NEEDLE + AT + "broker.invalid/x", {"credential_url"}),
        "connection_uri": (" mqtt://u" + COLON + NEEDLE + AT + "core-mosquitto:1883",
                           {"credential_url", "connection_uri"}),
        "user_pass_host": (" u" + COLON + NEEDLE + AT + "broker.invalid", {"user_pass_host"}),
    }


def labelled_credentials() -> dict[str, tuple[str, set[str]]]:
    """Labelled credentials and known secrets, each in its normal and its split (squashed-only) form."""

    return {
        "password_field": (" pass" + "word=" + NEEDLE, {"password_field"}),
        "password_field.squashed": (" pass word" + "=" + NEEDLE, {"password_field.squashed"}),
        "password_option": (" mosquitto_pub -" + "P " + NEEDLE, {"password_option"}),
        "password_option.split": (" mosquitto_pub -\t" + "P " + NEEDLE, {"password_option"}),
        "bearer": (" Bear" + "er " + NEEDLE, {"labelled_keyword:bearer"}),
        "bearer.squashed": (" Be ar" + "er " + NEEDLE, {"labelled_keyword:bearer.squashed"}),
        "token": (" tok" + "en " + NEEDLE, {"labelled_keyword:token"}),
        "token.squashed": (" to ken " + NEEDLE, {"labelled_keyword:token.squashed"}),
        "labelled_field": (" ps" + "k" + COLON + " " + NEEDLE, {"value_shape:labelled_field"}),
        "known_secret": (" value " + KNOWN, {"known_secret"}),
        "known_secret.squashed": (" value " + KNOWN[:4] + " " + KNOWN[4:], {"known_secret.squashed"}),
        "pem_header": (" -----BEGIN RSA PRIV" + "ATE KEY-----", {"labelled_keyword:private_key"}),
        "invisible": (" a​b " + NEEDLE, {credentials.RULE_INVISIBLE}),
    }


class LiveShapedLineTests(unittest.TestCase):
    """The live-shaped lines first: matter.js shown, every credential in them still withheld."""

    def test_live_shaped_matter_js_lines_are_shown(self):
        for line in corpus.MATTER_JS:
            with self.subTest(line=line[24:72]):
                self.assertEqual(hj.screen_text_rule(line.encode(), (KNOWN,)), (line, None, None))
                self.assertEqual(hj.screen_text_rules(line.encode(), (KNOWN,)), ())

    def test_the_corpus_reproduces_the_live_finding_on_the_beta42_screen(self):
        # Before step 2 the same lines were withheld, nearly all by the squashed shape alone.
        decided = [corpus.beta42_rule(line) for line in corpus.MATTER_JS]
        self.assertGreaterEqual(decided.count("user_pass_host.squashed"), len(corpus.MATTER_JS) - 2)
        self.assertEqual(set(decided), {None, "user_pass_host.squashed"})

    def test_a_contiguous_credential_appended_to_a_live_shaped_line_is_withheld(self):
        for line in corpus.MATTER_JS:
            for label, (suffix, rules) in contiguous_credentials().items():
                with self.subTest(line=line[24:72], credential=label):
                    text, omission, rule = hj.screen_text_rule((line + suffix).encode(), (KNOWN,))
                    self.assertEqual((text, omission), (credentials.WITHHELD_MARKER, credentials.OMISSION))
                    expected = rules | ({"credential_url"} if "://" in line else set())
                    self.assertIn(rule, expected)
                    self.assertNotIn(NEEDLE, text)

    def test_a_scheme_less_user_pass_host_is_decided_in_the_normal_form(self):
        for line in corpus.MATTER_JS[:-1]:
            with self.subTest(line=line[24:72]):
                raw = (line + contiguous_credentials()["user_pass_host"][0]).encode()
                self.assertEqual(hj.screen_text_rule(raw, ())[2], "user_pass_host")
                self.assertNotIn("user_pass_host.squashed", hj.screen_text_rules(raw, ()))

    def test_labelled_credentials_and_known_secrets_in_live_shaped_lines_are_withheld_in_both_forms(self):
        for line in corpus.MATTER_JS:
            for label, (suffix, rules) in labelled_credentials().items():
                with self.subTest(line=line[24:72], credential=label):
                    marker, omission, rule = hj.screen_text_rule((line + suffix).encode(), (KNOWN,))
                    self.assertIn(marker, (credentials.WITHHELD_MARKER, credentials.UNVERIFIABLE_MARKER))
                    self.assertIsNotNone(omission)
                    self.assertIn(rule, rules)

    def test_the_corpus_report_is_current(self):
        self.assertEqual(json.loads(corpus.REPORT_PATH.read_text(encoding="utf-8")), corpus.report())
        report = corpus.report()["corpora"]
        self.assertEqual(report["matter_js"]["step2"]["withheld_by_rule"], {"credential_url": 1})
        self.assertEqual(report["matter_js"]["step2"]["shown"], len(corpus.MATTER_JS))
        for name, counts in report.items():
            with self.subTest(corpus=name):
                # Step 2 only ever shows more: every other rule withholds what it withheld.
                self.assertGreaterEqual(counts["step2"]["shown"], counts["beta42"]["shown"])
                before = dict(counts["beta42"]["withheld_by_rule"])
                before.pop("user_pass_host.squashed", None)
                self.assertEqual(counts["step2"]["withheld_by_rule"], before)
        for name, lines in corpus.CORPORA.items():
            for line in lines:
                with self.subTest(corpus=name, line=line[24:72]):
                    before, after = corpus.beta42_rule(line), corpus.step2_rule(line)
                    # The only change: a line the squashed shape alone withheld.
                    if before != after:
                        self.assertIsNone(after)
                        self.assertEqual(credentials.screen_rules(line, (), labelled=True),
                                         ("user_pass_host.squashed",))


class JournalModeBoundaryTests(unittest.TestCase):
    """Only the journal opts in: the definitions and trace paths decide as before."""

    def test_definitions_and_trace_paths_still_withhold_the_live_shaped_lines(self):
        for line in corpus.MATTER_JS[1:-1]:
            if "Websocket client" in line:
                continue
            with self.subTest(line=line[24:72]):
                # HAMCP-132 (default mode) and HAMCP-225 (labelled mode): unchanged.
                self.assertEqual(credentials.screen_rule(line, ()),
                                 (credentials.WITHHELD_MARKER, "user_pass_host.squashed"))
                self.assertEqual(credentials.screen(line, (), labelled=True), credentials.WITHHELD_MARKER)
                withheld, count, _categories, _failed = screen_trace_value({"message": line})
                self.assertEqual((withheld, count), ({"message": credentials.WITHHELD_MARKER}, 1))
                self.assertEqual(credentials.withhold({"title": line})[0], {"title": credentials.WITHHELD_MARKER})

    def test_the_option_defaults_off_and_changes_only_the_squashed_shape(self):
        split = "u" + COLON + " " + NEEDLE + AT + "example.invalid"
        for labelled in (False, True):
            with self.subTest(labelled=labelled):
                self.assertEqual(credentials.screen_rule(split, (), labelled=labelled)[1], "user_pass_host.squashed")
                self.assertEqual(credentials.screen_rule(split, (), labelled=labelled,
                                                         contiguous_user_pass_host=True), (None, None))
                self.assertFalse(credentials.credential_text(split, (), labelled=labelled,
                                                             contiguous_user_pass_host=True))
        # Every other rule's deciding case decides the same with the option on.
        for rule, (raw, truncated) in step1.deciding_cases().items():
            if truncated or rule in hj.RULE_IDS - credentials.RULE_IDS:
                continue
            value = raw.decode()
            with self.subTest(rule=rule):
                self.assertEqual(credentials.screen_rule(value, (step1.KNOWN,), labelled=True,
                                                         contiguous_user_pass_host=True),
                                 credentials.screen_rule(value, (step1.KNOWN,), labelled=True))

    def test_every_hamcp132_and_hamcp224_credential_case_is_still_withheld_by_the_journal(self):
        secrets = (author.KNOWN_SECRET, review1.t.HA_TOKEN, review1.t.ACCESS_SECRET)
        for label, raw, _needle in review1.credential_cases():
            truncated = len(raw) > hj.MAX_FIELD_BYTES
            with self.subTest(case=label):
                text, omission = hj.screen_text(raw[:hj.MAX_FIELD_BYTES], secrets, truncated)
                self.assertIn(text, review1.MARKERS)
                self.assertIsNotNone(omission)

    def test_the_journal_never_reports_the_squashed_shape(self):
        self.assertNotIn("user_pass_host.squashed", hj.RULE_IDS)
        self.assertIn("user_pass_host", hj.RULE_IDS)
        self.assertEqual(dict(hj._JOURNAL_SCREEN), {"labelled": True, "contiguous_user_pass_host": True})


class LiveShapedReadTests(review1.Harness):
    """The live-shaped lines through the read and the calibration of ``addon:core_matter_server``."""

    def entries(self) -> list[tuple[bytes, bytes]]:
        lines = [line.encode() for line in corpus.MATTER_JS]
        planted = [(line + suffix).encode() for line in corpus.MATTER_JS[:3]
                   for suffix, _rules in list(contiguous_credentials().values()) + list(labelled_credentials().values())]
        return [(raw, b"6") for raw in lines + planted]

    async def test_the_read_shows_the_live_shaped_lines_and_withholds_every_credential(self):
        entries = self.entries()
        service = self.service(step1.Rewriting(self.helper.journal, MATTER, entries))
        service.static_secrets = (*service.static_secrets, KNOWN)
        pages = []
        page = await self.helper.read(service=service, sources=[MATTER], limit=200)
        pages.append(page)
        rows = page["entries"]
        self.assertEqual(len(rows), len(entries))
        shown = [row["message"] for row in rows if "withheld" not in row]
        self.assertEqual(shown, list(corpus.MATTER_JS))
        held = [row for row in rows if "withheld" in row]
        self.assertEqual(len(held), len(entries) - len(corpus.MATTER_JS))
        for row in held:
            self.assertIn(row["withheld_rules"]["message"], hj.RULE_IDS)
            self.assertNotEqual(row["withheld_rules"]["message"], "user_pass_host.squashed")
        rendered = json.dumps(pages, ensure_ascii=False)
        self.assertNotIn(NEEDLE, rendered)
        self.assertNotIn(KNOWN[:4] + " " + KNOWN[4:], rendered)
        self.assertFalse(page["complete"])

    async def test_the_calibration_counts_the_live_shaped_lines_shown(self):
        entries = [(line.encode(), b"6") for line in corpus.MATTER_JS + corpus.MATTER_JS_OTHER_RULE]
        service = self.service(step1.Rewriting(self.helper.journal, MATTER, entries))
        page = await self.helper.read(service=service, sources=[MATTER], calibration=True)
        row = page["sources"][0]
        self.assertEqual((row["scanned"], row["shown"], row["withheld_entries"]),
                         (len(entries), len(corpus.MATTER_JS), len(corpus.MATTER_JS_OTHER_RULE)))
        self.assertEqual(row["withheld_by_rule"], {"credential_url": 1})
        self.assertNotIn("user_pass_host.squashed", row["matched_by_rule"])
        self.assertEqual(page["rule_ids"], sorted(hj.RULE_IDS))
        self.assertNotIn("Session", json.dumps(page))


class ServedSchemaTests(unittest.TestCase):
    """B: ``calibration`` in the served ``get_incident_logs`` schema (``tools/list``) and reachable by a call."""

    @classmethod
    def setUpClass(cls):
        import test_beta_v2 as beta
        beta.restore_after_class(cls)

        class SyntheticCoreSource:
            async def capture_core_snapshot(self):
                return beta.core_snapshot("2026.8.1", evidence=beta.core_capability_evidence())

        cls.beta = beta
        cls.tempdir = tempfile.TemporaryDirectory()
        settings = beta.beta_settings(str(Path(cls.tempdir.name) / "audit.jsonl"))
        gateway = beta.create_application(settings, core_snapshot_source=SyntheticCoreSource())
        asyncio.run(beta.CORE_READMISSION.reconcile_once("startup"))
        asyncio.run(beta.GOVERNANCE.require().f3_runtime.recover_once("synthetic-application-startup"))
        cls.client_context = beta.SameThreadAsgiTestClient(gateway, lifespan_app=gateway.app, follow_redirects=False)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        cls.tempdir.cleanup()

    def served_tools(self) -> dict:
        headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
        path = f"/{self.beta.SECRET}/mcp"
        self.assertEqual(self.client.post(path, content=json.dumps(self.beta.INITIALIZE_REQUEST),
                                          headers=headers).status_code, 200)
        response = self.client.post(path, json={"jsonrpc": "2.0", "id": "hamcp234-list", "method": "tools/list",
                                                "params": {}}, headers=headers)
        self.assertEqual(response.status_code, 200)
        listing = json.loads(next(line.removeprefix("data: ") for line in response.text.replace("\r", "").splitlines()
                                  if line.startswith("data: ")))
        return {tool["name"]: tool for tool in listing["result"]["tools"]}

    def test_the_served_schema_offers_calibration(self):
        tool = self.served_tools()["get_incident_logs"]
        schema = tool["inputSchema"]
        self.assertEqual(schema["properties"]["calibration"], {"default": False, "title": "Calibration",
                                                              "type": "boolean"})
        self.assertEqual(set(schema["properties"]), {"sources", "start", "end", "min_level", "text", "limit",
                                                     "cursor", "calibration"})
        self.assertEqual(schema["required"], ["sources", "start"])
        self.assertIs(schema["additionalProperties"], False)
        self.assertIn("calibration: true", tool["description"])
        self.assertTrue(tool["annotations"]["readOnlyHint"])


class CalibrationOverTheWireTests(unittest.TestCase):
    """``calibration: true`` through the authenticated MCP gateway returns counts."""

    def test_a_calibration_call_returns_counts_over_the_wire(self):
        root = author.journal_root()
        self.addCleanup(author.shutil.rmtree, root, True)
        entries = [(line.encode(), b"6") for line in corpus.MATTER_JS + corpus.MATTER_JS_OTHER_RULE]
        stack = gateway_tests.IncidentLogGatewayStackTests()
        with patch.object(gateway_tests, "journald_off_reader", lambda: step1.Rewriting(root, MATTER, entries)):
            rendered, record, wire = stack.call({"sources": [MATTER], "start": author.iso(0), "end": author.iso(7000),
                                                 "calibration": True})
        self.assertTrue(rendered["success"], rendered)
        data = rendered["data"]
        self.assertEqual(data["mode"], "calibration")
        row = data["sources"][0]
        self.assertEqual((row["source"], row["scanned"], row["shown"], row["withheld_entries"]),
                         (MATTER, len(entries), len(corpus.MATTER_JS), 1))
        self.assertEqual(row["withheld_by_rule"], {"credential_url": 1})
        self.assertEqual(record["tool_name"], "get_incident_logs")
        for line in corpus.MATTER_JS:
            self.assertNotIn(line[24:], wire)


if __name__ == "__main__":
    unittest.main()
