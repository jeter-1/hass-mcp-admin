"""HAMCP-234 step 2: the journal's ``user_pass_host`` as one contiguous token, and calibration reachability.

Owner decision (Josh, 2026-10-27), from the beta.42 live calibration of
``addon:core_matter_server`` (37 of 40 lines withheld, every one by
``user_pass_host.squashed``): in the journal path only, ``user_pass_host``
is read in the normal form as one contiguous token and not in the squashed
form. Every other rule is unchanged in both forms; the configuration
definitions (HAMCP-132) and trace (HAMCP-225) paths are unchanged.

Correction batch (review of aaf1fe8e; owner clarification, Josh,
2026-10-28): "one contiguous token" is judged on the text as given and after
each round of its percent-decode, not only on the decoded normal form, so a
``%20`` inside a proxy password (the reviewer's reproduction) is withheld
through the final gateway response. The trace identity and name fields
(``alias``, ``id``, ``entity_id``, ``friendly_name``) keep the squashed
``user_pass_host`` through capture, the store, read-back and
``get_automation_trace``.

Correction D320-H1 (review of c0789386; Josh, 2026-10-28): the tokens are
taken from the text as given, split at its literal whitespace, and each is
read through its own bounded percent-decode with the whitespace a round
introduces kept inside the token, so an encoded ``:`` or ``@`` that decodes
in the same round as an encoded space (a whole value passed through
``quote(value, safe='')``) is withheld too. Literal whitespace in the text
as given still separates tokens: a scheme-less ``user:pass@host`` split by
a literal space is shown in the journal, a disclosed residual accepted by
Josh, 2026-10-28.

Correction R3-H1 (review of c371c739; Josh, 2026-10-28): each stage of a raw
token is normalized (NFKC, casefold, confusable fold) before its whitespace
is removed, so a space that NFKC introduces (U+00A8 is a space and a
diaeresis) stays inside the token. Raw tokens split at literal ASCII
whitespace only (U+0009 to U+000D, U+0020): a no-break, figure, narrow
no-break or ideographic space inside a password no longer splits it. The
residual is raw ASCII whitespace, journal mode, scheme-less only.

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
from urllib.parse import parse_qs, quote, unquote, urlencode
from urllib.request import _parse_proxy

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))
sys.path.insert(0, str(TESTS.parent / "hass_mcp_engineering_beta"))

import hamcp234_step2_corpus as corpus  # noqa: E402
import test_hamcp131_incident_logs as gateway_tests  # noqa: E402
import test_hamcp224_host_journal as author  # noqa: E402
import test_hamcp224_review1_corrections as review1  # noqa: E402
import test_hamcp225_trace_snapshots as traces  # noqa: E402
import test_hamcp234_journal_usability as step1  # noqa: E402

from ha_mcp_engineering.configuration_definitions import credentials  # noqa: E402
from ha_mcp_engineering.trace_normalization import IDENTITY_KEYS, screen_trace_value  # noqa: E402

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


# Percent-encoded whitespace inside a password, each of which a decode turns
# into whitespace that splits the decoded normal form (review of aaf1fe8e).
SPLITTING_ESCAPES = {
    "space": "%20", "tab": "%09", "line_feed": "%0A", "carriage_return": "%0D", "nbsp": "%C2%A0",
    "ideographic_space": "%E3%80%80", "plus_then_space": "%2B%20", "literal_plus_then_space": "+%20",
    "space_then_plus": "%20%2B", "double_encoded_space": "%2520", "lower_hex_space": "%0a",
}


def proxy_line(escape: str, needle: str = NEEDLE) -> str:
    """The reviewer's reproduction: a Python logging line naming a scheme-less proxy credential."""

    setting = json.dumps({"proxy": "sample" + COLON + needle + escape + "elm" + AT + "proxy.invalid" + COLON + "8080"})
    return "2026-10-27 03:16:00,123 INFO [proxy] Using proxy configuration " + setting


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


class PercentEncodedContiguityTests(unittest.TestCase):
    """Correction (High): contiguity is judged before decoding too; decoding never splits the token."""

    def test_the_reviewer_proxy_reproduction_is_withheld(self):
        line = proxy_line("%20")
        self.assertEqual(hj.screen_text_rule(line.encode(), (KNOWN,)),
                         (credentials.WITHHELD_MARKER, credentials.OMISSION, "user_pass_host"))
        self.assertIn("user_pass_host", hj.screen_text_rules(line.encode(), (KNOWN,)))
        # Base (beta.42) withheld it by the squashed shape; the journal still withholds it, by the token.
        self.assertEqual(corpus.beta42_rule(line), "user_pass_host.squashed")

    def test_percent_encoded_whitespace_inside_a_credential_is_withheld(self):
        for label, escape in SPLITTING_ESCAPES.items():
            line = proxy_line(escape)
            with self.subTest(escape=label):
                text, omission, rule = hj.screen_text_rule(line.encode(), (KNOWN,))
                self.assertEqual((text, omission, rule),
                                 (credentials.WITHHELD_MARKER, credentials.OMISSION, "user_pass_host"))
                # The decoded normal form alone no longer meets the token: the text as given does.
                decoded = credentials.canonical(line)[0]
                self.assertIsNone(credentials._USER_PASS_HOST.search(decoded))

    def test_encoded_separators_that_keep_the_token_whole_are_withheld(self):
        for label, escape in {"plus": "%2B", "literal_plus": "+", "comma": "%2C", "semicolon": "%3B"}.items():
            with self.subTest(escape=label):
                self.assertEqual(hj.screen_text_rule(proxy_line(escape).encode(), ())[1:],
                                 (credentials.OMISSION, "user_pass_host"))

    def test_encoded_controls_inside_a_credential_are_withheld(self):
        for escape in ("%0B", "%0C", "%1F", "%7F", "%C2%85"):
            with self.subTest(escape=escape):
                marker, omission, _rule = hj.screen_text_rule(proxy_line(escape).encode(), ())
                self.assertIn(marker, (credentials.WITHHELD_MARKER, credentials.UNVERIFIABLE_MARKER))
                self.assertIsNotNone(omission)

    def test_an_intermediate_decode_round_is_read(self):
        # Neither the text as given (no ``@`` yet) nor the decoded normal form (split)
        # meets the token; the first decode round does.
        value = "u" + COLON + NEEDLE + "%2520elm%40proxy.invalid"
        self.assertIsNone(credentials._USER_PASS_HOST.search(value))
        self.assertIsNone(credentials._USER_PASS_HOST.search(credentials.canonical(value)[0]))
        self.assertEqual(hj.screen_text_rule(value.encode(), ())[2], "user_pass_host")

    def test_each_round_is_normalized_and_folded_as_the_normal_form(self):
        # A fullwidth ``@`` (NFKC) and a ratio colon (the confusable fold) before a ``%20``.
        for label, at, colon in (("fullwidth_at", chr(0xFF20), COLON), ("ratio_colon", AT, chr(0x2236))):
            value = "u" + colon + NEEDLE + "%20elm" + at + "proxy.invalid"
            with self.subTest(case=label):
                self.assertEqual(hj.screen_text_rule(value.encode(), ())[2], "user_pass_host")

    def test_live_shaped_lines_with_percent_encoded_text_are_still_shown(self):
        for line in corpus.MATTER_JS:
            # A stray ``%`` (``%eth0``) beside an escape is refused as ambiguous, as before step 2.
            if "://" in line or "%" in line:
                continue
            for suffix in (" label=Living%20Room", " path=/api/states%3Fq%3Da%2520b", " note=50%25"):
                with self.subTest(line=line[24:72], suffix=suffix):
                    value = line + suffix
                    self.assertEqual(hj.screen_text_rule(value.encode(), ()), (value, None, None))

    def test_the_default_and_labelled_modes_are_unchanged(self):
        for escape in SPLITTING_ESCAPES.values():
            line = proxy_line(escape)
            for labelled in (False, True):
                with self.subTest(escape=escape, labelled=labelled):
                    self.assertEqual(credentials.screen_rule(line, (), labelled=labelled)[1],
                                     "user_pass_host.squashed")


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


# A credential only the squashed form meets: the token split by literal whitespace.
def squashed_only_credentials() -> dict[str, tuple[str, str]]:
    """``label -> (value, needle)`` for the trace identity and name fields."""

    needles = {label: NEEDLE + label.replace("_", "") for label in ("space", "tab", "line_feed", "encoded")}
    return {
        "space": ("u" + COLON + " " + needles["space"] + AT + "broker.invalid", needles["space"]),
        "tab": ("u" + COLON + needles["tab"] + "\tx" + AT + "broker.invalid", needles["tab"]),
        "line_feed": ("u" + COLON + needles["line_feed"] + "\nx" + AT + "broker.invalid", needles["line_feed"]),
        "encoded": ("u" + COLON + needles["encoded"] + "%20x" + AT + "broker.invalid", needles["encoded"]),
    }


IDENTITY_FIELDS = ("alias", "id", "entity_id", "friendly_name")


def identity_detail(run_id: str, value: str) -> dict:
    """A trace whose identity and name fields (and only those) hold ``value``."""

    return {**traces.header(run_id),
            "config": {"id": value, "alias": value, "entity_id": ["sensor.ok", value],
                       "triggers": [{"trigger": "state", "entity_id": value, "id": value}]},
            "context": {"id": "ctx"},
            "trace": {"action/0": [{"path": "action/0", "variables": {"trigger": {
                "entity_id": value, "to_state": {"attributes": {"friendly_name": value}}}}}]}}


class TraceIdentityBoundaryTests(traces.ServiceCase):
    """Correction (Medium): the trace identity and name fields keep the squashed ``user_pass_host``."""

    def test_the_named_fields_are_identity_keys_read_in_the_default_mode(self):
        self.assertLessEqual(set(IDENTITY_FIELDS), IDENTITY_KEYS)
        for label, (value, needle) in squashed_only_credentials().items():
            self.assertEqual(credentials.screen_rule(value, ())[1], "user_pass_host.squashed")
            if label != "encoded":  # the journal's token rule does not meet a literal split
                self.assertIsNone(hj.screen_text_rule(value.encode(), ())[1])
            for field in IDENTITY_FIELDS:
                with self.subTest(credential=label, field=field):
                    for item in ({field: value}, {field: ["sensor.ok", value]}):
                        screened, count, _categories, _failed = screen_trace_value(item, (KNOWN,))
                        self.assertEqual(count, 1)
                        self.assertNotIn(needle, json.dumps(screened))

    async def test_capture_store_and_read_back_withhold_squashed_only_identity_credentials(self):
        for number, (label, (value, needle)) in enumerate(squashed_only_credentials().items()):
            run_id = f"r{number}"
            self.core.add(run_id)
            self.core.details[run_id] = identity_detail(run_id, value)
            with self.subTest(credential=label):
                captured, _ = await self.service.capture(automation_id="a1", run_id=run_id)
                run = captured["runs"][0]
                self.assertEqual((run["outcome"], run["completeness"]), ("captured", "partial"))
                page, partial = await self.service.get(snapshot_id=run["snapshot_id"])
                self.assertTrue(partial)
                config = next(unit["value"] for unit in page["units"] if unit["kind"] == "config")
                self.assertEqual((config["id"], config["alias"], config["entity_id"]),
                                 (credentials.WITHHELD_MARKER, credentials.WITHHELD_MARKER,
                                  ["sensor.ok", credentials.WITHHELD_MARKER]))
                step = next(unit["value"] for unit in page["units"] if unit["kind"] == "step")
                trigger = step["variables"]["trigger"]
                self.assertEqual((trigger["entity_id"], trigger["to_state"]["attributes"]["friendly_name"]),
                                 (credentials.WITHHELD_MARKER, credentials.WITHHELD_MARKER))
                self.assertNotIn(needle, json.dumps((captured, page)))
                self.assertNotIn(needle.encode(), traces.all_store_bytes(self.root))

    async def test_get_automation_trace_withholds_squashed_only_identity_credentials(self):
        for label, (value, needle) in squashed_only_credentials().items():
            with self.subTest(credential=label):
                async def command(payload, value=value):
                    return identity_detail("r1", value)
                with patch.object(traces.compatibility, "ws_command", command):
                    envelope = json.loads(await traces.compatibility.get_automation_trace("a1", "r1"))
                self.assertNotIn(needle, json.dumps(envelope))
                self.assertEqual(envelope["metadata"]["completeness"], "partial")


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


class ProxyCredentialOverTheWireTests(unittest.TestCase):
    """Correction (High): the proxy reproduction is withheld in the final authenticated gateway response."""

    def test_the_proxy_credential_is_withheld_and_matter_js_shown_over_the_wire(self):
        root = author.journal_root()
        self.addCleanup(author.shutil.rmtree, root, True)
        proxies = [proxy_line(escape) for escape in SPLITTING_ESCAPES.values()]
        entries = [(line.encode(), b"6") for line in list(corpus.MATTER_JS) + proxies]
        stack = gateway_tests.IncidentLogGatewayStackTests()
        with patch.object(gateway_tests, "journald_off_reader", lambda: step1.Rewriting(root, MATTER, entries)):
            rendered, record, wire = stack.call({"sources": [MATTER], "start": author.iso(0), "end": author.iso(7000),
                                                 "limit": 200})
        self.assertTrue(rendered["success"], rendered)
        rows = rendered["data"]["entries"]
        self.assertEqual(len(rows), len(entries))
        self.assertEqual([row["message"] for row in rows if "withheld" not in row], list(corpus.MATTER_JS))
        held = [row for row in rows if "withheld" in row]
        self.assertEqual([row["withheld_rules"]["message"] for row in held], ["user_pass_host"] * len(proxies))
        for row in held:
            self.assertEqual(row["message"], credentials.WITHHELD_MARKER)
        # The final response, after the gateway's own sanitization: no part of the credential.
        self.assertNotIn(NEEDLE, wire)
        self.assertNotIn("proxy.invalid", wire)
        self.assertNotIn(NEEDLE, json.dumps(record))
        self.assertEqual(record["tool_name"], "get_incident_logs")


def encoded_proxy(colon: str, space: str, at: str, needle: str) -> str:
    """A scheme-less proxy setting whose ``:``, password whitespace and ``@`` are given as written."""

    return "reviewer" + colon + needle + space + "elm" + at + "proxy.invalid" + COLON + "8080"


def encoded_separator_cases() -> dict[str, str]:
    """``label -> value``: one raw token whose separators and password whitespace decode, each a distinct needle.

    Same round: an encoded separator and an encoded space decode together.
    Mixed rounds: they decode in different rounds. Complete value encoding:
    the whole setting through ``quote(value, safe='')``, once and twice.
    """

    cases = {}
    for colon_label, colon in (("plain_colon", COLON), ("upper_colon", "%3A"), ("lower_colon", "%3a")):
        for at_label, at in (("plain_at", AT), ("encoded_at", "%40")):
            if colon == COLON and at == AT:
                continue
            cases["same_round." + colon_label + "." + at_label] = (colon, "%20", at)
    cases.update({
        "same_round.tab_both_encoded": ("%3A", "%09", "%40"),
        "same_round.nbsp_both_encoded": ("%3a", "%C2%A0", "%40"),
        "mixed_rounds.encoded_at_double_space": (COLON, "%2520", "%40"),
        "mixed_rounds.lower_colon_double_space": ("%3a", "%2520", AT),
        "mixed_rounds.double_colon_single_space": ("%253A", "%20", AT),
        "mixed_rounds.double_at_single_space": (COLON, "%20", "%2540"),
        "mixed_rounds.triple_space_triple_at": (COLON, "%252520", "%252540"),
        "mixed_rounds.triple_all": ("%25253a", "%252520", "%252540"),
    })
    values = {label: encoded_proxy(*parts, NEEDLE + str(index))
              for index, (label, parts) in enumerate(cases.items())}
    base = len(values)
    values["complete_value.once"] = quote(encoded_proxy(COLON, " ", AT, NEEDLE + str(base)), safe="")
    values["complete_value.twice"] = quote(quote(encoded_proxy(COLON, " ", AT, NEEDLE + str(base + 1)), safe=""),
                                           safe="")
    values["complete_value.tab"] = quote(encoded_proxy(COLON, "\t", AT, NEEDLE + str(base + 2)), safe="")
    return values


class EncodedSeparatorContiguityTests(unittest.TestCase):
    """Correction D320-H1: a raw token whose separators decode with its password whitespace is withheld."""

    def test_encoded_separators_decoded_with_whitespace_are_withheld(self):
        for label, value in encoded_separator_cases().items():
            with self.subTest(case=label):
                # The decoded normal form alone is split: the raw token's decode meets it.
                self.assertIsNone(credentials._USER_PASS_HOST.search(credentials.canonical(value)[0]))
                self.assertEqual(hj.screen_text_rule(value.encode(), ()),
                                 (credentials.WITHHELD_MARKER, credentials.OMISSION, "user_pass_host"))
                self.assertEqual(hj.screen_text_rules(value.encode(), ())[0], "user_pass_host")

    def test_literal_whitespace_in_the_text_as_given_still_separates_tokens(self):
        # The accepted residual (Josh, 2026-10-28): a literal space splits the raw token.
        for label, value in {
            "literal_space": encoded_proxy(COLON, " ", AT, NEEDLE),
            "literal_space_encoded_plus": encoded_proxy(COLON, " %2B", AT, NEEDLE),
            "literal_space_encoded_separators": encoded_proxy("%3A", " ", "%40", NEEDLE),
            "literal_tab_encoded_space": encoded_proxy("%3A", "\t%20", "%40", NEEDLE),
        }.items():
            with self.subTest(case=label):
                self.assertEqual(hj.screen_text_rule(value.encode(), ()), (value, None, None))
                # Definitions and labelled modes are unchanged: the squashed shape withholds it.
                self.assertEqual(credentials.screen_rule(value, ())[1], "user_pass_host.squashed")
                self.assertEqual(credentials.screen_rule(value, (), labelled=True)[1], "user_pass_host.squashed")
        # A scheme still withholds it in the journal (credential_url).
        for scheme in ("http://", "https://", "socks5://"):
            with self.subTest(scheme=scheme):
                value = scheme + encoded_proxy(COLON, " ", AT, NEEDLE)
                self.assertEqual(hj.screen_text_rule(value.encode(), ())[2], "credential_url")

    def test_each_raw_token_is_decoded_at_most_the_fixed_number_of_rounds(self):
        decoded = []
        original = credentials.unquote_to_bytes
        # No ``@`` in any round, so every round of the token is read.
        token = "reviewer%3A" + NEEDLE + "%2525252520elm.proxy.invalid"
        counting = lambda text: decoded.append(text) or original(text)  # noqa: E731
        with patch.object(credentials, "unquote_to_bytes", counting):
            self.assertFalse(credentials._undecoded_user_pass_host("x%20y " + token + " z%2541"))
        self.assertEqual(credentials.MAX_DECODE_ROUNDS, 4)
        self.assertEqual(sum(1 for text in decoded if NEEDLE in text), credentials.MAX_DECODE_ROUNDS)
        self.assertEqual(len(decoded), 1 + credentials.MAX_DECODE_ROUNDS + 2)
        # The screen refuses the token that still carries an escape after the last round.
        self.assertEqual(hj.screen_text_rule(token.encode(), ())[2], credentials.RULE_CANONICAL_REFUSED)


class EncodedProxyOverTheWireTests(unittest.TestCase):
    """Correction D320-H1: encoded proxy credentials are withheld in the final response and the audit record."""

    def call(self, entries: list[tuple[bytes, bytes]]) -> tuple[dict, dict, str]:
        root = author.journal_root()
        self.addCleanup(author.shutil.rmtree, root, True)
        stack = gateway_tests.IncidentLogGatewayStackTests()
        with patch.object(gateway_tests, "journald_off_reader", lambda: step1.Rewriting(root, MATTER, entries)):
            rendered, record, wire = stack.call({"sources": [MATTER], "start": author.iso(0),
                                                 "end": author.iso(7000), "limit": 200})
        self.assertTrue(rendered["success"], rendered)
        self.assertEqual(record["tool_name"], "get_incident_logs")
        return rendered, record, wire

    def test_urlencoded_proxy_value_with_space_is_withheld_over_wire(self):
        # Ported from the round-2 reviewer regression (test_encoded_proxy_regression.py), assertions intact.
        needle = "SYNTHETIC" + "review320url"
        raw = "reviewer" + ":" + needle + " elm" + "@" + "proxy.invalid:8080"
        encoded = quote(raw, safe="")
        # A conventional form/query serialization, recovered without any network.
        query = urlencode({"proxy": raw}, quote_via=quote)
        recovered = parse_qs(query)["proxy"][0]
        self.assertEqual(recovered, raw)
        self.assertEqual(_parse_proxy(recovered)[2], needle + " elm")
        line = "2026-10-27 03:16:00,123 INFO [proxy] configuration " + json.dumps({"proxy": encoded})
        self.assertEqual(credentials.screen_rule(line, labelled=True)[1], "user_pass_host.squashed")
        result, audit, wire = self.call([(line.encode(), b"6")])
        self.assertTrue(result["success"])
        self.assertFalse(needle in wire, "whole-value percent encoding exposes proxy password in final response")
        self.assertNotIn(encoded, wire)
        self.assertNotIn("proxy.invalid", wire)
        self.assertNotIn(needle, json.dumps(audit))
        self.assertNotIn("proxy.invalid", json.dumps(audit))

    def test_every_encoded_separator_case_is_withheld_in_the_response_and_the_audit(self):
        cases = encoded_separator_cases()
        lines = ["2026-10-27 03:16:00,123 INFO [proxy] configuration " + json.dumps({"proxy": value})
                 for value in cases.values()]
        entries = [(line.encode(), b"6") for line in list(corpus.MATTER_JS) + lines]
        rendered, record, wire = self.call(entries)
        rows = rendered["data"]["entries"]
        self.assertEqual(len(rows), len(entries))
        # The live-shaped lines read as before; every encoded proxy line is withheld by the token.
        self.assertEqual([row["message"] for row in rows if "withheld" not in row], list(corpus.MATTER_JS))
        held = [row for row in rows if "withheld" in row]
        self.assertEqual([row["withheld_rules"]["message"] for row in held], ["user_pass_host"] * len(cases))
        audit = json.dumps(record)
        for index, (label, value) in enumerate(cases.items()):
            with self.subTest(case=label):
                self.assertEqual(held[index]["message"], credentials.WITHHELD_MARKER)
                self.assertNotIn(value, wire)
        for index in range(len(cases)):
            needle = NEEDLE + str(index)
            with self.subTest(needle=index):
                self.assertNotIn(needle, wire)
                self.assertNotIn(needle, audit)
        self.assertNotIn(NEEDLE, wire)
        self.assertNotIn(NEEDLE, audit)
        self.assertNotIn("proxy.invalid", wire)
        self.assertNotIn("proxy.invalid", audit)


# Spacing marks NFKC maps to a space and a combining mark (R3-H1).
NFKC_SPACING_MARKS = (0x00A8, 0x00AF, 0x00B4, 0x0385)
# Non-ASCII whitespace no longer splits a raw token (Josh, 2026-10-28); U+2028
# and U+0085 are withheld as invisible or control text.
NON_ASCII_WHITESPACE = (0x00A0, 0x2007, 0x202F, 0x3000, 0x2028, 0x0085)
# The disclosed residual: raw ASCII whitespace, journal mode, scheme-less only.
ASCII_WHITESPACE = (0x0020, 0x0009)


def nfkc_proxy(point: int, needle: str) -> str:
    """The round-3 reviewer's construction: a whole proxy setting through ``quote(value, safe='')``, one raw token."""

    return quote("reviewer" + COLON + needle + chr(point) + " tail" + AT + "proxy.invalid" + COLON + "8080", safe="")


def whitespace_proxy(point: int, needle: str) -> str:
    """A scheme-less ``user:pa<X>ss@host`` with the literal character ``X`` inside the password."""

    return "matrix" + COLON + needle + "pa" + chr(point) + "ss" + AT + "proxy.invalid"


def codepoint(point: int) -> str:
    return f"U+{point:04X}"


def journal_call(test: unittest.TestCase, entries: list[tuple[bytes, bytes]]) -> tuple[dict, dict, str]:
    root = author.journal_root()
    test.addCleanup(author.shutil.rmtree, root, True)
    stack = gateway_tests.IncidentLogGatewayStackTests()
    with patch.object(gateway_tests, "journald_off_reader", lambda: step1.Rewriting(root, MATTER, entries)):
        rendered, record, wire = stack.call({"sources": [MATTER], "start": author.iso(0),
                                             "end": author.iso(7000), "limit": 200})
    test.assertTrue(rendered["success"], rendered)
    test.assertEqual(record["tool_name"], "get_incident_logs")
    return rendered, record, wire


class NormalizationWhitespaceTests(unittest.TestCase):
    """Correction R3-H1: whitespace NFKC introduces inside one raw token stays part of the token."""

    def test_nfkc_whitespace_cannot_split_one_encoded_raw_token_on_the_screen(self):
        for point in NFKC_SPACING_MARKS:
            needle = NEEDLE + "nfkc" + str(point)
            value = nfkc_proxy(point, needle)
            line = json.dumps({"proxy": value})
            with self.subTest(codepoint=codepoint(point)):
                # One raw token: an encoded separator and an encoded space in the same token.
                self.assertEqual(len(value.split()), 1)
                self.assertFalse(any(character.isspace() for character in value))
                self.assertIn("%3A", value)
                self.assertIn("%40", value)
                self.assertIn("%20", value)
                # A proxy client recovers the password after decoding.
                self.assertEqual(_parse_proxy(unquote(value))[2], needle + chr(point) + " tail")
                self.assertEqual(hj.screen_text_rule(line.encode(), ()),
                                 (credentials.WITHHELD_MARKER, credentials.OMISSION, "user_pass_host"))
                # Definitions and labelled modes are unchanged: the squashed shape withholds it.
                self.assertEqual(credentials.screen_rule(line)[1], "user_pass_host.squashed")
                self.assertEqual(credentials.screen_rule(line, labelled=True)[1], "user_pass_host.squashed")

    def test_nfkc_whitespace_in_a_raw_token_without_an_escape_is_withheld(self):
        for point in NFKC_SPACING_MARKS:
            value = "reviewer" + COLON + NEEDLE + chr(point) + "tail" + AT + "proxy.invalid" + COLON + "8080"
            with self.subTest(codepoint=codepoint(point)):
                self.assertEqual(len(value.split()), 1)
                # The normal form alone is split by the space NFKC introduces.
                self.assertIsNone(credentials._USER_PASS_HOST.search(credentials.canonical(value)[0]))
                self.assertEqual(hj.screen_text_rule(value.encode(), ())[2], "user_pass_host")
                self.assertEqual(credentials.screen_rule(value)[1], "user_pass_host.squashed")

    def test_nfkc_whitespace_cannot_split_one_raw_token_over_the_wire(self):
        # Ported from the round-3 reviewer regression (test_normalization_regression.py), assertions
        # intact, for all four spacing marks.
        for point in NFKC_SPACING_MARKS:
            with self.subTest(codepoint=codepoint(point)):
                needle = "SYNTHETIC" + "normalization320"
                value = quote("reviewer:" + needle + chr(point) + " tail@proxy.invalid:8080", safe="")
                self.assertEqual(len(value.split()), 1)
                envelope, audit, wire = journal_call(self, [(json.dumps({"proxy": value}).encode(), b"6")])
                self.assertTrue(envelope["success"])
                self.assertNotIn(needle, wire, "NFKC-added whitespace must remain inside the original raw token")
                self.assertNotIn("proxy.invalid", wire)
                self.assertNotIn(needle, json.dumps(audit))
                self.assertNotIn("proxy.invalid", json.dumps(audit))
                self.assertEqual(envelope["data"]["entries"][0]["withheld_rules"]["message"], "user_pass_host")


class UnicodeWhitespaceMatrixTests(unittest.TestCase):
    """Josh, 2026-10-28: raw tokens split at ASCII whitespace only; the residual is raw ASCII whitespace."""

    def test_non_ascii_whitespace_inside_a_password_is_withheld_in_every_mode(self):
        for point in NON_ASCII_WHITESPACE:
            value = whitespace_proxy(point, NEEDLE)
            with self.subTest(codepoint=codepoint(point)):
                self.assertIn(hj.screen_text_rule(value.encode(), ()),
                              {(credentials.WITHHELD_MARKER, credentials.OMISSION, "user_pass_host"),
                               (credentials.UNVERIFIABLE_MARKER, credentials.UNVERIFIABLE_OMISSION,
                                credentials.RULE_INVISIBLE)})
                self.assertIsNotNone(credentials.screen_rule(value)[1])
                self.assertIsNotNone(credentials.screen_rule(value, labelled=True)[1])
                for field in ("alias", "message"):
                    self.assertEqual(screen_trace_value({field: value}, ())[1], 1, field)

    def test_non_ascii_whitespace_inside_a_password_is_withheld_in_the_response_and_the_audit(self):
        needles = [NEEDLE + "ws" + str(point) for point in NON_ASCII_WHITESPACE]
        entries = [(json.dumps({"proxy": whitespace_proxy(point, needle)}).encode(), b"6")
                   for point, needle in zip(NON_ASCII_WHITESPACE, needles)]
        rendered, record, wire = journal_call(self, entries)
        rows = rendered["data"]["entries"]
        self.assertEqual(len(rows), len(entries))
        audit = json.dumps(record)
        for point, needle, row in zip(NON_ASCII_WHITESPACE, needles, rows):
            with self.subTest(codepoint=codepoint(point)):
                self.assertIn("withheld", row)
                self.assertIn(row["message"], {credentials.WITHHELD_MARKER, credentials.UNVERIFIABLE_MARKER})
                self.assertNotIn(needle, wire)
                self.assertNotIn(needle, audit)
        self.assertNotIn(NEEDLE, wire)
        self.assertNotIn("proxy.invalid", wire)
        self.assertNotIn("proxy.invalid", audit)

    def test_raw_ascii_whitespace_is_the_disclosed_journal_residual(self):
        for point in ASCII_WHITESPACE:
            value = whitespace_proxy(point, NEEDLE)
            with self.subTest(codepoint=codepoint(point)):
                self.assertEqual(hj.screen_text_rule(value.encode(), ()), (value, None, None))
                # Definitions and trace are unchanged: the squashed shape withholds it.
                self.assertEqual(credentials.screen_rule(value)[1], "user_pass_host.squashed")
                self.assertEqual(credentials.screen_rule(value, labelled=True)[1], "user_pass_host.squashed")
                for field in ("alias", "message"):
                    self.assertEqual(screen_trace_value({field: value}, ())[1], 1, field)
        entries = [(whitespace_proxy(point, NEEDLE).encode(), b"6") for point in ASCII_WHITESPACE]
        rendered, _record, _wire = journal_call(self, entries)
        self.assertEqual([row["message"] for row in rendered["data"]["entries"]],
                         [whitespace_proxy(point, NEEDLE) for point in ASCII_WHITESPACE])


if __name__ == "__main__":
    unittest.main()
