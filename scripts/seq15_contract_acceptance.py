"""HAMCP-269 five-profile exact-Core evidence for 2026.10.1 and 2026.10.3.

The disposable harness calls these lanes only when REAL_HA_SEQ15_LANES is
enabled and the Core matches one of the two exact bindings. No live CLI.

Each lane exercises one dormant profile against the exact window Core under
two CI-only ephemeral authorities: a predecessor holding the seq-14 signed
release set without it (must refuse before Home Assistant) and an extension
with the lane's profile appended. The production signed registry is never read
for authority, changed or signed; its committed exact-release entry is only
compared with the predecessor. Every Home Assistant object a lane writes is
synthetic and created inside the disposable instance, then deleted and read
back as absent by the lane's own raw client; the governed paths never delete.
"""
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
from entity_metadata_contract_acceptance import (  # noqa: E402
    REFERENCE_KEYS, RawRefusal, RawWebSocket, engineering_source, load_receipt, receipt_bytes)
from seq13_contract_acceptance import NOVEMBER_DORMANT  # noqa: E402
from ha_mcp_engineering.governance.dashboard_metadata import (  # noqa: E402
    DEFAULT_DASHBOARD_URL_PATH, FIELDS as DASHBOARD_FIELDS, LIST_COMMAND as DASHBOARD_LIST,
    NEVER_SENT_COMMANDS as DASHBOARD_NEVER_SENT, UPDATE_COMMAND as DASHBOARD_UPDATE)
from ha_mcp_engineering.governance.dashboard_metadata_planning import NO_CHANGE as DASHBOARD_NO_CHANGE  # noqa: E402
from ha_mcp_engineering.governance.helper_options import NEVER_SENT as HELPER_NEVER_SENT  # noqa: E402
from ha_mcp_engineering.governance.input_select_option import NEVER_SENT_SERVICES  # noqa: E402
from ha_mcp_engineering.script_traces import NEVER_SENT_COMMANDS  # noqa: E402

ENABLE_FLAG = "REAL_HA_SEQ15_LANES"
ENABLED = "enabled"
# Both October releases retain disposable evidence; Josh selected only .3 for
# production sequence 15. The lane transition always starts from sequence 14.
TARGET_CORE = "2026.10.3"  # Default for historical offline receipt fixtures only.
TARGET_CORES = frozenset({"2026.10.1", "2026.10.3"})
HELPER_OPTIONS = "core.config_entry_helper_options_operation"
INPUT_SELECT = "core.typed_input_select_option_operation"
SCRIPT_TRACE = "core.script_trace_read"
DASHBOARD_METADATA = "core.dashboard_metadata_operation"
SCENE = "core.scene_configuration_operation"
SEQ15_DORMANT = frozenset({HELPER_OPTIONS, INPUT_SELECT, SCRIPT_TRACE, DASHBOARD_METADATA, SCENE})
# Compatibility name for older fixture imports; all five join this batch.
SEQ16_DORMANT = frozenset()
assert not SEQ15_DORMANT & SEQ16_DORMANT and SEQ15_DORMANT | SEQ16_DORMANT == NOVEMBER_DORMANT
# The seq-14 signed 2026.10.3 entry (upstream-trust/ha-core-release-registry.json).
PREDECESSOR_COUNT = 26
SIGNED_REGISTRY = ROOT / "upstream-trust" / "ha-core-release-registry.json"
PENDING = "pending_signed_registry_admission"
STALE = "stale_target_state"

# Engineering's observed requests, as "METHOD /route/{}" (REST) or the WebSocket type;
# a call_service is recorded as "call_service:<domain>.<service>".
OPTIONS_OPEN = "POST /config/config_entries/options/flow"
OPTIONS_SUBMIT = "POST /config/config_entries/options/flow/{}"
OPTIONS_ABORT = "DELETE /config/config_entries/options/flow/{}"
STATE_READ = "GET /states/{}"
ENTITY_GET, REGISTRY_LIST = "config/entity_registry/get", "config/entity_registry/list"
CONFIG_ENTRY_GET = "config_entries/get_single"
SELECT_OPTION = "call_service:input_select.select_option"
TRACE_LIST, TRACE_GET = "trace/list", "trace/get"
# The admitted get_dashboard_config read's own Core command (ha-mcp sends it with force).
LOVELACE_CONFIG = "lovelace/config"
# Only these count as Home Assistant writes in a lane's Engineering clients.
SCENE_GET = "GET /config/scene/config/{}"
SCENE_POST = "POST /config/scene/config/{}"
WRITE_REQUESTS = frozenset({OPTIONS_SUBMIT, SELECT_OPTION, DASHBOARD_UPDATE, SCENE_POST})
FIXTURE_ABSENCE = ("config_entries", "entities", "input_select", "script", "dashboard", "scene")
TRACE_SECRET_KEY = "seq15_probe"
LONG_VALUE = 5_000


def _lane(key, *, capability, adapter, tool, output_env, reviewed, required, never_sent, scenarios,
          predecessor_probe, predecessor_reason):
    return {"key": key, "capabilities": (capability,), "adapters": (adapter,), "tool": tool,
            "output_env": output_env, "reviewed_commands": frozenset(reviewed),
            "required_commands": frozenset(required), "never_sent": frozenset(never_sent),
            "scenarios": scenarios, "predecessor_probe": predecessor_probe,
            "predecessor_reason": predecessor_reason,
            "authority_transition": [PREDECESSOR_COUNT, PREDECESSOR_COUNT + 1]}


HELPER_TYPES = ("filter", "statistics", "template_sensor", "template_binary_sensor")
LANES = {
    "helper_options": _lane(
        "helper_options", capability=HELPER_OPTIONS,
        adapter="compiled-core-config-entry-helper-options-operation-v1",
        tool="create_helper_options_plan", output_env="REAL_HA_HELPER_OPTIONS_RESULT",
        reviewed={OPTIONS_OPEN, OPTIONS_SUBMIT, OPTIONS_ABORT, STATE_READ, ENTITY_GET, REGISTRY_LIST,
                  CONFIG_ENTRY_GET},
        required={OPTIONS_OPEN, OPTIONS_SUBMIT, OPTIONS_ABORT, ENTITY_GET, CONFIG_ENTRY_GET},
        # The gateway's own never-sent list; its "call_service" covers every service call.
        # The config-flow start is a CI-harness-only route the runtime never names.
        never_sent=(*HELPER_NEVER_SENT, "POST /config/config_entries/flow"),
        scenarios=(*HELPER_TYPES, "suggested_value", "field_not_editable", "validation_would_fail",
                   "stale_state", "concurrent_flow"),
        predecessor_probe={"changes": [{"entity_id": "sensor.seq15_absent", "set": {"precision": 1}}],
                           "reason": "Seq-15 predecessor probe"},
        predecessor_reason=PENDING),
    "input_select_option": _lane(
        "input_select_option", capability=INPUT_SELECT,
        adapter="compiled-core-typed-input-select-option-operation-v1",
        tool="create_input_select_option_plan", output_env="REAL_HA_INPUT_SELECT_OPTION_RESULT",
        reviewed={STATE_READ, SELECT_OPTION}, required={STATE_READ, SELECT_OPTION},
        never_sent={"call_service:input_select." + name for name in NEVER_SENT_SERVICES}
        | {"input_select/update", "input_select/create", "input_select/delete"},
        scenarios=("flip", "rollback", "already_desired", "option_not_available", "options_changed"),
        predecessor_probe={"entity_id": "input_select.seq15_absent", "option": "Away",
                           "reason": "Seq-15 predecessor probe"},
        predecessor_reason=PENDING),
    "script_traces": _lane(
        "script_traces", capability=SCRIPT_TRACE, adapter="compiled-core-script-trace-read-v1",
        tool="list_script_traces", output_env="REAL_HA_SCRIPT_TRACES_RESULT",
        reviewed={TRACE_LIST, TRACE_GET}, required={TRACE_LIST, TRACE_GET},
        never_sent=NEVER_SENT_COMMANDS,
        scenarios=("list", "get", "not_found", "invalid_arguments"),
        predecessor_probe={"script_id": "seq15_absent"},
        # The trace service refuses on its own Core status before any request.
        predecessor_reason="authority_unavailable"),
    "dashboard_metadata": _lane(
        "dashboard_metadata", capability=DASHBOARD_METADATA,
        adapter="compiled-core-dashboard-metadata-operation-v1",
        tool="create_dashboard_metadata_plan", output_env="REAL_HA_DASHBOARD_METADATA_RESULT",
        # The list read and the one update, plus the content-fingerprint read (DashboardContentRead).
        reviewed={DASHBOARD_LIST, DASHBOARD_UPDATE, LOVELACE_CONFIG},
        required={DASHBOARD_LIST, DASHBOARD_UPDATE, LOVELACE_CONFIG},
        never_sent=DASHBOARD_NEVER_SENT,
        scenarios=("title", "icon", "icon_clear", "show_in_sidebar", "require_admin", "all_fields", "rollback",
                   "no_change", "default_dashboard", "content_unchanged", "stale_state", "content_drift",
                   "unknown_id", "immutable_fields"),
        predecessor_probe={"url_path": "seq15-absent", "title": "x"},
        predecessor_reason=PENDING),
}
# Every lane now has a required evidence path and artifact entry.
WORKFLOW_PENDING_LANES = frozenset()
DASHBOARD_EDITS = ("title", "icon", "icon_clear", "show_in_sidebar", "require_admin", "all_fields")
# Stored exactly: the trailing space must survive the update and the readback.
DASHBOARD_TITLE = "Seq15 Map "
DASHBOARD_DEFAULT_REFUSED = "dashboard_metadata_default_dashboard_refused"
DASHBOARD_CONFIG = {"title": "Seq15", "views": [{"title": "Seq15", "path": "seq15", "cards": [
    {"type": "markdown", "content": "Seq15 synthetic dashboard"}]}]}
# Closed loopback routes the lane's raw client may use for setup, raw drift and cleanup only.
RAW_ROUTE = re.compile(
    r"/config/config_entries/(?:flow|flow/[0-9A-Za-z]{1,64}|entry/[0-9A-Za-z]{1,64}"
    r"|options/flow|options/flow/[0-9a-f]{32})"
    r"|/config/script/config/seq15_[0-9a-f]{8}"
    r"|/config/scene/config/(?:seq15_[0-9a-f]{8}|[0-9a-f]{32})"
    r"|/states/(?:input_select|binary_sensor|script|scene)\.seq15_[0-9a-z_]{1,64}")
_FLOW_ID = re.compile(r"/[0-9a-f]{32}\Z")


def enabled():
    """The single wiring switch; anything but the exact value keeps the lanes off."""
    return os.environ.get(ENABLE_FLAG) == ENABLED


def source_profiles(lane):
    from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
    by_id = {p.capability_id: p for p in CORE_RUNTIME_CAPABILITY_PROFILES}
    references = []
    for capability, adapter in zip(lane["capabilities"], lane["adapters"]):
        reference = {k: by_id[capability].to_mapping()[k] for k in REFERENCE_KEYS}
        if reference["adapter_id"] != adapter:
            raise ValueError("Seq-15 adapter differs from the reviewed lane")
        references.append(reference)
    return references


def predecessor_references(core_version=TARGET_CORE):
    """The exact ordered seq-14 release set, excluding the five additions.

    Compared, never trusted: it must equal the retained signed sequence-14 entry.
    """
    from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
    refs = [{k: p.to_mapping()[k] for k in REFERENCE_KEYS} for p in CORE_RUNTIME_CAPABILITY_PROFILES
            if p.capability_id not in SEQ15_DORMANT | SEQ16_DORMANT]
    assert len(refs) == PREDECESSOR_COUNT and len({r["capability_id"] for r in refs}) == len(refs)
    registry = json.loads(SIGNED_REGISTRY.read_text())
    assert core_version in TARGET_CORES
    (predecessor,) = [e for e in registry["envelopes"] if e["sequence"] == 14]
    (signed,) = [entry for entry in predecessor["entries"] if entry["version"] == core_version]

    def key(reference):
        return reference["capability_id"]
    assert sorted(refs, key=key) == sorted(signed["capabilities"], key=key)
    # Preserve the actual signed order, never the order of a reconstructed set.
    return signed["capabilities"]


def observed_rest_route(method, path):
    """Name one REST request by its reviewed template; ids never enter evidence."""
    path = str(path)
    if path.startswith("/states/"):
        return method + " /states/{}"
    if path.startswith("/config/scene/config/"):
        return method + " /config/scene/config/{}"
    if path.startswith("/config/config_entries/entry/"):
        tail = path.removeprefix("/config/config_entries/entry/").split("/", 1)
        return method + " /config/config_entries/entry/{}" + ("/" + tail[1] if len(tail) == 2 else "")
    return method + " " + _FLOW_ID.sub("/{}", path)


def observed_ws_type(payload):
    kind = payload.get("type")
    if kind == "call_service":
        return "call_service:" + str(payload.get("domain")) + "." + str(payload.get("service"))
    return kind


def _governed_success(value, writes):
    assert value["task_state"] == "succeeded_verified" and value["dispatch_counts"] == [1] * writes
    assert value["ha_writes"] == writes and value["readback"] == value["expected"]


def _stale(value):
    assert value["task_state"] == "failed_pre_dispatch" and value["dispatch_counts"] == [0]
    assert value["ha_writes"] == 0 and STALE in value["diagnostic_codes"]
    assert value["drift_retained"] is True


def _refused(value, reason, *, commands=None):
    assert value["plan_created"] is False and value["ha_writes"] == 0 and value["reason_code"] == reason
    if commands is not None:
        assert value["ha_commands"] == commands


def _helper_options_checks(name, value):
    if name in HELPER_TYPES:
        _governed_success(value, 1)
        assert value["write_types"] == [OPTIONS_SUBMIT] and value["expected"] != value["baseline"]
        rollback = value["rollback"]
        assert rollback["status"] == "rollback_plan_created" and rollback["defaults_not_restored"] == []
        _governed_success(rollback, 1)
        assert rollback["write_types"] == [OPTIONS_SUBMIT] and rollback["readback"] == value["baseline"]
    if name == "suggested_value":
        # Core's serializer carried every suggested value the planner disclosed (§2.3).
        assert value["created"] == value["disclosed_baseline"] == value["raw_baseline"]
    if name == "field_not_editable":
        _refused(value, "helper_options_field_not_editable", commands=0)
        assert value["error_code"] == "invalid_request"
    if name == "validation_would_fail":
        _refused(value, "helper_options_validation_would_fail")
    if name == "stale_state":
        _stale(value)
    if name == "concurrent_flow":
        # The OD-7 residual window, observed and disclosed: an older flow overwrites.
        _governed_success(value, 1)
        assert value["od7_overwrite_observed"] is True
        assert value["rollback_reason"] == "helper_options_rollback_baseline_drift"
        assert value["disclosed_window"] == "accepted_od_7"


def _input_select_checks(name, value):
    if name in {"flip", "rollback"}:
        _governed_success(value, 1)
        assert value["write_types"] == [SELECT_OPTION]
    if name == "rollback":
        assert value["status"] == "rollback_plan_created" and value["expected"] == value["prior"]
    if name == "already_desired":
        assert value == {"result": "PASS", "status": "no_change", "outcome": "already_desired",
                         "plan_created": False, "ha_writes": 0}
    if name == "option_not_available":
        _refused(value, "option_not_available")
    if name == "options_changed":
        _stale(value)


def _script_trace_checks(name, value):
    if name == "list":
        assert value["traces"] >= 1 and value["run_listed"] is True
        assert value["sanitization_failed_closed"] is False
    if name == "get":
        assert value["run_matches"] is True and value["secret_absent"] is True
        assert value["redacted"] is True and value["long_value_bounded"] is True
    if name == "not_found":
        assert value["reason"] == "trace_not_found" and value["error_code"] == "resource_not_found"
        assert value["ha_commands"] == 1
    if name == "invalid_arguments":
        assert value["reason"] == "invalid_arguments" and value["ha_commands"] == 0


def _dashboard_metadata_checks(name, value):
    if name in {*DASHBOARD_EDITS, "rollback"}:
        _governed_success(value, 1)
        assert value["write_types"] == [DASHBOARD_UPDATE] and value["expected"] != value["baseline"]
        # One update by the exact dashboard id carrying exactly the requested fields, each a real change.
        assert value["sent_fields"] == [value["requested_fields"]] and value["dashboard_ids_exact"] is True
        assert value["requested_fields"] == sorted(
            field for field in DASHBOARD_FIELDS if value["expected"][field] != value["baseline"][field])
    if name == "title":
        assert value["readback"]["title"] == DASHBOARD_TITLE
    if name == "icon_clear":
        assert value["expected"]["icon"] is None and value["icon_key_absent"] is True
    if name == "all_fields":
        assert value["requested_fields"] == sorted(DASHBOARD_FIELDS)
    if name == "rollback":
        # The reverse plan restores exactly the forward plan's changed fields.
        assert value["status"] == "rollback_plan_created" and value["expected"] == value["prior"]
        assert value["requested_fields"] == sorted(DASHBOARD_FIELDS)
    if name == "no_change":
        _refused(value, DASHBOARD_NO_CHANGE)
    if name == "default_dashboard":
        _refused(value, DASHBOARD_DEFAULT_REFUSED, commands=0)
        assert value["error_code"] == "invalid_request"
    if name == "content_unchanged":
        assert value["raw_config_unchanged"] is True and value["fingerprints_match"] is True
        assert type(value["fingerprint_reads"]) is int and value["fingerprint_reads"] >= 1
    if name in {"stale_state", "content_drift"}:
        _stale(value)
    if name == "unknown_id":
        assert value["error_code"] == "not_found" and value["inventory_unchanged"] is True
    if name == "immutable_fields":
        assert value["errors"] == dict.fromkeys(("url_path", "mode"), "invalid_format")
        assert value["row_unchanged"] is True


CHECKS = {"helper_options": _helper_options_checks, "input_select_option": _input_select_checks,
          "script_traces": _script_trace_checks, "dashboard_metadata": _dashboard_metadata_checks}


def verify_result(lane_key, result, *, core_version, support_slot):
    """Offline receipt verification against the window and current source.

    Refuses a receipt for another lane, Core, slot, image digest, adapter,
    compiled contract fingerprint or Engineering runtime tree. It does not
    substitute for execution.
    """
    from alarmo_inspection_contract_acceptance import slot_binding
    lane = LANES[lane_key]
    assert type(core_version) is str and type(support_slot) is str and core_version in TARGET_CORES
    binding = slot_binding(core_version)
    assert binding["slot"] == support_slot
    assert result["result"] == "PASS" and result["lane"] == lane_key and result["core"] == core_version
    assert type(result.get("support_slot")) is str and result["support_slot"] == support_slot
    assert result["core_image"] == binding["image"]
    assert result["image_index_digest"] == binding["image_index_digest"]
    assert len(result["image_receipt_sha256"]) == 64
    references = source_profiles(lane)
    assert result["added_capabilities"] == references
    assert result["engineering_source"]["runtime_tree"] == engineering_source()["runtime_tree"]
    assert len(result["engineering_source"]["commit"]) == 40
    assert result["authority_transition"] == lane["authority_transition"]
    assert result["authority_instances"] == "separate_ephemeral_registries"
    assert result["production_authority"] is False and result["fixture_cleanup"] is True
    assert result["fixture_absence"] == dict.fromkeys(FIXTURE_ABSENCE, True)
    assert result["provider_identity"] == "synthetic_disposable_lane"
    assert result["batch_health"] == {"compatible_count": 31, "withheld_count": 0}
    from ha_mcp_engineering.signed_registry import canonical_json
    assert result["predecessor_capabilities_sha256"] == hashlib.sha256(
        canonical_json(predecessor_references(core_version))).hexdigest()
    assert result["predecessor_admission"] == {
        "route_acquired": False, "route_available": False,
        "service_reason_code": lane["predecessor_reason"],
        "ha_requests": 0, "ha_writes": 0, "control_capability_acquired": True}
    commands = result["engineering_command_types"]
    assert type(commands) is list and commands == sorted(set(commands))
    assert set(commands) <= lane["reviewed_commands"]
    # "call_service" in a never-sent list names every service call.
    assert not {c for c in commands if c in lane["never_sent"] or c.split(":")[0] in lane["never_sent"]}
    assert lane["required_commands"] <= set(commands)
    scenarios = result["scenarios"]
    # Receipts are written with sorted keys, so the exact set is bound, not order.
    assert type(scenarios) is dict and sorted(scenarios) == sorted(lane["scenarios"])
    for name in lane["scenarios"]:
        assert scenarios[name]["result"] == "PASS"
        CHECKS[lane_key](name, scenarios[name])
    return {"result": "PASS", "lane": lane_key, "core": core_version, "support_slot": support_slot,
            "contract_fingerprints": [r["contract_fingerprint"] for r in references],
            "scenarios": len(lane["scenarios"]), "authority_transition": lane["authority_transition"],
            "fixture_cleanup": True}


def suggested_values(form):
    """Every suggested value a serialized options form carries, sections nested."""
    values = {}
    for item in form.get("data_schema") or []:
        if isinstance(item.get("schema"), list):
            nested = suggested_values({"data_schema": item["schema"]})
            if nested:
                values[item["name"]] = nested
        elif "suggested_value" in (item.get("description") or {}):
            values[item["name"]] = item["description"]["suggested_value"]
    return values


class LaneRaw(RawWebSocket):
    """The raw client plus the lane's closed loopback REST routes, for setup, drift and cleanup.

    Engineering never sends these through this client; only config-entry
    creation and deletion, options flows, one owned script's configuration and
    owned entities' states are reachable, and only on a loopback Core.
    """

    def __init__(self, configured):
        super().__init__(configured)
        self.api_url = configured.api_url.rstrip("/")
        self.token = configured.ha_token
        if urlsplit(self.api_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Seq-15 routes go only to a loopback Core")

    async def status(self, method, path, body=None):
        import aiohttp
        if RAW_ROUTE.fullmatch(path) is None:
            raise ValueError("Not a seq-15 lane route")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.request(method, self.api_url + path, json=body, allow_redirects=False,
                                       headers={"Authorization": "Bearer " + self.token}) as response:
                return response.status, (await response.json() if response.status < 300 else None)

    async def rest(self, method, path, body=None):
        status, value = await self.status(method, path, body)
        if status >= 300:
            # A closed route family only; no owned id or entity enters the diagnostic.
            raise RawRefusal(method + " " + (path.split("/")[2] if path.startswith("/config/") else "states"),
                             str(status))
        return value

    async def create_config_entry(self, handler, *steps):
        """Walk a config flow through each step's input; return the created entry id."""
        flow = await self.rest("POST", "/config/config_entries/flow",
                               {"handler": handler, "show_advanced_options": False})
        for user_input in steps:
            assert flow.get("type") in {"form", "menu"}
            flow = await self.rest("POST", "/config/config_entries/flow/" + flow["flow_id"], user_input)
        assert flow.get("type") == "create_entry"
        return flow["result"]["entry_id"]

    async def delete_config_entry(self, entry_id):
        await self.rest("DELETE", "/config/config_entries/entry/" + entry_id)

    async def open_options(self, entry_id):
        form = await self.rest("POST", "/config/config_entries/options/flow", {"handler": entry_id})
        assert form.get("type") == "form"
        return form

    async def options(self, entry_id):
        """Independent readback: open, read every suggested value, abort."""
        form = await self.open_options(entry_id)
        try:
            return suggested_values(form)
        finally:
            await self.rest("DELETE", "/config/config_entries/options/flow/" + form["flow_id"])

    async def submit_options(self, form, values):
        done = await self.rest("POST", "/config/config_entries/options/flow/" + form["flow_id"], values)
        assert done.get("type") == "create_entry"

    async def set_options(self, entry_id, **changes):
        """A raw options change made by something other than the plan under test."""
        form = await self.open_options(entry_id)
        await self.submit_options(form, {**suggested_values(form), **changes})


class DashboardContentRead:
    """Harness stand-in for the admitted ha-mcp ``get_dashboard_config`` read.

    The exact upstream starts only after the seq-15 lanes, so the lane sends
    ha-mcp's own read command (``lovelace/config`` with ``force``) through
    Engineering's observed client and hashes the complete raw configuration
    with Engineering's ``engineering_config_hash``. The production reader
    (``governance.runtime._dashboard_content_fingerprint_reader``) consumes it
    unchanged. Read-only: no other command is reachable from here.
    """

    def __init__(self, websocket, seen):
        self.websocket = websocket
        self.seen = seen

    async def get_dashboard_config(self, *, url_path, force_reload, response_limit):
        from ha_mcp_engineering.providers.upstream_dashboard import _engineering_config_hash
        config = await self.websocket.command({"type": LOVELACE_CONFIG, "url_path": url_path,
                                               "force": force_reload is True})
        if not isinstance(config, dict):
            raise ValueError("Dashboard content read is not a configuration")
        value = _engineering_config_hash(config)
        self.seen.append(value)
        return SimpleNamespace(data={"url_path": url_path, "engineering_config_hash": value})


class ObservedWebSocket:
    """Engineering's real client, observed; every write and command type counted."""

    def __init__(self, inner, log):
        self.inner = inner
        self.log = log

    async def command(self, payload):
        self.log.record(observed_ws_type(payload), payload)
        return await self.inner.command(payload)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class ObservedRest:
    """Engineering's real REST client, observed through the same log."""

    def __init__(self, inner, log):
        self.inner = inner
        self.log = log
        self.after_scene_save = None

    async def request(self, method, path, *args, **kwargs):
        self.log.record(observed_rest_route(method, path), None)
        result = await self.inner.request(method, path, *args, **kwargs)
        if method == "POST" and path.startswith("/config/scene/config/") and self.after_scene_save:
            callback, self.after_scene_save = self.after_scene_save, None
            await callback()
        return result

    def __getattr__(self, name):
        return getattr(self.inner, name)


class RequestLog:
    def __init__(self):
        self.command_types = []
        self.writes = []
        self.payloads = []

    def record(self, kind, payload):
        self.command_types.append(kind)
        if payload is not None:
            self.payloads.append(dict(payload))
        if kind in WRITE_REQUESTS:
            self.writes.append(kind)


async def cleanup_fixtures(raw, owned, *, attempts=50, delay=0.1):
    """Delete every owned fixture with the lane's raw client, then read back absence.

    Dependents go first (filter and statistics read the template source). An
    acknowledged delete is not evidence of absence; only an independent
    read-back is. Returns the per-type absence, or None when nothing was owned.
    """
    if not owned:
        return None
    for entry_id in reversed(owned.get("config_entries", [])):
        await raw.delete_config_entry(entry_id)
    if owned.get("input_select"):
        await raw.ok({"type": "input_select/delete", "input_select_id": owned["input_select"]})
    if owned.get("script"):
        await raw.rest("DELETE", "/config/script/config/" + owned["script"])
    if owned.get("dashboard"):
        await raw.ok({"type": "lovelace/dashboards/delete", "dashboard_id": owned["dashboard"]})
    for config_id in owned.get("scenes", []):
        await raw.rest("DELETE", "/config/scene/config/" + config_id)
    entries = set(owned.get("config_entries", []))

    async def config_entries_absent():
        return not entries & {item["entry_id"] for item in await raw.ok({"type": "config_entries/get"})}

    async def entities_absent():
        return not any(item.get("config_entry_id") in entries for item in await raw.ok({"type": REGISTRY_LIST}))

    async def input_select_absent():
        return not owned.get("input_select") or owned["input_select"] not in {
            item["id"] for item in await raw.ok({"type": "input_select/list"})}

    async def script_absent():
        return not owned.get("script") or (
            await raw.status("GET", "/config/script/config/" + owned["script"]))[0] == 404

    async def dashboard_absent():
        return not owned.get("dashboard") or owned["dashboard"] not in {
            item.get("id") for item in await raw.ok({"type": DASHBOARD_LIST})}

    async def scenes_absent():
        for config_id in owned.get("scenes", []):
            if (await raw.status("GET", "/config/scene/config/" + config_id))[0] != 404:
                return False
        return True

    checks = {"config_entries": config_entries_absent, "entities": entities_absent,
              "input_select": input_select_absent, "script": script_absent, "dashboard": dashboard_absent,
              "scene": scenes_absent}
    absence = {}
    for name in FIXTURE_ABSENCE:
        for _ in range(attempts):
            absent = await checks[name]()
            if absent:
                break
            await asyncio.sleep(delay)
        absence[name] = absent
    return absence


async def run_disposable(lane_key, configured, *, expected_image, core_version):
    """Preserve a bounded source location and stage on failure, never exception values."""
    stage = ["binding"]
    try:
        if not enabled():
            raise ValueError("Seq-15 lanes are defined but not wired")
        return await _run_disposable(LANES[lane_key], configured, expected_image=expected_image,
                                     core_version=core_version, stage=stage)
    except Exception as exc:
        cursor = exc.__traceback__
        lines = []
        while cursor is not None:
            if Path(cursor.tb_frame.f_code.co_filename).name in {"seq15_contract_acceptance.py", "seq15_scene_contract.py"}:
                lines.append({"file": Path(cursor.tb_frame.f_code.co_filename).name, "line": cursor.tb_lineno})
            cursor = cursor.tb_next
        exc.contract_missing_key = "seq15_" + str(lane_key if lane_key in LANES else "unknown")
        exc.contract_diagnostic = {"seq15_source_lines": lines[:12], "seq15_stage": stage[0]}
        if isinstance(exc, RawRefusal):
            exc.contract_diagnostic["raw_refusal"] = exc.diagnostic
        raise


async def _run_disposable(lane, configured, *, expected_image, core_version, stage):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from core_registry_contract_lane import lane_entry, probe_profile_for
    from prepare_core_release_registry import prepare_candidate, sign_candidate
    from alarmo_inspection_contract_acceptance import slot_binding, close_disposable_authority
    import ha_mcp_engineering.dependency as dependency_package
    from ha_mcp_engineering.audit import AuditLogger
    from ha_mcp_engineering.clients import HomeAssistantRestClient, HomeAssistantWebSocketClient
    from ha_mcp_engineering.dependency.runtime import DependencyAnalysisRuntime
    from ha_mcp_engineering.errors import GovernanceError
    from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration
    from ha_mcp_engineering.governance.entity_metadata_disclosure import (
        read_runtime_dependency_snapshot, runtime_consumer_evidence)
    from ha_mcp_engineering.governance.input_select_option import InputSelectGateway
    from ha_mcp_engineering.governance.resources import ConfigurationResourceGateway
    from ha_mcp_engineering.governance.runtime import _dashboard_content_fingerprint_reader
    from ha_mcp_engineering.governance.service import ChangeGovernanceService
    from ha_mcp_engineering.governance.storage import ChangePlanRepository
    from ha_mcp_engineering.request_context import begin_request, end_request
    from ha_mcp_engineering.ha_core_readmission import CoreRuntime
    from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
    from ha_mcp_engineering.ha_core_readmission.routes import static_tool_requirements, static_tool_target
    from ha_mcp_engineering.script_traces import ScriptTraceService
    from ha_mcp_engineering.signed_registry import canonical_json

    binding = slot_binding(core_version)
    entry = lane_entry(core_version)
    if (core_version not in TARGET_CORES
            or urlsplit(configured.ha_url).hostname not in {"127.0.0.1", "localhost", "::1"}
            or os.environ.get("HA_CONTRACT_LANE") != binding["lane"]
            or expected_image != binding["image"]
            or entry["image_index_digest"] != binding["image_index_digest"]):
        raise ValueError("Exact owned disposable seq-15 lane required")
    image_path = Path(os.environ["REAL_HA_ALARMO_IMAGE_RESULT"])
    image = load_receipt(image_path.read_bytes())
    assert (image["result"] == "PASS" and image["index"] == entry["image_index_digest"]
            and image["core_version"] == core_version and image["support_slot"] == binding["slot"])
    output = Path(os.environ[lane["output_env"]])
    probe_profile = probe_profile_for(core_version)
    added = source_profiles(lane)
    old = predecessor_references(core_version)
    new = old + added
    assert [len(old), len(new)] == lane["authority_transition"]
    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    evidence = ("Synthetic seq-15 " + lane["key"] + " lane; no production applicability").encode()
    entry.update(evidence_sha256=hashlib.sha256(evidence).hexdigest(),
                 probe_profile_id=probe_profile.profile_id,
                 probe_profile_sha256=probe_profile.contract_fingerprint)

    def signed(references):
        candidate = canonical_json(prepare_candidate(entry={**entry, "capabilities": references}, evidence=evidence,
                                                     previous=None, public_key=key.public_key(), now=now))
        return sign_candidate(candidate, expected_sha256=hashlib.sha256(candidate).hexdigest(),
                              previous=None, key=key, now=now)

    runtimes = []
    owned = {}
    tool = lane["tool"]
    caller = "seq15-" + lane["key"] + "-synthetic-owner"
    # A run-unique synthetic secret: placed in a trace, it must never leave the sanitizer.
    lane_secret = "seq15-" + secrets.token_hex(16)
    stage[0] = "authority"
    with tempfile.TemporaryDirectory(prefix="seq15-" + lane["key"] + "-") as directory:
        root = Path(directory)

        async def authority(references, name):
            payload = signed(references)

            async def fetch(_url, _maximum):
                return payload
            registry = CoreReleaseRegistry(enabled=True, public_key=base64.b64encode(
                key.public_key().public_bytes_raw()).decode(), fetcher=fetch, cache_path=root / (name + ".json"))
            assert await registry.refresh()
            runtime = CoreRuntime()
            runtimes.append(runtime)
            runtime.configure(configured, release_registry=registry)
            await runtime.reconcile_once("startup")
            assert runtime.health_snapshot()["compatible_count"] == len(references)
            return runtime

        log = RequestLog()
        engineering_ws = ObservedWebSocket(HomeAssistantWebSocketClient(configured), log)
        rest = ObservedRest(HomeAssistantRestClient(configured), log)
        raw = LaneRaw(configured)
        service = None
        # Every content fingerprint Engineering bound or rechecked, in order.
        content_seen = []

        def compose(runtime):
            resource_gateway = ConfigurationResourceGateway(rest, engineering_ws)
            # The production composition (governance/runtime.py), one service per authority.
            resource_gateway.entity_metadata_consumer_evidence = runtime_consumer_evidence()
            resource_gateway.availability_exposure_snapshot_reader = read_runtime_dependency_snapshot
            resource_gateway.dashboard_metadata.core_runtime = runtime
            resource_gateway.scene_configuration.core_runtime = runtime
            from ha_mcp_engineering.governance.entity_metadata_disclosure import read_cached_dependency_snapshot
            resource_gateway.scene_configuration.consumer_snapshot_reader = read_cached_dependency_snapshot
            resource_gateway.dashboard_metadata.content_reader = _dashboard_content_fingerprint_reader(
                DashboardContentRead(engineering_ws, content_seen), configured.response_size_limit)
            audit_secret = "seq15-" + lane["key"] + "-disposable-audit"
            input_select_gateway = InputSelectGateway(rest, engineering_ws)
            composed = ChangeGovernanceService(
                ChangePlanRepository(root / ("plans-" + str(len(runtimes)))), resource_gateway,
                AuditLogger(str(root / ("audit-" + str(len(runtimes)) + ".jsonl")), audit_secret),
                sensitive_values=(audit_secret, configured.ha_token, configured.access_secret),
                entity_metadata_gateway=resource_gateway.entity_metadata,
                entity_availability_exposure_gateway=resource_gateway.availability_exposure,
                label_registry_gateway=resource_gateway.label_registry,
                input_select_gateway=input_select_gateway,
                helper_options_gateway=resource_gateway.helper_options,
                dashboard_metadata_gateway=resource_gateway.dashboard_metadata,
                scene_configuration_gateway=resource_gateway.scene_configuration)

            async def provider_identity():
                return {"slug": "seq15_disposable_lane",
                        "evidence_hash": hashlib.sha256(b"Seq-15 synthetic provider lock").hexdigest()}
            composed.f3_runtime = F3RuntimeIntegration(
                service=composed, storage_root=str(root / ("plans-" + str(len(runtimes)))),
                configuration_gateway=resource_gateway, backup_gateway=None, lifecycle_gateway=None,
                input_select_gateway=input_select_gateway,
                provider_identity_reader=provider_identity, retention_days=90, core_runtime=runtime)
            return composed

        def traces(runtime):
            # The production composition (script_traces.ScriptTraceRuntime) plus the lane secret.
            return ScriptTraceService(engineering_ws, runtime, known_secrets=(
                configured.access_secret, configured.ha_token, lane_secret))

        async def routed(runtime, route_tool, arguments, call):
            """Mirror the MCP route: exact-target acquisition before the application."""
            telemetry, token = begin_request("seq15-" + lane["key"] + "-route")
            telemetry.caller_id = caller
            commits = None
            authority_lease = None
            try:
                authority_lease = runtime.acquire(static_tool_requirements(route_tool, arguments),
                                                  target=static_tool_target(route_tool, arguments))
                if authority_lease is None:
                    return None

                def authorize():
                    nonlocal commits
                    if commits is not None:
                        return runtime.revalidate(authority_lease, commits)
                    commits = runtime.consume(authority_lease)
                    return commits is not None
                telemetry.core_dispatch_authorizer = authorize
                return await call()
            finally:
                if authority_lease is not None:
                    telemetry.core_dispatch_authorizer = lambda: False
                    if commits is not None:
                        assert runtime.finish(commits)
                    else:
                        runtime.release(authority_lease)
                end_request(token)

        async def plan(runtime, arguments):
            return await routed(runtime, tool, arguments, lambda: getattr(service, tool)(dict(arguments)))

        async def refused_plan(runtime, arguments):
            """Record an expected refusal as the scenario outcome; acceptance is the failure."""
            from ha_mcp_engineering.ha_core_readmission.routes import StaticToolTargetRefused
            writes, commands = len(log.writes), len(log.command_types)
            try:
                await plan(runtime, arguments)
            except StaticToolTargetRefused as exc:
                # The route's InvalidStaticToolTarget refusal: no authority, no application call.
                error_code, reason_code = "invalid_request", exc.reason_code
            except GovernanceError as exc:
                details = exc.details if isinstance(exc.details, dict) else {}
                assert details.get("plan_created") is False
                assert details.get("provider_dispatch_occurred") is not True
                error_code, reason_code = exc.code.value, details.get("reason_code")
            else:
                raise AssertionError("Seq-15 planner accepted a refused request")
            return {"result": "PASS", "error_code": error_code, "reason_code": reason_code,
                    "plan_created": False, "ha_writes": len(log.writes) - writes,
                    "ha_commands": len(log.command_types) - commands}

        def owner_request(name):
            telemetry, token = begin_request("seq15-" + lane["key"] + "-" + name)
            telemetry.caller_id = caller
            return token

        async def approve_apply(created):
            token = owner_request("apply")
            try:
                granted = service.approve(created["plan_id"], created["plan_hash"])
                for _ in range(3):
                    _, csrf = await service.issue_external_csrf(created["plan_id"], granted["challenge_id"])
                    granted = await service.decide_external_approval(
                        plan_id=created["plan_id"], challenge_id=granted["challenge_id"],
                        expected_plan_hash=created["plan_hash"], approval_kind=granted["approval_kind"],
                        approval_action=granted["approval_action"], csrf_nonce=csrf, decision="approve",
                        approver_principal="home_assistant_admin_ingress:seq15-synthetic-reviewer")
                    if granted.get("status") != "approval_pending":
                        break
                else:
                    raise AssertionError("Synthetic approval did not settle")
                return await service.apply(created["plan_id"], created["plan_hash"])
            finally:
                end_request(token)

        def child_facts(task_id):
            items = service.f3_runtime.children.declarations_for_task(task_id)
            records = [service.f3_runtime.children.get(item["child_id"]) for item in items]
            codes = sorted({code for record in records for event in record.events
                            for code in event["diagnostic_codes"]})
            return [record.dispatch_count for record in records], codes

        async def apply_created(created, *, between=None):
            writes = len(log.writes)
            if between is not None:
                await between()
            applied = await approve_apply(created)
            counts, codes = child_facts(applied["task_id"])
            sent = log.writes[writes:]
            scope = ({key: applied.get(key) for key in ("verification_scope", "loaded_state")}
                     if lane["key"] == "scene_configuration" else {})
            return {**scope, "result": "PASS", "task_state": applied["task_state"], "dispatch_counts": counts,
                    "diagnostic_codes": codes, "ha_writes": len(sent), "write_types": list(sent)}

        async def governed(runtime, arguments, *, between=None, observed=None):
            created = await plan(runtime, arguments)
            # The input_select planner wraps its plan; the helper-options planner returns it.
            created = created.get("plan", created) if created is not None else None
            assert created is not None and created.get("plan_id")
            if observed is not None:
                observed["plan"] = created
            return await apply_created(created, between=between)

        async def rollback(plan_id):
            token = owner_request("rollback")
            try:
                created = await service.rollback_change(plan_id)
            finally:
                end_request(token)
            assert created.get("provider_dispatch_occurred") is False
            reverse = created.get("plan") or service.get_plan(created["rollback_plan_id"])
            return created, reverse

        async def refused_rollback(plan_id):
            token = owner_request("rollback")
            try:
                await service.rollback_change(plan_id)
            except GovernanceError as exc:
                return (exc.details or {}).get("reason")
            finally:
                end_request(token)
            raise AssertionError("Seq-15 rollback accepted a drifted baseline")

        dependency_runtime = DependencyAnalysisRuntime()
        fixture_cleanup = False
        fixture_absence = None
        result = None
        try:
            stage[0] = "predecessor"
            predecessor = await authority(old, "predecessor")
            control = predecessor.acquire(("core.dependency_helper_planning",))
            assert control is not None
            predecessor.release(control)
            probe = lane["predecessor_probe"]
            route_available = predecessor.route_status(tuple(lane["capabilities"]))["available"]
            assert route_available is False
            if lane["key"] == "script_traces":
                assert predecessor.acquire(tuple(lane["capabilities"])) is None
                assert await routed(predecessor, tool, probe, None) is None
            else:
                assert predecessor.acquire(tuple(lane["capabilities"]),
                                           target=static_tool_target(tool, probe)) is None
                assert await plan(predecessor, probe) is None
            # The service layer refuses first on its own, before any read.
            predecessor_service = compose(predecessor)
            token = owner_request("predecessor")
            try:
                if lane["key"] == "script_traces":
                    await traces(predecessor).list_traces(probe["script_id"])
                else:
                    await getattr(predecessor_service, tool)(dict(probe))
            except GovernanceError as exc:
                details = exc.details or {}
                service_reason = details.get("reason_code") or details.get("reason")
            else:
                raise AssertionError("Predecessor service admitted a dormant profile")
            finally:
                end_request(token)
            assert log.writes == [] and log.command_types == []
            predecessor_admission = {"route_acquired": False, "route_available": route_available,
                                     "service_reason_code": service_reason, "ha_requests": 0,
                                     "ha_writes": 0, "control_capability_acquired": True}

            stage[0] = "extension_authority"
            runtime = await authority(new, "extension")
            # Dependency evidence reads through its own unobserved clients, as in production.
            dependency_runtime.configure(HomeAssistantRestClient(configured), HomeAssistantWebSocketClient(configured),
                                         secret="seq15-disposable-dependency-evidence", core_runtime=runtime)
            service = compose(runtime)
            await service.f3_runtime.recover_once("startup")
            context = {"stage": stage, "owned": owned, "runtime": runtime, "raw": raw, "log": log,
                       "governed": governed, "refused_plan": refused_plan, "plan": plan,
                       "apply_created": apply_created, "rollback": rollback,
                       "refused_rollback": refused_rollback, "routed": routed, "traces": traces,
                       "owner_request": owner_request, "end_request": end_request,
                       "lane_secret": lane_secret, "GovernanceError": GovernanceError,
                       "content_seen": content_seen, "observed_rest": rest}
            with patch.object(dependency_package, "DEPENDENCY_ANALYSIS", dependency_runtime):
                scenarios = await SCENARIOS[lane["key"]](context)
            assert all(runtime.health_snapshot()[name] == 0 for name in (
                "issued_lease_count", "active_commit_count", "fallback_count"))
            batch = await authority(old + [ref for item in LANES.values() for ref in source_profiles(item)], "batch")
            batch_health = {name: batch.health_snapshot()[name] for name in ("compatible_count", "withheld_count")}
            assert batch_health == {"compatible_count": 31, "withheld_count": 0}
            result = {
                "result": "PASS", "lane": lane["key"], "core": core_version, "support_slot": binding["slot"],
                "core_image": binding["image"], "image_index_digest": binding["image_index_digest"],
                "image_receipt_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                "engineering_source": engineering_source(), "added_capabilities": added,
                "predecessor_capabilities_sha256": hashlib.sha256(canonical_json(old)).hexdigest(),
                "authority_transition": [len(old), len(new)],
                "authority_instances": "separate_ephemeral_registries", "production_authority": False,
                "provider_identity": "synthetic_disposable_lane", "batch_health": batch_health,
                "predecessor_admission": predecessor_admission,
                "engineering_command_types": sorted(set(log.command_types)),
                "scenarios": scenarios, "fixture_cleanup": False}
        finally:
            stage[0] = "cleanup:" + stage[0]
            try:
                try:
                    fixture_absence = await cleanup_fixtures(raw, owned)
                    fixture_cleanup = fixture_absence == dict.fromkeys(FIXTURE_ABSENCE, True)
                finally:
                    await dependency_runtime.shutdown()
            finally:
                for item in runtimes:
                    await close_disposable_authority(item)
        stage[0] = "verify"
        assert fixture_cleanup
        result.update(fixture_cleanup=True, fixture_absence=fixture_absence)
        try:
            # Verify the serialized evidence through the strict loader, not the in-memory value.
            summary = verify_result(lane["key"], load_receipt(receipt_bytes(result)), core_version=core_version,
                                    support_slot=binding["slot"])
        except Exception:
            # Retain the sanitized synthetic receipt as a failure.
            output.write_bytes(receipt_bytes({**result, "result": "FAIL"}))
            raise
        output.write_bytes(receipt_bytes(result))
        print("Seq-15 " + lane["key"] + " disposable contract: " + json.dumps(summary, sort_keys=True))
        return result


async def _entity_of(raw, entry_id):
    for _ in range(100):
        found = next((item["entity_id"] for item in await raw.ok({"type": REGISTRY_LIST})
                      if item.get("config_entry_id") == entry_id), None)
        if found is not None:
            return found
        await asyncio.sleep(0.1)
    raise AssertionError("Seq-15 fixture entity never registered")


async def _helper_options_scenarios(c):
    stage, owned, raw, runtime = c["stage"], c["owned"], c["raw"], c["runtime"]
    stage[0] = "fixture_setup"
    suffix = secrets.token_hex(4)
    owned["config_entries"] = []

    async def create(handler, *steps):
        entry_id = await raw.create_config_entry(handler, *steps)
        owned["config_entries"].append(entry_id)  # owned before any wait
        return entry_id, await _entity_of(raw, entry_id)

    source_entry, source = await create("template", {"next_step_id": "sensor"}, {
        "name": "Seq15 Source " + suffix, "state": "{{ 21 }}", "unit_of_measurement": "W",
        "state_class": "measurement"})
    binary_entry, binary = await create("template", {"next_step_id": "binary_sensor"}, {
        "name": "Seq15 Binary " + suffix, "state": "{{ false }}"})
    filter_entry, filtered = await create(
        "filter", {"name": "Seq15 Filter " + suffix, "entity_id": source, "filter": "lowpass"},
        {"window_size": 1, "time_constant": 7, "precision": 2})
    stats_entry, stats = await create(
        "statistics", {"name": "Seq15 Stats " + suffix, "entity_id": source},
        {"state_characteristic": "mean"}, {"sampling_size": 20, "precision": 2})
    scenarios = {}

    stage[0] = "field_not_editable"
    scenarios["field_not_editable"] = await c["refused_plan"](runtime, {
        "changes": [{"entity_id": filtered, "set": {"filter": "outlier"}}], "reason": "Seq-15 pinned"})

    edits = {"filter": (filter_entry, filtered, {"time_constant": 20}),
             "statistics": (stats_entry, stats, {"sampling_size": 30}),
             "template_sensor": (source_entry, source, {"unit_of_measurement": "kW"}),
             "template_binary_sensor": (binary_entry, binary, {"device_class": "door"})}
    for name, (entry_id, entity_id, change) in edits.items():
        stage[0] = name
        baseline = await raw.options(entry_id)
        observed = {}
        value = await c["governed"](runtime, {"changes": [{"entity_id": entity_id, "set": change}],
                                              "reason": "Seq-15 " + name}, observed=observed)
        readback = await raw.options(entry_id)
        if name == "filter":
            (row,) = (observed["plan"]["dry_run_results"]["operations"][0]["dry_run_results"]
                      ["config_entry_helper_options"]["changes"])
            scenarios["suggested_value"] = {"result": "PASS", "created": 7, "disclosed_baseline": row["baseline"],
                                            "raw_baseline": baseline.get("time_constant")}
        stage[0] = name + ":rollback"
        created, reverse = await c["rollback"](observed["plan"]["plan_id"])
        restored = await c["apply_created"](reverse)
        scenarios[name] = {**value, "baseline": baseline, "expected": {**baseline, **change}, "readback": readback,
                           "rollback": {**restored, "status": created["status"],
                                        "defaults_not_restored": created.get("defaults_not_restored"),
                                        "expected": baseline, "readback": await raw.options(entry_id)}}

    stage[0] = "validation_would_fail"
    scenarios["validation_would_fail"] = await c["refused_plan"](runtime, {
        "changes": [{"entity_id": stats, "clear": ["sampling_size"]}], "reason": "Seq-15 validation"})

    stage[0] = "stale_state"

    async def raw_drift():
        await raw.set_options(filter_entry, window_size=3)
    value = await c["governed"](runtime, {"changes": [{"entity_id": filtered, "set": {"time_constant": 30}}],
                                          "reason": "Seq-15 stale"}, between=raw_drift)
    after = await raw.options(filter_entry)
    scenarios["stale_state"] = {**value, "drift_retained": after.get("window_size") == 3
                                and after.get("time_constant") == 7}

    stage[0] = "concurrent_flow"
    # A flow opened before the plan holds the older snapshot (OD-7, §3.3).
    held = await raw.open_options(filter_entry)
    observed = {}
    value = await c["governed"](runtime, {"changes": [{"entity_id": filtered, "set": {"time_constant": 12}}],
                                          "reason": "Seq-15 concurrent"}, observed=observed)
    verified = (await raw.options(filter_entry)).get("time_constant")
    await raw.submit_options(held, suggested_values(held))
    overwritten = await raw.options(filter_entry)
    disclosure = (observed["plan"]["dry_run_results"]["operations"][0]["dry_run_results"]
                  ["config_entry_helper_options"])
    scenarios["concurrent_flow"] = {
        **value, "expected": {"time_constant": 12}, "readback": {"time_constant": verified},
        "od7_overwrite_observed": overwritten.get("time_constant") == 7,
        "rollback_reason": await c["refused_rollback"](observed["plan"]["plan_id"]),
        "disclosed_window": disclosure.get("residual_concurrency_window")}
    return scenarios


async def _input_select_scenarios(c):
    stage, owned, raw, runtime = c["stage"], c["owned"], c["raw"], c["runtime"]
    stage[0] = "fixture_setup"
    fixture_name = "Seq15 Select " + secrets.token_hex(4)
    created = await raw.ok({"type": "input_select/create", "name": fixture_name,
                            "options": ["Home", "Away", "Night"]})
    owned["input_select"] = created["id"]
    entity_id = "input_select." + owned["input_select"]

    async def state():
        status, value = await raw.status("GET", "/states/" + entity_id)
        return value if status == 200 else None
    for _ in range(100):
        if await state() is not None:
            break
        await asyncio.sleep(0.1)
    assert (await state())["state"] == "Home"
    scenarios = {}

    stage[0] = "already_desired"
    writes = len(c["log"].writes)
    no_change = await c["plan"](runtime, {"entity_id": entity_id, "option": "Home", "reason": "Seq-15 no-op"})
    scenarios["already_desired"] = {"result": "PASS", "status": no_change.get("status"),
                                    "outcome": no_change.get("outcome"), "plan_created": no_change.get("plan_created"),
                                    "ha_writes": len(c["log"].writes) - writes}

    stage[0] = "option_not_available"
    scenarios["option_not_available"] = await c["refused_plan"](runtime, {
        "entity_id": entity_id, "option": "away", "reason": "Seq-15 exact option"})

    stage[0] = "flip"
    observed = {}
    value = await c["governed"](runtime, {"entity_id": entity_id, "option": "Away", "reason": "Seq-15 flip"},
                                observed=observed)
    scenarios["flip"] = {**value, "expected": "Away", "readback": (await state())["state"]}

    stage[0] = "rollback"
    created, reverse = await c["rollback"](observed["plan"]["plan_id"])
    value = await c["apply_created"](reverse)
    scenarios["rollback"] = {**value, "status": created["status"], "prior": "Home", "expected": "Home",
                             "readback": (await state())["state"]}

    stage[0] = "options_changed"

    async def raw_drift():
        # The reviewed reorder drift (tests/test_hamcp145_input_select_option.py).
        await raw.ok({"type": "input_select/update", "input_select_id": owned["input_select"],
                      "name": fixture_name, "options": ["Away", "Home", "Night"]})
    value = await c["governed"](runtime, {"entity_id": entity_id, "option": "Night", "reason": "Seq-15 stale"},
                                between=raw_drift)
    after = await state()
    scenarios["options_changed"] = {**value, "drift_retained": after["state"] == "Home"
                                    and after["attributes"]["options"] == ["Away", "Home", "Night"]}
    return scenarios


def _longest_string(value):
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return max([0, *(_longest_string(item) for item in value.values())])
    if isinstance(value, list):
        return max([0, *(_longest_string(item) for item in value)])
    return 0


async def _script_trace_scenarios(c):
    from ha_mcp_engineering.trace_normalization import TRACE_STRING_LIMIT
    stage, owned, raw, runtime, log = c["stage"], c["owned"], c["raw"], c["runtime"], c["log"]
    stage[0] = "fixture_setup"
    key = "seq15_" + secrets.token_hex(4)
    owned["script"] = key
    # Review H1: the known lane secret plus credential shapes the shared
    # screen must recognise without being told (a credential URI value and an
    # encoded key=value dictionary key), assembled at run time.
    shaped = "SEQ15" + secrets.token_hex(8)
    await raw.rest("POST", "/config/script/config/" + key, {
        "alias": "Seq15 Trace " + key[-8:], "mode": "single",
        "sequence": [{"variables": {
            TRACE_SECRET_KEY: c["lane_secret"], "seq15_long": "x" * LONG_VALUE,
            "seq15_uri": "postgresql" + "://seq15:" + shaped + "@seq15.invalid/db",
            "seq15_nested": {"api%5f" + "key=" + shaped: "benign"}}}]})
    for _ in range(100):
        if (await raw.status("GET", "/states/script." + key))[0] == 200:
            break
        await asyncio.sleep(0.1)
    await raw.ok({"type": "call_service", "domain": "script", "service": key, "service_data": {}})
    run_ids = []
    for _ in range(100):
        listed = await raw.ok({"type": "trace/list", "domain": "script", "item_id": key})
        run_ids = [item["run_id"] for item in listed if item.get("state") == "stopped"]
        if run_ids:
            break
        await asyncio.sleep(0.1)
    assert run_ids
    service = c["traces"](runtime)
    scenarios = {}

    async def call(route_tool, arguments, method):
        return await c["routed"](runtime, route_tool, arguments, method)

    stage[0] = "list"
    listed = await call("list_script_traces", {"script_id": key}, lambda: service.list_traces(key))
    scenarios["list"] = {"result": "PASS", "traces": len(listed["traces"]),
                         "run_listed": run_ids[0] in {item.get("run_id") for item in listed["traces"]},
                         "sanitization_failed_closed": listed["sanitization_failed_closed"]}

    stage[0] = "get"
    got = await call("get_script_trace", {"script_id": key, "run_id": run_ids[0]},
                     lambda: service.get_trace(key, run_ids[0]))
    rendered = json.dumps(got, sort_keys=True)
    scenarios["get"] = {"result": "PASS", "run_matches": got["run_id"] == run_ids[0],
                        "secret_absent": c["lane_secret"] not in rendered and shaped not in rendered,
                        "redacted": got["redaction"] is not None,
                        "long_value_bounded": _longest_string(got["trace"]) < LONG_VALUE
                        and _longest_string(got["trace"]) <= TRACE_STRING_LIMIT + 64}

    async def refusal(name, route_tool, arguments, method):
        commands = len(log.command_types)
        try:
            await call(route_tool, arguments, method)
        except c["GovernanceError"] as exc:
            return {"result": "PASS", "reason": (exc.details or {}).get("reason"), "error_code": exc.code.value,
                    "ha_commands": len(log.command_types) - commands}
        raise AssertionError("Seq-15 trace read accepted " + name)

    stage[0] = "not_found"
    missing = secrets.token_hex(16)
    scenarios["not_found"] = await refusal("not_found", "get_script_trace", {"script_id": key, "run_id": missing},
                                           lambda: service.get_trace(key, missing))
    stage[0] = "invalid_arguments"
    scenarios["invalid_arguments"] = await refusal(
        "invalid_arguments", "list_script_traces", {"script_id": "Seq15-Bad"},
        lambda: service.list_traces("Seq15-Bad"))
    # Every trace request named the exact owned script in the script domain.
    assert all(payload.get("domain") == "script" and payload.get("item_id") == key for payload in log.payloads)
    return scenarios


async def _dashboard_metadata_scenarios(c):
    from ha_mcp_engineering.providers.upstream_dashboard import _engineering_config_hash
    stage, owned, raw, runtime, log = c["stage"], c["owned"], c["raw"], c["runtime"], c["log"]
    stage[0] = "fixture_setup"
    # Harness-only setup through the raw client; Engineering never creates or saves a dashboard.
    url_path = "seq15-" + secrets.token_hex(4)
    created = await raw.ok({"type": "lovelace/dashboards/create", "url_path": url_path, "title": "Seq15 Board",
                            "icon": "mdi:view-dashboard", "show_in_sidebar": True, "require_admin": False})
    owned["dashboard"] = created["id"]  # owned before any further command
    await raw.ok({"type": "lovelace/config/save", "url_path": url_path, "config": DASHBOARD_CONFIG})

    async def row():
        (found,) = [item for item in await raw.ok({"type": DASHBOARD_LIST}) if item.get("url_path") == url_path]
        return found

    async def fields():
        found = await row()
        return {name: found.get(name) for name in DASHBOARD_FIELDS}

    async def content():
        return await raw.ok({"type": LOVELACE_CONFIG, "url_path": url_path, "force": True})

    def sent_since(start):
        updates = [payload for payload in log.payloads[start:] if payload.get("type") == DASHBOARD_UPDATE]
        return {"sent_fields": [sorted(set(payload) - {"type", "dashboard_id"}) for payload in updates],
                "dashboard_ids_exact": bool(updates) and all(
                    payload.get("dashboard_id") == owned["dashboard"] for payload in updates)}
    content_before = await content()
    scenarios = {}

    stage[0] = "unknown_id"
    inventory = await raw.ok({"type": DASHBOARD_LIST})
    code = await raw.refused({"type": DASHBOARD_UPDATE, "dashboard_id": "seq15_absent_" + secrets.token_hex(4),
                              "title": "No implicit create"})
    scenarios["unknown_id"] = {"result": "PASS", "error_code": code,
                               "inventory_unchanged": await raw.ok({"type": DASHBOARD_LIST}) == inventory}
    stage[0] = "immutable_fields"
    baseline = await row()
    errors = {}
    for field, value in (("url_path", "seq15-other"), ("mode", "yaml")):
        errors[field] = await raw.refused({"type": DASHBOARD_UPDATE, "dashboard_id": owned["dashboard"], field: value})
    scenarios["immutable_fields"] = {"result": "PASS", "errors": errors, "row_unchanged": await row() == baseline}

    stage[0] = "default_dashboard"
    scenarios["default_dashboard"] = await c["refused_plan"](runtime, {
        "url_path": DEFAULT_DASHBOARD_URL_PATH, "title": "Seq15"})
    stage[0] = "no_change"
    scenarios["no_change"] = await c["refused_plan"](runtime, {"url_path": url_path, "title": "Seq15 Board"})

    async def edit(name, change):
        stage[0] = name
        baseline, start, observed = await fields(), len(log.payloads), {}
        value = await c["governed"](runtime, {"url_path": url_path, **change}, observed=observed)
        found = await row()
        scenarios[name] = {**value, **sent_since(start), "baseline": baseline, "expected": {**baseline, **change},
                           "readback": {field: found.get(field) for field in DASHBOARD_FIELDS},
                           "requested_fields": sorted(change), "icon_key_absent": "icon" not in found}
        return observed["plan"]
    await edit("title", {"title": DASHBOARD_TITLE})
    await edit("icon", {"icon": "mdi:map-marker"})
    await edit("icon_clear", {"icon": None})
    await edit("show_in_sidebar", {"show_in_sidebar": False})
    await edit("require_admin", {"require_admin": True})
    prior = await fields()
    forward = await edit("all_fields", {"title": "Seq15 Floor", "icon": "mdi:floor-plan",
                                        "show_in_sidebar": True, "require_admin": False})

    stage[0] = "rollback"
    baseline, start = await fields(), len(log.payloads)
    created, reverse = await c["rollback"](forward["plan_id"])
    value = await c["apply_created"](reverse)
    # For the reverse plan the requested fields are the ones it restores.
    scenarios["rollback"] = {**value, **sent_since(start), "status": created["status"], "baseline": baseline,
                             "prior": prior, "expected": prior, "readback": await fields(),
                             "requested_fields": sorted(created.get("restored_fields") or [])}

    stage[0] = "content_unchanged"
    # Views and cards are never sent: every bound fingerprint is the seeded content's.
    content_after = await content()
    scenarios["content_unchanged"] = {
        "result": "PASS", "raw_config_unchanged": content_after == content_before,
        "fingerprint_reads": len(c["content_seen"]),
        "fingerprints_match": set(c["content_seen"]) == {_engineering_config_hash(content_before)}}

    stage[0] = "stale_state"

    async def title_drift():
        await raw.ok({"type": DASHBOARD_UPDATE, "dashboard_id": owned["dashboard"], "title": "Seq15 Drift"})
    value = await c["governed"](runtime, {"url_path": url_path, "title": "Seq15 Stale"}, between=title_drift)
    scenarios["stale_state"] = {**value, "drift_retained": (await row())["title"] == "Seq15 Drift"}

    stage[0] = "content_drift"
    drifted = {**DASHBOARD_CONFIG, "title": "Seq15 Drift"}

    async def content_change():
        await raw.ok({"type": "lovelace/config/save", "url_path": url_path, "config": drifted})
    value = await c["governed"](runtime, {"url_path": url_path, "show_in_sidebar": True}, between=content_change)
    after = await row()
    scenarios["content_drift"] = {**value, "drift_retained": await content() == drifted
                                  and after["show_in_sidebar"] is False and after["title"] == "Seq15 Drift"}
    return scenarios


SCENARIOS = {"helper_options": _helper_options_scenarios, "input_select_option": _input_select_scenarios,
             "script_traces": _script_trace_scenarios, "dashboard_metadata": _dashboard_metadata_scenarios}

# Kept separate so the new scene harness cannot alter any runtime module.
import seq15_scene_contract as scene_contract  # noqa: E402
LANES["scene_configuration"] = _lane(
    "scene_configuration", capability=SCENE, adapter="compiled-core-scene-configuration-operation-v1",
    tool="create_scene_configuration_plan", output_env="REAL_HA_SCENE_CONFIGURATION_RESULT",
    reviewed={SCENE_GET, SCENE_POST, STATE_READ, ENTITY_GET, REGISTRY_LIST},
    required={SCENE_GET, SCENE_POST, STATE_READ, ENTITY_GET, REGISTRY_LIST},
    never_sent={"DELETE /config/scene/config/{}", "call_service", "scene.apply", "scene.create",
                "scene.delete", "scene.reload", "scene.turn_on"},
    scenarios=scene_contract.SCENARIOS,
    predecessor_probe={"config_id": "seq15_00000000", "name": "Absent"}, predecessor_reason=PENDING)
SCENARIOS["scene_configuration"] = scene_contract.run
CHECKS["scene_configuration"] = scene_contract.checks
