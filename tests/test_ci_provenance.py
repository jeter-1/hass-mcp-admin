"""Synthetic GitHub API boundaries; no network, credentials or real merges."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "ci_provenance", ROOT / "scripts/validate_ci_provenance.py"
)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def fixture(base="b" * 40, head="4deb1d30edc7ccb8ced7c8438930ca1310c3775b"):
    prefix = "repos/jeter-1/hass-mcp-admin"
    repo = {"url": f"https://api.github.com/{prefix}"}
    prs = [{"number": 164, "base": {"ref": "main", "sha": base, "repo": repo},
            "head": {"sha": head, "repo": repo}}]
    check = {"id": 77, "name": "validate", "head_sha": head,
             "app": {"id": 15368, "slug": "github-actions"},
             "check_suite": {"id": 88}, "pull_requests": prs,
             "status": "completed", "conclusion": "success"}
    run = {"id": 99, "workflow_id": 123, "path": ".github/workflows/ci.yml",
           "run_attempt": 1, "check_suite_id": 88, "head_sha": head,
           "pull_requests": prs, "event": "pull_request",
           "repository": {"full_name": "jeter-1/hass-mcp-admin"},
           "head_repository": {"full_name": "jeter-1/hass-mcp-admin"},
           "status": "completed", "conclusion": "success"}
    job = {"id": 77, "name": "validate", "run_id": 99, "run_attempt": 1,
           "head_sha": head, "status": "completed", "conclusion": "success",
           "check_run_url": f"https://api.github.com/{prefix}/check-runs/77"}
    return {
        f"{prefix}/commits/{head}/check-runs?check_name=validate&filter=latest&per_page=100":
            {"total_count": 1, "check_runs": [check]},
        f"{prefix}/actions/workflows/ci.yml":
            {"id": 123, "path": ".github/workflows/ci.yml", "state": "active"},
        f"{prefix}/actions/workflows/123/runs?check_suite_id=88&head_sha={head}&event=pull_request&per_page=100":
            {"total_count": 1, "workflow_runs": [run]},
        f"{prefix}/actions/runs/99/attempts/1/jobs?per_page=100":
            {"total_count": 1, "jobs": [job]},
        f"{prefix}/actions/runs/99": deepcopy(run),
    }


# Existing workflow-shell tests use the same API fixture through a fake gh.
# The actual protected helper still runs; this substitutes only GitHub I/O.
MOCK_API_SHELL = '''elif [[ -n "${MOCK_PROVENANCE_JSON:-}" ]]; then
  python3 - "$2" <<'MOCK_API'
import json, os, sys
data = json.loads(os.environ["MOCK_PROVENANCE_JSON"])
if sys.argv[1] not in data:
    raise SystemExit(2)
print(json.dumps(data[sys.argv[1]]))
MOCK_API
'''


class CIProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()
        self.routes = list(self.data)
        self.calls = []

    def api(self, route):
        self.calls.append(route)
        return deepcopy(self.data[route])

    def verify(self):
        return validator.verify(164, "b" * 40,
                                "4deb1d30edc7ccb8ced7c8438930ca1310c3775b", self.api)

    def test_exact_current_ci_succeeds_with_traceable_receipt(self):
        result = self.verify()
        self.assertEqual(result["status"], "success")
        self.assertEqual((result["app_id"], result["run_id"], result["run_attempt"],
                          result["check_run_id"]), (15368, 99, 1, 77))
        self.assertEqual(len(self.calls), 6)

    def test_other_app_cannot_supply_the_required_check(self):
        for key, value in (("id", 1), ("slug", "another-app")):
            with self.subTest(key=key):
                self.setUp()
                self.data[self.routes[0]]["check_runs"][0]["app"][key] = value
                with self.assertRaisesRegex(validator.Refusal, "wrong_check_producer"):
                    self.verify()
                self.assertEqual(len(self.calls), 1)

    def test_wrong_workflow_event_suite_repository_or_revision_refuses(self):
        changes = (("workflow_id", 2), ("path", ".github/workflows/other.yml"),
                   ("event", "workflow_dispatch"), ("check_suite_id", 1),
                   ("head_sha", "a" * 40),
                   ("repository", {"full_name": "other/repo"}),
                   ("head_repository", {"full_name": "other/fork"}))
        for key, value in changes:
            with self.subTest(key=key):
                self.setUp()
                self.data[self.routes[2]]["workflow_runs"][0][key] = value
                with self.assertRaises(validator.Refusal):
                    self.verify()

    def test_wrong_pr_base_head_or_ambiguous_association_refuses(self):
        for index, collection in ((0, "check_runs"), (2, "workflow_runs")):
            for change in ("base", "head", "number", "repository", "duplicate", "missing"):
                with self.subTest(index=index, change=change):
                    self.setUp()
                    prs = self.data[self.routes[index]][collection][0]["pull_requests"]
                    if change in ("base", "head"):
                        prs[0][change]["sha"] = "c" * 40
                    elif change == "number":
                        prs[0]["number"] = 999
                    elif change == "repository":
                        prs[0]["head"]["repo"] = {"url": "https://invalid.example"}
                    elif change == "duplicate":
                        prs.append(deepcopy(prs[0]))
                    else:
                        prs.clear()
                    with self.assertRaises(validator.Refusal):
                        self.verify()

    def test_missing_duplicate_or_truncated_run_job_or_check_refuses(self):
        for index, key in ((0, "check_runs"), (2, "workflow_runs"), (3, "jobs")):
            for change in ("duplicate", "truncated", "malformed", "missing"):
                if index == 0 and change == "missing":
                    continue  # Initial absent check is allowed only as pending.
                with self.subTest(index=index, change=change):
                    self.setUp()
                    data = self.data[self.routes[index]]
                    if change == "duplicate":
                        data[key] *= 2
                        data["total_count"] = 2
                    elif change == "truncated":
                        data["total_count"] = 101
                    elif change == "malformed":
                        data[key] = None
                    else:
                        data[key], data["total_count"] = [], 0
                    with self.assertRaises(validator.Refusal):
                        self.verify()

    def test_job_must_bind_exact_check_and_current_attempt(self):
        for key, value in (("run_id", 2), ("run_attempt", 2), ("head_sha", "c" * 40),
                           ("check_run_url", "https://api.github.com/repos/jeter-1/hass-mcp-admin/check-runs/78")):
            with self.subTest(key=key):
                self.setUp()
                self.data[self.routes[3]]["jobs"][0][key] = value
                with self.assertRaisesRegex(validator.Refusal, "wrong_ci_job"):
                    self.verify()

    def test_run_rerun_during_verification_refuses(self):
        self.data[self.routes[4]]["run_attempt"] = 2
        with self.assertRaisesRegex(validator.Refusal, "ci_run_changed"):
            self.verify()

    def test_successful_current_rerun_is_accepted(self):
        self.data[self.routes[2]]["workflow_runs"][0]["run_attempt"] = 2
        self.data[self.routes[4]]["run_attempt"] = 2
        jobs = self.data.pop(self.routes[3])
        jobs["jobs"][0]["run_attempt"] = 2
        self.data[self.routes[3].replace("/attempts/1/", "/attempts/2/")] = jobs
        self.assertEqual(self.verify()["run_attempt"], 2)

    def test_unknown_status_and_malformed_workflow_refuse(self):
        self.data[self.routes[0]]["check_runs"][0].update(status="unknown", conclusion=None)
        with self.assertRaisesRegex(validator.Refusal, "invalid_ci_status"):
            self.verify()
        self.setUp()
        self.data[self.routes[1]]["path"] = ".github/workflows/other.yml"
        with self.assertRaisesRegex(validator.Refusal, "wrong_ci_workflow"):
            self.verify()

    def test_replaced_check_during_verification_refuses(self):
        def api(route):
            data = self.api(route)
            if len(self.calls) == 6:
                data["check_runs"][0]["id"] = 78
            return data
        with self.assertRaisesRegex(validator.Refusal, "validate_check_changed"):
            validator.verify(164, "b" * 40, "4deb1d30edc7ccb8ced7c8438930ca1310c3775b", api)

    def test_failures_cancellation_skips_and_neutral_never_pass(self):
        for index, key in ((0, "check_runs"), (2, "workflow_runs"), (3, "jobs"), (4, None)):
            for conclusion in ("failure", "cancelled", "skipped", "neutral", "timed_out", None):
                with self.subTest(index=index, conclusion=conclusion):
                    self.setUp()
                    obj = self.data[self.routes[index]]
                    if key:
                        obj = obj[key][0]
                    obj["conclusion"] = conclusion
                    with self.assertRaises(validator.Refusal):
                        self.verify()

    def test_pending_or_absent_check_cannot_be_success(self):
        self.data[self.routes[0]] = {"total_count": 0, "check_runs": []}
        self.assertEqual(self.verify(), {"status": "pending"})
        self.setUp()
        self.data[self.routes[0]]["check_runs"][0].update(status="in_progress", conclusion=None)
        self.assertEqual(self.verify(), {"status": "pending"})
        self.setUp()
        self.data[self.routes[2]]["workflow_runs"][0].update(status="in_progress", conclusion=None)
        self.assertEqual(self.verify()["status"], "pending")

    def test_input_id_validation_precedes_network(self):
        for pr, base, head in ((0, "b" * 40, "a" * 40), (164, "main", "a" * 40),
                               (164, "b" * 40, "a" * 40 + "/other")):
            with self.assertRaises(validator.Refusal):
                validator.verify(pr, base, head, self.api)
        self.assertEqual(self.calls, [])

    def test_api_get_is_bounded_readonly_and_refusals_do_not_echo_text(self):
        for raw, code in ((b'{"safe":true}', 0), (b'private malformed response', 0),
                          (b'{}', 1), (b'{"a":1,"a":2}', 0), (b'x' * 33, 0)):
            with self.subTest(raw=raw):
                def run(args, **kwargs):
                    self.assertIn("GET", args)
                    self.assertEqual(kwargs["timeout"], 30)
                    self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
                    kwargs["stdout"].write(raw)
                    return subprocess.CompletedProcess(args, code)
                with patch.object(validator.subprocess, "run", run), patch.object(validator, "MAX_RESPONSE_BYTES", 32):
                    if raw == b'{"safe":true}':
                        self.assertEqual(validator.github_get("fixture"), {"safe": True})
                    else:
                        with self.assertRaises(validator.Refusal) as result:
                            validator.github_get("fixture")
                        self.assertNotIn("private", str(result.exception))

    def test_api_timeout_refuses_and_closes_output(self):
        outputs = []
        def run(args, **kwargs):
            outputs.append(kwargs["stdout"])
            raise subprocess.TimeoutExpired(args, 30)
        with patch.object(validator.subprocess, "run", run):
            with self.assertRaisesRegex(validator.Refusal, "github_read_unavailable"):
                validator.github_get("fixture")
        self.assertTrue(outputs[0].closed)


if __name__ == "__main__":
    unittest.main()
