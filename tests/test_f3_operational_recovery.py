"""F3-C2 durable-intent, response-loss, process-loss, and cancellation tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.f3.executor import (
    PreIntentRetryRequired,
    SimulatedProcessLoss,
)
from ha_mcp_engineering.f3.contracts import RecoveryContext
from ha_mcp_engineering.f3.operational_adapter import (
    OperationalAdapterError,
    execute_operational,
)
from ha_mcp_engineering.f3.operational_models import (
    CONTROLLED_RELOAD,
    CREATE_FULL_BACKUP,
    RESTART_ADDON,
    RESTART_HOME_ASSISTANT,
    SET_INPUT_BOOLEAN_STATE,
)
from ha_mcp_engineering.f3_configuration.locks import (
    unconstrained_helper_dependency_lock_key,
)
from ha_mcp_engineering.f3.persistence import DurableExecutionRepository

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


class RecordIntentBoundary:
    def __init__(self, trace: list[str]):
        self.trace = trace

    def __call__(self, stage: str) -> None:
        if stage == "after_durable_intent_before_provider_invocation":
            self.trace.append("intent")


async def execute_until_terminal(
    executor, context, prepared, *, maximum=5, owner_id="owner-1"
):
    result = None
    for _ in range(maximum):
        result = await execute_operational(
            executor,
            adapter=context.adapter,
            prepared=prepared,
            identity=execution_identity(owner_id=owner_id),
            approval_consumption=context.approval.consume,
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

    async def test_preflight_never_consumes_approval_for_any_operation(self):
        for operation in (
            CREATE_FULL_BACKUP,
            CONTROLLED_RELOAD,
            RESTART_ADDON,
            RESTART_HOME_ASSISTANT,
        ):
            with self.subTest(operation=operation):
                context = make_context(self.root / operation, operation)
                prepared = await prepare_context(context)
                result = await context.adapter.preflight(
                    prepared,
                    acquired_locks=context.adapter.lock_requests(prepared),
                )
                self.assertTrue(result.eligible)
                self.assertEqual(context.approval.callback_count, 0)

    async def test_provider_rejection_before_intent_consumes_no_approval(self):
        for operation in (
            CREATE_FULL_BACKUP,
            CONTROLLED_RELOAD,
            RESTART_ADDON,
            RESTART_HOME_ASSISTANT,
        ):
            with self.subTest(operation=operation):
                root = self.root / f"provider-{operation}"
                context = make_context(root, operation)
                gateway = (
                    context.backup
                    if operation == CREATE_FULL_BACKUP
                    else context.lifecycle
                )
                gateway.behavior = "provider_unavailable"
                prepared = await prepare_context(context)
                result = await execute_operational(
                    make_executor(root, prepared=prepared),
                    adapter=context.adapter,
                    prepared=prepared,
                    identity=execution_identity(),
                    approval_consumption=context.approval.consume,
                )
                self.assertEqual(
                    result.outcome, "provider_unavailable_pre_dispatch"
                )
                self.assertEqual(context.approval.callback_count, 0)
                self.assertEqual(gateway.provider_dispatches, 0)

    async def test_approval_intent_provider_order_is_exact_for_all_operations(self):
        for operation in (
            CREATE_FULL_BACKUP,
            CONTROLLED_RELOAD,
            RESTART_ADDON,
            RESTART_HOME_ASSISTANT,
        ):
            with self.subTest(operation=operation):
                root = self.root / f"order-{operation}"
                context = make_context(root, operation)
                prepared = await prepare_context(context)
                await execute_operational(
                    make_executor(
                        root,
                        prepared=prepared,
                        executor_fault_hook=RecordIntentBoundary(context.trace),
                    ),
                    adapter=context.adapter,
                    prepared=prepared,
                    identity=execution_identity(),
                    approval_consumption=context.approval.consume,
                )
                boundary = [
                    item
                    for item in context.trace
                    if item in {"approval", "intent", "provider"}
                ]
                self.assertEqual(boundary[:3], ["approval", "intent", "provider"])
                self.assertNotIn(
                    "evidence_read",
                    context.trace[: context.trace.index("provider")],
                )
                gateway = (
                    context.backup
                    if operation == CREATE_FULL_BACKUP
                    else context.lifecycle
                )
                self.assertEqual(gateway.provider_dispatches, 1)

    async def test_approval_failure_invokes_provider_zero_times(self):
        for operation in (
            CREATE_FULL_BACKUP,
            CONTROLLED_RELOAD,
            RESTART_ADDON,
            RESTART_HOME_ASSISTANT,
        ):
            with self.subTest(operation=operation):
                root = self.root / f"approval-{operation}"
                context = make_context(root, operation)
                context.approval.fail = True
                prepared = await prepare_context(context)
                with self.assertRaises(PreIntentRetryRequired):
                    await execute_operational(
                        make_executor(root, prepared=prepared),
                        adapter=context.adapter,
                        prepared=prepared,
                        identity=execution_identity(),
                        approval_consumption=context.approval.consume,
                    )
                gateway = (
                    context.backup
                    if operation == CREATE_FULL_BACKUP
                    else context.lifecycle
                )
                self.assertEqual(gateway.provider_dispatches, 0)
                self.assertEqual(gateway.simulated_effects, 0)

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
                    root,
                    prepared=prepared,
                    execution_fault_hook=FailNthReplace(4),
                )
                with self.assertRaises(PreIntentRetryRequired):
                    await execute_operational(
                        executor,
                        adapter=context.adapter,
                        prepared=prepared,
                        identity=execution_identity(),
                        approval_consumption=context.approval.consume,
                    )
                gateway = context.backup if operation == CREATE_FULL_BACKUP else context.lifecycle
                self.assertEqual(gateway.provider_dispatches, 0)
                self.assertEqual(gateway.simulated_effects, 0)
                self.assertEqual(context.approval.consumption_count, 1)
                result = await execute_operational(
                    make_executor(root, prepared=prepared),
                    adapter=context.adapter,
                    prepared=prepared,
                    identity=execution_identity(owner_id="owner-2"),
                    approval_consumption=context.approval.consume,
                )
                self.assertEqual(result.dispatch_count, 1)
                self.assertEqual(gateway.provider_dispatches, 1)
                self.assertEqual(context.approval.consumption_count, 1)

    async def test_backup_response_loss_before_effect_never_redispatches(self):
        context = make_context(self.root, CREATE_FULL_BACKUP)
        context.backup.behavior = "response_lost_before_effect"
        prepared = await prepare_context(context)
        result = await execute_until_terminal(
            make_executor(self.root, prepared=prepared), context, prepared
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
            make_executor(self.root, prepared=prepared),
            adapter=context.adapter,
            prepared=prepared,
            identity=execution_identity(), approval_consumption=context.approval.consume,
        )
        self.assertEqual(result.outcome, "succeeded_verified")
        self.assertFalse(result.provider_response_received)
        self.assertEqual(context.backup.provider_dispatches, 1)
        self.assertEqual(context.backup.simulated_effects, 1)

    async def test_reload_lost_response_never_verifies_from_readiness_alone(self):
        for behavior in ("response_lost_before_effect", "response_lost_after_effect"):
            with self.subTest(behavior=behavior):
                root = self.root / behavior
                context = make_context(root, CONTROLLED_RELOAD)
                context.lifecycle.behavior = behavior
                prepared = await prepare_context(context)
                result = await execute_operational(
                    make_executor(root, prepared=prepared),
                    adapter=context.adapter,
                    prepared=prepared,
                    identity=execution_identity(), approval_consumption=context.approval.consume,
                )
                # Readiness is necessary but cannot prove that a reload
                # occurred after a lost provider response.
                self.assertIn(
                    result.outcome,
                    {"observing", "manual_review_required"},
                )
                self.assertEqual(context.lifecycle.provider_dispatches, 1)
                self.assertLessEqual(context.lifecycle.simulated_effects, 1)

    async def test_ordinary_addon_lost_response_requires_manual_review(self):
        context = make_context(self.root, RESTART_ADDON)
        context.lifecycle.behavior = "response_lost_after_effect"
        prepared = await prepare_context(context)
        result = await execute_until_terminal(
            make_executor(self.root, prepared=prepared), context, prepared
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
            make_executor(self.root, prepared=prepared),
            adapter=context.adapter,
            prepared=prepared,
            identity=execution_identity(), approval_consumption=context.approval.consume,
        )
        self.assertEqual(result.outcome, "succeeded_verified")
        self.assertEqual(context.lifecycle.provider_dispatches, 1)
        self.assertEqual(context.lifecycle.simulated_effects, 1)

    async def test_home_assistant_lost_response_observes_but_never_redispatches(self):
        context = make_context(self.root, RESTART_HOME_ASSISTANT)
        context.lifecycle.behavior = "response_lost_after_effect"
        prepared = await prepare_context(context)
        result = await execute_until_terminal(
            make_executor(self.root, prepared=prepared), context, prepared
        )
        self.assertEqual(result.outcome, "manual_review_required")
        self.assertEqual(result.dispatch_count, 1)
        self.assertEqual(context.lifecycle.provider_dispatches, 1)
        self.assertEqual(context.lifecycle.simulated_effects, 1)
        self.assertTrue(context.evidence.operation_evidence.get("outage_observed"))

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
                make_executor(root, prepared=prepared),
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(), approval_consumption=context.approval.consume,
            )
            self.assertEqual(result.outcome, "dispatch_failed_confirmed")
            self.assertEqual(result.dispatch_count, 1)
            self.assertEqual(gateway.provider_dispatches, 1)
            self.assertEqual(gateway.simulated_effects, 0)


class OperationalEvidenceDeadlineBindingTests(unittest.IsolatedAsyncioTestCase):
    DURATIONS = {
        CREATE_FULL_BACKUP: 86_400,
        CONTROLLED_RELOAD: 900,
        RESTART_ADDON: 1_800,
        RESTART_HOME_ASSISTANT: 1_800,
    }

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _gateway(context, operation):
        return (
            context.backup
            if operation == CREATE_FULL_BACKUP
            else context.lifecycle
        )

    async def test_exact_operation_timing_commits_and_preserves_deadline(self):
        for operation, duration in self.DURATIONS.items():
            with self.subTest(operation=operation):
                root = self.root / operation
                context = make_context(root, operation)
                prepared = await prepare_context(context)
                executor = make_executor(root, prepared=prepared)
                self.assertEqual(
                    executor.executor_timing.post_dispatch_evidence_seconds,
                    duration,
                )
                first = await execute_operational(
                    executor,
                    adapter=context.adapter,
                    prepared=prepared,
                    identity=execution_identity(),
                    approval_consumption=context.approval.consume,
                )
                record = DurableExecutionRepository(root).get(TASK_ID)
                self.assertIsNotNone(record)
                self.assertIsNotNone(record.dispatch_intent)
                intent_time = datetime.fromisoformat(
                    record.dispatch_intent["committed_at"]
                )
                deadline = datetime.fromisoformat(
                    record.dispatch_intent["evidence_deadline"]
                )
                self.assertEqual(
                    deadline - intent_time,
                    timedelta(seconds=duration),
                )
                original_deadline = record.dispatch_intent[
                    "evidence_deadline"
                ]
                duplicate = await execute_operational(
                    executor,
                    adapter=context.adapter,
                    prepared=prepared,
                    identity=execution_identity(),
                    approval_consumption=context.approval.consume,
                )
                after_duplicate = DurableExecutionRepository(root).get(TASK_ID)
                self.assertEqual(
                    after_duplicate.dispatch_intent["evidence_deadline"],
                    original_deadline,
                )
                self.assertEqual(first.dispatch_count, 1)
                self.assertEqual(duplicate.dispatch_count, 1)
                self.assertEqual(
                    self._gateway(context, operation).provider_dispatches,
                    1,
                )
                self.assertEqual(context.approval.consumption_count, 1)

    async def test_short_long_and_universal_timings_fail_before_claim(self):
        for operation, duration in self.DURATIONS.items():
            for label, configured in (
                ("short", duration - 1),
                ("long", duration + 1),
                ("universal_3600", 3_600),
            ):
                with self.subTest(operation=operation, case=label):
                    root = self.root / operation / label
                    context = make_context(root, operation)
                    prepared = await prepare_context(context)
                    executor = make_executor(root, prepared=prepared)
                    executor.executor_timing = replace(
                        executor.executor_timing,
                        post_dispatch_evidence_seconds=configured,
                    )
                    with self.assertRaises(OperationalAdapterError) as caught:
                        await execute_operational(
                            executor,
                            adapter=context.adapter,
                            prepared=prepared,
                            identity=execution_identity(),
                            approval_consumption=context.approval.consume,
                        )
                    self.assertEqual(
                        caught.exception.category,
                        "executor_evidence_deadline_mismatch",
                    )
                    self.assertIsNone(
                        DurableExecutionRepository(root).get(TASK_ID)
                    )
                    gateway = self._gateway(context, operation)
                    self.assertEqual(context.approval.callback_count, 0)
                    self.assertEqual(gateway.provider_dispatches, 0)
                    self.assertEqual(gateway.simulated_effects, 0)

    async def test_reconstruction_preserves_exact_original_deadline(self):
        for operation in self.DURATIONS:
            with self.subTest(operation=operation):
                root = self.root / operation
                clock = MutableClock()
                context = make_context(root, operation)
                prepared = await prepare_context(context)
                with self.assertRaises(SimulatedProcessLoss):
                    await execute_operational(
                        make_executor(
                            root,
                            prepared=prepared,
                            now=clock,
                            executor_fault_hook=RaiseAt(
                                "after_durable_intent_before_provider_invocation"
                            ),
                        ),
                        adapter=context.adapter,
                        prepared=prepared,
                        identity=execution_identity(),
                        approval_consumption=context.approval.consume,
                    )
                before = DurableExecutionRepository(root).get(TASK_ID)
                original_deadline = before.dispatch_intent[
                    "evidence_deadline"
                ]
                clock.advance(61)
                await execute_operational(
                    make_executor(root, prepared=prepared, now=clock),
                    adapter=context.adapter,
                    prepared=prepared,
                    identity=execution_identity(owner_id="owner-2"),
                    approval_consumption=context.approval.consume,
                )
                after = DurableExecutionRepository(root).get(TASK_ID)
                self.assertEqual(
                    after.dispatch_intent["evidence_deadline"],
                    original_deadline,
                )
                self.assertEqual(after.dispatch_count, 1)
                self.assertEqual(
                    self._gateway(context, operation).provider_dispatches,
                    0,
                )

    async def test_mutated_projection_or_recovery_deadline_fails_closed(self):
        for operation in self.DURATIONS:
            with self.subTest(operation=operation):
                root = self.root / operation
                clock = MutableClock()
                context = make_context(root, operation)
                prepared = await prepare_context(context)
                with self.assertRaises(SimulatedProcessLoss):
                    await execute_operational(
                        make_executor(
                            root,
                            prepared=prepared,
                            now=clock,
                            executor_fault_hook=RaiseAt(
                                "after_durable_intent_before_provider_invocation"
                            ),
                        ),
                        adapter=context.adapter,
                        prepared=prepared,
                        identity=execution_identity(),
                        approval_consumption=context.approval.consume,
                    )
                record = DurableExecutionRepository(root).get(TASK_ID)
                exact_deadline = datetime.fromisoformat(
                    record.dispatch_intent["evidence_deadline"]
                )
                changed_deadline = (
                    exact_deadline + timedelta(seconds=1)
                ).isoformat()
                recovery = await context.adapter.recover(
                    prepared,
                    context=RecoveryContext(
                        dispatch_intent_recorded=True,
                        provider_invocation_may_have_occurred=True,
                        provider_response_received=False,
                        prior_observation_attempts=0,
                        prior_verification_attempts=0,
                        post_dispatch_deadline=changed_deadline,
                    ),
                )
                self.assertEqual(
                    recovery.outcome, "manual_review_required"
                )

                context.evidence.record_observation(
                    evidence_deadline=changed_deadline,
                    selective_hold_keys=prepared.selective_hold_keys,
                )
                clock.advance(61)
                result = await execute_operational(
                    make_executor(root, prepared=prepared, now=clock),
                    adapter=context.adapter,
                    prepared=prepared,
                    identity=execution_identity(owner_id="owner-2"),
                    approval_consumption=context.approval.consume,
                )
                after = DurableExecutionRepository(root).get(TASK_ID)
                self.assertEqual(result.outcome, "manual_review_required")
                self.assertEqual(after.dispatch_count, 1)
                self.assertEqual(
                    after.dispatch_intent["evidence_deadline"],
                    exact_deadline.isoformat(),
                )
                self.assertEqual(
                    context.evidence.operation_evidence[
                        "selective_hold_keys"
                    ],
                    prepared.selective_hold_keys,
                )
                self.assertEqual(
                    self._gateway(context, operation).provider_dispatches,
                    0,
                )

    async def test_deadline_threshold_is_inclusive_and_never_releases_hold(self):
        for operation, duration in self.DURATIONS.items():
            with self.subTest(operation=operation):
                root = self.root / operation
                clock = MutableClock()
                context = make_context(root, operation)
                context.adapter.now = clock
                prepared = await prepare_context(context)
                with self.assertRaises(SimulatedProcessLoss):
                    await execute_operational(
                        make_executor(
                            root,
                            prepared=prepared,
                            now=clock,
                            executor_fault_hook=RaiseAt(
                                "after_durable_intent_before_provider_invocation"
                            ),
                        ),
                        adapter=context.adapter,
                        prepared=prepared,
                        identity=execution_identity(),
                        approval_consumption=context.approval.consume,
                    )
                record = DurableExecutionRepository(root).get(TASK_ID)
                deadline = record.dispatch_intent["evidence_deadline"]
                context.evidence.record_observation(
                    selective_hold_keys=prepared.selective_hold_keys
                )
                recovery_context = RecoveryContext(
                    dispatch_intent_recorded=True,
                    provider_invocation_may_have_occurred=True,
                    provider_response_received=False,
                    prior_observation_attempts=0,
                    prior_verification_attempts=0,
                    post_dispatch_deadline=deadline,
                )
                clock.advance(duration - 1)
                before = await context.adapter.recover(
                    prepared, context=recovery_context
                )
                self.assertEqual(before.outcome, "observing")
                clock.advance(1)
                at_deadline = await context.adapter.recover(
                    prepared, context=recovery_context
                )
                self.assertEqual(
                    at_deadline.outcome, "manual_review_required"
                )
                projection = context.evidence.read(prepared)
                self.assertEqual(
                    projection.selective_hold_keys,
                    prepared.selective_hold_keys,
                )
                self.assertEqual(
                    self._gateway(context, operation).provider_dispatches,
                    0,
                )


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
                make_executor(self.root, prepared=prepared, now=clock, executor_fault_hook=fault),
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(), approval_consumption=context.approval.consume,
            )
        self.assertEqual(context.backup.provider_dispatches, 0)
        clock.advance(61)
        result = await execute_until_terminal(
            make_executor(self.root, prepared=prepared, now=clock),
            context,
            prepared,
            owner_id="owner-2",
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
                make_executor(self.root, prepared=prepared, now=clock, executor_fault_hook=fault),
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(), approval_consumption=context.approval.consume,
            )
        clock.advance(61)
        result = await execute_until_terminal(
            make_executor(self.root, prepared=prepared, now=clock),
            context,
            prepared,
            owner_id="owner-2",
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
                make_executor(self.root, prepared=prepared, now=clock, executor_fault_hook=fault),
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(), approval_consumption=context.approval.consume,
            )
        self.assertEqual(context.backup.provider_dispatches, 0)
        clock.advance(61)
        result = await execute_until_terminal(
            make_executor(self.root, prepared=prepared, now=clock), context, prepared
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
                make_executor(self.root, prepared=prepared, now=clock, executor_fault_hook=fault),
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(), approval_consumption=context.approval.consume,
            )
        self.assertEqual(context.backup.provider_dispatches, 1)
        clock.advance(61)
        result = await execute_until_terminal(
            make_executor(self.root, prepared=prepared, now=clock),
            context,
            prepared,
            owner_id="owner-2",
        )
        self.assertEqual(result.outcome, "succeeded_verified")
        self.assertEqual(context.backup.provider_dispatches, 1)
        self.assertEqual(context.backup.simulated_effects, 1)

    async def test_clean_helper_recovery_restores_exact_stability_fence_once(self):
        clock = MutableClock()
        context = make_context(
            self.root,
            SET_INPUT_BOOLEAN_STATE,
            conservative_helper_dependency=False,
        )
        prepared = await prepare_context(context)
        first_executor = make_executor(
            self.root,
            prepared=prepared,
            now=clock,
            executor_fault_hook=RaiseAt(
                "after_provider_response_before_observation"
            ),
        )
        with self.assertRaises(SimulatedProcessLoss):
            await execute_operational(
                first_executor,
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(),
                approval_consumption=context.approval.consume,
            )

        fence_key = unconstrained_helper_dependency_lock_key()
        before = DurableExecutionRepository(self.root).get(TASK_ID)
        before_fence = next(
            token for token in before.lock_tokens if token["key"] == fence_key
        )
        self.assertEqual("shared", before_fence["mode"])
        self.assertEqual(1, context.adapter.strategies[
            SET_INPUT_BOOLEAN_STATE
        ].gateway.provider_dispatches)

        clock.advance(61)
        restarted = make_executor(self.root, prepared=prepared, now=clock)
        result = await execute_until_terminal(
            restarted,
            context,
            prepared,
            owner_id="owner-2",
        )
        after = DurableExecutionRepository(self.root).get(TASK_ID)
        after_fences = [
            token for token in after.lock_tokens if token["key"] == fence_key
        ]
        self.assertEqual(1, len(after_fences))
        self.assertEqual("shared", after_fences[0]["mode"])
        self.assertGreater(
            after_fences[0]["generation"], before_fence["generation"]
        )
        self.assertEqual(
            after.lock_tokens,
            after.dispatch_intent["lock_tokens"],
        )
        self.assertEqual("succeeded_verified", result.outcome)
        self.assertEqual((), restarted.lock_store.records())
        self.assertEqual(1, context.adapter.strategies[
            SET_INPUT_BOOLEAN_STATE
        ].gateway.provider_dispatches)

        duplicate = await execute_operational(
            restarted,
            adapter=context.adapter,
            prepared=prepared,
            identity=execution_identity(owner_id="owner-2"),
            approval_consumption=context.approval.consume,
        )
        self.assertEqual("succeeded_verified", duplicate.outcome)
        self.assertEqual((), restarted.lock_store.records())
        self.assertEqual(1, context.adapter.strategies[
            SET_INPUT_BOOLEAN_STATE
        ].gateway.provider_dispatches)

    async def test_process_loss_after_observation_resumes_verification_only(self):
        clock = MutableClock()
        context = make_context(self.root, RESTART_ADDON)
        prepared = await prepare_context(context)
        fault = RaiseAt("after_observation_before_verification")
        with self.assertRaises(SimulatedProcessLoss):
            await execute_operational(
                make_executor(self.root, prepared=prepared, now=clock, executor_fault_hook=fault),
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(), approval_consumption=context.approval.consume,
            )
        clock.advance(61)
        result = await execute_until_terminal(
            make_executor(self.root, prepared=prepared, now=clock),
            context,
            prepared,
            owner_id="owner-2",
        )
        self.assertEqual(result.outcome, "succeeded_verified")
        self.assertEqual(context.lifecycle.provider_dispatches, 1)

    async def test_process_loss_after_verified_result_repairs_release_only(self):
        context = make_context(self.root, CREATE_FULL_BACKUP)
        prepared = await prepare_context(context)
        fault = RaiseAt("after_verified_result_before_lock_release")
        executor = make_executor(self.root, prepared=prepared, executor_fault_hook=fault)
        with self.assertRaises(SimulatedProcessLoss):
            await execute_operational(
                executor,
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(), approval_consumption=context.approval.consume,
            )
        result = await execute_operational(
            make_executor(self.root, prepared=prepared),
            adapter=context.adapter,
            prepared=prepared,
            identity=execution_identity(), approval_consumption=context.approval.consume,
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
            prepared=prepared,
            executor_fault_hook=RaiseAt("after_lock_acquisition_before_preflight"),
        )
        with self.assertRaises(SimulatedProcessLoss):
            await execute_operational(
                executor,
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(), approval_consumption=context.approval.consume,
            )
        self.assertTrue(await executor.cancel(TASK_ID))
        self.assertEqual(context.lifecycle.provider_dispatches, 0)
        self.assertEqual(context.lifecycle.simulated_effects, 0)

    async def test_postintent_cancellation_is_rejected(self):
        context = make_context(self.root, CREATE_FULL_BACKUP)
        prepared = await prepare_context(context)
        executor = make_executor(
            self.root,
            prepared=prepared,
            executor_fault_hook=RaiseAt("after_durable_intent_before_provider_invocation"),
        )
        with self.assertRaises(SimulatedProcessLoss):
            await execute_operational(
                executor,
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(), approval_consumption=context.approval.consume,
            )
        self.assertFalse(await executor.cancel(TASK_ID))
        self.assertEqual(context.backup.provider_dispatches, 0)

    async def test_manual_review_current_f3a_hold_scope_is_disclosed_blocker(self):
        context = make_context(self.root, RESTART_ADDON)
        context.lifecycle.behavior = "response_lost_after_effect"
        prepared = await prepare_context(context)
        executor = make_executor(self.root, prepared=prepared)
        result = await execute_until_terminal(executor, context, prepared)
        self.assertEqual(result.outcome, "manual_review_required")
        records = executor.lock_store.records()
        actual_holds = {record.key for record in records if record.conflict_hold}
        declared_holds = set(prepared.selective_hold_keys)
        self.assertTrue(declared_holds < actual_holds)
        self.assertIn(f"addon:{PROVIDER_SLUG}", actual_holds)
        # F3-D must add selective promotion/release before activation.  F3-C2
        # does not patch or fork the accepted F3-A lock core.


class OperationalEvidenceAuthorityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_no_independent_operational_ledger_or_evidence_writer_remains(self):
        runtime = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering" / "f3"
        sources = "\n".join(
            (runtime / name).read_text(encoding="utf-8")
            for name in (
                "operational_adapter.py",
                "operational_models.py",
                "operational_strategies.py",
            )
        )
        self.assertNotIn("OperationalRecoveryLedger", sources)
        self.assertNotIn("recovery_ledger", sources)
        self.assertNotIn("evidence_reader.merge", sources)
        self.assertNotIn("manual_review_hold_max_seconds", sources)

    async def test_corrupt_authoritative_evidence_fails_closed_without_redispatch(self):
        clock = MutableClock()
        context = make_context(self.root, CREATE_FULL_BACKUP)
        prepared = await prepare_context(context)
        with self.assertRaises(SimulatedProcessLoss):
            await execute_operational(
                make_executor(
                    self.root,
                    prepared=prepared,
                    now=clock,
                    executor_fault_hook=RaiseAt(
                        "after_durable_intent_before_provider_invocation"
                    ),
                ),
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(),
                approval_consumption=context.approval.consume,
            )
        context.evidence.corrupt = True
        clock.advance(61)
        result = await execute_until_terminal(
            make_executor(self.root, prepared=prepared, now=clock),
            context,
            prepared,
            owner_id="owner-2",
        )
        self.assertEqual(result.outcome, "manual_review_required")
        self.assertEqual(result.dispatch_count, 1)
        self.assertEqual(context.backup.provider_dispatches, 0)

    async def test_jsonl_projection_can_never_be_execution_authority(self):
        context = make_context(self.root, CREATE_FULL_BACKUP)
        prepared = await prepare_context(context)
        projection = context.evidence.read(prepared)
        with self.assertRaisesRegex(ValueError, "JSONL"):
            replace(projection, jsonl_authoritative=True).validate(prepared)

    async def test_missing_optional_provider_ids_never_authorize_retry(self):
        context = make_context(self.root, CREATE_FULL_BACKUP)
        context.backup.behavior = "response_lost_after_effect"
        prepared = await prepare_context(context)
        result = await execute_operational(
            make_executor(self.root, prepared=prepared),
            adapter=context.adapter,
            prepared=prepared,
            identity=execution_identity(),
            approval_consumption=context.approval.consume,
        )
        projection = context.evidence.read(prepared)
        self.assertIsNone(projection.provider_operation_id)
        self.assertIsNone(projection.provider_backup_id)
        self.assertEqual(result.outcome, "succeeded_verified")
        self.assertEqual(context.backup.provider_dispatches, 1)


if __name__ == "__main__":
    unittest.main()
