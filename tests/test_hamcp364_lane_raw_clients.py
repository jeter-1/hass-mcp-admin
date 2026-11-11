"""HAMCP-364: the acceptance lanes' own clients reach only a disposable loopback Core.

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
import entity_metadata_contract_acceptance as entity  # noqa: E402
import real_ha_contract_tests as harness  # noqa: E402
import security_metadata_contract_acceptance as security  # noqa: E402
import seq13_contract_acceptance as seq13  # noqa: E402
import seq15_contract_acceptance as seq15  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from support.process_isolation import restore_after_class  # noqa: E402

SCRIPTS = ROOT / "scripts"
HARNESS_PATH = SCRIPTS / "real_ha_contract_tests.py"
MAIN_CORE, MAIN_SOCKET = "http://127.0.0.1:8123", "ws://127.0.0.1:8123/api/websocket"
LOCAL_CORE, LOCAL_SOCKET = "http://127.0.0.1:18123", "ws://127.0.0.1:18123/api/websocket"
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


def configured(url, **more):
    return SimpleNamespace(websocket_url=url, api_url=MAIN_CORE + "/api", ha_token=TOKEN, **more)


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


def lane(name, module, client, send, pin, examples, near, sources, methods, seconds, signatures):
    return SimpleNamespace(name=name, module=module, client=client, send=send, pin=pin, examples=examples,
                           near=near, sources=sources, methods=methods, seconds=seconds, signatures=signatures)


def call(client, payload):
    return client.call(payload)


def command(client, payload):
    return client.command(payload)


def security_signatures():
    return {(kind, frozenset({"type", *fields})) for kind, fields in security.RAW_COMMANDS.items()}


SOCKET_METHODS = frozenset({"ok", "call", "refused"})
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
    lane("security_metadata", security, security.RawCore, command, LOCAL_SOCKET, SECURITY_EXAMPLES, SECURITY_NEAR,
         (("security_metadata_contract_acceptance.py", security),), frozenset({"result", "command"}), 60,
         security_signatures),
)
SHAPED = tuple(item for item in LANES if item.module is not security)


class ClientCase(unittest.IsolatedAsyncioTestCase):
    def assert_fixed_code(self, exception, reason):
        # A fixed code and nothing else: no URL, token or payload text.
        self.assertIs(type(exception), ValueError)
        self.assertEqual(exception.args, (reason,))
        self.assertIsNone(exception.__cause__)
        self.assertIsNone(exception.__context__)
        self.assertEqual(exception.__dict__, {})

    def refused_url(self, item, url):
        events = []
        with patch.object(aiohttp, "ClientSession", new=fake_session(events)):
            with self.assertRaises(ValueError) as caught:
                item.client(configured(url))
        self.assert_fixed_code(caught.exception, URL_REFUSED)
        self.assertEqual(events, [])

    async def refused_command(self, item, payload, *, prepare=None):
        events = []
        with patch.object(aiohttp, "ClientSession", new=fake_session(events)):
            client = item.client(configured(item.pin))
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
            client = item.client(configured(item.pin)) if client is None else client
            if after is not None:
                after()
            reply = await item.send(client, payload)
        return reply, events


class PinnedUrl(ClientCase):
    """Each client is built only for its lane's one loopback Core socket."""

    def cases(self, pin):
        port = pin.split(":")[2].split("/")[0]
        other = {"8123": "18123", "18123": "8123"}[port]
        origin = "ws://127.0.0.1:" + port
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
            pin + "@" + ELSEWHERE_NAME, "ws://user:synthetic@127.0.0.1:" + port + "/api/websocket",
            "ws://@127.0.0.1:" + port + "/api/websocket",
            # Another host, including names and forms that only resolve to loopback.
            pin.replace("127.0.0.1", "localhost"), pin.replace("127.0.0.1", "[::1]"),
            pin.replace("127.0.0.1", "127.0.0.2"), pin.replace("127.0.0.1", "127.1"),
            pin.replace("127.0.0.1", "2130706433"), pin.replace("127.0.0.1", "0.0.0.0"),
            pin.replace("127.0.0.1", "127.0.0.1." + ELSEWHERE_NAME), pin.replace("127.0.0.1", ELSEWHERE),
            pin.replace("127.0.0.1", ELSEWHERE_NAME), "ws://supervisor/core/websocket",
            # Not exactly the text, or not text at all.
            " " + pin, pin + " ", pin + "\n", pin + "\x00", pin.upper(), "", None, 0, True,
            pin.encode(), (pin,), [pin], {"url": pin},
            # A str subclass, whether it merely equals the pin or only claims to.
            Text(pin), Claims(pin), Claims(pin.replace("127.0.0.1", ELSEWHERE)))

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
                    client = item.client(configured(item.pin))
                self.assertEqual(events, [])
                # The checked value is not kept for a later dial.
                self.assertNotIn(item.pin, [value for value in vars(client).values() if type(value) is str])

    def test_refusal_carries_neither_the_url_nor_the_token(self):
        private = "ws://127.0.0.1:8123@" + ELSEWHERE_NAME + "/api/websocket?k=synthetic-query"
        for item in LANES:
            with self.subTest(lane=item.name):
                with patch.object(aiohttp, "ClientSession", new=fake_session([])):
                    with self.assertRaises(ValueError) as caught:
                        item.client(configured(private))
                shown = str(caught.exception) + repr(caught.exception)
                self.assertEqual(str(caught.exception), URL_REFUSED)
                for text in (private, TOKEN, ELSEWHERE_NAME, "synthetic-query", "127.0.0.1", "ws://"):
                    self.assertNotIn(text, shown)

    def test_the_pins_are_the_two_disposable_loopback_sockets(self):
        self.assertEqual(entity.RAW_CORE_URL, MAIN_SOCKET)
        self.assertEqual(security.RAW_CORE_URL, LOCAL_SOCKET)
        for item in LANES:
            with self.subTest(lane=item.name):
                # One constant per family: a lane subclass does not carry its own target.
                self.assertNotIn("RAW_CORE_URL", vars(item.client))
                if item.module is not security:
                    self.assertTrue(issubclass(item.client, entity.RawWebSocket))


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
                self.assertIs(item.client.commands, item.module.RAW_COMMANDS)
                await self.refused_command(
                    item, unlisted, prepare=lambda client: setattr(client, "commands", (unlisted,)))

    async def test_one_lane_does_not_pass_another_lanes_commands(self):
        # The shared client's own list is entity_metadata's; each subclass names
        # its own, so no lane inherits a command it never sends.
        registry_list, hub = {"type": "config/entity_registry/list"}, ENTITY_EXAMPLES[2]
        by_name = {item.name: item for item in LANES}
        await self.refused_command(by_name["entity_metadata"], registry_list)
        for name in ("configuration_delete", "seq13", "seq15", "security_metadata"):
            with self.subTest(lane=name):
                reply, _events = await self.sent(by_name[name], registry_list)
                self.assertIs(reply["success"], True)
                await self.refused_command(by_name[name], hub)
        await self.refused_command(by_name["seq13"], SELECT)
        await self.refused_command(by_name["seq15"], SEQ13_EXAMPLES[6])
        await self.refused_command(by_name["security_metadata"], SEQ13_EXAMPLES[5])


class NamedValue(ClientCase):
    """A field that names its value takes that value and no other of its type."""

    def test_the_table_holds_every_such_field_of_every_list(self):
        listed, tabled = {}, {}
        for name, kind, keys, field, want in (
                *((item.name, shape["type"], frozenset(shape), field, want)
                  for item in SHAPED for shape in item.module.RAW_COMMANDS
                  for field, want in shape.items() if field != "type" and names_its_value(want)),
                *(("security_metadata", kind, frozenset({"type", *fields}), field, want)
                  for kind, fields in security.RAW_COMMANDS.items() for field, want in fields.items())):
            listed.setdefault((name, kind, keys, field), []).extend(want if type(want) is tuple else [want])
        for name, example, fields in NAMED:
            for field in fields:
                tabled.setdefault((name, example["type"], frozenset(example), field), []).append(example[field])
        self.assertEqual(set(tabled), set(listed))
        self.assertEqual(len(tabled), 29)
        # And the values a list names for the field are the ones the table sends: no list names one more.
        for key, values in listed.items():
            with self.subTest(field=key):
                self.assertEqual([value for value in values if value not in tabled[key]], [])
                self.assertEqual([value for value in tabled[key] if value not in values], [])
        # The type of every shape is named too; no list holds a pattern or a free string for it.
        for item in SHAPED:
            for shape in item.module.RAW_COMMANDS:
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
            constant = item.module.RAW_CORE_URL if item.module is security else entity.RAW_CORE_URL
            for payload in item.examples:
                with self.subTest(lane=item.name, payload=payload):
                    before = repr(payload)
                    reply, events = await self.sent(item, payload)
                    self.assertEqual(reply, {"type": "result", "id": 1, "success": True, "result": None})
                    self.assertEqual([event[0] for event in events], ["session", "ws_connect", "send", "send"])
                    self.assertEqual(set(events[0][1]), {"timeout", "trust_env", "trace_configs"})
                    self.assertIs(events[0][1]["trust_env"], False)
                    self.assertEqual(events[0][1]["timeout"].total, item.seconds)
                    self.assertIs(events[1][1], constant)
                    self.assertEqual(events[1][1:], (item.pin, {}))
                    self.assertEqual(events[2][1], {"type": "auth", "access_token": TOKEN})
                    self.assertEqual(events[3][1], {"id": 1, **payload})
                    self.assertEqual(repr(payload), before)

    async def test_the_dial_site_uses_the_constant_not_the_configured_value(self):
        for item in LANES:
            constant = item.module.RAW_CORE_URL if item.module is security else entity.RAW_CORE_URL
            equal, elsewhere = item.pin.encode().decode(), item.pin.replace("127.0.0.1", ELSEWHERE)
            self.assertEqual(equal, constant)
            self.assertIsNot(equal, constant)
            for label, later in (("an equal string", equal), ("changed after the client was built", elsewhere)):
                with self.subTest(lane=item.name, configured=label):
                    settings, events = configured(equal), []
                    with patch.object(aiohttp, "ClientSession", new=fake_session(events)):
                        client = item.client(settings)
                        settings.websocket_url = later
                        client.url = client.websocket_url = later
                        reply = await item.send(client, item.examples[0])
                    self.assertIs(reply["success"], True)
                    dialled = [event[1] for event in events if event[0] == "ws_connect"]
                    self.assertEqual(len(dialled), 1)
                    self.assertIs(dialled[0], constant)
                    self.assertIs(type(dialled[0]), str)

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
            reply = await item.send(item.client(configured(item.pin)), payload)
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
    "security_metadata": {"payload": lambda: {
        (payload["type"], frozenset(payload)) for payload in security.SNAPSHOTS}},
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
                self.assertEqual(sent, item.signatures())

    def test_the_loop_behind_the_seq15_dynamic_site_is_the_reviewed_one(self):
        source = (SCRIPTS / "seq15_contract_acceptance.py").read_text(encoding="utf-8")
        self.assertEqual(source.count('for field, value in (("url_path", "seq15-other"), ("mode", "yaml")):'), 1)
        self.assertEqual(source.count("field: value}"), 1)

    def test_every_listed_shape_has_a_sent_example_and_every_example_is_listed(self):
        for item in SHAPED:
            with self.subTest(lane=item.name):
                for shape in item.module.RAW_COMMANDS:
                    self.assertTrue(any(entity.raw_command_listed(example, (shape,)) for example in item.examples),
                                    shape["type"])
                for example in item.examples:
                    self.assertTrue(entity.raw_command_listed(example, item.module.RAW_COMMANDS), example)
        self.assertEqual({(example["type"], example.get("assistant")) for example in SECURITY_EXAMPLES},
                         {(kind, None) for kind, fields in security.RAW_COMMANDS.items() if not fields}
                         | {(security.EXPOSE_NEW_GET, assistant) for assistant in security.KNOWN_ASSISTANTS})
        self.assertEqual(len(SECURITY_EXAMPLES), 8 + len(security.KNOWN_ASSISTANTS))

    def test_no_lane_source_builds_the_client_under_another_name(self):
        # The inventory follows the name ``raw``; nothing else holds one of these clients.
        builders = {"RawWebSocket", "LaneRaw", "RawCore"}
        for item in LANES:
            for filename, _names in item.sources:
                tree = ast.parse((SCRIPTS / filename).read_text(encoding="utf-8"))
                built = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                         and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                         and node.value.func.id in builders]
                calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                         and isinstance(node.func, ast.Name) and node.func.id in builders]
                with self.subTest(lane=item.name, source=filename):
                    self.assertEqual(len(built), len(calls))
                    for node in built:
                        self.assertEqual([ast.unparse(target) for target in node.targets], ["raw"])
                        self.assertEqual(ast.unparse(node.value.args[0]), "configured")

    def test_each_lane_builds_its_own_client_and_no_other(self):
        # A lane that built the shared client would be held to entity_metadata's
        # list and not its own, so the one client a lane builds is its own class.
        builders = {"RawWebSocket", "LaneRaw", "RawCore"}
        own = {"entity_metadata": "RawWebSocket", "configuration_delete": "LaneRaw", "seq13": "LaneRaw",
               "seq15": "LaneRaw", "security_metadata": "RawCore"}
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
                self.assertEqual(built, [own[item.name]])
                # The only other use of any of these names is the lane subclass naming its base.
                subclass = [("LaneRaw", "RawWebSocket")] if own[item.name] == "LaneRaw" else []
                self.assertEqual(bases, subclass)
                self.assertEqual(uses, 1 + len(subclass))
                if item.module is not security:
                    # The list that class enforces is its own module's, set on the class itself.
                    self.assertIs(vars(item.client)["commands"], item.module.RAW_COMMANDS)
                    self.assertTrue(all(vars(other.client)["commands"] is not item.module.RAW_COMMANDS
                                        for other in SHAPED if other is not item))


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
        for item in SHAPED:
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
        self.refused_url(LANES[-1], self.settings(MAIN_CORE).websocket_url)
        self.refused_url(LANES[0], real.websocket_url)


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


class HarnessFlow(HarnessCase):
    """A config flow id is what Core issues, or it never reaches a request path."""

    FLOW = "0123456789abcdef0123456789abcdef"
    OPEN = "/api/config/config_entries/flow"

    async def advance(self, *replies, inputs=({"slot": "a"},)):
        session = Requests(*replies)
        with patch.object(harness, "HA_URL", MAIN_CORE):
            try:
                return await harness._advance_config_flow(session, TOKEN, "synthetic_fixture", list(inputs)), session
            except Exception as error:
                error.session = session
                raise

    async def test_a_flow_id_outside_the_closed_pattern_is_refused_before_it_is_sent(self):
        for flow_id in ("../../auth/token", "..", "a/b", self.FLOW + "/../../../auth/token", self.FLOW + "?x=1",
                        self.FLOW + "#x", self.FLOW + "\n", " " + self.FLOW, self.FLOW[:-1], self.FLOW + "0",
                        self.FLOW.upper(), self.FLOW[:-1] + "g", "@" + ELSEWHERE + "/x", "%2e%2e%2f" + self.FLOW,
                        "01JSYNTHETICFLOW0000000000", "synthetic-flow", Text(self.FLOW), Claims(self.FLOW)):
            with self.subTest(flow_id=flow_id, kind=type(flow_id).__name__):
                with self.assertRaises(ValueError) as caught:
                    await self.advance({"type": "form", "flow_id": flow_id})
                session = caught.exception.__dict__.pop("session")
                self.assert_fixed_code(caught.exception, "real_ha_flow_id_not_listed")
                self.assertNotIn(str(flow_id), str(caught.exception))
                # Only the opening request was made; the id never entered a path.
                self.assertEqual([(method, url) for method, url, _kwargs in session.calls],
                                 [("POST", MAIN_CORE + self.OPEN)])

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
        other = "f" * 32
        result, session = await self.advance({"type": "menu", "flow_id": self.FLOW},
                                             {"type": "form", "flow_id": other}, created,
                                             inputs=({"next_step_id": "tools"}, {}))
        self.assertEqual(result, created)
        self.assertEqual([url for _method, url, _kwargs in session.calls],
                         [MAIN_CORE + self.OPEN, MAIN_CORE + self.OPEN + "/" + self.FLOW,
                          MAIN_CORE + self.OPEN + "/" + other])

    def test_the_pattern_is_the_32_hex_digits_of_a_core_flow_id(self):
        self.assertEqual(harness._FLOW_ID.pattern, "[0-9a-f]{32}")
        self.assertEqual(harness._FLOW_ID.flags & ~32, 0)
        self.assertEqual(seq15._FLOW_ID.pattern, r"/[0-9a-f]{32}\Z")


if __name__ == "__main__":
    unittest.main()
