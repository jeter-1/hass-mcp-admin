"""Local parallel shard runner (check.ps1 -Parallel); synthetic disposable repositories only."""

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/local_unit_test_shards.py"
CI_SCRIPT = ROOT / "scripts/unit_test_shards.py"
spec = importlib.util.spec_from_file_location("local_unit_test_shards", SCRIPT)
local = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local)
shards = local.unit_test_shards

MODULES = {
    "test_alpha.py": """
import os
import unittest

class Alpha(unittest.TestCase):
    def test_one(self):
        self.assertEqual(1, 1)

    def test_two(self):
        self.assertFalse(os.environ.get("SYNTHETIC_FAIL"), "synthetic failure")

    def test_crash(self):
        if os.environ.get("SYNTHETIC_CRASH"):
            os._exit(7)

    def test_kill(self):
        if os.environ.get("SYNTHETIC_KILL"):
            import signal
            os.kill(os.getpid(), signal.SIGKILL)

    def test_hang(self):
        if os.environ.get("SYNTHETIC_HANG"):
            import time
            time.sleep(120)
""",
    "test_beta.py": """
import os
import unittest

class Beta(unittest.TestCase):
    def test_runs(self):
        self.assertTrue(True)

    @unittest.skip("synthetic skip")
    def test_skipped(self):
        raise AssertionError("not reached")

    def test_environment_is_the_plain_local_one(self):
        if os.environ.get("SYNTHETIC_ENV_PROBE"):
            self.assertNotIn("GITHUB_RUN_ID", os.environ)
            self.assertNotIn("GITHUB_RUN_ATTEMPT", os.environ)
""",
    "test_delta.py": """
import os
from pathlib import Path
import unittest

class Delta(unittest.TestCase):
    def test_runs(self):
        with self.subTest(case=1):
            self.assertTrue(True)

    def test_grows_suite(self):
        if os.environ.get("SYNTHETIC_GROW"):
            Path(__file__).with_name("test_late.py").write_text(
                "import unittest\\n\\nclass Late(unittest.TestCase):\\n    def test_late(self):\\n        pass\\n")
""",
    "test_epsilon.py": """
import os
import sys
import unittest

class Epsilon(unittest.TestCase):
    def run(self, result=None):
        # A worker can exit 0 with an OK summary while its manifest is wrong:
        # the test never starts, or starts twice. Only the final proof sees it.
        if os.environ.get("SYNTHETIC_UNSTARTED"):
            print("synthetic-unstarted-diagnostic " + self.id(), file=sys.stderr)
            return result
        if os.environ.get("SYNTHETIC_TWICE"):
            print("synthetic-twice-diagnostic " + self.id(), file=sys.stderr)
            super().run(result)
        return super().run(result)

    def test_runs(self):
        self.assertTrue(True)
""",
    "test_gamma.py": """
import unittest
from support.real_time import real_time_measurement

class Gamma(unittest.TestCase):
    @real_time_measurement("synthetic latency bound")
    def test_measured(self):
        self.assertTrue(True)

    def test_unmarked_neighbor(self):
        self.assertTrue(True)
""",
}
PLAN = {
    "schema_version": 1, "general_shards": 3, "default_module_seconds": 1,
    "module_seconds": {"test_alpha": 5, "test_beta": 3, "test_delta": 2, "test_epsilon": 1},
}
SYNTHETIC_KEYS = ("SYNTHETIC_FAIL", "SYNTHETIC_CRASH", "SYNTHETIC_KILL", "SYNTHETIC_HANG",
                  "SYNTHETIC_GROW", "SYNTHETIC_ENV_PROBE", "SYNTHETIC_UNSTARTED", "SYNTHETIC_TWICE")


def plain_env(**extra):
    """The local environment without any CI run identity or synthetic switch."""

    env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k not in SYNTHETIC_KEYS}
    env.update(extra)
    return env


def make_repository(root):
    tests = root / "tests"
    (tests / "support").mkdir(parents=True)
    (tests / "support" / "__init__.py").write_text("")
    shutil.copyfile(ROOT / "tests/support/real_time.py", tests / "support" / "real_time.py")
    for name, body in MODULES.items():
        (tests / name).write_text(body.lstrip())
    (root / "ci").mkdir()
    (root / shards.PLAN_FILE).write_text(json.dumps(PLAN))
    for args in (("init", "-q"), ("config", "user.name", "Synthetic test"),
                 ("config", "user.email", "synthetic@example.invalid"), ("add", "."),
                 ("commit", "-qm", "Synthetic source")):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


class LocalShardFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        cls.root = cls.base / "source"
        make_repository(cls.root)
        cls.result, cls.summary = cls.run_local(cls.root, 2, "passing", env=plain_env(SYNTHETIC_ENV_PROBE="1"))
        assert cls.result.returncode == 0, cls.result.stdout + cls.result.stderr
        cls.single = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                                    cwd=cls.root, env=plain_env(), capture_output=True, text=True, timeout=120)
        assert cls.single.returncode == 0, cls.single.stderr

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def run_local(cls, root, workers, name, env=None, timeout=None):
        output = cls.base / "runs" / name
        command = [sys.executable, str(SCRIPT), "run", "--repo-root", str(root),
                   "--workers", str(workers), "--output-dir", str(output)]
        if timeout is not None:
            command += ["--shard-timeout", str(timeout)]
        result = subprocess.run(command, cwd=str(root), env=env or plain_env(), capture_output=True,
                                text=True, timeout=300)
        summary_path = output / "summary.json"
        summary = json.loads(summary_path.read_text()) if summary_path.is_file() else None
        return result, summary

    def clone(self, name):
        target = self.base / name
        shutil.copytree(self.root, target)
        return target

    def manifests(self, summary_dir=None):
        directory = summary_dir or self.base / "runs" / "passing" / "manifests"
        return {int(p.stem.split("-")[1]): json.loads(p.read_text()) for p in directory.glob("shard-*.json")}


class LocalParallelRunTests(LocalShardFixture):

    def test_parallel_run_proves_completeness(self):
        summary = self.summary
        self.assertEqual(summary["result"], "passed")
        self.assertEqual(summary["problems"], [])
        self.assertEqual(summary["mode"], "sharded")
        self.assertEqual(summary["workers"], 2)
        self.assertEqual((summary["general_shards"], summary["shard_count"], summary["serial_shards"]), (3, 4, [3]))
        self.assertEqual(summary["source_sha"], subprocess.check_output(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True).strip())
        manifests = self.manifests()
        self.assertEqual(sorted(manifests), [0, 1, 2, 3])
        executed = [row[0] for m in manifests.values() for row in m["executed"]]
        # Independent listing: the ids a plain single-process ``unittest discover -v`` reports.
        discovered = sorted(line.split(" ... ")[0].split(" (", 1)[1].rstrip(")")
                            for line in self.single.stderr.splitlines() if " ... " in line)
        self.assertEqual(len(executed), len(set(executed)))
        self.assertEqual(sorted(executed), discovered)
        self.assertEqual(summary["discovered_count"], len(discovered))
        self.assertEqual(summary["discovered_sha256"], shards.digest(discovered))
        self.assertIn("Completeness proven: 13 discovered tests executed exactly once", self.result.stdout)

    def test_totals_equal_a_single_process_run(self):
        single = self.single
        ran = int(re.search(r"Ran (\d+) tests?", single.stderr).group(1))
        skipped = int(re.search(r"OK \(skipped=(\d+)\)", single.stderr).group(1))
        self.assertEqual((self.summary["tests_run"], self.summary["skipped"]), (ran, skipped))
        self.assertEqual((ran, skipped), (13, 1))
        self.assertIn(f"Sharded totals: ran={ran} skipped={skipped}", self.result.stdout)

    def test_assignment_and_proof_are_ci_identical(self):
        local_manifests = self.manifests()
        ci_env = plain_env(GITHUB_RUN_ID="4242", GITHUB_RUN_ATTEMPT="1")
        count = subprocess.run([sys.executable, str(CI_SCRIPT), "count", "--repo-root", str(self.root)],
                               capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(count.stdout), sorted(local_manifests))
        ci_dir = self.base / "ci-manifests"
        for shard in sorted(local_manifests):
            output = ci_dir / f"shard-{shard}" / "manifest.json"
            subprocess.run([sys.executable, str(CI_SCRIPT), "run", "--shard", str(shard), "--output", str(output),
                            "--repo-root", str(self.root)], cwd=self.root, env=ci_env, capture_output=True,
                           check=True, timeout=120)
            ci = json.loads(output.read_text())
            for key in ("assigned", "discovered_count", "discovered_sha256", "plan_sha256", "shard_count",
                        "source_sha", "schema_version"):
                self.assertEqual(local_manifests[shard][key], ci[key], (shard, key))
            self.assertEqual(sorted(r[0] for r in local_manifests[shard]["executed"]),
                             sorted(r[0] for r in ci["executed"]))
        # CI's own aggregate command accepts the local manifests under the local run identity.
        aggregate = subprocess.run(
            [sys.executable, str(CI_SCRIPT), "aggregate", "--manifests", str(self.base / "runs/passing/manifests"),
             "--repo-root", str(self.root)], cwd=self.root, capture_output=True, text=True, timeout=120,
            env=plain_env(GITHUB_RUN_ID=self.summary["run_id"], GITHUB_RUN_ATTEMPT="1"))
        self.assertEqual(aggregate.returncode, 0, aggregate.stderr)
        self.assertEqual(json.loads(aggregate.stdout)["plan_sha256"], self.summary["plan_sha256"])

    def test_real_time_shard_runs_alone_after_the_parallel_shards(self):
        rows = {row["shard"]: row for row in self.summary["shards"]}
        serial = rows[3]
        self.assertEqual(serial["phase"], "serial_real_time")
        parallel_end = max(r["finished_offset_seconds"] for s, r in rows.items() if s != 3)
        self.assertGreaterEqual(serial["started_offset_seconds"], parallel_end)
        assigned = self.manifests()[3]["assigned"]
        self.assertIn("test_gamma.Gamma.test_measured", assigned)
        self.assertTrue(all(t.startswith("test_gamma.") for t in assigned))

    def test_no_more_than_the_requested_workers_overlap(self):
        intervals = [(r["started_offset_seconds"], r["finished_offset_seconds"]) for r in self.summary["shards"]]
        for start, _ in intervals:
            alive = sum(1 for s, e in intervals if s <= start < e)
            self.assertLessEqual(alive, 2)

    def test_run_identity_never_reaches_the_test_environment(self):
        # The fixture run set SYNTHETIC_ENV_PROBE; Beta asserts no GITHUB_RUN_* variable is visible.
        beta = next(m for m in self.manifests().values()
                    if "test_beta.Beta.test_environment_is_the_plain_local_one" in m["assigned"])
        self.assertIn(["test_beta.Beta.test_environment_is_the_plain_local_one", "success"], beta["executed"])
        self.assertEqual(self.manifests()[0]["run_id"], self.summary["run_id"])
        self.assertRegex(self.summary["run_id"], r"^[1-9][0-9]{18}$")

    def test_worker_count_is_bounded(self):
        for workers in (0, 5, -1):
            with self.subTest(workers=workers):
                result, summary = self.run_local(self.root, workers, f"bad-{workers}")
                self.assertEqual(result.returncode, 1)
                self.assertIn("refused: invalid_worker_count", result.stderr)
                self.assertIsNone(summary)
        self.assertEqual(local.MAX_WORKERS, 4)

    def test_existing_output_directory_is_refused(self):
        result, _ = self.run_local(self.root, 1, "passing")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refused: output_directory_exists", result.stderr)

    def test_single_worker_run_matches_the_parallel_proof(self):
        result, summary = self.run_local(self.root, 1, "one-worker")
        self.assertEqual(result.returncode, 0, result.stdout)
        for key in ("discovered_sha256", "plan_sha256", "tests_run", "skipped", "shard_count"):
            self.assertEqual(summary[key], self.summary[key], key)


class LocalFailureTests(LocalShardFixture):

    def assertFailedRun(self, result, summary, *problems):
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(summary["result"], "failed")
        self.assertIsNone(summary["tests_run"])
        self.assertIsNone(summary["skipped"])
        self.assertIsNone(summary["discovered_count"])
        for problem in problems:
            self.assertTrue(any(problem in p for p in summary["problems"]), (problem, summary["problems"]))
        self.assertIn("Sharded unittest run FAILED", result.stdout)
        self.assertNotIn("Completeness proven", result.stdout)

    def alpha_shard(self, summary):
        return next(s for s, m in self.manifests().items() if "test_alpha.Alpha.test_one" in m["assigned"])

    def test_failing_test_fails_the_run_and_prints_that_shards_output(self):
        result, summary = self.run_local(self.root, 2, "failing", env=plain_env(SYNTHETIC_FAIL="1"))
        shard = self.alpha_shard(summary)
        self.assertFailedRun(result, summary, f"shard_{shard}:exit_code_1", f"shard_{shard}:runner_reported_failure")
        self.assertIn(f"----- shard {shard} output", result.stdout)
        self.assertIn("synthetic failure", result.stdout)
        # Other shards still ran and are reported, but nothing is summed as a pass.
        self.assertEqual(len(summary["shards"]), 4)

    def test_crashed_worker_is_a_failure(self):
        result, summary = self.run_local(self.root, 2, "crash", env=plain_env(SYNTHETIC_CRASH="1"))
        shard = self.alpha_shard(summary)
        self.assertFailedRun(result, summary, f"shard_{shard}:exit_code_7", f"shard_{shard}:manifest_missing",
                             f"shard_{shard}:runner_summary_missing")
        self.assertIn(f"----- shard {shard} output", result.stdout)

    @unittest.skipIf(os.name == "nt", "POSIX signal semantics")
    def test_killed_worker_is_a_failure(self):
        result, summary = self.run_local(self.root, 2, "killed", env=plain_env(SYNTHETIC_KILL="1"))
        shard = self.alpha_shard(summary)
        self.assertFailedRun(result, summary, f"shard_{shard}:killed_by_signal_9", f"shard_{shard}:manifest_missing")

    def test_hung_worker_times_out_as_a_failure(self):
        result, summary = self.run_local(self.root, 2, "hang", env=plain_env(SYNTHETIC_HANG="1"), timeout=3)
        shard = self.alpha_shard(summary)
        self.assertFailedRun(result, summary, f"shard_{shard}:timed_out", f"shard_{shard}:manifest_missing")
        row = next(r for r in summary["shards"] if r["shard"] == shard)
        self.assertLess(row["duration_seconds"], 60)

    def test_suite_changing_during_the_run_fails_completeness(self):
        root = self.clone("growing")
        result, summary = self.run_local(root, 1, "growing", env=plain_env(SYNTHETIC_GROW="1"))
        self.assertFailedRun(result, summary, "completeness_refused:")
        # No manifest names an offending test, so every shard is failed and its output shown.
        self.assertEqual(summary["completeness"]["findings"], [])
        self.assertEqual({r["status"] for r in summary["shards"]}, {"failed"})
        self.assertIn("no shard manifest names the offending tests; every shard's output is shown above",
                      result.stdout)
        for row in summary["shards"]:
            self.assertIn(f"----- shard {row['shard']} output (completeness_refused) -----", result.stdout)

    def assertCompletenessOnlyFailure(self, name, switch, refusal, kind, marker):
        result, summary = self.run_local(self.root, 2, name, env=plain_env(**{switch: "1"}))
        test_id = "test_epsilon.Epsilon.test_runs"
        shard = next(s for s, m in self.manifests().items() if test_id in m["assigned"])
        self.assertFailedRun(result, summary, f"completeness_refused:{refusal}", f"shard_{shard}:completeness_{kind}")
        # Every worker exited 0 with unittest's OK; only the final proof refused.
        self.assertEqual({r["exit_code"] for r in summary["shards"]}, {0})
        self.assertEqual(summary["completeness"]["findings"], [{"kind": kind, "shards": [shard], "tests": [test_id]}])
        statuses = {r["shard"]: r["status"] for r in summary["shards"]}
        self.assertEqual(statuses, {s: "failed" if s == shard else "unproven" for s in statuses})
        self.assertNotRegex(result.stdout, r"(?m)^Shard \d+ \([a-z_]+\): passed")
        self.assertIn(f"Completeness check FAILED (completeness_refused:{refusal}): shard(s) {shard} {kind}: {test_id}",
                      result.stdout)
        # The offending shard's console output is shown; the others' are not.
        self.assertIn(f"----- shard {shard} output (completeness_{kind}) -----", result.stdout)
        self.assertIn(f"{marker} {test_id}", result.stdout)
        for other in set(statuses) - {shard}:
            self.assertNotIn(f"----- shard {other} output", result.stdout)

    def test_unstarted_test_fails_completeness_with_its_id_and_shard_output(self):
        self.assertCompletenessOnlyFailure("unstarted", "SYNTHETIC_UNSTARTED", "shard_execution_incomplete_or_extra",
                                           "missing", "synthetic-unstarted-diagnostic")

    def test_test_run_twice_fails_completeness_with_its_id_and_shard_output(self):
        self.assertCompletenessOnlyFailure("twice", "SYNTHETIC_TWICE", "test_executed_more_than_once",
                                           "duplicated", "synthetic-twice-diagnostic")

    def test_completeness_findings_name_ids_and_shards(self):
        directory = self.base / "tampered" / "findings"
        shutil.copytree(self.base / "runs/passing/manifests", directory)
        paths = {int(p.stem.split("-")[1]): p for p in directory.glob("shard-*.json")}
        first, second = json.loads(paths[0].read_text()), json.loads(paths[1].read_text())
        lost, moved = first["executed"][0][0], second["executed"][0][0]
        first["executed"] = first["executed"][1:] + [second["executed"][0]]
        paths[0].write_text(json.dumps(first))
        (directory / "unreadable.json").write_text("{not json")
        findings = local.completeness_findings(directory)
        self.assertIn({"kind": "missing", "shards": [0], "tests": [lost]}, findings)
        self.assertIn({"kind": "unassigned", "shards": [0], "tests": [moved]}, findings)
        self.assertIn({"kind": "executed_in_multiple_shards", "shards": [0, 1], "tests": [moved]}, findings)
        self.assertEqual(len(findings), 3)
        self.assertEqual(local.listed([f"t{i}" for i in range(local.MAX_LISTED_IDS + 3)]).split(", ")[-1],
                         "... and 3 more (see summary.json)")

    def verify(self, directory, run_id=None):
        """Apply the helper's completeness proof in a subprocess (discovery imports test modules)."""

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "verify", "--repo-root", str(self.root), "--manifests", str(directory),
             "--run-id", run_id or self.summary["run_id"]], cwd=self.root, env=plain_env(),
            capture_output=True, text=True, timeout=120)
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0 if payload["result"] == "passed" else 1, payload)
        return payload

    def finalize_with(self, name, mutate):
        directory = self.base / "tampered" / name
        shutil.copytree(self.base / "runs/passing/manifests", directory)
        manifests = {int(p.stem.split("-")[1]): p for p in directory.glob("shard-*.json")}
        mutate(manifests)
        payload = self.verify(directory)
        return {k: v for k, v in payload.items() if k not in ("result", "problems")}, payload["problems"]

    def test_untampered_manifests_verify(self):
        payload = self.verify(self.base / "runs/passing/manifests")
        self.assertEqual(payload["result"], "passed")
        self.assertEqual(payload["discovered_sha256"], self.summary["discovered_sha256"])

    def test_missing_test_is_refused(self):
        def drop(paths):
            data = json.loads(paths[0].read_text())
            data["executed"] = data["executed"][1:]
            paths[0].write_text(json.dumps(data))
        proof, problems = self.finalize_with("missing", drop)
        self.assertEqual(proof, {})
        self.assertEqual(problems, ["completeness_refused:shard_execution_incomplete_or_extra"])

    def test_duplicated_test_is_refused(self):
        def duplicate(paths):
            first, second = json.loads(paths[0].read_text()), json.loads(paths[1].read_text())
            second["executed"].append(first["executed"][0])
            paths[1].write_text(json.dumps(second))
        proof, problems = self.finalize_with("duplicate", duplicate)
        self.assertEqual(proof, {})
        self.assertEqual(problems, ["completeness_refused:shard_execution_incomplete_or_extra"])

    def test_missing_shard_manifest_is_refused(self):
        proof, problems = self.finalize_with("absent", lambda paths: paths[2].unlink())
        self.assertEqual(problems, ["completeness_refused:shard_manifest_count_mismatch"])

    def test_foreign_run_identity_is_refused(self):
        payload = self.verify(self.base / "runs/passing/manifests", run_id="1234567890123456789")
        self.assertEqual(payload["problems"], ["completeness_refused:shard_run_mismatch"])

    def test_untallied_shard_is_not_a_pass(self):
        # Rejected before any discovery, so this stays in-process.
        proof, problems = local.finalize(self.root, self.base / "absent", self.summary["run_id"],
                                         [{"tests_run": 4}, {"tests_run": None}])
        self.assertEqual((proof, problems), ({}, ["runner_totals_unavailable"]))


class RunnerTotalsTests(unittest.TestCase):

    def test_summary_forms(self):
        cases = {
            "Ran 12 tests in 1.250s\n\nOK\n": (12, 0, True),
            "Ran 12 tests in 1.250s\n\nOK (skipped=2)\n": (12, 2, True),
            "Ran 1 test in 0.001s\n\nOK (skipped=1, expected failures=1)\n": (1, 1, True),
            "Ran 3 tests in 0.1s\n\nFAILED (failures=1, skipped=1)\n": (3, 1, False),
            "Ran 3 tests in 0.1s\n\nFAILED (unexpected successes=1)\n": (3, 0, False),
            "Ran 0 tests in 0.000s\n\nNO TESTS RAN\n": (0, 0, True),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(local.runner_totals("test_x ... ok\n" + text + "Shard 0: done\n"), expected)

    def test_missing_or_malformed_summary_is_unavailable(self):
        for text in ("", "Traceback (most recent call last):\n", "Ran 3 tests in 0.1s\n",
                     "Ran 2 tests in 0.1s\n\nNO TESTS RAN\n", "Ran 3 tests in 0.1s\n\nOK (skipped=x)\n"):
            with self.subTest(text=text):
                self.assertIsNone(local.runner_totals(text))

    def test_the_last_runner_summary_wins(self):
        text = ("Ran 99 tests in 0.1s\n\nOK\n"  # e.g. a nested runner's output printed by a test
                "test_x ... ok\n\nRan 4 tests in 1.0s\n\nFAILED (errors=1)\n")
        self.assertEqual(local.runner_totals(text), (4, 0, False))


class SharedLogicTests(unittest.TestCase):

    def test_assignment_and_completeness_logic_is_not_copied(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for name in ("plan", "verify", "discover", "identity", "run_shard", "load_plan_file", "fixture_skipped"):
            self.assertNotRegex(source, rf"(?m)^def {name}\(", name)
        self.assertNotIn("class RecordingResult", source)
        for call in ("unit_test_shards.run_shard(", "unit_test_shards.verify(", "unit_test_shards.load_plan_file(",
                     "unit_test_shards.load_manifests("):
            self.assertIn(call, source)
        self.assertIs(local.unit_test_shards.verify, shards.verify)

    def test_check_script_worker_bound_matches_the_helper(self):
        text = (ROOT / "scripts/check.ps1").read_text(encoding="utf-8")
        self.assertIn(f"[ValidateRange(1, {local.MAX_WORKERS})]\n    [int]$Parallel", text)

    def test_run_identity_is_accepted_by_ci_validation(self):
        for _ in range(50):
            run_id = local.new_run_id()
            self.assertEqual(shards.run_identity(local.run_identity_env(run_id)), (run_id, "1"))


if __name__ == "__main__":
    unittest.main()
