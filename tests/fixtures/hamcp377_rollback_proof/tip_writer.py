"""HAMCP-377 rollback proof: the current release writes, nothing else does.

Run only by ``scripts/rollback_proof_lane.py``, in its own interpreter::

    python -B -I tip_writer.py WRITER_SOURCE SCENARIO ARGUMENT...

``WRITER_SOURCE`` is the source tree of the release being rolled back from
(the checkout, or a ``git archive`` extraction of one commit). Every record
this driver leaves behind is written by that tree's own unmodified code,
driven through that tree's own offline test harness over a synthetic
provider. No Home Assistant, no socket, no container.

Scenarios (each prints exactly one JSON object on its last output line):

``registry OUT PUBLISHED_JOURNAL``
    The tree's registry tooling builds a journal signed by a key generated in
    this process and never written: sequence 1 carries the published Core
    2026.11.0 entry as published, sequence 2 extends it to every capability
    profile the tree compiles.
``device_hold STORE``
    One device-configuration operation ends in manual review with a conflict
    hold on its entity.
``device_crash STORE``
    The process dies inside the provider write of one device-configuration
    operation, after dispatch intent is durable.
``device_settle_release STORE TASK_ID ADVANCE_SECONDS``
    A fresh service on the crashed store settles the task through its own
    recovery passes, then releases the resulting hold through the service's
    own hold-release path (the path the Ingress page calls).
``plans OUT``
    Scene-create and created-object rollback plans in the states named by
    ``PLAN_MODES``, one store per mode.

The driver asserts nothing about the previous release. The lane classifies.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
import uuid


PUBLISHED_JOURNAL_SHA256 = "3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d"
CORE_VERSION = "2026.11.0"
REFERENCE_KEYS = ("capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint")
HOLD_RELEASE_PRINCIPAL = "home_assistant_admin_ingress:synthetic-hamcp377-lane"
PLAN_MODES = (
    "scene_pending", "scene_approved", "scene_applied", "scene_rollback_approved",
    "helper_rollback_approved", "automation_rollback_approved", "automation_rollback_pending",
    "script_rollback_approved", "automation_created",
)


def emit(value: dict) -> None:
    print(json.dumps(value, sort_keys=True, default=str), flush=True)


def identity() -> dict:
    import ha_mcp_engineering
    from ha_mcp_engineering.version import SERVER_VERSION

    return {"writer_version": SERVER_VERSION,
            "writer_package": str(Path(ha_mcp_engineering.__file__).resolve().parent)}


def registry(root: Path, out: Path, published: Path) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core_registry_one_tap import load_public_key
    from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
    from ha_mcp_engineering.signed_registry import canonical_json
    from prepare_core_release_registry import (
        EXTENSION_EVIDENCE_MODEL, parse_journal, prepare_candidate, sign_candidate)

    raw = published.read_bytes()
    published_sha256 = hashlib.sha256(raw).hexdigest()
    if published_sha256 != PUBLISHED_JOURNAL_SHA256:
        raise SystemExit(f"published journal sha256 {published_sha256} is not the pinned one")
    pinned_key = load_public_key(
        (root / "docs/evidence/hamcp222/core-public-key.txt").read_bytes(), pinned=True)
    accepted = parse_journal(raw, pinned_key).accepted
    entry, = (item.to_mapping() for item in accepted.entries if item.version == CORE_VERSION)
    previous = entry["capabilities"]
    signed = {reference["capability_id"] for reference in previous}
    added = [{key: profile.to_mapping()[key] for key in REFERENCE_KEYS}
             for profile in CORE_RUNTIME_CAPABILITY_PROFILES if profile.capability_id not in signed]

    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    first_evidence = b"Disposable rollback-proof test authority; not registry publication authority"
    first = {**entry, "evidence_sha256": hashlib.sha256(first_evidence).hexdigest()}
    candidate = canonical_json(prepare_candidate(
        entry=first, evidence=first_evidence, previous=None, public_key=key.public_key(), now=now))
    journal = sign_candidate(candidate, expected_sha256=hashlib.sha256(candidate).hexdigest(),
                             previous=None, key=key, now=now)
    extension_evidence = canonical_json({
        "model": EXTENSION_EVIDENCE_MODEL, "version": entry["version"],
        "previous_entry_sha256": hashlib.sha256(canonical_json(first)).hexdigest(),
        "previous_capabilities": previous, "added_capabilities": added,
        "review_artifacts_sha256": [
            hashlib.sha256(b"disposable rollback-proof review artifact").hexdigest()]})
    second = {**first, "capabilities": previous + added,
              "evidence_sha256": hashlib.sha256(extension_evidence).hexdigest()}
    candidate = canonical_json(prepare_candidate(
        entry=second, evidence=extension_evidence, previous=journal, public_key=key.public_key(),
        operation="extend-capabilities", now=now))
    journal = sign_candidate(candidate, expected_sha256=hashlib.sha256(candidate).hexdigest(),
                             previous=journal, key=key, now=now)
    public_key = key.public_key()
    del key
    reparsed = parse_journal(journal, public_key).accepted
    final, = (item.to_mapping() for item in reparsed.entries if item.version == CORE_VERSION)

    out.mkdir(parents=True, exist_ok=False)
    (out / "journal.json").write_bytes(journal)
    (out / "public_key").write_text(
        base64.b64encode(public_key.public_bytes_raw()).decode() + "\n", encoding="ascii")
    emit({**identity(), "scenario": "registry",
          "published_journal_sha256": published_sha256,
          "published_sequence": accepted.sequence,
          "journal_sha256": hashlib.sha256(journal).hexdigest(),
          "sequence": reparsed.sequence,
          "signer": "ed25519 key generated in this process and not retained",
          "built_by": "scripts/prepare_core_release_registry.py prepare_candidate and "
                      "sign_candidate: add, then extend-capabilities",
          "previous_references": [reference["capability_id"] for reference in previous],
          "added": [reference["capability_id"] for reference in added],
          "references": [reference["capability_id"] for reference in final["capabilities"]],
          "fingerprints": {reference["capability_id"]: reference["contract_fingerprint"]
                           for reference in final["capabilities"]},
          "compiled_profiles": len(CORE_RUNTIME_CAPABILITY_PROFILES)})


def lock_records(service) -> list[dict]:
    return [record.to_dict() for record in service.locks.records()]


async def device(store: Path, crash: bool) -> None:
    import tests.test_hamcp297_device_configuration as harness
    from ha_mcp_engineering.request_context import begin_request, end_request

    store.mkdir(parents=True, exist_ok=False)
    clock, core, home = harness.Clock(), harness.SignedCore(), harness.FakeHomeAssistant()
    service = harness.DeviceConfigurationService(
        str(store), home.gateway(), harness.DeviceConfigurationCoreAuthority(core),
        now=clock, sleep=clock.sleep)
    telemetry, token = begin_request("synthetic-device-configuration")
    request = harness.DeviceConfigurationRequest(
        entity_id=harness.SWITCH, action="turn_on",
        operation_id=f"{int(clock().timestamp())}-{uuid.uuid4().hex}")
    telemetry.ordinary_device_configuration_binding = harness.digest(request.model_dump())
    base = {**identity(), "scenario": "device_crash" if crash else "device_hold",
            "entity_id": harness.SWITCH, "action": "turn_on", "task_id": request.task_id,
            "operation_id": request.operation_id, "started_at": clock().isoformat(),
            "provider": "synthetic FakeHomeAssistant from the writer tree's "
                        "tests/test_hamcp297_device_configuration.py"}
    if crash:
        def die(payload):
            home.writes.append(payload)
            emit({**base, "died_in": "provider call_service, after dispatch intent was recorded",
                  "clock": clock().isoformat(), "provider_writes": len(home.writes),
                  "locks": lock_records(service)})
            os._exit(0)

        home.call_service = die
    else:
        home.mode = "ack_only"
    result = await service.control(request)
    summary = {"control": {key: result.get(key) for key in (
        "state", "status", "terminal_outcome", "dispatch_intent_recorded")}}
    clock.advance(200)
    settled = await service.reconcile(request.task_id)
    summary["reconcile"] = {key: settled.get(key) for key in ("state", "status", "terminal_outcome")}
    end_request(token)
    emit({**base, "result": summary, "clock": clock().isoformat(),
          "provider_writes": len(home.writes), "locks": lock_records(service)})
    os._exit(0)


async def device_settle_release(store: Path, task_id: str, advance: int) -> None:
    import tests.test_hamcp297_device_configuration as harness
    from ha_mcp_engineering.request_context import begin_request, end_request

    clock, core, home = harness.Clock(), harness.SignedCore(), harness.FakeHomeAssistant()
    clock.advance(advance)
    service = harness.DeviceConfigurationService(
        str(store), home.gateway(), harness.DeviceConfigurationCoreAuthority(core),
        now=clock, sleep=clock.sleep)
    _telemetry, token = begin_request("synthetic-device-configuration")
    out = {**identity(), "scenario": "device_settle_release", "task_id": task_id,
           "started_at": clock().isoformat(), "locks_before": lock_records(service),
           "recovery_passes": 0}
    for _ in range(3):
        await service.recover_once()
        out["recovery_passes"] += 1
        clock.advance(60)
    execution = service.executions.get(task_id)
    out["settled"] = {"terminal": execution.terminal,
                      "normalized_outcome": execution.normalized_outcome,
                      "locks": lock_records(service), "provider_writes": len(home.writes)}
    item = await service.hold_release_review(task_id)
    out["review"] = {key: item.get(key) for key in (
        "record_generation", "manual_review_reason", "comparison", "hold_binding",
        "prepared_hash", "readback_hash")}
    released = await service.release_hold(
        task_id, record_generation=item["record_generation"], prepared_hash=item["prepared_hash"],
        hold_binding=item["hold_binding"], reviewed_readback_hash=item["readback_hash"],
        authorized_principal=HOLD_RELEASE_PRINCIPAL)
    out["release"] = {"path": "DeviceConfigurationService.hold_release_review then release_hold",
                      "principal": HOLD_RELEASE_PRINCIPAL, "result": released,
                      "locks": lock_records(service), "provider_writes": len(home.writes)}
    out["health"] = service.health()
    out["clock"] = clock().isoformat()
    end_request(token)
    emit(out)
    os._exit(0)


def plans(out: Path) -> None:
    from tests.hamcp155_fixtures import automation_config, script_config
    from tests.hamcp329_298_fixtures import CreatedObjectServiceTestCase

    summary: dict = {**identity(), "scenario": "plans", "modes": {},
                     "provider": "synthetic store from the writer tree's "
                                 "tests/hamcp329_298_fixtures.py"}

    def create_operation(resource_type: str, target_id: str, config: dict) -> dict:
        return {"operation_id": "create_1", "resource_type": resource_type, "action": "create",
                "target_id": target_id, "proposed_config": config, "depends_on": []}

    class Writer(CreatedObjectServiceTestCase):
        mode = ""

        def keep(self, **roles) -> None:
            shutil.copytree(self.root, out / self.mode)
            summary["modes"][self.mode] = {role: self.facts(plan) for role, plan in roles.items()}

        def facts(self, plan: dict) -> dict:
            stored = self.service.get_plan(plan["plan_id"])
            return {"plan_id": stored["plan_id"], "plan_hash": stored["plan_hash"],
                    "status": stored.get("status"),
                    "operations": [[item.get("action"), item.get("resource_type"),
                                    item.get("target_id")]
                                   for item in stored.get("operations") or []]}

        async def created_automation(self) -> dict:
            return await self.applied(await self.configuration_plan(
                create_operation("automation", "porch_light", automation_config("porch_light"))))

        async def test_write(self) -> None:
            mode = self.mode
            if mode == "scene_pending":
                self.keep(plan=await self.scene_plan())
            elif mode == "scene_approved":
                plan = await self.scene_plan()
                await self.approve(plan)
                self.keep(plan=plan)
            elif mode == "scene_applied":
                self.keep(plan=await self.created_scene())
            elif mode == "scene_rollback_approved":
                created = await self.created_scene()
                _rollback, plan = await self.rollback_plan(created)
                self.keep(created=created, rollback=plan)
            elif mode == "helper_rollback_approved":
                created = await self.created_helper("input_boolean")
                _rollback, plan = await self.rollback_plan(created)
                self.keep(created=created, rollback=plan)
            elif mode == "automation_rollback_approved":
                created = await self.created_automation()
                _rollback, plan = await self.rollback_plan(created)
                self.keep(created=created, rollback=plan)
            elif mode == "automation_rollback_pending":
                created = await self.created_automation()
                _rollback, plan = await self.rollback_plan(created, approve=False)
                self.keep(created=created, rollback=plan)
            elif mode == "script_rollback_approved":
                created = await self.applied(await self.configuration_plan(
                    create_operation("script", "porch_script", script_config("porch_script"))))
                _rollback, plan = await self.rollback_plan(created)
                self.keep(created=created, rollback=plan)
            elif mode == "automation_created":
                self.keep(created=await self.created_automation())
            else:
                raise AssertionError(mode)

    out.mkdir(parents=True, exist_ok=False)
    suite = unittest.TestSuite(
        type(f"Writer_{mode}", (Writer,), {"mode": mode})("test_write") for mode in PLAN_MODES)
    result = unittest.TextTestRunner(verbosity=0, stream=sys.stderr).run(suite)
    summary["harness_ok"] = result.wasSuccessful()
    summary["harness_errors"] = [text[-800:] for _test, text in result.errors + result.failures]
    emit(summary)


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    scenario, arguments = sys.argv[2], sys.argv[3:]
    sys.path[:0] = [str(root / "hass_mcp_engineering_beta"), str(root / "tests"), str(root),
                    str(root / "scripts")]
    if scenario == "registry":
        registry(root, Path(arguments[0]).resolve(), Path(arguments[1]).resolve())
    elif scenario in {"device_hold", "device_crash"}:
        asyncio.run(device(Path(arguments[0]).resolve(), scenario == "device_crash"))
    elif scenario == "device_settle_release":
        asyncio.run(device_settle_release(
            Path(arguments[0]).resolve(), arguments[1], int(arguments[2])))
    elif scenario == "plans":
        plans(Path(arguments[0]).resolve())
    else:
        raise SystemExit(f"unknown scenario {scenario!r}")


if __name__ == "__main__":
    main()
