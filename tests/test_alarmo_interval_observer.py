"""Bounded synthetic observer/proof controls; actual Core execution is separate CI.

Alarmo acceptance follows every non-empty support-window Core slot (HAMCP-159).
Slot-driven tests iterate the committed window; single-slot tests name the
rollback slot explicitly. Rotations use a temporary repository root.
"""
import asyncio
from contextlib import contextmanager
import copy
import hashlib
import importlib
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import alarmo_inspection_contract_acceptance as acceptance
import support_window

spec = importlib.util.spec_from_file_location("synthetic_alarmo_observer", acceptance.OBSERVER)
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)

WINDOW = support_window.load(ROOT)
# Every Alarmo-bearing slot in the committed window, in window order.
ALARMO_SLOTS = support_window.core_slots(WINDOW, support_window.ALARMO_SLOTS)
SLOT_VERSIONS = {slot: WINDOW["core"][slot]["version"] for slot in ALARMO_SLOTS}
# Single-slot checks use the rollback slot explicitly (Core 2026.9.4 today).
CORE = WINDOW["core"]["rollback"]["version"]
OTHER_CORE = WINDOW["core"]["current"]["version"]
CI = ROOT / ".github/workflows/ci.yml"
BETA23 = ROOT / "tests/fixtures/real_ha_device_migration/custom_components/beta23_device_fixture"
NOT_SLOTS = ("2026.9.3", "2026.9.2", "2026.11.0", "2026.10", "2026.10.", "2026.10.*", "2026.9", "2026.9.*",
             "", " 2026.10.0", "2026.10.0 ", "2026.10.0\n", "v2026.10.0", "2026.10.00")
NOT_STRINGS = (None, 2026.9, 2026, b"2026.10.0", ["2026.10.0"], {"version": "2026.10.0"})


def receipt(commands=(), core_version=CORE):
    ledger = observer.Ledger(lambda: 1)
    hashes = {key: None for key in acceptance.STORE_HASH_KEYS}
    identity = ledger.start("inspection", hashes)
    for command in commands:
        ledger.record("command", command=command)
    value = ledger.finish(identity, dict(hashes), {key: True for key in acceptance.OBSERVER_HOOKS})
    value.update(core_version=core_version, observer_sha256=hashlib.sha256(acceptance.OBSERVER.read_bytes()).hexdigest())
    return value


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def window_root(directory):
    """A disposable repository root holding the committed window and provenance."""
    root = Path(directory)
    (root / "ci").mkdir(parents=True)
    (root / "tests" / "fixtures").mkdir(parents=True)
    shutil.copy2(ROOT / "ci" / "support-window.yaml", root / "ci" / "support-window.yaml")
    for fixture in (ROOT / "tests" / "fixtures").glob("core_*_lane_provenance.json"):
        shutil.copy2(fixture, root / "tests" / "fixtures" / fixture.name)
    return root


def provenance_path(root, version):
    return Path(root, "tests", "fixtures", "core_" + version.replace(".", "_") + "_lane_provenance.json")


def edit_window(root, change):
    path = Path(root, "ci", "support-window.yaml")
    window = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(window)
    path.write_text(yaml.safe_dump(window, sort_keys=False), encoding="utf-8")


@contextmanager
def bound_window(root):
    """Make slot_binding read ``root`` instead of the committed repository."""
    module = importlib.import_module("support_window")
    original = module.alarmo_slot
    with patch.object(module, "alarmo_slot", lambda version: original(version, root=root)):
        yield


def core_archive(path, files=None, *, roots=("core-synthetic",), extra=()):
    """A codeload-shaped tarball (pax global header, one top directory)."""
    if files is None:
        files = {name: b"synthetic Core read source " + name.encode() for name in acceptance.CORE_READ_SOURCES}
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT, pax_headers={"comment": "0" * 40}) as stream:
        for root in roots:
            directory = tarfile.TarInfo(root)
            directory.type = tarfile.DIRTYPE
            stream.addfile(directory)
            for name, raw in [*files.items(), ("homeassistant/__init__.py", b"synthetic"), *extra]:
                info = tarfile.TarInfo(name if name.startswith(("/", "..")) else root + "/" + name)
                info.size = len(raw)
                stream.addfile(info, io.BytesIO(raw))
    return {name: sha256(raw) for name, raw in sorted(files.items())}


def cleanup_value(core_version):
    binding = acceptance.slot_binding(core_version)
    observation = receipt(acceptance.EXPECTED_COMMANDS, core_version)
    control = receipt(["get_states"], core_version); control["kind"] = "control"
    denial = receipt((), core_version); denial["kind"] = "control"
    return dict(result="PASS", scenario="alarmo_inspection", core=core_version, support_slot=binding["slot"],
                core_image=binding["image"], runtime_monitor_closed=True, interval_observation=observation,
                missing_authority_observation=denial, negative_control_observation=control,
                automation_baseline={"synthetic_receipt": True},
                interval=acceptance.verify_interval(observation, observation["interval_id"], acceptance.EXPECTED_COMMANDS,
                                                    core_version=core_version),
                missing_authority=acceptance.verify_interval(denial, denial["interval_id"], [], kind="control",
                                                             core_version=core_version))


class ObserverTests(unittest.IsolatedAsyncioTestCase):
    async def test_disposable_cleanup_retires_real_core_runtime_and_joins_its_monitor(self):
        sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
        from ha_mcp_engineering.ha_core_readmission import CoreRuntime
        runtime = CoreRuntime()
        stopped = asyncio.Event()
        async def monitor():
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        task = asyncio.create_task(monitor())
        runtime._connection_monitor_task = task
        await asyncio.sleep(0)
        await acceptance.close_disposable_authority(runtime)
        self.assertTrue(stopped.is_set())
        self.assertTrue(task.done())
        self.assertIsNone(runtime._connection_monitor_task)
        self.assertFalse(runtime.initialized)
        await acceptance.close_disposable_authority(runtime)

    async def test_setup_wait_covers_source_defined_startup_delay_without_feature_calls(self):
        now, calls = [0], []
        async def command(payload):
            calls.append(payload)
            return {"core_running": now[0] >= 5, "storage_pending": now[0] < 180, "hooks_intact": True}
        async def sleep(seconds):
            now[0] += seconds
        result = await acceptance.wait_disposable_setup(SimpleNamespace(command=command), clock=lambda: now[0], sleep=sleep)
        self.assertEqual(result, {"result": "PASS", "readiness_reads": 37})
        self.assertEqual(now[0], 180)
        self.assertEqual(calls, [{"type": "alarmo_interval_observer/ready"}] * 37)
        self.assertIn("alarmo_interval_observer/ready", observer.CONTROL_COMMANDS)

    async def test_setup_wait_refuses_timeout_missing_hooks_bad_shape_and_request_failure(self):
        for value in (
            {"core_running": True, "storage_pending": True, "hooks_intact": True},
            {"core_running": False, "storage_pending": False, "hooks_intact": True},
            {"core_running": True, "storage_pending": False, "hooks_intact": False},
            {"core_running": True, "storage_pending": 0, "hooks_intact": True},
            {},
        ):
            now, calls = [0], []
            async def command(payload):
                calls.append(payload)
                return value
            async def sleep(seconds):
                now[0] += seconds
            with self.subTest(value=value), self.assertRaises(ValueError):
                await acceptance.wait_disposable_setup(SimpleNamespace(command=command), clock=lambda: now[0], sleep=sleep)
            self.assertLessEqual(len(calls), 41)
            self.assertLessEqual(now[0], 200)
        calls = []
        async def failure(payload):
            calls.append(payload)
            raise RuntimeError("synthetic transport unavailable")
        with self.assertRaises(RuntimeError):
            await acceptance.wait_disposable_setup(SimpleNamespace(command=failure))
        self.assertEqual(len(calls), 1)

    def test_empty_and_exact_command_intervals_are_useful_successes(self):
        for commands in ([], acceptance.EXPECTED_COMMANDS):
            value = receipt(commands)
            result = acceptance.verify_interval(value, value["interval_id"], commands, core_version=CORE)
            self.assertEqual(result["result"], "PASS")
            self.assertEqual(result["commands"], commands)

    def test_independent_extra_command_control_rejects_and_callbacks_preserved(self):
        ledger = observer.Ledger()
        identity = ledger.start("inspection", {})
        seen = []
        def original(connection, message):
            seen.append(message)
            return 7
        original._hass_callback = True
        wrapped = observer.observe_sync(ledger, "command", original)
        self.assertTrue(wrapped._hass_callback)
        for name in ("alarmo/config", "get_states", [], None):
            self.assertEqual(wrapped(None, {"type": name, "secret": "DO_NOT_RECORD"}), 7)
        wrapped(None, {"type": "alarmo_interval_observer/finish"})
        value = ledger.finish(identity, {}, {})
        self.assertEqual([e["command"] for e in value["events"]], ["alarmo/config", "other", "other", "other"])
        self.assertNotIn("DO_NOT_RECORD", json.dumps(value))
        self.assertEqual(len(seen), 5)
        value = receipt([*acceptance.EXPECTED_COMMANDS, "get_states"])
        with self.assertRaises(ValueError):
            acceptance.verify_interval(value, value["interval_id"], acceptance.EXPECTED_COMMANDS, core_version=CORE)

    async def test_context_survives_async_handler_and_resets_for_background(self):
        ledger = observer.Ledger()
        identity = ledger.start("inspection", {})
        async def save(store, body):
            return body
        store = SimpleNamespace(key="alarmo.storage", _data=None)
        wrapped_save = observer.observe_async(ledger, "storage_save", save)
        tasks = []
        def handler(connection, message):
            tasks.append(asyncio.create_task(wrapped_save(store, "synthetic-private-value")))
        observer.observe_sync(ledger, "command", handler)(None, {"type": "alarmo/config"})
        self.assertIsNone(observer.REQUEST.get())
        self.assertEqual(await tasks[0], "synthetic-private-value")
        ledger.record("storage_write", store=SimpleNamespace(key="auth", _data=None))
        events = ledger.finish(identity, {}, {})["events"]
        self.assertEqual(events[1]["origin"], "command")
        self.assertEqual(events[2]["origin"], "background")
        self.assertNotIn("synthetic-private-value", json.dumps(events))

    def test_flows_services_command_storage_and_unrelated_stores_fail(self):
        events = [
            {"kind": "flow_init", "origin": "background"},
            {"kind": "config_flow_init", "origin": "command"},
            {"kind": "flow_configure", "origin": "command"},
            {"kind": "flow_abort", "origin": "command"},
            {"kind": "service", "origin": "background"},
            {"kind": "storage_save", "origin": "command", "store": "auth"},
            {"kind": "storage_write", "origin": "background", "store": "alarmo.storage"},
            {"kind": "storage_delay_save", "origin": "background", "store": "other"},
            {"kind": "storage_remove", "origin": "background", "store": "auth"},
        ]
        for event in events:
            value = receipt()
            value["events"].append(event)
            with self.subTest(event=event), self.assertRaises(ValueError):
                acceptance.verify_interval(value, value["interval_id"], [], core_version=CORE)

    def test_background_bookkeeping_is_explicit_not_global_zero_claim(self):
        value = receipt()
        event = {"kind": "storage_write", "origin": "background", "store": "auth"}
        value["events"].append(event)
        proof = acceptance.verify_interval(value, value["interval_id"], [], core_version=CORE)
        self.assertEqual(proof["background_bookkeeping"], [event])
        self.assertNotIn("feature_writes", proof)

    def test_bounds_expiration_coverage_identity_and_changed_store_refuse(self):
        for mutate in (
            lambda v: v.update(expired=True), lambda v: v.update(overflow=True),
            lambda v: v.update(elapsed_seconds=46), lambda v: v.update(elapsed_seconds=float("nan")),
            lambda v: v.update(core_version="2026.9.3"), lambda v: v.update(observer_sha256="0" * 64),
            lambda v: v.update(coverage={}), lambda v: v["coverage"].update({"Store.async_save": False}),
            lambda v: v["final_store_hashes"].update({"alarmo.storage": "0" * 64}),
            lambda v: v.update(events=[{}] * 129), lambda v: v.update(extra="private"),
            lambda v: v["events"].append({"kind": "command", "origin": "background", "command": "other", "args": "private"}),
        ):
            value = receipt()
            mutate(value)
            with self.assertRaises(ValueError):
                acceptance.verify_interval(value, value["interval_id"], [], core_version=CORE)
        value = receipt()
        with self.assertRaises(ValueError):
            acceptance.verify_interval(value, "0" * 32, [], core_version=CORE)
        now = [0]
        ledger = observer.Ledger(lambda: now[0])
        identity = ledger.start("inspection", {})
        for _ in range(1000):
            ledger.record("service")
        now[0] = 46
        result = ledger.finish(identity, {}, {})
        self.assertTrue(result["overflow"] and result["expired"])
        self.assertEqual(len(result["events"]), 128)

    def test_interval_is_bound_to_the_exact_caller_supplied_slot_core(self):
        for slot, version in SLOT_VERSIONS.items():
            value = receipt(acceptance.EXPECTED_COMMANDS, version)
            with self.subTest(slot=slot):
                proof = acceptance.verify_interval(value, value["interval_id"], acceptance.EXPECTED_COMMANDS,
                                                   core_version=version)
                self.assertEqual(proof["result"], "PASS")
            for other in [v for v in SLOT_VERSIONS.values() if v != version] + list(NOT_SLOTS):
                with self.subTest(slot=slot, other=other), self.assertRaises(ValueError):
                    acceptance.verify_interval(value, value["interval_id"], acceptance.EXPECTED_COMMANDS,
                                               core_version=other)
        for wrong in NOT_STRINGS:
            value = receipt((), wrong)
            with self.subTest(core_version=wrong), self.assertRaises(ValueError):
                acceptance.verify_interval(value, value["interval_id"], [], core_version=wrong)
        value = receipt()
        with self.assertRaises(TypeError):
            acceptance.verify_interval(value, value["interval_id"], [])

    def test_setup_readiness_hashes_symlinks_and_active_interval_refusal(self):
        ledger = observer.Ledger()
        write_lock = SimpleNamespace(locked=lambda: False)
        store = SimpleNamespace(key="alarmo.storage", _data={"not-read": "private"}, _write_lock=write_lock)
        ledger.record("storage_save", store=store)
        self.assertTrue(ledger.pending_setup_storage())
        store._data = None
        self.assertFalse(ledger.pending_setup_storage())
        write_lock.locked = lambda: True
        self.assertTrue(ledger.pending_setup_storage())
        write_lock.locked = lambda: False
        identity = ledger.start("inspection", {})
        with self.assertRaises(ValueError):
            ledger.start("control", {})
        with self.assertRaises(ValueError):
            ledger.finish("wrong", {}, {})
        ledger.finish(identity, {}, {})
        with tempfile.TemporaryDirectory() as directory:
            storage = Path(directory) / ".storage"
            storage.mkdir()
            raw = b"synthetic-private-store"
            (storage / "alarmo.storage").write_bytes(raw)
            hashes = observer.store_hashes(directory)
            self.assertEqual(hashes["alarmo.storage"], hashlib.sha256(raw).hexdigest())
            self.assertNotIn("synthetic-private", json.dumps(hashes))
            (storage / "core.config_entries").symlink_to(storage / "alarmo.storage")
            with self.assertRaises(ValueError):
                observer.store_hashes(directory)


UNSET = object()


class PublicationCleanupTests(unittest.TestCase):
    def test_cleanup_requires_successful_enumeration_no_residue_and_actual_interval(self):
        value = cleanup_value(CORE)
        lane = acceptance.slot_binding(CORE)["lane"]
        self.assertEqual(lane, "ha-" + CORE.replace(".", "-"))
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "result.json", Path(directory) / "cleanup.json"
            source.write_text(json.dumps(value))
            with patch("subprocess.run", return_value=SimpleNamespace(stdout=b"")), patch(
                    "automation_baseline_contract_acceptance.verify_result", return_value={"result": "PASS"}) as baseline:
                self.assertEqual(acceptance.verify_disposable_cleanup(source, output, CORE)["result"], "PASS")
                baseline.assert_called_once_with(value["automation_baseline"], core_version=CORE)
            with patch("subprocess.run", return_value=SimpleNamespace(stdout=b"")), patch(
                    "automation_baseline_contract_acceptance.verify_result", side_effect=ValueError("missing baseline proof")), self.assertRaises(ValueError):
                acceptance.verify_disposable_cleanup(source, output, CORE)
            value["runtime_monitor_closed"] = False
            source.write_text(json.dumps(value))
            with patch("subprocess.run", return_value=SimpleNamespace(stdout=b"")), self.assertRaises(ValueError):
                acceptance.verify_disposable_cleanup(source, output, CORE)
            value["runtime_monitor_closed"] = True
            source.write_text(json.dumps(value))
            for response in (f"beta25-real-ha-{lane}\n".encode(), f"beta-rc2-contract-{lane}\n".encode(),
                             f"beta25-real-ha-writer-{lane}\n".encode(), f"beta25-real-ha-upstream-{lane}\n".encode()):
                with self.subTest(residue=response), patch(
                        "subprocess.run", return_value=SimpleNamespace(stdout=response)), self.assertRaises(ValueError):
                    acceptance.verify_disposable_cleanup(source, output, CORE)
            with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "docker")), self.assertRaises(subprocess.CalledProcessError):
                acceptance.verify_disposable_cleanup(source, output, CORE)
            value["result"] = "NOT_RUN"
            source.write_text(json.dumps(value))
            with patch("subprocess.run", return_value=SimpleNamespace(stdout=b"")), self.assertRaises(ValueError):
                acceptance.verify_disposable_cleanup(source, output, CORE)

    def test_cleanup_follows_every_alarmo_slot(self):
        for slot, version in SLOT_VERSIONS.items():
            value = cleanup_value(version)
            self.assertEqual(value["support_slot"], slot)
            with self.subTest(slot=slot), tempfile.TemporaryDirectory() as directory:
                source, output = Path(directory) / "result.json", Path(directory) / "cleanup.json"
                source.write_text(json.dumps(value))
                with patch("subprocess.run", return_value=SimpleNamespace(stdout=b"")) as run, patch(
                        "automation_baseline_contract_acceptance.verify_result", return_value={"result": "PASS"}) as baseline:
                    result = acceptance.verify_disposable_cleanup(source, output, version)
                self.assertEqual((result["result"], result["core"], result["support_slot"]), ("PASS", version, slot))
                self.assertEqual(json.loads(output.read_text()), result)
                baseline.assert_called_once_with(value["automation_baseline"], core_version=version)
                self.assertEqual(run.call_count, 2)

    def test_cleanup_refuses_wrong_unknown_or_tampered_slot_binding(self):
        value = cleanup_value(CORE)
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "result.json", Path(directory) / "cleanup.json"
            source.write_text(json.dumps(value))
            for other in [v for v in SLOT_VERSIONS.values() if v != CORE]:
                with self.subTest(other=other), patch("subprocess.run", return_value=SimpleNamespace(stdout=b"")), patch(
                        "automation_baseline_contract_acceptance.verify_result", return_value={"result": "PASS"}), \
                        self.assertRaises(ValueError):
                    acceptance.verify_disposable_cleanup(source, output, other)
            for unknown in [*NOT_SLOTS, *NOT_STRINGS]:
                with self.subTest(unknown=unknown), patch("subprocess.run") as run, self.assertRaises(ValueError):
                    acceptance.verify_disposable_cleanup(source, output, unknown)
                run.assert_not_called()
            other_image = acceptance.slot_binding(OTHER_CORE)["image"]
            for mutate in (lambda v: v.update(support_slot="current"), lambda v: v.pop("support_slot"),
                           lambda v: v.update(core_image=other_image), lambda v: v.pop("core_image"),
                           lambda v: v.update(core=OTHER_CORE),
                           lambda v: v["interval_observation"].update(core_version=OTHER_CORE),
                           lambda v: v["negative_control_observation"].update(core_version=OTHER_CORE)):
                tampered = copy.deepcopy(value)
                mutate(tampered)
                source.write_text(json.dumps(tampered))
                with self.subTest(tampered=tampered.get("support_slot")), patch(
                        "subprocess.run", return_value=SimpleNamespace(stdout=b"")), patch(
                        "automation_baseline_contract_acceptance.verify_result", return_value={"result": "PASS"}) as baseline, \
                        self.assertRaises(ValueError):
                    acceptance.verify_disposable_cleanup(source, output, CORE)
                baseline.assert_not_called()

    def publication(self, root, slot, files=None, **archive_options):
        """Point ``slot`` in the temp window at synthetic index, manifest and source bytes."""
        version = WINDOW["core"][slot]["version"]
        archive = Path(root) / f"source-{slot}.tar.gz"
        hashes = core_archive(archive, files, **archive_options)
        manifest = json.dumps({"config": {"digest": "sha256:" + "a" * 64}}).encode()
        md = "sha256:" + sha256(manifest)
        index = json.dumps({"manifests": [
            {"digest": md, "size": len(manifest), "platform": {"os": "linux", "architecture": "amd64"}},
            {"digest": "sha256:" + "b" * 64, "size": 1, "platform": {"os": "linux", "architecture": "arm64"}}]}).encode()
        index_digest = "sha256:" + sha256(index)
        fixture = provenance_path(root, version)
        entry = json.loads(fixture.read_text())
        entry.update(source_archive_sha256=sha256(archive.read_bytes()), image_index_digest=index_digest,
                     architecture_manifests={"linux/amd64": md, "linux/arm64": "sha256:" + "b" * 64})
        fixture.write_text(json.dumps(entry))
        edit_window(root, lambda w: w["core"][slot].update(image_index_digest=index_digest))
        identity = b"synthetic-container sha256:" + b"a" * 64
        return SimpleNamespace(
            version=version, slot=slot, archive=archive, hashes=hashes, entry=entry,
            image=f"ghcr.io/home-assistant/home-assistant:{version}@{index_digest}",
            container="beta25-real-ha-ha-" + version.replace(".", "-"),
            outputs=[index + b"\n", manifest, identity, b"sha256:" + b"a" * 64 + b" linux/amd64", identity])

    def publish(self, root, case, outputs=None, *, image=None, container=None, version=UNSET, archive=None):
        output = Path(root) / "image.json"
        with bound_window(root), patch("subprocess.run", side_effect=[
                SimpleNamespace(stdout=x) for x in (case.outputs if outputs is None else outputs)]) as run:
            try:
                return acceptance.verify_disposable_publication(
                    case.image if image is None else image, case.container if container is None else container,
                    case.archive if archive is None else archive, output,
                    case.version if version is UNSET else version)
            finally:
                self.calls = run.call_count

    def test_image_binding_is_digest_architecture_and_container_continuity_bound(self):
        for slot in ALARMO_SLOTS:
            with self.subTest(slot=slot), tempfile.TemporaryDirectory() as directory:
                root = window_root(directory)
                case = self.publication(root, slot)
                result = self.publish(root, case)
                self.assertEqual(result["result"], "PASS")
                self.assertEqual((result["core_version"], result["support_slot"]), (case.version, slot))
                self.assertEqual(result["index"], case.entry["image_index_digest"])
                self.assertEqual(result["source_commit"], case.entry["source_commit"])
                self.assertEqual(result["source_archive_sha256"], case.entry["source_archive_sha256"])
                self.assertEqual(result["core_read_sources_sha256"], case.hashes)
                self.assertEqual(set(result["core_read_sources_sha256"]), set(acceptance.CORE_READ_SOURCES))
                self.assertEqual(json.loads((root / "image.json").read_text()), result)
                for i, wrong in ((0, b"wrong-index"), (1, b"wrong-manifest"), (3, b"sha256:" + b"a" * 64 + b" linux/arm64"),
                                 (4, b"other-container sha256:" + b"a" * 64)):
                    bad = list(case.outputs); bad[i] = wrong
                    with self.subTest(slot=slot, index=i), self.assertRaises(ValueError):
                        self.publish(root, case, bad)

    def test_publication_refuses_other_slot_container_image_or_unknown_core(self):
        with tempfile.TemporaryDirectory() as directory:
            root = window_root(directory)
            case = self.publication(root, "rollback")
            others = [slot for slot in ALARMO_SLOTS if slot != "rollback"]
            self.assertTrue(others)
            for other in others:
                version = WINDOW["core"][other]["version"]
                other_image = f"ghcr.io/home-assistant/home-assistant:{version}@{WINDOW['core'][other]['image_index_digest']}"
                for kwargs in ({"container": "beta25-real-ha-ha-" + version.replace(".", "-")},
                               {"image": other_image},
                               {"version": version}):
                    with self.subTest(other=other, kwargs=kwargs), self.assertRaises(ValueError):
                        self.publish(root, case, **kwargs)
                    self.assertEqual(self.calls, 0)
            for container in ("beta25-real-ha-ha-2026-9-3", "beta25-real-ha-writer-" + "ha-" + CORE.replace(".", "-"),
                              "beta25-real-ha-" + CORE, ""):
                with self.subTest(container=container), self.assertRaises(ValueError):
                    self.publish(root, case, container=container)
                self.assertEqual(self.calls, 0)
            image_without_digest = "ghcr.io/home-assistant/home-assistant:" + CORE
            with self.assertRaises(ValueError):
                self.publish(root, case, image=image_without_digest)
            for unknown in [*NOT_SLOTS, *NOT_STRINGS]:
                with self.subTest(unknown=unknown), self.assertRaises(ValueError):
                    self.publish(root, case, version=unknown)
                self.assertEqual(self.calls, 0)
            tampered = Path(directory) / "tampered.tar.gz"
            tampered.write_bytes(case.archive.read_bytes() + b"\0")
            with self.assertRaisesRegex(ValueError, "Core source archive mismatch"):
                self.publish(root, case, archive=tampered)
            self.assertEqual(self.calls, 0)

    def test_publication_refuses_provenance_that_disagrees_with_the_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = window_root(directory)
            case = self.publication(root, "rollback")
            fixture = provenance_path(root, case.version)
            entry = json.loads(fixture.read_text())
            entry["image_index_digest"] = "sha256:" + "0" * 64
            fixture.write_text(json.dumps(entry))
            with self.assertRaises(ValueError):
                self.publish(root, case)
            self.assertEqual(self.calls, 0)
            entry["image_index_digest"] = case.entry["image_index_digest"]
            entry["source_commit"] = "main"
            fixture.write_text(json.dumps(entry))
            with self.assertRaises(ValueError):
                self.publish(root, case)
            self.assertEqual(self.calls, 0)
            fixture.unlink()
            with self.assertRaises(ValueError):
                self.publish(root, case)

    def test_publication_refuses_archives_without_exact_core_read_sources(self):
        partial = {name: b"synthetic" for name in acceptance.CORE_READ_SOURCES[1:]}
        for options in ({"files": partial}, {"roots": ("core-a", "core-b")}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                root = window_root(directory)
                case = self.publication(root, "rollback", **options)
                with self.assertRaises(ValueError):
                    self.publish(root, case)
                self.assertFalse((root / "image.json").exists())


class CoreReadSourceTests(unittest.TestCase):
    def test_hashes_exactly_the_three_read_handlers_from_one_root(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "core.tar.gz"
            hashes = core_archive(archive)
            self.assertEqual(acceptance.core_read_source_hashes(archive), hashes)
            self.assertEqual(list(hashes), sorted(acceptance.CORE_READ_SOURCES))

    def test_missing_ambiguous_escaping_or_oversized_members_refuse(self):
        sources = acceptance.CORE_READ_SOURCES
        cases = {
            "missing": dict(files={name: b"x" for name in sources[:-1]}),
            "two_roots": dict(roots=("core-a", "core-b")),
            "duplicate": dict(extra=((sources[0], b"second copy"),)),
            "traversal": dict(extra=(("../escape.py", b"x"),)),
            "absolute": dict(extra=(("/etc/escape.py", b"x"),)),
            "oversized": dict(files={**{name: b"x" for name in sources[1:]}, sources[0]: b"\0" * (4 * 1024 * 1024 + 1)}),
        }
        for name, options in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                archive = Path(directory) / "core.tar.gz"
                core_archive(archive, **options)
                with self.assertRaises(ValueError):
                    acceptance.core_read_source_hashes(archive)


class SlotBindingTests(unittest.TestCase):
    def test_binding_follows_every_committed_alarmo_slot(self):
        self.assertEqual(ALARMO_SLOTS, ("current", "candidate", "rollback"))
        self.assertIn(CORE, SLOT_VERSIONS.values())
        for slot, version in SLOT_VERSIONS.items():
            provenance = json.loads(provenance_path(ROOT, version).read_text(encoding="utf-8"))
            with self.subTest(slot=slot):
                binding = acceptance.slot_binding(version)
                self.assertEqual(binding, {
                    "slot": slot, "version": version, "lane": "ha-" + version.replace(".", "-"),
                    "image": f"ghcr.io/home-assistant/home-assistant:{version}@{WINDOW['core'][slot]['image_index_digest']}",
                    "image_index_digest": WINDOW["core"][slot]["image_index_digest"],
                    "source_commit": provenance["source_commit"],
                    "source_archive_sha256": provenance["source_archive_sha256"],
                    "provenance": provenance})

    def test_binding_refuses_unknown_family_prefix_and_non_string_cores(self):
        for value in [*NOT_SLOTS, *NOT_STRINGS]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "exact support-window Core slot"):
                acceptance.slot_binding(value)
        for value in NOT_SLOTS:
            with self.subTest(value=value), self.assertRaisesRegex(support_window.Refusal, "^alarmo_core_not_in_window_slot$"):
                support_window.alarmo_slot(value)

    def test_binding_follows_a_rotated_window_without_code_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = window_root(directory)
            current, candidate, rollback = (WINDOW["core"][s] for s in ("current", "candidate", "rollback"))
            # Deploy the candidate: current -> rollback, candidate -> current, no new candidate.
            edit_window(root, lambda w: w["core"].update(current=dict(candidate), rollback=dict(current), candidate=None))
            with bound_window(root):
                self.assertEqual(acceptance.slot_binding(candidate["version"])["slot"], "current")
                self.assertEqual(acceptance.slot_binding(current["version"])["slot"], "rollback")
                with self.assertRaisesRegex(ValueError, "exact support-window Core slot"):
                    acceptance.slot_binding(rollback["version"])
                with tempfile.TemporaryDirectory() as scratch, patch("subprocess.run") as run, self.assertRaises(ValueError):
                    acceptance.verify_disposable_cleanup(Path(scratch) / "result.json", Path(scratch) / "out.json",
                                                         rollback["version"])
                run.assert_not_called()


def ha_stubs(version):
    """Minimal synthetic Home Assistant modules; restored by ``patch.dict``."""
    def identity(function):
        return function

    class ActiveConnection:
        def async_handle(self, message): return message

    class ConfigEntriesFlowManager:
        async def async_init(self, *args, **kwargs): return None

    class FlowManager:
        async def async_init(self, *args, **kwargs): return None
        async def async_configure(self, *args, **kwargs): return None
        def async_abort(self, *args, **kwargs): return None

    class ServiceRegistry:
        async def async_call(self, *args, **kwargs): return None

    class Store:
        async def async_save(self, *args): return None
        def async_delay_save(self, *args): return None
        async def _async_write_data(self, *args): return None
        async def async_remove(self): return None

    def module(name, **attributes):
        value = ModuleType(name)
        value.__dict__.update(attributes)
        return value

    registered = []
    websocket = module("homeassistant.components.websocket_api", websocket_command=lambda schema: identity,
                       require_admin=identity, async_response=identity,
                       async_register_command=lambda hass, handler: registered.append(handler.__name__))
    modules = {
        "voluptuous": module("voluptuous", Required=lambda key: ("required", key), In=tuple, Match=str),
        "homeassistant": module("homeassistant"),
        "homeassistant.const": module("homeassistant.const", __version__=version,
                                      EVENT_HOMEASSISTANT_STOP="homeassistant_stop"),
        "homeassistant.core": module("homeassistant.core", CoreState=SimpleNamespace(running="running"),
                                     ServiceRegistry=ServiceRegistry, callback=identity, HomeAssistant=object),
        "homeassistant.config_entries": module("homeassistant.config_entries",
                                               ConfigEntriesFlowManager=ConfigEntriesFlowManager, ConfigEntry=object),
        "homeassistant.data_entry_flow": module("homeassistant.data_entry_flow", FlowManager=FlowManager),
        "homeassistant.helpers": module("homeassistant.helpers"),
        "homeassistant.helpers.storage": module("homeassistant.helpers.storage", Store=Store),
        "homeassistant.components": module("homeassistant.components", websocket_api=websocket),
        "homeassistant.components.websocket_api": websocket,
        "homeassistant.components.websocket_api.connection": module(
            "homeassistant.components.websocket_api.connection", ActiveConnection=ActiveConnection),
    }
    classes = (ActiveConnection, ConfigEntriesFlowManager, FlowManager, ServiceRegistry, Store)
    originals = {(cls, name): value for cls in classes for name, value in vars(cls).items() if not name.startswith("__")}
    return modules, registered, originals


class ObserverSetupGuardTests(unittest.IsolatedAsyncioTestCase):
    """The observer installs hooks only under CI's exact ``core_version`` binding."""

    def hass(self):
        return SimpleNamespace(data={}, bus=SimpleNamespace(async_listen_once=Mock()), state=None)

    async def test_exact_ci_binding_installs_hooks_for_every_slot(self):
        for version in SLOT_VERSIONS.values():
            modules, registered, originals = ha_stubs(version)
            hass = self.hass()
            with self.subTest(version=version), patch.dict(sys.modules, modules):
                self.assertIs(await observer.async_setup(hass, {observer.DOMAIN: {"core_version": version}}), True)
                self.assertIsInstance(hass.data[observer.DOMAIN], observer.Ledger)
                self.assertEqual(registered, ["ready", "start", "finish"])
                self.assertTrue(all(getattr(cls, name) is not original for (cls, name), original in originals.items()))
                (event, restore), _ = hass.bus.async_listen_once.call_args
                self.assertEqual(event, "homeassistant_stop")
                restore(None)
                self.assertTrue(all(getattr(cls, name) is original for (cls, name), original in originals.items()))
            self.assertNotIn("homeassistant.const", sys.modules)

    async def test_missing_partial_extra_or_other_core_binding_refuses_before_hooks(self):
        configs = (
            {}, {observer.DOMAIN: None}, {observer.DOMAIN: {}}, {observer.DOMAIN: "core_version"},
            {observer.DOMAIN: {"core_version": OTHER_CORE}}, {observer.DOMAIN: {"core_version": CORE[:-2]}},
            {observer.DOMAIN: {"core_version": CORE[:-1] + "*"}}, {observer.DOMAIN: {"core_version": None}},
            {observer.DOMAIN: {"core_version": CORE, "extra": True}},
            {observer.DOMAIN: {"baseline_core_version": CORE}}, {"beta23_device_fixture": {"baseline_core_version": CORE}},
        )
        for config in configs:
            modules, registered, originals = ha_stubs(CORE)
            hass = self.hass()
            with self.subTest(config=config), patch.dict(sys.modules, modules):
                with self.assertRaisesRegex(ValueError, "exact disposable Core revision"):
                    await observer.async_setup(hass, config)
                self.assertEqual((hass.data, registered), ({}, []))
                hass.bus.async_listen_once.assert_not_called()
                self.assertTrue(all(getattr(cls, name) is original for (cls, name), original in originals.items()))
        modules, registered, _ = ha_stubs(CORE)
        hass = self.hass()
        hass.data[observer.DOMAIN] = "already-installed"
        with patch.dict(sys.modules, modules), self.assertRaises(ValueError):
            await observer.async_setup(hass, {observer.DOMAIN: {"core_version": CORE}})
        self.assertEqual((hass.data, registered), ({observer.DOMAIN: "already-installed"}, []))

    def test_observer_source_has_no_hard_coded_core(self):
        source = acceptance.OBSERVER.read_text(encoding="utf-8")
        self.assertIn('config.get(DOMAIN) != {"core_version": __version__} or DOMAIN in hass.data', source)
        self.assertNotRegex(source, r"20[0-9]{2}\.[0-9]{1,2}\.[0-9]")


class BaselineFixtureGuardTests(unittest.IsolatedAsyncioTestCase):
    """beta23 registers the automation-baseline fixture only under CI's exact binding."""

    PACKAGE = "beta23_device_fixture_guard_under_test"

    def load(self, version):
        modules, _, _ = ha_stubs(version)
        register = Mock()
        modules[self.PACKAGE + ".baseline_fixture"] = SimpleNamespace(register=register)
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        spec = importlib.util.spec_from_file_location(self.PACKAGE, BETA23 / "__init__.py",
                                                      submodule_search_locations=[str(BETA23)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[self.PACKAGE] = module
        spec.loader.exec_module(module)
        hass = SimpleNamespace(data={}, config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock(return_value=True)))
        return module, hass, register, modules["homeassistant.const"]

    async def test_exact_binding_registers_the_baseline_once_for_every_slot(self):
        for version in SLOT_VERSIONS.values():
            with self.subTest(version=version):
                module, hass, register, _ = self.load(version)
                config = {module.DOMAIN: {"baseline_core_version": version}}
                self.assertIs(await module.async_setup(hass, config), True)
                self.assertEqual(hass.data, {module.BASELINE_KEY: version})
                entry = object()
                self.assertIs(await module.async_setup_entry(hass, entry), True)
                register.assert_called_once_with(hass)
                hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(entry, module.PLATFORMS)

    async def test_absent_binding_sets_up_entries_without_the_baseline(self):
        module, hass, register, _ = self.load(CORE)
        for config in ({}, {"alarmo_interval_observer": {"core_version": CORE}}):
            self.assertIs(await module.async_setup(hass, config), True)
            self.assertEqual(hass.data, {})
        self.assertIs(await module.async_setup_entry(hass, object()), True)
        register.assert_not_called()
        hass.config_entries.async_forward_entry_setups.assert_awaited_once()

    async def test_mismatched_binding_refuses_and_never_registers(self):
        for value in (None, {}, CORE, {"baseline_core_version": OTHER_CORE}, {"baseline_core_version": CORE[:-2]},
                      {"baseline_core_version": CORE, "extra": True}, {"core_version": CORE},
                      {"baseline_core_version": None}):
            with self.subTest(value=value):
                module, hass, register, _ = self.load(CORE)
                with self.assertRaisesRegex(ValueError, "exact disposable Core revision"):
                    await module.async_setup(hass, {module.DOMAIN: value})
                self.assertEqual(hass.data, {})
                await module.async_setup_entry(hass, object())
                register.assert_not_called()

    async def test_recorded_binding_must_still_equal_the_running_core(self):
        module, hass, register, const = self.load(CORE)
        await module.async_setup(hass, {module.DOMAIN: {"baseline_core_version": CORE}})
        const.__version__ = OTHER_CORE
        await module.async_setup_entry(hass, object())
        register.assert_not_called()
        hass.data[module.BASELINE_KEY] = CORE[:-2]
        const.__version__ = CORE
        await module.async_setup_entry(hass, object())
        register.assert_not_called()

    def test_fixture_sources_have_no_hard_coded_core(self):
        for name in ("__init__.py", "baseline_fixture.py"):
            with self.subTest(name=name):
                self.assertNotRegex((BETA23 / name).read_text(encoding="utf-8"), r"20[0-9]{2}\.[0-9]{1,2}\.[0-9]")


class WorkflowBindingTests(unittest.TestCase):
    """ci.yml runs Alarmo on every ``matrix.alarmo`` row with no literal Core."""

    @classmethod
    def setUpClass(cls):
        workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
        cls.steps = workflow["jobs"]["real-ha-contract-tests"]["steps"]
        cls.names = [step.get("name") for step in cls.steps]

    def step(self, name):
        return self.steps[self.names.index(name)]

    def test_alarmo_steps_are_gated_on_matrix_alarmo_only(self):
        gated = {step["name"]: step["if"] for step in self.steps if "alarmo" in str(step.get("if", ""))}
        self.assertEqual(gated, {
            "Stage exact Alarmo and independent interval observer for the Core slot": "matrix.alarmo",
            "Bind the Core slot's disposable image and source": "matrix.alarmo",
            "Verify owned cleanup and required Alarmo interval": "always() && matrix.alarmo",
            "Upload bounded Alarmo interval and image evidence": "always() && matrix.alarmo",
        })
        for step in self.steps:
            text = json.dumps(step)
            if "alarmo" in text.lower():
                with self.subTest(step=step.get("name")):
                    self.assertIn(step.get("if"), ("matrix.alarmo", "always() && matrix.alarmo"))
                    self.assertNotIn("2026.9.4", text)
                    self.assertNotIn("2026-9-4", text)
                    self.assertNotIn("matrix.ha_version ==", text)
        self.assertNotIn("matrix.ha_version ==", CI.read_text(encoding="utf-8"))
        names = self.names
        stage = names.index("Stage exact Alarmo and independent interval observer for the Core slot")
        start = names.index("Start disposable exact target Home Assistant Core")
        bind = names.index("Bind the Core slot's disposable image and source")
        run = names.index("Run real Home Assistant contracts")
        sanitize = names.index("Sanitize and remove disposable Home Assistant")
        cleanup = names.index("Verify owned cleanup and required Alarmo interval")
        self.assertLess(names.index("Persist migration fixture with exact Home Assistant 2026.7.2"), stage)
        self.assertTrue(stage < start < bind < run < sanitize < cleanup)
        self.assertIn("--verify-publication", self.steps[bind]["run"])
        self.assertIn("--verify-cleanup", self.steps[cleanup]["run"])

    def test_core_archive_uses_only_fixed_codeload_forms_and_exact_digest(self):
        step = self.step("Stage exact Alarmo and independent interval observer for the Core slot")
        self.assertEqual(step["env"], {"REAL_HA_CORE_SOURCE_COMMIT": "${{ matrix.core_source_commit }}",
                                       "REAL_HA_CORE_SOURCE_SHA256": "${{ matrix.core_source_sha256 }}"})
        script = step["run"]
        self.assertTrue(script.startswith("set -euo pipefail\n"))
        urls = re.findall(r"https://[^\s'\"]+", script)
        self.assertEqual(urls, [
            "https://codeload.github.com/nielsfaber/alarmo/tar.gz/" + acceptance.COMMIT,
            "https://codeload.github.com/home-assistant/core/$form/$REAL_HA_CORE_SOURCE_COMMIT"])
        self.assertEqual(re.findall(r"^\s*for form in (.*); do$", script, re.MULTILINE), ["legacy.tar.gz tar.gz"])
        self.assertEqual(script.count("sha256sum --check --strict"), 1)
        self.assertIn('printf \'%s  %s\\n\' "$REAL_HA_CORE_SOURCE_SHA256" "$core_archive" | sha256sum --check --strict', script)
        self.assertIn('test -n "$matched"', script)
        self.assertIn("--proto '=https' --tlsv1.2", script)
        # No pinned Core commit or digest survives in the workflow step itself.
        self.assertEqual(re.findall(r"\b[0-9a-f]{40}\b", script), [acceptance.COMMIT])
        self.assertEqual(re.findall(r"\b[0-9a-f]{64}\b", script), [])
        self.assertIn("'alarmo_interval_observer:' \"  core_version: \\\"$HA_CONTRACT_VERSION\\\"\"", script)
        self.assertIn("'beta23_device_fixture:' \"  baseline_core_version: \\\"$HA_CONTRACT_VERSION\\\"\"", script)

    def test_evidence_artifact_is_named_by_core_and_slot(self):
        step = self.step("Upload bounded Alarmo interval and image evidence")
        self.assertEqual(step["with"]["name"],
                         "alarmo-inspection-core-${{ matrix.ha_version }}-${{ matrix.support_slot }}-amd64")
        self.assertEqual([line.strip() for line in step["with"]["path"].strip().splitlines()], [
            "${{ runner.temp }}/alarmo-integration-${{ matrix.lane }}.json",
            "${{ runner.temp }}/alarmo-image-${{ matrix.lane }}*.json",
            "${{ runner.temp }}/alarmo-cleanup-${{ matrix.lane }}.json"])
        self.assertEqual(step["with"]["if-no-files-found"], "error")
