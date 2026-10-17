"""HAMCP-189 O9: expired declared work whose child was never claimed ends.

A process loss between task initialization (the durable child declaration)
and the first child claim leaves a declaration without an execution record.
Preparation of an expired plan always refuses, so recovery can never build the
record, and before this correction the task stayed ``created``/``preflight``
forever, including after restart.

After the same deadline as claimed pre-intent work (dispatch authority expiry
plus the retry window), recovery now creates the child record already terminal
from the declaration alone: ``preflight_rejected`` with ``plan_expired`` and
the existing ``execution_started``/``pre_dispatch_terminal`` events.  The task
projects ``failed_pre_dispatch``.  The whole recovery sweep decides and writes
that outcome locally before preparation: no provider or provider-identity
contact precedes, delays or gates it (review of ``1189020``, M2-R).  No
dispatch occurs, the service reconciler is not needed, a recorded cancel takes
precedence and a competing first claim is never overwritten.  Real governance,
F3 runtime and persistence; provider I/O is synthetic and offline.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ha_mcp_engineering.audit import AuditLogger
from ha_mcp_engineering.errors import GovernanceError
from ha_mcp_engineering.f3.models import ExecutionIdentity, ExecutorTiming
from ha_mcp_engineering.f3.operational_adapter import OperationalAdapterError
from ha_mcp_engineering.f3_runtime.runtime import (
    EXPIRED_PRE_INTENT_RETRY_WINDOW_SECONDS,
    F3RuntimeIntegration,
    _dispatch_authority_expiry,
)
from ha_mcp_engineering.governance.models import PlanStatus
from ha_mcp_engineering.governance.storage import ChangePlanRepository
from ha_mcp_engineering.governance.service import ChangeGovernanceService
from ha_mcp_engineering.governance.task_models import ExecutionTaskState

from tests import test_beta37_exact_helper_state as helper_fixtures
from tests import test_hamcp189_authority_cancel as cancel_fixtures
from tests.test_2_1a_operational_backup import Clock
from tests import test_dashboard_update_mvp as dashboard_fixtures


TERMINAL_EVENTS = ["execution_started", "pre_dispatch_terminal"]


class _UnclaimedExpiry:
    """Shared steps; each family supplies ``initialized`` and ``restart``."""

    def declaration(self, task):
        declarations = self.runtime.children.declarations_for_task(task.task_id)
        self.assertEqual(len(declarations), 1)
        return declarations[0]

    def deadline(self, plan_id):
        plan = self.service._load(plan_id)
        return _dispatch_authority_expiry(plan) + timedelta(
            seconds=EXPIRED_PRE_INTENT_RETRY_WINDOW_SECONDS
        )

    def move_to(self, instant):
        delta = (instant - self.clock()).total_seconds()
        self.assertGreaterEqual(delta, 0)
        self.clock.advance(seconds=int(delta) + (delta % 1 > 0))

    async def unclaimed(self):
        """Initialize, capture the would-be record operation, then restart."""

        approved, plan, task = await self.initialized()
        declaration = self.declaration(task)
        self.assertIsNone(self.runtime.children.get(declaration["child_id"]))
        prepared, _requests = await self.runtime._load_prepared(plan, task)
        # Process loss before the first claim: fresh runtime, same storage.
        self.restart()
        self.assertIsNone(self.runtime.children.get(declaration["child_id"]))
        return approved, task, declaration, prepared[0].operation

    def assert_unclaimed_rejected(self, approved, task, declaration, operation):
        record = self.runtime.children.get(declaration["child_id"])
        self.assertIsNotNone(record)
        self.assertTrue(record.terminal)
        self.assertEqual(record.normalized_outcome, "preflight_rejected")
        self.assertEqual(record.task_state, "failed_pre_dispatch")
        self.assertEqual(
            [event["event_type"] for event in record.events], TERMINAL_EVENTS
        )
        self.assertEqual(record.events[-1]["diagnostic_codes"], ["plan_expired"])
        self.assertIsNone(record.dispatch_intent)
        self.assertEqual(record.dispatch_count, 0)
        self.assertFalse(record.lock_tokens)
        # Bound to the declaration: the same identity and operation the
        # executor would have claimed with.
        identity = record.execution_identity()
        self.assertEqual(identity.plan_id, declaration["plan_id"])
        self.assertEqual(identity.attempt_id, declaration["attempt_id"])
        self.assertEqual(identity.request_id, declaration["request_id"])
        self.assertEqual(record.adapter_id, declaration["adapter_id"])
        self.assertEqual(record.operation, operation)
        self.assertEqual(
            record.prepared_operation_hash,
            declaration["prepared_operation_hash"],
        )
        current = self.service._load_task(task.task_id)
        self.assertEqual(current.state, ExecutionTaskState.FAILED_PRE_DISPATCH)
        self.assertEqual(current.terminal_outcome, "failed_pre_dispatch")
        self.assertEqual(
            self.service._load(approved["plan_id"]).status, PlanStatus.FAILED
        )
        self.assertEqual(
            self.runtime.children.runtime(declaration["child_id"])[
                "reconciliation_result"
            ],
            "transition_processed",
        )
        self.assertTrue(self.runtime.readiness_state()["execution_ready"])

    async def expire_and_recover(self, approved, declaration):
        """Recover past the deadline with no service reconciler."""

        self.move_to(self.deadline(approved["plan_id"]) + timedelta(seconds=1))
        before = self.provider_contacts()
        await self.runtime.recover_once("startup")
        record = self.runtime.children.get(declaration["child_id"])
        self.assertIsNotNone(record, "recovery left the declaration unclaimed")
        return before

    async def check_restart_then_expiry(self):
        approved, task, declaration, operation = await self.unclaimed()
        await self.expire_and_recover(approved, declaration)
        self.assertEqual(self.dispatches(), 0)
        self.assert_unclaimed_rejected(approved, task, declaration, operation)
        # Settled: later sweeps neither change the record nor dispatch.
        settled = self.runtime.children.get(declaration["child_id"])
        self.clock.advance(seconds=3600)
        await self.runtime.recover_once("periodic")
        self.restart()
        await self.runtime.recover_once("startup")
        self.assertEqual(
            self.runtime.children.get(declaration["child_id"]).to_dict(),
            settled.to_dict(),
        )
        self.assertEqual(self.dispatches(), 0)
        # A repeated apply of the expired plan cannot dispatch either.  Its
        # refusal type is the pre-existing expired re-apply behavior.
        with self.assertRaises((GovernanceError, OperationalAdapterError)):
            await self.service.apply(approved["plan_id"], approved["plan_hash"])
        self.assertEqual(self.dispatches(), 0)

    async def check_before_deadline_keeps_retrying(self):
        approved, task, declaration, _operation = await self.unclaimed()
        self.move_to(self.deadline(approved["plan_id"]) - timedelta(seconds=1))
        await self.runtime.recover_once("startup")
        self.assertIsNone(self.runtime.children.get(declaration["child_id"]))
        self.assertIn(
            self.service._load_task(task.task_id).state,
            {ExecutionTaskState.CREATED, ExecutionTaskState.PREFLIGHT},
        )
        state = self.runtime.children.runtime(declaration["child_id"])
        self.assertEqual(state["reconciliation_result"], "bounded_retry")
        self.assertFalse(self.runtime.readiness_state()["execution_ready"])
        self.assertEqual(self.dispatches(), 0)


class OperationalUnclaimedTests(_UnclaimedExpiry, unittest.IsolatedAsyncioTestCase):
    """A reload task (operational adapter) lost before its first claim."""

    asyncSetUp = cancel_fixtures.DurableCancelTests.asyncSetUp
    asyncTearDown = cancel_fixtures.DurableCancelTests.asyncTearDown
    build = cancel_fixtures.DurableCancelTests.build
    read_identity = cancel_fixtures.DurableCancelTests.read_identity
    initialized = cancel_fixtures.DurableCancelTests.initialized
    _grant = cancel_fixtures.DurableCancelTests._grant
    provider_contacts = cancel_fixtures.DurableCancelTests.provider_contacts

    def restart(self):
        self.service, self.runtime = self.build()

    def dispatches(self):
        return self.lifecycle.dispatch_count

    async def test_restart_then_expiry_rejects_without_provider_contact(self):
        approved, task, declaration, operation = await self.unclaimed()
        self.move_to(self.deadline(approved["plan_id"]) + timedelta(seconds=1))
        # The provider is down: terminalization must not need it.
        self.provider = "down"
        self.lifecycle.outage = True
        before = self.provider_contacts()
        await self.runtime.recover_once("startup")
        # The whole sweep is local: identity, planning, dispatch and
        # verification counters are all unchanged (M2-R).
        self.assertEqual(self.provider_contacts(), before)
        self.assert_unclaimed_rejected(approved, task, declaration, operation)

    async def test_recovery_sweep_never_invokes_the_identity_reader(self):
        # Review probe (M2-R): an identity reader that fails the test if it
        # is invoked at all, after a restart and past the deadline.
        approved, task, declaration, operation = await self.unclaimed()
        self.move_to(self.deadline(approved["plan_id"]) + timedelta(seconds=1))
        invoked = []

        async def forbidden_identity():
            invoked.append(True)
            raise AssertionError("provider identity contacted after expiry")

        self.runtime.provider_identity_reader = forbidden_identity
        before = self.provider_contacts()
        await self.runtime.recover_once("reviewer-expired-no-child")
        self.assertEqual(invoked, [])
        self.assertEqual(self.provider_contacts(), before)
        self.assert_unclaimed_rejected(approved, task, declaration, operation)

    async def test_slow_identity_does_not_delay_local_termination(self):
        # Review probe (M2-R): a provider identity response that never
        # arrives must not delay or block the expiry outcome.
        approved, task, declaration, operation = await self.unclaimed()
        self.move_to(self.deadline(approved["plan_id"]) + timedelta(seconds=1))
        entered = asyncio.Event()
        release = asyncio.Event()

        async def delayed_identity():
            entered.set()
            await release.wait()
            return await self.read_identity()

        self.runtime.provider_identity_reader = delayed_identity
        pending = asyncio.create_task(
            self.runtime.recover_once("reviewer-slow-identity")
        )
        try:
            await asyncio.wait_for(asyncio.shield(pending), timeout=0.25)
        except TimeoutError:
            self.fail("local expiry waited for provider identity")
        finally:
            release.set()
            await pending
        self.assertFalse(entered.is_set())
        self.assert_unclaimed_rejected(approved, task, declaration, operation)

    async def test_expired_claimed_child_ends_before_provider_contact(self):
        # The same local-first order holds when a first claim exists but its
        # owner was lost (claim expired, no intent): the claimed O9 path also
        # runs before preparation.
        approved, plan, task = await self.initialized()
        declaration = self.declaration(task)
        prepared, _requests = await self.runtime._load_prepared(plan, task)
        self.competing_claim(declaration, prepared[0])
        self.restart()
        self.move_to(self.deadline(approved["plan_id"]) + timedelta(seconds=1))
        self.provider = "down"
        self.lifecycle.outage = True
        before = self.provider_contacts()
        await self.runtime.recover_once("startup")
        self.assertEqual(self.provider_contacts(), before)
        record = self.runtime.children.get(declaration["child_id"])
        self.assertTrue(record.terminal)
        self.assertEqual(record.normalized_outcome, "preflight_rejected")
        self.assertEqual(record.events[-1]["diagnostic_codes"], ["plan_expired"])
        self.assertIsNone(record.dispatch_intent)
        self.assertEqual(record.dispatch_count, 0)
        self.assertEqual(
            self.service._load_task(task.task_id).state,
            ExecutionTaskState.FAILED_PRE_DISPATCH,
        )
        self.assertTrue(self.runtime.readiness_state()["execution_ready"])
        self.assertEqual(self.dispatches(), 0)

    async def test_before_deadline_recovery_still_prepares(self):
        # Preservation: before the deadline the local-first branch does not
        # run; the sweep prepares as before (one identity read) and keeps the
        # bounded retry.
        approved, task, declaration, _operation = await self.unclaimed()
        self.move_to(self.deadline(approved["plan_id"]) - timedelta(seconds=1))
        with patch.object(
            self.runtime,
            "_terminate_expired_pre_intent",
            side_effect=AssertionError("local expiry ran before the deadline"),
        ):
            before = self.identity_reads
            await self.runtime.recover_once("startup")
        self.assertEqual(self.identity_reads, before + 1)
        self.assertIsNone(self.runtime.children.get(declaration["child_id"]))
        self.assertEqual(
            self.runtime.children.runtime(declaration["child_id"])[
                "reconciliation_result"
            ],
            "bounded_retry",
        )
        self.assertEqual(self.dispatches(), 0)

    async def test_terminalization_step_contacts_no_provider(self):
        approved, task, declaration, operation = await self.unclaimed()
        self.move_to(self.deadline(approved["plan_id"]) + timedelta(seconds=1))
        self.provider = "down"
        self.lifecycle.outage = True
        before = self.provider_contacts()
        self.assertTrue(
            self.runtime._terminate_expired_unclaimed(
                declaration, now=self.clock()
            )
        )
        self.assertEqual(self.provider_contacts(), before)
        # The next sweep finds a terminal child: still no provider contact,
        # and readiness is restored.
        await self.runtime.recover_once("startup")
        self.assertEqual(self.provider_contacts(), before)
        self.assert_unclaimed_rejected(approved, task, declaration, operation)

    async def test_restart_then_expiry_settles(self):
        await self.check_restart_then_expiry()

    async def test_before_deadline_keeps_bounded_retry(self):
        await self.check_before_deadline_keeps_retrying()

    async def test_expiry_terminalization_is_in_process_too(self):
        # No restart: the same runtime that initialized the task recovers it.
        approved, _plan, task = await self.initialized()
        declaration = self.declaration(task)
        await self.expire_and_recover(approved, declaration)
        record = self.runtime.children.get(declaration["child_id"])
        self.assertEqual(record.normalized_outcome, "preflight_rejected")
        self.assertEqual(
            self.service._load_task(task.task_id).state,
            ExecutionTaskState.FAILED_PRE_DISPATCH,
        )
        self.assertEqual(self.dispatches(), 0)

    async def test_operator_cancel_keeps_precedence(self):
        # A cancel recorded before expiry owns the child; expiry recovery
        # never replaces its outcome.
        approved, _plan, task = await self.initialized()
        declaration = self.declaration(task)
        await self.service.cancel_execution_task(task.task_id)
        cancelled = self.runtime.children.get(declaration["child_id"])
        self.assertEqual(cancelled.normalized_outcome, "cancelled_pre_dispatch")
        self.restart()
        self.move_to(self.deadline(approved["plan_id"]) + timedelta(seconds=1))
        await self.runtime.recover_once("startup")
        self.assertEqual(
            self.runtime.children.get(declaration["child_id"]).to_dict(),
            cancelled.to_dict(),
        )
        self.assertEqual(
            self.service._load_task(task.task_id).state,
            ExecutionTaskState.CANCELLED_PRE_DISPATCH,
        )
        self.assertEqual(self.dispatches(), 0)

    async def test_cancel_recorded_inside_the_projection_window_wins(self):
        # The cancel check is repeated under the public projection
        # transaction, after the unlocked pre-checks.
        approved, task, declaration, _operation = await self.unclaimed()
        self.move_to(self.deadline(approved["plan_id"]) + timedelta(seconds=1))
        transaction = self.runtime.children.public_projection_transaction
        cancelled = {"value": False}
        recorded = self.runtime._cancel_recorded

        def cancel_lands_first():
            cancelled["value"] = True
            return transaction()

        def cancel_recorded(task_id):
            return cancelled["value"] or recorded(task_id)

        with patch.object(
            self.runtime.children,
            "public_projection_transaction",
            side_effect=cancel_lands_first,
        ), patch.object(
            self.runtime, "_cancel_recorded", side_effect=cancel_recorded
        ):
            ended = self.runtime._terminate_expired_unclaimed(
                declaration, now=self.clock()
            )
        self.assertFalse(ended)
        self.assertTrue(cancelled["value"])
        self.assertIsNone(self.runtime.children.get(declaration["child_id"]))
        self.assertEqual(self.dispatches(), 0)

    def competing_claim(self, declaration, prepared):
        return self.runtime.children.claim(
            identity=ExecutionIdentity(
                task_id=declaration["child_id"],
                plan_id=declaration["plan_id"],
                attempt_id=declaration["attempt_id"],
                request_id=declaration["request_id"],
                owner_id="synthetic-competing-owner",
            ),
            prepared=prepared,
            timing=ExecutorTiming(120, 120, 32, 32),
            now=self.clock(),
        )

    async def test_first_claim_that_wins_is_never_overwritten(self):
        approved, plan, task = await self.initialized()
        declaration = self.declaration(task)
        prepared, _requests = await self.runtime._load_prepared(plan, task)
        self.competing_claim(declaration, prepared[0])
        claimed = self.runtime.children.get(declaration["child_id"])
        self.assertFalse(claimed.terminal)
        # The primitive refuses any existing record...
        self.assertIsNone(
            self.runtime.children.materialize_expired_pre_dispatch(
                identity=ExecutionIdentity(
                    task_id=declaration["child_id"],
                    plan_id=declaration["plan_id"],
                    attempt_id=declaration["attempt_id"],
                    request_id=declaration["request_id"],
                    owner_id="expired-synthetic",
                ),
                adapter_id=declaration["adapter_id"],
                operation=prepared[0].operation,
                target_type=declaration["target_type"],
                target_id=declaration["target_id"],
                prepared_operation_hash=declaration["prepared_operation_hash"],
                diagnostic_codes=("plan_expired",),
                now=self.clock(),
            )
        )
        # ...and the unclaimed path never runs once a record exists.
        self.move_to(self.deadline(approved["plan_id"]) + timedelta(seconds=1))
        self.assertFalse(
            self.runtime._terminate_expired_unclaimed(
                declaration, now=self.clock()
            )
        )
        self.assertEqual(
            self.runtime.children.get(declaration["child_id"]).to_dict(),
            claimed.to_dict(),
        )
        self.assertEqual(self.dispatches(), 0)

    async def test_first_claim_that_loses_finds_a_terminal_record(self):
        approved, plan, task = await self.initialized()
        declaration = self.declaration(task)
        prepared, _requests = await self.runtime._load_prepared(plan, task)
        self.move_to(self.deadline(approved["plan_id"]) + timedelta(seconds=1))
        self.assertTrue(
            self.runtime._terminate_expired_unclaimed(
                declaration, now=self.clock()
            )
        )
        ended = self.runtime.children.get(declaration["child_id"])
        claim = self.competing_claim(declaration, prepared[0])
        self.assertTrue(claim.record.terminal)
        self.assertEqual(
            self.runtime.children.get(declaration["child_id"]).to_dict(),
            ended.to_dict(),
        )
        self.assertEqual(self.dispatches(), 0)


class HelperStateUnclaimedTests(_UnclaimedExpiry, unittest.IsolatedAsyncioTestCase):
    """A helper-state task lost before its first claim."""

    asyncSetUp = helper_fixtures.ExactHelperStateRuntimeTests.asyncSetUp
    asyncTearDown = helper_fixtures.ExactHelperStateRuntimeTests.asyncTearDown
    grant = helper_fixtures.ExactHelperStateRuntimeTests.grant
    create_and_grant = helper_fixtures.ExactHelperStateRuntimeTests.create_and_grant

    async def initialized(self):
        self.helper.set_observed_state("on", self.clock)
        approved = await self.create_and_grant("off")
        plan = self.service._load(approved["plan_id"])
        task, _prepared, _requests = await self.runtime._initialize(
            plan, approved["plan_hash"]
        )
        return approved, plan, task

    def restart(self):
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(Path(self.temp.name) / "plans"),
            configuration_gateway=helper_fixtures.UnusedConfigurationGateway(),
            backup_gateway=None,
            lifecycle_gateway=None,
            helper_state_gateway=self.helper,
            provider_identity_reader=helper_fixtures.forbidden_upstream_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime

    def provider_contacts(self):
        return (self.helper.dispatch_count,)

    def dispatches(self):
        return self.helper.dispatch_count

    async def test_restart_then_expiry_settles(self):
        await self.check_restart_then_expiry()
        self.assertEqual(self.helper.state, "on")

    async def test_before_deadline_keeps_bounded_retry(self):
        await self.check_before_deadline_keeps_retrying()


class DashboardUnclaimedTests(_UnclaimedExpiry, unittest.IsolatedAsyncioTestCase):
    """A dashboard update task lost before its first claim."""

    _plan = dashboard_fixtures.DashboardUpdateRuntimeTests._plan
    _approve = dashboard_fixtures.DashboardUpdateRuntimeTests._approve

    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.clock = Clock()
        self.dashboard = dashboard_fixtures._DashboardGateway()
        self.service = ChangeGovernanceService(
            ChangePlanRepository(self.root / "plans"),
            dashboard_fixtures._UnusedConfigurationGateway(),
            AuditLogger(str(self.root / "audit.jsonl"), "test-access-secret"),
            dashboard_gateway=self.dashboard,
            provider_identity_reader=(
                dashboard_fixtures._unrelated_lifecycle_provider_identity
            ),
            now=self.clock,
        )
        self.restart()
        await self.runtime.recover_once("startup")

    async def asyncTearDown(self):
        self.temporary.cleanup()

    def restart(self):
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=dashboard_fixtures._UnusedConfigurationGateway(),
            backup_gateway=None,
            lifecycle_gateway=None,
            dashboard_gateway=self.dashboard,
            provider_identity_reader=(
                dashboard_fixtures._unrelated_lifecycle_provider_identity
            ),
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime

    async def initialized(self):
        approved = await self._plan()
        await self._approve(approved)
        plan = self.service._load(approved["plan_id"])
        task, _prepared, _requests = await self.runtime._initialize(
            plan, approved["plan_hash"]
        )
        return approved, plan, task

    def dispatches(self):
        return self.dashboard.write_count

    def provider_contacts(self):
        return (self.dashboard.preread_count, self.dashboard.write_count)

    async def test_restart_past_deadline_ends_locally_without_preread(self):
        # Past the deadline a dashboard declaration also ends locally before
        # preparation: no dashboard pre-read and no write (M2-R).
        approved, task, declaration, operation = await self.unclaimed()
        before = await self.expire_and_recover(approved, declaration)
        self.assertEqual(self.provider_contacts(), before)
        self.assert_unclaimed_rejected(approved, task, declaration, operation)
        self.assertEqual(self.dashboard.configuration["title"], "Before")

    async def test_restart_then_expiry_is_rejected_without_the_unclaimed_path(self):
        # Dashboard preparation does not refuse an expired plan, so the
        # existing executor preflight claims the child and rejects it at the
        # first sweep after expiry.  It was never stuck; the unclaimed path is
        # not needed and is not used.
        approved, task, declaration, operation = await self.unclaimed()
        prereads = self.dashboard.preread_count
        self.move_to(
            _dispatch_authority_expiry(self.service._load(approved["plan_id"]))
            + timedelta(seconds=1)
        )
        with patch.object(
            self.runtime,
            "_terminate_expired_unclaimed",
            side_effect=AssertionError("unclaimed path used"),
        ):
            await self.runtime.recover_once("startup")
        record = self.runtime.children.get(declaration["child_id"])
        self.assertEqual(record.normalized_outcome, "preflight_rejected")
        self.assertIn("plan_expired", record.events[-1]["diagnostic_codes"])
        self.assertEqual(record.operation, operation)
        self.assertIsNone(record.dispatch_intent)
        self.assertEqual(
            self.service._load_task(task.task_id).state,
            ExecutionTaskState.FAILED_PRE_DISPATCH,
        )
        self.assertEqual(self.dashboard.preread_count, prereads)
        self.assertEqual(self.dispatches(), 0)
        self.assertEqual(self.dashboard.configuration["title"], "Before")
        self.assertTrue(self.runtime.readiness_state()["execution_ready"])


if __name__ == "__main__":
    unittest.main()
