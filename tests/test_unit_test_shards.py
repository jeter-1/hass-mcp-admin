"""Sharded unit-suite completeness proof; synthetic disposable repositories only."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/unit_test_shards.py"
spec = importlib.util.spec_from_file_location("unit_test_shards", SCRIPT)
shards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shards)

MODULES = {
    "test_alpha.py": """
import os
import unittest

class Alpha(unittest.TestCase):
    def test_one(self):
        self.assertEqual(1, 1)

    def test_two(self):
        self.assertFalse(os.environ.get("SYNTHETIC_FAIL"), "synthetic failure")
""",
    "test_beta.py": """
import unittest

class Beta(unittest.TestCase):
    def test_runs(self):
        self.assertTrue(True)

    @unittest.skip("synthetic skip")
    def test_skipped(self):
        raise AssertionError("not reached")
""",
    "test_delta.py": """
import unittest

class Delta(unittest.TestCase):
    def test_runs(self):
        with self.subTest(case=1):
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


SPLIT_MODULES = {
    "test_heavy.py": """
import unittest
from support.real_time import real_time_measurement

class Heavy(unittest.TestCase):
    def test_a_slow(self):
        self.assertTrue(True)

    def test_b_medium(self):
        self.assertTrue(True)

    def test_c_medium(self):
        self.assertTrue(True)

    @real_time_measurement("synthetic latency bound")
    def test_d_measured(self):
        self.assertTrue(True)
""",
    "test_light.py": """
import unittest

class Light(unittest.TestCase):
    def test_runs(self):
        self.assertTrue(True)
""",
}
SPLIT_PLAN = {
    "schema_version": 1, "general_shards": 2, "default_module_seconds": 1,
    "module_seconds": {"test_heavy": 12, "test_light": 1},
    "split_modules": ["test_heavy"],
    "test_seconds": {"test_heavy.Heavy.test_a_slow": 8, "test_heavy.Heavy.test_b_medium": 3},
}


class ShardFixture(unittest.TestCase):
    MODULES = MODULES
    PLAN = {
        "schema_version": 1, "general_shards": 2, "default_module_seconds": 1,
        "module_seconds": {"test_alpha": 5, "test_beta": 3, "test_delta": 1},
    }

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name) / "source"
        tests = cls.root / "tests"
        (tests / "support").mkdir(parents=True)
        (tests / "support" / "__init__.py").write_text("")
        shutil.copyfile(ROOT / "tests/support/real_time.py", tests / "support" / "real_time.py")
        for name, body in cls.MODULES.items():
            (tests / name).write_text(body.lstrip())
        (cls.root / "ci").mkdir()
        (cls.root / shards.PLAN_FILE).write_text(json.dumps(cls.PLAN))
        cls.git("init", "-q")
        cls.git("config", "user.name", "Synthetic test")
        cls.git("config", "user.email", "synthetic@example.invalid")
        cls.git("add", ".")
        cls.git("commit", "-qm", "Synthetic source")
        cls.head = cls.git("rev-parse", "HEAD")
        cls.env = {**os.environ, "GITHUB_RUN_ID": "1001", "GITHUB_RUN_ATTEMPT": "1"}
        cls.env.pop("SYNTHETIC_FAIL", None)
        cls.manifests = {}
        for shard in range(3):
            result = cls.cli("run", "--shard", str(shard), "--output", str(cls.out(f"m{shard}.json")))
            assert result.returncode == 0, result.stderr
            cls.manifests[shard] = json.loads(cls.out(f"m{shard}.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def git(cls, *args):
        return subprocess.check_output(["git", "-C", str(cls.root), *args],
                                       stderr=subprocess.DEVNULL, text=True).strip()

    @classmethod
    def out(cls, name):
        return Path(cls.temp.name) / "out" / name

    @classmethod
    def cli(cls, *args, env=None, root=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args, "--repo-root", str(root or cls.root)],
                              env=env or cls.env, cwd=str(root or cls.root), capture_output=True,
                              text=True, timeout=120)

    def aggregate(self, manifests, env=None, root=None):
        directory = Path(tempfile.mkdtemp(dir=self.temp.name))
        for index, manifest in enumerate(manifests):
            (directory / f"shard-{index}").mkdir()
            (directory / f"shard-{index}" / "manifest.json").write_text(json.dumps(manifest))
        return self.cli("aggregate", "--manifests", str(directory), env=env, root=root)

    def assertRefused(self, result, category):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("refused: " + category, result.stderr)

    def copies(self):
        return [json.loads(json.dumps(self.manifests[i])) for i in range(3)]

    def with_plan(self, name, **changes):
        clone = Path(self.temp.name) / name
        shutil.copytree(self.root, clone)
        (clone / shards.PLAN_FILE).write_text(json.dumps({**self.PLAN, **changes}))
        return clone


class UnitTestShardTests(ShardFixture):

    def test_complete_successful_shards_prove_full_discovery(self):
        result = self.aggregate(self.copies())
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["result"], "success")
        self.assertEqual(summary["source_sha"], self.head)
        self.assertEqual(summary["discovered_count"], 7)
        self.assertEqual(summary["shard_count"], 3)
        executed = sorted(row[0] for m in self.manifests.values() for row in m["executed"])
        self.assertEqual(len(executed), 7)
        self.assertEqual(len(set(executed)), 7)

    def test_union_equals_unsharded_discovery(self):
        unsharded = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=self.root,
            env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(unsharded.returncode, 0, unsharded.stderr)
        listed = sorted(line.split(" ... ")[0].split(" (", 1)[1].rstrip(")")
                        for line in unsharded.stderr.splitlines() if " ... " in line)
        executed = sorted(test_id for m in self.manifests.values() for test_id, _ in m["executed"])
        self.assertEqual(len(listed), 7)
        self.assertEqual(executed, listed)

    def test_real_time_module_is_isolated_in_last_shard(self):
        self.assertEqual(sorted(t for t, _ in self.manifests[2]["executed"]),
                         ["test_gamma.Gamma.test_measured", "test_gamma.Gamma.test_unmarked_neighbor"])
        for shard in (0, 1):
            self.assertFalse(any(t.startswith("test_gamma.") for t, _ in self.manifests[shard]["executed"]))

    def test_plan_is_deterministic_and_balanced_by_weight(self):
        first = json.loads(self.cli("describe").stdout)
        second = json.loads(self.cli("describe").stdout)
        self.assertEqual(first, second)
        self.assertEqual(first["units"], {"test_alpha": 0, "test_beta": 1, "test_delta": 1, "test_gamma": 2})
        self.assertEqual(json.loads(self.cli("count").stdout), [0, 1, 2])

    def test_missing_shard_is_refused(self):
        self.assertRefused(self.aggregate(self.copies()[:2]), "shard_manifest_count_mismatch")

    def test_duplicate_shard_is_refused(self):
        manifests = self.copies()
        manifests[2] = manifests[1]
        self.assertRefused(self.aggregate(manifests), "shard_index_invalid_or_duplicate")

    def test_wrong_run_attempt_or_commit_is_refused(self):
        self.assertRefused(self.aggregate(self.copies(), env={**self.env, "GITHUB_RUN_ATTEMPT": "2"}),
                           "shard_run_mismatch")
        self.assertRefused(self.aggregate(self.copies(), env={**self.env, "GITHUB_RUN_ID": "1002"}),
                           "shard_run_mismatch")
        manifests = self.copies()
        manifests[0]["source_sha"] = "0" * 40
        self.assertRefused(self.aggregate(manifests), "shard_source_mismatch")

    def test_dropped_extra_or_repeated_execution_is_refused(self):
        manifests = self.copies()
        manifests[0]["executed"].pop()
        self.assertRefused(self.aggregate(manifests), "shard_execution_incomplete_or_extra")
        manifests = self.copies()
        manifests[0]["executed"].append(manifests[1]["executed"][0])
        self.assertRefused(self.aggregate(manifests), "shard_execution_incomplete_or_extra")
        manifests = self.copies()
        manifests[0]["executed"].append(manifests[0]["executed"][0])
        self.assertRefused(self.aggregate(manifests), "test_executed_more_than_once")

    def test_reassigned_plan_is_refused(self):
        manifests = self.copies()
        moved = manifests[1]["assigned"].pop()
        manifests[0]["assigned"] = sorted(manifests[0]["assigned"] + [moved])
        self.assertRefused(self.aggregate(manifests), "shard_assignment_mismatch")
        manifests = self.copies()
        manifests[0]["plan_sha256"] = "0" * 64
        self.assertRefused(self.aggregate(manifests), "shard_discovery_or_plan_mismatch")

    def test_failed_outcome_or_unsuccessful_shard_is_refused(self):
        for outcome in ("failure", "error", "unexpected_success"):
            manifests = self.copies()
            manifests[1]["executed"][0][1] = outcome
            self.assertRefused(self.aggregate(manifests), "test_did_not_succeed")
        manifests = self.copies()
        manifests[1]["was_successful"] = False
        self.assertRefused(self.aggregate(manifests), "shard_did_not_succeed")
        manifests = self.copies()
        manifests[1]["unattributed_errors"] = 1
        self.assertRefused(self.aggregate(manifests), "shard_fixture_error")

    def test_real_failing_test_fails_shard_and_aggregate(self):
        env = {**self.env, "SYNTHETIC_FAIL": "1"}
        shard = self.manifests[0]["assigned"]
        self.assertIn("test_alpha.Alpha.test_two", shard)
        output = self.out("failing.json")
        result = self.cli("run", "--shard", "0", "--output", str(output), env=env)
        self.assertEqual(result.returncode, 1)
        failing = json.loads(output.read_text())
        self.assertIn(["test_alpha.Alpha.test_two", "failure"], failing["executed"])
        self.assertFalse(failing["was_successful"])
        manifests = self.copies()
        manifests[0] = failing
        self.assertRefused(self.aggregate(manifests), "test_did_not_succeed")

    def test_suite_changed_after_shards_ran_is_refused(self):
        clone = Path(self.temp.name) / "changed"
        shutil.copytree(self.root, clone)
        (clone / "tests" / "test_epsilon.py").write_text(
            "import unittest\n\nclass Epsilon(unittest.TestCase):\n    def test_new(self):\n        pass\n")
        self.assertRefused(self.aggregate(self.copies(), root=clone), "shard_discovery_or_plan_mismatch")

    def test_import_failure_is_a_failing_test_not_a_silent_omission(self):
        clone = Path(self.temp.name) / "broken"
        shutil.copytree(self.root, clone)
        (clone / "tests" / "test_broken.py").write_text("import module_that_does_not_exist\n")
        subprocess.check_call(["git", "-C", str(clone), "add", "."], stdout=subprocess.DEVNULL)
        subprocess.check_call(["git", "-C", str(clone), "commit", "-qm", "broken"], stdout=subprocess.DEVNULL)
        plan = json.loads(self.cli("describe", root=clone).stdout)
        self.assertIn("test_broken", plan["units"])
        shard = plan["units"]["test_broken"]
        output = self.out("broken.json")
        result = self.cli("run", "--shard", str(shard), "--output", str(output), root=clone)
        self.assertEqual(result.returncode, 1)
        self.assertIn(["unittest.loader._FailedTest.test_broken", "error"],
                      json.loads(output.read_text())["executed"])

    def test_malformed_manifest_and_plan_are_refused(self):
        directory = Path(tempfile.mkdtemp(dir=self.temp.name))
        (directory / "bad.json").write_text("{not json")
        self.assertRefused(self.cli("aggregate", "--manifests", str(directory)), "shard_manifest_malformed")
        clone = Path(self.temp.name) / "noplan"
        shutil.copytree(self.root, clone)
        (clone / shards.PLAN_FILE).unlink()
        self.assertRefused(self.cli("count", root=clone), "plan_file_unavailable")

    def test_missing_run_identity_is_refused(self):
        env = {k: v for k, v in self.env.items() if k != "GITHUB_RUN_ID"}
        self.assertRefused(self.aggregate(self.copies(), env=env), "invalid_run_identity")


class SplitModuleShardTests(ShardFixture):
    MODULES = SPLIT_MODULES
    PLAN = SPLIT_PLAN

    def executed(self, shard):
        return sorted(t for t, _ in self.manifests[shard]["executed"])

    def test_split_module_tests_are_balanced_individually(self):
        plan = json.loads(self.cli("describe").stdout)["units"]
        # Weights: a_slow 8, b_medium 3, c_medium 12/4 = 3 (module weight per test), light 1.
        self.assertEqual(plan, {
            "test_heavy.Heavy.test_a_slow": 0,
            "test_heavy.Heavy.test_b_medium": 1,
            "test_heavy.Heavy.test_c_medium": 1,
            "test_heavy.Heavy.test_d_measured": 2,
            "test_light": 1,
        })
        self.assertEqual(self.executed(0), ["test_heavy.Heavy.test_a_slow"])
        self.assertEqual(self.executed(1), ["test_heavy.Heavy.test_b_medium",
                                            "test_heavy.Heavy.test_c_medium", "test_light.Light.test_runs"])

    def test_real_time_test_in_split_module_isolates_only_that_test(self):
        self.assertEqual(self.executed(2), ["test_heavy.Heavy.test_d_measured"])

    def test_split_union_is_complete_and_aggregates(self):
        result = self.aggregate(self.copies())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["discovered_count"], 5)
        executed = [t for m in self.manifests.values() for t, _ in m["executed"]]
        self.assertEqual(len(executed), 5)
        self.assertEqual(len(set(executed)), 5)

    def test_reassigned_split_test_is_refused(self):
        manifests = self.copies()
        manifests[1]["assigned"].remove("test_heavy.Heavy.test_b_medium")
        manifests[0]["assigned"] = sorted(manifests[0]["assigned"] + ["test_heavy.Heavy.test_b_medium"])
        self.assertRefused(self.aggregate(manifests), "shard_assignment_mismatch")

    def test_changed_split_plan_changes_identity_and_is_refused(self):
        clone = self.with_plan("unsplit", split_modules=[], test_seconds={})
        self.assertRefused(self.aggregate(self.copies(), root=clone), "shard_discovery_or_plan_mismatch")

    def test_invalid_split_configuration_is_refused(self):
        cases = (
            ({"split_modules": "test_heavy"}, "plan_file_split_modules"),
            ({"split_modules": ["test_heavy", "test_heavy"]}, "plan_file_split_modules"),
            ({"split_modules": [1]}, "plan_file_split_modules"),
            ({"test_seconds": {"test_light.Light.test_runs": 1}}, "plan_file_test_weights"),
            ({"test_seconds": {"test_heavy.Heavy.test_a_slow": -1}}, "plan_file_test_weights"),
            ({"test_seconds": {"test_heavy.Heavy.test_a_slow": "8"}}, "plan_file_test_weights"),
            ({"test_seconds": []}, "plan_file_test_weights"),
        )
        for index, (changes, category) in enumerate(cases):
            with self.subTest(changes=changes):
                self.assertRefused(self.cli("describe", root=self.with_plan(f"bad{index}", **changes)), category)


if __name__ == "__main__":
    unittest.main()
