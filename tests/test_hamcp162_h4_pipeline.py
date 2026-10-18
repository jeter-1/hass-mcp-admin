"""HAMCP-162 H4: one screen for every string the reader can emit.

- Codex's ten H4 reproductions (five key shapes at both supported ha-mcp
  versions, through the real pinned MCP ClientSession), the 199/200/201
  opaque-key boundary, nested/list and unlisted-marker-wrapped keys,
  lowercase PEM keys, and the encoded, uppercase and value controls.
- A1 (architect review): header-less base64 bodies wrapped by newlines or
  spaces under an innocent key, the wrapped 199/200/201 boundary, and the
  punctuated multi-line prose that stays visible.
- The rule x position matrix: one synthetic case for every redaction rule
  (the rules registered in ``SCANNER_REGISTRY``, which is the execution
  path, with the shared sanitizer expanded into its segment and serialized
  rules and its known-secret rule) in every output position
  (``OUTPUT_POSITIONS``). Each case runs through ``screen`` directly and
  end to end through the gateway: at 8.5.0 over a real MCP client session
  to the Engineering server and a real ClientSession to a synthetic ha-mcp
  peer, and at 8.4.3 through the in-memory transport. The needle never
  appears in the serialized response.
- The coverage test (M1): every registered rule x every position is a
  matrix cell and every matrix cell names a registered rule; proof tests
  show that an unmatrixed registered rule and an unregistered matrixed rule
  each fail it, every withheld cell is decided by a registered rule, and
  the executor holds no check outside the registry.
- The uniform-entry test: every emitted string is a ``screen`` output in
  its field's position.
- The committed-file scan: no HAMCP-162 test, fixture or evidence file
  contains literal secret-shaped text.

Every synthetic secret is built at runtime by ``hamcp162_synthetic``.
Synthetic peers only; no live Home Assistant, no real credential.
"""

import ast
import asyncio
from collections import Counter
from contextlib import ExitStack, asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, fields as dataclass_fields, replace
import inspect
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable
import unittest
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

import hamcp162_synthetic as syn  # noqa: E402
import test_hamcp162_yaml_reader as T  # noqa: E402
from ha_mcp_engineering import sanitization as S  # noqa: E402
from ha_mcp_engineering.providers import upstream_read_gateway as G  # noqa: E402

y = T.y

# Each gateway publishes its catalog into the process-wide capability catalog.
# The shared per-test reset (tests/support/process_isolation.py) restores it
# after every test, so later modules see the static catalog only.

KNOWN = syn.needle("matrixconfigured")  # the configured ha_token
MATTER_DIGITS = "3141-5926-535"


def nest(context: tuple[str, ...], leaf: Any) -> Any:
    for key in reversed(context):
        leaf = {key: leaf}
    return leaf


def serialized(result: Any) -> str:
    return json.dumps(result, ensure_ascii=False)


def absent(needle: str, text: str) -> bool:
    return needle.casefold() not in text.casefold()


# ---------------------------------------------------------------------------
# The rules.


@dataclass(frozen=True)
class Rule:
    name: str  # unique case id
    covers: str  # the code rule it exercises
    text: str  # the synthetic string
    needle: str  # what must never be emitted
    fires: Callable[[str], bool]  # the rule's own predicate holds on text
    context: tuple[str, ...] = ()  # keys above the string


def _canonical(text: str) -> str:
    return y.canonical(text)[0]


def _refused(text: str) -> bool:
    try:
        y.canonical_forms(text)
    except y._EncodingRefused:
        return True
    return False


def _pattern_names(module) -> dict[int, str]:
    return {id(value): name for name, value in vars(module).items() if isinstance(value, re.Pattern)}


SEGMENT_RULE_NAMES = tuple(_pattern_names(S)[id(pattern)] for pattern, _c, _f in S._SEGMENT_RULES)
SERIALIZED_CATEGORIES = tuple(category for _pattern, category in S._SERIALIZED_PATTERNS)


SANITIZER_RULE_IDS = frozenset(
    {"segment:" + name for name in SEGMENT_RULE_NAMES}
    | {"serialized:" + category for category in SERIALIZED_CATEGORIES}
    | {"sanitizer:known_secret"}
)


def expected_rule_ids() -> set[str]:
    """Every redaction rule, enumerated at call time from the registry that
    executes them (M1); "sanitizer" expands into the sanitizer's own rules."""

    registered = set(y.scanner_rule_ids())
    if "sanitizer" in registered:
        registered = (registered - {"sanitizer"}) | SANITIZER_RULE_IDS
    return registered


def registered_rule(covers: str) -> str:
    """The registry rule a matrix case's covered rule belongs to."""

    return "sanitizer" if covers in SANITIZER_RULE_IDS else covers


_SEGMENT_BUILDERS: dict[str, Callable[[str], str]] = {
    "_AUTH_HEADER": syn.auth_header,
    "_BEARER": syn.bearer,
    "_COOKIE_HEADER": syn.cookie_header,
    "_JWT": lambda n: "ey" + "J" + "synthHead0" + "." + n + "." + "synthSig00",
    "_URL_USERINFO": lambda n: syn.url("https", "u", n, "h.invalid/x"),
    "_URL_ENCODED_USERINFO": lambda n: syn.url("https", None, None, "u%3A" + n + "@h.invalid/x"),
    "_URL_CREDENTIAL_PARAMETER": lambda n: syn.url_parameter("signature", n),
    "_WEBHOOK_PATH": syn.webhook_path,
    "_WEBHOOK_PROSE": syn.webhook_prose,
    "_LOGIN_FLOW_PATH": syn.login_flow_path,
    "_MATTER_PAYLOAD": syn.matter_payload,
    "_MATTER_CODE_LABEL": lambda _n: syn.matter_code(MATTER_DIGITS),
}
_SERIALIZED_KEYS = {
    "matter_setup_code": "pairing_code",
    "matter_setup_payload": "setup_payload",
    "auth_flow": "login_flow_id",
    "webhook_identifier": "webhook_id",
    "webhook_secret": "webhook_token",
    "token": "client_secret",
    "password": "passphrase",
    "auth_cookie": "set_cookie",
}


def build_rules() -> tuple[Rule, ...]:
    rules = []

    # Needles are neutral: a rule name such as "credential_text" would
    # itself carry a credential word and be withheld without its trigger.
    needles: dict[str, str] = {}

    def n(name):
        return needles.setdefault(name, syn.needle(f"neutralmatrixcase{len(needles):02d}"))

    def add(name, text, fires, *, covers=None, needle=None, context=()):
        rules.append(Rule(name, covers or name, text, needle or n(name), fires, context))

    add("credential_key_context", n("credential_key_context"),
        lambda _t: any(y.is_credential_key(k) for k in ("password",)), context=("password",))
    add("key_scoped", n("key_scoped"),
        lambda _t: S.key_category("payload", ("matter",)) is not None
        and not any(y.is_credential_key(k) for k in ("matter", "payload")),
        context=("matter", "payload"))
    add("connection_context", n("connection_context"),
        lambda _t: y.is_connection_key("db_url") and not y.is_credential_key("db_url"),
        context=("db_url",))
    add("canonicalize", syn.enc(syn.url("mysql", "u", n("canonicalize"), "db.invalid/x"), 2),
        lambda t: _canonical(t) != t.casefold() and y.credential_text(_canonical(t))
        and not y.credential_text(t.casefold()) and y.scanner_category(t, ()) is None)
    add("canonicalize:refused", "%41" + n("canonicalize:refused") + "%zz", _refused,
        covers="canonicalize")
    # U+27E6 folds to "[" and U+0301 is a combining mark the fold drops.
    add("marker_lookalike", "\u27e6r\u0301edacted:" + n("marker_lookalike") + "\u27e7",
        S.marker_like_text)
    add("credential_text_userinfo", syn.url("mysql", "u", n("credential_text_userinfo"), "db.invalid/x"),
        lambda t: y._userinfo(_canonical(t)) and y.scanner_category(t, ()) is None)
    add("credential_text_word", "auth " + n("credential_text_word"),
        lambda t: any(word in _canonical(t) for word in y.CREDENTIAL_WORDS))
    add("credential_text_delimited", "pass=" + n("credential_text_delimited"),
        lambda t: y._DELIMITED_TOKEN.search(_canonical(t)) is not None
        and not any(word in _canonical(t) for word in y.CREDENTIAL_WORDS))
    add("key_text", "api key " + n("key_text"),
        lambda t: y.key_text_unsafe(_canonical(t)) and not y.credential_text(_canonical(t)))
    for name in SEGMENT_RULE_NAMES:
        needle = MATTER_DIGITS if name == "_MATTER_CODE_LABEL" else n(name)
        pattern = getattr(S, name)
        add("segment:" + name, _SEGMENT_BUILDERS[name](needle),
            lambda t, p=pattern: p.search(t) is not None, needle=needle)
    for pattern, category in S._SERIALIZED_PATTERNS:
        add("serialized:" + category, syn.serialized(_SERIALIZED_KEYS[category], n(category)),
            lambda t, p=pattern: p.search(t) is not None, needle=n(category))
    add("sanitizer:known_secret", "id " + KNOWN + " end",
        lambda t: KNOWN in t, needle=KNOWN)
    add("known_secret_casefold", KNOWN.swapcase(),
        lambda t: KNOWN not in t and KNOWN.casefold() in _canonical(t), needle=KNOWN)
    add("pem", syn.pem("CERTIFICATE", n("pem")), lambda t: y._PEM.search(t) is not None)
    add("pem:lower", syn.pem("CERTIFICATE", n("pem:lower"), lower=True),
        lambda t: y._PEM.search(t) is not None and t == t.lower(), covers="pem")
    add("pem:private", syn.private_key_pem(n("pem:private")),
        lambda t: y._PEM.search(t) is not None, covers="pem")
    blob_needle = n("opaque_blob")
    add("opaque_blob", blob_needle + syn.opaque(200 - len(blob_needle)),
        lambda t: y._OPAQUE_BLOB.search(t) is not None and len(t) == 200, needle=blob_needle)
    # A1: the same 200 characters wrapped at 64, header-less. No single run
    # reaches 200; only the whitespace-stripped check fires.
    for wrap, separator in (("newline_wrapped", "\n"), ("space_wrapped", " ")):
        name = "opaque_blob:" + wrap
        wrapped_needle = n(name)
        add(name, syn.wrapped(wrapped_needle + syn.opaque(200 - len(wrapped_needle)),
                              separator=separator),
            lambda t, s=separator: s in t and y._OPAQUE_BLOB.search(t) is None
            and y._OPAQUE_BLOB.fullmatch(re.sub(r"\s", "", t)) is not None
            and len(re.sub(r"\s", "", t)) == 200,
            covers="opaque_blob_wrapped", needle=wrapped_needle)
    return tuple(rules)


RULES = build_rules()

# Owner decision D12: a credential under an innocent key with no credential
# word in its text is not recognized. key_text (a credential spelled without
# a credential word, such as ``api key x``) therefore stays visible as a value
# or list item, exactly as the pinned D12 residual tests require; it is
# withheld in every other position. These two cells are the only cases where
# the needle is shown, and the matrix asserts that they are.
D12_RESIDUALS = frozenset({("key_text", "value"), ("key_text", "list_item")})


# ---------------------------------------------------------------------------
# The positions: how a string reaches each one end to end.


@dataclass
class Injection:
    yaml_path: str = "sensor.value"
    upstream: Any = "ordinary"
    failure: bool = False


def _success_field(field: str, how: str):
    def inject(stack: ExitStack, planted: Any, injection: Injection) -> None:
        real = G.SuccessResponse.as_dict

        def as_dict(self):
            body = real(self)
            if how == "set":
                body[field] = planted
            elif how == "append":
                body[field] = [*body[field], planted]
            else:
                body[field] = {**body[field], "injected": planted}
            return body

        stack.enter_context(patch.object(G.SuccessResponse, "as_dict", as_dict))
    return inject


def _failure_field(field: str, how: str):
    def inject(stack: ExitStack, planted: Any, injection: Injection) -> None:
        injection.failure = True
        real = G.FailureResponse.as_dict

        def as_dict(self):
            body = real(self)
            if how == "set":
                body[field] = planted
            else:
                body[field] = {**body[field], "injected": planted}
            return body

        stack.enter_context(patch.object(G.FailureResponse, "as_dict", as_dict))
    return inject


def _upstream(build):
    def inject(stack: ExitStack, rule: Rule, injection: Injection) -> None:
        build(rule, injection)
    inject.upstream = True
    return inject


def _value(rule, injection):
    injection.upstream = nest(rule.context + ("leaf",), rule.text)


def _list_item(rule, injection):
    injection.upstream = nest(rule.context + ("items",), [rule.text])


def _key(rule, injection):
    injection.upstream = nest(rule.context, {rule.text: 1})


def _reference_name(rule, injection):
    injection.upstream = nest(rule.context + ("holder",), "!secret " + rule.text)


def _yaml_path(rule, injection):
    injection.yaml_path = ".".join(("sensor",) + rule.context + (rule.text,))


def _include_path(rule, injection):
    injection.yaml_path = ".".join(("sensor",) + rule.context + (rule.text,))
    injection.upstream = "!include extra.yaml"


def _envelope(stack, planted, injection):
    stack.enter_context(patch.object(y, "SOURCE", planted))


def _limitation(stack, planted, injection):
    stack.enter_context(patch.object(y, "DISCLOSURE_LIMITATIONS", (planted,)))


def _redaction(stack, planted, injection):
    real = y.seal

    def seal(value, known_secrets, vocabulary=frozenset()):
        if "redaction" in value:
            value = deepcopy(value)
            value["redaction"]["categories"] = [*value["redaction"]["categories"], planted]
        return real(value, known_secrets, vocabulary)

    stack.enter_context(patch.object(y, "seal", seal))


def _request_id(stack, planted, injection):
    stack.enter_context(patch.object(G, "current_request_id", lambda: planted))


INJECTORS: dict[str, Callable] = {
    "value": _upstream(_value),
    "list_item": _upstream(_list_item),
    "key": _upstream(_key),
    "reference_name": _upstream(_reference_name),
    "yaml_path": _upstream(_yaml_path),
    "include_path": _upstream(_include_path),
    "envelope": _envelope,
    "limitation": _limitation,
    "redaction": _redaction,
    "operation": _success_field("operation", "set"),
    "summary": _success_field("summary", "set"),
    "warning": _success_field("warnings", "append"),
    "metadata": _success_field("metadata", "add"),
    "timing": _success_field("timing", "add"),
    "request_id": _request_id,
    "status": _success_field("success", "set"),
    "error": _failure_field("error", "set"),
    "error_code": _failure_field("error_code", "set"),
    "message": _failure_field("message", "set"),
    "details": _failure_field("details", "add"),
    "retryable": _failure_field("retryable", "set"),
}


def unit_path(rule: Rule, position: str) -> tuple[str, ...]:
    # A reference name's context is the key above its holder.
    return rule.context + (("holder",) if position == "reference_name" else ())


def prepare(stack: ExitStack, rule: Rule, position: str) -> Injection:
    injection = Injection()
    inject = INJECTORS[position]
    if getattr(inject, "upstream", False):
        inject(stack, rule, injection)
    else:
        inject(stack, nest(rule.context, rule.text), injection)
    return injection


def reply(injection: Injection, args: dict) -> dict:
    if injection.failure:
        error = {"code": "CONFIG_INVALID", "message": "synthetic failure"}
        return {"isError": True, "content": [
            {"type": "text", "text": json.dumps({"success": False, "error": error})}]}
    return T.ok(T.payload(deepcopy(injection.upstream), args["yaml_path"]))


def known_settings():
    return replace(T._settings("synthetic-no-key"), ha_mcp_release_registry_enabled=False,
                   ha_token=KNOWN)


async def memory_gateway(version: str):
    transport = T.Transport(T.catalog(version), version)
    gateway = T.UpstreamReadGateway()
    gateway.configure(known_settings(), transport=transport)
    await gateway.initialize(FastMCP(f"synthetic-hamcp162-h4-{version}"))
    return gateway, transport


@asynccontextmanager
async def sdk_gateway(version: str, settings=None):
    """The real pinned SDK on both sides: a client session to the
    Engineering server and a ClientSession to a synthetic ha-mcp peer."""

    peer = T.YamlPeer(T.catalog(version), version)
    gateway = T.UpstreamReadGateway()
    gateway.configure(
        settings or known_settings(),
        transport=T.upstream_read.McpReadGatewayTransport("", timeout_seconds=5,
                                                          client_version="synthetic"),
    )
    with patch.object(T.upstream_read, "streamablehttp_client", peer.connect):
        server = FastMCP(f"synthetic-hamcp162-h4-sdk-{version}")
        await gateway.initialize(server)
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            async def run(arguments):
                result = await client.call_tool(TOOL, arguments)
                return json.loads(result.content[0].text)
            yield peer, run


TOOL = T.TOOL


# ---------------------------------------------------------------------------
# Codex's H4 reproductions and the review's boundary cases.


def blob(length: int = 220) -> str:
    return syn.opaque(length)


def codex_shapes() -> list[tuple[str, Any, str]]:
    b = blob()
    pem = syn.pem("CERTIFICATE", "synthetic", lower=True).rsplit("\n", 1)[0]
    return [
        ("direct", {b: 1}, b),
        ("nested", {"outer": {b: 1}}, b),
        ("list", [{b: 1}], b),
        ("lookalike", {"[ɍedacted:" + b + "]": 1}, b),
        ("pem", {pem: 1}, pem),
    ]


def review_cases() -> list[tuple[str, Any, str, bool]]:
    """(name, upstream value, needle, needle visible?) beyond Codex's ten."""

    b = blob()
    cases = []
    for length in (199, 200, 201):
        key = syn.opaque(length)
        cases.append((f"boundary_{length}", {key: 1}, key, length < 200))
        cases.append((f"boundary_{length}_nested_list", {"a": [{"b": {key: 1}}]}, key, length < 200))
    for name, wrapped in (("unlisted_r", "[ɍedacted:" + b + "]"),
                          ("unlisted_small_capital", "(ʀedacted " + b + ")"),
                          ("unlisted_list", "<ɍedacted>" + b)):
        cases.append((name, {"a": [{wrapped: 1}]}, b, False))
    lower_pem = syn.pem("CERTIFICATE", "synthetic", lower=True)
    cases += [
        ("pem_lower_key", {lower_pem: 1}, lower_pem.split("\n")[0], False),
        ("pem_lower_private_key", {syn.private_key_pem("x").lower(): 1}, "private key", False),
        ("pem_lower_nested", {"a": {"b": [{lower_pem: 1}]}}, lower_pem.split("\n")[0], False),
        # Controls: withheld before H4 too, and still.
        ("control_encoded_key", {"%62" + b[1:]: 1}, b[1:], False),
        ("control_upper_pem_key", {syn.pem("CERTIFICATE", "synthetic"): 1}, "certificate", False),
        ("control_value", {"a": b}, b, False),
        # Usefulness: ordinary keys and values stay visible.
        ("visible_ordinary", {"purge_keep_days": 10, "name": "kitchen"}, "kitchen", True),
    ]
    return cases


class CodexReproductionTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_ten_h4_reproductions(self):
        count = 0
        for version in T.VERSIONS:
            async with sdk_gateway(version, replace(T._settings("synthetic-no-key"),
                                                    ha_mcp_release_registry_enabled=False)) as (peer, run):
                for name, value, needle in codex_shapes():
                    with self.subTest(version=version, shape=name):
                        self.assertIsNotNone(y.scanner_category(needle, ()))
                        peer.reply = lambda args, v=value: T.ok(T.payload(v, args["yaml_path"]))
                        result = await run({"yaml_path": "sensor.value"})
                        self.assertTrue(result["success"], result)
                        self.assertNotIn(needle, serialized(result))
                        self.assertTrue(absent(needle, serialized(result)))
                        count += 1
        self.assertEqual(count, 10)

    async def test_boundary_nested_unlisted_pem_and_controls(self):
        for version in T.VERSIONS:
            async with sdk_gateway(version) as (peer, run):
                for name, value, needle, visible in review_cases():
                    with self.subTest(version=version, case=name):
                        peer.reply = lambda args, v=value: T.ok(T.payload(v, args["yaml_path"]))
                        result = await run({"yaml_path": "sensor.value"})
                        self.assertTrue(result["success"], result)
                        text = serialized(result)
                        if visible:
                            self.assertIn(needle, text)
                            self.assertNotIn("[REDACTED:", serialized(result["data"]["value"]))
                        else:
                            self.assertTrue(absent(needle, text), name)
                            self.assertGreater(result["data"]["redaction"]["redacted_field_count"], 0)

    def test_screen_boundary_in_every_position(self):
        for length in (199, 200, 201):
            for position in sorted(y.OUTPUT_POSITIONS):
                with self.subTest(length=length, position=position):
                    # A path segment is bounded to 64 characters before the
                    # screen; the screen itself still withholds 200+.
                    screened = y.screen(syn.opaque(length), (), position=position)
                    self.assertEqual(screened.kept, length < 200)

    def test_a_path_segment_is_screened_under_the_segments_above_it(self):
        # A path never shows key text the value tree would withhold: the
        # context rules (B, connection context) apply to path segments too.
        child = syn.needle("pathchild")
        for position in ("yaml_path", "include_path"):
            with self.subTest(position=position):
                self.assertEqual(y.project_path(["sensor", "db_url", child], (), position),
                                 "sensor.db_url.*")
                self.assertEqual(y.project_path(["matter", "payload", child], (), position),
                                 "matter.payload.*")
                self.assertEqual(y.project_path(["sensor", "plain", child], (), position),
                                 "sensor.plain." + child)
                self.assertTrue(y.path_segment_safe(child, (), position))
                self.assertFalse(y.path_segment_safe(child, (), position, ("db_url",)))

    def test_a_key_is_scanned_in_its_decoded_and_canonical_forms(self):
        # The scanners run on the raw, decoded and canonical forms of a key
        # too. A full-width or fully percent-encoded run hides from the raw
        # scan and is caught only in a decoded or canonical form.
        def full_width(text):
            return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in text)

        blob = syn.opaque(220)
        cases = {
            "full_width_opaque": (full_width(blob), "opaque_blob"),
            "percent_encoded_opaque": ("".join(f"%{ord(c):02X}" for c in blob), "opaque_blob"),
            "full_width_pem": (full_width(syn.pem("CERTIFICATE", syn.needle("widekey"))), "pem"),
        }
        for name, (key, category) in cases.items():
            with self.subTest(case=name):
                self.assertIsNone(y.scanner_category(key, ()), "the raw form must hide the run")
                for position in sorted(y._KEY_POSITIONS):
                    screened = y.screen(key, (), position=position)
                    self.assertFalse(screened.kept, (name, position))
                    self.assertEqual(screened.category, category)
                result = T.project({key: 1, "name": "ha"})
                text = serialized(result.envelope)
                self.assertNotIn(key, text)
                self.assertNotIn(y.canonical(key)[0], text.casefold())
                self.assertGreater(result.redacted_field_count, 0)


# ---------------------------------------------------------------------------
# A1: a wrapped, header-less key body under an innocent key.


def wrapped_body(tag: str, length: int, separator: str = "\n", width: int = 64) -> tuple[str, str]:
    """(wrapped text, needle): ``length`` base64 characters, needle first."""

    marker = syn.needle(tag)
    return syn.wrapped(marker + syn.opaque(length - len(marker)), width, separator), marker


def ordinary_prose() -> list[str]:
    """Multi-line and long single-line text a configuration really holds."""

    return [
        "Turns on the hallway lights when motion is detected after sunset.\n"
        "The lights stay on for ten minutes, then dim to 20% before turning off.\n"
        "If someone is still moving, the timer restarts; nothing happens by day.\n"
        "Set the house mode to away on the dashboard to skip all of this.\n",
        "{% if is_state('sun.sun', 'below_horizon') %}\n"
        "  {{ states('sensor.hallway_lux') | float(0) < 40 }}\n"
        "{% else %}\n  false\n{% endif %}\n",
        "Reminder for the weekly backup: copy the snapshot to the NAS, check that "
        "the share is mounted, verify the size against last week's copy, and "
        "notify the household if anything looks wrong or the copy took too long.",
    ]


class WrappedOpaqueBlobTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_architect_reproduction_end_to_end(self):
        # 1,200 base64 characters wrapped at 64 under "note": kept before A1.
        # The same text on one line is the control.
        for version in T.VERSIONS:
            async with sdk_gateway(version) as (peer, run):
                for wrap, separator in (("newline", "\n"), ("space", " "), ("one_line", "")):
                    text, marker = wrapped_body("architect" + wrap, 1200, separator)
                    for shape, value in (("value", {"note": text}),
                                         ("list_item", {"notes": [text, "kitchen"]})):
                        with self.subTest(version=version, wrap=wrap, shape=shape):
                            peer.reply = lambda args, v=value: T.ok(T.payload(v, args["yaml_path"]))
                            result = await run({"yaml_path": "sensor.value"})
                            self.assertTrue(result["success"], result)
                            self.assertTrue(absent(marker, serialized(result)))
                            data = result["data"]
                            if shape == "value":
                                self.assertEqual(data["value"]["note"], "[REDACTED:opaque_blob]")
                            else:
                                self.assertEqual(data["value"]["notes"],
                                                 ["[REDACTED:opaque_blob]", "kitchen"])
                            self.assertEqual(data["redaction"]["categories"], ["opaque_blob"])

    def test_the_single_run_check_alone_misses_the_wrapped_body(self):
        # The regression's premise: without A1 no rule fires on this text.
        for separator in ("\n", " "):
            text, _marker = wrapped_body("premise", 1200, separator)
            with self.subTest(separator=separator):
                self.assertIsNone(y._OPAQUE_BLOB.search(text))
                self.assertEqual(y.scanner_category(text, ()), "opaque_blob")

    def test_wrapped_boundary_and_whitespace_forms(self):
        forms = {
            "newline_64": ("\n", 64), "space_64": (" ", 64), "crlf_76": ("\r\n", 76),
            "tab_32": ("\t", 32), "double_space_8": ("  ", 8), "newline_1": ("\n", 1),
            "indented": ("\n    ", 64), "nbsp_64": (" ", 64),
        }
        for name, (separator, width) in forms.items():
            for length in (199, 200, 201):
                text, marker = wrapped_body("boundary" + name, length, separator, width)
                for padded in (text, "\n " + text + " \n"):
                    for position in ("value", "list_item", "key", "reference_name"):
                        with self.subTest(form=name, length=length, position=position,
                                          padded=padded != text):
                            screened = y.screen(padded, (), position=position)
                            self.assertEqual(screened.kept, length < 200)
                            if length >= 200:
                                self.assertEqual(screened.category, "opaque_blob")
                                self.assertTrue(absent(marker, screened.text))

    def test_a_wrapped_body_is_withheld_in_every_position(self):
        for separator in ("\n", " "):
            text, marker = wrapped_body("everyposition", 1200, separator)
            for position in sorted(y.OUTPUT_POSITIONS):
                with self.subTest(separator=separator, position=position):
                    screened = y.screen(text, (), position=position)
                    self.assertFalse(screened.kept)
                    self.assertTrue(absent(marker, screened.text))

    def test_a_wrapped_key_is_withheld(self):
        for separator in ("\n", " "):
            text, marker = wrapped_body("wrappedkey", 260, separator)
            with self.subTest(separator=separator):
                result = T.project({"outer": {text: 1}, "name": "ha"})
                rendered = serialized(result.envelope)
                self.assertTrue(absent(marker, rendered))
                self.assertIn("ha", result.envelope["value"].values())
                self.assertGreater(result.redacted_field_count, 0)

    async def test_ordinary_multiline_prose_stays_visible(self):
        prose = ordinary_prose()
        for text in prose:
            self.assertGreater(len(text), 120)
        # More than 200 characters once whitespace is removed, but punctuation
        # breaks every run well below 200.
        self.assertGreater(len(re.sub(r"\s", "", prose[0])), 200)
        for text in prose:
            for position in ("value", "list_item"):
                with self.subTest(text=text[:20], position=position):
                    self.assertTrue(y.screen(text, (), position=position).kept)
        for version in T.VERSIONS:
            async with sdk_gateway(version) as (peer, run):
                value = {"description": prose[0], "template": prose[1], "notes": [prose[2], prose[0]]}
                peer.reply = lambda args, v=value: T.ok(T.payload(v, args["yaml_path"]))
                result = await run({"yaml_path": "sensor.value"})
                with self.subTest(version=version):
                    self.assertTrue(result["success"], result)
                    self.assertEqual(result["data"]["value"], value)
                    self.assertEqual(result["data"]["redaction"]["redacted_field_count"], 0)

    def test_unpunctuated_words_past_the_bound_are_withheld(self):
        # Disclosed fail-safe boundary of A1: 200 or more letters and digits
        # with only whitespace between them read as a wrapped body, so such a
        # text is withheld. One punctuation mark inside each 200 keeps it.
        words = ("turn on the hallway light when motion is seen after sunset and keep "
                 "it on for ten minutes unless the door opens again ").split()
        text = "\n".join(" ".join(words) for _ in range(3))
        self.assertGreaterEqual(len(re.sub(r"\s", "", text)), 200)
        self.assertEqual(y.screen(text, (), position="value").category, "opaque_blob")
        punctuated = "\n".join(" ".join(words) + "." for _ in range(3))
        self.assertTrue(y.screen(punctuated, (), position="value").kept)


# ---------------------------------------------------------------------------
# The rule x position matrix.


class MatrixTests(unittest.IsolatedAsyncioTestCase):
    def test_every_rule_fires_on_its_case_and_the_needle_alone_is_kept(self):
        for rule in RULES:
            with self.subTest(rule=rule.name):
                self.assertTrue(rule.fires(rule.text), rule.name)
                self.assertTrue(rule.needle.casefold() in rule.text.casefold() or
                                rule.covers == "known_secret_casefold")
                for position in sorted(y.OUTPUT_POSITIONS):
                    # Control: without the rule's trigger the needle is shown.
                    self.assertTrue(y.screen(rule.needle, (), position=position).kept,
                                    (rule.name, position))

    def test_unit_matrix(self):
        cases = 0
        for rule in RULES:
            for position in sorted(y.OUTPUT_POSITIONS):
                with self.subTest(rule=rule.name, position=position):
                    screened = y.screen(rule.text, (KNOWN,), position=position,
                                        path=unit_path(rule, position))
                    cases += 1
                    if (rule.name, position) in D12_RESIDUALS:
                        self.assertTrue(screened.kept)
                        continue
                    self.assertFalse(screened.kept)
                    self.assertTrue(absent(rule.needle, screened.text))
                    self.assertIn(screened.category, y.REDACTION_CATEGORIES)
        self.assertEqual(cases, len(RULES) * len(y.OUTPUT_POSITIONS))

    async def _matrix(self, run, set_reply):
        cases, outcomes = 0, {}
        for rule in RULES:
            for position in sorted(y.OUTPUT_POSITIONS):
                with self.subTest(rule=rule.name, position=position), ExitStack() as stack:
                    injection = prepare(stack, rule, position)
                    set_reply(lambda args, i=injection: reply(i, args))
                    result = await run({"yaml_path": injection.yaml_path})
                    text = serialized(result)
                    cases += 1
                    if (rule.name, position) in D12_RESIDUALS:
                        self.assertIs(result.get("success"), True)
                        self.assertIn(rule.needle, text)
                        continue
                    self.assertTrue(absent(rule.needle, text), (rule.name, position))
                    if position != "status":
                        # A planted status is screened in place (a string or,
                        # with a key context, a mapping), never the needle.
                        self.assertIn(result.get("success"), (True, False))
                    outcomes.setdefault(position, set()).add(result.get("success") is True)
        self.assertEqual(cases, len(RULES) * len(y.OUTPUT_POSITIONS))
        return outcomes

    async def test_matrix_over_real_mcp_sessions_8_5_0(self):
        async with sdk_gateway("8.5.0") as (peer, run):
            def set_reply(function):
                peer.reply = function
            outcomes = await self._matrix(run, set_reply)
        self.assert_useful(outcomes)

    async def test_matrix_through_the_gateway_8_4_3(self):
        gateway, transport = await memory_gateway("8.4.3")

        async def run(arguments):
            return await T.call(gateway, TOOL, arguments)

        def set_reply(function):
            transport.respond = lambda tool, args: function(args)
        outcomes = await self._matrix(run, set_reply)
        self.assert_useful(outcomes)

    def assert_useful(self, outcomes):
        # Redaction is not refusal: a planted string on a success path is
        # replaced and the read still succeeds.
        for position in ("value", "list_item", "key", "reference_name", "include_path",
                         "envelope", "limitation", "redaction", "operation", "summary",
                         "warning", "metadata", "timing", "request_id"):
            self.assertIn(True, outcomes[position], position)


# ---------------------------------------------------------------------------
# Coverage: the matrix dimensions equal what the code enumerates.


def code_positions() -> set[str]:
    """Every position literal the reader passes to ``screen`` or a path
    projection, and every field position."""

    tree = ast.parse(inspect.getsource(y))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "position" and isinstance(
                node.value, ast.Constant):
            found.add(node.value.value)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) in {
                "project_path", "path_segment_safe"} and len(node.args) >= 3:
            if isinstance(node.args[2], ast.Constant):
                found.add(node.args[2].value)
        if isinstance(node, ast.arg) and node.arg == "position":
            pass
    return found | set(y.FIELD_POSITIONS.values()) | set(y._PATH_POSITIONS) | set(
        y._KEY_POSITIONS) | set(y._VALUE_POSITIONS)


# The positions every matrix case runs in (each has an end-to-end injector).
MATRIX_POSITIONS = tuple(sorted(y.OUTPUT_POSITIONS))


class CoverageTests(unittest.TestCase):
    def test_positions_from_the_code_are_all_in_the_matrix(self):
        self.assertLessEqual(code_positions(), set(y.OUTPUT_POSITIONS))
        self.assertEqual(set(INJECTORS), set(y.OUTPUT_POSITIONS))
        self.assertEqual(len(INJECTORS), 21)

    def test_every_gateway_response_field_has_a_position(self):
        for model in (G.SuccessResponse, G.FailureResponse):
            for field in dataclass_fields(model):
                with self.subTest(model=model.__name__, field=field.name):
                    self.assertIn(field.name, y.FIELD_POSITIONS)
        envelope = T.project({"a": 1}).envelope
        self.assertLessEqual(set(envelope) - {"value"}, set(y.FIELD_POSITIONS))

    def test_rules_from_the_code_are_all_in_the_matrix(self):
        # M1: rules come from the registry that executes them, positions
        # from OUTPUT_POSITIONS and FIELD_POSITIONS; every rule x position
        # is a matrix cell, and every matrix cell names a registered rule.
        positions = set(y.OUTPUT_POSITIONS) | set(y.FIELD_POSITIONS.values())
        required = {(rule_id, position) for rule_id in expected_rule_ids()
                    for position in positions}
        cells = {(rule.covers, position) for rule in RULES for position in MATRIX_POSITIONS}
        self.assertEqual(required - cells, set(), "registered rule x position without a case")
        self.assertEqual(cells - required, set(), "matrix case for an unregistered rule")
        self.assertLessEqual(D12_RESIDUALS, {(rule.name, p) for rule in RULES for p in positions})
        self.assertLessEqual({("opaque_blob_wrapped", p) for p in positions}, cells)
        self.assertEqual(len(SEGMENT_RULE_NAMES), 12)
        self.assertEqual(len(SERIALIZED_CATEGORIES), 8)
        self.assertEqual(len({rule.name for rule in RULES}), len(RULES))

    def test_the_registry_is_the_execution_path(self):
        ids = y.scanner_rule_ids()
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, tuple(rule.rule_id for rule in y.SCANNER_REGISTRY))
        self.assertFalse(hasattr(y, "SCANNER_RULES"), "no list independent of the registry")
        self.assertIn("opaque_blob_wrapped", ids)
        self.assertLess(ids.index("canonicalize"), ids.index("marker_lookalike"))
        with self.assertRaises(ValueError):
            y._rule(ids[0])(lambda scan: None)
        self.assertEqual(y.scanner_rule_ids(), ids)

    def _coverage_failures(self) -> int:
        result = unittest.TestResult()
        CoverageTests("test_rules_from_the_code_are_all_in_the_matrix").run(result)
        self.assertEqual(result.errors, [])
        return len(result.failures)

    def test_proof_an_unmatrixed_registered_rule_fails_coverage(self):
        self.assertEqual(self._coverage_failures(), 0)
        trigger = syn.needle("unmatrixedrule")
        dummy = y.ScannerRule(
            "test_only_unmatrixed",
            lambda scan: (y.CREDENTIAL_MARKER, "credential_key") if trigger in scan.text else None)
        with patch.object(y, "SCANNER_REGISTRY", y.SCANNER_REGISTRY + (dummy,)):
            # The dummy really executes: its trigger is withheld everywhere.
            for position in sorted(y.OUTPUT_POSITIONS):
                self.assertFalse(y.screen(trigger, (), position=position).kept, position)
            self.assertEqual(y._finding(trigger, (), "value", ()).rule, dummy.rule_id)
            self.assertEqual(self._coverage_failures(), 1)
        self.assertTrue(y.screen(trigger, (), position="value").kept)
        self.assertEqual(self._coverage_failures(), 0)

    def test_proof_a_matrixed_rule_removed_from_execution_fails_coverage(self):
        pem_text = next(rule.text for rule in RULES if rule.name == "pem")
        self.assertEqual(y._finding(pem_text, (), "value", ()).rule, "pem")
        without = tuple(rule for rule in y.SCANNER_REGISTRY if rule.rule_id != "pem")
        with patch.object(y, "SCANNER_REGISTRY", without):
            finding = y._finding(pem_text, (), "value", ())
            self.assertTrue(finding is None or finding.rule != "pem")
            self.assertEqual(self._coverage_failures(), 1)
        self.assertEqual(self._coverage_failures(), 0)

    def test_every_withheld_cell_is_decided_by_a_registered_rule(self):
        registered = set(y.scanner_rule_ids())
        deciders: dict[str, set[str]] = {}
        for rule in RULES:
            for position in sorted(y.OUTPUT_POSITIONS):
                with self.subTest(rule=rule.name, position=position):
                    finding = y._finding(rule.text, (KNOWN,), position,
                                         unit_path(rule, position))
                    screened = y.screen(rule.text, (KNOWN,), position=position,
                                        path=unit_path(rule, position))
                    self.assertEqual(finding is None, screened.kept)
                    if finding is None:
                        self.assertIn((rule.name, position), D12_RESIDUALS)
                        continue
                    self.assertIn(finding.rule, registered)
                    self.assertEqual(finding.category, screened.category)
                    deciders.setdefault(registered_rule(rule.covers), set()).add(finding.rule)
        # Each registered rule decides at least one of its own cases.
        for rule_id in registered:
            with self.subTest(rule_id=rule_id):
                self.assertIn(rule_id, deciders.get(rule_id, set()))

    def test_the_executor_holds_no_check_outside_the_registry(self):
        # A check written inline in the executor or in screen (bypassing the
        # registry) changes these exact shapes.
        def shape(function):
            node = ast.parse(inspect.getsource(function)).body[0]
            body = node.body[1:] if isinstance(node.body[0], ast.Expr) else node.body
            names = {n.id for statement in body for n in ast.walk(statement)
                     if isinstance(n, ast.Name)}
            kinds = [type(n).__name__ for statement in body for n in ast.walk(statement)
                     if isinstance(n, (ast.If, ast.Return, ast.For, ast.While, ast.Try,
                                       ast.With, ast.IfExp, ast.BoolOp, ast.Compare))]
            return names, Counter(kinds)

        names, kinds = shape(y._finding)
        self.assertEqual(names, {
            "Finding", "SCANNER_REGISTRY", "_Scan", "attrgetter", "category", "context",
            "form", "found", "group", "groupby", "known_secrets", "path", "per_form",
            "position", "rule", "rules", "scan", "text", "tuple"})
        self.assertEqual(kinds, Counter({"If": 3, "Return": 3, "For": 4, "IfExp": 1,
                                         "Compare": 3}))
        names, kinds = shape(y.screen)
        self.assertEqual(names, {
            "OUTPUT_POSITIONS", "PATH_MASK", "Screened", "TypeError", "ValueError",
            "_PATH_POSITIONS", "_finding", "_projectable", "category", "finding",
            "known_secrets", "path", "position", "replacement", "str", "text", "tuple",
            "type", "vocabulary"})
        self.assertEqual(kinds, Counter({"If": 5, "Return": 3, "Compare": 6, "BoolOp": 1}))

    def test_unknown_position_and_non_text_are_refused(self):
        with self.assertRaises(ValueError):
            y.screen("x", (), position="unlisted")
        with self.assertRaises(TypeError):
            y.screen(1, (), position="value")
        with self.assertRaises(y.YamlReadRefusal):
            y.seal({"unlisted_field": "x"}, ())


# ---------------------------------------------------------------------------
# The uniform entry: every emitted string is a screen output.


def walk_strings(value, field, out):
    if type(value) is str:
        out.append((field, value))
    elif type(value) is dict:
        for key, child in value.items():
            out.append(("key", key))
            walk_strings(child, field, out)
    elif type(value) is list:
        for child in value:
            walk_strings(child, field, out)


class UniformEntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_emitted_string_is_a_screen_output(self):
        gateway, transport = await memory_gateway("8.5.0")
        upstreams = [
            ("success", "sensor.value", {"a": 1, "name": "kitchen", "password": "!secret db_pass",
                                         "inc": "!include extra.yaml", "items": ["x", {"k": "y"}],
                                         "pem": syn.pem("CERTIFICATE", "z"), "x": KNOWN}),
            ("refused", "sensor." + KNOWN, "ordinary"),
            ("failure", "sensor.value", None),
        ]
        for name, path, value in upstreams:
            with self.subTest(case=name):
                if value is None:
                    transport.respond = lambda tool, args: reply(Injection(failure=True), args)
                else:
                    transport.respond = lambda tool, args, v=value: T.ok(T.payload(v, args["yaml_path"]))
                calls = []
                real = y.screen

                def spy(text, known, *, position, path=(), vocabulary=frozenset()):
                    result = real(text, known, position=position, path=path, vocabulary=vocabulary)
                    calls.append((position, result.text))
                    return result

                with patch.object(y, "screen", spy):
                    result = await T.call(gateway, TOOL, {"yaml_path": path})
                outputs = set(calls)
                emitted = []
                for field, child in result.items():
                    emitted.append(("key", field))
                    if field == "data":
                        for inner, grandchild in child.items():
                            emitted.append(("key", inner))
                            if inner != "value":
                                walk_strings(grandchild, y.FIELD_POSITIONS[inner], emitted)
                        walk_strings(child["value"], "value", emitted)
                    else:
                        walk_strings(child, y.FIELD_POSITIONS[field], emitted)
                value_tree = {"value", "list_item", "reference_name"}
                for position, text in emitted:
                    with self.subTest(case=name, position=position, text=text[:24]):
                        if position == "value":
                            self.assertTrue(
                                any((p, text) in outputs for p in value_tree)
                                or text in VALUE_TREE_CONSTANTS, text)
                        elif position == "key":
                            self.assertTrue(("key", text) in outputs or text in RENDERED_KEYS, text)
                        else:
                            self.assertIn((position, text), outputs)


VALUE_TREE_CONSTANTS = frozenset({
    y.CREDENTIAL_MARKER, y.ENCODING_MARKER, y.CONNECTION_STRING_MARKER,
    y.REFERENCE_MALFORMED_MARKER, y.COLLISION_MARKER, y.INCLUDE_MALFORMED_MARKER,
    y.DEPTH_MARKER, y.MARKER_COLLISION,
})
RENDERED_KEYS = frozenset({"secret_ref", "env_var_ref", "default_redacted"})


# ---------------------------------------------------------------------------
# Runtime-built synthetic secrets: the committed-file scan.


class CommittedFileScanTests(unittest.TestCase):
    def test_no_committed_hamcp162_file_has_secret_shaped_text(self):
        files = syn.scanned_files()
        self.assertGreater(len(files), 50)
        self.assertIn(ROOT / "tests/test_hamcp162_h4_pipeline.py", files)
        self.assertIn(ROOT / "docs/evidence/hamcp162/fixtures/cases.json", files)
        self.assertIn(ROOT / "docs/evidence/hamcp162/captures/cases-8.5.0.json", files)
        self.assertIn(ROOT / "docs/evidence/hamcp162-mutations/run_mutations.py", files)
        self.assertEqual(syn.scan(files), [])

    def test_the_scan_uses_the_repository_patterns_and_detects_built_secrets(self):
        patterns = syn.repository_patterns()
        self.assertEqual(len(patterns), 4)
        built = {
            "repo_pem_private": syn.private_key_pem("x"),
            "pem_any": syn.pem("CERTIFICATE", "x", lower=True),
            "jwt": syn.jwt("scan"),
            "opaque": syn.opaque(120),
            "url_userinfo": syn.url("mysql", "u", "p" * 4, "db.invalid"),
            "bearer": syn.bearer("x" * 16),
            "odbc_pwd": syn.pwd("x" * 8),
            "webhook": syn.webhook_path("x" * 12),
        }
        for name, text in built.items():
            with self.subTest(pattern=name):
                self.assertTrue(syn.codec.PATTERNS[name].search(text), name)
        # The repository's own private-key pattern matches the built header.
        self.assertTrue(any(p.search(built["repo_pem_private"]) for p in patterns.values()))

    def test_the_evidence_codec_round_trips_and_verifies(self):
        self.assertEqual(syn.codec.verify(), [])
        decoded = syn.codec._listing("DECODED.sha256")
        self.assertGreaterEqual(len(decoded), 10)
        for relative in decoded:
            with self.subTest(file=relative):
                raw = (syn.EVIDENCE / relative).read_text(encoding="utf-8")
                self.assertIn(syn.codec.SENTINEL, raw)
                self.assertEqual(syn.codec.findings(raw), [])
                original = syn.codec.read_text(syn.EVIDENCE / relative)
                self.assertNotIn(syn.codec.SENTINEL, original)
                self.assertTrue(syn.codec.findings(original))
                self.assertEqual(syn.codec.decode(syn.codec.encode(original)), original)

    def test_a_tampered_encoding_is_refused(self):
        relative = next(iter(syn.codec._listing("DECODED.sha256")))
        path = (syn.EVIDENCE / relative).resolve()
        real = Path.read_bytes

        def read_bytes(self):
            data = real(self)
            return data + b"x" if self == path else data

        with patch.object(Path, "read_bytes", read_bytes):
            with self.assertRaises(ValueError):
                syn.codec.read_text(path)
            problems = syn.codec.verify()
        self.assertIn("manifest mismatch: " + relative, problems)
        self.assertIn("decoded evidence hash mismatch: " + relative, problems)
        self.assertEqual(syn.codec.verify(), [])


if __name__ == "__main__":
    unittest.main()
