"""Offline publisher -> reusable CI -> real harness entry-point regressions.

GitHub preserves the caller's event context in reusable workflows. Exercise the
declared publisher events and all candidate matrix rows through main/cleanup;
only Git checkout observation and disposable container operations are mocked.
These tests do not claim to run GitHub Actions or actual Core containers.
"""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import yaml
from tests.test_ha_mcp_850_candidate_lane import lane, ROOT


class PublicationCIContextTests(unittest.TestCase):
    def setUp(self):
        self.publisher = yaml.safe_load((ROOT / ".github/workflows/publish-rc-image.yml").read_text())
        self.ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        self.job = self.ci["jobs"]["exact-addon-runtime-acceptance"]
        self.rows = [row for row in self.job["strategy"]["matrix"]["include"]
                     if row.get("candidate_power")]
        self.env = {
            "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "jeter-1/hass-mcp-admin",
            "GITHUB_EVENT_NAME": "workflow_run", "GITHUB_REF": "refs/heads/main",
            "GITHUB_WORKFLOW_REF": "jeter-1/hass-mcp-admin/.github/workflows/publish-rc-image.yml@refs/heads/main",
            "GITHUB_SHA": "a" * 40, "GITHUB_WORKFLOW_SHA": "a" * 40,
            "GITHUB_JOB": "exact-addon-runtime-acceptance",
            "GITHUB_RUN_ID": "1234", "GITHUB_RUN_ATTEMPT": "1",
        }

    def invoke(self, row, runner, *, event="workflow_run", cleanup=False,
               overrides=None, checked_sha=None, assessment_error=None):
        name = ("Always reconcile owned container cleanup" if cleanup else
                "Verify power candidate with both exact upstream variants and Core")
        step = next(step for step in self.job["steps"] if step.get("name") == name)
        command = next(line for line in step["run"].splitlines() if line.startswith("python "))
        env = {**self.env, "GITHUB_EVENT_NAME": event, "RUNNER_TEMP": str(runner),
               "ASSESSMENT_ARCH": row["architecture"], "UPSTREAM_VERSION": row["upstream_version"],
               "ASSESSMENT_CORE": row["candidate_core_version"]}
        if event == "pull_request":
            env.update(GITHUB_REF="refs/pull/999/merge",
                       GITHUB_WORKFLOW_REF="jeter-1/hass-mcp-admin/.github/workflows/ci.yml@refs/pull/999/merge")
        env.update(overrides or {})
        output = io.StringIO()
        assess = AsyncMock(side_effect=assessment_error)
        with patch.dict(os.environ, env, clear=True), \
                patch.object(lane.subprocess, "check_output", return_value=checked_sha or env["GITHUB_SHA"]) as git, \
                patch.object(lane, "assess", assess), \
                patch.object(lane, "cleanup", return_value=[]) as clean, \
                patch.object(lane, "startup_diagnostics", return_value=[]) as diagnostics, \
                patch.object(lane, "docker", side_effect=AssertionError("No Docker in offline tests")), \
                patch.object(lane.logging, "disable"), redirect_stdout(output):
            args = shlex.split(os.path.expandvars(command))[1:]
            self.assertEqual(args[0], "scripts/ha_mcp_850_container_acceptance.py")
            with patch.object(sys, "argv", args):
                try:
                    lane.main()
                    result = 0
                except SystemExit as error:
                    result = error.code
                except lane.Refusal as error:
                    result = str(error)
        return result, output.getvalue(), assess, clean, git, diagnostics

    def test_every_publisher_trigger_reaches_all_candidate_entries_and_cleanup(self):
        triggers = self.publisher.get("on", self.publisher.get(True))
        self.assertEqual(set(triggers), {"push", "workflow_dispatch", "workflow_run"})
        self.assertEqual(triggers["workflow_run"], {
            "workflows": ["Merge owner-authorized pull request"], "types": ["completed"]})
        self.assertEqual(self.publisher["jobs"]["validate"]["uses"], "./.github/workflows/ci.yml")
        self.assertEqual(self.publisher["jobs"]["validate"]["permissions"], {"contents": "read"})
        self.assertNotIn("needs", self.publisher["jobs"]["detect-release"])
        self.assertEqual(self.publisher["jobs"]["validate"]["needs"], "detect-release")
        self.assertEqual(self.publisher["jobs"]["validate"]["if"],
                         "needs.detect-release.outputs.release_action == 'publish'")
        self.assertEqual(len(self.rows), 6)
        for event in (*triggers, "pull_request"):
            for row in self.rows:
                with self.subTest(event=event, row=row), tempfile.TemporaryDirectory() as tmp:
                    result, output, assess, clean, git, _ = self.invoke(row, Path(tmp), event=event)
                    self.assertEqual(result, 0, output)
                    assess.assert_awaited_once()
                    git.assert_called_once_with(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True)
                    identity = f"h{row['candidate_resource_code']}-1234-1-{row['architecture']}"
                    clean.assert_called_once_with(identity)
                    receipt = json.loads((Path(tmp) / (identity + "-evidence/receipt.json")).read_text())
                    self.assertEqual(receipt["status"], "PASS")
                    self.assertEqual(receipt["source"], self.env["GITHUB_SHA"])
                    result, output, assess, clean, _, _ = self.invoke(row, Path(tmp), event=event, cleanup=True)
                    self.assertEqual(result, 0, output)
                    clean.assert_called_once_with(identity)
                    assess.assert_not_awaited()
                    self.assertEqual(json.loads(output), {"cleanup": []})

    def test_workflow_run_refuses_invalid_context_before_assessment_or_cleanup(self):
        invalid = {
            "GITHUB_ACTIONS": ("false", ""),
            "GITHUB_REPOSITORY": ("foreign/repo", ""),
            "GITHUB_EVENT_NAME": ("workflow_call", "pull_request_target", "issue_comment", ""),
            "GITHUB_REF": ("refs/heads/feature", "refs/pull/999/merge", ""),
            "GITHUB_WORKFLOW_REF": (
                "jeter-1/hass-mcp-admin/.github/workflows/ci.yml@refs/heads/main",
                "jeter-1/hass-mcp-admin/.github/workflows/publish-rc-image.yml@refs/heads/feature",
                "foreign/repo/.github/workflows/publish-rc-image.yml@refs/heads/main", ""),
            "GITHUB_JOB": ("promote", ""), "GITHUB_SHA": ("bad", ""),
            "GITHUB_RUN_ID": ("../other", ""), "GITHUB_RUN_ATTEMPT": ("1\n", ""),
        }
        for field, values in invalid.items():
            for value in values:
                for cleanup in (False, True):
                    with self.subTest(field=field, value=value, cleanup=cleanup), tempfile.TemporaryDirectory() as tmp:
                        result, output, assess, clean, git, diagnostics = self.invoke(
                            self.rows[0], Path(tmp), cleanup=cleanup, overrides={field: value})
                        self.assertIsInstance(result, str)
                        self.assertEqual(output, "")
                        assess.assert_not_awaited()
                        clean.assert_not_called()
                        git.assert_not_called()
                        diagnostics.assert_not_called()
                        self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_workflow_run_checkout_mismatch_never_reaches_containers(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, output, assess, clean, _, _ = self.invoke(self.rows[0], Path(tmp), checked_sha="b" * 40)
            self.assertEqual(result, "candidate_checkout_mismatch")
            self.assertEqual(output, "")
            assess.assert_not_awaited()
            clean.assert_not_called()
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_workflow_run_assessment_failure_retains_failure_and_cleans_same_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, output, assess, clean, _, diagnostics = self.invoke(
                self.rows[0], Path(tmp), assessment_error=lane.Refusal("synthetic_assessment_failure"))
            self.assertEqual(result, 1)
            receipt = json.loads(output)
            self.assertEqual(receipt["status"], "FAIL")
            self.assertEqual(receipt["failure_category"], "synthetic_assessment_failure")
            assess.assert_awaited_once()
            clean.assert_called_once()
            diagnostics.assert_called_once_with(clean.call_args.args[0])
            result, _, assess, cleanup, _, _ = self.invoke(self.rows[0], Path(tmp), cleanup=True)
            self.assertEqual(result, 0)
            cleanup.assert_called_once_with(clean.call_args.args[0])
            assess.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
