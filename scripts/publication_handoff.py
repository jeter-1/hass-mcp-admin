"""Verify a completed protected merge before transferring publication authority.

Artifacts are bounded data, never executable authority. All API routes are fixed
repository routes built from validated numeric IDs; returned URLs are not used.
"""

import argparse
import email.utils
import hashlib
import io
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import time
from typing import Any
import zipfile

# Self-contained (HAMCP-204 M3): publishing jobs run this file as
# ``python3 -I -S``, which keeps sibling scripts and site-packages off sys.path.
# The definitions between the copy markers are verbatim copies of
# scripts/validate_ci_provenance.py, scripts/validate_ready_authorization.py and
# scripts/github_retry.py; tests.test_signing_workflow_supply_chain pins them
# to those sources.

# BEGIN COPY scripts/validate_ci_provenance.py
class Refusal(ValueError):
    pass


def require(condition, category):
    if not condition:
        raise Refusal(category)


def positive_id(value):
    require(type(value) is int and value > 0, "invalid_identity")
    return value


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def collection(value, key):
    require(isinstance(value, dict), "malformed_collection")
    items = value.get(key)
    count = value.get("total_count")
    require(type(count) is int and isinstance(items, list), "malformed_collection")
    require(count == len(items) and 0 <= count <= 100, "incomplete_collection")
    require(all(isinstance(item, dict) for item in items), "malformed_collection")
    return items
# END COPY


# BEGIN COPY scripts/validate_ready_authorization.py
AUTHORIZING_ACTOR = "jeter-1"
HEAD_LIFECYCLE_EVENTS = {
    "automatic_base_change_succeeded",
    "base_ref_changed",
    "closed",
    "committed",
    "convert_to_draft",
    "head_ref_deleted",
    "head_ref_force_pushed",
    "head_ref_restored",
    "ready_for_review",
    "reopened",
}


class AuthorizationError(ValueError):
    """The timeline does not prove current-head Ready authorization."""


def validate_timeline(timeline: list[dict[str, Any]]) -> dict[str, Any]:
    lifecycle = [
        item for item in timeline if item.get("event") in HEAD_LIFECYCLE_EVENTS
    ]
    if not lifecycle:
        raise AuthorizationError("pull-request timeline has no Ready lifecycle evidence")

    latest = lifecycle[-1]
    actor = latest.get("actor")
    actor_login = actor.get("login") if isinstance(actor, dict) else None
    if latest.get("event") != "ready_for_review" or actor_login != AUTHORIZING_ACTOR:
        raise AuthorizationError(
            "the latest head lifecycle event is not a Josh-authored Ready action"
        )
    return {
        "status": "authorized",
        "event_id": latest.get("id"),
        "actor": actor_login,
    }
# END COPY

# BEGIN COPY scripts/github_retry.py
MAX_ATTEMPTS = 5
MAX_TOTAL_SECONDS = 180.0
BASE_DELAY_SECONDS = 2.0
MAX_DELAY_SECONDS = 30.0
RETRY_AFTER_CAP_SECONDS = 60.0
GH_ATTEMPT_TIMEOUT_SECONDS = 30.0
MIN_ATTEMPT_SECONDS = 1.0
HEADER_BOUND = 65536
GH_DIAGNOSTIC_BOUND = 4096
GH_TLS_FAILURE = re.compile(rb"tls|ssl|x509|certificate", re.IGNORECASE)
GH_TRANSIENT_FAILURE = re.compile(
    rb"dial tcp|i/o timeout|context deadline exceeded|timeout awaiting response headers"
    rb"|client\.timeout exceeded|connection refused", re.IGNORECASE)
UNAVAILABLE_STATUSES = frozenset({"network", "timeout", "gh_unavailable"})


class GitHubReadError(Exception):
    """A read that failed closed; ``str()`` names only the host and status."""

    def __init__(self, host, status, attempts, exhausted):
        self.host = host
        self.status = status
        self.attempts = attempts
        self.exhausted = exhausted
        self.category = ("github_read_unavailable" if status in UNAVAILABLE_STATUSES
                         else "github_read_failed")
        reason = "retry bound reached" if exhausted else "not retried"
        super().__init__(f"GitHub read failed: host {host}, last status {status}, "
                         f"{attempts} attempt(s), {reason}")


class RetryableRead(Exception):
    def __init__(self, status, retry_after=None):
        super().__init__(status)
        self.status = status
        self.retry_after = retry_after


class FinalRead(Exception):
    def __init__(self, status):
        super().__init__(status)
        self.status = status


def retryable_status(status):
    return type(status) is int and (status == 429 or 500 <= status <= 599)


def http_outcome(status, retry_after):
    # Callers return the body for HTTP 200; every other status raises here.
    if retryable_status(status):
        raise RetryableRead(status, retry_after)
    raise FinalRead(status)


def retry_after_seconds(value, now):
    """Parse Retry-After as delta-seconds or an HTTP-date; None if unusable."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if re.fullmatch(r"[0-9]{1,10}", value):
        return float(int(value))
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if when is None or when.tzinfo is None:
        return None
    return max(0.0, when.timestamp() - now)


def backoff_delay(attempt, retry_after, jitter):
    """Seconds to wait before the next attempt; ``jitter`` is in [0, 1)."""
    if retry_after is not None:
        # Honor the server's request; the final, jittered wait never exceeds the cap.
        return min(RETRY_AFTER_CAP_SECONDS, retry_after + jitter)
    exponential = min(MAX_DELAY_SECONDS, BASE_DELAY_SECONDS * 2 ** (attempt - 1))
    return exponential * (0.5 + jitter / 2)


def bounded_retry(attempt_read, host, clock=None, sleep=None, jitter=None, wall=None):
    """Return ``attempt_read(remaining_seconds)`` under the shared bound."""
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    jitter, wall = jitter or random.random, wall or time.time
    start = clock()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        remaining = MAX_TOTAL_SECONDS - (clock() - start)
        try:
            return attempt_read(remaining)
        except FinalRead as exc:
            raise GitHubReadError(host, exc.status, attempt, False) from None
        except RetryableRead as exc:
            delay = backoff_delay(attempt, retry_after_seconds(exc.retry_after, wall()), jitter())
            # No attempt starts unless it can have MIN_ATTEMPT_SECONDS before the
            # total bound; each attempt is limited to the time that remains.
            if (attempt == MAX_ATTEMPTS
                    or clock() - start + delay + MIN_ATTEMPT_SECONDS > MAX_TOTAL_SECONDS):
                raise GitHubReadError(host, exc.status, attempt, True) from None
            sleep(delay)
    raise AssertionError("unreachable")


def parse_included(raw):
    """Split ``gh api --include`` output into status, lowercase headers, body."""
    end = raw.find(b"\n")
    if end < 0 or end > HEADER_BOUND:
        raise FinalRead("malformed_response")
    match = re.fullmatch(rb"HTTP/[0-9.]{1,3} ([0-9]{3})(?: [^\r\n]*)?\r?", raw[:end])
    if match is None:
        raise FinalRead("malformed_response")
    headers = {}
    position = end + 1
    while True:
        end = raw.find(b"\n", position)
        if end < 0 or end > HEADER_BOUND:
            raise FinalRead("malformed_response")
        line = raw[position:end].rstrip(b"\r")
        position = end + 1
        if not line:
            return int(match.group(1)), headers, raw[position:]
        name, separator, value = line.partition(b":")
        if not separator:
            raise FinalRead("malformed_response")
        headers[name.strip().lower().decode("latin-1")] = value.strip().decode("latin-1")


def gh_transport_failure(diagnostic):
    # gh names a failure that produced no response only on standard error,
    # which is classified here and never exported. Every TLS failure is final;
    # only a recognized connection or timeout failure is retried; anything else
    # (an EOF or reset, which could be mid-handshake) fails closed.
    if GH_TLS_FAILURE.search(diagnostic):
        return FinalRead("tls_failed")
    if GH_TRANSIENT_FAILURE.search(diagnostic):
        return RetryableRead("network")
    return FinalRead("transport_failed")


def gh_read_attempt(route, max_bytes, timeout, run=None):
    # The route is repository-relative and GET-only; gh authenticates with
    # GH_TOKEN. Neither gh diagnostics nor response text is exported.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as diagnostics:
        try:
            result = (run or subprocess.run)(
                ["gh", "api", route, "--include", "--hostname", "github.com",
                 "--method", "GET", "-H", "Accept: application/vnd.github+json"],
                stdout=output, stderr=diagnostics,
                timeout=min(GH_ATTEMPT_TIMEOUT_SECONDS, timeout), check=False,
            )
        except subprocess.TimeoutExpired:
            raise RetryableRead("timeout") from None
        except OSError:
            raise FinalRead("gh_unavailable") from None
        output.seek(0)
        raw = output.read(HEADER_BOUND + max_bytes + 1)
        diagnostics.seek(0)
        diagnostic = diagnostics.read(GH_DIAGNOSTIC_BOUND)
    if not raw:
        # gh prints no response when none arrived; exit 4 means no credentials.
        if result.returncode == 4:
            raise FinalRead("authentication_required")
        raise gh_transport_failure(diagnostic)
    status, headers, body = parse_included(raw)
    if status == 200 and result.returncode == 0:
        return body[:max_bytes + 1]
    http_outcome(status, headers.get("retry-after"))


def bounded_gh_read(route, max_bytes, run=None, **bounds):
    """One GitHub API GET under the shared bound.  At most ``max_bytes`` + 1
    body bytes are kept, so callers keep their own size refusal."""
    return bounded_retry(lambda timeout: gh_read_attempt(route, max_bytes, timeout, run),
                         "api.github.com", **bounds)
# END COPY

REPOSITORY = "jeter-1/hass-mcp-admin"
OWNER = "jeter-1"
PRODUCER = ".github/workflows/enable-auto-merge.yml"
PREFIX = "repos/" + REPOSITORY
BOUND = 4_000_000
RECEIPT_BOUND = 8192
POLICY_PATHS = (PRODUCER, "scripts/publication_handoff.py",
                "scripts/validate_ready_authorization.py", "scripts/validate_ci_provenance.py")
# Seconds between re-reads of the source attempt's jobs (HAMCP-258): six reads
# over 80 s. On 2026-10-30 a job record settled 34 s after its run completed.
JOB_READ_DELAYS = (5, 10, 15, 20, 30)
JOB_FIELD = re.compile(r"[a-z_]{1,32}")
JOB_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
MERGE_JOB = "merge-authorized-head"


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


def bounded_read_api(route, binary=False):
    """``read_api`` under the shared bounded retry (HAMCP-221), for the merge job."""
    try:
        raw = bounded_gh_read(route, BOUND)
    except GitHubReadError as exc:
        print(exc, file=sys.stderr)
        raise Refusal(exc.category) from None
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


def produce(env, ready, api=bounded_read_api, run_git=git):
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


def log_stderr(message):
    print(message, file=sys.stderr)


def observed(value):
    """A loggable job field: a bounded lowercase token, never raw API text."""
    if value is None:
        return "none"
    return value if isinstance(value, str) and JOB_FIELD.fullmatch(value) else "invalid"


def observed_name(value):
    return value if isinstance(value, str) and JOB_NAME.fullmatch(value) else "invalid"


def observed_attempt(value):
    return str(value) if type(value) is int and 0 < value < 1_000_000 else "invalid"


def job_list(response):
    """Shape-check one jobs read. Malformed or oversized evidence refuses at
    once; a well-formed page whose count disagrees with its list may still be
    settling, so it is returned for the caller to re-read."""
    require(isinstance(response, dict), "malformed_collection")
    jobs, count = response.get("jobs"), response.get("total_count")
    require(type(count) is int and isinstance(jobs, list), "malformed_collection")
    require(0 <= count <= 100 and len(jobs) <= 100, "incomplete_collection")
    require(all(isinstance(job, dict) for job in jobs), "malformed_collection")
    return jobs, count


def source_merge_job(route, run_id, attempt, head_sha, api, sleep, log):
    """Return the exact attempt's final merge job, re-reading a bounded number
    of times (HAMCP-258).

    The source run already reported ``completed``/``success``, so an absent,
    duplicated, unfinished or unsuccessful merge job, or a job count that
    disagrees with the listed jobs, contradicts it: GitHub can report a run
    complete before its job records settle. Only a complete list with exactly
    one merge job that is completed with ``success`` or ``skipped`` is
    returned. Only this exact run, attempt and head is ever read; a merge job
    bound to anything else, or malformed evidence, refuses at once.
    """
    reads = len(JOB_READ_DELAYS) + 1
    for read in range(1, reads + 1):
        prefix = f"Source merge job read {read}/{reads}: run {run_id} attempt {attempt}"
        response = api(f"{route}/attempts/{attempt}/jobs?per_page=100")
        try:
            jobs, count = job_list(response)
        except Refusal as refused:
            log(f"{prefix}, job list refused: {refused}")
            raise
        matches = [job for job in jobs if job.get("name") == MERGE_JOB]
        job = matches[0] if len(matches) == 1 else None
        listing = "; ".join(
            f"{observed_name(item.get('name'))} {observed(item.get('status'))}/"
            f"{observed(item.get('conclusion'))} attempt {observed_attempt(item.get('run_attempt'))}"
            for item in jobs) or "none"
        state = (f"{len(matches)} matches" if matches else "absent") if job is None else (
            f"status {observed(job.get('status'))}, conclusion {observed(job.get('conclusion'))}, "
            f"attempt {observed_attempt(job.get('run_attempt'))}")
        log(f"{prefix}, total_count {count}, {len(jobs)} job(s) listed [{listing}], "
            f"{MERGE_JOB} {state}")
        if not all(type(item.get("run_id")) is int and item["run_id"] == run_id
                   and type(item.get("run_attempt")) is int and item["run_attempt"] == attempt
                   and item.get("head_sha") == head_sha for item in matches):
            log(f"Source merge job refused at read {read}/{reads}: source_job_mismatch, "
                f"{MERGE_JOB} bound to another run, attempt or head")
            raise Refusal("source_job_mismatch")
        if (count == len(jobs) and job is not None and job.get("status") == "completed"
                and job.get("conclusion") in {"success", "skipped"}):
            require(collection(response, "jobs") == jobs, "incomplete_collection")
            return job
        if read < reads:
            sleep(JOB_READ_DELAYS[read - 1])
    if count != len(jobs) or job is None:
        reason = "source_job_mismatch"
    elif job.get("status") != "completed" or job.get("conclusion") is None:
        reason = "source_merge_not_final"
    else:
        reason = "source_merge_unsuccessful"
    names = ", ".join(sorted({observed_name(item.get("name")) for item in jobs})) or "none"
    log(f"Source merge job refused after {reads} reads: {reason}; final set total_count {count}, "
        f"{len(jobs)} job(s) listed, {len(matches)} {MERGE_JOB} match(es), names [{names}]")
    raise Refusal(reason)


def verify(run_id, attempt, authority, api=read_api, run_git=git, *, version_of,
           sleep=None, log=None):
    """Verify the handoff; ``version_of(ref, run_git)`` supplies the locked
    YAML version reader from ``publication_handoff_verify.py``, so this module
    and every write job that imports it stay standard-library only."""
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
    job = source_merge_job(route, run_id, attempt, run.get("head_sha"), api,
                           sleep or time.sleep, log or log_stderr)
    if job.get("conclusion") == "skipped":
        return {"release_action": "none"}
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
    merged_pr(value["pr_number"], base, head, merge, api, run_git)
    ready_before_merge(timeline(value["pr_number"], api), merge, value["ready_event_id"])
    guard(merge, authority, run_git)
    current = api(route)
    require(all(current.get(key) == run.get(key) for key in (
        "id", "workflow_id", "path", "head_sha", "run_attempt", "event", "status", "conclusion",
        "actor", "triggering_actor", "repository", "head_repository")), "source_run_changed")
    require(not run_git("ls-tree", "--name-only", base, "--", ".release/next-version"),
            "unmaterialized_release")
    previous_version, current_version = version_of(base, run_git), version_of(merge, run_git)
    if previous_version == current_version:
        # Authenticated maintenance, including policy edits, has no publication
        # authority to transfer. Never emit a binding on this no-op path.
        return {"release_action": "none"}
    # A real release still requires the exact reviewed producer policy. This is
    # never waived because the new policy appears equivalent or was owner-merged.
    for path in POLICY_PATHS:
        require(run_git("rev-parse", f"{base}:{path}") == run_git("rev-parse", f"{authority}:{path}"),
                "producer_policy_changed")
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
    parser.add_argument("operation", choices=("produce", "guard"))
    parser.add_argument("--ready", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--authority")
    parser.add_argument("--release")
    args = parser.parse_args()
    try:
        if args.operation == "produce":
            require(args.ready is not None and args.output is not None, "missing_receipt_path")
            ready = json.loads(args.ready.read_text(), object_pairs_hook=unique_keys)
            result = produce(os.environ, ready)
            args.output.write_text(json.dumps(result, sort_keys=True) + "\n")
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
