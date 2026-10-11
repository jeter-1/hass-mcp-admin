"""Run the Engineering unit suite as deterministic shards and prove completeness.

Every shard and the aggregator perform the same full ``unittest`` discovery
(``-s tests`` with ``tests`` as the top-level directory, exactly as
``python -m unittest discover -s tests`` does). The shard plan is a pure
function of that discovered set and the committed plan file, so every job
derives the identical partition without exchanging state.

The aggregator re-discovers the suite itself and refuses unless every shard
manifest comes from this run attempt and commit, the shards are exactly
0..N-1, each shard executed exactly its assigned tests once, the union of the
shards equals the full discovered set with no duplicates, and every executed
test succeeded (skips and expected failures are successes, as in unittest).
Only then may the same-run full-discovery receipt be recorded.

Skips are accounted as unittest accounts them. A test unittest never starts
because its own class's ``setUpClass`` or its module's ``setUpModule`` raised
``SkipTest`` is recorded once as skipped; any other assigned test that never
started stays missing and is refused. A skipped subtest does not give its
parent an outcome of its own, so a parent with no failing subtest is a success.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import unittest
from unittest.case import _SubTest
from unittest.suite import _ErrorHolder
from unittest.util import strclass

PLAN_FILE = "ci/unit-test-shards.json"
REAL_TIME_ATTRIBUTE = "__hamcp_real_time_measurement__"
SUCCESS_OUTCOMES = frozenset({"success", "skipped", "expected_failure"})
FAILED_IMPORT_PREFIXES = ("unittest.loader._FailedTest.", "unittest.loader.ModuleImportFailure.")
MAX_MANIFEST = 4_000_000


class Refusal(ValueError):
    pass


def require(condition, category):
    if not condition:
        raise Refusal(category)


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def discover(root):
    """Load the full suite exactly as ``unittest discover -s tests`` does."""

    start = str(Path(root, "tests").resolve())
    # ``python -m unittest`` run from the repository root puts the root first on
    # sys.path (tests import ``tests.<module>``); mirror that rather than rely on
    # an earlier test module inserting it.
    repository = str(Path(root).resolve())
    if repository not in sys.path:
        sys.path.insert(0, repository)
    loader = unittest.TestLoader()
    suite = loader.discover(start, top_level_dir=start)
    cases = list(flatten(suite))
    ids = [case.id() for case in cases]
    require(len(ids) == len(set(ids)), "duplicate_discovered_test_id")
    require(not loader.errors or any(i.startswith(FAILED_IMPORT_PREFIXES) for i in ids),
            "discovery_errors_without_failed_tests")
    return cases


def module_of(test_id):
    for prefix in FAILED_IMPORT_PREFIXES:
        if test_id.startswith(prefix):
            return test_id[len(prefix):]
    return test_id.split(".", 1)[0]


def is_real_time(case):
    method = getattr(case, getattr(case, "_testMethodName", ""), None)
    return bool(getattr(type(case), REAL_TIME_ATTRIBUTE, None) or getattr(method, REAL_TIME_ATTRIBUTE, None))


def load_plan_file(root):
    path = Path(root, PLAN_FILE)
    require(path.is_file() and not path.is_symlink(), "plan_file_unavailable")
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict) and value.get("schema_version") == 1, "plan_file_schema")
    general = value.get("general_shards")
    require(type(general) is int and 1 <= general <= 16, "plan_file_shard_count")
    weights = value.get("module_seconds")
    require(isinstance(weights, dict) and all(
        isinstance(k, str) and type(v) in {int, float} and v >= 0 for k, v in weights.items()),
        "plan_file_weights")
    default = value.get("default_module_seconds")
    require(type(default) in {int, float} and default > 0, "plan_file_default_weight")
    split = value.get("split_modules", [])
    require(isinstance(split, list) and all(isinstance(m, str) for m in split)
            and len(split) == len(set(split)), "plan_file_split_modules")
    test_weights = value.get("test_seconds", {})
    require(isinstance(test_weights, dict) and all(
        isinstance(k, str) and module_of(k) in split and type(v) in {int, float} and v >= 0
        for k, v in test_weights.items()), "plan_file_test_weights")
    return general, weights, default, frozenset(split), test_weights


def unit_of(test_id, split):
    """A module is one scheduling unit unless the plan splits it into its tests."""

    module = module_of(test_id)
    return test_id if module in split and not test_id.startswith(FAILED_IMPORT_PREFIXES) else module


def plan(root, cases):
    """Return ({unit: shard}, shard_count, split); the last shard is real-time only.

    A unit is a module, or one test of a module named in ``split_modules``. A
    real-time measurement moves its whole unit (module, or that one split test)
    to the final shard; the rest are balanced longest-first, ties by name.
    """

    general, weights, default, split, test_weights = load_plan_file(root)
    counts = {}
    for case in cases:
        counts[module_of(case.id())] = counts.get(module_of(case.id()), 0) + 1

    def weight(unit):
        module = module_of(unit)
        if unit != module:
            return float(test_weights.get(unit, float(weights.get(module, default)) / counts[module]))
        return float(weights.get(unit, default))

    units = sorted({unit_of(case.id(), split) for case in cases})
    assignment = {unit_of(case.id(), split): general for case in cases if is_real_time(case)}
    loads = [0.0] * general
    for unit in sorted((u for u in units if u not in assignment), key=lambda u: (-weight(u), u)):
        shard = min(range(general), key=lambda i: (loads[i], i))
        assignment[unit] = shard
        loads[shard] += weight(unit)
    return assignment, general + 1, split


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def identity(root, cases):
    ids = sorted(case.id() for case in cases)
    assignment, count, split = plan(root, cases)
    shard_of = {test_id: assignment[unit_of(test_id, split)] for test_id in ids}
    return ids, shard_of, count, {
        "discovered_count": len(ids),
        "discovered_sha256": digest(ids),
        "plan_sha256": digest(assignment),
        "shard_count": count,
    }


def head(root):
    try:
        value = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15).stdout.decode().strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refusal("git_identity_unavailable") from exc
    require(re.fullmatch(r"[0-9a-f]{40}", value), "invalid_commit")
    return value


def run_identity(env):
    run_id, attempt = env.get("GITHUB_RUN_ID", ""), env.get("GITHUB_RUN_ATTEMPT", "")
    require(re.fullmatch(r"[1-9][0-9]{0,19}", run_id) and re.fullmatch(r"[1-9][0-9]{0,19}", attempt),
            "invalid_run_identity")
    return run_id, attempt


class RecordingResult(unittest.TextTestResult):
    """Record each started test id and its final outcome.

    Only started tests receive outcomes. A class or module fixture reports
    through a placeholder (``setUpClass (module.Class)``); its skips and errors
    are kept apart, so a fixture error stays unattributed.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executed = []
        self.outcomes = {}
        self.durations = {}
        self.fixture_skips = set()
        self.fixture_errors = set()
        self.skipped_subtest_parents = set()
        self.started = set()
        self._start_times = {}

    def _set(self, test, outcome):
        test_id = test.id()
        if test_id not in self.started:
            return
        if outcome in SUCCESS_OUTCOMES and self.outcomes.get(test_id) not in (None, "success"):
            return
        self.outcomes[test_id] = outcome

    def outcome(self, test_id):
        """The final outcome; unittest reports none for a parent after a subtest skipped."""

        if test_id in self.outcomes:
            return self.outcomes[test_id]
        return "success" if test_id in self.skipped_subtest_parents else "error"

    def startTest(self, test):
        self.executed.append(test.id())
        self.started.add(test.id())
        self._start_times[test.id()] = time.monotonic()
        super().startTest(test)

    def stopTest(self, test):
        started = self._start_times.pop(test.id(), None)
        if started is not None:
            self.durations[test.id()] = round(time.monotonic() - started, 3)
        super().stopTest(test)

    def addSuccess(self, test):
        super().addSuccess(test)
        self._set(test, "success")

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        if isinstance(test, _SubTest):
            self.skipped_subtest_parents.add(test.test_case.id())
        elif isinstance(test, _ErrorHolder):
            self.fixture_skips.add(test.id())
        else:
            self._set(test, "skipped")

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._set(test, "expected_failure")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._set(test, "unexpected_success")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._set(test, "failure")

    def addError(self, test, err):
        super().addError(test, err)
        if isinstance(test, _ErrorHolder):
            self.fixture_errors.add(test.id())
        self._set(test, "error")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self._set(test, "failure" if issubclass(err[0], test.failureException) else "error")


def fixture_skipped(selected, result):
    """Rows for assigned tests never started because their own fixture raised SkipTest.

    Each test's placeholder names are built from its own class and module the
    way unittest builds them, so a skip never covers a test of another class or
    module. A class placeholder counts only when unittest marked that class's
    setup failed, and a fixture that also reported an error covers nothing.
    """

    rows = []
    for case in selected:
        if case.id() in result.started:
            continue
        cls = type(case)
        placeholders = {f"setUpModule ({cls.__module__})"}
        if getattr(cls, "_classSetupFailed", False) is True:
            placeholders.add(f"setUpClass ({strclass(cls)})")
        if placeholders & result.fixture_skips and not placeholders & result.fixture_errors:
            rows.append([case.id(), "skipped"])
    return rows


def run_shard(root, shard, env, output):
    cases = discover(root)
    ids, assignment, count, bound = identity(root, cases)
    require(0 <= shard < count, "invalid_shard_index")
    selected = [case for case in cases if assignment[case.id()] == shard]
    run_id, attempt = run_identity(env)
    runner = unittest.TextTestRunner(stream=sys.stderr, verbosity=2, resultclass=RecordingResult)
    result = runner.run(unittest.TestSuite(selected))
    manifest = {
        "schema_version": 1,
        "source_sha": head(root),
        "run_id": run_id,
        "run_attempt": attempt,
        "shard": shard,
        **bound,
        "assigned": sorted(case.id() for case in selected),
        "executed": [[test_id, result.outcome(test_id)] for test_id in result.executed]
        + fixture_skipped(selected, result),
        "unattributed_errors": len([t for t, _ in result.errors if t.id() not in result.outcomes])
        + len([t for t, _ in result.failures if t.id() not in result.outcomes]),
        "was_successful": result.wasSuccessful(),
        "durations": result.durations,
        "python_version": platform.python_version(),
    }
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return manifest


def verify(root, manifests, env):
    """Refuse unless the manifests prove the full suite ran once and passed."""

    cases = discover(root)
    ids, assignment, count, bound = identity(root, cases)
    run_id, attempt = run_identity(env)
    commit = head(root)
    require(len(manifests) == count, "shard_manifest_count_mismatch")
    by_shard = {}
    for manifest in manifests:
        require(isinstance(manifest, dict) and manifest.get("schema_version") == 1, "shard_manifest_schema")
        shard = manifest.get("shard")
        require(type(shard) is int and 0 <= shard < count and shard not in by_shard, "shard_index_invalid_or_duplicate")
        by_shard[shard] = manifest
        require(manifest.get("source_sha") == commit, "shard_source_mismatch")
        require(manifest.get("run_id") == run_id and manifest.get("run_attempt") == attempt, "shard_run_mismatch")
        require(all(manifest.get(key) == value for key, value in bound.items()), "shard_discovery_or_plan_mismatch")
        expected = sorted(test_id for test_id in ids if assignment[test_id] == shard)
        require(manifest.get("assigned") == expected, "shard_assignment_mismatch")
        executed = manifest.get("executed")
        require(isinstance(executed, list) and all(
            isinstance(row, list) and len(row) == 2 and all(isinstance(v, str) for v in row) for row in executed),
            "shard_execution_malformed")
        executed_ids = [row[0] for row in executed]
        require(len(executed_ids) == len(set(executed_ids)), "test_executed_more_than_once")
        require(sorted(executed_ids) == expected, "shard_execution_incomplete_or_extra")
        require(all(row[1] in SUCCESS_OUTCOMES for row in executed), "test_did_not_succeed")
        require(manifest.get("unattributed_errors") == 0, "shard_fixture_error")
        require(manifest.get("was_successful") is True, "shard_did_not_succeed")
    union = [row[0] for shard in range(count) for row in by_shard[shard]["executed"]]
    require(len(union) == len(set(union)), "test_executed_in_multiple_shards")
    require(sorted(union) == ids, "shard_union_differs_from_discovery")
    return {"result": "success", "source_sha": commit, "run_id": run_id, "run_attempt": attempt, **bound}


def load_manifests(directory):
    manifests = []
    for path in sorted(Path(directory).rglob("*.json")):
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_MANIFEST, "shard_manifest_unreadable")
        try:
            manifests.append(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, UnicodeError) as exc:
            raise Refusal("shard_manifest_malformed") from exc
    return manifests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    count = commands.add_parser("count", help="print the shard indexes as a JSON list")
    run = commands.add_parser("run")
    run.add_argument("--shard", type=int, required=True)
    run.add_argument("--output", type=Path, required=True)
    aggregate = commands.add_parser("aggregate")
    aggregate.add_argument("--manifests", type=Path, required=True)
    describe = commands.add_parser("describe", help="print the module plan for review")
    for command in (count, run, aggregate, describe):
        command.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        if args.operation == "count":
            print(json.dumps(list(range(load_plan_file(args.repo_root)[0] + 1))))
        elif args.operation == "describe":
            cases = discover(args.repo_root)
            _, _, _, bound = identity(args.repo_root, cases)
            units, _, _ = plan(args.repo_root, cases)
            print(json.dumps({**bound, "units": units}, sort_keys=True, indent=1))
        elif args.operation == "run":
            manifest = run_shard(args.repo_root, args.shard, os.environ, args.output)
            print(f"Shard {args.shard}: {len(manifest['executed'])} of {manifest['discovered_count']} tests executed.")
            if not manifest["was_successful"]:
                parser.exit(1, "Unit test shard did not succeed.\n")
        else:
            summary = verify(args.repo_root, load_manifests(args.manifests), os.environ)
            print(json.dumps(summary, sort_keys=True))
    except Refusal as exc:
        parser.exit(1, "Unit test shard validation refused: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
