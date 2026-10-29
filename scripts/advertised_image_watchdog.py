"""Read-only watchdog for advertised Engineering images (HAMCP-223).

``check`` reads, at one Git ref, every add-on configuration the Home Assistant
Supervisor store would read (``**/config.{yaml,yml,json}`` beside a root
``repository.*``, skipping any path part that starts with "." or is "rootfs",
as upstream ``supervisor/store/data.py`` does). For each add-on it requires
``<image>:<version>`` on ghcr.io to be an index with exactly one manifest for
every declared architecture, then reports one outcome:

* ``ok``: every declared architecture is present;
* ``pending``: incomplete, but a publish run for the release commit is queued
  or in progress;
* ``mismatch``: incomplete and no publish run for that commit is live;
* ``unknown``: the registry or run listing stayed ambiguous or unavailable after
  bounded retry, or an architecture has no reviewed platform mapping.

Registry reads are anonymous. ``GH_TOKEN``, when set, is sent only to
api.github.com. ``alert`` turns one report into one deduplicated issue: it
opens or updates the issue on ``mismatch`` or ``unknown`` and fails, and
closes it on an ``ok`` report for ``main``. Nothing else is written; this
module never dispatches, reruns, tags or pushes.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request


_spec = importlib.util.spec_from_file_location(
    "hamcp223_github_retry", Path(__file__).resolve().parent / "github_retry.py")
retry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(retry)

REPOSITORY = "jeter-1/hass-mcp-admin"
PUBLISH_WORKFLOW = "publish-rc-image.yml"
DEFAULT_REF = "main"
REGISTRY = "ghcr.io"
API = "api.github.com"
CONFIG_SUFFIXES = frozenset({".json", ".yaml", ".yml"})
REQUIRED_KEYS = ("name", "version", "slug", "description", "arch")
# Supervisor architecture -> OCI platform (architecture, accepted variants).
PLATFORMS = {"amd64": ("amd64", frozenset({None})),
             "aarch64": ("arm64", frozenset({None, "v8"}))}
INDEX_TYPES = frozenset({"application/vnd.oci.image.index.v1+json",
                         "application/vnd.docker.distribution.manifest.list.v2+json"})
MANIFEST_TYPES = frozenset({"application/vnd.oci.image.manifest.v1+json",
                            "application/vnd.docker.distribution.manifest.v2+json"})
LIVE_STATUSES = frozenset({"queued", "in_progress", "requested", "waiting", "pending"})
OUTCOMES = ("ok", "pending", "unknown", "mismatch")  # increasing severity
RELEASE_WALK_LIMIT = 1000
RUN_PAGES = 3
BLOB_BOUND = 1_000_000
MANIFEST_BOUND = 4_000_000
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
REF_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
IMAGE_PATTERN = re.compile(r"ghcr\.io/([a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)+)")
TAG_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
TEXT_PATTERN = re.compile(r"[A-Za-z0-9_.:/@+-]{1,200}")
MARKER = "<!-- hamcp-223-publication-watchdog -->"
LABEL = "publication-watchdog"
BOT = "github-actions[bot]"


class WatchdogError(Exception):
    """A fixed category; never arbitrary remote or repository text."""


def text(value):
    """Bounded report text: anything outside the closed alphabet is "invalid"."""
    return value if isinstance(value, str) and TEXT_PATTERN.fullmatch(value) else "invalid"


# --- Git: what the store reads at one ref ----------------------------------

def git(root, *args, stdin=None):
    try:
        result = subprocess.run(["git", "-C", str(root), *args], input=stdin, check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise WatchdogError("git_unavailable") from exc
    if len(result.stdout) > 50_000_000:
        raise WatchdogError("git_output_bound")
    return result.stdout


def resolve_ref(root, ref):
    if not isinstance(ref, str) or not REF_PATTERN.fullmatch(ref) or ".." in ref:
        raise WatchdogError("invalid_ref")
    names = [ref] if SHA_PATTERN.fullmatch(ref) else [
        f"refs/remotes/origin/{ref}", f"refs/heads/{ref}", f"refs/tags/{ref}"]
    for name in names:
        try:
            value = git(root, "rev-parse", "--verify", "--quiet", "--end-of-options",
                        name + "^{commit}").decode().strip()
        except WatchdogError:
            continue
        if SHA_PATTERN.fullmatch(value):
            return value
    raise WatchdogError("ref_not_found")


def tree_entries(root, commit):
    entries = []
    for record in git(root, "ls-tree", "-r", "-z", "--full-tree", commit).split(b"\0"):
        if record:
            meta, _, path = record.partition(b"\t")
            mode, kind, oid = meta.decode().split()
            entries.append((mode, kind, oid, path.decode("utf-8", "replace")))
    return entries


def store_candidates(entries):
    """Paths Supervisor's ``_find_app_configs`` would select, with blob modes."""
    for mode, kind, oid, path in entries:
        parts = PurePosixPath(path).parts
        name = parts[-1]
        if (name.startswith("config.") and PurePosixPath(name).suffix in CONFIG_SUFFIXES
                and not any(part.startswith(".") or part == "rootfs" for part in parts)):
            yield mode, kind, oid, path


def parse_config(raw, path):
    if len(raw) > BLOB_BOUND:
        raise WatchdogError("config_oversized")
    try:
        if path.endswith(".json"):
            return json.loads(raw)
        import yaml  # Lazy: ``alert`` stays standard-library only.
        return yaml.safe_load(raw)
    except Exception as exc:  # Any parse failure: the store skips the file.
        raise WatchdogError("config_unreadable") from exc


def config_version(root, commit, path, cache):
    """The ``version`` the store would read for ``path`` at ``commit`` (or None)."""
    line = git(root, "cat-file", "--batch-check=%(objectname) %(objecttype)",
               stdin=f"{commit}:{path}\n".encode()).decode().split()
    if len(line) != 2 or line[1] != "blob":
        return None
    if line[0] not in cache:
        try:
            config = parse_config(git(root, "cat-file", "blob", line[0]), path)
            cache[line[0]] = str(config["version"]) if isinstance(config, dict) else None
        except (WatchdogError, KeyError):
            cache[line[0]] = None
    return cache[line[0]]


def release_commit(root, head, path, version, limit=RELEASE_WALK_LIMIT):
    """Oldest first-parent ancestor in the contiguous run advertising ``version``."""
    commits = git(root, "rev-list", "--first-parent", f"--max-count={limit + 1}",
                  head).decode().split()
    cache, release = {}, None
    for commit in commits[:limit]:
        if config_version(root, commit, path, cache) != version:
            return release
        release = commit
    # The whole history matched: a root commit is the release; a cut walk is not.
    return release if len(commits) <= limit else None


def advertised_addons(root, commit):
    entries = tree_entries(root, commit)
    root_files = {path for _, kind, _, path in entries if kind == "blob" and "/" not in path}
    if not root_files & {"repository.yaml", "repository.yml", "repository.json"}:
        raise WatchdogError("repository_metadata_missing")
    addons, skipped = [], []
    for mode, kind, oid, path in store_candidates(entries):
        if kind != "blob" or mode not in {"100644", "100755"}:
            # A symlinked or unusual entry may resolve outside this tree.
            addons.append({"path": text(path), "outcome": "unknown", "reason": "config_not_regular_file"})
            continue
        try:
            config = parse_config(git(root, "cat-file", "blob", oid), path)
        except WatchdogError as exc:
            skipped.append({"path": text(path), "reason": str(exc)})
            continue
        if not isinstance(config, dict) or any(key not in config for key in REQUIRED_KEYS):
            skipped.append({"path": text(path), "reason": "not_a_store_config"})
            continue
        addons.append({"path": text(path), "config": config})
    return addons, skipped


# --- HTTP reads under the HAMCP-221 retry bound ----------------------------

class Reader:
    def __init__(self, opener=None, clock=None, sleep=None, jitter=None, wall=None):
        self.opener = opener or retry.default_opener()
        self.bounds = {"clock": clock, "sleep": sleep, "jitter": jitter, "wall": wall}

    def get(self, url, headers, max_bytes, method="GET", body=None, retried=True):
        host = urllib.parse.urlsplit(url).hostname

        def attempt(remaining):
            request = urllib.request.Request(url, headers=headers, method=method, data=body)
            try:
                response = self.opener.open(request, timeout=max(remaining, retry.MIN_ATTEMPT_SECONDS))
                with response:
                    status = response.status
                    if status not in (200, 201):
                        retry.http_outcome(status, response.headers.get("Retry-After"))
                    content = response.read(max_bytes + 1)
                    kind = response.headers.get("Content-Type") or ""
            except (OSError, http.client.HTTPException) as exc:
                raise retry.network_failure(exc) from None
            if len(content) > max_bytes:
                raise retry.FinalRead("oversized_response")
            return kind.split(";")[0].strip(), content

        if not retried:
            try:
                return attempt(retry.MAX_TOTAL_SECONDS)
            except (retry.RetryableRead, retry.FinalRead) as exc:
                raise retry.GitHubReadError(host, exc.status, 1, False) from None
        return retry.bounded_retry(attempt, host, **self.bounds)

    def json(self, url, headers, max_bytes=MANIFEST_BOUND, **kwargs):
        _, content = self.get(url, headers, max_bytes, **kwargs)
        try:
            return json.loads(content)
        except ValueError:
            raise retry.GitHubReadError(urllib.parse.urlsplit(url).hostname,
                                        "malformed_response", 1, False) from None


def registry_state(reader, repository_path, tag, arch):
    """``(state, missing, reason)``; state is complete, incomplete or unknown."""
    try:
        token = reader.json(f"https://{REGISTRY}/token?scope=repository:{repository_path}:pull"
                            f"&service={REGISTRY}", {"Accept": "application/json"}, 65536).get("token")
        if not isinstance(token, str) or not 0 < len(token) <= 8192:
            return "unknown", [], "registry_token_malformed"
        headers = {"Authorization": "Bearer " + token,
                   "Accept": ", ".join(sorted(INDEX_TYPES | MANIFEST_TYPES))}
        base = f"https://{REGISTRY}/v2/{repository_path}/manifests/"
        try:
            kind, content = reader.get(base + tag, headers, MANIFEST_BOUND)
        except retry.GitHubReadError as exc:
            if exc.status == 404:
                return "incomplete", sorted(arch), "image_tag_absent"
            raise
        index = json.loads(content)
        if kind not in INDEX_TYPES or index.get("mediaType", kind) not in INDEX_TYPES:
            return "unknown", [], "image_is_not_a_multi_platform_index"
        matches = {name: [entry for entry in index.get("manifests") or []
                          if isinstance(entry, dict) and isinstance(entry.get("platform"), dict)
                          and entry["platform"].get("os") == "linux"
                          and entry["platform"].get("architecture") == PLATFORMS[name][0]
                          and entry["platform"].get("variant") in PLATFORMS[name][1]]
                   for name in arch}
        if any(len(found) > 1 or (found and not (isinstance(found[0].get("digest"), str)
                                                 and DIGEST_PATTERN.fullmatch(found[0]["digest"])))
               for found in matches.values()):
            return "unknown", [], "ambiguous_platform_entry"
        missing = [name for name in arch if not matches[name]]
        for name in arch:
            if not matches[name]:
                continue
            digest = matches[name][0]["digest"]
            try:
                child_kind, child = reader.get(base + digest, headers, MANIFEST_BOUND)
            except retry.GitHubReadError as exc:
                if exc.status == 404:
                    missing.append(name)
                    continue
                raise
            if (child_kind not in MANIFEST_TYPES
                    or "sha256:" + hashlib.sha256(child).hexdigest() != digest):
                return "unknown", [], "platform_manifest_unverified"
        return ("incomplete" if missing else "complete"), sorted(missing), (
            "platform_manifest_missing" if missing else "all_platforms_present")
    except retry.GitHubReadError as exc:
        return "unknown", [], f"registry_{text(str(exc.status))}"
    except (ValueError, AttributeError, TypeError):
        return "unknown", [], "registry_response_malformed"


def publish_runs(reader, repository, sha, token=None):
    """Every publish run for exactly ``sha``; raises on an unknown listing."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = "Bearer " + token
    runs = []
    for page in range(1, RUN_PAGES + 1):
        data = reader.json(f"https://{API}/repos/{repository}/actions/workflows/"
                           f"{PUBLISH_WORKFLOW}/runs?head_sha={sha}&per_page=100&page={page}", headers)
        batch = data.get("workflow_runs") if isinstance(data, dict) else None
        if not isinstance(batch, list) or type(data.get("total_count")) is not int:
            raise WatchdogError("run_listing_malformed")
        for run in batch:
            if not isinstance(run, dict) or type(run.get("id")) is not int:
                raise WatchdogError("run_listing_malformed")
            if run.get("head_sha") == sha:
                runs.append({"id": run["id"], "attempt": run.get("run_attempt"),
                             "event": text(run.get("event")), "status": text(run.get("status")),
                             "conclusion": text(run.get("conclusion") or "none")})
        if len(batch) < 100:
            if data["total_count"] > (page - 1) * 100 + len(batch):
                raise WatchdogError("run_listing_inconsistent")
            return sorted(runs, key=lambda run: run["id"])
    raise WatchdogError("run_listing_truncated")


# --- Report ----------------------------------------------------------------

def inspect_addon(root, commit, addon, reader, repository, token):
    if "config" not in addon:
        return addon
    config, result = addon["config"], {"path": addon["path"]}
    version, image = str(config["version"]), config.get("image")
    arch = config["arch"]
    result.update(slug=text(str(config["slug"])), version=text(version),
                  image=text(image if isinstance(image, str) else "none"))
    if not isinstance(arch, list) or not arch or not all(isinstance(a, str) for a in arch):
        return dict(result, outcome="unknown", reason="arch_not_a_list")
    result["arch"] = [text(a) for a in sorted(set(arch))]
    unmapped = sorted(a for a in set(arch) if a not in PLATFORMS)
    match = IMAGE_PATTERN.fullmatch(image) if isinstance(image, str) else None
    release = release_commit(root, commit, addon["path"], version)
    result["release_sha"] = release or "unknown"
    if unmapped:
        return dict(result, outcome="unknown", reason="unmapped_arch", unknown_arch=[text(a) for a in unmapped])
    if not match or ".." in image:
        return dict(result, outcome="unknown", reason="image_not_a_ghcr_reference")
    if not TAG_PATTERN.fullmatch(version):
        return dict(result, outcome="unknown", reason="version_not_a_registry_tag")
    state, missing, reason = registry_state(reader, match.group(1), version, sorted(set(arch)))
    result.update(registry=state, missing_arch=missing, reason=reason)
    if state == "complete":
        return dict(result, outcome="ok")
    if release is None:
        return dict(result, outcome="unknown", reason=reason + "+release_commit_unknown")
    try:
        runs = publish_runs(reader, repository, release, token)
    except (retry.GitHubReadError, WatchdogError) as exc:
        detail = text(str(exc.status)) if isinstance(exc, retry.GitHubReadError) else str(exc)
        return dict(result, outcome="unknown", reason=reason + "+runs_" + detail)
    result["runs"] = runs
    if state == "unknown":
        return dict(result, outcome="unknown")
    live = any(run["status"] in LIVE_STATUSES for run in runs)
    return dict(result, outcome="pending" if live else "mismatch")


def check(root, ref, repository=REPOSITORY, reader=None, token=None):
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise WatchdogError("invalid_repository")
    report = {"schema": 1, "repository": repository, "ref": text(ref)}
    try:
        commit = resolve_ref(root, ref)
        report["ref_sha"] = commit
        addons, skipped = advertised_addons(root, commit)
    except WatchdogError as exc:
        return dict(report, outcome="unknown", reason=str(exc), addons=[], skipped=[])
    reader = reader or Reader()
    results = [inspect_addon(root, commit, addon, reader, repository, token) for addon in addons]
    outcome = max((r["outcome"] for r in results), key=OUTCOMES.index, default="unknown")
    report.update(outcome=outcome, addons=results, skipped=skipped)
    if not results:
        report["reason"] = "no_advertised_addons"
    return report


def encode(report):
    return json.dumps(report, sort_keys=True, separators=(",", ":"))


def summary_lines(report):
    yield (f"Watchdog outcome: {report.get('outcome')} for ref {report.get('ref')} "
           f"at {report.get('ref_sha', 'unknown')}" + (f" ({report['reason']})" if report.get("reason") else ""))
    for addon in report.get("addons", []):
        yield (f"- {addon.get('path')}: {addon.get('outcome')} version {addon.get('version', 'unknown')} "
               f"release {addon.get('release_sha', 'unknown')} reason {addon.get('reason')}"
               + (f" missing {','.join(addon['missing_arch'])}" if addon.get("missing_arch") else "")
               + (f" unknown arch {','.join(addon['unknown_arch'])}" if addon.get("unknown_arch") else ""))
        for run in addon.get("runs", []):
            yield (f"  publish run {run['id']} attempt {run['attempt']} ({run['event']}): "
                   f"{run['status']}/{run['conclusion']}")
    for item in report.get("skipped", []):
        yield f"- skipped {item['path']}: {item['reason']}"


# --- Alert: one deduplicated issue ------------------------------------------

def load_report(raw):
    """A malformed or missing report is itself an ``unknown`` outcome."""
    try:
        report = json.loads(raw) if isinstance(raw, str) and 0 < len(raw) <= 200_000 else None
    except ValueError:
        report = None
    if not isinstance(report, dict) or report.get("outcome") not in OUTCOMES:
        return {"schema": 1, "outcome": "unknown", "reason": "watchdog_check_produced_no_report",
                "addons": [], "skipped": []}
    return report


def fingerprint(report):
    return hashlib.sha256(encode(report).encode()).hexdigest()


def issue_body(report, run_url):
    addons = report.get("addons", [])
    version = next((a.get("version") for a in addons if a.get("outcome") != "ok"), "unknown")
    lines = [MARKER, f"<!-- fingerprint:{fingerprint(report)} -->",
             f"## Advertised Engineering image: `{report['outcome']}`", "",
             "The watchdog found that the image Home Assistant would install for the advertised",
             "version is not fully published, or could not prove that it is.", "",
             "```text", *summary_lines(report), "```", ""]
    if run_url:
        lines += [f"Watchdog run: {run_url}", ""]
    lines += ["Next step (read-only; it prints the owner recovery command or explains a roll-forward):",
              "", "```sh",
              f"python3 -I scripts/publication_recovery.py --version {version}", "```", "",
              "Runbook: `docs/PUBLICATION_RECOVERY_RUNBOOK.md`. This issue is updated in place and",
              "closed automatically by the next `ok` report for `main`."]
    return "\n".join(lines)


class IssueAPI:
    def __init__(self, repository, token, reader=None):
        if not REPOSITORY_PATTERN.fullmatch(repository) or not token:
            raise WatchdogError("alert_configuration_missing")
        self.base = f"https://{API}/repos/{repository}"
        self.reader = reader or Reader()
        self.headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                        "Authorization": "Bearer " + token}

    def read(self, route):
        return self.reader.json(self.base + route, self.headers)

    def write(self, method, route, payload):
        # Writes are never retried: a lost response must not duplicate an issue.
        return self.reader.json(self.base + route, dict(self.headers, **{"Content-Type": "application/json"}),
                                method=method, body=json.dumps(payload).encode(), retried=False)

    def ensure_label(self):
        try:
            self.read(f"/labels/{LABEL}")
        except retry.GitHubReadError as exc:
            if exc.status != 404:
                raise
            self.write("POST", "/labels", {"name": LABEL, "color": "b60205",
                                           "description": "Advertised image watchdog (HAMCP-223)"})

    def open_issues(self):
        issues = self.read(f"/issues?state=open&labels={LABEL}&per_page=100&sort=created&direction=asc")
        if not isinstance(issues, list):
            raise WatchdogError("issue_listing_malformed")
        return sorted((issue for issue in issues
                       if isinstance(issue, dict) and "pull_request" not in issue
                       and type(issue.get("number")) is int
                       and (issue.get("user") or {}).get("login") == BOT
                       and MARKER in (issue.get("body") or "")), key=lambda issue: issue["number"])


def alert(report, api, run_url=None):
    """Apply one report; returns (exit code, action)."""
    outcome = report["outcome"]
    if outcome in {"ok", "pending"}:
        if outcome == "pending" or report.get("ref") != DEFAULT_REF:
            # Pending neither raises nor clears; only main proves the store is healthy.
            return 0, "no_change"
        issues = api.open_issues()
        for issue in issues:
            api.write("POST", f"/issues/{issue['number']}/comments",
                      {"body": f"Resolved: `main` at `{report.get('ref_sha')}` reports `ok`."
                               + (f" Watchdog run: {run_url}" if run_url else "")})
            api.write("PATCH", f"/issues/{issue['number']}", {"state": "closed", "state_reason": "completed"})
        return 0, "closed" if issues else "no_change"
    body = issue_body(report, run_url)
    title = f"Publication watchdog: advertised image {outcome}"
    issues = api.open_issues()
    if not issues:
        api.ensure_label()
        api.write("POST", "/issues", {"title": title, "body": body, "labels": [LABEL]})
        return 1, "opened"
    issue = issues[0]
    previous = re.search(r"<!-- fingerprint:([0-9a-f]{64}) -->", issue.get("body") or "")
    if previous and previous.group(1) == fingerprint(report):
        return 1, "unchanged"
    api.write("PATCH", f"/issues/{issue['number']}", {"title": title, "body": body})
    api.write("POST", f"/issues/{issue['number']}/comments",
              {"body": f"Watchdog report changed: `{outcome}`." + (f" Run: {run_url}" if run_url else "")})
    return 1, "updated"


def run_url(environ):
    server, repository, run_id = (environ.get(k, "") for k in
                                  ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    if (server == "https://github.com" and REPOSITORY_PATTERN.fullmatch(repository)
            and re.fullmatch(r"[1-9][0-9]{0,19}", run_id)):
        return f"{server}/{repository}/actions/runs/{run_id}"
    return None


def main(argv=None, environ=os.environ):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="operation", required=True)
    checker = sub.add_parser("check")
    checker.add_argument("--repo-root", type=Path, default=Path.cwd())
    checker.add_argument("--ref", default=DEFAULT_REF)
    checker.add_argument("--repository", default=REPOSITORY)
    checker.add_argument("--github-output", type=Path)
    alerter = sub.add_parser("alert")
    alerter.add_argument("--repository", default=REPOSITORY)
    args = parser.parse_args(argv)
    try:
        if args.operation == "check":
            report = check(args.repo_root, args.ref, args.repository,
                           token=environ.get("GH_TOKEN") or None)
            for line in summary_lines(report):
                print(line)
            if args.github_output:
                with args.github_output.open("a", encoding="utf-8") as stream:
                    stream.write(f"report={encode(report)}\noutcome={report['outcome']}\n")
            return 0
        report = load_report(environ.get("WATCHDOG_REPORT"))
        code, action = alert(report, IssueAPI(args.repository, environ.get("GH_TOKEN")), run_url(environ))
        print(f"Watchdog outcome {report['outcome']}; alert issue: {action}.")
        return code
    except (WatchdogError, retry.GitHubReadError) as exc:
        # Both carry only fixed categories, hosts and statuses.
        print(f"Watchdog {args.operation} failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
