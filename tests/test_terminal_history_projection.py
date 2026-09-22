"""Useful terminal history reads with no restored approval or dispatch authority."""
from __future__ import annotations

import ast
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.errors import ErrorCode, GovernanceError
from ha_mcp_engineering.governance.historical_policy import (
    BETA38_TERMINAL_SOURCE_COMMIT, BETA4_TERMINAL_SOURCE_COMMIT,
    terminal_nonexecution_projection_match,
)
from ha_mcp_engineering.governance.models import ChangePlan
from ha_mcp_engineering.governance.service import ChangeGovernanceService
from ha_mcp_engineering.governance.storage import ChangePlanRepository
from ha_mcp_engineering.governance.task_storage import ExecutionTaskStorageError
from tests import test_beta34_historical_policy_projection as historical_tests

FIXTURES = ROOT / "tests/fixtures"
NAMES = ("expired_helper", "invalid_automation_f2_v1", "invalid_automation_f2_v2")


def raw(name):
    return (FIXTURES / ("terminal_" + name + ".json")).read_bytes()


def value(name):
    return json.loads(raw(name))


class TerminalHistoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plan_root = Path(self.tmp.name) / "plans"
        self.saved = {}
        for name in NAMES:
            data = value(name)
            directory = self.plan_root / "operational-administration-v3" if name == "expired_helper" else self.plan_root
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / (data["plan_id"] + ".json")
            path.write_bytes(raw(name))
            self.saved[name] = path
        self.gateway = historical_tests._NoWriteGateway()
        self.service = ChangeGovernanceService(
            ChangePlanRepository(self.plan_root), self.gateway,
            now=lambda: datetime(2026, 9, 22, tzinfo=timezone.utc),
        )

    def unchanged(self):
        for name, path in self.saved.items():
            self.assertEqual(path.read_bytes(), raw(name))
        self.assertEqual(self.gateway.calls, [])

    def test_exact_writer_provenance(self):
        provenance = json.loads((FIXTURES / "terminal_projection_provenance.json").read_bytes())
        self.assertEqual(provenance["generator_sha256"], hashlib.sha256((ROOT / provenance["generator"]).read_bytes()).hexdigest())
        self.assertFalse(provenance["production_records"])
        for row in provenance["fixtures"]:
            self.assertEqual(row["sha256"], hashlib.sha256((FIXTURES / row["fixture"]).read_bytes()).hexdigest())
            self.assertEqual(row["source_commit"], BETA4_TERMINAL_SOURCE_COMMIT if row["profile"].endswith("v2") else BETA38_TERMINAL_SOURCE_COMMIT)
            self.assertEqual(row["historical_read_error"], None if row["profile"] == "expired_helper" else "approval_sequence_failure")
            self.assertFalse(row["persisted_bytes_edited"])
            self.assertTrue(row["sanitized_before_writer"])
            self.assertEqual(row["provider_dispatch_count"], 0)
            self.assertEqual(row["execution_task_count"], 0)

    def test_get_list_and_health_preserve_useful_terminal_history(self):
        for name in NAMES:
            original = value(name)
            result = self.service.get_plan(original["plan_id"])
            self.assertEqual(result["status"], original["status"])
            self.assertEqual(result["policy_decision"], original["policy_decision"])
            self.assertEqual(result["proposed_config_hash"], original["proposed_config_hash"])
            self.assertEqual(result["approval"]["state"], original["approval"]["state"])
            self.assertFalse(result["apply_allowed"])
            self.assertFalse(result["approval_actionable"])
            self.assertIsNone(result["execution_task"]["task_id"])
            self.assertFalse(result["execution_task"]["provider_dispatch_occurred"])
        listed = self.service.list_plans(limit=10)
        self.assertEqual(listed["count"], 3)
        self.assertEqual(self.service.health_summary()["projection_failure_count"], 0)
        history = self.service.health_summary()["historical_policy_snapshot_compatibility"]
        self.assertEqual(history["compatible_count"], 3)
        self.assertEqual(history["authorization_effect"], "none_projection_only")
        self.assertEqual(sum(history["profile_counts"].values()), 3)
        self.assertEqual(
            self.service.deep_audit_plan_store()[
                "historical_policy_snapshot_compatibility"
            ], history,
        )
        restarted = ChangeGovernanceService(
            ChangePlanRepository(self.plan_root), self.gateway,
            now=lambda: datetime(2026, 9, 22, tzinfo=timezone.utc),
        )
        self.assertEqual(restarted.health_summary()[
            "historical_policy_snapshot_compatibility"
        ], history)
        self.unchanged()

    def test_mixed_integrity_failure_is_not_hidden_by_compatible_count(self):
        name = "invalid_automation_f2_v2"
        data = value(name)
        data["policy_decision"]["policy_subject_hash"] = "0" * 64
        self.saved[name].write_text(json.dumps(data))
        service = ChangeGovernanceService(
            ChangePlanRepository(self.plan_root), self.gateway,
            now=lambda: datetime(2026, 9, 22, tzinfo=timezone.utc),
        )
        with self.assertRaises(GovernanceError) as caught:
            service.get_plan(data["plan_id"])
        self.assertEqual(caught.exception.code, ErrorCode.POLICY_SNAPSHOT_MISMATCH)
        listed = service.list_plans(limit=10)
        self.assertTrue(listed["partial"])
        self.assertEqual(listed["count"], 2)
        self.assertEqual(listed["projection_failure_count"], 1)
        health = service.health_summary()
        self.assertEqual(health["projection_failure_count"], 1)
        self.assertEqual(health["historical_policy_snapshot_compatibility"][
            "compatible_count"
        ], 2)
        self.assertEqual(json.loads(self.saved[name].read_bytes()), data)
        self.assertEqual(self.gateway.calls, [])

    def test_summary_and_observability_preserve_configuration_and_secret_boundaries(self):
        for summary in self.service.list_plans(limit=10)["plans"]:
            self.assertNotIn("proposed_config", summary)
            self.assertNotIn("current_config", summary)
            self.assertNotIn("events", summary)
        for name in NAMES:
            plan = self.service.repository.get(value(name)["plan_id"])
            projection = self.service._public_plan_observability_projection(plan)
            self.assertNotIn("proposed_config", projection)
            self.assertNotIn("current_config", projection)
            self.assertNotIn("events", projection)
            self.assertNotIn("dry_run_results", projection)
            # Synthetic known-secret text remains subject to the existing
            # fail-closed observability sanitizer, including contract v1.
            with patch.object(self.service, "sensitive_values", (plan.title,)):
                with self.assertRaises(GovernanceError):
                    self.service._public_plan_observability_projection(plan)
        self.unchanged()

    async def test_all_authority_paths_still_refuse(self):
        for name in NAMES:
            ident = value(name)["plan_id"]
            plan = self.service.repository.get(ident)
            plan_hash = self.service.plan_hash(plan)
            for action in (
                lambda: self.service._require_policy_snapshot(plan),
                lambda: self.service.approve(ident, plan_hash),
            ):
                with self.assertRaises(GovernanceError):
                    action()
            with self.assertRaises(GovernanceError):
                await self.service.issue_external_csrf(ident, "synthetic-challenge")
            with self.assertRaises(GovernanceError):
                await self.service.decide_external_approval(
                    plan_id=ident, challenge_id="synthetic-challenge",
                    expected_plan_hash=plan_hash, approval_kind="apply",
                    csrf_nonce="synthetic-nonce", decision="approve",
                    approver_principal="synthetic-owner",
                )
            with self.assertRaises(GovernanceError):
                await self.service.rollback_change(ident, plan_hash)
            self.unchanged()
        for name in NAMES:
            ident = value(name)["plan_id"]
            plan = self.service.repository.get(ident)
            with self.assertRaises(GovernanceError) as caught:
                await self.service.apply(ident, self.service.plan_hash(plan))
            self.assertIsNone(self.service.task_repository.get_for_plan(ident))
            # The existing apply validator appends refusal evidence for these
            # automation records. Compatibility must not suppress that audit.
            after = json.loads(self.saved[name].read_bytes())
            before = value(name)
            if name == "expired_helper":
                self.assertEqual(after, before)
            else:
                event = after["events"].pop()
                self.assertEqual(event["event"], "change_apply_rejected")
                self.assertEqual(event["result_status"], "rejected")
                self.assertEqual(event["error_code"], caught.exception.code.value)
                after["updated_at"] = before["updated_at"]
                self.assertEqual(after, before)
                # An additional lifecycle is outside the exact writer shape.
                with self.assertRaises(GovernanceError):
                    self.service.get_plan(ident)
        self.assertEqual(self.gateway.calls, [])

    async def test_recovery_and_external_review_have_no_work_or_mutation(self):
        self.assertEqual(self.service.pending_external_reviews(), [])
        for name in NAMES:
            self.assertIsNone(self.service.pending_external_review(value(name)["plan_id"]))
        await self.service.reconcile_execution_tasks()
        await self.service.reconcile_operational_plans()
        self.assertEqual(self.service.task_repository.list(), [])
        self.unchanged()

    def test_task_presence_and_storage_failure_do_not_grant_compatibility(self):
        for name in NAMES:
            ident = value(name)["plan_id"]
            with patch.object(self.service.task_repository, "get_for_plan", return_value=object()):
                with self.assertRaises(GovernanceError):
                    self.service.get_plan(ident)
            with patch.object(self.service.task_repository, "get_for_plan", side_effect=ExecutionTaskStorageError("synthetic failure")):
                with self.assertRaises(GovernanceError) as caught:
                    self.service.get_plan(ident)
                self.assertEqual(caught.exception.code, ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        self.unchanged()

    def test_integrity_mutations_fail_public_reads(self):
        mutations = (
            lambda d: d["policy_decision"].update(policy_subject_hash="0" * 64),
            lambda d: d["policy_decision"].update(policy_decision_hash="0" * 64),
            lambda d: d["approval"].update(policy_decision_hash="0" * 64),
            lambda d: d.update(proposed_config_hash="0" * 64),
            lambda d: d["proposed_config"].update(synthetic_unbound="changed"),
        )
        for name in NAMES:
            for mutate in mutations:
                with self.subTest(name=name, mutation=mutate):
                    data = value(name); mutate(data)
                    plan = ChangePlan.from_dict(copy.deepcopy(data))
                    self.assertIsNone(terminal_nonexecution_projection_match(plan))
                    with patch.object(self.service.repository, "get", return_value=plan):
                        with self.assertRaises(GovernanceError):
                            self.service.get_plan(plan.plan_id)
        self.unchanged()

    def test_rehashed_and_unhashed_lifecycle_variants_remain_rejected(self):
        mutations = {
            "active": lambda d: d.update(status="awaiting_approval"),
            "contract": lambda d: d.update(contract_version=2),
            "operation": lambda d: d.update(operation="create_automation"),
            "normalization": lambda d: d.update(normalization_version=99),
            "approved": lambda d: d["approval"].update(state="approved"),
            "challenge": lambda d: d["approval"].update(challenge_id="synthetic-challenge"),
            "apply": lambda d: d.update(apply_request_id="synthetic-dispatch"),
            "snapshot": lambda d: d.update(failure_information={"synthetic": True}),
            "verification": lambda d: d["verification"].update(status="passed"),
            "rollback": lambda d: d["rollback"].update(request_id="synthetic-rollback"),
            "missing_event": lambda d: d.update(events=[]),
            "duplicate_event": lambda d: d["events"].append(copy.deepcopy(d["events"][0])),
            "foreign_event": lambda d: d["events"][0].update(event="change_apply_started"),
            "invalid_time": lambda d: d["events"][0].update(timestamp="invalid"),
            "future_event": lambda d: d["events"][0].update(timestamp="2027-01-01T00:00:00+00:00"),
            "operation_event": lambda d: d["events"][0].update(operation_id="synthetic-operation"),
            "naive_time": lambda d: d.update(created_at="2026-08-15T01:00:00"),
            "risk": lambda d: d["risk"].update(apply_allowed=True),
        }
        for name in NAMES:
            for label, mutate in mutations.items():
                with self.subTest(name=name, mutation=label):
                    data = value(name); mutate(data)
                    historical_tests.HistoricalPolicyProjectionTests._rebind_snapshot_hashes(data)
                    plan = ChangePlan.from_dict(copy.deepcopy(data))
                    self.assertIsNone(terminal_nonexecution_projection_match(plan))
                    with patch.object(self.service.repository, "get", return_value=plan):
                        with self.assertRaises(GovernanceError):
                            self.service.get_plan(plan.plan_id)
        self.unchanged()

    def test_family_specific_tampering_and_wrong_error_refuse(self):
        for name in NAMES:
            data = value(name)
            if name == "expired_helper":
                data["operational"]["dispatch"]["attempt_count"] = 1
            else:
                data["events"][0]["error_code"] = "configuration_conflict"
            self.assertIsNone(terminal_nonexecution_projection_match(ChangePlan.from_dict(data)))

    def test_helper_rehashed_dependency_dispatch_and_approval_variants_refuse(self):
        mutations = (
            lambda d: d["operational"]["baseline"]["dependency_risk"].update(model="unreviewed-model"),
            lambda d: d["operational"]["baseline"]["dependency_risk"].update(execution_eligible=True),
            lambda d: d["approval"]["elevated_risk_acknowledgement"].update(approver_principal="synthetic-owner"),
            lambda d: d["dry_run_results"].update(provider_dispatch_occurred=True),
            lambda d: d["operational"]["verification"].update(attempt_count=1),
            lambda d: d["events"].reverse(),
        )
        for mutate in mutations:
            data = value("expired_helper")
            mutate(data)
            historical_tests.HistoricalPolicyProjectionTests._rebind_snapshot_hashes(data)
            plan = ChangePlan.from_dict(data)
            self.assertIsNone(terminal_nonexecution_projection_match(plan))
            with patch.object(self.service.repository, "get", return_value=plan):
                with self.assertRaises(GovernanceError):
                    self.service.get_plan(plan.plan_id)
        self.unchanged()

    def test_new_matcher_has_only_projection_and_accounting_callers(self):
        source = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/governance/service.py"
        tree = ast.parse(source.read_text())
        calls = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any(isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id == "terminal_nonexecution_projection_match" for x in ast.walk(node)):
                    calls.add(node.name)
        self.assertEqual(calls, {"_require_projection_policy_snapshot", "_rebuild_projection_failure_index", "_update_projection_failure_index", "_build_health_summary"})


if __name__ == "__main__":
    unittest.main()
