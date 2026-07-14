import asyncio
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.approval_web import create_approval_application  # noqa: E402
from ha_mcp_engineering.application import _serve, validate_settings  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalState,
    ChangeOperation,
    PlanStatus,
    RiskLevel,
)
from ha_mcp_engineering.governance.normalize import normalize_automation, stable_hash  # noqa: E402
from ha_mcp_engineering.governance.runtime import GovernanceRuntime  # noqa: E402
from ha_mcp_engineering.governance.service import (  # noqa: E402
    APPROVAL_AUTHORITY_VERSION,
    APPROVAL_CHANNEL,
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import ChangePlanRepository  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402


CURRENT = {
    "alias": "Approval fixture",
    "description": "Before",
    "trigger": [{"platform": "event", "event_type": "beta25_fixture"}],
    "condition": [],
    "action": [{"service": "notify.fixture", "data": {"message": "No physical action"}}],
    "mode": "single",
}


class Clock:
    def __init__(self):
        self.value = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


class FakeGateway:
    def __init__(self):
        self.configs = {"fixture": {**copy.deepcopy(CURRENT), "id": "fixture"}}
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


class ExternalApprovalTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.gateway = FakeGateway()
        self.root = Path(self.temp.name) / "plans"
        self.audit_path = Path(self.temp.name) / "audit.jsonl"
        self.repository = ChangePlanRepository(self.root)
        self.service = ChangeGovernanceService(
            self.repository,
            self.gateway,
            AuditLogger(str(self.audit_path), "beta25-test-access-secret"),
            now=self.clock,
            sensitive_values=("beta25-test-access-secret", "supervisor-test-token"),
        )
        self.telemetry, self.context = begin_request("beta25-request")
        self.telemetry.caller_id = "mcp-caller-a"

    async def asyncTearDown(self):
        end_request(self.context)
        self.temp.cleanup()

    async def create(self, *, description="After", title="Approval fixture"):
        proposed = copy.deepcopy(CURRENT)
        proposed["description"] = description
        return await self.service.create_plan(
            title=title,
            description="Governed description-only update",
            operation="update_automation",
            automation_id="fixture",
            proposed_config=proposed,
        )

    async def grant(self, created, *, kind="apply", principal="home_assistant_admin_ingress:user-1"):
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        _, csrf = await self.service.issue_external_csrf(created["plan_id"], pending["challenge_id"])
        granted = await self.service.decide_external_approval(
            plan_id=created["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=created["plan_hash"],
            approval_kind=kind,
            csrf_nonce=csrf,
            decision="approve",
            approver_principal=principal,
        )
        return pending, granted


class PrincipalSeparationTests(ExternalApprovalTestCase):
    async def test_mcp_request_cannot_self_approve_or_apply(self):
        created = await self.create()
        reads_after_plan = self.gateway.reads
        pending = self.service.approve(created["plan_id"], created["plan_hash"], "human approved")
        self.assertEqual(pending["status"], "approval_pending")
        self.assertTrue(pending["external_approval_required"])
        plan = self.repository.get(created["plan_id"])
        self.assertEqual(plan.status, PlanStatus.AWAITING_APPROVAL)
        self.assertEqual(plan.approval.state, ApprovalState.EXTERNAL_PENDING)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.EXTERNAL_APPROVAL_REQUIRED)
        self.assertEqual(self.gateway.reads, reads_after_plan)
        self.assertEqual(self.gateway.writes, 0)
        self.assertIsNone(self.repository.get(created["plan_id"]).snapshot)
        self.assertEqual(self.repository.get(created["plan_id"]).approval.state, ApprovalState.EXTERNAL_PENDING)
        events = [json.loads(line)["event"] for line in self.audit_path.read_text().splitlines()]
        self.assertIn("external_approval_requested", events)
        self.assertNotIn("external_approval_granted", events)

    async def test_different_mcp_identity_and_note_do_not_grant_or_rotate_challenge(self):
        created = await self.create()
        first = self.service.approve(created["plan_id"], created["plan_hash"], "first")
        self.telemetry.caller_id = "mcp-caller-b"
        second = self.service.approve(created["plan_id"], created["plan_hash"], "I am the human")
        self.assertEqual(first["challenge_id"], second["challenge_id"])
        self.assertEqual(first["challenge_expires_at"], second["challenge_expires_at"])
        self.assertEqual(self.repository.get(created["plan_id"]).approval.state, ApprovalState.EXTERNAL_PENDING)

    async def test_valid_external_grant_allows_single_use_apply(self):
        created = await self.create()
        pending, result = await self.grant(created)
        self.assertEqual(result["status"], "approved")
        approved = self.repository.get(created["plan_id"])
        self.assertEqual(approved.approval.authority_version, APPROVAL_AUTHORITY_VERSION)
        self.assertEqual(approved.approval.channel, APPROVAL_CHANNEL)
        self.assertTrue(approved.approval.principal_separation_enforced)
        self.assertEqual(approved.approval.bound_plan_hash, created["plan_hash"])
        applied = await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(self.gateway.writes, 1)
        self.assertEqual(self.repository.get(created["plan_id"]).approval.state, ApprovalState.CONSUMED)
        duplicate = await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(duplicate["status"], "already_applied")
        self.assertEqual(self.gateway.writes, 1)
        with self.assertRaises(GovernanceError):
            await self.service.issue_external_csrf(created["plan_id"], pending["challenge_id"])

    async def test_challenge_identifier_is_not_a_bearer_credential(self):
        created = await self.create()
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        await self.service.issue_external_csrf(created["plan_id"], pending["challenge_id"])
        with self.assertRaises(GovernanceError) as raised:
            await self.service.decide_external_approval(
                plan_id=created["plan_id"], challenge_id=pending["challenge_id"],
                expected_plan_hash=created["plan_hash"], approval_kind="apply",
                csrf_nonce=pending["challenge_id"], decision="approve",
                approver_principal="home_assistant_admin_ingress",
            )
        self.assertEqual(raised.exception.code, ErrorCode.EXTERNAL_APPROVAL_INVALID)

    async def test_wrong_hash_kind_and_plan_binding_fail_closed(self):
        created = await self.create()
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        _, csrf = await self.service.issue_external_csrf(created["plan_id"], pending["challenge_id"])
        for field, value in (("expected_plan_hash", "0" * 64), ("approval_kind", "rollback")):
            arguments = {
                "plan_id": created["plan_id"], "challenge_id": pending["challenge_id"],
                "expected_plan_hash": created["plan_hash"], "approval_kind": "apply",
                "csrf_nonce": csrf, "decision": "approve",
                "approver_principal": "home_assistant_admin_ingress",
            }
            arguments[field] = value
            with self.assertRaises(GovernanceError):
                await self.service.decide_external_approval(**arguments)
        self.assertEqual(self.gateway.writes, 0)

    async def test_persisted_binding_changes_invalidate_challenge(self):
        mutators = (
            lambda plan: setattr(plan, "plan_version", plan.plan_version + 1),
            lambda plan: setattr(plan.target, "target_id", "different_fixture"),
            lambda plan: setattr(plan, "operation", ChangeOperation.CREATE_AUTOMATION),
            lambda plan: setattr(plan.risk, "level", RiskLevel.MEDIUM),
        )
        for index, mutate in enumerate(mutators):
            with self.subTest(binding=index):
                created = await self.create(description=f"Binding {index}")
                pending = self.service.approve(created["plan_id"], created["plan_hash"])
                plan = self.repository.get(created["plan_id"])
                mutate(plan)
                self.repository.save(plan)
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.issue_external_csrf(
                        created["plan_id"], pending["challenge_id"]
                    )
                self.assertEqual(raised.exception.code, ErrorCode.EXTERNAL_APPROVAL_INVALID)

    async def test_rejection_is_terminal_and_historical(self):
        created = await self.create()
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        _, csrf = await self.service.issue_external_csrf(created["plan_id"], pending["challenge_id"])
        rejected = await self.service.decide_external_approval(
            plan_id=created["plan_id"], challenge_id=pending["challenge_id"],
            expected_plan_hash=created["plan_hash"], approval_kind="apply", csrf_nonce=csrf,
            decision="reject", approver_principal="home_assistant_admin_ingress:user-2",
        )
        self.assertEqual(rejected["status"], "rejected")
        plan = self.repository.get(created["plan_id"])
        self.assertEqual(plan.status, PlanStatus.REJECTED)
        with self.assertRaises(GovernanceError) as raised:
            self.service.approve(created["plan_id"], created["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.CHANGE_PLAN_REJECTED)
        with self.assertRaises(GovernanceError):
            await self.service.apply(created["plan_id"])
        self.assertEqual(self.gateway.writes, 0)

    async def test_expired_challenge_is_replaced_without_revalidating_old_nonce(self):
        created = await self.create()
        first = self.service.approve(created["plan_id"], created["plan_hash"])
        _, old_csrf = await self.service.issue_external_csrf(created["plan_id"], first["challenge_id"])
        self.clock.advance(minutes=16)
        second = self.service.approve(created["plan_id"], created["plan_hash"])
        self.assertNotEqual(first["challenge_id"], second["challenge_id"])
        with self.assertRaises(GovernanceError):
            await self.service.decide_external_approval(
                plan_id=created["plan_id"], challenge_id=first["challenge_id"],
                expected_plan_hash=created["plan_hash"], approval_kind="apply", csrf_nonce=old_csrf,
                decision="approve", approver_principal="home_assistant_admin_ingress",
            )

    async def test_superseding_plan_invalidates_existing_challenge(self):
        first = await self.create(description="First")
        pending = self.service.approve(first["plan_id"], first["plan_hash"])
        second = await self.create(description="Second")
        self.assertNotEqual(first["plan_id"], second["plan_id"])
        old = self.repository.get(first["plan_id"])
        self.assertEqual(old.status, PlanStatus.SUPERSEDED)
        self.assertEqual(old.approval.state, ApprovalState.INVALIDATED)
        with self.assertRaises(GovernanceError):
            await self.service.issue_external_csrf(first["plan_id"], pending["challenge_id"])
        self.assertEqual(self.service.health_summary()["invalidated_challenge_count"], 1)

    async def test_restart_preserves_pending_and_consumed_state(self):
        created = await self.create()
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        reloaded = ChangeGovernanceService(self.repository, self.gateway, now=self.clock)
        self.assertEqual(reloaded.get_plan(created["plan_id"])["approval"]["state"], "external_pending")
        _, csrf = await reloaded.issue_external_csrf(created["plan_id"], pending["challenge_id"])
        await reloaded.decide_external_approval(
            plan_id=created["plan_id"], challenge_id=pending["challenge_id"],
            expected_plan_hash=created["plan_hash"], approval_kind="apply", csrf_nonce=csrf,
            decision="approve", approver_principal="home_assistant_admin_ingress",
        )
        await reloaded.apply(created["plan_id"], created["plan_hash"])
        recovered = ChangeGovernanceService(self.repository, self.gateway, now=self.clock)
        self.assertEqual(recovered.get_plan(created["plan_id"])["approval"]["state"], "consumed")

    async def test_restart_preserves_approved_rejected_and_expired_states(self):
        approved = await self.create(description="Approved restart")
        await self.grant(approved)
        approved_reload = ChangeGovernanceService(self.repository, self.gateway, now=self.clock)
        approved_plan = approved_reload.get_plan(approved["plan_id"])
        self.assertEqual(approved_plan["approval"]["state"], "approved")
        self.assertTrue(approved_plan["apply_allowed"])

        rejected = await self.create(description="Rejected restart")
        pending = self.service.approve(rejected["plan_id"], rejected["plan_hash"])
        _, csrf = await self.service.issue_external_csrf(rejected["plan_id"], pending["challenge_id"])
        await self.service.decide_external_approval(
            plan_id=rejected["plan_id"], challenge_id=pending["challenge_id"],
            expected_plan_hash=rejected["plan_hash"], approval_kind="apply", csrf_nonce=csrf,
            decision="reject", approver_principal="home_assistant_admin_ingress",
        )
        rejected_reload = ChangeGovernanceService(self.repository, self.gateway, now=self.clock)
        self.assertEqual(rejected_reload.get_plan(rejected["plan_id"])["status"], "rejected")

        expiring = await self.create(description="Expired restart")
        self.service.approve(expiring["plan_id"], expiring["plan_hash"])
        self.clock.advance(minutes=16)
        self.service.pending_external_reviews()
        expired_reload = ChangeGovernanceService(self.repository, self.gateway, now=self.clock)
        self.assertEqual(expired_reload.get_plan(expiring["plan_id"])["approval"]["state"], "expired")

    async def test_beta24_legacy_active_approval_cannot_apply(self):
        created = await self.create()
        plan = self.repository.get(created["plan_id"])
        plan.status = PlanStatus.APPROVED
        plan.approval.state = ApprovalState.APPROVED
        plan.approval.authority_version = 1
        legacy_hash = stable_hash(
            {
                "plan_id": plan.plan_id,
                "plan_version": plan.plan_version,
                "operation": plan.operation.value,
                "target_type": plan.target_type,
                "target_id": plan.target_id,
                "expires_at": plan.expires_at,
                "current_state_fingerprint": plan.current_state_fingerprint,
                "proposed_config_hash": stable_hash(
                    normalize_automation(plan.proposed_config) or {}
                ),
                "normalization_version": plan.normalization_version,
                "risk_level": plan.risk.level.value,
                "approval_kind": plan.approval.approval_kind,
                "rollback_expected_fingerprint": plan.rollback.expected_current_fingerprint,
            }
        )
        self.assertEqual(self.service.plan_hash(plan), legacy_hash)
        plan.approval.bound_plan_hash = self.service.plan_hash(plan)
        self.repository.save(plan)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.APPROVAL_AUTHORITY_MISMATCH)
        self.assertEqual(self.gateway.writes, 0)

    async def test_beta24_terminal_records_remain_readable_without_authority_upgrade(self):
        created = await self.create()
        path = self.repository._path(created["plan_id"])
        original = json.loads(path.read_text(encoding="utf-8"))
        for status in ("applied", "rolled_back", "expired", "superseded", "failed"):
            with self.subTest(status=status):
                legacy = copy.deepcopy(original)
                legacy["status"] = status
                legacy["approval"].pop("authority_version", None)
                path.write_text(json.dumps(legacy), encoding="utf-8")
                readable = self.service.get_plan(created["plan_id"])
                self.assertEqual(readable["status"], status)
                self.assertEqual(readable["approval"]["authority_version"], 1)

    async def test_beta24_rollback_pending_authority_cannot_execute(self):
        created = await self.create()
        plan = self.repository.get(created["plan_id"])
        plan.status = PlanStatus.ROLLBACK_PENDING
        plan.approval.state = ApprovalState.APPROVED
        plan.approval.authority_version = 1
        plan.snapshot = copy.deepcopy(plan.snapshot) or None
        if plan.snapshot is None:
            from ha_mcp_engineering.governance.models import ChangeSnapshot
            plan.snapshot = ChangeSnapshot(self.clock().isoformat(), copy.deepcopy(CURRENT), "legacy")
        plan.rollback.expected_current_fingerprint = "legacy"
        self.repository.save(plan)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"], "legacy-hash")
        self.assertEqual(raised.exception.code, ErrorCode.APPROVAL_AUTHORITY_MISMATCH)
        self.assertEqual(self.gateway.writes, 0)

    async def test_apply_and_rollback_use_distinct_external_authority(self):
        created = await self.create()
        await self.grant(created)
        await self.service.apply(created["plan_id"], created["plan_hash"])
        rollback = await self.service.rollback_change(created["plan_id"])
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"], rollback["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.EXTERNAL_APPROVAL_REQUIRED)
        rollback_created = {"plan_id": created["plan_id"], "plan_hash": rollback["plan_hash"]}
        pending, _ = await self.grant(rollback_created, kind="rollback")
        self.assertEqual(pending["approval_kind"], "rollback")
        result = await self.service.rollback_change(created["plan_id"], rollback["plan_hash"])
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(self.gateway.writes, 2)

    async def test_concurrent_external_submissions_grant_once(self):
        created = await self.create()
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        _, csrf = await self.service.issue_external_csrf(created["plan_id"], pending["challenge_id"])

        async def decide(value):
            try:
                return await self.service.decide_external_approval(
                    plan_id=created["plan_id"], challenge_id=pending["challenge_id"],
                    expected_plan_hash=created["plan_hash"], approval_kind="apply",
                    csrf_nonce=csrf, decision=value,
                    approver_principal="home_assistant_admin_ingress",
                )
            except GovernanceError as exc:
                return exc.code

        outcomes = await asyncio.gather(decide("approve"), decide("reject"))
        self.assertEqual(sum(isinstance(item, dict) for item in outcomes), 1)
        self.assertEqual(sum(item == ErrorCode.EXTERNAL_APPROVAL_INVALID for item in outcomes), 1)

    async def test_external_approval_health_counters_are_persisted_event_counts(self):
        created = await self.create()
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        _, csrf = await self.service.issue_external_csrf(created["plan_id"], pending["challenge_id"])
        with self.assertRaises(GovernanceError):
            await self.service.decide_external_approval(
                plan_id=created["plan_id"], challenge_id=pending["challenge_id"],
                expected_plan_hash=created["plan_hash"], approval_kind="apply",
                csrf_nonce="wrong", decision="approve",
                approver_principal="home_assistant_admin_ingress",
            )
        await self.service.decide_external_approval(
            plan_id=created["plan_id"], challenge_id=pending["challenge_id"],
            expected_plan_hash=created["plan_hash"], approval_kind="apply",
            csrf_nonce=csrf, decision="approve",
            approver_principal="home_assistant_admin_ingress",
        )
        await self.service.apply(created["plan_id"], created["plan_hash"])
        health = self.service.health_summary()
        self.assertEqual(health["approval_authority_version"], 2)
        self.assertEqual(health["pending_challenge_count"], 0)
        self.assertEqual(health["granted_approval_count"], 1)
        self.assertEqual(health["approval_consumption_count"], 1)
        self.assertEqual(health["last_approval_failure_category"], "external_approval_invalid")


class RuntimeShim:
    def __init__(self, service):
        self.service = service

    def require(self):
        return self.service


class IngressBoundaryTests(ExternalApprovalTestCase):
    async def _client(self, *, peer="172.30.32.2"):
        import httpx

        app = create_approval_application(RuntimeShim(self.service))
        transport = httpx.ASGITransport(app=app, client=(peer, 12345))
        return httpx.AsyncClient(
            transport=transport,
            base_url="http://approval.local",
            headers={"X-Ingress-Path": "/api/hassio_ingress/testtoken123", "X-Remote-User-Id": "ha-user-1"},
        )

    async def test_direct_peer_and_missing_ingress_header_cannot_view_or_mutate(self):
        created = await self.create()
        self.service.approve(created["plan_id"], created["plan_hash"])
        async with await self._client(peer="127.0.0.1") as client:
            self.assertEqual((await client.get("/")).status_code, 403)
        async with await self._client() as client:
            response = await client.get("/", headers={"X-Ingress-Path": ""})
            self.assertEqual(response.status_code, 403)

    async def test_server_rendered_review_escapes_untrusted_content_and_has_headers(self):
        created = await self.create(
            description='<script>alert(1)</script><img src=x onerror=alert(2)>\u202e',
            title='<form action="https://attacker.example">unsafe</form> beta25-test-access-secret',
        )
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        async with await self._client() as client:
            inbox = await client.get("/")
            self.assertEqual(inbox.status_code, 200)
            self.assertIn("&lt;form", inbox.text)
            review = await client.get(f"/plans/{created['plan_id']}")
            self.assertEqual(review.status_code, 200)
            self.assertNotIn("<script>", review.text)
            self.assertNotIn("<img src=x", review.text)
            self.assertIn("&lt;script&gt;", review.text)
            self.assertNotIn("\u202e", review.text)
            self.assertEqual(review.headers["cache-control"], "no-store")
            self.assertEqual(review.headers["referrer-policy"], "no-referrer")
            self.assertIn("default-src 'none'", review.headers["content-security-policy"])
            self.assertNotIn("beta25-test-access-secret", review.text)
            self.assertIn(pending["challenge_id"], review.text)

    async def test_request_note_is_sanitized_before_persistence_and_rendering(self):
        created = await self.create()
        self.service.approve(
            created["plan_id"], created["plan_hash"],
            "Authorization: Bearer beta25-test-access-secret <script>approve</script>",
        )
        persisted = self.repository.get(created["plan_id"])
        self.assertNotIn("beta25-test-access-secret", persisted.approval.request_note)
        async with await self._client() as client:
            review = await client.get(f"/plans/{created['plan_id']}")
            self.assertNotIn("beta25-test-access-secret", review.text)
            self.assertNotIn("<script>", review.text)
            self.assertIn("not human approval", review.text)

    async def test_post_only_csrf_and_content_type_enforcement(self):
        created = await self.create()
        self.service.approve(created["plan_id"], created["plan_hash"])
        async with await self._client() as client:
            review = await client.get(f"/plans/{created['plan_id']}")
            csrf = re.search(r'name="csrf" value="([^"]+)"', review.text).group(1)
            challenge = re.search(r'name="challenge_id" value="([^"]+)"', review.text).group(1)
            plan_hash = re.search(r'name="plan_hash" value="([^"]+)"', review.text).group(1)
            self.assertEqual((await client.get(f"/plans/{created['plan_id']}/approve")).status_code, 405)
            self.assertEqual(
                (await client.post(f"/plans/{created['plan_id']}/approve", content="x", headers={"Content-Type": "text/plain"})).status_code,
                415,
            )
            bad = {"challenge_id": challenge, "plan_hash": plan_hash, "approval_kind": "apply", "csrf": "wrong"}
            self.assertEqual((await client.post(f"/plans/{created['plan_id']}/approve", data=bad)).status_code, 409)
            good = {**bad, "csrf": csrf}
            self.assertEqual((await client.post(f"/plans/{created['plan_id']}/approve", data=good)).status_code, 200)
            self.assertEqual((await client.post(f"/plans/{created['plan_id']}/approve", data=good)).status_code, 409)
        plan = self.repository.get(created["plan_id"])
        self.assertEqual(plan.approval.approver_principal, "home_assistant_admin_ingress:ha-user-1")

    async def test_oversized_form_is_rejected_without_decision(self):
        created = await self.create()
        self.service.approve(created["plan_id"], created["plan_hash"])
        async with await self._client() as client:
            response = await client.post(
                f"/plans/{created['plan_id']}/approve",
                content=b"x" * 8_193,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            self.assertEqual(response.status_code, 413)
        self.assertEqual(self.repository.get(created["plan_id"]).approval.state, ApprovalState.EXTERNAL_PENDING)

    async def test_malicious_identity_header_is_not_recorded(self):
        created = await self.create()
        self.service.approve(created["plan_id"], created["plan_hash"])
        async with await self._client() as client:
            review = await client.get(f"/plans/{created['plan_id']}")
            values = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', review.text))
            response = await client.post(
                f"/plans/{created['plan_id']}/approve",
                data=values,
                headers={"X-Remote-User-Id": "<script>fake</script>"},
            )
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.repository.get(created["plan_id"]).approval.approver_principal, "home_assistant_admin_ingress")


class MetadataBoundaryTests(unittest.TestCase):
    def test_ingress_is_admin_only_internal_and_separate_from_mcp(self):
        import yaml

        config = yaml.safe_load((BETA_DIR / "config.yaml").read_text(encoding="utf-8"))
        self.assertTrue(config["ingress"])
        self.assertTrue(config["panel_admin"])
        self.assertEqual(config["ingress_port"], 8110)
        self.assertNotIn("8110/tcp", config.get("ports", {}))
        self.assertEqual(config["ports"]["8100/tcp"], 8100)
        self.assertNotIn("auth_api", config)

    def test_mcp_listener_does_not_register_ingress_routes(self):
        source = (BETA_DIR / "ha_mcp_engineering" / "application.py").read_text(encoding="utf-8")
        self.assertIn("create_approval_application", source)
        self.assertIn("settings.ingress_port", source)
        governance_tools = (BETA_DIR / "ha_mcp_engineering" / "tools" / "governance.py").read_text(encoding="utf-8")
        self.assertNotIn("issue_external_csrf", governance_tools)
        self.assertNotIn("decide_external_approval", governance_tools)


class ListenerStartTests(unittest.IsolatedAsyncioTestCase):
    def configured(self, **overrides):
        values = {
            "ha_url": "http://supervisor/core", "ha_token": "test-token",
            "access_secret": "beta25-listener-access-secret", "port": 8100,
            "audit_path": "/data/audit.jsonl", "rate_limit_per_minute": 120,
            "rate_limit_burst": 25, "destructive_services": frozenset(),
            "ingress_port": 8110,
        }
        values.update(overrides)
        return Settings(**values)

    async def test_two_distinct_listeners_are_built_and_started(self):
        instances = []

        class FakeConfig:
            def __init__(self, app, **kwargs):
                self.app = app
                self.port = kwargs["port"]

        class FakeServer:
            def __init__(self, config):
                self.config = config
                self.should_exit = False
                self.install_signal_handlers = lambda: None
                self.started = False
                instances.append(self)

            async def serve(self):
                self.started = True
                await asyncio.sleep(0)

        mcp_app, approval_app = object(), object()
        with patch("ha_mcp_engineering.application.uvicorn.Config", FakeConfig), patch(
            "ha_mcp_engineering.application.uvicorn.Server", FakeServer
        ), patch("ha_mcp_engineering.application.create_application", return_value=mcp_app), patch(
            "ha_mcp_engineering.application.create_approval_application", return_value=approval_app
        ):
            await _serve(self.configured())
        self.assertEqual({item.config.port for item in instances}, {8100, 8110})
        self.assertTrue(all(item.started for item in instances))
        self.assertEqual({item.config.app for item in instances}, {mcp_app, approval_app})

    async def test_private_listener_failure_stops_the_process(self):
        class FakeConfig:
            def __init__(self, app, **kwargs):
                self.port = kwargs["port"]

        class FakeServer:
            def __init__(self, config):
                self.config = config
                self.should_exit = False
                self.install_signal_handlers = lambda: None

            async def serve(self):
                if self.config.port == 8110:
                    raise OSError("approval listener unavailable")
                while not self.should_exit:
                    await asyncio.sleep(0)

        with patch("ha_mcp_engineering.application.uvicorn.Config", FakeConfig), patch(
            "ha_mcp_engineering.application.uvicorn.Server", FakeServer
        ), patch("ha_mcp_engineering.application.create_application", return_value=object()), patch(
            "ha_mcp_engineering.application.create_approval_application", return_value=object()
        ):
            with self.assertRaisesRegex(OSError, "approval listener unavailable"):
                await _serve(self.configured())

    def test_listener_ports_cannot_collide(self):
        with self.assertRaises(Exception):
            validate_settings(self.configured(ingress_port=8100))
