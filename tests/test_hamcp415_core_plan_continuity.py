"""HAMCP-415 (HAAC-E043): plans survive an identical Core authority restore.

A Core connection movement retires authority at once and restoration passes
the full recovery gate; neither changes here. What changes is the plan
cutoff: when the restored authority is identical to the one retired (same
observation apart from the authenticated session, same selected authority,
profile registry, release-registry selection and decisions), plans created
under that authority stay current. Any difference, a version or registry
movement, or an incompatible observation in between voids them, and so does
a plan created while authority was absent. Every retirement, replacement and
restoration records its reason. No live Home Assistant and no credential.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import unittest

from tests import test_ha_core_2026_9_integration as core_tests
from tests.support.core_gate import reconcile_through_gate
from ha_mcp_engineering.ha_core_readmission import CoreRuntime
from ha_mcp_engineering.ha_core_readmission import runtime as runtime_module
from ha_mcp_engineering.ha_core_readmission.routes import CORE_LEASE_STALE_PLAN


LOGGER = "ha_mcp_engineering.core_authority"
CAPABILITY = ("core.basic_rest_read",)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _context(record) -> dict:
    return getattr(record, "safe_context", None) or {}


class _Source(core_tests._MutableSource):
    fail = False

    async def capture_core_snapshot(self):
        if self.fail:
            raise TimeoutError
        return await super().capture_core_snapshot()


class CorePlanContinuity(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.source = _Source(core_tests._core_2026_9_snapshot())
        self.core = CoreRuntime()
        self.core.configure(object(), source=self.source)
        await self.core.reconcile_once("startup")
        self.assertTrue(self.core.route_status(CAPABILITY)["available"])
        # The boot admission sets the plan cutoff, so a plan before it is
        # stale throughout. This first reconnect is itself an identical
        # restoration and records one outage window.
        self.core.request_reconciliation(connection_changed=True)
        await reconcile_through_gate(self.core)
        self.assertTrue(self.core.route_status(CAPABILITY)["available"])
        self.before_cutoff = _iso(datetime.now(timezone.utc) - timedelta(days=1))
        self.plan_at = _iso(datetime.now(timezone.utc))

    def decide(self, created_at):
        decision = self.core.acquire_decision(CAPABILITY, plan_created_at=created_at)
        if decision.authority is not None:
            self.core.release(decision.authority)
        return decision

    def assert_current(self, created_at):
        decision = self.decide(created_at)
        self.assertIsNone(decision.refusal)
        self.assertIsNotNone(decision.authority)

    def assert_stale(self, created_at):
        decision = self.decide(created_at)
        self.assertEqual(decision.refusal, CORE_LEASE_STALE_PLAN)
        self.assertIsNone(decision.authority)

    async def reconnect(self, *, session_id="core-session-reconnected", fail_once=False):
        self.source.snapshot["session_id"] = session_id
        self.core.request_reconciliation(connection_changed=True)
        if fail_once:
            self.source.fail = True
            await self.core.reconcile_once("periodic")
            self.source.fail = False
        return await reconcile_through_gate(self.core)

    async def test_identical_restore_keeps_plan_current(self):
        self.assert_current(self.plan_at)
        with self.assertLogs(LOGGER, level=logging.INFO) as logs:
            await self.reconnect()
        self.assertTrue(self.core.route_status(CAPABILITY)["available"])
        self.assert_current(self.plan_at)
        # The pre-existing cutoff still applies.
        self.assert_stale(self.before_cutoff)
        restored = [r for r in logs.records if getattr(r, "event", None) == "core_authority_restored"]
        self.assertEqual(len(restored), 1)
        self.assertEqual(_context(restored[0])["reason"], "core_authority_identical")
        self.assertIs(_context(restored[0])["plans_preserved"], True)
        retired = [r for r in logs.records if getattr(r, "event", None) == "core_authority_retired"]
        self.assertEqual([_context(r)["reason"] for r in retired], ["core_connection_changed"])

    async def test_identical_restore_after_failed_probe_keeps_plan_current(self):
        await self.reconnect(fail_once=True)
        self.assertTrue(self.core.route_status(CAPABILITY)["available"])
        self.assert_current(self.plan_at)

    async def test_plan_created_while_authority_absent_stays_stale(self):
        self.source.snapshot["session_id"] = "core-session-reconnected"
        self.core.request_reconciliation(connection_changed=True)
        during = _iso(datetime.now(timezone.utc))
        await reconcile_through_gate(self.core)
        self.assertTrue(self.core.route_status(CAPABILITY)["available"])
        self.assert_current(self.plan_at)
        self.assert_stale(during)
        after = _iso(datetime.now(timezone.utc))
        self.assert_current(after)

    async def test_repeated_identical_outages_keep_plan_current(self):
        for index in range(3):
            await self.reconnect(session_id=f"core-session-{index}")
            self.assert_current(self.plan_at)

    async def test_changed_evidence_on_restore_voids_plan(self):
        evidence = self.source.snapshot["capability_evidence"]
        self.assertTrue(evidence)
        evidence.pop()
        with self.assertLogs(LOGGER, level=logging.INFO) as logs:
            await self.reconnect()
        self.assertTrue(self.core.route_status(CAPABILITY)["available"])
        self.assert_stale(self.plan_at)
        restored = [r for r in logs.records if getattr(r, "event", None) == "core_authority_restored"]
        self.assertEqual(len(restored), 1)
        context = _context(restored[0])
        self.assertEqual(context["reason"], "core_authority_changed")
        self.assertIs(context["plans_preserved"], False)
        self.assertIn("capability_evidence", context["changed_components"])
        self.assertNotIn("core_identity", context["changed_components"])

    async def test_version_change_voids_plan_even_if_it_returns(self):
        with self.assertLogs(LOGGER, level=logging.INFO) as logs:
            self.source.snapshot = core_tests._core_2026_9_snapshot(version="2026.9.1")
            await reconcile_through_gate(self.core)
            self.source.snapshot = core_tests._core_2026_9_snapshot(
                session_id="core-session-back"
            )
            await reconcile_through_gate(self.core)
        self.assertTrue(self.core.route_status(CAPABILITY)["available"])
        self.assert_stale(self.plan_at)
        restored = [r for r in logs.records if getattr(r, "event", None) == "core_authority_restored"]
        self.assertTrue(restored)
        context = _context(restored[-1])
        self.assertEqual(context["reason"], "core_authority_changed")
        self.assertIs(context["plans_preserved"], False)
        self.assertEqual(context["baseline_discarded_reason"], "core_observed_version_changed")
        # Only what really moved is named: the version, not the whole set.
        self.assertIn("core_identity", context["changed_components"])
        self.assertNotIn("core_connection", context["changed_components"])
        self.assertNotIn("profile_registry", context["changed_components"])

    async def test_incompatible_observation_between_voids_plan(self):
        self.source.snapshot["session_id"] = "core-session-reconnected"
        self.core.request_reconciliation(connection_changed=True)
        original = self.source.snapshot
        self.source.snapshot = core_tests._snapshot(
            "2026.9.0", session_id="core-session-reconnected", evidence=[]
        )
        await self.core.reconcile_once("periodic")
        self.assertFalse(self.core.route_status(CAPABILITY)["available"])
        self.source.snapshot = original
        with self.assertLogs(LOGGER, level=logging.INFO) as logs:
            await reconcile_through_gate(self.core)
        self.assertTrue(self.core.route_status(CAPABILITY)["available"])
        self.assert_stale(self.plan_at)
        restored = [r for r in logs.records if getattr(r, "event", None) == "core_authority_restored"]
        self.assertEqual(len(restored), 1)
        context = _context(restored[0])
        self.assertIs(context["plans_preserved"], False)
        self.assertEqual(context["baseline_discarded_reason"], "core_authority_incompatible")
        # The restored authority equals the last admitted one; the void comes
        # from the incompatible observation in between, not from the failed
        # observation's fingerprints.
        self.assertEqual(context["changed_components"], [])

    async def test_incomplete_observation_then_identical_restore_keeps_plan(self):
        # An incomplete observation (Core not connected, nothing proven) is a
        # transient outage, not an incompatible authority: the preserved
        # baseline is the admitted authority, never the failed observation.
        authority = self.core.acquire_decision(
            CAPABILITY, plan_created_at=self.plan_at
        ).authority
        self.assertIsNotNone(authority)
        self.source.snapshot = core_tests._snapshot(
            "2026.9.0", session_id="core-session-2026-9", connected=False, evidence=[]
        )
        await self.core.reconcile_once("periodic")
        self.assertFalse(self.core.route_status(CAPABILITY)["available"])
        self.assertEqual(
            self.core._preserved_plan_baseline[1], self.core._admitted_semantics
        )
        self.source.snapshot = core_tests._core_2026_9_snapshot()
        with self.assertLogs(LOGGER, level=logging.INFO) as logs:
            await reconcile_through_gate(self.core)
        self.assertTrue(self.core.route_status(CAPABILITY)["available"])
        self.assert_current(self.plan_at)
        # A lease from the retired generation still cannot dispatch.
        self.assertIsNone(self.core.consume(authority))
        restored = [r for r in logs.records if getattr(r, "event", None) == "core_authority_restored"]
        self.assertEqual(len(restored), 1)
        self.assertEqual(_context(restored[0])["reason"], "core_authority_identical")
        self.assertNotIn("baseline_discarded_reason", _context(restored[0]))

    async def test_window_capacity_overflow_fails_closed(self):
        capacity = runtime_module.CORE_PLAN_OUTAGE_WINDOW_CAPACITY
        held = len(self.core._plan_outage_windows)
        self.assertEqual(held, 1)
        for index in range(capacity - held):
            await self.reconnect(session_id=f"core-session-{index}")
            self.assert_current(self.plan_at)
        with self.assertLogs(LOGGER, level=logging.INFO) as logs:
            await self.reconnect(session_id="core-session-overflow")
        self.assertTrue(self.core.route_status(CAPABILITY)["available"])
        self.assert_stale(self.plan_at)
        restored = [r for r in logs.records if getattr(r, "event", None) == "core_authority_restored"]
        self.assertEqual(_context(restored[-1])["reason"], "core_plan_window_capacity")
        self.assertIs(_context(restored[-1])["plans_preserved"], False)
        self.assertEqual(self.core._plan_outage_windows, ())
        self.assert_current(_iso(datetime.now(timezone.utc)))

    async def test_session_only_refresh_does_not_retire(self):
        # Published admitted -> admitted with only the session moved (no
        # connection event observed): a refresh, not a retirement.
        retirements = self.core.health_snapshot()["counters"]["retirements"]
        self.source.snapshot["session_id"] = "core-session-silent"
        with self.assertLogs(LOGGER, level=logging.INFO) as logs:
            await self.core.reconcile_once("periodic")
        self.assertTrue(self.core.route_status(CAPABILITY)["available"])
        self.assert_current(self.plan_at)
        self.assertEqual(self.core.health_snapshot()["counters"]["retirements"], retirements)
        refreshed = [
            r for r in logs.records
            if getattr(r, "event", None) == "core_authority_generation_refreshed"
        ]
        self.assertEqual(len(refreshed), 1)
        self.assertEqual(_context(refreshed[0])["reason"], "core_session_changed")

    async def test_changed_admitted_replacement_records_reason_and_voids_plan(self):
        evidence = self.source.snapshot["capability_evidence"]
        evidence.pop()
        with self.assertLogs(LOGGER, level=logging.INFO) as logs:
            await self.core.reconcile_once("periodic")
        self.assert_stale(self.plan_at)
        replaced = [
            r for r in logs.records
            if getattr(r, "event", None) == "core_authority_replaced"
        ]
        self.assertTrue(self.core.route_status(CAPABILITY)["available"])
        self.assertEqual(len(replaced), 1)
        self.assertEqual(replaced[0].levelno, logging.WARNING)
        context = _context(replaced[0])
        self.assertEqual(context["reason"], "core_authority_semantics_changed")
        self.assertIs(context["plans_preserved"], False)
        self.assertIn("capability_evidence", context["changed_components"])
        # A plan created under the replacement is current.
        self.assert_current(_iso(datetime.now(timezone.utc)))

    async def test_configure_clears_preserved_baseline(self):
        self.source.snapshot["session_id"] = "core-session-reconnected"
        self.core.request_reconciliation(connection_changed=True)
        self.core.configure(object(), source=self.source)
        self.assertIsNone(self.core._preserved_plan_baseline)
        self.assertEqual(self.core._plan_outage_windows, ())


if __name__ == "__main__":
    unittest.main()
