"""HAMCP-225 automation trace snapshots and watches.

Offline: a fake Core command and fake Core runtime, a disposable store under a
temporary directory, and synthetic secrets from ``hamcp162_synthetic``.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))

import tests.hamcp162_synthetic as h  # noqa: E402

from ha_mcp_engineering.configuration_definitions import credentials  # noqa: E402
from ha_mcp_engineering.errors import HomeAssistantApiError  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    CORE_INDEPENDENT_STATIC_TOOLS, STATIC_TOOL_CORE_REQUIREMENTS,
)
from ha_mcp_engineering.providers.dispatch import CanonicalProviderDispatcher  # noqa: E402
from ha_mcp_engineering.providers.models import (  # noqa: E402
    ProviderCapability, ProviderCompleteness,
)
from ha_mcp_engineering.providers.routing import (  # noqa: E402
    ANALYTICAL_PROVIDER_POLICIES, TOOL_CAPABILITY_POLICY,
)
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.routing import _trace_snapshot_audit_parameters  # noqa: E402
from ha_mcp_engineering.tools import compatibility  # noqa: E402
from ha_mcp_engineering.tools import trace_snapshots as tool_module  # noqa: E402
from ha_mcp_engineering.trace_snapshots import contracts as c  # noqa: E402
from ha_mcp_engineering.trace_snapshots import service as service_module  # noqa: E402
from ha_mcp_engineering.trace_snapshots import store as store_module  # noqa: E402
from ha_mcp_engineering.trace_snapshots.poller import (  # noqa: E402
    TraceWatchPoller, watch_poll_authority,
)
from ha_mcp_engineering.trace_snapshots.runtime import TraceSnapshotRuntime  # noqa: E402
from ha_mcp_engineering.trace_snapshots.service import TraceSnapshotService  # noqa: E402


PACKAGE = Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
KNOWN = "synthetic-known-" + h.needle("known")
VERSION = "2026.10.1"
NOW = 1_793_000_000.0  # 2026-10-26, seconds


class FakeRuntime:
    """A Core runtime whose route availability and generation tests control."""

    def __init__(self):
        self.available = True
        self.generation = 7
        self.current_observation = SimpleNamespace(version=VERSION)
        self.calls = []

    def route_status(self, requirements):
        self.calls.append(("route_status", requirements))
        return {"available": self.available, "generation": self.generation}

    def acquire(self, requirements):
        self.calls.append(("acquire", requirements))
        return SimpleNamespace(generation=self.generation, core_version=VERSION)

    def consume(self, authority):
        self.calls.append(("consume",))
        return ("commit",)

    def revalidate(self, authority, commits):
        self.calls.append(("revalidate",))
        return self.available

    def finish(self, commits):
        self.calls.append(("finish",))

    def release(self, authority):
        self.calls.append(("release",))


class FakeAudit:
    def __init__(self):
        self.enabled = True
        self.fail = False
        self.rows = []

    def write(self, entry):
        if self.fail:
            return False
        self.rows.append(entry)
        return True


def header(run_id, execution="finished", *, minute=0, state="stopped", error=None, not_triggered=False):
    row = {"run_id": run_id, "state": state, "script_execution": execution, "domain": "automation",
           "item_id": "a1", "last_step": "action/0",
           "timestamp": {"start": f"2026-10-26T10:{minute:02d}:00+00:00",
                         "finish": f"2026-10-26T10:{minute:02d}:01+00:00"}}
    if error is not None:
        row["error"] = error
    if not_triggered:
        row["not_triggered"] = True
    return row


class FakeCore:
    """``trace/list`` and ``trace/get`` for automation ``a1`` only."""

    def __init__(self):
        self.runs = []
        self.details = {}
        self.sent = []
        self.fail_get = {}
        self.on_get = None

    def add(self, run_id, execution="finished", **kwargs):
        self.runs.append(header(run_id, execution, **kwargs))

    async def command(self, payload):
        self.sent.append(dict(payload))
        if payload.get("domain") != "automation":
            raise AssertionError("only the automation domain may be read")
        if payload["type"] == "trace/list":
            return [dict(row) for row in self.runs]
        if payload["type"] != "trace/get":
            raise AssertionError("only trace/list and trace/get may be sent")
        if self.on_get is not None:
            self.on_get(payload)
        failure = self.fail_get.get(payload["run_id"])
        if failure is not None:
            raise failure
        row = next((r for r in self.runs if r["run_id"] == payload["run_id"]), None)
        if row is None:
            raise HomeAssistantApiError(details={"status": 404})
        return self.details.get(payload["run_id"]) or {
            **row, "config": {"alias": "Synthetic"}, "context": {"id": "ctx"},
            "blueprint_inputs": None,
            "trace": {"trigger/0": [{"path": "trigger/0", "timestamp": "t"}],
                      "action/0": [{"path": "action/0", "result": {"ok": True}}]},
        }


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


def all_store_bytes(root):
    found = b""
    for directory, _, files in os.walk(root):
        for name in files:
            found += Path(directory, name).read_bytes()
    return found


class ServiceCase(unittest.IsolatedAsyncioTestCase):
    caller = "caller0a1b2c3"

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.core = FakeCore()
        self.runtime = FakeRuntime()
        self.audit = FakeAudit()
        self.clock = Clock()
        self.service = TraceSnapshotService(self.root, self.core.command, self.runtime, self.audit,
                                            known_secrets=(KNOWN,), clock=self.clock)
        self.telemetry, self.token = begin_request()
        self.telemetry.caller_id = self.caller
        self.telemetry.core_dispatch_authorizer = lambda: True

    async def asyncTearDown(self):
        end_request(self.token)
        self.directory.cleanup()

    def as_caller(self, caller):
        self.telemetry.caller_id = caller

    async def refused(self, call):
        with self.assertRaises(c.TraceSnapshotError) as raised:
            await call
        return raised.exception

    def index(self):
        return json.loads((self.root / c.MODEL / "index.json").read_text())


class CaptureTests(ServiceCase):
    async def test_capture_selects_stored_runs_and_reads_only_automation_traces(self):
        self.core.add("r1", minute=1)
        self.core.add("r2", "failed_conditions", minute=2)
        self.core.add("r3", "error", minute=3, error="boom")
        self.core.add("r4", minute=4, not_triggered=True)
        value, partial = await self.service.capture(automation_id="a1", note="synthetic note")
        outcomes = {run["run_id"]: run["outcome"] for run in value["runs"]}
        self.assertEqual(outcomes, {"r1": "captured", "r3": "captured"})
        self.assertEqual(value["selection"]["excluded"], {"not_triggered": 1, "failed_conditions": 1})
        self.assertFalse(partial)
        self.assertEqual(value["core"], {"version": VERSION, "generation": 7})
        self.assertEqual({p["type"] for p in self.core.sent}, {"trace/list", "trace/get"})
        self.assertEqual({p["domain"] for p in self.core.sent}, {"automation"})
        self.assertNotIn("script", json.dumps(self.core.sent))
        self.assertEqual(len(self.index()["snapshots"]), 2)
        self.assertEqual({e["captured_by"] for e in self.index()["snapshots"]}, {self.caller})
        self.assertEqual(self.telemetry.audit_context["outcomes"], ["captured"])

    async def test_condition_failures_are_opt_in(self):
        self.core.add("r2", "failed_conditions")
        value, _ = await self.service.capture(automation_id="a1", include_condition_failures=True)
        self.assertEqual([(r["run_id"], r["outcome"]) for r in value["runs"]], [("r2", "captured")])

    async def test_every_per_run_outcome(self):
        self.core.add("r1")
        value, partial = await self.service.capture(automation_id="a1", run_id="r1")
        self.assertEqual(value["runs"][0]["outcome"], "captured")
        value, partial = await self.service.capture(automation_id="a1", run_id="r1")
        self.assertEqual(value["runs"][0]["outcome"], "already_captured")
        self.assertFalse(partial)
        value, partial = await self.service.capture(automation_id="a1", run_id="gone")
        self.assertEqual(value["runs"], [{"run_id": "gone", "outcome": "trace_not_retained"}])
        self.assertTrue(partial)
        self.core.add("busy", state="running")
        value, _ = await self.service.capture(automation_id="a1", run_id="busy")
        self.assertEqual(value["runs"][0]["outcome"], "run_in_progress")
        self.core.add("bad")
        self.core.fail_get["bad"] = HomeAssistantApiError(details={"status": 500})
        value, _ = await self.service.capture(automation_id="a1", run_id="bad")
        self.assertEqual(value["runs"][0]["outcome"], "read_failed")
        self.core.add("big")
        self.core.details["big"] = {**header("big"), "trace": {
            f"action/{i}": [{"path": f"action/{i}", "result": "x" * 1_900}] for i in range(200)}}
        value, partial = await self.service.capture(automation_id="a1", run_id="big")
        self.assertEqual(value["runs"][0]["outcome"], "over_size_bound")
        self.assertTrue(partial)
        self.assertEqual(sorted(e["run_id"] for e in self.index()["snapshots"]), ["r1"])

    async def test_capture_bound_reports_omitted_runs_as_partial(self):
        for i in range(7):
            self.core.add(f"r{i}", minute=i)
        value, partial = await self.service.capture(automation_id="a1")
        self.assertEqual(len(value["runs"]), c.MAX_CAPTURES)
        self.assertEqual(value["selection"]["omitted_by_capture_bound"], 2)
        self.assertTrue(partial)

    async def test_manual_capture_is_refused_at_capacity_and_evicts_nothing(self):
        for i in range(3):
            self.core.add(f"r{i}", minute=i)
        with patch.object(c, "MAX_SNAPSHOTS", 2):
            value, partial = await self.service.capture(automation_id="a1")
        self.assertEqual([r["outcome"] for r in value["runs"]], ["captured", "captured", "store_full"])
        self.assertTrue(partial)
        self.assertIn("oldest_snapshot_age_seconds", value["store"])
        self.assertEqual(len(self.index()["snapshots"]), 2)

    async def test_authority_refusal_sends_nothing(self):
        self.core.add("r1")
        self.runtime.available = False
        error = await self.refused(self.service.capture(automation_id="a1"))
        self.assertEqual(error.reason, "authority_unavailable")
        self.runtime.available = True
        for authorizer in (None, lambda: False):
            self.telemetry.core_dispatch_authorizer = authorizer
            error = await self.refused(self.service.capture(automation_id="a1"))
            self.assertEqual(error.reason, "authority_unavailable")
        self.assertEqual(self.core.sent, [])
        self.assertFalse((self.root / c.MODEL / "index.json").exists())

    async def test_authority_drift_aborts_and_stores_nothing(self):
        self.core.add("r1")
        self.core.add("r2", minute=1)

        def drift(payload):
            self.runtime.generation = 8
        self.core.on_get = drift
        error = await self.refused(self.service.capture(automation_id="a1"))
        self.assertEqual(error.reason, "authority_drift")
        self.assertFalse((self.root / c.MODEL / "index.json").exists())

    async def test_source_failure_is_a_fixed_refusal(self):
        async def broken(payload):
            raise HomeAssistantApiError(details={"status": 500, "body": h.needle("source")})
        self.service.command = broken
        error = await self.refused(self.service.capture(automation_id="a1"))
        self.assertEqual(error.reason, "source_unavailable")
        self.assertNotIn(h.needle("source"), json.dumps(error.details))

    async def test_invalid_arguments_refuse_before_any_read(self):
        for arguments in ({"automation_id": "../a1"}, {"automation_id": "a1", "run_id": "x/y"},
                          {"automation_id": "a1", "include_condition_failures": 1},
                          {"automation_id": "a1", "note": "n" * (c.NOTE_CHARS + 1)}):
            with self.subTest(arguments=arguments):
                error = await self.refused(self.service.capture(**arguments))
                self.assertEqual(error.reason, "invalid_arguments")
        self.assertEqual(self.core.sent, [])

    async def test_unexpected_caller_shape_is_never_persisted(self):
        self.as_caller("caller with spaces " + h.needle("caller"))
        self.core.add("r1")
        await self.service.capture(automation_id="a1")
        self.assertEqual(self.index()["snapshots"][0]["captured_by"], "unattributed")
        self.assertNotIn(h.needle("caller").encode(), all_store_bytes(self.root))


class SanitizationTests(ServiceCase):
    """No secret reaches the store or a response (design note section 2.3)."""

    NEEDLES = {
        "bearer": lambda n: h.bearer(n + "abcdefghijklmnopqrstuvwxyz"),
        "jwt": lambda n: h.jwt(n),
        "url": lambda n: h.url("https", "user", n, "example.invalid/x"),
        "pem": lambda n: h.private_key_pem(n),
        "pem_prose": lambda n: "key " + h.private_key_pem(n).lower() + " end",
        "note": lambda n: h.bearer(n + "abcdefghijklmnopqrstuvwxyz"),
        "known": lambda n: KNOWN,
        "password": lambda n: n,
    }

    def detail(self):
        values = {kind: build(h.needle(kind)) for kind, build in self.NEEDLES.items()}
        return {
            **header("s1", "error", error="failed with " + values["bearer"]),
            "config": {"alias": "Synthetic", "password": values["password"], "url": values["url"]},
            "blueprint_inputs": {"input": {"value": values["jwt"]}},
            "context": {"id": "ctx", "note": values["known"]},
            "trace": {"action/0": [{"path": "action/0", "changed_variables": {
                "pem": values["pem"], "message": values["pem_prose"]}}]},
        }

    async def test_needles_never_reach_store_or_any_response(self):
        self.core.add("s1", "error")
        self.core.details["s1"] = self.detail()
        note = "see " + self.NEEDLES["note"](h.needle("note"))
        captured, partial = await self.service.capture(automation_id="a1", run_id="s1", note=note)
        self.assertTrue(partial)
        snapshot_id = captured["runs"][0]["snapshot_id"]
        self.assertEqual(captured["runs"][0]["completeness"], "partial")
        listed, _ = await self.service.list()
        pages = []
        cursor = ""
        while True:
            page, page_partial = await self.service.get(snapshot_id=snapshot_id, cursor=cursor)
            self.assertTrue(page_partial)
            pages.append(page)
            cursor = page["page"]["next_cursor"]
            if cursor is None:
                break
        stored = all_store_bytes(self.root)
        rendered = json.dumps([captured, listed, pages])
        for kind in self.NEEDLES:
            needle = h.needle(kind) if kind != "known" else KNOWN
            with self.subTest(kind=kind):
                self.assertNotIn(needle.encode(), stored)
                self.assertNotIn(needle, rendered)
        self.assertGreater(pages[0]["capture_sanitization"]["redacted_field_count"], 0)

    async def test_string_with_a_known_secret_is_withheld_whole_at_capture(self):
        # Review H1: the shared screen withholds the whole string before
        # storage, so its surrounding text is never stored either.
        prose = "surrounding-prose-" + h.needle("prose") + " then " + KNOWN + " end"
        self.core.add("p1")
        self.core.details["p1"] = {**header("p1"), "trace": {
            "action/0": [{"path": "action/0", "result": {"message": prose}}]}}
        captured, _ = await self.service.capture(automation_id="a1", run_id="p1")
        stored = all_store_bytes(self.root)
        self.assertNotIn(h.needle("prose").encode(), stored)
        self.assertNotIn(KNOWN.encode(), stored)
        self.assertIn(b"[withheld:credential_text]", stored)
        page, partial = await self.service.get(snapshot_id=captured["runs"][0]["snapshot_id"])
        self.assertTrue(partial)
        rendered = json.dumps(page)
        self.assertNotIn(h.needle("prose"), rendered)
        self.assertNotIn(KNOWN, rendered)


class ListAndGetTests(ServiceCase):
    async def capture_runs(self, count):
        for i in range(count):
            self.core.add(f"r{i}", minute=i)
            self.clock.now += 1
            await self.service.capture(automation_id="a1", run_id=f"r{i}")

    async def test_list_pages_newest_first_with_a_caller_bound_cursor(self):
        await self.capture_runs(3)
        first, partial = await self.service.list(limit=2)
        self.assertFalse(partial)
        self.assertEqual([e["run_id"] for e in first["snapshots"]], ["r2", "r1"])
        self.assertEqual(first["store"]["snapshots"], 3)
        second, _ = await self.service.list(limit=2, cursor=first["next_cursor"])
        self.assertEqual([e["run_id"] for e in second["snapshots"]], ["r0"])
        self.assertIsNone(second["next_cursor"])
        other, _ = await self.service.list(automation_id="other")
        self.assertEqual(other["snapshots"], [])
        tampered = first["next_cursor"][:-1] + ("0" if first["next_cursor"][-1] != "0" else "1")
        for cursor, kwargs in ((tampered, {}), (first["next_cursor"], {"automation_id": "a1"})):
            error = await self.refused(self.service.list(limit=2, cursor=cursor, **kwargs))
            self.assertEqual(error.reason, "invalid_cursor")
        self.as_caller("another0caller")
        error = await self.refused(self.service.list(limit=2, cursor=first["next_cursor"]))
        self.assertEqual(error.reason, "invalid_cursor")
        restarted = TraceSnapshotService(self.root, self.core.command, self.runtime, self.audit,
                                         clock=self.clock)
        self.as_caller(self.caller)
        error = await self.refused(restarted.list(limit=2, cursor=first["next_cursor"]))
        self.assertEqual(error.reason, "invalid_cursor")

    async def test_get_pages_steps_in_order_and_refuses_foreign_cursors(self):
        self.core.add("g1")
        self.core.details["g1"] = {**header("g1"), "config": {"alias": "Synthetic"}, "trace": {
            f"action/{i}": [{"path": f"action/{i}", "result": "y" * 1_500}] for i in range(60)}}
        self.core.add("g2", minute=1)
        await self.service.capture(automation_id="a1")
        listed, _ = await self.service.list()
        ids = {e["run_id"]: e["snapshot_id"] for e in listed["snapshots"]}
        first, _ = await self.service.get(snapshot_id=ids["g1"])
        self.assertEqual([u["kind"] for u in first["units"][:3]], ["config", "blueprint_inputs", "context"])
        self.assertGreater(first["page"]["count"], 1)
        paths = [u["path"] for u in first["units"] if u["kind"] == "step"]
        cursor = first["page"]["next_cursor"]
        while cursor:
            page, _ = await self.service.get(snapshot_id=ids["g1"], cursor=cursor)
            paths += [u["path"] for u in page["units"]]
            cursor = page["page"]["next_cursor"]
        self.assertEqual(paths, [f"action/{i}" for i in range(60)])
        foreign = first["page"]["next_cursor"]
        error = await self.refused(self.service.get(snapshot_id=ids["g2"], cursor=foreign))
        self.assertEqual(error.reason, "invalid_cursor")
        zero = ids["g1"] + ":0:" + "0" * 64
        error = await self.refused(self.service.get(snapshot_id=ids["g1"], cursor=zero))
        self.assertEqual(error.reason, "invalid_cursor")
        self.as_caller("another0caller")
        error = await self.refused(self.service.get(snapshot_id=ids["g1"], cursor=foreign))
        self.assertEqual(error.reason, "invalid_cursor")
        error = await self.refused(self.service.get(snapshot_id="0" * 32))
        self.assertEqual(error.reason, "snapshot_not_found")

    async def test_damaged_record_is_quarantined_and_never_reflected(self):
        await self.capture_runs(1)
        entry = self.index()["snapshots"][0]
        path = self.root / c.MODEL / "records" / (entry["snapshot_id"] + ".json")
        path.write_bytes(path.read_bytes().replace(b"Synthetic", b"Tampered!"))
        error = await self.refused(self.service.get(snapshot_id=entry["snapshot_id"]))
        self.assertEqual(error.reason, "store_invalid")
        self.assertEqual(self.index()["snapshots"], [])
        stubs = self.index()["quarantine"]["entries"]
        self.assertEqual([(s["snapshot_id"], s["reason"]) for s in stubs],
                         [(entry["snapshot_id"], "digest_mismatch")])
        self.assertFalse(path.exists())
        quarantine = self.root / c.MODEL / "quarantine"
        self.assertEqual(list(quarantine.iterdir()) if quarantine.exists() else [], [])
        self.assertNotIn(b"Tampered", all_store_bytes(self.root))
        self.assertNotIn("Tampered", json.dumps(error.details))

    async def test_corrupt_index_and_symlinks_fail_closed(self):
        await self.capture_runs(1)
        index_path = self.root / c.MODEL / "index.json"
        index_path.write_text('{"model": "' + c.MODEL + '", "snapshots": "' + h.needle("index") + '"}')
        for call in (self.service.list(), self.service.capture(automation_id="a1", run_id="r0"),
                     self.service.watch(action="list")):
            error = await self.refused(call)
            self.assertEqual(error.reason, "store_invalid")
            self.assertNotIn(h.needle("index"), json.dumps(error.details))
        index_path.unlink()
        records = self.root / c.MODEL / "records"
        for child in records.iterdir():
            child.unlink()
        records.rmdir()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        records.symlink_to(elsewhere)
        error = await self.refused(self.service.list())
        self.assertEqual(error.reason, "store_invalid")

    async def test_snapshots_and_watches_expire(self):
        await self.capture_runs(2)
        await self.service.watch(action="add", automation_id="a1", duration_hours=1)
        self.clock.now += 2 * 3600
        listed, _ = await self.service.watch(action="list")
        self.assertEqual(listed["watches"], [])
        self.assertIn("trace_watch_expired", [row["event"] for row in self.audit.rows])
        self.clock.now += c.RETENTION_SECONDS
        listed, _ = await self.service.list()
        self.assertEqual(listed["snapshots"], [])
        self.assertEqual(list((self.root / c.MODEL / "records").iterdir()), [])

    async def test_commit_failure_after_index_replace_blocks_the_store(self):
        self.core.add("r1")

        def fail(stage):
            if stage == "after_index":
                raise OSError("synthetic")
        self.service.store.fault_hook = fail
        error = await self.refused(self.service.capture(automation_id="a1"))
        self.assertEqual(error.reason, "store_unavailable")
        self.service.store.fault_hook = None
        error = await self.refused(self.service.list())
        self.assertEqual(error.reason, "store_unavailable")

    async def test_record_write_failure_leaves_no_orphan(self):
        self.core.add("r1")

        def fail(stage):
            if stage == "before_index":
                raise OSError("synthetic")
        self.service.store.fault_hook = fail
        error = await self.refused(self.service.capture(automation_id="a1"))
        self.assertEqual(error.reason, "store_unavailable")
        self.service.store.fault_hook = None
        self.assertEqual(list((self.root / c.MODEL / "records").iterdir()), [])
        listed, _ = await self.service.list()
        self.assertEqual(listed["snapshots"], [])


class WatchTests(ServiceCase):
    async def test_add_replace_list_remove_are_audited_with_the_caller(self):
        added, partial = await self.service.watch(action="add", automation_id="a1")
        self.assertFalse(partial)
        self.assertTrue(added["changed"])
        self.assertEqual(added["watch"]["created_by"], self.caller)
        self.assertEqual(added["watch"]["status"], "awaiting_first_poll")
        replaced, _ = await self.service.watch(action="add", automation_id="a1", mode="executed",
                                               duration_hours=c.WATCH_MAX_HOURS)
        self.assertEqual(replaced["watch"]["watch_id"], added["watch"]["watch_id"])
        self.assertEqual(replaced["watch"]["mode"], "executed")
        listed, _ = await self.service.watch(action="list")
        self.assertFalse(listed["changed"])
        self.assertEqual(len(listed["watches"]), 1)
        removed, _ = await self.service.watch(action="remove", automation_id="a1")
        self.assertEqual(removed["watches"], [])
        rows = [(r["event"], r["result_status"], r["caller_id"], r.get("replaced")) for r in self.audit.rows]
        self.assertEqual(rows, [("trace_watch_change", "add", self.caller, False),
                                ("trace_watch_change", "add", self.caller, True),
                                ("trace_watch_change", "remove", self.caller, False)])
        self.assertEqual(self.core.sent, [])

    async def test_eleventh_watch_is_refused_with_the_current_list(self):
        for i in range(c.MAX_WATCHES):
            await self.service.watch(action="add", automation_id=f"w{i}")
        error = await self.refused(self.service.watch(action="add", automation_id="w_extra"))
        self.assertEqual(error.reason, "watch_limit")
        self.assertEqual(len(error.details["watches"]), c.MAX_WATCHES)
        self.assertEqual(len(self.index()["watches"]), c.MAX_WATCHES)
        error = await self.refused(self.service.watch(action="remove", automation_id="w_extra"))
        self.assertEqual(error.reason, "watch_not_found")

    async def test_unavailable_audit_refuses_the_change(self):
        await self.service.watch(action="add", automation_id="a1")
        for broken in ("fail", "disabled"):
            with self.subTest(broken=broken):
                self.audit.fail = broken == "fail"
                self.audit.enabled = broken != "disabled"
                error = await self.refused(self.service.watch(action="remove", automation_id="a1"))
                self.assertEqual(error.reason, "audit_unavailable")
                self.assertEqual(len(error.details["watches"]), 1)
                self.assertEqual(len(self.index()["watches"]), 1)
        self.audit.fail, self.audit.enabled = False, True

    async def test_invalid_watch_arguments(self):
        for arguments in ({"action": "delete", "automation_id": "a1"},
                          {"action": "add", "automation_id": "a1", "duration_hours": 0},
                          {"action": "add", "automation_id": "a1", "duration_hours": c.WATCH_MAX_HOURS + 1},
                          {"action": "add", "automation_id": "a1", "mode": "all"},
                          {"action": "list", "automation_id": "a1"},
                          {"action": "add", "automation_id": ""}):
            with self.subTest(arguments=arguments):
                error = await self.refused(self.service.watch(**arguments))
                self.assertEqual(error.reason, "invalid_arguments")
        self.assertEqual(self.audit.rows, [])


class IdentifierTests(ServiceCase):
    """Review H2: a withheld identifier is persisted, audited and returned only as a digest."""

    # A run ID the default screen withholds (it holds a known secret); still
    # a valid Core run ID. ``run-token-1`` is shown (correction batch 2).
    WITHHELD_RUN = "run-" + KNOWN

    def sinks(self, *responses):
        return {
            "store": all_store_bytes(self.root),
            "audit": json.dumps(self.audit.rows).encode(),
            "audit_context": json.dumps(self.telemetry.audit_context).encode(),
            "responses": json.dumps(responses).encode(),
        }

    def assert_absent(self, raw, *responses):
        for sink, found in self.sinks(*responses).items():
            with self.subTest(sink=sink):
                self.assertNotIn(raw.encode(), found)

    async def test_capture_persists_and_returns_only_digests(self):
        self.core.add(self.WITHHELD_RUN)
        # Named explicitly: the HAMCP-191 list screen already redacts a listed
        # run ID that holds a known secret, so it is never auto-selected.
        captured, _ = await self.service.capture(automation_id=KNOWN, run_id=self.WITHHELD_RUN)
        stored_id = captured["automation_id"]
        self.assertRegex(stored_id, c.WITHHELD_ID)
        run = captured["runs"][0]
        self.assertEqual(run["outcome"], "captured")
        self.assertRegex(run["run_id"], c.WITHHELD_ID)
        # The raw IDs were still what Core was read with.
        self.assertEqual(self.core.sent[-1]["item_id"], KNOWN)
        self.assertEqual(self.core.sent[-1]["run_id"], self.WITHHELD_RUN)
        entry = self.index()["snapshots"][0]
        self.assertEqual((entry["automation_id"], entry["run_id"]), (stored_id, run["run_id"]))
        # The caller filters by the raw ID; the digest is stable in-process.
        listed, _ = await self.service.list(automation_id=KNOWN)
        self.assertEqual([e["snapshot_id"] for e in listed["snapshots"]], [run["snapshot_id"]])
        page, _ = await self.service.get(snapshot_id=run["snapshot_id"])
        again, _ = await self.service.capture(automation_id=KNOWN, run_id=self.WITHHELD_RUN)
        self.assertEqual((again["automation_id"], again["requested_run_id"], again["runs"][0]["outcome"]),
                         (stored_id, run["run_id"], "already_captured"))
        self.assert_absent(KNOWN, captured, listed, page, again)
        self.assert_absent(self.WITHHELD_RUN, captured, listed, page, again)

    async def test_clean_identifiers_are_kept(self):
        self.core.add("r1")
        captured, _ = await self.service.capture(automation_id="a1")
        self.assertEqual((captured["automation_id"], captured["runs"][0]["run_id"]), ("a1", "r1"))
        self.assertEqual(self.service.watch_sources, {})

    async def test_watch_rows_audit_and_polls_use_the_digest(self):
        added, _ = await self.service.watch(action="add", automation_id=KNOWN, mode="executed")
        stored_id = added["watch"]["automation_id"]
        self.assertRegex(stored_id, c.WITHHELD_ID)
        await self.service.poll_once(authority=(7, VERSION))
        self.core.add("r1")
        await self.service.poll_once(authority=(7, VERSION))
        await self.service.poll_once(authority=(7, VERSION))
        self.assertEqual({p["item_id"] for p in self.core.sent}, {KNOWN})
        entries = self.index()["snapshots"]
        self.assertEqual([(e["automation_id"], e["origin"]) for e in entries], [(stored_id, "trace_watch")])
        watch = self.index()["watches"][0]
        self.assertEqual(watch["seen_run_ids"], [entries[0]["run_id"]])
        listed, _ = await self.service.watch(action="list")
        removed, _ = await self.service.watch(action="remove", automation_id=KNOWN)
        self.assertEqual(removed["watches"], [])
        self.assertEqual(self.service.watch_sources, {})
        self.assertEqual([r["automation_id"] for r in self.audit.rows],
                         [stored_id, stored_id, stored_id])
        self.assert_absent(KNOWN, added, listed, removed)

    async def test_refusals_never_echo_a_withheld_identifier(self):
        await self.service.watch(action="add", automation_id=KNOWN)
        for i in range(c.MAX_WATCHES - 1):
            await self.service.watch(action="add", automation_id=f"w{i}")
        limit = await self.refused(self.service.watch(action="add", automation_id="w_extra"))
        self.assertEqual(limit.reason, "watch_limit")
        await self.service.watch(action="remove", automation_id="w0")
        missing = await self.refused(self.service.watch(action="remove",
                                                        automation_id=KNOWN + "x"))
        self.assertEqual(missing.reason, "watch_not_found")
        self.audit.fail = True
        audit = await self.refused(self.service.watch(action="remove", automation_id=KNOWN))
        self.assertEqual(audit.reason, "audit_unavailable")
        for error in (limit, missing, audit):
            with self.subTest(reason=error.reason):
                self.assertNotIn(KNOWN, json.dumps(error.details))
                self.assertEqual(sum(w["automation_id"].startswith("withheld_")
                                     for w in error.details["watches"]), 1)

    async def test_digest_shaped_input_is_refused(self):
        digest = "withheld_" + "0" * 32
        for call in (self.service.capture(automation_id=digest),
                     self.service.capture(automation_id="a1", run_id=digest),
                     self.service.list(automation_id=digest),
                     self.service.watch(action="add", automation_id=digest),
                     self.service.watch(action="remove", automation_id=digest)):
            error = await self.refused(call)
            self.assertEqual(error.reason, "invalid_arguments")
        self.assertEqual((self.core.sent, self.audit.rows), ([], []))

    async def test_a_restart_ends_a_watch_whose_identifier_was_withheld(self):
        await self.service.watch(action="add", automation_id=KNOWN)
        await self.service.watch(action="add", automation_id="a1")
        restarted = TraceSnapshotService(self.root, self.core.command, self.runtime, self.audit,
                                         known_secrets=(KNOWN,), clock=self.clock)
        await restarted.poll_once(authority=(7, VERSION))
        self.assertEqual([w["automation_id"] for w in self.index()["watches"]], ["a1"])
        self.assertEqual({p["item_id"] for p in self.core.sent}, {"a1"})
        ended = self.audit.rows[-1]
        self.assertEqual((ended["event"], ended["result_status"]),
                         ("trace_watch_expired", "identifier_unavailable"))
        self.assertRegex(ended["automation_id"], c.WITHHELD_ID)
        # The caller may add it again: a new digest, polled with the raw ID.
        added, _ = await restarted.watch(action="add", automation_id=KNOWN)
        self.assertNotEqual(added["watch"]["automation_id"], ended["automation_id"])
        self.assert_absent(KNOWN, added)

    async def test_quarantine_stub_keeps_only_the_digest(self):
        self.core.add("r1")
        captured, _ = await self.service.capture(automation_id=KNOWN, run_id="r1")
        snapshot_id = captured["runs"][0]["snapshot_id"]
        path = self.root / c.MODEL / "records" / (snapshot_id + ".json")
        path.write_bytes(path.read_bytes().replace(b"Synthetic", b"Tampered!"))
        error = await self.refused(self.service.get(snapshot_id=snapshot_id))
        self.assertEqual(error.reason, "store_invalid")
        stub = self.index()["quarantine"]["entries"][0]
        self.assertEqual(stub["automation_id"], captured["automation_id"])
        self.assert_absent(KNOWN, captured)

    def test_gateway_audit_parameters_never_copy_a_withheld_identifier(self):
        for tool, arguments in (
                ("capture_automation_trace", {"automation_id": KNOWN, "run_id": self.WITHHELD_RUN}),
                ("list_trace_snapshots", {"automation_id": KNOWN}),
                ("set_automation_trace_watch", {"action": "add", "automation_id": KNOWN})):
            with self.subTest(tool=tool):
                audit = _trace_snapshot_audit_parameters(tool, arguments, (KNOWN,))
                self.assertIsNone(audit["automation_id"])
                self.assertTrue(audit["identifiers_withheld"])
                self.assertNotIn(KNOWN, json.dumps(audit))
                self.assertNotIn(self.WITHHELD_RUN, json.dumps(audit))
        clean = _trace_snapshot_audit_parameters("capture_automation_trace",
                                                 {"automation_id": "a1", "run_id": "r1"}, (KNOWN,))
        self.assertEqual((clean["automation_id"], clean["run_id"], clean["identifiers_withheld"]),
                         ("a1", "r1", False))


class IdentifierModeTests(ServiceCase):
    """Correction batch 2 (owner decision): Home Assistant's own identifiers and
    names use the shared screen's default mode; trace values stay labelled."""

    BENIGN_ID = "rotate_api_token"
    BENIGN_RUN = "run-token-1"
    URI = "mqtt" + "://user:" + h.needle("identity") + "@host.invalid"

    async def test_benign_identifiers_are_shown_in_every_sink(self):
        self.assertIsNotNone(credentials.screen(self.BENIGN_ID, labelled=True))
        self.core.add(self.BENIGN_RUN)
        captured, _ = await self.service.capture(automation_id=self.BENIGN_ID)
        run = captured["runs"][0]
        self.assertEqual((captured["automation_id"], run["run_id"], run["outcome"]),
                         (self.BENIGN_ID, self.BENIGN_RUN, "captured"))
        entry = self.index()["snapshots"][0]
        self.assertEqual((entry["automation_id"], entry["run_id"]), (self.BENIGN_ID, self.BENIGN_RUN))
        listed, _ = await self.service.list(automation_id=self.BENIGN_ID)
        self.assertEqual([e["automation_id"] for e in listed["snapshots"]], [self.BENIGN_ID])
        page, partial = await self.service.get(snapshot_id=run["snapshot_id"])
        self.assertFalse(partial)
        self.assertEqual(page["snapshot"]["run_id"], self.BENIGN_RUN)
        added, _ = await self.service.watch(action="add", automation_id=self.BENIGN_ID)
        self.assertEqual(added["watch"]["automation_id"], self.BENIGN_ID)
        self.assertEqual(self.service.watch_sources, {})
        self.assertEqual(self.audit.rows[-1]["automation_id"], self.BENIGN_ID)
        audit = _trace_snapshot_audit_parameters(
            "capture_automation_trace", {"automation_id": self.BENIGN_ID, "run_id": self.BENIGN_RUN},
            (KNOWN,))
        self.assertEqual((audit["automation_id"], audit["run_id"], audit["identifiers_withheld"]),
                         (self.BENIGN_ID, self.BENIGN_RUN, False))

    async def test_ha_names_are_shown_and_trace_values_stay_labelled(self):
        self.core.add("r1")
        self.core.details["r1"] = {**header("r1"), "config": {
            "id": self.BENIGN_ID, "alias": "Token refresh",
            "triggers": [{"trigger": "state", "entity_id": ["sensor.api_token"], "id": "token_seen"}],
            "variables": {"message": "Token refresh"}},
            "context": {"id": "ctx"}, "trace": {"action/0": [{"path": "action/0", "variables": {
                "trigger": {"entity_id": "sensor.api_token",
                            "to_state": {"attributes": {"friendly_name": "API token"}}}}}]}}
        captured, _ = await self.service.capture(automation_id="a1", run_id="r1")
        page, partial = await self.service.get(snapshot_id=captured["runs"][0]["snapshot_id"])
        units = {unit["kind"]: unit["value"] for unit in page["units"] if unit["kind"] != "step"}
        step = next(unit["value"] for unit in page["units"] if unit["kind"] == "step")
        config = units["config"]
        self.assertEqual((config["id"], config["alias"], config["triggers"][0]["entity_id"],
                          config["triggers"][0]["id"]),
                         (self.BENIGN_ID, "Token refresh", ["sensor.api_token"], "token_seen"))
        trigger = step["variables"]["trigger"]
        self.assertEqual((trigger["entity_id"], trigger["to_state"]["attributes"]["friendly_name"]),
                         ("sensor.api_token", "API token"))
        # The same text outside an identifier or name keeps the labelled screen.
        self.assertEqual(config["variables"]["message"], credentials.WITHHELD_MARKER)
        self.assertTrue(partial)

    async def test_credentials_in_identifiers_and_names_are_still_withheld(self):
        self.core.add("r1")
        self.core.details["r1"] = {**header("r1"), "config": {
            "alias": self.URI, "id": KNOWN.upper(), "entity_id": ["sensor.ok", self.URI]},
            "context": {"id": "ctx"}, "trace": {}}
        captured, _ = await self.service.capture(automation_id="a1", run_id="r1")
        page, partial = await self.service.get(snapshot_id=captured["runs"][0]["snapshot_id"])
        config = next(unit["value"] for unit in page["units"] if unit["kind"] == "config")
        self.assertEqual(config, {"alias": credentials.WITHHELD_MARKER, "id": credentials.WITHHELD_MARKER,
                                  "entity_id": ["sensor.ok", credentials.WITHHELD_MARKER]})
        self.assertTrue(partial)
        for value in (self.URI, KNOWN, KNOWN.upper()):
            with self.subTest(value=value[:12]):
                self.assertRegex(self.service._persisted(value), c.WITHHELD_ID)
        self.assertNotIn(h.needle("identity").encode(), all_store_bytes(self.root))
        self.assertNotIn(KNOWN.upper().encode(), all_store_bytes(self.root))

    async def test_a_benign_watch_survives_a_restart(self):
        await self.service.watch(action="add", automation_id=self.BENIGN_ID, mode="executed")
        restarted = TraceSnapshotService(self.root, self.core.command, self.runtime, self.audit,
                                         known_secrets=(KNOWN,), clock=self.clock)
        await restarted.poll_once(authority=(7, VERSION))
        watches = self.index()["watches"]
        self.assertEqual([(w["automation_id"], w["baseline_done"]) for w in watches],
                         [(self.BENIGN_ID, True)])
        self.assertEqual({p["item_id"] for p in self.core.sent}, {self.BENIGN_ID})
        self.assertNotIn("identifier_unavailable", [r["result_status"] for r in self.audit.rows])


class QuarantineTests(ServiceCase):
    """H3: damaged records are deleted; only bounded content-free stubs remain."""

    async def corrupt(self, run_id):
        self.core.add(run_id)
        self.clock.now += 1
        value, _ = await self.service.capture(automation_id="a1", run_id=run_id)
        snapshot_id = value["runs"][0]["snapshot_id"]
        path = self.root / c.MODEL / "records" / (snapshot_id + ".json")
        path.write_bytes(path.read_bytes().replace(b"Synthetic", b"Tampered!"))
        error = await self.refused(self.service.get(snapshot_id=snapshot_id))
        self.assertEqual(error.reason, "store_invalid")
        return snapshot_id

    async def test_repeated_corruption_cannot_grow_storage_past_the_bound(self):
        sizes = []
        with patch.object(c, "QUARANTINE_MAX", 2):
            ids = []
            for i in range(6):
                ids.append(await self.corrupt(f"r{i}"))
                sizes.append(len(all_store_bytes(self.root)))
            stubs = self.index()["quarantine"]
            self.assertEqual([s["snapshot_id"] for s in stubs["entries"]], ids[-2:])
            self.assertEqual(stubs["dropped"], 4)
            listing, partial = await self.service.list()
        self.assertEqual((listing["store"]["quarantined"], listing["store"]["quarantine_dropped"]), (2, 4))
        self.assertEqual(listing["store"]["bytes"], 0)
        self.assertEqual(list((self.root / c.MODEL / "records").iterdir()), [])
        self.assertLessEqual(max(sizes[2:]) - min(sizes[2:]), 64)
        stored = all_store_bytes(self.root)
        self.assertNotIn(b"Tampered", stored)
        self.assertNotIn(b"Synthetic", stored)
        for stub in self.index()["quarantine"]["entries"]:
            self.assertEqual(set(stub), store_module.QUARANTINE_KEYS)
            self.assertEqual(stub["reason"], "digest_mismatch")

    async def test_stubs_expire_on_the_retention_rule(self):
        await self.corrupt("r0")
        self.assertEqual(len(self.index()["quarantine"]["entries"]), 1)
        self.clock.now += c.RETENTION_SECONDS + 1
        listing, _ = await self.service.list()
        self.assertEqual(listing["store"]["quarantined"], 0)
        self.assertEqual(self.index()["quarantine"]["entries"], [])

    async def test_a_missing_record_is_a_stub_and_never_reflected(self):
        self.core.add("r0")
        value, _ = await self.service.capture(automation_id="a1", run_id="r0")
        snapshot_id = value["runs"][0]["snapshot_id"]
        (self.root / c.MODEL / "records" / (snapshot_id + ".json")).unlink()
        error = await self.refused(self.service.get(snapshot_id=snapshot_id))
        self.assertEqual(error.reason, "store_invalid")
        stub = self.index()["quarantine"]["entries"][0]
        self.assertEqual((stub["reason"], stub["observed_bytes"], stub["observed_sha256"]),
                         ("missing", None, None))

    def test_index_refuses_an_oversized_or_content_bearing_quarantine(self):
        stub = {"snapshot_id": "a" * 32, "automation_id": "a1", "run_id": "r0",
                "quarantined_at_ms": 1, "reason": "digest_mismatch", "observed_bytes": 3,
                "observed_sha256": "0" * 64}
        index = store_module.empty_index()
        index["quarantine"]["entries"] = [stub]
        store_module.validate_index(index)
        for bad in ({"entries": [stub] * (c.QUARANTINE_MAX + 1), "dropped": 0},
                    {"entries": [{**stub, "content": "x"}], "dropped": 0},
                    {"entries": [{**stub, "reason": "other"}], "dropped": 0},
                    {"entries": [stub], "dropped": -1}):
            with self.subTest(bad=list(bad["entries"][0])[-1]):
                with self.assertRaises(store_module.StoreError):
                    store_module.validate_index({**index, "quarantine": bad})


class PollTests(ServiceCase):
    AUTHORITY = (7, VERSION)

    async def poll(self, authority=AUTHORITY):
        await self.service.poll_once(authority=authority)

    async def test_first_poll_is_a_baseline_then_new_failures_are_captured(self):
        self.core.add("old", "error", minute=0)
        self.clock.now = NOW + 2 * 3600 + 27 * 60  # 10:00:20, after "old" finished
        await self.service.watch(action="add", automation_id="a1")
        await self.poll()
        self.assertEqual(self.index()["snapshots"], [])
        self.core.add("ok", minute=1)
        self.core.add("bad", "aborted", minute=2)
        self.core.add("err", minute=3, error="synthetic error")
        self.core.add("cond", "failed_conditions", minute=4)
        self.core.add("busy", "error", minute=5, state="running")
        await self.poll()
        stored = {e["run_id"]: e for e in self.index()["snapshots"]}
        self.assertEqual(set(stored), {"bad", "err"})
        self.assertEqual({e["origin"] for e in stored.values()}, {"trace_watch"})
        listed, _ = await self.service.watch(action="list")
        watch = listed["watches"][0]
        self.assertEqual((watch["status"], watch["captured_count"], watch["snapshots_held"]),
                         ("watching", 2, 2))
        self.assertIn("trace_watch_capture", [r["event"] for r in self.audit.rows])
        self.core.runs[-1]["state"] = "stopped"
        await self.poll()
        self.assertIn("busy", {e["run_id"] for e in self.index()["snapshots"]})
        self.assertEqual({p["domain"] for p in self.core.sent}, {"automation"})

    async def test_executed_mode_skips_condition_failures(self):
        await self.service.watch(action="add", automation_id="a1", mode="executed")
        await self.poll()
        self.core.add("ok", minute=1)
        self.core.add("cond", "failed_conditions", minute=2)
        await self.poll()
        self.assertEqual([e["run_id"] for e in self.index()["snapshots"]], ["ok"])

    async def test_a_tick_without_authority_pauses_and_sends_nothing(self):
        await self.service.watch(action="add", automation_id="a1")
        await self.poll()
        sent = len(self.core.sent)
        await self.poll(None)
        await self.poll(None)
        self.assertEqual(len(self.core.sent), sent)
        listed, _ = await self.service.watch(action="list")
        self.assertEqual((listed["watches"][0]["status"], listed["watches"][0]["skipped_polls"]),
                         ("paused_core_unavailable", 2))

    async def test_watch_quota_evicts_only_its_own_oldest(self):
        self.core.add("manual0", minute=0)
        await self.service.capture(automation_id="a1", run_id="manual0")
        await self.service.watch(action="add", automation_id="a1", mode="executed")
        await self.poll()
        with patch.object(c, "WATCH_QUOTA", 2):
            for i in range(1, 4):
                self.clock.now += 1
                self.core.add(f"w{i}", minute=i)
                await self.poll()
        runs = sorted(e["run_id"] for e in self.index()["snapshots"])
        self.assertEqual(runs, ["manual0", "w2", "w3"])
        listed, _ = await self.service.watch(action="list")
        self.assertEqual(listed["watches"][0]["evicted_count"], 1)
        evictions = [r["evicted_snapshot_ids"] for r in self.audit.rows if r["event"] == "trace_watch_capture"]
        self.assertEqual([len(ids) for ids in evictions], [0, 0, 1])
        self.assertEqual(len(list((self.root / c.MODEL / "records").iterdir())), 3)

    async def test_manual_snapshots_are_never_evicted_for_a_watch(self):
        for i in range(2):
            self.core.add(f"m{i}", minute=i)
        await self.service.capture(automation_id="a1")
        await self.service.watch(action="add", automation_id="a1", mode="executed")
        await self.poll()
        self.core.add("w1", minute=5)
        with patch.object(c, "MAX_SNAPSHOTS", 2):
            await self.poll()
        self.assertEqual(sorted(e["run_id"] for e in self.index()["snapshots"]), ["m0", "m1"])
        listed, _ = await self.service.watch(action="list")
        self.assertEqual(listed["watches"][0]["uncaptured_runs"], 1)

    async def test_failed_capture_audit_stores_nothing_and_counts_a_gap(self):
        await self.service.watch(action="add", automation_id="a1", mode="executed")
        await self.poll()
        self.core.add("w1", minute=1)
        self.audit.fail = True
        await self.poll()
        self.audit.fail = False
        self.assertEqual(self.index()["snapshots"], [])
        listed, _ = await self.service.watch(action="list")
        self.assertEqual(listed["watches"][0]["uncaptured_runs"], 1)

    async def test_watch_removed_during_poll_stores_nothing(self):
        await self.service.watch(action="add", automation_id="a1", mode="executed")
        await self.poll()
        self.core.add("w1", minute=1)
        removing = []

        def remove(payload):
            index = self.index()
            index["watches"] = []
            (self.root / c.MODEL / "index.json").write_bytes(c.canonical(index))
            removing.append(payload)
        self.core.on_get = remove
        await self.poll()
        self.assertEqual(len(removing), 1)
        self.assertEqual(self.index()["snapshots"], [])

    async def test_first_poll_captures_runs_finished_after_the_watch_was_created(self):
        self.core.add("before", "error", minute=0)
        self.clock.now = NOW + 2 * 3600 + 27 * 60  # 10:00:20
        await self.service.watch(action="add", automation_id="a1")
        self.core.add("after", "error", minute=1)
        self.core.add("busy", "error", minute=2, state="running")
        # A restart between the add and the first tick keeps the rule.
        restarted = TraceSnapshotService(self.root, self.core.command, self.runtime, self.audit,
                                         known_secrets=(KNOWN,), clock=self.clock)
        await restarted.poll_once(authority=self.AUTHORITY)
        self.assertEqual([e["run_id"] for e in self.index()["snapshots"]], ["after"])
        self.core.runs[-1]["state"] = "stopped"
        await restarted.poll_once(authority=self.AUTHORITY)
        self.assertEqual(sorted(e["run_id"] for e in self.index()["snapshots"]), ["after", "busy"])

    async def two_watches(self):
        await self.service.watch(action="add", automation_id="a1", mode="executed")
        self.clock.now += 1
        await self.service.watch(action="add", automation_id="a2", mode="executed")
        await self.poll()
        await self.poll()  # two baseline ticks: the next one starts with a1 again
        self.core.add("w1", minute=1)
        self.core.sent.clear()

    async def test_slow_first_watch_cannot_starve_the_next_and_the_start_rotates(self):
        await self.two_watches()

        async def slow(payload):
            if payload["item_id"] == "a1" and payload["type"] == "trace/get":
                await asyncio.sleep(0.3)
            return await self.core.command(payload)
        self.service.command = slow
        await self.service.poll_once(authority=self.AUTHORITY, deadline=time.monotonic() + 0.2)
        lists = [p["item_id"] for p in self.core.sent if p["type"] == "trace/list"]
        held = {(e["automation_id"], e["run_id"]) for e in self.index()["snapshots"]}
        self.assertIn(("a2", "w1"), held)
        self.assertNotIn(("a1", "w1"), held)
        self.core.sent.clear()
        self.service.command = self.core.command
        await self.poll()
        lists.append([p["item_id"] for p in self.core.sent if p["type"] == "trace/list"])
        # Deferred, not a gap: the slow watch captures its run on the next tick.
        self.assertEqual(lists, ["a1", "a2", ["a2", "a1"]])
        self.assertIn(("a1", "w1"), {(e["automation_id"], e["run_id"]) for e in self.index()["snapshots"]})
        listed, _ = await self.service.watch(action="list")
        self.assertEqual([w["uncaptured_runs"] for w in listed["watches"]], [0, 0])

    async def test_an_exhausted_tick_reads_nothing_and_counts_every_skip(self):
        await self.two_watches()
        await self.service.poll_once(authority=self.AUTHORITY, deadline=time.monotonic() - 1)
        self.assertEqual(self.core.sent, [])
        listed, _ = await self.service.watch(action="list")
        self.assertEqual([w["skipped_polls"] for w in listed["watches"]], [1, 1])

    async def test_authority_retired_before_commit_stores_nothing_and_pauses(self):
        await self.two_watches()
        authorized = [True]
        self.telemetry.core_dispatch_authorizer = lambda: authorized[0]
        self.core.on_get = lambda payload: authorized.__setitem__(0, False)
        await self.poll()
        self.assertEqual(self.index()["snapshots"], [])
        self.assertTrue(self.service.paused)
        self.assertEqual([p["item_id"] for p in self.core.sent if p["type"] == "trace/list"], ["a1"])
        self.assertNotIn("trace_watch_capture", [r["event"] for r in self.audit.rows])
        listed, _ = await self.service.watch(action="list")
        self.assertEqual([(w["status"], w["skipped_polls"], w["uncaptured_runs"]) for w in listed["watches"]],
                         [("paused_core_unavailable", 1, 0)] * 2)

    async def test_generation_change_before_commit_stores_nothing(self):
        await self.two_watches()
        self.core.on_get = lambda payload: setattr(self.runtime, "generation", 8)
        await self.poll()
        self.assertEqual(self.index()["snapshots"], [])
        self.assertTrue(self.service.paused)


class QueuedCommitTests(ServiceCase):
    """Correction batch 2: authority is checked again inside the store lock,
    immediately before writing; a commit queued across retirement writes nothing."""

    async def queue_behind_lock(self, call, retire):
        """Run ``call``; ``retire()`` after its pre-lock checks passed, while its
        commit waits for the store lock (another holder releases it after)."""

        original = self.service._run

        async def observe(function, *args, **kwargs):
            if function == self.service._commit:
                await self.service.lock.acquire()
                try:
                    blocked = asyncio.create_task(original(function, *args, **kwargs))
                    await asyncio.sleep(0)
                    retire()
                finally:
                    self.service.lock.release()
                return await blocked
            return await original(function, *args, **kwargs)

        with patch.object(self.service, "_run", observe):
            return await asyncio.gather(call(), return_exceptions=True)

    async def watched(self):
        await self.service.watch(action="add", automation_id="a1", mode="executed")
        await self.service.poll_once(authority=(7, VERSION))
        self.core.add("r1")
        return self.index()["watches"][0]

    def retire_generation(self):
        self.runtime.generation += 1

    def expire_lease(self):
        self.telemetry.core_dispatch_authorizer = lambda: False

    async def test_queued_watch_commit_writes_nothing_after_retirement(self):
        for name, retire in (("generation", self.retire_generation), ("lease", self.expire_lease)):
            with self.subTest(retired=name):
                await self.asyncSetUp()
                before = await self.watched()
                audit_rows = len(self.audit.rows)
                [polled] = await self.queue_behind_lock(
                    lambda: self.service.poll_once(authority=(7, VERSION)), retire)
                self.assertIsNone(polled)
                self.assertEqual(self.index()["snapshots"], [])
                self.assertTrue(self.service.paused)
                after = self.index()["watches"][0]
                # Not even poll state: the run is not consumed and is read again.
                self.assertEqual(after, before)
                self.assertEqual(len(self.audit.rows), audit_rows)
                self.assertEqual(list((self.root / c.MODEL / "records").iterdir()), [])
                await self.asyncTearDown()
        await self.asyncSetUp()

    async def test_retired_commit_is_read_again_once_authority_returns(self):
        await self.watched()
        await self.queue_behind_lock(lambda: self.service.poll_once(authority=(7, VERSION)),
                                     self.retire_generation)
        await self.service.poll_once(authority=(8, VERSION))
        self.assertEqual([e["run_id"] for e in self.index()["snapshots"]], ["r1"])

    async def test_audit_fallback_commit_also_rechecks(self):
        before = await self.watched()

        def fail_and_retire(entry):
            self.runtime.generation += 1
            return False

        self.audit.write = fail_and_retire
        polled = await self.service._poll_watch(before, (7, VERSION))
        self.assertFalse(polled)
        self.assertTrue(self.service.paused)
        self.assertEqual(self.index()["snapshots"], [])
        self.assertEqual(self.index()["watches"][0], before)

    async def test_queued_manual_capture_is_refused_after_retirement(self):
        self.core.add("r1")
        [error] = await self.queue_behind_lock(
            lambda: self.service.capture(automation_id="a1", run_id="r1"), self.retire_generation)
        self.assertIsInstance(error, c.TraceSnapshotError)
        self.assertEqual(error.reason, "authority_drift")
        self.assertEqual(self.index()["snapshots"] if (self.root / c.MODEL / "index.json").exists()
                         else [], [])
        self.assertEqual(self.audit.rows, [])

    async def test_unanswered_or_failing_check_refuses(self):
        def broken():
            raise RuntimeError("synthetic")

        self.assertFalse(await asyncio.to_thread(self.service._on_loop(broken)))
        self.assertFalse(await asyncio.to_thread(self.service._on_loop(lambda: "yes")))
        self.assertTrue(await asyncio.to_thread(self.service._on_loop(lambda: True)))
        with patch.object(service_module, "_GUARD_SECONDS", 0.05):
            ask = self.service._on_loop(lambda: True)
            # The loop is blocked by this synchronous call, so no answer comes.
            self.assertFalse(ask())


class PollerAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_route_acquires_nothing_and_refuses(self):
        runtime = FakeRuntime()
        runtime.available = False
        with watch_poll_authority(runtime, asyncio.get_running_loop().time() + 60) as authority:
            from ha_mcp_engineering.request_context import current_telemetry
            telemetry = current_telemetry()
            self.assertIsNone(authority)
            self.assertFalse(telemetry.authorize_core_dispatch())
            self.assertEqual(telemetry.caller_id, "trace_watch")
        self.assertNotIn(("acquire", c.REQUIREMENTS), runtime.calls)

    async def test_lease_is_consumed_revalidated_and_closed(self):
        import time
        runtime = FakeRuntime()
        from ha_mcp_engineering.request_context import current_telemetry
        with watch_poll_authority(runtime, time.monotonic() + 60) as authority:
            telemetry = current_telemetry()
            self.assertEqual(authority, (7, VERSION))
            self.assertTrue(telemetry.authorize_core_dispatch())
            runtime.available = False
            self.assertFalse(telemetry.authorize_core_dispatch())
            runtime.available = True
            self.assertFalse(telemetry.authorize_core_dispatch())
        self.assertFalse(telemetry.authorize_core_dispatch())
        self.assertIn(("finish",), runtime.calls)
        with watch_poll_authority(runtime, time.monotonic() - 1) as authority:
            self.assertFalse(current_telemetry().authorize_core_dispatch())
        self.assertIn(("release",), runtime.calls)

    async def test_tick_dispatches_only_inside_its_own_lease(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        core = FakeCore()
        core.add("r1")
        authorized = []
        from ha_mcp_engineering.request_context import current_telemetry

        async def command(payload):
            authorized.append(current_telemetry().authorize_core_dispatch())
            return await core.command(payload)
        service = TraceSnapshotService(directory.name, command, FakeRuntime(), FakeAudit())
        telemetry, token = begin_request()
        telemetry.caller_id = "owner0caller"
        try:
            await service.watch(action="add", automation_id="a1")
        finally:
            end_request(token)
        poller = TraceWatchPoller(service, service.core_runtime)
        await poller.tick()
        self.assertEqual(authorized, [True])
        service.core_runtime.available = False
        await poller.tick()
        self.assertEqual(authorized, [True])
        self.assertTrue(service.paused)

    async def test_runtime_without_configuration_refuses(self):
        runtime = TraceSnapshotRuntime()
        with self.assertRaises(c.TraceSnapshotError):
            runtime.require()
        self.assertIsNone(runtime.start_poller())
        await runtime.shutdown()
        self.assertEqual(runtime.health(), {"configured": False})


class ContractAndToolTests(unittest.IsolatedAsyncioTestCase):
    def test_closed_contracts_reject_coercion_and_unknown_fields(self):
        refused = [
            (c.validate_capture, {"automation_id": "a1", "extra": 1}),
            (c.validate_capture, {"automation_id": 1}),
            (c.validate_list, {"limit": True}),
            (c.validate_list, {"limit": "5"}),
            (c.validate_list, {"limit": c.LIST_LIMIT + 1}),
            (c.validate_list, {"cursor": "1:" + "a" * 32 + ":short"}),
            (c.validate_get, {"snapshot_id": "A" * 32}),
            (c.validate_get, {"snapshot_id": "a" * 32, "cursor": ["x"]}),
            (c.validate_watch, {"action": ["add"]}),
            (c.validate_watch, {"action": "remove", "automation_id": "a1", "mode": "executed"}),
            (c.validate_watch, {"action": "add", "automation_id": "a1", "duration_hours": 24.0}),
        ]
        for validate, arguments in refused:
            with self.subTest(arguments=arguments):
                with self.assertRaises(c.TraceSnapshotError):
                    validate(arguments)
        c.validate_watch({"action": "list"})
        c.validate_list({})

    def test_registration_policy_and_core_routes(self):
        tools = tool_module.registered_tools()
        self.assertEqual(set(tools), tool_module._VALIDATORS.keys())
        for name, tool in tools.items():
            with self.subTest(name=name):
                self.assertFalse(tool.parameters["additionalProperties"])
                self.assertEqual(TOOL_CAPABILITY_POLICY[name], ProviderCapability.TRACE_SNAPSHOTS)
                self.assertFalse(tool.annotations.destructiveHint)
                self.assertFalse(tool.annotations.openWorldHint)
        self.assertEqual(STATIC_TOOL_CORE_REQUIREMENTS["capture_automation_trace"], c.REQUIREMENTS)
        self.assertEqual({"list_trace_snapshots", "get_trace_snapshot", "set_automation_trace_watch"}
                         - set(CORE_INDEPENDENT_STATIC_TOOLS), set())
        self.assertNotIn("capture_automation_trace", CORE_INDEPENDENT_STATIC_TOOLS)
        self.assertEqual(ANALYTICAL_PROVIDER_POLICIES["capture_automation_trace"]["fallback_policy"], "none")

    async def test_tool_refuses_wrong_types_without_coercion(self):
        tool = tool_module.registered_tools()["list_trace_snapshots"]
        result = json.loads(await tool.run({"limit": "5"}))
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "invalid_request")
        self.assertEqual(result["metadata"]["provider"], "none")

    async def test_unconfigured_tool_fails_closed(self):
        with patch.object(tool_module, "TRACE_SNAPSHOTS", TraceSnapshotRuntime()):
            result = json.loads(await tool_module.list_trace_snapshots())
        self.assertFalse(result["success"])
        self.assertEqual(result["details"]["reason"], "authority_unavailable")

    def test_audit_parameters_keep_only_bounded_valid_values(self):
        note = "private note " + h.needle("note")
        audit = _trace_snapshot_audit_parameters("capture_automation_trace", {
            "automation_id": "../" + h.needle("aid"), "run_id": "r1", "note": note,
            "include_condition_failures": "yes", "smuggled": h.needle("extra")})
        rendered = json.dumps(audit)
        for tag in ("note", "aid", "extra"):
            self.assertNotIn(h.needle(tag), rendered)
        self.assertEqual((audit["automation_id"], audit["run_id"], audit["include_condition_failures"],
                          audit["note_present"], audit["unknown_fields_present"]),
                         (None, "r1", None, True, True))
        self.assertNotIn("smuggled", audit["argument_fields"])
        watch = _trace_snapshot_audit_parameters("set_automation_trace_watch", {
            "action": "add", "automation_id": "a1", "duration_hours": True})
        self.assertEqual((watch["action"], watch["automation_id"], watch["duration_hours"]),
                         ("add", "a1", None))
        listing = _trace_snapshot_audit_parameters("list_trace_snapshots", {"cursor": h.needle("cursor")})
        self.assertNotIn(h.needle("cursor"), json.dumps(listing))
        self.assertTrue(listing["cursor_present"])


class GetAutomationTraceTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_trace_read_is_sanitized_and_marked_partial(self):
        detail = {**header("x1"), "config": {"password": h.needle("compat"),
                                             "cert": h.private_key_pem(h.needle("compat_pem"))},
                  "trace": {"action/0": [{"path": "action/0", "result": {"long": "z" * 3_000}}]}}

        async def ws_command(payload):
            self.assertEqual(payload["domain"], "automation")
            return detail
        with patch.object(compatibility, "ws_command", ws_command):
            raw = await compatibility.get_automation_trace("a1", "x1")
        envelope = json.loads(raw)
        self.assertNotIn(h.needle("compat"), raw)
        self.assertNotIn(h.needle("compat_pem"), raw)
        # Review H1: the PEM is withheld whole by the shared credential screen.
        self.assertIn("credential_text", envelope["metadata"]["sanitization"]["categories"])
        self.assertEqual(envelope["metadata"]["completeness"], "partial")
        self.assertEqual(envelope["metadata"]["sanitization"]["truncated_field_count"], 1)
        self.assertEqual(len(envelope["warnings"]), 1)
        result, data, warnings, metadata = await CanonicalProviderDispatcher()._direct(
            ProviderCapability.AUTOMATION_TRACE, lambda: raw)
        self.assertEqual(result.completeness, ProviderCompleteness.PARTIAL)
        self.assertEqual(warnings, envelope["warnings"])

    async def test_clean_trace_read_is_complete(self):
        async def ws_command(payload):
            return {**header("x2"), "trace": {}}
        with patch.object(compatibility, "ws_command", ws_command):
            raw = await compatibility.get_automation_trace("a1", "x2")
        envelope = json.loads(raw)
        self.assertEqual((envelope["metadata"]["completeness"], envelope["warnings"]), ("complete", []))
        result, *_ = await CanonicalProviderDispatcher()._direct(ProviderCapability.AUTOMATION_TRACE, lambda: raw)
        self.assertEqual(result.completeness, ProviderCompleteness.COMPLETE)


HA_TOKEN = "synthetic-ha-" + "credential-225"
ACCESS_SECRET = "synthetic-access-" + "secret-225"


class RefusingCore:
    """A gateway Core runtime that grants no lease and records every request."""

    def __init__(self):
        self.acquired = []

    def acquire(self, *args, **kwargs):
        self.acquired.append((args, kwargs))
        return None


class GatewayStackTests(unittest.TestCase):
    """The production tools behind the real FastMCP server, gateway and audit logger."""

    def call(self, name, arguments):
        from ha_mcp_engineering.audit import AuditLogger
        from ha_mcp_engineering.configuration import Settings
        from ha_mcp_engineering.mcp_server import create_mcp_server
        from ha_mcp_engineering.routing import AuthenticatedMcpGateway
        from tests.same_thread_asgi_client import SameThreadAsgiTestClient

        configured = Settings(
            ha_url="http://synthetic-ha.invalid", ha_token=HA_TOKEN, access_secret=ACCESS_SECRET,
            port=8100, audit_path=str(self.root / "audit.jsonl"),
            governance_path=str(self.root / "governance"),
            rate_limit_per_minute=1000, rate_limit_burst=100, destructive_services=frozenset(),
        )
        from ha_mcp_engineering.mcp_sdk_compatibility import McpSdkToolRegistry
        server = create_mcp_server(configured)
        tools = McpSdkToolRegistry(server)
        tools.replace({**tools.snapshot(), **tool_module.registered_tools()})
        inner = server.streamable_http_app()
        gateway = AuthenticatedMcpGateway(inner, configured, AuditLogger(configured.audit_path, ACCESS_SECRET),
                                          core_runtime=self.gateway_core)
        client = SameThreadAsgiTestClient(gateway, lifespan_app=inner, base_url="http://127.0.0.1:8100")
        with patch.object(tool_module, "TRACE_SNAPSHOTS", self.runtime), \
                patch.object(tool_module, "SETTINGS", configured), client:
            response = client.post("/" + ACCESS_SECRET + "/mcp",
                                   headers={"accept": "application/json, text/event-stream"},
                                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                         "params": {"name": name, "arguments": arguments}})
        self.assertEqual(response.status_code, 200)
        body = json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: ")))
        rendered = json.loads(body["result"]["content"][0]["text"])
        records = [json.loads(line) for line in Path(configured.audit_path).read_text().splitlines()
                   if line.strip()]
        tool_records = [record for record in records if record.get("event") == "tool_call"]
        self.calls += 1
        self.assertEqual(len(tool_records), self.calls, records)
        return rendered, tool_records[-1], response.text

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.core = FakeCore()
        self.core.add("r1")
        self.audit = FakeAudit()
        self.gateway_core = RefusingCore()
        self.calls = 0
        self.runtime = TraceSnapshotRuntime()
        self.runtime.service = TraceSnapshotService(self.root / "store", self.core.command, FakeRuntime(),
                                                    self.audit, known_secrets=(HA_TOKEN, ACCESS_SECRET))

    def test_invalid_arguments_are_refused_at_the_transport_with_a_bounded_audit(self):
        rendered, record, text = self.call("capture_automation_trace", {
            "automation_id": "a1", "include_condition_failures": "yes", "note": h.needle("note")})
        self.assertFalse(rendered["success"])
        self.assertEqual(rendered["error"], "InvalidTraceSnapshotRequest")
        self.assertNotIn(h.needle("note"), text)
        self.assertNotIn(h.needle("note"), json.dumps(record))
        self.assertEqual(self.core.sent, [])
        self.assertEqual(self.gateway_core.acquired, [])

    def test_refused_core_lease_captures_nothing(self):
        rendered, record, _ = self.call("capture_automation_trace", {"automation_id": "a1"})
        self.assertFalse(rendered["success"])
        self.assertEqual(self.core.sent, [])
        self.assertFalse((self.root / "store" / c.MODEL / "index.json").exists())

    def test_store_reads_and_the_watch_list_need_no_core(self):
        rendered, record, _ = self.call("set_automation_trace_watch", {"action": "add", "automation_id": "a1"})
        self.assertTrue(rendered["success"], rendered)
        self.assertTrue(c.CALLER.fullmatch(rendered["data"]["watch"]["created_by"]))
        self.assertEqual([row["event"] for row in self.audit.rows], ["trace_watch_change"])
        self.assertEqual((record["access"], record["parameters"]["action"]), ("write", "add"))
        rendered, record, _ = self.call("list_trace_snapshots", {})
        self.assertTrue(rendered["success"], rendered)
        self.assertEqual(rendered["data"]["snapshots"], [])
        self.assertEqual((self.core.sent, self.gateway_core.acquired), ([], []))


class SourceShapeTests(unittest.TestCase):
    def test_trace_snapshot_package_sends_only_automation_trace_commands(self):
        source = "\n".join(path.read_text(encoding="utf-8")
                           for path in (PACKAGE / "trace_snapshots").glob("*.py"))
        self.assertNotIn('"script"', source)
        self.assertIn('"domain": "automation"', source)
        self.assertEqual(sorted(set(__import__("re").findall(r'"type": "([a-z_/]+)"', source))),
                         ["trace/get"])

    def test_store_validation_rejects_unsafe_identity_fields(self):
        base = {"watch_id": "a" * 32, "automation_id": "a1", "mode": "failures", "created_at_ms": 1,
                "expires_at_ms": 2, "caller_id": "caller", "request_id": "req", "seen_run_ids": [],
                "baseline_done": False, "skipped_polls": 0, "uncaptured_runs": 0, "captured_count": 0,
                "evicted_count": 0, "last_poll_at_ms": None}
        empty = store_module.empty_index()["quarantine"]
        store_module.validate_index({"model": c.MODEL, "snapshots": [], "watches": [base],
                                     "quarantine": empty})
        for key, value in (("caller_id", "caller " + h.needle("x")), ("automation_id", "../a"),
                           ("seen_run_ids", ["bad/run"]), ("skipped_polls", True)):
            with self.subTest(key=key):
                with self.assertRaises(store_module.StoreError):
                    store_module.validate_index({"model": c.MODEL, "snapshots": [],
                                                 "watches": [{**base, key: value}],
                                                 "quarantine": empty})


if __name__ == "__main__":
    unittest.main()
