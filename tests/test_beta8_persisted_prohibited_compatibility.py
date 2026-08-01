import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalState,
    PlanStatus,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.governance.task_models import (  # noqa: E402
    new_execution_task,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
)
from tests.test_beta25_external_approval import (  # noqa: E402
    Clock,
    CURRENT,
    FakeGateway,
)


HISTORICAL_PLAN_ID = "a" * 32
FIXED_TIME = "2026-07-14T12:00:00+00:00"

# Produced with the Beta 6 merge's own ChangeGovernanceService constructors and
# ChangePlan.to_dict serializer. Beta 6 created a prohibited plan and then
# superseded it when a later plan targeted the same automation. Identifiers,
# caller attribution, text, and timestamps are fixed sanitized test values.
BETA6_PERSISTED_PROHIBITED = {
    "applied_at": None,
    "apply_request_id": None,
    "approval": {
        "approval_expires_at": None,
        "approval_kind": "apply",
        "approval_note": None,
        "approved_at": None,
        "approver_principal": None,
        "approving_caller_id": None,
        "authority_version": 3,
        "bound_plan_hash": None,
        "bundle_state": "invalidated",
        "challenge_expires_at": None,
        "challenge_id": None,
        "challenge_operation": None,
        "challenge_plan_version": None,
        "challenge_requested_at": None,
        "challenge_risk_level": None,
        "challenge_target_id": None,
        "challenge_target_type": None,
        "channel": None,
        "consumed_at": None,
        "csrf_digest": None,
        "csrf_issued_at": None,
        "policy_class": "prohibited",
        "policy_decision_hash": (
            "5fe6fc1561f7330a91c5fedc6dd21796"
            "ff3e81858ecf80373dd2ae7e623942d7"
        ),
        "principal_separation_enforced": None,
        "request_note": None,
        "state": "invalidated",
    },
    "caller_context": {},
    "created_at": FIXED_TIME,
    "current_config": {
        "action": [
            {
                "data": {"message": "No physical action"},
                "service": "notify.fixture",
            }
        ],
        "alias": "Approval fixture",
        "condition": [],
        "description": "Before",
        "id": "fixture",
        "mode": "single",
        "trigger": [
            {"event_type": "beta25_fixture", "platform": "event"}
        ],
    },
    "current_state_fingerprint": (
        "9cd0aa302c87dd4746a5c0bc445558ba"
        "eacd08b4d91459ffeb197fe845a7384b"
    ),
    "description": "Sanitized Beta 6 record",
    "dry_run_results": {
        "changed_fields": [
            {
                "after": {"count": 1, "type": "list"},
                "before": {"count": 1, "type": "list"},
                "change_type": "modified",
                "field": "actions",
            }
        ],
        "has_changes": True,
        "meaningful_change_count": 1,
        "unchanged_fields": ["alias", "description", "mode", "triggers"],
    },
    "events": [
        {
            "caller_id": "sanitized-fixture",
            "duration_ms": None,
            "error_code": None,
            "event": "change_plan_created",
            "request_id": "beta6-fixed-request",
            "result_status": "success",
            "timestamp": FIXED_TIME,
        },
        {
            "caller_id": "sanitized-fixture",
            "duration_ms": None,
            "error_code": None,
            "event": "change_plan_superseded",
            "request_id": "beta6-fixed-request",
            "result_status": "rejected",
            "timestamp": FIXED_TIME,
        },
    ],
    "expires_at": "2026-07-14T14:00:00+00:00",
    "failure_information": None,
    "normalization_version": 2,
    "normalized_current_config": {
        "action": [
            {
                "data": {"message": "No physical action"},
                "service": "notify.fixture",
            }
        ],
        "alias": "Approval fixture",
        "description": "Before",
        "mode": "single",
        "trigger": [
            {"event_type": "beta25_fixture", "platform": "event"}
        ],
    },
    "normalized_proposed_config": {
        "action": [
            {
                "service": "lock.unlock",
                "target": {"entity_id": "lock.fixture"},
            }
        ],
        "alias": "Approval fixture",
        "description": "Before",
        "mode": "single",
        "trigger": [
            {"event_type": "beta25_fixture", "platform": "event"}
        ],
    },
    "operation": "update_automation",
    "plan_id": HISTORICAL_PLAN_ID,
    "plan_version": 1,
    "policy_decision": {
        "physical_consequence": "safety_critical",
        "policy_class": "prohibited",
        "policy_decision_hash": (
            "5fe6fc1561f7330a91c5fedc6dd21796"
            "ff3e81858ecf80373dd2ae7e623942d7"
        ),
        "policy_subject_hash": (
            "ee6f0d10f70c6e4b9c76329d1751896"
            "c011fce9bcf706fe8d334a0f06ea97c18"
        ),
        "policy_version": "f2-v1",
        "reason_codes": [
            "safety_critical_effect_not_reviewed",
            "safety_critical_service_prohibited",
            "supported_configuration_change",
        ],
        "required_acknowledgements": [],
        "risk_delta": "high",
    },
    "post_apply_fingerprint": None,
    "proposed_config": {
        "action": [
            {
                "service": "lock.unlock",
                "target": {"entity_id": "lock.fixture"},
            }
        ],
        "alias": "Approval fixture",
        "condition": [],
        "description": "Before",
        "mode": "single",
        "trigger": [
            {"event_type": "beta25_fixture", "platform": "event"}
        ],
    },
    "proposed_config_hash": (
        "030b748b88bcd83a68012a22970f1e218"
        "04caa05c8ac4d7486078031941cebb7"
    ),
    "requested_by": "sanitized-fixture",
    "risk": {
        "apply_allowed": False,
        "evidence": [
            {
                "domain": "lock",
                "field": "action[0].target.entity_id",
                "trigger": "sensitive_entity_domain",
            },
            {
                "field": "action[0].service",
                "service": "lock.unlock",
                "trigger": "high_risk_service",
            },
            {
                "field": "action[0].service",
                "service": "lock.unlock",
                "trigger": "safety_critical_service",
            },
        ],
        "level": "high",
        "reasons": ["Structured action or target requires high-risk review"],
        "warnings": [],
    },
    "rollback": {
        "approved_at": None,
        "available": False,
        "expected_current_fingerprint": None,
        "failure_code": None,
        "request_id": None,
        "requested_at": None,
        "rolled_back_at": None,
        "status": "not_yet_available",
    },
    "snapshot": None,
    "status": "superseded",
    "target": {"target_id": "fixture", "target_type": "automation"},
    "title": "Historical prohibited fixture",
    "updated_at": FIXED_TIME,
    "validation_results": {"errors": [], "valid": True},
    "verification": {
        "actual_fingerprint": None,
        "checked_at": None,
        "config_check_status": None,
        "desired_fingerprint": None,
        "duration_ms": None,
        "mismatch_fields": [],
        "status": "not_run",
    },
    "warnings": [],
}


class PersistedProhibitedCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "plans"
        self.repository = ChangePlanRepository(self.root)
        self.gateway = FakeGateway()
        self.service = ChangeGovernanceService(
            self.repository,
            self.gateway,
            now=Clock(),
        )
        self.telemetry, self.context = begin_request("beta8-request")
        self.telemetry.caller_id = "sanitized-fixture"

    async def asyncTearDown(self):
        end_request(self.context)
        self.temp.cleanup()

    def write_historical(self, value=None):
        payload = copy.deepcopy(value or BETA6_PERSISTED_PROHIBITED)
        path = self.root / f"{payload['plan_id']}.json"
        path.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        return path

    def assert_sequence_failure(self, value):
        self.write_historical(value)
        with self.assertRaises(GovernanceError) as raised:
            self.service.get_plan(HISTORICAL_PLAN_ID)
        self.assertEqual(
            raised.exception.code, ErrorCode.APPROVAL_SEQUENCE_FAILURE
        )
        self.assertEqual(self.gateway.writes, 0)

    async def test_exact_beta6_shape_projects_prohibited_without_mutation(self):
        path = self.write_historical()
        before = path.read_bytes()
        persisted = self.repository.get(HISTORICAL_PLAN_ID)
        assert persisted is not None
        self.assertTrue(
            self.service._is_effectively_prohibited_plan(persisted)
        )
        public = self.service.get_plan(HISTORICAL_PLAN_ID)
        self.assertEqual(public["status"], "prohibited")
        self.assertEqual(public["approval"]["state"], "prohibited")
        self.assertEqual(public["approval_lifecycle"], "prohibited")
        self.assertEqual(public["approval_bundle_state"], "prohibited")
        self.assertFalse(public["approval_actionable"])
        self.assertFalse(public["approval_challenge_created"])
        self.assertFalse(public["apply_allowed"])
        self.assertIsNone(public["next_required_operation"])
        self.assertFalse(public["status_is_legacy"])
        self.assertEqual(
            public["policy_decision"]["required_acknowledgements"], []
        )
        self.assertIsNone(public["execution_task"]["task_id"])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.gateway.writes, 0)

    async def test_current_prohibited_representation_is_unchanged(self):
        proposed = copy.deepcopy(CURRENT)
        proposed["action"] = [
            {
                "service": "lock.unlock",
                "target": {"entity_id": "lock.fixture"},
            }
        ]
        created = await self.service.create_plan(
            title="Current prohibited fixture",
            description="Current Beta 8 representation",
            operation="update_automation",
            automation_id="fixture",
            proposed_config=proposed,
        )
        self.assertEqual(created["status"], "prohibited")
        self.assertEqual(created["approval"]["state"], "prohibited")
        self.assertEqual(created["approval_bundle_state"], "prohibited")
        self.assertFalse(created["approval_actionable"])
        self.assertEqual(self.gateway.writes, 0)

    async def test_contradictory_historical_shapes_fail_closed(self):
        nonempty = copy.deepcopy(BETA6_PERSISTED_PROHIBITED)
        nonempty["policy_decision"]["required_acknowledgements"] = [
            "plan_approval"
        ]
        self.write_historical(nonempty)
        with self.assertRaises(GovernanceError) as policy_error:
            self.service.get_plan(HISTORICAL_PLAN_ID)
        self.assertEqual(
            policy_error.exception.code, ErrorCode.POLICY_SNAPSHOT_MISMATCH
        )
        (self.root / f"{HISTORICAL_PLAN_ID}.json").unlink()

        consumed = copy.deepcopy(BETA6_PERSISTED_PROHIBITED)
        consumed["approval"]["state"] = "consumed"
        consumed["approval"]["bundle_state"] = "consumed"
        consumed["approval"]["consumed_at"] = FIXED_TIME
        self.assert_sequence_failure(consumed)
        (self.root / f"{HISTORICAL_PLAN_ID}.json").unlink()

        provider = copy.deepcopy(BETA6_PERSISTED_PROHIBITED)
        provider["events"].append(
            {
                "caller_id": "sanitized-fixture",
                "duration_ms": None,
                "error_code": None,
                "event": "automation_provider_completed",
                "request_id": "beta6-fixed-request",
                "result_status": "success",
                "timestamp": FIXED_TIME,
            }
        )
        self.assert_sequence_failure(provider)
        (self.root / f"{HISTORICAL_PLAN_ID}.json").unlink()

        applied = copy.deepcopy(BETA6_PERSISTED_PROHIBITED)
        applied["status"] = "applied"
        applied["applied_at"] = FIXED_TIME
        self.assert_sequence_failure(applied)
        (self.root / f"{HISTORICAL_PLAN_ID}.json").unlink()

        invalid_hash = copy.deepcopy(BETA6_PERSISTED_PROHIBITED)
        invalid_hash["policy_decision"]["policy_decision_hash"] = "0" * 64
        invalid_hash["approval"]["policy_decision_hash"] = "0" * 64
        self.write_historical(invalid_hash)
        with self.assertRaises(GovernanceError) as hash_error:
            self.service.get_plan(HISTORICAL_PLAN_ID)
        self.assertEqual(
            hash_error.exception.code, ErrorCode.POLICY_SNAPSHOT_MISMATCH
        )
        self.assertEqual(self.gateway.writes, 0)

    async def test_execution_task_contradiction_fails_closed(self):
        self.write_historical()
        task = new_execution_task(
            task_id="c" * 32,
            plan_id=HISTORICAL_PLAN_ID,
            plan_hash="d" * 64,
            operation="update_automation",
            target={"target_type": "automation", "target_id": "fixture"},
            timestamp=FIXED_TIME,
            execution_request_id="beta8-request",
            idempotency_key="e" * 64,
            approval_reference={"authority_version": 3},
            legacy_projection={},
        )
        self.service.task_repository.save(task)
        with self.assertRaises(GovernanceError) as raised:
            self.service.get_plan(HISTORICAL_PLAN_ID)
        self.assertEqual(
            raised.exception.code, ErrorCode.APPROVAL_SEQUENCE_FAILURE
        )
        self.assertEqual(self.gateway.writes, 0)


if __name__ == "__main__":
    unittest.main()
