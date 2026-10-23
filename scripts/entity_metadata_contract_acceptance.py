"""HAMCP-179 exact-Core entity-registry metadata evidence lane.

Called only by the disposable Alarmo-slot harness (HAMCP-159: every non-empty
support-window Core slot). No standalone/live CLI.

Exercises the dormant core.entity_registry_metadata_operation profile against
the exact window Core under two CI-only ephemeral authorities: a predecessor
without the capability (must refuse before Home Assistant) and an extension
with it appended. The production signed registry is never read for authority,
changed or signed. Every Home Assistant object the lane writes is synthetic and
created inside the disposable instance; the one kitchen_sink entity used for the
Core 2026.10 area/name rule is restored exactly.
"""
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
CAPABILITY = "core.entity_registry_metadata_operation"
ADAPTER = "compiled-core-entity-registry-metadata-operation-v1"
TOOL = "create_entity_metadata_plan"
GET, UPDATE = "config/entity_registry/get", "config/entity_registry/update"
REFERENCE_KEYS = ("capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint")
# The registry commands the reviewed gateway may send; label creation is HAMCP-069A-2.
REVIEWED_COMMANDS = frozenset({GET, UPDATE, "config/label_registry/list",
                               "config/area_registry/list", "config/category_registry/list"})
SCENARIOS = ("closed_field_update", "restore", "no_change", "not_found", "area_requires_own_name",
             "unknown_label", "alias_normalization", "response_lost_applied", "response_lost_unapplied")
CATEGORY_SCOPE = "helpers"
HELPER_NAME = "HAMCP179 Entity Metadata"
ABSENT = "input_boolean.hamcp179_absent"
UNKNOWN_LABEL = "hamcp179_unknown"
HUB_SWITCH = "switch.hub_switch"
# Bound into F3 task declarations only. A plain container has no Supervisor
# lifecycle provider; the registry contract under test does not depend on it.
SYNTHETIC_PROVIDER_IDENTITY = {"slug": "hamcp179_disposable_lane",
                               "evidence_hash": hashlib.sha256(b"HAMCP-179 synthetic provider lock").hexdigest()}
# Every owned fixture type whose absence cleanup reads back independently.
FIXTURE_ABSENCE = ("area", "category", "helper_config", "helper_entity", "labels")
DUPLICATE_KEY = "receipt_duplicate_key"


class DuplicateReceiptKey(ValueError):
    reason_code = DUPLICATE_KEY

    def __init__(self):
        super().__init__(DUPLICATE_KEY + ": evidence JSON repeats an object key")


def _unique_members(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateReceiptKey()
        value[key] = item
    return value


def load_receipt(raw):
    """The one JSON boundary for retained, standalone and cleanup evidence.

    Ordinary decoding keeps the last of repeated keys, so a FAIL beside a PASS
    would vanish before validation. A repeated key at any depth refuses.
    """
    return json.loads(raw, object_pairs_hook=_unique_members)


class RawRefusal(AssertionError):
    def __init__(self, command, code):
        super().__init__("Synthetic lane setup command refused")
        self.diagnostic = {"command": command, "error_code": code}


class RawWebSocket:
    """Lane-owned loopback client for setup, raw controls and independent readback.

    The Engineering client deliberately discards Home Assistant error codes; raw
    controls must observe them (``invalid_info``, ``not_found``) exactly.
    """

    def __init__(self, configured):
        self.url = configured.websocket_url
        self.token = configured.ha_token

    async def call(self, payload):
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.ws_connect(self.url) as websocket:
                assert (await websocket.receive_json()).get("type") == "auth_required"
                await websocket.send_json({"type": "auth", "access_token": self.token})
                assert (await websocket.receive_json()).get("type") == "auth_ok"
                await websocket.send_json({"id": 1, **payload})
                while True:
                    message = await websocket.receive_json()
                    if message.get("id") == 1 and message.get("type") == "result":
                        return message

    async def ok(self, payload):
        message = await self.call(payload)
        if message.get("success") is not True:
            raise RawRefusal(payload.get("type"), (message.get("error") or {}).get("code"))
        return message.get("result")

    async def refused(self, payload):
        """Return the Home Assistant error code; an accepted command is a finding."""
        message = await self.call(payload)
        if message.get("success") is True:
            return None
        return (message.get("error") or {}).get("code")


class ObservedWebSocket:
    """Engineering's real client with observation and controlled response loss."""

    def __init__(self, inner):
        self.inner = inner
        self.command_types = []
        self.updates_sent = 0
        self.update_attempts = 0
        self.lose = None

    async def command(self, payload):
        from ha_mcp_engineering.errors import HomeAssistantTimeoutError
        kind = payload.get("type")
        self.command_types.append(kind)
        if kind != UPDATE:
            return await self.inner.command(payload)
        self.update_attempts += 1
        lose, self.lose = self.lose, None
        if lose == "unsent":
            raise HomeAssistantTimeoutError()
        self.updates_sent += 1
        result = await self.inner.command(payload)
        if lose == "after_send":
            raise HomeAssistantTimeoutError()
        return result

    def __getattr__(self, name):
        return getattr(self.inner, name)


def source_profile():
    from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
    (profile,) = [p for p in CORE_RUNTIME_CAPABILITY_PROFILES if p.capability_id == CAPABILITY]
    reference = {k: profile.to_mapping()[k] for k in REFERENCE_KEYS}
    if reference["adapter_id"] != ADAPTER:
        raise ValueError("Entity metadata adapter differs from the reviewed lane")
    return reference


def engineering_source():
    def rev(spec):
        # Popen, not run: offline cleanup tests stub subprocess.run as the Docker inventory.
        with subprocess.Popen(["git", "rev-parse", spec], cwd=ROOT, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL) as process:
            value = process.communicate(timeout=30)[0].decode().strip()
        if process.returncode != 0 or len(value) != 40 or set(value) - set("0123456789abcdef"):
            raise ValueError("Engineering source revision unavailable")
        return value
    return {"commit": rev("HEAD"), "runtime_tree": rev("HEAD:hass_mcp_engineering_beta/ha_mcp_engineering")}


def managed(entry):
    from ha_mcp_engineering.governance.entity_metadata import MANAGED_FIELDS
    value = {field: entry.get(field) for field in MANAGED_FIELDS}
    value["labels"] = sorted(value["labels"] or [])
    value["categories"] = dict(sorted((value["categories"] or {}).items()))
    return value


def unmanaged(entry):
    from ha_mcp_engineering.governance.entity_metadata import MANAGED_FIELDS, _UNBOUND_FIELDS
    return {k: v for k, v in entry.items() if k not in {*MANAGED_FIELDS, *_UNBOUND_FIELDS}}


def _scenario_checks(name, value, labels):
    """Exact per-scenario expectations; never relaxed to match an observation."""
    assert value["result"] == "PASS"
    if name in {"closed_field_update", "restore"}:
        assert value["task_state"] == "succeeded_verified" and value["dispatch_count"] == 1
        assert value["ha_updates"] == 1 and value["readback"] == value["expected"]
        assert value["unmanaged_fields_preserved"] is True
    if name == "closed_field_update":
        expected = value["expected"]
        assert expected["hidden_by"] == "user" and expected["labels"] == sorted(labels)
        assert None in expected["aliases"] and expected["area_id"] and expected["categories"]
        assert expected["name"] and expected["icon"]
    if name == "restore":
        assert value["expected"] == value["preflight"]
    if name == "no_change":
        assert value == {"result": "PASS", "status": "no_change", "plan_created": False,
                         "provider_dispatch_occurred": False, "ha_updates": 0}
    if name == "not_found":
        assert value == {"result": "PASS", "error_code": "entity_not_found", "plan_created": False,
                         "provider_dispatch_occurred": False, "ha_updates": 0}
    if name == "area_requires_own_name":
        assert value["preconditions"] == {"device_bound": True, "device_has_parent": False,
                                          "own_name": False, "original_name": False, "area": False}
        assert value["planner"] == {"area_without_name": "entity_metadata_area_requires_own_name",
                                    "clear_name_with_area": "entity_metadata_area_requires_own_name"}
        assert value["raw"] == {"area_without_name": "invalid_info", "name_with_area": "accepted",
                                "clear_name_with_area": "invalid_info"}
        assert value["ha_updates"] == 0 and value["restored"] is True
    if name == "unknown_label":
        assert value["planner_reason"] == "entity_metadata_reference_not_found" and value["ha_updates"] == 0
        assert value["raw_written"] == [labels[0], UNKNOWN_LABEL] and value["raw_readback"] == [labels[0]]
        assert value["restored"] is True
    if name == "alias_normalization":
        assert value["planner_reason"] == "entity_metadata_invalid_value" and value["ha_commands"] == 0
        assert value["raw_written"] == [" Lamp ", "", None] and value["raw_readback"] == ["Lamp", None]
        assert value["restored"] is True
    if name == "response_lost_applied":
        assert value["task_state"] == "succeeded_verified" and value["dispatch_count"] == 1
        assert value["ha_updates"] == 1 and value["indeterminate"] is True and value["readback_applied"] is True
        assert value["replay_reused"] is True and value["replay_updates"] == 0
    if name == "response_lost_unapplied":
        assert value["task_state"] == "failed_post_dispatch" and value["outcome"] == "verification_mismatch"
        assert value["dispatch_count"] == 1 and value["ha_updates"] == 0 and value["indeterminate"] is True
        assert value["readback_unchanged"] is True
        assert value["replay"] == "duplicate_apply_attempt" and value["replay_updates"] == 0


def verify_result(result, *, core_version, support_slot):
    """Offline receipt verification against the window and current source.

    Refuses a receipt for another Core, slot, image digest, adapter, compiled
    contract fingerprint or Engineering runtime tree. It does not substitute
    for execution.
    """
    from alarmo_inspection_contract_acceptance import slot_binding
    assert type(core_version) is str and type(support_slot) is str
    binding = slot_binding(core_version)
    assert binding["slot"] == support_slot
    assert result["result"] == "PASS" and result["core"] == core_version
    assert type(result.get("support_slot")) is str and result["support_slot"] == support_slot
    assert result["core_image"] == binding["image"]
    assert result["image_index_digest"] == binding["image_index_digest"]
    assert len(result["image_receipt_sha256"]) == 64
    reference = source_profile()
    assert result["added_capability"] == reference
    assert all(result[key] == reference[key] for key in REFERENCE_KEYS)
    assert result["engineering_source"]["runtime_tree"] == engineering_source()["runtime_tree"]
    assert len(result["engineering_source"]["commit"]) == 40
    assert result["authority_transition"] == [21, 22]
    assert result["authority_instances"] == "separate_ephemeral_registries"
    assert result["production_authority"] is False and result["fixture_cleanup"] is True
    assert result["fixture_absence"] == dict.fromkeys(FIXTURE_ABSENCE, True)
    assert result["provider_identity"] == "synthetic_disposable_lane"
    assert len(result["predecessor_capabilities_sha256"]) == 64
    assert result["predecessor_admission"] == {
        "acquired": False, "ha_requests": 0, "ha_updates": 0,
        "reason": "authority_unavailable", "control_capability_acquired": True}
    assert set(result["engineering_command_types"]) <= REVIEWED_COMMANDS
    assert UPDATE in result["engineering_command_types"]
    labels = result["synthetic_labels"]
    assert type(labels) is list and len(labels) == 2 and labels == sorted(labels)
    assert UNKNOWN_LABEL not in labels
    scenarios = result["scenarios"]
    # Receipts are written with sorted keys, so the exact set is bound, not order.
    assert type(scenarios) is dict and sorted(scenarios) == sorted(SCENARIOS)
    for name in SCENARIOS:
        _scenario_checks(name, scenarios[name], labels)
    return {"result": "PASS", "core": core_version, "support_slot": support_slot,
            "contract_fingerprint": reference["contract_fingerprint"], "scenarios": len(SCENARIOS),
            "authority_transition": [21, 22], "fixture_cleanup": True}


def receipt_bytes(result):
    return (json.dumps(result, sort_keys=True, indent=2) + "\n").encode()


def verify_receipt_files(receipt_path, cleanup_path, *, core_version, support_slot):
    """Offline signing-session check of one slot's two artifact files.

    Both are parsed strictly. The entity receipt is re-verified against the
    window and current source, and the cleanup receipt must bind its exact bytes.
    """
    raw = Path(receipt_path).read_bytes()
    summary = verify_result(load_receipt(raw), core_version=core_version, support_slot=support_slot)
    cleanup = load_receipt(Path(cleanup_path).read_bytes())
    assert (cleanup["result"] == "PASS" and cleanup["core"] == core_version
            and cleanup["support_slot"] == support_slot)
    assert cleanup["owned_containers_remaining"] == 0 and cleanup["owned_network_remaining"] is False
    expected = {**summary, "receipt_sha256": hashlib.sha256(raw).hexdigest()}
    assert cleanup["required_entity_metadata"] == expected
    return expected


async def cleanup_fixtures(raw, owned, hub_before, *, attempts=50, delay=0.1):
    """Delete every owned fixture, then read back each one's absence.

    A delete Core acknowledged is not evidence of absence; only an independent
    read-back is. Returns the per-type absence, or None when nothing was owned.
    """
    if hub_before is not None:
        await raw.ok({"type": UPDATE, "entity_id": HUB_SWITCH,
                      "area_id": hub_before.get("area_id"), "name": hub_before.get("name")})
    if owned.get("helper"):
        await raw.ok({"type": "input_boolean/delete", "input_boolean_id": owned["helper"]})
    for label_id in owned.get("labels", []):
        await raw.ok({"type": "config/label_registry/delete", "label_id": label_id})
    if owned.get("category"):
        await raw.ok({"type": "config/category_registry/delete", "scope": CATEGORY_SCOPE,
                      "category_id": owned["category"]})
    if owned.get("area"):
        await raw.ok({"type": "config/area_registry/delete", "area_id": owned["area"]})
    if not owned:
        return None

    async def ids(command, field, **arguments):
        return {item[field] for item in await raw.ok({"type": command, **arguments})}

    async def helper_entity_absent():
        if not owned.get("helper"):
            return True
        found = await raw.call({"type": GET, "entity_id": "input_boolean." + owned["helper"]})
        return found.get("success") is not True and (found.get("error") or {}).get("code") == "not_found"

    checks = {
        "area": lambda: ids("config/area_registry/list", "area_id"),
        "category": lambda: ids("config/category_registry/list", "category_id", scope=CATEGORY_SCOPE),
        "helper_config": lambda: ids("input_boolean/list", "id"),
        "labels": lambda: ids("config/label_registry/list", "label_id"),
    }
    created = {"area": {owned.get("area")} - {None}, "category": {owned.get("category")} - {None},
               "helper_config": {owned.get("helper")} - {None}, "labels": set(owned.get("labels", []))}
    absence = {}
    for name in FIXTURE_ABSENCE:
        for _ in range(attempts):
            if name == "helper_entity":
                absent = await helper_entity_absent()
            else:
                absent = not created[name] & await checks[name]()
            if absent:
                break
            await asyncio.sleep(delay)
        absence[name] = absent
    return absence


async def run_disposable(configured, *, expected_image, core_version):
    """Preserve a bounded source location and stage on failure, never exception values."""
    stage = ["binding"]
    try:
        return await _run_disposable(configured, expected_image=expected_image,
                                     core_version=core_version, stage=stage)
    except Exception as exc:
        cursor = exc.__traceback__
        lines = []
        while cursor is not None:
            if cursor.tb_frame.f_code.co_filename == __file__:
                lines.append(cursor.tb_lineno)
            cursor = cursor.tb_next
        exc.contract_missing_key = "entity_metadata"
        exc.contract_diagnostic = {"entity_metadata_source_lines": lines[:12],
                                   "entity_metadata_stage": stage[0]}
        if isinstance(exc, RawRefusal):
            exc.contract_diagnostic["raw_refusal"] = exc.diagnostic
        raise


async def _run_disposable(configured, *, expected_image, core_version, stage):
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
    from ha_mcp_engineering.governance.entity_metadata_disclosure import runtime_consumer_evidence
    from ha_mcp_engineering.governance.resources import ConfigurationResourceGateway
    from ha_mcp_engineering.governance.service import ChangeGovernanceService
    from ha_mcp_engineering.governance.storage import ChangePlanRepository
    from ha_mcp_engineering.request_context import begin_request, end_request
    from ha_mcp_engineering.ha_core_readmission import CoreRuntime
    from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
    from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
    from ha_mcp_engineering.ha_core_readmission.routes import static_tool_requirements, static_tool_target
    from ha_mcp_engineering.signed_registry import canonical_json

    binding = slot_binding(core_version)
    entry = lane_entry(core_version)
    if (urlsplit(configured.ha_url).hostname not in {"127.0.0.1", "localhost", "::1"}
            or os.environ.get("HA_CONTRACT_LANE") != binding["lane"]
            or expected_image != binding["image"]
            or entry["image_index_digest"] != binding["image_index_digest"]):
        raise ValueError("Exact owned disposable entity metadata lane required")
    image_path = Path(os.environ["REAL_HA_ALARMO_IMAGE_RESULT"])
    image = load_receipt(image_path.read_bytes())
    assert (image["result"] == "PASS" and image["index"] == entry["image_index_digest"]
            and image["core_version"] == core_version and image["support_slot"] == binding["slot"])
    output = Path(os.environ["REAL_HA_ENTITY_METADATA_RESULT"])
    probe_profile = probe_profile_for(core_version)
    added = source_profile()
    refs = [{k: p.to_mapping()[k] for k in REFERENCE_KEYS} for p in CORE_RUNTIME_CAPABILITY_PROFILES
            # HAMCP-069B/165: the dormant availability/exposure and label profiles are not signed here.
            if p.capability_id not in {"core.entity_registry_availability_operation",
                                       "core.entity_assistant_exposure_operation",
                                       "core.label_registry_operation"}]
    old = [r for r in refs if r["capability_id"] != CAPABILITY]
    new = old + [added]
    assert len(old) == 21 and len(new) == 22 and len({r["capability_id"] for r in new}) == 22
    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    evidence = b"Synthetic HAMCP-179 entity metadata lane; no production applicability"
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
    hub_before = None
    stage[0] = "authority"
    with tempfile.TemporaryDirectory(prefix="hamcp179-entity-metadata-") as directory:
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
        raw = RawWebSocket(configured)
        service = None

        async def plan(runtime, arguments):
            """Mirror the MCP route: exact-target acquisition before the application."""
            telemetry, token = begin_request("hamcp179-entity-metadata-plan")
            telemetry.caller_id = "hamcp179-synthetic-owner"
            commits = None
            authority_lease = runtime.acquire(static_tool_requirements(TOOL, arguments),
                                              target=static_tool_target(TOOL, arguments))
            try:
                if authority_lease is None:
                    return None, telemetry.ha_request_count

                def authorize():
                    nonlocal commits
                    if commits is not None:
                        return runtime.revalidate(authority_lease, commits)
                    commits = runtime.consume(authority_lease)
                    return commits is not None
                telemetry.core_dispatch_authorizer = authorize
                return await service.create_entity_metadata_plan(dict(arguments)), telemetry.ha_request_count
            finally:
                if authority_lease is not None:
                    telemetry.core_dispatch_authorizer = lambda: False
                    if commits is not None:
                        assert runtime.finish(commits)
                    else:
                        runtime.release(authority_lease)
                end_request(token)

        async def refused_plan(runtime, arguments):
            updates = engineering_ws.updates_sent
            try:
                await plan(runtime, arguments)
            except GovernanceError as exc:
                details = exc.details if isinstance(exc.details, dict) else {}
                assert details.get("plan_created") is False
                assert details.get("provider_dispatch_occurred") is False
                assert engineering_ws.updates_sent == updates
                return exc.code.value, details.get("reason_code")
            raise AssertionError("Entity metadata planner accepted a refused request")

        async def approve_apply(created, lose=None):
            telemetry, token = begin_request("hamcp179-entity-metadata-apply")
            telemetry.caller_id = "hamcp179-synthetic-owner"
            try:
                granted = service.approve(created["plan_id"], created["plan_hash"])
                for _ in range(3):
                    _, csrf = await service.issue_external_csrf(created["plan_id"], granted["challenge_id"])
                    granted = await service.decide_external_approval(
                        plan_id=created["plan_id"], challenge_id=granted["challenge_id"],
                        expected_plan_hash=created["plan_hash"], approval_kind=granted["approval_kind"],
                        approval_action=granted["approval_action"], csrf_nonce=csrf, decision="approve",
                        approver_principal="home_assistant_admin_ingress:hamcp179-synthetic-reviewer")
                    if granted.get("status") != "approval_pending":
                        break
                else:
                    raise AssertionError("Synthetic approval did not settle")
                engineering_ws.lose = lose
                return await service.apply(created["plan_id"], created["plan_hash"])
            finally:
                engineering_ws.lose = None
                end_request(token)

        async def reapply(created):
            telemetry, token = begin_request("hamcp179-entity-metadata-replay")
            telemetry.caller_id = "hamcp179-synthetic-owner"
            try:
                return await service.apply(created["plan_id"], created["plan_hash"])
            finally:
                end_request(token)

        def child(task_id):
            (item,) = service.f3_runtime.children.declarations_for_task(task_id)
            record = service.f3_runtime.children.get(item["child_id"])
            codes = {code for event in record.events for code in event["diagnostic_codes"]}
            return record, codes

        async def read(entity_id):
            return await raw.ok({"type": GET, "entity_id": entity_id})

        async def governed(runtime, arguments, expected, preflight):
            updates = engineering_ws.updates_sent
            created, _ = await plan(runtime, arguments)
            assert created is not None and created.get("plan_id")
            applied = await approve_apply(created)
            record, _ = child(applied["task_id"])
            after = await read(arguments["entity_id"])
            value = {"result": "PASS", "task_state": applied["task_state"],
                     "dispatch_count": record.dispatch_count, "ha_updates": engineering_ws.updates_sent - updates,
                     "expected": expected, "readback": managed(after), "preflight": managed(preflight),
                     "unmanaged_fields_preserved": unmanaged(after) == unmanaged(preflight)}
            return value, after

        dependency_runtime = DependencyAnalysisRuntime()
        fixture_cleanup = False
        try:
            predecessor = await authority(old, "predecessor")
            stage[0] = "predecessor"
            target = static_tool_target(TOOL, {"entity_id": HUB_SWITCH})
            control = predecessor.acquire(("core.dependency_helper_planning",))
            assert control is not None
            predecessor.release(control)
            assert predecessor.acquire((CAPABILITY,), target=target) is None
            refused, requests = await plan(predecessor, {"entity_id": HUB_SWITCH, "name": "HAMCP179"})
            assert refused is None and type(requests) is int and requests == 0
            assert engineering_ws.updates_sent == 0 and engineering_ws.command_types == []
            predecessor_admission = {"acquired": False, "ha_requests": requests, "ha_updates": 0,
                                     "reason": "authority_unavailable", "control_capability_acquired": True}

            stage[0] = "extension_authority"
            runtime = await authority(new, "extension")
            dependency_runtime.configure(rest, HomeAssistantWebSocketClient(configured),
                                         secret="hamcp179-disposable-dependency-evidence", core_runtime=runtime)
            resource_gateway = ConfigurationResourceGateway(rest, engineering_ws)
            resource_gateway.entity_metadata_consumer_evidence = runtime_consumer_evidence()
            audit_secret = "hamcp179-disposable-entity-metadata-audit"

            async def provider_identity():
                return dict(SYNTHETIC_PROVIDER_IDENTITY)
            service = ChangeGovernanceService(
                ChangePlanRepository(root / "plans"), resource_gateway,
                AuditLogger(str(root / "audit.jsonl"), audit_secret),
                sensitive_values=(audit_secret, configured.ha_token, configured.access_secret),
                entity_metadata_gateway=resource_gateway.entity_metadata)
            service.f3_runtime = F3RuntimeIntegration(
                service=service, storage_root=str(root / "plans"), configuration_gateway=resource_gateway,
                backup_gateway=None, lifecycle_gateway=None, provider_identity_reader=provider_identity,
                retention_days=90, core_runtime=runtime)
            await service.f3_runtime.recover_once("startup")

            stage[0] = "fixture_setup"
            owned["area"] = (await raw.ok({"type": "config/area_registry/create", "name": "HAMCP179 Study"}))["area_id"]
            owned["labels"] = []
            for name in ("HAMCP179 Night", "HAMCP179 Outdoor"):
                owned["labels"].append((await raw.ok({"type": "config/label_registry/create",
                                                      "name": name}))["label_id"])
            labels = sorted(owned["labels"])
            owned["category"] = (await raw.ok({"type": "config/category_registry/create",
                                               "scope": CATEGORY_SCOPE, "name": "HAMCP179 Lighting"}))["category_id"]
            # A run-unique helper: Home Assistant restores a deleted entity's registry
            # metadata when the same unique ID is re-created.
            owned["helper"] = (await raw.ok({"type": "input_boolean/create",
                                             "name": HELPER_NAME + " " + secrets.token_hex(4)}))["id"]
            entity_id = "input_boolean." + owned["helper"]
            for _ in range(100):
                if (await raw.call({"type": GET, "entity_id": entity_id})).get("success"):
                    break
                await asyncio.sleep(0.1)
            preflight = await read(entity_id)
            assert preflight["device_id"] is None and preflight["hidden_by"] is None
            assert preflight["name"] is None and preflight["icon"] is None and preflight["aliases"] == [None]
            assert preflight["area_id"] is None and not preflight["labels"] and not preflight["categories"]
            scenarios = {}

            with patch.object(dependency_package, "DEPENDENCY_ANALYSIS", dependency_runtime):
                stage[0] = "closed_field_update"
                aliases = ["Desk lamp", None, "Reading light"]
                expected = {"aliases": aliases, "area_id": owned["area"],
                            "categories": {CATEGORY_SCOPE: owned["category"]}, "hidden_by": "user",
                            "icon": "mdi:desk-lamp", "labels": labels, "name": "HAMCP179 Desk"}
                scenarios["closed_field_update"], updated = await governed(runtime, {
                    "entity_id": entity_id, "name": expected["name"], "icon": expected["icon"],
                    "aliases": aliases, "area_id": owned["area"], "hidden": True, "add_labels": labels,
                    "categories": expected["categories"]}, expected, preflight)

                stage[0] = "restore"
                original = managed(preflight)
                scenarios["restore"], restored = await governed(runtime, {
                    "entity_id": entity_id, "name": original["name"], "icon": original["icon"],
                    "aliases": original["aliases"], "area_id": original["area_id"], "hidden": False,
                    "remove_labels": labels, "categories": {CATEGORY_SCOPE: None}}, original, preflight)

                stage[0] = "no_change"
                updates = engineering_ws.updates_sent
                unchanged, _ = await plan(runtime, {"entity_id": entity_id, "name": original["name"]})
                scenarios["no_change"] = {"result": "PASS", "status": unchanged["status"],
                                          "plan_created": unchanged["plan_created"],
                                          "provider_dispatch_occurred": unchanged["provider_dispatch_occurred"],
                                          "ha_updates": engineering_ws.updates_sent - updates}

                stage[0] = "not_found"
                assert (await raw.call({"type": GET, "entity_id": ABSENT})).get("error", {}).get("code") == "not_found"
                code, _ = await refused_plan(runtime, {"entity_id": ABSENT, "name": "HAMCP179 Absent"})
                scenarios["not_found"] = {"result": "PASS", "error_code": code, "plan_created": False,
                                          "provider_dispatch_occurred": False, "ha_updates": 0}

                stage[0] = "area_requires_own_name"
                hub_before = await read(HUB_SWITCH)
                devices = await raw.ok({"type": "config/device_registry/list"})
                (device,) = [d for d in devices if d.get("id") == hub_before.get("device_id")]
                preconditions = {"device_bound": bool(hub_before.get("device_id")),
                                 "device_has_parent": bool(device.get("parent_device_id")),
                                 "own_name": bool(hub_before.get("name")),
                                 "original_name": bool(hub_before.get("original_name")),
                                 "area": bool(hub_before.get("area_id"))}
                updates = engineering_ws.updates_sent
                planner = {"area_without_name": (await refused_plan(
                    runtime, {"entity_id": HUB_SWITCH, "area_id": owned["area"]}))[1]}
                raw_results = {"area_without_name": await raw.refused(
                    {"type": UPDATE, "entity_id": HUB_SWITCH, "area_id": owned["area"]})}
                assert managed(await read(HUB_SWITCH)) == managed(hub_before)
                accepted = await raw.refused({"type": UPDATE, "entity_id": HUB_SWITCH,
                                              "name": "HAMCP179 Hub", "area_id": owned["area"]})
                raw_results["name_with_area"] = "accepted" if accepted is None else accepted
                planner["clear_name_with_area"] = (await refused_plan(
                    runtime, {"entity_id": HUB_SWITCH, "name": None}))[1]
                raw_results["clear_name_with_area"] = await raw.refused(
                    {"type": UPDATE, "entity_id": HUB_SWITCH, "name": None})
                await raw.ok({"type": UPDATE, "entity_id": HUB_SWITCH, "area_id": None, "name": None})
                hub_after = await read(HUB_SWITCH)
                scenarios["area_requires_own_name"] = {
                    "result": "PASS", "preconditions": preconditions, "planner": planner, "raw": raw_results,
                    "ha_updates": engineering_ws.updates_sent - updates,
                    "restored": managed(hub_after) == managed(hub_before)}
                hub_before = None if scenarios["area_requires_own_name"]["restored"] else hub_before

                stage[0] = "unknown_label"
                updates = engineering_ws.updates_sent
                _, reason = await refused_plan(runtime, {"entity_id": entity_id, "add_labels": [UNKNOWN_LABEL]})
                written = [labels[0], UNKNOWN_LABEL]
                await raw.ok({"type": UPDATE, "entity_id": entity_id, "labels": written})
                observed = sorted((await read(entity_id))["labels"])
                await raw.ok({"type": UPDATE, "entity_id": entity_id, "labels": []})
                scenarios["unknown_label"] = {
                    "result": "PASS", "planner_reason": reason, "ha_updates": engineering_ws.updates_sent - updates,
                    "raw_written": written, "raw_readback": observed,
                    "restored": managed(await read(entity_id)) == original}

                stage[0] = "alias_normalization"
                commands = len(engineering_ws.command_types)
                _, reason = await refused_plan(runtime, {"entity_id": entity_id, "aliases": [" Lamp "]})
                ha_commands = len(engineering_ws.command_types) - commands
                written = [" Lamp ", "", None]
                await raw.ok({"type": UPDATE, "entity_id": entity_id, "aliases": written})
                observed = (await read(entity_id))["aliases"]
                await raw.ok({"type": UPDATE, "entity_id": entity_id, "aliases": original["aliases"]})
                scenarios["alias_normalization"] = {
                    "result": "PASS", "planner_reason": reason, "ha_commands": ha_commands,
                    "raw_written": written, "raw_readback": observed,
                    "restored": managed(await read(entity_id)) == original}

                stage[0] = "response_lost_applied"
                updates = engineering_ws.updates_sent
                created, _ = await plan(runtime, {"entity_id": entity_id, "name": "HAMCP179 Lost Applied"})
                applied = await approve_apply(created, lose="after_send")
                record, codes = child(applied["task_id"])
                sent = engineering_ws.updates_sent - updates
                attempts = engineering_ws.update_attempts
                replay = await reapply(created)
                scenarios["response_lost_applied"] = {
                    "result": "PASS", "task_state": applied["task_state"], "dispatch_count": record.dispatch_count,
                    "ha_updates": sent, "indeterminate": "provider_response_indeterminate" in codes,
                    "readback_applied": (await read(entity_id))["name"] == "HAMCP179 Lost Applied",
                    "replay_reused": replay.get("task_reused") is True,
                    "replay_updates": engineering_ws.update_attempts - attempts}

                stage[0] = "response_lost_unapplied"
                updates = engineering_ws.updates_sent
                created, _ = await plan(runtime, {"entity_id": entity_id, "name": "HAMCP179 Lost Unapplied"})
                applied = await approve_apply(created, lose="unsent")
                record, codes = child(applied["task_id"])
                attempts = engineering_ws.update_attempts
                try:
                    await reapply(created)
                    replay_code = "redispatch_permitted"
                except GovernanceError as exc:
                    replay_code = exc.code.value
                scenarios["response_lost_unapplied"] = {
                    "result": "PASS", "task_state": applied["task_state"], "outcome": record.normalized_outcome,
                    "dispatch_count": record.dispatch_count, "ha_updates": engineering_ws.updates_sent - updates,
                    "indeterminate": "provider_response_indeterminate" in codes,
                    "readback_unchanged": (await read(entity_id))["name"] == "HAMCP179 Lost Applied",
                    "replay": replay_code, "replay_updates": engineering_ws.update_attempts - attempts}
            assert all(runtime.health_snapshot()[name] == 0 for name in (
                "issued_lease_count", "active_commit_count", "fallback_count"))
            result = {
                "result": "PASS", "core": core_version, "support_slot": binding["slot"],
                "core_image": binding["image"], "image_index_digest": binding["image_index_digest"],
                "image_receipt_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                "engineering_source": engineering_source(), **added, "added_capability": added,
                "predecessor_capabilities_sha256": hashlib.sha256(canonical_json(old)).hexdigest(),
                "authority_transition": [len(old), len(new)],
                "authority_instances": "separate_ephemeral_registries", "production_authority": False,
                "provider_identity": "synthetic_disposable_lane",
                "predecessor_admission": predecessor_admission,
                "engineering_command_types": sorted(set(engineering_ws.command_types)),
                "synthetic_labels": labels, "scenarios": scenarios, "fixture_cleanup": False}
        finally:
            stage[0] = "cleanup:" + stage[0]
            try:
                try:
                    fixture_absence = await cleanup_fixtures(raw, owned, hub_before)
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
            summary = verify_result(load_receipt(receipt_bytes(result)), core_version=core_version,
                                    support_slot=binding["slot"])
        except Exception:
            # Retain the sanitized synthetic receipt; outer cleanup rejects it.
            output.write_bytes(receipt_bytes({**result, "result": "FAIL"}))
            raise
        output.write_bytes(receipt_bytes(result))
        print("Entity metadata disposable contract: " + json.dumps(summary, sort_keys=True))
        return result
