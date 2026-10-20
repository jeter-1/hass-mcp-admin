"""HAMCP-191: the shared strict sanitizer with identity-only markers.

Owner decisions D1-D11 (register row HAMCP-191) and the design
``docs/HAMCP_191_SHARED_SANITIZER_DESIGN.md`` (PR #271):

- T1, the unit matrix: every rule derived from ``sanitization.py`` (the
  segment and serialized rule tuples, the key-scoped categories that
  ``key_category`` returns, the known-secret rule and the marker look-alike)
  x every wrapper (plain, whole-value look-alike, the P2 anchor split, a
  suffix, a percent-encoded and a folded look-alike) x every generic output
  position. The needle never survives, the rule's category is recorded even
  when the look-alike decides the output (all rules run before the
  collision), and the case count is asserted.
- T3, coverage: an AST scan of the package for every sanitizer entry point
  equals the committed ``SANITIZER_SITES`` table, each site maps to a ranked
  family of design section 2, and ``identity_markers_only=False`` raises
  and appears nowhere.
- T6: the per-field failure branch writes a key only as scanned text.
- T7: the identity record keeps Engineering's own output by object
  identity only.
- D3 health counters, Finding 11 bounded redaction evidence, the D11
  stored-plan exception with its code-derived call-site allow-list, X1, X2,
  the linear look-alike detector and its ASCII fold fast path.

The family injectors (T2) and the gates (T5) live in
``test_hamcp191_families.py``; the compatibility corpus (T4) in
``test_hamcp191_compatibility.py``.

Every synthetic secret is built at runtime by ``hamcp162_synthetic``; no
committed file holds a literal secret-shaped value. No live Home Assistant.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import gc
import json
from pathlib import Path
import random
import re
import sys
import time
from typing import Any, Callable
import unicodedata
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

import hamcp162_synthetic as syn  # noqa: E402
from ha_mcp_engineering import health as H  # noqa: E402
from ha_mcp_engineering import sanitization as S  # noqa: E402
from ha_mcp_engineering.incident import normalization as N  # noqa: E402
from ha_mcp_engineering.logging_config import redact_data  # noqa: E402
from ha_mcp_engineering.models.responses import dump_json, dump_json_with_truncation  # noqa: E402
from ha_mcp_engineering.request_context import RequestTelemetry  # noqa: E402

PACKAGE = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering"
COLLISION = S.MARKER_COLLISION
COLLISION_CATEGORY = S.MARKER_COLLISION_CATEGORY


def marker(category: str) -> str:
    return "[REDACTED:" + category + "]"


# ---------------------------------------------------------------------------
# Rules, derived from code.


def _module_name(value: Any) -> str:
    names = [name for name, item in vars(S).items() if item is value]
    if len(names) != 1:
        raise AssertionError(f"rule object has {len(names)} module names")
    return names[0]


def key_categories_from_code() -> tuple[str, ...]:
    """Every category ``key_category`` can return, read from its source."""

    tree = ast.parse((PACKAGE / "sanitization.py").read_text(encoding="utf-8"))
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "key_category"
    )
    return tuple(sorted({
        node.value.value
        for node in ast.walk(function)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }))


def all_key_names() -> tuple[str, ...]:
    names: set[str] = set()
    for name, value in vars(S).items():
        if name.endswith("_KEYS") and isinstance(value, frozenset):
            names.update(value)
    return tuple(sorted(names))


def rule_ids_from_code() -> tuple[str, ...]:
    segment = tuple(
        f"segment:{_module_name(pattern)}:{category}"
        for pattern, category, _form in S._SEGMENT_RULES
    )
    serialized = tuple(f"serialized:{category}" for _pattern, category in S._SERIALIZED_PATTERNS)
    keyed = tuple(f"key:{category}" for category in key_categories_from_code())
    return (*segment, *serialized, *keyed, "known_secret", "marker_lookalike")


@dataclass(frozen=True)
class Case:
    """One rule's runtime-built case: ``prefix + payload`` fires the rule."""

    prefix: str
    payload: str
    needle: str
    category: str
    known_secrets: tuple[str, ...] = ()
    kind: str = "text"  # text, key or lookalike
    key: str = ""

    @property
    def text(self) -> str:
        return self.prefix + self.payload


def _digits(tag: str) -> str:
    digits = "".join(str((ord(char) * 7 + index) % 10) for index, char in enumerate(tag * 3))
    return digits[:4] + "-" + digits[4:7] + "-" + digits[7:11]


SEGMENT_BUILDERS: dict[str, Callable[[str], tuple[str, str, str]]] = {
    # name -> tag -> (prefix, payload, needle)
    "_AUTH_HEADER": lambda n: (syn.auth_header(""), n, n),
    "_BEARER": lambda n: (syn.bearer(""), n, n),
    "_COOKIE_HEADER": lambda n: (syn.cookie_header(""), syn.assign("sid", n), n),
    "_JWT": lambda n: ("", syn.jwt(n), syn.needle(n)),
    "_URL_USERINFO": lambda n: (
        "https" + syn.SCHEME_SEPARATOR + "synthuser:", n + "@synthetic.invalid/", n
    ),
    "_URL_ENCODED_USERINFO": lambda n: (
        "https" + syn.SCHEME_SEPARATOR + "synthuser%3A", n + "@synthetic.invalid/", n
    ),
    "_URL_CREDENTIAL_PARAMETER": lambda n: (syn.url_parameter("token", ""), n, n),
    "_WEBHOOK_PATH": lambda n: (syn.webhook_path(""), n, n),
    "_WEBHOOK_PROSE": lambda n: (syn.webhook_prose(""), n, n),
    "_LOGIN_FLOW_PATH": lambda n: (syn.login_flow_path(""), n, n),
    "_MATTER_PAYLOAD": lambda n: (syn.matter_payload(""), n.upper(), n),
    "_MATTER_CODE_LABEL": lambda n: (syn.matter_code(""), _digits(n), _digits(n)),
}


def serialized_key(pattern: re.Pattern[str]) -> str:
    """The first module key name the serialized pattern matches."""

    return next(key for key in all_key_names() if pattern.fullmatch(syn.serialized(key, "v")))


def keyed_name(category: str) -> str:
    return next(key for key in all_key_names() if S.key_category(key, ()) == category)


def build_case(rule_id: str) -> Case:
    tag = syn.needle("hamcp191 " + rule_id + " padding")
    if rule_id.startswith("segment:"):
        _, name, category = rule_id.split(":")
        prefix, payload, needle = SEGMENT_BUILDERS[name](tag)
        return Case(prefix, payload, needle, category)
    if rule_id.startswith("serialized:"):
        category = rule_id.split(":", 1)[1]
        pattern = next(p for p, c in S._SERIALIZED_PATTERNS if c == category)
        whole = syn.serialized(serialized_key(pattern), tag)
        split = whole.rindex(tag)
        return Case(whole[:split], whole[split:], tag, category)
    if rule_id.startswith("key:"):
        category = rule_id.split(":", 1)[1]
        return Case("value ", tag, tag, category, kind="key", key=keyed_name(category))
    if rule_id == "known_secret":
        return Case("value ", tag, tag, "token", known_secrets=(tag,))
    if rule_id == "marker_lookalike":
        return Case("", marker(tag), tag, COLLISION_CATEGORY, kind="lookalike")
    raise AssertionError(rule_id)


# ---------------------------------------------------------------------------
# Wrappers and look-alike spellings.

FORGED = marker("token")


def folded_spellings(body: str) -> list[tuple[str, str]]:
    """Marker look-alikes the fold must catch (the HAMCP-162 H3 spellings)."""

    fullwidth = "".join(
        chr(ord(char) + 0xFEE0) if "!" <= char <= "~" else char
        for char in "[REDACTED:" + body + "]"
    )
    return [
        ("fullwidth", fullwidth),
        ("cyrillic", "[RЕDАСTЕD:" + body + "]"),
        ("greek_cherokee", "[ᎡΕᎠΑϹΤΕD:" + body + "]"),
        ("zero_width", "[RE​DACTED:" + body + "]"),
        ("combining", "[ŔEDACTED:" + body + "]"),
        ("bracket_lookalike", "⟦REDACTED:" + body + "⟧"),
        ("cjk_bracket", "【REDACTED:" + body + "】"),
        ("prose_truncated", body + " (truncated)"),
        ("angle_truncated", body + syn.EQUALS[:0] + "...<truncated>"),
    ]


def wrap(wrapper: str, case: Case) -> list[tuple[str, str]]:
    """``(variant, text)`` for one wrapper applied to one case."""

    if wrapper == "plain":
        return [("", case.text)]
    if wrapper == "whole":
        return [("", "[REDACTED:" + case.text + "]")]
    if wrapper == "p2":
        return [("", case.prefix + FORGED + " " + case.payload)]
    if wrapper == "suffix":
        return [("", case.text + " " + FORGED)]
    if wrapper == "encoded":
        return [(f"x{times}", case.text + " " + syn.enc(FORGED, times)) for times in (1, 2, 3)]
    if wrapper == "folded":
        return [(name, case.text + " " + spelling) for name, spelling in folded_spellings("token")]
    raise AssertionError(wrapper)


WRAPPERS = ("plain", "whole", "p2", "suffix", "encoded", "folded")


# Raw keys whose ASCII alphanumerics are exactly ``token`` (so the field is
# key-scoped) while the key text folds to a redaction marker (S9).
def sensitive_lookalike_keys() -> list[tuple[str, str]]:
    small_caps = "ʀᴇԁᴀᴄᴛᴇԁ"
    fullwidth = "".join(chr(ord(char) + 0xFEE0) for char in "REDACTED")
    return [
        ("small_caps", "【" + small_caps + ":token】"),
        ("fullwidth", "［" + fullwidth + "：token］"),
        ("prose", "(" + small_caps + ") token"),
    ]


# ---------------------------------------------------------------------------
# Positions.


class _Opaque:
    def __init__(self, text: str) -> None:
        self.text = text

    def __repr__(self) -> str:
        return self.text


class _ExplodingMapping(dict):
    """A mapping whose items cannot be read: the per-field failure branch."""

    def items(self):  # noqa: D401
        raise RuntimeError("synthetic per-field failure")


POSITIONS = ("value", "list_item", "key", "sensitive_key_text", "repr", "failure_key", "top_level")
TEXT_POSITIONS = ("value", "list_item", "key", "repr", "failure_key", "top_level")
KEY_RULE_POSITIONS = ("value", "list_item", "top_level")


def place(position: str, text: str) -> Any:
    if position == "value":
        return {"outer": {"note": text}}
    if position == "list_item":
        return {"items": ["ordinary", text]}
    if position in ("key", "sensitive_key_text"):
        return {"outer": {text: "ordinary"}}
    if position == "repr":
        return {"note": _Opaque(text)}
    if position == "failure_key":
        return {text: _ExplodingMapping(note="ordinary")}
    if position == "top_level":
        return text
    raise AssertionError(position)


def extract(position: str, value: Any) -> str:
    if position == "value":
        return value["outer"]["note"]
    if position == "list_item":
        return value["items"][1]
    if position in ("key", "sensitive_key_text"):
        (key,) = value["outer"].keys()
        return key
    if position == "repr":
        return value["note"]
    if position == "failure_key":
        (key,) = value.keys()
        return key
    if position == "top_level":
        return value
    raise AssertionError(position)


def place_keyed(position: str, key: str, text: str) -> Any:
    field = {key: text}
    if position == "value":
        return {"outer": field}
    if position == "list_item":
        return {"items": [field]}
    if position == "top_level":
        return field
    raise AssertionError(position)


def extract_keyed(position: str, key: str, value: Any) -> Any:
    if position == "value":
        return value["outer"][key]
    if position == "list_item":
        return value["items"][0][key]
    if position == "top_level":
        return value[key]
    raise AssertionError(position)


def applicable_positions(case: Case) -> tuple[str, ...]:
    if case.kind == "key":
        return KEY_RULE_POSITIONS
    if case.kind == "lookalike":
        return POSITIONS
    return TEXT_POSITIONS


def matrix_cells() -> list[tuple[str, str, str]]:
    return [
        (rule_id, wrapper, position)
        for rule_id in rule_ids_from_code()
        for wrapper in WRAPPERS
        for position in applicable_positions(build_case(rule_id))
        if position != "sensitive_key_text" or wrapper == "folded"
    ]


def rendered(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# T1: the unit matrix.


class RuleDerivationTests(unittest.TestCase):
    def test_rules_are_derived_from_code_and_each_has_a_builder(self):
        rules = rule_ids_from_code()
        self.assertEqual(len(rules), len(set(rules)))
        self.assertEqual(len(S._SEGMENT_RULES), 12)
        self.assertEqual(len(S._SERIALIZED_PATTERNS), 8)
        self.assertEqual(len(key_categories_from_code()), 8)
        self.assertEqual(len(rules), 12 + 8 + 8 + 2)
        self.assertEqual(
            {rule.split(":")[1] for rule in rules if rule.startswith("segment:")},
            set(SEGMENT_BUILDERS),
        )
        for rule_id in rules:
            with self.subTest(rule=rule_id):
                case = build_case(rule_id)
                self.assertTrue(case.needle)
                self.assertIn(case.needle.lower(), case.text.lower())

    def test_each_case_fires_its_own_rule(self):
        for pattern, category, _form in S._SEGMENT_RULES:
            case = build_case(f"segment:{_module_name(pattern)}:{category}")
            with self.subTest(rule=_module_name(pattern)):
                self.assertIsNotNone(pattern.search(case.text))
        for pattern, category in S._SERIALIZED_PATTERNS:
            with self.subTest(rule=category):
                self.assertIsNotNone(pattern.search(build_case(f"serialized:{category}").text))
        for category in key_categories_from_code():
            with self.subTest(rule=category):
                self.assertEqual(S.key_category(S.normalize_key(build_case(f"key:{category}").key), ()), category)

    def test_matrix_cell_count_is_rules_by_wrappers_by_positions(self):
        cells = matrix_cells()
        self.assertEqual(len(cells), len(set(cells)))
        text_rules = 12 + 8 + 1
        expected = (
            text_rules * len(WRAPPERS) * len(TEXT_POSITIONS)
            + len(WRAPPERS) * len(TEXT_POSITIONS) + 1  # look-alike, plus S9
            + 8 * len(WRAPPERS) * len(KEY_RULE_POSITIONS)
        )
        self.assertEqual(len(cells), expected)
        self.assertEqual(len(cells), 937)
        self.assertEqual({position for _r, _w, position in cells}, set(POSITIONS))
        self.assertEqual({wrapper for _r, wrapper, _p in cells}, set(WRAPPERS))
        self.assertEqual({rule for rule, _w, _p in cells}, set(rule_ids_from_code()))

    def test_no_ascii_payload_can_sit_in_a_key_scoped_key(self):
        # Why S9 carries only folded look-alikes: a key is key-scoped only
        # when its normalized text is a key name, and normalization keeps
        # every ASCII letter and digit, so no needle can be in its text.
        for rule_id in rule_ids_from_code():
            case = build_case(rule_id)
            normalized = S.normalize_key(case.text)
            with self.subTest(rule=rule_id):
                self.assertIsNone(S.key_category(normalized, ()))
                self.assertIn(re.sub("[^a-z0-9]", "", case.needle.lower()), normalized.replace("_", ""))
        for name, key in sensitive_lookalike_keys():
            with self.subTest(key=name):
                self.assertEqual(S.key_category(S.normalize_key(key), ()), "token")
                self.assertTrue(S.marker_like_text(key))


class UnitMatrixTests(unittest.TestCase):
    def check_cell(self, rule_id: str, wrapper: str, position: str) -> int:
        case = build_case(rule_id)
        checked = 0
        if position == "sensitive_key_text":
            for name, key in sensitive_lookalike_keys():
                with self.subTest(rule=rule_id, wrapper=wrapper, position=position, variant=name):
                    result = S.sanitize_untrusted_data(place(position, key))
                    self.assertEqual(extract(position, result.value), COLLISION)
                    self.assertEqual(result.value["outer"][COLLISION], marker("token"))
                    self.assertIn(COLLISION_CATEGORY, result.redaction_categories)
                    self.assertIn("token", result.redaction_categories)
                    self.assertNotIn(key, rendered(result.value))
                    checked += 1
            return checked
        for variant, text in wrap(wrapper, case):
            with self.subTest(rule=rule_id, wrapper=wrapper, position=position, variant=variant):
                if case.kind == "key":
                    result = S.sanitize_untrusted_data(place_keyed(position, case.key, text))
                    self.assertEqual(extract_keyed(position, case.key, result.value), marker(case.category))
                    self.assertIn(case.category, result.redaction_categories)
                else:
                    result = S.sanitize_untrusted_data(
                        place(position, text), known_secrets=case.known_secrets
                    )
                    piece = extract(position, result.value)
                    if wrapper == "plain" and case.kind == "text":
                        self.assertIn(marker(case.category), piece)
                        self.assertNotIn(COLLISION_CATEGORY, result.redaction_categories)
                        self.assertEqual(result.marker_collision_count, 0)
                    else:
                        self.assertEqual(piece, COLLISION)
                        self.assertIn(COLLISION_CATEGORY, result.redaction_categories)
                        self.assertGreaterEqual(result.marker_collision_count, 1)
                    if wrapper != "p2":
                        # Every rule ran before the collision decision (M3).
                        self.assertIn(case.category, result.redaction_categories)
                    if position == "failure_key":
                        self.assertEqual(result.value[piece], S.SANITIZATION_FAILURE_MARKER)
                        self.assertTrue(result.failed_closed)
                self.assertTrue(result.redaction_applied)
                self.assertNotIn(case.needle.lower(), rendered(result.value).lower())
                checked += 1
        return checked

    def test_every_matrix_cell(self):
        checked = 0
        for rule_id, wrapper, position in matrix_cells():
            checked += self.check_cell(rule_id, wrapper, position)
        self.assertGreater(checked, len(matrix_cells()))

    def test_control_the_needle_alone_is_kept_in_every_position(self):
        # Proves the needle assertion can fail: ordinary text survives as is.
        tag = syn.needle("hamcp191 control")
        for position in POSITIONS:
            text = "ordinary " + tag
            with self.subTest(position=position):
                result = S.sanitize_untrusted_data(place(position, text))
                self.assertEqual(extract(position, result.value), text)
                self.assertIn(tag, rendered(result.value))
                self.assertEqual(result.marker_collision_count, 0)

    def test_p1_wrapped_known_secret_and_p2_anchor_split(self):
        secret = syn.needle("hamcp191 p1")
        # P1: a known secret inside a forged marker.
        result = S.sanitize_untrusted_data({"note": marker(secret)}, known_secrets=(secret,))
        self.assertEqual(result.value, {"note": COLLISION})
        self.assertEqual(set(result.redaction_categories), {"token", COLLISION_CATEGORY})
        # P2: the anchor separated from its value by a forged marker.
        for build in (syn.bearer, syn.auth_header, syn.webhook_path, syn.login_flow_path):
            text = build("") + FORGED + " " + secret
            with self.subTest(text=build.__name__):
                result = S.sanitize_untrusted_data({"note": text})
                self.assertEqual(result.value, {"note": COLLISION})
                self.assertNotIn(secret, rendered(result.value))

    def test_emitted_markers_deduplicate_by_type(self):
        secret = syn.needle("hamcp191 dedup")
        result = S.sanitize_untrusted_data(secret + " " + secret, known_secrets=(secret,))
        self.assertEqual(result.value, marker("token"))
        separated = S.sanitize_untrusted_data(secret + " x " + secret, known_secrets=(secret,))
        self.assertEqual(separated.value, marker("token") + " x " + marker("token"))
        mixed = S.sanitize_untrusted_data(
            syn.bearer(secret) + " " + syn.assign("password", secret)
        )
        self.assertEqual(mixed.marker_collision_count, 0)
        self.assertIn(marker("token"), mixed.value)
        self.assertIn(marker("password"), mixed.value)
        # Markers of different types separated only by whitespace both stay.
        payload = syn.matter_payload("") + syn.needle("hamcp191 dedup matter").upper()
        for text, expected in (
            (secret + " " + payload, marker("token") + " " + marker("matter_setup_payload")),
            (payload + " \n " + secret, marker("matter_setup_payload") + " \n " + marker("token")),
        ):
            with self.subTest(expected=expected):
                adjacent = S.sanitize_untrusted_data(text, known_secrets=(secret,))
                self.assertEqual(adjacent.value, expected)
                self.assertEqual(adjacent.marker_collision_count, 0)

    def test_forged_markers_in_every_position_and_encoding(self):
        body = "token"
        spellings = [("exact", marker(body)), ("truncated", "[TRUNCATED:" + body + "]")]
        spellings += [(f"encoded_{n}", syn.enc(marker(body), n)) for n in (1, 2, 3)]
        spellings += folded_spellings(body)
        for name, spelling in spellings:
            for position in TEXT_POSITIONS:
                with self.subTest(spelling=name, position=position):
                    result = S.sanitize_untrusted_data(place(position, spelling))
                    self.assertEqual(extract(position, result.value), COLLISION)
                    self.assertEqual(result.marker_collision_count, 1)
            with self.subTest(spelling=name, position="nested"):
                nested = {"a": [{"b": ({"c": spelling},)}]}
                result = S.sanitize_untrusted_data(nested)
                self.assertEqual(result.value["a"][0]["b"][0]["c"], COLLISION)

    def test_five_decode_rounds_fail_closed(self):
        text = syn.enc("ordinary", 0)
        deep = syn.enc("%" + "41" * 3, S.MAX_MARKER_DECODE_ROUNDS + 1)
        self.assertFalse(S.marker_like_text(text))
        self.assertTrue(S.marker_like_text(deep))
        # Four rounds of ordinary encoding decode fully and are not a collision.
        self.assertEqual(S.MAX_MARKER_DECODE_ROUNDS, 4)
        four = syn.enc("ordinary words/and?more", 4)
        self.assertFalse(S.marker_like_text(four))
        kept = S.sanitize_untrusted_data({"v": four})
        self.assertIs(kept.value["v"], four)
        self.assertEqual(kept.marker_collision_count, 0)


# ---------------------------------------------------------------------------
# T3: coverage of every sanitizer entry point.

ENTRY_POINTS = frozenset(
    {
        "sanitize_untrusted_data", "rescan_stored_plan", "redact_data", "redact_untrusted_text",
        "sanitize_log_lines", "screen_plan_content",
    }
)

# The eight ranked families of design section 2.
FAMILIES = {
    "logs": "error and Core logs",
    "delegated": "delegated ha_* reads and the held-read canary",
    "logbook": "logbook",
    "analysis": "analysis tools: incident, handoff, reliability, impact, integrity, traces, dependency",
    "dashboard": "dashboard reads",
    "governance": "governance projections, plan persistence and approval",
    "identifier": "identifier projections",
    "audit": "audit and logging sinks",
}

# (module, enclosing function, entry point) -> (call count, family).
SANITIZER_SITES = {
    ("audit.py", "AuditLogger.sanitize", "redact_data"): (1, "audit"),
    ("audit_baseline/capture_provider.py", "Projector.identifier", "sanitize_untrusted_data"): (1, "identifier"),
    ("clients/logbook.py", "project", "sanitize_untrusted_data"): (1, "logbook"),
    ("dashboard_analysis/models.py", "_scalar", "sanitize_untrusted_data"): (1, "identifier"),
    ("dashboard_analysis/models.py", "validate_projection", "sanitize_untrusted_data"): (1, "identifier"),
    ("dashboard_analysis/provider.py", "safe_entity", "sanitize_untrusted_data"): (1, "identifier"),
    ("dependency/extraction.py", "_bounded", "redact_data"): (1, "analysis"),
    ("dependency/models.py", "_bounded_evidence_text", "sanitize_untrusted_data"): (1, "analysis"),
    ("dependency/models.py", "_bounded_evidence_values", "sanitize_untrusted_data"): (1, "analysis"),
    ("dependency/provider.py", "DirectHaDependencyProvider.scan", "redact_data"): (2, "analysis"),
    ("dependency/provider.py", "_bounded_provider_identity", "redact_data"): (1, "analysis"),
    ("dependency/provider.py", "_display_safe_effect_tokens", "redact_data"): (1, "analysis"),
    ("dependency/script_calls.py", "extract_script_calls", "sanitize_untrusted_data"): (1, "analysis"),
    ("dependency/script_calls.py", "extract_script_calls.add", "sanitize_untrusted_data"): (1, "analysis"),
    ("dependency/script_sources.py", "collect_script_diagnostics", "sanitize_untrusted_data"): (1, "analysis"),
    ("f3_dashboard/approval_projection.py", "validate_dashboard_approval_projection", "sanitize_untrusted_data"): (1, "governance"),
    ("governance/config_validation.py", "normalize_configuration_validation", "sanitize_untrusted_data"): (2, "governance"),
    ("governance/operational_lifecycle.py", "OperationalLifecycleGateway.configuration_validation", "sanitize_untrusted_data"): (1, "governance"),
    ("governance/resources.py", "persistence_safety_errors", "rescan_stored_plan"): (1, "governance"),
    ("governance/resources.py", "persistence_safety_errors", "sanitize_untrusted_data"): (1, "governance"),
    ("governance/semantic_projection.py", "_review_snapshot", "sanitize_untrusted_data"): (1, "governance"),
    ("governance/service.py", "ChangeGovernanceService._public", "rescan_stored_plan"): (1, "governance"),
    ("governance/service.py", "ChangeGovernanceService._public_plan_observability_projection", "rescan_stored_plan"): (1, "governance"),
    ("governance/service.py", "ChangeGovernanceService._review_summary", "rescan_stored_plan"): (3, "governance"),
    ("governance/service.py", "ChangeGovernanceService.approve", "sanitize_untrusted_data"): (1, "governance"),
    ("governance/service.py", "ChangeGovernanceService.create_dashboard_update_plan", "sanitize_untrusted_data"): (1, "governance"),
    ("governance/service.py", "_sanitize_configuration_caller_context", "sanitize_untrusted_data"): (1, "governance"),
    ("governance/storage.py", "ChangePlanRepository._screen_new_content", "screen_plan_content"): (1, "governance"),
    ("handoff/provider.py", "EngineeringHandoffProvider._automation_configs", "sanitize_untrusted_data"): (1, "analysis"),
    ("handoff/provider.py", "EngineeringHandoffProvider._automation_configs.one", "sanitize_untrusted_data"): (1, "analysis"),
    ("handoff/provider.py", "EngineeringHandoffProvider._states.one", "sanitize_untrusted_data"): (1, "analysis"),
    ("handoff/service.py", "HandoffGenerationService.generate", "sanitize_untrusted_data"): (1, "analysis"),
    ("impact/provider.py", "DirectHaImpactProvider._entity_registry", "sanitize_untrusted_data"): (1, "analysis"),
    ("impact/provider.py", "DirectHaImpactProvider._exact_state", "sanitize_untrusted_data"): (1, "analysis"),
    ("impact/provider.py", "DirectHaImpactProvider._safe_dependency", "sanitize_untrusted_data"): (1, "analysis"),
    ("impact/provider.py", "DirectHaImpactProvider._system_log", "sanitize_untrusted_data"): (1, "logs"),
    ("impact/provider.py", "DirectHaImpactProvider._target_summary", "sanitize_untrusted_data"): (1, "analysis"),
    ("impact/provider.py", "DirectHaImpactProvider.collect", "sanitize_untrusted_data"): (1, "analysis"),
    ("incident/provider.py", "DirectHaIncidentProvider._entity_activity.call", "sanitize_untrusted_data"): (1, "analysis"),
    ("incident/provider.py", "DirectHaIncidentProvider._registry", "sanitize_untrusted_data"): (1, "analysis"),
    ("incident/provider.py", "DirectHaIncidentProvider._states", "sanitize_untrusted_data"): (1, "analysis"),
    ("incident/provider.py", "DirectHaIncidentProvider._system_log", "sanitize_untrusted_data"): (1, "logs"),
    ("integration_inspection/projection.py", "Projector._project_steps.scalar", "sanitize_untrusted_data"): (1, "identifier"),
    ("integration_inspection/projection.py", "Projector.identifier", "sanitize_untrusted_data"): (1, "identifier"),
    ("integrity/provider.py", "DirectHaIntegrityProvider._current_states", "sanitize_untrusted_data"): (1, "analysis"),
    ("integrity/provider.py", "DirectHaIntegrityProvider._entity_registry", "sanitize_untrusted_data"): (1, "analysis"),
    ("integrity/provider.py", "DirectHaIntegrityProvider.collect", "sanitize_untrusted_data"): (2, "analysis"),
    ("logging_config.py", "JsonFormatter.format", "sanitize_untrusted_data"): (1, "audit"),
    ("logging_config.py", "log_event", "sanitize_untrusted_data"): (1, "audit"),
    ("logging_config.py", "redact_data", "sanitize_untrusted_data"): (1, "audit"),
    ("logging_config.py", "redact_untrusted_text", "sanitize_untrusted_data"): (1, "audit"),
    ("providers/core_logs.py", "CoreLogReader.evidence", "sanitize_log_lines"): (1, "logs"),
    ("providers/operational_lifecycle.py", "_safe_text", "sanitize_untrusted_data"): (1, "governance"),
    ("providers/upstream_dashboard.py", "UpstreamDashboardProvider._dashboard_metadata", "sanitize_untrusted_data"): (1, "dashboard"),
    ("providers/upstream_dashboard.py", "UpstreamDashboardProvider._safe_string", "sanitize_untrusted_data"): (1, "dashboard"),
    ("providers/upstream_dashboard.py", "UpstreamDashboardProvider.get_dashboard_config.normalize", "sanitize_untrusted_data"): (1, "dashboard"),
    ("providers/upstream_read_gateway.py", "UpstreamReadGateway._safe_identity_evidence", "sanitize_untrusted_data"): (1, "delegated"),
    ("providers/upstream_read_gateway.py", "UpstreamReadGateway._safe_observed_tool_name", "sanitize_untrusted_data"): (1, "delegated"),
    ("providers/upstream_read_gateway.py", "UpstreamReadGateway.execute", "sanitize_untrusted_data"): (1, "delegated"),
    ("providers/upstream_read_gateway.py", "UpstreamReadGateway.run_held_read_canary", "sanitize_untrusted_data"): (1, "delegated"),
    ("providers/upstream_yaml_reader.py", "_sanitizer_rule", "sanitize_untrusted_data"): (1, "delegated"),
    ("providers/upstream_yaml_reader.py", "project_success", "sanitize_untrusted_data"): (1, "delegated"),
    ("providers/upstream_yaml_reader.py", "seal", "sanitize_untrusted_data"): (1, "delegated"),
    ("reliability/provider.py", "DirectHaReliabilityProvider._collect_system_log", "sanitize_untrusted_data"): (1, "logs"),
    ("reliability/provider.py", "DirectHaReliabilityProvider._collect_traces.fetch", "sanitize_untrusted_data"): (1, "analysis"),
    ("reliability/provider.py", "DirectHaReliabilityProvider.collect", "sanitize_untrusted_data"): (2, "analysis"),
    ("request_context.py", "request_id_is_safe", "sanitize_untrusted_data"): (1, "identifier"),
    ("tools/compatibility.py", "get_error_log.action", "sanitize_untrusted_data"): (1, "logs"),
    ("tools/governance.py", "_identifier_is_safe", "sanitize_untrusted_data"): (1, "identifier"),
    ("trace_normalization.py", "fetch_normalized_trace_list", "sanitize_untrusted_data"): (1, "analysis"),
}

# D11: the only call sites allowed to use the stored-plan re-scan.
STORED_PLAN_RESCAN_SITES = {
    site: count
    for site, (count, _family) in SANITIZER_SITES.items()
    if site[2] == "rescan_stored_plan"
}


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def scan_package(package: Path = PACKAGE) -> dict[tuple[str, str, str], int]:
    """Every entry-point call in the package, keyed by its enclosing scope."""

    found: dict[tuple[str, str, str], int] = {}
    for path in sorted(package.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = path.relative_to(package).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def walk(node: ast.AST, scope: tuple[str, ...]) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    walk(child, (*scope, child.name))
                    continue
                if isinstance(child, ast.Call) and _call_name(child) in ENTRY_POINTS:
                    key = (module, ".".join(scope) or "<module>", _call_name(child))
                    found[key] = found.get(key, 0) + 1
                walk(child, scope)

        walk(tree, ())
    return found


def entry_point_aliases(package: Path = PACKAGE) -> list[str]:
    """Imports that rename an entry point, or reach one by another name."""

    aliases = []
    for path in sorted(package.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for name in node.names:
                    if name.name in ENTRY_POINTS and name.asname not in (None, name.name):
                        aliases.append(f"{path.name}:{node.lineno}")
            if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Name, ast.Attribute)):
                target = node.value.id if isinstance(node.value, ast.Name) else node.value.attr
                if target in ENTRY_POINTS:
                    aliases.append(f"{path.name}:{node.lineno}")
    return aliases


def identity_mode_disabled(package: Path = PACKAGE) -> list[str]:
    hits = []
    for path in sorted(package.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.keyword) and node.arg == "identity_markers_only":
                if not (isinstance(node.value, ast.Constant) and node.value.value is True):
                    hits.append(f"{path.name}:{node.value.lineno}")
    return hits


class CoverageTests(unittest.TestCase):
    def test_every_sanitizer_call_site_is_in_the_committed_table(self):
        found = scan_package()
        self.assertEqual(
            found, {site: count for site, (count, _family) in SANITIZER_SITES.items()}
        )

    def test_every_site_maps_to_a_ranked_family_with_an_injector(self):
        families = {family for _count, family in SANITIZER_SITES.values()}
        self.assertEqual(families, set(FAMILIES))
        injectors = (ROOT / "tests/test_hamcp191_families.py").read_text(encoding="utf-8")
        for family in FAMILIES:
            with self.subTest(family=family):
                self.assertRegex(injectors, rf"\bFAMILY = \"{family}\"")

    def test_the_proof_fails_for_an_unlisted_or_removed_site(self):
        found = scan_package()
        listed = {site: count for site, (count, _family) in SANITIZER_SITES.items()}
        extra = dict(listed)
        extra[("example.py", "new_tool", "sanitize_untrusted_data")] = 1
        self.assertNotEqual(found, extra)
        removed = dict(listed)
        removed.pop(next(iter(removed)))
        self.assertNotEqual(found, removed)

    def test_no_entry_point_is_reached_through_an_alias(self):
        self.assertEqual(entry_point_aliases(), [])

    def test_identity_markers_only_false_raises_and_appears_nowhere(self):
        self.assertEqual(identity_mode_disabled(), [])
        with self.assertRaises(TypeError):
            S.sanitize_untrusted_data("x", identity_markers_only=False)
        with self.assertRaises(TypeError):
            S.sanitize_untrusted_data("x", identity_markers_only=1)
        self.assertEqual(S.sanitize_untrusted_data("x", identity_markers_only=True).value, "x")

    def test_the_default_pass_is_strict(self):
        result = S.sanitize_untrusted_data(FORGED)
        self.assertEqual(result.value, COLLISION)


# ---------------------------------------------------------------------------
# T6: the per-field failure branch.


class _KeyThatFailsOnSecondRender:
    """A key whose first ``str`` (normalization) works and second raises."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.renders = 0

    def __str__(self) -> str:
        self.renders += 1
        if self.renders > 1:
            raise RuntimeError("synthetic key failure")
        return self.text

    def __hash__(self) -> int:
        return hash(self.text)


class FailureBranchTests(unittest.TestCase):
    def test_a_key_that_never_finished_its_scan_is_withheld(self):
        secret = syn.needle("hamcp191 failure key")
        key = _KeyThatFailsOnSecondRender(syn.bearer(secret))
        result = S.sanitize_untrusted_data({key: "ordinary"})
        self.assertEqual(
            result.value, {S.SANITIZATION_FAILURE_MARKER: S.SANITIZATION_FAILURE_MARKER}
        )
        self.assertTrue(result.failed_closed)
        self.assertNotIn(secret, rendered(result.value))

    def test_a_scanned_key_is_written_only_as_its_scanned_text(self):
        secret = syn.needle("hamcp191 failure value")
        for name, raw in (("bearer", syn.bearer(secret)), ("forged", FORGED + " " + secret)):
            with self.subTest(key=name):
                result = S.sanitize_untrusted_data({raw: _ExplodingMapping(a="b")})
                (key,) = result.value
                self.assertNotIn(secret, key)
                self.assertNotEqual(key, raw)
                self.assertEqual(result.value[key], S.SANITIZATION_FAILURE_MARKER)

    def test_a_key_that_cannot_render_fails_the_whole_value_closed(self):
        class Unrenderable:
            def __str__(self):
                raise RuntimeError("synthetic")

            def __hash__(self):
                return 1

        result = S.sanitize_untrusted_data({Unrenderable(): "x"})
        self.assertEqual(result.value, S.SANITIZATION_FAILURE_MARKER)
        self.assertTrue(result.failed_closed)


# ---------------------------------------------------------------------------
# T7: the identity record.


def produced(registry: S.ReferenceMarkerRegistry, secret: str, known=()) -> str:
    """A marker-bearing string the strict pass produced for ``registry``."""

    value = S.sanitize_untrusted_data(
        {"note": syn.bearer(secret) + " tail"}, registry=registry, known_secrets=known
    ).value["note"]
    assert marker("token") in value
    return value


class IdentityRecordTests(unittest.TestCase):
    def test_engineering_output_survives_by_identity(self):
        registry = S.ReferenceMarkerRegistry()
        own = produced(registry, syn.needle("hamcp191 own"))
        result = S.sanitize_untrusted_data({"again": own}, registry=registry)
        self.assertIs(result.value["again"], own)
        self.assertEqual(result.redacted_field_count, 0)
        self.assertEqual(result.marker_collision_count, 0)

    def test_a_byte_identical_upstream_copy_collides(self):
        registry = S.ReferenceMarkerRegistry()
        own = produced(registry, syn.needle("hamcp191 copy"))
        copy = "".join(list(own))
        self.assertEqual(copy, own)
        self.assertIsNot(copy, own)
        result = S.sanitize_untrusted_data({"upstream": copy}, registry=registry)
        self.assertEqual(result.value["upstream"], COLLISION)
        self.assertEqual(result.marker_collision_count, 1)

    def test_wrong_type_slice_and_other_request_are_scanned(self):
        registry = S.ReferenceMarkerRegistry()
        own = produced(registry, syn.needle("hamcp191 forms"))

        class Text(str):
            pass

        for name, value, other in (
            ("subclass", Text(own), registry),
            ("slice", own[:-1], registry),
            ("other_request", own, S.ReferenceMarkerRegistry()),
            ("no_registry", own, None),
        ):
            with self.subTest(form=name):
                result = S.sanitize_untrusted_data({"v": value}, registry=other)
                self.assertEqual(result.value["v"], COLLISION)
        with self.assertRaises(TypeError):
            registry.register_output(Text("x"))
        self.assertFalse(registry.output(Text(own)))

    def test_the_record_holds_a_strong_reference(self):
        registry = S.ReferenceMarkerRegistry()
        own = produced(registry, syn.needle("hamcp191 strong"))
        identity, text = id(own), str(own)
        del own
        gc.collect()
        held = registry._outputs[identity][0]
        self.assertEqual(held, text)
        self.assertEqual(id(held), identity)
        self.assertTrue(registry.output(held))

    def test_identity_never_skips_a_secret_the_producing_pass_lacked(self):
        registry = S.ReferenceMarkerRegistry()
        late = syn.needle("hamcp191 late")
        own = S.sanitize_untrusted_data(
            {"note": syn.bearer(syn.needle("hamcp191 first")) + " " + late}, registry=registry
        ).value["note"]
        self.assertIn(late, own)
        kept = S.sanitize_untrusted_data({"v": own}, registry=registry, known_secrets=(syn.needle("absent"),))
        self.assertIs(kept.value["v"], own)
        rescanned = S.sanitize_untrusted_data({"v": own}, registry=registry, known_secrets=(late,))
        self.assertEqual(rescanned.value["v"], COLLISION)
        self.assertNotIn(late, rendered(rescanned.value))

    def test_an_inherited_record_never_skips_a_secret_the_child_pass_adds(self):
        # Codex review R1 reproduction: a parent's output rescanned through a
        # child record with a newly known secret is scanned, not kept.
        parent = S.ReferenceMarkerRegistry()
        first, late = syn.needle("review first"), syn.needle("review late")
        original = S.sanitize_untrusted_data(
            syn.bearer(first) + " tail " + late, known_secrets=(first,), registry=parent
        ).value
        self.assertIn(late, original)
        child = S.ReferenceMarkerRegistry(inherits=parent)
        result = S.sanitize_untrusted_data(original, known_secrets=(first, late), registry=child)
        self.assertNotIn(late, rendered(result.value))
        self.assertTrue(result.redaction_applied)

    def test_an_inherited_record_is_read_only(self):
        parent = S.ReferenceMarkerRegistry()
        own = produced(parent, syn.needle("hamcp191 parent"))
        child = S.ReferenceMarkerRegistry(inherits=parent)
        self.assertIs(S.sanitize_untrusted_data({"v": own}, registry=child).value["v"], own)
        child_own = produced(child, syn.needle("hamcp191 child"))
        self.assertTrue(child.output(child_own))
        self.assertFalse(parent.output(child_own))
        self.assertEqual(
            S.sanitize_untrusted_data({"v": child_own}, registry=parent).value["v"], COLLISION
        )

    def test_each_request_gets_its_own_record(self):
        first, second = RequestTelemetry("hamcp191-a"), RequestTelemetry("hamcp191-b")
        self.assertIsInstance(first.sanitizer_registry, S.ReferenceMarkerRegistry)
        self.assertIsNot(first.sanitizer_registry, second.sanitizer_registry)
        own = produced(first.sanitizer_registry, syn.needle("hamcp191 request"))
        self.assertFalse(second.sanitizer_registry.output(own))

    def test_marker_text_past_the_string_limit_still_collides(self):
        # Codex review R2 reproduction: the collision check covers the whole
        # input string, not just the part that survives truncation.
        raw = "ordinary " * 100 + marker("token")
        result = S.sanitize_untrusted_data(raw, max_string=40)
        self.assertEqual(result.value, COLLISION)
        self.assertEqual(result.marker_collision_count, 1)

    def test_truncation_suffix_joins_the_record(self):
        registry = S.ReferenceMarkerRegistry()
        long_text = "ordinary " * 40
        bounded = S.sanitize_untrusted_data({"v": long_text}, max_string=32, registry=registry).value["v"]
        self.assertTrue(bounded.endswith(S.TRUNCATION_SUFFIX))
        self.assertTrue(registry.output(bounded))
        again = S.sanitize_untrusted_data({"v": bounded}, max_string=64, registry=registry)
        self.assertIs(again.value["v"], bounded)
        # A recorded output bounded again is cut by the same truncation.
        shorter = S.sanitize_untrusted_data({"v": bounded}, max_string=16, registry=registry)
        self.assertEqual(shorter.value["v"], bounded[:16] + S.TRUNCATION_SUFFIX)
        self.assertEqual(shorter.truncated_field_count, 1)
        self.assertTrue(registry.output(shorter.value["v"]))
        forged = S.sanitize_untrusted_data({"v": "".join(list(bounded))}, registry=registry)
        self.assertEqual(forged.value["v"], COLLISION)

    def test_dependency_snapshot_record_survives_for_analysis(self):
        from ha_mcp_engineering.dependency import models as M

        snapshot_registry = S.ReferenceMarkerRegistry()
        with M.bind_sanitizer_registry(snapshot_registry):
            self.assertIs(M.extraction_registry(), snapshot_registry)
            own = produced(snapshot_registry, syn.needle("hamcp191 snapshot"))
        self.assertIsNone(M.extraction_registry())
        analysis = S.ReferenceMarkerRegistry(inherits=snapshot_registry)
        self.assertIs(S.sanitize_untrusted_data({"v": own}, registry=analysis).value["v"], own)

    def test_audit_sink_keeps_only_the_requests_own_markers(self):
        from ha_mcp_engineering.audit import AuditLogger

        registry = S.ReferenceMarkerRegistry()
        own = produced(registry, syn.needle("hamcp191 audit"))
        copy = "".join(list(own))
        logger = AuditLogger.__new__(AuditLogger)
        logger.access_secret = ""
        logger.max_payload_chars = 1 << 20
        safe = logger.sanitize({"own": own, "copy": copy}, registry=registry)
        self.assertIs(safe["own"], own)
        self.assertEqual(safe["copy"], COLLISION)
        self.assertEqual(redact_data({"own": own}, registry=registry)["own"], own)

    def test_gateway_withheld_evidence_joins_the_active_request_record(self):
        from ha_mcp_engineering.providers import upstream_read_gateway as G
        from ha_mcp_engineering.request_context import begin_request, end_request

        self.assertTrue(S.marker_like_text(G._REDACTED_EVIDENCE))
        telemetry, token = begin_request("hamcp191-withheld")
        try:
            withheld = G._withheld_evidence()
            registry = telemetry.sanitizer_registry
            self.assertTrue(registry.output(withheld))
            kept = S.sanitize_untrusted_data({"e": withheld, "copy": "".join(list(withheld))}, registry=registry)
            self.assertIs(kept.value["e"], withheld)
            self.assertEqual(kept.value["copy"], COLLISION)
        finally:
            end_request(token)
        # Outside a request nothing is recorded, so the placeholder collides.
        self.assertEqual(S.sanitize_untrusted_data({"e": G._withheld_evidence()}).value["e"], COLLISION)


# ---------------------------------------------------------------------------
# D3 counters, Finding 11 evidence, D11 exception.


class MarkerCollisionHealthTests(unittest.TestCase):
    def test_collisions_and_refusals_are_counted(self):
        before = S.marker_collision_health()
        result = S.sanitize_untrusted_data({"a": FORGED, "b": [FORGED, "ordinary"]})
        self.assertEqual(result.marker_collision_count, 2)
        after = S.marker_collision_health()
        self.assertEqual(after["marker_collisions"] - before["marker_collisions"], 2)
        self.assertEqual(after["marker_collision_refusals"], before["marker_collision_refusals"])
        self.assertTrue(S.record_marker_collision_refusal(result))
        clean = S.sanitize_untrusted_data({"a": syn.bearer(syn.needle("hamcp191 d3"))})
        self.assertFalse(S.record_marker_collision_refusal(clean))
        final = S.marker_collision_health()
        self.assertEqual(final["marker_collision_refusals"] - before["marker_collision_refusals"], 1)
        self.assertEqual(final["marker_collisions"], after["marker_collisions"])

    def test_ordinary_data_counts_nothing(self):
        before = S.marker_collision_health()
        S.sanitize_untrusted_data({"state": "on", "friendly_name": "Kitchen [main]"})
        self.assertEqual(S.marker_collision_health(), before)

    def test_health_payload_carries_the_counters(self):
        source = (PACKAGE / "health.py").read_text(encoding="utf-8")
        self.assertIn('"strict_marker_mode": True,\n                **marker_collision_health(),\n'
                      '                "request_id_replacements": request_id_replacement_count(),', source)
        self.assertIs(H.marker_collision_health, S.marker_collision_health)
        self.assertEqual(set(S.marker_collision_health()), {"marker_collisions", "marker_collision_refusals"})


class RedactionEvidenceTests(unittest.TestCase):
    def test_summary_is_none_without_redaction(self):
        self.assertIsNone(S.redaction_summary(S.sanitize_untrusted_data({"state": "on"})))
        truncated = S.sanitize_untrusted_data("ordinary " * 20, max_string=8)
        self.assertEqual(truncated.truncated_field_count, 1)
        self.assertIsNone(S.redaction_summary(truncated))

    def test_summary_is_bounded_and_truthful(self):
        secret = syn.needle("hamcp191 summary")
        result = S.sanitize_untrusted_data({
            "a": syn.bearer(secret),
            "password": "x",
            "b": FORGED,
        })
        summary = S.redaction_summary(result)
        self.assertEqual(summary["redacted_field_count"], 3)
        self.assertEqual(summary["categories"], sorted({"token", "password", COLLISION_CATEGORY}))
        self.assertEqual(summary["category_count"], 3)
        self.assertEqual(summary["marker_collision_count"], 1)
        self.assertNotIn(secret, rendered(summary))

    def test_tally_bounds_categories_and_counts(self):
        tally = S.RedactionTally()
        for index in range(40):
            tally.add(S.SanitizationResult(
                value=None, redacted_field_count=10 ** 5,
                redaction_categories=(f"category_{index:02d}",), marker_collision_count=10 ** 5,
            ))
        summary = tally.summary()
        self.assertEqual(len(summary["categories"]), S.MAX_REDACTION_CATEGORIES)
        self.assertEqual(summary["category_count"], 40)
        self.assertEqual(summary["redacted_field_count"], S.MAX_REDACTION_COUNT)
        self.assertEqual(summary["marker_collision_count"], S.MAX_REDACTION_COUNT)
        self.assertTrue(tally.applied)
        self.assertFalse(S.RedactionTally().applied)


class StoredPlanExceptionTests(unittest.TestCase):
    """D11: the stored-plan re-scan keeps beta.19's marker tolerance."""

    def test_stored_plan_markers_are_kept_and_text_between_is_scanned(self):
        secret = syn.needle("hamcp191 stored")
        stored = {"note": FORGED + " " + syn.bearer(secret), "summary": "review ...<truncated>"}
        result = S.rescan_stored_plan(stored)
        self.assertEqual(result.value["note"], marker("token") + " Bearer " + marker("token"))
        self.assertEqual(result.value["summary"], "review ...<truncated>")
        self.assertEqual(result.marker_collision_count, 0)
        self.assertEqual(result.redaction_categories, ("token",))
        self.assertNotIn(secret, rendered(result.value))

    def test_canonical_markers_alone_are_not_counted(self):
        result = S.rescan_stored_plan({"note": marker("password") + " and " + marker("token")})
        self.assertEqual(result.value["note"], marker("password") + " and " + marker("token"))
        self.assertEqual(result.redacted_field_count, 0)

    def test_the_strict_pass_does_not_share_the_tolerance(self):
        stored = {"note": marker("password") + " ordinary"}
        self.assertEqual(S.sanitize_untrusted_data(stored).value["note"], COLLISION)
        self.assertEqual(S.rescan_stored_plan(stored).value["note"], stored["note"])

    def test_known_secrets_keys_and_failures_stay_strict(self):
        secret = syn.needle("hamcp191 stored known")
        result = S.rescan_stored_plan(
            {"a": marker(secret), "token": "x", FORGED: "y"}, known_secrets=(secret,)
        )
        self.assertNotIn(secret, rendered(result.value))
        self.assertEqual(result.value["token"], marker("token"))
        # A canonical marker key keeps the D11 tolerance; it is not a collision.
        self.assertEqual(result.value[FORGED], "y")
        self.assertEqual(result.marker_collision_count, 0)
        failed = S.rescan_stored_plan({"a": _ExplodingMapping(b="c")})
        self.assertEqual(failed.value["a"], S.SANITIZATION_FAILURE_MARKER)

    def test_memos_do_not_cross_passes(self):
        memo = S.SegmentMemo(())
        S.rescan_stored_plan({"a": "x"}, segment_memo=memo)
        with self.assertRaises(TypeError):
            S.sanitize_untrusted_data({"a": "x"}, segment_memo=memo)
        strict = S.SegmentMemo(())
        S.sanitize_untrusted_data({"a": "x"}, segment_memo=strict)
        with self.assertRaises(TypeError):
            S.rescan_stored_plan({"a": "x"}, segment_memo=strict)

    def test_only_the_allow_listed_sites_call_the_stored_plan_rescan(self):
        found = {
            site: count for site, count in scan_package().items() if site[2] == "rescan_stored_plan"
        }
        self.assertEqual(found, STORED_PLAN_RESCAN_SITES)
        self.assertEqual(sum(found.values()), 6)
        self.assertEqual({module for module, _scope, _name in found}, {"governance/resources.py", "governance/service.py"})

    def test_the_allow_list_proof_fails_for_a_new_caller(self):
        widened = dict(STORED_PLAN_RESCAN_SITES)
        widened[("providers/upstream_read_gateway.py", "UpstreamReadGateway.execute", "rescan_stored_plan")] = 1
        found = {site: count for site, count in scan_package().items() if site[2] == "rescan_stored_plan"}
        self.assertNotEqual(found, widened)


# ---------------------------------------------------------------------------
# X1, X2 and the linear look-alike detector.


class StructuralTruncationTests(unittest.TestCase):
    def test_flag_comes_from_the_bound_not_the_text(self):
        data = {"log": "line ... [truncated at 10 chars] upstream text"}
        text, truncated = dump_json_with_truncation(data, 10_000)
        self.assertFalse(truncated)
        self.assertEqual(text, dump_json(data, 10_000))
        big = {"rows": ["ordinary row"] * 2_000}
        text, truncated = dump_json_with_truncation(big, 2_000)
        self.assertTrue(truncated)
        self.assertLessEqual(len(text), 2_000)
        self.assertEqual(text, dump_json(big, 2_000))

    def test_dispatcher_uses_the_structural_flag(self):
        source = (PACKAGE / "providers/dispatch.py").read_text(encoding="utf-8")
        self.assertNotIn("[truncated at ", source)
        self.assertIn("rendered, truncated = dump_json_with_truncation(", source)


class IncidentHeaderTests(unittest.TestCase):
    def test_authorization_text_becomes_a_non_marker_constant(self):
        secret = syn.needle("hamcp191 incident")
        text = N._bounded_text("request failed " + syn.auth_header(syn.bearer(secret)) + " done")
        self.assertIn(N.AUTHORIZATION_WITHHELD, text)
        self.assertNotIn(secret, text)
        self.assertFalse(S.marker_like_text(N.AUTHORIZATION_WITHHELD))
        self.assertFalse(S.marker_like_text(text))
        self.assertEqual(S.sanitize_untrusted_data(text).value, text)


_REFERENCE_DETECTOR = re.compile(r"[\[(<{][\W_]*(?:redacted|truncated)")


def _reference_fold(text: str) -> str:
    folded = text.translate(S._MARKER_FOLD)
    folded = unicodedata.normalize("NFD", unicodedata.normalize("NFKC", folded))
    folded = "".join(char for char in folded if unicodedata.category(char) not in ("Cf", "Mn", "Me"))
    return folded.translate(S._MARKER_FOLD).casefold().translate(S._MARKER_FOLD)


class LinearDetectorTests(unittest.TestCase):
    ALPHABET = "[](){}<>:_ -.%【⁅redactuntRTе​́x1"

    def test_linear_detector_equals_the_reference_grammar(self):
        rng = random.Random(191)
        for _ in range(20_000):
            text = "".join(rng.choice(self.ALPHABET) for _ in range(rng.randint(0, 14)))
            folded = _reference_fold(text)
            with self.subTest(text=text):
                self.assertEqual(
                    S._marker_run_present(folded), _REFERENCE_DETECTOR.search(folded) is not None
                )

    def test_ascii_fast_path_equals_the_full_fold(self):
        rng = random.Random(1910)
        alphabet = [chr(code) for code in range(32, 127)]
        for _ in range(5_000):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24)))
            self.assertEqual(S._marker_fold(text), _reference_fold(text))

    def test_long_opener_runs_are_linear(self):
        for text in ("[" * 65_536 + "x", "([{<" * 16_384 + "redact", "[_" * 32_768):
            started = time.perf_counter()
            S.marker_like_text(text)
            S.sanitize_untrusted_data({"template": text}, max_string=None)
            self.assertLess(time.perf_counter() - started, 2.0)
        self.assertTrue(S.marker_like_text("[" * 65_536 + "redacted"))


# ---------------------------------------------------------------------------
# The committed-file scan for the HAMCP-191 files.

HAMCP191_GLOBS = (
    "tests/test_hamcp191_*.py",
    "tests/fixtures/hamcp191/**/*",
    "docs/evidence/hamcp191/**/*",
    "docs/evidence/hamcp191-mutations/**/*",
    "docs/HAMCP_191_*.md",
)

# Codex review L2: every other file this change touches, so the scan covers
# the whole pull-request diff and not only the HAMCP-191-named files.
HAMCP191_CHANGED_FILES = (
    "README.md",
    "docs/V2_4_1_BETA20_ACCEPTANCE.md",
    "docs/V2_4_1_BETA20_RELEASE_NOTES.md",
    "hass_mcp_engineering_beta/CHANGELOG.md",
    "hass_mcp_engineering_beta/config.yaml",
    "hass_mcp_engineering_beta/ha_mcp_engineering/audit.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/dependency/extraction.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/dependency/index.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/dependency/models.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/dependency/script_calls.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/dependency/script_sources.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/f3_dashboard/approval_projection.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/f3_dashboard/semantic_diff.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/governance/config_validation.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/governance/operational_lifecycle.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/governance/resources.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/governance/service.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/governance/storage.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/health.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/impact/provider.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/incident/normalization.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/integrity/provider.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/logging_config.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/models/responses.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/providers/core_logs.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/providers/dispatch.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_dashboard.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_yaml_reader.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/request_context.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/routing.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/sanitization.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/tools/governance.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/version.py",
    "scripts/validate_addon_metadata.py",
    "tests/test_2_1a_beta2_operational_lifecycle.py",
    "tests/test_automation_reliability_analysis.py",
    "tests/test_beta11_sanitization.py",
    "tests/test_beta25_external_approval.py",
    # tests/test_dev14_configuration_plans.py is not listed: this PR adds
    # only its plant_stored_record helper, and its pre-existing main-branch
    # fixtures carry secret-shaped refusal inputs by design.
    "tests/test_governance_async_health.py",
    "tests/test_hamcp162_yaml_reader.py",
    "tests/test_hamcp171_health_verdict_cache.py",
    "tests/test_readonly_upstream_gateway.py",
)


class CommittedFileScanTests(unittest.TestCase):
    def test_no_committed_hamcp191_file_has_secret_shaped_text(self):
        for name in HAMCP191_CHANGED_FILES:
            self.assertTrue((ROOT / name).is_file(), name)
        files = sorted({
            path.resolve()
            for pattern in (*HAMCP191_GLOBS, *HAMCP191_CHANGED_FILES)
            for path in ROOT.glob(pattern)
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        })
        self.assertIn(Path(__file__).resolve(), files)
        self.assertIn((ROOT / "tests/test_beta11_sanitization.py").resolve(), files)
        # Correction batch 3's tests are scanned through the glob.
        self.assertIn((ROOT / "tests/test_hamcp191_batch3.py").resolve(), files)
        self.assertEqual(syn.scan(files), [])


if __name__ == "__main__":
    unittest.main()
