import json
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

    def test_native_codex_receipt_runs_only_for_an_exact_owner_comment(self):
        events = workflow_events(self.receipt)
        self.assertEqual(events, {"issue_comment": {"types": ["created"]}})
        self.assertNotIn("pull_request_target", events)
        concurrency = self.receipt["concurrency"]
        self.assertIn("github.event.issue.number", concurrency["group"])
        self.assertIn("github.event.comment.id", concurrency["group"])
        self.assertTrue(concurrency["cancel-in-progress"])

        job = self.receipt["jobs"]["codex-review-receipt"]
        condition = job["if"]
        self.assertIn("github.actor == 'jeter-1'", condition)
        self.assertIn("github.event.issue.pull_request != null", condition)
        self.assertIn("github.event.comment.body == '@codex review'", condition)
        self.assertNotIn("ready_for_review", condition)
        self.assertNotIn("synchronize", condition)

    def test_optional_receipt_is_read_only_and_uses_protected_base_policy(self):
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

        context = next(step for step in job["steps"] if step.get("id") == "context")
        context_script = str(context["run"])
        self.assertIn('.state == "open"', context_script)
        self.assertIn(".draft == false", context_script)
        self.assertIn(".base.ref == \"main\"", context_script)
        self.assertIn(".head.repo.full_name == $repository", context_script)
        self.assertEqual(context["env"]["PR_NUMBER"], "${{ github.event.issue.number }}")

        checkout = next(
            step
            for step in job["steps"]
            if str(step.get("uses", "")).startswith("actions/checkout@")
        )
        self.assertRegex(checkout["uses"], r"^actions/checkout@[0-9a-f]{40}$")
        self.assertEqual(checkout["with"]["ref"], "${{ steps.context.outputs.base_sha }}")
        self.assertFalse(checkout["with"]["persist-credentials"])

        script = str(job["steps"][-1]["run"])
        self.assertIn('git rev-parse HEAD)" != "$BASE_SHA"', script)
        self.assertIn("EXPECTED_HEAD_SHA", script)
        self.assertIn("scripts/validate_native_codex_review.py", script)
        self.assertIn('context="codex-review-receipt"', script)
        self.assertEqual(script.count("gh api --paginate"), 3)
        self.assertNotIn("gh pr merge", script)
        self.assertNotIn("--admin", script)

    def test_owner_ready_merges_exact_head_after_checks_without_model_polling(self):
        events = workflow_events(self.auto_merge)
        self.assertEqual(
            events,
            {
                "issue_comment": {"types": ["created"]},
                "pull_request_target": {
                    "types": [
                        "closed",
                        "converted_to_draft",
                        "edited",
                        "ready_for_review",
                        "reopened",
                        "synchronize",
                    ]
                },
            },
        )
        job = self.auto_merge["jobs"]["merge-authorized-head"]
        condition = job["if"]
        self.assertIn("github.event.action == 'ready_for_review'", condition)
        self.assertIn("github.actor == 'jeter-1'", condition)
        self.assertIn("base.ref == 'main'", condition)
        self.assertIn("head.repo.full_name == github.repository", condition)
        self.assertIn("github.event.comment.body == '@merge'", condition)
        self.assertNotIn("@codex review", condition)
        self.assertEqual(
            job["permissions"],
            {
                "checks": "read",
                "contents": "write",
                "issues": "read",
                "pull-requests": "write",
            },
        )

        context = next(step for step in job["steps"] if step.get("id") == "context")
        self.assertIn("EVENT_HEAD_SHA", context["run"])
        checkout = next(
            step
            for step in job["steps"]
            if str(step.get("uses", "")).startswith("actions/checkout@")
        )
        self.assertRegex(checkout["uses"], r"^actions/checkout@[0-9a-f]{40}$")
        self.assertEqual(checkout["with"]["ref"], "${{ steps.context.outputs.base_sha }}")
        self.assertFalse(checkout["with"]["persist-credentials"])

        wait_step = next(
            step
            for step in job["steps"]
            if step.get("name")
            == "Wait for the exact-head deterministic validate check"
        )
        wait_script = str(wait_step["run"])
        self.assertIn("check-runs?check_name=validate&filter=latest", wait_script)
        self.assertIn("AUTHORIZED_BASE_SHA", wait_script)
        self.assertIn("AUTHORIZED_HEAD_SHA", wait_script)
        self.assertIn(".check_runs[0].pull_requests[]?", wait_script)
        self.assertIn(".base.sha == $base_sha", wait_script)
        self.assertIn(".head.sha == $head_sha", wait_script)
        self.assertIn(".number | tostring", wait_script)
        self.assertIn("VALIDATE_MAX_ATTEMPTS", wait_script)
        self.assertIn("The pull-request base or head moved", wait_script)
        self.assertNotIn("gh pr checks", wait_script)
        self.assertNotIn("validate_native_codex_review", wait_script)
        self.assertNotIn("codex-review-receipt", wait_script)

        merge_script = str(job["steps"][-1]["run"])
        self.assertIn("issues/${PR_NUMBER}/timeline?per_page=100", merge_script)
        self.assertIn("scripts/validate_ready_authorization.py", merge_script)
        self.assertIn('git rev-parse HEAD)" != "$AUTHORIZED_BASE_SHA"', merge_script)
        self.assertIn('.state == "open"', merge_script)
        self.assertIn(".draft == false", merge_script)
        self.assertIn('.base.ref == "main"', merge_script)
        self.assertIn(".base.sha == $base_sha", merge_script)
        self.assertIn(".head.repo.full_name == $repository", merge_script)
        self.assertIn(".head.sha == $head_sha", merge_script)
        self.assertIn("gh pr merge", merge_script)
        self.assertIn("--merge", merge_script)
        self.assertIn("--match-head-commit", merge_script)
        self.assertNotIn("--auto", merge_script)
        self.assertNotIn("validate_native_codex_review", merge_script)
        self.assertNotIn("codex-review-receipt", merge_script)
        self.assertNotIn("RECEIPT_", merge_script)
        self.assertNotIn("sleep ", merge_script)
        self.assertNotIn("--admin", merge_script)
        self.assertNotIn("--force", merge_script)

    def test_validate_wait_is_exact_head_deterministic_and_fail_closed(self):
        job = self.auto_merge["jobs"]["merge-authorized-head"]
        script = str(
            next(
                step
                for step in job["steps"]
                if step.get("name")
                == "Wait for the exact-head deterministic validate check"
            )["run"]
        )
        authorized_base = "b" * 40
        authorized_head = "4deb1d30edc7ccb8ced7c8438930ca1310c3775b"

        def pr_payload(*, base: str = authorized_base, head: str = authorized_head):
            return {
                "base": {"ref": "main", "sha": base},
                "head": {"sha": head},
            }

        def check_payload(
            *,
            base: str = authorized_base,
            head: str = authorized_head,
            number: int = 164,
            status: str = "completed",
            conclusion: str = "success",
        ):
            return {
                "total_count": 1,
                "check_runs": [
                    {
                        "head_sha": head,
                        "status": status,
                        "conclusion": conclusion,
                        "pull_requests": [
                            {
                                "number": number,
                                "base": {"ref": "main", "sha": base},
                                "head": {"sha": head},
                            }
                        ],
                    }
                ],
            }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_gh = root / "gh"
            gh_log = root / "gh.log"
            fake_gh.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$MOCK_GH_LOG"
if [[ "$1" == "api" && "$2" == "repos/${REPOSITORY}/pulls/${PR_NUMBER}" ]]; then
  printf '%s\\n' "$MOCK_PR_JSON"
elif [[ "$1" == "api" && "$*" == *"check-runs?check_name=validate&filter=latest"* ]]; then
  printf '%s\\n' "$MOCK_CHECKS_JSON"
else
  printf 'unexpected gh invocation: %s\\n' "$*" >&2
  exit 2
fi
""",
                encoding="utf-8",
            )
            fake_gh.chmod(0o700)

            cases = (
                (pr_payload(), check_payload(), 0, 2),
                (
                    pr_payload(),
                    check_payload(conclusion="failure"),
                    1,
                    2,
                ),
                (pr_payload(), {"total_count": 2, "check_runs": [{}, {}]}, 1, 2),
                (pr_payload(), {"total_count": 0, "check_runs": []}, 1, 2),
                (pr_payload(head="a" * 40), check_payload(), 1, 1),
                (pr_payload(base="c" * 40), check_payload(), 1, 1),
                (
                    pr_payload(),
                    check_payload(base="c" * 40),
                    1,
                    2,
                ),
                (pr_payload(), check_payload(number=999), 1, 2),
                (pr_payload(), check_payload(head="a" * 40), 1, 2),
            )
            for current_pr, checks_payload, returncode, call_count in cases:
                with self.subTest(
                    current_pr=current_pr, checks_payload=checks_payload
                ):
                    gh_log.write_text("", encoding="utf-8")
                    result = subprocess.run(
                        ["bash", "-c", script],
                        cwd=ROOT,
                        env={
                            "AUTHORIZED_BASE_SHA": authorized_base,
                            "AUTHORIZED_HEAD_SHA": authorized_head,
                            "GH_TOKEN": "test-token",
                            "MOCK_CHECKS_JSON": json.dumps(checks_payload),
                            "MOCK_PR_JSON": json.dumps(current_pr),
                            "MOCK_GH_LOG": str(gh_log),
                            "PATH": f"{root}:/usr/bin:/bin",
                            "PR_NUMBER": "164",
                            "REPOSITORY": "jeter-1/hass-mcp-admin",
                            "VALIDATE_MAX_ATTEMPTS": "1",
                            "VALIDATE_POLL_SECONDS": "0",
                        },
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, returncode, result.stderr)
                    calls = gh_log.read_text(encoding="utf-8").splitlines()
                    self.assertEqual(len(calls), call_count)
                    self.assertFalse(
                        any("codex" in call.lower() for call in calls), calls
                    )

    def test_merge_script_reaches_only_the_authorized_exact_head(self):
        script = str(
            self.auto_merge["jobs"]["merge-authorized-head"]["steps"][-1]["run"]
        )
        base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        authorized_head = "4deb1d30edc7ccb8ced7c8438930ca1310c3775b"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_gh = root / "gh"
            gh_log = root / "gh.log"
            summary = root / "summary.md"
            fake_gh.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$MOCK_GH_LOG"
if [[ "$1" == "api" && "$2" == "--paginate" ]]; then
  printf '%s\\n' '[{"event":"committed","sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},{"id":22,"event":"ready_for_review","actor":{"login":"jeter-1"}}]'
elif [[ "$1" == "api" && "$2" == "repos/${REPOSITORY}/pulls/${PR_NUMBER}" ]]; then
  printf '%s\\n' "$MOCK_PR_JSON"
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

            eligible = {
                "state": "open",
                "draft": False,
                "base": {"ref": "main", "sha": base_sha},
                "head": {
                    "sha": authorized_head,
                    "repo": {"full_name": "jeter-1/hass-mcp-admin"},
                },
            }
            cases = (
                (eligible, 0, True),
                (eligible | {"state": "closed"}, 1, False),
                (eligible | {"draft": True}, 1, False),
                (eligible | {"base": {"ref": "other", "sha": base_sha}}, 1, False),
                (eligible | {"base": {"ref": "main", "sha": "b" * 40}}, 1, False),
                (
                    eligible
                    | {
                        "head": {
                            "sha": "a" * 40,
                            "repo": {"full_name": "jeter-1/hass-mcp-admin"},
                        }
                    },
                    1,
                    False,
                ),
                (
                    eligible
                    | {
                        "head": {
                            "sha": authorized_head,
                            "repo": {"full_name": "untrusted/fork"},
                        }
                    },
                    1,
                    False,
                ),
            )
            for pr_payload, expected_returncode, merge_expected in cases:
                with self.subTest(pr_payload=pr_payload):
                    gh_log.write_text("", encoding="utf-8")
                    result = subprocess.run(
                        ["bash", "-c", script],
                        cwd=ROOT,
                        env={
                            "AUTHORIZED_HEAD_SHA": authorized_head,
                            "AUTHORIZED_BASE_SHA": base_sha,
                            "GH_TOKEN": "test-token",
                            "GITHUB_STEP_SUMMARY": str(summary),
                            "MOCK_GH_LOG": str(gh_log),
                            "MOCK_PR_JSON": json.dumps(pr_payload),
                            "PATH": f"{root}:/usr/bin:/bin",
                            "PR_NUMBER": "164",
                            "REPOSITORY": "jeter-1/hass-mcp-admin",
                        },
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, expected_returncode, result.stderr)
                    calls = gh_log.read_text(encoding="utf-8").splitlines()
                    merge_was_reached = any(
                        line.startswith("pr merge ") for line in calls
                    )
                    self.assertEqual(merge_was_reached, merge_expected)
                    if merge_expected:
                        self.assertEqual(len(calls), 3)
                        self.assertIn("--match-head-commit", calls[-1])
                        self.assertNotIn("--auto", calls[-1])

    def test_lifecycle_change_cancels_without_persistent_auto_merge_authority(self):
        job = self.auto_merge["jobs"]["retire-stale-ready-run"]
        self.assertEqual(job["name"], "retire-stale-ready-run")
        self.assertIn("github.event.action == 'synchronize'", job["if"])
        self.assertIn("github.event.action == 'closed'", job["if"])
        self.assertIn("github.event.action == 'converted_to_draft'", job["if"])
        self.assertIn("github.event.action == 'reopened'", job["if"])
        self.assertIn("github.event.action == 'edited'", job["if"])
        self.assertIn("github.event.changes.base != null", job["if"])
        self.assertIn("github.event.changes.base.ref.from == 'main'", job["if"])
        self.assertIn("base.ref == 'main'", job["if"])
        self.assertIn("head.repo.full_name == github.repository", job["if"])
        self.assertEqual(job["permissions"], {})
        self.assertTrue(job["concurrency"]["cancel-in-progress"])
        self.assertFalse(any("uses" in step for step in job["steps"]))
        script = str(job["steps"][0]["run"])
        self.assertIn("No persistent auto-merge request", script)
        workflow_text = AUTO_MERGE_WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("--auto", workflow_text)
        self.assertNotIn("--disable-auto", workflow_text)
        self.assertNotIn("--admin", script)
        self.assertNotIn("--force", script)

    def test_workflows_add_no_bypass_publication_or_deployment_authority(self):
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (CODEX_RECEIPT_WORKFLOW, AUTO_MERGE_WORKFLOW)
        )
        for forbidden in (
            "--admin",
            "--force",
            "workflow_dispatch",
            "docker push",
            "gh release create",
            "homeassistant",
        ):
            self.assertNotIn(forbidden, combined)

    def test_ci_runs_once_per_pull_request_and_is_reused_on_main(self):
        events = workflow_events(self.ci)
        self.assertNotIn("push", events)
        self.assertIn("pull_request", events)
        self.assertIn("workflow_call", events)
        self.assertIn("ready_for_review", events["pull_request"]["types"])

    def test_bounded_independent_review_contract_is_documented(self):
        guide = WORKFLOW_GUIDE.read_text(encoding="utf-8")
        instructions = ROOT_INSTRUCTIONS.read_text(encoding="utf-8")
        flattened = " ".join(guide.split())
        self.assertIn("one full independent review", flattened)
        self.assertIn("at most one delta rereview", flattened)
        self.assertIn("must not serve as its own repeated independent reviewer", flattened)
        self.assertIn("`@codex review` requests only the optional review", flattened)
        self.assertIn("`@merge` comment may retry", flattened)
        self.assertIn("load only from the protected base commit", flattened)
        self.assertIn("publication does not trigger another model review", flattened)
        self.assertIn("one final time", flattened)
        self.assertIn("separate administrative actions", flattened)
        self.assertIn("attests that the bounded independent review", instructions)
        self.assertNotIn("native Codex review receipt gate", instructions)


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

    def test_running_missing_stale_or_untrusted_evidence_is_pending(self):
        cases = (
            [self.summary(status="🔄 **Running** since now")],
            [self.summary(status="✅ **Completed** now", commit_ref="aaaaaaaa")],
            [],
            [
                self.summary(status="✅ **Completed** now")
                | {"user": {"login": "untrusted-contributor"}}
            ],
        )
        for comments in cases:
            with self.subTest(comments=comments):
                result, payload = self.run_validator(comments=comments, reviews=[])
                self.assertEqual(result.returncode, 75, result.stderr)
                self.assertEqual(payload["status"], "pending")

    def test_unknown_failed_or_operational_notice_evidence_fails_closed(self):
        for status in ("❌ **Failed**", "✨ **Mystery**"):
            with self.subTest(status=status):
                result, payload = self.run_validator(
                    comments=[self.summary(status=status)], reviews=[]
                )
                self.assertEqual(result.returncode, 1)
                self.assertIsNone(payload)
                self.assertIn("evidence is invalid", result.stderr)

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
                    "commit_id": "0" * 40,
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

    def test_unchanged_ready_head_allows_merge_retry(self):
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

    def test_invalidating_lifecycle_or_nonowner_fails_closed(self):
        cases = (
            [self.committed("a" * 40), self.ready(), {"event": "head_ref_force_pushed"}],
            [self.committed("a" * 40), self.ready(), {"event": "convert_to_draft"}],
            [self.committed("a" * 40), self.ready(), {"event": "base_ref_changed"}],
            [self.committed("a" * 40), self.ready(), {"event": "head_ref_restored"}],
            [self.committed("a" * 40), self.ready(), {"event": "closed"}],
            [
                self.committed("a" * 40),
                self.ready(),
                {"event": "closed"},
                {"event": "reopened"},
            ],
            [self.committed("a" * 40), self.ready(actor="untrusted-contributor")],
            [],
        )
        for timeline in cases:
            with self.subTest(timeline=timeline):
                result, payload = self.run_validator(timeline)
                self.assertEqual(result.returncode, 1)
                self.assertIsNone(payload)

        recovered, recovered_payload = self.run_validator(
            [
                self.committed("a" * 40),
                self.ready(),
                {"event": "closed"},
                {"event": "reopened"},
                self.ready(event_id=3),
            ]
        )
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(recovered_payload["event_id"], 3)


if __name__ == "__main__":
    unittest.main()
