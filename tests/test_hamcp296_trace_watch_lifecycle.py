"""HAMCP-296: trace watch expiry warnings, renewal, lapsed records and slot pressure.

Reuses the HAMCP-225 harness (fake Core that answers only ``trace/list`` and
``trace/get`` for the automation domain, fake audit, fake clock). Covers the
design note's success paths and its refusal, failure-preservation, rollback
and negative-reachability requirements.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))

import tests.test_hamcp225_trace_snapshots as base  # noqa: E402
from tests.test_hamcp225_trace_snapshots import (  # noqa: E402
    KNOWN, NOW, VERSION, FakeAudit, FakeCore, FakeRuntime, ServiceCase,
)
from ha_mcp_engineering.tools import compatibility  # noqa: E402
from ha_mcp_engineering.tools import trace_snapshots as tool_module  # noqa: E402
from ha_mcp_engineering.trace_snapshots import contracts as c  # noqa: E402
from ha_mcp_engineering.trace_snapshots import runtime as runtime_module  # noqa: E402
from ha_mcp_engineering.trace_snapshots import store as store_module  # noqa: E402
from ha_mcp_engineering.trace_snapshots.runtime import TraceSnapshotRuntime  # noqa: E402
from ha_mcp_engineering.trace_snapshots.service import TraceSnapshotService  # noqa: E402


FIXTURES = Path(__file__).resolve().parent / "fixtures"
BETA6_STORE = FIXTURES / "hamcp296_beta6_v1_trace_index"
BETA6_PROVENANCE = FIXTURES / "hamcp296_beta6_v1_trace_index_provenance.json"
HOUR = 3600
AUTHORITY = (7, VERSION)
FULL = c.SNAPSHOT_BYTES
ENDED_WATCH = "b" * 32


class LifecycleCase(ServiceCase):
    async def add(self, automation_id, **kwargs):
        value, _ = await self.service.watch(action="add", automation_id=automation_id, **kwargs)
        return value

    async def renew(self, automation_id="", **kwargs):
        value, _ = await self.service.watch(action="renew", automation_id=automation_id, **kwargs)
        return value

    async def listed(self):
        value, _ = await self.service.watch(action="list")
        return value

    def sidecar(self):
        path = self.root / c.MODEL / "watch-lifecycle.json"
        return json.loads(path.read_text()) if path.exists() else None

    def store_files(self):
        found = {}
        for path in sorted((self.root / c.MODEL).rglob("*")):
            if path.is_file() and path.name != ".transaction.lock":
                found[str(path.relative_to(self.root))] = path.read_bytes()
        return found

    async def fill(self, count=c.MAX_WATCHES, prefix="w", **kwargs):
        for i in range(count):
            self.clock.now += 1
            await self.add(f"{prefix}{i}", **kwargs)

    def overflow(self):
        path = self.root / c.MODEL / "snapshot-overflow.json"
        return json.loads(path.read_text()) if path.exists() else None

    def seed(self, count, *, origin="trace_watch", size=1, start=0, age_ms=0, sizes=None):
        """Store ``count`` synthetic entries (ended watch or manual) with record files.

        Only ``write_index``'s placement and the commit bounds look at them;
        their records are never read.
        """

        store = self.service.store
        now_ms = int(self.clock.now * 1000)
        with store.transaction():
            index = store.load_index()
            for i in range(start, start + count):
                snapshot_id = f"{i + 1:032x}"
                store.write_record(snapshot_id, b"{}")
                index["snapshots"].append({
                    "snapshot_id": snapshot_id, "automation_id": f"seeded_{origin}",
                    "run_id": f"seed-{i}", "origin": origin,
                    "watch_id": ENDED_WATCH if origin == "trace_watch" else None,
                    "captured_at_ms": now_ms - age_ms - i, "captured_by": self.caller,
                    "completeness": "complete",
                    "bytes": sizes[i - start] if sizes else size, "sha256": "0" * 64,
                    "state": "stopped", "script_execution": "finished", "started_at": None})
            store.write_index(index)

    def assert_v1_bounds(self):
        """``index.json`` passes the shipped rule; the merged set has no duplicate."""

        raw = self.index()
        store_module.validate_index(raw)
        self.assertLessEqual(len(raw["snapshots"]), c.V1_INDEX_MAX_SNAPSHOTS)
        self.assertLessEqual(sum(e["bytes"] for e in raw["snapshots"]), c.V1_INDEX_MAX_BYTES)
        self.assertLessEqual(len(raw["watches"]), c.V1_INDEX_MAX_WATCHES)
        merged = self.service.store.load_index()["snapshots"]
        ids = [e["snapshot_id"] for e in merged]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLessEqual(len(merged), c.MAX_SNAPSHOTS)
        return raw, merged

    def watch_origin(self):
        return [e for e in self.service.store.load_index()["snapshots"]
                if e["origin"] == "trace_watch"]

    async def poll_rounds(self, rounds, prefix="run"):
        for r in range(rounds):
            for i in range(c.MAX_CAPTURES):
                self.core.add(f"{prefix}{r}_{i}", minute=(r * c.MAX_CAPTURES + i) % 60)
            await self.service.poll_once(authority=AUTHORITY)


class BoundsTests(unittest.TestCase):
    def test_the_stated_bound_and_snapshot_budget(self):
        # Josh 2026-11-07 Q1 (b): the cap is raised to a stated bound of 20.
        self.assertEqual((c.MAX_WATCHES, c.V1_INDEX_MAX_WATCHES), (20, 10))
        # Decision E2 (2026-10-26), kept by Josh on 2026-11-07: 20 per watch.
        self.assertEqual(c.WATCH_QUOTA, 20)
        # Josh 2026-11-07: the watch-origin reservation is 20 x 20.
        self.assertEqual(c.WATCH_STORE_SNAPSHOTS, c.MAX_WATCHES * c.WATCH_QUOTA)
        self.assertEqual((c.WATCH_STORE_SNAPSHOTS, c.WATCH_STORE_BYTES), (400, 100 * 1024 * 1024))
        self.assertEqual(c.WATCH_STORE_BYTES, c.WATCH_STORE_SNAPSHOTS * c.SNAPSHOT_BYTES)
        # Orchestrator default (pending Codex/Josh): 500 snapshots, 128 MiB.
        self.assertEqual((c.MAX_SNAPSHOTS, c.STORE_BYTES), (500, 128 * 1024 * 1024))
        # Room that full watches can never take from manual captures.
        self.assertGreaterEqual(c.MAX_SNAPSHOTS - c.WATCH_STORE_SNAPSHOTS, 100)
        self.assertGreaterEqual(c.STORE_BYTES - c.WATCH_STORE_BYTES, 28 * 1024 * 1024)
        # The count binds first: 500 full-size snapshots are 125 MiB.
        self.assertLessEqual(c.MAX_SNAPSHOTS * c.SNAPSHOT_BYTES, c.STORE_BYTES)
        # index.json keeps the shipped v1 bounds (fixture provenance).
        provenance = json.loads(BETA6_PROVENANCE.read_text())["verdicts"]
        self.assertEqual((c.V1_INDEX_MAX_SNAPSHOTS, c.V1_INDEX_MAX_BYTES, c.INDEX_BYTES),
                         (provenance["shipped_max_snapshots"], provenance["shipped_store_bytes"],
                          provenance["shipped_index_bytes"]))
        # Polling load: one trace/list per watch per tick inside the read share.
        ticks_per_minute = 60 // c.TICK_SECONDS
        self.assertLessEqual(c.MAX_WATCHES * ticks_per_minute, 60)
        self.assertGreaterEqual(c.TICK_SECONDS * c.READ_SHARE / c.MAX_WATCHES, 0.8)

    def test_capabilities_state_the_bound(self):
        from ha_mcp_engineering.capabilities import capability_for_tool  # noqa: PLC0415
        poller = capability_for_tool("set_automation_trace_watch")["bounded_poller"]
        self.assertEqual((poller["max_watches"], poller["snapshot_quota"],
                          poller["watch_snapshot_reservation"], poller["lapsed_records"]),
                         (c.MAX_WATCHES, c.WATCH_QUOTA, c.WATCH_STORE_SNAPSHOTS, c.LAPSED_MAX))


class WarningTests(LifecycleCase):
    async def test_expiring_soon_uses_the_smaller_of_24h_and_half_the_term(self):
        await self.add("long", duration_hours=72)
        await self.add("short", duration_hours=4)
        value = await self.listed()
        rows = {w["automation_id"]: w for w in value["watches"]}
        self.assertEqual((rows["long"]["expiring_soon"], rows["short"]["expiring_soon"]),
                         (False, False))
        self.clock.now += 2 * HOUR  # short: 2h left of 4h, half the term
        value = await self.listed()
        rows = {w["automation_id"]: w for w in value["watches"]}
        self.assertTrue(rows["short"]["expiring_soon"])
        self.assertFalse(rows["long"]["expiring_soon"])
        self.assertEqual(rows["short"]["expires_in_seconds"], 2 * HOUR)
        self.clock.now += 46 * HOUR  # long: 24h left
        value = await self.listed()
        self.assertEqual(value["lifecycle"]["expiring_soon_count"], 1)
        self.assertEqual(value["lifecycle"]["warning_window_hours"], c.EXPIRY_WARNING_HOURS)
        self.assertTrue(value["watches"][0]["expiring_soon"])

    async def test_refusals_carry_the_lifecycle_block(self):
        error = await self.refused(self.service.watch(action="remove", automation_id="none"))
        self.assertEqual(error.reason, "watch_not_found")
        self.assertEqual(set(error.details["lifecycle"]) >= {"expiring_soon_count", "lapsed",
                                                             "lapsed_count", "next_expiry_at"}, True)


class RenewTests(LifecycleCase):
    async def test_named_renew_extends_never_shortens_and_is_capped(self):
        await self.add("a1", duration_hours=48)
        self.clock.now += HOUR
        value = await self.renew("a1", duration_hours=24)
        self.assertEqual(value["renewed"][0]["outcome"], "unchanged")
        self.assertFalse(value["changed"])
        value = await self.renew("a1", duration_hours=c.WATCH_MAX_HOURS)
        item = value["renewed"][0]
        self.assertEqual(item["outcome"], "renewed")
        self.assertEqual(item["expires_at"], c.utc_text(self.clock.now + c.WATCH_MAX_HOURS * HOUR))
        self.assertIsNotNone(value["watches"][0]["renewed_at"])
        # Idempotent at the cap.
        again = await self.renew("a1", duration_hours=c.WATCH_MAX_HOURS)
        self.assertEqual(again["renewed"][0]["outcome"], "unchanged")
        self.assertEqual([r["result_status"] for r in self.audit.rows], ["add", "renew"])
        self.assertEqual(self.core.sent, [])

    async def test_bulk_renew_extends_every_active_watch(self):
        await self.fill(3, duration_hours=2)
        value = await self.renew("", duration_hours=24)
        self.assertEqual([i["outcome"] for i in value["renewed"]], ["renewed"] * 3)
        self.assertEqual(sum(r["result_status"] == "renew" for r in self.audit.rows), 3)

    async def test_renew_unknown_is_not_found_and_changes_nothing(self):
        before = self.store_files()
        error = await self.refused(self.service.watch(action="renew", automation_id="none"))
        self.assertEqual(error.reason, "watch_not_found")
        self.assertEqual(self.store_files(), before)

    async def test_audit_failure_renews_nothing(self):
        await self.add("a1", duration_hours=2)
        index = self.index()
        self.audit.fail = True
        error = await self.refused(self.service.watch(action="renew", automation_id="a1",
                                                      duration_hours=48))
        self.audit.fail = False
        self.assertEqual(error.reason, "audit_unavailable")
        self.assertEqual(self.index(), index)
        self.assertIsNone(self.sidecar())


class LapsedTests(LifecycleCase):
    async def test_expiry_records_a_lapse_that_renew_reinstates(self):
        added = await self.add("a1", duration_hours=1, priority="high", mode="executed")
        watch_id = added["watch"]["watch_id"]
        self.clock.now += 3 * HOUR
        value = await self.listed()
        self.assertEqual(value["watches"], [])
        self.assertEqual([(r["automation_id"], r["reason"], r["priority"]) for r in value["lapsed"]],
                         [("a1", "expired", "high")])
        self.assertEqual(value["lapsed"][0]["expired_at"], c.utc_text(NOW + HOUR))
        value = await self.renew("a1", duration_hours=24)
        item = value["renewed"][0]
        self.assertEqual((item["outcome"], item["watch_id"], item["lapse_gap_seconds"]),
                         ("reinstated", watch_id, 2 * HOUR))
        watch = value["watches"][0]
        self.assertEqual((watch["mode"], watch["priority"], watch["status"]),
                         ("executed", "high", "awaiting_first_poll"))
        self.assertEqual(value["lapsed"], [])
        self.assertIn("reinstate", [r["result_status"] for r in self.audit.rows])
        # A fresh baseline: no backfill of runs during the gap.
        self.core.add("gap_run")
        await self.service.poll_once(authority=AUTHORITY)
        self.assertEqual(self.index()["snapshots"], [])

    async def test_reinstate_refused_when_full_and_lapse_kept(self):
        await self.add("a1", duration_hours=1)
        self.clock.now += 2 * HOUR
        await self.fill()
        error = await self.refused(self.service.watch(action="renew", automation_id="a1"))
        self.assertEqual(error.reason, "watch_limit")
        self.assertEqual([r["automation_id"] for r in error.details["lifecycle"]["lapsed"]], ["a1"])

    async def test_bulk_include_lapsed_reports_per_item(self):
        await self.add("a1", duration_hours=1)
        await self.add(KNOWN, duration_hours=1)
        self.clock.now += 2 * HOUR
        restarted = TraceSnapshotService(self.root, self.core.command, self.runtime, self.audit,
                                         known_secrets=(KNOWN,), clock=self.clock)
        value, _ = await restarted.watch(action="renew", automation_id="", include_lapsed=True)
        outcomes = {i["automation_id"] == "a1": i["outcome"] for i in value["renewed"]}
        # The withheld ID's per-process key is gone after a restart.
        self.assertEqual(outcomes, {True: "reinstated", False: "identifier_unavailable"})

    async def test_lapsed_records_age_out_after_seven_days(self):
        await self.add("a1", duration_hours=1)
        self.clock.now += HOUR + c.LAPSED_RETENTION_SECONDS + 1
        value = await self.listed()
        self.assertEqual(value["lapsed"], [])
        error = await self.refused(self.service.watch(action="renew", automation_id="a1"))
        self.assertEqual(error.reason, "watch_not_found")

    async def test_lapsed_records_are_bounded_and_drops_counted(self):
        for i in range(c.LAPSED_MAX + 3):
            await self.add(f"l{i}", duration_hours=1)
            await self.service.watch(action="remove", automation_id=f"l{i}")
        # Removal is not a lapse; expiries are.
        self.assertEqual((await self.listed())["lapsed"], [])
        for i in range(c.LAPSED_MAX + 3):
            self.clock.now += 1
            await self.add(f"x{i}", duration_hours=1)
            if (i + 1) % c.MAX_WATCHES == 0:
                self.clock.now += 2 * HOUR
                await self.listed()
        self.clock.now += 2 * HOUR
        value = await self.listed()
        self.assertEqual(len(value["lapsed"]), c.LAPSED_MAX)
        self.assertEqual(value["lifecycle"]["lapsed_dropped"], 3)
        health = self.service.watch_health()
        self.assertEqual(len(health["lapsed"]), c.HEALTH_LIST)
        self.assertEqual(health["lapsed_count"], c.LAPSED_MAX)

    async def test_a_new_add_clears_the_lapse(self):
        await self.add("a1", duration_hours=1)
        self.clock.now += 2 * HOUR
        value = await self.add("a1")
        self.assertEqual(value["lapsed"], [])


class PriorityTests(LifecycleCase):
    async def test_full_slots_refuse_without_displace(self):
        await self.fill()
        before = self.store_files()
        error = await self.refused(self.service.watch(action="add", automation_id="new"))
        self.assertEqual(error.reason, "watch_limit")
        self.assertEqual(len(error.details["watches"]), c.MAX_WATCHES)
        self.assertEqual(self.store_files(), before)

    async def test_displace_removes_only_the_soonest_expiring_normal_watch(self):
        await self.fill(c.MAX_WATCHES - 2, duration_hours=48)
        await self.add("high_soon", duration_hours=1, priority="high")
        await self.add("normal_soon", duration_hours=2)
        value = await self.add("new", displace=True, priority="high")
        self.assertEqual(value["displaced"]["automation_id"], "normal_soon")
        held = {w["automation_id"] for w in value["watches"]}
        self.assertIn("high_soon", held)
        self.assertNotIn("normal_soon", held)
        self.assertEqual([(r["automation_id"], r["reason"]) for r in value["lapsed"]],
                         [("normal_soon", "displaced")])
        statuses = [(r["result_status"], r["automation_id"]) for r in self.audit.rows[-2:]]
        self.assertEqual(statuses, [("displace", "normal_soon"), ("add", "new")])
        self.assertEqual(self.audit.rows[-2]["displaced_by_watch_id"], value["watch"]["watch_id"])

    async def test_all_high_is_refused_even_with_displace(self):
        await self.fill(priority="high")
        error = await self.refused(self.service.watch(action="add", automation_id="new",
                                                      displace=True))
        self.assertEqual(error.reason, "watch_limit")
        self.assertEqual(sum(r["result_status"] == "displace" for r in self.audit.rows), 0)

    async def test_displace_audit_failure_displaces_nothing(self):
        await self.fill()
        index = self.service.store.load_index()["watches"]
        self.audit.fail = True
        error = await self.refused(self.service.watch(action="add", automation_id="new",
                                                      displace=True))
        self.audit.fail = False
        self.assertEqual(error.reason, "audit_unavailable")
        self.assertEqual(self.service.store.load_index()["watches"], index)

    async def test_high_watches_are_polled_first(self):
        await self.add("n1", mode="executed")
        await self.add("h1", mode="executed", priority="high")
        await self.service.poll_once(authority=AUTHORITY)
        lists = [p["item_id"] for p in self.core.sent if p["type"] == "trace/list"]
        self.assertEqual(lists[:2], ["h1", "n1"])

    async def test_high_watches_stay_in_the_v1_index(self):
        await self.fill(c.MAX_WATCHES - 1)
        await self.add("vip", priority="high")
        self.assertIn("vip", [w["automation_id"] for w in self.index()["watches"]])


class WorstCaseSizeTests(unittest.TestCase):
    """INDEX_BYTES (unchanged), OVERFLOW_BYTES and LIFECYCLE_BYTES hold at the worst case."""

    WIDE = "\U0001F600"  # 12 canonical bytes when escaped

    def snapshot(self, i, size=1):
        return {"snapshot_id": f"{i + 1:032x}", "automation_id": "a" * 250 + f"{i:06d}",
                "run_id": "r" * 122 + f"{i:06d}", "origin": "trace_watch", "watch_id": "b" * 32,
                "captured_at_ms": 10**15, "captured_by": "c" * 64, "completeness": "complete",
                "bytes": size, "sha256": "f" * 64, "state": self.WIDE * 160,
                "script_execution": self.WIDE * 160, "started_at": self.WIDE * 160}

    def watch(self, i):
        return {"watch_id": f"{i + 1:032x}", "automation_id": "w" * 250 + f"{i:06d}",
                "mode": "failures", "created_at_ms": 10**15, "expires_at_ms": 10**15,
                "caller_id": "c" * 64, "request_id": self.WIDE * 128,
                "seen_run_ids": ["r" * 125 + f"{j:03d}" for j in range(c.MAX_SEEN_RUNS)],
                "baseline_done": False, "skipped_polls": 10**15, "uncaptured_runs": 10**15,
                "captured_count": 10**15, "evicted_count": 10**15, "last_poll_at_ms": 10**15}

    def stub(self, i):
        return {"snapshot_id": f"{i + 9000:032x}", "automation_id": "q" * 250 + f"{i:06d}",
                "run_id": "r" * 122 + f"{i:06d}", "quarantined_at_ms": 10**15,
                "reason": "digest_mismatch", "observed_bytes": 10**15, "observed_sha256": "e" * 64}

    def test_a_full_v1_index_fits_index_bytes(self):
        index = {"model": c.MODEL,
                 # Six-digit byte counts inside the 32 MiB v1 bound.
                 "snapshots": [self.snapshot(i, 100_000) for i in range(c.V1_INDEX_MAX_SNAPSHOTS)],
                 "watches": [self.watch(i) for i in range(c.V1_INDEX_MAX_WATCHES)],
                 "quarantine": {"entries": [self.stub(i) for i in range(c.QUARANTINE_MAX)],
                                "dropped": 10**15}}
        raw = c.canonical(store_module.validate_index(index))
        self.assertEqual(len(c.canonical(self.snapshot(0, FULL))), 6_575)
        self.assertLess(len(raw), 1_500_000)
        self.assertLessEqual(len(raw), c.INDEX_BYTES)

    def test_a_full_overflow_sidecar_fits_overflow_bytes(self):
        spill = {"model": c.OVERFLOW_MODEL,
                 "snapshots": [self.snapshot(i, FULL) for i in range(c.MAX_SNAPSHOTS)]}
        raw = c.canonical(store_module.validate_overflow(spill))
        self.assertLessEqual(len(raw), c.OVERFLOW_BYTES)
        # The same 500 entries in one index would not fit INDEX_BYTES.
        self.assertGreater(len(raw), c.INDEX_BYTES)

    def test_a_full_lifecycle_sidecar_fits_lifecycle_bytes(self):
        sidecar = {"model": c.LIFECYCLE_MODEL,
                   "overflow": [self.watch(i) for i in range(c.MAX_WATCHES)],
                   "watch_meta": [{"watch_id": f"{i + 1:032x}", "priority": "normal",
                                   "renewed_at_ms": 10**15} for i in range(c.MAX_WATCHES)],
                   "lapsed": [{"watch_id": f"{i + 100:032x}", "automation_id": "l" * 250 + f"{i:06d}",
                               "mode": "failures", "priority": "normal", "created_at_ms": 10**15,
                               "expired_at_ms": 10**15, "reason": "displaced",
                               "captured_count": 10**15} for i in range(c.LAPSED_MAX)],
                   "lapsed_dropped": 10**15}
        raw = c.canonical(store_module.validate_lifecycle(sidecar))
        self.assertLessEqual(len(raw), c.LIFECYCLE_BYTES)


class BudgetTests(LifecycleCase):
    async def test_a_watch_keeps_twenty_and_evicts_only_its_own_oldest(self):
        self.core.add("m1")
        await self.service.capture(automation_id="a1", run_id="m1")
        await self.add("a1", mode="executed")
        await self.add("a2", mode="executed")
        await self.service.poll_once(authority=AUTHORITY)  # baselines
        await self.poll_rounds(c.WATCH_QUOTA // c.MAX_CAPTURES)
        rows = {w["automation_id"]: w for w in self.service.store.load_index()["watches"]}
        own = [e for e in self.watch_origin() if e["watch_id"] == rows["a1"]["watch_id"]]
        self.assertEqual(len(own), c.WATCH_QUOTA)
        oldest = min(own, key=lambda e: (e["captured_at_ms"], e["snapshot_id"]))
        self.clock.now += 1
        self.core.add("late", minute=59)
        await self.service.poll_once(authority=AUTHORITY)
        held = self.service.store.load_index()["snapshots"]
        own = [e for e in held if e["watch_id"] == rows["a1"]["watch_id"]]
        self.assertEqual(len(own), c.WATCH_QUOTA)
        self.assertNotIn(oldest["snapshot_id"], {e["snapshot_id"] for e in own})
        self.assertIn("late", {e["run_id"] for e in own})
        # The other watch's and the manual snapshots are untouched.
        self.assertEqual(sum(e["watch_id"] == rows["a2"]["watch_id"] for e in held), c.WATCH_QUOTA)
        self.assertEqual([e["run_id"] for e in held if e["origin"] == "manual"], ["m1"])
        evicted = [i for r in self.audit.rows if r["event"] == "trace_watch_capture"
                   and r["watch_id"] == rows["a1"]["watch_id"] for i in r["evicted_snapshot_ids"]]
        self.assertEqual(evicted, [oldest["snapshot_id"]])
        self.assertFalse((self.root / c.MODEL / "records" / (oldest["snapshot_id"] + ".json")).exists())

    async def test_twenty_full_watches_fill_the_reservation_and_leave_manual_room(self):
        await self.fill(mode="executed")
        await self.service.poll_once(authority=AUTHORITY)  # baselines
        for r in range(c.WATCH_QUOTA // c.MAX_CAPTURES):
            await self.poll_rounds(1, prefix=f"full{r}_")
            self.assert_v1_bounds()
        self.assertEqual(len(self.watch_origin()), c.WATCH_STORE_SNAPSHOTS)
        raw, merged = self.assert_v1_bounds()
        self.assertEqual(len(raw["snapshots"]), c.V1_INDEX_MAX_SNAPSHOTS)
        self.assertEqual(len(self.overflow()["snapshots"]),
                         c.WATCH_STORE_SNAPSHOTS - c.V1_INDEX_MAX_SNAPSHOTS)
        # An ended watch's snapshots still count: a new watch's capture past
        # the reservation is store_full, and nothing is evicted for it.
        await self.service.watch(action="remove", automation_id="w0")
        late = (await self.add("late", mode="executed"))["watches"]
        late_id = next(w["watch_id"] for w in late if w["automation_id"] == "late")
        await self.service.poll_once(authority=AUTHORITY)  # its baseline
        before = {e["snapshot_id"] for e in self.watch_origin()}
        self.clock.now += 1
        self.core.add("past", minute=59)
        await self.service.poll_once(authority=AUTHORITY)
        origin = self.watch_origin()
        self.assertEqual(len(origin), c.WATCH_STORE_SNAPSHOTS)
        self.assertFalse(any(e["watch_id"] == late_id for e in origin))
        row = next(w for w in (await self.listed())["watches"] if w["automation_id"] == "late")
        # Every run it tried (from its baseline poll on) was refused and counted.
        self.assertGreaterEqual(row["uncaptured_runs"], 1)
        self.assertEqual(row["snapshots_held"], 0)
        # Watches at their quota still rotate their own oldest.
        self.assertEqual(len(before - {e["snapshot_id"] for e in origin}), c.MAX_WATCHES - 1)
        self.core.add("manual", minute=58)
        value, _ = await self.service.capture(automation_id="a1", run_id="manual")
        self.assertEqual(value["runs"][0]["outcome"], "captured")
        self.assertEqual(value["store"]["limits"]["snapshots"], c.MAX_SNAPSHOTS)
        raw, merged = self.assert_v1_bounds()
        self.assertIn("manual", {e["run_id"] for e in raw["snapshots"]})

    async def test_a_watch_capture_past_the_reservation_bytes_is_store_full(self):
        # WATCH_STORE_BYTES is exactly WATCH_STORE_SNAPSHOTS full-size
        # snapshots, so the count binds first at the shipped values; the byte
        # check is defence in depth and is proven with a smaller bound.
        self.assertEqual(c.WATCH_STORE_BYTES, c.WATCH_STORE_SNAPSHOTS * c.SNAPSHOT_BYTES)
        self.seed(10, size=FULL)
        await self.add("a1", mode="executed")
        await self.service.poll_once(authority=AUTHORITY)
        self.core.add("r1", minute=59)
        before = self.store_files()
        with patch.object(c, "WATCH_STORE_BYTES", 10 * FULL + 10):
            await self.service.poll_once(authority=AUTHORITY)
            self.assertEqual(len(self.watch_origin()), 10)
            row = (await self.listed())["watches"][0]
            self.assertGreaterEqual(row["uncaptured_runs"], 1)
            self.assertEqual(row["snapshots_held"], 0)
            # Refused, not evicted: every seeded record file is still there.
            self.assertTrue(all(name in self.store_files() for name in before
                                if "/records/" in name))
            value, _ = await self.service.capture(automation_id="a1", run_id="r1")
            self.assertEqual(value["runs"][0]["outcome"], "captured")
            self.assertEqual(value["store"]["limits"]["watch_bytes"], 10 * FULL + 10)
        self.assert_v1_bounds()

    async def test_a_watch_capture_past_the_reservation_count_is_store_full(self):
        self.seed(c.WATCH_STORE_SNAPSHOTS)
        await self.add("a1", mode="executed")
        await self.service.poll_once(authority=AUTHORITY)
        self.core.add("r1", minute=1)
        await self.service.poll_once(authority=AUTHORITY)
        self.assertEqual(len(self.watch_origin()), c.WATCH_STORE_SNAPSHOTS)
        self.assertEqual((await self.listed())["watches"][0]["uncaptured_runs"], 1)
        value, _ = await self.service.capture(automation_id="a1", run_id="r1")
        self.assertEqual(value["runs"][0]["outcome"], "captured")

    async def test_manual_captures_keep_room_up_to_500_snapshots(self):
        self.seed(c.WATCH_STORE_SNAPSHOTS, size=FULL)
        self.seed(c.MAX_SNAPSHOTS - c.WATCH_STORE_SNAPSHOTS - 1, origin="manual", size=FULL,
                  start=c.WATCH_STORE_SNAPSHOTS)
        self.core.add("m1", minute=1)
        self.core.add("m2", minute=2)
        value, _ = await self.service.capture(automation_id="a1", run_id="m1")
        self.assertEqual(value["runs"][0]["outcome"], "captured")
        self.assertEqual(value["store"]["snapshots"], c.MAX_SNAPSHOTS)
        before = self.store_files()
        value, _ = await self.service.capture(automation_id="a1", run_id="m2")
        self.assertEqual(value["runs"][0]["outcome"], "store_full")
        self.assertEqual(self.store_files(), before)
        raw, merged = self.assert_v1_bounds()
        self.assertEqual(len(merged), c.MAX_SNAPSHOTS)
        # Manual captures first: all 100 are in index.json.
        self.assertEqual(sum(e["origin"] == "manual" for e in raw["snapshots"]),
                         c.MAX_SNAPSHOTS - c.WATCH_STORE_SNAPSHOTS)

    async def test_the_store_byte_bound_still_refuses_a_manual_capture(self):
        self.seed(10, size=FULL)
        self.core.add("m1", minute=1)
        with patch.object(c, "STORE_BYTES", 10 * FULL + 10):
            value, _ = await self.service.capture(automation_id="a1", run_id="m1")
        self.assertEqual(value["runs"][0]["outcome"], "store_full")
        self.assertEqual(value["store"]["limits"]["bytes"], 10 * FULL + 10)


class OverflowStoreTests(LifecycleCase):
    async def test_index_json_keeps_manual_first_then_newest_watch_captures(self):
        self.seed(c.V1_INDEX_MAX_SNAPSHOTS + 10)  # watch captures, newest first by index
        self.seed(20, origin="manual", start=1000, age_ms=10 * HOUR * 1000)
        raw, merged = self.assert_v1_bounds()
        self.assertEqual(len(raw["snapshots"]), c.V1_INDEX_MAX_SNAPSHOTS)
        self.assertEqual(len(merged), c.V1_INDEX_MAX_SNAPSHOTS + 30)
        self.assertEqual(sum(e["origin"] == "manual" for e in raw["snapshots"]), 20)
        spilled = {e["run_id"] for e in self.overflow()["snapshots"]}
        self.assertEqual(spilled, {f"seed-{i}" for i in range(180, 210)})
        listed, _ = await self.service.list(limit=50)
        self.assertEqual(listed["store"]["snapshots"], c.V1_INDEX_MAX_SNAPSHOTS + 30)

    async def test_index_json_keeps_the_v1_byte_bound(self):
        self.seed(150, size=FULL)
        raw, merged = self.assert_v1_bounds()
        self.assertEqual(len(raw["snapshots"]), c.V1_INDEX_MAX_BYTES // FULL)
        self.assertEqual(sum(e["bytes"] for e in raw["snapshots"]), c.V1_INDEX_MAX_BYTES)
        self.assertEqual(len(merged), 150)

    async def test_eviction_moves_entries_between_the_index_and_the_sidecar(self):
        await self.add("a1", mode="executed")
        await self.add("a2", mode="executed")
        await self.service.poll_once(authority=AUTHORITY)
        with patch.object(c, "V1_INDEX_MAX_SNAPSHOTS", 7):
            for r in range(c.WATCH_QUOTA // c.MAX_CAPTURES + 2):
                await self.poll_rounds(1, prefix=f"e{r}_")
                raw, merged = self.assert_v1_bounds()
                self.assertEqual(len(raw["snapshots"]), 7)
                self.assertEqual(len(merged), min(2 * c.WATCH_QUOTA, 2 * c.MAX_CAPTURES * (r + 1)))
                # The index holds the newest; spilled entries are all older.
                newest = min(e["captured_at_ms"] for e in raw["snapshots"])
                spilled = [e for e in merged if e["snapshot_id"] not in
                           {x["snapshot_id"] for x in raw["snapshots"]}]
                self.assertTrue(all(e["captured_at_ms"] <= newest for e in spilled))
            files = {p.name[:-5] for p in (self.root / c.MODEL / "records").iterdir()}
            self.assertEqual(files, {e["snapshot_id"] for e in merged})

    async def test_the_retention_sweep_promotes_overflow_entries(self):
        self.seed(30, origin="manual", age_ms=91 * 24 * HOUR * 1000, start=1000)
        self.seed(c.V1_INDEX_MAX_SNAPSHOTS)
        self.assertEqual(len(self.overflow()["snapshots"]), 30)
        listed, _ = await self.service.list()
        self.assertEqual(listed["store"]["snapshots"], c.V1_INDEX_MAX_SNAPSHOTS)
        raw, merged = self.assert_v1_bounds()
        self.assertEqual(len(raw["snapshots"]), c.V1_INDEX_MAX_SNAPSHOTS)
        self.assertFalse(any(e["origin"] == "manual" for e in merged))
        records = {p.name[:-5] for p in (self.root / c.MODEL / "records").iterdir()}
        self.assertEqual(records, {e["snapshot_id"] for e in merged})

    async def test_a_failed_overflow_write_changes_nothing(self):
        self.seed(c.V1_INDEX_MAX_SNAPSHOTS + 5)
        before = self.store_files()
        self.core.add("m1", minute=1)

        def fail(stage):
            if stage == "before_overflow":
                raise OSError("synthetic")
        self.service.store.fault_hook = fail
        error = await self.refused(self.service.capture(automation_id="a1", run_id="m1"))
        self.service.store.fault_hook = None
        self.assertEqual(error.reason, "store_unavailable")
        self.assertEqual(self.store_files(), before)

    async def test_a_failed_index_write_after_the_sidecar_loses_no_entry(self):
        self.seed(c.V1_INDEX_MAX_SNAPSHOTS + 5)
        index_before = (self.root / c.MODEL / "index.json").read_bytes()
        held = {e["snapshot_id"] for e in self.service.store.load_index()["snapshots"]}
        self.core.add("m1", minute=1)

        def fail(stage):
            if stage == "before_index":
                raise OSError("synthetic")
        self.service.store.fault_hook = fail
        error = await self.refused(self.service.capture(automation_id="a1", run_id="m1"))
        self.service.store.fault_hook = None
        self.assertEqual(error.reason, "store_unavailable")
        self.assertEqual((self.root / c.MODEL / "index.json").read_bytes(), index_before)
        # The sidecar moved ahead (it now also holds the entry the manual
        # capture would have pushed out); the index wins and nothing is lost.
        raw, merged = self.assert_v1_bounds()
        self.assertEqual({e["snapshot_id"] for e in merged}, held)
        # Reload in a new process: the same set, and the next write settles it.
        service = TraceSnapshotService(self.root, self.core.command, self.runtime, self.audit,
                                       clock=self.clock)
        value, _ = await service.capture(automation_id="a1", run_id="m1")
        self.assertEqual(value["runs"][0]["outcome"], "captured")
        raw, merged = self.assert_v1_bounds()
        self.assertEqual(len(merged), len(held) + 1)

    async def test_a_damaged_overflow_sidecar_resets_and_never_blocks(self):
        self.seed(c.V1_INDEX_MAX_SNAPSHOTS + 5)
        path = self.root / c.MODEL / "snapshot-overflow.json"
        good = path.read_bytes()
        index = self.index()
        duplicate = json.dumps({"model": c.OVERFLOW_MODEL,
                                "snapshots": [index["snapshots"][0], index["snapshots"][0]]})
        for damage in ("not json", "symlink", "oversize", "wrong model", "duplicate", "extra key"):
            with self.subTest(damage=damage):
                path.unlink(missing_ok=True)
                if damage == "symlink":
                    target = self.root / "elsewhere.json"
                    target.write_bytes(good)
                    path.symlink_to(target)
                elif damage == "oversize":
                    path.write_text(" " * (c.OVERFLOW_BYTES + 1))
                elif damage == "wrong model":
                    path.write_text(json.dumps({"model": "other", "snapshots": []}))
                elif damage == "duplicate":
                    path.write_text(duplicate)
                elif damage == "extra key":
                    path.write_text(json.dumps({**json.loads(good), "more": 1}))
                else:
                    path.write_text(damage)
                service = TraceSnapshotService(self.root, self.core.command, self.runtime,
                                               self.audit, clock=self.clock)
                value, _ = await service.watch(action="list")
                self.assertEqual(value["lifecycle"]["overflow_store"], "reset")
                listed, _ = await service.list()
                self.assertEqual(listed["store"]["snapshots"], c.V1_INDEX_MAX_SNAPSHOTS)
        self.assertEqual((self.root / "elsewhere.json").read_bytes(), good)
        # The disclosed loss: the reset entries' record files are orphan-swept.
        records = {p.name[:-5] for p in (self.root / c.MODEL / "records").iterdir()}
        self.assertEqual(records, {e["snapshot_id"] for e in self.index()["snapshots"]})
        # The damaged file is reset on every load until the next write
        # replaces it (rename over it; a symlink is replaced, not followed).
        self.core.add("m1", minute=1)
        self.core.add("m2", minute=2)
        for run_id in ("m1", "m2"):
            value, _ = await self.service.capture(automation_id="a1", run_id=run_id)
            self.assertEqual(value["runs"][0]["outcome"], "captured")
        self.assertFalse(path.is_symlink())
        store_module.validate_overflow(self.overflow())
        self.assertEqual(len(self.overflow()["snapshots"]), 2)
        self.assertEqual((self.root / "elsewhere.json").read_bytes(), good)
        # The flag reports a reset this process saw; a fresh load is clean.
        service = TraceSnapshotService(self.root, self.core.command, self.runtime, self.audit,
                                       clock=self.clock)
        value, _ = await service.watch(action="list")
        self.assertEqual(value["lifecycle"]["overflow_store"], "ok")
        self.assert_v1_bounds()

    async def test_sidecar_entries_without_a_record_file_are_dropped(self):
        self.seed(c.V1_INDEX_MAX_SNAPSHOTS + 5)
        gone = self.overflow()["snapshots"][0]["snapshot_id"]
        (self.root / c.MODEL / "records" / (gone + ".json")).unlink()
        listed, _ = await self.service.list()
        self.assertEqual(listed["store"]["snapshots"], c.V1_INDEX_MAX_SNAPSHOTS + 4)
        # Rewritten without the dropped entry at that maintenance commit.
        self.assertNotIn(gone, {e["snapshot_id"] for e in self.overflow()["snapshots"]})
        self.assertEqual(self.service._lifecycle(self.service.store.load_index(),
                                                 0)["overflow_store"], "ok")

    async def test_the_v1_index_wins_over_a_sidecar_copy(self):
        self.seed(c.V1_INDEX_MAX_SNAPSHOTS + 1)
        index = self.index()
        spill = self.overflow()
        twin = dict(index["snapshots"][0], completeness="partial")
        same_run = dict(index["snapshots"][1], snapshot_id="f" * 32)
        spill["snapshots"] += [twin, same_run]
        (self.root / c.MODEL / "records" / ("f" * 32 + ".json")).write_bytes(b"{}")
        (self.root / c.MODEL / "snapshot-overflow.json").write_text(json.dumps(spill))
        merged = self.service.store.load_index()["snapshots"]
        self.assertEqual(len(merged), c.V1_INDEX_MAX_SNAPSHOTS + 1)
        first = next(e for e in merged if e["snapshot_id"] == twin["snapshot_id"])
        self.assertEqual(first["completeness"], "complete")
        self.assertNotIn("f" * 32, {e["snapshot_id"] for e in merged})

    async def test_a_sidecar_past_the_overall_caps_is_trimmed_least_valuable_first(self):
        with patch.object(c, "MAX_SNAPSHOTS", 30), patch.object(c, "V1_INDEX_MAX_SNAPSHOTS", 10):
            self.seed(30)
            spill = self.overflow()
            extra = dict(spill["snapshots"][-1], snapshot_id="e" * 32, run_id="extra-old",
                         captured_at_ms=spill["snapshots"][-1]["captured_at_ms"] - 1)
            spill["snapshots"].append(extra)
            (self.root / c.MODEL / "records" / ("e" * 32 + ".json")).write_bytes(b"{}")
            (self.root / c.MODEL / "snapshot-overflow.json").write_text(json.dumps(spill))
            merged = self.service.store.load_index()["snapshots"]
            self.assertEqual(len(merged), 30)
            self.assertNotIn("e" * 32, {e["snapshot_id"] for e in merged})
            await self.service.list()
            self.assertFalse((self.root / c.MODEL / "records" / ("e" * 32 + ".json")).exists())
            self.assert_v1_bounds()

    async def test_write_index_refuses_an_out_of_bounds_merged_set(self):
        self.seed(3)
        store = self.service.store
        before = self.store_files()
        with store.transaction():
            index = store.load_index()
            twin = dict(index["snapshots"][0])
            for bad in ("duplicate", "count", "bytes"):
                with self.subTest(bad=bad):
                    trial = {**index, "snapshots": list(index["snapshots"])}
                    if bad == "duplicate":
                        trial["snapshots"].append(twin)
                        context = patch.object(c, "MAX_SNAPSHOTS", c.MAX_SNAPSHOTS)
                    elif bad == "count":
                        context = patch.object(c, "MAX_SNAPSHOTS", 2)
                    else:
                        context = patch.object(c, "STORE_BYTES", 2)
                    with context, self.assertRaises(store_module.StoreError) as raised:
                        store.write_index(trial)
                    self.assertEqual(raised.exception.reason, "store_invalid")
        self.assertEqual(self.store_files(), before)


class StoreTests(LifecycleCase):
    async def test_twenty_watches_keep_index_json_valid_for_the_v1_rule(self):
        await self.fill(priority="normal")
        raw = self.index()
        self.assertEqual(len(raw["watches"]), c.V1_INDEX_MAX_WATCHES)
        store_module.validate_index(raw)  # the v1 rule, unchanged in shape
        self.assertEqual(len(self.sidecar()["overflow"]), c.MAX_WATCHES - c.V1_INDEX_MAX_WATCHES)
        self.assertEqual(len(self.service.store.load_index()["watches"]), c.MAX_WATCHES)

    def test_the_v1_rule_rejects_an_eleven_watch_index(self):
        provenance = json.loads(BETA6_PROVENANCE.read_text())
        self.assertEqual(provenance["verdicts"]["shipped_validate_index_rejects_eleven_watches"],
                         "store_invalid")
        self.assertEqual(provenance["verdicts"]["shipped_max_watches"], c.V1_INDEX_MAX_WATCHES)
        raw = json.loads((BETA6_STORE / c.MODEL / "index.json").read_text())
        extra = dict(raw["watches"][-1], watch_id="e" * 32, automation_id="extra")
        raw["watches"].append(extra)
        with self.assertRaises(store_module.StoreError):
            store_module.validate_index(raw)

    def test_the_shipped_snapshot_bounds_and_orphan_sweep(self):
        verdicts = json.loads(BETA6_PROVENANCE.read_text())["verdicts"]
        self.assertEqual(verdicts["shipped_max_snapshots"], c.V1_INDEX_MAX_SNAPSHOTS)
        self.assertEqual(verdicts["shipped_store_bytes"], c.V1_INDEX_MAX_BYTES)
        self.assertEqual(verdicts["shipped_index_bytes"], c.INDEX_BYTES)
        for key in ("shipped_validate_index_accepts_max_snapshots",
                    "shipped_validate_index_accepts_store_bytes",
                    "shipped_load_index_ignores_unknown_namespace_file",
                    "shipped_list_deletes_unnamed_record_file",
                    "shipped_list_keeps_named_record_files",
                    "shipped_list_leaves_sidecars_untouched",
                    "shipped_list_ignores_unknown_namespace_files"):
            with self.subTest(key=key):
                self.assertIs(verdicts[key], True)
        self.assertEqual(verdicts["shipped_validate_index_rejects_max_snapshots_plus_one"],
                         "store_invalid")
        self.assertEqual(verdicts["shipped_validate_index_rejects_store_bytes_plus_one"],
                         "store_invalid")

    async def test_removing_a_v1_watch_promotes_an_overflow_watch(self):
        await self.fill()
        await self.service.watch(action="remove", automation_id="w0")
        self.assertEqual(len(self.index()["watches"]), c.V1_INDEX_MAX_WATCHES)
        self.assertEqual(len(self.service.store.load_index()["watches"]), c.MAX_WATCHES - 1)

    async def test_a_failed_sidecar_write_leaves_the_index_unchanged(self):
        await self.fill(c.V1_INDEX_MAX_WATCHES)
        before = self.store_files()

        def fail(stage):
            if stage == "before_lifecycle":
                raise OSError("synthetic")
        self.service.store.fault_hook = fail
        error = await self.refused(self.service.watch(action="add", automation_id="w_extra"))
        self.service.store.fault_hook = None
        self.assertEqual(error.reason, "store_unavailable")
        self.assertEqual(self.store_files(), before)

    async def test_a_failed_index_write_after_the_sidecar_loses_no_watch(self):
        await self.fill(c.MAX_WATCHES)

        def fail(stage):
            if stage == "before_index":
                raise OSError("synthetic")
        self.service.store.fault_hook = fail
        await self.refused(self.service.watch(action="remove", automation_id="w0"))
        self.service.store.fault_hook = None
        held = [w["automation_id"] for w in self.service.store.load_index()["watches"]]
        self.assertEqual(len(held), len(set(held)))
        self.assertEqual(set(held), {f"w{i}" for i in range(c.MAX_WATCHES)})

    async def test_a_damaged_sidecar_resets_and_snapshots_stay_readable(self):
        self.core.add("r1")
        await self.service.capture(automation_id="a1")
        await self.add("a1", priority="high")
        path = self.root / c.MODEL / "watch-lifecycle.json"
        for damage in ("not json", "symlink", "oversize"):
            with self.subTest(damage=damage):
                path.unlink(missing_ok=True)
                if damage == "symlink":
                    target = self.root / "elsewhere.json"
                    target.write_text("{}")
                    path.symlink_to(target)
                elif damage == "oversize":
                    path.write_text(" " * (c.LIFECYCLE_BYTES + 1))
                else:
                    path.write_text(damage)
                service = TraceSnapshotService(self.root, self.core.command, self.runtime,
                                               self.audit, clock=self.clock)
                listed, _ = await service.list()
                self.assertEqual(len(listed["snapshots"]), 1)
                value, _ = await service.watch(action="list")
                self.assertEqual(value["lifecycle"]["lifecycle_store"], "reset")
                self.assertEqual([w["automation_id"] for w in value["watches"]], ["a1"])
        self.assertEqual((self.root / "elsewhere.json").read_text(), "{}")

    async def test_store_busy_changes_nothing(self):
        await self.fill(3)
        before = self.store_files()
        with patch.object(self.service.store, "transaction",
                          side_effect=store_module.StoreError("store_busy")):
            error = await self.refused(self.service.watch(action="renew", automation_id=""))
        self.assertEqual(error.reason, "store_busy")
        self.assertEqual(self.store_files(), before)


class Beta6FixtureTests(LifecycleCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        shutil.copytree(BETA6_STORE / c.MODEL, self.root / c.MODEL)

    def test_fixture_bytes_match_provenance(self):
        import hashlib  # noqa: PLC0415
        provenance = json.loads(BETA6_PROVENANCE.read_text())
        self.assertEqual(provenance["source"]["commit"], "af5ec8375401276f910804843c2ddb9180533c77")
        for name, digest in provenance["record"]["files"].items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((BETA6_STORE / name).read_bytes()).hexdigest(), digest)

    async def test_current_code_reads_the_shipped_v1_store(self):
        value = await self.listed()
        self.assertEqual(len(value["watches"]), 10)
        self.assertEqual({w["priority"] for w in value["watches"]}, {"normal"})
        self.assertEqual(value["lifecycle"]["lifecycle_store"], "ok")
        self.assertEqual(value["lifecycle"]["overflow_store"], "ok")
        listed, _ = await self.service.list()
        self.assertEqual(len(listed["snapshots"]), 2)
        self.assertIsNone(self.sidecar())
        self.assertIsNone(self.overflow())

    async def test_an_eleventh_watch_goes_to_the_sidecar(self):
        self.clock.now += 1  # the newest watch is the one past the v1 bound
        await self.add("eleventh")
        self.assertEqual(len(self.index()["watches"]), c.V1_INDEX_MAX_WATCHES)
        self.assertEqual([w["automation_id"] for w in self.sidecar()["overflow"]], ["eleventh"])
        store_module.validate_index(self.index())


class ArgumentTests(unittest.IsolatedAsyncioTestCase):
    def test_closed_lifecycle_arguments(self):
        refused = [
            {"action": "add", "automation_id": "a1", "priority": "urgent"},
            {"action": "add", "automation_id": "a1", "priority": 1},
            {"action": "add", "automation_id": "a1", "displace": "true"},
            {"action": "add", "automation_id": "a1", "displace": 1},
            {"action": "add", "automation_id": "a1", "include_lapsed": True},
            {"action": "remove", "automation_id": "a1", "displace": False},
            {"action": "remove", "automation_id": "a1", "priority": "high"},
            {"action": "list", "include_lapsed": False},
            {"action": "renew", "automation_id": "a1", "include_lapsed": True},
            {"action": "renew", "automation_id": "a1", "priority": "high"},
            {"action": "renew", "automation_id": "a1", "mode": "executed"},
            {"action": "renew", "include_lapsed": "yes"},
            {"action": "renew", "automation_id": "a1", "duration_hours": c.WATCH_MAX_HOURS + 1},
        ]
        for arguments in refused:
            with self.subTest(arguments=arguments):
                with self.assertRaises(c.TraceSnapshotError):
                    c.validate_watch(arguments)
        c.validate_watch({"action": "renew"})
        c.validate_watch({"action": "renew", "include_lapsed": True})
        c.validate_watch({"action": "add", "automation_id": "a1", "priority": "high",
                          "displace": True})

    async def test_the_tool_refuses_wrong_types_before_the_service(self):
        tool = tool_module.registered_tools()["set_automation_trace_watch"]
        self.assertEqual(set(tool.parameters["properties"]) - {
            "action", "automation_id", "mode", "duration_hours"}, {"priority", "displace", "include_lapsed"})
        self.assertFalse(tool.parameters["additionalProperties"])
        result = json.loads(await tool.run({"action": "add", "automation_id": "a1", "displace": "yes"}))
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "invalid_request")


class GatewayTests(base.GatewayStackTests):
    def test_renew_and_priority_reach_the_tool_without_core(self):
        rendered, record, _ = self.call("set_automation_trace_watch",
                                        {"action": "add", "automation_id": "a1", "priority": "high"})
        self.assertTrue(rendered["success"], rendered)
        self.assertEqual(record["parameters"]["priority"], "high")
        rendered, record, _ = self.call("set_automation_trace_watch",
                                        {"action": "renew", "automation_id": "", "include_lapsed": True})
        self.assertTrue(rendered["success"], rendered)
        self.assertEqual(record["parameters"]["include_lapsed"], True)
        rendered, record, text = self.call("set_automation_trace_watch",
                                           {"action": "remove", "automation_id": "a1", "displace": "x"})
        self.assertFalse(rendered["success"])
        self.assertEqual((self.core.sent, self.gateway_core.acquired), ([], []))


for _name in list(base.GatewayStackTests.__dict__):
    if _name.startswith("test_"):
        setattr(GatewayTests, _name, None)


class HealthTests(LifecycleCase):
    async def test_health_moves_from_not_yet_observed_to_observed(self):
        self.assertEqual(self.service.watch_health(), {"status": "not_yet_observed"})
        await self.add("a1", duration_hours=2)
        health = self.service.watch_health()
        self.assertEqual((health["status"], health["active"], health["limit"]),
                         ("observed", 1, c.MAX_WATCHES))
        self.clock.now += HOUR + 1
        await self.service.poll_once(authority=AUTHORITY)
        self.assertEqual(self.service.watch_health()["expiring_soon"][0]["automation_id"], "a1")

    async def test_health_never_echoes_a_withheld_identifier(self):
        await self.add(KNOWN, duration_hours=1)
        self.clock.now += 2 * HOUR
        await self.listed()
        rendered = json.dumps(self.service.watch_health())
        self.assertNotIn(KNOWN, rendered)
        self.assertRegex(self.service.watch_health()["lapsed"][0]["automation_id"], c.WITHHELD_ID)
        self.assertNotIn(KNOWN.encode(), base.all_store_bytes(self.root))

    async def test_health_reads_no_disk_and_sends_no_core_command(self):
        await self.add("a1")
        sent = len(self.core.sent)
        with patch.object(self.service.store, "load_index", side_effect=AssertionError("disk")):
            self.service.watch_health()
        self.assertEqual(len(self.core.sent), sent)

    async def test_runtime_block_degrades_alone(self):
        runtime = TraceSnapshotRuntime()
        self.assertEqual(runtime.watch_health(), {"configured": False, "status": "not_configured"})
        runtime.service = self.service
        self.assertEqual(runtime.watch_health(), {"configured": True, "status": "unavailable"})
        runtime.poller = SimpleNamespace(ticks=3, failed_ticks=0)
        self.assertEqual(runtime.watch_health()["status"], "not_yet_observed")
        await self.add("a1")
        self.assertEqual(runtime.watch_health()["status"], "observed")

    async def test_get_server_health_carries_the_trace_watches_block(self):
        runtime = TraceSnapshotRuntime()
        runtime.service = self.service
        runtime.poller = SimpleNamespace(ticks=1, failed_ticks=0)
        await self.add("a1")
        health = SimpleNamespace(async_snapshot=AsyncMock(return_value={"server": {}}))
        with patch.object(runtime_module, "TRACE_SNAPSHOTS", runtime), \
                patch.object(compatibility, "HEALTH", health):
            payload = json.loads(await compatibility.get_server_health(check_ha=False))
        self.assertTrue(payload["success"], payload)
        self.assertEqual(payload["data"]["trace_watches"]["active"], 1)
        self.assertEqual(payload["data"]["trace_watches"]["status"], "observed")


class NoCoreTests(LifecycleCase):
    async def test_renew_list_and_lapse_send_no_core_command(self):
        await self.add("a1", duration_hours=1)
        await self.renew("a1", duration_hours=24)
        await self.renew("", include_lapsed=True)
        self.clock.now += 30 * HOUR
        await self.listed()
        await self.renew("a1")
        self.service.watch_health()
        self.assertEqual(self.core.sent, [])
        self.assertEqual(self.runtime.calls, [])


if __name__ == "__main__":
    unittest.main()
