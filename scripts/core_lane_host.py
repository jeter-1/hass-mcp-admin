"""Shared host steps of the Core lanes, local and CI (HAMCP-443 D2/D5/D6).

One source of truth for the lanes' disposable Core configurations and fixture
directories, and for the host steps every harness shares: exact images, owned
names and free loopback ports, the claimed run directory, admission, the
dedicated bridge, the Core container, and teardown with absence checks.
Onboarding, readiness and lane-specific containers stay in the harness.

The harness's --mode argument chooses the mode; nothing here reads the
environment to choose one. CI mode refuses off Actions, without the
run's verified context (core_release_evidence.context, the check the
run_specialized groups use), on an anchor the lane has no reviewed data for
(its REVIEWED_ANCHORS), or on a checkout that is not the run's own commit. A Lane is bound to
its harness module and resolves every name through it at call time, so a
harness's own names stay its interface.
"""
import argparse
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time

import core_evidence_host as host
import core_local_resources as resources
import lane_failure_diagnostics as diagnostics
import core_release_evidence as release
from core_lane_bindings import REVIEWED_CORE_BINDINGS
from core_release_evidence import canonical

ROOT = Path(__file__).resolve().parents[1]
LANE_PROVENANCE = ROOT / "tests/fixtures/core_2026_11_0_lane_provenance.json"
MATTER_STUB = ROOT / "tests/fixtures/hamcp297_matter_server_stub"
DEVICE_LANE = ROOT / "tests/fixtures/hamcp366_device_configuration_lane"
CONFIGS = {
    'helper_value': """default_config:
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
""",
    'marketplace_read': """default_config:
homeassistant:
  name: HAMCP disposable marketplace evidence
  time_zone: UTC
http:
  server_port: 8123
automation: []
script: {}
input_boolean: {}
input_number: {}
""",
    'security_metadata': """default_config:
homeassistant:
  name: HAMCP disposable security metadata evidence
  time_zone: UTC
http:
  server_port: 8123
automation: []
script: {}
input_boolean: {}
input_number: {}
""",
    'thread_matter_topology': """default_config:
homeassistant:
  name: HAMCP disposable thread matter evidence
  time_zone: UTC
http:
  server_port: 8123
automation: []
script: {}
input_boolean: {}
input_number: {}
thread:
""",
    'device_configuration_entity': """default_config:
homeassistant:
  name: HAMCP disposable device configuration evidence
  time_zone: UTC
http:
  server_port: 8123
automation: []
script: {}
input_boolean: {}
input_number: {}
""",
    # The two yaml members exist only so a created scene has real entities to store.
    'created_object': """default_config:
homeassistant:
  name: HAMCP disposable created-object evidence
  time_zone: UTC
http:
  server_port: 8123
automation: !include automations.yaml
script: !include scripts.yaml
scene: !include scenes.yaml
input_boolean:
  hamcp_g4_lamp:
    name: HAMCP G4 lamp
  hamcp_g4_strip:
    name: HAMCP G4 strip
input_number: {}
""",
}
# Files a configuration includes, written empty next to it.
INCLUDES = {"created_object": ("automations.yaml", "scripts.yaml", "scenes.yaml")}
# Linux keeps a closed loopback port in TIME_WAIT for 60 s; the next lane waits at most this long (HAMCP-443 7c).
TIME_WAIT_SECONDS = 90
# CI mode (D6a): no lane container reaches the cloud metadata address. Forwarded traffic only, never the runner's own.
METADATA_RULE = ("DOCKER-USER", "--destination", "169.254.169.254/32", "--jump", "DROP")
# CI mode's D6a proof (HAMCP-443 part C): connects made from inside a lane container, as its own processes connect.
METADATA_PROBE = ("import socket\ntry:\n socket.create_connection(('169.254.169.254', 80), 3).close()\n"
                  " print('reachable')\nexcept OSError as exc:\n print('blocked', exc.errno)")
EGRESS_PROBE = ("import socket\ntry:\n socket.create_connection(('1.1.1.1', 443), 3).close()\n"
                " print('reachable')\nexcept OSError as exc:\n print('blocked', exc.errno)")
RUNTIME_TREE = "HEAD:hass_mcp_engineering_beta/ha_mcp_engineering"
# A digest pull of a Core image can outlast an ordinary host command.
PULL_TIMEOUT = 900


def lane_provenance(ctx):
    """The context anchor's committed lane provenance fixture: reviewed data only, never a runner input (D5).
    In main mode the release is the anchor, so the fixture must equal the context's provenance; in derived
    mode the release inherits the anchor's sources, so its own provenance differs by design (HAMCP-489)."""
    from core_lane_bindings import provenance_name
    anchor = ctx["anchor"]
    if anchor not in REVIEWED_CORE_BINDINGS:
        raise ValueError("lane_anchor_unreviewed")
    provenance = release.read_json(ROOT / "tests/fixtures" / provenance_name(anchor))
    if ctx["mode"] == "main" and canonical(provenance) != canonical(ctx["provenance"]):
        raise ValueError("lane_provenance_mismatch")
    return provenance


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def require_mode_arguments(parser, args):
    """--mode local pairs with --pull never and a journal; --mode ci with --pull digest and no journal,
    since its signed baseline comes from the run's context (D2)."""
    if ((args.mode, args.pull) not in (("local", "never"), ("ci", "digest"))
            or (args.journal is None) != (args.mode == "ci")):
        parser.error("--mode local takes --pull never and --journal; --mode ci takes --pull digest and no --journal")


def time_wait_only(port):
    """Whether a reusable bind succeeds: it passes the TIME_WAIT a reusable listener (docker-proxy) left,
    never a listener or another bound socket."""
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            return False
        return True


def listening(port):
    """Whether anything accepts a connection on the loopback port."""
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


class Lane:
    """The host steps of one harness, resolving its names (ROOT, LABEL, PORTS, Refusal...) at call time."""

    def __init__(self, module):
        self.module = module

    def command(self, *args, check=True, timeout=120):
        m = self.module
        host.require_memory(args)
        result = subprocess.run(args, cwd=m.ROOT, capture_output=True, timeout=timeout)
        if check and result.returncode:
            # Never include Docker output, synthetic credentials or arbitrary upstream text.
            raise m.Refusal("host_command_failed")
        return result

    def images(self, *, upstream, anchor=None):
        """The exact Core image, the reviewed ha-mcp child when the lane runs one, and the fixture writer.
        The binding is the lane's own VERSION's, or in CI mode the context's anchor's."""
        m = self.module
        binding = REVIEWED_CORE_BINDINGS[m.VERSION if anchor is None else anchor]
        core = f"ghcr.io/home-assistant/home-assistant:{m.VERSION}@{binding.image_index_digest}"
        if not upstream:
            return core, host.FIXTURE_IMAGE
        import yaml
        window = yaml.safe_load((m.ROOT / "ci/support-window.yaml").read_text())
        child = next((item for item in window["ha_mcp"].values()
                      if isinstance(item, dict) and item.get("version") == binding.child_consumer), None)
        if child is None:
            raise m.Refusal("reviewed_upstream_not_in_window")
        return (core, f"ghcr.io/homeassistant-ai/ha-mcp:{binding.child_consumer}@{child['image_index_digest']}",
                host.FIXTURE_IMAGE)

    @diagnostics.checked("image_verify", "cached_image_digest")
    def cached_image(self, image):
        m = self.module
        result = m.command("docker", "image", "inspect", image)
        value, = json.loads(result.stdout)
        if image.rsplit("@", 1)[1] not in {d.rsplit("@", 1)[1] for d in value.get("RepoDigests", [])}:
            raise m.Refusal("cached_image_digest_mismatch")
        return value["Id"]

    def require_free_ports(self, *, wait=TIME_WAIT_SECONDS, sleep=time.sleep, clock=time.monotonic):
        """Free loopback ports; a held port raises its EADDRINUSE as before. Only a port the previous lane
        left in TIME_WAIT gets a bounded wait (7c): a live listener, or any other holder, raises at once."""
        for port in self.module.PORTS:
            deadline = clock() + wait
            while True:
                try:
                    with socket.socket() as sock:
                        sock.bind(("127.0.0.1", port))
                    break
                except OSError as exc:
                    if (exc.errno != errno.EADDRINUSE or listening(port) or not time_wait_only(port)
                            or clock() >= deadline):
                        raise
                sleep(1)

    def inspect_owned(self, kind, name, run_id):
        m = self.module
        value = m.command("docker", kind, "inspect", name, check=False)
        if value.returncode:
            # A daemon error is not proof of absence. Enumerate and require success.
            listed = m.command("docker", kind, "ls", *( ["-a"] if kind == "container" else []),
                               "--format", "{{.Names}}" if kind == "container" else "{{.Name}}")
            if name in listed.stdout.decode().splitlines():
                raise m.Refusal("owned_resource_inspection_failed")
            return None
        data, = json.loads(value.stdout)
        labels = data.get("Config", {}).get("Labels") if kind == "container" else data.get("Labels")
        if (labels or {}).get(m.LABEL) != run_id:
            raise m.Refusal("resource_ownership_mismatch")
        return data

    @diagnostics.checked("container_start", "port_mapping")
    def verify_port_mapping(self, name, container_port, host_port, run_id, network=None):
        """Require actual Docker bindings, not merely requested HostConfig bindings."""
        m = self.module
        network = m.NETWORK if network is None else network
        data = m.inspect_owned("container", name, run_id)
        settings = (data or {}).get("NetworkSettings", {})
        ports = settings.get("Ports") or {}
        expected = [{"HostIp": "127.0.0.1", "HostPort": str(host_port)}]
        if (set(settings.get("Networks") or {}) != {network}
                or ports.get(f"{container_port}/tcp") != expected
                or any(bindings for port, bindings in ports.items() if port != f"{container_port}/tcp")):
            raise m.Refusal("Local container loopback port mapping is missing or unsafe")

    def clean_revision(self):
        """The HEAD commit of a clean committed checkout."""
        m = self.module
        # No ignored inputs may supply code/config; only committed files plus hash-bound inputs.
        if m.command("git", "status", "--porcelain", "--untracked-files=normal").stdout.strip():
            raise m.Refusal("clean_committed_checkout_required")
        return m.command("git", "rev-parse", "HEAD").stdout.decode().strip()

    def ci_context(self):
        """The run's verified context and its commit (D2): Actions only, this run's own checkout,
        and an anchor the lane has reviewed data for, since that data is keyed to the anchor (D5)."""
        m = self.module
        if os.environ.get("GITHUB_ACTIONS") != "true":
            raise m.Refusal("ci_mode_requires_actions")
        ctx = release.context()
        if ctx is None:
            raise m.Refusal("ci_context_required")
        if ctx["anchor"] not in m.REVIEWED_ANCHORS or ctx["anchor"] not in REVIEWED_CORE_BINDINGS:
            raise m.Refusal("lane_anchor_unreviewed")
        revision = self.clean_revision()
        if revision != ctx["run"]["GITHUB_SHA"]:
            raise m.Refusal("ci_revision_mismatch")
        return ctx, revision

    def ci_entry(self, ctx, archive_path):
        """The release's exact source archive and the signed entry its D3 proof starts from."""
        from core_lane_proof import authenticated_baseline, release_entry
        m = self.module
        if m.sha256(archive_path) != release.slot(ctx)["source_archive_sha256"]:
            raise m.Refusal("input_hash_mismatch")
        return release_entry(authenticated_baseline(ctx), ctx)

    def ci_images(self, ctx, *, upstream):
        """The release Core from the context's slot, then the anchor's reviewed child and the fixture
        writer, each pulled by digest and verified; the lane itself still runs ``--pull never``."""
        m = self.module
        images = (release.slot(ctx)["image"], *self.images(upstream=upstream, anchor=ctx["anchor"])[1:])
        for image in images:
            m.command("docker", "pull", image, timeout=PULL_TIMEOUT)
            m.cached_image(image)
        return images

    def block_metadata(self):
        """Drop forwarded traffic to the metadata address, then read the rule back (D6a)."""
        m = self.module
        rule = ("sudo", "-n", "iptables", "-w", "-C", *METADATA_RULE)
        if m.command(*rule, check=False).returncode:
            m.command("sudo", "-n", "iptables", "-w", "-I", *METADATA_RULE)
            m.command(*rule)

    def d6a_proof(self, result, containers, network, run_id, *, internal):
        """CI mode's D6a proof, from inside every lane container, kept in ``result["d6a"]`` before any
        check can refuse: each container sits on the lane's own bridge alone, never the host network,
        and cannot reach the metadata address; the bridge is internal exactly when the lane's is (A2),
        and on an internal bridge an outbound connect fails too. Any miss refuses the lane."""
        m = self.module
        proof = result["d6a"] = {"proved": False, "network": network, "internal": None, "containers": {}}
        data = m.inspect_owned("network", network, run_id)
        proof["internal"] = (data or {}).get("Internal")
        for name in containers:
            info = m.inspect_owned("container", name, run_id) or {}
            seen = proof["containers"][name] = {
                "network_mode": (info.get("HostConfig") or {}).get("NetworkMode"),
                "networks": sorted((info.get("NetworkSettings") or {}).get("Networks") or {})}
            for key, probe in (("metadata", METADATA_PROBE), *((("egress", EGRESS_PROBE),) if internal else ())):
                seen[key] = m.command("docker", "exec", name, "python3", "-I", "-c", probe).stdout.decode().strip()
        if (data is None or data.get("Driver") != "bridge" or proof["internal"] is not internal
                or any(seen["network_mode"] != network or seen["networks"] != [network]
                       or not all(seen[key].startswith("blocked") for key in ("metadata", "egress") if key in seen)
                       for seen in proof["containers"].values())):
            raise m.Refusal("d6a_proof_failed")
        proof["proved"] = True

    def ci_claim(self, path):
        """An exclusive run directory directly under the runner's own temporary directory."""
        m = self.module
        temp, run_dir = Path(os.environ.get("RUNNER_TEMP") or "relative"), path.absolute()
        # A relative RUNNER_TEMP never equals its resolve(); is_symlink() comes first, before resolve() meets a loop.
        if (temp.is_symlink() or temp.resolve() != temp
                or run_dir.resolve() != run_dir or run_dir.parent != temp):
            raise m.Refusal("runner_temp_run_directory_required")
        run_dir.mkdir(mode=0o700)  # Exclusive, as in local mode.
        return run_dir

    def ci_admission(self, path, inputs, *, upstream, containers, network):
        """CI mode's preflight in order, every step before any resource: the context, the harness's
        ``inputs(ctx)``, digest-pulled images, the metadata block, unclaimed names and ports, the run directory."""
        ctx, revision = self.ci_context()
        values = inputs(ctx)
        images = self.ci_images(ctx, upstream=upstream)
        self.block_metadata()
        self.require_unclaimed(containers, network)
        return ctx, revision, values, images, self.ci_claim(path)

    def ci_fields(self, ctx):
        """Receipt fields CI mode replaces: no journal or host admission, the release Core, the run's own tree."""
        return {"mode": "ci", "pull": "digest", "journal_sha256": None, "host_admission": None,
                "core_version": release.slot(ctx)["version"], "runtime_tree": self.runtime_tree()}

    def runtime_tree(self):
        """The Engineering runtime tree of the run's own checkout, never a pinned constant (HAMCP-443 7e)."""
        return self.module.command("git", "rev-parse", RUNTIME_TREE).stdout.decode().strip()

    def require_unclaimed(self, containers, network):
        m = self.module
        # Refuse names or occupied ports before claiming anything; never remove prior runs.
        for kind, names in (("container", tuple(containers)), ("network", (network,))):
            for name in names:
                if m.command("docker", kind, "inspect", name, check=False).returncode == 0:
                    raise m.Refusal("disposable_name_already_exists")
        m.require_free_ports()

    def claim(self, path):
        m = self.module
        run_dir = path.absolute()
        if run_dir.resolve() != run_dir or m.ROOT / ".artifacts" not in run_dir.parents:
            raise m.Refusal("new_task_artifact_directory_required")
        run_dir.mkdir(mode=0o700)  # Exclusive: never overwrite evidence or reuse a prior fixture.
        return run_dir

    def admit(self, run_dir, receipt, revision, containers, network):
        """The admission record, or None after writing the WITHHELD receipt of a refused admission."""
        m = self.module
        try:
            with diagnostics.check("admission", "host_resources"):
                admission = resources.wait_for_admission(run_dir / "resource-admission.json")
        except resources.Refusal as exc:
            result = {"result": "WITHHELD", "reason": str(exc), "source_commit": revision,
                      "network_posture": m.NETWORK_POSTURE,
                      "execution_started": False, "cleanup": {"clean": True, "resources_created": False},
                      "failure": diagnostics.failure(exc)}
            (run_dir / receipt).write_bytes(canonical(result))
            print(json.dumps(result))
            return None
        # Recheck identity/names/ports after a potentially hour-long wait.
        if m.command("git", "rev-parse", "HEAD").stdout.decode().strip() != revision or m.command(
                "git", "status", "--porcelain", "--untracked-files=normal").stdout.strip():
            raise m.Refusal("checkout_changed_during_admission")
        self.require_unclaimed(containers, network)
        return admission

    def configuration(self, run_dir, name, config, *, includes=(), components=()):
        """The Core's owned /config directory: its configuration, empty includes and copied components."""
        owned = run_dir / name
        owned.mkdir(mode=0o700)
        (owned / "configuration.yaml").write_text(config)
        for include in includes:
            (owned / include).write_text("")
        for component in components:
            # A committed test-only custom integration, hashed by the harness before it runs.
            shutil.copytree(component, owned / "custom_components" / Path(component).name,
                            ignore=shutil.ignore_patterns("__pycache__"))
        return owned

    def create_bridge(self, network, run_id):
        m = self.module
        with diagnostics.check("container_start", "network_create"):
            m.command("docker", "network", "create", "--driver", "bridge", "--label", m.LABEL + "=" + run_id, network)
            data = m.inspect_owned("network", network, run_id)
            if data is None or data.get("Driver") != "bridge" or data.get("Internal") is not False:
                raise m.Refusal("Local lane requires a dedicated non-internal bridge")
        return data

    def start_core(self, name, network, run_id, memory, owned, image, *options):
        """Run the Core; ``options`` (port publication, host entries) go after its memory limit."""
        m = self.module
        # No lane Core on the host network, in any mode (D6a); the bridge is the harness's own.
        if network == "host" or any(str(option).split("=", 1)[0] in ("--network", "--net") for option in options):
            raise m.Refusal("lane_core_host_network_refused")
        with diagnostics.check("container_start", "core_start"):
            m.command("docker", "run", "--detach", "--pull", "never", "--name", name,
                      "--label", m.LABEL + "=" + run_id, "--network", network, "--memory", memory,
                      *options, "--security-opt", "no-new-privileges",
                      "--mount", f"type=bind,source={owned},target=/config", image)

    def execute(self, result, receipt, body, *, run_dir, run_id, owned, network, remove, containers,
                fixture_memory, scratch=()):
        """Run ``body`` and tear down every owned resource; WITHHELD on any failure. Returns the exit code.

        ``remove`` is the removal order, ``containers`` the absence and log order, ``scratch`` the
        run's other owned directories, removed before the configuration. In CI mode a body that
        succeeded is followed by the D6a proof, while every container still runs; local mode has none.
        """
        m = self.module

        def interrupted(_signum, _frame):
            raise m.Refusal("lane_interrupted")
        previous_handler = signal.signal(signal.SIGTERM, interrupted)
        try:
            body()
            if result["mode"] == "ci":
                self.d6a_proof(result, containers, network, run_id, internal=False)
        except BaseException as exc:
            result.update(result="WITHHELD", failure_type=type(exc).__name__,
                          failure=diagnostics.failure(exc, extra=lambda stream: self.failure_logs(
                              stream, containers, run_id)))
        finally:
            try:
                with diagnostics.check("cleanup", "container_remove"):
                    for name in remove:
                        if m.inspect_owned("container", name, run_id) is not None:
                            m.command("docker", "rm", "-f", name)
                with diagnostics.check("cleanup", "network_remove"):
                    if m.inspect_owned("network", network, run_id) is not None:
                        m.command("docker", "network", "rm", network)
                with diagnostics.check("cleanup", "configuration_remove"):
                    for path in scratch:
                        shutil.rmtree(path)
                    host.local_remove(argparse.Namespace(directory=str(owned), memory=fixture_memory, owner=run_id),
                                      os.environ, subprocess.run)
                with diagnostics.check("cleanup", "cleanup_absence"):
                    assert not owned.exists() and not any(path.exists() for path in scratch)
                    assert all(m.inspect_owned("container", name, run_id) is None for name in containers)
                    assert m.inspect_owned("network", network, run_id) is None
                result["cleanup"] = {"clean": True, "containers_absent": True, "network_absent": True,
                                     "configuration_absent": True}
            except BaseException as exc:
                cleanup_failure = diagnostics.failure(exc)
                result.setdefault("failure", cleanup_failure)
                result.update(result="WITHHELD", cleanup={"clean": False, "failure_type": type(exc).__name__,
                                                        "failure": cleanup_failure})
            result["ended_utc"] = m.utc()
            (run_dir / receipt).write_bytes(canonical(result))
            signal.signal(signal.SIGTERM, previous_handler)
        print(json.dumps({key: result[key] for key in ("result", "started_utc", "ended_utc", "cleanup")}))
        return 0 if result["result"] == "PASS" and result["cleanup"]["clean"] else 1

    def failure_logs(self, stream, names, run_id):
        """Only owned disposable logs; never stdout, receipts or upload artifacts."""
        m = self.module
        for name in names:
            data = m.inspect_owned("container", name, run_id)
            if data is not None:
                stream.write("\nOwned container " + name + "\n")
                stream.write(json.dumps({key: data.get("State", {}).get(key)
                                        for key in ("Status", "ExitCode", "OOMKilled")}))
                output = subprocess.run(["docker", "logs", "--tail", "200", name],
                                        capture_output=True, timeout=10)
                stream.write("\n" + (output.stdout + output.stderr)[-262144:].decode("utf-8", errors="replace"))
