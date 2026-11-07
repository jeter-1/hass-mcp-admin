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


class BoundsTests(unittest.TestCase):
    def test_the_stated_bound_and_snapshot_budget(self):
        # Josh 2026-11-07 Q1 (b): the cap is raised to a stated bound of 20.
        self.assertEqual((c.MAX_WATCHES, c.V1_INDEX_MAX_WATCHES), (20, 10))
        self.assertEqual(c.WATCH_QUOTA, 5)
        self.assertEqual(c.WATCH_STORE_SNAPSHOTS, c.MAX_WATCHES * c.WATCH_QUOTA)
        self.assertEqual(c.WATCH_STORE_BYTES, c.WATCH_STORE_SNAPSHOTS * c.SNAPSHOT_BYTES)
        # Room that full watches can never take from manual captures.
        self.assertGreaterEqual(c.MAX_SNAPSHOTS - c.WATCH_STORE_SNAPSHOTS, 100)
        self.assertGreaterEqual(c.STORE_BYTES - c.WATCH_STORE_BYTES, 7 * 1024 * 1024)
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


class BudgetTests(LifecycleCase):
    async def test_twenty_full_watches_leave_room_for_manual_captures(self):
        await self.fill(mode="executed")
        await self.service.poll_once(authority=AUTHORITY)  # baselines
        for i in range(c.WATCH_QUOTA):
            self.core.add(f"run{i}", minute=i)
        await self.service.poll_once(authority=AUTHORITY)
        origin = [e for e in self.service.store.load_index()["snapshots"]
                  if e["origin"] == "trace_watch"]
        self.assertEqual(len(origin), c.WATCH_STORE_SNAPSHOTS)
        self.core.add("manual", minute=30)
        value, _ = await self.service.capture(automation_id="a1", run_id="manual")
        self.assertEqual(value["runs"][0]["outcome"], "captured")

    async def test_watch_captures_past_the_reservation_are_store_full(self):
        await self.add("a1", mode="executed")
        await self.add("a2", mode="executed")
        await self.service.poll_once(authority=AUTHORITY)
        for i in range(3):
            self.core.add(f"run{i}", minute=i)
        with patch.object(c, "WATCH_STORE_SNAPSHOTS", 4):
            await self.service.poll_once(authority=AUTHORITY)
        origin = [e for e in self.index()["snapshots"] if e["origin"] == "trace_watch"]
        self.assertEqual(len(origin), 4)
        # Nothing was evicted to make room: no new deletion class.
        evicted = [r["evicted_snapshot_ids"] for r in self.audit.rows
                   if r["event"] == "trace_watch_capture"]
        self.assertEqual(sum(len(ids) for ids in evicted), 0)
        self.core.add("manual", minute=40)
        with patch.object(c, "WATCH_STORE_SNAPSHOTS", 4):
            value, _ = await self.service.capture(automation_id="a1", run_id="manual")
        self.assertEqual(value["runs"][0]["outcome"], "captured")


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
        listed, _ = await self.service.list()
        self.assertEqual(len(listed["snapshots"]), 2)
        self.assertIsNone(self.sidecar())

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
