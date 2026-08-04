"""Deterministic process-loss matrix for the F3-A irreversible boundary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.f3.executor import (  # noqa: E402
    SharedOperationExecutor,
    SimulatedProcessLoss,
)
from ha_mcp_engineering.f3.locks import DurableLockStore  # noqa: E402
from ha_mcp_engineering.f3.models import (  # noqa: E402
    ExecutionIdentity,
    ExecutorTiming,
    LockOwner,
    LockTiming,
)
from ha_mcp_engineering.f3.persistence import (  # noqa: E402
    DurableExecutionRepository,
)
from tests.f3_synthetic_adapter import (  # noqa: E402
    SyntheticBehavior,
    SyntheticOperationAdapter,
    SyntheticProcessLoss,
    prepared_dashboard_operation,
)


LOCK_TIMING = LockTiming(60, 10, 0)
EXECUTOR_TIMING = ExecutorTiming(120, 60, 3, 3)


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


class FailAt:
    def __init__(self, stage: str):
        self.stage = stage
        self.triggered = False

    def __call__(self, stage: str) -> None:
        if stage == self.stage and not self.triggered:
            self.triggered = True
            raise SimulatedProcessLoss()


class FaultInjectionMatrixTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.clock = FakeClock()

    @staticmethod
    def identity(
        owner: str = "owner-primary",
        request: str = "request-primary",
    ) -> ExecutionIdentity:
        return ExecutionIdentity(
            task_id="task-fault-matrix",
            plan_id="plan-fault-matrix",
            attempt_id="attempt-fault-matrix",
            request_id=request,
            owner_id=owner,
        )

    def components(
        self,
        *,
        executor_fault=None,
        lock_fault=None,
        execution_fault=None,
    ):
        locks = DurableLockStore(
            self.temporary.name, fault_hook=lock_fault
        )
        executions = DurableExecutionRepository(
            self.temporary.name, fault_hook=execution_fault
        )
        executor = SharedOperationExecutor(
            lock_store=locks,
            execution_repository=executions,
            lock_timing=LOCK_TIMING,
            executor_timing=EXECUTOR_TIMING,
            now=self.clock.now,
            monotonic=self.clock.monotonic,
            sleep=self.clock.sleep,
            fault_hook=executor_fault,
        )
        return locks, executions, executor

    def report(self, locks, executions, adapter) -> dict[str, object]:
        record = executions.get("task-fault-matrix")
        lock_records = locks.records()
        dispatch_count = record.dispatch_count if record else 0
        intent = bool(record and record.dispatch_intent)
        terminal = bool(record and record.terminal)
        if terminal:
            next_operation = "return_terminal_result"
        elif intent:
            next_operation = "observation_or_verification_only"
        else:
            next_operation = "new_preflight_after_valid_claim"
        return {
            "durable_task_state": record.state if record else "not_created",
            "durable_lock_state": (
                "conflict_hold"
                if any(item.conflict_hold for item in lock_records)
                else "held" if lock_records else "none"
            ),
            "dispatch_count": dispatch_count,
            "mutation_count": adapter.counters.simulated_mutations,
            "allowed_next_operation": next_operation,
            "observation_required": intent and not terminal,
            "redispatch_prohibited": intent,
            "terminal_outcome": (
                record.normalized_outcome if terminal else None
            ),
        }

    async def execute_with_loss(
        self,
        *,
        executor_stage: str | None = None,
        lock_stage: str | None = None,
        execution_stage: str | None = None,
        behavior: SyntheticBehavior = SyntheticBehavior(),
        expected_exception=SimulatedProcessLoss,
    ):
        executor_fault = FailAt(executor_stage) if executor_stage else None
        lock_fault = FailAt(lock_stage) if lock_stage else None
        execution_fault = FailAt(execution_stage) if execution_stage else None
        locks, executions, executor = self.components(
            executor_fault=executor_fault,
            lock_fault=lock_fault,
            execution_fault=execution_fault,
        )
        adapter = SyntheticOperationAdapter(behavior)
        with self.assertRaises(expected_exception):
            await executor.execute(
                adapter=adapter,
                prepared=prepared_dashboard_operation(),
                identity=self.identity(),
            )
        report = self.report(locks, executions, adapter)
        self.assertLessEqual(report["dispatch_count"], 1)
        self.assertLessEqual(report["mutation_count"], 1)
        if (
            report["redispatch_prohibited"]
            and report["terminal_outcome"] is None
        ):
            self.assertEqual(
                report["allowed_next_operation"],
                "observation_or_verification_only",
            )
        return locks, executions, adapter, report

    async def test_01_process_loss_before_lock_acquisition(self):
        _, _, adapter, report = await self.execute_with_loss(
            executor_stage="before_lock_acquisition"
        )
        self.assertEqual(report["durable_task_state"], "planning")
        self.assertEqual(report["durable_lock_state"], "none")
        self.assertEqual(report["dispatch_count"], 0)
        self.assertEqual(adapter.counters.dispatch_invocations, 0)

    async def test_02_process_loss_during_atomic_multi_lock_acquisition(self):
        _, _, adapter, report = await self.execute_with_loss(
            lock_stage="before_state_replace"
        )
        self.assertEqual(report["durable_lock_state"], "none")
        self.assertEqual(report["dispatch_count"], 0)
        self.assertEqual(adapter.counters.preflight_invocations, 0)

    async def test_03_process_loss_after_locks_before_preflight(self):
        _, _, adapter, report = await self.execute_with_loss(
            executor_stage="after_lock_acquisition_before_preflight"
        )
        self.assertEqual(report["durable_task_state"], "preflight")
        self.assertEqual(report["durable_lock_state"], "held")
        self.assertEqual(adapter.counters.preflight_invocations, 0)

    async def test_04_process_loss_after_preflight_before_intent(self):
        _, executions, adapter, report = await self.execute_with_loss(
            executor_stage="after_preflight_before_durable_intent"
        )
        record = executions.get("task-fault-matrix")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertTrue(record.preflight_completed)
        self.assertIsNone(record.dispatch_intent)
        self.assertEqual(report["dispatch_count"], 0)
        self.assertEqual(adapter.counters.preflight_invocations, 1)

    async def test_05_process_loss_during_durable_intent_persistence(self):
        _, executions, adapter, report = await self.execute_with_loss(
            execution_stage="before_durable_intent_persistence"
        )
        record = executions.get("task-fault-matrix")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertIsNone(record.dispatch_intent)
        self.assertEqual(report["dispatch_count"], 0)
        self.assertEqual(adapter.counters.dispatch_invocations, 0)
        self.assertEqual(adapter.counters.simulated_mutations, 0)

    async def test_06_process_loss_after_intent_before_provider(self):
        _, executions, adapter, report = await self.execute_with_loss(
            executor_stage="after_durable_intent_before_provider_invocation"
        )
        record = executions.get("task-fault-matrix")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertIsNotNone(record.dispatch_intent)
        self.assertEqual(report["dispatch_count"], 1)
        self.assertEqual(adapter.counters.dispatch_invocations, 0)
        self.assertEqual(adapter.counters.simulated_mutations, 0)

    async def test_07_provider_failure_during_invocation_before_effect(self):
        locks, executions, executor = self.components()
        adapter = SyntheticOperationAdapter(
            SyntheticBehavior(dispatch="raise_before_effect")
        )
        result = await executor.execute(
            adapter=adapter,
            prepared=prepared_dashboard_operation(),
            identity=self.identity(),
        )
        report = self.report(locks, executions, adapter)
        self.assertEqual(result.outcome, "verification_mismatch")
        self.assertEqual(report["dispatch_count"], 1)
        self.assertEqual(report["mutation_count"], 0)
        self.assertEqual(adapter.counters.dispatch_invocations, 1)

    async def test_08_provider_failure_after_simulated_effect(self):
        locks, executions, executor = self.components()
        adapter = SyntheticOperationAdapter(
            SyntheticBehavior(dispatch="raise_after_effect")
        )
        result = await executor.execute(
            adapter=adapter,
            prepared=prepared_dashboard_operation(),
            identity=self.identity(),
        )
        report = self.report(locks, executions, adapter)
        self.assertEqual(result.outcome, "succeeded_verified")
        self.assertEqual(report["dispatch_count"], 1)
        self.assertEqual(report["mutation_count"], 1)

    async def test_09_process_loss_after_response_before_observation(self):
        _, executions, adapter, report = await self.execute_with_loss(
            executor_stage="after_provider_response_before_observation"
        )
        record = executions.get("task-fault-matrix")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertTrue(record.provider_response_received)
        self.assertEqual(report["dispatch_count"], 1)
        self.assertEqual(report["mutation_count"], 1)
        self.assertEqual(adapter.counters.observation_invocations, 0)

    async def test_10_process_loss_during_observation(self):
        locks, executions, executor = self.components()
        adapter = SyntheticOperationAdapter(
            SyntheticBehavior(observation="process_loss")
        )
        with self.assertRaises(SyntheticProcessLoss):
            await executor.execute(
                adapter=adapter,
                prepared=prepared_dashboard_operation(),
                identity=self.identity(),
            )
        report = self.report(locks, executions, adapter)
        self.assertEqual(report["dispatch_count"], 1)
        self.assertEqual(report["mutation_count"], 1)
        self.assertTrue(report["observation_required"])

    async def test_11_process_loss_after_observation_before_verification(self):
        _, executions, adapter, report = await self.execute_with_loss(
            executor_stage="after_observation_before_verification"
        )
        record = executions.get("task-fault-matrix")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.observation_attempts, 1)
        self.assertEqual(record.verification_attempts, 0)
        self.assertEqual(report["dispatch_count"], 1)
        self.assertEqual(adapter.counters.verification_invocations, 0)

    async def test_12_process_loss_during_verification(self):
        locks, executions, executor = self.components()
        adapter = SyntheticOperationAdapter(
            SyntheticBehavior(verification="process_loss")
        )
        with self.assertRaises(SyntheticProcessLoss):
            await executor.execute(
                adapter=adapter,
                prepared=prepared_dashboard_operation(),
                identity=self.identity(),
            )
        report = self.report(locks, executions, adapter)
        self.assertEqual(report["dispatch_count"], 1)
        self.assertEqual(adapter.counters.verification_invocations, 1)
        self.assertTrue(report["observation_required"])

    async def test_13_process_loss_after_verified_before_release(self):
        locks, executions, adapter, report = await self.execute_with_loss(
            executor_stage="after_verified_result_before_lock_release"
        )
        record = executions.get("task-fault-matrix")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertTrue(record.terminal)
        self.assertEqual(record.normalized_outcome, "succeeded_verified")
        self.assertEqual(report["durable_lock_state"], "held")
        self.assertEqual(adapter.counters.dispatch_invocations, 1)

    async def test_14_process_loss_during_lock_renewal(self):
        fault = FailAt("during_lock_renewal")
        locks = DurableLockStore(self.temporary.name, fault_hook=fault)
        adapter = SyntheticOperationAdapter()
        request = adapter.lock_requests(prepared_dashboard_operation())[0]
        owner = LockOwner(
            "owner-primary",
            "task-fault-matrix",
            "plan-fault-matrix",
            "update_dashboard",
            "attempt-fault-matrix",
        )
        handle = locks.acquire_once(
            (request,),
            owner=owner,
            timing=LOCK_TIMING,
            now=self.clock.now(),
        )
        self.clock.advance(10)
        with self.assertRaises(SimulatedProcessLoss):
            locks.renew(handle, now=self.clock.now())
        self.assertEqual(len(locks.records()), 1)
        self.assertEqual(locks.records()[0].generation, handle.tokens[0].generation)

    async def test_15_process_loss_during_release_is_repaired_without_dispatch(self):
        fault = FailAt("during_lock_release")
        locks, executions, executor = self.components(lock_fault=fault)
        adapter = SyntheticOperationAdapter()
        with self.assertRaises(SimulatedProcessLoss):
            await executor.execute(
                adapter=adapter,
                prepared=prepared_dashboard_operation(),
                identity=self.identity(),
            )
        self.assertEqual(adapter.counters.dispatch_invocations, 1)
        self.assertEqual(len(locks.records()), 3)
        clean_locks = DurableLockStore(self.temporary.name)
        recovered_executor = SharedOperationExecutor(
            lock_store=clean_locks,
            execution_repository=executions,
            lock_timing=LOCK_TIMING,
            executor_timing=EXECUTOR_TIMING,
            now=self.clock.now,
            monotonic=self.clock.monotonic,
            sleep=self.clock.sleep,
        )
        result = await recovered_executor.execute(
            adapter=adapter,
            prepared=prepared_dashboard_operation(),
            identity=self.identity(request="request-recovery"),
        )
        self.assertEqual(result.outcome, "succeeded_verified")
        self.assertEqual(adapter.counters.dispatch_invocations, 1)
        self.assertEqual(clean_locks.records(), ())


if __name__ == "__main__":
    unittest.main()
