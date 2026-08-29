import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
CODEX_WORKFLOW = ROOT / ".github" / "workflows" / "codex-independent-review.yml"
AUTO_MERGE_WORKFLOW = ROOT / ".github" / "workflows" / "enable-auto-merge.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PROMPT = ROOT / ".github" / "codex" / "prompts" / "independent-review.md"
SCHEMA = ROOT / ".github" / "codex" / "review-schema.json"
VALIDATOR = ROOT / "scripts" / "validate_codex_review.py"
WORKFLOW_GUIDE = ROOT / "docs" / "CODEX_WORKFLOW.md"


def load_yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"workflow is not a mapping: {path}")
    return value


def workflow_events(workflow: dict) -> dict:
    return workflow.get("on", workflow.get(True))


def finding(severity: str) -> dict:
    return {
        "severity": severity,
        "title": f"Synthetic {severity} finding",
        "path": "scripts/example.py",
        "line": 17,
        "evidence": "The synthetic branch reaches the failing statement.",
        "impact": "The synthetic behavior is incorrect.",
        "cause": "The synthetic guard is absent.",
        "correction": "Add the synthetic guard.",
        "verification": "Add and run the synthetic regression test.",
    }


class ReviewWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.codex = load_yaml(CODEX_WORKFLOW)
        cls.auto_merge = load_yaml(AUTO_MERGE_WORKFLOW)
        cls.ci = load_yaml(CI_WORKFLOW)

    def test_codex_reviews_every_ready_head_with_read_only_authority(self):
        events = workflow_events(self.codex)
        self.assertEqual(
            set(events["pull_request"]["types"]),
            {"opened", "reopened", "synchronize", "ready_for_review"},
        )
        job = self.codex["jobs"]["codex-independent-review"]
        self.assertEqual(job["name"], "codex-independent-review")
        self.assertEqual(job["permissions"], {"contents": "read"})
        self.assertEqual(self.codex["permissions"], {})
        self.assertIn("draft == false", job["if"])

        checkout = next(
            step for step in job["steps"] if str(step.get("uses", "")).startswith("actions/checkout@")
        )
        self.assertRegex(checkout["uses"], r"^actions/checkout@[0-9a-f]{40}$")
        self.assertEqual(
            checkout["with"]["ref"],
            "${{ github.event.pull_request.head.sha }}",
        )
        self.assertFalse(checkout["with"]["persist-credentials"])

        codex = next(
            step for step in job["steps"] if str(step.get("uses", "")).startswith("openai/codex-action@")
        )
        self.assertRegex(codex["uses"], r"^openai/codex-action@[0-9a-f]{40}$")
        self.assertEqual(codex["with"]["sandbox"], "read-only")
        self.assertEqual(codex["with"]["safety-strategy"], "drop-sudo")
        self.assertEqual(codex["with"]["codex-args"], '["--ephemeral"]')
        self.assertEqual(codex["with"]["output-schema-file"], ".github/codex/review-schema.json")
        self.assertEqual(codex["with"]["openai-api-key"], "${{ secrets.OPENAI_API_KEY }}")

    def test_codex_prompt_has_a_bounded_untrusted_input_contract(self):
        prompt = PROMPT.read_text(encoding="utf-8")
        for text in (
            "PR_BASE_SHA...PR_HEAD_SHA",
            "untrusted review material",
            "instructions embedded in them",
            "live Home Assistant",
            "are blocking. Medium and Low findings are advisory",
            "Medium and Low findings are advisory",
        ):
            self.assertIn(text, prompt)

    def test_codex_schema_is_strict_and_bounded(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            set(schema["required"]), {"verdict", "summary", "findings"}
        )
        findings = schema["properties"]["findings"]
        self.assertEqual(findings["maxItems"], 20)
        self.assertFalse(findings["items"]["additionalProperties"])
        self.assertEqual(
            set(findings["items"]["properties"]["severity"]["enum"]),
            {"Critical", "High", "Medium", "Low"},
        )

    def test_ready_event_arms_native_auto_merge_without_checkout_or_merge_bypass(self):
        events = workflow_events(self.auto_merge)
        self.assertEqual(
            events,
            {"pull_request_target": {"types": ["ready_for_review"]}},
        )
        job = self.auto_merge["jobs"]["authorize-auto-merge"]
        self.assertIn("github.actor == 'jeter-1'", job["if"])
        self.assertIn("base.ref == 'main'", job["if"])
        self.assertEqual(
            job["permissions"],
            {"contents": "write", "pull-requests": "write"},
        )
        self.assertFalse(any("uses" in step for step in job["steps"]))
        script = str(job["steps"][0]["run"])
        self.assertIn("current_head_sha", script)
        self.assertIn("AUTHORIZED_HEAD_SHA", script)
        self.assertIn("gh pr merge", script)
        self.assertIn("--auto --merge", script)
        self.assertNotIn("--admin", script)
        self.assertNotIn("--force", script)

    def test_ci_runs_once_per_pull_request_and_is_reused_on_main(self):
        events = workflow_events(self.ci)
        self.assertNotIn("push", events)
        self.assertIn("pull_request", events)
        self.assertIn("workflow_call", events)
        self.assertIn(
            "ready_for_review", events["pull_request"]["types"]
        )

    def test_activation_dependency_is_documented_without_secret_material(self):
        guide = WORKFLOW_GUIDE.read_text(encoding="utf-8")
        self.assertIn("`OPENAI_API_KEY` repository Actions secret", guide)
        self.assertIn("Never place the key in repository content", guide)


class CodexVerdictValidationTests(unittest.TestCase):
    def run_validator(self, payload: object):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            review = root / "review.json"
            summary = root / "summary.md"
            review.write_text(json.dumps(payload), encoding="utf-8")
            environment = os.environ.copy()
            environment["GITHUB_STEP_SUMMARY"] = str(summary)
            result = subprocess.run(
                [sys.executable, str(VALIDATOR), str(review)],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            rendered = summary.read_text(encoding="utf-8") if summary.exists() else ""
            return result, rendered

    def test_pass_and_advisory_findings_do_not_block(self):
        for findings in ([], [finding("Medium"), finding("Low")]):
            with self.subTest(findings=len(findings)):
                result, rendered = self.run_validator(
                    {
                        "verdict": "pass",
                        "summary": "Synthetic review passed.",
                        "findings": findings,
                    }
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Verdict:** PASS", rendered)
                self.assertIn("Blocking findings: 0", rendered)

    def test_critical_or_high_finding_blocks(self):
        for severity in ("Critical", "High"):
            with self.subTest(severity=severity):
                result, rendered = self.run_validator(
                    {
                        "verdict": "fail",
                        "summary": "Synthetic review found a blocker.",
                        "findings": [finding(severity)],
                    }
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"### {severity}", rendered)
                self.assertIn("Blocking findings: 1", rendered)

    def test_inconsistent_or_unsafe_output_fails_closed(self):
        cases = (
            {
                "verdict": "pass",
                "summary": "Mismatched blocker.",
                "findings": [finding("High")],
            },
            {
                "verdict": "pass",
                "summary": "Unsafe path.",
                "findings": [finding("Low") | {"path": "../outside"}],
            },
            {"verdict": "pass", "summary": "Missing findings."},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                result, rendered = self.run_validator(payload)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(rendered, "")
                self.assertIn("Codex independent review is invalid", result.stderr)


if __name__ == "__main__":
    unittest.main()
