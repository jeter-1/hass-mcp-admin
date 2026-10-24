"""HAMCP-155 exact-Core governed deletion evidence lane for the seq-13 batch.

Defined, not wired: no harness or workflow calls this lane yet. Wiring it into
the disposable Alarmo-slot harness, and the CI container lane, is a follow-up
(``ci.yml`` is outside HAMCP-155). Even when called it refuses unless
``REAL_HA_SEQ13_LANES`` is exactly ``enabled``, which only the owner's seq-13
batch evidence run sets. No standalone or live CLI.

The lane exercises the dormant ``core.configuration_delete_operation`` profile
against the exact window Core under two CI-only ephemeral authorities: a
predecessor holding the shipped signed set without it, and an extension with it
appended. The production signed registry is never read for authority, changed
or signed.

Refusals are expected outcomes, not failures: the predecessor's service refusal
(``pending_signed_registry_admission``, zero Home Assistant requests), the
not-found planner refusal and the drifted-target stale refusal are each recorded
with their exact code and asserted. Every absence the lane claims is read back
independently by its own raw clients: the config GET answers 404, no entity
registry entry has the target's platform and unique ID, and the state GET
answers 404. Every Home Assistant object the lane writes is synthetic, created
inside the disposable instance, and deleted and read back as absent at cleanup.
"""
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
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
OUTPUT_ENV = "REAL_HA_CONFIGURATION_DELETE_RESULT"
CAPABILITY = "core.configuration_delete_operation"
ADAPTER = "compiled-core-configuration-delete-operation-v1"
TOOL = "create_configuration_delete_plan"
PENDING = "pending_signed_registry_admission"
# Every runtime profile this source compiles that the shipped signed set lacks.
DORMANT = frozenset({CAPABILITY, "core.entity_registry_availability_operation",
                     "core.entity_assistant_exposure_operation"})
SHIPPED_SIGNED_COUNT = 22
AUTHORITY_TRANSITION = [SHIPPED_SIGNED_COUNT, SHIPPED_SIGNED_COUNT + 1]
REGISTRY_LIST = "config/entity_registry/list"
SCENARIOS = ("delete_automation", "recreate_automation", "delete_script", "recreate_script",
             "protected_effect", "not_found", "stale_state")
NOT_FOUND = "configuration_delete_target_not_found"
STALE = "stale_target_state"
BASIS_PROTECTED_EFFECT = "delete_removes_protected_effect"
ABSENT_ID = "hamcp155_absent"
FIXTURE_ABSENCE = ("automation_config", "script_config", "registry_entries", "states")
SYNTHETIC_PROVIDER_IDENTITY = {"slug": "hamcp155_disposable_lane",
                               "evidence_hash": hashlib.sha256(b"HAMCP-155 synthetic provider lock").hexdigest()}


def enabled():
    """The single wiring switch; anything but the exact value keeps the lane off."""
    return os.environ.get(ENABLE_FLAG) == ENABLED


def source_profile():
    from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
    (profile,) = [p for p in CORE_RUNTIME_CAPABILITY_PROFILES if p.capability_id == CAPABILITY]
    reference = {k: profile.to_mapping()[k] for k in REFERENCE_KEYS}
    if reference["adapter_id"] != ADAPTER:
        raise ValueError("Configuration delete adapter differs from the reviewed lane")
    return reference


def shipped_references():
    """The predecessor: every compiled profile except the dormant set."""
    from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
    refs = [{k: p.to_mapping()[k] for k in REFERENCE_KEYS} for p in CORE_RUNTIME_CAPABILITY_PROFILES
            if p.capability_id not in DORMANT]
    assert len(refs) == SHIPPED_SIGNED_COUNT and len({r["capability_id"] for r in refs}) == len(refs)
    return refs


def automation_config(target_id, action):
    return {"id": target_id, "alias": "HAMCP155 " + target_id, "description": "HAMCP-155 synthetic",
            "mode": "single", "triggers": [{"trigger": "event", "event_type": "hamcp155_" + target_id}],
            "conditions": [], "actions": [action]}


def script_config(target_id):
    return {"alias": "HAMCP155 " + target_id, "mode": "single",
            "sequence": [{"event": "hamcp155_" + target_id, "event_data": {}}]}


def _absent(readback):
    return readback == {"config_status": 404, "registry_entry": None, "state_status": 404}


def _scenario_checks(name, value):
    """Exact per-scenario expectations; never relaxed to match an observation."""
    if name in {"delete_automation", "delete_script"}:
        assert value["task_state"] == "succeeded_verified" and value["dispatch_counts"] == [1]
        assert value["write_methods"] == ["DELETE"] and value["ha_writes"] == 1
        assert value["protected"] is False and value["risk_level"] == "medium"
        assert value["preflight_present"] is True and _absent(value["readback"])
    if name in {"recreate_automation", "recreate_script"}:
        assert value["rollback_status"] == "rollback_plan_created"
        assert value["rollback_dispatched_on_create"] is False and value["operations_excluded"] == []
        assert value["task_state"] == "succeeded_verified" and value["dispatch_counts"] == [1]
        assert value["write_methods"] == ["POST"] and value["ha_writes"] == 1
        assert value["config_matches_pre_image"] is True
        assert value["registry_entry_id_restored"] is True and value["state_present"] is True
    if name == "protected_effect":
        assert value["plan_created"] is True and value["protected"] is True
        assert BASIS_PROTECTED_EFFECT in value["basis"] and value["risk_level"] == "high"
        assert value["removes_statement"] is True and value["ha_writes"] == 0
    if name == "not_found":
        assert value["preflight_config_status"] == 404
        assert value["reason_code"] == NOT_FOUND and value["plan_created"] is False
        assert value["provider_dispatch_occurred"] is False and value["ha_writes"] == 0
    if name == "stale_state":
        assert value["task_state"] == "failed_pre_dispatch" and value["dispatch_counts"] == [0]
        assert STALE in value["codes"] and value["ha_writes"] == 0
        assert value["drifted_config_retained"] is True


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
    assert result["result"] == "PASS" and result["lane"] == "configuration_delete"
    assert result["core"] == core_version and result["support_slot"] == support_slot
    assert result["core_image"] == binding["image"]
    assert result["image_index_digest"] == binding["image_index_digest"]
    assert len(result["image_receipt_sha256"]) == 64
    reference = source_profile()
    assert result["added_capability"] == reference
    assert result["engineering_source"]["runtime_tree"] == engineering_source()["runtime_tree"]
    assert len(result["engineering_source"]["commit"]) == 40
    assert result["authority_transition"] == AUTHORITY_TRANSITION
    assert result["authority_instances"] == "separate_ephemeral_registries"
    assert result["production_authority"] is False and result["fixture_cleanup"] is True
    assert result["fixture_absence"] == dict.fromkeys(FIXTURE_ABSENCE, True)
    assert result["provider_identity"] == "synthetic_disposable_lane"
    assert len(result["predecessor_capabilities_sha256"]) == 64
    assert result["predecessor_admission"] == {
        "route_acquired": False, "service_reason_code": PENDING, "plan_created": False,
        "ha_requests": 0, "ha_writes": 0, "control_capability_acquired": True}
    assert result["engineering_write_methods"] == ["DELETE", "POST"]
    scenarios = result["scenarios"]
    # Receipts are written with sorted keys, so the exact set is bound, not order.
    assert type(scenarios) is dict and sorted(scenarios) == sorted(SCENARIOS)
    for name in SCENARIOS:
        assert scenarios[name]["result"] == "PASS"
        _scenario_checks(name, scenarios[name])
    return {"result": "PASS", "lane": "configuration_delete", "core": core_version,
            "support_slot": support_slot, "contract_fingerprint": reference["contract_fingerprint"],
            "scenarios": len(SCENARIOS), "authority_transition": AUTHORITY_TRANSITION, "fixture_cleanup": True}


def config_route(resource_type, target_id):
    """The bound runtime route for one automation or script config (HAMCP-200)."""
    from ha_mcp_engineering.clients.paths import rest_path
    if resource_type == "automation":
        return rest_path("/config/automation/config/{}", target_id)
    if resource_type == "script":
        return rest_path("/config/script/config/{}", target_id)
    raise ValueError("unsupported resource type")


def state_route(resource_type, target_id):
    from ha_mcp_engineering.clients.paths import rest_path
    return rest_path("/states/{}", resource_type + "." + target_id, kinds=("entity_id",))


class RawRest:
    """Lane-owned REST client for setup and independent readback.

    A separate guarded Engineering client, so every path is a bound runtime
    route (HAMCP-200) and none of its requests is counted as Engineering's.
    """

    def __init__(self, configured):
        from ha_mcp_engineering.clients import HomeAssistantRestClient
        self.client = HomeAssistantRestClient(configured)

    async def call(self, method, route, body=None):
        from ha_mcp_engineering.clients.rest import ExpectedHttpStatus
        from ha_mcp_engineering.errors import HomeAssistantApiError
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

    async def ok(self, method, route, body=None):
        status, payload = await self.call(method, route, body)
        if status != 200:
            raise RawRefusal(method + " " + route.split("/")[1], status)
        return payload


class ObservedRest:
    """Engineering's real REST client; every non-GET request is counted."""

    def __init__(self, inner):
        self.inner = inner
        self.writes = []

    async def request(self, method, path, *args, **kwargs):
        if method != "GET":
            self.writes.append(method)
        return await self.inner.request(method, path, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.inner, name)


async def independent_readback(rest, raw, resource_type, target_id):
    """The three absence facts, each read by the lane, never inferred."""
    config_status, config = await rest.call("GET", config_route(resource_type, target_id))
    matches = [item for item in await raw.ok({"type": REGISTRY_LIST})
               if item.get("platform") == resource_type and item.get("unique_id") == target_id]
    assert len(matches) <= 1
    entry = matches[0] if matches else None
    state_status, _state = await rest.call("GET", state_route(resource_type, target_id))
    return {"config_status": config_status, "registry_entry": None if entry is None else entry.get("id"),
            "state_status": state_status}, config


async def cleanup_fixtures(rest, raw, owned, *, attempts=50, delay=0.1):
    """Delete every owned fixture with the lane's raw client, then read back absence.

    An acknowledged delete is not evidence of absence; only an independent
    read-back is. Returns the per-type absence, or None when nothing was owned.
    """
    if not owned:
        return None
    for resource_type, target_id in owned:
        status, _ = await rest.call("GET", config_route(resource_type, target_id))
        if status == 200:
            await rest.ok("DELETE", config_route(resource_type, target_id))
    absence = dict.fromkeys(FIXTURE_ABSENCE, True)
    for resource_type, target_id in owned:
        for _ in range(attempts):
            readback, _config = await independent_readback(rest, raw, resource_type, target_id)
            if _absent(readback):
                break
            await asyncio.sleep(delay)
        absence[resource_type + "_config"] &= readback["config_status"] == 404
        absence["registry_entries"] &= readback["registry_entry"] is None
        absence["states"] &= readback["state_status"] == 404
    return absence


async def run_disposable(configured, *, expected_image, core_version):
    """Preserve a bounded source location and stage on failure, never exception values."""
    stage = ["binding"]
    try:
        if not enabled():
            raise ValueError("Seq-13 lanes are defined but not wired")
        return await _run_disposable(configured, expected_image=expected_image,
                                     core_version=core_version, stage=stage)
    except Exception as exc:
        cursor = exc.__traceback__
        lines = []
        while cursor is not None:
            if cursor.tb_frame.f_code.co_filename == __file__:
                lines.append(cursor.tb_lineno)
            cursor = cursor.tb_next
        exc.contract_missing_key = "seq13_configuration_delete"
        exc.contract_diagnostic = {"configuration_delete_source_lines": lines[:12],
                                   "configuration_delete_stage": stage[0]}
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
        raise ValueError("Exact owned disposable configuration delete lane required")
    image_path = Path(os.environ["REAL_HA_ALARMO_IMAGE_RESULT"])
    image = load_receipt(image_path.read_bytes())
    assert (image["result"] == "PASS" and image["index"] == entry["image_index_digest"]
            and image["core_version"] == core_version and image["support_slot"] == binding["slot"])
    output = Path(os.environ[OUTPUT_ENV])
    probe_profile = probe_profile_for(core_version)
    added = source_profile()
    old = shipped_references()
    new = old + [added]
    assert [len(old), len(new)] == AUTHORITY_TRANSITION
    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    evidence = b"Synthetic HAMCP-155 configuration delete lane; no production applicability"
    entry.update(evidence_sha256=hashlib.sha256(evidence).hexdigest(),
                 probe_profile_id=probe_profile.profile_id,
                 probe_profile_sha256=probe_profile.contract_fingerprint)

    def signed(references):
        candidate = canonical_json(prepare_candidate(entry={**entry, "capabilities": references}, evidence=evidence,
                                                     previous=None, public_key=key.public_key(), now=now))
        return sign_candidate(candidate, expected_sha256=hashlib.sha256(candidate).hexdigest(),
                              previous=None, key=key, now=now)

    runtimes = []
    owned = []
    stage[0] = "authority"
    with tempfile.TemporaryDirectory(prefix="hamcp155-configuration-delete-") as directory:
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

        engineering_rest = ObservedRest(HomeAssistantRestClient(configured))
        engineering_ws = HomeAssistantWebSocketClient(configured)
        rest = RawRest(configured)
        raw = RawWebSocket(configured)

        def compose(runtime, name):
            """The production composition (governance/runtime.py), one service per authority."""
            resource_gateway = ConfigurationResourceGateway(engineering_rest, engineering_ws)
            resource_gateway.entity_metadata_consumer_evidence = runtime_consumer_evidence()
            resource_gateway.availability_exposure_snapshot_reader = read_runtime_dependency_snapshot
            audit_secret = "hamcp155-disposable-configuration-delete-audit"

            async def provider_identity():
                return dict(SYNTHETIC_PROVIDER_IDENTITY)
            composed = ChangeGovernanceService(
                ChangePlanRepository(root / ("plans-" + name)), resource_gateway,
                AuditLogger(str(root / ("audit-" + name + ".jsonl")), audit_secret),
                sensitive_values=(audit_secret, configured.ha_token, configured.access_secret),
                entity_metadata_gateway=resource_gateway.entity_metadata,
                entity_availability_exposure_gateway=resource_gateway.availability_exposure,
                configuration_delete_gateway=resource_gateway.configuration_delete)
            composed.f3_runtime = F3RuntimeIntegration(
                service=composed, storage_root=str(root / ("plans-" + name)),
                configuration_gateway=resource_gateway, backup_gateway=None, lifecycle_gateway=None,
                provider_identity_reader=provider_identity, retention_days=90, core_runtime=runtime)
            return composed

        async def plan(runtime, service, arguments):
            """Mirror the MCP route: exact-target acquisition before the application."""
            telemetry, token = begin_request("hamcp155-configuration-delete-plan")
            telemetry.caller_id = "hamcp155-synthetic-owner"
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
                return await service.create_configuration_delete_plan(dict(arguments)), telemetry.ha_request_count
            finally:
                if authority_lease is not None:
                    telemetry.core_dispatch_authorizer = lambda: False
                    if commits is not None:
                        assert runtime.finish(commits)
                    else:
                        runtime.release(authority_lease)
                end_request(token)

        async def refused_plan(runtime, service, arguments):
            """A refusal is the expected outcome here; its exact code is the evidence."""
            writes = len(engineering_rest.writes)
            try:
                await plan(runtime, service, arguments)
            except GovernanceError as exc:
                details = exc.details if isinstance(exc.details, dict) else {}
                assert len(engineering_rest.writes) == writes
                return details
            raise AssertionError("Configuration delete planner accepted a refused request")

        async def approve_apply(service, created):
            telemetry, token = begin_request("hamcp155-configuration-delete-apply")
            telemetry.caller_id = "hamcp155-synthetic-owner"
            try:
                granted = service.approve(created["plan_id"], created["plan_hash"])
                for _ in range(3):
                    _, csrf = await service.issue_external_csrf(created["plan_id"], granted["challenge_id"])
                    granted = await service.decide_external_approval(
                        plan_id=created["plan_id"], challenge_id=granted["challenge_id"],
                        expected_plan_hash=created["plan_hash"], approval_kind=granted["approval_kind"],
                        approval_action=granted["approval_action"], csrf_nonce=csrf, decision="approve",
                        approver_principal="home_assistant_admin_ingress:hamcp155-synthetic-reviewer")
                    if granted.get("status") != "approval_pending":
                        break
                else:
                    raise AssertionError("Synthetic approval did not settle")
                return await service.apply(created["plan_id"], created["plan_hash"])
            finally:
                end_request(token)

        async def rollback(service, plan_id):
            telemetry, token = begin_request("hamcp155-configuration-delete-rollback")
            telemetry.caller_id = "hamcp155-synthetic-owner"
            try:
                return await service.rollback_change(plan_id)
            finally:
                end_request(token)

        def children(service, task_id):
            records = [service.f3_runtime.children.get(item["child_id"])
                       for item in service.f3_runtime.children.declarations_for_task(task_id)]
            codes = sorted({code for record in records if record is not None
                            for event in record.events for code in event["diagnostic_codes"]})
            return [0 if record is None else record.dispatch_count for record in records], codes

        def disclosure(created):
            (operation,) = created["dry_run_results"]["operations"]
            return operation["dry_run_results"]["configuration_delete"]

        async def create_fixture(resource_type, target_id, config):
            owned.append((resource_type, target_id))
            await rest.ok("POST", config_route(resource_type, target_id), config)
            for _ in range(100):
                readback, stored = await independent_readback(rest, raw, resource_type, target_id)
                if readback["config_status"] == 200 and readback["registry_entry"] and readback["state_status"] == 200:
                    return readback, stored
                await asyncio.sleep(0.1)
            raise AssertionError("Synthetic fixture did not appear")

        async def governed_delete(runtime, service, resource_type, target_id):
            before, pre_image = await independent_readback(rest, raw, resource_type, target_id)
            writes = len(engineering_rest.writes)
            created, _ = await plan(runtime, service, {
                "deletions": [{"resource_type": resource_type, "target_id": target_id}],
                "reason": "HAMCP-155 disposable lane"})
            assert created is not None and created.get("plan_id")
            shown = disclosure(created)
            applied = await approve_apply(service, created)
            counts, codes = children(service, applied["task_id"])
            readback, _config = await independent_readback(rest, raw, resource_type, target_id)
            value = {"result": "PASS", "task_state": applied["task_state"], "dispatch_counts": counts,
                     "write_methods": engineering_rest.writes[writes:],
                     "ha_writes": len(engineering_rest.writes) - writes,
                     "protected": shown["protection"]["protected"],
                     "risk_level": str(created.get("risk_level", "")).lower(),
                     "preflight_present": before["config_status"] == 200 and before["registry_entry"] is not None
                     and before["state_status"] == 200, "readback": readback, "codes": codes}
            return value, created, pre_image, before

        async def governed_recreate(runtime, service, created, resource_type, target_id, pre_image, before):
            writes = len(engineering_rest.writes)
            rolled = await rollback(service, created["plan_id"])
            recreate = service.get_plan(rolled["rollback_plan_id"])
            applied = await approve_apply(service, recreate)
            counts, _codes = children(service, applied["task_id"])
            for _ in range(100):
                readback, stored = await independent_readback(rest, raw, resource_type, target_id)
                if readback["state_status"] == 200:
                    break
                await asyncio.sleep(0.1)
            return {"result": "PASS", "rollback_status": rolled["status"],
                    "rollback_dispatched_on_create": rolled["provider_dispatch_occurred"],
                    "operations_excluded": rolled["operations_excluded"], "task_state": applied["task_state"],
                    "dispatch_counts": counts, "write_methods": engineering_rest.writes[writes:],
                    "ha_writes": len(engineering_rest.writes) - writes,
                    "config_matches_pre_image": stored == pre_image,
                    "registry_entry_id_restored": readback["registry_entry"] == before["registry_entry"],
                    "state_present": readback["state_status"] == 200}

        dependency_runtime = DependencyAnalysisRuntime()
        fixture_cleanup = False
        try:
            suffix = secrets.token_hex(4)
            automation_id = "hamcp155_auto_" + suffix
            script_id = "hamcp155_script_" + suffix
            protected_id = "hamcp155_lock_" + suffix
            stale_id = "hamcp155_stale_" + suffix
            stage[0] = "fixture_setup"
            await create_fixture("automation", automation_id, automation_config(
                automation_id, {"event": "hamcp155_noop", "event_data": {}}))
            await create_fixture("script", script_id, script_config(script_id))
            await create_fixture("automation", protected_id, automation_config(
                protected_id, {"action": "lock.lock", "target": {"entity_id": "lock.front_door"}}))
            await create_fixture("automation", stale_id, automation_config(
                stale_id, {"event": "hamcp155_noop", "event_data": {}}))

            stage[0] = "predecessor"
            predecessor = await authority(old, "predecessor")
            predecessor_service = compose(predecessor, "predecessor")
            control = predecessor.acquire(("core.dependency_helper_planning",))
            assert control is not None
            predecessor.release(control)
            arguments = {"deletions": [{"resource_type": "automation", "target_id": automation_id}],
                         "reason": "HAMCP-155 predecessor refusal"}
            route, requests = await plan(predecessor, predecessor_service, arguments)
            assert route is None and requests == 0
            # The service is the first layer too: called directly, it refuses
            # before any read and stores nothing.
            telemetry, token = begin_request("hamcp155-predecessor-service")
            try:
                try:
                    await predecessor_service.create_configuration_delete_plan(dict(arguments))
                    raise AssertionError("Unadmitted configuration delete planning succeeded")
                except GovernanceError as exc:
                    details = exc.details if isinstance(exc.details, dict) else {}
                service_requests = telemetry.ha_request_count
            finally:
                end_request(token)
            assert predecessor_service.repository.list() == []
            predecessor_admission = {
                "route_acquired": False, "service_reason_code": details.get("reason_code"),
                "plan_created": details.get("plan_created", False),
                "ha_requests": requests + service_requests, "ha_writes": len(engineering_rest.writes),
                "control_capability_acquired": True}

            stage[0] = "extension_authority"
            runtime = await authority(new, "extension")
            dependency_runtime.configure(engineering_rest, HomeAssistantWebSocketClient(configured),
                                         secret="hamcp155-disposable-dependency-evidence", core_runtime=runtime)
            service = compose(runtime, "extension")
            await service.f3_runtime.recover_once("startup")
            scenarios = {}

            with patch.object(dependency_package, "DEPENDENCY_ANALYSIS", dependency_runtime):
                for resource_type, target_id in (("automation", automation_id), ("script", script_id)):
                    stage[0] = "delete_" + resource_type
                    value, created, pre_image, before = await governed_delete(
                        runtime, service, resource_type, target_id)
                    scenarios["delete_" + resource_type] = value
                    stage[0] = "recreate_" + resource_type
                    scenarios["recreate_" + resource_type] = await governed_recreate(
                        runtime, service, created, resource_type, target_id, pre_image, before)

                stage[0] = "protected_effect"
                writes = len(engineering_rest.writes)
                created, _ = await plan(runtime, service, {
                    "deletions": [{"resource_type": "automation", "target_id": protected_id}],
                    "reason": "HAMCP-155 protected effect"})
                shown = disclosure(created)
                scenarios["protected_effect"] = {
                    "result": "PASS", "plan_created": bool(created.get("plan_id")),
                    "protected": shown["protection"]["protected"], "basis": shown["protection"]["basis"],
                    "risk_level": str(created.get("risk_level", "")).lower(),
                    "removes_statement": any("removes these protected effects" in item
                                             for item in shown["consequence_statements"]),
                    "ha_writes": len(engineering_rest.writes) - writes}

                stage[0] = "not_found"
                status, _ = await rest.call("GET", config_route("automation", ABSENT_ID))
                details = await refused_plan(runtime, service, {
                    "deletions": [{"resource_type": "automation", "target_id": ABSENT_ID}],
                    "reason": "HAMCP-155 not found"})
                scenarios["not_found"] = {
                    "result": "PASS", "preflight_config_status": status, "reason_code": details.get("reason_code"),
                    "plan_created": details.get("plan_created"),
                    "provider_dispatch_occurred": details.get("provider_dispatch_occurred"), "ha_writes": 0}

                stage[0] = "stale_state"
                writes = len(engineering_rest.writes)
                created, _ = await plan(runtime, service, {
                    "deletions": [{"resource_type": "automation", "target_id": stale_id}],
                    "reason": "HAMCP-155 stale state"})
                drifted = automation_config(stale_id, {"event": "hamcp155_drifted", "event_data": {}})
                await rest.ok("POST", config_route("automation", stale_id), drifted)
                applied = await approve_apply(service, created)
                counts, codes = children(service, applied["task_id"])
                readback, stored = await independent_readback(rest, raw, "automation", stale_id)
                scenarios["stale_state"] = {
                    "result": "PASS", "task_state": applied["task_state"], "dispatch_counts": counts,
                    "codes": codes, "ha_writes": len(engineering_rest.writes) - writes,
                    "drifted_config_retained": readback["config_status"] == 200 and stored == drifted}
            assert all(runtime.health_snapshot()[name] == 0 for name in (
                "issued_lease_count", "active_commit_count", "fallback_count"))
            result = {
                "result": "PASS", "lane": "configuration_delete", "core": core_version,
                "support_slot": binding["slot"], "core_image": binding["image"],
                "image_index_digest": binding["image_index_digest"],
                "image_receipt_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                "engineering_source": engineering_source(), "added_capability": added,
                "predecessor_capabilities_sha256": hashlib.sha256(canonical_json(old)).hexdigest(),
                "authority_transition": [len(old), len(new)],
                "authority_instances": "separate_ephemeral_registries", "production_authority": False,
                "provider_identity": "synthetic_disposable_lane",
                "predecessor_admission": predecessor_admission,
                "engineering_write_methods": sorted(set(engineering_rest.writes)),
                "scenarios": scenarios, "fixture_cleanup": False}
        finally:
            stage[0] = "cleanup:" + stage[0]
            try:
                try:
                    fixture_absence = await cleanup_fixtures(rest, raw, owned)
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
        print("Configuration delete disposable contract: " + json.dumps(summary, sort_keys=True))
        return result
