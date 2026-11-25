"""Committed, cached-image G4 lanes for the two created-object profiles (HAMCP-329, HAMCP-298).

``--lane scene_create_delete`` proves ``core.scene_create_delete_operation`` and
``--lane created_helper_delete`` proves ``core.created_helper_delete_operation``
on the exact Core 2026.11.0 image, one lane per run.

No Docker pulls, host-side GitHub access, publication or production authority.
Only the cached exact Core image on a dedicated bridge; the Core host port binds
loopback. No ha-mcp container: both capabilities are Engineering-native. A new
run directory and clean committed checkout are mandatory. Failure is WITHHELD.
The production journal is verified read-only; synthetic test keys stay in memory.
"""
import argparse
import asyncio
from dataclasses import replace
import hashlib
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

# signal, subprocess, resources and REVIEWED_CORE_BINDINGS stay reachable here: the lane's tests use them through it.
VERSION = "2026.11.0"
JOURNAL_SHA256 = "3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d"
MANIFEST = ROOT / "ci/core-contract-read-files.json"
GROUPS = ROOT / "ci/core-evidence-groups.json"
SOURCE_HASHES = ROOT / "docs/evidence/hamcp315/core-sources/2026.11.0.json"
# CI mode's other reviewed anchors (HAMCP-489): each record is that anchor's exact-archive source hashes.
ANCHOR_SOURCE_HASHES = {"2026.11.3": ROOT / "docs/evidence/hamcp315/core-sources/2026.11.3.json"}
REVIEWED_ANCHORS = frozenset({VERSION, *ANCHOR_SOURCE_HASHES})
# group -> (capability, the reviewed compiled fingerprint this lane proves, lane name).
# A changed profile must be re-proven: the fingerprint here is the signing fingerprint.
LANES = {
    "scene_create_delete": (
        "core.scene_create_delete_operation",
        "sha256:93378ae0385528f16deb977a985b778ba2dd2d39be6f5cb397ef74f2060a400d",
        "ha-2026-11-0-scene-create"),
    "created_helper_delete": (
        "core.created_helper_delete_operation",
        "sha256:3f2e681310a009a4fc853af6fa24f8eca1c671c7dd75bddbbc8b403fec9cdbf4",
        "ha-2026-11-0-helper-delete"),
}
NETWORK_POSTURE = {"driver": "bridge", "host_bind_address": "127.0.0.1", "egress_allowed": True}
LABEL = "org.hamcp.lane"
PORTS = (18123,)
CORE_MEMORY = "2g"
CONFIG = core_lane_host.CONFIGS["created_object"]
INCLUDED = core_lane_host.INCLUDES["created_object"]


def resource_names(group):
    lane = LANES[group][2]
    return "beta25-real-ha-" + lane, "beta-rc2-contract-" + lane


class Refusal(Exception):
    pass


LANE_HOST = core_lane_host.Lane(sys.modules[__name__])
utc = core_lane_host.utc
sha256 = core_lane_host.sha256
command = LANE_HOST.command
cached_image = LANE_HOST.cached_image
require_free_ports = LANE_HOST.require_free_ports
inspect_owned = LANE_HOST.inspect_owned


def compiled_reference(group):
    from created_object_contract_acceptance import reference
    return reference(group)


@diagnostics.checked("image_verify", "journal_inputs")
def source_manifest(group, archive_path, provenance, *, release_commit=None, anchor=None):
    """Bind the manifest profile, committed source hashes and exact archive. CI mode reads the release's
    archive (``release_commit``) against the anchor's committed hashes, so any drift refuses; an anchor
    without a committed record refuses."""
    anchor = VERSION if anchor is None else anchor
    hashes = SOURCE_HASHES if anchor == VERSION else ANCHOR_SOURCE_HASHES.get(anchor)
    capability = LANES[group][0]
    profile = read_json(MANIFEST).get("profiles", {}).get(capability)
    if not profile or hashes is None or not hashes.is_file() or read_json(GROUPS).get(group) != [capability]:
        raise Refusal("created_object_manifest_missing")
    record = read_json(hashes)
    if (record["version"], record["commit"], record["tree"], record["archive_sha256"]) != (
            anchor, provenance["source_commit"], provenance["source_tree"],
            provenance["source_archive_sha256"]):
        raise Refusal("created_object_source_provenance_mismatch")
    if len(set(profile)) != len(profile) or not set(profile) <= set(record["hashes"]):
        raise Refusal("created_object_manifest_mismatch")
    rows = {path: record["hashes"][path] for path in profile}
    commit = provenance["source_commit"] if release_commit is None else release_commit
    prefix, observed = "core-" + commit + "/", {}
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar:
            name = member.name[len(prefix):] if member.name.startswith(prefix) else None
            if name in rows and member.isreg():
                observed[name] = hashlib.sha256(tar.extractfile(member).read()).hexdigest()
    if observed != rows:
        raise Refusal("created_object_source_hash_mismatch")
    return {"manifest_sha256": sha256(MANIFEST), "source_hashes_sha256": sha256(hashes),
            "profile_paths": len(profile)}


@diagnostics.checked("image_verify", "journal_inputs")
def inputs(group, journal_path, archive_path):
    from core_registry_one_tap import load_public_key
    from prepare_core_release_registry import parse_journal, require_known_contracts
    provenance = read_json(core_lane_host.LANE_PROVENANCE)
    if (sha256(journal_path) != JOURNAL_SHA256
            or sha256(archive_path) != provenance["source_archive_sha256"]):
        raise Refusal("input_hash_mismatch")
    capability, fingerprint, _ = LANES[group]
    reference = compiled_reference(group)
    if reference.get("capability_id") != capability or reference.get("contract_fingerprint") != fingerprint:
        raise Refusal("profile_fingerprint_mismatch")
    sources = source_manifest(group, archive_path, provenance)
    public = load_public_key((ROOT / "docs/evidence/hamcp222/core-public-key.txt").read_bytes(), pinned=True)
    current = parse_journal(Path(journal_path).read_bytes(), public)
    if current.accepted.sequence != 17:
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
    if len(entry["capabilities"]) != 32 or any(
            p["capability_id"] == capability for p in entry["capabilities"]):
        raise Refusal("predecessor_capabilities_mismatch")
    return entry, reference, sources


@diagnostics.checked("image_verify", "journal_inputs")
def ci_inputs(group, ctx, archive_path):
    """CI mode's inputs: the release's signed entry and exact archive, and the reviewed profile and
    source hashes of the context's anchor (D5)."""
    entry = LANE_HOST.ci_entry(ctx, archive_path)
    capability, fingerprint, _ = LANES[group]
    reference = compiled_reference(group)
    if reference.get("capability_id") != capability or reference.get("contract_fingerprint") != fingerprint:
        raise Refusal("profile_fingerprint_mismatch")
    sources = source_manifest(group, archive_path, core_lane_host.lane_provenance(ctx),
                              release_commit=ctx["provenance"]["source_commit"], anchor=ctx["anchor"])
    return entry, reference, sources


def images():
    return LANE_HOST.images(upstream=False)


def verify_port_mapping(name, network, container_port, host_port, run_id):
    """Each group owns its own network, so the check names it."""
    return LANE_HOST.verify_port_mapping(name, container_port, host_port, run_id, network=network)


@diagnostics.checked("helper_setup", "helper_contract")
async def exercise(group, run_dir, entry, core_image, run_id, core_memory, *, version=VERSION, mode="local"):
    # Import only after the closed disposable endpoints have been set.
    import real_ha_contract_tests as harness
    from created_object_contract_acceptance import run
    core, _ = resource_names(group)
    with diagnostics.check("auth", "bootstrap"):
        token = await harness.bootstrap_disposable_admin()
    configured = replace(harness.settings(token), audit_path=str(run_dir / "audit.jsonl"))
    if configured.upstream_dashboard_mcp_url:
        raise Refusal("upstream_not_expected")
    from ha_mcp_engineering.clients import HomeAssistantRestClient
    with diagnostics.check("core_boot", "runtime_ready"):
        observed = await harness.wait_for_runtime_ready(HomeAssistantRestClient(configured))
    with diagnostics.check("image_verify", "running_identity"):
        if observed.get("version") != version:
            raise Refusal("running_core_version_mismatch")
        data = inspect_owned("container", core, run_id)
        if data is None or data["Image"] != cached_image(core_image) or data["Config"]["Image"] != core_image:
            raise Refusal("running_image_mismatch")
        if data.get("HostConfig", {}).get("Memory") != host.memory_bytes(core_memory):
            raise Refusal("running_container_memory_mismatch")
        identities = {core: {"image": core_image, "config_digest": data["Image"],
                             "memory_bytes": data["HostConfig"]["Memory"]}}
    return {**await run(group, configured, entry, run_dir, mode=mode), "images": identities}


@diagnostics.checked("admission", "preflight")
def _main(argv=None):
    if not __debug__:
        raise Refusal("assertions_required")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=tuple(LANES), required=True)
    parser.add_argument("--mode", choices=("local", "ci"), required=True)
    parser.add_argument("--pull", choices=("never", "digest"), required=True)
    parser.add_argument("--journal", type=Path, help="local mode only")
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--core-memory", default=CORE_MEMORY, help="Core Docker limit (default: 2g)")
    parser.add_argument("--fixture-memory", default=host.FIXTURE_MEMORY, help="cleanup Docker limit (default: 128m)")
    args = parser.parse_args(argv)
    core_lane_host.require_mode_arguments(parser, args)
    group, (core_name, network_name), lane_name = args.lane, resource_names(args.lane), LANES[args.lane][2]
    receipt = group + ".json"
    limits = {"core": args.core_memory, "fixture": args.fixture_memory}
    for memory in limits.values():
        host.memory_bytes(memory)
    if args.mode == "ci":
        ctx, revision, (entry, reference, sources), (core_image, fixture_image), run_dir = LANE_HOST.ci_admission(
            args.run_dir, lambda ctx: ci_inputs(group, ctx, args.source_archive),
            upstream=False, containers=(core_name,), network=network_name)
        admission, version = None, ctx["provenance"]["version"]
    else:
        if os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("CORE_EVIDENCE_CONTEXT"):
            raise Refusal("local_only_closed_lane")
        revision = LANE_HOST.clean_revision()
        entry, reference, sources = inputs(group, args.journal, args.source_archive)
        core_image, fixture_image = images()
        for image in (core_image, fixture_image):
            cached_image(image)
        LANE_HOST.require_unclaimed((core_name,), network_name)
        run_dir = LANE_HOST.claim(args.run_dir)
        admission = LANE_HOST.admit(run_dir, receipt, revision, (core_name,), network_name)
        if admission is None:
            return 1
        version = VERSION
    run_id = uuid4().hex
    owned = LANE_HOST.configuration(run_dir, core_name, CONFIG, includes=INCLUDED)
    # Engineering plan, audit and synthetic-registry state of the run; removed with the fixture.
    state = run_dir / "engineering-state"
    state.mkdir(mode=0o700)
    os.environ.update(CORE_EVIDENCE_HOST_MODE="actions" if args.mode == "ci" else "local", RUNNER_TEMP=str(run_dir),
        HA_CONTRACT_LANE=lane_name, HA_FIXTURE_WRITER_IMAGE=fixture_image,
        HA_CONTRACT_CONTAINER=core_name, HA_CONTRACT_NETWORK=network_name,
        REAL_HA_URL="http://127.0.0.1:18123", REAL_HA_EXPECTED_VERSION=version)
    for name in ("REAL_HA_UPSTREAM_IMAGE", "REAL_HA_UPSTREAM_VERSION",
                 "REAL_HA_UPSTREAM_CONTAINER", "REAL_HA_UPSTREAM_PORT"):
        os.environ.pop(name, None)
    result = {"model": "core-created-object-local-evidence-v1", "group": group,
              "result": "WITHHELD", "started_utc": utc(), "source_commit": revision,
              "journal_sha256": JOURNAL_SHA256, "core_version": VERSION,
              "profile_reference": reference, "contract_fingerprint": reference["contract_fingerprint"],
              **sources, "host_admission": admission, "container_memory": limits,
              "network_posture": NETWORK_POSTURE, "mode": "local", "pull": "never", "cleanup": {"clean": False}}
    if args.mode == "ci":
        result.update(LANE_HOST.ci_fields(ctx))
    def body():
        LANE_HOST.create_bridge(network_name, run_id)
        LANE_HOST.start_core(core_name, network_name, run_id, args.core_memory, owned, core_image,
                             "--publish", "127.0.0.1:18123:8123")
        verify_port_mapping(core_name, network_name, 8123, 18123, run_id)
        async def bounded():
            async with asyncio.timeout(1800):
                return await exercise(group, state, entry, core_image, run_id, args.core_memory,
                                      version=version, mode=args.mode)
        result.update(asyncio.run(bounded()))
        if result.get("profile") != reference:
            raise Refusal("receipt_profile_mismatch")
    return LANE_HOST.execute(result, receipt, body, run_dir=run_dir, run_id=run_id, owned=owned,
                             network=network_name, remove=(core_name,), containers=(core_name,),
                             fixture_memory=args.fixture_memory, scratch=(state,))


def main(argv=None):
    with diagnostics.lane("created_object"):
        return _main(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"result": "WITHHELD", "failure_type": type(exc).__name__,
                          "failure": diagnostics.failure(exc)}))
        raise SystemExit(1) from None
