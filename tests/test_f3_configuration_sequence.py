"""Ordered-plan, duplicate, cancellation, and outcome conformance."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.f3.contracts import NormalizedOperationOutcome
from ha_mcp_engineering.f3_configuration.outcomes import (
    CONFIGURATION_OUTCOME_MAPPING,
    outcome_mapping,
)
from ha_mcp_engineering.f3_configuration.sequence import (
    DuplicateExecutionSnapshot,
    SequenceNextAction,
    SequenceStepState,
    cancellation_decision,
    classify_duplicate_execution,
    prepare_configuration_sequence,
    recover_sequence_position,
    single_operation_child_descriptor,
)

from tests.f3_configuration_fixtures import (
    SyntheticConfigurationGateway,
    adapter_for,
    proposal_for,
)


class OrderedSequenceTests(unittest.IsolatedAsyncioTestCase):
    async def sequence(self):
        first_proposal = proposal_for(
            "automation", "update", operation_id="automation_step", order=0
        )
        second_proposal = proposal_for(
            "input_boolean",
            "create",
            operation_id="helper_step",
            order=1,
            depends_on=("automation_step",),
            plan_id=first_proposal.plan_id,
            task_id=first_proposal.task_id,
        )
        first_gateway = SyntheticConfigurationGateway(
            {
                ("automation", first_proposal.target_id): (
                    first_proposal.current_config()
                )
            }
        )
        first = await adapter_for(
            "automation", "update", first_gateway
        ).prepare(first_proposal)
        second = await adapter_for(
            "input_boolean", "create", SyntheticConfigurationGateway()
        ).prepare(second_proposal)
        return prepare_configuration_sequence((first, second))

    async def test_complete_lock_set_is_bound_before_first_dispatch(self):
        sequence = await self.sequence()
        self.assertEqual(len(sequence.operations), 2)
        self.assertEqual(
            {lock.key for lock in sequence.lock_requests},
            {
                "automation:porch_light",
                "helper:input_boolean.vacation_mode",
                "home_assistant:core",
                "reload:automation",
                "reload:input_boolean",
            },
        )
        self.assertEqual(len(sequence.lock_set_hash), 64)
        self.assertEqual(len(sequence.sequence_hash), 64)

    async def test_operation_one_verified_then_two_preflight_failure_is_partial(self):
        sequence = await self.sequence()
        decision = recover_sequence_position(
            sequence,
            (
                SequenceStepState.SUCCEEDED_VERIFIED,
                SequenceStepState.FAILED_PRE_DISPATCH,
            ),
        )
        self.assertEqual(decision.action, SequenceNextAction.STOP)
        self.assertEqual(decision.terminal_outcome, "partial_application")
        self.assertEqual(decision.completed_operation_ids, ("automation_step",))
        self.assertFalse(decision.redispatch_prohibited)

    async def test_operation_one_verified_then_two_indeterminate_observes_only(self):
        sequence = await self.sequence()
        decision = recover_sequence_position(
            sequence,
            (
                SequenceStepState.SUCCEEDED_VERIFIED,
                SequenceStepState.INTENT_COMMITTED,
            ),
        )
        self.assertEqual(decision.action, SequenceNextAction.OBSERVE)
        self.assertEqual(decision.operation_id, "helper_step")
        self.assertTrue(decision.redispatch_prohibited)

    async def test_process_loss_between_operations_records_position_without_dispatch_authority(self):
        sequence = await self.sequence()
        decision = recover_sequence_position(
            sequence,
            (
                SequenceStepState.SUCCEEDED_VERIFIED,
                SequenceStepState.NOT_STARTED,
            ),
        )
        self.assertEqual(decision.action, SequenceNextAction.NOT_STARTED)
        self.assertEqual(decision.operation_id, "helper_step")
        self.assertEqual(decision.completed_operation_ids, ("automation_step",))
        self.assertFalse(decision.dispatch_authorized)

    async def test_dependency_failure_keeps_later_operation_undispatched(self):
        sequence = await self.sequence()
        decision = recover_sequence_position(
            sequence,
            (
                SequenceStepState.FAILED_PRE_DISPATCH,
                SequenceStepState.BLOCKED_BY_PRIOR_OUTCOME,
            ),
        )
        self.assertEqual(decision.action, SequenceNextAction.STOP)
        self.assertEqual(decision.undispatched_operation_ids, ("helper_step",))
        self.assertEqual(decision.completed_operation_ids, ())

    async def test_all_verified_is_deterministic_terminal_success(self):
        sequence = await self.sequence()
        first = recover_sequence_position(
            sequence,
            (
                SequenceStepState.SUCCEEDED_VERIFIED,
                SequenceStepState.SUCCEEDED_VERIFIED,
            ),
        )
        second = recover_sequence_position(
            sequence,
            (
                SequenceStepState.SUCCEEDED_VERIFIED,
                SequenceStepState.SUCCEEDED_VERIFIED,
            ),
        )
        self.assertEqual(first, second)
        self.assertEqual(first.action, SequenceNextAction.COMPLETE)
        self.assertEqual(first.terminal_outcome, "succeeded_verified")

    async def test_noncanonical_order_and_forward_dependency_fail_before_execution(self):
        sequence = await self.sequence()
        reversed_operations = tuple(reversed(sequence.operations))
        with self.assertRaises(ValueError):
            prepare_configuration_sequence(reversed_operations)

    async def test_child_descriptors_are_deterministic_distinct_and_task_bound(self):
        sequence = await self.sequence()
        repeated = await self.sequence()
        self.assertEqual(sequence.child_descriptors, repeated.child_descriptors)
        self.assertEqual(
            {item.public_task_id for item in sequence.child_descriptors},
            {sequence.task_id},
        )
        self.assertEqual(
            {item.plan_id for item in sequence.child_descriptors},
            {sequence.plan_id},
        )
        self.assertEqual(
            len({item.operation_id for item in sequence.child_descriptors}), 2
        )
        self.assertEqual(
            len({item.attempt_id for item in sequence.child_descriptors}), 2
        )
        self.assertTrue(
            all(len(item.descriptor_hash) == 64 for item in sequence.child_descriptors)
        )

    async def test_sequence_model_persists_and_dispatches_nothing(self):
        first_proposal = proposal_for("automation", "update")
        gateway = SyntheticConfigurationGateway(
            {("automation", first_proposal.target_id): first_proposal.current_config()}
        )
        prepared = await adapter_for(
            "automation", "update", gateway
        ).prepare(first_proposal)
        sequence = prepare_configuration_sequence((prepared,))
        child = single_operation_child_descriptor(sequence)
        self.assertEqual(child.public_task_id, prepared.task_id)
        self.assertEqual(gateway.counters.dispatches, 0)
        self.assertEqual(gateway.counters.simulated_mutations, 0)
        source = (
            ROOT
            / "hass_mcp_engineering_beta"
            / "ha_mcp_engineering"
            / "f3_configuration"
            / "sequence.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("DurableExecutionRepository", source)
        self.assertNotIn("SharedOperationExecutor", source)
        self.assertNotIn("gateway.write", source)

    async def test_multi_operation_sequence_cannot_be_one_f3_execution(self):
        with self.assertRaisesRegex(ValueError, "cannot use one F3 execution"):
            single_operation_child_descriptor(await self.sequence())

    async def test_duplicate_exact_target_is_rejected(self):
        sequence = await self.sequence()
        first = sequence.operations[0]
        duplicate = first.__class__(
            **{
                **first.__dict__,
                "operation_id": "duplicate_target",
                "order": 1,
                "depends_on": (first.operation_id,),
            }
        )
        with self.assertRaisesRegex(ValueError, "targets must be unique"):
            prepare_configuration_sequence((first, duplicate))

    async def test_one_to_eight_operation_bound_is_explicit(self):
        operations = []
        for index in range(8):
            proposal = proposal_for(
                "automation",
                "update",
                operation_id=f"step_{index}",
                order=index,
                depends_on=(
                    () if index == 0 else (f"step_{index - 1}",)
                ),
            )
            proposal = replace(proposal, target_id=f"target_{index}")
            gateway = SyntheticConfigurationGateway()
            operations.append(
                await adapter_for(
                    "automation", "update", gateway
                ).prepare(proposal)
            )
        sequence = prepare_configuration_sequence(operations)
        self.assertEqual(len(sequence.operations), 8)
        self.assertEqual(len(sequence.child_descriptors), 8)
        self.assertEqual(
            {
                item.public_task_id
                for item in sequence.child_descriptors
            },
            {operations[0].task_id},
        )
        with self.assertRaises(ValueError):
            prepare_configuration_sequence(())
        with self.assertRaises(ValueError):
            prepare_configuration_sequence((*operations, operations[-1]))


class DuplicateApplyTests(unittest.TestCase):
    def test_active_duplicate_joins_without_task_lock_or_dispatch(self):
        snapshot = DuplicateExecutionSnapshot(
            task_id="b" * 32,
            plan_id="a" * 32,
            active=True,
            terminal=False,
            dispatch_count=1,
        )
        decision = classify_duplicate_execution(
            snapshot,
            requested_task_id=snapshot.task_id,
            requested_plan_id=snapshot.plan_id,
        )
        self.assertTrue(decision.reuse_existing_task)
        self.assertFalse(decision.acquire_locks)
        self.assertFalse(decision.create_task)
        self.assertFalse(decision.dispatch_permitted)
        self.assertEqual(decision.result, "join_active_task")

    def test_terminal_duplicate_returns_terminal_without_redispatch(self):
        snapshot = DuplicateExecutionSnapshot(
            task_id="b" * 32,
            plan_id="a" * 32,
            active=False,
            terminal=True,
            dispatch_count=1,
        )
        decision = classify_duplicate_execution(
            snapshot,
            requested_task_id=snapshot.task_id,
            requested_plan_id=snapshot.plan_id,
        )
        self.assertEqual(decision.result, "return_terminal_task")
        self.assertFalse(decision.dispatch_permitted)

    def test_plan_identity_does_not_reuse_unrelated_or_corrupt_task(self):
        snapshot = DuplicateExecutionSnapshot(
            task_id="b" * 32,
            plan_id="a" * 32,
            active=True,
            terminal=False,
            dispatch_count=0,
        )
        for task_id, plan_id in (
            ("c" * 32, snapshot.plan_id),
            (snapshot.task_id, "d" * 32),
        ):
            with self.subTest(task_id=task_id, plan_id=plan_id):
                with self.assertRaises(ValueError):
                    classify_duplicate_execution(
                        snapshot,
                        requested_task_id=task_id,
                        requested_plan_id=plan_id,
                    )


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    async def _sequence(self):
        return await OrderedSequenceTests().sequence()

    async def test_cancellation_before_first_intent_cancels_the_task(self):
        sequence = await self._sequence()
        decision = cancellation_decision(
            sequence,
            (
                SequenceStepState.NOT_STARTED,
                SequenceStepState.NOT_STARTED,
            ),
        )
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.outcome, "cancelled_pre_dispatch")
        self.assertEqual(decision.completed_operation_ids, ())
        self.assertEqual(
            decision.cancelled_operation_ids,
            ("automation_step", "helper_step"),
        )

    async def test_cancellation_after_any_intent_is_rejected_and_is_not_rollback(self):
        sequence = await self._sequence()
        decision = cancellation_decision(
            sequence,
            (
                SequenceStepState.INTENT_COMMITTED,
                SequenceStepState.LATER_OPERATIONS_UNDISPATCHED,
            ),
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(
            decision.outcome, "cancellation_rejected_after_possible_dispatch"
        )
        self.assertEqual(decision.undispatched_operation_ids, ("helper_step",))
        self.assertNotIn("rollback", decision.outcome)

    async def test_cancellation_after_verified_work_is_rejected_without_partial_cancel(self):
        sequence = await self._sequence()
        decision = cancellation_decision(
            sequence,
            (
                SequenceStepState.SUCCEEDED_VERIFIED,
                SequenceStepState.LATER_OPERATIONS_UNDISPATCHED,
            ),
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.completed_operation_ids, ("automation_step",))
        self.assertEqual(decision.cancelled_operation_ids, ())
        self.assertEqual(decision.undispatched_operation_ids, ("helper_step",))
        self.assertNotEqual(decision.outcome, "cancelled_pre_dispatch")


class OutcomeMappingTests(unittest.TestCase):
    def test_every_frozen_outcome_has_one_existing_task_state_projection(self):
        self.assertEqual(
            set(CONFIGURATION_OUTCOME_MAPPING), set(NormalizedOperationOutcome)
        )
        allowed_states = {
            "failed_pre_dispatch",
            "failed_post_dispatch",
            "observing",
            "succeeded_verified",
            "manual_review_required",
            "cancelled_pre_dispatch",
        }
        for outcome in NormalizedOperationOutcome:
            with self.subTest(outcome=outcome.value):
                mapping = outcome_mapping(outcome)
                self.assertIn(mapping.task_state, allowed_states)
                self.assertFalse(mapping.dispatch_possible)

    def test_possible_dispatch_outcomes_allow_only_readback_or_governed_review(self):
        for outcome in (
            NormalizedOperationOutcome.DISPATCH_INDETERMINATE,
            NormalizedOperationOutcome.OBSERVING,
            NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED,
        ):
            with self.subTest(outcome=outcome.value):
                self.assertIn(
                    outcome_mapping(outcome).permitted_recovery,
                    {"readback_only", "governed_reconciliation"},
                )


if __name__ == "__main__":
    unittest.main()
