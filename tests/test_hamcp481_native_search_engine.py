"""HAMCP-481 W3: the native ``ha_search`` engine (design sections 2 to 8).

The engine runs over the W2 Core reads (``CoreReadSession``) against the
synthetic Core 2026.11.3 transport of ``tests.test_hamcp481_core_reads`` and
injected sub-call page readers. Everything is synthetic and offline; no live
Home Assistant and no delegated route. The engine is not registered or
routed, so these tests drive it directly.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import dataclasses
import functools
import itertools
import json
from pathlib import Path
import sys
import time
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from tests.test_hamcp481_core_reads import NOW, Base, entity_row  # noqa: E402
from ha_mcp_engineering.clients.rest import ExpectedHttpStatus  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.errors import HomeAssistantApiError, HomeAssistantUnavailableError  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_2 as u  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_2_keys as k  # noqa: E402
from ha_mcp_engineering.search import configs, engine, entities, reply, subcalls  # noqa: E402
from ha_mcp_engineering.search import core_contracts as c  # noqa: E402
from ha_mcp_engineering.search.core_reads import CoreReadSession, ReadResult  # noqa: E402
from ha_mcp_engineering.search.reply import SearchError  # noqa: E402
from ha_mcp_engineering.tools import compatibility  # noqa: E402

PACKAGE = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering"
W3_MODULES = ("engine", "entities", "configs", "subcalls", "reply")
BUCKETS = ("entities", "automations", "scripts", "scenes", "helpers", "dashboards")
ADMITTED = k.ROWS["ha_search"].admitted
# Design section 5.1 names that Row 25 does not admit yet (W6 admits them).
NATIVE_ONLY = frozenset({"applied", "partial_reasons"})
# 9.2.0 top-level names Row 25 does not admit; never emitted, counted.
NINE_TWO_WITHHELD = frozenset({"area_filter", "area_names", "by_domain", "message", "partial_reason",
                               "state_filter_note"})
BATCH_COMMANDS = frozenset(c.READS[name].command for name in c.BATCH_READS)
SINGLE_COMMANDS = frozenset(read.command for read in c.READS.values()
                            if read.transport == "websocket") - BATCH_COMMANDS
SECRET = "synthetic-secret-value-not-real"
MARKER = "SYNTHETIC-CALLER-MARKER-NOT-REAL"


def st(entity_id, value, name, /, **attributes):
    return {"entity_id": entity_id, "state": value,
            "attributes": {"friendly_name": name, **attributes},
            "last_changed": "2026-11-25T11:00:00+00:00", "context": {"id": "01JSYNTHETICCONTEXT"}}


STATES = (
    ("light.kitchen", "on", "Kitchen ceiling", {}),
    ("sensor.temperature", "21", "Temperature", {}),
    ("light.hall", "off", "Hall light", {}),
    ("light.porch", "on", "Porch light", {}),
    ("switch.fan", "off", "Fan", {}),
    ("group.downstairs", "on", "Downstairs", {"entity_id": ["light.kitchen", "light.hall"]}),
    ("automation.morning", "on", "Morning routine", {}),
    ("automation.porch_lights", "on", "Porch lights", {}),
    ("script.wake", "off", "Wake up", {}),
    ("scene.movie", "unknown", "Movie time", {}),
    ("scene.other", "unknown", "Other scene", {}),
    ("input_boolean.guest_mode", "off", "Guest toggle", {}),
    ("sensor.probe", "5", "Probe reading", {}),
    ("sensor.blank", "1", "Blank sensor", {}),
    ("sensor.deep", "2", "Deep sensor", {}),
)
REGISTRY = {
    "light.kitchen": {"area_id": "kitchen", "labels": ["food"]},
    "sensor.temperature": {"device_id": "dev1"},
    "light.hall": {"hidden_by": "user", "area_id": "kitchen"},
    "light.porch": {"device_id": "dev3"},
    "scene.movie": {"platform": "homeassistant", "unique_id": "movie_uid"},
    "scene.other": {"platform": "hue", "unique_id": "hue_other"},
    "input_boolean.guest_mode": {"platform": "input_boolean", "unique_id": "guest_mode",
                                 "name": "Guest toggle"},
    "sensor.probe": {"device_id": "dev2"},
    "sensor.blank": {"area_id": "", "device_id": "dev1"},
    "sensor.deep": {"device_id": "dev5"},
}
BODIES = {
    "automation.morning": {"id": "1700000000001", "alias": "Morning routine",
                           "triggers": [{"trigger": "time", "at": "07:00:00"}],
                           "actions": [{"action": "light.turn_on",
                                        "target": {"entity_id": "light.kitchen"}}]},
    "automation.porch_lights": {"id": "1700000000002", "alias": "Porch lights",
                                "triggers": [{"trigger": "sun", "event": "sunset"}],
                                "actions": [{"action": "light.turn_on",
                                             "target": {"entity_id": "light.porch"}}]},
    "script.wake": {"alias": "Wake up",
                    "sequence": [{"action": "switch.turn_on", "target": {"entity_id": "switch.fan"}}]},
}
RELATED = {"light.kitchen": {"automation": ["automation.morning"], "script": ["script.wake"],
                             "scene": ["scene.movie"], "area": ["kitchen"]}}
ALIASES = {"light.porch": ["Veranda"]}
OTHER_INPUTS = ("input_number", "input_select", "input_text", "input_datetime", "input_button")


def area(area_id, name, aliases, floor_id, labels):
    return {"aliases": aliases, "area_id": area_id, "floor_id": floor_id, "humidity_entity_id": None,
            "icon": None, "labels": labels, "name": name, "picture": None,
            "temperature_entity_id": None, "created_at": 1.0, "modified_at": 1.0}


def floor(floor_id, name, level):
    return {"aliases": [], "created_at": 1.0, "floor_id": floor_id, "icon": None, "level": level,
            "name": name, "modified_at": 1.0}


def device(device_id, area_id, parent=None, labels=()):
    return {"area_id": area_id, "id": device_id, "labels": list(labels), "name": device_id.title(),
            "name_by_user": None, "parent_device_id": parent, "disabled_by": None}


def configs_reply(payloads):
    return [(True, {"config": copy.deepcopy(BODIES[payload["entity_id"]])})
            if payload["entity_id"] in BODIES else (False, "not_found") for payload in payloads]


def related_reply(payloads):
    return [(True, copy.deepcopy(RELATED.get(payload["item_id"], {}))) for payload in payloads]


def entries_reply(payload):
    return {entity_id: {"aliases": list(ALIASES.get(entity_id, []))} for entity_id in payload["entity_ids"]}


def world():
    return {
        "states": [st(entity_id, value, name, **attributes)
                   for entity_id, value, name, attributes in STATES],
        "config/area_registry/list": [area("kitchen", "Kitchen", ["Cookhouse"], "ground", ["food"]),
                                      area("garden", "Garden", ["Yard"], "outside", [])],
        "config/floor_registry/list": [floor("ground", "Ground", 0), floor("outside", "Outside", 1)],
        "config/label_registry/list": [
            {"color": None, "created_at": 1.0, "description": None, "icon": None, "label_id": label_id,
             "name": name, "modified_at": 1.0} for label_id, name in (("food", "Food"), ("night", "Night"))],
        "config/entity_registry/list": [entity_row(entity_id, **REGISTRY.get(entity_id, {}))
                                        for entity_id, *_ in STATES],
        "config/device_registry/list": [device("dev1", "kitchen", labels=["night"]),
                                        device("dev2", None, "dev1"), device("dev3", None, "dev4"),
                                        device("dev4", "garden"), device("dev5", None, "dev2")],
        "input_boolean/list": [{"id": "guest_mode", "name": "Guest mode", "icon": "mdi:account"}],
        **{f"{domain}/list": [] for domain in OTHER_INPUTS},
        "config/entity_registry/get_entries": entries_reply,
        "automation/config": configs_reply,
        "script/config": configs_reply,
        "search/related": related_reply,
        "/config/scene/config/movie_uid": {"id": "movie_uid", "name": "Movie time",
                                           "entities": {"light.kitchen": {"state": "off"}}},
    }


def reasons_of(result):
    return {(entry["reason"], entry["search_type"], entry["code"]): entry["count"]
            for entry in result["partial_reasons"]}


def page(rows=(), partial=False, has_more=False, total=0, withheld=frozenset()):
    return subcalls.PageResult(tuple(rows), partial, has_more, total, frozenset(withheld))


class World(Base):
    def setUp(self):
        super().setUp()
        # The REST and websocket fakes share this dict.
        self.values.clear()
        self.values.update(world())
        self.reads: list = []
        self.pages: list = []
        self.readers = {"helper": self.no_helpers}
        self.secrets: tuple = ()
        self.monotonic = time.monotonic

    async def no_helpers(self, arguments):
        self.pages.append(arguments)
        return page()

    def factory(self, reads):
        self.reads.append(reads)
        return CoreReadSession(self.rest, self.socket, self.core, reads, clock=lambda: NOW)

    def search(self, arguments):
        return engine.search(arguments, session_factory=self.factory, subcall_readers=self.readers,
                             known_secrets=self.secrets, monotonic=self.monotonic)

    async def go(self, **arguments):
        result = await self.search(arguments)
        self.check_calls()
        self.check_reply(result)
        return result

    async def refused(self, reason, arguments):
        with self.assertRaises(SearchError) as error:
            await self.search(arguments)
        self.assertEqual(error.exception.reason, reason)
        self.check_calls()
        return error.exception

    def check_calls(self):
        # Read-only Core commands only: the declared reads, nothing else.
        for call in self.calls:
            if call[0] == "rest":
                self.assertEqual(call[1], "GET")
                self.assertTrue(call[2] == "/states" or call[2].startswith("/config/scene/config/"), call)
            elif call[0] == "command":
                self.assertIn(call[1]["type"], SINGLE_COMMANDS)
            else:
                self.assertEqual(call[0], "batch")
                self.assertEqual(len({payload["type"] for payload in call[1]}), 1)
                self.assertIn(call[1][0]["type"], BATCH_COMMANDS)
        for reads in self.reads:
            self.assertLessEqual(set(reads), set(c.READS))

    def check_reply(self, result):
        self.assertIs(result["success"], True)
        self.assertIs(result["partial"], bool(result["partial_reasons"]))
        self.assertEqual(result["count"], sum(len(result.get(bucket, ())) for bucket in BUCKETS))
        self.assertLessEqual(set(result), ADMITTED | set(k.ENGINEERING_ADDED) | NATIVE_ONLY)
        for bucket in BUCKETS:
            for record in result.get(bucket, ()):
                self.assertLessEqual(set(record), ADMITTED, (bucket, record))
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=True)), reply.RESPONSE_BUDGET_BYTES)
        for entry in result["partial_reasons"]:
            self.assertEqual(list(entry), ["reason", "search_type", "count", "code"])
            self.assertIn(entry["code"], reply.REASON_CODES[entry["reason"]])

    def batches(self, command):
        return [call[1] for call in self.calls if call[0] == "batch" and call[1][0]["type"] == command]


class SchemaBindingTests(unittest.TestCase):
    """The engine's arguments are the public ``ha_search`` schema's."""

    schema = u.PUBLIC_SCHEMAS["ha_search"]

    def test_parameters_and_defaults_are_the_public_schema(self):
        properties = self.schema["properties"]
        self.assertIs(self.schema.get("additionalProperties"), False)
        self.assertEqual(set(engine.DEFAULTS), set(properties))
        self.assertEqual(set(engine.PARAMETERS), set(properties))
        self.assertEqual(list(engine.PARAMETERS), sorted(properties))
        self.assertEqual(SearchError.PARAMETERS, frozenset(properties))
        for name, value in properties.items():
            if "default" in value:
                self.assertEqual(engine.DEFAULTS[name], value["default"], name)

    def test_bounds_are_the_public_schema(self):
        properties = self.schema["properties"]
        self.assertEqual(properties["limit"]["minimum"], 1)
        self.assertEqual(properties["offset"]["minimum"], 0)
        budget = [branch for branch in properties["config_time_budget"]["anyOf"]
                  if branch.get("type") == "number"]
        self.assertEqual(len(budget), 1)
        self.assertEqual((budget[0]["minimum"], budget[0]["maximum"]), configs.TIME_BUDGET_RANGE)

    def test_reply_constants_are_bound_to_row_25(self):
        self.assertEqual(reply.COUNT_CAP, k.COUNT_CAP)
        self.assertLessEqual(set(reply.ENGINEERING_COUNTS), set(k.ENGINEERING_ADDED))
        self.assertLessEqual(set(entities.RECORD_KEYS) | set(entities.ENRICHMENT_FIELDS)
                             | set(entities.MEMBERSHIP_FIELDS), ADMITTED)
        self.assertNotIn(entities.COMPOSED_NAME_FIELD, entities.RECORD_KEYS)
        # Disclosed for W6: the section 5.1 names are not Row 25 names yet.
        self.assertFalse(NATIVE_ONLY & ADMITTED)
        # The 9.2.0 keep set names six keys Row 25 does not admit; the
        # engine withholds them (``engine.withheld_names``) and counts them.
        self.assertEqual(reply.ALWAYS_KEPT - ADMITTED - set(k.ENGINEERING_ADDED),
                         NATIVE_ONLY | NINE_TWO_WITHHELD)
        self.assertEqual((set(subcalls._ROW_KINDS) | {"score"}) - ADMITTED, set())


class PageSizeTests(unittest.TestCase):
    """Design section 2.6: a page of ``PAGE_SIZE`` rows of the largest row
    shape passes the gateway's size check."""

    @staticmethod
    def gateway_limit():
        policy = json.loads((PACKAGE / "upstream_tool_policy_9_2_0.json").read_text(encoding="utf-8"))
        entries = [tool for tool in policy["tools"] if tool["upstream_name"] == "ha_search"]
        assert len(entries) == 1
        default = Settings.__dataclass_fields__["response_size_limit"].default
        # ``upstream_read_gateway.py``: min(policy limit, settings limit).
        return min(entries[0]["response_limit_bytes"], default)

    @staticmethod
    def encoded(rows):
        # The largest Row 25 helper row: a 9.2.0 storage-helper row
        # (``_deep.py``) with a 64-character entity id and name.
        row = {"entity_id": "input_datetime." + "x" * 49, "helper_type": "input_datetime",
               "name": "N" * 64, "score": 100, "match_in_name": True, "match_in_config": True,
               "match_in_references": True}
        assert len(row["entity_id"]) == 64
        envelope = {"success": True, "query": "q" * reply.QUERY_ECHO_MAX, "entities": [],
                    "entity_total_matches": 0, "automations": [], "scripts": [], "scenes": [],
                    "helpers": [dict(row) for _ in range(rows)], "search_types": ["helper"],
                    "config_total_matches": 20_000, "partial": False, "errors": [], "warnings": [],
                    "count": rows, "offset": 19_800, "limit": rows, "has_more": True,
                    "next_offset": 20_000, "config_has_more": True, "config_next_offset": 20_000}
        # The gateway's encoding and margin (``upstream_read_gateway.py``).
        return len(json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                              allow_nan=False, default=str).encode("utf-8")) + 8_000

    def test_a_full_page_fits_the_gateway_check(self):
        limit = self.gateway_limit()
        self.assertEqual(limit, 60_000)
        self.assertEqual(subcalls.PAGE_SIZE, 150)
        self.assertLessEqual(self.encoded(subcalls.PAGE_SIZE), limit)
        # The design's 200 would not fit (disclosed in the PR).
        self.assertGreater(self.encoded(200), limit)

    def test_page_arguments_are_fixed(self):
        for search_type in subcalls.SUBCALL_TYPES:
            self.assertEqual(subcalls.page_arguments(search_type, "q", False, 3), {
                "query": "q", "search_types": [search_type], "exact_match": False,
                "include_config": False, "offset": 3 * subcalls.PAGE_SIZE, "limit": subcalls.PAGE_SIZE})
        for bad in (("automation", "q", True, 0), ("helper", 1, True, 0), ("helper", "q", 1, 0)):
            with self.assertRaises(ValueError):
                subcalls.page_arguments(*bad)
        # The scan deadline is the policy's ha_search timeout.
        policy = json.loads((PACKAGE / "upstream_tool_policy_9_2_0.json").read_text(encoding="utf-8"))
        entry = next(tool for tool in policy["tools"] if tool["upstream_name"] == "ha_search")
        self.assertEqual(subcalls.SCAN_DEADLINE_SECONDS, float(entry["timeout_seconds"]))


class ReachabilityTests(unittest.TestCase):
    """The W3 modules reach Core only through the injected W2 session and
    the delegated route only through the injected page readers."""

    ALLOWED_IMPORTS = {
        "engine": {".:configs,entities", ".:core_contracts", ".reply", "__future__", "dataclasses",
                   "json", "math", "time", "typing"},
        "entities": {".:core_contracts", ".:scoring", ".core_reads", ".reply", "__future__",
                     "dataclasses", "re", "types", "typing"},
        "configs": {"..entity_ids", ".:core_contracts", ".:scoring", ".:subcalls", ".core_reads",
                    ".entities", ".reply", "__future__", "asyncio", "dataclasses", "math", "re", "time",
                    "types", "typing"},
        "subcalls": {".:core_contracts", "__future__", "asyncio", "dataclasses", "time", "typing"},
        "reply": {"..errors", "..sanitization", "__future__", "dataclasses", "json", "typing"},
    }
    FORBIDDEN_CALLS = {"request", "command", "command_batch", "call_service", "send_json", "subscribe",
                       "write", "call_tool", "dispatch"}

    def tree(self, name):
        return ast.parse((PACKAGE / f"search/{name}.py").read_text(encoding="utf-8"))

    def test_imports_are_exactly_the_allowlist(self):
        for name in W3_MODULES:
            imports = set()
            for node in ast.walk(self.tree(name)):
                if isinstance(node, ast.ImportFrom):
                    if node.module is None:
                        imports.add("." * node.level + ":" + ",".join(alias.name for alias in node.names))
                    else:
                        imports.add("." * node.level + node.module)
                elif isinstance(node, ast.Import):
                    imports.update(alias.name for alias in node.names)
            self.assertEqual(imports, self.ALLOWED_IMPORTS[name], name)

    def test_no_transport_or_write_call(self):
        for name in W3_MODULES:
            for node in ast.walk(self.tree(name)):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, self.FORBIDDEN_CALLS, (name, node.lineno))
                if isinstance(node, (ast.Name, ast.Attribute)):
                    label = node.id if isinstance(node, ast.Name) else node.attr
                    self.assertNotIn(label, {"eval", "exec", "__import__", "importlib"}, name)


class GoldenTests(World):
    async def test_an_entity_id_query_searches_both_surfaces(self):
        result = await self.go(query="light.kitchen")
        defaults = ["automation", "script", "scene", "helper"]
        expected = {
            "success": True, "query": "light.kitchen",
            "entities": [{"entity_id": "light.kitchen", "friendly_name": "Kitchen ceiling", "domain": "light",
                          "state": "on", "score": 100, "match_type": "exact_match"}],
            "entity_total_matches": 1,
            "automations": [{"entity_id": "automation.morning", "friendly_name": "Morning routine",
                             "score": result["automations"][0]["score"], "match_in_name": False,
                             "match_in_config": True, "match_in_references": True}],
            "scripts": [{"entity_id": "script.wake", "friendly_name": "Wake up", "score": 100,
                         "match_in_name": False, "match_in_config": False, "match_in_references": True,
                         "script_id": "wake"}],
            "scenes": [{"entity_id": "scene.movie", "scene_id": "movie_uid", "friendly_name": "Movie time",
                        "score": result["scenes"][0]["score"], "match_in_name": False,
                        "match_in_config": True, "match_in_references": True}],
            "helpers": [], "search_types": defaults, "config_total_matches": 3, "partial": False,
            "errors": [],
            "applied": {"search_types": defaults, "domain_filter": None, "area_filter_set": False,
                        "state_filter_set": False, "exact_match": True, "include_hidden": True,
                        "limit": 10, "offset": 0, "entity_search_ran": True, "config_search_ran": True},
            "partial_reasons": [], "count": 4, "offset": 0, "limit": 10, "has_more": False,
            "next_offset": None, "entity_has_more": False, "entity_next_offset": None,
            "config_has_more": False, "config_next_offset": None, "search_type": "exact_match",
            "fields_withheld": 0, "fields_dropped_by_policy": 0, "upstream_warnings_replaced": 0,
        }
        self.assertEqual(result, expected)
        self.assertEqual(list(result), list(expected))
        self.assertGreaterEqual(result["automations"][0]["score"], 1)
        self.assertGreaterEqual(result["scenes"][0]["score"], 1)
        # One session; the reads this call needs, in READS order.
        self.assertEqual(self.reads, [(
            "states", "entity_registry", "automation_config", "script_config", "search_related",
            "scene_config", "input_boolean_list", "input_number_list", "input_select_list",
            "input_text_list", "input_datetime_list", "input_button_list")])
        # Graph-confirmed bodies are read last (``_fetch.py``).
        self.assertEqual([p["entity_id"] for p in self.batches("automation/config")[0]],
                         ["automation.porch_lights", "automation.morning"])
        self.assertEqual(self.batches("search/related"),
                         [[{"type": "search/related", "item_type": "entity", "item_id": "light.kitchen"}]])
        self.assertIn(("rest", "GET", "/config/scene/config/movie_uid"), [call[:3] for call in self.calls])
        # The integration scene is scored by name and never fetched.
        self.assertNotIn("/config/scene/config/hue_other", [call[2] for call in self.calls
                                                            if call[0] == "rest"])
        self.assertEqual(self.pages, [subcalls.page_arguments("helper", "light.kitchen", True, 0)])

    async def test_search_entities_oracle_match_sets(self):
        # Design section 8.3 as G-d (as relayed by the architect): match sets
        # and per-record fields on a well-formed ASCII fixture, not order.
        states = copy.deepcopy(self.values["states"])
        for query in ("light", "kitchen", "temp", "LIGHT ", "xyz", "Sensor", "s"):
            with self.subTest(query=query):
                with patch.object(compatibility, "rest", new=AsyncMock(return_value=states)):
                    oracle = json.loads(await compatibility.search_entities(query, "", 100))
                expected = {(row["entity_id"], row["state"], row["friendly_name"])
                            for row in oracle["data"]["results"]}
                self.assertFalse(oracle["data"]["truncated"])
                result = await self.go(query=query, limit=100, search_types=None)
                self.assertEqual({(record["entity_id"], record["state"], record["friendly_name"])
                                  for record in result["entities"]}, expected)
                self.assertEqual(result["entity_total_matches"], len(expected))


class EntityModeTests(World):
    async def test_fuzzy_reads_aliases_for_the_candidates(self):
        result = await self.go(query="veranda", exact_match=False, search_types=None)
        self.assertEqual(result["search_type"], "fuzzy_search")
        self.assertIn("light.porch", [record["entity_id"] for record in result["entities"]])
        entries = [call[1] for call in self.calls if call[0] == "command"
                   and call[1]["type"] == "config/entity_registry/get_entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["entity_ids"], [entity_id for entity_id, *_ in STATES])
        self.assertIn("entity_registry_entries", self.reads[0])

    async def test_include_hidden_false_drops_hidden_candidates(self):
        result = await self.go(query="hall", exact_match=False, include_hidden=False)
        self.assertNotIn("light.hall", [record["entity_id"] for record in result["entities"]])
        entries = next(call[1] for call in self.calls if call[0] == "command"
                       and call[1]["type"] == "config/entity_registry/get_entries")
        self.assertNotIn("light.hall", entries["entity_ids"])
        shown = await self.go(query="hall light")
        self.assertEqual([(r["entity_id"], r["score"]) for r in shown["entities"]],
                         [("light.hall", 100 - 20)])
        exact = await self.go(query="light.hall", include_hidden=False)
        self.assertEqual((exact["entities"], exact["entity_total_matches"]), ([], 0))

    async def test_exact_mode_reads_no_aliases(self):
        await self.go(query="kitchen")
        self.assertNotIn("entity_registry_entries", self.reads[0])
        self.assertFalse([call for call in self.calls if call[0] == "command"
                          and call[1]["type"] == "config/entity_registry/get_entries"])

    async def test_area_only_ladder(self):
        expected = [("light.kitchen", 100), ("sensor.probe", 100), ("sensor.temperature", 100),
                    ("light.hall", 80)]
        for area_filter in ("Kitchen", "kitchen", "Cookhouse", "Ground"):
            with self.subTest(area_filter=area_filter):
                result = await self.go(area_filter=area_filter)
                self.assertEqual([(r["entity_id"], r["score"]) for r in result["entities"]], expected)
                self.assertEqual({r["match_type"] for r in result["entities"]}, {"area_match"})
                self.assertEqual(result["search_type"], "area_only")
                self.assertEqual(result["fields_withheld"], 2)  # area_filter, area_names
                self.assertTrue(result["applied"]["area_filter_set"])
                self.assertFalse(result["applied"]["config_search_ran"])
                self.assertEqual((result["automations"], result["config_total_matches"]), ([], 0))
                self.assertNotIn("config_has_more", result)
        result = await self.go(area_filter="Garden")
        self.assertEqual([r["entity_id"] for r in result["entities"]], ["light.porch"])
        self.assertIn("area_registry", self.reads[-1])
        self.assertIn("device_registry", self.reads[-1])
        hidden = await self.go(area_filter="Kitchen", include_hidden=False)
        self.assertNotIn("light.hall", [r["entity_id"] for r in hidden["entities"]])

    async def test_area_only_with_no_match_withholds_the_message(self):
        result = await self.go(area_filter="Attic")
        self.assertEqual((result["entities"], result["entity_total_matches"]), ([], 0))
        self.assertEqual(result["fields_withheld"], 3)  # area_filter, area_names, message

    async def test_an_ambiguous_floor_selects_no_area(self):
        self.values["config/floor_registry/list"] = [floor("level_1", "Level 1", 1),
                                                     floor("level_2", "Level 2", 2)]
        self.values["config/area_registry/list"] = [area("kitchen", "Kitchen", [], "level_1", []),
                                                    area("garden", "Garden", [], "level_2", [])]
        result = await self.go(area_filter="Level")
        self.assertEqual(result["entities"], [])
        self.assertEqual(result["warnings"], [reply.WARNING_AMBIGUOUS_FLOOR])

    async def test_floors_are_read_but_required_only_after_an_area_miss(self):
        self.values["config/floor_registry/list"] = HomeAssistantApiError()
        result = await self.go(area_filter="Kitchen")
        self.assertEqual(len(result["entities"]), 4)
        error = await self.refused("source_unavailable", {"area_filter": "Ground"})
        self.assertEqual(error.details, {"reason": "source_unavailable", "source": "floor_registry"})

    async def test_area_filtered_query(self):
        result = await self.go(area_filter="Kitchen", query="light")
        self.assertEqual(result["search_type"], "area_filtered_query")
        ids = [r["entity_id"] for r in result["entities"]]
        self.assertIn("light.kitchen", ids)
        self.assertNotIn("light.porch", ids)
        self.assertEqual(result["warnings"], [reply.WARNING_CONFIG_SKIPPED])
        entries = next(call[1] for call in self.calls if call[0] == "command"
                       and call[1]["type"] == "config/entity_registry/get_entries")
        self.assertEqual(entries["entity_ids"],
                         ["light.hall", "light.kitchen", "sensor.probe", "sensor.temperature"])

    async def test_domain_and_state_listings(self):
        result = await self.go(domain_filter=" LIGHT ")
        self.assertEqual([(r["entity_id"], r["score"], r["match_type"]) for r in result["entities"]],
                         [("light.kitchen", 100, "domain_listing"), ("light.porch", 100, "domain_listing"),
                          ("light.hall", 80, "domain_listing")])
        self.assertEqual((result["search_type"], result["domain_filter"]), ("domain_listing", "light"))
        self.assertEqual(result["applied"]["domain_filter"], "light")
        shown = await self.go(domain_filter="light", include_hidden=False)
        self.assertEqual([r["entity_id"] for r in shown["entities"]], ["light.kitchen", "light.porch"])
        result = await self.go(state_filter="OFF", include_hidden=False)
        self.assertEqual([r["entity_id"] for r in result["entities"]],
                         ["input_boolean.guest_mode", "script.wake", "switch.fan"])
        self.assertEqual(result["search_type"], "state_listing")
        self.assertTrue(result["applied"]["state_filter_set"])
        self.assertNotIn("warnings", result)

    async def test_a_query_with_a_filter_skips_configuration_search(self):
        result = await self.go(query="kitchen", domain_filter="light")
        self.assertEqual(result["warnings"], [reply.WARNING_CONFIG_SKIPPED])
        self.assertFalse(result["applied"]["config_search_ran"])
        self.assertEqual([r["entity_id"] for r in result["entities"]], ["light.kitchen"])
        self.assertNotIn("automation_config", self.reads[0])

    async def test_state_filter_on_a_fuzzy_page_is_page_only(self):
        unfiltered = await self.go(query="light", exact_match=False, limit=2, search_types=None)
        filtered = await self.go(query="light", exact_match=False, limit=2, state_filter="off")
        self.assertEqual(filtered["entity_total_matches"], unfiltered["entity_total_matches"])
        self.assertEqual(filtered["entity_has_more"], unfiltered["entity_has_more"])
        self.assertTrue(all(r["state"] == "off" for r in filtered["entities"]))
        self.assertEqual(filtered["warnings"], [reply.WARNING_CONFIG_SKIPPED, reply.WARNING_STATE_PAGE_ONLY])
        self.assertEqual(filtered["fields_withheld"], 1)  # state_filter_note
        exact = await self.go(query="light", state_filter="off")
        self.assertEqual([r["entity_id"] for r in exact["entities"]], ["light.hall"])
        self.assertNotIn(reply.WARNING_STATE_PAGE_ONLY, exact["warnings"])

    async def test_enrichment_only_when_requested(self):
        plain = await self.go(query="light.kitchen")
        self.assertNotIn("area_registry", self.reads[0])
        self.assertEqual(set(plain["entities"][0]), set(entities.RECORD_KEYS))
        result = await self.go(query="temperature", result_fields=["entity_id", "area", "floor", "labels",
                                                                     "aliases"])
        self.assertEqual(result["entities"], [{"entity_id": "sensor.temperature", "area": "Kitchen",
                                               "floor": "Ground", "labels": ["Night"], "aliases": []}])
        self.assertLessEqual({"area_registry", "floor_registry", "label_registry", "device_registry",
                              "entity_registry_entries"}, set(self.reads[-1]))
        csv = await self.go(query="light.kitchen", result_fields="entity_id,labels")
        self.assertEqual(csv["entities"], [{"entity_id": "light.kitchen", "labels": ["Food"]}])

    async def test_an_unusable_enrichment_source_is_null_and_disclosed(self):
        self.values["config/area_registry/list"] = HomeAssistantApiError()
        result = await self.go(query="temperature", result_fields=["entity_id", "area"])
        self.assertEqual(result["entities"], [{"entity_id": "sensor.temperature", "area": None}])
        self.assertEqual(reasons_of(result), {("source_unavailable", "entity", "area_registry"): 1})

    async def test_membership(self):
        result = await self.go(query="downstairs", result_fields=["entity_id", "member_entity_ids"])
        self.assertEqual(result["entities"], [{"entity_id": "group.downstairs", "is_group": True,
                                               "member_entity_ids": ["light.hall", "light.kitchen"]}])
        hidden = await self.go(query="downstairs", result_fields=["entity_id", "member_entity_ids"],
                                include_hidden=False)
        # A denied member withholds the list, never just the member.
        self.assertEqual(hidden["entities"], [{"entity_id": "group.downstairs", "is_group": True}])
        # The first key whose every item is an entity id wins.
        self.values["states"].extend([
            st("group.mixed", "on", "Mixed", group_entities=["Light.Bad"], entity_id=["light.kitchen"]),
            st("group.invalid", "on", "Invalid", entity_id=["light.kitchen", "not an id"]),
        ])
        for entity_id, expected in (
            ("group.mixed", {"entity_id": "group.mixed", "is_group": True,
                             "member_entity_ids": ["light.kitchen"]}),
            ("group.invalid", {"entity_id": "group.invalid", "is_group": False}),
        ):
            with self.subTest(entity_id=entity_id):
                result = await self.go(query=entity_id, result_fields=["entity_id", "member_entity_ids"])
                self.assertEqual(result["entities"], [expected])

    async def test_a_composed_name_is_never_produced(self):
        result = await self.go(query="light.kitchen", result_fields=["entity_id", "name"])
        self.assertEqual(result["entities"], [{"entity_id": "light.kitchen"}])
        self.assertEqual(result["warnings"], [reply.WARNING_COMPOSED_NAME])


class PartialSourceTests(World):
    async def test_failed_alias_reads_are_partial_in_fuzzy_mode(self):
        # G-h (as relayed by the architect): partial, never a failed read.
        self.values["config/entity_registry/get_entries"] = HomeAssistantApiError()
        result = await self.go(query="kitchen", exact_match=False, search_types=None)
        self.assertEqual(reasons_of(result)[("source_unavailable", "entity", "entity_aliases")], len(STATES))
        self.assertIn("light.kitchen", [r["entity_id"] for r in result["entities"]])
        veranda = await self.go(query="veranda", exact_match=False)
        self.assertNotIn("light.porch", [r["entity_id"] for r in veranda["entities"]])

    async def test_failed_alias_reads_are_partial_in_area_query_mode(self):
        self.values["config/entity_registry/get_entries"] = HomeAssistantApiError()
        result = await self.go(area_filter="Kitchen", query="light")
        self.assertEqual(reasons_of(result)[("source_unavailable", "entity", "entity_aliases")], 4)
        self.assertIn("light.kitchen", [r["entity_id"] for r in result["entities"]])

    async def test_one_failed_entries_chunk_counts_its_entities(self):
        self.values["states"].extend(st(f"light.l{i:03}", "on", f"Lamp {i}") for i in range(600))
        failed = []

        def first_chunk_fails(payload):
            if not failed:
                failed.append(payload["entity_ids"])
                return HomeAssistantApiError()
            return entries_reply(payload)

        self.values["config/entity_registry/get_entries"] = first_chunk_fails
        result = await self.go(query="lamp", domain_filter="light", exact_match=False)
        self.assertEqual(len(failed[0]), c.GET_ENTRIES_CHUNK)
        self.assertEqual(reasons_of(result)[("source_unavailable", "entity", "entity_aliases")],
                         c.GET_ENTRIES_CHUNK)

    async def test_malformed_state_rows_are_one_reason(self):
        # G-f: dropped rows plus kept rows with an invalid state or attributes.
        self.values["states"].extend([
            st("light.bad_state", 7, "Bad state"),
            {**st("light.bad_attributes", "on", "x"), "attributes": "not a mapping"},
            st("Light.Bad", "on", "Bad id"),
        ])
        self.values["config/entity_registry/list"].append(entity_row("light.bad_state", area_id="kitchen"))
        result = await self.go(query="bad", search_types=None)
        self.assertEqual(reasons_of(result)[("source_unavailable", None, "malformed_state_row")], 3)
        self.assertEqual(result["entities"], [])
        area_result = await self.go(area_filter="Kitchen")
        self.assertNotIn("light.bad_state", [r["entity_id"] for r in area_result["entities"]])

    async def test_an_alias_reply_for_another_read_fails_closed(self):
        for value in (ReadResult("states", c.COMPLETE, None, MappingProxyType({})), None):
            with self.subTest(value=value), \
                    patch.object(CoreReadSession, "entity_entries", AsyncMock(return_value=value)):
                error = await self.refused("source_unavailable",
                                           {"query": "kitchen", "exact_match": False, "search_types": None})
                self.assertEqual(error.details, {"reason": "source_unavailable",
                                                 "source": "entity_registry_entries"})


class SourceFailureTests(World):
    async def assertSource(self, source, arguments=None, observed=None):
        error = await self.refused("source_unavailable" if observed is None else "inventory_bound_exceeded",
                                   arguments or {"query": "light.kitchen"})
        details = {"reason": error.reason, "source": source}
        if observed is not None:
            details["observed"] = observed
        self.assertEqual(error.details, details)
        return error

    async def test_required_sources_fail_closed(self):
        self.values["states"] = HomeAssistantUnavailableError()
        await self.assertSource("states")

    async def test_entity_registry_failure_fails_closed(self):
        self.values["config/entity_registry/list"] = HomeAssistantApiError()
        await self.assertSource("entity_registry")
        await self.assertSource("entity_registry", {"query": "guest", "search_types": ["helper"]})
        await self.assertSource("entity_registry", {"query": "movie", "search_types": ["scene"]})

    async def test_registry_drift_fails_closed(self):
        self.values["config/entity_registry/list"][2]["hidden_by"] = 7
        await self.assertSource("entity_registry")

    async def test_dropped_registry_rows_fail_closed(self):
        rows = self.values["config/entity_registry/list"]
        for extra in (entity_row("light.kitchen"), "not a row", {"entity_id": 7}):
            with self.subTest(extra=extra):
                self.values["config/entity_registry/list"] = [*copy.deepcopy(rows), extra]
                await self.assertSource("entity_registry")

    async def test_an_invalid_helper_registry_name_fails_closed(self):
        rows = self.values["config/entity_registry/list"]
        next(row for row in rows if row["entity_id"] == "input_boolean.guest_mode")["name"] = 7
        await self.assertSource("entity_registry", {"query": "guest", "search_types": ["helper"]})

    async def test_authority_and_declared_reads(self):
        self.authorized = False
        await self.assertSource("core_authority")
        self.authorized = True
        self.socket.hook = lambda command: setattr(self.core, "generation", self.core.generation + 1)
        await self.assertSource("core_authority")
        self.socket.hook = None
        original = self.factory
        self.factory = lambda reads: original(tuple(name for name in reads if name != "entity_registry"))
        await self.assertSource("core_reads")

    async def test_an_inventory_over_the_bound(self):
        self.values["states"] = [st(f"sensor.s{i:05}", "1", "S") for i in range(c.MAX_ITEMS + 1)]
        await self.assertSource("states", {"query": "kitchen", "search_types": None}, observed=c.MAX_ITEMS + 1)


class RefusalTests(World):
    CASES = (
        ({"query": 5}, "invalid_argument", "query"),
        ({"query": "x", "unknown": 1}, "invalid_argument", None),
        ({"query": "x", "limit": True}, "invalid_argument", "limit"),
        ({"query": "x", "limit": 0}, "invalid_argument", "limit"),
        ({"query": "x", "limit": 1.0}, "invalid_argument", "limit"),
        ({"query": "x", "offset": -1}, "invalid_argument", "offset"),
        ({"query": "x", "exact_match": "yes"}, "invalid_argument", "exact_match"),
        ({"query": "x", "include_hidden": 1}, "invalid_argument", "include_hidden"),
        ({"query": "x", "include_config": None}, "invalid_argument", "include_config"),
        ({"query": "x", "group_by_domain": "false"}, "invalid_argument", "group_by_domain"),
        ({"query": "x", "per_domain_limit": "3"}, "invalid_argument", "per_domain_limit"),
        ({"query": "x", "config_time_budget": 0}, "invalid_argument", "config_time_budget"),
        ({"query": "x", "config_time_budget": 301}, "invalid_argument", "config_time_budget"),
        ({"query": "x", "config_time_budget": float("nan")}, "invalid_argument", "config_time_budget"),
        ({"query": "x", "config_time_budget": float("inf")}, "invalid_argument", "config_time_budget"),
        ({"query": "x", "config_time_budget": "5"}, "invalid_argument", "config_time_budget"),
        ({"query": "x", "config_time_budget": True}, "invalid_argument", "config_time_budget"),
        ({"query": "x", "area_filter": ["Kitchen"]}, "invalid_argument", "area_filter"),
        ({"query": "x", "search_types": []}, "empty_search_types", "search_types"),
        ({"query": "x", "search_types": ["automation", "entity"]}, "invalid_argument", "search_types"),
        ({"query": "x", "search_types": "automation,script"}, "invalid_argument", "search_types"),
        ({"query": "x", "search_types": [MARKER]}, "invalid_argument", "search_types"),
        ({"query": "x", "result_fields": []}, "empty_result_fields", "result_fields"),
        ({"query": "x", "result_fields": ["entity_id", MARKER]}, "invalid_argument", "result_fields"),
        ({"query": "x", "fields": [1]}, "invalid_argument", "fields"),
        ({}, "no_criteria", "query"),
        ({"query": "   "}, "no_criteria", "query"),
        ({"query": None, "domain_filter": " ", "search_types": ["automation"]}, "no_criteria", "query"),
        ({"domain_filter": "light", "search_types": ["automation"]}, "no_criteria", "query"),
    )

    async def test_refusals_happen_before_any_read(self):
        for arguments, reason, parameter in self.CASES:
            with self.subTest(arguments=arguments):
                error = await self.refused(reason, arguments)
                expected = {"reason": reason}
                if parameter is not None:
                    expected["parameter"] = parameter
                self.assertEqual(error.details, expected)
                self.assertEqual(error.safe_message, SearchError.MESSAGES[reason])
                self.assertNotIn(MARKER, repr(error.details) + error.safe_message)
        self.assertEqual((self.reads, self.calls), ([], []))

    async def test_a_non_mapping_and_a_non_string_key(self):
        for arguments in (["query", "x"], "query=x", None, {1: "x"}):
            with self.subTest(arguments=arguments):
                await self.refused("invalid_argument", arguments)
        self.assertEqual((self.reads, self.calls), ([], []))

    async def test_accepted_argument_forms(self):
        result = await self.go(query="routine", search_types='["automation"]')
        self.assertEqual(result["search_types"], ["automation"])
        result = await self.go(query="routine", search_types=["automation", "automation"])
        # The echo keeps the caller's list; ``applied`` shows the effective types.
        self.assertEqual(result["search_types"], ["automation", "automation"])
        self.assertEqual(result["applied"]["search_types"], ["automation"])
        self.assertEqual(result["warnings"], [reply.WARNING_ENTITY_SKIPPED])
        self.assertEqual(len(self.batches("automation/config")[-1]), 2)
        # A JSON list is never split on its commas.
        result = await self.go(query="light.kitchen", result_fields='["entity_id", "labels"]')
        self.assertEqual(result["entities"], [{"entity_id": "light.kitchen", "labels": ["Food"]}])
        result = await self.go(query="light.kitchen", fields='["entities"]')
        self.assertEqual(set(result) - reply.ALWAYS_KEPT, {"entities"})
        await self.go(query="routine", config_time_budget=0.001, search_types=["script"])
        await self.go(query="routine", config_time_budget=300, search_types=["script"])


class ConfigTests(World):
    async def test_storage_helpers(self):
        result = await self.go(query="guest", search_types=["helper"])
        self.assertEqual([{key: record[key] for key in ("entity_id", "helper_type", "name",
                                                         "match_in_name", "match_in_references")}
                          for record in result["helpers"]],
                         [{"entity_id": "input_boolean.guest_mode", "helper_type": "input_boolean",
                           "name": "Guest toggle", "match_in_name": True, "match_in_references": False}])
        self.assertEqual(set(result["helpers"][0]), {"entity_id", "helper_type", "name", "score",
                                                     "match_in_name", "match_in_config",
                                                     "match_in_references"})
        self.assertEqual(result["entities"], [])
        self.assertEqual(result["entity_total_matches"], 0)
        self.assertNotIn("search_type", result)
        self.assertNotIn("entity_has_more", result)
        self.assertEqual(self.pages, [subcalls.page_arguments("helper", "guest", True, 0)])

    async def test_include_config_withholds_bodies(self):
        result = await self.go(query="guest", search_types=["helper"], include_config=True)
        self.assertEqual(reasons_of(result), {("filtered", "config", "config_bodies_withheld"): 1})
        self.assertEqual(result["fields_withheld"], 1)
        self.assertNotIn("config", result["helpers"][0])
        quiet = await self.go(query="guest", search_types=["helper"])
        self.assertEqual((quiet["partial_reasons"], quiet["fields_withheld"]), ([], 0))

    async def test_helper_lists_that_cannot_be_read(self):
        self.values["input_number/list"] = HomeAssistantApiError()
        self.values["input_boolean/list"].append({"id": "nameless", "icon": "mdi:help"})
        result = await self.go(query="guest", search_types=["helper"])
        self.assertEqual(reasons_of(result), {("not_searched", "helper", "read_failed"): None})
        self.assertEqual(len(result["helpers"]), 1)

    async def test_a_dropped_helper_row_is_counted(self):
        self.values["input_boolean/list"].append({"id": "guest_mode", "name": "Duplicate"})
        result = await self.go(query="guest", search_types=["helper"])
        self.assertEqual(reasons_of(result), {("not_searched", "helper", "read_failed"): 1})

    async def test_the_helper_sub_call(self):
        flow = {"entity_id": "sensor.guest_template", "helper_type": "template", "name": "Guest template",
                "score": 90, "match_in_name": True, "match_in_config": False, "match_in_references": False}
        storage = {"entity_id": "input_boolean.guest_mode", "helper_type": "input_boolean",
                   "name": "Guest mode", "score": 100, "match_in_name": True, "match_in_config": False,
                   "match_in_references": False}

        async def reader(arguments):
            self.pages.append(arguments)
            return page([storage, flow], total=2, withheld={"entry_id"})

        self.readers = {"helper": reader}
        result = await self.go(query="guest", search_types=["helper"])
        names = [record["entity_id"] for record in result["helpers"]]
        # Native storage helpers win; the sub-call's storage rows are dropped.
        self.assertEqual(sorted(names), ["input_boolean.guest_mode", "sensor.guest_template"])
        self.assertEqual(result["config_total_matches"], 2)
        self.assertEqual(result["fields_withheld"], 1)  # entry_id
        self.assertEqual(self.pages, [subcalls.page_arguments("helper", "guest", True, 0)])
        self.assertEqual(self.pages[0]["limit"], 150)

    async def test_sub_call_failures_are_partial(self):
        async def raising(arguments):
            raise RuntimeError(MARKER)

        async def partial(arguments):
            return page(partial=True)

        async def oversized(arguments):
            if arguments["offset"]:
                raise RuntimeError("response_too_large")
            flow = {"helper_type": "template", "name": "Guest flow", "score": 90}
            return page([flow], has_more=True, total=400)

        for readers, expected in (
            ({}, {("source_unavailable", "helper", "subcall_not_admitted"): None}),
            ({"helper": raising}, {("source_unavailable", "helper", "helper_subcall_incomplete"): None}),
            ({"helper": partial}, {("not_searched", "helper", "upstream_partial"): None}),
            ({"helper": oversized}, {("source_unavailable", "helper", "helper_subcall_incomplete"): None}),
        ):
            with self.subTest(readers=readers):
                self.readers = readers
                result = await self.go(query="guest", search_types=["helper"])
                self.assertEqual(reasons_of(result), expected)
                self.assertNotIn(MARKER, json.dumps(result))
                self.assertIn("input_boolean.guest_mode", [r.get("entity_id") for r in result["helpers"]])
        self.assertIn("Guest flow", [r["name"] for r in result["helpers"]])
        # The default types include helper, so no reader is disclosed.
        self.readers = {}
        result = await self.go(query="light.kitchen")
        self.assertEqual(reasons_of(result), {("source_unavailable", "helper", "subcall_not_admitted"): None})

    async def test_invalid_pages_and_scan_bounds_are_incomplete(self):
        good = {"helper_type": "template", "name": "Guest flow", "score": 90}
        bad_pages = (
            page([{**good, "entry_id": "01J"}], total=1),
            page([{**good, "score": 9.5}], total=1),
            page([{"name": "no type", "score": 1}], total=1),
            page([{**good, "name": 7}], total=1),
            page([good], total=-1),
            page([good], total=1, withheld={7}),
            page([], has_more=True, total=1),
            page([good] * (subcalls.PAGE_SIZE + 1), total=1),
            SimpleNamespace(rows=(good,), partial=False, has_more=False, config_total_matches=1,
                            withheld_names=frozenset()),
        )
        for bad in bad_pages:
            with self.subTest(bad=repr(bad)[:60]):
                # A good page would follow; the scan stops at the bad one.
                reader = AsyncMock(side_effect=[bad, page([good], total=1)])
                self.readers = {"helper": reader}
                result = await self.go(query="guest", search_types=["helper"])
                self.assertEqual(reasons_of(result),
                                 {("source_unavailable", "helper", "helper_subcall_incomplete"): None})
                self.assertEqual(reader.await_count, 1)
        moving = iter([page([good], has_more=True, total=3), page([good], has_more=True, total=4)])
        self.readers = {"helper": AsyncMock(side_effect=lambda arguments: next(moving))}
        result = await self.go(query="guest", search_types=["helper"])
        self.assertEqual(reasons_of(result), {("source_unavailable", "helper", "helper_subcall_incomplete"): None})
        self.assertEqual([r["name"] for r in result["helpers"]].count("Guest flow"), 1)
        endless = AsyncMock(return_value=page([good], has_more=True, total=5))
        self.readers = {"helper": endless}
        self.monotonic = itertools.chain([0.0, 0.0, 0.0], itertools.repeat(61.0)).__next__
        result = await self.go(query="guest", search_types=["helper"])
        # A spent deadline sends no page: the reader is not even called.
        self.assertEqual((endless.call_count, endless.await_count), (2, 2))
        self.assertEqual(reasons_of(result), {("source_unavailable", "helper", "helper_subcall_incomplete"): None})
        self.monotonic = time.monotonic
        endless.reset_mock()
        with patch.object(subcalls, "MAX_SCAN_ROWS", 3):
            await self.go(query="guest", search_types=["helper"])
        self.assertEqual(endless.await_count, 3)

    async def test_dashboards_are_held_to_the_page(self):
        board = {"name": "Kitchen board", "score": 80, "match_in_name": True}
        reader = AsyncMock(return_value=page([board] * 3, has_more=True, total=25))
        self.readers = {"dashboard": reader}
        result = await self.go(query="kitchen", search_types=["dashboard"], limit=2)
        self.assertEqual(len(result["dashboards"]), 2)
        self.assertEqual(result["config_total_matches"], 25)
        self.assertEqual((result["config_has_more"], result["config_next_offset"]), (True, 2))
        self.assertEqual(reasons_of(result), {("paged", "config", "more_matches"): 23})
        reader.assert_awaited_once_with(subcalls.page_arguments("dashboard", "kitchen", True, 0))
        plain = await self.go(query="kitchen", search_types=["automation"])
        self.assertNotIn("dashboards", plain)
        # One row a page: the scan reads until it holds offset + limit rows.
        boards = [{"name": f"Board {index}", "score": 80 - index, "match_in_name": True} for index in range(3)]
        reader = AsyncMock(side_effect=[page([board], has_more=True, total=25) for board in boards])
        self.readers = {"dashboard": reader}
        result = await self.go(query="kitchen", search_types=["dashboard"], limit=2)
        self.assertEqual([r["name"] for r in result["dashboards"]], ["Board 0", "Board 1"])
        self.assertEqual(reader.await_count, 2)

    async def test_config_paging_and_order(self):
        result = await self.go(query="light.kitchen", limit=1, offset=1)
        self.assertEqual(result["entities"], [])
        self.assertEqual([r["entity_id"] for r in result["scripts"]], ["script.wake"])
        self.assertEqual((result["config_has_more"], result["config_next_offset"]), (True, 2))
        self.assertEqual((result["has_more"], result["next_offset"]), (True, 2))
        self.assertEqual(reasons_of(result), {("paged", "config", "more_matches"): 1})
        rows = [{"entity_id": f"sensor.{name}", "helper_type": "template", "name": f"Zzz {name}",
                 "score": score} for name, score in (("low", 50), ("high", 90))]
        self.readers = {"helper": AsyncMock(return_value=page(rows, total=2))}
        result = await self.go(query="zzz", search_types=["helper"])
        # Highest score first, whatever order the page held.
        self.assertEqual([r["entity_id"] for r in result["helpers"]], ["sensor.high", "sensor.low"])


class BodyReadTests(World):
    def add_automations(self, count):
        for index in range(count):
            self.values["states"].append(st(f"automation.extra_{index:02}", "on", f"Extra {index}"))

    async def test_a_spent_budget_is_time_budget(self):
        # G-b (as relayed by the architect): each chunk waits at most
        # min(remaining type budget, 5 s).
        self.add_automations(15)

        async def hang(payloads):
            self.calls.append(("batch", payloads))
            await asyncio.Event().wait()

        self.socket.command_batch = hang
        self.monotonic = itertools.chain([0.0, 0.0], itertools.repeat(10.0)).__next__
        result = await self.go(query="routine", search_types=["automation"], config_time_budget=0.05)
        self.assertEqual(reasons_of(result), {("not_searched", "automation", "time_budget"): 17})
        self.assertEqual([r["entity_id"] for r in result["automations"]], ["automation.morning"])
        self.assertEqual(len(self.batches("automation/config")), 1)

    async def test_a_per_item_timeout_is_read_failed(self):
        self.add_automations(15)

        async def hang(payloads):
            self.calls.append(("batch", payloads))
            await asyncio.Event().wait()

        self.socket.command_batch = hang
        with patch.object(configs, "ITEM_TIMEOUT_SECONDS", 0.05):
            result = await self.go(query="routine", search_types=["automation"])
        self.assertEqual(reasons_of(result), {("not_searched", "automation", "read_failed"): 17})
        self.assertEqual([len(batch) for batch in self.batches("automation/config")],
                         [configs.CONFIG_CHUNK, 17 - configs.CONFIG_CHUNK])

    async def test_failed_and_missing_bodies(self):
        self.values["automation/config"] = HomeAssistantApiError()
        result = await self.go(query="routine", search_types=["automation"])
        self.assertEqual(reasons_of(result), {("not_searched", "automation", "read_failed"): 2})
        self.assertEqual([r["entity_id"] for r in result["automations"]], ["automation.morning"])
        self.values["automation/config"] = configs_reply
        with patch.dict(BODIES, clear=False):
            del BODIES["automation.porch_lights"]
            result = await self.go(query="routine", search_types=["automation"])
        self.assertEqual(reasons_of(result), {("not_searched", "automation", "not_readable"): 1})

    async def test_scene_bodies(self):
        result = await self.go(query="movie", search_types=["scene"])
        self.assertEqual(result["scenes"][0]["scene_id"], "movie_uid")
        self.values["/config/scene/config/movie_uid"] = ExpectedHttpStatus(404)
        result = await self.go(query="movie", search_types=["scene"])
        self.assertEqual(reasons_of(result), {("not_searched", "scene", "not_readable"): 1})
        self.assertEqual(result["scenes"][0]["scene_id"], "movie_uid")
        rows = self.values["config/entity_registry/list"]
        next(row for row in rows if row["entity_id"] == "scene.movie")["unique_id"] = "bad id!"
        self.calls.clear()
        result = await self.go(query="movie", search_types=["scene"])
        self.assertEqual(reasons_of(result), {("not_searched", "scene", "not_readable"): 1})
        self.assertFalse([call for call in self.calls if call[0] == "rest" and "scene" in call[2]])

    async def test_an_authority_change_during_a_scene_read_fails_the_search(self):
        self.rest.hook = lambda path: (setattr(self.core, "generation", self.core.generation + 1)
                                       if path.startswith("/config/scene/config/") else None)
        error = await self.refused("source_unavailable", {"query": "movie", "search_types": ["scene"]})
        self.assertEqual(error.details, {"reason": "source_unavailable", "source": "core_authority"})

    async def test_reads_outside_the_read_contract_are_read_failed(self):
        original = CoreReadSession.batch

        async def unavailable(session, name, keys):
            result = await original(session, name, keys)
            if name != "automation_config":
                return result
            # Items present, but the batch itself says unavailable.
            return dataclasses.replace(result, status=c.UNAVAILABLE, reason="source_unavailable")

        with patch.object(CoreReadSession, "batch", unavailable):
            result = await self.go(query="routine", search_types=["automation"])
        self.assertEqual(reasons_of(result), {("not_searched", "automation", "read_failed"): 2})
        body = ReadResult("scene_config", c.COMPLETE, None, ["not", "a", "body"])
        with patch.object(CoreReadSession, "scene_config", AsyncMock(return_value=body)):
            result = await self.go(query="movie", search_types=["scene"])
        self.assertEqual(reasons_of(result), {("not_searched", "scene", "read_failed"): 1})

    async def test_config_matches_find_targets(self):
        result = await self.go(query="switch.fan", search_types=["script"])
        self.assertEqual([(r["entity_id"], r["match_in_config"]) for r in result["scripts"]],
                         [("script.wake", True)])


class GraphTests(World):
    async def test_an_unreadable_graph_is_disclosed(self):
        self.values["search/related"] = HomeAssistantApiError()
        result = await self.go(query="light.kitchen")
        self.assertEqual(reasons_of(result), {("not_searched", "config", "graph_unavailable"): None})
        self.assertEqual(result["scripts"], [])
        self.assertFalse(any(r["match_in_references"] for r in result["automations"]))

    async def test_graph_drift_is_unavailable(self):
        for related in ({"automation": ["script.wake"]}, {"automation": "automation.morning"},
                        {"script": ["script.Wake"]}):
            with self.subTest(related=related):
                with patch.dict(RELATED, {"light.kitchen": related}):
                    result = await self.go(query="light.kitchen")
                self.assertEqual(reasons_of(result)[("not_searched", "config", "graph_unavailable")], None)
                self.assertEqual(result["scripts"], [])

    async def test_a_non_ascii_entity_id_query_is_never_sent(self):
        result = await self.go(query="light.k٣")
        self.assertEqual(reasons_of(result), {("not_searched", "config", "graph_unavailable"): None})
        self.assertEqual(self.batches("search/related"), [])

    async def test_the_graph_needs_an_entity_id_shaped_query(self):
        result = await self.go(query="kitchen")
        self.assertNotIn("search_related", self.reads[0])
        self.assertEqual(self.batches("search/related"), [])
        self.assertNotIn(("not_searched", "config", "graph_unavailable"), reasons_of(result))
        await self.go(query="light.kitchen", search_types=["helper"])
        self.assertNotIn("search_related", self.reads[-1])


class ReplyTests(World):
    async def test_fields_projection(self):
        result = await self.go(query="light.kitchen", fields=["entities"])
        self.assertEqual(set(result) - reply.ALWAYS_KEPT, {"entities"})
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["config_total_matches"], 3)
        csv = await self.go(query="light.kitchen", fields="entities,scripts")
        self.assertEqual(set(csv) - reply.ALWAYS_KEPT, {"entities", "scripts"})
        self.assertEqual(csv["count"], 2)

    async def test_the_response_budget_pages(self):
        self.values["states"].extend(st(f"sensor.s{i:03}", "1", f"Sensor number {i}") for i in range(200))
        budget = 6_000
        with patch.object(engine, "build_reply", functools.partial(reply.build_reply, budget=budget)):
            result = await self.go(domain_filter="sensor", limit=200)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=True)), budget)
        kept = len(result["entities"])
        self.assertGreater(kept, 0)
        self.assertEqual((result["entity_has_more"], result["entity_next_offset"]), (True, kept))
        total = result["entity_total_matches"]
        self.assertEqual(total, 204)  # 4 fixture sensors + 200
        self.assertEqual(reasons_of(result), {("paged", "entity", "response_budget"): total - kept})
        # Records stop with the margin still free, not at the edge.
        self.assertLessEqual(reply._size(result) + reply.FIT_MARGIN_BYTES // 2, budget)

    async def test_the_first_record_is_admitted_without_the_margin(self):
        full = await self.go(domain_filter="switch")
        self.assertEqual(len(full["entities"]), 1)
        budget = reply._size(full) + reply.FIT_MARGIN_BYTES // 2
        with patch.object(engine, "build_reply", functools.partial(reply.build_reply, budget=budget)):
            result = await self.go(domain_filter="switch")
        self.assertEqual(result, full)

    async def test_long_lists_are_capped_and_disclosed(self):
        members = [f"light.member_{index:04}" for index in range(1_200)]
        self.values["states"].append(st("group.big", "on", "Big group", entity_id=members))
        result = await self.go(query="group.big", result_fields=["entity_id", "member_entity_ids"])
        record = result["entities"][0]
        kept = record["member_entity_ids"]
        self.assertLess(len(kept), len(members))
        self.assertEqual(kept, sorted(members)[:len(kept)])
        self.assertLessEqual(reply._size(record), reply.RECORD_BUDGET_BYTES)
        self.assertEqual(reasons_of(result)[("truncated", "entity", "record_lists")], len(members) - len(kept))

    async def test_long_strings_are_truncated_and_disclosed(self):
        self.values["states"][0]["attributes"]["friendly_name"] = "Kitchen " + "y" * 300
        result = await self.go(query="light.kitchen", search_types=None)
        self.assertLess(len(result["entities"][0]["friendly_name"]), 300)
        self.assertEqual(reasons_of(result)[("truncated", "entity", "record_strings")], 1)
        long_query = await self.go(query="z" * 1_100, search_types=["automation"])
        self.assertLess(len(long_query["query"]), 1_100)
        self.assertEqual(reasons_of(long_query), {("truncated", None, "query_echo"): 1})

    async def test_known_secrets_are_redacted(self):
        self.values["states"][0]["attributes"]["friendly_name"] = f"Kitchen {SECRET}"
        self.secrets = (SECRET,)
        result = await self.go(query="light.kitchen", search_types=None)
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertEqual(reasons_of(result), {("filtered", None, "sanitizer_redaction"): 1})

    async def test_a_sanitizer_failure_withholds_the_reply(self):
        def failed(value, **kwargs):
            # The value passes through: only the failed flag can withhold it.
            return SimpleNamespace(failed_closed=True, value=value, redacted_field_count=0,
                                   truncated_field_count=0)

        nothing = SimpleNamespace(failed_closed=True, value=None, redacted_field_count=0,
                                  truncated_field_count=0)
        for patched in ({"side_effect": failed}, {"return_value": nothing}):
            with self.subTest(patched=list(patched)), patch.object(reply, "sanitize_untrusted_data", **patched):
                error = await self.refused("sanitization_failed", {"query": "light.kitchen"})
            self.assertEqual(error.details, {"reason": "sanitization_failed"})

    async def test_group_by_domain_is_withheld(self):
        result = await self.go(query="light.kitchen", group_by_domain=True)
        self.assertEqual(result["fields_withheld"], 1)
        self.assertNotIn("by_domain", result)


class SearchErrorTests(unittest.TestCase):
    """The refusal details carry closed values only (design section 5.3)."""

    def test_details_are_closed(self):
        self.assertEqual(SearchError("invalid_argument", parameter="limit").details,
                         {"reason": "invalid_argument", "parameter": "limit"})
        self.assertEqual(SearchError("source_unavailable", source="states", observed=3).details,
                         {"reason": "source_unavailable", "source": "states", "observed": 3})
        for kwargs in ({"parameter": MARKER}, {"source": f"{MARKER} x"}, {"source": 7},
                       {"observed": -1}, {"observed": True}, {"observed": 1.0}, {"observed": "3"}):
            with self.subTest(kwargs=kwargs):
                error = SearchError("invalid_argument", **kwargs)
                self.assertEqual(error.details, {"reason": "invalid_argument"})
                self.assertNotIn(MARKER, repr(error.details) + error.safe_message)

    def test_an_unknown_reason_is_sanitization_failed(self):
        error = SearchError(MARKER)
        self.assertEqual((error.reason, error.details), ("sanitization_failed", {"reason": "sanitization_failed"}))
        self.assertEqual(error.safe_message, SearchError.MESSAGES["sanitization_failed"])


if __name__ == "__main__":
    unittest.main()
