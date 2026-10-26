"""HAMCP-224 review 2 corrections: label-keyed withholding, two-form ANSI screening, level-filtered withholding.

Synthetic fixtures only; no live Home Assistant and no host journal. Every
credential-shaped payload is assembled at run time, so this file holds no
secret-shaped literal.

The reviewer's delta suite (``test_pr313_delta_residuals``) is kept byte for
byte. One of its methods,
``test_original_control_text_is_screened_before_ansi_removal``, expects a
coloured ordinary line to be withheld; the owner decided otherwise (review 2
M4: an SGR colour code is a recognized display format, stripped for display
and never by itself a reason to withhold; review 3 narrowed the exemption
from every CSI sequence to SGR codes only). That method uses only SGR codes,
so it is registered below as an expected failure: it still runs, still has to
fail, and turns the build red if colour codes are ever withheld again.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest
from urllib.parse import quote

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))
sys.path.insert(0, str(TESTS.parent / "hass_mcp_engineering_beta"))

import test_hamcp224_review1_corrections as review1  # noqa: E402
import test_pr313_delta_residuals as residuals  # noqa: E402

from ha_mcp_engineering.configuration_definitions import credentials  # noqa: E402

hj = review1.hj
author = review1.author

# Owner decision, HAMCP-224 review 2 M4 (recorded in the PR body and the
# reviewer delta brief): superseded, so expected to fail.
SUPERSEDED = "test_original_control_text_is_screened_before_ansi_removal"
_method = getattr(residuals.DeltaResidualTests, SUPERSEDED)
if not getattr(_method, "__unittest_expecting_failure__", False):
    setattr(residuals.DeltaResidualTests, SUPERSEDED, unittest.expectedFailure(_method))

REVIEWER_SUITES = {
    "test_pr313_independent_review.py": "30884b4c495d6b680e5bff3b80e7fbc70e2641bea51006a30391c193fb8987d8",
    "test_pr313_delta_residuals.py": "9b3d8d37299a60a36e3d7e7098882d000149b6c556be657cfbf95d0a7848e202",
}

NEEDLE = "SYNTHETIC" + "compact79"
FIELDS = (("MESSAGE", "supervisor", "message"), ("SYSLOG_IDENTIFIER", "host:kernel", "process"),
          ("_KERNEL_SUBSYSTEM", "host:kernel", "kernel_subsystem"), ("_UDEV_SYSNAME", "host:kernel", "kernel_device"))
OPTION = "-" + "P"


def option_forms() -> dict[str, str]:
    """Every quoted, argv and split form of the short password option."""

    argv = ["mosquitto_pub", OPTION, NEEDLE]
    return {
        "separate": "mosquitto_pub " + OPTION + " " + NEEDLE,
        "attached": "mosquitto_pub " + OPTION + NEEDLE,
        "single_quoted_value": "mosquitto_pub " + OPTION + "'" + NEEDLE + "'",
        "double_quoted_value": "mosquitto_pub " + OPTION + '"' + NEEDLE + '"',
        "double_quoted_option": 'mosquitto_pub "' + OPTION + '" ' + NEEDLE,
        "single_quoted_option": "mosquitto_pub '" + OPTION + "' " + NEEDLE,
        "equals": "mosquitto_pub " + OPTION + "= " + NEEDLE,
        "equals_attached": "mosquitto_pub " + OPTION + "=" + NEEDLE,
        "backslash": "mosquitto_pub \\" + OPTION + " " + NEEDLE,
        "backtick": "mosquitto_pub `" + OPTION + "` " + NEEDLE,
        "python_argv": repr(argv),
        "json_argv": json.dumps(argv),
        "encoded_argv": quote(json.dumps(argv), safe=""),
        "tab_split": "mosquitto_pub -\tP " + NEEDLE,
        "newline_split": "mosquitto_pub -\nP " + NEEDLE,
        "space_split": "mosquitto_pub - P " + NEEDLE,
        "en_dash": "mosquitto_pub –P " + NEEDLE,
        "minus_sign": "mosquitto_pub −P " + NEEDLE,
        "cyrillic_er": "mosquitto_pub -Р " + NEEDLE,
        "greek_rho": "mosquitto_pub -Ρ " + NEEDLE,
        "fullwidth": "mosquitto_pub －Ｐ " + NEEDLE,
    }


def bearer_forms() -> dict[str, str]:
    return {
        "plain": "Authorization: Bearer " + NEEDLE,
        "short": "Bearer " + "ab12" + "cd34",
        "tab_split": "Be\tarer " + NEEDLE,
        "spaced": "B e a r e r " + NEEDLE,
        "newline": "BEARER\n" + NEEDLE,
        "squashed": "bearer" + NEEDLE,
        "colon": "bearer:" + NEEDLE,
    }


KEYWORDS = ("bearer", "password", "passwd", "pwd", "--pw", "authorization", "api key", "apikey", "api_key",
            "secret", "token", "private key")
_CYRILLIC = str.maketrans({"a": "а", "e": "е", "o": "о", "p": "р", "c": "с",
                           "i": "і", "y": "у", "x": "х"})
_CYRILLIC_UPPER = str.maketrans({"A": "А", "E": "Е", "O": "О", "P": "Р", "C": "С"})


def keyword_forms(keyword: str) -> dict[str, str]:
    """A keyword in every case, spacing, encoding and confusable form."""

    return {
        "lower": keyword, "upper": keyword.upper(), "title": keyword.title(),
        "spaced": " ".join(keyword), "tab": keyword[:2] + "\t" + keyword[2:],
        "newline": keyword[:2] + "\n" + keyword[2:],
        "cyrillic": keyword.translate(_CYRILLIC), "upper_cyrillic": keyword.upper().translate(_CYRILLIC_UPPER),
        "fullwidth": "".join(chr(ord(ch) + 0xFEE0) if "!" <= ch <= "~" else ch for ch in keyword),
        "encoded": quote(keyword, safe=""),
    }


def pem_forms() -> dict[str, str]:
    dashes = "-" * 5
    return {
        "rsa": dashes + "BEGIN RSA " + "PRIV" + "ATE KEY" + dashes,
        "split_newline": dashes + "BEGIN OPENSSH PRIV\nATE KEY" + dashes,
        "spaced": dashes + "BEGIN  PRIVATE  " + "KEY" + dashes,
        "lower": dashes + "begin ec priv" + "ate key" + dashes,
        "no_space": dashes + "BEGINPRIVATE" + "KEY" + dashes,
    }


# Ordinary add-on and host lines that must stay shown and complete.
BENIGN = (
    "mosquitto -p 1883 -c /etc/mosquitto/mosquitto.conf",
    "ssh -p 22 root@192.0.2.10",
    "docker run -p 8080:80 nginx",
    "mkdir -p /data/options",
    "rsync -avz --progress /share/ /backup/",
    "rsync -avP /share/ /backup/",
    "iptables -L -n",
    "Kitchen - Pool pump on",
    "Garage - Porch light off",
    "X-Powered-By: Express",
    "Zigbee2MQTT:info  MQTT publish: topic 'zigbee2mqtt/Kitchen', payload '{\"state\":\"ON\"}'",
    "[core-ssh] Port 22 open",
    "2026-10-20 10:00:00.120 INFO (MainThread) [supervisor.core] Supervisor is up and running",
)

# Ordinary lines the label rule now withholds (a disclosed, accepted cost of
# keying on the label): a real -P option that is not a password, and a
# keyword with no credential after it.
BENIGN_WITHHELD = (
    "iptables " + OPTION + " INPUT ACCEPT",
    "grep " + OPTION + " '[0-9]+' /config/home-assistant.log",
    "rsync " + OPTION + " /share/ /backup/",
    "xargs " + OPTION + " 4 -n 1 echo",
    "token refreshed",
    "Authorization header present",
    "the api key rotated",
    "reset your password in the companion app",
)


class ReviewerSuiteTests(unittest.TestCase):
    def test_reviewer_suites_are_unchanged(self):
        for name, expected in REVIEWER_SUITES.items():
            with self.subTest(suite=name):
                self.assertEqual(hashlib.sha256((TESTS / name).read_bytes()).hexdigest(), expected)

    def test_only_the_superseded_method_is_an_expected_failure(self):
        for name in dir(residuals.DeltaResidualTests):
            if name.startswith("test"):
                with self.subTest(method=name):
                    expecting = getattr(getattr(residuals.DeltaResidualTests, name),
                                        "__unittest_expecting_failure__", False)
                    self.assertEqual(expecting, name == SUPERSEDED)

    def test_the_superseded_method_still_runs_and_fails(self):
        result = unittest.TestResult()
        residuals.DeltaResidualTests(SUPERSEDED).run(result)
        self.assertEqual((len(result.expectedFailures), result.unexpectedSuccesses, result.failures, result.errors),
                         (1, [], [], []))


class AnsiScreeningTests(review1.Harness):
    """Review 2 M4 (owner decision, SGR only since review 3): screen the original and the stripped text, show the stripped."""

    async def test_coloured_ordinary_text_is_shown_in_every_field(self):
        for value in ("\x1b[31mordinary\x1b[0m", "\x1b[1;32mUSB\x1b[0m reconnected"):
            shown = value.replace("\x1b[31m", "").replace("\x1b[1;32m", "").replace("\x1b[0m", "")
            for field, source, key in FIELDS:
                with self.subTest(value=value, field=field):
                    page = await self.planted(value.encode(), field, source)
                    entry = page["entries"][0]
                    self.assertEqual(entry[key], shown)
                    self.assertNotIn("withheld", entry)
                    self.assertNotIn("\x1b", json.dumps(page, ensure_ascii=False))

    def test_the_shared_screen_still_refuses_raw_escapes(self):
        # The exemption is the journal's display format, not the shared screen.
        for labelled in (False, True):
            self.assertEqual(credentials.screen("\x1b[31mordinary\x1b[0m", (), labelled=labelled),
                             credentials.UNVERIFIABLE_MARKER)

    async def test_a_credential_split_by_a_colour_code_is_withheld(self):
        # Seen whole only once the sequence is removed.
        for value in ("pass\x1b[1mword=" + NEEDLE, "Be\x1b[0marer " + NEEDLE,
                      "mosquitto_pub -\x1b[1mP " + NEEDLE):
            for field, source, _key in FIELDS:
                with self.subTest(value=value, field=field):
                    page = await self.planted(value.encode(), field, source)
                    self.assertNotIn(NEEDLE, json.dumps(page))
                    self.assertTrue(all("withheld" in entry for entry in page["entries"]))
                    self.assertFalse(page["complete"])

    async def test_a_label_spelled_by_the_sequence_is_withheld(self):
        # The sequence's own final byte would start the label. Since review 3
        # such a sequence is not an SGR code: it is never removed, and the
        # field is withheld as unverifiable.
        for value in ("\x1b[password=" + NEEDLE, "\x1b[Bearer " + NEEDLE, "\x1b[1;Token " + NEEDLE):
            self.assertEqual(hj.screen_text(value.encode(), ()), (hj.UNVERIFIABLE_MARKER, hj.UNVERIFIABLE_OMISSION))
            for field, source, _key in FIELDS:
                with self.subTest(value=value, field=field):
                    page = await self.planted(value.encode(), field, source)
                    self.assertNotIn(NEEDLE, json.dumps(page))
                    self.assertTrue(all("withheld" in entry for entry in page["entries"]))
                    self.assertFalse(page["complete"])

    def test_other_escapes_and_controls_stay_unverifiable(self):
        for value in ("\x1b]0;title\x07ordinary", "ordinary \x1b text", "\x1bMordinary", "ordinary\x1b",
                      "\x1b[" + "1;" * 20 + "mordinary", "\x9b31mordinary", "ordinary\x07", "\x1b[31\x01mx"):
            with self.subTest(value=value):
                self.assertEqual(hj.screen_text(value.encode(), ()), (hj.UNVERIFIABLE_MARKER, hj.UNVERIFIABLE_OMISSION))


class LabelKeyedTests(review1.Harness):
    """Review 2 H2 residual: in the journal's labelled mode the label withholds, the value shape is a second layer."""

    def assert_withheld(self, value: str) -> None:
        self.assertEqual(hj.screen_text(value.encode(), ()), (hj.WITHHELD_MARKER, hj.WITHHELD_OMISSION))

    async def test_every_password_option_form_is_withheld_in_every_field(self):
        for label, value in option_forms().items():
            for field, source, _key in FIELDS:
                with self.subTest(form=label, field=field):
                    page = await self.planted(value.encode(), field, source)
                    self.assertNotIn(NEEDLE, json.dumps(page))
                    self.assertTrue(all("withheld" in entry for entry in page["entries"]))
                    self.assertFalse(page["complete"])

    def test_the_option_tokenizer(self):
        for label, value in option_forms().items():
            with self.subTest(form=label):
                self.assert_withheld(value)
        for value in ("-p 1883", "-avP src", "Kitchen - Pool", "x -- P", "a-P", "-p'1883'"):
            with self.subTest(benign=value):
                self.assertFalse(credentials.password_option(value))
        # Over the scan bound the decision fails closed.
        self.assertTrue(credentials.password_option("x " * (credentials.MAX_OPTION_SCAN // 2 + 1)))

    def test_split_and_short_bearer_forms_are_withheld(self):
        for label, value in bearer_forms().items():
            with self.subTest(form=label):
                self.assert_withheld(value)

    def test_every_keyword_form_is_withheld_with_or_without_a_value(self):
        for keyword in KEYWORDS:
            for label, form in keyword_forms(keyword).items():
                for value in ("service " + form + " rotated", form + "=" + NEEDLE):
                    with self.subTest(keyword=keyword, form=label, value=value):
                        self.assert_withheld(value)
        for label, value in pem_forms().items():
            with self.subTest(pem=label):
                self.assert_withheld(value)

    def test_a_keyword_alone_is_enough(self):
        # No value shape in these: only the label rule withholds them, in
        # either the normal or the squashed form.
        for value in ("token refreshed", "Authorization header present", "the api key rotated",
                      "the pass word was rotated", "to ken refreshed", "a se cret was read",
                      "client ––pw set", "client −−pw given"):
            with self.subTest(value=value):
                self.assert_withheld(value)

    def test_the_default_mode_is_unchanged(self):
        # HAMCP-132: labelled mode stays off for configuration definitions.
        for value in ("token refreshed", "the api key rotated", option_forms()["separate"].replace(NEEDLE, "x")):
            with self.subTest(value=value):
                self.assertIsNone(credentials.screen(value, ()))

    async def test_ordinary_lines_stay_shown_and_complete(self):
        for value in BENIGN:
            with self.subTest(value=value):
                page = await self.planted(value.encode())
                self.assertEqual(page["entries"][0]["message"], value)
                self.assertTrue(page["complete"])

    def test_known_benign_lines_withheld_by_the_label_rule(self):
        for value in BENIGN_WITHHELD:
            with self.subTest(value=value):
                self.assert_withheld(value)

    def test_fixture_corpus_false_positives(self):
        # Every ordinary journal fixture line stays shown; of the HAMCP-191
        # ordinary samples only the three keyword-only lines are withheld.
        fixtures = TESTS / "fixtures"
        lines = [line[len(b"MESSAGE="):] for path in sorted((fixtures / "hamcp224_journal").glob("*.export"))
                 for line in path.read_bytes().split(b"\n")
                 if line.startswith(b"MESSAGE=") and b"PLANTED" not in line]
        self.assertEqual(len(lines), 32)
        for raw in lines:
            with self.subTest(line=raw):
                self.assertIsNone(hj.screen_text(raw, ())[1])
        corpus = json.loads((fixtures / "hamcp191" / "compat_corpus.json").read_text(encoding="utf-8"))
        withheld = set()
        for sample in corpus["samples"]:
            if sample["class"] != "ordinary":
                continue
            value = json.loads(sample["input"])
            if isinstance(value, str) and hj.screen_text(value.encode(), ())[1] is not None:
                withheld.add(sample["id"])
        self.assertEqual(withheld, {"text_16", "text_17", "text_30"})

    def test_the_value_shapes_remain_a_layer_of_their_own(self):
        # The label rule overlaps them, so they are checked apart: dropping
        # one shape must fail here even though a keyword still withholds.
        def value_shape(value: str) -> bool:
            text, _rounds = credentials.canonical(value)
            text = credentials.fold_confusables(text)
            return credentials._value_shape(text, credentials.squash(text))

        bearer = {name: value for name, value in bearer_forms().items() if name != "colon"}
        labelled = {"field": "pass" + "word=" + NEEDLE, "encoded": "api%5f" + "key=" + NEEDLE,
                    "tab": "api_\tkey: " + NEEDLE}
        for name, value in {**pem_forms(), **bearer, **labelled}.items():
            with self.subTest(form=name):
                self.assertTrue(value_shape(value))
        # Keyword-only text is the label rule's alone.
        for value in ("token refreshed", "the api key rotated", "Authorization header present"):
            with self.subTest(value=value):
                self.assertFalse(value_shape(value))
                self.assertEqual(credentials.screen(value, (), labelled=True), credentials.WITHHELD_MARKER)


class LevelFilterTests(review1.Harness):
    """Review 2 M1 residual: the level is the journal PRIORITY, never the message text."""

    async def test_the_level_never_comes_from_the_message_text(self):
        for text in ("CRITICAL: disk failed", "<2>kernel panic", "level=critical msg=down", "ERROR (MainThread) x"):
            with self.subTest(text=text):
                page = await self.planted(text.encode())
                entry = page["entries"][0]
                self.assertEqual((entry["level"], entry["level_basis"]), ("info", hj.LEVEL_BASIS))
                page = await self.planted(text.encode(), min_level="critical")
                self.assertEqual((page["returned"], page["complete"]), (0, True))

    async def test_the_priority_field_sets_the_level(self):
        library = review1.NativeFieldOverlay("PRIORITY", b"2")
        service = self.service(author.reader(self.helper.journal, library=lambda: library))
        page = await self.helper.read(service=service, sources=["supervisor"], start=author.iso(0),
                                      end=author.iso(1), min_level="critical")
        self.assertEqual(page["entries"][0]["level"], "critical")

    async def test_a_withheld_entry_left_out_by_its_level_is_partial(self):
        page = await self.planted(("pass" + "word=" + NEEDLE).encode(), min_level="critical")
        row = page["sources"][0]
        self.assertEqual((page["returned"], row["skipped_by_filter"], row["withheld_level_filtered"],
                          row["withheld_not_searched"], row["status"]), (0, 1, 1, 0, "partial"))
        self.assertIn("withheld_level_filtered:supervisor", page["omissions"])
        self.assertFalse(page["complete"])
        # Shown at its level, it is not counted as level-filtered.
        page = await self.planted(("pass" + "word=" + NEEDLE).encode())
        self.assertEqual(page["sources"][0]["withheld_level_filtered"], 0)


if __name__ == "__main__":
    unittest.main()
