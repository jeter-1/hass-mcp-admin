"""HAMCP-168 reproduction: idle F3 recovery supersedes long deep-health scans.

Test-only reproduction of the deployed 2.4.1-beta.3 symptom: `get_server_health`
fails with `execution_task_storage_error` / `health_snapshot_superseded` while
nothing user-visible is writing. These tests assert current behavior and are a
characterization, not a fix: they are expected to change when a fix lands.

Mechanism under test (real writers, disposable synthetic history):
1. On an idle store with settled F3 history, every periodic recovery sweep
   (`recover_once("periodic")`, every RECOVERY_CADENCE_SECONDS) writes into
   the children namespace, moving `children._health_generation` and the
   children-root stamp.
2. Deep health fences that namespace for its whole scan. A scan that spans one
   sweep is refused as superseded, with the generic "storage is unavailable"
   message, no readiness fault, no warning-level log and no phase receipt.
3. Nothing retries, so a scan that always outlasts the cadence never succeeds.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import unittest
from unittest.mock import patch

from tests import test_f3_orphan_child_recovery as fixtures
from ha_mcp_engineering.errors import ErrorCode, GovernanceError
from ha_mcp_engineering.f3_runtime import runtime as f3_runtime_module
from ha_mcp_engineering.f3_runtime.health_scan import HealthScanFence
from ha_mcp_engineering.governance import service as service_module

_HOLD_TIMEOUT_SECONDS = 30


class _RecordingHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


class IdleSweepSupersedesDeepHealthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixtures.OrphanChildRecoveryTests()
        await self.fixture.asyncSetUp()
        self.service = self.fixture.service
        self.runtime = self.fixture.runtime
        self.children = self.runtime.children
        # Settled history only: no nonterminal work, no pending orphans.
        self.fixture._populate_terminal_history(
            task_count=4, declarations_per_task=2
        )
        self.logs = _RecordingHandler()
        root = logging.getLogger()
        self._previous_level = root.level
        root.addHandler(self.logs)
        root.setLevel(logging.DEBUG)

    async def asyncTearDown(self):
        root = logging.getLogger()
        root.removeHandler(self.logs)
        root.setLevel(self._previous_level)
        await self.fixture.asyncTearDown()
        self.fixture.doCleanups()

    def _children_fence_token(self):
        return (
            self.children._health_generation,
            HealthScanFence.stamp(self.children.root),
        )

    async def _health_with_sweep_during_validation(self, *, sweep: bool):
        """Hold deep health inside its validation worker; optionally sweep."""

        original = service_module._validate_health_records
        entered = threading.Event()
        release = threading.Event()

        def held(*args, **kwargs):
            entered.set()
            if not release.wait(_HOLD_TIMEOUT_SECONDS):
                raise AssertionError("validation hold was never released")
            return original(*args, **kwargs)

        with patch.object(service_module, "_validate_health_records", held):
            health = asyncio.create_task(self.service.async_health_summary())
            try:
                self.assertTrue(
                    await asyncio.to_thread(entered.wait, _HOLD_TIMEOUT_SECONDS)
                )
                if sweep:
                    await self.runtime.recover_once("periodic")
            finally:
                release.set()
            return await asyncio.gather(health, return_exceptions=True)

    def _warning_or_supersession_records(self):
        return [
            record for record in self.logs.records
            if record.levelno >= logging.WARNING
            or "superseded" in record.getMessage()
        ]

    async def test_idle_periodic_sweep_writes_fenced_children_namespace_every_cadence(self):
        """Defect A precondition: an idle store still moves the fence each sweep."""

        self.assertEqual(30, f3_runtime_module.RECOVERY_CADENCE_SECONDS)
        provider_calls = len(self.fixture.gateway.calls)
        before_health = self.runtime.children.health()
        self.assertEqual(0, before_health["nonterminal_execution_count"])
        tokens = [self._children_fence_token()]
        for _ in range(3):
            result = await self.runtime.recover_once("periodic")
            self.assertEqual(0, result.get("orphaned_children_terminalized", 0))
            tokens.append(self._children_fence_token())
        for earlier, later in zip(tokens, tokens[1:]):
            # Each idle sweep rewrites the historical recovery cursor in the
            # children root. The fence compares (generation, stamp) as one
            # token, so the generation bump alone supersedes; the directory
            # stamp may coalesce within the filesystem timestamp granularity.
            self.assertGreater(later[0], earlier[0])
            self.assertNotEqual(later, earlier)
        # It is bookkeeping only: no dispatch and no task-state change.
        self.assertEqual(provider_calls, len(self.fixture.gateway.calls))
        self.assertEqual(
            0, self.runtime.children.health()["nonterminal_execution_count"]
        )

    async def test_control_same_hold_without_a_sweep_succeeds(self):
        """The hold alone is harmless: the storage itself is healthy."""

        result = await self._health_with_sweep_during_validation(sweep=False)
        self.assertNotIsInstance(result[0], BaseException)
        self.assertIn("f3", result[0])

    async def test_sweep_during_scan_reports_storage_unavailable_silently(self):
        """Defect A: one sweep inside the scan window fails health, quietly."""

        # Publish a successful phase receipt first.
        await self.service.async_health_summary()
        receipt = dict(
            self.service._hot_path_metrics["governance_health"]["phase_elapsed_ms"]
        )
        readiness = self.runtime.readiness_state()
        self.logs.records.clear()

        result = await self._health_with_sweep_during_validation(sweep=True)

        error = result[0]
        self.assertIsInstance(error, GovernanceError)
        self.assertEqual(ErrorCode.EXECUTION_TASK_STORAGE_ERROR, error.code)
        self.assertEqual({"reason": "health_snapshot_superseded"}, error.details)
        # Client-visible text claims an outage although storage is healthy.
        self.assertEqual("Execution-task storage is unavailable.", error.safe_message)
        self.assertTrue(error.retryable)
        # No readiness fault, no warning-level or supersession log line, and the
        # stale success receipt is all that remains for diagnosis.
        self.assertEqual(readiness, self.runtime.readiness_state())
        self.assertEqual([], self._warning_or_supersession_records())
        self.assertEqual(
            receipt,
            self.service._hot_path_metrics["governance_health"]["phase_elapsed_ms"],
        )

    async def test_scan_outlasting_cadence_fails_every_attempt_without_retry(self):
        """Defect A: when every scan spans a sweep, health never succeeds."""

        validations = 0
        original = service_module._validate_health_records

        def counted(*args, **kwargs):
            nonlocal validations
            validations += 1
            return original(*args, **kwargs)

        outcomes = []
        with patch.object(service_module, "_validate_health_records", counted):
            for _ in range(3):
                result = await self._health_with_sweep_during_validation(sweep=True)
                outcomes.append(result[0])
        for outcome in outcomes:
            self.assertIsInstance(outcome, GovernanceError)
            self.assertEqual({"reason": "health_snapshot_superseded"}, outcome.details)
        # One validation per call: supersession is not retried internally.
        self.assertEqual(3, validations)
        # The same store answers as soon as no sweep lands inside the window.
        recovered = await self._health_with_sweep_during_validation(sweep=False)
        self.assertNotIsInstance(recovered[0], BaseException)


if __name__ == "__main__":
    unittest.main()
