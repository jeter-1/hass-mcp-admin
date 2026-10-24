"""HAMCP-155 governed deletion: protected-effect policy and planner refusals.

A delete is classified by the effects and dependents it REMOVES.  Removing
a protected effect is elevated with the HAMCP-213 acknowledgement and an
explicit consequence statement; an unlock or disarm is held at the floor;
incomplete consumer evidence is treated as protected.  Policy recomputes the
basis from the immutable plan, so a tampered marker, basis, hash, mixed plan
or oversize plan is prohibited.  The planner refuses every malformed or
unreviewable request before anything is stored.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import GovernanceError  # noqa: E402
from ha_mcp_engineering.governance import (  # noqa: E402
    configuration_delete_planning as planning,
)
from ha_mcp_engineering.f3_configuration.adapter import (  # noqa: E402
    ConfigurationOperationAdapter,
)
from ha_mcp_engineering.f3_configuration.delete import (  # noqa: E402
    ConfigurationDeleteAdapter,
)
from ha_mcp_engineering.governance.models import RiskLevel  # noqa: E402
from ha_mcp_engineering.governance.physical_effects import (  # noqa: E402
    NONE_COMPARISON,
    compare_physical_effects,
)
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    normalize_resource_config,
)
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    ApprovalPolicyClass,
    CONFIGURATION_DELETE_INCOMPLETE,
    configuration_operation_policy,
    evaluate_change_policy,
)

from tests.hamcp069b_fixtures import exposure_snapshot  # noqa: E402
from tests.hamcp155_fixtures import (  # noqa: E402
    ConfigurationDeleteServiceTestCase,
    automation_config,
    script_config,
)


def disclosure(created: dict, index: int = 0) -> dict:
    return created["dry_run_results"]["operations"][index]["dry_run_results"][
        "configuration_delete"
    ]


class ProtectedEffectTests(ConfigurationDeleteServiceTestCase):
    async def test_removing_a_lock_action_is_elevated_with_acknowledgement(self):
        self.add(
            "automation",
            "night_lock",
            automation_config("night_lock", service="lock.lock", target="lock.front_door"),
        )
        created = await self.create_one("automation", "night_lock")
        self.assertEqual(created["policy_decision"]["policy_class"], "elevated_admin")
        details = disclosure(created)
        self.assertTrue(details["protection"]["protected"])
        self.assertIn(planning.BASIS_PROTECTED_EFFECT, details["protection"]["basis"])
        self.assertEqual(
            [row["object_id"] for row in details["removed_effects"]], ["lock.front_door"]
        )
        self.assertTrue(
            any(
                text.startswith(
                    "Deleting automation night_lock removes these protected effects: "
                    "lock.front_door (lock.lock)"
                )
                for text in details["consequence_statements"]
            ),
            details["consequence_statements"],
        )
        # The approval asks for the per-object safety-critical acknowledgement.
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        review = pending
        for _ in range(2):
            review, csrf = await self.service.issue_external_csrf(
                created["plan_id"], review["challenge_id"]
            )
            if review.get("safety_critical_acknowledgement"):
                break
            review = await self.service.decide_external_approval(
                plan_id=created["plan_id"],
                challenge_id=review["challenge_id"],
                expected_plan_hash=created["plan_hash"],
                approval_kind=review["approval_kind"],
                approval_action=review["approval_action"],
                csrf_nonce=csrf,
                decision="approve",
                approver_principal="home_assistant_admin_ingress:hamcp155-reviewer",
            )
        objects = review["safety_critical_acknowledgement"]["objects"]
        self.assertTrue(any("lock.front_door" in str(row) for row in objects), objects)

    async def test_an_unlock_is_held_at_the_floor(self):
        self.add(
            "script",
            "open_door",
            script_config("open_door", service="lock.unlock", target="lock.front_door"),
        )
        created = await self.create_one("script", "open_door")
        self.assertEqual(created["policy_decision"]["policy_class"], "elevated_admin")
        protection = disclosure(created)["protection"]
        self.assertTrue(protection["protected"])
        self.assertTrue(protection["unlock_or_disarm"])
        self.assertTrue(
            any(
                "contains, or may contain, an unlock or disarm action" in text
                for text in disclosure(created)["consequence_statements"]
            )
        )

    async def test_an_ordinary_delete_is_standard_but_still_destructive(self):
        self.add("automation", "porch_light")
        created = await self.create_one("automation", "porch_light")
        self.assertEqual(created["policy_decision"]["policy_class"], "standard_admin")
        plan = self.repository.get(created["plan_id"])
        (operation,) = plan.operations
        # A delete is never LOW: it removes a resource even with no effects.
        self.assertIs(operation.risk.level, RiskLevel.MEDIUM)
        self.assertEqual(disclosure(created)["protection"]["basis"], [])
        self.assertIn(
            "No indexed automation or script consumer was found.",
            disclosure(created)["consequence_statements"],
        )

    async def test_incomplete_consumer_evidence_is_treated_as_protected(self):
        self.add("automation", "porch_light")
        self.snapshot = exposure_snapshot()
        self.snapshot.dynamic_reference_overflow_count = 1
        created = await self.create_one("automation", "porch_light")
        self.assertEqual(created["policy_decision"]["policy_class"], "elevated_admin")
        details = disclosure(created)
        self.assertFalse(details["consumers"]["evidence_complete"])
        self.assertIn(planning.BASIS_PROTECTED_DEPENDENCY, details["protection"]["basis"])
        self.assertTrue(
            any("treated as protected" in text for text in details["consequence_statements"])
        )


class PolicyBoundaryTests(ConfigurationDeleteServiceTestCase):
    async def planned(self, resource_type: str, target_id: str, config=None):
        self.add(resource_type, target_id, config)
        created = await self.create_one(resource_type, target_id)
        return self.repository.get(created["plan_id"])

    def assert_prohibited(self, plan) -> None:
        decision = evaluate_change_policy(plan)
        self.assertEqual(decision.policy_class, ApprovalPolicyClass.PROHIBITED)
        self.assertIn(CONFIGURATION_DELETE_INCOMPLETE, decision.reason_codes)

    @staticmethod
    def with_marker(plan, change=None, *, evidence=None, level=None):
        (operation,) = plan.operations
        if evidence is None:
            evidence = tuple(
                {**item, **change}
                if isinstance(item, dict) and item.get("trigger") == planning.POLICY_TRIGGER
                else item
                for item in operation.risk.evidence
            )
        risk = replace(operation.risk, evidence=evidence)
        if level is not None:
            risk = replace(risk, level=level)
        tampered = deepcopy(plan)
        tampered.operations = [replace(operation, risk=risk)]
        return tampered

    async def test_tampered_marker_or_basis_is_prohibited(self):
        plan = await self.planned("automation", "porch_light")
        self.assertEqual(
            evaluate_change_policy(plan).policy_class, ApprovalPolicyClass.STANDARD_ADMIN
        )
        for change in (
            {"policy": "elevated"},
            {"physical_consequence": "safety_critical"},
            {"basis": planning.BASIS_PROTECTED_EFFECT},
            {"basis": None},
        ):
            with self.subTest(change=change):
                self.assert_prohibited(self.with_marker(plan, change))
        (operation,) = plan.operations
        for evidence in ((), tuple(operation.risk.evidence) * 2):
            with self.subTest(markers=len(evidence)):
                self.assert_prohibited(self.with_marker(plan, evidence=evidence))

    async def test_a_protected_effect_relabelled_standard_is_prohibited(self):
        """The removed effect is recomputed from the pre-image, never trusted."""

        plan = await self.planned(
            "automation",
            "night_lock",
            automation_config("night_lock", service="lock.lock", target="lock.front_door"),
        )
        self.assertEqual(
            evaluate_change_policy(plan).policy_class, ApprovalPolicyClass.ELEVATED_ADMIN
        )
        tampered = self.with_marker(
            plan,
            {"policy": "standard", "physical_consequence": "none", "basis": ""},
            level=RiskLevel.MEDIUM,
        )
        self.assert_prohibited(tampered)

    async def test_hash_or_pre_image_mismatch_is_prohibited(self):
        plan = await self.planned("automation", "porch_light")
        (operation,) = plan.operations
        changed = deepcopy(operation.current_config)
        changed["alias"] = "Changed"
        for label, tampered_operation in (
            ("proposal_hash", replace(operation, proposed_config_hash="0" * 64)),
            ("pre_image", replace(operation, current_config=changed)),
            ("state_fingerprint", replace(operation, current_state_fingerprint="0" * 64)),
            ("not_valid", replace(operation, validation_results={"valid": False})),
            ("helper_type", replace(operation, helper_type="input_boolean")),
        ):
            with self.subTest(label=label):
                tampered = deepcopy(plan)
                tampered.operations = [tampered_operation]
                self.assert_prohibited(tampered)
                self.assertEqual(
                    configuration_operation_policy(tampered_operation).policy_class,
                    ApprovalPolicyClass.PROHIBITED,
                )

    async def test_mixed_duplicate_dependent_and_oversize_plans_are_prohibited(self):
        delete = (await self.planned("automation", "porch_light")).operations[0]
        other = (await self.planned("script", "other")).operations[0]
        base = await self.planned("automation", "spare")
        for label, operations in (
            ("mixed_with_update", [delete, replace(other, operation_id="u", action="update")]),
            ("duplicate_target", [delete, replace(delete, operation_id="second")]),
            ("dependent", [delete, replace(other, depends_on=(delete.operation_id,))]),
            ("nine_children", [
                replace(delete, operation_id=f"op_{index}", target_id=f"x{index}")
                for index in range(9)
            ]),
        ):
            with self.subTest(label=label):
                tampered = deepcopy(base)
                tampered.operations = operations
                self.assert_prohibited(tampered)

    async def test_a_delete_is_never_classified_as_no_effect(self):
        """Not create/update is not "no effect": the delete branch decides."""

        plan = await self.planned(
            "automation",
            "night_lock",
            automation_config("night_lock", service="lock.lock", target="lock.front_door"),
        )
        (operation,) = plan.operations
        decision = configuration_operation_policy(operation)
        self.assertEqual(decision.policy_class, ApprovalPolicyClass.ELEVATED_ADMIN)
        self.assertIn("configuration_delete_elevated_policy", decision.reason_codes)
        self.assertTrue(decision.safety_critical_acknowledgement)


class RemovedEffectComparisonTests(unittest.TestCase):
    """Lesson 4: "not a create or update" never means "no effect"."""

    def test_an_unmodelled_action_is_unresolved_never_no_effect(self):
        config = normalize_resource_config(
            "automation",
            automation_config("night_lock", service="lock.lock", target="lock.front_door"),
        )
        for action in ("frobnicate", "replace", ""):
            with self.subTest(action=action):
                comparison = compare_physical_effects("automation", action, config, None, None)
                self.assertIsNot(comparison, NONE_COMPARISON)
                self.assertTrue(comparison.bounds_exceeded)
                self.assertTrue(comparison.unlock_or_disarm)
                self.assertIn("comparison_action_unsupported", comparison.reason_codes)

    def test_a_delete_removes_every_protected_effect_of_the_pre_image(self):
        config = normalize_resource_config(
            "automation",
            automation_config("night_lock", service="lock.lock", target="lock.front_door"),
        )
        comparison = planning.compare_removed_effects("automation", config)
        self.assertTrue(comparison.protected)
        self.assertIn("configuration_deleted", comparison.reason_codes)
        ordinary = planning.compare_removed_effects(
            "automation", normalize_resource_config("automation", automation_config("a"))
        )
        self.assertFalse(ordinary.protected)

    def test_a_delete_compares_against_nothing_after(self):
        # The after side of a delete is empty: every protected row is removed,
        # never "changed" against a retained copy of the pre-image.
        config = normalize_resource_config(
            "automation",
            automation_config("night_lock", service="lock.lock", target="lock.front_door"),
        )
        comparison = compare_physical_effects("automation", "delete", config, config, None)
        rows = comparison.projection["rows"]
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(row=row.get("object_id")):
                self.assertEqual(row["verdict"], "removed")
                self.assertEqual(row["after"], [])
        self.assertIn("configuration_deleted", comparison.reason_codes)

    def test_no_adapter_defaults_an_effect(self):
        for action in ("delete", "frobnicate"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                ConfigurationOperationAdapter._expected_effect(action)
        self.assertEqual(
            ConfigurationDeleteAdapter._expected_effect("delete"),
            "configuration_resource_deleted",
        )
        for action in ("create", "update"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                ConfigurationDeleteAdapter._expected_effect(action)


class PlannerRefusalTests(ConfigurationDeleteServiceTestCase):
    async def refused(self, arguments, reason_code: str) -> dict:
        reads = self.reads()
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_configuration_delete_plan(arguments)
        details = raised.exception.details
        self.assertEqual(details.get("reason_code"), reason_code, details)
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.config_rest.writes(), [])
        return {"details": details, "reads": self.reads() - reads}

    async def test_malformed_requests_refuse_before_any_read(self):
        one = {"resource_type": "automation", "target_id": "a"}
        cases = [
            ({"deletions": [], "reason": "r"}, planning.INVALID_VALUE),
            ({"deletions": one, "reason": "r"}, planning.INVALID_VALUE),
            (
                {
                    "deletions": [
                        {"resource_type": "automation", "target_id": f"a{i}"}
                        for i in range(9)
                    ],
                    "reason": "r",
                },
                "configuration_delete_too_many_targets",
            ),
            ({"deletions": [one, dict(one)], "reason": "r"}, "configuration_delete_duplicate_target"),
            (
                {"deletions": [{"resource_type": "helper", "target_id": "a"}], "reason": "r"},
                "configuration_delete_resource_type_not_supported",
            ),
            ({"deletions": [{**one, "extra": 1}], "reason": "r"}, planning.INVALID_VALUE),
            ({"deletions": [one], "reason": "r", "force": True}, planning.FIELD_NOT_SUPPORTED),
            (
                {"deletions": [{"resource_type": "automation", "target_id": "../a"}], "reason": "r"},
                planning.INVALID_VALUE,
            ),
            (
                {"deletions": [{"resource_type": "automation", "target_id": " a"}], "reason": "r"},
                planning.INVALID_VALUE,
            ),
            ({"deletions": [one], "reason": " "}, planning.INVALID_VALUE),
            ({"deletions": [one], "reason": "r" * 501}, planning.INVALID_VALUE),
            ({"deletions": [one], "reason": "r", "expiration_minutes": 4}, planning.INVALID_VALUE),
            ({"deletions": [one], "reason": "r", "expiration_minutes": True}, planning.INVALID_VALUE),
        ]
        for arguments, reason_code in cases:
            with self.subTest(arguments=repr(arguments)[:80]):
                outcome = await self.refused(arguments, reason_code)
                self.assertEqual(outcome["reads"], 0)

    async def test_missing_config_refuses_as_not_found(self):
        await self.refused(
            {"deletions": [{"resource_type": "automation", "target_id": "absent"}], "reason": "r"},
            "configuration_delete_target_not_found",
        )

    async def test_missing_registry_entry_refuses(self):
        self.add("script", "orphan")
        self.ws.entries.pop("script.orphan")
        await self.refused(
            {"deletions": [{"resource_type": "script", "target_id": "orphan"}], "reason": "r"},
            "configuration_delete_registry_entry_missing",
        )

    async def test_one_bad_target_refuses_the_whole_plan(self):
        self.add("automation", "present")
        await self.refused(
            {
                "deletions": [
                    {"resource_type": "automation", "target_id": "present"},
                    {"resource_type": "automation", "target_id": "absent"},
                ],
                "reason": "r",
            },
            "configuration_delete_target_not_found",
        )
        self.assertIn("present", self.config_rest.configs["automation"])


if __name__ == "__main__":
    unittest.main()
