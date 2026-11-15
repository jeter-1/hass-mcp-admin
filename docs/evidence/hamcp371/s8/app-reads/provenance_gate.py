"""HAMCP-371 S8 Q7-b: the provenance gate for the 9.2.0 App images.

Ruling Q7-b (a), 2026-11-15 08:05 CST. It replaces the withdrawn label gate
of ``check_app_reads.py``, which stays as the record of that run.

Run from the repository root with ``python -I``. Reads only the committed
files in ``raw/``, after checking each against ``SHA256SUMS``. On both
architectures the gate requires:

- source commit ``SOURCE_COMMIT`` (SLSA ``vcs:revision``) and repository
  ``REPOSITORY`` (``vcs:source``, ``github_repository`` and its ids);
- a run in that repository (the builder id names the run and attempt) on a
  GitHub-hosted runner;
- that run on the repository's default branch (from the event payload), as a
  branch ref that GitHub reports protected;
- an embedded Dockerfile whose git object id is ``DOCKERFILE_BLOB``, the
  ``homeassistant-addon/Dockerfile`` entry of the v9.2.0 tree;
- the index digest, the image manifest digest and the platform the reads
  recorded, with the index's attestation manifest referring to that image
  manifest.

Disclosed, not gated: the workflow file commit (``github_workflow_sha``)
differs from the source commit, and the provenance is an unsigned BuildKit
statement. Exit 0 only if every gate holds on both architectures.
"""

import base64
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
RAW = BASE / "raw"
SOURCE_COMMIT = "51e3aac788482a081807591cd5d58d07dc4e540b"
REPOSITORY = "homeassistant-ai/ha-mcp"
REPOSITORY_ID = "1056618941"
OWNER_ID = "232192432"
SERVER = "https://github.com"
DOCKERFILE_BLOB = "119a01c01755c3b681afeb415c4ebb0ebb7fbb09"
DOCKERFILE_DIR = "homeassistant-addon"
WORKFLOW_SHA = "e664ece999585b64e936a887d1be474d8fa9988a"
EXPECTED = {
    "amd64": {
        "index": "sha256:e0fd8903915be64f65744f7fa78d8d46432388159bc000ff0807079f494fe3ba",
        "manifest": "sha256:928c55d6ba38d0219058be2e0ae4ae36375eb3db7aad0cb21c5b573848ed9244",
        "architecture": "amd64",
        "target": "linux/amd64",
    },
    "aarch64": {
        "index": "sha256:54459403f8bc0c77d108d8df5f3f64cc64f98eaf0d79dafdb7df5832fc9c326a",
        "manifest": "sha256:de36201ee267e111cb7f675ea978aef8861bd5ddc812f86976425c572e9010dc",
        "architecture": "arm64",
        "target": "linux/arm64",
    },
}


def _sums():
    sums = {}
    for line in (BASE / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        sums[name] = digest
    return sums


def _read(sums, name):
    raw = (RAW / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != sums[name]:
        raise SystemExit(f"sha256 mismatch: {name}")
    return raw


def _gate(failures, arch, name, actual, expected):
    ok = actual == expected
    print(f"{arch}: {'PASS' if ok else 'FAIL'} {name} {actual}")
    if not ok:
        failures.append(f"{arch}: {name} is {actual!r}, not {expected!r}")


def check(sums, arch, expected, failures):
    index_raw = _read(sums, f"index-{arch}.raw.json")
    manifest_raw = _read(sums, f"manifest-{arch}.raw.json")
    index_digest = "sha256:" + hashlib.sha256(index_raw).hexdigest()
    manifest_digest = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
    _gate(failures, arch, "index digest", index_digest, expected["index"])
    _gate(failures, arch, "image manifest digest", manifest_digest, expected["manifest"])
    manifests = json.loads(index_raw)["manifests"]
    images = [
        item["digest"] for item in manifests
        if item.get("platform", {}).get("architecture") == expected["architecture"]
    ]
    _gate(failures, arch, "index image manifests", images, [manifest_digest])
    references = [
        (item.get("annotations") or {}).get("vnd.docker.reference.digest")
        for item in manifests
        if (item.get("annotations") or {}).get("vnd.docker.reference.type") == "attestation-manifest"
    ]
    _gate(failures, arch, "attestation manifest refers to", references, [manifest_digest])

    slsa = json.loads(_read(sums, f"provenance-{arch}.json"))["SLSA"]
    args = slsa["buildDefinition"]["externalParameters"]["request"]["root"]["request"]["args"]
    internal = slsa["buildDefinition"]["internalParameters"]
    payload = internal["github_event_payload"]
    _gate(failures, arch, "vcs:revision", args.get("vcs:revision"), SOURCE_COMMIT)
    _gate(failures, arch, "vcs:source", args.get("vcs:source"), f"{SERVER}/{REPOSITORY}")
    _gate(failures, arch, "github_server_url", internal.get("github_server_url"), SERVER)
    _gate(failures, arch, "github_repository", internal.get("github_repository"), REPOSITORY)
    _gate(failures, arch, "github_repository_id", internal.get("github_repository_id"), REPOSITORY_ID)
    _gate(failures, arch, "github_repository_owner_id", internal.get("github_repository_owner_id"), OWNER_ID)
    _gate(failures, arch, "event repository", (payload["repository"].get("full_name"), str(payload["repository"].get("id"))), (REPOSITORY, REPOSITORY_ID))
    run = f"{SERVER}/{REPOSITORY}/actions/runs/{internal.get('github_run_id')}/attempts/{internal.get('github_run_attempt')}"
    _gate(failures, arch, "builder id", slsa["runDetails"]["builder"]["id"], run)
    _gate(failures, arch, "github_runner_environment", internal.get("github_runner_environment"), "github-hosted")
    default_branch = payload["repository"].get("default_branch")
    _gate(failures, arch, "github_ref", internal.get("github_ref"), f"refs/heads/{default_branch}")
    _gate(failures, arch, "github_ref_type", internal.get("github_ref_type"), "branch")
    _gate(failures, arch, "github_ref_protected", internal.get("github_ref_protected"), "true")
    _gate(failures, arch, "targetPlatform", internal.get("targetPlatform"), expected["target"])
    _gate(failures, arch, "vcs:localdir:dockerfile", args.get("vcs:localdir:dockerfile"), DOCKERFILE_DIR)
    infos = slsa["runDetails"]["metadata"]["buildkit_metadata"]["source"]["infos"]
    dockerfiles = [info for info in infos if info.get("filename") == "Dockerfile"]
    blobs = []
    for info in dockerfiles:
        data = base64.b64decode(info["data"])
        blobs.append(hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest())
    _gate(failures, arch, "embedded Dockerfile git blob", blobs, [DOCKERFILE_BLOB])
    print(f"{arch}: run {internal.get('github_run_id')} attempt {internal.get('github_run_attempt')}"
          f" workflow {internal.get('github_workflow_ref')} job {internal.get('github_job')}"
          f" event {internal.get('github_event_name')} default branch {default_branch}")
    workflow_sha = internal.get("github_workflow_sha")
    note = "differs from" if workflow_sha != SOURCE_COMMIT else "equals"
    print(f"{arch}: DISCLOSED workflow file commit {workflow_sha} {note} the source commit {SOURCE_COMMIT}")
    _gate(failures, arch, "workflow file commit (recorded)", workflow_sha, WORKFLOW_SHA)
    return (internal.get("github_run_id"), internal.get("github_run_attempt"), workflow_sha)


def main():
    sums = _sums()
    if sorted(sums) != sorted(path.name for path in RAW.iterdir()):
        raise SystemExit("raw/ and SHA256SUMS list different files")
    failures = []
    runs = {arch: check(sums, arch, expected, failures) for arch, expected in EXPECTED.items()}
    _gate(failures, "both", "same run, attempt and workflow commit", len(set(runs.values())), 1)
    print("DISCLOSED provenance: unsigned BuildKit in-toto statement, read anonymously"
          " from the registry; the HAMCP-351 evidence class, which relies on upstream's"
          " registry write control")
    for failure in failures:
        print(f"GATE FAIL {failure}")
    print("Q7-b provenance gate", "FAIL" if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
