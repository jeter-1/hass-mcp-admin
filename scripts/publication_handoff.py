"""Verify a completed protected merge before transferring publication authority.

Artifacts are bounded data, never executable authority. All API routes are fixed
repository routes built from validated numeric IDs; returned URLs are not used.
"""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

from validate_ci_provenance import Refusal, collection, positive_id, require, unique_keys
from validate_ready_authorization import validate_timeline

REPOSITORY = "jeter-1/hass-mcp-admin"
OWNER = "jeter-1"
PRODUCER = ".github/workflows/enable-auto-merge.yml"
PREFIX = "repos/" + REPOSITORY
BOUND = 4_000_000
RECEIPT_BOUND = 8192
POLICY_PATHS = (PRODUCER, "scripts/publication_handoff.py",
                "scripts/validate_ready_authorization.py", "scripts/validate_ci_provenance.py")


def sha(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value), "invalid_sha")
    return value


def check_binding(value, release):
    """Bounded source identity carried from verification into the attempt claim."""
    require(isinstance(value, dict) and set(value) == {
        "release_sha", "run_id", "run_attempt", "workflow_id", "event", "head_sha"
    }, "handoff_binding_shape")
    require(sha(value["release_sha"]) == release, "handoff_binding_release")
    sha(value["head_sha"])
    for key in ("run_id", "run_attempt", "workflow_id"):
        positive_id(value[key])
        require(value[key] < 10 ** 20, "handoff_binding_identity")
    require(value["run_attempt"] <= 100
            and value["event"] in {"pull_request_target", "issue_comment"}, "handoff_binding_identity")
    return value


def read_api(route, binary=False):
    with tempfile.TemporaryFile() as output:
        try:
            result = subprocess.run(
                ["gh", "api", route, "--hostname", "github.com", "--method", "GET"],
                stdout=output, stderr=subprocess.DEVNULL, timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Refusal("github_read_unavailable") from exc
        require(result.returncode == 0, "github_read_failed")
        output.seek(0)
        raw = output.read(BOUND + 1)
    require(len(raw) <= BOUND, "github_response_bound")
    if binary:
        return raw
    return json.loads(raw, object_pairs_hook=unique_keys)


def git(*args):
    try:
        result = subprocess.run(["git", *args], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, check=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refusal("git_evidence_unavailable") from exc
    require(len(result.stdout) <= BOUND, "git_output_bound")
    return result.stdout.decode().strip()


def timeline(pr, api):
    # Fail closed rather than silently ignoring older lifecycle events.
    events = api(f"{PREFIX}/issues/{pr}/timeline?per_page=100")
    require(isinstance(events, list) and len(events) < 100
            and all(isinstance(item, dict) for item in events), "timeline_incomplete")
    return events


def merged_pr(pr, base, head, merge, api, run_git):
    value = api(f"{PREFIX}/pulls/{positive_id(pr)}")
    require(value.get("number") == pr and value.get("merged") is True
            and value.get("state") == "closed" and value.get("draft") is False
            and value.get("merge_commit_sha") == merge
            and value.get("base", {}).get("ref") == "main"
            and value.get("head", {}).get("sha") == head,
            "merged_pr_mismatch")
    require(all(value.get(side, {}).get("repo", {}).get("full_name") == REPOSITORY
                for side in ("base", "head")), "foreign_pr")
    parents = run_git("show", "-s", "--format=%P", merge).split()
    require(parents == [base, head], "merge_parents_mismatch")
    tree = run_git("merge-tree", "--write-tree", base, head)
    require(tree == run_git("rev-parse", merge + "^{tree}"), "merge_tree_mismatch")
    return value


def ready_before_merge(events, merge, expected_id):
    matching = [i for i, item in enumerate(events)
                if item.get("event") == "merged" and item.get("commit_id") == merge]
    require(len(matching) == 1, "merge_timeline_mismatch")
    result = validate_timeline(events[:matching[0]])
    require(result.get("status") == "authorized" and result.get("event_id") == expected_id,
            "ready_authority_changed")
    # Later Ready/force-push/reopen activity cannot borrow the consumed decision.
    require(not any(item.get("event") in {"ready_for_review", "reopened", "head_ref_force_pushed",
                                         "convert_to_draft", "base_ref_changed"}
                    for item in events[matching[0] + 1:]), "post_merge_lifecycle_changed")


def produce(env, ready, api=read_api, run_git=git):
    require(env.get("GITHUB_REPOSITORY") == REPOSITORY, "wrong_repository")
    require(env.get("GITHUB_ACTOR") == OWNER and env.get("GITHUB_TRIGGERING_ACTOR") == OWNER,
            "wrong_owner")
    base, head = sha(env.get("AUTHORIZED_BASE_SHA")), sha(env.get("AUTHORIZED_HEAD_SHA"))
    require(env.get("GITHUB_WORKFLOW_SHA") == base, "producer_workflow_base_mismatch")
    require(run_git("rev-parse", "HEAD") == base, "untrusted_producer_checkout")
    require(env.get("GITHUB_EVENT_NAME") in {"pull_request_target", "issue_comment"}, "wrong_event")
    pr = positive_id(int(env["PR_NUMBER"]))
    value = api(f"{PREFIX}/pulls/{pr}")
    merge = sha(value.get("merge_commit_sha"))
    merged_pr(pr, base, head, merge, api, run_git)
    ready_id = positive_id(ready.get("event_id"))
    ready_before_merge(timeline(pr, api), merge, ready_id)
    return {
        "schema": 1, "repository": REPOSITORY, "pr_number": pr,
        "base_sha": base, "head_sha": head, "merge_sha": merge,
        "ready_event_id": ready_id,
        "source_run_id": positive_id(int(env["GITHUB_RUN_ID"])),
        "source_run_attempt": positive_id(int(env["GITHUB_RUN_ATTEMPT"])),
        "source_event": env["GITHUB_EVENT_NAME"], "source_workflow_sha": base,
    }


def unpack(raw, digest):
    require(isinstance(raw, bytes) and len(raw) <= BOUND, "artifact_bound")
    require(digest == "sha256:" + hashlib.sha256(raw).hexdigest(), "artifact_digest_mismatch")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(len(entries) == 1 and entries[0].filename == "publication-handoff.json"
                and 0 < entries[0].file_size <= RECEIPT_BOUND
                and not entries[0].is_dir(), "artifact_shape")
        raw_receipt = archive.read(entries[0])
    return json.loads(raw_receipt, object_pairs_hook=unique_keys)


def check_receipt(value, run_id, attempt):
    fields = {"schema", "repository", "pr_number", "base_sha", "head_sha", "merge_sha",
              "ready_event_id", "source_run_id", "source_run_attempt", "source_event", "source_workflow_sha"}
    require(isinstance(value, dict) and set(value) == fields, "receipt_shape")
    require(type(value["schema"]) is int and value["schema"] == 1
            and value["repository"] == REPOSITORY
            and value["source_workflow_sha"] == value["base_sha"]
            and positive_id(value["source_run_id"]) == run_id
            and positive_id(value["source_run_attempt"]) == attempt, "receipt_identity")
    for field in ("base_sha", "head_sha", "merge_sha"):
        sha(value[field])
    for field in ("pr_number", "ready_event_id"):
        positive_id(value[field])
    require(value["source_event"] in {"pull_request_target", "issue_comment"}, "receipt_event")


def verify(run_id, attempt, authority, api=read_api, run_git=git):
    positive_id(run_id)
    positive_id(attempt)
    sha(authority)
    workflow = api(f"{PREFIX}/actions/workflows/enable-auto-merge.yml")
    require(workflow.get("path") == PRODUCER and workflow.get("state") == "active", "wrong_workflow")
    workflow_id = positive_id(workflow.get("id"))
    route = f"{PREFIX}/actions/runs/{run_id}"
    run = api(route)
    require(run.get("id") == run_id and run.get("run_attempt") == attempt
            and run.get("workflow_id") == workflow_id and run.get("path") == PRODUCER
            and run.get("status") == "completed" and run.get("conclusion") == "success"
            and run.get("event") in {"pull_request_target", "issue_comment"}, "wrong_source_run")
    require(all(run.get(key, {}).get("full_name") == REPOSITORY
                for key in ("repository", "head_repository")), "foreign_source_run")
    require(all(run.get(key, {}).get("login") == OWNER for key in ("actor", "triggering_actor")),
            "source_owner_mismatch")
    jobs = collection(api(f"{route}/attempts/{attempt}/jobs?per_page=100"), "jobs")
    matches = [job for job in jobs if job.get("name") == "merge-authorized-head"]
    require(len(matches) == 1 and matches[0].get("run_id") == run_id
            and matches[0].get("run_attempt") == attempt
            and matches[0].get("head_sha") == run.get("head_sha"), "source_job_mismatch")
    if matches[0].get("conclusion") == "skipped":
        return {"release_action": "none"}
    require(matches[0].get("status") == "completed"
            and matches[0].get("conclusion") == "success", "source_merge_unsuccessful")
    artifacts = collection(api(f"{route}/artifacts?per_page=100"), "artifacts")
    name = f"publication-handoff-{run_id}-{attempt}"
    artifacts = [item for item in artifacts if item.get("name") == name]
    require(len(artifacts) == 1 and artifacts[0].get("expired") is False, "handoff_unavailable")
    artifact = artifacts[0]
    artifact_id = positive_id(artifact.get("id"))
    require(type(artifact.get("size_in_bytes")) is int
            and 0 < artifact["size_in_bytes"] <= BOUND
            and artifact.get("workflow_run", {}).get("id") == run_id, "artifact_identity")
    value = unpack(api(f"{PREFIX}/actions/artifacts/{artifact_id}/zip", binary=True), artifact.get("digest"))
    check_receipt(value, run_id, attempt)
    base, head, merge = (value[key] for key in ("base_sha", "head_sha", "merge_sha"))
    require(value["source_event"] == run["event"], "source_event_mismatch")
    require(run.get("head_sha") == (head if run["event"] == "pull_request_target" else base),
            "source_commit_mismatch")
    # The completed source ran reviewed base policy, not policy supplied by the PR.
    for path in POLICY_PATHS:
        require(run_git("rev-parse", f"{base}:{path}") == run_git("rev-parse", f"{authority}:{path}"),
                "producer_policy_changed")
    merged_pr(value["pr_number"], base, head, merge, api, run_git)
    ready_before_merge(timeline(value["pr_number"], api), merge, value["ready_event_id"])
    guard(merge, authority, run_git)
    current = api(route)
    require(all(current.get(key) == run.get(key) for key in (
        "id", "workflow_id", "path", "head_sha", "run_attempt", "event", "status", "conclusion",
        "actor", "triggering_actor", "repository", "head_repository")), "source_run_changed")
    binding = check_binding({
        "release_sha": merge, "run_id": run_id, "run_attempt": attempt,
        "workflow_id": workflow_id, "event": run["event"], "head_sha": run["head_sha"],
    }, merge)
    return {"release_action": "inspect", "release_sha": merge, "validation_base": base,
            "source_handoff": json.dumps(binding, sort_keys=True, separators=(",", ":"))}


def guard(release, authority, run_git=git):
    sha(release)
    sha(authority)
    require(run_git("rev-parse", "refs/remotes/origin/main") == authority, "protected_main_moved")
    require(release in run_git("rev-list", "--first-parent", authority).splitlines(),
            "release_not_retained")
    require(not run_git("diff", "--name-only", release, authority, "--", "hass_mcp_engineering_beta",
                        ".release"), "release_authority_drift")
    require(not run_git("ls-tree", "--name-only", authority, "--", ".release/next-version"),
            "unmaterialized_release")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("produce", "verify", "guard"))
    parser.add_argument("--ready", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--attempt", type=int)
    parser.add_argument("--authority")
    parser.add_argument("--release")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "produce":
            require(args.ready is not None and args.output is not None, "missing_receipt_path")
            ready = json.loads(args.ready.read_text(), object_pairs_hook=unique_keys)
            result = produce(os.environ, ready)
            args.output.write_text(json.dumps(result, sort_keys=True) + "\n")
        elif args.operation == "verify":
            result = verify(args.run_id, args.attempt, args.authority)
            if args.github_output:
                with args.github_output.open("a") as output:
                    for key, value in result.items():
                        output.write(f"{key}={value}\n")
        else:
            guard(args.release, args.authority)
            result = {"status": "verified"}
    except Refusal as exc:
        parser.exit(1, "Publication handoff refused: " + str(exc) + "\n")
    except (ValueError, KeyError, TypeError, AttributeError, OSError, zipfile.BadZipFile):
        # Do not print arbitrary API data, artifact contents or subprocess errors.
        parser.exit(1, "Publication handoff refused: evidence missing, stale or mismatched.\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
