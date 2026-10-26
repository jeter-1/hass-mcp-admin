"""HAMCP-224 review 3 correction (H3): only SGR colour codes are removed for display.

Synthetic fixtures only; no live Home Assistant and no host journal. Every
credential-shaped payload is assembled at run time, so this file holds no
secret-shaped literal.

Review 3 found that removing every well-formed CSI sequence let two of them
hide a label from both screened forms: ``ESC[p`` takes the ``p`` of
``password`` as its final byte, and a colour code inside the rest of the
word is removed too. The owner-approved correction narrows the review 2 M4
exemption to SGR codes (``ESC [ [0-9;]{0,32} m``), whose bytes hold no label
character. Any other escape is never removed and withholds the field as
unverifiable. The reviewer's reproduction suite
(``test_review2_ansi_residual``) is kept byte for byte.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import sys
import unittest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))
sys.path.insert(0, str(TESTS.parent / "hass_mcp_engineering_beta"))

import test_hamcp224_review2_corrections as review2  # noqa: E402  (registers the superseded method)
import test_pr313_delta_residuals as residuals  # noqa: E402
import test_pr313_independent_review as independent  # noqa: E402
import test_review2_ansi_residual as combined  # noqa: E402

hj = review2.hj
credentials = review2.credentials
review1 = review2.review1

REVIEWER_SUITES = {
    **review2.REVIEWER_SUITES,
    "test_review2_ansi_residual.py": "c3d604680a6e9d85cf36e83aceff04859702836f0dad9a1d6cc33f8d16e4265b",
}

NEEDLE = "SYNTHETIC" + "sgrOnly79"
FIELDS = review2.FIELDS
UNVERIFIABLE = (hj.UNVERIFIABLE_MARKER, hj.UNVERIFIABLE_OMISSION)
WITHHELD = (hj.WITHHELD_MARKER, hj.WITHHELD_OMISSION)
# Every CSI final byte but the SGR's m.
FINAL_BYTES = tuple(chr(code) for code in range(0x40, 0x7F) if chr(code) != "m")
# Labels as words, each split below at every inner position.
LABELS = ("pass" + "word", "passwd", "token", "bearer", "secret", "authorization", "api_key", "credentials")
SGR_CODES = ("\x1b[m", "\x1b[0m", "\x1b[1;31m", "\x1b[38;5;208m", "\x1b[1m\x1b[31m", "\x1b[;m")


def splits(label: str):
    return ((label[:index], label[index:]) for index in range(1, len(label)))


class ReviewerSuiteTests(unittest.TestCase):
    def test_reviewer_suites_are_unchanged(self):
        for name, expected in REVIEWER_SUITES.items():
            with self.subTest(suite=name):
                self.assertEqual(hashlib.sha256((TESTS / name).read_bytes()).hexdigest(), expected)

    def test_the_superseded_method_is_the_only_expected_failure_in_any_reviewer_suite(self):
        expecting = []
        for module in (independent, residuals, combined):
            for _name, case in inspect.getmembers(module, inspect.isclass):
                if not issubclass(case, unittest.TestCase) or case.__module__ != module.__name__:
                    continue
                for method in dir(case):
                    if method.startswith("test") and getattr(getattr(case, method),
                                                             "__unittest_expecting_failure__", False):
                        expecting.append(f"{module.__name__}.{case.__name__}.{method}")
        self.assertEqual(expecting, ["test_pr313_delta_residuals.DeltaResidualTests." + review2.SUPERSEDED])

    def test_the_superseded_method_uses_only_sgr_codes(self):
        # It still fails for the reason the owner superseded: colours are shown.
        source = inspect.getsource(getattr(residuals.DeltaResidualTests, review2.SUPERSEDED))
        self.assertIn("'\\x1b[31mordinary\\x1b[0m'", source)
        self.assertEqual(hj.screen_text("\x1b[31mordinary\x1b[0m".encode(), ()), ("ordinary", None))


class SgrOnlyTests(review1.Harness):
    """H3: an SGR code is removed for display; any other escape withholds the field."""

    async def test_the_combined_forms_are_withheld_in_every_field(self):
        # The reviewer's three forms and every split of every label: a
        # letter-final CSI starting the label, a colour code inside it.
        values = [value for value in combined.forms().values()]
        values += ["\x1b[" + head + "\x1b[32m" + tail + "=" + NEEDLE for label in LABELS for head, tail in splits(label)]
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(hj.screen_text(value.encode(), ()), UNVERIFIABLE)
        for value in values[:3] + values[3::7]:
            for field, source, _key in FIELDS:
                with self.subTest(value=value, field=field):
                    page = await self.planted(value.encode(), field, source)
                    self.assertNotIn(combined.S, json.dumps(page))
                    self.assertNotIn(NEEDLE, json.dumps(page))
                    self.assertTrue(all("withheld" in entry for entry in page["entries"]))
                    self.assertFalse(page["complete"])

    async def test_every_other_csi_final_byte_withholds(self):
        for final in FINAL_BYTES:
            for value in ("pass\x1b[1" + final + "word=" + NEEDLE, "\x1b[" + final + "pass" + "word=" + NEEDLE,
                          "to\x1b[" + final + "ken " + NEEDLE, "\x1b[" + final + "mount /data",
                          "\x1b[2" + final + "ordinary", "\x1b[1 " + final + "ordinary"):
                with self.subTest(final=final, value=value):
                    self.assertEqual(hj.screen_text(value.encode(), ()), UNVERIFIABLE)
        for field, source, _key in FIELDS:
            for final in ("p", "B", "K", "J", "@", "`", "~"):
                with self.subTest(final=final, field=field):
                    page = await self.planted(("pass\x1b[" + final + "word=" + NEEDLE).encode(), field, source)
                    self.assertNotIn(NEEDLE, json.dumps(page))
                    self.assertTrue(all("withheld" in entry for entry in page["entries"]))
                    self.assertFalse(page["complete"])

    def test_other_escapes_are_never_removed(self):
        for value in ("\x1b[2Jtidy", "\x1b[0Kline", "\x1b[Hhome", "\x1b[?25lhidden", "\x1b]0;title\x07x",
                      "\x1b]8;;http://example.invalid\x1b\\link", "\x1bMup", "\x1b(Bascii", "x\x1b", "\x1b[31",
                      "\x1b[31;m\x1b[", "\x1b[1:2m", "\x1b[>4;1m", "\x1b[ m", "\x9b31mordinary"):
            with self.subTest(value=value):
                self.assertEqual(hj.screen_text(value.encode(), ()), UNVERIFIABLE)

    async def test_a_label_split_by_a_colour_code_is_withheld_in_the_stripped_form(self):
        for label in LABELS:
            for head, tail in splits(label):
                for code in SGR_CODES:
                    value = head + code + tail + "=" + NEEDLE
                    with self.subTest(value=value):
                        self.assertEqual(hj.screen_text(value.encode(), ()), WITHHELD)
        for field, source, _key in FIELDS:
            for code in SGR_CODES:
                with self.subTest(code=code, field=field):
                    page = await self.planted(("pass" + code + "word=" + NEEDLE).encode(), field, source)
                    self.assertNotIn(NEEDLE, json.dumps(page))
                    self.assertTrue(all("withheld" in entry for entry in page["entries"]))
                    self.assertFalse(page["complete"])

    async def test_coloured_ordinary_lines_are_shown_in_every_field(self):
        lines = {
            "\x1b[32mSupervisor is up\x1b[0m": "Supervisor is up",
            "\x1b[1m\x1b[31mUSB\x1b[0m\x1b[0m reconnected": "USB reconnected",
            "\x1b[38;5;208mZigbee2MQTT:info\x1b[39m  MQTT publish": "Zigbee2MQTT:info  MQTT publish",
            "\x1b[mreset\x1b[;m and \x1b[0;1;4mbold\x1b[22;24m": "reset and bold",
            "INFO\x1b[0m\x1b[0m\x1b[0m:" + " mounted": "INFO: mounted",
        }
        for value, shown in lines.items():
            self.assertEqual(hj.screen_text(value.encode(), ()), (shown, None))
            for field, source, key in FIELDS:
                with self.subTest(value=value, field=field):
                    page = await self.planted(value.encode(), field, source)
                    entry = page["entries"][0]
                    self.assertEqual(entry[key], shown)
                    self.assertNotIn("withheld", entry)
                    self.assertTrue(page["complete"])
                    self.assertNotIn("\x1b", json.dumps(page, ensure_ascii=False))

    def test_the_sgr_code_is_bounded_and_removed_once(self):
        self.assertEqual(hj.screen_text(("\x1b[" + "1;" * 16 + "mordinary").encode(), ()), ("ordinary", None))
        self.assertEqual(hj.screen_text(("\x1b[" + "1;" * 16 + "1mordinary").encode(), ()), UNVERIFIABLE)
        self.assertEqual(hj.screen_text(("\x1b[" + "9" * 200 + "mordinary").encode(), ()), UNVERIFIABLE)
        # One pass: a code assembled by removing another is a stray escape.
        for value in ("\x1b[3\x1b[1m1mordinary", "\x1b\x1b[0m[31mordinary", "\x1b[\x1b[0mmordinary"):
            with self.subTest(value=value):
                self.assertEqual(hj.screen_text(value.encode(), ()), UNVERIFIABLE)

    async def test_a_label_behind_a_refused_escape_is_unverifiable_not_matched(self):
        # Removing more than one SGR code (a letter in it, any length, or a
        # code assembled by a removal) would expose the label, a credential
        # match. The escape stays, so the field is withheld as unverifiable.
        values = ["pass\x1b[1" + final + "mword=" + NEEDLE for final in FINAL_BYTES if final.isalpha()]
        values += ["pass\x1b[" + "1" * 33 + "mword=" + NEEDLE, "pass\x1b[" + "9" * 200 + "mword=" + NEEDLE,
                   "pass\x1b[3\x1b[1m1mword=" + NEEDLE, "pass\x1b\x1b[0m[31mword=" + NEEDLE,
                   "pass\x1b[\x1b[0mmword=" + NEEDLE]
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(hj.screen_text(value.encode(), ()), UNVERIFIABLE)
        for field, source, _key in FIELDS:
            for value in (values[0], values[-5], values[-3]):
                with self.subTest(value=value, field=field):
                    page = await self.planted(value.encode(), field, source)
                    self.assertNotIn(NEEDLE, json.dumps(page))
                    self.assertEqual(page["entries"][0]["withheld"], hj.UNVERIFIABLE_OMISSION)
                    self.assertFalse(page["complete"])

    def test_the_original_form_is_still_screened(self):
        # Text the code's own characters spell: only the original with the
        # ESC removed holds it.
        secret = "synth" + "[1m" + "q" * 12
        value = "synth\x1b[1m" + "q" * 12
        self.assertIsNone(credentials.screen(hj._SGR.sub("", value), (secret,), labelled=True))
        self.assertEqual(hj.screen_text(value.encode(), (secret,)), WITHHELD)
        value = "login user:\x1b[0m@host.example.invalid"
        self.assertIsNone(credentials.screen(hj._SGR.sub("", value), (), labelled=True))
        self.assertEqual(hj.screen_text(value.encode(), ()), WITHHELD)


if __name__ == "__main__":
    unittest.main()
