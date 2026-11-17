"""SR live-socket replay through a local Engineering image (HAMCP-371, SR).

``build`` makes the Engineering image from the committed head's
``hass_mcp_engineering_beta`` tree, with the build arguments ci.yml gives it,
under a local tag only.  It is never pushed.  The build context is a tar
stream made from the git tree object by object, so its sha256 is a property
of the commit; the image id and that sha256 go to
``docs/evidence/hamcp371/sr/engineering-image.json``.

``run`` is the SR contract (architect ruling 2026-11-15 17:50 CST), two setups
in one run, one after the other, each on its own disposable Core:

* ha-mcp 9.2.0 with component 9.2.0 (upstream commit 51e3aac7), the S5
  profile B setup unchanged;
* the control: ha-mcp 8.5.0 with component 2.2.1 (fc54437a), whose tree is
  checked against the committed S7 listing before anything starts.

Each setup uses the S5 lane as it is: one idle holder with no network, Core
and every other container in its namespace, ``--pull never``, no published
port, the posture probed from the holder and from Core, the production tool
flags (D1) checked on the peer and in its catalog.  This run adds two
containers to that namespace.  One is the recording proxy, run inside the
holder: Engineering's upstream URL names it, and it passes every exchange
through to the peer unchanged and stores the raw upstream bytes.  The other
is Engineering from the image ``build`` recorded, given the administrator
token as ``HA_TOKEN`` by name and never a ``SUPERVISOR_TOKEN``.  The holder
then makes the 27 admitted reads through Engineering's public tools.

Engineering's ``/data`` starts with ci.yml's options plus the Core registry
switch and the committed Core public key, and with the Core registry cache and
its ``.lifecycle`` witness that Engineering's own ``refresh()`` wrote offline
(``core_registry_seed.py``, ruling 2026-11-16 06:05 CST, seeding (a)).  Both
are copied unchanged under the names refresh() gave them; the preflight checks
them against the seed record's sha256s.  Known difference from production:
same verification and write path, local transport instead of GitHub.

"Host network" in the contract is read as the holder's loopback-only
namespace: Core, the peer, the proxy and Engineering all reach each other on
127.0.0.1 and nothing reaches out.  The receipt says so.

A preflight refusal happens before any container, volume or run directory
exists.  A failure after that is WITHHELD and is reported, never run again.
Standard library only; container output is untrusted data.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tarfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import core_read_replay_capture_local as lane  # noqa: E402
import upstream_catalog_capture_local as s4  # noqa: E402
from upstream_catalog_capture_local import Refusal, canonical, command, now  # noqa: E402

MODEL = "hamcp371-live-replay-local-v1"
PROJECT = "hamcp371-sr"
CANDIDATE, CONTROL = s4.CANDIDATE_VERSION, "8.5.0"
SETUPS = (CANDIDATE, CONTROL)
# The component each server version runs with (ruling 2026-11-15 17:50).
COMPONENT_OF = {CANDIDATE: CANDIDATE, CONTROL: "2.2.1"}
CONTROL_TREE_LIST = ROOT / "docs/evidence/hamcp371/s7/component-2.2.1-tree.txt"
UPSTREAM_SECRET_PATH = "/synthetic-upstream-secret/mcp"
STRICT_SUFFIX = "/catalog/full"
# 9.x upstream URLs are strict and end in the full-catalog path; 8.5.0 is legacy.
STRICT_VERSIONS = (CANDIDATE,)
PROXY_PORT = 18087
ENGINEERING_SECRET = "synthetic-engineering-access-secret"
ENGINEERING_PORT, APPROVAL_PORT = 18100, 18110
ENGINEERING_ENDPOINT = f"http://127.0.0.1:{ENGINEERING_PORT}/{ENGINEERING_SECRET}/mcp"
ENGINEERING_READY = f"http://127.0.0.1:{ENGINEERING_PORT}/ready"
# The lane's containers stay inside the 3g ceiling with Engineering added.
ENGINEERING_MEMORY = "384m"
ENGINEERING_DATA = "/data"
# ci.yml's synthetic options, unchanged.
ENGINEERING_OPTIONS = {
    "access_secret": ENGINEERING_SECRET, "audit_enabled": True, "prewarm_enabled": False,
    "rate_limit_per_minute": 600, "rate_limit_burst": 100,
    "approval_notification_service": "notify.mobile_app_beta31_fixture",
    "upstream_trust_registry_enabled": False,
}
# Seeding ruling (a), 2026-11-16 06:05 CST: the Core registry is enabled with the
# committed key, and its cache and witness are the files Engineering's refresh()
# wrote offline (docs/evidence/hamcp371/sr/core_registry_seed.py), copied unchanged.
CORE_KEY_FILE = ROOT / "docs/evidence/hamcp222/core-public-key.txt"
CORE_SEED_DIR = ROOT / "docs/evidence/hamcp371/sr/core-registry-seed"
CORE_SEED_RECORD = CORE_SEED_DIR / "seed-record.json"
CORE_SEED_SCHEMA = "hamcp371.sr.core_registry_seed.v1"
CORE_SEED_FILES = ("ha-core-release-registry-cache.json",
                   "ha-core-release-registry-cache.json.lifecycle")
CORE_SEED_BLOB = "6d91c9b243cd7c0414d134e6620718903ef8fc33"
CORE_SEED_MODE = 0o600  # The mode refresh() wrote them with.
ENGINEERING_TOKEN_VARIABLE = "HA_TOKEN"
FORBIDDEN_VARIABLE = "SUPERVISOR_TOKEN"
HOLDER_SCRIPT = "/lane/scripts/hamcp371_live_replay_holder.py"
READS_SECONDS, READY_SECONDS = 1800, 120
IMAGE_REPOSITORY = "hamcp371-sr-engineering"
IMAGE_RECORD = ROOT / "docs/evidence/hamcp371/sr/engineering-image.json"
IMAGE_SCHEMA = "hamcp371.sr.engineering_image.v1"
CONTEXT_PATH = "hass_mcp_engineering_beta"
CONTEXT_MODES = {"100644": 0o644, "100755": 0o755}
BUILD_SECONDS = 2400
CONFIG_VERSION = re.compile(r'^version:[ \t]*"?([^"\s]+)"?[ \t]*$', re.MULTILINE)
OBJECT_ID = re.compile(r"[0-9a-f]{40}")
LIST_LINE = re.compile(r"([0-9a-f]{64}) ([0-9]+) (100644) " + re.escape(lane.COMPONENT_PATH)
                       + r"/(.+)")
NOT_REPLAYED = {"r27-apps": "not replayable in the local lane (no Supervisor)"}


# --- The Engineering image ---------------------------------------------------


def git(*args):
    return command("git", *args, timeout=120).stdout


def build_context(treeish, read=git):
    """The build context of one committed tree as a tar stream; same tree, same bytes.

    Only ordinary files are allowed; a link or submodule refuses.  Docker
    applies the tree's own ``.dockerignore`` to the stream as it would to the
    directory.
    """
    entries = []
    for raw in read("ls-tree", "-r", "-z", "--full-tree", treeish).split(b"\0"):
        if not raw:
            continue
        meta, path = raw.split(b"\t", 1)
        mode, kind, oid = meta.decode("ascii").split()
        if mode not in CONTEXT_MODES or kind != "blob" or not OBJECT_ID.fullmatch(oid):
            raise Refusal("build_context_entry_not_a_file")
        entries.append((path.decode("utf-8"), mode, oid))
    if not entries:
        raise Refusal("build_context_empty")
    stream, listing = io.BytesIO(), []
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path, mode, oid in sorted(entries):
            body = read("cat-file", "blob", oid)
            if hashlib.sha1(b"blob %d\0" % len(body) + body).hexdigest() != oid:
                raise Refusal("build_context_object_mismatch")
            info = tarfile.TarInfo(path)
            info.size, info.mode, info.mtime = len(body), CONTEXT_MODES[mode], 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(body))
            listing.append(f"{mode} {hashlib.sha256(body).hexdigest()} {len(body)} {path}\n")
    raw = stream.getvalue()
    text = "".join(listing).encode("utf-8")
    return raw, {"files": len(entries), "bytes": len(raw),
                 "sha256": hashlib.sha256(raw).hexdigest(),
                 "listing_sha256": hashlib.sha256(text).hexdigest()}


def config_version(text):
    found = CONFIG_VERSION.findall(text)
    if len(found) != 1:
        raise Refusal("engineering_version_unreadable")
    return found[0]


def build_arguments(version, revision, built_at):
    """ci.yml's four build arguments, in its order."""
    return [("BUILD_VERSION", version), ("HAMCP_BUILD_SHA", revision),
            ("HAMCP_BUILD_TIME", built_at), ("HAMCP_BUILD_DIRTY", "false")]


def build_argv(tag, arguments):
    argv = ["docker", "build", "--tag", tag]
    for key, value in arguments:
        argv += ["--build-arg", f"{key}={value}"]
    return argv + ["-"]


def build(args, launch):
    revision = s4.require_clean_checkout()
    figures = s4.host_figures()
    s4.require_host_floors(figures)
    tree = git("rev-parse", f"{revision}:{CONTEXT_PATH}").decode("ascii").strip()
    version = config_version(git("show", f"{revision}:{CONTEXT_PATH}/config.yaml").decode())
    raw, context = build_context(f"{revision}:{CONTEXT_PATH}")
    tag = f"{IMAGE_REPOSITORY}:{revision[:12]}"
    if command("docker", "image", "inspect", tag, check=False).returncode == 0:
        raise Refusal("local_tag_already_exists")
    built_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    arguments = build_arguments(version, revision, built_at)
    launch["execution_started"] = True
    log = Path(args.log)
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    done = subprocess.run(build_argv(tag, arguments), cwd=ROOT, input=raw,
                          capture_output=True, timeout=BUILD_SECONDS,
                          env={**os.environ, "DOCKER_BUILDKIT": "1"})
    log.write_bytes((done.stdout + done.stderr)[-s4.LOG_BOUND:])
    if done.returncode:
        raise Refusal("engineering_build_failed")
    value, = json.loads(command("docker", "image", "inspect", tag).stdout)
    labels = (value.get("Config") or {}).get("Labels") or {}
    if (labels.get("org.opencontainers.image.revision") != revision
            or labels.get("org.opencontainers.image.version") != version
            or labels.get("io.hass-mcp.build.dirty") != "false"):
        raise Refusal("built_image_label_mismatch")
    record = {"schema": IMAGE_SCHEMA, "tag": tag, "image_id": value["Id"],
              "repo_digests": value.get("RepoDigests") or [], "pushed": False,
              "revision": revision, "tree": tree, "context_path": CONTEXT_PATH,
              "version": version, "built_at": built_at,
              "build_arguments": [list(item) for item in arguments],
              "build_seconds": round(time.monotonic() - started, 1),
              "context": context, "architecture": value.get("Architecture"),
              "size_bytes": value.get("Size"),
              "labels": {key: labels[key] for key in sorted(labels)},
              "method": ("git ls-tree and cat-file of the committed tree into one tar stream "
                         "(mtime 0, uid 0, modes from git), checked against each blob id, "
                         "then docker build - with ci.yml's build arguments; local tag only, "
                         "never pushed"),
              "host_at_start": figures}
    IMAGE_RECORD.write_bytes(canonical(record))
    launch.update(result="BUILT", image_id=value["Id"], tag=tag,
                  context_sha256=context["sha256"])
    return 0


def engineering_image(record_path=IMAGE_RECORD, inspect=None, read=git):
    """The recorded local image, unchanged, built from the same tree as this checkout."""
    try:
        record = s4.read_json(record_path)
    except (OSError, ValueError):
        raise Refusal("engineering_image_record_missing") from None
    if record.get("schema") != IMAGE_SCHEMA or record.get("pushed") is not False:
        raise Refusal("engineering_image_record_mismatch")
    here = read("rev-parse", f"HEAD:{CONTEXT_PATH}").decode("ascii").strip()
    then = read("rev-parse", f"{record['revision']}:{CONTEXT_PATH}").decode("ascii").strip()
    if here != record["tree"] or then != record["tree"]:
        raise Refusal("engineering_tree_changed_since_build")
    found = (inspect or (lambda tag: json.loads(
        command("docker", "image", "inspect", tag, check=False).stdout or b"[]")))(
            record["tag"])
    if len(found) != 1 or found[0].get("Id") != record["image_id"]:
        raise Refusal("engineering_image_not_the_recorded_build")
    labels = (found[0].get("Config") or {}).get("Labels") or {}
    if labels.get("org.opencontainers.image.revision") != record["revision"]:
        raise Refusal("engineering_image_not_the_recorded_build")
    return {"image": record["tag"], "config_digest": record["image_id"],
            "revision": record["revision"], "tree": record["tree"],
            "context_sha256": record["context"]["sha256"],
            "record": record_path.relative_to(ROOT).as_posix()
            if Path(record_path).is_relative_to(ROOT) else Path(record_path).name}


# --- The two setups ----------------------------------------------------------


def upstream_url(version, port):
    """Engineering's configured upstream: strict full-catalog for 9.x, legacy for 8.5.0."""
    base = f"http://127.0.0.1:{port}{UPSTREAM_SECRET_PATH}"
    return base + STRICT_SUFFIX if version in STRICT_VERSIONS else base


def control_binding(registry=s4.REGISTRY):
    """8.5.0 from its reviewed registry row, in the shape of ``s4.binding``."""
    rows = [row for row in s4.read_json(registry)["releases"] if row.get("version") == CONTROL]
    if len(rows) != 1 or rows[0].get("approval_status") != "reviewed":
        raise Refusal("control_registry_row_missing")
    row = rows[0]
    for key in ("image_revision", "source_commit"):
        if not OBJECT_ID.fullmatch(str(row.get(key))):
            raise Refusal("binding_malformed")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(row.get("image_index_digest"))):
        raise Refusal("binding_malformed")
    return {"version": CONTROL, "index_digest": row["image_index_digest"],
            "revision": row["image_revision"], "source_commit": row["source_commit"],
            "bound_by": "reviewed registry row",
            "committed_capture_sha256": row["capture_sha256"],
            "image": f"{s4.UPSTREAM_REPOSITORY}@{row['image_index_digest']}",
            "labels": {"org.opencontainers.image.revision": row["image_revision"],
                       "org.opencontainers.image.version": CONTROL,
                       "org.opencontainers.image.source": s4.UPSTREAM_SOURCE}}


def upstream_binding(version):
    return s4.binding(version) if version == CANDIDATE else control_binding()


def names(version):
    stem = f"{PROJECT}-{version.replace('.', '-')}"
    wanted = {"holder": stem + "-holder", "core": stem + "-core", "volume": stem + "-config",
              "peer": stem + "-peer", "engineering": stem + "-engineering",
              "engineering_volume": stem + "-engineering-data"}
    if version == CANDIDATE:
        wanted["package"] = stem + "-package"
    return wanted


def container_keys(wanted):
    """Removal order: Engineering, the peer, the package source, Core, the namespace owner."""
    return ["engineering", "peer", *(["package"] if "package" in wanted else []), "core",
            "holder"]


def labels_argv(run_id):
    return ["--label", f"{s4.LANE_LABEL}={run_id}", "--label", f"{s4.PROJECT_LABEL}={PROJECT}"]


def relabel(argv):
    """A lane argv with this run's project label in place of the lane's."""
    old = f"{s4.PROJECT_LABEL}={lane.PROJECT}"
    if argv.count(old) != 1:
        raise Refusal("lane_argv_not_reviewed")
    return [f"{s4.PROJECT_LABEL}={PROJECT}" if item == old else item for item in argv]


def peer_environment():
    """The lane's peer environment with the synthetic secret path ci.yml uses."""
    return tuple(("MCP_SECRET_PATH", UPSTREAM_SECRET_PATH) if key == "MCP_SECRET_PATH"
                 else (key, value) for key, value in lane.PEER_ENVIRONMENT)


def peer_argv(name, run_id, holder, image):
    """One pinned ha-mcp image; the token is passed by name."""
    argv = ["docker", "run", "--detach", "--pull", "never", "--name", name, *labels_argv(run_id),
            "--network", "container:" + holder, "--read-only", "--tmpfs", "/tmp",
            "--tmpfs", s4.DATA_DIRECTORIES["mcpuser"], "--memory", s4.PEER_MEMORY,
            "--pids-limit", "256", "--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
    for key, value in peer_environment():
        argv += ["--env", f"{key}={value}"]
    argv += ["--env", lane.TOKEN_VARIABLE, image, s4.PEER_COMMAND]
    s4.host.require_memory(argv)
    return argv


def catalog_check(path, version):
    """The disabled tools are absent for both; the YAML read is required for 9.2.0 only."""
    stored = Path(path).read_bytes()
    names_found = (json.loads(stored) or {}).get("tool_names_in_wire_order")
    if not isinstance(names_found, list) or not all(isinstance(n, str) for n in names_found):
        raise Refusal("peer_catalog_unreadable")
    if any(name in names_found for name in lane.ABSENT_FROM_CATALOG):
        raise Refusal("peer_catalog_advertises_a_disabled_tool")
    present = {name: name in names_found for name in lane.PRESENT_IN_CATALOG}
    if version == CANDIDATE and not all(present.values()):
        raise Refusal("peer_catalog_lacks_the_yaml_read")
    return {"file": Path(path).name, "sha256": hashlib.sha256(stored).hexdigest(),
            "tool_count": len(names_found), "absent": list(lane.ABSENT_FROM_CATALOG),
            "present": present,
            "present_required": version == CANDIDATE}


def engineering_environment(upstream):
    return (("HA_URL", lane.CORE_URL), ("MCP_PORT", str(ENGINEERING_PORT)),
            ("APPROVAL_INGRESS_PORT", str(APPROVAL_PORT)),
            ("UPSTREAM_DASHBOARD_MCP_URL", upstream))


def engineering_argv(name, run_id, holder, volume, image, upstream):
    """``docker create`` for Engineering in the holder's namespace; the token by name only."""
    argv = ["docker", "create", "--pull", "never", "--name", name, *labels_argv(run_id),
            "--network", "container:" + holder, "--read-only", "--tmpfs", "/tmp",
            "--memory", ENGINEERING_MEMORY, "--pids-limit", "256", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--mount", f"type=volume,source={volume},target={ENGINEERING_DATA}"]
    for key, value in engineering_environment(upstream):
        argv += ["--env", f"{key}={value}"]
    argv += ["--env", ENGINEERING_TOKEN_VARIABLE, image]
    if any(FORBIDDEN_VARIABLE in item for item in argv):
        raise Refusal("engineering_given_a_supervisor_token")
    s4.host.require_memory(["docker", "run", *argv[2:]])  # The guard reads ``run`` only.
    return argv


def engineering_token_environment(token):
    """The host environment with ``HA_TOKEN`` and without any ``SUPERVISOR_TOKEN``."""
    return {**{key: value for key, value in os.environ.items() if key != FORBIDDEN_VARIABLE},
            ENGINEERING_TOKEN_VARIABLE: token}


def engineering_options(key_file=CORE_KEY_FILE):
    """ci.yml's options with the Core registry enabled under the committed key."""
    return {**ENGINEERING_OPTIONS, "ha_core_release_registry_enabled": True,
            "ha_core_release_registry_public_key": Path(key_file).read_text(
                encoding="utf-8").strip()}


def core_seed(record_path=CORE_SEED_RECORD, key_file=CORE_KEY_FILE):
    """The committed refresh() output, each file exactly as its seed record says."""
    try:
        record = s4.read_json(record_path)
        files = {name: (Path(record_path).parent / name).read_bytes() for name in CORE_SEED_FILES}
        key = Path(key_file).read_bytes()
    except (OSError, ValueError):
        raise Refusal("core_seed_missing") from None
    recorded = record.get("files") or {}
    if (record.get("schema") != CORE_SEED_SCHEMA
            or (record.get("registry") or {}).get("blob") != CORE_SEED_BLOB
            or (record.get("public_key") or {}).get("sha256") != hashlib.sha256(key).hexdigest()
            or record.get("lane_paths") != {name: f"{ENGINEERING_DATA}/{name}"
                                            for name in CORE_SEED_FILES}
            or sorted(recorded) != sorted(CORE_SEED_FILES)
            or any(hashlib.sha256(data).hexdigest() != recorded[name].get("sha256")
                   for name, data in files.items())):
        raise Refusal("core_seed_mismatch")
    return {"record": Path(record_path).relative_to(ROOT).as_posix()
            if Path(record_path).is_relative_to(ROOT) else Path(record_path).name,
            "record_sha256": hashlib.sha256(Path(record_path).read_bytes()).hexdigest(),
            "files": {name: {"path": f"{ENGINEERING_DATA}/{name}", "bytes": len(data),
                             "sha256": recorded[name]["sha256"]}
                      for name, data in files.items()},
            "known_difference": record.get("known_difference"), "data": files}


def options_archive(seed, options=None):
    """options.json and the two seed files, under the names Engineering reads in /data."""
    body = json.dumps(engineering_options() if options is None else options,
                      indent=2, sort_keys=True).encode()
    members = [("options.json", body, 0o644)] + [
        (name, seed["data"][name], CORE_SEED_MODE) for name in CORE_SEED_FILES]
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data, mode in members:
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), mode, 0
            archive.addfile(info, io.BytesIO(data))
    return raw.getvalue()


def seed_summary(seed):
    return {key: value for key, value in seed.items() if key != "data"}


def environment_names(data):
    return sorted({entry.split("=", 1)[0] for entry in (data.get("Config") or {}).get("Env") or []
                   if isinstance(entry, str)})


def verify_engineering(data, holder_id, image, volume):
    """Engineering runs the recorded image in the holder's namespace with its one volume."""
    config, settings = data.get("HostConfig") or {}, data.get("NetworkSettings") or {}
    if (data.get("Image") != image["config_digest"]
            or (data.get("Config") or {}).get("Image") != image["image"]):
        raise Refusal("running_image_mismatch")
    if (config.get("Memory") != s4.host.memory_bytes(ENGINEERING_MEMORY)
            or config.get("ReadonlyRootfs") is not True):
        raise Refusal("running_container_limits_mismatch")
    if config.get("Privileged") or config.get("CapAdd") or config.get("Devices"):
        raise Refusal("engineering_has_added_privilege")
    if config.get("PortBindings") or config.get("PublishAllPorts") or any(
            bindings for bindings in (settings.get("Ports") or {}).values()):
        raise Refusal("container_publishes_ports")
    if config.get("NetworkMode") != "container:" + holder_id or settings.get("Networks"):
        raise Refusal("engineering_network_not_holder_namespace")
    mounts = [(item.get("Type"), item.get("Name"), item.get("Destination"))
              for item in data.get("Mounts") or []]
    if mounts != [("volume", volume, ENGINEERING_DATA)] or config.get("Binds"):
        raise Refusal("engineering_mounts_not_the_run_volume")
    variables = environment_names(data)
    if FORBIDDEN_VARIABLE in variables or ENGINEERING_TOKEN_VARIABLE not in variables:
        raise Refusal("engineering_token_variable_mismatch")
    if (data.get("State") or {}).get("Running") is not True:
        raise Refusal("container_not_running")
    return {"network_mode": "container:<holder>", "docker_networks": [], "published_ports": [],
            "mounts": ["volume:" + ENGINEERING_DATA], "memory": ENGINEERING_MEMORY,
            "read_only_rootfs": True, "environment_names": variables}


def control_tree(component, listing=CONTROL_TREE_LIST):
    """Component 2.2.1 as read must be the committed S7 listing, file for file."""
    lines = Path(listing).read_text(encoding="utf-8").splitlines()
    source = lane.COMPONENT_SOURCES["2.2.1"]
    if not lines or lines[0] != "commit " + source["commit"]:
        raise Refusal("control_tree_listing_mismatch")
    listed = set()
    for line in lines[1:-1]:
        match = LIST_LINE.fullmatch(line)
        if match is None:
            raise Refusal("control_tree_listing_mismatch")
        listed.add((match.group(4), int(match.group(2)), match.group(1)))
    read = {(row["path"], row["size"], row["sha256"]) for row in component["records"]}
    if read != listed or len(read) != len(component["records"]):
        raise Refusal("control_tree_listing_mismatch")
    footer = lines[-1]
    if not footer.startswith(f"FILES {len(read)} ") or not footer.endswith(
            "MISSING 0 SHA1_MISMATCH 0"):
        raise Refusal("control_tree_listing_mismatch")
    return {"listing": listing.relative_to(ROOT).as_posix() if Path(listing).is_relative_to(ROOT)
            else Path(listing).name,
            "listing_sha256": hashlib.sha256(Path(listing).read_bytes()).hexdigest(),
            "files": len(read), "equal": True}


def control_requirements(candidate_built):
    """Component 2.2.1's three requirements, as the 9.2.0 inventory resolved them (S5).

    2.2.1 names no ha-mcp pin; its other three requirements are the first
    three of 9.2.0's, so the committed inventory-3 rows answer for it.
    """
    rows = candidate_built["requirements"][:3]
    if tuple(row.get("requirement") for row in rows) != lane.COMPONENT_REQUIREMENTS["2.2.1"]:
        raise Refusal("control_requirements_not_in_inventory")
    if any(row.get("source") == "missing" for row in rows):
        raise Refusal("profile_b_not_feasible")
    return rows


def control_resolved_check(stdout):
    """The control Core resolves the two wheels from the overlay, ``mcp`` from the image,
    and no ``ha_mcp`` at all."""
    try:
        value = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        value = None
    if not isinstance(value, dict) or not isinstance(value.get("distributions"), dict):
        return {"readable": False}, "overlay_not_resolved_in_core"
    found, paths = value["distributions"], value.get("core_python_path")
    summary = {"readable": True, "distributions": found, "core_python_path": paths}
    for pin in lane.N32_WHEELS:
        name = pin["requirement"].split(">=")[0]
        if found.get(name) != {"version": pin["version"], "location": lane.OVERLAY_TARGET}:
            return summary, "overlay_not_resolved_in_core"
    if found.get("ha-mcp") is not None:
        return summary, "control_core_resolves_ha_mcp"
    mcp = found.get("mcp")
    if not isinstance(mcp, dict) or mcp.get("location") == lane.OVERLAY_TARGET:
        return summary, "overlay_not_resolved_in_core"
    if paths != "unreadable" and paths != [lane.OVERLAY_TARGET]:
        return summary, "overlay_not_resolved_in_core"
    return summary, None


def resolved_for(version, stdout):
    if version == CANDIDATE:
        return lane.resolved_check(stdout, CANDIDATE)
    return control_resolved_check(stdout)


def component_phase(section, wanted, token, version, phase, setup_dir, setup):
    """The lane's component phase with the setup's component named explicitly."""
    component, name = COMPONENT_OF[version], f"component-{phase}.json"
    done = command("docker", "exec", "--env", lane.TOKEN_VARIABLE, wanted["holder"], "python",
                   "-I", "-B", "-c", lane.COMPONENT_CHECK, lane.CORE_URL,
                   "setup" if setup else "status", str(lane.RUNNING_SECONDS),
                   "/out/profile-b/" + name, env=lane.with_token(token), check=False,
                   timeout=lane.RUNNING_SECONDS + 120)
    try:
        report = s4.read_json(setup_dir / "out" / "profile-b" / name)
    except (OSError, ValueError):
        report = None
    section.update(phase=phase, setup=setup, check_exit=done.returncode, file=name)
    section["component"], component_failure = lane.component_check(report, component)
    resolved = command("docker", "exec", "--workdir", "/", wanted["core"], "python3", "-P", "-B",
                       "-c", lane.RESOLVED, *lane.RESOLVED_NAMES, check=False, timeout=120)
    section["resolved"], resolved_failure = resolved_for(version, resolved.stdout)
    logs = command("docker", "logs", wanted["core"], check=False, timeout=120)
    section["core_log"], log_failure = lane.core_log_check(
        (logs.stdout + logs.stderr).decode("utf-8", "replace"))
    failure = log_failure or component_failure or resolved_failure or (
        "component_setup_failed" if done.returncode else None)
    if failure:
        raise Refusal(failure)
    return section


def start_peer(section, version, wanted, run_id, upstream, holder_id, token, setup_dir):
    holder, peer = wanted["holder"], wanted["peer"]
    section["network_before_peer"] = lane.posture(wanted)
    command(*peer_argv(peer, run_id, holder, upstream["image"]), env=lane.with_token(token))
    data = s4.inspect_owned(peer, run_id) or {}
    section["peer_network"] = s4.verify_peer(data, holder_id, upstream)
    section["peer_flags"] = lane.peer_flags(data)
    endpoint = upstream_url(version, s4.PEER_PORT)
    command(*s4.exec_argv(holder, "-c", s4.WAIT_HTTP, endpoint, "120", "any"), timeout=150)
    (setup_dir / "out" / "catalog").mkdir(mode=0o700)
    command(*s4.exec_argv(holder, "/lane/scripts/mcp_raw_observation.py", "--endpoint",
                          endpoint, "--output", "/out/catalog/peer.json"), timeout=300)
    section["peer_catalog"] = catalog_check(setup_dir / "out" / "catalog" / "peer.json", version)
    return section


def start_proxy(section, wanted):
    """The recording proxy, inside the holder, between Engineering and the peer."""
    command("docker", "exec", "--detach", wanted["holder"], "python", "-I", "-B", HOLDER_SCRIPT,
            "proxy", "--listen", str(PROXY_PORT), "--target", str(s4.PEER_PORT),
            "--out", "/out/proxy", "--seconds", str(lane.HOLDER_SECONDS))
    command(*s4.exec_argv(wanted["holder"], "-c", s4.WAIT_HTTP,
                          f"http://127.0.0.1:{PROXY_PORT}/", "60", "any"), timeout=90)
    section["proxy"] = {"listen": f"127.0.0.1:{PROXY_PORT}",
                        "target": f"127.0.0.1:{s4.PEER_PORT}", "records": "out/proxy",
                        "readiness_probe": "one GET / before Engineering starts, recorded"}
    return section


def start_engineering(section, version, wanted, run_id, holder_id, image, token, seed):
    url = upstream_url(version, PROXY_PORT)
    command("docker", "volume", "create", *labels_argv(run_id), wanted["engineering_volume"])
    command(*engineering_argv(wanted["engineering"], run_id, wanted["holder"],
                              wanted["engineering_volume"], image["image"], url),
            env=engineering_token_environment(token))
    lane.stream_in(["docker", "cp", "-", f"{wanted['engineering']}:{ENGINEERING_DATA}"],
                   options_archive(seed))
    command("docker", "start", wanted["engineering"])
    section["engineering_container"] = verify_engineering(
        s4.inspect_owned(wanted["engineering"], run_id) or {}, holder_id, image,
        wanted["engineering_volume"])
    section["engineering_upstream_url"] = url
    section["engineering_environment"] = [list(item) for item in engineering_environment(url)]
    section["engineering_options"] = engineering_options()
    section["engineering_core_registry_seed"] = seed_summary(seed)
    section["network_after_engineering"] = lane.posture(wanted)
    command(*s4.exec_argv(wanted["holder"], "-c", s4.WAIT_HTTP, ENGINEERING_READY,
                          str(READY_SECONDS), "ok"), timeout=READY_SECONDS + 30)
    return section


def read_rows(index):
    """The receipt's rows of one setup: the call, Engineering's class and the raw upstream."""
    rows = []
    for row in index.get("reads") or []:
        rows.append({key: row.get(key) for key in (
            "id", "version", "tool", "arguments", "result_class", "withheld",
            "exchange_failure", "engineering_exchange_sha256", "upstream_wire_arguments",
            "other_upstream_traffic", "note", "note_confirmed_by_response")}
            | {key: row[key] for key in ("search_descriptor_variant", "warning_codes")
               if key in row}
            | {"upstream_raw_response_sha256": [
                item.get("raw_response_sha256") for item in row.get("upstream") or []],
               "upstream": row.get("upstream")})
    return rows


def run_reads(section, version, wanted, setup_dir):
    done = command(*s4.exec_argv(
        wanted["holder"], HOLDER_SCRIPT, "reads", "--endpoint", ENGINEERING_ENDPOINT,
        "--seed", "/out/seed/seed.json", "--proxy-dir", "/out/proxy", "--out", "/out/reads",
        "--version", version), check=False, timeout=READS_SECONDS)
    try:
        index = s4.read_json(setup_dir / "out" / "reads" / "_index.json")
    except (OSError, ValueError):
        raise Refusal("reads_index_unreadable") from None
    section["health"] = index.get("health")
    section["reads"] = read_rows(index)
    classes = {}
    for row in section["reads"]:
        classes[row["result_class"]] = classes.get(row["result_class"], 0) + 1
    section["reads_summary"] = {"reads": len(section["reads"]), "result_classes": classes,
                                "stop": index.get("stop"), "exit": done.returncode}
    if index.get("stop"):
        raise Refusal(index["stop"] if re.fullmatch(r"[a-z0-9_]{1,64}", str(index["stop"]))
                      else "reads_stopped")
    if done.returncode or len(section["reads"]) != 27:
        raise Refusal("reads_step_failed")
    return section


def require_running(name, run_id, failure):
    state = (s4.inspect_owned(name, run_id) or {}).get("State") or {}
    if state.get("Running") is not True or state.get("OOMKilled"):
        raise Refusal(failure)
    return {"running": True, "oom_killed": False}


def volume_owned(name, run_id):
    return lane.volume_owned(name, run_id)


def cleanup(wanted, run_id):
    keys = container_keys(wanted)
    for key in keys:
        if s4.inspect_owned(wanted[key], run_id) is not None:
            command("docker", "rm", "-f", "-v", wanted[key])
    for key in ("volume", "engineering_volume"):
        if volume_owned(wanted[key], run_id):
            command("docker", "volume", "rm", wanted[key])
    for key in keys:
        if s4.inspect_owned(wanted[key], run_id) is not None:
            raise Refusal("owned_container_remains")
    for key in ("volume", "engineering_volume"):
        if volume_owned(wanted[key], run_id):
            raise Refusal("owned_volume_remains")
    return {"clean": True, "containers_absent": True, "volumes_absent": True,
            "docker_networks_created": 0}


def run_setup(section, version, ctx, tokens):
    """One setup from an empty namespace to its cleanup; ``section`` is in the receipt."""
    wanted, run_id, run_dir = ctx["names"][version], ctx["run_id"], ctx["run_dir"]
    setup_dir = run_dir / version
    out_dir = setup_dir / "out"
    for path in (setup_dir, out_dir, out_dir / "seed", out_dir / "profile-b"):
        path.mkdir(mode=0o700)
    upstream, core_image, wheels = ctx["upstream"][version], ctx["core_image"], ctx["wheels"]
    component = ctx["components"][version]
    try:
        package = {"files": {}}
        if version == CANDIDATE:
            package = lane.copy_package(wanted["package"], run_id, upstream, CANDIDATE,
                                        ctx["candidate_built"]["package_source"])
            section["package_copy"] = {"source": package["source"],
                                       "records": package["records"]}
        else:
            section["package_copy"] = None
        command(*relabel(lane.idle_holder_argv(wanted["holder"], run_id, out_dir)))
        data = s4.inspect_owned(wanted["holder"], run_id)
        if data is None:
            raise Refusal("holder_missing")
        holder_id = data["Id"]
        network = section["network_posture"] = {"holder": s4.verify_holder(
            data, lane.namespace_probe(wanted["holder"], "python"), ctx["helper"])}
        section["helper_runtime"] = s4.helper_runtime(wanted["holder"])
        command("docker", "volume", "create", *labels_argv(run_id), wanted["volume"])
        command(*relabel(lane.core_argv(wanted["core"], run_id, wanted["holder"],
                                        wanted["volume"], core_image["image"], overlay=True)))
        lane.stream_in(["docker", "cp", "-", f"{wanted['core']}:{lane.CONFIG_TARGET}"],
                       lane.config_archive(lane.seed_step.CONFIG_FILES))
        archives = section["overlay_archives"] = {}
        streams = (
            ("component", lane.CONFIG_TARGET, lane.tree_archive(
                ["custom_components", lane.COMPONENT_PATH],
                {lane.COMPONENT_PATH + "/" + path: body
                 for path, body in component["files"].items()})),
            ("overlay", "/", lane.tree_archive([lane.OVERLAY_TARGET.lstrip("/")],
                                               lane.overlay_files(wheels, package))))
        for key, target, raw in streams:
            archives[key] = {"into": target, "bytes": len(raw),
                             "sha256": hashlib.sha256(raw).hexdigest()}
            lane.stream_in(["docker", "cp", "-", f"{wanted['core']}:{target}"], raw)
        command("docker", "start", wanted["core"])
        section["core_container"] = lane.verify_core(
            s4.inspect_owned(wanted["core"], run_id) or {}, holder_id, core_image,
            wanted["volume"], overlay=True)
        network["before_seed"] = lane.posture(wanted)
        seeded = command(*s4.exec_argv(wanted["holder"], "/lane/scripts/core_read_replay_seed.py",
                                       "--out", "/out/seed"), check=False,
                         timeout=lane.SEED_SECONDS)
        complete, found = lane.seed_output(seeded.stdout)
        tokens.update({f"{version}:{name}": value for name, value in found.items()})
        section["seed"] = lane.seed_summary(out_dir / "seed" / "seed-report.json")
        if seeded.returncode or not complete or set(found) != set(lane.TOKEN_NAMES):
            raise Refusal("seed_incomplete")
        token = found["administrator"]
        lane.wait_running(wanted["holder"], token)
        network["before_component_setup"] = lane.posture(wanted)
        section["component_phase"] = {}
        component_phase(section["component_phase"], wanted, token, version, "after-setup",
                        setup_dir, setup=True)
        start_peer(section, version, wanted, run_id, upstream, holder_id, token, setup_dir)
        start_proxy(section, wanted)
        start_engineering(section, version, wanted, run_id, holder_id, ctx["engineering"],
                          token, ctx["core_seed"])
        network["before_reads"] = lane.posture(wanted)
        run_reads(section, version, wanted, setup_dir)
        section["engineering_after_reads"] = require_running(
            wanted["engineering"], run_id, "engineering_did_not_stay_running")
        section["peer_after_reads"] = require_running(wanted["peer"], run_id,
                                                      "peer_did_not_stay_running")
        network["after_reads"] = lane.posture(wanted)
    finally:
        for key in container_keys(wanted):
            if s4.inspect_owned(wanted[key], run_id) is not None:
                s4.bounded_logs(wanted[key], setup_dir / (key + ".log"))
        section["cleanup"] = cleanup(wanted, run_id)
    return section


def preflight(args):
    """Everything that can refuse before a container, a volume or the run directory exists."""
    if not __debug__:
        raise Refusal("assertions_required")
    core = lane.core_binding()
    wheels = lane.read_wheels(args.wheel_dir)
    candidate_built = lane.require_profile_b(core, CANDIDATE, wheels)
    control_rows = control_requirements(candidate_built)
    bindings = {version: upstream_binding(version) for version in SETUPS}
    components = {
        CANDIDATE: lane.read_component(args.upstream_git, CANDIDATE,
                                       bindings[CANDIDATE]["source_commit"]),
        CONTROL: lane.read_component(args.upstream_git, COMPONENT_OF[CONTROL])}
    control = control_tree(components[CONTROL])
    total = sum(s4.host.memory_bytes(value) for value in (
        s4.HOLDER_MEMORY, lane.CORE_MEMORY, s4.PEER_MEMORY, ENGINEERING_MEMORY))
    if total > s4.host.memory_bytes(lane.MEMORY_CEILING):
        raise Refusal("container_memory_above_ceiling")
    revision = s4.require_clean_checkout()
    engineering = engineering_image()
    seed = core_seed()
    helper = s4.cached_image(s4.HELPER_IMAGE, s4.HELPER_LABELS)
    core_image = s4.cached_image(core["image"], lane.CORE_LABELS)
    upstream = {version: s4.cached_image(bindings[version]["image"], bindings[version]["labels"])
                for version in SETUPS}
    figures = s4.host_figures()
    s4.require_host_floors(figures)
    all_names = {version: names(version) for version in SETUPS}
    s4.require_names_free({f"{v}:{k}": n for v, wanted in all_names.items()
                           for k, n in wanted.items() if not k.endswith("volume")})
    for wanted in all_names.values():
        for key in ("volume", "engineering_volume"):
            lane.require_volume_free(wanted[key])
    return {"core": core, "wheels": wheels, "candidate_built": candidate_built,
            "control_rows": control_rows, "bindings": bindings, "components": components,
            "control_tree": control, "revision": revision, "engineering": engineering,
            "core_seed": seed,
            "helper": helper, "core_image": core_image, "upstream": upstream,
            "figures": figures, "names": all_names, "memory_total_bytes": total}


INTERPRETATIONS = (
    "host network: Core, the peer, the recording proxy and Engineering share the holder's "
    "loopback-only namespace and reach each other on 127.0.0.1; Docker refuses --add-host "
    "with a container network and the host's own network would give Core a route out",
    "the upstream URL Engineering is given names the recording proxy (127.0.0.1:18087), "
    "which passes every exchange to the peer (127.0.0.1:18086) unchanged; the raw upstream "
    "response sha256 is of the bytes the peer sent",
    "the control Core has component 2.2.1 and the two N32 wheels and no ha_mcp package: "
    "2.2.1 names no ha-mcp pin, so none is copied and the run refuses if Core resolves one",
    "the control's three requirements are answered by the committed S5 inventory-3 rows of "
    "9.2.0, whose first three requirements are 2.2.1's; no new inventory ran",
    "withheld: every *_withheld, *_dropped_by_policy and *_replaced counter Engineering's "
    "result carries, to depth 3, by key; values are counts only",
    "the 8.5.0 catalog must lack the three disabled tools; whether it advertises "
    "ha_config_get_yaml is recorded, not required",
    "whether Engineering admits each peer catalog under the D1 flags was not known before "
    "this run; each read's result class shows it",
    "r12 reads the default Core blueprint homeassistant/motion_light.yaml in get mode",
    "Engineering's /data holds the Core registry cache and .lifecycle witness its own "
    "refresh() wrote offline from the registry blob at 917bfa2a (seeding (a)): same "
    "verification and write path, local transport instead of GitHub; in the lane its "
    "refresh has no route out and fails, which keeps the copied cache",
)


def execute(args, launch):
    ctx = preflight(args)
    run_dir = s4.new_run_directory(args.run_dir)
    run_dir.mkdir(mode=0o700, parents=False)  # Exclusive; evidence is never overwritten.
    launch["execution_started"] = True
    run_id = uuid4().hex
    ctx.update(run_dir=run_dir, run_id=run_id)
    result = {
        "model": MODEL, "command": args.command, "result": "WITHHELD", "started": now(),
        "harness_commit": ctx["revision"], "run_id": run_id, "notes": list(args.note or []),
        "ruling": "architect ruling 2026-11-15 17:50 CST (SR contract)",
        "seeding_ruling": "architect ruling 2026-11-16 06:05 CST (seeding (a))",
        "pull": "never", "push": "never",
        "bindings": {"core": ctx["core"], **{v: {k: b[k] for k in sorted(b) if k != "labels"}
                                             for v, b in ctx["bindings"].items()}},
        "components": {v: {"source": c["source"], "manifest": c["manifest"],
                           "records": c["records"]} for v, c in ctx["components"].items()},
        "component_of": dict(COMPONENT_OF), "control_tree": ctx["control_tree"],
        "control_requirements": ctx["control_rows"],
        "candidate_requirements": ctx["candidate_built"]["requirements"],
        "wheels": {"wheels": ctx["wheels"]["wheels"], "records": ctx["wheels"]["records"]},
        "engineering_image": ctx["engineering"], "helper_image": ctx["helper"],
        "core_registry_seed": seed_summary(ctx["core_seed"]),
        "core_image": ctx["core_image"], "upstream_images": ctx["upstream"],
        "host_at_start": ctx["figures"],
        "container_memory": {"holder": s4.HOLDER_MEMORY, "core": lane.CORE_MEMORY,
                             "peer": s4.PEER_MEMORY, "engineering": ENGINEERING_MEMORY,
                             "total_bytes": ctx["memory_total_bytes"],
                             "ceiling": lane.MEMORY_CEILING, "one_setup_at_a_time": True},
        "peer_environment": [list(item) for item in peer_environment()],
        "peer_environment_by_name_only": [lane.TOKEN_VARIABLE],
        "engineering_environment_by_name_only": [ENGINEERING_TOKEN_VARIABLE],
        "engineering_never_given": [FORBIDDEN_VARIABLE],
        "interpretations": list(INTERPRETATIONS), "not_replayed": dict(NOT_REPLAYED),
        "pass_means": ("both setups ran to their end, every posture check held, r12 succeeded "
                       "in get mode, r27 was confirmed not replayable by its response, "
                       "nothing stored carries a token and cleanup is proven; any other read "
                       "Engineering answered with an error is a recorded result, not a failure"),
        "setups": {}, "cleanup": {"clean": False}}
    tokens = {}

    def interrupted(_signum, _frame):
        raise Refusal("lane_interrupted")
    previous = signal.signal(signal.SIGTERM, interrupted)
    started = time.monotonic()
    try:
        for version in SETUPS:
            result["setups"][version] = {"component": COMPONENT_OF[version]}
            run_setup(result["setups"][version], version, ctx, tokens)
        result["result"] = "PASS"
    except BaseException as exc:  # WITHHELD, with a fixed token only.
        result.update(result="WITHHELD", failure_type=type(exc).__name__,
                      failure=str(exc) if isinstance(exc, Refusal) else "unexpected_error")
    finally:
        try:
            for wanted in ctx["names"].values():
                cleanup(wanted, run_id)
            result["cleanup"] = {"clean": True, "setups": len(result["setups"])}
        except BaseException as exc:
            result.update(result="WITHHELD",
                          cleanup={"clean": False, "failure_type": type(exc).__name__})
        try:
            scan = lane.secret_scan(run_dir, tokens)
            result["secret_scan"] = scan
            if scan["files_removed_for_a_secret"] or scan["links"]:
                result.update(result="WITHHELD", failure="secret_or_link_in_stored_files")
        except BaseException as exc:
            result.update(result="WITHHELD", failure="stored_files_not_scanned",
                          failure_type=type(exc).__name__)
        result.update(ended=now(), seconds=round(time.monotonic() - started, 1),
                      host_at_end=s4.host_figures())
        receipt = canonical(result)
        if any(needle in receipt for needle in lane.secret_needles(tokens)):
            receipt = canonical({"model": MODEL, "command": args.command, "result": "WITHHELD",
                                 "failure": "secret_reached_the_receipt", "run_id": run_id,
                                 "cleanup": result["cleanup"]})
            result.update(result="WITHHELD", failure="secret_reached_the_receipt")
        (run_dir / "receipt.json").write_bytes(receipt)
        lane.write_sums(run_dir)
        signal.signal(signal.SIGTERM, previous)
    launch.update(result=result["result"], failure=result.get("failure"),
                  cleanup_clean=result["cleanup"]["clean"])
    return 0 if result["result"] == "PASS" and result["cleanup"]["clean"] else 1


COMMANDS = {"build": build, "run": execute}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("build")
    sub.add_argument("--log", type=Path, required=True)
    sub.add_argument("--launch-log", type=Path, required=True)
    sub.add_argument("--note", action="append")
    sub = commands.add_parser("run")
    sub.add_argument("--run-dir", type=Path, required=True)
    sub.add_argument("--launch-log", type=Path, required=True)
    sub.add_argument("--wheel-dir", type=Path, required=True)
    sub.add_argument("--upstream-git", type=Path, required=True)
    sub.add_argument("--note", action="append")
    args = parser.parse_args(argv)
    launch = {"command": args.command, "started": now(), "execution_started": False,
              "notes": list(args.note or [])}
    try:
        code = COMMANDS[args.command](args, launch)
    except Refusal as exc:
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
