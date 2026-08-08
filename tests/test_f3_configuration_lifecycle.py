"""Preflight, dispatch, verification, and recovery tests for F3-C1."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.f3.contracts import NormalizedOperationOutcome
from ha_mcp_engineering.f3.executor import (
    PreIntentRetryRequired,
    SharedOperationExecutor,
    SimulatedProcessLoss,
)
from ha_mcp_engineering.f3.locks import DurableLockStore
from ha_mcp_engineering.f3.models import (
    ExecutionIdentity,
    ExecutorTiming,
    LockOwner,
    LockTiming,
)
from ha_mcp_engineering.f3.persistence import DurableExecutionRepository
from ha_mcp_engineering.f3_configuration.adapter import (
    ConfigurationOperationAdapter,
)
from ha_mcp_engineering.f3_configuration.observability import (
    ConfigurationAdapterMetrics,
    InMemoryConfigurationEventSink,
)
from ha_mcp_engineering.f3_configuration.sequence import (
    prepare_configuration_sequence,
    single_operation_child_descriptor,
)

from tests.f3_configuration_fixtures import (
    ConfigurationLifecycleHarness,
    FIXED_NOW,
    SyntheticConfigurationGateway,
    SyntheticProcessLoss,
    adapter_for,
    proposal_for,
    recovery_context,
    valid_config,
)
from tests.f3_synthetic_adapter import SyntheticApprovalRecorder


LOCK_TIMING = LockTiming(60, 10, 0)
EXECUTOR_TIMING = ExecutorTiming(120, 60, 3, 3)


class FakeClock:
    def __init__(self) -> None:
        self.wall = FIXED_NOW
        self.monotonic_value = 0.0

    def now(self):
        return self.wall

    def monotonic(self) -> float:
        return self.monotonic_value

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.monotonic_value += seconds

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)


class LifecycleTestCase(unittest.IsolatedAsyncioTestCase):
    async def prepared(
        self,
        resource_type: str = "automation",
        action: str = "update",
        *,
        gateway: SyntheticConfigurationGateway | None = None,
        proposal=None,
    ):
        proposal = proposal or proposal_for(resource_type, action)
        gateway = gateway or SyntheticConfigurationGateway()
        if action == "update" and not gateway.states:
            gateway.states[(resource_type, proposal.target_id)] = (
                proposal.current_config()
            )
        adapter = adapter_for(resource_type, action, gateway)
        prepared = await adapter.prepare(proposal)
        return proposal, gateway, adapter, prepared

    async def eligible(self, *args, **kwargs):
        proposal, gateway, adapter, prepared = await self.prepared(
            *args, **kwargs
        )
        preflight = await adapter.preflight(
            prepared, acquired_locks=adapter.lock_requests(prepared)
        )
        self.assertTrue(preflight.eligible, preflight.diagnostic_codes)
        return proposal, gateway, adapter, prepared, preflight


class PreflightTests(LifecycleTestCase):
    async def test_create_cannot_imply_a_registry_category_write(self):
        proposed = valid_config("automation")
        proposed["category"] = "security"
        proposal = proposal_for(
            "automation", "create", proposed_config=proposed
        )
        gateway = SyntheticConfigurationGateway()
        adapter = adapter_for("automation", "create", gateway)
        with self.assertRaisesRegex(
            ValueError, "failed existing validation"
        ):
            await adapter.prepare(proposal)
        self.assertEqual(gateway.counters.dispatches, 0)
        self.assertEqual(gateway.counters.simulated_mutations, 0)

    async def test_missing_complete_lock_set_rejects_before_provider_dispatch(self):
        _proposal, gateway, adapter, prepared = await self.prepared()
        result = await adapter.preflight(prepared, acquired_locks=())
        self.assertEqual(result.outcome, NormalizedOperationOutcome.LOCK_CONFLICT)
        self.assertEqual(gateway.counters.dispatches, 0)
        self.assertEqual(gateway.counters.simulated_mutations, 0)

    async def test_policy_provider_and_expiry_each_fail_closed(self):
        variants = (
            ("policy", {"policy_snapshot_valid": False}, "policy_snapshot_mismatch"),
            ("provider", {"provider_admitted": False}, "provider_not_admitted"),
        )
        for name, kwargs, code in variants:
            with self.subTest(name=name):
                proposal = proposal_for("automation", "update", **kwargs)
                _p, gateway, adapter, prepared = await self.prepared(
                    proposal=proposal
                )
                result = await adapter.preflight(
                    prepared,
                    acquired_locks=adapter.lock_requests(prepared),
                )
                self.assertFalse(result.eligible)
                self.assertIn(code, result.diagnostic_codes)
                self.assertEqual(gateway.counters.dispatches, 0)

        proposal = replace(
            proposal_for("automation", "update"),
            plan_expires_at="2026-08-04T11:59:59+00:00",
        )
        _p, gateway, adapter, prepared = await self.prepared(proposal=proposal)
        result = await adapter.preflight(
            prepared, acquired_locks=adapter.lock_requests(prepared)
        )
        self.assertIn("plan_expired", result.diagnostic_codes)
        self.assertEqual(gateway.counters.dispatches, 0)

    async def test_full_configuration_check_is_required_at_preflight(self):
        proposal = proposal_for("script", "update")
        gateway = SyntheticConfigurationGateway(
            {("script", proposal.target_id): proposal.current_config()}
        )
        gateway.validation_result = {"result": "valid"}
        adapter = adapter_for("script", "update", gateway)
        prepared = await adapter.prepare(proposal)
        result = await adapter.preflight(
            prepared, acquired_locks=adapter.lock_requests(prepared)
        )
        self.assertFalse(result.eligible)
        self.assertIn("configuration_validation_failed", result.diagnostic_codes)
        self.assertEqual(gateway.counters.validation_calls, 1)
        self.assertEqual(gateway.counters.dispatches, 0)

    async def test_final_authoritative_state_read_occurs_in_preflight(self):
        proposal, gateway, adapter, prepared = await self.prepared()
        stale = valid_config("automation")
        stale["alias"] = "external edit before final preflight read"
        stale["id"] = proposal.target_id
        gateway.states[("automation", proposal.target_id)] = stale
        result = await adapter.preflight(
            prepared, acquired_locks=adapter.lock_requests(prepared)
        )
        self.assertIn("stale_target_state", result.diagnostic_codes)
        self.assertEqual(gateway.counters.reads, 1)
        self.assertEqual(gateway.counters.dispatches, 0)


class DurableDispatchTests(LifecycleTestCase):
    async def test_intent_persistence_failure_invokes_provider_zero_times(self):
        _p, gateway, adapter, prepared, preflight = await self.eligible()

        async def fail_intent():
            raise OSError("synthetic persistence failure")

        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=fail_intent
        )
        self.assertEqual(
            dispatch.outcome,
            NormalizedOperationOutcome.FAILED_PRE_DISPATCH,
        )
        self.assertEqual(dispatch.adapter_dispatch_count, 0)
        self.assertEqual(dispatch.provider_mutation_count, 0)
        self.assertEqual(gateway.counters.dispatches, 0)
        self.assertEqual(gateway.counters.simulated_mutations, 0)

    async def test_confirmed_provider_failure_is_one_call_and_zero_mutations(self):
        _p, gateway, adapter, prepared, preflight = await self.eligible()
        gateway.dispatch_mode = "confirmed_failure"
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        self.assertEqual(
            dispatch.outcome,
            NormalizedOperationOutcome.DISPATCH_FAILED_CONFIRMED,
        )
        self.assertTrue(dispatch.dispatch_intent_recorded)
        self.assertEqual(dispatch.adapter_dispatch_count, 1)
        self.assertEqual(dispatch.provider_mutation_count, 0)
        self.assertEqual(gateway.counters.dispatches, 1)

    async def test_provider_rejection_reports_response_and_zero_mutations(self):
        _p, gateway, adapter, prepared, preflight = await self.eligible()
        gateway.dispatch_mode = "provider_rejection"
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        self.assertEqual(
            dispatch.outcome,
            NormalizedOperationOutcome.DISPATCH_FAILED_CONFIRMED,
        )
        self.assertTrue(dispatch.provider_response_received)
        self.assertEqual(dispatch.provider_mutation_count, 0)
        self.assertIn(
            "provider_rejected_mutation", dispatch.diagnostic_codes
        )
        self.assertEqual(gateway.counters.dispatches, 1)
        self.assertEqual(gateway.counters.simulated_mutations, 0)

    async def test_registry_metadata_is_not_dispatched_or_verified_as_config(self):
        for resource_type in ("automation", "script"):
            with self.subTest(resource_type=resource_type):
                current = valid_config(resource_type)
                proposed = valid_config(resource_type, updated=True)
                proposed["category"] = "security"
                proposal = proposal_for(
                    resource_type,
                    "update",
                    current_config=current,
                    proposed_config=proposed,
                )
                gateway = SyntheticConfigurationGateway(
                    {
                        (resource_type, proposal.target_id): (
                            proposal.current_config()
                        )
                    }
                )
                adapter = adapter_for(
                    resource_type, "update", gateway
                )
                prepared = await adapter.prepare(proposal)
                without_metadata = dict(proposed)
                without_metadata.pop("category")
                expected_descriptor = adapter.strategy.provider_descriptor(
                    proposal.target_id, without_metadata
                )
                self.assertEqual(
                    prepared.provider_descriptor.arguments_hash,
                    expected_descriptor.arguments_hash,
                )
                preflight = await adapter.preflight(
                    prepared,
                    acquired_locks=adapter.lock_requests(prepared),
                )
                harness = ConfigurationLifecycleHarness(adapter)
                dispatch = await adapter.dispatch(
                    prepared, preflight, before_dispatch=harness.intent
                )
                observation = await harness.observe(prepared, dispatch)
                verification = await harness.verify(
                    prepared, observation
                )
                self.assertNotIn(
                    "category",
                    gateway.states[(resource_type, proposal.target_id)],
                )
                self.assertEqual(
                    verification.outcome,
                    NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
                )

    async def test_success_response_is_only_observing_until_exact_readback(self):
        _p, gateway, adapter, prepared, preflight = await self.eligible()
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        self.assertEqual(dispatch.outcome, NormalizedOperationOutcome.OBSERVING)
        observation = await harness.observe(prepared, dispatch)
        verification = await harness.verify(prepared, observation)
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
        )
        self.assertEqual(gateway.counters.dispatches, 1)
        self.assertEqual(gateway.counters.simulated_mutations, 1)

    async def test_malformed_provider_response_never_authorizes_redispatch(self):
        _p, gateway, adapter, prepared, preflight = await self.eligible()
        gateway.dispatch_mode = "malformed_provider_response"
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        self.assertEqual(
            dispatch.outcome,
            NormalizedOperationOutcome.DISPATCH_INDETERMINATE,
        )
        self.assertTrue(dispatch.provider_response_received)
        observation = await harness.recover(
            prepared, recovery_context(response_received=True)
        )
        verification = await harness.verify(prepared, observation)
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
        )
        self.assertEqual(gateway.counters.dispatches, 1)


class CanonicalExecutorConfigurationTests(unittest.IsolatedAsyncioTestCase):
    """Exercise one exact C1 operation through the merged F3-A executor."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.clock = FakeClock()
        self.component_index = 0

    def components(self, *, fault_hook=None, execution_fault_hook=None):
        self.component_index += 1
        root = Path(self.temporary.name) / f"case-{self.component_index}"
        locks = DurableLockStore(root)
        executions = DurableExecutionRepository(
            root, fault_hook=execution_fault_hook
        )
        executor = SharedOperationExecutor(
            lock_store=locks,
            execution_repository=executions,
            lock_timing=LOCK_TIMING,
            executor_timing=EXECUTOR_TIMING,
            now=self.clock.now,
            monotonic=self.clock.monotonic,
            sleep=self.clock.sleep,
            fault_hook=fault_hook,
        )
        return locks, executions, executor

    async def prepared_case(
        self,
        resource_type: str,
        action: str,
        *,
        case: str,
        gateway: SyntheticConfigurationGateway | None = None,
        proposal=None,
    ):
        proposal = proposal or proposal_for(
            resource_type,
            action,
            task_id=f"task-{resource_type}-{action}-{case}",
            plan_id=f"plan-{resource_type}-{action}-{case}",
        )
        gateway = gateway or SyntheticConfigurationGateway()
        if action == "update" and not gateway.states:
            gateway.states[(resource_type, proposal.target_id)] = (
                proposal.current_config()
            )
        adapter = adapter_for(resource_type, action, gateway)
        prepared = await adapter.prepare(proposal)
        sequence = prepare_configuration_sequence((prepared,))
        child = single_operation_child_descriptor(sequence)
        identity = ExecutionIdentity(
            task_id=child.public_task_id,
            plan_id=child.plan_id,
            attempt_id=child.attempt_id,
            request_id=f"request-{case}",
            owner_id=f"owner-{case}",
        )
        return proposal, gateway, adapter, prepared, child, identity

    async def test_all_resource_families_conform_end_to_end_once(self):
        for resource_type in (
            "automation",
            "script",
            "input_boolean",
            "input_number",
        ):
            for action in ("create", "update"):
                case = f"success-{resource_type}-{action}"
                with self.subTest(resource_type=resource_type, action=action):
                    _, gateway, adapter, prepared, child, identity = (
                        await self.prepared_case(
                            resource_type, action, case=case
                        )
                    )
                    _locks, _executions, executor = self.components()
                    approval = SyntheticApprovalRecorder()
                    result = await executor.execute(
                        adapter=adapter,
                        prepared=prepared,
                        identity=identity,
                        approval_consumption=approval,
                    )
                    self.assertEqual(result.outcome, "succeeded_verified")
                    self.assertEqual(result.task_id, child.public_task_id)
                    self.assertEqual(result.attempt_id, child.attempt_id)
                    self.assertEqual(result.dispatch_count, 1)
                    self.assertEqual(approval.invocations, 1)
                    self.assertEqual(approval.consumptions, 1)
                    self.assertEqual(gateway.counters.dispatches, 1)
                    self.assertEqual(gateway.counters.simulated_mutations, 1)

    async def test_preflight_refusals_for_every_family_consume_no_approval(self):
        resources = (
            "automation",
            "script",
            "input_boolean",
            "input_number",
        )
        for resource_type in resources:
            cases = []

            stale_proposal = proposal_for(
                resource_type,
                "update",
                task_id=f"task-{resource_type}-stale",
                plan_id=f"plan-{resource_type}-stale",
            )
            stale = valid_config(resource_type, updated=True)
            stale["id"] = (
                stale_proposal.target_id.split(".", 1)[1]
                if resource_type.startswith("input_")
                else stale_proposal.target_id
            )
            cases.append(
                (
                    "stale",
                    stale_proposal,
                    SyntheticConfigurationGateway(
                        {(resource_type, stale_proposal.target_id): stale}
                    ),
                    "preflight_rejected",
                )
            )

            create_proposal = proposal_for(
                resource_type,
                "create",
                task_id=f"task-{resource_type}-exists",
                plan_id=f"plan-{resource_type}-exists",
            )
            existing = create_proposal.proposed_config()
            existing["id"] = (
                create_proposal.target_id.split(".", 1)[1]
                if resource_type.startswith("input_")
                else create_proposal.target_id
            )
            cases.append(
                (
                    "exists",
                    create_proposal,
                    SyntheticConfigurationGateway(
                        {(resource_type, create_proposal.target_id): existing}
                    ),
                    "preflight_rejected",
                )
            )

            provider_proposal = proposal_for(
                resource_type,
                "update",
                task_id=f"task-{resource_type}-provider",
                plan_id=f"plan-{resource_type}-provider",
            )
            provider_gateway = SyntheticConfigurationGateway(
                {
                    (resource_type, provider_proposal.target_id): (
                        provider_proposal.current_config()
                    )
                }
            )
            provider_gateway.provider_admitted = False
            cases.append(
                (
                    "provider",
                    provider_proposal,
                    provider_gateway,
                    "provider_unavailable_pre_dispatch",
                )
            )

            validation_proposal = proposal_for(
                resource_type,
                "update",
                task_id=f"task-{resource_type}-validation",
                plan_id=f"plan-{resource_type}-validation",
            )
            validation_gateway = SyntheticConfigurationGateway(
                {
                    (resource_type, validation_proposal.target_id): (
                        validation_proposal.current_config()
                    )
                }
            )
            validation_gateway.validation_result = {
                "result": "invalid",
                "errors": "synthetic validation failure",
            }
            cases.append(
                (
                    "validation",
                    validation_proposal,
                    validation_gateway,
                    "preflight_rejected",
                )
            )

            for case, proposal, gateway, expected in cases:
                with self.subTest(resource_type=resource_type, case=case):
                    _, gateway, adapter, prepared, _child, identity = (
                        await self.prepared_case(
                            resource_type,
                            proposal.action,
                            case=f"{resource_type}-{case}",
                            proposal=proposal,
                            gateway=gateway,
                        )
                    )
                    _locks, _executions, executor = self.components()
                    approval = SyntheticApprovalRecorder()
                    result = await executor.execute(
                        adapter=adapter,
                        prepared=prepared,
                        identity=identity,
                        approval_consumption=approval,
                    )
                    self.assertEqual(result.outcome, expected)
                    self.assertEqual(result.dispatch_count, 0)
                    self.assertEqual(approval.invocations, 0)
                    self.assertEqual(gateway.counters.dispatches, 0)

    async def test_static_validation_failure_for_every_family_is_pre_execution(self):
        invalid = {
            "automation": {"alias": "Missing trigger and action"},
            "script": {
                "alias": "Empty sequence",
                "sequence": [],
                "mode": "single",
            },
            "input_boolean": {"name": None},
            "input_number": {
                **valid_config("input_number"),
                "min": 30,
                "max": 10,
            },
        }
        for resource_type, proposed in invalid.items():
            with self.subTest(resource_type=resource_type):
                gateway = SyntheticConfigurationGateway()
                adapter = adapter_for(resource_type, "create", gateway)
                approval = SyntheticApprovalRecorder()
                with self.assertRaises(ValueError):
                    await adapter.prepare(
                        proposal_for(
                            resource_type,
                            "create",
                            proposed_config=proposed,
                        )
                    )
                self.assertEqual(approval.invocations, 0)
                self.assertEqual(gateway.counters.dispatches, 0)

    async def test_lock_conflict_consumes_no_approval(self):
        _, gateway, adapter, prepared, _child, identity = (
            await self.prepared_case(
                "automation", "update", case="lock-conflict"
            )
        )
        locks, _executions, executor = self.components()
        blocker = locks.acquire_once(
            (adapter.lock_requests(prepared)[0],),
            owner=LockOwner(
                "owner-blocker",
                "task-blocker",
                "plan-blocker",
                prepared.operation,
                "attempt-blocker",
            ),
            timing=LOCK_TIMING,
            now=self.clock.now(),
        )
        approval = SyntheticApprovalRecorder()
        result = await executor.execute(
            adapter=adapter,
            prepared=prepared,
            identity=identity,
            approval_consumption=approval,
        )
        self.assertEqual(result.outcome, "lock_conflict")
        self.assertEqual(approval.invocations, 0)
        self.assertEqual(gateway.counters.dispatches, 0)
        locks.release(blocker)

    async def test_pre_intent_cancellation_for_every_family_consumes_no_approval(self):
        for resource_type in (
            "automation",
            "script",
            "input_boolean",
            "input_number",
        ):
            with self.subTest(resource_type=resource_type):
                _, gateway, adapter, prepared, _child, identity = (
                    await self.prepared_case(
                        resource_type,
                        "update",
                        case=f"cancel-before-intent-{resource_type}",
                    )
                )
                cancellation_repository = None

                def cancel_after_preflight(stage: str) -> None:
                    if stage == "after_preflight_before_durable_intent":
                        assert cancellation_repository is not None
                        cancellation_repository.cancel(
                            identity.task_id, now=self.clock.now()
                        )

                _locks, cancellation_repository, executor = self.components(
                    fault_hook=cancel_after_preflight
                )
                approval = SyntheticApprovalRecorder()
                result = await executor.execute(
                    adapter=adapter,
                    prepared=prepared,
                    identity=identity,
                    approval_consumption=approval,
                )
                self.assertEqual(result.outcome, "cancelled_pre_dispatch")
                self.assertEqual(approval.invocations, 0)
                self.assertEqual(gateway.counters.dispatches, 0)

    async def test_approval_and_intent_failures_never_reach_gateway(self):
        for resource_type in (
            "automation",
            "script",
            "input_boolean",
            "input_number",
        ):
            with self.subTest(resource_type=resource_type, boundary="approval"):
                _, gateway, adapter, prepared, _child, identity = (
                    await self.prepared_case(
                        resource_type,
                        "update",
                        case=f"approval-failure-{resource_type}",
                    )
                )
                _locks, _executions, executor = self.components()
                approval = SyntheticApprovalRecorder(failures_remaining=1)
                with self.assertRaises(PreIntentRetryRequired):
                    await executor.execute(
                        adapter=adapter,
                        prepared=prepared,
                        identity=identity,
                        approval_consumption=approval,
                    )
                self.assertEqual(approval.invocations, 1)
                self.assertEqual(approval.consumptions, 0)
                self.assertEqual(gateway.counters.dispatches, 0)

            with self.subTest(resource_type=resource_type, boundary="intent"):
                class FailOnce:
                    triggered = False

                    def __call__(self, stage: str) -> None:
                        if (
                            stage == "before_durable_intent_persistence"
                            and not self.triggered
                        ):
                            self.triggered = True
                            raise OSError("synthetic durable intent failure")

                _, gateway, adapter, prepared, _child, identity = (
                    await self.prepared_case(
                        resource_type,
                        "update",
                        case=f"intent-failure-{resource_type}",
                    )
                )
                approval = SyntheticApprovalRecorder()
                _locks, executions, executor = self.components(
                    execution_fault_hook=FailOnce()
                )
                with self.assertRaises(PreIntentRetryRequired):
                    await executor.execute(
                        adapter=adapter,
                        prepared=prepared,
                        identity=identity,
                        approval_consumption=approval,
                    )
                record = executions.get(identity.task_id)
                self.assertIsNotNone(record)
                assert record is not None
                self.assertIsNone(record.dispatch_intent)
                self.assertEqual(record.dispatch_count, 0)
                self.assertEqual(gateway.counters.dispatches, 0)
                result = await executor.execute(
                    adapter=adapter,
                    prepared=prepared,
                    identity=identity,
                    approval_consumption=approval,
                )
                self.assertEqual(result.outcome, "succeeded_verified")
                self.assertEqual(approval.invocations, 2)
                self.assertEqual(approval.consumptions, 1)
                self.assertEqual(gateway.counters.dispatches, 1)

    async def test_response_loss_never_permits_a_second_write(self):
        for resource_type in (
            "automation",
            "script",
            "input_boolean",
            "input_number",
        ):
            for mode, expected, mutations in (
                ("response_lost_before_effect", "verification_mismatch", 0),
                ("response_lost_after_effect", "succeeded_verified", 1),
            ):
                case = f"{resource_type}-{mode}"
                with self.subTest(resource_type=resource_type, mode=mode):
                    _, gateway, adapter, prepared, _child, identity = (
                        await self.prepared_case(
                            resource_type, "update", case=case
                        )
                    )
                    gateway.dispatch_mode = mode
                    _locks, _executions, executor = self.components()
                    approval = SyntheticApprovalRecorder()
                    first = await executor.execute(
                        adapter=adapter,
                        prepared=prepared,
                        identity=identity,
                        approval_consumption=approval,
                    )
                    second = await executor.execute(
                        adapter=adapter,
                        prepared=prepared,
                        identity=identity,
                        approval_consumption=approval,
                    )
                    self.assertEqual(first.outcome, expected)
                    self.assertEqual(second.outcome, expected)
                    self.assertTrue(second.duplicate_execution)
                    self.assertEqual(gateway.counters.dispatches, 1)
                    self.assertEqual(
                        gateway.counters.simulated_mutations, mutations
                    )
                    self.assertEqual(approval.invocations, 1)

    async def test_post_intent_process_reconstruction_is_readback_only(self):
        for resource_type in (
            "automation",
            "script",
            "input_boolean",
            "input_number",
        ):
            for mode, mutations, expected in (
                (
                    "process_loss_before_effect",
                    0,
                    "verification_mismatch",
                ),
                (
                    "process_loss_after_effect",
                    1,
                    "succeeded_verified",
                ),
            ):
                case = f"reconstruct-{resource_type}-{mode}"
                with self.subTest(resource_type=resource_type, mode=mode):
                    _, gateway, adapter, prepared, _child, identity = (
                        await self.prepared_case(
                            resource_type, "update", case=case
                        )
                    )
                    gateway.dispatch_mode = mode
                    locks, executions, executor = self.components()
                    approval = SyntheticApprovalRecorder()
                    with self.assertRaises(SyntheticProcessLoss):
                        await executor.execute(
                            adapter=adapter,
                            prepared=prepared,
                            identity=identity,
                            approval_consumption=approval,
                        )
                    record = executions.get(identity.task_id)
                    self.assertIsNotNone(record)
                    assert record is not None
                    self.assertEqual(record.dispatch_count, 1)
                    # Expire the 60-second claim/lock lease without crossing
                    # the immutable 120-second post-dispatch evidence deadline.
                    self.clock.advance(61)
                    recovered = SharedOperationExecutor(
                        lock_store=locks,
                        execution_repository=executions,
                        lock_timing=LOCK_TIMING,
                        executor_timing=EXECUTOR_TIMING,
                        now=self.clock.now,
                        monotonic=self.clock.monotonic,
                        sleep=self.clock.sleep,
                    )
                    recovery_identity = replace(
                        identity,
                        request_id=f"request-recovery-{resource_type}",
                        owner_id=f"owner-recovery-{resource_type}",
                    )
                    result = await recovered.execute(
                        adapter=adapter,
                        prepared=prepared,
                        identity=recovery_identity,
                        approval_consumption=approval,
                    )
                    self.assertEqual(result.outcome, expected)
                    self.assertEqual(result.dispatch_count, 1)
                    self.assertEqual(gateway.counters.dispatches, 1)
                    self.assertEqual(
                        gateway.counters.simulated_mutations, mutations
                    )
                    self.assertEqual(approval.invocations, 1)

    async def test_cancellation_after_durable_intent_is_rejected_for_every_family(self):
        for resource_type in (
            "automation",
            "script",
            "input_boolean",
            "input_number",
        ):
            with self.subTest(resource_type=resource_type):
                _, gateway, adapter, prepared, _child, identity = (
                    await self.prepared_case(
                        resource_type,
                        "update",
                        case=f"cancel-after-intent-{resource_type}",
                    )
                )

                def lose_after_intent(stage: str) -> None:
                    if stage == "after_durable_intent_before_provider_invocation":
                        raise SimulatedProcessLoss()

                _locks, _executions, executor = self.components(
                    fault_hook=lose_after_intent
                )
                approval = SyntheticApprovalRecorder()
                with self.assertRaises(SimulatedProcessLoss):
                    await executor.execute(
                        adapter=adapter,
                        prepared=prepared,
                        identity=identity,
                        approval_consumption=approval,
                    )
                self.assertFalse(await executor.cancel(identity.task_id))
                self.assertEqual(approval.invocations, 1)
                self.assertEqual(gateway.counters.dispatches, 0)
                self.assertEqual(gateway.counters.simulated_mutations, 0)

class ResponseAndProcessLossTests(LifecycleTestCase):
    async def test_response_loss_before_effect_recovers_by_readback_without_redispatch(self):
        _p, gateway, adapter, prepared, preflight = await self.eligible()
        gateway.dispatch_mode = "response_lost_before_effect"
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        self.assertEqual(
            dispatch.outcome,
            NormalizedOperationOutcome.DISPATCH_INDETERMINATE,
        )
        observation = await harness.recover(prepared, recovery_context())
        verification = await harness.verify(prepared, observation)
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.VERIFICATION_MISMATCH,
        )
        self.assertEqual(gateway.counters.dispatches, 1)
        self.assertEqual(gateway.counters.simulated_mutations, 0)

    async def test_response_loss_after_effect_recovers_verified_without_redispatch(self):
        _p, gateway, adapter, prepared, preflight = await self.eligible()
        gateway.dispatch_mode = "response_lost_after_effect"
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        observation = await harness.recover(prepared, recovery_context())
        verification = await harness.verify(prepared, observation)
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
        )
        self.assertEqual(gateway.counters.dispatches, 1)
        self.assertEqual(gateway.counters.simulated_mutations, 1)

    async def test_process_loss_before_and_after_effect_never_redispatches(self):
        for mode, expected_mutations, expected_outcome in (
            (
                "process_loss_before_effect",
                0,
                NormalizedOperationOutcome.VERIFICATION_MISMATCH,
            ),
            (
                "process_loss_after_effect",
                1,
                NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
            ),
        ):
            with self.subTest(mode=mode):
                _p, gateway, adapter, prepared, preflight = await self.eligible()
                gateway.dispatch_mode = mode
                harness = ConfigurationLifecycleHarness(adapter)
                with self.assertRaises(SyntheticProcessLoss):
                    await adapter.dispatch(
                        prepared,
                        preflight,
                        before_dispatch=harness.intent,
                    )
                reconstructed = adapter_for("automation", "update", gateway)
                recovered = ConfigurationLifecycleHarness(reconstructed)
                observation = await recovered.recover(
                    prepared, recovery_context()
                )
                verification = await recovered.verify(prepared, observation)
                self.assertEqual(verification.outcome, expected_outcome)
                self.assertEqual(gateway.counters.dispatches, 1)
                self.assertEqual(
                    gateway.counters.simulated_mutations, expected_mutations
                )

    async def test_process_loss_after_intent_before_provider_is_observation_only(self):
        _p, gateway, adapter, prepared, preflight = await self.eligible()
        intent_committed = False

        async def commit_then_lose_process():
            nonlocal intent_committed
            intent_committed = True
            raise SyntheticProcessLoss()

        with self.assertRaises(SyntheticProcessLoss):
            await adapter.dispatch(
                prepared,
                preflight,
                before_dispatch=commit_then_lose_process,
            )
        self.assertTrue(intent_committed)
        reconstructed = adapter_for("automation", "update", gateway)
        harness = ConfigurationLifecycleHarness(reconstructed)
        observation = await harness.recover(prepared, recovery_context())
        verification = await harness.verify(prepared, observation)
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.VERIFICATION_MISMATCH,
        )
        self.assertEqual(gateway.counters.dispatches, 0)
        self.assertEqual(gateway.counters.simulated_mutations, 0)

    async def test_unreadable_recovery_observes_until_fixed_deadline_then_manual_review(self):
        _p, gateway, adapter, prepared, _preflight = await self.eligible()
        gateway.read_error_count = 1
        harness = ConfigurationLifecycleHarness(adapter)
        observation = await harness.recover(prepared, recovery_context())
        self.assertFalse(observation.observation_complete)
        self.assertEqual(observation.outcome, NormalizedOperationOutcome.OBSERVING)
        expired = await harness.recover(
            prepared,
            recovery_context(deadline="2026-08-04T11:59:59+00:00"),
        )
        self.assertEqual(
            expired.outcome,
            NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED,
        )
        self.assertEqual(gateway.counters.dispatches, 0)

    async def test_contradictory_recovery_context_fails_closed(self):
        _p, gateway, adapter, prepared = await self.prepared()
        harness = ConfigurationLifecycleHarness(adapter)
        context = replace(
            recovery_context(),
            dispatch_intent_recorded=False,
        )
        observation = await harness.recover(prepared, context)
        self.assertEqual(
            observation.outcome,
            NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED,
        )
        self.assertEqual(gateway.counters.dispatches, 0)

    async def test_intent_without_provider_call_still_recovers_by_readback_only(self):
        _p, gateway, adapter, prepared = await self.prepared()
        harness = ConfigurationLifecycleHarness(adapter)
        context = replace(
            recovery_context(),
            provider_invocation_may_have_occurred=False,
        )
        observation = await harness.recover(prepared, context)
        self.assertEqual(
            observation.outcome,
            NormalizedOperationOutcome.OBSERVING,
        )
        self.assertTrue(observation.observation_complete)
        self.assertEqual(gateway.counters.dispatches, 0)
        self.assertEqual(gateway.counters.simulated_mutations, 0)

    async def test_pre_intent_process_loss_can_only_resume_through_fresh_preflight(self):
        _p, gateway, adapter, prepared, _preflight = await self.eligible()
        reconstructed = adapter_for("automation", "update", gateway)
        renewed = await reconstructed.preflight(
            prepared,
            acquired_locks=reconstructed.lock_requests(prepared),
        )
        self.assertTrue(renewed.eligible)
        self.assertEqual(gateway.counters.dispatches, 0)
        harness = ConfigurationLifecycleHarness(reconstructed)
        dispatched = await reconstructed.dispatch(
            prepared,
            renewed,
            before_dispatch=harness.intent,
        )
        self.assertEqual(dispatched.adapter_dispatch_count, 1)
        self.assertEqual(gateway.counters.dispatches, 1)
        self.assertEqual(gateway.counters.simulated_mutations, 1)

    async def test_all_post_intent_reconstruction_points_are_readback_only(self):
        _p, gateway, adapter, prepared, preflight = await self.eligible()
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        observation = await harness.observe(prepared, dispatch)
        verification = await harness.verify(prepared, observation)
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
        )

        # These snapshots represent loss after response, before/during
        # readback, during verification, and after verification before release.
        for point in (
            "after_provider_response",
            "before_readback",
            "during_readback",
            "during_verification",
            "after_verification_before_lock_release",
        ):
            with self.subTest(point=point):
                reconstructed = adapter_for("automation", "update", gateway)
                recovered = ConfigurationLifecycleHarness(reconstructed)
                readback = await recovered.recover(
                    prepared,
                    recovery_context(response_received=True),
                )
                result = await recovered.verify(prepared, readback)
                self.assertEqual(
                    result.outcome,
                    NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
                )
                self.assertEqual(gateway.counters.dispatches, 1)
                self.assertEqual(gateway.counters.simulated_mutations, 1)


class ExternalWriterConcurrencyTests(LifecycleTestCase):
    async def test_external_writer_before_lock_or_preflight_is_stale_rejection(self):
        proposal = proposal_for("automation", "update")
        external = proposal.current_config()
        external["alias"] = "external before lock"
        gateway = SyntheticConfigurationGateway(
            {("automation", proposal.target_id): external}
        )
        adapter = adapter_for("automation", "update", gateway)
        prepared = await adapter.prepare(proposal)
        preflight = await adapter.preflight(
            prepared, acquired_locks=adapter.lock_requests(prepared)
        )
        self.assertIn("stale_target_state", preflight.diagnostic_codes)
        self.assertEqual(gateway.counters.dispatches, 0)

    async def test_external_writer_after_preflight_exposes_existing_non_atomic_limitation(self):
        proposal, gateway, adapter, prepared, preflight = await self.eligible()
        external = proposal.current_config()
        external["alias"] = "external after preflight"
        gateway.states[("automation", proposal.target_id)] = external
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        observation = await harness.observe(prepared, dispatch)
        verification = await harness.verify(prepared, observation)
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
        )
        self.assertEqual(gateway.counters.dispatches, 1)
        self.assertEqual(gateway.counters.simulated_mutations, 1)

    async def test_external_writer_during_intent_exposes_non_atomic_limitation(self):
        proposal, gateway, adapter, prepared, preflight = await self.eligible()

        async def intent_with_external_write():
            external = proposal.current_config()
            external["alias"] = "external inside non-atomic window"
            gateway.states[("automation", proposal.target_id)] = external

        dispatch = await adapter.dispatch(
            prepared,
            preflight,
            before_dispatch=intent_with_external_write,
        )
        harness = ConfigurationLifecycleHarness(adapter)
        observation = await harness.observe(prepared, dispatch)
        verification = await harness.verify(prepared, observation)
        # Exact final readback cannot prove the external edit was not
        # overwritten between the final preread and the provider save.
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
        )
        self.assertEqual(gateway.counters.dispatches, 1)
        self.assertEqual(gateway.counters.simulated_mutations, 1)

    async def test_external_writer_immediately_after_save_is_verification_mismatch(self):
        proposal, gateway, adapter, prepared, preflight = await self.eligible()

        def external_after_save(gateway, resource_type, target):
            gateway.states[(resource_type, target)]["alias"] = "external after save"

        gateway.after_write_hook = external_after_save
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        observation = await harness.observe(prepared, dispatch)
        verification = await harness.verify(prepared, observation)
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.VERIFICATION_MISMATCH,
        )
        self.assertEqual(gateway.counters.dispatches, 1)


class MetricsAndSanitizationTests(LifecycleTestCase):
    async def test_metrics_and_events_contain_only_closed_labels_and_hashes(self):
        proposal = proposal_for("automation", "update")
        gateway = SyntheticConfigurationGateway(
            {("automation", proposal.target_id): proposal.current_config()}
        )
        metrics = ConfigurationAdapterMetrics()
        sink = InMemoryConfigurationEventSink()
        adapter = ConfigurationOperationAdapter(
            "automation",
            "update",
            gateway,
            metrics=metrics,
            event_sink=sink,
            now=lambda: __import__(
                "tests.f3_configuration_fixtures",
                fromlist=["FIXED_NOW"],
            ).FIXED_NOW,
        )
        prepared = await adapter.prepare(proposal)
        preflight = await adapter.preflight(
            prepared, acquired_locks=adapter.lock_requests(prepared)
        )
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        observation = await harness.observe(prepared, dispatch)
        await harness.verify(prepared, observation)
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["dispatch_attempts:automation:update"], 1)
        self.assertEqual(snapshot["verification_successes:automation:update"], 1)
        rendered = repr((snapshot, sink.snapshot()))
        self.assertNotIn("Porch light", rendered)
        self.assertNotIn("light.synthetic", rendered)
        self.assertNotIn("notify", rendered)
        self.assertTrue(all(len(event.target_identity_hash) == 64 for event in sink.snapshot()))


if __name__ == "__main__":
    unittest.main()
