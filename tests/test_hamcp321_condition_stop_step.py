"""HAMCP-321: a trace names a condition stop only where the run stopped.

Core sets ``script_execution`` to ``failed_conditions`` when an automation
condition is false and to ``aborted`` when a condition action is false, and
``last_step`` to the last step it traced. The reliability trace named the
first false condition anywhere in the trace, so a run that a choose routed past
a false option, and that then stopped at a later condition action, was
reported as stopped at the choose option. A false branch condition is not a
stop; nor is an abort by a stop action or a wait timeout.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT / "tests"))

from ha_mcp_engineering.incident.normalization import normalize_traces  # noqa: E402
from ha_mcp_engineering.reliability.provider import _normalize_trace  # noqa: E402
from ha_mcp_engineering.reliability.rules import evaluate_rules  # noqa: E402

from test_automation_reliability_analysis import bundle  # noqa: E402

FALSE = {"result": {"result": False}}
TRUE = {"result": {"result": True}}


def summary(last_step, script_execution, run_id="r1", **extra):
    return {"run_id": run_id, "timestamp": "2026-07-12T10:00:00Z", "state": "stopped",
            "last_step": last_step, "script_execution": script_execution, **extra}


def routed_past_then_stopped():
    """The live shape: a choose routes past a false option to its default,
    then a template condition action is false and aborts the run."""

    return {"trace": {
        "trigger/0": [{"path": "trigger/0"}],
        "action/0": [{"path": "action/0", "result": {"choice": "default"}}],
        "action/0/choose/0": [{"path": "action/0/choose/0", **FALSE}],
        "action/0/choose/0/conditions/0": [{"path": "action/0/choose/0/conditions/0", **FALSE}],
        "action/0/default/0": [{"path": "action/0/default/0", "result": {"params": {}}}],
        "action/1": [{"path": "action/1", **FALSE}],
    }}


class ConditionStopStepTests(unittest.TestCase):
    def stop(self, last_step, script_execution, detail, **extra):
        return _normalize_trace(summary(last_step, script_execution, **extra), detail)["condition_stop_step"]

    def test_a_condition_action_that_aborts_is_the_stop_not_a_routed_branch(self):
        self.assertEqual(self.stop("action/1", "aborted", routed_past_then_stopped()), "action/1")

    def test_an_automation_condition_that_fails_is_the_stop(self):
        detail = {"trace": {"trigger/0": [{}], "condition/0": [TRUE], "condition/1": [FALSE]}}
        self.assertEqual(self.stop("condition/1", "failed_conditions", detail), "condition/1")

    def test_a_false_branch_condition_in_a_finished_run_is_not_a_stop(self):
        # A choose with no default as the final action: nothing matched, and
        # the run finished at the false option's condition.
        detail = {"trace": {
            "trigger/0": [{}],
            "action/0": [{"result": {}}],
            "action/0/choose/0": [FALSE],
            "action/0/choose/0/conditions/0": [FALSE],
        }}
        self.assertIsNone(self.stop("action/0/choose/0/conditions/0", "finished", detail))
        self.assertIsNone(self.stop("action/1", "finished", routed_past_then_stopped()))

    def test_an_abort_that_is_not_a_condition_is_not_a_condition_stop(self):
        trace = routed_past_then_stopped()["trace"]
        for result in ({"stop": "Synthetic stop", "error": True},
                       {"wait": {"remaining": 0.0, "completed": False}}):
            with self.subTest(result=result):
                detail = {"trace": {**trace, "action/1": [{"path": "action/1", "result": result}]}}
                self.assertIsNone(self.stop("action/1", "aborted", detail))

    def test_a_condition_that_errored_is_an_error_not_a_stop(self):
        detail = {"trace": {"condition/0": [{"error": "Synthetic template error", **FALSE}]}}
        self.assertIsNone(self.stop("condition/0", "failed_conditions", detail))

    def test_without_the_step_entry_only_a_condition_step_name_counts(self):
        self.assertEqual(self.stop("condition/0", "failed_conditions", None), "condition/0")
        self.assertEqual(self.stop("condition/0", "aborted", {"trace": {}}), "condition/0")
        self.assertIsNone(self.stop("action/1", "aborted", None))
        for execution in ("finished", "cancelled", "error", None):
            with self.subTest(script_execution=execution):
                self.assertIsNone(self.stop("condition/0", execution, None))

    def test_a_compound_check_stops_at_the_enclosing_condition(self):
        # Core 2026.11.3: a not condition is false when its inner check is
        # true; the outer condition records the false result, and the inner
        # key, an ancestor's child, is last_step.
        automation = {"trace": {"trigger/0": [{}], "condition/0": [FALSE],
                                "condition/0/conditions/0": [TRUE]}}
        self.assertEqual(self.stop("condition/0/conditions/0", "failed_conditions", automation),
                         "condition/0")
        action = {"trace": {**routed_past_then_stopped()["trace"], "action/1/conditions/0": [TRUE]}}
        self.assertEqual(self.stop("action/1/conditions/0", "aborted", action), "action/1")
        # A numeric_state that returns false early sets no child result.
        numeric = {"trace": {"condition/0": [FALSE], "condition/0/entity_id/0": [{"path": "x"}]}}
        self.assertEqual(self.stop("condition/0/entity_id/0", "failed_conditions", numeric), "condition/0")
        # A child that records its own false result is the deepest stop.
        child = {"trace": {"condition/0": [FALSE], "condition/0/entity_id/0": [FALSE]}}
        self.assertEqual(self.stop("condition/0/entity_id/0", "failed_conditions", child),
                         "condition/0/entity_id/0")

    def test_a_false_condition_off_the_stopping_path_is_not_the_stop(self):
        # An if whose condition is false routes to else, which waits and aborts.
        routed = {"trace": {
            "action/0": [{"result": {"choice": "else"}}],
            "action/0/if/condition/0": [FALSE],
            "action/0/else/0": [{"result": {"wait": {"remaining": 0.0}, "timeout": True}}],
        }}
        self.assertIsNone(self.stop("action/0/else/0", "aborted", routed))
        # action/1 is not an ancestor of action/10.
        sibling = {"trace": {"action/1": [FALSE], "action/10": [{"result": {"wait": {}}}]}}
        self.assertIsNone(self.stop("action/10", "aborted", sibling))
        # The last run of a repeated step decides, not an earlier one.
        repeated = {"trace": {"action/0": [FALSE, {"result": {"wait": {}}}]}}
        self.assertIsNone(self.stop("action/0", "aborted", repeated))

    def test_an_earlier_or_nested_value_error_does_not_hide_the_stop(self):
        earlier = {"trace": {"action/0": [{"error": "Synthetic earlier failure"}],
                             "condition/1": [FALSE]}}
        self.assertEqual(self.stop("condition/1", "failed_conditions", earlier), "condition/1")
        nested = {"trace": {"condition/0": [{**FALSE, "changed_variables": {"response": {"error": "busy"}}}]}}
        self.assertEqual(self.stop("condition/0", "failed_conditions", nested), "condition/0")
        normalized = _normalize_trace(summary("condition/1", "failed_conditions"), earlier)
        self.assertEqual(normalized["error"], "Synthetic earlier failure")

    def test_an_error_within_the_stopping_condition_or_the_run_is_not_a_stop(self):
        inner = {"trace": {"condition/0": [FALSE],
                           "condition/0/conditions/0": [{"error": "Synthetic inner error"}]}}
        self.assertIsNone(self.stop("condition/0/conditions/0", "failed_conditions", inner))
        detail = {"trace": {"condition/0": [FALSE]}}
        self.assertIsNone(self.stop("condition/0", "failed_conditions", detail, error="Synthetic run error"))

    def test_the_live_case_reaches_the_rule_and_the_incident_at_the_stop(self):
        traces = [
            _normalize_trace(summary("action/1", "aborted", run_id=run_id,
                                     timestamp=f"2026-07-12T1{index}:00:00Z"), routed_past_then_stopped())
            for index, run_id in enumerate(("r1", "r2"))
        ]
        finding = next(item for item in evaluate_rules(bundle(traces=traces))
                       if item.rule_id == "repeated_condition_stop")
        self.assertEqual(finding.trace_step, "action/1")
        events = normalize_traces(traces[:1], "automation.synthetic", {})
        stopped = [item for item in events if item.event_type == "automation_condition_failed"]
        self.assertEqual([item.summary for item in stopped], ["Trace stopped at condition action/1."])


if __name__ == "__main__":
    unittest.main()
