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

No pull happens here (``--pull never`` everywhere).  A preflight refusal
happens before any container, volume or run directory exists.  A failure
after that point is recorded as WITHHELD and is not a reason to run again for
a pass.  The Home Assistant tokens are minted in the disposable Core, live in
the run directory with mode 0600 until cleanup and are never written to a
receipt; every stored file is scanned for them.

Everything a container or the upstream clone produces is untrusted data: it is
written under the run directory or streamed into the disposable Core, parsed
as JSON or as a tar stream at most, and never executed on the host.  Standard
library only.
"""

import argparse
import json
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


def scan_export(stream):
    """Names and versions only, from a container export stream.

    Members are read from the stream; nothing is extracted to disk or executed.
    A distribution counts when its METADATA member is a regular file or a hard
    link (an export stream writes the second name of one inode as a link).  A
    dist-info directory seen without such a member is listed, not counted.
    """
    pythons, distributions, manifests, seen = set(), {}, [], set()
    kinds = {"regular": 0, "hard_link": 0}
    package_files = package_bytes = 0
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


COMMANDS = {"inventory": inventory}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        sub = commands.add_parser(name)
        sub.add_argument("--run-dir", type=Path, required=True)
        sub.add_argument("--launch-log", type=Path, required=True)
        sub.add_argument("--note", action="append")
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
