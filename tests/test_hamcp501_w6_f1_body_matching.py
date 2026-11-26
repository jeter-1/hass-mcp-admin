"""HAMCP-501 W6 F1: a route body match cannot confirm a secret value.

Codex r1 F1 (High): ha-mcp 9.2.0's component scores a numeric body leaf by
its ``str()`` without the secret scrub, and its automation, script and scene
pages carry no degraded-scrub flag. Native search therefore drops the route's
body match, keeping a native name rescore and graph references, when the
query could match a number (the A'+ trigger, decided by Josh as relayed by the
architect, 2026-11-26), and when a sub-call page or the helper probe read
before and after the scans does not show a clean scrub.

The upstream results are the committed golden
(``docs/evidence/hamcp501/f1``), which its generator produced by executing the
pinned 9.2.0 component offline with synthetic secrets. ``GoldenTests`` bind
the golden to its source and to the native constants;
``AcceptanceTests`` carry every golden result through native search over the
9.2.0 gateway's page readers, under both descriptors. No test prints a
reviewed scrub-degraded text: those are compared by sha256 or by
``assertFalse`` with a fixed message.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT), str(ROOT / "tests")]

from tests import test_hamcp501_search_subcall_readers as w4  # noqa: E402
from tests.test_hamcp481_native_search_engine import MARKER, reasons_of  # noqa: E402
from ha_mcp_engineering.providers import upstream_read_gateway as gw  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_2_keys as k  # noqa: E402
from ha_mcp_engineering.search import configs, reply, subcalls  # noqa: E402

F1 = ROOT / "docs/evidence/hamcp501/f1"
GENERATOR = F1 / "generate_body_match_fixtures.py"
GOLDEN_BYTES = (F1 / "body-match-golden.json").read_bytes()
GOLDEN = json.loads(GOLDEN_BYTES)
PROVENANCE = json.loads((F1 / "provenance.json").read_text(encoding="utf-8"))


def _generator():
    """The generator's module, for its pinned constants and canonical
    rendering; nothing upstream is loaded or run."""

    spec = importlib.util.spec_from_file_location("hamcp501_f1_generator", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN = _generator()


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


BODY_CODES = ("body_numeric_query", "body_scrub_degraded", "body_scrub_unverified")
NUMERIC_KINDS = ("numeric_secret_full", "numeric_secret_substring", "numeric_secret_near",
                 "numeric_secret_mixed", "float_secret_full", "legit_numeric")
SECRET_KINDS = ("numeric_secret_full", "numeric_secret_substring", "numeric_secret_near",
                "numeric_secret_mixed", "float_secret_full", "string_secret_full",
                "string_secret_substring", "string_secret_near", "list_secret_member")
# The disclosed residual (for 2.11): a list or dict secret is never collected
# by 9.2.0 ``_collect_secret_strings``, so its members stay matchable.
RESIDUAL = ("list_secret_member", "clean")

# The A'+ trigger as decided, written here independently of the source:
# exact mode, a non-empty query of only 0-9 . + - e : T and space (after
# lowercasing); fuzzy mode, that or any token holding an ASCII digit. The
# "inf" and "nan" forms are narrowed out (disclosed).
NUMBER_CHARS = frozenset("0123456789.+-e:t ")
SPLIT = re.compile(GOLDEN["constants"]["_SPLIT_RE"])


def tokens(query):
    return [token for token in SPLIT.split(query.strip().lower()) if token]


def trigger(query, exact):
    q = query.strip().lower()
    if not q:
        return False
    if set(q) <= NUMBER_CHARS:
        return True
    return not exact and any(set(token) & set("0123456789") for token in tokens(query))


def golden_score(bucket, variant, query, exact):
    (row,) = [row for row in GOLDEN["scores"] if (row["bucket"], row["variant"], row["query"],
                                                  row["exact"]) == (bucket, variant, query, exact)]
    return row["result"]


TIERS = {(row["bucket"], row["query"], row["exact"]): row["tier"] for row in GOLDEN["name_tiers"]}
DEGRADED_TEXT = {sha: text for text in k.SEARCH_SCRUB_DEGRADED_WARNINGS
                 for sha in (sha256(text),)}
# The text a 9.2.0 helper page carries when the scrub degraded and the
# search did not ask for bodies (``include_config`` is never forwarded).
MATCH_TEXT = DEGRADED_TEXT[GOLDEN["degraded_text_sha256"]["SCRUB_DEGRADED_MATCH_WARNING"]]
PAGE_TYPE = {"automation": "automation", "helper": "helper", "flow": "helper"}
FLOW_ENTRY = "01JSYNTHETICFLOW"


def upstream_row(bucket, result):
    """The 9.2.0 component row (``_normalize_component_config_record``) the
    golden subject scores as ``result``."""

    score, in_name, in_config = result
    flags = {"score": score, "match_in_name": in_name, "match_in_config": in_config,
             "match_in_references": False}
    if bucket == "automation":
        return {"entity_id": "automation.front_gate", "friendly_name": "Front gate", **flags}
    if bucket == "helper":
        return {"entity_id": "timer.front_gate", "helper_type": "timer", "name": "Front gate",
                **flags}
    return {"entry_id": FLOW_ENTRY, "helper_type": "template", "name": "Front gate flow",
            "options_withheld": True, **flags}


def subject_records(result, bucket):
    if bucket == "automation":
        return [record for record in result.get("automations", ())
                if record.get("entity_id") == "automation.front_gate"]
    if bucket == "helper":
        return [record for record in result.get("helpers", ())
                if record.get("entity_id") == "timer.front_gate"]
    return [record for record in result.get("helpers", ())
            if record.get("name") == "Front gate flow"]


def flags_of(records):
    return [(record["score"], record["match_in_name"], record["match_in_config"])
            for record in records]


class GoldenTests(unittest.TestCase):
    """The golden is the generator's output over the pinned source, shows the
    hole Codex reported, and agrees with the native constants."""

    def test_the_golden_is_the_generators_canonical_output_of_the_pinned_source(self):
        self.assertEqual(GEN.render(GOLDEN), GOLDEN_BYTES)
        self.assertEqual(hashlib.sha256(GOLDEN_BYTES).hexdigest(), PROVENANCE["golden"]["sha256"])
        self.assertEqual(hashlib.sha256(GENERATOR.read_bytes()).hexdigest(),
                         PROVENANCE["generator"]["sha256"])
        self.assertEqual(GOLDEN["source"]["commit"], GEN.SOURCE_COMMIT)
        self.assertEqual(PROVENANCE["source"]["commit"], GEN.SOURCE_COMMIT)
        self.assertEqual(GOLDEN["source"]["sha256"], GEN.SOURCE_SHA256)
        self.assertEqual(PROVENANCE["source"]["files"], GEN.SOURCE_SHA256)
        # Every subject, variant, query and mode was scored.
        self.assertEqual(
            {(row["bucket"], row["variant"], row["kind"], row["query"], row["exact"])
             for row in GOLDEN["scores"]},
            {(bucket, variant, kind, query, exact) for bucket, _, _ in GEN.SUBJECTS
             for variant in GEN.SCORED_VARIANTS for kind, query in GEN.QUERIES
             for exact in (True, False)})
        self.assertEqual(len(GOLDEN["scores"]), len(GOLDEN["name_tiers"]) * 2)

    def test_the_golden_shows_the_hole_and_the_degraded_scrub(self):
        # Codex's repro: a substring of a numeric secret matches the body
        # with the scrub loaded clean.
        self.assertEqual(golden_score("automation", "clean", "2468", True), [100, False, True])
        self.assertEqual(golden_score("helper", "clean", "2468", False), [100, False, True])
        # A string secret is scrubbed when the scrub loads, and not when it
        # degrades.
        for exact in (True, False):
            self.assertIsNone(golden_score("automation", "clean", "quasarvelvet", exact))
            self.assertEqual(golden_score("automation", "degraded", "quasarvelvet", exact),
                             [100, False, True])
        self.assertEqual(golden_score("automation", "clean", "nebulaorchid", True),
                         [100, False, True])
        loads = {row["variant"]: row for row in GOLDEN["scrub_loads"]}
        self.assertIs(loads["clean"]["holds"]["24681357"], True)
        self.assertEqual((loads["degraded"]["degraded"], loads["degraded"]["size"]), (True, 0))
        # Only a helper search reads the degraded flag (``_search_prep``).
        for row in GOLDEN["prep"]:
            with self.subTest(prep=row):
                helper = row["search_types"] is None or "helper" in row["search_types"]
                if helper:
                    self.assertIs(row["degraded"], row["variant"] == "degraded")
                else:
                    self.assertIsNone(row["degraded"])

    def test_the_reviewed_texts_are_the_upstream_texts(self):
        self.assertEqual({sha256(text) for text in k.SEARCH_SCRUB_DEGRADED_WARNINGS},
                         set(GOLDEN["degraded_text_sha256"].values()))
        texts = {entry["text"] for entry in GOLDEN["page_warning_texts"]}
        self.assertLessEqual(k.SEARCH_NEUTRAL_PAGE_WARNINGS, texts)
        self.assertIn(w4.SKIP, k.SEARCH_NEUTRAL_PAGE_WARNINGS)
        self.assertIn(GOLDEN["helper_flows_degraded_warning"], k.SEARCH_NEUTRAL_PAGE_WARNINGS)
        self.assertIn(k.SEARCH_PARTIAL_MIRROR_PREFIX, texts)
        # The others are the two composed-name notes, which apply only when
        # ``result_fields`` names ``name``; a sub-call page never sends it.
        others = [entry for entry in GOLDEN["page_warning_texts"]
                  if entry["text"] not in k.SEARCH_NEUTRAL_PAGE_WARNINGS
                  and entry["text"] != k.SEARCH_PARTIAL_MIRROR_PREFIX]
        self.assertEqual([entry["file"] for entry in others], [GEN.ENTITIES_PATH] * 2)
        for search_type in subcalls.SUBCALL_TYPES:
            self.assertNotIn("result_fields", subcalls.page_arguments(search_type, "q", True, 0, 1))

    def test_the_scrub_states_agree(self):
        self.assertEqual(subcalls.SCRUB_STATES, k.SCRUB_STATES)
        self.assertEqual((subcalls.SCRUB_CLEAN, subcalls.SCRUB_DEGRADED, subcalls.SCRUB_UNVERIFIED),
                         (k.SCRUB_CLEAN, k.SCRUB_DEGRADED, k.SCRUB_UNVERIFIED))

    def test_the_trigger_is_the_decided_predicate(self):
        for row in GOLDEN["tokens"]:
            with self.subTest(query=row["query"]):
                self.assertEqual(tokens(row["query"]), row["tokens"])
        extra = ("-inf", "Info", "NaN", "1E5", " 12 ", "kitchen2", "kitchen two", "T", ":",
                 "12:30 pm", "light.kitchen_2", "front gate", "x", "0")
        queries = ({row["query"] for row in GOLDEN["scores"]}
                   | {row["query"] for row in GOLDEN["tokens"]} | set(extra))
        for query in sorted(queries):
            for exact in (True, False):
                with self.subTest(query=query, exact=exact):
                    self.assertIs(subcalls.body_match_unsafe(query, exact), trigger(query, exact))
        pinned = {
            ("2468", True): True, ("gate 24681358", True): False,
            ("gate 24681358", False): True, ("10:00", True): True,
            ("2026-01-01T10:00:00", True): True, ("kitchen 2", True): False,
            ("kitchen 2", False): True, ("１２", False): False, ("", False): False,
            ("  ", True): False, ("front gate", False): False,
            # Narrowed (disclosed): the "inf" and "nan" forms.
            ("info", False): False, ("nano", False): False, ("inf", True): False,
            ("nan", False): False,
        }
        for (query, exact), expected in pinned.items():
            with self.subTest(pinned=query, exact=exact):
                self.assertIs(subcalls.body_match_unsafe(query, exact), expected)

    def test_the_trigger_covers_every_numeric_body_hit(self):
        hits = 0
        for row in GOLDEN["scores"]:
            if row["kind"] in NUMERIC_KINDS and row["result"] is not None and row["result"][2]:
                hits += 1
                with self.subTest(row=row):
                    self.assertTrue(trigger(row["query"], row["exact"]))
        self.assertGreater(hits, 0)

        def leaves(value):
            if isinstance(value, dict):
                for child in value.values():
                    yield from leaves(child)
            elif isinstance(value, list):
                for child in value:
                    yield from leaves(child)
            elif type(value) in (int, float):
                yield value

        numbers = list(leaves(GOLDEN["config"]))
        self.assertTrue(numbers)
        for number in numbers:
            self.assertLessEqual(set(str(number).lower()), NUMBER_CHARS, number)

    def test_the_name_rescore_is_the_component_name_tier(self):
        self.assertTrue(any(row["tier"] is not None for row in GOLDEN["name_tiers"]))
        for row in GOLDEN["name_tiers"]:
            with self.subTest(row=row):
                self.assertEqual(
                    configs._route_name_tier(row["query"].strip().lower(),
                                             (row["entity_id"], row["name"]), exact=row["exact"]),
                    row["tier"])
        # The threshold is the component's; a ratio just under it is no match.
        self.assertEqual(configs.ROUTE_FUZZY_THRESHOLD, GOLDEN["constants"]["FUZZY_THRESHOLD"])
        texts = ("automation.front_gate", "Front gate")
        for query, tier in (("frt g", None), ("fr gt", None), ("frnt gx", 70)):
            with self.subTest(query=query):
                self.assertEqual(configs._route_name_tier(query, texts, exact=False), tier)

    def test_the_raw_page_scrub_state(self):
        other = [text for text in k.SEARCH_SCRUB_DEGRADED_WARNINGS if text != MATCH_TEXT]
        self.assertEqual(len(other), 1)
        flows = GOLDEN["helper_flows_degraded_warning"]
        mirror = k.SEARCH_PARTIAL_MIRROR_PREFIX + "synthetic reason"
        cases = [
            ({"warnings": [w4.SKIP]}, k.SCRUB_CLEAN),
            ({"warnings": [w4.SKIP, flows]}, k.SCRUB_CLEAN),
            ({"warnings": [w4.SKIP, mirror], "partial": True,
              "partial_reason": "synthetic reason"}, k.SCRUB_CLEAN),
            ({"warnings": [w4.SKIP, MATCH_TEXT]}, k.SCRUB_DEGRADED),
            ({"warnings": [w4.SKIP, other[0]]}, k.SCRUB_DEGRADED),
            ({"warnings": ["prefix: " + MATCH_TEXT + " (suffix)"]}, k.SCRUB_DEGRADED),
            (None, k.SCRUB_UNVERIFIED), ([], k.SCRUB_UNVERIFIED), ("x", k.SCRUB_UNVERIFIED),
            ({}, k.SCRUB_UNVERIFIED), ({"warnings": None}, k.SCRUB_UNVERIFIED),
            ({"warnings": []}, k.SCRUB_UNVERIFIED), ({"warnings": w4.SKIP}, k.SCRUB_UNVERIFIED),
            ({"warnings": [w4.SKIP, 1]}, k.SCRUB_UNVERIFIED),
            ({"warnings": [w4.SKIP, MATCH_TEXT, None]}, k.SCRUB_UNVERIFIED),
            ({"warnings": [w4.SKIP, "a synthetic unreviewed warning"]}, k.SCRUB_UNVERIFIED),
            ({"warnings": [w4.SKIP, mirror], "partial": False,
              "partial_reason": "synthetic reason"}, k.SCRUB_UNVERIFIED),
            ({"warnings": [w4.SKIP, mirror], "partial": True,
              "partial_reason": "another reason"}, k.SCRUB_UNVERIFIED),
            ({"warnings": [w4.SKIP, k.SEARCH_PARTIAL_MIRROR_PREFIX], "partial": True,
              "partial_reason": ""}, k.SCRUB_UNVERIFIED),
        ]
        for index, (payload, expected) in enumerate(cases):
            with self.subTest(case=index):
                self.assertEqual(k.page_scrub_state(payload), expected)

    def test_the_worst_scrub_and_the_body_code(self):
        clean, degraded, unverified = subcalls.SCRUB_STATES
        self.assertEqual(subcalls.worst_scrub(), clean)
        self.assertEqual(subcalls.worst_scrub(clean, clean), clean)
        self.assertEqual(subcalls.worst_scrub(clean, unverified), unverified)
        self.assertEqual(subcalls.worst_scrub(degraded, unverified, clean), degraded)
        for odd in (None, "", "Clean", 0, True):
            with self.subTest(odd=odd):
                self.assertEqual(subcalls.worst_scrub(clean, odd), unverified)
        self.assertEqual(subcalls.worst_scrub(None, degraded), degraded)
        cases = {(True, clean): "body_numeric_query", (True, degraded): "body_numeric_query",
                 (False, degraded): "body_scrub_degraded",
                 (False, unverified): "body_scrub_unverified",
                 (False, "x"): "body_scrub_unverified", (False, clean): None}
        for (unsafe, scrub), code in cases.items():
            self.assertEqual(configs._body_code(unsafe, scrub), code)
        self.assertLessEqual(set(BODY_CODES), reply.REASON_CODES["not_searched"])

    def test_a_bucket_discloses_its_kept_rows_withheld_names_only(self):
        flow = {"helper_type": "template", "name": "x", "score": 100}
        named = {"entity_id": "timer.x", **flow}
        self.assertEqual(configs._kept_withheld("helper", (named, flow)),
                         frozenset({"entry_id", "options_withheld"}))
        self.assertEqual(configs._kept_withheld("helper", (named,)), frozenset())
        self.assertEqual(configs._kept_withheld("helper", ()), frozenset())
        self.assertEqual(configs._kept_withheld("automation", (flow,)), frozenset())


class GuardTests(unittest.TestCase):
    """The scan and the probe check each page themselves: the gateway refuses
    an invalid page first, so only a direct reader reaches these guards."""

    @staticmethod
    def page(**changes):
        fields = {"rows": (), "partial": False, "has_more": False,
                  "config_total_matches": 0, "scrub": subcalls.SCRUB_CLEAN}
        fields.update(changes)
        return subcalls.PageResult(**fields)

    @staticmethod
    def reader(page, sent):
        async def read(arguments):
            sent.append(arguments)
            return page
        return read

    def scan(self, page):
        return asyncio.run(subcalls.scan("automation", self.reader(page, []), query="gate",
                                         exact_match=True, needed=10))

    def probe(self, page, sent):
        return asyncio.run(subcalls.probe_scrub(self.reader(page, sent), query="gate",
                                                exact_match=False))

    def test_a_scan_page_with_an_invalid_scrub_is_incomplete(self):
        for scrub in subcalls.SCRUB_STATES:
            with self.subTest(scrub=scrub):
                outcome = self.scan(self.page(scrub=scrub))
                self.assertEqual((outcome.status, outcome.scrub), (subcalls.COMPLETE, scrub))
        for scrub in (None, "", "Clean", "bogus", 0, True):
            with self.subTest(scrub=scrub):
                self.assertEqual(self.scan(self.page(scrub=scrub)).status, subcalls.INCOMPLETE)

    def test_an_invalid_probe_page_is_unverified(self):
        sent = []
        for scrub in subcalls.SCRUB_STATES:
            with self.subTest(scrub=scrub):
                self.assertEqual(self.probe(self.page(scrub=scrub), sent), scrub)
        self.assertEqual(sent, [subcalls.page_arguments("helper", "gate", False, 0,
                                                        subcalls.PROBE_SIZE)] * 3)
        invalid = (object(), {"scrub": subcalls.SCRUB_CLEAN},
                   self.page(rows=({}, {})),
                   self.page(rows=({"helper_type": "template"},)),
                   self.page(config_total_matches=-1),
                   self.page(has_more=True),
                   self.page(scrub="bogus"),
                   self.page(scrub=None))
        for index, page in enumerate(invalid):
            with self.subTest(index=index):
                self.assertEqual(self.probe(page, []), subcalls.SCRUB_UNVERIFIED)


class AcceptanceTests(w4.Readers):
    """Every golden result through native search, over the gateway's readers."""

    def page(self, search_type, rows, query, warnings, **changes):
        return w4.reply_of(w4.body(search_type, rows, query=query, warnings=warnings, **changes))

    def warnings(self, variant, search_type):
        # 9.2.0 warns a degraded scrub on a helper page only.
        if variant == "degraded" and search_type == "helper":
            return [w4.SKIP, MATCH_TEXT]
        return [w4.SKIP]

    def stage(self, query, search_type, rows, *, variant="clean", probe=None):
        self.served.clear()
        self.transport.sent.clear()
        self.served[(search_type, 0)] = self.page(search_type, rows, query,
                                                  self.warnings(variant, search_type))
        self.served[("helper", 0, subcalls.PROBE_SIZE)] = probe or self.page(
            "helper", [], query, self.warnings(variant, "helper"), limit=subcalls.PROBE_SIZE)

    def probes(self, query, exact):
        return self.sent().count(("ha_search", w4.probe_arguments(query, exact)))

    def body_codes(self, result):
        return {key: count for key, count in reasons_of(result).items() if key[2] in BODY_CODES}

    def emitted(self, result):
        blob = json.dumps(result, ensure_ascii=False)
        return any(text in blob for text in k.SEARCH_SCRUB_DEGRADED_WARNINGS)

    async def test_every_golden_result_through_native_search(self):
        seen = {"kept_body": 0, "dropped": 0, "renamed": 0, "residual": 0}
        for row in GOLDEN["scores"]:
            bucket, variant, query = row["bucket"], row["variant"], row["query"]
            exact, result, kind = row["exact"], row["result"], row["kind"]
            search_type = PAGE_TYPE[bucket]
            with self.subTest(bucket=bucket, variant=variant, query=query, exact=exact):
                rows = [] if result is None else [upstream_row(bucket, result)]
                self.stage(query, search_type, rows, variant=variant)
                native = await self.go(query=query, search_types=[search_type],
                                       exact_match=exact)
                unsafe = trigger(query, exact)
                code = ("body_numeric_query" if unsafe
                        else "body_scrub_degraded" if variant == "degraded" else None)
                if result is None:
                    expected = []
                elif code is None:
                    expected = [tuple(result)]
                else:
                    tier = TIERS[(bucket, query, exact)]
                    expected = [] if tier is None else [(tier, True, False)]
                got = flags_of(subject_records(native, bucket))
                self.assertEqual(got, expected)
                self.assertEqual(self.body_codes(native),
                                 {("not_searched", search_type, code): None} if code else {})
                self.assertEqual(self.probes(query, exact),
                                 2 if search_type == "automation" and not unsafe else 0)
                if kind in SECRET_KINDS and (kind, variant) != RESIDUAL:
                    self.assertFalse(any(flag[2] for flag in got), "a secret matched a body")
                self.assertFalse(self.emitted(native), "a scrub-degraded text was emitted")
                if (kind, variant) == RESIDUAL:
                    self.assertEqual(got, [(100, False, True)])
                    seen["residual"] += 1
                if expected and code is None and expected[0][2]:
                    seen["kept_body"] += 1
                if result is not None and not expected:
                    seen["dropped"] += 1
                if result is not None and expected and code is not None:
                    seen["renamed"] += 1
        self.assertTrue(all(seen.values()), seen)

    async def test_legitimate_body_matches_are_kept_and_numeric_ones_dropped(self):
        for query, exact, kept in (("unlock", True, True), ("disarmed", False, True),
                                   ("dissarmed", False, True), ("75", True, False),
                                   ("10:00", False, False)):
            with self.subTest(query=query, exact=exact):
                result = golden_score("automation", "clean", query, exact)
                self.assertIsNotNone(result)
                self.stage(query, "automation", [upstream_row("automation", result)])
                native = await self.go(query=query, search_types=["automation"],
                                       exact_match=exact)
                got = flags_of(subject_records(native, "automation"))
                self.assertEqual(got, [tuple(result)] if kept else [])

    async def closed(self, code, query="unlock"):
        """The automation body match is dropped under ``code``; a name match
        stays, rescored with no body match."""

        native = await self.go(query=query, search_types=["automation"], exact_match=True)
        self.assertEqual(self.body_codes(native), {("not_searched", "automation", code): None})
        expected = TIERS[("automation", query, True)]
        self.assertEqual(flags_of(subject_records(native, "automation")),
                         [] if expected is None else [(expected, True, False)])
        return native

    def automation_page(self, query):
        result = golden_score("automation", "clean", query, True)
        return self.page("automation", [upstream_row("automation", result)], query, [w4.SKIP])

    async def test_an_unreviewed_probe_warning_closes_body_matching(self):
        for query in ("unlock", "gate"):
            with self.subTest(query=query):
                probe = self.page("helper", [], query, [w4.SKIP, "a synthetic unreviewed warning"],
                                  limit=subcalls.PROBE_SIZE)
                self.stage(query, "automation", [], probe=probe)
                self.served[("automation", 0)] = self.automation_page(query)
                await self.closed("body_scrub_unverified", query)

    async def test_the_probe_brackets_the_scans(self):
        degraded = self.page("helper", [], "unlock", [w4.SKIP, MATCH_TEXT], limit=subcalls.PROBE_SIZE)
        clean = self.page("helper", [], "unlock", [w4.SKIP], limit=subcalls.PROBE_SIZE)
        for first, after in ((clean, degraded), (degraded, clean)):
            with self.subTest(first_degraded=first is degraded):
                self.stage("unlock", "automation", [], probe=first)
                self.served[("automation", 0)] = self.automation_page("unlock")
                original = self.readers

                async def automation(arguments, read=original["automation"], swap=after):
                    page = await read(arguments)
                    # The scrub changes between the two probes.
                    self.served[("helper", 0, subcalls.PROBE_SIZE)] = swap
                    return page

                self.readers = {**original, "automation": automation}
                try:
                    await self.closed("body_scrub_degraded")
                finally:
                    self.readers = original
                self.assertEqual(self.probes("unlock", True), 2)

    async def test_a_missing_or_failed_probe_is_unverified(self):
        original = self.readers

        async def raising(arguments, error):
            raise error

        cases = {
            "no reader": {key: value for key, value in original.items() if key != "helper"},
            "error reply": None, "invalid page": None,
            "exception": {**original, "helper": lambda arguments: raising(arguments, RuntimeError())},
            "timeout": {**original,
                        "helper": lambda arguments: raising(arguments, asyncio.TimeoutError())},
        }
        for name, readers in cases.items():
            with self.subTest(case=name):
                probe = None
                if name == "error reply":
                    probe = {"isError": True, "content": [{"type": "text", "text": MARKER}]}
                elif name == "invalid page":
                    probe = w4.reply_of({"success": False, "error": MARKER})
                self.stage("unlock", "automation", [], probe=probe)
                self.served[("automation", 0)] = self.automation_page("unlock")
                self.readers = readers or original
                try:
                    await self.closed("body_scrub_unverified")
                finally:
                    self.readers = original

    async def test_a_cancelled_probe_propagates(self):
        async def cancelled(arguments):
            raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            await subcalls.probe_scrub(cancelled, query="unlock", exact_match=True)

    async def test_an_unscanned_type_gets_no_body_code(self):
        original = self.readers
        self.readers = {key: value for key, value in original.items() if key != "automation"}
        try:
            for query in ("2468", "unlock"):
                with self.subTest(query=query):
                    self.stage(query, "automation", [])
                    native = await self.go(query=query, search_types=["automation"],
                                           exact_match=True)
                    self.assertEqual(self.body_codes(native), {})
                    self.assertIn(("source_unavailable", "automation", "subcall_not_admitted"),
                                  reasons_of(native))
        finally:
            self.readers = original

    async def test_a_dashboard_bucket_gets_no_body_code(self):
        # Dashboards are outside the component's scrub surfaces and the
        # trigger (a disclosed residual): their route rows stay as served.
        for query, variant, code in (("2468", "clean", "body_numeric_query"),
                                     ("unlock", "degraded", "body_scrub_degraded")):
            with self.subTest(query=query):
                self.stage(query, "automation", [], variant=variant)
                self.served[("dashboard", 0)] = self.page(
                    "dashboard", [w4.board(0, score=100)], query, [w4.SKIP])
                native = await self.go(query=query, search_types=["automation", "dashboard"],
                                       exact_match=True)
                self.assertEqual(self.body_codes(native),
                                 {("not_searched", "automation", code): None})
                self.assertEqual(len(native["dashboards"]), 1)

    async def test_the_helper_page_scrub_closes_its_own_bucket(self):
        # A helper search reads no probe; its own pages carry the flag.
        result = golden_score("helper", "degraded", "unlock", True)
        self.stage("unlock", "helper", [upstream_row("helper", result)], variant="degraded")
        native = await self.go(query="unlock", search_types=["helper"], exact_match=True)
        self.assertEqual(self.body_codes(native),
                         {("not_searched", "helper", "body_scrub_degraded"): None})
        self.assertEqual(subject_records(native, "helper"), [])
        self.assertEqual(self.probes("unlock", True), 0)
        # An unreviewed warning on the helper page is unverified.
        self.served[("helper", 0)] = self.page(
            "helper", [upstream_row("helper", result)], "unlock",
            [w4.SKIP, "a synthetic unreviewed warning"])
        self.transport.sent.clear()
        native = await self.go(query="unlock", search_types=["helper"], exact_match=True)
        self.assertEqual(self.body_codes(native),
                         {("not_searched", "helper", "body_scrub_unverified"): None})

    async def test_a_dropped_flow_row_leaves_no_withheld_names(self):
        counts = {}
        for query in ("gate", "quasarvelvet"):
            result = golden_score("flow", "degraded", query, True)
            self.stage(query, "helper", [upstream_row("flow", result)], variant="degraded")
            native = await self.go(query=query, search_types=["helper"], exact_match=True)
            counts[query] = (native["fields_withheld"], len(subject_records(native, "flow")))
        self.assertEqual((counts["gate"][1], counts["quasarvelvet"][1]), (1, 0))
        self.assertEqual(counts["gate"][0] - counts["quasarvelvet"][0],
                         len(configs._FLOW_HELPER_WITHHELD))

    async def test_a_dropped_flow_row_adds_no_withheld_names_to_a_kept_row(self):
        # A kept timer row and a flow row whose name does not match: the
        # page's withheld names (the flow row's) must not reach the bucket.
        kept = upstream_row("helper", (100, True, True))
        dropped = {**upstream_row("flow", (90, False, True)), "name": "Porch flow"}
        seen = []
        for rows in ([kept], [kept, dropped]):
            self.stage("gate", "helper", rows, variant="degraded")
            native = await self.go(query="gate", search_types=["helper"], exact_match=True)
            self.assertEqual(self.body_codes(native),
                             {("not_searched", "helper", "body_scrub_degraded"): None})
            seen.append((native["fields_withheld"], len(native["helpers"]),
                         len(subject_records(native, "helper"))))
        self.assertEqual(seen[0], seen[1])
        self.assertEqual(seen[0][1:], (1, 1))

    async def test_the_gateway_reads_the_scrub_from_the_raw_page(self):
        for warnings, expected in (([w4.SKIP], k.SCRUB_CLEAN),
                                   ([w4.SKIP, MATCH_TEXT], k.SCRUB_DEGRADED),
                                   ([w4.SKIP, "a synthetic unreviewed warning"], k.SCRUB_UNVERIFIED),
                                   (None, k.SCRUB_UNVERIFIED)):
            with self.subTest(expected=expected):
                self.served.clear()
                self.audit.reset_mock()
                value = w4.body("helper", [w4.flow(0)], warnings=warnings)
                if warnings is None:
                    del value["warnings"]
                self.served[("helper", 0)] = w4.reply_of(value)
                page = await self.readers["helper"](w4.arguments())
                self.assertEqual(page.scrub, expected)
                self.assertEqual(page.rows, (w4.flow(0),))
                records = self.audits()
                self.assertTrue(records)
                for record in records:
                    self.assertEqual(set(record), w4.AUDIT_KEYS)
                self.assertFalse(any(text in json.dumps(records)
                                     for text in k.SEARCH_SCRUB_DEGRADED_WARNINGS),
                                 "a scrub-degraded text reached the audit")

    async def test_the_gateway_refuses_a_preset_scrub_without_dispatch(self):
        with self.gateway._lock:
            routes = {**self.gateway._exposed, **self.gateway._internal}
        (name, route), = [(name, route) for name, route in routes.items()
                          if route.entry.upstream_name == "ha_search"]
        self.served[("helper", 0)] = w4.reply_of(w4.body("helper", [w4.flow(0)]))
        for scrub in (k.SCRUB_CLEAN, k.SCRUB_DEGRADED, k.SCRUB_UNVERIFIED, "x"):
            with self.subTest(scrub=scrub):
                capture = gw._SearchPageCapture("helpers")
                capture.scrub = scrub
                result = json.loads(await self.gateway.execute(
                    exposed_name=name, arguments=w4.arguments(),
                    reviewed_schema=gw._public_read_schema(route.entry,
                                                           route.observed_tool["inputSchema"]),
                    policy_entry=route.entry, admission_generation=route.generation,
                    contract_fingerprint=route.contract_fingerprint, search_page=capture))
                self.assertIs(result["success"], False)
                self.assertEqual(result["details"]["failure_category"], "prohibited_delegation")
        self.assertEqual(self.transport.calls, 0)


class UnifiedAcceptanceTests(AcceptanceTests):
    """The same under the ``unified`` ha_search descriptor."""

    UNIFIED = True


if __name__ == "__main__":
    unittest.main()
