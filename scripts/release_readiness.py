"""Read-only release readiness sweep (HAMCP-458; release plan F10, step 0).

One command reports the sweep's state from results that already exist. It
starts nothing: no workflow dispatch, rerun, comment or label, and no Home
Assistant or MCP access. Three parts are checked:

- support window: ``ci/support-window.yaml`` loads through
  ``scripts/support_window.py``; its current Core and ha-mcp pair is the pair the
  sweep is about;
- contract matrix: the newest ``ci.yml`` run of this repository (not a fork)
  for the exact head commit completed successfully, and in its latest attempt
  ``validate`` and every full-tier ``Real HA`` lane of the window succeeded;
- Core evidence: the newest ``core-release-evidence.yml`` pull-request run of
  this repository for the exact head (or the run ``--core-run`` names)
  completed successfully, and in its latest attempt every lane-group job and
  the lanes job of each of its cases proved the window's current Core:
  ``coverage`` and ``fixture`` for a pull request, or ``release`` and its
  selected-release job for a scheduled or dispatched run. Scheduled runs only
  try Core releases newer than the window, so they rarely prove its Core.

The other sweep parts need their owners; they are listed, never reported as
ready. A part that cannot be read, or whose result is missing, is not ready.
Exit 0 means every checked part is ready, 1 that one is not, 2 a usage
refusal. Every GitHub read is one bounded GET through
``github_retry.api_get``; only fixed categories, validated ids and this
script's own expected job names are reported, never response text.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import core_release_evidence  # noqa: E402  (the lane-group names; never copied)
import support_window  # noqa: E402  (the window and its Real HA lanes; never copied)

_spec = importlib.util.spec_from_file_location("hamcp458_github_retry", SCRIPTS / "github_retry.py")
retry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(retry)

SCHEMA_VERSION = 1
DEFAULT_REPOSITORY = "jeter-1/hass-mcp-admin"
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}")
SHA = re.compile(r"[0-9a-f]{40}")
RUN_ID = re.compile(r"[1-9][0-9]{0,19}")
CI_WORKFLOW = ".github/workflows/ci.yml"
CORE_WORKFLOW = ".github/workflows/core-release-evidence.yml"
EVENTS = frozenset({"pull_request", "schedule", "workflow_dispatch"})
# The cases core_evidence_workflow.py prepares for each event (matrix.case).
CORE_CASES = {"pull_request": ("coverage", "fixture"), "schedule": ("release",), "workflow_dispatch": ("release",)}
STATUSES = frozenset({"queued", "in_progress", "completed", "waiting", "requested", "pending"})
CONCLUSIONS = frozenset({"success", "failure", "cancelled", "skipped", "timed_out", "action_required",
                         "neutral", "stale", "startup_failure"})
JOB_PAGES = 10
PAGE = 100
READY, NOT_READY = "ready", "not ready"
# Sweep parts this command cannot check from recorded results, with who supplies them.
MANUAL = (
    {"part": "Upstream-read audit", "owner": "the builder of each read-tool change",
     "needed": "the HAMCP-442 method, re-run on every tool the release changes"},
    {"part": "Output-expanding parameters (F5)", "owner": "the builder of each read-tool change",
     "needed": "every expanding parameter of each changed read tool, enumerated from source, each with a captured call"},
    {"part": "Exact-version gates (F2)", "owner": "the release orchestrator",
     "needed": "a runtime sweep for version literals outside the support window, until the version-gate lint (HAMCP-456) is in CI"},
    {"part": "Live re-checks", "owner": "Josh, relayed from HA Admin",
     "needed": "the live checks the release plan lists; this command never contacts Home Assistant"},
)


class Unreadable(Exception):
    """A GitHub result that could not be read; ``str`` is a fixed category."""


def github_read(route):
    return retry.api_get(route)


def read_json(read, route):
    """One bounded GET, parsed strictly; a failure is a fixed category, never response text."""

    try:
        body = read(route)
    except retry.GitHubReadError as exc:
        raise Unreadable(f"{exc.category}:{exc.status}") from None
    except (ValueError, OSError) as exc:
        detail = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[a-z0-9_]+", str(exc)) \
            else type(exc).__name__
        raise Unreadable(f"github_read_refused:{detail}") from None
    if not isinstance(body, bytes):
        raise Unreadable("github_response_malformed")
    if len(body) > retry.API_BOUND:
        raise Unreadable("github_response_too_large")

    def unique(pairs):
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError("duplicate key")
        return dict(pairs)

    def finite(_constant):
        raise ValueError("non-finite number")

    try:
        return json.loads(body.decode("utf-8"), object_pairs_hook=unique, parse_constant=finite)
    except (ValueError, RecursionError):
        raise Unreadable("github_response_malformed") from None


def listed(value, key):
    items = value.get(key) if isinstance(value, dict) else None
    if not isinstance(items, list) or len(items) > PAGE or not all(isinstance(item, dict) for item in items):
        raise Unreadable("github_response_malformed")
    return items


def own_run(run, repository, workflow, events):
    """A run of this repository's workflow (not a fork) for an accepted event."""

    if type(run.get("id")) is not int or run["id"] < 1:
        return False
    for key in ("repository", "head_repository"):
        owner = run.get(key)
        if not (isinstance(owner, dict) and isinstance(owner.get("full_name"), str)
                and owner["full_name"].lower() == repository.lower()):
            return False
    return run.get("path") == workflow and run.get("event") in events


def state(value, allowed):
    return value if isinstance(value, str) and value in allowed else "unrecognized"


def run_label(run):
    attempt = run.get("run_attempt")
    attempt = attempt if type(attempt) is int and attempt >= 1 else "unknown"
    return f"run {run['id']} attempt {attempt} ({run['event']})"


def run_complete(run):
    """None for a completed, successful run; otherwise why it is not ready."""

    status = state(run.get("status"), STATUSES)
    if status != "completed":
        return f"{run_label(run)} is {status}"
    conclusion = state(run.get("conclusion"), CONCLUSIONS)
    if conclusion != "success":
        return f"{run_label(run)} concluded {conclusion}"
    return None


def latest_jobs(read, repository, run_id):
    """Each job name of the run's latest attempt with its conclusions."""

    jobs = {}
    for page in range(1, JOB_PAGES + 1):
        batch = listed(read_json(read, f"repos/{repository}/actions/runs/{run_id}/jobs"
                                       f"?filter=latest&per_page={PAGE}&page={page}"), "jobs")
        for job in batch:
            name = job.get("name")
            if not isinstance(name, str):
                raise Unreadable("github_response_malformed")
            jobs.setdefault(name, []).append(state(job.get("conclusion"), CONCLUSIONS))
        if len(batch) < PAGE:
            return jobs
    raise Unreadable("github_jobs_bound")


def job_gaps(jobs, expected):
    """The expected jobs that are absent or did not all succeed, by name."""

    gaps = []
    for name in expected:
        conclusions = jobs.get(name)
        if not conclusions:
            gaps.append(f"{name}: absent")
        elif any(conclusion != "success" for conclusion in conclusions):
            gaps.append(f"{name}: " + ", ".join(sorted(set(conclusions))))
    return gaps


def part(name, status, detail):
    return {"part": name, "status": status, "detail": detail}


def full_tier_lanes(window):
    """The ``Real HA`` job names of the window's full tier, as ci.yml names them."""

    return [f"Real HA {row['lane']} ({row['support_slot']})"
            for row in support_window.real_ha_matrix(window, "full")["include"]]


def newest_run(read, repository, workflow, head, events):
    """The newest run of this repository's workflow for the exact head, or None."""

    runs = listed(read_json(read, f"repos/{repository}/actions/workflows/{Path(workflow).name}/runs"
                                  f"?head_sha={head}&per_page={PAGE}"), "workflow_runs")
    own = [run for run in runs if own_run(run, repository, workflow, events) and run.get("head_sha") == head]
    return max(own, key=lambda item: item["id"]) if own else None


def contract_matrix(read, repository, head, lanes):
    name = "Contract matrix"
    run = newest_run(read, repository, CI_WORKFLOW, head, EVENTS)
    if run is None:
        return part(name, NOT_READY, "no ci.yml run of this repository for the head")
    problem = run_complete(run)
    if problem:
        return part(name, NOT_READY, problem)
    gaps = job_gaps(latest_jobs(read, repository, run["id"]), ["validate", *lanes])
    if gaps:
        return part(name, NOT_READY, f"{run_label(run)}: " + "; ".join(gaps))
    return part(name, READY, f"{run_label(run)}: validate and {', '.join(lanes)} succeeded")


def lane_groups():
    """The lane groups core-release-evidence.yml proves in their own jobs, in groups.json order."""

    return [group for group in core_release_evidence.groups() if group in core_release_evidence.LANE_GROUPS]


def core_jobs(version, event, groups):
    """The jobs core-release-evidence.yml names when a run of ``event`` proves Core ``version``."""

    names = [] if event == "pull_request" else [f"core-release-attempt-{version}"]
    for case in CORE_CASES[event]:
        names += [f"Core evidence group {case} {group}" for group in groups] + [f"Core evidence {case} {version}"]
    return names


def core_evidence(read, repository, head, version, groups, core_run=None):
    name = "Core evidence"
    if core_run is None:
        run = newest_run(read, repository, CORE_WORKFLOW, head, {"pull_request"})
        if run is None:
            return part(name, NOT_READY, "no core-release-evidence.yml pull-request run of this repository for "
                                         "the head; pass --core-run")
    else:
        run = read_json(read, f"repos/{repository}/actions/runs/{core_run}")
        if not (isinstance(run, dict) and run.get("id") == int(core_run)
                and own_run(run, repository, CORE_WORKFLOW, EVENTS)):
            return part(name, NOT_READY, f"run {core_run} is not a core-release-evidence.yml run of this repository")
    label = run_label(run) + ("" if run.get("head_sha") == head else ", not the head")
    problem = run_complete(run)
    if problem:
        return part(name, NOT_READY, problem.replace(run_label(run), label, 1))
    jobs = latest_jobs(read, repository, run["id"])
    expected = core_jobs(version, run["event"], groups)
    if run["event"] != "pull_request" and expected[0] not in jobs:
        return part(name, NOT_READY, f"{label}: did not select Core {version}")
    gaps = job_gaps(jobs, expected)
    if gaps:
        return part(name, NOT_READY, f"{label}: " + "; ".join(gaps))
    cases = " and ".join(CORE_CASES[run["event"]])
    return part(name, READY, f"{label}: every lane-group and lanes job of {cases} proved Core {version}")


def guarded(name, check, *args):
    try:
        return check(*args)
    except Unreadable as exc:
        return part(name, NOT_READY, f"unreadable: {exc}")


def sweep(repository, head, read=github_read, core_run=None, load_window=support_window.load):
    # The checkout's own declarations come first; without them nothing is read.
    try:
        window = load_window()
        lanes = full_tier_lanes(window)
        core, ha_mcp = window["core"]["current"]["version"], window["ha_mcp"]["current"]["version"]
        groups = lane_groups()
    except (ValueError, OSError, support_window.yaml.YAMLError) as exc:
        category = str(exc) if isinstance(exc, support_window.Refusal) else type(exc).__name__
        reason = f"support window or Core evidence groups refused: {category}"
        parts = [part("Support window", NOT_READY, reason),
                 part("Contract matrix", NOT_READY, "not checked: " + reason),
                 part("Core evidence", NOT_READY, "not checked: " + reason)]
        pair = None
    else:
        pair = {"core": core, "ha_mcp": ha_mcp}
        parts = [part("Support window", READY, f"current pair Core {core} with ha-mcp {ha_mcp}"),
                 guarded("Contract matrix", contract_matrix, read, repository, head, lanes),
                 guarded("Core evidence", core_evidence, read, repository, head, core, groups, core_run)]
    return {"schema_version": SCHEMA_VERSION, "repository": repository, "head": head, "pair": pair,
            "ready": all(item["status"] == READY for item in parts), "parts": parts, "manual": list(MANUAL)}


def markdown(report):
    ready = sum(item["status"] == READY for item in report["parts"])
    lines = ["# Release readiness sweep", "",
             f"Repository {report['repository']}, head {report['head']}.", "",
             "| Part | Status | Detail |", "| --- | --- | --- |"]
    lines += [f"| {item['part']} | {item['status']} | {item['detail']} |" for item in report["parts"]]
    lines += ["", "Not checked by this command (each needs its owner):", ""]
    lines += [f"- {item['part']} ({item['owner']}): {item['needed']}." for item in report["manual"]]
    verdict = "Every checked part is ready." if report["ready"] else "Not ready."
    lines += ["", f"Checked parts ready: {ready} of {len(report['parts'])}. {verdict}"]
    return "\n".join(lines) + "\n"


def main(argv=None, read=github_read, stdout=None, stderr=None):
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=DEFAULT_REPOSITORY, help="owner/name (default %(default)s)")
    parser.add_argument("--head", required=True, help="the exact 40-hex commit the release would ship")
    parser.add_argument("--core-run", help="a core-release-evidence.yml run id instead of the head's newest pull-request run")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args(argv)
    for value, pattern, category in ((args.repo, REPOSITORY, "repository_invalid"), (args.head, SHA, "head_invalid"),
                                     (args.core_run, RUN_ID, "core_run_invalid")):
        if value is not None and not pattern.fullmatch(value):
            print(f"Release readiness refused: {category}", file=stderr)
            return 2
    report = sweep(args.repo, args.head, read, args.core_run)
    stdout.write(json.dumps(report, indent=2) + "\n" if args.format == "json" else markdown(report))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
