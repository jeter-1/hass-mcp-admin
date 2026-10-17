"""HAMCP-162: the configuration.yaml reader (``ha_config_get_yaml``).

Design ``docs/HAMCP_162_YAML_READER_DESIGN.md`` §8.1 groups 1-9 and 14, the
438 assertions of the revision-4 delta probe set, and the D1 capture evidence
(``docs/evidence/hamcp162``) replayed at ha-mcp 8.4.3 and 8.5.0.

Synthetic peers only: an in-memory transport, or the real pinned MCP
ClientSession over in-memory streams. No live Home Assistant, no deployed
endpoint, no real credential. Secret and redaction assertions run on the
final wrapper output (tool result, error, warnings, limitations and the
audit record), never only on the shared sanitizer.
"""
import __future__
import asyncio
import base64
from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
from dataclasses import replace
import inspect
import json
from pathlib import Path
import sys
import tempfile
import textwrap
from typing import Any
import unicodedata
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, quote, unquote
import uuid

import anyio
from mcp import types
from mcp.server.fastmcp import FastMCP
from mcp.shared.message import SessionMessage

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from ha_mcp_engineering import sanitization  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.capabilities import (  # noqa: E402
    replace_dynamic_upstream_capabilities,
)
from ha_mcp_engineering.clients import upstream_read  # noqa: E402
from ha_mcp_engineering.providers import upstream_yaml_reader as y  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402
from ha_mcp_engineering.sanitization import (  # noqa: E402
    EngineeringMarker,
    MARKER_COLLISION,
    ReferenceMarker,
    ReferenceMarkerRegistry,
    sanitize_untrusted_data,
)
from test_hamcp129_platform_reads import call, ok, refused  # noqa: E402
from test_hamcp129_platform_reads import Transport  # noqa: E402
from test_ha_mcp_production_readmission import _settings  # noqa: E402
from test_readonly_upstream_gateway import SECRET as ACCESS_SECRET  # noqa: E402
from test_readonly_upstream_gateway import settings as access_settings  # noqa: E402

EVIDENCE = ROOT / "docs/evidence/hamcp162"
VERSIONS = ("8.4.3", "8.5.0")
TOOL = y.TOOL_NAME
SYN = "SYNTHETIC_VALUE_162"
# Split so the repository credential-pattern scan does not match a synthetic header.
PEM_KEY_BEGIN = "-----BEGIN " + "PRIVATE KEY-----"
CRED = y.CREDENTIAL_MARKER
ENC = y.ENCODING_MARKER
CONNSTR = y.CONNECTION_STRING_MARKER
D1_SECRET = r"Zq7v\d{4}Xw"
LABEL = r"^\[REDACTED:[a-z_]+\]$"


def enc(value: str, times: int) -> str:
    for _ in range(times):
        value = quote(value, safe="")
    return value


def forms(secret: str) -> tuple[str, ...]:
    """A synthetic credential raw and 1x, 2x and 3x percent-encoded."""

    return tuple(dict.fromkeys(enc(secret, n) for n in range(4)))


def hidden(value, secret=SYN) -> bool:
    text = json.dumps(value, ensure_ascii=False)
    return secret not in text and secret.lower() not in text


def assert_absent(test, text: str, secret: str) -> None:
    for form in forms(secret):
        test.assertNotIn(form, text)
        test.assertNotIn(form.lower(), text)


def payload(parsed, yaml_path, *, found=True, **overrides):
    value = {
        "success": True,
        "yaml_path": yaml_path,
        "matches": (
            [{"file": "configuration.yaml", "yaml_path": yaml_path, "parsed": parsed}]
            if found else []
        ),
        "count": 1 if found else 0,
        "files_searched": 1,
        "files_unreadable": 0,
    }
    value.update(overrides)
    return value


def project(parsed, path="sensor.value", **kwargs):
    return y.project_success(ok(payload(parsed, path)), path, **kwargs)


def out(parsed, path="sensor.value"):
    return project(parsed, path).envelope["value"]


def pipeline(value, path=("sensor", "value")):
    """The rule model's pipeline(v, path), on the real wrapper projection."""

    return out(value, ".".join(path))


def catalog(version):
    return json.loads((EVIDENCE / f"captures/catalog-{version}-A.json").read_text())["tools"]


def d1_cases(version):
    for suffix in ("", "-supp", "-supp2"):
        data = json.loads((EVIDENCE / f"captures/cases-{version}{suffix}.json").read_text())
        for case in data["cases"]:
            if case.get("tool") == TOOL:
                yield data["label"], case


def d1_result(version, case_id):
    for _label, case in d1_cases(version):
        if case["id"] == case_id:
            return case["response"]["message"]["result"]
    raise KeyError(case_id)


@contextmanager
def mutated(owner, name, old, new):
    """Replace one exact source fragment of ``owner.name`` for the block.

    The mutant is compiled from the shipped source, so a mutation always
    names real code; a fragment that no longer matches exactly once fails.
    """

    function = inspect.getattr_static(owner, name)
    source = textwrap.dedent(inspect.getsource(function))
    if source.count(old) != 1:
        raise AssertionError(f"mutation fragment not unique in {name}: {old!r}")
    namespace: dict = {}
    module = sys.modules[function.__module__]
    code = compile(
        source.replace(old, new), f"<mutant {name}>", "exec",
        flags=__future__.annotations.compiler_flag, dont_inherit=True,
    )
    exec(code, vars(module), namespace)  # noqa: S102 - test-only mutant
    with patch.object(owner, name, namespace[name]):
        yield


# Mutations of rule A used by the probe port (rule model names).
def rule_a_mutation(name):
    if name == "once":
        return patch.object(y, "MAX_DECODE_ROUNDS", 1)
    if name == "bound3":
        return patch.object(y, "MAX_DECODE_ROUNDS", 3)
    if name == "bound5":
        return patch.object(y, "MAX_DECODE_ROUNDS", 5)
    if name == "unbounded":
        return patch.object(y, "MAX_DECODE_ROUNDS", 40)
    if name == "no_stability":
        return mutated(y, "canonical_forms", "    if _ESCAPE.search(text):\n", "    if False:\n")
    if name == "lenient":
        return mutated(y, "canonical_forms", "if _STRAY_PERCENT.search(value):", "if False:")
    if name == "plus":
        return mutated(
            y, "canonical_forms", "        value = decoded\n",
            "        value = decoded.replace('+', ' ')\n",
        )
    if name == "raw":
        return mutated(y._L4, "_string", "if credential_text(text):", "if credential_text(value):")
    if name == "emit_canonical":
        return mutated(
            y._L4, "_string",
            "    # Visible values are emitted raw, never in canonical form.\n    return value",
            "    return text",
        )
    raise KeyError(name)


# ---------------------------------------------------------------------------
# Gateway harnesses.


async def yaml_gateway(version="8.5.0", tools=None, *, response_size_limit=None, core_runtime=None):
    transport = Transport(catalog(version) if tools is None else tools, version)
    transport.respond = lambda tool, args: ok(payload({"purge_keep_days": 10}, args["yaml_path"]))
    configured = replace(
        _settings("synthetic-no-key"), ha_mcp_release_registry_enabled=False,
        upstream_dashboard_mcp_url="http://synthetic-provider:9583/synthetic-path-credential/mcp",
    )
    if response_size_limit is not None:
        configured = replace(configured, response_size_limit=response_size_limit)
    gateway = UpstreamReadGateway()
    kwargs = {"core_runtime": core_runtime} if core_runtime is not None else {}
    gateway.configure(configured, transport=transport, **kwargs)
    await gateway.initialize(FastMCP(f"synthetic-hamcp162-{version}"))
    return gateway, transport


class YamlPeer:
    """Synthetic ha-mcp peer over the real ClientSession framing."""

    def __init__(self, tools, version):
        self.tools, self.version = tools, version
        self.calls = []
        self.reply = None

    async def serve(self, incoming, outgoing):
        async with incoming, outgoing:
            async for message in incoming:
                request = message.message.root
                if not isinstance(request, types.JSONRPCRequest):
                    continue
                if request.method == "initialize":
                    result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                              "serverInfo": {"name": "ha-mcp", "version": self.version}}
                elif request.method == "tools/list":
                    result = {"tools": self.tools}
                elif request.method == "tools/call":
                    name, args = request.params["name"], request.params["arguments"]
                    self.calls.append((name, deepcopy(args)))
                    if name != TOOL:
                        raise AssertionError("Unexpected synthetic dispatch")
                    result = deepcopy(self.reply(args))
                else:
                    raise AssertionError("Unexpected MCP method")
                await outgoing.send(SessionMessage(types.JSONRPCMessage(
                    types.JSONRPCResponse(jsonrpc="2.0", id=request.id, result=result))))

    @asynccontextmanager
    async def connect(self, unused, **kwargs):
        assert unused == ""
        session = "synthetic-session-" + uuid.uuid4().hex
        send_to_client, read = anyio.create_memory_object_stream(10)
        write, read_from_client = anyio.create_memory_object_stream(10)
        async with anyio.create_task_group() as group:
            group.start_soon(self.serve, read_from_client, send_to_client)
            try:
                yield read, write, lambda: session
            finally:
                group.cancel_scope.cancel()


# ---------------------------------------------------------------------------
# Group 4 (part): the 438 assertions of the revision-4 delta probe set.


class ProbePortTests(unittest.TestCase):
    """Port of ``pr260-3391172/probes.py`` through the revision-5 rule model.

    Provenance: ``.artifacts/reviews/pr260-3391172/probes.py`` (438
    assertions), ported in ``~/tmp/hamcp162-r5/rule_model.py`` part 1. The
    boundary loop and the ODBC line below are verbatim from
    ``pr260-042f254/boundary_probes.py`` and ``pr260-e6fb813/probes.py:82``.
    Each port entry evaluates the original predicate on the real wrapper
    output; the 13 deliberate revision-5 flips (design §15) assert that the
    predicate is now false.
    """

    def setUp(self):
        self.port = []

    def check(self, name, holds, flip=None):
        self.port.append((name, bool(holds), flip))
        with self.subTest(name=name, flip=flip):
            if flip is None:
                self.assertTrue(holds, name)
            else:
                self.assertFalse(holds, "expected deliberate flip: " + name)

    def test_the_438_port(self):
        rules = {"encode": enc, "SYN": SYN}
        fixtures = []
        # Verbatim boundary loop (pr260-042f254/boundary_probes.py).
        for depth in range(1,7):
            fixtures.append(('keyword',depth,rules['encode']('Server=db;Uid=u;Pwd='+rules['SYN'],depth),depth>4))
            fixtures.append(('encoded_uri',depth,rules['encode']('mysql://u:'+rules['SYN']+'@db/ha',depth),True))
        for kind, depth, raw, _ in fixtures:
            expected = ENC if depth > 4 else CONNSTR if kind == "keyword" else "mysql://[REDACTED:connection_uri]"
            for path in (("sensor", "value"), ("recorder", "db_url")):
                self.check(f"boundary {kind} {depth} {path}", pipeline(raw, path) == expected)

        SYN_ = SYN  # noqa: N806 - the verbatim line names SYN
        # Verbatim ODBC probe (pr260-e6fb813/probes.py:82).
        odbc='mssql+pyodbc:///?odbc_connect='+quote('DRIVER={Synthetic Driver};SERVER=db.invalid;DATABASE=ha;UID=synthetic;PWD='+SYN_,safe='')  # noqa: E231,E501
        historical = [odbc]
        for scheme in ("mysql", "mysql+pymysql", "postgresql", "postgresql+psycopg2", "mssql+pyodbc", "MYSQL+PYMYSQL"):
            historical.append(f"{scheme}://synthetic_user:{SYN}@db.invalid/ha")
        historical += [
            f"mysql://user:p%40ss%3A{SYN}@db.invalid/ha", f"mysql://db.invalid/ha?password={SYN}",
            f"mysql://db.invalid/ha?%70assword={SYN}", f"mysql://db.invalid/ha?sslpassword={SYN}",
            f"mysql://db.invalid/ha?x=1;password={SYN}", f"mysql://user:a/b{SYN}@db.invalid/ha",
            f"mysql://user:a@b{SYN}@db.invalid/ha", f"mysql://user:{SYN}@db.invalid/ha#fragment",
            f"mysql://user:{SYN}%ZZ@db.invalid/ha", f"DRIVER=fixture;PWD={SYN}",
            f"host=db.invalid password={SYN}", f"prefix mysql://user:{SYN}@db.invalid/ha",
        ]
        for i, raw in enumerate(historical):
            for depth in range(7):
                for path in (("sensor", "value"), ("recorder", "db_url")):
                    self.check(f"historical {i} depth{depth} {path}", hidden(pipeline(enc(raw, depth), path)))
        for depth in range(7):
            for name in ("odbc_connect", "driver", "application_name", "TrUsTeD_Connection"):
                raw = "mssql+pyodbc://db/ha?" + name + "=" + enc("X;UID=u;PWD=" + SYN, depth)
                self.check(f"prior nested {name} {depth}", hidden(pipeline(raw)))

        self.check("4 harmless raw", pipeline(enc("Living Room", 4)) == enc("Living Room", 4))
        self.check("5 harmless redacted", pipeline(enc("Living Room", 5)) == ENC)
        self.check("4 Pwd exact marker", pipeline(enc("Pwd=S", 4)) == CONNSTR)
        for raw in ("%41%", "%G1%41", "%41 50% off", "%FF", "%C0%AE", "a%00b", "a%7fb", "a%C2%85b"):
            self.check("invalid/ambiguous " + raw, pipeline(raw) == ENC)
        for raw in ("50% off", "100%", "%G1", "unknown://host/path",
                    "mysql://db.local:3306/ha?charset=utf8mb4",
                    "Server=db;Database=ha;Trusted_Connection=yes"):
            self.check("ordinary usefulness " + raw, pipeline(raw) == raw)
        self.check("unknown scheme under B",
                   pipeline("unknown://host/path", ("recorder", "db_url")) == "unknown://[REDACTED:connection_uri]")
        self.check("mixed plus preserved", pipeline(enc("A+B C", 2)) == enc("A+B C", 2))
        for name in ("db_url", "DB_URL", "dbUrl", "Db-Url", "db%5Fur%6C", "conn_str", "DATABASE",
                     "connection_string", "security"):
            for child in (SYN, 42, True, None, {"value": SYN}, [SYN]):
                self.check("B nested " + name + " " + type(child).__name__,
                           hidden(pipeline({"nested": [{name: child}]})))
        for raw in ("Pwd%3d" + SYN, "p%77%64=" + SYN, "%50wd=" + SYN, "mysql%3a//u:" + SYN + "%40db/ha",
                    "mysql://u:" + SYN + "%2540db/ha"):
            self.check("mixed encoding " + raw[:12], hidden(pipeline(raw)))

        sqlite_flip = 'R11: ".." segment refused; value also contains the word "secret"'
        for raw, visible, flip in [
            ("sqlite:////config/home-assistant_v2.db", True, None),
            ("sqlite:///../../secrets.yaml", True, sqlite_flip),
            ("sqlite:////config/../secrets.yaml", True, sqlite_flip),
            ("sqlite:///./x", True, None), ("sqlite:///x?mode=ro", False, None),
            ("sqlite:///x;Pwd=" + SYN, False, None), ("sqlite:///x@host", False, None),
            ("sqlite:///x%2Fy", False, None), ("sqlite:///x%252Fy", False, None),
            ("sqlite:///../%2e%2e/x", False, None), ("sqlite:///caf\u00e9.db", False, None),
            ("sqlite:///x\uff0fy", False, None), ("sqlite:///x\\..\\y", False, None),
            ("sqlite:///x\n", False, None), ("SQLITE:///x", False, None),
            ("sqlite:///:memory:", False, None),
        ]:
            result = pipeline(raw, ("recorder", "db_url"))
            self.check("sqlite " + repr(raw), (result == raw) == visible, flip)

        r2f = "R2 corrected by C' (substring deny; no == exemption)"
        r10f = "R10 corrected: canonical keys before credential classification"
        d12f = "D12 class now caught by C'"
        cases = [
            ("encoded credential key", {"%70assword": SYN}, r10f),
            ("encoded credential ancestor", {"%63redentials": [{"value": SYN}]}, r10f),
            ("two-level encoded key", {"%2570assword": {"inner": SYN}}, r10f),
            ("C2 leading-equals password", "Server=db;Uid=u;Pwd==" + SYN, r2f),
            ("C2 leading-equals query", "mysql://db/ha?pwd==" + SYN, r2f),
            ("percent-encoded leading-equals", enc("Pwd==" + SYN, 3), r2f),
            ("innocent raw key", {"foo": SYN}, None),
            ("split across keys", {"parts": ["Pw", "d=", SYN]}, None),
            ("short base64", base64.b64encode(("Pwd=" + SYN).encode()).decode(), None),
            ("HTML equals", "Pwd&#61;" + SYN, d12f),
            ("unicode escape equals", r"Pwd\u003d" + SYN, d12f),
            ("unicode name lookalike", "P\u0441d=" + SYN, None),
            ("unicode delimiter", "Pwd\uff1d" + SYN, d12f),
            ("unicode connection key", {"db_ur\u2113": SYN}, d12f),
            ("JSON payload", json.dumps({"pwd": SYN}), d12f),
            ("unlisted assignment", "login=" + SYN, None),
        ]
        for name, raw, flip in cases:
            result = pipeline(raw)
            retained = (result == raw) if name == "short base64" else not hidden(result)
            self.check("adversarial observed " + name, retained, flip)
        self.check("query parser confirms equals-leading value", parse_qs("pwd==" + SYN)["pwd"] == ["=" + SYN])

        controls = [(enc("Living Room", 4), enc("Living Room", 4)), (enc("Living Room", 5), ENC),
                    (enc("Pwd=S", 4), CONNSTR), (enc("Pwd=S", 2), CONNSTR), ("%41%", ENC)]
        for name in ("once", "bound3", "bound5", "no_stability", "unbounded", "raw", "emit_canonical", "lenient"):
            with rule_a_mutation(name):
                failed = sum(pipeline(value) != expected for value, expected in controls)
            self.check("mutation caught " + name, failed > 0)
        plain = y.canonical("%41+%42")[0]
        with rule_a_mutation("plus"):
            plus = y.canonical("%41+%42")[0]
        self.check("plus mutation canonical distinction", plain == "a+b" and plus == "a b")

        self.assertEqual(len(self.port), 438)
        self.assertEqual(sum(1 for _, _, flip in self.port if flip), 13)
        # Every mutation is undone: the controls hold again.
        for value, expected in controls:
            self.assertEqual(pipeline(value), expected)


# ---------------------------------------------------------------------------
# Groups 3, 3a and 4: redaction on the final projection.


def secret(tag):
    """A synthetic credential with no credential word, unique per use."""

    return f"SYN{tag}Q7V"


class PathContextTests(unittest.TestCase):
    """Group 3: R1 path-context redaction (final projection, real L5)."""

    def config(self, s):
        return {
            "broker": "mqtt.local", "port": 1883, "username": "synthetic_user",
            "password": s, "credentials": {"username": "u2", "value": s, "id": 7},
            "api_key": s, "token": s, "pin": 1234,
            "devices": [{"name": "a", "password": s}, {"name": "b", "api_key": s, "port": 1}],
            "users": {"credentials": [{"user": "u", "value": s}]},
        }

    def test_parent_keeps_harmless_siblings_and_redacts_credentials(self):
        s = secret("G3A")
        result = project(self.config(s), "mqtt")
        value = result.envelope["value"]
        self.assertEqual(
            {k: value[k] for k in ("broker", "port", "username")},
            {"broker": "mqtt.local", "port": 1883, "username": "synthetic_user"},
        )
        for key in ("password", "credentials", "api_key", "token", "pin"):
            self.assertRegex(value[key], r"^\[REDACTED:[a-z_]+\]$", key)
        self.assertEqual(value["devices"][0]["name"], "a")
        self.assertEqual(value["devices"][1]["name"], "b")
        self.assertEqual(value["devices"][1]["port"], 1)
        self.assertRegex(value["devices"][0]["password"], r"^\[REDACTED:")
        self.assertRegex(value["devices"][1]["api_key"], r"^\[REDACTED:")
        self.assertRegex(value["users"]["credentials"], LABEL)
        assert_absent(self, json.dumps(result.envelope), s)
        self.assertGreaterEqual(result.redacted_field_count, 8)

    def test_scalars_of_every_type_under_sensitive_keys(self):
        for scalar in (1234, 12.5, True, False, None, "", "x"):
            with self.subTest(scalar=scalar):
                value = out({"password": scalar, "pin": scalar, "token": scalar, "broker": "b"}, "x")
                self.assertEqual(value["broker"], "b")
                for key in ("password", "pin", "token"):
                    self.assertRegex(value[key], r"^\[REDACTED:", key)

    def test_innocuous_children_under_sensitive_ancestor_are_withheld_whole(self):
        s = secret("G3B")
        for child in ({"value": s, "id": 1}, [{"value": s}, {"id": 2}]):
            value = out({"credentials": child}, "x")
            self.assertEqual(list(value), ["credentials"])
            self.assertRegex(value["credentials"], LABEL)
            self.assertTrue(hidden(value, s))

    def test_direct_sensitive_selection_with_l3_disabled_returns_only_the_marker(self):
        s = secret("G3C")
        # L4 alone (no L3) still returns only the marker for a direct selection.
        self.assertEqual(out(s, "mqtt.password"), CRED)
        self.assertEqual(out({"value": s, "username": "u"}, "mqtt.credentials"), CRED)
        self.assertEqual(out(1234, "x.pin"), CRED)

    def test_direct_selection_through_the_gateway_with_l3_mutated_off(self):
        async def scenario():
            s = secret("G3D")
            gateway, transport = await yaml_gateway()
            transport.respond = lambda tool, args: ok(payload(s, args["yaml_path"]))
            with mutated(y, "validate_arguments", "raise YamlReadRefusal(PATH_SENSITIVE)", "pass"):
                result = await call(gateway, TOOL, {"yaml_path": "mqtt.password"})
            self.assertTrue(result["success"], result)
            self.assertEqual(result["data"]["value"], CRED)
            assert_absent(self, json.dumps(result), s)
            self.assertEqual(len(transport.sent), 1)

        asyncio.run(scenario())


class ReferenceMarkerTests(unittest.TestCase):
    """Group 3a: R8 reference markers on the final pipeline."""

    def test_secret_and_env_var_references_become_markers(self):
        value = out({
            "password": "!secret mqtt_password", "api_key": "!secret k",
            "broker": "!secret mqtt_host", "token": "!env_var TOK",
        }, "mqtt")
        self.assertEqual(value, {
            "password": {"secret_ref": "mqtt_password"}, "api_key": {"secret_ref": "k"},
            "broker": {"secret_ref": "mqtt_host"}, "token": {"env_var_ref": "TOK"},
        })

    def test_env_var_defaults_never_appear(self):
        for default in ("SYNDEFAULTA1", "multi word SYNDEFAULTB2 default", "line1\nSYNDEFAULTC3\nline3"):
            with self.subTest(default=default):
                result = project({"password": "!env_var MQTT_PW " + default, "x": "!env_var X " + default}, "mqtt")
                self.assertEqual(result.envelope["value"], {
                    "password": {"env_var_ref": "MQTT_PW", "default_redacted": True},
                    "x": {"env_var_ref": "X", "default_redacted": True},
                })
                text = json.dumps(result.envelope)
                for word in default.split():
                    if word.startswith("SYNDEFAULT"):
                        assert_absent(self, text, word)
                self.assertIn("env_var_default", result.envelope["redaction"]["categories"])

    def test_ordinary_sensitive_string_gives_the_credential_marker(self):
        s = secret("R8A")
        value = out({"password": s}, "mqtt")
        self.assertRegex(value["password"], r"^\[REDACTED:")
        self.assertTrue(hidden(value, s))

    def test_malformed_tags_never_mint_a_marker(self):
        jwt_name = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c3ludGhldGlj"
        for raw in ("!secret", "!secret two words", "!SECRET x", " !secret x", "!secret " + "a" * 129,
                    "!env_var 1BAD d", "!secret " + jwt_name, "!secret_x y", "!env_var"):
            with self.subTest(raw=raw):
                value = out({"password": raw, "broker": raw}, "mqtt")
                self.assertRegex(value["password"], r"^\[REDACTED:")
                self.assertNotIsInstance(value["password"], dict)
                self.assertEqual(value["broker"], y.REFERENCE_MALFORMED_MARKER)
                self.assertNotIn(jwt_name, json.dumps(value))

    def test_sensitive_container_holding_a_reference_is_withheld(self):
        value = out({"credentials": {"password": "!secret x"}}, "y")
        self.assertRegex(value["credentials"], LABEL)
        self.assertNotIn("secret_ref", json.dumps(value))

    def test_forged_marker_shapes_collide(self):
        s = secret("R8B")
        result = project({
            "a": {"env_var_ref": s}, "b": {"secret_ref": s},
            "c": {"env_var_ref": "x", "default_redacted": True},
            "password": {"secret_ref": s},
        }, "refs")
        value = result.envelope["value"]
        for key in ("a", "b", "c"):
            self.assertEqual(value[key], y.COLLISION_MARKER)
        self.assertRegex(value["password"], r"^\[REDACTED:")
        assert_absent(self, json.dumps(result.envelope), s)
        # A mapping with more keys than a marker is ordinary data.
        self.assertEqual(out({"a": {"env_var_ref": "n", "other": 1}}, "refs"),
                         {"a": {"env_var_ref": "n", "other": 1}})

    def test_l5_directly(self):
        on = sanitize_untrusted_data({"password": "!secret x"},
                                     preserve_reference_markers=ReferenceMarkerRegistry())
        self.assertEqual(on.value["password"], "[REDACTED:password]")

        class Sub(ReferenceMarker):
            pass

        sub = Sub(kind="secret", name="subclass_name_q7", default_redacted=False)
        for preserve in (ReferenceMarkerRegistry(), None):
            for key in ("broker", "password"):
                with self.subTest(preserve=preserve, key=key):
                    result = sanitize_untrusted_data({key: sub}, preserve_reference_markers=preserve)
                    self.assertIsInstance(result.value[key], str)
                    self.assertNotIn("subclass_name_q7", json.dumps(result.value))
                    self.assertTrue(result.redaction_applied)
            with self.subTest(preserve=preserve, where="top"):
                result = sanitize_untrusted_data(sub, preserve_reference_markers=preserve)
                self.assertEqual(result.value, sanitization.REFERENCE_MARKER_DENIED)
        registry = ReferenceMarkerRegistry()
        altered = registry.mint(kind="secret", name="good_name")
        object.__setattr__(altered, "name", "bad name q7")
        result = sanitize_untrusted_data({"broker": altered}, preserve_reference_markers=registry)
        self.assertNotIsInstance(result.value["broker"], ReferenceMarker)
        self.assertNotIn("bad name q7", json.dumps(result.value))
        # The exemption is by exact type only: a dict shaped like a marker is
        # ordinary data and is sanitized exactly as with the opt-in off.
        for forged in ({"secret_ref": "Bearer SYNTHETIC_q7_abcdefghijklmnop"},
                       {"env_var_ref": {"password": "SYNTHETIC_q7"}, "default_redacted": True},
                       {"secret_ref": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMifQ.c3ludGhldGljc2lnbmF0dXJlX3E3"}):
            for where in ("top", "nested"):
                value = forged if where == "top" else {"broker": forged}
                with self.subTest(forged=forged, where=where):
                    on = sanitize_untrusted_data(value,
                                                 preserve_reference_markers=ReferenceMarkerRegistry())
                    off = sanitize_untrusted_data(value)
                    self.assertEqual(on.value, off.value)
                    self.assertTrue(on.redaction_applied)
                    self.assertNotIn("SYNTHETIC_q7", json.dumps(on.value))
        marker = ReferenceMarker(kind="secret", name="off_name_q7", default_redacted=False)
        result = sanitize_untrusted_data({"broker": marker})
        self.assertEqual(result.value["broker"], "[REDACTED:reference_marker]")
        self.assertNotIn("off_name_q7", json.dumps(result.value))

    def test_l5_exempts_markers_by_registry_identity_only(self):
        # H2: the exemption is the identity of a marker this read minted.
        registry = ReferenceMarkerRegistry()
        minted = registry.mint(kind="env_var", name="SYNTHETIC_ID_q7", default_redacted=True)
        result = sanitize_untrusted_data({"broker": minted}, preserve_reference_markers=registry)
        self.assertIs(result.value["broker"], minted)
        self.assertTrue(registry.minted(minted))
        twin = ReferenceMarker(kind="env_var", name="SYNTHETIC_ID_q7", default_redacted=True)
        self.assertEqual(twin, minted)
        foreign = ReferenceMarkerRegistry().mint(kind="env_var", name="SYNTHETIC_ID_q7",
                                                 default_redacted=True)
        for stranger in (twin, foreign):
            for where in ("top", "nested", "list"):
                value = {"top": stranger, "nested": {"broker": stranger},
                         "list": [stranger]}[where]
                with self.subTest(stranger=stranger is twin, where=where):
                    out_ = sanitize_untrusted_data(value, preserve_reference_markers=registry)
                    self.assertNotIn("SYNTHETIC_ID_q7", json.dumps(out_.value))
                    self.assertIn(sanitization.REFERENCE_MARKER_DENIED, json.dumps(out_.value))
                    self.assertTrue(out_.redaction_applied)
        self.assertFalse(registry.minted(twin))
        self.assertFalse(registry.minted({"env_var_ref": "SYNTHETIC_ID_q7"}))
        # A bare boolean is no longer an opt-in.
        for bad in (True, False, 1, "yes", object()):
            with self.subTest(bad=bad), self.assertRaises(TypeError):
                sanitize_untrusted_data({}, preserve_reference_markers=bad)

    def test_other_tools_unchanged_opt_in_is_byte_identical_without_markers(self):
        corpus = json.loads((EVIDENCE / "fixtures/cases.json").read_text())
        values = [entry["value"] for entry in corpus["corpus"].values()]
        values += [{"password": "x", "nested": [{"token": "eyJhbGciOiJIUzI1NiJ9.e30.c2ln"}]},
                   {"broker": "!secret x", "secret_ref": "n"}, "!secret x", ["a", 1, None, 2.5],
                   {"Authorization": "Bearer abcdef"}, PEM_KEY_BEGIN + "\nabc"]
        values += [enc("Server=db;Uid=u;Pwd=" + SYN, n) for n in range(7)]
        for value in values:
            with self.subTest(value=repr(value)[:60]):
                for kwargs in ({}, {"max_string": 64}, {"known_secrets": ("Zq7v0001Xw",)}):
                    on = sanitize_untrusted_data(
                        value, preserve_reference_markers=ReferenceMarkerRegistry(), **kwargs)
                    off = sanitize_untrusted_data(value, **kwargs)
                    self.assertEqual(json.dumps(on.value, sort_keys=True), json.dumps(off.value, sort_keys=True))
                    self.assertEqual(
                        (on.redacted_field_count, on.redaction_categories, on.truncated_field_count,
                         on.failed_closed),
                        (off.redacted_field_count, off.redaction_categories, off.truncated_field_count,
                         off.failed_closed),
                    )

    def test_quoted_literal_residual_is_pinned_as_a_marker(self):
        # Upstream renders password: "!secret abc" exactly like the tag (D1
        # main:refs.quoted); the disclosed residual is a marker, never a value.
        for version in VERSIONS:
            with self.subTest(version=version):
                envelope = y.project_success(d1_result(version, "main:refs.quoted"), "refs.quoted").envelope
                text = json.dumps(envelope["value"])
                self.assertIn("secret_ref", text)
                self.assertIsNone(__import__("re").search(D1_SECRET, text))


class ConnectionTextTests(unittest.TestCase):
    """Group 4: R2 connection and credential text (exact outputs)."""

    PATHS = ("sensor.value", "recorder.db_url", "recorder")

    def at(self, value, path):
        if path == "recorder":
            return out({"db_url": value, "purge_keep_days": 3}, "recorder")["db_url"]
        return out(value, path)

    def expect(self, raw, total_depth, redacted, *, paths=PATHS):
        for path in paths:
            result = self.at(raw, path)
            expected = redacted if total_depth <= 4 else ENC
            self.assertEqual(result, expected, (raw, path, total_depth))

    def test_boundary_dsn_and_uri_identical_at_both_paths(self):
        s = secret("B12")
        for depth in range(1, 7):
            self.expect(enc("Server=db;Uid=u;Pwd=" + s, depth), depth, CONNSTR)
            self.expect(enc("mysql://u:" + s + "@db/ha", depth), depth, "mysql://[REDACTED:connection_uri]")

    def test_revision4_counterexample(self):
        s = secret("C2X")
        for depth in range(7):
            self.expect(enc("Server=db;Uid=u;Pwd==" + s, depth), depth, CONNSTR)
        self.expect("mysql://db/ha?pwd==" + s, 0, "mysql://[REDACTED:connection_uri]")

    def test_pwd_variants(self):
        s = secret("PWV")
        variants = [("Pwd==S", 0), ("PWD :=S", 0), (";pwd;S", 0), ("P%57D=S", 1), ("\uff50\uff57\uff44=S", 0),
                    ("pwd=S", 0), ("?x=PWD%3DS%3Bz", 1), ("?%2550WD=S", 2), ('{"pwd": "S"}', 0)]
        for template, intrinsic in variants:
            raw = template.replace("S", s) if template != "?x=PWD%3DS%3Bz" else f"?x=PWD%3D{s}%3Bz"
            for depth in range(7):
                with self.subTest(raw=raw, depth=depth):
                    self.expect(enc(raw, depth), intrinsic + depth, CONNSTR)

    def test_delimited_token_catches(self):
        s = secret("C3D")
        variants = [("pass={s}", 0), ("PASS =={s}", 0), ("key: {s}", 0), (";key={s}", 0), ("key=={s}", 0), ("pass:={s}", 0),
                    ("key :={s}", 0), ("Key={s}", 0), ("x=1;pass={s}", 0), ("a&key={s}", 0), ("pass\t={s}", 0),
                    ("key\u00a0={s}", 0), ("\uff4b\uff45\uff59\uff1d{s}", 0), ("\uff50\uff41\uff53\uff53\uff1a{s}", 0),
                    ("pass%3D{s}", 1), ("%6Bey={s}", 1), ("k%45Y: {s}", 1)]
        for template, intrinsic in variants:
            raw = template.format(s=s)
            for depth in range(7):
                with self.subTest(raw=raw, depth=depth):
                    self.expect(enc(raw, depth), intrinsic + depth, CONNSTR)

    def test_delimited_token_query_forms(self):
        s = secret("C3Q")
        for raw, scheme in ((f"https://api.example.com/v1?key={s}", "https"),
                            (f"mysql://db/ha?pass={s}", "mysql"),
                            (f"https://api.example.com/v1?entity=sensor.x&KEY={s}", "https")):
            for depth in range(5):
                with self.subTest(raw=raw, depth=depth):
                    self.expect(enc(raw, depth), depth, f"{scheme}://[REDACTED:connection_uri]")

    def test_k1_withholds_mappings_whose_keys_carry_credentials(self):
        s = secret("K1A")
        for key in (f"key={s}", enc(f"pass: {s}", 2), f"pwd={s}", enc(f"Pwd={s}", 2), f"mysql://u:{s}@db",
                    f"token: {s}"):
            with self.subTest(key=key):
                self.assertEqual(out({"outer": {key: 1, "name": "n"}}, "x"), {"outer": CONNSTR})

    def test_non_catches_stay_raw(self):
        for raw in ("keypad", "monkey", "bypass", "compass", "key fob", "monkey=1", "keypad: 4", "bypass=on",
                    "compass: north", "hotkey=F5", "passkey=1", "turnkey: yes", "Kitchen keypad", "Hall pass",
                    "pass the salt", "key lime pie", "keys=3", "passes: 2"):
            for path in ("sensor.value", "light.name"):
                with self.subTest(raw=raw, path=path):
                    self.assertEqual(out(raw, path), raw)

    def test_fully_encoded_uris(self):
        s = secret("URI")
        for raw, scheme in ((f"postgresql+psycopg2://u:{s}@db/ha", "postgresql+psycopg2"),
                            (f"mssql+pyodbc://u:{s}@db/ha", "mssql+pyodbc"),
                            (f"redis://:{s}@db:6379/0", "redis"), (f"https://u:{s}@api.example.com/x", "https"),
                            ("mysql://u@db/ha", "mysql"), (f"mysql://u:{s}\uff20db/ha", "mysql"),
                            (f"mysql://db/ha?password={s}", "mysql")):
            for depth in range(7):
                with self.subTest(raw=raw, depth=depth):
                    self.expect(enc(raw, depth), depth, f"{scheme}://[REDACTED:connection_uri]",
                                paths=self.PATHS + ("rest.resource",))

    def test_standalone_connection_strings(self):
        s = secret("STD")
        odbc = "mssql+pyodbc:///?odbc_connect=" + quote(
            "DRIVER={Synthetic Driver};SERVER=db.invalid;DATABASE=ha;UID=synthetic;PWD=" + s, safe="")
        for raw, intrinsic, redacted in ((f"DRIVER={{X}};SERVER=db;UID=u;PWD={s}", 0, CONNSTR),
                                         (f"Data Source=db;User ID=u;Password={s}", 0, CONNSTR),
                                         (f"host=db dbname=ha user=u password={s}", 0, CONNSTR),
                                         (odbc, 1, "mssql+pyodbc://[REDACTED:connection_uri]")):
            for depth in range(7):
                with self.subTest(raw=raw[:30], depth=depth):
                    self.expect(enc(raw, depth), intrinsic + depth, redacted)

    def test_canonical_keys(self):
        s = secret("R10")
        for key, intrinsic in (("p%61ssword", 1), ("%70assword", 1), ("PASSWORD", 0),
                               ("\uff50\uff41\uff53\uff53\uff57\uff4f\uff52\uff44", 0), ("MQTT-PassWord", 0)):
            for depth in range(7):
                raw_key = enc(key, depth)
                # A key with nothing to encode is identical at every depth.
                total = intrinsic + (depth if quote(key, safe="") != key else 0)
                with self.subTest(key=raw_key, depth=depth):
                    value = out({"mqtt": {"broker": "mqtt.local", raw_key: s}}, "cfg")["mqtt"]
                    if total <= 4:
                        self.assertEqual(value["broker"], "mqtt.local")
                        self.assertIn(raw_key, value)
                        self.assertRegex(value[raw_key], r"^\[REDACTED:")
                    else:
                        self.assertEqual(value, ENC)
                    self.assertTrue(hidden(value, s))
        self.assertEqual(out({"mqtt": {"broker": "b", "Pass Word": s}}, "cfg"), {"mqtt": CONNSTR})

    def test_encoded_ancestors_are_withheld_whole(self):
        s = secret("ANC")
        for key in ("%63redentials", "p%61ssword", "api%5Fkey", "%74oken", "session%5Fid", "%41uth"):
            for child in ({"value": s}, [{"value": s}, {"id": 1}]):
                with self.subTest(key=key, child=type(child).__name__):
                    self.assertEqual(out({key: child, "name": "n"}, "x"), {key: CRED, "name": "n"})

    def test_encoded_and_nfkc_connection_keys_apply_rule_b(self):
        for key in ("db%5Furl", "db_ur\u2113"):
            with self.subTest(key=key):
                value = out({key: "mysql://db.local:3306/ha?charset=utf8mb4", "name": "n"}, "x")
                self.assertEqual(value, {key: "mysql://[REDACTED:connection_uri]", "name": "n"})
                self.assertEqual(out({key: "plain"}, "x"), {key: CONNSTR})

    def test_non_converging_ambiguous_and_invalid(self):
        s = secret("ENC")
        for raw in (enc("Living Room", 5), enc("Living Room", 6), "%41%", "%G1%41", "%41 50% off", "%FF",
                    "%C0%AE", "a%00b", "\uff05\uff17\uff10wd=" + s):
            for path in self.PATHS:
                with self.subTest(raw=raw, path=path):
                    self.assertEqual(self.at(raw, path), ENC)

    def test_r9_bound_discriminators(self):
        self.assertEqual(out(enc("Living Room", 4)), enc("Living Room", 4))
        self.assertEqual(out(enc("Living Room", 5)), ENC)
        self.assertEqual(out(enc("Pwd=" + secret("R9A"), 4)), CONNSTR)
        for raw in ("50% off", "100%", "%G1", "{{ states('sensor.x') }}"):
            self.assertEqual(out(raw), raw)

    def test_key_scoped_name_variants(self):
        for key in ("db_url", "DB_URL", "dbUrl", "database_url", "url", "base_url", "external_url", "uri",
                    "redirect_uri", "dsn", "DSN", "connection", "Connection_String", "conn_str", "connect",
                    "connect_args", "database", "Database", "odbc_connect", "sqlalchemy_url", "Db-Url",
                    "db%5Furl", "db_ur\u2113", "reconnect_interval", "security"):
            with self.subTest(key=key):
                value = out({key: "mysql://db.local:3306/ha?charset=utf8mb4"}, "x")
                self.assertEqual(value, {key: "mysql://[REDACTED:connection_uri]"})
                self.assertEqual(out({key: "plain"}, "x"), {key: CONNSTR})
                self.assertEqual(out({key: 42}, "x"), {key: CONNSTR})
        self.assertEqual(out({"host": "influx.local", "database": {"name": "ha", "retention": "30d"}}, "influxdb"),
                         {"host": "influx.local", "database": CONNSTR})
        self.assertEqual(out({"name": "ha"}, "influxdb.database"), CONNSTR)

    def test_r11_sqlite(self):
        for raw in ("sqlite:////config/home-assistant_v2.db", "sqlite:///home-assistant_v2.db", "sqlite:///a..b.db"):
            for path in self.PATHS:
                with self.subTest(raw=raw, path=path):
                    self.assertEqual(self.at(raw, path), raw)
        redacted = "sqlite://[REDACTED:connection_uri]"
        for raw in ("sqlite:////config/home-assistant_v2.db\n", "sqlite:////config/home\n-assistant_v2.db",
                    "sqlite:////config/x\r.db", "sqlite:////config/x\t.db", "sqlite:////config/x\x00.db",
                    "sqlite:////config/x\x7f.db", "sqlite:////config/x\u2028.db", "sqlite:////config/x\u2029.db",
                    "sqlite:///../x.db", "sqlite:////config/../x.db", "sqlite:////config/x.db/..",
                    "sqlite:////config/x%2D.db", "sqlite:////config/x%.db", "SQLITE:///x.db", "sqlite:///X.db",
                    "sqlite:////config/\uff58.db", "sqlite:////config/x.db?mode=ro", "sqlite:////config/u@x.db",
                    "sqlite:////config/x;y.db", "sqlite:////config/x=y.db", "sqlite:////config/x y.db",
                    "sqlite:///:memory:"):
            for path in ("recorder.db_url", "recorder"):
                with self.subTest(raw=raw, path=path):
                    self.assertEqual(self.at(raw, path), redacted)
        # Redacted by C' first: the same output at an ordinary path.
        for path in self.PATHS:
            self.assertEqual(self.at("sqlite:////config/session.db", path), redacted)
        self.assertEqual(out({"db_url": "!secret recorder_db_url"}, "recorder"),
                         {"db_url": {"secret_ref": "recorder_db_url"}})

    def test_r11_layers_reject_independently(self):
        # The full-string match and the explicit control-character check each
        # reject on their own (design §2.2 R11, §8.2), so neither can mask a
        # weakened other.
        newline = "sqlite:///x.db\n"
        self.assertFalse(y.sqlite_visible(newline, newline, 0))
        with patch.object(y, "_SQLITE_FORBIDDEN", y.re.compile("(?!)")):
            self.assertFalse(y.sqlite_visible(newline, newline, 0))
            self.assertTrue(y.sqlite_visible("sqlite:///x.db", "sqlite:///x.db", 0))
        widened = y.re.compile(r"sqlite:///[a-z0-9_./\s-]+")
        with patch.object(y, "_SQLITE", widened):
            for raw in (newline, "sqlite:///x\r.db", "sqlite:///x .db", "sqlite:///x\x7f.db"):
                with self.subTest(raw=raw):
                    self.assertFalse(y.sqlite_visible(raw, raw, 0))

    def test_lists_under_connection_keys_are_replaced_whole(self):
        # Rule B replaces a list under a connection-shaped key; it never walks
        # it, so a safe SQLite element does not become visible.
        safe = "sqlite:////config/home-assistant_v2.db"
        self.assertEqual(out({"db_url": [safe]}, "recorder"), {"db_url": CONNSTR})
        self.assertEqual(out([safe, "mysql://db.local/ha"], "recorder.db_url"), CONNSTR)
        self.assertEqual(out({"db_url": []}, "recorder"), {"db_url": CONNSTR})
        result = project({"db_url": [safe, safe]}, "recorder")
        self.assertEqual(result.envelope["value"], {"db_url": CONNSTR})
        self.assertEqual(result.redacted_field_count, 1)

    def test_each_credential_word_alone_redacts_value_text(self):
        words = ("pwd", "passw", "secret", "token", "apikey", "api_key", "api-key", "auth",
                 "credential", "private", "bearer", "cookie", "session")
        self.assertEqual(len(words), 13)
        for word in words:
            raw = f"note {word} here"
            # The case holds no other trigger: no other word, no C'3 label.
            self.assertEqual([w for w in words if w in raw], [word])
            self.assertIsNone(y._DELIMITED_TOKEN.search(raw))
            for path in self.PATHS:
                with self.subTest(word=word, path=path):
                    self.assertEqual(self.at(raw, path), CONNSTR)
            with self.subTest(word=word, scheme=True):
                self.assertEqual(out(f"{word}://host/x"), CONNSTR)

    def test_useful_values_stay_visible_raw(self):
        for raw in ("mysql://db.local:3306/ha?charset=utf8mb4", "postgresql://db.local/ha?sslmode=require",
                    "mysql://[fd00::5]:3306/ha", "unknown://host/path", "https://api.example.com/v1?entity=sensor.x",
                    "https://api.example.com/v1/{{ states('sensor.x') }}/value",
                    "Server=db;Database=ha;Trusted_Connection=yes", "Living Room", "50% off"):
            for path in ("sensor.value", "rest.resource"):
                with self.subTest(raw=raw, path=path):
                    self.assertEqual(out(raw, path), raw)
        recorder = {"purge_keep_days": 10, "commit_interval": 5, "auto_purge": True,
                    "exclude": {"domains": ["automation", "updater"], "entity_globs": ["sensor.weather_*"]}}
        result = project(recorder, "recorder")
        self.assertEqual(result.envelope["value"], recorder)
        self.assertEqual(result.redacted_field_count, 0)
        self.assertEqual(self.at("unknown://host/path", "recorder.db_url"), "unknown://[REDACTED:connection_uri]")

    def test_disclosed_over_redaction_is_pinned(self):
        for raw, expected in (
            ("Refresh the access token daily", CONNSTR), ("Author: Synthetic Person", CONNSTR),
            ("Session started", CONNSTR), ("private room", CONNSTR), ("cookie jar", CONNSTR),
            ("secretary", CONNSTR), ("https://example.com/oauth/authorize", "https://[REDACTED:connection_uri]"),
            ("{% if token == 1 %}", CONNSTR), ("token://host/x", CONNSTR),
            ("https://example.com/contact person@example.com", "https://[REDACTED:connection_uri]"),
            ("Hall pass: yes", CONNSTR), ("Press key: Enter", CONNSTR), ("{% set key = 1 %}", CONNSTR),
            ("Spare key=garage", CONNSTR), ("key://host/x", "key://[REDACTED:connection_uri]"),
            ("http://key:8080/x", "http://[REDACTED:connection_uri]"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(out(raw), expected)
        self.assertEqual(out({"auth_providers": [{"type": "homeassistant"}], "name": "n"}, "homeassistant"),
                         {"auth_providers": CRED, "name": "n"})
        self.assertEqual(out({"private_ble_device": {"name": "tag"}}, "x"), {"private_ble_device": CRED})

    def test_pinned_d12_residuals_are_not_redacted(self):
        s = secret("D12")
        short = base64.b64encode(("Pwd=" + s).encode()).decode()
        for raw in (f"login={s}", f"pw={s}", f"psk={s}", f"p_w_d={s}", f"P\u0441d={s}", f"k\u0435y={s}", short,
                    f"my_key={s}", f"app_key={s}", f"passkey={s}", f"pass_word={s}", f'"key": "{s}"',
                    f"{{'pass': '{s}'}}", f"key\u2192{s}"):
            with self.subTest(raw=raw):
                self.assertEqual(out(raw), raw)
        self.assertEqual(out({"foo": s}, "x"), {"foo": s})

    def test_raw_emitted_never_canonical(self):
        for raw in (enc("Living Room", 4), "Living Room", "MiXeD Case", "\uff21wide", "a%20b"):
            self.assertEqual(out(raw), raw)
        self.assertEqual(out({"Living%20Room": 1, "MiXeD": 2}, "x"), {"Living%20Room": 1, "MiXeD": 2})


class OtherSecretTests(unittest.TestCase):
    """Group 5: other secrets in output."""

    def test_other_secret_shapes(self):
        s = secret("OS1")
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMifQ." + s
        blob = base64.b64encode((s * 20).encode()).decode()
        self.assertGreaterEqual(len(blob), 200)
        result = project({
            "items": [{"name": "a", "wifi_psk": s}, {"name": "b", "door_pin": 4321}, {"name": "c", "app_code": s}],
            "headers": {"Authorization": "Bearer " + s, "Accept": "application/json"},
            "jwt": jwt,
            "key_pem": PEM_KEY_BEGIN + "\n" + s + "\n-----END PRIVATE KEY-----",
            "cert": "-----BEGIN CERTIFICATE-----\nU1lOVEhFVElD" + s + "\n-----END CERTIFICATE-----",
            "blob": blob,
            "ref": "!secret mqtt_password",
        }, "x")
        value = result.envelope["value"]
        self.assertEqual([item["name"] for item in value["items"]], ["a", "b", "c"])
        for item in value["items"]:
            self.assertTrue(all(v.startswith("[REDACTED:") for k, v in item.items() if k != "name"))
        self.assertEqual(value["headers"]["Accept"], "application/json")
        self.assertRegex(value["headers"]["Authorization"], LABEL)
        self.assertRegex(value["jwt"], r"^\[REDACTED:")
        self.assertEqual(value["key_pem"], CONNSTR)  # C'2 "private" first
        self.assertEqual(value["cert"], "[REDACTED:pem]")
        self.assertEqual(value["blob"], "[REDACTED:opaque_blob]")
        self.assertEqual(value["ref"], {"secret_ref": "mqtt_password"})
        assert_absent(self, json.dumps(result.envelope), s)

    def test_known_secrets_are_redacted_by_the_scanner(self):
        s = "synthetic-known-secret-value-q7"
        result = project({"note": "prefix " + s + " suffix"}, "x", known_secrets=(s,))
        self.assertTrue(hidden(result.envelope, s))

    def test_no_content_field_ever(self):
        async def scenario():
            for version in VERSIONS:
                gateway, transport = await yaml_gateway(version)
                for case_id in ("content+parsed:refs", "content+parsed:anchors"):
                    result_value = d1_result(version, case_id)
                    transport.respond = lambda tool, args, r=result_value: deepcopy(r)
                    path = "refs" if case_id.endswith("refs") else "anchors"
                    result = await call(gateway, TOOL, {"yaml_path": path})
                    refused(self, result, dispatched=True, category="invalid_response")
                    self.assertEqual(result["error_code"], y.IDENTITY_INVALID)
                    self.assertNotIn("content", json.dumps(result.get("data", {})))
                recorder = d1_result(version, "main:recorder")
                transport.respond = lambda tool, args, r=recorder: deepcopy(r)
                good = await call(gateway, TOOL, {"yaml_path": "recorder"})
                self.assertEqual(set(good["data"]), {"file", "yaml_path", "source", "found", "value",
                                                     "completeness", "limitations", "redaction"})

        asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Groups 1, 2, 5-9 and 14 through the gateway.


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_useful_success_at_both_versions(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                gateway, transport = await yaml_gateway(version)
                for case_id, path in (("main:recorder", "recorder"), ("main:homeassistant.customize",
                                      "homeassistant.customize"), ("main:template", "template"),
                                      ("main:recorder.missing", "recorder.missing")):
                    reply = d1_result(version, case_id)
                    transport.respond = lambda tool, args, r=reply: deepcopy(r)
                    transport.sent.clear()
                    result = await call(gateway, TOOL, {"yaml_path": path})
                    self.assertTrue(result["success"], result)
                    self.assertEqual(transport.sent, [(TOOL, y.upstream_arguments(path))])
                    data = result["data"]
                    self.assertEqual((data["file"], data["yaml_path"], data["source"]),
                                     ("configuration.yaml", path, "disk"))
                    self.assertEqual(data["found"], case_id != "main:recorder.missing")
                    self.assertEqual(data["limitations"][-3:], list(y.DISCLOSURE_LIMITATIONS))
                    self.assertEqual(result["warnings"], [y.K7_WARNING])
                    self.assertIsNone(__import__("re").search(D1_SECRET, json.dumps(result)))
                    self.assertEqual(result["metadata"]["upstream_tool"], TOOL)
                    self.assertEqual(result["metadata"]["upstream_version"], version)
                recorder = await self._read(gateway, transport, version, "main:recorder", "recorder")
                self.assertIn("purge_keep_days", recorder["data"]["value"])
                # The explicit constant file argument is accepted and identical.
                transport.sent.clear()
                await call(gateway, TOOL, {"yaml_path": "recorder", "file": "configuration.yaml"})
                self.assertEqual(transport.sent, [(TOOL, y.upstream_arguments("recorder"))])

    async def _read(self, gateway, transport, version, case_id, path):
        reply = d1_result(version, case_id)
        transport.respond = lambda tool, args: deepcopy(reply)
        return await call(gateway, TOOL, {"yaml_path": path})

    async def test_pre_dispatch_refusals_make_zero_upstream_calls(self):
        files = ["configuration.yml", "Configuration.yaml", "CONFIGURATION.YAML", "./configuration.yaml",
                 "/config/configuration.yaml", "configuration.yaml ", "packages/x.yaml", "packages/*.yaml",
                 "*.yaml", "secrets.yaml", "Secrets.YAML", "PACKAGES/../secrets.yaml", ".storage/core.config",
                 "scripts.yaml", "configuration.yaml\x00", "packages\\x.yaml", "%2e%2e/secrets.yaml",
                 "//configuration.yaml", "~/configuration.yaml", "http://example.invalid/configuration.yaml",
                 "c" * 5000, ""]
        paths = ["", ".", "recorder.", ".recorder", "a..b", " recorder", "recorder ", "*", "recorder.*",
                 "r" * 1000, "a.b.c.d.e.f.g.h.i", "x" * 65, "recorder.0x/", "-recorder", "recorder\n",
                 "\uff52ecorder", "rec%6Frder"]
        sensitive = ["mqtt.password", "mqtt.credentials", "mqtt.credentials.username", "http.api_key",
                     "influxdb.token", "x.pin", "x.code", "x.auth.password", "mqtt.Password", "mqtt.api-key",
                     "homeassistant.auth_providers", "mqtt.MQTT-PassWord", "x.session_timeout"]
        shapes = [{"yaml_path": "recorder", "include_content": True}, {"yaml_path": "recorder", "path": "x"},
                  {"yaml_path": 1}, {"file": "configuration.yaml"}, {}, {"yaml_path": "recorder", "file": None},
                  {"yaml_path": ["recorder"]}, {"yaml_path": "recorder", "include_parsed": False}]
        for version in VERSIONS:
            gateway, transport = await yaml_gateway(version)
            cases = ([({"yaml_path": "recorder", "file": f}, y.FILE_NOT_ALLOWED) for f in files]
                     + [({"yaml_path": p}, y.PATH_INVALID) for p in paths]
                     + [({"yaml_path": p}, y.PATH_SENSITIVE) for p in sensitive]
                     + [(a, None) for a in shapes])
            for arguments, code in cases:
                with self.subTest(version=version, arguments=repr(arguments)[:60]):
                    result = await call(gateway, TOOL, arguments)
                    refused(self, result, dispatched=False)
                    if code is not None:
                        self.assertEqual(result["error_code"], code)
                        self.assertEqual(result["message"], y.FAILURES[code][1])
                        self.assertEqual(result["details"]["failure_category"], "invalid_request")
                    else:
                        self.assertIn(result["details"]["failure_category"],
                                      {"invalid_request", "argument_validation", "invalid_arguments"})
                    self.assertFalse(result["retryable"])
                    self.assertEqual(transport.sent, [])
                    self.assertEqual(result["timing"]["upstream_request_count"], 0)
                    for value in arguments.values():
                        if isinstance(value, str) and len(value) > 3 and value.strip() != y.ALLOWED_FILE:
                            self.assertNotIn(value, json.dumps(result))

    def test_validate_arguments_codes_and_order(self):
        self.assertEqual(y.validate_arguments({"yaml_path": "recorder"}), "recorder")
        self.assertEqual(y.validate_arguments({"yaml_path": "a.b", "file": "configuration.yaml"}), "a.b")
        for arguments, code in (
            ([], y.ARGUMENTS_INVALID), ({"yaml_path": "x", "z": "1"}, y.ARGUMENTS_INVALID),
            ({"yaml_path": 1}, y.ARGUMENTS_INVALID), ({"file": "configuration.yaml"}, y.ARGUMENTS_INVALID),
            ({"yaml_path": "mqtt.password", "file": "secrets.yaml"}, y.FILE_NOT_ALLOWED),
            ({"yaml_path": "", "file": "secrets.yaml"}, y.FILE_NOT_ALLOWED),
            ({"yaml_path": "mqtt..password"}, y.PATH_INVALID),
            ({"yaml_path": "mqtt.password"}, y.PATH_SENSITIVE),
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(y.YamlReadRefusal) as caught:
                    y.validate_arguments(arguments)
                self.assertEqual(caught.exception.code, code)
                self.assertIn(code, y.PRE_DISPATCH_CODES)
        self.assertEqual(y.refusal_audit_details({"yaml_path": "abc", "file": "secrets.yaml", "x": "y"}),
                         {"file_length": 12, "yaml_path_length": 3})
        self.assertEqual(y.upstream_arguments("recorder"), {"yaml_path": "recorder", "file": "configuration.yaml",
                                                            "include_content": False, "include_parsed": True})

    def test_l1_l2_relationship(self):
        # L1 admits exactly one value and L2 never denies it.
        self.assertTrue(y.l1_allows("configuration.yaml"))
        self.assertFalse(y.l2_denies("configuration.yaml"))
        for value in ("Configuration.yaml", "configuration.yml", None, 1, b"configuration.yaml"):
            self.assertFalse(y.l1_allows(value))
        # L2 is an independent floor: with L1 widened to anything, it still
        # refuses the sensitive surface.
        for value in ("secrets.yaml", "Secrets.YAML", "PACKAGES/../secrets.yaml", ".storage/core.config",
                      "packages/x.yaml", "www/data.yaml", "custom_components/x/__init__.py", "home-assistant_v2.db",
                      "home-assistant.log", "approval_pin.json", "ssl/fullchain.pem", "x/.ha_mcp_tools/auth",
                      "blueprints/a.yaml", "backups/x.tar", "id_rsa", "token.txt", "my_credentials.yaml",
                      "core.json", "x.sqlite3", "deps/x", "tts/x.mp3", ".cloud/x", ".git/config"):
            with self.subTest(value=value):
                self.assertTrue(y.l2_denies(value))
        with mutated(y, "l1_allows", 'return type(file_value) is str and file_value == ALLOWED_FILE',
                     "return True"):
            for value in ("secrets.yaml", "packages/x.yaml", ".storage/core.config"):
                with self.assertRaises(y.YamlReadRefusal) as caught:
                    y.validate_arguments({"yaml_path": "x", "file": value})
                self.assertEqual(caught.exception.code, y.FILE_NOT_ALLOWED)

    async def test_identity_and_shape_refusals(self):
        good = payload({"a": 1}, "recorder")
        variants = {
            "file": lambda p: p["matches"][0].update(file="secrets.yaml"),
            "match_path": lambda p: p["matches"][0].update(yaml_path="other"),
            "top_path": lambda p: p.update(yaml_path="other"),
            "count_2": lambda p: p.update(count=2, matches=p["matches"] * 2),
            "count_mismatch": lambda p: p.update(count=0),
            "count_bool": lambda p: p.update(count=True),
            "searched_0": lambda p: p.update(files_searched=0),
            "searched_2": lambda p: p.update(files_searched=2),
            "unreadable": lambda p: p.update(files_unreadable=1),
            "warnings": lambda p: p.update(warnings=["synthetic upstream warning q7"]),
            "extra_top": lambda p: p.update(extra=1),
            "extra_match": lambda p: p["matches"][0].update(content="raw text q7"),
            "missing_match_key": lambda p: p["matches"][0].pop("parsed"),
            "success_false": lambda p: p.update(success=False),
            "success_str": lambda p: p.update(success="true"),
            "matches_dict": lambda p: p.update(matches={}),
            "missing_count": lambda p: p.pop("count"),
            "searched_str": lambda p: p.update(files_searched="1"),
        }
        gateway, transport = await yaml_gateway()
        for name, change in variants.items():
            with self.subTest(name=name):
                bad = deepcopy(good)
                change(bad)
                transport.respond = lambda tool, args, b=bad: ok(b)
                result = await call(gateway, TOOL, {"yaml_path": "recorder"})
                refused(self, result, dispatched=True, category="invalid_response")
                self.assertEqual(result["error_code"], y.IDENTITY_INVALID)
                self.assertNotIn("q7", json.dumps(result))
        for name, reply in {
            "structured_differs": {"isError": False, "structuredContent": payload({"a": 2}, "recorder"),
                                   "content": [{"type": "text", "text": json.dumps(good)}]},
            "two_texts": {"isError": False, "content": [{"type": "text", "text": json.dumps(good)}] * 2},
            "duplicate_member": {"isError": False, "content": [{"type": "text", "text": json.dumps(good)[:-1]
                                                                + ', "count": 1}'}]},
            "nan": {"isError": False, "content": [{"type": "text", "text": json.dumps(good).replace(
                '"a": 1', '"a": NaN')}]},
            "not_json": {"isError": False, "content": [{"type": "text", "text": "plain q7"}]},
            "image": {"isError": False, "content": [{"type": "image", "data": "", "mimeType": "image/png"}]},
        }.items():
            with self.subTest(name=name):
                transport.respond = lambda tool, args, r=reply: deepcopy(r)
                result = await call(gateway, TOOL, {"yaml_path": "recorder"})
                refused(self, result, dispatched=True)
                self.assertIn(result["error_code"], {y.IDENTITY_INVALID, "invalid_response", "provider_error"})
                self.assertNotIn("q7", json.dumps(result))

    async def test_errors_map_to_fixed_codes_without_upstream_text(self):
        canary = "SYNUPSTREAMTEXTQ7"

        def error(body):
            return {"isError": True, "content": [{"type": "text", "text": json.dumps(body)}]}

        cases = [
            (error({"success": False, "error": f"Path not allowed: {canary} allowlist www/ packages/"}),
             y.FILE_NOT_ALLOWED_UPSTREAM, {}),
            (error({"success": False, "error": f"File does not exist: {canary}"}), y.FILE_NOT_FOUND, {}),
            (error({"success": False, "error": f"Other component failure {canary}"}), y.READ_FAILED, {}),
            (error({"success": False, "error": {"code": "CONFIG_INVALID",
                                                "message": f"bad {canary} at line 12, column 3"}}),
             y.PARSE_ERROR, {"line": 12, "column": 3}),
            (error({"success": False, "error": {"code": "CONFIG_INVALID", "message": canary}}), y.PARSE_ERROR, {}),
            (error({"success": False, "error": {"code": "VALIDATION_FAILED", "message": canary}}),
             y.VALIDATION_OR_UNAVAILABLE, {}),
            (error({"success": False, "error": {"code": "INTERNAL_ERROR", "message": canary}}), y.READ_FAILED, {}),
            (error({"success": True, "error": canary}), y.READ_FAILED, {}),
            ({"isError": True, "content": [{"type": "text", "text": canary}]}, y.READ_FAILED, {}),
            ({"isError": True, "content": []}, y.READ_FAILED, {}),
        ]
        gateway, transport = await yaml_gateway()
        for reply, code, details in cases:
            with self.subTest(code=code, reply=json.dumps(reply)[:80]):
                transport.respond = lambda tool, args, r=reply: deepcopy(r)
                result = await call(gateway, TOOL, {"yaml_path": "recorder"})
                refused(self, result, dispatched=True)
                self.assertEqual(result["error_code"], code)
                self.assertEqual(result["message"], y.FAILURES[code][1])
                self.assertEqual(result["details"], {"failure_category": y.FAILURES[code][0], **details})
                self.assertEqual(result["retryable"], code in {y.VALIDATION_OR_UNAVAILABLE, y.READ_FAILED})
                self.assertNotIn(canary, json.dumps(result))
                self.assertNotIn("allowlist", json.dumps(result))
        # K2: never a definite "component unavailable" claim.
        self.assertEqual(y.FAILURES[y.VALIDATION_OR_UNAVAILABLE][1],
                         "upstream validation failed or component unavailable")

    async def test_d1_errors_replayed(self):
        expected = {
            "variant:parse_error:recorder": y.PARSE_ERROR, "variant:duplicate_keys:recorder": y.PARSE_ERROR,
            "variant:binary:recorder": y.READ_FAILED, "variant:latin1_text:recorder": y.READ_FAILED,
            "yaml11key:bigint": y.READ_FAILED, "main:yaml11": y.READ_FAILED,
            "variant:missing_config:recorder": y.FILE_NOT_FOUND,
            "unavailable:entry_removed": y.VALIDATION_OR_UNAVAILABLE,
            "supp:config_symlink_escape:x": y.FILE_NOT_ALLOWED_UPSTREAM,
        }
        for version in VERSIONS:
            gateway, transport = await yaml_gateway(version)
            cases = {case["id"]: case for _label, case in d1_cases(version)}
            for case_id, code in expected.items():
                with self.subTest(version=version, case=case_id):
                    case = cases[case_id]
                    reply = case["response"]["message"]["result"]
                    transport.respond = lambda tool, args, r=reply: deepcopy(r)
                    result = await call(gateway, TOOL, {"yaml_path": case["arguments"]["yaml_path"]})
                    refused(self, result, dispatched=True)
                    self.assertEqual(result["error_code"], code)
                    upstream_text = reply["content"][0]["text"]
                    self.assertNotIn(upstream_text[:40], json.dumps(result))
                    self.assertIsNone(__import__("re").search(D1_SECRET, json.dumps(result)))

    async def test_bounds(self):
        gateway, transport = await yaml_gateway()
        # 4,001 nodes: deterministic breadth-first truncation, partial.
        big = {f"k{i:04d}": i for i in range(4000)}
        transport.respond = lambda tool, args: ok(payload(big, args["yaml_path"]))
        result = await call(gateway, TOOL, {"yaml_path": "big"})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["completeness"], "partial")
        self.assertEqual(result["metadata"]["completeness"], "partial")
        self.assertIn("value_truncated:1 nodes omitted", result["data"]["limitations"])
        self.assertEqual(list(result["data"]["value"]), [f"k{i:04d}" for i in range(3999)])
        again = await call(gateway, TOOL, {"yaml_path": "big"})
        self.assertEqual(again["data"], result["data"])
        # Depth 25.
        deep = "leaf"
        for i in range(25):
            deep = {f"d{i}": deep}
        transport.respond = lambda tool, args: ok(payload(deep, args["yaml_path"]))
        result = await call(gateway, TOOL, {"yaml_path": "deep"})
        self.assertEqual(result["data"]["completeness"], "partial")
        self.assertIn(f"value_depth_limited:{y.MAX_DEPTH}", result["data"]["limitations"])
        self.assertIn(y.DEPTH_MARKER, json.dumps(result["data"]["value"]))
        # Alias expansion arrives as an expanded tree; the node cap bounds it.
        leaf = ["anchor-value"] * 8
        tree = leaf
        for _ in range(4):
            tree = [deepcopy(tree) for _ in range(8)]
        transport.respond = lambda tool, args: ok(payload(tree, args["yaml_path"]))
        result = await call(gateway, TOOL, {"yaml_path": "bomb"})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["completeness"], "partial")
        self.assertTrue(any(item.startswith("value_truncated:") for item in result["data"]["limitations"]))

    async def test_truncation_runs_before_redaction(self):
        # H2 (owner direction, superseding design section 6.3): the node cap
        # applies to the decoded upstream tree first; redaction then judges
        # exactly the kept shapes. Beyond-cap nodes are never returned, so they
        # are neither examined nor counted.
        s = secret("TRN")
        gateway, transport = await yaml_gateway()
        value = {"inside": f"mysql://u:{s}@db/ha", **{f"k{i:04d}": i for i in range(4100)},
                 "zz_beyond": f"mysql://u:{s}@db/ha", "zz_password": s}
        transport.respond = lambda tool, args: ok(payload(value, args["yaml_path"]))
        result = await call(gateway, TOOL, {"yaml_path": "big"})
        text = json.dumps(result)
        assert_absent(self, text, s)
        self.assertEqual(result["data"]["value"]["inside"], "mysql://[REDACTED:connection_uri]")
        self.assertNotIn("zz_beyond", result["data"]["value"])
        self.assertEqual(result["data"]["redaction"]["redacted_field_count"], 1)
        self.assertEqual(result["data"]["completeness"], "partial")
        self.assertIn("value_truncated:104 nodes omitted", result["data"]["limitations"])
        # A redacted subtree still counts toward the cap: the cap measures the
        # upstream tree, so a credential list beyond it makes the read partial.
        value = {"password": [0] * 5000, "a": 1}
        transport.respond = lambda tool, args: ok(payload(value, args["yaml_path"]))
        result = await call(gateway, TOOL, {"yaml_path": "big"})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["value"], {"password": "[REDACTED:password]", "a": 1})
        self.assertEqual(result["data"]["completeness"], "partial")
        self.assertIn("value_truncated:1003 nodes omitted", result["data"]["limitations"])

    async def test_response_too_large_is_a_failure_not_a_partial(self):
        gateway, transport = await yaml_gateway(response_size_limit=20_000)
        value = {f"k{i}": "lorem ipsum " * 125 for i in range(40)}
        transport.respond = lambda tool, args: ok(payload(value, args["yaml_path"]))
        result = await call(gateway, TOOL, {"yaml_path": "big"})
        refused(self, result, dispatched=True, category="response_too_large")
        self.assertNotIn("lorem ipsum", json.dumps(result))

    async def test_strings_are_bounded_after_redaction(self):
        gateway, transport = await yaml_gateway()
        transport.respond = lambda tool, args: ok(payload({"long": "a b " * 9000}, args["yaml_path"]))
        result = await call(gateway, TOOL, {"yaml_path": "x"})
        self.assertTrue(result["success"], result)
        self.assertTrue(any(item.startswith("strings_truncated:") for item in result["data"]["limitations"]))
        self.assertEqual(result["data"]["completeness"], "partial")

    async def test_includes_are_returned_and_never_followed(self):
        gateway, transport = await yaml_gateway()
        value = {"automation": "!include automations.yaml", "group": "!include_dir_merge_named groups/",
                 "nested": {"items": ["!include_dir_list lists/"]}, "bad": "!include", "weird": "!INCLUDE x.yaml",
                 "odd name!": {"a": "!include a.yaml"}}
        transport.respond = lambda tool, args: ok(payload(value, args["yaml_path"]))
        result = await call(gateway, TOOL, {"yaml_path": "includes"})
        data = result["data"]
        self.assertEqual(data["value"]["automation"], "!include automations.yaml")
        self.assertEqual(data["value"]["group"], "!include_dir_merge_named groups/")
        self.assertEqual(data["value"]["bad"], y.INCLUDE_MALFORMED_MARKER)
        self.assertEqual(data["value"]["weird"], y.INCLUDE_MALFORMED_MARKER)
        self.assertEqual(data["completeness"], "partial")
        self.assertIn("include_not_followed:includes.automation", data["limitations"])
        self.assertIn("include_not_followed:includes.group", data["limitations"])
        self.assertIn("include_not_followed:includes.nested.items", data["limitations"])
        self.assertIn("include_not_followed:includes.*", data["limitations"])
        self.assertEqual(len(transport.sent), 1)
        many = {f"i{n:02d}": f"!include f{n}.yaml" for n in range(20)}
        transport.respond = lambda tool, args: ok(payload(many, args["yaml_path"]))
        result = await call(gateway, TOOL, {"yaml_path": "includes"})
        limits = [item for item in result["data"]["limitations"] if item.startswith("include_not_followed")]
        self.assertEqual(len(limits), y.MAX_INCLUDE_LIMITATIONS + 1)
        self.assertEqual(limits[-1], "include_not_followed_truncated")

    async def test_every_channel_is_searched(self):
        s = "SYNCHAN/+Q7:x"
        gateway, transport = await yaml_gateway()
        replies = [
            ok(payload({"db_url": f"mysql://u:{s}@db/ha", "password": s, "note": f"token {s}",
                        **{f"k{i}": i for i in range(4100)}, "zz": f"mysql://u:{s}@db"}, "x")),
            {"isError": True, "content": [{"type": "text", "text": json.dumps(
                {"success": False, "error": {"code": "CONFIG_INVALID", "message": f"{s} at line 1, column 2"}})}]},
            {"isError": True, "content": [{"type": "text", "text": json.dumps(
                {"success": False, "error": f"Path not allowed: {s}"})}]},
            ok(payload({"a": 1}, "x", warnings=[s])),
        ]
        for reply in replies:
            transport.respond = lambda tool, args, r=reply: deepcopy(r)
            result = await call(gateway, TOOL, {"yaml_path": "x"})
            assert_absent(self, json.dumps(result), s)
        for arguments in ({"yaml_path": "x", "file": s}, {"yaml_path": s}, {"yaml_path": "x", "z": s}):
            result = await call(gateway, TOOL, arguments)
            assert_absent(self, json.dumps(result), s)


class CorpusTests(unittest.TestCase):
    """The D1 corpus (409 values) through the projection, per group, at both versions."""

    RAW = {"c3_noncatch", "d12_residual", "r11_sqlite_visible", "unicode_controls", "useful_visible"}
    REDACTED = {"a_nonconverging", "c2_pwd_variants", "c3_delimited", "c3_query", "over_redaction",
                "r2_boundary_dsn", "r2_boundary_uri", "r2_c2_counterexample", "r2_c2_uri", "standalone_cs",
                "uri_userinfo"}

    def test_corpus_groups(self):
        import re
        fixtures = json.loads((EVIDENCE / "fixtures/cases.json").read_text())
        corpus = fixtures["corpus"]
        self.assertEqual(len(corpus), 409)
        self.assertEqual(set(fixtures["corpus_groups"]),
                         self.RAW | self.REDACTED | {"other_secrets", "r9_bounds", "r11_sqlite_variants"})
        for version in VERSIONS:
            reply = d1_result(version, "main:corpus")
            parsed = json.loads(reply["content"][0]["text"])["matches"][0]["parsed"]
            self.assertEqual(parsed, {key: entry["value"] for key, entry in corpus.items()})
            value = y.project_success(reply, "corpus").envelope["value"]
            for key, entry in corpus.items():
                raw, group, result = entry["value"], entry["group"], value[key]
                with self.subTest(version=version, key=key, group=group):
                    if group in self.RAW:
                        self.assertEqual(result, raw)
                    elif group in self.REDACTED:
                        self.assertNotEqual(result, raw)
                        self.assertRegex(json.dumps(result), r"\[REDACTED:[a-z_]+\]")
                        self.assertIsNone(re.search(D1_SECRET, json.dumps(result)))
                    elif group == "r11_sqlite_variants":
                        # Not under a connection key here: only C' text is redacted.
                        self.assertEqual(result != raw, "@" in raw or "session" in raw)
                    elif group == "r9_bounds":
                        # Five rounds of encoding, and a credential, are redacted.
                        self.assertEqual(result == raw, key not in {"c0324", "c0325"})
                    elif group == "other_secrets":
                        # c0399 is 154 characters of base64, under the 200-character
                        # blob threshold: a disclosed D12 residual.
                        self.assertEqual(result == raw, key == "c0399")
            # Under a connection key the R11 variants are redacted.
            for key, entry in corpus.items():
                if entry["group"] == "r11_sqlite_variants":
                    self.assertEqual(out(entry["value"], "recorder.db_url"), "sqlite://[REDACTED:connection_uri]")

    def test_key_variants_mapping(self):
        for version in VERSIONS:
            envelope = y.project_success(d1_result(version, "main:keys"), "keys").envelope
            # One key is encoded past the bound: the whole mapping is withheld.
            self.assertEqual(envelope["value"], ENC)
            self.assertIsNone(__import__("re").search(D1_SECRET, json.dumps(envelope)))


class D1ReplayTests(unittest.IsolatedAsyncioTestCase):
    """Every captured ha_config_get_yaml case, replayed at both versions."""

    async def test_replay(self):
        import re
        replayed = refused_pre = 0
        for version in VERSIONS:
            gateway, transport = await yaml_gateway(version)
            for _label, case in d1_cases(version):
                arguments = case["arguments"]
                message = (case.get("response") or {}).get("message") or {}
                reply = message.get("result")
                public = {"yaml_path": arguments.get("yaml_path")}
                if "file" in arguments and arguments["file"] != "configuration.yaml":
                    public["file"] = arguments["file"]
                with self.subTest(version=version, case=case["id"]):
                    try:
                        y.validate_arguments(public)
                        pre = None
                    except y.YamlReadRefusal as refusal:
                        pre = refusal.code
                    transport.sent.clear()
                    transport.respond = lambda tool, args, r=reply: deepcopy(r)
                    if pre is not None:
                        result = await call(gateway, TOOL, public)
                        refused(self, result, dispatched=False)
                        self.assertEqual(result["error_code"], pre)
                        self.assertEqual(transport.sent, [])
                        refused_pre += 1
                        continue
                    if reply is None or arguments != y.upstream_arguments(public["yaml_path"]):
                        continue  # never sent by Engineering, or no result captured
                    result = await call(gateway, TOOL, public)
                    replayed += 1
                    self.assertEqual(transport.sent, [(TOOL, y.upstream_arguments(public["yaml_path"]))])
                    leaks = set(re.findall(D1_SECRET, json.dumps(result)))
                    if case["id"] == "main:corpus":
                        fixtures = json.loads((EVIDENCE / "fixtures/cases.json").read_text())
                        allowed = {m for e in fixtures["corpus"].values()
                                   if e["group"] in {"d12_residual", "other_secrets"}
                                   for m in re.findall(D1_SECRET, e["value"])}
                        self.assertLessEqual(leaks, allowed)
                    else:
                        self.assertEqual(leaks, set(), case["id"])
                    if result["success"]:
                        self.assertEqual(result["warnings"], [y.K7_WARNING])
                        self.assertEqual(result["data"]["limitations"][-3:], list(y.DISCLOSURE_LIMITATIONS))
        self.assertGreater(replayed, 150)
        self.assertGreater(refused_pre, 100)

    def test_component_calls_per_read(self):
        """D1 proxy log: one component read_file per read, never list_files.

        The first call per process also fetches the caller token (K1).
        """

        rows = [json.loads(line) for line in
                (EVIDENCE / "captures/proxy-structure.jsonl").read_text().splitlines()]
        windows, current = {}, None
        for row in rows:
            if row["kind"] == "marker":
                label = row["label"]
                current = label[: -len(":begin")] if label.endswith(":begin") else None
                if current is not None:
                    windows[current] = []
            elif current is not None:
                windows[current].append(row)
        checked = 0
        for version in VERSIONS:
            for label, case in d1_cases(version):
                arguments = case["arguments"]
                yaml_path = arguments.get("yaml_path")
                if not isinstance(yaml_path, str) or arguments != y.upstream_arguments(yaml_path):
                    continue
                window = windows.get(f"{label}:{case['id']}")
                if window is None:
                    continue
                paths = [row.get("path") for row in window if row["kind"] == "http"]
                with self.subTest(version=version, case=case["id"]):
                    self.assertEqual(paths.count("/api/services/ha_mcp_tools/read_file"), 1, paths)
                    self.assertEqual(paths.count("/api/services/ha_mcp_tools/list_files"), 0)
                    self.assertLessEqual(set(paths), {"/api/services/ha_mcp_tools/read_file", "/api/services",
                                                      "/api/services/ha_mcp_tools/get_caller_token"})
                    checked += 1
        self.assertGreater(checked, 100)


class SdkSessionTests(unittest.IsolatedAsyncioTestCase):
    """The real pinned ClientSession against a synthetic ha-mcp peer."""

    async def test_reads_over_a_real_session(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                peer = YamlPeer(catalog(version), version)
                replies = {"recorder": d1_result(version, "main:recorder"),
                           "mqtt": d1_result(version, "main:mqtt")}
                peer.reply = lambda args: replies[args["yaml_path"]]
                gateway = UpstreamReadGateway()
                gateway.configure(
                    replace(_settings("synthetic-no-key"), ha_mcp_release_registry_enabled=False),
                    transport=upstream_read.McpReadGatewayTransport("", timeout_seconds=3,
                                                                    client_version="synthetic"),
                )
                with patch.object(upstream_read, "streamablehttp_client", peer.connect):
                    server = FastMCP(f"synthetic-hamcp162-sdk-{version}")
                    await gateway.initialize(server)
                    self.assertIn(TOOL, gateway._registered_tool_registry.snapshot())
                    # The published description carries the K1 dependency and
                    # the K7 disclosure; the siblings are never published.
                    published = {tool.name: tool for tool in await server.list_tools()}
                    self.assertEqual(published[TOOL].description, y.DESCRIPTION)
                    self.assertIn("admin caller token", published[TOOL].description)
                    # L1: the accepted K1 bootstrap is named; no blanket claim.
                    self.assertIn("POST ha_mcp_tools/get_caller_token", published[TOOL].description)
                    self.assertNotIn("zero service call", published[TOOL].description.lower())
                    self.assertIn("YAML 1.1", published[TOOL].description)
                    self.assertTrue(published[TOOL].annotations.readOnlyHint)
                    self.assertFalse(set(y.SIBLING_TOOLS) & set(published))
                    for name in y.SIBLING_TOOLS:
                        self.assertNotIn(name, gateway._registered_tool_registry.snapshot())
                    peer.calls.clear()
                    recorder = await call(gateway, TOOL, {"yaml_path": "recorder"})
                    mqtt = await call(gateway, TOOL, {"yaml_path": "mqtt"})
                    refusal = await call(gateway, TOOL, {"yaml_path": "x", "file": "secrets.yaml"})
                self.assertTrue(recorder["success"], recorder)
                self.assertTrue(mqtt["success"], mqtt)
                self.assertEqual(peer.calls, [(TOOL, y.upstream_arguments("recorder")),
                                              (TOOL, y.upstream_arguments("mqtt"))])
                refused(self, refusal, dispatched=False)
                self.assertEqual(refusal["error_code"], y.FILE_NOT_ALLOWED)
                self.assertGreater(mqtt["data"]["redaction"]["redacted_field_count"], 0)
                self.assertIsNone(__import__("re").search(D1_SECRET, json.dumps([recorder, mqtt])))
                self.assertEqual(mqtt["warnings"], [y.K7_WARNING])


class AuditTests(unittest.IsolatedAsyncioTestCase):
    """Group 14: audit attribution and fixed reason codes, through routing."""

    async def audited(self, gateway, arguments):
        tool = gateway._registered_tool_registry.snapshot()[TOOL]

        async def app(scope, receive, send):
            message = await receive()
            request = json.loads(message["body"])
            text = await tool.run(request["params"]["arguments"])
            body = json.dumps({"jsonrpc": "2.0", "id": request["id"],
                               "result": {"content": [{"type": "text", "text": text}], "isError": False}}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body, "more_body": False})

        replace_dynamic_upstream_capabilities(({
            "tool": TOOL, "upstream_tool": TOOL, "status": "delegated", "category": "upstream_read_gateway",
            "risk": "read", "operation_class": "automatic_read", "provider": "upstream_read_gateway",
            "fallback": "none",
        },), {})
        try:
            with tempfile.TemporaryDirectory() as directory:
                audit_path = Path(directory) / "audit.jsonl"
                routed = AuthenticatedMcpGateway(app, replace(access_settings(), audit_path=str(audit_path)),
                                                 AuditLogger(str(audit_path), ACCESS_SECRET))
                request = {"jsonrpc": "2.0", "id": "yaml-audit", "method": "tools/call",
                           "params": {"name": TOOL, "arguments": arguments}}
                delivered = False

                async def receive():
                    nonlocal delivered
                    if delivered:
                        return {"type": "http.disconnect"}
                    delivered = True
                    return {"type": "http.request", "body": json.dumps(request).encode(), "more_body": False}

                async def send(_message):
                    return None

                await routed({"type": "http", "method": "POST", "path": f"/{ACCESS_SECRET}/mcp",
                              "raw_path": f"/{ACCESS_SECRET}/mcp".encode(),
                              "headers": [(b"host", b"127.0.0.1:8100"), (b"content-type", b"application/json"),
                                          (b"x-request-id", b"yaml-audit")],
                              "client": ("127.0.0.1", 1)}, receive, send)
                serialized = audit_path.read_text(encoding="utf-8")
        finally:
            replace_dynamic_upstream_capabilities((), {})
        return serialized, json.loads(serialized.splitlines()[-1])

    async def test_audit_records(self):
        s = secret("AUD")
        gateway, transport = await yaml_gateway()
        transport.respond = lambda tool, args: ok(payload({"password": s, "broker": "b"}, args["yaml_path"]))
        serialized, record = await self.audited(gateway, {"yaml_path": "mqtt"})
        self.assertEqual(record["tool_name"], TOOL)
        self.assertEqual(record["result_status"], "success")
        self.assertEqual(record["parameters"]["provider"], "upstream_read_gateway")
        self.assertEqual(record["parameters"]["classification"], "automatic_read")
        self.assertEqual(record["parameters"]["argument_fields"], ["yaml_path"])
        assert_absent(self, serialized, s)
        for arguments, code in (({"yaml_path": "x", "file": "secrets.yaml"}, y.FILE_NOT_ALLOWED),
                                ({"yaml_path": "mqtt.password"}, y.PATH_SENSITIVE),
                                ({"yaml_path": "a..b"}, y.PATH_INVALID)):
            with self.subTest(code=code):
                transport.sent.clear()
                serialized, record = await self.audited(gateway, arguments)
                self.assertEqual(record["result_status"], "failure")
                self.assertEqual(record["error_code"], code)
                self.assertEqual(record["parameters"]["argument_fields"], sorted(arguments))
                for value in arguments.values():
                    self.assertNotIn(value, serialized)
                self.assertEqual(transport.sent, [])
        canary = "SYNAUDITTEXTQ7"
        transport.respond = lambda tool, args: {"isError": True, "content": [{"type": "text", "text": json.dumps(
            {"success": False, "error": {"code": "VALIDATION_FAILED", "message": canary}})}]}
        serialized, record = await self.audited(gateway, {"yaml_path": "recorder"})
        self.assertEqual(record["error_code"], y.VALIDATION_OR_UNAVAILABLE)
        self.assertNotIn(canary, serialized)



# ---------------------------------------------------------------------------
# HAMCP-162 Part 2 corrections: H1 (every client-visible field passes the
# pipeline) and H2 (markers are trusted by registry identity, never shape).

KNOWN = "Zq7v162KnownFixtureOnly42Xw"  # Codex H1 synthetic ha_token
ACCESS_KNOWN = "a1" * 32  # Codex H1 synthetic 64-character access secret
MARKER_KEYS = frozenset({"secret_ref", "env_var_ref", "default_redacted"})
FORGED_AFTER_CAP = [0] * 3997 + [{"env_var_ref": "SYNTHETIC_267", "extra": 0}]


def strings(value):
    """Every string key and value in a tree, and every marker name."""

    if type(value) is str:
        yield value
    elif isinstance(value, ReferenceMarker):
        yield value.name
    elif isinstance(value, EngineeringMarker):
        # H3: an L4 marker leaves L5 as its registered object.
        yield value.text
    elif isinstance(value, dict):
        for key, child in value.items():
            if type(key) is str:
                yield key
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


@contextmanager
def pipeline_spy():
    """Record every shared-sanitizer output and every seal vocabulary."""

    outputs, vocabulary = set(), set()
    real_sanitize, real_seal = y.sanitize_untrusted_data, y.seal

    def sanitize(value, **kwargs):
        result = real_sanitize(value, **kwargs)
        outputs.update(strings(result.value))
        return result

    def seal(value, known_secrets, vocabulary_=frozenset()):
        vocabulary.update(y.ENVELOPE_VOCABULARY | vocabulary_)
        return real_seal(value, known_secrets, vocabulary_)

    with patch.object(y, "sanitize_untrusted_data", sanitize), patch.object(y, "seal", seal):
        yield outputs, vocabulary


async def known_gateway(version, **overrides):
    """The Codex reproduction configuration: a configured known secret."""

    transport = Transport(catalog(version), version)
    gateway = UpstreamReadGateway()
    gateway.configure(replace(_settings("synthetic-no-key"), ha_mcp_release_registry_enabled=False,
                              **overrides), transport=transport)
    await gateway.initialize(FastMCP(f"synthetic-hamcp162-h1-{version}"))
    return gateway, transport


def h1_channels(known):
    """A configured known secret planted in every upstream and caller channel."""

    include = "!include ordinary.yaml"
    return [
        ("include_key", "sensor.value", {known: include}),
        ("include_nested_key", "sensor.value", {"a": {"b": {known: include}}}),
        ("include_list_key", "sensor.value", {"a": [{known: include}, {"c": include}]}),
        ("include_encoded_key", "sensor.value", {quote(known): include}),
        ("include_prefix_key", "sensor.value", {"x" + known + "y": include}),
        ("include_many", "sensor.value", {f"k{i:02d}{known}": include for i in range(20)}),
        ("include_value", "sensor.value", {"a": f"!include {known}.yaml"}),
        ("plain_key", "sensor.value", {known: 1, "a": known}),
        ("selected_path", "sensor." + known, "ordinary"),
        ("selected_path_deep", f"a.b.{known}.c", {known: include}),
        # H3: marker-shaped text carrying the secret, as keys and values.
        *((f"h3_{name}", "sensor.value", value) for name, value in h3_channels(known)),
    ]


def pct(text: str) -> str:
    """Percent-encode every character (``quote`` keeps letters and digits)."""

    return "".join(f"%{byte:02X}" for byte in text.encode("utf-8"))


def h3_markers(secret: str) -> list[tuple[str, str]]:
    """Marker-shaped text with and without the secret, in many spellings."""

    fullwidth = "".join(chr(ord(char) + 0xFEE0) if "!" <= char <= "~" else char
                        for char in f"[REDACTED:{secret}]")
    return [
        ("exact", f"[REDACTED:{secret}]"),
        ("no_secret", "[REDACTED:token]"),
        ("engineering_identical", y.CREDENTIAL_MARKER),
        ("collision_identical", MARKER_COLLISION),
        ("truncated", f"[TRUNCATED:{secret}]"),
        ("depth_identical", y.DEPTH_MARKER),
        ("embedded", f"see [REDACTED:{secret}] here"),
        ("marker_then_secret", f"[REDACTED:token] {secret}"),
        ("lowercase", f"[redacted:{secret}]"),
        ("spaced", f"[ REDACTED : {secret} ]"),
        ("open_only", f"[REDACTED {secret}"),
        ("encoded_1", enc(f"[REDACTED:{secret}]", 1)),
        ("encoded_2", enc(f"[REDACTED:{secret}]", 2)),
        ("encoded_3", enc(f"[REDACTED:{secret}]", 3)),
        ("encoded_all", pct(f"[REDACTED:{secret}]")),
        ("fullwidth", fullwidth),
        ("cyrillic", f"[R\u0415D\u0410\u0421T\u0415D:{secret}]"),
        ("greek_cherokee", f"[\u13a1\u0395\u13a0\u0391\u03f9\u03a4\u0395D:{secret}]"),
        ("zero_width", f"[RE\u200bDACTED:{secret}]"),
        ("combining", f"[R\u0301EDACTED:{secret}]"),
        ("bracket_lookalike", f"\u27e6REDACTED:{secret}\u27e7"),
        ("cjk_bracket", f"\u3010REDACTED:{secret}\u3011"),
        ("angle_truncated", f"{secret}...<truncated>"),
        ("scheme_prefix", f"mqtt://[REDACTED:{secret}]"),
    ]


def h3_channels(known: str) -> list[tuple[str, Any]]:
    marker = f"[REDACTED:{known}]"
    return [
        ("marker_key", {marker: "ordinary"}),
        ("marker_key_nested", {"outer": {marker: 1}}),
        ("marker_key_list", [{marker: 1}]),
        ("marker_value", {"a": marker}),
        ("marker_value_list", ["ok", marker]),
        ("marker_value_encoded", {"a": enc(marker, 2)}),
        ("marker_key_fullwidth", {h3_markers(known)[15][1]: 1}),
        ("marker_key_truncated", {f"[TRUNCATED:{known}]": 1}),
        ("encoded_key", {pct(known): 1}),
    ]


class EnvelopeSealTests(unittest.IsolatedAsyncioTestCase):
    """H1: no client-visible field of a reader response bypasses the pipeline."""

    def assert_sealed(self, result, known):
        text = json.dumps(result, ensure_ascii=False)
        assert_absent(self, text, known)
        self.assertNotIn(json.dumps(known)[1:-1], json.dumps(result))

    def assert_through_pipeline(self, result, outputs, vocabulary):
        allowed = outputs | vocabulary | MARKER_KEYS
        for text in strings(result):
            self.assertIn(text, allowed, f"returned around the pipeline: {text!r}")

    async def test_codex_h1_reproduction(self):
        for version in VERSIONS:
            gateway, transport = await known_gateway(version, ha_token=KNOWN)
            self.assertIn(KNOWN, gateway._known_secrets)
            for case, path, value in (("include_path", "sensor.value", {KNOWN: "!include ordinary.yaml"}),
                                      ("selected_path", "sensor." + KNOWN, "ordinary")):
                with self.subTest(version=version, case=case):
                    transport.respond = lambda tool, args, v=value: ok(payload(v, args["yaml_path"]))
                    result = await call(gateway, TOOL, {"yaml_path": path})
                    self.assertTrue(result["success"], result)
                    self.assert_sealed(result, KNOWN)
                    # The useful read survives: provider identity, the
                    # disclosure and the include limitation are intact.
                    self.assertEqual(result["metadata"]["provider"], "upstream_read_gateway")
                    self.assertEqual(result["warnings"], [y.K7_WARNING])
                    if case == "include_path":
                        self.assertEqual(result["data"]["yaml_path"], "sensor.value")
                        self.assertIn("include_not_followed:sensor.value.*", result["data"]["limitations"])
                        self.assertEqual(result["data"]["completeness"], "partial")
                    else:
                        self.assertEqual(result["data"]["yaml_path"], "sensor.*")
                        self.assertEqual(result["data"]["value"], "ordinary")
                        self.assertEqual(transport.sent[-1][1], y.upstream_arguments(path))

    async def test_codex_h1_and_h2_reproductions_over_a_real_session(self):
        for version in VERSIONS:
            for setting, known in (("ha_token", KNOWN), ("access_secret", ACCESS_KNOWN)):
                peer = YamlPeer(catalog(version), version)
                gateway = UpstreamReadGateway()
                gateway.configure(
                    replace(_settings("synthetic-no-key"), ha_mcp_release_registry_enabled=False,
                            **{setting: known}),
                    transport=upstream_read.McpReadGatewayTransport("", timeout_seconds=5,
                                                                    client_version="synthetic"),
                )
                with patch.object(upstream_read, "streamablehttp_client", peer.connect):
                    await gateway.initialize(FastMCP(f"synthetic-hamcp162-h1-sdk-{version}"))
                    for case, value in (("known_secret_include", {known: "!include ordinary.yaml"}),
                                        ("forged_after_cap", FORGED_AFTER_CAP)):
                        with self.subTest(version=version, setting=setting, case=case):
                            fixture = json.loads(json.dumps({"sensor": {"value": value}}))
                            peer.reply = lambda args, v=fixture["sensor"]["value"]: ok(
                                payload(v, args["yaml_path"]))
                            with pipeline_spy() as (outputs, vocabulary):
                                result = await call(gateway, TOOL, {"yaml_path": "sensor.value"})
                            self.assertTrue(result["success"], result)
                            self.assert_sealed(result, known)
                            self.assert_through_pipeline(result, outputs, vocabulary)
                            data = result["data"]
                            self.assertEqual(data["completeness"], "partial")
                            if case == "known_secret_include":
                                self.assertIn("include_not_followed:sensor.value.*", data["limitations"])
                            else:
                                self.assertEqual(data["value"][-1], y.COLLISION_MARKER)
                                self.assertNotIn("SYNTHETIC_267", json.dumps(result))
                                self.assertIn("value_truncated:1 nodes omitted", data["limitations"])
                                self.assertIn("reference_marker_collision", data["redaction"]["categories"])
                self.assertEqual(len(peer.calls), 2)

    async def test_known_secret_in_every_channel(self):
        for version in VERSIONS:
            for setting, known in (("ha_token", KNOWN), ("access_secret", ACCESS_KNOWN)):
                gateway, transport = await known_gateway(version, **{setting: known})
                for case, path, value in h1_channels(known):
                    with self.subTest(version=version, setting=setting, case=case):
                        transport.respond = lambda tool, args, v=value: ok(payload(v, args["yaml_path"]))
                        with pipeline_spy() as (outputs, vocabulary):
                            result = await call(gateway, TOOL, {"yaml_path": path})
                        self.assertTrue(result["success"], result)
                        self.assert_sealed(result, known)
                        self.assert_through_pipeline(result, outputs, vocabulary)
                        self.assertLessEqual(
                            sum(item.startswith("include_not_followed:")
                                for item in result["data"]["limitations"]),
                            y.MAX_INCLUDE_LIMITATIONS)
                # Upstream errors carrying the secret: fixed codes, no text.
                for error in ({"code": "CONFIG_INVALID", "message": f"bad {known} line 3, column 4"},
                              {"code": "VALIDATION_FAILED", "message": known},
                              f"Path not allowed: {known}", f"File does not exist: {known}", known):
                    with self.subTest(version=version, setting=setting, error=str(error)[:20]):
                        transport.respond = lambda tool, args, e=error: {"isError": True, "content": [
                            {"type": "text", "text": json.dumps({"success": False, "error": e})}]}
                        with pipeline_spy() as (outputs, vocabulary):
                            result = await call(gateway, TOOL, {"yaml_path": "sensor." + known})
                        self.assertFalse(result["success"], result)
                        self.assert_sealed(result, known)
                        self.assert_through_pipeline(result, outputs, vocabulary)
                        self.assertEqual(result["metadata"]["upstream_dispatch_occurred"], True)
                # Pre-dispatch refusals echo nothing either.
                transport.sent.clear()
                with pipeline_spy() as (outputs, vocabulary):
                    result = await call(gateway, TOOL, {"yaml_path": known, "file": known})
                self.assertFalse(result["success"], result)
                self.assert_sealed(result, known)
                self.assert_through_pipeline(result, outputs, vocabulary)
                self.assertEqual(transport.sent, [])

    async def test_known_secret_never_reaches_the_audit_record(self):
        gateway, transport = await known_gateway("8.5.0", ha_token=KNOWN)
        for case, path, value in h1_channels(KNOWN):
            with self.subTest(case=case):
                transport.respond = lambda tool, args, v=value: ok(payload(v, args["yaml_path"]))
                serialized, record = await AuditTests.audited(self, gateway, {"yaml_path": path})
                self.assertEqual(record["tool_name"], TOOL)
                assert_absent(self, serialized, KNOWN)

    def test_every_envelope_field_is_sealed(self):
        # Direct projection: every envelope string is a pipeline output or a
        # source constant, for each planted channel.
        for case, path, value in h1_channels(KNOWN):
            with self.subTest(case=case), pipeline_spy() as (outputs, vocabulary):
                result = y.project_success(ok(payload(value, path)), path, known_secrets=(KNOWN,))
                assert_absent(self, json.dumps(result.envelope), KNOWN)
                for text in strings(result.envelope):
                    self.assertIn(text, outputs | vocabulary | MARKER_KEYS)
        self.assertEqual(set(result.envelope), {"file", "yaml_path", "source", "found", "value",
                                                "completeness", "limitations", "redaction"})

    def test_path_projection(self):
        ks = (KNOWN,)
        self.assertEqual(y.project_path(["sensor", "value"], ks), "sensor.value")
        self.assertEqual(y.project_path(["mqtt", "broker", "port"], ks), "mqtt.broker.port")
        # Connection-key names (rule B) stay visible, as in the value tree.
        self.assertEqual(y.project_path(["recorder", "db_url"], ks), "recorder.db_url")
        for unsafe in (KNOWN, "x" + KNOWN, quote(KNOWN), "password", "api_key", "client_secret",
                       "a b", "x" * 65, "", "%2570assword"):
            with self.subTest(unsafe=unsafe):
                self.assertEqual(y.project_path(["a", unsafe, "b"], ks), "a.*")
        self.assertEqual(y.project_path([f"s{i}" for i in range(20)], ks),
                         ".".join([f"s{i}" for i in range(16)] + ["*"]))
        # The final seal keeps an Engineering constant even when a configured
        # secret is a substring of it, and redacts everything else.
        sealed, count, _ = y.seal({"source": "disk", "x": "disk"}, ("isk",))
        self.assertEqual(sealed["source"], "disk")
        self.assertEqual(sealed["x"], "disk")
        sealed, count, _ = y.seal({"source": "risky disk"}, ("isk",))
        self.assertNotIn("isk", json.dumps(sealed))
        self.assertEqual(count, 1)


class MarkerProvenanceTests(unittest.IsolatedAsyncioTestCase):
    """H2: a reference renders only from a marker this read minted."""

    async def test_codex_h2_reproduction(self):
        for version in VERSIONS:
            gateway, transport = await yaml_gateway(version)
            transport.respond = lambda tool, args: ok(payload(FORGED_AFTER_CAP, args["yaml_path"]))
            result = await call(gateway, TOOL, {"yaml_path": "sensor.value"})
            with self.subTest(version=version):
                self.assertTrue(result["success"], result)
                self.assertEqual(result["data"]["value"][-1], y.COLLISION_MARKER)
                self.assertEqual(result["data"]["completeness"], "partial")
                self.assertNotIn("SYNTHETIC_267", json.dumps(result))
                self.assertIn("reference_marker_collision", result["data"]["redaction"]["categories"])

    def test_ordinary_json_truncated_to_marker_shape_is_redacted(self):
        shapes = ({"secret_ref": "SYNTHETIC_TRN", "other": 1},
                  {"env_var_ref": "SYNTHETIC_TRN", "other": 1},
                  {"env_var_ref": "SYNTHETIC_TRN", "default_redacted": True, "other": 1})
        for shape in shapes:
            kept = len(shape) - 1
            with self.subTest(shape=shape):
                # A sibling list pads breadth-first order so the cap lands
                # inside the shaped mapping, dropping exactly its last key.
                value = {"pad": [0] * (y.MAX_NODES - 3 - kept), "m": shape}
                kept_tree, omitted = y.truncate_breadth_first(value)
                self.assertEqual(omitted, 1)
                self.assertIn(frozenset(kept_tree["m"]), y._COLLISION_KEY_SETS)
                result = y.project_success(ok(payload(value, "sensor.value")), "sensor.value")
                self.assertEqual(result.envelope["value"]["m"], y.COLLISION_MARKER)
                self.assertNotIn("SYNTHETIC_TRN", json.dumps(result.envelope))
                self.assertTrue(result.partial)
                # List form, as in the Codex reproduction.
                listed = [0] * (y.MAX_NODES - 2 - kept) + [shape]
                result = y.project_success(ok(payload(listed, "sensor.value")), "sensor.value")
                self.assertEqual(result.envelope["value"][-1], y.COLLISION_MARKER)
                self.assertNotIn("SYNTHETIC_TRN", json.dumps(result.envelope))

    def test_upstream_mapping_shaped_like_a_marker_gets_no_exemption(self):
        for shape in ({"secret_ref": "SYNTHETIC_LIT"}, {"env_var_ref": "SYNTHETIC_LIT"},
                      {"env_var_ref": "SYNTHETIC_LIT", "default_redacted": True}):
            for where in ("top", "nested", "list"):
                value = {"top": shape, "nested": {"a": shape}, "list": [shape]}[where]
                with self.subTest(shape=shape, where=where):
                    result = y.project_success(ok(payload(value, "sensor.value")), "sensor.value")
                    self.assertNotIn("SYNTHETIC_LIT", json.dumps(result.envelope))
                    self.assertIn(y.COLLISION_MARKER, json.dumps(result.envelope))

    def test_render_trusts_registry_identity_only(self):
        registry, account = ReferenceMarkerRegistry(), y._Accounting()
        minted = registry.mint(kind="secret", name="broker_pw")
        self.assertEqual(y.render_markers({"a": minted}, registry, account),
                         {"a": {"secret_ref": "broker_pw"}})
        self.assertEqual(account.redacted, 0)
        twin = ReferenceMarker(kind="secret", name="broker_pw", default_redacted=False)
        foreign = ReferenceMarkerRegistry().mint(kind="secret", name="broker_pw")
        for stranger in (twin, foreign, {"secret_ref": "broker_pw"}, {"env_var_ref": "N"},
                         {"env_var_ref": "N", "default_redacted": True}):
            with self.subTest(stranger=repr(stranger)):
                account = y._Accounting()
                self.assertEqual(y.render_markers({"a": [stranger]}, registry, account),
                                 {"a": [y.COLLISION_MARKER]})
                self.assertEqual(account.categories, {"reference_marker_collision"})

    async def test_genuine_markers_still_pass(self):
        for version in VERSIONS:
            gateway, transport = await yaml_gateway(version)
            value = {"password": "!secret broker_pw", "user": "!env_var SYNTHETIC_USER",
                     "pin": "!env_var SYNTHETIC_PIN 1234", "list": ["!secret other_pw"],
                     "pad": [0] * 100}
            transport.respond = lambda tool, args: ok(payload(value, args["yaml_path"]))
            result = await call(gateway, TOOL, {"yaml_path": "mqtt"})
            with self.subTest(version=version):
                self.assertTrue(result["success"], result)
                rendered = result["data"]["value"]
                self.assertEqual(rendered["password"], {"secret_ref": "broker_pw"})
                self.assertEqual(rendered["user"], {"env_var_ref": "SYNTHETIC_USER"})
                self.assertEqual(rendered["pin"], {"env_var_ref": "SYNTHETIC_PIN", "default_redacted": True})
                self.assertEqual(rendered["list"], [{"secret_ref": "other_pw"}])
                self.assertNotIn("1234", json.dumps(rendered))
                self.assertEqual(result["data"]["completeness"], "complete")


def decoded_texts(text: str) -> list[str]:
    forms = [text]
    for _ in range(4):
        forms.append(unquote(forms[-1]))
    return forms


class RedactionMarkerIdentityTests(unittest.IsolatedAsyncioTestCase):
    """H3: a redaction marker is trusted by registry identity, never by text."""

    def assert_secret_absent(self, value, secret):
        text = json.dumps(value, ensure_ascii=False)
        for form in decoded_texts(text):
            assert_absent(self, form, secret)
            self.assertNotIn(secret.casefold(), unicodedata.normalize("NFKC", form).casefold())

    def assert_only_engineering_markers(self, value):
        """Every string a client could read as a marker is a valid Engineering marker."""

        for text in strings(value):
            if sanitization.marker_like_text(text):
                self.assertTrue(EngineeringMarker(text).valid(), f"marker-shaped text kept: {text!r}")

    async def test_codex_h3_reproductions_over_a_real_session(self):
        """The six Codex reproductions: known secret in a marker-shaped key."""

        key = f"[REDACTED:{ACCESS_KNOWN}]"
        for version in VERSIONS:
            for setting, known in (("access_secret", ACCESS_KNOWN), ("ha_token", ACCESS_KNOWN)):
                peer = YamlPeer(catalog(version), version)
                gateway = UpstreamReadGateway()
                gateway.configure(
                    replace(_settings("synthetic-no-key"), ha_mcp_release_registry_enabled=False,
                            **{setting: known}),
                    transport=upstream_read.McpReadGatewayTransport("", timeout_seconds=5,
                                                                    client_version="synthetic"),
                )
                with patch.object(upstream_read, "streamablehttp_client", peer.connect):
                    await gateway.initialize(FastMCP(f"synthetic-hamcp162-h3-{version}"))
                    for shape, value, expected in (
                        ("direct", {key: "ordinary"}, MARKER_COLLISION),
                        ("nested", {"outer": {key: 1}}, {"outer": MARKER_COLLISION}),
                        ("list", [{key: 1}], [MARKER_COLLISION]),
                    ):
                        with self.subTest(version=version, setting=setting, shape=shape):
                            peer.reply = lambda args, v=value: ok(payload(v, args["yaml_path"]))
                            with pipeline_spy() as (outputs, vocabulary):
                                result = await call(gateway, TOOL, {"yaml_path": "sensor.value"})
                            self.assertTrue(result["success"], result)
                            self.assertNotIn(ACCESS_KNOWN, json.dumps(result))
                            EnvelopeSealTests.assert_sealed(self, result, ACCESS_KNOWN)
                            EnvelopeSealTests.assert_through_pipeline(self, result, outputs, vocabulary)
                            data = result["data"]
                            self.assertEqual(data["value"], expected)
                            self.assertIn("redaction_marker_collision", data["redaction"]["categories"])
                            self.assertEqual(data["redaction"]["redacted_field_count"], 1)
                            self.assertEqual(result["metadata"]["provider"], "upstream_read_gateway")
                self.assertEqual(len(peer.calls), 3)

    def test_marker_shaped_keys_and_values_are_withheld(self):
        known = (ACCESS_KNOWN,)
        for name, marker in h3_markers(ACCESS_KNOWN):
            cases = (
                ("value", {"a": marker}, {"a": MARKER_COLLISION}),
                ("list_value", ["ok", marker], ["ok", MARKER_COLLISION]),
                ("connection_value", {"db_url": marker}, {"db_url": MARKER_COLLISION}),
                # L4 withholds it; L5 relabels by the key, as for any value.
                ("credential_value", {"password": marker}, {"password": "[REDACTED:password]"}),
                ("key", {marker: "ordinary"}, MARKER_COLLISION),
                ("nested_key", {"outer": {marker: 1}, "b": 2}, {"outer": MARKER_COLLISION, "b": 2}),
                ("list_key", [{marker: 1}, {"c": 3}], [MARKER_COLLISION, {"c": 3}]),
            )
            for where, value, expected in cases:
                with self.subTest(marker=name, where=where):
                    result = project(value, known_secrets=known)
                    self.assertEqual(result.envelope["value"], expected)
                    self.assert_secret_absent(result.envelope, ACCESS_KNOWN)
                    self.assert_only_engineering_markers(result.envelope)
                    self.assertFalse(result.sanitization_failed)
                    self.assertGreaterEqual(result.redacted_field_count, 1)

    def test_l4_withholds_marker_shaped_text_on_its_own(self):
        # Layer independence: L4 alone withholds marker-shaped text; it emits
        # only registry-minted markers, never a marker-shaped string.
        for name, marker in h3_markers(ACCESS_KNOWN):
            for where, value in (("value", marker), ("key", {marker: 1}),
                                 ("nested_key", {"o": {marker: 1}})):
                with self.subTest(marker=name, where=where):
                    registry = ReferenceMarkerRegistry()
                    walker = y._L4((ACCESS_KNOWN,), registry)
                    redacted = walker.redact(value, ["sensor", "value"], ["sensor", "value"], 1)
                    emitted = redacted["o"] if where == "nested_key" else redacted
                    self.assertIs(type(emitted), EngineeringMarker)
                    self.assertTrue(registry.emitted(emitted))
                    self.assertEqual(emitted.text, MARKER_COLLISION)
                    self.assertEqual(walker.account.categories, {"redaction_marker_collision"})

    def test_l5_strict_mode_scans_marker_shaped_text_on_its_own(self):
        # Layer independence: the strict sanitizer alone trusts no marker text.
        for name, marker in h3_markers(ACCESS_KNOWN):
            for where, value, expected in (
                ("value", marker, MARKER_COLLISION),
                ("key", {marker: "ordinary"}, {MARKER_COLLISION: "ordinary"}),
                ("sensitive_key_value", {"token": marker}, {"token": "[REDACTED:token]"}),
                ("list", [marker], [MARKER_COLLISION]),
            ):
                with self.subTest(marker=name, where=where):
                    result = sanitize_untrusted_data(
                        value, known_secrets=(ACCESS_KNOWN,), identity_markers_only=True)
                    self.assertEqual(result.value, expected)
                    self.assert_secret_absent(result.value, ACCESS_KNOWN)
                    self.assertFalse(result.failed_closed)
        # A sensitive key's own text passes the scan too (default keeps it raw).
        odd = "~token~"
        default = sanitize_untrusted_data({odd: "x"}, known_secrets=(odd,))
        self.assertEqual(default.value, {odd: "[REDACTED:token]"})
        strict = sanitize_untrusted_data({odd: "x"}, known_secrets=(odd,), identity_markers_only=True)
        self.assertEqual(strict.value, {"[REDACTED:token]": "[REDACTED:token]"})
        strict = sanitize_untrusted_data({"[REDACTED:x] token": "x"}, identity_markers_only=True)
        self.assertEqual(strict.value, {MARKER_COLLISION: "x"})

    def test_strict_mode_matches_default_on_ordinary_text(self):
        corpus = [
            "plain text", "Authorization: Bearer abc.def", "bearer SYNTHETICTOKEN123",
            "Cookie: a=b; c=d", "eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl",
            "https://user:pw@example.invalid/x", "https://u%3Ap@example.invalid/",
            "https://example.invalid/?access_token=abc&x=1", "/api/webhook/synthetic-hook-id",
            "local webhook id ABCDEFGHIJKLMNOPQRSTUV", "/auth/login_flow/abc123",
            "MT:Y.K9042C00KA0648G00", "setup code: 1234-567-8901", 'token="abc" password=xyz',
            f"{KNOWN} and {KNOWN}", f"Bearer {KNOWN}", f"Bearer {KNOWN} Bearer {KNOWN}",
            "x" * 50,
        ]
        for text in corpus:
            for max_string in (None, 20):
                with self.subTest(text=text, max_string=max_string):
                    default = sanitize_untrusted_data(text, known_secrets=(KNOWN,), max_string=max_string)
                    strict = sanitize_untrusted_data(text, known_secrets=(KNOWN,), max_string=max_string,
                                                     identity_markers_only=True)
                    self.assertEqual(strict.value, default.value)
                    self.assertEqual(strict.redaction_categories, default.redaction_categories)
                    self.assertEqual(strict.redacted_field_count, default.redacted_field_count)
                    self.assertEqual(strict.truncated_field_count, default.truncated_field_count)

    def test_emitted_markers_are_skipped_by_identity(self):
        # Known secrets that are parts of marker text cannot rewrite an
        # emitted marker: later rules skip emitted pieces by type.
        result = sanitize_untrusted_data("a token b", known_secrets=("token", "REDACTED", "["),
                                         identity_markers_only=True)
        self.assertEqual(result.value, "a [REDACTED:token] b")
        result = sanitize_untrusted_data("Bearer abc", known_secrets=("REDACTED",),
                                         identity_markers_only=True)
        self.assertEqual(result.value, "Bearer [REDACTED:token]")

    def test_default_mode_is_unchanged_for_other_callers(self):
        # Pinned so any change to the shared default is a visible decision.
        self.assertEqual(sanitize_untrusted_data("[REDACTED:abc] x").value, "[REDACTED:abc] x")
        self.assertEqual(sanitize_untrusted_data({"token": "v"}).value, {"token": "[REDACTED:token]"})
        self.assertEqual(sanitize_untrusted_data(f"[REDACTED:token] {KNOWN}", known_secrets=(KNOWN,)).value,
                         "[REDACTED:token]")
        self.assertEqual(
            sanitize_untrusted_data(EngineeringMarker(y.CREDENTIAL_MARKER)).value, MARKER_COLLISION)
        with self.assertRaises(TypeError):
            sanitize_untrusted_data("x", identity_markers_only=1)

    def test_upstream_string_identical_to_an_engineering_marker_is_not_trusted(self):
        constants = (y.CREDENTIAL_MARKER, y.ENCODING_MARKER, y.CONNECTION_STRING_MARKER,
                     "mqtt" + y.CONNECTION_URI_SUFFIX, y.REFERENCE_MALFORMED_MARKER, y.COLLISION_MARKER,
                     y.INCLUDE_MALFORMED_MARKER, y.DEPTH_MARKER, MARKER_COLLISION,
                     sanitization.SANITIZATION_FAILURE_MARKER, "[REDACTED:token]")
        for constant in constants:
            with self.subTest(constant=constant):
                result = project({"a": constant, constant: 1}, known_secrets=(KNOWN,))
                self.assertEqual(result.envelope["value"], MARKER_COLLISION)
                result = project({"a": constant}, known_secrets=(KNOWN,))
                self.assertEqual(result.envelope["value"], {"a": MARKER_COLLISION})
                self.assertIn("redaction_marker_collision", result.redaction_categories)
                self.assertEqual(result.redacted_field_count, 1)

    def test_l5_and_render_trust_registry_identity_only(self):
        registry = ReferenceMarkerRegistry()
        minted = registry.mint_marker(y.CREDENTIAL_MARKER)
        kept = sanitize_untrusted_data({"a": [minted]}, preserve_reference_markers=registry,
                                       identity_markers_only=True)
        self.assertIs(kept.value["a"][0], minted)
        account = y._Accounting()
        self.assertEqual(y.render_markers(kept.value, registry, account), {"a": [y.CREDENTIAL_MARKER]})
        self.assertEqual(account.redacted, 0)

        class Sub(EngineeringMarker):
            pass

        strangers = (EngineeringMarker(y.CREDENTIAL_MARKER),
                     ReferenceMarkerRegistry().mint_marker(y.CREDENTIAL_MARKER),
                     Sub(y.CREDENTIAL_MARKER), y.CREDENTIAL_MARKER)
        for stranger in strangers:
            with self.subTest(stranger=repr(stranger)):
                result = sanitize_untrusted_data({"a": [stranger]}, preserve_reference_markers=registry,
                                                 identity_markers_only=True)
                self.assertEqual(result.value, {"a": [MARKER_COLLISION]})
                account = y._Accounting()
                if type(stranger) is not str:
                    self.assertEqual(y.render_markers({"a": [stranger]}, registry, account),
                                     {"a": [MARKER_COLLISION]})
                    self.assertEqual(account.categories, {"redaction_marker_collision"})
        # A minted marker whose text is not a marker grammar is withheld.
        forged = registry.mint_marker(f"x{KNOWN}")
        self.assertFalse(forged.valid())
        self.assertEqual(sanitize_untrusted_data([forged], preserve_reference_markers=registry,
                                                 identity_markers_only=True).value, [MARKER_COLLISION])
        self.assertEqual(y.render_markers([forged], registry, y._Accounting()), [MARKER_COLLISION])

    def test_seal_neutralizes_marker_shaped_metadata(self):
        marker = f"[REDACTED:{ACCESS_KNOWN}]"
        for name, text in h3_markers(ACCESS_KNOWN):
            with self.subTest(marker=name):
                sealed, count, categories = y.seal({"upstream_version": text, text: 1}, (ACCESS_KNOWN,))
                self.assertEqual(sealed, {"upstream_version": MARKER_COLLISION, MARKER_COLLISION: 1})
                self.assert_secret_absent(sealed, ACCESS_KNOWN)
                # Byte-identical replacement text is not a visible change.
                self.assertEqual(count, 0 if text == MARKER_COLLISION else 2)
                if count:
                    self.assertIn("redaction_marker_collision", categories)
        body = {"success": False, "metadata": {"upstream_version": marker}, "data": None}
        sealed = y.seal_response(body, (ACCESS_KNOWN,))
        self.assertEqual(sealed["metadata"]["upstream_version"], MARKER_COLLISION)

    def test_marker_like_predicate(self):
        for name, text in h3_markers("SyntheticOnly"):
            with self.subTest(text=text):
                self.assertTrue(sanitization.marker_like_text(text), name)
        for text in ("the value was redacted by the admin", "value_truncated:3 nodes omitted",
                     "strings_truncated:2", "include_not_followed_truncated", "[note] truncated",
                     "sqlite:////config/home-assistant_v2.db", "!secret broker_pw", "mqtt://broker",
                     "REDACTED", "token"):
            with self.subTest(text=text):
                self.assertFalse(sanitization.marker_like_text(text))
        # Still encoded after the bound: fail closed.
        self.assertTrue(sanitization.marker_like_text(enc("[x]", 6)))

    def test_sweep_encoded_and_lookalike_key_text_is_scanned(self):
        fullwidth = "".join(chr(ord(char) + 0xFEE0) for char in KNOWN)
        for name, key in (("percent_all", pct(KNOWN)), ("double", enc(pct(KNOWN), 1)),
                          ("fullwidth", fullwidth)):
            with self.subTest(key=name):
                result = project({key: 1, "b": 2}, known_secrets=(KNOWN,))
                self.assertEqual(result.envelope["value"], "[REDACTED:token]")
                self.assert_secret_absent(result.envelope, KNOWN)
        # A raw key keeps L5's key-only replacement (the useful H1 behavior).
        result = project({"x" + KNOWN: 1, "b": 2}, known_secrets=(KNOWN,))
        self.assertEqual(result.envelope["value"], {"x[REDACTED:token]": 1, "b": 2})

    def test_sweep_sqlite_shape_still_gets_the_scanners(self):
        secret = "a1" * 32
        value = {"recorder": {"db_url": f"sqlite:////config/{secret}.db"}}
        result = project(value, "recorder", known_secrets=(secret,))
        self.assertEqual(result.envelope["value"], {"recorder": {"db_url": "[REDACTED:token]"}})
        self.assert_secret_absent(result.envelope, secret)
        # The reviewed R11 shape stays visible.
        clean = {"db_url": "sqlite:////config/home-assistant_v2.db"}
        self.assertEqual(project(clean, "recorder", known_secrets=(secret,)).envelope["value"], clean)

    def test_sweep_kept_scheme_is_scanned(self):
        for secret, value in (("zq7v162scheme", "zq7v162scheme://user:pw@host"),
                              ("Zq7vScheme162", "Zq7vScheme162://user:pw@host")):
            with self.subTest(secret=secret):
                result = project({"db_url": value}, known_secrets=(secret,))
                self.assertEqual(result.envelope["value"], {"db_url": y.CONNECTION_STRING_MARKER})
                self.assert_secret_absent(result.envelope, secret)
        result = project({"db_url": "mqtt://user:pw@host"}, known_secrets=(KNOWN,))
        self.assertEqual(result.envelope["value"], {"db_url": "mqtt://[REDACTED:connection_uri]"})

    def test_relabel_accounting_is_by_identity(self):
        # L4 redacts the credential key; L5 relabels it; counted once.
        self.assertEqual(project({"token": "synthetic"}).redacted_field_count, 1)
        self.assertEqual(project({"matter": {"payload": "[REDACTED:x]"}}, "sensor.value").redacted_field_count, 1)
        self.assertEqual(project({"a": "[REDACTED:token]", "token": "x"}).redacted_field_count, 2)


if __name__ == "__main__":
    unittest.main()
