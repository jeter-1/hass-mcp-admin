"""Beta 35 approval-notification mobile navigation contract."""

from __future__ import annotations

import hashlib
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
from scripts import fake_ha_read_gateway_contract_server as exact_fixture  # noqa: E402


SELF_SLUG = "df26dea6_hass_mcp_engineering_beta"
PLAN_ID = "a" * 32
SECOND_PLAN_ID = "b" * 32
CHALLENGE_ID = "synthetic-beta35-opaque-challenge"


class CapturingRestClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    async def request(self, method: str, path: str, body=None):
        self.calls.append((method, path, body))
        return {"context": {"id": "synthetic-beta35-submission"}}


class FixtureRequest:
    def __init__(self, body: dict) -> None:
        self.headers = {"Authorization": f"Bearer {exact_fixture.TOKEN}"}
        self.body = body

    async def json(self):
        return self.body


class Beta35MobileNavigationTests(unittest.IsolatedAsyncioTestCase):
    def test_acceptance_orders_release_gates_before_live_testing(self):
        acceptance = (
            ROOT / "docs" / "V2_2_0_BETA35_ACCEPTANCE.md"
        ).read_text(encoding="utf-8")
        normalized = " ".join(acceptance.split())
        pre_deployment = acceptance.index("## Pre-deployment gates")
        post_deployment = acceptance.index(
            "## Post-deployment entry criteria"
        )
        live_acceptance = acceptance.index(
            "## A — Mobile navigation matrices"
        )

        self.assertLess(pre_deployment, post_deployment)
        self.assertLess(post_deployment, live_acceptance)
        self.assertIn(
            "before a separately authorized deployment", normalized
        )
        self.assertIn(
            "Sections A through C apply only after that deployment",
            normalized,
        )
        pre_deployment_text = acceptance[
            pre_deployment:post_deployment
        ]
        self.assertIn("deployment-candidate image", pre_deployment_text)
        self.assertNotIn("deployed image", pre_deployment_text)

    async def _manager(self, rest: CapturingRestClient):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        audit_path = Path(self.temp.name) / "audit.jsonl"

        async def identity():
            return type("Identity", (), {"slug": SELF_SLUG})()

        return (
            ApprovalNotificationManager(
                rest,
                AuditLogger(
                    str(audit_path), "synthetic-beta35-audit-secret"
                ),
                service="notify.mobile_app_synthetic_pixel",
                timeout_seconds=2,
                addon_identity_resolver=identity,
            ),
            audit_path,
        )

    @staticmethod
    def _enqueue(manager, plan_id: str, challenge_id: str) -> None:
        manager._enqueue(
            "notify",
            plan_id,
            challenge_id,
            "plan_approval",
            "synthetic-beta35-request",
        )

    async def test_body_tap_and_action_derive_from_one_exact_plan_target(self):
        rest = CapturingRestClient()
        manager, _ = await self._manager(rest)
        self._enqueue(manager, PLAN_ID, CHALLENGE_ID)

        await manager.process_next()

        self.assertEqual(len(rest.calls), 1)
        body = rest.calls[0][2]
        review_path = f"/hassio/ingress/{SELF_SLUG}/plans/{PLAN_ID}"
        navigation_uri = f"homeassistant://navigate{review_path}"
        android_navigation_uri = f"deep-link://{navigation_uri}"
        self.assertEqual(body["data"]["url"], navigation_uri)
        self.assertEqual(
            body["data"]["clickAction"], android_navigation_uri
        )
        self.assertEqual(
            body["data"]["actions"],
            [
                {
                    "action": "URI",
                    "title": "Open Approval Panel",
                    "uri": review_path,
                }
            ],
        )
        self.assertEqual(
            body["data"]["url"].removeprefix(
                "homeassistant://navigate"
            ),
            review_path,
        )
        self.assertEqual(
            body["data"]["clickAction"].removeprefix(
                "deep-link://homeassistant://navigate"
            ),
            review_path,
        )
        self.assertEqual(
            body["data"]["clickAction"].count("deep-link://"), 1
        )
        self.assertEqual(
            body["data"]["clickAction"].count(
                "homeassistant://navigate"
            ),
            1,
        )

        encoded = json.dumps(body, sort_keys=True)
        self.assertIn(PLAN_ID, encoded)
        self.assertNotIn(CHALLENGE_ID, encoded)
        for forbidden in (
            "approval_token",
            "challenge_id",
            "csrf",
            "plan_hash",
            "nonce",
            "proposed_config",
            "approve",
            "reject",
        ):
            self.assertNotIn(forbidden, encoded.lower())

    async def test_action_button_uses_cross_platform_ingress_target(self):
        """The shared action must not reuse Android's body-only wrapper."""

        rest = CapturingRestClient()
        manager, _ = await self._manager(rest)
        self._enqueue(manager, PLAN_ID, CHALLENGE_ID)

        await manager.process_next()

        body = rest.calls[0][2]
        review_path = f"/hassio/ingress/{SELF_SLUG}/plans/{PLAN_ID}"
        self.assertEqual(
            body["data"]["actions"],
            [
                {
                    "action": "URI",
                    "title": "Open Approval Panel",
                    "uri": review_path,
                }
            ],
        )
        self.assertNotIn(
            "deep-link://", body["data"]["actions"][0]["uri"]
        )

    async def test_ios_body_and_action_resolve_the_exact_plan_route(self):
        rest = CapturingRestClient()
        manager, _ = await self._manager(rest)
        self._enqueue(manager, PLAN_ID, CHALLENGE_ID)

        await manager.process_next()

        data = rest.calls[0][2]["data"]
        review_path = f"/hassio/ingress/{SELF_SLUG}/plans/{PLAN_ID}"
        self.assertEqual(
            data["url"], f"homeassistant://navigate{review_path}"
        )
        self.assertEqual(
            data["url"].removeprefix("homeassistant://navigate"),
            review_path,
        )
        self.assertEqual(data["actions"][0]["uri"], review_path)
        self.assertNotIn("deep-link://", data["actions"][0]["uri"])

    async def test_android_body_and_action_resolve_the_exact_plan_route(self):
        rest = CapturingRestClient()
        manager, _ = await self._manager(rest)
        self._enqueue(manager, PLAN_ID, CHALLENGE_ID)

        await manager.process_next()

        data = rest.calls[0][2]["data"]
        review_path = f"/hassio/ingress/{SELF_SLUG}/plans/{PLAN_ID}"
        self.assertEqual(
            data["clickAction"],
            f"deep-link://homeassistant://navigate{review_path}",
        )
        self.assertEqual(
            data["clickAction"].removeprefix(
                "deep-link://homeassistant://navigate"
            ),
            review_path,
        )
        self.assertEqual(data["actions"][0]["uri"], review_path)
        self.assertNotIn("deep-link://", data["actions"][0]["uri"])

    async def test_each_notification_keeps_its_own_plan_navigation_target(self):
        rest = CapturingRestClient()
        manager, _ = await self._manager(rest)
        self._enqueue(manager, PLAN_ID, CHALLENGE_ID)
        self._enqueue(
            manager,
            SECOND_PLAN_ID,
            "synthetic-beta35-second-opaque-challenge",
        )

        await manager.process_next()
        await manager.process_next()

        self.assertEqual(len(rest.calls), 2)
        targets = [call[2]["data"]["clickAction"] for call in rest.calls]
        self.assertEqual(
            targets,
            [
                "deep-link://homeassistant://navigate/hassio/ingress/"
                f"{SELF_SLUG}/plans/{PLAN_ID}",
                "deep-link://homeassistant://navigate/hassio/ingress/"
                f"{SELF_SLUG}/plans/{SECOND_PLAN_ID}",
            ],
        )
        action_targets = [
            call[2]["data"]["actions"][0]["uri"] for call in rest.calls
        ]
        self.assertEqual(
            action_targets,
            [
                f"/hassio/ingress/{SELF_SLUG}/plans/{PLAN_ID}",
                f"/hassio/ingress/{SELF_SLUG}/plans/{SECOND_PLAN_ID}",
            ],
        )

    async def test_documented_navigation_forms_match_runtime_contract(self):
        rest = CapturingRestClient()
        manager, _ = await self._manager(rest)
        self._enqueue(manager, PLAN_ID, CHALLENGE_ID)

        await manager.process_next()

        data = rest.calls[0][2]["data"]

        def documented(value: str) -> str:
            return value.replace(
                SELF_SLUG, "{verified_addon_slug}"
            ).replace(PLAN_ID, "{plan_id}")

        external_approval = (
            ROOT / "docs" / "EXTERNAL_APPROVAL.md"
        ).read_text(encoding="utf-8")
        mobile_navigation = (
            ROOT / "docs" / "BETA35_MOBILE_APPROVAL_NAVIGATION.md"
        ).read_text(encoding="utf-8")
        combined = f"{external_approval}\n{mobile_navigation}"
        self.assertIn(documented(data["url"]), combined)
        self.assertIn(documented(data["clickAction"]), combined)
        self.assertIn(documented(data["actions"][0]["uri"]), combined)
        self.assertNotIn(
            "Android `clickAction`, iOS `url`, and URI action target",
            external_approval,
        )
        acceptance = (
            ROOT / "docs" / "V2_2_0_BETA35_ACCEPTANCE.md"
        ).read_text(encoding="utf-8")
        self.assertIn("### Android", acceptance)
        self.assertIn("### iOS", acceptance)
        self.assertIn(
            "iOS must remain explicitly unaccepted",
            " ".join(acceptance.split()),
        )

    async def test_malformed_plan_identity_fails_before_provider_dispatch(self):
        rest = CapturingRestClient()
        manager, audit_path = await self._manager(rest)
        self._enqueue(manager, "../not-a-plan", CHALLENGE_ID)

        await manager.process_next()

        self.assertEqual(rest.calls, [])
        health = manager.health_snapshot()
        self.assertEqual(health["submitted"], 0)
        self.assertEqual(health["delivered"], 0)
        self.assertEqual(health["failed"], 1)
        self.assertEqual(
            health["last_failure_category"], "invalid_navigation_target"
        )
        record = next(
            json.loads(line)
            for line in audit_path.read_text(encoding="utf-8").splitlines()
            if "approval_notification_notify_failed" in line
        )
        self.assertFalse(record["provider_dispatch_occurred"])
        self.assertFalse(record["provider_response_received"])
        self.assertFalse(record["approval_authority_changed"])
        self.assertEqual(record["fallback"], "none")

    async def test_uppercase_plan_identity_fails_before_provider_dispatch(self):
        rest = CapturingRestClient()
        manager, _ = await self._manager(rest)
        self._enqueue(manager, "A" * 32, CHALLENGE_ID)

        await manager.process_next()

        self.assertEqual(rest.calls, [])
        self.assertEqual(
            manager.health_snapshot()["last_failure_category"],
            "invalid_navigation_target",
        )

    async def test_malformed_addon_slug_remains_fail_closed(self):
        rest = CapturingRestClient()
        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"

            async def invalid_identity():
                return type("Identity", (), {"slug": "../unsafe"})()

            manager = ApprovalNotificationManager(
                rest,
                AuditLogger(
                    str(audit_path), "synthetic-beta35-audit-secret"
                ),
                service="notify.mobile_app_synthetic_pixel",
                timeout_seconds=2,
                addon_identity_resolver=invalid_identity,
            )
            self._enqueue(manager, PLAN_ID, CHALLENGE_ID)
            await manager.process_next()

        self.assertEqual(rest.calls, [])
        self.assertEqual(manager.health_snapshot()["failed"], 1)
        self.assertEqual(
            manager.health_snapshot()["last_failure_category"],
            "malformed_response",
        )

    async def test_clear_keeps_only_the_bounded_notification_correlation(self):
        rest = CapturingRestClient()
        manager, _ = await self._manager(rest)
        self._enqueue(manager, PLAN_ID, CHALLENGE_ID)
        queued = manager.queue.get_nowait()
        manager.queue.task_done()
        manager._scheduled.clear()
        manager._enqueue(
            "clear",
            PLAN_ID,
            CHALLENGE_ID,
            "plan_approval",
            "synthetic-beta35-clear-request",
        )

        await manager.process_next()

        body = rest.calls[0][2]
        self.assertEqual(
            body,
            {
                "message": "clear_notification",
                "data": {"tag": queued.notification_key},
            },
        )
        health = manager.health_snapshot()
        self.assertEqual(health["clear_submitted"], 1)
        self.assertEqual(health["cleared"], 0)
        self.assertFalse(health["handset_clear_observable"])

    async def test_exact_image_fixture_records_bounded_platform_hashes(self):
        review_path = f"/hassio/ingress/{SELF_SLUG}/plans/{PLAN_ID}"
        ios_target = f"homeassistant://navigate{review_path}"
        android_target = f"deep-link://{ios_target}"
        body = {
            "title": "Home Assistant approval requested",
            "message": (
                "A governed Home Assistant change is waiting for "
                "administrator review."
            ),
            "data": {
                "tag": "ha_mcp_approval_synthetic",
                "url": ios_target,
                "clickAction": android_target,
                "actions": [
                    {
                        "action": "URI",
                        "title": "Open Approval Panel",
                        "uri": review_path,
                    }
                ],
            },
        }
        exact_fixture.STATE.approval_notification_calls.clear()

        response = await exact_fixture.approval_notification(
            FixtureRequest(body)
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(
            len(exact_fixture.STATE.approval_notification_calls), 1
        )
        recorded = exact_fixture.STATE.approval_notification_calls[0]
        self.assertEqual(
            recorded["ingress_path_sha256"],
            hashlib.sha256(review_path.encode()).hexdigest(),
        )
        self.assertEqual(
            recorded["ios_url_sha256"],
            hashlib.sha256(ios_target.encode()).hexdigest(),
        )
        self.assertEqual(
            recorded["android_click_action_sha256"],
            hashlib.sha256(android_target.encode()).hexdigest(),
        )
        self.assertEqual(
            recorded["action_uri_sha256"],
            recorded["ingress_path_sha256"],
        )
        self.assertTrue(
            recorded["action_uri_matches_cross_platform_target"]
        )
        self.assertEqual(recorded["action"], "URI")
        self.assertEqual(
            recorded["title_sha256"],
            hashlib.sha256(body["title"].encode()).hexdigest(),
        )
        self.assertEqual(
            recorded["message_sha256"],
            hashlib.sha256(body["message"].encode()).hexdigest(),
        )
        self.assertEqual(
            recorded["action_title_sha256"],
            hashlib.sha256(
                body["data"]["actions"][0]["title"].encode()
            ).hexdigest(),
        )
        self.assertFalse(recorded["authority_material_present"])
        self.assertFalse(recorded["authentication_required_present"])
        self.assertNotIn(PLAN_ID, json.dumps(recorded, sort_keys=True))

    async def test_exact_image_fixture_rejects_drift_and_authority_fields(self):
        review_path = f"/hassio/ingress/{SELF_SLUG}/plans/{PLAN_ID}"
        ios_target = f"homeassistant://navigate{review_path}"
        android_target = f"deep-link://{ios_target}"
        base = {
            "title": "Home Assistant approval requested",
            "message": (
                "A governed Home Assistant change is waiting for "
                "administrator review."
            ),
            "data": {
                "tag": "ha_mcp_approval_synthetic",
                "url": ios_target,
                "clickAction": android_target,
                "actions": [
                    {
                        "action": "URI",
                        "title": "Open Approval Panel",
                        "uri": review_path,
                    }
                ],
            },
        }
        invalid_bodies = []
        for mutation in (
            "relative_body",
            "android_action",
            "ios_action",
            "authority_title",
            "authority_message",
            "authority_data",
            "authority_action_title",
            "authority_action_uri",
            "authority_action_verb",
            "authentication_required",
        ):
            candidate = json.loads(json.dumps(base))
            if mutation == "relative_body":
                candidate["data"]["url"] = review_path
                relative_android_target = f"deep-link://{review_path}"
                candidate["data"]["clickAction"] = relative_android_target
            elif mutation == "android_action":
                candidate["data"]["actions"][0]["uri"] = android_target
            elif mutation == "ios_action":
                candidate["data"]["actions"][0]["uri"] = ios_target
            elif mutation == "authority_title":
                candidate["title"] = "Home Assistant plan_hash requested"
            elif mutation == "authority_message":
                candidate["message"] = "Open /approve directly"
            elif mutation == "authority_data":
                candidate["data"]["approval_token"] = (
                    "synthetic-forbidden"
                )
            elif mutation == "authority_action_title":
                candidate["data"]["actions"][0]["title"] = "Reject plan"
            elif mutation == "authority_action_uri":
                candidate["data"]["actions"][0]["uri"] = (
                    f"{review_path}/approve"
                )
            elif mutation == "authority_action_verb":
                candidate["data"]["actions"][0]["action"] = "APPROVE"
            else:
                candidate["data"]["actions"][0][
                    "authenticationRequired"
                ] = True
            invalid_bodies.append(candidate)

        exact_fixture.STATE.approval_notification_calls.clear()
        for candidate in invalid_bodies:
            response = await exact_fixture.approval_notification(
                FixtureRequest(candidate)
            )
            self.assertEqual(response.status, 400)
        self.assertEqual(exact_fixture.STATE.approval_notification_calls, [])


if __name__ == "__main__":
    unittest.main()
