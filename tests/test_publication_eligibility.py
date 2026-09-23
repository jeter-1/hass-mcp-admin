"""Compose real Git, authentic synthetic handoffs, and the actual detector.

The small job-condition model tests the declared dependency contract; it is not
an execution of GitHub's scheduler. All API data and Git repositories are local.
"""

import ast
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from tests import test_publication_handoff as fixtures

handoff = fixtures.handoff
ROOT = Path(__file__).resolve().parents[1]


def job_runs(job, results, context):
    """Evaluate this workflow's bounded boolean job conditions plus success()."""
    needs = job.get("needs", [])
    if isinstance(needs, str):
        needs = [needs]
    # GitHub supplies implicit success() when no status function is specified.
    if any(results.get(name) != "success" for name in needs):
        return False
    expression = job.get("if", "True").replace("&&", "and").replace("||", "or")

    def value(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return context[node.id]
        if isinstance(node, ast.Attribute):
            return value(node.value).get(node.attr, "")
        if isinstance(node, ast.BoolOp):
            values = (bool(value(item)) for item in node.values)
            if isinstance(node.op, ast.And):
                return all(values)
            if isinstance(node.op, ast.Or):
                return any(values)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = value(node.left), value(node.comparators[0])
            if isinstance(left, str) and isinstance(right, str):
                left, right = left.casefold(), right.casefold()
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
        raise AssertionError(f"Unsupported job condition: {ast.dump(node)}")

    # Hyphens in job identifiers are valid GHA property names, unlike Python.
    expression = expression.replace("detect-release", "detect_release")
    return bool(value(ast.parse(expression, mode="eval").body))


class PublicationEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.jobs = yaml.safe_load((ROOT / ".github/workflows/publish-rc-image.yml").read_text())["jobs"]

    def compose(self, *, release=False, policy_edit=False, forged=False):
        """Produce the receipt at protected base, verify at actual merged main."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.run(["git", *args], cwd=root, check=True,
                                      capture_output=True, text=True, timeout=15).stdout.strip()

            git("init", "-b", "main")
            git("config", "user.name", "Publication eligibility fixture")
            git("config", "user.email", "eligibility@example.invalid")
            config = root / "hass_mcp_engineering_beta/config.yaml"
            config.parent.mkdir()
            config.write_text('version: "2.3.0"\n')
            for path in handoff.POLICY_PATHS:
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("reviewed protected policy\n")
            git("add", ".")
            git("commit", "-m", "Protected base")
            base = git("rev-parse", "HEAD")
            git("switch", "-c", "reviewed")
            if release:
                config.write_text('version: "2.3.1"\n')
            if policy_edit:
                (root / handoff.PRODUCER).write_text("reviewed action pin refresh\n")
            (root / "maintenance.txt").write_text("bounded reviewed change\n")
            git("add", ".")
            git("commit", "-m", "Reviewed candidate")
            head = git("rev-parse", "HEAD")
            git("switch", "main")
            git("merge", "--no-ff", "reviewed", "-m", "Owner-authorized merge")
            merge = git("rev-parse", "HEAD")
            git("update-ref", "refs/remotes/origin/main", merge)
            git("remote", "add", "origin", str(root))

            fixture = fixtures.HandoffTests()
            fixture.setUp()
            fixture.run["head_sha"] = fixture.job["head_sha"] = head
            fixture.pr["head"]["sha"] = head
            fixture.pr["merge_commit_sha"] = merge
            fixture.events[2]["commit_id"] = merge
            git("checkout", "--detach", base)
            fixture.receipt = handoff.produce({
                "GITHUB_REPOSITORY": handoff.REPOSITORY,
                "GITHUB_ACTOR": "jeter-1", "GITHUB_TRIGGERING_ACTOR": "jeter-1",
                "AUTHORIZED_BASE_SHA": base, "AUTHORIZED_HEAD_SHA": head,
                "PR_NUMBER": "5", "GITHUB_RUN_ID": "7", "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_EVENT_NAME": "pull_request_target", "GITHUB_WORKFLOW_SHA": base,
            }, {"event_id": 10}, fixture.api, git)
            fixture.bind_artifact()
            git("checkout", "main")
            if forged:
                fixture.run["actor"] = {"login": "foreign"}
            verified = handoff.verify(7, 1, merge, fixture.api, git)
            environment = {**os.environ, **{
                name: "" for name in self.jobs["detect-release"]["steps"][-1]["env"]}}
            environment.update({
                "EVENT_NAME": "workflow_run", "TRIGGER_SHA": merge,
                "HANDOFF_ACTION": verified["release_action"],
                "HANDOFF_RELEASE_SHA": verified.get("release_sha", ""),
                "HANDOFF_BASE_SHA": verified.get("validation_base", ""),
                "GITHUB_OUTPUT": str(root / "output"),
                "GITHUB_STEP_SUMMARY": str(root / "summary"),
            })
            result = subprocess.run(["bash", "-c", self.jobs["detect-release"]["steps"][-1]["run"]],
                                    cwd=root, env=environment, capture_output=True,
                                    text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs = dict(line.split("=", 1) for line in (root / "output").read_text().splitlines())
            return verified, outputs, merge

    def assert_schedule(self, outputs, *, eligible):
        context = {"needs": {"detect_release": {"outputs": outputs}}}
        results = {"detect-release": "success"}
        self.assertEqual(job_runs(self.jobs["validate"], results, context), eligible)
        self.assertEqual(job_runs(self.jobs["recovery-source"], results, context), eligible)
        for status in ("success", "failure", "cancelled", "skipped"):
            results.update(validate=status, **{"recovery-source": "success"})
            self.assertEqual(job_runs(self.jobs["promote"], results, context),
                             eligible and status == "success")
        for name in ("detect-release", "recovery-source"):
            for status in ("failure", "cancelled", "skipped"):
                results = {"validate": "success", "detect-release": "success", "recovery-source": "success"}
                results[name] = status
                self.assertFalse(job_runs(self.jobs["promote"], results, context))

    def test_ordinary_merge_skips_complete_ci_and_all_publication(self):
        verified, outputs, _ = self.compose()
        self.assertEqual(verified, {"release_action": "none"})
        self.assertEqual(outputs, {"release_action": "none"})
        self.assert_schedule(outputs, eligible=False)

    def test_policy_only_merge_regression_skips_ci_without_authority(self):
        verified, outputs, _ = self.compose(policy_edit=True)
        self.assertEqual(verified, {"release_action": "none"})
        self.assertEqual(outputs, {"release_action": "none"})
        self.assert_schedule(outputs, eligible=False)

    def test_real_release_keeps_exact_handoff_and_requires_full_validation(self):
        verified, outputs, merge = self.compose(release=True)
        self.assertEqual(json.loads(verified["source_handoff"])["release_sha"], merge)
        self.assertEqual(outputs["release_action"], "publish")
        self.assertEqual(outputs["release_sha"], merge)
        self.assertEqual(outputs["source_mode"], "build")
        self.assert_schedule(outputs, eligible=True)

    def test_release_policy_drift_still_refuses(self):
        with self.assertRaisesRegex(ValueError, "producer_policy_changed"):
            self.compose(release=True, policy_edit=True)

    def test_forged_maintenance_handoff_refuses_before_detection(self):
        with self.assertRaises(ValueError):
            self.compose(policy_edit=True, forged=True)

    def test_digest_recovery_requires_full_ci_and_successful_source_verification(self):
        self.assert_schedule({"release_action": "publish", "source_mode": "resume_digest"}, eligible=True)

    def test_detection_failure_never_starts_ci_even_with_publish_output(self):
        context = {"needs": {"detect_release": {"outputs": {"release_action": "publish"}}}}
        for status in ("failure", "cancelled", "skipped"):
            for job in ("validate", "recovery-source"):
                self.assertFalse(job_runs(self.jobs[job], {"detect-release": status}, context))

    def test_event_gate_keeps_push_and_manual_paths_and_refuses_failed_producer(self):
        for event in ("push", "workflow_dispatch", "workflow_run"):
            for conclusion in ("success", "failure", "cancelled", "skipped"):
                payload = {"workflow_run": {"conclusion": conclusion}} if event == "workflow_run" else {}
                context = {"github": {"event_name": event, "event": payload}}
                self.assertEqual(job_runs(self.jobs["detect-release"], {}, context),
                                 event != "workflow_run" or conclusion == "success")


if __name__ == "__main__":
    unittest.main()
