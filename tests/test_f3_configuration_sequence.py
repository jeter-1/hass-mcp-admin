"""Ordered-plan, duplicate, cancellation, and outcome conformance."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from f3_contracts.operation_adapter import NormalizedOperationOutcome
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

    async def test_process_loss_between_operations_resumes_exact_pending_position(self):
        sequence = await self.sequence()
        decision = recover_sequence_position(
            sequence,
            (
                SequenceStepState.SUCCEEDED_VERIFIED,
                SequenceStepState.PENDING,
            ),
        )
        self.assertEqual(decision.action, SequenceNextAction.DISPATCH)
        self.assertEqual(decision.operation_id, "helper_step")
        self.assertEqual(decision.completed_operation_ids, ("automation_step",))

    async def test_dependency_failure_keeps_later_operation_undispatched(self):
        sequence = await self.sequence()
        decision = recover_sequence_position(
            sequence,
            (
                SequenceStepState.FAILED_PRE_DISPATCH,
                SequenceStepState.PENDING,
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

    async def test_pre_intent_cancellation_preserves_verified_and_cancels_pending(self):
        sequence = await self._sequence()
        decision = cancellation_decision(
            sequence,
            (
                SequenceStepState.SUCCEEDED_VERIFIED,
                SequenceStepState.PENDING,
            ),
        )
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.outcome, "cancelled_pre_dispatch")
        self.assertEqual(decision.completed_operation_ids, ("automation_step",))
        self.assertEqual(decision.cancelled_operation_ids, ("helper_step",))

    async def test_cancellation_after_intent_is_rejected_and_is_not_rollback(self):
        sequence = await self._sequence()
        decision = cancellation_decision(
            sequence,
            (
                SequenceStepState.SUCCEEDED_VERIFIED,
                SequenceStepState.INTENT_COMMITTED,
            ),
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.outcome, "cancellation_rejected_after_intent")
        self.assertNotIn("rollback", decision.outcome)


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
