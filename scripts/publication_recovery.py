"""Read-only recovery advisor for a failed Engineering publication (HAMCP-223).

Given a publish run ID and/or a version, it inspects the release commit on
``main``'s first-parent history, Engineering-tree drift, staged declarations,
the create-only claim ref, the Git tag, the GitHub Release, both registry tags
and live publish runs. It then prints exactly one verdict:

* ``published``: nothing to recover;
* ``wait``: a publish run for the release or for ``main`` is still live;
* ``dispatch_fresh`` / ``dispatch_digest``: the exact owner
  ``workflow_dispatch`` command, with every input validated against the
  publisher's own input contract;
* ``needs_digest``: digest recovery applies, but the digest and build time
  must be read from the failed run first;
* ``roll_forward``: the only recovery is a new release N+1;
* ``reconcile`` / ``stop``: an owner decision or a fresh inspection is needed.

It never dispatches, reruns, tags, pushes or writes anything. GitHub reads use
``GH_TOKEN`` only when set, and registry reads are anonymous.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import sys


_spec = importlib.util.spec_from_file_location(
    "hamcp223_watchdog", Path(__file__).resolve().parent / "advertised_image_watchdog.py")
watchdog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watchdog)
retry = watchdog.retry

REPOSITORY = watchdog.REPOSITORY
OWNER = "jeter-1"
WORKFLOW_FILE = "publish-rc-image.yml"
WORKFLOW_PATH = ".github/workflows/" + WORKFLOW_FILE
ENGINEERING_TREE = "hass_mcp_engineering_beta"
CONFIG_PATH = ENGINEERING_TREE + "/config.yaml"
STAGED_DECLARATION = ".release/next-version"
CLAIM_PREFIX = "engineering-publication-attempt/v"
BUILD_JOB = "Build release image by digest without final tags"
RELEASE_WALK_LIMIT = 1000
# The publisher's detect-release input contract, verbatim, in dispatch order.
INPUT_PATTERNS = {
    "release_sha": r"[0-9a-f]{40}",
    "expected_version": r"[0-9A-Za-z][0-9A-Za-z.+-]{0,63}",
    "recovery_run_id": r"[1-9][0-9]{0,19}",
    "recovery_run_attempt": r"([1-9][0-9]?|100)",
    "recovery_source_digest": r"sha256:[0-9a-f]{64}",
    "recovery_build_time": r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
}
RECOVERY_FIELDS = ("recovery_run_id", "recovery_source_digest", "recovery_build_time")
FAILURE_MODES = {
    "published": "none", "wait": "1/5 (a run is live)",
    "dispatch_fresh": "4/5 (no claim, no artifacts)",
    "dispatch_digest": "6 (claimed run produced a digest)",
    "needs_digest": "6 (claimed run produced a digest)",
    "roll_forward": "3/6/8 (drift or consumed claim without a recoverable digest)",
    "reconcile": "7 (partial, unknown or inconsistent publication state)",
    "stop": "inspection incomplete",
}


class RecoveryError(Exception):
    """A fixed category; never arbitrary remote text."""


def dispatch_command(inputs, repository=REPOSITORY):
    """The exact owner dispatch command, or RecoveryError for any invalid input."""
    if set(inputs) - set(INPUT_PATTERNS) or not {"release_sha", "expected_version"} <= set(inputs):
        raise RecoveryError("dispatch_inputs_incomplete")
    recovery = [field for field in RECOVERY_FIELDS if field in inputs]
    if recovery and len(recovery) != len(RECOVERY_FIELDS):
        raise RecoveryError("recovery_fields_must_be_supplied_together")
    if "recovery_run_attempt" in inputs and not recovery:
        raise RecoveryError("recovery_attempt_requires_recovery_fields")
    for field, value in inputs.items():
        if not isinstance(value, str) or not re.fullmatch(INPUT_PATTERNS[field], value):
            raise RecoveryError(f"invalid_{field}")
    if "recovery_build_time" in inputs:
        try:
            datetime.strptime(inputs["recovery_build_time"], "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            raise RecoveryError("invalid_recovery_build_time") from None
    if not watchdog.REPOSITORY_PATTERN.fullmatch(repository):
        raise RecoveryError("invalid_repository")
    # Every value matches a shell-safe closed alphabet, so no quoting is needed.
    return (f"gh workflow run {WORKFLOW_FILE} --repo {repository} --ref main "
            + " ".join(f"-f {field}={inputs[field]}" for field in INPUT_PATTERNS if field in inputs))


# --- Git facts ---------------------------------------------------------------

def present(root, commit, path):
    """Whether ``path`` exists at ``commit``, as the publisher's ``git cat-file -e`` reads it.

    Git proves absence; a read failure raises and is never "absent".
    """
    return watchdog.tree_entry(root, commit, path) is not None


def blob(root, commit, path):
    """The file at ``path``, or None only when Git proves the path absent.

    Read failures (``git_unavailable``, ``git_output_bound``, ...) and
    non-regular entries raise, so they stop the inspection with exit 2.
    """
    entry = watchdog.tree_entry(root, commit, path)
    if entry is None:
        return None
    mode, kind, oid = entry
    if kind != "blob" or mode not in watchdog.REGULAR_MODES:
        raise watchdog.WatchdogError("config_not_regular_file")
    return watchdog.git(root, "cat-file", "blob", oid)


def config_at(root, commit):
    raw = blob(root, commit, CONFIG_PATH)
    try:
        config = watchdog.parse_config(raw, CONFIG_PATH) if raw is not None else None
    except watchdog.ConfigInvalid:
        return None  # Store-invalid only; an over-bound or unprocessable config propagates and stops.
    return config if isinstance(config, dict) else None


def version_at(root, commit):
    config = config_at(root, commit)
    return str(config["version"]) if config and "version" in config else None


def parent(root, commit):
    line = watchdog.git(root, "rev-list", "--parents", "-n", "1", commit).decode().split()
    return line[1] if len(line) > 1 else None


def release_commits(root, main_sha, version, limit=RELEASE_WALK_LIMIT):
    """First-parent commits on main where the advertised version became ``version``."""
    changed = watchdog.git(root, "rev-list", "--first-parent", f"--max-count={limit + 1}",
                           main_sha, "--", CONFIG_PATH).decode("ascii", "replace").split()
    if len(changed) > limit:
        # A cut walk could hide another transition: never name a release from it.
        raise RecoveryError("release_walk_truncated")
    return [commit for commit in changed if version_at(root, commit) == version
            and (parent(root, commit) is None or version_at(root, parent(root, commit)) != version)]


def tree_drift(root, release, main_sha):
    """Status 1 proves drift and 0 proves none; any other failure raises."""
    status, _ = watchdog.git_run(root, "diff", "--quiet", release, main_sha, "--", ENGINEERING_TREE,
                                 statuses=(0, 1))
    return status == 1


# --- Remote facts --------------------------------------------------------------

# Only an allowed HTTP 404 produces ABSENT; no decoded JSON value can equal it.
ABSENT = object()
UNKNOWN = "unknown"
# Each object endpoint's keys the helper reads, with their exact types. A nested
# dict is a nested object; element and value checks stay with each caller.
MAIN_REF = {"object": {"sha": (str,)}}
TAG_REF = {"object": {"type": (str,), "sha": (str,)}}
GIT_TAG = {"message": (str,)}
RELEASE = {"tag_name": (str,)}
RUN = {"id": (int,), "run_attempt": (int,), "event": (str,), "status": (str,), "conclusion": (str, type(None)),
       "head_sha": (str,), "path": (str,), "triggering_actor": {"login": (str,)}}
JOBS = {"total_count": (int,), "jobs": (list,)}


def shaped(document, schema):
    """True only for an object carrying every schema key with an exact schema type."""
    if not isinstance(document, dict):
        return False
    for key, expected in schema.items():
        if key not in document:
            return False
        value = document[key]
        if not (shaped(value, expected) if isinstance(expected, dict) else type(value) in expected):
            return False
    return True


class GitHub:
    def __init__(self, repository, reader, token=None):
        self.base = f"https://{watchdog.API}/repos/{repository}"
        self.reader = reader
        self.token = token
        self.headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            self.headers["Authorization"] = "Bearer " + token

    def read(self, route, label, schema, absent_ok=False):
        """A schema-valid object, ABSENT for an allowed 404, or UNKNOWN for any other read failure.

        A 2xx body that is not such an object (null, a list, a scalar, a
        missing or wrong-typed key) is never absence: it raises, so the CLI
        exits 2 with a fixed category and prints no command.
        """
        try:
            document = self.reader.json(self.base + route, self.headers)
        except retry.GitHubReadError as exc:
            return ABSENT if absent_ok and exc.status == 404 else UNKNOWN
        if not shaped(document, schema):
            raise RecoveryError(f"github_response_malformed:{label}")
        return document

    def presence(self, route, label, schema):
        found = self.read(route, label, schema, absent_ok=True)
        return UNKNOWN if found is UNKNOWN else ("absent" if found is ABSENT else "present")


def run_summary(run):
    """A run's fields, or "unknown" when any of them is unreadable or unrecognized."""
    if not isinstance(run, dict) or type(run.get("id")) is not int \
            or not isinstance(run.get("triggering_actor"), dict):
        return "unknown"
    summary = {"id": run["id"], "attempt": run.get("run_attempt"), "event": watchdog.text(run.get("event")),
               "status": watchdog.text(run.get("status")),
               "conclusion": watchdog.text(run.get("conclusion") or "none"),
               "head_sha": watchdog.text(run.get("head_sha")), "path": watchdog.text(run.get("path")),
               "actor": watchdog.text(run["triggering_actor"].get("login"))}
    if "invalid" in summary.values() or summary["status"] not in watchdog.RUN_STATUSES \
            or not watchdog.SHA_PATTERN.fullmatch(summary["head_sha"]):
        return "unknown"
    return summary


def read_claim(api, version):
    ref = api.read(f"/git/ref/tags/{CLAIM_PREFIX}{version}", "claim_ref", TAG_REF, absent_ok=True)
    if ref is ABSENT:
        return None
    if ref is UNKNOWN:
        return "unknown"
    target = ref["object"]
    if target["type"] != "tag" or not watchdog.SHA_PATTERN.fullmatch(target["sha"]):
        return "unknown"
    tag = api.read(f"/git/tags/{target['sha']}", "claim_tag", GIT_TAG)
    try:
        claim = json.loads(tag["message"]) if isinstance(tag, dict) and len(tag.get("message", "")) <= 8192 else None
    except (ValueError, TypeError):
        claim = None
    return claim if isinstance(claim, dict) else "unknown"


def build_conclusion(api, run_id, attempt):
    jobs = api.read(f"/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100", "claim_jobs", JOBS)
    listing = jobs.get("jobs") if isinstance(jobs, dict) else None
    # Only a complete, well-formed listing proves the build job never started.
    if not isinstance(listing, list) or type(jobs.get("total_count")) is not int \
            or jobs["total_count"] != len(listing) or not all(isinstance(job, dict) for job in listing):
        return "unknown"
    found = [job for job in listing if job.get("name") == BUILD_JOB]
    if len(found) > 1:
        return "unknown"
    if not found:
        return "not_started"
    conclusion = watchdog.text(found[0].get("conclusion") or "none")
    return "unknown" if conclusion == "invalid" else conclusion


def registry_presence(reader, image_path, reference, arch):
    state, missing, reason = watchdog.registry_state(reader, image_path, reference, arch)
    if state == "complete":
        return "present"
    if state == "incomplete" and reason == "image_tag_absent":
        return "absent"
    return "partial" if state == "incomplete" else "unknown"


def inspect(root, repository=REPOSITORY, reader=None, token=None, run_id=None, version=None,
            main_ref="main", digest=None):
    """Collect every fact the verdict needs; raises nothing for remote ambiguity."""
    if not watchdog.REPOSITORY_PATTERN.fullmatch(repository):
        raise RecoveryError("invalid_repository")
    reader = reader or watchdog.Reader()
    api = GitHub(repository, reader, token)
    facts = {"repository": repository}
    try:
        facts["main_sha"] = main_sha = watchdog.resolve_ref(root, main_ref)
    except watchdog.WatchdogError as exc:
        raise RecoveryError(str(exc)) from None
    remote = api.read("/git/ref/heads/main", "main_ref", MAIN_REF)
    facts["remote_main_sha"] = UNKNOWN if remote is UNKNOWN else watchdog.text(remote["object"]["sha"])
    if run_id is not None:
        if not re.fullmatch(INPUT_PATTERNS["recovery_run_id"], str(run_id)):
            raise RecoveryError("invalid_run_id")
        facts["run"] = run = run_summary(api.read(f"/actions/runs/{run_id}", "run", RUN))
        if run != "unknown":
            # The publisher refuses Engineering drift, so the run's own checkout
            # advertises the version it published or recovered. An unreadable
            # checkout stops: it never silently skips the cross-check.
            try:
                head = watchdog.resolve_ref(root, run["head_sha"])
            except watchdog.WatchdogError as exc:
                raise RecoveryError(f"run_head_{exc}") from None
            derived = version_at(root, head)
            if version is not None and derived is not None and derived != version:
                raise RecoveryError("run_version_differs_from_requested_version")
            version = version or derived
    if version is None or not re.fullmatch(INPUT_PATTERNS["expected_version"], version):
        raise RecoveryError("version_unknown_or_invalid")
    facts["version"] = version
    candidates = release_commits(root, main_sha, version)
    facts["release_candidates"] = candidates
    if len(candidates) != 1:
        return facts
    facts["release_sha"] = release = candidates[0]
    config = config_at(root, release) or {}
    facts["main_version"] = version_at(root, main_sha)
    facts["engineering_drift"] = tree_drift(root, release, main_sha)
    facts["staged"] = any(present(root, commit, STAGED_DECLARATION) for commit in (release, main_sha))
    image = config.get("image")
    match = watchdog.IMAGE_PATTERN.fullmatch(image) if isinstance(image, str) else None
    arch = sorted(set(config.get("arch") or [])) if isinstance(config.get("arch"), list) else []
    if not match or not arch or any(a not in watchdog.PLATFORMS for a in arch):
        facts["registry_version"] = facts["registry_commit"] = "unknown"
    else:
        facts["registry_version"] = registry_presence(reader, match.group(1), version, arch)
        facts["registry_commit"] = registry_presence(reader, match.group(1), "sha-" + release, arch)
        if digest is not None and re.fullmatch(INPUT_PATTERNS["recovery_source_digest"], digest):
            facts["digest"] = registry_presence(reader, match.group(1), digest, arch)
    facts["git_tag"] = api.presence(f"/git/ref/tags/v{version}", "tag_ref", TAG_REF)
    facts["github_release"] = api.presence(f"/releases/tags/v{version}", "release", RELEASE)
    live = []
    for sha in dict.fromkeys((release, main_sha)):
        try:
            live += [run for run in watchdog.publish_runs(reader, repository, sha, token)
                     if run["status"] in watchdog.LIVE_STATUSES]
        except (retry.GitHubReadError, watchdog.WatchdogError):
            facts["live_runs"] = "unknown"
            break
    else:
        facts["live_runs"] = live
    claim = read_claim(api, version)
    facts["claim"] = claim
    if isinstance(claim, dict) and type(claim.get("run_id")) is int and type(claim.get("run_attempt")) is int \
            and 0 < claim["run_id"] and 0 < claim["run_attempt"] <= 100:
        facts["claim_run"] = run_summary(api.read(f"/actions/runs/{claim['run_id']}/attempts/{claim['run_attempt']}",
                                                  "claim_run", RUN))
        facts["claim_build"] = build_conclusion(api, claim["run_id"], claim["run_attempt"])
    return facts


# --- Verdict -------------------------------------------------------------------

def verdict(facts, digest=None, build_time=None):
    """``(verdict, reasons, command)`` from collected facts; pure and offline."""
    version, release = facts.get("version"), facts.get("release_sha")
    if facts.get("remote_main_sha") != facts.get("main_sha"):
        return "stop", ["local main differs from GitHub main, or GitHub main is unknown: "
                        "fetch origin main and run again"], None
    run = facts.get("run")
    if run is not None:
        if run == "unknown":
            return "stop", ["the named run could not be read"], None
        if run["path"] != WORKFLOW_PATH:
            return "stop", [f"run {run['id']} is not a {WORKFLOW_PATH} run"], None
    if release is None:
        return "stop", [f"version {version} has {len(facts.get('release_candidates', []))} release "
                        "transitions on main's first-parent history; exactly one is required"], None
    artifacts = {key: facts.get(key) for key in ("registry_version", "registry_commit", "git_tag", "github_release")}
    if facts.get("live_runs") == "unknown":
        return "stop", ["publish-run listing is unknown"], None
    if facts.get("live_runs"):
        ids = ", ".join(str(r["id"]) for r in facts["live_runs"])
        return "wait", [f"publish run(s) {ids} are still live; rerun this helper after they complete"], None
    if all(value == "present" for value in artifacts.values()):
        return "published", [f"{version} is fully published: both image tags, Git tag and GitHub Release exist",
                             "In Home Assistant: Settings > System > Updates > Check for updates "
                             "(bypasses the 3-hour store cache)"], None
    if "unknown" in artifacts.values():
        unknown = ", ".join(key for key, value in artifacts.items() if value == "unknown")
        return "stop", [f"artifact state is unknown ({unknown}); retry later"], None
    if facts.get("engineering_drift") or facts.get("main_version") != version:
        return "roll_forward", [
            f"{ENGINEERING_TREE}/ on main differs from release {release} (main advertises "
            f"{facts.get('main_version')}); the publisher refuses drift, so {version} cannot be published",
            "Recover by merging a new reviewed release N+1 that advertises a new version"], None
    if facts.get("staged"):
        return "stop", [f"{STAGED_DECLARATION} exists on main or at the release; the publisher refuses "
                        "staged state"], None
    present = [key for key, value in artifacts.items() if value != "absent"]
    if present:
        return "reconcile", [f"partial publication: {', '.join(present)} already exist; the publisher "
                             "stops on any existing artifact, so this needs an owner reconciliation decision"], None
    claim = facts.get("claim")
    base = {"release_sha": release, "expected_version": version}
    if claim is None:
        return "dispatch_fresh", [f"no claim and no artifacts exist for {version}; Josh may dispatch a fresh "
                                  "recovery from current main"], dispatch_command(base, facts["repository"])
    if not isinstance(claim, dict):
        return "stop", ["the claim ref exists but could not be read"], None
    if claim.get("release_sha") != release or claim.get("version") != version \
            or claim.get("schema") not in (1, 2) or (claim.get("event") == "workflow_run") != (claim.get("schema") == 2):
        return "reconcile", ["the claim does not bind this release and version under a known schema"], None
    claim_run, build = facts.get("claim_run"), facts.get("claim_build")
    if claim_run is None or claim_run == "unknown" or build in (None, "unknown"):
        return "stop", ["the claimed run or its build job could not be read"], None
    if claim_run["status"] != "completed" or claim_run["conclusion"] != "failure" \
            or claim_run["path"] != WORKFLOW_PATH or claim_run["actor"] != OWNER:
        return "roll_forward", [
            f"claim run {claim['run_id']} attempt {claim['run_attempt']} is {claim_run['status']}/"
            f"{claim_run['conclusion']} (triggered by {claim_run['actor']}); digest recovery requires a completed "
            "failed run triggered by the owner, and the consumed claim forbids a fresh build"], None
    if build != "success":
        return "roll_forward", [
            f"claim run {claim['run_id']} attempt {claim['run_attempt']} consumed the claim but its build job "
            f"concluded {build}, so no recoverable digest exists and a fresh build is forbidden"], None
    recovery = dict(base, recovery_run_id=str(claim["run_id"]), recovery_run_attempt=str(claim["run_attempt"]))
    if digest is None or build_time is None:
        return "needs_digest", [
            f"claim run {claim['run_id']} attempt {claim['run_attempt']} built an image; read its digest from "
            "the run summary line 'OCI digest:' (job 'Write promotion and reconciliation summary')",
            "read its build time with: docker buildx imagetools inspect "
            "ghcr.io/jeter-1/hass-mcp-engineering-beta@<digest> --format '{{json .Image}}' "
            "(label org.opencontainers.image.created)",
            "then run this helper again with --digest and --build-time"], None
    command = dispatch_command(dict(recovery, recovery_source_digest=digest, recovery_build_time=build_time),
                               facts["repository"])
    if facts.get("digest") != "present":
        return "stop", [f"digest {digest} is not a complete multi-architecture index on ghcr.io"], None
    return "dispatch_digest", [f"claim run {claim['run_id']} attempt {claim['run_attempt']} built {digest}; "
                               "Josh may dispatch digest recovery"], command


def render(facts, result):
    name, reasons, command = result
    lines = [f"Recovery verdict: {name} (failure mode {FAILURE_MODES[name]})",
             f"version {facts.get('version')} release {facts.get('release_sha', 'unknown')} "
             f"main {facts.get('main_sha')}"]
    for key in ("registry_version", "registry_commit", "git_tag", "github_release", "engineering_drift",
                "staged", "claim_build"):
        if key in facts:
            lines.append(f"  {key}: {facts[key]}")
    if isinstance(facts.get("claim"), dict):
        claim = facts["claim"]
        lines.append(f"  claim: run {claim.get('run_id')} attempt {claim.get('run_attempt')} "
                     f"event {watchdog.text(claim.get('event'))} schema {claim.get('schema')}")
    lines += [f"- {reason}" for reason in reasons]
    if command:
        lines += ["Owner command (Josh only; this helper never runs it):", command]
    return lines


def main(argv=None, environ=os.environ):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--run-id")
    parser.add_argument("--version")
    parser.add_argument("--digest")
    parser.add_argument("--build-time")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--repository", default=REPOSITORY)
    parser.add_argument("--main-ref", default="main")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.run_id is None and args.version is None:
            raise RecoveryError("run_id_or_version_required")
        facts = inspect(args.repo_root, args.repository, token=environ.get("GH_TOKEN") or None,
                        run_id=args.run_id, version=args.version, main_ref=args.main_ref, digest=args.digest)
        result = verdict(facts, args.digest, args.build_time)
    except (RecoveryError, watchdog.WatchdogError) as exc:
        print(f"Recovery inspection failed: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # Never a verdict or command from an unexpected failure.
        print(f"Recovery inspection failed: unexpected_{watchdog.text(type(exc).__name__)}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"verdict": result[0], "reasons": result[1], "command": result[2], "facts": facts},
                         sort_keys=True))
    else:
        print("\n".join(render(facts, result)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
