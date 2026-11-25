"""Committed, cached-image device_configuration_entity lane (HAMCP-366).

The signing lane for ``core.device_configuration_entity_operation`` (design
HAMCP-297 section 9.2): real Core 2026.11.0 ``matter`` entities from a
lane-owned, test-only Matter Server stub, exercised by the signed build only.

No Docker pulls, GitHub access, publication or production authority. Only cached
exact images on a dedicated bridge with egress; host ports bind loopback. A new
run directory and clean committed checkout are mandatory. Failure is WITHHELD.
The production journal is verified read-only; synthetic test keys stay in memory.
The run refuses before any container starts unless the compiled profile equals
the reference recorded here for signing, unless the manifest profile entry and
its committed source hashes match the Core source archive (HAMCP-297 G5), and
unless the stub, its lane entry and the three node dumps match their committed
provenance and the Core source archive.
"""
import argparse
import asyncio
from dataclasses import replace
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import core_evidence_host as host
import core_lane_host
import core_local_resources as resources
import lane_failure_diagnostics as diagnostics
from core_lane_bindings import REVIEWED_CORE_BINDINGS
from core_release_evidence import read_json

# signal, subprocess and resources stay reachable here: the lane's tests patch them through it.
VERSION = "2026.11.0"
JOURNAL_SHA256 = "3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d"
PREDECESSOR_SEQUENCE, PREDECESSOR_CAPABILITIES = 17, 32
GROUP = "device_configuration_entity"
RECEIPT = GROUP + ".json"
CAPABILITY = "core.device_configuration_entity_operation"
MANIFEST = ROOT / "ci/core-contract-read-files.json"
SOURCE_HASHES = ROOT / "docs/evidence/hamcp297/lane/core-device-configuration-source-hashes.json"
# CI mode's other reviewed anchors (HAMCP-489): each record is generated from that anchor's exact archive.
ANCHOR_SOURCE_HASHES = {"2026.11.3": ROOT / "docs/evidence/hamcp297/lane/core-device-configuration-source-hashes-2026.11.3.json"}
REVIEWED_ANCHORS = frozenset({VERSION, *ANCHOR_SOURCE_HASHES})
# The profile reference recorded for signing. A build whose compiled profile
# differs in any of these values is not the signed build and does not run.
PROFILE = {
    "capability_id": CAPABILITY,
    "profile_id": "core_device_configuration_entity_operation_v1",
    "profile_version": 1,
    "adapter_id": "compiled-core-device-configuration-entity-operation-v1",
    "contract_fingerprint": "sha256:74a03a640f60c9adaf8508a0a7fcbca7745f29b3d09e1dc9a611ac361475293c",
}
LANE = "ha-2026-11-0-device-configuration"
CORE = "beta25-real-ha-" + LANE
UPSTREAM = "beta25-real-ha-upstream-" + LANE
STUB = "beta25-real-ha-matter-stub-" + LANE
NETWORK = "beta-rc2-contract-" + LANE
NETWORK_POSTURE = {"driver": "bridge", "host_bind_address": "127.0.0.1", "egress_allowed": True}
LABEL = "org.hamcp.helper-evidence-run"
# Loopback host ports: Core, the exact ha-mcp child, the stub's control route.
CORE_PORT, UPSTREAM_PORT, STUB_PORT = 18297, 18298, 18299
PORTS = (CORE_PORT, UPSTREAM_PORT, STUB_PORT)
STUB_CONTAINER_PORT = 5580
STUB_ALIAS = "hamcp366-matter-stub"
CORE_MEMORY = "2g"
UPSTREAM_MEMORY = "768m"
STUB_MEMORY = "256m"
STUB_ASSET = core_lane_host.MATTER_STUB
LANE_ASSET = core_lane_host.DEVICE_LANE
FIXTURE_COMPONENT = "hamcp366_refusal_fixture"
CORE_PROVENANCE = core_lane_host.LANE_PROVENANCE
DUMP_DIRECTORY = "tests/components/matter/fixtures/nodes/"
CONFIG = core_lane_host.CONFIGS["device_configuration_entity"]


class Refusal(Exception):
    pass


LANE_HOST = core_lane_host.Lane(sys.modules[__name__])
utc = core_lane_host.utc
sha256 = core_lane_host.sha256
command = LANE_HOST.command
cached_image = LANE_HOST.cached_image
require_free_ports = LANE_HOST.require_free_ports
inspect_owned = LANE_HOST.inspect_owned
verify_port_mapping = LANE_HOST.verify_port_mapping


@diagnostics.checked("image_verify", "journal_inputs")
def source_manifest(archive_path, provenance, *, release_commit=None, anchor=None):
    """Bind the manifest profile, committed source hashes and exact archive. CI mode reads the release's
    archive (``release_commit``) against the anchor's committed hashes, so any drift refuses; an anchor
    without a committed record refuses."""
    anchor = VERSION if anchor is None else anchor
    hashes = SOURCE_HASHES if anchor == VERSION else ANCHOR_SOURCE_HASHES.get(anchor)
    profile = read_json(MANIFEST).get("profiles", {}).get(CAPABILITY)
    if not profile or hashes is None or not hashes.is_file():
        raise Refusal("device_configuration_manifest_missing")
    record = read_json(hashes)
    rows = {row["path"]: row["sha256"] for row in record["files"]}
    if (record["core_version"], record["source_commit"], record["source_archive_sha256"]) != (
            anchor, provenance["source_commit"], provenance["source_archive_sha256"]):
        raise Refusal("device_configuration_source_provenance_mismatch")
    if [row["path"] for row in record["files"]] != profile or len(rows) != len(profile):
        raise Refusal("device_configuration_manifest_mismatch")
    commit = provenance["source_commit"] if release_commit is None else release_commit
    prefix, observed = "core-" + commit + "/", {}
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar:
            name = member.name[len(prefix):] if member.name.startswith(prefix) else None
            if name in rows and member.isreg():
                observed[name] = hashlib.sha256(tar.extractfile(member).read()).hexdigest()
    if observed != rows:
        raise Refusal("device_configuration_source_hash_mismatch")
    return {"manifest_sha256": sha256(MANIFEST), "source_hashes_sha256": sha256(hashes),
            "profile_paths": len(profile)}


@diagnostics.checked("image_verify", "journal_inputs")
def inputs(journal_path, archive_path):
    from core_registry_one_tap import load_public_key
    from prepare_core_release_registry import parse_journal, require_known_contracts
    provenance = read_json(CORE_PROVENANCE)
    if (sha256(journal_path) != JOURNAL_SHA256
            or sha256(archive_path) != provenance["source_archive_sha256"]):
        raise Refusal("input_hash_mismatch")
    sources = source_manifest(archive_path, provenance)
    public = load_public_key((ROOT / "docs/evidence/hamcp222/core-public-key.txt").read_bytes(), pinned=True)
    current = parse_journal(Path(journal_path).read_bytes(), public)
    if current.accepted.sequence != PREDECESSOR_SEQUENCE:
        raise Refusal("predecessor_sequence_mismatch")
    entries = [entry for entry in current.accepted.entries if entry.version == VERSION]
    if len(entries) != 1:
        raise Refusal("predecessor_entry_missing")
    require_known_contracts(entries[0])
    entry = entries[0].to_mapping()
    # Fixture entry_id intentionally names disposable authority; the production
    # entry retains its journal-authenticated identity. All actual provenance agrees.
    if any(entry[key] != value for key, value in provenance.items() if key != "entry_id"):
        raise Refusal("predecessor_provenance_mismatch")
    if len(entry["capabilities"]) != PREDECESSOR_CAPABILITIES or any(
            p["capability_id"] == PROFILE["capability_id"] for p in entry["capabilities"]):
        raise Refusal("predecessor_capabilities_mismatch")
    return entry, sources


def loaded_profile():
    """The compiled profile must be the one recorded for signing (design section 9.2, F1)."""
    sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
    from ha_mcp_engineering.ha_core_readmission.profiles import CORE_DEVICE_CONFIGURATION_PROFILES
    if len(CORE_DEVICE_CONFIGURATION_PROFILES) != 1:
        raise Refusal("profile_fingerprint_mismatch")
    value = CORE_DEVICE_CONFIGURATION_PROFILES[0].to_mapping()
    loaded = {key: value.get(key) for key in PROFILE}
    # Type-exact: version True or 1.0 equals 1 under ==, and is not the recorded profile.
    if [(type(item), item) for item in loaded.values()] != [(type(item), item) for item in PROFILE.values()]:
        raise Refusal("profile_fingerprint_mismatch")
    return loaded


@diagnostics.checked("image_verify", "journal_inputs")
def stub_assets(archive_path, *, release_commit=None):
    """Hash-bind the stub, its lane entry, the refusal fixture and the three node dumps.

    The stub files must equal their committed provenance, and each dump must
    equal the member of the hash-pinned Core source archive it was copied from.
    The dumps are version-specific, so keyed to the anchor (HAMCP-443 D5): CI mode
    reads them from the release's archive (``release_commit``) and refuses any drift.
    """
    provenance = read_json(STUB_ASSET / "provenance.json")
    files = provenance["files"]
    present = {path.relative_to(STUB_ASSET).as_posix() for path in STUB_ASSET.rglob("*")
               if path.is_file() and "__pycache__" not in path.parts}
    if present != set(files) | {"provenance.json"} or any(
            sha256(STUB_ASSET / name) != digest for name, digest in files.items()):
        raise Refusal("stub_asset_hash_mismatch")
    dumps = {name[len("nodes/"):]: digest for name, digest in files.items() if name.startswith("nodes/")}
    source = read_json(CORE_PROVENANCE)
    if (len(dumps) != 3 or set(dumps) != set(provenance["node_dumps"]["node_ids"])
            or provenance["node_dumps"]["source_commit"] != source["source_commit"]):
        raise Refusal("stub_dump_source_mismatch")
    commit = source["source_commit"] if release_commit is None else release_commit
    prefix = "core-" + commit + "/" + DUMP_DIRECTORY
    found = {}
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            name = member.name[len(prefix):] if member.name.startswith(prefix) else None
            if name in dumps and member.isfile():
                found[name] = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
                if len(found) == len(dumps):
                    break
    if found != dumps:
        raise Refusal("stub_dump_source_mismatch")
    component = LANE_ASSET / "custom_components" / FIXTURE_COMPONENT
    return {"stub_sha256": files["stub.py"], "entry_sha256": sha256(LANE_ASSET / "stub_entry.py"),
            "dump_hashes": dumps,
            "refusal_fixture_sha256": {path.name: sha256(path) for path in sorted(component.iterdir())
                                       if path.is_file()}}


def start_stub(core_image, run_id, memory, output, dumps):
    """The lane-owned Matter Server stub: test-only, lane network plus one loopback port."""
    mounts = [(STUB_ASSET / "stub.py", "/lane/stub.py"), (LANE_ASSET / "stub_entry.py", "/wrapper/stub_entry.py"),
              *((STUB_ASSET / "nodes" / name, "/nodes/" + name) for name in sorted(dumps))]
    command("docker", "run", "--detach", "--pull", "never", "--name", STUB,
            "--label", LABEL + "=" + run_id, "--network", NETWORK, "--network-alias", STUB_ALIAS,
            "--memory", memory, "--publish", f"127.0.0.1:{STUB_PORT}:{STUB_CONTAINER_PORT}",
            "--security-opt", "no-new-privileges", "--cap-drop", "ALL", "--read-only",
            "--user", f"{os.getuid()}:{os.getgid()}",
            *(item for source, target in mounts
              for item in ("--mount", f"type=bind,source={source},target={target},readonly")),
            "--mount", f"type=bind,source={output},target=/out",
            "--entrypoint", "python3", core_image, "-I", "/wrapper/stub_entry.py",
            "--stub", "/lane/stub.py", "--nodes", "/nodes", "--out", "/out/stub.jsonl",
            "--port", str(STUB_CONTAINER_PORT))


@diagnostics.checked("container_start", "port_mapping")
def stub_address(run_id, network):
    """The stub's one IPv4 address, which must lie inside the lane network's own subnet.

    Observed on this lane's non-internal bridge: the running Core does not resolve
    Docker's embedded-DNS names (resolver timeout; cause not established). The stub
    name is therefore pinned for Core as a hosts entry. Only an address that the
    lane's own dedicated network assigned to the lane's own stub may be pinned.
    """
    data = inspect_owned("container", STUB, run_id)
    networks = ((data or {}).get("NetworkSettings") or {}).get("Networks") or {}
    value = (networks.get(NETWORK) or {}).get("IPAddress") if set(networks) == {NETWORK} else None
    ranges = ((network or {}).get("IPAM") or {}).get("Config") or []
    try:
        if type(value) is not str or len(ranges) != 1 or type(ranges[0].get("Subnet")) is not str:
            raise ValueError
        address, subnet = ipaddress.IPv4Address(value), ipaddress.IPv4Network(ranges[0]["Subnet"])
    except (ValueError, AttributeError):
        raise Refusal("stub_address_not_on_lane_network") from None
    if (not subnet.is_private or address not in subnet
            or address in (subnet.network_address, subnet.broadcast_address)
            or value == ranges[0].get("Gateway")):
        raise Refusal("stub_address_not_on_lane_network")
    return value


@diagnostics.checked("image_verify", "journal_inputs")
def ci_inputs(ctx, archive_path):
    """CI mode's inputs: the release's signed entry and exact archive, the anchor's source hashes and dumps (D5)."""
    commit = ctx["provenance"]["source_commit"]
    entry = LANE_HOST.ci_entry(ctx, archive_path)
    sources = source_manifest(archive_path, core_lane_host.lane_provenance(ctx), release_commit=commit,
                              anchor=ctx["anchor"])
    return entry, sources, stub_assets(archive_path, release_commit=commit)


def images():
    return LANE_HOST.images(upstream=True)


@diagnostics.checked("helper_setup", "helper_contract")
async def exercise(run_dir, entry, core_image, upstream_image, run_id, memory, stub_hashes, *, version=VERSION,
                   mode="local", anchor=VERSION):
    # Import only after the closed disposable endpoints have been set.
    import real_ha_contract_tests as harness
    import device_configuration_contract_acceptance as acceptance
    with diagnostics.check("auth", "bootstrap"):
        token = await harness.bootstrap_disposable_admin()
    configured = replace(harness.settings(token), audit_path=str(run_dir / "audit.jsonl"))
    from ha_mcp_engineering.clients import HomeAssistantRestClient
    with diagnostics.check("core_boot", "runtime_ready"):
        observed = await harness.wait_for_runtime_ready(
            HomeAssistantRestClient(acceptance.pinned(configured)))
    with diagnostics.check("image_verify", "running_identity"):
        if observed.get("version") != version:
            raise Refusal("running_core_version_mismatch")
    with diagnostics.check("container_start", "upstream_start"):
        await harness._start_exact_upstream(
            token, pull_policy="never", labels=(LABEL + "=" + run_id,), environment_directory=run_dir,
            memory=memory["upstream"],
            verify_started=lambda: verify_port_mapping(UPSTREAM, UPSTREAM_PORT, UPSTREAM_PORT, run_id))
    identities = {}
    with diagnostics.check("image_verify", "running_identity"):
        for name, image, limit in ((CORE, core_image, memory["core"]),
                                   (UPSTREAM, upstream_image, memory["upstream"]),
                                   (STUB, core_image, memory["stub"])):
            data = inspect_owned("container", name, run_id)
            if data is None or data["Image"] != cached_image(image) or data["Config"]["Image"] != image:
                raise Refusal("running_image_mismatch")
            if data.get("HostConfig", {}).get("Memory") != host.memory_bytes(limit):
                raise Refusal("running_container_memory_mismatch")
            identities[name] = {"image": image, "config_digest": data["Image"],
                                "memory_bytes": data["HostConfig"]["Memory"]}
    control = f"http://127.0.0.1:{STUB_PORT}/_lane/control"
    with diagnostics.check("container_start", "upstream_start"):
        await acceptance.stub_ready(control)
    result = await acceptance.run(configured, entry, run_dir, mode=mode, stub_url=control, stub_hashes={
        key: stub_hashes[key] for key in ("stub_sha256", "entry_sha256", "dump_hashes")},
        upstream_version=REVIEWED_CORE_BINDINGS[anchor].child_consumer)
    return {**result, "images": identities}


@diagnostics.checked("admission", "preflight")
def _main(argv=None):
    if not __debug__:
        raise Refusal("assertions_required")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("local", "ci"), required=True)
    parser.add_argument("--pull", choices=("never", "digest"), required=True)
    parser.add_argument("--journal", type=Path, help="local mode only")
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--core-memory", default=CORE_MEMORY, help="Core Docker limit (default: 2g)")
    parser.add_argument("--upstream-memory", default=UPSTREAM_MEMORY, help="upstream Docker limit (default: 768m)")
    parser.add_argument("--stub-memory", default=STUB_MEMORY, help="Matter stub Docker limit (default: 256m)")
    parser.add_argument("--fixture-memory", default=host.FIXTURE_MEMORY, help="cleanup Docker limit (default: 128m)")
    args = parser.parse_args(argv)
    core_lane_host.require_mode_arguments(parser, args)
    limits = {"core": args.core_memory, "upstream": args.upstream_memory, "stub": args.stub_memory,
              "fixture": args.fixture_memory}
    for memory in limits.values():
        host.memory_bytes(memory)
    if args.mode == "ci":
        # The signed build only: checked before any input, image or container.
        ctx, revision, (profile, (entry, sources, stub_hashes)), pulled, run_dir = LANE_HOST.ci_admission(
            args.run_dir, lambda ctx: (loaded_profile(), ci_inputs(ctx, args.source_archive)),
            upstream=True, containers=(CORE, UPSTREAM, STUB), network=NETWORK)
        core_image, upstream_image, fixture_image = pulled
        admission, version, anchor = None, ctx["provenance"]["version"], ctx["anchor"]
    else:
        if os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("CORE_EVIDENCE_CONTEXT"):
            raise Refusal("local_only_closed_lane")
        revision = LANE_HOST.clean_revision()
        # The signed build only: checked before the journal, any image or any container.
        profile = loaded_profile()
        entry, sources = inputs(args.journal, args.source_archive)
        stub_hashes = stub_assets(args.source_archive)
        core_image, upstream_image, fixture_image = images()
        for image in (core_image, upstream_image, fixture_image):
            cached_image(image)
        LANE_HOST.require_unclaimed((CORE, UPSTREAM, STUB), NETWORK)
        run_dir = LANE_HOST.claim(args.run_dir)
        admission = LANE_HOST.admit(run_dir, RECEIPT, revision, (CORE, UPSTREAM, STUB), NETWORK)
        if admission is None:
            return 1
        version = anchor = VERSION
    run_id = uuid4().hex
    # Refusal fixture (test-only custom integration); committed, hashed above.
    owned = LANE_HOST.configuration(run_dir, CORE, CONFIG,
                                    components=(LANE_ASSET / "custom_components" / FIXTURE_COMPONENT,))
    stub_output = run_dir / "matter-stub"
    stub_output.mkdir(mode=0o700)
    os.environ.update(CORE_EVIDENCE_HOST_MODE="actions" if args.mode == "ci" else "local", RUNNER_TEMP=str(run_dir),
        HA_CONTRACT_LANE=LANE, HA_FIXTURE_WRITER_IMAGE=fixture_image,
        HA_CONTRACT_CONTAINER=CORE, HA_CONTRACT_NETWORK=NETWORK,
        REAL_HA_URL=f"http://127.0.0.1:{CORE_PORT}", REAL_HA_EXPECTED_VERSION=version,
        REAL_HA_UPSTREAM_IMAGE=upstream_image, REAL_HA_UPSTREAM_VERSION=REVIEWED_CORE_BINDINGS[anchor].child_consumer,
        REAL_HA_UPSTREAM_CONTAINER=UPSTREAM, REAL_HA_UPSTREAM_PORT=str(UPSTREAM_PORT))
    result = {"model": "core-device-configuration-local-evidence-v1", "group": GROUP,
              "result": "WITHHELD", "started_utc": utc(), "source_commit": revision,
              "core_version": VERSION, "signing_profile": profile, "stub_assets": stub_hashes,
              "journal_sha256": JOURNAL_SHA256, **sources,
              "host_admission": admission, "container_memory": limits, "network_posture": NETWORK_POSTURE,
              "mode": "local", "pull": "never", "cleanup": {"clean": False}}
    if args.mode == "ci":
        result.update(LANE_HOST.ci_fields(ctx))
    def body():
        network = LANE_HOST.create_bridge(NETWORK, run_id)
        # The stub starts first: Core is given the stub's lane address as a hosts entry.
        with diagnostics.check("container_start", "core_start"):
            start_stub(core_image, run_id, args.stub_memory, stub_output, stub_hashes["dump_hashes"])
        verify_port_mapping(STUB, STUB_CONTAINER_PORT, STUB_PORT, run_id)
        address = stub_address(run_id, network)
        result["stub_host_entry"] = {"name": STUB_ALIAS, "address": address}
        LANE_HOST.start_core(CORE, NETWORK, run_id, args.core_memory, owned, core_image,
                             "--add-host", f"{STUB_ALIAS}:{address}", "--publish", f"127.0.0.1:{CORE_PORT}:8123")
        verify_port_mapping(CORE, 8123, CORE_PORT, run_id)
        async def bounded():
            async with asyncio.timeout(1800):
                return await exercise(run_dir, entry, core_image, upstream_image, run_id, limits,
                                      stub_hashes, version=version, mode=args.mode, anchor=anchor)
        outcome = asyncio.run(bounded())
        # A receipt for any other profile, or for other stub bytes, is not this lane's receipt.
        if outcome.get("profile") != profile or outcome.get("stub") != {
                key: stub_hashes[key] for key in ("stub_sha256", "entry_sha256", "dump_hashes")}:
            raise Refusal("receipt_profile_mismatch")
        result.update(outcome)
    return LANE_HOST.execute(result, RECEIPT, body, run_dir=run_dir, run_id=run_id, owned=owned, network=NETWORK,
                             remove=(UPSTREAM, STUB, CORE), containers=(CORE, UPSTREAM, STUB),
                             fixture_memory=args.fixture_memory)


def main(argv=None):
    with diagnostics.lane(GROUP):
        return _main(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"result": "WITHHELD", "failure_type": type(exc).__name__,
                          "failure": diagnostics.failure(exc)}))
        raise SystemExit(1) from None
