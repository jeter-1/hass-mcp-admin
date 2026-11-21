"""Committed, cached-image helper_value lane for the seq-16 -> seq-17 review.

No Docker pulls, GitHub access, publication or production authority. Only cached
exact images on a dedicated bridge with egress; host ports bind loopback. A new
run directory and clean committed checkout are mandatory. Failure is WITHHELD.
The production journal is verified read-only; synthetic test keys stay in memory.
"""
import argparse
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import core_evidence_host as host
import core_lane_host
import core_local_resources as resources
import lane_failure_diagnostics as diagnostics
from core_lane_bindings import REVIEWED_CORE_BINDINGS
from core_release_evidence import canonical, read_json

# signal, subprocess, resources and canonical stay reachable here: the lane's tests patch them through it.
VERSION = "2026.11.0"
JOURNAL_SHA256 = "8df3665c8c27222541f99682dcc3b65b8f65d590b726f2f5bb0e74b55e71b82c"
LANE = "ha-2026-11-0-helper"
CORE = "beta25-real-ha-" + LANE
UPSTREAM = "beta25-real-ha-upstream-" + LANE
NETWORK = "beta-rc2-contract-" + LANE
NETWORK_POSTURE = {"driver": "bridge", "host_bind_address": "127.0.0.1", "egress_allowed": True}
LABEL = "org.hamcp.helper-evidence-run"
PORTS = (18123, 18086)
CORE_MEMORY = "2g"
UPSTREAM_MEMORY = "768m"
CONFIG = core_lane_host.CONFIGS["helper_value"]
RECEIPT = "helper_value.json"


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
def inputs(journal_path, archive_path):
    from core_registry_one_tap import load_public_key
    from prepare_core_release_registry import parse_journal, require_known_contracts
    provenance = read_json(core_lane_host.LANE_PROVENANCE)
    if (sha256(journal_path) != JOURNAL_SHA256
            or sha256(archive_path) != provenance["source_archive_sha256"]):
        raise Refusal("input_hash_mismatch")
    public = load_public_key((ROOT / "docs/evidence/hamcp222/core-public-key.txt").read_bytes(), pinned=True)
    current = parse_journal(Path(journal_path).read_bytes(), public)
    if current.accepted.sequence != 16:
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
    if len(entry["capabilities"]) != 31 or any(
            p["capability_id"] == "core.typed_helper_value_operation" for p in entry["capabilities"]):
        raise Refusal("predecessor_capabilities_mismatch")
    return entry


def images():
    return LANE_HOST.images(upstream=True)


@diagnostics.checked("helper_setup", "helper_contract")
async def exercise(run_dir, entry, core_image, upstream_image, run_id, core_memory, upstream_memory):
    # Import only after the closed disposable endpoints have been set.
    import real_ha_contract_tests as harness
    from helper_value_contract_acceptance import run
    with diagnostics.check("auth", "bootstrap"):
        token = await harness.bootstrap_disposable_admin()
    configured = replace(harness.settings(token), audit_path=str(run_dir / "audit.jsonl"))
    from ha_mcp_engineering.clients import HomeAssistantRestClient
    with diagnostics.check("core_boot", "runtime_ready"):
        observed = await harness.wait_for_runtime_ready(HomeAssistantRestClient(configured))
    with diagnostics.check("image_verify", "running_identity"):
        if observed.get("version") != VERSION:
            raise Refusal("running_core_version_mismatch")
    with diagnostics.check("container_start", "upstream_start"):
        await harness._start_exact_upstream(token, pull_policy="never", labels=(LABEL + "=" + run_id,),
                                            environment_directory=run_dir, memory=upstream_memory,
                                            verify_started=lambda: verify_port_mapping(UPSTREAM, 18086, 18086, run_id))
    identities = {}
    with diagnostics.check("image_verify", "running_identity"):
        for name, image, memory in ((CORE, core_image, core_memory), (UPSTREAM, upstream_image, upstream_memory)):
            data = inspect_owned("container", name, run_id)
            if data is None or data["Image"] != cached_image(image) or data["Config"]["Image"] != image:
                raise Refusal("running_image_mismatch")
            if data.get("HostConfig", {}).get("Memory") != host.memory_bytes(memory):
                raise Refusal("running_container_memory_mismatch")
            identities[name] = {"image": image, "config_digest": data["Image"],
                                "memory_bytes": data["HostConfig"]["Memory"]}
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
    parser.add_argument("--upstream-memory", default=UPSTREAM_MEMORY, help="upstream Docker limit (default: 768m)")
    parser.add_argument("--fixture-memory", default=host.FIXTURE_MEMORY, help="cleanup Docker limit (default: 128m)")
    args = parser.parse_args(argv)
    limits = {"core": args.core_memory, "upstream": args.upstream_memory, "fixture": args.fixture_memory}
    for memory in limits.values():
        host.memory_bytes(memory)
    if os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("CORE_EVIDENCE_CONTEXT"):
        raise Refusal("local_only_closed_lane")
    revision = LANE_HOST.clean_revision()
    entry = inputs(args.journal, args.source_archive)
    core_image, upstream_image, fixture_image = images()
    for image in (core_image, upstream_image, fixture_image):
        cached_image(image)
    LANE_HOST.require_unclaimed((CORE, UPSTREAM), NETWORK)
    run_dir = LANE_HOST.claim(args.run_dir)
    admission = LANE_HOST.admit(run_dir, RECEIPT, revision, (CORE, UPSTREAM), NETWORK)
    if admission is None:
        return 1
    run_id = uuid4().hex
    owned = LANE_HOST.configuration(run_dir, CORE, CONFIG)
    os.environ.update(CORE_EVIDENCE_HOST_MODE="local", RUNNER_TEMP=str(run_dir),
        HA_CONTRACT_LANE=LANE, HA_FIXTURE_WRITER_IMAGE=fixture_image,
        HA_CONTRACT_CONTAINER=CORE, HA_CONTRACT_NETWORK=NETWORK,
        REAL_HA_URL="http://127.0.0.1:18123", REAL_HA_EXPECTED_VERSION=VERSION,
        REAL_HA_UPSTREAM_IMAGE=upstream_image, REAL_HA_UPSTREAM_VERSION=REVIEWED_CORE_BINDINGS[VERSION].child_consumer,
        REAL_HA_UPSTREAM_CONTAINER=UPSTREAM, REAL_HA_UPSTREAM_PORT="18086")
    result = {"model": "core-helper-value-local-evidence-v1", "group": "helper_value",
              "result": "WITHHELD", "started_utc": utc(), "source_commit": revision,
              "journal_sha256": JOURNAL_SHA256, "manifest_sha256": sha256(ROOT / "ci/core-contract-read-files.json"),
              "host_admission": admission, "container_memory": limits, "network_posture": NETWORK_POSTURE,
              "mode": "local", "pull": "never", "cleanup": {"clean": False}}
    def body():
        LANE_HOST.create_bridge(NETWORK, run_id)
        LANE_HOST.start_core(CORE, NETWORK, run_id, args.core_memory, owned, core_image,
                             "--publish", "127.0.0.1:18123:8123")
        verify_port_mapping(CORE, 8123, 18123, run_id)
        async def bounded():
            async with asyncio.timeout(1800):
                return await exercise(run_dir, entry, core_image, upstream_image, run_id,
                                      args.core_memory, args.upstream_memory)
        result.update(asyncio.run(bounded()))
    return LANE_HOST.execute(result, RECEIPT, body, run_dir=run_dir, run_id=run_id, owned=owned, network=NETWORK,
                             remove=(UPSTREAM, CORE), containers=(CORE, UPSTREAM),
                             fixture_memory=args.fixture_memory)


def main(argv=None):
    with diagnostics.lane("helper_value"):
        return _main(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"result": "WITHHELD", "failure_type": type(exc).__name__,
                          "failure": diagnostics.failure(exc)}))
        raise SystemExit(1) from None
