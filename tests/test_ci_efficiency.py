"""Offline scheduling contracts; GitHub execution remains separate evidence."""

import ast
import json
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def evaluate(expression, github):
    """Evaluate only the expression subset used here, with synthetic contexts.

    This is a scheduling model, not a replacement for GitHub/actionlint syntax
    validation. Unsupported syntax refuses instead of silently approximating it.
    """
    source = expression.strip()
    if not (source.startswith("${{") and source.endswith("}}")):
        raise ValueError("expected expression")
    source = source[3:-2].replace("&&", " and ").replace("||", " or ").strip()

    def visit(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name) and node.id == "github":
            return github
        if isinstance(node, ast.Attribute):
            value = visit(node.value)
            return value.get(node.attr, "") if isinstance(value, dict) else ""
        if isinstance(node, ast.BoolOp):
            for child in node.values:
                value = visit(child)
                if isinstance(node.op, ast.And) and not value:
                    return value
                if isinstance(node.op, ast.Or) and value:
                    return value
            return value
        if (isinstance(node, ast.Compare) and len(node.ops) == 1
                and isinstance(node.ops[0], ast.Eq)):
            left, right = visit(node.left), visit(node.comparators[0])
            if isinstance(left, str) and isinstance(right, str):
                return left.casefold() == right.casefold()
            return left == right
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "format" and not node.keywords):
            template, *values = [visit(arg) for arg in node.args]
            return template.format(*values)
        raise ValueError("unsupported expression syntax")

    return visit(ast.parse("(" + source + ")", mode="eval").body)


class CIEfficiencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        cls.jobs = cls.workflow["jobs"]

    def context(self, *, pr=194, run=1001, attempt=1, event="pull_request",
                workflow="ci.yml", repository="jeter-1/hass-mcp-admin"):
        ref = f"refs/pull/{pr}/merge" if event == "pull_request" else "refs/heads/main"
        return {
            "repository": repository, "event_name": event, "ref": ref,
            "workflow_ref": f"{repository}/.github/workflows/{workflow}@{ref}",
            "workflow": "CI", "run_id": run, "run_attempt": attempt,
            "head_ref": "same-branch-name",
            "event": {"pull_request": {"number": pr}} if event == "pull_request" else {},
        }

    def policy(self, context):
        policy = self.workflow["concurrency"]
        return (evaluate(policy["group"], context),
                evaluate(policy["cancel-in-progress"], context))

    def test_same_pr_supersedes_obsolete_run(self):
        old = self.policy(self.context())
        new = self.policy(self.context(run=1002))
        self.assertEqual(old[0], new[0])
        self.assertIs(new[1], True)

    def test_different_prs_and_repositories_never_share_group(self):
        group, _ = self.policy(self.context())
        for ctx in (self.context(pr=195), self.context(repository="example/other")):
            self.assertNotEqual(group, self.policy(ctx)[0])

    def test_non_pr_calls_isolate_pending_running_and_rerun_attempts(self):
        groups = set()
        for event in ("push", "workflow_dispatch", "schedule"):
            for run, attempt in ((1001, 1), (1002, 1), (1002, 2)):
                # Use distinct real-world run IDs for distinct trigger events.
                ctx = self.context(event=event, run=run + len(groups) * 100,
                                   attempt=attempt, workflow="publish-rc-image.yml")
                group, cancel = self.policy(ctx)
                self.assertIs(cancel, False)
                self.assertNotIn(group, groups)
                self.assertNotEqual(group, self.policy(self.context())[0])
                groups.add(group)
        self.assertNotEqual(
            self.policy(self.context(event="workflow_dispatch", attempt=1))[0],
            self.policy(self.context(event="workflow_dispatch", attempt=2))[0],
        )

    def test_pr_reusable_caller_does_not_cancel_standalone_ci_or_its_caller(self):
        direct = self.policy(self.context())
        for workflow in ("publish-rc-image.yml", "enable-auto-merge.yml", "other.yml"):
            first = self.policy(self.context(workflow=workflow))
            second = self.policy(self.context(workflow=workflow, run=1002))
            self.assertIs(first[1], False)
            self.assertNotEqual(first[0], direct[0])
            self.assertNotEqual(first[0], second[0])
        publication = yaml.safe_load(
            (ROOT / ".github/workflows/publish-rc-image.yml").read_text()
        )["concurrency"]
        self.assertNotEqual(direct[0], publication["group"])
        self.assertFalse(publication["cancel-in-progress"])

    def test_missing_pr_context_does_not_enable_cancellation(self):
        ctx = self.context(event="workflow_dispatch")
        self.assertIs(self.policy(ctx)[1], False)
        self.assertNotIn("None", self.policy(ctx)[0])

    def test_source_and_packaging_are_siblings_after_required_preflight(self):
        for name in ("validate_source", "validate_packaging"):
            self.assertEqual(self.jobs[name]["needs"], "validate_prerequisites")
            self.assertNotIn("if", self.jobs[name])
            self.assertNotIn("continue-on-error", self.jobs[name])
        self.assertNotIn("needs", self.jobs["validate_prerequisites"])
        self.assertEqual(set(self.jobs["validate"]["needs"]), set(self.jobs) - {"validate"})

    def test_workers_checkout_the_same_event_revision_without_ref_override(self):
        for name in ("validate_prerequisites", "validate_source", "validate_packaging"):
            checkout = self.jobs[name]["steps"][0]
            self.assertEqual(checkout["uses"],
                             "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683")
            self.assertNotIn("ref", checkout.get("with", {}))

    def test_preflight_preserves_checks_and_fixture_cleanup(self):
        steps = self.jobs["validate_prerequisites"]["steps"]
        by_name = {s.get("name"): s for s in steps}
        for name in (
            "Regenerate historical compatibility fixtures", "Audit Engineering dependencies",
            "Compile Python", "Validate deployment metadata",
            "Validate exact materialized release transition", "Validate staged promotion candidate",
            "Validate reviewed upstream registry evidence", "Validate PowerShell deployment script syntax",
            "Validate add-on YAML",
        ):
            self.assertIn(name, by_name)
        self.assertIn("trap cleanup EXIT", by_name[
            "Regenerate historical compatibility fixtures"]["run"])

    def test_full_suite_runs_once_with_declared_dependencies(self):
        runs = [s["run"] for j in self.jobs.values() for s in j.get("steps", []) if "run" in s]
        self.assertEqual(runs.count("python -m unittest discover -s tests -v"), 1)
        source = self.jobs["validate_source"]["steps"]
        self.assertEqual(source[-2]["run"], "python -m unittest discover -s tests -v")
        self.assertEqual(source[-2]["id"], "unit_tests")
        self.assertEqual(source[-1]["id"], "source_receipt")
        self.assertEqual(source[-1]["env"]["FULL_SUITE_OUTCOME"], "${{ steps.unit_tests.outcome }}")
        self.assertEqual(source[-3]["run"],
                         "python -m pip --isolated install --require-hashes --only-binary=:all: --index-url https://pypi.org/simple -r tests/requirements.lock")

    def test_packaging_preserves_platforms_embedded_identity_and_no_push(self):
        steps = self.jobs["validate_packaging"]["steps"]
        scripts = "\n".join(s.get("run", "") for s in steps)
        self.assertIn("docker build -t hass-mcp-admin:test ./hass_mcp_admin", scripts)
        self.assertIn('for architecture in amd64 arm64; do', scripts)
        self.assertIn('--load --tag', scripts)
        self.assertIn('--network none', scripts)
        self.assertIn('120s docker run', scripts)
        self.assertIn('/app/build_inputs.py smoke', scripts)
        self.assertIn('--expected-platform', scripts)
        self.assertIn('trap', scripts)
        self.assertIn('BUILD_SHA == os.environ["EXPECTED_BUILD_SHA"]', scripts)
        self.assertIn('SERVER_VERSION == os.environ["EXPECTED_BUILD_VERSION"]', scripts)
        self.assertIn("BUILD_DIRTY is False", scripts)
        self.assertNotIn("--push", scripts)
        self.assertNotIn("docker push", scripts)

    def test_all_workers_are_bounded_without_weakening_existing_limits(self):
        for name, job in self.jobs.items():
            with self.subTest(job=name):
                self.assertIsInstance(job.get("timeout-minutes"), int)
                self.assertGreater(job["timeout-minutes"], 0)
        for name, expected in {"validate": 5, "real-ha-contract-tests": 50,
                               "exact-image-read-gateway": 25,
                               "exact-addon-runtime-acceptance": 20}.items():
            self.assertEqual(self.jobs[name]["timeout-minutes"], expected)

    def test_validation_does_not_add_permissions_or_write_actions(self):
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        for job in self.jobs.values():
            self.assertNotIn("permissions", job)
        text = json.dumps(self.workflow)
        for command in ("docker login", "docker push", "gh release create", "git push"):
            self.assertNotIn(command, text)


if __name__ == "__main__":
    unittest.main()
