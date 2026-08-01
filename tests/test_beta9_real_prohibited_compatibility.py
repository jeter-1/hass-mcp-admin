import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    StepExecutionStatus,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    BETA6_PROHIBITED_COMPAT_CONTRACT_VERSION,
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
    ChangePlanStorageError,
)
from ha_mcp_engineering.governance.task_models import (  # noqa: E402
    new_execution_task,
)
from ha_mcp_engineering.governance.task_storage import (  # noqa: E402
    ExecutionTaskStorageError,
)
from ha_mcp_engineering.handoff.provider import (  # noqa: E402
    EngineeringHandoffProvider,
)
from tests.test_beta25_external_approval import (  # noqa: E402
    Clock,
    FakeGateway,
    RuntimeShim,
)


FIXTURE_ROOT = ROOT / "tests" / "fixtures"
PROVENANCE_PATH = (
    FIXTURE_ROOT
    / "beta6_prohibited_superseded_contract_v2_provenance.json"
)
FIXTURE_PATHS = (
    FIXTURE_ROOT / "beta6_prohibited_superseded_contract_v2_a.json",
    FIXTURE_ROOT / "beta6_prohibited_superseded_contract_v2_b.json",
)
GENERATOR_PATH = (
    ROOT / "scripts" / "generate_beta6_prohibited_compatibility_fixtures.py"
)
BETA6_SOURCE_COMMIT = "5c7eebf962837f85f2309b1b5099401fb075cd6e"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RealBeta6FixtureProvenanceTests(unittest.TestCase):
    def test_fixture_provenance_binds_exact_beta6_writer_and_bytes(self):
        provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            provenance["historical_source_commit"], BETA6_SOURCE_COMMIT
        )
        self.assertEqual(
            provenance["generator_sha256"], _sha256(GENERATOR_PATH)
        )
        by_path = {item["path"]: item for item in provenance["fixtures"]}
        for path in FIXTURE_PATHS:
            relative = path.relative_to(ROOT).as_posix()
            self.assertEqual(by_path[relative]["sha256"], _sha256(path))
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(value["contract_version"], 2)
            self.assertTrue(value["operations"])
            self.assertEqual(
                {operation["execution_status"] for operation in value["operations"]},
                {"pending"},
            )
            for operation in value["operations"]:
                self.assertIsNone(operation["execution_receipt"])
                self.assertIsNone(operation["post_apply_fingerprint"])
                self.assertIsNone(operation["failure_information"])
                self.assertEqual(operation["verification"]["status"], "not_run")


class RealBeta6CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "plans"
        self.repository = ChangePlanRepository(self.root)
        self.service = ChangeGovernanceService(
            self.repository,
            FakeGateway(),
            now=Clock(),
        )

    def tearDown(self):
        self.temporary.cleanup()

    def _write_value(self, value: dict) -> tuple[str, Path]:
        raw = json.dumps(
            value, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        plan_id = value["plan_id"]
        path = self.root / f"{plan_id}.json"
        path.write_bytes(raw)
        return plan_id, path

    def _write(self, fixture_path: Path) -> tuple[str, Path]:
        raw = fixture_path.read_bytes()
        plan_id = json.loads(raw)["plan_id"]
        path = self.root / f"{plan_id}.json"
        path.write_bytes(raw)
        return plan_id, path

    def _fixture(self) -> dict:
        return json.loads(FIXTURE_PATHS[0].read_text(encoding="utf-8"))

    def _remove(self, path: Path) -> None:
        path.unlink()

    def _assert_refused(
        self,
        value: dict,
        expected_code: ErrorCode = ErrorCode.APPROVAL_SEQUENCE_FAILURE,
    ) -> None:
        plan_id, path = self._write_value(value)
        before = path.read_bytes()
        with self.assertRaises(GovernanceError) as raised:
            self.service.get_plan(plan_id)
        self.assertEqual(raised.exception.code, expected_code)
        self.assertEqual(path.read_bytes(), before)
        self.assertIsNone(self.service.task_repository.get_for_plan(plan_id))
        self.assertEqual(self.service.gateway.writes, 0)
        self._remove(path)

    def test_real_beta6_contract_v2_shape_is_recognized_without_mutation(self):
        self.assertEqual(BETA6_PROHIBITED_COMPAT_CONTRACT_VERSION, 2)
        for fixture_path in FIXTURE_PATHS:
            with self.subTest(fixture=fixture_path.name):
                plan_id, persisted_path = self._write(fixture_path)
                before = persisted_path.read_bytes()
                plan = self.repository.get(plan_id)
                self.assertIsNotNone(plan)
                assert plan is not None
                self.assertEqual(
                    self.service._effective_prohibited_plan_failures(plan),
                    (),
                )
                public = self.service.get_plan(plan_id)
                self.assertEqual(public["status"], "prohibited")
                self.assertEqual(public["approval"]["state"], "prohibited")
                self.assertEqual(public["approval_lifecycle"], "prohibited")
                self.assertEqual(public["approval_bundle_state"], "prohibited")
                self.assertFalse(public["approval_actionable"])
                self.assertEqual(
                    public["policy_decision"]["required_acknowledgements"],
                    [],
                )
                self.assertFalse(public["approval_challenge_created"])
                self.assertFalse(public["apply_allowed"])
                self.assertIsNone(public["next_required_operation"])
                self.assertEqual(persisted_path.read_bytes(), before)
                persisted_path.unlink()

    def test_contract_v2_prepared_operations_are_not_execution_evidence(self):
        plan_id, _path = self._write(FIXTURE_PATHS[0])
        plan = self.repository.get(plan_id)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.contract_version, 2)
        self.assertTrue(plan.operations)
        for operation in plan.operations:
            self.assertEqual(
                operation.execution_status,
                StepExecutionStatus.PENDING,
            )
            self.assertIsNone(operation.execution_receipt)
            self.assertIsNone(operation.snapshot)
            self.assertEqual(operation.verification.status, "not_run")
        self.assertFalse(
            self.service._prohibited_plan_has_execution_evidence(plan)
        )

    def test_contradictory_contract_v2_records_remain_fail_closed(self):
        cases: list[tuple[str, dict, ErrorCode]] = []

        acknowledgements = self._fixture()
        acknowledgements["policy_decision"]["required_acknowledgements"] = [
            "plan_approval"
        ]
        cases.append(
            (
                "nonempty_acknowledgements",
                acknowledgements,
                ErrorCode.POLICY_SNAPSHOT_MISMATCH,
            )
        )

        granted = self._fixture()
        granted["approval"]["state"] = "approved"
        granted["approval"]["approved_at"] = granted["updated_at"]
        cases.append(("granted_approval", granted, ErrorCode.APPROVAL_SEQUENCE_FAILURE))

        consumed = self._fixture()
        consumed["approval"]["state"] = "consumed"
        consumed["approval"]["bundle_state"] = "consumed"
        consumed["approval"]["consumed_at"] = consumed["updated_at"]
        cases.append(("consumed_approval", consumed, ErrorCode.APPROVAL_SEQUENCE_FAILURE))

        challenge = self._fixture()
        challenge["approval"]["challenge_id"] = "synthetic-challenge"
        challenge["approval"]["challenge_requested_at"] = challenge["updated_at"]
        cases.append(("challenge", challenge, ErrorCode.APPROVAL_SEQUENCE_FAILURE))

        applying = self._fixture()
        applying["operations"][0]["execution_status"] = "applying"
        cases.append(("operation_applying", applying, ErrorCode.APPROVAL_SEQUENCE_FAILURE))

        receipt = self._fixture()
        receipt["operations"][0]["execution_receipt"] = {
            "provider_operation_id": "synthetic-provider-operation",
            "response_received": True,
        }
        cases.append(("provider_receipt", receipt, ErrorCode.APPROVAL_SEQUENCE_FAILURE))

        applied = self._fixture()
        applied["status"] = "applied"
        applied["applied_at"] = applied["updated_at"]
        cases.append(("successful_apply", applied, ErrorCode.APPROVAL_SEQUENCE_FAILURE))

        verification = self._fixture()
        verification["operations"][0]["verification"]["status"] = "verified"
        verification["operations"][0]["verification"]["checked_at"] = (
            verification["updated_at"]
        )
        cases.append(("verification", verification, ErrorCode.APPROVAL_SEQUENCE_FAILURE))

        rollback = self._fixture()
        rollback["rollback"]["requested_at"] = rollback["updated_at"]
        cases.append(("rollback", rollback, ErrorCode.APPROVAL_SEQUENCE_FAILURE))

        policy_hash = self._fixture()
        policy_hash["policy_decision"]["policy_decision_hash"] = "0" * 64
        policy_hash["approval"]["policy_decision_hash"] = "0" * 64
        cases.append(("policy_hash", policy_hash, ErrorCode.POLICY_SNAPSHOT_MISMATCH))

        plan_hash = self._fixture()
        plan_hash["operations"][0]["proposed_config_hash"] = "1" * 64
        cases.append(("plan_hash", plan_hash, ErrorCode.POLICY_SNAPSHOT_MISMATCH))

        disallowed_event = self._fixture()
        disallowed_event["events"].append(
            {
                "caller_id": "synthetic-caller",
                "duration_ms": None,
                "error_code": None,
                "event": "configuration_operation_started",
                "operation_id": disallowed_event["operations"][0]["operation_id"],
                "operation_order": 0,
                "request_id": "synthetic-request",
                "resource_id": disallowed_event["operations"][0]["target_id"],
                "resource_type": "automation",
                "result_status": "success",
                "timestamp": disallowed_event["updated_at"],
            }
        )
        cases.append(("disallowed_event", disallowed_event, ErrorCode.APPROVAL_SEQUENCE_FAILURE))

        wrong_code = self._fixture()
        wrong_code["events"][-1]["error_code"] = "synthetic_wrong_code"
        cases.append(("wrong_event_code", wrong_code, ErrorCode.APPROVAL_SEQUENCE_FAILURE))

        unexpected_state = self._fixture()
        unexpected_state["operations"][0]["execution_status"] = (
            "not_attempted_dependency_failure"
        )
        cases.append(("unexpected_execution_state", unexpected_state, ErrorCode.APPROVAL_SEQUENCE_FAILURE))

        for name, value, expected_code in cases:
            with self.subTest(case=name):
                self._assert_refused(value, expected_code)

    def test_execution_task_contradiction_is_rejected(self):
        plan_id, path = self._write(FIXTURE_PATHS[0])
        public_plan = self.repository.get(plan_id)
        self.assertIsNotNone(public_plan)
        assert public_plan is not None
        task = new_execution_task(
            task_id="c" * 32,
            plan_id=plan_id,
            plan_hash=self.service.plan_hash(public_plan),
            operation="configuration_plan",
            target={
                "target_type": "configuration_plan",
                "target_id": plan_id,
            },
            timestamp=public_plan.updated_at,
            execution_request_id="synthetic-beta9-request",
            idempotency_key="d" * 64,
            approval_reference={"authority_version": 3},
            legacy_projection={},
        )
        self.service.task_repository.save(task)
        before = path.read_bytes()
        with self.assertRaises(GovernanceError) as raised:
            self.service.get_plan(plan_id)
        self.assertEqual(
            raised.exception.code,
            ErrorCode.APPROVAL_SEQUENCE_FAILURE,
        )
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.service.gateway.writes, 0)

    def test_task_storage_errors_propagate_from_clause_evaluation(self):
        plan_id, _path = self._write(FIXTURE_PATHS[0])
        plan = self.repository.get(plan_id)
        self.assertIsNotNone(plan)
        assert plan is not None
        with patch.object(
            self.service.task_repository,
            "get_for_plan",
            side_effect=ExecutionTaskStorageError("synthetic storage error"),
        ):
            with self.assertRaises(GovernanceError) as raised:
                self.service._effective_prohibited_plan_failures(plan)
        self.assertEqual(
            raised.exception.code,
            ErrorCode.EXECUTION_TASK_STORAGE_ERROR,
        )

    def test_partial_listing_reports_projection_failure_without_hiding_valid_plan(self):
        valid_id, valid_path = self._write(FIXTURE_PATHS[0])
        invalid = json.loads(FIXTURE_PATHS[1].read_text(encoding="utf-8"))
        invalid["approval"]["state"] = "consumed"
        invalid["approval"]["bundle_state"] = "consumed"
        invalid["approval"]["consumed_at"] = invalid["updated_at"]
        invalid_id, invalid_path = self._write_value(invalid)
        before = {
            valid_id: valid_path.read_bytes(),
            invalid_id: invalid_path.read_bytes(),
        }

        unfiltered = self.service.list_plans(limit=100)
        self.assertEqual([item["plan_id"] for item in unfiltered["plans"]], [valid_id])
        self.assertTrue(unfiltered["partial"])
        self.assertEqual(unfiltered["projection_failure_count"], 1)
        self.assertEqual(
            unfiltered["projection_failures"],
            [
                {
                    "plan_id": invalid_id,
                    "error_code": "approval_sequence_failure",
                }
            ],
        )
        self.assertFalse(unfiltered["projection_failures_truncated"])

        prohibited = self.service.list_plans(status="prohibited", limit=100)
        self.assertEqual(
            [item["plan_id"] for item in prohibited["plans"]],
            [valid_id],
        )
        self.assertEqual(prohibited["projection_failure_count"], 1)
        awaiting = self.service.list_plans(
            status="awaiting_approval", limit=100
        )
        self.assertEqual(awaiting["plans"], [])
        self.assertEqual(awaiting["projection_failure_count"], 1)

        health = self.service.health_summary()
        self.assertEqual(health["total_plans"], 2)
        self.assertEqual(health["plans_by_policy_class"]["prohibited"], 1)
        self.assertEqual(
            health["plans_by_policy_class"]["projection_failed"], 1
        )
        self.assertEqual(health["projection_failure_count"], 1)
        self.assertEqual(
            health["projection_failure_warning"],
            "one_or_more_governance_plans_could_not_be_projected",
        )
        self.assertTrue(health["policy_class_accounting_valid"])
        self.assertEqual(
            sum(health["plans_by_policy_class"].values()),
            health["total_plans"],
        )
        self.assertEqual(health["plans_awaiting_approval"], 0)
        self.assertEqual(health["plans_requiring_approval"], 0)
        self.assertEqual(health["pending_plan_approvals"], 0)
        self.assertEqual(health["pending_elevated_acknowledgements"], 0)
        self.assertEqual(self.service.pending_external_reviews(), [])

        recovered = ChangeGovernanceService(
            self.repository,
            self.service.gateway,
            now=Clock(),
        )
        self.assertEqual(recovered.pending_external_reviews(), [])
        recovered_health = recovered.health_summary()
        self.assertEqual(recovered_health["projection_failure_count"], 1)
        self.assertTrue(recovered_health["policy_class_accounting_valid"])
        self.assertEqual(valid_path.read_bytes(), before[valid_id])
        self.assertEqual(invalid_path.read_bytes(), before[invalid_id])
        self.assertEqual(self.service.gateway.writes, 0)

    def test_multiple_projection_failures_are_deterministic_and_bounded(self):
        expected_ids = []
        for fixture_path in reversed(FIXTURE_PATHS):
            value = json.loads(fixture_path.read_text(encoding="utf-8"))
            value["approval"]["state"] = "consumed"
            value["approval"]["bundle_state"] = "consumed"
            value["approval"]["consumed_at"] = value["updated_at"]
            plan_id, _path = self._write_value(value)
            expected_ids.append(plan_id)
        listed = self.service.list_plans(limit=100)
        self.assertEqual(listed["plans"], [])
        self.assertEqual(listed["projection_failure_count"], 2)
        self.assertEqual(
            [item["plan_id"] for item in listed["projection_failures"]],
            sorted(expected_ids),
        )
        self.assertTrue(listed["partial"])
        self.assertFalse(listed["projection_failures_truncated"])

    def test_projection_failure_details_are_bounded(self):
        value = self._fixture()
        plan_id, _path = self._write_value(value)
        plan = self.repository.get(plan_id)
        self.assertIsNotNone(plan)
        assert plan is not None
        failures = []
        for index in range(25):
            projected = copy.copy(plan)
            projected.plan_id = f"{index:032x}"
            failures.append(
                (projected, ErrorCode.APPROVAL_SEQUENCE_FAILURE)
            )
        with patch.object(
            self.service,
            "_resolved_plans_with_projection_failures",
            return_value=([], failures),
        ):
            listed = self.service.list_plans(limit=100)
        self.assertEqual(listed["projection_failure_count"], 25)
        self.assertEqual(len(listed["projection_failures"]), 20)
        self.assertTrue(listed["projection_failures_truncated"])
        self.assertTrue(listed["partial"])

    def test_systemic_plan_storage_failure_is_not_partial_success(self):
        with patch.object(
            self.repository,
            "list",
            side_effect=ChangePlanStorageError("synthetic unavailable"),
        ):
            with self.assertRaises(GovernanceError) as raised:
                self.service.list_plans(limit=100)
        self.assertEqual(
            raised.exception.code,
            ErrorCode.CHANGE_PLAN_STORAGE_ERROR,
        )


class ProjectionFailureHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_handoff_keeps_valid_plans_and_marks_projection_coverage_partial(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "plans"
            repository = ChangePlanRepository(root)
            gateway = FakeGateway()
            service = ChangeGovernanceService(
                repository,
                gateway,
                now=Clock(),
            )
            valid = FIXTURE_PATHS[0].read_bytes()
            valid_id = json.loads(valid)["plan_id"]
            valid_path = root / f"{valid_id}.json"
            valid_path.write_bytes(valid)
            invalid = json.loads(
                FIXTURE_PATHS[1].read_text(encoding="utf-8")
            )
            invalid["approval"]["state"] = "consumed"
            invalid["approval"]["bundle_state"] = "consumed"
            invalid["approval"]["consumed_at"] = invalid["updated_at"]
            invalid_id = invalid["plan_id"]
            invalid_path = root / f"{invalid_id}.json"
            invalid_path.write_text(
                json.dumps(invalid, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            before = {
                valid_id: valid_path.read_bytes(),
                invalid_id: invalid_path.read_bytes(),
            }
            provider = EngineeringHandoffProvider(
                governance=RuntimeShim(service),
                incident=None,
                dependency_index=SimpleNamespace(),
                rest_client=None,
                health=None,
            )
            operational_reconciliation = (
                await service.reconcile_operational_plans(
                    trigger="startup"
                )
            )
            self.assertEqual(operational_reconciliation["checked"], 0)
            self.assertEqual(operational_reconciliation["pending"], 0)
            self.assertEqual(operational_reconciliation["failed"], 0)
            bundle = await provider._collect(
                {
                    "include_runtime_health": False,
                    "include_governance_context": True,
                    "include_dependency_context": False,
                    "include_integrity_context": False,
                    "include_reliability_context": False,
                    "include_incident_context": False,
                    "include_recommendations": False,
                    "focus_entity_ids": [],
                    "automation_ids": [],
                    "change_plan_ids": [valid_id, invalid_id],
                    "lookback_hours": 168,
                    "refresh_index": False,
                }
            )
            item = next(
                item
                for item in bundle.items
                if valid_id in item.change_plan_ids
            )
            self.assertEqual(item.status, "not_applicable")
            self.assertFalse(item.requires_authorization)
            failed_item = next(
                item
                for item in bundle.items
                if invalid_id in item.change_plan_ids
            )
            self.assertFalse(failed_item.requires_authorization)
            self.assertEqual(failed_item.authorization_type, "none")
            coverage = next(
                item
                for item in bundle.coverage
                if item.source_type == "governance_plans"
            )
            self.assertEqual(coverage.completeness, "partial")
            self.assertEqual(coverage.failed_items, 1)
            self.assertEqual(
                coverage.failure_category,
                "governance_projection_failure",
            )
            self.assertEqual(valid_path.read_bytes(), before[valid_id])
            self.assertEqual(invalid_path.read_bytes(), before[invalid_id])
            self.assertEqual(gateway.writes, 0)


if __name__ == "__main__":
    unittest.main()
