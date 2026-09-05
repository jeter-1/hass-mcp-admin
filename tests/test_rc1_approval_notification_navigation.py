"""RC1 approval-notification app-panel navigation regressions."""

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
from ha_mcp_engineering.governance.approval_notifications import (  # noqa: E402
    ApprovalNotificationManager,
)


SELF_SLUG = "df26dea6_hass_mcp_engineering_beta"
PLAN_ID = "a" * 32
SECOND_PLAN_ID = "b" * 32
CHALLENGE_ID = "synthetic-rc1-opaque-challenge"


class CapturingRestClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    async def request(self, method: str, path: str, body=None):
        self.calls.append((method, path, body))
        return {"context": {"id": "synthetic-rc1-submission"}}


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


if __name__ == "__main__":
    unittest.main()
