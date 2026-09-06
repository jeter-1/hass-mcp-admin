"""RC1 approval-notification app-panel navigation regressions."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ha_mcp_engineering.governance.approval_notifications import (  # noqa: E402
    ApprovalNotificationManager,
)
from ha_mcp_engineering.governance.models import ApprovalState  # noqa: E402
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.providers.supervisor_self import (  # noqa: E402
    SupervisorSelfAddonIdentityResolver,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
)


SELF_SLUG = "df26dea6_hass_mcp_engineering_beta"
PLAN_ID = "a" * 32
SECOND_PLAN_ID = "b" * 32
CHALLENGE_ID = "synthetic-rc1-opaque-challenge"
CURRENT = {
    "alias": "RC1 notification refusal fixture",
    "description": "Before",
    "trigger": [{"platform": "event", "event_type": "rc1_fixture"}],
    "condition": [],
    "action": [
        {
            "service": "notify.fixture",
            "data": {"message": "Synthetic test fixture"},
        }
    ],
    "mode": "single",
}


class CapturingRestClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    async def request(self, method: str, path: str, body=None):
        self.calls.append((method, path, body))
        return {"context": {"id": "synthetic-rc1-submission"}}


class ScriptedRestClient(CapturingRestClient):
    def __init__(self, outcome: object) -> None:
        super().__init__()
        self.outcome = outcome

    async def request(self, method: str, path: str, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class FakeOperationGateway:
    def __init__(self) -> None:
        self.configs = {
            "fixture": {**copy.deepcopy(CURRENT), "id": "fixture"}
        }
        self.writes = 0

    async def get(self, automation_id: str):
        return copy.deepcopy(self.configs.get(automation_id))

    async def write(self, automation_id: str, config: dict):
        self.writes += 1
        self.configs[automation_id] = {
            **copy.deepcopy(config),
            "id": automation_id,
        }
        return {"result": "ok"}

    async def validate(self):
        return {"result": "valid", "errors": None}


class Rc1ApprovalNotificationNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def _manager(self, rest: CapturingRestClient):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        async def identity():
            return type("Identity", (), {"slug": SELF_SLUG})()

        manager = ApprovalNotificationManager(
            rest,
            AuditLogger(
                str(Path(temp.name) / "audit.jsonl"),
                "synthetic-rc1-audit-secret",
            ),
            service="notify.mobile_app_synthetic_phone",
            timeout_seconds=2,
            addon_identity_resolver=identity,
        )
        return manager

    @staticmethod
    def _enqueue(
        manager: ApprovalNotificationManager,
        plan_id: str,
        challenge_id: str,
    ) -> None:
        manager._enqueue(
            "notify",
            plan_id,
            challenge_id,
            "plan_approval",
            "synthetic-rc1-request",
        )

    async def test_every_interaction_uses_exact_same_server_app_panel_path(self):
        rest = CapturingRestClient()
        manager = await self._manager(rest)
        self._enqueue(manager, PLAN_ID, CHALLENGE_ID)

        await manager.process_next()

        self.assertEqual(len(rest.calls), 1)
        method, service_path, body = rest.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(
            service_path,
            "/services/notify/mobile_app_synthetic_phone",
        )
        expected = f"/app/{SELF_SLUG}"
        self.assertEqual(body["data"]["url"], expected)
        self.assertEqual(body["data"]["clickAction"], expected)
        self.assertEqual(
            body["data"]["actions"],
            [
                {
                    "action": "URI",
                    "title": "Open Approval Panel",
                    "uri": expected,
                }
            ],
        )

        encoded = json.dumps(body, sort_keys=True)
        for forbidden in (
            "/hassio/ingress/",
            "deep-link://",
            "homeassistant://",
            "http://",
            "https://",
            "api/hassio_ingress",
            CHALLENGE_ID,
            "challenge_id",
            "csrf",
            "approval_token",
            "plan_hash",
            "approve",
            "reject",
        ):
            self.assertNotIn(forbidden, encoded.lower())

        self.assertEqual(manager.status_for(CHALLENGE_ID)["authority"], "none")
        self.assertEqual(manager.health_snapshot()["fallback_count"], 0)

    async def test_distinct_plan_tags_share_the_same_inbox_route(self):
        rest = CapturingRestClient()
        manager = await self._manager(rest)
        self._enqueue(manager, PLAN_ID, CHALLENGE_ID)
        self._enqueue(
            manager,
            SECOND_PLAN_ID,
            "synthetic-rc1-second-opaque-challenge",
        )

        await manager.process_next()
        await manager.process_next()

        self.assertEqual(len(rest.calls), 2)
        first = rest.calls[0][2]["data"]
        second = rest.calls[1][2]["data"]
        self.assertNotEqual(first["tag"], second["tag"])
        self.assertEqual(first["url"], f"/app/{SELF_SLUG}")
        self.assertEqual(second["url"], f"/app/{SELF_SLUG}")
        self.assertNotIn(PLAN_ID, json.dumps(first, sort_keys=True))
        self.assertNotIn(SECOND_PLAN_ID, json.dumps(second, sort_keys=True))

    async def test_notify_failure_matrix_preserves_governance_authority(self):
        cases = (
            (
                "timeout",
                HomeAssistantTimeoutError(),
                "provider_timeout",
                False,
            ),
            (
                "unavailable",
                HomeAssistantUnavailableError(),
                "provider_unavailable",
                False,
            ),
            (
                "unauthenticated",
                HomeAssistantApiError(details={"status": 401}),
                "authentication_failure",
                True,
            ),
            (
                "forbidden",
                HomeAssistantApiError(details={"status": 403}),
                "authentication_failure",
                True,
            ),
            (
                "provider_rejected",
                HomeAssistantApiError(details={"status": 500}),
                "provider_rejected",
                True,
            ),
            (
                "unexpected_exception",
                ValueError("synthetic malformed response"),
                "internal_error",
                False,
            ),
            (
                "malformed_success_body",
                "synthetic non-json success body",
                None,
                True,
            ),
        )
        for name, outcome, expected_failure, response_received in cases:
            with self.subTest(name=name):
                temp = tempfile.TemporaryDirectory()
                self.addCleanup(temp.cleanup)
                root = Path(temp.name)
                audit_path = root / "audit.jsonl"
                audit = AuditLogger(
                    str(audit_path), "synthetic-rc1-matrix-audit-secret"
                )
                rest = ScriptedRestClient(outcome)

                async def identity():
                    return type("Identity", (), {"slug": SELF_SLUG})()

                notifications = ApprovalNotificationManager(
                    rest,
                    audit,
                    service="notify.mobile_app_synthetic_phone",
                    timeout_seconds=2,
                    addon_identity_resolver=identity,
                )
                repository = ChangePlanRepository(root / "plans")
                gateway = FakeOperationGateway()
                service = ChangeGovernanceService(
                    repository,
                    gateway,
                    audit,
                    approval_notifications=notifications,
                )
                _telemetry, context = begin_request(
                    f"synthetic-rc1-{name}"
                )
                try:
                    proposed = copy.deepcopy(CURRENT)
                    proposed["description"] = "After"
                    created = await service.create_plan(
                        title="Synthetic RC1 notification matrix",
                        description="Advisory refusal preservation",
                        operation="update_automation",
                        automation_id="fixture",
                        proposed_config=proposed,
                    )
                    pending = service.approve(
                        created["plan_id"], created["plan_hash"]
                    )
                    authority_before = repository.get(
                        created["plan_id"]
                    ).to_dict()
                    await notifications.process_next()
                finally:
                    end_request(context)

                persisted = repository.get(created["plan_id"])
                self.assertEqual(persisted.to_dict(), authority_before)
                self.assertEqual(
                    persisted.approval.state, ApprovalState.EXTERNAL_PENDING
                )
                self.assertEqual(
                    persisted.approval.challenge_id,
                    pending["challenge_id"],
                )
                self.assertEqual(gateway.writes, 0)
                self.assertEqual(len(rest.calls), 1)

                health = notifications.health_snapshot()
                status = notifications.status_for(pending["challenge_id"])
                self.assertEqual(health["fallback_count"], 0)
                self.assertEqual(health["delivered"], 0)
                self.assertEqual(status["authority"], "none")
                self.assertFalse(status["approval_performed"])
                records = [
                    json.loads(line)
                    for line in audit_path.read_text(
                        encoding="utf-8"
                    ).splitlines()
                ]
                terminal = next(
                    record
                    for record in records
                    if record["event"]
                    in {
                        "approval_notification_notify_failed",
                        "approval_notification_notify_submitted",
                    }
                )
                self.assertFalse(terminal["approval_authority_changed"])
                self.assertEqual(terminal["fallback"], "none")
                self.assertEqual(
                    terminal["provider_response_received"],
                    response_received,
                )
                if expected_failure is None:
                    self.assertEqual(health["submitted"], 1)
                    self.assertEqual(health["failed"], 0)
                    self.assertIsNone(health["last_failure_category"])
                    self.assertEqual(status["status"], "submitted")
                    self.assertEqual(terminal["result_status"], "success")
                else:
                    self.assertEqual(health["submitted"], 0)
                    self.assertEqual(health["failed"], 1)
                    self.assertEqual(
                        health["last_failure_category"], expected_failure
                    )
                    self.assertEqual(status["status"], "notify_failed")
                    self.assertEqual(
                        terminal["failure_category"], expected_failure
                    )

    async def test_supervisor_identity_refusal_matrix_prevents_notify(self):
        async def response(payload: bytes):
            return 200, payload

        valid_fields = (
            '"name":"HA MCP Engineering Server",'
            '"version":"2.2.0-rc.1","repository":"df26dea6"'
        )
        cases = (
            ("missing_resolver", None, "configuration_unavailable"),
            (
                "missing_slug",
                SupervisorSelfAddonIdentityResolver(
                    base_url="http://supervisor",
                    token="synthetic-token",
                    timeout_seconds=2,
                    fetcher=lambda: response(
                        (
                            '{"result":"ok","data":{'
                            f"{valid_fields}}}}}"
                        ).encode("utf-8")
                    ),
                ).resolve,
                "malformed_response",
            ),
            (
                "invalid_slug",
                SupervisorSelfAddonIdentityResolver(
                    base_url="http://supervisor",
                    token="synthetic-token",
                    timeout_seconds=2,
                    fetcher=lambda: response(
                        (
                            '{"result":"ok","data":{'
                            '"slug":"../unsafe",'
                            f"{valid_fields}}}}}"
                        ).encode("utf-8")
                    ),
                ).resolve,
                "malformed_response",
            ),
            (
                "conflicting_slug_members",
                SupervisorSelfAddonIdentityResolver(
                    base_url="http://supervisor",
                    token="synthetic-token",
                    timeout_seconds=2,
                    fetcher=lambda: response(
                        (
                            '{"result":"ok","data":{'
                            f'"slug":"{SELF_SLUG}",'
                            '"slug":"other_safe_slug",'
                            f"{valid_fields}}}}}"
                        ).encode("utf-8")
                    ),
                ).resolve,
                "malformed_response",
            ),
            (
                "malformed_payload",
                SupervisorSelfAddonIdentityResolver(
                    base_url="http://supervisor",
                    token="synthetic-token",
                    timeout_seconds=2,
                    fetcher=lambda: response(b"not-json"),
                ).resolve,
                "malformed_response",
            ),
        )
        for name, resolver, expected_failure in cases:
            with self.subTest(name=name):
                rest = CapturingRestClient()
                temp = tempfile.TemporaryDirectory()
                self.addCleanup(temp.cleanup)
                audit_path = Path(temp.name) / "audit.jsonl"
                manager = ApprovalNotificationManager(
                    rest,
                    AuditLogger(
                        str(audit_path),
                        "synthetic-rc1-identity-audit-secret",
                    ),
                    service="notify.mobile_app_synthetic_phone",
                    timeout_seconds=2,
                    addon_identity_resolver=resolver,
                )
                self._enqueue(manager, PLAN_ID, f"{CHALLENGE_ID}-{name}")
                await manager.process_next()

                self.assertEqual(rest.calls, [])
                health = manager.health_snapshot()
                self.assertEqual(health["submitted"], 0)
                self.assertEqual(health["failed"], 1)
                self.assertEqual(
                    health["last_failure_category"], expected_failure
                )
                self.assertEqual(health["fallback_count"], 0)
                failure = next(
                    json.loads(line)
                    for line in audit_path.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if "approval_notification_notify_failed" in line
                )
                self.assertFalse(failure["provider_dispatch_occurred"])
                self.assertFalse(failure["provider_response_received"])
                self.assertFalse(failure["approval_authority_changed"])
                self.assertEqual(failure["fallback"], "none")


if __name__ == "__main__":
    unittest.main()
