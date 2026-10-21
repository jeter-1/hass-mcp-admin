"""HAMCP-213 D1: the separate per-object safety-critical acknowledgement.

Plan approval alone must never dispatch a plan whose f2-v3 policy requires the
safety-critical acknowledgement. The acknowledgement is an Ingress-only
decision that names every reviewed object of the immutable comparison; every
forged, partial, replayed or stale submission is refused without a write.
"""

from __future__ import annotations

import copy
import dataclasses
from datetime import timedelta
import json
from pathlib import Path
import re
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT))

from ha_mcp_engineering.approval_web import (  # noqa: E402
    _render_review,
    _render_safety_critical,
)
from ha_mcp_engineering.capabilities import BETA_NATIVE_CAPABILITIES  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.f3_runtime import runtime as f3_runtime  # noqa: E402
from ha_mcp_engineering.governance import service as service_module  # noqa: E402
from ha_mcp_engineering.governance.approval_notifications import (  # noqa: E402
    ApprovalNotificationManager,
)
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalActionKind,
    ApprovalActionRecord,
    ApprovalState,
    PlanStatus,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from tests.test_beta30_approval_notifications import (  # noqa: E402
    FakeRestClient,
)
from tests.test_beta33_risk_delta import (  # noqa: E402
    CURRENT_GARAGE_AUTOMATION,
    GARAGE_TARGET,
)
from tests.test_dev14_configuration_plans import (  # noqa: E402
    ConfigurationPlanTestCase,
)

PRINCIPAL = "home_assistant_admin_ingress:dev14-reviewer"
OTHER_PRINCIPAL = "home_assistant_admin_ingress:other-reviewer"
SAFETY = ApprovalActionKind.SAFETY_CRITICAL_ACKNOWLEDGEMENT.value


class SafetyCriticalAcknowledgementTests(ConfigurationPlanTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.gateway.configs[GARAGE_TARGET] = copy.deepcopy(
            CURRENT_GARAGE_AUTOMATION
        )

    async def create_changed_effect_plan(self):
        proposed = copy.deepcopy(CURRENT_GARAGE_AUTOMATION)
        proposed.pop("id")
        proposed["action"][0]["service"] = "cover.open_cover"
        created = await self.service.create_configuration_plan(
            title="Changed garage effect",
            description="Owner-authorized changed protected effect",
            operations=[
                {
                    "operation_id": "change_garage_effect",
                    "resource_type": "automation",
                    "action": "update",
                    "target_id": GARAGE_TARGET[1],
                    "depends_on": [],
                    "proposed_config": proposed,
                }
            ],
        )
        self.assertEqual(
            created["policy_decision"]["required_acknowledgements"],
            ["plan_approval", SAFETY],
        )
        return created

    async def approve_plan_only(self, created):
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        _review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        return await self.service.decide_external_approval(
            plan_id=created["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=created["plan_hash"],
            approval_kind=pending["approval_kind"],
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal=PRINCIPAL,
        )

    async def acknowledge(
        self,
        created,
        plan_only,
        *,
        tokens=None,
        projection_hash=None,
        principal=PRINCIPAL,
        decision="approve",
        csrf=None,
    ):
        review, issued = await self.service.issue_external_csrf(
            created["plan_id"], plan_only["challenge_id"]
        )
        safety = review["safety_critical_acknowledgement"]
        return await self.service.decide_external_approval(
            plan_id=created["plan_id"],
            challenge_id=plan_only["challenge_id"],
            expected_plan_hash=created["plan_hash"],
            approval_kind=plan_only["approval_kind"],
            approval_action=plan_only["approval_action"],
            csrf_nonce=issued if csrf is None else csrf,
            decision=decision,
            approver_principal=principal,
            acknowledged_objects=(
                tuple(row["object_ref"] for row in safety["objects"])
                if tokens is None
                else tuple(tokens)
            ),
            acknowledged_projection_hash=(
                safety["projection_hash"]
                if projection_hash is None
                else projection_hash
            ),
        )

    def writes(self):
        return [call for call in self.gateway.calls if call[0] == "write"]

    async def assert_apply_refused(self, created, code=None):
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(created["plan_id"], created["plan_hash"])
        if code is not None:
            self.assertEqual(raised.exception.code, code)
        self.assertEqual(self.writes(), [])
        self.assertIsNone(
            self.service.task_repository.get_for_plan(created["plan_id"])
        )

    async def test_plan_approval_alone_never_dispatches(self):
        created = await self.create_changed_effect_plan()
        plan_only = await self.approve_plan_only(created)
        self.assertEqual(plan_only["status"], "approval_pending")
        self.assertEqual(plan_only["approval_action"], SAFETY)
        stored = self.repository.get(created["plan_id"])
        self.assertEqual(
            stored.approval.bundle_state,
            "pending_safety_critical_acknowledgement",
        )
        acknowledgement = stored.approval.elevated_risk_acknowledgement
        self.assertIs(
            acknowledgement.kind,
            ApprovalActionKind.SAFETY_CRITICAL_ACKNOWLEDGEMENT,
        )
        self.assertEqual(acknowledgement.authority_version, 4)
        self.assertIsNone(acknowledgement.acknowledged_objects)
        self.assertEqual(
            self.service._approval_requirement_error(stored, "apply"),
            ErrorCode.SAFETY_CRITICAL_ACKNOWLEDGEMENT_REQUIRED,
        )
        await self.assert_apply_refused(created)

    async def test_dispatch_check_alone_refuses_a_forged_fully_approved_bundle(
        self,
    ):
        # Mutation guard: a record that claims every other dispatch condition
        # (approved status, fully_approved bundle) but lacks the granted
        # acknowledgement is refused only by the dispatch-time check.
        created = await self.create_changed_effect_plan()
        await self.approve_plan_only(created)
        plan = self.repository.get(created["plan_id"])
        plan.status = PlanStatus.APPROVED
        plan.approval.bundle_state = "fully_approved"
        with mock.patch.object(
            ChangeGovernanceService,
            "_approval_bundle_state",
            return_value="fully_approved",
        ):
            self.assertEqual(
                self.service._approval_requirement_error(plan, "apply"),
                ErrorCode.SAFETY_CRITICAL_ACKNOWLEDGEMENT_REQUIRED,
            )
            # Removing the dispatch check (the mutation) lets the forged
            # bundle through, so the assertion above fails under it.
            with mock.patch.object(
                ChangeGovernanceService,
                "_safety_critical_dispatch_error",
                return_value=None,
            ):
                self.assertIsNone(
                    self.service._approval_requirement_error(plan, "apply")
                )
        self.repository.save(plan)
        await self.assert_apply_refused(created)

    async def test_dispatch_check_refuses_object_and_principal_drift(self):
        created = await self.create_changed_effect_plan()
        plan_only = await self.approve_plan_only(created)
        granted = await self.acknowledge(created, plan_only)
        self.assertEqual(granted["status"], "approved")
        plan = self.repository.get(created["plan_id"])
        self.assertIsNone(self.service._approval_requirement_error(plan, "apply"))

        drifted = copy.deepcopy(plan)
        drifted.approval.elevated_risk_acknowledgement.acknowledged_objects = []
        self.assertEqual(
            self.service._safety_critical_dispatch_error(drifted),
            ErrorCode.SAFETY_CRITICAL_ACKNOWLEDGEMENT_MISMATCH,
        )
        drifted = copy.deepcopy(plan)
        drifted.approval.elevated_risk_acknowledgement.safety_critical_projection_hash = (
            "0" * 64
        )
        self.assertEqual(
            self.service._safety_critical_dispatch_error(drifted),
            ErrorCode.SAFETY_CRITICAL_ACKNOWLEDGEMENT_MISMATCH,
        )
        drifted = copy.deepcopy(plan)
        drifted.approval.elevated_risk_acknowledgement.approver_principal = (
            OTHER_PRINCIPAL
        )
        self.assertEqual(
            self.service._safety_critical_dispatch_error(drifted),
            ErrorCode.APPROVAL_PRINCIPAL_MISMATCH,
        )
        drifted = copy.deepcopy(plan)
        drifted.approval.elevated_risk_acknowledgement.authority_version = 3
        self.assertEqual(
            self.service._safety_critical_dispatch_error(drifted),
            ErrorCode.SAFETY_CRITICAL_ACKNOWLEDGEMENT_REQUIRED,
        )
        drifted = copy.deepcopy(plan)
        drifted.approval.elevated_risk_acknowledgement.kind = (
            ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT
        )
        self.assertEqual(
            self.service._safety_critical_dispatch_error(drifted),
            ErrorCode.SAFETY_CRITICAL_ACKNOWLEDGEMENT_REQUIRED,
        )
        self.assertEqual(self.writes(), [])

    async def test_exact_acknowledgement_is_owner_actionable(self):
        created = await self.create_changed_effect_plan()
        plan_only = await self.approve_plan_only(created)
        granted = await self.acknowledge(created, plan_only)
        self.assertEqual(granted["status"], "approved")
        plan = self.repository.get(created["plan_id"])
        acknowledgement = plan.approval.elevated_risk_acknowledgement
        self.assertEqual(acknowledgement.state, ApprovalState.APPROVED)
        self.assertEqual(acknowledgement.approver_principal, PRINCIPAL)
        self.assertEqual(
            acknowledgement.acknowledged_objects,
            service_module.safety_critical_binding(plan)["objects"],
        )
        self.assertEqual(plan.approval.bundle_state, "fully_approved")
        self.assertIsNone(self.service._approval_requirement_error(plan, "apply"))

    async def test_exact_acknowledgement_dispatches_once_and_verifies(self):
        created = await self.create_changed_effect_plan()
        plan_only = await self.approve_plan_only(created)
        granted = await self.acknowledge(created, plan_only)
        self.assertEqual(granted["status"], "approved")
        applied = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(
            self.gateway.configs[GARAGE_TARGET]["action"][0]["service"],
            "cover.open_cover",
        )
        task = self.service.task_repository.get_for_plan(created["plan_id"])
        reference = task.approval_reference["elevated_risk_acknowledgement"]
        self.assertEqual(reference["kind"], SAFETY)
        self.assertEqual(reference["acknowledged_object_count"], 1)
        self.assertEqual(
            reference["safety_critical_projection_hash"],
            service_module.safety_critical_binding(
                self.repository.get(created["plan_id"])
            )["projection_hash"],
        )
        # A repeated apply reuses the terminal task and never writes again.
        try:
            await self.service.apply(created["plan_id"], created["plan_hash"])
        except GovernanceError:
            pass
        self.assertEqual(len(self.writes()), 1)

    async def assert_ack_refused(self, created, plan_only, code, **kwargs):
        with self.assertRaises(GovernanceError) as raised:
            await self.acknowledge(created, plan_only, **kwargs)
        self.assertEqual(raised.exception.code, code)
        plan = self.repository.get(created["plan_id"])
        self.assertNotEqual(
            plan.approval.elevated_risk_acknowledgement.state,
            ApprovalState.APPROVED,
        )
        self.assertIsNone(
            plan.approval.elevated_risk_acknowledgement.acknowledged_objects
        )
        await self.assert_apply_refused(created)

    async def test_forged_object_sets_are_refused(self):
        created = await self.create_changed_effect_plan()
        plan_only = await self.approve_plan_only(created)
        review, _ = await self.service.issue_external_csrf(
            created["plan_id"], plan_only["challenge_id"]
        )
        tokens = [
            row["object_ref"]
            for row in review["safety_critical_acknowledgement"]["objects"]
        ]
        self.assertEqual(len(tokens), 1)
        cases = {
            "missing": [],
            "wrong": ["f" * 32],
            "duplicate": tokens + tokens,
            "extra": tokens + ["e" * 32],
        }
        for name, forged in cases.items():
            with self.subTest(name):
                await self.assert_ack_refused(
                    created,
                    plan_only,
                    ErrorCode.SAFETY_CRITICAL_ACKNOWLEDGEMENT_MISMATCH,
                    tokens=forged,
                )
        with self.subTest("tampered projection hash"):
            await self.assert_ack_refused(
                created,
                plan_only,
                ErrorCode.SAFETY_CRITICAL_ACKNOWLEDGEMENT_MISMATCH,
                projection_hash="0" * 64,
            )
        with self.subTest("non-ascii projection hash"):
            await self.assert_ack_refused(
                created,
                plan_only,
                ErrorCode.SAFETY_CRITICAL_ACKNOWLEDGEMENT_MISMATCH,
                projection_hash="é" * 8,
            )
        events = [
            json.loads(line)
            for line in self.audit_path.read_text().splitlines()
            if "safety_critical_acknowledgement_failed" in line
        ]
        self.assertTrue(events)
        # The correct submission still succeeds after the refused attempts.
        granted = await self.acknowledge(created, plan_only)
        self.assertEqual(granted["status"], "approved")

    async def test_objects_outside_safety_approve_are_refused(self):
        created = await self.create_changed_effect_plan()
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        _review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        with self.assertRaises(GovernanceError) as raised:
            await self.service.decide_external_approval(
                plan_id=created["plan_id"],
                challenge_id=pending["challenge_id"],
                expected_plan_hash=created["plan_hash"],
                approval_kind=pending["approval_kind"],
                approval_action=pending["approval_action"],
                csrf_nonce=csrf,
                decision="approve",
                approver_principal=PRINCIPAL,
                acknowledged_objects=("a" * 32,),
            )
        self.assertEqual(
            raised.exception.code, ErrorCode.EXTERNAL_APPROVAL_INVALID
        )
        self.assertNotEqual(
            self.repository.get(created["plan_id"]).approval.state,
            ApprovalState.APPROVED,
        )

        created = await self.create_changed_effect_plan()
        plan_only = await self.approve_plan_only(created)
        await self.assert_ack_refused(
            created,
            plan_only,
            ErrorCode.EXTERNAL_APPROVAL_INVALID,
            decision="reject",
        )

    async def test_different_principal_is_refused(self):
        created = await self.create_changed_effect_plan()
        plan_only = await self.approve_plan_only(created)
        await self.assert_ack_refused(
            created,
            plan_only,
            ErrorCode.APPROVAL_PRINCIPAL_MISMATCH,
            principal=OTHER_PRINCIPAL,
        )

    async def test_replayed_csrf_is_refused(self):
        created = await self.create_changed_effect_plan()
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        _review, first_csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        plan_only = await self.service.decide_external_approval(
            plan_id=created["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=created["plan_hash"],
            approval_kind=pending["approval_kind"],
            approval_action=pending["approval_action"],
            csrf_nonce=first_csrf,
            decision="approve",
            approver_principal=PRINCIPAL,
        )
        with self.assertRaises(GovernanceError):
            await self.acknowledge(created, plan_only, csrf=first_csrf)
        plan = self.repository.get(created["plan_id"])
        self.assertNotEqual(
            plan.approval.elevated_risk_acknowledgement.state,
            ApprovalState.APPROVED,
        )
        await self.assert_apply_refused(created)

    async def test_expired_acknowledgement_is_refused(self):
        created = await self.create_changed_effect_plan()
        plan_only = await self.approve_plan_only(created)
        review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], plan_only["challenge_id"]
        )
        safety = review["safety_critical_acknowledgement"]
        base = self.service.now()
        self.service.now = lambda: base + timedelta(days=30)
        with self.assertRaises(GovernanceError):
            await self.service.decide_external_approval(
                plan_id=created["plan_id"],
                challenge_id=plan_only["challenge_id"],
                expected_plan_hash=created["plan_hash"],
                approval_kind=plan_only["approval_kind"],
                approval_action=plan_only["approval_action"],
                csrf_nonce=csrf,
                decision="approve",
                approver_principal=PRINCIPAL,
                acknowledged_objects=tuple(
                    row["object_ref"] for row in safety["objects"]
                ),
                acknowledged_projection_hash=safety["projection_hash"],
            )
        plan = self.repository.get(created["plan_id"])
        self.assertIsNone(
            plan.approval.elevated_risk_acknowledgement.acknowledged_objects
        )
        with self.assertRaises(GovernanceError):
            await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(self.writes(), [])

    async def test_stale_current_state_is_refused_without_a_write(self):
        created = await self.create_changed_effect_plan()
        plan_only = await self.approve_plan_only(created)
        granted = await self.acknowledge(created, plan_only)
        self.assertEqual(granted["status"], "approved")
        drifted = copy.deepcopy(CURRENT_GARAGE_AUTOMATION)
        drifted["description"] = "Changed outside the plan"
        self.gateway.configs[GARAGE_TARGET] = drifted
        with self.assertRaises(GovernanceError):
            await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(self.writes(), [])

    async def test_acknowledgement_over_the_object_bound_is_refused(self):
        with mock.patch.object(service_module, "MAX_ACKNOWLEDGED_OBJECTS", 0):
            proposed = copy.deepcopy(CURRENT_GARAGE_AUTOMATION)
            proposed.pop("id")
            proposed["action"][0]["service"] = "cover.open_cover"
            with self.assertRaises(GovernanceError) as raised:
                await self.service.create_configuration_plan(
                    title="Unbindable acknowledgement",
                    description="More objects than one acknowledgement binds",
                    operations=[
                        {
                            "operation_id": "change_garage_effect",
                            "resource_type": "automation",
                            "action": "update",
                            "target_id": GARAGE_TARGET[1],
                            "depends_on": [],
                            "proposed_config": proposed,
                        }
                    ],
                )
        self.assertEqual(
            raised.exception.code, ErrorCode.POLICY_SNAPSHOT_MISMATCH
        )
        self.assertEqual(self.writes(), [])

    def test_no_tool_or_catalog_entry_grants_the_acknowledgement(self):
        catalog = json.dumps(BETA_NATIVE_CAPABILITIES, default=str)
        for field in ("acknowledged_objects", "ack_object", "ack_projection_hash"):
            self.assertNotIn(field, catalog)

    async def test_review_and_form_render_one_required_checkbox_per_object(
        self,
    ):
        created = await self.create_changed_effect_plan()
        plan_only = await self.approve_plan_only(created)
        review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], plan_only["challenge_id"]
        )
        self.assertEqual(review["second_action"], SAFETY)
        html = _render_review("/api/hassio_ingress/hamcp213", review, csrf)
        boxes = re.findall(
            r'<input type="checkbox" name="ack_object" value="([0-9a-f]{32})"'
            r' form="safety-critical-acknowledgement" required>',
            html,
        )
        self.assertEqual(
            boxes,
            [
                row["object_ref"]
                for row in review["safety_critical_acknowledgement"]["objects"]
            ],
        )
        self.assertIn('id="safety-critical-acknowledgement"', html)
        self.assertIn('name="ack_projection_hash"', html)

        _section, _fields, error = _render_safety_critical(
            {"available": True, "projection_hash": "x", "objects": [{"object_ref": "NOT-HEX"}]},
            required=True,
            decision_form=True,
        )
        self.assertTrue(error)
        _section, _fields, error = _render_safety_critical(
            {"available": False, "objects": []},
            required=True,
            decision_form=True,
        )
        self.assertTrue(error)

    async def test_safety_critical_notification_text(self):
        rest = FakeRestClient()

        async def addon_identity():
            return type("Identity", (), {"slug": "repository_hamcp213"})()

        notifications = ApprovalNotificationManager(
            rest,
            self.service.audit,
            service="notify.mobile_app_hamcp213_test",
            timeout_seconds=2,
            addon_identity_resolver=addon_identity,
        )
        self.service.approval_notifications = notifications
        created = await self.create_changed_effect_plan()
        await self.approve_plan_only(created)
        while not notifications.queue.empty():
            await notifications.process_next()
        titles = [body.get("title") for _method, _path, body in rest.calls]
        self.assertIn(
            "Home Assistant safety-critical acknowledgement required", titles
        )
        self.assertNotIn(created["plan_id"], json.dumps(rest.calls))
        self.assertEqual(self.writes(), [])


class BundleHashCompatibilityTests(unittest.TestCase):
    def test_v3_records_hash_without_the_new_fields(self):
        record = ApprovalActionRecord(
            kind=ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT,
            state=ApprovalState.APPROVED,
        )
        self.assertEqual(f3_runtime._safety_critical_bundle_fields(record), {})
        safety = dataclasses.replace(
            record,
            kind=ApprovalActionKind.SAFETY_CRITICAL_ACKNOWLEDGEMENT,
            safety_critical_projection_hash="a" * 64,
            acknowledged_objects=[{"operation_id": "op", "object_id": "obj"}],
        )
        self.assertEqual(
            f3_runtime._safety_critical_bundle_fields(safety),
            {
                "safety_critical_projection_hash": "a" * 64,
                "acknowledged_objects": [
                    {"operation_id": "op", "object_id": "obj"}
                ],
            },
        )


if __name__ == "__main__":
    unittest.main()
