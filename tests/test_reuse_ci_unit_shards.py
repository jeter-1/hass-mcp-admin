"""Evidence reuse of CI unit-shard results (HAMCP-365); synthetic repositories and a fake GitHub only."""

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

import test_unit_test_shards as fixtures


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hamcp365_reuse_ci_unit_shards", ROOT / "scripts/reuse_ci_unit_shards.py")
reuse = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reuse)

REPO = "example-owner/example-repo"
RUN = 1001
ACTIONS = f"repos/{REPO}/actions"
BASE = "b" * 40
MERGE = "c" * 40
SUMMARY_KEYS = {
    "schema_version", "mode", "result", "problems", "repository", "run_id", "run_attempt", "run_url", "event",
    "head_sha", "source_tree", "ci_source_sha", "shard_count", "general_shards", "serial_shards",
    "discovered_count", "discovered_sha256", "plan_sha256", "outcomes", "executed_tests", "skipped",
    "python_versions", "artifacts",
}


def encode(value):
    return json.dumps(value).encode()


def zipped(members, compression=zipfile.ZIP_DEFLATED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        for name, raw in members:
            archive.writestr(name, raw)
    return buffer.getvalue()


class FakeGitHub:
    """Serves fixed bodies by exact route; a list serves its items in turn, the last one repeating."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, route):
        self.calls.append(route)
        value = self.routes[route]
        if isinstance(value, list):
            value = value.pop(0) if len(value) > 1 else value[0]
        if callable(value):
            value = value()
        if isinstance(value, BaseException):
            raise value
        return value


class ReuseFixture(fixtures.ShardFixture):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Discovery writes bytecode into the synthetic checkout; ignoring it keeps the tree clean.
        (cls.root / ".git" / "info").mkdir(exist_ok=True)
        (cls.root / ".git" / "info" / "exclude").write_text("__pycache__/\n.artifacts/\n")
        cls.tree = cls.git("rev-parse", "HEAD^{tree}")

    def output(self):
        return Path(tempfile.mkdtemp(dir=self.temp.name)) / "receipt"

    def scenario(self, manifests=None, attempt=1, source=None):
        manifests = self.copies() if manifests is None else manifests
        if source:
            for manifest in manifests:
                manifest["source_sha"] = source
        identity = {"run_id": RUN, "run_attempt": attempt, "head_sha": self.head,
                    "status": "completed", "conclusion": "success"}
        jobs = [{"name": f"unit tests shard {index}", **identity} for index in range(3)]
        jobs += [{"name": "validate_source", **identity}, {**identity, "name": "lint", "conclusion": "skipped"}]
        archives, artifacts = {}, []
        for index, manifest in enumerate(manifests):
            body = zipped([("manifest.json", encode(manifest))])
            archives[500 + index] = body
            artifacts.append({"id": 500 + index, "name": f"unit-test-shard-{index}-{RUN}-{attempt}",
                              "digest": "sha256:" + hashlib.sha256(body).hexdigest(), "size_in_bytes": len(body),
                              "expired": False, "workflow_run": {"id": RUN, "head_sha": self.head}})
        def unusable(identifier, name):
            return {"id": identifier, "name": name, "digest": "sha256:" + "0" * 64, "size_in_bytes": 1,
                    "expired": True, "workflow_run": {"id": RUN, "head_sha": self.head}}

        # Earlier attempts' uploads stay listed under the run; unusable ones prove they are never selected.
        artifacts += [unusable(400 + 10 * earlier + index, f"unit-test-shard-{index}-{RUN}-{earlier}")
                      for earlier in range(1, attempt) for index in range(3)]
        artifacts.append(unusable(499, f"source-validation-{RUN}-{attempt}"))
        commits = {self.head: {"sha": self.head, "tree": {"sha": self.tree}, "parents": [{"sha": BASE}]}}
        if source:
            commits[source] = {"sha": source, "tree": {"sha": self.tree},
                               "parents": [{"sha": BASE}, {"sha": self.head}]}
        return {
            "run": {"id": RUN, "run_attempt": attempt, "status": "completed", "conclusion": "success",
                    "head_sha": self.head, "path": ".github/workflows/ci.yml", "event": "pull_request",
                    "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO}},
            "jobs": jobs, "artifacts": artifacts, "archives": archives, "commits": commits,
        }

    def routes(self, s):
        run = s.get("run_again", s["run"])
        routes = {
            f"{ACTIONS}/runs/{RUN}": [encode(s["run"]), encode(run)],
            f"{ACTIONS}/runs/{RUN}/attempts/{s['run']['run_attempt']}/jobs?per_page=100":
                encode({"total_count": s.get("jobs_total", len(s["jobs"])), "jobs": s["jobs"]}),
            f"{ACTIONS}/runs/{RUN}/artifacts?per_page=100":
                encode({"total_count": s.get("artifacts_total", len(s["artifacts"])), "artifacts": s["artifacts"]}),
        }
        routes.update({f"{ACTIONS}/artifacts/{key}/zip": body for key, body in s["archives"].items()})
        routes.update({f"repos/{REPO}/git/commits/{sha}": encode(c) for sha, c in s["commits"].items()})
        routes.update(s.get("routes", {}))
        return routes

    def reuse(self, s, root=None, output=None):
        github = FakeGitHub(self.routes(s))
        output = output or self.output()
        summary = reuse.reuse(root or self.root, REPO, str(RUN), output, read=github)
        self.assertEqual(json.loads((output / "summary.json").read_text()), summary)
        self.assertEqual(set(summary), SUMMARY_KEYS)
        return summary, github, output

    def assertProblem(self, s, problem, root=None):
        summary, _, _ = self.reuse(s, root=root)
        self.assertEqual((summary["result"], summary["problems"]), ("failed", [problem]))
        return summary

    def set_archive(self, s, index, body):
        """Replace one shard's archive and relist it with a matching digest and size."""
        artifact = s["artifacts"][index]
        s["archives"][artifact["id"]] = body
        artifact.update(digest="sha256:" + hashlib.sha256(body).hexdigest(), size_in_bytes=len(body))

    def clone(self, name):
        clone = Path(self.temp.name) / name
        shutil.copytree(self.root, clone)
        return clone


class ReuseAcceptanceTests(ReuseFixture):

    def test_results_of_a_ci_run_on_this_commit_are_reused(self):
        summary, github, output = self.reuse(self.scenario())
        self.assertEqual((summary["result"], summary["problems"]), ("passed", []))
        self.assertEqual(summary["mode"], "reused_ci")
        self.assertEqual((summary["head_sha"], summary["ci_source_sha"], summary["source_tree"]),
                         (self.head, self.head, self.tree))
        self.assertEqual((summary["run_id"], summary["run_attempt"], summary["event"]), (RUN, 1, "pull_request"))
        self.assertEqual(summary["run_url"], f"https://github.com/{REPO}/actions/runs/{RUN}/attempts/1")
        self.assertEqual((summary["shard_count"], summary["general_shards"], summary["serial_shards"]), (3, 2, [2]))
        self.assertEqual(summary["outcomes"], {"expected_failure": 0, "skipped": 1, "success": 6})
        self.assertEqual((summary["discovered_count"], summary["executed_tests"], summary["skipped"]), (7, 7, 1))
        self.assertEqual(summary["plan_sha256"], self.manifests[0]["plan_sha256"])
        self.assertEqual(summary["discovered_sha256"], self.manifests[0]["discovered_sha256"])
        self.assertEqual(summary["python_versions"], [self.manifests[0]["python_version"]])
        self.assertEqual([(a["shard"], a["id"], a["name"]) for a in summary["artifacts"]],
                         [(i, 500 + i, f"unit-test-shard-{i}-{RUN}-1") for i in range(3)])
        for index in range(3):
            self.assertEqual((output / "manifests" / f"shard-{index}.json").read_bytes(),
                             encode(self.manifests[index]))
        self.assertIn("exit 0", (output / "logs" / "aggregate.log").read_text())
        self.assertEqual(github.calls, [
            f"{ACTIONS}/runs/{RUN}", f"{ACTIONS}/runs/{RUN}/attempts/1/jobs?per_page=100",
            f"{ACTIONS}/runs/{RUN}/artifacts?per_page=100",
            *(f"{ACTIONS}/artifacts/{500 + i}/zip" for i in range(3)),
            f"repos/{REPO}/git/commits/{self.head}", f"{ACTIONS}/runs/{RUN}"])

    def test_a_pull_request_merge_commit_with_this_tree_is_reused(self):
        summary, _, _ = self.reuse(self.scenario(source=MERGE))
        self.assertEqual((summary["result"], summary["problems"]), ("passed", []))
        self.assertEqual((summary["head_sha"], summary["ci_source_sha"]), (self.head, MERGE))

    def test_a_later_attempt_is_reused_only_from_its_own_artifacts(self):
        manifests = self.copies()
        for manifest in manifests:
            manifest["run_attempt"] = "2"
        summary, _, _ = self.reuse(self.scenario(manifests, attempt=2))
        self.assertEqual((summary["result"], summary["run_attempt"]), ("passed", 2))
        self.assertEqual({a["name"] for a in summary["artifacts"]}, {f"unit-test-shard-{i}-{RUN}-2" for i in range(3)})

    def test_repository_names_compare_without_case(self):
        s = self.scenario()
        s["run"]["repository"]["full_name"] = REPO.upper()
        self.assertEqual(self.reuse(s)[0]["result"], "passed")


class ReuseRefusalTests(ReuseFixture):

    def test_the_run_must_be_this_repositorys_successful_ci_run_for_head(self):
        cases = [
            ("id", 1002, "ci_run_mismatch"), ("id", str(RUN), "ci_run_mismatch"),
            ("run_attempt", 0, "ci_run_mismatch"), ("run_attempt", True, "ci_run_mismatch"),
            ("run_attempt", "1", "ci_run_mismatch"),
            ("repository", {"full_name": "other-owner/example-repo"}, "ci_run_repository_mismatch"),
            ("head_repository", {"full_name": "fork-owner/example-repo"}, "ci_run_repository_mismatch"),
            ("head_repository", None, "ci_run_repository_mismatch"),
            ("path", ".github/workflows/publish-rc-image.yml", "ci_run_workflow_mismatch"),
            ("event", "pull_request_target", "ci_run_event_unsupported"),
            ("event", "workflow_call", "ci_run_event_unsupported"),
            ("status", "in_progress", "ci_run_not_successful"), ("conclusion", "failure", "ci_run_not_successful"),
            ("conclusion", None, "ci_run_not_successful"), ("head_sha", BASE, "ci_run_head_mismatch"),
        ]
        for key, value, problem in cases:
            with self.subTest(key=key, value=value):
                s = self.scenario()
                s["run"][key] = value
                summary = self.assertProblem(s, problem)
                self.assertEqual(summary["artifacts"], [])

    def test_every_shard_job_and_validate_source_must_have_succeeded_in_that_attempt(self):
        def edit(change):
            s = self.scenario()
            change(s["jobs"], s)
            return s

        cases = [
            (lambda jobs, s: jobs.pop(1), "ci_shard_jobs_mismatch"),
            (lambda jobs, s: jobs.append({**jobs[0], "name": "unit tests shard 3"}), "ci_shard_jobs_mismatch"),
            (lambda jobs, s: jobs.append(dict(jobs[0])), "ci_shard_jobs_mismatch"),
            (lambda jobs, s: jobs.pop(3), "ci_validate_source_missing"),
            (lambda jobs, s: jobs.append(dict(jobs[3])), "ci_validate_source_missing"),
            (lambda jobs, s: jobs[2].update(conclusion="failure"), "ci_job_not_successful"),
            (lambda jobs, s: jobs[3].update(conclusion="skipped"), "ci_job_not_successful"),
            (lambda jobs, s: jobs[0].update(status="in_progress"), "ci_job_not_successful"),
            (lambda jobs, s: jobs[1].update(run_attempt=2), "ci_job_identity_mismatch"),
            (lambda jobs, s: jobs[3].update(head_sha=BASE), "ci_job_identity_mismatch"),
            (lambda jobs, s: jobs[0].update(run_id=str(RUN)), "ci_job_identity_mismatch"),
            (lambda jobs, s: jobs[0].update(name=None), "ci_jobs_malformed"),
            (lambda jobs, s: s.update(jobs_total=len(jobs) + 1), "ci_jobs_incomplete"),
            (lambda jobs, s: jobs.extend({"name": f"extra {i}"} for i in range(100)), "ci_jobs_incomplete"),
        ]
        for number, (change, problem) in enumerate(cases):
            with self.subTest(case=number, problem=problem):
                self.assertProblem(edit(change), problem)

    def test_each_shard_needs_exactly_one_unexpired_artifact_from_that_attempt(self):
        def edit(change):
            s = self.scenario()
            change(s["artifacts"], s)
            return s

        cases = [
            (lambda items, s: items.pop(1), "ci_artifact_missing"),
            (lambda items, s: items[1].update(name=f"unit-test-shard-1-{RUN}-2"), "ci_artifact_missing"),
            (lambda items, s: items.append(dict(items[0])), "ci_artifact_duplicate"),
            (lambda items, s: items[2].update(expired=True), "ci_artifact_expired"),
            (lambda items, s: items[2].update(expired=None), "ci_artifact_expired"),
            (lambda items, s: items[0].update(workflow_run={"id": RUN + 1, "head_sha": self.head}),
             "ci_artifact_identity_mismatch"),
            (lambda items, s: items[0].update(workflow_run={"id": RUN, "head_sha": BASE}),
             "ci_artifact_identity_mismatch"),
            (lambda items, s: items[0].update(digest=None), "ci_artifact_digest_missing"),
            (lambda items, s: items[0].update(digest=items[0]["digest"].upper()), "ci_artifact_digest_missing"),
            (lambda items, s: items[0].update(size_in_bytes=0), "ci_artifact_size_unsupported"),
            (lambda items, s: items[0].update(size_in_bytes=reuse.retry.API_BOUND + 1),
             "ci_artifact_size_unsupported"),
            (lambda items, s: items[0].update(id="500"), "ci_artifacts_malformed"),
            (lambda items, s: items[0].update(id=0), "ci_artifacts_malformed"),
            (lambda items, s: s.update(artifacts_total=len(items) + 1), "ci_artifacts_incomplete"),
        ]
        for number, (change, problem) in enumerate(cases):
            with self.subTest(case=number, problem=problem):
                self.assertProblem(edit(change), problem)

    def test_each_archive_must_be_the_listed_bytes_holding_one_manifest(self):
        raw = encode(self.manifests[1])
        stored = zipped([("manifest.json", raw)], zipfile.ZIP_STORED)
        corrupt = stored.replace(raw, raw.replace(b'"shard": 1', b'"shard": 9'))
        self.assertNotEqual(corrupt, stored)
        # The writer always clears the encryption flag, so set it in both headers by hand.
        encrypted = bytearray(stored)
        encrypted[stored.index(b"PK\x03\x04") + 6] |= 0x1
        encrypted[stored.index(b"PK\x01\x02") + 8] |= 0x1
        encrypted = bytes(encrypted)
        with zipfile.ZipFile(io.BytesIO(encrypted)) as archive:
            self.assertTrue(archive.infolist()[0].flag_bits & 0x1)
        cases = [
            (zipped([("manifest.json", raw), ("extra.json", b"{}")]), "ci_artifact_layout"),
            (zipped([("shard-1.json", raw)]), "ci_artifact_layout"),
            (zipped([("manifest.json/", b"")]), "ci_artifact_layout"),
            (zipped([("manifest.json", b"")]), "ci_artifact_layout"),
            (zipped([("manifest.json", raw)], zipfile.ZIP_BZIP2), "ci_artifact_layout"),
            (encrypted, "ci_artifact_layout"),
            (b"not an archive", "ci_artifact_unreadable"),
            (corrupt, "ci_artifact_unreadable"),
            (zipped([("manifest.json", raw[:-1])]), "ci_manifest_malformed"),
            (zipped([("manifest.json", b'{"shard": 1, "shard": 1}')]), "ci_manifest_malformed"),
            (zipped([("manifest.json", b'{"shard": 1, "x": NaN}')]), "ci_manifest_malformed"),
            (zipped([("manifest.json", b'{"shard": 1, "x": "\xff"}')]), "ci_manifest_malformed"),
            (zipped([("manifest.json", b"[1]")]), "ci_manifest_shard_mismatch"),
            (zipped([("manifest.json", encode(self.manifests[0]))]), "ci_manifest_shard_mismatch"),
            (zipped([("manifest.json", encode({**self.manifests[1], "shard": True}))]), "ci_manifest_shard_mismatch"),
        ]
        for number, (body, problem) in enumerate(cases):
            with self.subTest(case=number, problem=problem):
                s = self.scenario()
                self.set_archive(s, 1, body)
                self.assertProblem(s, problem)
        for listed in ("digest", "size_in_bytes"):
            with self.subTest(listed=listed):
                s = self.scenario()
                artifact = s["artifacts"][1]
                artifact[listed] = "sha256:" + "0" * 64 if listed == "digest" else artifact[listed] + 1
                self.assertProblem(s, "ci_artifact_digest_mismatch")

    def test_cis_completeness_proof_must_accept_the_manifests_for_this_tree_and_plan(self):
        def edit(change):
            manifests = self.copies()
            change(manifests)
            return self.scenario(manifests)

        cases = [
            (lambda m: m[1].update(source_tree="0" * 40), "shard_source_tree_mismatch"),
            (lambda m: m[1].pop("source_tree"), "shard_source_tree_missing"),
            (lambda m: m[2].update(source_sha=MERGE), "shard_source_mixed"),
            (lambda m: m[0].update(run_attempt="2"), "shard_run_mismatch"),
            (lambda m: m[0].update(plan_sha256="0" * 64), "shard_discovery_or_plan_mismatch"),
            (lambda m: m[0]["executed"].pop(), "shard_execution_incomplete_or_extra"),
            (lambda m: m[1]["executed"][0].__setitem__(1, "failure"), "test_did_not_succeed"),
            (lambda m: m[1].update(was_successful=False), "shard_did_not_succeed"),
        ]
        for number, (change, problem) in enumerate(cases):
            with self.subTest(case=number, problem=problem):
                summary = self.assertProblem(edit(change), "ci_proof_refused:" + problem)
                self.assertIsNone(summary["executed_tests"])

    def test_a_checkout_with_another_tree_is_refused(self):
        clone = self.clone("other-tree")
        (clone / "notes.txt").write_text("synthetic non-test file\n")
        subprocess.check_call(["git", "-C", str(clone), "add", "notes.txt"])
        subprocess.check_call(["git", "-C", str(clone), "commit", "-qm", "other tree"])
        # The run must name the clone's HEAD, so the tree check is the one that refuses.
        head = subprocess.check_output(["git", "-C", str(clone), "rev-parse", "HEAD"], text=True).strip()
        s = self.scenario()
        s["run"]["head_sha"] = head
        for job in s["jobs"]:
            job["head_sha"] = head
        for artifact in s["artifacts"]:
            artifact["workflow_run"]["head_sha"] = head
        self.assertProblem(s, "ci_proof_refused:shard_source_tree_mismatch", root=clone)

    def test_github_must_record_the_tested_commit_as_head_or_its_merge(self):
        cases = [
            ({"sha": BASE}, "ci_commit_mismatch"),
            ({"tree": {"sha": "0" * 40}}, "ci_commit_tree_mismatch"),
            ({"tree": None}, "ci_commit_tree_mismatch"),
            ({"parents": [{"sha": BASE}, {"sha": BASE}]}, "ci_commit_not_head_merge"),
            ({"parents": [{"sha": self.head}]}, "ci_commit_not_head_merge"),
            ({"parents": [{"sha": BASE}, {"sha": self.head}, {"sha": BASE}]}, "ci_commit_not_head_merge"),
        ]
        for change, problem in cases:
            with self.subTest(change=change):
                s = self.scenario(source=MERGE)
                s["commits"][MERGE].update(change)
                self.assertProblem(s, problem)
        for event in ("schedule", "workflow_dispatch"):
            with self.subTest(event=event):
                s = self.scenario(source=MERGE)
                s["run"]["event"] = event
                self.assertProblem(s, "ci_commit_not_head_merge")

    def test_a_run_that_changed_during_the_proof_is_refused(self):
        for key, value in (("run_attempt", 2), ("conclusion", "failure"), ("status", "in_progress")):
            with self.subTest(key=key):
                s = self.scenario()
                s["run_again"] = {**s["run"], key: value}
                summary = self.assertProblem(s, "ci_run_changed")
                self.assertIsNotNone(summary["executed_tests"])

    def test_a_checkout_that_changed_during_the_proof_is_refused(self):
        for name, change in (("commit", ("commit", "--allow-empty", "-qm", "moved")), ("file", None)):
            with self.subTest(change=name):
                clone = self.clone(f"moving-{name}")

                def move(clone=clone, change=change):
                    if change:
                        subprocess.check_call(["git", "-C", str(clone), *change])
                    else:
                        (clone / "untracked.txt").write_text("appeared during the proof\n")
                    return encode({"sha": self.head, "tree": {"sha": self.tree}, "parents": []})

                s = self.scenario()
                s["routes"] = {f"repos/{REPO}/git/commits/{self.head}": move}
                self.assertProblem(s, "worktree_changed" if change else "worktree_not_clean", root=clone)

    def test_a_dirty_checkout_is_refused_before_anything_is_read_or_written(self):
        for name in ("untracked", "modified"):
            with self.subTest(change=name):
                clone = self.clone(f"dirty-{name}")
                if name == "untracked":
                    (clone / "scratch.txt").write_text("not committed\n")
                else:
                    (clone / "tests" / "test_beta.py").write_text("# edited\n")
                github, output = FakeGitHub({}), self.output()
                with self.assertRaisesRegex(reuse.Refusal, "^worktree_not_clean$"):
                    reuse.reuse(clone, REPO, str(RUN), output, read=github)
                self.assertEqual(github.calls, [])
                self.assertFalse(output.exists())

    def test_github_failures_are_reported_by_category_without_response_text(self):
        error = reuse.retry.GitHubReadError
        cases = [
            (error("github.com", 404, 1, False), "github_read_failed:404"),
            (error("github.com", "network", 3, True), "github_read_unavailable:network"),
            (error("github.com", "authentication_required", 1, False), "github_read_failed:authentication_required"),
            (ValueError("api_route_not_allowed"), "github_read_refused:api_route_not_allowed"),
            (ValueError("Bad credentials for token ghp_example"), "github_read_refused:ValueError"),
            (FileNotFoundError("/home/example/.config/gh"), "github_read_refused:FileNotFoundError"),
            ("text, not bytes", "github_response_malformed"),
            (b"x" * (reuse.retry.API_BOUND + 1), "github_response_too_large"),
        ]
        for value, problem in cases:
            with self.subTest(problem=problem):
                s = self.scenario()
                s["routes"] = {f"{ACTIONS}/runs/{RUN}/artifacts?per_page=100": value}
                summary, _, output = self.reuse(s)
                self.assertEqual(summary["problems"], [problem])
                written = (output / "summary.json").read_text()
                self.assertNotIn("ghp_example", written)
                self.assertNotIn(".config", written)
        for body, problem in ((b"{", "ci_run_malformed"), (b'{"id": 1, "id": 1}', "ci_run_malformed"),
                              (b"[]", "ci_run_mismatch")):
            with self.subTest(run=body):
                s = self.scenario()
                s["routes"] = {f"{ACTIONS}/runs/{RUN}": body}
                self.assertProblem(s, problem)

    def test_an_unexpected_defect_fails_closed(self):
        s = self.scenario()
        s["routes"] = {f"{ACTIONS}/runs/{RUN}/artifacts?per_page=100": lambda: 1 / 0}
        self.assertProblem(s, "reuse_error:ZeroDivisionError")

    def test_invalid_arguments_are_refused_before_any_read(self):
        github = FakeGitHub({})
        for repository, run_id, problem in (
                ("example-owner/..", str(RUN), "invalid_repository"), ("../example-repo", str(RUN), "invalid_repository"),
                ("example-owner", str(RUN), "invalid_repository"), (f"{REPO}/extra", str(RUN), "invalid_repository"),
                ("-owner/example-repo", str(RUN), "invalid_repository"), (None, str(RUN), "invalid_repository"),
                (REPO, "0", "invalid_run_id"), (REPO, "01", "invalid_run_id"), (REPO, "1e3", "invalid_run_id"),
                (REPO, "1" * 21, "invalid_run_id"), (REPO, RUN, "invalid_run_id")):
            with self.subTest(repository=repository, run_id=run_id):
                output = self.output()
                with self.assertRaisesRegex(reuse.Refusal, f"^{problem}$"):
                    reuse.reuse(self.root, repository, run_id, output, read=github)
                self.assertFalse(output.exists())
        self.assertEqual(github.calls, [])


class ReuseProofProcessTests(ReuseFixture):

    def layout(self):
        output = self.output()
        return reuse.local_unit_test_shards.prepare_layout(output)

    def test_the_proof_runs_in_a_child_and_its_failures_have_categories(self):
        layout = self.layout()
        with mock.patch.object(reuse, "PROOF_TIMEOUT", 0.001):
            with self.assertRaisesRegex(reuse.Refusal, "^ci_proof_timeout$"):
                reuse.run_proof(self.root, layout, self.head, RUN, 1)
        with mock.patch.object(reuse.subprocess, "run", side_effect=OSError("no interpreter")):
            with self.assertRaisesRegex(reuse.Refusal, "^ci_proof_unavailable$"):
                reuse.run_proof(self.root, layout, self.head, RUN, 1)
        for stdout, stderr, code, problem in (
                ("", "Traceback (most recent call last):\n", 3, "ci_proof_error:exit_3"),
                ("not json\n", "", 0, "ci_proof_malformed"), ("", "", 0, "ci_proof_malformed")):
            with self.subTest(problem=problem):
                done = subprocess.CompletedProcess([], code, stdout=stdout, stderr=stderr)
                with mock.patch.object(reuse.subprocess, "run", return_value=done):
                    with self.assertRaisesRegex(reuse.Refusal, f"^{problem}$"):
                        reuse.run_proof(self.root, layout, self.head, RUN, 1)

    def test_a_proof_summary_must_name_this_tree_run_and_plan(self):
        proof = {"result": "success", "binding": "tree", "source_tree": self.tree, "run_id": str(RUN),
                 "run_attempt": "1", "shard_count": 3, "ci_source_sha": self.head, "discovered_count": 7,
                 "discovered_sha256": "a" * 64, "plan_sha256": "d" * 64,
                 "outcomes": {"expected_failure": 0, "skipped": 1, "success": 6}}
        reuse.check_proof(proof, self.tree, RUN, 1, 3)
        for key, value in (("result", "failed"), ("binding", "commit"), ("source_tree", "0" * 40),
                           ("run_id", RUN), ("run_attempt", 1), ("shard_count", 2), ("ci_source_sha", "x"),
                           ("discovered_count", 8), ("plan_sha256", None), ("discovered_sha256", "A" * 64),
                           ("outcomes", {"success": 7, "skipped": -0.5}), ("outcomes", None)):
            with self.subTest(key=key, value=value):
                with self.assertRaisesRegex(reuse.Refusal, "^ci_proof_malformed$"):
                    reuse.check_proof({**proof, key: value}, self.tree, RUN, 1, 3)


class ReuseCommandTests(ReuseFixture):

    def main(self, *args, routes=None, root=None):
        github = FakeGitHub(routes if routes is not None else self.routes(self.scenario()))
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = reuse.main(["--repository", REPO, "--run-id", str(RUN), *args,
                                   "--repo-root", str(root or self.root)], read=github)
            except SystemExit as exc:
                code = exc.code
        return code, stdout.getvalue(), stderr.getvalue(), github

    def test_a_pass_prints_the_summary_path_and_the_reused_count(self):
        output = self.output()
        code, stdout, stderr, _ = self.main("--output-dir", str(output))
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(stdout.splitlines(), [
            f"Summary: {output / 'summary.json'}",
            f"Reused 7 unit-test results from CI run {RUN} attempt 1 for tree {self.tree}; no test ran locally."])

    def test_a_relative_output_directory_works_from_another_cwd(self):
        # The proof's child runs with the checkout as its cwd, so the manifests path must be absolute.
        elsewhere = Path(tempfile.mkdtemp(dir=self.temp.name))
        with contextlib.chdir(elsewhere):
            code, stdout, stderr, _ = self.main("--output-dir", "receipt")
        self.assertEqual((code, stderr), (0, ""), stdout)
        summary = json.loads((elsewhere / "receipt" / "summary.json").read_text())
        self.assertEqual((summary["result"], summary["problems"]), ("passed", []))
        self.assertFalse((self.root / "receipt").exists())

    def test_a_refusal_exits_one_with_its_categories(self):
        s = self.scenario()
        s["run"]["conclusion"] = "failure"
        output = self.output()
        code, stdout, stderr, _ = self.main("--output-dir", str(output), routes=self.routes(s))
        self.assertEqual(code, 1)
        self.assertEqual(stdout, f"Summary: {output / 'summary.json'}\n")
        self.assertEqual(stderr, "CI unit-shard reuse refused: ci_run_not_successful\n")
        self.assertEqual(json.loads((output / "summary.json").read_text())["result"], "failed")

    def test_arguments_and_output_ownership_are_checked(self):
        existing = self.output()
        existing.mkdir()
        (existing / "keep.txt").write_text("unrelated\n")
        for args, problem in (
                (("--output-dir", str(existing)), "output_directory_exists"),
                (("--output-dir", str(self.output()), "--replace-output"), "replacement_requires_fixed_latest_directory"),
                (("--run-id", "0", "--output-dir", str(self.output())), "invalid_run_id")):
            with self.subTest(problem=problem):
                code, stdout, stderr, github = self.main(*args)
                self.assertEqual((code, stdout), (1, ""))
                self.assertEqual(stderr, f"CI unit-shard reuse refused: {problem}\n")
                self.assertEqual(github.calls, [])
        self.assertEqual((existing / "keep.txt").read_text(), "unrelated\n")
        code, _, stderr, _ = self.main()
        self.assertEqual(code, 2)
        self.assertIn("--output-dir", stderr)

    def test_replace_output_overwrites_only_the_owned_latest_receipt(self):
        clone = self.clone("latest-receipt")
        latest = clone / ".artifacts" / "unit-test-shards" / "latest"
        latest.mkdir(parents=True)
        (latest / "foreign.txt").write_text("not ours\n")
        code, _, stderr, _ = self.main("--output-dir", str(latest), "--replace-output", root=clone)
        self.assertEqual((code, stderr), (1, "CI unit-shard reuse refused: latest_output_not_owned\n"))
        self.assertEqual((latest / "foreign.txt").read_text(), "not ours\n")
        shutil.rmtree(latest)
        for attempt in range(2):
            with self.subTest(attempt=attempt):
                code, _, stderr, _ = self.main("--output-dir", str(latest), "--replace-output", root=clone)
                self.assertEqual((code, stderr), (0, ""))
                self.assertEqual((latest / ".hamcp-unit-run").read_text(), "HAMCP-272\n")
                self.assertEqual(json.loads((latest / "summary.json").read_text())["result"], "passed")
                self.assertFalse((latest.parent / "latest.lock").exists())

    def test_the_default_reader_sends_only_allowlisted_routes(self):
        routes = self.routes(self.scenario())
        github = FakeGitHub(routes)
        seen = []

        def api_get(route, *args, **kwargs):
            seen.append((route, args, kwargs))
            self.assertTrue(reuse.retry.API_ROUTE.fullmatch(route), route)
            self.assertNotIn("..", route)
            self.assertNotIn("//", route)
            return github(route)

        with mock.patch.object(reuse.retry, "api_get", api_get):
            summary = reuse.reuse(self.root, REPO, str(RUN), self.output())
        self.assertEqual(summary["result"], "passed", summary["problems"])
        self.assertEqual(len(seen), 8)
        self.assertTrue(all(args == () and kwargs == {} for _, args, kwargs in seen))


if __name__ == "__main__":
    unittest.main()
