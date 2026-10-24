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
for path in (ROOT, ROOT / "tests", ROOT / "hass_mcp_engineering_beta"):
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



CORE_CAPABILITY = ("core.basic_rest_read",)
CORE_RUNTIME_MODULE = "hamcp219_synthetic_core_runtime"


def _boot_core(runtime):
    """Configure ``runtime`` and boot it once, as ToolRefusalTests.setUpClass does."""

    import asyncio
    from tests import test_ha_core_2026_9_integration as core_tests

    runtime.configure(object(), source=core_tests._MutableSource(core_tests._core_2026_9_snapshot()))
    asyncio.run(runtime.reconcile_once("startup"))
    return runtime.acquire(CORE_CAPABILITY) is not None


class CoreBootLatchTests(GuardHarness, unittest.TestCase):
    """HAMCP-219: the Core runtime's one-shot boot exception is guarded by name.

    ``CORE_READMISSION`` admits once per process without the restoration gate.
    A class that boots it spends that exception, and every later configure()
    then inherits the gate, so a later class that boots Core once is refused:
    ``tests.test_beta_v2.BetaApplicationTests`` followed by
    ``tests.test_hamcp200_path_safety.ToolRefusalTests`` failed 106 subtests
    with CoreCapabilityUnavailable. The guard's probe for the real runtime is
    exercised here on a separate ``CoreRuntime`` so the process-wide one is
    never configured with a synthetic source.
    """

    def setUp(self):
        super().setUp()
        from ha_mcp_engineering.ha_core_readmission import CoreRuntime

        self.names.append(CORE_RUNTIME_MODULE)
        self.runtime_module = types.ModuleType(CORE_RUNTIME_MODULE)
        self.runtime_module.RUNTIME = self.runtime = CoreRuntime()
        sys.modules[CORE_RUNTIME_MODULE] = self.runtime_module
        self.probe = process_isolation.CarriedStateProbe(
            CORE_RUNTIME_MODULE, "RUNTIME", process_isolation.CORE_LATCH,
            process_isolation.CORE_CARRIED)

    def spender_and_consumer(self, prefix):
        runtime, admitted = self.runtime, []
        spender = self.module(
            f"{prefix}_spender",
            setUpClass=classmethod(lambda cls: _boot_core(runtime)),
            test_runs=lambda case: None)
        consumer = self.module(
            f"{prefix}_consumer",
            test_boots=lambda case: admitted.append(_boot_core(runtime)))
        return spender, consumer, admitted

    def test_spent_boot_exception_fails_the_module_and_the_next_module_boots(self):
        spender, consumer, admitted = self.spender_and_consumer("hamcp219_synthetic_core")
        guard, result = self.run_guarded(spender, consumer, probes=(self.probe,))
        self.assertEqual(result.failures, [])
        self.assertEqual([str(holder) for holder, _ in result.errors],
                         ["tearDownModule (hamcp219_synthetic_core_spender)"])
        trace = result.errors[0][1]
        self.assertIn("ProcessStateLeak", trace)
        self.assertIn(f"{CORE_RUNTIME_MODULE}.RUNTIME: _boot_exception_spent False -> True", trace)
        self.assertIn("carried into the next configure(): _admitted", trace)
        self.assertEqual([module for module, _ in guard.leaks], ["hamcp219_synthetic_core_spender"])
        self.assertEqual(admitted, [True])

    def test_mutation_without_the_core_probe_reproduces_core_capability_unavailable(self):
        spender, consumer, admitted = self.spender_and_consumer("hamcp219_synthetic_unprobed")
        guard, result = self.run_guarded(spender, consumer, probes=())
        self.assertTrue(result.wasSuccessful(), result.errors)
        self.assertEqual(guard.leaks, [])
        # The consumer's single boot is refused: the router answers that as
        # CoreCapabilityUnavailable (routing.py ``acquire`` returned None).
        self.assertEqual(admitted, [False])
        self.assertTrue(self.runtime._boot_exception_spent)

    def test_spend_inside_a_test_is_reset_before_the_next_test(self):
        runtime, admitted = self.runtime, []
        module = self.module(
            "hamcp219_synthetic_core_body",
            test_a_spends=lambda case: admitted.append(_boot_core(runtime)),
            test_b_boots=lambda case: admitted.append(_boot_core(runtime)))
        guard, result = self.run_guarded(module, probes=(self.probe,))
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        # Each boot spends the exception; each is reset before the next test.
        self.assertEqual(admitted, [True, True])
        self.assertEqual((guard.tests_reset, guard.leaks), (2, []))

    def test_unspent_state_that_configure_resets_is_not_reported(self):
        saved = self.probe.capture()
        self.runtime._admitted, self.runtime._flap_count = True, 3
        self.assertEqual(self.probe.changes(saved), [])
        self.runtime._boot_exception_spent = True
        spent = self.probe.capture()
        self.runtime._flap_count = 4
        self.assertEqual(self.probe.changes(spent), [
            f"{CORE_RUNTIME_MODULE}.RUNTIME: _boot_exception_spent True -> True; "
            "carried into the next configure(): _flap_count"])
        self.probe.restore(spent)
        self.assertEqual(self.runtime._flap_count, 3)
        self.probe.restore(saved)
        self.assertIs(self.runtime._boot_exception_spent, False)

    def test_probe_guards_the_real_runtime_with_exactly_what_configure_carries(self):
        import ast
        import inspect
        from ha_mcp_engineering.ha_core_readmission import runtime as core_runtime

        probes = [probe for probe in process_isolation.PROBES
                  if isinstance(probe, process_isolation.CarriedStateProbe)]
        self.assertEqual([(probe.module, probe.owner, probe.latch, probe.carried) for probe in probes],
                         [(core_runtime.__name__, "CORE_READMISSION", "_boot_exception_spent",
                           process_isolation.CORE_CARRIED)])
        self.assertIsInstance(core_runtime.CORE_READMISSION, core_runtime.CoreRuntime)
        configure = ast.parse(inspect.getsource(core_runtime.CoreRuntime.configure).strip())
        carried, latch_tests, admitted_reads = None, [], []
        for node in ast.walk(configure):
            if isinstance(node, ast.Assign) and [getattr(t, "id", None) for t in node.targets] == ["carried"]:
                carried = tuple(item.attr for item in node.value.elts)
            if isinstance(node, ast.If) and isinstance(node.test, ast.Attribute):
                latch_tests.append(node.test.attr)
            if (isinstance(node, ast.Assign) and [getattr(t, "id", None) for t in node.targets]
                    == ["was_admitted"]):
                admitted_reads.append(node.value.attr)
        self.assertIn("_boot_exception_spent", latch_tests)
        self.assertEqual(admitted_reads, ["_admitted"])
        self.assertEqual(("_admitted", *carried), process_isolation.CORE_CARRIED)


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
# HAMCP-219: imports the guard but never calls install() itself.
CONSUMER = f"""
import os, unittest
from support import process_isolation

class Consumer(unittest.TestCase):
    def test_baseline_restored(self):
        self.assertNotIn({SYNTHETIC_ENV!r}, os.environ)
"""
INSTALL_ON_IMPORT = "\ninstall()\n"
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

    def named_run_root(self, name, *, install_on_import=True):
        """A copy of the synthetic tree plus a module that only imports the guard."""
        root = Path(self.temp.name) / name
        shutil.copytree(self.root, root)
        (root / "tests/test_d_consumer.py").write_text(CONSUMER.lstrip())
        guard = root / "tests/support/process_isolation.py"
        source = guard.read_text()
        self.assertTrue(source.endswith(INSTALL_ON_IMPORT))
        if not install_on_import:
            guard.write_text(source[:-len(INSTALL_ON_IMPORT)] + "\n")
        return root

    def run_named(self, root):
        return subprocess.run([sys.executable, "-m", "unittest", "-v", "test_b_leaker", "test_d_consumer"],
                              cwd=str(root / "tests"), env=self.env, capture_output=True, text=True,
                              timeout=120)

    def test_named_module_run_is_guarded_by_importing_the_guard(self):
        # HAMCP-219: test_beta_v2 after test_hamcp131/132 run by name had no guard.
        result = self.run_named(self.named_run_root("named"))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("ERROR: tearDownModule (test_b_leaker)", result.stderr)
        self.assertIn(SYNTHETIC_ENV, result.stderr)
        self.assertNotIn(SYNTHETIC_VALUE, result.stderr)
        self.assertIn("test_baseline_restored (test_d_consumer.Consumer.test_baseline_restored) ... ok",
                      result.stderr)
        self.assertIn("FAILED (errors=1)", result.stderr)

    def test_mutation_without_install_on_import_leaks_into_the_named_consumer(self):
        result = self.run_named(self.named_run_root("named-mutant", install_on_import=False))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("tearDownModule", result.stderr)
        self.assertIn("FAIL: test_baseline_restored (test_d_consumer.Consumer", result.stderr)
        self.assertIn("FAILED (failures=1)", result.stderr)


class RecordedOrderRegressionTests(unittest.TestCase):
    """HAMCP-219: the recorded minimal failing orders for both leaks pass.

    Core latch: before the Core probe, ``BetaApplicationTests`` spent the
    process-wide boot exception and ``ToolRefusalTests`` (which boots Core
    once) then failed 106 subtests with CoreCapabilityUnavailable.

    Session manager: in an unguarded named run, ``ProductionCompositionTests``
    left ``_SERVER._session_manager`` built; the first ``ToolRefusalTests``
    started it and ``restore_after_class`` restored the same (now started)
    object by reference, so the next lifespan raised "StreamableHTTPSessionManager
    .run() can only be called once" in setUpClass. Installing the guard on
    import resets the attribute after the leaking test.
    """

    PAIR = ("tests.test_beta_v2.BetaApplicationTests", "tests.test_hamcp200_path_safety.ToolRefusalTests")
    SESSION_MANAGER_ORDER = (
        "tests.test_hamcp164_core_gate_fail_closed.ProductionCompositionTests",
        "tests.test_hamcp200_path_safety.ToolRefusalTests",
        "tests.test_hamcp200_path_safety.SourceScanTests",
        "tests.test_hamcp200_path_safety.ToolRefusalTests",
    )

    def run_order(self, names):
        env = {key: value for key, value in os.environ.items() if key != SYNTHETIC_ENV}
        env["PYTHONPATH"] = os.pathsep.join(str(path) for path in (ROOT / "tests", ROOT / "hass_mcp_engineering_beta"))
        result = subprocess.run([sys.executable, "-m", "unittest", *names], cwd=str(ROOT), env=env,
                                capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])
        self.assertNotIn("ProcessStateLeak", result.stderr)
        self.assertRegex(result.stderr, r"\nOK\n")
        return result

    def test_session_manager_order_passes_in_one_process(self):
        result = self.run_order(self.SESSION_MANAGER_ORDER)
        self.assertNotIn("can only be called once", result.stderr)

    def test_core_latch_pair_passes_in_one_process(self):
        result = self.run_order(self.PAIR)
        self.assertNotIn("CoreCapabilityUnavailable", result.stderr)


if __name__ == "__main__":
    unittest.main()
