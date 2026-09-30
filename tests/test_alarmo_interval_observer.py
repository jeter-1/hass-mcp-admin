"""Bounded synthetic observer/proof controls; actual Core execution is separate CI."""
import asyncio
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import alarmo_inspection_contract_acceptance as acceptance

spec = importlib.util.spec_from_file_location("synthetic_alarmo_observer", acceptance.OBSERVER)
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


def receipt(commands=()):
    ledger = observer.Ledger(lambda: 1)
    hashes = {key: None for key in acceptance.STORE_HASH_KEYS}
    identity = ledger.start("inspection", hashes)
    for command in commands:
        ledger.record("command", command=command)
    value = ledger.finish(identity, dict(hashes), {key: True for key in acceptance.OBSERVER_HOOKS})
    value.update(core_version="2026.9.4", observer_sha256=hashlib.sha256(acceptance.OBSERVER.read_bytes()).hexdigest())
    return value


class ObserverTests(unittest.IsolatedAsyncioTestCase):
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
            result = acceptance.verify_interval(value, value["interval_id"], commands)
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
            acceptance.verify_interval(value, value["interval_id"], acceptance.EXPECTED_COMMANDS)

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
                acceptance.verify_interval(value, value["interval_id"], [])

    def test_background_bookkeeping_is_explicit_not_global_zero_claim(self):
        value = receipt()
        event = {"kind": "storage_write", "origin": "background", "store": "auth"}
        value["events"].append(event)
        proof = acceptance.verify_interval(value, value["interval_id"], [])
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
                acceptance.verify_interval(value, value["interval_id"], [])
        value = receipt()
        with self.assertRaises(ValueError):
            acceptance.verify_interval(value, "0" * 32, [])
        now = [0]
        ledger = observer.Ledger(lambda: now[0])
        identity = ledger.start("inspection", {})
        for _ in range(1000):
            ledger.record("service")
        now[0] = 46
        result = ledger.finish(identity, {}, {})
        self.assertTrue(result["overflow"] and result["expired"])
        self.assertEqual(len(result["events"]), 128)

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


class PublicationCleanupTests(unittest.TestCase):
    def test_cleanup_requires_successful_enumeration_no_residue_and_actual_interval(self):
        observation = receipt(acceptance.EXPECTED_COMMANDS)
        control = receipt(["get_states"]); control["kind"] = "control"
        denial = receipt(); denial["kind"] = "control"
        value = dict(result="PASS", scenario="alarmo_inspection", core="2026.9.4", interval_observation=observation,
                     missing_authority_observation=denial, negative_control_observation=control,
                     interval=acceptance.verify_interval(observation, observation["interval_id"], acceptance.EXPECTED_COMMANDS),
                     missing_authority=acceptance.verify_interval(denial, denial["interval_id"], [], kind="control"))
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "result.json", Path(directory) / "cleanup.json"
            source.write_text(json.dumps(value))
            with patch("subprocess.run", return_value=SimpleNamespace(stdout=b"")):
                self.assertEqual(acceptance.verify_disposable_cleanup(source, output)["result"], "PASS")
            for response in (b"beta25-real-ha-ha-2026-9-4\n", b"beta-rc2-contract-ha-2026-9-4\n"):
                with patch("subprocess.run", return_value=SimpleNamespace(stdout=response)), self.assertRaises(ValueError):
                    acceptance.verify_disposable_cleanup(source, output)
            with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "docker")), self.assertRaises(subprocess.CalledProcessError):
                acceptance.verify_disposable_cleanup(source, output)
            value["result"] = "NOT_RUN"
            source.write_text(json.dumps(value))
            with patch("subprocess.run", return_value=SimpleNamespace(stdout=b"")), self.assertRaises(ValueError):
                acceptance.verify_disposable_cleanup(source, output)

    def test_image_binding_is_digest_architecture_and_container_continuity_bound(self):
        fixture = json.loads((ROOT / "tests/fixtures/core_2026_9_4_lane_provenance.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            archive, output = Path(directory) / "source.tar", Path(directory) / "image.json"
            archive.write_bytes(b"synthetic-source")
            manifest = json.dumps({"config": {"digest": "sha256:" + "a" * 64}}).encode()
            md = "sha256:" + hashlib.sha256(manifest).hexdigest()
            index = json.dumps({"manifests": [{"digest": md, "size": len(manifest), "platform": {"os": "linux", "architecture": "amd64"}}]}).encode()
            fixture.update(source_archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                           image_index_digest="sha256:" + hashlib.sha256(index).hexdigest(), architecture_manifests={"linux/amd64": md})
            image = "ghcr.io/home-assistant/home-assistant:2026.9.4@" + fixture["image_index_digest"]
            identity = b"synthetic-container sha256:" + b"a" * 64
            outputs = [index + b"\n", manifest, identity, b"sha256:" + b"a" * 64 + b" linux/amd64", identity]
            original_read = Path.read_text
            def read(path, *args, **kwargs):
                return json.dumps(fixture) if path.name == "core_2026_9_4_lane_provenance.json" else original_read(path, *args, **kwargs)
            def run(values):
                with patch.object(Path, "read_text", read), patch("subprocess.run", side_effect=[SimpleNamespace(stdout=x) for x in values]):
                    return acceptance.verify_disposable_publication(image, "beta25-real-ha-ha-2026-9-4", archive, output)
            self.assertEqual(run(outputs)["result"], "PASS")
            for i, wrong in ((0, b"wrong-index"), (1, b"wrong-manifest"), (3, b"sha256:" + b"a" * 64 + b" linux/arm64"), (4, b"other-container sha256:" + b"a" * 64)):
                bad = list(outputs); bad[i] = wrong
                with self.subTest(index=i), self.assertRaises(ValueError):
                    run(bad)
