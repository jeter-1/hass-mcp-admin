"""HAMCP-231 A2 success-lane acceptance; never a production entry point.

Called only by core_thread_matter_topology_a2_local after it owns and verifies
the Core and Matter Server stub containers. The stub is the HAMCP-297 committed
test-only stub (merged by PR #428), consumed by path and per-file SHA-256 (never
copied or forked here); it answers ``get_network_topology`` with a static,
synthetic matter-python-client 1.5.5 ``NetworkTopology``, records every
command it receives, and takes faults on its control endpoint.

The real ThreadMatterTopologyService, TopologyReader and CoreRuntime read
``matter/network_topology`` through the wired static route. The checks are:

- every stub-recorded ``get_network_topology`` carries args that are exactly
  ``{"refresh": false}``, in the jsonl record and in the stub's own
  ``topology_requests``; Engineering adds no other command to the record;
- every node and edge id is a per-response ``g<k>`` reference; the ghost edge
  (``thread_FFFF00000000DEAD``, no such node) is dropped and counted, never
  emitted with a raw endpoint;
- matter nodes keep ``node_id`` and resolve ``ha_device_id`` to the disposable
  device registry entry; non-HA kinds carry no ``ha_device_id``;
- the kept-raw network values (Thread ``network_name``, ``ext_pan_id``,
  ``rloc16``) pass unchanged, Wi-Fi names are withheld, and none of the
  parameterized canaries (extended addresses, graph ids, BSSID, SSID, host
  name, the 32-hex network-key-shaped vendor text, upstream error text)
  appears in any result;
- ``topology_error`` (Matter Server error_code 7) refuses the section as
  ``upstream_command_failed`` without reflecting upstream text;
- a Matter Server reporting schema 12 makes Core answer ``not_supported``
  before it calls the server, which the section reports as
  ``matter_server_schema_below_13``.

Canary literals and stub identity come from
``docs/evidence/hamcp231/lane/a2-stub-expectations.json``.
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
    ThreadMatterTopologyService, normalize_identifier, validate_arguments)
from ha_mcp_engineering.request_context import begin_request, end_request

import lane_failure_diagnostics as diagnostics

TOOL = "get_thread_matter_topology"
STUB_TOPOLOGY_COMMAND = "get_network_topology"
EXPECTATIONS_MODEL = "hamcp231-a2-stub-expectations-v2"
GRAPH_REF = re.compile(r"g[1-9][0-9]*")
# Core 2026.11.0 components/matter/helpers.py:137-146 get_node_device_identifier.
NODE_DEVICE_IDENTIFIER = re.compile(r"deviceid_[0-9A-F]{16}-([0-9A-F]{16})-MatterNodeDevice")
TOPOLOGY_READS = 2  # the default read and the topology-only read
SHA256 = re.compile(r"[0-9a-f]{64}")
REQUIRED_STUB_FILES = ("stub.py", "provenance.json")
KEPT_HEX = frozenset({"ext_pan_id"})


class ExpectationsPending(Exception):
    pass


def ready_expectations(spec):
    """The parameterized stub contract; refuses unless every pin is present."""
    if spec.get("model") != EXPECTATIONS_MODEL:
        raise ExpectationsPending("a2_expectations_model_mismatch")
    stub = spec.get("stub") or {}
    files = stub.get("files")
    if (spec.get("status") != "ready" or not stub.get("committed_dir") or type(files) is not dict
            or not all(name in files for name in REQUIRED_STUB_FILES)
            or not any(name.startswith("nodes/") for name in files)
            or not all(type(d) is str and SHA256.fullmatch(d) for d in files.values())
            or type(stub.get("source_commit")) is not str or len(stub["source_commit"]) != 40):
        raise ExpectationsPending("a2_stub_pending")
    canaries = spec.get("canaries")
    if (type(canaries) is not list or len(canaries) < 5
            or any(type(c) is not str or len(c) < 6 for c in canaries)):
        raise ExpectationsPending("a2_canaries_pending")
    expected = spec.get("expected") or {}
    kept = spec.get("kept_raw") or {}
    for value in kept.get("border_router", {}).values():
        if any(str(value).lower() in c.lower() or c.lower() in str(value).lower() for c in canaries):
            raise ExpectationsPending("a2_kept_raw_value_is_a_canary")
    if (not expected.get("matter_node_ids") or not expected.get("required_kinds")
            or type(expected.get("nodes")) is not int or type(expected.get("connections")) is not int
            or type(expected.get("dropped_unresolved")) is not int or not expected.get("background_commands")
            or not expected.get("topology_error_status") or not expected.get("schema_fault_status")):
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


def check_stub_record(records, *, mark, topology_reads, background_commands):
    """Before ``mark``: Core's own traffic and the harness's raw read. After it:
    exactly ``topology_reads`` topology commands, plus only Core background traffic.

    Every recorded ``get_network_topology`` carries args that are exactly the
    literal ``{"refresh": False}``.
    """
    allowed = set(background_commands) | {STUB_TOPOLOGY_COMMAND}
    assert all(record["command"] in allowed for record in records), "unexpected stub command"
    tail = [record["command"] for record in records[mark:]]
    assert tail.count(STUB_TOPOLOGY_COMMAND) == topology_reads
    topology = [record for record in records if record["command"] == STUB_TOPOLOGY_COMMAND]
    for record in topology:
        args = record["args"]
        assert type(args) is dict and set(args) == {"refresh"} and args["refresh"] is False
    return {"recorded_topology_commands": len(topology), "engineering_topology_commands": topology_reads,
            "recorded_refresh": [record["args"]["refresh"] for record in topology],
            "commands_after_mark": sorted(set(tail))}


def check_stub_state(state, *, recorded):
    """The stub's own in-memory view agrees with its jsonl record."""
    requests = state.get("topology_requests")
    assert type(requests) is list and len(requests) == recorded
    assert all(request == {"refresh": False} for request in requests)
    return {"topology_requests": len(requests), "refresh_values": sorted({r["refresh"] for r in requests})}


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


def check_topology_success(section, *, raw, devices, expected, kept_raw):
    """The projected A2 section against the raw Core reply and the raw registry."""
    assert section["status"] == "ok" and section["truncated"] is False
    nodes, connections = section["nodes"], section["connections"]
    ids = [node["id"] for node in nodes]
    assert all(GRAPH_REF.fullmatch(value) for value in ids) and len(set(ids)) == len(ids)
    assert section["total"]["nodes"] == len(raw["nodes"]) == len(nodes) == expected["nodes"]
    for edge in connections:
        assert edge["source"] in ids and edge["target"] in ids
    dropped = section["connections_dropped_unresolved"]
    assert dropped == expected["dropped_unresolved"]
    assert len(connections) + dropped == section["total"]["connections"] == len(raw["connections"])
    assert section["total"]["connections"] == expected["connections"]
    # The ghost edge: its endpoint is in no raw node, so it is the one dropped.
    raw_ids = {node["id"] for node in raw["nodes"]}
    ghosts = [c for c in raw["connections"] if c["source"] not in raw_ids or c["target"] not in raw_ids]
    assert len(ghosts) == dropped
    assert set(expected["required_kinds"]) <= {node["kind"] for node in nodes}
    matter = {node["node_id"]: node for node in nodes if node["kind"] == "matter"}
    assert sorted(matter) == sorted(expected["matter_node_ids"])
    for node_id, node in matter.items():
        assert node["ha_device_id"] == devices[node_id] and node["identity"] == "ha_device"
    for node_id in expected.get("wifi_station_node_ids", ()):
        assert matter[node_id]["network_type"] == "wifi"
        assert matter[node_id]["network_name"] is None and matter[node_id]["network_name_withheld"] is True
    for node in nodes:
        if node["kind"] in NON_HA_KINDS:
            assert node["ha_device_id"] is None and node["identity"] == "not_an_ha_device"
        if node["kind"] == "wifi_ap":
            assert node["network_name"] is None and node["network_name_withheld"] is True
        for key in ("ext_address", "ssid", "bssid", "host_name", "last_seen"):
            assert key not in node
    # Kept network-level values (section 5.1.2): unchanged from Core's reply.
    routers = [node for node in nodes if node["kind"] == "border_router"]
    assert len(routers) == 1
    router = routers[0]
    upstream_router = next(node for node in raw["nodes"] if node["kind"] == "border_router")
    for key, value in kept_raw["border_router"].items():
        # Kept, not made opaque; network-level hex is emitted in the module's
        # normalized form (lowercase, no separators), never otherwise changed.
        kept = normalize_identifier(value).lower() if key in KEPT_HEX else value
        assert router[key] == kept, key
        assert upstream_router[key] == value, key
    assert router["network_name_withheld"] is False
    for key in expected.get("withheld_border_router_text", ()):
        assert router[key] is None
    raw_by_node = {node.get("node_id"): node for node in raw["nodes"] if node.get("node_id") is not None}
    for node_id, node in matter.items():
        upstream = raw_by_node[node_id]
        assert node["rloc16"] == upstream.get("rloc16")
        assert node["network_type"] == upstream.get("network_type")
        assert node["ha_device_id"] == upstream.get("ha_device_id")
    return {"nodes": len(nodes), "connections": len(connections), "dropped_unresolved": dropped,
            "kinds": sorted({node["kind"] for node in nodes}), "matter_nodes": sorted(matter),
            "ha_device_ids_resolved": len(matter), "kept_raw_checked": sorted(kept_raw["border_router"]),
            "withheld_border_router_text": list(expected.get("withheld_border_router_text", ()))}


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
async def run(configured, entry, directory, *, expectations, read_stub_record, read_devices,
              stub_control, stub_state, reload_matter):
    """Run the A2 success and fault checks; the caller owns startup, identity and teardown proof.

    Harness-side callables, never via Engineering: ``read_stub_record()`` returns
    the stub's jsonl lines; ``read_devices()`` the raw disposable device
    registry; ``stub_control(body)`` posts a fault to the stub;
    ``stub_state()`` reads the stub's control view; ``reload_matter()`` reloads
    the disposable Core's Matter entry so it reconnects to the stub.
    """
    old = entry["capabilities"]
    assert len(old) == 32 and CAPABILITY not in {p["capability_id"] for p in old}
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
        return await _exercise(configured, runtime, upstream, devices, mark, canaries, expectations,
                               read_stub_record, stub_control, stub_state, reload_matter)
    finally:
        with diagnostics.check("cleanup", "service_close"):
            await close_disposable_authority(runtime)


async def _exercise(configured, runtime, upstream, devices, mark, canaries, expectations,
                    read_stub_record, stub_control, stub_state, reload_matter):
    expected = expectations["expected"]
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

    def assert_clean(result):
        assert result["identifier_policy"] == "opaque_per_response_v1"
        assert result["sanitization_failed_closed"] is False
        assert leaks(result, canaries) == []

    def recorded_topology():
        return sum(1 for r in stub_commands(read_stub_record()) if r["command"] == STUB_TOPOLOGY_COMMAND)

    with diagnostics.check("operation", "operation_dispatch"):
        both = await routed({})
        only_topology = await routed({"sections": [TOPOLOGY]})
        assert only_topology["sections"] == [TOPOLOGY] and only_topology["commands_sent"] == 1
    with diagnostics.check("readback", "value_assertions"):
        kept = expectations["kept_raw"]
        summary = check_topology_success(both[TOPOLOGY], raw=upstream, devices=devices, expected=expected,
                                         kept_raw=kept)
        assert check_topology_success(only_topology[TOPOLOGY], raw=upstream, devices=devices,
                                      expected=expected, kept_raw=kept) == summary
        for result in (both, only_topology):
            assert_clean(result)
            assert "connections_dropped_unresolved" in result["partial_reasons"]

    # Fault 1: Matter Server error_code 7 on get_network_topology.
    with diagnostics.check("operation", "operation_dispatch"):
        await stub_control({"mode": "topology_error"})
        before = recorded_topology()
        failed = await routed({"sections": [TOPOLOGY]})
        await stub_control({"mode": "none"})
    with diagnostics.check("readback", "value_assertions"):
        assert recorded_topology() == before + 1  # dispatched once, refused by the server
        assert failed[TOPOLOGY] == {"status": expected["topology_error_status"],
                                    "upstream_activity": list(both[TOPOLOGY]["upstream_activity"])}
        assert failed["completeness"] == "partial" and failed["partial_reasons"] == ["section_unavailable"]
        assert_clean(failed)

    # Fault 2: a server reporting schema 12; Core refuses before calling it.
    with diagnostics.check("helper_setup", "provider_setup"):
        await stub_control({"mode": "none", "server_info_schema_version": expected["schema_fault_version"]})
        await reload_matter()
    with diagnostics.check("operation", "operation_dispatch"):
        before = recorded_topology()
        old_schema = await routed({"sections": [TOPOLOGY]})
    with diagnostics.check("readback", "value_assertions"):
        assert recorded_topology() == before  # Core never forwarded the read
        assert old_schema[TOPOLOGY]["status"] == expected["schema_fault_status"]
        assert old_schema["partial_reasons"] == ["section_unavailable"]
        assert_clean(old_schema)

    with diagnostics.check("readback", "value_assertions"):
        engineering_reads = TOPOLOGY_READS + 1
        stub = check_stub_record(stub_commands(read_stub_record()), mark=mark,
                                 topology_reads=engineering_reads,
                                 background_commands=expected["background_commands"])
        state = check_stub_state(await stub_state(), recorded=stub["recorded_topology_commands"])
        assert NETWORK_TOPOLOGY_COMMAND in reader.sent
        assert not set(reader.sent) & set(NEVER_SENT_COMMANDS)
        assert runtime.health_snapshot()["fallback_count"] == 0
    return {"result": "PASS", "group": "thread_matter_topology", "scenario": "a2_success",
            "profile": reference(), "topology": summary, "stub_record": stub, "stub_state": state,
            "topology_refresh": False, "commands_sent": list(reader.sent),
            "datasets_status": both.get("datasets", {}).get("status"),
            "faults": {"topology_error": failed[TOPOLOGY]["status"],
                       "schema_version_12": old_schema[TOPOLOGY]["status"],
                       "schema_fault_forwarded_to_server": False},
            "canaries_checked": len(canaries), "canary_leaks": 0,
            "stub": {key: expectations["stub"][key] for key in ("owner", "source_pr", "source_commit",
                                                                 "committed_dir", "files")},
            "fallback_count": 0, "production_authority": False}
