import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
CODEX_RECEIPT_WORKFLOW = ROOT / ".github" / "workflows" / "codex-review-receipt.yml"
LEGACY_CODEX_WORKFLOW = (
    ROOT / ".github" / "workflows" / "codex-independent-review.yml"
)
AUTO_MERGE_WORKFLOW = ROOT / ".github" / "workflows" / "enable-auto-merge.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
WORKFLOW_GUIDE = ROOT / "docs" / "CODEX_WORKFLOW.md"
ROOT_INSTRUCTIONS = ROOT / "AGENTS.md"
RECEIPT_VALIDATOR = ROOT / "scripts" / "validate_native_codex_review.py"


def load_yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"workflow is not a mapping: {path}")
    return value


def workflow_events(workflow: dict) -> dict:
    return workflow.get("on", workflow.get(True))


class ReviewWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.receipt = load_yaml(CODEX_RECEIPT_WORKFLOW)
        cls.auto_merge = load_yaml(AUTO_MERGE_WORKFLOW)
        cls.ci = load_yaml(CI_WORKFLOW)

    def test_paid_codex_action_is_not_part_of_the_repository(self):
        self.assertFalse(LEGACY_CODEX_WORKFLOW.exists())
        self.assertFalse(
            (ROOT / ".github" / "codex" / "prompts" / "independent-review.md").exists()
        )
        self.assertFalse(
            (ROOT / ".github" / "codex" / "review-schema.json").exists()
        )
        self.assertFalse((ROOT / "scripts" / "validate_codex_review.py").exists())

        repository_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                CODEX_RECEIPT_WORKFLOW,
                AUTO_MERGE_WORKFLOW,
                WORKFLOW_GUIDE,
                ROOT_INSTRUCTIONS,
            )
        )
        self.assertNotIn("OPENAI_API_KEY", repository_text)
        self.assertNotIn("openai/codex-action", repository_text)

    def test_native_codex_receipt_gate_has_read_only_authority(self):
        events = workflow_events(self.receipt)
        self.assertEqual(
            set(events["pull_request_target"]["types"]),
            {"opened", "reopened", "synchronize", "ready_for_review"},
        )
        job = self.receipt["jobs"]["codex-review-receipt"]
        self.assertEqual(job["name"], "codex-review-receipt-observer")
        self.assertEqual(self.receipt["permissions"], {})
        self.assertEqual(
            job["permissions"],
            {
                "contents": "read",
                "issues": "read",
                "pull-requests": "read",
                "statuses": "write",
            },
        )
        self.assertIn("draft == false", job["if"])
        self.assertIn("base.ref == 'main'", job["if"])
        self.assertIn("head.repo.full_name == github.repository", job["if"])

        checkout = next(
            step for step in job["steps"] if str(step.get("uses", "")).startswith("actions/checkout@")
        )
        self.assertRegex(checkout["uses"], r"^actions/checkout@[0-9a-f]{40}$")
        self.assertEqual(checkout["with"]["ref"], "${{ github.event.pull_request.base.sha }}")
        self.assertFalse(checkout["with"]["persist-credentials"])

        script = str(job["steps"][-1]["run"])
        self.assertIn("BASE_SHA", script)
        self.assertIn("EXPECTED_HEAD_SHA", script)
        self.assertIn("scripts/validate_native_codex_review.py", script)
        self.assertIn("context=\"codex-review-receipt\"", script)
        self.assertEqual(script.count("gh api --paginate"), 2)
        self.assertEqual(script.count("jq -s 'add'"), 2)

    def test_ready_event_arms_native_auto_merge_without_checkout_or_merge_bypass(self):
        events = workflow_events(self.auto_merge)
        self.assertEqual(
            events,
            {
                "pull_request_target": {
                    "types": ["ready_for_review", "synchronize"]
                }
            },
        )
        job = self.auto_merge["jobs"]["authorize-auto-merge"]
        self.assertIn("github.event.action == 'ready_for_review'", job["if"])
        self.assertIn("github.actor == 'jeter-1'", job["if"])
        self.assertIn("base.ref == 'main'", job["if"])
        self.assertEqual(
            job["permissions"],
            {
                "contents": "write",
                "pull-requests": "write",
                "statuses": "read",
            },
        )
        self.assertFalse(any("uses" in step for step in job["steps"]))
        step = job["steps"][0]
        self.assertEqual(step["env"]["RECEIPT_MAX_ATTEMPTS"], "105")
        self.assertEqual(step["env"]["RECEIPT_POLL_SECONDS"], "20")
        script = str(step["run"])
        self.assertIn("current_head_sha", script)
        self.assertIn("AUTHORIZED_HEAD_SHA", script)
        self.assertIn("gh pr merge", script)
        self.assertIn("--auto", script)
        self.assertIn("--merge", script)
        self.assertIn("--match-head-commit", script)
        self.assertIn("codex-review-receipt", script)
        self.assertIn("receipt_state", script)
        self.assertIn("missing|pending", script)
        self.assertIn("failure|error", script)
        self.assertIn("unknown state", script)
        self.assertLess(script.index("receipt_state"), script.index("gh pr merge"))
        self.assertNotIn("--admin", script)
        self.assertNotIn("--force", script)

    def test_auto_merge_is_blocked_until_exact_head_receipt_succeeds(self):
        job = self.auto_merge["jobs"]["authorize-auto-merge"]
        step = job["steps"][0]
        script = str(step["run"])
        head = "4deb1d30edc7ccb8ced7c8438930ca1310c3775b"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gh_log = root / "gh.log"
            summary = root / "summary.md"
            fake_gh = root / "gh"
            fake_gh.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$MOCK_GH_LOG"
if [[ "$1" == "api" && "$2" == *"/pulls/"* ]]; then
  printf '%s\\n' "$AUTHORIZED_HEAD_SHA"
elif [[ "$1" == "api" && "$2" == *"/commits/"*"/status" ]]; then
  printf '%s\\n' "$MOCK_RECEIPT_STATE"
elif [[ "$1" == "pr" && "$2" == "merge" ]]; then
  exit 0
else
  printf 'unexpected gh invocation: %s\\n' "$*" >&2
  exit 2
fi
""",
                encoding="utf-8",
            )
            fake_gh.chmod(0o700)

            for state, expected_returncode in (
                ("success", 0),
                ("missing", 1),
                ("pending", 1),
                ("failure", 1),
                ("error", 1),
                ("unexpected", 1),
            ):
                with self.subTest(receipt_state=state):
                    gh_log.write_text("", encoding="utf-8")
                    result = subprocess.run(
                        ["bash", "-c", script],
                        cwd=ROOT,
                        env={
                            **os.environ,
                            "AUTHORIZED_HEAD_SHA": head,
                            "GH_TOKEN": "test-token",
                            "GITHUB_STEP_SUMMARY": str(summary),
                            "MOCK_GH_LOG": str(gh_log),
                            "MOCK_RECEIPT_STATE": state,
                            "PATH": f"{root}:{os.environ['PATH']}",
                            "PR_NUMBER": "164",
                            "RECEIPT_MAX_ATTEMPTS": "1",
                            "RECEIPT_POLL_SECONDS": "0",
                            "REPOSITORY": "jeter-1/hass-mcp-admin",
                        },
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(
                        result.returncode,
                        expected_returncode,
                        result.stderr,
                    )
                    merge_was_reached = any(
                        line.startswith("pr merge ")
                        for line in gh_log.read_text(encoding="utf-8").splitlines()
                    )
                    self.assertEqual(merge_was_reached, state == "success")

    def test_head_change_withdraws_ready_authorization_and_disarms_auto_merge(self):
        job = self.auto_merge["jobs"]["revoke-auto-merge-on-head-change"]
        self.assertEqual(job["name"], "revoke-auto-merge-on-head-change")
        self.assertIn("github.event.action == 'synchronize'", job["if"])
        self.assertIn("base.ref == 'main'", job["if"])
        self.assertIn("head.repo.full_name == github.repository", job["if"])
        self.assertEqual(job["permissions"], {"pull-requests": "write"})
        self.assertFalse(any("uses" in step for step in job["steps"]))
        script = str(job["steps"][0]["run"])
        self.assertIn("autoMergeRequest", script)
        self.assertIn("--disable-auto", script)
        self.assertNotIn("--admin", script)
        self.assertNotIn("--force", script)

    def test_ci_runs_once_per_pull_request_and_is_reused_on_main(self):
        events = workflow_events(self.ci)
        self.assertNotIn("push", events)
        self.assertIn("pull_request", events)
        self.assertIn("workflow_call", events)
        self.assertIn("ready_for_review", events["pull_request"]["types"])

    def test_native_review_activation_and_exact_head_contract_are_documented(self):
        guide = WORKFLOW_GUIDE.read_text(encoding="utf-8")
        self.assertIn("Codex cloud repository access", guide)
        self.assertIn("`@codex review`", guide)
        self.assertIn("does not use\nan OpenAI API key or API billing", guide)
        self.assertIn("exact current head", guide)
        self.assertIn("load only from the\nprotected base commit", guide)
        self.assertIn("service-side eligibility policy", guide)


class NativeCodexReceiptValidationTests(unittest.TestCase):
    HEAD = "4deb1d30edc7ccb8ced7c8438930ca1310c3775b"

    def run_validator(self, *, comments: list[dict], reviews: list[dict]):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            comments_path = root / "comments.json"
            reviews_path = root / "reviews.json"
            output_path = root / "receipt.json"
            comments_path.write_text(json.dumps(comments), encoding="utf-8")
            reviews_path.write_text(json.dumps(reviews), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(RECEIPT_VALIDATOR),
                    "--head",
                    self.HEAD,
                    "--comments",
                    str(comments_path),
                    "--reviews",
                    str(reviews_path),
                    "--output",
                    str(output_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            payload = (
                json.loads(output_path.read_text(encoding="utf-8"))
                if output_path.exists()
                else None
            )
            return result, payload

    def summary(self, *, status: str, commit_ref: str = "4deb1d3") -> dict:
        return {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": (
                "<!-- codex-pull-request-review-summary -->\n"
                "| Review | Status | Commit | Review trigger |\n"
                "| --- | --- | --- | --- |\n"
                f"| 📝 **Code Review** | {status} | `{commit_ref}` | Manual request |"
            ),
        }

    def test_exact_submitted_review_is_complete(self):
        result, payload = self.run_validator(
            comments=[],
            reviews=[
                {
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "commit_id": self.HEAD,
                    "state": "COMMENTED",
                }
            ],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["evidence_kind"], "submitted_review")
        self.assertTrue(payload["exact"])

    def test_completed_summary_requires_server_side_commit_resolution(self):
        result, payload = self.run_validator(
            comments=[self.summary(status="✅ **Completed** 2 minutes ago")],
            reviews=[],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["evidence_kind"], "completed_summary")
        self.assertEqual(payload["commit_ref"], "4deb1d3")
        self.assertFalse(payload["exact"])

    def test_receipt_after_first_hundred_records_is_recognized(self):
        filler = [{"user": {"login": "unrelated-bot"}, "body": "filler"}]
        cases = (
            {
                "comments": filler * 100
                + [self.summary(status="✅ **Completed** 2 minutes ago")],
                "reviews": [],
                "evidence_kind": "completed_summary",
            },
            {
                "comments": [],
                "reviews": filler * 100
                + [
                    {
                        "user": {"login": "chatgpt-codex-connector[bot]"},
                        "commit_id": self.HEAD,
                        "state": "COMMENTED",
                    }
                ],
                "evidence_kind": "submitted_review",
            },
        )
        for case in cases:
            with self.subTest(evidence_kind=case["evidence_kind"]):
                result, payload = self.run_validator(
                    comments=case["comments"], reviews=case["reviews"]
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(payload["evidence_kind"], case["evidence_kind"])

    def test_running_or_missing_current_head_is_pending(self):
        cases = (
            [self.summary(status="🔄 **Running** since now")],
            [self.summary(status="✅ **Completed** now", commit_ref="aaaaaaaa")],
            [],
        )
        for comments in cases:
            with self.subTest(comments=comments):
                result, payload = self.run_validator(comments=comments, reviews=[])
                self.assertEqual(result.returncode, 75, result.stderr)
                self.assertEqual(payload["status"], "pending")

    def test_connector_marker_from_an_untrusted_author_is_ignored(self):
        result, payload = self.run_validator(
            comments=[
                self.summary(status="✅ **Completed** now")
                | {"user": {"login": "untrusted-contributor"}}
            ],
            reviews=[],
        )
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual(payload["status"], "pending")

    def test_unknown_or_failed_connector_state_fails_closed(self):
        for status in ("❌ **Failed**", "✨ **Mystery**"):
            with self.subTest(status=status):
                result, payload = self.run_validator(
                    comments=[self.summary(status=status)],
                    reviews=[],
                )
                self.assertEqual(result.returncode, 1)
                self.assertIsNone(payload)
                self.assertIn("evidence is invalid", result.stderr)


if __name__ == "__main__":
    unittest.main()
