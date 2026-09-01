"""Beta 33 retained-effect risk-delta and staged-release coverage."""

from __future__ import annotations

import copy
from pathlib import Path
import re
import sys
import unittest

from awesomeversion import AwesomeVersion


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.capabilities import BETA_NATIVE_CAPABILITIES  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalPolicyClass,
    ChangeOperation,
    ConfigurationOperation,
    PhysicalConsequence,
    RiskDelta,
)
from ha_mcp_engineering.governance.normalize import (  # noqa: E402
    normalize_automation,
    stable_hash,
    state_fingerprint,
    structured_diff,
)
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    configuration_operation_policy,
)
from ha_mcp_engineering.governance.risk import classify_risk  # noqa: E402
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.task_models import (  # noqa: E402
    ExecutionTaskState,
)
from tests.test_dev14_configuration_plans import (  # noqa: E402
    ConfigurationPlanTestCase,
)


PRE_PROMOTION_VERSION = "2.2.0-beta.32"
BETA33_VERSION = "2.2.0-beta.33"
GARAGE_TARGET = ("automation", "garage_close_guard")
CURRENT_GARAGE_AUTOMATION = {
    "id": GARAGE_TARGET[1],
    "alias": "Garage close guard",
    "description": "Close the garage only after the existing delay",
    "mode": "single",
    "trigger": [
        {
            "platform": "state",
            "entity_id": "binary_sensor.garage_open",
            "to": "on",
            "for": "00:10:00",
        }
    ],
    "condition": [],
    "action": [
        {
            "service": "cover.close_cover",
            "target": {"entity_id": "cover.garage_door"},
        }
    ],
}
OBSTRUCTION_GUARD = {
    "condition": "state",
    "entity_id": "binary_sensor.garage_obstruction",
    "state": "off",
}


def _operation_policy(current, proposed, *, action="update"):
    risk_operation = (
        ChangeOperation.CREATE_AUTOMATION
        if action == "create"
        else ChangeOperation.UPDATE_AUTOMATION
    )
    risk = classify_risk(
        risk_operation,
        structured_diff(current if action == "update" else None, proposed),
        proposed,
    )
    normalized_current = normalize_automation(
        current if action == "update" else None
    )
    normalized_proposed = normalize_automation(proposed) or {}
    operation = ConfigurationOperation(
        operation_id="garage_guard_delta",
        order=0,
        depends_on=[],
        resource_type="automation",
        action=action,
        target_id=GARAGE_TARGET[1],
        helper_type=None,
        proposed_config=copy.deepcopy(proposed),
        current_config=copy.deepcopy(current) if current is not None else None,
        normalized_proposed_config=normalized_proposed,
        normalized_current_config=normalized_current,
        current_state_fingerprint=state_fingerprint(
            current if action == "update" else None
        ),
        proposed_config_hash=stable_hash(normalized_proposed),
        normalization_version=3,
        risk=risk,
    )
    return configuration_operation_policy(operation)


class Beta33RiskDeltaTests(ConfigurationPlanTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.gateway.configs[GARAGE_TARGET] = copy.deepcopy(
            CURRENT_GARAGE_AUTOMATION
        )

    def proposed_with_guard(self):
        proposed = copy.deepcopy(CURRENT_GARAGE_AUTOMATION)
        proposed.pop("id")
        proposed["condition"] = [copy.deepcopy(OBSTRUCTION_GUARD)]
        return proposed

    async def create_guard_plan(self):
        return await self.service.create_configuration_plan(
            title="Add garage obstruction guard",
            description=(
                "Retain the existing close effect and narrow its execution"
            ),
            operations=[
                {
                    "operation_id": "add_obstruction_guard",
                    "resource_type": "automation",
                    "action": "update",
                    "target_id": GARAGE_TARGET[1],
                    "depends_on": [],
                    "proposed_config": self.proposed_with_guard(),
                }
            ],
        )

    async def test_retained_effect_guard_uses_elevated_approval_and_f3_once(
        self,
    ):
        created = await self.create_guard_plan()
        decision = created["policy_decision"]
        self.assertEqual(decision["policy_class"], "elevated_admin")
        self.assertEqual(decision["risk_delta"], "moderate")
        self.assertEqual(
            decision["physical_consequence"], "safety_critical"
        )
        self.assertEqual(
            decision["required_acknowledgements"],
            ["plan_approval"],
        )
        self.assertEqual(
            decision["reason_codes"],
            [
                "non_risk_increasing_condition_guard_added",
                "retained_safety_critical_effect",
                "safety_critical_effect_requires_elevated_review",
                "supported_configuration_change",
            ],
        )

        _pending, review, granted = await self.approve(created)
        self.assertEqual(granted["status"], "approved")
        changes = review["operation_summaries"][0][
            "semantic_projection"
        ]["changes"]
        self.assertTrue(
            any(item["path"].startswith("/condition/0") for item in changes)
        )
        self.assertFalse(
            any(item["path"].startswith("/action") for item in changes)
        )

        self.gateway.calls.clear()
        applied = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        writes = [call for call in self.gateway.calls if call[0] == "write"]
        self.assertEqual(
            writes,
            [("write", "update", "automation", GARAGE_TARGET[1])],
        )
        self.assertEqual(applied["execution_outcome"], "applied")
        self.assertEqual(
            applied["operations"][0]["execution_status"],
            "applied_verified",
        )
        task = self.service.task_repository.get_for_plan(created["plan_id"])
        self.assertIsNotNone(task)
        assert task is not None
        self.assertEqual(task.state, ExecutionTaskState.SUCCEEDED_VERIFIED)
        self.assertEqual(len(task.provider_attempts), 1)

        repeated = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual(repeated["status"], "already_applied")
        self.assertEqual(
            len([call for call in self.gateway.calls if call[0] == "write"]),
            1,
        )

    async def test_stale_current_state_still_fails_before_dispatch(self):
        created = await self.create_guard_plan()
        await self.approve(created)
        changed = copy.deepcopy(CURRENT_GARAGE_AUTOMATION)
        changed["description"] = "Changed after approval"
        self.gateway.configs[GARAGE_TARGET] = changed
        self.gateway.calls.clear()

        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )

        self.assertEqual(raised.exception.code, ErrorCode.STALE_TARGET_STATE)
        self.assertEqual(
            [call for call in self.gateway.calls if call[0] == "write"], []
        )
        task = self.service.task_repository.get_for_plan(created["plan_id"])
        self.assertIsNotNone(task)
        assert task is not None
        self.assertEqual(task.state, ExecutionTaskState.FAILED_PRE_DISPATCH)
        self.assertEqual(task.provider_attempts, [])

    async def test_tampered_retained_effect_proof_cannot_create_authority(self):
        created = await self.create_guard_plan()
        plan = self.repository.get(created["plan_id"])
        self.assertIsNotNone(plan)
        assert plan is not None
        plan.operations[0].normalized_current_config["action"][0][
            "service"
        ] = "cover.open_cover"
        self.repository.save(plan)

        with self.assertRaises(GovernanceError) as raised:
            self.service.approve(created["plan_id"], created["plan_hash"])

        self.assertEqual(
            raised.exception.code, ErrorCode.POLICY_SNAPSHOT_MISMATCH
        )
        self.assertEqual(
            [call for call in self.gateway.calls if call[0] == "write"], []
        )
        self.assertIsNone(
            self.service.task_repository.get_for_plan(created["plan_id"])
        )

    async def test_new_or_changed_exact_effect_is_owner_actionable_without_dispatch(
        self,
    ):
        proposed = self.proposed_with_guard()
        proposed["action"][0]["service"] = "cover.open_cover"
        created = await self.service.create_configuration_plan(
            title="Changed garage effect",
            description="Disclose the changed effect for one owner decision",
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
        decision = created["policy_decision"]
        self.assertEqual(decision["policy_version"], "f2-v2")
        self.assertEqual(decision["policy_class"], "elevated_admin")
        self.assertEqual(decision["physical_consequence"], "safety_critical")
        self.assertEqual(decision["required_acknowledgements"], ["plan_approval"])
        self.assertTrue(created["approval_actionable"])
        _pending, _review, granted = await self.approve(created)
        self.assertEqual(granted["status"], "approved")
        self.assertEqual(
            [call for call in self.gateway.calls if call[0] == "write"], []
        )
        self.assertIsNone(self.service.task_repository.get_for_plan(created["plan_id"]))

    def test_only_exact_appended_reviewed_guards_qualify(self):
        current = copy.deepcopy(CURRENT_GARAGE_AUTOMATION)
        current.pop("id")
        current["condition"] = [
            {
                "condition": "state",
                "entity_id": "input_boolean.garage_automation_enabled",
                "state": "on",
            }
        ]
        valid = copy.deepcopy(current)
        valid["condition"].append(copy.deepcopy(OBSTRUCTION_GUARD))
        accepted = _operation_policy(current, valid)
        self.assertEqual(
            accepted.policy_class, ApprovalPolicyClass.ELEVATED_ADMIN
        )
        self.assertEqual(accepted.risk_delta, RiskDelta.MODERATE)
        self.assertEqual(
            accepted.physical_consequence,
            PhysicalConsequence.SAFETY_CRITICAL,
        )

        cases = {}
        cases["condition_removed"] = {
            **copy.deepcopy(current),
            "condition": [],
        }
        cases["condition_replaced"] = {
            **copy.deepcopy(current),
            "condition": [copy.deepcopy(OBSTRUCTION_GUARD)],
        }
        inserted = copy.deepcopy(current)
        inserted["condition"].insert(0, copy.deepcopy(OBSTRUCTION_GUARD))
        cases["condition_inserted_before_existing"] = inserted
        changed_trigger = copy.deepcopy(valid)
        changed_trigger["trigger"][0]["for"] = "00:01:00"
        cases["trigger_broadened"] = changed_trigger
        changed_target = copy.deepcopy(valid)
        changed_target["action"][0]["target"] = {
            "entity_id": "cover.garage_side_door"
        }
        cases["target_changed"] = changed_target
        added_action = copy.deepcopy(valid)
        added_action["action"].append(
            {
                "service": "cover.close_cover",
                "target": {"entity_id": "cover.garage_side_door"},
            }
        )
        cases["action_added"] = added_action
        disabled = copy.deepcopy(current)
        disabled["condition"].append(
            {**copy.deepcopy(OBSTRUCTION_GUARD), "enabled": False}
        )
        cases["disabled_guard"] = disabled
        unknown = copy.deepcopy(current)
        unknown["condition"].append({"condition": "future_family"})
        cases["unknown_guard"] = unknown
        executable = copy.deepcopy(current)
        executable["condition"].append(
            {
                "condition": "state",
                "entity_id": "binary_sensor.garage_obstruction",
                "state": "off",
                "service": "cover.close_cover",
            }
        )
        cases["action_like_guard"] = executable
        unknown_behavioral_field = copy.deepcopy(valid)
        unknown_behavioral_field["future_behavior"] = None
        cases["unknown_behavioral_field_added"] = unknown_behavioral_field

        for name, proposed in cases.items():
            with self.subTest(name=name):
                decision = _operation_policy(current, proposed)
                self.assertEqual(
                    decision.policy_class, ApprovalPolicyClass.PROHIBITED
                )
                self.assertIn(
                    "safety_critical_effect_not_reviewed",
                    decision.reason_codes,
                )

        retained_lock = copy.deepcopy(current)
        retained_lock["action"] = [
            {
                "service": "lock.unlock",
                "target": {"entity_id": "lock.garage_entry"},
            }
        ]
        guarded_lock = copy.deepcopy(retained_lock)
        guarded_lock["condition"].append(copy.deepcopy(OBSTRUCTION_GUARD))
        self.assertEqual(
            _operation_policy(retained_lock, guarded_lock).policy_class,
            ApprovalPolicyClass.ELEVATED_ADMIN,
        )

    def test_tautological_template_guard_is_not_labeled_strict_reduction(self):
        current = copy.deepcopy(CURRENT_GARAGE_AUTOMATION)
        current.pop("id")
        proposed = copy.deepcopy(current)
        proposed["condition"] = [
            {
                "condition": "template",
                "value_template": "{{ true }}",
            }
        ]

        decision = _operation_policy(current, proposed)

        self.assertEqual(
            decision.policy_class, ApprovalPolicyClass.ELEVATED_ADMIN
        )
        self.assertEqual(decision.risk_delta, RiskDelta.MODERATE)
        self.assertIn(
            "non_risk_increasing_condition_guard_added",
            decision.reason_codes,
        )
        self.assertNotIn(
            "risk_reducing_condition_guard_added",
            decision.reason_codes,
        )

    def test_ambiguous_or_blueprint_effects_never_use_the_exception(self):
        unresolved = copy.deepcopy(CURRENT_GARAGE_AUTOMATION)
        unresolved.pop("id")
        unresolved["action"][0]["target"] = {
            "entity_id": "{{ selected_garage_cover }}"
        }
        guarded_unresolved = copy.deepcopy(unresolved)
        guarded_unresolved["condition"] = [copy.deepcopy(OBSTRUCTION_GUARD)]

        blueprint = {
            "alias": "Blueprint garage fixture",
            "use_blueprint": {
                "path": "example/garage.yaml",
                "input": {"garage_cover": "cover.garage_door"},
            },
        }
        guarded_blueprint = copy.deepcopy(blueprint)
        guarded_blueprint["condition"] = [copy.deepcopy(OBSTRUCTION_GUARD)]

        for name, current, proposed in (
            ("unresolved", unresolved, guarded_unresolved),
            ("blueprint", blueprint, guarded_blueprint),
        ):
            with self.subTest(name=name):
                decision = _operation_policy(current, proposed)
                self.assertEqual(
                    decision.policy_class, ApprovalPolicyClass.PROHIBITED
                )

    def test_create_never_uses_the_retained_effect_exception(self):
        proposed = copy.deepcopy(CURRENT_GARAGE_AUTOMATION)
        proposed.pop("id")
        proposed["condition"] = [copy.deepcopy(OBSTRUCTION_GUARD)]
        decision = _operation_policy(None, proposed, action="create")
        self.assertEqual(decision.policy_class, ApprovalPolicyClass.PROHIBITED)
        self.assertEqual(decision.risk_delta, RiskDelta.HIGH)


class Beta33ReleaseBoundaryTests(unittest.TestCase):
    def authoritative_versions(self) -> set[str]:
        patterns = (
            (BETA_DIR / "config.yaml", r'(?m)^version: "([^"]+)"$'),
            (
                BETA_DIR / "ha_mcp_engineering" / "version.py",
                r'(?m)^SERVER_VERSION = "([^"]+)"$',
            ),
            (
                ROOT / "scripts" / "validate_addon_metadata.py",
                r'(?m)^BETA_VERSION = "([^"]+)"$',
            ),
        )
        versions = set()
        for path, pattern in patterns:
            matches = re.findall(pattern, path.read_text(encoding="utf-8"))
            self.assertEqual(len(matches), 1, str(path))
            versions.add(matches[0])
        return versions

    def require_release_phase(self, expected_version: str) -> set[str]:
        versions = self.authoritative_versions()
        self.assertEqual(len(versions), 1)
        actual_version = next(iter(versions))
        if AwesomeVersion(actual_version) > AwesomeVersion(BETA33_VERSION):
            self.skipTest(
                "Beta 33 phase assertions do not apply after a later release"
            )
        self.assertIn(actual_version, (PRE_PROMOTION_VERSION, BETA33_VERSION))
        if actual_version != expected_version:
            self.skipTest(
                f"{expected_version} assertions do not apply to "
                f"release phase {actual_version}"
            )
        return versions

    def assert_beta33_documents_resolve_exactly(self) -> None:
        release = ROOT / "docs" / "V2_2_0_BETA33_RELEASE_NOTES.md"
        acceptance = ROOT / "docs" / "V2_2_0_BETA33_ACCEPTANCE.md"
        self.assertTrue(release.is_file())
        self.assertTrue(acceptance.is_file())
        self.assertIn(
            "Reviewed non-expansion retained-effect proof",
            release.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "Fresh garage retained-effect canary",
            acceptance.read_text(encoding="utf-8"),
        )

    def test_beta33_is_staged_without_changing_published_versions(self):
        self.assertEqual(
            self.require_release_phase(PRE_PROMOTION_VERSION),
            {PRE_PROMOTION_VERSION},
        )
        self.assertEqual(
            (ROOT / ".release" / "next-version").read_text().strip(),
            BETA33_VERSION,
        )
        self.assertLess(
            AwesomeVersion(PRE_PROMOTION_VERSION),
            AwesomeVersion(BETA33_VERSION),
        )
        self.assertIn(
            'version: "1.1.2"',
            (ROOT / "hass_mcp_admin" / "config.yaml").read_text(),
        )
        self.assert_beta33_documents_resolve_exactly()

    def test_beta33_generated_release_state_is_exact(self):
        marker = ROOT / ".release" / "next-version"
        if marker.exists() and AwesomeVersion(
            marker.read_text().strip()
        ) > AwesomeVersion(BETA33_VERSION):
            self.skipTest(
                "Beta 33 generated-state assertions do not apply while a "
                "later release is staged"
            )
        self.assertEqual(
            self.require_release_phase(BETA33_VERSION), {BETA33_VERSION}
        )
        self.assertFalse(marker.exists())
        self.assertIn(
            'version: "1.1.2"',
            (ROOT / "hass_mcp_admin" / "config.yaml").read_text(),
        )
        self.assert_beta33_documents_resolve_exactly()

    def test_scope_adds_no_tool_provider_or_fallback(self):
        self.assertEqual(len(BETA_NATIVE_CAPABILITIES), 26)
        source = (
            BETA_DIR
            / "ha_mcp_engineering"
            / "governance"
            / "policy.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("fallback", source)
        self.assertNotIn("call_service", source)


if __name__ == "__main__":
    unittest.main()
