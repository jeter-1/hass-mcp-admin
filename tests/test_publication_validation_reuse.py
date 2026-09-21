"""Same-run publication reuse; no GitHub or publishing access is used."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/publication_validation.py"
spec = importlib.util.spec_from_file_location("publication_validation", SCRIPT)
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


class PublicationValidationReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Synthetic test")
        self.git("config", "user.email", "synthetic@example.invalid")
        for path in validation.BOUND_FILES:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(SCRIPT.read_bytes() if path == "scripts/publication_validation.py"
                               else ("synthetic " + path + "\n").encode())
        self.git("add", ".")
        self.git("commit", "-qm", "Synthetic source")
        self.head = self.git("rev-parse", "HEAD")
        self.env = {
            "GITHUB_REPOSITORY": validation.REPOSITORY,
            "GITHUB_WORKFLOW_REF": validation.REPOSITORY + "/" + validation.WORKFLOW + "@refs/heads/main",
            "GITHUB_SHA": self.head, "GITHUB_WORKFLOW_SHA": self.head,
            "GITHUB_RUN_ID": "1001", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_EVENT_NAME": "workflow_dispatch", "FULL_SUITE_OUTCOME": "success",
            "COMPLETE_CI_RESULT": "success", "DETECTED_RELEASE_SHA": self.head,
        }
        self.receipt = validation.record(self.root, self.env)
        self.env["SOURCE_VALIDATION_RECEIPT"] = json.dumps(self.receipt)
        self.lock = Path(self.temp.name) / "installed.lock"
        self.lock.write_bytes((self.root / "tests/requirements.lock").read_bytes())

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args],
                                       stderr=subprocess.DEVNULL, text=True).strip()

    def decide(self, env=None):
        return validation.decide(self.root, env or self.env, self.lock)

    def test_exact_same_run_reuses_successful_full_discovery(self):
        self.assertEqual(self.decide(), "reuse_same_run")
        self.assertLess(len(json.dumps(self.receipt).encode()), validation.MAX_RECEIPT)

    def test_verified_handoff_event_keeps_exact_same_run_validation_binding(self):
        self.env["GITHUB_EVENT_NAME"] = "workflow_run"
        self.env["SOURCE_VALIDATION_RECEIPT"] = json.dumps(validation.record(self.root, self.env))
        self.assertEqual(self.decide(), "reuse_same_run")
        with self.assertRaises(validation.Refusal):
            self.decide(dict(self.env, GITHUB_RUN_ATTEMPT="2"))

    def test_historical_source_runs_discovery_even_when_trees_are_identical(self):
        self.git("commit", "--allow-empty", "-qm", "New workflow authority")
        trigger = self.git("rev-parse", "HEAD")
        self.env.update(GITHUB_SHA=trigger, GITHUB_WORKFLOW_SHA=trigger)
        self.env["SOURCE_VALIDATION_RECEIPT"] = json.dumps(validation.record(self.root, self.env))
        self.git("checkout", "-q", self.head)
        self.assertEqual(self.decide(), "validate_release_source")

    def test_changed_historical_source_still_runs_its_own_discovery(self):
        (self.root / "tests/requirements.lock").write_text("new synthetic lock\n")
        self.git("commit", "-qam", "New test dependencies")
        trigger = self.git("rev-parse", "HEAD")
        self.env.update(GITHUB_SHA=trigger, GITHUB_WORKFLOW_SHA=trigger)
        self.env["SOURCE_VALIDATION_RECEIPT"] = json.dumps(validation.record(self.root, self.env))
        self.lock.write_bytes((self.root / "tests/requirements.lock").read_bytes())
        self.git("checkout", "-q", self.head)
        self.assertEqual(self.decide(), "validate_release_source")

    def test_incomplete_ci_never_permits_publication_or_isolated_revalidation(self):
        for result in (None, "", "failure", "cancelled", "skipped", "neutral"):
            with self.subTest(result=result), self.assertRaisesRegex(validation.Refusal, "complete_ci"):
                self.decide(dict(self.env, COMPLETE_CI_RESULT=result))

    def test_unsuccessful_unit_step_cannot_emit_success_receipt(self):
        for result in (None, "", "failure", "cancelled", "skipped"):
            with self.subTest(result=result), self.assertRaisesRegex(validation.Refusal, "full_suite"):
                validation.record(self.root, dict(self.env, FULL_SUITE_OUTCOME=result))

    def test_every_receipt_identity_is_checked(self):
        for key, value in self.receipt.items():
            mutated = copy.deepcopy(self.receipt)
            mutated[key] = "altered"
            with self.subTest(key=key), self.assertRaises(validation.Refusal):
                self.decide(dict(self.env, SOURCE_VALIDATION_RECEIPT=json.dumps(mutated)))
        for path in validation.BOUND_FILES:
            mutated = copy.deepcopy(self.receipt)
            mutated["file_sha256"][path] = "0" * 64
            with self.subTest(path=path), self.assertRaises(validation.Refusal):
                self.decide(dict(self.env, SOURCE_VALIDATION_RECEIPT=json.dumps(mutated)))

    def test_closed_receipt_and_exact_types(self):
        for raw in (None, "", "[1]", "null", "{", "x" * 4097,
                    '{"schema_version":1,"schema_version":1}',
                    json.dumps(dict(self.receipt, extra="unreviewed")),
                    json.dumps(dict(self.receipt, schema_version=True))):
            with self.subTest(raw_type=type(raw)), self.assertRaises(validation.Refusal):
                self.decide(dict(self.env, SOURCE_VALIDATION_RECEIPT=raw))

    def test_wrong_current_context_and_stale_receipts_are_refused(self):
        for key, value in {
            "GITHUB_REPOSITORY": "other/repo", "GITHUB_WORKFLOW_REF": "other/workflow",
            "GITHUB_SHA": "0" * 40, "GITHUB_WORKFLOW_SHA": "1" * 40,
            "GITHUB_RUN_ID": "1002", "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_EVENT_NAME": "pull_request", "DETECTED_RELEASE_SHA": "2" * 40,
        }.items():
            with self.subTest(key=key), self.assertRaises(validation.Refusal):
                self.decide(dict(self.env, **{key: value}))

    def test_wrong_interpreter_and_installed_dependency_lock_are_refused(self):
        with self.assertRaises(validation.Refusal):
            validation.decide(self.root, self.env, self.lock, python_version="0.0.0")
        self.lock.write_text("changed\n")
        with self.assertRaisesRegex(validation.Refusal, "installed_lock_mismatch"):
            self.decide()
        self.lock.unlink()
        with self.assertRaisesRegex(validation.Refusal, "installed_lock_unavailable"):
            self.decide()

    def test_dirty_and_mismatched_validation_source_cannot_emit_receipt(self):
        with self.assertRaises(validation.Refusal):
            validation.record(self.root, dict(self.env, GITHUB_SHA="0" * 40))
        (self.root / "untracked.txt").write_text("change")
        with self.assertRaisesRegex(validation.Refusal, "dirty_validation_source"):
            validation.record(self.root, self.env)
        with self.assertRaisesRegex(validation.Refusal, "dirty_validation_source"):
            self.decide()

    def test_cli_emits_single_bounded_output_and_refuses_without_leaking_receipt(self):
        output = Path(self.temp.name) / "github-output"
        env = dict(os.environ, **self.env, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, str(SCRIPT), "record", "--repo-root",
                                 str(self.root), "--github-output", str(output)],
                                env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = output.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0].split("=", 1)[1]), self.receipt)
        env["SOURCE_VALIDATION_RECEIPT"] = "synthetic-sensitive-untrusted-content"
        result = subprocess.run([sys.executable, str(SCRIPT), "decide", "--repo-root",
                                 str(self.root), "--installed-lock", str(self.lock)],
                                env=env, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(env["SOURCE_VALIDATION_RECEIPT"], result.stdout + result.stderr)


class PublicationValidationWiringTests(unittest.TestCase):
    def test_receipt_is_success_only_output_not_an_alternative_ci_gate(self):
        ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        output = ci[True]["workflow_call"]["outputs"]["source_validation"]
        self.assertEqual(output["value"], "${{ jobs.validate_source.outputs.source_validation }}")
        source = ci["jobs"]["validate_source"]
        self.assertEqual(source["outputs"]["source_validation"],
                         "${{ steps.source_receipt.outputs.source_validation }}")
        unit, receipt = source["steps"][-2:]
        self.assertEqual(unit["run"], validation.COMMAND)
        self.assertEqual(receipt["env"]["FULL_SUITE_OUTCOME"], "${{ steps.unit_tests.outcome }}")
        for step in (unit, receipt):
            self.assertNotIn("continue-on-error", step)
            self.assertNotIn("if", step)
        self.assertEqual(set(ci["jobs"]["validate"]["needs"]), set(ci["jobs"]) - {"validate"})

    def test_publisher_uses_protected_trigger_verifier_and_complete_ci_result(self):
        publisher = yaml.safe_load((ROOT / ".github/workflows/publish-rc-image.yml").read_text())
        self.assertEqual(publisher["jobs"]["validate"]["uses"], "./.github/workflows/ci.yml")
        prepare = next(s for s in publisher["jobs"]["promote"]["steps"] if s.get("id") == "prepare")
        self.assertEqual(prepare["env"]["COMPLETE_CI_RESULT"], "${{ needs.validate.result }}")
        self.assertEqual(prepare["env"]["SOURCE_VALIDATION_RECEIPT"], "${{ needs.validate.outputs.source_validation }}")
        script = prepare["run"]
        self.assertIn('git show "${TRIGGER_SHA}:scripts/publication_validation.py"', script)
        start = script.index('if [[ "$validation_mode" == "validate_release_source" ]]')
        end = script.index('echo "full_suite_validation=', start)
        # Execute the actual workflow branch with a disposable command counter.
        for mode, expected_calls, expected_status in (("reuse_same_run", 0, 0),
                                                       ("validate_release_source", 1, 0),
                                                       ("unexpected", 0, 1)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                log = Path(directory) / "calls"
                wrapper = 'python() { printf "%s\\n" "$*" >> "$CALL_LOG"; };\n'
                result = subprocess.run(["bash", "-e", "-c", wrapper + script[start:end]],
                                        env=dict(os.environ, validation_mode=mode, CALL_LOG=str(log)),
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, expected_status)
                calls = log.read_text().splitlines() if log.exists() else []
                self.assertEqual(calls, ["-m unittest discover -s tests -v"] * expected_calls)


if __name__ == "__main__":
    unittest.main()
