import copy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.approval_web import (  # noqa: E402
    create_approval_application,
)
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalActionKind,
    ApprovalPolicyClass,
    ApprovalState,
    ChangeApproval,
    ChangeOperation,
    ChangePolicyDecision,
    ConfigurationOperation,
    PhysicalConsequence,
    RiskDelta,
)
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    POLICY_VERSION,
    OperationPolicyClassification,
    aggregate_policy_classifications,
    evaluate_change_policy,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    APPROVAL_AUTHORITY_VERSION,
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.governance.task_models import (  # noqa: E402
    ExecutionTaskState,
    TASK_SCHEMA_VERSION,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
)
from tests.test_beta25_external_approval import (  # noqa: E402
    Clock,
    CURRENT,
    FakeGateway,
)


ADMIN_A = "home_assistant_admin_ingress:admin-a"
ADMIN_B = "home_assistant_admin_ingress:admin-b"


class RuntimeShim:
    def __init__(self, service):
        self.service = service

    def require(self):
        return self.service


class F2PolicyModelTests(unittest.TestCase):
    def test_exact_enums_and_immutable_decision(self):
        self.assertEqual(
            [item.value for item in ApprovalPolicyClass],
            ["standard_admin", "elevated_admin", "prohibited"],
        )
        self.assertEqual(
            [item.value for item in RiskDelta],
            ["none", "low", "moderate", "high", "critical"],
        )
        self.assertEqual(
            [item.value for item in PhysicalConsequence],
            ["none", "indirect", "direct", "safety_critical"],
        )
        for enum_type in (
            ApprovalPolicyClass,
            RiskDelta,
            PhysicalConsequence,
            ApprovalActionKind,
        ):
            with self.subTest(enum=enum_type.__name__):
                with self.assertRaises(ValueError):
                    enum_type("caller_selected")

        decision = ChangePolicyDecision(
            policy_version=POLICY_VERSION,
            policy_class=ApprovalPolicyClass.STANDARD_ADMIN,
            risk_delta=RiskDelta.LOW,
            physical_consequence=PhysicalConsequence.NONE,
            reason_codes=("bounded_reason",),
            required_acknowledgements=(
                ApprovalActionKind.PLAN_APPROVAL,
            ),
            policy_subject_hash="a" * 64,
            policy_decision_hash="b" * 64,
        )
        with self.assertRaises(FrozenInstanceError):
            decision.policy_class = ApprovalPolicyClass.PROHIBITED
        self.assertEqual(
            ChangePolicyDecision.from_dict(decision.to_dict()), decision
        )
        malformed = decision.to_dict()
        malformed["caller_policy_override"] = "standard_admin"
        with self.assertRaises(ValueError):
            ChangePolicyDecision.from_dict(malformed)

    def test_strictest_policy_aggregation_is_order_independent(self):
        standard = OperationPolicyClassification(
            ApprovalPolicyClass.STANDARD_ADMIN,
            RiskDelta.MODERATE,
            PhysicalConsequence.INDIRECT,
            ("standard",),
        )
        elevated = OperationPolicyClassification(
            ApprovalPolicyClass.ELEVATED_ADMIN,
            RiskDelta.HIGH,
            PhysicalConsequence.DIRECT,
            ("elevated",),
        )
        prohibited = OperationPolicyClassification(
            ApprovalPolicyClass.PROHIBITED,
            RiskDelta.CRITICAL,
            PhysicalConsequence.SAFETY_CRITICAL,
            ("prohibited",),
        )
        forward = aggregate_policy_classifications(
            (standard, elevated, prohibited)
        )
        reverse = aggregate_policy_classifications(
            (prohibited, elevated, standard)
        )
        self.assertEqual(forward, reverse)
        self.assertEqual(
            forward.policy_class, ApprovalPolicyClass.PROHIBITED
        )
        self.assertEqual(forward.risk_delta, RiskDelta.CRITICAL)
        self.assertEqual(
            forward.physical_consequence,
            PhysicalConsequence.SAFETY_CRITICAL,
        )


class F2ApprovalTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.gateway = FakeGateway()
        self.repository = ChangePlanRepository(
            Path(self.temp.name) / "plans"
        )
        self.service = ChangeGovernanceService(
            self.repository,
            self.gateway,
            now=self.clock,
        )
        self.telemetry, self.context = begin_request("f2-request")
        self.telemetry.caller_id = "mcp-requester"

    async def asyncTearDown(self):
        end_request(self.context)
        self.temp.cleanup()

    async def create_standard(self):
        proposed = copy.deepcopy(CURRENT)
        proposed["description"] = "Standard F2 metadata change"
        return await self.service.create_plan(
            title="Standard F2 plan",
            description="A low-risk metadata update",
            operation="update_automation",
            automation_id="fixture",
            proposed_config=proposed,
        )

    async def create_elevated(self):
        proposed = copy.deepcopy(CURRENT)
        proposed["action"] = [
            {
                "service": "light.turn_on",
                "target": {"entity_id": "light.f2_fixture"},
            }
        ]
        return await self.service.create_plan(
            title="Elevated F2 plan",
            description="A future physical action configuration",
            operation="update_automation",
            automation_id="fixture",
            proposed_config=proposed,
        )

    async def create_prohibited(self):
        proposed = copy.deepcopy(CURRENT)
        proposed["action"] = [
            {
                "service": "lock.unlock",
                "target": {"entity_id": "lock.f2_fixture"},
            }
        ]
        return await self.service.create_plan(
            title="Prohibited F2 plan",
            description="A safety-critical fixture",
            operation="update_automation",
            automation_id="fixture",
            proposed_config=proposed,
        )

    async def decide(
        self,
        created,
        pending,
        *,
        principal=ADMIN_A,
        decision="approve",
    ):
        _, csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        return await self.service.decide_external_approval(
            plan_id=created["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=created["plan_hash"],
            approval_kind="apply",
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision=decision,
            approver_principal=principal,
        )

    async def fully_approve(self, created, *, principal=ADMIN_A):
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        result = await self.decide(
            created, pending, principal=principal
        )
        if result.get("status") == "approval_pending":
            result = await self.decide(
                created, result, principal=principal
            )
        return result


class F2ApprovalWorkflowTests(F2ApprovalTestCase):
    async def test_every_existing_writable_operation_has_explicit_policy(self):
        created = await self.create_standard()
        source = self.repository.get(created["plan_id"])
        assert source is not None
        operation_expectations = {
            ChangeOperation.CREATE_FULL_BACKUP: "standard_admin",
            ChangeOperation.CONTROLLED_RELOAD: "standard_admin",
            ChangeOperation.RESTART_ADDON: "elevated_admin",
            ChangeOperation.RESTART_HOME_ASSISTANT: "elevated_admin",
            ChangeOperation.UPDATE_AUTOMATION: "standard_admin",
        }
        for operation, expected in operation_expectations.items():
            with self.subTest(operation=operation.value):
                candidate = copy.deepcopy(source)
                candidate.operation = operation
                self.assertEqual(
                    evaluate_change_policy(candidate).policy_class.value,
                    expected,
                )

        helper_plan = copy.deepcopy(source)
        helper_plan.contract_version = 2
        helper_plan.operation = ChangeOperation.CONFIGURATION_PLAN
        helper_plan.operations = [
            ConfigurationOperation(
                operation_id="helper_update",
                order=0,
                depends_on=[],
                resource_type="helper",
                action="update",
                target_id="input_boolean.f2_fixture",
                helper_type="input_boolean",
                proposed_config={"name": "F2 fixture"},
                current_config={"name": "Before"},
                normalized_proposed_config={"name": "F2 fixture"},
                normalized_current_config={"name": "Before"},
                current_state_fingerprint="a" * 64,
                proposed_config_hash="b" * 64,
                normalization_version=1,
                risk=copy.deepcopy(source.risk),
            )
        ]
        self.assertEqual(
            evaluate_change_policy(helper_plan).policy_class,
            ApprovalPolicyClass.STANDARD_ADMIN,
        )
        helper_plan.operations = []
        self.assertEqual(
            evaluate_change_policy(helper_plan).policy_class,
            ApprovalPolicyClass.PROHIBITED,
        )

    async def test_policy_mapping_and_hash_binding(self):
        standard = await self.create_standard()
        elevated = await self.create_elevated()
        prohibited = await self.create_prohibited()
        self.assertEqual(
            standard["policy_decision"]["policy_class"],
            "standard_admin",
        )
        self.assertEqual(
            elevated["policy_decision"]["policy_class"],
            "elevated_admin",
        )
        self.assertEqual(
            elevated["policy_decision"]["physical_consequence"],
            "direct",
        )
        self.assertEqual(
            prohibited["policy_decision"]["policy_class"],
            "prohibited",
        )
        plan = self.repository.get(standard["plan_id"])
        assert plan is not None and plan.policy_decision is not None
        original_hash = self.service.plan_hash(plan)
        original_decision_hash = plan.policy_decision.policy_decision_hash
        plan.target.target_id = "mutated_fixture"
        self.assertNotEqual(self.service.plan_hash(plan), original_hash)
        self.assertNotEqual(
            evaluate_change_policy(plan).policy_decision_hash,
            original_decision_hash,
        )

    async def test_standard_bundle_consumes_with_durable_task(self):
        created = await self.create_standard()
        granted = await self.fully_approve(created)
        self.assertEqual(granted["status"], "approved")
        applied = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual(applied["status"], "applied")
        task = self.service.task_repository.get_for_plan(
            created["plan_id"]
        )
        self.assertIsNotNone(task)
        assert task is not None
        self.assertEqual(task.task_schema_version, TASK_SCHEMA_VERSION)
        self.assertEqual(
            task.approval_reference["authority_version"],
            APPROVAL_AUTHORITY_VERSION,
        )
        self.assertEqual(
            task.approval_reference["approval_bundle_state"],
            "consumed",
        )
        self.assertEqual(
            task.approval_reference["plan_approval"]["state"],
            "consumed",
        )
        self.assertEqual(self.gateway.writes, 1)

    async def test_elevated_sequence_same_principal_and_task_evidence(self):
        created = await self.create_elevated()
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual(pending["approval_action"], "plan_approval")
        with self.assertRaises(GovernanceError) as sequence_error:
            _, csrf = await self.service.issue_external_csrf(
                created["plan_id"], pending["challenge_id"]
            )
            await self.service.decide_external_approval(
                plan_id=created["plan_id"],
                challenge_id=pending["challenge_id"],
                expected_plan_hash=created["plan_hash"],
                approval_kind="apply",
                approval_action="elevated_risk_acknowledgement",
                csrf_nonce=csrf,
                decision="approve",
                approver_principal=ADMIN_A,
            )
        self.assertEqual(
            sequence_error.exception.code,
            ErrorCode.APPROVAL_SEQUENCE_FAILURE,
        )

        second = await self.decide(created, pending, principal=ADMIN_A)
        self.assertEqual(second["status"], "approval_pending")
        self.assertEqual(
            second["approval_action"],
            "elevated_risk_acknowledgement",
        )
        with self.assertRaises(GovernanceError) as not_ready:
            await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )
        self.assertEqual(
            not_ready.exception.code,
            ErrorCode.ELEVATED_RISK_ACKNOWLEDGEMENT_REQUIRED,
        )
        self.assertIsNone(
            self.service.task_repository.get_for_plan(created["plan_id"])
        )
        self.assertEqual(self.gateway.writes, 0)

        with self.assertRaises(GovernanceError) as mismatch:
            await self.decide(created, second, principal=ADMIN_B)
        self.assertEqual(
            mismatch.exception.code,
            ErrorCode.APPROVAL_PRINCIPAL_MISMATCH,
        )
        granted = await self.decide(created, second, principal=ADMIN_A)
        self.assertEqual(granted["status"], "approved")
        approved_plan = self.repository.get(created["plan_id"])
        assert (
            approved_plan is not None
            and approved_plan.policy_decision is not None
        )
        plan_hash_before_apply = self.service.plan_hash(approved_plan)
        policy_hash_before_apply = (
            approved_plan.policy_decision.policy_decision_hash
        )
        self.assertEqual(
            approved_plan.policy_decision.policy_class.value,
            "elevated_admin",
        )
        self.assertEqual(
            approved_plan.policy_decision.risk_delta.value, "moderate"
        )
        self.assertEqual(
            approved_plan.policy_decision.physical_consequence.value,
            "direct",
        )
        self.assertEqual(
            [
                item.value
                for item in approved_plan.policy_decision.required_acknowledgements
            ],
            ["plan_approval", "elevated_risk_acknowledgement"],
        )
        applied = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual(applied["status"], "applied")
        task = self.service.task_repository.get_for_plan(
            created["plan_id"]
        )
        assert task is not None
        reference = task.approval_reference
        self.assertEqual(reference["policy_class"], "elevated_admin")
        self.assertTrue(reference["same_principal_confirmed"])
        acknowledgement = reference["elevated_risk_acknowledgement"]
        self.assertEqual(
            acknowledgement["authority_version"],
            APPROVAL_AUTHORITY_VERSION,
        )
        self.assertEqual(
            acknowledgement["bound_plan_hash"], created["plan_hash"]
        )
        self.assertEqual(
            acknowledgement["policy_decision_hash"],
            reference["policy_decision_hash"],
        )
        self.assertEqual(
            acknowledgement["policy_class"], "elevated_admin"
        )
        self.assertEqual(acknowledgement["risk_delta"], "moderate")
        self.assertEqual(
            acknowledgement["physical_consequence"], "direct"
        )
        self.assertEqual(
            acknowledgement["state"],
            "consumed",
        )
        self.assertNotIn("admin-a", json.dumps(task.to_dict()))
        applied_plan = self.repository.get(created["plan_id"])
        assert (
            applied_plan is not None
            and applied_plan.policy_decision is not None
        )
        self.assertEqual(
            self.service.plan_hash(applied_plan), plan_hash_before_apply
        )
        self.assertEqual(
            applied_plan.policy_decision.policy_decision_hash,
            policy_hash_before_apply,
        )
        duplicate = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual(duplicate["status"], "already_applied")
        self.assertEqual(self.gateway.writes, 1)

    async def test_elevated_bundle_rehydrates_and_expires_without_dispatch(self):
        created = await self.create_elevated()
        first = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        second = await self.decide(created, first, principal=ADMIN_A)
        recovered = ChangeGovernanceService(
            self.repository,
            self.gateway,
            now=self.clock,
        )
        reviews = recovered.pending_external_reviews()
        self.assertEqual(len(reviews), 1)
        self.assertEqual(
            reviews[0]["approval_action"],
            "elevated_risk_acknowledgement",
        )
        self.clock.advance(minutes=61)
        self.assertEqual(recovered.pending_external_reviews(), [])
        plan = self.repository.get(created["plan_id"])
        assert plan is not None
        self.assertEqual(plan.approval.state, ApprovalState.EXPIRED)
        self.assertEqual(
            plan.approval.elevated_risk_acknowledgement.state,
            ApprovalState.EXPIRED,
        )
        self.assertEqual(self.gateway.writes, 0)
        self.assertIsNone(
            recovered.task_repository.get_for_plan(created["plan_id"])
        )
        self.assertEqual(second["approval_bundle_state"], "pending_elevated_risk_acknowledgement")

    async def test_elevated_rejection_requires_same_principal_and_is_terminal(self):
        created = await self.create_elevated()
        first = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        second = await self.decide(created, first, principal=ADMIN_A)
        with self.assertRaises(GovernanceError) as mismatch:
            await self.decide(
                created,
                second,
                principal=ADMIN_B,
                decision="reject",
            )
        self.assertEqual(
            mismatch.exception.code,
            ErrorCode.APPROVAL_PRINCIPAL_MISMATCH,
        )
        rejected = await self.decide(
            created,
            second,
            principal=ADMIN_A,
            decision="reject",
        )
        self.assertEqual(rejected["status"], "rejected")
        plan = self.repository.get(created["plan_id"])
        assert plan is not None
        self.assertEqual(plan.approval.state, ApprovalState.REJECTED)
        self.assertEqual(plan.approval.bundle_state, "rejected")
        self.assertEqual(
            plan.approval.elevated_risk_acknowledgement.state,
            ApprovalState.REJECTED,
        )
        self.assertIsNone(
            self.service.task_repository.get_for_plan(created["plan_id"])
        )
        self.assertEqual(self.gateway.writes, 0)

    async def test_superseding_plan_invalidates_both_elevated_actions(self):
        created = await self.create_elevated()
        first = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        await self.decide(created, first, principal=ADMIN_A)
        await self.create_standard()
        plan = self.repository.get(created["plan_id"])
        assert plan is not None
        self.assertEqual(plan.approval.state, ApprovalState.INVALIDATED)
        self.assertEqual(plan.approval.bundle_state, "invalidated")
        self.assertEqual(
            plan.approval.elevated_risk_acknowledgement.state,
            ApprovalState.INVALIDATED,
        )
        self.assertEqual(self.gateway.writes, 0)

    async def test_prohibited_plan_has_no_approval_task_provider_or_fallback(self):
        created = await self.create_prohibited()
        with self.assertRaises(GovernanceError) as approval_error:
            self.service.approve(
                created["plan_id"], created["plan_hash"]
            )
        self.assertEqual(
            approval_error.exception.code, ErrorCode.PROHIBITED_CHANGE
        )
        with self.assertRaises(GovernanceError) as apply_error:
            await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )
        self.assertEqual(
            apply_error.exception.code, ErrorCode.PROHIBITED_CHANGE
        )
        self.assertEqual(self.gateway.writes, 0)
        self.assertIsNone(
            self.service.task_repository.get_for_plan(created["plan_id"])
        )
        health = self.service.health_summary()
        self.assertEqual(health["prohibited_policy_decisions"], 1)

    async def test_task_persistence_failure_cannot_consume_approval(self):
        created = await self.create_standard()
        await self.fully_approve(created)
        with patch.object(
            self.service,
            "_save_task",
            side_effect=GovernanceError(
                ErrorCode.EXECUTION_TASK_STORAGE_ERROR
            ),
        ):
            with self.assertRaises(GovernanceError) as raised:
                await self.service.apply(
                    created["plan_id"], created["plan_hash"]
                )
        self.assertEqual(
            raised.exception.code,
            ErrorCode.EXECUTION_TASK_STORAGE_ERROR,
        )
        plan = self.repository.get(created["plan_id"])
        assert plan is not None
        self.assertEqual(plan.approval.state, ApprovalState.APPROVED)
        self.assertEqual(plan.approval.bundle_state, "fully_approved")
        self.assertEqual(self.gateway.writes, 0)
        self.assertIsNone(
            self.service.task_repository.get_for_plan(created["plan_id"])
        )

    async def test_consumption_projection_failure_keeps_one_durable_task(self):
        created = await self.create_standard()
        await self.fully_approve(created)
        original_save = self.service._save_task
        save_count = 0

        def fail_consumed_projection(task):
            nonlocal save_count
            save_count += 1
            if save_count == 3:
                raise GovernanceError(
                    ErrorCode.EXECUTION_TASK_STORAGE_ERROR
                )
            original_save(task)

        with patch.object(
            self.service,
            "_save_task",
            side_effect=fail_consumed_projection,
        ):
            with self.assertRaises(GovernanceError) as raised:
                await self.service.apply(
                    created["plan_id"], created["plan_hash"]
                )
        self.assertEqual(
            raised.exception.code,
            ErrorCode.EXECUTION_TASK_STORAGE_ERROR,
        )
        plan = self.repository.get(created["plan_id"])
        assert plan is not None
        self.assertEqual(plan.approval.state, ApprovalState.CONSUMED)
        task = self.service.task_repository.get_for_plan(
            created["plan_id"]
        )
        assert task is not None
        original_task_id = task.task_id
        self.assertEqual(
            task.state, ExecutionTaskState.FAILED_PRE_DISPATCH
        )
        self.assertEqual(self.gateway.writes, 0)

        with self.assertRaises(GovernanceError) as duplicate:
            await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )
        self.assertEqual(
            duplicate.exception.code,
            ErrorCode.DUPLICATE_APPLY_ATTEMPT,
        )
        reserved = self.service.task_repository.get_for_plan(
            created["plan_id"]
        )
        assert reserved is not None
        self.assertEqual(reserved.task_id, original_task_id)
        self.assertEqual(self.gateway.writes, 0)

    async def test_legacy_authority_v2_is_readable_but_not_actionable(self):
        created = await self.create_standard()
        plan = self.repository.get(created["plan_id"])
        assert plan is not None
        plan.policy_decision = None
        plan.approval = ChangeApproval(authority_version=2)
        self.repository.save(plan)
        readable = self.service.get_plan(created["plan_id"])
        self.assertIsNone(readable.get("policy_decision"))
        with self.assertRaises(GovernanceError) as approval_error:
            self.service.approve(
                created["plan_id"], self.service.plan_hash(plan)
            )
        self.assertEqual(
            approval_error.exception.code,
            ErrorCode.POLICY_SNAPSHOT_REQUIRED,
        )
        with self.assertRaises(GovernanceError) as apply_error:
            await self.service.apply(created["plan_id"])
        self.assertEqual(
            apply_error.exception.code,
            ErrorCode.POLICY_SNAPSHOT_REQUIRED,
        )
        self.assertEqual(self.gateway.writes, 0)
        self.assertIsNone(
            self.service.task_repository.get_for_plan(created["plan_id"])
        )

    async def test_startup_quarantines_invalid_f2_authority_without_replacement(self):
        created = await self.create_standard()
        await self.fully_approve(created)
        plan = self.repository.get(created["plan_id"])
        assert plan is not None
        task, reused = self.service._resolve_task_for_apply(
            plan, created["plan_hash"]
        )
        assert task is not None
        self.assertFalse(reused)
        plan.policy_decision = ChangePolicyDecision(
            **{
                **plan.policy_decision.__dict__,
                "policy_decision_hash": "f" * 64,
            }
        )
        self.repository.save(plan)

        recovered = ChangeGovernanceService(
            self.repository,
            self.gateway,
            now=self.clock,
        )
        result = await recovered.reconcile_execution_tasks(
            trigger="startup"
        )
        self.assertEqual(result["failed"], 1)
        rehydrated = recovered.task_repository.get_for_plan(
            created["plan_id"]
        )
        assert rehydrated is not None
        self.assertEqual(rehydrated.task_id, task.task_id)
        self.assertEqual(
            rehydrated.state, ExecutionTaskState.FAILED_PRE_DISPATCH
        )
        self.assertEqual(
            rehydrated.last_error["failure_category"],
            "immutable_plan_authority_invalid",
        )
        self.assertEqual(self.gateway.writes, 0)

    async def test_policy_snapshot_and_bundle_tampering_fail_closed(self):
        created = await self.create_standard()
        plan = self.repository.get(created["plan_id"])
        assert plan is not None and plan.policy_decision is not None
        original_policy = plan.policy_decision
        plan.policy_decision = ChangePolicyDecision(
            **{
                **plan.policy_decision.__dict__,
                "policy_decision_hash": "0" * 64,
            }
        )
        self.repository.save(plan)
        with self.assertRaises(GovernanceError) as policy_error:
            self.service.get_plan(created["plan_id"])
        self.assertEqual(
            policy_error.exception.code,
            ErrorCode.POLICY_SNAPSHOT_MISMATCH,
        )
        with self.assertRaises(GovernanceError) as list_error:
            self.service.list_plans()
        self.assertEqual(
            list_error.exception.code,
            ErrorCode.POLICY_SNAPSHOT_MISMATCH,
        )
        health = self.service.health_summary()
        self.assertEqual(health["policy_snapshot_mismatches"], 1)
        plan.policy_decision = original_policy
        self.repository.save(plan)

        created = await self.create_elevated()
        await self.fully_approve(created)
        plan = self.repository.get(created["plan_id"])
        assert plan is not None
        plan.approval.same_principal_confirmed = False
        self.repository.save(plan)
        with self.assertRaises(GovernanceError) as bundle_error:
            self.service.get_plan(created["plan_id"])
        self.assertEqual(
            bundle_error.exception.code,
            ErrorCode.APPROVAL_PRINCIPAL_MISMATCH,
        )

    async def test_health_counters_are_persisted_and_read_only(self):
        standard = await self.create_standard()
        await self.fully_approve(standard)
        await self.service.apply(
            standard["plan_id"], standard["plan_hash"]
        )
        elevated = await self.create_elevated()
        await self.fully_approve(elevated)
        await self.service.apply(
            elevated["plan_id"], elevated["plan_hash"]
        )
        await self.create_prohibited()
        first = self.service.health_summary()
        second = self.service.health_summary()
        persistent_fields = (
            "plans_by_policy_class",
            "pending_plan_approvals",
            "pending_elevated_acknowledgements",
            "granted_elevated_acknowledgements",
            "consumed_standard_approval_bundles",
            "consumed_elevated_approval_bundles",
            "prohibited_policy_decisions",
            "policy_snapshot_mismatches",
            "approval_principal_mismatches",
            "approval_sequence_failures",
        )
        self.assertEqual(
            {key: first[key] for key in persistent_fields},
            {key: second[key] for key in persistent_fields},
        )
        self.assertEqual(first["approval_authority_version"], 3)
        self.assertEqual(first["consumed_standard_approval_bundles"], 1)
        self.assertEqual(first["consumed_elevated_approval_bundles"], 1)
        self.assertEqual(first["granted_elevated_acknowledgements"], 1)
        self.assertEqual(
            first["plans_by_policy_class"],
            {
                "standard_admin": 1,
                "elevated_admin": 1,
                "prohibited": 1,
                "legacy_without_policy_snapshot": 0,
            },
        )


class F2IngressTests(F2ApprovalTestCase):
    async def _client(self, user_id=ADMIN_A.rsplit(":", 1)[-1]):
        import httpx

        app = create_approval_application(RuntimeShim(self.service))
        transport = httpx.ASGITransport(
            app=app, client=("172.30.32.2", 12345)
        )
        return httpx.AsyncClient(
            transport=transport,
            base_url="http://approval.local",
            headers={
                "X-Ingress-Path": "/api/hassio_ingress/f2fixture",
                "X-Remote-User-Id": user_id,
            },
        )

    @staticmethod
    def _hidden(body, name):
        match = re.search(
            rf'name="{re.escape(name)}" value="([^"]*)"', body
        )
        if match is None:
            raise AssertionError(f"missing hidden input: {name}")
        return match.group(1)

    def _form(self, body):
        return {
            name: self._hidden(body, name)
            for name in (
                "challenge_id",
                "plan_hash",
                "approval_kind",
                "approval_action",
                "csrf",
            )
        }

    async def test_ingress_requires_two_server_owned_actions_and_same_admin(self):
        created = await self.create_elevated()
        self.service.approve(created["plan_id"], created["plan_hash"])
        async with await self._client() as client:
            first = await client.get(f"/plans/{created['plan_id']}")
            self.assertEqual(first.status_code, 200)
            self.assertIn("Approve exact plan", first.text)
            self.assertIn("elevated_admin", first.text)
            self.assertIn("direct", first.text)
            granted = await client.post(
                f"/plans/{created['plan_id']}/approve",
                data=self._form(first.text),
            )
            self.assertEqual(granted.status_code, 200)

            second = await client.get(f"/plans/{created['plan_id']}")
            self.assertEqual(second.status_code, 200)
            self.assertIn("Acknowledge elevated risk", second.text)
            second_form = self._form(second.text)

        async with await self._client("admin-b") as other_admin:
            refused = await other_admin.post(
                f"/plans/{created['plan_id']}/approve",
                data=second_form,
            )
            self.assertEqual(refused.status_code, 409)
            self.assertIn("same administrator", refused.text)

        async with await self._client() as same_admin:
            refreshed = await same_admin.get(
                f"/plans/{created['plan_id']}"
            )
            accepted = await same_admin.post(
                f"/plans/{created['plan_id']}/approve",
                data=self._form(refreshed.text),
            )
            self.assertEqual(accepted.status_code, 200)
        plan = self.repository.get(created["plan_id"])
        assert plan is not None
        self.assertEqual(plan.approval.bundle_state, "fully_approved")
        self.assertTrue(plan.approval.same_principal_confirmed)

    async def test_ingress_refuses_non_supervisor_peer(self):
        created = await self.create_standard()
        self.service.approve(created["plan_id"], created["plan_hash"])
        import httpx

        app = create_approval_application(RuntimeShim(self.service))
        transport = httpx.ASGITransport(
            app=app, client=("127.0.0.1", 12345)
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://approval.local",
            headers={
                "X-Ingress-Path": "/api/hassio_ingress/f2fixture",
                "X-Remote-User-Id": "admin-a",
            },
        ) as client:
            response = await client.get(f"/plans/{created['plan_id']}")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
