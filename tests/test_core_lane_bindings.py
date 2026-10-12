"""Executable controls for the one reviewed Core binding list (HAMCP-159).

Contract amendment (Josh, 2026-10-11): rotation among Cores already in the
support window edits only ``ci/support-window.yaml``; a brand-new Core first
needs its exact binding in ``scripts/core_lane_bindings.py``; a window Core
without a reviewed binding is refused at window validation, before any matrix;
and no matrix row may advertise Alarmo or baseline coverage that the executable
path would not run. These tests drive synthetic windows through window
validation, matrix derivation, the disposable lane helpers, the Alarmo and
automation-baseline acceptance entry points (up to their first Core contact)
and the real-HA device-contract dispatcher. Offline; no Core is started.
"""

from __future__ import annotations

import ast
from contextlib import contextmanager
import dataclasses
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
import core_lane_bindings  # noqa: E402
import support_window  # noqa: E402
import core_registry_contract_lane as lane  # noqa: E402
import alarmo_inspection_contract_acceptance as alarmo  # noqa: E402
import automation_baseline_contract_acceptance as baseline  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.probe_profiles import (  # noqa: E402
    CHILD_DEVICE_NAME_PARTS_PROBE_PROFILE, CHILD_DEVICE_PROBE_PROFILE,
)

CONTRACT_PATH = ROOT / "scripts" / "real_ha_contract_tests.py"
_spec = importlib.util.spec_from_file_location("_core_lane_bindings_contract_subject", CONTRACT_PATH)
contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contract)

COMMITTED = support_window.load(ROOT)
NEW_VERSION = "2026.10.2"
NEW_DIGEST = "sha256:" + "c" * 64
# Synthetic, offline, same exact shape as a committed lane provenance fixture.
NEW_PROVENANCE = {"entry_id": "disposable-core-2026.10.2-cccccccc", "server_name": "home-assistant-core",
                  "version": NEW_VERSION, "source_commit": "d" * 40, "source_tree": "f" * 40,
                  "source_archive_sha256": "e" * 64, "image_index_digest": NEW_DIGEST,
                  "architecture_manifests": {"linux/amd64": "sha256:" + "1" * 64,
                                             "linux/arm64": "sha256:" + "2" * 64}}
NEW_BINDING = core_lane_bindings.CoreBinding(NEW_DIGEST, core_lane_bindings.CHILD_DEVICE_NAME_PARTS, True, "8.5.0")


class Boundary(Exception):
    """Raised at the first Core contact: every binding check before it passed."""


def pin(version):
    return {"version": version,
            "image_index_digest": core_lane_bindings.reviewed_lane_binding(version).image_index_digest}


# (a) Rotation among reviewed Cores: 2026.10.1 deployed, 2026.10.0 rolled back,
# no candidate under evaluation. Only the window changes.
ROTATED = {"current": pin("2026.10.1"), "candidate": None, "rollback": pin("2026.10.0")}
# Finding 1: the committed window with no Core candidate.
NULL_CANDIDATE = {"current": COMMITTED["core"]["current"], "candidate": None,
                  "rollback": COMMITTED["core"]["rollback"]}
# (b)/(c): a brand-new Core as candidate.
WITH_NEW = {"current": pin("2026.10.0"), "candidate": {"version": NEW_VERSION, "image_index_digest": NEW_DIGEST},
            "rollback": pin("2026.9.4")}


@contextmanager
def window(core):
    """Point every consumer at a disposable root holding ``core`` as the window."""

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "ci").mkdir()
        (root / "tests" / "fixtures").mkdir(parents=True)
        value = yaml.safe_load((ROOT / "ci" / "support-window.yaml").read_text(encoding="utf-8"))
        value["core"] = core
        (root / "ci" / "support-window.yaml").write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
        for fixture in (ROOT / "tests" / "fixtures").glob("core_*_lane_provenance.json"):
            shutil.copy2(fixture, root / "tests" / "fixtures" / fixture.name)
        (root / "tests" / "fixtures" / core_lane_bindings.provenance_name(NEW_VERSION)).write_text(
            json.dumps(NEW_PROVENANCE), encoding="utf-8")
        with patch.object(support_window, "ROOT", root), patch.object(lane, "ROOT", root):
            yield root


def dispatcher_statements():
    """The exact device-contract dispatch statements inside ``run_contracts``."""

    tree = ast.parse(CONTRACT_PATH.read_text(encoding="utf-8"))
    runner = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_contracts")
    found = []
    for node in ast.walk(runner):
        for field in ("body", "orelse", "finalbody"):
            statements = getattr(node, field, None)
            if not isinstance(statements, list):
                continue
            for index, statement in enumerate(statements):
                if (isinstance(statement, ast.Assign)
                        and ast.unparse(statement.value) == "_device_contract_phase(EXPECTED_HA_VERSION)"):
                    found.append(statements[index:index + 2])
    if len(found) != 1:
        raise AssertionError("expected exactly one device-contract dispatch")
    return found[0]


async def dispatch(version):
    """Execute the runner's own dispatch statements for ``version``."""

    statements = dispatcher_statements()
    function = ast.AsyncFunctionDef(
        name="dispatch", args=ast.arguments(posonlyargs=[], args=[ast.arg("rest"), ast.arg("websocket"),
                                                                  ast.arg("token")],
                                            kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=[*statements, ast.Return(ast.Name("phase", ast.Load()))], decorator_list=[], returns=None,
        type_params=[])
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    child, migration = AsyncMock(), AsyncMock()
    namespace = {"EXPECTED_HA_VERSION": version, "_device_contract_phase": contract._device_contract_phase,
                 "CHILD_CONTRACT_PHASE": contract.CHILD_CONTRACT_PHASE,
                 "_run_core_2026_9_child_contract": child, "_run_device_migration_contract": migration}
    exec(compile(module, str(CONTRACT_PATH), "exec"), namespace)
    try:
        phase = await namespace["dispatch"](object(), object(), "synthetic-token")
    finally:
        dispatch.calls = (child.await_count, migration.await_count)
    return phase


class ConsolidatedListTests(unittest.TestCase):
    def test_consumers_hold_no_local_core_version_list(self):
        for name in ("support_window.py", "real_ha_contract_tests.py", "core_registry_contract_lane.py",
                     "alarmo_inspection_contract_acceptance.py", "automation_baseline_contract_acceptance.py"):
            source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            literals = [node.value for node in ast.walk(ast.parse(source))
                        if isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and node.value in core_lane_bindings.REVIEWED_CORE_BINDINGS]
            with self.subTest(script=name):
                # The lane helper keeps only its historical 2026.9.2 keyword default.
                self.assertEqual(literals, ["2026.9.2"] if name == "core_registry_contract_lane.py" else [])
        for retired in ("CORE_2026_9_VERSIONS", "CORE_2026_10_VERSIONS", "CORE_CHILD_CONTRACT_VERSIONS",
                        "CORE_TEST_AUTHORITY_VERSIONS", "CORE_UPSTREAM_850_VERSIONS"):
            self.assertFalse(hasattr(contract, retired), retired)
        for retired in ("CORE_FIXTURES", "CORE_NAME_PARTS_VERSIONS", "CORE_TYPED_VERSIONS"):
            self.assertFalse(hasattr(lane, retired), retired)
        self.assertIs(support_window.core_lane_bindings, core_lane_bindings)
        self.assertIs(contract._core_lane_bindings(), core_lane_bindings)

    def test_every_lane_binding_matches_its_committed_provenance(self):
        for version, binding in core_lane_bindings.REVIEWED_CORE_BINDINGS.items():
            fixture = ROOT / "tests" / "fixtures" / core_lane_bindings.provenance_name(version)
            with self.subTest(version=version):
                self.assertIs(core_lane_bindings.reviewed_core_binding(version), binding)
                if not binding.lane:
                    self.assertIsNone(core_lane_bindings.reviewed_lane_binding(version))
                    self.assertFalse(fixture.exists())
                    continue
                entry = json.loads(fixture.read_text(encoding="utf-8"))
                self.assertEqual((entry["version"], entry["image_index_digest"]),
                                 (version, binding.image_index_digest))
                self.assertEqual(lane.lane_entry(version), entry)

    def test_lookup_is_exact_and_malformed_bindings_fail_closed(self):
        for value in ("2026.10", "2026.10.*", "2026.10.1 ", " 2026.10.1", "v2026.10.1", "2026.10.01", "",
                      None, 2026.10, b"2026.10.1", ["2026.10.1"]):
            with self.subTest(value=value):
                self.assertIsNone(core_lane_bindings.reviewed_core_binding(value))
                self.assertIsNone(core_lane_bindings.reviewed_lane_binding(value))
        for field, value in (("device_registry", "child_devices_v3"), ("child_consumer", "8.6.0"),
                             ("typed_operations", 1)):
            malformed = dataclasses.replace(NEW_BINDING, **{field: value})
            with self.subTest(field=field), patch.dict(core_lane_bindings.REVIEWED_CORE_BINDINGS,
                                                       {NEW_VERSION: malformed}):
                with self.assertRaisesRegex(ValueError, "^malformed reviewed Core binding$"):
                    core_lane_bindings.reviewed_core_binding(NEW_VERSION)
                with self.assertRaisesRegex(ValueError, "^malformed reviewed Core binding$"):
                    contract._device_contract_phase(NEW_VERSION)


class ExecutablePathTests(unittest.IsolatedAsyncioTestCase):
    """Every advertised slot runs the executable binding helpers and dispatcher."""

    async def assert_slot_executes(self, row):
        version, slot = row["ha_version"], row["support_slot"]
        binding = core_lane_bindings.reviewed_lane_binding(version)
        self.assertIs(row["alarmo"], True)
        # Disposable lane helpers.
        entry = lane.lane_entry(version)
        self.assertEqual(entry["image_index_digest"], binding.image_index_digest)
        self.assertEqual(entry["source_commit"], row["core_source_commit"])
        self.assertIs(lane.probe_profile_for(version),
                      CHILD_DEVICE_NAME_PARTS_PROBE_PROFILE
                      if binding.device_registry == core_lane_bindings.CHILD_DEVICE_NAME_PARTS
                      else CHILD_DEVICE_PROBE_PROFILE)
        # Alarmo slot binding is exactly the advertised row.
        bound = alarmo.slot_binding(version)
        self.assertEqual((bound["slot"], bound["version"], bound["image"], bound["lane"]),
                         (slot, version, row["ha_image"], row["lane"]))
        # Contract runner: the advertising slot takes the only route that runs
        # Alarmo and baseline acceptance.
        self.assertIs(contract._alarmo_slot_required(version), True)
        self.assertEqual(contract._device_contract_phase(version), contract.CHILD_CONTRACT_PHASE)
        self.assertIs(contract._core_test_authority(version), True)
        self.assertIs(contract._expected_device_registry_schema(version),
                      contract.DeviceRegistrySchema.CHILD_DEVICE_NAME_PARTS
                      if binding.device_registry == core_lane_bindings.CHILD_DEVICE_NAME_PARTS
                      else contract.DeviceRegistrySchema.CHILD_DEVICES)
        self.assertEqual(await dispatch(version), contract.CHILD_CONTRACT_PHASE)
        self.assertEqual(dispatch.calls, (1, 0))
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            local = SimpleNamespace(ha_url="http://127.0.0.1:8123")
            # CI-only test authority signs the exact lane entry before runtime use.
            runtime = SimpleNamespace(configure=Mock(side_effect=Boundary))
            with self.assertRaises(Boundary):
                await lane.configure_with_test_authority(runtime, local, cache_path=directory / "core.json",
                                                         expected_image=row["ha_image"], core_version=version)
            # Alarmo acceptance reaches the disposable Core only after every binding check.
            profile = json.loads((alarmo.FIXTURES / "alarmo_profile.json").read_text(encoding="utf-8"))
            receipt = directory / "fixture.json"
            receipt.write_text(json.dumps({"fixture": "synthetic-alarmo-inspection-v1",
                                           "alarmo_source": alarmo.COMMIT,
                                           "archive_sha256": profile["archive_sha256"]}), encoding="utf-8")
            existing = SimpleNamespace(current_observation=SimpleNamespace(version=version),
                                       acquire=Mock(side_effect=Boundary))
            with self.assertRaises(Boundary):
                await alarmo.run_disposable(local, existing, fixture_receipt=receipt,
                                            expected_image=row["ha_image"], core_version=version)
            existing.acquire.assert_called_once()
            # Automation-baseline acceptance stops at its first Core client.
            image = directory / "image.json"
            image.write_text(json.dumps({"result": "PASS", "index": binding.image_index_digest,
                                         "core_version": version, "support_slot": slot}), encoding="utf-8")
            environ = {**os.environ, "HA_CONTRACT_LANE": row["lane"], "REAL_HA_ALARMO_IMAGE_RESULT": str(image)}
            with patch.dict(os.environ, environ, clear=True), \
                    patch("ha_mcp_engineering.clients.HomeAssistantWebSocketClient", side_effect=Boundary) as client:
                with self.assertRaises(Boundary):
                    await baseline.run_disposable(local, expected_image=row["ha_image"], core_version=version)
                client.assert_called_once_with(local)
                other = next(s for s in support_window.SLOTS if s != slot)
                image.write_text(json.dumps({"result": "PASS", "index": binding.image_index_digest,
                                             "core_version": version, "support_slot": other}), encoding="utf-8")
                with self.assertRaises(AssertionError):
                    await baseline.run_disposable(local, expected_image=row["ha_image"], core_version=version)
                client.assert_called_once()

    async def assert_window_executes(self, slots):
        loaded = support_window.load()
        self.assertEqual(support_window.core_slots(loaded, support_window.SLOTS), slots)
        for tier in ("pr", "full"):
            rows = support_window.real_ha_matrix(loaded, tier)["include"]
            self.assertEqual([r["support_slot"] for r in rows],
                             [s for s in slots if s in support_window.TIERS[tier]])
            for row in rows:
                with self.subTest(tier=tier, slot=row["support_slot"]):
                    await self.assert_slot_executes(row)

    async def test_committed_window_executes_every_advertised_slot(self):
        with window(COMMITTED["core"]):
            await self.assert_window_executes(support_window.core_slots(COMMITTED, support_window.SLOTS))

    async def test_null_candidate_executes_current_and_rollback_only(self):
        with window(NULL_CANDIDATE):
            await self.assert_window_executes(("current", "rollback"))
            rows = support_window.real_ha_matrix(support_window.load(), "pr")["include"]
            self.assertEqual([r["support_slot"] for r in rows], ["current"])
            former = COMMITTED["core"]["candidate"]
            if former is not None:
                with self.assertRaisesRegex(ValueError, "exact support-window Core slot"):
                    alarmo.slot_binding(former["version"])
                self.assertIs(contract._alarmo_slot_required(former["version"]), False)

    def test_null_candidate_command_line_advertises_no_candidate_core_job(self):
        with window(NULL_CANDIDATE) as root:
            # The add-on matrix reuses the acceptance scripts' own pairing authority.
            shutil.copytree(ROOT / "scripts", root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
            for event, slots in (("pull_request", ["current"]), ("schedule", ["current", "rollback"])):
                output = root / ("github-output-" + event)
                with self.subTest(event=event):
                    result = subprocess.run([sys.executable, str(root / "scripts" / "support_window.py"),
                                             "--event", event, "--github-output", str(output)],
                                            cwd=root, capture_output=True, text=True, timeout=120)
                    self.assertEqual((result.returncode, result.stderr), (0, ""))
                    outputs = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
                    rows = json.loads(outputs["real_ha"])["include"]
                    self.assertEqual([r["support_slot"] for r in rows], slots)
                    self.assertTrue(all(r["alarmo"] is True for r in rows))
                    if COMMITTED["core"]["candidate"] is not None:
                        self.assertNotIn(COMMITTED["core"]["candidate"]["version"], {r["ha_version"] for r in rows})
                    self.assertEqual(json.loads(result.stdout)["real_ha"]["include"], rows)

    async def test_a_rotation_among_reviewed_cores_executes_without_code_edits(self):
        before = dict(core_lane_bindings.REVIEWED_CORE_BINDINGS)
        with window(ROTATED):
            await self.assert_window_executes(("current", "rollback"))
            self.assertEqual(alarmo.slot_binding("2026.10.1")["slot"], "current")
            self.assertEqual(alarmo.slot_binding("2026.10.0")["slot"], "rollback")
            # 2026.9.4 left the window: no Alarmo binding or requirement remains.
            with self.assertRaisesRegex(ValueError, "exact support-window Core slot"):
                alarmo.slot_binding("2026.9.4")
            self.assertIs(contract._alarmo_slot_required("2026.9.4"), False)
        self.assertEqual(core_lane_bindings.REVIEWED_CORE_BINDINGS, before)

    async def test_b_unreviewed_core_is_refused_and_never_dispatched(self):
        self.assertIsNone(core_lane_bindings.reviewed_core_binding(NEW_VERSION))
        with window(WITH_NEW):
            with self.assertRaisesRegex(support_window.Refusal, "^core_candidate_binding_unreviewed$"):
                support_window.load()
            for version in (NEW_VERSION, "2026.10.0", "2026.9.4"):
                with self.subTest(version=version):
                    with self.assertRaisesRegex(ValueError, "exact support-window Core slot"):
                        alarmo.slot_binding(version)
                    with self.assertRaisesRegex(support_window.Refusal, "^core_candidate_binding_unreviewed$"):
                        contract._device_contract_phase(version)
                    with self.assertRaisesRegex(support_window.Refusal, "^core_candidate_binding_unreviewed$"):
                        await dispatch(version)
                    self.assertEqual(dispatch.calls, (0, 0))
            with self.assertRaisesRegex(ValueError, "^unknown disposable Core release$"):
                lane.lane_entry(NEW_VERSION)

    async def test_c_new_core_executes_once_its_binding_is_in_the_reviewed_list(self):
        with window(WITH_NEW), patch.dict(core_lane_bindings.REVIEWED_CORE_BINDINGS, {NEW_VERSION: NEW_BINDING}):
            await self.assert_window_executes(support_window.SLOTS)
            row = support_window.real_ha_matrix(support_window.load(), "pr")["include"][1]
            self.assertEqual((row["support_slot"], row["ha_version"], row["core_source_commit"]),
                             ("candidate", NEW_VERSION, "d" * 40))
        # The fixture binding was added for the test only.
        self.assertIsNone(core_lane_bindings.reviewed_core_binding(NEW_VERSION))


class DispatcherTests(unittest.IsolatedAsyncioTestCase):
    """The dispatcher cannot route an Alarmo-advertising slot around Alarmo."""

    def test_dispatch_is_the_only_route_to_either_device_contract(self):
        statements = dispatcher_statements()
        self.assertEqual(ast.unparse(statements[0]), "phase = _device_contract_phase(EXPECTED_HA_VERSION)")
        branch = statements[1]
        self.assertIsInstance(branch, ast.If)
        self.assertEqual(ast.unparse(branch.test), "phase == CHILD_CONTRACT_PHASE")
        self.assertEqual([ast.unparse(s) for s in branch.body],
                         ["await _run_core_2026_9_child_contract(websocket, token)"])
        self.assertEqual([ast.unparse(s) for s in branch.orelse],
                         ["await _run_device_migration_contract(rest, websocket, token)"])
        tree = ast.parse(CONTRACT_PATH.read_text(encoding="utf-8"))
        calls = [ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)]
        for name in ("_run_core_2026_9_child_contract", "_run_device_migration_contract", "_device_contract_phase"):
            self.assertEqual(calls.count(name), 1, name)
        # Alarmo and baseline acceptance run only inside the child contract.
        functions = {node.name: node for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for name, function in functions.items():
            invoked = {ast.unparse(node.func) for node in ast.walk(function) if isinstance(node, ast.Call)}
            if invoked & {"run_disposable", "run_baseline"}:
                self.assertEqual(name, "_run_core_2026_9_child_contract")

    def test_alarmo_advertisement_takes_precedence_over_every_other_route(self):
        # Even a version with no binding is routed to the child contract (which
        # then fails closed) if any slot advertises Alarmo for it.
        with patch.object(contract, "_alarmo_slot_required", return_value=True), \
                patch.object(contract, "_core_binding", return_value=None):
            self.assertEqual(contract._device_contract_phase("2099.1.0"), contract.CHILD_CONTRACT_PHASE)
        with patch.object(contract, "_alarmo_slot_required", return_value=False), \
                patch.object(contract, "_core_binding", return_value=None):
            self.assertEqual(contract._device_contract_phase("2099.1.0"), contract.MIGRATION_CONTRACT_PHASE)
        # Versions outside the window and the list (e.g. the latest-stable early
        # warning) keep the pre-existing migration route.
        for version in ("2026.7.2", "2026.8.1", "2026.11.0", NEW_VERSION):
            with self.subTest(version=version):
                self.assertEqual(contract._device_contract_phase(version), contract.MIGRATION_CONTRACT_PHASE)

    async def test_every_advertised_row_dispatches_to_the_child_contract(self):
        for core in (COMMITTED["core"], NULL_CANDIDATE, ROTATED):
            with window(core):
                for row in support_window.real_ha_matrix(support_window.load(), "full")["include"]:
                    with self.subTest(core=core, slot=row["support_slot"]):
                        self.assertIs(row["alarmo"], True)
                        self.assertEqual(await dispatch(row["ha_version"]), contract.CHILD_CONTRACT_PHASE)
                        self.assertEqual(dispatch.calls, (1, 0))

    async def test_binding_removed_after_validation_fails_closed(self):
        version = COMMITTED["core"]["current"]["version"]
        with patch.dict(core_lane_bindings.REVIEWED_CORE_BINDINGS):
            del core_lane_bindings.REVIEWED_CORE_BINDINGS[version]
            with self.assertRaisesRegex(support_window.Refusal, "^core_current_binding_unreviewed$"):
                await dispatch(version)
            self.assertEqual(dispatch.calls, (0, 0))
        # The child contract itself refuses a version with no reviewed binding
        # before any Core read.
        websocket = SimpleNamespace(command=AsyncMock())
        with patch.object(contract, "EXPECTED_HA_VERSION", "2099.1.0"), self.assertRaises(Exception) as caught:
            await contract._run_core_2026_9_child_contract(websocket, "synthetic-token")
        self.assertNotIsInstance(caught.exception, AttributeError)
        websocket.command.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
