"""Local catalog capture of one pinned ha-mcp image (HAMCP-371, step S4).

Observations P1 (``initialize`` result), P2 (full ``tools/list``) and P7 (the
resource files inside the image), plus the reviewed catalog capture taken
twice and compared byte for byte.

Network posture: one network namespace with a loopback interface and nothing
else.  A holder container from the Engineering image is started with
``--network none`` and runs the repository's synthetic read-only Home
Assistant fixture; the ha-mcp peer joins that namespace with
``--network container:<holder>``; every client runs by ``docker exec`` inside
the holder.  No Docker network is created, no port is published and no route
leaves the namespace.  The lane verifies this on the running containers before
the peer is observed and refuses otherwise.

No Home Assistant Core is started here.  The peer's Home Assistant is the
synthetic fixture ``fake_ha_read_gateway_contract_server.py``; observations
against a disposable real Core belong to the later capture step.

No pull happens in ``selftest`` or ``run`` (``--pull never`` everywhere).  The
``pull`` command fetches one image by its index digest, anonymously, after the
committed provenance record has been checked.  A preflight refusal happens
before any container or run directory exists.  A failure after that point is
recorded as WITHHELD and is not a reason to run again for a pass.

Everything a container produces is untrusted data: it is written under the run
directory, parsed as JSON at most and never executed.  Standard library only.
"""

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import core_evidence_host as host  # noqa: E402

MODEL = "hamcp371-upstream-catalog-capture-local-v1"
UPSTREAM_REPOSITORY = "ghcr.io/homeassistant-ai/ha-mcp"
UPSTREAM_SOURCE = "https://github.com/homeassistant-ai/ha-mcp"
REGISTRY = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/upstream_release_registry.json"
REQUIREMENTS = ROOT / "hass_mcp_engineering_beta/requirements.txt"
# The candidate is bound by the committed provenance record, the baseline by
# its reviewed registry row.  The candidate's source commit is fixed here so a
# replaced record cannot move the lane to another source.
CANDIDATE_VERSION = "9.2.0"
CANDIDATE_SOURCE_COMMIT = "51e3aac788482a081807591cd5d58d07dc4e540b"
CANDIDATE_PROVENANCE = ROOT / "docs/evidence/hamcp371/s4/provenance-9.2.0.json"
BASELINE_VERSION = "9.1.2"
# Python runtime for the fixture and the clients; the code it runs is this
# checkout's scripts/ and hass_mcp_engineering_beta/, mounted read-only.
HELPER_IMAGE = ("ghcr.io/jeter-1/hass-mcp-engineering-beta"
                "@sha256:5801d42fd19bfe813cccfe0a81b218d9f3a8cabf6f12ee044a160c0603c83c47")
HELPER_LABELS = {
    "org.opencontainers.image.revision": "b614be43f8c56ae06fdec0ac0bcb479b9da39715",
    "org.opencontainers.image.source": "https://github.com/jeter-1/hass-mcp-admin",
    "io.hass-mcp.build.dirty": "false",
}
LANE_LABEL = "org.hamcp.lane"
# One compose-project value groups the lane's containers for host-wide job counting.
PROJECT_LABEL = "com.docker.compose.project"
PROJECT = "hamcp371-s4"
FIXTURE_PORT, PEER_PORT = 18123, 18086
FIXTURE_URL = f"http://127.0.0.1:{FIXTURE_PORT}"
ENDPOINT = f"http://127.0.0.1:{PEER_PORT}/mcp"
# Exactly the environment of the reviewed registry-update workflow.  The token
# is a synthetic value; the fixture is not a Home Assistant.
PEER_ENVIRONMENT = (
    ("HOMEASSISTANT_URL", FIXTURE_URL),
    ("HOMEASSISTANT_TOKEN", "synthetic-release-registry-token"),
    ("MCP_HOST", "127.0.0.1"),
    ("MCP_PORT", str(PEER_PORT)),
    ("MCP_SECRET_PATH", "/mcp"),
    ("HA_MCP_DISABLE_SETTINGS_UI", "true"),
)
PEER_COMMAND = "ha-mcp-web"
HOLDER_MEMORY, PEER_MEMORY, INSPECT_MEMORY = "384m", "768m", "64m"
MUTATION_COUNTERS = ("http_mutations", "websocket_mutations",
                     "operational_backup_creates", "approval_notification_calls")
RESOURCE_MARKER = "/ha_mcp/resources/"
MAX_RESOURCE_FILES, MAX_RESOURCE_BYTES = 4096, 64 * 1024 * 1024
LOG_BOUND = 1024 * 1024
MIN_MEMORY_GB, MAX_LOAD, MIN_ROOT_FREE_GB = 3.5, 14.0, 5.0
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
ENETUNREACH = 101

NET_PROBE = r"""import json,os,socket
names=sorted(os.listdir('/sys/class/net'))
routes=open('/proc/net/route').read().splitlines()[1:]
s=socket.socket(); s.settimeout(3)
try:
    s.connect(('192.0.2.1',9)); code=0
except OSError as exc:
    code=exc.errno
print(json.dumps({'interfaces':names,'ipv4_routes':len(routes),'egress_errno':code}))
"""
WAIT_HTTP = r"""import sys,time,urllib.error,urllib.request
url,limit,mode=sys.argv[1],float(sys.argv[2]),sys.argv[3]
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
end=time.monotonic()+limit
while True:
    try:
        opener.open(url,timeout=5).close(); break
    except urllib.error.HTTPError:
        if mode=='any': break
    except OSError: pass
    if time.monotonic()>end: raise SystemExit(1)
    time.sleep(1)
"""
FETCH = r"""import sys,urllib.request
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(sys.argv[1],timeout=5) as r:
    sys.stdout.buffer.write(r.read(65537))
"""
RUNTIME = r"""import importlib.metadata as m,json,sys
import aiohttp,mcp.client.streamable_http
print(json.dumps({'python':sys.version.split()[0],
 'mcp':m.version('mcp'),'aiohttp':m.version('aiohttp')}))
"""


class Refusal(Exception):
    """The lane refuses or withholds; the message is a fixed token."""


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def canonical(value):
    return (json.dumps(value, indent=1, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")


def sha256_bytes(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def command(*args, check=True, timeout=120, env=None):
    host.require_memory(args)
    result = subprocess.run(list(args), cwd=ROOT, capture_output=True, timeout=timeout, env=env)
    if check and result.returncode:
        # Docker output and upstream text never reach an exception message.
        raise Refusal("host_command_failed")
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def binding(version, *, registry=REGISTRY, provenance=CANDIDATE_PROVENANCE):
    """Return the exact image and expected labels for one bound version."""
    if version == BASELINE_VERSION:
        rows = [row for row in read_json(registry)["releases"] if row.get("version") == version]
        if len(rows) != 1 or rows[0].get("approval_status") != "reviewed":
            raise Refusal("baseline_registry_row_missing")
        row = rows[0]
        bound = {"index_digest": row["image_index_digest"], "revision": row["image_revision"],
                 "source_commit": row["source_commit"], "bound_by": "reviewed registry row",
                 "committed_capture_sha256": row["capture_sha256"]}
    elif version == CANDIDATE_VERSION:
        if not Path(provenance).is_file():
            raise Refusal("candidate_provenance_record_missing")
        record = read_json(provenance)
        if (record.get("schema") != "hamcp.upstream_image_provenance.v1"
                or record.get("version") != version
                or record.get("image_repository") != UPSTREAM_REPOSITORY
                or record.get("source_commit") != CANDIDATE_SOURCE_COMMIT
                or record.get("slsa_provenance") != "present_per_platform"
                or set(record.get("platform_digests", {})) != {"linux/amd64", "linux/arm64"}):
            raise Refusal("candidate_provenance_record_mismatch")
        bound = {"index_digest": record["image_index_digest"], "revision": record["workflow_sha"],
                 "source_commit": record["source_commit"],
                 "bound_by": "committed provenance record",
                 "provenance_record_sha256": sha256_bytes(Path(provenance).read_bytes())}
    else:
        raise Refusal("version_not_bound")
    for key in ("revision", "source_commit"):
        if not re.fullmatch(r"[0-9a-f]{40}", str(bound[key])):
            raise Refusal("binding_malformed")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(bound["index_digest"])):
        raise Refusal("binding_malformed")
    bound.update(version=version, image=f"{UPSTREAM_REPOSITORY}@{bound['index_digest']}",
                 labels={"org.opencontainers.image.revision": bound["revision"],
                         "org.opencontainers.image.version": version,
                         "org.opencontainers.image.source": UPSTREAM_SOURCE})
    return bound


def cached_image(image, labels):
    """Require the exact digest locally, with the expected labels; never pulls."""
    result = command("docker", "image", "inspect", image, check=False)
    if result.returncode:
        raise Refusal("image_not_cached_by_digest")
    value, = json.loads(result.stdout)
    digest = image.rsplit("@", 1)[1]
    if digest not in {item.rsplit("@", 1)[1] for item in value.get("RepoDigests") or []}:
        raise Refusal("cached_image_digest_mismatch")
    found = (value.get("Config") or {}).get("Labels") or {}
    if any(found.get(key) != expected for key, expected in labels.items()):
        raise Refusal("cached_image_label_mismatch")
    if found.get("io.hass-mcp.build.dirty", "false") not in ("", "false"):
        raise Refusal("cached_image_label_mismatch")
    config = value.get("Config") or {}
    return {"image": image, "config_digest": value["Id"], "size_bytes": value.get("Size"),
            "architecture": value.get("Architecture"),
            "labels": {key: found.get(key) for key in sorted(labels)},
            # Image defaults as built; nothing here is supplied by the lane.
            "image_defaults": {"user": config.get("User"), "cmd": config.get("Cmd"),
                               "entrypoint": config.get("Entrypoint"),
                               "environment": sorted(config.get("Env") or [])}}


def host_figures():
    meminfo = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    usage = shutil.disk_usage("/")
    return {"memory_available_gb": round(int(meminfo["MemAvailable"].split()[0]) / 1048576, 2),
            "load_1m": os.getloadavg()[0],
            "root_free_gb": round(usage.free / 1024 ** 3, 2),
            "root_total_gb": round(usage.total / 1024 ** 3, 2)}


def require_host_floors(figures):
    if (figures["memory_available_gb"] < MIN_MEMORY_GB or figures["load_1m"] >= MAX_LOAD
            or figures["root_free_gb"] < MIN_ROOT_FREE_GB):
        raise Refusal("host_resource_floor_not_met")


def require_clean_checkout():
    if os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("CI"):
        raise Refusal("local_only_lane")
    # Only committed files may supply code; ignored scratch is never mounted.
    if command("git", "status", "--porcelain", "--untracked-files=normal").stdout.strip():
        raise Refusal("clean_committed_checkout_required")
    return command("git", "rev-parse", "HEAD").stdout.decode().strip()


def new_run_directory(path):
    run_dir = Path(path).absolute()
    if run_dir.resolve() != run_dir or ROOT / ".artifacts" not in run_dir.parents:
        raise Refusal("new_task_artifact_directory_required")
    if os.path.lexists(run_dir):
        raise Refusal("run_directory_already_exists")
    return run_dir


def names(version):
    stem = f"{PROJECT}-{version.replace('.', '-')}"
    return {"holder": stem + "-fixture", "peer": stem + "-peer", "inspect": stem + "-fs"}


def require_names_free(wanted):
    listed = command("docker", "container", "ls", "-a", "--format", "{{.Names}}")
    if set(listed.stdout.decode().split()) & set(wanted.values()):
        raise Refusal("disposable_name_already_exists")


def labels_argv(run_id):
    return ["--label", f"{LANE_LABEL}={run_id}", "--label", f"{PROJECT_LABEL}={PROJECT}"]


def holder_argv(name, run_id, out_dir, version, memory=HOLDER_MEMORY):
    argv = ["docker", "run", "--detach", "--pull", "never", "--name", name, *labels_argv(run_id),
            "--network", "none", "--read-only", "--tmpfs", "/tmp", "--memory", memory,
            "--pids-limit", "256", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", f"{os.getuid()}:{os.getgid()}", "--workdir", "/",
            "--mount", f"type=bind,source={ROOT / 'scripts'},target=/lane/scripts,readonly",
            "--mount", (f"type=bind,source={ROOT / 'hass_mcp_engineering_beta'},"
                        "target=/lane/hass_mcp_engineering_beta,readonly"),
            "--mount", f"type=bind,source={out_dir},target=/out",
            "--entrypoint", "python", HELPER_IMAGE, "-I", "-B",
            "/lane/scripts/fake_ha_read_gateway_contract_server.py",
            "--port", str(FIXTURE_PORT), "--upstream-version", version]
    host.require_memory(argv)
    return argv


def peer_argv(name, run_id, holder, image, memory=PEER_MEMORY, stand_in=False):
    """The ha-mcp peer; ``stand_in`` is the selftest's idle helper in the same position."""
    argv = ["docker", "run", "--detach", "--pull", "never", "--name", name, *labels_argv(run_id),
            "--network", "container:" + holder, "--read-only", "--tmpfs", "/tmp",
            "--tmpfs", "/home/mcpuser/.ha-mcp", "--memory", memory, "--pids-limit", "256",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
    if stand_in:
        argv += ["--entrypoint", "python", image, "-I", "-c", "import time; time.sleep(900)"]
    else:
        for key, value in PEER_ENVIRONMENT:
            argv += ["--env", f"{key}={value}"]
        argv += [image, PEER_COMMAND]
    host.require_memory(argv)
    return argv


def exec_argv(holder, *python_args):
    return ["docker", "exec", holder, "python", "-I", "-B", *python_args]


def inspect_owned(name, run_id):
    """Inspect one of this run's containers; None only when it is proven absent."""
    result = command("docker", "container", "inspect", name, check=False)
    if result.returncode:
        listed = command("docker", "container", "ls", "-a", "--format", "{{.Names}}")
        if name in listed.stdout.decode().split():
            raise Refusal("owned_container_inspection_failed")
        return None
    data, = json.loads(result.stdout)
    if ((data.get("Config") or {}).get("Labels") or {}).get(LANE_LABEL) != run_id:
        raise Refusal("container_ownership_mismatch")
    return data


def _common_container_checks(data, image, memory):
    config, settings = data.get("HostConfig") or {}, data.get("NetworkSettings") or {}
    if data.get("Image") != image["config_digest"] or (data.get("Config") or {}).get("Image") != image["image"]:
        raise Refusal("running_image_mismatch")
    if config.get("Memory") != host.memory_bytes(memory) or config.get("ReadonlyRootfs") is not True:
        raise Refusal("running_container_limits_mismatch")
    if config.get("Privileged") or config.get("PortBindings") or config.get("PublishAllPorts"):
        raise Refusal("container_publishes_ports")
    if any(bindings for bindings in (settings.get("Ports") or {}).values()):
        raise Refusal("container_publishes_ports")
    if (data.get("State") or {}).get("Running") is not True:
        raise Refusal("container_not_running")
    return config, settings


def verify_holder(data, probe, image, memory=HOLDER_MEMORY):
    """The namespace owner has no network: mode none, only ``lo``, no route out."""
    config, settings = _common_container_checks(data, image, memory)
    if config.get("NetworkMode") != "none" or set(settings.get("Networks") or {}) != {"none"}:
        raise Refusal("holder_network_not_none")
    if probe.get("interfaces") != ["lo"] or probe.get("ipv4_routes") != 0:
        raise Refusal("namespace_not_loopback_only")
    if probe.get("egress_errno") != ENETUNREACH:
        raise Refusal("namespace_egress_not_refused")
    return {"network_mode": "none", "docker_networks": ["none"], **probe,
            "published_ports": [], "docker_networks_created": []}


def verify_peer(data, holder_id, image, memory=PEER_MEMORY):
    """The peer shares the holder's namespace and nothing else."""
    config, settings = _common_container_checks(data, image, memory)
    if config.get("NetworkMode") != "container:" + holder_id or settings.get("Networks"):
        raise Refusal("peer_network_not_holder_namespace")
    if any(item.get("Type") == "bind" for item in data.get("Mounts") or []):
        raise Refusal("peer_has_bind_mount")
    return {"network_mode": "container:<holder>", "docker_networks": [],
            "mounts": sorted(f"{item.get('Type')}:{item.get('Destination')}"
                             for item in data.get("Mounts") or []),
            "tmpfs": sorted(config.get("Tmpfs") or {})}


def resource_inventory(stream):
    """Hash every file under ``ha_mcp/resources/`` in a container export stream.

    Members are read from the stream; nothing is extracted to disk or executed.
    """
    files, links, packages, total = [], [], set(), 0
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            path = "/" + member.name.lstrip("./")
            match = re.search(r"/site-packages/([A-Za-z0-9_.\-]+)\.dist-info/METADATA$", path)
            if match and member.isreg():
                packages.add(match.group(1))
            if RESOURCE_MARKER not in path:
                continue
            if member.isreg():
                total += member.size
                if len(files) >= MAX_RESOURCE_FILES or total > MAX_RESOURCE_BYTES:
                    raise Refusal("image_resources_exceed_bound")
                digest, source = hashlib.sha256(), archive.extractfile(member)
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
                files.append({"path": path, "bytes": member.size, "sha256": digest.hexdigest()})
            elif member.issym() or member.islnk():
                links.append({"path": path, "target": member.linkname})
    files.sort(key=lambda item: item["path"])
    links.sort(key=lambda item: item["path"])
    tree = hashlib.sha256(
        "".join(f"{item['sha256']}  {item['path']}\n" for item in files).encode("utf-8"))
    return {"marker": RESOURCE_MARKER, "file_count": len(files), "total_bytes": total,
            "tree_sha256": tree.hexdigest(), "files": files, "links": links,
            "installed_distributions": sorted(packages)}


def image_resources(name, run_id, image):
    """P7: read the image's file system from a container that is never started."""
    command("docker", "create", "--pull", "never", "--name", name, *labels_argv(run_id),
            "--network", "none", "--memory", INSPECT_MEMORY, image["image"])
    try:
        process = subprocess.Popen(["docker", "export", name], cwd=ROOT,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            inventory = resource_inventory(process.stdout)
        except (tarfile.TarError, OSError):
            raise Refusal("image_export_unreadable") from None
        finally:
            process.stdout.close()
            exported = process.wait(timeout=300)
        if exported:
            raise Refusal("image_export_failed")
        state = (inspect_owned(name, run_id) or {}).get("State") or {}
        if state.get("Status") != "created":
            raise Refusal("inspection_container_was_started")
    finally:
        if inspect_owned(name, run_id) is not None:
            command("docker", "rm", "-f", "-v", name)
    return inventory


def bounded_logs(name, path):
    result = command("docker", "logs", name, check=False)
    Path(path).write_bytes((result.stdout + result.stderr)[-LOG_BOUND:])


def helper_runtime(holder):
    """The helper's Python and client library versions must match this checkout's pins."""
    runtime = json.loads(command(*exec_argv(holder, "-c", RUNTIME)).stdout)
    pins = dict(line.strip().split("==", 1) for line in REQUIREMENTS.read_text().splitlines()
                if "==" in line and not line.startswith("#"))
    if any(runtime[name] != pins.get(name) for name in ("mcp", "aiohttp")):
        raise Refusal("helper_runtime_does_not_match_requirements")
    return runtime


def start_holder(wanted, run_id, out_dir, version, helper):
    command(*holder_argv(wanted["holder"], run_id, out_dir, version))
    data = inspect_owned(wanted["holder"], run_id)
    if data is None:
        raise Refusal("holder_missing")
    probe = json.loads(command(*exec_argv(wanted["holder"], "-c", NET_PROBE)).stdout)
    posture = verify_holder(data, probe, helper)
    command(*exec_argv(wanted["holder"], "-c", WAIT_HTTP, FIXTURE_URL + "/api/", "60", "ok"),
            timeout=90)
    return data["Id"], posture


def fixture_stats(holder):
    raw = command(*exec_argv(holder, "-c", FETCH, FIXTURE_URL + "/__fixture__/stats")).stdout
    stats = json.loads(raw)
    if not isinstance(stats, dict) or any(stats.get(key) != 0 for key in MUTATION_COUNTERS):
        raise Refusal("fixture_recorded_a_mutation_surface")
    return stats


def observe(wanted, run_id, out_dir, bound, upstream, holder_id):
    """Start the peer in the holder's namespace and take P1, P2 and the capture twice."""
    holder, peer = wanted["holder"], wanted["peer"]
    command(*peer_argv(peer, run_id, holder, upstream["image"]))
    posture = verify_peer(inspect_owned(peer, run_id) or {}, holder_id, upstream)
    command(*exec_argv(holder, "-c", WAIT_HTTP, ENDPOINT, "120", "any"), timeout=150)
    review = "/lane/scripts/review_upstream_read_release.py"
    for index in (1, 2):
        command(*exec_argv(holder, review, "capture", "--endpoint", ENDPOINT,
                           "--output", f"/out/capture-{index}.json"), timeout=300)
    command(*exec_argv(holder, "/lane/scripts/mcp_raw_observation.py", "--endpoint", ENDPOINT,
                       "--output", "/out/observation.json"), timeout=300)
    stats = fixture_stats(holder)
    state = (inspect_owned(peer, run_id) or {}).get("State") or {}
    if state.get("Running") is not True or state.get("OOMKilled"):
        raise Refusal("peer_did_not_stay_running")
    first, second = ((out_dir / f"capture-{index}.json").read_bytes() for index in (1, 2))
    if first != second:
        raise Refusal("capture_not_deterministic")
    capture = json.loads(first)
    observation_raw = (out_dir / "observation.json").read_bytes()
    observation = json.loads(observation_raw)
    if capture.get("server_version") != bound["version"]:
        raise Refusal("peer_version_mismatch")
    if observation.get("tool_count") != capture.get("tool_count"):
        raise Refusal("observation_and_capture_disagree")
    result = {"peer_network": posture, "fixture_stats": stats,
              "capture": {key: capture.get(key) for key in (
                  "capture_format_version", "server_name", "server_version",
                  "protocol_version", "tool_count", "catalog_fingerprint")},
              "capture_sha256": sha256_bytes(first), "capture_bytes": len(first),
              "captures_byte_identical": True,
              "observation_sha256": sha256_bytes(observation_raw),
              "observation": {key: observation.get(key) for key in (
                  "negotiated_protocol_version", "requested_protocol_version", "tool_count")}}
    if "committed_capture_sha256" in bound:
        result["matches_committed_capture"] = (
            result["capture_sha256"] == bound["committed_capture_sha256"])
    return result


def cleanup(wanted, run_id):
    for key in ("peer", "holder", "inspect"):
        if inspect_owned(wanted[key], run_id) is not None:
            command("docker", "rm", "-f", "-v", wanted[key])
    for key in ("peer", "holder", "inspect"):
        if inspect_owned(wanted[key], run_id) is not None:
            raise Refusal("owned_container_remains")
    return {"clean": True, "containers_absent": True, "docker_networks_created": 0}


def execute(args, launch):
    """Shared body of ``selftest`` and ``run``; everything before mkdir is preflight."""
    if not __debug__:
        raise Refusal("assertions_required")
    version = getattr(args, "version", None) or BASELINE_VERSION
    if not VERSION_PATTERN.fullmatch(version):
        raise Refusal("version_not_bound")
    revision = require_clean_checkout()
    bound = binding(version) if args.command == "run" else None
    helper = cached_image(HELPER_IMAGE, HELPER_LABELS)
    upstream = cached_image(bound["image"], bound["labels"]) if bound else None
    figures = host_figures()
    require_host_floors(figures)
    wanted = names(version if bound else "selftest")
    require_names_free(wanted)
    run_dir = new_run_directory(args.run_dir)
    run_dir.mkdir(mode=0o700, parents=False)  # Exclusive; evidence is never overwritten.
    launch["execution_started"] = True
    out_dir = run_dir / "out"
    out_dir.mkdir(mode=0o700)
    run_id = uuid4().hex
    result = {
        "model": MODEL, "command": args.command, "result": "WITHHELD", "started": now(),
        "harness_commit": revision, "run_id": run_id, "notes": list(args.note or []),
        "helper_image": helper, "host_at_start": figures, "pull": "never",
        "home_assistant": ("synthetic read-only fixture "
                           "scripts/fake_ha_read_gateway_contract_server.py; "
                           "no Home Assistant Core was started"),
        "container_memory": {"holder": HOLDER_MEMORY, "peer": PEER_MEMORY,
                             "inspect": INSPECT_MEMORY},
        "cleanup": {"clean": False}}
    if bound:
        result.update(
            binding={key: bound[key] for key in sorted(bound) if key not in ("labels", "image")},
            upstream_image=upstream,
            redaction={"setting": "image defaults",
                       "detail": "no redaction option is passed to the peer",
                       "peer_environment": [list(item) for item in PEER_ENVIRONMENT],
                       "peer_command": PEER_COMMAND})

    def interrupted(_signum, _frame):
        raise Refusal("lane_interrupted")
    previous = signal.signal(signal.SIGTERM, interrupted)
    started = time.monotonic()
    try:
        if bound:
            inventory = image_resources(wanted["inspect"], run_id, upstream)
            (run_dir / "image-resources.json").write_bytes(canonical(inventory))
            result["image_resources"] = {key: inventory[key] for key in (
                "file_count", "total_bytes", "tree_sha256")}
        holder_id, posture = start_holder(wanted, run_id, out_dir, version, helper)
        result["network_posture"] = posture
        result["helper_runtime"] = helper_runtime(wanted["holder"])
        if bound:
            result.update(observe(wanted, run_id, out_dir, bound, upstream, holder_id))
        else:
            # Selftest: an idle helper stands where the peer will, so the namespace
            # checks are exercised on this Docker before any upstream image runs.
            command(*peer_argv(wanted["peer"], run_id, wanted["holder"], HELPER_IMAGE,
                               stand_in=True))
            result["peer_network"] = verify_peer(
                inspect_owned(wanted["peer"], run_id) or {}, holder_id, helper)
            command(*exec_argv(wanted["holder"], "/lane/scripts/review_upstream_read_release.py",
                               "capture", "--help"))
            command(*exec_argv(wanted["holder"], "/lane/scripts/mcp_raw_observation.py",
                               "--help"))
            result["fixture_stats"] = fixture_stats(wanted["holder"])
        result["result"] = "PASS"
    except BaseException as exc:  # WITHHELD, with a fixed token only.
        result.update(result="WITHHELD", failure_type=type(exc).__name__,
                      failure=str(exc) if isinstance(exc, Refusal) else "unexpected_error")
    finally:
        try:
            for key, target in (("peer", "peer.log"), ("holder", "fixture.log")):
                if inspect_owned(wanted[key], run_id) is not None:
                    bounded_logs(wanted[key], run_dir / target)
            result["cleanup"] = cleanup(wanted, run_id)
        except BaseException as exc:
            result.update(result="WITHHELD",
                          cleanup={"clean": False, "failure_type": type(exc).__name__})
        result.update(ended=now(), seconds=round(time.monotonic() - started, 1),
                      host_at_end=host_figures())
        (run_dir / "receipt.json").write_bytes(canonical(result))
        signal.signal(signal.SIGTERM, previous)
    launch.update(result=result["result"], failure=result.get("failure"),
                  cleanup_clean=result["cleanup"]["clean"])
    return 0 if result["result"] == "PASS" and result["cleanup"]["clean"] else 1


def pull(args, launch):
    """Fetch the bound candidate by index digest, anonymously, after the binding check."""
    require_clean_checkout()
    bound = binding(args.version)
    if "provenance_record_sha256" not in bound:
        raise Refusal("pull_requires_committed_provenance_record")
    figures = host_figures()
    require_host_floors(figures)
    record_dir = new_run_directory(args.run_dir)
    record_dir.mkdir(mode=0o700)
    launch["execution_started"] = True
    empty = record_dir / "docker-config-empty"
    empty.mkdir(mode=0o700)  # No credential store: the registry read is anonymous.
    environment = {key: value for key, value in os.environ.items()
                   if key in ("PATH", "HOME", "LANG", "XDG_RUNTIME_DIR", "DOCKER_HOST")}
    environment["DOCKER_CONFIG"] = str(empty)
    result = {"model": MODEL, "command": "pull", "result": "WITHHELD", "started": now(),
              "binding": {key: bound[key] for key in sorted(bound) if key != "labels"},
              "platform": "linux/amd64", "registry_authentication": "none (empty DOCKER_CONFIG)",
              "host_before": figures, "notes": list(args.note or [])}
    try:
        outcome = command("docker", "pull", "--quiet", "--platform", "linux/amd64",
                          bound["image"], check=False, timeout=1500, env=environment)
        if outcome.returncode:
            raise Refusal("pull_failed")
        result["upstream_image"] = cached_image(bound["image"], bound["labels"])
        result["result"] = "PASS"
    except BaseException as exc:
        result.update(failure_type=type(exc).__name__,
                      failure=str(exc) if isinstance(exc, Refusal) else "unexpected_error")
    result.update(ended=now(), host_after=host_figures())
    (record_dir / "receipt.json").write_bytes(canonical(result))
    launch.update(result=result["result"], failure=result.get("failure"))
    return 0 if result["result"] == "PASS" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("selftest", "run", "pull"):
        sub = commands.add_parser(name)
        sub.add_argument("--run-dir", type=Path, required=True)
        sub.add_argument("--launch-log", type=Path, required=True)
        sub.add_argument("--note", action="append")
        if name != "selftest":
            sub.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    launch = {"command": args.command, "version": getattr(args, "version", None),
              "run_dir": str(args.run_dir), "started": now(), "execution_started": False,
              "notes": list(args.note or [])}
    try:
        code = (pull if args.command == "pull" else execute)(args, launch)
    except Refusal as exc:
        # Before the run directory exists this is a preflight refusal, not a lane result.
        launch.update(result="REFUSED" if not launch["execution_started"] else "WITHHELD",
                      failure=str(exc))
        code = 2 if not launch["execution_started"] else 1
    except Exception as exc:  # noqa: BLE001 - the launch log names the type only.
        launch.update(result="ERROR", failure=type(exc).__name__)
        code = 3
    launch["ended"] = now()
    args.launch_log.parent.mkdir(parents=True, exist_ok=True)
    with args.launch_log.open("a", encoding="ascii") as log:
        log.write(json.dumps(launch, sort_keys=True) + "\n")
    print(json.dumps(launch, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
