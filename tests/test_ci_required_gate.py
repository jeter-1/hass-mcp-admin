"""Exercise the required CI result gate without running or publishing workflows."""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
REQUIRED_JOBS = (
    "validate_prerequisites",
    "validate_packaging",
    "validate_source",
    "real-ha-contract-tests",
    "prepare_exact_image_matrix",
    "exact-image-read-gateway",
    "exact-addon-runtime-acceptance",
)


class RequiredCIGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        cls.jobs = cls.workflow["jobs"]

    def test_required_check_waits_for_every_existing_job_family(self):
        # On RC9/GA, validate is an independent worker, so a failed real-HA
        # contract can coexist with the required check's success.
        gate = self.jobs["validate"]
        self.assertEqual(set(gate.get("needs", ())), set(REQUIRED_JOBS))
        self.assertEqual(set(gate["needs"]), set(self.jobs) - {"validate"})

    def _step(self):
        return next(
            step
            for step in self.jobs["validate"]["steps"]
            if step.get("name") == "Require every CI job family to succeed"
        )

    def _results(self):
        return {name: {"result": "success", "outputs": {}} for name in REQUIRED_JOBS}

    def _run(self, results, *, raw=False):
        env = {"PATH": os.defpath}
        if results is not None:
            env["REQUIRED_JOB_RESULTS"] = results if raw else json.dumps(results)
        return subprocess.run(
            [sys.executable, "-c", self._step()["run"]],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def test_successful_source_and_documentation_changes_are_not_filtered(self):
        events = self.workflow.get("on", self.workflow.get(True))
        self.assertEqual(
            events["pull_request"],
            {"types": ["opened", "reopened", "synchronize"]},
        )
        self.assertIn("workflow_call", events)
        for name in REQUIRED_JOBS:
            self.assertNotIn("if", self.jobs[name])
        result = self._run(self._results())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("All required CI job families succeeded", result.stdout)

    def test_each_failed_cancelled_or_skipped_family_refuses(self):
        for name in REQUIRED_JOBS:
            for outcome in ("failure", "cancelled", "skipped", "timed_out"):
                with self.subTest(job=name, outcome=outcome):
                    results = self._results()
                    results[name]["result"] = outcome
                    result = self._run(results)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(name, result.stderr)

    def test_failed_matrix_preparation_and_skipped_gateway_refuse(self):
        results = self._results()
        results["prepare_exact_image_matrix"]["result"] = "failure"
        results["exact-image-read-gateway"]["result"] = "skipped"
        result = self._run(results)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("prepare_exact_image_matrix", result.stderr)
        self.assertIn("exact-image-read-gateway", result.stderr)

    def test_failed_preflight_cannot_skip_source_and_packaging_into_success(self):
        results = self._results()
        results["validate_prerequisites"]["result"] = "failure"
        results["validate_source"]["result"] = "skipped"
        results["validate_packaging"]["result"] = "skipped"
        result = self._run(results)
        self.assertNotEqual(result.returncode, 0)
        for name in ("validate_prerequisites", "validate_source", "validate_packaging"):
            self.assertIn(name, result.stderr)

    def test_successful_preparation_cannot_hide_failed_matrix_child(self):
        # GitHub reports a failed matrix job family when any required child
        # fails. A successful preparation job does not supersede that result.
        results = self._results()
        results["exact-image-read-gateway"]["result"] = "failure"
        self.assertNotEqual(self._run(results).returncode, 0)
        for name in (
            "real-ha-contract-tests",
            "exact-image-read-gateway",
            "exact-addon-runtime-acceptance",
        ):
            self.assertFalse(self.jobs[name]["strategy"]["fail-fast"])
            self.assertFalse(self.jobs[name].get("continue-on-error", False))

    def test_missing_or_additional_job_results_refuse(self):
        for name in REQUIRED_JOBS:
            with self.subTest(missing=name):
                results = self._results()
                del results[name]
                self.assertNotEqual(self._run(results).returncode, 0)
        results = self._results()
        results["unexpected-job"] = {"result": "success"}
        self.assertNotEqual(self._run(results).returncode, 0)

    def test_missing_unknown_or_malformed_result_never_becomes_success(self):
        for record in (
            None, [], "success", {}, {"result": None}, {"result": True},
            {"result": "neutral"}, {"result": "timed_out"},
            {"result": "SUCCESS"}, {"result": ["success"]},
        ):
            with self.subTest(record=record):
                results = self._results()
                results["validate_source"] = record
                self.assertNotEqual(self._run(results).returncode, 0)

    def test_missing_malformed_or_wrong_shape_payload_refuses(self):
        for payload in (None, "", "{", "null", "[]", '"success"', "false"):
            with self.subTest(payload=payload):
                self.assertNotEqual(self._run(payload, raw=True).returncode, 0)

    def test_workflow_cancellation_refuses_even_when_dependencies_succeeded(self):
        self.assertEqual(self._run(self._results()).returncode, 0)
        cancellation = self.jobs["validate"]["steps"][-1]
        self.assertEqual(cancellation["name"], "Refuse workflow cancellation")
        self.assertEqual(cancellation["if"], "${{ cancelled() }}")
        result = subprocess.run(
            [sys.executable, "-c", cancellation["run"]],
            env={"PATH": os.defpath}, capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("workflow was cancelled", result.stderr)

    def test_status_functions_are_only_used_in_supported_conditions(self):
        # GitHub accepts status functions in `if`, not step env/run expressions.
        # YAML parsing and Python execution alone do not validate that boundary.
        for step in self.jobs["validate"]["steps"]:
            for key, value in step.items():
                if key != "if":
                    self.assertNotRegex(
                        json.dumps(value), r"\$\{\{[^}]*\b(?:always|cancelled|success|failure)\("
                    )

    def test_outputs_do_not_substitute_for_job_results(self):
        results = self._results()
        results["validate_source"] = {
            "result": "failure",
            "outputs": {"result": "success", "conclusion": "success"},
        }
        self.assertNotEqual(self._run(results).returncode, 0)

    def test_ordinary_matrix_outputs_are_allowed_without_being_printed(self):
        results = self._results()
        results["prepare_exact_image_matrix"]["outputs"] = {
            "matrix": '{"include":[{"lane":"synthetic-reviewed-lane"}]}'
        }
        result = self._run(results)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("synthetic-reviewed-lane", result.stdout + result.stderr)

    def test_failure_diagnostics_do_not_echo_untrusted_result_or_outputs(self):
        marker = "synthetic-private-marker\n::warning::untrusted"
        results = self._results()
        results["validate_source"] = {"result": marker, "outputs": {"text": marker}}
        result = self._run(results)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(marker, result.stdout + result.stderr)
        self.assertNotIn("::warning::", result.stdout + result.stderr)

    def test_public_check_name_remains_unique_and_gate_always_observes_results(self):
        gate = self.jobs["validate"]
        self.assertEqual(gate.get("name", "validate"), "validate")
        self.assertEqual(
            sum(job.get("name", name) == "validate" for name, job in self.jobs.items()),
            1,
        )
        self.assertEqual(gate["if"], "${{ always() }}")
        self.assertEqual(self._step()["if"], "${{ always() }}")
        self.assertEqual(self._step()["shell"], "python")
        self.assertEqual(
            self._step()["env"],
            {
                "REQUIRED_JOB_RESULTS": "${{ toJSON(needs) }}",
            },
        )
        self.assertFalse(gate.get("continue-on-error", False))
        for step in gate["steps"]:
            self.assertFalse(step.get("continue-on-error", False))

    def test_aggregate_is_bounded_and_has_no_checkout_action_or_write_permission(self):
        gate = self.jobs["validate"]
        self.assertGreater(gate["timeout-minutes"], 0)
        self.assertLessEqual(gate["timeout-minutes"], 5)
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertEqual(len(gate["steps"]), 2)
        for step in gate["steps"]:
            self.assertNotIn("uses", step)
            self.assertEqual(step["shell"], "python")
        self.assertNotIn("permissions", gate)


if __name__ == "__main__":
    unittest.main()
