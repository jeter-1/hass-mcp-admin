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

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


recovery = load("publication_recovery", ROOT / "scripts/publication_recovery.py")
helpers = load("hamcp223_watchdog_test_helpers", ROOT / "tests/test_advertised_image_watchdog.py")
Response, Opener = helpers.Response, helpers.Opener

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


class InspectionTests(unittest.TestCase):
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
        self.route("/actions/runs/77/attempts/2/jobs", {"jobs": [{"name": recovery.BUILD_JOB,
                                                                  "conclusion": "success"}]})
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
        self.route("/actions/runs/77/attempts/2/jobs", {"jobs": [{"name": recovery.BUILD_JOB}] * 2})
        self.opener.routes[("GET", API + "/actions/runs/77/attempts/2/jobs")].pop(0)
        self.assertEqual(recovery.inspect(self.root, REPO, self.reader, version="1.0.0")["claim_build"], "unknown")

    def test_unreadable_claim_and_remote_errors_are_unknown(self):
        prefix = "/git/ref/tags/engineering-publication-attempt/v1.0.0"
        for document in ({"object": {"type": "commit", "sha": "c" * 40}}, {"object": {"type": "tag", "sha": "x"}}, []):
            self.opener.routes[("GET", API + prefix)] = [Response(200, json.dumps(document).encode())]
            with self.subTest(document=document):
                self.assertEqual(recovery.inspect(self.root, REPO, self.reader, version="1.0.0")["claim"], "unknown")
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


if __name__ == "__main__":
    unittest.main()
