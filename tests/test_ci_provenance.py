"""Synthetic GitHub API boundaries; no network, credentials or real merges."""

from copy import deepcopy
import importlib.util
import io
import itertools
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
             "check_suite": {"id": 88}, "pull_requests": deepcopy(prs),
             "status": "completed", "conclusion": "success"}
    run = {"id": 99, "workflow_id": 123, "path": ".github/workflows/ci.yml",
           "run_attempt": 1, "check_suite_id": 88, "head_sha": head,
           "pull_requests": deepcopy(prs), "event": "pull_request",
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
        # GitHub answers an empty collection when no dispatch run has the suite.
        f"{prefix}/actions/workflows/123/runs?check_suite_id=88&head_sha={head}&event=workflow_dispatch&per_page=100":
            {"total_count": 0, "workflow_runs": []},
    }


POLICY_CHECKS = ("policy-workflow-lint", "policy-secret-scan")
# Dispatch extras as (check id, check suite id, run id); the PR check is 77/88/99.
EXTRAS = ((76, 89, 98), (75, 87, 97), (74, 86, 96))


def policy_fixture(base="b" * 40, head="4deb1d30edc7ccb8ced7c8438930ca1310c3775b",
                   suites=(501,)):
    """policy-locks.yml runs: one suite and run per pull_request_target event."""

    prefix = "repos/jeter-1/hass-mcp-admin"
    repo = {"url": f"https://api.github.com/{prefix}"}
    prs = [{"number": 164, "base": {"ref": "main", "sha": base, "repo": repo},
            "head": {"sha": head, "repo": repo}}]
    data = {f"{prefix}/actions/workflows/policy-locks.yml":
            {"id": 456, "path": ".github/workflows/policy-locks.yml", "state": "active"}}
    for offset, name in enumerate(POLICY_CHECKS):
        checks = [{"id": suite * 10 + offset, "name": name, "head_sha": head,
                   "app": {"id": 15368, "slug": "github-actions"},
                   "check_suite": {"id": suite}, "pull_requests": deepcopy(prs),
                   "status": "completed", "conclusion": "success"} for suite in suites]
        data[f"{prefix}/commits/{head}/check-runs?check_name={name}&filter=latest"
             "&per_page=100"] = {"total_count": len(checks), "check_runs": checks}
    for suite in suites:
        run = {"id": suite + 1000, "workflow_id": 456,
               "path": ".github/workflows/policy-locks.yml", "run_attempt": 1,
               "check_suite_id": suite, "head_sha": head, "pull_requests": deepcopy(prs),
               "event": "pull_request_target",
               "repository": {"full_name": "jeter-1/hass-mcp-admin"},
               "head_repository": {"full_name": "jeter-1/hass-mcp-admin"},
               "status": "completed", "conclusion": "success"}
        data[f"{prefix}/actions/workflows/456/runs?check_suite_id={suite}&head_sha={head}"
             "&event=pull_request_target&per_page=100"] = {
                 "total_count": 1, "workflow_runs": [run]}
        jobs = [{"id": suite * 10 + offset, "name": name, "run_id": suite + 1000,
                 "run_attempt": 1, "head_sha": head, "status": "completed",
                 "conclusion": "success",
                 "check_run_url": f"https://api.github.com/{prefix}/check-runs/"
                                  f"{suite * 10 + offset}"}
                for offset, name in enumerate(POLICY_CHECKS)]
        data[f"{prefix}/actions/runs/{suite + 1000}/attempts/1/jobs?per_page=100"] = {
            "total_count": len(jobs), "jobs": jobs}
        data[f"{prefix}/actions/runs/{suite + 1000}"] = deepcopy(run)
    return data


def required_fixture(base="b" * 40, head="4deb1d30edc7ccb8ced7c8438930ca1310c3775b"):
    """Every required check (validate first, so its routes keep their indices)."""

    return fixture(base, head) | policy_fixture(base, head)


# Existing workflow-shell tests use the same API fixture through a fake gh.
# The actual protected helper still runs; this substitutes only GitHub I/O,
# answering ``gh api --include`` as gh does (status line, headers, body).
MOCK_API_SHELL = '''elif [[ -n "${MOCK_PROVENANCE_JSON:-}" ]]; then
  python3 - "$@" <<'MOCK_API'
import json, os, sys
data = json.loads(os.environ["MOCK_PROVENANCE_JSON"])
include = "--include" in sys.argv
if sys.argv[2] not in data:
    if include:
        sys.stdout.write("HTTP/2.0 404 Not Found\\r\\n\\r\\n{}")
    raise SystemExit(1 if include else 2)
if include:
    sys.stdout.write("HTTP/2.0 200 OK\\r\\nContent-Type: application/json\\r\\n\\r\\n")
print(json.dumps(data[sys.argv[2]]))
MOCK_API
'''
# The same status line and blank header line for other fake gh branches.
MOCK_INCLUDE_SHELL = '''include_header() {
  if [[ " $* " == *" --include "* ]]; then printf 'HTTP/2.0 200 OK\\r\\n\\r\\n'; fi
}
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
        ok = b"HTTP/2.0 200 OK\r\nContent-Type: application/json\r\n\r\n"
        missing = b"HTTP/2.0 404 Not Found\r\n\r\n"
        for raw, code in ((ok + b'{"safe":true}', 0), (ok + b'private malformed response', 0),
                          (missing + b'{"message":"private"}', 1), (ok + b'{"a":1,"a":2}', 0),
                          (ok + b'x' * 33, 0)):
            with self.subTest(raw=raw):
                calls = []
                def run(args, **kwargs):
                    calls.append(args)
                    self.assertIn("GET", args)
                    self.assertIn("--include", args)
                    self.assertEqual(kwargs["timeout"], 30)
                    # gh diagnostics go to a private file for classification
                    # only; they never reach the job log.
                    self.assertNotIn(kwargs["stderr"], (None, subprocess.PIPE))
                    self.assertTrue(hasattr(kwargs["stderr"], "read"))
                    kwargs["stdout"].write(raw)
                    return subprocess.CompletedProcess(args, code)
                with patch.object(validator.subprocess, "run", run), \
                        patch.object(validator, "MAX_RESPONSE_BYTES", 32), \
                        patch.object(validator.time, "sleep") as sleep, \
                        patch("sys.stderr", new_callable=io.StringIO):
                    if raw.endswith(b'{"safe":true}'):
                        self.assertEqual(validator.github_get("fixture"), {"safe": True})
                    else:
                        with self.assertRaises(validator.Refusal) as result:
                            validator.github_get("fixture")
                        self.assertNotIn("private", str(result.exception))
                # A 404, malformed or oversized response is never retried.
                self.assertEqual(len(calls), 1)
                sleep.assert_not_called()

    def test_api_timeout_retries_within_the_bound_then_refuses_and_closes_output(self):
        outputs = []
        def run(args, **kwargs):
            outputs.append(kwargs["stdout"])
            raise subprocess.TimeoutExpired(args, 30)
        with patch.object(validator.subprocess, "run", run), \
                patch.object(validator.time, "sleep") as sleep, \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            with self.assertRaisesRegex(validator.Refusal, "github_read_unavailable"):
                validator.github_get("fixture")
        self.assertEqual(len(outputs), validator.MAX_ATTEMPTS)
        self.assertEqual(sleep.call_count, validator.MAX_ATTEMPTS - 1)
        self.assertTrue(all(output.closed for output in outputs))
        self.assertIn("host api.github.com, last status timeout", stderr.getvalue())

    def test_api_rate_limit_is_retried_then_succeeds(self):
        responses = [(b"HTTP/2.0 429 Too Many Requests\r\nRetry-After: 3\r\n\r\n{}", 1),
                     (b"HTTP/2.0 503 Service Unavailable\r\n\r\n{}", 1),
                     (b"HTTP/2.0 200 OK\r\n\r\n{\"safe\":true}", 0)]
        def run(args, **kwargs):
            raw, code = responses.pop(0)
            kwargs["stdout"].write(raw)
            return subprocess.CompletedProcess(args, code)
        with patch.object(validator.subprocess, "run", run), \
                patch.object(validator.time, "sleep") as sleep:
            self.assertEqual(validator.github_get("fixture"), {"safe": True})
        self.assertEqual(responses, [])
        self.assertEqual(sleep.call_count, 2)
        self.assertGreaterEqual(sleep.call_args_list[0].args[0], 3)


class DispatchValidateExtraTests(unittest.TestCase):
    """HAMCP-521: a full-tier ci.yml workflow_dispatch on the PR head adds a
    second validate check. Exactly one pull_request check stays the bound
    producer; each extra must be a successful ci.yml dispatch on the head."""

    head = "4deb1d30edc7ccb8ced7c8438930ca1310c3775b"
    prefix = "repos/jeter-1/hass-mcp-admin"

    def setUp(self):
        self.data = fixture()
        self.calls = []
        self.add_dispatch()

    def api(self, route):
        self.calls.append(route)
        return deepcopy(self.data[route])

    def verify(self):
        return validator.verify(164, "b" * 40, self.head, self.api)

    def runs_route(self, suite, event):
        return (f"{self.prefix}/actions/workflows/123/runs?check_suite_id={suite}"
                f"&head_sha={self.head}&event={event}&per_page=100")

    def set_runs(self, suite, event, runs):
        self.data[self.runs_route(suite, event)] = {
            "total_count": len(runs), "workflow_runs": runs}

    @property
    def checks(self):
        return self.data[f"{self.prefix}/commits/{self.head}/check-runs"
                         "?check_name=validate&filter=latest&per_page=100"]

    def extra_check(self, check_id=76):
        return next(check for check in self.checks["check_runs"] if check["id"] == check_id)

    def extra_run(self, suite=89):
        return self.data[self.runs_route(suite, "workflow_dispatch")]["workflow_runs"][0]

    def extra_jobs(self, run_id=98):
        return self.data[f"{self.prefix}/actions/runs/{run_id}/attempts/1/jobs?per_page=100"]

    def add_dispatch(self, first=False, extra=EXTRAS[0]):
        check_id, suite, run_id = extra
        check = deepcopy(self.extra_check(77))
        check.update(id=check_id, check_suite={"id": suite}, pull_requests=[])
        if first:
            self.checks["check_runs"].insert(0, check)
        else:
            self.checks["check_runs"].append(check)
        self.checks["total_count"] = len(self.checks["check_runs"])
        run = deepcopy(self.data[f"{self.prefix}/actions/runs/99"])
        run.update(id=run_id, check_suite_id=suite, event="workflow_dispatch", pull_requests=[])
        self.set_runs(suite, "pull_request", [])
        self.set_runs(suite, "workflow_dispatch", [run])
        job = {"id": check_id, "name": "validate", "run_id": run_id, "run_attempt": 1,
               "head_sha": self.head, "status": "completed", "conclusion": "success",
               "check_run_url": f"https://api.github.com/{self.prefix}/check-runs/{check_id}"}
        self.data[f"{self.prefix}/actions/runs/{run_id}/attempts/1/jobs?per_page=100"] = {
            "total_count": 1, "jobs": [job]}
        self.data[f"{self.prefix}/actions/runs/{run_id}"] = deepcopy(run)

    def add_extras(self, count, pr_index=0):
        """The PR check and ``count`` dispatch extras, the PR check at ``pr_index``."""

        self.data = fixture()
        self.calls = []
        for extra in EXTRAS[:count]:
            self.add_dispatch(extra=extra)
        listed = self.checks["check_runs"]
        listed.insert(pr_index, listed.pop(0))

    def receipt(self, extra):
        check_id, suite, run_id = extra
        return {"run_id": run_id, "run_attempt": 1, "job_id": check_id,
                "check_run_id": check_id, "check_suite_id": suite}

    def layouts(self):
        """Two and three extras, with the PR check at every position."""

        for count in (2, 3):
            for pr_index in range(count + 1):
                yield count, pr_index

    def reset(self, first=False):
        self.data = fixture()
        self.calls = []
        self.add_dispatch(first)

    def test_successful_dispatch_extra_keeps_the_pull_request_receipt(self):
        for first in (False, True):
            with self.subTest(first=first):
                self.reset(first)
                result = self.verify()
                self.assertEqual(result["status"], "success")
                self.assertEqual((result["run_id"], result["check_run_id"],
                                  result["check_suite_id"]), (99, 77, 88))
                self.assertEqual(result["dispatch_runs"], [{
                    "run_id": 98, "run_attempt": 1, "job_id": 76,
                    "check_run_id": 76, "check_suite_id": 89}])
                self.assertNotIn("runs", result)

    def test_dispatch_extra_that_names_the_pull_request_is_also_accepted(self):
        # Whether GitHub associates a dispatch check with the open PR is not
        # relied on either way.
        prs = deepcopy(self.checks["check_runs"][0]["pull_requests"])
        self.extra_check()["pull_requests"] = prs
        self.assertEqual(self.verify()["status"], "success")

    def test_pending_dispatch_extra_waits(self):
        self.extra_check().update(status="in_progress", conclusion=None)
        self.assertEqual(self.verify(), {"status": "pending"})
        self.setUp()
        self.extra_run().update(status="in_progress", conclusion=None)
        self.data[f"{self.prefix}/actions/runs/98"].update(status="in_progress",
                                                         conclusion=None)
        self.assertEqual(self.verify()["status"], "pending")

    def test_failed_dispatch_extra_refuses(self):
        for target in ("check", "run", "job", "reread"):
            for conclusion in ("failure", "cancelled", "skipped", "neutral", "timed_out"):
                with self.subTest(target=target, conclusion=conclusion):
                    self.setUp()
                    obj = {"check": self.extra_check, "run": self.extra_run,
                           "job": lambda: self.extra_jobs()["jobs"][0],
                           "reread": lambda: self.data[f"{self.prefix}/actions/runs/98"],
                           }[target]()
                    obj["conclusion"] = conclusion
                    with self.assertRaisesRegex(validator.Refusal, "unsuccessful_ci"):
                        self.verify()

    def test_extra_from_another_event_or_workflow_refuses(self):
        # Another event (push, schedule, ...) or another workflow's same-name
        # check has no ci.yml pull_request or dispatch run for its suite.
        self.set_runs(89, "workflow_dispatch", [])
        with self.assertRaisesRegex(validator.Refusal, "missing_or_ambiguous_ci_run"):
            self.verify()
        changes = (("event", "push"), ("path", ".github/workflows/other.yml"),
                   ("workflow_id", 2), ("check_suite_id", 1), ("head_sha", "a" * 40))
        for key, value in changes:
            with self.subTest(key=key):
                self.setUp()
                self.extra_run()[key] = value
                with self.assertRaisesRegex(validator.Refusal, "wrong_ci_run"):
                    self.verify()
        for key in ("repository", "head_repository"):
            with self.subTest(key=key):
                self.setUp()
                self.extra_run()[key] = {"full_name": "other/fork"}
                with self.assertRaisesRegex(validator.Refusal, "foreign_ci_run"):
                    self.verify()

    def test_suite_with_both_events_or_a_duplicate_run_refuses(self):
        run = deepcopy(self.extra_run())
        self.set_runs(89, "pull_request", [run])
        with self.assertRaisesRegex(validator.Refusal, "missing_or_ambiguous_ci_run"):
            self.verify()
        self.setUp()
        self.set_runs(89, "workflow_dispatch", [self.extra_run()] * 2)
        with self.assertRaisesRegex(validator.Refusal, "missing_or_ambiguous_ci_run"):
            self.verify()

    def test_two_pull_request_checks_or_only_dispatch_checks_refuse(self):
        run = deepcopy(self.data[f"{self.prefix}/actions/runs/99"])
        run.update(id=98, check_suite_id=89)
        self.set_runs(89, "workflow_dispatch", [])
        self.set_runs(89, "pull_request", [run])
        with self.assertRaisesRegex(validator.Refusal, "ambiguous_validate_check"):
            self.verify()
        self.setUp()
        self.set_runs(88, "pull_request", [])
        self.set_runs(88, "workflow_dispatch", [deepcopy(self.extra_run())])
        with self.assertRaisesRegex(validator.Refusal, "ambiguous_validate_check"):
            self.verify()

    def test_pull_request_binding_is_still_required_beside_an_extra(self):
        run_route = self.runs_route(88, "pull_request")
        for target, change in itertools.product(("check", "run"),
                                                ("base", "head", "number", "missing")):
            with self.subTest(target=target, change=change):
                self.setUp()
                # A run change is made in its listing and its re-read alike,
                # so the binding refusal, not ci_run_changed, is what refuses.
                if target == "check":
                    bindings = [self.checks["check_runs"][0]["pull_requests"]]
                else:
                    bindings = [self.data[run_route]["workflow_runs"][0]["pull_requests"],
                                self.data[f"{self.prefix}/actions/runs/99"]["pull_requests"]]
                for prs in bindings:
                    if change in ("base", "head"):
                        prs[0][change]["sha"] = "c" * 40
                    elif change == "number":
                        prs[0]["number"] = 999
                    else:
                        prs.clear()
                with self.assertRaisesRegex(validator.Refusal, "_pr_binding"):
                    self.verify()

    def test_extra_from_another_app_or_head_refuses(self):
        for key, value, category in (("app", {"id": 1, "slug": "github-actions"},
                                      "wrong_check_producer"),
                                     ("head_sha", "a" * 40, "wrong_validate_check"),
                                     ("name", "Validate", "wrong_validate_check")):
            with self.subTest(key=key):
                self.setUp()
                self.extra_check()[key] = value
                with self.assertRaisesRegex(validator.Refusal, category):
                    self.verify()

    def test_dispatch_job_must_bind_its_own_check(self):
        url = f"https://api.github.com/{self.prefix}/check-runs/77"
        self.extra_jobs()["jobs"][0]["check_run_url"] = url
        with self.assertRaisesRegex(validator.Refusal, "wrong_ci_job"):
            self.verify()

    def test_dispatch_rerun_or_replaced_extra_during_verification_refuses(self):
        self.data[f"{self.prefix}/actions/runs/98"]["run_attempt"] = 2
        with self.assertRaisesRegex(validator.Refusal, "ci_run_changed"):
            self.verify()
        self.setUp()
        def api(route):
            data = self.api(route)
            if route.startswith(f"{self.prefix}/commits/") and len(self.calls) > 1:
                data["check_runs"][1]["conclusion"] = "failure"
            return data
        with self.assertRaisesRegex(validator.Refusal, "validate_check_changed"):
            validator.verify(164, "b" * 40, self.head, api)

    # Codex r1 R1: every check past the first two must also be verified.

    def test_two_or_three_successful_extras_list_every_receipt_in_any_order(self):
        for count, pr_index in self.layouts():
            with self.subTest(count=count, pr_index=pr_index):
                self.add_extras(count, pr_index)
                result = self.verify()
                self.assertEqual(result["status"], "success")
                self.assertEqual((result["run_id"], result["check_run_id"],
                                  result["check_suite_id"]), (99, 77, 88))
                self.assertEqual(result["dispatch_runs"],
                                 [self.receipt(extra) for extra in EXTRAS[:count]])
        # The receipts follow the listing order, whatever it is.
        self.add_extras(3)
        self.checks["check_runs"].reverse()
        self.assertEqual(self.verify()["dispatch_runs"],
                         [self.receipt(extra) for extra in reversed(EXTRAS)])

    def test_any_pending_extra_among_several_waits(self):
        for count, pr_index in self.layouts():
            for check_id, suite, run_id in EXTRAS[:count]:
                with self.subTest(count=count, pr_index=pr_index, extra=check_id, target="check"):
                    self.add_extras(count, pr_index)
                    self.extra_check(check_id).update(status="in_progress", conclusion=None)
                    self.assertEqual(self.verify(), {"status": "pending"})
                with self.subTest(count=count, pr_index=pr_index, extra=check_id, target="run"):
                    self.add_extras(count, pr_index)
                    for run in (self.extra_run(suite), self.data[f"{self.prefix}/actions/runs/{run_id}"]):
                        run.update(status="in_progress", conclusion=None)
                    self.assertEqual(self.verify()["status"], "pending")

    def test_any_failed_extra_among_several_refuses(self):
        for count, pr_index in self.layouts():
            for check_id, suite, run_id in EXTRAS[:count]:
                for target in ("check", "run", "job", "reread"):
                    with self.subTest(count=count, pr_index=pr_index, extra=check_id, target=target):
                        self.add_extras(count, pr_index)
                        obj = {"check": lambda: self.extra_check(check_id),
                               "run": lambda: self.extra_run(suite),
                               "job": lambda: self.extra_jobs(run_id)["jobs"][0],
                               "reread": lambda: self.data[f"{self.prefix}/actions/runs/{run_id}"],
                               }[target]()
                        obj["conclusion"] = "failure"
                        with self.assertRaisesRegex(validator.Refusal, "unsuccessful_ci"):
                            self.verify()

    def test_a_wrong_event_among_several_extras_refuses(self):
        for count, pr_index in self.layouts():
            for check_id, suite, run_id in EXTRAS[:count]:
                label = dict(count=count, pr_index=pr_index, extra=check_id)
                with self.subTest(**label, change="no ci.yml run"):
                    self.add_extras(count, pr_index)
                    self.set_runs(suite, "workflow_dispatch", [])
                    with self.assertRaisesRegex(validator.Refusal, "missing_or_ambiguous_ci_run"):
                        self.verify()
                with self.subTest(**label, change="push"):
                    self.add_extras(count, pr_index)
                    self.extra_run(suite)["event"] = "push"
                    with self.assertRaisesRegex(validator.Refusal, "wrong_ci_run"):
                        self.verify()
                with self.subTest(**label, change="second pull_request check"):
                    self.add_extras(count, pr_index)
                    run = deepcopy(self.data[f"{self.prefix}/actions/runs/99"])
                    run.update(id=run_id, check_suite_id=suite)
                    self.set_runs(suite, "workflow_dispatch", [])
                    self.set_runs(suite, "pull_request", [run])
                    with self.assertRaisesRegex(validator.Refusal, "ambiguous_validate_check"):
                        self.verify()

    def test_pull_request_binding_is_checked_wherever_the_pr_check_is_listed(self):
        for count, pr_index in self.layouts():
            with self.subTest(count=count, pr_index=pr_index):
                self.add_extras(count, pr_index)
                self.extra_check(77)["pull_requests"][0]["head"]["sha"] = "c" * 40
                with self.assertRaisesRegex(validator.Refusal, "_pr_binding"):
                    self.verify()

    def test_any_check_changed_or_rerun_during_verification_refuses(self):
        commits = f"{self.prefix}/commits/"
        for count, pr_index in self.layouts():
            for position in range(count + 1):
                with self.subTest(count=count, pr_index=pr_index, position=position):
                    self.add_extras(count, pr_index)

                    def api(route, position=position):
                        data = self.api(route)
                        if route.startswith(commits) and len(self.calls) > 1:
                            data["check_runs"][position]["conclusion"] = "failure"
                        return data

                    with self.assertRaisesRegex(validator.Refusal, "validate_check_changed"):
                        validator.verify(164, "b" * 40, self.head, api)
            for check_id, _, run_id in EXTRAS[:count]:
                with self.subTest(count=count, pr_index=pr_index, rerun=check_id):
                    self.add_extras(count, pr_index)
                    self.data[f"{self.prefix}/actions/runs/{run_id}"]["run_attempt"] = 2
                    with self.assertRaisesRegex(validator.Refusal, "ci_run_changed"):
                        self.verify()


class PolicyCheckProvenanceTests(unittest.TestCase):
    """CODEX-022: policy checks bind to policy-locks.yml, every suite on the head."""

    head = "4deb1d30edc7ccb8ced7c8438930ca1310c3775b"
    prefix = "repos/jeter-1/hass-mcp-admin"

    def setUp(self):
        self.data = policy_fixture(suites=(501, 502))
        self.calls = []

    def api(self, route):
        self.calls.append(route)
        return deepcopy(self.data[route])

    def checks(self, name="policy-workflow-lint"):
        return self.data[f"{self.prefix}/commits/{self.head}/check-runs?check_name={name}"
                         "&filter=latest&per_page=100"]

    def verify(self, name="policy-workflow-lint"):
        return validator.verify(164, "b" * 40, self.head, self.api, check_name=name)

    def test_every_event_suite_from_policy_locks_succeeds(self):
        for name in POLICY_CHECKS:
            with self.subTest(name=name):
                self.setUp()
                result = self.verify(name)
                self.assertEqual(result["status"], "success")
                self.assertEqual(result["check_name"], name)
                self.assertEqual([run["check_suite_id"] for run in result["runs"]],
                                 [501, 502])
                self.assertEqual(len(self.calls), 9)

    def test_same_name_check_from_another_workflow_cannot_supply_it(self):
        spoof = deepcopy(self.checks()["check_runs"][0])
        spoof.update(id=999, check_suite={"id": 777})  # e.g. a ci.yml job copy
        self.checks()["check_runs"].append(spoof)
        self.checks()["total_count"] = 3
        self.data[f"{self.prefix}/actions/workflows/456/runs?check_suite_id=777"
                  f"&head_sha={self.head}&event=pull_request_target&per_page=100"] = {
                      "total_count": 0, "workflow_runs": []}
        with self.assertRaisesRegex(validator.Refusal, "missing_or_ambiguous_ci_run"):
            self.verify()

    def test_any_failed_suite_refuses_and_any_pending_suite_waits(self):
        self.checks()["check_runs"][1]["conclusion"] = "failure"
        with self.assertRaisesRegex(validator.Refusal, "unsuccessful_ci"):
            self.verify()
        self.setUp()
        self.checks()["check_runs"][0].update(status="in_progress", conclusion=None)
        self.assertEqual(self.verify(), {"status": "pending"})

    def test_wrong_event_job_or_producer_refuses(self):
        run_route = (f"{self.prefix}/actions/workflows/456/runs?check_suite_id=502"
                     f"&head_sha={self.head}&event=pull_request_target&per_page=100")
        jobs_route = f"{self.prefix}/actions/runs/1502/attempts/1/jobs?per_page=100"
        for change in ("event", "path", "job", "app"):
            with self.subTest(change=change):
                self.setUp()
                if change == "event":
                    self.data[run_route]["workflow_runs"][0]["event"] = "pull_request"
                elif change == "path":
                    self.data[run_route]["workflow_runs"][0]["path"] = ".github/workflows/ci.yml"
                elif change == "job":
                    self.data[jobs_route]["jobs"] = [
                        job for job in self.data[jobs_route]["jobs"]
                        if job["name"] != "policy-workflow-lint"]
                    self.data[jobs_route]["total_count"] = 1
                else:
                    self.checks()["check_runs"][1]["app"]["id"] = 1
                with self.assertRaises(validator.Refusal):
                    self.verify()

    def test_replaced_policy_check_during_verification_refuses(self):
        def api(route):
            data = self.api(route)
            if len(self.calls) == 9:
                data["check_runs"][1]["conclusion"] = "failure"
            return data
        with self.assertRaisesRegex(validator.Refusal, "validate_check_changed"):
            validator.verify(164, "b" * 40, self.head, api, check_name="policy-workflow-lint")

    def test_unknown_check_name_refuses_before_network(self):
        for name in ("Validate", "policy-workflow-lint ", "other"):
            with self.assertRaisesRegex(validator.Refusal, "unknown_required_check"):
                validator.verify(164, "b" * 40, self.head, self.api, check_name=name)
        self.assertEqual(self.calls, [])

    def test_validate_still_accepts_only_one_pull_request_producer_check(self):
        data = fixture()
        route = next(iter(data))
        data[route]["check_runs"].append(deepcopy(data[route]["check_runs"][0]))
        data[route]["total_count"] = 2
        with self.assertRaisesRegex(validator.Refusal, "ambiguous_validate_check"):
            validator.verify(164, "b" * 40, self.head, lambda r: deepcopy(data[r]))

    def test_producers_are_the_explicit_required_check_list(self):
        self.assertEqual(validator.PRODUCERS, {
            "validate": ("ci.yml", "pull_request"),
            "policy-workflow-lint": ("policy-locks.yml", "pull_request_target"),
            "policy-secret-scan": ("policy-locks.yml", "pull_request_target"),
        })


if __name__ == "__main__":
    unittest.main()
