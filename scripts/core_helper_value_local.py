"""Committed, offline-host helper_value lane for the seq-16 -> seq-17 review.

No Docker pulls, GitHub access, publication or production authority. Only cached
exact images on a new internal Docker network; host ports bind loopback. A new
run directory and clean committed checkout are mandatory. Failure is WITHHELD.
The production journal is verified read-only; synthetic test keys stay in memory.
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
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import core_evidence_host as host
import core_local_resources as resources
import lane_failure_diagnostics as diagnostics
from core_lane_bindings import REVIEWED_CORE_BINDINGS
from core_release_evidence import canonical, read_json

VERSION = "2026.11.0"
JOURNAL_SHA256 = "8df3665c8c27222541f99682dcc3b65b8f65d590b726f2f5bb0e74b55e71b82c"
LANE = "ha-2026-11-0-helper"
CORE = "beta25-real-ha-" + LANE
UPSTREAM = "beta25-real-ha-upstream-" + LANE
NETWORK = "beta-rc2-contract-" + LANE
LABEL = "org.hamcp.helper-evidence-run"
PORTS = (18123, 18086)
CORE_MEMORY = "2g"
UPSTREAM_MEMORY = "768m"
CONFIG = """default_config:
homeassistant:
  name: HAMCP disposable helper evidence
  time_zone: UTC
http:
  server_port: 8123
automation: []
script: {}
input_boolean:
  hamcp_seq17:
    initial: false
input_number:
  hamcp_seq17:
    min: 10
    max: 30
    step: 0.5
    initial: 20
input_select:
  hamcp_seq17:
    options: [Home, Away, Night]
    initial: Home
input_text:
  hamcp_seq17:
    min: 1
    max: 8
    pattern: '[a-z]+'
    initial: abc
input_datetime:
  hamcp_seq17:
    has_date: true
    has_time: true
    initial: '2026-11-01 01:02:03'
input_button:
  hamcp_seq17: {}
counter:
  hamcp_seq17:
    initial: 2
    step: 1
    minimum: 0
    maximum: 6
timer:
  hamcp_seq17:
    duration: '00:05:00'
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


@diagnostics.checked("image_verify", "journal_inputs")
def inputs(journal_path, archive_path):
    from core_registry_one_tap import load_public_key
    from prepare_core_release_registry import parse_journal, require_known_contracts
    provenance = read_json(ROOT / "tests/fixtures/core_2026_11_0_lane_provenance.json")
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
    import yaml
    binding = REVIEWED_CORE_BINDINGS[VERSION]
    window = yaml.safe_load((ROOT / "ci/support-window.yaml").read_text())
    upstream = next((item for item in window["ha_mcp"].values()
                     if isinstance(item, dict) and item.get("version") == binding.child_consumer), None)
    if upstream is None:
        raise Refusal("reviewed_upstream_not_in_window")
    return (f"ghcr.io/home-assistant/home-assistant:{VERSION}@{binding.image_index_digest}",
            f"ghcr.io/homeassistant-ai/ha-mcp:{binding.child_consumer}@{upstream['image_index_digest']}",
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
                                            environment_directory=run_dir, memory=upstream_memory)
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
    parser.add_argument("--upstream-memory", default=UPSTREAM_MEMORY, help="upstream Docker limit (default: 768m)")
    parser.add_argument("--fixture-memory", default=host.FIXTURE_MEMORY, help="cleanup Docker limit (default: 128m)")
    args = parser.parse_args(argv)
    limits = {"core": args.core_memory, "upstream": args.upstream_memory, "fixture": args.fixture_memory}
    for memory in limits.values():
        host.memory_bytes(memory)
    if os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("CORE_EVIDENCE_CONTEXT"):
        raise Refusal("local_only_closed_lane")
    # No ignored inputs may supply code/config; only committed files plus hash-bound inputs.
    if command("git", "status", "--porcelain", "--untracked-files=normal").stdout.strip():
        raise Refusal("clean_committed_checkout_required")
    revision = command("git", "rev-parse", "HEAD").stdout.decode().strip()
    entry = inputs(args.journal, args.source_archive)
    core_image, upstream_image, fixture_image = images()
    for image in (core_image, upstream_image, fixture_image):
        cached_image(image)
    # Refuse names or occupied ports before claiming anything; never remove prior runs.
    for kind, names in (("container", (CORE, UPSTREAM)), ("network", (NETWORK,))):
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
                  "execution_started": False, "cleanup": {"clean": True, "resources_created": False},
                  "failure": diagnostics.failure(exc)}
        (run_dir / "helper_value.json").write_bytes(canonical(result))
        print(json.dumps(result))
        return 1
    # Recheck identity/names/ports after a potentially hour-long wait.
    if command("git", "rev-parse", "HEAD").stdout.decode().strip() != revision or command(
            "git", "status", "--porcelain", "--untracked-files=normal").stdout.strip():
        raise Refusal("checkout_changed_during_admission")
    for kind, names in (("container", (CORE, UPSTREAM)), ("network", (NETWORK,))):
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
        REAL_HA_URL="http://127.0.0.1:18123", REAL_HA_EXPECTED_VERSION=VERSION,
        REAL_HA_UPSTREAM_IMAGE=upstream_image, REAL_HA_UPSTREAM_VERSION="8.5.0",
        REAL_HA_UPSTREAM_CONTAINER=UPSTREAM, REAL_HA_UPSTREAM_PORT="18086")
    result = {"model": "core-helper-value-local-evidence-v1", "group": "helper_value",
              "result": "WITHHELD", "started_utc": utc(), "source_commit": revision,
              "journal_sha256": JOURNAL_SHA256, "manifest_sha256": sha256(ROOT / "ci/core-contract-read-files.json"),
              "host_admission": admission, "container_memory": limits, "mode": "local", "pull": "never", "cleanup": {"clean": False}}
    def interrupted(_signum, _frame):
        raise Refusal("lane_interrupted")
    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        with diagnostics.check("container_start", "network_create"):
            command("docker", "network", "create", "--internal", "--label", LABEL + "=" + run_id, NETWORK)
        with diagnostics.check("container_start", "core_start"):
            command("docker", "run", "--detach", "--pull", "never", "--name", CORE,
                    "--label", LABEL + "=" + run_id, "--network", NETWORK, "--memory", args.core_memory,
                    "--publish", "127.0.0.1:18123:8123", "--security-opt", "no-new-privileges",
                    "--mount", f"type=bind,source={owned},target=/config", core_image)
        async def bounded():
            async with asyncio.timeout(1800):
                return await exercise(run_dir, entry, core_image, upstream_image, run_id,
                                      args.core_memory, args.upstream_memory)
        result.update(asyncio.run(bounded()))
    except BaseException as exc:
        result.update(result="WITHHELD", failure_type=type(exc).__name__,
                      failure=diagnostics.failure(exc, extra=lambda stream: local_failure_logs(stream, run_id)))
    finally:
        try:
            with diagnostics.check("cleanup", "container_remove"):
                for name in (UPSTREAM, CORE):
                    if inspect_owned("container", name, run_id) is not None:
                        command("docker", "rm", "-f", name)
            with diagnostics.check("cleanup", "network_remove"):
                if inspect_owned("network", NETWORK, run_id) is not None:
                    command("docker", "network", "rm", NETWORK)
            with diagnostics.check("cleanup", "configuration_remove"):
                host.local_remove(argparse.Namespace(directory=str(owned), memory=args.fixture_memory, owner=run_id), os.environ, subprocess.run)
            with diagnostics.check("cleanup", "cleanup_absence"):
                assert not owned.exists()
                assert all(inspect_owned("container", name, run_id) is None for name in (CORE, UPSTREAM))
                assert inspect_owned("network", NETWORK, run_id) is None
            result["cleanup"] = {"clean": True, "containers_absent": True, "network_absent": True,
                                 "configuration_absent": True}
        except BaseException as exc:
            cleanup_failure = diagnostics.failure(exc)
            result.setdefault("failure", cleanup_failure)
            result.update(result="WITHHELD", cleanup={"clean": False, "failure_type": type(exc).__name__,
                                                    "failure": cleanup_failure})
        result["ended_utc"] = utc()
        (run_dir / "helper_value.json").write_bytes(canonical(result))
        signal.signal(signal.SIGTERM, previous_handler)
    print(json.dumps({key: result[key] for key in ("result", "started_utc", "ended_utc", "cleanup")}))
    return 0 if result["result"] == "PASS" and result["cleanup"]["clean"] else 1


def local_failure_logs(stream, run_id):
    """Only owned disposable logs; never stdout, receipts or upload artifacts."""
    for name in (CORE, UPSTREAM):
        data = inspect_owned("container", name, run_id)
        if data is not None:
            stream.write("\nOwned container " + name + "\n")
            stream.write(json.dumps({key: data.get("State", {}).get(key)
                                    for key in ("Status", "ExitCode", "OOMKilled")}))
            output = subprocess.run(["docker", "logs", "--tail", "200", name],
                                    capture_output=True, timeout=10)
            stream.write("\n" + (output.stdout + output.stderr)[-262144:].decode("utf-8", errors="replace"))


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
