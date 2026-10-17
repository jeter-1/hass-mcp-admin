"""HAMCP-189 O9: expired pre-intent work gets bounded retries, then ends.

A contract-v3 child that lost its first attempt before dispatch intent is
retried by F3 recovery.  Once its plan dispatch authority has expired, every
retry fails before the executor runs.  After the durable expiry plus the retry
window, recovery rejects the child before dispatch with the outcome the
configuration preflight gives an expired plan (``preflight_rejected`` with
``plan_expired``), releases its locks and projects the task
``failed_pre_dispatch``.  It never dispatches.  Real governance, F3 runtime and
persistence; only provider I/O and the Core seam are synthetic.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import unittest

from ha_mcp_engineering.f3.persistence import (
    BlindRedispatchProhibited,
    ExecutionClaimLost,
    ExecutionStorageError,
)
from ha_mcp_engineering.f3_runtime.runtime import (
    EXPIRED_PRE_INTENT_RETRY_WINDOW_SECONDS,
    F3RuntimeIntegration,
)
from ha_mcp_engineering.governance.models import PlanStatus

from tests import test_beta37_exact_helper_state as fixtures
from tests import test_rc5_recovery_approval as rc5


# The doubling backoff (5, 10, 20, 40, 80, 160, then 300 s) fits at most
# seven retries inside the 600 s window after expiry (5+10+...+160 = 315 s;
# the seventh schedules past the window), even when recovery runs at every
# eligible instant.
MAX_EXPIRED_RETRIES = 7


class ExpiredPreIntentDeadlineTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ExactHelperStateRuntimeTests.asyncSetUp
    asyncTearDown = fixtures.ExactHelperStateRuntimeTests.asyncTearDown
    grant = fixtures.ExactHelperStateRuntimeTests.grant
    create_and_grant = fixtures.ExactHelperStateRuntimeTests.create_and_grant
    start_apply = rc5.RecoveryApprovalTests.start_apply
    finish_pending = staticmethod(rc5.RecoveryApprovalTests.finish_pending)
    child_and_task = rc5.RecoveryApprovalTests.child_and_task
    assert_settled = rc5.RecoveryApprovalTests.assert_settled

    async def stuck_pre_intent(self):
        """Lose the first attempt at approval consumption (no intent)."""

        plan, core, pending = await self.start_apply()
        consume = core.consume

        def refuse(authority):
            core.leases.discard(authority)
            return None

        core.consume = refuse
        core.resume.set()
        await asyncio.gather(pending, return_exceptions=True)
        core.consume = consume
        child, task = self.child_and_task(plan)
        self.assertFalse(child.terminal)
        self.assertEqual(child.state, "preflight")
        self.assertIsNone(child.dispatch_intent)
        self.assertEqual(child.dispatch_count, 0)
        self.assertEqual(
            child.events[-1]["event_type"], "pre_intent_retry_required"
        )
        expiry = datetime.fromisoformat(
            self.service._load(plan["plan_id"]).expires_at
        )
        return plan, core, child, task, expiry

    def child_runtime(self, plan):
        _child, task = self.child_and_task(plan)
        declaration = self.runtime.children.declarations_for_task(
            task.task_id
        )[0]
        return self.runtime.children.runtime(declaration["child_id"])

    def move_to(self, instant):
        delta = (instant - self.clock()).total_seconds()
        self.assertGreaterEqual(delta, 0)
        self.clock.advance(seconds=int(delta))

    async def recover_at_every_eligible_instant(self, plan, *, limit=40):
        """Run recovery as often as the schedule allows; count retries."""

        retries = 0
        for _ in range(limit):
            await self.runtime.recover_once("periodic")
            child, _task = self.child_and_task(plan)
            if child.terminal:
                return retries
            state = self.child_runtime(plan)
            self.assertEqual(state["reconciliation_result"], "bounded_retry")
            retries += 1
            self.move_to(datetime.fromisoformat(state["next_eligible_at"]))
        self.fail("expired pre-intent work never terminated")

    def assert_truthful_expired_terminal(self, plan, core):
        child, task = self.child_and_task(plan)
        self.assertTrue(child.terminal)
        self.assertEqual(child.normalized_outcome, "preflight_rejected")
        self.assertEqual(child.task_state, "failed_pre_dispatch")
        self.assertEqual(child.events[-1]["event_type"], "pre_dispatch_terminal")
        self.assertEqual(child.events[-1]["diagnostic_codes"], ["plan_expired"])
        self.assertIsNone(child.dispatch_intent)
        self.assertEqual(child.dispatch_count, 0)
        self.assertEqual(task.state.value, "failed_pre_dispatch")
        self.assertEqual(task.terminal_outcome, "failed_pre_dispatch")
        self.assertEqual(
            self.service._load(plan["plan_id"]).status, PlanStatus.FAILED
        )
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assertEqual(self.helper.state, "on")
        self.assertTrue(self.runtime.readiness_state()["execution_ready"])
        self.assert_settled(core)

    async def test_expired_task_terminates_after_bounded_retries(self):
        plan, core, _child, _task, expiry = await self.stuck_pre_intent()
        self.move_to(expiry + timedelta(seconds=1))
        retries = await self.recover_at_every_eligible_instant(plan)
        self.assertGreaterEqual(retries, 1)
        self.assertLessEqual(retries, MAX_EXPIRED_RETRIES)
        self.assertGreaterEqual(
            self.clock(),
            expiry + timedelta(seconds=EXPIRED_PRE_INTENT_RETRY_WINDOW_SECONDS),
        )
        self.assert_truthful_expired_terminal(plan, core)
        # A terminal child is settled: later sweeps change nothing.
        self.clock.advance(seconds=3600)
        await self.runtime.recover_once("terminal_control")
        self.assert_truthful_expired_terminal(plan, core)

    async def test_retries_continue_until_the_deadline(self):
        plan, core, _child, _task, expiry = await self.stuck_pre_intent()
        deadline = expiry + timedelta(
            seconds=EXPIRED_PRE_INTENT_RETRY_WINDOW_SECONDS
        )
        self.move_to(deadline - timedelta(seconds=1))
        await self.runtime.recover_once("periodic")
        child, task = self.child_and_task(plan)
        self.assertFalse(child.terminal)
        self.assertEqual(task.state.value, "preflight")
        self.assertEqual(
            self.child_runtime(plan)["reconciliation_result"], "bounded_retry"
        )
        self.assertFalse(self.runtime.readiness_state()["execution_ready"])
        self.move_to(
            datetime.fromisoformat(self.child_runtime(plan)["next_eligible_at"])
        )
        await self.runtime.recover_once("periodic")
        self.assert_truthful_expired_terminal(plan, core)

    async def test_unexpired_failure_keeps_its_bounded_retry(self):
        # Preservation: a failure while the plan is still valid keeps its
        # bounded retry and is never terminalized.
        plan, core, _child, _task, expiry = await self.stuck_pre_intent()
        load_prepared = self.runtime._load_prepared

        async def unavailable(*args, **kwargs):
            raise RuntimeError("synthetic preparation failure")

        self.runtime._load_prepared = unavailable
        for _ in range(8):
            await self.runtime.recover_once("periodic")
            child, _task = self.child_and_task(plan)
            self.assertFalse(child.terminal)
            state = self.child_runtime(plan)
            self.assertEqual(state["reconciliation_result"], "bounded_retry")
            self.move_to(datetime.fromisoformat(state["next_eligible_at"]))
        self.assertLess(self.clock(), expiry)
        self.assertEqual(self.helper.dispatch_count, 0)
        # The same task, once preparation recovers before expiry, still
        # dispatches exactly once.
        self.runtime._load_prepared = load_prepared
        await self.runtime.recover_once("periodic")
        child, task = self.child_and_task(plan)
        self.assertEqual(child.normalized_outcome, "succeeded_verified")
        self.assertEqual(self.helper.dispatch_count, 1)
        self.assertEqual(child.dispatch_count, 1)

    async def test_deadline_survives_a_restart(self):
        plan, core, _child, _task, expiry = await self.stuck_pre_intent()
        self.move_to(expiry + timedelta(seconds=1))
        await self.runtime.recover_once("periodic")
        self.assertFalse(self.child_and_task(plan)[0].terminal)
        root = Path(self.temp.name)
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(root / "plans"),
            configuration_gateway=fixtures.UnusedConfigurationGateway(),
            backup_gateway=None,
            lifecycle_gateway=None,
            helper_state_gateway=self.helper,
            provider_identity_reader=fixtures.forbidden_upstream_identity,
            retention_days=90,
        )
        self.runtime.core_runtime = core
        self.service.f3_runtime = self.runtime
        self.move_to(
            expiry
            + timedelta(seconds=EXPIRED_PRE_INTENT_RETRY_WINDOW_SECONDS + 1)
        )
        await self.runtime.recover_once("startup")
        self.assert_truthful_expired_terminal(plan, core)

    async def test_service_reconciler_composition_never_dispatches(self):
        # Production runs the service reconciler after each recovery pass.
        # Whichever reconciler ends the task, nothing dispatches and the
        # runtime settles.
        plan, core, _child, _task, expiry = await self.stuck_pre_intent()
        self.move_to(expiry + timedelta(seconds=1))
        for _ in range(12):
            await self.runtime.recover_once("periodic")
            await self.service.reconcile_execution_tasks()
            self.clock.advance(seconds=120)
        child, task = self.child_and_task(plan)
        self.assertTrue(child.terminal)
        self.assertIsNone(child.dispatch_intent)
        self.assertEqual(child.dispatch_count, 0)
        self.assertEqual(task.state.value, "failed_pre_dispatch")
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assertTrue(self.runtime.readiness_state()["execution_ready"])
        self.assert_settled(core)


class ExpiredClaimTerminalPrimitiveTests(unittest.IsolatedAsyncioTestCase):
    """The claimless primitive refuses every record it must not end."""

    asyncSetUp = fixtures.ExactHelperStateRuntimeTests.asyncSetUp
    asyncTearDown = fixtures.ExactHelperStateRuntimeTests.asyncTearDown
    grant = fixtures.ExactHelperStateRuntimeTests.grant
    create_and_grant = fixtures.ExactHelperStateRuntimeTests.create_and_grant
    start_apply = rc5.RecoveryApprovalTests.start_apply
    finish_pending = staticmethod(rc5.RecoveryApprovalTests.finish_pending)
    child_and_task = rc5.RecoveryApprovalTests.child_and_task
    stuck_pre_intent = ExpiredPreIntentDeadlineTests.stuck_pre_intent

    def terminalize(self, child, **changes):
        arguments = {
            "expected_claim_generation": child.claim_generation,
            "diagnostic_codes": ("plan_expired",),
            "now": self.clock(),
        }
        arguments.update(changes)
        return self.runtime.children.terminalize_expired_claim_pre_dispatch(
            child.execution_identity().task_id, **arguments
        )

    async def test_live_or_changed_claim_is_not_overtaken(self):
        plan, _core, child, _task, _expiry = await self.stuck_pre_intent()
        with self.assertRaises(ExecutionClaimLost):
            self.terminalize(child, now=self.clock() - timedelta(seconds=1))
        with self.assertRaises(ExecutionClaimLost):
            self.terminalize(
                child, expected_claim_generation=child.claim_generation + 1
            )
        unchanged, _task = self.child_and_task(plan)
        self.assertFalse(unchanged.terminal)
        self.assertEqual(unchanged.events, child.events)
        ended = self.terminalize(child)
        self.assertEqual(ended.normalized_outcome, "preflight_rejected")
        with self.assertRaises(ExecutionStorageError):
            self.terminalize(child)

    async def test_dispatched_record_is_never_ended_pre_dispatch(self):
        self.helper.set_observed_state("on", self.clock)
        plan = await self.create_and_grant("off")
        await self.service.apply(plan["plan_id"], plan["plan_hash"])
        child, _task = self.child_and_task(plan)
        self.assertEqual(child.dispatch_count, 1)
        self.clock.advance(seconds=3600)
        with self.assertRaises(BlindRedispatchProhibited):
            self.terminalize(child)
        after, _task = self.child_and_task(plan)
        self.assertEqual(after.normalized_outcome, child.normalized_outcome)
        self.assertEqual(after.events, child.events)


if __name__ == "__main__":
    unittest.main()
