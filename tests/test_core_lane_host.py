"""Offline checks of the shared local lane host and the explicit lane mode (HAMCP-443 D2/D5).

No Docker, network, Home Assistant or production key. Every acceptance ``run()``
takes ``mode`` as a required keyword: ``"ci"`` refuses until 443-B, anything but
``"local"`` refuses, both before any argument is touched, and nothing in the
environment chooses or changes the outcome. The seven local harnesses take
their configuration and fixture paths from ``core_lane_host``, delegate the
shared host steps to it, and keep their closed-lane refusal.
"""
import ast
import asyncio
import inspect
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "scripts")]

import core_created_object_local as created_object  # noqa: E402
import core_device_configuration_local as device  # noqa: E402
import core_helper_value_local as helper  # noqa: E402
import core_lane_host as lane_host  # noqa: E402
import core_marketplace_read_local as marketplace  # noqa: E402
import core_security_metadata_local as security  # noqa: E402
import core_thread_matter_topology_a2_local as a2  # noqa: E402
import core_thread_matter_topology_local as a1  # noqa: E402
import created_object_contract_acceptance  # noqa: E402
import device_configuration_contract_acceptance  # noqa: E402
import helper_value_contract_acceptance  # noqa: E402
import marketplace_read_contract_acceptance  # noqa: E402
import security_metadata_contract_acceptance  # noqa: E402
import thread_matter_topology_a2_acceptance  # noqa: E402
import thread_matter_topology_contract_acceptance  # noqa: E402
from core_release_evidence import read_json  # noqa: E402

# Each acceptance module and the keywords its run() takes besides mode.
ACCEPTANCE = {
    helper_value_contract_acceptance: (),
    marketplace_read_contract_acceptance: (),
    security_metadata_contract_acceptance: (),
    thread_matter_topology_contract_acceptance: (),
    thread_matter_topology_a2_acceptance: ("expectations", "read_stub_record", "read_devices", "stub_control",
                                           "stub_state", "reload_matter"),
    created_object_contract_acceptance: (),
    device_configuration_contract_acceptance: ("stub_url", "stub_hashes", "upstream_version"),
}
SCENE = "scene_create_delete"
# The harnesses that own a host, keyed by their configuration in core_lane_host.CONFIGS.
HOSTED = {"helper_value": helper, "marketplace_read": marketplace, "security_metadata": security,
          "thread_matter_topology": a1, "device_configuration_entity": device, "created_object": created_object}
HARNESSES = (*HOSTED.values(), a2)
MARKERS = ("GITHUB_ACTIONS", "CORE_EVIDENCE_CONTEXT", "CORE_EVIDENCE_HOST_MODE")
ENVIRONMENTS = ({"CORE_EVIDENCE_CONTEXT": "1"}, {"CORE_EVIDENCE_CONTEXT": "ci"}, {"CORE_EVIDENCE_CONTEXT": "local"},
                {"CORE_EVIDENCE_CONTEXT": '{"mode": "ci"}'}, {"GITHUB_ACTIONS": "true"},
                {"GITHUB_ACTIONS": "true", "CORE_EVIDENCE_CONTEXT": "ci"}, {"CORE_EVIDENCE_HOST_MODE": "ci"})
UNKNOWN_MODES = ("LOCAL", "Local", " local", "local ", "CI", "ci ", "", "release", "main", None, b"local", 1, True)
HOST_FUNCTIONS = {name for name in vars(lane_host.Lane) if not name.startswith("_")} | {
    "utc", "sha256", "local_failure_logs"}


class PastGuard(Exception):
    pass


class Untouchable:
    """Any use raises PastGuard, so a refusal raised instead proves nothing was touched first."""

    def _used(self, *_args):
        raise PastGuard("argument used")

    __getattr__ = __getitem__ = __iter__ = __len__ = __bool__ = __hash__ = __fspath__ = _used
    __truediv__ = __rtruediv__ = __contains__ = __call__ = _used


def unmarked(**extra):
    return {**{k: v for k, v in os.environ.items() if k not in MARKERS}, **extra}


def invoke(module, lane=None, **mode):
    """Await one acceptance run() whose every other argument refuses any use."""
    head = (Untouchable() if lane is None else lane,) if module is created_object_contract_acceptance else ()
    extras = {name: Untouchable() for name in ACCEPTANCE[module]}

    async def awaited():
        return await module.run(*head, Untouchable(), Untouchable(), Untouchable(), **mode, **extras)
    return asyncio.run(awaited())


def outcome(module, mode):
    try:
        invoke(module, lane=SCENE if mode == "local" else None, mode=mode)
    except BaseException as exc:  # noqa: BLE001 - the outcome is the exception itself
        return type(exc), str(exc)
    return None, None


def tree(module):
    return ast.parse(Path(module.__file__).read_text())


class ModeIsRequired(unittest.TestCase):
    def test_mode_is_a_required_keyword_on_every_run(self):
        for module in ACCEPTANCE:
            with self.subTest(module=module.__name__):
                parameter = inspect.signature(module.run).parameters["mode"]
                self.assertIs(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
                self.assertIs(parameter.default, inspect.Parameter.empty)

    def test_a_run_without_mode_refuses(self):
        for module in ACCEPTANCE:
            with self.subTest(module=module.__name__), self.assertRaises(TypeError) as caught:
                invoke(module)
            self.assertIn("'mode'", str(caught.exception))

    def test_ci_refuses_until_443_b_before_touching_anything(self):
        for module in ACCEPTANCE:
            with self.subTest(module=module.__name__), self.assertRaises(NotImplementedError) as caught:
                invoke(module, mode="ci")
            self.assertEqual(str(caught.exception), "ci mode lands in 443-B")

    def test_an_unknown_mode_refuses_before_touching_anything(self):
        for module in ACCEPTANCE:
            for mode in UNKNOWN_MODES:
                with self.subTest(module=module.__name__, mode=mode), self.assertRaises(ValueError) as caught:
                    invoke(module, mode=mode)
                self.assertEqual(str(caught.exception), "lane_mode_unknown")

    def test_local_runs_past_the_guard_into_the_lane(self):
        self.assertIn(SCENE, created_object_contract_acceptance.LANES)
        for module in ACCEPTANCE:
            with self.subTest(module=module.__name__), self.assertRaises(PastGuard):
                invoke(module, lane=SCENE, mode="local")

    def test_every_harness_names_local(self):
        for module in HARNESSES:
            with self.subTest(module=module.__name__):
                # The acceptance run(), imported by name or called through its module.
                calls = [node for node in ast.walk(tree(module)) if isinstance(node, ast.Call) and (
                    isinstance(node.func, ast.Name) and node.func.id == "run"
                    or isinstance(node.func, ast.Attribute) and node.func.attr == "run"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "acceptance")]
                self.assertTrue(calls)
                for node in calls:
                    modes = [k.value for k in node.keywords if k.arg == "mode"]
                    self.assertEqual(len(modes), 1)
                    self.assertIsInstance(modes[0], ast.Constant)
                    self.assertEqual(modes[0].value, "local")


class EnvironmentDoesNotChooseTheMode(unittest.TestCase):
    def test_the_environment_never_changes_a_run_outcome(self):
        for module in ACCEPTANCE:
            for mode in ("local", "ci", "release"):
                with mock.patch.dict(os.environ, unmarked(), clear=True):
                    expected = outcome(module, mode)
                self.assertIsNotNone(expected[0])
                for environment in ENVIRONMENTS:
                    with self.subTest(module=module.__name__, mode=mode, environment=environment), \
                            mock.patch.dict(os.environ, unmarked(**environment), clear=True):
                        self.assertEqual(outcome(module, mode), expected)

    def test_no_acceptance_module_reads_the_environment(self):
        for module in ACCEPTANCE:
            with self.subTest(module=module.__name__):
                for node in ast.walk(tree(module)):
                    self.assertFalse(isinstance(node, ast.Attribute) and node.attr in {"environ", "environb", "getenv"})
                    self.assertFalse(isinstance(node, ast.Name) and node.id in {"environ", "environb", "getenv"})
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        for marker in MARKERS:
                            self.assertNotIn(marker, node.value)

    def test_the_host_passes_the_environment_on_only_for_removal(self):
        nodes = list(ast.walk(tree(lane_host)))
        passed = {id(arg) for node in nodes if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute) and node.func.attr == "local_remove" for arg in node.args}
        uses = [node for node in nodes if isinstance(node, ast.Attribute) and node.attr == "environ"]
        self.assertEqual(len(uses), 1)
        self.assertIn(id(uses[0]), passed)
        for node in nodes:
            self.assertFalse(isinstance(node, (ast.Attribute, ast.Name))
                             and getattr(node, "attr", getattr(node, "id", None)) in {"environb", "getenv"})
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for marker in MARKERS:
                    self.assertNotIn(marker, node.value)


class ClosedLaneKept(unittest.TestCase):
    def test_every_harness_refuses_ci_or_evidence_before_any_command(self):
        run_dir = ROOT / ".artifacts/never-created-hamcp443"
        reached = AssertionError("reached past the closed-lane guard")
        for module in HARNESSES:
            argv = ["--mode", "local", "--pull", "never", "--journal", "j", "--source-archive", "a",
                    "--run-dir", str(run_dir)]
            if module is created_object:
                argv += ["--lane", SCENE]
            for environment in ({"GITHUB_ACTIONS": "true"}, {"CORE_EVIDENCE_CONTEXT": "1"}):
                with self.subTest(module=module.__name__, environment=environment), \
                        mock.patch.dict(os.environ, unmarked(**environment), clear=True), \
                        mock.patch.object(module, "command", side_effect=reached) as command, \
                        mock.patch.object(a1, "command", side_effect=reached), \
                        mock.patch.object(a1, "inputs", side_effect=reached), \
                        mock.patch.object(a1, "images", side_effect=reached), \
                        mock.patch.object(lane_host.subprocess, "run", side_effect=reached) as process, \
                        mock.patch.object(lane_host.resources, "wait_for_admission", side_effect=reached), \
                        mock.patch.object(lane_host.diagnostics, "_raw_log", return_value=False):
                    with self.assertRaises(module.Refusal) as caught:
                        module.main(argv)
                    self.assertEqual(str(caught.exception), "local_only_closed_lane")
                    command.assert_not_called()
                    process.assert_not_called()
                    self.assertFalse(run_dir.exists())


class OneSourceOfTruth(unittest.TestCase):
    def test_each_harness_takes_its_configuration_from_the_host(self):
        self.assertEqual(set(lane_host.CONFIGS), set(HOSTED))
        for key, module in HOSTED.items():
            with self.subTest(module=module.__name__):
                self.assertIs(module.CONFIG, lane_host.CONFIGS[key])
                self.assertIn("default_config:", module.CONFIG)
        self.assertIs(created_object.INCLUDED, lane_host.INCLUDES["created_object"])

    def test_fixture_paths_come_from_the_host(self):
        self.assertIs(device.STUB_ASSET, lane_host.MATTER_STUB)
        self.assertIs(device.LANE_ASSET, lane_host.DEVICE_LANE)
        self.assertIs(device.CORE_PROVENANCE, lane_host.LANE_PROVENANCE)
        for path in (lane_host.MATTER_STUB, lane_host.DEVICE_LANE, lane_host.LANE_PROVENANCE):
            self.assertTrue(path.exists(), path)
        # A2's stub directory is bound by the hash-pinned expectations; it must be the host's one stub.
        stub = read_json(a2.EXPECTATIONS)["stub"]
        self.assertEqual((ROOT / stub["committed_dir"]).absolute(), lane_host.MATTER_STUB)

    def test_a2_reuses_the_a1_configuration(self):
        nodes = list(ast.walk(tree(a2)))
        self.assertFalse([n for n in nodes if isinstance(n, ast.Name) and n.id == "CONFIG"
                          and isinstance(n.ctx, ast.Store)])
        self.assertTrue([n for n in nodes if isinstance(n, ast.Attribute) and n.attr == "CONFIG"
                         and isinstance(n.value, ast.Name) and n.value.id == "a1"])
        for name in ("command", "inspect_owned", "sha256", "utc", "Refusal"):
            self.assertIs(getattr(a2, name), getattr(a1, name))

    def test_no_harness_copies_a_host_literal(self):
        fixtures = {path.relative_to(ROOT).as_posix()
                    for path in (lane_host.MATTER_STUB, lane_host.DEVICE_LANE, lane_host.LANE_PROVENANCE)}
        for module in HARNESSES:
            with self.subTest(module=module.__name__):
                for node in ast.walk(tree(module)):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        self.assertNotIn("default_config:", node.value)
                        self.assertNotIn("tests/fixtures/", node.value)
                        self.assertNotIn(node.value, {*lane_host.CONFIGS.values(), *fixtures})

    def test_no_harness_redefines_a_host_function(self):
        for module in HARNESSES:
            for node in tree(module).body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in HOST_FUNCTIONS:
                    continue
                with self.subTest(module=module.__name__, function=node.name):
                    # Only a one-line delegation to the module's own host is allowed.
                    body = [s for s in node.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
                    self.assertEqual(len(body), 1)
                    self.assertIsInstance(body[0], ast.Return)
                    call = body[0].value
                    self.assertIsInstance(call, ast.Call)
                    self.assertIsInstance(call.func, ast.Attribute)
                    self.assertEqual((call.func.value.id, call.func.attr), ("LANE_HOST", node.name))

    def test_harness_aliases_are_bound_to_their_own_host(self):
        for module in HOSTED.values():
            with self.subTest(module=module.__name__):
                self.assertIs(module.LANE_HOST.module, module)
                self.assertIs(module.utc, lane_host.utc)
                self.assertIs(module.sha256, lane_host.sha256)
                names = ["command", "cached_image", "require_free_ports", "inspect_owned"]
                if module is not created_object:
                    names.append("verify_port_mapping")
                for name in names:
                    alias = getattr(module, name)
                    self.assertIs(alias.__self__, module.LANE_HOST)
                    self.assertIs(alias.__func__, getattr(lane_host.Lane, name))


class LaneHost(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.directory)

    def test_the_host_resolves_harness_names_when_called(self):
        def dirty(*args, **_kwargs):
            return mock.Mock(stdout=b" M scripts/x.py\n" if "status" in args else b"f" * 40 + b"\n")

        def clean(*args, **_kwargs):
            return mock.Mock(stdout=b"" if "status" in args else b"e" * 40 + b"\n")
        with mock.patch.object(helper, "command", dirty), mock.patch.object(marketplace, "command", clean):
            with self.assertRaises(helper.Refusal) as caught:
                helper.LANE_HOST.clean_revision()
            self.assertEqual(str(caught.exception), "clean_committed_checkout_required")
            # Each harness's host sees only its own module: the marketplace host is unaffected.
            self.assertEqual(marketplace.LANE_HOST.clean_revision(), "e" * 40)

    def test_claim_takes_only_a_new_directory_under_artifacts(self):
        artifacts = self.directory / ".artifacts"
        artifacts.mkdir()
        (self.directory / "outside").mkdir()
        (artifacts / "link").symlink_to(self.directory / "outside")
        with mock.patch.object(helper, "ROOT", self.directory):
            claimed = helper.LANE_HOST.claim(artifacts / "run")
            self.assertEqual(claimed, artifacts / "run")
            self.assertEqual(stat.S_IMODE(claimed.stat().st_mode), 0o700)
            with self.assertRaises(FileExistsError):
                helper.LANE_HOST.claim(artifacts / "run")
            for path in (self.directory / "outside" / "run", artifacts, artifacts / "link" / "run",
                         self.directory / ".artifacts-other" / "run"):
                with self.subTest(path=path), self.assertRaises(helper.Refusal) as caught:
                    helper.LANE_HOST.claim(path)
                self.assertEqual(str(caught.exception), "new_task_artifact_directory_required")
        self.assertEqual(list((self.directory / "outside").iterdir()), [])

    def test_configuration_writes_the_config_empty_includes_and_clean_components(self):
        component = self.directory / "component" / "fixture_integration"
        (component / "__pycache__").mkdir(parents=True)
        (component / "__init__.py").write_text("DOMAIN = 'fixture_integration'\n")
        (component / "__pycache__" / "__init__.cpython-312.pyc").write_bytes(b"stale")
        run_dir = self.directory / "run"
        run_dir.mkdir()
        owned = created_object.LANE_HOST.configuration(run_dir, "core", lane_host.CONFIGS["created_object"],
                                                       includes=lane_host.INCLUDES["created_object"],
                                                       components=(component,))
        self.assertEqual(owned, run_dir / "core")
        self.assertEqual(stat.S_IMODE(owned.stat().st_mode), 0o700)
        self.assertEqual((owned / "configuration.yaml").read_text(), lane_host.CONFIGS["created_object"])
        for include in lane_host.INCLUDES["created_object"]:
            self.assertEqual((owned / include).read_text(), "")
        copied = owned / "custom_components" / "fixture_integration"
        self.assertEqual(sorted(p.name for p in copied.iterdir()), ["__init__.py"])
        with self.assertRaises(FileExistsError):
            created_object.LANE_HOST.configuration(run_dir, "core", "default_config:\n")


if __name__ == "__main__":
    unittest.main()
