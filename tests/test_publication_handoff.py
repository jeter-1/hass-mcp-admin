"""Offline authority transfer and dispatch tests; no real GitHub/registry writes."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from contextlib import redirect_stderr
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
try:
    import publication_handoff as handoff
    import publication_claim as claim
finally:
    sys.path.pop(0)

BASE, HEAD, MERGE, TREE = (character * 40 for character in "bacd")
PREFIX = handoff.PREFIX


def archive(value, name="publication-handoff.json"):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as target:
        target.writestr(name, json.dumps(value))
    return output.getvalue()


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.receipt = {
            "schema": 1, "repository": handoff.REPOSITORY, "pr_number": 5,
            "base_sha": BASE, "head_sha": HEAD, "merge_sha": MERGE,
            "ready_event_id": 10, "source_run_id": 7, "source_run_attempt": 1,
            "source_event": "pull_request_target", "source_workflow_sha": BASE,
        }
        repo = {"full_name": handoff.REPOSITORY}
        self.run = {
            "id": 7, "run_attempt": 1, "workflow_id": 8, "path": handoff.PRODUCER,
            "event": "pull_request_target", "head_sha": HEAD,
            "status": "completed", "conclusion": "success",
            "repository": repo, "head_repository": repo,
            "actor": {"login": "jeter-1"}, "triggering_actor": {"login": "jeter-1"},
        }
        self.job = {"head_sha": HEAD, "name": "merge-authorized-head", "run_id": 7, "run_attempt": 1,
                    "status": "completed", "conclusion": "success"}
        self.pr = {"number": 5, "merged": True, "state": "closed", "draft": False,
                   "merge_commit_sha": MERGE, "base": {"ref": "main", "repo": repo},
                   "head": {"sha": HEAD, "repo": repo}}
        self.events = [
            {"id": 9, "event": "committed"},
            {"id": 10, "event": "ready_for_review", "actor": {"login": "jeter-1"}},
            {"id": 11, "event": "merged", "commit_id": MERGE},
            {"id": 12, "event": "closed"},
        ]
        self.data = {
            f"{PREFIX}/actions/workflows/enable-auto-merge.yml":
                {"id": 8, "path": handoff.PRODUCER, "state": "active"},
            f"{PREFIX}/actions/runs/7": self.run,
            f"{PREFIX}/actions/runs/7/attempts/1/jobs?per_page=100":
                {"total_count": 1, "jobs": [self.job]},
            f"{PREFIX}/pulls/5": self.pr,
            f"{PREFIX}/issues/5/timeline?per_page=100": self.events,
        }
        self.git_data = {
            ("rev-parse", "HEAD"): BASE,
            ("show", "-s", "--format=%P", MERGE): f"{BASE} {HEAD}",
            ("merge-tree", "--write-tree", BASE, HEAD): TREE,
            ("rev-parse", MERGE + "^{tree}"): TREE,
            ("rev-parse", "refs/remotes/origin/main"): MERGE,
            ("rev-list", "--first-parent", MERGE): f"{MERGE}\n{BASE}",
            ("diff", "--name-only", MERGE, MERGE, "--", "hass_mcp_engineering_beta", ".release"): "",
            ("ls-tree", "--name-only", MERGE, "--", ".release/next-version"): "",
        }
        for path in handoff.POLICY_PATHS:
            for commit in (BASE, MERGE):
                self.git_data[("rev-parse", f"{commit}:{path}")] = "reviewed policy " + path
        self.calls = []
        self.bind_artifact()

    def bind_artifact(self, raw=None):
        raw = archive(self.receipt) if raw is None else raw
        self.artifact = {"id": 13, "name": "publication-handoff-7-1", "expired": False,
                         "workflow_run": {"id": 7}, "size_in_bytes": len(raw),
                         "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}
        self.data[f"{PREFIX}/actions/runs/7/artifacts?per_page=100"] = {
            "total_count": 1, "artifacts": [self.artifact]}
        self.data[f"{PREFIX}/actions/artifacts/13/zip"] = raw

    def api(self, route, binary=False):
        self.calls.append(route)
        return deepcopy(self.data[route])

    def git(self, *args):
        return self.git_data[args]

    def verify(self):
        return handoff.verify(7, 1, MERGE, self.api, self.git)

    def test_owner_ready_merge_round_trip_identifies_actual_merge_commit(self):
        env = {"GITHUB_REPOSITORY": handoff.REPOSITORY, "GITHUB_ACTOR": "jeter-1",
               "GITHUB_TRIGGERING_ACTOR": "jeter-1", "AUTHORIZED_BASE_SHA": BASE,
               "AUTHORIZED_HEAD_SHA": HEAD, "PR_NUMBER": "5", "GITHUB_RUN_ID": "7",
               "GITHUB_RUN_ATTEMPT": "1", "GITHUB_EVENT_NAME": "pull_request_target",
               "GITHUB_WORKFLOW_SHA": BASE}
        result = handoff.produce(env, {"event_id": 10}, self.api, self.git)
        self.assertEqual(result, self.receipt)
        with self.assertRaises(ValueError):
            handoff.produce({**env, "GITHUB_WORKFLOW_SHA": HEAD}, {"event_id": 10}, self.api, self.git)
        self.assertEqual(self.verify(), {"release_action": "inspect", "release_sha": MERGE,
                                         "validation_base": BASE})
        self.assertEqual(self.calls.count(f"{PREFIX}/actions/runs/7"), 2)

    def test_owner_comment_retry_uses_base_run_identity(self):
        self.run.update(event="issue_comment", head_sha=BASE)
        self.job["head_sha"] = BASE
        self.receipt["source_event"] = "issue_comment"
        self.bind_artifact()
        self.assertEqual(self.verify()["release_sha"], MERGE)

    def test_lifecycle_only_success_is_no_op_without_artifact_access(self):
        self.job["conclusion"] = "skipped"
        self.assertEqual(self.verify(), {"release_action": "none"})
        self.assertFalse(any("artifacts" in route for route in self.calls))

    def test_forged_failed_or_replayed_run_is_refused(self):
        for key, bad in (
            ("id", 6), ("run_attempt", 2), ("workflow_id", 42), ("path", "other.yml"),
            ("event", "pull_request"), ("head_sha", BASE), ("status", "in_progress"),
            ("conclusion", "failure"), ("repository", {"full_name": "foreign/repo"}),
            ("head_repository", {"full_name": "foreign/repo"}),
            ("actor", {"login": "other"}), ("triggering_actor", {"login": "other"}),
        ):
            with self.subTest(key=key):
                old = self.run[key]
                self.run[key] = bad
                with self.assertRaises(ValueError):
                    self.verify()
                self.run[key] = old

    def test_exact_job_and_complete_collections_required(self):
        route = f"{PREFIX}/actions/runs/7/attempts/1/jobs?per_page=100"
        for value in ({"total_count": 2, "jobs": [self.job]},
                      {"total_count": 2, "jobs": [self.job, self.job]},
                      {"total_count": 0, "jobs": []}):
            with self.subTest(value=value):
                self.data[route] = value
                with self.assertRaises(ValueError):
                    self.verify()

    def test_receipt_identity_and_schema_are_not_authority(self):
        for key, bad in (("schema", True), ("repository", "other/repo"), ("source_run_id", 3),
                         ("source_run_attempt", 2), ("source_workflow_sha", HEAD), ("base_sha", "f" * 40),
                         ("head_sha", BASE), ("merge_sha", HEAD), ("ready_event_id", 9),
                         ("source_event", "push")):
            with self.subTest(key=key):
                old = self.receipt[key]
                self.receipt[key] = bad
                self.bind_artifact()
                with self.assertRaises((ValueError, KeyError)):
                    self.verify()
                self.receipt[key] = old
        self.receipt["unexpected"] = "not executable"
        self.bind_artifact()
        with self.assertRaises(ValueError):
            self.verify()

    def test_untrusted_archive_shapes_and_digest_are_refused(self):
        for raw in (archive(self.receipt, "../publication-handoff.json"),
                    archive("x" * 9000), b"not zip"):
            with self.subTest(raw=raw[:20]):
                self.bind_artifact(raw)
                with self.assertRaises((ValueError, zipfile.BadZipFile)):
                    self.verify()
        self.bind_artifact()
        self.artifact["digest"] = "sha256:" + "0" * 64
        with self.assertRaises(ValueError):
            self.verify()

    def test_expired_foreign_and_missing_artifacts_refuse(self):
        for key, bad in (("expired", True), ("workflow_run", {"id": 8}),
                         ("name", "publication-handoff-7-2"), ("size_in_bytes", 4_000_001)):
            with self.subTest(key=key):
                self.bind_artifact()
                self.artifact[key] = bad
                with self.assertRaises(ValueError):
                    self.verify()

    def test_ready_replaced_or_wrong_owner_refuses(self):
        self.events[1]["actor"]["login"] = "model"
        with self.assertRaises(ValueError):
            self.verify()
        self.events[1]["actor"]["login"] = "jeter-1"
        self.events.insert(2, {"event": "committed"})
        with self.assertRaises(ValueError):
            self.verify()

    def test_ready_after_reopen_does_not_reauthorize_old_merge(self):
        self.events.append({"event": "reopened"})
        with self.assertRaises(ValueError):
            self.verify()

    def test_merge_parent_tree_policy_and_current_main_drift_refuse(self):
        for args, bad in (
            (("show", "-s", "--format=%P", MERGE), HEAD),
            (("merge-tree", "--write-tree", BASE, HEAD), "e" * 40),
            (("rev-parse", f"{BASE}:{handoff.PRODUCER}"), "forged policy"),
            (("rev-parse", "refs/remotes/origin/main"), HEAD),
            (("rev-list", "--first-parent", MERGE), BASE),
            (("diff", "--name-only", MERGE, MERGE, "--", "hass_mcp_engineering_beta", ".release"), "changed.py"),
            (("ls-tree", "--name-only", MERGE, "--", ".release/next-version"), ".release/next-version"),
        ):
            with self.subTest(args=args):
                old = self.git_data[args]
                self.git_data[args] = bad
                with self.assertRaises(ValueError):
                    self.verify()
                self.git_data[args] = old

    def test_run_rerun_during_verification_is_refused(self):
        count = 0

        def api(route, **kwargs):
            nonlocal count
            value = self.api(route, **kwargs)
            if route == f"{PREFIX}/actions/runs/7":
                count += 1
                if count == 2:
                    value["run_attempt"] = 2
            return value

        with self.assertRaises(ValueError):
            handoff.verify(7, 1, MERGE, api, self.git)

    def test_cli_redacts_malformed_evidence_but_preserves_fixed_refusal_category(self):
        for error, expected in ((ValueError("synthetic-private-marker"), "evidence missing"),
                                (handoff.Refusal("artifact_digest_mismatch"), "artifact_digest_mismatch")):
            output = io.StringIO()
            with patch.object(sys, "argv", ["publication_handoff.py", "verify"]), \
                    patch.object(handoff, "verify", side_effect=error), redirect_stderr(output):
                with self.assertRaises(SystemExit) as stopped:
                    handoff.main()
            self.assertEqual(stopped.exception.code, 1)
            self.assertIn(expected, output.getvalue())
            self.assertNotIn("synthetic-private-marker", output.getvalue())

    def test_real_git_merge_tree_and_wrong_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            def git(*args):
                return subprocess.run(["git", "-C", directory, *args], check=True,
                                      capture_output=True, text=True).stdout.strip()
            git("init", "-b", "main")
            git("config", "user.name", "Synthetic fixture")
            git("config", "user.email", "fixture@example.invalid")
            Path(directory, "file").write_text("base\n")
            git("add", ".")
            git("commit", "-m", "base")
            base = git("rev-parse", "HEAD")
            git("switch", "-c", "feature")
            Path(directory, "file").write_text("release\n")
            git("commit", "-am", "head")
            head = git("rev-parse", "HEAD")
            git("switch", "main")
            git("merge", "--no-ff", "feature", "-m", "merge")
            merge = git("rev-parse", "HEAD")
            self.pr["head"]["sha"] = head
            self.pr["merge_commit_sha"] = merge
            handoff.merged_pr(5, base, head, merge, self.api, git)
            with self.assertRaises(ValueError):
                handoff.merged_pr(5, head, base, merge, self.api, git)


class ClaimTests(unittest.TestCase):
    def setUp(self):
        self.value = {"schema": 1, "release_sha": MERGE, "version": "2.3.0-beta.5",
                      "workflow_sha": MERGE, "run_id": 9, "run_attempt": 1, "event": "workflow_run"}
        self.tags = {}
        self.reference = None
        self.lock = threading.Lock()
        self.writes = []

    def post(self, route, payload):
        with self.lock:
            self.writes.append(route)
            if route.endswith("/git/tags"):
                oid = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()
                self.tags[oid] = {"sha": oid, "tag": payload["tag"], "message": payload["message"],
                                  "object": {"type": "commit", "sha": payload["object"]}}
                return {"sha": oid}
            self.assertEqual(route, PREFIX + "/git/refs")
            if self.reference is not None:
                raise handoff.Refusal("already_exists")
            self.reference = {"ref": payload["ref"], "object": {"type": "tag", "sha": payload["sha"]}}
            return deepcopy(self.reference)

    def api(self, route):
        if "/git/ref/" in route:
            return deepcopy(self.reference)
        return deepcopy(self.tags[route.rsplit("/", 1)[1]])

    def test_claim_requires_exact_protected_publisher_context(self):
        env = {
            "GITHUB_REPOSITORY": handoff.REPOSITORY, "GITHUB_REF": "refs/heads/main",
            "GITHUB_WORKFLOW_REF": handoff.REPOSITORY + "/.github/workflows/publish-rc-image.yml@refs/heads/main",
            "GITHUB_SHA": MERGE, "GITHUB_WORKFLOW_SHA": MERGE,
            "GITHUB_RUN_ID": "9", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_EVENT_NAME": "workflow_run",
        }
        self.assertEqual(claim.identity(env, MERGE, self.value["version"]), self.value)
        for key, wrong in (("GITHUB_WORKFLOW_REF", "untrusted/workflow"),
                           ("GITHUB_WORKFLOW_SHA", HEAD), ("GITHUB_REF", "refs/heads/feature"),
                           ("GITHUB_REPOSITORY", "foreign/repo"), ("GITHUB_EVENT_NAME", "pull_request")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                claim.identity({**env, key: wrong}, MERGE, self.value["version"])

    def test_create_and_exact_digest_resume_succeed_without_new_writes(self):
        claim.create(self.value, self.post, self.api)
        self.assertEqual(claim.read(self.value["version"], self.api), self.value)
        claim.resume(self.value, 9, 1, MERGE, "workflow_run", self.api)
        self.assertEqual(len(self.writes), 2)

    def test_duplicate_same_attempt_or_rerun_cannot_claim_again(self):
        claim.create(self.value, self.post, self.api)
        for attempt in (1, 2):
            candidate = {**self.value, "run_attempt": attempt}
            with self.assertRaises(ValueError):
                claim.create(candidate, self.post, self.api)
        self.assertEqual(claim.read(self.value["version"], self.api), self.value)

    def test_concurrent_handoffs_only_one_can_pass_build_authority(self):
        def attempt(run_id):
            try:
                claim.create({**self.value, "run_id": run_id}, self.post, self.api)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as workers:
            self.assertEqual(sum(workers.map(attempt, (8, 9))), 1)

    def test_uncertain_creation_does_not_retry_or_delete_marker(self):
        def ambiguous(route, payload):
            result = self.post(route, payload)
            if route.endswith("/git/refs"):
                raise handoff.Refusal("acknowledgement_lost")
            return result
        with self.assertRaises(ValueError):
            claim.create(self.value, ambiguous, self.api)
        self.assertIsNotNone(self.reference)
        self.assertEqual(len(self.writes), 2)
        with self.assertRaises(ValueError):
            claim.create(self.value, self.post, self.api)

    def test_failed_readback_consumes_authority(self):
        with self.assertRaises((ValueError, AttributeError)):
            claim.create(self.value, self.post, lambda route: {})
        with self.assertRaises(ValueError):
            claim.create(self.value, self.post, self.api)

    def test_recovery_cannot_borrow_other_run_attempt_commit_or_version(self):
        claim.create(self.value, self.post, self.api)
        for run, attempt, workflow, event in ((8, 1, MERGE, "workflow_run"), (9, 2, MERGE, "workflow_run"),
                                             (9, 1, HEAD, "workflow_run"), (9, 1, MERGE, "push")):
            with self.subTest(run=run, attempt=attempt, workflow=workflow, event=event):
                with self.assertRaises(ValueError):
                    claim.resume(self.value, run, attempt, workflow, event, self.api)
        with self.assertRaises(ValueError):
            claim.resume({**self.value, "release_sha": HEAD}, 9, 1, MERGE, "workflow_run", self.api)
        self.assertEqual(len(self.writes), 2)


class WorkflowBoundaryTests(unittest.TestCase):
    def test_handoff_validation_read_only_and_claim_precedes_registry_access(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/publish-rc-image.yml").read_text())
        events = workflow.get("on", workflow.get(True))
        self.assertEqual(events["workflow_run"], {"workflows": ["Merge owner-authorized pull request"],
                                                 "types": ["completed"]})
        self.assertEqual(workflow["permissions"], {})
        self.assertNotIn("write", workflow["jobs"]["detect-release"]["permissions"].values())
        steps = workflow["jobs"]["promote"]["steps"]
        names = [step.get("name") for step in steps]
        claim_index = names.index("Consume create-only publication attempt authority")
        self.assertLess(names.index("Revalidate publication authority before registry access"), claim_index)
        self.assertLess(claim_index, names.index("Log in to GHCR"))
        self.assertLess(claim_index, names.index("Build release commit without a temporary tag"))
        self.assertIn('"$SOURCE_MODE" == "resume_digest"', steps[claim_index]["run"])
        self.assertIn('publication_claim.py" resume', steps[claim_index]["run"])
        text = (ROOT / ".github/workflows/publish-rc-image.yml").read_text()
        self.assertEqual(text.count('"$DETECTED_RELEASE_MODE" == "protected_merge_handoff"'), 5)
        self.assertNotIn("actions: write", text)


if __name__ == "__main__":
    unittest.main()
