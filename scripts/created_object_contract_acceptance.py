"""Disposable-Core G4 acceptance for the two dormant created-object profiles.

HAMCP-329 ``core.scene_create_delete_operation`` and HAMCP-298
``core.created_helper_delete_operation``, Core 2026.11.0 only.

Each lane loads exactly one of the two compiled profiles into a test-signed,
in-memory authority (the signed predecessor's references plus that profile) and
drives the production composition against an owned loopback Core: a governed
forward change that ends ``succeeded_verified``, its governed undo that ends
``succeeded_verified``, and an independent read-back of each by the lane's own
client. The predecessor authority, which lacks the profile, must refuse first.

The lane's own REST requests go through a separate guarded Engineering client
on bound runtime routes (HAMCP-200). That transport refuses a scene config
DELETE from anything but the created-object delete gateway, so the lane never
removes a scene itself: every scene it creates leaves Core through the governed
undo it proves, and clean-up only reads that it is gone.

No production authority, key, registry publication or deployed system is
involved; the caller owns container start-up, image identity and teardown.
"""
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import sys
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT / "scripts"))

from entity_metadata_contract_acceptance import (  # noqa: E402
    REFERENCE_KEYS, RawRefusal, RawWebSocket, engineering_source, load_receipt, receipt_bytes)
import lane_failure_diagnostics as diagnostics  # noqa: E402

MODEL = "hamcp329-298-created-object-lane-v1"
CORE_VERSION = "2026.11.0"
SIGNED_PREDECESSOR_COUNT = 32
PENDING = "pending_signed_registry_admission"
NO_ELIGIBLE = "no_eligible_created_object"
VERIFIED = "succeeded_verified"
REVIEWER = "home_assistant_admin_ingress:hamcp329-298-synthetic-reviewer"
CALLER = "hamcp329-298-synthetic-owner"
REGISTRY_LIST = "config/entity_registry/list"
SCENE_TOOL = "create_scene_configuration_plan"
SCENE_ROUTE = "/config/scene/config/{}"
AUTOMATION_ROUTE = "/config/automation/config/{}"
STATE_ROUTE = "/states/{}"
SCENE_POST = "POST " + SCENE_ROUTE
SCENE_DELETE = "DELETE " + SCENE_ROUTE
AUTOMATION_POST = "POST " + AUTOMATION_ROUTE
HELPER_DOMAINS = ("input_boolean", "input_number")
READ_ONLY_POSTS = frozenset({"POST /config/core/check_config", "POST /template"})
WS_WRITE_SUFFIXES = frozenset({"create", "update", "delete", "remove"})
SYNTHETIC_PROVIDER_IDENTITY = {"slug": "hamcp329_298_disposable_lane",
                               "evidence_hash": hashlib.sha256(b"HAMCP-329/298 synthetic provider lock").hexdigest()}
LANES = {
    "scene_create_delete": {
        "capability": "core.scene_create_delete_operation",
        "fingerprint": "sha256:93378ae0385528f16deb977a985b778ba2dd2d39be6f5cb397ef74f2060a400d",
        "kinds": ("scene",),
        "scenarios": ("scene_create", "scene_undo", "scene_recreate", "scene_existing_id_refused",
                      "scene_recreate_undo"),
        "writes": frozenset({SCENE_POST, SCENE_DELETE}),
    },
    "created_helper_delete": {
        "capability": "core.created_helper_delete_operation",
        "fingerprint": "sha256:3f2e681310a009a4fc853af6fa24f8eca1c671c7dd75bddbbc8b403fec9cdbf4",
        "kinds": HELPER_DOMAINS,
        "scenarios": ("helper_create_input_boolean", "helper_undo_input_boolean",
                      "helper_create_input_number", "helper_undo_input_number",
                      "mixed_plan_create", "mixed_plan_undo"),
        "writes": frozenset({AUTOMATION_POST, *(domain + "/" + verb for domain in HELPER_DOMAINS
                                                for verb in ("create", "delete"))}),
    },
}
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
# What the lane's own client may name: only fixtures this lane owns.
LANE_ID = re.compile(r"hamcp_g4_[0-9a-z_]{1,48}")
LANE_ENTITY = re.compile(r"(?:scene|input_boolean|input_number)\.hamcp_g4_[0-9a-z_]{1,72}")
# Every method and route the lane's own client sends. No scene config write or
# delete is among them: those are Engineering's, through its gateways.
LANE_REQUESTS = frozenset({("GET", STATE_ROUTE), ("GET", SCENE_ROUTE), ("GET", AUTOMATION_ROUTE),
                           ("POST", AUTOMATION_ROUTE), ("DELETE", AUTOMATION_ROUTE)})


def reference(lane):
    """The compiled profile reference one lane loads; its fingerprint is the signing fingerprint."""
    from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
    (profile,) = [p for p in CORE_RUNTIME_CAPABILITY_PROFILES if p.capability_id == LANES[lane]["capability"]]
    value = {key: profile.to_mapping()[key] for key in REFERENCE_KEYS}
    if value["contract_fingerprint"] != LANES[lane]["fingerprint"]:
        raise ValueError("Compiled profile fingerprint differs from the reviewed lane")
    return value


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def rest_kind(method, path):
    """Name one REST request by its reviewed template; ids never enter evidence."""
    template = getattr(path, "template", None)
    if isinstance(template, str):
        return method + " " + template
    path = str(path)
    for prefix in ("/states/", "/config/scene/config/", "/config/automation/config/", "/config/script/config/"):
        if path.startswith(prefix):
            return method + " " + prefix + "{}"
    return method + " " + path


def is_write(kind):
    if " " in kind:
        return not kind.startswith("GET ") and kind not in READ_ONLY_POSTS
    return (kind.rpartition("/")[2] in WS_WRITE_SUFFIXES or kind.startswith("call_service")
            or kind == "execute_script")


class RequestLog:
    """Every request Engineering's own clients sent, by reviewed kind."""

    def __init__(self):
        self.kinds = []
        self.payloads = []

    def record(self, kind, payload=None):
        self.kinds.append(kind)
        self.payloads.append(None if payload is None else dict(payload))

    def mark(self):
        return len(self.kinds)

    def writes_since(self, mark=0):
        return [kind for kind in self.kinds[mark:] if is_write(kind)]

    def payloads_since(self, mark, kind):
        return [payload for name, payload in zip(self.kinds[mark:], self.payloads[mark:]) if name == kind]


class ObservedRest:
    def __init__(self, inner, log):
        self.inner = inner
        self.log = log

    async def request(self, method, path, *args, **kwargs):
        self.log.record(rest_kind(method, path))
        return await self.inner.request(method, path, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class ObservedWebSocket:
    def __init__(self, inner, log):
        self.inner = inner
        self.log = log

    async def command(self, payload):
        kind = str(payload.get("type"))
        if kind == "call_service":
            kind = "call_service:" + str(payload.get("domain")) + "." + str(payload.get("service"))
        self.log.record(kind, payload)
        return await self.inner.command(payload)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _owned(pattern, value):
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ValueError("Not a created-object lane fixture")
    return value


def scene_route(config_id):
    """The bound runtime route of one lane-owned scene config (HAMCP-200)."""
    from ha_mcp_engineering.clients.paths import rest_path
    return rest_path("/config/scene/config/{}", _owned(LANE_ID, config_id))


def automation_route(target_id):
    """The bound runtime route of the lane's synthetic consumer automation."""
    from ha_mcp_engineering.clients.paths import rest_path
    return rest_path("/config/automation/config/{}", _owned(LANE_ID, target_id))


def state_route(entity_id):
    """The state route of a lane-owned entity: a declared member or a captured or registered identity."""
    from ha_mcp_engineering.clients.paths import rest_path
    return rest_path("/states/{}", _owned(LANE_ENTITY, entity_id), kinds=("entity_id",))


class LaneRaw(RawWebSocket):
    """The lane's own loopback client: fixture setup, independent read-back and clean-up.

    REST goes through a separate guarded Engineering client, never the one
    Engineering's gateways hold, so every path is a bound runtime route
    (HAMCP-200) and none of its requests is counted as Engineering's. Only the
    ``LANE_REQUESTS`` pairs are sent, only for a lane-owned id.
    """

    def __init__(self, configured):
        from ha_mcp_engineering.clients import HomeAssistantRestClient
        api = urlsplit(configured.api_url)
        # One exact loopback origin for REST and WebSocket: no credentials,
        # other path, query or fragment a route could be re-rooted by.
        if (api.hostname not in LOOPBACK or "@" in api.netloc
                or configured.api_url != "http://" + api.netloc + "/api"
                or configured.websocket_url != "ws://" + api.netloc + "/api/websocket"):
            raise ValueError("Created-object lane requests go only to a loopback Core")
        super().__init__(configured)
        self.client = HomeAssistantRestClient(configured)

    async def status(self, method, route, body=None):
        from ha_mcp_engineering.clients.paths import is_route
        from ha_mcp_engineering.clients.rest import ExpectedHttpStatus
        from ha_mcp_engineering.errors import HomeAssistantApiError
        if (not is_route(route) or (method, route.template) not in LANE_REQUESTS or len(route.values) != 1
                or route.query):
            raise ValueError("Not a created-object lane request")
        _owned(LANE_ENTITY if route.template == STATE_ROUTE else LANE_ID, route.values[0])
        try:
            payload = await self.client.request(method, route, body, expected_statuses=frozenset({404}))
        except HomeAssistantApiError as exc:
            status = (exc.details if isinstance(exc.details, dict) else {}).get("status")
            if type(status) is not int:
                raise
            return status, None
        if isinstance(payload, ExpectedHttpStatus):
            return payload.status, None
        return 200, payload


class Reader:
    """Independent read-back facts, each read by the lane and never inferred."""

    def __init__(self, status, ok):
        self.status = status
        self.ok = ok

    async def registry(self, platform, unique_id):
        matches = [item for item in await self.ok({"type": REGISTRY_LIST})
                   if item.get("platform") == platform and item.get("unique_id") == unique_id]
        assert len(matches) <= 1
        return matches[0].get("entity_id") if matches else None

    async def state(self, entity_id):
        status, value = await self.status("GET", state_route(entity_id))
        return status, (value or {}).get("state")

    async def scene(self, config_id, entity_id=None):
        config_status, config = await self.status("GET", scene_route(config_id))
        registered = await self.registry("homeassistant", config_id)
        entity_id = entity_id or registered
        if entity_id is None:
            raise ValueError("No captured or registered scene identity to read back")
        return {"config_status": config_status, "registry_entity_id": registered, "entity_id": entity_id,
                "state_status": (await self.state(entity_id))[0]}, config

    async def helper(self, domain, object_id, entity_id=None):
        stored = [item for item in await self.ok({"type": domain + "/list"}) if item.get("id") == object_id]
        registered = await self.registry(domain, object_id)
        entity_id = entity_id or registered
        if entity_id is None:
            raise ValueError("No captured or registered helper identity to read back")
        return {"stored": len(stored) == 1, "registry_entity_id": registered, "entity_id": entity_id,
                "state_status": (await self.state(entity_id))[0]}, (stored[0] if stored else None)

    async def automation(self, target_id):
        status, config = await self.status("GET", automation_route(target_id))
        assert status == 200
        return config


class Context:
    """What one scenario may use; the lane and the offline test each build one."""

    def __init__(self, *, service, log, reader, scene_plan, configuration_plan, add_automation, members,
                 suffix=None, settle=0.2):
        self.service = service
        self.log = log
        self.reader = reader
        self.scene_plan = scene_plan
        self.configuration_plan = configuration_plan
        self.add_automation = add_automation
        self.members = tuple(members)
        self.suffix = suffix or secrets.token_hex(4)
        self.settle = settle
        self.owned = {"scenes": [], "helpers": [], "automations": []}
        self.state = {}

    async def until(self, read, done):
        """Poll an independent read-back until ``done``; the last value is the evidence."""
        for _ in range(100):
            value = await read()
            if done(value[0]):
                break
            await asyncio.sleep(self.settle)
        return value


async def approve_apply(service, plan):
    """Exact-hash external approval (every step the review asks for), then apply."""
    from ha_mcp_engineering.request_context import begin_request, end_request
    telemetry, token = begin_request("hamcp329-298-lane-apply")
    telemetry.caller_id = CALLER
    try:
        granted = service.approve(plan["plan_id"], plan["plan_hash"])
        for _ in range(3):
            review, csrf = await service.issue_external_csrf(plan["plan_id"], granted["challenge_id"])
            safety = review.get("safety_critical_acknowledgement") if isinstance(review, dict) else None
            acknowledgement = {} if not safety else {
                "acknowledged_objects": tuple(row["object_ref"] for row in safety["objects"]),
                "acknowledged_projection_hash": safety["projection_hash"]}
            granted = await service.decide_external_approval(
                plan_id=plan["plan_id"], challenge_id=granted["challenge_id"],
                expected_plan_hash=plan["plan_hash"], approval_kind=granted["approval_kind"],
                approval_action=granted["approval_action"], csrf_nonce=csrf, decision="approve",
                approver_principal=REVIEWER, **acknowledgement)
            if granted.get("status") != "approval_pending":
                break
        else:
            raise AssertionError("Synthetic approval did not settle")
        return await service.apply(plan["plan_id"], plan["plan_hash"])
    finally:
        end_request(token)


async def rollback(service, plan_id):
    from ha_mcp_engineering.request_context import begin_request, end_request
    telemetry, token = begin_request("hamcp329-298-lane-rollback")
    telemetry.caller_id = CALLER
    try:
        return await service.rollback_change(plan_id)
    finally:
        end_request(token)


def facts(service, plan_id):
    """Durable task facts of one applied plan: state, children and captured identities."""
    task = service.task_repository.get_for_plan(plan_id)
    runtime = service.f3_runtime
    declared = runtime.children.declarations_for_task(task.task_id)
    return {"task_state": task.state.value,
            "children": [[item["operation_id"], item.get("normalized_outcome"), item.get("dispatch_count")]
                         for item in task.verification_summary["children"]],
            "identities": [runtime.created_identities.get(item["child_id"]) for item in declared]}


async def applied(c, plan, writes):
    """Apply ``plan``; exactly ``writes`` reach Core, each child dispatched once and verified."""
    mark = c.log.mark()
    result = await approve_apply(c.service, plan)
    value = facts(c.service, plan["plan_id"])
    sent = c.log.writes_since(mark)
    assert result["task_state"] == VERIFIED == value["task_state"], (result.get("task_state"), value["children"])
    assert sent == writes, sent
    assert all(child[1:] == [VERIFIED, 1] for child in value["children"]), value["children"]
    return {"task_state": value["task_state"], "children": value["children"], "writes": sent}, value, mark


async def undo_plan(c, plan_id, *, delete, restore=0):
    """``rollback_change`` only plans: nothing is sent until the reverse plan is approved."""
    mark = c.log.mark()
    rolled = await rollback(c.service, plan_id)
    assert rolled["status"] == "rollback_plan_created", rolled.get("status")
    assert rolled["provider_dispatch_occurred"] is False and rolled["approval_required"] is True
    assert rolled.get("operations_to_delete", []) == delete and rolled["operations_excluded"] == []
    assert len(rolled.get("operations_to_restore", [])) == restore
    assert c.log.writes_since(mark) == []
    return rolled, c.service.get_plan(rolled["rollback_plan_id"])


async def refused(awaitable):
    """The closed details of a governed refusal; anything else is a lane failure."""
    from ha_mcp_engineering.errors import GovernanceError
    try:
        await awaitable
    except GovernanceError as exc:
        return exc.details if isinstance(exc.details, dict) else {}
    raise AssertionError("A governed refusal was expected")


async def refusal(awaitable):
    details = await refused(awaitable)
    return details.get("reason_code") or details.get("reason")


def scene_probe(config_id, member):
    """A complete scene create request: the route derives its exact target from all of it."""
    return {"action": "create", "config_id": config_id, "name": "HAMCP G4 withheld",
            "entities": {member: {"state": "on"}}}


async def withheld_scene_create(c, plan):
    """Without the profile a scene create is refused: no plan stored, nothing written."""
    probe = scene_probe("hamcp_g4_withheld_" + c.suffix, c.members[0])
    mark, plans = c.log.mark(), len(c.service.repository.list())
    reason = await refusal(plan(probe))
    return {"reason_code": reason, "undo_writes": c.log.writes_since(mark), "ha_requests": c.log.mark() - mark,
            "plan_created": len(c.service.repository.list()) != plans}


async def withheld_helper_undo(c):
    """The signed predecessor already creates helpers; without the profile no delete
    identity is captured, so the undo is refused and the helper stays."""
    domain = "input_boolean"
    operation, object_id = helper_operation(domain, "HAMCP G4 withheld " + c.suffix)
    c.owned["helpers"].append((domain, object_id))
    plan = await c.configuration_plan([operation])
    _result, value, _mark = await applied(c, plan, [domain + "/create"])
    record = value["identities"][0]
    mark = c.log.mark()
    details = await refused(rollback(c.service, plan["plan_id"]))
    readback, _stored = await c.until(lambda: c.reader.helper(domain, object_id, domain + "." + object_id),
                                      lambda item: item["state_status"] == 200)
    return {"forward_task_state": value["task_state"], "reason_code": details.get("reason_code"),
            "operations_excluded": [item.get("reason") for item in details.get("operations_excluded", [])],
            "provider_dispatch_occurred": details.get("provider_dispatch_occurred"),
            "identity_outcome": record and record["outcome"], "identity_captured": bool(record and record["identity"]),
            "undo_writes": c.log.writes_since(mark),
            "helper_retained": readback["stored"] and readback["state_status"] == 200}


def captured(value, index=0):
    record = value["identities"][index]
    assert record is not None and record["outcome"] == "captured", record
    return record["identity"]


# -- scene lane ----------------------------------------------------------
async def scene_create(c):
    config_id = "hamcp_g4_scene_" + c.suffix
    name = "HAMCP G4 scene " + c.suffix
    entities = {c.members[0]: {"state": "on"}, c.members[1]: {"state": "off"}}
    before = [await c.reader.state(member) for member in c.members]
    assert (await c.reader.status("GET", scene_route(config_id)))[0] == 404
    mark = c.log.mark()
    c.owned["scenes"].append(config_id)
    plan = await c.scene_plan({"action": "create", "config_id": config_id, "name": name, "entities": entities})
    assert c.log.writes_since(mark) == []
    result, value, mark = await applied(c, plan, [SCENE_POST])
    identity = captured(value)
    assert identity["registry_unique_id"] == config_id and identity["registry_platform"] == "homeassistant"
    assert identity["delete_argument"] == {"method": "DELETE", "path": "/config/scene/config/" + config_id}
    readback, stored = await c.until(lambda: c.reader.scene(config_id, identity["entity_id"]),
                                     lambda item: item["state_status"] == 200)
    assert readback == {"config_status": 200, "registry_entity_id": identity["entity_id"],
                        "entity_id": identity["entity_id"], "state_status": 200}, readback
    assert stored == {"id": config_id, "name": name, "entities": entities}, sorted(stored)
    # A create stores a scene; it never activates it.
    assert [await c.reader.state(member) for member in c.members] == before
    c.state.update(config_id=config_id, plan=plan, entity_id=identity["entity_id"], stored=stored)
    return {"result": "PASS", **result, "identity_outcome": "captured", "readback": readback,
            "readback_fingerprint": digest(stored), "members_unchanged": True}


async def scene_undo(c):
    config_id, entity_id = c.state["config_id"], c.state["entity_id"]
    rolled, plan = await undo_plan(c, c.state["plan"]["plan_id"], delete=["scene_create_1"])
    result, _value, _mark = await applied(c, plan, [SCENE_DELETE])
    readback, stored = await c.until(lambda: c.reader.scene(config_id, entity_id),
                                     lambda item: item["state_status"] == 404)
    assert readback == {"config_status": 404, "registry_entity_id": None, "entity_id": entity_id,
                        "state_status": 404} and stored is None, readback
    c.state["undo"] = rolled
    return {"result": "PASS", **result, "rollback_status": rolled["status"], "readback": readback,
            "readback_fingerprint": digest(readback)}


async def scene_recreate(c):
    config_id = c.state["config_id"]
    mark = c.log.mark()
    rolled = await rollback(c.service, c.state["undo"]["rollback_plan_id"])
    assert rolled["status"] == "rollback_plan_created" and rolled["operations_to_restore"] == ["scene_create_1"]
    assert c.log.writes_since(mark) == []
    plan = c.service.get_plan(rolled["rollback_plan_id"])
    result, value, _mark = await applied(c, plan, [SCENE_POST])
    identity = captured(value)
    readback, stored = await c.until(lambda: c.reader.scene(config_id),
                                     lambda item: item["state_status"] == 200)
    assert readback["config_status"] == 200 and readback["registry_entity_id"] == readback["entity_id"]
    assert identity["registry_unique_id"] == config_id and identity["entity_id"] == readback["entity_id"]
    assert stored == c.state["stored"]
    c.state["recreate"] = (plan, readback["entity_id"])
    return {"result": "PASS", **result, "rollback_status": rolled["status"], "identity_outcome": "captured",
            "same_config_id": True, "config_matches_first_create": True, "readback": readback,
            "readback_fingerprint": digest(stored)}


async def scene_existing_id_refused(c):
    """A create never overwrites: an existing config id refuses before any write."""
    config_id = c.state["config_id"]
    mark, plans = c.log.mark(), len(c.service.repository.list())
    reason = await refusal(c.scene_plan({"action": "create", "config_id": config_id, "name": "HAMCP G4 overwrite",
                                         "entities": {c.members[0]: {"state": "off"}}}))
    assert c.log.writes_since(mark) == [] and len(c.service.repository.list()) == plans
    _readback, stored = await c.reader.scene(config_id)
    assert stored == c.state["stored"]
    return {"result": "PASS", "reason_code": reason, "plan_created": False, "writes": [],
            "existing_scene_unchanged": True}


async def scene_recreate_undo(c):
    """The recreate is a governed create like the first, so its undo is the same governed delete.

    It runs last: only the created-object gateway may delete a scene config, so
    this is also how the lane leaves no scene behind.
    """
    config_id = c.state["config_id"]
    plan, entity_id = c.state["recreate"]
    rolled, reverse = await undo_plan(c, plan["plan_id"], delete=["scene_create_1"])
    result, _value, _mark = await applied(c, reverse, [SCENE_DELETE])
    readback, stored = await c.until(lambda: c.reader.scene(config_id, entity_id),
                                     lambda item: item["state_status"] == 404)
    assert readback == {"config_status": 404, "registry_entity_id": None, "entity_id": entity_id,
                        "state_status": 404} and stored is None, readback
    return {"result": "PASS", **result, "rollback_status": rolled["status"], "readback": readback,
            "readback_fingerprint": digest(readback)}


# -- created-helper lane ---------------------------------------------------
def helper_config(domain, name):
    if domain == "input_boolean":
        return {"name": name, "icon": "mdi:toggle-switch"}
    return {"name": name, "min": 16, "max": 30, "step": 0.5, "mode": "slider", "unit_of_measurement": "°C"}


def helper_operation(domain, name, *, operation_id="create_helper"):
    object_id = name.lower().replace(" ", "_")
    return {"operation_id": operation_id, "resource_type": "helper", "helper_type": domain, "action": "create",
            "target_id": domain + "." + object_id, "depends_on": [],
            "proposed_config": helper_config(domain, name)}, object_id


async def created_helper(c, domain, label, *more):
    """One governed helper create, verified, with its identity captured and read back."""
    operation, object_id = helper_operation(domain, "HAMCP G4 " + label + " " + c.suffix)
    c.owned["helpers"].append((domain, object_id))
    plan = await c.configuration_plan([operation, *more])
    result, value, _mark = await applied(c, plan, [domain + "/create", *([AUTOMATION_POST] if more else [])])
    identity = captured(value)
    assert identity["storage_object_id"] == object_id == identity["registry_unique_id"]
    assert identity["registry_platform"] == domain and identity["entity_id"] == domain + "." + object_id
    assert identity["delete_argument"] == {"type": domain + "/delete", domain + "_id": object_id}
    readback, stored = await c.until(lambda: c.reader.helper(domain, object_id, identity["entity_id"]),
                                     lambda item: item["state_status"] == 200)
    assert readback == {"stored": True, "registry_entity_id": identity["entity_id"],
                        "entity_id": identity["entity_id"], "state_status": 200}, readback
    return plan, identity, {"result": "PASS", **result, "identity_outcome": "captured", "readback": readback,
                            "readback_fingerprint": digest(stored)}


async def deleted_helper(c, plan, identity, writes):
    """Apply a reverse plan; the helper delete is the captured argument, sent exactly once."""
    domain = identity["registry_platform"]
    result, _value, mark = await applied(c, plan, writes)
    assert c.log.payloads_since(mark, domain + "/delete") == [identity["delete_argument"]]
    readback, stored = await c.until(
        lambda: c.reader.helper(domain, identity["storage_object_id"], identity["entity_id"]),
        lambda item: item["state_status"] == 404)
    assert readback == {"stored": False, "registry_entity_id": None, "entity_id": identity["entity_id"],
                        "state_status": 404} and stored is None, readback
    return {"result": "PASS", **result, "delete_argument_is_captured_identity": True, "readback": readback,
            "readback_fingerprint": digest(readback)}


def _helper_scenarios(domain):
    async def create(c):
        plan, identity, value = await created_helper(c, domain, "toggle" if domain == "input_boolean" else "target")
        c.state[domain] = (plan, identity)
        return value

    async def undo(c):
        plan, identity = c.state[domain]
        rolled, reverse = await undo_plan(c, plan["plan_id"], delete=["create_helper"])
        return {**await deleted_helper(c, reverse, identity, [domain + "/delete"]),
                "rollback_status": rolled["status"]}
    return create, undo


async def mixed_plan_create(c):
    """One plan creates a helper and updates a consumer that then references it."""
    target_id = "hamcp_g4_consumer_" + c.suffix
    c.owned["automations"].append(target_id)
    original = await c.add_automation(target_id)
    operation, object_id = helper_operation("input_boolean", "HAMCP G4 mixed " + c.suffix)
    proposed = {**original, "description": "Uses input_boolean." + object_id}
    update = {"operation_id": "update_consumer", "resource_type": "automation", "action": "update",
              "target_id": target_id, "depends_on": ["create_helper"], "proposed_config": proposed}
    plan, identity, value = await created_helper(c, "input_boolean", "mixed", update)
    assert (await c.reader.automation(target_id)).get("description") == proposed["description"]
    c.state["mixed"] = (plan, identity, target_id, original)
    return {**value, "consumer_updated": True}


async def mixed_plan_undo(c):
    """The reverse plan restores the consumer first, then deletes the helper it no longer uses."""
    plan, identity, target_id, original = c.state["mixed"]
    rolled, reverse = await undo_plan(c, plan["plan_id"], delete=["create_helper"], restore=1)
    order = [[item.resource_type, item.action] for item in c.service._load(reverse["plan_id"]).operations]
    assert order == [["automation", "update"], ["helper", "delete"]], order
    value = await deleted_helper(c, reverse, identity, [AUTOMATION_POST, "input_boolean/delete"])
    assert await c.reader.automation(target_id) == original
    return {**value, "rollback_status": rolled["status"], "reverse_order": order, "consumer_restored": True}


SCENARIOS = {"scene_create": scene_create, "scene_undo": scene_undo, "scene_recreate": scene_recreate,
             "scene_existing_id_refused": scene_existing_id_refused, "scene_recreate_undo": scene_recreate_undo,
             "mixed_plan_create": mixed_plan_create, "mixed_plan_undo": mixed_plan_undo}
for _domain in HELPER_DOMAINS:
    SCENARIOS["helper_create_" + _domain], SCENARIOS["helper_undo_" + _domain] = _helper_scenarios(_domain)


async def run_scenarios(lane, c):
    scenarios = {}
    for name in LANES[lane]["scenarios"]:
        scenarios[name] = await SCENARIOS[name](c)
    return scenarios


async def cleanup_fixtures(raw, owned, *, attempts=50, delay=0.1):
    """Remove the owned automations and helpers with the lane's own client, then read back absence.

    No scene is removed here: a scene config DELETE belongs to the created-object
    delete gateway alone. A scene the governed undo did not remove is reported
    as present and stays in the disposable Core until the caller's teardown.
    """
    for target_id in owned["automations"]:
        await raw.status("DELETE", automation_route(target_id))
    for domain, object_id in owned["helpers"]:
        await raw.call({"type": domain + "/delete", domain + "_id": object_id})

    async def scenes():
        return all([(await raw.status("GET", scene_route(item)))[0] == 404
                    for item in owned["scenes"]])

    async def automations():
        return all([(await raw.status("GET", automation_route(item)))[0] == 404
                    for item in owned["automations"]])

    async def helpers():
        listed = {domain: {item.get("id") for item in await raw.ok({"type": domain + "/list"})}
                  for domain in {domain for domain, _ in owned["helpers"]}}
        return not any(object_id in listed[domain] for domain, object_id in owned["helpers"])

    async def registry():
        ids = set(owned["scenes"]) | set(owned["automations"]) | {item for _, item in owned["helpers"]}
        return not any(item.get("unique_id") in ids for item in await raw.ok({"type": REGISTRY_LIST}))

    absence = {}
    for name, check in (("scenes", scenes), ("automations", automations), ("helpers", helpers),
                        ("registry_entries", registry)):
        for _ in range(attempts):
            absent = await check()
            if absent:
                break
            await asyncio.sleep(delay)
        absence[name] = absent
    return absence


def _forward_and_undo(scenarios, forward, undo, writes):
    assert scenarios[forward]["task_state"] == VERIFIED and scenarios[undo]["task_state"] == VERIFIED
    assert scenarios[forward]["writes"] == writes[0] and scenarios[undo]["writes"] == writes[1]
    for name in (forward, undo):
        value = scenarios[name]
        assert value["result"] == "PASS" and value["children"]
        assert all(child[1:] == [VERIFIED, 1] for child in value["children"]), value["children"]
        fingerprint = value["readback_fingerprint"]
        assert type(fingerprint) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint)
    assert scenarios[forward]["identity_outcome"] == "captured"
    assert scenarios[undo]["rollback_status"] == "rollback_plan_created"


def verify_result(lane, result):
    """Strict re-check of one serialized lane receipt against current source."""
    spec = LANES[lane]
    scenarios = result["scenarios"]
    assert result["result"] == "PASS" and result["model"] == MODEL and result["lane"] == lane
    assert result["core"] == CORE_VERSION and result["production_authority"] is False
    assert result["profile"] == reference(lane) and result["profile"]["contract_fingerprint"] == spec["fingerprint"]
    assert result["authority_transition"] == [SIGNED_PREDECESSOR_COUNT, SIGNED_PREDECESSOR_COUNT + 1]
    assert result["engineering_source"] == engineering_source()
    assert sorted(scenarios) == sorted(spec["scenarios"])  # A serialized receipt carries no order.
    assert set(result["engineering_write_kinds"]) <= spec["writes"], result["engineering_write_kinds"]
    assert result["fixture_absence"] == dict.fromkeys(("scenes", "automations", "helpers", "registry_entries"), True)
    assert result["authority_leases_released"] is True
    before = result["predecessor_admission"]
    assert before["profile_available"] is False
    assert before["undo_writes"] == [] and before["control_capability_acquired"] is True
    if lane == "scene_create_delete":
        assert before["reason_code"] == PENDING
        assert before["route_acquired"] is False and before["ha_requests"] == 0 and before["plan_created"] is False
        _forward_and_undo(scenarios, "scene_create", "scene_undo", ([SCENE_POST], [SCENE_DELETE]))
        assert scenarios["scene_create"]["members_unchanged"] is True
        assert scenarios["scene_create"]["readback"]["state_status"] == 200
        assert scenarios["scene_undo"]["readback"] == {
            "config_status": 404, "registry_entity_id": None, "state_status": 404,
            "entity_id": scenarios["scene_create"]["readback"]["entity_id"]}
        recreate = scenarios["scene_recreate"]
        assert recreate["task_state"] == VERIFIED and recreate["writes"] == [SCENE_POST]
        assert recreate["same_config_id"] is True and recreate["config_matches_first_create"] is True
        assert recreate["readback_fingerprint"] == scenarios["scene_create"]["readback_fingerprint"]
        refused = scenarios["scene_existing_id_refused"]
        assert refused["writes"] == [] and refused["plan_created"] is False
        assert type(refused["reason_code"]) is str and refused["reason_code"]
        assert refused["existing_scene_unchanged"] is True
        # The recreate is undone like the first create; nothing else removes a scene.
        _forward_and_undo(scenarios, "scene_recreate", "scene_recreate_undo", ([SCENE_POST], [SCENE_DELETE]))
        assert scenarios["scene_recreate_undo"]["readback"] == {
            "config_status": 404, "registry_entity_id": None, "state_status": 404,
            "entity_id": recreate["readback"]["entity_id"]}
    else:
        assert before["forward_task_state"] == VERIFIED and before["helper_retained"] is True
        assert before["identity_captured"] is False and before["identity_outcome"] == "capability_unadmitted"
        assert before["reason_code"] == NO_ELIGIBLE and before["provider_dispatch_occurred"] is False
        assert before["operations_excluded"] == ["created_object_identity_not_captured"]
        for domain in HELPER_DOMAINS:
            create, undo = "helper_create_" + domain, "helper_undo_" + domain
            _forward_and_undo(scenarios, create, undo, ([domain + "/create"], [domain + "/delete"]))
            assert scenarios[undo]["delete_argument_is_captured_identity"] is True
            assert scenarios[undo]["readback"] == {
                "stored": False, "registry_entity_id": None, "state_status": 404,
                "entity_id": scenarios[create]["readback"]["entity_id"]}
        _forward_and_undo(scenarios, "mixed_plan_create", "mixed_plan_undo",
                          (["input_boolean/create", AUTOMATION_POST], [AUTOMATION_POST, "input_boolean/delete"]))
        mixed = scenarios["mixed_plan_undo"]
        assert mixed["reverse_order"] == [["automation", "update"], ["helper", "delete"]]
        assert mixed["consumer_restored"] is True and mixed["delete_argument_is_captured_identity"] is True
    kinds = sorted({name.removeprefix("helper_create_") for name in scenarios if name.startswith("helper_create_")}
                   or {"scene"})
    return {"lane": lane, "profile_fingerprint": spec["fingerprint"], "kinds": kinds,
            "scenarios": list(scenarios), "result": "PASS"}


async def run(lane, configured, entry, directory):
    """Run one lane against an owned loopback Core; the caller owns start-up, identity and teardown."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from alarmo_inspection_contract_acceptance import close_disposable_authority
    from prepare_core_release_registry import prepare_candidate, sign_candidate
    import ha_mcp_engineering.dependency as dependency_package
    from ha_mcp_engineering.audit import AuditLogger
    from ha_mcp_engineering.clients import HomeAssistantRestClient, HomeAssistantWebSocketClient
    from ha_mcp_engineering.dependency.runtime import DependencyAnalysisRuntime
    from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration
    from ha_mcp_engineering.governance.entity_metadata_disclosure import (
        read_cached_dependency_snapshot, read_runtime_dependency_snapshot, runtime_consumer_evidence)
    from ha_mcp_engineering.governance.helper_state import HelperStateGateway
    from ha_mcp_engineering.governance.resources import ConfigurationResourceGateway
    from ha_mcp_engineering.governance.runtime import _RuntimeGovernanceGateway
    from ha_mcp_engineering.governance.service import ChangeGovernanceService
    from ha_mcp_engineering.governance.storage import ChangePlanRepository
    from ha_mcp_engineering.ha_core_readmission import CoreRuntime
    from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
    from ha_mcp_engineering.ha_core_readmission.routes import static_tool_requirements, static_tool_target
    from ha_mcp_engineering.request_context import begin_request, end_request
    from ha_mcp_engineering.signed_registry import canonical_json

    spec = LANES[lane]
    added = reference(lane)
    old = entry["capabilities"]
    if (entry["version"] != CORE_VERSION or len(old) != SIGNED_PREDECESSOR_COUNT
            or spec["capability"] in {item["capability_id"] for item in old}
            or urlsplit(configured.ha_url).hostname not in {"127.0.0.1", "localhost", "::1"}):
        raise ValueError("Exact owned disposable created-object lane required")
    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    evidence = ("Disposable " + lane + " test authority; not registry publication authority").encode()
    root = Path(directory)
    runtimes = []
    log = RequestLog()
    engineering_rest = ObservedRest(HomeAssistantRestClient(configured), log)
    engineering_ws = ObservedWebSocket(HomeAssistantWebSocketClient(configured), log)
    raw = LaneRaw(configured)
    reader = Reader(raw.status, raw.ok)

    @diagnostics.checked("helper_setup", "authority_setup")
    async def authority(references, name):
        test_entry = {**entry, "capabilities": references, "evidence_sha256": hashlib.sha256(evidence).hexdigest()}
        candidate = canonical_json(prepare_candidate(entry=test_entry, evidence=evidence, previous=None,
                                                     public_key=key.public_key(), now=now))
        payload = sign_candidate(candidate, expected_sha256=hashlib.sha256(candidate).hexdigest(),
                                 previous=None, key=key, now=now)

        async def fetch(_url, _maximum):
            return payload
        registry = CoreReleaseRegistry(enabled=True, public_key=base64.b64encode(
            key.public_key().public_bytes_raw()).decode(), fetcher=fetch, cache_path=root / (name + ".json"))
        assert await registry.refresh()
        runtime = CoreRuntime()
        runtimes.append(runtime)
        runtime.configure(configured, release_registry=registry)
        await runtime.reconcile_once("startup")
        assert runtime.current_observation.version == CORE_VERSION
        assert runtime.health_snapshot()["compatible_count"] == len(references)
        return runtime

    @diagnostics.checked("helper_setup", "provider_setup")
    async def compose(runtime, name):
        """The production composition (governance/runtime.py), one service per authority."""
        gateway = ConfigurationResourceGateway(engineering_rest, engineering_ws)
        gateway.entity_metadata_consumer_evidence = runtime_consumer_evidence()
        gateway.availability_exposure_snapshot_reader = read_runtime_dependency_snapshot
        gateway.scene_configuration.core_runtime = runtime
        gateway.scene_configuration.consumer_snapshot_reader = read_cached_dependency_snapshot
        gateway.scene_create.core_runtime = runtime
        gateway.scene_create.consumer_snapshot_reader = read_cached_dependency_snapshot
        gateway.created_object_delete.core_runtime = runtime
        helper_state = HelperStateGateway(engineering_rest, engineering_ws)
        audit_secret = "hamcp329-298-disposable-lane-audit"

        async def provider_identity():
            return dict(SYNTHETIC_PROVIDER_IDENTITY)
        service = ChangeGovernanceService(
            ChangePlanRepository(root / ("plans-" + name)),
            _RuntimeGovernanceGateway(engineering_rest, engineering_ws, gateway),
            AuditLogger(str(root / ("audit-" + name + ".jsonl")), audit_secret),
            sensitive_values=(audit_secret, configured.ha_token, configured.access_secret),
            helper_state_gateway=helper_state, entity_metadata_gateway=gateway.entity_metadata,
            entity_availability_exposure_gateway=gateway.availability_exposure,
            label_registry_gateway=gateway.label_registry,
            configuration_delete_gateway=gateway.configuration_delete,
            helper_options_gateway=gateway.helper_options,
            dashboard_metadata_gateway=gateway.dashboard_metadata,
            scene_configuration_gateway=gateway.scene_configuration,
            scene_create_gateway=gateway.scene_create,
            created_object_delete_gateway=gateway.created_object_delete)
        service.f3_runtime = F3RuntimeIntegration(
            service=service, storage_root=str(root / ("plans-" + name)), configuration_gateway=gateway,
            backup_gateway=None, lifecycle_gateway=None, helper_state_gateway=helper_state,
            provider_identity_reader=provider_identity, retention_days=90, core_runtime=runtime)
        await service.f3_runtime.recover_once("startup")
        return service

    async def routed(runtime, tool, arguments, call):
        """Mirror the MCP route: exact-target acquisition before the application."""
        telemetry, token = begin_request("hamcp329-298-lane-plan")
        telemetry.caller_id = CALLER
        commits = None
        lease = runtime.acquire(static_tool_requirements(tool, arguments), target=static_tool_target(tool, arguments))
        try:
            if lease is None:
                return None, telemetry.ha_request_count

            def authorize():
                nonlocal commits
                if commits is not None:
                    return runtime.revalidate(lease, commits)
                commits = runtime.consume(lease)
                return commits is not None
            telemetry.core_dispatch_authorizer = authorize
            return await call(), telemetry.ha_request_count
        finally:
            if lease is not None:
                telemetry.core_dispatch_authorizer = lambda: False
                if commits is not None:
                    assert runtime.finish(commits)
                else:
                    runtime.release(lease)
            end_request(token)

    def planners(runtime, service):
        async def scene_plan(request):
            created, _ = await routed(runtime, SCENE_TOOL, request,
                                      lambda: service.create_scene_configuration_plan(dict(request)))
            assert created is not None and created.get("plan_id")
            return created

        async def configuration_plan(operations):
            arguments = {"title": "HAMCP G4 created-object lane", "description": "Synthetic governed create",
                         "operations": list(operations)}
            created, _ = await routed(runtime, "create_configuration_plan", arguments,
                                      lambda: service.create_configuration_plan(**arguments))
            assert created is not None and created.get("plan_id")
            return created
        return scene_plan, configuration_plan

    async def add_automation(target_id):
        config = {"alias": "HAMCP G4 consumer " + target_id[-8:], "description": "Synthetic consumer",
                  "triggers": [{"trigger": "event", "event_type": "hamcp_g4_noop"}], "conditions": [],
                  "actions": [{"event": "hamcp_g4_noop_done", "event_data": {}}], "mode": "single"}
        status, _ = await raw.status("POST", automation_route(target_id), config)
        if status != 200:
            raise RawRefusal("POST automation", str(status))
        for _ in range(100):
            if await reader.registry("automation", target_id):
                return await reader.automation(target_id)
            await asyncio.sleep(0.1)
        raise AssertionError("Synthetic consumer did not appear")

    def context(runtime, service):
        scene_plan, configuration_plan = planners(runtime, service)
        return Context(service=service, log=log, reader=reader, scene_plan=scene_plan,
                       configuration_plan=configuration_plan, add_automation=add_automation,
                       members=("input_boolean.hamcp_g4_lamp", "input_boolean.hamcp_g4_strip"))

    dependency_runtime = DependencyAnalysisRuntime()
    contexts = []
    fixture_absence = None
    try:
        with diagnostics.check("operation", "negative_controls"):
            predecessor = await authority(old, "predecessor")
            control = predecessor.acquire(("core.dependency_helper_planning",))
            assert control is not None
            predecessor.release(control)
            assert predecessor.route_status((spec["capability"],))["available"] is False
            dependency_runtime.configure(HomeAssistantRestClient(configured), HomeAssistantWebSocketClient(configured),
                                         secret="hamcp329-298-disposable-dependency-evidence",
                                         core_runtime=predecessor)
            withheld = await compose(predecessor, "predecessor")
            before = context(predecessor, withheld)
            contexts.append(before)
            admission = {"profile_available": False, "control_capability_acquired": True}
            with patch.object(dependency_package, "DEPENDENCY_ANALYSIS", dependency_runtime):
                if lane == "scene_create_delete":
                    # The MCP route does not acquire, and the service refuses on its own.
                    route, requests = await routed(
                        predecessor, SCENE_TOOL, scene_probe("hamcp_g4_withheld_route", before.members[0]),
                        lambda: None)
                    assert route is None and requests == 0

                    async def direct(probe):
                        nonlocal requests
                        telemetry, token = begin_request("hamcp329-298-predecessor-service")
                        try:
                            return await withheld.create_scene_configuration_plan(dict(probe))
                        finally:
                            requests += telemetry.ha_request_count
                            end_request(token)
                    admission.update(await withheld_scene_create(before, direct))
                    assert log.kinds == [] and requests == 0
                    admission.update(route_acquired=False)
                else:
                    admission.update(await withheld_helper_undo(before))
        await dependency_runtime.shutdown()

        runtime = await authority(old + [added], "extension")
        assert runtime.route_status((spec["capability"],))["available"] is True
        dependency_runtime = DependencyAnalysisRuntime()
        dependency_runtime.configure(HomeAssistantRestClient(configured), HomeAssistantWebSocketClient(configured),
                                     secret="hamcp329-298-disposable-dependency-evidence", core_runtime=runtime)
        service = await compose(runtime, "extension")
        admitted = context(runtime, service)
        contexts.append(admitted)
        with diagnostics.check("operation", "operation_dispatch"):
            with patch.object(dependency_package, "DEPENDENCY_ANALYSIS", dependency_runtime):
                scenarios = await run_scenarios(lane, admitted)
        released = all(item.health_snapshot()[name] == 0 for item in runtimes
                       for name in ("issued_lease_count", "active_commit_count", "fallback_count"))
        result = {
            "result": "PASS", "model": MODEL, "lane": lane, "core": CORE_VERSION, "profile": added,
            "engineering_source": engineering_source(),
            "predecessor_capabilities_sha256": hashlib.sha256(canonical_json(old)).hexdigest(),
            "authority_transition": [len(old), len(old) + 1],
            "authority_instances": "separate_ephemeral_registries", "production_authority": False,
            "provider_identity": "synthetic_disposable_lane", "predecessor_admission": admission,
            "engineering_write_kinds": sorted(set(log.writes_since(0))),
            "engineering_request_kinds": sorted(set(log.kinds)),
            "authority_leases_released": released, "scenarios": scenarios}
    finally:
        try:
            try:
                with diagnostics.check("cleanup", "cleanup_absence"):
                    owned = {name: [item for c in contexts for item in c.owned[name]]
                             for name in ("scenes", "automations", "helpers")}
                    fixture_absence = await cleanup_fixtures(raw, owned)
            finally:
                await dependency_runtime.shutdown()
        finally:
            with diagnostics.check("cleanup", "service_close"):
                for item in runtimes:
                    await close_disposable_authority(item)
    result["fixture_absence"] = fixture_absence
    # Verify the serialized evidence through the strict loader, not the in-memory value.
    result["verification"] = verify_result(lane, load_receipt(receipt_bytes(result)))
    return result
