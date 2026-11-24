"""Read-only release readiness sweep (HAMCP-458); a fake GitHub reader only, no network."""

import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hamcp458_release_readiness", ROOT / "scripts/release_readiness.py")
readiness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(readiness)
support_window = sys.modules["support_window"]

REPO = "example-owner/example-repo"
HEAD = "a" * 40
CI_RUN, CORE_RUN = 2001, 3001
CI_RUNS = f"repos/{REPO}/actions/workflows/ci.yml/runs?head_sha={HEAD}&per_page=100"
CORE_RUNS = f"repos/{REPO}/actions/workflows/core-release-evidence.yml/runs?head_sha={HEAD}&per_page=100"
WINDOW = support_window.load()
CORE = WINDOW["core"]["current"]["version"]
LANES = readiness.full_tier_lanes(WINDOW)
GROUPS = readiness.lane_groups()
CORE_JOBS = readiness.core_jobs(CORE, "pull_request", GROUPS)
RELEASE_JOBS = readiness.core_jobs(CORE, "workflow_dispatch", GROUPS)
MARKER = "INJECTED|response-text"


def jobs_route(run_id, page=1):
    return f"repos/{REPO}/actions/runs/{run_id}/jobs?filter=latest&per_page=100&page={page}"


def encode(value):
    return json.dumps(value).encode()


def run(run_id, workflow=".github/workflows/ci.yml", event="pull_request", status="completed",
        conclusion="success", head=HEAD, branch="agent/example", owner=REPO, head_owner=REPO):
    return {"id": run_id, "run_attempt": 1, "path": workflow, "event": event, "status": status,
            "conclusion": conclusion, "head_sha": head, "head_branch": branch,
            "repository": {"full_name": owner}, "head_repository": {"full_name": head_owner}}


def core_run(run_id, **fields):
    return run(run_id, workflow=".github/workflows/core-release-evidence.yml", **fields)


def jobs(names, conclusion="success"):
    return {"total_count": len(names), "jobs": [{"name": name, "conclusion": conclusion} for name in names]}


class FakeGitHub:
    """Routes to bodies or exceptions; any other route is a test failure, never a read."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    def __call__(self, route):
        self.calls.append(route)
        if route not in self.routes:
            raise AssertionError(f"unexpected read {route}")
        body = self.routes[route]
        if isinstance(body, BaseException):
            raise body
        return body if isinstance(body, bytes) else encode(body)


def ready_routes():
    return {
        CI_RUNS: {"total_count": 1, "workflow_runs": [run(CI_RUN)]},
        jobs_route(CI_RUN): jobs(["validate", *LANES, "unit tests shard 0"]),
        CORE_RUNS: {"total_count": 1, "workflow_runs": [core_run(CORE_RUN)]},
        jobs_route(CORE_RUN): jobs(["prepare", *CORE_JOBS]),
    }


def sweep(routes, **options):
    github = FakeGitHub(routes)
    return readiness.sweep(REPO, HEAD, github, **options), github


def detail(report, name):
    (item,) = [item for item in report["parts"] if item["part"] == name]
    return item["status"], item["detail"]


class ReadinessTests(unittest.TestCase):
    def test_every_checked_part_ready(self):
        github = FakeGitHub(ready_routes())
        out = io.StringIO()
        self.assertEqual(readiness.main(["--repo", REPO, "--head", HEAD], read=github, stdout=out), 0)
        text = out.getvalue()
        self.assertIn(f"| Support window | ready | current pair Core {CORE} with ha-mcp "
                      f"{WINDOW['ha_mcp']['current']['version']} |", text)
        self.assertIn(f"| Contract matrix | ready | run {CI_RUN} attempt 1 (pull_request): validate and "
                      f"{', '.join(LANES)} succeeded |", text)
        self.assertIn(f"| Core evidence | ready | run {CORE_RUN} attempt 1 (pull_request): every lane-group and "
                      f"lanes job of coverage and fixture proved Core {CORE} |", text)
        self.assertIn("Checked parts ready: 3 of 3. Every checked part is ready.", text)
        # Only these four GETs; nothing is dispatched, rerun or written.
        self.assertEqual(github.calls, [CI_RUNS, jobs_route(CI_RUN), CORE_RUNS, jobs_route(CORE_RUN)])

    def test_json_report_and_manual_parts_are_never_ready(self):
        out = io.StringIO()
        self.assertEqual(readiness.main(["--repo", REPO, "--head", HEAD, "--format", "json"],
                                        read=FakeGitHub(ready_routes()), stdout=out), 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report["schema_version"], 1)
        self.assertTrue(report["ready"])
        self.assertEqual([item["status"] for item in report["parts"]], ["ready"] * 3)
        self.assertEqual(report["pair"], {"core": CORE, "ha_mcp": WINDOW["ha_mcp"]["current"]["version"]})
        self.assertEqual([item["part"] for item in report["manual"]],
                         ["Upstream-read audit", "Output-expanding parameters (F5)", "Exact-version gates (F2)",
                          "Live re-checks"])
        self.assertTrue(all("status" not in item for item in report["manual"]))

    def test_core_jobs_follow_the_evidence_workflow(self):
        evidence = sys.modules["core_release_evidence"]
        self.assertEqual(sorted(GROUPS), sorted(evidence.LANE_GROUPS))
        self.assertEqual(GROUPS, [group for group in evidence.groups() if group in GROUPS])
        # A pull request proves the coverage and fixture cases; it has no selected-release job.
        self.assertEqual(CORE_JOBS, [f"Core evidence group coverage {group}" for group in GROUPS]
                         + [f"Core evidence coverage {CORE}"]
                         + [f"Core evidence group fixture {group}" for group in GROUPS]
                         + [f"Core evidence fixture {CORE}"])
        # A scheduled or dispatched run proves the release case after selecting the release.
        self.assertEqual(RELEASE_JOBS, [f"core-release-attempt-{CORE}"]
                         + [f"Core evidence group release {group}" for group in GROUPS]
                         + [f"Core evidence release {CORE}"])
        self.assertEqual(readiness.core_jobs(CORE, "schedule", GROUPS), RELEASE_JOBS)
        self.assertTrue(LANES and all(name.startswith("Real HA ha-") for name in LANES))

    def test_default_reader_is_the_bounded_get(self):
        with mock.patch.object(readiness.retry, "api_get", return_value=b"{}") as api_get:
            self.assertEqual(readiness.github_read("repos/o/r/actions/runs/1"), b"{}")
        api_get.assert_called_once_with("repos/o/r/actions/runs/1")


class ContractMatrixTests(unittest.TestCase):
    def assert_contract(self, routes, expected, **options):
        report, _ = sweep(routes, **options)
        self.assertFalse(report["ready"])
        self.assertEqual(detail(report, "Contract matrix"), ("not ready", expected))
        # One part's gap never hides another part's result.
        self.assertEqual(detail(report, "Core evidence")[0], "ready")

    def test_no_run_for_the_head_is_not_ready(self):
        routes = ready_routes()
        routes[CI_RUNS] = {"total_count": 0, "workflow_runs": []}
        self.assert_contract(routes, "no ci.yml run of this repository for the head")

    def test_fork_other_workflow_event_or_head_runs_are_ignored(self):
        routes = ready_routes()
        routes[CI_RUNS] = {"workflow_runs": [
            run(9001, head_owner="fork-owner/example-repo"), run(9002, owner="other/example-repo"),
            run(9003, workflow=".github/workflows/policy-locks.yml"), run(9004, event="push"),
            run(9005, head="b" * 40), {**run(9006), "id": "9006"}, {**run(9007), "id": True}]}
        self.assert_contract(routes, "no ci.yml run of this repository for the head")

    def test_the_newest_own_run_is_judged(self):
        routes = ready_routes()
        routes[CI_RUNS] = {"workflow_runs": [run(CI_RUN), run(CI_RUN + 5, conclusion="failure"),
                                             run(CI_RUN + 9, head_owner="fork/example-repo")]}
        self.assert_contract(routes, f"run {CI_RUN + 5} attempt 1 (pull_request) concluded failure")

    def test_unfinished_or_unsuccessful_run_is_not_ready(self):
        for fields, expected in (({"status": "in_progress", "conclusion": None}, "is in_progress"),
                                 ({"status": "queued", "conclusion": None}, "is queued"),
                                 ({"conclusion": "cancelled"}, "concluded cancelled"),
                                 ({"conclusion": None}, "concluded unrecognized"),
                                 ({"status": MARKER}, "is unrecognized")):
            with self.subTest(fields=fields):
                routes = ready_routes()
                routes[CI_RUNS] = {"workflow_runs": [run(CI_RUN, **fields)]}
                self.assert_contract(routes, f"run {CI_RUN} attempt 1 (pull_request) {expected}")

    def test_validate_or_lane_absent_or_failed_is_not_ready(self):
        prefix = f"run {CI_RUN} attempt 1 (pull_request): "
        cases = (
            (jobs(LANES), "validate: absent"),
            (jobs(["validate"]), "; ".join(f"{lane}: absent" for lane in LANES)),
            ({"jobs": [{"name": "validate", "conclusion": "success"}]
              + [{"name": lane, "conclusion": "failure"} for lane in LANES]},
             "; ".join(f"{lane}: failure" for lane in LANES)),
            ({"jobs": [{"name": "validate", "conclusion": "success"}, {"name": "validate", "conclusion": "skipped"}]
              + [{"name": lane, "conclusion": "success"} for lane in LANES]}, "validate: skipped, success"),
        )
        for body, expected in cases:
            with self.subTest(expected=expected):
                routes = ready_routes()
                routes[jobs_route(CI_RUN)] = body
                self.assert_contract(routes, prefix + expected)

    def test_a_full_tier_lane_missing_from_the_run_is_not_ready(self):
        extra = "Real HA ha-2099-1-0 (rollback)"
        with mock.patch.object(readiness, "full_tier_lanes", return_value=[*LANES, extra]):
            self.assert_contract(ready_routes(), f"run {CI_RUN} attempt 1 (pull_request): {extra}: absent")

    def test_jobs_are_paged_and_bounded(self):
        routes = ready_routes()
        routes[jobs_route(CI_RUN)] = jobs([f"filler {n}" for n in range(100)])
        routes[jobs_route(CI_RUN, 2)] = jobs(["validate", *LANES])
        report, github = sweep(routes)
        self.assertTrue(report["ready"])
        self.assertIn(jobs_route(CI_RUN, 2), github.calls)
        for page in range(1, readiness.JOB_PAGES + 1):
            routes[jobs_route(CI_RUN, page)] = jobs([f"filler {page} {n}" for n in range(100)])
        self.assert_contract(routes, "unreadable: github_jobs_bound")


class CoreEvidenceTests(unittest.TestCase):
    def assert_core(self, routes, expected, **options):
        report, github = sweep(routes, **options)
        self.assertFalse(report["ready"])
        self.assertEqual(detail(report, "Core evidence"), ("not ready", expected))
        self.assertEqual(detail(report, "Contract matrix")[0], "ready")
        return github

    def test_no_pull_request_run_for_the_head_is_not_ready(self):
        routes = ready_routes()
        routes[CORE_RUNS] = {"workflow_runs": []}
        self.assert_core(routes, "no core-release-evidence.yml pull-request run of this repository for the head; "
                                 "pass --core-run")

    def test_the_newest_own_pull_request_run_for_the_head_is_judged(self):
        routes = ready_routes()
        routes[CORE_RUNS] = {"workflow_runs": [
            core_run(CORE_RUN), core_run(CORE_RUN - 1, conclusion="failure"),
            core_run(CORE_RUN + 1, event="workflow_dispatch"), core_run(CORE_RUN + 2, event="schedule"),
            core_run(CORE_RUN + 3, head_owner="fork/example-repo"), core_run(CORE_RUN + 4, owner="fork/example-repo"),
            core_run(CORE_RUN + 5, head="b" * 40), run(CORE_RUN + 6), core_run(str(CORE_RUN + 7)),
            core_run(True)]}
        report, github = sweep(routes)
        self.assertTrue(report["ready"])
        self.assertEqual(github.calls[-2:], [CORE_RUNS, jobs_route(CORE_RUN)])
        routes[CORE_RUNS]["workflow_runs"][1]["id"] = CORE_RUN + 8
        self.assert_core(routes, f"run {CORE_RUN + 8} attempt 1 (pull_request) concluded failure")

    def test_unfinished_unsuccessful_or_incomplete_run_is_not_ready(self):
        prefix = f"run {CORE_RUN} attempt 1 (pull_request)"
        for fields, expected in (({"status": "in_progress", "conclusion": None}, f"{prefix} is in_progress"),
                                 ({"conclusion": "cancelled"}, f"{prefix} concluded cancelled"),
                                 ({"conclusion": MARKER}, f"{prefix} concluded unrecognized")):
            with self.subTest(fields=fields):
                routes = ready_routes()
                routes[CORE_RUNS] = {"workflow_runs": [core_run(CORE_RUN, **fields)]}
                self.assertNotIn(jobs_route(CORE_RUN), self.assert_core(routes, expected).calls)
        group, lanes = CORE_JOBS[len(GROUPS) + 1], CORE_JOBS[len(GROUPS)]
        fixture = CORE_JOBS[len(GROUPS) + 1:]
        cases = (
            ("withheld group", jobs([name for name in CORE_JOBS if name != group]), f"{prefix}: {group}: absent"),
            ("failed group", {"jobs": [{"name": name, "conclusion": "failure" if name == group else "success"}
                                       for name in CORE_JOBS]}, f"{prefix}: {group}: failure"),
            ("no lanes job", jobs([name for name in CORE_JOBS if name != lanes]), f"{prefix}: {lanes}: absent"),
            ("no fixture case", jobs(CORE_JOBS[:len(GROUPS) + 1]),
             f"{prefix}: " + "; ".join(f"{name}: absent" for name in fixture)),
            ("release case only", jobs(RELEASE_JOBS), f"{prefix}: " + "; ".join(f"{name}: absent" for name in CORE_JOBS)),
        )
        for label, body, expected in cases:
            with self.subTest(label):
                routes = ready_routes()
                routes[jobs_route(CORE_RUN)] = body
                self.assert_core(routes, expected)

    def test_named_scheduled_or_dispatched_run_proves_the_release_case(self):
        route = f"repos/{REPO}/actions/runs/{CORE_RUN}"
        for event in ("workflow_dispatch", "schedule"):
            with self.subTest(event=event):
                routes = ready_routes()
                del routes[CORE_RUNS]
                routes[route] = core_run(CORE_RUN, event=event, branch="main", head="c" * 40)
                routes[jobs_route(CORE_RUN)] = jobs(["prepare", *RELEASE_JOBS])
                report, github = sweep(routes, core_run=str(CORE_RUN))
                self.assertTrue(report["ready"])
                self.assertEqual(detail(report, "Core evidence"),
                                 ("ready", f"run {CORE_RUN} attempt 1 ({event}), not the head: every lane-group and "
                                           f"lanes job of release proved Core {CORE}"))
                self.assertNotIn(CORE_RUNS, github.calls)
        routes = ready_routes()
        routes[route] = core_run(CORE_RUN, event="schedule", branch="main")
        routes[jobs_route(CORE_RUN)] = jobs(["prepare", "core-release-attempt-2099.1.0"]
                                            + [name.replace(CORE, "2099.1.0") for name in RELEASE_JOBS[1:]])
        self.assert_core(routes, f"run {CORE_RUN} attempt 1 (schedule): did not select Core {CORE}",
                         core_run=str(CORE_RUN))
        routes[jobs_route(CORE_RUN)] = jobs(RELEASE_JOBS[:-1])
        self.assert_core(routes, f"run {CORE_RUN} attempt 1 (schedule): {RELEASE_JOBS[-1]}: absent",
                         core_run=str(CORE_RUN))
        routes[route] = core_run(CORE_RUN, event="schedule", head="c" * 40, status="queued", conclusion=None)
        self.assert_core(routes, f"run {CORE_RUN} attempt 1 (schedule), not the head is queued",
                         core_run=str(CORE_RUN))

    def test_named_pull_request_run_is_read_directly(self):
        routes = ready_routes()
        del routes[CORE_RUNS]
        routes[f"repos/{REPO}/actions/runs/{CORE_RUN}"] = core_run(CORE_RUN)
        report, github = sweep(routes, core_run=str(CORE_RUN))
        self.assertTrue(report["ready"])
        self.assertEqual(github.calls[-2:], [f"repos/{REPO}/actions/runs/{CORE_RUN}", jobs_route(CORE_RUN)])

    def test_named_run_must_be_this_repository_evidence_run(self):
        route = f"repos/{REPO}/actions/runs/{CORE_RUN}"
        refused = f"run {CORE_RUN} is not a core-release-evidence.yml run of this repository"
        for body in (run(CORE_RUN), core_run(CORE_RUN, event="push"), core_run(CORE_RUN, head_owner="fork/example-repo"),
                     core_run(CORE_RUN, owner="fork/example-repo"), core_run(CORE_RUN + 1), core_run(str(CORE_RUN)),
                     [], {"workflow_runs": [core_run(CORE_RUN)]}):
            with self.subTest(body=body):
                routes = ready_routes()
                del routes[CORE_RUNS]
                routes[route] = body
                self.assertNotIn(jobs_route(CORE_RUN), self.assert_core(routes, refused, core_run=str(CORE_RUN)).calls)


class RefusalTests(unittest.TestCase):
    def test_unreadable_results_are_fixed_categories(self):
        cases = (
            (readiness.retry.GitHubReadError("api.github.com", 404, 1, False), "github_read_failed:404"),
            (readiness.retry.GitHubReadError("api.github.com", "timeout", 5, True), "github_read_unavailable:timeout"),
            (ValueError("api_route_not_allowed"), "github_read_refused:api_route_not_allowed"),
            (ValueError(MARKER), "github_read_refused:ValueError"),
            (OSError(MARKER), "github_read_refused:OSError"),
            (b"not json " + MARKER.encode(), "github_response_malformed"),
            (b'{"workflow_runs": [], "workflow_runs": []}', "github_response_malformed"),
            (b'{"workflow_runs": [NaN]}', "github_response_malformed"),
            (b"\xff\xfe", "github_response_malformed"),
            (encode({"workflow_runs": {}}), "github_response_malformed"),
            (encode({"workflow_runs": ["run"]}), "github_response_malformed"),
            (encode({"workflow_runs": [{}] * 101}), "github_response_malformed"),
            (encode([]), "github_response_malformed"),
            (b" " * (readiness.retry.API_BOUND + 1), "github_response_too_large"),
        )
        for body, category in cases:
            with self.subTest(category=category, body=repr(body)[:40]):
                routes = ready_routes()
                routes[CI_RUNS] = body
                routes[CORE_RUNS] = body
                report, _ = sweep(routes)
                self.assertFalse(report["ready"])
                for name in ("Contract matrix", "Core evidence"):
                    self.assertEqual(detail(report, name), ("not ready", f"unreadable: {category}"))
                self.assertNotIn(MARKER, readiness.markdown(report))

    def test_a_non_bytes_body_is_refused(self):
        routes = ready_routes()
        github = FakeGitHub(routes)
        report = readiness.sweep(REPO, HEAD, lambda route: github(route).decode())
        self.assertEqual(detail(report, "Contract matrix"), ("not ready", "unreadable: github_response_malformed"))

    def test_response_text_is_never_reported(self):
        routes = ready_routes()
        routes[CI_RUNS] = {"workflow_runs": [{**run(CI_RUN), "run_attempt": MARKER}]}
        routes[jobs_route(CI_RUN)] = {"jobs": [{"name": MARKER, "conclusion": "failure"},
                                               {"name": "validate", "conclusion": MARKER}]
                                      + [{"name": lane, "conclusion": "success"} for lane in LANES]}
        routes[jobs_route(CORE_RUN)] = {"jobs": [{"name": name, "conclusion": "success"} for name in CORE_JOBS]
                                        + [{"name": MARKER, "conclusion": MARKER}]}
        report, _ = sweep(routes)
        self.assertEqual(detail(report, "Contract matrix"),
                         ("not ready", f"run {CI_RUN} attempt unknown (pull_request): validate: unrecognized"))
        self.assertNotIn(MARKER, readiness.markdown(report))
        self.assertNotIn(MARKER, json.dumps(report))
        routes[jobs_route(CI_RUN)] = {"jobs": [{"name": 7, "conclusion": "success"}]}
        report, _ = sweep(routes)
        self.assertEqual(detail(report, "Contract matrix"), ("not ready", "unreadable: github_response_malformed"))

    def test_usage_refusals_read_nothing(self):
        cases = (
            (["--repo", "bad repo", "--head", HEAD], "repository_invalid"),
            (["--repo", "owner/name/extra", "--head", HEAD], "repository_invalid"),
            (["--repo", REPO, "--head", "A" * 40], "head_invalid"),
            (["--repo", REPO, "--head", HEAD[:39]], "head_invalid"),
            (["--repo", REPO, "--head", HEAD + "\n"], "head_invalid"),
            (["--repo", REPO, "--head", HEAD, "--core-run", "0"], "core_run_invalid"),
            (["--repo", REPO, "--head", HEAD, "--core-run", "12a"], "core_run_invalid"),
        )
        for argv, category in cases:
            with self.subTest(argv=argv):
                github, out, err = FakeGitHub({}), io.StringIO(), io.StringIO()
                self.assertEqual(readiness.main(argv, read=github, stdout=out, stderr=err), 2)
                self.assertEqual(err.getvalue(), f"Release readiness refused: {category}\n")
                self.assertEqual((github.calls, out.getvalue()), ([], ""))

    def test_a_refused_window_reads_nothing(self):
        for error, category in ((support_window.Refusal("core_current_binding_mismatch"),
                                 "core_current_binding_mismatch"),
                                (OSError(MARKER), "OSError"), (support_window.yaml.YAMLError(MARKER), "YAMLError")):
            with self.subTest(category=category):
                github = FakeGitHub({})

                def refuse():
                    raise error

                report = readiness.sweep(REPO, HEAD, github, load_window=refuse)
                reason = f"support window or Core evidence groups refused: {category}"
                self.assertEqual([(item["status"], item["detail"]) for item in report["parts"]],
                                 [("not ready", reason), ("not ready", "not checked: " + reason),
                                  ("not ready", "not checked: " + reason)])
                self.assertIsNone(report["pair"])
                self.assertEqual(github.calls, [])
                self.assertNotIn(MARKER, readiness.markdown(report))

    def test_not_ready_exits_one(self):
        routes = ready_routes()
        routes[CI_RUNS] = {"workflow_runs": []}
        out = io.StringIO()
        self.assertEqual(readiness.main(["--repo", REPO, "--head", HEAD], read=FakeGitHub(routes), stdout=out), 1)
        self.assertIn("Checked parts ready: 2 of 3. Not ready.", out.getvalue())


if __name__ == "__main__":
    unittest.main()
