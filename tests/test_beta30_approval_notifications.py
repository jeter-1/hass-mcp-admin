"""Beta 30 advisory approval notifications and Ingress navigation."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from types import SimpleNamespace
import sys
import tempfile
import unittest

from awesomeversion import AwesomeVersion


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.approval_web import create_approval_application  # noqa: E402
from ha_mcp_engineering.application import validate_settings  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.capabilities import BETA_NATIVE_CAPABILITIES  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
)
from ha_mcp_engineering.governance.approval_notifications import (  # noqa: E402
    ApprovalNotificationManager,
    MAX_NOTIFICATION_QUEUE,
)
from ha_mcp_engineering.governance.models import ApprovalState  # noqa: E402
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import ChangePlanRepository  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402


PRE_PROMOTION_VERSION = "2.2.0-beta.29"
BETA30_VERSION = "2.2.0-beta.30"
CURRENT = {
    "alias": "Beta 30 notification fixture",
    "description": "Before",
    "trigger": [{"platform": "event", "event_type": "beta30_fixture"}],
    "condition": [],
    "action": [
        {
            "service": "notify.fixture",
            "data": {"message": "Synthetic test fixture"},
        }
    ],
    "mode": "single",
}


class FakeGateway:
    def __init__(self):
        self.configs = {"fixture": {**copy.deepcopy(CURRENT), "id": "fixture"}}
        self.writes = 0

    async def get(self, automation_id):
        return copy.deepcopy(self.configs.get(automation_id))

    async def write(self, automation_id, config):
        self.writes += 1
        self.configs[automation_id] = {**copy.deepcopy(config), "id": automation_id}
        return {"result": "ok"}

    async def validate(self):
        return {"result": "valid", "errors": None}


class FakeRestClient:
    def __init__(self, failures=None):
        self.failures = list(failures or [])
        self.calls = []

    async def request(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        if self.failures:
            failure = self.failures.pop(0)
            if failure is not None:
                raise failure
        return {"context": {"id": "synthetic-notification-context"}}


class NotificationGovernanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repository = ChangePlanRepository(self.root / "plans")
        self.audit_path = self.root / "audit.jsonl"
        self.audit = AuditLogger(
            str(self.audit_path), "beta30-synthetic-access-secret"
        )
        self.rest = FakeRestClient()
        self.notifications = ApprovalNotificationManager(
            self.rest,
            self.audit,
            service="notify.mobile_app_josh_test_phone",
            timeout_seconds=2,
            addon_identity_resolver=self.addon_identity,
        )
        self.gateway = FakeGateway()
        self.service = ChangeGovernanceService(
            self.repository,
            self.gateway,
            self.audit,
            approval_notifications=self.notifications,
        )
        self.telemetry, self.context = begin_request("beta30-request")
        self.telemetry.caller_id = "beta30-mcp-caller"

    @staticmethod
    async def addon_identity():
        return SimpleNamespace(slug="repository_hass_mcp_engineering_beta")

    async def asyncTearDown(self):
        end_request(self.context)
        self.temp.cleanup()

    async def create(self):
        proposed = copy.deepcopy(CURRENT)
        proposed["description"] = "After"
        return await self.service.create_plan(
            title="Synthetic Beta 30 approval",
            description="Notification boundary regression",
            operation="update_automation",
            automation_id="fixture",
            proposed_config=proposed,
        )

    async def test_success_is_advisory_allowlisted_and_deduplicated(self):
        created = await self.create()
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        authority_before = self.repository.get(created["plan_id"]).to_dict()
        generation_before = self.repository.generation

        self.assertEqual(
            pending["approval_notification"]["status"], "queued"
        )
        self.assertEqual(
            pending["approval_notification"]["authority"], "none"
        )
        await self.notifications.process_next()

        self.assertEqual(self.repository.generation, generation_before)
        self.assertEqual(
            self.repository.get(created["plan_id"]).to_dict(), authority_before
        )
        self.assertEqual(self.gateway.writes, 0)
        self.assertEqual(len(self.rest.calls), 1)
        method, path, body = self.rest.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(
            path, "/services/notify/mobile_app_josh_test_phone"
        )
        self.assertEqual(body["data"]["actions"][0]["action"], "URI")
        self.assertEqual(
            body["data"]["actions"][0]["title"], "Open Approval Panel"
        )
        self.assertEqual(
            body["data"]["clickAction"],
            f"deep-link://{body['data']['url']}",
        )
        self.assertEqual(
            body["data"]["actions"][0]["uri"],
            body["data"]["url"].removeprefix(
                "homeassistant://navigate"
            ),
        )
        self.assertNotIn(
            "authenticationRequired", body["data"]["actions"][0]
        )
        self.assertIn(
            f"/hassio/ingress/repository_hass_mcp_engineering_beta/plans/{created['plan_id']}",
            body["data"]["url"],
        )
        encoded = json.dumps(body, sort_keys=True)
        self.assertNotIn(pending["challenge_id"], encoded)
        self.assertNotIn(created["plan_hash"], encoded)
        self.assertNotIn("Approve exact plan", encoded)
        self.assertNotIn("Reject plan", encoded)

        repeated = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual(repeated["challenge_id"], pending["challenge_id"])
        self.assertEqual(
            repeated["approval_notification"]["status"], "submitted"
        )
        health = self.notifications.health_snapshot()
        self.assertEqual(health["submitted"], 1)
        self.assertEqual(health["delivered"], 0)
        self.assertFalse(health["handset_delivery_observable"])
        self.assertEqual(self.notifications.queue.qsize(), 0)

    async def test_structured_provider_error_is_truthful_and_non_authoritative(self):
        self.notifications.rest_client = FakeRestClient(
            [HomeAssistantApiError(details={"status": 500})]
        )
        created = await self.create()
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        authority_before = self.repository.get(created["plan_id"]).to_dict()
        await self.notifications.process_next()

        self.assertEqual(
            self.repository.get(created["plan_id"]).to_dict(), authority_before
        )
        self.assertEqual(
            self.repository.get(created["plan_id"]).approval.state,
            ApprovalState.EXTERNAL_PENDING,
        )
        self.assertEqual(self.gateway.writes, 0)
        health = self.notifications.health_snapshot()
        self.assertEqual(health["failed"], 1)
        self.assertEqual(health["last_failure_category"], "provider_rejected")
        records = [
            json.loads(line) for line in self.audit_path.read_text().splitlines()
        ]
        failure = next(
            item
            for item in records
            if item["event"] == "approval_notification_notify_failed"
        )
        self.assertTrue(failure["provider_response_received"])
        self.assertFalse(failure["approval_authority_changed"])
        self.assertNotIn(pending["challenge_id"], json.dumps(failure))

    async def test_timeout_does_not_block_challenge_or_claim_response(self):
        self.notifications.rest_client = FakeRestClient(
            [HomeAssistantTimeoutError()]
        )
        created = await self.create()
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        await self.notifications.process_next()
        persisted = self.repository.get(created["plan_id"])
        self.assertEqual(persisted.approval.state, ApprovalState.EXTERNAL_PENDING)
        self.assertEqual(persisted.approval.challenge_id, pending["challenge_id"])
        failure = next(
            json.loads(line)
            for line in self.audit_path.read_text().splitlines()
            if "approval_notification_notify_failed" in line
        )
        self.assertEqual(failure["failure_category"], "provider_timeout")
        self.assertFalse(failure["provider_response_received"])

    async def test_decision_queues_tag_clear_without_changing_decision(self):
        created = await self.create()
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        await self.notifications.process_next()
        _, csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        decision = await self.service.decide_external_approval(
            plan_id=created["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=created["plan_hash"],
            approval_kind="apply",
            csrf_nonce=csrf,
            decision="reject",
            approver_principal="home_assistant_admin_ingress:synthetic-user",
        )
        authority_before_clear = self.repository.get(
            created["plan_id"]
        ).to_dict()
        await self.notifications.process_next()

        self.assertEqual(decision["status"], "rejected")
        self.assertEqual(
            self.repository.get(created["plan_id"]).to_dict(),
            authority_before_clear,
        )
        self.assertEqual(self.rest.calls[-1][2]["message"], "clear_notification")
        self.assertEqual(
            self.rest.calls[0][2]["data"]["tag"],
            self.rest.calls[-1][2]["data"]["tag"],
        )
        health = self.notifications.health_snapshot()
        self.assertEqual(health["clear_submitted"], 1)
        self.assertEqual(health["cleared"], 0)
        self.assertFalse(health["handset_clear_observable"])
        clear_event = next(
            json.loads(line)
            for line in self.audit_path.read_text().splitlines()
            if "approval_notification_clear_submitted" in line
        )
        self.assertTrue(clear_event["provider_response_received"])
        self.assertFalse(clear_event["handset_outcome_observable"])

    async def test_startup_reconciliation_replaces_only_active_challenges(self):
        created = await self.create()
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        await self.notifications.process_next()
        replacement = ApprovalNotificationManager(
            self.rest,
            self.audit,
            service="notify.mobile_app_josh_test_phone",
            timeout_seconds=2,
            addon_identity_resolver=self.addon_identity,
        )
        replacement.reconcile_pending(self.service.pending_external_reviews())
        self.assertEqual(replacement.queue.qsize(), 1)
        await replacement.process_next()
        self.assertEqual(
            self.rest.calls[-1][2]["data"]["tag"],
            self.rest.calls[0][2]["data"]["tag"],
        )
        self.assertNotIn(pending["challenge_id"], json.dumps(self.rest.calls[-1]))

    async def test_every_terminal_or_authority_transition_queues_tag_clear(self):
        created = await self.create()
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        plan = self.repository.get(created["plan_id"])
        for event in (
            "external_approval_granted",
            "external_approval_rejected",
            "external_approval_expired",
            "external_approval_invalidated",
            "external_approval_consumed",
        ):
            with self.subTest(event=event):
                manager = ApprovalNotificationManager(
                    self.rest,
                    self.audit,
                    service="notify.mobile_app_josh_test_phone",
                    timeout_seconds=2,
                    addon_identity_resolver=self.addon_identity,
                )
                manager.observe(
                    plan,
                    event,
                    request_id="beta30-request",
                    approval_action="plan_approval",
                )
                work = manager.queue.get_nowait()
                self.assertEqual(work.operation, "clear")
                self.assertNotIn(
                    pending["challenge_id"], work.notification_key
                )

    async def test_exact_review_page_has_back_and_section_navigation(self):
        import httpx

        created = await self.create()
        self.service.approve(created["plan_id"], created["plan_hash"])
        app = create_approval_application(_RuntimeShim(self.service))
        transport = httpx.ASGITransport(
            app=app, client=("172.30.32.2", 12345)
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://approval.local",
            headers={
                "X-Ingress-Path": "/api/hassio_ingress/synthetic123",
                "X-Remote-User-Id": "synthetic-admin",
            },
        ) as client:
            review = await client.get(f"/plans/{created['plan_id']}")
        self.assertEqual(review.status_code, 200)
        self.assertIn("Back to pending approvals", review.text)
        self.assertIn("F3 reconciliation", review.text)

    async def test_unverified_runtime_addon_identity_fails_before_notify_dispatch(self):
        async def unavailable_identity():
            raise RuntimeError("synthetic supervisor failure")

        self.notifications.addon_identity_resolver = unavailable_identity
        created = await self.create()
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        await self.notifications.process_next()
        self.assertEqual(self.rest.calls, [])
        self.assertEqual(
            self.repository.get(created["plan_id"]).approval.state,
            ApprovalState.EXTERNAL_PENDING,
        )
        self.assertEqual(
            self.notifications.health_snapshot()["last_failure_category"],
            "transport_failure",
        )
        self.assertEqual(
            self.notifications.status_for(pending["challenge_id"])["status"],
            "notify_failed",
        )

    async def test_bounded_queue_failure_does_not_block_approval(self):
        for _index in range(MAX_NOTIFICATION_QUEUE):
            self.notifications.queue.put_nowait(object())
        created = await self.create()
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual(pending["status"], "approval_pending")
        self.assertEqual(
            self.repository.get(created["plan_id"]).approval.state,
            ApprovalState.EXTERNAL_PENDING,
        )
        self.assertEqual(self.notifications.health_snapshot()["queue_full"], 1)


class ConfigurationBoundaryTests(unittest.TestCase):
    @staticmethod
    def configured(service: str) -> Settings:
        return Settings(
            ha_url="http://supervisor/core",
            ha_token="synthetic-token",
            access_secret="beta30-synthetic-access-secret",
            port=8100,
            audit_path="/tmp/synthetic-beta30-audit.jsonl",
            rate_limit_per_minute=120,
            rate_limit_burst=25,
            destructive_services=frozenset(),
            approval_notification_service=service,
        )

    def test_only_mobile_app_notify_service_is_accepted(self):
        validate_settings(self.configured(""))
        validate_settings(self.configured("notify.mobile_app_josh_phone"))
        for rejected in (
            "notify.notify",
            "light.turn_on",
            "mobile_app_josh_phone",
            "notify.mobile_app_PHONE",
            "notify.mobile_app_phone/../../light",
        ):
            with self.subTest(rejected=rejected):
                with self.assertRaises(Exception):
                    validate_settings(self.configured(rejected))


class _RuntimeShim:
    def __init__(self, service):
        self.service = service

    def require(self):
        return self.service


class _NavigationService:
    def __init__(self, result_status="approved", remaining=0):
        self.result_status = result_status
        self.remaining = remaining

    async def decide_external_approval(self, **_kwargs):
        return {"status": self.result_status}

    def pending_external_reviews(self):
        return [
            {"plan_id": f"remaining-{index}"}
            for index in range(self.remaining)
        ]

    def pending_external_review(self, _plan_id):
        return None


class IngressNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def client(self, service):
        import httpx

        app = create_approval_application(_RuntimeShim(service))
        transport = httpx.ASGITransport(
            app=app, client=("172.30.32.2", 12345)
        )
        return httpx.AsyncClient(
            transport=transport,
            base_url="http://approval.local",
            headers={
                "X-Ingress-Path": "/api/hassio_ingress/synthetic123",
                "X-Remote-User-Id": "synthetic-admin",
            },
        )

    async def test_inbox_and_stale_exact_link_have_consistent_navigation(self):
        async with await self.client(_NavigationService()) as client:
            inbox = await client.get("/")
            stale = await client.get("/plans/opaque-stale-plan")
        for response in (inbox, stale):
            self.assertIn(
                'href="/api/hassio_ingress/synthetic123/"', response.text
            )
            self.assertIn(
                'href="/api/hassio_ingress/synthetic123/f3"', response.text
            )
        self.assertEqual(stale.status_code, 404)

    async def test_decision_uses_current_inbox_and_elevated_continuation(self):
        form = {
            "challenge_id": "opaque-challenge",
            "plan_hash": "a" * 64,
            "approval_kind": "apply",
            "approval_action": "plan_approval",
            "csrf": "synthetic-csrf",
        }
        async with await self.client(
            _NavigationService(result_status="approved", remaining=2)
        ) as client:
            approved = await client.post("/plans/opaque-plan/approve", data=form)
        self.assertIn("Review remaining approvals (2)", approved.text)
        self.assertIn(
            'href="/api/hassio_ingress/synthetic123/"', approved.text
        )

        async with await self.client(
            _NavigationService(result_status="approval_pending")
        ) as client:
            elevated = await client.post("/plans/opaque-plan/approve", data=form)
        self.assertIn("Continue elevated-risk acknowledgement", elevated.text)
        self.assertIn(
            'href="/api/hassio_ingress/synthetic123/plans/opaque-plan"',
            elevated.text,
        )

    async def test_non_ingress_denial_does_not_render_internal_navigation(self):
        import httpx

        app = create_approval_application(_RuntimeShim(_NavigationService()))
        transport = httpx.ASGITransport(
            app=app, client=("127.0.0.1", 12345)
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://approval.local"
        ) as client:
            response = await client.get("/")
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("Approval panel navigation", response.text)


class Beta30ReleaseBoundaryTests(unittest.TestCase):
    def authoritative_versions(self) -> set[str]:
        patterns = (
            (
                BETA_DIR / "config.yaml",
                re.compile(r'(?m)^version: "([^"]+)"$'),
            ),
            (
                BETA_DIR / "ha_mcp_engineering" / "version.py",
                re.compile(r'(?m)^SERVER_VERSION = "([^"]+)"$'),
            ),
            (
                ROOT / "scripts" / "validate_addon_metadata.py",
                re.compile(r'(?m)^BETA_VERSION = "([^"]+)"$'),
            ),
        )
        versions = set()
        for path, pattern in patterns:
            matches = pattern.findall(path.read_text(encoding="utf-8"))
            self.assertEqual(len(matches), 1, str(path))
            versions.add(matches[0])
        return versions

    def test_beta30_staged_or_generated_release_state_is_exact(self):
        versions = self.authoritative_versions()
        self.assertEqual(len(versions), 1)
        current = next(iter(versions))
        if AwesomeVersion(current) > AwesomeVersion(BETA30_VERSION):
            self.skipTest("Beta 30 assertions do not apply after a later release")
        self.assertIn(current, {PRE_PROMOTION_VERSION, BETA30_VERSION})
        marker = ROOT / ".release" / "next-version"
        if current == PRE_PROMOTION_VERSION:
            self.assertEqual(marker.read_text().strip(), BETA30_VERSION)
        elif marker.exists() and AwesomeVersion(
            marker.read_text().strip()
        ) > AwesomeVersion(BETA30_VERSION):
            self.skipTest("Beta 30 is published and a later release is staged")
        else:
            self.assertFalse(marker.exists())
        self.assertIn(
            'version: "1.1.2"',
            (ROOT / "hass_mcp_admin" / "config.yaml").read_text(),
        )

    def test_scope_adds_no_tool_or_provider_fallback(self):
        self.assertEqual(len(BETA_NATIVE_CAPABILITIES), 25)
        source = (
            BETA_DIR
            / "ha_mcp_engineering"
            / "governance"
            / "approval_notifications.py"
        ).read_text()
        self.assertIn("MOBILE_NOTIFY_SERVICE", source)
        self.assertIn('"fallback": "none"', source)
        self.assertNotIn("call_service", source)

    def test_release_documents_exist_and_state_authority_boundary(self):
        release = ROOT / "docs" / "V2_2_0_BETA30_RELEASE_NOTES.md"
        acceptance = ROOT / "docs" / "V2_2_0_BETA30_ACCEPTANCE.md"
        self.assertTrue(release.is_file())
        self.assertTrue(acceptance.is_file())
        self.assertIn("notification is not approval", release.read_text())
        self.assertIn("Open Approval Panel", acceptance.read_text())


if __name__ == "__main__":
    unittest.main()
