"""Exercise the actual final workflow step with an offline GitHub CLI double."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import quote

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/enable-auto-merge.yml"
STEP_NAME = "Delete merged head branch after receipt binding"
REPOSITORY = "jeter-1/hass-mcp-admin"
HEAD = "a" * 40
REF = "codex/HAMCP-284-delete-merged-head"


def workflow_job():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["merge-authorized-head"]


def cleanup_step():
    return next(step for step in workflow_job()["steps"] if step.get("name") == STEP_NAME)


class CleanupWorkflowTests(unittest.TestCase):
    def test_cleanup_is_one_final_step_after_binding_and_retention(self):
        steps = workflow_job()["steps"]
        names = [step.get("name") for step in steps]
        self.assertEqual(names.count(STEP_NAME), 1)
        self.assertEqual(names[-1], STEP_NAME)
        self.assertLess(names.index("Bind completed merge to publication receipt"),
                        names.index("Retain exact publication handoff"))
        self.assertLess(names.index("Retain exact publication handoff"), names.index(STEP_NAME))
        # Default success() preserves receipt/upload failure; no always() cleanup.
        self.assertNotIn("if", cleanup_step())

    def test_values_use_env_and_no_expression_is_executable(self):
        step = cleanup_step()
        self.assertEqual(step["env"]["AUTHORIZED_HEAD_SHA"], "${{ steps.context.outputs.head_sha }}")
        self.assertEqual(step["env"]["PR_NUMBER"], "${{ steps.context.outputs.pr_number }}")
        self.assertEqual(step["env"]["REPOSITORY"], "${{ github.repository }}")
        self.assertEqual(step["env"]["GH_TOKEN"], "${{ github.token }}")
        self.assertIn("export HEAD_REF MERGED_HEAD_SHA", step["run"])
        self.assertIn('os.environ["HEAD_REF"]', step["run"])
        self.assertIn('os.environ["MERGED_HEAD_SHA"]', step["run"])
        self.assertNotIn("${{", step["run"])
        self.assertNotIn("--delete-branch", WORKFLOW.read_text())

    def test_guards_and_nonfatal_exit_are_explicit(self):
        step = cleanup_step()
        self.assertTrue(step["continue-on-error"])
        self.assertTrue(step["run"].rstrip().endswith("exit 0"))
        self.assertIn('pr["head"]["repo"]["full_name"] == pr["base"]["repo"]["full_name"] == repository', step["run"])
        self.assertIn('head_ref in {"main", "registry"}', step["run"])
        self.assertIn('head_ref.startswith("registry-candidates/")', step["run"])
        self.assertIn('branch.get("protected") is not False', step["run"])
        self.assertIn('ref["object"].get("sha") != head_sha', step["run"])
        self.assertIn("GITHUB_STEP_SUMMARY", step["run"])
        self.assertEqual(workflow_job()["permissions"], {
            "actions": "read", "checks": "read", "contents": "write",
            "issues": "read", "pull-requests": "write",
        })


class CleanupExecutionTests(unittest.TestCase):
    def setUp(self):
        self.pr = {"merged": True,
                   "head": {"ref": REF, "sha": HEAD, "repo": {"full_name": REPOSITORY}},
                   "base": {"repo": {"full_name": REPOSITORY}}}
        self.branch = {"name": REF, "protected": False}
        self.ref = {"ref": "refs/heads/" + REF, "object": {"type": "commit", "sha": HEAD}}

    def run_step(self, *, fail_route="", bad_json_route="", authorized_head=HEAD):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = "repos/" + REPOSITORY
            encoded = quote(str(self.pr.get("head", {}).get("ref", REF)), safe="")
            routes = {
                f"{prefix}/pulls/5": self.pr,
                f"{prefix}/branches/{encoded}": self.branch,
                f"{prefix}/git/ref/heads/{encoded}": self.ref,
                f"{prefix}/git/refs/heads/{encoded}": None,
            }
            (root / "fixture.json").write_text(json.dumps(routes))
            fake_gh = root / "gh"
            fake_gh.write_text(f"#!{sys.executable}\n" + '''import json, os, sys
from pathlib import Path
root = Path(os.environ["FAKE_GH_ROOT"])
args = sys.argv[1:]
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps(args) + "\\n")
route = args[-1]
if route == os.environ["FAIL_ROUTE"]:
    print("synthetic-private-error", file=sys.stderr)
    sys.exit(1)
if route == os.environ["BAD_JSON_ROUTE"]:
    print("not json synthetic-private-error")
    sys.exit(0)
routes = json.loads((root / "fixture.json").read_text())
if route not in routes:
    sys.exit(2)
if routes[route] is not None:
    print(json.dumps(routes[route]))
''')
            fake_gh.chmod(0o755)
            env = {"PATH": str(root) + os.pathsep + os.environ["PATH"],
                   "FAKE_GH_ROOT": str(root), "FAIL_ROUTE": fail_route,
                   "BAD_JSON_ROUTE": bad_json_route, "REPOSITORY": REPOSITORY,
                   "PR_NUMBER": "5", "AUTHORIZED_HEAD_SHA": authorized_head,
                   "GITHUB_STEP_SUMMARY": str(root / "summary.md")}
            result = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", cleanup_step()["run"]],
                                    env=env, capture_output=True, text=True, timeout=10)
            calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
            summary = (root / "summary.md").read_text()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("synthetic-private-error", result.stdout + result.stderr + summary)
            return calls, summary, result.stdout

    def assert_skips(self, reason, **kwargs):
        calls, summary, output = self.run_step(**kwargs)
        self.assertFalse(any("DELETE" in call for call in calls), calls)
        self.assertIn("Warning:", summary)
        self.assertIn(reason, summary)
        self.assertIn("::warning::", output)

    def test_deletes_exact_merged_head_once_after_all_reads(self):
        calls, summary, output = self.run_step()
        prefix = "repos/" + REPOSITORY
        encoded = quote(REF, safe="")
        self.assertEqual(calls, [
            ["api", f"{prefix}/pulls/5"],
            ["api", "--method", "GET", f"{prefix}/branches/{encoded}"],
            ["api", "--method", "GET", f"{prefix}/git/ref/heads/{encoded}"],
            ["api", "--method", "DELETE", f"{prefix}/git/refs/heads/{encoded}"],
        ])
        self.assertIn("merged head branch deleted", summary)
        self.assertNotIn("Warning:", summary)
        self.assertNotIn("::warning::", output)

    def test_unmerged_pr_never_deletes(self):
        for value in (False, None, "true", 1):
            with self.subTest(value=value):
                self.pr["merged"] = value
                self.assert_skips("not merged")

    def test_fork_or_foreign_base_never_deletes(self):
        for side in ("head", "base"):
            with self.subTest(side=side):
                original = deepcopy(self.pr)
                self.pr[side]["repo"]["full_name"] = "outsider/fork"
                self.assert_skips("repository mismatch")
                self.pr = original

    def test_moved_or_mismatched_ref_never_deletes(self):
        for ref in ({"ref": "refs/heads/" + REF, "object": {"type": "commit", "sha": "b" * 40}},
                    {"ref": "refs/heads/another", "object": {"type": "commit", "sha": HEAD}},
                    {"ref": "refs/heads/" + REF, "object": {"type": "tag", "sha": HEAD}}):
            with self.subTest(ref=ref):
                self.ref = ref
                self.assert_skips("head ref moved or mismatched")

    def test_excluded_names_never_delete(self):
        for name in ("main", "registry", "registry-candidates/review", "registry-candidates/nested/ref"):
            with self.subTest(name=name):
                self.pr["head"]["ref"] = self.branch["name"] = name
                self.ref["ref"] = "refs/heads/" + name
                self.assert_skips("excluded head ref")

    def test_protected_or_unknown_protection_never_deletes(self):
        for value in (True, None, "false", 0):
            with self.subTest(value=value):
                self.branch["protected"] = value
                self.assert_skips("protected branch or unknown protection")

    def test_invalid_names_never_reach_branch_api(self):
        for name in ("../main", "a..b", "-main", ".hidden", "foo//bar", "foo.lock", "foo./bar",
                     "foo.lock/bar", "a\nb", "a;echo", "a$(id)", "a`id`", "a%2fb", "a?x", "a#x", "a" * 256):
            with self.subTest(name=name):
                self.pr["head"]["ref"] = name
                self.assert_skips("invalid head ref")

    def test_authorization_sha_mismatch_or_malformed_never_deletes(self):
        for value in ("b" * 40, "invalid", "a" * 39):
            with self.subTest(value=value):
                self.pr["head"]["sha"] = value
                self.assert_skips("SHA does not match authorization")

    def test_api_failures_warn_exit_zero_and_never_retry(self):
        prefix = "repos/" + REPOSITORY
        encoded = quote(REF, safe="")
        for route in (f"{prefix}/pulls/5", f"{prefix}/branches/{encoded}",
                      f"{prefix}/git/ref/heads/{encoded}", f"{prefix}/git/refs/heads/{encoded}"):
            with self.subTest(route=route):
                calls, summary, output = self.run_step(fail_route=route)
                self.assertEqual(sum(call[-1] == route for call in calls), 1)
                self.assertIn("Warning:", summary)
                self.assertIn("::warning::", output)
                self.assertIn("failed" if route.endswith("pulls/5") else "failure", summary)

    def test_missing_head_or_malformed_evidence_warns(self):
        del self.pr["head"]["ref"]
        self.assert_skips("PR head identity missing")
        self.pr["head"]["ref"] = REF
        self.assert_skips("API or evidence failure", bad_json_route=f"repos/{REPOSITORY}/branches/{quote(REF, safe='')}")


if __name__ == "__main__":
    unittest.main()
