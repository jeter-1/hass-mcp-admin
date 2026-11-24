"""HAMCP-293: exact-update risk reasons name what changed; the level does not move.

Josh's middle path (2026-11-24, as relayed by the architect): the
whole-configuration risk level stays exactly as it is, with no downgrade path
of any kind. Only the reasons change: they name the changed fields and action
steps, and list a high-risk call that did not change as "present, unchanged"
rather than as the cause. Each high-risk call is named by its path and its
literal targets, so two calls to one service are reported separately as
changed, moved, removed, or unchanged.

Every plan-path assertion compares the plan with the same plan classified by
the legacy rule alone (``_change_reasons`` disabled): level, apply decision,
evidence, warnings, policy class, and acknowledgements must be equal, and the
legacy reasons must come first, unchanged. The PR #494 round-1
counterexamples (Cursor H1, H2, M1, M2, L1; Codex H1, H2, H3, M1), the round-2
counterexamples (Cursor R2-M1, R2-M2), and the I4a plan shape are reproduced
here. Configurations are synthetic.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT))

from ha_mcp_engineering.governance.models import (  # noqa: E402
    ChangeOperation,
    RiskLevel,
)
from ha_mcp_engineering.governance.physical_effects import (  # noqa: E402
    VERDICT_UNRESOLVED,
    compare_operation,
)
from ha_mcp_engineering.governance import risk as risk_module  # noqa: E402
from ha_mcp_engineering.governance.risk import (  # noqa: E402
    _legacy_classify_risk,
    classify_risk,
)
from tests.test_dev14_configuration_plans import (  # noqa: E402
    ConfigurationPlanTestCase,
)

SAFETY = "safety_critical_acknowledgement"
LOW_IMPACT = "Only low-impact metadata, logging, notification text, or minor timing changed"
PHYSICAL = "Physical-device action detected"
CHANGED = "Changed"
STEPS = "Changed action steps"
ADDED = "High-risk call added or changed"
MOVED = "High-risk call moved"
REMOVED = "High-risk call removed"
UNCHANGED = "High-risk call present, unchanged"
NEW_LABELS = (CHANGED, STEPS, ADDED, MOVED, REMOVED, UNCHANGED)

HELPER = {"action": "script.hamcp293_helper"}
PAUSE = {"action": "automation.turn_off", "target": {"entity_id": "automation.lights"}}
FANS = {"action": "automation.turn_off", "target": {"entity_id": "automation.fans"}}
UNLOCK = {"action": "lock.unlock", "target": {"entity_id": "lock.front_door"}}


def notify(message):
    return {"action": "notify.mobile_app_phone", "data": {"message": message}}


def automation(*steps, condition=None, mode="single", **extra):
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
    config.update(extra)
    return config


def script(*steps, alias="HAMCP-293 script", mode="single", **extra):
    return {"alias": alias, "mode": mode, "sequence": list(steps), **extra}


def condition(entity_id):
    return [{"condition": "state", "entity_id": entity_id, "state": "on"}]


def device_lock(kind):
    return {
        "device_id": "devlock",
        "domain": "lock",
        "type": kind,
        "entity_id": "lock.front_door",
    }


def legacy_rule():
    """Classify by the whole-configuration rule alone, as before HAMCP-293."""

    return mock.patch.object(risk_module, "_change_reasons", lambda *_args: [])


class ChangeReasonPlanTests(ConfigurationPlanTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.gateway.configs[("script", "hamcp293_helper")] = script(
            notify("helper")
        )
        self.gateway.configs[("script", "thermostat_apply_setpoints")] = script(
            notify("setpoints")
        )
        self.gateway.configs[("script", "hamcp293_spare")] = script(notify("spare"))

    async def plan(self, resource_type, target, before, after):
        self.gateway.configs[(resource_type, target)] = copy.deepcopy(before)
        created = await self.service.create_configuration_plan(
            title="HAMCP-293",
            description="Change-reason regression",
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

    async def classify(self, resource_type, target, before, after):
        """Plan once with the change reasons and once by the legacy rule.

        Asserts that everything but the appended reasons is the legacy
        result, and returns the plan, the operation, and the added reasons.
        """

        created, operation = await self.plan(resource_type, target, before, after)
        with legacy_rule():
            legacy_created, legacy = await self.plan(
                resource_type, f"{target}_legacy", before, after
            )
        self.assertEqual(operation.risk.level, legacy.risk.level)
        self.assertEqual(operation.risk.apply_allowed, legacy.risk.apply_allowed)
        self.assertEqual(operation.risk.evidence, legacy.risk.evidence)
        self.assertEqual(operation.risk.warnings, legacy.risk.warnings)
        self.assertEqual(self.policy(created), self.policy(legacy_created))
        count = len(legacy.risk.reasons)
        self.assertEqual(operation.risk.reasons[:count], legacy.risk.reasons)
        added = operation.risk.reasons[count:]
        for reason in added:
            self.assertTrue(reason.startswith(NEW_LABELS), reason)
            self.assertNotIn(PHYSICAL, reason)
        return created, operation, added

    # The I4a shape (plan e2df21db): a condition and description edit beside
    # an unchanged ``script.turn_on`` call.

    async def test_i4a_shape_reads_as_changed_parts_with_the_call_unchanged(self):
        call = {
            "action": "script.turn_on",
            "target": {"entity_id": "script.thermostat_apply_setpoints"},
        }
        before = automation(call, condition=condition("binary_sensor.window_a"))
        after = automation(
            call,
            condition=condition("binary_sensor.window_b"),
            description="HAMCP-293 regression, edited",
        )
        _created, operation, added = await self.classify(
            "automation", "a293_i4a", before, after
        )
        self.assertEqual(operation.risk.level, RiskLevel.HIGH)
        self.assertEqual(
            added,
            [
                f"{CHANGED}: conditions, description",
                f"{UNCHANGED}: script.turn_on at action[0] "
                "(script.thermostat_apply_setpoints)",
            ],
        )

    # Round-1 counterexamples: each keeps the legacy level.

    async def test_cursor_h1_fields_outside_the_allowlist_keep_the_legacy_level(self):
        cases = (
            ("automation", "a293_trigger_variables",
             automation(HELPER, trigger_variables={"x": 1}),
             automation(HELPER, trigger_variables={"x": 2}), "trigger_variables"),
            ("script", "s293_max", script(HELPER, mode="queued", max=2),
             script(HELPER, mode="queued", max=5), "max"),
            ("script", "s293_fields", script(HELPER, fields={"who": {"default": "a"}}),
             script(HELPER, fields={"who": {"default": "b"}}), "fields"),
            ("automation", "a293_initial_state", automation(HELPER, initial_state=False),
             automation(HELPER, initial_state=True), "initial_state"),
        )
        for resource_type, target, before, after, field in cases:
            with self.subTest(change=target):
                _created, _operation, added = await self.classify(
                    resource_type, target, before, after
                )
                # The level is whatever the legacy rule sets (Codex H3 names
                # the allowlist gap as pre-existing); HAMCP-293 leaves it.
                self.assertEqual(
                    added,
                    [f"{CHANGED}: {field}", f"{UNCHANGED}: script.hamcp293_helper at action[0]"],
                )

    async def test_cursor_h2_device_unlock_after_an_unchanged_call_stays_high(self):
        created, operation, added = await self.classify(
            "automation",
            "a293_device_unlock",
            automation(HELPER, device_lock("lock")),
            automation(HELPER, device_lock("unlock")),
        )
        self.assertEqual(operation.risk.level, RiskLevel.HIGH)
        self.assertNotIn(LOW_IMPACT, operation.risk.reasons)
        self.assertEqual(
            added,
            [
                f"{CHANGED}: actions",
                f"{STEPS}: action[1]",
                f"{UNCHANGED}: script.hamcp293_helper at action[0]",
            ],
        )
        self.assertIn(SAFETY, self.policy(created)[1])

    async def test_cursor_m1_call_replaced_by_a_template_names_the_removed_call(self):
        templated = {"action": "{{ which }}", "target": {"entity_id": "automation.lights"}}
        created, _operation, added = await self.classify(
            "automation", "a293_templated", automation(PAUSE), automation(templated)
        )
        self.assertEqual(
            added,
            [
                f"{CHANGED}: actions",
                f"{STEPS}: action[0]",
                f"{REMOVED}: automation.turn_off at action[0] (automation.lights)",
            ],
        )

    async def test_cursor_m2_unwalked_steps_keep_the_legacy_level(self):
        cases = (
            ("a293_bare_sequence", automation({"sequence": [PAUSE]}),
             automation({"sequence": [FANS]})),
            ("a293_then_mapping",
             automation({"if": condition("input_boolean.a"), "then": HELPER}),
             automation({"if": condition("input_boolean.b"), "then": HELPER})),
        )
        for target, before, after in cases:
            with self.subTest(change=target):
                _created, _operation, added = await self.classify(
                    "automation", target, before, after
                )
                self.assertEqual(added[:2], [f"{CHANGED}: actions", f"{STEPS}: action[0]"])

    async def test_codex_h1_trailing_delay_change_stays_high(self):
        cases = (
            ("automation", "a293_delay_single",
             automation(PAUSE, {"delay": 60}), automation(PAUSE, {"delay": 1})),
            ("automation", "a293_delay_queued",
             automation(PAUSE, {"delay": 60}, mode="queued"),
             automation(PAUSE, {"delay": 1}, mode="queued")),
            ("automation", "a293_wait_parallel",
             automation(PAUSE, {"wait_template": "{{ false }}", "timeout": 60}, mode="parallel"),
             automation(PAUSE, {"wait_template": "{{ false }}", "timeout": 1}, mode="parallel")),
            ("script", "s293_delay",
             script(PAUSE, {"delay": 60}), script(PAUSE, {"delay": 1})),
        )
        for resource_type, target, before, after in cases:
            with self.subTest(change=target):
                _created, operation, added = await self.classify(
                    resource_type, target, before, after
                )
                self.assertEqual(operation.risk.level, RiskLevel.HIGH)
                self.assertNotIn(LOW_IMPACT, operation.risk.reasons)
                self.assertEqual(
                    added[-1],
                    f"{UNCHANGED}: automation.turn_off at action[0] (automation.lights)",
                )
                self.assertTrue(
                    any(reason.startswith(f"{STEPS}: ") and "[1]" in reason for reason in added),
                    added,
                )

    async def test_codex_h2_alias_read_by_a_template_condition_stays_high(self):
        gate = [{"condition": "template",
                 "value_template": "{{ this.attributes.friendly_name == 'enabled' }}"}]
        before = automation(PAUSE, notify("a"), condition=gate, alias="disabled")
        after = automation(PAUSE, notify("b"), condition=gate, alias="enabled")
        _created, operation, added = await self.classify(
            "automation", "a293_alias_gate", before, after
        )
        self.assertEqual(operation.risk.level, RiskLevel.HIGH)
        self.assertEqual(
            added,
            [
                f"{CHANGED}: actions, alias",
                f"{STEPS}: action[1]",
                f"{UNCHANGED}: automation.turn_off at action[0] (automation.lights)",
            ],
        )

    async def test_codex_m1_unchanged_water_call_keeps_its_evidence(self):
        water = {"action": "water_heater.turn_off",
                 "target": {"entity_id": "water_heater.tank"}}
        _created, operation, added = await self.classify(
            "automation", "a293_water",
            automation(water, notify("a")), automation(water, notify("b")),
        )
        self.assertEqual(operation.risk.level, RiskLevel.HIGH)
        self.assertTrue(operation.risk.evidence)
        self.assertEqual(
            added[-1],
            f"{UNCHANGED}: water_heater.turn_off at action[0] (water_heater.tank)",
        )

    # Reason semantics on the plan path.

    async def test_unrelated_change_beside_an_unchanged_call_stays_high(self):
        _created, operation, added = await self.classify(
            "automation", "a293_unrelated",
            automation(HELPER, notify("a")), automation(HELPER, notify("b")),
        )
        self.assertEqual(operation.risk.level, RiskLevel.HIGH)
        self.assertEqual(
            added,
            [
                f"{CHANGED}: actions",
                f"{STEPS}: action[1]",
                f"{UNCHANGED}: script.hamcp293_helper at action[0]",
            ],
        )

    async def test_changed_added_removed_and_moved_calls_are_named(self):
        cases = (
            ("a293_changed", automation(HELPER, PAUSE), automation(HELPER, FANS),
             [f"{ADDED}: automation.turn_off at action[1] (automation.fans)",
              f"{UNCHANGED}: script.hamcp293_helper at action[0]"]),
            ("a293_added", automation(notify("a")), automation(notify("a"), PAUSE),
             [f"{ADDED}: automation.turn_off at action[1] (automation.lights)"]),
            ("a293_removed", automation(HELPER, PAUSE), automation(HELPER),
             [f"{REMOVED}: automation.turn_off at action[1] (automation.lights)",
              f"{UNCHANGED}: script.hamcp293_helper at action[0]"]),
            ("a293_moved", automation(HELPER, PAUSE), automation(PAUSE, HELPER),
             [f"{MOVED}: automation.turn_off at action[0] from action[1] "
              "(automation.lights), script.hamcp293_helper at action[1] from action[0]"]),
            ("a293_duplicate", automation(PAUSE), automation(PAUSE, PAUSE),
             [f"{ADDED}: automation.turn_off at action[1] (automation.lights)",
              f"{UNCHANGED}: automation.turn_off at action[0] (automation.lights)"]),
        )
        for target, before, after, calls in cases:
            with self.subTest(change=target):
                _created, _operation, added = await self.classify(
                    "automation", target, before, after
                )
                self.assertEqual(
                    [reason for reason in added if reason.startswith("High-risk call")],
                    calls,
                )

    async def test_condition_trigger_and_mode_changes_name_the_field(self):
        moved = automation(HELPER)
        moved["trigger"][0]["to"] = "off"
        cases = (
            ("a293_condition", automation(HELPER, condition=condition("input_boolean.a")),
             automation(HELPER, condition=condition("input_boolean.b")), "conditions"),
            ("a293_trigger", automation(HELPER), moved, "triggers"),
            ("a293_mode", automation(HELPER), automation(HELPER, mode="restart"), "mode"),
        )
        for target, before, after, field in cases:
            with self.subTest(change=target):
                _created, operation, added = await self.classify(
                    "automation", target, before, after
                )
                self.assertEqual(operation.risk.level, RiskLevel.HIGH)
                self.assertEqual(
                    added,
                    [f"{CHANGED}: {field}", f"{UNCHANGED}: script.hamcp293_helper at action[0]"],
                )

    async def test_nested_branch_changes_name_the_step(self):
        def branches(test_entity, else_call):
            return automation(
                {"if": condition(test_entity), "then": [HELPER], "else": [else_call]}
            )

        before = branches("input_boolean.a", PAUSE)
        _created, else_only, else_added = await self.classify(
            "automation", "a293_else", before, branches("input_boolean.a", FANS)
        )
        self.assertEqual(
            else_added,
            [
                f"{CHANGED}: actions",
                f"{STEPS}: action[0].else[0]",
                f"{ADDED}: automation.turn_off at action[0].else[0] (automation.fans)",
                f"{UNCHANGED}: script.hamcp293_helper at action[0].then[0]",
            ],
        )
        _created, test_changed, test_added = await self.classify(
            "automation", "a293_if", before, branches("input_boolean.b", PAUSE)
        )
        self.assertEqual(
            test_added,
            [
                f"{CHANGED}: actions",
                f"{STEPS}: action[0]",
                f"{UNCHANGED}: script.hamcp293_helper at action[0].then[0], "
                "automation.turn_off at action[0].else[0] (automation.lights)",
            ],
        )
        for operation in (else_only, test_changed):
            self.assertEqual(operation.risk.level, RiskLevel.HIGH)

    async def test_unlock_floor_and_reasons_are_kept(self):
        created, operation, added = await self.classify(
            "automation", "a293_unlock",
            automation(UNLOCK, notify("a")), automation(UNLOCK, notify("b")),
        )
        self.assertEqual(
            added[-1], f"{UNCHANGED}: lock.unlock at action[0] (lock.front_door)"
        )
        self.assertEqual(self.policy(created)[1], ["plan_approval", SAFETY])
        (row,) = compare_operation(operation).projection["rows"]
        self.assertIn("unlock_or_disarm_effect", row["reason_codes"])

    async def test_unresolved_comparison_keeps_the_legacy_level(self):
        missing = {"action": "script.hamcp293_missing"}
        created, operation, added = await self.classify(
            "automation", "a293_unresolved",
            automation(missing, notify("a")), automation(missing, notify("b")),
        )
        self.assertEqual(compare_operation(operation).verdict, VERDICT_UNRESOLVED)
        self.assertEqual(operation.risk.level, RiskLevel.HIGH)
        self.assertEqual(added[-1], f"{UNCHANGED}: script.hamcp293_missing at action[0]")
        self.assertIn(SAFETY, self.policy(created)[1])

    async def test_script_reasons_keep_dotted_call_names(self):
        trigger = {"action": "automation.trigger", "target": {"entity_id": "automation.lights"}}
        _created, operation, added = await self.classify(
            "script", "s293_trigger", script(notify("a")), script(notify("a"), trigger)
        )
        self.assertIn(
            f"{ADDED}: automation.trigger at action[1] (automation.lights)", added
        )
        self.assertFalse(
            any("script.trigger" in reason for reason in operation.risk.reasons),
            operation.risk.reasons,
        )

    # Round-2 counterexamples: two calls to one service, and a second root.

    async def test_cursor_r2_m1_two_script_turn_on_calls_are_told_apart(self):
        def turn_on(script_id):
            return {"action": "script.turn_on", "target": {"entity_id": f"script.{script_id}"}}

        _created, operation, added = await self.classify(
            "automation", "a293_two_turn_on",
            automation(turn_on("hamcp293_helper"), turn_on("thermostat_apply_setpoints")),
            automation(turn_on("hamcp293_helper"), turn_on("hamcp293_spare")),
        )
        self.assertEqual(operation.risk.level, RiskLevel.HIGH)
        self.assertEqual(
            added,
            [
                f"{CHANGED}: actions",
                f"{STEPS}: action[1]",
                f"{ADDED}: script.turn_on at action[1] (script.hamcp293_spare)",
                f"{UNCHANGED}: script.turn_on at action[0] (script.hamcp293_helper)",
            ],
        )

    async def test_cursor_r2_m1_removing_one_of_two_unlocks_is_named(self):
        back = {"action": "lock.unlock", "target": {"entity_id": "lock.back_door"}}
        cases = (
            ("a293_unlock_second", automation(UNLOCK, back), automation(UNLOCK),
             [f"{REMOVED}: lock.unlock at action[1] (lock.back_door)",
              f"{UNCHANGED}: lock.unlock at action[0] (lock.front_door)"]),
            ("a293_unlock_first", automation(UNLOCK, back), automation(back),
             [f"{MOVED}: lock.unlock at action[0] from action[1] (lock.back_door)",
              f"{REMOVED}: lock.unlock at action[0] (lock.front_door)"]),
        )
        for target, before, after, calls in cases:
            with self.subTest(change=target):
                created, _operation, added = await self.classify(
                    "automation", target, before, after
                )
                self.assertEqual(
                    [reason for reason in added if reason.startswith("High-risk call")],
                    calls,
                )
                self.assertIn(SAFETY, self.policy(created)[1])

    async def test_cursor_r2_m2_a_call_under_a_second_root_reads_as_added(self):
        # The diff reports the second root as ``other:actions`` (or nothing,
        # when ``actions`` comes first); either way the call is compared.
        def both_roots(actions_first):
            config = automation(HELPER)
            action = config.pop("action")
            if actions_first:
                config["actions"] = [PAUSE]
                config["action"] = action
            else:
                config["action"] = action
                config["actions"] = [PAUSE]
            return config

        for actions_first in (False, True):
            with self.subTest(actions_first=actions_first):
                _created, _operation, added = await self.classify(
                    "automation", f"a293_two_roots_{int(actions_first)}",
                    automation(HELPER), both_roots(actions_first),
                )
                self.assertEqual(
                    [reason for reason in added if reason.startswith("High-risk call")],
                    [
                        f"{ADDED}: automation.turn_off at actions[0] (automation.lights)",
                        f"{UNCHANGED}: script.hamcp293_helper at action[0]",
                    ],
                )

    async def test_plan_reasons_carry_the_operation_prefix(self):
        created, operation, added = await self.classify(
            "automation", "a293_prefix",
            automation(HELPER, notify("a")), automation(HELPER, notify("b")),
        )
        plan = self.repository.get(created["plan_id"])
        self.assertTrue(added)
        for reason in added:
            self.assertIn(f"op: {reason}", plan.risk.reasons)
        self.assertEqual(plan.risk.level, operation.risk.level)

    async def test_stored_plan_hash_does_not_rederive_risk(self):
        # A plan stored before an upgrade keeps its hash: re-hashing reads the
        # stored risk and never calls the classifier again.
        created, _operation = await self.plan(
            "automation", "a293_stored",
            automation(HELPER, notify("a")), automation(HELPER, notify("b")),
        )
        expected = self.service.plan_hash(self.repository.get(created["plan_id"]))
        with mock.patch.object(
            risk_module, "_change_reasons", side_effect=AssertionError("re-derived")
        ), mock.patch.object(
            risk_module, "_legacy_classify_risk", side_effect=AssertionError("re-derived")
        ):
            self.assertEqual(
                self.service.plan_hash(self.repository.get(created["plan_id"])),
                expected,
            )


class DirectClassifierTests(unittest.TestCase):
    """``classify_risk`` against ``_legacy_classify_risk`` on the same inputs."""

    DIFF = {"changed_fields": [{"field": "actions", "change_type": "modified"}]}

    def assert_legacy(self, operation, diff, proposed, **kwargs):
        risk = classify_risk(operation, diff, proposed, **kwargs)
        legacy = _legacy_classify_risk(operation, diff, proposed, **kwargs)
        self.assertEqual(risk.level, legacy.level)
        self.assertEqual(risk.apply_allowed, legacy.apply_allowed)
        self.assertEqual(risk.evidence, legacy.evidence)
        self.assertEqual(risk.warnings, legacy.warnings)
        self.assertEqual(risk.reasons[: len(legacy.reasons)], legacy.reasons)
        return risk, legacy

    def test_update_without_current_is_the_legacy_result(self):
        risk, legacy = self.assert_legacy(
            ChangeOperation.UPDATE_AUTOMATION, self.DIFF, automation(HELPER, notify("b"))
        )
        self.assertEqual(risk.reasons, legacy.reasons)

    def test_create_is_the_legacy_result(self):
        risk, legacy = self.assert_legacy(
            ChangeOperation.CREATE_AUTOMATION, {"changed_fields": []},
            automation(HELPER), current=None,
        )
        self.assertEqual(risk.reasons, legacy.reasons)

    def test_unresolved_verdict_keeps_the_legacy_level(self):
        risk, _legacy = self.assert_legacy(
            ChangeOperation.UPDATE_AUTOMATION, self.DIFF, automation(HELPER, notify("b")),
            current=automation(HELPER, notify("a")), protected_effect_verdict=VERDICT_UNRESOLVED,
        )
        self.assertEqual(risk.level, RiskLevel.HIGH)
        self.assertIn(f"{UNCHANGED}: script.hamcp293_helper at action[0]", risk.reasons)

    def test_cursor_l1_sequence_only_diff_keeps_the_legacy_level(self):
        risk, _legacy = self.assert_legacy(
            ChangeOperation.UPDATE_AUTOMATION,
            {"changed_fields": [{"field": "sequence", "change_type": "modified"}]},
            script(PAUSE), current=script(notify("a")),
        )
        self.assertIn(f"{CHANGED}: sequence", risk.reasons)

    def test_both_action_roots_keep_the_legacy_level(self):
        after = automation(HELPER, notify("b"))
        after["actions"] = [notify("c")]
        self.assert_legacy(
            ChangeOperation.UPDATE_AUTOMATION, self.DIFF, after,
            current=automation(HELPER, notify("a")),
        )

    def test_action_and_actions_keys_compare_by_the_same_paths(self):
        before = automation(HELPER, PAUSE)
        after = automation(HELPER, PAUSE)
        after["actions"] = after.pop("action")
        risk, _legacy = self.assert_legacy(
            ChangeOperation.UPDATE_AUTOMATION, self.DIFF, after, current=before
        )
        self.assertIn(
            f"{UNCHANGED}: script.hamcp293_helper at actions[0], "
            "automation.turn_off at actions[1] (automation.lights)",
            risk.reasons,
        )
        self.assertFalse(any(reason.startswith(STEPS) for reason in risk.reasons))

    def test_other_prefixed_action_field_is_compared(self):
        risk, _legacy = self.assert_legacy(
            ChangeOperation.UPDATE_AUTOMATION,
            {"changed_fields": [{"field": "other:actions", "change_type": "modified"}]},
            automation(HELPER, PAUSE), current=automation(HELPER),
        )
        self.assertIn(
            f"{ADDED}: automation.turn_off at action[1] (automation.lights)", risk.reasons
        )

    def test_a_second_action_root_is_compared(self):
        before = automation(HELPER)
        after = automation(HELPER)
        after["actions"] = [PAUSE]
        for diff in (
            {"changed_fields": [{"field": "other:actions", "change_type": "added"}]},
            {"changed_fields": []},
        ):
            with self.subTest(diff=diff):
                risk, _legacy = self.assert_legacy(
                    ChangeOperation.UPDATE_AUTOMATION, diff, after, current=before
                )
                self.assertIn(
                    f"{ADDED}: automation.turn_off at actions[0] (automation.lights)",
                    risk.reasons,
                )
                self.assertFalse(
                    any(
                        reason.startswith(UNCHANGED) and "automation.turn_off" in reason
                        for reason in risk.reasons
                    ),
                    risk.reasons,
                )

    def test_shown_targets_are_bounded_plain_identifiers(self):
        def unlock(entity_id):
            return {"action": "lock.unlock", "target": {"entity_id": entity_id}}

        cases = (
            (unlock([f"lock.door_{letter}" for letter in "abcde"]),
             "lock.unlock at action[0] (lock.door_a, lock.door_b, lock.door_c, +2)"),
            (unlock(["{{ which }}", "Lock.Front_Door", "lock." + "x" * 64,
                     "Physical-device action detected"]),
             "lock.unlock at action[0] (lock.front_door)"),
            (unlock("{{ which }}"), "lock.unlock at action[0]"),
        )
        for call, shown in cases:
            with self.subTest(shown=shown):
                risk, legacy = self.assert_legacy(
                    ChangeOperation.UPDATE_AUTOMATION, self.DIFF, automation(call),
                    current=automation(notify("a")),
                )
                self.assertIn(f"{ADDED}: {shown}", risk.reasons)
                self.assertEqual(
                    sum(PHYSICAL in reason for reason in risk.reasons),
                    sum(PHYSICAL in reason for reason in legacy.reasons),
                )

    def test_name_lists_are_bounded(self):
        calls = [{"action": f"script.hamcp293_{index:02d}"} for index in range(11)]
        risk, _legacy = self.assert_legacy(
            ChangeOperation.UPDATE_AUTOMATION, self.DIFF, automation(*calls),
            current=automation(notify("a")),
        )
        (line,) = [reason for reason in risk.reasons if reason.startswith(ADDED)]
        self.assertTrue(line.endswith(", and 3 more"), line)
        self.assertEqual(line.count("script.hamcp293_"), 8)

    def test_reasons_are_deterministic(self):
        args = (ChangeOperation.UPDATE_AUTOMATION, self.DIFF, automation(PAUSE, HELPER))
        first = classify_risk(*args, current=automation(HELPER, PAUSE)).reasons
        second = classify_risk(*args, current=automation(HELPER, PAUSE)).reasons
        self.assertEqual(first, second)

    def test_a_failed_comparison_keeps_the_legacy_result_and_the_field_line(self):
        for error in (TypeError, ValueError, RecursionError, OverflowError):
            with self.subTest(error=error.__name__), mock.patch.object(
                risk_module, "_change_calls", side_effect=error("probe")
            ):
                risk, legacy = self.assert_legacy(
                    ChangeOperation.UPDATE_AUTOMATION, self.DIFF,
                    automation(HELPER, notify("b")), current=automation(HELPER, notify("a")),
                )
                self.assertEqual(risk.reasons, [*legacy.reasons, f"{CHANGED}: actions"])

    def test_no_new_reason_reads_as_a_physical_device_action(self):
        light = {"action": "light.turn_on", "target": {"entity_id": "light.a"}}
        risk, legacy = self.assert_legacy(
            ChangeOperation.UPDATE_AUTOMATION, self.DIFF, automation(notify("a")),
            current=automation(light),
        )
        self.assertEqual(
            sum(PHYSICAL in reason for reason in risk.reasons),
            sum(PHYSICAL in reason for reason in legacy.reasons),
        )


if __name__ == "__main__":
    unittest.main()
