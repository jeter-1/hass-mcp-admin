"""HAMCP-198: bounded, fixed-vocabulary identification of projection failures.

Health names at most eight failing plans as ``{plan_id, category}`` so the
operator can retrieve and classify them. The category names the first failing
check; it never carries record contents, and diagnosis never changes a
projection outcome or grants authority. Synthetic offline plans only.
"""
from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "hass_mcp_engineering_beta")]

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance import (  # noqa: E402
    projection_diagnostics as diagnostics,
)
from ha_mcp_engineering.governance.historical_policy import (  # noqa: E402
    _decision_payload,
)
from ha_mcp_engineering.governance.models import ChangePlan  # noqa: E402
from ha_mcp_engineering.governance.normalize import stable_hash  # noqa: E402
from ha_mcp_engineering.governance.runtime import (  # noqa: E402
    GovernanceRuntime,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from tests.test_governance import GovernanceTestCase  # noqa: E402

CONSUMED_FIXTURE = (
    ROOT / "tests/fixtures/beta33_initial_retained_effect_consumed_plan.json"
)
VOCABULARY = frozenset(diagnostics.PROJECTION_FAILURE_CATEGORIES)
PLAN_ID = re.compile(r"[0-9a-f]{32}")
WARNING = "one_or_more_governance_plans_could_not_be_projected"


def rebound(plan: ChangePlan, **changes) -> ChangePlan:
    """Change decision fields and recompute its hash and approval binding.

    The result keeps intact stored-hash integrity, so only the policy
    comparison (or a later binding) can fail.
    """

    decision = replace(plan.policy_decision, **changes)
    decision = replace(
        decision, policy_decision_hash=stable_hash(_decision_payload(decision))
    )
    plan = copy.deepcopy(plan)
    plan.policy_decision = decision
    plan.approval.policy_decision_hash = decision.policy_decision_hash
    plan.approval.policy_class = decision.policy_class.value
    return plan


def drifted(plan: ChangePlan, **changes) -> ChangePlan:
    codes = tuple(
        sorted(plan.policy_decision.reason_codes + ("synthetic_drift_reason",))
    )
    return rebound(plan, reason_codes=codes, **changes)


class Base(GovernanceTestCase):
    async def plans(self, count):
        return [(await self.update_plan())["plan_id"] for _ in range(count)]

    def stored(self, plan_id):
        return self.repository.get(plan_id)

    def write(self, plan: ChangePlan):
        path = self.repository._path(plan.plan_id)
        path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")

    def corrupt_subject(self, plan_id):
        path = self.repository._path(plan_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["policy_decision"]["policy_subject_hash"] = "0" * 64
        path.write_text(json.dumps(data), encoding="utf-8")

    def health(self):
        service = ChangeGovernanceService(
            ChangePlanRepository(self.repository.root),
            self.gateway,
            now=self.clock,
        )
        return service, service.health_summary()


class CategoryTests(Base):
    """Each category is reachable and names the first failing check."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.plan = self.stored((await self.plans(1))[0])

    def category(self, plan, code=ErrorCode.POLICY_SNAPSHOT_MISMATCH):
        result = diagnostics.projection_failure_category(plan, code)
        self.assertIn(result, VOCABULARY)
        return result

    def test_subject_hash_not_reproducible(self):
        plan = copy.deepcopy(self.plan)
        plan.policy_decision = replace(
            plan.policy_decision, policy_subject_hash="0" * 64
        )
        self.assertEqual(
            self.category(plan), "policy_subject_hash_not_reproducible"
        )

    def test_decision_hash_not_reproducible(self):
        plan = copy.deepcopy(self.plan)
        plan.policy_decision = replace(
            plan.policy_decision, policy_decision_hash="1" * 64
        )
        self.assertEqual(
            self.category(plan), "policy_decision_hash_not_reproducible"
        )

    def test_approval_policy_binding_mismatch(self):
        for field, value in (
            ("policy_decision_hash", "2" * 64),
            ("policy_class", "prohibited"),
        ):
            with self.subTest(field=field):
                plan = copy.deepcopy(self.plan)
                setattr(plan.approval, field, value)
                self.assertEqual(
                    self.category(plan), "approval_policy_binding_mismatch"
                )

    def test_current_policy_decision_drift(self):
        self.assertEqual(
            self.category(drifted(self.plan)), "current_policy_decision_drift"
        )

    def test_superseded_policy_decision_drift(self):
        for version in ("f2-v1", "f2-v2"):
            with self.subTest(version=version):
                plan = drifted(self.plan, policy_version=version)
                self.assertEqual(
                    self.category(plan), "superseded_policy_decision_drift"
                )

    def test_snapshot_that_reproduces_is_a_binding_mismatch(self):
        # Current snapshot intact: the mismatch came from a later binding.
        self.assertEqual(
            self.category(self.plan), "policy_snapshot_binding_mismatch"
        )

    def test_approval_codes_map_to_their_own_categories(self):
        for code, expected in (
            (
                ErrorCode.APPROVAL_AUTHORITY_MISMATCH,
                "approval_authority_version_mismatch",
            ),
            (
                ErrorCode.APPROVAL_PRINCIPAL_MISMATCH,
                "approval_principal_mismatch",
            ),
            (
                ErrorCode.APPROVAL_SEQUENCE_FAILURE,
                "approval_sequence_inconsistent",
            ),
        ):
            with self.subTest(code=code):
                self.assertEqual(self.category(self.plan, code), expected)

    def test_snapshot_checks_precede_approval_codes(self):
        plan = copy.deepcopy(self.plan)
        plan.policy_decision = replace(
            plan.policy_decision, policy_subject_hash="0" * 64
        )
        self.assertEqual(
            self.category(plan, ErrorCode.APPROVAL_SEQUENCE_FAILURE),
            "policy_subject_hash_not_reproducible",
        )

    def test_historical_profile_rejected_only_by_approval(self):
        value = json.loads(CONSUMED_FIXTURE.read_text(encoding="utf-8"))
        value["approval"]["bound_plan_hash"] = "e" * 64
        plan = ChangePlan.from_dict(value)
        for code in (
            ErrorCode.POLICY_SNAPSHOT_MISMATCH,
            ErrorCode.APPROVAL_SEQUENCE_FAILURE,
        ):
            with self.subTest(code=code):
                self.assertEqual(
                    self.category(plan, code),
                    "historical_profile_approval_mismatch",
                )
        # The intact fixture is not this category.
        intact = ChangePlan.from_dict(
            json.loads(CONSUMED_FIXTURE.read_text(encoding="utf-8"))
        )
        self.assertNotEqual(
            self.category(intact), "historical_profile_approval_mismatch"
        )

    def test_unexaminable_records_are_category_unavailable(self):
        missing = copy.deepcopy(self.plan)
        missing.policy_decision = None
        self.assertEqual(self.category(missing), "category_unavailable")
        self.assertEqual(
            self.category(self.plan, ErrorCode.CONFIGURATION_VALIDATION_FAILED),
            "category_unavailable",
        )
        with patch.object(
            diagnostics,
            "persisted_policy_snapshot_integrity_matches",
            side_effect=RuntimeError("synthetic-secret-detail"),
        ):
            self.assertEqual(self.category(self.plan), "category_unavailable")

    def test_vocabulary_is_fixed_and_bounded(self):
        self.assertEqual(len(VOCABULARY), len(diagnostics.PROJECTION_FAILURE_CATEGORIES))
        for category in VOCABULARY:
            self.assertRegex(category, r"^[a-z_]{1,64}$")
        self.assertEqual(diagnostics.MAX_HEALTH_PROJECTION_FAILURES, 8)
        self.assertEqual(
            diagnostics.PROJECTION_FAILURE_CATEGORY_MODEL,
            "hamcp198-projection-failure-category-v1",
        )


class HealthIdentificationTests(Base):
    def assert_rows(self, health, expected_ids, category):
        rows = health["projection_failures"]
        self.assertEqual([row["plan_id"] for row in rows], expected_ids)
        for row in rows:
            self.assertEqual(set(row), {"plan_id", "category"})
            self.assertRegex(row["plan_id"], PLAN_ID)
            self.assertEqual(row["category"], category)

    async def test_no_failures_reports_empty_bounded_fields(self):
        await self.plans(2)
        _, health = self.health()
        self.assertEqual(health["projection_failure_count"], 0)
        self.assertIsNone(health["projection_failure_warning"])
        self.assertEqual(health["projection_failures"], [])
        self.assertFalse(health["projection_failures_truncated"])
        self.assertEqual(
            health["projection_failure_category_model"],
            diagnostics.PROJECTION_FAILURE_CATEGORY_MODEL,
        )

    async def test_new_failure_raises_the_warning_and_is_identified(self):
        # Positive control: a new real instance still lights the warning, and
        # identification is additive (count and warning are unchanged).
        ids = await self.plans(3)
        broken = sorted(ids[:2])
        before = {i: self.repository._path(i).read_bytes() for i in ids}
        for plan_id in broken:
            self.corrupt_subject(plan_id)
        service, health = self.health()
        self.assertEqual(health["projection_failure_count"], 2)
        self.assertEqual(health["projection_failure_warning"], WARNING)
        self.assertEqual(health["plans_by_policy_class"]["projection_failed"], 2)
        self.assertFalse(health["projection_failures_truncated"])
        self.assert_rows(health, broken, "policy_subject_hash_not_reproducible")
        # Reads still fail closed; nothing was rewritten or dispatched.
        with self.assertRaises(GovernanceError) as caught:
            service.get_plan(broken[0])
        self.assertEqual(caught.exception.code, ErrorCode.POLICY_SNAPSHOT_MISMATCH)
        self.assertEqual(self.repository._path(ids[2]).read_bytes(), before[ids[2]])
        self.assertEqual(self.gateway.write_calls, 0)

    async def test_current_policy_drift_is_identified_end_to_end(self):
        ids = await self.plans(2)
        self.write(drifted(self.stored(ids[0])))
        _, health = self.health()
        self.assertEqual(health["projection_failure_count"], 1)
        self.assertEqual(health["projection_failure_warning"], WARNING)
        self.assert_rows(health, [ids[0]], "current_policy_decision_drift")

    async def test_more_than_eight_failures_are_truncated_and_sorted(self):
        ids = await self.plans(11)
        broken = ids[:10]
        for plan_id in broken:
            self.corrupt_subject(plan_id)
        _, health = self.health()
        self.assertEqual(health["projection_failure_count"], 10)
        self.assertEqual(health["projection_failure_warning"], WARNING)
        self.assertTrue(health["projection_failures_truncated"])
        self.assert_rows(
            health, sorted(broken)[:8], "policy_subject_hash_not_reproducible"
        )

    async def test_exactly_eight_failures_are_not_truncated(self):
        ids = await self.plans(8)
        for plan_id in ids:
            self.corrupt_subject(plan_id)
        _, health = self.health()
        self.assertEqual(len(health["projection_failures"]), 8)
        self.assertFalse(health["projection_failures_truncated"])

    async def test_identification_carries_no_record_contents(self):
        sentinel = "synthetic-record-content-sentinel"
        plan_id = (await self.update_plan(
            {"alias": sentinel, "description": sentinel,
             "triggers": [], "conditions": [], "actions": []}
        ))["plan_id"]
        self.corrupt_subject(plan_id)
        _, health = self.health()
        self.assertEqual(health["projection_failure_count"], 1)
        rows = json.dumps(health["projection_failures"])
        self.assertNotIn(sentinel, rows)
        self.assertNotIn("0" * 64, rows)
        self.assertEqual(json.loads(rows), [
            {"plan_id": plan_id, "category": "policy_subject_hash_not_reproducible"}
        ])

    async def test_only_listed_records_are_examined(self):
        ids = await self.plans(10)
        for plan_id in ids:
            self.corrupt_subject(plan_id)
        with patch.object(
            diagnostics, "projection_failure_category",
            wraps=diagnostics.projection_failure_category,
        ) as spy:
            self.health()
        self.assertEqual(spy.call_count, 8)

    def test_unconfigured_default_shape_has_the_fields(self):
        runtime = GovernanceRuntime()
        runtime.storage_error = "synthetic_storage_error"
        health = runtime.health_summary()
        self.assertEqual(health["projection_failure_count"], 0)
        self.assertEqual(health["projection_failures"], [])
        self.assertFalse(health["projection_failures_truncated"])
        self.assertEqual(
            health["projection_failure_category_model"],
            diagnostics.PROJECTION_FAILURE_CATEGORY_MODEL,
        )

if __name__ == "__main__":
    unittest.main()
