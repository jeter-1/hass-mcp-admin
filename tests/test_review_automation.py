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
READY_AUTH_VALIDATOR = ROOT / "scripts" / "validate_ready_authorization.py"


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
        self.assertEqual(events["issue_comment"]["types"], ["created"])
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
        self.assertIn("github.event.comment.body == '@codex review'", job["if"])
        self.assertIn("github.actor == 'jeter-1'", job["if"])

        context = next(step for step in job["steps"] if step.get("id") == "context")
        context_script = str(context["run"])
        self.assertIn('.state == "open"', context_script)
        self.assertIn("EVENT_HEAD_SHA", context_script)
        self.assertIn(
            "${{ github.event.pull_request.number || github.event.issue.number }}",
            context["env"]["PR_NUMBER"],
        )

        checkout = next(
            step for step in job["steps"] if str(step.get("uses", "")).startswith("actions/checkout@")
        )
        self.assertRegex(checkout["uses"], r"^actions/checkout@[0-9a-f]{40}$")
        self.assertEqual(checkout["with"]["ref"], "${{ steps.context.outputs.base_sha }}")
        self.assertFalse(checkout["with"]["persist-credentials"])

        script = str(job["steps"][-1]["run"])
        self.assertEqual(
            job["steps"][-1]["env"]["RECEIPT_MAX_ATTEMPTS"],
            "90",
        )
        self.assertEqual(job["steps"][-1]["env"]["RECEIPT_POLL_SECONDS"], "20")
        self.assertIn("BASE_SHA", script)
        self.assertIn("EXPECTED_HEAD_SHA", script)
        self.assertIn("scripts/validate_native_codex_review.py", script)
        self.assertIn("context=\"codex-review-receipt\"", script)
        self.assertEqual(script.count("gh api --paginate"), 3)
        self.assertEqual(script.count("jq -s 'add'"), 3)

    def test_ready_event_arms_native_auto_merge_from_protected_base_policy(self):
        events = workflow_events(self.auto_merge)
        self.assertEqual(
            events,
            {
                "issue_comment": {"types": ["created"]},
                "pull_request_target": {
                    "types": ["ready_for_review", "synchronize"]
                }
            },
        )
        job = self.auto_merge["jobs"]["authorize-auto-merge"]
        self.assertIn("github.event.action == 'ready_for_review'", job["if"])
        self.assertIn("github.actor == 'jeter-1'", job["if"])
        self.assertIn("base.ref == 'main'", job["if"])
        self.assertIn("github.event.comment.body == '@codex review'", job["if"])
        self.assertEqual(
            job["permissions"],
            {
                "contents": "write",
                "issues": "read",
                "pull-requests": "write",
            },
        )
        context = next(step for step in job["steps"] if step.get("id") == "context")
        context_script = str(context["run"])
        self.assertIn('.state == "open"', context_script)
        self.assertIn("EVENT_HEAD_SHA", context_script)
        self.assertIn(
            "${{ github.event.pull_request.number || github.event.issue.number }}",
            context["env"]["PR_NUMBER"],
        )
        checkout = next(
            step
            for step in job["steps"]
            if str(step.get("uses", "")).startswith("actions/checkout@")
        )
        self.assertRegex(checkout["uses"], r"^actions/checkout@[0-9a-f]{40}$")
        self.assertEqual(checkout["with"]["ref"], "${{ steps.context.outputs.base_sha }}")
        self.assertFalse(checkout["with"]["persist-credentials"])

        ready_guard = next(
            candidate
            for candidate in job["steps"]
            if candidate.get("name") == "Verify current-head Ready authorization for retry"
        )
        self.assertEqual(ready_guard["if"], "github.event_name == 'issue_comment'")
        self.assertIn("issues/${PR_NUMBER}/timeline?per_page=100", ready_guard["run"])
        self.assertIn("scripts/validate_ready_authorization.py", ready_guard["run"])

        step = job["steps"][-1]
        self.assertEqual(step["env"]["RECEIPT_MAX_ATTEMPTS"], "105")
        self.assertEqual(step["env"]["RECEIPT_POLL_SECONDS"], "20")
        script = str(step["run"])
        self.assertIn("BASE_SHA", script)
        self.assertIn("current_head_sha", script)
        self.assertIn("AUTHORIZED_HEAD_SHA", script)
        self.assertIn("scripts/validate_native_codex_review.py", script)
        self.assertEqual(script.count("gh api --paginate"), 3)
        self.assertEqual(script.count("jq -s 'add'"), 3)
        self.assertIn("gh pr merge", script)
        self.assertIn("--auto", script)
        self.assertIn("--merge", script)
        self.assertIn("--match-head-commit", script)
        self.assertNotIn("/status", script)
        self.assertNotIn("receipt_state", script)
        self.assertLess(
            script.index("scripts/validate_native_codex_review.py"),
            script.index("gh pr merge"),
        )
        self.assertNotIn("--admin", script)
        self.assertNotIn("--force", script)

    def test_untrusted_success_status_cannot_replace_native_codex_evidence(self):
        job = self.auto_merge["jobs"]["authorize-auto-merge"]
        step = job["steps"][-1]
        script = str(step["run"])
        head = "4deb1d30edc7ccb8ced7c8438930ca1310c3775b"
        base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            comments = root / "comments.json"
            gh_log = root / "gh.log"
            review_comments = root / "review-comments.json"
            reviews = root / "reviews.json"
            status_payload = root / "status.json"
            summary = root / "summary.md"
            fake_gh = root / "gh"
            comments.write_text("[]", encoding="utf-8")
            review_comments.write_text("[]", encoding="utf-8")
            status_payload.write_text(
                json.dumps(
                    {
                        "statuses": [
                            {
                                "state": "success",
                                "creator": {"login": "github-actions[bot]"},
                                "target_url": "https://github.example/candidate-run",
                            },
                            {
                                "state": "pending",
                                "creator": {"login": "github-actions[bot]"},
                                "target_url": "https://github.example/trusted-run",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            fake_gh.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$MOCK_GH_LOG"
if [[ "$1" == "api" && "$2" == "repos/${REPOSITORY}/pulls/${PR_NUMBER}" ]]; then
  printf '%s\\n' "$AUTHORIZED_HEAD_SHA"
elif [[ "$1" == "api" && "$2" == "--method" && "$3" == "POST" && "$4" == *"/statuses/"* ]]; then
  exit 0
elif [[ "$1" == "api" && "$2" == "--paginate" && "$3" == *"/issues/"*"/comments?"* ]]; then
  cat "$MOCK_COMMENTS_FILE"
elif [[ "$1" == "api" && "$2" == "--paginate" && "$3" == *"/pulls/"*"/reviews?"* ]]; then
  cat "$MOCK_REVIEWS_FILE"
elif [[ "$1" == "api" && "$2" == "--paginate" && "$3" == *"/pulls/"*"/comments?"* ]]; then
  cat "$MOCK_REVIEW_COMMENTS_FILE"
elif [[ "$1" == "api" && "$2" == *"/commits/"*"/status" ]]; then
  cat "$MOCK_STATUS_FILE"
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

            operational_notice = {
                "pull_request_review_id": 5058935360,
                "commit_id": head,
                "user": {"login": "chatgpt-codex-connector[bot]"},
                "body": (
                    "To use Codex here, [create an environment for this repo]"
                    "(https://chatgpt.com/codex/cloud/settings/environments)."
                ),
            }
            submitted_review = {
                "id": 5058935360,
                "user": {"login": "chatgpt-codex-connector[bot]"},
                "commit_id": head,
                "state": "COMMENTED",
            }

            for (
                evidence_kind,
                review_payload,
                inline_payload,
                expected_returncode,
            ) in (
                ("timed-out-before-review", [], [], 1),
                ("operational-notice", [submitted_review], [operational_notice], 1),
                (
                    "retriggered-after-exact-review",
                    [submitted_review],
                    [],
                    0,
                ),
            ):
                with self.subTest(evidence_kind=evidence_kind):
                    gh_log.write_text("", encoding="utf-8")
                    reviews.write_text(json.dumps(review_payload), encoding="utf-8")
                    review_comments.write_text(
                        json.dumps(inline_payload), encoding="utf-8"
                    )
                    result = subprocess.run(
                        ["bash", "-c", script],
                        cwd=ROOT,
                        env={
                            **os.environ,
                            "AUTHORIZED_HEAD_SHA": head,
                            "BASE_SHA": base_sha,
                            "GH_TOKEN": "test-token",
                            "GITHUB_STEP_SUMMARY": str(summary),
                            "MOCK_COMMENTS_FILE": str(comments),
                            "MOCK_GH_LOG": str(gh_log),
                            "MOCK_REVIEW_COMMENTS_FILE": str(review_comments),
                            "MOCK_REVIEWS_FILE": str(reviews),
                            "MOCK_STATUS_FILE": str(status_payload),
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
                    self.assertEqual(
                        merge_was_reached,
                        evidence_kind == "retriggered-after-exact-review",
                    )
                    self.assertNotIn(
                        "/status",
                        gh_log.read_text(encoding="utf-8"),
                    )

            receipt_script = str(
                self.receipt["jobs"]["codex-review-receipt"]["steps"][-1]["run"]
            )
            for (
                evidence_kind,
                review_payload,
                inline_payload,
                expected_returncode,
                status_state,
            ) in (
                ("timed-out-before-review", [], [], 1, "failure"),
                (
                    "operational-notice",
                    [submitted_review],
                    [operational_notice],
                    1,
                    "failure",
                ),
                (
                    "retriggered-after-exact-review",
                    [submitted_review],
                    [],
                    0,
                    "success",
                ),
            ):
                with self.subTest(receipt_recovery=evidence_kind):
                    gh_log.write_text("", encoding="utf-8")
                    reviews.write_text(json.dumps(review_payload), encoding="utf-8")
                    review_comments.write_text(
                        json.dumps(inline_payload), encoding="utf-8"
                    )
                    result = subprocess.run(
                        ["bash", "-c", receipt_script],
                        cwd=ROOT,
                        env={
                            **os.environ,
                            "AUTHORIZED_HEAD_SHA": head,
                            "BASE_SHA": base_sha,
                            "EXPECTED_HEAD_SHA": head,
                            "GH_TOKEN": "test-token",
                            "GITHUB_STEP_SUMMARY": str(summary),
                            "MOCK_COMMENTS_FILE": str(comments),
                            "MOCK_GH_LOG": str(gh_log),
                            "MOCK_REVIEW_COMMENTS_FILE": str(review_comments),
                            "MOCK_REVIEWS_FILE": str(reviews),
                            "MOCK_STATUS_FILE": str(status_payload),
                            "PATH": f"{root}:{os.environ['PATH']}",
                            "PR_NUMBER": "164",
                            "RECEIPT_MAX_ATTEMPTS": "1",
                            "RECEIPT_POLL_SECONDS": "0",
                            "REPOSITORY": "jeter-1/hass-mcp-admin",
                            "RUN_URL": "https://github.example/trusted-run",
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
                    self.assertIn(
                        f"state={status_state}",
                        gh_log.read_text(encoding="utf-8"),
                    )

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
        flattened = " ".join(guide.split())
        self.assertIn("load only from the protected base commit", flattened)
        self.assertIn("service-side eligibility policy", flattened)
        self.assertIn("ruleset still requires only `validate`", flattened)
        self.assertIn(
            "does not trust a candidate-head commit status as authorization",
            flattened,
        )


class NativeCodexReceiptValidationTests(unittest.TestCase):
    HEAD = "4deb1d30edc7ccb8ced7c8438930ca1310c3775b"

    def run_validator(
        self,
        *,
        comments: list[dict],
        reviews: list[dict],
        review_comments: list[dict] | None = None,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            comments_path = root / "comments.json"
            reviews_path = root / "reviews.json"
            review_comments_path = root / "review-comments.json"
            output_path = root / "receipt.json"
            comments_path.write_text(json.dumps(comments), encoding="utf-8")
            reviews_path.write_text(json.dumps(reviews), encoding="utf-8")
            review_comments_path.write_text(
                json.dumps(review_comments or []), encoding="utf-8"
            )
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
                    "--review-comments",
                    str(review_comments_path),
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
                    "id": 4242,
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
                        "id": 4242,
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

    def test_operational_notice_cannot_satisfy_submitted_review_gate(self):
        result, payload = self.run_validator(
            comments=[],
            reviews=[
                {
                    "id": 5058935360,
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "commit_id": self.HEAD,
                    "state": "COMMENTED",
                    "body": "",
                }
            ],
            review_comments=[
                {
                    "pull_request_review_id": 5058935360,
                    "commit_id": self.HEAD,
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "body": (
                        "To use Codex here, [create an environment for this repo]"
                        "(https://chatgpt.com/codex/cloud/settings/environments)."
                    ),
                }
            ],
        )
        self.assertEqual(result.returncode, 1)
        self.assertIsNone(payload)
        self.assertIn("operational notice instead of a code review", result.stderr)

    def test_actionable_feedback_about_failure_paths_remains_review_evidence(self):
        result, payload = self.run_validator(
            comments=[],
            reviews=[
                {
                    "id": 4242,
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "commit_id": self.HEAD,
                    "state": "COMMENTED",
                }
            ],
            review_comments=[
                {
                    "pull_request_review_id": 4242,
                    "commit_id": self.HEAD,
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "body": (
                        "The Codex review failed path and "
                        "https://chatgpt.com/codex/cloud/settings/environments "
                        "marker both need bounded handling."
                    ),
                }
            ],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["evidence_kind"], "submitted_review")


class ReadyAuthorizationValidationTests(unittest.TestCase):
    def run_validator(self, timeline: list[dict]):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timeline_path = root / "timeline.json"
            output_path = root / "authorization.json"
            timeline_path.write_text(json.dumps(timeline), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(READY_AUTH_VALIDATOR),
                    "--timeline",
                    str(timeline_path),
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

    @staticmethod
    def committed(sha: str) -> dict:
        return {"event": "committed", "sha": sha}

    @staticmethod
    def ready(*, actor: str = "jeter-1", event_id: int = 1) -> dict:
        return {
            "id": event_id,
            "event": "ready_for_review",
            "actor": {"login": actor},
        }

    def test_unchanged_ready_head_allows_comment_retry(self):
        result, payload = self.run_validator(
            [self.committed("a" * 40), self.ready(event_id=22)]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["status"], "authorized")
        self.assertEqual(payload["event_id"], 22)

    def test_synchronized_head_requires_another_ready_action(self):
        head_a = self.committed("a" * 40)
        head_b = self.committed("b" * 40)
        result, payload = self.run_validator([head_a, self.ready(), head_b])
        self.assertEqual(result.returncode, 1)
        self.assertIsNone(payload)

        recovered, recovered_payload = self.run_validator(
            [head_a, self.ready(), head_b, self.ready(event_id=2)]
        )
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(recovered_payload["event_id"], 2)

    def test_force_push_draft_or_nonowner_ready_fails_closed(self):
        cases = (
            [self.committed("a" * 40), self.ready(), {"event": "head_ref_force_pushed"}],
            [self.committed("a" * 40), self.ready(), {"event": "convert_to_draft"}],
            [self.committed("a" * 40), self.ready(actor="untrusted-contributor")],
            [],
        )
        for timeline in cases:
            with self.subTest(timeline=timeline):
                result, payload = self.run_validator(timeline)
                self.assertEqual(result.returncode, 1)
                self.assertIsNone(payload)


if __name__ == "__main__":
    unittest.main()
