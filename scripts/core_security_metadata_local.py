"""Committed, cached-image security_metadata lane for the seq-17 -> seq-18 review.

No Docker pulls, GitHub access, publication or production authority. Only cached
exact images on a dedicated bridge (non-internal only because Docker cannot
publish ports from an internal network; the lane itself needs no egress); the
Core host port binds loopback. No ha-mcp container: the capability is
Engineering-native and every read is a Core WebSocket read. A new run directory and clean committed
checkout are mandatory. Failure is WITHHELD. The production journal is verified
read-only; synthetic test keys stay in memory.
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
from core_release_evidence import read_json

# signal, subprocess and resources stay reachable here: the lane's tests patch them through it.
VERSION = "2026.11.0"
JOURNAL_SHA256 = "3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d"
CAPABILITY = "core.security_metadata_read"
# The reviewed compiled profile this lane proves; a changed profile must be re-proven.
CONTRACT_FINGERPRINT = "sha256:9aa788b50f6d9544808bd8b89fd50c00ffd32e7ebcdd940b9dddace18b2e9c55"
MANIFEST = ROOT / "ci/core-contract-read-files.json"
SOURCE_HASHES = ROOT / "docs/evidence/hamcp134/lane/core-security-metadata-source-hashes.json"
LANE = "ha-2026-11-0-security-metadata"
CORE = "beta25-real-ha-" + LANE
NETWORK = "beta-rc2-contract-" + LANE
NETWORK_POSTURE = {"driver": "bridge", "host_bind_address": "127.0.0.1", "egress_allowed": True}
LABEL = "org.hamcp.security-metadata-evidence-run"
PORTS = (18123,)
CORE_MEMORY = "2g"
RECEIPT = "security_metadata.json"
CONFIG = core_lane_host.CONFIGS["security_metadata"]


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


def compiled_reference():
    sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
    from security_metadata_contract_acceptance import reference
    return reference()


@diagnostics.checked("image_verify", "journal_inputs")
def source_manifest(archive_path, provenance):
    """Bind the manifest profile, committed source hashes and exact archive."""
    profile = read_json(MANIFEST).get("profiles", {}).get(CAPABILITY)
    if not profile or not SOURCE_HASHES.is_file():
        raise Refusal("security_metadata_manifest_missing")
    record = read_json(SOURCE_HASHES)
    rows = {row["path"]: row["sha256"] for row in record["files"]}
    if (record["core_version"], record["source_commit"], record["source_archive_sha256"]) != (
            VERSION, provenance["source_commit"], provenance["source_archive_sha256"]):
        raise Refusal("security_metadata_source_provenance_mismatch")
    if [row["path"] for row in record["files"]] != profile or len(rows) != len(profile):
        raise Refusal("security_metadata_manifest_mismatch")
    prefix, observed = "core-" + provenance["source_commit"] + "/", {}
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar:
            name = member.name[len(prefix):] if member.name.startswith(prefix) else None
            if name in rows and member.isreg():
                observed[name] = hashlib.sha256(tar.extractfile(member).read()).hexdigest()
    if observed != rows:
        raise Refusal("security_metadata_source_hash_mismatch")
    return {"manifest_sha256": sha256(MANIFEST), "source_hashes_sha256": sha256(SOURCE_HASHES),
            "profile_paths": len(profile)}


@diagnostics.checked("image_verify", "journal_inputs")
def inputs(journal_path, archive_path):
    from core_registry_one_tap import load_public_key
    from prepare_core_release_registry import parse_journal, require_known_contracts
    provenance = read_json(core_lane_host.LANE_PROVENANCE)
    if (sha256(journal_path) != JOURNAL_SHA256
            or sha256(archive_path) != provenance["source_archive_sha256"]):
        raise Refusal("input_hash_mismatch")
    reference = compiled_reference()
    if reference.get("capability_id") != CAPABILITY or reference.get("contract_fingerprint") != CONTRACT_FINGERPRINT:
        raise Refusal("profile_fingerprint_mismatch")
    sources = source_manifest(archive_path, provenance)
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
            p["capability_id"] == CAPABILITY for p in entry["capabilities"]):
        raise Refusal("predecessor_capabilities_mismatch")
    return entry, reference, sources


def images():
    return LANE_HOST.images(upstream=False)


@diagnostics.checked("helper_setup", "helper_contract")
async def exercise(run_dir, entry, core_image, run_id, core_memory):
    # Import only after the closed disposable endpoints have been set.
    import real_ha_contract_tests as harness
    from security_metadata_contract_acceptance import run
    with diagnostics.check("auth", "bootstrap"):
        token = await harness.bootstrap_disposable_admin()
    configured = replace(harness.settings(token), audit_path=str(run_dir / "audit.jsonl"))
    if configured.upstream_dashboard_mcp_url:
        raise Refusal("upstream_not_expected")
    from ha_mcp_engineering.clients import HomeAssistantRestClient
    with diagnostics.check("core_boot", "runtime_ready"):
        observed = await harness.wait_for_runtime_ready(HomeAssistantRestClient(configured))
    with diagnostics.check("image_verify", "running_identity"):
        if observed.get("version") != VERSION:
            raise Refusal("running_core_version_mismatch")
        data = inspect_owned("container", CORE, run_id)
        if data is None or data["Image"] != cached_image(core_image) or data["Config"]["Image"] != core_image:
            raise Refusal("running_image_mismatch")
        if data.get("HostConfig", {}).get("Memory") != host.memory_bytes(core_memory):
            raise Refusal("running_container_memory_mismatch")
        identities = {CORE: {"image": core_image, "config_digest": data["Image"],
                             "memory_bytes": data["HostConfig"]["Memory"]}}
    return {**await run(configured, entry, run_dir, mode="local"), "images": identities}


@diagnostics.checked("admission", "preflight")
def _main(argv=None):
    if not __debug__:
        raise Refusal("assertions_required")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("local",), required=True)
    parser.add_argument("--pull", choices=("never",), required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--core-memory", default=CORE_MEMORY, help="Core Docker limit (default: 2g)")
    parser.add_argument("--fixture-memory", default=host.FIXTURE_MEMORY, help="cleanup Docker limit (default: 128m)")
    args = parser.parse_args(argv)
    limits = {"core": args.core_memory, "fixture": args.fixture_memory}
    for memory in limits.values():
        host.memory_bytes(memory)
    if os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("CORE_EVIDENCE_CONTEXT"):
        raise Refusal("local_only_closed_lane")
    revision = LANE_HOST.clean_revision()
    entry, reference, sources = inputs(args.journal, args.source_archive)
    core_image, fixture_image = images()
    for image in (core_image, fixture_image):
        cached_image(image)
    LANE_HOST.require_unclaimed((CORE,), NETWORK)
    run_dir = LANE_HOST.claim(args.run_dir)
    admission = LANE_HOST.admit(run_dir, RECEIPT, revision, (CORE,), NETWORK)
    if admission is None:
        return 1
    run_id = uuid4().hex
    owned = LANE_HOST.configuration(run_dir, CORE, CONFIG)
    os.environ.update(CORE_EVIDENCE_HOST_MODE="local", RUNNER_TEMP=str(run_dir),
        HA_CONTRACT_LANE=LANE, HA_FIXTURE_WRITER_IMAGE=fixture_image,
        HA_CONTRACT_CONTAINER=CORE, HA_CONTRACT_NETWORK=NETWORK,
        REAL_HA_URL="http://127.0.0.1:18123", REAL_HA_EXPECTED_VERSION=VERSION)
    for name in ("REAL_HA_UPSTREAM_IMAGE", "REAL_HA_UPSTREAM_VERSION",
                 "REAL_HA_UPSTREAM_CONTAINER", "REAL_HA_UPSTREAM_PORT"):
        os.environ.pop(name, None)
    result = {"model": "core-security-metadata-read-local-evidence-v1", "group": "security_metadata",
              "result": "WITHHELD", "started_utc": utc(), "source_commit": revision,
              "journal_sha256": JOURNAL_SHA256, "core_version": VERSION,
              "profile_reference": reference, "contract_fingerprint": reference["contract_fingerprint"],
              **sources, "host_admission": admission, "container_memory": limits,
              "network_posture": NETWORK_POSTURE, "mode": "local", "pull": "never", "cleanup": {"clean": False}}
    def body():
        LANE_HOST.create_bridge(NETWORK, run_id)
        LANE_HOST.start_core(CORE, NETWORK, run_id, args.core_memory, owned, core_image,
                             "--publish", "127.0.0.1:18123:8123")
        verify_port_mapping(CORE, 8123, 18123, run_id)
        async def bounded():
            async with asyncio.timeout(1800):
                return await exercise(run_dir, entry, core_image, run_id, args.core_memory)
        result.update(asyncio.run(bounded()))
        if result.get("profile") != reference:
            raise Refusal("receipt_profile_mismatch")
    return LANE_HOST.execute(result, RECEIPT, body, run_dir=run_dir, run_id=run_id, owned=owned, network=NETWORK,
                             remove=(CORE,), containers=(CORE,), fixture_memory=args.fixture_memory)


def main(argv=None):
    with diagnostics.lane("security_metadata"):
        return _main(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"result": "WITHHELD", "failure_type": type(exc).__name__,
                          "failure": diagnostics.failure(exc)}))
        raise SystemExit(1) from None
