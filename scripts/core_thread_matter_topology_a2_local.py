"""Committed, cached-image A2 success lane for HAMCP-231 on HAMCP-297's Matter Server stub.

Same closed posture as core_thread_matter_topology_local (A1): cached exact
images only, no pulls, no GitHub, no publication or production authority, a
clean committed checkout, a new run directory under .artifacts/, failure
WITHHELD and cleanup read back. Unlike A1 the lane network is an ``--internal``
bridge: no container has egress and no port is published; the host-side
harness reaches Core and the stub control endpoint by container address on
that bridge. Core's lack of egress is probed and recorded.

The stub is the HAMCP-297 test-only stub (draft PR #428), never copied or
forked into this branch. Its committed directory is the default; until #428
merges, ``--stub-dir`` names an extracted copy of the pinned commit under
.artifacts/. Either way the exact file set and every SHA-256 in
docs/evidence/hamcp231/lane/a2-stub-expectations.json must match before any
container starts, and the stub is mounted read-only. Matter is set up through
the disposable Core's own config flow pointing at the stub.
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
CORE_PORT = 8123
STUB_MEMORY = "256m"
RECEIPT = "thread_matter_topology_a2.json"
NETWORK_POSTURE = {"driver": "bridge", "internal": True, "egress_allowed": False, "published_ports": False,
                   "host_access": "container_address_on_internal_bridge",
                   "egress_required_by_capability": False}
EGRESS_PROBE = ("import socket\ntry:\n socket.create_connection(('1.1.1.1', 443), 3).close()\n"
                " print('reachable')\nexcept OSError as exc:\n print('blocked', exc.errno)")


def expectations():
    from thread_matter_topology_a2_acceptance import ExpectationsPending, ready_expectations
    try:
        return ready_expectations(read_json(EXPECTATIONS))
    except ExpectationsPending as exc:
        raise Refusal(str(exc)) from None


def verify_stub(directory, stub):
    """The exact pinned file set; every file's SHA-256; nothing extra."""
    directory = Path(directory).absolute()
    committed = (ROOT / stub["committed_dir"]).absolute()
    if directory.resolve() != directory or (directory != committed and ROOT / ".artifacts" not in directory.parents):
        raise Refusal("a2_stub_directory_not_committed_or_task_artifact")
    if not directory.is_dir():
        raise Refusal("a2_stub_missing")
    present = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file() or p.is_symlink()}
    if present != set(stub["files"]):
        raise Refusal("a2_stub_file_set_mismatch")
    for name, digest in sorted(stub["files"].items()):
        path = directory / name
        if path.is_symlink() or sha256(path) != digest:
            raise Refusal("a2_stub_hash_mismatch")
    return {"directory": directory.relative_to(ROOT).as_posix(),
            "source": "committed" if directory == committed else "extracted_pinned_commit",
            "files_verified": len(present)}


def address(name, run_id):
    data = inspect_owned("container", name, run_id) or {}
    networks = data.get("NetworkSettings", {}).get("Networks") or {}
    if set(networks) != {NETWORK} or any((data.get("NetworkSettings", {}).get("Ports") or {}).values()):
        raise Refusal("lane_container_network_unsafe")
    value = networks[NETWORK].get("IPAddress")
    if not value:
        raise Refusal("lane_container_address_missing")
    return value


class CoreAdmin:
    """Harness-only REST to the disposable Core and the stub control endpoint; never through Engineering."""

    def __init__(self, ha_url, token, stub_url):
        self.ha_url, self.token, self.stub_url = ha_url, token, stub_url

    async def _call(self, method, url, body=None, headers=None):
        import aiohttp
        async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.request(method, url, json=body, headers=headers, allow_redirects=False) as reply:
                assert reply.status < 400, (method, reply.status)
                return await reply.json()

    async def core(self, method, path, body=None):
        return await self._call(method, self.ha_url + path, body, {"Authorization": f"Bearer {self.token}"})

    async def stub_control(self, body):
        return await self._call("POST", self.stub_url + "/_stub/control", body)

    async def stub_state(self):
        return await self._call("GET", self.stub_url + "/_stub/control")

    async def matter_entry(self):
        entries = await self.core("GET", "/api/config/config_entries/entry?domain=matter")
        assert len(entries) == 1
        return entries[0]

    async def wait_loaded(self):
        for _ in range(60):
            if (await self.matter_entry()).get("state") == "loaded":
                return
            await asyncio.sleep(2)
        raise Refusal("matter_not_loaded_from_stub")

    async def configure_matter(self):
        """The disposable Core's own config flow to the stub, then wait for Matter loaded."""
        flow = await self.core("POST", "/api/config/config_entries/flow", {"handler": "matter"})
        assert flow.get("step_id") == "manual"
        done = await self.core("POST", f"/api/config/config_entries/flow/{flow['flow_id']}",
                               {"url": f"ws://{STUB_ALIAS}:5580/ws"})
        assert done.get("type") == "create_entry"
        await self.wait_loaded()
        return await self.core("GET", "/api/config")

    async def reload_matter(self):
        entry = await self.matter_entry()
        await self.core("POST", f"/api/config/config_entries/entry/{entry['entry_id']}/reload")
        await self.wait_loaded()


@diagnostics.checked("helper_setup", "helper_contract")
async def exercise(run_dir, entry, spec, core_image, run_id, core_memory, record, ha_url, stub_url):
    import real_ha_contract_tests as harness
    if harness.HA_URL != ha_url:
        raise Refusal("harness_core_address_mismatch")
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
    admin = CoreAdmin(configured.ha_url, token, stub_url)
    with diagnostics.check("helper_setup", "provider_setup"):
        observed = await admin.configure_matter()
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
                       read_devices=devices, stub_control=admin.stub_control, stub_state=admin.stub_state,
                       reload_matter=admin.reload_matter)
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
    parser.add_argument("--stub-dir", type=Path, default=None,
                        help="extracted copy of the pinned HAMCP-297 commit under .artifacts/ until #428 merges")
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
    stub = spec["stub"]
    stub_dir = (args.stub_dir or ROOT / stub["committed_dir"]).absolute()
    stub_identity = verify_stub(stub_dir, stub)
    entry, reference, sources = a1.inputs(args.journal, args.source_archive)
    core_image, fixture_image = a1.images()
    for image in (core_image, fixture_image):
        a1.cached_image(image)
    for kind, names in (("container", (CORE, STUB)), ("network", (NETWORK,))):
        for name in names:
            if command("docker", kind, "inspect", name, check=False).returncode == 0:
                raise Refusal("disposable_name_already_exists")
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
        HA_CONTRACT_CONTAINER=CORE, HA_CONTRACT_NETWORK=NETWORK, REAL_HA_EXPECTED_VERSION=a1.VERSION)
    os.environ.pop("REAL_HA_URL", None)
    for name in ("REAL_HA_UPSTREAM_IMAGE", "REAL_HA_UPSTREAM_VERSION",
                 "REAL_HA_UPSTREAM_CONTAINER", "REAL_HA_UPSTREAM_PORT"):
        os.environ.pop(name, None)
    result = {"model": "core-thread-matter-topology-a2-local-evidence-v1", "group": "thread_matter_topology",
              "scenario": "a2_success", "result": "WITHHELD", "started_utc": utc(), "source_commit": revision,
              "journal_sha256": a1.JOURNAL_SHA256, "core_version": a1.VERSION,
              "profile_reference": reference, "contract_fingerprint": reference["contract_fingerprint"],
              "expectations_sha256": sha256(EXPECTATIONS), "stub_identity": stub_identity,
              "stub_dependency": f"HAMCP-297 draft PR #{stub['source_pr']} at {stub['source_commit']}",
              **sources, "host_admission": admission,
              "container_memory": {"core": args.core_memory, "stub": STUB_MEMORY, "fixture": args.fixture_memory},
              "network_posture": NETWORK_POSTURE, "mode": "local", "pull": "never", "cleanup": {"clean": False}}

    def interrupted(_signum, _frame):
        raise Refusal("lane_interrupted")
    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        with diagnostics.check("container_start", "network_create"):
            command("docker", "network", "create", "--internal", "--driver", "bridge",
                    "--label", a1.LABEL + "=" + run_id, NETWORK)
            network = inspect_owned("network", NETWORK, run_id)
            if network is None or network.get("Driver") != "bridge" or network.get("Internal") is not True:
                raise Refusal("lane_requires_internal_bridge")
        with diagnostics.check("container_start", "stub_start"):
            command("docker", "run", "--detach", "--pull", "never", "--name", STUB,
                    "--label", a1.LABEL + "=" + run_id, "--network", NETWORK, "--network-alias", STUB_ALIAS,
                    "--memory", STUB_MEMORY, "--security-opt", "no-new-privileges",
                    "--user", f"{os.getuid()}:{os.getgid()}",
                    "--mount", f"type=bind,source={stub_dir},target=/lane,readonly",
                    "--mount", f"type=bind,source={record_dir},target=/out",
                    "--entrypoint", "python3", core_image, "-I", "/lane/stub.py", "--nodes", "/lane/nodes",
                    "--out", "/out/" + stub["record_name"], "--port", str(stub["port"]))
        with diagnostics.check("container_start", "core_start"):
            command("docker", "run", "--detach", "--pull", "never", "--name", CORE,
                    "--label", a1.LABEL + "=" + run_id, "--network", NETWORK, "--memory", args.core_memory,
                    "--security-opt", "no-new-privileges",
                    "--mount", f"type=bind,source={owned},target=/config", core_image)
        with diagnostics.check("container_start", "network_posture"):
            ha_url = f"http://{address(CORE, run_id)}:{CORE_PORT}"
            stub_url = f"http://{address(STUB, run_id)}:{stub['port']}"
            os.environ["REAL_HA_URL"] = ha_url
            probe = command("docker", "exec", CORE, "python3", "-I", "-c", EGRESS_PROBE).stdout.decode().strip()
            if not probe.startswith("blocked"):
                raise Refusal("lane_core_egress_reachable")
            result["egress_probe"] = {"from": "core", "target": "1.1.1.1:443", "outcome": probe}

        async def bounded():
            async with asyncio.timeout(1800):
                return await exercise(run_dir, entry, spec, core_image, run_id, args.core_memory, record,
                                      ha_url, stub_url)
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
