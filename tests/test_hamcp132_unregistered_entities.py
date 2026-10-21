"""HAMCP-132 read-only unregistered-entity report (``get_unregistered_entities``).

Fixtures model the pinned Home Assistant Core 2026.10.1 shapes of
``GET /api/states``, ``config/entity_registry/list``, ``entity/source``
(``{entity_id: {"domain": platform}}``) and ``search/related`` with
``item_type: entity``. Everything is synthetic; no live Home Assistant.
Credential-shaped values are assembled at test time.
"""

from __future__ import annotations

import ast
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests"), str(ROOT)]

import httpx  # noqa: E402

import hamcp162_synthetic as syn  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.capabilities import (  # noqa: E402
    BETA_NATIVE_CAPABILITIES,
    CAPABILITY_PROVIDER_MATRIX,
)
from ha_mcp_engineering.clients import websocket as ws  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.entity_reconciliation import contracts as c  # noqa: E402
from ha_mcp_engineering.entity_reconciliation.service import (  # noqa: E402
    ENTITY_RECONCILIATION,
    EntityReconciliationRuntime,
    EntityReconciliationService,
)
from ha_mcp_engineering.errors import (  # noqa: E402
    AuthorizationError,
    ErrorCode,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ha_mcp_engineering.ha_core_readmission.routes import STATIC_TOOL_CORE_REQUIREMENTS  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402
from ha_mcp_engineering.tools import entity_reconciliation as public  # noqa: E402
from ha_mcp_engineering.tools import get_registered_server, registered_tools  # noqa: E402

PACKAGE = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/entity_reconciliation"
TOOL = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/tools/entity_reconciliation.py"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
HA_TOKEN = syn.needle("hatoken")
ACCESS_SECRET = syn.needle("accesssecret")
ATTRIBUTE_SECRET = syn.needle("stateattributesecret")
UNKNOWN_VALUE = syn.needle("unknownvalue")
UNKNOWN_KEY = syn.needle("unknownkey")
NEEDLES = (HA_TOKEN, ACCESS_SECRET, ATTRIBUTE_SECRET, UNKNOWN_VALUE, UNKNOWN_KEY)
ENTRY = "01JSYNTHETICCONFIGENTRY0001"
OTHER_ENTRY = "01JSYNTHETICCONFIGENTRY0002"


def state(entity_id, value="on"):
    return {"entity_id": entity_id, "state": value,
            "attributes": {"friendly_name": entity_id, "api_key": ATTRIBUTE_SECRET},
            "last_changed": "2026-10-20T11:00:00+00:00", "context": {"id": "01JSYNTHETICCONTEXT"}}


def states(*entity_ids):
    return [state(entity_id) for entity_id in entity_ids]


def registry(*entity_ids):
    return [{"entity_id": entity_id, "platform": "synthetic", "config_entry_id": OTHER_ENTRY,
             "unique_id": entity_id + "_uid"} for entity_id in entity_ids]


REGISTERED = ("light.kitchen", "sensor.temperature")
UNREGISTERED = ("binary_sensor.yaml_door", "light.yaml_group", "sensor.directly_written",
                "sensor.yaml_template")


def healthy():
    return {
        "states": states(*REGISTERED, *UNREGISTERED),
        "config/entity_registry/list": registry(*REGISTERED),
        "entity/source": {"light.kitchen": {"domain": "hue"}, "sensor.temperature": {"domain": "zha"},
                          "binary_sensor.yaml_door": {"domain": "template"},
                          "light.yaml_group": {"domain": "group"},
                          "sensor.yaml_template": {"domain": "template"}},
        "search/related": {"binary_sensor.yaml_door": (True, {"config_entry": [ENTRY]}),
                           "light.yaml_group": (True, {"config_entry": [], "entity": ["light.kitchen"]}),
                           "sensor.yaml_template": (True, {"config_entry": [ENTRY, OTHER_ENTRY]})},
    }


class SyntheticCore:
    def __init__(self):
        self.available, self.generation = True, 1
        self.current_observation = SimpleNamespace(version="2026.10.1")

    def route_status(self, requirements):
        assert requirements == c.CORE_REQUIREMENTS
        return {"available": self.available, "generation": self.generation}


class SyntheticRest:
    """Answers only ``GET /states``."""

    def __init__(self, values, calls):
        self.values, self.calls, self.hook = values, calls, None

    async def request(self, method, path, **kwargs):
        assert (method, path, kwargs) == ("GET", "/states", {}), (method, path, kwargs)
        self.calls.append("states")
        if self.hook:
            self.hook("states")
        value = self.values["states"]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


class SyntheticWebSocket:
    def __init__(self, values, calls):
        self.values, self.calls, self.hook = values, calls, None
        self.batches = []

    async def command(self, payload):
        assert payload in ({"type": "config/entity_registry/list"}, {"type": "entity/source"}), payload
        kind = payload["type"]
        self.calls.append(kind)
        if self.hook:
            self.hook(kind)
        value = self.values[kind]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    async def command_batch(self, payloads):
        assert all(ws.batch_command_allowed(payload) for payload in payloads), payloads
        self.calls.append("search/related")
        self.batches.append(copy.deepcopy(payloads))
        if self.hook:
            self.hook("search/related")
        value = self.values["search/related"]
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value(payloads)
        return [copy.deepcopy(value.get(item["item_id"], (True, {}))) for item in payloads]


def setup(values=None, *, known_secrets=(HA_TOKEN, ACCESS_SECRET), caller="synthetic_authenticated_caller"):
    calls = []
    rest = SyntheticRest(values if values is not None else healthy(), calls)
    socket = SyntheticWebSocket(rest.values, calls)
    core = SyntheticCore()
    service = EntityReconciliationService(rest, socket, core, known_secrets=known_secrets, clock=lambda: NOW)
    telemetry, token = begin_request()
    telemetry.caller_id = caller
    telemetry.core_dispatch_authorizer = lambda: core.available
    return service, rest, socket, core, telemetry, token, calls


def settings(**values):
    return Settings(**{**dict(ha_url="http://synthetic.invalid", ha_token=HA_TOKEN,
        access_secret=ACCESS_SECRET, port=8100, audit_path="unused",
        rate_limit_per_minute=1000, rate_limit_burst=1000, destructive_services=frozenset()), **values})


def source(report, name):
    return next(item for item in report["sources"] if item["source"] == name)


class Base(unittest.IsolatedAsyncioTestCase):
    async def run_report(self, values=None, domains=None, limit=c.DEFAULT_LIMIT, **kwargs):
        service, rest, socket, core, telemetry, token, calls = setup(values, **kwargs)
        self.addCleanup(end_request, token)
        report = await service.report(domains, limit)
        self.socket, self.core, self.telemetry, self.calls = socket, core, telemetry, calls
        text = json.dumps(report)
        for value in NEEDLES:
            self.assertNotIn(value, text)
        self.assertNotIn("api_key", text)
        return report

    @staticmethod
    def rows(report):
        return {row["entity_id"]: row for row in report["entities"]}


class ReconciliationTests(Base):
    async def test_healthy_reconciliation_and_call_order(self):
        report = await self.run_report()
        self.assertEqual(self.calls, ["states", "config/entity_registry/list", "entity/source", "search/related"])
        self.assertEqual(self.socket.batches, [[c.search_payload(e) for e in
                                                ("binary_sensor.yaml_door", "light.yaml_group",
                                                 "sensor.yaml_template")]])
        self.assertTrue(report["complete"], report["omissions"])
        self.assertEqual(report["schema"], c.SCHEMA)
        self.assertEqual(report["counts"], {"states": 6, "registry_entries": 2, "unregistered": 4,
                                            "unregistered_in_scope": 4, "listed": 4,
                                            "directly_written_states": 1})
        self.assertEqual([row["entity_id"] for row in report["entities"]], sorted(UNREGISTERED))
        rows = self.rows(report)
        self.assertEqual(rows["binary_sensor.yaml_door"], {
            "entity_id": "binary_sensor.yaml_door", "state_present": True, "registry_present": False,
            "definition_location": "not_determinable", "platform": "template", "platform_status": "entity_source",
            "config_entry_id": ENTRY, "config_entry_status": "resolved"})
        self.assertEqual((rows["light.yaml_group"]["config_entry_id"], rows["light.yaml_group"]["config_entry_status"]),
                         (None, "none_related"))
        self.assertEqual((rows["sensor.yaml_template"]["config_entry_id"],
                          rows["sensor.yaml_template"]["config_entry_status"]), (None, "ambiguous"))
        self.assertEqual(rows["sensor.directly_written"]["platform_status"], "directly_written_state")
        self.assertEqual(rows["sensor.directly_written"]["config_entry_status"], "not_applicable")
        self.assertEqual(report["scope"], {"domains": None, "limit": c.DEFAULT_LIMIT})
        self.assertFalse(report["truncated"])
        self.assertEqual(report["coverage"]["candidate_definitions"], "not offered in this release")
        self.assertTrue(all(s["status"] == "complete" for s in report["sources"]))
        self.assertEqual(self.telemetry.result_status, "success")
        self.assertEqual(self.telemetry.audit_context["upstream_calls"], 0)
        self.assertEqual(self.telemetry.audit_context["entity_count"], 4)
        self.assertEqual(self.telemetry.audit_context["fallback"], "none")

    async def test_nothing_unregistered_reads_no_platforms(self):
        values = healthy() | {"states": states(*REGISTERED)}
        report = await self.run_report(values)
        self.assertEqual(self.calls, ["states", "config/entity_registry/list"])
        self.assertEqual(report["entities"], [])
        self.assertTrue(report["complete"])

    async def test_only_directly_written_states_issue_no_search(self):
        values = healthy() | {"states": states(*REGISTERED, "sensor.directly_written")}
        report = await self.run_report(values)
        self.assertEqual(self.calls, ["states", "config/entity_registry/list", "entity/source"])
        self.assertEqual(report["counts"]["directly_written_states"], 1)
        self.assertTrue(report["complete"])

    async def test_domain_scope_and_limit_truncation(self):
        report = await self.run_report(domains=["sensor", "climate"])
        self.assertEqual([row["entity_id"] for row in report["entities"]],
                         ["sensor.directly_written", "sensor.yaml_template"])
        self.assertEqual(report["counts"]["unregistered"], 4)
        self.assertEqual(report["counts"]["unregistered_in_scope"], 2)
        self.assertEqual(report["scope"], {"domains": ["sensor", "climate"], "limit": c.DEFAULT_LIMIT})
        self.assertTrue(report["complete"])
        report = await self.run_report(limit=1)
        self.assertEqual([row["entity_id"] for row in report["entities"]], ["binary_sensor.yaml_door"])
        self.assertTrue(report["truncated"])
        self.assertEqual(report["counts"]["listed"], 1)
        self.assertIn("entities_not_listed_limit", report["omissions"])
        self.assertFalse(report["complete"])
        self.assertEqual(self.socket.batches, [[c.search_payload("binary_sensor.yaml_door")]])

    async def test_malformed_rows_are_counted_not_reconciled(self):
        values = healthy()
        values["states"] += [{"entity_id": "Not.Valid"}, {"state": "on"}, ["light.x"], None,
                             {"entity_id": 7}]
        values["config/entity_registry/list"] += [{"entity_id": None}]
        report = await self.run_report(values)
        self.assertEqual(report["counts"]["states"], 6)
        self.assertIn("malformed_state_rows_not_reconciled", report["omissions"])
        self.assertIn("malformed_registry_rows_not_reconciled", report["omissions"])
        self.assertFalse(report["complete"])


class PlatformTests(Base):
    async def test_entity_source_unavailable_or_invalid(self):
        for value, reason in ((HomeAssistantTimeoutError(), "timeout"), (AuthorizationError(), "permission_failure"),
                              (["template"], "invalid_response"), (RuntimeError(UNKNOWN_VALUE), "source_unavailable")):
            with self.subTest(reason):
                report = await self.run_report(healthy() | {"entity/source": value})
                self.assertEqual(source(report, "entity/source")["reason"], reason)
                self.assertEqual(source(report, "entity/source")["status"], "unavailable")
                for row in report["entities"]:
                    self.assertEqual((row["platform"], row["platform_status"]), (None, "unknown"))
                    self.assertEqual(row["config_entry_status"], "unknown")
                self.assertEqual(report["counts"]["directly_written_states"], 0)
                self.assertIn("platform_unknown:entity_source_unavailable", report["omissions"])
                self.assertIn("source_unavailable:entity/source", report["omissions"])
                self.assertNotIn("search/related", self.calls)

    async def test_invalid_platform_value_is_not_reported(self):
        values = healthy()
        values["entity/source"]["binary_sensor.yaml_door"] = {"domain": "Template!" + UNKNOWN_VALUE}
        values["entity/source"]["light.yaml_group"] = "group"
        values["entity/source"]["Not.Valid"] = {"domain": "x"}
        report = await self.run_report(values)
        rows = self.rows(report)
        for entity_id in ("binary_sensor.yaml_door", "light.yaml_group"):
            self.assertEqual((rows[entity_id]["platform"], rows[entity_id]["platform_status"]),
                             (None, "invalid_response"))
            self.assertEqual(rows[entity_id]["config_entry_status"], "unknown")
        self.assertIn("platform_unknown:invalid_response", report["omissions"])
        self.assertEqual(self.socket.batches, [[c.search_payload("sensor.yaml_template")]])


class SearchTests(Base):
    async def test_whole_search_batch_failure(self):
        for value, reason in ((HomeAssistantApiError(), "command_failed"), (AuthorizationError(), "permission_failure"),
                              (HomeAssistantUnavailableError(), "home_assistant_unavailable"),
                              (lambda payloads: [], "invalid_response"), (lambda payloads: {}, "invalid_response")):
            with self.subTest(reason):
                report = await self.run_report(healthy() | {"search/related": value})
                status = source(report, "search/related")
                self.assertEqual((status["status"], status["reason"]), ("unavailable", reason))
                rows = self.rows(report)
                self.assertEqual(rows["binary_sensor.yaml_door"]["config_entry_status"], "unknown")
                self.assertEqual(rows["sensor.directly_written"]["config_entry_status"], "not_applicable")
                self.assertIn("config_entry_unknown:search_related_unavailable", report["omissions"])
                self.assertEqual(rows["binary_sensor.yaml_door"]["platform"], "template")

    async def test_per_entity_search_failures(self):
        values = healthy()
        values["search/related"] = {
            "binary_sensor.yaml_door": (False, "not_found"),
            "light.yaml_group": (True, ["config_entry"]),
            "sensor.yaml_template": (True, {"config_entry": [ENTRY]}),
        }
        report = await self.run_report(values)
        rows = self.rows(report)
        self.assertEqual(rows["binary_sensor.yaml_door"]["config_entry_status"], "command_failed")
        self.assertEqual(rows["light.yaml_group"]["config_entry_status"], "invalid_response")
        self.assertEqual(rows["sensor.yaml_template"]["config_entry_status"], "resolved")
        status = source(report, "search/related")
        self.assertEqual((status["status"], status["reason"]), ("partial", "per_entity_failures"))
        self.assertIn("config_entry_unknown:binary_sensor.yaml_door", report["omissions"])
        self.assertIn("config_entry_unknown:light.yaml_group", report["omissions"])
        self.assertIn("source_partial:search/related", report["omissions"])
        values["search/related"] = {e: (False, "unknown_error") for e in UNREGISTERED}
        report = await self.run_report(values)
        self.assertEqual(source(report, "search/related")["status"], "unavailable")

    async def test_invalid_config_entry_shapes(self):
        for related in ({"config_entry": "x"}, {"config_entry": [7]}, {"config_entry": [""]},
                        {"config_entry": ["x" * 65]}, ("not", "a", "pair")):
            with self.subTest(related=related):
                outcome = related if isinstance(related, tuple) else (True, related)
                values = healthy()
                values["search/related"] = {"binary_sensor.yaml_door": outcome}
                report = await self.run_report(values)
                self.assertEqual(self.rows(report)["binary_sensor.yaml_door"]["config_entry_status"],
                                 "invalid_response")


class FailureTests(Base):
    async def test_states_or_registry_unavailable_is_an_error(self):
        for key in ("states", "config/entity_registry/list"):
            for value in (HomeAssistantTimeoutError(), AuthorizationError(), {"entity_id": "light.x"}, None,
                          RuntimeError(UNKNOWN_VALUE)):
                with self.subTest(key=key, value=value):
                    service, rest, socket, core, telemetry, token, calls = setup(healthy() | {key: value})
                    try:
                        with self.assertRaises(c.EntityReconciliationError) as caught:
                            await service.report()
                        self.assertEqual(caught.exception.reason, "sources_unavailable")
                        self.assertNotIn("entity/source", calls)
                        self.assertNotIn(UNKNOWN_VALUE, json.dumps(caught.exception.details))
                    finally:
                        end_request(token)

    async def test_known_secret_in_reported_text_is_redacted(self):
        values = healthy()
        values["search/related"]["binary_sensor.yaml_door"] = (True, {"config_entry": [HA_TOKEN[:64]]})
        report = await self.run_report(values)
        self.assertIn("redacted_by_policy", report["omissions"])
        self.assertFalse(report["complete"])

    async def test_byte_budget_trims_rows_and_reports_partial(self):
        many = [f"sensor.yaml_{n:04d}_" + "x" * 200 for n in range(c.MAX_LIMIT)]
        values = {"states": states(*many), "config/entity_registry/list": [], "entity/source": {},
                  "search/related": {}}
        report = await self.run_report(values, limit=c.MAX_LIMIT)
        self.assertLess(report["counts"]["listed"], c.MAX_LIMIT)
        self.assertEqual(report["counts"]["listed"], len(report["entities"]))
        self.assertTrue(report["truncated"])
        self.assertIn("entities_not_listed_response_bytes", report["omissions"])
        self.assertLessEqual(len(json.dumps(report, separators=(",", ":"))), c.RESPONSE_BUDGET + 200)

    async def test_omissions_are_bounded(self):
        values = healthy()
        many = [f"sensor.yaml_{n:03d}" for n in range(80)]
        values["states"] = states(*many)
        values["config/entity_registry/list"] = []
        values["entity/source"] = {e: {"domain": "template"} for e in many}
        values["search/related"] = {e: (False, "not_found") for e in many}
        report = await self.run_report(values)
        self.assertEqual(len(report["omissions"]), 50)


class AuthorityTests(Base):
    async def test_unavailable_route_anonymous_or_no_authorizer_dispatch_nothing(self):
        for variant in ("route", "anonymous", "no_authorizer", "refusing_authorizer"):
            with self.subTest(variant):
                service, rest, socket, core, telemetry, token, calls = setup(
                    caller="anonymous" if variant == "anonymous" else "synthetic_authenticated_caller")
                try:
                    if variant == "route":
                        core.available = False
                    elif variant == "no_authorizer":
                        telemetry.core_dispatch_authorizer = None
                    elif variant == "refusing_authorizer":
                        telemetry.core_dispatch_authorizer = lambda: False
                    with self.assertRaises(c.EntityReconciliationError) as caught:
                        await service.report()
                    self.assertEqual(caught.exception.reason, "authority_unavailable")
                    self.assertEqual(calls, [])
                finally:
                    end_request(token)

    async def test_authority_change_during_any_read_fails_closed(self):
        for stage in ("states", "config/entity_registry/list", "entity/source", "search/related"):
            for change in ("generation", "version", "retired"):
                with self.subTest(stage=stage, change=change):
                    service, rest, socket, core, telemetry, token, calls = setup()

                    def bump(kind, stage=stage, change=change, core=core):
                        if kind == stage:
                            if change == "generation":
                                core.generation += 1
                            elif change == "version":
                                core.current_observation = SimpleNamespace(version="2026.11.0")
                            else:
                                core.available = False
                    rest.hook = socket.hook = bump
                    try:
                        with self.assertRaises(c.EntityReconciliationError) as caught:
                            await service.report()
                        self.assertEqual(caught.exception.reason, "authority_unavailable")
                        self.assertEqual(calls[-1], stage)
                    finally:
                        end_request(token)

    async def test_invalid_arguments_dispatch_nothing(self):
        for domains, limit in ((["Light"], 10), ([], 10), (["light", "light"], 10), (["light"] * 51, 10),
                               (None, 0), (None, 501), (None, True), (None, "10"), ("light", 10), ("ab", 10),
                               ({"light": 1}, 10), (frozenset({"light"}), 10),
                               (["_light"], 10), (["li__ght"], 10), ([7], 10)):
            with self.subTest(domains=domains, limit=limit):
                service, rest, socket, core, telemetry, token, calls = setup()
                try:
                    with self.assertRaises((ValueError, TypeError)):
                        await service.report(domains, limit)
                    self.assertEqual(calls, [])
                finally:
                    end_request(token)
        for arguments in (None, [], {UNKNOWN_KEY: 1}, {"domains": None, "limit": 1, "x": 2}):
            with self.assertRaises(ValueError):
                c.validate_arguments(arguments)
        report = await self.run_report(domains=("sensor",))
        self.assertEqual(report["scope"]["domains"], ["sensor"])
        self.assertEqual(c.validate_arguments({}), (None, c.DEFAULT_LIMIT))
        self.assertEqual(c.validate_arguments({"domains": None}), (None, c.DEFAULT_LIMIT))
        with self.assertRaises(ValueError):
            c.search_payload("Not.Valid")

    async def test_runtime_configuration(self):
        with self.assertRaises(c.EntityReconciliationError):
            EntityReconciliationRuntime().require()
        runtime = EntityReconciliationRuntime()
        runtime.configure(Mock(), Mock(), SyntheticCore(), settings())
        self.assertEqual(runtime.require().known_secrets, (ACCESS_SECRET, HA_TOKEN))
        self.assertEqual(c.EntityReconciliationError(UNKNOWN_VALUE).reason, "sources_unavailable")
        self.assertEqual(c.EntityReconciliationError("invalid_arguments").code, ErrorCode.INVALID_REQUEST)
        self.assertEqual(c.EntityReconciliationError("authority_unavailable").code, ErrorCode.PROVIDER_UNAVAILABLE)


def docstring_ids(tree):
    return {id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}


class NoWritePathTests(unittest.IsolatedAsyncioTestCase):
    def sources(self):
        return {path.name: path.read_text() for path in sorted(PACKAGE.glob("*.py"))} | {"tool": TOOL.read_text()}

    def test_fixed_contract(self):
        self.assertEqual(dict(c.REGISTRY_COMMAND), {"type": "config/entity_registry/list"})
        self.assertEqual(dict(c.SOURCE_COMMAND), {"type": "entity/source"})
        self.assertEqual(c.STATES_PATH, "/states")
        self.assertEqual(c.search_payload("light.x"), {"type": "search/related", "item_type": "entity",
                                                       "item_id": "light.x"})
        with self.assertRaises(TypeError):
            c.REGISTRY_COMMAND["type"] = "config/entity_registry/remove"  # type: ignore[index]

    def test_static_only_fixed_reads(self):
        literals, client_attributes, rest_calls, clients_imports = set(), set(), [], []
        for name, text in self.sources().items():
            tree = ast.parse(text)
            skip = docstring_ids(tree)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
                        and ("/" in node.value) and " " not in node.value):
                    literals.add(node.value)
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) \
                        and node.value.attr in {"client", "rest_client"}:
                    client_attributes.add((node.value.attr, node.attr))
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "request"):
                    rest_calls.append(ast.unparse(node))
                if isinstance(node, ast.ImportFrom) and "clients" in (node.module or ""):
                    clients_imports.append((node.module, [alias.name for alias in node.names]))
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = (node.module or "") if isinstance(node, ast.ImportFrom) else ""
                    names = [alias.name for alias in node.names]
                    for banned in ("httpx", "aiohttp", "requests", "websockets", "supervisor", "upstream",
                                   "governance", "providers", "subprocess", "socket"):
                        self.assertFalse(banned in module or any(banned in n for n in names), (name, module, names))
        self.assertEqual(literals, {"/states", "config/entity_registry/list", "entity/source", "search/related"})
        self.assertEqual(client_attributes, {("rest_client", "request"), ("client", "command"),
                                             ("client", "command_batch")})
        self.assertEqual(rest_calls, ['self.rest_client.request(\'GET\', rest_path(c.STATES_PATH))'])
        self.assertEqual(clients_imports, [("clients.paths", ["rest_path"])])
        service = ast.parse(self.sources()["service.py"])
        commands = [ast.unparse(node.args[0]) for node in ast.walk(service)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "command"]
        self.assertEqual(commands, ["dict(c.REGISTRY_COMMAND)", "dict(c.SOURCE_COMMAND)"])
        for text in self.sources().values():
            for word in ("call_service", "post(", "put(", "delete(", "\"POST\"", "'POST'",
                         "entity_registry/update", "entity_registry/remove", "remove_entity"):
                self.assertNotIn(word, text)

    async def test_runtime_dispatch_is_exactly_the_fixed_reads(self):
        service, rest, socket, core, telemetry, token, calls = setup()
        self.addCleanup(end_request, token)
        await service.report()
        self.assertEqual(calls, ["states", "config/entity_registry/list", "entity/source", "search/related"])
        self.assertTrue(all(payload["item_type"] == "entity" for batch in socket.batches for payload in batch))


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    def test_core_gate_mapping(self):
        self.assertEqual(STATIC_TOOL_CORE_REQUIREMENTS["get_unregistered_entities"], c.CORE_REQUIREMENTS)

    def test_capability_metadata_read_only(self):
        native = next(item for item in BETA_NATIVE_CAPABILITIES if item["tool"] == "get_unregistered_entities")
        self.assertEqual((native["risk"], native["operation_class"], native["routing"], native["fallback"],
                          native["direct_write_allowed"], native["data_providers"]),
                         ("read", "read", "engineering_native", "none", False, ["direct_ha_api"]))
        matrix = next(item for item in CAPABILITY_PROVIDER_MATRIX if item["tool"] == "get_unregistered_entities")
        self.assertEqual((matrix["selected_provider"], matrix["fallback_policy"], matrix["trust_mode"]),
                         ("engineering", "none", "reviewed_native_read"))

    def test_tool_annotations_and_schema(self):
        tool = registered_tools(get_registered_server())["get_unregistered_entities"]
        annotations = tool.annotations
        self.assertEqual((annotations.readOnlyHint, annotations.destructiveHint, annotations.idempotentHint,
                          annotations.openWorldHint), (True, False, True, False))
        schema = tool.parameters
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(set(schema["properties"]), {"domains", "limit"})
        self.assertEqual(schema["properties"]["limit"]["maximum"], c.MAX_LIMIT)
        self.assertEqual(schema["properties"]["limit"]["default"], c.DEFAULT_LIMIT)
        self.assertNotIn("required", schema)

    async def test_served_tool_rejects_raw_arguments_without_echo(self):
        tool = registered_tools(get_registered_server())["get_unregistered_entities"]
        for arguments in (None, [], {"domains": [UNKNOWN_VALUE]}, {"limit": UNKNOWN_VALUE}, {"limit": 1.0},
                          {"limit": True}, {UNKNOWN_KEY: 1}, {"domains": []}):
            output = await tool.run(arguments)
            result = json.loads(output)
            self.assertFalse(result["success"])
            self.assertEqual(result["error_code"], "invalid_request")
            self.assertNotIn(UNKNOWN_VALUE, output)
            self.assertNotIn(UNKNOWN_KEY, output)

    async def test_gateway_rejects_before_sdk_and_audit_excludes_invalid_material(self):
        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            config = settings(audit_path=str(audit_path))
            app = Mock(side_effect=AssertionError("SDK must not be reached"))
            core = SimpleNamespace(acquire=Mock(return_value=None))
            gateway = AuthenticatedMcpGateway(app, config, AuditLogger(str(audit_path), config.access_secret),
                                              core_runtime=core)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway),
                                         base_url="http://127.0.0.1:8100") as client:
                for bad in ({"domains": [UNKNOWN_VALUE]}, {UNKNOWN_KEY: UNKNOWN_VALUE}, {"limit": UNKNOWN_VALUE}):
                    response = await client.post(f"/{config.access_secret}/mcp", json={
                        "jsonrpc": "2.0", "id": "synthetic-request", "method": "tools/call",
                        "params": {"name": "get_unregistered_entities", "arguments": bad}})
                    for value in (UNKNOWN_VALUE, UNKNOWN_KEY):
                        self.assertNotIn(value, response.text)
                    self.assertIn("invalid_request", response.text)
            text = audit_path.read_text()
            for value in (UNKNOWN_VALUE, UNKNOWN_KEY):
                self.assertNotIn(value, text)
            app.assert_not_called()
            core.acquire.assert_not_called()

    async def test_gateway_positive_output_and_audit(self):
        service, _rest, _socket, core, _telemetry, token, _calls = setup()
        end_request(token)
        core.acquire = Mock(return_value=object())
        core.consume = Mock(return_value=())
        core.revalidate = Mock(return_value=True)
        core.finish = Mock(return_value=True)
        core.release = Mock(return_value=True)

        async def app(scope, receive, send):
            rpc = json.loads((await receive())["body"])
            tool = registered_tools(get_registered_server())["get_unregistered_entities"]
            rendered = await tool.run(rpc["params"]["arguments"])
            result = {"jsonrpc": "2.0", "id": rpc["id"],
                      "result": {"content": [{"type": "text", "text": rendered}], "isError": False}}
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": json.dumps(result).encode()})

        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            config_ = settings(audit_path=str(audit_path))
            gateway = AuthenticatedMcpGateway(app, config_, AuditLogger(str(audit_path), config_.access_secret),
                                              core_runtime=core)
            with patch.object(ENTITY_RECONCILIATION, "service", service), patch.object(public, "SETTINGS", config_):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway),
                                             base_url="http://127.0.0.1:8100") as http:
                    response = await http.post(f"/{config_.access_secret}/mcp", json={
                        "jsonrpc": "2.0", "id": "synthetic-request", "method": "tools/call",
                        "params": {"name": "get_unregistered_entities",
                                   "arguments": {"domains": ["sensor", "binary_sensor"], "limit": 10}}})
            audit = audit_path.read_text()
        data = json.loads(response.json()["result"]["content"][0]["text"])
        self.assertTrue(data["success"], data)
        self.assertEqual(data["data"]["counts"]["listed"], 3)
        self.assertEqual(data["metadata"]["data_providers"], ["direct_ha_api"])
        self.assertEqual(data["metadata"]["upstream_calls"], 0)
        self.assertFalse(data["metadata"]["fallback_occurred"])
        for value in NEEDLES:
            self.assertNotIn(value, response.text + audit)
        record = next(r for r in map(json.loads, audit.splitlines())
                      if r.get("tool_name") == "get_unregistered_entities")
        self.assertEqual(record["resource_ids"], {})
        self.assertEqual(record["parameters"]["profile"], "unregistered-entities-read-v1")
        self.assertEqual(record["parameters"]["domain_count"], 2)
        self.assertEqual(record["parameters"]["limit"], 10)
        self.assertNotIn("sensor.yaml_template", json.dumps(record))
        core.finish.assert_called_once()


if __name__ == "__main__":
    unittest.main()
