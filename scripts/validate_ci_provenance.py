"""Read-only, protected-base verification of the required PR CI producer.

GitHub Actions app identity alone does not identify a workflow. Resolve the
check suite through the fixed CI workflow, then bind the current attempt's job
to the exact check. Never execute candidate code or follow returned URLs.
"""

import argparse
import email.utils
import json
import random
import re
import subprocess
import sys
import tempfile
import time


REPOSITORY = "jeter-1/hass-mcp-admin"
WORKFLOW_PATH = ".github/workflows/ci.yml"
APP_ID = 15368
MAX_RESPONSE_BYTES = 4_000_000
PENDING = {"queued", "in_progress", "requested", "waiting", "pending"}


# Verbatim copy of the shared bounded GitHub read (HAMCP-221);
# tests.test_signing_workflow_supply_chain pins it to its source.
# BEGIN COPY scripts/github_retry.py
MAX_ATTEMPTS = 5
MAX_TOTAL_SECONDS = 180.0
BASE_DELAY_SECONDS = 2.0
MAX_DELAY_SECONDS = 30.0
RETRY_AFTER_CAP_SECONDS = 60.0
GH_ATTEMPT_TIMEOUT_SECONDS = 30.0
MIN_ATTEMPT_SECONDS = 1.0
HEADER_BOUND = 65536
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
        # Honor the server's request, but never wait longer than the cap.
        return min(RETRY_AFTER_CAP_SECONDS, retry_after) + jitter
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


def gh_read_attempt(route, max_bytes, timeout, run=None):
    # The route is repository-relative and GET-only; gh authenticates with
    # GH_TOKEN. Neither gh diagnostics nor response text is exported.
    with tempfile.TemporaryFile() as output:
        try:
            result = (run or subprocess.run)(
                ["gh", "api", route, "--include", "--hostname", "github.com",
                 "--method", "GET", "-H", "Accept: application/vnd.github+json"],
                stdout=output, stderr=subprocess.DEVNULL,
                timeout=min(GH_ATTEMPT_TIMEOUT_SECONDS, timeout), check=False,
            )
        except subprocess.TimeoutExpired:
            raise RetryableRead("timeout") from None
        except OSError:
            raise FinalRead("gh_unavailable") from None
        output.seek(0)
        raw = output.read(HEADER_BOUND + max_bytes + 1)
    if not raw:
        # gh prints no response when none arrived; exit 4 means no credentials.
        if result.returncode == 4:
            raise FinalRead("authentication_required")
        raise RetryableRead("network")
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


def github_get(route):
    # All callers construct repository-relative GET routes from validated IDs.
    # Only 429, 5xx and transient network failures are retried, within the
    # shared bound; the failure names the host and last status, never gh
    # diagnostics, the token or arbitrary API text.
    try:
        raw = bounded_gh_read(route, MAX_RESPONSE_BYTES)
    except GitHubReadError as exc:
        print(exc, file=sys.stderr)
        raise Refusal(exc.category) from None
    require(len(raw) <= MAX_RESPONSE_BYTES, "github_response_too_large")
    try:
        value = json.loads(raw, object_pairs_hook=unique_keys)
    except (ValueError, UnicodeError) as exc:
        raise Refusal("github_response_malformed") from exc
    require(isinstance(value, dict), "github_response_malformed")
    return value


def collection(value, key):
    require(isinstance(value, dict), "malformed_collection")
    items = value.get(key)
    count = value.get("total_count")
    require(type(count) is int and isinstance(items, list), "malformed_collection")
    require(count == len(items) and 0 <= count <= 100, "incomplete_collection")
    require(all(isinstance(item, dict) for item in items), "malformed_collection")
    return items


def pr_binding(value, pr_number, base, head):
    associations = value.get("pull_requests")
    require(isinstance(associations, list) and len(associations) == 1,
            "ambiguous_pr_binding")
    pr = associations[0]
    require(isinstance(pr, dict) and pr.get("number") == pr_number,
            "wrong_pr_binding")
    require(pr.get("base", {}).get("ref") == "main"
            and pr.get("base", {}).get("sha") == base
            and pr.get("head", {}).get("sha") == head, "stale_pr_binding")
    for side in ("base", "head"):
        require(pr[side].get("repo", {}).get("url") ==
                f"https://api.github.com/repos/{REPOSITORY}", "foreign_pr_repository")


def successful(value):
    status, conclusion = value.get("status"), value.get("conclusion")
    if status == "completed":
        require(conclusion == "success", "unsuccessful_ci")
        return True
    require(status in PENDING and conclusion is None, "invalid_ci_status")
    return False


def verify(pr_number, base, head, api=github_get):
    positive_id(pr_number)
    require(all(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha)
                for sha in (base, head)), "invalid_commit")
    prefix = f"repos/{REPOSITORY}"
    checks_route = (f"{prefix}/commits/{head}/check-runs"
                    "?check_name=validate&filter=latest&per_page=100")
    checks = collection(api(checks_route), "check_runs")
    if not checks:
        return {"status": "pending"}
    require(len(checks) == 1, "ambiguous_validate_check")
    check = checks[0]
    require(check.get("name") == "validate" and check.get("head_sha") == head,
            "wrong_validate_check")
    require(check.get("app", {}).get("id") == APP_ID
            and check.get("app", {}).get("slug") == "github-actions",
            "wrong_check_producer")
    pr_binding(check, pr_number, base, head)
    check_id = positive_id(check.get("id"))
    suite_id = positive_id(check.get("check_suite", {}).get("id"))
    if not successful(check):
        return {"status": "pending"}

    workflow = api(f"{prefix}/actions/workflows/ci.yml")
    workflow_id = positive_id(workflow.get("id"))
    require(workflow.get("path") == WORKFLOW_PATH
            and workflow.get("state") == "active", "wrong_ci_workflow")
    runs = collection(api(
        f"{prefix}/actions/workflows/{workflow_id}/runs"
        f"?check_suite_id={suite_id}&head_sha={head}&event=pull_request&per_page=100"
    ), "workflow_runs")
    require(len(runs) == 1, "missing_or_ambiguous_ci_run")
    run = runs[0]
    run_id = positive_id(run.get("id"))
    attempt = positive_id(run.get("run_attempt"))
    require(run.get("workflow_id") == workflow_id
            and run.get("path") == WORKFLOW_PATH
            and run.get("check_suite_id") == suite_id
            and run.get("event") == "pull_request"
            and run.get("head_sha") == head, "wrong_ci_run")
    require(all(run.get(key, {}).get("full_name") == REPOSITORY
                for key in ("repository", "head_repository")), "foreign_ci_run")
    pr_binding(run, pr_number, base, head)
    run_success = successful(run)
    jobs = collection(api(
        f"{prefix}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
    ), "jobs")
    matches = [job for job in jobs if job.get("name") == "validate"]
    require(len(matches) == 1, "missing_or_ambiguous_ci_job")
    job = matches[0]
    job_id = positive_id(job.get("id"))
    require(job.get("run_id") == run_id and job.get("run_attempt") == attempt
            and job.get("head_sha") == head
            and job.get("check_run_url") ==
            f"https://api.github.com/repos/{REPOSITORY}/check-runs/{check_id}",
            "wrong_ci_job")
    job_success = successful(job)
    # Refuse a rerun or replacement check racing the collected metadata.
    current = api(f"{prefix}/actions/runs/{run_id}")
    require(all(current.get(key) == run.get(key) for key in (
        "id", "workflow_id", "path", "check_suite_id", "event", "head_sha",
        "run_attempt", "pull_requests", "repository", "head_repository",
    )), "ci_run_changed")
    current_success = successful(current)
    latest = collection(api(checks_route), "check_runs")
    require(len(latest) == 1 and all(latest[0].get(key) == check.get(key) for key in (
        "id", "name", "head_sha", "app", "check_suite", "pull_requests",
        "status", "conclusion",
    )), "validate_check_changed")
    return {
        "status": "success" if run_success and job_success and current_success else "pending",
        "app_id": APP_ID, "workflow_id": workflow_id,
        "run_id": run_id, "run_attempt": attempt, "job_id": job_id,
        "check_run_id": check_id, "check_suite_id": suite_id,
        "pr_number": pr_number, "base_sha": base, "head_sha": head,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--require-success", action="store_true")
    args = parser.parse_args()
    try:
        require(args.repository == REPOSITORY, "wrong_repository")
        result = verify(args.pr_number, args.base_sha, args.head_sha)
        require(not args.require_success or result["status"] == "success", "ci_not_complete")
    except (Refusal, KeyError, TypeError, AttributeError) as exc:
        category = str(exc) if isinstance(exc, Refusal) else "malformed_evidence"
        print(json.dumps({"status": "refused", "category": category}))
        return 1
    except (EOFError, KeyboardInterrupt):
        print(json.dumps({"status": "refused", "category": "interrupted"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
