"""HAMCP-223 read-only publication recovery advisor, from recorded states only.

No network, GitHub credentials or registry access: verdicts are computed from
recorded facts, and inspection runs against a disposable Git repository with a
fake opener that replays registry and API responses.
"""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


recovery = load("publication_recovery", ROOT / "scripts/publication_recovery.py")
helpers = load("hamcp223_watchdog_test_helpers", ROOT / "tests/test_advertised_image_watchdog.py")
Response, Opener, git_fault = helpers.Response, helpers.Opener, helpers.git_fault

REPO = "jeter-1/hass-mcp-admin"
API = f"https://api.github.com/repos/{REPO}"
IMAGE = "ghcr.io/jeter-1/hass-mcp-engineering-beta"
MANIFESTS = "https://ghcr.io/v2/jeter-1/hass-mcp-engineering-beta/manifests/"
# Recorded from the real beta.42 incident (successful owner dispatch 40597505590).
BETA42_SHA = "f8bbb47c9298370ab21042c166e22a6224178b64"
BETA42 = "2.4.1-beta.42"
BETA42_COMMAND = (f"gh workflow run publish-rc-image.yml --repo {REPO} --ref main "
                  f"-f release_sha={BETA42_SHA} -f expected_version={BETA42}")
# Recorded from beta.32 (run 39891520683): main later moved to beta.43.
BETA32_SHA = "91ab868d10924caeaa268e005d5cee5b318cab7c"
MAIN_SHA = "0e0624297d32608bd4dfda36d90879b3462334fa"
DIGEST = "sha256:" + "4" * 64
BUILD_TIME = "2026-10-20T08:15:02Z"


def facts(**overrides):
    """beta.42 just before its successful dispatch: nothing claimed, nothing published."""
    base = {"repository": REPO, "main_sha": BETA42_SHA, "remote_main_sha": BETA42_SHA,
            "version": BETA42, "release_candidates": [BETA42_SHA], "release_sha": BETA42_SHA,
            "main_version": BETA42, "engineering_drift": False, "staged": False,
            "registry_version": "absent", "registry_commit": "absent",
            "git_tag": "absent", "github_release": "absent", "live_runs": [], "claim": None}
    base.update(overrides)
    return base


def claimed(event="workflow_dispatch", schema=1, run_id=40597505590, attempt=1,
            status="completed", conclusion="failure", build="success", actor="jeter-1", **claim):
    record = {"schema": schema, "event": event, "release_sha": BETA42_SHA, "version": BETA42,
              "workflow_sha": BETA42_SHA, "run_id": run_id, "run_attempt": attempt, **claim}
    run = {"id": run_id, "attempt": attempt, "event": event, "status": status, "conclusion": conclusion,
           "head_sha": BETA42_SHA, "path": recovery.WORKFLOW_PATH, "actor": actor}
    return facts(claim=record, claim_run=run, claim_build=build)


class DispatchCommandTests(unittest.TestCase):
    def test_beta42_shape_prints_the_exact_successful_owner_dispatch(self):
        self.assertEqual(recovery.dispatch_command({"release_sha": BETA42_SHA, "expected_version": BETA42}),
                         BETA42_COMMAND)

    def test_beta42_malformed_dispatches_are_refused(self):
        # Runs 40596292661 and 40597314169 were dispatched with these inputs.
        for sha, version in (("f8bbb47c…", BETA42), ("f8bbb47c…", "2.4.1-beta.42;"),
                             (BETA42_SHA, "2.4.1-beta.42;"), (BETA42_SHA.upper(), BETA42),
                             (BETA42_SHA[:39], BETA42), (BETA42_SHA + "0", BETA42), (BETA42_SHA, ""),
                             (BETA42_SHA, "-2.4.1"), (BETA42_SHA, "2.4.1 beta"), (BETA42_SHA, "a" * 65),
                             (BETA42_SHA, BETA42 + "\n"), (None, BETA42)):
            with self.subTest(sha=sha, version=version), self.assertRaises(recovery.RecoveryError):
                recovery.dispatch_command({"release_sha": sha, "expected_version": version})

    def test_digest_recovery_inputs_follow_the_publisher_contract(self):
        inputs = {"release_sha": BETA42_SHA, "expected_version": BETA42, "recovery_run_id": "40597505590",
                  "recovery_run_attempt": "2", "recovery_source_digest": DIGEST,
                  "recovery_build_time": BUILD_TIME}
        self.assertEqual(recovery.dispatch_command(inputs), BETA42_COMMAND
                         + f" -f recovery_run_id=40597505590 -f recovery_run_attempt=2"
                           f" -f recovery_source_digest={DIGEST} -f recovery_build_time={BUILD_TIME}")
        without_attempt = {k: v for k, v in inputs.items() if k != "recovery_run_attempt"}
        self.assertNotIn("recovery_run_attempt", recovery.dispatch_command(without_attempt))
        for field, value in (("recovery_run_id", "0"), ("recovery_run_id", "1" * 21),
                             ("recovery_run_attempt", "0"), ("recovery_run_attempt", "101"),
                             ("recovery_run_attempt", "01"), ("recovery_source_digest", "sha256:" + "A" * 64),
                             ("recovery_source_digest", DIGEST[:-1]), ("recovery_build_time", "2026-10-20 08:15:02Z"),
                             ("recovery_build_time", "2026-13-20T08:15:02Z"),
                             ("recovery_build_time", "2026-02-30T08:15:02Z"), ("recovery_run_id", 40597505590)):
            with self.subTest(field=field, value=value), self.assertRaises(recovery.RecoveryError):
                recovery.dispatch_command(dict(inputs, **{field: value}))

    def test_recovery_fields_must_be_complete_and_known(self):
        base = {"release_sha": BETA42_SHA, "expected_version": BETA42}
        for partial in ({"recovery_run_id": "1"}, {"recovery_source_digest": DIGEST},
                        {"recovery_run_id": "1", "recovery_build_time": BUILD_TIME},
                        {"recovery_run_attempt": "1"}, {"unexpected": "1"}):
            with self.subTest(partial=partial), self.assertRaises(recovery.RecoveryError):
                recovery.dispatch_command(dict(base, **partial))
        for missing in ("release_sha", "expected_version"):
            with self.subTest(missing=missing), self.assertRaises(recovery.RecoveryError):
                recovery.dispatch_command({k: v for k, v in base.items() if k != missing})
        with self.assertRaises(recovery.RecoveryError):
            recovery.dispatch_command(base, repository="bad repository")


class VerdictTests(unittest.TestCase):
    def assertVerdict(self, state, expected, command=None, **kwargs):
        name, reasons, printed = recovery.verdict(state, **kwargs)
        self.assertEqual(name, expected, reasons)
        self.assertEqual(printed, command)
        self.assertTrue(reasons)
        return reasons

    def test_beta42_recoverable_state_prints_fresh_dispatch(self):
        self.assertVerdict(facts(), "dispatch_fresh", BETA42_COMMAND)
        # A later main commit without Engineering drift is still recoverable.
        self.assertVerdict(facts(main_sha=MAIN_SHA, remote_main_sha=MAIN_SHA), "dispatch_fresh", BETA42_COMMAND)

    def test_beta32_drift_prints_roll_forward_advice(self):
        reasons = self.assertVerdict(facts(version="2.4.1-beta.32", release_candidates=[BETA32_SHA],
                                           release_sha=BETA32_SHA, main_sha=MAIN_SHA, remote_main_sha=MAIN_SHA,
                                           main_version="2.4.1-beta.43", engineering_drift=True), "roll_forward")
        self.assertIn("N+1", " ".join(reasons))
        # Drift is enough even when main happens to advertise the same version.
        self.assertVerdict(facts(engineering_drift=True), "roll_forward")
        self.assertVerdict(facts(main_version="2.4.1-beta.43"), "roll_forward")

    def test_fully_published_needs_nothing_but_a_store_refresh(self):
        reasons = self.assertVerdict(facts(registry_version="present", registry_commit="present",
                                           git_tag="present", github_release="present",
                                           engineering_drift=True), "published")
        self.assertIn("Check for updates", " ".join(reasons))

    def test_live_run_waits_before_anything_else(self):
        live = [{"id": 1, "status": "in_progress"}]
        self.assertVerdict(facts(live_runs=live), "wait")
        self.assertVerdict(facts(live_runs=live, engineering_drift=True, git_tag="present"), "wait")

    def test_partial_publication_is_a_reconciliation_stop(self):
        for key, value in (("registry_version", "present"), ("registry_version", "partial"),
                           ("registry_commit", "present"), ("git_tag", "present"), ("github_release", "present")):
            with self.subTest(key=key, value=value):
                self.assertVerdict(facts(**{key: value}), "reconcile")

    def test_unknown_or_stale_inputs_stop(self):
        for state in (facts(remote_main_sha="unknown"), facts(remote_main_sha=MAIN_SHA),
                      facts(release_sha=None, release_candidates=[]),
                      facts(release_sha=None, release_candidates=[BETA42_SHA, BETA32_SHA]),
                      facts(live_runs="unknown"), facts(git_tag="unknown"), facts(registry_commit="unknown"),
                      facts(staged=True), facts(claim="unknown"), facts(run="unknown"),
                      facts(run={"id": 7, "path": ".github/workflows/ci.yml"})):
            with self.subTest(state={k: v for k, v in state.items() if facts().get(k) != v}):
                self.assertVerdict(state, "stop")

    def test_claim_with_recoverable_digest_asks_for_digest_then_prints_it(self):
        reasons = self.assertVerdict(claimed(), "needs_digest")
        self.assertIn("OCI digest:", " ".join(reasons))
        self.assertVerdict(claimed(), "needs_digest", digest=DIGEST)
        command = (BETA42_COMMAND + f" -f recovery_run_id=40597505590 -f recovery_run_attempt=1"
                   f" -f recovery_source_digest={DIGEST} -f recovery_build_time={BUILD_TIME}")
        self.assertVerdict(dict(claimed(), digest="present"), "dispatch_digest", command,
                           digest=DIGEST, build_time=BUILD_TIME)
        handoff = claimed(event="workflow_run", schema=2, run_id=39891520683, attempt=2)
        self.assertVerdict(dict(handoff, digest="present"), "dispatch_digest",
                           command.replace("40597505590 -f recovery_run_attempt=1",
                                           "39891520683 -f recovery_run_attempt=2"),
                           digest=DIGEST, build_time=BUILD_TIME)

    def test_supplied_digest_must_be_valid_and_present(self):
        for digest_state in ("absent", "partial", "unknown", None):
            with self.subTest(digest_state=digest_state):
                self.assertVerdict(dict(claimed(), digest=digest_state), "stop", digest=DIGEST, build_time=BUILD_TIME)
        with self.assertRaises(recovery.RecoveryError):
            recovery.verdict(dict(claimed(), digest="present"), DIGEST, "2026-10-20")

    def test_consumed_claim_without_recoverable_digest_rolls_forward(self):
        for kwargs in ({"build": "failure"}, {"build": "cancelled"}, {"build": "skipped"},
                       {"build": "not_started"}, {"conclusion": "cancelled"}, {"conclusion": "success"},
                       {"status": "in_progress", "conclusion": "none"}, {"actor": "someone-else"}):
            with self.subTest(**kwargs):
                self.assertVerdict(claimed(**kwargs), "roll_forward", digest=DIGEST, build_time=BUILD_TIME)

    def test_inconsistent_or_unreadable_claim_is_never_recovered(self):
        for state in (claimed(release_sha=BETA32_SHA), claimed(version="2.4.1-beta.41"), claimed(schema=3),
                      claimed(event="workflow_run", schema=1), claimed(event="workflow_dispatch", schema=2)):
            with self.subTest(claim=state["claim"]):
                self.assertVerdict(state, "reconcile", digest=DIGEST, build_time=BUILD_TIME)
        for override in ({"claim_run": "unknown"}, {"claim_run": None}, {"claim_build": "unknown"},
                         {"claim_build": None}):
            with self.subTest(override=override):
                self.assertVerdict(dict(claimed(), **override), "stop")
        wrong_path = claimed()
        wrong_path["claim_run"] = dict(wrong_path["claim_run"], path=".github/workflows/ci.yml")
        self.assertVerdict(wrong_path, "roll_forward")

    def test_every_verdict_renders_with_its_failure_mode(self):
        for name in recovery.FAILURE_MODES:
            lines = recovery.render(claimed(), (name, ["reason"], "command" if name.startswith("dispatch") else None))
            self.assertTrue(lines[0].startswith(f"Recovery verdict: {name} "))
            self.assertIn("- reason", lines)
            self.assertEqual("command" in lines, name.startswith("dispatch"))


class InspectionFixture(unittest.TestCase):
    """End to end over a synthetic first-parent history and recorded remote state."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Synthetic test")
        self.git("config", "user.email", "synthetic@example.invalid")
        self.config = {"name": "Engineering", "version": "1.0.0", "slug": "engineering",
                       "description": "Synthetic", "arch": ["amd64", "aarch64"], "image": IMAGE}
        self.commit("0.9.0")
        self.release = self.commit("1.0.0")
        (self.root / "README.md").write_text("docs only\n")
        self.git("add", ".")
        self.git("commit", "-qm", "Docs only")
        self.head = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", self.head)
        self.opener = Opener()
        self.reader = recovery.watchdog.Reader(self.opener, clock=lambda: 0.0, sleep=lambda s: None,
                                               jitter=lambda: 0.0, wall=lambda: 0.0)
        self.route("/git/ref/heads/main", {"object": {"sha": self.head}})
        self.opener.add("GET", "https://ghcr.io/token", Response(200, b'{"token":"synthetic"}'))
        for tag in ("1.0.0", "sha-" + self.release):
            self.opener.add("GET", MANIFESTS + tag, Response(404))
        for route in ("/git/ref/tags/v1.0.0", "/releases/tags/v1.0.0",
                      "/git/ref/tags/engineering-publication-attempt/v1.0.0"):
            self.opener.add("GET", API + route, Response(404))
        self.route("/actions/workflows/publish-rc-image.yml/runs", {"total_count": 0, "workflow_runs": []})

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args],
                                       stderr=subprocess.DEVNULL, text=True).strip()

    def commit(self, version, extra=None):
        path = self.root / recovery.CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({**self.config, "version": version}))
        (self.root / "repository.yaml").write_text("name: Synthetic\n")
        for name, content in (extra or {}).items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(content)
        self.git("add", ".")
        self.git("commit", "-qm", f"Release {version}")
        return self.git("rev-parse", "HEAD")

    def route(self, route, document, method="GET"):
        self.opener.add(method, API + route, Response(200, json.dumps(document).encode()))

    def inspect(self, **kwargs):
        state = recovery.inspect(self.root, REPO, self.reader, **kwargs)
        return state, recovery.verdict(state, kwargs.get("digest"))


class InspectionTests(InspectionFixture):
    def test_unpublished_release_without_claim_prints_fresh_dispatch(self):
        state, (name, _, command) = self.inspect(version="1.0.0")
        self.assertEqual(name, "dispatch_fresh")
        self.assertEqual(state["release_sha"], self.release)
        self.assertEqual(command, f"gh workflow run publish-rc-image.yml --repo {REPO} --ref main "
                                  f"-f release_sha={self.release} -f expected_version=1.0.0")
        self.assertTrue(all(method == "GET" for method, *_ in self.opener.requests))
        self.assertTrue(all("Authorization" not in headers or "ghcr.io" in url
                            for _, url, headers, _ in self.opener.requests))

    def test_run_id_derives_version_and_rejects_conflicts(self):
        self.route("/actions/runs/123", {"id": 123, "run_attempt": 2, "event": "workflow_run",
                                         "status": "completed", "conclusion": "failure", "head_sha": self.release,
                                         "path": recovery.WORKFLOW_PATH, "triggering_actor": {"login": "jeter-1"}})
        state, (name, _, _) = self.inspect(run_id="123")
        self.assertEqual((state["version"], name), ("1.0.0", "dispatch_fresh"))
        with self.assertRaises(recovery.RecoveryError):
            recovery.inspect(self.root, REPO, self.reader, run_id="123", version="0.9.0")
        for bad in ("0", "abc", "1" * 21):
            with self.assertRaises(recovery.RecoveryError):
                recovery.inspect(self.root, REPO, self.reader, run_id=bad)

    def test_engineering_drift_rolls_forward(self):
        self.git("update-ref", "refs/remotes/origin/main",
                 self.commit("1.0.0", {"hass_mcp_engineering_beta/app.py": "changed\n"}))
        self.opener.routes[("GET", API + "/git/ref/heads/main")] = [
            Response(200, json.dumps({"object": {"sha": self.git("rev-parse", "HEAD")}}).encode())]
        state, (name, _, _) = self.inspect(version="1.0.0")
        self.assertEqual((state["engineering_drift"], name), (True, "roll_forward"))

    def test_later_config_edit_at_same_version_is_not_another_release(self):
        self.config["description"] = "Edited after release"
        self.git("update-ref", "refs/remotes/origin/main", self.commit("1.0.0"))
        self.opener.routes[("GET", API + "/git/ref/heads/main")] = [
            Response(200, json.dumps({"object": {"sha": self.git("rev-parse", "HEAD")}}).encode())]
        state, (name, _, _) = self.inspect(version="1.0.0")
        self.assertEqual((state["release_candidates"], state["release_sha"]), ([self.release], self.release))
        self.assertEqual(name, "roll_forward")

    def test_over_bound_config_in_history_stops_instead_of_misattributing(self):
        padding = "# padding\n" * (recovery.watchdog.BLOB_BOUND // 10 + 1)
        path = self.root / recovery.CONFIG_PATH
        path.write_text(path.read_text() + padding)
        self.git("commit", "-qam", "Over-bound at the same version")
        self.config["description"] = "Trimmed"
        self.git("update-ref", "refs/remotes/origin/main", self.commit("1.0.0"))
        with self.assertRaisesRegex(recovery.watchdog.WatchdogError, "config_oversized"):
            recovery.inspect(self.root, REPO, self.reader, version="1.0.0")
        original = recovery.watchdog.Reader
        recovery.watchdog.Reader = lambda: self.reader
        self.addCleanup(setattr, recovery.watchdog, "Reader", original)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(recovery.main(["--repo-root", str(self.root), "--version", "1.0.0"], {}), 2)
        self.assertIn("config_oversized", err.getvalue())
        self.assertTrue(all(method == "GET" for method, *_ in self.opener.requests))

    def test_incomplete_version_index_is_partial_not_absent(self):
        index, children = helpers.index(("amd64", None))
        self.opener.routes[("GET", MANIFESTS + "1.0.0")] = [Response(200, index, helpers.INDEX_TYPE)]
        for digest, body in children.items():
            self.opener.add("GET", MANIFESTS + digest, Response(200, body, helpers.MANIFEST_TYPE))
        state, (name, _, command) = self.inspect(version="1.0.0")
        self.assertEqual((state["registry_version"], name, command), ("partial", "reconcile", None))

    def test_staged_declaration_and_unknown_version_stop(self):
        state, (name, _, _) = self.inspect(version="2.0.0")
        self.assertEqual((state["release_candidates"], name), ([], "stop"))
        self.git("update-ref", "refs/remotes/origin/main", self.commit("1.0.0", {".release/next-version": "x\n"}))
        self.opener.routes[("GET", API + "/git/ref/heads/main")] = [
            Response(200, json.dumps({"object": {"sha": self.git("rev-parse", "HEAD")}}).encode())]
        state, (name, _, _) = self.inspect(version="1.0.0")
        self.assertEqual((state["staged"], name), (True, "stop"))
        with self.assertRaises(recovery.RecoveryError):
            recovery.inspect(self.root, REPO, self.reader, version="1.0.0;")

    def test_claim_and_build_job_are_read_from_the_claimed_attempt(self):
        claim = {"schema": 1, "event": "workflow_dispatch", "release_sha": self.release, "version": "1.0.0",
                 "workflow_sha": self.head, "run_id": 77, "run_attempt": 2}
        prefix = "/git/ref/tags/engineering-publication-attempt/v1.0.0"
        self.opener.routes[("GET", API + prefix)] = [
            Response(200, json.dumps({"object": {"type": "tag", "sha": "c" * 40}}).encode())]
        self.route("/git/tags/" + "c" * 40, {"message": json.dumps(claim)})
        self.route("/actions/runs/77/attempts/2", {"id": 77, "run_attempt": 2, "event": "workflow_dispatch",
                                                   "status": "completed", "conclusion": "failure",
                                                   "head_sha": self.head, "path": recovery.WORKFLOW_PATH,
                                                   "triggering_actor": {"login": "jeter-1"}})
        self.route("/actions/runs/77/attempts/2/jobs", {"total_count": 1, "jobs": [
            {"name": recovery.BUILD_JOB, "conclusion": "success"}]})
        state, (name, _, _) = self.inspect(version="1.0.0")
        self.assertEqual((state["claim_build"], name), ("success", "needs_digest"))
        index, children = helpers.index(("amd64", None), ("arm64", "v8"))
        self.opener.add("GET", MANIFESTS + DIGEST, Response(200, index, helpers.INDEX_TYPE))
        for digest, body in children.items():
            self.opener.add("GET", MANIFESTS + digest, Response(200, body, helpers.MANIFEST_TYPE))
        state = recovery.inspect(self.root, REPO, self.reader, version="1.0.0", digest=DIGEST)
        name, _, command = recovery.verdict(state, DIGEST, BUILD_TIME)
        self.assertEqual(name, "dispatch_digest")
        self.assertTrue(command.endswith(f"-f recovery_run_id=77 -f recovery_run_attempt=2 "
                                         f"-f recovery_source_digest={DIGEST} -f recovery_build_time={BUILD_TIME}"))
        self.route("/actions/runs/77/attempts/2/jobs", {"total_count": 2, "jobs": [{"name": recovery.BUILD_JOB}] * 2})
        self.opener.routes[("GET", API + "/actions/runs/77/attempts/2/jobs")].pop(0)
        self.assertEqual(recovery.inspect(self.root, REPO, self.reader, version="1.0.0")["claim_build"], "unknown")

    def test_unreadable_claim_and_remote_errors_are_unknown(self):
        prefix = "/git/ref/tags/engineering-publication-attempt/v1.0.0"
        for document in ({"object": {"type": "commit", "sha": "c" * 40}}, {"object": {"type": "tag", "sha": "x"}}):
            self.opener.routes[("GET", API + prefix)] = [Response(200, json.dumps(document).encode())]
            with self.subTest(document=document):
                self.assertEqual(recovery.inspect(self.root, REPO, self.reader, version="1.0.0")["claim"], "unknown")
        self.opener.routes[("GET", API + prefix)] = [Response(200, b"[]")]
        with self.assertRaisesRegex(recovery.RecoveryError, "github_response_malformed:claim_ref"):
            recovery.inspect(self.root, REPO, self.reader, version="1.0.0")
        self.opener.routes[("GET", API + prefix)] = [Response(200, json.dumps(
            {"object": {"type": "tag", "sha": "c" * 40}}).encode())]
        self.route("/git/tags/" + "c" * 40, {"message": "not json"})
        self.assertEqual(recovery.inspect(self.root, REPO, self.reader, version="1.0.0")["claim"], "unknown")
        self.opener.routes[("GET", API + "/releases/tags/v1.0.0")] = [Response(403)]
        self.opener.routes[("GET", API + "/git/ref/heads/main")] = [Response(403)]
        state = recovery.inspect(self.root, REPO, self.reader, version="1.0.0")
        self.assertEqual((state["github_release"], state["remote_main_sha"]), ("unknown", "unknown"))
        self.assertEqual(recovery.verdict(state)[0], "stop")

    def test_live_publish_run_waits(self):
        self.opener.routes[("GET", API + "/actions/workflows/publish-rc-image.yml/runs")] = [Response(200, json.dumps(
            {"total_count": 1, "workflow_runs": [helpers.run(5, "queued", None, sha=self.head)]}).encode())]
        self.assertEqual(self.inspect(version="1.0.0")[1][0], "wait")

    def test_command_line_is_read_only_and_bounded(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(recovery.main(["--repo-root", str(self.root)], {}), 2)
            self.assertEqual(recovery.main(["--repo-root", str(self.root), "--version", "1.0.0",
                                            "--repository", "bad"], {}), 2)
        self.assertIn("run_id_or_version_required", err.getvalue())
        original = recovery.watchdog.Reader
        recovery.watchdog.Reader = lambda: self.reader
        self.addCleanup(setattr, recovery.watchdog, "Reader", original)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(recovery.main(["--repo-root", str(self.root), "--version", "1.0.0", "--json"], {}), 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report["verdict"], "dispatch_fresh")
        self.assertIn(f"release_sha={self.release}", report["command"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(recovery.main(["--repo-root", str(self.root), "--version", "1.0.0"], {}), 0)
        self.assertIn("Owner command (Josh only; this helper never runs it):", out.getvalue())

    def test_source_has_no_write_dispatch_or_rerun_path(self):
        source = (ROOT / "scripts/publication_recovery.py").read_text()
        for forbidden in ('"POST"', '"PATCH"', '"PUT"', '"DELETE"', "/dispatches", "/rerun", "subprocess.",
                          "retried=False", "method="):
            self.assertNotIn(forbidden, source)



class SharedReaderTests(unittest.TestCase):
    """SV-M1: only an allowed HTTP 404 is absence; a 2xx body must be a schema-valid object."""

    def api(self, *responses):
        opener = Opener()
        opener.add("GET", API + "/x", *responses)
        reader = recovery.watchdog.Reader(opener, clock=lambda: 0.0, sleep=lambda s: None,
                                          jitter=lambda: 0.0, wall=lambda: 0.0)
        return recovery.GitHub(REPO, reader)

    def test_only_an_allowed_404_is_absence(self):
        self.assertIs(self.api(Response(404)).read("/x", "tag_ref", recovery.TAG_REF, absent_ok=True),
                      recovery.ABSENT)
        self.assertEqual(self.api(Response(404)).presence("/x", "tag_ref", recovery.TAG_REF), "absent")
        # A 404 where absence is not allowed, and every other failure, is unknown.
        self.assertEqual(self.api(Response(404)).read("/x", "tag_ref", recovery.TAG_REF), "unknown")
        for status in (301, 302, 403, 410, 422, 500):
            with self.subTest(status=status):
                self.assertEqual(self.api(*[Response(status)] * 10).presence("/x", "tag_ref", recovery.TAG_REF),
                                 "unknown")
        self.assertEqual(self.api(Response(200, b"not json")).presence("/x", "tag_ref", recovery.TAG_REF),
                         "unknown")

    def test_a_2xx_body_is_never_the_absence_sentinel(self):
        schemas = {"main_ref": recovery.MAIN_REF, "tag_ref": recovery.TAG_REF, "release": recovery.RELEASE,
                   "claim_ref": recovery.TAG_REF, "claim_tag": recovery.GIT_TAG, "run": recovery.RUN,
                   "claim_run": recovery.RUN, "claim_jobs": recovery.JOBS}
        self.assertEqual(set(schemas), set(SHAPE_FAILURES))
        for label, schema in schemas.items():
            for body in (*NON_OBJECTS, *SHAPE_FAILURES[label]):
                for status in (200, 201):
                    with self.subTest(label=label, body=body, status=status), self.assertRaisesRegex(
                            recovery.RecoveryError, f"^github_response_malformed:{label}$"):
                        self.api(Response(status, json.dumps(body).encode())).read(
                            "/x", label, schema, absent_ok=True)

    def test_valid_objects_are_returned_and_present(self):
        documents = {"tag_ref": (recovery.TAG_REF, TAG_DOC),
                     "release": (recovery.RELEASE, {"tag_name": "v1.0.0", "id": 1}),
                     "main_ref": (recovery.MAIN_REF, {"object": {"sha": "a" * 40}}),
                     "claim_tag": (recovery.GIT_TAG, {"message": "{}"}),
                     "run": (recovery.RUN, dict(RUN_DOC, conclusion=None)),
                     "claim_jobs": (recovery.JOBS, {"total_count": 0, "jobs": []})}
        for label, (schema, document) in documents.items():
            with self.subTest(label=label):
                body = json.dumps(document).encode()
                self.assertEqual(self.api(Response(200, body)).read("/x", label, schema), document)
                self.assertEqual(self.api(Response(200, body)).presence("/x", label, schema), "present")


    def test_run_summary_refuses_unreadable_runs_on_its_own(self):
        # Defense in depth behind the shared reader: run_summary never trusts its input's shape.
        self.assertEqual(recovery.run_summary(RUN_DOC)["actor"], "jeter-1")
        for run in ("unknown", None, [], dict(RUN_DOC, id="77"), dict(RUN_DOC, triggering_actor=None),
                    {k: v for k, v in RUN_DOC.items() if k != "triggering_actor"}):
            with self.subTest(run=run):
                self.assertEqual(recovery.run_summary(run), "unknown")


class ObjectEndpointControlTests(InspectionFixture):
    """Ordinary object responses keep the published and partial outcomes."""

    def publish_registry(self):
        index, children = helpers.index(("amd64", None), ("arm64", "v8"))
        for tag in ("1.0.0", "sha-" + self.release):
            self.opener.routes[("GET", MANIFESTS + tag)] = [Response(200, index, helpers.INDEX_TYPE)]
        for digest, body in children.items():
            self.opener.add("GET", MANIFESTS + digest, Response(200, body, helpers.MANIFEST_TYPE))

    def test_valid_tag_and_release_objects_are_published(self):
        self.publish_registry()
        self.opener.routes[("GET", API + "/git/ref/tags/v1.0.0")] = [Response(200, json.dumps(
            {"ref": "refs/tags/v1.0.0", "object": {"type": "tag", "sha": "c" * 40}}).encode())]
        self.opener.routes[("GET", API + "/releases/tags/v1.0.0")] = [Response(200, json.dumps(
            {"tag_name": "v1.0.0", "id": 1}).encode())]
        state, (name, _, command) = self.inspect(version="1.0.0")
        self.assertEqual((state["git_tag"], state["github_release"], name, command),
                         ("present", "present", "published", None))

    def test_a_valid_tag_object_alone_is_partial(self):
        self.opener.routes[("GET", API + "/git/ref/tags/v1.0.0")] = [Response(200, json.dumps(
            {"ref": "refs/tags/v1.0.0", "object": {"type": "commit", "sha": self.release}}).encode())]
        state, (name, _, command) = self.inspect(version="1.0.0")
        self.assertEqual((state["git_tag"], state["github_release"], name, command),
                         ("present", "absent", "reconcile", None))


# HAMCP-223 R2 failure-class sweep. Each ID is one row of the handoff table
# "Failure paths: publication recovery helper"; every row has a case. Every
# failure ends in exit 2 with a fixed category, or in a stop verdict, and never
# prints dispatch inputs derived from incomplete data.
HELPER_FAILURE_ROWS = {
    "H1": "historical config blob read fails -> exit 2 git_unavailable, no command",
    "H2": "historical valid config over the Git output bound -> exit 2 git_output_bound, no command",
    "H3": "config tree-entry read fails -> exit 2",
    "H4": "config or declaration path proven absent -> None / absent (unchanged)",
    "H5": "non-regular config entry -> exit 2 config_not_regular_file",
    "H6": "staged-declaration read fails -> exit 2, never 'not staged'",
    "H7": "staged declaration present stops; absent permits fresh (unchanged)",
    "H8": "config non-syntax parse failure -> exit 2 config_unprocessable",
    "H9": "over-bound config -> exit 2 config_oversized (unchanged)",
    "H10": "syntax-invalid config has no version (unchanged)",
    "H11": "Engineering-tree diff fails -> exit 2, never drift or no drift",
    "H12": "release walk cut by its bound -> exit 2 release_walk_truncated",
    "H13": "first-parent walk or parent read fails -> exit 2 (unchanged)",
    "H14": "named run's head unreadable or not local -> exit 2 run_head_*",
    "H15": "named run with unrecognized field values -> run unknown -> stop (wrong types: H23)",
    "H16": "claim build-job listing incomplete or with malformed entries -> build unknown -> stop "
           "(wrong top-level shape: H23)",
    "H17": "claim build conclusion unreadable -> build unknown -> stop",
    "H18": "publish-run status unrecognized -> live runs unknown -> stop",
    "H19": "registry manifest listing malformed -> unknown -> stop, never partial/absent",
    "H20": "GitHub read failures -> unknown -> stop (unchanged)",
    "H21": "local main unresolvable or unreadable -> exit 2",
    "H22": "unexpected exception -> exit 2 unexpected_<type>, no command",
    "H23": "2xx GitHub body that is not an object with the read keys and types (null, list, scalar, {}, "
           "missing or wrong-typed key) -> exit 2 github_response_malformed:<endpoint>, never absence",
}
# Each object endpoint the helper reads: label, route, a valid document, and
# missing-key / wrong-type variants of it.
CLAIM_REF = "/git/ref/tags/engineering-publication-attempt/v1.0.0"
TAG_DOC = {"ref": "refs/tags/v1.0.0", "object": {"type": "tag", "sha": "c" * 40}}
RUN_DOC = {"id": 77, "run_attempt": 2, "event": "workflow_dispatch", "status": "completed", "conclusion": "failure",
           "head_sha": "d" * 40, "path": ".github/workflows/publish-rc-image.yml",
           "triggering_actor": {"login": "jeter-1"}}
SHAPE_FAILURES = {
    "main_ref": [{"object": {}}, {"object": {"sha": 7}}, {"object": "x"}, {"ref": "refs/heads/main"}],
    "tag_ref": [{"object": {"sha": "c" * 40}}, {"object": {"type": None, "sha": "c" * 40}},
                {"object": {"type": "tag", "sha": ["c" * 40]}}, {"object": None}, {"ref": "refs/tags/v1.0.0"}],
    "release": [{"name": "v1.0.0"}, {"tag_name": 1}, {"tag_name": None}, {"tag_name": ["v1.0.0"]}],
    "claim_ref": [{"object": {"type": "tag"}}, {"object": {"type": 1, "sha": "c" * 40}}, {"object": []}],
    "claim_tag": [{"tag": "x"}, {"message": None}, {"message": 7}, {"message": {"schema": 1}}],
    "run": [{k: v for k, v in RUN_DOC.items() if k != "path"}, dict(RUN_DOC, run_attempt="2"),
            dict(RUN_DOC, id=True), dict(RUN_DOC, conclusion=0), dict(RUN_DOC, triggering_actor={})],
    "claim_run": [{k: v for k, v in RUN_DOC.items() if k != "head_sha"}, dict(RUN_DOC, status=None),
                  dict(RUN_DOC, id=77.0), dict(RUN_DOC, triggering_actor={"login": None})],
    "claim_jobs": [{"jobs": []}, {"total_count": True, "jobs": []}, {"total_count": 0, "jobs": None}],
}
# Bodies that are never an object with keys: every endpoint refuses each one.
NON_OBJECTS = (None, [], [TAG_DOC], "present", "", 0, 1, 1.5, True, {})
COMMAND = "gh workflow run"


class HelperFailureClassTests(InspectionFixture):
    """Only proven absence is None; every read or processing failure is exit 2 or a stop."""

    def cli(self, *args):
        original = recovery.watchdog.Reader
        recovery.watchdog.Reader = lambda: self.reader
        self.addCleanup(setattr, recovery.watchdog, "Reader", original)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = recovery.main(["--repo-root", str(self.root), *(args or ("--version", "1.0.0"))], {})
        return code, out.getvalue(), err.getvalue()

    def assertExit2(self, category, *args):
        code, out, err = self.cli(*args)
        self.assertEqual(code, 2, out + err)
        self.assertEqual(err.strip(), f"Recovery inspection failed: {category}")
        self.assertNotIn(COMMAND, out + err)
        self.assertNotIn("release_sha=", out + err)
        self.assertTrue(all(method == "GET" for method, *_ in self.opener.requests))

    def assertFreshControl(self, *args):
        """Without the fault, the same history names the release and prints its command."""
        code, out, _ = self.cli(*args)
        self.assertEqual(code, 0)
        self.assertIn(COMMAND, out)

    def assertStop(self, **kwargs):
        state, (name, _, command) = self.inspect(**kwargs)
        self.assertEqual((name, command), ("stop", None))
        return state

    def advance(self, sha=None):
        sha = sha or self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", sha)
        self.opener.routes[("GET", API + "/git/ref/heads/main")] = [
            Response(200, json.dumps({"object": {"sha": sha}}).encode())]
        return sha

    def config_oid(self, commit):
        return self.git("rev-parse", f"{commit}:{recovery.CONFIG_PATH}")

    def claim(self, jobs, run=None):
        claim = {"schema": 1, "event": "workflow_dispatch", "release_sha": self.release, "version": "1.0.0",
                 "workflow_sha": self.head, "run_id": 77, "run_attempt": 2}
        self.opener.routes[("GET", API + "/git/ref/tags/engineering-publication-attempt/v1.0.0")] = [
            Response(200, json.dumps({"object": {"type": "tag", "sha": "c" * 40}}).encode())]
        self.route("/git/tags/" + "c" * 40, {"message": json.dumps(claim)})
        self.route("/actions/runs/77/attempts/2", run or {
            "id": 77, "run_attempt": 2, "event": "workflow_dispatch", "status": "completed",
            "conclusion": "failure", "head_sha": self.head, "path": recovery.WORKFLOW_PATH,
            "triggering_actor": {"login": "jeter-1"}})
        self.opener.routes[("GET", API + "/actions/runs/77/attempts/2/jobs")] = [
            Response(200, json.dumps(jobs).encode())]

    # --- Git facts ---------------------------------------------------------------

    def row_H1(self):
        self.assertFreshControl()
        older = self.config_oid(self.release + "^")
        for outcome in (128, subprocess.TimeoutExpired("git", 60), OSError("synthetic")):
            with self.subTest(outcome=type(outcome).__name__), git_fault(
                    recovery.watchdog, lambda args: args == ("cat-file", "blob", older), outcome):
                self.assertExit2("git_unavailable")

    def row_H2(self):
        # Codex's construction: a valid 50,000,151-byte 1.1.0 config, then only its comment trimmed.
        path = self.root / recovery.CONFIG_PATH
        body = yaml.safe_dump({**self.config, "version": "1.1.0"})
        comment = "# " + "x" * 97 + "\n"
        padding = 50_000_151 - len(body)
        path.write_text(body + comment * (padding // len(comment)) + "#" * (padding % len(comment) - 1) + "\n")
        self.assertEqual(path.stat().st_size, 50_000_151)
        self.git("add", ".")
        self.git("commit", "-qm", "1.1.0 with a long comment")
        path.write_text(body)
        self.git("commit", "-qam", "1.1.0, comment trimmed")
        self.advance()
        self.assertExit2("git_output_bound", "--version", "1.1.0")
        self.assertEqual(recovery.version_at(self.root, "HEAD"), "1.1.0")

    def row_H3(self):
        release = self.release
        with git_fault(recovery.watchdog, lambda args: "ls-tree" in args and recovery.CONFIG_PATH in args
                       and release in args, 128):
            self.assertExit2("git_unavailable")

    def row_H4(self):
        self.assertIsNone(recovery.blob(self.root, self.release, "absent/config.yaml"))
        self.assertIsNone(recovery.blob(self.root, self.release, recovery.STAGED_DECLARATION))
        self.assertFalse(recovery.present(self.root, self.release, recovery.STAGED_DECLARATION))
        self.git("rm", "-q", recovery.CONFIG_PATH)
        self.git("commit", "-qm", "Config removed")
        self.assertIsNone(recovery.config_at(self.root, "HEAD"))
        self.assertIsNone(recovery.version_at(self.root, "HEAD"))
        self.git("reset", "-q", "--hard", self.head)
        self.assertFreshControl()

    def row_H5(self):
        path = self.root / recovery.CONFIG_PATH
        real = path.with_name("real.yaml")
        real.write_text(path.read_text())
        path.unlink()
        path.symlink_to("real.yaml")
        self.git("add", ".")
        self.git("commit", "-qm", "Symlinked config")
        self.advance()
        self.assertExit2("config_not_regular_file")

    def row_H6(self):
        for outcome in (128, b"garbage-without-a-tab\0"):
            with self.subTest(outcome=outcome), git_fault(
                    recovery.watchdog, lambda args: "ls-tree" in args and recovery.STAGED_DECLARATION in args,
                    outcome):
                self.assertExit2("git_unavailable" if outcome == 128 else "git_output_unexpected")

    def row_H7(self):
        self.assertFreshControl()
        for extra in ({".release/next-version": "x\n"}, {".release/next-version/inner": "x\n"}):
            with self.subTest(extra=extra):
                self.git("reset", "-q", "--hard", self.head)
                self.git("clean", "-qfdx")
                self.advance(self.commit("1.0.0", extra))
                self.assertTrue(self.assertStop(version="1.0.0")["staged"])

    def row_H8(self):
        path = self.root / recovery.CONFIG_PATH
        path.write_text(path.read_text() + "build_date: 2026-13-45\n")
        self.git("commit", "-qam", "Unprocessable at the same version")
        self.advance()
        self.assertExit2("config_unprocessable")
        self.git("reset", "-q", "--hard", self.head)
        self.advance(self.head)
        original = yaml.safe_load

        def parse(raw, *args, **kwargs):
            if b"0.9.0" in raw:
                raise MemoryError
            return original(raw, *args, **kwargs)
        with mock.patch.object(yaml, "safe_load", parse):
            self.assertExit2("config_unprocessable")

    def row_H9(self):
        path = self.root / recovery.CONFIG_PATH
        path.write_text(path.read_text() + "# padding\n" * (recovery.watchdog.BLOB_BOUND // 10 + 1))
        self.git("commit", "-qam", "Over-bound at the same version")
        self.config["description"] = "Trimmed"
        self.advance(self.commit("1.0.0"))
        self.assertExit2("config_oversized")

    def row_H10(self):
        path = self.root / recovery.CONFIG_PATH
        path.write_text("version: [unterminated\n")
        self.git("commit", "-qam", "Store-invalid")
        self.assertIsNone(recovery.config_at(self.root, "HEAD"))
        self.assertIsNone(recovery.version_at(self.root, "HEAD"))
        self.assertEqual(recovery.release_commits(self.root, self.git("rev-parse", "HEAD"), "1.0.0"),
                         [self.release])

    def row_H11(self):
        self.assertFreshControl()
        for outcome in (128, subprocess.TimeoutExpired("git", 60)):
            with self.subTest(outcome=outcome), git_fault(
                    recovery.watchdog, lambda args: args[:2] == ("diff", "--quiet"), outcome):
                self.assertExit2("git_unavailable")

    def row_H12(self):
        with self.assertRaisesRegex(recovery.RecoveryError, "release_walk_truncated"):
            recovery.release_commits(self.root, self.head, "1.0.0", limit=1)
        self.assertEqual(recovery.release_commits(self.root, self.head, "1.0.0", limit=2), [self.release])
        with mock.patch.object(recovery.release_commits, "__defaults__", (1,)):
            self.assertExit2("release_walk_truncated")

    def row_H13(self):
        for prefix in (("rev-list", "--first-parent"), ("rev-list", "--parents")):
            with self.subTest(prefix=prefix), git_fault(
                    recovery.watchdog, lambda args: args[:2] == prefix, 128):
                self.assertExit2("git_unavailable")

    def named_run(self, **fields):
        document = {"id": 123, "run_attempt": 1, "event": "workflow_dispatch", "status": "completed",
                    "conclusion": "failure", "head_sha": self.release, "path": recovery.WORKFLOW_PATH,
                    "triggering_actor": {"login": "jeter-1"}, **fields}
        self.opener.routes[("GET", API + "/actions/runs/123")] = [
            Response(200, json.dumps(document).encode())]

    def row_H14(self):
        self.named_run(head_sha="d" * 40)
        self.assertExit2("run_head_ref_not_found", "--run-id", "123")
        release = self.release
        for outcome, category in ((128, "run_head_git_unavailable"),
                                  (b"not-a-sha\n", "run_head_git_output_unexpected")):
            with self.subTest(outcome=outcome), git_fault(
                    recovery.watchdog, lambda args: args[:1] == ("rev-parse",)
                    and release + "^{commit}" in args, outcome):
                self.named_run()
                self.assertExit2(category, "--run-id", "123")

    def row_H15(self):
        for fields in ({"status": "action_required"}, {"head_sha": "short"}, {"path": "../x\n"},
                       {"event": "bad event\n"}, {"triggering_actor": {"login": "bad login\n"}}):
            with self.subTest(fields=fields):
                self.named_run(**fields)
                state = self.assertStop(run_id="123", version="1.0.0")
                self.assertEqual(state["run"], "unknown")
        # A wrong-typed key is a malformed response (H23), never a readable run.
        for fields in ({"id": "123"}, {"triggering_actor": None}, {"status": None}, {"event": 7}):
            with self.subTest(fields=fields):
                self.named_run(**fields)
                self.assertExit2("github_response_malformed:run", "--run-id", "123")

    def row_H16(self):
        job = {"name": recovery.BUILD_JOB, "conclusion": "success"}
        for jobs in ({"total_count": 2, "jobs": [job]}, {"total_count": 2, "jobs": [job, 1]}):
            with self.subTest(jobs=jobs):
                self.claim(jobs)
                self.assertEqual(self.assertStop(version="1.0.0")["claim_build"], "unknown")
        # A missing or wrong-typed top-level key is a malformed response (H23).
        for jobs in ({"jobs": [job]}, {"total_count": "1", "jobs": [job]}, {"total_count": 1, "jobs": {}}, [job]):
            with self.subTest(jobs=jobs):
                self.claim(jobs)
                self.assertExit2("github_response_malformed:claim_jobs")
        self.claim({"total_count": 1, "jobs": [job]})
        self.assertEqual(self.inspect(version="1.0.0")[1][0], "needs_digest")
        self.claim({"total_count": 0, "jobs": []})
        self.assertEqual(self.inspect(version="1.0.0")[0]["claim_build"], "not_started")

    def row_H17(self):
        for conclusion in (7, ["success"], "bad conclusion\n"):
            with self.subTest(conclusion=conclusion):
                self.claim({"total_count": 1, "jobs": [{"name": recovery.BUILD_JOB, "conclusion": conclusion}]})
                self.assertEqual(self.assertStop(version="1.0.0")["claim_build"], "unknown")

    def row_H18(self):
        runs = API + "/actions/workflows/publish-rc-image.yml/runs"
        for status in ("action_required", None, "Queued"):
            with self.subTest(status=status):
                self.opener.routes[("GET", runs)] = [Response(200, json.dumps(
                    {"total_count": 1, "workflow_runs": [helpers.run(5, status, None, sha=self.head)]}).encode())]
                self.assertEqual(self.assertStop(version="1.0.0")["live_runs"], "unknown")

    def row_H19(self):
        body, children = helpers.index(("amd64", None), ("arm64", "v8"))
        document = json.loads(body)
        for manifests in (None, {}, [1], document["manifests"] + [{"digest": "sha256:" + "e" * 64,
                                                                   "platform": "linux/arm64"}]):
            with self.subTest(manifests=manifests):
                value = {k: v for k, v in dict(document, manifests=manifests).items() if v is not None}
                self.opener.routes[("GET", MANIFESTS + "1.0.0")] = [
                    Response(200, json.dumps(value).encode(), helpers.INDEX_TYPE)]
                for digest, child in children.items():
                    self.opener.routes[("GET", MANIFESTS + digest)] = [
                        Response(200, child, helpers.MANIFEST_TYPE)]
                self.assertEqual(self.assertStop(version="1.0.0")["registry_version"], "unknown")

    def row_H20(self):
        for route in ("/git/ref/tags/v1.0.0", "/releases/tags/v1.0.0", "/git/ref/heads/main"):
            with self.subTest(route=route):
                saved = self.opener.routes.get(("GET", API + route))
                self.opener.routes[("GET", API + route)] = [Response(500)] * 10
                self.assertStop(version="1.0.0")
                self.opener.routes[("GET", API + route)] = saved

    def row_H21(self):
        self.assertExit2("ref_not_found", "--version", "1.0.0", "--main-ref", "absent")
        for outcome in (128, subprocess.TimeoutExpired("git", 60)):
            with self.subTest(outcome=outcome), git_fault(
                    recovery.watchdog, lambda args: "refs/remotes/origin/main^{commit}" in args, outcome):
                self.assertExit2("git_unavailable")

    def row_H22(self):
        for error in (MemoryError(), RecursionError(), KeyError("x")):
            with self.subTest(error=type(error).__name__), mock.patch.object(
                    recovery.watchdog, "registry_state", side_effect=error):
                self.assertExit2(f"unexpected_{type(error).__name__}")

    def malformed(self, label, body):
        """Serve ``body`` from ``label``'s endpoint, everything before it valid."""
        if label in ("claim_tag", "claim_run", "claim_jobs"):
            self.claim({"total_count": 1, "jobs": [{"name": recovery.BUILD_JOB, "conclusion": "success"}]})
        route = {"main_ref": "/git/ref/heads/main", "tag_ref": "/git/ref/tags/v1.0.0",
                 "release": "/releases/tags/v1.0.0", "claim_ref": CLAIM_REF, "claim_tag": "/git/tags/" + "c" * 40,
                 "run": "/actions/runs/123", "claim_run": "/actions/runs/77/attempts/2",
                 "claim_jobs": "/actions/runs/77/attempts/2/jobs"}[label]
        if label == "run":
            self.named_run()
        self.opener.routes[("GET", API + route)] = [Response(200, json.dumps(body).encode())]
        return ("--run-id", "123") if label == "run" else ()

    def row_H23(self):
        clean = {key: list(queue) for key, queue in self.opener.routes.items()}

        def reset():
            self.opener.routes = {key: list(queue) for key, queue in clean.items()}
        # Codex SV-M1: 200 + null on the claim ref, the version tag and the GitHub Release.
        for label in ("claim_ref", "tag_ref", "release"):
            with self.subTest(codex=label):
                self.malformed(label, None)
                self.assertExit2(f"github_response_malformed:{label}")
                with self.assertRaisesRegex(recovery.RecoveryError, f"github_response_malformed:{label}"):
                    recovery.inspect(self.root, REPO, self.reader, version="1.0.0")
            reset()
        for label, variants in SHAPE_FAILURES.items():
            for body in (*NON_OBJECTS, *variants):
                with self.subTest(label=label, body=body):
                    args = self.malformed(label, body)
                    self.assertExit2(f"github_response_malformed:{label}", *args)
                reset()
        # Positive control: the allowed 404 on every absent-capable endpoint is the
        # only absence, and leads to the fresh recovery command.
        self.assertFreshControl()

    def test_every_table_row_has_a_case(self):
        cases = {name.removeprefix("row_") for name in dir(self) if name.startswith("row_")}
        self.assertEqual(cases, set(HELPER_FAILURE_ROWS))


for _row in HELPER_FAILURE_ROWS:
    setattr(HelperFailureClassTests, f"test_row_{_row}", lambda self, row=_row: getattr(self, f"row_{row}")())


if __name__ == "__main__":
    unittest.main()
