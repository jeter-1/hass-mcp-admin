"""Synthetic offline lifecycle contract; no HA fixtures, template evaluation or IO."""
import copy
from datetime import datetime, timezone
import itertools
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.reliability import lifecycle
from ha_mcp_engineering.reliability.service import AutomationReliabilityAnalysisService
from ha_mcp_engineering.reliability.rules import evaluate_rules
from ha_mcp_engineering.errors import ErrorCode, GovernanceError
from ha_mcp_engineering.observability import METRICS
from tests.test_automation_reliability_analysis import (
    AUTOMATION_ID, ANALYSIS_INSTANT, FakeProvider, bundle, coverage,
)


def literal(value):
    return {"condition": "template", "value_template": value}


def inspect(config, detail="evidence"):
    return lifecycle.analyze_lifecycle(
        config, automation_id="synthetic", configuration_fingerprint="a" * 64,
        analysis_timestamp="2026-09-26T00:00:00Z", detail_level=detail,
    )


def actions(*values):
    return {"actions": list(values)}


def paths(result):
    return [item["configuration_path"] for item in result["findings"]]


class ParallelTests(unittest.TestCase):
    def assert_source_path(self, config, path):
        """Resolve a generated structural path against the unnormalized fixture."""
        self.assertTrue(path.startswith("$."), path)
        value = config
        for part in path[2:].replace("[", ".").replace("]", "").split("."):
            if part.isdecimal():
                self.assertIsInstance(value, list, path)
                value = value[int(part)]
            else:
                self.assertIsInstance(value, dict, path)
                value = value[part]
        return value

    def test_proven_stop_suppresses_outer_suffix_in_each_raw_form(self):
        for branch in ({"stop": "synthetic"}, {"sequence": [{"stop": "synthetic"}]}):
            for parallel in (branch, [branch]):
                with self.subTest(parallel=parallel):
                    result = inspect(actions({"parallel": parallel}, {"delay": 3}))
                    self.assertEqual(result["hazard_count"], 0)
                    self.assertTrue(result["coverage_complete"])
                    self.assertEqual(result["coverage"]["suppression_counts"]["unreachable_suffixes"], 1)

    def test_stop_retains_all_parallel_siblings_regardless_of_order(self):
        for stop_index in range(3):
            branches = [{"delay": 1}, {"sequence": [{"delay": 2}]}]
            branches.insert(stop_index, {"stop": "synthetic"})
            config = actions({"parallel": branches}, {"delay": 99})
            with self.subTest(stop_index=stop_index):
                result = inspect(config)
                self.assertEqual(result["hazard_count"], 2)
                self.assertTrue(result["coverage_complete"])
                self.assertEqual([self.assert_source_path(config, p) for p in paths(result)], [1, 2])
                self.assertTrue(all(p.startswith("$.actions[0].parallel[") for p in paths(result)))

    def test_branch_condition_failure_is_local_and_unknown_is_not_proven_stop(self):
        for condition, complete in ((False, True), ("{{ synthetic_condition }}", False)):
            config = actions({"parallel": [
                {"sequence": [literal(condition), {"stop": "synthetic"}]},
                {"delay": 1},
            ]}, {"delay": 2})
            with self.subTest(condition=condition):
                result = inspect(config)
                self.assertEqual(paths(result), ["$.actions[0].parallel[1].delay", "$.actions[1].delay"])
                self.assertEqual(result["coverage_complete"], complete)
                # A false condition suppresses its own branch's stop, not the caller.
                self.assertEqual(result["coverage"]["suppression_counts"].get("unreachable_suffixes", 0), int(complete))

    def test_disabled_and_unknown_stop_ancestors_preserve_outer_suffix(self):
        for enabled, complete in ((False, True), ("{{ synthetic_enabled }}", False)):
            candidates = [
                {"parallel": [{"stop": "synthetic", "enabled": enabled}]},
                {"parallel": {"sequence": [{"stop": "synthetic"}], "enabled": enabled}},
                {"parallel": [{"stop": "synthetic"}], "enabled": enabled},
                {"sequence": [{"parallel": [{"stop": "synthetic"}]}], "enabled": enabled},
            ]
            for candidate in candidates:
                with self.subTest(enabled=enabled, candidate=candidate):
                    result = inspect(actions(candidate, {"delay": 2}))
                    self.assertEqual(paths(result), ["$.actions[1].delay"])
                    self.assertEqual(result["coverage_complete"], complete)
                    self.assertNotIn("unreachable_suffixes", result["coverage"]["suppression_counts"])

    def test_selected_branch_stop_only_propagates_when_statically_proven(self):
        for decision, outer, complete in ((True, False, True), (False, True, True), ("{{ synthetic }}", True, False)):
            branches = [
                {"if": [literal(decision)], "then": [{"stop": "synthetic"}]},
                {"choose": [{"conditions": [literal(decision)], "sequence": [{"stop": "synthetic"}]}]},
            ]
            for branch in branches:
                with self.subTest(decision=decision, branch=branch):
                    result = inspect(actions({"parallel": [branch, {"delay": 1}]}, {"delay": 2}))
                    expected = ["$.actions[0].parallel[1].delay"] + (["$.actions[1].delay"] if outer else [])
                    self.assertEqual(paths(result), expected)
                    self.assertEqual(result["coverage_complete"], complete)

    def test_nested_parallel_preserves_peers_and_propagates_proven_stop(self):
        config = actions({"parallel": [
            {"sequence": [{"parallel": [{"stop": "synthetic"}, {"delay": 1}]}, {"delay": 98}]},
            {"delay": 2},
        ]}, {"delay": 99})
        result = inspect(config)
        self.assertEqual(paths(result), [
            "$.actions[0].parallel[0].sequence[0].parallel[1].delay",
            "$.actions[0].parallel[1].delay",
        ])
        self.assertEqual([self.assert_source_path(config, p) for p in paths(result)], [1, 2])
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(result["coverage"]["suppression_counts"]["unreachable_suffixes"], 2)
        # The containing condition can prevent the nested stop from being reached.
        config["actions"][0]["parallel"][0]["sequence"].insert(0, literal("{{ synthetic }}"))
        result = inspect(config)
        self.assertEqual(result["hazard_count"], 3)
        self.assertEqual(paths(result)[-1], "$.actions[1].delay")
        self.assertFalse(result["coverage_complete"])

    def test_raw_paths_resolve_for_mapping_list_shorthand_and_nested_parallel(self):
        cases = [
            ({"delay": 1}, "$.actions[0].parallel.delay"),
            ({"sequence": [{"delay": 1}]}, "$.actions[0].parallel.sequence[0].delay"),
            ([{"delay": 1}], "$.actions[0].parallel[0].delay"),
            ([{"sequence": [{"delay": 1}]}], "$.actions[0].parallel[0].sequence[0].delay"),
            ([[{"delay": 1}]], "$.actions[0].parallel[0][0].delay"),
            ({"parallel": [{"sequence": [{"parallel": {"delay": 1}}]}]},
             "$.actions[0].parallel.parallel[0].sequence[0].parallel.delay"),
        ]
        for parallel, expected in cases:
            config = actions({"parallel": parallel})
            unchanged = copy.deepcopy(config)
            with self.subTest(parallel=parallel):
                for detail in ("summary", "standard", "evidence"):
                    result = inspect(config, detail)
                    self.assertEqual(paths(result), [expected])
                    self.assertEqual(self.assert_source_path(config, paths(result)[0]), 1)
                    self.assertTrue(result["coverage_complete"])
                self.assertEqual(config, unchanged)

    def test_mapping_note_and_gap_paths_also_resolve(self):
        config = actions({"parallel": {"sequence": [{"delay": "{{ synthetic_delay }}"}, {"delay": 1}]}})
        result = inspect(config)
        self.assertEqual(result["hazard_count"], 1)
        self.assertFalse(result["coverage_complete"])
        entries = result["findings"] + result["review_notes"] + result["coverage"]["gaps"]
        self.assertGreaterEqual(len(entries), 3)
        for entry in entries:
            self.assert_source_path(config, entry["configuration_path"])

    def test_parallel_stop_does_not_bypass_shared_budget_or_hide_positive(self):
        config = actions({"parallel": [{"stop": "synthetic"}, {"delay": 1}] + [None] * 12_000}, {"delay": 99})
        result = inspect(config)
        self.assertEqual(paths(result), ["$.actions[0].parallel[1].delay"])
        self.assertEqual(result["hazard_count"], 1)
        self.assertLessEqual(result["coverage"]["structural_items_examined"], 10_000)
        self.assertTrue(result["truncated"])
        self.assertFalse(result["coverage_complete"])
        self.assertEqual(result["count_precision"], "lower_bound")


class ErrorContinuationTests(unittest.TestCase):
    def containers(self, body):
        return [
            {"parallel": [{"sequence": body}]},
            {"sequence": body},
            {"if": [literal(True)], "then": body},
            {"choose": [{"conditions": [literal(True)], "sequence": body}]},
            {"repeat": {"count": 1, "sequence": body}},
        ]

    def test_parallel_error_stop_order_keeps_conservative_outer_and_sibling_hazards(self):
        for branches in (
            [{"action": "synthetic_lifecycle.fail"}, {"stop": "synthetic"}, {"delay": 2}],
            [{"stop": "synthetic"}, {"action": "synthetic_lifecycle.fail"}, {"delay": 2}],
            [{"event": "synthetic_no_error"}, {"stop": "synthetic"}, {"delay": 2}],
        ):
            config = actions({"parallel": branches, "continue_on_error": True}, {"delay": 1})
            with self.subTest(branches=branches):
                result = inspect(config)
                self.assertEqual(paths(result), ["$.actions[0].parallel[2].delay", "$.actions[1].delay"])
                self.assertEqual(result["hazard_count"], 2)
                self.assertEqual(result["assessment"], "partial")
                self.assertFalse(result["truncated"])
                self.assertEqual(result["coverage"]["gap_counts"], {"container_error_continuation_unresolved": 1})
                self.assertTrue(all("Conditional" in item["reachability_qualification"] for item in result["findings"]))

    def test_supported_false_selectors_preserve_stop_proofs_for_all_containers(self):
        for selector in (False, 0, "false", " off ", "disable"):
            for container in self.containers([{"action": "synthetic_lifecycle.fail"}, {"stop": "synthetic"}]):
                with self.subTest(selector=selector, container=container):
                    container["continue_on_error"] = selector
                    result = inspect(actions(container, {"delay": 1}))
                    self.assertEqual(result["hazard_count"], 0)
                    self.assertTrue(result["coverage_complete"])
                    self.assertEqual(result["coverage"]["suppression_counts"]["unreachable_suffixes"], 1)

    def test_enabled_unresolved_and_malformed_selectors_withdraw_container_stop_proof(self):
        for selector in (True, 1, "true", "{{ false }}", None, [], {}, "SYNTHETIC_PRIVATE_INVALID"):
            for container in self.containers([{"action": "synthetic_lifecycle.fail"}, {"stop": "synthetic"}]):
                with self.subTest(selector=selector, container=container):
                    container["continue_on_error"] = selector
                    result = inspect(actions(container, {"delay": 1}))
                    self.assertEqual(paths(result), ["$.actions[1].delay"])
                    self.assertFalse(result["coverage_complete"])
                    self.assertFalse(result["truncated"])
                    self.assertEqual(result["coverage"]["gaps"], [{
                        "category": "container_error_continuation_unresolved",
                        "configuration_path": "$.actions[0].continue_on_error",
                    }])
                    self.assertNotIn("SYNTHETIC_PRIVATE_INVALID", json.dumps(result))
                    self.assertNotIn("{{ false }}", json.dumps(result))

    def test_outer_continuation_can_handle_nested_parallel_error(self):
        for container in self.containers([{"parallel": [
            {"action": "synthetic_lifecycle.fail"}, {"stop": "synthetic"},
        ]}]):
            container["continue_on_error"] = True
            result = inspect(actions(container, {"delay": 1}))
            self.assertEqual(paths(result), ["$.actions[1].delay"])
            self.assertFalse(result["coverage_complete"])
            self.assertEqual(result["coverage"]["gaps"][0]["configuration_path"], "$.actions[0].continue_on_error")

    def test_nested_continuation_does_not_become_a_proven_stop_in_its_parent(self):
        inner = {"parallel": [{"action": "synthetic_lifecycle.fail"}, {"stop": "synthetic"}],
                 "continue_on_error": True}
        for container in self.containers([inner]):
            config = actions(container, {"delay": 1})
            result = inspect(config)
            self.assertEqual(paths(result), ["$.actions[1].delay"])
            self.assertFalse(result["coverage_complete"])
            self.assertEqual(len(result["coverage"]["gaps"]), 1)
            gap_path = result["coverage"]["gaps"][0]["configuration_path"]
            self.assertIs(ParallelTests.assert_source_path(self, config, gap_path), True)

    def test_disabled_stop_or_container_keeps_existing_reachability(self):
        for container in self.containers([{"stop": "synthetic", "enabled": False}]):
            container["continue_on_error"] = True
            result = inspect(actions(container, {"delay": 1}))
            self.assertEqual(paths(result), ["$.actions[1].delay"])
            self.assertTrue(result["coverage_complete"])
        for container in self.containers([{"stop": "synthetic"}]):
            container.update(enabled=False, continue_on_error="{{ unsupported }}")
            result = inspect(actions(container, {"delay": 1}))
            self.assertEqual(paths(result), ["$.actions[1].delay"])
            self.assertTrue(result["coverage_complete"])

    def test_direct_stop_is_not_swallowed_and_later_stop_still_suppresses_suffix(self):
        result = inspect(actions({"stop": "synthetic", "continue_on_error": True}, {"delay": 1}))
        self.assertEqual(result["hazard_count"], 0)
        self.assertTrue(result["coverage_complete"])
        result = inspect(actions({"parallel": [{"stop": "synthetic"}], "continue_on_error": True},
                                 {"stop": "synthetic"}, {"delay": 1}))
        self.assertEqual(result["hazard_count"], 0)
        self.assertFalse(result["coverage_complete"])

    def test_unresolved_selector_uses_scalar_budget_without_reflection(self):
        result = inspect(actions({"parallel": {"stop": "synthetic"}, "continue_on_error": "SYNTHETIC" * 300},
                                 {"delay": 1}))
        self.assertEqual(paths(result), ["$.actions[1].delay"])
        self.assertTrue(result["truncated"])
        self.assertIn("scalar_length", result["limiting_reasons"])
        self.assertEqual(result["count_precision"], "lower_bound")
        self.assertIn("container_error_continuation_unresolved", result["coverage"]["gap_counts"])
        self.assertNotIn("SYNTHETIC", json.dumps(result))


class EligibilityTests(unittest.TestCase):
    def test_positive_duration_spellings_and_zero_rounding(self):
        for spelling in (1, 1.5, "1", "00:01", "00:00:01.25", {"milliseconds": 1}, {"minutes": 1, "seconds": -1}):
            with self.subTest(spelling=spelling):
                result = inspect(actions({"delay": spelling}))
                self.assertEqual(result["hazard_count"], 1)
                self.assertEqual(paths(result), ["$.actions[0].delay"])
                self.assertTrue(result["coverage_complete"])
        for spelling in (0, "0", "00:00", {"seconds": 0}, 1e-10):
            with self.subTest(zero=spelling):
                result = inspect(actions({"delay": spelling}))
                self.assertEqual(result["hazard_count"], 0)
                self.assertTrue(result["coverage_complete"])
                self.assertEqual(result["coverage"]["suppression_counts"]["zero_duration_constructs"], 1)

    def test_dynamic_malformed_and_oversized_durations_retain_neighbor(self):
        for value in ("{{ 1 }}", {"seconds": "{{ duration }}"}, -1, float("nan"), float("inf"), [], {"bad": 1}, "x" * 2001):
            with self.subTest(kind=type(value).__name__):
                result = inspect(actions({"delay": value}, {"delay": 3}))
                self.assertEqual(result["hazard_count"], 1)
                self.assertEqual(result["assessment"], "partial")
                self.assertEqual(paths(result), ["$.actions[1].delay"])
                self.assertEqual(result["review_note_count"], 1)

    def test_enabled_disabled_and_dynamic_parent(self):
        for disabled in (False, "false", "off", 0):
            result = inspect(actions({"enabled": disabled, "sequence": [{"delay": 9}, {"delay": "{{ x }}"}]}))
            self.assertFalse(result["hazards_detected"])
            self.assertTrue(result["coverage_complete"])
            self.assertEqual(result["coverage"]["suppression_counts"]["disabled_subtrees"], 1)
        result = inspect(actions({"enabled": "{{ flag }}", "sequence": [{"delay": 9}]}))
        self.assertEqual(result["hazard_count"], 1)
        self.assertFalse(result["coverage_complete"])
        self.assertIn("enabled_unresolved", result["coverage"]["gap_counts"])

    def test_trigger_for_syntax_and_disabled(self):
        for root, field, family in itertools.product(("trigger", "triggers"), ("platform", "trigger"), ("state", "numeric_state")):
            trigger = {field: family, "entity_id": "sensor.synthetic", "for": 30}
            result = inspect({root: [trigger]})
            self.assertEqual(result["hazard_count"], 1)
            self.assertEqual(paths(result), [f"$.{root}[0].for"])
            trigger["enabled"] = False
            result = inspect({root: [trigger]})
            self.assertEqual(result["hazard_count"], 0)
            self.assertTrue(result["coverage_complete"])

    def test_zero_and_dynamic_trigger_duration(self):
        for duration, hazards, complete in ((0, 0, True), ("{{ 0 }}", 0, False), (1, 1, True)):
            result = inspect({"triggers": [{"trigger": "state", "for": duration}]})
            self.assertEqual((result["hazard_count"], result["coverage_complete"]), (hazards, complete))

    def test_unsupported_trigger_and_nested_trigger_lists(self):
        result = inspect({"triggers": [{"triggers": [{"trigger": "state", "for": 1}]}, {"trigger": "template", "for": 3}]})
        self.assertEqual(result["hazard_count"], 1)
        self.assertFalse(result["coverage_complete"])
        self.assertIn("unsupported_trigger", result["coverage"]["gap_counts"])
        self.assertEqual(paths(result), ["$.triggers[0].triggers[0].for"])

    def test_schedule_and_repeat_notes_are_not_hazards(self):
        result = inspect({"trigger": [{"platform": "time", "at": ["09:00", "23:59:59"]}],
                          "actions": [{"repeat": {"while": [literal(True)], "sequence": [{"event": "synthetic"}]}}]})
        self.assertEqual(result["review_note_count"], 3)
        self.assertEqual(result["hazard_count"], 0)
        self.assertEqual(result["assessment"], "no_detected_hazards_in_scope")
        self.assertTrue(result["coverage_complete"])
        self.assertTrue(all(n["status"] == "review_note" for n in result["review_notes"]))

    def test_dynamic_schedule_has_no_missed_event_claim(self):
        result = inspect({"trigger": {"platform": "time", "at": "input_datetime.synthetic"}})
        self.assertEqual(result["review_note_count"], 0)
        self.assertFalse(result["coverage_complete"])
        self.assertFalse(result["hazards_detected"])

    def test_wait_templates_and_absent_timeout(self):
        for wait_value, hazards, complete in ((False, 1, True), ("false", 1, True), (True, 0, True), ("true", 0, True), ("{{ ready }}", 0, False)):
            with self.subTest(wait=wait_value):
                result = inspect(actions({"wait_template": wait_value}))
                self.assertEqual((result["hazard_count"], result["coverage_complete"]), (hazards, complete))
        for timeout in (0, "00:00", {"seconds": 0}):
            result = inspect(actions({"wait_template": False, "timeout": timeout}, {"delay": 1}))
            self.assertEqual(paths(result), ["$.actions[1].delay"])
            self.assertTrue(result["coverage_complete"])

    def test_wait_trigger_timeout_and_no_registered_trigger(self):
        for timeout in (None, 1, 0, "{{ timeout }}"):
            step = {"wait_for_trigger": [{"trigger": "event", "event_type": "synthetic"}]}
            if timeout is not None:
                step["timeout"] = timeout
            result = inspect(actions(step))
            self.assertEqual(result["hazard_count"], int(timeout in (None, 1)))
            self.assertEqual(result["coverage_complete"], timeout != "{{ timeout }}")
        result = inspect(actions({"wait_for_trigger": [{"trigger": "event", "enabled": False}]}))
        self.assertFalse(result["hazards_detected"])
        self.assertTrue(result["coverage_complete"])
        result = inspect(actions({"wait_for_trigger": [{"trigger": "synthetic_unknown"}]}))
        self.assertFalse(result["coverage_complete"])
        self.assertFalse(result["hazards_detected"])

    def test_zero_timeout_preserves_continuation_and_satisfied_wait(self):
        for key, value in (("wait_template", False), ("wait_for_trigger", [{"trigger": "event"}])):
            for continuation in (True, False):
                result = inspect(actions({key: value, "timeout": 0, "continue_on_timeout": continuation}, {"delay": 2}))
                self.assertEqual(result["hazard_count"], int(continuation))
        result = inspect(actions({"wait_template": True, "timeout": 0, "continue_on_timeout": False}, {"delay": 2}))
        self.assertEqual(result["hazard_count"], 1)

    def test_unknown_wait_readiness_cannot_prove_zero_timeout_suffix_unreachable(self):
        result = inspect(actions({"wait_template": "{{ already_ready }}", "timeout": 0,
                                  "continue_on_timeout": False}, {"delay": 3}))
        self.assertEqual(paths(result), ["$.actions[1].delay"])
        self.assertFalse(result["coverage_complete"])
        self.assertNotIn("unreachable_suffixes", result["coverage"]["suppression_counts"])

    def test_malformed_signed_duration_is_not_an_eligible_hazard(self):
        result = inspect(actions({"delay": "+-01:00"}))
        self.assertEqual(result["hazard_count"], 0)
        self.assertFalse(result["coverage_complete"])

    def test_repeat_count_zero_finite_dynamic_and_for_each(self):
        for selector, count, complete in ((0, 0, True), (2, 1, True), ("2", 1, True), ("{{ count }}", 1, False)):
            result = inspect(actions({"repeat": {"count": selector, "sequence": [{"delay": 2}]}}))
            self.assertEqual((result["hazard_count"], result["coverage_complete"]), (count, complete))
            self.assertEqual(result["review_note_count"], 0)
        for values in ([], [1, 2]):
            result = inspect(actions({"repeat": {"for_each": values, "sequence": [{"delay": 2}]}}))
            self.assertEqual(result["hazard_count"], int(bool(values)))
        result = inspect(actions({"repeat": {"count": 1_000_000, "sequence": [{"event": "synthetic"}]}}))
        self.assertEqual(result["hazard_count"], 0)
        self.assertEqual(result["review_note_count"], 0)

    def test_while_false_suppresses_until_does_not(self):
        for key in ("while", "until"):
            result = inspect(actions({"repeat": {key: [literal(False)], "sequence": [{"delay": 3}]}}))
            self.assertEqual(result["hazard_count"], int(key == "until"))
            self.assertEqual(result["review_note_count"], int(key == "until"))
            self.assertTrue(result["coverage_complete"])

    def test_static_if_choose_and_automation_conditions(self):
        result = inspect(actions({"if": [literal(False)], "then": [{"delay": 2}], "else": [{"delay": 4}]}))
        self.assertEqual(paths(result), ["$.actions[0].else[0].delay"])
        result = inspect(actions({"choose": [
            {"conditions": [literal(False)], "sequence": [{"delay": 1}]},
            {"conditions": [], "sequence": [{"delay": 2}]},
            {"conditions": [], "sequence": [{"delay": 3}]}], "default": [{"delay": 4}]}))
        self.assertEqual(paths(result), ["$.actions[0].choose[1].sequence[0].delay"])
        result = inspect({"conditions": [literal(False)], "actions": [{"delay": 1}, {"delay": "{{ opaque }}"}]})
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(result["hazard_count"], 0)

    def test_unknown_condition_preserves_conditional_neighbors(self):
        result = inspect(actions({"if": [{"condition": "state", "entity_id": "sensor.synthetic", "state": "on"}],
                                  "then": [{"delay": 1}], "else": [{"delay": 2}]}, {"delay": 3}))
        self.assertEqual(result["hazard_count"], 3)
        self.assertFalse(result["coverage_complete"])
        self.assertTrue(all("Conditional" in f["reachability_qualification"] for f in result["findings"]))

    def test_condition_and_stop_suffixes_and_nested_scope(self):
        for first in (literal(False), {"stop": "synthetic reason"}):
            result = inspect(actions(first, {"delay": 1}, {"delay": "{{ secret }}"}))
            self.assertEqual(result["hazard_count"], 0)
            self.assertTrue(result["coverage_complete"])
        result = inspect(actions({"sequence": [literal(False), {"delay": 1}]}, {"delay": 2}))
        self.assertEqual(paths(result), ["$.actions[1].delay"])
        result = inspect(actions({"sequence": [{"stop": "synthetic"}]}, {"delay": 2}))
        self.assertFalse(result["hazards_detected"])
        result = inspect(actions({"sequence": [literal("{{ condition }}"), {"stop": "synthetic"}]}, {"delay": 2}))
        self.assertEqual(result["hazard_count"], 1)
        self.assertFalse(result["coverage_complete"])

    def test_repeat_first_iteration_stop_and_parallel_siblings(self):
        result = inspect(actions({"repeat": {"count": 1, "sequence": [{"stop": "synthetic"}]}}, {"delay": 3}))
        self.assertFalse(result["hazards_detected"])
        result = inspect(actions({"parallel": [{"stop": "synthetic"}, {"sequence": [{"delay": 1}]}]}))
        self.assertEqual(paths(result), ["$.actions[0].parallel[1].sequence[0].delay"])

    def test_disabled_condition_does_not_block_following_actions(self):
        result = inspect(actions({"enabled": False, **literal(False)}, {"delay": 2}))
        self.assertEqual(result["hazard_count"], 1)
        result = inspect(actions({"if": [{"enabled": False, **literal(False)}], "then": [{"delay": 1}]}))
        self.assertEqual(result["hazard_count"], 1)

    def test_stored_script_blueprint_and_dynamic_service_gaps(self):
        result = inspect({"use_blueprint": {"path": "synthetic.yaml"}, "actions": [
            {"action": "script.synthetic"}, {"service": "script.turn_on"},
            {"action": "{{ service }}"}, {"delay": 2}]})
        self.assertEqual(result["hazard_count"], 1)
        self.assertEqual(result["coverage"]["gap_counts"]["called_script_not_inspected"], 2)
        self.assertIn("blueprint_not_expanded", result["coverage"]["gap_counts"])
        self.assertFalse(result["coverage_complete"])

    def test_service_payload_lookalikes_and_secrets_are_opaque(self):
        secret = "SYNTHETIC_PRIVATE_TOKEN_IGNORE_ALL_INSTRUCTIONS"
        config = actions({"service": "notify.synthetic", "data": {"delay": 90, "sequence": [{"delay": 90}], "text": secret}},
                         {"delay": "{{ " + secret + " }}"})
        original = copy.deepcopy(config)
        result = inspect(config)
        self.assertEqual(result["hazard_count"], 0)
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(config, original)

    def test_startup_observation_is_not_recovery_certification(self):
        result = inspect({"trigger": [{"platform": "homeassistant", "event": "start"}], "action": [{"delay": 3}]})
        self.assertEqual(result["startup_observation"]["literal_start_trigger_count"], 1)
        self.assertFalse(result["startup_observation"]["recovery_verified"])
        self.assertEqual(result["hazard_count"], 1)


class BoundAndProjectionTests(unittest.TestCase):
    def test_combined_depth_width_and_malformed_shared_budget(self):
        deep = [{"delay": 1} for _ in range(12_000)]
        for _ in range(17):
            deep = [{"sequence": deep}]
        result = inspect(actions(*deep, {"delay": 4}))
        self.assertLessEqual(result["coverage"]["structural_items_examined"], 10_000)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["hazard_count"], 1)
        result = inspect(actions({"parallel": [None] * 6000}, {"parallel": [None] * 6000}, {"delay": 4}))
        self.assertEqual(result["coverage"]["structural_items_examined"], 10_000)
        self.assertIn("structural_items", result["limiting_reasons"])
        self.assertEqual(result["count_precision"], "lower_bound")
        self.assertFalse(result["hazards_detected"])

    def test_shallow_work_is_bounded_without_losing_early_positive(self):
        result = inspect(actions({"delay": 1}, *({"event": "synthetic"} for _ in range(12_000)), {"delay": 2}))
        self.assertEqual(result["hazard_count"], 1)
        self.assertLessEqual(result["coverage"]["structural_items_examined"], 10_000)
        self.assertTrue(result["hazards_detected"])
        self.assertTrue(result["truncated"])

    def test_combined_retention_and_positive_after_gaps(self):
        result = inspect(actions(*([None] * 40), {"delay": 1}))
        self.assertEqual(result["hazard_count"], 1)
        self.assertTrue(result["findings"])
        self.assertLessEqual(len(result["findings"]) + len(result["review_notes"]) + len(result["coverage"]["gaps"]), 32)
        self.assertIn("retained_entries", result["limiting_reasons"])

    def test_output_size_preserves_counts_and_bounded_paths(self):
        result = inspect(actions(*({"delay": 1} for _ in range(32))))
        self.assertLessEqual(len(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()), 16_384)
        self.assertTrue(result["hazards_detected"])
        self.assertEqual(result["hazard_count"], 32)
        self.assertTrue(result["truncated"])
        self.assertIn("output_bytes", result["limiting_reasons"])
        self.assertEqual(result["count_precision"], "lower_bound")
        self.assertTrue(all(len(item["configuration_path"]) <= 256 for item in result["findings"]))

    def test_detail_levels_keep_classification_and_fingerprint(self):
        config = actions(*({"delay": 1} for _ in range(5)))
        results = [inspect(config, detail) for detail in ("summary", "standard", "evidence")]
        for key in ("assessment", "hazard_count", "coverage_complete", "truncated", "count_precision", "evidence_fingerprint", "analysis_timestamp", "provenance"):
            self.assertEqual(results[0][key], results[1][key])
            self.assertEqual(results[1][key], results[2][key])
        self.assertEqual(len(results[0]["findings"]), 3)
        self.assertEqual(results[0]["display_omission"]["findings"], 2)
        self.assertFalse(results[0]["truncated"])
        self.assertEqual(len(results[1]["findings"]), 5)
        self.assertNotIn("derivation", results[1]["findings"][0])
        self.assertIn("derivation", results[2]["findings"][0])

    def test_summary_notes_limit_and_stable_ids(self):
        config = {"trigger": [{"platform": "time", "at": ["09:00"] * 5}]}
        summary = inspect(config, "summary")
        self.assertEqual(len(summary["review_notes"]), 3)
        self.assertEqual(summary["display_omission"]["review_notes"], 2)
        self.assertFalse(summary["truncated"])
        first, second = inspect(actions({"delay": 1})), inspect(actions({"delay": 1}))
        self.assertEqual(first, second)
        self.assertNotEqual(first["findings"][0]["finding_id"], inspect(actions({"event": "synthetic"}, {"delay": 1}))["findings"][0]["finding_id"])


class ResponseContractTests(unittest.IsolatedAsyncioTestCase):
    def make_bundle(self, legacy=False, hazard=False, incomplete=False, base_partial=False):
        config = actions(*(([{"delay": 1}] if hazard else []) + ([{"service": "script.synthetic"}] if incomplete else [])))
        value = bundle(config=config, state="off" if legacy else "on")
        if base_partial:
            value.coverage.append(coverage("entity_state", "partial", failed=1))
        return value

    async def test_full_L_H_C_B_decision_table_all_detail_levels(self):
        for legacy, hazard, incomplete, base_partial, detail in itertools.product((False, True), (False, True), (False, True), (False, True), ("summary", "standard", "evidence")):
            with self.subTest(L=legacy, H=hazard, C=not incomplete, B=base_partial, detail=detail):
                value = self.make_bundle(legacy, hazard, incomplete, base_partial)
                expected_ids = [x.finding_id for x in evaluate_rules(copy.deepcopy(value))]
                provider = FakeProvider(value)
                result = await AutomationReliabilityAnalysisService(provider, clock=lambda: ANALYSIS_INSTANT).analyze(automation_id=AUTOMATION_ID, detail_level=detail)
                partial = incomplete or base_partial
                self.assertEqual(result.partial, partial)
                self.assertEqual(result.data["overall_assessment"], "partial_evidence" if partial else "findings_present" if legacy or hazard else "no_findings")
                self.assertEqual(result.data["result_status"], "partial" if partial else "success")
                lc = result.data["lifecycle_analysis"]
                self.assertEqual(lc["assessment"], "partial" if incomplete else "potential_hazards" if hazard else "no_detected_hazards_in_scope")
                self.assertEqual(lc["hazard_count"], int(hazard))
                self.assertEqual(sum(result.data["finding_counts_by_severity"].values()), len(expected_ids))
                self.assertEqual(result.data["pagination"]["total"], len(expected_ids))
                if detail != "summary":
                    self.assertEqual([x["finding_id"] for x in result.data["findings"]], expected_ids)
                self.assertEqual(len(provider.calls), 1)

    async def test_notes_alone_are_no_findings(self):
        value = bundle(config={"trigger": [{"platform": "time", "at": "09:00"}]})
        result = await AutomationReliabilityAnalysisService(FakeProvider(value)).analyze(automation_id=AUTOMATION_ID)
        self.assertEqual(result.data["overall_assessment"], "no_findings")
        self.assertEqual(result.data["lifecycle_analysis"]["review_note_count"], 1)

    async def test_foundational_trace_gate_preserved_for_hazards(self):
        for completeness, collection, legacy in itertools.product(("unavailable", "failed"), ("timeout", "list_failed"), (False, True)):
            with self.subTest(completeness=completeness, collection=collection, legacy=legacy):
                value = self.make_bundle(legacy=legacy, hazard=True)
                value.traces = []
                trace = coverage("automation_traces", completeness, examined=0, failed=1)
                trace.collection_state = collection
                value.coverage[5] = trace
                provider = FakeProvider(value)
                service = AutomationReliabilityAnalysisService(provider)
                if not legacy:
                    with patch("ha_mcp_engineering.reliability.service.analyze_lifecycle", side_effect=AssertionError("must remain behind gate")):
                        with self.assertRaises(GovernanceError) as caught:
                            await service.analyze(automation_id=AUTOMATION_ID)
                    self.assertEqual(caught.exception.code, ErrorCode.PROVIDER_TIMEOUT if collection == "timeout" else ErrorCode.ANALYSIS_UNAVAILABLE)
                else:
                    result = await service.analyze(automation_id=AUTOMATION_ID)
                    self.assertTrue(result.partial)
                    self.assertTrue(result.data["lifecycle_analysis"]["hazards_detected"])
                self.assertEqual(len(provider.calls), 1)

    async def test_successful_empty_traces_use_normal_table(self):
        value = self.make_bundle(hazard=True)
        value.traces = []
        value.coverage[5].trustworthy_empty = True
        value.coverage[5].collection_state = "complete"
        result = await AutomationReliabilityAnalysisService(FakeProvider(value)).analyze(automation_id=AUTOMATION_ID)
        self.assertTrue(result.data["lifecycle_analysis"]["hazards_detected"])
        self.assertFalse(result.partial)
        self.assertEqual(result.data["overall_assessment"], "findings_present")

    async def test_cursor_freezes_lifecycle_no_recollection_or_metric_inflation(self):
        for incomplete, base_partial in itertools.product((False, True), repeat=2):
            value = self.make_bundle(hazard=True, incomplete=incomplete, base_partial=base_partial)
            value.references = [{"entity_id": f"sensor.synthetic_{i}", "status": "missing", "config_path": f"$.actions[{i}]"} for i in range(5)]
            provider = FakeProvider(value)
            service = AutomationReliabilityAnalysisService(provider)
            METRICS.reset()
            first = await service.analyze(automation_id=AUTOMATION_ID, limit=2)
            frozen = copy.deepcopy(first.data["lifecycle_analysis"])
            before = METRICS.snapshot()["automation_reliability_analysis"]
            provider.values[0].configuration = actions()
            second = await service.analyze(automation_id=AUTOMATION_ID, limit=2, cursor=first.data["pagination"]["next_cursor"])
            last = await service.analyze(automation_id=AUTOMATION_ID, limit=2, cursor=second.data["pagination"]["next_cursor"])
            self.assertTrue(first.partial)
            self.assertTrue(second.partial)
            self.assertEqual(last.partial, incomplete or base_partial)
            self.assertEqual(last.data["overall_assessment"], "partial_evidence" if incomplete or base_partial else "findings_present")
            self.assertEqual(frozen, second.data["lifecycle_analysis"])
            self.assertEqual(frozen, last.data["lifecycle_analysis"])
            self.assertEqual(len(provider.calls), 1)
            after = METRICS.snapshot()["automation_reliability_analysis"]
            self.assertEqual(before["finding_counts_by_severity"], after["finding_counts_by_severity"])
            self.assertEqual(before["root_cause_counts_by_severity"], after["root_cause_counts_by_severity"])

    async def test_truncated_lifecycle_stays_partial_on_final_legacy_page(self):
        value = self.make_bundle()
        value.configuration = actions(*({"delay": 1} for _ in range(32)))
        value.references = [{"entity_id": f"sensor.synthetic_{i}", "status": "missing", "config_path": f"$.action[{i}]"} for i in range(2)]
        provider = FakeProvider(value)
        service = AutomationReliabilityAnalysisService(provider)
        first = await service.analyze(automation_id=AUTOMATION_ID, limit=1)
        last = await service.analyze(automation_id=AUTOMATION_ID, limit=1, cursor=first.data["pagination"]["next_cursor"])
        self.assertTrue(last.partial)
        self.assertTrue(last.data["lifecycle_analysis"]["truncated"])
        self.assertEqual(first.data["lifecycle_analysis"], last.data["lifecycle_analysis"])
        self.assertEqual(len(provider.calls), 1)

    async def test_whole_provider_failure_and_timeout_remain_failures(self):
        from ha_mcp_engineering.providers import ProviderResult, ProviderCapability, ProviderCompleteness
        class FailedProvider:
            async def fetch(self, request):
                return ProviderResult(provider_id="engineering", capability=ProviderCapability.RELIABILITY_ANALYSIS,
                                      completeness=ProviderCompleteness.FAILED)
        with self.assertRaises(GovernanceError) as caught:
            await AutomationReliabilityAnalysisService(FailedProvider()).analyze(automation_id=AUTOMATION_ID)
        self.assertEqual(caught.exception.code, ErrorCode.ANALYSIS_UNAVAILABLE)
        with self.assertRaises(GovernanceError) as caught:
            await AutomationReliabilityAnalysisService(FakeProvider(TimeoutError())).analyze(automation_id=AUTOMATION_ID)
        self.assertEqual(caught.exception.code, ErrorCode.PROVIDER_TIMEOUT)

    async def test_hazard_output_truncation_is_partial_with_zero_legacy_counts(self):
        value = bundle(config=actions(*({"delay": 1} for _ in range(32))))
        result = await AutomationReliabilityAnalysisService(FakeProvider(value)).analyze(automation_id=AUTOMATION_ID)
        self.assertTrue(result.partial)
        self.assertEqual(result.data["lifecycle_analysis"]["hazard_count"], 32)
        self.assertEqual(sum(result.data["finding_counts_by_severity"].values()), 0)
        self.assertEqual(result.data["pagination"]["total"], 0)
        self.assertIsNone(result.data["pagination"]["next_cursor"])


if __name__ == "__main__":
    unittest.main()
