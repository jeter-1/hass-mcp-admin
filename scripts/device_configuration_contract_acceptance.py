"""HAMCP-366 device-configuration disposable acceptance; never a production entry point.

Called only by core_device_configuration_local after it owns and verifies the
disposable Core, the exact ha-mcp child and the lane's Matter Server stub. The
real Engineering service, adapter, gateway, Core probes and product clients run
against real Core 2026.11.0 ``matter`` entities (design section 9.2). The two
signatures below use a fresh in-memory test key, never publication keys.

Every network client this module opens is closed by construction:

* it compares the configured URL with a loopback constant and refuses when
  they differ, then dials the constant, never the configured value;
* it sends only an exact shape on a closed list (type plus exact field set,
  or method plus path plus body);
* it refuses with a fixed reason code before any session or client exists.
"""
import asyncio
import base64
import contextvars
import copy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp.server.fastmcp import FastMCP
from alarmo_inspection_contract_acceptance import close_disposable_authority
from prepare_core_release_registry import prepare_candidate, sign_candidate
from ha_mcp_engineering.clients import HomeAssistantRestClient, HomeAssistantWebSocketClient
from ha_mcp_engineering.clients.paths import Route, rest_path
from ha_mcp_engineering.clients.rest import ExpectedHttpStatus
from ha_mcp_engineering.device_configuration.contracts import (
    ALLOWLIST, DeviceConfigurationRefusal, DeviceConfigurationRequest, digest, identity)
from ha_mcp_engineering.device_configuration.gateway import DeviceConfigurationGateway
from ha_mcp_engineering.device_configuration.service import (
    DeviceConfigurationCoreAuthority, DeviceConfigurationService)
from ha_mcp_engineering.errors import HomeAssistantApiError
from ha_mcp_engineering.ha_core_readmission import CoreRuntime
from ha_mcp_engineering.ha_core_readmission.profiles import CORE_DEVICE_CONFIGURATION_PROFILES
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
from ha_mcp_engineering.power.contracts import PowerRequest, digest as power_digest
from ha_mcp_engineering.power.service import PowerCoreAuthority, PowerService
from ha_mcp_engineering.providers.upstream_power import PowerProvider
from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway
from ha_mcp_engineering.request_context import begin_request, end_request
from ha_mcp_engineering.signed_registry import canonical_json
from ha_mcp_engineering.upstream_tool_policy import load_reviewed_upstream_release_registry

import lane_failure_diagnostics as diagnostics
from lane_session import lane_session, refuse_redirect

CAPABILITY = "core.device_configuration_entity_operation"
GROUP = "device_configuration_entity"
REFERENCE_KEYS = ("capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint")
# The three admitted triples, restated independently of the runtime constant.
ADMITTED = (("number", "matter", "sensitivity_level"), ("select", "matter", "startup_on_off"),
            ("switch", "matter", "child_lock"))
PREDECESSOR_CAPABILITIES = 32

# The only places this module dials. Exact strings: no other host, port,
# scheme, path, userinfo or query, and no name that only resolves to loopback.
CORE_PORT, UPSTREAM_PORT, STUB_PORT = 18297, 18298, 18299
CORE_URL = "http://127.0.0.1:18297"
CORE_API_URL = "http://127.0.0.1:18297/api"
CORE_WS_URL = "ws://127.0.0.1:18297/api/websocket"
UPSTREAM_URL = "http://127.0.0.1:18298/beta23-real-ha-mcp"
STUB_CONTROL_URL = "http://127.0.0.1:18299/_lane/control"
# Where the disposable Core reaches the stub: a lane-private network alias.
STUB_ALIAS = "hamcp366-matter-stub"
MATTER_URL = "ws://hamcp366-matter-stub:5580/ws"
# Code-owned refusals: never the URL, the token, a path or a payload.
CORE_URL_REFUSED = "core_url_not_pinned"
CORE_COMMAND_REFUSED = "core_command_not_listed"
CORE_REST_REFUSED = "core_rest_request_not_listed"
STUB_URL_REFUSED = "stub_url_not_pinned"
STUB_REQUEST_REFUSED = "stub_request_not_listed"
UPSTREAM_URL_REFUSED = "upstream_url_not_pinned"
FRAME_BYTES = 8 * 1024 * 1024

# The admitted lane entities Core 2026.11.0 creates from the three pinned dumps.
SELECT = "select.eve_energy_plug_power_on_behavior"
SWITCH = "switch.eve_energy_20ecn4101_child_lock_top"
NUMBER = "number.aqara_door_and_window_sensor_p2_sensitivity"
SELECT_NODE, SWITCH_NODE, NUMBER_NODE = 61, 199, 91
SELECT_PATH, NUMBER_PATH = "1/6/16387", "1/128/0"
SWITCH_PATH_SUFFIX = "/319486977/319422481"
FIXTURE_DOMAIN = "hamcp366_refusal_fixture"
FIXTURE_UNIQUE = "hamcp366-refusal-"
# Independent restatement of Core's device values (not production desired()).
SELECT_DEVICE = {"on": 1, "off": 0, "toggle": 2, "previous": None}
SELECT_OPTIONS = ("on", "off", "toggle", "previous")
NUMBER_VALUES = ("1", "2", "3")
# Refusal-fixture cases: unique-id key -> the refusal the signed build gives.
FIXTURE_CASES = (
    ("t2_select", "select", "device_configuration_target_kind_not_admitted"),
    ("t2_switch", "switch", "device_configuration_target_kind_not_admitted"),
    ("t2_number", "number", "device_configuration_target_kind_not_admitted"),
    ("plain_switch", "switch", "device_configuration_not_config_category"),
    ("diagnostic_select", "select", "device_configuration_not_config_category"),
    ("disabled_switch", "switch", "device_configuration_target_disabled"),
    ("no_device_select", "select", "device_configuration_no_device"),
    ("unavailable_select", "select", "device_configuration_target_kind_not_admitted"),
    ("converter_number", "number", "device_configuration_target_kind_not_admitted"),
)
HELD_STATUSES = ("changed_unverified", "verification_failed")
# Registry-level facts recorded beside the fingerprint fields, read from Core's
# own registry answer (not from the runtime projection).
REGISTRY_LEVEL = ("entity_id", "id", "created_at", "modified_at", "device_id", "unique_id",
                  "platform", "config_entry_id")
# The re-registration path the lane takes. The Matter config entry is kept.
REREGISTRATION_PATH = ("websocket config/entity_registry/remove",
                       "POST /api/config/config_entries/entry/{entry_id}/reload")
RELOAD_ONLY_PATH = REREGISTRATION_PATH[1:]
# R1 on the pinned Core: a same-unique_id re-registration under a kept config
# entry restores the registry id, so the undo is refused with exactly this code.
REREGISTRATION_UNDO_REFUSAL = "device_configuration_undo_post_state_unproven"
EVIDENCE_WAIT_SECONDS = 260


def _pattern(regex):
    compiled = re.compile(regex)
    return lambda value: type(value) is str and compiled.fullmatch(value) is not None


def _one_of(*values):
    # Type-exact: True never stands in for 1, nor 1 for 1.0.
    return lambda value: any(type(value) is type(item) and value == item for item in values)


def _int(low, high):
    return lambda value: type(value) is int and low <= value <= high


def _match(value, spec):
    if callable(spec):
        return spec(value) is True
    if type(spec) is dict:
        return (type(value) is dict and value.keys() == spec.keys()
                and all(_match(value[key], item) for key, item in spec.items()))
    return type(value) is type(spec) and value == spec


def listed(value, specs):
    """Whether the value is exactly one of the closed shapes."""
    return any(_match(value, spec) for spec in specs)


ENTITY_PATTERN = r"(select|switch|number)\.[a-z0-9_]{1,120}"
ENTITY = _pattern(ENTITY_PATTERN)
# Everything the product gateway may send through the lane: the two registry
# reads and one service payload per admitted lane entity. A call_service for
# any other entity, service, field or value is refused before the client.
GATEWAY_COMMANDS = (
    {"type": "config/entity_registry/get", "entity_id": ENTITY},
    {"type": "config/device_registry/list"},
    {"type": "call_service", "domain": "select", "service": "select_option",
     "service_data": {"option": _one_of(*SELECT_OPTIONS)}, "target": {"entity_id": SELECT}},
    {"type": "call_service", "domain": "switch", "service": _one_of("turn_on", "turn_off"),
     "target": {"entity_id": SWITCH}},
    {"type": "call_service", "domain": "number", "service": "set_value",
     "service_data": {"value": _one_of(1.0, 2.0, 3.0)}, "target": {"entity_id": NUMBER}},
)
# Everything the lane's own setup and readback WebSocket sends. The update and
# the remove are fixture setup on one named entity each, never the product path.
RAW_COMMANDS = (
    {"type": "config/entity_registry/list"},
    {"type": "config/entity_registry/get", "entity_id": ENTITY},
    {"type": "config/device_registry/list"},
    {"type": "config_entries/get", "domain": "matter"},
    {"type": "config/entity_registry/update",
     "entity_id": _pattern(r"number\.hamcp366_[a-z0-9_]{1,100}"),
     "options_domain": "number", "options": {"unit_of_measurement": "°F"}},
    {"type": "config/entity_registry/remove", "entity_id": SELECT},
)
# Everything the lane's own raw REST session sends: config-entry flow start,
# flow continue and entry reload, by method, exact path shape and body. A state
# read is not on it; LaneState reads state on the bound runtime route.
RAW_REST = (
    {"method": "POST", "path": "/api/config/config_entries/flow",
     "body": {"handler": _one_of("matter", FIXTURE_DOMAIN)}},
    {"method": "POST", "path": _pattern(r"/api/config/config_entries/flow/[0-9A-Za-z]{26,32}"),
     "body": {"url": MATTER_URL}},
    {"method": "POST", "path": _pattern(r"/api/config/config_entries/entry/[0-9A-Z]{26}/reload"),
     "body": None},
)
# Everything the lane posts to its stub: the write faults of design section
# 9.2 and a drift of one admitted attribute. Tighter than the stub entry's list.
STUB_POSTS = (
    {"mode": "none"},
    {"mode": "error"},
    {"mode": "delay", "seconds": _int(1, 10)},
    {"mode": "stall", "seconds": _int(1, 60)},
    {"mode": "mismatch", "stored": _one_of(0, 1, 2)},
    {"action": "drift", "node_id": SELECT_NODE, "attribute_path": SELECT_PATH,
     "value": _one_of(None, 0, 1, 2)},
    {"action": "drift", "node_id": SWITCH_NODE,
     "attribute_path": _pattern(r"[0-9]{1,3}" + SWITCH_PATH_SUFFIX), "value": _one_of(True, False)},
)


def pinned(configured):
    """The configured Core must be the lane's loopback Core; return pinned settings."""
    # Type-exact and in order: a URL that is not the constant is never parsed.
    if (type(configured.ha_url) is not str or configured.ha_url != CORE_URL
            or (configured.api_url, configured.websocket_url) != (CORE_API_URL, CORE_WS_URL)):
        raise ValueError(CORE_URL_REFUSED)
    return replace(configured, ha_url=CORE_URL)


def pinned_upstream(configured):
    """The configured ha-mcp child must be the lane's loopback child."""
    url = configured.upstream_dashboard_mcp_url
    if type(url) is not str or url != UPSTREAM_URL:
        raise ValueError(UPSTREAM_URL_REFUSED)
    return replace(configured, upstream_dashboard_mcp_url=UPSTREAM_URL)


def reference():
    profile, = CORE_DEVICE_CONFIGURATION_PROFILES
    value = profile.to_mapping()
    return {key: value[key] for key in REFERENCE_KEYS}


class LaneWebSocket:
    """The product WebSocket client behind the closed GATEWAY_COMMANDS list.

    Records every admitted registry read and service send in ``journal``; an
    armed hook runs before the Nth registry read of one entity is delegated
    (2 is the locked preflight, 3 is ``once()``).
    """

    def __init__(self, configured, journal=None):
        self.client = HomeAssistantWebSocketClient(pinned(configured))
        self.journal = [] if journal is None else journal
        self.sends, self.refused, self.reads, self.hooks = [], [], {}, {}

    def arm(self, entity_id, nth, hook):
        self.hooks[entity_id] = [self.reads.get(entity_id, 0) + nth, hook]

    async def command(self, payload):
        if not listed(payload, GATEWAY_COMMANDS):
            self.refused.append(CORE_COMMAND_REFUSED)
            raise ValueError(CORE_COMMAND_REFUSED)
        kind = payload["type"]
        if kind == "config/entity_registry/get":
            entity_id = payload["entity_id"]
            self.reads[entity_id] = self.reads.get(entity_id, 0) + 1
            self.journal.append(("registry", entity_id))
            armed = self.hooks.get(entity_id)
            if armed is not None and armed[0] == self.reads[entity_id]:
                del self.hooks[entity_id]
                await armed[1]()
        elif kind == "call_service":
            self.sends.append(copy.deepcopy(payload))
            self.journal.append(("send", payload["target"]["entity_id"]))
        return await self.client.command(payload)


class LaneRest:
    """The product REST client behind one closed request: GET one entity state."""

    PATH = staticmethod(_pattern(r"/states/" + ENTITY_PATTERN))

    def __init__(self, configured, journal=None):
        self.client = HomeAssistantRestClient(pinned(configured))
        self.journal = [] if journal is None else journal
        self.refused = []

    async def request(self, method, path, body=None, raw=False, expected_statuses=frozenset(),
                      *, logbook_read=False):
        if not (method == "GET" and type(path) is Route and self.PATH(str(path)) and body is None
                and raw is False and expected_statuses == frozenset() and logbook_read is False):
            self.refused.append(CORE_REST_REFUSED)
            raise ValueError(CORE_REST_REFUSED)
        self.journal.append(("state", str(path).rsplit("/", 1)[1]))
        return await self.client.request(method, path)


class RawCore:
    """Lane-side authenticated WebSocket for setup and readback; one command per connection.

    Only CORE_WS_URL is dialled and only a RAW_COMMANDS shape is sent. Anything
    else is refused with a fixed code before a session exists.
    """

    def __init__(self, configured):
        pinned(configured)
        self.token = configured.ha_token

    async def command(self, payload, *, seconds=60):
        if not listed(payload, RAW_COMMANDS):
            raise ValueError(CORE_COMMAND_REFUSED)
        # Copied in the same step as the check, before anything is awaited: the
        # frame sent is the command that was checked, whatever the caller does
        # with its payload while the socket connects.
        checked = copy.deepcopy(payload)
        async with lane_session(timeout=aiohttp.ClientTimeout(total=seconds)) as session:
            async with session.ws_connect(CORE_WS_URL, max_msg_size=FRAME_BYTES) as socket:
                assert (await socket.receive_json())["type"] == "auth_required"
                await socket.send_json({"type": "auth", "access_token": self.token})
                assert (await socket.receive_json())["type"] == "auth_ok"
                await socket.send_json({"id": 1, **checked})
                reply = await socket.receive_json()
                assert reply.get("type") == "result" and reply.get("id") == 1
                return reply


class LaneState:
    """Lane-side independent state readback: GET one entity state, nothing else.

    A separate guarded runtime client, never the one the gateway holds, so the
    path is a bound runtime route (HAMCP-200) and no read is journalled as the
    product's. Each read runs in an empty context: a read made by a hook inside
    a product request is not timed, counted or authorized as that request's.
    """

    NOT_FOUND = frozenset({404})

    def __init__(self, configured):
        self.client = HomeAssistantRestClient(pinned(configured))

    async def read(self, entity_id):
        """The state object, or None when Core answers anything but 200."""
        if not ENTITY(entity_id):
            raise ValueError(CORE_REST_REFUSED)
        call = self.client.request("GET", rest_path("/states/{}", entity_id, kinds=("entity_id",)),
                                   expected_statuses=self.NOT_FOUND)
        try:
            value = await asyncio.get_running_loop().create_task(call, context=contextvars.Context())
        except HomeAssistantApiError as exc:
            status = (exc.details if isinstance(exc.details, dict) else {}).get("status")
            if type(status) is not int:
                raise
            return None
        return None if isinstance(value, ExpectedHttpStatus) else value


class RawRest:
    """Lane-side authenticated REST for setup: only the three RAW_REST config-entry routes."""

    def __init__(self, configured):
        pinned(configured)
        self.token = configured.ha_token

    async def rest(self, method, path, body=None, *, seconds=60):
        if not listed({"method": method, "path": path, "body": body}, RAW_REST):
            raise ValueError(CORE_REST_REFUSED)
        # Copied with the check, as RawCore.command copies: the body sent is the
        # body that was checked. The method and the path are exact strings.
        checked = copy.deepcopy(body)
        headers = {"Authorization": "Bearer " + self.token}
        options = {} if checked is None else {"json": checked}
        async with lane_session(timeout=aiohttp.ClientTimeout(total=seconds)) as session:
            async with session.request(method, CORE_URL + path, headers=headers,
                                       allow_redirects=False, **options) as response:
                refuse_redirect(response.status)
                value = await response.json(content_type=None) if response.status == 200 else None
                return response.status, value


class StubControl:
    """The lane's only client of its Matter Server stub: one loopback control route."""

    def __init__(self, url):
        if type(url) is not str or url != STUB_CONTROL_URL:
            raise ValueError(STUB_URL_REFUSED)

    async def _request(self, method, body=None):
        options = {} if body is None else {"json": body}
        async with lane_session(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.request(method, STUB_CONTROL_URL, allow_redirects=False,
                                       **options) as response:
                refuse_redirect(response.status)
                assert response.status == 200
                return await response.json(content_type=None)

    async def read(self):
        return await self._request("GET")

    async def post(self, body):
        if not listed(body, STUB_POSTS):
            raise ValueError(STUB_REQUEST_REFUSED)
        # Copied with the check: the body posted is the body that was checked.
        return await self._request("POST", copy.deepcopy(body))


async def stub_ready(url, seconds=30):
    """Wait until the stub's control route answers through the guarded client."""
    control, deadline = StubControl(url), time.monotonic() + seconds
    while True:
        try:
            return await control.read()
        except (aiohttp.ClientError, asyncio.TimeoutError, AssertionError):
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(0.5)


def operation_id():
    return f"{int(datetime.now(timezone.utc).timestamp())}-{uuid4().hex}"


def device_request(entity_id, value=None, undo=None):
    """One typed request; the action follows the domain, an undo names its task."""
    domain = entity_id.split(".", 1)[0]
    fields = {"entity_id": entity_id, "operation_id": operation_id()}
    if undo is not None:
        fields.update(action="undo", undo_task_id=undo)
    elif domain == "select":
        fields.update(action="select_option", value=value)
    elif domain == "number":
        fields.update(action="set_value", value=value)
    else:
        fields.update(action="turn_" + value)
    return DeviceConfigurationRequest.parse(fields)


def expected_payload(entity_id, value):
    """The one service frame the design allows, restated field by field in order."""
    domain = entity_id.split(".", 1)[0]
    target = {"entity_id": entity_id}
    if domain == "select":
        return {"type": "call_service", "domain": "select", "service": "select_option",
                "service_data": {"option": value}, "target": target}
    if domain == "switch":
        return {"type": "call_service", "domain": "switch", "service": "turn_" + value,
                "target": target}
    return {"type": "call_service", "domain": "number", "service": "set_value",
            "service_data": {"value": float(value)}, "target": target}


def same_frame(sent, expected):
    """Equal values, equal key order and an exact float for the number value."""
    if sent != expected or list(sent) != list(expected):
        return False
    data, wanted = sent.get("service_data"), expected.get("service_data")
    return data is None or (list(data) == list(wanted)
                            and all(type(data[key]) is type(wanted[key]) for key in wanted))


def stub_value(entity_id, value):
    """The device value Core writes for a Home Assistant value (Core 2026.11.0 matter)."""
    domain = entity_id.split(".", 1)[0]
    if domain == "select":
        return SELECT_DEVICE[value]
    if domain == "switch":
        return value == "on"
    return int(float(value)) - 1


def same_state(entity_id, observed, value):
    if entity_id.startswith("number."):
        try:
            return float(observed) == float(value)
        except (TypeError, ValueError):
            return False
    return observed == value


def predicted_refusal(entry, devices):
    """The refusal the registry facts alone predict, in the adapter's check order."""
    if entry.get("entity_category") != "config":
        return "device_configuration_not_config_category"
    owners = [item for item in devices if item.get("id") == entry.get("device_id")]
    if not entry.get("device_id") or len(owners) != 1:
        return "device_configuration_no_device"
    if entry.get("disabled_by") is not None or owners[0].get("disabled_by") is not None:
        return "device_configuration_target_disabled"
    kind = (entry["entity_id"].split(".", 1)[0], entry.get("platform"), entry.get("translation_key"))
    return None if kind in ADMITTED else "device_configuration_target_kind_not_admitted"


def hold_present(receipt, entity_id):
    return any(item.get("key") == "entity:" + entity_id for item in receipt.get("retained_locks", ()))


def evidence(receipt):
    """The durable facts one operation contributes to the lane receipt."""
    keys = ("task_id", "entity_id", "action", "status", "terminal", "terminal_outcome",
            "dispatch_intent_recorded", "provider_response_received", "verification",
            "requested_value", "safety_gate", "reason", "undo_of", "undone")
    value = {key: receipt.get(key) for key in keys if key in receipt}
    value["retained_locks"] = [
        {"key": item.get("key"), "mode": item.get("mode"), "conflict_hold": item.get("conflict_hold")}
        for item in receipt.get("retained_locks", ())]
    value["observation_attempts"] = (receipt.get("observation_status") or {}).get("observation_attempts")
    return value


async def composite(service, entity_id):
    """The runtime's own composite observation of one entity, in its own authority window.

    ``DeviceConfigurationAdapter._read`` under ``DeviceConfigurationCoreAuthority.read``:
    the call preparation, the locked preflight, ``once()`` and the readback make.
    Its ``digest`` is the state fingerprint an undo compares with the recorded
    post-change fingerprint. Nothing here restates the projection.

    The window names its target in the mapping form ``CoreRuntime.target_fingerprint``
    accepts, which fingerprints the same as the adapter's own target for the
    entity, so this module imports nothing from the ``f3`` package.
    """
    adapter = service.adapter.inner
    window = SimpleNamespace(
        target={"target_type": entity_id.split(".", 1)[0], "target_id": entity_id},
        provider_contract=None, core_binding=None)
    _telemetry, token = begin_request("hamcp366-disposable-lane")
    try:
        observed, _display = await adapter.core.read(
            lambda check: adapter._read(entity_id, check), window)
    finally:
        end_request(token)
    return observed


def field_changes(before, after):
    """Every leaf field of two observations: before, after, changed.

    A scalar is recorded as observed (disposable values only); a list is
    recorded by the runtime digest of its value. ``changed`` is decided by that
    digest, so it means exactly "this field moves the fingerprint".
    """
    def leaves(value, prefix):
        if type(value) is dict and value:
            for key in sorted(value):
                yield from leaves(value[key], prefix + (key,))
        else:
            yield ".".join(prefix), value

    def shown(found, name):
        if name not in found:
            return {"absent": True}
        value = found[name]
        return {"sha256": digest(value)} if type(value) in (list, dict) else value

    old, new = dict(leaves(before, ())), dict(leaves(after, ()))
    return {name: {"before": shown(old, name), "after": shown(new, name),
                   "changed": (name in old) != (name in new)
                   or (name in old and digest(old[name]) != digest(new[name]))}
            for name in sorted(set(old) | set(new))}


def observation_delta(before, after):
    """What happened to the runtime's composite observation between two reads."""
    fields = field_changes(before, after)
    return {
        "fingerprint_before": digest(before), "fingerprint_after": digest(after),
        "fingerprint_changed": digest(before) != digest(after),
        "identity_restored": identity(before) == identity(after),
        "changed_fields": [name for name, item in fields.items() if item["changed"]],
        "fields": fields,
    }


def registry_level(before, after):
    """The REGISTRY_LEVEL facts of two raw registry answers: before, after, changed."""
    return field_changes({key: before.get(key) for key in REGISTRY_LEVEL},
                         {key: after.get(key) for key in REGISTRY_LEVEL})


def restored_reregistration(registered, again):
    """R1: Core gave the removed registry entry back as itself, rewritten.

    A new registry id, creation time or unique_id is another case (a replaced
    device, or an entry purged after its config entry was removed). It is not
    accepted here under any undo refusal: it fails the lane.
    """
    assert again["unique_id"] == registered["unique_id"]
    assert (again["id"], again["created_at"]) == (registered["id"], registered["created_at"])
    assert again["modified_at"] != registered["modified_at"]


async def refusal_form(attempt, code):
    """How one refused request ended. Any refusal other than ``code`` fails the lane."""
    try:
        receipt = await attempt()
    except DeviceConfigurationRefusal as exc:
        assert exc.details["category"] == code
        return "raised_before_task"
    assert receipt["terminal"] is True and receipt["dispatch_intent_recorded"] is False
    assert receipt.get("reason") == code and receipt["status"] != "confirmation_required"
    return receipt["terminal_outcome"]


@diagnostics.checked("helper_setup", "helper_contract")
async def run(configured, entry, directory, *, stub_url, stub_hashes):
    """Run only device_configuration_entity; caller owns startup, identity and teardown proof."""
    # Every client is pinned before any authority, service or socket exists.
    safe = pinned_upstream(pinned(configured))
    stub = StubControl(stub_url)
    journal = []
    ws, rest = LaneWebSocket(configured, journal), LaneRest(configured, journal)
    raw, http, reader = RawCore(configured), RawRest(configured), LaneState(configured)

    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    evidence_text = b"Disposable device-configuration test authority; not registry publication authority"
    old = entry["capabilities"]
    assert len(old) == PREDECESSOR_CAPABILITIES and CAPABILITY not in {p["capability_id"] for p in old}
    assert tuple(sorted(ALLOWLIST)) == tuple(sorted(ADMITTED))  # F1: exactly three triples
    runtimes, services = [], []
    counted = {"sends": 0, "writes": 0}
    record = {"operations": {}, "refusals": {}, "observations": {}}

    @diagnostics.checked("helper_setup", "authority_setup")
    async def authority(refs, name):
        test_entry = {**entry, "capabilities": refs,
                      "evidence_sha256": hashlib.sha256(evidence_text).hexdigest()}
        candidate = canonical_json(prepare_candidate(entry=test_entry, evidence=evidence_text,
                                                      previous=None, public_key=key.public_key(), now=now))
        payload = sign_candidate(candidate, expected_sha256=hashlib.sha256(candidate).hexdigest(),
                                 previous=None, key=key, now=now)
        async def fetch(_url, _maximum):
            return payload
        registry = CoreReleaseRegistry(enabled=True, public_key=base64.b64encode(
            key.public_key().public_bytes_raw()).decode(), fetcher=fetch, cache_path=directory / (name + ".json"))
        assert await registry.refresh()
        runtime = CoreRuntime()
        runtimes.append(runtime)
        runtime.configure(safe, release_registry=registry)
        await runtime.reconcile_once("startup")
        assert runtime.current_observation.version == entry["version"]
        return runtime

    @diagnostics.checked("helper_setup", "provider_setup")
    async def compose(runtime, name, *, power=False):
        # Production wiring: the gateway over the two product clients, no extra
        # kind, no safety trigger (design section 9.2, "the signed build only").
        service = DeviceConfigurationService(directory / name, DeviceConfigurationGateway(rest, ws),
                                             DeviceConfigurationCoreAuthority(runtime))
        services.append(service)
        assert service.adapter.inner.extra_kinds == () and service.safety_trigger is None
        if not power:
            return service, None
        gateway = UpstreamReadGateway()
        gateway.configure(safe, release_registry=load_reviewed_upstream_release_registry(),
                          core_runtime=runtime)
        await gateway.reconcile_until_initialized(FastMCP("hamcp366-disposable-" + name))
        # Same root as production: both services share one lock namespace.
        power_service = PowerService(directory / name, PowerProvider.configured(safe, gateway),
                                     PowerCoreAuthority(runtime))
        services.append(power_service)
        return service, power_service

    async def bound(service, req, attribute, digest_of):
        telemetry, token = begin_request("hamcp366-disposable-lane")
        setattr(telemetry, attribute, digest_of(req.model_dump()))
        try:
            return await service.control(req)
        finally:
            end_request(token)

    async def control(service, req):
        return await bound(service, req, "ordinary_device_configuration_binding", digest)

    async def power_control(service, req):
        return await bound(service, req, "ordinary_power_binding", power_digest)

    async def reconcile(service, task_id):
        _telemetry, token = begin_request("hamcp366-disposable-lane")
        try:
            return await service.reconcile(task_id)
        finally:
            end_request(token)

    async def state(entity_id):
        return await reader.read(entity_id)

    async def wait_state(entity_id, accept, seconds):
        deadline = time.monotonic() + seconds
        while True:
            value = await state(entity_id)
            if value is not None and accept(value):
                return value
            assert time.monotonic() < deadline
            await asyncio.sleep(0.5)

    async def writes():
        return (await stub.read())["writes"]

    async def wait_writes(count, seconds):
        deadline = time.monotonic() + seconds
        while len(await writes()) < count:
            assert time.monotonic() < deadline
            await asyncio.sleep(0.2)

    async def fault(body):
        assert await stub.post(body) == {"fault": body}

    async def registry_entry(entity_id):
        reply = await raw.command({"type": "config/entity_registry/get", "entity_id": entity_id})
        return reply["result"] if reply.get("success") else None

    async def unchanged(sends, written):
        assert len(ws.sends) == sends and len(await writes()) == written

    def available(value):
        return value["state"] not in ("unavailable", "unknown")

    @diagnostics.checked("operation", "negative_controls")
    async def zero_dispatch_refusal(service, req, code, label):
        """A refusal with no service frame and no stub write, in either form the source allows."""
        sends, written = len(ws.sends), len(await writes())
        form = await refusal_form(lambda: control(service, req), code)
        await unchanged(sends, written)
        record["refusals"][label] = {"code": code, "form": form, "sends": 0, "stub_writes": 0}
        return form

    @diagnostics.checked("operation", "negative_controls")
    async def observed_undo(service, entity_id, task_id):
        """Observation only: how one undo ends, whichever way. Every frame stays counted."""
        sends, written = len(ws.sends), len(await writes())
        try:
            receipt = await control(service, device_request(entity_id, undo=task_id))
        except DeviceConfigurationRefusal as exc:
            ended = {"code": exc.details["category"], "form": "raised_before_task"}
        else:
            assert receipt["terminal"] is True
            ended = {"code": receipt.get("reason"), "form": receipt["terminal_outcome"]}
            if receipt["dispatch_intent_recorded"]:
                assert receipt["status"] == "succeeded_verified" and receipt["undone"] is True
                counted["sends"] += 1
                counted["writes"] += 1
                await unchanged(sends + 1, written + 1)
                return {**ended, "sends": 1, "stub_writes": 1}
        await unchanged(sends, written)
        return {**ended, "sends": 0, "stub_writes": 0}

    @diagnostics.checked("operation", "operation_dispatch")
    async def verified(service, entity_id, value, node, path, label, *, undo=None, delayed=False):
        """One verified change: one frame, one stub write, Core readback, no replay."""
        sends, written, mark = len(ws.sends), len(await writes()), len(journal)
        req = device_request(entity_id, value, undo)
        started = time.monotonic()
        receipt = await control(service, req)
        elapsed = time.monotonic() - started
        assert receipt["status"] == "succeeded_verified" and receipt["terminal"] is True
        assert receipt["terminal_outcome"] == "succeeded_verified"
        assert receipt["dispatch_intent_recorded"] is True and receipt["entity_id"] == entity_id
        assert receipt["safety_gate"] == "not_safety_critical"  # S8: never confirmation_required
        assert receipt["core_binding"]["contract_fingerprint"] == reference()["contract_fingerprint"]
        counted["sends"] += 1
        counted["writes"] += 1
        assert len(ws.sends) == sends + 1 and same_frame(ws.sends[-1], expected_payload(entity_id, value))
        new = (await writes())[written:]
        assert len(new) == 1
        assert (new[0]["node_id"], new[0]["attribute_path"]) == (node, path)
        wanted = stub_value(entity_id, value)
        assert type(new[0]["value"]) is type(wanted) and new[0]["value"] == wanted
        with diagnostics.check("readback", "value_assertions"):
            observed = await state(entity_id)
            assert observed is not None and same_state(entity_id, observed["state"], value)
            events = journal[mark:]
            sent_at = events.index(("send", entity_id))
            assert events.count(("send", entity_id)) == 1
            # Preparation, the locked preflight and once() each re-read the registry.
            assert events[:sent_at].count(("registry", entity_id)) == 3
            reads_after = events[sent_at:].count(("state", entity_id))
            assert reads_after >= (2 if delayed else 1)
        if undo is None:
            assert receipt["undo"]["available"] is True and receipt["undo"]["undo_task_id"] == req.task_id
        else:
            assert receipt["undone"] is True and receipt["undo_of"] == undo
        with diagnostics.check("operation", "duplicate_suppression"):
            again = await control(service, req)
            assert again["task_id"] == receipt["task_id"]
            assert again["safety_gate"] == "not_evaluated_reconciliation_of_existing_task"
            await unchanged(sends + 1, written + 1)
        record["operations"][label] = {
            **evidence(receipt), "value": value, "stub_write": new[0], "core_state": observed["state"],
            "state_reads_after_send": reads_after, "seconds": round(elapsed, 1)}
        return receipt

    async def others(entity_id, current, pool):
        return [item for item in pool if not same_state(entity_id, current, item)]

    async def snapshot(service):
        """Core's registry answer and the runtime's composite observation, per admitted entity."""
        return {entity_id: (await registry_entry(entity_id), await composite(service, entity_id))
                for entity_id in (SELECT, SWITCH, NUMBER)}

    try:
        # --- Stub identity and lane setup (fixture setup, never the product path) ---
        observed_stub = await stub.read()
        assert observed_stub["stub_sha256"] == stub_hashes["stub_sha256"]
        assert observed_stub["entry_sha256"] == stub_hashes["entry_sha256"]
        assert observed_stub["dump_hashes"] == stub_hashes["dump_hashes"]
        assert observed_stub["writes"] == []
        status, flow = await http.rest("POST", "/api/config/config_entries/flow",
                                       {"handler": FIXTURE_DOMAIN})
        assert status == 200 and flow["type"] == "create_entry"
        status, flow = await http.rest("POST", "/api/config/config_entries/flow", {"handler": "matter"})
        assert status == 200 and flow["type"] == "form" and flow["step_id"] == "manual"
        status, flow = await http.rest("POST", "/api/config/config_entries/flow/" + flow["flow_id"],
                                       {"url": MATTER_URL})
        assert status == 200 and flow["type"] == "create_entry"

        async def matter_entry():
            reply = await raw.command({"type": "config_entries/get", "domain": "matter"})
            assert reply["success"] and len(reply["result"]) == 1
            return reply["result"][0]

        async def wait_loaded():
            deadline = time.monotonic() + 60
            while (found := await matter_entry())["state"] != "loaded":
                assert time.monotonic() < deadline
                await asyncio.sleep(1)
            return found

        matter = await wait_loaded()
        for entity_id in (SELECT, SWITCH, NUMBER):
            await wait_state(entity_id, available, 60)
        listing = await raw.command({"type": "config/entity_registry/list"})
        assert listing["success"]
        entries = {item["entity_id"]: item for item in listing["result"]}
        for entity_id in (SELECT, SWITCH, NUMBER):
            item = entries[entity_id]
            assert (entity_id.split(".", 1)[0], item["platform"], item["translation_key"]) in ADMITTED
            assert item["entity_category"] == "config" and item["disabled_by"] is None
        switch_path = entries[SWITCH]["unique_id"].split("-")[3] + SWITCH_PATH_SUFFIX
        fixtures = {item["unique_id"][len(FIXTURE_UNIQUE):]: item for item in entries.values()
                    if item["platform"] == FIXTURE_DOMAIN}
        assert set(fixtures) == {case[0] for case in FIXTURE_CASES}
        for case, domain, _code in FIXTURE_CASES:
            assert fixtures[case]["entity_id"].split(".", 1)[0] == domain
        converter = fixtures["converter_number"]["entity_id"]
        reply = await raw.command({"type": "config/entity_registry/update", "entity_id": converter,
                                   "options_domain": "number", "options": {"unit_of_measurement": "°F"}})
        assert reply["success"]
        record["observations"]["matter_entry_state"] = matter["state"]
        record["observations"]["switch_attribute_path"] = switch_path

        # --- (A) Predecessor authority: the capability is unsigned, nothing is sent ---
        before = await authority(old, "predecessor")
        assert not before.route_status((CAPABILITY,))["available"]
        withheld, _none = await compose(before, "withheld")
        current = (await state(SELECT))["state"]
        target = (await others(SELECT, current, SELECT_OPTIONS))[0]
        await zero_dispatch_refusal(withheld, device_request(SELECT, target),
                                    "device_configuration_core_authority_unavailable",
                                    "predecessor_authority")
        assert ws.sends == [] and await writes() == []

        # --- (B) Extended authority: the signed three-triple build ---
        after = await authority(old + [reference()], "extended")
        assert after.route_status((CAPABILITY,))["available"]
        service, power = await compose(after, "admitted", power=True)

        # Every admitted kind: verified forward and verified undo, one write each.
        current = (await state(SELECT))["state"]
        assert current in SELECT_OPTIONS
        target = (await others(SELECT, current, SELECT_OPTIONS))[0]
        forward = await verified(service, SELECT, target, SELECT_NODE, SELECT_PATH, "select_forward")
        await verified(service, SELECT, current, SELECT_NODE, SELECT_PATH, "select_undo",
                       undo=forward["task_id"])

        current = (await state(SWITCH))["state"]
        assert current in ("on", "off")
        flipped = "on" if current == "off" else "off"
        await verified(service, SWITCH, flipped, SWITCH_NODE, switch_path, "switch_forward_" + flipped)
        forward = await verified(service, SWITCH, current, SWITCH_NODE, switch_path,
                                 "switch_forward_" + current)
        await verified(service, SWITCH, flipped, SWITCH_NODE, switch_path, "switch_undo",
                       undo=forward["task_id"])

        number_state = await state(NUMBER)
        current = number_state["state"]
        assert (number_state["attributes"]["min"], number_state["attributes"]["max"],
                number_state["attributes"]["step"]) == (1, 3, 1)
        target = (await others(NUMBER, current, NUMBER_VALUES))[0]
        forward = await verified(service, NUMBER, target, NUMBER_NODE, NUMBER_PATH, "number_forward")
        await verified(service, NUMBER, str(int(float(current))), NUMBER_NODE, NUMBER_PATH, "number_undo",
                       undo=forward["task_id"])

        # Delayed state reporting is verified on a later poll, still one write.
        current = (await state(SELECT))["state"]
        target = (await others(SELECT, current, SELECT_OPTIONS))[0]
        await fault({"mode": "delay", "seconds": 3})
        await verified(service, SELECT, target, SELECT_NODE, SELECT_PATH, "select_delayed", delayed=True)
        await fault({"mode": "none"})
        assert record["operations"]["select_delayed"]["seconds"] >= 3

        # Value, bound and step refusals on the admitted entities (N10, N24, N25).
        await zero_dispatch_refusal(service, device_request(NUMBER, "4"),
                                    "device_configuration_number_out_of_range", "number_above_maximum")
        await zero_dispatch_refusal(service, device_request(NUMBER, "0"),
                                    "device_configuration_number_out_of_range", "number_below_minimum")
        await zero_dispatch_refusal(service, device_request(NUMBER, "1.5"),
                                    "device_configuration_number_step_misaligned", "number_off_step")
        await zero_dispatch_refusal(service, device_request(SELECT, "bogus"),
                                    "device_configuration_value_not_offered", "select_option_not_offered")

        # Wrong domain and wrong action never become a request (no I/O at all).
        with diagnostics.check("operation", "negative_controls"):
            mark = len(journal)
            for label, fields, code in (
                ("wrong_domain_action_on_select", {"entity_id": SELECT, "action": "turn_on"},
                 "device_configuration_action_not_supported"),
                ("wrong_domain_action_on_switch", {"entity_id": SWITCH, "action": "set_value", "value": "1"},
                 "device_configuration_action_not_supported"),
                ("wrong_domain_action_on_number", {"entity_id": NUMBER, "action": "select_option",
                                                   "value": "on"},
                 "device_configuration_action_not_supported"),
                ("domain_outside_contract", {"entity_id": "light.hamcp366_other", "action": "turn_on"},
                 "device_configuration_invalid_value"),
                ("extra_field", {"entity_id": SWITCH, "action": "turn_on", "service": "toggle"},
                 "device_configuration_field_not_supported"),
            ):
                try:
                    DeviceConfigurationRequest.parse({**fields, "operation_id": operation_id()})
                except DeviceConfigurationRefusal as exc:
                    assert exc.details["category"] == code
                else:
                    raise AssertionError("request_not_refused")
                record["refusals"][label] = {"code": code, "form": "refused_at_parse", "sends": 0,
                                             "stub_writes": 0}
            assert len(journal) == mark

        # Refusal fixture: every case ends with zero sends and an untouched entity.
        for case, domain, code in FIXTURE_CASES:
            entity_id = fixtures[case]["entity_id"]
            value = {"select": "on", "switch": "on", "number": "3"}[domain]
            if case == "converter_number":
                value = "21"
            await zero_dispatch_refusal(service, device_request(entity_id, value), code, "fixture_" + case)
            observed = await state(entity_id)
            if case == "disabled_switch":
                assert observed is None  # a disabled entity has no state at all
            elif case == "unavailable_select":
                # Core publishes no extra attributes for an unavailable entity
                # (helpers/entity.py, the available branch), so there is no counter to read.
                assert observed["state"] == "unavailable" and "service_calls" not in observed["attributes"]
            else:
                assert observed["attributes"]["service_calls"] == 0
        # Every other matter select, switch or number the dumps create that the
        # registry facts place outside the allowlist is refused the same way.
        devices = await raw.command({"type": "config/device_registry/list"})
        assert devices["success"]
        not_exercised, refused_matter = [], 0
        for entity_id, item in sorted(entries.items()):
            if (item["platform"] != "matter" or entity_id in (SELECT, SWITCH, NUMBER)
                    or entity_id.split(".", 1)[0] not in ("select", "switch", "number")):
                continue
            code = predicted_refusal(item, devices["result"])
            if code is None:
                not_exercised.append(entity_id)
                continue
            value = {"select": "on", "switch": "on", "number": "1"}[entity_id.split(".", 1)[0]]
            await zero_dispatch_refusal(service, device_request(entity_id, value), code,
                                        "matter_" + entity_id)
            refused_matter += 1
        assert refused_matter >= 1
        record["observations"]["admitted_kind_entities_not_exercised"] = not_exercised

        # Composite identity (N12): a change between preparation and the locked
        # preflight, then between the preflight and once(), is refused stale.
        def drift_select(option):
            async def hook():
                reply = await stub.post({"action": "drift", "node_id": SELECT_NODE,
                                         "attribute_path": SELECT_PATH, "value": SELECT_DEVICE[option]})
                assert "drift" in reply
                await wait_state(SELECT, lambda value: value["state"] == option, 10)
            return hook

        async def stale(req, entity_id, nth, hook, outcome, reason, label):
            sends, written = len(ws.sends), len(await writes())
            ws.arm(entity_id, nth, hook)
            receipt = await control(service, req)
            assert entity_id not in ws.hooks  # the hook ran at the intended read
            assert receipt["terminal"] is True and receipt["terminal_outcome"] == outcome
            assert receipt["dispatch_intent_recorded"] is False and receipt.get("reason") == reason
            await unchanged(sends, written)
            record["refusals"][label] = {"code": reason, "form": outcome, "sends": 0, "stub_writes": 0}
            return receipt

        with diagnostics.check("operation", "negative_controls"):
            for nth, outcome, label in ((2, "preflight_rejected", "state_drift_before_locked_preflight"),
                                        (3, "failed_pre_dispatch", "state_drift_before_once")):
                current = (await state(SELECT))["state"]
                target, third = (await others(SELECT, current, SELECT_OPTIONS))[:2]
                await stale(device_request(SELECT, target), SELECT, nth, drift_select(third), outcome,
                            "device_configuration_state_changed", label)

            # R4: the post-change state drifts before the undo's locked preflight.
            current = (await state(SWITCH))["state"]
            flipped = "on" if current == "off" else "off"
            forward = await verified(service, SWITCH, flipped, SWITCH_NODE, switch_path, "switch_before_drift")

            async def drift_switch(original=current):
                reply = await stub.post({"action": "drift", "node_id": SWITCH_NODE,
                                         "attribute_path": switch_path, "value": original == "on"})
                assert "drift" in reply
                await wait_state(SWITCH, lambda value: value["state"] == original, 10)

            await stale(device_request(SWITCH, undo=forward["task_id"]), SWITCH, 2, drift_switch,
                        "preflight_rejected", "device_configuration_undo_post_state_unproven",
                        "undo_after_post_state_drift")

            # The switch is changed first: the reload below passes over it
            # without removing it, and its undo is observed afterwards.
            current = (await state(SWITCH))["state"]
            reloaded = await verified(service, SWITCH, "on" if current == "off" else "off", SWITCH_NODE,
                                      switch_path, "switch_before_reload")

            # N12 registry variant and R1: the registry entry is deleted and
            # re-registered at the same entity_id between the preflight and once().
            current = (await state(SELECT))["state"]
            target, third = (await others(SELECT, current, SELECT_OPTIONS))[:2]
            forward = await verified(service, SELECT, target, SELECT_NODE, SELECT_PATH,
                                     "select_before_reregistration")
            registered = await registry_entry(SELECT)
            first = await snapshot(service)

            async def reregister():
                reply = await raw.command({"type": "config/entity_registry/remove", "entity_id": SELECT})
                assert reply["success"] and await registry_entry(SELECT) is None
                status, _body = await http.rest(
                    "POST", "/api/config/config_entries/entry/" + matter["entry_id"] + "/reload")
                assert status == 200
                await wait_loaded()
                for entity_id in (SELECT, SWITCH, NUMBER):
                    await wait_state(entity_id, available, 60)
                assert await registry_entry(SELECT) is not None

            await stale(device_request(SELECT, third), SELECT, 3, reregister, "failed_pre_dispatch",
                        "device_configuration_state_changed", "reregistration_before_once")
            again = await registry_entry(SELECT)
            second = await snapshot(service)
            kept = await matter_entry()
            # R1: the entry is restored, so the undo is refused with the one
            # exact code. No other refusal is accepted in its place.
            restored_reregistration(registered, again)
            code = REREGISTRATION_UNDO_REFUSAL
            await zero_dispatch_refusal(service, device_request(SELECT, undo=forward["task_id"]), code,
                                        "undo_after_reregistration")
            # Observation only: the undo of the change made before a reload
            # that never removed the entity. The code returned is recorded.
            reload_undo = await observed_undo(service, SWITCH, reloaded["task_id"])
            record["observations"]["reregistration"] = {
                "registry_id_changed": again["id"] != registered["id"],
                "created_at_changed": again["created_at"] != registered["created_at"],
                "modified_at_changed": again["modified_at"] != registered["modified_at"],
                "undo_refusal": code}

            # Observation only (no assertion on its content): what the remove and
            # same-unique_id re-registration did to the runtime's own composite
            # observation, per field, with Core's registry-level facts beside it.
            # The two entities that were only reloaded are a second path.
            def table(entity_id):
                return {"registry_level": registry_level(first[entity_id][0], second[entity_id][0]),
                        **observation_delta(first[entity_id][1], second[entity_id][1])}

            recorded = record["operations"]["select_before_reregistration"]["verification"]
            record["observations"]["reregistration_fingerprint"] = {
                "matter_config_entry_kept": (kept["entry_id"], kept["state"]) == (matter["entry_id"], "loaded"),
                "remove_then_reload": {
                    "path": list(REREGISTRATION_PATH), "when": "between_locked_preflight_and_once",
                    "targets": {SELECT: {
                        **table(SELECT), "undo_refusal": code,
                        "before_is_recorded_post_fingerprint":
                            digest(first[SELECT][1]) == recorded["resulting_state_fingerprint"]}}},
                "reload_only": {
                    "path": list(RELOAD_ONLY_PATH),
                    "targets": {entity_id: table(entity_id) for entity_id in (SWITCH, NUMBER)},
                    "undo_after_reload": {
                        "entity_id": SWITCH, "operation": "switch_before_reload", **reload_undo,
                        "before_is_recorded_post_fingerprint": digest(first[SWITCH][1]) == record[
                            "operations"]["switch_before_reload"]["verification"][
                                "resulting_state_fingerprint"]}}}

        # C1: control_power holds the switch; the device-configuration change
        # does not send while it does.
        with diagnostics.check("operation", "negative_controls"):
            current = (await state(SWITCH))["state"]
            flipped = "on" if current == "off" else "off"
            sends, written = len(ws.sends), len(await writes())
            await fault({"mode": "delay", "seconds": 6})
            holder = asyncio.create_task(power_control(power, PowerRequest(
                entity_id=SWITCH, action="turn_" + flipped, operation_id=operation_id())))
            try:
                await wait_writes(written + 1, 30)
                counted["writes"] += 1
                blocked = await control(service, device_request(SWITCH, flipped))
                assert blocked["terminal"] is True and blocked["dispatch_intent_recorded"] is False
                assert blocked["terminal_outcome"] == "lock_conflict"
                await unchanged(sends, written + 1)
            finally:
                held = await holder
            await fault({"mode": "none"})
            assert held["dispatch_intent_recorded"] is True and held["provider_attempt_count"] == 1
            assert held["terminal_outcome"] == "succeeded_verified"
            await unchanged(sends, written + 1)
            await wait_state(SWITCH, lambda value: value["state"] == flipped, 10)
            record["refusals"]["device_configuration_while_power_holds"] = {
                "code": "lock_conflict", "form": blocked["terminal_outcome"], "sends": 0,
                "stub_writes": 0, "power_outcome": held["terminal_outcome"]}

            # C2: the device-configuration change holds the switch; control_power
            # ends lock_conflict with no power send.
            current = (await state(SWITCH))["state"]
            flipped = "on" if current == "off" else "off"
            sends, written = len(ws.sends), len(await writes())
            await fault({"mode": "delay", "seconds": 6})
            req = device_request(SWITCH, flipped)
            holder = asyncio.create_task(control(service, req))
            try:
                await wait_writes(written + 1, 30)
                counted["sends"] += 1
                counted["writes"] += 1
                blocked = await power_control(power, PowerRequest(
                    entity_id=SWITCH, action="turn_" + flipped, operation_id=operation_id()))
                assert blocked["terminal"] is True and blocked["dispatch_intent_recorded"] is False
                assert blocked["terminal_outcome"] == "lock_conflict"
                assert blocked["provider_attempt_count"] == 0
            finally:
                held = await holder
            await fault({"mode": "none"})
            assert held["status"] == "succeeded_verified" and held["task_id"] == req.task_id
            assert same_frame(ws.sends[-1], expected_payload(SWITCH, flipped))
            await unchanged(sends + 1, written + 1)
            record["refusals"]["power_while_device_configuration_holds"] = {
                "code": "lock_conflict", "form": blocked["terminal_outcome"], "sends": 0,
                "stub_writes": 0, "device_configuration_outcome": held["terminal_outcome"]}

        # Faulted writes last: each leaves a held entity that later steps must not need.
        @diagnostics.checked("readback", "recovery")
        async def held_change(entity_id, value, mode, node, path, label):
            """One dispatched write that is never verified: one write, a hold, no replay."""
            sends, written = len(ws.sends), len(await writes())
            await fault(mode)
            started = time.monotonic()
            receipt = await control(service, device_request(entity_id, value))
            elapsed = time.monotonic() - started
            await fault({"mode": "none"})
            counted["sends"] += 1
            counted["writes"] += 1
            assert receipt["dispatch_intent_recorded"] is True and receipt["provider_attempt_count"] == 1
            assert receipt["status"] in HELD_STATUSES and receipt["terminal_outcome"] != "succeeded_verified"
            assert hold_present(receipt, entity_id)
            assert len(ws.sends) == sends + 1 and same_frame(ws.sends[-1], expected_payload(entity_id, value))
            new = (await writes())[written:]
            assert len(new) == 1 and (new[0]["node_id"], new[0]["attribute_path"]) == (node, path)
            wanted = stub_value(entity_id, value)
            assert type(new[0]["value"]) is type(wanted) and new[0]["value"] == wanted
            record["operations"][label] = {**evidence(receipt), "value": value, "stub_write": new[0],
                                           "seconds": round(elapsed, 1)}
            return receipt

        # Rejected write (N15a): one write, value unchanged, the entity stays held.
        current = (await state(SELECT))["state"]
        target = (await others(SELECT, current, SELECT_OPTIONS))[0]
        rejected = await held_change(SELECT, target, {"mode": "error"}, SELECT_NODE, SELECT_PATH,
                                     "select_rejected_write")
        assert (await state(SELECT))["state"] == current
        # R7: an unverified task has no undo; a second change waits behind the hold.
        await zero_dispatch_refusal(service, device_request(SELECT, undo=rejected["task_id"]),
                                    "device_configuration_undo_unavailable", "undo_of_unverified_task")
        with diagnostics.check("operation", "negative_controls"):
            sends, written = len(ws.sends), len(await writes())
            blocked = await control(service, device_request(SELECT, target))
            assert blocked["terminal_outcome"] == "lock_conflict"
            assert blocked["dispatch_intent_recorded"] is False
            await unchanged(sends, written)
            record["refusals"]["change_behind_hold"] = {"code": "lock_conflict", "form": "lock_conflict",
                                                        "sends": 0, "stub_writes": 0}

        # Stored-value mismatch (N26): the device stores another value; never verified.
        current = (await state(NUMBER))["state"]
        target, stored = (await others(NUMBER, current, NUMBER_VALUES))[:2]
        mismatched = await held_change(NUMBER, target, {"mode": "mismatch", "stored": int(stored) - 1},
                                       NUMBER_NODE, NUMBER_PATH, "number_stored_value_mismatch")
        await wait_state(NUMBER, lambda value: same_state(NUMBER, value["state"], stored), 10)

        # Stalled write (N16): the provider answer never arrives in time.
        current = (await state(SWITCH))["state"]
        flipped = "on" if current == "off" else "off"
        stalled = await held_change(SWITCH, flipped, {"mode": "stall", "seconds": 50}, SWITCH_NODE,
                                    switch_path, "switch_stalled_write")
        assert stalled["provider_response_received"] is False

        # Readback after the evidence deadline: no task is promoted, none re-sent.
        with diagnostics.check("readback", "recovery"):
            sends, written = len(ws.sends), len(await writes())
            pending = {"select_rejected_write": rejected, "number_stored_value_mismatch": mismatched,
                       "switch_stalled_write": stalled}
            deadline = time.monotonic() + EVIDENCE_WAIT_SECONDS
            final = {}
            while True:
                for label, receipt in pending.items():
                    final[label] = await reconcile(service, receipt["task_id"])
                if all(item["terminal"] for item in final.values()):
                    break
                assert time.monotonic() < deadline
                await asyncio.sleep(5)
            await unchanged(sends, written)
            for label, receipt in final.items():
                assert receipt["provider_attempt_count"] == 1
                record["operations"][label]["final"] = evidence(receipt)
            assert final["number_stored_value_mismatch"]["status"] == "verification_failed"
            assert hold_present(final["number_stored_value_mismatch"], NUMBER)
            assert final["select_rejected_write"]["status"] in HELD_STATUSES
            assert hold_present(final["select_rejected_write"], SELECT)
            assert final["switch_stalled_write"]["status"] in HELD_STATUSES + ("succeeded_verified",)

        # Totals: every frame and every stub write is accounted for.
        assert ws.refused == [] and rest.refused == []
        assert len(ws.sends) == counted["sends"]
        assert len(await writes()) == counted["writes"]
        for label in ("select_forward", "select_undo", "switch_forward_on", "switch_forward_off",
                      "switch_undo", "number_forward", "number_undo"):
            assert record["operations"][label]["status"] == "succeeded_verified"
            assert record["operations"][label]["verification"]["resulting_state_fingerprint"]
        health = service.health()
        assert health["fallback_count"] == after.health_snapshot()["fallback_count"] == 0
        return {
            "result": "PASS", "group": GROUP, "profile": reference(),
            "authority_transition": [len(old), len(old) + 1],
            "admitted_kinds": [list(kind) for kind in ADMITTED],
            "admitted_entities": {entity_id: {
                "platform": entries[entity_id]["platform"],
                "translation_key": entries[entity_id]["translation_key"],
                "entity_category": entries[entity_id]["entity_category"]}
                for entity_id in (SELECT, SWITCH, NUMBER)},
            "stub": {"stub_sha256": observed_stub["stub_sha256"],
                     "entry_sha256": observed_stub["entry_sha256"],
                     "dump_hashes": observed_stub["dump_hashes"]},
            "operations": record["operations"], "refusals": record["refusals"],
            "observations": {**record["observations"],
                             "retained_lock_count": health["retained_lock_count"],
                             "nonterminal_tasks": health["nonterminal_tasks"]},
            "service_sends": counted["sends"], "stub_writes": counted["writes"],
            "client_refusals": 0, "fallback_count": 0, "production_authority": False,
        }
    finally:
        with diagnostics.check("cleanup", "service_close"):
            for service in services:
                await service.close()
            for runtime in runtimes:
                await close_disposable_authority(runtime)
