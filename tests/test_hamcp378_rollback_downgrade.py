"""HAMCP-378 option B: a created-object rollback child names its form.

Release 2.11.0 Lane C GO, 2026-11-27.  Every created-object rollback child
(owned or modified since create, alone or after a restore) carries
``created_object_rollback_child`` in its policy decision.  Class, risk,
consequence and acknowledgement are unchanged.  A runtime that does not add
the marker recomputes a different decision for the same plan and refuses it
with ``policy_snapshot_mismatch``; the offline cross-version proof against
2.4.2-beta.7 is ``scripts/hamcp378_downgrade_harness.py``.

The "old form" below is this tree's policy with the marker stripped, which is
the 2.4.2-beta.8 to beta.14 decision for the same plan (``governance/`` and the
fixtures are unchanged between beta.14 and the 2.11.0 base).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance import (  # noqa: E402
    created_object_rollback as rollback_model,
    policy as policy_model,
)

from tests.hamcp329_298_fixtures import (  # noqa: E402
    CreatedObjectServiceTestCase,
    automation_config,
    script_config,
)

ROLLBACK_CHILD = rollback_model.ROLLBACK_CHILD
CURRENT_FORM = policy_model._created_object_rollback_policy


def old_form(operation, delete_policy):
    """The pre-378 child decision: this tree's decision without the marker."""

    result = CURRENT_FORM(operation, delete_policy)
    return replace(
        result,
        reason_codes=tuple(code for code in result.reason_codes if code != ROLLBACK_CHILD),
    )


def old_form_runtime():
    return mock.patch.object(policy_model, "_created_object_rollback_policy", old_form)


def create_operation(resource_type: str, target_id: str) -> dict:
    config = (
        automation_config(target_id)
        if resource_type == "automation"
        else script_config(target_id)
    )
    return {
        "operation_id": "create_1",
        "resource_type": resource_type,
        "action": "create",
        "target_id": target_id,
        "proposed_config": config,
        "depends_on": [],
    }


class RollbackFormTestCase(CreatedObjectServiceTestCase):
    async def created(self, kind: str) -> dict:
        if kind in {"automation", "script"}:
            return await self.applied(
                await self.configuration_plan(create_operation(kind, "porch_light"))
            )
        if kind == "scene":
            return await self.created_scene()
        return await self.created_helper(kind)

    def old_decision(self, plan_id: str):
        plan = self.service._load(plan_id)
        with old_form_runtime():
            return policy_model.evaluate_change_policy(plan)

    def stored_reasons(self, plan_id: str) -> list[str]:
        """The persisted decision, read without the snapshot check."""

        record = json.loads((self.root / "plans" / f"{plan_id}.json").read_text("utf-8"))
        return record["policy_decision"]["reason_codes"]

    def assertOnlyTheFormDiffers(self, decision, old) -> None:
        """Same authority; one added reason code, so a different decision hash."""

        self.assertEqual(set(decision.reason_codes) - set(old.reason_codes), {ROLLBACK_CHILD})
        self.assertEqual(set(old.reason_codes) - set(decision.reason_codes), set())
        for field in (
            "policy_version",
            "policy_class",
            "risk_delta",
            "physical_consequence",
            "required_acknowledgements",
            "policy_subject_hash",
        ):
            self.assertEqual(getattr(decision, field), getattr(old, field), field)
        self.assertNotEqual(decision.policy_decision_hash, old.policy_decision_hash)


class MarkerTests(RollbackFormTestCase):
    async def test_every_owned_rollback_child_names_its_form_and_nothing_else_changes(self):
        for kind in ("automation", "script", "input_boolean", "input_number", "scene"):
            with self.subTest(kind=kind):
                created = await self.created(kind)
                _rollback, plan = await self.rollback_plan(created, approve=False)
                (marker,) = self.markers(plan["plan_id"])
                self.assertEqual(marker["ownership"], rollback_model.OWNED)
                decision = self.policy(plan["plan_id"])
                self.assertIn(ROLLBACK_CHILD, decision.reason_codes)
                self.assertNotIn(
                    rollback_model.BASIS_MODIFIED_SINCE_CREATE, decision.reason_codes
                )
                self.assertOnlyTheFormDiffers(decision, self.old_decision(plan["plan_id"]))
                self.assertEqual(decision.policy_class.value, "standard_admin")

    async def test_a_modified_since_create_child_keeps_its_elevation_and_names_its_form(self):
        created = await self.created("automation")
        self.ws.entries["automation.porch_light"]["modified_at"] = 7.25
        _rollback, plan = await self.rollback_plan(created, approve=False)
        (marker,) = self.markers(plan["plan_id"])
        self.assertEqual(marker["ownership"], rollback_model.MODIFIED_SINCE_CREATE)
        decision = self.policy(plan["plan_id"])
        self.assertEqual(decision.policy_class.value, "elevated_admin")
        self.assertTrue(
            {ROLLBACK_CHILD, rollback_model.BASIS_MODIFIED_SINCE_CREATE}
            <= set(decision.reason_codes)
        )
        self.assertOnlyTheFormDiffers(decision, self.old_decision(plan["plan_id"]))

    async def test_a_restore_and_delete_rollback_names_its_form(self):
        self.add("automation", "existing")
        update = {
            "operation_id": "update_1",
            "resource_type": "automation",
            "action": "update",
            "target_id": "existing",
            "proposed_config": automation_config("existing", target="light.hall"),
            "depends_on": ["create_1"],
        }
        created = await self.applied(
            await self.configuration_plan(create_operation("automation", "porch_light"), update)
        )
        _rollback, plan = await self.rollback_plan(created, approve=False)
        actions = [item.action for item in sorted(self.operations(plan["plan_id"]), key=lambda item: item.order)]
        self.assertEqual(actions, ["update", "delete"])
        decision = self.policy(plan["plan_id"])
        self.assertIn(ROLLBACK_CHILD, decision.reason_codes)
        self.assertOnlyTheFormDiffers(decision, self.old_decision(plan["plan_id"]))

    async def test_an_ordinary_delete_plan_is_unchanged(self):
        """The marker is only on rollback children: a HAMCP-155 delete is as before."""

        for resource_type, target_id in (("automation", "hall"), ("script", "evening")):
            with self.subTest(resource_type=resource_type):
                self.add(resource_type, target_id)
                plan = await self.create_one(resource_type, target_id)
                decision = self.policy(plan["plan_id"])
                self.assertNotIn(ROLLBACK_CHILD, decision.reason_codes)
                self.assertEqual(decision, self.old_decision(plan["plan_id"]))

    async def test_an_incomplete_marker_is_still_prohibited_without_the_form(self):
        created = await self.created("automation")
        _rollback, plan = await self.rollback_plan(created, approve=False)
        (operation,) = self.operations(plan["plan_id"])
        (marker,) = rollback_model.markers(operation)
        marker["field"] = "another_operation"
        delete_policy = policy_model.OperationPolicyClassification(
            policy_model.ApprovalPolicyClass.STANDARD_ADMIN,
            policy_model.RiskDelta.MODERATE,
            policy_model.PhysicalConsequence.NONE,
            ("configuration_delete_standard_policy",),
        )
        result = policy_model._created_object_rollback_policy(operation, delete_policy)
        self.assertIs(result.policy_class, policy_model.ApprovalPolicyClass.PROHIBITED)
        self.assertEqual(result.reason_codes, (rollback_model.INCOMPLETE,))


class DowngradeTests(RollbackFormTestCase):
    async def test_a_runtime_without_the_form_refuses_a_marked_plan_and_writes_nothing(self):
        for kind in ("automation", "input_boolean", "scene"):
            with self.subTest(kind=kind):
                created = await self.created(kind)
                _rollback, plan = await self.rollback_plan(created, approve=True)
                mark = self.mark()
                with old_form_runtime():
                    for call in (
                        lambda: self.service.get_plan(plan["plan_id"]),
                        lambda: self.apply(plan),
                    ):
                        with self.assertRaises(GovernanceError) as raised:
                            result = call()
                            if hasattr(result, "__await__"):
                                await result
                        self.assertEqual(
                            raised.exception.code, ErrorCode.POLICY_SNAPSHOT_MISMATCH
                        )
                self.assertEqual(self.writes_since(mark), [])
                self.assertIsNone(
                    self.service.task_repository.get_for_plan(plan["plan_id"])
                )

    async def test_an_old_form_plan_is_refused_after_upgrade_and_replanned_after_expiry(self):
        created = await self.created("automation")
        with old_form_runtime():
            _rollback, old = await self.rollback_plan(created, approve=True)
        self.assertNotIn(ROLLBACK_CHILD, self.stored_reasons(old["plan_id"]))
        mark = self.mark()
        with self.assertRaises(GovernanceError) as raised:
            await self.apply(old)
        self.assertEqual(raised.exception.code, ErrorCode.POLICY_SNAPSHOT_MISMATCH)
        # While the old plan is live it is the answer, and it cannot run.
        again = await self.service.rollback_change(created["plan_id"])
        self.assertEqual(again["rollback_plan_id"], old["plan_id"])
        self.assertEqual(again["rollback_plan_resolution"], "existing_live_plan_returned")
        self.assertEqual(self.writes_since(mark), [])
        self.assertIn("porch_light", self.config_rest.configs["automation"])
        # Once it expires, the request plans afresh in the new form.
        self.clock[0] += timedelta(minutes=121)
        fresh = await self.service.rollback_change(created["plan_id"])
        self.assertEqual(fresh["rollback_plan_resolution"], "fresh_plan_created")
        self.assertEqual(fresh["replaced_rollback_plan_id"], old["plan_id"])
        self.assertNotEqual(fresh["rollback_plan_id"], old["plan_id"])
        self.assertIn(ROLLBACK_CHILD, self.policy(fresh["rollback_plan_id"]).reason_codes)
        self.assertEqual(self.writes_since(mark), [])
        result = await self.apply_rollback(fresh)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            self.writes_since(mark), [("DELETE", "/config/automation/config/porch_light")]
        )


if __name__ == "__main__":
    unittest.main()
