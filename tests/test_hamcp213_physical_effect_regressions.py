"""HAMCP-213 D10: E013 regressions and the D2 comparison rules.

One test per E013 symptom (design section 1.3), the OD-6(b) unlock floor,
the unresolved-target row, and one direct test per D2 rule (abort point,
mode, run occupancy). Each rule test fails when its rule is removed.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT))

from ha_mcp_engineering.approval_web import _render_review  # noqa: E402
from ha_mcp_engineering.governance.physical_effects import (  # noqa: E402
    VERDICT_CHANGED,
    VERDICT_EQUIVALENT,
    VERDICT_NONE,
    VERDICT_UNRESOLVED,
    compare_operation,
    compare_physical_effects,
)
from tests.test_dev14_configuration_plans import (  # noqa: E402
    ConfigurationPlanTestCase,
)

PRINCIPAL = "home_assistant_admin_ingress:dev14-reviewer"
SAFETY = "safety_critical_acknowledgement"
RECIPIENT_REASON = "Recipient or environmental-control behavior may change"
RETRY_WARNING = "No complete retry-transformation proof"

CLOSE = {"action": "cover.close_cover", "target": {"entity_id": "cover.garage_main"}}


def notify(message, service="notify.mobile_app_phone", **extra):
    return {"action": service, "data": {"message": message}, **extra}


def logbook(message, entity_id="cover.garage_main"):
    return {
        "action": "logbook.log",
        "data": {"name": "Garage", "message": message, "entity_id": entity_id},
    }


def script(*steps, mode="single", alias="Regression script"):
    return {"alias": alias, "mode": mode, "sequence": list(steps)}


def automation(*steps, alias="Regression automation"):
    return {
        "alias": alias,
        "description": "HAMCP-213 regression",
        "mode": "single",
        "trigger": [
            {"platform": "state", "entity_id": "binary_sensor.door", "to": "on"}
        ],
        "condition": [],
        "action": list(steps),
    }


def triggers(operation):
    return {item.get("trigger") for item in operation.risk.evidence}


class ComparisonRuleTests(unittest.TestCase):
    """Direct D2 rules; each fails when its rule is removed."""

    def verdict(self, before, after):
        return compare_physical_effects("script", "update", before, after)

    def test_abort_point_rule_keeps_a_reference_step_ahead_of_an_effect(self):
        # A notify with continue_on_error can still abort the run (unknown
        # service, template error), so inserting it ahead of a close changes
        # the run structure (conservative OD-10).
        ahead = self.verdict(
            script(CLOSE), script(notify("a", continue_on_error=True), CLOSE)
        )
        self.assertEqual(ahead.verdict, VERDICT_CHANGED, ahead.reason_codes)
        self.assertIn("run_structure_changed", ahead.reason_codes)
        # ...and is erased when no effect follows it.
        after = self.verdict(
            script(CLOSE), script(CLOSE, notify("a", continue_on_error=True))
        )
        self.assertEqual(after.verdict, VERDICT_EQUIVALENT, after.reason_codes)
        # Without continue_on_error the step is kept, wording ignored.
        kept = self.verdict(
            script(notify("a"), CLOSE), script(notify("b"), CLOSE)
        )
        self.assertEqual(kept.verdict, VERDICT_EQUIVALENT, kept.reason_codes)

    def test_mode_rule_compares_the_run_mode(self):
        changed = self.verdict(script(CLOSE), script(CLOSE, mode="queued"))
        self.assertEqual(changed.verdict, VERDICT_CHANGED)
        self.assertIn("mode_changed", changed.reason_codes)
        same = self.verdict(script(CLOSE), script(CLOSE, alias="Renamed"))
        self.assertEqual(same.verdict, VERDICT_EQUIVALENT, same.reason_codes)

    def test_run_occupancy_rule_keeps_steps_after_the_last_effect(self):
        # A long wait after the close keeps a single-mode run occupied, so
        # later triggers of the close are dropped.
        for tail in (
            {"delay": "00:10:00"},
            {"wait_template": "{{ false }}", "timeout": "00:10:00"},
        ):
            with self.subTest(tail=sorted(tail)):
                occupied = self.verdict(script(CLOSE), script(CLOSE, tail))
                self.assertEqual(
                    occupied.verdict, VERDICT_CHANGED, occupied.reason_codes
                )
                self.assertIn("run_structure_changed", occupied.reason_codes)

    def test_reference_only_table_never_names_an_effect(self):
        for service in ("logbook.log", "system_log.write"):
            with self.subTest(service=service):
                step = {"action": service, "data": {"entity_id": "cover.garage_main", "message": "a"}}
                self.assertEqual(
                    self.verdict(script(step), script(step)).verdict, VERDICT_NONE
                )
        # Control: an actuating service with the same data.entity_id is an effect.
        actuating = {"action": "cover.close_cover", "data": {"entity_id": "cover.garage_main"}}
        self.assertNotEqual(
            self.verdict(script(), script(actuating)).verdict, VERDICT_NONE
        )


class E013RegressionTests(ConfigurationPlanTestCase):
    async def plan(self, resource_type, target, before, after, *, title="E013"):
        self.gateway.configs[(resource_type, target)] = copy.deepcopy(before)
        created = await self.service.create_configuration_plan(
            title=title,
            description="HAMCP-213 E013 regression",
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

    def required(self, created):
        return created["policy_decision"]["required_acknowledgements"]

    async def test_logbook_mention_of_a_garage_cover_is_not_a_target(self):
        created, operation = await self.plan(
            "script",
            "e013_logbook",
            script(logbook("Garage left open")),
            script(logbook("Garage still open")),
        )
        self.assertNotIn("garage_cover_target", triggers(operation))
        self.assertEqual(compare_operation(operation).verdict, VERDICT_NONE)
        self.assertEqual(created["policy_decision"]["policy_class"], "standard_admin")
        self.assertEqual(self.required(created), ["plan_approval"])

    async def test_notify_routed_by_name_has_no_omitted_target(self):
        _created, operation = await self.plan(
            "script",
            "e013_notify",
            script(notify("a", service="notify.mobile_app_phone")),
            script(notify("b", service="notify.mobile_app_phone")),
        )
        self.assertNotIn("omitted_action_target", triggers(operation))
        # Control: an entity-acting service without a target keeps the trigger.
        _created, operation = await self.plan(
            "script",
            "e013_untargeted_light",
            script({"action": "light.turn_on"}),
            script({"action": "light.turn_on", "data": {"brightness": 10}}),
        )
        self.assertIn("omitted_action_target", triggers(operation))

    async def test_no_retry_warning_outside_a_retry_candidate(self):
        self.gateway.configs[("automation", "e013_retry_sibling")] = automation(
            notify("a")
        )
        self.gateway.configs[("script", "e013_retry_close")] = script(
            notify("a"), CLOSE
        )
        created = await self.service.create_configuration_plan(
            title="E013 retry",
            description="Two operations: not a 1- or 5-operation retry shape",
            operations=[
                {
                    "operation_id": "close",
                    "resource_type": "script",
                    "action": "update",
                    "target_id": "e013_retry_close",
                    "depends_on": [],
                    "proposed_config": script(notify("b"), CLOSE),
                },
                {
                    "operation_id": "sibling",
                    "resource_type": "automation",
                    "action": "update",
                    "target_id": "e013_retry_sibling",
                    "depends_on": [],
                    "proposed_config": automation(notify("b")),
                },
            ],
        )
        for operation in self.repository.get(created["plan_id"]).operations:
            with self.subTest(op=operation.operation_id):
                self.assertFalse(
                    any(RETRY_WARNING in item for item in operation.risk.warnings),
                    operation.risk.warnings,
                )
        self.assertEqual(self.required(created), ["plan_approval"])

    async def test_recipient_reason_only_when_recipients_change(self):
        first = notify("a", service="notify.mobile_app_a")
        second = notify("b", service="notify.mobile_app_b")
        _created, reordered = await self.plan(
            "automation",
            "e013_recipients",
            automation(first, second),
            automation(second, first),
        )
        self.assertNotIn(RECIPIENT_REASON, reordered.risk.reasons)
        _created, retargeted = await self.plan(
            "automation",
            "e013_recipients_changed",
            automation(first, second),
            automation(first, notify("b", service="notify.mobile_app_c")),
        )
        self.assertIn(RECIPIENT_REASON, retargeted.risk.reasons)

    async def test_wording_only_change_needs_plan_approval_only(self):
        created, operation = await self.plan(
            "automation",
            "e013_wording",
            automation(notify("Closing the garage"), CLOSE),
            automation(notify("The garage is closing"), CLOSE),
        )
        comparison = compare_operation(operation)
        self.assertEqual(comparison.verdict, VERDICT_EQUIVALENT, comparison.reason_codes)
        self.assertEqual(created["policy_decision"]["policy_class"], "elevated_admin")
        self.assertEqual(self.required(created), ["plan_approval"])
        self.assertNotIn(
            "Structured action or target requires high-risk review",
            operation.risk.reasons,
        )

    async def test_equivalent_unlock_still_needs_the_acknowledgement(self):
        # OD-6(b): the unlock/disarm floor applies even when equivalent.
        unlock = {"action": "lock.unlock", "target": {"entity_id": "lock.front_door"}}
        created, operation = await self.plan(
            "script",
            "e013_unlock",
            script(notify("a"), unlock),
            script(notify("b"), unlock),
        )
        comparison = compare_operation(operation)
        self.assertEqual(comparison.verdict, VERDICT_EQUIVALENT, comparison.reason_codes)
        (row,) = comparison.projection["rows"]
        self.assertEqual(row["object_id"], "lock.front_door")
        self.assertEqual(row["verdict"], "equivalent")
        self.assertIn("unlock_or_disarm_effect", row["reason_codes"])
        self.assertTrue(row["acknowledgement_required"])
        self.assertEqual(self.required(created), ["plan_approval", SAFETY])

    async def test_unresolved_target_is_shown_as_cannot_be_determined(self):
        dynamic = {
            "action": "lock.lock",
            "target": {"entity_id": "{{ states('input_text.lock_target') }}"},
        }
        created, operation = await self.plan(
            "script",
            "e013_unresolved",
            script(dynamic),
            script(dynamic, alias="Renamed"),
        )
        comparison = compare_operation(operation)
        self.assertEqual(comparison.verdict, VERDICT_UNRESOLVED, comparison.reason_codes)
        (row,) = comparison.projection["rows"]
        self.assertEqual(row["protection"], "effect_cannot_be_determined")
        self.assertEqual(self.required(created), ["plan_approval", SAFETY])

        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        _review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        plan_only = await self.service.decide_external_approval(
            plan_id=created["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=created["plan_hash"],
            approval_kind=pending["approval_kind"],
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal=PRINCIPAL,
        )
        review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], plan_only["challenge_id"]
        )
        (shown,) = review["safety_critical_acknowledgement"]["objects"]
        self.assertEqual(shown["verdict"], "unresolved")
        self.assertEqual(shown["protection"], "effect_cannot_be_determined")
        html = _render_review("/api/hassio_ingress/hamcp213", review, csrf)
        self.assertIn("effect_cannot_be_determined", html)


if __name__ == "__main__":
    unittest.main()
