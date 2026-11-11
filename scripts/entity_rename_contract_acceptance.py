"""HAMCP-295/109 entity rename disposable acceptance; never a production entry point.

Called only by core_entity_rename_local after it owns and verifies the Core
container. Phase A: the planner, gateway and dependency-bound executor modules
are exercised directly against exact Core 2026.11.0 (the governance service is
wired separately). Two in-memory test authorities are used: the seq-17
predecessor (32 refs, capability absent) and an extension with the dormant
profile appended; never the publication key or the production registry.
Every Home Assistant object is run-unique, created here and removed with an
absence readback. Raw harness reads and controls never pass through Engineering.
"""
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
import aiohttp
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from alarmo_inspection_contract_acceptance import close_disposable_authority
from prepare_core_release_registry import prepare_candidate, sign_candidate
from ha_mcp_engineering.clients import HomeAssistantRestClient, HomeAssistantWebSocketClient
from ha_mcp_engineering.f3_runtime import dependency_bound as db
from ha_mcp_engineering.governance import entity_rename as er
from ha_mcp_engineering.governance import entity_rename_rewrites as rw
from ha_mcp_engineering.governance.resources import ConfigurationMutationNotDispatchedError
from ha_mcp_engineering.ha_core_readmission import CoreRuntime
from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
from ha_mcp_engineering.ha_core_readmission.routes import ENTITY_RENAME_RESOURCE_TYPE, f3_requirements
from ha_mcp_engineering.request_context import begin_request, end_request, observe_provider_send
from ha_mcp_engineering.signed_registry import canonical_json

import lane_failure_diagnostics as diagnostics

CAPABILITY = "core.entity_registry_rename_operation"
F3_VERIFICATION = "core.f3_mutation_verification"
REFERENCE_KEYS = ("capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint")
PREPARED = SimpleNamespace(capability_id=er.ENTITY_RENAME_CAPABILITY_IDENTITY,
                           operation=er.ENTITY_RENAME_CAPABILITY_IDENTITY)
CALLER = "hamcp295-disposable-admin"
READY_SECONDS = 60


def reference():
    profile, = (item for item in CORE_RUNTIME_CAPABILITY_PROFILES if item.capability_id == CAPABILITY)
    value = profile.to_mapping()
    return {key: value[key] for key in REFERENCE_KEYS}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# The one harness REST route: this run's own disposable automation.
RAW_ROUTE = re.compile(r"/config/automation/config/hamcp295_[0-9a-f]{8}")


class RawCore:
    """Harness-side authenticated WebSocket and REST; one command per connection."""

    def __init__(self, configured):
        self.url, self.token = configured.websocket_url, configured.ha_token
        self.base = configured.ha_url.rstrip("/")
        if urlsplit(self.base).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Entity rename lane routes go only to a loopback Core")

    async def call(self, payload):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60), trust_env=False) as session:
            async with session.ws_connect(self.url) as ws:
                assert (await ws.receive_json())["type"] == "auth_required"
                await ws.send_json({"type": "auth", "access_token": self.token})
                assert (await ws.receive_json())["type"] == "auth_ok"
                await ws.send_json({"id": 1, **payload})
                while True:
                    reply = await ws.receive_json()
                    if reply.get("id") == 1 and reply.get("type") == "result":
                        return reply

    async def ok(self, payload):
        reply = await self.call(payload)
        assert reply["success"] is True, (payload.get("type"), (reply.get("error") or {}).get("code"))
        return reply["result"]

    async def error_code(self, payload):
        reply = await self.call(payload)
        return None if reply["success"] is True else (reply.get("error") or {}).get("code")

    async def entry(self, entity_id):
        reply = await self.call({"type": er.ENTITY_RENAME_READ_COMMAND, "entity_id": entity_id})
        if reply["success"] is True:
            return reply["result"]
        assert reply["error"]["code"] == "not_found"
        return None

    async def rest(self, method, path, body=None):
        if method not in {"GET", "POST", "DELETE"} or RAW_ROUTE.fullmatch(path) is None:
            raise ValueError("Not an entity rename lane route")
        headers = {"Authorization": "Bearer " + self.token}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60), trust_env=False) as session:
            async with session.request(method, self.base + "/api" + path, json=body, headers=headers,
                                       allow_redirects=False) as reply:
                text = await reply.text()
                return reply.status, (json.loads(text) if text and reply.content_type == "application/json" else None)


class ObservedWebSocket:
    """Engineering's real client; counts registry updates actually sent."""

    def __init__(self, inner):
        self.inner, self.types, self.updates_sent = inner, [], 0

    async def command(self, payload):
        self.types.append(payload.get("type"))
        if payload.get("type") == er.ENTITY_RENAME_COMMAND:
            self.updates_sent += 1
        return await self.inner.command(payload)

    def __getattr__(self, name):
        return getattr(self.inner, name)


@diagnostics.checked("helper_setup", "helper_contract")
async def run(configured, entry, directory):
    """Run only the rename contract; caller owns startup, identity and teardown proof."""
    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    evidence = b"Disposable entity-rename test authority; not registry publication authority"
    old_refs = entry["capabilities"]
    old_ids = {p["capability_id"] for p in old_refs}
    assert len(old_refs) == 32 and CAPABILITY not in old_ids and F3_VERIFICATION in old_ids
    runtimes, raw = [], RawCore(configured)
    tag = secrets.token_hex(4)
    A, B = f"input_boolean.hamcp295_a_{tag}", f"input_boolean.hamcp295_b_{tag}"
    NEW, SECOND = f"input_boolean.hamcp295_renamed_{tag}", f"input_boolean.hamcp295_second_{tag}"
    AUTOMATION = f"hamcp295_{tag}"
    owned = {"helpers": [], "automation": False}

    @diagnostics.checked("helper_setup", "authority_setup")
    async def authority(refs, name):
        test_entry = {**entry, "capabilities": refs, "evidence_sha256": hashlib.sha256(evidence).hexdigest()}
        candidate = canonical_json(prepare_candidate(entry=test_entry, evidence=evidence, previous=None,
                                                      public_key=key.public_key(), now=now))
        payload = sign_candidate(candidate, expected_sha256=hashlib.sha256(candidate).hexdigest(),
                                 previous=None, key=key, now=now)

        async def fetch(_url, _maximum):
            return payload
        registry = CoreReleaseRegistry(enabled=True, public_key=base64.b64encode(
            key.public_key().public_bytes_raw()).decode(), fetcher=fetch, cache_path=directory / (name + ".json"))
        assert await registry.refresh()
        runtime = CoreRuntime()
        runtimes.append(runtime)
        runtime.configure(configured, release_registry=registry)
        await runtime.reconcile_once("startup")
        assert runtime.current_observation.version == entry["version"]
        assert runtime.health_snapshot()["compatible_count"] == len(refs)
        return runtime

    ws = ObservedWebSocket(HomeAssistantWebSocketClient(configured))
    gateway = er.EntityRegistryRenameGateway(HomeAssistantRestClient(configured), ws)

    async def engineering(name, call):
        telemetry, token = begin_request("hamcp295-" + name)
        telemetry.caller_id = CALLER
        try:
            return await call()
        finally:
            end_request(token)

    async def observe(old, new):
        """Engineering's own reviewed reads: both registry ids and the destination state."""
        async def reads():
            return (await gateway.read_entry(old), await gateway.read_entry(new),
                    await gateway.state_exists(new))
        return await engineering("read", reads)

    def target(old, new):
        return {"target_type": ENTITY_RENAME_RESOURCE_TYPE, "target_id": old + "->" + new}

    async def dispatch(runtime, command):
        """Exact-target lease, consumed only at the gateway's send boundary."""
        lease = runtime.acquire(f3_requirements(PREPARED),
                                target=target(command["entity_id"], command["new_entity_id"]))
        assert lease is not None
        commits = []

        def sent():
            assert not commits, "at most one send per lease"
            value = runtime.consume(lease)
            assert value is not None
            commits.append(value)
        try:
            with observe_provider_send(sent):
                return await engineering("rename", lambda: gateway.rename(command))
        finally:
            if commits:
                assert runtime.finish(commits[0])
            else:
                runtime.release(lease)

    async def wait_entry(entity_id):
        for _ in range(READY_SECONDS * 2):
            value = await raw.entry(entity_id)
            if value is not None:
                return value
            await asyncio.sleep(0.5)
        raise RuntimeError("disposable helper registry entry did not appear")

    async def automation_config():
        status, body = await raw.rest("GET", f"/config/automation/config/{AUTOMATION}")
        assert status == 200 and isinstance(body, dict)
        return body

    results = {}
    try:
        with diagnostics.check("operation", "negative_controls"):
            # Predecessor seq-17 shape: no route and no lease, before any Home Assistant write.
            before = await authority(old_refs, "predecessor")
            assert not before.route_status((CAPABILITY,))["available"]
            assert before.acquire((CAPABILITY,), target=target(A, NEW)) is None
            assert before.acquire(f3_requirements(PREPARED), target=target(A, NEW)) is None
        after = await authority(old_refs + [reference()], "extended")
        with diagnostics.check("operation", "negative_controls"):
            assert after.route_status((CAPABILITY,))["available"]
            # mutation_capable class: a lease without an exact target is refused.
            assert after.acquire((CAPABILITY,)) is None
            assert f3_requirements(PREPARED) == tuple(sorted((CAPABILITY, F3_VERIFICATION)))
            lease = after.acquire(f3_requirements(PREPARED), target=target(A, NEW))
            assert lease is not None and after.release(lease)

        with diagnostics.check("helper_setup", "helper_contract"):
            for name in ("a", "b"):
                created = await raw.ok({"type": "input_boolean/create", "name": f"hamcp295 {name} {tag}"})
                owned["helpers"].append(created["id"])
            assert [f"input_boolean.{item}" for item in owned["helpers"]] == [A, B]
            entry_a, entry_b = await wait_entry(A), await wait_entry(B)
            config = {"alias": f"HAMCP295 lane {tag}", "description": "",
                      "triggers": [{"trigger": "state", "entity_id": A}],
                      "conditions": [{"condition": "template", "value_template": "{{ is_state('" + A + "', 'on') }}"}],
                      "actions": [{"action": "input_boolean.turn_off", "target": {"entity_id": A}}],
                      "mode": "single"}
            status, _ = await raw.rest("POST", f"/config/automation/config/{AUTOMATION}", config)
            assert status == 200
            owned["automation"] = True
            stored = await automation_config()

        with diagnostics.check("operation", "operation_dispatch"):
            # Planning: Engineering reads, the B1-B6 rewrite plan, and the binding.
            old_entry, new_entry, new_in_states = await observe(A, NEW)
            assert old_entry is not None and new_entry is None and new_in_states is False
            plan = rw.plan_rewrites(old=A, new=NEW, holders=[
                {"holder_type": "automation", "holder_id": AUTOMATION, "config": stored}])
            step, = plan.steps
            assert step.rewritten_paths == ("$.actions[0].target.entity_id", "$.triggers[0].entity_id")
            assert [(i["position"], i["reason"]) for i in step.not_rewritten] == [
                ("$.conditions[0].value_template", rw.TEMPLATE_NOT_REWRITTEN)]
            assert plan.not_rewritten_count == 1  # plan-level elevated ack required (policy unit tests)
            impact = digest({"steps": [[s.holder_type, s.holder_id, list(s.rewritten_paths)] for s in plan.steps],
                             "not_rewritten": plan.not_rewritten_references})
            binding = er.planning_binding(A, NEW, old_entry, new_registered=False,
                                          new_in_states=False, impact_hash=impact)
            # An occupied destination refuses at planning (registry and states).
            try:
                er.planning_binding(A, B, old_entry, new_registered=True, new_in_states=True, impact_hash=impact)
            except er.EntityRenameRequestError as exc:
                assert exc.code == er.REFUSAL_DESTINATION_OCCUPIED
            else:
                raise AssertionError("occupied destination must refuse")
            # Closed payload: an extra field or a cross-domain id is never sent.
            sent = ws.updates_sent
            try:
                await engineering("closed", lambda: gateway.rename({**er.rename_command(A, NEW), "name": "x"}))
            except ConfigurationMutationNotDispatchedError as exc:
                assert exc.call_not_sent is True and exc.details["reason"] == er.REFUSAL_CLOSED_PAYLOAD
            else:
                raise AssertionError("closed payload must refuse")
            try:
                er.rename_command(A, f"switch.hamcp295_x_{tag}")
            except ValueError:
                pass
            else:
                raise AssertionError("cross-domain rename must refuse")
            assert ws.updates_sent == sent == 0
            cross = await raw.error_code({"type": er.ENTITY_RENAME_COMMAND, "entity_id": A,
                                          "new_entity_id": f"switch.hamcp295_x_{tag}"})
            assert cross == "invalid_info"
            # Stale pre-image: a raw change to the bound entry refuses at final preflight.
            await raw.ok({"type": er.ENTITY_RENAME_COMMAND, "entity_id": A, "name": f"stale {tag}"})
            old_entry, new_entry, new_in_states = await observe(A, NEW)
            stale = er.classify_preflight(binding, old_entry=old_entry, new_entry=new_entry,
                                          new_in_states=new_in_states)
            assert stale == er.REFUSAL_SOURCE_CHANGED and ws.updates_sent == 0
            await raw.ok({"type": er.ENTITY_RENAME_COMMAND, "entity_id": A, "name": None})
            old_entry, new_entry, new_in_states = await observe(A, NEW)
            assert er.classify_preflight(binding, old_entry=old_entry, new_entry=new_entry,
                                         new_in_states=new_in_states) == er.PREFLIGHT_DISPATCH
            # Forward rename: exactly one closed command under the exact-target lease.
            forward = await dispatch(after, er.rename_command(A, NEW))
            assert forward["entity_entry"]["entity_id"] == NEW and ws.updates_sent == 1

        with diagnostics.check("readback", "state_readback"):
            old_entry, new_entry = await raw.entry(A), await raw.entry(NEW)
            applied, reasons = er.verify_readback(binding, old_entry=old_entry, new_entry=new_entry)
            assert applied, reasons
            states = er.state_observation(await engineering("state", lambda: gateway.state_exists(A)),
                                          await engineering("state", lambda: gateway.state_exists(NEW)))
            # V9 case 2: re-entry finds the exact planned result and never re-sends.
            old_entry, new_entry, new_in_states = await observe(A, NEW)
            assert er.classify_preflight(binding, old_entry=old_entry, new_entry=new_entry,
                                         new_in_states=new_in_states) == er.PREFLIGHT_ALREADY_PRESENT
            # Core does not rewrite dependent configuration.
            unchanged = await automation_config()
            assert unchanged == stored and NEW not in json.dumps(unchanged)

        with diagnostics.check("readback", "value_assertions"):
            # Structured rewrite only; the template occurrence stays (and is disclosed).
            status, _ = await raw.rest("POST", f"/config/automation/config/{AUTOMATION}", step.proposed_config)
            assert status == 200
            rewritten = await automation_config()
            assert rewritten == step.proposed_config
            assert rewritten["triggers"][0]["entity_id"] == NEW
            assert rewritten["actions"][0]["target"]["entity_id"] == NEW
            assert rewritten["conditions"] == stored["conditions"]

        with diagnostics.check("recovery", "recovery"):
            # Dependency-bound compensation: rename NEW -> SECOND owned and verified,
            # then the dependent rewrite is refused by Core and is not applied.
            old_entry, new_entry, new_in_states = await observe(NEW, SECOND)
            plan2 = rw.plan_rewrites(old=NEW, new=SECOND, holders=[
                {"holder_type": "automation", "holder_id": AUTOMATION, "config": rewritten}])
            step2, = plan2.steps
            binding2 = er.planning_binding(NEW, SECOND, old_entry, new_registered=new_entry is not None,
                                           new_in_states=new_in_states, impact_hash=digest(
                                               {"steps": [list(step2.rewritten_paths)]}))
            assert er.classify_preflight(binding2, old_entry=old_entry, new_entry=new_entry,
                                         new_in_states=new_in_states) == er.PREFLIGHT_DISPATCH
            forward2 = await dispatch(after, er.rename_command(NEW, SECOND))
            applied, reasons = er.verify_readback(binding2, old_entry=await raw.entry(NEW),
                                                  new_entry=await raw.entry(SECOND))
            assert applied and forward2["entity_entry"]["entity_id"] == SECOND, reasons
            invalid = {**step2.proposed_config, "triggers": [{"trigger": "hamcp295_not_a_trigger"}]}
            status, _ = await raw.rest("POST", f"/config/automation/config/{AUTOMATION}", invalid)
            assert 400 <= status < 500
            assert await automation_config() == rewritten  # rewrite not applied, determinate
            classes = [
                db.ChildClass("rename", db.OWNED_VERIFIED, "dispatched_verified", "rename-child",
                              digest(forward2), True),
                db.ChildClass("rewrite-automation", db.NOT_APPLIED_DETERMINATE,
                              "dispatched_not_applied_verified", "rewrite-child", digest(invalid), True),
            ]
            assessment = db.assess_forward(classes)
            assert assessment.outcome == db.COMPENSATE
            plan_binding = db.PlanBinding("hamcp295-lane-" + tag, "0" * 64, "task-" + tag, "1" * 64)
            decision = db.build_compensation_decision(
                plan=plan_binding, sequence_hash=digest([c.operation_id for c in classes]),
                assessment=assessment, compensation_hashes={"rename": digest(er.compensation_command(binding2))},
                reason="dependent_rewrite_failed")
            inverse = er.compensation_binding(binding2)
            loop = asyncio.get_running_loop()
            persisted, recorded = [], {}

            def on_loop(coroutine):
                return asyncio.run_coroutine_threadsafe(coroutine, loop).result(timeout=120)

            def bundle_check(_entry):
                # Full-bundle state check: SECOND still holds the exact forward result.
                return er.classify_preflight(
                    inverse, old_entry=on_loop(raw.entry(SECOND)), new_entry=on_loop(raw.entry(NEW)),
                    new_in_states=on_loop(engineering("state", lambda: gateway.state_exists(NEW)))
                ) == er.PREFLIGHT_DISPATCH

            def run_child(item):
                on_loop(dispatch(after, er.compensation_command(binding2)))
                ok, _ = er.verify_readback(inverse, old_entry=on_loop(raw.entry(SECOND)),
                                           new_entry=on_loop(raw.entry(NEW)))
                child = db.ChildClass(item["operation_id"], db.OWNED_VERIFIED if ok else db.INDETERMINATE,
                                      "compensation_verified" if ok else "compensation_unproven",
                                      "compensation-child", None, True)
                recorded[item["operation_id"]] = child
                return child

            hooks = db.CompensationHooks(persisted.append, lambda: True, bundle_check, run_child,
                                         lambda item: recorded.get(item["operation_id"]))
            sends = ws.updates_sent
            result = await asyncio.to_thread(db.run_compensation, decision, hooks)
            assert result.outcome == db.OUTCOME_COMPENSATED and result.child_runs == 1
            assert ws.updates_sent == sends + 1 and persisted == [decision]
            # Re-entry with the persisted decision never re-sends a recorded child.
            again = await asyncio.to_thread(db.run_compensation, decision, hooks, persisted=persisted[0])
            assert again.outcome == db.OUTCOME_COMPENSATED and again.child_runs == 0
            assert ws.updates_sent == sends + 1
            restored, reasons = er.verify_readback(binding, old_entry=await raw.entry(A),
                                                   new_entry=await raw.entry(NEW))
            assert restored, reasons  # pre-image of the second rename is NEW's verified entry
            projection = db.terminal_projection(result.outcome, classes, compensated=result.compensated)

        with diagnostics.check("operation", "negative_controls"):
            # Destination taken at raw Core: the answered error stays uncertain and
            # readback proves nothing moved; never a retry or fallback.
            before_new, before_b = await raw.entry(NEW), await raw.entry(B)
            sends = ws.updates_sent
            try:
                await dispatch(after, er.rename_command(NEW, B))
            except er.EntityRenameUpdateErrorResult as exc:
                assert exc.details["reason"] == er.ENTITY_RENAME_ERROR_RESULT_REASON
            else:
                raise AssertionError("occupied destination must be refused by Core")
            assert ws.updates_sent == sends + 1
            after_new, after_b = await raw.entry(NEW), await raw.entry(B)
            assert er.identity(after_new) == er.identity(before_new) and er.bound_entry(after_new) == er.bound_entry(before_new)
            assert er.identity(after_b) == er.identity(before_b) == er.identity(entry_b)
            health = after.health_snapshot()
            assert health["fallback_count"] == 0
            assert set(ws.types) <= {er.ENTITY_RENAME_READ_COMMAND, er.ENTITY_RENAME_COMMAND}
            results = {
                "result": "PASS", "group": "entity_registry_rename", "profile": reference(),
                "authority_transition": [32, 33], "predecessor_leases": 0,
                "untargeted_lease": "refused", "f3_requirements": list(f3_requirements(PREPARED)),
                "rewrite_plan": {"rewritten_paths": list(step.rewritten_paths),
                                 "not_rewritten": [i["reason"] for i in step.not_rewritten],
                                 "plan_not_rewritten_count": plan.not_rewritten_count},
                "binding_fingerprint": er.binding_fingerprint(binding),
                "forward_result_fingerprint": er.forward_result_fingerprint(binding),
                "preimage_fingerprint": er.preimage_fingerprint(binding),
                "state_observation": states,
                "core_rewrote_dependents": False,
                "compensation": {"outcome": result.outcome, "child_runs": result.child_runs,
                                 "reentry_child_runs": again.child_runs,
                                 "decision_hash": decision["decision_hash"], "terminal": projection},
                "refusals": {"planning_destination_occupied": er.REFUSAL_DESTINATION_OCCUPIED,
                             "closed_payload": er.REFUSAL_CLOSED_PAYLOAD, "cross_domain_core": cross,
                             "stale_preimage": stale,
                             "core_destination_occupied": er.ENTITY_RENAME_ERROR_RESULT_REASON},
                "engineering_registry_updates_sent": ws.updates_sent,
                "engineering_command_types": sorted(set(ws.types)),
                "lease_commits": health["counters"].get("lease_commits"),
                "fallback_count": 0, "production_authority": False}
        return results
    finally:
        with diagnostics.check("cleanup", "service_close"):
            try:
                if owned["automation"]:
                    status, _ = await raw.rest("DELETE", f"/config/automation/config/{AUTOMATION}")
                    assert status == 200
                    status, _ = await raw.rest("GET", f"/config/automation/config/{AUTOMATION}")
                    assert status == 404
                for helper in owned["helpers"]:
                    await raw.ok({"type": "input_boolean/delete", "input_boolean_id": helper})
                for entity_id in (A, B, NEW, SECOND):
                    assert await raw.entry(entity_id) is None
            finally:
                for runtime in runtimes:
                    await close_disposable_authority(runtime)
