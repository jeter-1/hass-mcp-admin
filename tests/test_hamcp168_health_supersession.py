"""HAMCP-168: idle F3 recovery must not supersede deep-health scans.

Lineage: inverted from the test-only reproduction in draft PR #245
(tests/test_hamcp168_health_supersession_repro.py at 27e9bdc), which proved
that every idle periodic sweep rewrote the historical recovery cursor in the
fenced children namespace and so refused any scan spanning a sweep.

A-1: while a deep-health scan holds its fence, the sweep defers only its idle
historical-cursor write, for a bounded number of consecutive sweeps. Real
writes (child updates, locks, in-place edits, recovery transitions) still
supersede the scan.
Every holder is released on every exit path. Refusals keep their public
error code, message, details and cause.

Real writers and disposable synthetic history only; no live dependency.
"""
from __future__ import annotations

import asyncio
import json
import threading
import unittest
from unittest.mock import patch

from tests import test_f3_orphan_child_recovery as fixtures
from ha_mcp_engineering.errors import ErrorCode, GovernanceError
from ha_mcp_engineering.f3_runtime import runtime as f3_runtime_module
from ha_mcp_engineering.f3_runtime.health_scan import HealthScanFence
from ha_mcp_engineering.governance import service as service_module
from ha_mcp_engineering.governance.task_storage import ExecutionTaskStorageError

# F3 storage classes, reached through the reviewed f3_runtime boundary.
ExecutionStorageError = f3_runtime_module.ExecutionStorageError
LockStorageError = f3_runtime_module.LockStorageError

_HOLD_TIMEOUT_SECONDS = 30
_SUPERSEDED = {"reason": "health_snapshot_superseded"}


class _HealthScanCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixtures.OrphanChildRecoveryTests()
        await self.fixture.asyncSetUp()
        self.service = self.fixture.service
        self.runtime = self.fixture.runtime
        self.children = self.runtime.children

    async def asyncTearDown(self):
        await self.fixture.asyncTearDown()
        self.fixture.doCleanups()

    def _children_fence_token(self):
        return (
            self.children._health_generation,
            HealthScanFence.stamp(self.children.root),
        )

    async def _held_health(self, during=None):
        """Hold deep health inside its validation worker and run `during`."""

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
                self.assertEqual(1, self.runtime._health_fence_holders)
                if during is not None:
                    outcome = during()
                    if asyncio.iscoroutine(outcome):
                        await outcome
            finally:
                release.set()
            result = (await asyncio.gather(health, return_exceptions=True))[0]
        self.assertEqual(0, self.runtime._health_fence_holders)
        return result

    def assertSuperseded(self, result, code=ErrorCode.EXECUTION_TASK_STORAGE_ERROR):
        self.assertIsInstance(result, GovernanceError)
        self.assertEqual(code, result.code)
        self.assertEqual(_SUPERSEDED, result.details)
        # Public contract unchanged.
        self.assertEqual(
            "Governance storage is unavailable."
            if code == ErrorCode.CHANGE_PLAN_STORAGE_ERROR
            else "Execution-task storage is unavailable.",
            result.safe_message,
        )

    def assertHealthy(self, result):
        self.assertNotIsInstance(result, BaseException)
        self.assertIn("f3", result)


class IdleSweepDeferralTests(_HealthScanCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        # Settled history only: no nonterminal work, no pending orphans.
        self.fixture._populate_terminal_history(
            task_count=4, declarations_per_task=2
        )

    async def test_idle_sweep_without_a_scan_still_advances_cursor_every_cadence(self):
        """No scan holds a fence: historical navigation is unchanged."""

        self.assertEqual(30, f3_runtime_module.RECOVERY_CADENCE_SECONDS)
        provider_calls = len(self.fixture.gateway.calls)
        tokens = [self._children_fence_token()]
        cursors = [self.children.recovery_cursor()]
        for _ in range(4):
            result = await self.runtime.recover_once("periodic")
            self.assertEqual(0, result.get("orphaned_children_terminalized", 0))
            tokens.append(self._children_fence_token())
            cursors.append(self.children.recovery_cursor())
        for earlier, later in zip(tokens, tokens[1:]):
            self.assertGreater(later[0], earlier[0])
        for earlier, later in zip(cursors, cursors[1:]):
            self.assertNotEqual(earlier, later)
        self.assertEqual(0, self.runtime._recovery_cursor_deferrals)
        self.assertEqual(provider_calls, len(self.fixture.gateway.calls))

    async def test_control_same_hold_without_a_sweep_succeeds(self):
        self.assertHealthy(await self._held_health())

    async def test_scan_spanning_an_idle_sweep_succeeds(self):
        """Inverted Defect A: a sweep inside the scan window no longer refuses."""

        cursor = self.children.recovery_cursor()
        token = self._children_fence_token()
        result = await self._held_health(
            lambda: self.runtime.recover_once("periodic"))
        self.assertHealthy(result)
        # Only the idle cursor write was deferred, so the fence never moved.
        self.assertEqual(cursor, self.children.recovery_cursor())
        self.assertEqual(token, self._children_fence_token())
        self.assertEqual(1, self.runtime._recovery_cursor_deferrals)
        # The next unfenced sweep advances the deferred cursor and resets.
        await self.runtime.recover_once("periodic")
        self.assertNotEqual(cursor, self.children.recovery_cursor())
        self.assertEqual(0, self.runtime._recovery_cursor_deferrals)

    async def test_three_consecutive_scans_each_spanning_a_sweep_all_succeed(self):
        validations = 0
        original = service_module._validate_health_records

        def counted(*args, **kwargs):
            nonlocal validations
            validations += 1
            return original(*args, **kwargs)

        outcomes = []
        with patch.object(service_module, "_validate_health_records", counted):
            for _ in range(3):
                outcomes.append(await self._held_health(
                    lambda: self.runtime.recover_once("periodic")))
        for outcome in outcomes:
            self.assertHealthy(outcome)
        self.assertEqual(3, validations)
        self.assertEqual(3, self.runtime._recovery_cursor_deferrals)

    async def test_cursor_eventually_advances_under_continuous_health_load(self):
        """The deferral is bounded: the cap'th+1 sweep advances regardless."""

        limit = f3_runtime_module.RECOVERY_CURSOR_HEALTH_DEFERRAL_LIMIT
        self.assertEqual(3, limit)
        cursor = self.children.recovery_cursor()
        token = self._children_fence_token()
        observed = []

        async def sweeps():
            for _ in range(limit + 1):
                await self.runtime.recover_once("periodic")
                observed.append(
                    (self.children.recovery_cursor(), self._children_fence_token()))

        result = await self._held_health(sweeps)
        # Every deferred sweep left the cursor and the fence untouched ...
        for deferred in observed[:limit]:
            self.assertEqual((cursor, token), deferred)
        # ... and the next one advanced it, superseding the long-held scan.
        self.assertNotEqual(cursor, observed[limit][0])
        self.assertGreater(observed[limit][1][0], token[0])
        self.assertEqual(0, self.runtime._recovery_cursor_deferrals)
        self.assertSuperseded(result)
        # A fresh scan with no sweep inside it succeeds again.
        self.assertHealthy(await self._held_health())

    async def test_real_child_update_during_scan_still_refuses(self):
        child_id = json.loads(
            next(self.children.root.glob("*.child.json")).read_text()
        )["declaration"]["child_id"]
        readiness = self.runtime.readiness_state()
        result = await self._held_health(lambda: self.children.update_runtime(
            child_id, changes={"backoff_seconds": 1}))
        self.assertSuperseded(result)
        self.assertEqual(readiness, self.runtime.readiness_state())

    async def test_real_child_update_alongside_a_deferred_sweep_still_refuses(self):
        child_id = json.loads(
            next(self.children.root.glob("*.child.json")).read_text()
        )["declaration"]["child_id"]

        async def sweep_then_write():
            await self.runtime.recover_once("periodic")
            self.children.update_runtime(child_id, changes={"backoff_seconds": 1})

        self.assertSuperseded(await self._held_health(sweep_then_write))
        self.assertEqual(1, self.runtime._recovery_cursor_deferrals)

    async def test_in_place_child_movement_during_scan_still_refuses(self):
        """An edit after the scan read a child file is caught by its stamp."""

        path = next(self.children.root.glob("*.child.json"))
        original = self.children.collect_health

        async def collect_then_edit(*args, **kwargs):
            result = await original(*args, **kwargs)
            # Directory identity and generation are unchanged by this edit.
            path.write_text(path.read_text() + " ")
            return result

        with patch.object(self.children, "collect_health", collect_then_edit):
            result = await self._held_health()
        self.assertSuperseded(result)

    async def test_in_place_task_movement_during_scan_still_refuses(self):
        path = next(self.service.task_repository.root.glob("*.json"))
        self.assertSuperseded(await self._held_health(
            lambda: path.write_text(path.read_text() + " ")))

    async def test_parent_task_update_during_scan_still_refuses(self):
        task = self.service.task_repository.list()[0]
        self.assertSuperseded(await self._held_health(
            lambda: self.service.task_repository.save(task)))


class RealRecoveryWriteTests(_HealthScanCase):
    async def test_lock_race_during_scan_still_refuses(self):
        _task, declarations = await self.fixture._build_live_orphan()
        before = self.runtime.readiness_state()
        result = await self._held_health(lambda: self.fixture._hold_child_lock(
            declarations[0], conflict_hold=True))
        self.assertSuperseded(result)
        self.assertEqual(before, self.runtime.readiness_state())
        fresh = await self.service.async_health_summary()
        self.assertGreater(fresh["f3"]["active_conflict_hold_count"], 0)

    async def test_lock_only_movement_during_scan_still_refuses(self):
        def acquire():
            self.runtime.locks.acquire_once(
                (
                    fixtures.LockRequest(
                        key="automation:hamcp168_lock_only",
                        scopes=(fixtures.LockScope.RESOURCE,),
                        mode=fixtures.LockMode.EXCLUSIVE,
                        reason_codes=("hamcp168_test",),
                    ),
                ),
                owner=fixtures.LockOwner(
                    owner_id="hamcp168-owner",
                    task_id="hamcp168-task",
                    plan_id="hamcp168-plan",
                    operation_id="hamcp168-operation",
                    attempt_id="hamcp168-attempt",
                ),
                timing=fixtures.LockTiming(120, 20, 0, 0.05),
                now=self.service.now(),
            )

        self.assertSuperseded(await self._held_health(acquire))

    async def test_orphan_recovery_write_during_scan_still_refuses(self):
        _task, declarations = await self.fixture._build_live_orphan()
        self.assertEqual(1, self.children.health()["nonterminal_execution_count"])
        cursor = self.children.recovery_cursor()
        result = await self._held_health(
            lambda: self.runtime.recover_once("periodic"))
        # The sweep made a real recovery transition, which moved the fence,
        # and so it advanced its cursor rather than deferring it.
        self.assertEqual(0, self.children.health()["nonterminal_execution_count"])
        self.assertEqual(0, self.runtime._recovery_cursor_deferrals)
        self.assertNotEqual(cursor, self.children.recovery_cursor())
        self.assertSuperseded(result)
        self.assertHealthy(await self.service.async_health_summary())

    async def test_plan_update_during_scan_still_refuses(self):
        created = await self.fixture.create_automation_plan()
        plan = self.service.repository.get(created["plan_id"])
        self.assertSuperseded(
            await self._held_health(lambda: self.service.repository.save(plan)),
            code=ErrorCode.CHANGE_PLAN_STORAGE_ERROR,
        )


class HolderCleanupTests(_HealthScanCase):
    """A-1: every exit path releases the holder; refusals are unchanged."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fixture._populate_terminal_history(task_count=2, declarations_per_task=2)
        self.child_id = json.loads(
            next(self.children.root.glob("*.child.json")).read_text()
        )["declaration"]["child_id"]

    def _move_child(self):
        self.children.update_runtime(self.child_id, changes={"backoff_seconds": 1})

    async def test_storage_failure_keeps_its_refusal_and_releases_the_holder(self):
        failure = ExecutionTaskStorageError("synthetic read failure")
        with patch.object(self.service.task_repository, "_load", side_effect=failure):
            with self.assertRaises(GovernanceError) as error:
                await self.service.async_health_summary()
        self.assertEqual(ErrorCode.EXECUTION_TASK_STORAGE_ERROR, error.exception.code)
        self.assertEqual({}, error.exception.details)
        self.assertEqual("Execution-task storage is unavailable.", error.exception.safe_message)
        self.assertIs(failure, error.exception.__cause__)
        self.assertEqual(0, self.runtime._health_fence_holders)
        self.assertHealthy(await self.service.async_health_summary())

    async def test_fence_holder_is_released_after_cancellation(self):
        original = service_module._validate_health_records
        entered = threading.Event()
        release = threading.Event()

        def held(*args, **kwargs):
            entered.set()
            release.wait(_HOLD_TIMEOUT_SECONDS)
            return original(*args, **kwargs)

        with patch.object(service_module, "_validate_health_records", held):
            health = asyncio.create_task(self.service.async_health_summary())
            self.assertTrue(await asyncio.to_thread(entered.wait, _HOLD_TIMEOUT_SECONDS))
            health.cancel()
            release.set()
            await asyncio.gather(health, return_exceptions=True)
        self.assertEqual(0, self.runtime._health_fence_holders)
        # Without a holder the next sweep is not deferred.
        cursor = self.children.recovery_cursor()
        await self.runtime.recover_once("periodic")
        self.assertNotEqual(cursor, self.children.recovery_cursor())

    async def test_holder_clears_when_fence_sources_raise(self):
        """No fence object is ever built, yet no holder leaks."""

        with patch.object(
                self.runtime, "_health_fence_sources",
                side_effect=RuntimeError("synthetic construction failure")):
            with self.assertRaises(RuntimeError):
                await self.service.async_health_summary()
        self.assertEqual(0, self.runtime._health_fence_holders)
        self.assertHealthy(await self.service.async_health_summary())

    async def test_f3_storage_failures_propagate_unconverted_and_release_the_holder(self):
        for error in (ExecutionStorageError, LockStorageError):
            failure = error("synthetic failure")
            with self.subTest(error=error.__name__), patch.object(
                    self.runtime, "_async_health", side_effect=failure):
                with self.assertRaises(error) as raised:
                    await self.service.async_health_summary()
                self.assertIs(failure, raised.exception)
                self.assertEqual(0, self.runtime._health_fence_holders)

    async def test_storage_failure_with_proven_movement_refuses_as_superseded(self):
        def moved_then_failed(*args, **kwargs):
            self._move_child()
            raise ExecutionTaskStorageError("synthetic failure")

        with patch.object(
                self.service.task_repository, "_load", side_effect=moved_then_failed):
            with self.assertRaises(GovernanceError) as error:
                await self.service.async_health_summary()
        self.assertSuperseded(error.exception)
        self.assertEqual(0, self.runtime._health_fence_holders)

    async def test_standalone_runtime_health_holds_and_releases_the_fence(self):
        observed = []

        async def recorded(**kwargs):
            observed.append(self.runtime._health_fence_holders)
            if len(observed) == 2:
                raise ExecutionStorageError("synthetic failure")
            return {}

        with patch.object(self.runtime, "_async_health", side_effect=recorded):
            await self.runtime.async_health()
            self.assertEqual(0, self.runtime._health_fence_holders)
            with self.assertRaises(ExecutionStorageError):
                await self.runtime.async_health()
        self.assertEqual([1, 1], observed)
        self.assertEqual(0, self.runtime._health_fence_holders)

class DeferralBoundResetTests(_HealthScanCase):
    async def test_unfenced_sweep_restores_the_full_deferral_bound(self):
        """A sweep with no scan in flight restores the bound, even if it writes nothing."""

        limit = f3_runtime_module.RECOVERY_CURSOR_HEALTH_DEFERRAL_LIMIT
        self.runtime._recovery_cursor_deferrals = limit
        # Empty history: an unfenced sweep has no cursor to move.
        token = self._children_fence_token()
        await self.runtime.recover_once("periodic")
        self.assertIsNone(self.children.recovery_cursor())
        self.assertEqual(token, self._children_fence_token())
        self.assertEqual(0, self.runtime._recovery_cursor_deferrals)
        self.fixture._populate_terminal_history(task_count=4, declarations_per_task=2)
        cursor = self.children.recovery_cursor()
        result = await self._held_health(
            lambda: self.runtime.recover_once("periodic"))
        self.assertHealthy(result)
        self.assertEqual(cursor, self.children.recovery_cursor())
        self.assertEqual(1, self.runtime._recovery_cursor_deferrals)


if __name__ == "__main__":
    unittest.main()
