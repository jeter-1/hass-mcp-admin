"""HAMCP-223 advertised-image watchdog, with recorded responses only.

No network, GitHub credentials or registry access: a disposable Git repository
supplies the store layout and a fake opener replays registry and API responses.
"""

import email.message
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.parse

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/advertised_image_watchdog.py"
WORKFLOW = ROOT / ".github/workflows/publication-watchdog.yml"
spec = importlib.util.spec_from_file_location("advertised_image_watchdog", SCRIPT)
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)

IMAGE = "ghcr.io/example-owner/example-addon"
REGISTRY_TOKEN = "synthetic-anonymous-registry-token"
API_TOKEN = "synthetic-workflow-token"
INDEX_TYPE = "application/vnd.oci.image.index.v1+json"
MANIFEST_TYPE = "application/vnd.oci.image.manifest.v1+json"


class Response:
    def __init__(self, status, body=b"", content_type="application/json", headers=None):
        self.status, self.body = status, body
        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type
        for key, value in (headers or {}).items():
            self.headers[key] = value

    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Opener:
    """Replays queued responses by (method, URL without query) and records requests."""

    def __init__(self):
        self.routes, self.requests = {}, []

    def add(self, method, url, *responses):
        self.routes.setdefault((method, url), []).extend(responses)

    def open(self, request, timeout=None):
        url = request.full_url.split("?")[0]
        self.requests.append((request.get_method(), request.full_url, dict(request.header_items()),
                              request.data))
        queue = self.routes.get((request.get_method(), url))
        if not queue:
            raise AssertionError(f"unexpected request {request.get_method()} {url}")
        response = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(response, BaseException):
            raise response
        if response.status not in (200, 201):
            raise urllib.error.HTTPError(request.full_url, response.status, "synthetic",
                                         response.headers, io.BytesIO(response.body))
        return response


def child(arch):
    body = json.dumps({"mediaType": MANIFEST_TYPE, "arch": arch}).encode()
    return "sha256:" + hashlib.sha256(body).hexdigest(), body


def index(*platforms, extra=()):
    entries, children = [], {}
    for architecture, variant in platforms:
        digest, body = child(architecture + (variant or ""))
        platform = {"os": "linux", "architecture": architecture}
        if variant:
            platform["variant"] = variant
        entries.append({"mediaType": MANIFEST_TYPE, "digest": digest, "platform": platform})
        children[digest] = body
    # Build attestations are listed with an unknown platform and never count.
    entries.append({"mediaType": MANIFEST_TYPE, "digest": "sha256:" + "a" * 64,
                    "platform": {"os": "unknown", "architecture": "unknown"}})
    entries.extend(extra)
    return json.dumps({"mediaType": INDEX_TYPE, "manifests": entries}).encode(), children


def run(run_id, status="completed", conclusion="success", attempt=1, sha=None):
    return {"id": run_id, "run_attempt": attempt, "event": "workflow_run", "status": status,
            "conclusion": conclusion, "head_sha": sha}


class WatchdogFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Synthetic test")
        self.git("config", "user.email", "synthetic@example.invalid")
        self.write("repository.yaml", {"name": "Synthetic", "url": "https://example.invalid",
                                       "maintainer": "Synthetic"})
        self.config = {"name": "Addon", "version": "1.0.0", "slug": "addon",
                       "description": "Synthetic", "arch": ["amd64", "aarch64"], "image": IMAGE}
        self.commit({**self.config, "version": "0.9.0"}, "Older release")
        self.release = self.commit(self.config, "Release 1.0.0")
        (self.root / "README.md").write_text("later unrelated change\n")
        self.git("add", ".")
        self.git("commit", "-qm", "Later change")
        self.head = self.git("rev-parse", "HEAD")
        self.opener = Opener()
        self.sleeps = []
        self.clock = [0.0]
        self.reader = watchdog.Reader(self.opener, clock=lambda: self.clock[0],
                                      sleep=self.sleep, jitter=lambda: 0.0, wall=lambda: 0.0)

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.clock[0] += seconds

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args],
                                       stderr=subprocess.DEVNULL, text=True).strip()

    def write(self, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(data) if isinstance(data, dict) else data)

    def commit(self, config, message, path="addon/config.yaml"):
        self.write(path, config)
        self.git("add", ".")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def registry(self, *responses, tag="1.0.0", children=None):
        self.opener.add("GET", "https://ghcr.io/token",
                        Response(200, json.dumps({"token": REGISTRY_TOKEN}).encode()))
        self.opener.add("GET", f"https://ghcr.io/v2/example-owner/example-addon/manifests/{tag}", *responses)
        for digest, body in (children or {}).items():
            self.opener.add("GET", f"https://ghcr.io/v2/example-owner/example-addon/manifests/{digest}",
                            Response(200, body, MANIFEST_TYPE))

    def present(self, *platforms):
        body, children = index(*platforms)
        self.registry(Response(200, body, INDEX_TYPE), children=children)

    def runs(self, *runs, sha=None, total=None):
        runs = [dict(r, head_sha=r["head_sha"] or (sha or self.release)) for r in runs]
        self.opener.add("GET", "https://api.github.com/repos/jeter-1/hass-mcp-admin/actions/"
                        "workflows/publish-rc-image.yml/runs",
                        Response(200, json.dumps({"total_count": len(runs) if total is None else total,
                                                  "workflow_runs": runs}).encode()))

    def check(self, ref="main", token=API_TOKEN):
        return watchdog.check(self.root, ref, reader=self.reader, token=token)

    def addon(self, report):
        self.assertEqual(len(report["addons"]), 1)
        return report["addons"][0]


class RegistryOutcomeTests(WatchdogFixture):
    def test_present_image_for_every_declared_arch_is_ok(self):
        self.present(("amd64", None), ("arm64", "v8"))
        report = self.check()
        self.assertEqual(report["outcome"], "ok")
        addon = self.addon(report)
        self.assertEqual((addon["version"], addon["release_sha"], addon["missing_arch"]),
                         ("1.0.0", self.release, []))
        self.assertEqual(report["ref_sha"], self.head)
        # ok never needs the run listing.
        self.assertFalse(any("api.github.com" in url for _, url, _, _ in self.opener.requests))

    def test_registry_reads_are_anonymous_and_api_token_stays_on_api_host(self):
        self.registry(Response(404))
        self.runs(run(10, conclusion="failure"))
        self.check()
        for _, url, headers, _ in self.opener.requests:
            host = urllib.parse.urlsplit(url).hostname
            authorization = headers.get("Authorization", "")
            if host == "ghcr.io":
                self.assertNotIn(API_TOKEN, authorization)
                self.assertIn(authorization, ("", "Bearer " + REGISTRY_TOKEN))
            else:
                self.assertEqual((host, authorization), ("api.github.com", "Bearer " + API_TOKEN))
        self.assertEqual(self.opener.requests[0][1].split("?")[0], "https://ghcr.io/token")
        self.assertNotIn("Authorization", self.opener.requests[0][2])

    def test_absent_image_without_live_run_is_mismatch_naming_runs(self):
        self.registry(Response(404))
        self.runs(run(39891520683, conclusion="failure", attempt=2), run(39891944329, conclusion="skipped"))
        report = self.check()
        addon = self.addon(report)
        self.assertEqual(report["outcome"], "mismatch")
        self.assertEqual((addon["reason"], addon["missing_arch"]), ("image_tag_absent", ["aarch64", "amd64"]))
        self.assertEqual([(r["id"], r["attempt"], r["conclusion"]) for r in addon["runs"]],
                         [(39891520683, 2, "failure"), (39891944329, 1, "skipped")])
        lines = "\n".join(watchdog.summary_lines(report))
        for expected in ("1.0.0", self.release, "aarch64,amd64", "39891520683 attempt 2", "completed/failure"):
            self.assertIn(expected, lines)

    def test_absent_image_with_no_publish_run_at_all_is_mismatch(self):
        self.registry(Response(404))
        self.runs()
        self.assertEqual(self.check()["outcome"], "mismatch")

    def test_one_arch_missing_is_mismatch_naming_that_arch(self):
        self.present(("amd64", None))
        self.runs(run(5, conclusion="success"))
        addon = self.addon(self.check())
        self.assertEqual((addon["outcome"], addon["missing_arch"], addon["reason"]),
                         ("mismatch", ["aarch64"], "platform_manifest_missing"))

    def test_listed_platform_whose_manifest_is_gone_is_missing(self):
        body, children = index(("amd64", None), ("arm64", None))
        gone = next(d for d, b in children.items() if b"arm64" in b)
        self.registry(Response(200, body, INDEX_TYPE),
                      children={d: b for d, b in children.items() if d != gone})
        self.opener.add("GET", f"https://ghcr.io/v2/example-owner/example-addon/manifests/{gone}", Response(404))
        self.runs()
        addon = self.addon(self.check())
        self.assertEqual((addon["outcome"], addon["missing_arch"]), ("mismatch", ["aarch64"]))

    def test_live_publish_run_for_release_commit_is_pending(self):
        for status in ("queued", "in_progress", "waiting", "requested", "pending"):
            with self.subTest(status=status):
                self.opener.routes.clear()
                self.registry(Response(404))
                self.runs(run(7, conclusion="failure"), run(8, status=status, conclusion=None))
                self.assertEqual(self.check()["outcome"], "pending")

    def test_live_run_for_another_commit_never_makes_pending(self):
        self.registry(Response(404))
        self.runs(run(8, status="in_progress", conclusion=None, sha="f" * 40))
        addon = self.addon(self.check())
        self.assertEqual((addon["outcome"], addon["runs"]), ("mismatch", []))

    def test_completed_failed_run_is_mismatch(self):
        self.present(("amd64", None))
        self.runs(run(9, conclusion="failure"), run(10, conclusion="cancelled"))
        addon = self.addon(self.check())
        self.assertEqual(addon["outcome"], "mismatch")
        self.assertEqual([r["conclusion"] for r in addon["runs"]], ["failure", "cancelled"])

    def test_registry_5xx_is_unknown_after_bounded_retry(self):
        self.registry(Response(503))
        self.runs(run(11, conclusion="failure"))
        addon = self.addon(self.check())
        self.assertEqual((addon["outcome"], addon["reason"]), ("unknown", "registry_503"))
        self.assertEqual(addon["runs"][0]["id"], 11)
        manifest_reads = [u for _, u, _, _ in self.opener.requests if "/manifests/" in u]
        self.assertEqual(len(manifest_reads), watchdog.retry.MAX_ATTEMPTS)
        self.assertEqual(self.sleeps, [1.0, 2.0, 4.0, 8.0])

    def test_transient_5xx_then_success_is_ok(self):
        body, children = index(("amd64", None), ("arm64", None))
        self.registry(Response(502), Response(429, headers={"Retry-After": "3"}),
                      Response(200, body, INDEX_TYPE), children=children)
        self.assertEqual(self.check()["outcome"], "ok")
        self.assertEqual(self.sleeps, [1.0, 3.0])

    def test_network_timeout_is_unknown(self):
        self.registry(urllib.error.URLError(TimeoutError("synthetic")))
        self.runs()
        addon = self.addon(self.check())
        self.assertEqual((addon["outcome"], addon["reason"]), ("unknown", "registry_timeout"))

    def test_ambiguous_registry_responses_are_unknown(self):
        body, children = index(("amd64", None), ("arm64", None))
        bad_child = {d: b + b" " for d, b in children.items()}
        duplicate, _ = index(("amd64", None), ("amd64", None), ("arm64", None))
        cases = {
            "token_refused": None,
            "single_manifest": (Response(200, json.dumps({"mediaType": MANIFEST_TYPE}).encode(), MANIFEST_TYPE), {}),
            "digest_mismatch": (Response(200, body, INDEX_TYPE), bad_child),
            "duplicate_platform": (Response(200, duplicate, INDEX_TYPE), {}),
            "malformed_index": (Response(200, b"{", INDEX_TYPE), {}),
            "forbidden": (Response(403), {}),
        }
        for name, case in cases.items():
            with self.subTest(name=name):
                self.opener.routes.clear()
                if case is None:
                    self.opener.add("GET", "https://ghcr.io/token", Response(401))
                else:
                    self.registry(case[0], children=case[1])
                self.runs()
                self.assertEqual(self.check()["outcome"], "unknown")

    def test_unmapped_arch_is_unknown_never_ok(self):
        self.commit(dict(self.config, arch=["amd64", "aarch64", "armv7"]), "Add armv7")
        self.present(("amd64", None), ("arm64", None), ("arm", "v7"))
        addon = self.addon(self.check())
        self.assertEqual((addon["outcome"], addon["reason"], addon["unknown_arch"]),
                         ("unknown", "unmapped_arch", ["armv7"]))
        self.assertEqual(self.opener.requests, [])

    def test_run_listing_failure_while_incomplete_is_unknown(self):
        self.registry(Response(404))
        cases = (Response(500), Response(200, b"[]"),
                 Response(200, json.dumps({"total_count": 5, "workflow_runs": []}).encode()))
        for response in cases:
            with self.subTest(status=response.status, body=response.body[:20]):
                self.opener.routes.pop(("GET", "https://api.github.com/repos/jeter-1/hass-mcp-admin/"
                                        "actions/workflows/publish-rc-image.yml/runs"), None)
                self.opener.add("GET", "https://api.github.com/repos/jeter-1/hass-mcp-admin/actions/"
                                "workflows/publish-rc-image.yml/runs", response)
                addon = self.addon(self.check())
                self.assertEqual(addon["outcome"], "unknown")
                self.assertIn("+runs_", addon["reason"])

    def test_non_ghcr_or_missing_image_is_unknown(self):
        for image in (None, "docker.io/example/addon", "ghcr.io/example/{arch}-addon",
                      "ghcr.io/Example/addon", "ghcr.io/example/../addon"):
            with self.subTest(image=image):
                config = dict(self.config, image=image) if image else {
                    k: v for k, v in self.config.items() if k != "image"}
                self.commit(config, "Change image " + str(image))
                self.assertEqual(self.addon(self.check())["outcome"], "unknown")


class StoreDiscoveryTests(WatchdogFixture):
    def test_only_store_visible_configs_are_inspected(self):
        for path in (".hidden/config.yaml", "addon/rootfs/config.yaml", "other/config.txt",
                     "retired/retired-addon-config.yaml", "addon/.cache/config.json"):
            self.write(path, dict(self.config, slug="hidden", image="docker.io/never/read"))
        self.write("broken/config.yaml", "name: [unterminated\n")
        self.write("partial/config.yml", {"name": "No version"})
        self.git("add", ".")
        self.git("commit", "-qm", "Store-invisible files")
        self.present(("amd64", None), ("arm64", None))
        report = self.check()
        self.assertEqual(report["outcome"], "ok")
        self.assertEqual([a["path"] for a in report["addons"]], ["addon/config.yaml"])
        self.assertEqual(sorted((s["path"], s["reason"]) for s in report["skipped"]),
                         [("broken/config.yaml", "config_unreadable"),
                          ("partial/config.yml", "not_a_store_config")])

    def test_json_config_is_read_like_the_store(self):
        self.git("rm", "-q", "addon/config.yaml")
        self.write("addon/config.json", json.dumps(self.config))
        self.git("add", ".")
        self.git("commit", "-qm", "JSON config")
        self.present(("amd64", None), ("arm64", None))
        addon = self.addon(self.check())
        self.assertEqual((addon["path"], addon["outcome"]), ("addon/config.json", "ok"))

    def test_missing_repository_metadata_or_unresolvable_ref_is_unknown(self):
        self.git("rm", "-q", "repository.yaml")
        self.git("commit", "-qm", "No repository metadata")
        self.assertEqual(self.check()["reason"], "repository_metadata_missing")
        for ref in ("does-not-exist", "-rf", "main..other", "a" * 300, "x y", ""):
            with self.subTest(ref=ref):
                report = self.check(ref)
                self.assertEqual(report["outcome"], "unknown")
        self.assertEqual(self.opener.requests, [])

    def test_no_store_config_is_unknown(self):
        self.git("rm", "-q", "addon/config.yaml")
        self.git("commit", "-qm", "No add-ons")
        report = self.check()
        self.assertEqual((report["outcome"], report["reason"]), ("unknown", "no_advertised_addons"))

    def test_symlinked_config_is_unknown(self):
        (self.root / "linked").mkdir()
        (self.root / "linked/config.yaml").symlink_to("../addon/config.yaml")
        self.git("add", ".")
        self.git("commit", "-qm", "Symlink")
        self.present(("amd64", None), ("arm64", None))
        report = self.check()
        self.assertEqual(report["outcome"], "unknown")
        self.assertIn({"path": "linked/config.yaml", "outcome": "unknown",
                       "reason": "config_not_regular_file"}, report["addons"])

    def test_ref_may_be_a_full_sha_branch_or_tag(self):
        self.git("tag", "v1", self.release)
        for ref, sha in ((self.release, self.release), ("v1", self.release), ("main", self.head)):
            with self.subTest(ref=ref):
                self.opener.routes.clear()
                self.present(("amd64", None), ("arm64", None))
                self.assertEqual(self.check(ref)["ref_sha"], sha)

    def test_release_commit_is_first_commit_of_contiguous_version_run(self):
        path = "addon/config.yaml"
        self.assertEqual(watchdog.release_commit(self.root, self.head, path, "1.0.0"), self.release)
        older = self.git("rev-parse", "HEAD~2")
        self.assertEqual(watchdog.release_commit(self.root, self.head, path, "0.9.0"), None)
        self.assertEqual(watchdog.release_commit(self.root, older, path, "0.9.0"), older)
        # The walk bound never guesses a release.
        self.assertIsNone(watchdog.release_commit(self.root, self.head, path, "1.0.0", limit=2))
        self.assertEqual(watchdog.release_commit(self.root, self.head, path, "1.0.0", limit=3), self.release)
        # A version that returns after a different one starts a new release.
        self.commit(dict(self.config, version="1.1.0"), "1.1.0")
        again = self.commit(self.config, "Back to 1.0.0")
        self.assertEqual(watchdog.release_commit(self.root, again, path, "1.0.0"), again)


class CommandLineTests(WatchdogFixture):
    def test_check_writes_one_line_report_and_outcome(self):
        output = Path(self.temp.name) / "output"
        self.assertEqual(watchdog.main(["check", "--repo-root", str(self.root), "--ref", "missing",
                                        "--github-output", str(output)], {}), 0)
        lines = output.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0].removeprefix("report="))["reason"], "ref_not_found")
        self.assertEqual(lines[1], "outcome=unknown")

    def test_invalid_repository_fails_without_report(self):
        self.assertEqual(watchdog.main(["check", "--repo-root", str(self.root),
                                        "--repository", "not a repo"], {}), 2)

    def test_alert_without_token_fails_closed(self):
        self.assertEqual(watchdog.main(["alert"], {"WATCHDOG_REPORT": "{}"}), 2)


ISSUES = "https://api.github.com/repos/jeter-1/hass-mcp-admin/issues"


class IssueStore(Opener):
    """A small in-memory model of the GitHub issue API."""

    def __init__(self):
        super().__init__()
        self.issues, self.comments, self.label = {}, [], False
        self.fail_writes = None

    def open(self, request, timeout=None):
        method, url = request.get_method(), request.full_url
        path = urllib.parse.urlsplit(url).path.removeprefix("/repos/jeter-1/hass-mcp-admin")
        self.requests.append((method, url, dict(request.header_items()), request.data))
        payload = json.loads(request.data) if request.data else None
        if method != "GET" and self.fail_writes:
            raise urllib.error.HTTPError(url, self.fail_writes, "synthetic", email.message.Message(),
                                         io.BytesIO(b""))
        if (method, path) == ("GET", "/labels/" + watchdog.LABEL):
            if not self.label:
                raise urllib.error.HTTPError(url, 404, "missing", email.message.Message(), io.BytesIO(b""))
            return Response(200, b"{}")
        if (method, path) == ("POST", "/labels"):
            self.label = True
            return Response(201, b"{}")
        if (method, path) == ("GET", "/issues"):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            assert query["labels"] == [watchdog.LABEL] and query["state"] == ["open"]
            return Response(200, json.dumps([i for i in self.issues.values()
                                             if i["state"] == "open" and watchdog.LABEL in i["labels"]]).encode())
        if (method, path) == ("POST", "/issues"):
            number = len(self.issues) + 100
            self.issues[number] = {"number": number, "state": "open", "user": {"login": watchdog.BOT},
                                   "labels": payload["labels"], "title": payload["title"], "body": payload["body"]}
            return Response(201, json.dumps(self.issues[number]).encode())
        parts = path.split("/")
        if len(parts) >= 3 and parts[1] == "issues":
            issue = self.issues[int(parts[2])]
            if method == "PATCH":
                issue.update(payload)
                return Response(200, json.dumps(issue).encode())
            if method == "POST" and parts[3:] == ["comments"]:
                self.comments.append((issue["number"], payload["body"]))
                return Response(201, b"{}")
        raise AssertionError(f"unexpected {method} {path}")


def report(outcome="mismatch", ref="main", version="2.4.1-beta.32", runs=((39891520683, "failure"),)):
    return {"schema": 1, "outcome": outcome, "ref": ref, "ref_sha": "9" * 40, "repository": watchdog.REPOSITORY,
            "skipped": [], "addons": [{"path": "hass_mcp_engineering_beta/config.yaml", "version": version,
                                       "outcome": outcome, "release_sha": "9" * 40, "reason": "image_tag_absent",
                                       "missing_arch": ["aarch64", "amd64"],
                                       "runs": [{"id": i, "attempt": 2, "event": "workflow_run",
                                                 "status": "completed", "conclusion": c} for i, c in runs]}]}


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.store = IssueStore()
        self.api = watchdog.IssueAPI(watchdog.REPOSITORY, API_TOKEN, watchdog.Reader(
            self.store, clock=lambda: 0.0, sleep=lambda s: None, jitter=lambda: 0.0, wall=lambda: 0.0))

    def alert(self, value, url="https://github.com/jeter-1/hass-mcp-admin/actions/runs/1"):
        return watchdog.alert(value, self.api, url)

    def open_issues(self):
        return [i for i in self.store.issues.values() if i["state"] == "open"]

    def test_mismatch_opens_one_labeled_issue_and_fails(self):
        self.assertEqual(self.alert(report()), (1, "opened"))
        (issue,) = self.open_issues()
        self.assertTrue(self.store.label)
        self.assertEqual(issue["labels"], [watchdog.LABEL])
        for expected in (watchdog.MARKER, "2.4.1-beta.32", "9" * 40, "aarch64,amd64",
                         "39891520683 attempt 2", "completed/failure", "publication_recovery.py --version 2.4.1-beta.32",
                         "PUBLICATION_RECOVERY_RUNBOOK.md", "actions/runs/1"):
            self.assertIn(expected, issue["body"])
        self.assertNotIn(API_TOKEN, issue["body"])

    def test_repeat_and_changed_reports_update_without_duplicating(self):
        self.alert(report())
        writes = len([r for r in self.store.requests if r[0] != "GET"])
        self.assertEqual(self.alert(report()), (1, "unchanged"))
        self.assertEqual(len([r for r in self.store.requests if r[0] != "GET"]), writes)
        self.assertEqual(self.alert(report("unknown")), (1, "updated"))
        (issue,) = self.open_issues()
        self.assertIn("`unknown`", issue["body"])
        self.assertEqual(len(self.store.comments), 1)
        self.assertEqual(self.alert(report("unknown")), (1, "unchanged"))
        self.assertEqual(len(self.store.issues), 1)

    def test_ok_for_main_closes_with_comment(self):
        self.alert(report())
        self.assertEqual(self.alert(report("ok")), (0, "closed"))
        self.assertEqual(self.open_issues(), [])
        (issue,) = self.store.issues.values()
        self.assertEqual(issue["state_reason"], "completed")
        self.assertIn("reports `ok`", self.store.comments[-1][1])
        self.assertEqual(self.alert(report("ok")), (0, "no_change"))
        self.assertEqual(self.alert(report()), (1, "opened"))
        self.assertEqual(len(self.store.issues), 2)

    def test_pending_and_non_main_ok_neither_raise_nor_clear(self):
        self.alert(report())
        for value in (report("pending"), report("ok", ref="91ab868d10924caeaa268e005d5cee5b318cab7c")):
            with self.subTest(outcome=value["outcome"]):
                self.assertEqual(self.alert(value), (0, "no_change"))
                self.assertEqual(len(self.open_issues()), 1)

    def test_only_bot_issues_with_marker_are_reused(self):
        self.store.label = True
        self.store.issues[1] = {"number": 1, "state": "open", "user": {"login": "someone"},
                                "labels": [watchdog.LABEL], "title": "x", "body": watchdog.MARKER}
        self.store.issues[2] = {"number": 2, "state": "open", "user": {"login": watchdog.BOT},
                                "labels": [watchdog.LABEL], "title": "x", "body": "no marker"}
        self.assertEqual(self.alert(report()), (1, "opened"))
        self.assertEqual(self.alert(report("ok")), (0, "closed"))
        self.assertEqual([i["number"] for i in self.open_issues()], [1, 2])

    def test_missing_or_malformed_report_is_unknown_and_alerts(self):
        for raw in (None, "", "{", "[]", json.dumps({"outcome": "fine"}), "x" * 200_001):
            with self.subTest(raw=str(raw)[:10]):
                value = watchdog.load_report(raw)
                self.assertEqual((value["outcome"], value["reason"]),
                                 ("unknown", "watchdog_check_produced_no_report"))
        self.assertEqual(self.alert(watchdog.load_report(None)), (1, "opened"))

    def test_writes_are_not_retried(self):
        self.store.label = True
        self.store.fail_writes = 502
        with self.assertRaises(watchdog.retry.GitHubReadError):
            self.alert(report())
        self.assertEqual(len([r for r in self.store.requests if r[0] == "POST"]), 1)
        self.assertEqual(self.store.issues, {})

    def test_cli_alert_exit_codes(self):
        calls = []

        class FakeAPI:
            def __init__(self, repository, token):
                calls.append((repository, token))

        original = watchdog.IssueAPI
        watchdog.IssueAPI = FakeAPI
        self.addCleanup(setattr, watchdog, "IssueAPI", original)
        results = iter([(1, "opened"), (0, "closed")])
        original_alert = watchdog.alert
        watchdog.alert = lambda value, api, url: next(results)
        self.addCleanup(setattr, watchdog, "alert", original_alert)
        env = {"GH_TOKEN": API_TOKEN, "WATCHDOG_REPORT": json.dumps(report())}
        self.assertEqual(watchdog.main(["alert"], env), 1)
        self.assertEqual(watchdog.main(["alert"], env), 0)
        self.assertEqual(calls, [(watchdog.REPOSITORY, API_TOKEN)] * 2)

    def test_run_url_is_bounded(self):
        good = {"GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": watchdog.REPOSITORY,
                "GITHUB_RUN_ID": "123"}
        self.assertEqual(watchdog.run_url(good), "https://github.com/jeter-1/hass-mcp-admin/actions/runs/123")
        for key, value in (("GITHUB_SERVER_URL", "https://evil.invalid"), ("GITHUB_RUN_ID", "1 2"),
                           ("GITHUB_REPOSITORY", "a/b/c")):
            self.assertIsNone(watchdog.run_url(dict(good, **{key: value})))


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load(WORKFLOW.read_text())
        cls.text = WORKFLOW.read_text()

    def test_triggers(self):
        triggers = self.workflow[True]
        self.assertEqual(triggers["schedule"], [{"cron": "23 * * * *"}])
        self.assertEqual(triggers["workflow_run"], {"workflows": ["Publish reviewed Engineering release"],
                                                    "types": ["completed"]})
        self.assertEqual(set(triggers["workflow_dispatch"]["inputs"]), {"ref"})
        publisher = yaml.safe_load((ROOT / ".github/workflows/publish-rc-image.yml").read_text())
        self.assertEqual(publisher["name"], "Publish reviewed Engineering release")
        self.assertEqual(Path(".github/workflows/publish-rc-image.yml").name, watchdog.PUBLISH_WORKFLOW)

    def test_minimum_permissions_and_no_write_authority_beyond_issues(self):
        self.assertEqual(self.workflow["permissions"], {})
        jobs = self.workflow["jobs"]
        self.assertEqual(jobs["check"]["permissions"], {"contents": "read", "actions": "read"})
        self.assertEqual(jobs["alert"]["permissions"], {"contents": "read", "issues": "write"})
        self.assertEqual(set(jobs), {"check", "alert"})
        for word in ("packages", "contents: write", "id-token", "secrets.", "docker login", "gh workflow run",
                     "gh run rerun", "git push", "git tag"):
            self.assertNotIn(word, self.text)

    def test_alert_always_runs_and_inputs_never_reach_shell_text(self):
        jobs = self.workflow["jobs"]
        self.assertEqual(jobs["alert"]["needs"], "check")
        self.assertEqual(jobs["alert"]["if"], "${{ always() }}")
        for job in jobs.values():
            self.assertEqual(job["runs-on"], "ubuntu-24.04")
            self.assertLessEqual(job["timeout-minutes"], 30)
            for step in job["steps"]:
                self.assertNotIn("${{", step.get("run", ""))
                if step.get("uses", "").startswith("actions/checkout@"):
                    self.assertFalse(step["with"]["persist-credentials"])
        self.assertEqual(self.workflow["concurrency"], {"group": "publication-watchdog",
                                                        "cancel-in-progress": False})
        self.assertEqual(jobs["check"]["steps"][0]["with"]["fetch-depth"], 0)
        self.assertIn("advertised_image_watchdog.py check", self.text)
        self.assertIn("python3 -I scripts/advertised_image_watchdog.py alert", self.text)


if __name__ == "__main__":
    unittest.main()
