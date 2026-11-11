"""Committed, cached-image device_configuration_entity lane (HAMCP-366).

The signing lane for ``core.device_configuration_entity_operation`` (design
HAMCP-297 section 9.2): real Core 2026.11.0 ``matter`` entities from a
lane-owned, test-only Matter Server stub, exercised by the signed build only.

No Docker pulls, GitHub access, publication or production authority. Only cached
exact images on a dedicated bridge with egress; host ports bind loopback. A new
run directory and clean committed checkout are mandatory. Failure is WITHHELD.
The production journal is verified read-only; synthetic test keys stay in memory.
The run refuses before any container starts unless the compiled profile equals
the reference recorded here for signing, and unless the stub, its lane entry and
the three node dumps match their committed provenance and the Core source archive.
"""
import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
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
PREDECESSOR_SEQUENCE, PREDECESSOR_CAPABILITIES = 17, 32
GROUP = "device_configuration_entity"
RECEIPT = GROUP + ".json"
# The profile reference recorded for signing. A build whose compiled profile
# differs in any of these values is not the signed build and does not run.
PROFILE = {
    "capability_id": "core.device_configuration_entity_operation",
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
STUB_ASSET = ROOT / "tests/fixtures/hamcp297_matter_server_stub"
LANE_ASSET = ROOT / "tests/fixtures/hamcp366_device_configuration_lane"
FIXTURE_COMPONENT = "hamcp366_refusal_fixture"
CORE_PROVENANCE = ROOT / "tests/fixtures/core_2026_11_0_lane_provenance.json"
DUMP_DIRECTORY = "tests/components/matter/fixtures/nodes/"
CONFIG = """default_config:
homeassistant:
  name: HAMCP disposable device configuration evidence
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


@diagnostics.checked("image_verify", "journal_inputs")
def inputs(journal_path, archive_path):
    from core_registry_one_tap import load_public_key
    from prepare_core_release_registry import parse_journal, require_known_contracts
    provenance = read_json(CORE_PROVENANCE)
    if (sha256(journal_path) != JOURNAL_SHA256
            or sha256(archive_path) != provenance["source_archive_sha256"]):
        raise Refusal("input_hash_mismatch")
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
    return entry


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
def stub_assets(archive_path):
    """Hash-bind the stub, its lane entry, the refusal fixture and the three node dumps.

    The stub files must equal their committed provenance, and each dump must
    equal the member of the hash-pinned Core source archive it was copied from.
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
    prefix = "core-" + source["source_commit"] + "/" + DUMP_DIRECTORY
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
async def exercise(run_dir, entry, core_image, upstream_image, run_id, memory, stub_hashes):
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
        if observed.get("version") != VERSION:
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
    result = await acceptance.run(configured, entry, run_dir, stub_url=control, stub_hashes={
        key: stub_hashes[key] for key in ("stub_sha256", "entry_sha256", "dump_hashes")})
    return {**result, "images": identities}


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
    parser.add_argument("--stub-memory", default=STUB_MEMORY, help="Matter stub Docker limit (default: 256m)")
    parser.add_argument("--fixture-memory", default=host.FIXTURE_MEMORY, help="cleanup Docker limit (default: 128m)")
    args = parser.parse_args(argv)
    limits = {"core": args.core_memory, "upstream": args.upstream_memory, "stub": args.stub_memory,
              "fixture": args.fixture_memory}
    for memory in limits.values():
        host.memory_bytes(memory)
    if os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("CORE_EVIDENCE_CONTEXT"):
        raise Refusal("local_only_closed_lane")
    # No ignored inputs may supply code/config; only committed files plus hash-bound inputs.
    if command("git", "status", "--porcelain", "--untracked-files=normal").stdout.strip():
        raise Refusal("clean_committed_checkout_required")
    revision = command("git", "rev-parse", "HEAD").stdout.decode().strip()
    # The signed build only: checked before the journal, any image or any container.
    profile = loaded_profile()
    entry = inputs(args.journal, args.source_archive)
    stub_hashes = stub_assets(args.source_archive)
    core_image, upstream_image, fixture_image = images()
    for image in (core_image, upstream_image, fixture_image):
        cached_image(image)
    # Refuse names or occupied ports before claiming anything; never remove prior runs.
    for kind, names in (("container", (CORE, UPSTREAM, STUB)), ("network", (NETWORK,))):
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
    for kind, names in (("container", (CORE, UPSTREAM, STUB)), ("network", (NETWORK,))):
        for name in names:
            if command("docker", kind, "inspect", name, check=False).returncode == 0:
                raise Refusal("disposable_name_already_exists")
    require_free_ports()
    run_id = uuid4().hex
    owned = run_dir / CORE
    owned.mkdir(mode=0o700)
    (owned / "configuration.yaml").write_text(CONFIG)
    # Refusal fixture (test-only custom integration); committed, hashed above.
    shutil.copytree(LANE_ASSET / "custom_components" / FIXTURE_COMPONENT,
                    owned / "custom_components" / FIXTURE_COMPONENT,
                    ignore=shutil.ignore_patterns("__pycache__"))
    stub_output = run_dir / "matter-stub"
    stub_output.mkdir(mode=0o700)
    os.environ.update(CORE_EVIDENCE_HOST_MODE="local", RUNNER_TEMP=str(run_dir),
        HA_CONTRACT_LANE=LANE, HA_FIXTURE_WRITER_IMAGE=fixture_image,
        HA_CONTRACT_CONTAINER=CORE, HA_CONTRACT_NETWORK=NETWORK,
        REAL_HA_URL=f"http://127.0.0.1:{CORE_PORT}", REAL_HA_EXPECTED_VERSION=VERSION,
        REAL_HA_UPSTREAM_IMAGE=upstream_image, REAL_HA_UPSTREAM_VERSION="8.5.0",
        REAL_HA_UPSTREAM_CONTAINER=UPSTREAM, REAL_HA_UPSTREAM_PORT=str(UPSTREAM_PORT))
    result = {"model": "core-device-configuration-local-evidence-v1", "group": GROUP,
              "result": "WITHHELD", "started_utc": utc(), "source_commit": revision,
              "core_version": VERSION, "signing_profile": profile, "stub_assets": stub_hashes,
              "journal_sha256": JOURNAL_SHA256, "manifest_sha256": sha256(ROOT / "ci/core-contract-read-files.json"),
              "host_admission": admission, "container_memory": limits, "network_posture": NETWORK_POSTURE,
              "mode": "local", "pull": "never", "cleanup": {"clean": False}}
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
                    "--publish", f"127.0.0.1:{CORE_PORT}:8123", "--security-opt", "no-new-privileges",
                    "--mount", f"type=bind,source={owned},target=/config", core_image)
        verify_port_mapping(CORE, 8123, CORE_PORT, run_id)
        with diagnostics.check("container_start", "core_start"):
            start_stub(core_image, run_id, args.stub_memory, stub_output, stub_hashes["dump_hashes"])
        verify_port_mapping(STUB, STUB_CONTAINER_PORT, STUB_PORT, run_id)
        async def bounded():
            async with asyncio.timeout(1800):
                return await exercise(run_dir, entry, core_image, upstream_image, run_id, limits,
                                      stub_hashes)
        outcome = asyncio.run(bounded())
        # A receipt for any other profile, or for other stub bytes, is not this lane's receipt.
        if outcome.get("profile") != profile or outcome.get("stub") != {
                key: stub_hashes[key] for key in ("stub_sha256", "entry_sha256", "dump_hashes")}:
            raise Refusal("receipt_profile_mismatch")
        result.update(outcome)
    except BaseException as exc:
        result.update(result="WITHHELD", failure_type=type(exc).__name__,
                      failure=diagnostics.failure(exc, extra=lambda stream: local_failure_logs(stream, run_id)))
    finally:
        try:
            with diagnostics.check("cleanup", "container_remove"):
                for name in (UPSTREAM, STUB, CORE):
                    if inspect_owned("container", name, run_id) is not None:
                        command("docker", "rm", "-f", name)
            with diagnostics.check("cleanup", "network_remove"):
                if inspect_owned("network", NETWORK, run_id) is not None:
                    command("docker", "network", "rm", NETWORK)
            with diagnostics.check("cleanup", "configuration_remove"):
                host.local_remove(argparse.Namespace(directory=str(owned), memory=args.fixture_memory, owner=run_id), os.environ, subprocess.run)
            with diagnostics.check("cleanup", "cleanup_absence"):
                assert not owned.exists()
                assert all(inspect_owned("container", name, run_id) is None
                           for name in (CORE, UPSTREAM, STUB))
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
    for name in (CORE, UPSTREAM, STUB):
        data = inspect_owned("container", name, run_id)
        if data is not None:
            stream.write("\nOwned container " + name + "\n")
            stream.write(json.dumps({key: data.get("State", {}).get(key)
                                    for key in ("Status", "ExitCode", "OOMKilled")}))
            output = subprocess.run(["docker", "logs", "--tail", "200", name],
                                    capture_output=True, timeout=10)
            stream.write("\n" + (output.stdout + output.stderr)[-262144:].decode("utf-8", errors="replace"))


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
