"""Beta 32 approval-notification payload and observability regression."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import HomeAssistantApiError  # noqa: E402
from ha_mcp_engineering.governance.approval_notifications import (  # noqa: E402
    ApprovalNotificationManager,
)


SELF_SLUG = "df26dea6_hass_mcp_engineering_beta"
PLAN_ID = "b" * 32
CHALLENGE_ID = "synthetic-beta32-opaque-challenge"


class AndroidCompanionContractRestClient:
    """Model the documented Android mobile-app notification contract."""

    def __init__(self, *, reject: bool = False) -> None:
        self.reject = reject
        self.calls: list[tuple[str, str, object]] = []

    async def request(self, method: str, path: str, body=None):
        self.calls.append((method, path, body))
        if self.reject:
            raise HomeAssistantApiError(details={"status": 400})
        if isinstance(body, dict) and body.get("message") == "clear_notification":
            return {"context": {"id": "synthetic-beta32-clear-submission"}}
        data = body.get("data") if isinstance(body, dict) else None
        actions = data.get("actions") if isinstance(data, dict) else None
        action = actions[0] if isinstance(actions, list) and actions else None
        navigation_uri = data.get("url") if isinstance(data, dict) else None
        android_navigation_uri = (
            f"deep-link://{navigation_uri}"
            if isinstance(navigation_uri, str)
            else None
        )
        if (
            not isinstance(navigation_uri, str)
            or data.get("clickAction") != android_navigation_uri
            or not isinstance(action, dict)
            or action.get("action") != "URI"
            or action.get("uri") != android_navigation_uri
            or "authenticationRequired" in action
        ):
            raise HomeAssistantApiError(details={"status": 400})
        return {"context": {"id": "synthetic-beta32-submission"}}


class Beta32ApprovalNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def _manager(self, rest_client):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        audit_path = Path(self.temp.name) / "audit.jsonl"
        audit = AuditLogger(str(audit_path), "synthetic-beta32-audit-secret")

        async def identity():
            return type("Identity", (), {"slug": SELF_SLUG})()

        manager = ApprovalNotificationManager(
            rest_client,
            audit,
            service="notify.mobile_app_synthetic_pixel",
            timeout_seconds=2,
            addon_identity_resolver=identity,
        )
        manager._enqueue(
            "notify",
            PLAN_ID,
            CHALLENGE_ID,
            "plan_approval",
            "synthetic-beta32-request",
        )
        return manager, audit_path

    async def _clear_manager(self, rest_client):
        manager, audit_path = await self._manager(rest_client)
        manager.queue.get_nowait()
        manager.queue.task_done()
        manager._scheduled.clear()
        manager._enqueue(
            "clear",
            PLAN_ID,
            CHALLENGE_ID,
            "plan_approval",
            "synthetic-beta32-clear-request",
        )
        return manager, audit_path

    async def test_documented_android_payload_is_submitted_once(self):
        rest = AndroidCompanionContractRestClient()
        manager, audit_path = await self._manager(rest)

        await manager.process_next()

        self.assertEqual(len(rest.calls), 1)
        method, path, body = rest.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/services/notify/mobile_app_synthetic_pixel")
        review_url = (
            "homeassistant://navigate/hassio/ingress/"
            f"{SELF_SLUG}/plans/{PLAN_ID}"
        )
        self.assertEqual(body["data"]["url"], review_url)
        android_navigation_uri = f"deep-link://{review_url}"
        self.assertEqual(
            body["data"]["clickAction"], android_navigation_uri
        )
        self.assertEqual(
            body["data"]["actions"][0]["uri"], android_navigation_uri
        )
        self.assertNotIn(
            "authenticationRequired", body["data"]["actions"][0]
        )

        health = manager.health_snapshot()
        self.assertEqual(health["submitted"], 1)
        self.assertEqual(health["delivered"], 0)
        self.assertFalse(health["handset_delivery_observable"])
        self.assertEqual(health["failed"], 0)
        self.assertEqual(
            manager.status_for(CHALLENGE_ID)["status"], "submitted"
        )
        records = [
            json.loads(line) for line in audit_path.read_text().splitlines()
        ]
        submitted = next(
            record
            for record in records
            if record["event"] == "approval_notification_notify_submitted"
        )
        self.assertEqual(submitted["result_status"], "success")
        self.assertTrue(submitted["provider_dispatch_occurred"])
        self.assertTrue(submitted["provider_response_received"])
        self.assertFalse(submitted["approval_authority_changed"])
        self.assertEqual(
            submitted["submission_semantics"],
            "home_assistant_service_response_only",
        )
        self.assertFalse(submitted["handset_outcome_observable"])

    async def test_structured_rejection_is_not_submission_or_delivery(self):
        rest = AndroidCompanionContractRestClient(reject=True)
        manager, audit_path = await self._manager(rest)

        await manager.process_next()

        health = manager.health_snapshot()
        self.assertEqual(health["submitted"], 0)
        self.assertEqual(health["delivered"], 0)
        self.assertEqual(health["failed"], 1)
        self.assertEqual(health["last_failure_category"], "provider_rejected")
        failure = next(
            json.loads(line)
            for line in audit_path.read_text().splitlines()
            if "approval_notification_notify_failed" in line
        )
        self.assertTrue(failure["provider_response_received"])
        self.assertFalse(failure["approval_authority_changed"])
        self.assertEqual(failure["fallback"], "none")

    async def test_clear_response_is_submitted_not_claimed_as_handset_clear(self):
        rest = AndroidCompanionContractRestClient()
        manager, audit_path = await self._clear_manager(rest)

        await manager.process_next()

        self.assertEqual(len(rest.calls), 1)
        self.assertEqual(rest.calls[0][2]["message"], "clear_notification")
        health = manager.health_snapshot()
        self.assertEqual(health["clear_submitted"], 1)
        self.assertEqual(health["cleared"], 0)
        self.assertFalse(health["handset_clear_observable"])
        self.assertEqual(
            manager.status_for(CHALLENGE_ID)["status"],
            "clear_submitted",
        )
        submitted = next(
            json.loads(line)
            for line in audit_path.read_text().splitlines()
            if "approval_notification_clear_submitted" in line
        )
        self.assertTrue(submitted["provider_response_received"])
        self.assertFalse(submitted["handset_outcome_observable"])


if __name__ == "__main__":
    unittest.main()
