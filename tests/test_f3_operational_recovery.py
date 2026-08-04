"""F3-C2 durable-intent, response-loss, process-loss, and cancellation tests."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.f3.executor import SimulatedProcessLoss
from ha_mcp_engineering.f3.operational_adapter import execute_operational
from ha_mcp_engineering.f3.operational_models import (
    CONTROLLED_RELOAD,
    CREATE_FULL_BACKUP,
    RESTART_ADDON,
    RESTART_HOME_ASSISTANT,
)

from tests.f3_operational_fixtures import (
    NOW,
    PROVIDER_SLUG,
    TASK_ID,
    execution_identity,
    make_context,
    make_executor,
    prepare_context,
)


class MutableClock:
    def __init__(self):
        self.value = NOW

    def __call__(self):
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class RaiseAt:
    def __init__(self, stage: str):
        self.stage = stage
        self.triggered = False

    def __call__(self, stage: str) -> None:
        if stage == self.stage and not self.triggered:
            self.triggered = True
            raise SimulatedProcessLoss(stage)


class FailNthReplace:
    def __init__(self, target: int):
        self.target = target
        self.count = 0

    def __call__(self, stage: str) -> None:
        if stage == "before_execution_replace":
            self.count += 1
            if self.count == self.target:
                raise OSError("synthetic intent persistence failure")


async def execute_until_terminal(executor, context, prepared, *, maximum=5):
    result = None
    for _ in range(maximum):
        result = await execute_operational(
            executor,
            adapter=context.adapter,
            prepared=prepared,
            identity=execution_identity(),
        )
        if result.terminal:
            return result
    raise AssertionError("synthetic operation did not reach a terminal result")


class DurableBoundaryAndResponseLossTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_intent_persistence_failure_invokes_provider_zero_times(self):
        for operation in (
            CREATE_FULL_BACKUP,
            CONTROLLED_RELOAD,
            RESTART_ADDON,
            RESTART_HOME_ASSISTANT,
        ):
            with self.subTest(operation=operation):
                root = self.root / operation
                context = make_context(root, operation)
                prepared = await prepare_context(context)
                executor = make_executor(
                    root, execution_fault_hook=FailNthReplace(4)
                )
                result = await execute_operational(
                    executor,
                    adapter=context.adapter,
                    prepared=prepared,
                    identity=execution_identity(),
                )
                gateway = context.backup if operation == CREATE_FULL_BACKUP else context.lifecycle
                self.assertEqual(result.outcome, "failed_pre_dispatch")
                self.assertEqual(result.dispatch_count, 0)
                self.assertEqual(gateway.provider_dispatches, 0)
                self.assertEqual(gateway.simulated_effects, 0)

    async def test_backup_response_loss_before_effect_never_redispatches(self):
        context = make_context(self.root, CREATE_FULL_BACKUP)
        context.backup.behavior = "response_lost_before_effect"
        prepared = await prepare_context(context)
        result = await execute_until_terminal(
            make_executor(self.root), context, prepared
        )
        self.assertEqual(result.outcome, "manual_review_required")
        self.assertEqual(result.dispatch_count, 1)
        self.assertEqual(context.backup.provider_dispatches, 1)
        self.assertEqual(context.backup.simulated_effects, 0)

    async def test_backup_response_loss_after_effect_verifies_by_inventory(self):
        context = make_context(self.root, CREATE_FULL_BACKUP)
        context.backup.behavior = "response_lost_after_effect"
        prepared = await prepare_context(context)
        result = await execute_operational(
            make_executor(self.root),
            adapter=context.adapter,
            prepared=prepared,
            identity=execution_identity(),
        )
        self.assertEqual(result.outcome, "succeeded_verified")
        self.assertFalse(result.provider_response_received)
        self.assertEqual(context.backup.provider_dispatches, 1)
        self.assertEqual(context.backup.simulated_effects, 1)

    async def test_reload_lost_response_preserves_beta15_readiness_contract(self):
        for behavior in ("response_lost_before_effect", "response_lost_after_effect"):
            with self.subTest(behavior=behavior):
                root = self.root / behavior
                context = make_context(root, CONTROLLED_RELOAD)
                context.lifecycle.behavior = behavior
                prepared = await prepare_context(context)
                result = await execute_operational(
                    make_executor(root),
                    adapter=context.adapter,
                    prepared=prepared,
                    identity=execution_identity(),
                )
                # Beta 15 verifies post-reload readiness and has no direct
                # reload-effect signal.  F3-C2 preserves that limitation.
                self.assertEqual(result.outcome, "succeeded_verified")
                self.assertEqual(context.lifecycle.provider_dispatches, 1)
                self.assertLessEqual(context.lifecycle.simulated_effects, 1)

    async def test_ordinary_addon_lost_response_requires_manual_review(self):
        context = make_context(self.root, RESTART_ADDON)
        context.lifecycle.behavior = "response_lost_after_effect"
        prepared = await prepare_context(context)
        result = await execute_until_terminal(
            make_executor(self.root), context, prepared
        )
        self.assertEqual(result.outcome, "manual_review_required")
        self.assertEqual(context.lifecycle.provider_dispatches, 1)
        self.assertEqual(context.lifecycle.simulated_effects, 1)

    async def test_upstream_addon_lost_response_verifies_by_exact_readmission(self):
        context = make_context(
            self.root,
            RESTART_ADDON,
            target_id=PROVIDER_SLUG,
            target_class="upstream_ha_mcp_addon",
        )
        context.lifecycle.behavior = "response_lost_after_effect"
        prepared = await prepare_context(context)
        result = await execute_operational(
            make_executor(self.root),
            adapter=context.adapter,
            prepared=prepared,
            identity=execution_identity(),
        )
        self.assertEqual(result.outcome, "succeeded_verified")
        self.assertEqual(context.lifecycle.provider_dispatches, 1)
        self.assertEqual(context.lifecycle.simulated_effects, 1)

    async def test_home_assistant_lost_response_observes_but_never_redispatches(self):
        context = make_context(self.root, RESTART_HOME_ASSISTANT)
        context.lifecycle.behavior = "response_lost_after_effect"
        prepared = await prepare_context(context)
        result = await execute_until_terminal(
            make_executor(self.root), context, prepared
        )
        self.assertEqual(result.outcome, "manual_review_required")
        self.assertEqual(result.dispatch_count, 1)
        self.assertEqual(context.lifecycle.provider_dispatches, 1)
        self.assertEqual(context.lifecycle.simulated_effects, 1)
        self.assertTrue(context.ledger.load(TASK_ID).get("outage_observed"))

    async def test_confirmed_provider_rejections_are_terminal_post_dispatch(self):
        for operation in (
            CREATE_FULL_BACKUP,
            CONTROLLED_RELOAD,
            RESTART_ADDON,
            RESTART_HOME_ASSISTANT,
        ):
            root = self.root / operation
            context = make_context(root, operation)
            gateway = context.backup if operation == CREATE_FULL_BACKUP else context.lifecycle
            gateway.behavior = "confirmed_rejection"
            prepared = await prepare_context(context)
            result = await execute_operational(
                make_executor(root),
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(),
            )
            self.assertEqual(result.outcome, "dispatch_failed_confirmed")
            self.assertEqual(result.dispatch_count, 1)
            self.assertEqual(gateway.provider_dispatches, 1)
            self.assertEqual(gateway.simulated_effects, 0)


class OperationalProcessLossTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_process_loss_after_locks_repeats_preflight_then_dispatches_once(self):
        clock = MutableClock()
        context = make_context(self.root, CREATE_FULL_BACKUP)
        prepared = await prepare_context(context)
        fault = RaiseAt("after_lock_acquisition_before_preflight")
        with self.assertRaises(SimulatedProcessLoss):
            await execute_operational(
                make_executor(self.root, now=clock, executor_fault_hook=fault),
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(),
            )
        self.assertEqual(context.backup.provider_dispatches, 0)
        clock.advance(61)
        result = await execute_operational(
            make_executor(self.root, now=clock),
            adapter=context.adapter,
            prepared=prepared,
            identity=execution_identity(owner_id="owner-2"),
        )
        self.assertEqual(result.outcome, "succeeded_verified")
        self.assertEqual(context.backup.provider_dispatches, 1)

    async def test_process_loss_after_preflight_repeats_safely(self):
        clock = MutableClock()
        context = make_context(self.root, CONTROLLED_RELOAD)
        prepared = await prepare_context(context)
        fault = RaiseAt("after_preflight_before_durable_intent")
        with self.assertRaises(SimulatedProcessLoss):
            await execute_operational(
                make_executor(self.root, now=clock, executor_fault_hook=fault),
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(),
            )
        clock.advance(61)
        result = await execute_operational(
            make_executor(self.root, now=clock),
            adapter=context.adapter,
            prepared=prepared,
            identity=execution_identity(owner_id="owner-2"),
        )
        self.assertEqual(result.outcome, "succeeded_verified")
        self.assertEqual(context.lifecycle.provider_dispatches, 1)

    async def test_process_loss_after_intent_before_provider_is_observation_only(self):
        clock = MutableClock()
        context = make_context(self.root, CREATE_FULL_BACKUP)
        prepared = await prepare_context(context)
        fault = RaiseAt("after_durable_intent_before_provider_invocation")
        with self.assertRaises(SimulatedProcessLoss):
            await execute_operational(
                make_executor(self.root, now=clock, executor_fault_hook=fault),
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(),
            )
        self.assertEqual(context.backup.provider_dispatches, 0)
        clock.advance(61)
        result = await execute_until_terminal(
            make_executor(self.root, now=clock), context, prepared
        )
        self.assertEqual(result.outcome, "manual_review_required")
        self.assertEqual(result.dispatch_count, 1)
        self.assertEqual(context.backup.provider_dispatches, 0)
        self.assertEqual(context.backup.simulated_effects, 0)

    async def test_process_loss_after_provider_response_verifies_without_redispatch(self):
        clock = MutableClock()
        context = make_context(self.root, CREATE_FULL_BACKUP)
        prepared = await prepare_context(context)
        fault = RaiseAt("after_provider_response_before_observation")
        with self.assertRaises(SimulatedProcessLoss):
            await execute_operational(
                make_executor(self.root, now=clock, executor_fault_hook=fault),
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(),
            )
        self.assertEqual(context.backup.provider_dispatches, 1)
        clock.advance(61)
        result = await execute_operational(
            make_executor(self.root, now=clock),
            adapter=context.adapter,
            prepared=prepared,
            identity=execution_identity(owner_id="owner-2"),
        )
        self.assertEqual(result.outcome, "succeeded_verified")
        self.assertEqual(context.backup.provider_dispatches, 1)
        self.assertEqual(context.backup.simulated_effects, 1)

    async def test_process_loss_after_observation_resumes_verification_only(self):
        clock = MutableClock()
        context = make_context(self.root, RESTART_ADDON)
        prepared = await prepare_context(context)
        fault = RaiseAt("after_observation_before_verification")
        with self.assertRaises(SimulatedProcessLoss):
            await execute_operational(
                make_executor(self.root, now=clock, executor_fault_hook=fault),
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(),
            )
        clock.advance(61)
        result = await execute_operational(
            make_executor(self.root, now=clock),
            adapter=context.adapter,
            prepared=prepared,
            identity=execution_identity(owner_id="owner-2"),
        )
        self.assertEqual(result.outcome, "succeeded_verified")
        self.assertEqual(context.lifecycle.provider_dispatches, 1)

    async def test_process_loss_after_verified_result_repairs_release_only(self):
        context = make_context(self.root, CREATE_FULL_BACKUP)
        prepared = await prepare_context(context)
        fault = RaiseAt("after_verified_result_before_lock_release")
        executor = make_executor(self.root, executor_fault_hook=fault)
        with self.assertRaises(SimulatedProcessLoss):
            await execute_operational(
                executor,
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(),
            )
        result = await execute_operational(
            make_executor(self.root),
            adapter=context.adapter,
            prepared=prepared,
            identity=execution_identity(),
        )
        self.assertEqual(result.outcome, "succeeded_verified")
        self.assertEqual(context.backup.provider_dispatches, 1)
        self.assertEqual(context.backup.simulated_effects, 1)


class OperationalCancellationAndHoldTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_preintent_cancellation_is_terminal_and_dispatch_free(self):
        context = make_context(self.root, RESTART_ADDON)
        prepared = await prepare_context(context)
        executor = make_executor(
            self.root,
            executor_fault_hook=RaiseAt("after_lock_acquisition_before_preflight"),
        )
        with self.assertRaises(SimulatedProcessLoss):
            await execute_operational(
                executor,
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(),
            )
        self.assertTrue(await executor.cancel(TASK_ID))
        self.assertEqual(context.lifecycle.provider_dispatches, 0)
        self.assertEqual(context.lifecycle.simulated_effects, 0)

    async def test_postintent_cancellation_is_rejected(self):
        context = make_context(self.root, CREATE_FULL_BACKUP)
        prepared = await prepare_context(context)
        executor = make_executor(
            self.root,
            executor_fault_hook=RaiseAt("after_durable_intent_before_provider_invocation"),
        )
        with self.assertRaises(SimulatedProcessLoss):
            await execute_operational(
                executor,
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(),
            )
        self.assertFalse(await executor.cancel(TASK_ID))
        self.assertEqual(context.backup.provider_dispatches, 0)

    async def test_manual_review_current_f3a_hold_scope_is_disclosed_blocker(self):
        context = make_context(self.root, RESTART_ADDON)
        context.lifecycle.behavior = "response_lost_after_effect"
        prepared = await prepare_context(context)
        executor = make_executor(self.root)
        result = await execute_until_terminal(executor, context, prepared)
        self.assertEqual(result.outcome, "manual_review_required")
        records = executor.lock_store.records()
        actual_holds = {record.key for record in records if record.conflict_hold}
        declared_holds = set(prepared.manual_review_hold_keys)
        self.assertTrue(declared_holds < actual_holds)
        self.assertIn(f"addon:{PROVIDER_SLUG}", actual_holds)
        # F3-D must add selective promotion/release before activation.  F3-C2
        # does not patch or fork the accepted F3-A lock core.


if __name__ == "__main__":
    unittest.main()
