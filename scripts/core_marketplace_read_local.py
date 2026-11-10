"""Committed, cached-image marketplace_read lane for the seq-17 -> seq-18 review.

No Docker pulls, GitHub access, publication or production authority. Only cached
exact images on a dedicated bridge with egress (Core's Marketplace fetches its
public store); the Core host port binds loopback. No ha-mcp container: the
capability is Engineering-native. A new run directory and clean committed
checkout are mandatory. Failure is WITHHELD. The production journal is verified
read-only; synthetic test keys stay in memory.
"""
import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tarfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import core_evidence_host as host
import core_local_resources as resources
import lane_failure_diagnostics as diagnostics
from core_lane_bindings import REVIEWED_CORE_BINDINGS
from core_release_evidence import canonical, read_json

VERSION = "2026.11.0"
JOURNAL_SHA256 = "3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d"
CAPABILITY = "core.marketplace_read"
# The reviewed compiled profile this lane proves; a changed profile must be re-proven.
CONTRACT_FINGERPRINT = "sha256:9393b59801d97786e3ac8cf2b44c1aec121198568bfb017cabaa5691acc0cfeb"
MANIFEST = ROOT / "ci/core-contract-read-files.json"
SOURCE_HASHES = ROOT / "docs/evidence/hamcp290/lane/core-marketplace-source-hashes.json"
LANE = "ha-2026-11-0-marketplace"
CORE = "beta25-real-ha-" + LANE
NETWORK = "beta-rc2-contract-" + LANE
NETWORK_POSTURE = {"driver": "bridge", "host_bind_address": "127.0.0.1", "egress_allowed": True}
LABEL = "org.hamcp.marketplace-evidence-run"
PORTS = (18123,)
CORE_MEMORY = "2g"
RECEIPT = "marketplace_read.json"
CONFIG = """default_config:
homeassistant:
  name: HAMCP disposable marketplace evidence
  time_zone: UTC
http:
  server_port: 8123
automation: []
script: {}
input_boolean: {}
input_number: {}
"""


class Refusal(Exception):
    pass


def utc():
    return datetime.now(timezone.utc).isoformat()


def command(*args, check=True):
    host.require_memory(args)
    result = subprocess.run(args, cwd=ROOT, capture_output=True, timeout=120)
    if check and result.returncode:
        # Never include Docker output, synthetic credentials or arbitrary upstream text.
        raise Refusal("host_command_failed")
    return result


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def compiled_reference():
    sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
    from marketplace_read_contract_acceptance import reference
    return reference()


@diagnostics.checked("image_verify", "journal_inputs")
def source_manifest(archive_path, provenance):
    """Bind the manifest profile, committed source hashes and exact archive."""
    profile = read_json(MANIFEST).get("profiles", {}).get(CAPABILITY)
    if not profile or not SOURCE_HASHES.is_file():
        raise Refusal("marketplace_manifest_missing")
    record = read_json(SOURCE_HASHES)
    rows = {row["path"]: row["sha256"] for row in record["files"]}
    if (record["core_version"], record["source_commit"], record["source_archive_sha256"]) != (
            VERSION, provenance["source_commit"], provenance["source_archive_sha256"]):
        raise Refusal("marketplace_source_provenance_mismatch")
    if [row["path"] for row in record["files"]] != profile or len(rows) != len(profile):
        raise Refusal("marketplace_manifest_mismatch")
    prefix, observed = "core-" + provenance["source_commit"] + "/", {}
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar:
            name = member.name[len(prefix):] if member.name.startswith(prefix) else None
            if name in rows and member.isreg():
                observed[name] = hashlib.sha256(tar.extractfile(member).read()).hexdigest()
    if observed != rows:
        raise Refusal("marketplace_source_hash_mismatch")
    return {"manifest_sha256": sha256(MANIFEST), "source_hashes_sha256": sha256(SOURCE_HASHES),
            "profile_paths": len(profile)}


@diagnostics.checked("image_verify", "journal_inputs")
def inputs(journal_path, archive_path):
    from core_registry_one_tap import load_public_key
    from prepare_core_release_registry import parse_journal, require_known_contracts
    provenance = read_json(ROOT / "tests/fixtures/core_2026_11_0_lane_provenance.json")
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
    binding = REVIEWED_CORE_BINDINGS[VERSION]
    return (f"ghcr.io/home-assistant/home-assistant:{VERSION}@{binding.image_index_digest}",
            host.FIXTURE_IMAGE)


@diagnostics.checked("image_verify", "cached_image_digest")
def cached_image(image):
    result = command("docker", "image", "inspect", image)
    value, = json.loads(result.stdout)
    if image.rsplit("@", 1)[1] not in {d.rsplit("@", 1)[1] for d in value.get("RepoDigests", [])}:
        raise Refusal("cached_image_digest_mismatch")
    return value["Id"]


def require_free_ports():
    for port in PORTS:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))


def inspect_owned(kind, name, run_id):
    value = command("docker", kind, "inspect", name, check=False)
    if value.returncode:
        # A daemon error is not proof of absence. Enumerate and require success.
        listed = command("docker", kind, "ls", *( ["-a"] if kind == "container" else []),
                         "--format", "{{.Names}}" if kind == "container" else "{{.Name}}")
        if name in listed.stdout.decode().splitlines():
            raise Refusal("owned_resource_inspection_failed")
        return None
    data, = json.loads(value.stdout)
    labels = data.get("Config", {}).get("Labels") if kind == "container" else data.get("Labels")
    if (labels or {}).get(LABEL) != run_id:
        raise Refusal("resource_ownership_mismatch")
    return data


@diagnostics.checked("container_start", "port_mapping")
def verify_port_mapping(name, container_port, host_port, run_id):
    """Require actual Docker bindings, not merely requested HostConfig bindings."""
    data = inspect_owned("container", name, run_id)
    settings = (data or {}).get("NetworkSettings", {})
    ports = settings.get("Ports") or {}
    expected = [{"HostIp": "127.0.0.1", "HostPort": str(host_port)}]
    if (set(settings.get("Networks") or {}) != {NETWORK}
            or ports.get(f"{container_port}/tcp") != expected
            or any(bindings for port, bindings in ports.items() if port != f"{container_port}/tcp")):
        raise Refusal("Local container loopback port mapping is missing or unsafe")


@diagnostics.checked("helper_setup", "helper_contract")
async def exercise(run_dir, entry, core_image, run_id, core_memory):
    # Import only after the closed disposable endpoints have been set.
    import real_ha_contract_tests as harness
    from marketplace_read_contract_acceptance import run
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
    return {**await run(configured, entry, run_dir), "images": identities}


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
    # No ignored inputs may supply code/config; only committed files plus hash-bound inputs.
    if command("git", "status", "--porcelain", "--untracked-files=normal").stdout.strip():
        raise Refusal("clean_committed_checkout_required")
    revision = command("git", "rev-parse", "HEAD").stdout.decode().strip()
    entry, reference, sources = inputs(args.journal, args.source_archive)
    core_image, fixture_image = images()
    for image in (core_image, fixture_image):
        cached_image(image)
    # Refuse names or occupied ports before claiming anything; never remove prior runs.
    for kind, names in (("container", (CORE,)), ("network", (NETWORK,))):
        for name in names:
            if command("docker", kind, "inspect", name, check=False).returncode == 0:
                raise Refusal("disposable_name_already_exists")
    require_free_ports()
    run_dir = args.run_dir.absolute()
    if run_dir.resolve() != run_dir or ROOT / ".artifacts" not in run_dir.parents:
        raise Refusal("new_task_artifact_directory_required")
    run_dir.mkdir(mode=0o700)  # Exclusive: never overwrite evidence or reuse a prior fixture.
    try:
        with diagnostics.check("admission", "host_resources"):
            admission = resources.wait_for_admission(run_dir / "resource-admission.json")
    except resources.Refusal as exc:
        result = {"result": "WITHHELD", "reason": str(exc), "source_commit": revision,
                  "network_posture": NETWORK_POSTURE,
                  "execution_started": False, "cleanup": {"clean": True, "resources_created": False},
                  "failure": diagnostics.failure(exc)}
        (run_dir / RECEIPT).write_bytes(canonical(result))
        print(json.dumps(result))
        return 1
    # Recheck identity/names/ports after a potentially hour-long wait.
    if command("git", "rev-parse", "HEAD").stdout.decode().strip() != revision or command(
            "git", "status", "--porcelain", "--untracked-files=normal").stdout.strip():
        raise Refusal("checkout_changed_during_admission")
    for kind, names in (("container", (CORE,)), ("network", (NETWORK,))):
        for name in names:
            if command("docker", kind, "inspect", name, check=False).returncode == 0:
                raise Refusal("disposable_name_already_exists")
    require_free_ports()
    run_id = uuid4().hex
    owned = run_dir / CORE
    owned.mkdir(mode=0o700)
    (owned / "configuration.yaml").write_text(CONFIG)
    os.environ.update(CORE_EVIDENCE_HOST_MODE="local", RUNNER_TEMP=str(run_dir),
        HA_CONTRACT_LANE=LANE, HA_FIXTURE_WRITER_IMAGE=fixture_image,
        HA_CONTRACT_CONTAINER=CORE, HA_CONTRACT_NETWORK=NETWORK,
        REAL_HA_URL="http://127.0.0.1:18123", REAL_HA_EXPECTED_VERSION=VERSION)
    for name in ("REAL_HA_UPSTREAM_IMAGE", "REAL_HA_UPSTREAM_VERSION",
                 "REAL_HA_UPSTREAM_CONTAINER", "REAL_HA_UPSTREAM_PORT"):
        os.environ.pop(name, None)
    result = {"model": "core-marketplace-read-local-evidence-v1", "group": "marketplace_read",
              "result": "WITHHELD", "started_utc": utc(), "source_commit": revision,
              "journal_sha256": JOURNAL_SHA256, "core_version": VERSION,
              "profile_reference": reference, "contract_fingerprint": reference["contract_fingerprint"],
              **sources, "host_admission": admission, "container_memory": limits,
              "network_posture": NETWORK_POSTURE, "mode": "local", "pull": "never", "cleanup": {"clean": False}}
    def interrupted(_signum, _frame):
        raise Refusal("lane_interrupted")
    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        with diagnostics.check("container_start", "network_create"):
            command("docker", "network", "create", "--driver", "bridge", "--label", LABEL + "=" + run_id, NETWORK)
            network = inspect_owned("network", NETWORK, run_id)
            if network is None or network.get("Driver") != "bridge" or network.get("Internal") is not False:
                raise Refusal("Local lane requires a dedicated non-internal bridge")
        with diagnostics.check("container_start", "core_start"):
            command("docker", "run", "--detach", "--pull", "never", "--name", CORE,
                    "--label", LABEL + "=" + run_id, "--network", NETWORK, "--memory", args.core_memory,
                    "--publish", "127.0.0.1:18123:8123", "--security-opt", "no-new-privileges",
                    "--mount", f"type=bind,source={owned},target=/config", core_image)
        verify_port_mapping(CORE, 8123, 18123, run_id)
        async def bounded():
            async with asyncio.timeout(1800):
                return await exercise(run_dir, entry, core_image, run_id, args.core_memory)
        result.update(asyncio.run(bounded()))
        if result.get("profile") != reference:
            raise Refusal("receipt_profile_mismatch")
    except BaseException as exc:
        result.update(result="WITHHELD", failure_type=type(exc).__name__,
                      failure=diagnostics.failure(exc, extra=lambda stream: local_failure_logs(stream, run_id)))
    finally:
        try:
            with diagnostics.check("cleanup", "container_remove"):
                if inspect_owned("container", CORE, run_id) is not None:
                    command("docker", "rm", "-f", CORE)
            with diagnostics.check("cleanup", "network_remove"):
                if inspect_owned("network", NETWORK, run_id) is not None:
                    command("docker", "network", "rm", NETWORK)
            with diagnostics.check("cleanup", "configuration_remove"):
                host.local_remove(argparse.Namespace(directory=str(owned), memory=args.fixture_memory, owner=run_id), os.environ, subprocess.run)
            with diagnostics.check("cleanup", "cleanup_absence"):
                assert not owned.exists()
                assert inspect_owned("container", CORE, run_id) is None
                assert inspect_owned("network", NETWORK, run_id) is None
            result["cleanup"] = {"clean": True, "containers_absent": True, "network_absent": True,
                                 "configuration_absent": True}
        except BaseException as exc:
            cleanup_failure = diagnostics.failure(exc)
            result.setdefault("failure", cleanup_failure)
            result.update(result="WITHHELD", cleanup={"clean": False, "failure_type": type(exc).__name__,
                                                    "failure": cleanup_failure})
        result["ended_utc"] = utc()
        (run_dir / RECEIPT).write_bytes(canonical(result))
        signal.signal(signal.SIGTERM, previous_handler)
    print(json.dumps({key: result[key] for key in ("result", "started_utc", "ended_utc", "cleanup")}))
    return 0 if result["result"] == "PASS" and result["cleanup"]["clean"] else 1


def local_failure_logs(stream, run_id):
    """Only owned disposable logs; never stdout, receipts or upload artifacts."""
    data = inspect_owned("container", CORE, run_id)
    if data is not None:
        stream.write("\nOwned container " + CORE + "\n")
        stream.write(json.dumps({key: data.get("State", {}).get(key)
                                for key in ("Status", "ExitCode", "OOMKilled")}))
        output = subprocess.run(["docker", "logs", "--tail", "200", CORE],
                                capture_output=True, timeout=10)
        stream.write("\n" + (output.stdout + output.stderr)[-262144:].decode("utf-8", errors="replace"))


def main(argv=None):
    with diagnostics.lane("marketplace_read"):
        return _main(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"result": "WITHHELD", "failure_type": type(exc).__name__,
                          "failure": diagnostics.failure(exc)}))
        raise SystemExit(1) from None
