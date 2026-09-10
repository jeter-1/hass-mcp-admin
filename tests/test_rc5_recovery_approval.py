"""RC5 recovery/approval overlap regressions using real governance and F3.

Derived from the preserved disposable RC5 reproduction (incident source
10a84027f2ef32763aa1f1eb5e44f72227e95c44). These assert corrected behavior.
Only provider I/O and the Core scheduling seam are synthetic.
"""
from __future__ import annotations

import asyncio
import unittest

from tests import test_beta37_exact_helper_state as fixtures
from ha_mcp_engineering.errors import GovernanceError


class PausedCore:
    def __init__(self):
        self.entered = asyncio.Event()
        self.resume = asyncio.Event()
        self.leases = set()
        self.commits = set()
        self.acquired = self.consumed = self.released = self.finished = 0

    async def reconcile_once(self, reason):
        if reason == "mutation_pre_dispatch":
            self.entered.set()
            await self.resume.wait()

    def acquire_f3(self, prepared, **kwargs):
        authority = object()
        self.leases.add(authority)
        self.acquired += 1
        return authority

    def consume(self, authority):
        self.leases.remove(authority)
        commit = object()
        self.commits.add(commit)
        self.consumed += 1
        return commit

    def revalidate(self, authority, commits):
        return commits in self.commits

    def release(self, authority):
        self.leases.remove(authority)
        self.released += 1
        return True

    def finish(self, commits):
        self.commits.remove(commits)
        self.finished += 1
        return True


class RecoveryApprovalTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ExactHelperStateRuntimeTests.asyncSetUp
    asyncTearDown = fixtures.ExactHelperStateRuntimeTests.asyncTearDown
    grant = fixtures.ExactHelperStateRuntimeTests.grant
    create_and_grant = fixtures.ExactHelperStateRuntimeTests.create_and_grant

    async def start_apply(self):
        self.helper.set_observed_state("on", self.clock)
        plan = await self.create_and_grant("off")
        core = PausedCore()
        self.runtime.core_runtime = core
        pending = asyncio.create_task(self.service.apply(plan["plan_id"], plan["plan_hash"]))
        self.addAsyncCleanup(self.finish_pending, pending, core)
        await asyncio.wait_for(core.entered.wait(), 5)
        return plan, core, pending

    @staticmethod
    async def finish_pending(pending, core):
        core.resume.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)

    def child_and_task(self, plan):
        task = self.service.task_repository.get_for_plan(plan["plan_id"])
        declaration = self.runtime.children.declarations_for_task(task.task_id)[0]
        return self.runtime.children.get(declaration["child_id"]), task

    def assert_settled(self, core):
        self.assertEqual(core.leases, set())
        self.assertEqual(core.commits, set())
        health = self.runtime.health()
        self.assertEqual(health["nonterminal_execution_count"], 0)
        self.assertEqual(health["active_normal_lock_count"], 0)
        self.assertEqual(health["active_conflict_hold_count"], 0)
        self.assertEqual(health["fallback_count"], 0)

    async def assert_finishes_once(self, plan, core, pending):
        core.resume.set()
        result = await asyncio.wait_for(pending, 5)
        self.assertEqual(result["task_state"], "succeeded_verified")
        child, task = self.child_and_task(plan)
        self.assertEqual(self.helper.state, "off")
        self.assertEqual(self.helper.dispatch_count, 1)
        self.assertEqual(child.dispatch_count, 1)
        self.assertEqual(len(task.provider_attempts), 1)
        self.assertEqual(self.service._load(plan["plan_id"]).approval.state.value, "consumed")
        # The outer governed apply and the child dispatch each own authority.
        self.assertEqual((core.acquired, core.consumed, core.finished, core.released), (2, 2, 2, 0))
        await self.runtime.recover_once("terminal_parent_control")
        self.assertEqual(self.helper.dispatch_count, 1)
        self.assert_settled(core)

    async def assert_invalid_authority_refused(self, field, value):
        plan, core, pending = await self.start_apply()
        saved = self.service._load(plan["plan_id"])
        # Negative controls change only synthetic authority, never plan status.
        setattr(saved.approval, field, value)
        self.service.repository.save(saved)
        await self.runtime.recover_once("invalid_authority_overlap")
        core.resume.set()
        try:
            await pending
        except GovernanceError:
            pass
        self.clock.advance(seconds=121)
        await self.service.reconcile_execution_tasks()
        await self.runtime.recover_once("invalid_owner_lost")
        self.assertEqual(self.helper.dispatch_count, 0)
        child, _ = self.child_and_task(plan)
        self.assertEqual(child.dispatch_count, 0)
        self.assertIsNone(child.dispatch_intent)
        self.assert_settled(core)

    async def test_revoked_approval_cannot_dispatch(self):
        await self.assert_invalid_authority_refused("state", fixtures.ApprovalState.REJECTED)

    async def test_expired_approval_cannot_dispatch(self):
        await self.assert_invalid_authority_refused("approval_expires_at", self.clock().isoformat())

    async def test_invalid_hash_cannot_dispatch(self):
        await self.assert_invalid_authority_refused("bound_plan_hash", "0" * 64)

    async def test_missing_principal_cannot_dispatch(self):
        await self.assert_invalid_authority_refused("approver_principal", None)

    async def test_policy_binding_mismatch_cannot_dispatch(self):
        await self.assert_invalid_authority_refused("policy_decision_hash", "0" * 64)

    async def test_cancelled_parent_cannot_be_revived_by_recovery(self):
        plan, core, pending = await self.start_apply()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        _, task = self.child_and_task(plan)
        await self.service.cancel_execution_task(task.task_id)
        core.resume.set()
        self.clock.advance(seconds=121)
        await self.runtime.recover_once("cancelled_parent")
        self.assertEqual(self.helper.dispatch_count, 0)
        child, task = self.child_and_task(plan)
        self.assertEqual(task.state.value, "cancelled_pre_dispatch")
        self.assertTrue(child.terminal)
        self.assert_settled(core)

    async def test_post_intent_recovery_defers_to_owner_without_redispatch(self):
        dispatched = asyncio.Event()
        response = asyncio.Event()
        original = self.helper.set_state

        async def held_response(*args, **kwargs):
            result = await original(*args, **kwargs)
            dispatched.set()
            await response.wait()
            return result

        self.helper.set_state = held_response
        plan, core, pending = await self.start_apply()
        core.resume.set()
        await asyncio.wait_for(dispatched.wait(), 5)
        await self.runtime.recover_once("post_intent_active_duplicate")
        self.assertEqual(self.helper.dispatch_count, 1)
        response.set()
        await asyncio.wait_for(pending, 5)
        await self.runtime.recover_once("post_intent_settled")
        child, task = self.child_and_task(plan)
        self.assertEqual(task.state.value, "succeeded_verified")
        self.assertEqual((self.helper.dispatch_count, child.dispatch_count), (1, 1))
        self.assert_settled(core)

    async def test_no_overlap_restores_once(self):
        await self.assert_finishes_once(*await self.start_apply())

    async def test_active_recovery_duplicate_preserves_approval_and_dispatches_once(self):
        plan, core, pending = await self.start_apply()
        before = self.service._load(plan["plan_id"])
        collisions = self.runtime._sweep_collisions
        await asyncio.wait_for(self.runtime.recover_once("overlapping_recovery"), 5)
        during = self.service._load(plan["plan_id"])
        self.assertEqual(during.status.value, "approved")
        self.assertIsNone(self.service._approval_requirement_error(during, "apply"))
        self.assertEqual(self.service.plan_hash(during), plan["plan_hash"])
        self.assertEqual(before.approval.approval_expires_at, during.approval.approval_expires_at)
        self.assertEqual(self.runtime._sweep_collisions, collisions + 1)
        self.assertEqual(self.helper.dispatch_count, 0)
        await self.assert_finishes_once(plan, core, pending)

    async def test_pre_intent_progress_projection_preserves_approval(self):
        plan, core, pending = await self.start_apply()
        _, task = self.child_and_task(plan)
        self.runtime._project(self.service._load(plan["plan_id"]), task)
        self.assertEqual(self.service._load(plan["plan_id"]).status.value, "approved")
        await self.assert_finishes_once(plan, core, pending)

    async def test_owner_loss_before_intent_can_recover_once(self):
        plan, core, pending = await self.start_apply()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertEqual(self.helper.dispatch_count, 0)
        core.resume.set()
        self.clock.advance(seconds=121)
        await self.runtime.recover_once("owner_lost")
        child, task = self.child_and_task(plan)
        self.assertEqual(task.state.value, "succeeded_verified")
        self.assertEqual((self.helper.dispatch_count, child.dispatch_count), (1, 1))
        self.assertEqual(self.helper.state, "off")
        self.assert_settled(core)
