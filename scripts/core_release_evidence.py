"""HAMCP-279 CI-only O3/withholding contracts. No signing or runtime imports.

Artifacts are admitted only from the same workflow run/attempt and source SHA.
Their hash is an integrity binding, not independent signing authority. The main
workflow and its read-only prepare job own artifact admission.
"""
from __future__ import annotations

import copy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
# Stable releases in future years are also supported; no beta/rc/dev suffix.
STABLE = re.compile(r"20[0-9]{2}\.(?:[1-9]|1[0-2])\.(?:0|[1-9][0-9]*)\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
CONTEXT_ENV = "CORE_EVIDENCE_CONTEXT"
PROVENANCE_FIELDS = frozenset({"entry_id", "server_name", "version", "source_commit", "source_tree",
                              "source_archive_sha256", "image_index_digest", "architecture_manifests"})


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(path, limit=2 * 1024 * 1024):
    raw = Path(path).read_bytes()
    if not 0 < len(raw) <= limit:
        raise ValueError("JSON input exceeds bound")
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError("duplicate member")
            result[k] = v
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


def manifest():
    value = read_json(ROOT / "ci/core-contract-read-files.json")
    paths = value["paths"]
    if (not paths or paths != sorted(set(paths))
            or any(not p.startswith("homeassistant/") or ".." in p or p.endswith("/") for p in paths)):
        raise ValueError("invalid contract-read manifest")
    validate_manifest_coverage(paths)
    return value


def validate_manifest_coverage(paths):
    """Union inventory guard: every path must be witnessed in a bound release.

    The committed records are generated from hash-pinned source archives, not
    path guesses. Bind their provenance to the existing reviewed release fixtures.
    This validation runs in CI's source tests and every evidence preparation.
    """
    from core_lane_bindings import REVIEWED_CORE_BINDINGS, provenance_name
    records = sorted((ROOT / "docs/evidence/hamcp315/core-sources").glob("*.json"))
    present, versions = set(), set()
    for path in records:
        record = read_json(path)
        version = record.get("version")
        if version not in REVIEWED_CORE_BINDINGS or version in versions:
            raise ValueError("source-hash record requires a distinct bound Core release")
        fixture = read_json(ROOT / "tests/fixtures" / provenance_name(version))
        if (record.get("commit") != fixture["source_commit"]
                or record.get("tag_commit") != fixture["source_commit"]
                or record.get("archive_sha256") != fixture["source_archive_sha256"]
                or fixture["image_index_digest"] != REVIEWED_CORE_BINDINGS[version].image_index_digest):
            raise ValueError("source-hash record provenance mismatch")
        hashes = record.get("hashes")
        if not isinstance(hashes, dict) or any(
                not isinstance(value, str) or not HASH.fullmatch(value) for value in hashes.values()):
            raise ValueError("source-hash record contains invalid hashes")
        present.update(hashes)
        versions.add(version)
    missing = sorted(set(paths) - present)
    if missing:
        raise ValueError("manifest paths absent from every bound source record: " + ", ".join(missing))


def equal_source_hashes(left, right):
    """O3: two explicit absences are equal; all present bytes stay exact."""
    if left is None and right is None:
        return True
    return isinstance(left, str) and HASH.fullmatch(left) is not None and left == right


def groups():
    value = read_json(ROOT / "ci/core-evidence-groups.json")
    flat = [p for refs in value.values() for p in refs]
    if len(flat) != len(set(flat)) or len(value["general"]) != 19:
        raise ValueError("invalid evidence groups")
    return value


def stable_version(value):
    return isinstance(value, str) and STABLE.fullmatch(value) is not None


def version_key(value):
    if not stable_version(value):
        raise ValueError("exact stable Core release required")
    return tuple(map(int, value.split(".")))


def select_release(releases, published, runs, requested=None, *, retry_refused=False):
    """Pending/success suppress; O3 refusal is terminal except explicit retry."""
    eligible = {r["tag_name"] for r in releases if not r.get("draft") and not r.get("prerelease")
                and stable_version(r.get("tag_name"))}
    if requested and requested not in eligible:
        raise ValueError("requested release is not an official stable release")
    for version in sorted(eligible, key=version_key, reverse=True):
        if requested and version != requested:
            continue
        if version in published or (published and version_key(version) <= max(map(version_key, published))):
            continue
        if any(r.get("release") == version and (r.get("status") != "completed"
               or (r.get("outcome") == "refused" and not (retry_refused and requested == version))
               or r.get("conclusion") == "success") for r in runs):
            continue
        return version
    return None


def compare_sources(predecessor, target, paths=None):
    """Complete union comparison; one-sided absence and changed bytes differ."""
    paths = manifest()["paths"] if paths is None else paths
    for source in (predecessor, target):
        if (not stable_version(source.get("version")) or not SHA.fullmatch(source.get("commit", ""))
                or source.get("tag_commit") != source["commit"]):
            raise ValueError("tag SHA mismatch")
    rows = [{"path": p, "predecessor_sha256": predecessor["hashes"].get(p),
             "release_sha256": target["hashes"].get(p)} for p in paths]
    differing = [r["path"] for r in rows
                 if not equal_source_hashes(r["predecessor_sha256"], r["release_sha256"])]
    return {"model": "core-o3-comparison-v1", "predecessor": predecessor["version"],
            "release": target["version"], "predecessor_commit": predecessor["commit"],
            "release_commit": target["commit"], "files": rows, "differing_files": differing,
            "inherited": not differing, "outcome": "inherit" if not differing else "binding PR required"}


def verify_proof(proof, predecessor, release, source_commit):
    if (proof.get("model") != "core-o3-comparison-v1" or proof.get("predecessor") != predecessor
            or proof.get("release") != release or proof.get("release_commit") != source_commit
            or not SHA.fullmatch(proof.get("predecessor_commit", ""))):
        raise ValueError("O3 proof identity mismatch")
    paths = manifest()["paths"]
    rows = proof.get("files", [])
    if [r.get("path") for r in rows] != paths:
        raise ValueError("O3 proof file coverage mismatch")
    if any("predecessor_sha256" not in r or "release_sha256" not in r
           or not equal_source_hashes(r["predecessor_sha256"], r["release_sha256"]) for r in rows):
        raise ValueError("O3 proof does not establish byte identity")
    if proof.get("inherited") is not True or proof.get("differing_files") != []:
        raise ValueError("O3 proof refused")


def validate_provenance(value):
    if set(value) != PROVENANCE_FIELDS or not stable_version(value["version"]):
        raise ValueError("invalid provenance fields")
    if (value["server_name"] != "home-assistant-core"
            or not SHA.fullmatch(value["source_commit"]) or not SHA.fullmatch(value["source_tree"])
            or not HASH.fullmatch(value["source_archive_sha256"])
            or not DIGEST.fullmatch(value["image_index_digest"])
            or set(value["architecture_manifests"]) != {"linux/amd64", "linux/arm64"}
            or any(not DIGEST.fullmatch(v) for v in value["architecture_manifests"].values())):
        raise ValueError("invalid source/image provenance")


def derive_binding(predecessor, provenance, proof):
    """Only release, digests and provenance change. Semantics are copied exactly."""
    validate_provenance(provenance)
    verify_proof(proof, predecessor["provenance"]["version"], provenance["version"], provenance["source_commit"])
    if proof["predecessor_commit"] != predecessor["provenance"]["source_commit"]:
        raise ValueError("O3 predecessor SHA mismatch")
    result = copy.deepcopy(predecessor)
    result["provenance"] = copy.deepcopy(provenance)
    result["binding"]["image_index_digest"] = provenance["image_index_digest"]
    return result


def passing_subset(predecessor, results):
    mapping = groups()
    known = {p for values in mapping.values() for p in values}
    ids = [p["capability_id"] for p in predecessor]
    if len(ids) != len(set(ids)) or not set(ids) <= known:
        raise ValueError("unmapped or duplicate predecessor profile: binding PR required")
    if set(results) - set(mapping):
        raise ValueError("unknown result group")
    allowed = {p for name, values in mapping.items() if results.get(name) is True for p in values}
    selected = [copy.deepcopy(p) for p in predecessor if p["capability_id"] in allowed]
    # References themselves, not just capability IDs, must be inherited.
    if any(p not in predecessor for p in selected):
        raise ValueError("never add a profile")
    return selected


def run_identity():
    return {k: os.environ.get(k, "") for k in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA")}


def verify_context(value, expected=None):
    from core_lane_bindings import REVIEWED_CORE_BINDINGS
    expected = run_identity() if expected is None else expected
    if (value.get("model") != "core-evidence-context-v1" or value.get("run") != expected
            or not all(expected.values()) or not SHA.fullmatch(expected["GITHUB_SHA"])):
        raise ValueError("binding proof must come from the same run/attempt/head")
    if value.get("manifest_sha256") != digest(canonical(manifest())):
        raise ValueError("contract-read manifest changed")
    provenance = value["provenance"]
    validate_provenance(provenance)
    anchor = value["anchor"]
    base = REVIEWED_CORE_BINDINGS.get(anchor)
    if base is None:
        raise ValueError("unreviewed binding anchor")
    fields = asdict(base)
    fields["image_index_digest"] = provenance["image_index_digest"]
    if value["binding"] != fields:
        raise ValueError("derived binding changes semantics")
    if value["mode"] == "main":
        committed = read_json(ROOT / "tests/fixtures" / ("core_" + anchor.replace(".", "_") + "_lane_provenance.json"))
        if provenance != committed or value["binding"] != asdict(base):
            raise ValueError("main binding provenance mismatch")
    elif value["mode"] == "derived":
        verify_proof(value["proof"], value["predecessor"], provenance["version"], provenance["source_commit"])
    else:
        raise ValueError("binding without verified proof")
    return value


def context(version=None):
    path = os.environ.get(CONTEXT_ENV)
    if not path:
        return None
    raw = Path(path).read_bytes()
    if digest(raw) != os.environ.get("CORE_EVIDENCE_CONTEXT_SHA256"):
        raise ValueError("binding artifact digest mismatch")
    value = verify_context(read_json(path))
    if version is not None and value["provenance"]["version"] != version:
        raise ValueError("binding release mismatch")
    return value


def slot(value):
    p = value["provenance"]; v = p["version"]
    return {"slot": "release", "version": v, "lane": "ha-" + v.replace(".", "-"),
            "image": f"ghcr.io/home-assistant/home-assistant:{v}@{p['image_index_digest']}",
            "image_index_digest": p["image_index_digest"], "source_commit": p["source_commit"],
            "source_archive_sha256": p["source_archive_sha256"], "provenance": copy.deepcopy(p)}


def handoff_request(version, candidate, evidence, *, registry_exists, dry_run):
    if not stable_version(version):
        raise ValueError("handoff requires an exact stable version")
    if dry_run or not registry_exists:
        return None
    return {"ref": "registry-candidates/" + version, "workflow": "core-release-registry-publish.yml",
            "workflow_ref": "main", "inputs": {"core_version": version,
            "candidate_ref": "registry-candidates/" + version, "candidate_sha256": digest(candidate),
            "evidence_sha256": digest(evidence), "dry_run": "false"}}
