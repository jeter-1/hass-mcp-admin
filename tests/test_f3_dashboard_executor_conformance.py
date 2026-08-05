"""Nonmutating dashboard conformance against the merged F3-A executor."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.f3.contracts import (  # noqa: E402
    F3_ADAPTER_CONTRACT_MODEL,
    AdapterCapabilityDescriptor,
    DispatchResult,
    LockMode,
    LockRequest,
    LockScope,
    NormalizedOperationOutcome,
    ObservationResult,
    OperationTarget,
    PreflightResult,
    PreparedOperation,
    RecoveryContext,
    VerificationResult,
)
from ha_mcp_engineering.f3.executor import SharedOperationExecutor  # noqa: E402
from ha_mcp_engineering.f3.locks import (  # noqa: E402
    DurableLockStore,
    normalize_lock_requests,
)
from ha_mcp_engineering.f3.models import (  # noqa: E402
    ExecutionIdentity,
    ExecutorTiming,
    LockTiming,
)
from ha_mcp_engineering.f3.persistence import (  # noqa: E402
    DurableExecutionRepository,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64
HASH_F = "f" * 64


def _prepared_dashboard_deferral() -> PreparedOperation:
    return PreparedOperation(
        contract_model=F3_ADAPTER_CONTRACT_MODEL,
        adapter_id="dashboard_atomicity_gate",
        operation="dashboard_execution_deferred",
        target=OperationTarget("dashboard", "overview"),
        current_state_fingerprint=HASH_A,
        normalized_proposed_hash=HASH_B,
        prepared_operation_hash=HASH_C,
        risk_level="high",
        policy_decision_hash=HASH_D,
        approval_bundle_hash=HASH_E,
        expected_effects=("dashboard_execution_rejected",),
        verification_contract_model="dashboard_exact_reread_v1",
        verification_contract_hash=HASH_F,
        rollback_available=False,
    )


class AtomicityBlockedDashboardAdapter:
    """Test-only adapter proving rejection before durable dispatch intent."""

    def __init__(self) -> None:
        self.preflight_invocations = 0
        self.dispatch_invocations = 0
        self.setter_invocations = 0
        self.fixture_mutations = 0
        self.capabilities = AdapterCapabilityDescriptor(
            adapter_id="dashboard_atomicity_gate",
            contract_model=F3_ADAPTER_CONTRACT_MODEL,
            operation_family="dashboard_execution_deferral",
            supported_operations=("dashboard_execution_deferred",),
            rollback_supported=False,
            readback_recovery_supported=True,
            exact_provider_contract_required=True,
        )

    async def prepare(self, proposal: object) -> PreparedOperation:
        del proposal
        return _prepared_dashboard_deferral()

    def lock_requests(
        self, operation: PreparedOperation
    ) -> tuple[LockRequest, ...]:
        return (
            LockRequest(
                key=f"dashboard:{operation.target.target_id}",
                scopes=(LockScope.RESOURCE,),
                mode=LockMode.EXCLUSIVE,
                reason_codes=("dashboard_target_mutation",),
            ),
            LockRequest(
                key="home_assistant:core",
                scopes=(LockScope.RESOURCE,),
                mode=LockMode.SHARED,
                reason_codes=("home_assistant_availability_dependency",),
            ),
            LockRequest(
                key="addon:ha_mcp",
                scopes=(LockScope.PROVIDER,),
                mode=LockMode.SHARED,
                reason_codes=("upstream_provider_dependency",),
            ),
        )

    async def preflight(
        self,
        operation: PreparedOperation,
        *,
        acquired_locks: tuple[LockRequest, ...],
    ) -> PreflightResult:
        self.preflight_invocations += 1
        if not acquired_locks:
            raise AssertionError("complete lock set was not supplied")
        return PreflightResult(
            eligible=False,
            outcome=NormalizedOperationOutcome.PREFLIGHT_REJECTED,
            confirmed_target=operation.target,
            observed_state_fingerprint=operation.current_state_fingerprint,
            provider_contract=None,
            provider_operation=None,
            provider_arguments_hash=None,
            evidence_hash=HASH_F,
            diagnostic_codes=("dashboard_atomicity_unavailable",),
        )

    async def dispatch(
        self,
        operation: PreparedOperation,
        preflight: PreflightResult,
        *,
        before_dispatch,
    ) -> DispatchResult:
        del operation, preflight, before_dispatch
        self.dispatch_invocations += 1
        raise AssertionError("dashboard dispatch must remain unreachable")

    async def observe(
        self,
        operation: PreparedOperation,
        dispatch: DispatchResult | None,
    ) -> ObservationResult:
        del operation, dispatch
        raise AssertionError("blocked execution has no post-dispatch observation")

    async def verify(
        self,
        operation: PreparedOperation,
        observation: ObservationResult,
    ) -> VerificationResult:
        del operation, observation
        raise AssertionError("blocked execution has no executor verification")

    async def recover(
        self,
        operation: PreparedOperation,
        *,
        context: RecoveryContext,
    ) -> ObservationResult:
        del operation, context
        raise AssertionError("blocked execution has no recovery intent")

    async def prepare_rollback(
        self,
        operation: PreparedOperation,
        *,
        expected_current_fingerprint: str,
    ) -> PreparedOperation | None:
        del operation, expected_current_fingerprint
        return None


class DashboardExecutorConformanceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.locks = DurableLockStore(self.temporary.name)
        self.executions = DurableExecutionRepository(self.temporary.name)
        self.executor = SharedOperationExecutor(
            lock_store=self.locks,
            execution_repository=self.executions,
            lock_timing=LockTiming(60, 10, 0),
            executor_timing=ExecutorTiming(120, 60, 3, 3),
            now=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        )
        self.approval_invocations = 0

    async def consume_approval(self) -> None:
        self.approval_invocations += 1

    def test_dashboard_lock_set_normalizes_to_f3_a_model(self):
        adapter = AtomicityBlockedDashboardAdapter()
        normalized = normalize_lock_requests(
            adapter.lock_requests(_prepared_dashboard_deferral())
        )
        self.assertEqual(
            tuple(item.key for item in normalized),
            ("addon:ha_mcp", "dashboard:overview", "home_assistant:core"),
        )
        self.assertEqual(normalized[0].scopes, ("provider",))
        self.assertEqual(normalized[1].mode, "exclusive")
        self.assertEqual(normalized[2].mode, "shared")

    async def test_atomicity_gate_rejects_before_intent_or_mutation(self):
        adapter = AtomicityBlockedDashboardAdapter()
        result = await self.executor.execute(
            adapter=adapter,
            prepared=_prepared_dashboard_deferral(),
            identity=ExecutionIdentity(
                task_id="task-dashboard-deferral",
                plan_id="plan-dashboard-deferral",
                attempt_id="attempt-dashboard-deferral",
                request_id="request-dashboard-deferral",
                owner_id="owner-dashboard-deferral",
            ),
            approval_consumption=self.consume_approval,
        )
        record = self.executions.get("task-dashboard-deferral")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(result.outcome, "preflight_rejected")
        self.assertEqual(result.dispatch_count, 0)
        self.assertFalse(result.dispatch_intent_recorded)
        self.assertIsNone(record.dispatch_intent)
        self.assertEqual(adapter.preflight_invocations, 1)
        self.assertEqual(adapter.dispatch_invocations, 0)
        self.assertEqual(adapter.setter_invocations, 0)
        self.assertEqual(adapter.fixture_mutations, 0)
        self.assertEqual(self.approval_invocations, 0)
        self.assertEqual(self.locks.records(), ())


if __name__ == "__main__":
    unittest.main()
