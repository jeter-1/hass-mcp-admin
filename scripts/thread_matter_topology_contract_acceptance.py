"""HAMCP-231 thread_matter_topology disposable acceptance; never a production entry point.

Called only by core_thread_matter_topology_local after it owns and verifies the
Core container. The real Engineering ThreadMatterTopologyService, TopologyReader
and CoreRuntime are used against exact Core 2026.11.0; no ha-mcp container and
no Matter Server. The two signatures below use a fresh in-memory test key, never
publication keys.

The harness's own raw commands never pass through Engineering. They seed two
synthetic Thread datasets into the disposable store (``thread/add_dataset_tlv``),
take an independent ``thread/list_datasets`` baseline, and confirm that Core
answers ``matter/network_topology`` with ``unknown_command`` while Matter is not
set up. Each dataset's network key and PSKc are random bytes made at run time;
they are canaries, not credentials, and they never leave memory.

Scope: A1 (``thread/list_datasets``) success and the A2 fail-closed case
(``matter`` not set up -> ``matter_not_loaded``). The A2 success lane needs a
Matter Server stub (HAMCP-297) and is not run here, so A2 signing evidence is
incomplete until it runs.
"""
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
import secrets
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
import aiohttp
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from alarmo_inspection_contract_acceptance import close_disposable_authority
from prepare_core_release_registry import prepare_candidate, sign_candidate
from ha_mcp_engineering.ha_core_readmission import CoreRuntime
from ha_mcp_engineering.ha_core_readmission.profiles import CORE_THREAD_MATTER_TOPOLOGY_PROFILES
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
from ha_mcp_engineering.thread_matter_topology import (
    CORE_REQUIREMENTS, DATASETS, LIST_DATASETS_COMMAND, NETWORK_TOPOLOGY_COMMAND, NEVER_SENT_COMMANDS,
    PAYLOADS, TOPOLOGY, ThreadMatterTopologyService, TopologyError, TopologyReader, validate_arguments)
from ha_mcp_engineering.request_context import begin_request, end_request
from ha_mcp_engineering.signed_registry import canonical_json

import lane_failure_diagnostics as diagnostics

CAPABILITY = "core.thread_matter_topology_read"
REFERENCE_KEYS = ("capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint")
CALLER = "hamcp231-disposable-admin"
HARNESS_FRAME_BYTES = 1024 * 1024
THREAD_READY_SECONDS = 300
ADD_DATASET_COMMAND = "thread/add_dataset_tlv"
CLEAN_SOURCE = "hamcp231-lane"
INVALID_ARGUMENTS = ({"refresh": False}, {"refresh": True}, {"sections": []}, {"sections": ["otbr"]},
                     {"sections": [TOPOLOGY, TOPOLOGY]}, {"device_id": "synthetic"}, {"sections": "datasets"})
ITEM_KEYS = frozenset({"dataset_id", "extended_pan_id", "pan_id", "channel", "preferred", "created",
                       "preferred_border_agent_ref", "preferred_extended_address_ref", "network_name", "source"})


def reference():
    profile, = (item for item in CORE_THREAD_MATTER_TOPOLOGY_PROFILES if item.capability_id == CAPABILITY)
    value = profile.to_mapping()
    return {key: value[key] for key in REFERENCE_KEYS}


def tlv(tag, data):
    return struct.pack("!BB", tag, len(data)) + data


def synthetic_dataset(name, channel):
    """A complete operational dataset with random identity and canary credentials."""
    ext_pan_id, network_key, pskc = secrets.token_bytes(8), secrets.token_bytes(16), secrets.token_bytes(16)
    pan_id = secrets.randbelow(0xFFFE).to_bytes(2, "big")  # never 0xFFFF (broadcast)
    encoded = b"".join((
        tlv(0, b"\x00" + channel.to_bytes(2, "big")),  # channel page 0
        tlv(1, pan_id),
        tlv(2, ext_pan_id),
        tlv(3, name.encode()),
        tlv(4, pskc),
        tlv(5, network_key),
        tlv(7, b"\xfd" + secrets.token_bytes(7)),  # mesh-local prefix
        tlv(14, struct.pack("!Q", 1 << 16)),  # active timestamp: 1 s, not authoritative
    ))
    return {"name": name, "channel": channel, "pan_id": pan_id.hex(), "extended_pan_id": ext_pan_id.hex(),
            "tlv": encoded.hex(), "canaries": (network_key.hex(), pskc.hex(), encoded.hex())}


def leaks(value, canaries):
    text = json.dumps(value, ensure_ascii=False)
    return [index for index, canary in enumerate(canaries)
            if canary and (canary.lower() in text.lower() or canary.upper() in text.upper())]


class ObservedReader(TopologyReader):
    """The real reader; records only what it admitted and wrote."""

    def __init__(self, settings):
        super().__init__(settings)
        self.reads, self.admitted, self.sent = 0, [], []

    async def read(self, *, sections, core_version, authorize, on_dispatch=lambda _c: None,
                   on_sent=lambda _c: None):
        self.reads += 1

        def dispatch(command):
            on_dispatch(command)
            self.admitted.append(command)

        def sent(command):
            on_sent(command)
            self.sent.append(command)
        return await super().read(sections=sections, core_version=core_version, authorize=authorize,
                                  on_dispatch=dispatch, on_sent=sent)


class RawCore:
    """Harness-side authenticated WebSocket; one command per connection."""

    def __init__(self, configured):
        self.url, self.token = configured.websocket_url, configured.ha_token

    async def command(self, payload):
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            async with session.ws_connect(self.url, max_msg_size=HARNESS_FRAME_BYTES) as ws:
                assert (await ws.receive_json())["type"] == "auth_required"
                await ws.send_json({"type": "auth", "access_token": self.token})
                assert (await ws.receive_json())["type"] == "auth_ok"
                await ws.send_json({"id": 1, **payload})
                reply = await ws.receive_json()
                assert reply.get("type") == "result" and reply.get("id") == 1
                return reply


@diagnostics.checked("helper_setup", "helper_contract")
async def run(configured, entry, directory):
    """Run only thread_matter_topology; caller owns startup, identity and teardown proof."""
    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    evidence = b"Disposable thread-matter-topology test authority; not registry publication authority"
    old = entry["capabilities"]
    assert len(old) == 32 and CAPABILITY not in {p["capability_id"] for p in old}
    runtimes = []
    raw = RawCore(configured)
    clean = synthetic_dataset("HAMCP-231-A", 15)
    marked = synthetic_dataset("HAMCP-231-B", 25)
    # The second dataset's free-text source carries its own network key: a
    # secret-shaped value in a kept text field must be withheld, never echoed.
    marked_source = "hamcp231 " + marked["canaries"][0]
    canaries = (*clean["canaries"], *marked["canaries"], configured.ha_token, configured.access_secret)

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
        return runtime

    def compose(runtime):
        # The production composition (ThreadMatterTopologyRuntime.configure).
        reader = ObservedReader(configured)
        return ThreadMatterTopologyService(reader, runtime, known_secrets=(
            configured.access_secret, configured.ha_token)), reader

    async def routed(runtime, service, arguments, caller=CALLER):
        """Mirror the MCP route: Core acquisition before the application.

        The tool is not registered yet (pending wiring), so the route's static
        requirement is the module's own CORE_REQUIREMENTS, with no target.
        """
        telemetry, token = begin_request("hamcp231-thread-matter-route")
        telemetry.caller_id = caller
        commits = None
        lease = None
        try:
            lease = runtime.acquire(CORE_REQUIREMENTS)
            if lease is None:
                return None

            def authorize():
                nonlocal commits
                if commits is not None:
                    return runtime.revalidate(lease, commits)
                commits = runtime.consume(lease)
                return commits is not None
            telemetry.core_dispatch_authorizer = authorize
            return await service.topology(validate_arguments(dict(arguments)))
        finally:
            if lease is not None:
                telemetry.core_dispatch_authorizer = lambda: False
                if commits is not None:
                    assert runtime.finish(commits)
                else:
                    runtime.release(lease)
            end_request(token)

    async def refused(call):
        try:
            await call
        except TopologyError as exc:
            return exc
        raise AssertionError("expected a Thread/Matter topology refusal")

    @diagnostics.checked("core_boot", "runtime_ready")
    async def thread_ready():
        # thread registers its commands in async_setup; wait for that bounded.
        for _ in range(THREAD_READY_SECONDS // 5):
            reply = await raw.command({"type": LIST_DATASETS_COMMAND})
            if reply.get("success") is True:
                assert reply["result"] == {"datasets": []}, "Disposable Thread store was not fresh"
                return
            assert reply["error"]["code"] == "unknown_command"
            await asyncio.sleep(5)
        raise RuntimeError("Disposable Thread integration did not register its commands")

    @diagnostics.checked("helper_setup", "provider_setup")
    async def seed():
        for dataset, source in ((clean, CLEAN_SOURCE), (marked, marked_source)):
            reply = await raw.command({"type": ADD_DATASET_COMMAND, "source": source, "tlv": dataset["tlv"]})
            assert reply["success"] is True and reply["result"] == {"result": "stored"}

    @diagnostics.checked("readback", "state_readback")
    async def raw_datasets():
        reply = await raw.command({"type": LIST_DATASETS_COMMAND})
        assert reply["success"] is True and type(reply["result"].get("datasets")) is list
        return reply["result"]["datasets"]

    def check_datasets(section, baseline):
        assert section["status"] == "ok" and section["total"] == len(baseline) == 2
        assert section["truncated"] is False and section["upstream_activity"] == ["cached_graph"]
        assert all(set(item) == ITEM_KEYS for item in section["items"])
        assert [item["dataset_id"] for item in section["items"]] == [r["dataset_id"] for r in baseline]
        by_name = {}
        for item, record in zip(section["items"], baseline):
            # PAN IDs, channel and network name are kept raw by design (section 5.1.2).
            assert item["extended_pan_id"] == record["extended_pan_id"].lower()
            assert item["pan_id"] == record["pan_id"].lower() and item["channel"] == record["channel"]
            assert item["preferred"] is record["preferred"]
            assert item["preferred_border_agent_ref"] is None and item["preferred_extended_address_ref"] is None
            by_name[record["network_name"]] = (item, record)
        for dataset in (clean, marked):
            item, record = by_name[dataset["name"]]
            assert item["network_name"] == dataset["name"]
            assert (item["extended_pan_id"], item["pan_id"], item["channel"]) == (
                dataset["extended_pan_id"], dataset["pan_id"], dataset["channel"])
        assert by_name[clean["name"]][0]["source"] == CLEAN_SOURCE
        assert by_name[marked["name"]][1]["source"] == marked_source  # the raw store does hold it
        assert by_name[marked["name"]][0]["source"] is None  # withheld in the projection

    def check_topology(section):
        assert section == {"status": "matter_not_loaded", "upstream_activity": ["cached_graph",
                                                                                 "may_start_br_discovery"]}

    try:
        await thread_ready()
        await seed()
        baseline = await raw_datasets()
        with diagnostics.check("readback", "state_readback"):
            # Matter is not set up: Core itself answers unknown_command.
            matter = await raw.command(dict(PAYLOADS[TOPOLOGY]))
            assert matter["success"] is False and matter["error"]["code"] == "unknown_command"
        assert dict(PAYLOADS[TOPOLOGY]) == {"type": NETWORK_TOPOLOGY_COMMAND, "refresh": False}
        assert dict(PAYLOADS[DATASETS]) == {"type": LIST_DATASETS_COMMAND}

        with diagnostics.check("operation", "negative_controls"):
            # Predecessor seq-17 shape (32 refs): the route refuses before the
            # application, and the service itself refuses with zero reads.
            before = await authority(old, "predecessor")
            assert not before.route_status((CAPABILITY,))["available"]
            withheld, withheld_reader = compose(before)
            assert await routed(before, withheld, {}) is None
            telemetry, token = begin_request("hamcp231-thread-matter-direct")
            telemetry.caller_id = CALLER
            try:
                exc = await refused(withheld.topology(validate_arguments({})))
            finally:
                end_request(token)
            assert exc.reason == "authority_unavailable"
            assert withheld_reader.reads == 0 and withheld_reader.sent == []

        after = await authority(old + [reference()], "extended")
        assert after.route_status((CAPABILITY,))["available"]
        service, reader = compose(after)
        with diagnostics.check("operation", "operation_dispatch"):
            both = await routed(after, service, {})
            assert both is not None and both["sections"] == [DATASETS, TOPOLOGY]
            assert both["commands_sent"] == 2
            assert reader.reads == 1 and reader.admitted == reader.sent == [
                LIST_DATASETS_COMMAND, NETWORK_TOPOLOGY_COMMAND]
            only_datasets = await routed(after, service, {"sections": [DATASETS]})
            assert only_datasets["sections"] == [DATASETS] and only_datasets["commands_sent"] == 1
            only_topology = await routed(after, service, {"sections": [TOPOLOGY]})
            assert only_topology["sections"] == [TOPOLOGY] and only_topology["commands_sent"] == 1
            assert reader.sent == [LIST_DATASETS_COMMAND, NETWORK_TOPOLOGY_COMMAND,
                                   LIST_DATASETS_COMMAND, NETWORK_TOPOLOGY_COMMAND]
        with diagnostics.check("readback", "value_assertions"):
            later = await raw_datasets()
            assert later == baseline  # the reads changed nothing in the store
            for result in (both, only_datasets):
                check_datasets(result[DATASETS], baseline)
                assert result["withheld_identifier_field_count"] == 1
            for result in (both, only_topology):
                check_topology(result[TOPOLOGY])
            assert both["completeness"] == "partial"
            assert both["partial_reasons"] == ["section_unavailable", "sanitized_text"]
            assert only_datasets["partial_reasons"] == ["sanitized_text"]
            assert only_topology["partial_reasons"] == ["section_unavailable"]
            assert only_topology["withheld_identifier_field_count"] == 0
            for result in (both, only_datasets, only_topology):
                assert result["identifier_policy"] == "opaque_per_response_v1"
                assert result["sanitization_failed_closed"] is False
                assert leaks(result, canaries) == []
            assert not set(reader.sent) & set(NEVER_SENT_COMMANDS)
            assert ADD_DATASET_COMMAND not in reader.sent

        with diagnostics.check("operation", "negative_controls"):
            # Anonymous caller: refused before any read.
            count = reader.reads
            exc = await refused(routed(after, service, {}, caller="anonymous"))
            assert exc.reason == "authority_unavailable" and reader.reads == count
            # Wrong Core identity: refused at auth_ok before any command is admitted.
            direct = ObservedReader(configured)
            exc = await refused(direct.read(sections=(DATASETS, TOPOLOGY), core_version="2026.10.3",
                                            authorize=lambda: None))
            assert exc.reason == "identity_mismatch" and direct.admitted == direct.sent == []
            # Out-of-contract arguments (including any refresh) never reach Core.
            sent = len(reader.sent)
            for arguments in INVALID_ARGUMENTS:
                try:
                    validate_arguments(arguments)
                except TopologyError as exc:
                    assert exc.reason == "invalid_arguments"
                else:
                    raise AssertionError("invalid arguments must refuse")
            assert len(reader.sent) == sent
            assert after.health_snapshot()["fallback_count"] == 0
        return {"result": "PASS", "group": "thread_matter_topology", "profile": reference(),
                "authority_transition": [32, 33], "predecessor_reads": 0,
                "scope": {"a1_list_datasets": "success", "a2_network_topology": "matter_not_loaded_only",
                          "a2_success_lane": "not_run_pending_matter_server_stub"},
                "seeded_datasets": len(baseline),
                "topology": {"sections": both["sections"], "completeness": both["completeness"],
                             "partial_reasons": both["partial_reasons"],
                             "datasets_status": both[DATASETS]["status"],
                             "topology_status": both[TOPOLOGY]["status"],
                             "withheld_identifier_field_count": both["withheld_identifier_field_count"],
                             "commands_sent": both["commands_sent"]},
                "pan_ids_kept": True, "canary_leaks": 0, "canaries_checked": len(canaries),
                "raw_matter_reply": "unknown_command", "topology_refresh": False,
                "commands_sent": list(reader.sent),
                "refusals": {"predecessor": "authority_unavailable", "anonymous": "authority_unavailable",
                             "wrong_core_version": "identity_mismatch",
                             "invalid_arguments": len(INVALID_ARGUMENTS),
                             "matter_not_set_up": "matter_not_loaded"},
                "fallback_count": 0, "production_authority": False}
    finally:
        with diagnostics.check("cleanup", "service_close"):
            for runtime in runtimes:
                await close_disposable_authority(runtime)
