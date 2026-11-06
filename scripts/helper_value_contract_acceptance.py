"""HAMCP-315 helper-only disposable acceptance; never a production entry point.

Called only by core_helper_value_local after it owns and verifies both containers.
Real Engineering service/provider/Core probes and real ha-mcp transport are used.
The two signatures below use a fresh in-memory test key, never publication keys.
"""
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp.server.fastmcp import FastMCP
from alarmo_inspection_contract_acceptance import close_disposable_authority
from prepare_core_release_registry import prepare_candidate, sign_candidate
from ha_mcp_engineering.clients import HomeAssistantRestClient
from ha_mcp_engineering.clients.paths import rest_path
from ha_mcp_engineering.ha_core_readmission import CoreRuntime
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
from ha_mcp_engineering.ha_core_readmission.typed_operations import operation_profile
from ha_mcp_engineering.helper_value.contracts import HelperRequest, HelperRefusal, digest
from ha_mcp_engineering.helper_value.service import HelperService, HelperCoreAuthority
from ha_mcp_engineering.providers.upstream_helper_value import HelperProvider
from ha_mcp_engineering.request_context import begin_request, end_request
from ha_mcp_engineering.signed_registry import canonical_json
from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway
from ha_mcp_engineering.upstream_tool_policy import load_reviewed_upstream_release_registry

CAPABILITY = "core.typed_helper_value_operation"
REFERENCE_KEYS = ("capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint")
# Each allowed domain/service pair, plus timer resume/restart and clamping.
# The independent readback checks below do not call production desired().
CASES = (
    ("input_boolean", "turn_on", None, "on"),
    ("input_boolean", "turn_off", None, "off"),
    ("input_select", "select_option", "Away", "Away"),
    ("input_number", "set_value", 21.5, "21.5"),
    ("input_text", "set_value", "changed", "changed"),
    ("input_datetime", "set_datetime", "2026-11-05 12:34:56", "2026-11-05 12:34:56"),
    ("input_button", "press", None, "timestamp"),
    ("counter", "increment", None, "3"),
    ("counter", "decrement", None, "2"),
    ("counter", "set_value", 6, "6"),
    ("counter", "increment", None, "refused_at_limit"),
    ("counter", "reset", None, "2"),
    ("timer", "start", "00:05:00", "active"),
    ("timer", "start", "00:06:00", "active"),
    ("timer", "pause", None, "paused"),
    ("timer", "start", None, "active"),
    ("timer", "cancel", None, "idle"),
    ("timer", "start", "00:05:00", "active"),
    ("timer", "finish", None, "idle"),
)


def reference():
    value = operation_profile("helper_value").to_mapping()
    return {key: value[key] for key in REFERENCE_KEYS}


def helper_request(domain, action, value=None):
    return HelperRequest(entity_id=domain + ".hamcp_seq17", action=action, value=value,
        operation_id=f"{int(datetime.now(timezone.utc).timestamp())}-{uuid4().hex}")


def durable(receipt):
    # control() adds current-call safety disclosure; replay explicitly says it
    # used the stored receipt. All persisted execution facts must still match.
    return {key: value for key, value in receipt.items() if key != "safety"}


class ObservedProvider(HelperProvider):
    """Real calls; explicit lost-response/readback faults never invent success."""
    lose_response = False
    block_after_write = False
    hide_readback = False

    async def _call(self, tool, args, before, **kwargs):
        if tool == "ha_get_state" and self.hide_readback:
            raise TimeoutError("synthetic_readback_loss")
        if tool == "ha_call_service":
            original = before
            async def before():
                await original()
                self.writes.append(dict(args))
        result = await super()._call(tool, args, before, **kwargs)
        if tool == "ha_call_service":
            self.hide_readback = self.block_after_write
            if self.lose_response:
                raise TimeoutError("synthetic_response_loss_after_real_dispatch")
        return result


async def run(configured, entry, directory):
    """Run only helper_value; caller owns startup, identity and teardown proof."""
    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    evidence = b"Disposable helper-value test authority; not registry publication authority"
    old = entry["capabilities"]
    assert len(old) == 31 and CAPABILITY not in {p["capability_id"] for p in old}
    runtimes, services = [], []

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
        return runtime

    async def compose(runtime, name):
        gateway = UpstreamReadGateway()
        gateway.configure(configured, release_registry=load_reviewed_upstream_release_registry(),
                          core_runtime=runtime)
        await gateway.reconcile_until_initialized(FastMCP("seq17-disposable-" + name))
        provider = ObservedProvider.configured(configured, gateway)
        provider.writes = []
        service = HelperService(directory / name, provider, HelperCoreAuthority(runtime))
        services.append(service)
        return service, provider

    async def call(service, req):
        telemetry, token = begin_request("seq17-disposable-helper")
        telemetry.ordinary_helper_value_binding = digest(req.model_dump())
        try:
            receipt = await service.control(req)
            for _ in range(12):
                if receipt.get("terminal"):
                    break
                await asyncio.sleep(1)
                receipt = await service.reconcile(req.task_id)
            return receipt
        finally:
            end_request(token)

    raw = HomeAssistantRestClient(configured)
    async def state(entity):
        return await raw.request("GET", rest_path("/states/{}", entity, kinds=("entity_id",)))

    try:
        before = await authority(old, "predecessor")
        assert not before.route_status((CAPABILITY,))["available"]
        withheld, withheld_provider = await compose(before, "withheld")
        baseline = await state("input_boolean.hamcp_seq17")
        try:
            result = await call(withheld, helper_request("input_boolean", "turn_on"))
        except HelperRefusal as exc:
            assert exc.details["category"] == "helper_core_authority_unavailable"
        else:
            assert result["status"] in {"refused", "provider_unavailable"}
            assert result.get("dispatch_intent_recorded") is not True
        assert withheld_provider.writes == []
        assert (await state("input_boolean.hamcp_seq17"))["state"] == baseline["state"]

        after = await authority(old + [reference()], "extended")
        assert after.route_status((CAPABILITY,))["available"]
        service, provider = await compose(after, "admitted")
        outcomes = []
        for domain, action, value, expected in CASES:
            req = helper_request(domain, action, value)
            previous = await state(req.entity_id)
            count = len(provider.writes)
            if expected == "refused_at_limit":
                try:
                    await call(service, req)
                except HelperRefusal as exc:
                    assert exc.details["category"] == "helper_counter_at_limit"
                else:
                    raise AssertionError("counter limit must refuse")
                assert len(provider.writes) == count and (await state(req.entity_id))["state"] == previous["state"]
                outcomes.append({"domain": domain, "action": action, "status": "refused_at_limit", "attempts": 0})
                continue
            receipt = await call(service, req)
            assert receipt["status"] in {"succeeded_verified", "no_change"}
            delta = len(provider.writes) - count
            assert delta == (0 if receipt["status"] == "no_change" else 1)
            if delta:
                assert provider.writes[-1] == req.arguments()
                assert receipt["provider_attempt_count"] == 1
            duplicate = await call(service, req)
            assert durable(duplicate) == durable(receipt), (domain, action)
            assert len(provider.writes) == count + delta
            observed = await state(req.entity_id)
            if expected == "timestamp":
                assert observed["state"] != previous["state"]
                assert datetime.fromisoformat(observed["state"].replace("Z", "+00:00")).tzinfo is not None
            else:
                assert observed["state"] == expected
            if domain == "timer":
                attrs = observed["attributes"]
                if action == "start":
                    assert attrs.get("finishes_at") and attrs["finishes_at"] != previous["attributes"].get("finishes_at")
                if action in {"cancel", "finish"}:
                    assert attrs["last_transition"] == {"cancel": "cancelled", "finish": "finished"}[action]
            outcomes.append({"domain": domain, "action": action, "status": receipt["status"], "attempts": delta})

        # Lost acknowledgement: independently observed effect can settle, never replay.
        provider.lose_response = True
        req = helper_request("input_boolean", "turn_on")
        count = len(provider.writes)
        receipt = await call(service, req)
        assert receipt["status"] == "succeeded_verified" and not receipt["provider_response_received"]
        assert (await state(req.entity_id))["state"] == "on"
        assert durable(await call(service, req)) == durable(receipt) and len(provider.writes) == count + 1
        provider.lose_response = False

        # A real mutation with unavailable readback stays unverified. Reopen the
        # durable store and recover with real reads only, never another dispatch.
        provider.block_after_write = True
        req = helper_request("input_boolean", "turn_off")
        count = len(provider.writes)
        receipt = await call(service, req)
        assert receipt["status"] == "changed_unverified" and not receipt["terminal"]
        assert (await state(req.entity_id))["state"] == "off"
        await service.close()
        services.remove(service)
        provider.block_after_write = provider.hide_readback = False
        service = HelperService(directory / "admitted", provider, HelperCoreAuthority(after))
        services.append(service)
        # A reopened process must wait for the previous durable claim/lease;
        # never shorten that lease or rewrite the store to make the test pass.
        for _ in range(240):
            receipt = await service.reconcile(req.task_id)
            if receipt["terminal"]:
                break
            await asyncio.sleep(1)
        assert receipt["status"] == "succeeded_verified"
        assert durable(await call(service, req)) == durable(receipt) and len(provider.writes) == count + 1

        # Invalid live values and unavailable targets must preserve state without a write.
        for req in (helper_request("input_number", "set_value", 999),
                    helper_request("input_select", "select_option", "Missing"),
                    helper_request("input_text", "set_value", "TOO-LONG-AND-INVALID"),
                    helper_request("input_boolean", "turn_on").model_copy(update={"entity_id": "input_boolean.absent_seq17"})):
            count = len(provider.writes)
            try:
                result = await call(service, req)
            except HelperRefusal:
                pass
            else:
                assert result["status"] in {"refused", "provider_unavailable"}
            assert len(provider.writes) == count
        assert not service.locks.records() and service.health()["nonterminal_tasks"] == 0
        assert service.health()["fallback_count"] == after.health_snapshot()["fallback_count"] == 0
        return {"result": "PASS", "group": "helper_value", "profile": reference(),
                "authority_transition": [31, 32], "predecessor_writes": 0,
                "operations": outcomes, "invalid_requests": 4, "duplicate_writes": 0,
                "lost_response_verified_without_replay": True,
                "unverifiable_effect_not_success": True, "reopened_recovery_read_only": True,
                "fallback_count": 0, "production_authority": False}
    finally:
        for service in services:
            await service.close()
        for runtime in runtimes:
            await close_disposable_authority(runtime)
