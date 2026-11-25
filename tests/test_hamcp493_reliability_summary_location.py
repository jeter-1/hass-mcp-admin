"""HAMCP-493: summary root causes carry their current state and trace step.

At ``detail_level="summary"`` the single tool and the batch digest dropped the
group's ``affected_step``. A concurrency rejection cites its step only on its
evidence, and a disabled automation had no ``current_state``.
"""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))
sys.path.insert(0, str(ROOT / "tests"))

from ha_mcp_engineering.reliability import batch as batch_module  # noqa: E402
from ha_mcp_engineering.reliability.batch import ReliabilityBatchAnalyzer  # noqa: E402
from ha_mcp_engineering.reliability.rules import build_root_cause_groups, evaluate_rules  # noqa: E402
from ha_mcp_engineering.reliability.runtime import RELIABILITY_ANALYSIS  # noqa: E402
from ha_mcp_engineering.reliability.service import AutomationReliabilityAnalysisService  # noqa: E402
from ha_mcp_engineering.tools.analysis import (  # noqa: E402
    automation_reliability_analysis,
    automation_reliability_batch_analysis,
)

from test_automation_reliability_analysis import AUTOMATION_ID, FakeProvider, bundle  # noqa: E402
from test_hamcp229_reliability_batch import PerIdProvider, service_for  # noqa: E402


INSTANT = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
GUIDE = ROOT / "docs" / "AUTOMATION_RELIABILITY_ANALYSIS.md"

TRACES = [
    {"run_id": "1", "timestamp": "2026-07-12T10:00:00Z", "last_step": "action/0", "failure_step": "action/0",
     "error": "service failed", "action_error": True, "affected_dependency": "light.kitchen"},
    {"run_id": "2", "timestamp": "2026-07-12T11:00:00Z", "last_step": "action/0", "failure_step": "action/0",
     "error": "service failed", "action_error": True, "affected_dependency": "light.kitchen"},
    {"run_id": "3", "timestamp": "2026-07-12T10:30:00Z", "last_step": "condition/0",
     "condition_stop_step": "condition/0", "error": None},
    {"run_id": "4", "timestamp": "2026-07-12T11:30:00Z", "last_step": "condition/0",
     "condition_stop_step": "condition/0", "error": None},
    {"run_id": "5", "timestamp": "2026-07-12T11:40:00Z", "last_step": "action/1", "failure_step": "action/1",
     "error": "Already running"},
]
REFERENCES = [
    {"entity_id": "sensor.gone", "status": "missing", "config_path": "$.action[0].target.entity_id",
     "relation": "action"},
]
LOGS = [{"identity": "log1", "timestamp": "2026-07-12T11:00:00Z", "summary": "Synthetic correlated entry."}]


def every_rule_bundle(state="off"):
    return bundle(state=state, traces=TRACES, references=REFERENCES, logs=LOGS)


def run(coro):
    return asyncio.run(coro)


def summary_groups(value):
    output = run(AutomationReliabilityAnalysisService(FakeProvider(value), clock=lambda: INSTANT).analyze(
        automation_id=AUTOMATION_ID, detail_level="summary",
    ))
    findings = evaluate_rules(value)
    rules = {}
    for item in findings:
        rules.setdefault(item.root_cause_group_id, set()).add(item.rule_id)
    return {
        frozenset(rules[group["root_cause_group_id"]]): group
        for group in output.data["root_cause_groups"]
    }, findings


class SingleToolSummaryTests(unittest.TestCase):
    def setUp(self):
        self.groups, self.findings = summary_groups(every_rule_bundle())

    def group(self, *rule_ids):
        return self.groups[frozenset(rule_ids)]

    def test_trace_failure_groups_carry_their_step(self):
        group = self.group("repeated_action_error", "repeated_trace_failure")
        self.assertEqual(group["affected_step"], "action/0")
        self.assertEqual(group["affected_dependency"], "light.kitchen")

    def test_a_condition_stop_carries_its_condition_step(self):
        self.assertEqual(self.group("repeated_condition_stop")["affected_step"], "condition/0")

    def test_a_concurrency_rejection_takes_its_step_from_the_evidence(self):
        finding = next(item for item in self.findings if item.rule_id == "mode_concurrency_conflict")
        # The finding itself names no step; only its trace evidence does.
        self.assertIsNone(finding.trace_step)
        group_model = next(
            item for item in build_root_cause_groups(self.findings)
            if item.root_cause_group_id == finding.root_cause_group_id
        )
        self.assertIsNone(group_model.affected_step)
        self.assertEqual(self.group("mode_concurrency_conflict")["affected_step"], "action/1")

    def test_a_disabled_automation_reports_its_state(self):
        self.assertEqual(self.group("automation_disabled")["current_state"], "disabled")

    def test_an_enabled_automation_has_no_disabled_group(self):
        groups, _ = summary_groups(every_rule_bundle(state="on"))
        self.assertNotIn(frozenset({"automation_disabled"}), groups)
        self.assertTrue(all(group["current_state"] != "disabled" for group in groups.values()))

    def test_entity_state_and_path_are_unchanged(self):
        group = self.group("missing_referenced_entity")
        self.assertEqual(group["current_state"], "missing")
        self.assertEqual(group["representative_configuration_paths"], ["$.action[0].target.entity_id"])
        self.assertIsNone(group["affected_step"])

    def test_evidence_without_a_location_invents_none(self):
        group = self.group("correlated_system_log_error")
        self.assertIsNone(group["current_state"])
        self.assertEqual(group["representative_configuration_paths"], [])
        self.assertIsNone(group["affected_step"])

    def test_every_summary_group_has_the_key(self):
        for rules, group in self.groups.items():
            with self.subTest(rules=sorted(rules)):
                self.assertIn("affected_step", group)

    def test_standard_groups_keep_the_group_model_shape(self):
        value = every_rule_bundle()
        output = run(AutomationReliabilityAnalysisService(FakeProvider(value), clock=lambda: INSTANT).analyze(
            automation_id=AUTOMATION_ID, detail_level="standard",
        ))
        expected = [group.public() for group in build_root_cause_groups(evaluate_rules(value))]
        self.assertEqual(output.data["root_cause_groups"], expected)
        for group in output.data["root_cause_groups"]:
            self.assertNotIn("current_state", group)


class BatchSummaryTests(unittest.TestCase):
    def test_batch_summaries_carry_the_single_tools_step_and_state(self):
        provider = PerIdProvider(default=every_rule_bundle())
        with patch.object(RELIABILITY_ANALYSIS, "service", service_for(provider)):
            single = json.loads(run(automation_reliability_analysis(
                automation_id="steps", lookback_hours=24, trace_limit=5, detail_level="summary",
            )))
            batch = json.loads(run(automation_reliability_batch_analysis(
                automation_ids=["steps"], lookback_hours=24, trace_limit=5,
            )))
        self.assertTrue(single["success"])
        self.assertEqual(batch["data"]["root_cause_detail"], "full")
        keys = batch_module._BASIC_ROOT_CAUSE_KEYS + batch_module._DETAIL_ROOT_CAUSE_KEYS
        self.assertIn("affected_step", keys)
        summaries = batch["data"]["items"][0]["root_cause_summaries"]
        groups = single["data"]["root_cause_groups"][:batch_module.MAX_ROOT_CAUSE_SUMMARIES]
        self.assertEqual(len(summaries), batch_module.MAX_ROOT_CAUSE_SUMMARIES)
        for summary, group in zip(summaries, groups):
            with self.subTest(group=group["root_cause_group_id"]):
                self.assertEqual(tuple(summary), keys)
                self.assertEqual(summary, {key: group[key] for key in keys})
        self.assertIn("action/0", [summary["affected_step"] for summary in summaries])

    def test_reduced_detail_still_drops_the_step(self):
        digest = {"root_cause_summaries": [
            {key: "x" for key in batch_module._BASIC_ROOT_CAUSE_KEYS + batch_module._DETAIL_ROOT_CAUSE_KEYS}
        ]}
        reduced = batch_module._reduced_digest(digest)["root_cause_summaries"][0]
        self.assertEqual(tuple(reduced), batch_module._BASIC_ROOT_CAUSE_KEYS)

    def test_the_batch_digest_is_bounded_by_the_existing_detail_budget(self):
        output = run(ReliabilityBatchAnalyzer().analyze(
            service_for(PerIdProvider(default=every_rule_bundle())), automation_ids=["bounded"],
        ))
        self.assertEqual(output.data["root_cause_detail"], "full")
        self.assertEqual(output.data["items"][0]["status"], "analyzed")


class GuideTests(unittest.TestCase):
    def test_the_guide_documents_the_step_and_the_disabled_state(self):
        text = " ".join(GUIDE.read_text(encoding="utf-8").split())
        self.assertIn("`affected_step`", text)
        self.assertIn("HAMCP-493", text)
        self.assertIn("`automation_disabled`", text)


if __name__ == "__main__":
    unittest.main()
