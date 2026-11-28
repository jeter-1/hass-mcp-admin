"""HAMCP-481 W2: the fixed Core reads of native ``ha_search``.

Fixtures model the Home Assistant Core 2026.11.3 (92da9f15) reply shapes of
``GET /api/states``, the area, floor, label, entity and device registry
lists, ``config/entity_registry/get_entries``, ``search/related`` and the six
``input_*/list`` collections. HAMCP-501 W6 S1 b+ (approved by Josh as relayed
by the architect) removed the ``automation/config``, ``script/config`` and
scene configuration reads: those bodies are searched only through the
delegated sub-call, which scrubs them. Everything is synthetic; no live Home
Assistant. The module is not registered or routed, so these tests drive it
directly.
"""

from __future__ import annotations

import ast
import asyncio
import copy
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from ha_mcp_engineering.clients import websocket as ws  # noqa: E402
from ha_mcp_engineering.clients.paths import require_route  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    AuthorizationError,
    ErrorCode,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ha_mcp_engineering.ha_core_readmission.profiles import CORE_CAPABILITY_PROFILES  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.source import MAX_CORE_PROBE_ITEMS  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.search import core_contracts as c  # noqa: E402
from ha_mcp_engineering.search import core_reads as r  # noqa: E402
from ha_mcp_engineering.search.core_reads import INVALID, CoreReadSession  # noqa: E402

PACKAGE = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering"
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
CORE_VERSION = "2026.11.3"
# The signed profiles J2' option A allows (as relayed by the architect).
APPROVED_REQUIREMENTS = frozenset({
    "core.basic_rest_read", "core.basic_websocket_read", "core.non_device_registry_read",
    "core.direct_device_registry_read", "core.automation_configuration_read",
    "core.direct_entity_state_read",
})
# Every read: (transport, command, requirement, attested, shape).
EXPECTED_TABLE = {
    "states": ("rest", "GET /states", "core.basic_rest_read", True, "rows"),
    "area_registry": ("websocket", "config/area_registry/list", "core.non_device_registry_read", True, "rows"),
    "floor_registry": ("websocket", "config/floor_registry/list", "core.non_device_registry_read", True, "rows"),
    "label_registry": ("websocket", "config/label_registry/list", "core.non_device_registry_read", True, "rows"),
    "entity_registry": ("websocket", "config/entity_registry/list", "core.non_device_registry_read", True,
                        "rows"),
    "device_registry": ("websocket", "config/device_registry/list", "core.direct_device_registry_read", True,
                        "rows"),
    "search_related": ("websocket", "search/related", "core.basic_websocket_read", False, "related"),
    **{f"{domain}_list": ("websocket", f"{domain}/list", "core.basic_websocket_read", False, "rows")
       for domain in ("input_boolean", "input_number", "input_select", "input_text", "input_datetime",
                      "input_button")},
    "entity_registry_entries": ("websocket", "config/entity_registry/get_entries",
                                "core.basic_websocket_read", False, "entries"),
}
# The signed check that pins each attested read's exact command and shape.
ATTESTING_CHECK = {
    "states": "rest_states_list",
    "area_registry": "websocket_area_registry_list",
    "floor_registry": "websocket_floor_registry_list",
    "label_registry": "websocket_label_registry_list",
    "entity_registry": "websocket_entity_registry_list",
    "device_registry": "websocket_device_registry_list",
}
# HAMCP-501 W6 S1 b+: the body reads native search no longer has.
REMOVED_READS = ("automation_config", "script_config", "scene_config")


def state(entity_id, value="on"):
    return {"entity_id": entity_id, "state": value, "attributes": {"friendly_name": entity_id.title()},
            "last_changed": "2026-11-25T11:00:00+00:00", "context": {"id": "01JSYNTHETICCONTEXT"}}


def entity_row(entity_id, **values):
    # Core 2026.11.3 ``as_partial_dict``: no ``aliases`` and no ``has_entity_name``.
    # ``next_name_part`` is ``_entity_next_name_part`` (entity_registry.py 252-260).
    part = ("area" if values.get("area_id") is not None
            else "device" if values.get("device_id") is not None else None)
    return {"area_id": None, "categories": {}, "config_entry_id": "01JSYNTHETICENTRY", "config_subentry_id": None,
            "created_at": 1.0, "device_id": None, "disabled_by": None, "entity_category": None,
            "entity_id": entity_id, "hidden_by": None, "icon": None, "id": "01JSYNTHETICID" + entity_id,
            "labels": [], "modified_at": 1.0, "name": None, "next_name_part": part, "options": {},
            "original_name": entity_id.title(), "platform": "synthetic", "translation_key": None,
            "unique_id": entity_id + "_uid", **values}


def healthy():
    return {
        "states": [state("light.kitchen"), state("sensor.temperature", "21")],
        "config/area_registry/list": [
            {"aliases": ["Cookhouse"], "area_id": "kitchen", "floor_id": "ground", "humidity_entity_id": None,
             "icon": None, "labels": ["food"], "name": "Kitchen", "picture": None,
             "temperature_entity_id": None, "created_at": 1.0, "modified_at": 1.0}],
        "config/floor_registry/list": [
            {"aliases": [], "created_at": 1.0, "floor_id": "ground", "icon": None, "level": 0,
             "name": "Ground", "modified_at": 1.0}],
        "config/label_registry/list": [
            {"color": None, "created_at": 1.0, "description": None, "icon": None, "label_id": "food",
             "name": "Food", "modified_at": 1.0}],
        "config/entity_registry/list": [entity_row("light.kitchen", area_id="kitchen"),
                                        entity_row("sensor.temperature", device_id="dev1")],
        # Core 2026.11.3 ``DeviceEntry.dict_repr`` and the child row
        # (device_registry.py 750-791, 891-910): no manufacturer or model on a child.
        "config/device_registry/list": [
            {"area_id": "kitchen", "id": "dev1", "labels": [], "name": "Thermo", "name_by_user": None,
             "parent_device_id": None, "disabled_by": None, "manufacturer": "Synthetic",
             "model": "Synthetic model", "next_name_part": "area"},
            {"area_id": None, "id": "dev2", "labels": [], "name": "Probe", "name_by_user": "Probe 2",
             "parent_device_id": "dev1", "disabled_by": None, "next_name_part": "parent_device"}],
        **{f"{domain}/list": [{"id": "guest_mode", "name": "Guest mode", "icon": "mdi:account"}]
           for domain in ("input_boolean", "input_number", "input_select", "input_text", "input_datetime",
                          "input_button")},
    }


class SyntheticCore:
    def __init__(self):
        self.unavailable: set[str] = set()
        self.generation = 7
        self.current_observation = SimpleNamespace(version=CORE_VERSION)
        self.route_calls: list[tuple[str, ...]] = []

    def route_status(self, requirements):
        self.route_calls.append(requirements)
        return {"available": bool(requirements) and not set(requirements) & self.unavailable,
                "disposition": "admitted", "reason_codes": [], "generation": self.generation}


class SyntheticRest:
    def __init__(self, values, calls):
        self.values, self.calls, self.hook = values, calls, None

    async def request(self, method, path, **kwargs):
        self.calls.append(("rest", method, str(path), kwargs))
        if self.hook:
            self.hook(str(path))
        value = self.values.get(str(path), self.values.get("states") if str(path) == "/states" else None)
        if isinstance(value, BaseException):
            raise value
        return copy.deepcopy(value)


class SyntheticWebSocket:
    def __init__(self, values, calls):
        self.values, self.calls, self.hook = values, calls, None

    async def command(self, payload):
        self.calls.append(("command", copy.deepcopy(payload)))
        if self.hook:
            self.hook(payload["type"])
        value = self.values[payload["type"]]
        if callable(value):
            value = value(payload)
        if isinstance(value, BaseException):
            raise value
        return copy.deepcopy(value)

    async def command_batch(self, payloads):
        assert all(ws.batch_command_allowed(payload) for payload in payloads), payloads
        self.calls.append(("batch", copy.deepcopy(payloads)))
        if self.hook:
            self.hook(payloads[0]["type"])
        value = self.values[payloads[0]["type"]]
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            return value(payloads)
        return copy.deepcopy(value)


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.values = healthy()
        self.calls: list = []
        self.rest = SyntheticRest(self.values, self.calls)
        self.socket = SyntheticWebSocket(self.values, self.calls)
        self.core = SyntheticCore()
        self.telemetry, token = begin_request()
        self.addCleanup(end_request, token)
        self.telemetry.caller_id = "synthetic_authenticated_caller"
        self.authorized = True
        self.telemetry.core_dispatch_authorizer = lambda: self.authorized

    def session(self, *reads):
        return CoreReadSession(self.rest, self.socket, self.core, reads or tuple(c.READS), clock=lambda: NOW)

    def assertRefused(self, reason, error):
        self.assertIsInstance(error.exception, c.CoreReadError)
        self.assertEqual(error.exception.reason, reason)
        self.assertEqual(error.exception.details, {"reason": reason})


class ContractTableTests(unittest.TestCase):
    def test_the_table_is_exactly_design_2_2_plus_get_entries(self):
        self.assertEqual(
            {name: (read.transport, read.command, read.requirement, read.attested, read.shape)
             for name, read in c.READS.items()},
            EXPECTED_TABLE)
        self.assertTrue(all(read.name == name for name, read in c.READS.items()))
        with self.assertRaises(TypeError):
            c.READS["extra"] = c.READS["states"]

    def test_requirements_are_existing_signed_profiles_only(self):
        profiles = {profile.capability_id: profile for profile in CORE_CAPABILITY_PROFILES}
        self.assertLessEqual(set(c.CORE_REQUIREMENTS), APPROVED_REQUIREMENTS)
        self.assertLessEqual(set(c.CORE_REQUIREMENTS), set(profiles))
        self.assertEqual({read.requirement for read in c.READS.values()}, set(c.CORE_REQUIREMENTS))
        # The states list is ``rest_states_list``; the single-entity profile is unused.
        self.assertNotIn("core.direct_entity_state_read", c.CORE_REQUIREMENTS)
        self.assertEqual(c.requirements_for(c.READS), c.CORE_REQUIREMENTS)
        self.assertEqual(c.requirements_for(["search_related", "states"]),
                         ("core.basic_rest_read", "core.basic_websocket_read"))
        # HAMCP-501 W6 S1 b+: no read needs the automation configuration profile.
        self.assertNotIn("core.automation_configuration_read", c.CORE_REQUIREMENTS)

    def test_attested_reads_are_pinned_by_their_profile_and_the_rest_ride_basic_transport(self):
        profiles = {profile.capability_id: profile for profile in CORE_CAPABILITY_PROFILES}
        self.assertEqual({name for name, read in c.READS.items() if read.attested}, set(ATTESTING_CHECK))
        for name, check in ATTESTING_CHECK.items():
            self.assertIn(check, profiles[c.READS[name].requirement].required_checks, name)
        for read in c.READS.values():
            if not read.attested:
                self.assertIn(read.requirement, {"core.basic_rest_read", "core.basic_websocket_read"})

    def test_every_read_is_read_only(self):
        for read in c.READS.values():
            last = read.command.rsplit("/", 1)[-1]
            self.assertIn(last, {"states", "list", "related", "get_entries"}, read.name)
        source = (PACKAGE / "search/core_reads.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        methods = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "request":
                    methods.append(node.args[0].value)
                self.assertNotIn(node.func.attr, {"call_service", "subscribe", "send_json", "write"})
        self.assertEqual(set(methods), {"GET"})

    def test_batched_payloads_are_ones_the_client_admits(self):
        # HAMCP-501 W6 S1 b+: ``search/related`` is the only batched read.
        self.assertEqual(c.BATCH_READS, {"search_related"})
        payload = c.batch_payload("search_related", "light.kitchen")
        self.assertTrue(ws.batch_command_allowed(payload), payload)
        self.assertEqual(payload, {"type": "search/related", "item_type": "entity", "item_id": "light.kitchen"})
        # Collections and ``get_entries`` are single commands, never batched.
        for name in ("input_boolean_list", "entity_registry_entries", "states"):
            self.assertFalse(ws.batch_command_allowed({"type": c.READS[name].command}))
        for name, item in (("automation_config", "automation.morning"), ("script_config", "script.wake"),
                           ("scene_config", "abc_1"), ("search_related", "Light.Kitchen"),
                           ("search_related", 7), ("states", "light.kitchen"), (7, "light.kitchen")):
            with self.assertRaises(c.CoreReadError) as error:
                c.batch_payload(name, item)
            self.assertEqual(error.exception.reason, "invalid_arguments")

    def test_s1_body_reads_are_gone_from_the_contract(self):
        # HAMCP-501 W6 S1 b+ (approved by Josh as relayed by the architect):
        # no automation, script or scene configuration body is read natively.
        for name in REMOVED_READS:
            self.assertNotIn(name, c.READS)
        for attribute in ("AUTOMATION_ENTITY", "SCRIPT_ENTITY", "SCENE_CONFIG_ID", "AUTOMATION_CONFIGURATION"):
            self.assertFalse(hasattr(c, attribute), attribute)
        for attribute in ("project_config", "project_scene_config"):
            self.assertFalse(hasattr(r, attribute), attribute)
        self.assertFalse(hasattr(CoreReadSession, "scene_config"))
        self.assertEqual({read.shape for read in c.READS.values()}, {"rows", "related", "entries"})
        for read in c.READS.values():
            self.assertNotIn(read.command, {"automation/config", "script/config", "GET /config/scene/config/{}"})

    def test_rest_routes_are_already_reviewed_get_routes(self):
        # No ``clients/paths.py`` change: the one REST read passes the existing route table.
        route = require_route(r.rest_route("states"), "GET")
        self.assertEqual(c.READS["states"].command, f"GET {route.template}")
        self.assertEqual(route.template, "/states")
        # Only ``states`` has a route; S1 b+ removed the scene configuration GET.
        for name, arguments in (("states", ("x",)), ("scene_config", ()), ("scene_config", ("abc_1",)),
                                ("area_registry", ()), ("search_related", ("light.kitchen",))):
            with self.subTest(name=name, arguments=arguments), self.assertRaises(c.CoreReadError) as error:
                r.rest_route(name, *arguments)
            self.assertEqual(error.exception.reason, "invalid_arguments")

    def test_bounds(self):
        self.assertEqual(c.MAX_ITEMS, MAX_CORE_PROBE_ITEMS)
        self.assertEqual(c.MAX_BATCH_ITEMS, ws.MAX_BATCH_COMMANDS)
        self.assertEqual(c.GET_ENTRIES_CHUNK, 500)

    def test_entries_payload_refuses_a_bad_chunk(self):
        self.assertEqual(c.entries_payload(("light.kitchen",)),
                         {"type": "config/entity_registry/get_entries", "entity_ids": ["light.kitchen"]})
        for chunk in ((), ["light.kitchen"], ("light.kitchen", "Light.Bad"),
                      tuple(f"light.l{i}" for i in range(501))):
            with self.assertRaises(c.CoreReadError):
                c.entries_payload(chunk)

    def test_result_reasons_are_closed(self):
        self.assertEqual(r._unavailable("states", "free text").reason, "source_unavailable")
        self.assertEqual(r._unavailable("states", "timeout").reason, "timeout")
        result = r._finished("states", None, {"free text", "duplicate_rows"})
        self.assertEqual((result.status, result.partial), (c.PARTIAL, ("duplicate_rows", "field_drift")))
        self.assertEqual(r._finished("states", None, set()).status, c.COMPLETE)

    def test_error_reasons_are_closed(self):
        self.assertEqual(c.CoreReadError("authority_unavailable").code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(c.CoreReadError("invalid_arguments").code, ErrorCode.INVALID_REQUEST)
        error = c.CoreReadError("free text from somewhere")
        self.assertEqual((error.reason, error.details), ("authority_unavailable", {"reason": "authority_unavailable"}))


class AuthorityTests(Base):
    async def test_anonymous_caller_is_refused_before_any_route_check(self):
        self.telemetry.caller_id = "anonymous"
        with self.assertRaises(c.CoreReadError) as error:
            self.session("states")
        self.assertRefused("authority_unavailable", error)
        self.assertEqual((self.core.route_calls, self.calls), ([], []))

    async def test_reads_must_be_declared_known_and_unique(self):
        for reads in ((), ("states", "states"), ("states", "unknown"), ("states", 7), ("states", ["x"])):
            with self.subTest(reads=reads), self.assertRaises(c.CoreReadError) as error:
                CoreReadSession(self.rest, self.socket, self.core, reads)
            self.assertRefused("invalid_arguments", error)
        with self.assertRaises(c.CoreReadError):
            CoreReadSession(self.rest, self.socket, self.core, "states")
        self.assertEqual(self.calls, [])

    async def test_only_the_declared_reads_requirements_are_checked(self):
        self.session("states")
        self.session("entity_registry", "device_registry", "entity_registry_entries")
        self.assertEqual(self.core.route_calls, [
            ("core.basic_rest_read",),
            ("core.basic_websocket_read", "core.non_device_registry_read", "core.direct_device_registry_read"),
        ])

    async def test_a_degraded_requirement_refuses_before_dispatch(self):
        self.core.unavailable = {"core.direct_device_registry_read"}
        with self.assertRaises(c.CoreReadError) as error:
            self.session("states", "device_registry")
        self.assertRefused("authority_unavailable", error)
        # A search that does not read devices is unaffected.
        result = await self.session("states").inventory("states")
        self.assertEqual(result.status, c.COMPLETE)

    async def test_missing_observation_authorizer_or_generation_refuses(self):
        cases = {
            "observation": lambda: setattr(self.core, "current_observation", None),
            "authorizer": lambda: setattr(self.telemetry, "core_dispatch_authorizer", None),
            "authorizer_false": lambda: setattr(self, "authorized", False),
            "generation": lambda: setattr(self.core, "generation", None),
        }
        for label, breaker in cases.items():
            with self.subTest(label):
                self.setUp()
                breaker()
                with self.assertRaises(c.CoreReadError) as error:
                    self.session("states")
                self.assertRefused("authority_unavailable", error)
                self.assertEqual(self.calls, [])

    async def test_a_changed_generation_or_version_before_a_read_refuses_without_dispatch(self):
        for change in (lambda: setattr(self.core, "generation", 8),
                       lambda: setattr(self.core, "current_observation", SimpleNamespace(version="2026.12.0"))):
            self.setUp()
            session = self.session("states")
            change()
            with self.assertRaises(c.CoreReadError) as error:
                await session.inventory("states")
            self.assertRefused("authority_unavailable", error)
            self.assertEqual(self.calls, [])

    async def test_a_change_during_a_read_refuses_after_it(self):
        session = self.session("entity_registry")
        self.socket.hook = lambda kind: setattr(self.core, "generation", 8)
        with self.assertRaises(c.CoreReadError) as error:
            await session.inventory("entity_registry")
        self.assertRefused("authority_unavailable", error)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(session.sources, [])

    async def test_an_undeclared_read_or_wrong_method_refuses(self):
        session = self.session("states", "search_related")
        for call in (lambda: session.inventory("entity_registry"),
                     lambda: session.inventory("search_related"),
                     lambda: session.batch("states", ["light.kitchen"]),
                     lambda: session.batch("automation_config", ["automation.morning"]),
                     lambda: session.batch("script_config", ["script.wake"]),
                     lambda: session.entity_entries(["light.kitchen"])):
            with self.assertRaises(c.CoreReadError) as error:
                await call()
            self.assertRefused("invalid_arguments", error)
        self.assertEqual(self.calls, [])


class InventoryTests(Base):
    async def test_every_list_read_issues_its_exact_command_and_projects_its_fields(self):
        session = self.session()
        for name, read in c.READS.items():
            if read.shape != "rows":
                continue
            with self.subTest(name):
                self.calls.clear()
                result = await session.inventory(name)
                expected_call = (("rest", "GET", "/states", {}) if name == "states"
                                 else ("command", {"type": read.command}))
                self.assertEqual(self.calls, [expected_call])
                self.assertEqual((result.status, result.reason, result.partial, result.dropped),
                                 (c.COMPLETE, None, (), 0))
        self.assertEqual([source["source"] for source in session.sources],
                         [name for name, read in c.READS.items() if read.shape == "rows"])
        self.assertTrue(all(source["status"] == c.COMPLETE and source["observed_at"] == "2026-11-25T12:00:00Z"
                            and source["observed"] is None for source in session.sources))

    async def test_projection_keeps_only_the_fixed_fields(self):
        session = self.session()
        states = await session.inventory("states")
        self.assertEqual(dict(states.value)["light.kitchen"], {
            "entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Light.Kitchen"}})
        entities = await session.inventory("entity_registry")
        self.assertEqual(entities.value["light.kitchen"], {
            "entity_id": "light.kitchen", "name": None, "original_name": "Light.Kitchen",
            "platform": "synthetic", "unique_id": "light.kitchen_uid", "area_id": "kitchen", "device_id": None,
            "labels": (), "hidden_by": None, "disabled_by": None, "entity_category": None,
            "next_name_part": "area"})
        areas = await session.inventory("area_registry")
        self.assertEqual(areas.value["kitchen"], {"area_id": "kitchen", "name": "Kitchen", "aliases": ("Cookhouse",),
                                                  "floor_id": "ground", "labels": ("food",)})
        floors = await session.inventory("floor_registry")
        self.assertEqual(floors.value["ground"], {"floor_id": "ground", "name": "Ground", "aliases": (), "level": 0})
        devices = await session.inventory("device_registry")
        self.assertEqual(devices.value["dev1"], {
            "id": "dev1", "name": "Thermo", "name_by_user": None, "area_id": "kitchen",
            "parent_device_id": None, "labels": (), "disabled_by": None, "manufacturer": "Synthetic",
            "model": "Synthetic model", "next_name_part": "area"})
        self.assertEqual(devices.value["dev2"], {
            "id": "dev2", "name": "Probe", "name_by_user": "Probe 2", "area_id": None,
            "parent_device_id": "dev1", "labels": (), "disabled_by": None, "next_name_part": "parent_device"})
        # Helper rows are kept whole for body search.
        helpers = await session.inventory("input_select_list")
        self.assertEqual(helpers.value["guest_mode"], {"id": "guest_mode", "name": "Guest mode", "icon": "mdi:account"})
        with self.assertRaises(TypeError):
            states.value["new"] = {}

    async def test_has_entity_name_is_optional_but_typed(self):
        session = self.session("entity_registry")
        self.values["config/entity_registry/list"] = [entity_row("light.a", has_entity_name=True),
                                                      entity_row("light.b")]
        result = await session.inventory("entity_registry")
        self.assertEqual(result.status, c.COMPLETE)
        self.assertIs(result.value["light.a"]["has_entity_name"], True)
        self.assertNotIn("has_entity_name", result.value["light.b"])
        self.values["config/entity_registry/list"] = [entity_row("light.a", has_entity_name="yes")]
        result = await session.inventory("entity_registry")
        self.assertEqual((result.status, result.partial), (c.PARTIAL, ("field_drift",)))
        self.assertIs(result.value["light.a"]["has_entity_name"], INVALID)

    async def test_naming_wire_fields_are_optional_but_typed(self):
        # HAMCP-501 W6b: an absent ``next_name_part`` is derived by the search,
        # and a child device has no manufacturer or model. A present value
        # must still be a string or null.
        session = self.session("entity_registry", "device_registry")
        bare = {key: value for key, value in entity_row("light.b").items() if key != "next_name_part"}
        self.values["config/entity_registry/list"] = [entity_row("light.a"), bare]
        result = await session.inventory("entity_registry")
        self.assertEqual(result.status, c.COMPLETE)
        self.assertNotIn("next_name_part", result.value["light.b"])
        device = {"area_id": None, "id": "dev9", "labels": [], "name": "Synthetic", "name_by_user": None,
                  "parent_device_id": None, "disabled_by": None, "manufacturer": "Synthetic",
                  "model": None, "next_name_part": None}
        self.values["config/device_registry/list"] = [
            {key: value for key, value in device.items() if key != absent} | {"id": f"dev{index}"}
            for index, absent in enumerate(("manufacturer", "model", "next_name_part"))]
        result = await session.inventory("device_registry")
        self.assertEqual(result.status, c.COMPLETE)
        for index, absent in enumerate(("manufacturer", "model", "next_name_part")):
            self.assertNotIn(absent, result.value[f"dev{index}"])
        for field_name in ("manufacturer", "model", "next_name_part"):
            self.values["config/device_registry/list"] = [{**device, field_name: 7}]
            result = await session.inventory("device_registry")
            self.assertEqual((result.status, result.partial), (c.PARTIAL, ("field_drift",)))
            self.assertIs(result.value["dev9"][field_name], INVALID)
        self.values["config/entity_registry/list"] = [entity_row("light.a", next_name_part=["area"])]
        result = await session.inventory("entity_registry")
        self.assertEqual((result.status, result.partial), (c.PARTIAL, ("field_drift",)))
        self.assertIs(result.value["light.a"]["next_name_part"], INVALID)

    async def test_field_drift_is_partial_never_silent(self):
        session = self.session("area_registry", "floor_registry", "entity_registry")
        area = healthy()["config/area_registry/list"][0]
        self.values["config/area_registry/list"] = [{**area, "aliases": "Cookhouse"},
                                                    {**area, "area_id": "den", "labels": ["ok", 3]},
                                                    {k: v for k, v in area.items() if k != "name"} | {"area_id": "hall"}]
        result = await session.inventory("area_registry")
        self.assertEqual((result.status, result.reason, result.partial), (c.PARTIAL, None, ("field_drift",)))
        self.assertIs(result.value["kitchen"]["aliases"], INVALID)
        self.assertIs(result.value["den"]["labels"], INVALID)
        self.assertIs(result.value["hall"]["name"], INVALID)
        self.assertEqual(session.sources[-1]["partial"], ["field_drift"])
        floor = healthy()["config/floor_registry/list"][0]
        self.values["config/floor_registry/list"] = [{**floor, "level": True}, {**floor, "floor_id": "up", "level": None}]
        result = await session.inventory("floor_registry")
        self.assertIs(result.value["ground"]["level"], INVALID)
        self.assertIsNone(result.value["up"]["level"])
        self.assertEqual(result.status, c.PARTIAL)
        self.values["config/entity_registry/list"] = [entity_row("light.a", platform=None)]
        result = await session.inventory("entity_registry")
        self.assertIs(result.value["light.a"]["platform"], INVALID)

    async def test_one_missing_required_field_alone_is_partial(self):
        session = self.session("area_registry")
        area = healthy()["config/area_registry/list"][0]
        self.values["config/area_registry/list"] = [{k: v for k, v in area.items() if k != "floor_id"}]
        result = await session.inventory("area_registry")
        self.assertEqual((result.status, result.partial), (c.PARTIAL, ("field_drift",)))
        self.assertIs(result.value["kitchen"]["floor_id"], INVALID)

    async def test_malformed_and_duplicate_rows_are_dropped_and_disclosed(self):
        session = self.session("states", "device_registry")
        self.values["states"] = [state("light.kitchen"), "text", {"state": "on"}, state("Light.Bad"),
                                 state("light.kitchen", "off"), state("sensor.ok")]
        result = await session.inventory("states")
        self.assertEqual((result.status, result.partial, result.dropped),
                         (c.PARTIAL, ("duplicate_rows", "malformed_rows"), 4))
        self.assertEqual(list(result.value), ["light.kitchen", "sensor.ok"])
        self.assertEqual(result.value["light.kitchen"]["state"], "on")
        self.values["config/device_registry/list"] = [{"id": "x\n", "name": "Bad"}, {"id": "", "name": "Bad"}]
        result = await session.inventory("device_registry")
        self.assertEqual((result.status, result.reason), (c.UNAVAILABLE, "invalid_response"))

    async def test_wrong_top_level_shapes_and_the_inventory_bound_fail_closed(self):
        session = self.session("states", "label_registry")
        for value, reason, observed in (
                ({"entity_id": "light.kitchen"}, "invalid_response", None), (None, "invalid_response", None),
                ([state(f"sensor.s{i}") for i in range(c.MAX_ITEMS + 1)], "inventory_bound_exceeded",
                 c.MAX_ITEMS + 1)):
            self.values["states"] = value
            result = await session.inventory("states")
            self.assertEqual((result.status, result.reason, result.value, result.observed, result.dropped),
                             (c.UNAVAILABLE, reason, None, observed, 0))
            self.assertEqual((session.sources[-1]["reason"], session.sources[-1]["observed"]), (reason, observed))
        self.values["states"] = [state(f"sensor.s{i}") for i in range(c.MAX_ITEMS)]
        result = await session.inventory("states")
        self.assertEqual((result.status, len(result.value), result.observed), (c.COMPLETE, c.MAX_ITEMS, None))
        self.assertIsNone(session.sources[-1]["observed"])
        self.values["config/label_registry/list"] = []
        result = await session.inventory("label_registry")
        self.assertEqual((result.status, dict(result.value)), (c.COMPLETE, {}))

    async def test_exceptions_map_to_closed_reasons_without_text(self):
        session = self.session("entity_registry")
        cases = (
            (AuthorizationError(details={"secret": "synthetic-needle-x"}), "permission_failure"),
            (HomeAssistantTimeoutError(), "timeout"),
            (HomeAssistantUnavailableError(), "home_assistant_unavailable"),
            (HomeAssistantApiError(details={"status": 500}), "command_failed"),
            (RuntimeError("synthetic-needle-y"), "source_unavailable"),
        )
        with patch.object(r.METRICS, "record_provider_result") as metrics:
            for error, reason in cases:
                self.values["config/entity_registry/list"] = error
                result = await session.inventory("entity_registry")
                self.assertEqual((result.status, result.reason, result.value), (c.UNAVAILABLE, reason, None))
                self.assertNotIn("synthetic-needle", repr(result) + repr(session.sources))
        self.assertEqual([call.args for call in metrics.call_args_list], [("direct_ha_api", "failed")] * len(cases))
        self.values["config/entity_registry/list"] = asyncio.CancelledError()
        with patch.object(r.METRICS, "record_provider_result") as metrics, \
                self.assertRaises(asyncio.CancelledError):
            await session.inventory("entity_registry")
        self.assertEqual(metrics.call_args.args, ("direct_ha_api", "failed"))
        self.values["config/entity_registry/list"] = [entity_row("light.a")]
        with patch.object(r.METRICS, "record_provider_result") as metrics:
            await session.inventory("entity_registry")
        self.assertEqual(metrics.call_args.args, ("direct_ha_api", "complete"))


class BatchTests(Base):
    async def test_body_reads_are_not_reads(self):
        # HAMCP-501 W6 S1 b+: a session cannot even declare a removed body read.
        for name in REMOVED_READS:
            with self.subTest(name), self.assertRaises(c.CoreReadError) as error:
                self.session("states", name)
            self.assertRefused("invalid_arguments", error)
        self.assertEqual((self.core.route_calls, self.calls), ([], []))

    async def test_a_batch_that_fails_everywhere_or_changes_length_is_unavailable(self):
        session = self.session("search_related")
        self.values["search/related"] = [(False, "unknown_error"), "bad"]
        result = await session.batch("search_related", ["light.a", "light.b"])
        self.assertEqual((result.status, result.reason), (c.UNAVAILABLE, "command_failed"))
        self.values["search/related"] = [(True, {})]
        result = await session.batch("search_related", ["light.a", "light.b"])
        self.assertEqual((result.status, result.reason), (c.UNAVAILABLE, "invalid_response"))
        self.values["search/related"] = HomeAssistantTimeoutError()
        result = await session.batch("search_related", ["light.a"])
        self.assertEqual((result.status, result.reason), (c.UNAVAILABLE, "timeout"))

    async def test_bad_items_refuse_before_dispatch(self):
        session = self.session("search_related")
        for name, items in (("search_related", ["light.ok", "Light.Wrong"]),
                            ("search_related", ["light.a", "light.a"]),
                            ("search_related", "light.a"),
                            ("search_related", [f"light.l{i}" for i in range(c.MAX_BATCH_ITEMS + 1)]),
                            ("search_related", ["light.ok", None]),
                            ("search_related", ["light.ok", ["light.unhashable"]])):
            with self.assertRaises(c.CoreReadError) as error:
                await session.batch(name, items)
            self.assertRefused("invalid_arguments", error)
        self.assertEqual(self.calls, [])
        result = await session.batch("search_related", [])
        self.assertEqual((result.status, self.calls, session.sources), (c.COMPLETE, [], []))

    async def test_related_buckets_are_projected_fail_closed(self):
        session = self.session("search_related")
        self.values["search/related"] = [
            (True, {"automation": ["automation.morning"], "area": ["kitchen"], "automation_blueprint": ["x/y.yaml"]}),
            (True, {}),
            (True, {"automation": "automation.morning"}),
            (True, {"new_core_type": ["x"]}),
            (True, ["not", "a", "mapping"]),
            (False, "unknown_error"),
        ]
        items = ["light.a", "light.b", "light.c", "light.d", "light.e", "light.f"]
        result = await session.batch("search_related", items)
        self.assertEqual(self.calls[0][1][0], {"type": "search/related", "item_type": "entity", "item_id": "light.a"})
        outcomes = result.items
        self.assertEqual(dict(outcomes["light.a"].value), {"automation": ("automation.morning",), "area": ("kitchen",)})
        self.assertEqual((outcomes["light.b"].status, dict(outcomes["light.b"].value)), (c.COMPLETE, {}))
        self.assertIs(outcomes["light.c"].value["automation"], INVALID)
        self.assertEqual((outcomes["light.c"].status, outcomes["light.c"].partial), (c.PARTIAL, ("field_drift",)))
        self.assertEqual((outcomes["light.d"].status, dict(outcomes["light.d"].value)), (c.PARTIAL, {}))
        self.assertEqual((outcomes["light.e"].status, outcomes["light.e"].reason), (c.UNAVAILABLE, "invalid_response"))
        self.assertEqual((outcomes["light.f"].status, outcomes["light.f"].reason), (c.UNAVAILABLE, "command_failed"))
        self.assertEqual((result.status, result.partial), (c.PARTIAL, ("field_drift", "item_failures")))

    async def test_a_related_bucket_with_one_bad_element_is_invalid(self):
        session = self.session("search_related")
        for bucket in (["automation.ok", 7], ["automation.ok", "bad\n"], ["automation.ok", ""]):
            self.values["search/related"] = [(True, {"automation": bucket})]
            result = await session.batch("search_related", ["light.a"])
            outcome = result.items["light.a"]
            self.assertIs(outcome.value["automation"], INVALID, bucket)
            self.assertEqual((outcome.status, result.partial), (c.PARTIAL, ("field_drift",)))


class EntityEntriesTests(Base):
    def entries(self, payload):
        reply = {}
        for entity_id in payload["entity_ids"]:
            reply[entity_id] = {**entity_row(entity_id), "aliases": [entity_id.title(), None],
                                "capabilities": None, "device_class": None, "original_device_class": None,
                                "original_icon": None}
        return reply

    async def test_get_entries_is_chunked_at_500_and_keeps_string_aliases_only(self):
        session = self.session("entity_registry_entries")
        self.values["config/entity_registry/get_entries"] = self.entries
        entity_ids = [f"light.l{i}" for i in range(1201)]
        result = await session.entity_entries(entity_ids)
        self.assertEqual([call[1]["entity_ids"] for call in self.calls],
                         [entity_ids[:500], entity_ids[500:1000], entity_ids[1000:]])
        self.assertTrue(all(call[1]["type"] == "config/entity_registry/get_entries" and set(call[1]) ==
                            {"type", "entity_ids"} for call in self.calls))
        self.assertEqual((result.status, len(result.value)), (c.COMPLETE, 1201))
        # ``None`` is Core's computed-name sentinel and is dropped.
        self.assertEqual(dict(result.value["light.l0"]), {"aliases": ("Light.L0",)})
        self.assertEqual(len(session.sources), 1)

    async def test_unknown_entities_and_drift(self):
        session = self.session("entity_registry_entries")
        self.values["config/entity_registry/get_entries"] = {
            "light.none": None, "light.ok": {"aliases": ["Desk", None, "Lamp"]}, "light.bad": {"aliases": "Desk"},
            "light.int": {"aliases": ["Desk", 3]}, "light.noaliases": {}, "light.list": ["x"],
            "light.unrequested": None,
        }
        requested = ["light.none", "light.ok", "light.bad", "light.int", "light.noaliases", "light.list",
                     "light.missing"]
        result = await session.entity_entries(requested)
        self.assertEqual((result.status, result.partial), (c.PARTIAL, ("field_drift",)))
        self.assertIsNone(result.value["light.none"])
        self.assertEqual(dict(result.value["light.ok"]), {"aliases": ("Desk", "Lamp")})
        for entity_id in ("light.bad", "light.int", "light.noaliases"):
            self.assertIs(result.value[entity_id]["aliases"], INVALID)
        self.assertIs(result.value["light.list"], INVALID)
        self.assertNotIn("light.missing", result.value)
        self.assertNotIn("light.unrequested", result.value)

    async def test_each_entries_drift_alone_is_partial(self):
        session = self.session("entity_registry_entries")
        good = {"aliases": ["Desk"]}
        for label, reply, expected in (
            ("unrequested", {"light.a": good, "light.extra": good}, {"light.a": {"aliases": ("Desk",)}}),
            ("missing", {}, {}),
            ("not_a_mapping", {"light.a": "Desk"}, {"light.a": INVALID}),
            ("aliases_absent", {"light.a": {}}, {"light.a": {"aliases": INVALID}}),
        ):
            with self.subTest(label):
                self.values["config/entity_registry/get_entries"] = reply
                result = await session.entity_entries(["light.a"])
                self.assertEqual((result.status, result.partial), (c.PARTIAL, ("field_drift",)))
                self.assertEqual({key: value if value is INVALID else dict(value)
                                  for key, value in result.value.items()}, expected)

    async def test_a_failed_chunk_is_partial_and_all_failing_is_unavailable(self):
        session = self.session("entity_registry_entries")

        def first_chunk_fails(payload):
            if payload["entity_ids"][0] == "light.l0":
                return HomeAssistantApiError()
            return self.entries(payload)

        self.values["config/entity_registry/get_entries"] = first_chunk_fails
        entity_ids = [f"light.l{i}" for i in range(600)]
        result = await session.entity_entries(entity_ids)
        self.assertEqual((result.status, result.partial), (c.PARTIAL, ("item_failures",)))
        self.assertEqual(sorted(result.value), sorted(entity_ids[500:]))
        self.values["config/entity_registry/get_entries"] = ["not", "a", "mapping"]
        result = await session.entity_entries(entity_ids)
        self.assertEqual((result.status, result.reason), (c.UNAVAILABLE, "invalid_response"))
        self.values["config/entity_registry/get_entries"] = HomeAssistantTimeoutError()
        result = await session.entity_entries(entity_ids)
        self.assertEqual((result.status, result.reason), (c.UNAVAILABLE, "timeout"))

    async def test_bad_entity_ids_refuse_before_any_chunk_is_sent(self):
        session = self.session("entity_registry_entries")
        valid = [f"light.l{i}" for i in range(600)]
        for entity_ids in (valid + ["Light.Bad"], valid + ["light.l0"], "light.l0", valid + [{"x": 1}],
                           [f"light.l{i}" for i in range(c.MAX_ITEMS + 1)]):
            with self.assertRaises(c.CoreReadError) as error:
                await session.entity_entries(entity_ids)
            self.assertRefused("invalid_arguments", error)
        self.assertEqual(self.calls, [])
        result = await session.entity_entries([])
        self.assertEqual((result.status, dict(result.value), self.calls), (c.COMPLETE, {}, []))

    async def test_authority_is_rechecked_between_chunks(self):
        session = self.session("entity_registry_entries")
        self.values["config/entity_registry/get_entries"] = self.entries
        self.socket.hook = lambda kind: setattr(self.core, "generation", 8)
        with self.assertRaises(c.CoreReadError) as error:
            await session.entity_entries([f"light.l{i}" for i in range(600)])
        self.assertRefused("authority_unavailable", error)
        self.assertEqual(len(self.calls), 1)


class ReachabilityTests(unittest.TestCase):
    def test_core_reads_import_only_reviewed_modules(self):
        for module, allowed in (
            ("core_contracts", {"__future__", "dataclasses", "re", "types", "entity_ids", "errors"}),
            ("core_reads", {"__future__", "asyncio", "dataclasses", "datetime", "types", "typing",
                            "clients.paths", "entity_ids", "errors", "observability",
                            "request_context", None}),
        ):
            imported = set()
            for node in ast.walk(ast.parse((PACKAGE / f"search/{module}.py").read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.add(node.module)
            self.assertEqual(imported, allowed, module)

    def test_no_runtime_module_outside_search_imports_it(self):
        importers = []
        for path in PACKAGE.rglob("*.py"):
            relative = path.relative_to(PACKAGE.parent)
            if relative.parts[1] == "search":
                continue
            package = list(relative.parts[:-1])
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base = package[:len(package) - node.level + 1] if node.level else []
                    module = ".".join(base + ([node.module] if node.module else []))
                    names = [module] + [f"{module}.{alias.name}" for alias in node.names]
                hits = [n for n in names
                        if n == "ha_mcp_engineering.search" or n.startswith("ha_mcp_engineering.search.")]
                if hits:
                    importers.append((str(relative), tuple(sorted(hits))))
        # HAMCP-501 W4: the gateway's sub-call page readers take the fixed W3
        # page contract. W6: the application composes CoreReadSession with no
        # I/O, and the native tool reaches only the engine, SearchError and
        # (HAMCP-523) warning_codes.
        # Nothing else outside search imports the package.
        subcalls = "ha_mcp_engineering.search.subcalls"
        self.assertEqual(sorted(importers), [
            ("ha_mcp_engineering/application.py", (
                "ha_mcp_engineering.search.core_reads",
                "ha_mcp_engineering.search.core_reads.CoreReadSession")),
            # HAMCP-519: get_helper_settings reads through the fixed Core read
            # contracts (the entity registry and input_*/list rows) and the
            # session's INVALID marker; it adds no Core command.
            ("ha_mcp_engineering/helper_settings/contracts.py", (
                "ha_mcp_engineering.search", "ha_mcp_engineering.search.core_contracts")),
            ("ha_mcp_engineering/helper_settings/service.py", (
                "ha_mcp_engineering.search", "ha_mcp_engineering.search.core_contracts")),
            ("ha_mcp_engineering/helper_settings/service.py", (
                "ha_mcp_engineering.search.core_reads",
                "ha_mcp_engineering.search.core_reads.INVALID")),
            ("ha_mcp_engineering/providers/upstream_read_gateway.py", (
                subcalls, f"{subcalls}.BUCKETS", f"{subcalls}.MAX_SCAN_ROWS",
                f"{subcalls}.PAGE_SIZE_LADDER", f"{subcalls}.PageResult",
                f"{subcalls}.PageTooLarge", f"{subcalls}.page_arguments")),
            ("ha_mcp_engineering/tools/search.py", (
                "ha_mcp_engineering.search", "ha_mcp_engineering.search.engine")),
            ("ha_mcp_engineering/tools/search.py", (
                "ha_mcp_engineering.search.reply", "ha_mcp_engineering.search.reply.SearchError",
                "ha_mcp_engineering.search.reply.warning_codes")),
        ])


if __name__ == "__main__":
    unittest.main()
