"""HAMCP-231 A2 success-lane acceptance; never a production entry point.

Called only by core_thread_matter_topology_a2_local after it owns and verifies
the Core and Matter Server stub containers. The stub is the HAMCP-297 committed
test-only stub, consumed by path and SHA-256 (never copied or forked here); it
answers ``get_network_topology`` with a static matter-python-client 1.5.5
``NetworkTopology`` and records every command it receives.

The real ThreadMatterTopologyService, TopologyReader and CoreRuntime read
``matter/network_topology`` through the wired static route. The checks are:

- the stub-recorded ``get_network_topology`` args are exactly ``{"refresh": false}``,
  and Engineering's reads add only that command to the stub record;
- every node and edge id is a per-response ``g<k>`` reference, and unresolved
  (ghost) edges are dropped, never emitted with a raw endpoint;
- matter nodes keep ``node_id`` and resolve ``ha_device_id`` to the disposable
  device registry entry, and non-HA kinds carry no ``ha_device_id``;
- none of the parameterized stub canaries (ext_address, BSSID, SSID, host name,
  key-shaped text) appears in any result.

Canary literals and stub identity come from
``docs/evidence/hamcp231/lane/a2-stub-expectations.json``. Until HAMCP-297
fills it, the lane refuses ``a2_stub_pending`` and A2 signing evidence stays
incomplete.
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
import re

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from alarmo_inspection_contract_acceptance import close_disposable_authority
from prepare_core_release_registry import prepare_candidate, sign_candidate
from thread_matter_topology_contract_acceptance import (
    CALLER, CAPABILITY, ObservedReader, RawCore, leaks, reference)
from ha_mcp_engineering.ha_core_readmission import CoreRuntime
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
from ha_mcp_engineering.signed_registry import canonical_json
from ha_mcp_engineering.ha_core_readmission.routes import static_tool_requirements
from ha_mcp_engineering.thread_matter_topology import (
    NETWORK_TOPOLOGY_COMMAND, NEVER_SENT_COMMANDS, PAYLOADS, TOPOLOGY, NON_HA_KINDS,
    ThreadMatterTopologyService, validate_arguments)
from ha_mcp_engineering.request_context import begin_request, end_request

import lane_failure_diagnostics as diagnostics

TOOL = "get_thread_matter_topology"
STUB_TOPOLOGY_COMMAND = "get_network_topology"
EXPECTATIONS_MODEL = "hamcp231-a2-stub-expectations-v1"
GRAPH_REF = re.compile(r"g[1-9][0-9]*")
# Core 2026.11.0 components/matter/helpers.py:137-146 get_node_device_identifier.
NODE_DEVICE_IDENTIFIER = re.compile(r"deviceid_[0-9A-F]{16}-([0-9A-F]{16})-MatterNodeDevice")
TOPOLOGY_READS = 2  # the default read and the topology-only read


class ExpectationsPending(Exception):
    pass


def ready_expectations(spec):
    """The parameterized stub contract; refuses until HAMCP-297 fills it."""
    if spec.get("model") != EXPECTATIONS_MODEL:
        raise ExpectationsPending("a2_expectations_model_mismatch")
    stub = spec.get("stub") or {}
    if (spec.get("status") != "ready" or not stub.get("path") or not stub.get("sha256")
            or not stub.get("nodes_path") or not stub.get("nodes_sha256")):
        raise ExpectationsPending("a2_stub_pending")
    canaries = spec.get("canaries")
    if (type(canaries) is not list or len(canaries) < 5
            or any(type(c) is not str or len(c) < 6 for c in canaries)):
        raise ExpectationsPending("a2_canaries_pending")
    expected = spec["expected"]
    if not expected.get("matter_node_ids") or not expected.get("required_kinds"):
        raise ExpectationsPending("a2_expectations_incomplete")
    return spec


def stub_commands(lines):
    """Parse the stub's jsonl record into ordered command records."""
    records = []
    for line in lines:
        if not line.strip():
            continue
        value = json.loads(line)
        if value.get("kind") == "command":
            records.append({"command": value["command"], "args": value.get("args")})
    return records


def check_stub_record(records, *, mark, topology_reads, startup_commands):
    """Before ``mark`` only startup and the harness's own raw read; after it only Engineering's reads.

    Every recorded ``get_network_topology`` carries args that are exactly the
    literal ``{"refresh": False}``.
    """
    allowed_before = set(startup_commands) | {STUB_TOPOLOGY_COMMAND}
    assert all(record["command"] in allowed_before for record in records[:mark]), "unexpected startup command"
    tail = records[mark:]
    assert [record["command"] for record in tail] == [STUB_TOPOLOGY_COMMAND] * topology_reads
    topology = [record for record in records if record["command"] == STUB_TOPOLOGY_COMMAND]
    for record in topology:
        args = record["args"]
        assert type(args) is dict and set(args) == {"refresh"} and args["refresh"] is False
    return {"recorded_topology_commands": len(topology), "engineering_topology_commands": len(tail),
            "recorded_refresh": [record["args"]["refresh"] for record in topology]}


def registry_node_devices(devices):
    """Raw disposable device registry: Matter node_id -> HA device id."""
    mapping = {}
    for device in devices:
        for domain, identifier in device.get("identifiers") or ():
            match = NODE_DEVICE_IDENTIFIER.fullmatch(identifier) if domain == "matter" else None
            if match:
                node_id = int(match.group(1), 16)
                assert node_id not in mapping, "duplicate Matter node device"
                mapping[node_id] = device["id"]
    return mapping


def check_topology_success(section, *, raw, devices, expected):
    """The projected A2 section against the raw Core reply and the raw registry."""
    assert section["status"] == "ok" and section["truncated"] is False
    nodes, connections = section["nodes"], section["connections"]
    ids = [node["id"] for node in nodes]
    assert all(GRAPH_REF.fullmatch(value) for value in ids) and len(set(ids)) == len(ids)
    assert section["total"]["nodes"] == len(raw["nodes"]) == len(nodes)
    for edge in connections:
        assert edge["source"] in ids and edge["target"] in ids
    dropped = section["connections_dropped_unresolved"]
    assert dropped >= expected.get("min_dropped_edges", 0)
    assert len(connections) + dropped == section["total"]["connections"] == len(raw["connections"])
    assert set(expected["required_kinds"]) <= {node["kind"] for node in nodes}
    matter = {node["node_id"]: node for node in nodes if node["kind"] == "matter"}
    assert sorted(matter) == sorted(expected["matter_node_ids"])
    for node_id, node in matter.items():
        assert node["ha_device_id"] == devices[node_id] and node["identity"] == "ha_device"
    for node in nodes:
        if node["kind"] in NON_HA_KINDS:
            assert node["ha_device_id"] is None and node["identity"] == "not_an_ha_device"
        if node["kind"] == "wifi_ap":
            assert node["network_name"] is None and node["network_name_withheld"] is True
    # Kept network-level values match Core's own reply node for node.
    raw_by_node = {node.get("node_id"): node for node in raw["nodes"] if node.get("node_id") is not None}
    for node_id, node in matter.items():
        upstream = raw_by_node[node_id]
        assert node["rloc16"] == upstream.get("rloc16")
        assert node["network_type"] == upstream.get("network_type")
        assert node["ha_device_id"] == upstream.get("ha_device_id")
    return {"nodes": len(nodes), "connections": len(connections), "dropped_unresolved": dropped,
            "kinds": sorted({node["kind"] for node in nodes}), "matter_nodes": sorted(matter),
            "ha_device_ids_resolved": len(matter)}


@diagnostics.checked("helper_setup", "authority_setup")
async def disposable_authority(configured, entry, refs, path):
    """The A1 lane's construction: a fresh in-memory test key, never publication keys."""
    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    evidence = b"Disposable thread-matter-topology A2 test authority; not registry publication authority"
    test_entry = {**entry, "capabilities": refs, "evidence_sha256": hashlib.sha256(evidence).hexdigest()}
    candidate = canonical_json(prepare_candidate(entry=test_entry, evidence=evidence, previous=None,
                                                  public_key=key.public_key(), now=now))
    payload = sign_candidate(candidate, expected_sha256=hashlib.sha256(candidate).hexdigest(),
                             previous=None, key=key, now=now)

    async def fetch(_url, _maximum):
        return payload
    registry = CoreReleaseRegistry(enabled=True, public_key=base64.b64encode(
        key.public_key().public_bytes_raw()).decode(), fetcher=fetch, cache_path=path)
    assert await registry.refresh()
    runtime = CoreRuntime()
    runtime.configure(configured, release_registry=registry)
    await runtime.reconcile_once("startup")
    assert runtime.current_observation.version == entry["version"]
    return runtime


@diagnostics.checked("helper_setup", "helper_contract")
async def run(configured, entry, directory, *, expectations, read_stub_record, read_devices):
    """Run the A2 success checks; the caller owns startup, identity and teardown proof.

    ``read_stub_record()`` returns the stub's jsonl lines; ``read_devices()``
    the raw disposable device registry (harness-side, never via Engineering).
    """
    old = entry["capabilities"]
    assert len(old) == 32 and CAPABILITY not in {p["capability_id"] for p in old}
    expected = expectations["expected"]
    canaries = (*expectations["canaries"], configured.ha_token, configured.access_secret)
    raw = RawCore(configured)
    assert dict(PAYLOADS[TOPOLOGY]) == {"type": NETWORK_TOPOLOGY_COMMAND, "refresh": False}
    assert static_tool_requirements(TOOL, {}) == (CAPABILITY,)

    with diagnostics.check("readback", "state_readback"):
        reply = await raw.command(dict(PAYLOADS[TOPOLOGY]))
        assert reply["success"] is True and type(reply["result"]) is dict
        upstream = reply["result"]
        devices = registry_node_devices(await read_devices())
        mark = len(stub_commands(read_stub_record()))

    runtime = await disposable_authority(configured, entry, old + [reference()], directory / "a2-extended.json")
    try:
        return await _exercise(configured, runtime, upstream, devices, mark, canaries, expected,
                               expectations, read_stub_record)
    finally:
        with diagnostics.check("cleanup", "service_close"):
            await close_disposable_authority(runtime)


async def _exercise(configured, runtime, upstream, devices, mark, canaries, expected, expectations,
                    read_stub_record):
    assert runtime.route_status((CAPABILITY,))["available"]
    reader = ObservedReader(configured)
    service = ThreadMatterTopologyService(reader, runtime, known_secrets=(
        configured.access_secret, configured.ha_token))

    async def routed(arguments):
        telemetry, token = begin_request("hamcp231-a2-route")
        telemetry.caller_id = CALLER
        commits = lease = None
        try:
            lease = runtime.acquire(static_tool_requirements(TOOL, {}))
            assert lease is not None

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

    with diagnostics.check("operation", "operation_dispatch"):
        both = await routed({})
        only_topology = await routed({"sections": [TOPOLOGY]})
        assert only_topology["sections"] == [TOPOLOGY] and only_topology["commands_sent"] == 1
    with diagnostics.check("readback", "value_assertions"):
        summary = check_topology_success(both[TOPOLOGY], raw=upstream, devices=devices, expected=expected)
        assert check_topology_success(only_topology[TOPOLOGY], raw=upstream, devices=devices,
                                      expected=expected) == summary
        stub = check_stub_record(stub_commands(read_stub_record()), mark=mark,
                                 topology_reads=TOPOLOGY_READS,
                                 startup_commands=expected["startup_commands"])
        for result in (both, only_topology):
            assert result["identifier_policy"] == "opaque_per_response_v1"
            assert result["sanitization_failed_closed"] is False
            assert leaks(result, canaries) == []
        assert NETWORK_TOPOLOGY_COMMAND in reader.sent
        assert not set(reader.sent) & set(NEVER_SENT_COMMANDS)
        assert runtime.health_snapshot()["fallback_count"] == 0
    return {"result": "PASS", "group": "thread_matter_topology", "scenario": "a2_success",
            "profile": reference(), "topology": summary, "stub_record": stub,
            "topology_refresh": False, "commands_sent": list(reader.sent),
            "canaries_checked": len(canaries), "canary_leaks": 0,
            "stub": {key: expectations["stub"][key] for key in ("path", "sha256", "nodes_path", "nodes_sha256")},
            "fallback_count": 0, "production_authority": False}
