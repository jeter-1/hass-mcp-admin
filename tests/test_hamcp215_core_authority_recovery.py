"""HAMCP-215: Core authority recovers from transients, visibly and fail-closed.

Design ``docs/HAMCP_215_CORE_AUTHORITY_RECOVERY_DESIGN.md``. The production
CoreRuntime, coordinator, aiohttp snapshot source and lifecycle watcher run
unchanged; only network sessions and the supervisor's retry clock are
synthetic (the RC3 post-Core harness). Midpoint jitter makes every delay exact.
No live Home Assistant and no real credential.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
import unittest
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP

from tests import test_ha_core_2026_9_integration as core_tests
from tests import test_rc3_postcore_regressions as rc3
from tests import test_dependency_build_authority as dependency_tests
import tests.test_hamcp162_yaml_admission as yaml_tests
from ha_mcp_engineering.clients.rest import HomeAssistantRestClient
from ha_mcp_engineering.clients.websocket import HomeAssistantWebSocketClient
from ha_mcp_engineering.dependency.runtime import DependencyAnalysisRuntime
from ha_mcp_engineering.errors import HomeAssistantUnavailableError
from ha_mcp_engineering.ha_core_readmission import CoreRuntime
from ha_mcp_engineering.ha_core_readmission.runtime import (
    CORE_RECOVERY_CONFIRMATION_SECONDS,
    CORE_RECOVERY_CONFIRMATIONS,
    CORE_RECOVERY_FLAP_CONFIRMATIONS,
    CORE_RECOVERY_FLAP_WINDOW_SECONDS,
    CORE_RECOVERY_RETRY_BASE_SECONDS,
    CORE_RECOVERY_RETRY_CAP_SECONDS,
    CORE_RECOVERY_RETRY_JITTER,
    CORE_RECOVERY_STATES,
)
from ha_mcp_engineering.ha_core_readmission.source import AiohttpCoreSnapshotSource


LOGGER = "ha_mcp_engineering.core_authority"
CAPABILITY = ("core.basic_rest_read",)
# Midpoint jitter: confirmation spacing is CONFIRMATION * (1 + JITTER * 0.5).
CONFIRM = CORE_RECOVERY_CONFIRMATION_SECONDS * (1 + CORE_RECOVERY_RETRY_JITTER * 0.5)
RECOVERY_KEYS = {
    "recovery_state", "consecutive_failures", "last_failure_reason",
    "last_failure_detail", "last_failure_at", "retired_at", "last_attempt_at",
    "next_recovery_attempt_at", "retry_delay_seconds", "confirmations",
    "confirmations_required", "last_restored_at",
}
WARNING_EVENTS = {
    "core_authority_retired", "core_authority_verification_failed",
    "core_authority_recovery_attempt", "core_authority_recovery_confirming",
    "core_authority_recovery_reset", "core_authority_restored",
    "core_authority_capabilities_withdrawn", "core_catalog_tools_withdrawn",
    "core_catalog_tools_restored",
}
# mode -> (how the transient is noticed, retired reason, failure detail)
TRANSIENTS = {
    "http": ("probe", "core_lifecycle_monitor_unavailable", "core_probe_http_failure"),
    "connection": ("probe", "core_lifecycle_monitor_unavailable", "core_probe_connection_failed"),
    "timeout": ("probe", "core_lifecycle_monitor_unavailable", "core_probe_timeout"),
    "auth": ("probe", "core_lifecycle_monitor_unavailable", "core_probe_authentication_failed"),
    "monitor_auth": ("socket", "core_connection_changed", "core_lifecycle_monitor_attach_failed"),
    "monitor_timeout": ("socket", "core_connection_changed", "core_lifecycle_monitor_attach_failed"),
}


class _FlakySource(core_tests._MutableSource):
    """A snapshot source without a lifecycle watcher that can time out."""

    fail = False

    async def capture_core_snapshot(self):
        if self.fail:
            raise asyncio.TimeoutError
        return await super().capture_core_snapshot()


def _events(records, event):
    return [record for record in records if getattr(record, "event", None) == event]


class Recovery(unittest.IsolatedAsyncioTestCase):
    # Borrow the RC3 harness without re-running its tests in this module.
    asyncSetUp = rc3.PostCoreRegressions.asyncSetUp
    assert_settled = rc3.PostCoreRegressions.assert_settled
    assert_authority_round_trip = rc3.PostCoreRegressions.assert_authority_round_trip
    until = rc3.PostCoreRegressions.until
    start_supervisor = rc3.PostCoreRegressions.start_supervisor

    async def asyncTearDown(self):
        await self.stop_core()
        await rc3.PostCoreRegressions.asyncTearDown(self)

    async def stop_core(self):
        if self.supervisor is not None:
            self.supervisor.cancel()
            await asyncio.gather(self.supervisor, return_exceptions=True)
        monitor = self.core._connection_monitor_task
        if monitor is not None:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        self.core._connection_monitor_task = None

    async def fresh_core(self):
        await self.stop_core()
        self.network.mode = "ok"
        self.supervisor = None
        self.clock = rc3._RetryClock()
        self.notifications = []
        self.core = CoreRuntime()
        self.core.configure(self.settings, source=AiohttpCoreSnapshotSource(self.settings))
        self.core.register_reconciliation_listener(
            lambda: self.notifications.append(self.core.health_snapshot()["current_generation"])
        )

    def recovery(self):
        return self.core.health_projection()["recovery"]

    def counters(self):
        return self.core.health_snapshot()["counters"]

    async def admit(self):
        self.start_supervisor()
        await self.until(lambda: self.core.initialized and self.network.monitors
                         and self.core._connection_monitor_epoch is not None)
        self.assertEqual(self.recovery()["recovery_state"], "stable")

    def induce(self, mode):
        self.network.mode = mode
        if TRANSIENTS[mode][0] == "probe":
            self.core.request_reconciliation()  # an ordinary wake meets the fault
        else:
            self.network.monitors[-1].disconnect.set()  # the watched socket moves

    async def release(self, expected):
        delay = await self.clock.next_delay()
        self.assertAlmostEqual(self.clock.delays[-1], expected, places=6)
        return delay

    def assert_withheld(self):
        self.assertFalse(self.core.initialized)
        self.assertIsNone(self.core.acquire(CAPABILITY))
        self.assert_settled()

    def assert_bounded_projection(self):
        recovery = self.recovery()
        self.assertEqual(set(recovery), RECOVERY_KEYS)
        self.assertIn(recovery["recovery_state"], CORE_RECOVERY_STATES)
        self.assertLess(len(json.dumps(recovery)), 4096)
        for key in ("last_failure_at", "retired_at", "last_attempt_at",
                    "next_recovery_attempt_at", "last_restored_at"):
            if recovery[key] is not None:
                self.assertIsNotNone(datetime.fromisoformat(recovery[key]).tzinfo, key)
        return recovery

    def assert_logs_safe(self, records):
        token = self.settings.ha_token
        for record in records:
            text = record.getMessage() + json.dumps(getattr(record, "safe_context", {}), default=str)
            self.assertNotIn(token, text)
            self.assertNotIn("synthetic connection loss", text)  # exception text
            if record.event in WARNING_EVENTS:
                self.assertEqual(record.levelno, logging.WARNING, record.event)
            self.assertIn("at", record.safe_context)

    async def recover_through_confirmation(self, first_delay, *, required=CORE_RECOVERY_CONFIRMATIONS,
                                           spacing=CONFIRM, delay=None):
        """Release a backoff delay with Core healthy, then each confirmation."""
        restorations = self.counters()["authority_restorations"]
        if delay is None:
            delay = await self.release(first_delay)
        self.network.mode = "ok"
        delay.set_result(None)
        for count in range(1, required):
            delay = await self.release(spacing)
            recovery = self.assert_bounded_projection()
            self.assertEqual((recovery["recovery_state"], recovery["confirmations"],
                              recovery["confirmations_required"]),
                             ("confirming", count, required))
            self.assert_withheld()
            self.assertEqual(self.counters()["authority_restorations"], restorations)
            delay.set_result(None)
        await self.until(lambda: self.core.initialized)
        self.assertEqual(self.counters()["authority_restorations"], restorations + 1)

    # -- A: each transient retires, then restores automatically -------------

    async def test_each_transient_retires_then_restores_after_confirmed_reverification(self):
        for mode, (_, reason, detail) in TRANSIENTS.items():
            with self.subTest(mode=mode):
                await self.fresh_core()
                await self.admit()
                with self.assertLogs(LOGGER, level="INFO") as logs:
                    self.induce(mode)
                    delay = await self.release(CORE_RECOVERY_RETRY_BASE_SECONDS)
                    self.assert_withheld()
                    self.assertEqual(len(self.notifications), 2)  # admitted, retired
                    recovery = self.assert_bounded_projection()
                    self.assertEqual(recovery["recovery_state"], "backing_off")
                    self.assertEqual(recovery["consecutive_failures"], 1)
                    self.assertEqual(recovery["last_failure_reason"],
                                     "core_lifecycle_monitor_unavailable")
                    self.assertEqual(recovery["last_failure_detail"], detail)
                    self.assertIsNotNone(recovery["retired_at"])
                    self.assertIsNotNone(recovery["next_recovery_attempt_at"])
                    self.assertEqual(recovery["retry_delay_seconds"],
                                     CORE_RECOVERY_RETRY_BASE_SECONDS)
                    await self.assert_restores(delay, reason, detail)
                self.assert_restored(logs.records, reason, detail)

    async def assert_restores(self, delay, reason, detail):
        await self.recover_through_confirmation(None, delay=delay)

    def assert_restored(self, records, reason, detail):
        retired = _events(records, "core_authority_retired")
        self.assertEqual(len(retired), 1)
        self.assertEqual(retired[0].safe_context["reason"], reason)
        self.assertTrue(retired[0].safe_context["admitted"])
        failed = _events(records, "core_authority_verification_failed")
        self.assertGreaterEqual(len(failed), 1)
        self.assertEqual(failed[0].safe_context["detail"], detail)
        self.assertEqual(len(_events(records, "core_authority_recovery_confirming")), 1)
        restored = _events(records, "core_authority_restored")
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].safe_context["recovery_state"], "restored")
        self.assertEqual(restored[0].safe_context["verified_confirmations"],
                         CORE_RECOVERY_CONFIRMATIONS)
        self.assertGreaterEqual(len(_events(records, "core_authority_recovery_attempt")), 2)
        self.assert_logs_safe(records)
        recovery = self.assert_bounded_projection()
        self.assertEqual(recovery["recovery_state"], "restored")
        self.assertEqual(recovery["consecutive_failures"], 0)
        self.assertIsNotNone(recovery["last_restored_at"])
        self.assertIsNone(recovery["next_recovery_attempt_at"])
        self.assertEqual(self.core.health_snapshot()["compatible_count"], 17)
        self.assertEqual(len(self.notifications), 3)  # admitted, retired, restored
        self.assert_authority_round_trip()

    async def test_single_supervisor_recovers_without_restart(self):
        await self.admit()
        with self.assertLogs(LOGGER, level="INFO") as logs:
            self.induce("timeout")
            delay = await self.release(CORE_RECOVERY_RETRY_BASE_SECONDS)
            self.network.mode = "ok"
            delay.set_result(None)
            delay = await self.release(CONFIRM)
            self.assertEqual(self.recovery()["recovery_state"], "confirming")
            self.assert_withheld()
            delay.set_result(None)
            await self.until(lambda: self.core.initialized)
        names = [record.event for record in logs.records]
        self.assertEqual(
            [name for name in names if name != "core_authority_recovery_scheduled"],
            ["core_authority_retired", "core_authority_verification_failed",
             "core_authority_recovery_attempt", "core_authority_recovery_confirming",
             "core_authority_recovery_attempt", "core_authority_restored"],
        )
        self.assertEqual(names.count("core_authority_recovery_scheduled"), 2)
        counters = self.counters()
        self.assertEqual((counters["recovery_attempts"], counters["recovery_confirmations"],
                          counters["authority_restorations"], counters["retirements"]),
                         (2, 1, 1, 1))
        self.assertEqual(self.recovery()["recovery_state"], "restored")
        self.assert_authority_round_trip()

    # -- A: persistent failure stays withheld, backs off and logs ----------

    async def test_persistent_failure_backs_off_to_cap_logs_and_refuses(self):
        await self.admit()
        expected = [min(CORE_RECOVERY_RETRY_CAP_SECONDS, CORE_RECOVERY_RETRY_BASE_SECONDS * 2 ** n)
                    for n in range(8)]
        self.assertEqual(expected[:6], [20, 40, 80, 160, 240, 240])
        with self.assertLogs(LOGGER, level="INFO") as logs:
            self.induce("http")
            for index, value in enumerate(expected):
                delay = await self.release(value)
                self.assert_withheld()
                recovery = self.assert_bounded_projection()
                self.assertEqual(recovery["recovery_state"], "backing_off")
                self.assertEqual(recovery["consecutive_failures"], index + 1)
                self.assertEqual(recovery["retry_delay_seconds"], value)
                delay.set_result(None)
            await self.clock.next_delay()
        self.assertEqual(len(self.notifications), 2)  # one retirement notification
        self.assertEqual(len(_events(logs.records, "core_authority_retired")), 1)
        self.assertEqual(len(_events(logs.records, "core_authority_verification_failed")),
                         len(expected) + 1)
        self.assertEqual(len(_events(logs.records, "core_authority_recovery_attempt")),
                         len(expected))
        self.assertEqual(_events(logs.records, "core_authority_restored"), [])
        self.assertEqual(self.counters()["retirements"], 1)
        self.assert_logs_safe(logs.records)

    async def test_startup_failure_is_logged_and_paced(self):
        self.network.mode = "connection"
        with self.assertLogs(LOGGER, level="INFO") as logs:
            self.start_supervisor()
            delay = await self.release(CORE_RECOVERY_RETRY_BASE_SECONDS)
        failed = _events(logs.records, "core_authority_verification_failed")
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].safe_context["detail"], "core_probe_connection_failed")
        self.assertEqual(_events(logs.records, "core_authority_retired"), [])  # nothing published
        self.assertEqual(self.recovery()["recovery_state"], "backing_off")
        self.network.mode = "ok"
        delay.set_result(None)
        # Nothing was admitted before, so the first complete success publishes.
        await self.until(lambda: self.core.initialized)
        self.assertEqual(self.recovery()["recovery_state"], "stable")
        self.assert_authority_round_trip()

    # -- A: an incompatible Core stays retired but is re-checked -----------

    async def test_incompatibility_stays_retired_and_is_rechecked_without_backoff(self):
        self.core.request_reconciliation()
        self.supervisor = asyncio.create_task(self.core.supervise(
            interval_seconds=0.05, sleep=self.clock.sleep, jitter=lambda: 0.5))
        await self.until(lambda: self.core.initialized and self.core.acquire(CAPABILITY))
        self.core._coordinator._issued_leases.clear() if hasattr(
            self.core._coordinator, "_issued_leases") else None
        original = self.core._authority_provider
        with self.assertLogs(LOGGER, level="INFO") as logs:
            with patch.object(self.core, "_authority_provider", return_value=()):
                await self.until(lambda: self.recovery()["recovery_state"] == "incompatible")
                attempts = self.counters()["verification_attempts"]
                await self.until(lambda: self.counters()["verification_attempts"] >= attempts + 3)
                self.assertIsNone(self.core.acquire(CAPABILITY))
                recovery = self.assert_bounded_projection()
                self.assertEqual(recovery["last_failure_reason"], "core_authority_incompatible")
                self.assertIsNotNone(recovery["next_recovery_attempt_at"])
                self.assertEqual(self.clock.delays, [])  # periodic re-check, not backoff
            await self.until(lambda: self.recovery()["recovery_state"] == "restored")
        self.assertIs(self.core._authority_provider, original)
        retired = _events(logs.records, "core_authority_retired")
        self.assertEqual(len(retired), 1)
        self.assertEqual(retired[0].safe_context["reason"], "core_authority_incompatible")
        self.assertEqual(len(_events(logs.records, "core_authority_restored")), 1)
        self.assertEqual(self.counters()["retirements"], 2)  # withdrawal, then restoration generation
        self.assert_authority_round_trip()

    # -- A: hysteresis -----------------------------------------------------

    async def test_failure_during_confirmation_restarts_the_count(self):
        await self.admit()
        with self.assertLogs(LOGGER, level="INFO") as logs:
            self.induce("http")
            delay = await self.release(CORE_RECOVERY_RETRY_BASE_SECONDS)
            self.network.mode = "ok"
            delay.set_result(None)
            delay = await self.release(CONFIRM)
            self.assertEqual(self.recovery()["confirmations"], 1)
            self.network.mode = "http"  # the confirmation attempt fails
            delay.set_result(None)
            await self.recover_through_confirmation(2 * CORE_RECOVERY_RETRY_BASE_SECONDS)
        self.assertEqual(len(_events(logs.records, "core_authority_restored")), 1)
        self.assertEqual(len(_events(logs.records, "core_authority_recovery_confirming")), 2)
        self.assertEqual(self.counters()["authority_restorations"], 1)
        self.assert_authority_round_trip()

    async def test_connection_move_during_confirmation_restarts_the_count(self):
        await self.admit()
        with self.assertLogs(LOGGER, level="INFO") as logs:
            self.induce("http")
            delay = await self.release(CORE_RECOVERY_RETRY_BASE_SECONDS)
            self.network.mode = "ok"
            delay.set_result(None)
            delay = await self.release(CONFIRM)
            self.core.request_reconciliation(connection_changed=True)
            self.assertEqual(self.recovery()["confirmations"], 0)
            self.assertEqual(self.recovery()["recovery_state"], "retrying")
            delay.set_result(None)
            # The next attempt is the first of a fresh confirmation sequence.
            delay = await self.release(CONFIRM)
            self.assertEqual(self.recovery()["confirmations"], 1)
            self.assert_withheld()
            delay.set_result(None)
            await self.until(lambda: self.core.initialized)
        reset = _events(logs.records, "core_authority_recovery_reset")
        self.assertEqual(len(reset), 1)
        self.assertEqual(reset[0].safe_context["reason"], "core_connection_changed")
        self.assertEqual(len(_events(logs.records, "core_authority_restored")), 1)

    async def test_monitorless_failure_during_confirmation_restarts_the_count(self):
        source = _FlakySource(core_tests._core_2026_9_snapshot())
        core = CoreRuntime()
        core.configure(object(), source=source)
        await core.reconcile_once("startup")
        self.assertTrue(core.release(core.acquire(CAPABILITY)))
        with self.assertLogs(LOGGER, level="INFO") as logs:
            source.fail = True
            await core.reconcile_once("periodic")
            recovery = core.health_projection()["recovery"]
            self.assertEqual((recovery["recovery_state"], recovery["last_failure_reason"],
                              recovery["last_failure_detail"]),
                             ("backing_off", "core_observation_unavailable", "core_probe_timeout"))
            for fail, confirmations in ((False, 1), (True, 0), (False, 1)):
                source.fail = fail
                await core.reconcile_once("failure_retry")
                self.assertEqual(core.health_projection()["recovery"]["confirmations"], confirmations)
                self.assertIsNone(core.acquire(CAPABILITY))
            await core.reconcile_once("failure_retry")
        self.assertEqual(core.health_projection()["recovery"]["recovery_state"], "restored")
        self.assertEqual(len(_events(logs.records, "core_authority_retired")), 1)
        self.assertEqual(len(_events(logs.records, "core_authority_restored")), 1)
        self.assertTrue(core.release(core.acquire(CAPABILITY)))

    async def test_different_version_during_confirmation_restarts_the_count(self):
        await self.admit()
        self.induce("http")
        delay = await self.release(CORE_RECOVERY_RETRY_BASE_SECONDS)
        self.network.mode = "ok"
        delay.set_result(None)
        delay = await self.release(CONFIRM)
        self.assertEqual(self.recovery()["confirmations"], 1)
        self.network.version = "2026.9.0"  # a different reviewed Core answers next
        delay.set_result(None)
        delay = await self.release(CONFIRM)
        self.assertEqual(self.recovery()["confirmations"], 1)  # a new streak, not two
        self.assert_withheld()
        delay.set_result(None)
        await self.until(lambda: self.core.initialized)
        self.assertEqual(self.core.current_observation.version, "2026.9.0")
        self.assertEqual(self.counters()["authority_restorations"], 1)
        self.assert_authority_round_trip()

    async def test_on_demand_reverification_during_recovery_is_refused_and_does_not_count(self):
        await self.admit()
        self.induce("http")
        delay = await self.release(CORE_RECOVERY_RETRY_BASE_SECONDS)
        self.network.mode = "ok"
        delay.set_result(None)
        delay = await self.release(CONFIRM)
        for trigger in ("mutation_pre_dispatch", "configuration_reverification", "startup"):
            snapshot = await self.core.reconcile_once(trigger)
            self.assertFalse(snapshot["initialized"], trigger)
            self.assertEqual(self.recovery()["confirmations"], 1, trigger)
            self.assertIsNone(self.core.acquire(CAPABILITY))
        delay.set_result(None)
        await self.until(lambda: self.core.initialized)
        self.assertEqual(self.counters()["authority_restorations"], 1)

    async def test_flapping_requires_more_spaced_confirmations_and_does_not_toggle(self):
        clock = [1000.0]
        self.core._monotonic = lambda: clock[0]
        await self.admit()
        self.induce("http")
        await self.recover_through_confirmation(CORE_RECOVERY_RETRY_BASE_SECONDS)
        with self.assertLogs(LOGGER, level="INFO") as logs:
            clock[0] += 60  # a second outage inside the flap window
            self.induce("http")
            flap_spacing = CONFIRM * 2
            await self.recover_through_confirmation(
                2 * CORE_RECOVERY_RETRY_BASE_SECONDS,
                required=CORE_RECOVERY_FLAP_CONFIRMATIONS, spacing=flap_spacing)
        self.assertEqual(self.counters()["authority_restorations"], 2)
        self.assertEqual(len(_events(logs.records, "core_authority_restored")), 1)
        self.assertEqual(
            _events(logs.records, "core_authority_restored")[0].safe_context["verified_confirmations"],
            CORE_RECOVERY_FLAP_CONFIRMATIONS)

        # Outside the flap window the ordinary confirmation count applies again.
        clock[0] += CORE_RECOVERY_FLAP_WINDOW_SECONDS + 1
        self.induce("http")
        await self.recover_through_confirmation(CORE_RECOVERY_RETRY_BASE_SECONDS)
        self.assertEqual(self.counters()["authority_restorations"], 3)
        self.assert_authority_round_trip()

    async def test_rapid_flapping_never_publishes_between_confirmations(self):
        await self.admit()
        published = []
        self.core.register_reconciliation_listener(lambda: published.append(self.core.initialized))
        self.induce("http")
        delay = await self.release(CORE_RECOVERY_RETRY_BASE_SECONDS)
        for value in (2 * CORE_RECOVERY_RETRY_BASE_SECONDS, 4 * CORE_RECOVERY_RETRY_BASE_SECONDS):
            self.network.mode = "ok"
            delay.set_result(None)
            delay = await self.release(CONFIRM)
            self.network.mode = "http"
            delay.set_result(None)
            delay = await self.release(value)
            self.assert_withheld()
        self.assertEqual(published, [False])  # only the original retirement
        self.assertEqual(self.counters()["authority_restorations"], 0)
        delay.cancel()

    # -- schedule bounds ---------------------------------------------------

    def test_backoff_and_confirmation_bounds_hold_for_extreme_jitter(self):
        core = CoreRuntime()
        for sample in (0.0, 1.0, -5, 7, "x", None):
            with self.subTest(sample=sample):
                core._backoff_level = 0
                core._recovery_active = False
                core._recovery_confirmations = 0
                delays = [core._schedule_recovery_attempt(
                    base=CORE_RECOVERY_RETRY_BASE_SECONDS, cap=CORE_RECOVERY_RETRY_CAP_SECONDS,
                    confirmation=CORE_RECOVERY_CONFIRMATION_SECONDS, sample=sample)
                    for _ in range(12)]
                low = CORE_RECOVERY_RETRY_BASE_SECONDS * (1 - CORE_RECOVERY_RETRY_JITTER)
                self.assertGreaterEqual(delays[0], low - 1e-9)
                self.assertTrue(all(0 < value <= CORE_RECOVERY_RETRY_CAP_SECONDS for value in delays))
                self.assertEqual(delays[-1], delays[-2])
                core._recovery_active = True
                core._recovery_confirmations = 1
                core._flap_count = 50
                confirmation = core._schedule_recovery_attempt(
                    base=CORE_RECOVERY_RETRY_BASE_SECONDS, cap=CORE_RECOVERY_RETRY_CAP_SECONDS,
                    confirmation=CORE_RECOVERY_CONFIRMATION_SECONDS, sample=sample)
                self.assertLessEqual(confirmation, CORE_RECOVERY_RETRY_CAP_SECONDS)
                self.assertEqual(core.health_projection()["recovery"]["recovery_state"], "confirming")
                core._flap_count = 0

    async def test_supervise_rejects_non_positive_recovery_delays(self):
        for kwargs in ({"retry_base_seconds": 0}, {"retry_cap_seconds": -1},
                       {"confirmation_seconds": 0}):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                await self.core.supervise(sleep=self.clock.sleep, **kwargs)

    # -- C: dependents resume after restoration ----------------------------

    async def test_catalog_and_yaml_reader_resume_after_transient(self):
        await self.admit()
        gateway, transport = await yaml_tests.Harness.gateway(
            self, "8.5.0", mode="A", core_runtime=self.core)
        server = gateway.synthetic_server
        tool = yaml_tests.TOOL
        self.assertIn(tool, yaml_tests.Harness.exposed(gateway))
        baseline = set(yaml_tests.Harness.exposed(gateway))
        gateway_wakes = []
        self.core.register_reconciliation_listener(lambda: gateway_wakes.append(True))
        result = await yaml_tests.call(gateway, tool, {"yaml_path": "recorder"})
        self.assertTrue(result["success"], result)
        sent = len(transport.sent)

        with self.assertLogs(LOGGER, level="INFO") as logs:
            self.induce("connection")
            delay = await self.release(CORE_RECOVERY_RETRY_BASE_SECONDS)
            self.assertEqual(len(gateway_wakes), 1)
            await gateway.initialize(server)  # the gateway's re-enumeration
            health = gateway.health_snapshot()
            withheld = {item["tool"] for item in health["core_withheld_tools"]}
            self.assertIn(tool, withheld)
            self.assertEqual(health["optional_tools"][tool], "withheld:core_unavailable")
            self.assertNotIn(tool, yaml_tests.Harness.exposed(gateway))
            self.assertEqual(len(transport.sent), sent)  # zero dispatch while withheld
            withdrawals = self.counters()["catalog_withdrawals"]
            self.assertEqual(withdrawals, len(withheld))
            self.network.mode = "ok"
            delay.set_result(None)
            (await self.release(CONFIRM)).set_result(None)
            await self.until(lambda: self.core.initialized)
            self.assertEqual(len(gateway_wakes), 2)
            await gateway.initialize(server)
        self.assertEqual(set(yaml_tests.Harness.exposed(gateway)), baseline)
        self.assertEqual(gateway.health_snapshot()["core_withheld_tools"], [])
        self.assertEqual(self.counters()["catalog_restorations"], withdrawals)
        withdrawn = _events(logs.records, "core_catalog_tools_withdrawn")
        restored = _events(logs.records, "core_catalog_tools_restored")
        self.assertEqual([item.safe_context["count"] for item in withdrawn], [withdrawals])
        self.assertEqual([item.safe_context["count"] for item in restored], [withdrawals])
        result = await yaml_tests.call(gateway, tool, {"yaml_path": "recorder"})
        self.assertTrue(result["success"], result)
        self.assertEqual(len(transport.sent), sent + 1)
        self.assertEqual(gateway.health_snapshot()["fallback_count"], 0)
        self.assert_logs_safe(logs.records)
        self.assert_settled()

    async def test_dependency_index_refuses_during_outage_and_resumes(self):
        await self.admit()
        dep_network = dependency_tests._Network()
        dep_network.payloads["/config"] = {"version": "2026.9.1"}
        dependency = DependencyAnalysisRuntime()
        dependency.configure(HomeAssistantRestClient(self.settings),
                             HomeAssistantWebSocketClient(self.settings),
                             core_runtime=self.core)
        self.addAsyncCleanup(dependency.shutdown)
        index = dependency.require().index

        async def dependency_read():
            with patch("ha_mcp_engineering.clients.rest.aiohttp.ClientSession",
                       side_effect=dep_network.session):
                return await index.get()

        original, _, _ = await dependency_read()
        self.induce("timeout")
        delay = await self.release(CORE_RECOVERY_RETRY_BASE_SECONDS)
        self.assertTrue(index.invalidated)
        calls = len(dep_network.calls)
        with self.assertRaises(HomeAssistantUnavailableError):
            await dependency_read()
        self.assertEqual(len(dep_network.calls), calls)
        self.network.mode = "ok"
        delay.set_result(None)
        confirming = await self.release(CONFIRM)
        with self.assertRaises(HomeAssistantUnavailableError):
            await dependency_read()  # still refused while confirming
        self.assertEqual(len(dep_network.calls), calls)
        confirming.set_result(None)
        await self.until(lambda: self.core.initialized)
        replacement, _, _ = await dependency_read()
        self.assertGreater(replacement.generation, original.generation)
        self.assertTrue(index.active_identity()["current"])
        self.assert_authority_round_trip()


if __name__ == "__main__":
    unittest.main()
