"""Shared process-state isolation and the module leak guard (HAMCP-206).

Importing this module installs ``support.process_isolation`` for the whole run.
Serial ``unittest discover``, ``check.ps1 -Parallel`` and CI shards all import
every test module during discovery, so the hook is active in each of them
before any test runs. The tests below prove that a leaking module is named and
failed, that state a test leaves behind is reset before the next test, that
nested in-place changes to the catalog are restored by identity and value, that
the failure reaches CI's shard manifests as an unattributed fixture error, and
that the hook is bound to the run executing this suite.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "tests", ROOT / "hass_mcp_engineering_beta"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from support import process_isolation  # noqa: E402

process_isolation.install()

from ha_mcp_engineering import capabilities  # noqa: E402

SHARD_SCRIPT = ROOT / "scripts/unit_test_shards.py"
SYNTHETIC_ENV = "HAMCP206_SYNTHETIC_LEAK"
SYNTHETIC_VALUE = "synthetic-value-never-reported"
SYNTHETIC_TOOL = "hamcp206_synthetic_leaked_tool"
NESTED_TOOL = "hamcp206_synthetic_nested_tool"


def _synthetic_module(name, **tests):
    """Register a throwaway test module so module fixtures resolve it."""

    module = types.ModuleType(name)
    namespace = {"__module__": name}
    namespace.update(tests)
    module.Case = type("Case", (unittest.TestCase,), namespace)
    sys.modules[name] = module
    return module


def _calls(action):
    def test(case):
        action()
    return test


def _leak_catalog_and_env(cls):
    capabilities.replace_dynamic_upstream_capabilities(
        ({"name": SYNTHETIC_TOOL},), {"status": "synthetic"})
    os.environ[SYNTHETIC_ENV] = SYNTHETIC_VALUE


class GuardHarness:
    """Runs synthetic modules under a ``Guard`` bound to their own result."""

    def setUp(self):
        self.names = []

    def tearDown(self):
        for name in self.names:
            sys.modules.pop(name, None)

    def module(self, name, **tests):
        self.names.append(name)
        return _synthetic_module(name, **tests)

    def run_guarded(self, *modules, probes=process_isolation.PROBES):
        suite = unittest.TestSuite(
            unittest.defaultTestLoader.loadTestsFromTestCase(module.Case) for module in modules)
        result = unittest.TestResult()
        guard = process_isolation.Guard(probes)
        process_isolation.bind(guard, result)
        self.addCleanup(process_isolation.unbind, result)
        suite.run(result)
        return guard, result


class GuardTests(GuardHarness, unittest.TestCase):

    def test_module_fixture_leak_fails_the_module_and_names_entries(self):
        leaker = self.module(
            "hamcp206_synthetic_leaker",
            setUpClass=classmethod(_leak_catalog_and_env),
            test_runs=lambda self: None)
        observed = {}
        clean = self.module(
            "hamcp206_synthetic_clean",
            test_sees_baseline=lambda case: observed.update(
                catalog=capabilities._DYNAMIC_UPSTREAM_CAPABILITIES,
                env=os.environ.get(SYNTHETIC_ENV)))
        before = capabilities._DYNAMIC_UPSTREAM_CAPABILITIES
        guard, result = self.run_guarded(leaker, clean)

        self.assertEqual(result.testsRun, 2)
        self.assertEqual(result.failures, [])
        self.assertEqual(len(result.errors), 1, result.errors)
        holder, trace = result.errors[0]
        self.assertEqual(str(holder), "tearDownModule (hamcp206_synthetic_leaker)")
        self.assertIn("ProcessStateLeak", trace)
        self.assertIn("'hamcp206_synthetic_leaker'", trace)
        self.assertIn(SYNTHETIC_TOOL, trace)
        self.assertIn("_UPSTREAM_READ_GATEWAY_SUMMARY", trace)
        self.assertIn(SYNTHETIC_ENV, trace)
        self.assertNotIn(SYNTHETIC_VALUE, trace)
        self.assertEqual([module for module, _ in guard.leaks], ["hamcp206_synthetic_leaker"])
        self.assertEqual(guard.modules_checked, 2)
        # The guard restored the baseline, so the next module and this test
        # see neither the leaked catalog nor the leaked variable.
        self.assertIsNone(observed["env"])
        self.assertNotIn(SYNTHETIC_TOOL, json.dumps(observed["catalog"], default=str))
        self.assertIs(capabilities._DYNAMIC_UPSTREAM_CAPABILITIES, before)
        self.assertNotIn(SYNTHETIC_ENV, os.environ)

    def test_leak_in_module_teardown_is_attributed_to_that_module(self):
        leaker = self.module("hamcp206_synthetic_teardown", test_runs=lambda self: None)
        leaker.tearDownModule = lambda: os.environ.__setitem__(SYNTHETIC_ENV, SYNTHETIC_VALUE)
        guard, result = self.run_guarded(leaker)
        self.assertEqual([str(holder) for holder, _ in result.errors],
                         ["tearDownModule (hamcp206_synthetic_teardown)"])
        self.assertNotIn(SYNTHETIC_ENV, os.environ)

    def test_state_a_test_leaves_is_reset_before_the_next_test(self):
        seen = []

        def leaks(case):
            capabilities.replace_dynamic_upstream_capabilities(
                ({"name": SYNTHETIC_TOOL},), {"status": "synthetic"})
            os.environ[SYNTHETIC_ENV] = SYNTHETIC_VALUE
            sys.path.append("/hamcp206-synthetic-path")

        def checks(case):
            seen.append((SYNTHETIC_TOOL in json.dumps(capabilities._DYNAMIC_UPSTREAM_CAPABILITIES),
                         os.environ.get(SYNTHETIC_ENV), "/hamcp206-synthetic-path" in sys.path))

        module = self.module("hamcp206_synthetic_body_leak", test_a_leaks=leaks, test_b_checks=checks)
        guard, result = self.run_guarded(module)
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        self.assertEqual(seen, [(False, None, False)])
        self.assertEqual(guard.tests_reset, 1)
        self.assertEqual(guard.leaks, [])

    def test_unchanged_state_is_neither_reset_nor_reported(self):
        module = self.module("hamcp206_synthetic_quiet", test_quiet=lambda self: None)
        guard, result = self.run_guarded(module)
        self.assertTrue(result.wasSuccessful())
        self.assertEqual((guard.tests_reset, guard.leaks, guard.modules_checked), (0, [], 1))

    def test_unbound_inner_runs_are_left_alone(self):
        leaker = self.module(
            "hamcp206_synthetic_unbound",
            setUpClass=classmethod(_leak_catalog_and_env), test_runs=lambda self: None)
        result = unittest.TestResult()
        unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(leaker.Case)).run(result)
        self.assertEqual(result.errors, [])
        # This test's own reset removes what the inner run left behind.
        self.assertEqual(os.environ.get(SYNTHETIC_ENV), SYNTHETIC_VALUE)

    def test_hook_is_installed_and_bound_to_this_run(self):
        state = getattr(unittest.TestCase, process_isolation.STATE_ATTRIBUTE)
        self.assertIs(process_isolation.install(), state.process_guard)
        self.assertTrue(hasattr(unittest.TestCase.__call__, "__wrapped__"))
        self.assertIsNotNone(state.process_result)
        self.assertIs(state.guards[id(state.process_result)], state.process_guard)
        self.assertIsNotNone(state.process_guard.baseline)


class _Unrestorable:
    """A probe whose restore cannot undo the change it reports."""

    name = "synthetic"

    def __init__(self):
        self.value = 0

    def capture(self):
        return self.value

    def changes(self, saved):
        return [] if self.value == saved else [f"synthetic: {saved} -> {self.value}"]

    def restore(self, saved):
        pass


class DeepRestoreTests(GuardHarness, unittest.TestCase):
    """Nested in-place catalog changes are restored, not only detected (review F1)."""

    def setUp(self):
        super().setUp()
        # Published inside this test, so the process guard resets it afterwards.
        capabilities.replace_dynamic_upstream_capabilities(
            ({"name": NESTED_TOOL,
              "schema": {"type": "object", "properties": {"q": {"type": "string"}}},
              "tags": ["read", {"scope": ["state"]}]},),
            {"status": "original", "nested": {"counts": [1, 2]}})
        self.held = self.containers()
        self.expected = self.values()

    @staticmethod
    def containers():
        """Every container code may hold, from the two globals inward."""

        catalog = capabilities._DYNAMIC_UPSTREAM_CAPABILITIES
        summary = capabilities._UPSTREAM_READ_GATEWAY_SUMMARY
        entry = catalog[0]
        return {"catalog": catalog, "summary": summary, "entry": entry,
                "schema": entry["schema"], "properties": entry["schema"]["properties"],
                "q": entry["schema"]["properties"]["q"], "tags": entry["tags"],
                "tag": entry["tags"][1], "scope": entry["tags"][1]["scope"],
                "nested": summary["nested"], "counts": summary["nested"]["counts"]}

    @staticmethod
    def values():
        return copy.deepcopy((capabilities._DYNAMIC_UPSTREAM_CAPABILITIES,
                              capabilities._UPSTREAM_READ_GATEWAY_SUMMARY))

    def observe(self):
        return self.containers(), self.values()

    def assertPristine(self, observed):
        containers, values = observed
        self.assertEqual(set(containers), set(self.held))
        for name, held in self.held.items():
            self.assertIs(containers[name], held, f"{name} is no longer the original object")
        self.assertEqual(values, self.expected)
        self.assertEqual(values[0][0]["schema"]["type"], "object")

    def test_review_reproduction_nested_mutation_is_restored_for_the_next_test(self):
        observed, later = [], []

        def mutate(case):
            capabilities._DYNAMIC_UPSTREAM_CAPABILITIES[0]["schema"]["type"] = "leaked"

        mutating = self.module("hamcp206_synthetic_nested_mutation", test_a_mutate=mutate,
                               test_b_observe=lambda case: observed.append(self.observe()))
        innocent = self.module("hamcp206_synthetic_after_nested",
                               test_innocent=lambda case: later.append(self.observe()))
        guard, result = self.run_guarded(mutating, innocent)

        self.assertEqual(result.testsRun, 3)
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        self.assertEqual((guard.tests_reset, guard.leaks, guard.modules_checked), (1, [], 2))
        self.assertPristine(observed[0])
        self.assertPristine(later[0])
        self.assertPristine(self.observe())

    def test_fixture_nested_mutation_names_only_the_mutating_module(self):
        seen = []

        def leak(cls):
            entry = capabilities._DYNAMIC_UPSTREAM_CAPABILITIES[0]
            entry["schema"]["properties"]["q"]["type"] = "leaked"
            entry["tags"][1]["scope"].append("write")
            capabilities._UPSTREAM_READ_GATEWAY_SUMMARY["nested"]["counts"].append(3)

        leaker = self.module("hamcp206_synthetic_nested_fixture",
                             setUpClass=classmethod(leak), test_runs=lambda case: None)
        following = [self.module(f"hamcp206_synthetic_nested_{name}",
                                 test_sees=lambda case: seen.append(self.observe()))
                     for name in ("next", "later")]
        guard, result = self.run_guarded(leaker, *following)

        self.assertEqual(result.failures, [])
        self.assertEqual([str(holder) for holder, _ in result.errors],
                         ["tearDownModule (hamcp206_synthetic_nested_fixture)"])
        trace = result.errors[0][1]
        self.assertIn(f"changed {NESTED_TOOL}", trace)
        self.assertIn("keys changed: nested", trace)
        self.assertNotIn("NOT restorable", trace)
        self.assertEqual([module for module, _ in guard.leaks], ["hamcp206_synthetic_nested_fixture"])
        self.assertEqual(guard.modules_checked, 3)
        self.assertEqual(len(seen), 2)
        for observed in seen:
            self.assertPristine(observed)
        self.assertPristine(self.observe())

    def test_nested_shapes_and_held_references_are_restored_by_identity_and_value(self):
        held = self.held
        catalog = lambda: capabilities._DYNAMIC_UPSTREAM_CAPABILITIES  # noqa: E731
        mutations = {
            "dict_in_dict": lambda: catalog()[0]["schema"]["properties"]["q"].update(
                type="leaked", extra=1),
            "list_in_dict": lambda: catalog()[0]["tags"].insert(0, "write"),
            "dict_in_list": lambda: catalog()[0]["tags"][1]["scope"].clear(),
            "key_removed": lambda: catalog()[0].pop("schema"),
            "held_reference": lambda: (held["q"].__setitem__("type", "leaked"),
                                       held["counts"].append(3), held["entry"].clear()),
            "summary_nested": lambda: capabilities._UPSTREAM_READ_GATEWAY_SUMMARY[
                "nested"]["counts"].pop(),
            "equal_inner_replacement": lambda: catalog()[0].__setitem__(
                "schema", copy.deepcopy(held["schema"])),
            "equal_global_replacement": lambda: setattr(
                capabilities, "_DYNAMIC_UPSTREAM_CAPABILITIES", copy.deepcopy(held["catalog"])),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                observed = []
                module = self.module(
                    f"hamcp206_synthetic_shape_{label}",
                    test_a_mutate=_calls(mutate),
                    test_b_observe=lambda case: observed.append(self.observe()))
                guard, result = self.run_guarded(module)
                self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
                self.assertEqual((guard.tests_reset, guard.leaks), (1, []))
                self.assertPristine(observed[0])
                self.assertPristine(self.observe())

    def test_probe_restores_shared_containers_by_identity_and_value(self):
        shared, inner = {"k": [1]}, [2]
        pair = (shared, inner)
        value = {"a": shared, "b": shared, "t": pair}
        holder = types.SimpleNamespace(value=value)
        probe = process_isolation.ModuleAttributeProbe(__name__, "_PROBE_SUBJECT")
        globals()["_PROBE_SUBJECT"] = value
        self.addCleanup(globals().pop, "_PROBE_SUBJECT", None)
        saved = probe.capture()
        self.assertEqual(probe.changes(saved), [])

        # Equal values, broken aliasing: detected by identity alone.
        value["b"] = copy.deepcopy(shared)
        self.assertEqual(value, saved[1])
        self.assertEqual(probe.changes(saved), [f"{__name__}._PROBE_SUBJECT: changed"])
        probe.restore(saved)
        self.assertEqual(probe.changes(saved), [])
        self.assertIs(value["b"], shared)

        # Nested in-place changes through held references.
        shared["k"].append(9)
        inner.clear()
        value["t"] = (copy.deepcopy(shared), inner)
        self.assertTrue(probe.changes(saved))
        probe.restore(saved)
        self.assertIs(globals()["_PROBE_SUBJECT"], value)
        self.assertIs(holder.value, value)
        self.assertEqual([value[key] is shared for key in "ab"], [True, True])
        self.assertIs(value["t"], pair)
        self.assertEqual((value["t"][0] is shared, value["t"][1] is inner), (True, True))
        self.assertEqual(value, {"a": {"k": [1]}, "b": {"k": [1]}, "t": ({"k": [1]}, [2])})
        self.assertEqual(probe.changes(saved), [])

    def test_unrestorable_leak_stays_with_the_module_that_caused_it(self):
        stubborn = _Unrestorable()
        leaker = self.module(
            "hamcp206_synthetic_unrestorable",
            setUpClass=classmethod(lambda cls: setattr(stubborn, "value", 1)),
            test_runs=lambda case: None)
        innocent = self.module("hamcp206_synthetic_inherits", test_runs=lambda case: None)
        guard, result = self.run_guarded(leaker, innocent, probes=(stubborn,))
        self.assertEqual([str(holder) for holder, _ in result.errors],
                         ["tearDownModule (hamcp206_synthetic_unrestorable)"])
        self.assertIn("NOT restorable", result.errors[0][1])
        self.assertEqual([module for module, _ in guard.leaks], ["hamcp206_synthetic_unrestorable"])
        self.assertEqual(guard.modules_checked, 2)


INSTALLER = """
import sys, unittest
from support import process_isolation
process_isolation.install()

class Installer(unittest.TestCase):
    def test_installed(self):
        self.assertIsNotNone(process_isolation.process_guard())
"""
LEAKER = f"""
import os, unittest

class Leaker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ[{SYNTHETIC_ENV!r}] = {SYNTHETIC_VALUE!r}

    def test_runs(self):
        pass
"""
CLEAN = f"""
import os, unittest

class Clean(unittest.TestCase):
    def test_baseline_restored(self):
        self.assertNotIn({SYNTHETIC_ENV!r}, os.environ)
"""


class RunnerParityTests(unittest.TestCase):
    """The guard fails a leaking module in serial runs and in CI shards."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name) / "source"
        tests = cls.root / "tests"
        (tests / "support").mkdir(parents=True)
        (tests / "support" / "__init__.py").write_text("")
        for name in ("process_isolation.py", "real_time.py"):
            shutil.copyfile(ROOT / "tests/support" / name, tests / "support" / name)
        for name, body in (("test_a_installer.py", INSTALLER), ("test_b_leaker.py", LEAKER),
                           ("test_c_clean.py", CLEAN)):
            (tests / name).write_text(body.lstrip())
        cls.env = {key: value for key, value in os.environ.items()
                   if key not in (SYNTHETIC_ENV, "PYTHONPATH")}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_process(self, *args, env=None):
        return subprocess.run([sys.executable, *args], cwd=str(self.root), env=env or self.env,
                              capture_output=True, text=True, timeout=120)

    def test_serial_discover_fails_naming_the_leaking_module(self):
        result = self.run_process("-m", "unittest", "discover", "-s", "tests", "-v")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("ERROR: tearDownModule (test_b_leaker)", result.stderr)
        self.assertIn(SYNTHETIC_ENV, result.stderr)
        self.assertNotIn(SYNTHETIC_VALUE, result.stderr)
        self.assertIn("test_baseline_restored (test_c_clean.Clean", result.stderr)
        self.assertIn("Ran 3 tests", result.stderr)
        self.assertIn("FAILED (errors=1)", result.stderr)

    def test_ci_shard_reports_an_unattributed_fixture_error(self):
        root = Path(self.temp.name) / "ci-source"
        shutil.copytree(self.root, root)
        (root / "ci").mkdir()
        (root / "ci/unit-test-shards.json").write_text(json.dumps({
            "schema_version": 1, "general_shards": 1, "default_module_seconds": 1,
            "module_seconds": {}}))
        for args in (("init", "-q"), ("config", "user.name", "Synthetic test"),
                     ("config", "user.email", "synthetic@example.invalid"), ("add", "."),
                     ("commit", "-qm", "Synthetic source")):
            subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
        env = {**self.env, "GITHUB_RUN_ID": "1001", "GITHUB_RUN_ATTEMPT": "1"}
        manifests = Path(self.temp.name) / "manifests"
        outcomes = []
        for shard in range(2):
            (manifests / f"shard-{shard}").mkdir(parents=True)
            output = manifests / f"shard-{shard}" / "manifest.json"
            run = subprocess.run(
                [sys.executable, str(SHARD_SCRIPT), "run", "--shard", str(shard),
                 "--output", str(output), "--repo-root", str(root)],
                cwd=str(root), env=env, capture_output=True, text=True, timeout=120)
            outcomes.append((run.returncode, run.stderr))
        leaking = json.loads((manifests / "shard-0" / "manifest.json").read_text())
        self.assertNotEqual(outcomes[0][0], 0, outcomes[0][1])
        self.assertIn("tearDownModule (test_b_leaker)", outcomes[0][1])
        self.assertGreaterEqual(leaking["unattributed_errors"], 1)
        aggregate = subprocess.run(
            [sys.executable, str(SHARD_SCRIPT), "aggregate", "--manifests", str(manifests),
             "--repo-root", str(root)],
            cwd=str(root), env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(aggregate.returncode, 1, aggregate.stdout + aggregate.stderr)
        self.assertIn("refused: shard_fixture_error", aggregate.stderr)


if __name__ == "__main__":
    unittest.main()
