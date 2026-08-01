import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.approval_web import (  # noqa: E402
    create_approval_application,
)
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalState,
    ChangePlan,
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
from ha_mcp_engineering.handoff.provider import (  # noqa: E402
    EngineeringHandoffProvider,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
)
from tests.test_beta25_external_approval import (  # noqa: E402
    Clock,
    CURRENT,
    FakeGateway,
    RuntimeShim,
)


HISTORICAL_PLAN_ID = "a" * 32
FIXED_TIME = "2026-07-14T12:00:00+00:00"

# Retained only to document the fictional Beta 8 regression fixture. It omitted
# contract_version and operations, so deserialization treated it as contract-v1.
# Compatibility tests below use the source-generated contract-v2 fixture.
_RETIRED_MANUAL_CONTRACT_V1_FIXTURE = {
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

_REAL_BETA6_FIXTURE_PATH = (
    ROOT
    / "tests"
    / "fixtures"
    / "beta6_prohibited_superseded_contract_v2_a.json"
)
BETA6_PERSISTED_PROHIBITED = json.loads(
    _REAL_BETA6_FIXTURE_PATH.read_text(encoding="utf-8")
)
HISTORICAL_PLAN_ID = BETA6_PERSISTED_PROHIBITED["plan_id"]


class PersistedProhibitedCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "plans"
        self.repository = ChangePlanRepository(self.root)
        self.gateway = FakeGateway()
        self.clock = Clock()
        self.service = ChangeGovernanceService(
            self.repository,
            self.gateway,
            now=self.clock,
        )
        self.telemetry, self.context = begin_request("beta8-request")
        self.telemetry.caller_id = "sanitized-fixture"

    async def asyncTearDown(self):
        end_request(self.context)
        self.temp.cleanup()

    def test_retired_manual_fixture_exposes_beta8_contract_default_error(self):
        self.assertNotIn(
            "contract_version", _RETIRED_MANUAL_CONTRACT_V1_FIXTURE
        )
        self.assertNotIn("operations", _RETIRED_MANUAL_CONTRACT_V1_FIXTURE)
        deserialized = ChangePlan.from_dict(
            copy.deepcopy(_RETIRED_MANUAL_CONTRACT_V1_FIXTURE)
        )
        self.assertEqual(deserialized.contract_version, 1)

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

    def add_automation(self, automation_id):
        self.gateway.configs[automation_id] = {
            **copy.deepcopy(CURRENT),
            "id": automation_id,
        }

    async def create_current_plan(self, policy_class, automation_id):
        self.add_automation(automation_id)
        proposed = copy.deepcopy(CURRENT)
        if policy_class == "standard_admin":
            proposed["description"] = f"Standard fixture {automation_id}"
        elif policy_class == "elevated_admin":
            proposed["action"] = [
                {
                    "service": "light.turn_on",
                    "target": {"entity_id": "light.beta8_fixture"},
                }
            ]
        elif policy_class == "prohibited":
            proposed["action"] = [
                {
                    "service": "lock.unlock",
                    "target": {"device_id": "beta8_nonexistent_device"},
                }
            ]
        else:
            raise AssertionError(f"unsupported fixture policy: {policy_class}")
        created = await self.service.create_plan(
            title=f"{policy_class} Beta 8 fixture",
            description="Bounded persisted compatibility fixture",
            operation="update_automation",
            automation_id=automation_id,
            proposed_config=proposed,
        )
        self.assertEqual(
            created["policy_decision"]["policy_class"], policy_class
        )
        return created

    async def fully_approve(self, created):
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        while pending.get("status") == "approval_pending":
            _review, csrf = await self.service.issue_external_csrf(
                created["plan_id"], pending["challenge_id"]
            )
            pending = await self.service.decide_external_approval(
                plan_id=created["plan_id"],
                challenge_id=pending["challenge_id"],
                expected_plan_hash=created["plan_hash"],
                approval_kind="apply",
                approval_action=pending["approval_action"],
                csrf_nonce=csrf,
                decision="approve",
                approver_principal=(
                    "home_assistant_admin_ingress:beta8-admin"
                ),
            )
        self.assertEqual(pending["status"], "approved")
        return pending

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

    async def test_mixed_listing_and_health_use_effective_status(self):
        self.write_historical()
        current_prohibited = await self.create_current_plan(
            "prohibited", "current-prohibited"
        )
        standard = await self.create_current_plan(
            "standard_admin", "standard-pending"
        )
        elevated = await self.create_current_plan(
            "elevated_admin", "elevated-pending"
        )
        applied = await self.create_current_plan(
            "standard_admin", "standard-applied"
        )
        await self.fully_approve(applied)
        applied_result = await self.service.apply(
            applied["plan_id"], applied["plan_hash"]
        )
        self.assertEqual(applied_result["status"], "applied")

        unfiltered = self.service.list_plans(limit=100)
        self.assertEqual(unfiltered["count"], 5)
        by_id = {plan["plan_id"]: plan for plan in unfiltered["plans"]}
        self.assertEqual(by_id[HISTORICAL_PLAN_ID]["status"], "prohibited")
        self.assertEqual(
            by_id[current_prohibited["plan_id"]]["status"], "prohibited"
        )

        prohibited = self.service.list_plans(
            status="prohibited", limit=100
        )
        self.assertEqual(
            {plan["plan_id"] for plan in prohibited["plans"]},
            {HISTORICAL_PLAN_ID, current_prohibited["plan_id"]},
        )
        awaiting = self.service.list_plans(
            status="awaiting_approval", limit=100
        )
        self.assertEqual(
            {plan["plan_id"] for plan in awaiting["plans"]},
            {standard["plan_id"], elevated["plan_id"]},
        )
        self.assertNotIn(HISTORICAL_PLAN_ID, {
            plan["plan_id"] for plan in awaiting["plans"]
        })
        applied_listing = self.service.list_plans(
            status="applied", limit=100
        )
        self.assertEqual(
            [plan["plan_id"] for plan in applied_listing["plans"]],
            [applied["plan_id"]],
        )

        health = self.service.health_summary()
        self.assertEqual(health["total_plans"], 5)
        self.assertEqual(
            health["plans_by_policy_class"],
            {
                "standard_admin": 2,
                "elevated_admin": 1,
                "prohibited": 2,
                "legacy_without_policy_snapshot": 0,
            },
        )
        self.assertEqual(health["prohibited_policy_decisions"], 2)
        self.assertEqual(health["plans_awaiting_approval"], 2)
        self.assertEqual(health["plans_requiring_approval"], 2)
        self.assertEqual(health["pending_plan_approvals"], 0)
        self.assertEqual(health["pending_elevated_acknowledgements"], 0)

    async def test_rehydration_and_ingress_are_non_actionable_and_read_only(
        self,
    ):
        path = self.write_historical()
        before = path.read_bytes()
        self.clock.advance(minutes=181)
        recovered = ChangeGovernanceService(
            self.repository,
            self.gateway,
            now=self.clock,
        )

        public = recovered.get_plan(HISTORICAL_PLAN_ID)
        self.assertEqual(public["status"], "prohibited")
        self.assertEqual(public["approval_lifecycle"], "prohibited")
        self.assertFalse(public["approval_actionable"])
        self.assertEqual(
            recovered.list_plans(status="prohibited")["count"], 1
        )
        self.assertEqual(recovered.pending_external_reviews(), [])
        health = recovered.health_summary()
        self.assertEqual(health["prohibited_policy_decisions"], 1)
        self.assertEqual(health["plans_awaiting_approval"], 0)
        self.assertEqual(health["plans_requiring_approval"], 0)
        self.assertEqual(health["pending_challenge_count"], 0)
        self.assertEqual(
            recovered.list_execution_tasks(plan_id=HISTORICAL_PLAN_ID)[
                "count"
            ],
            0,
        )

        import httpx

        app = create_approval_application(RuntimeShim(recovered))
        transport = httpx.ASGITransport(
            app=app, client=("172.30.32.2", 12345)
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://approval.local",
            headers={
                "X-Ingress-Path": "/api/hassio_ingress/beta8fixture",
                "X-Remote-User-Id": "beta8-admin",
            },
        ) as client:
            inbox = await client.get("/")
            review = await client.get(f"/plans/{HISTORICAL_PLAN_ID}")
        self.assertEqual(inbox.status_code, 200)
        self.assertIn("No governed plans", inbox.text)
        self.assertNotIn(HISTORICAL_PLAN_ID, inbox.text)
        self.assertEqual(review.status_code, 404)
        self.assertNotIn("Approve exact plan", review.text)

        self.assertEqual(path.read_bytes(), before)
        persisted = self.repository.get(HISTORICAL_PLAN_ID)
        assert persisted is not None
        self.assertEqual(
            [event.event for event in persisted.events],
            ["change_plan_created", "change_plan_superseded"],
        )
        self.assertEqual(self.gateway.writes, 0)

    async def test_handoff_projects_historical_record_as_prohibited(self):
        self.write_historical()
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
                "change_plan_ids": [HISTORICAL_PLAN_ID],
                "lookback_hours": 168,
                "refresh_index": False,
            }
        )
        item = next(
            item
            for item in bundle.items
            if HISTORICAL_PLAN_ID in item.change_plan_ids
        )
        self.assertEqual(item.section, "confirmed_findings")
        self.assertEqual(item.status, "not_applicable")
        self.assertFalse(item.requires_authorization)
        self.assertEqual(item.authorization_type, "none")
        self.assertIn("prohibited", item.summary)

    async def test_historical_prohibited_refuses_approval_and_apply(self):
        self.write_historical()
        public = self.service.get_plan(HISTORICAL_PLAN_ID)
        with self.assertRaises(GovernanceError) as approval_error:
            self.service.approve(HISTORICAL_PLAN_ID, public["plan_hash"])
        self.assertEqual(
            approval_error.exception.code, ErrorCode.PROHIBITED_CHANGE
        )
        with self.assertRaises(GovernanceError) as apply_error:
            await self.service.apply(HISTORICAL_PLAN_ID, public["plan_hash"])
        self.assertEqual(
            apply_error.exception.code, ErrorCode.PROHIBITED_CHANGE
        )
        persisted = self.repository.get(HISTORICAL_PLAN_ID)
        assert persisted is not None
        self.assertIsNone(persisted.approval.challenge_id)
        self.assertIsNone(
            self.service.task_repository.get_for_plan(HISTORICAL_PLAN_ID)
        )
        self.assertEqual(self.gateway.writes, 0)


if __name__ == "__main__":
    unittest.main()
