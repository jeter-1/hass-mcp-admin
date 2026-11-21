"""Reuse a CI run's unit-test shard results as Evidence for the same tree (HAMCP-365).

This is the opt-in ``check.ps1 -Tier Evidence -ReuseCiRun <run-id>`` engine. It
runs no tests. It accepts a CI run's results only when all of the following
hold, and otherwise refuses with a fixed category:

- the checkout is clean, and the run is a completed, successful
  ``.github/workflows/ci.yml`` run of this repository (not a fork) for this
  exact HEAD commit, with every shard job and ``validate_source`` successful in
  the run's latest attempt;
- each shard's artifact from that attempt is present and unexpired, matches the
  digest and size GitHub records for it, and holds exactly one manifest;
- CI's own completeness proof (``unit_test_shards.py aggregate --binding
  tree``), run here in a child process against this checkout, accepts the
  manifests: this checkout's discovery and shard plan, every discovered test
  executed exactly once and succeeding, and every manifest naming HEAD's tree;
- GitHub records the commit CI tested with HEAD's tree, and that commit is HEAD
  itself or, for a pull request, GitHub's merge commit whose second parent is
  HEAD; and
- neither the run nor the checkout changed while the proof was made.

Reused results are trusted CI job output, not an independent signature. Only
runs whose manifests name their tree (written after HAMCP-365) can be reused,
and only while their artifacts are retained. Every GitHub read is one bounded
GET through ``github_retry.api_get``; no response text is reported.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import zipfile
import zlib

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import local_unit_test_shards  # noqa: E402  (the output layout and owned latest/ receipt)
import unit_test_shards  # noqa: E402  (the shared CI module; never copied)

_spec = importlib.util.spec_from_file_location("hamcp365_github_retry", SCRIPTS / "github_retry.py")
retry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(retry)

Refusal = unit_test_shards.Refusal
require = unit_test_shards.require

SUMMARY_SCHEMA_VERSION = 1
WORKFLOW_PATH = ".github/workflows/ci.yml"
EVENTS = frozenset({"pull_request", "schedule", "workflow_dispatch"})
RUN_FIELDS = ("id", "run_attempt", "status", "conclusion", "head_sha", "path", "event")
PAGE = 100
PROOF_TIMEOUT = 1800
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}")
RUN_ID = re.compile(r"[1-9][0-9]{0,19}")
SHA = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,2}[A-Za-z0-9.+-]{0,20}")
SHARD_JOB = re.compile(r"unit tests shard (0|[1-9][0-9]*)")
PROOF_REFUSAL = re.compile(r"Unit test shard validation refused: ([a-z0-9_]+)")
ZIP_ERRORS = (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError, NotImplementedError,
              RuntimeError, ValueError, struct.error, zlib.error)
PREFIX = "CI unit-shard reuse refused: "


def github_read(route):
    return retry.api_get(route)


def fetch(read, route):
    """One bounded GET; a failure is a fixed category, never response text."""

    try:
        body = read(route)
    except retry.GitHubReadError as exc:
        raise Refusal(f"{exc.category}:{exc.status}") from None
    except (ValueError, OSError) as exc:
        detail = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[a-z0-9_]+", str(exc)) \
            else type(exc).__name__
        raise Refusal(f"github_read_refused:{detail}") from None
    require(isinstance(body, bytes), "github_response_malformed")
    require(len(body) <= retry.API_BOUND, "github_response_too_large")
    return body


def strict_json(raw, category):
    """Parse untrusted JSON; duplicate keys and NaN/Infinity are refused."""

    def unique(pairs):
        require(len({key for key, _ in pairs}) == len(pairs), category)
        return dict(pairs)

    def finite(_constant):
        raise Refusal(category)

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=finite)
    except (ValueError, RecursionError) as exc:
        raise Refusal(category) from exc


def snapshot(root):
    """HEAD and its tree, refusing a checkout whose files differ from them."""

    try:
        status = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
                                check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=120).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refusal("git_status_unavailable") from exc
    require(not status.strip(), "worktree_not_clean")
    return unit_test_shards.head(root), unit_test_shards.tree(root)


def check_run(run, run_id, repository, head):
    require(isinstance(run, dict) and type(run.get("id")) is int and run["id"] == run_id, "ci_run_mismatch")
    for key in ("repository", "head_repository"):
        owner = run.get(key)
        require(isinstance(owner, dict) and isinstance(owner.get("full_name"), str)
                and owner["full_name"].lower() == repository.lower(), "ci_run_repository_mismatch")
    require(run.get("path") == WORKFLOW_PATH, "ci_run_workflow_mismatch")
    require(isinstance(run.get("event"), str) and run["event"] in EVENTS, "ci_run_event_unsupported")
    require(run.get("status") == "completed" and run.get("conclusion") == "success", "ci_run_not_successful")
    require(run.get("head_sha") == head, "ci_run_head_mismatch")
    attempt = run.get("run_attempt")
    require(type(attempt) is int and attempt >= 1, "ci_run_mismatch")
    return attempt


def check_jobs(listing, run_id, attempt, head, shard_count):
    require(isinstance(listing, dict), "ci_jobs_malformed")
    jobs = listing.get("jobs")
    require(isinstance(jobs, list) and type(listing.get("total_count")) is int
            and listing["total_count"] == len(jobs) <= PAGE, "ci_jobs_incomplete")
    shards, validate_source = {}, []
    for job in jobs:
        require(isinstance(job, dict) and isinstance(job.get("name"), str), "ci_jobs_malformed")
        match = SHARD_JOB.fullmatch(job["name"])
        if match:
            index = int(match.group(1))
            require(index < shard_count and index not in shards, "ci_shard_jobs_mismatch")
            shards[index] = job
        elif job["name"] == "validate_source":
            validate_source.append(job)
    require(len(shards) == shard_count, "ci_shard_jobs_mismatch")
    require(len(validate_source) == 1, "ci_validate_source_missing")
    for job in (*shards.values(), *validate_source):
        require(job.get("status") == "completed" and job.get("conclusion") == "success", "ci_job_not_successful")
        require(type(job.get("run_id")) is int and job["run_id"] == run_id
                and type(job.get("run_attempt")) is int and job["run_attempt"] == attempt
                and job.get("head_sha") == head, "ci_job_identity_mismatch")


def select_artifacts(listing, run_id, attempt, head, shard_count):
    """One artifact per shard, by the exact name ci.yml gives this attempt's upload."""

    require(isinstance(listing, dict), "ci_artifacts_malformed")
    items = listing.get("artifacts")
    require(isinstance(items, list) and type(listing.get("total_count")) is int
            and listing["total_count"] == len(items) <= PAGE, "ci_artifacts_incomplete")
    wanted = {f"unit-test-shard-{index}-{run_id}-{attempt}": index for index in range(shard_count)}
    found = {}
    for item in items:
        require(isinstance(item, dict) and isinstance(item.get("name"), str), "ci_artifacts_malformed")
        index = wanted.get(item["name"])
        if index is not None:
            require(index not in found, "ci_artifact_duplicate")
            found[index] = item
    require(len(found) == shard_count, "ci_artifact_missing")
    selected = []
    for index in range(shard_count):
        item = found[index]
        origin = item.get("workflow_run")
        require(type(item.get("id")) is int and item["id"] > 0, "ci_artifacts_malformed")
        require(item.get("expired") is False, "ci_artifact_expired")
        require(isinstance(origin, dict) and type(origin.get("id")) is int and origin["id"] == run_id
                and origin.get("head_sha") == head, "ci_artifact_identity_mismatch")
        require(isinstance(item.get("digest"), str) and DIGEST.fullmatch(item["digest"]), "ci_artifact_digest_missing")
        require(type(item.get("size_in_bytes")) is int and 0 < item["size_in_bytes"] <= retry.API_BOUND,
                "ci_artifact_size_unsupported")
        selected.append({"shard": index, "name": item["name"], "id": item["id"], "digest": item["digest"],
                         "size": item["size_in_bytes"]})
    return selected


def read_manifest(body, artifact):
    """The archive's single manifest, after checking the archive is the one GitHub listed."""

    require(len(body) == artifact["size"] and "sha256:" + hashlib.sha256(body).hexdigest() == artifact["digest"],
            "ci_artifact_digest_mismatch")
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
        members = archive.infolist()
    except ZIP_ERRORS as exc:
        raise Refusal("ci_artifact_unreadable") from exc
    with archive:
        require(len(members) == 1, "ci_artifact_layout")
        member = members[0]
        require(member.filename == "manifest.json" and not member.is_dir() and not member.flag_bits & 0x1
                and member.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                and 0 < member.file_size <= unit_test_shards.MAX_MANIFEST, "ci_artifact_layout")
        try:
            with archive.open(member) as stream:
                # Reading past the declared size reaches the end, where the CRC is checked.
                raw = stream.read(unit_test_shards.MAX_MANIFEST + 1)
        except ZIP_ERRORS as exc:
            raise Refusal("ci_artifact_unreadable") from exc
    require(len(raw) == member.file_size, "ci_artifact_unreadable")
    manifest = strict_json(raw, "ci_manifest_malformed")
    require(isinstance(manifest, dict) and type(manifest.get("shard")) is int
            and manifest["shard"] == artifact["shard"], "ci_manifest_shard_mismatch")
    return raw, manifest


def run_proof(root, layout, head, run_id, attempt):
    """CI's completeness proof over the downloaded manifests, in a child process so
    test-module discovery never imports into this one."""

    command = [sys.executable, str(SCRIPTS / "unit_test_shards.py"), "aggregate", "--binding", "tree",
               "--expected-source-sha", head, "--manifests", str(layout["manifests"]), "--repo-root", str(root)]
    env = {**os.environ, "GITHUB_RUN_ID": str(run_id), "GITHUB_RUN_ATTEMPT": str(attempt)}
    try:
        result = subprocess.run(command, cwd=root, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, errors="replace", timeout=PROOF_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise Refusal("ci_proof_timeout") from exc
    except OSError as exc:
        raise Refusal("ci_proof_unavailable") from exc
    (layout["logs"] / "aggregate.log").write_text(
        f"$ {' '.join(command)}\nexit {result.returncode}\n--- stdout\n{result.stdout}\n--- stderr\n{result.stderr}",
        encoding="utf-8")
    if result.returncode:
        refused = PROOF_REFUSAL.search(result.stderr)
        raise Refusal(f"ci_proof_refused:{refused.group(1)}" if refused else f"ci_proof_error:exit_{result.returncode}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    require(lines, "ci_proof_malformed")
    return strict_json(lines[-1].encode(), "ci_proof_malformed")


def check_proof(proof, local_tree, run_id, attempt, shard_count):
    require(isinstance(proof, dict) and proof.get("result") == "success" and proof.get("binding") == "tree"
            and proof.get("source_tree") == local_tree and proof.get("run_id") == str(run_id)
            and proof.get("run_attempt") == str(attempt) and proof.get("shard_count") == shard_count
            and isinstance(proof.get("ci_source_sha"), str) and SHA.fullmatch(proof["ci_source_sha"])
            and all(isinstance(proof.get(key), str) and SHA256.fullmatch(proof[key])
                    for key in ("discovered_sha256", "plan_sha256")), "ci_proof_malformed")
    outcomes = proof.get("outcomes")
    require(isinstance(outcomes, dict) and all(type(count) is int and count >= 0 for count in outcomes.values())
            and type(proof.get("discovered_count")) is int
            and sum(outcomes.values()) == proof["discovered_count"], "ci_proof_malformed")


def check_commit(commit, ci_source, local_tree, head, event):
    require(isinstance(commit, dict) and commit.get("sha") == ci_source, "ci_commit_mismatch")
    tree_ref = commit.get("tree")
    require(isinstance(tree_ref, dict) and tree_ref.get("sha") == local_tree, "ci_commit_tree_mismatch")
    if ci_source != head:
        # A pull-request run tests GitHub's merge of HEAD into its base; that
        # merge has HEAD's tree only when the base adds nothing HEAD lacks.
        parents = commit.get("parents")
        require(event == "pull_request" and isinstance(parents, list) and len(parents) == 2
                and isinstance(parents[1], dict) and parents[1].get("sha") == head, "ci_commit_not_head_merge")


def prove(root, repository, run_id, head, local_tree, layout, summary, read):
    general = unit_test_shards.load_plan_file(root)[0]
    shard_count = general + 1
    summary.update(shard_count=shard_count, general_shards=general, serial_shards=[general])
    actions = f"repos/{repository}/actions"
    run = strict_json(fetch(read, f"{actions}/runs/{run_id}"), "ci_run_malformed")
    attempt = check_run(run, run_id, repository, head)
    summary.update(run_attempt=attempt, event=run["event"],
                   run_url=f"https://github.com/{repository}/actions/runs/{run_id}/attempts/{attempt}")
    jobs = strict_json(fetch(read, f"{actions}/runs/{run_id}/attempts/{attempt}/jobs?per_page={PAGE}"),
                       "ci_jobs_malformed")
    check_jobs(jobs, run_id, attempt, head, shard_count)
    listing = strict_json(fetch(read, f"{actions}/runs/{run_id}/artifacts?per_page={PAGE}"), "ci_artifacts_malformed")
    versions = set()
    for artifact in select_artifacts(listing, run_id, attempt, head, shard_count):
        raw, manifest = read_manifest(fetch(read, f"{actions}/artifacts/{artifact['id']}/zip"), artifact)
        (layout["manifests"] / f"shard-{artifact['shard']}.json").write_bytes(raw)
        version = manifest.get("python_version")
        versions.add(version if isinstance(version, str) and VERSION.fullmatch(version) else "unknown")
        summary["artifacts"].append({key: artifact[key] for key in ("shard", "name", "id", "digest")})
    summary["python_versions"] = sorted(versions)
    proof = run_proof(root, layout, head, run_id, attempt)
    check_proof(proof, local_tree, run_id, attempt, shard_count)
    ci_source = proof["ci_source_sha"]
    commit = strict_json(fetch(read, f"repos/{repository}/git/commits/{ci_source}"), "ci_commit_malformed")
    check_commit(commit, ci_source, local_tree, head, run["event"])
    outcomes = proof["outcomes"]
    summary.update(ci_source_sha=ci_source, discovered_count=proof["discovered_count"],
                   discovered_sha256=proof["discovered_sha256"], plan_sha256=proof["plan_sha256"],
                   outcomes=outcomes, executed_tests=sum(outcomes.values()), skipped=outcomes.get("skipped", 0))
    again = strict_json(fetch(read, f"{actions}/runs/{run_id}"), "ci_run_malformed")
    require(isinstance(again, dict) and all(again.get(key) == run.get(key) for key in RUN_FIELDS), "ci_run_changed")
    require(snapshot(root) == (head, local_tree), "worktree_changed")


def reuse(root, repository, run_id, output_dir, read=github_read):
    """Write ``summary.json`` in a new ``output_dir`` and return it. ``result`` is
    ``passed`` only when every check in the module docstring held."""

    root = Path(root).resolve()
    require(isinstance(repository, str) and REPOSITORY.fullmatch(repository)
            and repository.split("/")[1] not in {".", ".."}, "invalid_repository")
    require(isinstance(run_id, str) and RUN_ID.fullmatch(run_id), "invalid_run_id")
    run_id = int(run_id)
    head, local_tree = snapshot(root)
    # Absolute, because the proof's child process runs with ``root`` as its cwd.
    layout = local_unit_test_shards.prepare_layout(Path(output_dir).absolute())
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION, "mode": "reused_ci", "result": "failed", "problems": [],
        "repository": repository, "run_id": run_id, "run_attempt": None, "run_url": None, "event": None,
        "head_sha": head, "source_tree": local_tree, "ci_source_sha": None,
        "shard_count": None, "general_shards": None, "serial_shards": None,
        "discovered_count": None, "discovered_sha256": None, "plan_sha256": None,
        "outcomes": None, "executed_tests": None, "skipped": None, "python_versions": [], "artifacts": [],
    }
    try:
        prove(root, repository, run_id, head, local_tree, layout, summary, read)
    except Refusal as exc:
        summary["problems"].append(str(exc))
    except Exception as exc:  # A defect here must never read as a pass.
        summary["problems"].append(f"reuse_error:{type(exc).__name__}")
    summary["result"] = "failed" if summary["problems"] else "passed"
    (layout["root"] / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    return summary


def main(argv=None, read=github_read):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repository", required=True, help="OWNER/NAME of this checkout's GitHub repository")
    parser.add_argument("--run-id", required=True, help="the ci.yml run whose latest attempt tested this HEAD")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--replace-output", action="store_true",
                        help="overwrite only the owned .artifacts/unit-test-shards/latest receipt")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        if args.replace_output:
            with local_unit_test_shards.latest_output(args.repo_root, args.output_dir):
                summary = reuse(args.repo_root, args.repository, args.run_id, args.output_dir, read)
        else:
            summary = reuse(args.repo_root, args.repository, args.run_id, args.output_dir, read)
    except Refusal as exc:
        parser.exit(1, PREFIX + str(exc) + "\n")
    print(f"Summary: {Path(args.output_dir) / 'summary.json'}", flush=True)
    if summary["result"] != "passed":
        parser.exit(1, PREFIX + "; ".join(summary["problems"]) + "\n")
    print(f"Reused {summary['executed_tests']} unit-test results from CI run {summary['run_id']} attempt "
          f"{summary['run_attempt']} for tree {summary['source_tree']}; no test ran locally.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
