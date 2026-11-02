"""Bounded public upstream acquisition for the CI-only Core evidence workflow."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
import urllib.request

from core_release_evidence import DIGEST, SHA, digest, manifest, stable_version

REPOSITORY = "jeter-1/hass-mcp-admin"
CORE = "repos/home-assistant/core"


def github(endpoint, *, optional=False):
    proc = subprocess.run(["gh", "api", endpoint], capture_output=True, timeout=90)
    if proc.returncode:
        try:
            missing = json.loads(proc.stdout).get("status") == "404"
        except (ValueError, AttributeError):
            missing = False
        if optional and missing:
            return None
        raise ValueError("GitHub read failed")
    if len(proc.stdout) > 16 * 1024 * 1024:
        raise ValueError("GitHub response exceeds bound")
    return json.loads(proc.stdout)


def source(version, directory):
    if not stable_version(version):
        raise ValueError("stable source tag required")
    ref = github(f"{CORE}/git/ref/tags/{version}")["object"]
    if ref["type"] != "commit" or not SHA.fullmatch(ref["sha"]):
        raise ValueError("Core tag must resolve to an exact commit")
    commit = ref["sha"]
    identity = github(f"{CORE}/git/commits/{commit}")
    if (identity["sha"] != commit or not SHA.fullmatch(identity["tree"]["sha"])
            or identity.get("verification", {}).get("verified") is not True):
        raise ValueError("Core source tree mismatch")
    archive = Path(directory) / (version + ".tar.gz")
    subprocess.run(["curl", "--fail", "--location", "--proto", "=https", "--tlsv1.2",
                    "--max-time", "180", "--max-filesize", "134217728", "--silent", "--show-error",
                    f"https://codeload.github.com/home-assistant/core/tar.gz/{commit}",
                    "--output", str(archive)], check=True, timeout=190)
    if not 0 < archive.stat().st_size <= 128 * 1024 * 1024:
        raise ValueError("Core archive exceeds bound")
    paths = set(manifest()["paths"]); hashes = {}
    with tarfile.open(archive) as stream:
        if stream.pax_headers.get("comment") != commit:
            raise ValueError("archive tag SHA mismatch")
        prefix = "core-" + commit + "/"
        for member in stream:
            if not member.name.startswith(prefix):
                if member.name == prefix[:-1] and member.isdir():
                    continue
                raise ValueError("unexpected archive root")
            path = member.name[len(prefix):]
            if path not in paths:
                continue
            if path in hashes or not member.isfile() or member.size > 4 * 1024 * 1024:
                raise ValueError("invalid contract-read file")
            hashes[path] = digest(stream.extractfile(member).read())
    # Detect tag movement during the fetch, rather than comparing a guessed tag.
    if github(f"{CORE}/git/ref/tags/{version}")["object"] != ref:
        raise ValueError("tag SHA changed")
    with archive.open("rb") as file:
        archive_sha = hashlib.file_digest(file, "sha256").hexdigest()
    return {"version": version, "commit": commit, "tag_commit": commit,
            "tree": identity["tree"]["sha"], "archive_sha256": archive_sha, "hashes": hashes}


def _get(url, headers=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=60) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if not 0 < len(raw) <= 2 * 1024 * 1024:
        raise ValueError("OCI response exceeds bound")
    return raw


def image_provenance(version, commit):
    """Same index→manifest→config/attestation chain as HAMCP-222/seq15."""
    if not stable_version(version) or not SHA.fullmatch(commit):
        raise ValueError("exact OCI version/commit required")
    token = json.loads(_get("https://ghcr.io/token?scope=repository:home-assistant/home-assistant:pull&service=ghcr.io"))["token"]
    headers = {"Authorization": "Bearer " + token, "Accept": ",".join([
        "application/vnd.oci.image.index.v1+json", "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json", "application/vnd.in-toto+json"])}
    receipts = {}
    def get(kind, ref):
        if ref != version and not DIGEST.fullmatch(ref):
            raise ValueError("invalid OCI reference")
        raw = _get(f"https://ghcr.io/v2/home-assistant/home-assistant/{kind}/{ref}", headers)
        sha = "sha256:" + digest(raw)
        if ref.startswith("sha256:") and sha != ref:
            raise ValueError("OCI digest mismatch")
        receipts[sha] = json.loads(raw)
        return sha, receipts[sha]
    index_digest, index = get("manifests", version)
    platforms = {}; attestations = {}
    for item in index["manifests"]:
        platform = item.get("platform", {})
        if platform.get("os") == "linux" and platform.get("architecture") in ("amd64", "arm64"):
            name = "linux/" + platform["architecture"]
            if name in platforms:
                raise ValueError("duplicate architecture")
            platforms[name] = item["digest"]
        if platform.get("os") == "unknown":
            subject = item.get("annotations", {}).get("vnd.docker.reference.digest")
            if subject in attestations:
                raise ValueError("duplicate attestation")
            attestations[subject] = item["digest"]
    if set(platforms) != {"linux/amd64", "linux/arm64"}:
        raise ValueError("missing required architectures")
    for architecture, md in platforms.items():
        _, mf = get("manifests", md)
        _, cfg = get("blobs", mf["config"]["digest"])
        if cfg["os"] + "/" + cfg["architecture"] != architecture:
            raise ValueError("configuration architecture mismatch")
        _, att = get("manifests", attestations[md])
        layers = [l for l in att["layers"] if l.get("annotations", {}).get("in-toto.io/predicate-type", "").startswith("https://slsa.dev/provenance")]
        if len(layers) != 1:
            raise ValueError("missing/ambiguous source attestation")
        _, statement = get("blobs", layers[0]["digest"])
        if md not in {"sha256:" + s["digest"]["sha256"] for s in statement["subject"]}:
            raise ValueError("attestation subject mismatch")
        predicate = statement["predicate"]
        vcs = predicate["runDetails"]["metadata"]["buildkit_metadata"]["vcs"]
        if vcs != {**vcs, "revision": commit, "source": "https://github.com/home-assistant/core"}:
            raise ValueError("attestation source mismatch")
        if not re.fullmatch(r"https://github.com/home-assistant/core/actions/runs/[0-9]+/.*", predicate["runDetails"]["builder"]["id"]):
            raise ValueError("unexpected builder identity")
        labels = cfg["config"]["Labels"]
        bases = [x for x in predicate["buildDefinition"]["resolvedDependencies"] if "homeassistant-base@" in x.get("uri", "")]
        if (labels["io.hass.version"] != version or len(bases) != 1
                or labels["io.hass.base.version"] != bases[0]["uri"].split("?")[0].rsplit("@", 1)[1]
                or not DIGEST.fullmatch("sha256:" + bases[0]["digest"]["sha256"])):
            raise ValueError("image version/base provenance mismatch")
    return index_digest, platforms, receipts
