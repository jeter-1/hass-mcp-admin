"""Offline checks of the CI lane-group runner (HAMCP-443 D1, D2, D4, D8, carried item 7b).

No Docker, network, Home Assistant, GitHub credential or production key: every harness
process, Docker read and git read is a mock, and the run context is the synthetic one
the assemble tests already verify. The runner refuses off Actions and without the run's
verified context before any process starts; a group passes only on each process's own
CI receipt for this run saying PASS; and what it writes is exactly what assemble admits.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "scripts"), str(ROOT / "tests")]

import core_created_object_local as created_object  # noqa: E402
import core_evidence_lane_group as lg  # noqa: E402
import core_evidence_workflow as w  # noqa: E402
import core_release_evidence as e  # noqa: E402
import core_thread_matter_topology_a2_local as a2  # noqa: E402
import core_thread_matter_topology_local as a1  # noqa: E402
from marketplace_read_contract_acceptance import ExternalDependencyUnavailable  # noqa: E402
from test_core_release_evidence import RUN, assembly_inputs, ctx  # noqa: E402

RUNTIME = "d" * 40
ARCHIVE = "/runner/core-source.tar.gz"


# A proved D6a record as Lane.d6a_proof keeps it (HAMCP-443 part C item 3).
PROVED = {"proved": True, "network": "lane-net", "internal": False, "containers": {"lane-core": {
    "network_mode": "lane-net", "networks": ["lane-net"], "metadata": "blocked 110"}}}


class Reached(Exception):
    """Raised by a mock that must not be reached."""


def receipt(name, value, **changes):
    """A harness's CI receipt for this run, PASS and clean unless changed."""
    result = {"group": name, "mode": "ci", "pull": "digest", "source_commit": value["run"]["GITHUB_SHA"],
              "runtime_tree": RUNTIME, "core_version": e.slot(value)["version"], "result": "PASS",
              "cleanup": {"clean": True, "containers_absent": True, "network_absent": True}, "d6a": PROVED}
    if name in ("scene_create_delete", "created_helper_delete"):
        result["lane"] = name
    result.update(changes)
    return {k: v for k, v in result.items() if v is not Ellipsis}


class Harness:
    """Stands in for the harness processes: writes each one's receipt where the runner reads it."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes); self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append((argv, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        returncode, name, body = outcome
        run_dir = Path(argv[argv.index("--run-dir") + 1]); run_dir.mkdir(parents=True)
        if body is not None:
            (run_dir / name).write_text(json.dumps(body))
        return returncode


def docker(containers=(), networks=()):
    def command(args, **_kwargs):
        if args[:2] == ["docker", "ps"]:
            return mock.Mock(stdout="\n".join(containers).encode())
        if args[:3] == ["docker", "network", "ls"]:
            return mock.Mock(stdout="\n".join(networks).encode())
        raise Reached(args)
    return command


class Scratch(unittest.TestCase):
    def setUp(self):
        (ROOT / ".artifacts").mkdir(exist_ok=True)
        self.scratch = tempfile.TemporaryDirectory(dir=ROOT / ".artifacts")
        self.base = Path(self.scratch.name).resolve()
        self.value = ctx("main"); self.runs = 0

    def tearDown(self):
        self.scratch.cleanup()

    def runner(self):
        """A fresh RUNNER_TEMP per runner invocation, as each lane-groups job has."""
        self.runs += 1
        return str(self.base / "runner" / str(self.runs))

    def group(self, group, outcomes, *, clock=None):
        harness = Harness(outcomes)
        with mock.patch.dict(os.environ, {"RUNNER_TEMP": self.runner()}):
            kwargs = {"clock": clock} if clock else {}
            return lg.run_group(group, ARCHIVE, self.value, RUNTIME, run=harness, **kwargs), harness

    def single(self, group, body, returncode=0):
        name = lg.lanes(group)[0][2]
        (passed, result), _ = self.group(group, [(returncode, name, body)])
        return passed, result


class GroupMap(unittest.TestCase):
    """D1/D8: the seven lane groups, each process's receipt and every name it owns."""

    def test_harness_map_is_exactly_the_seven_lane_groups(self):
        self.assertEqual(set(lg.HARNESSES), set(e.LANE_GROUPS))
        self.assertEqual(len(lg.HARNESSES), 7)

    def test_names_come_from_the_harness_constants(self):
        for group in e.LANE_GROUPS:
            for (module, names), lane in zip(lg.HARNESSES[group], lg.lanes(group)):
                with self.subTest(group=group, harness=module.__name__):
                    if module is created_object:
                        core, network = created_object.resource_names(group)
                        self.assertEqual(lane[1:], (("--lane", group), group + ".json", (core,), network))
                    else:
                        self.assertEqual(lane[1:], ((), module.RECEIPT, tuple(getattr(module, n) for n in names),
                                                    module.NETWORK))

    def test_thread_runs_a1_and_a2_on_separate_networks(self):
        lanes = lg.lanes("thread_matter_topology")
        self.assertEqual([lane[0] for lane in lanes], [a1, a2])
        self.assertNotEqual(lanes[0][4], lanes[1][4])
        self.assertEqual(lanes[1][3], (a2.CORE, a2.STUB))

    def test_owned_containers_are_the_ones_each_harness_tears_down(self):
        for group in e.LANE_GROUPS:
            for module, names in lg.HARNESSES[group]:
                if names is None or module is a2:
                    continue
                tree = ast.parse(Path(module.__file__).read_text())
                torn = {tuple(elt.id for elt in kw.value.elts) for node in ast.walk(tree)
                        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "execute"
                        for kw in node.keywords if kw.arg == "containers"}
                with self.subTest(group=group):
                    self.assertEqual(torn, {tuple(names)})


class Admission(Scratch):
    """D2: off Actions or without the run's verified context, nothing starts and nothing is written."""

    def unreachable(self):
        return [mock.patch.object(lg, "run_group", side_effect=Reached),
                mock.patch.object(lg, "runtime_tree", side_effect=Reached),
                mock.patch.object(lg.subprocess, "run", side_effect=Reached),
                mock.patch.object(lg.subprocess, "Popen", side_effect=Reached)]

    def main(self, env, argv=("helper_value", "--source-archive", ARCHIVE)):
        results = self.base / "results"
        patches = self.unreachable()
        for patch in patches:
            patch.start()
        try:
            with mock.patch.dict(os.environ, {**env, "CORE_EVIDENCE_RESULTS": str(results)}, clear=True):
                return lg.main(list(argv))
        finally:
            for patch in patches:
                patch.stop()
            self.assertFalse(results.exists())

    def test_refuses_off_actions_before_the_context(self):
        for value in (None, "", "True", "1", " true", "false"):
            env = {} if value is None else {"GITHUB_ACTIONS": value}
            with self.subTest(value=value), mock.patch.object(lg, "context", side_effect=Reached), \
                    self.assertRaisesRegex(ValueError, "^lane groups require GitHub Actions$"):
                self.main(env)

    def test_refuses_without_a_context(self):
        with self.assertRaisesRegex(ValueError, "^evidence lanes require an admitted run binding$"):
            self.main({"GITHUB_ACTIONS": "true", **RUN})

    def test_context_verification_is_the_shared_one(self):
        self.assertIs(lg.context, e.context)
        path = self.base / "context.json"; path.write_bytes(e.canonical(self.value))
        env = {"GITHUB_ACTIONS": "true", **RUN, e.CONTEXT_ENV: str(path),
               "CORE_EVIDENCE_CONTEXT_SHA256": "0" * 64}
        with self.assertRaisesRegex(ValueError, "^binding artifact digest mismatch$"):
            self.main(env)
        sha = e.digest(path.read_bytes())
        for key, other in (("GITHUB_RUN_ID", "280"), ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_SHA", "b" * 40)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.main({**env, "CORE_EVIDENCE_CONTEXT_SHA256": sha, key: other})

    def test_unknown_group_or_missing_archive_refuses(self):
        for argv in (("device_configuration", "--source-archive", ARCHIVE), ("integration_inspection",
                     "--source-archive", ARCHIVE), ("helper_value",)):
            with self.subTest(argv=argv), mock.patch("sys.stderr"), self.assertRaises(SystemExit):
                self.main({"GITHUB_ACTIONS": "true"}, argv)


class Process(Scratch):
    """The exact child command: the harness in CI mode with no bytecode written (7b)."""

    def test_child_argv_is_exact(self):
        (passed, _), harness = self.group("thread_matter_topology", [
            (0, a1.RECEIPT, receipt("thread_matter_topology", self.value)),
            (0, a2.RECEIPT, receipt("thread_matter_topology", self.value))])
        self.assertTrue(passed)
        for index, ((argv, _), module) in enumerate(zip(harness.calls, (a1, a2))):
            self.assertEqual(argv, [sys.executable, "-B", str(ROOT / "scripts" / (module.__name__ + ".py")),
                                    "--mode", "ci", "--pull", "digest", "--source-archive", ARCHIVE, "--run-dir",
                                    str(self.base / "runner/1" / ("lane-group-thread_matter_topology-" + str(index)))])
        (passed, _), harness = self.group("scene_create_delete", [
            (0, "scene_create_delete.json", receipt("scene_create_delete", self.value))])
        self.assertTrue(passed)
        self.assertEqual(harness.calls[0][0][3:5], ["--lane", "scene_create_delete"])

    def test_child_environment_writes_no_bytecode(self):
        process = mock.Mock(); process.wait.return_value = 0
        with mock.patch.object(lg.subprocess, "Popen", return_value=process) as popen, \
                mock.patch.dict(os.environ, {"PYTHONDONTWRITEBYTECODE": ""}):
            self.assertEqual(lg.run_harness(["x"], 5), 0)
        self.assertEqual(popen.call_args.kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")
        process.wait.assert_called_once_with(timeout=5)

    def test_timeout_terminates_then_kills(self):
        for teardown_ends in (True, False):
            process = mock.Mock()
            waits = [subprocess.TimeoutExpired("x", 5)] + ([0] if teardown_ends else
                                                           [subprocess.TimeoutExpired("x", 120), -9])
            process.wait.side_effect = waits
            with self.subTest(teardown_ends=teardown_ends), \
                    mock.patch.object(lg.subprocess, "Popen", return_value=process), \
                    self.assertRaises(subprocess.TimeoutExpired):
                lg.run_harness(["x"], 5)
            process.terminate.assert_called_once_with()
            self.assertEqual(process.kill.called, not teardown_ends)
            self.assertEqual(process.wait.call_args_list[1], mock.call(timeout=lg.TEARDOWN_TIMEOUT))

    def test_one_deadline_for_the_whole_group(self):
        ticks = iter([0, 0, 1400, 1400])
        (passed, _), harness = self.group("thread_matter_topology", [
            (0, a1.RECEIPT, receipt("thread_matter_topology", self.value)),
            (0, a2.RECEIPT, receipt("thread_matter_topology", self.value))], clock=lambda: next(ticks))
        self.assertTrue(passed)
        self.assertEqual([timeout for _, timeout in harness.calls], [lg.GROUP_TIMEOUT, lg.GROUP_TIMEOUT - 1400])

    def test_spent_deadline_starts_no_process(self):
        ticks = iter([0, 0, lg.GROUP_TIMEOUT])
        (passed, result), harness = self.group("thread_matter_topology", [
            (0, a1.RECEIPT, receipt("thread_matter_topology", self.value))], clock=lambda: next(ticks))
        self.assertEqual((passed, result), (False, {"failure_type": "TimeoutExpired"}))
        self.assertEqual(len(harness.calls), 1)

    def test_timeout_is_a_failed_group(self):
        passed, result = self.group("helper_value", [subprocess.TimeoutExpired("x", 5)])[0]
        self.assertEqual((passed, result), (False, {"failure_type": "TimeoutExpired"}))


class Verdict(Scratch):
    """D4: passed only on result == PASS, in this run's own clean CI receipt."""

    def test_pass(self):
        for group in e.LANE_GROUPS:
            if group == "thread_matter_topology":
                continue
            body = receipt(group, self.value)
            with self.subTest(group=group):
                self.assertEqual(self.single(group, body), (True, {lg.lanes(group)[0][2][:-len(".json")]: body}))

    def test_anything_but_pass_refuses(self):
        for result in ("WITHHELD", "FAIL", "pass", "PASS ", None, True, ...):
            with self.subTest(result=result):
                self.assertEqual(self.single("helper_value", receipt("helper_value", self.value, result=result)),
                                 (False, {"failure_type": "ValueError"}))

    def test_each_binding_field_refuses(self):
        changes = {"group": ["marketplace_read", None, ...], "mode": ["local", None, ...],
                   "pull": ["local", "tag", ...], "source_commit": ["b" * 40, ...],
                   "runtime_tree": ["0" * 40, ...], "core_version": ["2026.10.2", ...]}
        for key, values in changes.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assertEqual(self.single("helper_value", receipt("helper_value", self.value, **{key: value})),
                                     (False, {"failure_type": "ValueError"}))

    def test_nonzero_exit_unclean_cleanup_or_failure_refuses(self):
        cases = [(1, {}), (2, {}), (0, {"cleanup": {"clean": False}}), (0, {"cleanup": {"clean": "true"}}),
                 (0, {"cleanup": {}}), (0, {"cleanup": ...}), (0, {"failure": {"failure_type": "Refusal"}}),
                 (0, {"failure_type": "Refusal"})]
        for returncode, change in cases:
            with self.subTest(returncode=returncode, change=change):
                self.assertEqual(self.single("helper_value", receipt("helper_value", self.value, **change),
                                             returncode), (False, {"failure_type": "ValueError"}))

    def test_missing_or_linked_receipt_refuses(self):
        self.assertEqual(self.single("helper_value", None), (False, {"failure_type": "ValueError"}))
        real = self.base / "real.json"; real.write_text(json.dumps(receipt("helper_value", self.value)))

        def linked(argv, _timeout):
            run_dir = Path(argv[argv.index("--run-dir") + 1]); run_dir.mkdir(parents=True)
            (run_dir / "helper_value.json").symlink_to(real)
            return 0
        with mock.patch.dict(os.environ, {"RUNNER_TEMP": self.runner()}):
            self.assertEqual(lg.run_group("helper_value", ARCHIVE, self.value, RUNTIME, run=linked),
                             (False, {"failure_type": "ValueError"}))

    def test_exception_gives_its_type_only(self):
        passed, result = self.group("helper_value", [RuntimeError("token=secret http://ha.local")])[0]
        self.assertEqual((passed, result), (False, {"failure_type": "RuntimeError"}))
        self.assertNotIn(b"secret", e.canonical(result))

    def test_created_object_lane_must_equal_the_group(self):
        for group, other in (("scene_create_delete", "created_helper_delete"),
                             ("created_helper_delete", "scene_create_delete")):
            for lane in (other, None, ...):
                with self.subTest(group=group, lane=lane):
                    self.assertEqual(self.single(group, receipt(group, self.value, lane=lane)),
                                     (False, {"failure_type": "ValueError"}))

    def test_thread_needs_a1_and_a2(self):
        good = receipt("thread_matter_topology", self.value)
        bad = receipt("thread_matter_topology", self.value, result="WITHHELD")
        for first, second in ((good, bad), (bad, good), (bad, bad)):
            (passed, result), harness = self.group("thread_matter_topology",
                                                   [(0, a1.RECEIPT, first), (0, a2.RECEIPT, second)])
            with self.subTest(first=first["result"], second=second["result"]):
                self.assertEqual((passed, result), (False, {"failure_type": "ValueError"}))
                self.assertEqual(len(harness.calls), 2)
        (passed, result), harness = self.group("thread_matter_topology",
                                               [RuntimeError("a1"), (0, a2.RECEIPT, good)])
        self.assertEqual((passed, result), (False, {"failure_type": "RuntimeError"}))
        self.assertEqual(len(harness.calls), 2)
        (passed, result), _ = self.group("thread_matter_topology", [(0, a1.RECEIPT, good), (0, a2.RECEIPT, good)])
        self.assertEqual((passed, set(result)), (True, {"thread_matter_topology", "thread_matter_topology_a2"}))


class ExternalDependency(Scratch):
    """D6b: only marketplace's own code-owned store signal, in its bound receipt, is the retry failure."""

    def signal(self, group="marketplace_read", **changes):
        return receipt(group, self.value, **{"result": "WITHHELD",
                                             "failure_type": ExternalDependencyUnavailable.__name__, **changes})

    def test_marketplace_store_signal_is_the_retry_failure(self):
        self.assertEqual(self.single("marketplace_read", self.signal(), returncode=1),
                         (False, {"failure_type": e.EXTERNAL_DEPENDENCY}))

    def test_signal_needs_the_binding(self):
        for key, value in (("source_commit", "b" * 40), ("mode", "local"), ("runtime_tree", "0" * 40),
                           ("group", "security_metadata")):
            with self.subTest(key=key):
                self.assertEqual(self.single("marketplace_read", self.signal(**{key: value})),
                                 (False, {"failure_type": "ValueError"}))

    def test_signal_from_another_group_is_an_ordinary_failure(self):
        for group in e.LANE_GROUPS:
            if group in ("marketplace_read", "thread_matter_topology"):
                continue
            with self.subTest(group=group):
                self.assertEqual(self.single(group, self.signal(group)), (False, {"failure_type": "ValueError"}))

    def test_other_marketplace_failures_are_ordinary(self):
        for failure_type in ("Refusal", "RuntimeError", e.EXTERNAL_DEPENDENCY, None):
            with self.subTest(failure_type=failure_type):
                body = self.signal(failure_type=failure_type)
                self.assertEqual(self.single("marketplace_read", body), (False, {"failure_type": "ValueError"}))
        self.assertEqual(self.single("marketplace_read", self.signal(result="PASS")),
                         (False, {"failure_type": "ValueError"}))


class Written(Scratch):
    """D8: what the runner writes is exactly what assemble admits; residue writes nothing."""

    def assembly(self):
        path = self.base / "assembly"; path.mkdir()
        return path

    def run_main(self, group, outcomes, *, present=((), ()), extra=None):
        prepared, receipts, env, _ = assembly_inputs(self.assembly(), self.value, "coverage")
        folder = receipts / w.producer_name("coverage", group)
        shutil.rmtree(folder)
        harness = Harness(outcomes)
        with mock.patch.dict(os.environ, {**env, "GITHUB_ACTIONS": "true", "RUNNER_TEMP": self.runner(),
                                          "CORE_EVIDENCE_RESULTS": str(folder), **(extra or {})}, clear=True), \
                mock.patch.object(lg, "run_harness", harness), \
                mock.patch.object(lg.subprocess, "Popen", side_effect=Reached), \
                mock.patch.object(lg, "runtime_tree", return_value=RUNTIME), \
                mock.patch.object(lg.subprocess, "run", side_effect=docker(*present)), \
                mock.patch("sys.stdout"):
            code = lg.main([group, "--source-archive", ARCHIVE])
        return code, prepared, receipts, env, folder

    def assemble(self, prepared, receipts, env):
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.stdout"):
            w.assemble(prepared, receipts, "coverage")
        return e.read_json(receipts / "candidate/core-review-evidence.json")

    def test_pass_is_admitted_by_assemble(self):
        code, prepared, receipts, env, folder = self.run_main(
            "helper_value", [(0, "helper_value.json", receipt("helper_value", self.value))])
        self.assertEqual(code, 0)
        self.assertEqual({p.name for p in folder.iterdir()}, {"helper_value.json", "cleanup-helper_value.json"})
        group = e.read_json(folder / "helper_value.json")
        self.assertEqual((group["model"], group["group"], group["passed"]), ("core-evidence-group-v1", "helper_value",
                                                                             True))
        cleanup = e.read_json(folder / "cleanup-helper_value.json")
        self.assertEqual(cleanup, {"clean": True, "run": RUN, "context_sha256": env["CORE_EVIDENCE_CONTEXT_SHA256"],
                                   "group": "helper_value", "owned": {
                                       "containers": sorted(lg.lanes("helper_value")[0][3]),
                                       "networks": [lg.lanes("helper_value")[0][4]]}})
        self.assertTrue(self.assemble(prepared, receipts, env)["lane_groups"]["helper_value"])

    def test_thread_owned_names_cover_both_processes(self):
        good = receipt("thread_matter_topology", self.value)
        code, prepared, receipts, env, folder = self.run_main(
            "thread_matter_topology", [(0, a1.RECEIPT, good), (0, a2.RECEIPT, good)])
        owned = e.read_json(folder / "cleanup-thread_matter_topology.json")["owned"]
        self.assertEqual(owned, {"containers": sorted({a1.CORE, a2.CORE, a2.STUB}),
                                 "networks": sorted({a1.NETWORK, a2.NETWORK})})
        self.assertTrue(self.assemble(prepared, receipts, env)["lane_groups"]["thread_matter_topology"])

    def test_failed_group_is_recorded_and_withheld(self):
        code, prepared, receipts, env, folder = self.run_main("security_metadata", [RuntimeError("x")])
        self.assertEqual(code, 0)
        self.assertEqual(e.read_json(folder / "security_metadata.json")["receipt"], {"failure_type": "RuntimeError"})
        evidence = self.assemble(prepared, receipts, env)
        self.assertFalse(evidence["lane_groups"]["security_metadata"])

    def test_marketplace_store_signal_fails_the_run_before_a_candidate(self):
        body = receipt("marketplace_read", self.value, result="WITHHELD",
                       failure_type=ExternalDependencyUnavailable.__name__)
        code, prepared, receipts, env, _ = self.run_main("marketplace_read", [(1, "marketplace_read.json", body)])
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.stdout"), \
                self.assertRaisesRegex(ValueError, "^external dependency unavailable: marketplace_read$"):
            w.assemble(prepared, receipts, "coverage")
        self.assertFalse((receipts / "candidate").exists())

    def test_residue_writes_nothing(self):
        lanes = lg.lanes("thread_matter_topology")
        good = receipt("thread_matter_topology", self.value)
        for present in (((a2.STUB,), ()), ((), (lanes[0][4],)), ((a1.CORE, "unrelated"), (a2.NETWORK,))):
            with self.subTest(present=present):
                with self.assertRaisesRegex(ValueError, "^owned disposable residue remains$"):
                    self.run_main("thread_matter_topology", [(0, a1.RECEIPT, good), (0, a2.RECEIPT, good)],
                                  present=present)
                folder = self.base / "assembly/receipts" / w.producer_name("coverage", "thread_matter_topology")
                self.assertFalse(folder.exists())
            shutil.rmtree(self.base / "assembly")

    def test_unrelated_containers_are_not_residue(self):
        code, *_ = self.run_main("helper_value", [(0, "helper_value.json", receipt("helper_value", self.value))],
                                 present=(("other-lane-core",), ("bridge", "host", "none")))
        self.assertEqual(code, 0)

    def test_d6a_lines_reach_the_job_summary(self):
        summary = self.base / "summary.md"
        code, *_ = self.run_main("helper_value", [(0, "helper_value.json", receipt("helper_value", self.value))],
                                 extra={"GITHUB_STEP_SUMMARY": str(summary)})
        self.assertEqual(code, 0)
        self.assertEqual(summary.read_text().splitlines(), [
            "D6a helper_value: network lane-net internal=False proved=True",
            "D6a helper_value: lane-core network_mode=lane-net networks=lane-net metadata 169.254.169.254:80 "
            "blocked 110"])

    def test_a_failed_d6a_proof_fails_the_row(self):
        failed = {**PROVED, "proved": False, "containers": {"lane-core": {
            "network_mode": "host", "networks": [], "metadata": "reachable"}}}
        body = receipt("helper_value", self.value, result="FAIL", failure="d6a_proof_failed", d6a=failed)
        code, _, _, _, folder = self.run_main("helper_value", [(1, "helper_value.json", body)])
        self.assertEqual(code, 1)
        self.assertFalse(e.read_json(folder / "helper_value.json")["passed"])

    def test_a_pass_without_a_proof_fails_the_row(self):
        for proof in (Ellipsis, None, {**PROVED, "proved": "true"}):
            with self.subTest(proof=proof):
                code, *_ = self.run_main(
                    "helper_value", [(0, "helper_value.json", receipt("helper_value", self.value, d6a=proof))])
                self.assertEqual(code, 1)
            shutil.rmtree(self.base / "assembly")

    def test_thread_needs_both_proofs(self):
        good, failed = receipt("thread_matter_topology", self.value), receipt(
            "thread_matter_topology", self.value, result="FAIL", d6a={**PROVED, "proved": False, "internal": True})
        code, *_ = self.run_main("thread_matter_topology", [(0, a1.RECEIPT, good), (1, a2.RECEIPT, failed)])
        self.assertEqual(code, 1)

    def test_a_lane_that_never_reached_its_proof_stays_a_recorded_failure(self):
        body = receipt("security_metadata", self.value, result="FAIL", failure="x", d6a=Ellipsis)
        for outcome in (RuntimeError("x"), (1, "security_metadata.json", body)):
            with self.subTest(outcome=type(outcome).__name__):
                code, _, _, _, folder = self.run_main("security_metadata", [outcome])
                self.assertEqual(code, 0)
                self.assertFalse(e.read_json(folder / "security_metadata.json")["passed"])
            shutil.rmtree(self.base / "assembly")

    def test_docker_read_failure_writes_nothing(self):
        def failing(args, **_kwargs):
            raise subprocess.CalledProcessError(1, args)
        prepared, receipts, env, _ = assembly_inputs(self.assembly(), self.value, "coverage")
        folder = receipts / w.producer_name("coverage", "helper_value"); shutil.rmtree(folder)
        with mock.patch.dict(os.environ, {**env, "GITHUB_ACTIONS": "true", "RUNNER_TEMP": self.runner(),
                                          "CORE_EVIDENCE_RESULTS": str(folder)}, clear=True), \
                mock.patch.object(lg, "run_harness",
                                  Harness([(0, "helper_value.json", receipt("helper_value", self.value))])), \
                mock.patch.object(lg.subprocess, "Popen", side_effect=Reached), \
                mock.patch.object(lg, "runtime_tree", return_value=RUNTIME), \
                mock.patch.object(lg.subprocess, "run", side_effect=failing), \
                self.assertRaises(subprocess.CalledProcessError):
            lg.main(["helper_value", "--source-archive", ARCHIVE])
        self.assertFalse(folder.exists())


class RuntimeTree(unittest.TestCase):
    """7e: the expectation is this checkout's own Engineering tree, read from git, never a constant."""

    def test_reads_the_checkout(self):
        with mock.patch.object(lg.subprocess, "run", return_value=mock.Mock(stdout=b"abc\n")) as run:
            self.assertEqual(lg.runtime_tree(), "abc")
        self.assertEqual(run.call_args.args[0], ["git", "rev-parse", lg.core_lane_host.RUNTIME_TREE])
        self.assertEqual(run.call_args.kwargs["cwd"], e.ROOT)
        self.assertTrue(run.call_args.kwargs["check"])
        expected = subprocess.run(["git", "rev-parse", "HEAD:hass_mcp_engineering_beta/ha_mcp_engineering"],
                                  cwd=ROOT, capture_output=True, check=True).stdout.decode().strip()
        self.assertEqual(lg.runtime_tree(), expected)


class StubBytecode(unittest.TestCase):
    """7b: under the runner's child environment, importing the A2 stub leaves its pinned file set
    exact; with bytecode allowed, the same import writes __pycache__ and the strict check refuses."""

    def setUp(self):
        (ROOT / ".artifacts").mkdir(exist_ok=True)
        self.scratch = tempfile.TemporaryDirectory(dir=ROOT / ".artifacts")
        self.directory = Path(self.scratch.name).resolve() / "stub"
        shutil.copytree(lg.core_lane_host.MATTER_STUB, self.directory,
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.stub = {"committed_dir": "tests/fixtures/hamcp297_matter_server_stub",
                     "files": {p.relative_to(self.directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in self.directory.rglob("*") if p.is_file()}}
        # aiohttp is the stub's container dependency, not the unit environment's: a stand-in.
        self.code = ("import sys; from unittest import mock; sys.modules['aiohttp'] = mock.MagicMock(); "
                     "sys.path.insert(0, sys.argv[1]); import stub, driver")

    def tearDown(self):
        self.scratch.cleanup()

    def test_child_environment_keeps_the_stub_exact(self):
        self.assertEqual(lg.run_harness([sys.executable, "-c", self.code, str(self.directory)], 60), 0)
        self.assertEqual(a2.verify_stub(self.directory, self.stub)["files_verified"], len(self.stub["files"]))

    def test_bytecode_breaks_the_strict_file_set(self):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
        subprocess.run([sys.executable, "-c", self.code, str(self.directory)], env=env, check=True, timeout=60)
        self.assertTrue((self.directory / "__pycache__").is_dir())
        with self.assertRaises(a1.Refusal) as caught:
            a2.verify_stub(self.directory, self.stub)
        self.assertEqual(str(caught.exception), "a2_stub_file_set_mismatch")


if __name__ == "__main__":
    unittest.main()
