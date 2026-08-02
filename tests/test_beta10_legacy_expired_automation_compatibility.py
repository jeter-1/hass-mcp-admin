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

from ha_mcp_engineering.approval_web import (  # noqa: E402
    create_approval_application,
)
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ChangePlan,
)
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    policy_snapshot_matches,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    BETA6_LEGACY_EXPIRED_AUTOMATION_CONTRACT_VERSION,
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
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
FIXTURE_PATHS = (
    FIXTURE_ROOT / "beta6_legacy_prohibited_expired_automation_a.json",
    FIXTURE_ROOT / "beta6_legacy_prohibited_expired_automation_b.json",
)
PROVENANCE_PATH = (
    FIXTURE_ROOT
    / "beta6_legacy_prohibited_expired_automation_provenance.json"
)
CONTRACT_V2_FIXTURE_PATHS = (
    FIXTURE_ROOT / "beta6_prohibited_superseded_contract_v2_a.json",
    FIXTURE_ROOT / "beta6_prohibited_superseded_contract_v2_b.json",
)
GENERATOR_PATH = (
    ROOT / "scripts" / "generate_beta6_prohibited_compatibility_fixtures.py"
)
BETA6_SOURCE_COMMIT = "5c7eebf962837f85f2309b1b5099401fb075cd6e"
EXPECTED_EVENT_SEQUENCES = (
    (
        ("change_plan_created", "success", None),
        ("change_plan_expired", "rejected", "change_plan_expired"),
    ),
    (
        ("change_plan_created", "success", None),
        (
            "policy_approval_rejected",
            "rejected",
            "prohibited_change",
        ),
        ("change_apply_rejected", "rejected", "prohibited_change"),
        ("change_plan_expired", "rejected", "change_plan_expired"),
    ),
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LegacyFixtureProvenanceTests(unittest.TestCase):
    def test_fixtures_bind_the_exact_beta6_legacy_writer(self):
        provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            provenance["historical_source_commit"], BETA6_SOURCE_COMMIT
        )
        self.assertEqual(
            provenance["generator_sha256"], _sha256(GENERATOR_PATH)
        )
        self.assertEqual(
            provenance["writer_path"],
            "ChangeGovernanceService.create_plan",
        )
        self.assertEqual(
            provenance["terminal_lifecycle"],
            "ChangeGovernanceService._expire_if_needed",
        )
        by_path = {item["path"]: item for item in provenance["fixtures"]}
        for index, path in enumerate(FIXTURE_PATHS):
            with self.subTest(fixture=path.name):
                relative = path.relative_to(ROOT).as_posix()
                self.assertEqual(by_path[relative]["sha256"], _sha256(path))
                value = json.loads(path.read_text(encoding="utf-8"))
                self.assertNotIn("contract_version", value)
                self.assertNotIn("operations", value)
                plan = ChangePlan.from_dict(value)
                self.assertEqual(plan.contract_version, 1)
                self.assertEqual(plan.plan_version, 1)
                self.assertEqual(plan.operation.value, "update_automation")
                self.assertEqual(plan.target_type, "automation")
                self.assertTrue(plan.target_id)
                self.assertNotEqual(plan.target_id, plan.plan_id)
                self.assertEqual(plan.operations, [])
                self.assertEqual(plan.status.value, "expired")
                self.assertEqual(
                    tuple(
                        (
                            event.event,
                            event.result_status,
                            event.error_code,
                        )
                        for event in plan.events
                    ),
                    EXPECTED_EVENT_SEQUENCES[index],
                )


class LegacyExpiredAutomationCompatibilityTests(
    unittest.IsolatedAsyncioTestCase
):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "plans"
        self.repository = ChangePlanRepository(self.root)
        self.gateway = FakeGateway()
        self.clock = Clock()
        self.service = ChangeGovernanceService(
            self.repository,
            self.gateway,
            now=self.clock,
        )

    async def asyncTearDown(self):
        self.temporary.cleanup()

    def _fixture(self, index: int = 0) -> dict:
        return json.loads(FIXTURE_PATHS[index].read_text(encoding="utf-8"))

    def _write_bytes(self, path: Path) -> tuple[str, Path]:
        raw = path.read_bytes()
        plan_id = json.loads(raw)["plan_id"]
        persisted = self.root / f"{plan_id}.json"
        persisted.write_bytes(raw)
        return plan_id, persisted

    def _write_value(self, value: dict) -> tuple[str, Path]:
        plan_id = value["plan_id"]
        persisted = self.root / f"{plan_id}.json"
        persisted.write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        return plan_id, persisted

    def _clear(self, plan_id: str) -> None:
        path = self.root / f"{plan_id}.json"
        if path.exists():
            path.unlink()

    def _assert_refused(
        self,
        value: dict,
        expected_code: ErrorCode = ErrorCode.APPROVAL_SEQUENCE_FAILURE,
    ) -> None:
        plan_id, path = self._write_value(value)
        before = path.read_bytes()
        task_count = len(self.service.task_repository.list())
        event_count = len(value.get("events") or [])
        loaded = self.repository.get(plan_id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertTrue(
            self.service._beta6_legacy_expired_automation_failures(loaded)
        )
        with self.assertRaises(GovernanceError) as raised:
            self.service.get_plan(plan_id)
        self.assertEqual(raised.exception.code, expected_code)
        self.assertEqual(path.read_bytes(), before)
        persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(persisted.get("events") or []), event_count)
        self.assertEqual(len(self.service.task_repository.list()), task_count)
        self.assertEqual(self.gateway.writes, 0)
        self._clear(plan_id)

    async def test_exact_legacy_profiles_project_prohibited_without_mutation(
        self,
    ):
        self.assertEqual(
            BETA6_LEGACY_EXPIRED_AUTOMATION_CONTRACT_VERSION,
            1,
        )
        for fixture_path in FIXTURE_PATHS:
            with self.subTest(fixture=fixture_path.name):
                plan_id, persisted_path = self._write_bytes(fixture_path)
                before = persisted_path.read_bytes()
                plan = self.repository.get(plan_id)
                self.assertIsNotNone(plan)
                assert plan is not None
                self.service._require_v2_persisted_plan_safe(plan)
                self.assertTrue(policy_snapshot_matches(plan))
                self.assertRegex(self.service.plan_hash(plan), r"^[0-9a-f]{64}$")
                self.assertEqual(
                    self.service._beta6_legacy_expired_automation_failures(
                        plan
                    ),
                    (),
                )
                self.assertEqual(
                    self.service._effective_prohibited_plan_failures(plan),
                    (),
                )
                public = self.service.get_plan(plan_id)
                self.assertEqual(public["status"], "prohibited")
                self.assertEqual(public["approval"]["state"], "prohibited")
                self.assertEqual(public["approval_lifecycle"], "prohibited")
                self.assertEqual(
                    public["approval_bundle_state"], "prohibited"
                )
                self.assertFalse(public["approval_actionable"])
                self.assertEqual(
                    public["policy_decision"]["required_acknowledgements"],
                    [],
                )
                self.assertFalse(public["approval_challenge_created"])
                self.assertFalse(public["apply_allowed"])
                self.assertIsNone(public["next_required_operation"])
                self.assertIsNone(public["execution_task"]["task_id"])
                self.assertEqual(persisted_path.read_bytes(), before)
                self.assertEqual(self.gateway.writes, 0)
                self._clear(plan_id)

    async def test_list_health_rehydration_and_ingress_are_read_only(self):
        paths = []
        before = {}
        event_evidence = {}
        for fixture_path in FIXTURE_PATHS:
            plan_id, path = self._write_bytes(fixture_path)
            paths.append((plan_id, path))
            before[plan_id] = path.read_bytes()
            value = json.loads(before[plan_id])
            event_evidence[plan_id] = [
                (event["event"], event["timestamp"])
                for event in value["events"]
            ]

        unfiltered = self.service.list_plans(limit=100)
        self.assertEqual(unfiltered["count"], 2)
        self.assertFalse(unfiltered["partial"])
        self.assertEqual(unfiltered["projection_failure_count"], 0)
        prohibited = self.service.list_plans(
            status="prohibited", limit=100
        )
        self.assertEqual(prohibited["count"], 2)
        awaiting = self.service.list_plans(
            status="awaiting_approval", limit=100
        )
        self.assertEqual(awaiting["count"], 0)
        self.assertFalse(awaiting["partial"])

        health = self.service.health_summary()
        self.assertEqual(health["total_plans"], 2)
        self.assertEqual(
            health["plans_by_policy_class"]["prohibited"], 2
        )
        self.assertEqual(health["prohibited_policy_decisions"], 2)
        self.assertEqual(
            health["plans_by_policy_class"]["projection_failed"], 0
        )
        self.assertEqual(health["projection_failure_count"], 0)
        self.assertTrue(health["policy_class_accounting_valid"])
        for field in (
            "plans_awaiting_approval",
            "plans_requiring_approval",
            "pending_plan_approvals",
            "pending_elevated_acknowledgements",
            "pending_challenge_count",
        ):
            self.assertEqual(health[field], 0)
        self.assertEqual(self.service.pending_external_reviews(), [])

        recovered = ChangeGovernanceService(
            self.repository,
            self.gateway,
            now=self.clock,
        )
        reconciliation = await recovered.reconcile_operational_plans(
            trigger="startup"
        )
        self.assertEqual(reconciliation["checked"], 0)
        self.assertEqual(reconciliation["pending"], 0)
        self.assertEqual(reconciliation["failed"], 0)
        self.assertEqual(recovered.pending_external_reviews(), [])

        import httpx

        app = create_approval_application(RuntimeShim(recovered))
        transport = httpx.ASGITransport(
            app=app, client=("172.30.32.2", 12345)
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://approval.local",
            headers={
                "X-Ingress-Path": "/api/hassio_ingress/beta10fixture",
                "X-Remote-User-Id": "beta10-admin",
            },
        ) as client:
            inbox = await client.get("/")
            self.assertEqual(inbox.status_code, 200)
            self.assertIn("No governed plans", inbox.text)
            for plan_id, _path in paths:
                self.assertNotIn(plan_id, inbox.text)
                review = await client.get(f"/plans/{plan_id}")
                self.assertEqual(review.status_code, 404)
                self.assertNotIn("Approve exact plan", review.text)

        for plan_id, path in paths:
            self.assertEqual(path.read_bytes(), before[plan_id])
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                [
                    (event["event"], event["timestamp"])
                    for event in persisted["events"]
                ],
                event_evidence[plan_id],
            )
            self.assertIsNone(
                recovered.task_repository.get_for_plan(plan_id)
            )
        self.assertEqual(self.gateway.writes, 0)

    async def test_handoff_projects_both_legacy_records_as_prohibited(self):
        plan_ids = [self._write_bytes(path)[0] for path in FIXTURE_PATHS]
        before = {
            plan_id: (self.root / f"{plan_id}.json").read_bytes()
            for plan_id in plan_ids
        }
        provider = EngineeringHandoffProvider(
            governance=RuntimeShim(self.service),
            incident=None,
            dependency_index=SimpleNamespace(),
            rest_client=None,
            health=None,
        )
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
                "change_plan_ids": plan_ids,
                "lookback_hours": 168,
                "refresh_index": False,
            }
        )
        for plan_id in plan_ids:
            item = next(
                item for item in bundle.items if plan_id in item.change_plan_ids
            )
            self.assertEqual(item.section, "confirmed_findings")
            self.assertEqual(item.status, "not_applicable")
            self.assertFalse(item.requires_authorization)
            self.assertEqual(item.authorization_type, "none")
            self.assertIn("prohibited", item.summary)
            self.assertEqual(
                (self.root / f"{plan_id}.json").read_bytes(),
                before[plan_id],
            )
        self.assertEqual(self.gateway.writes, 0)

    async def test_legacy_contradiction_matrix_fails_closed(self):
        cases: list[tuple[str, dict, ErrorCode]] = []

        def case(name: str, mutate, expected=ErrorCode.APPROVAL_SEQUENCE_FAILURE):
            value = self._fixture()
            mutate(value)
            cases.append((name, value, expected))

        case(
            "contract_v2",
            lambda value: value.__setitem__("contract_version", 2),
            ErrorCode.POLICY_SNAPSHOT_MISMATCH,
        )
        case(
            "contract_v3",
            lambda value: value.__setitem__("contract_version", 3),
            ErrorCode.POLICY_SNAPSHOT_MISMATCH,
        )
        case(
            "plan_version",
            lambda value: value.__setitem__("plan_version", 2),
            ErrorCode.POLICY_SNAPSHOT_MISMATCH,
        )
        case(
            "operation",
            lambda value: value.__setitem__(
                "operation", "create_automation"
            ),
            ErrorCode.POLICY_SNAPSHOT_MISMATCH,
        )
        case(
            "target_type",
            lambda value: value["target"].__setitem__(
                "target_type", "script"
            ),
            ErrorCode.POLICY_SNAPSHOT_MISMATCH,
        )
        case(
            "empty_target",
            lambda value: value["target"].__setitem__("target_id", ""),
            ErrorCode.POLICY_SNAPSHOT_MISMATCH,
        )
        case(
            "self_target",
            lambda value: value["target"].__setitem__(
                "target_id", value["plan_id"]
            ),
            ErrorCode.POLICY_SNAPSHOT_MISMATCH,
        )
        contract_v2_operation = json.loads(
            CONTRACT_V2_FIXTURE_PATHS[0].read_text(encoding="utf-8")
        )["operations"][0]
        case(
            "nonempty_operations",
            lambda value: value.__setitem__(
                "operations", [copy.deepcopy(contract_v2_operation)]
            ),
            ErrorCode.POLICY_SNAPSHOT_MISMATCH,
        )
        case("status", lambda value: value.__setitem__("status", "superseded"))
        case("approval_state", lambda value: value["approval"].__setitem__("state", "required"))
        case("bundle_state", lambda value: value["approval"].__setitem__("bundle_state", "prohibited"))
        case(
            "authority_version",
            lambda value: value["approval"].__setitem__(
                "authority_version", 2
            ),
            ErrorCode.APPROVAL_AUTHORITY_MISMATCH,
        )
        case("approval_kind", lambda value: value["approval"].__setitem__("approval_kind", "rollback"))
        case(
            "policy_class",
            lambda value: value["policy_decision"].__setitem__(
                "policy_class", "standard_admin"
            ),
            ErrorCode.POLICY_SNAPSHOT_MISMATCH,
        )
        case(
            "acknowledgements",
            lambda value: value["policy_decision"].__setitem__(
                "required_acknowledgements", ["plan_approval"]
            ),
            ErrorCode.POLICY_SNAPSHOT_MISMATCH,
        )
        case(
            "apply_allowed",
            lambda value: value["risk"].__setitem__("apply_allowed", True),
            ErrorCode.POLICY_SNAPSHOT_MISMATCH,
        )
        case(
            "policy_hash",
            lambda value: (
                value["policy_decision"].__setitem__(
                    "policy_decision_hash", "0" * 64
                ),
                value["approval"].__setitem__(
                    "policy_decision_hash", "0" * 64
                ),
            ),
            ErrorCode.POLICY_SNAPSHOT_MISMATCH,
        )
        case(
            "plan_hash_subject",
            lambda value: value.__setitem__("proposed_config_hash", "1" * 64),
            ErrorCode.POLICY_SNAPSHOT_MISMATCH,
        )
        case("challenge", lambda value: value["approval"].__setitem__("challenge_id", "synthetic-challenge"))
        case(
            "granted",
            lambda value: (
                value["approval"].__setitem__("state", "approved"),
                value["approval"].__setitem__("approved_at", value["updated_at"]),
            ),
        )
        case(
            "consumed",
            lambda value: (
                value["approval"].__setitem__("state", "consumed"),
                value["approval"].__setitem__("consumed_at", value["updated_at"]),
            ),
        )
        case(
            "elevated_acknowledgement",
            lambda value: value["approval"].__setitem__(
                "elevated_risk_acknowledgement",
                {"kind": "elevated_risk_acknowledgement"},
            ),
        )
        case("bound_authority", lambda value: value["approval"].__setitem__("bound_plan_hash", "2" * 64))
        case("apply_evidence", lambda value: value.__setitem__("applied_at", value["updated_at"]))
        case(
            "verification",
            lambda value: (
                value["verification"].__setitem__("status", "verified"),
                value["verification"].__setitem__("checked_at", value["updated_at"]),
            ),
        )
        case("rollback", lambda value: value["rollback"].__setitem__("requested_at", value["updated_at"]))

        base_events = self._fixture()["events"]
        case("missing_expiry", lambda value: value.__setitem__("events", value["events"][:-1]))
        case("duplicate_expiry", lambda value: value["events"].append(copy.deepcopy(value["events"][-1])))
        case(
            "additional_event",
            lambda value: value["events"].insert(
                1,
                {
                    **copy.deepcopy(value["events"][0]),
                    "event": "change_apply_started",
                },
            ),
        )
        case("wrong_order", lambda value: value["events"].reverse())
        case("expiry_status", lambda value: value["events"][-1].__setitem__("result_status", "success"))
        case("expiry_code", lambda value: value["events"][-1].__setitem__("error_code", "prohibited_change"))
        case(
            "approval_rejection_code",
            lambda value: value["events"].insert(
                1,
                {
                    **copy.deepcopy(value["events"][0]),
                    "event": "policy_approval_rejected",
                    "result_status": "rejected",
                    "error_code": "external_approval_required",
                },
            ),
        )
        wrong_apply = self._fixture(1)
        wrong_apply["events"][2]["error_code"] = "external_approval_required"
        cases.append(
            (
                "apply_rejection_code",
                wrong_apply,
                ErrorCode.APPROVAL_SEQUENCE_FAILURE,
            )
        )

        for name, value, expected in cases:
            with self.subTest(case=name):
                self._assert_refused(value, expected)

        self.assertEqual(base_events[-1]["event"], "change_plan_expired")

    async def test_operation_provider_evidence_is_rejected(self):
        operation = json.loads(
            CONTRACT_V2_FIXTURE_PATHS[0].read_text(encoding="utf-8")
        )["operations"][0]
        cases = []
        for name, receipt in (
            ("provider_attempt", {}),
            (
                "provider_operation_id",
                {"provider_operation_id": "synthetic-provider-operation"},
            ),
            (
                "response_receipt",
                {"provider_response_received": True},
            ),
        ):
            value = self._fixture()
            operation_value = copy.deepcopy(operation)
            operation_value["execution_receipt"] = receipt
            value["operations"] = [operation_value]
            cases.append((name, value))
        for name, value in cases:
            with self.subTest(case=name):
                self._assert_refused(
                    value,
                    ErrorCode.POLICY_SNAPSHOT_MISMATCH,
                )

    async def test_execution_task_and_task_store_errors_fail_closed(self):
        plan_id, path = self._write_bytes(FIXTURE_PATHS[0])
        plan = self.repository.get(plan_id)
        self.assertIsNotNone(plan)
        assert plan is not None
        task = new_execution_task(
            task_id="e" * 32,
            plan_id=plan_id,
            plan_hash=self.service.plan_hash(plan),
            operation="update_automation",
            target={"target_type": "automation", "target_id": plan.target_id},
            timestamp=plan.updated_at,
            execution_request_id="synthetic-beta10-request",
            idempotency_key="f" * 64,
            approval_reference={"authority_version": 3},
            legacy_projection={},
        )
        self.service.task_repository.save(task)
        before = path.read_bytes()
        with self.assertRaises(GovernanceError) as raised:
            self.service.get_plan(plan_id)
        self.assertEqual(
            raised.exception.code, ErrorCode.APPROVAL_SEQUENCE_FAILURE
        )
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.gateway.writes, 0)

        self._clear(plan_id)
        storage_plan_id, storage_path = self._write_bytes(FIXTURE_PATHS[1])
        storage_before = storage_path.read_bytes()
        loaded = self.repository.get(storage_plan_id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        with patch.object(
            self.service.task_repository,
            "get_for_plan",
            side_effect=ExecutionTaskStorageError("synthetic unavailable"),
        ):
            with self.assertRaises(GovernanceError) as storage_error:
                self.service._effective_prohibited_plan_failures(loaded)
        self.assertEqual(
            storage_error.exception.code,
            ErrorCode.EXECUTION_TASK_STORAGE_ERROR,
        )
        self.assertEqual(storage_path.read_bytes(), storage_before)
        self.assertEqual(self.gateway.writes, 0)

    async def test_contract_v2_profile_remains_unchanged(self):
        for fixture_path in CONTRACT_V2_FIXTURE_PATHS:
            with self.subTest(fixture=fixture_path.name):
                plan_id, path = self._write_bytes(fixture_path)
                before = path.read_bytes()
                plan = self.repository.get(plan_id)
                self.assertIsNotNone(plan)
                assert plan is not None
                self.assertEqual(
                    self.service._effective_prohibited_plan_failures(plan),
                    (),
                )
                self.assertEqual(
                    self.service.get_plan(plan_id)["status"], "prohibited"
                )
                self.assertEqual(path.read_bytes(), before)
                self._clear(plan_id)


if __name__ == "__main__":
    unittest.main()
