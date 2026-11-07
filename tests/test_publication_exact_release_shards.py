"""Exact-release publication sharding, offline and without publication authority."""

import json
import os
from pathlib import Path
import subprocess
import unittest

import yaml

from tests import test_unit_test_shards as fixtures


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/publish-rc-image.yml"


class ExactReleaseShardTests(fixtures.ShardFixture):
    MODULES = {**fixtures.MODULES, "test_expected.py": """
import unittest
class Expected(unittest.TestCase):
    @unittest.expectedFailure
    def test_known_failure(self):
        self.fail('synthetic expected failure')
"""}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.git("commit", "--allow-empty", "-qm", "Later workflow trigger, same tree")
        cls.trigger = cls.git("rev-parse", "HEAD")
        cls.git("checkout", "-q", cls.head)
        cls.env["GITHUB_SHA"] = cls.trigger

    @classmethod
    def cli(cls, *args, env=None, root=None):
        return super().cli(*args, "--expected-source-sha", cls.head, env=env, root=root)

    def test_exact_release_uses_normal_plan_and_complete_discovery(self):
        self.assertNotEqual(self.head, self.trigger)
        self.assertEqual(json.loads(self.cli("count").stdout), [0, 1, 2])
        result = self.aggregate(self.copies())
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["source_sha"], self.head)
        self.assertEqual(summary["discovered_count"], 8)
        self.assertEqual(summary["outcomes"], {"success": 6, "skipped": 1, "expected_failure": 1})
        self.assertEqual(sum(summary["outcomes"].values()), summary["discovered_count"])

    def test_missing_exact_release_shard_refuses(self):
        self.assertRefused(self.aggregate(self.copies()[:-1]), "shard_manifest_count_mismatch")

    def test_failing_exact_release_shard_refuses(self):
        manifests = self.copies()
        shard = next(m["shard"] for m in manifests if "test_alpha.Alpha.test_two" in m["assigned"])
        output = self.out("failed.json")
        result = self.cli("run", "--shard", str(shard), "--output", str(output),
                          env={**self.env, "SYNTHETIC_FAIL": "1"})
        self.assertEqual(result.returncode, 1, result.stderr)
        manifests[shard] = json.loads(output.read_text())
        self.assertRefused(self.aggregate(manifests), "test_did_not_succeed")

    def test_exact_release_discovery_count_mismatch_refuses(self):
        manifests = self.copies()
        manifests[0]["discovered_count"] += 1
        self.assertRefused(self.aggregate(manifests), "shard_discovery_or_plan_mismatch")

    def test_trigger_commit_manifests_cannot_replace_release_execution(self):
        manifests = self.copies()
        for manifest in manifests:
            manifest["source_sha"] = self.trigger
        self.assertRefused(self.aggregate(manifests), "shard_source_mismatch")

    def test_wrong_checkout_refuses_before_plan_execution_or_aggregation(self):
        for args in (("count",), ("run", "--shard", "0", "--output", str(self.out("wrong.json"))),
                     ("aggregate", "--manifests", str(self.out("absent")))):
            with self.subTest(operation=args[0]):
                result = fixtures.ShardFixture.cli.__func__(
                    type(self), *args, "--expected-source-sha", self.trigger)
                self.assertRefused(result, "exact_release_checkout_mismatch")
        self.assertFalse(self.out("wrong.json").exists())


class ExactReleaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]

    def test_all_publication_phases_depend_on_aggregation(self):
        for name in ("claim", "build", "verify-image", "promote"):
            with self.subTest(job=name):
                self.assertIn("verify-release-aggregate", self.jobs[name]["needs"])
                self.assertIn("verify-release", self.jobs[name]["needs"])
                self.assertIn("validate", self.jobs[name]["needs"])
                self.assertEqual(self.jobs[name]["if"], "needs.detect-release.outputs.release_action == 'publish'")
        aggregate = self.jobs["verify-release-aggregate"]
        self.assertEqual(aggregate["needs"], ["verify-release", "verify-release-shards"])
        self.assertEqual(aggregate["if"], "${{ always() && !cancelled() && needs.verify-release.result == 'success' }}")

    def test_matrix_is_exact_release_plan_and_cannot_fail_open(self):
        prepare = self.jobs["verify-release"]
        self.assertEqual(prepare["outputs"]["release_shards"], "${{ steps.prepare.outputs.release_shards }}")
        script = next(s["run"] for s in prepare["steps"] if s.get("id") == "prepare")
        self.assertIn('count --repo-root . --expected-source-sha "$release_sha"', script)
        self.assertNotIn("python -m unittest discover", script)
        shard = self.jobs["verify-release-shards"]
        self.assertEqual(shard["needs"], "verify-release")
        self.assertNotIn("if", shard)
        self.assertEqual(shard["strategy"], {"fail-fast": False, "matrix": {
            "shard": "${{ fromJSON(needs.verify-release.outputs.release_shards) }}"}})
        self.assertEqual(shard["timeout-minutes"], 30)
        for name in ("verify-release-shards", "verify-release-aggregate"):
            job = self.jobs[name]
            self.assertEqual(job["permissions"], {"contents": "read"})
            self.assertNotIn("continue-on-error", job)
            self.assertEqual(job["runs-on"], "ubuntu-24.04")
            checkout = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@"))
            self.assertEqual(checkout["with"]["ref"], "${{ needs.verify-release.outputs.release_sha }}")
            self.assertIs(checkout["with"]["persist-credentials"], False)
            scripts = "\n".join(s.get("run", "") for s in job["steps"])
            self.assertIn('git show "${WORKFLOW_AUTHORITY_SHA}:scripts/unit_test_shards.py"', scripts)
            self.assertIn('git show "${WORKFLOW_AUTHORITY_SHA}:tests/requirements.lock"', scripts)
            self.assertIn('--expected-source-sha "$RELEASE_SHA"', scripts)
            for step in job["steps"]:
                self.assertNotIn("continue-on-error", step)

    def test_artifacts_are_distinct_from_ci_and_bound_to_run_attempt(self):
        steps = self.jobs["verify-release-shards"]["steps"]
        upload = next(s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@"))
        self.assertEqual(upload["if"], "${{ always() && needs.verify-release.outputs.full_suite_validation == 'validate_release_source' }}")
        self.assertEqual(upload["with"]["if-no-files-found"], "error")
        self.assertEqual(upload["with"]["name"],
                         "release-unit-test-shard-${{ matrix.shard }}-${{ github.run_id }}-${{ github.run_attempt }}")
        steps = self.jobs["verify-release-aggregate"]["steps"]
        download = next(s for s in steps if s.get("uses", "").startswith("actions/download-artifact@"))
        self.assertEqual(download["with"]["pattern"],
                         "release-unit-test-shard-*-${{ github.run_id }}-${{ github.run_attempt }}")
        self.assertNotIn("merge-multiple", download["with"])
        proof = steps[-1]
        self.assertIn('aggregate', proof["run"])
        self.assertIn('--manifests "$RUNNER_TEMP/release-unit-test-shards"', proof["run"])
        self.assertIn('>> "$GITHUB_STEP_SUMMARY"', proof["run"])

    def test_actual_gate_shell_rejects_missing_failed_cancelled_or_unknown_path(self):
        step = self.jobs["verify-release-aggregate"]["steps"][0]
        self.assertEqual(step["env"], {
            "VALIDATION_MODE": "${{ needs.verify-release.outputs.full_suite_validation }}",
            "SHARD_RESULT": "${{ needs.verify-release-shards.result }}"})
        for mode in ("reuse_same_run", "validate_release_source", "unknown", ""):
            for result in ("success", "failure", "skipped", "cancelled", ""):
                with self.subTest(mode=mode, result=result):
                    completed = subprocess.run(["bash", "-c", step["run"]], capture_output=True, text=True,
                                               env=dict(os.environ, VALIDATION_MODE=mode, SHARD_RESULT=result), timeout=10)
                    accepted = (mode, result) in (("reuse_same_run", "success"), ("validate_release_source", "success"))
                    self.assertEqual(completed.returncode, 0 if accepted else 1, completed.stderr)


if __name__ == "__main__":
    unittest.main()
