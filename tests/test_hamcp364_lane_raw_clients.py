"""HAMCP-364: the acceptance lanes' own clients reach only the disposable Core of their lane.

That Core is on loopback, except the Thread/Matter A2 lane's, which is on the
internal network its runner created and reaches the lane through one hand-over.

Offline. A stand-in session records every construction, connection, frame and
request; nothing here opens a socket, and the Core harness is never run.
"""
import ast
import copy
import importlib.util
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "hass_mcp_engineering_beta")]
if str(ROOT / "tests") not in sys.path:
    sys.path.append(str(ROOT / "tests"))
import configuration_delete_contract_acceptance as config_delete  # noqa: E402
import core_thread_matter_topology_a2_local as a2_runner  # noqa: E402
import created_object_contract_acceptance as created  # noqa: E402
import device_configuration_contract_acceptance as device  # noqa: E402
import entity_metadata_contract_acceptance as entity  # noqa: E402
import lane_session as shared  # noqa: E402
import real_ha_contract_tests as harness  # noqa: E402
import security_metadata_contract_acceptance as security  # noqa: E402
import seq13_contract_acceptance as seq13  # noqa: E402
import seq15_contract_acceptance as seq15  # noqa: E402
import thread_matter_topology_a2_acceptance as a2  # noqa: E402
import thread_matter_topology_contract_acceptance as thread  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from support.process_isolation import restore_after_class  # noqa: E402

SCRIPTS = ROOT / "scripts"
HARNESS_PATH = SCRIPTS / "real_ha_contract_tests.py"
MAIN_CORE, MAIN_SOCKET = "http://127.0.0.1:8123", "ws://127.0.0.1:8123/api/websocket"
LOCAL_CORE, LOCAL_SOCKET = "http://127.0.0.1:18123", "ws://127.0.0.1:18123/api/websocket"
# The Thread/Matter lanes: A1's Core on a loopback port of its own; A2's on the internal
# bridge its runner creates, here a synthetic address inside a synthetic subnet.
THREAD_CORE, THREAD_SOCKET = "http://127.0.0.1:18124", "ws://127.0.0.1:18124/api/websocket"
BRIDGE_CORE, BRIDGE_SUBNET = "http://172.30.0.2:8123", "172.30.0.0/16"
BRIDGE_SOCKET = "ws://172.30.0.2:8123/api/websocket"
# Reserved documentation addresses and names; never a real peer.
ELSEWHERE, ELSEWHERE_NAME = "198.51.100.7", "core.example.invalid"
TOKEN = "synthetic-token-364"
HEX = "0a1b2c3d"
URL_REFUSED, COMMAND_REFUSED = "raw_core_url_not_pinned", "raw_core_command_not_listed"


class Text(str):
    """A str subclass that behaves like its text."""


class Claims(str):
    """A str subclass that answers every comparison in its own favour."""

    __hash__ = str.__hash__

    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False


class Mapping(dict):
    """A dict subclass that behaves like its items."""


class Hides(dict):
    """A dict subclass that shows fewer keys than it holds."""

    shown = ("type",)

    def keys(self):
        return {key: None for key in self.shown}.keys()


# Every step a client takes between its command check and its command frame.
STAGES = ("session", "session entered", "ws_connect", "socket entered", "auth_required", "auth sent", "auth_ok")


def fake_session(events, reply=None, during=lambda stage: None):
    """Stand-in for aiohttp.ClientSession: records its construction, connection and frames.

    ``during`` is told each step before the command frame, as the caller's own
    code would run while the client waits there.
    """
    class Socket:
        def __init__(self):
            self.replies = [{"type": "auth_required"}]

        async def __aenter__(self):
            during("socket entered")
            return self

        async def __aexit__(self, *_exc):
            return False

        async def receive_json(self):
            message = self.replies.pop(0)
            if message["type"] in ("auth_required", "auth_ok"):
                during(message["type"])
            return message

        async def send_json(self, payload):
            events.append(("send", payload))
            if payload.get("type") == "auth":
                during("auth sent")
                self.replies.append({"type": "auth_ok"})
            else:
                self.replies.append({"type": "result", "id": payload["id"], "success": True,
                                     "result": None if reply is None else reply(payload)})

    class Session:
        def __init__(self, **kwargs):
            events.append(("session", kwargs))
            during("session")

        async def __aenter__(self):
            during("session entered")
            return self

        async def __aexit__(self, *_exc):
            return False

        def ws_connect(self, url, **kwargs):
            events.append(("ws_connect", url, kwargs))
            during("ws_connect")
            return Socket()

        def request(self, method, url, **kwargs):
            events.append(("request", method, url, kwargs))
            raise AssertionError("no REST request belongs to these tests")

    return Session


def configured(url, api_url=MAIN_CORE + "/api", **more):
    return SimpleNamespace(websocket_url=url, api_url=api_url, ha_token=TOKEN, **more)


def configured_local(url):
    """Settings for the created-object client, which also reads the REST root of its Core."""
    return configured(url, LOCAL_CORE + "/api")


def shape_signatures(commands):
    return {(shape["type"], frozenset(shape)) for shape in commands}


E, S, Q, X = entity, seq13, seq15, security
ENTITY_EXAMPLES = (
    {"type": E.GET, "entity_id": "input_boolean.hamcp179_entity_metadata_" + HEX},
    {"type": E.GET, "entity_id": E.ABSENT},
    {"type": E.UPDATE, "entity_id": E.HUB_SWITCH, "area_id": "hamcp179_study"},
    {"type": E.UPDATE, "entity_id": E.HUB_SWITCH, "name": None},
    {"type": E.UPDATE, "entity_id": E.HUB_SWITCH, "name": "HAMCP179 Hub", "area_id": "hamcp179_study"},
    {"type": E.UPDATE, "entity_id": E.HUB_SWITCH, "area_id": None, "name": None},
    {"type": E.UPDATE, "entity_id": E.HUB_SWITCH, "area_id": "synthetic_area", "name": None},
    {"type": E.UPDATE, "entity_id": "light.synthetic", "labels": ["hamcp179_night", E.UNKNOWN_LABEL]},
    {"type": E.UPDATE, "entity_id": "light.synthetic", "labels": []},
    {"type": E.UPDATE, "entity_id": "light.synthetic", "aliases": [" Lamp ", "", None]},
    {"type": E.UPDATE, "entity_id": "light.synthetic", "aliases": []},
    {"type": E.UPDATE, "entity_id": "light.synthetic", "aliases": ["Synthetic Lamp", None]},
    {"type": "config/area_registry/create", "name": "HAMCP179 Study"},
    {"type": "config/label_registry/create", "name": "HAMCP179 Night"},
    {"type": "config/label_registry/create", "name": "HAMCP179 Outdoor"},
    {"type": "config/category_registry/create", "scope": E.CATEGORY_SCOPE, "name": "HAMCP179 Lighting"},
    {"type": "input_boolean/create", "name": E.HELPER_NAME + " " + HEX},
    {"type": "config/device_registry/list"},
    {"type": "config/area_registry/list"},
    {"type": "config/category_registry/list", "scope": E.CATEGORY_SCOPE},
    {"type": "input_boolean/list"},
    {"type": "config/label_registry/list"},
    {"type": "input_boolean/delete", "input_boolean_id": "hamcp179_entity_metadata_" + HEX},
    {"type": "config/label_registry/delete", "label_id": "hamcp179_night"},
    {"type": "config/category_registry/delete", "scope": E.CATEGORY_SCOPE, "category_id": "01SYNTHETICCATEGORY"},
    {"type": "config/area_registry/delete", "area_id": "hamcp179_study"},
)
ENTITY_NEAR = (
    {"type": E.GET}, {"type": E.GET, "entity_id": ""}, {"type": E.GET, "entity_id": 7},
    # The area and name rule is exercised on the one hub entity only.
    {"type": E.UPDATE, "entity_id": "light.synthetic", "area_id": "synthetic_area"},
    {"type": E.UPDATE, "entity_id": "light.synthetic", "name": None},
    {"type": E.UPDATE, "entity_id": "light.synthetic", "area_id": None, "name": None},
    {"type": E.UPDATE, "entity_id": E.HUB_SWITCH, "name": "HAMCP179 Hub"},
    {"type": E.UPDATE, "entity_id": E.HUB_SWITCH, "disabled_by": "user"},
    {"type": E.UPDATE, "entity_id": E.HUB_SWITCH, "area_id": 7, "name": None},
    {"type": E.UPDATE, "entity_id": E.HUB_SWITCH, "area_id": "", "name": None},
    {"type": E.UPDATE, "entity_id": E.HUB_SWITCH, "area_id": None, "name": None, "hidden_by": "user"},
    {"type": E.UPDATE, "entity_id": "light.synthetic", "labels": "hamcp179_night"},
    {"type": E.UPDATE, "entity_id": "light.synthetic", "labels": ("hamcp179_night",)},
    {"type": E.UPDATE, "entity_id": "light.synthetic", "labels": ["hamcp179_night", None]},
    {"type": E.UPDATE, "entity_id": "light.synthetic", "labels": [""]},
    {"type": E.UPDATE, "entity_id": "light.synthetic", "aliases": ["Lamp", 7]},
    {"type": E.UPDATE, "entity_id": "light.synthetic", "aliases": [""]},
    {"type": E.UPDATE, "entity_id": "light.synthetic", "aliases": None},
    {"type": E.UPDATE, "entity_id": "light.synthetic", "labels": [], "aliases": []},
    {"type": E.UPDATE, "entity_id": "light.synthetic", "labels": [], "disabled_by": "user"},
    {"type": E.UPDATE, "entity_id": "", "labels": []},
    {"type": "config/area_registry/create", "name": "Synthetic Other"},
    {"type": "config/label_registry/create", "name": "HAMCP179 Lighting"},
    {"type": "config/category_registry/create", "scope": "automation", "name": "HAMCP179 Lighting"},
    {"type": "config/category_registry/create", "name": "HAMCP179 Lighting"},
    {"type": "input_boolean/create", "name": E.HELPER_NAME},
    {"type": "input_boolean/create", "name": E.HELPER_NAME + " 0A1B2C3D"},
    {"type": "input_boolean/create", "name": E.HELPER_NAME + " " + HEX + "\n"},
    {"type": "input_boolean/create", "name": E.HELPER_NAME + " " + HEX + "0"},
    {"type": "input_boolean/create", "name": "Synthetic " + E.HELPER_NAME + " " + HEX},
    {"type": "input_boolean/create", "name": E.HELPER_NAME + " " + HEX, "initial": True},
    {"type": "config/category_registry/list"},
    {"type": "config/category_registry/list", "scope": "automation"},
    {"type": "config/area_registry/list", "scope": E.CATEGORY_SCOPE},
    # Another lane's read, and writes no lane sends.
    {"type": "config/entity_registry/list"},
    {"type": "config/entity_registry/remove", "entity_id": "light.synthetic"},
    {"type": "config/device_registry/update", "device_id": "synthetic", "area_id": "synthetic_area"},
    {"type": "config/floor_registry/delete", "floor_id": "synthetic"},
    {"type": "config/area_registry/delete"}, {"type": "config/area_registry/delete", "area_id": ""},
    {"type": "config/area_registry/delete", "area_id": ["hamcp179_study"]},
    {"type": "config/category_registry/delete", "category_id": "01SYNTHETICCATEGORY"},
)
CONFIG_DELETE_EXAMPLES = ({"type": config_delete.REGISTRY_LIST},)
CONFIG_DELETE_NEAR = (
    *ENTITY_EXAMPLES,
    {"type": config_delete.REGISTRY_LIST, "domain": "automation"},
    {"type": "config/entity_registry/list_for_display"},
    {"type": "config/entity_registry/get", "entity_id": "automation.synthetic"},
    {"type": "config/entity_registry/remove", "entity_id": "automation.synthetic"},
)
# The scene lane sends one command; the helper lane sends that one and four more of its own.
CREATED_SCENE_EXAMPLES = ({"type": created.REGISTRY_LIST},)
CREATED_HELPER_EXAMPLES = (
    {"type": created.REGISTRY_LIST}, {"type": "input_boolean/list"}, {"type": "input_number/list"},
    # One per helper the lane creates: the toggle, the target, the mixed plan's and the withheld probes'.
    {"type": "input_boolean/delete", "input_boolean_id": "hamcp_g4_toggle_" + HEX},
    {"type": "input_boolean/delete", "input_boolean_id": "hamcp_g4_mixed_" + HEX},
    {"type": "input_boolean/delete", "input_boolean_id": "hamcp_g4_withheld_" + HEX},
    {"type": "input_number/delete", "input_number_id": "hamcp_g4_target_" + HEX},
    {"type": "input_number/delete", "input_number_id": "hamcp_g4_withheld_" + HEX},
)
CREATED_NEAR = (
    # A helper the lane does not own, by each way an id can miss the lane's pattern.
    *({"type": "input_boolean/delete", "input_boolean_id": other} for other in (
        "users_own_helper", "seq13_lane_" + HEX, "hamcp_g4_", "hamcp_g4", "HAMCP_G4_TOGGLE_" + HEX,
        "hamcp_g4_Toggle_" + HEX, "hamcp_g4_toggle_" + HEX + "\n", " hamcp_g4_toggle_" + HEX,
        "x_hamcp_g4_toggle_" + HEX, "input_boolean.hamcp_g4_toggle_" + HEX, "hamcp_g4_toggle-" + HEX,
        "hamcp_g4_toggle/" + HEX, "hamcp_g4_" + "a" * 49, ["hamcp_g4_toggle_" + HEX], None, 7)),
    {"type": "input_number/delete", "input_number_id": "users_own_helper"},
    # The id under the other domain's field, under both, or under none.
    {"type": "input_boolean/delete", "input_number_id": "hamcp_g4_toggle_" + HEX},
    {"type": "input_number/delete", "input_boolean_id": "hamcp_g4_target_" + HEX},
    {"type": "input_boolean/delete", "input_boolean_id": "hamcp_g4_toggle_" + HEX,
     "input_number_id": "hamcp_g4_toggle_" + HEX},
    {"type": "input_boolean/delete"}, {"type": "input_number/delete"},
    # The helper creates and the scene and automation writes are Engineering's, never this client's.
    {"type": "input_boolean/create", "name": "HAMCP G4 toggle " + HEX},
    {"type": "input_number/create", "name": "HAMCP G4 target " + HEX, "min": 16, "max": 30},
    {"type": "input_boolean/update", "input_boolean_id": "hamcp_g4_toggle_" + HEX, "name": "Synthetic"},
    # Another helper domain, and reads or writes this lane does not send.
    {"type": "input_select/list"}, {"type": "input_text/list"}, {"type": "input_boolean/list", "domain": "x"},
    {"type": "input_select/delete", "input_select_id": "hamcp_g4_toggle_" + HEX},
    {"type": created.REGISTRY_LIST, "domain": "scene"},
    {"type": "config/entity_registry/list_for_display"},
    {"type": "config/entity_registry/get", "entity_id": "input_boolean.hamcp_g4_toggle_" + HEX},
    {"type": "config/entity_registry/remove", "entity_id": "input_boolean.hamcp_g4_toggle_" + HEX},
    {"type": "config/entity_registry/update", "entity_id": "input_boolean.hamcp_g4_toggle_" + HEX, "name": "x"},
    {"type": "config/device_registry/list"}, {"type": "config/label_registry/list"},
    {"type": "call_service", "domain": "scene", "service": "delete",
     "service_data": {"entity_id": "scene.hamcp_g4_scene_" + HEX}},
)
# What only the helper lane sends is as far from the scene lane's list as any other command.
CREATED_HELPER_ONLY = CREATED_HELPER_EXAMPLES[1:]
CREATED_SCENE_NEAR = (*CREATED_NEAR, *CREATED_HELPER_ONLY)
SEQ13_EXAMPLES = (
    {"type": S.ENTITY_GET, "entity_id": "input_boolean.seq13_lane_" + HEX},
    {"type": S.ENTITY_GET, "entity_id": S.ABSENT},
    {"type": S.REGISTRY_LIST}, {"type": S.LABEL_LIST}, {"type": "input_boolean/list"},
    {"type": "config_entries/get"},
    {"type": "input_boolean/create", "name": "Seq13 Lane " + HEX},
    {"type": S.LABEL_UPDATE, "label_id": S.CANARY_ID, "description": "Seq-13 raw drift"},
    {"type": S.ENTITY_UPDATE, "entity_id": "input_boolean.seq13_lane_" + HEX, "name": "Seq13 Raw Drift"},
    {"type": S.ENTITY_UPDATE, "entity_id": "sensor.synthetic_backed", "disabled_by": "user"},
    {"type": "input_boolean/delete", "input_boolean_id": "seq13_lane_" + HEX},
    {"type": "config/label_registry/delete", "label_id": S.CANARY_ID},
)
SEQ13_NEAR = (
    {"type": S.LABEL_UPDATE, "label_id": "synthetic_other", "description": "Seq-13 raw drift"},
    {"type": S.LABEL_UPDATE, "label_id": S.CANARY_ID, "description": "Synthetic other"},
    {"type": S.LABEL_UPDATE, "label_id": S.CANARY_ID, "name": "Synthetic other"},
    {"type": S.LABEL_UPDATE, "label_id": S.CANARY_ID},
    {"type": S.LABEL_CREATE, "name": "Synthetic other"},
    {"type": S.ENTITY_UPDATE, "entity_id": "sensor.synthetic", "name": "Synthetic other"},
    {"type": S.ENTITY_UPDATE, "entity_id": "sensor.synthetic", "disabled_by": None},
    {"type": S.ENTITY_UPDATE, "entity_id": "sensor.synthetic", "disabled_by": "integration"},
    {"type": S.ENTITY_UPDATE, "entity_id": "sensor.synthetic", "name": "Seq13 Raw Drift", "disabled_by": "user"},
    {"type": S.ENTITY_UPDATE, "entity_id": "sensor.synthetic", "hidden_by": "user"},
    {"type": S.ENTITY_UPDATE, "entity_id": "sensor.synthetic", "labels": []},
    {"type": S.ENTITY_UPDATE, "entity_id": "", "disabled_by": "user"},
    {"type": S.ENTITY_UPDATE, "disabled_by": "user"},
    {"type": S.EXPOSE, "assistants": ["conversation"], "entity_ids": ["sensor.synthetic"], "should_expose": True},
    {"type": "input_boolean/create", "name": "Seq13 Lane 0a1b2c3"},
    {"type": "input_boolean/create", "name": "Seq13 Lane " + HEX + "0"},
    {"type": "input_boolean/create", "name": "Seq13 Lane " + HEX + "\n"},
    {"type": "input_boolean/create", "name": "Synthetic Seq13 Lane " + HEX},
    {"type": "input_boolean/create", "name": "Seq13 Lane 0A1B2C3D"},
    {"type": "input_boolean/create", "name": "Seq15 Select " + HEX},
    {"type": "input_boolean/create", "name": E.HELPER_NAME + " " + HEX},
    {"type": "config_entries/get", "domain": "synthetic"},
    {"type": S.CONFIG_ENTRY_GET, "entry_id": "synthetic-entry"},
    {"type": "config_entries/remove", "entry_id": "synthetic-entry"},
    {"type": "config_entries/disable", "entry_id": "synthetic-entry", "disabled_by": "user"},
    {"type": "config/label_registry/delete"}, {"type": "config/label_registry/delete", "label_id": ""},
    {"type": "config/entity_registry/remove", "entity_id": "sensor.synthetic"},
    {"type": "config/area_registry/delete", "area_id": "hamcp179_study"},
)
BOARD = {"type": "lovelace/dashboards/create", "url_path": "seq15-" + HEX, "title": "Seq15 Board",
         "icon": "mdi:view-dashboard", "show_in_sidebar": True, "require_admin": False}
SCRIPT_RUN = {"type": "call_service", "domain": "script", "service": "seq15_" + HEX, "service_data": {}}
SELECT = {"type": "input_select/create", "name": "Seq15 Select " + HEX, "options": ["Home", "Away", "Night"]}
SEQ15_EXAMPLES = (
    {"type": Q.REGISTRY_LIST}, {"type": "config_entries/get"}, {"type": "input_select/list"},
    {"type": Q.DASHBOARD_LIST},
    SELECT, {**SELECT, "name": "Seq15 Scene Member " + HEX},
    {"type": "input_select/update", "input_select_id": "seq15_select_" + HEX, "name": "Seq15 Select " + HEX,
     "options": ["Away", "Home", "Night"]},
    SCRIPT_RUN,
    {"type": Q.TRACE_LIST, "domain": "script", "item_id": "seq15_" + HEX},
    BOARD,
    {"type": "lovelace/config/save", "url_path": "seq15-" + HEX, "config": Q.DASHBOARD_CONFIG},
    {"type": "lovelace/config/save", "url_path": "seq15-" + HEX,
     "config": {**Q.DASHBOARD_CONFIG, "title": "Seq15 Drift"}},
    {"type": Q.LOVELACE_CONFIG, "url_path": "seq15-" + HEX, "force": True},
    {"type": Q.DASHBOARD_UPDATE, "dashboard_id": "seq15_absent_" + HEX, "title": "No implicit create"},
    {"type": Q.DASHBOARD_UPDATE, "dashboard_id": "seq15_" + HEX, "url_path": "seq15-other"},
    {"type": Q.DASHBOARD_UPDATE, "dashboard_id": "seq15_" + HEX, "mode": "yaml"},
    {"type": Q.DASHBOARD_UPDATE, "dashboard_id": "seq15_" + HEX, "title": "Seq15 Drift"},
    {"type": "input_select/delete", "input_select_id": "seq15_select_" + HEX},
    {"type": "lovelace/dashboards/delete", "dashboard_id": "seq15_" + HEX},
)
SEQ15_NEAR = (
    {"type": "call_service", "domain": "homeassistant", "service": "restart", "service_data": {}},
    {**SCRIPT_RUN, "service": "turn_on"}, {**SCRIPT_RUN, "service": "seq15_" + HEX + "x"},
    {**SCRIPT_RUN, "service": "x_seq15_" + HEX}, {**SCRIPT_RUN, "service": "seq15_" + HEX + "\n"},
    # Each pattern is matched in full: nothing before it and nothing after it.
    *({"type": Q.TRACE_LIST, "domain": "script", "item_id": item}
      for item in ("seq15_" + HEX + "0", "x_seq15_" + HEX, "seq15_" + HEX + "\n")),
    *({**BOARD, "url_path": path} for path in ("seq15-" + HEX + "0", "x-seq15-" + HEX, "seq15-" + HEX + "/x")),
    *({"type": "lovelace/config/save", "url_path": path, "config": Q.DASHBOARD_CONFIG}
      for path in ("seq15-" + HEX + "0", "x-seq15-" + HEX, "seq15-" + HEX + "\n")),
    *({"type": Q.LOVELACE_CONFIG, "url_path": path, "force": True}
      for path in ("seq15-" + HEX + "0", "x-seq15-" + HEX, "seq15-" + HEX + "\n")),
    *({"type": Q.DASHBOARD_UPDATE, "dashboard_id": item, "title": "No implicit create"}
      for item in ("seq15_absent_" + HEX + "0", "x_seq15_absent_" + HEX, "seq15_absent_" + HEX + "\n")),
    *({**SELECT, "name": name} for name in ("Seq15 Select " + HEX + "0", "Synthetic Seq15 Select " + HEX,
                                             "Seq15 Scene Member " + HEX + "\n", "Seq15 Select 0A1B2C3D")),
    *({"type": "input_select/update", "input_select_id": "seq15_select_" + HEX, "name": name,
       "options": ["Away", "Home", "Night"]}
      for name in ("Seq15 Select " + HEX + "0", "Synthetic Seq15 Select " + HEX, "Seq15 Select " + HEX + "\n")),
    {**SCRIPT_RUN, "service": "seq15_0A1B2C3D"}, {**SCRIPT_RUN, "domain": "scene"},
    {**SCRIPT_RUN, "service_data": {"entity_id": "all"}}, {**SCRIPT_RUN, "service_data": None},
    {**SCRIPT_RUN, "target": {"entity_id": "light.synthetic"}},
    {key: value for key, value in SCRIPT_RUN.items() if key != "service_data"},
    {"type": Q.TRACE_LIST, "domain": "automation", "item_id": "seq15_" + HEX},
    {"type": Q.TRACE_LIST, "domain": "script"},
    {"type": Q.TRACE_GET, "domain": "script", "item_id": "seq15_" + HEX, "run_id": "synthetic"},
    {**BOARD, "require_admin": True}, {**BOARD, "show_in_sidebar": 1}, {**BOARD, "require_admin": 0},
    {**BOARD, "title": "Synthetic other"}, {**BOARD, "url_path": "seq15-other"}, {**BOARD, "mode": "yaml"},
    {key: value for key, value in BOARD.items() if key != "icon"},
    {"type": "lovelace/config/save", "url_path": "seq15-" + HEX, "config": {"title": "Synthetic", "views": []}},
    {"type": "lovelace/config/save", "url_path": "lovelace", "config": Q.DASHBOARD_CONFIG},
    {"type": "lovelace/config/save", "url_path": None, "config": Q.DASHBOARD_CONFIG},
    {"type": "lovelace/config/save", "config": Q.DASHBOARD_CONFIG},
    {"type": "lovelace/config/delete", "url_path": "seq15-" + HEX},
    {"type": Q.LOVELACE_CONFIG, "url_path": "seq15-" + HEX, "force": False},
    {"type": Q.LOVELACE_CONFIG, "url_path": "seq15-" + HEX, "force": 1},
    {"type": Q.LOVELACE_CONFIG, "url_path": "seq15-" + HEX},
    {"type": Q.LOVELACE_CONFIG, "force": True},
    {"type": Q.DASHBOARD_UPDATE, "dashboard_id": "seq15_" + HEX, "title": "Synthetic other"},
    {"type": Q.DASHBOARD_UPDATE, "dashboard_id": "seq15_" + HEX, "title": "No implicit create"},
    {"type": Q.DASHBOARD_UPDATE, "dashboard_id": "seq15_" + HEX, "url_path": "seq15-" + HEX},
    {"type": Q.DASHBOARD_UPDATE, "dashboard_id": "seq15_" + HEX, "mode": "storage"},
    {"type": Q.DASHBOARD_UPDATE, "dashboard_id": "seq15_" + HEX, "require_admin": True},
    {"type": Q.DASHBOARD_UPDATE, "dashboard_id": "seq15_" + HEX, "title": "Seq15 Drift", "mode": "yaml"},
    {"type": Q.DASHBOARD_UPDATE, "dashboard_id": "", "title": "Seq15 Drift"},
    {"type": Q.DASHBOARD_UPDATE, "title": "Seq15 Drift"},
    {**SELECT, "options": ["Away", "Home", "Night"]}, {**SELECT, "options": ("Home", "Away", "Night")},
    {**SELECT, "options": ["Home", "Away"]}, {**SELECT, "name": "Seq15 Other " + HEX},
    {**SELECT, "name": "Seq13 Lane " + HEX}, {**SELECT, "initial": "Home"},
    {"type": "input_select/update", "input_select_id": "seq15_select_" + HEX,
     "name": "Seq15 Scene Member " + HEX, "options": ["Away", "Home", "Night"]},
    {"type": "input_select/delete"}, {"type": "input_select/delete", "input_select_id": ""},
    {"type": "input_boolean/delete", "input_boolean_id": "seq13_lane_" + HEX},
    {"type": "config_entries/get", "domain": "synthetic"},
    {"type": "config_entries/remove", "entry_id": "synthetic-entry"},
    {"type": "lovelace/dashboards/delete"},
    {"type": "lovelace/resources/delete", "resource_id": "synthetic"},
)
SECURITY_EXAMPLES = (
    {"type": X.AUTH_LIST}, {"type": X.CURRENT_USER}, {"type": X.HTTP_CONFIG}, {"type": X.GET_CONFIG},
    {"type": X.PIPELINE_LIST}, {"type": X.AGENT_LIST}, {"type": X.GET_PANELS},
    {"type": "config/entity_registry/list"},
    *({"type": X.EXPOSE_NEW_GET, "assistant": assistant} for assistant in X.KNOWN_ASSISTANTS),
)
SECURITY_NEAR = (
    {"type": X.EXPOSE_NEW_GET}, {"type": X.EXPOSE_NEW_GET, "assistant": "synthetic_other"},
    {"type": X.EXPOSE_NEW_GET, "assistant": ""}, {"type": X.EXPOSE_NEW_GET, "assistant": None},
    {"type": X.EXPOSE_NEW_GET, "assistant": [X.KNOWN_ASSISTANTS[0]]},
    {"type": X.EXPOSE_NEW_GET, "assistant": X.KNOWN_ASSISTANTS[0], "expose_new": True},
    {"type": "homeassistant/expose_new_entities/set", "assistant": X.KNOWN_ASSISTANTS[0], "expose_new": True},
    {"type": X.AUTH_LIST, "user_id": "synthetic"}, {"type": X.CURRENT_USER, "assistant": X.KNOWN_ASSISTANTS[0]},
    {"type": "config/auth/delete", "user_id": "synthetic"}, {"type": "config/auth/create", "name": "synthetic"},
    {"type": "config/auth/update", "user_id": "synthetic", "is_active": False},
    {"type": "auth/long_lived_access_token", "client_name": "synthetic", "lifespan": 1},
    {"type": "auth/refresh_tokens"}, {"type": "config/entity_registry/list", "domain": "light"},
    {"type": "config/entity_registry/update", "entity_id": "light.synthetic", "disabled_by": "user"},
    {"type": "assist_pipeline/pipeline/delete", "pipeline_id": "synthetic"},
    {"type": "config/label_registry/list"}, {"type": "config_entries/get"},
)


TOPOLOGY_READ = {"type": thread.NETWORK_TOPOLOGY_COMMAND, "refresh": False}
THREAD_EXAMPLES = (
    {"type": thread.LIST_DATASETS_COMMAND},
    {"type": thread.ADD_DATASET_COMMAND, "source": "hamcp231-clean", "tlv": "0e080000000000010000"},
    {"type": thread.ADD_DATASET_COMMAND, "source": "hamcp231-" + HEX, "tlv": "0e08000000000001000035060004001fffe0"},
    TOPOLOGY_READ,
)
A2_EXAMPLES = (TOPOLOGY_READ, {"type": a2.DEVICE_REGISTRY_COMMAND})
THREAD_MATTER_NEAR = (
    # The topology read with a refresh, or with anything that is not the built-in False.
    {**TOPOLOGY_READ, "refresh": True}, {**TOPOLOGY_READ, "refresh": 0}, {**TOPOLOGY_READ, "refresh": None},
    {**TOPOLOGY_READ, "refresh": "false"}, {**TOPOLOGY_READ, "refresh": []},
    {**TOPOLOGY_READ, "node_id": 1}, {"type": thread.NETWORK_TOPOLOGY_COMMAND},
    # Other Thread and Matter commands, none of which either lane sends.
    {"type": "thread/delete_dataset", "dataset_id": "synthetic"},
    {"type": "thread/set_preferred_dataset", "dataset_id": "synthetic"},
    {"type": "thread/get_dataset_tlv", "dataset_id": "synthetic"},
    {"type": "thread/set_preferred_border_agent", "dataset_id": "synthetic", "border_agent_id": "synthetic",
     "extended_address": "synthetic"},
    {"type": "thread/discover_routers"}, {"type": "otbr/info"},
    {"type": "matter/commission", "code": "synthetic"}, {"type": "matter/commission_on_network", "pin": 1},
    {"type": "matter/set_thread", "thread_operation_dataset": "0e08"},
    {"type": "matter/set_wifi_credentials", "network_name": "synthetic", "password": "synthetic"},
    {"type": "matter/node_diagnostics", "device_id": "synthetic"},
    {"type": "matter/ping_node", "device_id": "synthetic"},
    {"type": "matter/remove_matter_fabric", "device_id": "synthetic", "fabric_index": 1},
    # Registry and entry writes next to the one registry read the A2 lane sends.
    {"type": "config/device_registry/update", "device_id": "synthetic", "name_by_user": "synthetic"},
    {"type": "config/device_registry/remove_config_entry", "device_id": "synthetic", "config_entry_id": "synthetic"},
    {"type": a2.DEVICE_REGISTRY_COMMAND, "refresh": False},
    {"type": "config/entity_registry/list"}, {"type": "config_entries/get"},
    {"type": "config_entries/disable", "entry_id": "synthetic", "disabled_by": "user"},
)
THREAD_NEAR = (
    *THREAD_MATTER_NEAR,
    # A dataset is stored under a source and a TLV that are both text, and not empty.
    {"type": thread.ADD_DATASET_COMMAND, "source": "", "tlv": "0e08"},
    {"type": thread.ADD_DATASET_COMMAND, "source": "hamcp231-clean", "tlv": ""},
    {"type": thread.ADD_DATASET_COMMAND, "source": None, "tlv": "0e08"},
    {"type": thread.ADD_DATASET_COMMAND, "source": "hamcp231-clean", "tlv": b"0e08"},
    {"type": thread.ADD_DATASET_COMMAND, "source": "hamcp231-clean", "tlv": ["0e08"]},
    {"type": thread.ADD_DATASET_COMMAND, "source": "hamcp231-clean", "tlv": "0e08", "preferred": True},
    {"type": thread.ADD_DATASET_COMMAND, "source": "hamcp231-clean"},
    {"type": thread.ADD_DATASET_COMMAND, "tlv": "0e08"},
    {"type": thread.LIST_DATASETS_COMMAND, "source": "hamcp231-clean"},
    # The A2 lane's registry read is not this lane's.
    A2_EXAMPLES[1],
)
# The A1 lane's dataset commands are not the A2 lane's.
A2_NEAR = (*THREAD_MATTER_NEAR, THREAD_EXAMPLES[0], THREAD_EXAMPLES[1], THREAD_EXAMPLES[2])

HUB = {"type": E.UPDATE, "entity_id": E.HUB_SWITCH}
CATEGORY = {"type": "config/category_registry/create", "scope": E.CATEGORY_SCOPE, "name": "HAMCP179 Lighting"}
OWNED_BOARD = {"type": Q.DASHBOARD_UPDATE, "dashboard_id": "seq15_" + HEX}
# Each field of a listed command that names its value or values outright (not
# any string, and not a pattern): the lane, a command it sends, those fields.
NAMED = (
    ("entity_metadata", {**HUB, "area_id": "hamcp179_study"}, ("entity_id",)),
    ("entity_metadata", {**HUB, "name": None}, ("entity_id", "name")),
    ("entity_metadata", {**HUB, "area_id": None, "name": None}, ("entity_id",)),
    ("entity_metadata", {**HUB, "area_id": "synthetic_area", "name": "HAMCP179 Hub"}, ("entity_id",)),
    ("entity_metadata", {"type": "config/area_registry/create", "name": "HAMCP179 Study"}, ("name",)),
    ("entity_metadata", {"type": "config/label_registry/create", "name": "HAMCP179 Night"}, ("name",)),
    ("entity_metadata", {"type": "config/label_registry/create", "name": "HAMCP179 Outdoor"}, ("name",)),
    ("entity_metadata", CATEGORY, ("scope", "name")),
    ("entity_metadata", {"type": "config/category_registry/list", "scope": E.CATEGORY_SCOPE}, ("scope",)),
    ("entity_metadata", {"type": "config/category_registry/delete", "scope": E.CATEGORY_SCOPE,
                         "category_id": "01SYNTHETICCATEGORY"}, ("scope",)),
    ("seq13", {"type": S.LABEL_UPDATE, "label_id": S.CANARY_ID, "description": "Seq-13 raw drift"},
     ("label_id", "description")),
    ("seq13", {"type": S.ENTITY_UPDATE, "entity_id": "sensor.synthetic", "name": "Seq13 Raw Drift"}, ("name",)),
    ("seq13", {"type": S.ENTITY_UPDATE, "entity_id": "sensor.synthetic", "disabled_by": "user"}, ("disabled_by",)),
    ("seq15", SELECT, ("options",)),
    ("seq15", {**SELECT, "name": "Seq15 Scene Member " + HEX}, ("options",)),
    ("seq15", {"type": "input_select/update", "input_select_id": "seq15_select_" + HEX,
               "name": "Seq15 Select " + HEX, "options": ["Away", "Home", "Night"]}, ("options",)),
    ("seq15", SCRIPT_RUN, ("domain", "service_data")),
    ("seq15", {"type": Q.TRACE_LIST, "domain": "script", "item_id": "seq15_" + HEX}, ("domain",)),
    ("seq15", BOARD, ("title", "icon", "show_in_sidebar", "require_admin")),
    ("seq15", {"type": "lovelace/config/save", "url_path": "seq15-" + HEX, "config": Q.DASHBOARD_CONFIG},
     ("config",)),
    ("seq15", {"type": "lovelace/config/save", "url_path": "seq15-" + HEX,
               "config": {**Q.DASHBOARD_CONFIG, "title": "Seq15 Drift"}}, ("config",)),
    ("seq15", {"type": Q.LOVELACE_CONFIG, "url_path": "seq15-" + HEX, "force": True}, ("force",)),
    ("seq15", {"type": Q.DASHBOARD_UPDATE, "dashboard_id": "seq15_absent_" + HEX, "title": "No implicit create"},
     ("title",)),
    ("seq15", {**OWNED_BOARD, "url_path": "seq15-other"}, ("url_path",)),
    ("seq15", {**OWNED_BOARD, "mode": "yaml"}, ("mode",)),
    ("seq15", {**OWNED_BOARD, "title": "Seq15 Drift"}, ("title",)),
    *(("security_metadata", {"type": X.EXPOSE_NEW_GET, "assistant": assistant}, ("assistant",))
      for assistant in X.KNOWN_ASSISTANTS),
    ("thread_matter", TOPOLOGY_READ, ("refresh",)),
    ("thread_matter_a2", TOPOLOGY_READ, ("refresh",)),
)


def names_its_value(want):
    """Whether a listed field names its value or values outright: no free string and no pattern within."""
    if want is str or type(want) is re.Pattern:
        return False
    if type(want) is entity.Each:
        return names_its_value(want.item)
    if type(want) in (tuple, list, dict):
        return all(map(names_its_value, want.values() if type(want) is dict else want))
    return True


def others(value):
    """Other values of the same built-in type as the one a command names."""
    if type(value) is str:
        yield from (value + "x", "x" + value, value[:-1], value + " ", "", "synthetic-other-364")
        if value.upper() != value:
            yield value.upper()
    elif type(value) is bool:
        yield not value
    elif value is None:
        # None is the one value of its type, so here it is any other value at all.
        yield from ("", "synthetic-other-364", False, 0, [], {})
    elif type(value) is list:
        yield from ([], value + value[:1], *([value[:-1], value[::-1]] if len(value) > 1 else []))
        for index, item in enumerate(value):
            yield from (value[:index] + [other] + value[index + 1:] for other in others(item))
    elif type(value) is dict:
        yield {**value, "synthetic": True}
        for key, item in value.items():
            yield {name: kept for name, kept in value.items() if name != key}
            yield from ({**value, key: other} for other in others(item))
    else:
        raise AssertionError(type(value))


def lane(name, module, client, send, pin, examples, near, sources, methods, seconds, signatures,
         settings=configured, earlier_check=False, commands=None, dial=None, named=True):
    """One lane's client. ``earlier_check`` marks a client whose own check of its settings runs before the pin.

    ``commands`` is the lane's closed list where its module holds more than one.
    ``dial`` is what the client passes to the connection besides its target.
    ``named`` is False for the one client whose target no constant names: the A2
    lane's, which reads the Core the harness accepted each time it is used.
    """
    constant = vars(module).get("RAW_CORE_URL", entity.RAW_CORE_URL) if named else None
    return SimpleNamespace(name=name, module=module, client=client, send=send, pin=pin, examples=examples,
                           near=near, sources=sources, methods=methods, seconds=seconds, signatures=signatures,
                           settings=settings, earlier_check=earlier_check, dial=dial or {}, constant=constant,
                           commands=module.RAW_COMMANDS if commands is None else commands)


def call(client, payload):
    return client.call(payload)


def command(client, payload):
    return client.command(payload)


def security_signatures():
    return {(kind, frozenset({"type", *fields})) for kind, fields in security.RAW_COMMANDS.items()}


SOCKET_METHODS = frozenset({"ok", "call", "refused"})
# Every name a lane's client class goes by in its script.
BUILDERS = frozenset({"RawWebSocket", "LaneRaw", "RawCore", "SceneLaneRaw", "HelperLaneRaw", "A2RawCore"})
THREAD_DIAL = {"max_msg_size": thread.HARNESS_FRAME_BYTES}
LANES = (
    lane("entity_metadata", entity, entity.RawWebSocket, call, MAIN_SOCKET, ENTITY_EXAMPLES, ENTITY_NEAR,
         (("entity_metadata_contract_acceptance.py", entity),), SOCKET_METHODS, 30,
         lambda: shape_signatures(entity.RAW_COMMANDS)),
    lane("configuration_delete", config_delete, config_delete.LaneRaw, call, MAIN_SOCKET, CONFIG_DELETE_EXAMPLES,
         CONFIG_DELETE_NEAR, (("configuration_delete_contract_acceptance.py", config_delete),), SOCKET_METHODS, 30,
         lambda: shape_signatures(config_delete.RAW_COMMANDS)),
    lane("seq13", seq13, seq13.LaneRaw, call, MAIN_SOCKET, SEQ13_EXAMPLES, SEQ13_NEAR,
         (("seq13_contract_acceptance.py", seq13),), SOCKET_METHODS, 30,
         lambda: shape_signatures(seq13.RAW_COMMANDS)),
    lane("seq15", seq15, seq15.LaneRaw, call, MAIN_SOCKET, SEQ15_EXAMPLES, SEQ15_NEAR,
         (("seq15_contract_acceptance.py", seq15), ("seq15_scene_contract.py", seq15)), SOCKET_METHODS, 30,
         lambda: shape_signatures(seq15.RAW_COMMANDS)),
    lane("created_scene", created, created.SceneLaneRaw, call, LOCAL_SOCKET, CREATED_SCENE_EXAMPLES,
         CREATED_SCENE_NEAR, (("created_object_contract_acceptance.py", created),), SOCKET_METHODS, 30,
         lambda: shape_signatures(created.SCENE_RAW_COMMANDS), settings=configured_local, earlier_check=True,
         commands=created.SCENE_RAW_COMMANDS),
    lane("created_helper", created, created.HelperLaneRaw, call, LOCAL_SOCKET, CREATED_HELPER_EXAMPLES,
         CREATED_NEAR, (("created_object_contract_acceptance.py", created),), SOCKET_METHODS, 30,
         lambda: shape_signatures(created.HELPER_RAW_COMMANDS), settings=configured_local, earlier_check=True,
         commands=created.HELPER_RAW_COMMANDS),
    lane("security_metadata", security, security.RawCore, command, LOCAL_SOCKET, SECURITY_EXAMPLES, SECURITY_NEAR,
         (("security_metadata_contract_acceptance.py", security),), frozenset({"result", "command"}), 60,
         security_signatures),
    lane("thread_matter", thread, thread.RawCore, command, THREAD_SOCKET, THREAD_EXAMPLES, THREAD_NEAR,
         (("thread_matter_topology_contract_acceptance.py", thread),), frozenset({"command"}), 60,
         lambda: shape_signatures(thread.RAW_COMMANDS), dial=THREAD_DIAL),
    lane("thread_matter_a2", a2, a2.A2RawCore, command, BRIDGE_SOCKET, A2_EXAMPLES, A2_NEAR,
         (("thread_matter_topology_a2_acceptance.py", a2), ("core_thread_matter_topology_a2_local.py", a2_runner)),
         frozenset({"command"}), 60, lambda: shape_signatures(a2.A2_RAW_COMMANDS), commands=a2.A2_RAW_COMMANDS,
         dial=THREAD_DIAL, named=False),
)
BY_NAME = {item.name: item for item in LANES}
SHAPED = tuple(item for item in LANES if item.module is not security)
# The two Thread/Matter lanes: a Core, a client class and a closed list each.
THREAD_LANES = tuple(item for item in LANES if item.module in (thread, a2))
# The two created-object lanes: one script and one Core, a client class and a closed list each.
CREATED_LANES = tuple(item for item in LANES if item.module is created)
# The lanes the Core harness runs, on its Core; the others each start a Core of their own.
HARNESS_LANES = tuple(item for item in SHAPED if item.pin == MAIN_SOCKET)
# Texts a refusal must not carry, whichever check made it.
PRIVATE = ("127.0.0.1", "ws://", "http://", TOKEN, ELSEWHERE, ELSEWHERE_NAME, "18123", "8123", "18124",
           "172.30.")


def host_of(pin):
    return pin.split("//")[1].split(":")[0]


class ClientCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # The A2 lane's client has a Core only once its runner has handed one over, so every
        # test here starts after that hand-over, made through the harness's own function and
        # undone when the test ends.
        manager = patch.multiple(harness, HA_URL=harness.HA_URL, CLIENT_ID=harness.CLIENT_ID, _lane_core=None)
        manager.start()
        self.addCleanup(manager.stop)
        self.assertEqual(harness.use_internal_lane_core(BRIDGE_CORE, BRIDGE_SUBNET), BRIDGE_CORE)

    def assert_dialled(self, item, target):
        """What was dialled is the lane's pin, and the very constant where one names it."""
        self.assertIs(type(target), str)
        self.assertEqual(target, item.pin)
        if item.constant is not None:
            self.assertIs(target, item.constant)

    def assert_fixed_code(self, exception, reason):
        # A fixed code and nothing else: no URL, token or payload text.
        self.assertIs(type(exception), ValueError)
        self.assertEqual(exception.args, (reason,))
        self.assertIsNone(exception.__cause__)
        self.assertIsNone(exception.__context__)
        self.assertEqual(exception.__dict__, {})

    def refused_url(self, item, url, *, pinned=None):
        """The client is not built for ``url``. ``pinned`` says whether the refusal must be the pin's own.

        A client with an earlier check of its own may refuse there first, in that
        check's words; either way it is a ValueError before anything is opened.
        """
        events = []
        with patch.object(aiohttp, "ClientSession", new=fake_session(events)):
            with self.assertRaises(ValueError) as caught:
                item.client(url if pinned == "settings" else item.settings(url))
        if pinned or not item.earlier_check:
            self.assert_fixed_code(caught.exception, URL_REFUSED)
        else:
            self.assertIs(type(caught.exception), ValueError)
            self.assertEqual([type(part) for part in caught.exception.args], [str])
            for text in PRIVATE:
                self.assertNotIn(text, caught.exception.args[0])
        self.assertEqual(events, [])

    async def refused_command(self, item, payload, *, prepare=None):
        events = []
        with patch.object(aiohttp, "ClientSession", new=fake_session(events)):
            client = item.client(item.settings(item.pin))
            if prepare is not None:
                prepare(client)
            with self.assertRaises(ValueError) as caught:
                await item.send(client, payload)
        self.assert_fixed_code(caught.exception, COMMAND_REFUSED)
        # Refused before any session exists, so nothing was connected or sent.
        self.assertEqual(events, [])

    async def sent(self, item, payload, *, client=None, after=None):
        events = []
        with patch.object(aiohttp, "ClientSession", new=fake_session(events)):
            client = item.client(item.settings(item.pin)) if client is None else client
            if after is not None:
                after()
            reply = await item.send(client, payload)
        return reply, events


class PinnedUrl(ClientCase):
    """Each client is built only for its lane's one Core socket."""

    def cases(self, pin):
        port, host = pin.split(":")[2].split("/")[0], host_of(pin)
        other = {"8123": "18123", "18123": "8123", "18124": "18123"}[port]
        origin = "ws://" + host + ":" + port
        self.assertEqual(pin, origin + "/api/websocket")
        return (
            # Another port, including the other lanes' own.
            pin.replace(port, other), pin.replace(port, str(int(port) + 1)), pin.replace(":" + port, ""),
            pin.replace(port, "0" + port), pin.replace(port, port + "0"),
            # Another scheme.
            pin.replace("ws://", "wss://"), pin.replace("ws://", "http://"), pin.replace("ws://", "WS://"),
            pin.replace("ws://", "ws:/"), pin.replace("ws://", ""),
            # Anything after, or instead of, the one path.
            pin + "/", pin + "/..", pin + "?next=1", pin + "#x", pin + "/" + ELSEWHERE,
            origin + "/core/websocket", origin + "//api/websocket", origin + "/api", origin,
            # Userinfo, so that the pinned text is not the host.
            origin + "@" + ELSEWHERE + "/api/websocket", origin + "@" + ELSEWHERE_NAME + "/api/websocket",
            pin + "@" + ELSEWHERE_NAME, "ws://user:synthetic@" + host + ":" + port + "/api/websocket",
            "ws://@" + host + ":" + port + "/api/websocket",
            # Another host, including names and forms that only resolve to loopback.
            pin.replace(host, "localhost"), pin.replace(host, "[::1]"),
            pin.replace(host, "127.0.0.2"), pin.replace(host, "127.1"),
            pin.replace(host, "2130706433"), pin.replace(host, "0.0.0.0"),
            pin.replace(host, host + "." + ELSEWHERE_NAME), pin.replace(host, ELSEWHERE),
            pin.replace(host, ELSEWHERE_NAME), "ws://supervisor/core/websocket",
            # Loopback, the bridge Core handed over in these tests, its neighbour on that
            # bridge, and a household address: each is another lane's Core or no lane's.
            *(pin.replace(host, another) for another in ("127.0.0.1", "172.30.0.2", "172.30.0.3", "172.30.0.02",
                                                         "172.30.0.20", "192.168.1.10", "10.0.0.2")
              if another != host),
            # Not exactly the text, or not text at all.
            " " + pin, pin + " ", pin + "\n", pin + "\x00", pin.upper(), "", None, 0, True,
            pin.encode(), (pin,), [pin], {"url": pin},
            # A str subclass, whether it merely equals the pin or only claims to.
            Text(pin), Claims(pin), Claims(pin.replace(host, ELSEWHERE)))

    def test_any_url_but_the_lane_pin_is_refused_when_the_client_is_built(self):
        for item in LANES:
            for url in self.cases(item.pin):
                with self.subTest(lane=item.name, url=url, kind=type(url).__name__):
                    self.refused_url(item, url)

    def test_the_pinned_url_builds_a_client_and_opens_nothing(self):
        for item in LANES:
            with self.subTest(lane=item.name):
                events = []
                with patch.object(aiohttp, "ClientSession", new=fake_session(events)):
                    client = item.client(item.settings(item.pin))
                self.assertEqual(events, [])
                # The checked value is not kept for a later dial.
                self.assertNotIn(item.pin, [value for value in vars(client).values() if type(value) is str])

    def test_refusal_carries_neither_the_url_nor_the_token(self):
        private = "ws://127.0.0.1:8123@" + ELSEWHERE_NAME + "/api/websocket?k=synthetic-query"
        for item in LANES:
            with self.subTest(lane=item.name):
                with patch.object(aiohttp, "ClientSession", new=fake_session([])):
                    with self.assertRaises(ValueError) as caught:
                        item.client(item.settings(private))
                shown = str(caught.exception) + repr(caught.exception)
                if not item.earlier_check:
                    self.assertEqual(str(caught.exception), URL_REFUSED)
                for text in (private, TOKEN, ELSEWHERE_NAME, "synthetic-query", "127.0.0.1", "ws://", "172.30."):
                    self.assertNotIn(text, shown)

    def test_each_pin_is_the_socket_of_its_lanes_own_disposable_core(self):
        self.assertEqual(entity.RAW_CORE_URL, MAIN_SOCKET)
        self.assertEqual(security.RAW_CORE_URL, LOCAL_SOCKET)
        self.assertEqual(created.RAW_CORE_URL, LOCAL_SOCKET)
        self.assertEqual(thread.RAW_CORE_URL, THREAD_SOCKET)
        # The A2 lane's Core has no address until its runner starts it, so its script names
        # none: the class reads the one the harness accepted.
        self.assertNotIn("RAW_CORE_URL", vars(a2))
        self.assertEqual(sorted(name for name, value in vars(a2).items()
                                if type(value) is str and value.startswith(("ws://", "http://"))), [])
        self.assertEqual({item.pin for item in LANES}, {MAIN_SOCKET, LOCAL_SOCKET, THREAD_SOCKET, BRIDGE_SOCKET})
        for item in LANES:
            with self.subTest(lane=item.name):
                self.assertNotIn("RAW_CORE_URL", vars(item.client))
                if item.module is security:
                    continue
                if item in THREAD_LANES:
                    # A client of their own, not the shared one: the target is still the class's,
                    # never an instance's, and each class names its own.
                    self.assertFalse(issubclass(item.client, entity.RawWebSocket))
                    self.assertIn("core_url", vars(item.client))
                    self.assertEqual(item.client.core_url, item.pin)
                    client = item.client(item.settings(item.pin))
                    self.assertNotIn("core_url", vars(client))
                    self.assertEqual(sorted(vars(client)), ["token"])
                    continue
                self.assertTrue(issubclass(item.client, entity.RawWebSocket))
                # The target is the class's, never an instance's: the harness lanes share the
                # base's, and the one lane with a Core of its own names that Core on its class.
                self.assertIs(item.client.core_url, item.module.RAW_CORE_URL if item.module is created
                              else entity.RAW_CORE_URL)
                self.assertEqual("core_url" in vars(item.client), item.module is entity)
                if item.module is created:
                    # Both created-object lanes take the target from the one class that names their Core.
                    self.assertEqual(item.client.__bases__, (created.LaneRaw,))
                self.assertNotIn("core_url", vars(item.client(item.settings(item.pin))))
        self.assertIs(vars(entity.RawWebSocket)["core_url"], entity.RAW_CORE_URL)
        self.assertIs(vars(created.LaneRaw)["core_url"], created.RAW_CORE_URL)
        self.assertIs(vars(thread.RawCore)["core_url"], thread.RAW_CORE_URL)
        self.assertIs(type(vars(a2.A2RawCore)["core_url"]), a2.AcceptedCore)
        self.assertEqual(a2.A2RawCore.__bases__, (thread.RawCore,))

    def test_the_a2_client_has_no_core_until_the_harness_has_accepted_one(self):
        item = BY_NAME["thread_matter_a2"]
        events = []
        with patch.object(aiohttp, "ClientSession", new=fake_session(events)):
            built = item.client(item.settings(item.pin))
            # No origin accepted, or a value there that is not an http origin: no socket to pin.
            for accepted in (None, "", "https://172.30.0.2:8123", "ws://172.30.0.2:8123", "172.30.0.2:8123",
                             BRIDGE_CORE.encode(), Text(BRIDGE_CORE), (BRIDGE_CORE,), 8123, True):
                with self.subTest(accepted=accepted), patch.object(harness, "_lane_core", accepted):
                    for url in (item.pin, MAIN_SOCKET, LOCAL_SOCKET, THREAD_SOCKET, None):
                        with self.assertRaises(ValueError) as caught:
                            item.client(item.settings(url))
                        self.assert_fixed_code(caught.exception, URL_REFUSED)
                    with self.assertRaises(ValueError) as caught:
                        a2.A2RawCore.core_url
                    self.assert_fixed_code(caught.exception, URL_REFUSED)
        self.assertEqual(events, [])
        self.assertIs(type(built), a2.A2RawCore)

    async def test_an_a2_client_whose_core_is_no_longer_accepted_refuses_before_a_session(self):
        item = BY_NAME["thread_matter_a2"]
        for accepted in (None, "https://172.30.0.2:8123", 8123):
            for payload in item.examples:
                events = []
                with self.subTest(accepted=accepted, payload=payload), \
                        patch.object(aiohttp, "ClientSession", new=fake_session(events)):
                    client = item.client(item.settings(item.pin))
                    with patch.object(harness, "_lane_core", accepted), self.assertRaises(ValueError) as caught:
                        await item.send(client, dict(payload))
                    self.assert_fixed_code(caught.exception, URL_REFUSED)
                    self.assertEqual(events, [])

    def test_the_a2_pin_is_the_accepted_origin_and_nothing_else_supplies_it(self):
        item = BY_NAME["thread_matter_a2"]
        self.assertEqual(harness.accepted_lane_core(), BRIDGE_CORE)
        self.assertEqual(a2.A2RawCore.core_url, "ws://" + BRIDGE_CORE[len("http://"):] + "/api/websocket")
        # Another run's network: the pin is that run's Core, and this one's is refused.
        with patch.multiple(harness, HA_URL=MAIN_CORE, CLIENT_ID=MAIN_CORE + "/", _lane_core=None):
            harness.use_internal_lane_core("http://10.89.4.7:8123", "10.89.4.0/24")
            self.assertEqual(a2.A2RawCore.core_url, "ws://10.89.4.7:8123/api/websocket")
            item.client(item.settings("ws://10.89.4.7:8123/api/websocket"))
            self.refused_url(item, item.pin)
        # Neither the settings, the class nor an instance moves it.
        client = item.client(item.settings(item.pin))
        client.core_url = MAIN_SOCKET
        self.assertEqual(type(client).core_url, item.pin)
        tree = ast.parse((SCRIPTS / "thread_matter_topology_a2_acceptance.py").read_text(encoding="utf-8"))
        descriptor, = (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AcceptedCore")
        self.assertEqual([node.name for node in descriptor.body if isinstance(node, ast.FunctionDef)], ["__get__"])
        self.assertEqual([ast.unparse(statement) for statement in descriptor.body[-1].body], [
            "import real_ha_contract_tests as harness",
            "accepted = harness.accepted_lane_core()",
            "if type(accepted) is not str or not accepted.startswith('http://'):\n    raise ValueError(RAW_URL_REFUSED)",
            "return 'ws://' + accepted[len('http://'):] + '/api/websocket'"])
        # The client reads the target from its class and never from itself or its settings.
        source = (SCRIPTS / "thread_matter_topology_contract_acceptance.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("type(self).core_url"), 2)
        self.assertEqual(source.count("self.core_url"), 0)
        self.assertEqual(source.count("configured.websocket_url"), 1)
        self.assertEqual(source.count("session.ws_connect(target, max_msg_size=HARNESS_FRAME_BYTES)"), 1)

    def test_a_loopback_core_that_is_not_the_created_object_pin_is_refused_by_the_pin(self):
        # Each pair is one loopback Core for REST and WebSocket alike, which is all the lane's
        # earlier check of its own asks for. Only the published port of its own Core is the pin.
        for item in CREATED_LANES:
            with self.subTest(lane=item.name):
                self.a_loopback_core_that_is_not_the_pin_is_refused_by_the_pin(item)

    def a_loopback_core_that_is_not_the_pin_is_refused_by_the_pin(self, item):
        for host in ("localhost:18123", "[::1]:18123", "127.0.0.1:8123", "localhost:8123", "[::1]:8123",
                     "127.0.0.1", "localhost", "[::1]", "127.0.0.1:18124", "127.0.0.1:1812", "127.0.0.1:181230",
                     "127.0.0.1:018123", "127.0.0.1:80", "127.0.0.1:"):
            with self.subTest(host=host):
                settings = configured("ws://" + host + "/api/websocket", "http://" + host + "/api")
                with patch("ha_mcp_engineering.clients.HomeAssistantRestClient") as rest:
                    self.refused_url(item, settings, pinned="settings")
                # Refused before the lane's REST client is built for that Core.
                rest.assert_not_called()
        # And the pin is the built-in str itself, where the earlier check takes an equal or agreeable subclass.
        for url in (Text(item.pin), Claims(item.pin), Claims(item.pin.replace("127.0.0.1", ELSEWHERE))):
            with self.subTest(kind=type(url).__name__), \
                    patch("ha_mcp_engineering.clients.HomeAssistantRestClient") as rest:
                self.refused_url(item, url, pinned=True)
                rest.assert_not_called()
        # The one pair that is accepted builds the lane's REST client for the same Core.
        client = item.client(item.settings(item.pin))
        self.assertEqual(client.client.settings.api_url, LOCAL_CORE + "/api")


class ClosedList(ClientCase):
    """Only the command shapes a lane sends pass; the rest is refused before a session."""

    def generic(self, example):
        kind = example["type"]
        return (
            {"type": "auth", "access_token": TOKEN}, {"type": "get_states"}, {"type": "subscribe_events"},
            {"type": "call_service", "domain": "homeassistant", "service": "restart"},
            {"type": "supervisor/api", "endpoint": "/host/reboot", "method": "post"},
            {"type": "config/core/update", "location_name": "synthetic"},
            # A listed type with one more field, or under a spelling that is not it.
            {**example, "id": 7}, {**example, "extra": 1}, {**example, 1: 2}, {**example, None: None},
            {**example, "type": kind.upper()}, {**example, "type": kind + " "}, {**example, "type": kind + "/"},
            {**example, "type": kind + "\n"}, {**example, "type": kind.encode()},
            # Not a command at all.
            {}, {"type": None}, {"type": [kind]}, {"id": 1}, None, kind, [("type", kind)], tuple(example.items()))

    async def test_a_command_outside_the_lane_list_is_refused_before_a_session(self):
        for item in LANES:
            for payload in (*self.generic(item.examples[0]), *item.near):
                with self.subTest(lane=item.name, payload=payload):
                    await self.refused_command(item, payload)

    async def test_a_listed_command_missing_a_field_is_refused_before_a_session(self):
        for item in LANES:
            sent = {(example["type"], frozenset(example)) for example in item.examples}
            for example in item.examples:
                for dropped in example:
                    payload = {key: value for key, value in example.items() if key != dropped}
                    # What is left may itself be a shape the lane sends, such as the hub's area alone.
                    if (payload.get("type"), frozenset(payload)) in sent:
                        continue
                    with self.subTest(lane=item.name, payload=payload):
                        await self.refused_command(item, payload)

    async def test_a_dict_subclass_payload_is_refused_before_a_session(self):
        # A payload is the built-in dict itself. A subclass can show one set of
        # keys while {"id": 1, **payload} copies every item it really holds.
        for item in LANES:
            example = item.examples[0]
            hidden = Hides({**example, "extra": 1})
            hidden.shown = tuple(example)
            self.assertEqual(hidden.keys(), example.keys())
            self.assertEqual({"id": 1, **hidden}, {"id": 1, **example, "extra": 1})
            for payload in (Mapping(example), hidden, *(Mapping(other) for other in item.examples[1:3])):
                with self.subTest(lane=item.name, kind=type(payload).__name__, payload=payload):
                    await self.refused_command(item, payload)

    async def test_a_str_subclass_in_a_command_is_refused_before_a_session(self):
        # A type, key or field is the built-in str itself. A subclass can answer
        # == as it likes while JSON writes its real text.
        for item in LANES:
            cases = []
            for example in item.examples:
                cases.append({**example, "type": Text(example["type"])})
                cases.append({Text(key): value for key, value in example.items()})
                for key, value in example.items():
                    if key != "type" and type(value) is str:
                        cases.append({**example, key: Text(value)})
                        cases.append({**example, key: Claims("synthetic-elsewhere")})
            cases.append({"type": Claims("config/auth/delete")})
            cases.append({"type": Claims("call_service"), "domain": "homeassistant", "service": "restart"})
            self.assertGreater(len(cases), 2 * len(item.examples))
            for payload in cases:
                with self.subTest(lane=item.name, payload=payload):
                    await self.refused_command(item, payload)

    async def test_a_nested_value_is_the_exact_built_in_type(self):
        # seq-15 is the lane that sends nested values: booleans, a list and a mapping.
        item = next(found for found in LANES if found.name == "seq15")
        cases = (
            {**BOARD, "show_in_sidebar": 1, "require_admin": 0}, {**BOARD, "show_in_sidebar": "true"},
            {**SCRIPT_RUN, "service_data": Mapping()}, {**SCRIPT_RUN, "service_data": []},
            {**SELECT, "options": [Text("Home"), "Away", "Night"]},
            {**SELECT, "options": ["Home", "Away", "Night", "Synthetic"]},
            {"type": "lovelace/config/save", "url_path": "seq15-" + HEX, "config": Mapping(Q.DASHBOARD_CONFIG)},
            {"type": "lovelace/config/save", "url_path": "seq15-" + HEX,
             "config": {**Q.DASHBOARD_CONFIG, "views": []}},
            {"type": "lovelace/config/save", "url_path": "seq15-" + HEX,
             "config": {**Q.DASHBOARD_CONFIG, "strategy": {"type": "synthetic"}}},
            {"type": Q.LOVELACE_CONFIG, "url_path": Text("seq15-" + HEX), "force": True})
        for payload in cases:
            with self.subTest(payload=payload):
                await self.refused_command(item, payload)

    async def test_a_client_instance_cannot_widen_its_list(self):
        unlisted = {"type": "get_states"}
        for item in SHAPED:
            with self.subTest(lane=item.name):
                self.assertIs(item.client.commands, item.commands)
                await self.refused_command(
                    item, unlisted, prepare=lambda client: setattr(client, "commands", (unlisted,)))

    async def test_one_lane_does_not_pass_another_lanes_commands(self):
        # The shared client's own list is entity_metadata's; each subclass names
        # its own, so no lane inherits a command it never sends.
        registry_list, hub = {"type": "config/entity_registry/list"}, ENTITY_EXAMPLES[2]
        by_name = {item.name: item for item in LANES}
        await self.refused_command(by_name["entity_metadata"], registry_list)
        for name in ("configuration_delete", "seq13", "seq15", "created_scene", "created_helper",
                     "security_metadata"):
            with self.subTest(lane=name):
                reply, _events = await self.sent(by_name[name], registry_list)
                self.assertIs(reply["success"], True)
                await self.refused_command(by_name[name], hub)
        await self.refused_command(by_name["seq13"], SELECT)
        await self.refused_command(by_name["seq15"], SEQ13_EXAMPLES[6])
        await self.refused_command(by_name["security_metadata"], SEQ13_EXAMPLES[5])
        # seq-13 deletes its own helper by any id; the created-object lane only one of its own.
        self.assertEqual(SEQ13_EXAMPLES[10]["type"], CREATED_HELPER_EXAMPLES[3]["type"])
        for item in CREATED_LANES:
            await self.refused_command(item, SEQ13_EXAMPLES[10])
            await self.refused_command(item, SEQ13_EXAMPLES[6])
        await self.refused_command(by_name["configuration_delete"], CREATED_HELPER_EXAMPLES[3])
        await self.refused_command(by_name["seq15"], CREATED_HELPER_EXAMPLES[6])
        # The Thread/Matter lanes send no registry read of entities and no entity update, and
        # no other lane sends a Thread or Matter command.
        for item in THREAD_LANES:
            with self.subTest(lane=item.name):
                await self.refused_command(item, registry_list)
                await self.refused_command(item, hub)
        for item in LANES:
            if item in THREAD_LANES:
                continue
            for payload in THREAD_EXAMPLES:
                with self.subTest(lane=item.name, payload=payload):
                    await self.refused_command(item, copy.deepcopy(payload))

    def test_each_thread_matter_lane_list_is_exactly_this(self):
        # Written out, so a command added to either list fails here.
        topology = {"type": "matter/network_topology", "refresh": False}
        self.assertEqual(thread.RAW_COMMANDS, (
            {"type": "thread/list_datasets"}, {"type": "thread/add_dataset_tlv", "source": str, "tlv": str},
            topology))
        self.assertEqual(a2.A2_RAW_COMMANDS, (topology, {"type": "config/device_registry/list"}))
        for commands in (thread.RAW_COMMANDS, a2.A2_RAW_COMMANDS):
            self.assertIs(type(commands), tuple)
            self.assertEqual([type(shape) for shape in commands], [dict] * len(commands))
            # ``refresh`` is the built-in False itself, which 0 does not pass for.
            self.assertEqual([shape["refresh"] for shape in commands if "refresh" in shape], [False])
            self.assertIs(next(shape["refresh"] for shape in commands if "refresh" in shape), False)
        # A list per lane: each class holds its own, and the A2 script has no list by the A1 name.
        self.assertIsNot(thread.RAW_COMMANDS, a2.A2_RAW_COMMANDS)
        self.assertNotIn("RAW_COMMANDS", vars(a2))
        self.assertIs(vars(thread.RawCore)["commands"], thread.RAW_COMMANDS)
        self.assertIs(vars(a2.A2RawCore)["commands"], a2.A2_RAW_COMMANDS)
        self.assertEqual(set(vars(a2.A2RawCore)) - {"__module__", "__doc__", "__firstlineno__",
                                                    "__static_attributes__"}, {"core_url", "commands"})
        # The topology read is the one the product's own reader sends.
        self.assertEqual(dict(thread.PAYLOADS[thread.TOPOLOGY]), topology)
        self.assertEqual(dict(a2.PAYLOADS[a2.TOPOLOGY]), topology)

    async def test_each_thread_matter_lane_refuses_what_only_the_other_lane_sends(self):
        first, second = BY_NAME["thread_matter"], BY_NAME["thread_matter_a2"]
        only_first = [shape for shape in thread.RAW_COMMANDS if shape not in a2.A2_RAW_COMMANDS]
        only_second = [shape for shape in a2.A2_RAW_COMMANDS if shape not in thread.RAW_COMMANDS]
        self.assertEqual(([shape["type"] for shape in only_first], [shape["type"] for shape in only_second]),
                         (["thread/list_datasets", "thread/add_dataset_tlv"], ["config/device_registry/list"]))
        for sends, refuses, only, examples in ((first, second, only_first, THREAD_EXAMPLES[:3]),
                                               (second, first, only_second, A2_EXAMPLES[1:])):
            for payload in examples:
                with self.subTest(sends=sends.name, payload=payload):
                    self.assertTrue(entity.raw_command_listed(payload, tuple(only)))
                    self.assertFalse(entity.raw_command_listed(payload, refuses.commands))
                    reply, events = await self.sent(sends, copy.deepcopy(payload))
                    self.assertIs(reply["success"], True)
                    self.assertEqual(events[-1][1], {"id": 1, **payload})
                    await self.refused_command(refuses, copy.deepcopy(payload))
            # Every shape that is only the one lane's was tried.
            for shape in only:
                self.assertTrue(any(entity.raw_command_listed(payload, (shape,)) for payload in examples))
        # The one command both send is the topology read.
        self.assertEqual([shape for shape in thread.RAW_COMMANDS if shape in a2.A2_RAW_COMMANDS], [TOPOLOGY_READ])
        for item in (first, second):
            reply, _events = await self.sent(item, dict(TOPOLOGY_READ))
            self.assertIs(reply["success"], True)

    def test_each_created_object_lane_list_is_exactly_this(self):
        # Written out, so a command added to either list fails here.
        registry_list = {"type": "config/entity_registry/list"}
        self.assertEqual(created.SCENE_RAW_COMMANDS, (registry_list,))
        self.assertEqual(created.HELPER_RAW_COMMANDS, (
            registry_list, {"type": "input_boolean/list"}, {"type": "input_number/list"},
            {"type": "input_boolean/delete", "input_boolean_id": created.LANE_ID},
            {"type": "input_number/delete", "input_number_id": created.LANE_ID}))
        self.assertEqual((type(created.LANE_ID), created.LANE_ID.pattern, created.LANE_ID.flags & ~re.UNICODE),
                         (re.Pattern, "hamcp_g4_[0-9a-z_]{1,48}", 0))
        for commands in (created.SCENE_RAW_COMMANDS, created.HELPER_RAW_COMMANDS):
            self.assertIs(type(commands), tuple)
            self.assertEqual([type(shape) for shape in commands], [dict] * len(commands))
        # One list per lane and no list for the script: each class holds its own, and the
        # class both extend lists nothing.
        self.assertFalse(hasattr(created, "RAW_COMMANDS"))
        self.assertIsNot(created.SCENE_RAW_COMMANDS, created.HELPER_RAW_COMMANDS)
        self.assertEqual(created.LANE_CLIENTS, {"scene_create_delete": created.SceneLaneRaw,
                                                "created_helper_delete": created.HelperLaneRaw})
        self.assertEqual(set(created.LANE_CLIENTS), set(created.LANES))
        self.assertIs(vars(created.SceneLaneRaw)["commands"], created.SCENE_RAW_COMMANDS)
        self.assertIs(vars(created.HelperLaneRaw)["commands"], created.HELPER_RAW_COMMANDS)
        self.assertEqual(vars(created.LaneRaw)["commands"], ())
        for client in (created.SceneLaneRaw, created.HelperLaneRaw):
            self.assertEqual(set(vars(client)) - {"__module__", "__doc__", "__firstlineno__",
                                                  "__static_attributes__"}, {"commands"})

    async def test_each_created_object_lane_refuses_what_only_the_other_lane_sends(self):
        scene, helper = BY_NAME["created_scene"], BY_NAME["created_helper"]
        only_helper = [shape for shape in created.HELPER_RAW_COMMANDS if shape not in created.SCENE_RAW_COMMANDS]
        self.assertEqual(len(only_helper), 4)
        self.assertEqual(len(CREATED_HELPER_ONLY), 7)
        for payload in CREATED_HELPER_ONLY:
            with self.subTest(payload=payload):
                # Only on the helper lane's list ...
                self.assertTrue(entity.raw_command_listed(payload, tuple(only_helper)))
                self.assertFalse(entity.raw_command_listed(payload, created.SCENE_RAW_COMMANDS))
                # ... so the helper lane sends it and the scene lane refuses it before a session.
                reply, events = await self.sent(helper, copy.deepcopy(payload))
                self.assertIs(reply["success"], True)
                self.assertEqual(events[-1][1], {"id": 1, **payload})
                await self.refused_command(scene, copy.deepcopy(payload))
        # Every shape that is only the helper lane's was tried.
        for shape in only_helper:
            self.assertTrue(any(entity.raw_command_listed(payload, (shape,)) for payload in CREATED_HELPER_ONLY))
        # The scene lane's one command is the helper lane's too, so no command is the scene
        # lane's alone and the helper lane has none of the scene lane's to refuse.
        self.assertEqual([shape for shape in created.SCENE_RAW_COMMANDS
                          if shape not in created.HELPER_RAW_COMMANDS], [])

    async def test_the_class_the_created_object_lanes_extend_sends_no_command(self):
        base = lane("created_base", created, created.LaneRaw, call, LOCAL_SOCKET, (), (), (), SOCKET_METHODS, 30,
                    set, settings=configured_local, earlier_check=True, commands=())
        for payload in CREATED_HELPER_EXAMPLES:
            with self.subTest(payload=payload):
                await self.refused_command(base, copy.deepcopy(payload))


class NamedValue(ClientCase):
    """A field that names its value takes that value and no other of its type."""

    def test_the_table_holds_every_such_field_of_every_list(self):
        listed, tabled = {}, {}
        for name, kind, keys, field, want in (
                *((item.name, shape["type"], frozenset(shape), field, want)
                  for item in SHAPED for shape in item.commands
                  for field, want in shape.items() if field != "type" and names_its_value(want)),
                *(("security_metadata", kind, frozenset({"type", *fields}), field, want)
                  for kind, fields in security.RAW_COMMANDS.items() for field, want in fields.items())):
            listed.setdefault((name, kind, keys, field), []).extend(want if type(want) is tuple else [want])
        for name, example, fields in NAMED:
            for field in fields:
                tabled.setdefault((name, example["type"], frozenset(example), field), []).append(example[field])
        self.assertEqual(set(tabled), set(listed))
        self.assertEqual(len(tabled), 31)
        # And the values a list names for the field are the ones the table sends: no list names one more.
        for key, values in listed.items():
            with self.subTest(field=key):
                self.assertEqual([value for value in values if value not in tabled[key]], [])
                self.assertEqual([value for value in tabled[key] if value not in values], [])
        # The type of every shape is named too; no list holds a pattern or a free string for it.
        for item in SHAPED:
            for shape in item.commands:
                self.assertIs(type(shape["type"]), str)
        for kind in security.RAW_COMMANDS:
            self.assertIs(type(kind), str)

    async def test_another_value_of_a_named_field_is_refused_before_a_session(self):
        by_name, count = {item.name: item for item in LANES}, 0
        for name, example, fields in NAMED:
            item = by_name[name]
            # The command as its lane sends it passes, so each refusal below is the one field's.
            reply, _events = await self.sent(item, copy.deepcopy(example))
            self.assertIs(reply["success"], True)
            for field in fields:
                named = example[field]
                for value in others(named):
                    self.assertNotEqual(value, named)
                    if named is not None:
                        self.assertIs(type(value), type(named))
                    count += 1
                    with self.subTest(lane=name, type=example["type"], field=field, value=value):
                        await self.refused_command(item, {**copy.deepcopy(example), field: value})
        self.assertGreater(count, 6 * len(NAMED))

    async def test_another_type_is_refused_for_every_command_a_lane_sends(self):
        for item in LANES:
            for example in item.examples:
                for value in others(example["type"]):
                    with self.subTest(lane=item.name, payload=example, type=value):
                        await self.refused_command(item, {**example, "type": value})


class DialTarget(ClientCase):
    """What is dialled is the pinned constant, and what is sent is the listed command."""

    async def test_every_command_a_lane_sends_goes_to_the_pin_unchanged(self):
        for item in LANES:
            for payload in item.examples:
                with self.subTest(lane=item.name, payload=payload):
                    before = repr(payload)
                    reply, events = await self.sent(item, payload)
                    self.assertEqual(reply, {"type": "result", "id": 1, "success": True, "result": None})
                    self.assertEqual([event[0] for event in events], ["session", "ws_connect", "send", "send"])
                    self.assertEqual(set(events[0][1]), {"timeout", "trust_env", "trace_configs"})
                    self.assertIs(events[0][1]["trust_env"], False)
                    self.assertEqual(events[0][1]["timeout"].total, item.seconds)
                    self.assert_dialled(item, events[1][1])
                    self.assertEqual(events[1][1:], (item.pin, item.dial))
                    self.assertEqual(events[2][1], {"type": "auth", "access_token": TOKEN})
                    self.assertEqual(events[3][1], {"id": 1, **payload})
                    self.assertEqual(repr(payload), before)

    async def test_the_dial_site_uses_the_constant_not_the_configured_value(self):
        for item in LANES:
            equal, elsewhere = item.pin.encode().decode(), item.pin.replace(host_of(item.pin), ELSEWHERE)
            self.assertNotEqual(elsewhere, item.pin)
            self.assertEqual(equal, item.pin)
            if item.constant is not None:
                self.assertEqual(equal, item.constant)
                self.assertIsNot(equal, item.constant)
            for label, later in (("an equal string", equal), ("changed after the client was built", elsewhere)):
                with self.subTest(lane=item.name, configured=label):
                    settings, events = item.settings(equal), []
                    with patch.object(aiohttp, "ClientSession", new=fake_session(events)):
                        client = item.client(settings)
                        settings.websocket_url = later
                        client.url = client.websocket_url = client.core_url = later
                        reply = await item.send(client, item.examples[0])
                    self.assertIs(reply["success"], True)
                    dialled = [event[1] for event in events if event[0] == "ws_connect"]
                    self.assertEqual(len(dialled), 1)
                    self.assert_dialled(item, dialled[0])

    async def test_the_socket_never_trusts_the_proxy_environment(self):
        proxies = {name: "http://proxy.invalid:3128" for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                                                                  "WS_PROXY", "WSS_PROXY")}
        for item in LANES:
            with self.subTest(lane=item.name), patch.dict(os.environ, proxies):
                _reply, events = await self.sent(item, item.examples[0])
                self.assertIs(events[0][1].get("trust_env"), False)
                self.assertEqual(events[1][1], item.pin)


UNLISTED = {"type": "config/auth/delete", "user_id": "synthetic"}


def replaced(payload):
    payload.clear()
    payload.update(UNLISTED)


def retyped(payload):
    payload["type"] = "config/core/update"


def widened(payload):
    payload["extra"] = True


def nested(payload):
    """Change a list or mapping inside the payload, leaving the payload's own items as they are."""
    for value in payload.values():
        if type(value) is list:
            value.append("synthetic-added")
        elif type(value) is dict:
            value["synthetic-added"] = True


class CheckedFrame(ClientCase):
    """The frame sent is the command that was checked, whatever happens to the caller's payload meanwhile."""

    async def changed_during(self, item, example, stage, change):
        payload, checked, seen = copy.deepcopy(example), copy.deepcopy(example), []

        def during(now):
            seen.append(now)
            if now == stage:
                change(payload)

        events = []
        with patch.object(aiohttp, "ClientSession", new=fake_session(events, during=during)):
            reply = await item.send(item.client(item.settings(item.pin)), payload)
        self.assertEqual(tuple(seen), STAGES)
        # The caller's payload really was changed while the client waited.
        self.assertNotEqual(payload, checked)
        self.assertEqual([event[1] for event in events if event[0] == "send"],
                         [{"type": "auth", "access_token": TOKEN}, {"id": 1, **checked}])
        self.assertEqual(reply, {"type": "result", "id": 1, "success": True, "result": None})

    async def test_a_payload_changed_while_the_socket_connects_is_not_what_is_sent(self):
        for item in LANES:
            for stage in STAGES:
                for change in (replaced, retyped, widened):
                    for example in item.examples:
                        with self.subTest(lane=item.name, stage=stage, change=change.__name__, payload=example):
                            await self.changed_during(item, example, stage, change)

    async def test_a_value_changed_inside_the_payload_while_the_socket_connects_is_not_what_is_sent(self):
        # A copy of the payload's own items alone would still share these.
        count = 0
        for item in LANES:
            for example in item.examples:
                if not any(type(value) in (list, dict) for value in example.values()):
                    continue
                count += 1
                for stage in STAGES:
                    with self.subTest(lane=item.name, stage=stage, payload=example):
                        await self.changed_during(item, example, stage, nested)
        # entity_metadata's label and alias lists, and seq-15's options, service data and dashboard.
        self.assertEqual(count, 11)

    async def test_the_frame_shares_nothing_with_the_callers_payload(self):
        # The copy is the client's own, and the caller's payload is left as it was.
        for item in LANES:
            for example in item.examples:
                with self.subTest(lane=item.name, payload=example):
                    payload = copy.deepcopy(example)
                    _reply, events = await self.sent(item, payload)
                    self.assertEqual(payload, example)
                    frame = events[3][1]
                    self.assertEqual(frame, {"id": 1, **example})
                    for key, value in frame.items():
                        if type(value) in (list, dict):
                            self.assertIsNot(value, payload[key])


def raw_sites(item):
    """Every ``raw.<send>(...)`` call in a lane's sources: resolved signatures, and the rest by text."""
    static, dynamic = set(), {}
    for filename, names in item.sources:
        tree = ast.parse((SCRIPTS / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in item.methods and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "raw"):
                continue
            assert len(node.args) == 1 and not node.keywords, (filename, node.lineno)
            argument = node.args[0]
            signature = None
            if isinstance(argument, ast.Dict):
                keys = [key.value if isinstance(key, ast.Constant) and type(key.value) is str else None
                        for key in argument.keys]
                if None not in keys and "type" in keys and len(set(keys)) == len(keys):
                    kind = argument.values[keys.index("type")]
                    value = (kind.value if isinstance(kind, ast.Constant)
                             else getattr(names, kind.id, None) if isinstance(kind, ast.Name) else None)
                    if type(value) is str:
                        signature = (value, frozenset(keys))
            if signature is None:
                dynamic.setdefault(ast.unparse(argument), []).append((filename, node.lineno))
            else:
                static.add(signature)
    return static, dynamic


def ids_calls(filename, names):
    """The ``ids(command, field, **arguments)`` cleanup reads of one lane source."""
    found = set()
    for node in ast.walk(ast.parse((SCRIPTS / filename).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ids":
            kind = node.args[0]
            value = kind.value if isinstance(kind, ast.Constant) else getattr(names, kind.id)
            assert type(value) is str and all(keyword.arg for keyword in node.keywords)
            found.add((value, frozenset({"type", *(keyword.arg for keyword in node.keywords)})))
    return found


# The send sites whose command is not a literal at the call: what each one sends.
DYNAMIC_SITES = {
    "entity_metadata": {"{'type': command, **arguments}": lambda: ids_calls(
        "entity_metadata_contract_acceptance.py", entity)},
    "configuration_delete": {},
    "seq13": {"{'type': command}": lambda: ids_calls("seq13_contract_acceptance.py", seq13)},
    "seq15": {"{'type': DASHBOARD_UPDATE, 'dashboard_id': owned['dashboard'], field: value}": lambda: {
        (seq15.DASHBOARD_UPDATE, frozenset({"type", "dashboard_id", field})) for field in ("url_path", "mode")}},
    **dict.fromkeys(("created_scene", "created_helper"), {
        "{'type': domain + '/list'}": lambda: {
            (domain + "/list", frozenset({"type"})) for domain in created.HELPER_DOMAINS},
        "{'type': domain + '/delete', domain + '_id': object_id}": lambda: {
            (domain + "/delete", frozenset({"type", domain + "_id"})) for domain in created.HELPER_DOMAINS}}),
    "security_metadata": {"payload": lambda: {
        (payload["type"], frozenset(payload)) for payload in security.SNAPSHOTS}},
    # Both Thread/Matter lanes read the topology with the product reader's own payload.
    "thread_matter": {"dict(PAYLOADS[TOPOLOGY])": lambda: {
        (thread.PAYLOADS[thread.TOPOLOGY]["type"], frozenset(thread.PAYLOADS[thread.TOPOLOGY]))}},
    "thread_matter_a2": {"dict(PAYLOADS[TOPOLOGY])": lambda: {
        (a2.PAYLOADS[a2.TOPOLOGY]["type"], frozenset(a2.PAYLOADS[a2.TOPOLOGY]))}},
}


class LaneInventory(ClientCase):
    """Each closed list is what its lane sends: nothing more is listed and nothing sent is missing."""

    def test_each_list_is_exactly_the_commands_its_lane_sources_send(self):
        for item in LANES:
            with self.subTest(lane=item.name):
                static, dynamic = raw_sites(item)
                expected = DYNAMIC_SITES[item.name]
                self.assertEqual(set(dynamic), set(expected))
                sent = set(static)
                for resolve in expected.values():
                    resolved = resolve()
                    self.assertTrue(resolved)
                    sent |= resolved
                # Lanes that share a script share its send sites: together their lists are those
                # sites, and the test after the next says which of them each lane reaches.
                sharing = [other for other in LANES if other.sources == item.sources]
                self.assertEqual(len(sharing), 2 if item in CREATED_LANES else 1)
                self.assertEqual(sent, set().union(*(other.signatures() for other in sharing)))

    def test_the_loop_behind_the_seq15_dynamic_site_is_the_reviewed_one(self):
        source = (SCRIPTS / "seq15_contract_acceptance.py").read_text(encoding="utf-8")
        self.assertEqual(source.count('for field, value in (("url_path", "seq15-other"), ("mode", "yaml")):'), 1)
        self.assertEqual(source.count("field: value}"), 1)

    def test_the_created_object_reader_sends_through_the_lane_client_and_only_listed_reads(self):
        # That lane's reads go through a Reader handed the client's own ``ok``, so they are
        # not ``raw.<send>`` sites. What it is handed, and what it sends through it, is this.
        tree = ast.parse((SCRIPTS / "created_object_contract_acceptance.py").read_text(encoding="utf-8"))
        nodes = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        def is_raw(node):
            return isinstance(node, ast.Name) and node.id == "raw"

        # Every place the client, or one of its methods, is given to something else.
        handed = [ast.unparse(node) for node in nodes
                  for argument in (*node.args, *(keyword.value for keyword in node.keywords))
                  if is_raw(argument) or isinstance(argument, ast.Attribute) and is_raw(argument.value)]
        self.assertEqual(sorted(handed), ["Reader(raw.status, raw.ok)", "Reader(raw.status, raw.ok)",
                                          "cleanup_fixtures(raw, owned)"])
        # And it is bound once, never rebound, aliased or returned.
        names = [node for node in ast.walk(tree) if is_raw(node)]
        self.assertEqual([type(node.ctx) for node in names].count(ast.Store), 1)
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        loads = [node for node in names if isinstance(node.ctx, ast.Load)]
        self.assertEqual({type(parents[node]).__name__ for node in loads}, {"Attribute", "Call"})
        reader = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "Reader")
        sends = [node for node in ast.walk(reader) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == "ok"]
        self.assertEqual(sorted(ast.unparse(node) for node in sends),
                         ["self.ok({'type': REGISTRY_LIST})", "self.ok({'type': domain + '/list'})"])
        # No other ``ok``, ``call`` or ``refused`` is reached in the script except on ``raw`` itself.
        elsewhere = [ast.unparse(node.func) for node in nodes if isinstance(node.func, ast.Attribute)
                     and node.func.attr in SOCKET_METHODS and node not in sends
                     and ast.unparse(node.func.value) != "raw"]
        self.assertEqual(elsewhere, [])
        # A domain is one of the two helper domains wherever a helper is created or read back.
        self.assertEqual(created.LANES["created_helper_delete"]["kinds"], created.HELPER_DOMAINS)
        self.assertEqual(created.HELPER_DOMAINS, ("input_boolean", "input_number"))
        for client in (created.LaneRaw, created.SceneLaneRaw, created.HelperLaneRaw):
            self.assertIs(client.ok, entity.RawWebSocket.ok)
            self.assertIs(client.call, entity.RawWebSocket.call)

    def test_every_listed_shape_has_a_sent_example_and_every_example_is_listed(self):
        for item in SHAPED:
            with self.subTest(lane=item.name):
                for shape in item.commands:
                    self.assertTrue(any(entity.raw_command_listed(example, (shape,)) for example in item.examples),
                                    shape["type"])
                for example in item.examples:
                    self.assertTrue(entity.raw_command_listed(example, item.commands), example)
        self.assertEqual({(example["type"], example.get("assistant")) for example in SECURITY_EXAMPLES},
                         {(kind, None) for kind, fields in security.RAW_COMMANDS.items() if not fields}
                         | {(security.EXPOSE_NEW_GET, assistant) for assistant in security.KNOWN_ASSISTANTS})
        self.assertEqual(len(SECURITY_EXAMPLES), 8 + len(security.KNOWN_ASSISTANTS))

    def test_no_lane_source_builds_the_client_under_another_name(self):
        # The inventory follows the name ``raw``; nothing else holds one of these clients.
        def builds(node):
            # A client class called by name, or the created-object lanes' class looked up by lane.
            return isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id in BUILDERS
                or isinstance(node.func, ast.Subscript) and ast.unparse(node.func.value) == "LANE_CLIENTS")
        for item in LANES:
            for filename, _names in item.sources:
                tree = ast.parse((SCRIPTS / filename).read_text(encoding="utf-8"))
                built = [node for node in ast.walk(tree) if isinstance(node, ast.Assign) and builds(node.value)]
                calls = [node for node in ast.walk(tree) if builds(node)]
                with self.subTest(lane=item.name, source=filename):
                    self.assertEqual(len(calls), 0 if filename == "seq15_scene_contract.py" else 1)
                    self.assertEqual(len(built), len(calls))
                    for node in built:
                        self.assertEqual([ast.unparse(target) for target in node.targets], ["raw"])
                        self.assertEqual(ast.unparse(node.value.args[0]), "configured")

    def test_each_lane_builds_its_own_client_and_no_other(self):
        # A lane that built the shared client would be held to entity_metadata's
        # list and not its own, so the one client a lane builds is its own class.
        builders = BUILDERS
        own = {"entity_metadata": "RawWebSocket", "configuration_delete": "LaneRaw", "seq13": "LaneRaw",
               "seq15": "LaneRaw", "created_scene": "SceneLaneRaw", "created_helper": "HelperLaneRaw",
               "security_metadata": "RawCore", "thread_matter": "RawCore", "thread_matter_a2": "A2RawCore"}
        self.assertEqual(set(own), {item.name for item in LANES})
        for item in LANES:
            with self.subTest(lane=item.name):
                self.assertEqual(item.client.__name__, own[item.name])
                self.assertIs(getattr(item.module, own[item.name]), item.client)
                self.assertEqual(item.client.__module__, item.module.__name__)
                built, bases, uses = [], [], 0
                for filename, _names in item.sources:
                    tree = ast.parse((SCRIPTS / filename).read_text(encoding="utf-8"))
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in builders:
                            built.append(node.func.id)
                        elif isinstance(node, ast.ClassDef):
                            bases += [(node.name, base.id) for base in node.bases
                                      if isinstance(base, ast.Name) and base.id in builders]
                        uses += ((isinstance(node, ast.Name) and node.id in builders)
                                 or (isinstance(node, ast.Attribute) and node.attr in builders))
                if item in CREATED_LANES:
                    # No class is called by name there: the script builds the one its lane is listed
                    # under (the next test). The names are otherwise each class's base and that table.
                    self.assertEqual(built, [])
                    self.assertEqual(bases, [("LaneRaw", "RawWebSocket"), ("SceneLaneRaw", "LaneRaw"),
                                             ("HelperLaneRaw", "LaneRaw")])
                    self.assertEqual(uses, len(bases) + 2)
                elif item.name == "thread_matter_a2":
                    # The script and its runner each build the A2 class once, for the one run; the
                    # only other use of any of these names is that class naming the A1 client as its base.
                    self.assertEqual(built, ["A2RawCore", "A2RawCore"])
                    self.assertEqual(bases, [("A2RawCore", "RawCore")])
                    self.assertEqual(uses, 3)
                else:
                    self.assertEqual(built, [own[item.name]])
                    # The only other use of any of these names is the lane subclass naming its base.
                    subclass = [("LaneRaw", "RawWebSocket")] if own[item.name] == "LaneRaw" else []
                    self.assertEqual(bases, subclass)
                    self.assertEqual(uses, 1 + len(subclass))
                if item.module is not security:
                    # The list that class enforces is its own module's, set on the class itself.
                    self.assertIs(vars(item.client)["commands"], item.commands)
                    self.assertTrue(all(vars(other.client)["commands"] is not item.commands
                                        for other in SHAPED if other is not item))

    def test_the_created_object_script_builds_the_client_of_the_lane_it_runs(self):
        tree = ast.parse((SCRIPTS / "created_object_contract_acceptance.py").read_text(encoding="utf-8"))
        table = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "LANE_CLIENTS"]
        # Written once and read once.
        self.assertEqual([type(node.ctx) for node in table], [ast.Store, ast.Load])
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        self.assertEqual(ast.unparse(parents[table[0]]), "LANE_CLIENTS = {'scene_create_delete': SceneLaneRaw, "
                                                         "'created_helper_delete': HelperLaneRaw}")
        self.assertIn(parents[table[0]], tree.body)
        build = parents[parents[parents[table[1]]]]
        self.assertEqual(ast.unparse(build), "raw = LANE_CLIENTS[lane](configured)")
        run = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "run")
        self.assertIn(build, run.body)
        # ``lane`` there is the lane the caller asked to run: the function's own argument, never rebound.
        self.assertEqual([argument.arg for argument in run.args.args], ["lane", "configured", "entry", "directory"])
        self.assertEqual([node for node in ast.walk(run) if isinstance(node, ast.Name) and node.id == "lane"
                          and not isinstance(node.ctx, ast.Load)], [])
        self.assertEqual([node.name for node in ast.walk(run)
                          if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
                          and "lane" in [argument.arg for argument in node.args.args] and node is not run], [])

    def test_each_created_object_lane_reaches_exactly_the_sends_on_its_list(self):
        # The two lanes share a script, so which of its send sites a lane reaches is read from
        # what that lane runs: its scenarios, its predecessor probe, and clean-up of what it owns.
        tree = ast.parse((SCRIPTS / "created_object_contract_acceptance.py").read_text(encoding="utf-8"))
        top = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

        def named(*parts):
            return {node.id for part in parts for node in ast.walk(part)
                    if isinstance(node, ast.Name) and node.id in top}

        def reach(roots):
            seen, todo = set(), sorted(roots)
            while todo:
                name = todo.pop()
                if name not in seen:
                    seen.add(name)
                    todo += sorted(named(top[name]))
            return seen
        # ``run`` picks the predecessor probe by lane, in one place.
        (choice,) = [node for node in ast.walk(top["run"]) if isinstance(node, ast.If)
                     and ast.unparse(node.test) == "lane == 'scene_create_delete'"]
        probes = {"scene_create_delete": named(*choice.body), "created_helper_delete": named(*choice.orelse)}
        self.assertEqual(probes, {"scene_create_delete": {"scene_probe", "withheld_scene_create"},
                                  "created_helper_delete": {"withheld_helper_undo"}})
        # What a Reader method sends, with the methods it calls on itself.
        reader = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Reader")
        methods = {node.name: node for node in reader.body if isinstance(node, ast.AsyncFunctionDef)}

        def reads(name, seen=()):
            found = set()
            for node in ast.walk(methods[name]):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and ast.unparse(node.func.value) == "self"):
                    if node.func.attr == "ok":
                        found.add(ast.unparse(node.args[0]))
                    elif node.func.attr in methods and node.func.attr not in seen:
                        found |= reads(node.func.attr, (*seen, name))
            return found
        self.assertEqual({name: reads(name) for name in methods}, {
            "registry": {"{'type': REGISTRY_LIST}"}, "state": set(), "automation": set(),
            "scene": {"{'type': REGISTRY_LIST}"},
            "helper": {"{'type': REGISTRY_LIST}", "{'type': domain + '/list'}"}})
        # Clean-up reads the registry for every lane, and lists and deletes helpers only for the
        # helpers the lane's own scenarios recorded as theirs.
        cleanup = top["cleanup_fixtures"]
        loops = [node for node in ast.walk(cleanup) if isinstance(node, (ast.For, ast.comprehension))
                 and "owned['helpers']" in ast.unparse(node.iter)]
        within = {id(inner) for loop in loops for inner in ast.walk(loop)}
        outer = {id(inner) for node in ast.walk(cleanup) if isinstance(node, ast.DictComp)
                 and any(generator in loops for generator in node.generators) for inner in ast.walk(node)}
        sends = {ast.unparse(node.args[0]): id(node) in within | outer for node in ast.walk(cleanup)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr in SOCKET_METHODS and ast.unparse(node.func.value) == "raw"}
        self.assertEqual(sends, {"{'type': REGISTRY_LIST}": False, "{'type': domain + '/list'}": True,
                                 "{'type': domain + '/delete', domain + '_id': object_id}": True})
        resolve = {"{'type': REGISTRY_LIST}": lambda: {(created.REGISTRY_LIST, frozenset({"type"}))},
                   **DYNAMIC_SITES["created_helper"]}
        owners = {}
        for item, name in zip(CREATED_LANES, ("scene_create_delete", "created_helper_delete")):
            with self.subTest(lane=name):
                self.assertIs(item.client, created.LANE_CLIENTS[name])
                scenarios = {created.SCENARIOS[scenario].__qualname__.split(".")[0]
                             for scenario in created.LANES[name]["scenarios"]}
                reached = reach(scenarios | probes[name])
                used = {node.attr for function in reached for node in ast.walk(top[function])
                        if isinstance(node, ast.Attribute) and ast.unparse(node.value) == "c.reader"}
                self.assertLessEqual(used, {*methods, "status"})
                # The synthetic consumer the mixed plan adds is read back through the registry.
                adds = any(ast.unparse(node) == "c.add_automation" for function in reached
                           for node in ast.walk(top[function]))
                owners[name] = any(ast.unparse(node).startswith("c.owned['helpers'].append(")
                                   for function in reached for node in ast.walk(top[function]))
                sent = {"{'type': REGISTRY_LIST}"}.union(*(reads(method) for method in used & set(methods)))
                sent |= {text for text, helpers_only in sends.items() if owners[name] or not helpers_only}
                self.assertEqual(adds, name == "created_helper_delete")
                self.assertEqual(set().union(*(resolve[text]() for text in sent)), item.signatures())
        self.assertEqual(owners, {"scene_create_delete": False, "created_helper_delete": True})


class LaneSettings(ClientCase):
    """The URL each runner really configures is the one its lane's client pins."""

    def settings(self, core):
        return Settings(ha_url=core, ha_token=TOKEN, access_secret="synthetic-secret", port=8100,
                        audit_path="unused", rate_limit_per_minute=120, rate_limit_burst=25,
                        destructive_services=frozenset())

    def test_the_harness_core_is_the_main_pin(self):
        # entity_metadata, configuration_delete, seq-13 and seq-15 run only inside
        # the Core harness, with the settings it builds from its own REAL_HA_URL.
        source = HARNESS_PATH.read_text(encoding="utf-8")
        start = 'HA_URL = loopback_core_url(os.environ.get("REAL_HA_URL", "' + MAIN_CORE + '"))'
        self.assertEqual(source.count(start), 1)
        self.assertEqual(source.count("        ha_url=HA_URL,\n"), 1)
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("REAL_HA_URL: " + MAIN_CORE + "\n", workflow)
        real = self.settings(MAIN_CORE)
        self.assertEqual(real.websocket_url, MAIN_SOCKET)
        self.assertEqual(real.websocket_url, entity.RAW_CORE_URL)
        self.assertEqual([item.name for item in HARNESS_LANES],
                         ["entity_metadata", "configuration_delete", "seq13", "seq15"])
        for item in HARNESS_LANES:
            with self.subTest(lane=item.name):
                events = []
                with patch.object(aiohttp, "ClientSession", new=fake_session(events)):
                    client = item.client(real)
                self.assertEqual(events, [])
                if hasattr(client, "api_url"):
                    self.assertEqual(client.api_url, MAIN_CORE + "/api")

    def test_the_local_lane_core_is_the_local_pin(self):
        import core_security_metadata_local as local
        self.assertEqual(local.PORTS, (18123,))
        self.assertIn('REAL_HA_URL="' + LOCAL_CORE + '"', Path(local.__file__).read_text(encoding="utf-8"))
        real = self.settings(LOCAL_CORE)
        self.assertEqual(real.websocket_url, security.RAW_CORE_URL)
        events = []
        with patch.object(aiohttp, "ClientSession", new=fake_session(events)):
            security.RawCore(real)
        self.assertEqual(events, [])
        # The other family's Core is not this client's, in either direction.
        self.refused_url(BY_NAME["security_metadata"], self.settings(MAIN_CORE).websocket_url)
        self.refused_url(BY_NAME["entity_metadata"], real.websocket_url)

    def test_the_created_object_runner_core_is_its_pin(self):
        # The created-object lanes run only through their local runner, on the Core it starts.
        import core_created_object_local as local
        self.assertEqual(local.PORTS, (18123,))
        source = Path(local.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count('REAL_HA_URL="' + LOCAL_CORE + '"'), 1)
        self.assertEqual(source.count('"127.0.0.1:18123:8123"'), 1)
        self.assertEqual(created.RAW_CORE_URL, f"ws://127.0.0.1:{local.PORTS[0]}/api/websocket")
        real = self.settings(LOCAL_CORE)
        self.assertEqual((real.websocket_url, real.api_url), (created.RAW_CORE_URL, LOCAL_CORE + "/api"))
        for item in CREATED_LANES:
            events = []
            with patch.object(aiohttp, "ClientSession", new=fake_session(events)):
                client = item.client(real)
            self.assertEqual(events, [])
            self.assertIs(client.client.settings, real)
            # The harness Core is not this client's, nor this Core any harness lane's.
            self.refused_url(item, self.settings(MAIN_CORE), pinned="settings")
        for item in HARNESS_LANES:
            self.refused_url(item, real.websocket_url)
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("created_object", workflow)

    def test_the_thread_matter_runner_core_is_its_pin(self):
        # The A1 lane runs only through its local runner, on the Core it starts and publishes on loopback.
        import core_thread_matter_topology_local as local
        self.assertEqual((local.PORT, local.PORTS), (18124, (18124,)))
        source = Path(local.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count('REAL_HA_URL=f"http://127.0.0.1:{PORT}"'), 1)
        self.assertEqual(source.count('"--publish", f"127.0.0.1:{PORT}:8123"'), 1)
        self.assertEqual(source.count("REAL_HA_URL="), 1)
        self.assertEqual(thread.RAW_CORE_URL, f"ws://127.0.0.1:{local.PORT}/api/websocket")
        real = self.settings(THREAD_CORE)
        self.assertEqual(real.websocket_url, thread.RAW_CORE_URL)
        item, events = BY_NAME["thread_matter"], []
        with patch.object(aiohttp, "ClientSession", new=fake_session(events)):
            thread.RawCore(real)
        self.assertEqual(events, [])
        # No other lane's Core is this client's, nor this Core any other lane's client's.
        for core in (MAIN_CORE, LOCAL_CORE, BRIDGE_CORE):
            self.refused_url(item, self.settings(core).websocket_url)
        for other in LANES:
            if other is not item:
                with self.subTest(lane=other.name):
                    self.refused_url(other, real.websocket_url)

    def test_the_a2_runner_core_is_the_one_it_handed_over(self):
        # The A2 lane runs only through its runner, which hands its Core to the harness; the
        # settings the runner then takes from the harness are for that Core, and so is the pin.
        source = (SCRIPTS / A2_RUNNER).read_text(encoding="utf-8")
        self.assertEqual(source.count("configured = replace(harness.settings(token), "), 1)
        self.assertEqual(source.count("raw = A2RawCore(configured)"), 1)
        self.assertEqual((a2_runner.CORE_PORT, harness.HA_URL, harness.accepted_lane_core()),
                         (8123, BRIDGE_CORE, BRIDGE_CORE))
        real = self.settings(harness.HA_URL)
        self.assertEqual(real.websocket_url, BRIDGE_SOCKET)
        self.assertEqual(real.websocket_url, a2.A2RawCore.core_url)
        item, events = BY_NAME["thread_matter_a2"], []
        with patch.object(aiohttp, "ClientSession", new=fake_session(events)):
            a2.A2RawCore(real)
        self.assertEqual(events, [])
        for core in (MAIN_CORE, LOCAL_CORE, THREAD_CORE, "http://172.30.0.3:8123", LAN_CORE):
            self.refused_url(item, self.settings(core).websocket_url)
        for other in LANES:
            if other is not item:
                with self.subTest(lane=other.name):
                    self.refused_url(other, real.websocket_url)


class Response:
    def __init__(self, body, status=200):
        self.body, self.status = body, status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def json(self, content_type=None):
        return self.body


class Requests:
    """Stand-in for the session the harness passes to ``_json_request``."""

    def __init__(self, *replies):
        self.calls, self.replies = [], list(replies)

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return Response(self.replies.pop(0) if self.replies else {})

    def get(self, *args, **kwargs):
        raise AssertionError("no probe belongs to these tests")


class HarnessCase(unittest.IsolatedAsyncioTestCase):
    def assert_fixed_code(self, exception, reason):
        self.assertIs(type(exception), ValueError)
        self.assertEqual(exception.args, (reason,))
        self.assertIsNone(exception.__cause__)
        self.assertEqual(exception.__dict__, {})


class HarnessStart(HarnessCase):
    """The Core harness refuses to start for any REAL_HA_URL but a bare loopback origin."""

    REFUSED = (
        "http://" + ELSEWHERE + ":8123", "http://" + ELSEWHERE_NAME + ":8123", "http://homeassistant.local:8123",
        "http://supervisor/core", "http://localhost:8123", "http://127.0.0.2:8123", "http://127.1:8123",
        "http://2130706433:8123", "http://0.0.0.0:8123", "http://[::]:8123", "http://[::2]:8123",
        "http://::1:8123", "http://127.0.0.1." + ELSEWHERE_NAME + ":8123",
        # Another character where the address has a dot, or one character where it has the bracketed
        # address: each is a name that would be looked up.
        "http://127a0b0c1:8123", "http://127-0-0-1:8123", "http://127.0.0x1", "http://127x0.0.1:8123",
        "http://127.0x0.1:8123", "http://127.0.0x1:8123", "http://127001:8123", "http://1:8123", "http://::8123",
        # Userinfo, so that the loopback text is not the host.
        "http://127.0.0.1:8123@" + ELSEWHERE, "http://127.0.0.1@" + ELSEWHERE_NAME + ":8123",
        "http://user:synthetic@127.0.0.1:8123", "http://@127.0.0.1:8123",
        # Another scheme, or more than the origin.
        "https://127.0.0.1:8123", "ws://127.0.0.1:8123", "HTTP://127.0.0.1:8123", "127.0.0.1:8123",
        "//127.0.0.1:8123", "http://127.0.0.1:8123/api", "http://127.0.0.1:8123/@" + ELSEWHERE_NAME,
        "http://127.0.0.1:8123?x=1", "http://127.0.0.1:8123#x", "http://127.0.0.1:", "http://127.0.0.1:port",
        "http://127.0.0.1:812345", "http://127.0.0.1:-1",
        " http://127.0.0.1:8123", "http://127.0.0.1:8123 ", "http://127.0.0.1:8123\n", "http://127.0.0.1:8123\x00",
        "", "http://", None, 8123, b"http://127.0.0.1:8123", ("http://127.0.0.1:8123",),
        Text("http://127.0.0.1:8123"), Claims("http://" + ELSEWHERE + ":8123"))
    ACCEPTED = {
        MAIN_CORE: MAIN_CORE, LOCAL_CORE: LOCAL_CORE, MAIN_CORE + "/": MAIN_CORE, LOCAL_CORE + "/": LOCAL_CORE,
        "http://[::1]:8123": "http://[::1]:8123", "http://127.0.0.1": "http://127.0.0.1"}

    @classmethod
    def setUpClass(cls):
        restore_after_class(cls)

    def start(self, value):
        """Run the harness module body, as ``python scripts/real_ha_contract_tests.py`` would, under one URL."""
        events = []
        spec = importlib.util.spec_from_file_location("_hamcp364_harness_start_subject", HARNESS_PATH)
        module = importlib.util.module_from_spec(spec)
        environment = {} if value is None else {"REAL_HA_URL": value}
        with patch.dict(os.environ, environment), patch.object(sys, "path", list(sys.path)), \
                patch.object(aiohttp, "ClientSession", new=fake_session(events)):
            if value is None:
                os.environ.pop("REAL_HA_URL", None)
            try:
                spec.loader.exec_module(module)
            finally:
                self.assertNotIn(spec.name, sys.modules)
        self.assertEqual(events, [])
        return module

    def test_any_other_core_url_is_refused_by_the_check(self):
        for value in self.REFUSED:
            with self.subTest(value=value, kind=type(value).__name__):
                with self.assertRaises(ValueError) as caught:
                    harness.loopback_core_url(value)
                self.assert_fixed_code(caught.exception, "real_ha_url_not_loopback")
                self.assertNotIn(ELSEWHERE, repr(caught.exception))

    def test_the_configured_loopback_cores_pass_unchanged(self):
        for value, expected in self.ACCEPTED.items():
            with self.subTest(value=value):
                self.assertEqual(harness.loopback_core_url(value), expected)
                self.assertIs(type(harness.loopback_core_url(value)), str)

    def test_the_harness_does_not_start_for_another_core(self):
        # The refusal is the module's own first use of REAL_HA_URL: no entry
        # point, bootstrap or request helper exists to open a session with.
        for value in ("http://" + ELSEWHERE + ":8123", "http://homeassistant.local:8123", "http://localhost:8123",
                      "https://127.0.0.1:8123", "http://127.0.0.1:8123@" + ELSEWHERE, "http://127.0.0.1:8123/api"):
            with self.subTest(value=value):
                events = []
                spec = importlib.util.spec_from_file_location("_hamcp364_harness_refused_subject", HARNESS_PATH)
                module = importlib.util.module_from_spec(spec)
                with patch.dict(os.environ, {"REAL_HA_URL": value}), patch.object(sys, "path", list(sys.path)), \
                        patch.object(aiohttp, "ClientSession", new=fake_session(events)):
                    with self.assertRaises(ValueError) as caught:
                        spec.loader.exec_module(module)
                self.assert_fixed_code(caught.exception, "real_ha_url_not_loopback")
                self.assertEqual(events, [])
                for name in ("HA_URL", "CLIENT_ID", "_json_request", "bootstrap_disposable_admin", "settings",
                             "main"):
                    self.assertFalse(hasattr(module, name), name)

    def test_the_harness_starts_for_each_configured_loopback_core(self):
        for value, expected in ((None, MAIN_CORE), (MAIN_CORE, MAIN_CORE), (LOCAL_CORE, LOCAL_CORE),
                                (LOCAL_CORE + "/", LOCAL_CORE)):
            with self.subTest(value=value):
                module = self.start(value)
                self.assertEqual((module.HA_URL, module.CLIENT_ID), (expected, expected + "/"))
                self.assertEqual(module.settings(TOKEN).ha_url, expected)
                self.assertTrue(callable(module.bootstrap_disposable_admin))


class HarnessRequest(HarnessCase):
    """``_json_request`` sends only a rooted path, and only to the configured Core's host."""

    async def refused(self, path, reason, *, core=MAIN_CORE):
        session = Requests()
        with patch.object(harness, "HA_URL", core):
            with self.assertRaises(ValueError) as caught:
                await harness._json_request(session, "POST", path, json_body={"synthetic": True}, token=TOKEN)
        self.assert_fixed_code(caught.exception, reason)
        shown = str(caught.exception) + repr(caught.exception)
        for private in (TOKEN, ELSEWHERE, ELSEWHERE_NAME, "127.0.0.1", "http"):
            self.assertNotIn(private, shown)
        # Refused before the request was made.
        self.assertEqual(session.calls, [])

    async def test_a_path_that_is_not_rooted_is_refused_before_send(self):
        for path in ("api/onboarding", "", "@" + ELSEWHERE + "/api/onboarding", "." + ELSEWHERE_NAME + "/api",
                     ":9/api", "0/api", "?x=1", "#x", " /api/onboarding", "\n/api/onboarding",
                     "http://" + ELSEWHERE + "/api", None, 7, b"/api/onboarding", ("/api/onboarding",),
                     ["/api/onboarding"], Text("/api/onboarding"), Claims("@" + ELSEWHERE + "/api")):
            with self.subTest(path=path, kind=type(path).__name__):
                await self.refused(path, "real_ha_path_not_rooted")
        # Without a port in the configured URL the same text would extend the host itself.
        await self.refused("." + ELSEWHERE_NAME + "/api", "real_ha_path_not_rooted", core="http://127.0.0.1")

    async def test_a_target_whose_host_is_not_the_configured_core_is_refused_before_send(self):
        # A rooted path cannot change the host of a URL that has one, so the
        # join can only land elsewhere when the configured value has none. The
        # check is made on the joined URL all the same: it names a host, and
        # that host is the configured one.
        for core, path in (("http:", "//" + ELSEWHERE + "/api/onboarding"),
                           ("", "//" + ELSEWHERE_NAME + "/api/onboarding"),
                           ("http:", "//127.0.0.1:8123/api/onboarding"),
                           ("http:", "/api/onboarding"), ("", "/api/onboarding"),
                           ("http://", "/api/onboarding"), ("http://[", "/api/onboarding"),
                           ("http://[::1", "/api/onboarding"), ("http://:8123", "/api/onboarding")):
            with self.subTest(core=core, path=path):
                await self.refused(path, "real_ha_target_not_configured_core", core=core)

    async def test_the_harness_requests_are_sent_unchanged(self):
        body = {"client_id": MAIN_CORE + "/", "name": "Synthetic"}
        for core in (MAIN_CORE, LOCAL_CORE, "http://[::1]:8123", "http://127.0.0.1"):
            for path in ("/api/onboarding", "/api/onboarding/users", "/auth/token",
                         "/api/onboarding/integration", "/auth/long_lived_access_token",
                         "/api/config/config_entries/flow",
                         "/api/config/config_entries/flow/0123456789abcdef0123456789abcdef"):
                with self.subTest(core=core, path=path):
                    session = Requests({"synthetic": "reply"})
                    with patch.object(harness, "HA_URL", core):
                        reply = await harness._json_request(session, "POST", path, json_body=body, token=TOKEN)
                    self.assertEqual(reply, {"synthetic": "reply"})
                    self.assertEqual(session.calls, [("POST", core + path, {
                        "json": body, "data": None, "headers": {"Authorization": "Bearer " + TOKEN},
                        "allow_redirects": False})])
        session = Requests([{"step": "user", "done": False}])
        with patch.object(harness, "HA_URL", MAIN_CORE):
            self.assertEqual(await harness._json_request(session, "GET", "/api/onboarding"),
                             [{"step": "user", "done": False}])
        self.assertEqual(session.calls, [("GET", MAIN_CORE + "/api/onboarding",
                                          {"json": None, "data": None, "headers": {},
                                           "allow_redirects": False})])

    async def test_a_lane_that_rebinds_the_core_keeps_its_own_host(self):
        # The candidate-image lane points the harness at its own fixture network
        # after import; its requests still go to that one configured host.
        bridge = "http://172.25.0.2:8123"
        session = Requests({})
        with patch.object(harness, "HA_URL", bridge):
            await harness._json_request(session, "GET", "/api/onboarding")
            self.assertEqual(session.calls[0][1], bridge + "/api/onboarding")
        await self.refused("@" + ELSEWHERE + "/api", "real_ha_path_not_rooted", core=bridge)

    async def test_a_core_error_status_is_still_the_harness_failure(self):
        class Failing(Requests):
            def request(self, method, url, **kwargs):
                self.calls.append((method, url, kwargs))
                return Response({}, status=401)

        session = Failing()
        with patch.object(harness, "HA_URL", MAIN_CORE), self.assertRaises(RuntimeError):
            await harness._json_request(session, "POST", "/synthetic")
        self.assertEqual(len(session.calls), 1)


# A LAN-style private address, as a Home Assistant on a household network would have.
LAN_CORE = "http://192.168.1.10:8123"
A2_RUNNER = "core_thread_matter_topology_a2_local.py"


class HarnessHandOver(HarnessCase):
    """A Core off loopback reaches the harness only through ``use_internal_lane_core``.

    The A2 runner calls it with the subnet of the internal network it created.
    REAL_HA_URL stays loopback-only, and anything but a bare http://<IPv4>:8123
    origin inside that subnet is refused before anything is sent.
    """

    def setUp(self):
        self.events = []
        for manager in (patch.multiple(harness, HA_URL=harness.HA_URL, CLIENT_ID=harness.CLIENT_ID, _lane_core=None),
                        patch.object(aiohttp, "ClientSession", new=fake_session(self.events))):
            manager.start()
            self.addCleanup(manager.stop)
        self.before = (harness.HA_URL, harness.CLIENT_ID)

    def refused(self, origin, subnet, reason):
        with self.assertRaises(ValueError) as caught:
            harness.use_internal_lane_core(origin, subnet)
        self.assert_fixed_code(caught.exception, reason)
        shown = str(caught.exception) + repr(caught.exception)
        for private in ("172.", "192.168", "http", "/16", ELSEWHERE, ELSEWHERE_NAME):
            self.assertNotIn(private, shown)
        # Nothing was bound and nothing was opened.
        self.assertEqual((harness.HA_URL, harness.CLIENT_ID), self.before)
        self.assertIsNone(harness._lane_core)
        self.assertEqual(self.events, [])

    def test_real_ha_url_with_a_bridge_or_lan_address_still_refuses(self):
        for value in (BRIDGE_CORE, LAN_CORE, "http://172.17.0.1:8123", "http://172.25.0.2:8123",
                      "http://10.0.0.5:8123", "http://192.168.0.1:8123", "http://169.254.1.1:8123"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as caught:
                    harness.loopback_core_url(value)
                self.assert_fixed_code(caught.exception, "real_ha_url_not_loopback")
                # And the module itself does not start under it.
                spec = importlib.util.spec_from_file_location("_hamcp364_harness_bridge_subject", HARNESS_PATH)
                module = importlib.util.module_from_spec(spec)
                with patch.dict(os.environ, {"REAL_HA_URL": value}), patch.object(sys, "path", list(sys.path)):
                    with self.assertRaises(ValueError) as caught:
                        spec.loader.exec_module(module)
                self.assert_fixed_code(caught.exception, "real_ha_url_not_loopback")
                for name in ("HA_URL", "use_internal_lane_core", "_json_request", "bootstrap_disposable_admin"):
                    self.assertFalse(hasattr(module, name), name)
        self.assertEqual(self.events, [])

    def test_an_origin_that_is_not_a_bare_ipv4_on_8123_is_refused(self):
        for origin in (
                BRIDGE_CORE + "/", BRIDGE_CORE + "/api", BRIDGE_CORE + "?x=1", BRIDGE_CORE + "#x",
                "http://user@172.30.0.2:8123", "http://user:synthetic@172.30.0.2:8123", "http://@172.30.0.2:8123",
                "http://172.30.0.2:8123@" + ELSEWHERE, "http://[fd00::2]:8123", "http://[::1]:8123",
                "http://[::ffff:172.30.0.2]:8123", "http://core:8123", "http://homeassistant.local:8123",
                "http://172.30.0.2." + ELSEWHERE_NAME + ":8123", "http://localhost:8123",
                "http://172.30.0.2", "http://172.30.0.2:", "http://172.30.0.2:80", "http://172.30.0.2:18123",
                "http://172.30.0.2:08123", "http://172.30.0.2:81230", "https://172.30.0.2:8123",
                "ws://172.30.0.2:8123", "HTTP://172.30.0.2:8123", "172.30.0.2:8123", "//172.30.0.2:8123",
                # Not four plain decimal octets: each would be read as a name or as another address.
                "http://172.030.0.2:8123", "http://172.30.0.02:8123", "http://172.300.0.2:8123",
                "http://172.30.2:8123", "http://172.30.0.2.1:8123", "http://2887647234:8123",
                "http://0xac.30.0.2:8123", "http://172.30.0.2\uff10:8123", "http://172-30-0-2:8123",
                " " + BRIDGE_CORE, BRIDGE_CORE + " ", BRIDGE_CORE + "\n", BRIDGE_CORE + "\x00",
                "", "http://", None, 8123, BRIDGE_CORE.encode(), (BRIDGE_CORE,), Text(BRIDGE_CORE),
                Claims("http://" + ELSEWHERE + ":8123")):
            with self.subTest(origin=origin, kind=type(origin).__name__):
                self.refused(origin, BRIDGE_SUBNET, "real_ha_lane_core_not_bare_ipv4")

    def test_a_subnet_that_is_not_one_canonical_ipv4_network_is_refused(self):
        for subnet in ("172.30.0.0/8", "172.30.0.2/16", "172.30.0.0", "172.30.0.0/255.255.0.0", "172.30.0.0/016",
                       "172.30.0.0/16 ", " 172.30.0.0/16", "172.30.0.0/16\n", "172.30.0.0/33", "172.30/16",
                       # Wider than a Docker pool, or with no host address to give.
                       "0.0.0.0/0", "10.0.0.0/8", "172.16.0.0/12", "172.30.0.0/15", "172.30.0.2/31",
                       "172.30.0.2/32", "fd00::/64", "", None, 16, ["172.30.0.0/16"], ("172.30.0.0/16",),
                       b"172.30.0.0/16", Text(BRIDGE_SUBNET), Claims("10.0.0.0/8")):
            with self.subTest(subnet=subnet, kind=type(subnet).__name__):
                self.refused(BRIDGE_CORE, subnet, "real_ha_lane_subnet_not_usable")

    def test_an_address_outside_the_subnet_is_refused(self):
        for origin, subnet in ((LAN_CORE, BRIDGE_SUBNET), ("http://172.31.0.2:8123", BRIDGE_SUBNET),
                               ("http://172.29.255.254:8123", BRIDGE_SUBNET), ("http://10.0.0.5:8123", BRIDGE_SUBNET),
                               (MAIN_CORE, BRIDGE_SUBNET), ("http://" + ELSEWHERE + ":8123", BRIDGE_SUBNET),
                               (BRIDGE_CORE, "172.30.1.0/24"), (BRIDGE_CORE, "192.168.1.0/24"),
                               # Inside the range, but the network's own address or its broadcast address.
                               ("http://172.30.0.0:8123", BRIDGE_SUBNET),
                               ("http://172.30.255.255:8123", BRIDGE_SUBNET)):
            with self.subTest(origin=origin, subnet=subnet):
                self.refused(origin, subnet, "real_ha_lane_core_outside_subnet")

    async def test_the_accepted_core_is_the_one_the_harness_then_sends_to(self):
        self.assertEqual(self.before, (MAIN_CORE, MAIN_CORE + "/"))
        accepted = harness.use_internal_lane_core(BRIDGE_CORE, BRIDGE_SUBNET)
        self.assertEqual(accepted, BRIDGE_CORE)
        self.assertIs(type(accepted), str)
        self.assertEqual((harness.HA_URL, harness.CLIENT_ID, harness._lane_core),
                         (BRIDGE_CORE, BRIDGE_CORE + "/", BRIDGE_CORE))
        self.assertEqual(harness.settings(TOKEN).ha_url, BRIDGE_CORE)
        self.assertEqual(self.events, [])
        session = Requests({"synthetic": "reply"})
        self.assertEqual(await harness._json_request(session, "GET", "/api/onboarding"), {"synthetic": "reply"})
        self.assertEqual(session.calls, [("GET", BRIDGE_CORE + "/api/onboarding",
                                          {"json": None, "data": None, "headers": {}, "allow_redirects": False})])

    async def test_the_send_time_check_is_bound_to_the_accepted_host(self):
        harness.use_internal_lane_core(BRIDGE_CORE, BRIDGE_SUBNET)
        # Whatever the module names later, a request goes to the accepted Core or nowhere.
        for later in (MAIN_CORE, LOCAL_CORE, LAN_CORE, "http://172.30.0.3:8123", "http://172.30.0.2:8124",
                      "http://" + ELSEWHERE + ":8123", BRIDGE_CORE + "/", Text(BRIDGE_CORE + "0")):
            with self.subTest(later=later):
                session = Requests()
                with patch.object(harness, "HA_URL", later):
                    with self.assertRaises(ValueError) as caught:
                        await harness._json_request(session, "GET", "/api/onboarding", token=TOKEN)
                    self.assert_fixed_code(caught.exception, "real_ha_target_not_configured_core")
                    self.assertEqual(session.calls, [])
                    # The readiness probe of the bootstrap is held to it as well.
                    with self.assertRaises(ValueError) as caught:
                        await harness.bootstrap_disposable_admin()
                    self.assert_fixed_code(caught.exception, "real_ha_target_not_configured_core")
                self.assertEqual(self.events, [])

    def test_a_second_hand_over_is_refused(self):
        harness.use_internal_lane_core(BRIDGE_CORE, BRIDGE_SUBNET)
        for origin, subnet in ((BRIDGE_CORE, BRIDGE_SUBNET), ("http://172.30.0.3:8123", BRIDGE_SUBNET),
                               (LAN_CORE, "192.168.1.0/24")):
            with self.subTest(origin=origin):
                with self.assertRaises(ValueError) as caught:
                    harness.use_internal_lane_core(origin, subnet)
                self.assert_fixed_code(caught.exception, "real_ha_lane_core_already_set")
                self.assertEqual((harness.HA_URL, harness.CLIENT_ID, harness._lane_core),
                                 (BRIDGE_CORE, BRIDGE_CORE + "/", BRIDGE_CORE))

    def test_no_core_is_handed_over_until_the_function_is_called(self):
        # The four lanes that rebind HA_URL themselves never call it, and keep the check they had.
        spec = importlib.util.spec_from_file_location("_hamcp364_harness_fresh_subject", HARNESS_PATH)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ), patch.object(sys, "path", list(sys.path)):
            os.environ.pop("REAL_HA_URL", None)
            spec.loader.exec_module(module)
        self.assertIsNone(module._lane_core)
        self.assertEqual(module.HA_URL, MAIN_CORE)
        self.assertEqual(module._LANE_CORE.pattern,
                         r"http://((?:0|[1-9][0-9]{0,2})(?:\.(?:0|[1-9][0-9]{0,2})){3}):8123")
        self.assertEqual(module._LANE_CORE.flags & ~32, 0)

    def named_in(self, name):
        """The scripts whose code names ``name``: as a name, an attribute, a definition, an import or a string."""
        found = set()
        for path in sorted(SCRIPTS.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = (
                    [node.id] if isinstance(node, ast.Name) else [node.attr] if isinstance(node, ast.Attribute)
                    else [node.name] if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    else [alias.name for alias in node.names] + [alias.asname for alias in node.names]
                    if isinstance(node, (ast.Import, ast.ImportFrom))
                    else node.names if isinstance(node, (ast.Global, ast.Nonlocal))
                    else [node.arg] if isinstance(node, (ast.arg, ast.keyword))
                    else [node.value] if isinstance(node, ast.Constant) and type(node.value) is str else [])
                if name in names:
                    found.add(path.name)
        return found

    def test_only_the_a2_runner_hands_a_core_over(self):
        # By what each script's code names; a sentence in a docstring is not a use.
        self.assertEqual(self.named_in("use_internal_lane_core"), {HARNESS_PATH.name, A2_RUNNER})
        self.assertEqual(self.named_in("_lane_core"), {HARNESS_PATH.name})
        self.assertEqual(self.named_in("_LANE_CORE"), {HARNESS_PATH.name})
        tree = ast.parse((SCRIPTS / A2_RUNNER).read_text(encoding="utf-8"))
        owners = [function.name for function in ast.walk(tree) if isinstance(function, ast.FunctionDef)
                  for node in ast.walk(function)
                  if isinstance(node, ast.Attribute) and node.attr == "use_internal_lane_core"]
        self.assertEqual(owners, ["hand_over_core"])
        function, = (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "hand_over_core")
        # The network is inspected, and the container's address read, before the harness is called.
        steps = [ast.unparse(statement) for statement in function.body[1:]]
        self.assertEqual(steps, [
            "subnet = created_network(network_id, run_id)",
            "target = address(CORE, run_id, network_id)",
            "import real_ha_contract_tests as harness",
            "origin = harness.use_internal_lane_core(f'http://{target}:{CORE_PORT}', subnet)",
            "return (origin, {'name': NETWORK, 'subnet': subnet, 'target_address': target})"])
        calls = [ast.unparse(node) for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "hand_over_core"]
        self.assertEqual(calls, ["hand_over_core(network_id, run_id)"])
        # In the harness, only the module body and the hand-over function name the Core.
        harness_tree = ast.parse(HARNESS_PATH.read_text(encoding="utf-8"))
        writers = []
        for scope in [harness_tree, *(node for node in ast.walk(harness_tree)
                                      if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))]:
            body = scope.body if scope is harness_tree else list(ast.walk(scope))
            for node in body:
                targets = node.targets if isinstance(node, ast.Assign) else ()
                names = {name.id for target in targets for name in ast.walk(target) if isinstance(name, ast.Name)}
                if names & {"HA_URL", "CLIENT_ID", "_lane_core"}:
                    writers.append((getattr(scope, "name", "module"), tuple(sorted(names))))
        self.assertEqual(writers, [("module", ("HA_URL",)), ("module", ("CLIENT_ID",)), ("module", ("_lane_core",)),
                                   ("use_internal_lane_core", ("CLIENT_ID", "HA_URL", "_lane_core"))])

    def test_the_accepted_core_is_read_only_by_the_a2_lanes_own_clients(self):
        # What the harness accepted can be read, never set, and only the A2 lane's two clients read it.
        self.assertIsNone(harness.accepted_lane_core())
        harness.use_internal_lane_core(BRIDGE_CORE, BRIDGE_SUBNET)
        self.assertEqual(harness.accepted_lane_core(), BRIDGE_CORE)
        self.assertIs(harness.accepted_lane_core(), harness._lane_core)
        tree = ast.parse(HARNESS_PATH.read_text(encoding="utf-8"))
        function, = (node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == "accepted_lane_core")
        self.assertEqual(ast.unparse(function.args), "")
        self.assertEqual([ast.unparse(statement) for statement in function.body[1:]], ["return _lane_core"])
        self.assertIsInstance(function.body[0].value, ast.Constant)
        # The harness itself never calls it: its own send-time check reads the record directly.
        self.assertEqual([node for node in ast.walk(tree) if isinstance(node, ast.Call)
                          and getattr(node.func, "id", getattr(node.func, "attr", "")) == "accepted_lane_core"], [])
        acceptance = "thread_matter_topology_a2_acceptance.py"
        self.assertEqual(self.named_in("accepted_lane_core"), {HARNESS_PATH.name, A2_RUNNER, acceptance})
        readers = {}
        for name in (A2_RUNNER, acceptance):
            script = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
            for owner in script.body:
                read = [ast.unparse(node) for node in ast.walk(owner)
                        if isinstance(node, ast.Attribute) and node.attr == "accepted_lane_core"]
                if read:
                    readers[name, getattr(owner, "name", type(owner).__name__)] = read
        self.assertEqual(readers, {(A2_RUNNER, "CoreAdmin"): ["harness.accepted_lane_core"] * 3,
                                   (acceptance, "AcceptedCore"): ["harness.accepted_lane_core"]})


class HarnessFlow(HarnessCase):
    """A config flow id is what Core issues, or it never reaches a request path.

    Core issues a ULID for a config entry flow (``ConfigEntriesFlowManager.async_init``,
    ``ulid_util.ulid_now()``): 26 characters of Crockford base32, the first one 0 to 7.
    The 32 hex digits of ``data_entry_flow.FlowManager`` are an options flow id,
    which this harness never sends.
    """

    FLOW = "01KA8ZQ5W3T9XR7N2M4HVB6CDE"
    HEX_FLOW = "0123456789abcdef0123456789abcdef"
    ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    OPEN = "/api/config/config_entries/flow"

    async def advance(self, *replies, inputs=({"slot": "a"},)):
        session = Requests(*replies)
        with patch.object(harness, "HA_URL", MAIN_CORE):
            try:
                return await harness._advance_config_flow(session, TOKEN, "synthetic_fixture", list(inputs)), session
            except Exception as error:
                error.session = session
                raise

    def outside(self):
        return ("../../auth/token", "..", "a/b", self.FLOW + "/../../../auth/token", self.FLOW + "?x=1",
                self.FLOW + "#x", self.FLOW + "\n", " " + self.FLOW, self.FLOW[:-1], self.FLOW + "0",
                self.FLOW.lower(), self.FLOW[:-1] + "g", "@" + ELSEWHERE + "/x", "%2e%2e%2f" + self.FLOW,
                # An options flow id, and its upper-case form: not what a config entry flow is given.
                self.HEX_FLOW, self.HEX_FLOW.upper(), self.HEX_FLOW[:26],
                # Letters the encoding never emits, and a first character past 7.
                "01JSYNTHETICFLOW0000000000", self.FLOW[:-1] + "I", self.FLOW[:-1] + "L",
                self.FLOW[:-1] + "O", self.FLOW[:-1] + "U", "8" + self.FLOW[1:], "Z" + self.FLOW[1:],
                "synthetic-flow", Text(self.FLOW), Claims(self.FLOW))

    async def test_a_flow_id_outside_the_closed_pattern_is_refused_before_it_is_sent(self):
        for flow_id in self.outside():
            with self.subTest(flow_id=flow_id, kind=type(flow_id).__name__):
                with self.assertRaises(ValueError) as caught:
                    await self.advance({"type": "form", "flow_id": flow_id})
                session = caught.exception.__dict__.pop("session")
                self.assert_fixed_code(caught.exception, "real_ha_flow_id_not_listed")
                self.assertNotIn(str(flow_id), str(caught.exception))
                # Only the opening request was made; the id never entered a path.
                self.assertEqual([(method, url) for method, url, _kwargs in session.calls],
                                 [("POST", MAIN_CORE + self.OPEN)])

    async def test_a_later_step_flow_id_outside_the_closed_pattern_is_refused_before_it_is_sent(self):
        # The check is made on every step's id, not only on the reply to the opening request.
        created = {"type": "create_entry", "result": {"entry_id": "synthetic-entry"}}
        listed = ({"type": "menu", "flow_id": self.FLOW}, {"type": "form", "flow_id": "7" + "Z" * 25})
        inputs = ({"next_step_id": "tools"}, {}, {})
        for flow_id in self.outside():
            for sent in (1, 2):
                with self.subTest(flow_id=flow_id, kind=type(flow_id).__name__, steps_sent=sent):
                    with self.assertRaises(ValueError) as caught:
                        await self.advance(*listed[:sent], {"type": "form", "flow_id": flow_id}, created, created,
                                           inputs=inputs)
                    session = caught.exception.__dict__.pop("session")
                    self.assert_fixed_code(caught.exception, "real_ha_flow_id_not_listed")
                    self.assertNotIn(str(flow_id), str(caught.exception))
                    # The steps before it were sent, each on its own listed id; this one never was.
                    self.assertEqual(
                        [(method, url, kwargs["json"]) for method, url, kwargs in session.calls],
                        [("POST", MAIN_CORE + self.OPEN, {"handler": "synthetic_fixture",
                                                         "show_advanced_options": False})]
                        + [("POST", MAIN_CORE + self.OPEN + "/" + reply["flow_id"], step)
                           for reply, step in zip(listed[:sent], inputs)])

    async def test_a_missing_flow_id_is_still_the_harness_failure(self):
        for flow_id in (None, "", 7, [self.FLOW]):
            with self.subTest(flow_id=flow_id):
                with self.assertRaises(RuntimeError) as caught:
                    await self.advance({"type": "form", "flow_id": flow_id})
                self.assertEqual(len(caught.exception.session.calls), 1)

    async def test_a_core_issued_flow_id_is_sent_unchanged(self):
        created = {"type": "create_entry", "result": {"entry_id": "synthetic-entry"}}
        result, session = await self.advance({"type": "form", "flow_id": self.FLOW}, created)
        self.assertEqual(result, created)
        headers = {"Authorization": "Bearer " + TOKEN}
        self.assertEqual(session.calls, [
            ("POST", MAIN_CORE + self.OPEN, {"json": {"handler": "synthetic_fixture", "show_advanced_options": False},
                                             "data": None, "headers": headers, "allow_redirects": False}),
            ("POST", MAIN_CORE + self.OPEN + "/" + self.FLOW, {"json": {"slot": "a"}, "data": None,
                                                              "headers": headers, "allow_redirects": False})])
        # A menu step and then a form step, each on the id Core gave for it.
        other = "7" + "Z" * 25
        result, session = await self.advance({"type": "menu", "flow_id": self.FLOW},
                                             {"type": "form", "flow_id": other}, created,
                                             inputs=({"next_step_id": "tools"}, {}))
        self.assertEqual(result, created)
        self.assertEqual([url for _method, url, _kwargs in session.calls],
                         [MAIN_CORE + self.OPEN, MAIN_CORE + self.OPEN + "/" + self.FLOW,
                          MAIN_CORE + self.OPEN + "/" + other])

    def test_the_pattern_is_the_ulid_of_a_core_config_flow_id(self):
        self.assertEqual(harness._FLOW_ID.pattern, "[0-7][0-9A-HJKMNP-TV-Z]{25}")
        self.assertEqual(harness._FLOW_ID.flags & ~32, 0)
        # The seq15 lane's own pattern names an options flow id, which is 32 hex digits.
        self.assertEqual(seq15._FLOW_ID.pattern, r"/[0-9a-f]{32}\Z")

    def test_the_pattern_admits_exactly_what_the_ulid_encoding_emits(self):
        # 26 characters; the first carries the top three bits of the timestamp, the rest five bits each.
        self.assertEqual((len(self.ALPHABET), len(set(self.ALPHABET)), len(self.FLOW)), (32, 32, 26))
        printable = [chr(code) for code in range(32, 127)] + ["\n", "\x00", "\u00c5", "\uff10"]
        for position in range(26):
            allowed = self.ALPHABET[:8] if position == 0 else self.ALPHABET
            admitted = {character for character in printable if harness._FLOW_ID.fullmatch(
                self.FLOW[:position] + character + self.FLOW[position + 1:]) is not None}
            self.assertEqual(admitted, set(allowed), position)
        for length in (0, 1, 25, 27, 32, 52):
            self.assertIsNone(harness._FLOW_ID.fullmatch("0" * length), length)
        for flow_id in ("0" * 26, "7" + "Z" * 25, self.FLOW):
            self.assertIsNotNone(harness._FLOW_ID.fullmatch(flow_id))

    async def test_every_step_of_the_migration_fixture_flows_is_sent_on_a_ulid(self):
        # The three flows prepare_migration_fixture walks, each id a ULID as Core 2026.7.2 gives it.
        created = {"type": "create_entry", "result": {"entry_id": "synthetic-entry"}}
        first, second = self.FLOW, "0" + self.ALPHABET[:25]
        for handler, replies, inputs in (
                (harness.FIXTURE_PLATFORM, [{"type": "form", "flow_id": first}], [{"slot": "a"}]),
                (harness.FIXTURE_PLATFORM, [{"type": "form", "flow_id": second}], [{"slot": "b"}]),
                ("ha_mcp_tools", [{"type": "menu", "flow_id": first}, {"type": "form", "flow_id": first}],
                 [{"next_step_id": "tools"}, {}])):
            with self.subTest(handler=handler, inputs=inputs):
                session = Requests(*replies, created)
                with patch.object(harness, "HA_URL", MAIN_CORE):
                    result = await harness._advance_config_flow(session, TOKEN, handler, inputs)
                self.assertEqual(result, created)
                self.assertEqual([url for _method, url, _kwargs in session.calls],
                                 [MAIN_CORE + self.OPEN] + [MAIN_CORE + self.OPEN + "/" + reply["flow_id"]
                                                            for reply in replies])
                self.assertEqual([kwargs["json"] for _method, _url, kwargs in session.calls[1:]], inputs)


# The A2 stub's control origin, as its runner reads it: the stub's address on the same bridge.
BRIDGE_STUB_ORIGIN = "http://172.30.0.3:5580"


class OtherLaneOrigin(ClientCase):
    """Neither Thread/Matter lane can be pointed at the other lane's Core.

    A1's Core is on loopback. A2's is the bare ``http://<IPv4>:8123`` its runner
    handed over from inside the internal subnet it verified, which ``setUp`` has
    done here. So both origins exist in the one process, and each lane refuses
    the other's before a session exists.
    """

    def setUp(self):
        super().setUp()
        self.events = []
        manager = patch.object(aiohttp, "ClientSession", new=fake_session(self.events))
        manager.start()
        self.addCleanup(manager.stop)

    def dialled(self, events):
        return [event[1] for event in events if event[0] == "ws_connect"]

    async def test_the_a1_lane_refuses_the_a2_lanes_internal_network_origin(self):
        first = BY_NAME["thread_matter"]
        # A2's origin as its runner hands it over: verified inside the internal subnet, and accepted.
        self.assertEqual((harness.accepted_lane_core(), harness.HA_URL), (BRIDGE_CORE, BRIDGE_CORE))
        self.assertEqual(a2.A2RawCore.core_url, BRIDGE_SOCKET)
        # The A1 client is not built for it, as a socket or as the origin, while the harness holds it.
        for url in (BRIDGE_SOCKET, a2.A2RawCore.core_url, BRIDGE_CORE, BRIDGE_CORE + "/api/websocket"):
            for api_url in (THREAD_CORE + "/api", BRIDGE_CORE + "/api"):
                with self.subTest(url=url, api_url=api_url):
                    with self.assertRaises(ValueError) as caught:
                        thread.RawCore(configured(url, api_url))
                    self.assert_fixed_code(caught.exception, URL_REFUSED)
        # Its pin did not follow the hand-over: it is still the constant that names loopback.
        self.assertIs(thread.RawCore.core_url, thread.RAW_CORE_URL)
        self.assertEqual(thread.RAW_CORE_URL, THREAD_SOCKET)
        # The harness on the A1 side takes its Core from REAL_HA_URL, which refuses that origin as well.
        for value in (BRIDGE_CORE, BRIDGE_CORE + "/"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as caught:
                    harness.loopback_core_url(value)
                self.assert_fixed_code(caught.exception, "real_ha_url_not_loopback")
        # Every refusal above came before anything was opened.
        self.assertEqual(self.events, [])
        # What A1 does send goes to loopback, even through an instance told to dial the bridge.
        client = thread.RawCore(configured(THREAD_SOCKET))
        client.core_url = BRIDGE_SOCKET
        reply, events = await self.sent(first, dict(TOPOLOGY_READ), client=client)
        self.assertIs(reply["success"], True)
        self.assertEqual(self.dialled(events), [THREAD_SOCKET])
        self.assertNotIn("172.30.", repr(events))

    async def test_the_a2_lane_refuses_the_a1_lanes_loopback_origin(self):
        second = BY_NAME["thread_matter_a2"]
        self.assertEqual(harness.accepted_lane_core(), BRIDGE_CORE)
        # The websocket client: A1's own socket, and every other loopback one, is not A2's pin.
        for url in (THREAD_SOCKET, thread.RAW_CORE_URL, THREAD_CORE, MAIN_SOCKET, LOCAL_SOCKET,
                    "ws://127.30.0.2:8123/api/websocket", "ws://[::1]:8123/api/websocket"):
            for api_url in (BRIDGE_CORE + "/api", THREAD_CORE + "/api"):
                with self.subTest(url=url, api_url=api_url):
                    with self.assertRaises(ValueError) as caught:
                        a2.A2RawCore(configured(url, api_url))
                    self.assert_fixed_code(caught.exception, URL_REFUSED)
        # The hand-over: no loopback Core is accepted, so the pin can never become A1's.
        with patch.multiple(harness, HA_URL=MAIN_CORE, CLIENT_ID=MAIN_CORE + "/", _lane_core=None):
            for origin, subnet, reason in (
                    # A1's own origin: not the port a handed-over Core has, whatever the subnet.
                    (THREAD_CORE, BRIDGE_SUBNET, "real_ha_lane_core_not_bare_ipv4"),
                    (THREAD_CORE, "127.0.0.0/16", "real_ha_lane_core_not_bare_ipv4"),
                    ("http://[::1]:8123", "127.0.0.0/16", "real_ha_lane_core_not_bare_ipv4"),
                    # Loopback on the handed-over port: outside the bridge's subnet,
                    (MAIN_CORE, BRIDGE_SUBNET, "real_ha_lane_core_outside_subnet"),
                    # and a loopback subnet is not one a created network has.
                    (MAIN_CORE, "127.0.0.0/16", "real_ha_lane_subnet_not_usable"),
                    (MAIN_CORE, "127.0.0.0/24", "real_ha_lane_subnet_not_usable"),
                    (MAIN_CORE, "127.0.0.0/30", "real_ha_lane_subnet_not_usable"),
                    ("http://127.30.0.2:8123", "127.30.0.0/16", "real_ha_lane_subnet_not_usable"),
                    ("http://127.255.255.254:8123", "127.255.0.0/16", "real_ha_lane_subnet_not_usable")):
                with self.subTest(origin=origin, subnet=subnet):
                    with self.assertRaises(ValueError) as caught:
                        harness.use_internal_lane_core(origin, subnet)
                    self.assert_fixed_code(caught.exception, reason)
                    self.assertEqual((harness.HA_URL, harness.CLIENT_ID, harness._lane_core),
                                     (MAIN_CORE, MAIN_CORE + "/", None))
                    # With no Core accepted the client has no socket at all, A1's included.
                    with self.assertRaises(ValueError) as caught:
                        a2.A2RawCore(configured(THREAD_SOCKET))
                    self.assert_fixed_code(caught.exception, URL_REFUSED)
        # The runner's REST client: loopback is neither its Core nor its stub, when built ...
        for core, stub in ((THREAD_CORE, BRIDGE_STUB_ORIGIN), (MAIN_CORE, BRIDGE_STUB_ORIGIN),
                           (LOCAL_CORE, BRIDGE_STUB_ORIGIN), (BRIDGE_CORE, THREAD_CORE), (BRIDGE_CORE, MAIN_CORE),
                           (BRIDGE_CORE, LOCAL_CORE), (BRIDGE_CORE, "http://127.0.0.1:5580"),
                           (BRIDGE_CORE, "http://127.30.0.3:5580"), (BRIDGE_CORE, "http://0.0.0.0:5580"),
                           (BRIDGE_CORE, "http://[::1]:5580"), (BRIDGE_CORE, "http://localhost:5580")):
            with self.subTest(core=core, stub=stub):
                with self.assertRaises(a2_runner.Refusal) as raised:
                    a2_runner.CoreAdmin(core, TOKEN, stub)
                self.assertEqual(raised.exception.args, (a2_runner.ADMIN_TARGET_REFUSED,))
        # ... and when it sends, should either origin have been changed since.
        for name, value, send in (
                ("ha_url", THREAD_CORE, lambda admin: admin.core("GET", "/api/config")),
                ("ha_url", MAIN_CORE, lambda admin: admin.core("GET", "/api/config")),
                ("stub_url", THREAD_CORE, lambda admin: admin.stub_state()),
                ("stub_url", MAIN_CORE, lambda admin: admin.stub_control({})),
                ("stub_url", "http://127.0.0.1:5580", lambda admin: admin.stub_state()),
                ("stub_url", "http://0.0.0.0:5580", lambda admin: admin.stub_control({}))):
            with self.subTest(name=name, value=value):
                admin = a2_runner.CoreAdmin(BRIDGE_CORE, TOKEN, BRIDGE_STUB_ORIGIN)
                setattr(admin, name, value)
                with self.assertRaises(a2_runner.Refusal) as raised:
                    await send(admin)
                self.assertEqual(raised.exception.args, (a2_runner.ADMIN_TARGET_REFUSED,))
        # The harness's own requests stay on the accepted Core when the module later names loopback.
        for later in (THREAD_CORE, MAIN_CORE, LOCAL_CORE):
            with self.subTest(later=later), patch.object(harness, "HA_URL", later):
                session = Requests()
                with self.assertRaises(ValueError) as caught:
                    await harness._json_request(session, "GET", "/api/onboarding", token=TOKEN)
                self.assertEqual(caught.exception.args, ("real_ha_target_not_configured_core",))
                self.assertEqual(session.calls, [])
        # Every refusal above came before anything was opened.
        self.assertEqual(self.events, [])
        # What A2 does send goes to the accepted Core on the bridge, and nothing to loopback.
        reply, events = await self.sent(second, dict(TOPOLOGY_READ))
        self.assertIs(reply["success"], True)
        self.assertEqual(self.dialled(events), [BRIDGE_SOCKET])
        self.assertNotIn("127.", repr(events))


# The device-configuration lane. Its clients keep the refusal codes and the settings type of
# their own lane, so they have a table of their own here and not a row of ``LANES``.
DEVICE_CORE, DEVICE_SOCKET = "http://127.0.0.1:18297", "ws://127.0.0.1:18297/api/websocket"
DEVICE_UPSTREAM, DEVICE_STUB = "http://127.0.0.1:18298/beta23-real-ha-mcp", "http://127.0.0.1:18299/_lane/control"
DEVICE_FLOW = "/api/config/config_entries/flow"
DEVICE_RELOAD = "/api/config/config_entries/entry/01JSYNTHETICSYNTHETICSYNTH/reload"
DEVICE_COMMAND = {"type": "config_entries/get", "domain": "matter"}
DEVICE_BEARER = {"Authorization": "Bearer " + TOKEN}
REDIRECT_REFUSED = "lane_redirect_refused"


def device_settings(core=DEVICE_CORE, upstream=DEVICE_UPSTREAM):
    return Settings(ha_url=core, ha_token=TOKEN, access_secret="synthetic-secret", port=8100, audit_path="unused",
                    rate_limit_per_minute=120, rate_limit_burst=25, destructive_services=frozenset(),
                    upstream_dashboard_mcp_url=upstream)


# client: the function of the lane script that opens its session, one listed send, the seconds
# of that session, the session call as the script writes it, and the one thing the send dials.
DEVICE_CLIENTS = {
    "raw_core": ("RawCore.command", lambda: device.RawCore(device_settings()).command(dict(DEVICE_COMMAND)), 60,
                 "lane_session(timeout=aiohttp.ClientTimeout(total=seconds))",
                 ("ws_connect", DEVICE_SOCKET, {"max_msg_size": 8 * 1024 * 1024})),
    "raw_rest": ("RawRest.rest", lambda: device.RawRest(device_settings()).rest("POST", DEVICE_RELOAD), 60,
                 "lane_session(timeout=aiohttp.ClientTimeout(total=seconds))",
                 ("request", "POST", DEVICE_CORE + DEVICE_RELOAD, {"headers": DEVICE_BEARER, "allow_redirects": False})),
    "stub_control": ("StubControl._request", lambda: device.StubControl(DEVICE_STUB).read(), 30,
                     "lane_session(timeout=aiohttp.ClientTimeout(total=30))",
                     ("request", "GET", DEVICE_STUB, {"allow_redirects": False})),
}
# Every other request the two REST clients send, each to the same pin.
DEVICE_REQUESTS = (
    (lambda: device.RawRest(device_settings()).rest("POST", DEVICE_FLOW, {"handler": "matter"}),
     ("request", "POST", DEVICE_CORE + DEVICE_FLOW,
      {"headers": DEVICE_BEARER, "allow_redirects": False, "json": {"handler": "matter"}})),
    (lambda: device.StubControl(DEVICE_STUB).post({"mode": "none"}),
     ("request", "POST", DEVICE_STUB, {"allow_redirects": False, "json": {"mode": "none"}})),
)


def device_session(events, status=200, value=None, during=lambda stage: None):
    """Stand-in for aiohttp.ClientSession with a REST answer: records construction, dials and reads.

    ``during`` is told each step before the request or the command frame, as
    ``fake_session`` tells it.
    """
    class Answer:
        def __init__(self):
            self.status = status

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def json(self, **kwargs):
            events.append(("json", kwargs))
            return value

    class Session(fake_session(events, during=during)):
        def request(self, method, url, **kwargs):
            events.append(("request", method, url, kwargs))
            return Answer()

    return Session


class DeviceConfigurationClients(unittest.IsolatedAsyncioTestCase):
    """The device-configuration lane's three clients: the shared lane session, to the lane's pins only."""

    def test_the_table_is_every_function_of_the_lane_script_that_opens_a_session(self):
        tree = ast.parse((SCRIPTS / "device_configuration_contract_acceptance.py").read_text(encoding="utf-8"))
        opened = []
        for owner in tree.body:
            for function in ast.walk(owner):
                if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    opened += [(owner.name if owner is function else f"{owner.name}.{function.name}", ast.unparse(node))
                               for node in ast.walk(function) if isinstance(node, ast.Call)
                               and "lane_session" in ast.unparse(node.func)]
        self.assertEqual(sorted(opened), sorted((where, written)
                                                for where, _send, _seconds, written, _dial in DEVICE_CLIENTS.values()))
        # The session is used where it is opened, by the name ``session``, and nowhere else.
        held = [ast.unparse(node.context_expr) + " as " + ast.unparse(node.optional_vars)
                for node in ast.walk(tree) if isinstance(node, ast.withitem) and "lane_session" in ast.unparse(node)]
        self.assertEqual(sorted(held), sorted(written + " as session"
                                              for _where, _send, _seconds, written, _dial in DEVICE_CLIENTS.values()))
        # The pins and the frame bound of the table are the script's own.
        self.assertEqual((device.CORE_URL, device.CORE_WS_URL, device.UPSTREAM_URL, device.STUB_CONTROL_URL),
                         (DEVICE_CORE, DEVICE_SOCKET, DEVICE_UPSTREAM, DEVICE_STUB))
        self.assertEqual((device.CORE_PORT, device.UPSTREAM_PORT, device.STUB_PORT, device.FRAME_BYTES),
                         (18297, 18298, 18299, 8 * 1024 * 1024))

    async def test_each_client_opens_one_shared_lane_session_and_dials_its_pin(self):
        proxies = {name: "http://proxy.invalid:3128" for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                                                                  "WS_PROXY", "WSS_PROXY", "NO_PROXY")}
        for name, (_where, send, seconds, _written, dial) in DEVICE_CLIENTS.items():
            for environment in ({}, proxies):
                with self.subTest(client=name, proxies=bool(environment)), patch.dict(os.environ, environment):
                    events = []
                    with patch.object(aiohttp, "ClientSession", new=device_session(events, value=["answer"])):
                        await send()
                    sessions = [event[1] for event in events if event[0] == "session"]
                    self.assertEqual(len(sessions), 1)
                    self.assertEqual(set(sessions[0]), {"timeout", "trust_env", "trace_configs"})
                    self.assertIs(sessions[0]["trust_env"], False)
                    self.assertEqual(sessions[0]["timeout"], aiohttp.ClientTimeout(total=seconds))
                    trace, = sessions[0]["trace_configs"]
                    self.assertIs(type(trace), aiohttp.TraceConfig)
                    # The one redirect callback is the shared helper's, which raises the shared refusal.
                    self.assertEqual(list(trace.on_request_redirect), [shared._refuse_redirect_hop])
                    self.assertEqual([event for event in events if event[0] in ("ws_connect", "request")], [dial])
                    self.assertEqual(events[0][0], "session")
                    self.assertNotIn(ELSEWHERE, repr(events))

    async def test_every_other_request_of_the_rest_clients_goes_to_the_same_pin(self):
        for number, (send, dial) in enumerate(DEVICE_REQUESTS):
            with self.subTest(request=number):
                events = []
                with patch.object(aiohttp, "ClientSession", new=device_session(events, value={"flow_id": HEX})):
                    await send()
                self.assertEqual([event[0] for event in events], ["session", "request", "json"])
                self.assertEqual(events[1], dial)

    async def test_each_client_asks_the_shared_helper_for_its_session_and_builds_none_itself(self):
        self.assertIs(device.lane_session, shared.lane_session)
        self.assertIs(device.refuse_redirect, shared.refuse_redirect)
        for name, (_where, send, seconds, _written, dial) in DEVICE_CLIENTS.items():
            with self.subTest(client=name):
                asked, events = [], []

                def helper(*args, **kwargs):
                    asked.append((args, kwargs))
                    return device_session(events, value=["answer"])()

                def built(*_args, **_kwargs):
                    raise AssertionError("the lane built a session of its own")

                with patch.object(device, "lane_session", helper), patch.object(aiohttp, "ClientSession", built):
                    await send()
                # One session, asked of the helper by keyword, with nothing but the timeout.
                self.assertEqual(asked, [((), {"timeout": aiohttp.ClientTimeout(total=seconds)})])
                self.assertEqual([event for event in events if event[0] in ("ws_connect", "request")], [dial])

    async def test_a_redirect_status_is_the_shared_refusal_before_the_answer_is_read(self):
        rest = {name: row for name, row in DEVICE_CLIENTS.items() if row[4][0] == "request"}
        self.assertEqual(sorted(rest), ["raw_rest", "stub_control"])
        for name, (_where, send, _seconds, _written, dial) in rest.items():
            for status in (300, 301, 302, 303, 304, 305, 307, 308, 399):
                with self.subTest(client=name, status=status):
                    events = []
                    with patch.object(aiohttp, "ClientSession", new=device_session(events, status, ["answer"])):
                        with self.assertRaises(Exception) as caught:
                            await send()
                    self.assertIs(type(caught.exception), shared.LaneRedirectRefused)
                    self.assertEqual(caught.exception.args, (REDIRECT_REFUSED,))
                    self.assertEqual(caught.exception.__dict__, {})
                    # Declined on the request itself, and refused before the body is read.
                    self.assertEqual([event[0] for event in events], ["session", "request"])
                    self.assertEqual(events[1], dial)
                    self.assertIs(events[1][3]["allow_redirects"], False)
        # Either side of the range keeps the meaning it had: a Core status is handed
        # back with no body, and the stub's is the lane's own failure.
        for status in (299, 400):
            with self.subTest(status=status):
                events = []
                with patch.object(aiohttp, "ClientSession", new=device_session(events, status, ["answer"])):
                    self.assertEqual(await rest["raw_rest"][1](), (status, None))
                    with self.assertRaises(AssertionError):
                        await rest["stub_control"][1]()
                self.assertEqual([event[0] for event in events], ["session", "request", "session", "request"])
        events = []
        with patch.object(aiohttp, "ClientSession", new=device_session(events, 200, ["answer"])):
            self.assertEqual(await rest["raw_rest"][1](), (200, ["answer"]))
            self.assertEqual(await rest["stub_control"][1](), ["answer"])
        self.assertEqual([event for event in events if event[0] == "json"], [("json", {"content_type": None})] * 2)

    async def test_the_shared_refusal_ends_the_stub_wait_at_once(self):
        events = []

        async def waited(_seconds):
            raise AssertionError("the wait asked again")

        with patch.object(aiohttp, "ClientSession", new=device_session(events, 302)), \
                patch.object(device.asyncio, "sleep", waited):
            with self.assertRaises(shared.LaneRedirectRefused) as caught:
                await device.stub_ready(DEVICE_STUB, seconds=30)
        self.assertEqual(caught.exception.args, (REDIRECT_REFUSED,))
        self.assertEqual([event[0] for event in events], ["session", "request"])
        # It is not one of the errors the wait retries on.
        self.assertFalse(issubclass(shared.LaneRedirectRefused, (aiohttp.ClientError, TimeoutError, AssertionError)))

    async def test_what_the_lane_refuses_before_a_session_is_refused_as_before(self):
        events = []
        refusals = (
            ("core_url_not_pinned", lambda: device.RawCore(device_settings(MAIN_CORE))),
            ("core_url_not_pinned", lambda: device.RawRest(device_settings(f"http://{ELSEWHERE}:18297"))),
            ("core_url_not_pinned", lambda: device.RawRest(device_settings(DEVICE_CORE + "/"))),
            ("stub_url_not_pinned", lambda: device.StubControl(DEVICE_STUB + "/")),
            ("stub_url_not_pinned", lambda: device.StubControl(f"http://{ELSEWHERE}:18299/_lane/control")),
            ("stub_url_not_pinned", lambda: device.stub_ready(DEVICE_CORE)),
            ("core_command_not_listed", lambda: device.RawCore(device_settings()).command(dict(UNLISTED))),
            ("core_command_not_listed",
             lambda: device.RawCore(device_settings()).command({"type": "config_entries/get", "domain": "thread"})),
            ("core_rest_request_not_listed", lambda: device.RawRest(device_settings()).rest("GET", DEVICE_FLOW)),
            ("core_rest_request_not_listed",
             lambda: device.RawRest(device_settings()).rest("POST", DEVICE_RELOAD + "/../..")),
            ("core_rest_request_not_listed",
             lambda: device.RawRest(device_settings()).rest("POST", f"//{ELSEWHERE}{DEVICE_FLOW}",
                                                            {"handler": "matter"})),
            ("stub_request_not_listed", lambda: device.StubControl(DEVICE_STUB).post({"mode": "open"})),
        )
        with patch.object(aiohttp, "ClientSession", new=device_session(events)):
            for number, (code, attempt) in enumerate(refusals):
                with self.subTest(refusal=number, code=code):
                    with self.assertRaises(ValueError) as caught:
                        made = attempt()
                        if hasattr(made, "__await__"):
                            await made
                    self.assertIs(type(caught.exception), ValueError)
                    self.assertEqual(caught.exception.args, (code,))
        # None of them opened a session, so nothing was dialled.
        self.assertEqual(events, [])


# Every step a REST client takes between its check and its request.
REST_STAGES = STAGES[:2]
DEVICE_UPDATE = {"type": "config/entity_registry/update", "entity_id": "number.hamcp366_synthetic",
                 "options_domain": "number", "options": {"unit_of_measurement": "\N{DEGREE SIGN}F"}}
DEVICE_CHECKED_COMMANDS = (DEVICE_COMMAND, {"type": "config/entity_registry/get", "entity_id": "select.synthetic"},
                           DEVICE_UPDATE)
DEVICE_CHECKED_BODIES = ((DEVICE_FLOW, {"handler": "matter"}),
                         (DEVICE_FLOW + "/01JSYNTHETICSYNTHETICSYNTH", {"url": device.MATTER_URL}))
DEVICE_CHECKED_POSTS = ({"mode": "none"}, {"mode": "delay", "seconds": 5},
                        {"action": "drift", "node_id": device.SELECT_NODE, "attribute_path": device.SELECT_PATH,
                         "value": 1})


class DeviceCheckedRequest(unittest.IsolatedAsyncioTestCase):
    """What a device-configuration client sends is what it checked, whatever the caller does to it meanwhile."""

    async def sent_while(self, stages, stage, change, held, send):
        """One send, with ``change`` applied to what the caller handed over once the client reaches ``stage``."""
        seen, events = [], []

        def during(now):
            seen.append(now)
            if now == stage:
                change(held)

        with patch.object(aiohttp, "ClientSession", new=device_session(events, value=["answer"], during=during)):
            await send(held)
        self.assertEqual(tuple(seen), stages)
        return events

    def test_the_examples_are_listed_shapes(self):
        for example in DEVICE_CHECKED_COMMANDS:
            self.assertTrue(device.listed(example, device.RAW_COMMANDS), example)
        for path, example in DEVICE_CHECKED_BODIES:
            self.assertTrue(device.listed({"method": "POST", "path": path, "body": example}, device.RAW_REST), path)
        for example in DEVICE_CHECKED_POSTS:
            self.assertTrue(device.listed(example, device.STUB_POSTS), example)
        # The update is the one command with a mapping inside it.
        self.assertEqual([example for example in DEVICE_CHECKED_COMMANDS
                          if any(type(value) is dict for value in example.values())], [DEVICE_UPDATE])

    async def test_a_command_changed_while_the_socket_connects_is_not_the_frame_sent(self):
        client = device.RawCore(device_settings())
        for example in DEVICE_CHECKED_COMMANDS:
            changes = (replaced, retyped, widened) + ((nested,) if example is DEVICE_UPDATE else ())
            for stage in STAGES:
                for change in changes:
                    with self.subTest(payload=example["type"], stage=stage, change=change.__name__):
                        payload = copy.deepcopy(example)
                        events = await self.sent_while(STAGES, stage, change, payload, client.command)
                        # The caller's payload really was changed, into something the list does not hold.
                        self.assertNotEqual(payload, example)
                        self.assertFalse(device.listed(payload, device.RAW_COMMANDS))
                        self.assertEqual([event[1] for event in events if event[0] == "send"],
                                         [{"type": "auth", "access_token": TOKEN}, {"id": 1, **example}])

    async def test_a_body_changed_while_the_session_opens_is_not_the_body_sent_to_core(self):
        client = device.RawRest(device_settings())
        for path, example in DEVICE_CHECKED_BODIES:
            for stage in REST_STAGES:
                for change in (replaced, widened):
                    with self.subTest(body=sorted(example), stage=stage, change=change.__name__):
                        body = copy.deepcopy(example)
                        events = await self.sent_while(REST_STAGES, stage, change, body,
                                                       lambda held, path=path: client.rest("POST", path, held))
                        self.assertNotEqual(body, example)
                        self.assertFalse(device.listed({"method": "POST", "path": path, "body": body},
                                                       device.RAW_REST))
                        self.assertEqual([event for event in events if event[0] == "request"],
                                         [("request", "POST", DEVICE_CORE + path,
                                           {"headers": DEVICE_BEARER, "allow_redirects": False, "json": example})])

    async def test_a_body_changed_while_the_session_opens_is_not_the_body_sent_to_the_stub(self):
        client = device.StubControl(DEVICE_STUB)
        for example in DEVICE_CHECKED_POSTS:
            for stage in REST_STAGES:
                for change in (replaced, widened):
                    with self.subTest(body=sorted(example), stage=stage, change=change.__name__):
                        body = copy.deepcopy(example)
                        events = await self.sent_while(REST_STAGES, stage, change, body, client.post)
                        self.assertNotEqual(body, example)
                        self.assertFalse(device.listed(body, device.STUB_POSTS))
                        self.assertEqual([event for event in events if event[0] == "request"],
                                         [("request", "POST", DEVICE_STUB,
                                           {"allow_redirects": False, "json": example})])

    async def test_what_is_sent_shares_nothing_with_what_the_caller_handed_over(self):
        # The copy is the client's own, and the caller's object is left as it was.
        payload, events = copy.deepcopy(DEVICE_UPDATE), []
        with patch.object(aiohttp, "ClientSession", new=device_session(events)):
            await device.RawCore(device_settings()).command(payload)
        frame = [event[1] for event in events if event[0] == "send"][1]
        self.assertEqual((payload, frame), (DEVICE_UPDATE, {"id": 1, **DEVICE_UPDATE}))
        self.assertIsNot(frame["options"], payload["options"])
        core = device.RawRest(device_settings())
        sends = ((lambda held: core.rest("POST", DEVICE_FLOW, held), {"handler": "matter"}),
                 (device.StubControl(DEVICE_STUB).post, DEVICE_CHECKED_POSTS[2]))
        for number, (send, example) in enumerate(sends):
            with self.subTest(send=number):
                body, events = copy.deepcopy(example), []
                with patch.object(aiohttp, "ClientSession", new=device_session(events, value=["answer"])):
                    await send(body)
                request, = [event for event in events if event[0] == "request"]
                self.assertEqual((body, request[3]["json"]), (example, example))
                self.assertIsNot(request[3]["json"], body)


if __name__ == "__main__":
    unittest.main()
