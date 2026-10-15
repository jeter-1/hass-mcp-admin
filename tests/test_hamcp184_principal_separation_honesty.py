"""HAMCP-184 F-02: principal separation is reported truthfully.

The Ingress approval route falls back to the anonymous principal
``home_assistant_admin_ingress`` when Home Assistant supplies no
``X-Remote-User-Id``. That records that an administrator decided through the
separate Ingress channel, but not which administrator, so principal separation
is not proven. Before HAMCP-184 every Ingress decision stored and displayed
``principal_separation_enforced: true``.

This change is reporting only (owner decision): who may approve, the approval
flow, standard-class eligibility of the anonymous fallback, and elevated-class
behavior are unchanged.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalPolicyClass,
    ApprovalState,
    PlanStatus,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    DEFAULT_APPROVER_PRINCIPAL,
    ChangeGovernanceService,
    principal_separation_proven,
    principal_separation_status,
)
from tests.test_beta25_external_approval import (  # noqa: E402
    ExternalApprovalTestCase,
)
from tests.test_f2_policy_approval import F2ApprovalTestCase  # noqa: E402

ANONYMOUS = DEFAULT_APPROVER_PRINCIPAL
IDENTIFIED = "home_assistant_admin_ingress:hamcp184-admin"


class PrincipalSeparationHonestyTests(F2ApprovalTestCase):
    def _surfaces(self, plan_id):
        """Return (persisted flag, projected flag, projected status)."""

        record = self.repository.get(plan_id)
        public = self.service.get_plan(plan_id)["approval"]
        return (
            record.approval.principal_separation_enforced,
            public["principal_separation_enforced"],
            public["principal_separation_status"],
        )

    async def test_anonymous_fallback_standard_approval_is_not_enforced_and_stays_eligible(self):
        created = await self.create_standard()
        granted = await self.fully_approve(created, principal=ANONYMOUS)
        self.assertEqual(granted["status"], "approved")
        persisted, projected, status = self._surfaces(created["plan_id"])
        self.assertIs(persisted, False)
        self.assertIs(projected, False)
        self.assertEqual(
            status,
            {"evaluated": True, "enforced": False, "reason": "anonymous_fallback_principal"},
        )
        record = self.repository.get(created["plan_id"])
        self.assertEqual(record.approval.approver_principal, ANONYMOUS)
        self.assertEqual(record.approval.state, ApprovalState.APPROVED)
        # Eligibility unchanged: the standard-class fallback approval applies.
        applied = await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(self.gateway.writes, 1)
        # The terminal record keeps reporting the same truth.
        persisted, projected, status = self._surfaces(created["plan_id"])
        self.assertEqual((persisted, projected, status["reason"]), (False, False, "anonymous_fallback_principal"))
        listed = [
            item for item in self.service.list_plans(limit=50)["plans"]
            if item["plan_id"] == created["plan_id"]
        ]
        for item in listed:
            if "approval" in item:
                self.assertIsNot(item["approval"].get("principal_separation_enforced"), True)

    async def test_identified_principal_remains_enforced(self):
        created = await self.create_standard()
        await self.fully_approve(created, principal=IDENTIFIED)
        persisted, projected, status = self._surfaces(created["plan_id"])
        self.assertIs(persisted, True)
        self.assertIs(projected, True)
        self.assertEqual(
            status,
            {"evaluated": True, "enforced": True, "reason": "external_administrator_distinct"},
        )
        applied = await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(applied["status"], "applied")

    async def test_identified_elevated_approval_unchanged(self):
        created = await self.create_elevated()
        self.assertEqual(created["policy_decision"]["policy_class"], "elevated_admin")
        granted = await self.fully_approve(created, principal=IDENTIFIED)
        self.assertEqual(granted["status"], "approved")
        persisted, projected, status = self._surfaces(created["plan_id"])
        self.assertEqual((persisted, projected, status["reason"]), (True, True, "external_administrator_distinct"))
        applied = await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(applied["status"], "applied")

    async def test_elevated_still_refuses_anonymous_fallback(self):
        created = await self.create_elevated()
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        with self.assertRaises(GovernanceError) as raised:
            await self.decide(created, pending, principal=ANONYMOUS)
        self.assertEqual(raised.exception.code, ErrorCode.APPROVAL_PRINCIPAL_MISMATCH)
        record = self.repository.get(created["plan_id"])
        self.assertNotEqual(record.approval.state, ApprovalState.APPROVED)
        self.assertIsNone(record.approval.principal_separation_enforced)
        self.assertEqual(self.gateway.writes, 0)

    async def test_anonymous_rejection_is_not_enforced(self):
        created = await self.create_standard()
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        result = await self.decide(created, pending, principal=ANONYMOUS, decision="reject")
        self.assertEqual(result["status"], "rejected")
        record = self.repository.get(created["plan_id"])
        self.assertEqual(record.status, PlanStatus.REJECTED)
        self.assertIs(record.approval.principal_separation_enforced, False)
        status = principal_separation_status(record.approval)
        self.assertEqual((status["enforced"], status["reason"]), (False, "anonymous_fallback_principal"))

    async def test_identified_rejection_is_enforced(self):
        created = await self.create_standard()
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        await self.decide(created, pending, principal=IDENTIFIED, decision="reject")
        record = self.repository.get(created["plan_id"])
        self.assertIs(record.approval.principal_separation_enforced, True)

    async def test_pre_hamcp184_true_stored_fallback_record_projects_truthfully_and_stays_eligible(self):
        created = await self.create_standard()
        await self.fully_approve(created, principal=ANONYMOUS)
        plan = self.repository.get(created["plan_id"])
        # Exactly what a pre-HAMCP-184 writer persisted for this decision.
        plan.approval.principal_separation_enforced = True
        self.repository.save(plan)
        persisted, projected, status = self._surfaces(created["plan_id"])
        self.assertIs(persisted, True)  # never rewritten
        self.assertIs(projected, False)
        self.assertEqual(status["reason"], "anonymous_fallback_principal")
        applied = await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(applied["status"], "applied")
        self.assertIs(
            self.repository.get(created["plan_id"]).approval.principal_separation_enforced,
            True,
        )

    async def test_forged_false_with_identified_principal_is_not_eligible(self):
        # The eligibility rule accepts ``False`` only for the anonymous
        # fallback principal on a standard-class plan, exactly the decisions
        # that were eligible before HAMCP-184.
        created = await self.create_standard()
        await self.fully_approve(created, principal=IDENTIFIED)
        plan = self.repository.get(created["plan_id"])
        plan.approval.principal_separation_enforced = False
        self.repository.save(plan)
        with self.assertRaises(GovernanceError):
            await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(self.gateway.writes, 0)

    async def test_eligibility_rule_matrix(self):
        created = await self.create_standard()
        await self.fully_approve(created, principal=ANONYMOUS)
        plan = self.repository.get(created["plan_id"])
        valid = ChangeGovernanceService._external_principal_authority_valid
        elevated = replace(plan.policy_decision, policy_class=ApprovalPolicyClass.ELEVATED_ADMIN)
        cases = (
            (True, IDENTIFIED, plan.policy_decision, True),
            (True, ANONYMOUS, plan.policy_decision, True),  # pre-HAMCP-184 record
            (False, ANONYMOUS, plan.policy_decision, True),
            (False, IDENTIFIED, plan.policy_decision, False),
            (False, ANONYMOUS, elevated, False),
            (None, ANONYMOUS, plan.policy_decision, False),
            (None, IDENTIFIED, plan.policy_decision, False),
        )
        for flag, principal, decision, expected in cases:
            with self.subTest(flag=flag, principal=principal, policy=decision.policy_class.value):
                plan.approval.principal_separation_enforced = flag
                plan.approval.approver_principal = principal
                plan.policy_decision = decision
                self.assertIs(valid(plan), expected)

    def test_proven_helper(self):
        self.assertFalse(principal_separation_proven(ANONYMOUS))
        self.assertTrue(principal_separation_proven(IDENTIFIED))


class PrincipalSeparationAuditTests(ExternalApprovalTestCase):
    async def test_audit_never_claims_separation_for_anonymous_fallback(self):
        created = await self.create()
        _, granted = await self.grant(created, principal=ANONYMOUS)
        self.assertEqual(granted["status"], "approved")
        await self.service.apply(created["plan_id"], created["plan_hash"])
        records = [json.loads(line) for line in self.audit_path.read_text().splitlines()]
        self.assertTrue(records)
        for record in records:
            # Audit carries presence of a principal only, never a separation
            # claim, so it cannot contradict the plan projection.
            self.assertNotIn("principal_separation_enforced", json.dumps(record))
        granted_events = [r for r in records if r.get("event") == "external_approval_granted"]
        self.assertTrue(granted_events)
        self.assertTrue(all(r.get("approval_principal_present") is True for r in granted_events))
        task = self.service.task_repository.get_for_plan(created["plan_id"])
        self.assertNotIn("principal_separation_enforced", json.dumps(task.to_dict() if hasattr(task, "to_dict") else str(task)))


if __name__ == "__main__":
    unittest.main()
