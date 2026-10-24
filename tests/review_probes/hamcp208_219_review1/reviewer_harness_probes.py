"""Offline probes: real observer wrappers, synthetic Store and executor round-trip.

Core abe1fdd7: core.py:875 uses loop.run_in_executor (no context copy).
issue_registry.py:394 calls run_callback_threadsafe(...).result(); util/async_.py:72
uses loop.call_soon_threadsafe. These are the primitives exercised here.
"""
import asyncio
import concurrent.futures
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import tests.test_alarmo_interval_observer as alarmo
import tests.test_dashboard_disposable_acceptance as dashboard

spec = importlib.util.spec_from_file_location("review_dashboard_observer",
    Path.cwd() / "tests/fixtures/dashboard_interval_observer/custom_components/dashboard_interval_observer/__init__.py")
dashboard_observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard_observer)

def dashboard_accepts(event):
    # Exact inlined base predicate; the head extracted it into a function.
    predicate = getattr(dashboard.driver, "background_bookkeeping", lambda e:
        e.get("origin") == "background" and e.get("store") in {"auth", "core.restore_state"}
        and e.get("kind") in {"storage_save", "storage_delay_save", "storage_write"})
    return predicate(event)


class HarnessProbes(unittest.IsolatedAsyncioTestCase):
    async def test_command_executor_roundtrip_must_not_pass_as_background(self):
        for name, observer in (("alarmo", alarmo.observer), ("dashboard", dashboard_observer)):
            with self.subTest(observer=name), tempfile.TemporaryDirectory() as directory:
                loop = asyncio.get_running_loop()
                ledger = observer.Ledger()
                hashes = {key: None for key in observer.HASH_KEYS}
                identity = ledger.start("control", hashes)
                path = Path(directory) / "repairs.issue_registry"
                done = asyncio.Event()
                store = alarmo.FakeStore("repairs.issue_registry", data={}, delay_handle=None)

                async def write(obj):
                    path.write_text('{"synthetic_issue":true}')
                    done.set()

                observed_write = observer.observe_async(ledger, "storage_write", write)

                def delayed_save(obj):
                    loop.call_later(.01, lambda: asyncio.create_task(observed_write(obj)))

                observed_schedule = observer.observe_sync(ledger, "storage_delay_save", delayed_save)

                def worker():
                    future = concurrent.futures.Future()
                    def callback():
                        observed_schedule(store)
                        future.set_result(None)
                    loop.call_soon_threadsafe(callback)
                    future.result(timeout=5)

                async def handler():
                    self.assertIsNotNone(observer.REQUEST.get())
                    # Same executor boundary as Core.async_add_executor_job.
                    await loop.run_in_executor(None, worker)
                    await asyncio.wait_for(done.wait(), 5)

                def dispatch(_connection, _message):
                    return asyncio.create_task(handler())

                command = "config/entity_registry/list"
                observed_dispatch = observer.observe_sync(ledger, "command", dispatch)
                await observed_dispatch(None, {"type": command})
                self.assertTrue(path.exists(), "Synthetic command actually caused a file write")
                if name == "alarmo":
                    coverage = {key: True for key in alarmo.acceptance.OBSERVER_HOOKS}
                    value = ledger.finish(identity, dict(hashes), coverage)
                    value.update(core_version=alarmo.CORE,
                                 observer_sha256=hashlib.sha256(alarmo.acceptance.OBSERVER.read_bytes()).hexdigest())
                    try:
                        proof = alarmo.acceptance.verify_interval(value, identity, [command],
                            kind="control", core_version=alarmo.CORE)
                        accepted = proof["result"] == "PASS"
                    except alarmo.acceptance.ObservationError:
                        accepted = False
                else:
                    value = ledger.finish(identity, dict(hashes), {})
                    accepted = all(dashboard_accepts(e)
                                   for e in value["events"] if e["kind"] != "command")
                print(json.dumps({"observer": name, "command_caused_file_write": path.exists(),
                                  "events": value["events"], "accepted": accepted}), flush=True)
                self.assertFalse(accepted, "Command-caused repairs write passed as background bookkeeping")

    def test_non_delayed_repairs_save_kinds_must_be_refused(self):
        for kind in ("storage_save", "storage_write"):
            with self.subTest(kind=kind):
                observer = alarmo.observer
                ledger = observer.Ledger(lambda: 1)
                hashes = {key: None for key in observer.HASH_KEYS}
                identity = ledger.start("control", hashes)
                ledger.record(kind, store=alarmo.FakeStore("repairs.issue_registry", data={}))
                value = ledger.finish(identity, dict(hashes), {key: True for key in alarmo.acceptance.OBSERVER_HOOKS})
                value.update(core_version=alarmo.CORE,
                    observer_sha256=hashlib.sha256(alarmo.acceptance.OBSERVER.read_bytes()).hexdigest())
                with self.assertRaises(alarmo.acceptance.ObservationError):
                    alarmo.acceptance.verify_interval(value, identity, [], kind="control", core_version=alarmo.CORE)
            with self.subTest(kind=kind, observer="dashboard"):
                self.assertFalse(dashboard_accepts({
                    "origin": "background", "store": "repairs.issue_registry", "kind": kind}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
