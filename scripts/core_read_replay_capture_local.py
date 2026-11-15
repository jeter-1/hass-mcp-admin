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

Profile A is a bare Core.  Profile B (C4 and N32, 2026-11-14) is one version
per run on a Core that also has the ``ha_mcp_tools`` component of the same
version.  The component tree is read object by object from a local clone of
upstream at the version's tag with git plumbing only, every object checked
against its own id, and copied into the disposable Core's ``custom_components``
before Core first starts.  The ``ha_mcp`` package comes from the pinned image
(read from a container that is created and never started) and the two
requirements the Core image lacks come from two hash-pinned wheels (N32).
Both go into a directory of the disposable Core's own file system that the
Core container's ``PYTHONPATH`` names; they go with the container.  No HACS,
no fetch by Core and no route: the Core log is read for any request to
install and the lane refuses one.  The component is then set up through
Core's own config flow and its version read back before the first capture.

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

Everything a container, a wheel or the upstream clone produces is untrusted
data: it is written under the run directory or streamed into the disposable
Core, parsed as JSON, a tar or zip stream or a git object at most, and never
executed on the host.  Standard library only.
"""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tarfile
import time
from uuid import uuid4
import zipfile

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
# Component 2.2.1 ships with ha-mcp 8.5.0 and 8.6.0 and names no ha-mcp pin.
COMPONENT_REQUIREMENTS["2.2.1"] = COMPONENT_REQUIREMENTS[s4.CANDIDATE_VERSION][:3]
COMPONENT_PATH = "custom_components/ha_mcp_tools"
COMPONENT_DOMAIN = "ha_mcp_tools"
COMPONENT_DEPENDENCIES = ["webhook"]
# C4 (2026-11-14): each component from the upstream tag that ships it.  Every
# id was read from the tag object down; the lane walks the same chain and
# refuses a difference anywhere on it.
COMPONENT_SOURCES = {
    "9.2.0": {"tag": "v9.2.0", "tag_object": "95924750d3a1cdbcf6eb9ffe8dbd5a3ed6616fe5",
              "commit": "51e3aac788482a081807591cd5d58d07dc4e540b",
              "root_tree": "377909bd56f80aea959c0eca00014c489febd4c8",
              "tree": "028e8a137dcd6a7fd083a5d63c2886d7b098659d"},
    "9.1.2": {"tag": "v9.1.2", "tag_object": "6cb02b093bfcdfb64ea470bdbdd8d1084efbc455",
              "commit": "bab615dfc90cc510f5e19a9118707d0fae03a37c",
              "root_tree": "85d0ea3c9377fb8d71cc51e7fe45e5052be455f6",
              "tree": "f143f37c81d5c7ddb2a2583db74bb5f69b8fcbc6"},
    "2.2.1": {"tag": "v8.6.0", "tag_object": "64a01e065bf8736c5745afb617e4315297b3ca6c",
              "commit": "fc54437a804858732e4bc927add98e202d879a09",
              "root_tree": "6b726b7501e914ad0a98e77c0fbaf8089b9ce950",
              "tree": "a2b8b98375c709d47360cd1ed2ca0f65d81c67c7"},
}
# C4: a server version is captured only with the component of the same version.
COMPONENT_FOR = {version: version for version in VERSIONS}
COMPONENT_FILES, COMPONENT_BYTES, COMPONENT_DEPTH = 512, 16 * 1024 * 1024, 8
OBJECT_ID = re.compile(r"[0-9a-f]{40}")
GIT_SECONDS = 120
# Git plumbing on the clone runs from ``/`` with this environment and nothing
# else: no lazy fetch, no replace objects, no system or global configuration.
GIT_ENVIRONMENT = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C",
                   "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1",
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                   "GIT_TERMINAL_PROMPT": "0"}
# N32 (2026-11-14): the two requirements the Core image lacks, at the versions
# Core would resolve from the 9.2.0 component manifest, fetched on scriptbox
# with these hashes before any run.  The lane only reads the files.
N32_WHEELS = (
    {"requirement": "ruamel.yaml>=0.18.0", "distribution": "ruamel-yaml", "version": "0.19.1",
     "file": "ruamel_yaml-0.19.1-py3-none-any.whl",
     "sha256": "27592957fedf6e0b62f281e96effd28043345e0e66001f97683aa9a40c667c93",
     "source": ("https://files.pythonhosted.org/packages/b8/0c/51f6841f1d84f404f92463fc2b1ba0da"
                "357ca1e3db6b7fbda26956c3b82a/ruamel_yaml-0.19.1-py3-none-any.whl"),
     "tops": ("ruamel", "ruamel_yaml-0.19.1.dist-info")},
    {"requirement": "voluptuous-openapi>=0.4.1", "distribution": "voluptuous-openapi",
     "version": "0.4.1", "file": "voluptuous_openapi-0.4.1-py3-none-any.whl",
     "sha256": "b30abc527c622cd62dba1269a05099e6b6170678c2838908d347fa85ab0cdb44",
     "source": ("https://files.pythonhosted.org/packages/bc/bb/64db054ad3852e3b63841a570942d3ce"
                "29e6b44979d5ac4be806973a2338/voluptuous_openapi-0.4.1-py3-none-any.whl"),
     "tops": ("voluptuous_openapi", "voluptuous_openapi-0.4.1.dist-info")},
)
WHEEL_BYTES = 8 * 1024 * 1024
WHEEL_SOURCE = "hash-pinned wheel (N32)"
# The other requirements of the ha-mcp package that the Core image lacks.
# N32: Core must not ask for any of them.
OTHER_REQUIREMENTS = (
    "cyclopts", "griffelib", "jsonref", "jsonschema-path", "openapi-pydantic",
    "py-key-value-aio", "pydantic-monty", "pyperclip", "uncalled-for", "watchfiles")
OVERLAY_TARGET = "/opt/hamcp371-overlay"
OVERLAY_ENVIRONMENT = "PYTHONPATH=" + OVERLAY_TARGET
PACKAGE_SOURCE = "/app/src/ha_mcp/"
PACKAGE_DIST_INFO = re.compile(
    r"(.+/lib/python3\.[0-9]+/site-packages)/(ha_mcp-([0-9][A-Za-z0-9_.+!]*)\.dist-info)/(.+)")
SITE_PACKAGE = re.compile(r".+/lib/python3\.[0-9]+/site-packages/ha_mcp/.+")
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


def export_scan(name, run_id, image, parse=scan_export):
    """Read one image's file system from a container that is created and never started."""
    command("docker", "create", "--pull", "never", "--name", name, *labels_argv(run_id),
            "--network", "none", "--memory", INSPECT_MEMORY, image["image"])
    try:
        process = subprocess.Popen(["docker", "export", name], cwd=ROOT,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            found = parse(process.stdout)
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


# --- Profile B sources: the component tree, the package and the two wheels ---

def safe_part(name):
    """One path component the lane will write: no separator, no control character."""
    return (isinstance(name, str) and 0 < len(name) <= 255 and name not in (".", "..")
            and "/" not in name and "\\" not in name
            and not any(ord(char) < 32 or ord(char) == 127 for char in name))


def git_objects(git_dir, ids):
    """Raw objects from a local clone by id, each checked against its own sha1 id.

    ``git cat-file --batch`` from ``/`` with ``GIT_ENVIRONMENT`` only; nothing in
    the clone is checked out or executed and a missing object is never fetched.
    """
    wanted = sorted(set(ids))
    if not wanted or not all(OBJECT_ID.fullmatch(item) for item in wanted):
        raise Refusal("component_object_id_not_reviewed")
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-c", "protocol.allow=never",
             "-c", "core.fsmonitor=false", "--git-dir=" + str(git_dir), "cat-file", "--batch"],
            cwd="/", env=dict(GIT_ENVIRONMENT), capture_output=True, timeout=GIT_SECONDS,
            input="".join(item + "\n" for item in wanted).encode("ascii"))
    except (OSError, subprocess.SubprocessError):
        raise Refusal("upstream_clone_read_failed") from None
    if result.returncode:
        raise Refusal("upstream_clone_read_failed")
    raw, offset, found = result.stdout, 0, {}
    for oid in wanted:
        end = raw.find(b"\n", offset)
        header = raw[offset:end].decode("ascii", "replace").split(" ") if end >= 0 else []
        if header == [oid, "missing"]:
            raise Refusal("component_object_missing")
        if len(header) != 3 or header[0] != oid or not header[2].isdigit():
            raise Refusal("upstream_clone_read_failed")
        size = int(header[2])
        body = raw[end + 1:end + 1 + size]
        if len(body) != size or raw[end + 1 + size:end + 2 + size] != b"\n":
            raise Refusal("upstream_clone_read_failed")
        if hashlib.sha1(f"{header[1]} {size}\0".encode("ascii") + body).hexdigest() != oid:
            raise Refusal("component_object_hash_mismatch")
        found[oid] = (header[1], body)
        offset = end + 2 + size
    if offset != len(raw):
        raise Refusal("upstream_clone_read_failed")
    return found


def tree_entries(body):
    """``(mode, name, id)`` for each entry of one raw tree object."""
    entries, offset = [], 0
    while offset < len(body):
        space = body.find(b" ", offset)
        nul = body.find(b"\0", space + 1) if space >= 0 else -1
        if space < 0 or nul < 0 or nul + 21 > len(body):
            raise Refusal("component_tree_unreadable")
        try:
            mode, name = body[offset:space].decode("ascii"), body[space + 1:nul].decode("utf-8")
        except UnicodeDecodeError:
            raise Refusal("component_name_not_reviewed") from None
        entries.append((mode, name, body[nul + 1:nul + 21].hex()))
        offset = nul + 21
    return entries


def git_object(git_dir, oid, kind):
    found_kind, body = git_objects(git_dir, [oid])[oid]
    if found_kind != kind:
        raise Refusal("component_source_mismatch")
    return body


def read_component(git_dir, component, bound_commit=None):
    """The component tree of one upstream tag, read from the tag object down.

    The tag, its commit, the root tree and the component subtree must be the
    ids in ``COMPONENT_SOURCES``; then every entry must be an ordinary file or
    a directory with a reviewable name, inside the bounds, and the manifest
    must name the domain, version, requirements and dependency the lane
    expects.  Returns the file bytes and one record per file.
    """
    source = COMPONENT_SOURCES[component]
    if bound_commit is not None and bound_commit != source["commit"]:
        raise Refusal("component_source_not_the_bound_commit")
    head = git_object(git_dir, source["tag_object"], "tag").split(b"\n\n", 1)[0]
    if head.decode("utf-8", "replace").splitlines()[:3] != [
            "object " + source["commit"], "type commit", "tag " + source["tag"]]:
        raise Refusal("component_source_mismatch")
    commit = git_object(git_dir, source["commit"], "commit")
    if commit.split(b"\n", 1)[0] != b"tree " + source["root_tree"].encode("ascii"):
        raise Refusal("component_source_mismatch")
    tree = source["root_tree"]
    for part in COMPONENT_PATH.split("/"):
        matches = [oid for mode, name, oid in tree_entries(git_object(git_dir, tree, "tree"))
                   if name == part and mode == "40000"]
        if len(matches) != 1:
            raise Refusal("component_source_mismatch")
        tree = matches[0]
    if tree != source["tree"]:
        raise Refusal("component_source_mismatch")
    blobs, pending = {}, [(tree, "", 1)]
    while pending:
        level, following = git_objects(git_dir, [oid for oid, _, _ in pending]), []
        for oid, prefix, depth in pending:
            kind, body = level[oid]
            if kind != "tree":
                raise Refusal("component_source_mismatch")
            for mode, name, child in tree_entries(body):
                if not safe_part(name):
                    raise Refusal("component_name_not_reviewed")
                if mode == "100644":
                    blobs[prefix + name] = child
                elif mode == "40000" and depth < COMPONENT_DEPTH:
                    following.append((child, prefix + name + "/", depth + 1))
                elif mode == "40000":
                    raise Refusal("component_above_bounds")
                else:
                    raise Refusal("component_entry_not_reviewed")
        if len(blobs) > COMPONENT_FILES:
            raise Refusal("component_above_bounds")
        pending = following
    contents = git_objects(git_dir, blobs.values()) if blobs else {}
    files, records, total = {}, [], 0
    for path in sorted(blobs):
        kind, body = contents[blobs[path]]
        total += len(body)
        if kind != "blob":
            raise Refusal("component_entry_not_reviewed")
        if total > COMPONENT_BYTES:
            raise Refusal("component_above_bounds")
        files[path] = body
        records.append({"path": path, "size": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(), "git_blob": blobs[path]})
    try:
        manifest = json.loads(files["manifest.json"].decode("utf-8"))
    except (KeyError, UnicodeDecodeError, ValueError):
        raise Refusal("component_manifest_mismatch") from None
    if (not isinstance(manifest, dict) or manifest.get("domain") != COMPONENT_DOMAIN
            or manifest.get("version") != component
            or not isinstance(manifest.get("requirements"), list)
            or tuple(manifest["requirements"]) != COMPONENT_REQUIREMENTS[component]
            or manifest.get("dependencies") != COMPONENT_DEPENDENCIES):
        raise Refusal("component_manifest_mismatch")
    return {"files": files, "records": records,
            "manifest": {key: manifest[key]
                         for key in ("domain", "version", "requirements", "dependencies")},
            "source": {**source, "component": component, "path": COMPONENT_PATH,
                       "files": len(records), "bytes": total,
                       "read": ("git cat-file --batch on a local clone, from the tag object "
                                "down; every object checked against its own sha1 id; nothing "
                                "checked out, fetched or executed")}}


def metadata_headers(raw):
    """``Name`` and ``Version`` from one METADATA text; each must appear once."""
    try:
        lines = raw.decode("utf-8").split("\n\n", 1)[0].splitlines()
    except UnicodeDecodeError:
        return None, None
    names = [line[5:].strip() for line in lines if line.startswith("Name:")]
    versions = [line[8:].strip() for line in lines if line.startswith("Version:")]
    if len(names) != 1 or len(versions) != 1:
        return None, None
    return normalized(names[0]), versions[0]


def read_wheels(wheel_dir):
    """The two N32 wheels, by hash, as member bytes; nothing is installed or executed."""
    files, records, wheels = {}, [], []
    for pin in N32_WHEELS:
        # No link is followed and a FIFO in the wheel's place never blocks the open.
        try:
            descriptor = os.open(Path(wheel_dir) / pin["file"],
                                 os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            raise Refusal("n32_wheel_missing") from None
        except OSError:
            raise Refusal("n32_wheel_unreadable") from None
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > WHEEL_BYTES:
                raise Refusal("n32_wheel_unreadable")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                raw = handle.read(WHEEL_BYTES + 1)
            if len(raw) > WHEEL_BYTES:
                raise Refusal("n32_wheel_unreadable")
        except OSError:
            raise Refusal("n32_wheel_unreadable") from None
        finally:
            os.close(descriptor)
        if hashlib.sha256(raw).hexdigest() != pin["sha256"]:
            raise Refusal("n32_wheel_hash_mismatch")
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                for member in archive.infolist():
                    parts = member.filename.split("/")
                    if (member.is_dir() or stat.S_ISLNK(member.external_attr >> 16)
                            or not all(safe_part(part) for part in parts)
                            or parts[0] not in pin["tops"] or len(parts) < 2):
                        raise Refusal("n32_wheel_member_not_reviewed")
                    if member.filename in files:
                        raise Refusal("overlay_member_collision")
                    body = archive.read(member)
                    files[member.filename] = body
                    records.append({"wheel": pin["file"], "path": member.filename,
                                    "size": len(body), "sha256": hashlib.sha256(body).hexdigest()})
        except Refusal:
            raise
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError, RuntimeError,
                EOFError, NotImplementedError):
            raise Refusal("n32_wheel_unreadable") from None
        found = metadata_headers(files.get(pin["tops"][1] + "/METADATA", b""))
        if found != (pin["distribution"], pin["version"]):
            raise Refusal("n32_wheel_metadata_mismatch")
        wheels.append({key: pin[key] for key in
                       ("file", "sha256", "source", "version", "distribution", "requirement")})
    return {"files": files, "records": records, "wheels": wheels}


def package_members(stream, version, expected):
    """The ``ha_mcp`` package and its one dist-info, from a pinned image's export stream.

    Read from the stream into memory; nothing is extracted or executed.  The
    package source must hold exactly the files and bytes the committed
    inventory counted, and the dist-info must name the bound version.
    Compiled ``__pycache__`` files are counted, not copied.  A symbolic link in
    the package source from one Markdown file to a sibling Markdown file (both
    images carry ``settings_ui/CLAUDE.md`` -> ``AGENTS.md``) is recorded, not
    copied and not counted, as the inventory counted none; any other link
    refuses, so no module or data file is silently left out.
    """
    files, bodies, roots, links = {}, {}, set(), []
    figures, site_copy = {"files": 0, "bytes": 0}, 0
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            path = "/" + member.name.lstrip("./")
            dist = PACKAGE_DIST_INFO.fullmatch(path)
            if path.startswith(PACKAGE_SOURCE):
                target = "ha_mcp/" + path[len(PACKAGE_SOURCE):]
            elif dist:
                roots.add(dist.group(1, 2, 3))
                target = dist.group(2) + "/" + dist.group(4)
            else:
                target = None
            site = SITE_PACKAGE.fullmatch(path) is not None
            if site and (member.isreg() or member.islnk()):
                site_copy += 1
            if member.isdir() or (target is None and not site):
                continue
            if member.isreg():
                body = archive.extractfile(member).read()
                bodies[member.name] = body  # A later hard link may name this member.
            if target is None:
                continue  # A site-packages copy is counted, and kept for links only.
            if member.isreg():
                pass
            elif (member.issym() and path.startswith(PACKAGE_SOURCE)
                  and safe_part(member.linkname) and member.linkname.endswith(".md")
                  and target.endswith(".md")
                  and all(safe_part(part) for part in target.split("/"))):
                links.append({"path": target, "link": member.linkname})
                continue
            elif member.islnk():
                body = bodies.get(member.linkname)
                if body is None:
                    raise Refusal("package_member_link_unresolved")
            else:
                raise Refusal("package_member_not_reviewed")
            if path.startswith(PACKAGE_SOURCE):
                figures["files"] += 1
                figures["bytes"] += member.size
            parts = target.split("/")
            if "__pycache__" in parts:
                continue
            if not all(safe_part(part) for part in parts) or target in files:
                raise Refusal("package_member_not_reviewed")
            files[target] = body
    if len(roots) != 1:
        raise Refusal("package_dist_info_not_unique")
    (site, root, found), = roots
    if found != version or metadata_headers(files.get(root + "/METADATA", b"")) != (
            "ha-mcp", version):
        raise Refusal("package_dist_info_version_mismatch")
    if figures != {"files": expected.get("files"), "bytes": expected.get("bytes")}:
        raise Refusal("package_copy_does_not_match_inventory")
    records = [{"path": path, "size": len(files[path]),
                "sha256": hashlib.sha256(files[path]).hexdigest()} for path in sorted(files)]
    return {"files": files, "records": records,
            "source": {"package_source": PACKAGE_SOURCE, "dist_info": site + "/" + root,
                       "figures_in_image": figures, "files_copied": len(records),
                       "pycache_counted_not_copied": True,
                       "symbolic_links_not_copied": links,
                       "site_packages_ha_mcp_members": site_copy}}


def copy_package(name, run_id, image, version, expected):
    return export_scan(name, run_id, image,
                       parse=lambda stream: package_members(stream, version, expected))


def tree_archive(bases, files):
    """Directories and files as one PAX tar stream for ``docker cp -``; nothing touches disk.

    Each file must lie below one of ``bases``; the directories between are
    added, the parents of ``bases`` are not.
    """
    directories = set(bases)
    for path in files:
        base = max((item for item in bases if path.startswith(item + "/")), key=len,
                   default=None)
        if base is None:
            raise Refusal("archive_member_outside_base")
        parts = path[len(base) + 1:].split("/")
        if not all(safe_part(part) for part in parts):
            raise Refusal("archive_member_not_reviewed")
        directories.update(base + "/" + "/".join(parts[:depth]) for depth in range(1, len(parts)))
    if directories & set(files):
        raise Refusal("overlay_member_collision")
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(directories):
            info = tarfile.TarInfo(path)
            info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, 0
            archive.addfile(info)
        for path in sorted(files):
            info = tarfile.TarInfo(path)
            info.size, info.mode, info.mtime = len(files[path]), 0o644, 0
            archive.addfile(info, io.BytesIO(files[path]))
    return raw.getvalue()


def overlay_files(wheels, package):
    """The wheel members and the package copy, below the overlay directory."""
    merged = {}
    for path, body in (*wheels["files"].items(), *package["files"].items()):
        target = OVERLAY_TARGET.lstrip("/") + "/" + path
        if target in merged:
            raise Refusal("overlay_member_collision")
        merged[target] = body
    return merged


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
# The 11 reads that use the component when it is present (Q3, 2026-11-14), by
# 9.1.2 upstream name; the YAML read is the twelfth, and profile B only.
COMPONENT_READS = (
    "ha_config_get_scene", "ha_config_list_helpers", "ha_eval_template",
    "ha_manage_blueprints", "ha_get_entity_exposure", "ha_get_history", "ha_get_overview",
    "ha_get_state", "ha_get_zone", "ha_list_services", "ha_search")
YAML_READ = "ha_config_get_yaml"
COMPONENT_FAILURES = (
    "flow_menu_unexpected", "flow_form_unexpected", "flow_entry_not_created",
    "entry_not_loaded", "config_unreadable", "component_not_loaded", "websocket_refused")
# Holder: set the component up through Core's own config flow (``setup``) or
# only wait for its one entry (``status``), then read its manifest and info.
# Entry, flow and webhook ids are not kept; the info answer holds none.
COMPONENT_CHECK = r"""import asyncio,json,os,sys
import aiohttp
url,mode,limit,out=sys.argv[1],sys.argv[2],float(sys.argv[3]),sys.argv[4]
headers={'Authorization':'Bearer '+os.environ['HOMEASSISTANT_TOKEN']}
class Stop(Exception): pass
async def rest(s,method,path,body=None):
    async with s.request(method,url+path,headers=headers,json=body) as r:
        raw=await r.read()
        try: return r.status,json.loads(raw)
        except ValueError: return r.status,None
def entry(e):
    return {k:e.get(k) for k in ('domain','title','source','state','disabled_by','reason')}
async def ws(s,report):
    socket=await s.ws_connect(url+'/api/websocket',max_msg_size=4194304)
    if (await socket.receive_json()).get('type')!='auth_required': raise Stop('websocket_refused')
    await socket.send_json({'type':'auth','access_token':os.environ['HOMEASSISTANT_TOKEN']})
    if (await socket.receive_json()).get('type')!='auth_ok': raise Stop('websocket_refused')
    asks={1:('manifest',{'type':'manifest/get','integration':'ha_mcp_tools'}),
          2:('info',{'type':'ha_mcp_tools/info'})}
    for key,(name,body) in asks.items():
        await socket.send_json({'id':key,**body})
        while True:
            m=await socket.receive_json()
            if m.get('id')==key and m.get('type')=='result': break
        report[name]={'success':m.get('success') is True,'result':m.get('result'),
                      'error_code':(m.get('error') or {}).get('code')}
    await socket.close()
async def main():
    report={'mode':mode,'ok':False}
    loop=asyncio.get_running_loop()
    async with aiohttp.ClientSession(trust_env=False,timeout=aiohttp.ClientTimeout(total=60)) as s:
        try:
            if mode=='setup':
                flow=report['flow']={}
                st,step=await rest(s,'POST','/api/config/config_entries/flow',
                                   {'handler':'ha_mcp_tools','show_advanced_options':False})
                if (st!=200 or not isinstance(step,dict) or step.get('type')!='menu'
                        or 'tools' not in (step.get('menu_options') or [])):
                    raise Stop('flow_menu_unexpected')
                flow['menu_step'],flow['menu_options']=step.get('step_id'),step.get('menu_options')
                path='/api/config/config_entries/flow/'+str(step.get('flow_id'))
                st,step=await rest(s,'POST',path,{'next_step_id':'tools'})
                if (st!=200 or not isinstance(step,dict) or step.get('type')!='form'
                        or step.get('step_id')!='tools'):
                    raise Stop('flow_form_unexpected')
                flow['form_step']=step['step_id']
                flow['fields']=[f.get('name') for f in step.get('data_schema') or []]
                if flow['fields']: raise Stop('flow_form_unexpected')
                st,step=await rest(s,'POST',path,{})
                if st!=200 or not isinstance(step,dict) or step.get('type')!='create_entry':
                    raise Stop('flow_entry_not_created')
                flow['result_type'],flow['title']=step['type'],step.get('title')
            end=loop.time()+limit
            while True:
                st,entries=await rest(s,'GET','/api/config/config_entries/entry?domain=ha_mcp_tools')
                if st==200 and isinstance(entries,list):
                    report['entries']=[entry(e) for e in entries if isinstance(e,dict)]
                    if [e['state'] for e in report['entries']]==['loaded']: break
                if loop.time()>end: raise Stop('entry_not_loaded')
                await asyncio.sleep(1)
            st,config=await rest(s,'GET','/api/config')
            if st!=200 or not isinstance(config,dict): raise Stop('config_unreadable')
            report['components']={k:k in (config.get('components') or [])
                                  for k in ('webhook','ha_mcp_tools')}
            if not all(report['components'].values()): raise Stop('component_not_loaded')
            await ws(s,report)
            report['ok']=True
        except Stop as exc:
            report['failure']=str(exc)
        except (aiohttp.ClientError,asyncio.TimeoutError,OSError,ValueError,TypeError,KeyError):
            report['failure']='client_error'
    with open(out,'x',encoding='utf-8') as handle:
        json.dump(report,handle,sort_keys=True,ensure_ascii=False)
    print(json.dumps({'ok':report['ok']}))
asyncio.run(main())
"""
# Core container, no token: which distribution each name resolves to with
# the container's own environment, and the PYTHONPATH of the running Core
# process (its other environment entries are not read out).
RESOLVED = r"""import importlib.metadata as m,json,os,sys
found={}
for name in sys.argv[1:]:
    try:
        d=m.distribution(name)
        found[name]={'version':d.version,'location':str(d.locate_file(''))}
    except m.PackageNotFoundError:
        found[name]=None
paths=None
for pid in sorted(p for p in os.listdir('/proc') if p.isdigit()):
    try:
        with open('/proc/'+pid+'/cmdline','rb') as h: argv=h.read().split(b'\0')
    except OSError: continue
    if any(argv[i:i+2]==[b'-m',b'homeassistant'] for i in range(len(argv)-1)):
        try:
            with open('/proc/'+pid+'/environ','rb') as h: env=h.read().split(b'\0')
            paths=[item[11:].decode('utf-8','replace') for item in env
                   if item.startswith(b'PYTHONPATH=')]
        except OSError: paths='unreadable'
        break
print(json.dumps({'distributions':found,'core_python_path':paths},sort_keys=True))
"""
RESOLVED_NAMES = ("ha-mcp", "ruamel.yaml", "voluptuous-openapi", "mcp", *OTHER_REQUIREMENTS)
ANSI = re.compile(r"\x1b\[[0-9;]*m")
INSTALL_MARKERS = ("No module named", "Unable to install", "Requirements for",
                   "Attempting install")
NOT_BUILT = (
    "profile B overlay (component tree, package copy and the two N32 wheels): built only "
    "by ``run --profile B``, one version per run; profile A and ``recapture`` have none",
    "Supervisor facade (form 2): not built; reads that need a Supervisor record what the "
    "peer returns without one",
    "token revocation between calls (P4): not exercised; the unknown-identifier cases only",
)


ORDINARY_PEER = "peer-" + s4.CANDIDATE_VERSION + "-ordinary-user"


def lane_names(command_name, profile_b_version=None):
    stem = f"{PROJECT}-{command_name}"
    wanted = {"holder": stem + "-holder", "core": stem + "-core", "volume": stem + "-config"}
    for version in VERSIONS:
        wanted["peer-" + version] = f"{stem}-peer-{version.replace('.', '-')}"
    if profile_b_version is not None:
        wanted["package"] = stem + "-package"
        if profile_b_version == s4.CANDIDATE_VERSION:
            wanted[ORDINARY_PEER] = wanted["peer-" + profile_b_version] + "-ordinary-user"
    return wanted


def container_keys(wanted):
    """Removal order: peers, the package source, then Core, then the namespace owner."""
    return [*sorted(key for key in wanted if key.startswith("peer-")),
            *(["package"] if "package" in wanted else []), "core", "holder"]


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


def core_argv(name, run_id, holder, volume, image, overlay=False):
    """``docker create`` for the disposable Core; its only network is the holder's namespace.

    With ``overlay`` (profile B) Core's ``PYTHONPATH`` names the overlay
    directory, which is copied into this container's own file system.
    """
    s4.host.memory_bytes(CORE_MEMORY)
    return ["docker", "create", "--pull", "never", "--name", name, *labels_argv(run_id),
            "--network", "container:" + holder, "--memory", CORE_MEMORY,
            "--pids-limit", str(CORE_PIDS), "--security-opt", "no-new-privileges",
            "--env", "TZ=" + seed_step.TIME_ZONE,
            *(["--env", OVERLAY_ENVIRONMENT] if overlay else []),
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


def verify_core(data, holder_id, image, volume, overlay=False):
    """Core runs the bound image in the holder's namespace with one volume and no way out."""
    config, settings = data.get("HostConfig") or {}, data.get("NetworkSettings") or {}
    if (data.get("Image") != image["config_digest"]
            or (data.get("Config") or {}).get("Image") != image["image"]):
        raise Refusal("running_image_mismatch")
    python_path = [item for item in (data.get("Config") or {}).get("Env") or []
                   if isinstance(item, str) and item.startswith("PYTHONPATH=")]
    if python_path != ([OVERLAY_ENVIRONMENT] if overlay else []):
        raise Refusal("core_overlay_environment_mismatch")
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
            "pids_limit": CORE_PIDS, "privileged": False, "no_new_privileges": True,
            "python_path": [OVERLAY_TARGET] if overlay else []}


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


def restart_core(wanted, run_id, holder_id, core_image, token, overlay=False):
    """Restart Core from the host; it must come back in the same namespace, still shut in."""
    command("docker", "restart", "-t", "60", wanted["core"], timeout=180)
    verify_core(s4.inspect_owned(wanted["core"], run_id) or {}, holder_id, core_image,
                wanted["volume"], overlay)
    checked = posture(wanted)
    wait_running(wanted["holder"], token)
    return checked


def start_peer(version, wanted, run_id, upstream, holder_id, tokens, key=None,
               token_name="administrator"):
    """Posture first, then the one peer, checked, and its endpoint answering."""
    holder, peer = wanted["holder"], wanted[key or "peer-" + version]
    section = {"network_before_capture": posture(wanted), "token": token_name}
    command(*peer_argv(peer, run_id, holder, upstream["image"]),
            env=with_token(tokens[token_name]))
    section["peer_network"] = s4.verify_peer(s4.inspect_owned(peer, run_id) or {}, holder_id,
                                             upstream)
    command(*s4.exec_argv(holder, "-c", s4.WAIT_HTTP, s4.ENDPOINT, "120", "any"), timeout=150)
    return section


def remove_peer(version, wanted, run_id, log, key=None):
    """The peer must still be running; its log is kept and it is proven gone."""
    peer = wanted[key or "peer-" + version]
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


def core_log_check(text):
    """Counts from Core's log, and the first line that asks for a requirement or fails setup.

    Only lines that name an install, a missing module or a requirement, or a
    setup failure of the component, are refused; other integrations' errors
    are counted.  INFO lines are not in a log at the default WARNING level.
    """
    lines = ANSI.sub("", text).splitlines()
    ten = {form for name in OTHER_REQUIREMENTS for form in (name, name.replace("-", "_"))}
    failure = None
    rules = (
        ("core_requested_one_of_the_ten",
         lambda line: any(marker in line for marker in INSTALL_MARKERS)
         and any(name in line for name in ten)),
        ("core_requested_a_requirement",
         lambda line: any(marker in line for marker in (
             "Unable to install package", "Requirements for " + COMPONENT_DOMAIN + " not found",
             "Attempting install of"))),
        ("core_reported_a_missing_module", lambda line: "No module named" in line),
        ("core_component_setup_failed",
         lambda line: f"Setup failed for custom integration '{COMPONENT_DOMAIN}'" in line
         or ("Error setting up entry" in line and COMPONENT_DOMAIN in line)),
    )
    for token, matches in rules:
        if any(matches(line) for line in lines):
            failure = token
            break
    return {"lines": len(lines),
            "custom_integration_warnings": sum(
                1 for line in lines if "We found a custom integration" in line),
            "component_lines": sum(1 for line in lines if COMPONENT_DOMAIN in line),
            "error_lines": sum(1 for line in lines if " ERROR " in line),
            "refused": failure}, failure


def component_check(report, component):
    """The holder's component report, reduced to what the receipt keeps."""
    if not isinstance(report, dict):
        return {"readable": False}, "component_check_unreadable"
    info = report.get("info") if isinstance(report.get("info"), dict) else {}
    manifest = report.get("manifest") if isinstance(report.get("manifest"), dict) else {}
    answer = info.get("result") if isinstance(info.get("result"), dict) else {}
    found = (manifest.get("result") if isinstance(manifest.get("result"), dict) else {})
    summary = {"readable": True, "mode": report.get("mode"), "ok": report.get("ok") is True,
               "failure": report.get("failure") if report.get("failure") in (
                   *COMPONENT_FAILURES, "client_error") else None,
               "flow": report.get("flow"), "entries": report.get("entries"),
               "components": report.get("components"),
               "manifest": {"success": manifest.get("success"),
                            "version": found.get("version"),
                            "is_built_in": found.get("is_built_in"),
                            "requirements": found.get("requirements")},
               "info": {"success": info.get("success"),
                        **{key: answer.get(key) for key in (
                            "component_version", "schema_version", "tools_services",
                            "webhook_auth", "timezone", "capabilities", "limits")}}}
    if summary["ok"] is not True:
        return summary, "component_setup_failed"
    if info.get("success") is not True or manifest.get("success") is not True:
        return summary, "component_info_failed"
    if answer.get("component_version") != component or found.get("version") != component:
        return summary, "component_version_mismatch"
    return summary, None


def resolved_check(stdout, version):
    """What the Core container resolves: the overlay for the three, the image for ``mcp``."""
    try:
        value = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        value = None
    if not isinstance(value, dict) or not isinstance(value.get("distributions"), dict):
        return {"readable": False}, "overlay_not_resolved_in_core"
    found = value["distributions"]
    wanted = {"ha-mcp": version, **{pin["requirement"].split(">=")[0]: pin["version"]
                                    for pin in N32_WHEELS}}
    paths = value.get("core_python_path")
    summary = {"readable": True, "distributions": found, "core_python_path": paths}
    for name, expected in wanted.items():
        row = found.get(name)
        if not isinstance(row, dict) or row != {"version": expected,
                                                "location": OVERLAY_TARGET}:
            return summary, "overlay_not_resolved_in_core"
    mcp = found.get("mcp")
    if not isinstance(mcp, dict) or mcp.get("location") == OVERLAY_TARGET:
        return summary, "overlay_not_resolved_in_core"
    if paths != "unreadable" and paths != [OVERLAY_TARGET]:
        return summary, "overlay_not_resolved_in_core"
    return summary, None


def component_phase(section, wanted, token, version, phase, run_dir, setup):
    """Set up or check the component, read what Core resolves and Core's log, in one section.

    ``version`` is the ha-mcp version under capture; its component is
    ``COMPONENT_FOR[version]``.  ``section`` is already in the receipt;
    everything read is kept in it before a refusal is raised.  A request to
    install, from Core's log, is refused first.
    """
    component = COMPONENT_FOR[version]
    name = f"component-{phase}.json"
    done = command("docker", "exec", "--env", TOKEN_VARIABLE, wanted["holder"], "python", "-I",
                   "-B", "-c", COMPONENT_CHECK, CORE_URL, "setup" if setup else "status",
                   str(RUNNING_SECONDS), "/out/profile-b/" + name, env=with_token(token),
                   check=False, timeout=RUNNING_SECONDS + 120)
    try:
        report = s4.read_json(run_dir / "out" / "profile-b" / name)
    except (OSError, ValueError):
        report = None
    section.update(phase=phase, setup=setup, check_exit=done.returncode, file=name)
    section["component"], component_failure = component_check(report, component)
    resolved = command("docker", "exec", "--workdir", "/", wanted["core"], "python3", "-P", "-B",
                       "-c", RESOLVED, *RESOLVED_NAMES, check=False, timeout=120)
    section["resolved"], resolved_failure = resolved_check(resolved.stdout, version)
    logs = command("docker", "logs", wanted["core"], check=False, timeout=120)
    section["core_log"], log_failure = core_log_check(
        (logs.stdout + logs.stderr).decode("utf-8", "replace"))
    failure = log_failure or component_failure or resolved_failure or (
        "component_setup_failed" if done.returncode else None)
    if failure:
        raise Refusal(failure)
    return section


def component_reads(path):
    """Index rows of the component-dependent reads and the YAML read; no payload."""
    rows = (s4.read_json(Path(path) / "_index.json").get("calls") or [])
    return [{key: row.get(key) for key in ("id", "upstream_tool", "file", "sha256",
                                           "exchange_failure", "is_error")}
            for row in rows if row.get("upstream_tool") in (*COMPONENT_READS, YAML_READ)]


def capture_profile_b(section, version, wanted, run_id, run_dir, upstream, holder_id,
                      core_image, tokens, phases):
    """One version on its component: capture and the YAML read, a Core restart, and again.

    For 9.2.0 the YAML read is also made through a peer holding the ordinary
    user's token (N29 a).  ``section`` and ``phases`` are already in the receipt.
    """
    holder, out_dir = wanted["holder"], run_dir / "out"
    section.update(start_peer(version, wanted, run_id, upstream, holder_id, tokens))
    (out_dir / version).mkdir(mode=0o700)
    section["calls"] = run_calls(holder, version, "calls", CAPTURE_GROUPS + PROFILE_B_GROUPS,
                                 out_dir)
    section["component_reads"] = component_reads(out_dir / version / "calls")
    section["network_after_core_restart"] = restart_core(
        wanted, run_id, holder_id, core_image, tokens["administrator"], overlay=True)
    phases["after-core-restart"] = {}
    component_phase(phases["after-core-restart"], wanted, tokens["administrator"],
                    version, "after-core-restart", run_dir, setup=False)
    section["calls_after_core_restart"] = run_calls(
        holder, version, "calls-after-core-restart", RESTART_GROUPS, out_dir)
    remove_peer(version, wanted, run_id, run_dir / f"peer-{version}.log")
    if ORDINARY_PEER in wanted:
        ordinary = section["ordinary_user"] = {}
        ordinary.update(start_peer(version, wanted, run_id, upstream, holder_id, tokens,
                                   key=ORDINARY_PEER, token_name="ordinary_user"))
        ordinary["calls"] = run_calls(holder, version, "yaml-ordinary-user", PROFILE_B_GROUPS,
                                      out_dir)
        ordinary["component_reads"] = component_reads(out_dir / version / "yaml-ordinary-user")
        remove_peer(version, wanted, run_id, run_dir / f"peer-{version}-ordinary-user.log",
                    key=ORDINARY_PEER)
    return section


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


def require_profile_b(core, version, wheels=None, inventory=PROFILE_B_INVENTORY):
    """Profile B's preflight for one version, from the committed image inventory.

    Each requirement must come from the Core image, the pinned ha-mcp image or
    (with ``wheels``) one N32 wheel whose version meets it; a requirement the
    Core image already carries is never shadowed by the overlay.  Without
    wheels the two missing requirements still refuse.
    """
    try:
        receipt = s4.read_json(inventory)
    except (OSError, ValueError):
        raise Refusal("profile_b_inventory_missing") from None
    if (not isinstance(receipt, dict) or receipt.get("command") != "inventory"
            or receipt.get("result") != "PASS"):
        raise Refusal("profile_b_inventory_missing")
    if ((receipt.get("bindings") or {}).get("core") or {}).get("image") != core["image"]:
        raise Refusal("profile_b_inventory_stale")
    report = (receipt.get("profile_b") or {}).get(version)
    if not isinstance(report, dict) or not isinstance(report.get("missing"), list):
        raise Refusal("profile_b_inventory_missing")
    rows = report.get("requirements")
    if (not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows)
            or tuple(row.get("requirement") for row in rows) != COMPONENT_REQUIREMENTS[version]
            or report["missing"] != [row["requirement"] for row in rows
                                     if row.get("source") == "missing"]):
        raise Refusal("profile_b_inventory_stale")
    pinned = {normalized(item["distribution"]): item for item in (wheels or {}).get("wheels", [])}
    supplied = []
    for row in rows:
        name = parse_requirement(row["requirement"])[0]
        row = dict(row)
        if (name in pinned or name == "ha-mcp") and row.get("core_image_version") is not None:
            raise Refusal("overlay_distribution_in_core_image")
        if name in pinned and row.get("source") == "missing":
            if not satisfied(row["requirement"], pinned[name]["version"]):
                raise Refusal("n32_wheel_does_not_meet_requirement")
            row.update(source=WHEEL_SOURCE, wheel_version=pinned[name]["version"])
        supplied.append(row)
    report = {**report, "requirements": supplied,
              "missing": [row["requirement"] for row in supplied if row["source"] == "missing"]}
    require_profile_b_feasible(report)
    package = (receipt.get("package_source_in_image") or {}).get(version)
    if (not isinstance(package, dict) or package.get("path") != PACKAGE_SOURCE
            or not isinstance(package.get("files"), int)
            or not isinstance(package.get("bytes"), int)):
        raise Refusal("profile_b_inventory_missing")
    inventory = Path(inventory)
    return {"requirements": supplied, "package_source": package,
            "upstream_python": (receipt.get("upstream_python") or {}).get(version),
            "core_python": receipt.get("core_python"),
            "inventory": (inventory.relative_to(ROOT).as_posix()
                          if inventory.is_relative_to(ROOT) else inventory.name)}


def execute(args, launch):
    """Body of ``selftest``, ``run`` and ``recapture``; everything before mkdir is preflight."""
    if not __debug__:
        raise Refusal("assertions_required")
    capture = args.command in ("run", "recapture")
    profile_b = capture and args.profile == "B"
    options = [getattr(args, key, None) for key in ("version", "wheel_dir", "upstream_git")]
    if not profile_b and any(item is not None for item in options):
        raise Refusal("profile_a_takes_no_profile_b_options")
    if profile_b and any(item is None for item in options):
        raise Refusal("profile_b_options_missing")
    core = core_binding()
    versions = (args.version,) if profile_b else VERSIONS
    if profile_b:
        wheels = read_wheels(args.wheel_dir)
        built = require_profile_b(core, args.version, wheels)
        component = read_component(args.upstream_git, COMPONENT_FOR[args.version],
                                   s4.binding(args.version)["source_commit"])
    if memory_total() > s4.host.memory_bytes(MEMORY_CEILING):
        raise Refusal("container_memory_above_ceiling")
    revision = s4.require_clean_checkout()
    helper = s4.cached_image(s4.HELPER_IMAGE, s4.HELPER_LABELS)
    core_image = s4.cached_image(core["image"], CORE_LABELS)
    bindings, upstream = {"core": core}, {}
    if capture:
        for version in versions:
            bound = s4.binding(version)
            bindings[version] = {key: bound[key] for key in sorted(bound)
                                 if key not in ("labels", "image")}
            upstream[version] = s4.cached_image(bound["image"], bound["labels"])
    figures = s4.host_figures()
    s4.require_host_floors(figures)
    wanted = lane_names(args.command, args.version if profile_b else None)
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
    if profile_b:
        overlay = {"target": OVERLAY_TARGET, "core_environment": OVERLAY_ENVIRONMENT,
                   "method": ("docker cp - of tar streams built in memory, into the Core "
                              "container's own file system and its run volume before Core "
                              "first starts; gone with the container and the volume"),
                   "archives": {}}
        result["profile_b"] = section_b = {
            "rulings": ["C4", "N32", "N29", "Q3"], "version": args.version,
            "component": {"source": component["source"], "manifest": component["manifest"],
                          "records": component["records"]},
            "wheels": {"wheels": wheels["wheels"], "records": wheels["records"]},
            "requirements": built["requirements"], "inventory": built["inventory"],
            "upstream_python": built["upstream_python"], "core_python": built["core_python"],
            "package_copy": None, "overlay": overlay, "phases": {},
            "not_done": [
                "no HACS, no pip and no fetch by Core or by the lane; Core's log is read for "
                "any request to install and the lane refuses one",
                "INFO lines (Core's own 'Attempting install') are not in a log at the "
                "default WARNING level; what Core resolves is read from inside the container",
                "no server entry of the component is created; webhook_auth is read as the "
                "info answer gives it",
                "the component 2.2.1 / ha-mcp 8.5.0 control belongs to SR, not to this run"]}
    tokens = {}

    def interrupted(_signum, _frame):
        raise Refusal("lane_interrupted")
    previous = signal.signal(signal.SIGTERM, interrupted)
    started = time.monotonic()
    try:
        if profile_b:
            # The package copy first: the pinned image is read from a container
            # that is created, exported and removed without ever starting.
            package = copy_package(wanted["package"], run_id, upstream[args.version],
                                   args.version, built["package_source"])
            section_b["package_copy"] = {"source": package["source"],
                                         "records": package["records"]}
            (out_dir / "profile-b").mkdir(mode=0o700)
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
                           core_image["image"], overlay=profile_b))
        stream_in(["docker", "cp", "-", f"{wanted['core']}:{CONFIG_TARGET}"],
                  config_archive(seed_step.CONFIG_FILES))
        if profile_b:
            streams = (
                ("component", CONFIG_TARGET, tree_archive(
                    ["custom_components", COMPONENT_PATH],
                    {COMPONENT_PATH + "/" + path: body
                     for path, body in component["files"].items()})),
                ("overlay", "/", tree_archive([OVERLAY_TARGET.lstrip("/")],
                                              overlay_files(wheels, package))))
            for key, target, raw in streams:
                overlay["archives"][key] = {"into": target, "bytes": len(raw),
                                            "sha256": hashlib.sha256(raw).hexdigest()}
                stream_in(["docker", "cp", "-", f"{wanted['core']}:{target}"], raw)
        command("docker", "start", wanted["core"])
        result["core_container"] = verify_core(
            s4.inspect_owned(wanted["core"], run_id) or {}, holder_id, core_image,
            wanted["volume"], overlay=profile_b)
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
        elif profile_b:
            version = args.version
            network["before_component_setup"] = posture(wanted)
            section_b["phases"]["after-setup"] = {}
            component_phase(section_b["phases"]["after-setup"], wanted, tokens["administrator"],
                            version, "after-setup", run_dir, setup=True)
            result["captures"][version] = {}
            capture_profile_b(result["captures"][version], version, wanted, run_id, run_dir,
                              upstream[version], holder_id, core_image, tokens,
                              section_b["phases"])
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
            # Profile B only, and all three: one version per run (C4, N32).
            sub.add_argument("--version", choices=VERSIONS)
            sub.add_argument("--wheel-dir", type=Path)
            sub.add_argument("--upstream-git", type=Path)
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
