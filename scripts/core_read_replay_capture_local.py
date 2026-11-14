"""Local read capture against a disposable Home Assistant Core (HAMCP-371, S5/S6).

The lane starts one disposable Core 2026.11.0 from its reviewed digest, seeds
it, starts one pinned ha-mcp image at a time against it and stores the raw
result of every delegated read, called with the wire arguments Engineering's
adapter builds for the same public call.

Network posture: one network namespace with a loopback interface and nothing
else.  An idle holder container from the Engineering image is started with
``--network none``; the disposable Core and the ha-mcp peer join that
namespace with ``--network container:<holder>``; every client runs by
``docker exec`` inside the holder.  No Docker network is created, no port is
published and no route leaves the namespace at any point, for Core included.
The lane verifies this on the running containers before the component is set
up and again before the first capture, and refuses otherwise.

Profile A is a bare Core.  Profile B adds the ``ha_mcp_tools`` component tree
of the version under capture, read by ``git archive`` from a local clone at
the bound source commit, with the ``ha_mcp`` package supplied from the pinned
image.  Requirements the pinned image cannot supply must already be in the
Core image: the lane refuses profile B otherwise and never adds a route.
The committed image inventory shows two such requirements missing, so
``run --profile B`` refuses at preflight and the overlay is not built here.

``recapture`` is profile A for R1 and R2 of 2026-11-14: both versions on one
Core that is restarted once, the order cases with each version before that
restart, then each version back to back with the capture, forced and order
cases after it.  ``run`` never selects the forced or order cases.

No pull happens here (``--pull never`` everywhere).  A preflight refusal
happens before any container, volume or run directory exists.  A failure
after that point is recorded as WITHHELD and is not a reason to run again for
a pass.  The Home Assistant tokens are minted in the disposable Core and held
in the memory of the host process; the administrator token is also in the
environment of the peer container, passed by name.  They are written to no
file and no receipt, every stored file is scanned for them, and they stop
working when the Core and its volume are removed.

Everything a container or the upstream clone produces is untrusted data: it is
written under the run directory or streamed into the disposable Core, parsed
as JSON or as a tar stream at most, and never executed on the host.  Standard
library only.
"""

import argparse
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
import core_lane_bindings  # noqa: E402
import core_read_replay_calls as calls_step  # noqa: E402
import core_read_replay_seed as seed_step  # noqa: E402
import upstream_catalog_capture_local as s4  # noqa: E402
from upstream_catalog_capture_local import Refusal, canonical, command, now  # noqa: E402

MODEL = "hamcp371-core-read-replay-capture-local-v1"
PROJECT = "hamcp371-s6"
CORE_VERSION = "2026.11.0"
CORE_REPOSITORY = "ghcr.io/home-assistant/home-assistant"
CORE_LABELS = {"io.hass.version": CORE_VERSION, "io.hass.type": "core",
               "org.opencontainers.image.source": "https://github.com/home-assistant/core"}
VERSIONS = (s4.BASELINE_VERSION, s4.CANDIDATE_VERSION)
INSPECT_MEMORY = "64m"
# What the component manifest of each version requires (step S2, read from
# upstream source).  The lane compares this with the manifest inside the
# component tree it loads and refuses a difference.
COMPONENT_REQUIREMENTS = {
    version: ("mcp>=1.24.0", "ruamel.yaml>=0.18.0", "voluptuous-openapi>=0.4.1",
              "ha-mcp==" + version)
    for version in VERSIONS
}
COMPONENT_PATH = "custom_components/ha_mcp_tools"
PACKAGE_SOURCE = "/app/src/ha_mcp/"
DISTRIBUTION = re.compile(
    r"/lib/python(3\.[0-9]+)/site-packages/([A-Za-z0-9_.]+)-([0-9][A-Za-z0-9_.+!]*)\.dist-info/METADATA$")
DIST_INFO = re.compile(
    r"/site-packages/([A-Za-z0-9_.]+)-([0-9][A-Za-z0-9_.+!]*)\.dist-info(?:/|$)")
REQUIREMENT = re.compile(r"([A-Za-z0-9_.\-]+)(>=|==)([0-9]+(?:\.[0-9]+)*)")
PACKAGE_METADATA = re.compile(
    r"/site-packages/ha_mcp-[0-9][A-Za-z0-9_.+!]*\.dist-info/METADATA$")
METADATA_BYTES = 262144
REQUIRES_ROWS = 200
REQUIRES = re.compile(
    r"Requires-Dist:[ \t]*([A-Za-z0-9][A-Za-z0-9._\-]*)[ \t]*(\[[^\]]*\])?[ \t]*([^;]*?)[ \t]*"
    r"(?:;[ \t]*(.*?))?[ \t]*")
SPECIFIER = re.compile(r"[0-9A-Za-z.,<>=!~*() ]*")
MARKER = re.compile(r"[0-9A-Za-z._<>=!~\"' ()]*")
EXPORT_SECONDS = 900
UPSTREAM_DISTRIBUTIONS = ("ha-mcp", "fastmcp", "mcp")


def normalized(name):
    """Distribution names compare case-insensitively with ``-``, ``_`` and ``.`` alike."""
    return re.sub(r"[-_.]+", "-", name).lower()


def version_key(text):
    """Leading numeric release segments only; anything else is not compared."""
    match = re.match(r"[0-9]+(?:\.[0-9]+)*", text)
    if not match:
        raise Refusal("distribution_version_unreadable")
    return tuple(int(part) for part in match.group(0).split("."))


def parse_requirement(text):
    match = REQUIREMENT.fullmatch(text)
    if not match:
        raise Refusal("component_requirement_form_not_reviewed")
    return normalized(match.group(1)), match.group(2), match.group(3)


def satisfied(requirement, installed):
    """Whether ``installed`` (a version string or None) meets one requirement."""
    _, operator, wanted = parse_requirement(requirement)
    if installed is None:
        return False
    have, want = version_key(installed), version_key(wanted)
    width = max(len(have), len(want))
    have, want = have + (0,) * (width - len(have)), want + (0,) * (width - len(want))
    return have == want if operator == "==" else have >= want


def requirement_report(version, core_distributions):
    """Where each manifest requirement of the component would come from.

    ``ha-mcp==<version>`` is supplied from the pinned image (22:59 record,
    item 4).  Every other requirement must already be met by the Core image;
    nothing is fetched and no route is added.
    """
    rows, missing = [], []
    for requirement in COMPONENT_REQUIREMENTS[version]:
        name = parse_requirement(requirement)[0]
        installed = core_distributions.get(name)
        if name == "ha-mcp":
            source = "pinned ha-mcp image"
        elif satisfied(requirement, installed):
            source = "core image"
        else:
            source = "missing"
            missing.append(requirement)
        rows.append({"requirement": requirement, "core_image_version": installed,
                     "source": source})
    return {"version": version, "requirements": rows, "missing": missing,
            "profile_b_feasible_without_a_route": not missing}


def require_profile_b_feasible(report):
    if report["missing"]:
        raise Refusal("profile_b_core_requirement_missing")


def package_requires(text):
    """The ``Requires-Dist`` header rows of one METADATA text, as bounded data.

    A specifier or marker outside the plain character set is recorded as
    ``unreadable``; nothing here is evaluated.
    """
    rows = []
    for line in text.split("\n\n", 1)[0].splitlines():
        match = REQUIRES.fullmatch(line.rstrip("\r"))
        if not match or len(rows) >= REQUIRES_ROWS:
            continue
        name, _extras, specifier, marker = match.groups()
        rows.append({
            "name": normalized(name),
            "specifier": specifier if SPECIFIER.fullmatch(specifier) else "unreadable",
            "marker": None if marker is None else (
                marker if MARKER.fullmatch(marker) else "unreadable")})
    return rows


def package_requirement_report(rows, core_distributions):
    """Which declared requirements of the package the Core image does not carry by name.

    Rows that apply only to an extra are listed but not compared.  Versions
    are shown next to the specifier and are not evaluated against it.
    """
    out, absent = [], []
    for row in rows:
        optional = row["marker"] is not None and "extra" in row["marker"]
        installed = core_distributions.get(row["name"])
        out.append({**row, "core_image_version": installed, "extra_only": optional})
        if not optional and installed is None and row["name"] not in absent:
            absent.append(row["name"])
    return {"declared": len(rows), "rows": out, "absent_from_core_image": sorted(absent),
            "specifiers_evaluated": False}


def scan_export(stream):
    """Names and versions only, from a container export stream.

    Members are read from the stream; nothing is extracted to disk or executed.
    The one member whose content is read is the METADATA text of the ``ha-mcp``
    distribution, for its ``Requires-Dist`` rows.
    A distribution counts when its METADATA member is a regular file or a hard
    link (an export stream writes the second name of one inode as a link).  A
    dist-info directory seen without such a member is listed, not counted.
    """
    pythons, distributions, manifests, seen = set(), {}, [], set()
    kinds = {"regular": 0, "hard_link": 0}
    package_files = package_bytes = 0
    requires = None
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            path = "/" + member.name.lstrip("./")
            named = DIST_INFO.search(path)
            if named:
                seen.add(normalized(named.group(1)))
            match = DISTRIBUTION.search(path)
            if match and (member.isreg() or member.islnk()):
                kinds["regular" if member.isreg() else "hard_link"] += 1
                pythons.add(match.group(1))
                distributions[normalized(match.group(2))] = match.group(3)
            if (PACKAGE_METADATA.search(path) and member.isreg()
                    and member.size <= METADATA_BYTES and requires is None):
                requires = package_requires(
                    archive.extractfile(member).read().decode("utf-8", "replace"))
            if path.endswith("/" + COMPONENT_PATH + "/manifest.json"):
                manifests.append(path)
            if path.startswith(PACKAGE_SOURCE) and (member.isreg() or member.islnk()):
                package_files += 1
                package_bytes += member.size
    return {"python": sorted(pythons), "distribution_count": len(distributions),
            "distributions": dict(sorted(distributions.items())),
            "metadata_members": kinds,
            "dist_info_without_metadata_file": sorted(seen - set(distributions)),
            "component_manifests_in_image": sorted(manifests),
            "package_requires": requires,
            "package_source": {"path": PACKAGE_SOURCE, "files": package_files,
                               "bytes": package_bytes}}


def require_scan_complete(scans):
    """Refuse a requirement answer that rests on a distribution the scan could not count."""
    relevant = {normalized(parse_requirement(text)[0])
                for version in VERSIONS for text in COMPONENT_REQUIREMENTS[version]}
    relevant |= set(UPSTREAM_DISTRIBUTIONS)
    for found in scans.values():
        if relevant & set(found["dist_info_without_metadata_file"]):
            raise Refusal("distribution_scan_incomplete")


def labels_argv(run_id):
    return ["--label", f"{s4.LANE_LABEL}={run_id}", "--label", f"{s4.PROJECT_LABEL}={PROJECT}"]


def core_binding():
    bound = core_lane_bindings.REVIEWED_CORE_BINDINGS.get(CORE_VERSION)
    if bound is None or not bound.lane:
        raise Refusal("core_binding_missing")
    return {"version": CORE_VERSION, "index_digest": bound.image_index_digest,
            "image": f"{CORE_REPOSITORY}@{bound.image_index_digest}",
            "bound_by": "scripts/core_lane_bindings.py"}


def export_scan(name, run_id, image):
    """Scan one image's file system from a container that is created and never started."""
    command("docker", "create", "--pull", "never", "--name", name, *labels_argv(run_id),
            "--network", "none", "--memory", INSPECT_MEMORY, image["image"])
    try:
        process = subprocess.Popen(["docker", "export", name], cwd=ROOT,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            found = scan_export(process.stdout)
        except (tarfile.TarError, OSError):
            raise Refusal("image_export_unreadable") from None
        finally:
            process.stdout.close()
            exported = process.wait(timeout=EXPORT_SECONDS)
        if exported:
            raise Refusal("image_export_failed")
        state = (s4.inspect_owned(name, run_id) or {}).get("State") or {}
        if state.get("Status") != "created":
            raise Refusal("inspection_container_was_started")
    finally:
        if s4.inspect_owned(name, run_id) is not None:
            command("docker", "rm", "-f", "-v", name)
    return found


def inventory(args, launch):
    """Read distribution names and versions from the three bound images; starts nothing."""
    revision = s4.require_clean_checkout()
    core = core_binding()
    images = {"core": s4.cached_image(core["image"], CORE_LABELS)}
    bindings = {"core": core}
    for version in VERSIONS:
        bound = s4.binding(version)
        bindings[version] = {key: bound[key] for key in sorted(bound)
                             if key not in ("labels", "image")}
        images[version] = s4.cached_image(bound["image"], bound["labels"])
    figures = s4.host_figures()
    s4.require_host_floors(figures)
    wanted = {key: f"{PROJECT}-inventory-{key.replace('.', '-')}" for key in images}
    s4.require_names_free(wanted)
    run_dir = s4.new_run_directory(args.run_dir)
    run_dir.mkdir(mode=0o700, parents=False)
    launch["execution_started"] = True
    run_id = uuid4().hex
    result = {"model": MODEL, "command": "inventory", "result": "WITHHELD", "started": now(),
              "harness_commit": revision, "run_id": run_id, "notes": list(args.note or []),
              "bindings": bindings, "images": images, "host_at_start": figures,
              "pull": "never", "containers_started": 0,
              "method": "docker create, docker export read as a stream, docker rm; "
                        "no container is started"}

    def interrupted(_signum, _frame):
        raise Refusal("lane_interrupted")
    previous = signal.signal(signal.SIGTERM, interrupted)
    started = time.monotonic()
    try:
        scans = {key: export_scan(wanted[key], run_id, images[key]) for key in images}
        (run_dir / "inventory.json").write_bytes(canonical(scans))
        require_scan_complete(scans)
        core_distributions = scans["core"]["distributions"]
        result["core_python"] = scans["core"]["python"]
        result["profile_b"] = {version: requirement_report(version, core_distributions)
                               for version in VERSIONS}
        result["component_tree_in_image"] = {
            version: scans[version]["component_manifests_in_image"] for version in VERSIONS}
        result["package_source_in_image"] = {
            version: scans[version]["package_source"] for version in VERSIONS}
        result["package_distribution_in_image"] = {
            version: scans[version]["distributions"].get("ha-mcp") for version in VERSIONS}
        result["upstream_distributions"] = {
            version: {name: scans[version]["distributions"].get(name)
                      for name in UPSTREAM_DISTRIBUTIONS} for version in VERSIONS}
        result["scan"] = {key: {"metadata_members": scans[key]["metadata_members"],
                                "dist_info_without_metadata_file":
                                    scans[key]["dist_info_without_metadata_file"]}
                          for key in scans}
        result["package_requires"] = {
            version: None if scans[version]["package_requires"] is None
            else package_requirement_report(scans[version]["package_requires"],
                                            core_distributions)
            for version in VERSIONS}
        result["upstream_python"] = {version: scans[version]["python"] for version in VERSIONS}
        result["result"] = "PASS"
    except BaseException as exc:  # WITHHELD, with a fixed token only.
        result.update(failure_type=type(exc).__name__,
                      failure=str(exc) if isinstance(exc, Refusal) else "unexpected_error")
    finally:
        remaining = [name for name in wanted.values()
                     if s4.inspect_owned(name, run_id) is not None]
        result.update(cleanup={"clean": not remaining, "containers_absent": not remaining},
                      ended=now(), seconds=round(time.monotonic() - started, 1),
                      host_at_end=s4.host_figures())
        (run_dir / "receipt.json").write_bytes(canonical(result))
        signal.signal(signal.SIGTERM, previous)
    launch.update(result=result["result"], failure=result.get("failure"),
                  cleanup_clean=result["cleanup"]["clean"])
    return 0 if result["result"] == "PASS" and result["cleanup"]["clean"] else 1


# --- The disposable Core, its seed and the captures -------------------------

CORE_URL = seed_step.BASE
CORE_MEMORY, CORE_PIDS = "1536m", 1024
MEMORY_CEILING = "3g"
CONFIG_TARGET = "/config"
HOLDER_SECONDS = 7200
SEED_SECONDS, CALLS_SECONDS, RUNNING_SECONDS = 900, 900, 300
# Engineering's adapter builds the wire arguments.  9.2.0 is not in the
# reviewed registry before step S8, so both versions get the 9.1.2 arguments
# and every stored call says so.
ARGUMENT_POLICY = s4.BASELINE_VERSION
CAPTURE_GROUPS = ("reads", "variants", "errors", "transport")
RESTART_GROUPS = ("after_restart",)
PROFILE_B_GROUPS = ("yaml",)
# ``recapture`` only (R1 and R2, 2026-11-14): the forced VALIDATION_INVALID_JSON
# case and the order cases, which ``run`` never selects.
FORCED_GROUPS = ("forced",)
ORDER_GROUPS = ("order",)
RECAPTURE_GROUPS = CAPTURE_GROUPS + FORCED_GROUPS + ORDER_GROUPS
RECAPTURE_DESIGN = (
    "one disposable Core, seeded once; the order group with each version, one peer at a "
    "time, before one Core restart; after that restart and no other, each version back to "
    "back with the capture, forced and order groups, one peer at a time")
PROFILES = ("A", "B")
PROFILE_B_INVENTORY = ROOT / "docs/evidence/hamcp371/s5/inventory-3/receipt.json"
TOKEN_VARIABLE = "HOMEASSISTANT_TOKEN"
TOKEN = re.compile(r"[A-Za-z0-9._\-]{32,4096}")
TOKEN_NAMES = ("administrator", "ordinary_user")
CONFIG_NAME = re.compile(r"[a-z_]{1,32}\.yaml")
# What the lane sets on the peer.  The token is passed by name only; no
# redaction option or variable is set, so redaction is the image default.
PEER_ENVIRONMENT = (
    ("HOMEASSISTANT_URL", CORE_URL),
    ("MCP_HOST", "127.0.0.1"),
    ("MCP_PORT", str(s4.PEER_PORT)),
    ("MCP_SECRET_PATH", "/mcp"),
    ("HA_MCP_DISABLE_SETTINGS_UI", "true"),
)
WAIT_RUNNING = r"""import json,os,sys,time,urllib.request
url,limit=sys.argv[1],float(sys.argv[2])
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
request=urllib.request.Request(url+'/api/config',
 headers={'Authorization':'Bearer '+os.environ['HOMEASSISTANT_TOKEN']})
end=time.monotonic()+limit
while True:
    try:
        with opener.open(request,timeout=5) as r:
            if json.loads(r.read(1048576)).get('state')=='RUNNING': break
    except (OSError,ValueError,AttributeError): pass
    if time.monotonic()>end: raise SystemExit(1)
    time.sleep(1)
"""
NOT_BUILT = (
    "profile B overlay (component tree and package copy): refused at preflight until the "
    "owner answers how the missing requirements may be supplied",
    "Supervisor facade (form 2): not built; reads that need a Supervisor record what the "
    "peer returns without one",
    "token revocation between calls (P4): not exercised; the unknown-identifier cases only",
)


def lane_names(command_name):
    stem = f"{PROJECT}-{command_name}"
    wanted = {"holder": stem + "-holder", "core": stem + "-core", "volume": stem + "-config"}
    for version in VERSIONS:
        wanted["peer-" + version] = f"{stem}-peer-{version.replace('.', '-')}"
    return wanted


def container_keys(wanted):
    """Removal order: peers, then Core, then the namespace owner."""
    return [*sorted(key for key in wanted if key.startswith("peer-")), "core", "holder"]


def idle_holder_argv(name, run_id, out_dir):
    """The namespace owner: the helper image, idle, with no network at all."""
    argv = ["docker", "run", "--detach", "--pull", "never", "--name", name, *labels_argv(run_id),
            "--network", "none", "--read-only", "--tmpfs", "/tmp", "--memory", s4.HOLDER_MEMORY,
            "--pids-limit", "256", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", f"{os.getuid()}:{os.getgid()}", "--workdir", "/",
            "--mount", f"type=bind,source={ROOT / 'scripts'},target=/lane/scripts,readonly",
            "--mount", (f"type=bind,source={ROOT / 'hass_mcp_engineering_beta'},"
                        "target=/lane/hass_mcp_engineering_beta,readonly"),
            "--mount", f"type=bind,source={out_dir},target=/out",
            "--entrypoint", "python", s4.HELPER_IMAGE, "-I", "-c",
            f"import time; time.sleep({HOLDER_SECONDS})"]
    s4.host.require_memory(argv)
    return argv


def core_argv(name, run_id, holder, volume, image):
    """``docker create`` for the disposable Core; its only network is the holder's namespace."""
    s4.host.memory_bytes(CORE_MEMORY)
    return ["docker", "create", "--pull", "never", "--name", name, *labels_argv(run_id),
            "--network", "container:" + holder, "--memory", CORE_MEMORY,
            "--pids-limit", str(CORE_PIDS), "--security-opt", "no-new-privileges",
            "--env", "TZ=" + seed_step.TIME_ZONE,
            "--mount", f"type=volume,source={volume},target={CONFIG_TARGET}", image]


def peer_argv(name, run_id, holder, image):
    """One pinned ha-mcp image against the disposable Core; the token is named, never written."""
    argv = ["docker", "run", "--detach", "--pull", "never", "--name", name, *labels_argv(run_id),
            "--network", "container:" + holder, "--read-only", "--tmpfs", "/tmp",
            "--tmpfs", "/home/mcpuser/.ha-mcp", "--memory", s4.PEER_MEMORY, "--pids-limit", "256",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
    for key, value in PEER_ENVIRONMENT:
        argv += ["--env", f"{key}={value}"]
    argv += ["--env", TOKEN_VARIABLE, image, s4.PEER_COMMAND]
    s4.host.require_memory(argv)
    return argv


def memory_total():
    return sum(s4.host.memory_bytes(value)
               for value in (s4.HOLDER_MEMORY, CORE_MEMORY, s4.PEER_MEMORY))


def with_token(token):
    """The host environment plus the one variable Docker forwards by name."""
    return {**os.environ, TOKEN_VARIABLE: token}


def config_archive(files):
    """The configuration files as one tar stream, for ``docker cp -``; nothing touches disk."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(files):
            if not CONFIG_NAME.fullmatch(name):
                raise Refusal("configuration_file_name_not_reviewed")
            body = files[name].encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(body), 0o644, 0
            archive.addfile(info, io.BytesIO(body))
    return raw.getvalue()


def stream_in(argv, raw, timeout=120):
    result = subprocess.run(list(argv), cwd=ROOT, input=raw, capture_output=True,
                            timeout=timeout)
    if result.returncode:
        raise Refusal("host_command_failed")


def verify_core(data, holder_id, image, volume):
    """Core runs the bound image in the holder's namespace with one volume and no way out."""
    config, settings = data.get("HostConfig") or {}, data.get("NetworkSettings") or {}
    if (data.get("Image") != image["config_digest"]
            or (data.get("Config") or {}).get("Image") != image["image"]):
        raise Refusal("running_image_mismatch")
    if (config.get("Memory") != s4.host.memory_bytes(CORE_MEMORY)
            or config.get("PidsLimit") != CORE_PIDS):
        raise Refusal("running_container_limits_mismatch")
    if config.get("Privileged") or config.get("CapAdd") or config.get("Devices"):
        raise Refusal("core_has_added_privilege")
    if "no-new-privileges" not in (config.get("SecurityOpt") or []):
        raise Refusal("core_has_added_privilege")
    if config.get("PortBindings") or config.get("PublishAllPorts"):
        raise Refusal("container_publishes_ports")
    if any(bindings for bindings in (settings.get("Ports") or {}).values()):
        raise Refusal("container_publishes_ports")
    if config.get("NetworkMode") != "container:" + holder_id or settings.get("Networks"):
        raise Refusal("core_network_not_holder_namespace")
    mounts = [(item.get("Type"), item.get("Name"), item.get("Destination"))
              for item in data.get("Mounts") or []]
    if mounts != [("volume", volume, CONFIG_TARGET)] or config.get("Binds"):
        raise Refusal("core_mounts_not_the_run_volume")
    if (data.get("State") or {}).get("Running") is not True:
        raise Refusal("container_not_running")
    return {"network_mode": "container:<holder>", "docker_networks": [], "published_ports": [],
            "mounts": ["volume:" + CONFIG_TARGET], "memory": CORE_MEMORY,
            "pids_limit": CORE_PIDS, "privileged": False, "no_new_privileges": True}


def require_no_egress(probe):
    """A namespace with ``lo`` only, no IPv4 route and a refused connection out."""
    if (not isinstance(probe, dict) or probe.get("interfaces") != ["lo"]
            or probe.get("ipv4_routes") != 0):
        raise Refusal("namespace_not_loopback_only")
    if probe.get("egress_errno") != s4.ENETUNREACH:
        raise Refusal("namespace_egress_not_refused")
    return {key: probe[key] for key in ("interfaces", "ipv4_routes", "egress_errno")}


def namespace_probe(container, python):
    result = command("docker", "exec", container, python, "-I", "-c", s4.NET_PROBE)
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise Refusal("namespace_probe_unreadable") from None


def posture(wanted):
    """No egress, as seen from the holder and from inside the Core container itself."""
    return {"seen_from_holder": require_no_egress(namespace_probe(wanted["holder"], "python")),
            "seen_from_core": require_no_egress(namespace_probe(wanted["core"], "python3")),
            "checked": now()}


def seed_output(stdout):
    """The seed step's one line: whether every step passed, and the two tokens."""
    lines = stdout.decode("ascii", "replace").splitlines()
    try:
        value = json.loads(lines[0]) if len(lines) == 1 else None
    except ValueError:
        value = None
    if not isinstance(value, dict) or not isinstance(value.get("tokens"), dict):
        raise Refusal("seed_output_unreadable")
    tokens = value["tokens"]
    if set(tokens) - set(TOKEN_NAMES) or any(
            not isinstance(item, str) or not TOKEN.fullmatch(item) for item in tokens.values()):
        raise Refusal("seed_output_unreadable")
    return value.get("all_steps_ok") is True, dict(tokens)


def seed_summary(path):
    report = s4.read_json(path)
    return {"all_steps_ok": report.get("all_steps_ok") is True,
            "steps": {row.get("step"): row.get("status") for row in report.get("steps") or []},
            "tokens_issued": report.get("tokens_issued")}


def calls_summary(path):
    """Counts only; the payloads stay in their own files."""
    index = s4.read_json(Path(path) / "_index.json")
    rows = index.get("calls") or []
    return {"calls": len(rows),
            "exchange_failures": sum(1 for row in rows if row.get("exchange_failure")),
            "is_error": sum(1 for row in rows if row.get("is_error") is True),
            "sessions_opened": index.get("sessions_opened"),
            "arguments_built_with_policy": ARGUMENT_POLICY}


def run_calls(holder, version, name, groups, out_dir):
    done = command(*s4.exec_argv(
        holder, "/lane/scripts/core_read_replay_calls.py", "--endpoint", s4.ENDPOINT,
        "--seed", "/out/seed/seed.json", "--out", f"/out/{version}/{name}",
        "--policy", ARGUMENT_POLICY, "--groups", ",".join(groups)),
        check=False, timeout=CALLS_SECONDS)
    if done.returncode:
        raise Refusal("calls_step_failed")
    return {"groups": list(groups), **calls_summary(out_dir / version / name)}


def wait_running(holder, token):
    command("docker", "exec", "--env", TOKEN_VARIABLE, holder, "python", "-I", "-B", "-c",
            WAIT_RUNNING, CORE_URL, str(RUNNING_SECONDS), env=with_token(token),
            timeout=RUNNING_SECONDS + 60)


def restart_core(wanted, run_id, holder_id, core_image, token):
    """Restart Core from the host; it must come back in the same namespace, still shut in."""
    command("docker", "restart", "-t", "60", wanted["core"], timeout=180)
    verify_core(s4.inspect_owned(wanted["core"], run_id) or {}, holder_id, core_image,
                wanted["volume"])
    checked = posture(wanted)
    wait_running(wanted["holder"], token)
    return checked


def start_peer(version, wanted, run_id, upstream, holder_id, tokens):
    """Posture first, then the one peer, checked, and its endpoint answering."""
    holder, peer = wanted["holder"], wanted["peer-" + version]
    section = {"network_before_capture": posture(wanted)}
    command(*peer_argv(peer, run_id, holder, upstream["image"]),
            env=with_token(tokens["administrator"]))
    section["peer_network"] = s4.verify_peer(s4.inspect_owned(peer, run_id) or {}, holder_id,
                                             upstream)
    command(*s4.exec_argv(holder, "-c", s4.WAIT_HTTP, s4.ENDPOINT, "120", "any"), timeout=150)
    return section


def remove_peer(version, wanted, run_id, log):
    """The peer must still be running; its log is kept and it is proven gone."""
    peer = wanted["peer-" + version]
    state = (s4.inspect_owned(peer, run_id) or {}).get("State") or {}
    if state.get("Running") is not True or state.get("OOMKilled"):
        raise Refusal("peer_did_not_stay_running")
    s4.bounded_logs(peer, log)
    command("docker", "rm", "-f", "-v", peer)
    if s4.inspect_owned(peer, run_id) is not None:
        raise Refusal("owned_container_remains")


def capture_version(version, wanted, run_id, run_dir, upstream, holder_id, core_image, tokens):
    """One peer at a time: start it, capture, restart Core under it, capture again, remove it."""
    holder, out_dir = wanted["holder"], run_dir / "out"
    section = start_peer(version, wanted, run_id, upstream, holder_id, tokens)
    (out_dir / version).mkdir(mode=0o700)
    section["calls"] = run_calls(holder, version, "calls", CAPTURE_GROUPS, out_dir)
    section["network_after_core_restart"] = restart_core(
        wanted, run_id, holder_id, core_image, tokens["administrator"])
    section["calls_after_core_restart"] = run_calls(
        holder, version, "calls-after-core-restart", RESTART_GROUPS, out_dir)
    remove_peer(version, wanted, run_id, run_dir / f"peer-{version}.log")
    return section


def recapture(wanted, run_id, run_dir, upstream, holder_id, core_image, tokens):
    """R1 and R2: both versions on one Core, the order group either side of its one restart.

    A step that fails raises; nothing is retried, and a later version never
    starts after an earlier one failed.
    """
    holder, out_dir = wanted["holder"], run_dir / "out"
    captures = {"design": RECAPTURE_DESIGN, "before_core_restart": {},
                "after_core_restart": {}}
    for version in VERSIONS:
        section = start_peer(version, wanted, run_id, upstream[version], holder_id, tokens)
        (out_dir / version).mkdir(mode=0o700)
        section["calls"] = run_calls(holder, version, "order-before-core-restart",
                                     ORDER_GROUPS, out_dir)
        remove_peer(version, wanted, run_id, run_dir / f"peer-{version}-before-core-restart.log")
        captures["before_core_restart"][version] = section
    captures["network_after_core_restart"] = restart_core(
        wanted, run_id, holder_id, core_image, tokens["administrator"])
    for version in VERSIONS:
        section = start_peer(version, wanted, run_id, upstream[version], holder_id, tokens)
        section["calls"] = run_calls(holder, version, "calls-after-core-restart",
                                     RECAPTURE_GROUPS, out_dir)
        remove_peer(version, wanted, run_id, run_dir / f"peer-{version}-after-core-restart.log")
        captures["after_core_restart"][version] = section
    return captures


def secret_needles(tokens):
    """Each token whole, and the signature segment of a three-part token."""
    needles = set()
    for value in tokens.values():
        needles.add(value.encode("ascii"))
        parts = value.split(".")
        if len(parts) == 3 and len(parts[2]) >= 16:
            needles.add(parts[2].encode("ascii"))
    return needles


def secret_scan(run_dir, tokens):
    """Read every stored file for the exact token bytes; a file that has them is removed."""
    needles, files, removed, links = secret_needles(tokens), 0, [], []
    for path in sorted(Path(run_dir).rglob("*")):
        name = path.relative_to(run_dir).as_posix()
        if path.is_symlink():
            links.append(name)
        elif path.is_file():
            files += 1
            raw = path.read_bytes()
            if any(needle in raw for needle in needles):
                removed.append(name)
                path.unlink()
    return {"files_scanned": files, "needles": len(needles),
            "method": "exact bytes of each token and of its signature segment",
            "files_removed_for_a_secret": removed, "links": links}


def write_sums(run_dir):
    run_dir = Path(run_dir)
    lines = [f"{s4.sha256_bytes(path.read_bytes())[7:]}  {path.relative_to(run_dir).as_posix()}\n"
             for path in sorted(run_dir.rglob("*"))
             if path.is_file() and not path.is_symlink() and path.name != "SHA256SUMS"]
    (run_dir / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")


def volume_owned(name, run_id):
    """Whether this run's volume exists; False only when it is proven absent."""
    result = command("docker", "volume", "inspect", name, check=False)
    if result.returncode:
        listed = command("docker", "volume", "ls", "--format", "{{.Name}}")
        if name in listed.stdout.decode().split():
            raise Refusal("owned_volume_inspection_failed")
        return False
    data, = json.loads(result.stdout)
    if (data.get("Labels") or {}).get(s4.LANE_LABEL) != run_id:
        raise Refusal("volume_ownership_mismatch")
    return True


def require_volume_free(name):
    listed = command("docker", "volume", "ls", "--format", "{{.Name}}")
    if name in listed.stdout.decode().split():
        raise Refusal("disposable_name_already_exists")


def cleanup(wanted, run_id):
    keys = container_keys(wanted)
    for key in keys:
        if s4.inspect_owned(wanted[key], run_id) is not None:
            command("docker", "rm", "-f", "-v", wanted[key])
    if volume_owned(wanted["volume"], run_id):
        command("docker", "volume", "rm", wanted["volume"])
    for key in keys:
        if s4.inspect_owned(wanted[key], run_id) is not None:
            raise Refusal("owned_container_remains")
    if volume_owned(wanted["volume"], run_id):
        raise Refusal("owned_volume_remains")
    return {"clean": True, "containers_absent": True, "volume_absent": True,
            "docker_networks_created": 0}


def require_profile_b(core, inventory=PROFILE_B_INVENTORY):
    """Profile B's preflight, from the committed image inventory.  Always refuses for now."""
    try:
        receipt = s4.read_json(inventory)
    except (OSError, ValueError):
        raise Refusal("profile_b_inventory_missing") from None
    if (not isinstance(receipt, dict) or receipt.get("command") != "inventory"
            or receipt.get("result") != "PASS"):
        raise Refusal("profile_b_inventory_missing")
    if ((receipt.get("bindings") or {}).get("core") or {}).get("image") != core["image"]:
        raise Refusal("profile_b_inventory_stale")
    for version in VERSIONS:
        report = (receipt.get("profile_b") or {}).get(version)
        if not isinstance(report, dict) or not isinstance(report.get("missing"), list):
            raise Refusal("profile_b_inventory_missing")
        require_profile_b_feasible(report)
    # Nothing past this point exists yet: the overlay is deliberately not built.
    raise Refusal("profile_b_overlay_not_built")


def execute(args, launch):
    """Body of ``selftest``, ``run`` and ``recapture``; everything before mkdir is preflight."""
    if not __debug__:
        raise Refusal("assertions_required")
    capture = args.command in ("run", "recapture")
    core = core_binding()
    if capture and args.profile != "A":
        require_profile_b(core)
    if memory_total() > s4.host.memory_bytes(MEMORY_CEILING):
        raise Refusal("container_memory_above_ceiling")
    revision = s4.require_clean_checkout()
    helper = s4.cached_image(s4.HELPER_IMAGE, s4.HELPER_LABELS)
    core_image = s4.cached_image(core["image"], CORE_LABELS)
    bindings, upstream = {"core": core}, {}
    if capture:
        for version in VERSIONS:
            bound = s4.binding(version)
            bindings[version] = {key: bound[key] for key in sorted(bound)
                                 if key not in ("labels", "image")}
            upstream[version] = s4.cached_image(bound["image"], bound["labels"])
    figures = s4.host_figures()
    s4.require_host_floors(figures)
    wanted = lane_names(args.command)
    s4.require_names_free(wanted)
    require_volume_free(wanted["volume"])
    run_dir = s4.new_run_directory(args.run_dir)
    run_dir.mkdir(mode=0o700, parents=False)  # Exclusive; evidence is never overwritten.
    launch["execution_started"] = True
    out_dir = run_dir / "out"
    out_dir.mkdir(mode=0o700)
    (out_dir / "seed").mkdir(mode=0o700)
    run_id = uuid4().hex
    result = {
        "model": MODEL, "command": args.command, "result": "WITHHELD", "started": now(),
        "harness_commit": revision, "run_id": run_id, "notes": list(args.note or []),
        "profile": getattr(args, "profile", None), "bindings": bindings, "pull": "never",
        "helper_image": helper, "core_image": core_image, "upstream_images": upstream,
        "host_at_start": figures,
        "home_assistant": ("one disposable Core started by this run in a namespace with no "
                           "interface but loopback; removed with its volume at the end"),
        "container_memory": {"holder": s4.HOLDER_MEMORY, "core": CORE_MEMORY,
                             "peer": s4.PEER_MEMORY, "ceiling": MEMORY_CEILING,
                             "one_peer_at_a_time": True},
        "tokens": {"kind": "two long-lived access tokens minted in this disposable Core: its "
                           "owner (administrator) and one ordinary user",
                   "held": "in the memory of the host process and in the environment of the "
                           "peer container; written to no file; gone with the Core",
                   "production_equivalence": "assumed, not established: whether the token "
                                             "ha-mcp is given in production is of the same "
                                             "kind is for the owner to confirm"},
        "redaction": {"setting": "image defaults",
                      "detail": "the lane passes no redaction option or variable to the peer",
                      "peer_environment": [list(item) for item in PEER_ENVIRONMENT],
                      "peer_environment_by_name_only": [TOKEN_VARIABLE],
                      "peer_command": s4.PEER_COMMAND,
                      "production_setting": "not read by any agent; an entry in the upgrade "
                                            "decision record, filled at upgrade time"},
        "pass_means": ("the lane ran to its end, every posture check held, nothing stored "
                       "carries a token and cleanup is proven; a call the peer answered with "
                       "an error or broke is a stored result, counted here, not a failure"),
        "not_built": list(NOT_BUILT), "captures": {}, "cleanup": {"clean": False}}
    tokens = {}

    def interrupted(_signum, _frame):
        raise Refusal("lane_interrupted")
    previous = signal.signal(signal.SIGTERM, interrupted)
    started = time.monotonic()
    try:
        command(*idle_holder_argv(wanted["holder"], run_id, out_dir))
        data = s4.inspect_owned(wanted["holder"], run_id)
        if data is None:
            raise Refusal("holder_missing")
        holder_id = data["Id"]
        network = {"holder": s4.verify_holder(
            data, namespace_probe(wanted["holder"], "python"), helper)}
        result["network_posture"] = network
        result["helper_runtime"] = s4.helper_runtime(wanted["holder"])
        command("docker", "volume", "create", *labels_argv(run_id), wanted["volume"])
        command(*core_argv(wanted["core"], run_id, wanted["holder"], wanted["volume"],
                           core_image["image"]))
        stream_in(["docker", "cp", "-", f"{wanted['core']}:{CONFIG_TARGET}"],
                  config_archive(seed_step.CONFIG_FILES))
        command("docker", "start", wanted["core"])
        result["core_container"] = verify_core(
            s4.inspect_owned(wanted["core"], run_id) or {}, holder_id, core_image,
            wanted["volume"])
        network["before_seed"] = posture(wanted)
        seeded = command(*s4.exec_argv(wanted["holder"], "/lane/scripts/core_read_replay_seed.py",
                                       "--out", "/out/seed"), check=False, timeout=SEED_SECONDS)
        complete, found = seed_output(seeded.stdout)
        tokens.update(found)
        result["seed"] = seed_summary(out_dir / "seed" / "seed-report.json")
        if seeded.returncode or not complete or set(tokens) != set(TOKEN_NAMES):
            raise Refusal("seed_incomplete")
        wait_running(wanted["holder"], tokens["administrator"])
        if args.command == "recapture":
            result["captures"] = recapture(wanted, run_id, run_dir, upstream, holder_id,
                                           core_image, tokens)
        elif capture:
            for version in VERSIONS:
                result["captures"][version] = capture_version(
                    version, wanted, run_id, run_dir, upstream[version], holder_id, core_image,
                    tokens)
        else:
            # Selftest: no upstream image.  The restart and the calls module's own
            # start-up are exercised so the first capture is not their first use.
            network["after_core_restart"] = restart_core(
                wanted, run_id, holder_id, core_image, tokens["administrator"])
            command(*s4.exec_argv(wanted["holder"], "/lane/scripts/core_read_replay_calls.py",
                                  "--help"))
        result["result"] = "PASS"
    except BaseException as exc:  # WITHHELD, with a fixed token only.
        result.update(result="WITHHELD", failure_type=type(exc).__name__,
                      failure=str(exc) if isinstance(exc, Refusal) else "unexpected_error")
    finally:
        try:
            for key in container_keys(wanted):
                target = run_dir / (key + ".log")
                if not target.exists() and s4.inspect_owned(wanted[key], run_id) is not None:
                    s4.bounded_logs(wanted[key], target)
            result["cleanup"] = cleanup(wanted, run_id)
        except BaseException as exc:
            result.update(result="WITHHELD",
                          cleanup={"clean": False, "failure_type": type(exc).__name__})
        try:
            scan = secret_scan(run_dir, tokens)
            result["secret_scan"] = scan
            if scan["files_removed_for_a_secret"] or scan["links"]:
                result.update(result="WITHHELD", failure="secret_or_link_in_stored_files")
        except BaseException as exc:
            result.update(result="WITHHELD", failure="stored_files_not_scanned",
                          failure_type=type(exc).__name__)
        result.update(ended=now(), seconds=round(time.monotonic() - started, 1),
                      host_at_end=s4.host_figures())
        receipt = canonical(result)
        if any(needle in receipt for needle in secret_needles(tokens)):
            receipt = canonical({"model": MODEL, "command": args.command, "result": "WITHHELD",
                                 "failure": "secret_reached_the_receipt", "run_id": run_id,
                                 "cleanup": result["cleanup"]})
            result.update(result="WITHHELD", failure="secret_reached_the_receipt")
        (run_dir / "receipt.json").write_bytes(receipt)
        write_sums(run_dir)
        signal.signal(signal.SIGTERM, previous)
    launch.update(result=result["result"], failure=result.get("failure"),
                  cleanup_clean=result["cleanup"]["clean"])
    return 0 if result["result"] == "PASS" and result["cleanup"]["clean"] else 1


COMMANDS = {"inventory": inventory, "selftest": execute, "run": execute,
            "recapture": execute}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        sub = commands.add_parser(name)
        sub.add_argument("--run-dir", type=Path, required=True)
        sub.add_argument("--launch-log", type=Path, required=True)
        sub.add_argument("--note", action="append")
        if name == "run":
            sub.add_argument("--profile", choices=PROFILES, required=True)
        elif name == "recapture":
            sub.set_defaults(profile="A")  # Profile A only; there is no option.
    args = parser.parse_args(argv)
    launch = {"command": args.command, "run_dir": str(args.run_dir), "started": now(),
              "execution_started": False, "notes": list(args.note or [])}
    try:
        code = COMMANDS[args.command](args, launch)
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
