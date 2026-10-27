"""HAMCP-225 review H1: the HAMCP-132 and HAMCP-224 reviewer credential cases on the trace paths.

Every credential case from the HAMCP-132 reviewer suites
(``test_pr294_delta_review``, ``test_pr294_corr2_extra``,
``test_pr294_final32_review``) and the HAMCP-224 reviewer suites
(``test_pr313_independent_review``, ``test_pr313_delta_residuals`` and the
review 3 reproduction ``test_review2_ansi_residual``) is planted in every
trace position, as a value and as a dictionary key, and sent through both
trace paths:

* the snapshot capture path: it must be withheld before anything is
  written (the store never holds it), on read-back, and the snapshot is
  partial; and
* ``get_automation_trace``: it must be withheld and the read is partial.

The ``forms()`` tables are imported from the reviewer suites themselves;
cases written inline in a reviewer test are rebuilt here from the same
expression and named after it. Synthetic values only, assembled at run
time; offline.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

TESTS = Path(__file__).resolve().parent
sys.path[:0] = [str(TESTS), str(TESTS.parent / "hass_mcp_engineering_beta"), str(TESTS.parent)]

import test_hamcp132_configuration_definitions as t132  # noqa: E402
import test_hamcp224_host_journal as t224  # noqa: E402
import test_hamcp225_trace_snapshots as a  # noqa: E402
import test_pr294_corr2_extra as corr2  # noqa: E402
import test_pr294_delta_review as delta294  # noqa: E402
import test_pr294_final32_review as final32  # noqa: E402
import test_pr313_delta_residuals as residuals313  # noqa: E402
import test_review2_ansi_residual as ansi3  # noqa: E402
from ha_mcp_engineering.configuration_definitions.contracts import MAX_TEXT, MAX_TITLE  # noqa: E402
from ha_mcp_engineering.trace_normalization import TRACE_STRING_LIMIT  # noqa: E402


PASSWORD = "pass" + "word"
PLANTED = "PLANTED"
KNOWN_SECRETS = (t132.ACCESS_SECRET, t132.HA_TOKEN, t224.KNOWN_SECRET)


def _padded(value, bound):
    return "x" * (bound - len(value) - 1) + " " + value + "@synthetic.invalid"


def cases():
    """``{name: (value, needle)}``; ``needle`` must never be stored or returned."""

    found = {}

    def add(source, label, value, needle):
        found[f"{source}:{label}"] = (value, needle)

    # HAMCP-132: test_pr294_delta_review
    for label, value in delta294.forms().items():
        add("pr294_delta.forms", label, value, t132.DB_PASSWORD)
    for label, change in (("lower", str.lower), ("upper", str.upper), ("swapcase", str.swapcase)):
        variant = change(t132.ACCESS_SECRET)
        add("pr294_delta.known_secret_case", label, variant, variant)
    for prefix in ("", "https://"):
        credential = prefix + t132.DB_USER + ":" + t132.DB_PASSWORD
        for bound_name, bound in (("title", MAX_TITLE), ("trace", TRACE_STRING_LIMIT)):
            add("pr294_delta.truncation", f"{prefix or 'bare'}:{bound_name}",
                _padded(credential, bound), t132.DB_PASSWORD)
    add("pr294_delta.confusable", "ratio_division",
        t132.db_url().replace(":", "∶").replace("/", "∕"), t132.DB_PASSWORD)
    add("pr294_delta.confusable", "cyrillic_o_password",
        "https://synthetic.invalid/?passwоrd=" + t132.DB_PASSWORD, t132.DB_PASSWORD)

    # HAMCP-132: test_pr294_corr2_extra
    needle = corr2.NEEDLE
    for scheme in ("", "https://", "anything+driver://", "mysql://", "mqtt://"):
        credential = scheme + "synthetic:" + needle
        add("pr294_corr2.title_truncation", f"{scheme or 'bare'}:middle",
            _padded(credential, MAX_TITLE), needle)
        add("pr294_corr2.title_truncation", f"{scheme or 'bare'}:beyond",
            "x" * MAX_TITLE + " " + credential + "@synthetic.invalid", needle)
        add("pr294_corr2.title_truncation", f"{scheme or 'bare'}:trace",
            _padded(credential, TRACE_STRING_LIMIT), needle)
    for point in corr2.INVISIBLES:
        add("pr294_corr2.invisible", hex(point),
            "https://synthetic.invalid/?pass" + chr(point) + "word=" + needle, needle)
        add("pr294_corr2.invisible_encoded", hex(point),
            "https://synthetic.invalid/?pass" + quote(chr(point), safe="") + "word=" + needle, needle)
    for word in ("pαssword", "pаsswοrd", "passwоrd"):
        add("pr294_corr2.confusable", ascii(word), "https://synthetic.invalid/?" + word + "=" + needle,
            needle)
    for prefix in ("", "https://", "anything://"):
        add("pr294_corr2.text_bound", prefix or "bare",
            _padded(prefix + "synthetic:" + needle, MAX_TEXT), needle)

    # HAMCP-132: test_pr294_final32_review
    needle = final32.NEEDLE
    for point in (9, 10, 13):
        for rounds in range(3):
            marker = chr(point)
            for _ in range(rounds):
                marker = quote(marker, safe="")
            add("pr294_final32.control_password", f"{hex(point)}:{rounds}",
                "https://synthetic.invalid/?pass" + marker + "word=" + needle, needle)
    for point in (0x1F, 0x200E, 0xD800, 0xE123, 0x378, 0x2028, 0x2029, 0x034F, 0x115F, 0xFE0F):
        for rounds in range(3):
            text = "Probe" + chr(point) + "tail"
            for _ in range(rounds):
                text = quote(text, safe="", errors="surrogatepass")
            # Not a credential: an unverifiable form, which is never kept as is.
            add("pr294_final32.unicode_category", f"{hex(point)}:{rounds}", text, text)
    add("pr294_final32.uri", "db_url", final32.URI, needle)

    # HAMCP-224: test_pr313_independent_review
    user, secret = PLANTED + "USER", PLANTED + "PASS"
    for prefix in (user + ":" + secret, "https://" + user + ":" + secret):
        add("pr313_independent.truncated_uri", prefix.split(":")[0],
            prefix + "x" * 16400 + "@host", PLANTED)
    api, token = "api", PLANTED + "TOKEN0123456789"
    for label, value in (
            ("percent_underscore", api + "%5fkey=" + token),
            ("tab_underscore", api + "_\tkey=" + token),
            ("cyrillic_e", api + "_kеy=" + token),
            ("percent_authorization", "Authoriz%61tion: Be%61rer " + token),
            ("pw_tab", "mosquitto_pub --p\tw " + PLANTED + "PASS0123456789")):
        add("pr313_independent.obfuscated", label, value, PLANTED)
    add("pr313_independent.metadata", "labelled", PASSWORD + "=" + PLANTED + "METAPASS", PLANTED)
    add("pr313_independent.filtered", "labelled", PASSWORD + "=" + PLANTED + "FILTERED", PLANTED)
    for label, value in (("upper", t224.KNOWN_SECRET.upper()),
                         ("split", t224.KNOWN_SECRET.replace("known-", "known-\t")),
                         ("encoded", t224.KNOWN_SECRET.replace("-", "%2D"))):
        add("pr313_independent.known_secret", label, value, value)

    # HAMCP-224: test_pr313_delta_residuals
    for label, value in residuals313.forms().items():
        add("pr313_residuals.forms", label, value, residuals313.NEEDLE)
    add("pr313_residuals.control_text", "sgr", "\x1b[31mordinary\x1b[0m", "\x1b")
    add("pr313_residuals.level_filtered", "labelled", PASSWORD + "=" + residuals313.NEEDLE,
        residuals313.NEEDLE)

    # HAMCP-224 review 3: test_review2_ansi_residual
    for label, value in ansi3.forms().items():
        add("review3_ansi.forms", label, value, ansi3.S)
    for value in ("\x1b[" + PASSWORD + "=" + ansi3.S, "pass\x1b[32mword=" + ansi3.S,
                  "\x1b[Bearer " + ansi3.S, "Be\x1b[0marer " + ansi3.S):
        add("review3_ansi.components", ascii(value.split("=")[0].split(" ")[0]), value, ansi3.S)
    return found


def _nested(value, depth=30):
    for _ in range(depth):
        value = {"n": value}
    return value


POSITIONS = {
    "variables": lambda d, v: d["config"].update(variables={"message": v}),
    "variables_key": lambda d, v: d["config"].update(variables={v: "benign"}),
    "context": lambda d, v: d["context"].update(note=v),
    "context_key": lambda d, v: d["context"].update({v: {"nested": "benign"}}),
    "changed_variables": lambda d, v: d["trace"]["action/0"][0].update(changed_variables={"message": v}),
    "changed_variables_key": lambda d, v: d["trace"]["action/0"][0].update(changed_variables={v: 1}),
    "service_data": lambda d, v: d["config"].update(
        action=[{"action": "notify.notify", "data": {"message": v}}]),
    "template": lambda d, v: d["config"].update(
        action=[{"action": "notify.notify", "data": {"message": "{{ '" + v + "' }}"}}]),
    "error": lambda d, v: d.update(error=v),
    "trigger": lambda d, v: d.update(trigger=v),
    "deep_value": lambda d, v: d["context"].update(deep=_nested({"message": v})),
    "deep_key": lambda d, v: d["context"].update(deep=_nested({v: "benign"})),
}


def planted(run_id, position, value):
    detail = {**a.header(run_id), "config": {"alias": "Synthetic"}, "context": {"id": "ctx"},
              "trace": {"action/0": [{"path": "action/0", "result": {"ok": True}}]}}
    POSITIONS[position](detail, value)
    return detail


def strings(value):
    """Every string and dictionary key in a decoded JSON value."""

    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def holds(value, needle):
    return any(needle in text for text in strings(value))


def store_holds(root, needle):
    """The raw bytes, and every decoded JSON string, of every stored file."""

    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if needle in raw.decode("utf-8", "replace"):
            return True
        for line in raw.splitlines() or [raw]:
            try:
                decoded = json.loads(line)
            except ValueError:
                continue
            if holds(decoded, needle):
                return True
        try:
            if holds(json.loads(raw), needle):
                return True
        except ValueError:
            pass
    return False


class CrossPathCaseTests(unittest.TestCase):
    def test_every_reviewer_suite_contributes_cases(self):
        sources = {name.split(":")[0].split(".")[0] for name in cases()}
        self.assertEqual(sources, {"pr294_delta", "pr294_corr2", "pr294_final32",
                                   "pr313_independent", "pr313_residuals", "review3_ansi"})
        self.assertGreaterEqual(len(cases()), 100)


class CapturePathTests(a.ServiceCase):
    """Withheld before storage, on read-back, and marked partial."""

    async def test_every_case_in_every_position_is_withheld_on_the_capture_path(self):
        for name, (value, needle) in cases().items():
            with tempfile.TemporaryDirectory(dir=self.root) as directory:
                service = a.TraceSnapshotService(Path(directory), self.core.command, self.runtime,
                                                 self.audit, known_secrets=KNOWN_SECRETS,
                                                 clock=self.clock)
                self.core.runs, self.core.details = [], {}
                for number, position in enumerate(POSITIONS):
                    run_id = f"r{number}"
                    self.core.add(run_id)
                    self.core.details[run_id] = planted(run_id, position, value)
                for number, position in enumerate(POSITIONS):
                    with self.subTest(case=name, position=position):
                        captured, _ = await service.capture(automation_id="a1", run_id=f"r{number}")
                        run = captured["runs"][0]
                        self.assertEqual(run["outcome"], "captured")
                        self.assertEqual(run["completeness"], "partial")
                        page, partial = await service.get(snapshot_id=run["snapshot_id"])
                        self.assertTrue(partial)
                        self.assertFalse(holds(page, needle), "returned on read-back")
                        self.assertFalse(holds(captured, needle), "returned by capture")
                with self.subTest(case=name, sink="store"):
                    self.assertFalse(store_holds(directory, needle), "written to the store")


class GetAutomationTracePathTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_case_in_every_position_is_withheld_by_get_automation_trace(self):
        for name, (value, needle) in cases().items():
            for position in POSITIONS:
                with self.subTest(case=name, position=position):
                    async def command(payload, position=position):
                        return planted("r1", position, value)
                    with patch.object(a.compatibility, "ws_command", command), \
                            patch.object(a.compatibility, "ACCESS_SECRET", KNOWN_SECRETS[0]), \
                            patch.object(a.compatibility, "HA_TOKEN", KNOWN_SECRETS[2]):
                        raw = await a.compatibility.get_automation_trace("a1", "r1")
                    envelope = json.loads(raw)
                    self.assertFalse(holds(envelope, needle))
                    self.assertEqual(envelope["metadata"]["completeness"], "partial")


if __name__ == "__main__":
    unittest.main(verbosity=2)
