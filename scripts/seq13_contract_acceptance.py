"""HAMCP-165 and HAMCP-069B exact-Core evidence lanes for the seq-13 signing batch.

Defined, not wired: the disposable Alarmo-slot harness calls these lanes only
when ``REAL_HA_SEQ13_LANES`` is ``enabled``, and no CI job sets that value until
the owner wires the seq-13 batch evidence run. No standalone or live CLI.

Each lane exercises dormant profiles against the exact window Core under two
CI-only ephemeral authorities: a predecessor holding the shipped signed set
without them (must refuse before Home Assistant) and an extension with the
lane's profiles appended. The production signed registry is never read for
authority, changed or signed. Every Home Assistant object a lane writes is
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
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
from entity_metadata_contract_acceptance import (  # noqa: E402
    REFERENCE_KEYS, RawRefusal, RawWebSocket, engineering_source, load_receipt, receipt_bytes)

ENABLE_FLAG = "REAL_HA_SEQ13_LANES"
ENABLED = "enabled"
LABEL = "core.label_registry_operation"
AVAILABILITY = "core.entity_registry_availability_operation"
EXPOSURE = "core.entity_assistant_exposure_operation"
METADATA = "core.entity_registry_metadata_operation"
DELETE = "core.configuration_delete_operation"
# Every seq-13 profile this source compiles; none is in the shipped signed set.
# The HAMCP-155 delete profile has its own lane and stays unsigned in these.
SEQ13_DORMANT = frozenset({LABEL, AVAILABILITY, EXPOSURE, DELETE})
SHIPPED_SIGNED_COUNT = 22
ENTITY_GET, ENTITY_UPDATE = "config/entity_registry/get", "config/entity_registry/update"
LABEL_LIST = "config/label_registry/list"
LABEL_CREATE, LABEL_UPDATE = "config/label_registry/create", "config/label_registry/update"
EXPOSE = "homeassistant/expose_entity"
# Only these count as Home Assistant writes in a lane's Engineering client.
WRITE_COMMANDS = frozenset({ENTITY_UPDATE, LABEL_CREATE, LABEL_UPDATE, EXPOSE})
FIXTURE_ABSENCE = ("helper_config", "helper_entity", "labels", "config_entry")
CONFIG_ENTRY_GET, REGISTRY_LIST = "config_entries/get_single", "config/entity_registry/list"
# The two enable-path reads a config-entry-backed target must exercise (HAMCP-165 review1 M4).
CONFIG_ENTRY_READS = frozenset({CONFIG_ENTRY_GET, REGISTRY_LIST})
# Times of the Day: a Core helper whose config flow needs no network (Core 2026.10.0/2026.10.1 source).
CONFIG_ENTRY_DOMAIN = "tod"
CONFIG_ENTRY_ROUTE = re.compile(r"/config/config_entries/(?:flow|flow/[0-9A-Za-z]{1,64}|entry/[0-9A-Za-z]{1,64})")


def _lane(key, *, capabilities, adapters, tool, output_env, reviewed, required, scenarios):
    return {"key": key, "capabilities": capabilities, "adapters": adapters, "tool": tool,
            "output_env": output_env, "reviewed_commands": frozenset(reviewed),
            "required_commands": frozenset(required), "scenarios": scenarios,
            "authority_transition": [SHIPPED_SIGNED_COUNT, SHIPPED_SIGNED_COUNT + len(capabilities)]}


LANES = {
    "label_registry": _lane(
        "label_registry", capabilities=(LABEL,), adapters=("compiled-core-label-registry-operation-v1",),
        tool="create_label_plan", output_env="REAL_HA_LABEL_REGISTRY_RESULT",
        # The label gateway plus the signed 069A assignment path it reuses.
        reviewed={LABEL_LIST, LABEL_CREATE, LABEL_UPDATE, ENTITY_GET, ENTITY_UPDATE,
                  "config/area_registry/list", "config/category_registry/list"},
        required={LABEL_LIST, LABEL_CREATE, LABEL_UPDATE, ENTITY_UPDATE},
        scenarios=("create", "edit", "create_then_assign", "duplicate_name", "rename_in_use",
                   "invalid_values", "stale_state")),
    "availability_exposure": _lane(
        "availability_exposure", capabilities=(AVAILABILITY, EXPOSURE),
        adapters=("compiled-core-entity-registry-availability-operation-v1",
                  "compiled-core-entity-assistant-exposure-operation-v1"),
        tool="create_entity_availability_exposure_plan", output_env="REAL_HA_AVAILABILITY_EXPOSURE_RESULT",
        reviewed={ENTITY_GET, REGISTRY_LIST, CONFIG_ENTRY_GET,
                  "homeassistant/expose_new_entities/get", ENTITY_UPDATE, EXPOSE},
        required={ENTITY_GET, "homeassistant/expose_new_entities/get", ENTITY_UPDATE, EXPOSE,
                  *CONFIG_ENTRY_READS},
        scenarios=("disable", "already_disabled", "enable", "enable_config_entry", "unexpose", "expose",
                   "not_in_registry", "stale_state")),
}
# Commands no lane's Engineering client may ever send (deletion is HAMCP-155).
NEVER_SENT = frozenset({"config/label_registry/delete", "homeassistant/expose_entity/list",
                        "homeassistant/expose_new_entities/set", "cloud/status",
                        "cloud/google_assistant/entities/update", "cloud/alexa/entities/update"})
CANARY = {"name": "HAMCP165 Canary", "color": "indigo", "icon": "mdi:tag",
          "description": "HAMCP-165 synthetic disposable label"}
CANARY_ID = "hamcp165_canary"
EDITED = {"name": "HAMCP165 Canary Edited", "color": "#3366ff", "icon": "mdi:tag-outline", "description": None}
ASSIGN = {"name": "HAMCP165 Assign"}
ASSIGN_ID = "hamcp165_assign"
ABSENT = "input_boolean.hamcp069b_absent"
ASSISTANT = "conversation"


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
            raise ValueError("Seq-13 adapter differs from the reviewed lane")
        references.append(reference)
    return references


def shipped_references():
    """The predecessor: every compiled profile except the seq-13 dormant set."""
    from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
    refs = [{k: p.to_mapping()[k] for k in REFERENCE_KEYS} for p in CORE_RUNTIME_CAPABILITY_PROFILES
            if p.capability_id not in SEQ13_DORMANT]
    assert len(refs) == SHIPPED_SIGNED_COUNT and len({r["capability_id"] for r in refs}) == len(refs)
    assert METADATA in {r["capability_id"] for r in refs}
    return refs


def _label_checks(name, value):
    if name in {"create", "edit", "create_then_assign"}:
        assert value["task_state"] == "succeeded_verified"
        assert value["dispatch_counts"] == [1] * len(value["dispatch_counts"])
        assert value["ha_writes"] == len(value["dispatch_counts"])
        assert value["readback"] == value["expected"]
    if name == "create":
        assert value["dispatch_counts"] == [1] and value["write_types"] == [LABEL_CREATE]
        assert value["core_label_id"] == value["predicted_label_id"] == CANARY_ID
        assert value["expected"] == {"label_id": CANARY_ID, **CANARY}
    if name == "edit":
        assert value["dispatch_counts"] == [1] and value["write_types"] == [LABEL_UPDATE]
        assert value["expected"] == {"label_id": CANARY_ID, **EDITED}
        assert value["sent_fields"] == sorted(["label_id", *EDITED])
    if name == "create_then_assign":
        assert value["dispatch_counts"] == [1, 1] and value["write_types"] == [LABEL_CREATE, ENTITY_UPDATE]
        assert value["core_label_id"] == value["predicted_label_id"] == ASSIGN_ID
        assert value["expected"]["entity_labels"] == [ASSIGN_ID]
    if name in {"duplicate_name", "rename_in_use", "invalid_values"}:
        assert value["ha_writes"] == 0 and value["plan_created"] is False
    if name == "duplicate_name":
        assert value["reason_code"] == "label_already_exists"
    if name == "rename_in_use":
        assert value["reason_code"] == "label_name_in_use"
    if name == "invalid_values":
        assert value["reason_codes"] == {"color": "invalid_label_color", "icon": "invalid_label_icon"}
        assert value["ha_commands"] == 0
    if name == "stale_state":
        assert value["task_state"] == "failed_pre_dispatch" and value["dispatch_counts"] == [0]
        assert value["ha_writes"] == 0 and value["drift_retained"] is True


def _availability_checks(name, value):
    if name in {"disable", "enable", "unexpose", "expose"}:
        assert value["task_state"] == "succeeded_verified" and value["dispatch_counts"] == [1]
        assert value["ha_writes"] == 1 and value["readback"] == value["expected"]
    if name == "disable":
        assert value["write_types"] == [ENTITY_UPDATE] and value["expected"] == {"disabled_by": "user"}
    if name == "enable":
        assert value["write_types"] == [ENTITY_UPDATE] and value["expected"] == {"disabled_by": None}
    if name == "unexpose":
        assert value["write_types"] == [EXPOSE] and value["expected"] == {"should_expose": False}
    if name == "expose":
        assert value["write_types"] == [EXPOSE] and value["expected"] == {"should_expose": True}
    if name == "enable_config_entry":
        # Both config-entry reads ran during this plan and returned the lane-owned entry.
        assert value["task_state"] == "succeeded_verified" and value["dispatch_counts"] == [1]
        assert value["ha_writes"] == 1 and value["write_types"] == [ENTITY_UPDATE]
        assert value["expected"] == {"disabled_by": None} and value["readback"] == value["expected"]
        assert value["config_entry_reads"] == sorted(CONFIG_ENTRY_READS)
        assert value["config_entry"] == {"status": "read", "domain": CONFIG_ENTRY_DOMAIN,
                                         "supports_unload": True, "disabled_by": None}
        assert value["other_entities_in_entry"] == 0 and value["reload"] == "config_entry_reload_after_30s"
    if name in {"already_disabled", "not_in_registry"}:
        assert value["ha_writes"] == 0 and value["plan_created"] is False
    if name == "already_disabled":
        assert value["reason_code"] == "entity_already_disabled"
    if name == "not_in_registry":
        assert value["reason_code"] == "entity_not_in_registry"
    if name == "stale_state":
        assert value["task_state"] == "failed_pre_dispatch" and value["dispatch_counts"] == [0]
        assert value["ha_writes"] == 0 and value["drift_retained"] is True


CHECKS = {"label_registry": _label_checks, "availability_exposure": _availability_checks}


def verify_result(lane_key, result, *, core_version, support_slot):
    """Offline receipt verification against the window and current source.

    Refuses a receipt for another lane, Core, slot, image digest, adapter,
    compiled contract fingerprint or Engineering runtime tree. It does not
    substitute for execution.
    """
    from alarmo_inspection_contract_acceptance import slot_binding
    lane = LANES[lane_key]
    assert type(core_version) is str and type(support_slot) is str
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
    assert len(result["predecessor_capabilities_sha256"]) == 64
    assert result["predecessor_admission"] == {
        "route_acquired": False, "service_reason_code": "pending_signed_registry_admission",
        "ha_requests": 0, "ha_writes": 0, "control_capability_acquired": True}
    commands = result["engineering_command_types"]
    assert type(commands) is list and commands == sorted(set(commands))
    assert set(commands) <= lane["reviewed_commands"] and not set(commands) & NEVER_SENT
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


class LaneRaw(RawWebSocket):
    """The raw client plus the lane's loopback config-entry routes, for setup and cleanup only.

    Engineering never sends these. Only the flow, one flow step and one owned
    entry's deletion are reachable, and only on a loopback Core.
    """

    def __init__(self, configured):
        super().__init__(configured)
        self.api_url = configured.api_url.rstrip("/")
        self.token = configured.ha_token
        if urlsplit(self.api_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Seq-13 config-entry routes go only to a loopback Core")

    async def rest(self, method, path, body=None):
        import aiohttp
        if CONFIG_ENTRY_ROUTE.fullmatch(path) is None:
            raise ValueError("Not a seq-13 config-entry route")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.request(method, self.api_url + path, json=body, allow_redirects=False,
                                       headers={"Authorization": "Bearer " + self.token}) as response:
                if response.status >= 300:
                    raise RawRefusal(method + " " + path.split("/")[3], str(response.status))
                return await response.json()

    async def create_config_entry(self, handler, user_input):
        """Complete a one-form config flow; return the created entry id."""
        flow = await self.rest("POST", "/config/config_entries/flow",
                               {"handler": handler, "show_advanced_options": False})
        assert flow.get("type") == "form"
        created = await self.rest("POST", "/config/config_entries/flow/" + flow["flow_id"], user_input)
        assert created.get("type") == "create_entry"
        return created["result"]["entry_id"]

    async def delete_config_entry(self, entry_id):
        await self.rest("DELETE", "/config/config_entries/entry/" + entry_id)


class ObservedWebSocket:
    """Engineering's real client, observed; every write and command type counted."""

    def __init__(self, inner):
        self.inner = inner
        self.command_types = []
        self.writes = []

    async def command(self, payload):
        kind = payload.get("type")
        self.command_types.append(kind)
        if kind in WRITE_COMMANDS:
            self.writes.append(dict(payload))
        return await self.inner.command(payload)

    def __getattr__(self, name):
        return getattr(self.inner, name)


async def cleanup_fixtures(raw, owned, *, attempts=50, delay=0.1):
    """Delete every owned fixture with the lane's raw client, then read back absence.

    An acknowledged delete is not evidence of absence; only an independent
    read-back is. Returns the per-type absence, or None when nothing was owned.
    """
    if owned.get("helper"):
        await raw.ok({"type": "input_boolean/delete", "input_boolean_id": owned["helper"]})
    for label_id in owned.get("labels", []):
        await raw.ok({"type": "config/label_registry/delete", "label_id": label_id})
    if owned.get("config_entry"):
        await raw.delete_config_entry(owned["config_entry"])
    if not owned:
        return None

    async def ids(command, field):
        return {item[field] for item in await raw.ok({"type": command})}

    async def helper_entity_absent():
        if not owned.get("helper"):
            return True
        found = await raw.call({"type": ENTITY_GET, "entity_id": "input_boolean." + owned["helper"]})
        return found.get("success") is not True and (found.get("error") or {}).get("code") == "not_found"

    checks = {"helper_config": lambda: ids("input_boolean/list", "id"), "labels": lambda: ids(LABEL_LIST, "label_id"),
              "config_entry": lambda: ids("config_entries/get", "entry_id")}
    created = {"helper_config": {owned.get("helper")} - {None}, "labels": set(owned.get("labels", [])),
               "config_entry": {owned.get("config_entry")} - {None}}
    absence = {}
    for name in FIXTURE_ABSENCE:
        for _ in range(attempts):
            absent = await helper_entity_absent() if name == "helper_entity" else not created[name] & await checks[name]()
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
            raise ValueError("Seq-13 lanes are defined but not wired")
        return await _run_disposable(LANES[lane_key], configured, expected_image=expected_image,
                                     core_version=core_version, stage=stage)
    except Exception as exc:
        cursor = exc.__traceback__
        lines = []
        while cursor is not None:
            if cursor.tb_frame.f_code.co_filename == __file__:
                lines.append(cursor.tb_lineno)
            cursor = cursor.tb_next
        exc.contract_missing_key = "seq13_" + str(lane_key if lane_key in LANES else "unknown")
        exc.contract_diagnostic = {"seq13_source_lines": lines[:12], "seq13_stage": stage[0]}
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
    from ha_mcp_engineering.governance.resources import ConfigurationResourceGateway
    from ha_mcp_engineering.governance.service import ChangeGovernanceService
    from ha_mcp_engineering.governance.storage import ChangePlanRepository
    from ha_mcp_engineering.request_context import begin_request, end_request
    from ha_mcp_engineering.ha_core_readmission import CoreRuntime
    from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
    from ha_mcp_engineering.ha_core_readmission.routes import static_tool_requirements, static_tool_target
    from ha_mcp_engineering.signed_registry import canonical_json

    binding = slot_binding(core_version)
    entry = lane_entry(core_version)
    if (urlsplit(configured.ha_url).hostname not in {"127.0.0.1", "localhost", "::1"}
            or os.environ.get("HA_CONTRACT_LANE") != binding["lane"]
            or expected_image != binding["image"]
            or entry["image_index_digest"] != binding["image_index_digest"]):
        raise ValueError("Exact owned disposable seq-13 lane required")
    image_path = Path(os.environ["REAL_HA_ALARMO_IMAGE_RESULT"])
    image = load_receipt(image_path.read_bytes())
    assert (image["result"] == "PASS" and image["index"] == entry["image_index_digest"]
            and image["core_version"] == core_version and image["support_slot"] == binding["slot"])
    output = Path(os.environ[lane["output_env"]])
    probe_profile = probe_profile_for(core_version)
    added = source_profiles(lane)
    old = shipped_references()
    new = old + added
    assert [len(old), len(new)] == lane["authority_transition"]
    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    evidence = ("Synthetic seq-13 " + lane["key"] + " lane; no production applicability").encode()
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
    caller = "seq13-" + lane["key"] + "-synthetic-owner"
    stage[0] = "authority"
    with tempfile.TemporaryDirectory(prefix="seq13-" + lane["key"] + "-") as directory:
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

        engineering_ws = ObservedWebSocket(HomeAssistantWebSocketClient(configured))
        rest = HomeAssistantRestClient(configured)
        raw = LaneRaw(configured)
        service = None

        def compose(runtime):
            resource_gateway = ConfigurationResourceGateway(rest, engineering_ws)
            # The production composition (governance/runtime.py), one service per authority.
            resource_gateway.entity_metadata_consumer_evidence = runtime_consumer_evidence()
            resource_gateway.availability_exposure_snapshot_reader = read_runtime_dependency_snapshot
            audit_secret = "seq13-" + lane["key"] + "-disposable-audit"
            composed = ChangeGovernanceService(
                ChangePlanRepository(root / ("plans-" + str(len(runtimes)))), resource_gateway,
                AuditLogger(str(root / ("audit-" + str(len(runtimes)) + ".jsonl")), audit_secret),
                sensitive_values=(audit_secret, configured.ha_token, configured.access_secret),
                entity_metadata_gateway=resource_gateway.entity_metadata,
                entity_availability_exposure_gateway=resource_gateway.availability_exposure,
                label_registry_gateway=resource_gateway.label_registry)

            async def provider_identity():
                return {"slug": "seq13_disposable_lane",
                        "evidence_hash": hashlib.sha256(b"Seq-13 synthetic provider lock").hexdigest()}
            composed.f3_runtime = F3RuntimeIntegration(
                service=composed, storage_root=str(root / ("plans-" + str(len(runtimes)))),
                configuration_gateway=resource_gateway, backup_gateway=None, lifecycle_gateway=None,
                provider_identity_reader=provider_identity, retention_days=90, core_runtime=runtime)
            return composed

        async def plan(runtime, arguments, target_service=None):
            """Mirror the MCP route: exact-target acquisition before the application."""
            telemetry, token = begin_request("seq13-" + lane["key"] + "-plan")
            telemetry.caller_id = caller
            commits = None
            authority_lease = None
            try:
                # An invalid exact target raises StaticToolTargetRefused before any
                # authority is acquired, exactly as the route refuses it (HAMCP-165 M2).
                authority_lease = runtime.acquire(static_tool_requirements(tool, arguments),
                                                  target=static_tool_target(tool, arguments))
                if authority_lease is None:
                    return None

                def authorize():
                    nonlocal commits
                    if commits is not None:
                        return runtime.revalidate(authority_lease, commits)
                    commits = runtime.consume(authority_lease)
                    return commits is not None
                telemetry.core_dispatch_authorizer = authorize
                return await getattr(target_service or service, tool)(dict(arguments))
            finally:
                if authority_lease is not None:
                    telemetry.core_dispatch_authorizer = lambda: False
                    if commits is not None:
                        assert runtime.finish(commits)
                    else:
                        runtime.release(authority_lease)
                end_request(token)

        async def refused_plan(runtime, arguments):
            """Record an expected refusal as the scenario outcome; acceptance is the failure."""
            from ha_mcp_engineering.ha_core_readmission.routes import StaticToolTargetRefused
            writes, commands = len(engineering_ws.writes), len(engineering_ws.command_types)
            try:
                await plan(runtime, arguments)
            except StaticToolTargetRefused as exc:
                # The route's InvalidStaticToolTarget refusal: no authority, no application call.
                error_code, reason_code = "invalid_request", exc.reason_code
            except GovernanceError as exc:
                details = exc.details if isinstance(exc.details, dict) else {}
                assert details.get("plan_created") is False
                assert details.get("provider_dispatch_occurred") is False
                error_code, reason_code = exc.code.value, details.get("reason_code")
            else:
                raise AssertionError("Seq-13 planner accepted a refused request")
            return {"result": "PASS", "error_code": error_code, "reason_code": reason_code,
                    "plan_created": False, "ha_writes": len(engineering_ws.writes) - writes,
                    "ha_commands": len(engineering_ws.command_types) - commands}

        async def approve_apply(created):
            telemetry, token = begin_request("seq13-" + lane["key"] + "-apply")
            telemetry.caller_id = caller
            try:
                granted = service.approve(created["plan_id"], created["plan_hash"])
                for _ in range(3):
                    _, csrf = await service.issue_external_csrf(created["plan_id"], granted["challenge_id"])
                    granted = await service.decide_external_approval(
                        plan_id=created["plan_id"], challenge_id=granted["challenge_id"],
                        expected_plan_hash=created["plan_hash"], approval_kind=granted["approval_kind"],
                        approval_action=granted["approval_action"], csrf_nonce=csrf, decision="approve",
                        approver_principal="home_assistant_admin_ingress:seq13-synthetic-reviewer")
                    if granted.get("status") != "approval_pending":
                        break
                else:
                    raise AssertionError("Synthetic approval did not settle")
                return await service.apply(created["plan_id"], created["plan_hash"])
            finally:
                end_request(token)

        def dispatch_counts(task_id):
            items = service.f3_runtime.children.declarations_for_task(task_id)
            return [service.f3_runtime.children.get(item["child_id"]).dispatch_count for item in items]

        async def governed(runtime, arguments, *, between=None, observed=None):
            writes, commands = len(engineering_ws.writes), len(engineering_ws.command_types)
            created = await plan(runtime, arguments)
            assert created is not None and created.get("plan_id")
            if observed is not None:
                observed.update(plan=created, commands=engineering_ws.command_types[commands:])
            if between is not None:
                await between()
            applied = await approve_apply(created)
            sent = engineering_ws.writes[writes:]
            return applied, sent, {"result": "PASS", "task_state": applied["task_state"],
                                   "dispatch_counts": dispatch_counts(applied["task_id"]),
                                   "ha_writes": len(sent), "write_types": [item["type"] for item in sent]}

        async def label(label_id):
            for item in await raw.ok({"type": LABEL_LIST}):
                if item.get("label_id") == label_id:
                    return {k: item.get(k) for k in ("label_id", "name", "color", "icon", "description")}
            return None

        async def entity(entity_id):
            return await raw.ok({"type": ENTITY_GET, "entity_id": entity_id})

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
            probe = ({"operations": [{"operation": "create", **CANARY}]} if lane["key"] == "label_registry"
                     else {"operation": "disable", "entity_ids": [ABSENT]})
            assert predecessor.acquire(tuple(lane["capabilities"]), target=static_tool_target(tool, probe)) is None
            assert await plan(predecessor, probe) is None
            # The service layer refuses first on its own, before any read (HAMCP-165 / 069B M1).
            predecessor_service = compose(predecessor)
            telemetry, token = begin_request("seq13-" + lane["key"] + "-predecessor")
            telemetry.caller_id = caller
            try:
                await getattr(predecessor_service, tool)(dict(probe))
            except GovernanceError as exc:
                service_reason = (exc.details or {}).get("reason_code")
            else:
                raise AssertionError("Predecessor service admitted a dormant profile")
            finally:
                end_request(token)
            assert engineering_ws.writes == [] and engineering_ws.command_types == []
            predecessor_admission = {"route_acquired": False, "service_reason_code": service_reason,
                                     "ha_requests": 0, "ha_writes": 0, "control_capability_acquired": True}

            stage[0] = "extension_authority"
            runtime = await authority(new, "extension")
            dependency_runtime.configure(rest, HomeAssistantWebSocketClient(configured),
                                         secret="seq13-disposable-dependency-evidence", core_runtime=runtime)
            service = compose(runtime)
            await service.f3_runtime.recover_once("startup")

            stage[0] = "fixture_setup"
            # A run-unique helper: Home Assistant restores a deleted entity's registry
            # metadata when the same unique ID is re-created.
            owned["helper"] = (await raw.ok({"type": "input_boolean/create",
                                             "name": "Seq13 Lane " + secrets.token_hex(4)}))["id"]
            owned["labels"] = []
            entity_id = "input_boolean." + owned["helper"]
            for _ in range(100):
                if (await raw.call({"type": ENTITY_GET, "entity_id": entity_id})).get("success"):
                    break
                await asyncio.sleep(0.1)
            preflight = await entity(entity_id)
            assert preflight["disabled_by"] is None and not preflight["labels"]
            scenarios = {}

            with patch.object(dependency_package, "DEPENDENCY_ANALYSIS", dependency_runtime):
                if lane["key"] == "label_registry":
                    await _label_scenarios(stage, scenarios, owned, entity_id, governed, refused_plan,
                                           runtime, raw, label, entity)
                else:
                    await _availability_scenarios(stage, scenarios, owned, entity_id, governed, refused_plan,
                                                  runtime, raw, entity)
            assert all(runtime.health_snapshot()[name] == 0 for name in (
                "issued_lease_count", "active_commit_count", "fallback_count"))
            result = {
                "result": "PASS", "lane": lane["key"], "core": core_version, "support_slot": binding["slot"],
                "core_image": binding["image"], "image_index_digest": binding["image_index_digest"],
                "image_receipt_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                "engineering_source": engineering_source(), "added_capabilities": added,
                "predecessor_capabilities_sha256": hashlib.sha256(canonical_json(old)).hexdigest(),
                "authority_transition": [len(old), len(new)],
                "authority_instances": "separate_ephemeral_registries", "production_authority": False,
                "provider_identity": "synthetic_disposable_lane",
                "predecessor_admission": predecessor_admission,
                "engineering_command_types": sorted(set(engineering_ws.command_types)),
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
        print("Seq-13 " + lane["key"] + " disposable contract: " + json.dumps(summary, sort_keys=True))
        return result


async def _label_scenarios(stage, scenarios, owned, entity_id, governed, refused_plan, runtime, raw, label, entity):
    from ha_mcp_engineering.governance.label_registry import predicted_label_id

    stage[0] = "create"
    applied, sent, value = await governed(runtime, {"operations": [{"operation": "create", **CANARY}]})
    created = await label(CANARY_ID)
    if created is not None:
        owned["labels"].append(CANARY_ID)
    scenarios["create"] = {**value, "expected": {"label_id": CANARY_ID, **CANARY}, "readback": created,
                           "predicted_label_id": predicted_label_id(CANARY["name"]),
                           "core_label_id": created and created["label_id"]}

    stage[0] = "edit"
    applied, sent, value = await governed(runtime, {"operations": [
        {"operation": "update", "label_id": CANARY_ID, **EDITED}]})
    scenarios["edit"] = {**value, "expected": {"label_id": CANARY_ID, **EDITED},
                         "readback": await label(CANARY_ID),
                         "sent_fields": sorted(k for k in (sent[0] if sent else {}) if k != "type")}

    stage[0] = "create_then_assign"
    applied, sent, value = await governed(runtime, {"operations": [
        {"operation": "create", **ASSIGN, "entity_ids": [entity_id]}]})
    assigned = await label(ASSIGN_ID)
    if assigned is not None:
        owned["labels"].append(ASSIGN_ID)
    expected = {"label_id": ASSIGN_ID, "name": ASSIGN["name"], "color": None, "icon": None,
                "description": None, "entity_labels": [ASSIGN_ID]}
    readback = {**(assigned or {}), "entity_labels": sorted((await entity(entity_id))["labels"] or [])}
    scenarios["create_then_assign"] = {**value, "expected": expected, "readback": readback,
                                       "predicted_label_id": predicted_label_id(ASSIGN["name"]),
                                       "core_label_id": assigned and assigned["label_id"]}

    stage[0] = "duplicate_name"
    scenarios["duplicate_name"] = await refused_plan(runtime, {"operations": [
        {"operation": "create", "name": EDITED["name"].lower()}]})

    stage[0] = "rename_in_use"
    scenarios["rename_in_use"] = await refused_plan(runtime, {"operations": [
        {"operation": "update", "label_id": ASSIGN_ID, "name": EDITED["name"].upper()}]})

    stage[0] = "invalid_values"
    color = await refused_plan(runtime, {"operations": [
        {"operation": "update", "label_id": CANARY_ID, "color": "not a colour"}]})
    icon = await refused_plan(runtime, {"operations": [
        {"operation": "update", "label_id": CANARY_ID, "icon": "tag"}]})
    scenarios["invalid_values"] = {
        "result": "PASS", "plan_created": False, "ha_writes": color["ha_writes"] + icon["ha_writes"],
        "ha_commands": color["ha_commands"] + icon["ha_commands"],
        "reason_codes": {"color": color["reason_code"], "icon": icon["reason_code"]}}

    stage[0] = "stale_state"
    drift = "Seq-13 raw drift"

    async def raw_drift():
        await raw.ok({"type": LABEL_UPDATE, "label_id": CANARY_ID, "description": drift})
    applied, sent, value = await governed(runtime, {"operations": [
        {"operation": "update", "label_id": CANARY_ID, "color": "teal"}]}, between=raw_drift)
    after = await label(CANARY_ID)
    scenarios["stale_state"] = {**value, "drift_retained": after is not None and after["description"] == drift
                                and after["color"] == EDITED["color"]}


async def _availability_scenarios(stage, scenarios, owned, entity_id, governed, refused_plan, runtime, raw, entity):
    def exposure(entry):
        return {"should_expose": ((entry.get("options") or {}).get(ASSISTANT) or {}).get("should_expose")}

    stage[0] = "disable"
    _, _, value = await governed(runtime, {"operation": "disable", "entity_ids": [entity_id]})
    scenarios["disable"] = {**value, "expected": {"disabled_by": "user"},
                            "readback": {"disabled_by": (await entity(entity_id))["disabled_by"]}}

    stage[0] = "already_disabled"
    scenarios["already_disabled"] = await refused_plan(runtime, {"operation": "disable", "entity_ids": [entity_id]})

    stage[0] = "enable"
    _, _, value = await governed(runtime, {"operation": "enable", "entity_ids": [entity_id]})
    scenarios["enable"] = {**value, "expected": {"disabled_by": None},
                           "readback": {"disabled_by": (await entity(entity_id))["disabled_by"]}}

    stage[0] = "enable_config_entry"
    # A lane-owned config-entry-backed target: enable must read its config entry and
    # count the entry's other entities (HAMCP-165 review1 M4). Owned before any wait.
    owned["config_entry"] = await raw.create_config_entry(CONFIG_ENTRY_DOMAIN, {
        "name": "Seq13 Entry " + secrets.token_hex(4), "after_time": "08:00:00", "before_time": "20:00:00"})
    backed = None
    for _ in range(100):
        backed = next((item["entity_id"] for item in await raw.ok({"type": REGISTRY_LIST})
                       if item.get("config_entry_id") == owned["config_entry"]), None)
        if backed is not None:
            break
        await asyncio.sleep(0.1)
    assert backed is not None
    await raw.ok({"type": ENTITY_UPDATE, "entity_id": backed, "disabled_by": "user"})
    observed = {}
    _, _, value = await governed(runtime, {"operation": "enable", "entity_ids": [backed]}, observed=observed)
    (disclosure,) = [item["dry_run_results"]["entity_availability_exposure"]
                     for item in observed["plan"]["dry_run_results"]["operations"]]
    read = disclosure.get("config_entry") or {}
    scenarios["enable_config_entry"] = {
        **value, "expected": {"disabled_by": None},
        "readback": {"disabled_by": (await entity(backed))["disabled_by"]},
        "config_entry_reads": sorted(CONFIG_ENTRY_READS & set(observed["commands"])),
        "config_entry": {k: read.get(k) for k in ("status", "domain", "supports_unload", "disabled_by")},
        "other_entities_in_entry": disclosure.get("other_entities_in_entry"), "reload": disclosure.get("reload")}

    # Core may already hold an explicit value for a new helper; start from the opposite one.
    first = "expose" if exposure(await entity(entity_id))["should_expose"] is False else "unexpose"
    for operation in (first, "unexpose" if first == "expose" else "expose"):
        stage[0] = operation
        _, _, value = await governed(runtime, {"operation": operation, "entity_ids": [entity_id],
                                               "assistant": ASSISTANT})
        scenarios[operation] = {**value, "expected": {"should_expose": operation == "expose"},
                                "readback": exposure(await entity(entity_id))}

    stage[0] = "not_in_registry"
    assert (await raw.call({"type": ENTITY_GET, "entity_id": ABSENT})).get("error", {}).get("code") == "not_found"
    scenarios["not_in_registry"] = await refused_plan(runtime, {"operation": "disable", "entity_ids": [ABSENT]})

    stage[0] = "stale_state"
    drift = "Seq13 Raw Drift"

    async def raw_drift():
        await raw.ok({"type": ENTITY_UPDATE, "entity_id": entity_id, "name": drift})
    _, _, value = await governed(runtime, {"operation": "disable", "entity_ids": [entity_id]}, between=raw_drift)
    after = await entity(entity_id)
    scenarios["stale_state"] = {**value, "drift_retained": after["name"] == drift and after["disabled_by"] is None}
