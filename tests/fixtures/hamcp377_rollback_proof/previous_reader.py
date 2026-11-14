"""HAMCP-377 rollback proof: the previous release reads what the current one wrote.

Run only by ``scripts/rollback_proof_lane.py``, in its own interpreter::

    python -B -I previous_reader.py READER_SOURCE SCENARIO ARGUMENT...

``READER_SOURCE`` is a ``git archive`` extraction of the previous release's
tagged commit. Every import of the runtime and of its offline test harness
resolves inside that tree; nothing is imported from the checkout this file
lives in. The data directory is never opened for writing: each scenario
copies what it needs into ``WORK`` first, and the lane compares the data
directory's file hashes before and after.

Scenarios (each prints exactly one JSON object on its last output line):

``registry DATA WORK RECORDS``
    The tree's registry client loads the test-signed journal the writer's dry
    run left (``DATA/dry-run/ha-core-release-registry.json``) under the public
    half of the dry run's key (``DATA/dry-run/ephemeral-public-key.txt``); its
    Core runtime then reconciles a synthetic Core 2026.11.0 observation
    against it.
``device STORE WORK NOW TASK_ID``
    Over one copy of the store the tree's typed power service controls the
    held switch and an unrelated switch; over another the tree's governed
    runtime runs its startup and periodic recovery passes.
``plans STORES WORK SUMMARY``
    For every mode the writer left, the tree's governance service lists,
    reads, approves, applies and rolls back the plans the writer named.

The driver asserts nothing. It records what the tree did; the lane classifies.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import unittest
import uuid


CORE_VERSION = "2026.11.0"
HELD_SWITCH = "switch.plug_child_lock"
OTHER_SWITCH = "switch.synthetic"
SHIPPED_READER = "tests/fixtures/f3_hamcp181_candidate/shipped_reader.py"


def emit(value: dict) -> None:
    print(json.dumps(value, sort_keys=True, default=str), flush=True)


def identity() -> dict:
    import ha_mcp_engineering
    from ha_mcp_engineering.version import SERVER_VERSION

    return {"reader_version": SERVER_VERSION,
            "reader_package": str(Path(ha_mcp_engineering.__file__).resolve().parent)}


def problem(error: BaseException) -> dict:
    code = getattr(error, "code", None)
    details = getattr(error, "details", None)
    out = {"type": type(error).__name__, "code": getattr(code, "value", code),
           "text": str(error)[:300]}
    if isinstance(details, dict):
        out["details"] = {key: details[key] for key in sorted(details)[:16]
                          if isinstance(details[key], (str, int, bool, type(None), list))}
    return out


async def attempt(function, *arguments, **keywords) -> dict:
    try:
        value = function(*arguments, **keywords)
        if asyncio.iscoroutine(value):
            value = await value
        return {"ok": True, "value": value}
    except Exception as error:  # the lane classifies; the driver only records
        return {"ok": False, "error": problem(error)}


def file_hashes(root: Path) -> dict:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.suffix not in {".lock", ".jsonl"}}


def changed(before: dict, after: dict) -> dict:
    return {"added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after)),
            "modified": sorted(key for key in set(before) & set(after) if before[key] != after[key])}


SIGNED_JOURNAL = "dry-run/ha-core-release-registry.json"
TEST_PUBLIC_KEY = "dry-run/ephemeral-public-key.txt"


# --- registry --------------------------------------------------------------------------------

async def registry(data: Path, work: Path, records: Path) -> None:
    from ha_mcp_engineering.ha_core_readmission.models import CORE_IDENTITY
    from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
    from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
    from ha_mcp_engineering.ha_core_readmission.runtime import (
        CORE_RECOVERY_FLAP_CONFIRMATIONS, CoreRuntime)
    from ha_mcp_engineering.ha_core_readmission.source import capability_evidence_for_probes

    logging.disable(logging.CRITICAL)
    signed = (data / SIGNED_JOURNAL).read_bytes()
    public_key = (data / TEST_PUBLIC_KEY).read_text(encoding="ascii").strip()
    fixture = json.loads(records.read_text(encoding="utf-8"))
    work.mkdir(parents=True, exist_ok=False)

    async def fetch(_url, _maximum):
        return signed

    client = CoreReleaseRegistry(
        enabled=True, public_key=public_key, fetcher=fetch, cache_path=work / "cache.json")

    class Source:
        async def capture_core_snapshot(self):
            evidence = capability_evidence_for_probes(
                version=CORE_VERSION, probe_profile=client.probe_profile_for(CORE_VERSION),
                include_typed_operations=True, include_integration_inspection=True,
                include_automation_baseline=True, include_entity_metadata=True,
                rest_config={"version": CORE_VERSION}, websocket_config={"version": CORE_VERSION},
                states=[{"entity_id": "sensor.synthetic", "state": "ready", "attributes": {}}],
                services=[{"domain": "input_boolean", "services": {}}],
                configuration_validation={"result": "valid", "errors": None, "warnings": None},
                websocket_results={
                    "areas": [], "floors": [], "labels": [], "entities": fixture["entities"],
                    "devices": fixture["devices"],
                    "dashboards": [{"url_path": "synthetic-dashboard"}],
                    "automation": {"id": "synthetic"}, "dashboard": {"views": []},
                    "trace_list": [],
                    "trace_get": {"_ha_mcp_engineering_expected_probe_error": "not_found"}})
            return {"identity": CORE_IDENTITY, "connected": True, "authenticated": True,
                    "session_id": "synthetic-rollback-proof",
                    "rest_config": {"version": CORE_VERSION},
                    "websocket_auth_ok": {"type": "auth_ok", "ha_version": CORE_VERSION},
                    "websocket_get_config": {"version": CORE_VERSION},
                    "capability_evidence": evidence}

    core = CoreRuntime()
    core.configure(object(), source=Source(), release_registry=client)
    before = await core.reconcile_once("synthetic_missing_authority")
    refreshed = await attempt(client.refresh)
    out = {**identity(), "scenario": "registry", "refreshed": refreshed,
           "journal_sha256": hashlib.sha256(signed).hexdigest(),
           "compiled": {profile.capability_id: profile.to_mapping()["contract_fingerprint"]
                        for profile in CORE_RUNTIME_CAPABILITY_PROFILES},
           "before_refresh_compatible_count": before.get("compatible_count"),
           "core": "synthetic observation built by the reader tree's "
                   "capability_evidence_for_probes; no Home Assistant"}
    authority = client.authority()
    entry = authority.entry_for("home-assistant-core", CORE_VERSION)
    out["authority"] = {"sequence": getattr(authority, "sequence", None),
                        "positive_authority_current": authority.positive_authority_current,
                        "surface_denied": authority.surface_denied,
                        "entry_present": entry is not None}
    # Each reference as this tree decoded it: the five fields of a reference object.
    references = [] if entry is None else [item.to_mapping() for item in entry.capabilities]
    out["entry_references"] = references
    out["selections"] = [
        {"capability_ids": list(item.capability_ids), "source": item.source.value,
         "status": item.status.value} for item in client.selections(CORE_VERSION)]
    snapshot = await core.reconcile_once("periodic")
    passes = 1
    for _ in range(CORE_RECOVERY_FLAP_CONFIRMATIONS - 1):
        if snapshot["recovery"]["recovery_state"] != "confirming":
            break
        snapshot = await core.reconcile_once("periodic")
        passes += 1
    text = json.dumps(snapshot, default=str)
    ids = [item["capability_id"] for item in references]
    out["reconcile"] = {
        "passes": passes, "recovery_state": snapshot["recovery"]["recovery_state"],
        "counts": {key: value for key, value in snapshot.items() if key.endswith("_count")},
        "withheld": {key: [[item.get("capability_id"), item.get("disposition"),
                            item.get("reason_code")] for item in snapshot.get(key, [])]
                     for key in ("held_capabilities", "quarantined_capabilities",
                                 "unavailable_capabilities")},
        "route_status": {item: core.route_status((item,)) for item in ids},
        "named_in_snapshot": [item for item in ids if f'"{item}"' in text]}
    emit(out)


# --- device ----------------------------------------------------------------------------------

def lock_rows(store: Path) -> list:
    state = json.loads((store / "f3-operation-locks-v1" / "state.json").read_text(encoding="utf-8"))
    return [[row["key"], row["mode"], row["task_id"], row["generation"], row["conflict_hold"],
             row["lease_expires_at"]] for row in state["records"]]


async def power(store: Path, now: datetime, task_id: str) -> dict:
    import tests.test_typed_power as harness
    from tests.test_typed_fan import Clock, Core
    from ha_mcp_engineering.power.contracts import PowerRequest, digest
    from ha_mcp_engineering.power.service import PowerCoreAuthority, PowerService
    from ha_mcp_engineering.providers.upstream_power import PowerProvider
    from ha_mcp_engineering.request_context import begin_request, end_request

    clock = Clock()
    clock.value = now
    transport = harness.PowerTransport(harness.PowerTests.version)
    transport.states[HELD_SWITCH] = {
        "entity_id": HELD_SWITCH, "state": "off", "last_updated": "synthetic-revision-1"}
    provider = PowerProvider(transport, lambda *arguments: transport.authority)
    service = PowerService(
        str(store), provider, PowerCoreAuthority(Core()), now=clock, sleep=clock.sleep)
    telemetry, token = begin_request("synthetic-hamcp377-lane")
    out: dict = {"provider": "synthetic PowerTransport from the reader tree's "
                             "tests/test_typed_power.py"}
    try:
        out["recover_once"] = await attempt(service.recover_once)
        out["health"] = service.health()
        out["hold_release_review"] = await attempt(service.hold_release_review, task_id)
        out["load"] = await attempt(lambda: service.load(task_id) is not None)
        for label, entity in (("held_switch", HELD_SWITCH), ("other_switch", OTHER_SWITCH)):
            writes_before = transport.writes
            request = PowerRequest(entity_id=entity, action="turn_on",
                                   operation_id=f"{int(clock().timestamp())}-{uuid.uuid4().hex}")
            telemetry.ordinary_power_binding = digest(request.model_dump())
            result = await attempt(service.control, request)
            if result["ok"]:
                value = result["value"]
                result["value"] = {key: value.get(key) for key in (
                    "state", "status", "terminal_outcome", "provider_attempt_count",
                    "dispatch_intent_recorded", "reason_code", "outcome_code", "blocking_tasks")}
            result["entity_id"] = entity
            result["provider_writes"] = transport.writes - writes_before
            out[label] = result
        out["provider_writes"] = transport.writes
        out["provider_calls"] = [str(item[0]) for item in transport.calls]
        out["health_after"] = service.health()
        await service.close()
    finally:
        end_request(token)
    return out


async def governed(reader: Path, store: Path, now: datetime) -> dict:
    spec = importlib.util.spec_from_file_location("hamcp377_shipped_reader", reader / SHIPPED_READER)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    from ha_mcp_engineering.request_context import begin_request, end_request

    gate: dict = {}
    driver.sdk_gate(gate)
    telemetry, token = begin_request("synthetic-hamcp377-lane")
    telemetry.caller_id = "synthetic-hamcp377-lane"
    out: dict = {"sdk": gate, "driver": SHIPPED_READER, "passes": [], "readiness": []}
    try:
        stack = driver.Stack("configuration", store, store.parent / "audit.jsonl", now)
        with stack.exit:
            for trigger in ("startup", "periodic", "periodic"):
                result = await attempt(stack.recovery_pass, trigger)
                out["passes"].append({"trigger": trigger, "ok": result["ok"],
                                      "error": result.get("error")})
                out["readiness"].append(stack.runtime.readiness_state())
            out["require_execution_ready"] = await attempt(stack.runtime._require_execution_ready)
            out["require_execution_ready"].pop("value", None)
            snapshot = stack.snapshot(store)
            out["snapshot"] = {key: snapshot.get(key) for key in (
                "readiness", "runtime_status", "runtime_execution_ready",
                "runtime_corrupt_record_count", "quarantined", "provider_calls")}
    finally:
        end_request(token)
    return out


async def device(reader: Path, source: Path, work: Path, now: datetime, task_id: str) -> None:
    out = {**identity(), "scenario": "device", "now": now.isoformat(), "task_id": task_id}
    work.mkdir(parents=True, exist_ok=False)
    for name in ("power", "governed"):
        store = work / name / "store"
        shutil.copytree(source, store)
        before, locks_before = file_hashes(store), lock_rows(store)
        part = await (power(store, now, task_id) if name == "power"
                      else governed(reader, store, now))
        part["locks_before"], part["locks_after"] = locks_before, lock_rows(store)
        part["files"] = changed(before, file_hashes(store))
        out[name] = part
    emit(out)


# --- plans -----------------------------------------------------------------------------------

def plans(stores: Path, work: Path, summary: dict) -> None:
    import tests.hamcp069b_fixtures as base
    from tests.hamcp155_fixtures import REVIEWER, ConfigurationDeleteServiceTestCase
    from ha_mcp_engineering.governance.models import ChangePlan

    out: dict = {**identity(), "scenario": "plans", "modes": {},
                 "provider": "synthetic Core store from the reader tree's "
                             "tests/hamcp155_fixtures.py"}

    class Reader(ConfigurationDeleteServiceTestCase):
        mode = ""

        async def asyncSetUp(self) -> None:
            self.copy = work / self.mode
            shutil.copytree(stores / self.mode, self.copy)
            self.seen: dict = {"startup_assertions": []}
            out["modes"][self.mode] = self.seen
            original = base.tempfile.TemporaryDirectory
            base.tempfile.TemporaryDirectory = lambda *_a, **_k: SimpleNamespace(
                name=str(self.copy), cleanup=lambda: None)
            try:
                await super().asyncSetUp()
            finally:
                base.tempfile.TemporaryDirectory = original

        async def restart_runtime(self) -> None:
            try:
                await super().restart_runtime()
            except AssertionError as error:
                self.seen["startup_assertions"].append(str(error)[:300])

        def writes(self) -> list:
            return ([["rest", *map(str, item)] for item in self.config_rest.writes()]
                    + [["websocket", json.dumps(item, sort_keys=True, default=str)[:200]]
                       for item in self.ws.writes()])

        async def call(self, keep: tuple, function, *arguments, **keywords) -> dict:
            before = len(self.writes())
            result = await attempt(function, *arguments, **keywords)
            if result["ok"]:
                value = result.pop("value")
                result["value"] = ({key: value.get(key) for key in keep if key in value}
                                   if isinstance(value, dict) else None)
                result["raw"] = value
            result["provider_writes"] = self.writes()[before:]
            return result

        async def decide(self, plan_id: str, plan_hash: str, pending: dict) -> dict:
            """Finish the exact-hash external approval the request opened."""

            granted = pending
            for _ in range(3):
                if granted.get("status") != "approval_pending":
                    return granted
                review, csrf = await self.service.issue_external_csrf(
                    plan_id, granted["challenge_id"])
                safety = review.get("safety_critical_acknowledgement")
                acknowledgement = ({
                    "acknowledged_objects": tuple(row["object_ref"] for row in safety["objects"]),
                    "acknowledged_projection_hash": safety["projection_hash"]} if safety else {})
                granted = await self.service.decide_external_approval(
                    plan_id=plan_id, challenge_id=granted["challenge_id"],
                    expected_plan_hash=plan_hash, approval_kind=granted["approval_kind"],
                    approval_action=granted["approval_action"], csrf_nonce=csrf,
                    decision="approve", approver_principal=REVIEWER, **acknowledgement)
            return granted

        async def test_read(self) -> None:
            seen, before = self.seen, file_hashes(self.copy)
            seen["readiness_after_startup"] = self.runtime.readiness_state()
            seen["synthetic_core_objects"] = []
            for resource_type, target_id in (("automation", "porch_light"),
                                             ("script", "porch_script")):
                if resource_type in self.mode:
                    self.add(resource_type, target_id)
                    seen["synthetic_core_objects"].append([resource_type, target_id])
            seen["decode"] = {}
            for path in sorted((self.copy / "plans").glob("*.json")):
                decoded = await attempt(
                    lambda: ChangePlan.from_dict(json.loads(path.read_text(encoding="utf-8"))))
                seen["decode"][path.stem] = (
                    {"ok": True, "status": decoded["value"].status} if decoded["ok"] else decoded)
            listing = await self.call(("count", "partial", "projection_failure_count"),
                                      self.service.list_plans, limit=100)
            listing.pop("raw", None)
            seen["list_plans"] = listing
            seen["plans"] = {}
            for role, facts in summary["modes"][self.mode].items():
                plan_id, plan_hash = facts["plan_id"], facts["plan_hash"]
                record: dict = {"plan_id": plan_id, "writer_status": facts["status"],
                                "operations": facts.get("operations")}
                record["get_plan"] = await self.call(
                    ("status", "plan_hash"), self.service.get_plan, plan_id)
                record["get_plan"].pop("raw", None)
                record["approve"] = await self.call(
                    ("status", "approval_kind", "approval_action"),
                    self.service.approve, plan_id, plan_hash)
                pending = record["approve"].pop("raw", None)
                if isinstance(pending, dict) and pending.get("challenge_id"):
                    record["approval_decision"] = await self.call(
                        ("status", "approval_kind", "approval_action"),
                        self.decide, plan_id, plan_hash, pending)
                    record["approval_decision"].pop("raw", None)
                    again = await self.call(("status",), self.service.get_plan, plan_id)
                    again.pop("raw", None)
                    record["status_after_decision"] = again
                record["apply"] = await self.call(
                    ("status", "task_state", "terminal_outcome"),
                    self.service.apply, plan_id, plan_hash)
                record["apply"].pop("raw", None)
                if record["apply"]["provider_writes"]:
                    # The plan just ran here; asking to roll that back is a new request,
                    # not a reading of what the writer left.
                    record["rollback_change"] = None
                else:
                    record["rollback_change"] = await self.call(
                        ("status", "rollback_plan_id"), self.service.rollback_change, plan_id)
                    record["rollback_change"].pop("raw", None)
                final = await self.call(("status",), self.service.get_plan, plan_id)
                final.pop("raw", None)
                record["status_after"] = final
                seen["plans"][role] = record
            for name, function in (
                    ("recover_periodic", lambda: self.runtime.recover_once("periodic")),
                    ("reconcile_execution_tasks", self.service.reconcile_execution_tasks),
                    ("reconcile_operational_plans", self.service.reconcile_operational_plans)):
                result = await self.call((), function)
                result.pop("raw", None)
                result.pop("value", None)
                seen[name] = result
            seen["readiness_final"] = self.runtime.readiness_state()
            seen["provider_writes"] = self.writes()
            seen["files"] = changed(before, file_hashes(self.copy))
            seen["file_count"] = len(before)

    work.mkdir(parents=True, exist_ok=False)
    modes = sorted(summary["modes"])
    suite = unittest.TestSuite(
        type(f"Reader_{mode}", (Reader,), {"mode": mode})("test_read") for mode in modes)
    result = unittest.TextTestRunner(verbosity=0, stream=sys.stderr).run(suite)
    out["harness_ok"] = result.wasSuccessful()
    out["harness_errors"] = [text[-800:] for _test, text in result.errors + result.failures]
    emit(out)


def main() -> None:
    reader = Path(sys.argv[1]).resolve()
    scenario, arguments = sys.argv[2], sys.argv[3:]
    sys.path[:0] = [str(reader / "hass_mcp_engineering_beta"), str(reader / "tests"), str(reader)]
    if scenario == "registry":
        asyncio.run(registry(Path(arguments[0]).resolve(), Path(arguments[1]).resolve(),
                             Path(arguments[2]).resolve()))
    elif scenario == "device":
        asyncio.run(device(reader, Path(arguments[0]).resolve(), Path(arguments[1]).resolve(),
                           datetime.fromisoformat(arguments[2]), arguments[3]))
    elif scenario == "plans":
        plans(Path(arguments[0]).resolve(), Path(arguments[1]).resolve(),
              json.loads(Path(arguments[2]).read_text(encoding="utf-8")))
    else:
        raise SystemExit(f"unknown scenario {scenario!r}")


if __name__ == "__main__":
    main()
