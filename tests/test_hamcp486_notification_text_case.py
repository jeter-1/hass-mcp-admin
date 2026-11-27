"""HAMCP-486: the first proven lowering case for an exact update.

Release 2.11.0 Lane C GO (2026-11-27, as relayed by the architect): the safe
set is ``persistent_notification.create`` and
``notify.persistent_notification`` only. An exact update whose only
difference is the literal message or title of such a call is classified as
an alias-only edit of the same configuration. ``notify.mobile_app_*`` and
other notify platforms stay at the legacy level, because their message can
select a device command.

Every refusal case is compared with the same plan classified with the
HAMCP-486 case disabled: level, apply decision, evidence, warnings, policy
class, and acknowledgements must be equal. The PR #494 round-1
counterexamples that apply to an exact update are reproduced. Configurations
are synthetic.
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
    VERDICT_NONE,
    VERDICT_UNRESOLVED,
    compare_operation,
)
from ha_mcp_engineering.governance import risk as risk_module  # noqa: E402
from ha_mcp_engineering.governance.risk import classify_risk  # noqa: E402
from tests.test_dev14_configuration_plans import (  # noqa: E402
    ConfigurationPlanTestCase,
)
from tests.test_hamcp293_change_scoped_risk import (  # noqa: E402
    FANS,
    HELPER,
    PAUSE,
    UNCHANGED,
    UNLOCK,
    automation,
    condition,
    device_lock,
    notify,
    script,
)

SAFETY = "safety_critical_acknowledgement"
LOW_IMPACT = "Only low-impact metadata, logging, notification text, or minor timing changed"
CASE = risk_module._NOTIFICATION_TEXT_REASON


def card(message, title=None, *, service="persistent_notification.create", **data):
    payload = {"message": message, **data}
    if title is not None:
        payload["title"] = title
    return {"action": service, "data": payload}


def pn_notify(message, title=None):
    return card(message, title, service="notify.persistent_notification")


def case_disabled():
    return mock.patch.object(
        risk_module, "_notification_text_only", lambda *_args: False
    )


class NotificationTextPlanTests(ConfigurationPlanTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.gateway.configs[("script", "hamcp293_helper")] = script(card("helper"))
        self.gateway.configs[("script", "hamcp486_unlocker")] = script(UNLOCK)

    async def plan(self, resource_type, target, before, after):
        self.gateway.configs[(resource_type, target)] = copy.deepcopy(before)
        created = await self.service.create_configuration_plan(
            title="HAMCP-486",
            description="Notification-text regression",
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

    async def lowered(self, resource_type, target, before, after):
        created, operation = await self.plan(resource_type, target, before, after)
        self.assertEqual(compare_operation(operation).verdict, VERDICT_NONE)
        self.assertEqual(operation.risk.level, RiskLevel.LOW)
        self.assertTrue(operation.risk.apply_allowed)
        self.assertEqual(operation.risk.evidence, [])
        self.assertEqual(operation.risk.warnings, [])
        self.assertEqual(operation.risk.reasons[:2], [LOW_IMPACT, CASE])
        self.assertEqual(self.policy(created), ("standard_admin", ["plan_approval"]))
        for reason in operation.risk.reasons:
            self.assertNotIn("Physical-device action detected", reason)
        return created, operation

    async def unchanged(self, resource_type, target, before, after):
        """The plan equals the plan classified with HAMCP-486 disabled."""

        created, operation = await self.plan(resource_type, target, before, after)
        with case_disabled():
            legacy_created, legacy = await self.plan(
                resource_type, f"{target}_legacy", before, after
            )
        self.assertEqual(operation.risk.level, legacy.risk.level)
        self.assertEqual(operation.risk.apply_allowed, legacy.risk.apply_allowed)
        self.assertEqual(operation.risk.evidence, legacy.risk.evidence)
        self.assertEqual(operation.risk.warnings, legacy.risk.warnings)
        self.assertEqual(operation.risk.reasons, legacy.risk.reasons)
        self.assertEqual(self.policy(created), self.policy(legacy_created))
        self.assertNotIn(CASE, operation.risk.reasons)
        return created, operation

    # Useful authorized success: the case lowers.

    async def test_text_beside_an_unchanged_high_call_is_an_alias_only_edit(self):
        _created, operation = await self.lowered(
            "automation", "a486_pause",
            automation(PAUSE, card("a")), automation(PAUSE, card("b")),
        )
        # The HAMCP-293 reasons still name what changed.
        self.assertEqual(
            operation.risk.reasons[2:],
            [
                "Changed: actions",
                "Changed action steps: action[1]",
                f"{UNCHANGED}: automation.turn_off at action[0] (automation.lights)",
            ],
        )

    async def test_each_safe_service_and_field_lowers(self):
        cases = (
            ("a486_title", automation(PAUSE, card("m", "a")), automation(PAUSE, card("m", "b"))),
            ("a486_notify_pn", automation(PAUSE, pn_notify("a")), automation(PAUSE, pn_notify("b"))),
            ("a486_notify_pn_title",
             automation(PAUSE, pn_notify("m", "a")), automation(PAUSE, pn_notify("m", "b"))),
            ("a486_service_key",
             automation(PAUSE, {"service": "persistent_notification.create", "data": {"message": "a"}}),
             automation(PAUSE, {"service": "persistent_notification.create", "data": {"message": "b"}})),
            ("a486_multiline",
             automation(PAUSE, card("line one")), automation(PAUSE, card("line one\n\tline two"))),
            ("a486_both_cards",
             automation(card("a"), PAUSE, pn_notify("c")),
             automation(card("b"), PAUSE, pn_notify("d"))),
            ("a486_step_options",
             automation(PAUSE, {**card("a", notification_id="n1"), "alias": "Tell",
                                "enabled": True, "continue_on_error": True}),
             automation(PAUSE, {**card("b", notification_id="n1"), "alias": "Tell",
                                "enabled": True, "continue_on_error": True})),
        )
        for target, before, after in cases:
            with self.subTest(change=target):
                await self.lowered("automation", target, before, after)

    async def test_nested_branches_lower(self):
        def nested(text):
            return automation(
                PAUSE,
                {"choose": [{"conditions": condition("input_boolean.a"),
                             "sequence": [card(text)]}],
                 "default": [card("default")]},
                {"if": condition("input_boolean.b"), "then": [card(text)], "else": [FANS]},
                {"repeat": {"count": 2, "sequence": [card(text)]}},
                {"parallel": [{"sequence": [card(text)]}, card(text)]},
            )

        await self.lowered("automation", "a486_nested", nested("a"), nested("b"))

    async def test_script_text_lowers(self):
        _created, operation = await self.lowered(
            "script", "s486_helper",
            script(HELPER, card("a")), script(HELPER, card("b")),
        )
        self.assertIn(f"{UNCHANGED}: script.hamcp293_helper at action[0]", operation.risk.reasons)
        self.assertFalse(
            any(" automation " in f" {reason} " for reason in operation.risk.reasons[:2])
        )

    async def test_a_medium_text_change_lowers(self):
        light = {"action": "light.turn_on", "target": {"entity_id": "light.porch"}}
        await self.lowered(
            "automation", "a486_light", automation(light, card("a")), automation(light, card("b"))
        )

    # Refusal: every other difference keeps the legacy result.

    async def test_other_differences_keep_the_legacy_result(self):
        light = {"action": "light.turn_on", "target": {"entity_id": "light.porch"}}
        cases = (
            # mobile_app and unknown notify platforms stay out (GO).
            ("a486_mobile_app", automation(PAUSE, notify("a")), automation(PAUSE, notify("b"))),
            ("a486_unknown_notify",
             automation(PAUSE, card("a", service="notify.custom_sink")),
             automation(PAUSE, card("b", service="notify.custom_sink"))),
            ("a486_other_service",
             automation(PAUSE, card("a", service="persistent_notification.dismiss")),
             automation(PAUSE, card("b", service="persistent_notification.dismiss"))),
            # Codex H1: a trailing delay change.
            ("a486_delay",
             automation(PAUSE, card("a"), {"delay": 60}), automation(PAUSE, card("b"), {"delay": 1})),
            # A changed call beside the text.
            ("a486_call_changed", automation(PAUSE, card("a")), automation(FANS, card("b"))),
            # Cursor M1: the call replaced by a template.
            ("a486_templated_call",
             automation(PAUSE, card("a")),
             automation({"action": "{{ which }}", "target": {"entity_id": "automation.lights"}},
                        card("b"))),
            # Templates in either text, in any of the three markers.
            ("a486_template_after", automation(PAUSE, card("a")), automation(PAUSE, card("{{ x }}"))),
            ("a486_template_before", automation(PAUSE, card("{% if x %}y{% endif %}")),
             automation(PAUSE, card("b"))),
            ("a486_comment", automation(PAUSE, card("a")), automation(PAUSE, card("b {# c #}"))),
            # Key presence and type.
            ("a486_title_added", automation(PAUSE, card("m")), automation(PAUSE, card("m", "t"))),
            ("a486_title_removed", automation(PAUSE, card("m", "t")), automation(PAUSE, card("m"))),
            ("a486_message_removed",
             automation(PAUSE, card("m", "t")),
             automation(PAUSE, {"action": "persistent_notification.create", "data": {"title": "t"}})),
            ("a486_title_only_without_message",
             automation(PAUSE, {"action": "persistent_notification.create", "data": {"title": "a"}}),
             automation(PAUSE, {"action": "persistent_notification.create", "data": {"title": "b"}})),
            ("a486_message_not_text", automation(PAUSE, card("a")), automation(PAUSE, card(["b"]))),
            ("a486_message_number", automation(PAUSE, card(1)), automation(PAUSE, card(2))),
            ("a486_data_template",
             automation(PAUSE, {"action": "persistent_notification.create",
                                "data_template": {"message": "a"}}),
             automation(PAUSE, {"action": "persistent_notification.create",
                                "data_template": {"message": "b"}})),
            ("a486_notification_id", automation(PAUSE, card("m", notification_id="a")),
             automation(PAUSE, card("m", notification_id="b"))),
            ("a486_control_char", automation(PAUSE, card("a")), automation(PAUSE, card("b\x07"))),
            ("a486_too_long", automation(PAUSE, card("a")), automation(PAUSE, card("é" * 2049))),
            # A step that Home Assistant dispatches as another step type.
            ("a486_device_step",
             automation(PAUSE, {**card("a"), "device_id": "dev1"}),
             automation(PAUSE, {**card("b"), "device_id": "dev1"})),
            ("a486_response_variable",
             automation(PAUSE, {**card("a"), "response_variable": "r"}),
             automation(PAUSE, {**card("b"), "response_variable": "r"})),
            # Cursor M2: unwalked steps.
            ("a486_bare_sequence",
             automation(PAUSE, {"sequence": [card("a")]}), automation(PAUSE, {"sequence": [card("b")]})),
            ("a486_then_mapping",
             automation(PAUSE, {"if": condition("input_boolean.a"), "then": card("a")}),
             automation(PAUSE, {"if": condition("input_boolean.a"), "then": card("b")})),
            ("a486_variables_lookalike",
             automation(PAUSE, {"variables": {"step": card("a")}}),
             automation(PAUSE, {"variables": {"step": card("b")}})),
            # A persistent_notification trigger can read the text.
            ("a486_pn_trigger",
             {**automation(PAUSE, card("a")),
              "trigger": [{"platform": "persistent_notification", "update_type": ["added"]}]},
             {**automation(PAUSE, card("b")),
              "trigger": [{"platform": "persistent_notification", "update_type": ["added"]}]}),
            ("a486_pn_trigger_new_key",
             {**automation(PAUSE, card("a")), "trigger": [{"trigger": "persistent_notification"}]},
             {**automation(PAUSE, card("b")), "trigger": [{"trigger": "persistent_notification"}]}),
            ("a486_pn_wait",
             automation(PAUSE, card("a"),
                        {"wait_for_trigger": [{"platform": "persistent_notification"}]}),
             automation(PAUSE, card("b"),
                        {"wait_for_trigger": [{"platform": "persistent_notification"}]})),
            # Codex M1: a protected water target stays.
            ("a486_water",
             automation({"action": "water_heater.turn_off",
                         "target": {"entity_id": "water_heater.tank"}}, card("a")),
             automation({"action": "water_heater.turn_off",
                         "target": {"entity_id": "water_heater.tank"}}, card("b"))),
            # No change to the text at all: the light change is MEDIUM.
            ("a486_no_text_change", automation(light, card("a")),
             automation({**light, "data": {"brightness": 10}}, card("a"))),
        )
        for target, before, after in cases:
            with self.subTest(change=target):
                _created, operation = await self.unchanged("automation", target, before, after)
                self.assertNotEqual(operation.risk.reasons[:1], [LOW_IMPACT], target)

    async def test_script_fields_outside_the_actions_keep_the_legacy_result(self):
        # Cursor H1: script max and fields, and an automation initial_state.
        cases = (
            ("script", "s486_max", script(HELPER, card("a"), mode="queued", max=2),
             script(HELPER, card("b"), mode="queued", max=5)),
            ("script", "s486_fields", script(HELPER, card("a"), fields={"who": {"default": "a"}}),
             script(HELPER, card("b"), fields={"who": {"default": "b"}})),
            ("automation", "a486_initial_state", automation(PAUSE, card("a"), initial_state=False),
             automation(PAUSE, card("b"), initial_state=True)),
            ("automation", "a486_trigger_variables",
             automation(PAUSE, card("a"), trigger_variables={"x": 1}),
             automation(PAUSE, card("b"), trigger_variables={"x": 2})),
            # Codex H2: the alias changes beside the text.
            ("automation", "a486_alias",
             automation(PAUSE, card("a"), alias="disabled"),
             automation(PAUSE, card("b"), alias="enabled")),
        )
        for resource_type, target, before, after in cases:
            with self.subTest(change=target):
                await self.unchanged(resource_type, target, before, after)

    async def test_safety_critical_calls_keep_the_legacy_result(self):
        cases = (
            ("a486_unlock", automation(UNLOCK, card("a")), automation(UNLOCK, card("b"))),
            # Cursor H2: an unchanged device unlock beside the text.
            ("a486_device_unlock",
             automation(device_lock("unlock"), card("a")),
             automation(device_lock("unlock"), card("b"))),
            ("a486_device_lock_to_unlock",
             automation(device_lock("lock"), card("a")),
             automation(device_lock("unlock"), card("b"))),
            # Cursor Q5: a called script holds the unlock.
            ("a486_called_unlock",
             automation({"action": "script.hamcp486_unlocker"}, card("a")),
             automation({"action": "script.hamcp486_unlocker"}, card("b"))),
            ("a486_unresolved",
             automation({"action": "script.hamcp486_missing"}, card("a")),
             automation({"action": "script.hamcp486_missing"}, card("b"))),
        )
        for target, before, after in cases:
            with self.subTest(change=target):
                # The level is the legacy one (an equivalent protected effect
                # does not raise it by itself, HAMCP-213 D4.5); the safety
                # floor comes from the comparison and the policy.
                created, operation = await self.unchanged("automation", target, before, after)
                self.assertNotEqual(compare_operation(operation).verdict, VERDICT_NONE)
                if target != "a486_unresolved":
                    self.assertIn(SAFETY, self.policy(created)[1])
                else:
                    self.assertEqual(compare_operation(operation).verdict, VERDICT_UNRESOLVED)

    async def test_a_blueprint_keeps_the_legacy_result(self):
        def blueprint(text):
            return {
                "alias": "HAMCP-486 blueprint",
                "use_blueprint": {"path": "hamcp486/x.yaml", "input": {"note": "n"}},
                "action": [PAUSE, card(text)],
            }

        await self.unchanged("automation", "a486_blueprint", blueprint("a"), blueprint("b"))

    async def test_a_plan_stored_before_the_change_keeps_its_risk(self):
        # A plan created by the legacy rule keeps its stored HIGH risk and
        # policy: risk is never re-derived after the plan is stored.
        with case_disabled():
            created, _operation = await self.plan(
                "automation", "a486_stored",
                automation(PAUSE, card("a")), automation(PAUSE, card("b")),
            )
        stored = self.repository.get(created["plan_id"])
        self.assertEqual(stored.operations[0].risk.level, RiskLevel.HIGH)
        expected_hash = self.service.plan_hash(stored)
        with mock.patch.object(
            risk_module, "_notification_text_only", side_effect=AssertionError("re-derived")
        ):
            reread = self.repository.get(created["plan_id"])
            self.assertEqual(self.service.plan_hash(reread), expected_hash)
            self.assertEqual(reread.operations[0].risk.level, RiskLevel.HIGH)
        self.assertNotEqual(self.policy(created)[0], "standard_admin")


class DirectClassifierTests(unittest.TestCase):
    DIFF = {"changed_fields": [{"field": "actions", "change_type": "modified"}]}

    def classify(self, proposed, current, *, verdict=VERDICT_NONE, diff=None, operation=None):
        return classify_risk(
            operation or ChangeOperation.UPDATE_AUTOMATION,
            self.DIFF if diff is None else diff,
            proposed,
            current=current,
            protected_effect_verdict=verdict,
        )

    def test_the_case_needs_the_none_verdict(self):
        before, after = automation(PAUSE, card("a")), automation(PAUSE, card("b"))
        self.assertEqual(self.classify(after, before).level, RiskLevel.LOW)
        for verdict in ("equivalent", "changed", "unresolved", None):
            with self.subTest(verdict=verdict):
                self.assertEqual(
                    self.classify(after, before, verdict=verdict).level, RiskLevel.HIGH
                )

    def test_the_case_needs_an_exact_update_with_current(self):
        before, after = automation(PAUSE, card("a")), automation(PAUSE, card("b"))
        self.assertEqual(
            classify_risk(ChangeOperation.UPDATE_AUTOMATION, self.DIFF, after,
                          protected_effect_verdict=VERDICT_NONE).level,
            RiskLevel.HIGH,
        )
        self.assertEqual(
            self.classify(after, None, operation=ChangeOperation.CREATE_AUTOMATION,
                          diff={"changed_fields": []}).level,
            RiskLevel.HIGH,
        )

    def test_the_diff_fields_must_be_action_fields(self):
        before, after = automation(PAUSE, card("a")), automation(PAUSE, card("b"))
        for fields in ([], ["actions", "description"], ["triggers"]):
            with self.subTest(fields=fields):
                diff = {"changed_fields": [{"field": f, "change_type": "modified"} for f in fields]}
                self.assertNotEqual(self.classify(after, before, diff=diff).reasons[1:2], [CASE])

    def test_a_script_view_compares_its_sequence_once(self):
        def view(text):
            config = script(PAUSE, card(text))
            config["action"] = config["sequence"]
            return config

        risk = self.classify(
            view("b"), view("a"),
            diff={"changed_fields": [{"field": "sequence", "change_type": "modified"},
                                     {"field": "actions", "change_type": "modified"}]},
        )
        self.assertEqual(risk.level, RiskLevel.LOW)
        # A sequence that is not the action root is compared as it is.
        unaliased_before, unaliased_after = view("a"), view("b")
        unaliased_after["sequence"] = copy.deepcopy(unaliased_after["sequence"])
        unaliased_after["sequence"][1]["data"]["message"] = "c"
        self.assertEqual(
            self.classify(unaliased_after, unaliased_before).level, RiskLevel.HIGH
        )

    def test_non_json_values_keep_the_legacy_result(self):
        before = automation(PAUSE, card("a"))
        after = automation(PAUSE, card("b"))
        after["action"][1]["data"]["extra"] = {1: "x"}
        before["action"][1]["data"]["extra"] = {1: "x"}
        self.assertEqual(self.classify(after, before).level, RiskLevel.HIGH)
        after["action"][1]["data"]["extra"] = object()
        before["action"][1]["data"]["extra"] = after["action"][1]["data"]["extra"]
        self.assertEqual(self.classify(after, before).level, RiskLevel.HIGH)

    def test_the_text_bound_is_in_utf8_bytes(self):
        before = automation(PAUSE, card("a"))
        self.assertEqual(
            self.classify(automation(PAUSE, card("x" * 4096)), before).level, RiskLevel.LOW
        )
        self.assertEqual(
            self.classify(automation(PAUSE, card("é" * 2048)), before).level, RiskLevel.LOW
        )
        self.assertEqual(
            self.classify(automation(PAUSE, card("x" * 4097)), before).level, RiskLevel.HIGH
        )

    def assert_legacy(self, proposed, current, *, diff=None):
        risk = self.classify(proposed, current, diff=diff)
        with case_disabled():
            legacy = self.classify(proposed, current, diff=diff)
        self.assertNotIn(CASE, risk.reasons)
        self.assertEqual(
            (risk.level, risk.reasons, risk.evidence, risk.warnings),
            (legacy.level, legacy.reasons, legacy.evidence, legacy.warnings),
        )
        return risk

    def test_warnings_keep_the_legacy_result(self):
        dynamic = {"action": "automation.turn_off", "target": {"entity_id": "{{ t }}"}}
        risk = self.assert_legacy(
            automation(dynamic, card("b")), automation(dynamic, card("a"))
        )
        self.assertNotEqual(risk.level, RiskLevel.LOW)
        self.assertTrue(risk.warnings)

    def test_protected_high_services_keep_the_legacy_result(self):
        # The call reaches no protected entity, so only the high-risk service's
        # protected prefix marks it protected.
        for step in (
            {"action": "lock.lock", "target": {"device_id": "synthetic_device"}},
            {"action": "alarm_control_panel.alarm_arm_away",
             "target": {"device_id": "synthetic_device"}},
        ):
            with self.subTest(service=step["action"]):
                risk = self.assert_legacy(
                    automation(step, card("b")), automation(step, card("a"))
                )
                self.assertNotEqual(risk.level, RiskLevel.LOW)

    def test_a_text_must_actually_change(self):
        config = automation(PAUSE, card("a"))
        risk = self.assert_legacy(config, copy.deepcopy(config))
        self.assertEqual(risk.level, RiskLevel.HIGH)

    def test_values_json_would_coerce_keep_the_legacy_result(self):
        # json.dumps writes a tuple as a list, so only the plain-JSON copy
        # refuses it.
        before = automation(PAUSE, card("a"))
        after = automation(PAUSE, card("b"))
        after["action"][1]["data"]["extra"] = ("x",)
        before["action"][1]["data"]["extra"] = ("x",)
        self.assertEqual(self.assert_legacy(after, before).level, RiskLevel.HIGH)


if __name__ == "__main__":
    unittest.main()
