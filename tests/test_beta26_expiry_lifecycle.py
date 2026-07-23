import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.capabilities import (  # noqa: E402
    BETA_NATIVE_CAPABILITIES,
    CAPABILITIES,
    PLANNED_CAPABILITIES,
)
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalState,
    ChangeApproval,
    ChangeEvent,
    ChangeSnapshot,
    PlanStatus,
)
from ha_mcp_engineering.governance.normalize import state_fingerprint  # noqa: E402
from ha_mcp_engineering.governance.runtime import GOVERNANCE  # noqa: E402
from ha_mcp_engineering.governance.service import (  # noqa: E402
    APPROVAL_AUTHORITY_VERSION,
    APPROVAL_CHANNEL,
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import ChangePlanRepository  # noqa: E402
from ha_mcp_engineering.handoff.provider import EngineeringHandoffProvider  # noqa: E402
from ha_mcp_engineering.health import HealthRegistry  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.tools import get_registered_server  # noqa: E402
from ha_mcp_engineering.tools import compatibility, governance as governance_tools  # noqa: E402
from ha_mcp_engineering.version import SCHEMA_VERSION  # noqa: E402


BETA25_PUBLIC_SCHEMA_SHA256 = "eeec35d49f6d8c59fb1215694e54314b21bb6fd4a723d65e956e8e438699876a"

CURRENT = {
    "alias": "Beta 26 expiry fixture",
    "description": "Before",
    "trigger": [{"platform": "event", "event_type": "beta26_fixture"}],
    "condition": [],
    "action": [{"service": "notify.fixture", "data": {"message": "Read-only test fixture"}}],
    "mode": "single",
}


class Clock:
    def __init__(self):
        self.value = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


class CountingRepository(ChangePlanRepository):
    def __init__(self, *args, **kwargs):
        self.save_count = 0
        super().__init__(*args, **kwargs)

    def save(self, plan):
        self.save_count += 1
        return super().save(plan)


class FakeGateway:
    def __init__(self):
        self.configs = {}
        self.reads = 0
        self.writes = 0

    async def get(self, automation_id):
        self.reads += 1
        return copy.deepcopy(self.configs.get(automation_id))

    async def write(self, automation_id, config):
        self.writes += 1
        self.configs[automation_id] = {**copy.deepcopy(config), "id": automation_id}
        return {"result": "ok"}

    async def validate(self):
        return {"result": "valid", "errors": None}


class Beta26LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.gateway = FakeGateway()
        self.repository = CountingRepository(Path(self.temp.name) / "plans")
        self.audit_path = Path(self.temp.name) / "audit.jsonl"
        self.service = ChangeGovernanceService(
            self.repository,
            self.gateway,
            AuditLogger(str(self.audit_path), "beta26-test-access-secret"),
            now=self.clock,
            sensitive_values=("beta26-test-access-secret",),
        )
        self.old_governance_service = GOVERNANCE.service
        self.old_governance_error = GOVERNANCE.storage_error
        GOVERNANCE.service = self.service
        GOVERNANCE.storage_error = None
        self.telemetry, self.context = begin_request("beta26-lifecycle-request")
        self.telemetry.caller_id = "beta26-mcp-caller"

    async def asyncTearDown(self):
        end_request(self.context)
        GOVERNANCE.service = self.old_governance_service
        GOVERNANCE.storage_error = self.old_governance_error
        self.temp.cleanup()

    async def create(self, target="fixture", *, expiration_minutes=60, description="After"):
        self.gateway.configs[target] = {**copy.deepcopy(CURRENT), "id": target}
        proposed = copy.deepcopy(CURRENT)
        proposed["description"] = description
        return await self.service.create_plan(
            title=f"Expiry fixture {target}",
            description="Beta 26 lifecycle test",
            operation="update_automation",
            automation_id=target,
            proposed_config=proposed,
            expiration_minutes=expiration_minutes,
        )

    def audit_events(self):
        if not self.audit_path.exists():
            return []
        return [json.loads(line)["event"] for line in self.audit_path.read_text().splitlines()]

    def handoff_plans(self):
        provider = EngineeringHandoffProvider(
            governance=GOVERNANCE,
            incident=None,
            dependency_index=None,
            rest_client=None,
            health=None,
        )
        return provider._governance_plans({"change_plan_ids": []})[0]

    async def call_public_read_surfaces(self, plan_id, *, repetitions=2):
        registry = HealthRegistry()
        registry.configure(
            SimpleNamespace(
                ha_url="http://supervisor/core",
                log_level="INFO",
                redaction_enabled=True,
            ),
            self.service.audit,
            None,
            GOVERNANCE,
        )
        with patch.object(compatibility, "HEALTH", registry):
            for _ in range(repetitions):
                health = json.loads(await compatibility.get_server_health(check_ha=False))
                listed = json.loads(await governance_tools.list_change_plans(limit=100))
                fetched = json.loads(await governance_tools.get_change_plan(plan_id))
                self.assertTrue(health["success"])
                self.assertTrue(listed["success"])
                self.assertTrue(fetched["success"])

    async def test_already_expired_plan_is_idempotent_across_every_read_adapter(self):
        created = await self.create(expiration_minutes=5)
        plan = self.repository.get(created["plan_id"])
        plan.status = PlanStatus.EXPIRED
        plan.approval.state = ApprovalState.INVALIDATED
        plan.expires_at = (self.clock() - timedelta(minutes=1)).isoformat()
        plan.updated_at = "2026-07-14T11:59:00+00:00"
        plan.events.append(
            ChangeEvent(
                event="change_plan_expired",
                timestamp=plan.updated_at,
                request_id="persisted-beta25-event",
                caller_id="system",
                result_status="rejected",
                error_code=ErrorCode.CHANGE_PLAN_EXPIRED.value,
            )
        )
        self.repository.save(plan)
        self.repository.save_count = 0
        baseline_events = len(plan.events)
        baseline_audit = list(self.audit_events())

        await self.call_public_read_surfaces(created["plan_id"], repetitions=3)
        for _ in range(3):
            self.service.pending_external_reviews()
            self.handoff_plans()

        reloaded = self.repository.get(created["plan_id"])
        self.assertEqual(reloaded.updated_at, "2026-07-14T11:59:00+00:00")
        self.assertEqual(len(reloaded.events), baseline_events)
        self.assertEqual(
            sum(event.event == "change_plan_expired" for event in reloaded.events),
            1,
        )
        self.assertEqual(self.repository.save_count, 0)
        self.assertEqual(self.audit_events(), baseline_audit)

    async def test_first_plan_expiry_transitions_once_then_public_reads_are_inert(self):
        created = await self.create(expiration_minutes=5)
        self.clock.advance(minutes=6)
        self.repository.save_count = 0
        baseline_audit_count = len(self.audit_events())

        first = self.service.get_plan(created["plan_id"])
        self.assertEqual(first["status"], "expired")
        self.assertEqual(self.repository.save_count, 1)
        transitioned = self.repository.get(created["plan_id"])
        transitioned_at = transitioned.updated_at
        transitioned_event_count = len(transitioned.events)
        self.assertEqual(
            sum(event.event == "change_plan_expired" for event in transitioned.events),
            1,
        )
        self.assertEqual(len(self.audit_events()), baseline_audit_count + 1)

        self.repository.save_count = 0
        await self.call_public_read_surfaces(created["plan_id"], repetitions=3)
        for _ in range(3):
            self.service.pending_external_reviews()
            self.handoff_plans()

        reloaded = self.repository.get(created["plan_id"])
        self.assertEqual(reloaded.updated_at, transitioned_at)
        self.assertEqual(len(reloaded.events), transitioned_event_count)
        self.assertEqual(
            sum(event.event == "change_plan_expired" for event in reloaded.events),
            1,
        )
        self.assertEqual(self.repository.save_count, 0)
        self.assertEqual(len(self.audit_events()), baseline_audit_count + 1)

    async def test_expired_challenge_is_resolved_on_reads_and_can_be_replaced_once(self):
        created = await self.create(expiration_minutes=20)
        first = self.service.approve(created["plan_id"], created["plan_hash"])
        self.clock.advance(minutes=16)
        self.repository.save_count = 0

        public = self.service.get_plan(created["plan_id"])
        self.assertEqual(public["status"], "awaiting_approval")
        self.assertEqual(public["approval"]["state"], "expired")
        self.assertEqual(self.repository.save_count, 1)
        self.assertEqual(self.service.health_summary()["pending_challenge_count"], 0)
        self.assertEqual(self.service.pending_external_reviews(), [])
        handoff = next(plan for plan in self.handoff_plans() if plan.plan_id == created["plan_id"])
        self.assertEqual(handoff.approval.state, ApprovalState.EXPIRED)

        expired = self.repository.get(created["plan_id"])
        expiration_events = sum(
            event.event == "external_approval_expired" for event in expired.events
        )
        expired_updated_at = expired.updated_at
        self.repository.save_count = 0
        for _ in range(3):
            self.service.get_plan(created["plan_id"])
            self.service.list_plans(limit=100)
            self.service.health_summary()
            self.service.pending_external_reviews()
            self.handoff_plans()
        reloaded = self.repository.get(created["plan_id"])
        self.assertEqual(reloaded.updated_at, expired_updated_at)
        self.assertEqual(self.repository.save_count, 0)
        self.assertEqual(
            sum(event.event == "external_approval_expired" for event in reloaded.events),
            expiration_events,
        )
        self.assertEqual(expiration_events, 1)

        replacement = self.service.approve(created["plan_id"], created["plan_hash"])
        self.assertNotEqual(first["challenge_id"], replacement["challenge_id"])
        self.assertLessEqual(
            datetime.fromisoformat(replacement["challenge_expires_at"]),
            datetime.fromisoformat(replacement["plan_expires_at"]),
        )
        repeated = self.service.approve(created["plan_id"], created["plan_hash"])
        self.assertEqual(replacement["challenge_id"], repeated["challenge_id"])
        self.assertEqual(replacement["challenge_expires_at"], repeated["challenge_expires_at"])
        with self.assertRaises(GovernanceError) as old:
            await self.service.issue_external_csrf(created["plan_id"], first["challenge_id"])
        self.assertEqual(old.exception.code, ErrorCode.EXTERNAL_APPROVAL_INVALID)

    def configure_apply_denial(self, plan, case):
        if case == "missing":
            return
        if case == "invalidated":
            plan.approval.state = ApprovalState.INVALIDATED
            return
        plan.status = PlanStatus.APPROVED
        plan.approval.state = (
            ApprovalState.CONSUMED if case == "consumed" else ApprovalState.APPROVED
        )
        plan.approval.authority_version = 1 if case == "legacy" else APPROVAL_AUTHORITY_VERSION
        plan.approval.channel = APPROVAL_CHANNEL
        plan.approval.approver_principal = "home_assistant_admin_ingress"
        plan.approval.principal_separation_enforced = True
        plan.approval.approval_kind = "rollback" if case == "wrong_kind" else "apply"
        plan.approval.approval_expires_at = (self.clock() + timedelta(minutes=30)).isoformat()

    async def test_apply_denials_precede_all_upstream_or_write_activity(self):
        expected_codes = {
            "expired": ErrorCode.EXTERNAL_APPROVAL_REQUIRED,
            "invalidated": ErrorCode.EXTERNAL_APPROVAL_REQUIRED,
            "missing": ErrorCode.EXTERNAL_APPROVAL_REQUIRED,
            "wrong_kind": ErrorCode.APPROVAL_HASH_MISMATCH,
            "consumed": ErrorCode.APPROVAL_ALREADY_CONSUMED,
            "legacy": ErrorCode.APPROVAL_HASH_MISMATCH,
        }
        with patch("ha_mcp_engineering.dependency.DEPENDENCY_ANALYSIS.invalidate") as invalidate:
            for index, (case, expected) in enumerate(expected_codes.items()):
                with self.subTest(case=case):
                    created = await self.create(target=f"apply_{index}")
                    if case == "expired":
                        self.service.approve(created["plan_id"], created["plan_hash"])
                        self.clock.advance(minutes=16)
                    else:
                        plan = self.repository.get(created["plan_id"])
                        self.configure_apply_denial(plan, case)
                        self.repository.save(plan)
                    reads_before = self.gateway.reads
                    writes_before = self.gateway.writes
                    with self.assertRaises(GovernanceError) as raised:
                        await self.service.apply(created["plan_id"], created["plan_hash"])
                    self.assertEqual(raised.exception.code, expected)
                    self.assertEqual(self.gateway.reads, reads_before)
                    self.assertEqual(self.gateway.writes, writes_before)
                    self.assertIsNone(self.repository.get(created["plan_id"]).snapshot)
            invalidate.assert_not_called()

    def prepare_rollback_plan(self, plan, target, case):
        config = copy.deepcopy(self.gateway.configs[target])
        fingerprint = state_fingerprint(config)
        plan.status = PlanStatus.ROLLBACK_PENDING
        plan.plan_version += 1
        plan.snapshot = ChangeSnapshot(self.clock().isoformat(), config, fingerprint)
        plan.post_apply_fingerprint = fingerprint
        plan.rollback.available = True
        plan.rollback.status = "awaiting_approval"
        plan.rollback.expected_current_fingerprint = fingerprint
        plan.approval = ChangeApproval(
            state=ApprovalState.REQUIRED,
            authority_version=APPROVAL_AUTHORITY_VERSION,
            approval_kind="rollback",
        )
        if case == "invalidated":
            plan.approval.state = ApprovalState.INVALIDATED
        elif case == "wrong_kind":
            plan.approval.state = ApprovalState.APPROVED
            plan.approval.approval_kind = "apply"
        elif case == "consumed":
            plan.approval.state = ApprovalState.CONSUMED
        elif case == "legacy":
            plan.approval.state = ApprovalState.APPROVED
            plan.approval.authority_version = 1
        if plan.approval.state == ApprovalState.APPROVED:
            plan.approval.channel = APPROVAL_CHANNEL
            plan.approval.approver_principal = "home_assistant_admin_ingress"
            plan.approval.principal_separation_enforced = True
            plan.approval.approval_expires_at = (self.clock() + timedelta(minutes=30)).isoformat()

    async def test_rollback_denials_precede_all_upstream_or_write_activity(self):
        expected_codes = {
            "expired": ErrorCode.EXTERNAL_APPROVAL_REQUIRED,
            "invalidated": ErrorCode.EXTERNAL_APPROVAL_REQUIRED,
            "missing": ErrorCode.EXTERNAL_APPROVAL_REQUIRED,
            "wrong_kind": ErrorCode.EXTERNAL_APPROVAL_REQUIRED,
            "consumed": ErrorCode.EXTERNAL_APPROVAL_REQUIRED,
            "legacy": ErrorCode.APPROVAL_AUTHORITY_MISMATCH,
        }
        with patch("ha_mcp_engineering.dependency.DEPENDENCY_ANALYSIS.invalidate") as invalidate:
            for index, (case, expected) in enumerate(expected_codes.items()):
                with self.subTest(case=case):
                    target = f"rollback_{index}"
                    created = await self.create(target=target)
                    plan = self.repository.get(created["plan_id"])
                    self.prepare_rollback_plan(plan, target, case)
                    self.repository.save(plan)
                    if case == "expired":
                        current_hash = self.service.plan_hash(plan)
                        self.service.approve(created["plan_id"], current_hash)
                        self.clock.advance(minutes=16)
                    reads_before = self.gateway.reads
                    writes_before = self.gateway.writes
                    snapshot_before = self.repository.get(created["plan_id"]).snapshot.fingerprint
                    with self.assertRaises(GovernanceError) as raised:
                        await self.service.rollback_change(
                            created["plan_id"], self.service.plan_hash(self.repository.get(created["plan_id"]))
                        )
                    self.assertEqual(raised.exception.code, expected)
                    self.assertEqual(self.gateway.reads, reads_before)
                    self.assertEqual(self.gateway.writes, writes_before)
                    self.assertEqual(
                        self.repository.get(created["plan_id"]).snapshot.fingerprint,
                        snapshot_before,
                    )
            invalidate.assert_not_called()


class Beta26PublicCompatibilityTests(unittest.TestCase):
    def test_beta25_public_schema_snapshot_and_catalog_are_unchanged(self):
        tools = get_registered_server()._tool_manager.list_tools()
        schemas = {
            tool.name: tool.parameters
            for tool in tools
            if tool.name
            not in {
                "list_dashboards",
                "get_dashboard_config",
                "create_configuration_plan",
            }
        }
        encoded = json.dumps(schemas, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), BETA25_PUBLIC_SCHEMA_SHA256)
        self.assertEqual(len(tools), 41)
        self.assertEqual(len(CAPABILITIES), 25)
        self.assertEqual(len(CAPABILITIES) + len(BETA_NATIVE_CAPABILITIES), 41)
        self.assertEqual(PLANNED_CAPABILITIES, ())
        self.assertEqual(SCHEMA_VERSION, "1")


if __name__ == "__main__":
    unittest.main()
