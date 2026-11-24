"""HAMCP-293: change-scoped risk for exact automation and script updates.

Josh's rule (2026-11-24, as relayed by the architect): an update is HIGH
when a high-risk call is added or changed, or when what gates it (a
trigger, condition, mode, or an enclosing or preceding step) changes;
otherwise it is classified by the changed parts only. The reasons name the
cause and the calls, and the evidence drops unchanged calls.

Negatives: the unlock/disarm floor stays, an unresolved comparison keeps the
whole-configuration rule, an added or removed call is HIGH, a condition-only
change gating a call is HIGH, and the legacy route without the current
configuration is unchanged.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT))

from ha_mcp_engineering.governance.models import (  # noqa: E402
    ChangeOperation,
    RiskLevel,
)
from ha_mcp_engineering.governance.physical_effects import (  # noqa: E402
    VERDICT_EQUIVALENT,
    VERDICT_UNRESOLVED,
    compare_operation,
)
from ha_mcp_engineering.governance import risk as risk_module  # noqa: E402
from ha_mcp_engineering.governance.risk import classify_risk  # noqa: E402
from tests.test_dev14_configuration_plans import (  # noqa: E402
    ConfigurationPlanTestCase,
)

SAFETY = "safety_critical_acknowledgement"
GENERIC_HIGH = "Structured action or target requires high-risk review"
CHANGED = "High-risk call added or changed"
STEP = "Preceding or enclosing step change gating high-risk calls"
REMOVED = "High-risk call removed or moved"
BROAD = "Broad entity or area target detected"

HELPER = {"action": "script.hamcp293_helper"}
PAUSE = {"action": "automation.turn_off", "target": {"entity_id": "automation.lights"}}
UNLOCK = {"action": "lock.unlock", "target": {"entity_id": "lock.front_door"}}


def notify(message):
    return {"action": "notify.mobile_app_phone", "data": {"message": message}}


def automation(*steps, condition=None, mode="single", trace=None):
    config = {
        "alias": "HAMCP-293 automation",
        "description": "HAMCP-293 regression",
        "mode": mode,
        "trigger": [
            {"platform": "state", "entity_id": "binary_sensor.door", "to": "on"}
        ],
        "condition": list(condition or []),
        "action": list(steps),
    }
    if trace is not None:
        config["trace"] = {"stored_traces": trace}
    return config


def script(*steps, alias="HAMCP-293 script"):
    return {"alias": alias, "mode": "single", "sequence": list(steps)}


def condition(entity_id):
    return [{"condition": "state", "entity_id": entity_id, "state": "on"}]


def high_services(operation):
    return {
        item.get("service")
        for item in operation.risk.evidence
        if item.get("trigger") == "high_risk_service"
    }


class ChangeScopedRiskPlanTests(ConfigurationPlanTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        # The called script has no protected effect, so the comparison
        # verdict is "none" and the change scope applies.
        self.gateway.configs[("script", "hamcp293_helper")] = script(
            notify("helper")
        )

    async def plan(self, resource_type, target, before, after):
        self.gateway.configs[(resource_type, target)] = copy.deepcopy(before)
        created = await self.service.create_configuration_plan(
            title="HAMCP-293",
            description="Change-scoped risk regression",
            operations=[
                {
                    "operation_id": "op",
                    "resource_type": resource_type,
                    "action": "update",
                    "target_id": target,
                    "depends_on": [],
                    "proposed_config": copy.deepcopy(after),
                }
            ],
        )
        operation = self.repository.get(created["plan_id"]).operations[0]
        return created, operation

    def policy(self, created):
        decision = created["policy_decision"]
        return decision["policy_class"], decision["required_acknowledgements"]

    async def test_unrelated_change_with_an_unchanged_call_is_classified_by_the_change(self):
        before = automation(HELPER, notify("a"))
        after = automation(HELPER, notify("b"))
        created, operation = await self.plan("automation", "a293_unrelated", before, after)
        self.assertNotEqual(operation.risk.level, RiskLevel.HIGH, operation.risk.reasons)
        self.assertNotIn(GENERIC_HIGH, operation.risk.reasons)
        self.assertEqual(high_services(operation), set())
        self.assertEqual(self.policy(created), ("standard_admin", ["plan_approval"]))
        # Control: the legacy whole-configuration rule still flags it.
        diff = {"changed_fields": [{"field": "actions", "change_type": "modified"}]}
        legacy = classify_risk(ChangeOperation.UPDATE_AUTOMATION, diff, after)
        self.assertEqual(legacy.level, RiskLevel.HIGH)
        self.assertIn(GENERIC_HIGH, legacy.reasons)

    async def test_changed_call_is_cited_alone(self):
        before = automation(HELPER, PAUSE)
        after = automation(
            HELPER,
            {"action": "automation.turn_off", "target": {"entity_id": "automation.fans"}},
        )
        created, operation = await self.plan("automation", "a293_one_call", before, after)
        self.assertEqual(operation.risk.level, RiskLevel.HIGH)
        self.assertIn(f"{CHANGED}: automation.turn_off", operation.risk.reasons)
        self.assertFalse(
            any("script.hamcp293_helper" in reason for reason in operation.risk.reasons),
            operation.risk.reasons,
        )
        self.assertEqual(high_services(operation), {"automation.turn_off"})
        self.assertEqual(self.policy(created)[0], "elevated_admin")

    async def test_condition_only_change_gating_a_call_is_high(self):
        before = automation(HELPER, condition=condition("input_boolean.away"))
        after = automation(HELPER, condition=condition("input_boolean.home"))
        created, operation = await self.plan("automation", "a293_condition", before, after)
        self.assertEqual(operation.risk.level, RiskLevel.HIGH)
        self.assertIn(
            "Condition change gating high-risk calls: script.hamcp293_helper",
            operation.risk.reasons,
        )
        self.assertEqual(high_services(operation), {"script.hamcp293_helper"})
        self.assertEqual(self.policy(created)[0], "elevated_admin")

    async def test_trigger_and_mode_changes_gate_every_call(self):
        moved = automation(HELPER)
        moved["trigger"][0]["to"] = "off"
        for name, after, cause in (
            ("trigger", moved, "Trigger"),
            ("mode", automation(HELPER, mode="restart"), "Mode"),
        ):
            with self.subTest(change=name):
                _created, operation = await self.plan(
                    "automation", f"a293_{name}", automation(HELPER), after
                )
                self.assertEqual(operation.risk.level, RiskLevel.HIGH)
                self.assertIn(
                    f"{cause} change gating high-risk calls: script.hamcp293_helper",
                    operation.risk.reasons,
                )

    async def test_added_call_is_high(self):
        _created, operation = await self.plan(
            "automation", "a293_added", automation(notify("a")), automation(notify("a"), PAUSE)
        )
        self.assertEqual(operation.risk.level, RiskLevel.HIGH)
        self.assertIn(f"{CHANGED}: automation.turn_off", operation.risk.reasons)

    async def test_removed_call_is_high(self):
        _created, operation = await self.plan(
            "automation", "a293_removed", automation(HELPER, PAUSE), automation(HELPER)
        )
        self.assertEqual(operation.risk.level, RiskLevel.HIGH)
        self.assertIn(GENERIC_HIGH, operation.risk.reasons)
        self.assertIn(f"{REMOVED}: automation.turn_off", operation.risk.reasons)
        self.assertEqual(high_services(operation), set())

    async def test_preceding_step_change_gates_a_later_call(self):
        _created, operation = await self.plan(
            "automation",
            "a293_preceding",
            automation(notify("a"), HELPER),
            automation(notify("b"), HELPER),
        )
        self.assertEqual(operation.risk.level, RiskLevel.HIGH)
        self.assertIn(f"{STEP}: script.hamcp293_helper", operation.risk.reasons)

    async def test_trace_only_change_is_not_high(self):
        created, operation = await self.plan(
            "automation", "a293_trace", automation(HELPER, trace=5), automation(HELPER, trace=10)
        )
        self.assertNotEqual(operation.risk.level, RiskLevel.HIGH, operation.risk.reasons)
        self.assertEqual(high_services(operation), set())
        self.assertEqual(self.policy(created), ("standard_admin", ["plan_approval"]))

    async def test_unlock_floor_survives_an_unrelated_change(self):
        created, operation = await self.plan(
            "automation",
            "a293_unlock",
            automation(UNLOCK, notify("a")),
            automation(UNLOCK, notify("b")),
        )
        comparison = compare_operation(operation)
        self.assertEqual(comparison.verdict, VERDICT_EQUIVALENT)
        # The OD-6(b) floor comes from the comparison, not the risk level.
        (row,) = comparison.projection["rows"]
        self.assertIn("unlock_or_disarm_effect", row["reason_codes"])
        self.assertTrue(row["acknowledgement_required"])
        self.assertEqual(self.policy(created)[1], ["plan_approval", SAFETY])

    async def test_unlock_kept_while_a_changed_call_is_cited(self):
        created, operation = await self.plan(
            "automation",
            "a293_unlock_mixed",
            automation(UNLOCK, PAUSE),
            automation(
                UNLOCK,
                {"action": "automation.turn_off", "target": {"entity_id": "automation.fans"}},
            ),
        )
        self.assertEqual(operation.risk.level, RiskLevel.HIGH)
        self.assertIn(f"{CHANGED}: automation.turn_off", operation.risk.reasons)
        self.assertIn(
            "Protected-object call kept at high risk: lock.unlock",
            operation.risk.reasons,
        )
        self.assertIn("lock.unlock", high_services(operation))
        self.assertEqual(self.policy(created)[1], ["plan_approval", SAFETY])
        # A changed step ahead of the unlock gates it.
        _created, ahead = await self.plan(
            "automation",
            "a293_unlock_ahead",
            automation(PAUSE, UNLOCK),
            automation(
                {"action": "automation.turn_off", "target": {"entity_id": "automation.fans"}},
                UNLOCK,
            ),
        )
        self.assertIn(f"{STEP}: lock.unlock", ahead.risk.reasons)

    async def test_unresolved_comparison_keeps_the_whole_configuration_rule(self):
        # The called script is not captured, so the comparison cannot bound
        # it: the legacy rule applies and the OD-6(b) floor is required.
        missing = {"action": "script.hamcp293_missing"}
        created, operation = await self.plan(
            "automation",
            "a293_unresolved",
            automation(missing, notify("a")),
            automation(missing, notify("b")),
        )
        self.assertEqual(compare_operation(operation).verdict, VERDICT_UNRESOLVED)
        self.assertEqual(operation.risk.level, RiskLevel.HIGH)
        self.assertIn(GENERIC_HIGH, operation.risk.reasons)
        self.assertFalse(
            any(reason.startswith((CHANGED, STEP)) for reason in operation.risk.reasons),
            operation.risk.reasons,
        )
        self.assertIn("script.hamcp293_missing", high_services(operation))
        self.assertEqual(self.policy(created)[1], ["plan_approval", SAFETY])

    async def test_broad_target_counts_only_when_in_scope(self):
        lights = {
            "action": "light.turn_on",
            "target": {"entity_id": [f"light.room_{index}" for index in range(11)]},
        }
        _created, unchanged = await self.plan(
            "automation", "a293_broad_kept", automation(lights, notify("a")), automation(lights, notify("b"))
        )
        self.assertNotIn(BROAD, unchanged.risk.reasons)
        self.assertNotIn("Physical-device action detected", unchanged.risk.reasons)
        _created, added = await self.plan(
            "automation", "a293_broad_added", automation(notify("a")), automation(notify("a"), lights)
        )
        self.assertIn(BROAD, added.risk.reasons)

    async def test_removed_device_call_still_counts_as_a_device_change(self):
        def light(entity_id):
            return {"action": "light.turn_on", "target": {"entity_id": entity_id}}

        _created, operation = await self.plan(
            "automation",
            "a293_light_removed",
            automation(light("light.a"), light("light.b")),
            automation(light("light.a")),
        )
        self.assertEqual(operation.risk.level, RiskLevel.MEDIUM)
        self.assertIn("Physical-device action detected", operation.risk.reasons)

    async def test_branch_context_scopes_if_then_else(self):
        def branches(test_entity, else_target):
            return automation(
                {
                    "if": condition(test_entity),
                    "then": [HELPER],
                    "else": [
                        {"action": "automation.turn_off", "target": {"entity_id": else_target}}
                    ],
                }
            )

        before = branches("input_boolean.a", "automation.lights")
        _created, else_only = await self.plan(
            "automation", "a293_else", before, branches("input_boolean.a", "automation.fans")
        )
        self.assertIn(f"{CHANGED}: automation.turn_off", else_only.risk.reasons)
        self.assertEqual(high_services(else_only), {"automation.turn_off"})
        _created, test_changed = await self.plan(
            "automation", "a293_if", before, branches("input_boolean.b", "automation.lights")
        )
        self.assertIn(
            f"{STEP}: automation.turn_off, script.hamcp293_helper",
            test_changed.risk.reasons,
        )

    async def test_choose_option_gates_its_own_and_later_options(self):
        def chooser(first, second, second_call):
            return automation(
                {
                    "choose": [
                        {"conditions": condition(first), "sequence": [HELPER]},
                        {"conditions": condition(second), "sequence": [second_call]},
                    ]
                }
            )

        before = chooser("input_boolean.a", "input_boolean.b", PAUSE)
        _created, later = await self.plan(
            "automation", "a293_choose_later", before, chooser("input_boolean.a", "input_boolean.c", PAUSE)
        )
        self.assertEqual(high_services(later), {"automation.turn_off"})
        _created, earlier = await self.plan(
            "automation", "a293_choose_earlier", before, chooser("input_boolean.c", "input_boolean.b", PAUSE)
        )
        self.assertEqual(
            high_services(earlier), {"automation.turn_off", "script.hamcp293_helper"}
        )

    async def test_script_reasons_keep_dotted_call_names(self):
        trigger = {"action": "automation.trigger", "target": {"entity_id": "automation.lights"}}
        created, operation = await self.plan(
            "script", "s293_trigger", script(notify("a")), script(notify("a"), trigger)
        )
        self.assertIn(f"{CHANGED}: automation.trigger", operation.risk.reasons)
        self.assertFalse(
            any("script.trigger" in reason for reason in operation.risk.reasons),
            operation.risk.reasons,
        )
        self.assertEqual(self.policy(created)[0], "elevated_admin")

    async def test_script_unrelated_change_is_classified_by_the_change(self):
        created, operation = await self.plan(
            "script", "s293_unrelated", script(HELPER, notify("a")), script(HELPER, notify("b"))
        )
        self.assertNotEqual(operation.risk.level, RiskLevel.HIGH, operation.risk.reasons)
        self.assertEqual(self.policy(created), ("standard_admin", ["plan_approval"]))


class LegacyRouteTests(unittest.TestCase):
    """Without the current configuration the whole-configuration rule holds."""

    DIFF = {"changed_fields": [{"field": "actions", "change_type": "modified"}]}

    def test_update_without_current_is_unchanged(self):
        config = automation(HELPER, notify("b"))
        risk = classify_risk(ChangeOperation.UPDATE_AUTOMATION, self.DIFF, config)
        self.assertEqual(risk.level, RiskLevel.HIGH)
        self.assertEqual(risk.reasons, [GENERIC_HIGH])

    def test_create_is_unchanged(self):
        config = automation(HELPER)
        risk = classify_risk(
            ChangeOperation.CREATE_AUTOMATION, {"changed_fields": []}, config, current=None
        )
        self.assertEqual(risk.level, RiskLevel.HIGH)
        self.assertEqual(risk.reasons, [GENERIC_HIGH])

    def test_unresolved_verdict_is_unchanged(self):
        before = automation(HELPER, notify("a"))
        after = automation(HELPER, notify("b"))
        risk = classify_risk(
            ChangeOperation.UPDATE_AUTOMATION,
            self.DIFF,
            after,
            current=before,
            protected_effect_verdict="unresolved",
        )
        self.assertEqual(risk.reasons, [GENERIC_HIGH])

    def test_both_action_roots_use_the_legacy_rule(self):
        before = automation(HELPER, notify("a"))
        after = automation(HELPER, notify("b"))
        after["actions"] = [notify("c")]
        risk = classify_risk(
            ChangeOperation.UPDATE_AUTOMATION, self.DIFF, after, current=before
        )
        self.assertEqual(risk.reasons, [GENERIC_HIGH])

    def test_scoped_and_legacy_agree_when_every_call_changes(self):
        before = automation(notify("a"))
        after = automation(HELPER, PAUSE)
        scoped = classify_risk(
            ChangeOperation.UPDATE_AUTOMATION, self.DIFF, after, current=before
        )
        legacy = classify_risk(ChangeOperation.UPDATE_AUTOMATION, self.DIFF, after)
        self.assertEqual(scoped.level, legacy.level)
        self.assertEqual(scoped.evidence, legacy.evidence)
        self.assertIn(
            f"{CHANGED}: automation.turn_off, script.hamcp293_helper", scoped.reasons
        )


class ScopeTraversalTests(unittest.TestCase):
    """The target scans and the change scope must visit the same call nodes."""

    def test_every_scanned_call_node_has_a_signature(self):
        lights = {"action": "light.turn_on", "target": {"entity_id": "light.a"}}
        config = automation(
            {"choose": [{"conditions": [], "sequence": [lights]}], "default": [PAUSE]},
            {"if": [], "then": [UNLOCK], "else": [HELPER]},
            {"parallel": [lights, {"sequence": [notify("p")]}]},
            {"repeat": {"count": 2, "sequence": [notify("r")]}},
            {"sequence": [lights]},
        )
        roots = risk_module._action_roots(config)
        self.assertEqual(len(roots), 1)
        root_path, root = roots[0]
        scanned = {path for path, _node in risk_module._action_nodes(root, root_path)}
        signatures: dict = {}
        risk_module._scope_signatures(root, root_path, "root", signatures)
        self.assertTrue(scanned)
        self.assertLessEqual(scanned, set(signatures))

    def test_a_node_without_a_signature_stays_in_scope(self):
        # Fail closed: with no change evidence for a node, a target scan
        # keeps it under review rather than dropping it.
        scope = risk_module._ChangeScope(
            proposed={}, current={}, global_fields=(), actions_changed=True
        )
        self.assertTrue(scope.in_scope("action[0]"))


if __name__ == "__main__":
    unittest.main()
