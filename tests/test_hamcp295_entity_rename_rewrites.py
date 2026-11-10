"""HAMCP-295 dependent reference rewrites: boundary B1-B6 (design 3.3, N29, N34)."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.governance import entity_rename_rewrites as rw  # noqa: E402


OLD = "light.kitchen"
NEW = "light.cook"


def _plan(holder_type, config, **kwargs):
    original = copy.deepcopy(config)
    result = rw.plan_holder_rewrite(
        holder_type=holder_type, holder_id="h1", config=config, old=OLD, new=NEW, **kwargs
    )
    # The rewriter never mutates the holder it was given.
    assert config == original
    return result


def _reasons(result):
    return [(item["position"], item["reason"]) for item in result.not_rewritten]


AUTOMATION = {
    "alias": "light.kitchen lamp",
    "triggers": [{"trigger": "state", "entity_id": OLD}],
    "conditions": [
        {"condition": "template", "value_template": '{{ is_state("light.kitchen", "on") }}'}
    ],
    "actions": [
        {"action": "light.turn_on", "target": {"entity_id": [OLD, "light.hall"]}},
        {"action": "light.turn_off", "target": {"entity_id": "light.kitchen, light.hall"}},
    ],
}


class BoundaryTests(unittest.TestCase):
    def test_structured_leaves_and_list_elements_are_rewritten(self):
        result = _plan("automation", AUTOMATION)
        self.assertEqual(
            result.rewritten_paths, ("$.actions[0].target.entity_id", "$.triggers[0].entity_id")
        )
        proposed = result.proposed_config
        self.assertEqual(proposed["triggers"][0]["entity_id"], NEW)
        self.assertEqual(proposed["actions"][0]["target"]["entity_id"], [NEW, "light.hall"])
        # Free text and every listed position are untouched.
        self.assertEqual(proposed["alias"], AUTOMATION["alias"])
        self.assertEqual(proposed["conditions"], AUTOMATION["conditions"])
        self.assertEqual(proposed["actions"][1], AUTOMATION["actions"][1])
        self.assertEqual(
            _reasons(result),
            [
                ("$.conditions[0].value_template", rw.TEMPLATE_NOT_REWRITTEN),
                ("$.actions[1].target.entity_id", rw.UNSUPPORTED_VALUE_SHAPE),
            ],
        )

    def test_template_is_never_textually_rewritten(self):
        config = {
            "sequence": [
                {"action": "light.turn_on", "target": {"entity_id": OLD}},
                {"condition": "template", "value_template": "{{ states.light.kitchen.state }}"},
            ]
        }
        result = _plan("script", config)
        self.assertEqual(result.proposed_config["sequence"][1], config["sequence"][1])
        self.assertIn(("$.sequence[1].value_template", rw.TEMPLATE_NOT_REWRITTEN), _reasons(result))

    def test_event_trigger_data_is_listed_not_rewritten(self):
        config = {
            "triggers": [
                {"trigger": "state", "entity_id": OLD},
                {"trigger": "event", "event_type": "state_changed", "event_data": {"entity_id": OLD}},
            ],
            "actions": [],
        }
        result = _plan("automation", config)
        self.assertEqual(result.rewritten_paths, ("$.triggers[0].entity_id",))
        self.assertEqual(result.proposed_config["triggers"][1], config["triggers"][1])
        self.assertEqual(
            _reasons(result), [("$.triggers[1].event_data.entity_id", rw.EVENT_DATA_NOT_REWRITTEN)]
        )

    def test_script_event_action_entity_id_is_a_structured_reference(self):
        config = {
            "sequence": [
                {"action": "light.turn_on", "target": {"entity_id": OLD}},
                {"event": "custom", "event_data": {"entity_id": OLD}},
            ]
        }
        result = _plan("script", config)
        self.assertEqual(result.not_rewritten, [])
        self.assertEqual(result.proposed_config["sequence"][1]["event_data"]["entity_id"], NEW)

    def test_scene_key_is_renamed_preserving_order(self):
        config = {
            "name": "Evening",
            "entities": {"light.hall": {"state": "on"}, OLD: {"state": "on"}, "switch.a": {"state": "off"}},
        }
        result = _plan("scene", config)
        self.assertEqual(result.resource_type, "scene_configuration")
        self.assertEqual(list(result.proposed_config["entities"]), ["light.hall", NEW, "switch.a"])
        self.assertEqual(result.proposed_config["entities"][NEW], {"state": "on"})

    def test_blueprint_input_is_listed_not_rewritten(self):
        config = {"use_blueprint": {"path": "motion.yaml", "input": {"light": OLD}}}
        result = _plan("automation", config)
        self.assertIsNone(result.proposed_config)
        self.assertEqual(_reasons(result), [("$.use_blueprint.input.light", rw.BLUEPRINT_NOT_REWRITTEN)])

    def test_nested_value_fails_closed_for_the_whole_holder(self):
        config = {
            "triggers": [{"trigger": "state", "entity_id": OLD}],
            "actions": [{"action": "light.turn_on", "target": {"entity_id": {"x": OLD}}}],
        }
        result = _plan("automation", config)
        self.assertIsNone(result.proposed_config)
        self.assertEqual(
            _reasons(result),
            [
                ("$.triggers[0].entity_id", rw.REWRITE_EVIDENCE_MISMATCH),
                ("$.actions[0].target.entity_id.x", rw.UNSUPPORTED_VALUE_SHAPE),
            ],
        )

    def test_derived_obligation_change_fails_b6_for_the_whole_holder(self):
        # trigger.entity_id rendered downstream would silently change: B6.
        config = {
            "triggers": [{"trigger": "state", "entity_id": OLD}],
            "actions": [
                {"action": "script.turn_on", "data": {"variables": {"e": "{{ trigger.entity_id }}"}}}
            ],
        }
        result = _plan("automation", config)
        self.assertIsNone(result.proposed_config)
        self.assertEqual(_reasons(result), [("$.triggers[0].entity_id", rw.REWRITE_EVIDENCE_MISMATCH)])

    def test_prefix_and_other_ids_never_match(self):
        config = {"triggers": [{"trigger": "state", "entity_id": "light.kitchen_2"}], "actions": []}
        result = _plan("automation", config)
        self.assertIsNone(result.proposed_config)
        self.assertEqual(result.not_rewritten, [])

    def test_adapter_not_admitted_lists_candidates(self):
        result = _plan("automation", AUTOMATION, adapter_admitted=False)
        self.assertIsNone(result.proposed_config)
        self.assertIn(("$.triggers[0].entity_id", rw.HOLDER_ADAPTER_NOT_ADMITTED), _reasons(result))

    def test_invalid_inputs_are_refused(self):
        with self.assertRaises(ValueError):
            rw.plan_holder_rewrite(holder_type="dashboard", holder_id="h", config={}, old=OLD, new=NEW)
        with self.assertRaises(ValueError):
            rw.plan_holder_rewrite(holder_type="script", holder_id="h", config={}, old=OLD, new="switch.cook")
        with self.assertRaises(ValueError):
            rw.plan_holder_rewrite(holder_type="script", holder_id="h", config={}, old=OLD, new=OLD)

    def test_every_listed_reason_is_closed(self):
        result = _plan("automation", AUTOMATION)
        self.assertTrue({item["reason"] for item in result.not_rewritten} <= rw.NOT_REWRITTEN_REASONS)


def _automation(index):
    return {
        "holder_type": "automation",
        "holder_id": f"a{index}",
        "config": {"triggers": [{"trigger": "state", "entity_id": OLD}], "actions": []},
    }


class PlanTests(unittest.TestCase):
    def test_fixed_order_and_sorted_not_rewritten_set(self):
        plan = rw.plan_rewrites(
            old=OLD,
            new=NEW,
            holders=[
                {"holder_type": "scene", "holder_id": "s1", "config": {"entities": {OLD: {"state": "on"}}}},
                {"holder_type": "script", "holder_id": "b", "config": {"sequence": [{"action": "light.turn_on", "target": {"entity_id": OLD}}]}},
                _automation(2),
                _automation(1),
            ],
            other_references=[{"holder_type": "dashboard", "holder_id": "overview", "position": "$.views[0]"}],
        )
        self.assertEqual(
            [(s.holder_type, s.holder_id) for s in plan.steps],
            [("automation", "a1"), ("automation", "a2"), ("script", "b"), ("scene", "s1")],
        )
        self.assertEqual(plan.not_rewritten_count, 1)
        self.assertEqual(plan.not_rewritten_references[0]["reason"], rw.NO_GOVERNED_WRITER)

    def test_more_than_seven_rewrites_is_refused(self):
        rw.plan_rewrites(old=OLD, new=NEW, holders=[_automation(i) for i in range(7)])
        with self.assertRaises(rw.EntityRenameRewriteLimitError):
            rw.plan_rewrites(old=OLD, new=NEW, holders=[_automation(i) for i in range(8)])

    def test_duplicate_holder_and_b1_type_as_other_reference_are_refused(self):
        with self.assertRaises(ValueError):
            rw.plan_rewrites(old=OLD, new=NEW, holders=[_automation(1), _automation(1)])
        with self.assertRaises(ValueError):
            rw.plan_rewrites(
                old=OLD, new=NEW, holders=[],
                other_references=[{"holder_type": "automation", "holder_id": "a1"}],
            )

    def test_reference_set_recheck_detects_any_difference(self):
        plan = rw.plan_rewrites(
            old=OLD, new=NEW, holders=[_automation(1)],
            other_references=[{"holder_type": "dashboard", "holder_id": "overview"}],
        )
        persisted = plan.not_rewritten_references
        self.assertFalse(rw.reference_set_changed(persisted, list(reversed(persisted))))
        grown = persisted + [
            {"holder_type": "group", "holder_id": "g", "position": "$", "reason": rw.NO_GOVERNED_WRITER}
        ]
        self.assertTrue(rw.reference_set_changed(persisted, grown))
        self.assertTrue(rw.reference_set_changed(persisted, []))


if __name__ == "__main__":
    unittest.main()
