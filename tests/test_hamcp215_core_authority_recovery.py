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
from tests import test_core_update_continuity as continuity_tests
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
from tests.support.core_gate import reconcile_through_gate


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
# Every production on-demand caller, plus unknown names (health or tool probes)
# and the boot triggers once the boot window has closed. None may confirm.
ON_DEMAND = (
    "mutation_pre_dispatch", "mutation_verification", "configuration_reverification",
    "fan_authority", "health_probe", "tool_call", "startup", "startup_retry",
)
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


class _Hold:
    """Suspend selected calls of ``owner.<name>`` until each is released.

    Calls are counted from installation. ``after`` suspends once the original
    call has returned instead of before it runs.
    """

    def __init__(self, owner, name, *calls, after=False):
        self.entered = {call: asyncio.Event() for call in calls or (1,)}
        self.released = {call: asyncio.Event() for call in self.entered}
        original = getattr(owner, name)
        count = 0

        async def held(*args, **kwargs):
            nonlocal count
            count += 1
            call = count
            if call in self.entered and not after:
                self.entered[call].set()
                await self.released[call].wait()
            result = await original(*args, **kwargs)
            if call in self.entered and after:
                self.entered[call].set()
                await self.released[call].wait()
            return result

        setattr(owner, name, held)

    async def wait(self, call=1):
        await asyncio.wait_for(self.entered[call].wait(), 2)

    def release(self, call=1):
        self.released[call].set()


class BoundedRecovery(unittest.IsolatedAsyncioTestCase):
    """R1 (review of 2ad04a3): each recovery attempt is scheduled promptly.

    Adopted from the reviewer's bounded check: a supervisor that stopped
    scheduling bounded attempts fails here by assertion within one second,
    not by exhausting a resource.
    """

    async def test_background_recovery_within_required_attempts(self):
        source = _FlakySource(core_tests._core_2026_9_snapshot())
        core = CoreRuntime()
        core.configure(object(), source=source)
        await core.reconcile_until_initialized()
        waits = asyncio.Queue()

        async def sleep(delay):
            ready = asyncio.get_running_loop().create_future()
            await waits.put((delay, ready))
            await ready

        source.fail = True
        core.request_reconciliation()
        supervisor = asyncio.create_task(core.supervise(sleep=sleep, jitter=lambda: 0.5))
        try:
            for _ in range(CORE_RECOVERY_CONFIRMATIONS):
                try:
                    _delay, ready = await asyncio.wait_for(waits.get(), 1)
                except TimeoutError:
                    self.fail("background recovery did not schedule its next bounded attempt")
                self.assertIsNone(core.acquire(CAPABILITY))
                source.fail = False
                ready.set_result(None)
            for _ in range(100):
                await asyncio.sleep(0)
                if core.route_status(CAPABILITY)["available"]:
                    break
            self.assertTrue(core.route_status(CAPABILITY)["available"])
            self.assertEqual(core.health_snapshot()["counters"]["recovery_confirmations"],
                             CORE_RECOVERY_CONFIRMATIONS)
        finally:
            supervisor.cancel()
            await asyncio.gather(supervisor, return_exceptions=True)


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
                         (2, 2, 1, 1))  # both accepted confirmations
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

    async def test_startup_failure_is_logged_paced_and_gated(self):
        self.network.mode = "connection"
        with self.assertLogs(LOGGER, level="INFO") as logs:
            self.start_supervisor()
            delay = await self.release(CORE_RECOVERY_RETRY_BASE_SECONDS)
        failed = _events(logs.records, "core_authority_verification_failed")
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].safe_context["detail"], "core_probe_connection_failed")
        self.assertEqual(_events(logs.records, "core_authority_retired"), [])  # nothing published
        self.assertEqual(self.recovery()["recovery_state"], "backing_off")
        # Never initialized after a failed first attempt is not first admission
        # any more: it passes the same restoration gate.
        await self.recover_through_confirmation(None, delay=delay)
        self.assertEqual(self.recovery()["recovery_state"], "restored")
        self.assertEqual(self.counters()["recovery_confirmations"], CORE_RECOVERY_CONFIRMATIONS)
        self.assert_authority_round_trip()

    async def test_boot_sequence_first_admission_is_not_gated(self):
        # The production boot (application.py) of a new runtime admits on its
        # first complete verification: the one-shot boot exception.
        sleeps = []
        snapshot = await self.core.reconcile_until_initialized(
            retry_delays=(0.25, 1.0), sleep=lambda delay: sleeps.append(delay))
        self.assertTrue(snapshot["initialized"])
        self.assertEqual(sleeps, [])
        self.assertEqual((self.counters()["recovery_confirmations"],
                          self.counters()["authority_restorations"]), (0, 0))
        self.assertTrue(self.core._boot_exception_spent)
        self.assert_authority_round_trip()

    async def test_failed_boot_attempt_spends_the_boot_exception(self):
        # H1 (review of a6e66a8): a failed verification spends the exception
        # even inside the boot sequence; no boot retry can then admit, and the
        # supervisor starts on recovery pacing instead of the 300 s interval.
        self.network.mode = "connection"
        sleeps = []

        async def boot_sleep(delay):
            sleeps.append(delay)
            self.network.mode = "ok"

        snapshot = await self.core.reconcile_until_initialized(
            retry_delays=(0.25, 1.0), sleep=boot_sleep)
        self.assertFalse(snapshot["initialized"])
        self.assertEqual(sleeps, [])  # a futile boot retry is not attempted
        self.network.mode = "ok"
        for trigger in ("startup", "startup_retry", *ON_DEMAND):
            await self.core.reconcile_once(trigger)
            self.assertIsNone(self.core.acquire(CAPABILITY), trigger)
        self.assertEqual(self.recovery()["confirmations"], 0)
        self.supervisor = asyncio.create_task(self.core.supervise(
            interval_seconds=300, sleep=self.clock.sleep, jitter=lambda: 0.5))
        await self.recover_through_confirmation(CORE_RECOVERY_RETRY_BASE_SECONDS)
        self.assertEqual(self.counters()["recovery_confirmations"], CORE_RECOVERY_CONFIRMATIONS)
        self.assert_authority_round_trip()

    async def test_boot_sequence_end_spends_the_exception_without_an_outcome(self):
        # Every boot attempt can stop at an early fence (a stale connection
        # epoch, a registry move during the probe) without reaching a
        # verification outcome. The end of the boot sequence still spends the
        # one-shot exception, so the first background success is gated.
        source = _FlakySource(core_tests._core_2026_9_snapshot())
        core = CoreRuntime()
        core.configure(object(), source=source)
        triggers = []

        async def early_fence(trigger, _stamp):
            triggers.append(trigger)
            return {"identity_agreement": False, "initialized": False}

        core._reconcile = early_fence
        await core.reconcile_until_initialized(
            retry_delays=(0.25,), sleep=lambda _delay: asyncio.sleep(0))
        del core._reconcile
        self.assertEqual(triggers, ["startup", "startup_retry"])
        self.assertTrue(core._boot_exception_spent)
        await core.reconcile_once("periodic")
        self.assertIsNone(core.acquire(CAPABILITY))
        self.assertEqual(core.health_projection()["recovery"]["confirmations"], 1)
        for _ in range(CORE_RECOVERY_CONFIRMATIONS - 1):
            await core.reconcile_once("periodic")
        self.assertTrue(core.release(core.acquire(CAPABILITY)))

    async def test_failed_boot_sequence_leaves_never_initialized_gated(self):
        self.network.mode = "connection"
        snapshot = await self.core.reconcile_until_initialized(
            retry_delays=(0.25,), sleep=lambda _delay: asyncio.sleep(0))
        self.assertFalse(snapshot["initialized"])
        self.network.mode = "ok"
        for trigger in ON_DEMAND:
            await self.core.reconcile_once(trigger)
            self.assertIsNone(self.core.acquire(CAPABILITY), trigger)
        await self.core.reconcile_once("periodic")
        self.assertIsNone(self.core.acquire(CAPABILITY))
        self.assertEqual(self.recovery()["confirmations"], 1)
        await self.core.reconcile_once("periodic")
        self.assert_authority_round_trip()
        self.assertEqual(self.counters()["authority_restorations"], 1)

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
            # Compatible again: the next periodic check is only the first of
            # the gate's confirmations, spaced (capped by the interval).
            delay = await self.clock.next_delay()
            self.assertEqual(self.clock.delays, [0.05])
            self.assertEqual((self.recovery()["recovery_state"], self.recovery()["confirmations"]),
                             ("confirming", 1))
            self.assertIsNone(self.core.acquire(CAPABILITY))
            delay.set_result(None)
            await self.until(lambda: self.recovery()["recovery_state"] == "restored")
        self.assertIs(self.core._authority_provider, original)
        retired = _events(logs.records, "core_authority_retired")
        self.assertEqual(len(retired), 1)
        self.assertEqual(retired[0].safe_context["reason"], "core_authority_incompatible")
        restored = _events(logs.records, "core_authority_restored")
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].safe_context["verified_confirmations"],
                         CORE_RECOVERY_CONFIRMATIONS)
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
        # The failed probe restarts the count as a failure, not a key change.
        self.assertEqual(len(_events(logs.records, "core_authority_verification_failed")), 2)
        self.assertEqual(_events(logs.records, "core_authority_recovery_reset"), [])
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
        accepted = self.counters()["recovery_confirmations"]
        scheduled = self.recovery()["next_recovery_attempt_at"]
        for trigger in ON_DEMAND:
            snapshot = await self.core.reconcile_once(trigger)
            self.assertFalse(snapshot["initialized"], trigger)
            self.assertEqual(self.recovery()["confirmations"], 1, trigger)
            self.assertEqual(self.recovery()["next_recovery_attempt_at"], scheduled, trigger)
            self.assertIsNone(self.core.acquire(CAPABILITY))
        # L1: refused on-demand calls are counted apart, never as confirmations.
        self.assertEqual(self.counters()["recovery_confirmations"], accepted)
        self.assertEqual(self.counters()["recovery_on_demand_refusals"], len(ON_DEMAND))
        delay.set_result(None)
        await self.until(lambda: self.core.initialized)
        self.assertEqual(self.counters()["authority_restorations"], 1)
        self.assertEqual(self.counters()["recovery_confirmations"], accepted + 1)

    # -- H1: one restoration gate for every non-admitted disposition ------
    # Reviewer reproductions (review of 4e677ec), through the production
    # aiohttp collector and lifecycle watcher.

    async def boot(self):
        await self.core.reconcile_until_initialized(retry_delays=())
        self.assertIsNotNone(self.core._connection_monitor_epoch)
        self.assert_authority_round_trip()

    async def test_reviewer_incompatible_contract_cannot_restore_on_demand(self):
        await self.boot()
        with patch.object(self.core, "_authority_provider", return_value=()):
            await self.core.reconcile_once("periodic")
        self.assertEqual(self.recovery()["recovery_state"], "incompatible")
        self.assertIsNone(self.core.acquire(CAPABILITY))
        await self.core.reconcile_once("mutation_pre_dispatch")
        self.assertIsNone(self.core.acquire(CAPABILITY),
                          "one on-demand verification restored incompatible authority")
        self.assertEqual(self.counters()["authority_restorations"], 0)

    async def test_reviewer_incompatible_contract_cannot_restore_after_one_periodic(self):
        await self.boot()
        with patch.object(self.core, "_authority_provider", return_value=()):
            await self.core.reconcile_once("periodic")
        await self.core.reconcile_once("periodic")
        self.assertIsNone(self.core.acquire(CAPABILITY),
                          "one periodic verification restored incompatible authority")
        self.assertEqual(self.recovery()["confirmations"], 1)
        await self.core.reconcile_once("periodic")
        self.assert_authority_round_trip()

    async def test_reviewer_unreviewed_version_then_on_demand_through_watcher(self):
        await self.boot()
        self.network.version = "2099.1.0"
        await self.core.reconcile_once("periodic")
        self.assertIsNone(self.core.acquire(CAPABILITY))
        self.assertEqual(self.recovery()["recovery_state"], "incompatible")
        self.network.version = "2026.9.1"
        await self.core.reconcile_once("mutation_pre_dispatch")
        self.assertIsNone(self.core.acquire(CAPABILITY),
                          "restored by one on-demand verification through the watcher")
        self.assertEqual(self.core._connection_monitor_epoch, self.core._connection_epoch)
        for count in range(1, CORE_RECOVERY_CONFIRMATIONS):
            await self.core.reconcile_once("periodic")
            self.assertEqual(self.recovery()["confirmations"], count)
            self.assertIsNone(self.core.acquire(CAPABILITY))
        await self.core.reconcile_once("periodic")
        self.assert_authority_round_trip()

    async def test_reviewer_on_demand_does_not_increment_confirmation_counter(self):
        await self.boot()
        self.network.mode = "timeout"
        await self.core.reconcile_once("periodic")
        self.network.mode = "ok"
        await self.core.reconcile_once("failure_retry")
        before = self.counters()["recovery_confirmations"]
        self.assertEqual(before, 1)
        for _ in range(3):
            await self.core.reconcile_once("mutation_pre_dispatch")
            self.assertIsNone(self.core.acquire(CAPABILITY))
        self.assertEqual(self.counters()["recovery_confirmations"], before)
        self.assertEqual(self.counters()["recovery_on_demand_refusals"], 3)
        self.assertEqual(self.recovery()["confirmations"], 1)

    async def test_reviewer_partial_and_stale_observations_never_restore(self):
        await self.boot()
        self.core.request_reconciliation(connection_changed=True)
        for mode in ("http", "auth", "connection", "monitor_auth"):
            self.network.mode = mode
            await self.core.reconcile_once("failure_retry")
            self.assertIsNone(self.core.acquire(CAPABILITY), mode)
            self.assertEqual(self.recovery()["confirmations"], 0, mode)
        self.network.mode = "ok"
        original = self.core._collector.collect

        async def moved():
            observation = await original()
            self.core.request_reconciliation(connection_changed=True)
            return observation

        with patch.object(self.core._collector, "collect", side_effect=moved):
            await self.core.reconcile_once("failure_retry")
        self.assertIsNone(self.core.acquire(CAPABILITY))
        self.assertEqual(self.recovery()["confirmations"], 0)

    async def induce_disposition(self, disposition):
        """Withdraw admitted authority (or never admit); return the restorer.

        Returns the confirmations already accepted by the inducing attempt.
        """
        if disposition == "never_initialized":
            self.network.mode = "connection"
            await self.core.reconcile_until_initialized(
                retry_delays=(0.25,), sleep=lambda _delay: asyncio.sleep(0))
            self.network.mode = "ok"
            return 0
        await self.boot()
        if disposition == "transient":
            self.network.mode = "http"
            await self.core.reconcile_once("periodic")
            self.network.mode = "ok"
        elif disposition == "socket_moved":
            self.network.monitors[-1].disconnect.set()
            await self.until(lambda: self.counters()["retirements"] == 1)
        elif disposition == "incompatible":
            with patch.object(self.core, "_authority_provider", return_value=()):
                await self.core.reconcile_once("periodic")
            self.assertEqual(self.recovery()["recovery_state"], "incompatible")
        elif disposition == "unreviewed_version":
            self.network.version = "2099.1.0"
            await self.core.reconcile_once("periodic")
            self.assertEqual(self.recovery()["recovery_state"], "incompatible")
            self.network.version = "2026.9.1"
        elif disposition == "unavailable":
            # A complete-identity probe whose evidence admits nothing.
            with patch.object(self.core._coordinator, "preview_admitted", return_value=()), \
                    patch.object(self.core, "_authority_provider", return_value=()):
                await self.core.reconcile_once("periodic")
        elif disposition == "version_changed":
            # A different reviewed Core version observed by a background check
            # retires the old generation; that same check is confirmation one.
            self.network.version = "2026.9.0"
            await self.core.reconcile_once("periodic")
            return 1
        else:  # pragma: no cover - the matrix names every disposition
            raise AssertionError(disposition)
        return 0

    async def test_every_disposition_needs_full_background_confirmation(self):
        dispositions = ("transient", "socket_moved", "incompatible", "unreviewed_version",
                        "unavailable", "never_initialized", "version_changed")
        for disposition in dispositions:
            for pattern in ("background", "on_demand", "mixed"):
                with self.subTest(disposition=disposition, pattern=pattern):
                    await self.fresh_core()
                    self.network.version = "2026.9.1"
                    seeded = await self.induce_disposition(disposition)
                    self.assertIsNone(self.core.acquire(CAPABILITY))
                    if disposition != "never_initialized":
                        # Every withdrawal of admitted authority arms recovery.
                        self.assertIsNotNone(self.recovery()["retired_at"])
                    self.assertEqual(self.counters()["authority_restorations"], 0)
                    accepted = self.counters()["recovery_confirmations"]
                    if pattern in ("on_demand", "mixed"):
                        for trigger in ON_DEMAND:
                            await self.core.reconcile_once(trigger)
                            self.assertIsNone(self.core.acquire(CAPABILITY), trigger)
                    self.assertEqual(self.counters()["recovery_confirmations"], accepted)
                    if pattern == "on_demand":
                        self.assertEqual(self.recovery()["confirmations"], seeded)
                        self.assertEqual(self.counters()["authority_restorations"], 0)
                        continue
                    for count in range(seeded + 1, CORE_RECOVERY_CONFIRMATIONS):
                        await self.core.reconcile_once("periodic")
                        self.assertEqual(self.recovery()["confirmations"], count)
                        self.assertIsNone(self.core.acquire(CAPABILITY))
                        if pattern == "mixed":
                            for trigger in ON_DEMAND:
                                await self.core.reconcile_once(trigger)
                                self.assertIsNone(self.core.acquire(CAPABILITY), trigger)
                                self.assertEqual(self.recovery()["confirmations"], count)
                    await self.core.reconcile_once("periodic")
                    self.assert_authority_round_trip()
                    self.assertEqual(self.counters()["authority_restorations"], 1)
                    self.assertEqual(self.counters()["recovery_confirmations"],
                                     CORE_RECOVERY_CONFIRMATIONS)

    async def test_pristine_runtime_never_admits_on_demand(self):
        for trigger in ON_DEMAND[:6]:
            with self.subTest(trigger=trigger):
                await self.fresh_core()
                await self.core.reconcile_once(trigger)
                self.assertIsNone(self.core.acquire(CAPABILITY))
                await self.core.reconcile_once("periodic")
                self.assertIsNone(self.core.acquire(CAPABILITY))  # first of two
                await self.core.reconcile_once("periodic")
                self.assert_authority_round_trip()

    # -- H1 (review of a6e66a8): the boot exception is one-shot -----------
    # Reviewer reproductions: re-initializing the same runtime, through the
    # application factory or configure, must never reopen first admission.

    async def withdraw(self):
        self.network.mode = "connection"
        await self.core.reconcile_once("periodic")
        self.assertIsNone(self.core.acquire(CAPABILITY))
        self.network.mode = "ok"

    async def assert_reinit_needs_background_confirmations(
        self, reinit, required=CORE_RECOVERY_CONFIRMATIONS
    ):
        """Re-initialize, run the boot sequence, then require the full gate."""
        previous_monitor = self.core._connection_monitor_task
        was_admitted = self.core.initialized
        await reinit()
        if was_admitted:
            # Discarding admitted authority is a withdrawal, visible in health.
            recovery = self.recovery()
            self.assertEqual(recovery["recovery_state"], "retrying")
            self.assertIsNotNone(recovery["retired_at"])
        if previous_monitor is not None:
            # configure() stops the previous lifecycle watcher (H1, review of
            # 2ad04a3); wait until its socket has closed.
            await asyncio.wait_for(asyncio.gather(previous_monitor, return_exceptions=True), 2)
            self.assertTrue(previous_monitor.done())
        self.assertTrue(self.core._boot_exception_spent)
        await self.core.reconcile_until_initialized()
        self.assertIsNone(self.core.acquire(CAPABILITY), "re-init reopened first admission")
        recovery = self.recovery()
        self.assertEqual((recovery["confirmations"], recovery["confirmations_required"]),
                         (0, required))
        for trigger in ON_DEMAND:
            await self.core.reconcile_once(trigger)
            self.assertIsNone(self.core.acquire(CAPABILITY), trigger)
        for count in range(1, required):
            await self.core.reconcile_once("periodic")
            self.assertIsNone(self.core.acquire(CAPABILITY), count)
            self.assertEqual(self.recovery()["confirmations"], count)
        await self.core.reconcile_once("periodic")
        self.assert_authority_round_trip()
        self.assertEqual(self.counters()["recovery_confirmations"], required)
        # configure() zeroes the counters, as in base.
        self.assertEqual(self.counters()["authority_restorations"], 1)

    def configure_again(self):
        async def reinit():
            self.core.configure(self.settings, source=AiohttpCoreSnapshotSource(self.settings))
        return reinit

    async def test_reviewer_application_reinit_cannot_reopen_boot_exception(self):
        from dataclasses import replace
        import tempfile
        from ha_mcp_engineering import application

        await self.boot()
        await self.withdraw()
        temp = tempfile.TemporaryDirectory(prefix="hamcp215-reinit-")
        self.addCleanup(temp.cleanup)
        settings = replace(self.settings, audit_path=temp.name + "/audit.jsonl",
                           governance_path=temp.name + "/governance")
        identity = id(self.core)

        async def reinit():
            with patch.object(application, "CORE_READMISSION", self.core):
                application.create_application(
                    settings, core_snapshot_source=AiohttpCoreSnapshotSource(settings))
            self.assertEqual(id(self.core), identity)  # the same runtime object

        await self.assert_reinit_needs_background_confirmations(reinit)

    async def test_reviewer_configure_after_retirement_cannot_reopen_boot_exception(self):
        await self.boot()
        await self.withdraw()
        await self.assert_reinit_needs_background_confirmations(self.configure_again())

    async def test_reinit_after_normal_admission_passes_the_gate(self):
        await self.boot()
        await self.assert_reinit_needs_background_confirmations(self.configure_again())

    async def test_any_admission_spends_the_boot_exception(self):
        # Admission itself consumes the latch, not only the end of the boot
        # sequence: a lone startup verification that admits cannot be followed
        # by a second first admission after a re-init.
        await self.core.reconcile_once("startup")
        self.assert_authority_round_trip()
        self.assertTrue(self.core._boot_exception_spent)
        await self.assert_reinit_needs_background_confirmations(self.configure_again())

    async def test_reinit_before_any_reconciliation_keeps_the_boot_exception(self):
        # Positive control: configuring twice before any verification is still
        # the runtime's first boot.
        self.core.configure(self.settings, source=AiohttpCoreSnapshotSource(self.settings))
        self.assertFalse(self.core._boot_exception_spent)
        await self.boot()
        self.assertEqual(self.counters()["recovery_confirmations"], 0)

    async def test_repeated_reinits_each_pass_the_gate(self):
        await self.boot()
        for round_ in range(3):
            with self.subTest(round=round_):
                if round_ == 1:
                    await self.withdraw()
                await self.assert_reinit_needs_background_confirmations(
                    self.configure_again(),
                    # A re-init within the flap window of a restoration is a flap.
                    required=(CORE_RECOVERY_CONFIRMATIONS if round_ == 0
                              else CORE_RECOVERY_FLAP_CONFIRMATIONS),
                )

    async def test_repeat_boot_without_configure_cannot_restore(self):
        await self.boot()
        await self.withdraw()
        await self.core.reconcile_until_initialized(
            retry_delays=(), sleep=lambda _delay: asyncio.sleep(0))
        self.assertIsNone(self.core.acquire(CAPABILITY))
        self.assertEqual(self.recovery()["confirmations"], 0)

    async def test_reinit_starts_supervisor_on_recovery_pacing(self):
        await self.boot()
        previous_monitor = self.core._connection_monitor_task
        await self.configure_again()()
        previous_monitor.cancel()
        await asyncio.gather(previous_monitor, return_exceptions=True)
        await self.core.reconcile_until_initialized()
        self.assertIsNone(self.core.acquire(CAPABILITY))
        self.supervisor = asyncio.create_task(self.core.supervise(
            interval_seconds=300, sleep=self.clock.sleep, jitter=lambda: 0.5))
        await self.recover_through_confirmation(CORE_RECOVERY_RETRY_BASE_SECONDS)
        self.assert_authority_round_trip()

    # -- H1 (review of 2ad04a3): superseded in-flight work is discarded -----
    # A verification records its runtime generation, configuration owner and
    # boot-exemption availability when it starts; publication re-checks all
    # three under the runtime lock, and every resume point re-checks the
    # first two. Superseded work is discarded at INFO and counts as nothing.
    # Every case ends with the current configuration restoring only through
    # its own complete background streak.

    async def rebuild(self, *, through_application=False):
        """Re-initialize the same runtime; configure stops its old watcher."""
        previous = self.core._connection_monitor_task
        if through_application:
            from dataclasses import replace
            import tempfile
            from ha_mcp_engineering import application

            temp = tempfile.TemporaryDirectory(prefix="hamcp215-rebuild-")
            self.addCleanup(temp.cleanup)
            settings = replace(self.settings, audit_path=temp.name + "/audit.jsonl",
                               governance_path=temp.name + "/governance")
            with patch.object(application, "CORE_READMISSION", self.core):
                application.create_application(
                    settings, core_snapshot_source=AiohttpCoreSnapshotSource(settings))
        else:
            self.core.configure(self.settings, source=AiohttpCoreSnapshotSource(self.settings))
        if previous is not None:
            # Would time out if configure had only forgotten the watcher.
            await asyncio.wait_for(asyncio.gather(previous, return_exceptions=True), 2)
            self.assertTrue(previous.done())

    async def finish_stale(self, release, tasks, phases, *, reason=None, withheld=True):
        """Let superseded work resume; it is discarded and changes nothing."""
        counters = self.counters()
        discards = counters.pop("stale_work_discards")
        recovery = self.recovery()
        generation = self.core.health_snapshot()["current_generation"]
        with self.assertLogs(LOGGER, level="INFO") as logs:
            release()
            await asyncio.wait_for(asyncio.gather(*tasks), 5)
        after = self.counters()
        self.assertEqual(after.pop("stale_work_discards"), discards + len(phases))
        self.assertEqual(after, counters)
        self.assertEqual(self.recovery(), recovery)
        self.assertEqual(self.core.health_snapshot()["current_generation"], generation)
        if withheld:
            self.assertIsNone(self.core.acquire(CAPABILITY), "superseded work published")
        discarded = _events(logs.records, "core_authority_stale_work_discarded")
        self.assertEqual(sorted(record.safe_context["phase"] for record in discarded),
                         sorted(phases))
        for record in discarded:
            self.assertEqual(record.levelno, logging.INFO)
            self.assertIn(record.safe_context["reason"], {
                "core_configuration_replaced", "core_runtime_generation_replaced",
                "core_boot_exemption_spent",
            } if reason is None else {reason})
        for event in ("core_authority_verification_failed", "core_authority_restored",
                      "core_authority_retired", "core_authority_recovery_confirming",
                      "core_authority_recovery_attempt"):
            self.assertEqual(_events(logs.records, event), [], event)
        self.assert_logs_safe(logs.records)

    async def assert_current_streak(self, required=CORE_RECOVERY_CONFIRMATIONS):
        """Only the current configuration's background verifications restore."""
        self.assertEqual(self.recovery()["confirmations_required"], required)
        self.assertEqual(self.counters()["recovery_confirmations"], 0)
        for trigger in ON_DEMAND:
            await self.core.reconcile_once(trigger)
            self.assertIsNone(self.core.acquire(CAPABILITY), trigger)
        for count in range(1, required):
            await self.core.reconcile_once("periodic")
            self.assertIsNone(self.core.acquire(CAPABILITY), count)
            self.assertEqual(self.recovery()["confirmations"], count)
        await self.core.reconcile_once("periodic")
        self.assert_authority_round_trip()
        self.assertEqual(self.counters()["recovery_confirmations"], required)
        self.assertEqual(self.counters()["authority_restorations"], 1)

    async def withdraw_by_rebuild_if_admitted(self):
        """Leave the current configuration withdrawn with the latch spent."""
        authority = self.core.acquire(CAPABILITY)
        if authority is not None:
            self.core.release(authority)
            await self.rebuild()
        self.assertTrue(self.core._boot_exception_spent)
        self.assertIsNone(self.core.acquire(CAPABILITY))

    async def test_reviewer_suspended_boot_cannot_cross_a_rebuild(self):
        # Reviewer production-path reproduction (review of 2ad04a3), through
        # the application factory and through configure: a startup
        # verification suspended in its snapshot capture outlives two
        # rebuilds, the second of which withdrew a later boot's admission.
        for through_application in (True, False):
            with self.subTest(through_application=through_application):
                await self.fresh_core()
                hold = _Hold(self.core._source, "capture_core_snapshot", 1)
                pending = asyncio.create_task(self.core.reconcile_once("startup"))
                await hold.wait()
                await self.rebuild(through_application=through_application)
                await self.core.reconcile_until_initialized()
                self.assert_authority_round_trip()  # the new boot's own admission
                await self.rebuild(through_application=through_application)
                self.assertIsNone(self.core.acquire(CAPABILITY))
                self.assertTrue(self.core._boot_exception_spent)
                await self.finish_stale(hold.release, [pending], ["observation"],
                                        reason="core_configuration_replaced")
                await self.core.reconcile_until_initialized()
                await self.assert_current_streak()

    async def test_superseded_work_suspended_at_each_await_is_discarded(self):
        # Every resume point of a verification, for a boot, a background and
        # an on-demand trigger. ``lock`` queues the work behind a held boot.
        def capture(call):
            return lambda core: (core._source, "capture_core_snapshot", (call,), False)

        points = {
            "reconciliation_lock": (capture(1), ["observation", "reconciliation_lock"]),
            "registry_refresh": (
                lambda core: (core._release_registry, "refresh_if_due", (1,), False),
                ["registry_refresh"]),
            "observation": (capture(1), ["observation"]),
            "monitor_attachment": (
                lambda core: (core, "_ensure_connection_monitor", (1,), True),
                ["monitor_attachment"]),
            "monitor_recollection": (capture(3), ["observation"]),
        }
        for point, (target, phases) in points.items():
            for trigger in ("startup", "periodic", "failure_retry", "mutation_pre_dispatch"):
                with self.subTest(point=point, trigger=trigger):
                    await self.fresh_core()
                    owner, name, calls, after = target(self.core)
                    hold = _Hold(owner, name, *calls, after=after)
                    tasks = []
                    if point == "reconciliation_lock":
                        tasks.append(asyncio.create_task(self.core.reconcile_once("startup")))
                    tasks.append(asyncio.create_task(self.core.reconcile_once(trigger)))
                    await hold.wait(calls[0])
                    for _ in range(3):
                        await asyncio.sleep(0)  # any queued work is stamped
                    await self.rebuild()
                    await self.core.reconcile_until_initialized()
                    await self.withdraw_by_rebuild_if_admitted()
                    await self.finish_stale(lambda: hold.release(calls[0]), tasks, phases)
                    await self.core.reconcile_until_initialized()
                    await self.assert_current_streak()

    async def test_rebuild_at_publication_discards_the_verification(self):
        # No await separates authority selection from publication, so only
        # the publication re-check sees a rebuild made from inside it.
        for trigger in ("startup", "periodic", "mutation_pre_dispatch"):
            with self.subTest(trigger=trigger):
                await self.fresh_core()
                previous = []
                select = self.core._authority_provider

                def rebuilding_selection(version):
                    previous.append(self.core._connection_monitor_task)
                    self.core.configure(
                        self.settings, source=AiohttpCoreSnapshotSource(self.settings))
                    return select(version)

                self.core._authority_provider = rebuilding_selection
                with self.assertLogs(LOGGER, level="INFO") as logs:
                    await self.core.reconcile_once(trigger)
                await asyncio.wait_for(asyncio.gather(*previous, return_exceptions=True), 2)
                counters = self.counters()
                self.assertEqual(counters.pop("stale_work_discards"), 1)
                self.assertEqual(set(counters.values()), {0}, counters)
                self.assertIsNone(self.core.acquire(CAPABILITY))
                [record] = _events(logs.records, "core_authority_stale_work_discarded")
                self.assertEqual((record.safe_context["phase"], record.safe_context["reason"]),
                                 ("publication", "core_configuration_replaced"))
                self.assertEqual(_events(logs.records, "core_authority_verification_failed"), [])
                if trigger == "mutation_pre_dispatch":
                    # An on-demand attempt spends the exemption when it starts.
                    await self.core.reconcile_until_initialized()
                    await self.assert_current_streak()
                else:
                    # Positive control: the exemption the superseded attempt
                    # could not use still admits the current first boot.
                    self.assertFalse(self.core._boot_exception_spent)
                    await self.boot()
                    self.assertEqual(self.counters()["recovery_confirmations"], 0)

    async def test_stale_work_from_several_generations_is_all_discarded(self):
        tasks, holds = [], []
        for trigger in ("startup", "periodic", "mutation_pre_dispatch", "failure_retry"):
            hold = _Hold(self.core._source, "capture_core_snapshot", 1)
            tasks.append(asyncio.create_task(self.core.reconcile_once(trigger)))
            await hold.wait()
            holds.append(hold)
            await self.rebuild()
        await self.core.reconcile_until_initialized()
        await self.withdraw_by_rebuild_if_admitted()

        def release_newest_first():
            for hold in reversed(holds):
                hold.release()

        await self.finish_stale(release_newest_first, tasks, ["observation"] * len(tasks),
                                reason="core_configuration_replaced")
        await self.assert_current_streak()

    async def test_stale_work_cannot_shorten_a_flap_restoration(self):
        await self.boot()
        await self.withdraw()
        for _ in range(CORE_RECOVERY_CONFIRMATIONS):
            await self.core.reconcile_once("periodic")
        self.assert_authority_round_trip()  # restored: the flap window is open
        hold = _Hold(self.core._source, "capture_core_snapshot", 1)
        pending = asyncio.create_task(self.core.reconcile_once("periodic"))
        await hold.wait()
        await self.rebuild()  # withdraws restored authority inside the window
        await self.core.reconcile_until_initialized()
        await self.finish_stale(hold.release, [pending], ["observation"],
                                reason="core_configuration_replaced")
        # configure() zeroed the restoration counter, as in base.
        await self.assert_current_streak(CORE_RECOVERY_FLAP_CONFIRMATIONS)

    async def test_concurrent_boot_attempt_cannot_reuse_a_spent_exemption(self):
        # Same configuration: a boot attempt queued while the exemption was
        # open must not claim it after another attempt's failure spent it.
        source = _FlakySource(core_tests._core_2026_9_snapshot())
        await self.stop_core()
        self.core = CoreRuntime()
        self.core.configure(object(), source=source)
        hold = _Hold(source, "capture_core_snapshot", 1, 2)
        first = asyncio.create_task(self.core.reconcile_once("startup"))
        await hold.wait(1)
        second = asyncio.create_task(self.core.reconcile_once("startup"))
        for _ in range(3):
            await asyncio.sleep(0)  # the second attempt is stamped and queued
        self.assertFalse(self.core._boot_exception_spent)
        source.fail = True
        hold.release(1)
        await asyncio.wait_for(first, 2)
        self.assertTrue(self.core._boot_exception_spent)  # its failure spent it
        await hold.wait(2)
        source.fail = False
        await self.finish_stale(lambda: hold.release(2), [second], ["publication"],
                                reason="core_boot_exemption_spent")
        await self.assert_current_streak()

    async def test_concurrent_healthy_boot_attempts_admit_once_without_discard(self):
        # Positive control: overlapping attempts of one configuration that
        # both see Core healthy publish normally; nothing is discarded.
        source = _FlakySource(core_tests._core_2026_9_snapshot())
        await self.stop_core()
        self.core = CoreRuntime()
        self.core.configure(object(), source=source)
        hold = _Hold(source, "capture_core_snapshot", 1)
        first = asyncio.create_task(self.core.reconcile_once("startup"))
        await hold.wait()
        second = asyncio.create_task(self.core.reconcile_once("periodic"))
        for _ in range(3):
            await asyncio.sleep(0)
        hold.release()
        await asyncio.wait_for(asyncio.gather(first, second), 2)
        self.assert_authority_round_trip()
        counters = self.counters()
        self.assertEqual((counters["stale_work_discards"], counters["recovery_confirmations"],
                          counters["verification_successes"]), (0, 0, 2))

    async def test_fresh_runtime_first_background_verification_still_admits(self):
        # Positive control: deferring the spend keeps the first background
        # verification of a never-booted runtime an ungated first admission.
        await self.core.reconcile_once("periodic")
        self.assert_authority_round_trip()
        self.assertTrue(self.core._boot_exception_spent)
        counters = self.counters()
        self.assertEqual((counters["stale_work_discards"], counters["recovery_confirmations"]),
                         (0, 0))

    async def assert_aborted_background_attempt_spent_the_exemption(self):
        """After an unpublished first background attempt, the gate applies.

        Only background triggers here: an on-demand probe would spend the
        latch itself and hide a missing deferred spend.
        """
        self.assertTrue(self.core._boot_exception_spent)
        self.assertIsNone(self.core.acquire(CAPABILITY))
        for count in range(1, CORE_RECOVERY_CONFIRMATIONS):
            await self.core.reconcile_once("periodic")
            self.assertIsNone(self.core.acquire(CAPABILITY), count)
            self.assertEqual(self.recovery()["confirmations"], count)
        await self.core.reconcile_once("periodic")
        self.assert_authority_round_trip()
        self.assertEqual(self.counters()["recovery_confirmations"],
                         CORE_RECOVERY_CONFIRMATIONS)

    async def test_cancelled_first_background_verification_spends_the_exemption(self):
        # The deferred spend also covers a background attempt that never
        # publishes: cancelled mid-capture, it still spends the exemption.
        hold = _Hold(self.core._source, "capture_core_snapshot", 1)
        pending = asyncio.create_task(self.core.reconcile_once("periodic"))
        await hold.wait()
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        self.assertTrue(pending.cancelled())
        await self.assert_aborted_background_attempt_spent_the_exemption()

    async def test_raising_first_background_verification_spends_the_exemption(self):
        # As above, for a background attempt whose collector raises.
        with patch.object(self.core._collector, "collect",
                          side_effect=RuntimeError("synthetic collector fault")):
            with self.assertRaises(RuntimeError):
                await self.core.reconcile_once("periodic")
        await self.assert_aborted_background_attempt_spent_the_exemption()

    async def test_superseded_supervisor_stops_without_touching_the_new_configuration(self):
        await self.admit()
        self.induce("connection")
        delay = await self.release(CORE_RECOVERY_RETRY_BASE_SECONDS)
        superseded, self.supervisor = self.supervisor, None
        self.network.mode = "ok"
        await self.rebuild()
        await self.core.reconcile_until_initialized()
        monitor = self.core._connection_monitor_task
        self.assertIsNotNone(monitor)
        counters, recovery = self.counters(), self.recovery()
        with self.assertLogs(LOGGER, level="INFO") as logs:
            delay.set_result(None)
            await asyncio.wait_for(superseded, 2)  # it returns; nothing raised
        self.assertEqual(len(_events(logs.records, "core_authority_supervisor_retired")), 1)
        self.assertEqual((self.counters(), self.recovery()), (counters, recovery))
        self.assertIs(self.core._connection_monitor_task, monitor)
        self.assertFalse(monitor.done())
        await self.assert_current_streak()

    async def test_retiring_the_supervisor_discards_its_in_flight_work(self):
        # Retirement of the current supervisor advances the runtime
        # generation: on-demand work still in flight cannot publish after it.
        await self.boot()
        self.start_supervisor()
        await self.until(lambda: self.counters()["reprobes"]
                         and not self.core._reconciliation_lock.locked())
        hold = _Hold(self.core._source, "capture_core_snapshot", 1)
        pending = asyncio.create_task(self.core.reconcile_once("mutation_pre_dispatch"))
        await hold.wait()
        self.supervisor.cancel()
        await asyncio.gather(self.supervisor, return_exceptions=True)
        self.supervisor = None
        # Retirement leaves the boot's admission in place (unchanged from
        # base); the superseded work must not re-publish over it.
        published = self.core.current_observation
        await self.finish_stale(hold.release, [pending], ["observation"],
                                reason="core_runtime_generation_replaced", withheld=False)
        self.assertIs(self.core.current_observation, published)
        self.assert_authority_round_trip()

    def test_production_constructs_exactly_one_runtime_and_never_rearms(self):
        # The per-object latch is the process lifetime only because production
        # builds one CoreRuntime, at import, and nothing re-arms the latch.
        import ast
        from pathlib import Path
        import ha_mcp_engineering
        from ha_mcp_engineering.ha_core_readmission import runtime as runtime_module

        package = Path(ha_mcp_engineering.__file__).parent
        constructions = []
        for path in sorted(package.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "CoreRuntime"):
                    constructions.append(path.relative_to(package).as_posix())
        self.assertEqual(constructions, ["ha_core_readmission/runtime.py"])
        tree = ast.parse(Path(runtime_module.__file__).read_text(encoding="utf-8"))
        module_level = [node for node in tree.body if isinstance(node, ast.Assign)
                        and isinstance(node.value, ast.Call)
                        and getattr(node.value.func, "id", None) == "CoreRuntime"]
        self.assertEqual([target.id for node in module_level for target in node.targets],
                         ["CORE_READMISSION"])
        writers = {}
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                targets = (node.targets if isinstance(node, ast.Assign)
                           else [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign))
                           else [])
                for target in targets:
                    if isinstance(target, ast.Attribute) and target.attr == "_boot_exception_spent":
                        writers.setdefault(function.name, []).append(ast.unparse(node.value))
        self.assertEqual(writers, {"__init__": ["False"],
                                   "_spend_boot_exception_locked": ["True"]})

    # -- H2 (review of a6e66a8): any changed key restarts the streak --------

    async def test_reviewer_on_demand_version_change_resets_the_streak(self):
        await self.boot()
        await self.withdraw()
        await self.core.reconcile_once("periodic")
        self.assertEqual(self.recovery()["confirmations"], 1)
        with self.assertLogs(LOGGER, level="INFO") as logs:
            self.network.version = "2026.9.0"  # B, observed on demand
            await self.core.reconcile_once("mutation_pre_dispatch")
        self.assertEqual(self.recovery()["confirmations"], 0)
        self.assertEqual(self.counters()["recovery_on_demand_refusals"], 1)
        reset = _events(logs.records, "core_authority_recovery_reset")
        self.assertEqual([item.safe_context["reason"] for item in reset],
                         ["core_authority_key_changed"])
        self.assertEqual(reset[0].levelno, logging.WARNING)
        self.assert_logs_safe(logs.records)
        self.network.version = "2026.9.1"  # back to A
        await self.core.reconcile_once("periodic")
        self.assertIsNone(self.core.acquire(CAPABILITY), "A-B-A restored after one confirmation")
        self.assertEqual(self.recovery()["confirmations"], 1)
        await self.core.reconcile_once("periodic")
        self.assert_authority_round_trip()
        self.assertEqual(self.counters()["recovery_confirmations"], 3)

    async def test_on_demand_same_key_neither_resets_nor_adds(self):
        await self.boot()
        await self.withdraw()
        await self.core.reconcile_once("periodic")
        for trigger in ON_DEMAND:
            await self.core.reconcile_once(trigger)
            self.assertEqual(self.recovery()["confirmations"], 1, trigger)
        await self.core.reconcile_once("periodic")
        self.assert_authority_round_trip()

    async def test_on_demand_version_change_mid_streak_needs_fresh_confirmations(self):
        # A flap needs three: a changed key on demand after two still needs three.
        await self.boot()
        await self.withdraw()
        await reconcile_through_gate(self.core)
        await self.withdraw()
        self.assertEqual(self.recovery()["confirmations_required"],
                         CORE_RECOVERY_FLAP_CONFIRMATIONS)
        for _ in range(CORE_RECOVERY_FLAP_CONFIRMATIONS - 1):
            await self.core.reconcile_once("periodic")
        self.network.version = "2026.9.0"
        await self.core.reconcile_once("configuration_reverification")
        self.assertEqual(self.recovery()["confirmations"], 0)
        for count in range(1, CORE_RECOVERY_FLAP_CONFIRMATIONS):
            await self.core.reconcile_once("periodic")
            self.assertIsNone(self.core.acquire(CAPABILITY), count)
        await self.core.reconcile_once("periodic")
        self.assertEqual(self.core.current_observation.version, "2026.9.0")
        self.assert_authority_round_trip()

    async def flaky_core_withdrawn(self):
        source = _FlakySource(core_tests._core_2026_9_snapshot())
        core = CoreRuntime()
        core.configure(object(), source=source)
        await core.reconcile_until_initialized()
        self.assertTrue(core.release(core.acquire(CAPABILITY)))
        source.fail = True
        await core.reconcile_once("periodic")
        source.fail = False
        self.assertIsNone(core.acquire(CAPABILITY))
        return core, source

    async def test_reviewer_on_demand_changed_contract_resets_the_streak(self):
        from copy import deepcopy

        core, source = await self.flaky_core_withdrawn()
        await core.reconcile_once("periodic")
        self.assertEqual(core.health_projection()["recovery"]["confirmations"], 1)
        complete = deepcopy(source.snapshot["capability_evidence"])
        source.snapshot["capability_evidence"] = [
            item for item in complete if item.get("capability_id") != "core.template_semantics"]
        await core.reconcile_once("mutation_pre_dispatch")
        self.assertEqual(core.health_projection()["recovery"]["confirmations"], 0)
        source.snapshot["capability_evidence"] = complete
        await core.reconcile_once("periodic")
        self.assertIsNone(core.acquire(CAPABILITY), "a changed contract bridged the streak")
        await core.reconcile_once("periodic")
        self.assertTrue(core.release(core.acquire(CAPABILITY)))
        # Cumulative: the discarded confirmation plus the fresh streak.
        self.assertEqual(core.health_snapshot()["counters"]["recovery_confirmations"],
                         1 + CORE_RECOVERY_CONFIRMATIONS)

    async def test_reviewer_concurrent_mixed_checks_count_only_background(self):
        core, _source = await self.flaky_core_withdrawn()
        await asyncio.gather(core.reconcile_once("mutation_pre_dispatch"),
                             core.reconcile_once("periodic"),
                             core.reconcile_once("mutation_pre_dispatch"))
        self.assertIsNone(core.acquire(CAPABILITY))
        self.assertEqual(core.health_projection()["recovery"]["confirmations"], 1)
        self.assertEqual(core.health_snapshot()["counters"]["recovery_on_demand_refusals"], 2)
        await core.reconcile_once("periodic")
        self.assertTrue(core.release(core.acquire(CAPABILITY)))

    # -- Root cause: the live beta.24 state recovers through the gate ------

    async def reproduce_live_incident_state(self, fault):
        """Boot, 25 successful supervisor wakes, then one failed attempt.

        The production delegated catalog (the reviewed ha-mcp 8.5.0 capture,
        whose Core-dependent tools include ``ha_config_get_yaml``) and the
        dependency index are attached, as in the add-on. The wakes are
        event-driven, not 300 s intervals: the counters cannot tell the two
        apart, so this reproduces the counters, not the live timing.
        """
        await self.boot()
        yaml_tests.Harness.setUp(self)
        gateway, transport = await yaml_tests.Harness.gateway(
            self, "8.5.0", mode="A", core_runtime=self.core)
        self.core.register_reconciliation_listener(gateway.request_core_reconciliation)
        dependency = DependencyAnalysisRuntime()
        dependency.configure(HomeAssistantRestClient(self.settings),
                             HomeAssistantWebSocketClient(self.settings),
                             core_runtime=self.core)
        self.addAsyncCleanup(dependency.shutdown)
        self.supervisor = asyncio.create_task(self.core.supervise(
            interval_seconds=300, sleep=self.clock.sleep, jitter=lambda: 0.5))
        for wake in range(25):
            self.core.request_reconciliation()
            await self.until(lambda: self.counters()["verification_successes"] == wake + 2)
        self.network.mode = "connection"
        if fault == "probe":
            self.core.request_reconciliation()  # P1: an ordinary wake fails
        else:
            self.network.monitors[-1].disconnect.set()  # P2: socket closes, reprobe fails
        delay = await self.release(CORE_RECOVERY_RETRY_BASE_SECONDS)
        await gateway.initialize(gateway.synthetic_server)  # the gateway's rebuild
        return delay, gateway, transport, dependency.require().index

    async def test_live_incident_state_recovers_automatically(self):
        # Reproduces every live counter in design Part 1 except lease
        # attempts/failures (11/2), which depend on live tool calls.
        for fault in ("probe", "socket"):
            with self.subTest(fault=fault):
                await self.fresh_core()
                delay, gateway, transport, index = await self.reproduce_live_incident_state(fault)
                health = self.core.health_snapshot()
                counters = health["counters"]
                # The live health state at 17:49Z.
                self.assertEqual(
                    (health["initialized"], health["observed_core_version"],
                     health["current_generation"]), (False, None, None))
                self.assertEqual(
                    {key: counters[key] for key in (
                        "verification_attempts", "verification_successes",
                        "verification_failures", "retirements", "reprobes",
                        "catalog_withdrawals", "catalog_restorations",
                        "authority_restorations")},
                    {"verification_attempts": 27, "verification_successes": 26,
                     "verification_failures": 1, "retirements": 1, "reprobes": 26,
                     "catalog_withdrawals": 29, "catalog_restorations": 0,
                     "authority_restorations": 0})
                catalog = gateway.health_snapshot()
                self.assertEqual(len(catalog["core_withheld_tools"]), 29)
                self.assertEqual(catalog["optional_tools"][yaml_tests.TOOL],
                                 "withheld:core_unavailable")
                self.assertEqual(transport.sent, [])
                self.assertTrue(index.invalidated)
                self.assertEqual(index._invalidation_reason, "core_authority_changed")
                recovery = self.assert_bounded_projection()
                # Unlike base, the retry is visible and paced by backoff, not 300 s.
                self.assertEqual(recovery["recovery_state"], "backing_off")
                self.assertEqual(recovery["retry_delay_seconds"], CORE_RECOVERY_RETRY_BASE_SECONDS)
                self.assertIsNotNone(recovery["next_recovery_attempt_at"])
                self.assertEqual(recovery["last_failure_reason"],
                                 "core_lifecycle_monitor_unavailable")
                await self.recover_through_confirmation(None, delay=delay)
                await gateway.initialize(gateway.synthetic_server)
                self.assertEqual(self.recovery()["recovery_state"], "restored")
                self.assertEqual(self.counters()["recovery_confirmations"],
                                 CORE_RECOVERY_CONFIRMATIONS)
                self.assertEqual(self.counters()["catalog_restorations"], 29)
                self.assertEqual(gateway.health_snapshot()["core_withheld_tools"], [])
                self.assert_authority_round_trip()

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


class SignedRegistryKey(unittest.IsolatedAsyncioTestCase):
    """H2: the selected signed-registry authority is part of the agreement key."""

    asyncSetUp = continuity_tests.CoreContinuityTests.asyncSetUp
    runtime = continuity_tests.CoreContinuityTests.runtime
    next_journal = continuity_tests.CoreContinuityTests.next_journal

    async def move_registry(self, digit):
        entry = continuity_tests.core_entry()
        entry["evidence_sha256"] = digit * 64
        self.raw = self.next_journal(entries=[entry])
        self.assertTrue(await self.registry.refresh())

    async def test_registry_move_seen_on_demand_resets_the_streak(self):
        core, _source = await self.runtime()
        self.assertTrue(core.release(core.acquire(CAPABILITY)))
        await self.move_registry("8")
        self.assertIsNone(core.acquire(CAPABILITY))
        await core.reconcile_once("periodic")
        self.assertEqual(core.health_projection()["recovery"]["confirmations"], 1)
        await self.move_registry("9")
        with self.assertLogs(LOGGER, level="INFO") as logs:
            await core.reconcile_once("mutation_pre_dispatch")
        self.assertEqual(core.health_projection()["recovery"]["confirmations"], 0)
        reset = _events(logs.records, "core_authority_recovery_reset")
        self.assertEqual([record.safe_context["reason"] for record in reset],
                         ["core_authority_key_changed"])
        await core.reconcile_once("periodic")
        self.assertIsNone(core.acquire(CAPABILITY), "the moved registry bridged the streak")
        await core.reconcile_once("periodic")
        self.assertTrue(core.release(core.acquire(CAPABILITY)))
        # Cumulative: the discarded confirmation plus the fresh streak.
        self.assertEqual(core.health_snapshot()["counters"]["recovery_confirmations"],
                         1 + CORE_RECOVERY_CONFIRMATIONS)


if __name__ == "__main__":
    unittest.main()
