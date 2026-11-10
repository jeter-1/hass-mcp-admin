"""Committed, cached-image A2 success lane for HAMCP-231; run pending HAMCP-297's stub.

Same closed posture as core_thread_matter_topology_local (A1): cached exact
images only, no pulls, no GitHub, no publication or production authority, a
clean committed checkout, a new run directory under .artifacts/, loopback-only
Core port, failure WITHHELD and cleanup read back. Adds one Matter Server stub
container: the HAMCP-297 committed test-only stub, run from its committed path
and verified by SHA-256 (never copied or forked), on the lane network with no
published port. Matter is set up through the disposable Core's own config flow
pointing at the stub.

The stub identity and the canary literals are parameters from
docs/evidence/hamcp231/lane/a2-stub-expectations.json. While that file is not
``ready`` the lane refuses ``a2_stub_pending`` before any container starts, and
A2 signing evidence stays incomplete.
"""
import argparse
import asyncio
from dataclasses import replace
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
import core_thread_matter_topology_local as a1
from core_thread_matter_topology_local import Refusal, command, host, inspect_owned, resources, sha256, utc
import lane_failure_diagnostics as diagnostics
from core_release_evidence import canonical, read_json

EXPECTATIONS = ROOT / "docs/evidence/hamcp231/lane/a2-stub-expectations.json"
LANE = "ha-2026-11-0-thread-matter-a2"
CORE = "beta25-real-ha-" + LANE
STUB = CORE + "-matter-stub"
STUB_ALIAS = "matter-stub"
NETWORK = "beta-rc2-contract-" + LANE
PORT = 18125  # distinct from the A1 (18124) and HAMCP-290 lanes
STUB_MEMORY = "256m"
RECEIPT = "thread_matter_topology_a2.json"
NETWORK_POSTURE = {**a1.NETWORK_POSTURE, "stub_published_ports": False}


def expectations():
    from thread_matter_topology_a2_acceptance import ExpectationsPending, ready_expectations
    try:
        spec = ready_expectations(read_json(EXPECTATIONS))
    except ExpectationsPending as exc:
        raise Refusal(str(exc)) from None
    stub = spec["stub"]
    for key, digest in (("path", "sha256"), ("nodes_path", "nodes_sha256")):
        path = (ROOT / stub[key]).resolve()
        if ROOT not in path.parents or not path.exists():
            raise Refusal("a2_stub_missing")
        if (sha256(path) if path.is_file() else tree_sha256(path)) != stub[digest]:
            raise Refusal("a2_stub_hash_mismatch")
    return spec


def tree_sha256(directory):
    """Stable digest of a committed fixture directory: sorted relative path and file hash lines."""
    import hashlib
    lines = sorted(f"{p.relative_to(directory).as_posix()} {sha256(p)}"
                   for p in Path(directory).rglob("*") if p.is_file())
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def require_free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", PORT))


async def configure_matter(configured, token):
    """Harness-only: the disposable Core's own config flow to the stub, then wait for Matter loaded.

    Never through Engineering. Returns Core's final /api/config.
    """
    import aiohttp
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=60)) as session:
        async def call(method, path, body=None):
            async with session.request(method, configured.ha_url + path, json=body, headers=headers) as reply:
                assert reply.status < 400
                return await reply.json()
        flow = await call("POST", "/api/config/config_entries/flow", {"handler": "matter"})
        assert flow.get("step_id") == "manual"
        done = await call("POST", f"/api/config/config_entries/flow/{flow['flow_id']}",
                          {"url": f"ws://{STUB_ALIAS}:5580/ws"})
        assert done.get("type") == "create_entry"
        for _ in range(60):
            observed = await call("GET", "/api/config")
            if "matter" in set(observed.get("components", [])):
                return observed
            await asyncio.sleep(5)
    raise Refusal("matter_not_loaded_from_stub")


@diagnostics.checked("helper_setup", "helper_contract")
async def exercise(run_dir, entry, spec, core_image, run_id, core_memory, record):
    import real_ha_contract_tests as harness
    from thread_matter_topology_a2_acceptance import run
    from thread_matter_topology_contract_acceptance import RawCore
    with diagnostics.check("auth", "bootstrap"):
        token = await harness.bootstrap_disposable_admin()
    configured = replace(harness.settings(token), audit_path=str(run_dir / "audit.jsonl"))
    if configured.upstream_dashboard_mcp_url:
        raise Refusal("upstream_not_expected")
    from ha_mcp_engineering.clients import HomeAssistantRestClient
    with diagnostics.check("core_boot", "runtime_ready"):
        observed = await harness.wait_for_runtime_ready(HomeAssistantRestClient(configured))
    with diagnostics.check("helper_setup", "provider_setup"):
        observed = await configure_matter(configured, token)
    with diagnostics.check("image_verify", "running_identity"):
        if observed.get("version") != a1.VERSION or "matter" not in set(observed.get("components", [])):
            raise Refusal("running_integration_set_mismatch")
        identities = {}
        for name, image, memory in ((CORE, core_image, core_memory), (STUB, core_image, STUB_MEMORY)):
            data = inspect_owned("container", name, run_id)
            if data is None or data["Image"] != a1.cached_image(image) or data["Config"]["Image"] != image:
                raise Refusal("running_image_mismatch")
            if data.get("HostConfig", {}).get("Memory") != host.memory_bytes(memory):
                raise Refusal("running_container_memory_mismatch")
            identities[name] = {"image": image, "config_digest": data["Image"],
                                "memory_bytes": data["HostConfig"]["Memory"]}
    raw = RawCore(configured)

    async def devices():
        reply = await raw.command({"type": "config/device_registry/list"})
        assert reply["success"] is True
        return reply["result"]
    result = await run(configured, entry, run_dir, expectations=spec,
                       read_stub_record=lambda: record.read_text().splitlines() if record.exists() else [],
                       read_devices=devices)
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
    parser.add_argument("--core-memory", default=a1.CORE_MEMORY)
    parser.add_argument("--fixture-memory", default=host.FIXTURE_MEMORY)
    args = parser.parse_args(argv)
    for memory in (args.core_memory, args.fixture_memory, STUB_MEMORY):
        host.memory_bytes(memory)
    if os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("CORE_EVIDENCE_CONTEXT"):
        raise Refusal("local_only_closed_lane")
    if command("git", "status", "--porcelain", "--untracked-files=normal").stdout.strip():
        raise Refusal("clean_committed_checkout_required")
    revision = command("git", "rev-parse", "HEAD").stdout.decode().strip()
    # The stub contract first: pending refuses before any input, image or container.
    spec = expectations()
    entry, reference, sources = a1.inputs(args.journal, args.source_archive)
    core_image, fixture_image = a1.images()
    for image in (core_image, fixture_image):
        a1.cached_image(image)
    for kind, names in (("container", (CORE, STUB)), ("network", (NETWORK,))):
        for name in names:
            if command("docker", kind, "inspect", name, check=False).returncode == 0:
                raise Refusal("disposable_name_already_exists")
    require_free_port()
    run_dir = args.run_dir.absolute()
    if run_dir.resolve() != run_dir or ROOT / ".artifacts" not in run_dir.parents:
        raise Refusal("new_task_artifact_directory_required")
    run_dir.mkdir(mode=0o700)
    admission = resources.wait_for_admission(run_dir / "resource-admission.json")
    if command("git", "rev-parse", "HEAD").stdout.decode().strip() != revision:
        raise Refusal("checkout_changed_during_admission")
    run_id = uuid4().hex
    owned, record_dir = run_dir / CORE, run_dir / "stub-record"
    owned.mkdir(mode=0o700)
    record_dir.mkdir(mode=0o700)
    (owned / "configuration.yaml").write_text(a1.CONFIG)
    record = record_dir / spec["stub"]["record_name"]
    os.environ.update(CORE_EVIDENCE_HOST_MODE="local", RUNNER_TEMP=str(run_dir),
        HA_CONTRACT_LANE=LANE, HA_FIXTURE_WRITER_IMAGE=fixture_image,
        HA_CONTRACT_CONTAINER=CORE, HA_CONTRACT_NETWORK=NETWORK,
        REAL_HA_URL=f"http://127.0.0.1:{PORT}", REAL_HA_EXPECTED_VERSION=a1.VERSION)
    for name in ("REAL_HA_UPSTREAM_IMAGE", "REAL_HA_UPSTREAM_VERSION",
                 "REAL_HA_UPSTREAM_CONTAINER", "REAL_HA_UPSTREAM_PORT"):
        os.environ.pop(name, None)
    result = {"model": "core-thread-matter-topology-a2-local-evidence-v1", "group": "thread_matter_topology",
              "scenario": "a2_success", "result": "WITHHELD", "started_utc": utc(), "source_commit": revision,
              "journal_sha256": a1.JOURNAL_SHA256, "core_version": a1.VERSION,
              "profile_reference": reference, "contract_fingerprint": reference["contract_fingerprint"],
              "expectations_sha256": sha256(EXPECTATIONS), **sources, "host_admission": admission,
              "container_memory": {"core": args.core_memory, "stub": STUB_MEMORY, "fixture": args.fixture_memory},
              "network_posture": NETWORK_POSTURE, "mode": "local", "pull": "never", "cleanup": {"clean": False}}

    def interrupted(_signum, _frame):
        raise Refusal("lane_interrupted")
    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    stub = spec["stub"]
    try:
        with diagnostics.check("container_start", "network_create"):
            command("docker", "network", "create", "--driver", "bridge", "--label", a1.LABEL + "=" + run_id, NETWORK)
        with diagnostics.check("container_start", "stub_start"):
            command("docker", "run", "--detach", "--pull", "never", "--name", STUB,
                    "--label", a1.LABEL + "=" + run_id, "--network", NETWORK, "--network-alias", STUB_ALIAS,
                    "--memory", STUB_MEMORY, "--security-opt", "no-new-privileges",
                    "--user", f"{os.getuid()}:{os.getgid()}",
                    "--mount", f"type=bind,source={ROOT / stub['path']},target=/lane/stub.py,readonly",
                    "--mount", f"type=bind,source={ROOT / stub['nodes_path']},target=/nodes,readonly",
                    "--mount", f"type=bind,source={record_dir},target=/out",
                    "--entrypoint", "python3", core_image, "-I", "/lane/stub.py", "--nodes", "/nodes",
                    "--out", "/out/" + stub["record_name"], "--port", str(stub["port"]))
        with diagnostics.check("container_start", "core_start"):
            command("docker", "run", "--detach", "--pull", "never", "--name", CORE,
                    "--label", a1.LABEL + "=" + run_id, "--network", NETWORK, "--memory", args.core_memory,
                    "--publish", f"127.0.0.1:{PORT}:8123", "--security-opt", "no-new-privileges",
                    "--mount", f"type=bind,source={owned},target=/config", core_image)
        a1.verify_port_mapping(CORE, 8123, PORT, run_id)
        stub_ports = (inspect_owned("container", STUB, run_id) or {}).get("NetworkSettings", {}).get("Ports") or {}
        if any(stub_ports.values()):
            raise Refusal("stub_port_published")

        async def bounded():
            async with asyncio.timeout(1800):
                return await exercise(run_dir, entry, spec, core_image, run_id, args.core_memory, record)
        result.update(asyncio.run(bounded()))
        if result.get("profile") != reference:
            raise Refusal("receipt_profile_mismatch")
    except BaseException as exc:
        result.update(result="WITHHELD", failure_type=type(exc).__name__, failure=diagnostics.failure(exc))
    finally:
        try:
            for name in (CORE, STUB):
                with diagnostics.check("cleanup", "container_remove"):
                    if inspect_owned("container", name, run_id) is not None:
                        command("docker", "rm", "-f", name)
            with diagnostics.check("cleanup", "network_remove"):
                if inspect_owned("network", NETWORK, run_id) is not None:
                    command("docker", "network", "rm", NETWORK)
            with diagnostics.check("cleanup", "configuration_remove"):
                host.local_remove(argparse.Namespace(directory=str(owned), memory=args.fixture_memory, owner=run_id),
                                  os.environ, subprocess.run)
            with diagnostics.check("cleanup", "cleanup_absence"):
                assert not owned.exists()
                for name in (CORE, STUB):
                    assert inspect_owned("container", name, run_id) is None
                assert inspect_owned("network", NETWORK, run_id) is None
            result["cleanup"] = {"clean": True, "containers_absent": True, "network_absent": True,
                                 "configuration_absent": True}
        except BaseException as exc:
            result.update(result="WITHHELD", cleanup={"clean": False, "failure_type": type(exc).__name__,
                                                    "failure": diagnostics.failure(exc)})
        result["ended_utc"] = utc()
        (run_dir / RECEIPT).write_bytes(canonical(result))
        signal.signal(signal.SIGTERM, previous_handler)
    print(json.dumps({key: result[key] for key in ("result", "started_utc", "ended_utc", "cleanup")}))
    return 0 if result["result"] == "PASS" and result["cleanup"]["clean"] else 1


def main(argv=None):
    with diagnostics.lane("thread_matter_topology"):
        return _main(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"result": "WITHHELD", "failure_type": type(exc).__name__,
                          "failure": diagnostics.failure(exc)}))
        raise SystemExit(1) from None
