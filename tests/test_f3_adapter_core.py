"""Shared executor lifecycle, durable intent, and reconstruction tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BETA_DIR))

from f3_contracts.operation_adapter import (  # noqa: E402
    NormalizedOperationOutcome,
)
from ha_mcp_engineering.f3.executor import (  # noqa: E402
    SharedOperationExecutor,
    SimulatedProcessLoss,
)
from ha_mcp_engineering.f3.locks import DurableLockStore  # noqa: E402
from ha_mcp_engineering.f3.models import (  # noqa: E402
    NORMALIZED_OUTCOME_TO_TASK_STATE,
    ExecutionIdentity,
    ExecutorTiming,
    LockOwner,
    LockTiming,
)
from ha_mcp_engineering.f3.observability import (  # noqa: E402
    BoundedEventRecorder,
    ExecutorMetrics,
)
from ha_mcp_engineering.f3.persistence import (  # noqa: E402
    DurableExecutionRepository,
)
from tests.f3_synthetic_adapter import (  # noqa: E402
    SyntheticBehavior,
    SyntheticOperationAdapter,
    prepared_dashboard_operation,
)


class FakeClock:
    def __init__(self) -> None:
        self.wall = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
        self.monotonic_value = 0.0

    def now(self) -> datetime:
        return self.wall

    def monotonic(self) -> float:
        return self.monotonic_value

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.monotonic_value += seconds

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)


LOCK_TIMING = LockTiming(60, 10, 0)
EXECUTOR_TIMING = ExecutorTiming(120, 60, 3, 3)


def _identity(
    *,
    owner: str = "owner-primary",
    request: str = "request-primary",
) -> ExecutionIdentity:
    return ExecutionIdentity(
        task_id="task-synthetic",
        plan_id="plan-synthetic",
        attempt_id="attempt-synthetic",
        request_id=request,
        owner_id=owner,
    )


class SharedExecutorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.clock = FakeClock()
        self.events = BoundedEventRecorder()
        self.metrics = ExecutorMetrics()
        self.locks = DurableLockStore(
            self.temporary.name, event_sink=self.events.emit
        )
        self.executions = DurableExecutionRepository(
            self.temporary.name,
            metrics=self.metrics,
            event_sink=self.events.emit,
        )

    def executor(self, *, fault_hook=None) -> SharedOperationExecutor:
        return SharedOperationExecutor(
            lock_store=self.locks,
            execution_repository=self.executions,
            lock_timing=LOCK_TIMING,
            executor_timing=EXECUTOR_TIMING,
            metrics=self.metrics,
            event_sink=self.events.emit,
            now=self.clock.now,
            monotonic=self.clock.monotonic,
            sleep=self.clock.sleep,
            fault_hook=fault_hook,
        )

    async def run_adapter(
        self,
        behavior: SyntheticBehavior = SyntheticBehavior(),
        *,
        executor: SharedOperationExecutor | None = None,
        identity: ExecutionIdentity | None = None,
        adapter: SyntheticOperationAdapter | None = None,
    ):
        adapter = adapter or SyntheticOperationAdapter(behavior)
        result = await (executor or self.executor()).execute(
            adapter=adapter,
            prepared=prepared_dashboard_operation(),
            identity=identity or _identity(),
        )
        return adapter, result

    async def test_successful_lifecycle_dispatches_and_mutates_once(self):
        adapter, result = await self.run_adapter()
        self.assertEqual(result.outcome, "succeeded_verified")
        self.assertTrue(result.terminal)
        self.assertEqual(result.dispatch_count, 1)
        self.assertEqual(adapter.counters.preflight_invocations, 1)
        self.assertEqual(adapter.counters.dispatch_invocations, 1)
        self.assertEqual(adapter.counters.simulated_mutations, 1)
        self.assertEqual(adapter.counters.observation_invocations, 1)
        self.assertEqual(adapter.counters.verification_invocations, 1)
        self.assertEqual(self.locks.records(), ())

    async def test_stale_preflight_rejects_before_dispatch_and_releases(self):
        adapter, result = await self.run_adapter(
            SyntheticBehavior(preflight="stale")
        )
        self.assertEqual(result.outcome, "preflight_rejected")
        self.assertEqual(adapter.counters.dispatch_invocations, 0)
        self.assertEqual(adapter.counters.simulated_mutations, 0)
        self.assertEqual(self.locks.records(), ())

    async def test_provider_unavailable_preflight_is_pre_dispatch(self):
        adapter, result = await self.run_adapter(
            SyntheticBehavior(preflight="unavailable")
        )
        self.assertEqual(
            result.outcome, "provider_unavailable_pre_dispatch"
        )
        self.assertEqual(result.dispatch_count, 0)
        self.assertEqual(adapter.counters.dispatch_invocations, 0)

    async def test_lock_conflict_prevents_preflight_and_dispatch(self):
        blocker = self.locks.acquire_once(
            (
                SyntheticOperationAdapter().lock_requests(
                    prepared_dashboard_operation()
                )[0],
            ),
            owner=LockOwner(
                "owner-blocker",
                "task-blocker",
                "plan-blocker",
                "update_dashboard",
                "attempt-blocker",
            ),
            timing=LOCK_TIMING,
            now=self.clock.now(),
        )
        adapter, result = await self.run_adapter()
        self.assertEqual(result.outcome, "lock_conflict")
        self.assertEqual(adapter.counters.preflight_invocations, 0)
        self.assertEqual(adapter.counters.dispatch_invocations, 0)
        self.locks.release(blocker)

    async def test_confirmed_dispatch_failure_is_terminal_without_mutation(self):
        adapter, result = await self.run_adapter(
            SyntheticBehavior(dispatch="confirmed_failure")
        )
        self.assertEqual(result.outcome, "dispatch_failed_confirmed")
        self.assertEqual(adapter.counters.dispatch_invocations, 1)
        self.assertEqual(adapter.counters.simulated_mutations, 0)
        self.assertEqual(self.locks.records(), ())

    async def test_lost_response_after_effect_recovers_by_readback_only(self):
        adapter, result = await self.run_adapter(
            SyntheticBehavior(dispatch="response_lost_after_effect")
        )
        self.assertEqual(result.outcome, "succeeded_verified")
        self.assertEqual(adapter.counters.dispatch_invocations, 1)
        self.assertEqual(adapter.counters.simulated_mutations, 1)
        self.assertEqual(adapter.counters.recovery_invocations, 1)
        self.assertTrue(result.redispatch_prohibited)

    async def test_dispatch_exception_before_effect_is_verified_mismatch(self):
        adapter, result = await self.run_adapter(
            SyntheticBehavior(dispatch="raise_before_effect")
        )
        self.assertEqual(result.outcome, "verification_mismatch")
        self.assertEqual(adapter.counters.dispatch_invocations, 1)
        self.assertEqual(adapter.counters.simulated_mutations, 0)
        self.assertEqual(adapter.counters.recovery_invocations, 1)

    async def test_malformed_dispatch_evidence_never_authorizes_redispatch(self):
        adapter, result = await self.run_adapter(
            SyntheticBehavior(dispatch="malformed_evidence")
        )
        self.assertEqual(result.outcome, "succeeded_verified")
        self.assertEqual(adapter.counters.dispatch_invocations, 1)
        self.assertEqual(adapter.counters.simulated_mutations, 1)
        self.assertEqual(adapter.counters.recovery_invocations, 1)

    async def test_eventual_observation_retries_without_redispatch(self):
        adapter = SyntheticOperationAdapter(
            SyntheticBehavior(
                observation="eventual", observations_before_complete=1
            )
        )
        executor = self.executor()
        _, first = await self.run_adapter(adapter=adapter, executor=executor)
        self.assertEqual(first.outcome, "observing")
        self.assertFalse(first.terminal)
        _, second = await self.run_adapter(adapter=adapter, executor=executor)
        self.assertEqual(second.outcome, "succeeded_verified")
        self.assertEqual(adapter.counters.dispatch_invocations, 1)
        self.assertEqual(adapter.counters.simulated_mutations, 1)
        self.assertEqual(adapter.counters.recovery_invocations, 1)

    async def test_verification_mismatch_is_explicit_and_releases(self):
        adapter, result = await self.run_adapter(
            SyntheticBehavior(observation="mismatch")
        )
        self.assertEqual(result.outcome, "verification_mismatch")
        self.assertEqual(adapter.counters.dispatch_invocations, 1)
        self.assertEqual(self.locks.records(), ())

    async def test_evidence_deadline_becomes_manual_review_and_conflict_hold(self):
        adapter = SyntheticOperationAdapter(
            SyntheticBehavior(
                observation="eventual", observations_before_complete=10
            )
        )
        executor = self.executor()
        _, first = await self.run_adapter(adapter=adapter, executor=executor)
        self.assertEqual(first.outcome, "observing")
        self.clock.advance(121)
        recovery_identity = _identity(
            owner="owner-recovery", request="request-recovery"
        )
        _, recovered = await self.run_adapter(
            adapter=adapter,
            executor=executor,
            identity=recovery_identity,
        )
        self.assertEqual(recovered.outcome, "manual_review_required")
        self.assertTrue(all(item.conflict_hold for item in self.locks.records()))
        self.assertEqual(adapter.counters.dispatch_invocations, 1)

    async def test_intent_persistence_failure_invokes_provider_zero_times(self):
        def fail(stage: str) -> None:
            if stage == "before_durable_intent_persistence":
                raise OSError("synthetic intent persistence failure")

        executions = DurableExecutionRepository(
            self.temporary.name,
            metrics=self.metrics,
            event_sink=self.events.emit,
            fault_hook=fail,
        )
        executor = SharedOperationExecutor(
            lock_store=self.locks,
            execution_repository=executions,
            lock_timing=LOCK_TIMING,
            executor_timing=EXECUTOR_TIMING,
            metrics=self.metrics,
            event_sink=self.events.emit,
            now=self.clock.now,
            monotonic=self.clock.monotonic,
            sleep=self.clock.sleep,
        )
        adapter = SyntheticOperationAdapter()
        result = await executor.execute(
            adapter=adapter,
            prepared=prepared_dashboard_operation(),
            identity=_identity(),
        )
        self.assertEqual(result.outcome, "failed_pre_dispatch")
        self.assertEqual(adapter.counters.dispatch_invocations, 0)
        self.assertEqual(adapter.counters.simulated_mutations, 0)
        self.assertEqual(self.locks.records(), ())

    async def test_process_loss_after_intent_never_redispatches(self):
        def lose(stage: str) -> None:
            if stage == "after_durable_intent_before_provider_invocation":
                raise SimulatedProcessLoss()

        adapter = SyntheticOperationAdapter()
        with self.assertRaises(SimulatedProcessLoss):
            await self.executor(fault_hook=lose).execute(
                adapter=adapter,
                prepared=prepared_dashboard_operation(),
                identity=_identity(),
            )
        durable = self.executions.get("task-synthetic")
        self.assertIsNotNone(durable)
        assert durable is not None
        self.assertIsNotNone(durable.dispatch_intent)
        self.assertEqual(durable.dispatch_count, 1)
        self.assertEqual(adapter.counters.dispatch_invocations, 0)
        self.clock.advance(61)
        recovered = await self.executor().execute(
            adapter=adapter,
            prepared=prepared_dashboard_operation(),
            identity=_identity(
                owner="owner-recovery", request="request-recovery"
            ),
        )
        self.assertEqual(recovered.outcome, "verification_mismatch")
        self.assertEqual(adapter.counters.dispatch_invocations, 0)
        self.assertEqual(adapter.counters.simulated_mutations, 0)
        self.assertEqual(adapter.counters.recovery_invocations, 1)

    async def test_expired_preintent_locks_reconstruct_with_new_preflight(self):
        def lose(stage: str) -> None:
            if stage == "after_preflight_before_durable_intent":
                raise SimulatedProcessLoss()

        adapter = SyntheticOperationAdapter()
        with self.assertRaises(SimulatedProcessLoss):
            await self.executor(fault_hook=lose).execute(
                adapter=adapter,
                prepared=prepared_dashboard_operation(),
                identity=_identity(),
            )
        first_generations = tuple(
            item.generation for item in self.locks.records()
        )
        self.assertEqual(adapter.counters.preflight_invocations, 1)
        self.assertEqual(adapter.counters.dispatch_invocations, 0)
        self.clock.advance(61)
        result = await self.executor().execute(
            adapter=adapter,
            prepared=prepared_dashboard_operation(),
            identity=_identity(
                owner="owner-recovery", request="request-recovery"
            ),
        )
        self.assertEqual(result.outcome, "succeeded_verified")
        self.assertEqual(adapter.counters.preflight_invocations, 2)
        self.assertEqual(adapter.counters.dispatch_invocations, 1)
        self.assertEqual(adapter.counters.simulated_mutations, 1)
        self.assertEqual(self.locks.records(), ())
        durable = self.executions.get("task-synthetic")
        self.assertIsNotNone(durable)
        assert durable is not None
        self.assertTrue(
            all(
                token["generation"] not in first_generations
                for token in durable.lock_tokens
            )
        )

    async def test_duplicate_active_task_reports_existing_without_locks(self):
        prepared = prepared_dashboard_operation()
        self.executions.claim(
            identity=_identity(),
            prepared=prepared,
            timing=EXECUTOR_TIMING,
            now=self.clock.now(),
        )
        adapter = SyntheticOperationAdapter()
        result = await self.executor().execute(
            adapter=adapter,
            prepared=prepared,
            identity=_identity(
                owner="owner-duplicate", request="request-duplicate"
            ),
        )
        self.assertTrue(result.duplicate_execution)
        self.assertEqual(result.dispatch_count, 0)
        self.assertEqual(adapter.counters.preflight_invocations, 0)
        self.assertEqual(self.locks.records(), ())

    async def test_terminal_duplicate_returns_existing_result(self):
        adapter = SyntheticOperationAdapter()
        executor = self.executor()
        first = await executor.execute(
            adapter=adapter,
            prepared=prepared_dashboard_operation(),
            identity=_identity(),
        )
        second = await executor.execute(
            adapter=adapter,
            prepared=prepared_dashboard_operation(),
            identity=_identity(request="request-second"),
        )
        self.assertEqual(first.outcome, second.outcome)
        self.assertTrue(second.duplicate_execution)
        self.assertEqual(adapter.counters.dispatch_invocations, 1)

    async def test_cancel_before_dispatch_is_terminal_and_releases(self):
        def lose(stage: str) -> None:
            if stage == "before_lock_acquisition":
                raise SimulatedProcessLoss()

        executor = self.executor(fault_hook=lose)
        with self.assertRaises(SimulatedProcessLoss):
            await executor.execute(
                adapter=SyntheticOperationAdapter(),
                prepared=prepared_dashboard_operation(),
                identity=_identity(),
            )
        self.assertTrue(await executor.cancel("task-synthetic"))
        record = self.executions.get("task-synthetic")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.normalized_outcome, "cancelled_pre_dispatch")
        self.assertEqual(record.dispatch_count, 0)

    async def test_cancel_after_intent_is_rejected(self):
        def lose(stage: str) -> None:
            if stage == "after_durable_intent_before_provider_invocation":
                raise SimulatedProcessLoss()

        executor = self.executor(fault_hook=lose)
        with self.assertRaises(SimulatedProcessLoss):
            await executor.execute(
                adapter=SyntheticOperationAdapter(),
                prepared=prepared_dashboard_operation(),
                identity=_identity(),
            )
        self.assertFalse(await executor.cancel("task-synthetic"))

    async def test_recovery_unsupported_requires_manual_review(self):
        def lose(stage: str) -> None:
            if stage == "after_durable_intent_before_provider_invocation":
                raise SimulatedProcessLoss()

        adapter = SyntheticOperationAdapter(
            SyntheticBehavior(recovery_supported=False)
        )
        with self.assertRaises(SimulatedProcessLoss):
            await self.executor(fault_hook=lose).execute(
                adapter=adapter,
                prepared=prepared_dashboard_operation(),
                identity=_identity(),
            )
        self.clock.advance(61)
        result = await self.executor().execute(
            adapter=adapter,
            prepared=prepared_dashboard_operation(),
            identity=_identity(
                owner="owner-recovery", request="request-recovery"
            ),
        )
        self.assertEqual(result.outcome, "manual_review_required")
        self.assertEqual(adapter.counters.dispatch_invocations, 0)

    async def test_outcome_mapping_exactly_matches_frozen_contract(self):
        self.assertEqual(
            set(NORMALIZED_OUTCOME_TO_TASK_STATE),
            {item.value for item in NormalizedOperationOutcome},
        )

    async def test_metrics_and_events_are_bounded_and_content_free(self):
        await self.run_adapter()
        snapshot = self.metrics.snapshot()
        self.assertEqual(snapshot["executions_started"], 1)
        self.assertEqual(snapshot["durable_intents_committed"], 1)
        self.assertEqual(snapshot["dispatch_attempts"], 1)
        self.assertEqual(snapshot["verification_successes"], 1)
        events = self.events.snapshot()
        self.assertTrue(events)
        serialized = repr(events)
        self.assertNotIn("synthetic response was lost", serialized)
        self.assertNotIn("provider payload", serialized)


if __name__ == "__main__":
    unittest.main()
