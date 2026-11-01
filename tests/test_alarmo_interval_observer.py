"""Bounded synthetic observer/proof controls; actual Core execution is separate CI.

Alarmo acceptance follows every non-empty support-window Core slot (HAMCP-159).
Slot-driven tests iterate the committed window; single-slot tests name the
rollback slot explicitly. Rotations use a temporary repository root.
"""
import asyncio
import concurrent.futures
from contextlib import contextmanager, nullcontext
import copy
import dataclasses
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
import core_lane_bindings
import support_window
from entity_metadata_contract_acceptance import load_receipt
from entity_metadata_receipts import synthetic_receipt as synthetic_entity_metadata_receipt

spec = importlib.util.spec_from_file_location("synthetic_alarmo_observer", acceptance.OBSERVER)
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)

WINDOW = support_window.load(ROOT)
# Every Alarmo-bearing slot in the committed window, in window order.
ALARMO_SLOTS = support_window.core_slots(WINDOW, support_window.ALARMO_SLOTS)
SLOT_VERSIONS = {slot: WINDOW["core"][slot]["version"] for slot in ALARMO_SLOTS}
SLOT_OF = {version: slot for slot, version in SLOT_VERSIONS.items()}
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


class FakeLock:
    def __init__(self):
        self.held = False

    def locked(self):
        return self.held


class FakeStore:
    """Only the readiness attributes Core's Store carries; weak-referenceable."""

    def __init__(self, key, data=None, delay_handle=None, final_listener=None):
        self.key = key
        self._data = data
        self._delay_handle = delay_handle
        self._unsub_final_write_listener = final_listener
        self._write_lock = FakeLock()


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
                discarded_observations=[],
                missing_authority_observation=denial, negative_control_observation=control,
                automation_baseline={"synthetic_receipt": True},
                entity_metadata=synthetic_entity_metadata_receipt(core_version),
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
            pending = now[0] < 180
            return {"core_running": now[0] >= 5, "storage_pending": pending, "hooks_intact": True,
                    "pending_store_classes": ["lovelace"] if pending else [], "last_start_refusal": None}
        async def sleep(seconds):
            now[0] += seconds
        result = await acceptance.wait_disposable_setup(SimpleNamespace(command=command), clock=lambda: now[0], sleep=sleep)
        self.assertEqual(result, {"result": "PASS", "readiness_reads": 37})
        self.assertEqual(now[0], 180)
        self.assertEqual(calls, [{"type": "alarmo_interval_observer/ready"}] * 37)
        self.assertIn("alarmo_interval_observer/ready", observer.CONTROL_COMMANDS)

    async def test_setup_wait_refuses_timeout_missing_hooks_bad_shape_and_request_failure(self):
        ready = {"core_running": True, "storage_pending": False, "hooks_intact": True, "pending_store_classes": [],
                 "last_start_refusal": None}
        for value in (
            dict(ready, storage_pending=True, pending_store_classes=["lovelace"]),
            dict(ready, core_running=False),
            dict(ready, hooks_intact=False),
            dict(ready, storage_pending=0),
            # HAMCP-180: the class list is bounded, sorted, consistent and token-only.
            {key: ready[key] for key in ("core_running", "storage_pending", "hooks_intact")},
            {key: ready[key] for key in ready if key != "last_start_refusal"},
            dict(ready, last_start_refusal={"cause": "observer_refused", "pending_store_classes": []}),
            dict(ready, extra=True),
            dict(ready, pending_store_classes=None),
            dict(ready, storage_pending=True),
            dict(ready, pending_store_classes=["lovelace"]),
            dict(ready, storage_pending=True, pending_store_classes=["person", "lovelace"]),
            dict(ready, storage_pending=True, pending_store_classes=["lovelace", "lovelace"]),
            dict(ready, storage_pending=True, pending_store_classes=["/config/.storage/lovelace"]),
            dict(ready, storage_pending=True, pending_store_classes=["lovelace.dashboard_private"]),
            dict(ready, storage_pending=True, pending_store_classes=[1]),
            dict(ready, storage_pending=True, pending_store_classes=[f"d{i:02d}" for i in range(17)]),
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
        with self.assertRaises(acceptance.ObservationError) as found:
            await acceptance.wait_disposable_setup(SimpleNamespace(command=failure))
        self.assertEqual(len(calls), 1)
        self.assertEqual(found.exception.diagnostic["cause"], "readiness_transport_error")
        self.assertIsInstance(found.exception.__cause__, RuntimeError)

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
        store = FakeStore("alarmo.storage")
        wrapped_save = observer.observe_async(ledger, "storage_save", save)
        tasks = []
        def handler(connection, message):
            tasks.append(asyncio.create_task(wrapped_save(store, "synthetic-private-value")))
        observer.observe_sync(ledger, "command", handler)(None, {"type": "alarmo/config"})
        self.assertIsNone(observer.REQUEST.get())
        self.assertEqual(await tasks[0], "synthetic-private-value")
        ledger.record("storage_write", store=FakeStore("auth"))
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
        now = [0]
        ledger = observer.Ledger(lambda: now[0])
        store = FakeStore("alarmo.storage", data={"not-read": "private"}, delay_handle=object())
        ledger.record("storage_delay_save", store=store)
        self.assertTrue(ledger.pending_setup_storage())
        now[0] = 60
        self.assertEqual(ledger.pending_store_classes(), ["alarmo.storage"])
        store._delay_handle, store._unsub_final_write_listener = None, object()
        self.assertTrue(ledger.pending_setup_storage())
        store._data = None
        self.assertFalse(ledger.pending_setup_storage())
        store._write_lock.held = True
        self.assertTrue(ledger.pending_setup_storage())
        store._write_lock.held = False
        # Core's async_remove clears the handle and listener but keeps _data: settled.
        store._data, store._unsub_final_write_listener = {"not-read": "private"}, None
        self.assertFalse(ledger.pending_setup_storage())
        self.assertNotIn("private", json.dumps(ledger.pending_store_classes()))
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


BOUNDED_TOKEN = re.compile(r"[A-Za-z0-9_./]{0,64}")


def assert_bounded(case, value):
    """A diagnostic holds only short vocabulary tokens, ints, bools and None."""
    if isinstance(value, dict):
        case.assertLessEqual(len(value), 32)
        for key, item in value.items():
            case.assertRegex(key, r"\A[a-z_]{1,32}\Z")
            assert_bounded(case, item)
    elif isinstance(value, list):
        case.assertLessEqual(len(value), acceptance.MAX_DIAGNOSTIC_ITEMS)
        for item in value:
            assert_bounded(case, item)
    elif isinstance(value, str):
        case.assertRegex(value, BOUNDED_TOKEN.pattern + r"\Z")
        case.assertNotRegex(value, r"[0-9a-f]{32}")
    else:
        case.assertTrue(value is None or type(value) in (int, bool), value)


def narrow_readiness(ledger):
    """Mutation: the pre-HAMCP-180 readiness, only the four hashed named stores."""
    with ledger.lock:
        return sorted({observer.store_class(s.key) for s in list(ledger.stores.values())
                       if s.key in observer.HASH_KEYS and (s._data is not None or s._write_lock.locked())})


class IntervalQuiescenceTests(unittest.IsolatedAsyncioTestCase):
    """HAMCP-180: every untolerated Core Store settles before a window opens,
    and a failed window names its condition with bounded tokens only."""

    UNNAMED = "core.synthetic_background"
    UNNAMED_CLASS = "core"  # an unknown Core name keeps only the fixed ``core`` class

    async def flake_signature(self):
        """Publish run 434's signature on the real observer handlers and setup wait.

        The named stores settle at t=1. An unnamed Core store is mid-burst of
        background delayed saves: Core keeps the earliest handle, so the burst
        stays scheduled until one flush at t=13. Five of its saves fall at t=6-10.
        Returns the setup result and both 5 s window proofs.
        """
        modules, _registered, _originals = ha_stubs(CORE)
        handlers = {}
        modules["homeassistant.components.websocket_api"].async_register_command = (
            lambda hass, handler: handlers.setdefault(handler.__name__, handler))
        Store = modules["homeassistant.helpers.storage"].Store

        def store(key):
            value = Store()
            value.key, value._data, value._delay_handle = key, None, None
            value._unsub_final_write_listener, value._write_lock = None, FakeLock()
            return value

        def delay_save(target):
            target._data = {"private": "DO_NOT_RECORD"}
            target._delay_handle = target._delay_handle or object()
            target.async_delay_save({"private": "DO_NOT_RECORD"})

        def flush(target):
            target._data = target._delay_handle = None

        unnamed, named = store(self.UNNAMED), store("core.entity_registry")
        now = [0]
        schedule = [(0, delay_save, named), (1, flush, named), (3, delay_save, unnamed),
                    *[(when, delay_save, unnamed) for when in (6, 7, 8, 9, 10)], (13, flush, unnamed)]

        def advance(to):
            while schedule and schedule[0][0] <= to:
                now[0], action, target = schedule.pop(0)
                action(target)
            now[0] = to

        hass = SimpleNamespace(data={}, bus=SimpleNamespace(async_listen_once=Mock()), state="running")
        with patch.dict(sys.modules, modules):
            self.assertIs(await observer.async_setup(hass, {observer.DOMAIN: {"core_version": CORE}}), True)
        ledger = hass.data[observer.DOMAIN]
        ledger.clock = lambda: now[0]
        advance(0)

        class Connection:
            async def command(self, payload):
                assert payload == {"type": "alarmo_interval_observer/ready"}
                sent = []
                handlers["ready"](hass, SimpleNamespace(send_result=lambda _id, value: sent.append(value)),
                                  {"id": 1, **payload})
                return sent[0]

        async def sleep(seconds):
            advance(now[0] + seconds)

        setup = await acceptance.wait_disposable_setup(Connection(), clock=lambda: now[0], sleep=sleep)
        hashes = {key: None for key in acceptance.STORE_HASH_KEYS}
        proofs = []
        for index in (1, 2):
            self.assertFalse(ledger.pending_setup_storage())  # the /start gate
            identity = ledger.start("control", dict(hashes))
            advance(now[0] + 5)
            value = ledger.finish(identity, dict(hashes), {key: True for key in acceptance.OBSERVER_HOOKS})
            value.update(core_version=CORE, observer_sha256=sha256(acceptance.OBSERVER.read_bytes()))
            self.assertNotIn("DO_NOT_RECORD", json.dumps(value))
            proofs.append(acceptance.verify_interval(value, identity, [], kind="control", core_version=CORE,
                                                     window="baseline_export", window_index=index))
        return setup, proofs, now[0]

    async def test_flake_signature_passes_after_every_store_settles(self):
        setup, proofs, finished = await self.flake_signature()
        # Readiness held through the unnamed burst (t=5, 10) and its quiet period.
        self.assertEqual(setup, {"result": "PASS", "readiness_reads": 4})
        self.assertEqual(finished, 25)
        for proof in proofs:
            self.assertEqual(proof["result"], "PASS")
            self.assertEqual(proof["background_bookkeeping"], [])

    async def test_mutation_named_store_readiness_reproduces_the_flake_with_its_cause(self):
        with patch.object(observer.Ledger, "pending_store_classes", narrow_readiness), \
                self.assertRaises(acceptance.ObservationError) as found:
            await self.flake_signature()
        diagnostic = found.exception.diagnostic
        self.assertEqual(str(found.exception), "Disposable Core interval failed verification")
        self.assertEqual({key: diagnostic[key] for key in (
            "condition", "window", "window_index", "interval_kind", "event_count", "event_index", "event")}, {
            "condition": "untolerated_store", "window": "baseline_export", "window_index": 1,
            "interval_kind": "control", "event_count": 5, "event_index": 0,
            "event": {"kind": "storage_delay_save", "origin": "background", "store": "other",
                      "store_class": self.UNNAMED_CLASS}})
        self.assertEqual(diagnostic["event_counts"], [{"count": 5, "kind": "storage_delay_save",
                                                       "origin": "background", "store": "other",
                                                       "store_class": self.UNNAMED_CLASS}])
        assert_bounded(self, diagnostic)
        self.assertNotIn("DO_NOT_RECORD", json.dumps(diagnostic))

    async def test_mutation_tolerating_the_unnamed_store_in_readiness_reproduces_the_flake(self):
        with patch.object(observer, "TOLERATED_KEYS", observer.TOLERATED_KEYS | {self.UNNAMED}), \
                self.assertRaises(acceptance.ObservationError) as found:
            await self.flake_signature()
        self.assertEqual(found.exception.diagnostic["condition"], "untolerated_store")

    def assert_untolerated_store_refused(self, event):
        value = receipt()
        value["events"].append(event)
        with self.assertRaises(acceptance.ObservationError) as found:
            acceptance.verify_interval(value, value["interval_id"], [], core_version=CORE)
        self.assertEqual(found.exception.diagnostic["condition"], "untolerated_store")

    def test_genuine_writes_to_unnamed_and_hashed_stores_still_fail(self):
        events = (
            {"kind": "storage_delay_save", "origin": "background", "store": "other", "store_class": self.UNNAMED_CLASS},
            {"kind": "storage_write", "origin": "background", "store": "other", "store_class": "lovelace"},
            {"kind": "storage_save", "origin": "background", "store": "other", "store_class": "unclassified"},
            {"kind": "storage_write", "origin": "background", "store": "alarmo.storage"},
            {"kind": "storage_delay_save", "origin": "background", "store": "core.entity_registry"},
        )
        for event in events:
            with self.subTest(event=event):
                self.assert_untolerated_store_refused(event)

    def test_mutation_widening_the_allowlist_blindly_admits_a_genuine_write(self):
        event = {"kind": "storage_delay_save", "origin": "background", "store": "other",
                 "store_class": self.UNNAMED_CLASS}
        with patch.object(acceptance, "TOLERATED_STORES", (*acceptance.TOLERATED_STORES, "other")), \
                self.assertRaises(AssertionError):
            self.assert_untolerated_store_refused(event)

    async def test_stores_alive_before_the_hooks_are_found_by_the_scan(self):
        for scan in (True, False):
            modules, _registered, _originals = ha_stubs(CORE)
            Store = modules["homeassistant.helpers.storage"].Store
            early = Store()
            early.key, early._data, early._delay_handle = "person", {"private": "DO_NOT_RECORD"}, object()
            early._unsub_final_write_listener, early._write_lock = None, FakeLock()
            hass = SimpleNamespace(data={}, bus=SimpleNamespace(async_listen_once=Mock()), state="running")
            objects = None if scan else (lambda: [])  # mutation: no scan of pre-hook Stores
            with self.subTest(scan=scan), patch.dict(sys.modules, modules), \
                    patch("gc.get_objects", objects) if objects else nullcontext():
                await observer.async_setup(hass, {observer.DOMAIN: {"core_version": CORE}})
                self.assertEqual(hass.data[observer.DOMAIN].pending_store_classes(), ["person"] if scan else [])
            del early

    def test_quiet_period_follows_the_last_untolerated_activity_only(self):
        now = [0]
        ledger = observer.Ledger(lambda: now[0])
        unnamed, auth = FakeStore("lovelace.dashboard_private"), FakeStore("auth", data={"x": 1}, delay_handle=object())
        ledger.record("storage_write", store=unnamed)
        self.assertEqual(ledger.pending_store_classes(), ["lovelace"])
        now[0] = observer.QUIET_SECONDS
        self.assertEqual(ledger.pending_store_classes(), [])
        # Tolerated bookkeeping neither blocks readiness nor restarts the quiet period.
        ledger.record("storage_delay_save", store=auth)
        self.assertEqual(ledger.pending_store_classes(), [])

    def test_store_class_is_bounded_and_never_a_path_or_identifier(self):
        cases = {
            "alarmo.storage": "alarmo.storage", "core.entity_registry": "core.entity_registry", "auth": "auth",
            "core.analytics": "core.analytics", "core.restore_state_extra": "core",
            "core.abc1": "core", "core.uuid": "core.uuid", "core.a_b_c_d_e": "core",
            "frontend.user_data_0123456789abcdef": "frontend", "lovelace.dashboard_x": "lovelace",
            "person": "person", "http.auth": "http", "Bad.key": "unclassified", "1x": "unclassified",
            "": "unclassified", "../../etc/passwd": "unclassified", "a" * 33: "unclassified",
            None: "unclassified", 7: "unclassified",
        }
        for key, expected in cases.items():
            with self.subTest(key=key):
                value = observer.store_class(key)
                self.assertEqual(value, expected)
                self.assertIn(value, acceptance.STORE_CLASSES)
                self.assertLessEqual(len(value), 64)

    def test_genuine_violations_fail_with_their_named_condition(self):
        cases = (
            ({"kind": "service", "origin": "background"}, "unexpected_event_kind"),
            ({"kind": "flow_init", "origin": "command"}, "unexpected_event_kind"),
            ({"kind": "config_flow_init", "origin": "command"}, "unexpected_event_kind"),
            ({"kind": "storage_remove", "origin": "background", "store": "auth"}, "unexpected_event_kind"),
            ({"kind": "storage_save", "origin": "command", "store": "auth"}, "command_origin_storage"),
            ({"kind": "storage_write", "origin": "background", "store": "other"}, "storage_event_shape"),
            ({"kind": "storage_write", "origin": "background", "store": "auth", "store_class": "auth"},
             "storage_event_shape"),
            ({"kind": "command", "origin": "background", "command": "other", "args": "private"},
             "command_event_shape"),
            ({"kind": "storage_write", "origin": "elsewhere", "store": "auth"}, "event_shape"),
        )
        for event, condition in cases:
            value = receipt()
            value["events"].append(event)
            with self.subTest(event=event), self.assertRaises(acceptance.ObservationError) as found:
                acceptance.verify_interval(value, value["interval_id"], [], core_version=CORE,
                                           window="inspection", window_index=0)
            diagnostic = found.exception.diagnostic
            self.assertEqual((diagnostic["condition"], diagnostic["event_index"], diagnostic["window"]),
                             (condition, 0, "inspection"))
            assert_bounded(self, diagnostic)
            self.assertNotIn("private", json.dumps(diagnostic))
        value = receipt([*acceptance.EXPECTED_COMMANDS, "get_states"])
        with self.assertRaises(acceptance.ObservationError) as found:
            acceptance.verify_interval(value, value["interval_id"], acceptance.EXPECTED_COMMANDS, core_version=CORE)
        diagnostic = found.exception.diagnostic
        self.assertEqual(diagnostic["condition"], "commands_mismatch")
        self.assertEqual((diagnostic["expected_command_count"], diagnostic["observed_command_count"]),
                         (len(acceptance.EXPECTED_COMMANDS), len(acceptance.EXPECTED_COMMANDS) + 1))
        self.assertEqual(diagnostic["observed_commands"][-1], "other")
        assert_bounded(self, diagnostic)
        value = receipt()
        changed = "f" * 64
        value["final_store_hashes"]["alarmo.storage"] = changed
        with self.assertRaises(acceptance.ObservationError) as found:
            acceptance.verify_interval(value, value["interval_id"], [], core_version=CORE)
        self.assertEqual((found.exception.diagnostic["condition"], found.exception.diagnostic["changed_stores"]),
                         ("store_hash_changed", ["alarmo.storage"]))
        self.assertNotIn(changed, json.dumps(found.exception.diagnostic))

    def test_diagnostic_is_bounded_for_hostile_or_oversized_intervals(self):
        value = receipt()
        value["events"] = [{"kind": f"kind_{index}", "origin": "background", "store": "/config/.storage/x",
                            "store_class": "/config/.storage/secret", "path": "/config/secret"}
                           for index in range(128)]
        with self.assertRaises(acceptance.ObservationError) as found:
            acceptance.verify_interval(value, value["interval_id"], [], core_version=CORE,
                                       window="/config/secret", window_index=10 ** 6)
        diagnostic = found.exception.diagnostic
        self.assertEqual((diagnostic["window"], diagnostic["window_index"], diagnostic["event_count"]),
                         (None, None, 128))
        self.assertEqual(diagnostic["event"], {"kind": "invalid", "origin": "background", "store": "invalid",
                                               "store_class": "invalid", "unexpected_field_count": 1})
        self.assertLessEqual(len(diagnostic["event_counts"]), acceptance.MAX_DIAGNOSTIC_ITEMS)
        assert_bounded(self, diagnostic)
        self.assertNotIn("/config", json.dumps(diagnostic))
        for broken in (None, [], {"events": "x"}):
            with self.subTest(broken=broken), self.assertRaises(acceptance.ObservationError) as found:
                acceptance.verify_interval(broken, "0" * 32, [], core_version=CORE)
            self.assertEqual(found.exception.diagnostic["condition"], "receipt_shape")
            assert_bounded(self, found.exception.diagnostic)

    async def test_setup_wait_names_the_pending_store_classes_it_gave_up_on(self):
        now = [0]
        async def command(payload):
            return {"core_running": True, "storage_pending": True, "hooks_intact": True,
                    "pending_store_classes": ["core.analytics", "lovelace"], "last_start_refusal": None}
        async def sleep(seconds):
            now[0] += seconds
        with self.assertRaises(acceptance.ObservationError) as found:
            await acceptance.wait_disposable_setup(SimpleNamespace(command=command), clock=lambda: now[0], sleep=sleep,
                                                   window="baseline_export", window_index=2)
        self.assertEqual(found.exception.diagnostic, {
            "condition": "setup_not_settled", "cause": "readiness_budget_exhausted", "phase": "readiness",
            "window": "baseline_export", "window_index": 2, "readiness_reads": 40, "core_running": True,
            "pending_store_classes": ["core.analytics", "lovelace"], "start_recovery": None})
        async def malformed(payload):
            return {"core_running": True, "storage_pending": True, "hooks_intact": True,
                    "pending_store_classes": ["/config/.storage/lovelace"], "last_start_refusal": None}
        with self.assertRaises(acceptance.ObservationError) as found:
            await acceptance.wait_disposable_setup(SimpleNamespace(command=malformed))
        self.assertEqual(found.exception.diagnostic, {
            "condition": "setup_observation_unavailable", "cause": "store_class_list_malformed",
            "phase": "readiness", "window": None, "window_index": None, "readiness_reads": 1,
            "core_running": None, "pending_store_classes": None, "start_recovery": None})
        # The dashboard observer keeps its three-field readiness explicitly.
        async def dashboard(payload):
            return {"core_running": True, "storage_pending": False, "hooks_intact": True}
        self.assertEqual(await acceptance.wait_disposable_setup(SimpleNamespace(command=dashboard),
                                                                store_classes=False),
                         {"result": "PASS", "readiness_reads": 1})
        async def classes(payload):
            return {"core_running": True, "storage_pending": False, "hooks_intact": True, "pending_store_classes": [],
                    "last_start_refusal": None}
        for observed, flag in ((dashboard, True), (classes, False)):
            with self.subTest(flag=flag), self.assertRaises(acceptance.ObservationError):
                await acceptance.wait_disposable_setup(SimpleNamespace(command=observed), store_classes=flag)

    async def test_alarmo_lane_attaches_only_bounded_observation_diagnostics(self):
        failure = acceptance.ObservationError("Disposable Core interval failed verification",
                                              {"condition": "commands_mismatch", "window": "inspection"})
        with patch.object(acceptance, "_run_disposable", side_effect=failure), \
                self.assertRaises(acceptance.ObservationError) as found:
            await acceptance.run_disposable(None, None, fixture_receipt=None, expected_image=None, core_version=CORE)
        self.assertEqual(found.exception.contract_missing_key, "alarmo_inspection")
        self.assertEqual(found.exception.contract_diagnostic, {"observation_failure": failure.diagnostic})
        with patch.object(acceptance, "_run_disposable", side_effect=ValueError("other")), \
                self.assertRaises(ValueError) as found:
            await acceptance.run_disposable(None, None, fixture_receipt=None, expected_image=None, core_version=CORE)
        self.assertFalse(hasattr(found.exception, "contract_diagnostic"))

    async def test_fixture_receipt_with_a_repeated_key_refuses_before_core_access(self):
        profile = json.loads((acceptance.FIXTURES / "alarmo_profile.json").read_text(encoding="utf-8"))
        unique = json.dumps({"fixture": "synthetic-alarmo-inspection-v1", "alarmo_source": acceptance.COMMIT,
                             "archive_sha256": profile["archive_sha256"]})
        duplicated = unique[:-1] + ', "fixture": "synthetic-alarmo-inspection-v1"}'
        self.assertEqual(json.loads(duplicated), json.loads(unique))  # the hazard ordinary decoding hides
        local = SimpleNamespace(ha_url="http://127.0.0.1:8123")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.json"
            path.write_text(duplicated, encoding="utf-8")
            existing = SimpleNamespace(current_observation=SimpleNamespace(version=CORE), acquire=Mock())
            with self.assertRaisesRegex(ValueError, "receipt_duplicate_key"):
                await acceptance.run_disposable(local, existing, fixture_receipt=path,
                                                expected_image=acceptance.slot_binding(CORE)["image"],
                                                core_version=CORE)
            existing.acquire.assert_not_called()


SYNTHETIC_IDS = ("abcdef0123456789" * 2, "12345678-1234-5678-1234-567812345678", "0123abcd", "kitchen_42")


async def observer_runtime(*, state="running", config_dir="/nonexistent-observer-config", hashes=None):
    """The real observer ``async_setup`` and its three handlers on synthetic Core stubs."""
    modules, _registered, _originals = ha_stubs(CORE)
    handlers = {}
    modules["homeassistant.components.websocket_api"].async_register_command = (
        lambda hass, handler: handlers.setdefault(handler.__name__, handler))

    async def executor(function, *args):
        if hashes is not None:
            return hashes(function, *args)
        return {key: None for key in observer.HASH_KEYS}

    hass = SimpleNamespace(data={}, bus=SimpleNamespace(async_listen_once=Mock()), state=state,
                           config=SimpleNamespace(config_dir=config_dir), async_add_executor_job=executor)
    with patch.dict(sys.modules, modules):
        await observer.async_setup(hass, {observer.DOMAIN: {"core_version": CORE}})
    return hass, hass.data[observer.DOMAIN], handlers, modules


class Wire:
    """The observer handlers behind a client that, like the runtime websocket
    client, drops the error code and raises a generic request failure."""

    def __init__(self, hass, handlers):
        self.hass, self.handlers, self.sent, self.payloads = hass, handlers, [], []

    async def command(self, payload):
        self.payloads.append(dict(payload))
        outcome = {}
        connection = SimpleNamespace(send_result=lambda _id, value: outcome.update(result=value),
                                     send_error=lambda _id, code, _message: outcome.update(error=code))
        handled = self.handlers[payload["type"].split("/", 1)[1]](self.hass, connection, {"id": 1, **payload})
        if asyncio.iscoroutine(handled):
            await handled
        self.sent.append(dict(outcome))
        if "error" in outcome:
            raise RuntimeError("Home Assistant request failed")
        return outcome["result"]


def pending_store(key):
    return FakeStore(key, data={"private": "DO_NOT_RECORD"}, delay_handle=object())


# Synthetic, test-owned /start attempt identifiers (32 lowercase hex).
ATTEMPTS = ("a" * 32, "b" * 32, "c" * 32)


def ready_value(**changes):
    return {"core_running": True, "storage_pending": False, "hooks_intact": True, "pending_store_classes": [],
            "last_start_refusal": None, **changes}


# Total decoders: the only code that reads an observer response (HAMCP-180 R1).
DECODERS = ("decode_readiness", "_start_response")
# Validators that read raw observer data; only a decoder may call them.
VALIDATORS = ("readiness_problem", "_class_list_problem", "_refusal_problem")


def setup_helpers(source, functions):
    """Every module function the named setup functions reach by direct call."""
    import ast
    tree = ast.parse(source)
    defined = {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    reached, queue = set(), [name for name in functions if name in defined]
    while queue:
        name = queue.pop()
        if name in reached:
            continue
        reached.add(name)
        queue += [node.func.id for node in ast.walk(defined[name])
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in defined]
    return {name: defined[name] for name in sorted(reached - set(functions))}


def boundary_sites(source, functions):
    """Structural exception boundary of setup, readiness and start (HAMCP-180 R1).

    A helper reached from the setup functions never raises; a decoder keeps all
    observer-data access inside ``try``/``except Exception``; and a setup
    function neither subscripts an observer response nor calls a validator
    directly. Implicit errors are covered by the decoders' boundary and the
    behavioral malformed-shape matrix, not by this enumeration alone.
    """
    import ast
    tree = ast.parse(source)
    problems = []
    helpers = setup_helpers(source, functions)
    for name in DECODERS:
        if name not in helpers:
            problems.append(f"decoder_unreached:{name}")
    for name, function in helpers.items():
        for node in ast.walk(function):
            if isinstance(node, ast.Raise):
                problems.append(f"helper_raise:{name}:{node.lineno}")
        if name in DECODERS:
            guarded = set()
            for node in ast.walk(function):
                if isinstance(node, ast.Try) and any(getattr(handler.type, "id", None) == "Exception"
                                                     for handler in node.handlers):
                    for statement in node.body:
                        guarded |= {id(inner) for inner in ast.walk(statement)}
            for node in ast.walk(function):
                reads = isinstance(node, ast.Subscript) or (
                    isinstance(node, ast.Call) and getattr(node.func, "id", None) in VALIDATORS)
                if reads and id(node) not in guarded:
                    problems.append(f"decoder_unguarded:{name}:{node.lineno}")
    roots = {node.name: node for node in ast.walk(tree)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions}
    for name, function in roots.items():
        for node in ast.walk(function):
            if isinstance(node, ast.Subscript) and getattr(node.value, "id", None) == "value":
                problems.append(f"response_subscript:{name}:{node.lineno}")
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) in VALIDATORS:
                problems.append(f"validator_call:{name}:{node.lineno}")
    return problems


def raise_sites(source, functions):
    """Each raise and return in the named setup/readiness/start functions that is
    not the fixed structured setup failure (HAMCP-180), plus forbidden handlers
    and every boundary problem in the helpers they reach (``boundary_sites``).
    The count is of the named functions' explicit raises only."""
    import ast
    tree = ast.parse(source)
    found = {node.name: node for node in ast.walk(tree)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions}
    problems = [f"missing:{name}" for name in functions if name not in found]
    problems += boundary_sites(source, functions)

    def structured(node):
        if isinstance(node, ast.Await):
            node = node.value
            return isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_start_failure"
        return isinstance(node, ast.Call) and getattr(node.func, "id", None) in {"failure", "setup_failure"}

    raises = 0
    for name, function in found.items():
        for node in ast.walk(function):
            if isinstance(node, ast.Raise):
                raises += 1
                if node.exc is None or not structured(node.exc):
                    problems.append(f"raise:{name}:{node.lineno}")
            elif isinstance(node, ast.ExceptHandler):
                caught = getattr(node.type, "id", None) or getattr(node.type, "attr", None)
                if caught not in {"Exception"}:
                    problems.append(f"handler:{name}:{node.lineno}")
            elif isinstance(node, ast.Return) and name in {"_start_failure", "failure"}:
                if not structured(node.value):
                    problems.append(f"return:{name}:{node.lineno}")
    return raises, problems


def observer_start_sites(source):
    """Each raise in the observer ``/start`` handler that is not one fixed refusal."""
    import ast
    start = next(node for node in ast.walk(ast.parse(source))
                 if isinstance(node, ast.AsyncFunctionDef) and node.name == "start")
    causes, problems = set(), []
    for node in ast.walk(start):
        if isinstance(node, ast.Raise):
            call = node.exc
            if (isinstance(call, ast.Call) and getattr(call.func, "id", None) == "Refusal" and len(call.args) == 1
                    and isinstance(call.args[0], ast.Constant) and call.args[0].value in observer.START_REFUSALS):
                causes.add(call.args[0].value)
            else:
                problems.append(f"raise:{node.lineno}")
        elif isinstance(node, ast.ExceptHandler):
            caught = getattr(node.type, "id", None)
            if caught not in {"Refusal", "ValueError", "Exception"}:
                problems.append(f"handler:{node.lineno}")
        elif isinstance(node, ast.Assign) and any(getattr(target, "id", None) == "cause" for target in node.targets):
            value = node.value
            if isinstance(value, ast.Constant):
                causes.add(value.value)
    return causes, problems


SETUP_FUNCTIONS = ("wait_disposable_setup", "failure", "start_interval", "_start_failure")


class SetupFailureDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    """HAMCP-180 R1: every setup, readiness and start failure carries one fixed
    structured diagnostic; cancellation and the causal error are preserved."""

    def assert_setup_diagnostic(self, error, cause, phase, **expected):
        diagnostic = error.diagnostic
        self.assertEqual(set(diagnostic), acceptance.SETUP_DIAGNOSTIC_KEYS)
        self.assertEqual((diagnostic["condition"], diagnostic["cause"], diagnostic["phase"]),
                         (acceptance.SETUP_FAILURES[cause], cause, phase))
        for key, value in expected.items():
            self.assertEqual(diagnostic[key], value, key)
        assert_bounded(self, diagnostic)
        text = json.dumps(diagnostic)
        for secret in ("DO_NOT_RECORD", "synthetic read", "request failed", *SYNTHETIC_IDS):
            self.assertNotIn(secret, text)
        self.assertEqual(str(error), "Disposable setup observation failed")
        return diagnostic

    async def test_late_start_non_settlement_names_its_condition_and_classes(self):
        # Codex probe made permanent: the real /start handler, two clock reads.
        hass, ledger, handlers, _modules = await observer_runtime()
        store = pending_store("lovelace.dashboard_" + SYNTHETIC_IDS[2])
        ledger.track(store)
        errors = []
        connection = SimpleNamespace(send_error=lambda *args: errors.append(args),
                                     send_result=lambda *args: self.fail("must not start"))
        with patch.object(observer.time, "monotonic", side_effect=[0, 21]):
            await handlers["start"](hass, connection, {"id": 1, "kind": "control", "attempt_id": ATTEMPTS[0]})
        self.assertEqual(errors, [(1, "setup_not_settled_late_start", "Observer refused interval")])
        self.assertIsNone(ledger.active)
        self.assertEqual(ledger.start_refusal(), {"attempt_id": ATTEMPTS[0], "cause": "setup_not_settled_late_start",
                                                  "pending_store_classes": ["lovelace"]})
        # The runtime client drops the code; the bounded /ready read-back attributes it.
        wire = Wire(hass, handlers)
        # Only the observer's clock: the client's own read-back timer keeps real time.
        with patch.object(observer, "time", SimpleNamespace(monotonic=Mock(side_effect=[0, 21]))), \
                self.assertRaises(acceptance.ObservationError) as found:
            await acceptance.start_interval(wire, "control", window="baseline_export", window_index=1)
        self.assert_setup_diagnostic(found.exception, "setup_not_settled_late_start", "start",
                                     window="baseline_export", window_index=1, readiness_reads=1,
                                     core_running=True, pending_store_classes=["lovelace"],
                                     start_recovery="current_refusal")
        self.assertIsInstance(found.exception.__cause__, RuntimeError)
        self.assertEqual([list(item) for item in wire.sent], [["error"], ["result"]])
        self.assertNotIn("DO_NOT_RECORD", json.dumps(wire.sent))
        self.assertIsNone(ledger.active)
        # Useful success: once the store settles the interval opens and the refusal clears.
        store._data = store._delay_handle = None
        identity = await acceptance.start_interval(wire, "control", window="baseline_export", window_index=1)
        self.assertRegex(identity, r"\A[0-9a-f]{32}\Z")
        self.assertEqual(ledger.active["interval_id"], identity)
        self.assertIsNone(ledger.start_refusal())

    async def test_every_observer_start_refusal_reaches_the_lane_with_its_fixed_cause(self):
        def unavailable(function, *args):
            raise ValueError("observer store symlink refused")

        def broken(function, *args):
            raise RuntimeError("synthetic read failed")

        cases = (
            ("core_not_running", {"state": "not_running"}, None),
            ("hooks_not_intact", {}, "unhook"),
            ("store_hash_unavailable", {"hashes": unavailable}, None),
            ("interval_unavailable", {}, "occupied"),
            ("start_internal_error", {"hashes": broken}, None),
        )
        for cause, options, prepare in cases:
            with self.subTest(cause=cause):
                hass, ledger, handlers, modules = await observer_runtime(**options)
                occupied = None
                if prepare == "unhook":
                    Store = modules["homeassistant.helpers.storage"].Store
                    Store.async_remove = lambda self: None
                elif prepare == "occupied":
                    occupied = ledger.start("control", {})
                wire = Wire(hass, handlers)
                with self.assertRaises(acceptance.ObservationError) as found:
                    await acceptance.start_interval(wire, "inspection", window="inspection")
                core_running = options.get("state", "running") == "running"
                self.assert_setup_diagnostic(found.exception, cause, "start", window="inspection",
                                             readiness_reads=1, core_running=core_running,
                                             pending_store_classes=[], start_recovery="current_refusal")
                attempt = wire.payloads[0]["attempt_id"]
                self.assertRegex(attempt, r"\A[0-9a-f]{32}\Z")
                self.assertEqual(ledger.start_refusal(), {"attempt_id": attempt, "cause": cause,
                                                          "pending_store_classes": []})
                self.assertEqual(None if ledger.active is None else ledger.active["interval_id"], occupied)

    async def test_readiness_timeout_transport_and_malformed_values_are_structured(self):
        # Codex probes made permanent, plus the class-list element types.
        async def timed_out(_payload):
            raise TimeoutError("synthetic read timed out")

        async def runtime_timeout(_payload):
            raise RuntimeError("Home Assistant request failed") from TimeoutError()

        async def transport(_payload):
            raise ConnectionError("synthetic read failed")

        for command, cause, causal in ((timed_out, "readiness_timeout", TimeoutError),
                                       (runtime_timeout, "readiness_timeout", RuntimeError),
                                       (transport, "readiness_transport_error", ConnectionError)):
            with self.subTest(cause=cause, causal=causal), self.assertRaises(acceptance.ObservationError) as found:
                await acceptance.wait_disposable_setup(SimpleNamespace(command=command), window="negative_control")
            self.assert_setup_diagnostic(found.exception, cause, "readiness", window="negative_control",
                                         readiness_reads=0, core_running=None, pending_store_classes=None)
            self.assertIsInstance(found.exception.__cause__, causal)

        # The real deadline timer, not a synthetic TimeoutError.
        async def hangs(_payload):
            await asyncio.sleep(3600)
        with self.assertRaises(acceptance.ObservationError) as found:
            await acceptance.wait_disposable_setup(SimpleNamespace(command=hangs),
                                                   clock=Mock(side_effect=[0, 199.95, 199.95, 199.95]))
        self.assert_setup_diagnostic(found.exception, "readiness_timeout", "readiness")

        malformed = {
            "store_class_list_malformed": (
                [{}], [1], [None], [["lovelace"]], ["lovelace", "lovelace"], ["person", "lovelace"],
                ["/config/.storage/lovelace"], [SYNTHETIC_IDS[0]], ["invalid"], ["lovelace"] * 17, "lovelace", None,
                {"lovelace": True}),
            "readiness_shape_invalid": (),
        }
        for classes in malformed["store_class_list_malformed"]:
            async def command(_payload, classes=classes):
                return ready_value(storage_pending=True, pending_store_classes=classes)
            with self.subTest(classes=classes), self.assertRaises(acceptance.ObservationError) as found:
                await acceptance.wait_disposable_setup(SimpleNamespace(command=command))
            self.assert_setup_diagnostic(found.exception, "store_class_list_malformed", "readiness",
                                         readiness_reads=1, pending_store_classes=None)
        shapes = (
            {"core_running": True, "storage_pending": True, "hooks_intact": True, "pending_store_classes": [{}]},
            ready_value(extra=True), ready_value(core_running="yes"), [], None,
            ready_value(last_start_refusal={"cause": "free text from a key", "pending_store_classes": []}),
            ready_value(last_start_refusal={"cause": "core_not_running", "pending_store_classes": [{}]}),
            ready_value(last_start_refusal={"cause": "core_not_running"}),
        )
        for value in shapes:
            async def command(_payload, value=value):
                return value
            with self.subTest(value=value), self.assertRaises(acceptance.ObservationError) as found:
                await acceptance.wait_disposable_setup(SimpleNamespace(command=command))
            self.assert_setup_diagnostic(found.exception, "readiness_shape_invalid", "readiness", readiness_reads=1)
        async def unhooked(_payload):
            return ready_value(hooks_intact=False)
        with self.assertRaises(acceptance.ObservationError) as found:
            await acceptance.wait_disposable_setup(SimpleNamespace(command=unhooked))
        self.assert_setup_diagnostic(found.exception, "readiness_hooks_not_intact", "readiness")
        # Storage pending must agree with its class list.
        async def disagree(_payload):
            return ready_value(storage_pending=False, pending_store_classes=["lovelace"])
        with self.assertRaises(acceptance.ObservationError) as found:
            await acceptance.wait_disposable_setup(SimpleNamespace(command=disagree))
        self.assert_setup_diagnostic(found.exception, "store_class_list_malformed", "readiness")

    async def test_start_transport_timeout_and_invalid_responses_are_structured(self):
        identity = "0" * 32

        def wire(start, ready):
            async def command(payload):
                return await (start if payload["type"].endswith("/start") else ready)(payload)
            return SimpleNamespace(command=command)

        async def timeout(_payload):
            raise RuntimeError("Home Assistant request failed") from TimeoutError()

        async def failed(_payload):
            raise RuntimeError("Home Assistant request failed")

        async def settled(_payload):
            return ready_value()

        async def pending(_payload):
            return ready_value(storage_pending=True, pending_store_classes=["frontend"])

        async def malformed(_payload):
            return ready_value(pending_store_classes=[{}])

        async def hangs(_payload):
            await asyncio.sleep(3600)

        real_timeout = asyncio.timeout
        cases = (
            (timeout, settled, "start_timeout", 1, True, [], "no_refusal"),
            (failed, pending, "start_transport_error", 1, True, ["frontend"], "no_refusal"),
            (failed, failed, "start_transport_error", 0, None, None, "recovery_unavailable"),
            (failed, malformed, "start_transport_error", 1, None, None, "ready_recovery_malformed"),
            (failed, hangs, "start_transport_error", 0, None, None, "recovery_unavailable"),
        )
        for start, ready, cause, reads, running, classes, recovery in cases:
            with self.subTest(start=start.__name__, ready=ready.__name__), \
                    patch("asyncio.timeout", lambda seconds: real_timeout(min(seconds, 0.05))), \
                    self.assertRaises(acceptance.ObservationError) as found:
                await acceptance.start_interval(wire(start, ready), "control", window="baseline_export",
                                                window_index=2)
            self.assert_setup_diagnostic(found.exception, cause, "start", window="baseline_export", window_index=2,
                                         readiness_reads=reads, core_running=running,
                                         pending_store_classes=classes, start_recovery=recovery)
            self.assertIsInstance(found.exception.__cause__, RuntimeError)
        for value in ({"model": "alarmo-core-interval-v1", "interval_id": identity},
                      {"model": acceptance.OBSERVER_MODEL, "interval_id": SYNTHETIC_IDS[1]},
                      {"model": acceptance.OBSERVER_MODEL, "interval_id": identity, "extra": 1},
                      {"model": acceptance.OBSERVER_MODEL}, [identity], None):
            async def returns(_payload, value=value):
                return value
            with self.subTest(value=value), self.assertRaises(acceptance.ObservationError) as found:
                await acceptance.start_interval(wire(returns, settled), "control")
            self.assert_setup_diagnostic(found.exception, "start_response_invalid", "start", readiness_reads=0,
                                         start_recovery=None)
        async def started(_payload):
            return {"model": acceptance.OBSERVER_MODEL, "interval_id": identity}
        self.assertEqual(await acceptance.start_interval(wire(started, failed), "control"), identity)

    async def test_cancellation_propagates_from_readiness_start_read_back_and_observer(self):
        async def cancelled(_payload):
            raise asyncio.CancelledError()

        async def failed(_payload):
            raise RuntimeError("Home Assistant request failed")

        with self.assertRaises(asyncio.CancelledError):
            await acceptance.wait_disposable_setup(SimpleNamespace(command=cancelled))
        with self.assertRaises(asyncio.CancelledError):
            await acceptance.start_interval(SimpleNamespace(command=cancelled), "control")
        async def start_fails_then_cancelled(payload):
            return await (failed if payload["type"].endswith("/start") else cancelled)(payload)
        with self.assertRaises(asyncio.CancelledError):
            await acceptance.start_interval(SimpleNamespace(command=start_fails_then_cancelled), "control")

        def cancel(function, *args):
            raise asyncio.CancelledError()
        hass, ledger, handlers, _modules = await observer_runtime(hashes=cancel)
        connection = SimpleNamespace(send_error=Mock(), send_result=Mock())
        with self.assertRaises(asyncio.CancelledError):
            await handlers["start"](hass, connection, {"id": 1, "kind": "control", "attempt_id": ATTEMPTS[0]})
        connection.send_error.assert_not_called()
        connection.send_result.assert_not_called()
        self.assertIsNone(ledger.active)
        self.assertIsNone(ledger.start_refusal())

    def test_every_setup_cause_maps_to_the_fixed_shape(self):
        self.assertEqual(observer.START_REFUSALS, acceptance.START_REFUSALS)
        self.assertLessEqual(set(acceptance.START_REFUSALS), set(acceptance.SETUP_FAILURES))
        self.assertEqual(set(acceptance.SETUP_FAILURES.values()),
                         {"setup_not_settled", "setup_observation_unavailable", "interval_start_refused"})
        for cause, condition in acceptance.SETUP_FAILURES.items():
            for phase in ("readiness", "start"):
                with self.subTest(cause=cause, phase=phase):
                    error = acceptance.setup_failure(cause, phase, window="inspection", window_index=0,
                                                     readiness_reads=3, core_running=True,
                                                     pending_store_classes=["lovelace", SYNTHETIC_IDS[0], {}])
                    self.assertIsInstance(error, acceptance.ObservationError)
                    diagnostic = self.assert_setup_diagnostic(error, cause, phase, window="inspection",
                                                              window_index=0, readiness_reads=3)
                    self.assertEqual(diagnostic["condition"], condition)
                    self.assertEqual(diagnostic["pending_store_classes"], ["lovelace", "invalid", "invalid"])
        # Unknown, hostile or oversized inputs never reach the diagnostic.
        error = acceptance.setup_failure(SYNTHETIC_IDS[0], "/config", window="/config/secret", window_index=10 ** 6,
                                         readiness_reads="x", core_running="yes",
                                         pending_store_classes=["lovelace"] * 40)
        diagnostic = self.assert_setup_diagnostic(error, "start_internal_error", "invalid", window=None,
                                                  window_index=None, readiness_reads=None, core_running=None)
        self.assertEqual(len(diagnostic["pending_store_classes"]), acceptance.MAX_DIAGNOSTIC_ITEMS)

    def test_every_setup_raise_site_emits_the_structured_diagnostic(self):
        """Enumerates every raise in setup, readiness and start, so a new path
        cannot skip the diagnostic; the checker itself is mutation-tested."""
        import ast
        source = Path(acceptance.__file__).read_text(encoding="utf-8")
        raises, problems = raise_sites(source, SETUP_FUNCTIONS)
        self.assertEqual(problems, [])
        # Pinned: a new raise site must be added here deliberately, and is checked above.
        self.assertEqual(raises, 5)
        # Every literal cause the client can emit is in the fixed vocabulary, and
        # together with the observer's refusals it is the whole vocabulary.
        literals = set()
        for name in (*SETUP_FUNCTIONS, "readiness_problem"):
            function = next(node for node in ast.walk(ast.parse(source))
                            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)
            literals |= {node.value for node in ast.walk(function) if isinstance(node, ast.Constant)
                         and type(node.value) is str and node.value in acceptance.SETUP_FAILURES}
            literals |= {node.value for node in ast.walk(function) if isinstance(node, ast.Constant)
                         and type(node.value) is str and re.fullmatch(r"(readiness|start)_[a-z_]+|store_class_[a-z_]+",
                                                                      node.value)
                         and node.value not in {"readiness_reads", "start_internal_error", "start_recovery"}}
        self.assertLessEqual(literals, set(acceptance.SETUP_FAILURES))
        self.assertEqual(literals | set(acceptance.START_REFUSALS), set(acceptance.SETUP_FAILURES))
        observer_source = acceptance.OBSERVER.read_text(encoding="utf-8")
        causes, problems = observer_start_sites(observer_source)
        self.assertEqual(problems, [])
        self.assertEqual(causes, set(observer.START_REFUSALS))
        # Mutations: a plain raise, a swallowed BaseException, an unstructured
        # read-back result or a free-text observer refusal are all caught.
        mutations = (
            ('    raise failure("readiness_budget_exhausted")', '    raise ValueError("setup did not settle")'),
            ('        raise setup_failure("start_response_invalid"', '        raise RuntimeError("start_response_invalid"'),
            ("    except Exception as exc:  # CancelledError is not an Exception and propagates\n"
             "        raise await _start_failure(",
             "    except BaseException as exc:\n        raise await _start_failure("),
            ('    return setup_failure(cause, "start"', '    return ValueError(cause, "start"'),
        )
        for old, new in mutations:
            with self.subTest(mutation=new):
                self.assertEqual(source.count(old), 1)
                self.assertNotEqual(raise_sites(source.replace(old, new), SETUP_FUNCTIONS)[1], [])
        for old, new in (('raise Refusal("core_not_running")', 'raise ValueError("core not running")'),
                         ('raise Refusal("hooks_not_intact")', 'raise Refusal("hooks broken for " + key)'),
                         ("        except Exception:  # cancellation", "        except BaseException:  # cancellation")):
            with self.subTest(mutation=new):
                self.assertEqual(observer_source.count(old), 1)
                causes, problems = observer_start_sites(observer_source.replace(old, new))
                self.assertTrue(problems or causes != set(observer.START_REFUSALS))

    async def test_setup_failures_reach_the_alarmo_lane_diagnostic(self):
        for cause in ("setup_not_settled_late_start", "readiness_timeout", "store_class_list_malformed"):
            failure = acceptance.setup_failure(cause, "start", window="inspection", readiness_reads=1,
                                               core_running=True, pending_store_classes=["lovelace"])
            with self.subTest(cause=cause), patch.object(acceptance, "_run_disposable", side_effect=failure), \
                    self.assertRaises(acceptance.ObservationError) as found:
                await acceptance.run_disposable(None, None, fixture_receipt=None, expected_image=None,
                                                core_version=CORE)
            self.assertEqual(found.exception.contract_missing_key, "alarmo_inspection")
            self.assertEqual(found.exception.contract_diagnostic, {"observation_failure": failure.diagnostic})
            self.assertEqual(found.exception.contract_diagnostic["observation_failure"]["cause"], cause)


class _Exploding:
    """Any use beyond identity raises: stands in for an unforeseen decoder error."""

    def __iter__(self):
        raise RuntimeError("synthetic decoder failure")

    def __eq__(self, other):
        raise RuntimeError("synthetic decoder failure")

    __hash__ = None


def mutated_acceptance(old, new):
    """The client module with one exact source replacement, for mutation tests."""
    source = Path(acceptance.__file__).read_text(encoding="utf-8")
    assert source.count(old) == 1, old
    module = ModuleType("mutated_alarmo_acceptance")
    module.__file__ = acceptance.__file__
    exec(compile(source.replace(old, new), acceptance.__file__, "exec"), module.__dict__)
    return module


class StartRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """HAMCP-180 R1 residual and R4: the one ``/ready`` read-back after a failed
    ``/start`` is fully validated before use, never escapes the structured
    diagnostic, and only a refusal from the current attempt names the cause."""

    assert_setup_diagnostic = SetupFailureDiagnosticTests.assert_setup_diagnostic

    def recovery_wire(self, start_error, ready, calls):
        async def command(payload):
            calls.append(dict(payload))
            if payload["type"].endswith("/start"):
                raise start_error
            return ready(payload) if callable(ready) else ready
        return SimpleNamespace(command=command)

    def assert_one_read_back(self, calls):
        self.assertEqual([call["type"] for call in calls],
                         ["alarmo_interval_observer/start", "alarmo_interval_observer/ready"])
        self.assertRegex(calls[0]["attempt_id"], r"\A[0-9a-f]{32}\Z")
        self.assertEqual(set(calls[0]), {"type", "kind", "attempt_id"})

    async def test_codex_probe_broken_hooks_and_malformed_read_back_keep_the_current_cause(self):
        # Codex delta probe made permanent: this raised TypeError at 3c255e5.
        calls = []
        wire = self.recovery_wire(TimeoutError("synthetic"), ready_value(
            hooks_intact=False, pending_store_classes=None, last_start_refusal={}), calls)
        with self.assertRaises(acceptance.ObservationError) as found:
            await acceptance.start_interval(wire, "control")
        self.assert_setup_diagnostic(found.exception, "start_timeout", "start", readiness_reads=1,
                                     core_running=None, pending_store_classes=None,
                                     start_recovery="ready_recovery_malformed")
        self.assertIsInstance(found.exception.__cause__, TimeoutError)
        self.assert_one_read_back(calls)

    async def test_malformed_read_back_matrix_stays_structured_with_the_current_cause(self):
        attempt = ATTEMPTS[0]
        valid_refusal = {"attempt_id": attempt, "cause": "core_not_running", "pending_store_classes": []}
        classes = (None, [{}], [None], [1], [["lovelace"]], "lovelace", {"lovelace": True},
                   [SYNTHETIC_IDS[0]], ["lovelace"] * 17, ["person", "lovelace"], _Exploding())
        refusals = (
            {}, [], "core_not_running", 7, SYNTHETIC_IDS[0], _Exploding(),
            {"cause": "core_not_running", "pending_store_classes": []},
            {"attempt_id": attempt, "cause": "core_not_running"},
            {**valid_refusal, "extra": True},
            {**valid_refusal, "cause": 7}, {**valid_refusal, "cause": ["core_not_running"]},
            {**valid_refusal, "cause": {"core_not_running": 1}}, {**valid_refusal, "cause": SYNTHETIC_IDS[0]},
            {**valid_refusal, "cause": "observer_refused"},
            {**valid_refusal, "attempt_id": 7}, {**valid_refusal, "attempt_id": None},
            {**valid_refusal, "attempt_id": SYNTHETIC_IDS[1]}, {**valid_refusal, "attempt_id": attempt.upper()},
            {**valid_refusal, "attempt_id": attempt + "\n"}, {**valid_refusal, "attempt_id": [attempt]},
            {**valid_refusal, "pending_store_classes": None}, {**valid_refusal, "pending_store_classes": [{}]},
            {**valid_refusal, "pending_store_classes": "lovelace"},
            {**valid_refusal, "pending_store_classes": [SYNTHETIC_IDS[0]]},
        )
        values = [None, [], "ready", 7, _Exploding(), {}, ready_value(extra=True),
                  {key: item for key, item in ready_value().items() if key != "last_start_refusal"},
                  ready_value(core_running="yes"), ready_value(hooks_intact=None), ready_value(storage_pending=0)]
        for hooks in (True, False):
            values += [ready_value(hooks_intact=hooks, storage_pending=True, pending_store_classes=item)
                       for item in classes]
            values += [ready_value(hooks_intact=hooks, last_start_refusal=item) for item in refusals]
            values += [ready_value(hooks_intact=hooks, storage_pending=False, pending_store_classes=["lovelace"])]
        errors = ((TimeoutError("synthetic"), "start_timeout"),
                  (RuntimeError("Home Assistant request failed"), "start_transport_error"))
        for index, value in enumerate(values):
            for error, cause in errors:
                calls = []
                with self.subTest(index=index, cause=cause), \
                        patch("uuid.uuid4", return_value=SimpleNamespace(hex=attempt)), \
                        self.assertRaises(acceptance.ObservationError) as found:
                    await acceptance.start_interval(self.recovery_wire(error, value, calls), "control",
                                                    window="baseline_export", window_index=3)
                diagnostic = self.assert_setup_diagnostic(
                    found.exception, cause, "start", window="baseline_export", window_index=3, readiness_reads=1,
                    core_running=None, pending_store_classes=None, start_recovery="ready_recovery_malformed")
                self.assertIs(found.exception.__cause__, error)
                self.assert_one_read_back(calls)
                self.assertNotIn(attempt, json.dumps(diagnostic))

    async def test_a_well_formed_broken_hooks_read_back_still_attributes_the_current_refusal(self):
        attempt = ATTEMPTS[1]
        for refusal, cause, recovery, classes in (
                ({"attempt_id": attempt, "cause": "hooks_not_intact", "pending_store_classes": ["lovelace"]},
                 "hooks_not_intact", "current_refusal", ["lovelace"]),
                (None, "start_transport_error", "no_refusal", []),
                ({"attempt_id": ATTEMPTS[2], "cause": "hooks_not_intact", "pending_store_classes": []},
                 "start_transport_error", "stale_refusal_ignored", [])):
            calls = []
            wire = self.recovery_wire(RuntimeError("Home Assistant request failed"),
                                      ready_value(hooks_intact=False, last_start_refusal=refusal), calls)
            with self.subTest(recovery=recovery), patch("uuid.uuid4", return_value=SimpleNamespace(hex=attempt)), \
                    self.assertRaises(acceptance.ObservationError) as found:
                await acceptance.start_interval(wire, "control")
            self.assert_setup_diagnostic(found.exception, cause, "start", readiness_reads=1, core_running=True,
                                         pending_store_classes=classes, start_recovery=recovery)
            self.assert_one_read_back(calls)

    async def test_readiness_wait_with_broken_hooks_and_malformed_fields_is_structured(self):
        for hooks in (True, False):
            for changes, cause in (
                    ({"storage_pending": True, "pending_store_classes": None}, "store_class_list_malformed"),
                    ({"storage_pending": True, "pending_store_classes": _Exploding()}, "store_class_list_malformed"),
                    ({"last_start_refusal": {}}, "readiness_shape_invalid"),
                    ({"last_start_refusal": _Exploding()}, "readiness_shape_invalid"),
                    ({"last_start_refusal": {"attempt_id": 7, "cause": "core_not_running",
                                             "pending_store_classes": []}}, "readiness_shape_invalid"),
                    ({}, None if hooks else "readiness_hooks_not_intact")):
                async def command(_payload, changes=changes):
                    return ready_value(hooks_intact=hooks, **changes)
                with self.subTest(hooks=hooks, cause=cause):
                    if cause is None:
                        self.assertEqual(await acceptance.wait_disposable_setup(SimpleNamespace(command=command)),
                                         {"result": "PASS", "readiness_reads": 1})
                        continue
                    with self.assertRaises(acceptance.ObservationError) as found:
                        await acceptance.wait_disposable_setup(SimpleNamespace(command=command))
                    self.assert_setup_diagnostic(found.exception, cause, "readiness", readiness_reads=1,
                                                 start_recovery=None)

    async def test_an_unforeseen_decoder_error_is_a_fixed_cause_on_both_paths(self):
        def broken(*_args, **_kwargs):
            raise RuntimeError("synthetic decoder failure")

        self.assertEqual(acceptance.decode_readiness(ready_value()), (None, {
            "core_running": True, "storage_pending": False, "pending_store_classes": [], "refusal": None}))
        with patch.object(acceptance, "readiness_problem", broken):
            self.assertEqual(acceptance.decode_readiness(ready_value()), ("readiness_shape_invalid", None))
            async def ready(_payload):
                return ready_value()
            with self.assertRaises(acceptance.ObservationError) as found:
                await acceptance.wait_disposable_setup(SimpleNamespace(command=ready))
            self.assert_setup_diagnostic(found.exception, "readiness_shape_invalid", "readiness", readiness_reads=1)
            calls = []
            with self.assertRaises(acceptance.ObservationError) as found:
                await acceptance.start_interval(self.recovery_wire(TimeoutError(), ready_value(), calls), "control")
            self.assert_setup_diagnostic(found.exception, "start_timeout", "start", readiness_reads=1,
                                         start_recovery="ready_recovery_malformed")
            self.assert_one_read_back(calls)
        # The /start result is decoded by the same total boundary.
        for value in (_Exploding(), {"model": _Exploding(), "interval_id": "0" * 32}):
            async def returns(_payload, value=value):
                return value
            with self.subTest(value=type(value).__name__), self.assertRaises(acceptance.ObservationError) as found:
                await acceptance.start_interval(SimpleNamespace(command=returns), "control")
            self.assert_setup_diagnostic(found.exception, "start_response_invalid", "start", readiness_reads=0)

    async def test_malformed_read_back_reaches_both_lane_diagnostics(self):
        import automation_baseline_contract_acceptance as baseline
        malformed = ready_value(hooks_intact=False, pending_store_classes=None, last_start_refusal={})

        async def lane(*_args, **_kwargs):
            await acceptance.start_interval(self.recovery_wire(TimeoutError(), malformed, []), "control",
                                            window="baseline_export", window_index=1)

        with patch.object(acceptance, "_run_disposable", lane), \
                self.assertRaises(acceptance.ObservationError) as found:
            await acceptance.run_disposable(None, None, fixture_receipt=None, expected_image=None, core_version=CORE)
        observed = found.exception.contract_diagnostic["observation_failure"]
        self.assertEqual((observed["cause"], observed["start_recovery"]), ("start_timeout", "ready_recovery_malformed"))
        with patch.object(baseline, "_run_disposable", lane), \
                self.assertRaises(acceptance.ObservationError) as found:
            await baseline.run_disposable(None, expected_image=None, core_version=CORE)
        observed = found.exception.contract_diagnostic["observation_failure"]
        self.assertEqual((observed["cause"], observed["start_recovery"], observed["window_index"]),
                         ("start_timeout", "ready_recovery_malformed", 1))
        assert_bounded(self, observed)

    async def test_codex_probe_a_stale_refusal_does_not_replace_the_current_timeout(self):
        # Codex delta probe made permanent: 3c255e5 reported core_not_running here.
        calls = []
        stale = {"attempt_id": ATTEMPTS[2], "cause": "core_not_running", "pending_store_classes": []}
        wire = self.recovery_wire(TimeoutError("synthetic"), ready_value(last_start_refusal=stale), calls)
        with self.assertRaises(acceptance.ObservationError) as found:
            await acceptance.start_interval(wire, "control")
        self.assert_setup_diagnostic(found.exception, "start_timeout", "start", readiness_reads=1, core_running=True,
                                     pending_store_classes=[], start_recovery="stale_refusal_ignored")
        self.assert_one_read_back(calls)

    async def test_a_refusal_counts_only_for_its_own_attempt_on_the_real_observer(self):
        hass, ledger, handlers, _modules = await observer_runtime(state="not_running")

        class Lost(Wire):
            """``/start`` fails in transport; ``before`` says whether the
            observer handler ran first (a lost response) or never (a lost request)."""

            def __init__(self, before):
                super().__init__(hass, handlers)
                self.before = before

            async def command(self, payload):
                if not payload["type"].endswith("/start"):
                    return await super().command(payload)
                self.payloads.append(dict(payload))
                if self.before:
                    connection = SimpleNamespace(send_result=Mock(), send_error=Mock())
                    await self.handlers["start"](self.hass, connection, {"id": 1, **payload})
                raise RuntimeError("Home Assistant request failed") from TimeoutError()

        async def attempt(wire):
            with self.assertRaises(acceptance.ObservationError) as found:
                await acceptance.start_interval(wire, "control", window="baseline_export", window_index=0)
            self.assert_one_read_back(wire.payloads)
            self.assertNotIn(wire.payloads[0]["attempt_id"], json.dumps(found.exception.diagnostic))
            return found.exception.diagnostic

        # 1. A current refusal names its own cause.
        first = Wire(hass, handlers)
        current = await attempt(first)
        self.assertEqual((current["cause"], current["start_recovery"]), ("core_not_running", "current_refusal"))
        self.assertEqual(ledger.start_refusal()["attempt_id"], first.payloads[0]["attempt_id"])
        # 2. The next start is lost before the observer: the retained refusal is stale.
        hass.state = "running"
        lost = Lost(before=False)
        stale = await attempt(lost)
        self.assertEqual((stale["condition"], stale["cause"], stale["start_recovery"], stale["core_running"]),
                         ("setup_observation_unavailable", "start_timeout", "stale_refusal_ignored", True))
        self.assertNotEqual(lost.payloads[0]["attempt_id"], first.payloads[0]["attempt_id"])
        # 3. A foreign attempt's refusal is equally ignored.
        ledger.refuse_start("hooks_not_intact", [], ATTEMPTS[2])
        foreign = await attempt(Lost(before=False))
        self.assertEqual((foreign["cause"], foreign["start_recovery"]), ("start_timeout", "stale_refusal_ignored"))
        # 4. The observer refused this attempt but its response was lost: still attributed.
        hass.state = "not_running"
        refused = await attempt(Lost(before=True))
        self.assertEqual((refused["cause"], refused["start_recovery"]), ("core_not_running", "current_refusal"))
        # 5. The interval opened but its response was lost: no refusal, the timeout stands.
        hass.state = "running"
        opened = await attempt(Lost(before=True))
        self.assertEqual((opened["cause"], opened["start_recovery"]), ("start_timeout", "no_refusal"))
        self.assertIsNotNone(ledger.active)
        self.assertIsNone(ledger.start_refusal())

    async def test_removing_the_attempt_correlation_is_caught(self):
        stale = {"attempt_id": ATTEMPTS[2], "cause": "core_not_running", "pending_store_classes": []}
        for old, new in (('            elif refusal["attempt_id"] == attempt_id:', "            elif True:"),
                         ('            elif refusal["attempt_id"] == attempt_id:',
                          '            elif refusal["attempt_id"] or attempt_id:')):
            with self.subTest(mutation=new):
                mutated = mutated_acceptance(old, new)
                with self.assertRaises(mutated.ObservationError) as found:
                    await mutated.start_interval(self.recovery_wire(
                        TimeoutError(), ready_value(last_start_refusal=stale), []), "control")
                # The stale-refusal test above would fail on this cause.
                self.assertEqual(found.exception.diagnostic["cause"], "core_not_running")
        # Observer mutation: a refusal recorded without the request's attempt is never current.
        hass, _ledger, handlers, _modules = await observer_runtime(state="not_running")
        original = observer.Ledger.refuse_start
        with patch.object(observer.Ledger, "refuse_start",
                          lambda self, cause, classes, _attempt: original(self, cause, classes, ATTEMPTS[2])), \
                self.assertRaises(acceptance.ObservationError) as found:
            await acceptance.start_interval(Wire(hass, handlers), "control")
        self.assertEqual((found.exception.diagnostic["cause"], found.exception.diagnostic["start_recovery"]),
                         ("start_transport_error", "stale_refusal_ignored"))

    def test_attempt_identifiers_are_fresh_and_bounded_by_the_observer_schema(self):
        source = acceptance.OBSERVER.read_text(encoding="utf-8")
        self.assertIn('vol.Required("attempt_id"): vol.Match("^" + ATTEMPT_ID + r"\\Z")', source)
        self.assertEqual(observer.ATTEMPT_ID, acceptance.ATTEMPT_ID)
        ledger = observer.Ledger(lambda: 0)
        for attempt in (None, 7, "A" * 32, "a" * 31, "a" * 32 + "\n", SYNTHETIC_IDS[1]):
            with self.subTest(attempt=attempt):
                self.assertEqual(ledger.refuse_start("core_not_running", [], attempt)["attempt_id"], "0" * 32)
        self.assertEqual(ledger.refuse_start("core_not_running", [], ATTEMPTS[0])["attempt_id"], ATTEMPTS[0])
        self.assertEqual(set(acceptance.START_RECOVERY), {"recovery_unavailable", "ready_recovery_malformed",
                                                          "no_refusal", "current_refusal", "stale_refusal_ignored"})


class CodexDeltaProbeTests(unittest.TestCase):
    """The reviewer's AST probes at 3c255e5, made permanent (HAMCP-180 R1)."""

    def test_an_added_raise_in_an_enumerated_function_is_caught(self):
        source = Path(acceptance.__file__).read_text(encoding="utf-8")
        mutated = source.replace("    for attempt in range(41):",
                                 '    raise RuntimeError("synthetic")\n    for attempt in range(41):')
        self.assertNotEqual(mutated, source)
        self.assertTrue(raise_sites(mutated, SETUP_FUNCTIONS)[1])

    def test_a_raise_in_a_reached_helper_is_caught(self):
        source = Path(acceptance.__file__).read_text(encoding="utf-8")
        mutated = source.replace('    booleans = ("core_running",',
                                 '    raise RuntimeError("synthetic")\n    booleans = ("core_running",')
        self.assertNotEqual(mutated, source)
        self.assertTrue(raise_sites(mutated, SETUP_FUNCTIONS)[1], "readiness_problem is not enumerated")

    def test_the_boundary_checker_catches_each_bypass(self):
        source = Path(acceptance.__file__).read_text(encoding="utf-8")
        self.assertEqual(boundary_sites(source, SETUP_FUNCTIONS), [])
        self.assertLessEqual({*DECODERS, *VALIDATORS}, set(setup_helpers(source, SETUP_FUNCTIONS)))
        mutations = (
            # The 3c255e5 shape: a setup function validating and reading the response itself.
            ("        _problem, ready = decode_readiness(value)",
             '        _problem, ready = readiness_problem(value), value\n        ready["core_running"]'),
            # A decoder reading observer data outside its guard.
            ("    try:\n        problem = readiness_problem(value, store_classes=store_classes)",
             '    value["core_running"]\n    try:\n        problem = readiness_problem(value, store_classes=store_classes)'),
            # A decoder whose guard no longer catches Exception.
            ("        return problem, state\n    except Exception:", "        return problem, state\n    except TypeError:"),
            # A helper that raises instead of returning a fixed cause.
            ('        return "readiness_shape_invalid"\n    if store_classes:',
             '        raise ValueError("readiness_shape_invalid")\n    if store_classes:'),
            # A setup function that bypasses the start-response decoder.
            ("    interval_id = _start_response(value)", '    interval_id = value["interval_id"]'),
        )
        for old, new in mutations:
            with self.subTest(mutation=new):
                self.assertEqual(source.count(old), 1, old)
                self.assertNotEqual(boundary_sites(source.replace(old, new), SETUP_FUNCTIONS), [])

    def test_extra_adversarial_store_keys_redact(self):
        keys = ["lоvelace.synthetic", "cоre.uuid", "/config/lovelace.secret", "../core.uuid", "a" * 100000,
                "CORE.uuid", "Core.config", "LOVELACE.secret", "core./config/private", "lovelace/../synthetic",
                "Knx.synthetic", "core.ｕuid"]
        expected = ["unclassified", "unclassified", "unclassified", "unclassified", "unclassified", "unclassified",
                    "unclassified", "unclassified", "core", "lovelace", "unclassified", "core"]
        for key, want in zip(keys, expected):
            with self.subTest(key=key[:40]):
                self.assertEqual(observer.store_class(key), want)
                self.assertEqual(acceptance._store_class(key), "invalid")


def legacy_store_class(key):
    """Mutation: the reviewed-head (48e7fec) shape-based classifier that R2 replaced."""
    if type(key) is not str:
        return "unclassified"
    if key in observer.STORE_KEYS:
        return key
    head, _dot, rest = key.partition(".")
    if head == "core" and re.fullmatch(r"[a-z]{1,32}(?:_[a-z]{1,32}){0,3}", rest):
        return "core." + rest
    return head if re.fullmatch(r"[a-z][a-z0-9_]{0,31}", head) else "unclassified"


IDENTIFIER_KEYS = {
    SYNTHETIC_IDS[0]: "unclassified",
    SYNTHETIC_IDS[0] + ".lovelace": "unclassified",
    SYNTHETIC_IDS[1]: "unclassified",
    "light.kitchen": "unclassified",
    "sensor.kitchen_42_temperature": "unclassified",
    "custom_integration_0123abcd": "unclassified",
    "my_integration.storage": "unclassified",
    "person_" + SYNTHETIC_IDS[0]: "person",
    "lovelace.dashboard_" + SYNTHETIC_IDS[2]: "lovelace",
    "frontend.user_data_" + SYNTHETIC_IDS[0]: "frontend",
    "mobile_app/" + SYNTHETIC_IDS[3]: "mobile_app",
    "homekit_controller-entity-map": "homekit_controller",
    "homekit." + SYNTHETIC_IDS[1]: "homekit",
    "homekitx": "unclassified",
    "lovelace" + SYNTHETIC_IDS[2]: "unclassified",
    "Lovelace.dashboard": "unclassified",
    "lovelace\n" + SYNTHETIC_IDS[3]: "unclassified",
    "core." + SYNTHETIC_IDS[0]: "core",
    "core.entity_registry_0123": "core",
    "core.entity_registry/../" + SYNTHETIC_IDS[3]: "core",
    "core.restore_state_extra": "core",
    "core.custom_component_cache_backup_store": "core",
    "core." + "a" * 40: "core",
    "core": "unclassified",
    "/config/.storage/core.entity_registry": "unclassified",
    "../../" + SYNTHETIC_IDS[3]: "unclassified",
    "core.analytics": "core.analytics",
    "core.uuid": "core.uuid",
    "alarmo.storage": "alarmo.storage",
    "alarmo.storage.bak": "alarmo",
}


class StoreClassRedactionTests(unittest.IsolatedAsyncioTestCase):
    """HAMCP-180 R2: a store class comes only from the fixed vocabulary; an
    identifier-shaped key is never echoed in any output."""

    def test_vocabulary_is_fixed_shared_and_identifier_free(self):
        self.assertEqual(observer.STORE_CLASSES, acceptance.STORE_CLASSES)
        self.assertEqual(observer.CORE_STORE_NAMES, set(acceptance.CORE_STORE_NAMES))
        self.assertEqual(observer.STORE_DOMAINS, set(acceptance.STORE_DOMAINS))
        self.assertLessEqual(observer.STORE_KEYS, observer.STORE_CLASSES)
        for value in observer.STORE_CLASSES:
            # No digit, hex run, separator or path can be emitted, whatever the input.
            self.assertRegex(value, r"\A[a-z_]{1,32}(?:\.[a-z_]{1,32})?\Z")

    def test_identifier_shaped_keys_map_to_a_class_never_the_key(self):
        # Codex probe made permanent, plus adversarial keys. All synthetic.
        for key, expected in IDENTIFIER_KEYS.items():
            with self.subTest(key=key):
                value = observer.store_class(key)
                self.assertEqual(value, expected)
                self.assertIn(value, acceptance.STORE_CLASSES)
                if key not in acceptance.STORE_CLASSES:
                    self.assertNotEqual(value, key)
                for identifier in SYNTHETIC_IDS:
                    self.assertNotIn(identifier, value)
        for key in (b"lovelace", type("Key", (str,), {})("lovelace"), ["lovelace"], None):
            self.assertEqual(observer.store_class(key), "unclassified")

    def leaks(self, classify=None, summarize=None):
        """Every observer and client output an unnamed store reaches, as text."""
        with patch.object(observer, "store_class", classify or observer.store_class), \
                patch.object(acceptance, "_store_class", summarize or acceptance._store_class):
            ledger = observer.Ledger(lambda: 0)
            stores = [pending_store(key) for key in IDENTIFIER_KEYS]
            for store in stores:
                ledger.track(store)
            pending = ledger.pending_store_classes()
            hashes = {key: None for key in acceptance.STORE_HASH_KEYS}
            identity = ledger.start("control", dict(hashes))
            for store in stores:
                ledger.record("storage_delay_save", store=store)
            refusal = ledger.refuse_start("setup_not_settled_late_start", pending, ATTEMPTS[0])
            value = ledger.finish(identity, dict(hashes),
                                  {key: True for key in acceptance.OBSERVER_HOOKS})
            value.update(core_version=CORE, observer_sha256=sha256(acceptance.OBSERVER.read_bytes()))
            try:
                acceptance.verify_interval(value, identity, [], kind="control", core_version=CORE,
                                           window="baseline_export", window_index=1)
            except acceptance.ObservationError as error:
                diagnostic = error.diagnostic
            else:
                self.fail("an untolerated store write must fail the interval")
            setup = acceptance.setup_failure("setup_not_settled_late_start", "start", core_running=True,
                                             pending_store_classes=pending).diagnostic
            outputs = {"pending": pending, "refusal": refusal, "events": value["events"],
                       "diagnostic": diagnostic, "setup": setup}
        return outputs, json.dumps(outputs)

    def assert_no_identifier(self, text):
        for key in IDENTIFIER_KEYS:
            if key not in acceptance.STORE_CLASSES:
                self.assertNotIn(json.dumps(key), text)
        for identifier in (*SYNTHETIC_IDS, "kitchen", "dashboard", "entity_registry_0123", "/config", "DO_NOT_RECORD"):
            self.assertNotIn(identifier, text)

    def test_no_output_echoes_an_identifier_shaped_key(self):
        outputs, text = self.leaks()
        self.assert_no_identifier(text)
        self.assertEqual(outputs["diagnostic"]["condition"], "untolerated_store")
        self.assertEqual(outputs["diagnostic"]["event_count"], len(IDENTIFIER_KEYS))
        assert_bounded(self, outputs["diagnostic"])
        assert_bounded(self, outputs["setup"])
        self.assertLessEqual(set(outputs["pending"]), acceptance.STORE_CLASSES)
        self.assertEqual({event.get("store_class") for event in outputs["events"]} - {None},
                         set(IDENTIFIER_KEYS.values()) - set(observer.STORE_KEYS))

    def test_mutations_echoing_the_key_or_trusting_its_shape_are_caught(self):
        for classify in (lambda key: key, legacy_store_class):
            with self.subTest(classify=classify), self.assertRaises(AssertionError):
                self.assert_no_identifier(self.leaks(classify=classify)[1])
        # Client mutation: a hostile observer class echoed into the summary.
        hostile = {"kind": "storage_write", "origin": "background", "store": "other",
                   "store_class": SYNTHETIC_IDS[0]}
        self.assertEqual(acceptance._event_summary(hostile)["store_class"], "invalid")
        with patch.object(acceptance, "_store_class", lambda value: value):
            self.assertEqual(acceptance._event_summary(hostile)["store_class"], SYNTHETIC_IDS[0])
            with self.assertRaises(AssertionError):
                self.assert_no_identifier(self.leaks(classify=lambda key: key,
                                                     summarize=lambda value: value)[1])

    async def test_hostile_observer_classes_are_invalid_in_every_client_output(self):
        value = receipt()
        value["events"] = [{"kind": "storage_write", "origin": "background", "store": "other",
                            "store_class": item} for item in (*SYNTHETIC_IDS, "core." + SYNTHETIC_IDS[0])]
        with self.assertRaises(acceptance.ObservationError) as found:
            acceptance.verify_interval(value, value["interval_id"], [], core_version=CORE)
        text = json.dumps(found.exception.diagnostic)
        self.assertEqual(found.exception.diagnostic["event"]["store_class"], "invalid")
        self.assertEqual({row["store_class"] for row in found.exception.diagnostic["event_counts"]}, {"invalid"})
        for identifier in SYNTHETIC_IDS:
            self.assertNotIn(identifier, text)
        async def hostile(_payload):
            return ready_value(storage_pending=True, pending_store_classes=[SYNTHETIC_IDS[0]])
        with self.assertRaises(acceptance.ObservationError) as found:
            await acceptance.wait_disposable_setup(SimpleNamespace(command=hostile))
        self.assertNotIn(SYNTHETIC_IDS[0], json.dumps(found.exception.diagnostic))

    async def test_ready_output_names_classes_only(self):
        hass, ledger, handlers, _modules = await observer_runtime()
        stores = [pending_store(key) for key in IDENTIFIER_KEYS]  # tracked weakly; keep them alive
        for store in stores:
            ledger.track(store)
        value = await Wire(hass, handlers).command({"type": "alarmo_interval_observer/ready"})
        self.assertEqual(value["pending_store_classes"],
                         sorted(set(IDENTIFIER_KEYS.values()) - observer.TOLERATED_KEYS)[:observer.MAX_STORE_CLASSES])
        self.assert_no_identifier(json.dumps(value))
        self.assertIsNone(acceptance.readiness_problem(value))


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
                baseline.assert_called_once_with(value["automation_baseline"], core_version=CORE,
                                                 support_slot=SLOT_OF[CORE])
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
                self.assertEqual(load_receipt(output.read_bytes()), result)
                baseline.assert_called_once_with(value["automation_baseline"], core_version=version, support_slot=slot)
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
        # The synthetic index stands in for a reviewed one, so the one reviewed
        # binding list carries its digest for the duration of ``publish``.
        binding = dataclasses.replace(core_lane_bindings.REVIEWED_CORE_BINDINGS[version], image_index_digest=index_digest)
        identity = b"synthetic-container sha256:" + b"a" * 64
        return SimpleNamespace(
            version=version, slot=slot, archive=archive, hashes=hashes, entry=entry, binding=binding,
            image=f"ghcr.io/home-assistant/home-assistant:{version}@{index_digest}",
            container="beta25-real-ha-ha-" + version.replace(".", "-"),
            outputs=[index + b"\n", manifest, identity, b"sha256:" + b"a" * 64 + b" linux/amd64", identity])

    def publish(self, root, case, outputs=None, *, image=None, container=None, version=UNSET, archive=None):
        output = Path(root) / "image.json"
        with bound_window(root), patch.dict(core_lane_bindings.REVIEWED_CORE_BINDINGS, {case.version: case.binding}), \
                patch("subprocess.run", side_effect=[
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
        # Derived from the committed window's non-empty slots (an empty Core
        # candidate has no binding and no Alarmo job).
        self.assertEqual(ALARMO_SLOTS, support_window.core_slots(WINDOW, support_window.SLOTS))
        self.assertEqual({"current", "rollback"} - set(ALARMO_SLOTS), set())
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
            # Rotation among reviewed Cores (amendment 2026-10-11): 2026.10.1 is
            # deployed, 2026.10.0 becomes rollback, 2026.9.4 leaves the window and
            # no candidate is under evaluation. Only the window changes.
            pins = {version: {"version": version, "image_index_digest":
                              core_lane_bindings.reviewed_lane_binding(version).image_index_digest}
                    for version in ("2026.10.1", "2026.10.0")}
            edit_window(root, lambda w: w["core"].update(current=pins["2026.10.1"], rollback=pins["2026.10.0"],
                                                         candidate=None))
            with bound_window(root):
                self.assertEqual(acceptance.slot_binding("2026.10.1")["slot"], "current")
                self.assertEqual(acceptance.slot_binding("2026.10.0")["slot"], "rollback")
                with self.assertRaisesRegex(ValueError, "exact support-window Core slot"):
                    acceptance.slot_binding("2026.9.4")
                with tempfile.TemporaryDirectory() as scratch, patch("subprocess.run") as run, self.assertRaises(ValueError):
                    acceptance.verify_disposable_cleanup(Path(scratch) / "result.json", Path(scratch) / "out.json",
                                                         "2026.9.4")
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


SEQ13_STAGE_IF = "matrix.alarmo && env.REAL_HA_SEQ13_LANES == 'enabled'"
SEQ13_UPLOAD_IF = "always() && " + SEQ13_STAGE_IF
SEQ13_STAGE = "Stage seq-13 contract evidence paths"
SEQ13_UPLOAD = "Upload bounded seq-13 label and availability/exposure contract evidence"
SEQ13_DELETE_UPLOAD = "Upload bounded seq-13 configuration delete contract evidence"
# HAMCP-269: exact-release gates apply to both reviewed October releases.
SEQ15_STAGE_IF = "matrix.alarmo && contains(fromJSON('[\"2026.10.1\",\"2026.10.3\"]'), matrix.ha_version) && env.REAL_HA_SEQ15_LANES == 'enabled'"
SEQ15_UPLOAD_IF = "always() && " + SEQ15_STAGE_IF
SEQ15_STAGE = "Stage seq-15 contract evidence paths"
SEQ15_UPLOAD = "Upload bounded seq-15 five-capability contract evidence"


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
            "Upload bounded entity metadata contract evidence": "always() && matrix.alarmo",
            # HAMCP-165/069B/155: the dormant seq-13 lanes add only these three
            # steps (one shared stage, one upload per lane family), each also
            # gated on the job-level enable flag.
            SEQ13_STAGE: SEQ13_STAGE_IF,
            SEQ13_UPLOAD: SEQ13_UPLOAD_IF,
            SEQ13_DELETE_UPLOAD: SEQ13_UPLOAD_IF,
            # The five-profile batch adds an isolated seed, stage and upload.
            "Seed seq-15 disposable scene configuration": SEQ15_STAGE_IF,
            SEQ15_STAGE: SEQ15_STAGE_IF,
            SEQ15_UPLOAD: SEQ15_UPLOAD_IF,
        })
        for step in self.steps:
            text = json.dumps(step)
            if "alarmo" in text.lower():
                with self.subTest(step=step.get("name")):
                    self.assertIn(step.get("if"), ("matrix.alarmo", "always() && matrix.alarmo",
                                                   SEQ13_STAGE_IF, SEQ13_UPLOAD_IF, SEQ15_STAGE_IF,
                                                   SEQ15_UPLOAD_IF))
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
        self.assertLess(cleanup, names.index("Upload bounded entity metadata contract evidence"))
        self.assertTrue(bind < names.index(SEQ13_STAGE) < run)
        self.assertLess(names.index("Upload bounded entity metadata contract evidence"),
                        names.index(SEQ13_UPLOAD))
        self.assertLess(names.index(SEQ13_UPLOAD), names.index(SEQ13_DELETE_UPLOAD))
        self.assertTrue(names.index(SEQ13_STAGE) < names.index(SEQ15_STAGE) < run)
        self.assertLess(names.index(SEQ13_DELETE_UPLOAD), names.index(SEQ15_UPLOAD))
        self.assertIn("--verify-publication", self.steps[bind]["run"])
        self.assertIn("--verify-cleanup", self.steps[cleanup]["run"])

    def test_seq13_lanes_are_defined_but_dormant(self):
        # HAMCP-165/069B/155: the committed workflow keeps the seq-13 lanes off;
        # the batch evidence run flips this one value. Nothing else reads the flag.
        workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
        job = workflow["jobs"]["real-ha-contract-tests"]
        self.assertEqual(job["env"]["REAL_HA_SEQ13_LANES"], "dormant")
        self.assertEqual(CI.read_text(encoding="utf-8").count("REAL_HA_SEQ13_LANES: "), 1)
        users = [step["name"] for step in self.steps if "REAL_HA_SEQ13_LANES" in json.dumps(step)]
        self.assertEqual(users, [SEQ13_STAGE, SEQ13_UPLOAD, SEQ13_DELETE_UPLOAD])
        stage = self.step(SEQ13_STAGE)
        self.assertEqual(re.findall(r"printf '([A-Z_]+)=", stage["run"]),
                         ["REAL_HA_LABEL_REGISTRY_RESULT", "REAL_HA_AVAILABILITY_EXPOSURE_RESULT",
                          "REAL_HA_CONFIGURATION_DELETE_RESULT"])
        self.assertIn('"$RUNNER_TEMP/seq13-configuration-delete-$HA_CONTRACT_LANE.json"', stage["run"])
        upload = self.step(SEQ13_UPLOAD)
        self.assertEqual(upload["uses"], self.step("Upload bounded entity metadata contract evidence")["uses"])
        self.assertEqual(upload["with"]["if-no-files-found"], "error")
        self.assertEqual(upload["with"]["retention-days"], 14)
        delete_upload = self.step(SEQ13_DELETE_UPLOAD)
        self.assertEqual(delete_upload["uses"], upload["uses"])
        self.assertEqual(delete_upload["with"]["name"], "seq13-configuration-delete-core-${{ matrix.ha_version }}"
                         "-${{ matrix.support_slot }}-${{ matrix.lane }}")
        self.assertEqual(delete_upload["with"]["path"].splitlines(), [
            "${{ runner.temp }}/seq13-configuration-delete-${{ matrix.lane }}.json",
            "${{ runner.temp }}/alarmo-cleanup-${{ matrix.lane }}.json"])
        self.assertEqual(delete_upload["with"]["if-no-files-found"], "error")
        self.assertEqual(delete_upload["with"]["retention-days"], 14)

    def test_seq15_lanes_cover_all_five_profiles_on_exact_releases(self):
        # All five receipts are required for both exact-release jobs.
        workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
        job = workflow["jobs"]["real-ha-contract-tests"]
        self.assertEqual(job["env"]["REAL_HA_SEQ15_LANES"], "enabled")
        self.assertEqual(CI.read_text(encoding="utf-8").count("REAL_HA_SEQ15_LANES: "), 1)
        users = [step["name"] for step in self.steps if "REAL_HA_SEQ15_LANES" in json.dumps(step)]
        self.assertEqual(users, ["Seed seq-15 disposable scene configuration", SEQ15_STAGE, SEQ15_UPLOAD])
        stage = self.step(SEQ15_STAGE)
        self.assertEqual(re.findall(r"printf '([A-Z_]+)=", stage["run"]),
                         ["REAL_HA_HELPER_OPTIONS_RESULT", "REAL_HA_INPUT_SELECT_OPTION_RESULT",
                          "REAL_HA_SCRIPT_TRACES_RESULT", "REAL_HA_DASHBOARD_METADATA_RESULT",
                          "REAL_HA_SCENE_CONFIGURATION_RESULT"])
        import seq15_contract_acceptance as seq15
        # Every defined lane is wired into the evidence batch.
        self.assertEqual(seq15.WORKFLOW_PENDING_LANES, frozenset())
        self.assertTrue(seq15.WORKFLOW_PENDING_LANES <= set(seq15.LANES))
        wired = [lane for key, lane in seq15.LANES.items() if key not in seq15.WORKFLOW_PENDING_LANES]
        self.assertEqual(sorted(lane["output_env"] for lane in wired),
                         sorted(re.findall(r"printf '([A-Z_]+)=", stage["run"])))
        for key in seq15.WORKFLOW_PENDING_LANES:
            self.assertNotIn(seq15.LANES[key]["output_env"], CI.read_text(encoding="utf-8"))
        upload = self.step(SEQ15_UPLOAD)
        self.assertEqual(upload["uses"], self.step(SEQ13_UPLOAD)["uses"])
        self.assertEqual(upload["with"]["name"], "seq15-contracts-core-${{ matrix.ha_version }}"
                         "-${{ matrix.support_slot }}-${{ matrix.lane }}")
        self.assertEqual(upload["with"]["path"].splitlines(), [
            "${{ runner.temp }}/seq15-helper-options-${{ matrix.lane }}.json",
            "${{ runner.temp }}/seq15-input-select-option-${{ matrix.lane }}.json",
            "${{ runner.temp }}/seq15-script-traces-${{ matrix.lane }}.json",
            "${{ runner.temp }}/seq15-dashboard-metadata-${{ matrix.lane }}.json",
            "${{ runner.temp }}/seq15-scene-configuration-${{ matrix.lane }}.json"])
        self.assertEqual(upload["with"]["if-no-files-found"], "error")
        self.assertEqual(upload["with"]["retention-days"], 14)
        # Rotation cannot silently broaden the two exact releases.
        self.assertEqual({SLOT_VERSIONS["candidate"], SLOT_VERSIONS["current"]}, seq15.TARGET_CORES)

    def test_core_archive_uses_only_fixed_codeload_forms_and_exact_digest(self):
        step = self.step("Stage exact Alarmo and independent interval observer for the Core slot")
        # HAMCP-221: the read-only job token authenticates the codeload reads.
        self.assertEqual(step["env"], {"GH_TOKEN": "${{ github.token }}",
                                       "REAL_HA_CORE_SOURCE_COMMIT": "${{ matrix.core_source_commit }}",
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
        # Both reads use the bounded retry helper (HTTPS, TLS 1.2+, no
        # redirects); the Core form also checks its digest in the helper,
        # where a mismatch is never retried, before the strict shell check.
        self.assertNotIn("curl", script)
        self.assertEqual(script.count("python3 -I -S scripts/github_retry.py download"), 2)
        self.assertEqual(script.count('--sha256 "$REAL_HA_CORE_SOURCE_SHA256"'), 1)
        self.assertIn('--sha256 "$REAL_HA_CORE_SOURCE_SHA256" \\\n'
                      '     && printf \'%s  %s\\n\' "$REAL_HA_CORE_SOURCE_SHA256"', script)
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


class RetryOnceCleanTests(unittest.IsolatedAsyncioTestCase):
    """HAMCP-219 review 1: a repairs delayed save reruns a window once; it is never tolerated.

    The beta.32 publish run failed ``baseline_export`` on one background
    ``storage_delay_save`` of Core's ``repairs.issue_registry``. Origin cannot
    prove that save was independent: Core's executor (core.py:875) and
    thread-safe callback (util/async_.py:72) drop the request context, so a
    command-caused save is labelled background. The window is discarded and
    rerun once instead; the rerun must verify strictly.
    """

    REPAIRS = "repairs.issue_registry"
    DELAYED = {"kind": "storage_delay_save", "origin": "background", "store": "repairs.issue_registry"}

    def window(self, saves=(), *, commands=(), kind="control"):
        """One window through the real observer ledger with the given repairs events."""
        ledger = observer.Ledger(lambda: 1)
        hashes = {name: None for name in acceptance.STORE_HASH_KEYS}
        identity = ledger.start(kind, hashes)
        for command in commands:
            ledger.record("command", command=command)
        for event_kind in saves:
            ledger.record(event_kind, store=FakeStore(self.REPAIRS, data={"private": "DO_NOT_RECORD"},
                                                      delay_handle=object()))
        value = ledger.finish(identity, dict(hashes), {name: True for name in acceptance.OBSERVER_HOOKS})
        value.update(core_version=CORE, observer_sha256=sha256(acceptance.OBSERVER.read_bytes()))
        self.assertNotIn("DO_NOT_RECORD", json.dumps(value))
        return value

    def verify(self, value, retry_allowed=False, expected=()):
        return acceptance.verify_interval(value, value["interval_id"], list(expected), kind="control",
                                          core_version=CORE, window="baseline_export", window_index=1,
                                          retry_allowed=retry_allowed)

    async def run_windows(self, windows, *, helper=None, expected=()):
        """The real retry helper over a scripted sequence of windows; returns runs and discards."""
        runs, discarded = [], []
        async def run():
            value = windows[len(runs)]()
            runs.append(value)
            return value["interval_id"], value
        result = await (helper or acceptance.observe_retry_once_clean)(
            run, lambda _identity, value, retry: self.verify(value, retry, expected), on_discard=discarded.append)
        return result, runs, discarded

    async def command_caused_window(self, *, flush_inside):
        """The reviewer's attribution-loss path: a command schedules a repairs save via Core's executor.

        Same primitives as Core abe1fdd7: ``loop.run_in_executor`` (core.py:875)
        then ``call_soon_threadsafe`` (issue_registry.py:394, util/async_.py:72).
        ``flush_inside`` lets the write land in the window (the reviewer's 10 ms
        timer); otherwise it lands after, like Core's 10 s ``SAVE_DELAY``.
        """
        loop = asyncio.get_running_loop()
        ledger = observer.Ledger()
        hashes = {name: None for name in acceptance.STORE_HASH_KEYS}
        identity = ledger.start("control", hashes)
        store = FakeStore(self.REPAIRS, data={}, delay_handle=None)
        written = asyncio.Event()
        async def write(_obj):
            written.set()
        observed_write = observer.observe_async(ledger, "storage_write", write)
        flushes = []
        def delayed_save(obj):
            flushes.append(lambda: asyncio.ensure_future(observed_write(obj)))
        observed_schedule = observer.observe_sync(ledger, "storage_delay_save", delayed_save)
        def worker():
            done = concurrent.futures.Future()
            def callback():
                observed_schedule(store)
                done.set_result(None)
            loop.call_soon_threadsafe(callback)
            done.result(timeout=5)
        async def handler():
            self.assertIsNotNone(observer.REQUEST.get())
            await loop.run_in_executor(None, worker)
        observed_dispatch = observer.observe_sync(ledger, "command", lambda _c, _m: asyncio.ensure_future(handler()))
        await observed_dispatch(None, {"type": "config/entity_registry/list"})
        if flush_inside:
            flushes.pop()()
            await asyncio.wait_for(written.wait(), 5)
        value = ledger.finish(identity, dict(hashes), {name: True for name in acceptance.OBSERVER_HOOKS})
        if not flush_inside:
            await flushes.pop()()
            self.assertTrue(written.is_set())
        value.update(core_version=CORE, observer_sha256=sha256(acceptance.OBSERVER.read_bytes()))
        # The context loss itself: the command-caused save carries no command origin.
        self.assertIn(self.DELAYED, value["events"])
        return value

    async def reproduce(self, *, flush_inside, helper=None):
        values = []
        async def run():
            value = await self.command_caused_window(flush_inside=flush_inside)
            values.append(value)
            return value["interval_id"], value
        discarded = []
        result = await (helper or acceptance.observe_retry_once_clean)(
            run, lambda _identity, value, retry: self.verify(value, retry, ["config/entity_registry/list"]),
            on_discard=discarded.append)
        return result, values, discarded

    async def test_reviewer_attribution_loss_recurs_on_the_rerun_and_fails(self):
        with self.assertRaises(acceptance.ObservationError) as found:
            await self.reproduce(flush_inside=False)
        diagnostic = found.exception.diagnostic
        assert_bounded(self, diagnostic)
        self.assertNotIsInstance(found.exception, acceptance.RetryableObservation)
        self.assertEqual((diagnostic["condition"], diagnostic["event"], diagnostic["rerun_after_discard"]),
                         ("untolerated_store", self.DELAYED, True))

    async def test_reviewer_attribution_loss_with_its_flush_inside_fails_without_a_rerun(self):
        runs = []
        async def run():
            runs.append(await self.command_caused_window(flush_inside=True))
            return runs[-1]["interval_id"], runs[-1]
        with self.assertRaises(acceptance.ObservationError) as found:
            await acceptance.observe_retry_once_clean(run, lambda _i, value, retry: self.verify(
                value, retry, ["config/entity_registry/list"]))
        self.assertEqual(len(runs), 1)
        self.assertEqual((found.exception.diagnostic["condition"], found.exception.diagnostic["event"]),
                         ("untolerated_store", {**self.DELAYED, "kind": "storage_write"}))
        self.assertNotIn("rerun_after_discard", found.exception.diagnostic)

    async def test_one_off_background_save_passes_on_the_clean_rerun_and_both_windows_are_kept(self):
        first, clean = self.window(["storage_delay_save"]), self.window()
        (_identity, observation, proof, discarded), runs, recorded = await self.run_windows(
            [lambda: first, lambda: clean])
        self.assertEqual(len(runs), 2)
        self.assertIs(observation, clean)
        self.assertEqual((proof["result"], proof["background_bookkeeping"]), ("PASS", []))
        self.assertEqual(recorded, [discarded])
        self.assertIs(discarded["observation"], first)
        diagnostic = discarded["diagnostic"]
        assert_bounded(self, diagnostic)
        self.assertEqual((diagnostic["condition"], diagnostic["window"], diagnostic["window_index"],
                          diagnostic["event"], diagnostic["retryable_event_count"]),
                         ("retryable_delayed_save", "baseline_export", 1, self.DELAYED, 1))
        # The discarded window is still refused strictly; it is evidence, not a pass.
        with self.assertRaises(acceptance.ObservationError) as found:
            self.verify(first)
        self.assertEqual(found.exception.diagnostic["condition"], "untolerated_store")

    async def test_second_background_save_on_the_rerun_fails(self):
        with self.assertRaises(acceptance.ObservationError) as found:
            await self.run_windows([lambda: self.window(["storage_delay_save"]),
                                    lambda: self.window(["storage_delay_save"]),
                                    lambda: self.fail("a third window ran")])
        self.assertEqual((found.exception.diagnostic["condition"], found.exception.diagnostic["rerun_after_discard"]),
                         ("untolerated_store", True))

    async def test_rerun_must_be_completely_clean(self):
        for name, second in (("other_store", lambda: self.window_with({"kind": "storage_save", "origin": "background",
                                                                         "store": "other", "store_class": "core"})),
                             ("extra_command", lambda: self.window(commands=["ping"])),
                             ("hash_changed", lambda: self.hash_changed(self.window()))):
            with self.subTest(rerun=name), self.assertRaises(acceptance.ObservationError) as found:
                await self.run_windows([lambda: self.window(["storage_delay_save"]), second])
            self.assertIs(found.exception.diagnostic["rerun_after_discard"], True)
            self.assertNotIsInstance(found.exception, acceptance.RetryableObservation)

    def window_with(self, event):
        value = self.window()
        value["events"].append(event)
        return value

    def hash_changed(self, value):
        value["final_store_hashes"]["core.device_registry"] = "1" * 64
        return value

    async def test_immediate_repairs_save_or_write_fails_with_no_rerun(self):
        for kind in ("storage_save", "storage_write"):
            with self.subTest(kind=kind), self.assertRaises(acceptance.ObservationError) as found:
                await self.run_windows([lambda: self.window([kind]), lambda: self.fail("rerun")])
            self.assertEqual((found.exception.diagnostic["condition"], found.exception.diagnostic["event"]),
                             ("untolerated_store", {**self.DELAYED, "kind": kind}))
            self.assertNotIn("rerun_after_discard", found.exception.diagnostic)
        with self.subTest(kind="delayed_with_write"), self.assertRaises(acceptance.ObservationError) as found:
            await self.run_windows([lambda: self.window(["storage_delay_save", "storage_write"]),
                                    lambda: self.fail("rerun")])
        self.assertEqual(found.exception.diagnostic["event_index"], 1)

    async def test_command_origin_and_other_failures_never_rerun(self):
        def command_origin():
            token = observer.REQUEST.set("synthetic-request")
            try:
                return self.window(["storage_delay_save"])
            finally:
                observer.REQUEST.reset(token)
        for name, first, condition in (
                ("command_origin", command_origin, "command_origin_storage"),
                ("commands_mismatch", lambda: self.window(["storage_delay_save"], commands=["ping"]), "commands_mismatch"),
                ("hash_changed", lambda: self.hash_changed(self.window(["storage_delay_save"])), "store_hash_changed"),
                ("other_repairs", lambda: self.window_with({**self.DELAYED, "store": "other", "store_class": "repairs"}),
                 "untolerated_store")):
            with self.subTest(first=name), self.assertRaises(acceptance.ObservationError) as found:
                await self.run_windows([first, lambda: self.fail("rerun")])
            self.assertEqual(found.exception.diagnostic["condition"], condition)
            self.assertNotIsInstance(found.exception, acceptance.RetryableObservation)

    def test_strict_verification_still_refuses_every_repairs_save(self):
        for kind in ("storage_save", "storage_delay_save", "storage_write"):
            with self.subTest(kind=kind), self.assertRaises(acceptance.ObservationError) as found:
                self.verify(self.window([kind]))
            self.assertEqual(found.exception.diagnostic["condition"], "untolerated_store")
            self.assertNotIsInstance(found.exception, acceptance.RetryableObservation)
        with self.assertRaises(acceptance.ObservationError):
            self.verify(self.window(["storage_delay_save"]), retry_allowed=1)

    async def test_mutation_tolerate_without_rerun_passes_the_reproduction(self):
        # Killed by test_reviewer_attribution_loss_recurs_on_the_rerun_and_fails.
        # The replaced a81b837 design: the store tolerated by name, no retry route.
        with patch.object(acceptance, "TOLERATED_STORES", (*acceptance.TOLERATED_STORES, self.REPAIRS)), \
                patch.object(acceptance, "RETRY_STORE", None):
            (_identity, _value, proof, discarded), values, _ = await self.reproduce(flush_inside=False)
        self.assertEqual((proof["result"], discarded, len(values)), ("PASS", None, 1))

    async def test_mutation_accepting_a_nonclean_rerun_passes_the_reproduction(self):
        # Killed by test_reviewer_attribution_loss_recurs_on_the_rerun_and_fails.
        async def lenient(run, verify, *, on_discard=None):
            for _ in range(2):
                outcome, observation = await run()
                try:
                    return outcome, observation, verify(outcome, observation, True), None
                except acceptance.RetryableObservation:
                    continue
            return outcome, observation, {"result": "PASS"}, None
        (_identity, _value, proof, _), values, _ = await self.reproduce(flush_inside=False, helper=lenient)
        self.assertEqual((proof["result"], len(values)), ("PASS", 2))

    def test_retry_store_is_named_mirrored_never_tolerated_and_never_hashed(self):
        self.assertEqual(set(acceptance.TOLERATED_STORES), observer.TOLERATED_KEYS)
        self.assertEqual(set(acceptance.TOLERATED_STORES), {"auth", "core.restore_state"})
        self.assertEqual({acceptance.RETRY_STORE}, set(observer.RETRY_KEYS))
        self.assertEqual(acceptance.RETRY_EVENT_KIND, "storage_delay_save")
        self.assertLessEqual(observer.RETRY_KEYS, observer.STORE_KEYS)
        self.assertFalse(observer.RETRY_KEYS & (observer.TOLERATED_KEYS | set(observer.HASH_KEYS)))
        self.assertEqual(set(observer.HASH_KEYS), acceptance.STORE_HASH_KEYS)
        self.assertEqual(observer.store_class(self.REPAIRS), self.REPAIRS)
        self.assertIn(self.REPAIRS, acceptance.STORE_CLASSES)

    def test_pending_issue_registry_save_holds_readiness_so_a_rerun_opens_after_its_flush(self):
        now = [0]
        ledger = observer.Ledger(lambda: now[0])
        repairs = FakeStore(self.REPAIRS, data={"x": 1}, delay_handle=object())
        ledger.record("storage_delay_save", store=repairs)
        self.assertEqual(ledger.pending_store_classes(), [self.REPAIRS])
        repairs._data, repairs._delay_handle = None, None
        self.assertEqual(ledger.pending_store_classes(), [self.REPAIRS])  # quiet period
        now[0] = observer.QUIET_SECONDS
        self.assertEqual(ledger.pending_store_classes(), [])


class DiscardedWindowEvidenceTests(unittest.TestCase):
    """HAMCP-219 review 1: discarded windows stay in the retained receipt and are re-verified."""

    def discarded(self, window, saves=("storage_delay_save",)):
        expected, kind = acceptance.ALARMO_WINDOWS[window]
        value = receipt(expected, CORE)
        value["kind"] = kind
        for event_kind in saves:
            value["events"].append({"kind": event_kind, "origin": "background", "store": acceptance.RETRY_STORE})
        with self.assertRaises(acceptance.RetryableObservation) as found:
            acceptance.verify_window(window, value, value["interval_id"], core_version=CORE, retry_allowed=True)
        return {"window": window, "observation": value, "diagnostic": found.exception.diagnostic}

    def cleanup(self, value):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "result.json", Path(directory) / "cleanup.json"
            source.write_text(json.dumps(value))
            with patch("subprocess.run", return_value=SimpleNamespace(stdout=b"")), \
                    patch("automation_baseline_contract_acceptance.verify_result", return_value={"result": "PASS"}):
                return acceptance.verify_disposable_cleanup(source, output, CORE)

    def test_each_window_may_retain_one_discarded_observation(self):
        value = cleanup_value(CORE)
        self.assertEqual(self.cleanup(value)["discarded_windows"], [])
        value["discarded_observations"] = [self.discarded(window) for window in acceptance.ALARMO_WINDOWS]
        self.assertEqual(self.cleanup(value)["discarded_windows"], sorted(acceptance.ALARMO_WINDOWS))

    def test_malformed_duplicate_clean_or_nonretryable_discards_refuse(self):
        def clean(item):
            item["observation"]["events"] = [e for e in item["observation"]["events"] if e.get("kind") == "command"]
        for name, mutate in (
                ("missing", lambda v: v.pop("discarded_observations")),
                ("not_list", lambda v: v.update(discarded_observations={})),
                ("duplicate", lambda v: v["discarded_observations"].append(copy.deepcopy(v["discarded_observations"][0]))),
                ("unknown_window", lambda v: v["discarded_observations"][0].update(window="baseline_export")),
                ("extra_field", lambda v: v["discarded_observations"][0].update(extra=1)),
                ("clean", lambda v: clean(v["discarded_observations"][0])),
                ("write", lambda v: v["discarded_observations"][0]["observation"]["events"].append(
                    {"kind": "storage_write", "origin": "background", "store": acceptance.RETRY_STORE})),
                ("diagnostic", lambda v: v["discarded_observations"][0]["diagnostic"].update(retryable_event_count=2)),
                ("not_dict", lambda v: v["discarded_observations"][0].update(observation=[]))):
            value = cleanup_value(CORE)
            value["discarded_observations"] = [self.discarded("inspection")]
            mutate(value)
            with self.subTest(mutation=name), self.assertRaises((ValueError, KeyError, acceptance.ObservationError)):
                self.cleanup(value)

    def test_too_many_discards_refuse(self):
        value = cleanup_value(CORE)
        value["discarded_observations"] = [self.discarded(window) for window in acceptance.ALARMO_WINDOWS]
        value["discarded_observations"].append(self.discarded("inspection"))
        with self.assertRaises(ValueError):
            self.cleanup(value)
