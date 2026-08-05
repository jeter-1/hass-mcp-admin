"""Risk and bounded semantic-diff tests over inert dashboard data."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import random
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from f3_dashboard.models import RiskCategory, RiskDisposition  # noqa: E402
from f3_dashboard.patch import compile_dashboard_patch  # noqa: E402
from f3_dashboard.risk import analyze_dashboard_risk  # noqa: E402
from f3_dashboard.semantic_diff import build_semantic_diff  # noqa: E402
from f3_dashboard_support import load_dashboard  # noqa: E402


class DashboardRiskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current = load_dashboard()

    def apply(self, path: str, value, *, kind: str = "add"):
        compilation = compile_dashboard_patch(
            self.current,
            [
                {
                    "operation_id": "risk-change",
                    "operation": kind,
                    "path": path,
                    "value": value,
                }
            ],
        )
        risk = analyze_dashboard_risk(self.current, compilation.resulting_configuration)
        return compilation, risk

    def action_change(
        self,
        current_action,
        proposed_action,
        *,
        card_type: str = "button",
        current_extra: dict | None = None,
        proposed_extra: dict | None = None,
    ):
        current_card = {
            "type": card_type,
            "entity": "lock.synthetic_front",
            "tap_action": current_action,
            **(current_extra or {}),
        }
        proposed_card = {
            "type": card_type,
            "entity": "lock.synthetic_front",
            "tap_action": proposed_action,
            **(proposed_extra or current_extra or {}),
        }
        current = {"views": [{"cards": [current_card]}]}
        proposed = {"views": [{"cards": [proposed_card]}]}
        return analyze_dashboard_risk(current, proposed)

    @staticmethod
    def changed_for(risk, reason_code: str):
        return [
            finding
            for finding in risk.findings
            if finding.reason_code == reason_code
            and finding.introduced_or_changed
        ]

    def test_existing_instruction_like_and_action_content_remains_inert_data(self):
        risk = analyze_dashboard_risk(self.current, deepcopy(self.current))
        self.assertEqual(risk.disposition, RiskDisposition.STANDARD_REVIEW)
        self.assertFalse(risk.manual_review_required)
        self.assertTrue(risk.findings)
        self.assertFalse(any(item.introduced_or_changed for item in risk.findings))

    def test_display_title_visibility_and_high_risk_entity_display_are_not_actuation(self):
        compilation, risk = self.apply("/views/0/title", "Renamed", kind="replace")
        self.assertEqual(compilation.semantic_leaf_change_count, 1)
        self.assertEqual(risk.disposition, RiskDisposition.STANDARD_REVIEW)
        display = [
            item
            for item in risk.findings
            if item.reason_code == "high_consequence_entity_is_display_only"
        ]
        self.assertTrue(display)
        self.assertTrue(all(item.category is RiskCategory.DISPLAY_ONLY for item in display))

    def test_navigation_more_info_toggle_and_service_are_distinguished(self):
        cases = (
            ({"action": "navigate", "navigation_path": "/synthetic"}, RiskCategory.NAVIGATION),
            ({"action": "more-info"}, RiskCategory.MORE_INFO),
            ({"action": "toggle"}, RiskCategory.HIGH_CONSEQUENCE),
            ({"action": "perform-action", "perform_action": "light.turn_on"}, RiskCategory.SERVICE_ACTION),
        )
        path = "/views/0/sections/0/cards/0/tap_action"
        for value, category in cases:
            with self.subTest(category=category.value):
                _, risk = self.apply(path, value)
                changed = [item for item in risk.findings if item.introduced_or_changed]
                self.assertIn(category, {item.category for item in changed})

    def test_confirmation_does_not_reduce_high_consequence_risk(self):
        _, risk = self.apply(
            "/views/0/sections/0/cards/0/hold_action",
            {
                "action": "perform-action",
                "perform_action": "lock.unlock",
                "confirmation": {"text": "Confirm"},
            },
        )
        categories = {
            item.category for item in risk.findings if item.introduced_or_changed
        }
        self.assertIn(RiskCategory.CONFIRMATION, categories)
        self.assertIn(RiskCategory.HIGH_CONSEQUENCE, categories)
        self.assertEqual(risk.disposition, RiskDisposition.ELEVATED_REVIEW)

    def test_destructive_administrative_action_is_elevated(self):
        _, risk = self.apply(
            "/views/0/sections/0/cards/0/tap_action",
            {
                "action": "call-service",
                "service": "homeassistant.restart",
            },
        )
        self.assertEqual(risk.destructive_admin_action_count, 1)
        self.assertEqual(risk.disposition, RiskDisposition.ELEVATED_REVIEW)

    def test_opaque_custom_and_templated_actions_require_manual_review(self):
        path = "/views/0/sections/0/cards/3/hold_action"
        _, opaque = self.apply(path, {"action": "fire-dom-event", "payload": "inert"})
        self.assertTrue(opaque.manual_review_required)
        self.assertGreaterEqual(opaque.opaque_custom_action_count, 1)
        _, templated = self.apply(
            "/views/0/sections/0/cards/0/tap_action",
            {"action": "perform-action", "perform_action": "{{ inert.data }}"},
        )
        self.assertTrue(templated.manual_review_required)
        self.assertIn(
            RiskCategory.TEMPLATE_OR_CONDITIONAL,
            {item.category for item in templated.findings if item.introduced_or_changed},
        )

    def test_unknown_action_semantics_require_manual_review(self):
        _, risk = self.apply(
            "/views/0/sections/0/cards/0/tap_action",
            {"action": "future-action"},
        )
        self.assertTrue(risk.manual_review_required)
        self.assertIn(
            RiskCategory.UNKNOWN,
            {item.category for item in risk.findings if item.introduced_or_changed},
        )

    def test_same_lock_service_with_changed_target_is_detected(self):
        current = {
            "action": "perform-action",
            "perform_action": "lock.unlock",
            "target": {"entity_id": "lock.synthetic_front"},
        }
        proposed = deepcopy(current)
        proposed["target"]["entity_id"] = "lock.synthetic_back"
        risk = self.action_change(current, proposed)
        changed = self.changed_for(
            risk, "service_is_high_consequence_action"
        )
        self.assertTrue(changed)
        self.assertEqual(risk.disposition, RiskDisposition.ELEVATED_REVIEW)

    def test_same_service_with_changed_service_data_is_detected(self):
        current = {
            "action": "perform-action",
            "perform_action": "light.turn_on",
            "service_data": {"brightness": 10},
        }
        proposed = deepcopy(current)
        proposed["service_data"]["brightness"] = 20
        risk = self.action_change(current, proposed)
        self.assertTrue(
            self.changed_for(risk, "frontend_service_action")
        )
        self.assertEqual(risk.disposition, RiskDisposition.ELEVATED_REVIEW)

    def test_changed_template_expression_remains_manual_review(self):
        current = {
            "action": "perform-action",
            "perform_action": "light.turn_on",
            "data": {"brightness": "{{ old_value }}"},
        }
        proposed = deepcopy(current)
        proposed["data"]["brightness"] = "{{ new_value }}"
        risk = self.action_change(current, proposed)
        self.assertTrue(
            self.changed_for(
                risk, "action_contains_conditional_or_template_data"
            )
        )
        self.assertTrue(risk.manual_review_required)

    def test_changed_fire_dom_event_payload_remains_manual_review(self):
        current = {"action": "fire-dom-event", "payload": {"value": 1}}
        proposed = {"action": "fire-dom-event", "payload": {"value": 2}}
        risk = self.action_change(current, proposed)
        self.assertTrue(
            self.changed_for(risk, "frontend_custom_dom_event_action")
        )
        self.assertTrue(risk.manual_review_required)

    def test_changed_navigation_and_url_destinations_are_detected(self):
        cases = (
            (
                {"action": "navigate", "navigation_path": "/old"},
                {"action": "navigate", "navigation_path": "/new"},
            ),
            (
                {"action": "url", "url_path": "https://old.invalid"},
                {"action": "url", "url_path": "https://new.invalid"},
            ),
        )
        for current, proposed in cases:
            with self.subTest(action=current["action"]):
                risk = self.action_change(current, proposed)
                self.assertTrue(
                    self.changed_for(risk, "frontend_navigation_action")
                )

    def test_changed_custom_card_nested_content_remains_manual_review(self):
        action = {"action": "fire-dom-event", "payload": {"value": 1}}
        risk = self.action_change(
            action,
            deepcopy(action),
            card_type="custom:synthetic-card",
            current_extra={"custom_options": {"nested_action": {"mode": "old"}}},
            proposed_extra={"custom_options": {"nested_action": {"mode": "new"}}},
        )
        self.assertTrue(
            self.changed_for(
                risk, "custom_card_may_reinterpret_action_schema"
            )
        )
        self.assertTrue(risk.manual_review_required)

    def test_changed_confirmation_content_is_detected(self):
        current = {
            "action": "perform-action",
            "perform_action": "lock.unlock",
            "confirmation": {
                "text": "Old confirmation",
                "exemptions": [{"user": "synthetic-a"}],
            },
        }
        proposed = deepcopy(current)
        proposed["confirmation"] = {
            "text": "New confirmation",
            "exemptions": [{"user": "synthetic-b"}],
        }
        risk = self.action_change(current, proposed)
        self.assertTrue(
            self.changed_for(
                risk, "frontend_confirmation_present_but_not_authority"
            )
        )

    def test_unchanged_complete_action_semantics_reuse_binding(self):
        action = {
            "action": "perform-action",
            "perform_action": "lock.unlock",
            "target": {
                "entity_id": "lock.synthetic_front",
                "device_id": "synthetic-device",
            },
            "data": {"code": "inert"},
        }
        reordered = {
            "data": {"code": "inert"},
            "target": {
                "device_id": "synthetic-device",
                "entity_id": "lock.synthetic_front",
            },
            "perform_action": "lock.unlock",
            "action": "perform-action",
        }
        risk = self.action_change(action, reordered)
        self.assertFalse(
            any(finding.introduced_or_changed for finding in risk.findings)
        )
        self.assertTrue(
            all(
                len(finding.semantic_binding_sha256) == 64
                for finding in risk.findings
            )
        )


class DashboardSemanticDiffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_dashboard()

    def compile_diff(self, operation):
        compilation = compile_dashboard_patch(self.config, [operation])
        risk = analyze_dashboard_risk(self.config, compilation.resulting_configuration)
        return compilation, build_semantic_diff(compilation, risk)

    def test_diff_distinguishes_missing_null_and_remove(self):
        _, added = self.compile_diff(
            {"operation_id": "add-null", "operation": "add", "path": "/new_value", "value": None}
        )
        self.assertEqual(added.entries[0].previous.value_type, "missing")
        self.assertEqual(added.entries[0].proposed.value_type, "null")
        _, removed = self.compile_diff(
            {"operation_id": "remove-title", "operation": "remove", "path": "/title"}
        )
        self.assertEqual(removed.entries[0].operation, "remove")
        self.assertFalse(removed.entries[0].proposed.present)

    def test_diff_marks_values_as_untrusted_and_redacts_likely_credentials(self):
        _, diff = self.compile_diff(
            {
                "operation_id": "add-token",
                "operation": "add",
                "path": "/unknown_root_extension/access_token",
                "value": "Bearer synthetic-not-a-secret",
            }
        )
        entry = diff.entries[0]
        self.assertEqual(entry.proposed.data_role, "untrusted_data")
        self.assertTrue(entry.proposed.redacted)
        self.assertEqual(entry.proposed.preview, "<redacted>")

    def test_diff_context_hashes_and_previews_are_bounded(self):
        compilation, diff = self.compile_diff(
            {
                "operation_id": "rename-card",
                "operation": "replace",
                "path": "/views/0/sections/0/cards/0/name",
                "value": "X" * 500,
            }
        )
        entry = diff.entries[0]
        self.assertEqual(entry.context, ("view:0", "section:0", "card:0"))
        self.assertTrue(entry.proposed.truncated)
        self.assertEqual(diff.resulting_sha256, compilation.resulting_sha256)
        self.assertRegex(diff.semantic_diff_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(len(diff.entries), len(compilation.operations))

    def test_randomized_unknown_fields_are_preserved_outside_declared_path(self):
        generator = random.Random(20260804)
        for index in range(25):
            config = load_dashboard()
            generated = {
                f"unknown_{slot}": [generator.randint(0, 1_000), None, False, ""]
                for slot in range(8)
            }
            config["randomized_extension"] = deepcopy(generated)
            compiled = compile_dashboard_patch(
                config,
                [
                    {
                        "operation_id": f"rename-{index}",
                        "operation": "replace",
                        "path": "/title",
                        "value": f"Run {index}",
                    }
                ],
            )
            self.assertEqual(compiled.resulting_configuration["randomized_extension"], generated)


if __name__ == "__main__":
    unittest.main()
