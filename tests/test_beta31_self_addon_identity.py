"""Beta 31 bounded Supervisor self-identity correction."""

from __future__ import annotations

import asyncio
import io
import json
import logging
from pathlib import Path
import sys
import tempfile
import unittest

import aiohttp


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.governance.approval_notifications import (  # noqa: E402
    ApprovalNotificationManager,
)
from ha_mcp_engineering.providers.supervisor_self import (  # noqa: E402
    MAX_SELF_INFO_BYTES,
    SELF_IDENTITY_FAILURE_CATEGORIES,
    SelfAddonIdentityError,
    SupervisorSelfAddonIdentityResolver,
)


BETA31_VERSION = "2.2.0-beta.31"
SELF_SLUG = "df26dea6_hass_mcp_engineering_beta"
SENSITIVE_MARKERS = (
    "synthetic-supervisor-option-secret",
    "synthetic-private-translation",
    "synthetic-long-description-marker",
)


def realistic_self_info_payload(*, padding_bytes: int = 48_000) -> bytes:
    payload = {
        "result": "ok",
        "data": {
            "slug": SELF_SLUG,
            "name": "HA MCP Engineering Server Beta",
            "version": BETA31_VERSION,
            "repository": "df26dea6",
            "long_description": (
                SENSITIVE_MARKERS[2] + ("x" * padding_bytes)
            ),
            "options": {
                "access_secret": SENSITIVE_MARKERS[0],
                "approval_notification_service": (
                    "notify.mobile_app_synthetic_fixture"
                ),
            },
            "schema": {
                "access_secret": "password",
                "approval_notification_service": "str",
            },
            "translations": {
                "en": {"configuration": SENSITIVE_MARKERS[1]}
            },
        },
    }
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    assert len(encoded) > 32 * 1024
    assert len(encoded) < MAX_SELF_INFO_BYTES
    return encoded


class FakeRestClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    async def request(self, method: str, path: str, body=None):
        self.calls.append((method, path, body))
        return {"context": {"id": "synthetic-beta31-notification"}}


class SupervisorSelfIdentityCorrectionTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_realistic_large_response_resolves_only_identity_fields(self):
        payload = realistic_self_info_payload()

        async def fetch():
            return 200, payload

        identity = await SupervisorSelfAddonIdentityResolver(
            base_url="http://supervisor",
            token="synthetic-supervisor-token",
            timeout_seconds=5,
            fetcher=fetch,
        ).resolve()

        self.assertEqual(identity.slug, SELF_SLUG)
        self.assertEqual(
            set(identity.as_dict()),
            {
                "slug",
                "name",
                "version",
                "repository",
                "identity_source",
                "authoritative",
            },
        )
        encoded_identity = json.dumps(identity.as_dict(), sort_keys=True)
        for marker in SENSITIVE_MARKERS:
            self.assertNotIn(marker, encoded_identity)

    async def test_failure_categories_are_safe_exact_and_fail_closed(self):
        async def oversized():
            return 200, b"x" * (MAX_SELF_INFO_BYTES + 1)

        async def http_error():
            return 503, SENSITIVE_MARKERS[0].encode()

        async def malformed():
            return 200, json.dumps(
                {
                    "result": "not-ok",
                    "options": {"secret": SENSITIVE_MARKERS[0]},
                }
            ).encode()

        async def timeout():
            raise asyncio.TimeoutError(SENSITIVE_MARKERS[0])

        async def transport():
            raise aiohttp.ClientConnectionError(SENSITIVE_MARKERS[0])

        cases = {
            "response_too_large": oversized,
            "http_status": http_error,
            "malformed_response": malformed,
            "timeout": timeout,
            "transport_failure": transport,
        }
        self.assertTrue(set(cases) < SELF_IDENTITY_FAILURE_CATEGORIES)
        for expected, fetcher in cases.items():
            with self.subTest(expected=expected):
                resolver = SupervisorSelfAddonIdentityResolver(
                    base_url="http://supervisor",
                    token="synthetic-supervisor-token",
                    timeout_seconds=5,
                    fetcher=fetcher,
                )
                with self.assertRaises(SelfAddonIdentityError) as raised:
                    await resolver.resolve()
                self.assertEqual(
                    raised.exception.failure_category, expected
                )
                self.assertIsNone(raised.exception.__context__)
                self.assertIsNone(raised.exception.__cause__)
                rendered = str(raised.exception)
                for marker in SENSITIVE_MARKERS:
                    self.assertNotIn(marker, rendered)

    async def test_missing_authority_inputs_never_guess_identity(self):
        for base_url, token in (
            ("", "synthetic-supervisor-token"),
            ("http://supervisor", ""),
        ):
            with self.subTest(base_url=base_url, token_present=bool(token)):
                with self.assertRaises(SelfAddonIdentityError) as raised:
                    await SupervisorSelfAddonIdentityResolver(
                        base_url=base_url,
                        token=token,
                        timeout_seconds=5,
                    ).resolve()
                self.assertEqual(
                    raised.exception.failure_category,
                    "configuration_unavailable",
                )

    async def test_large_response_delivers_advisory_notification_without_leakage(self):
        payload = realistic_self_info_payload()

        async def fetch():
            return 200, payload

        resolver = SupervisorSelfAddonIdentityResolver(
            base_url="http://supervisor",
            token="synthetic-supervisor-token",
            timeout_seconds=5,
            fetcher=fetch,
        )
        rest = FakeRestClient()
        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            audit = AuditLogger(
                str(audit_path), "synthetic-beta31-audit-secret"
            )
            manager = ApprovalNotificationManager(
                rest,
                audit,
                service="notify.mobile_app_synthetic_fixture",
                timeout_seconds=5,
                addon_identity_resolver=resolver.resolve,
            )
            manager._enqueue(
                "notify",
                "a" * 32,
                "synthetic-opaque-challenge",
                "plan_approval",
                "beta31-large-identity-request",
            )
            log_output = io.StringIO()
            handler = logging.StreamHandler(log_output)
            manager.logger.addHandler(handler)
            try:
                await manager.process_next()
            finally:
                manager.logger.removeHandler(handler)

            health = manager.health_snapshot()
            self.assertEqual(health["submitted"], 1)
            self.assertEqual(health["delivered"], 0)
            self.assertFalse(health["handset_delivery_observable"])
            self.assertEqual(health["failed"], 0)
            self.assertEqual(
                health["addon_identity_status"],
                "verified_supervisor_self_info",
            )
            self.assertIsNone(
                health["addon_identity_failure_category"]
            )
            self.assertEqual(health["fallback"], "none")
            self.assertEqual(len(rest.calls), 1)
            method, path, body = rest.calls[0]
            self.assertEqual(method, "POST")
            self.assertEqual(
                path,
                "/services/notify/mobile_app_synthetic_fixture",
            )
            self.assertEqual(
                body["data"]["url"],
                f"/hassio/ingress/{SELF_SLUG}/plans/{'a' * 32}",
            )
            self.assertEqual(body["data"]["actions"][0]["action"], "URI")

            persisted = audit_path.read_text(encoding="utf-8")
            exposed = "\n".join(
                (
                    persisted,
                    log_output.getvalue(),
                    json.dumps(health, sort_keys=True),
                )
            )
            for marker in SENSITIVE_MARKERS:
                self.assertNotIn(marker, exposed)
            self.assertNotIn("access_secret", exposed)
            self.assertNotIn("translations", exposed)

    async def test_identity_failure_prevents_notification_and_exposes_only_category(self):
        async def oversized():
            return 200, b"x" * (MAX_SELF_INFO_BYTES + 1)

        resolver = SupervisorSelfAddonIdentityResolver(
            base_url="http://supervisor",
            token="synthetic-supervisor-token",
            timeout_seconds=5,
            fetcher=oversized,
        )
        rest = FakeRestClient()
        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            manager = ApprovalNotificationManager(
                rest,
                AuditLogger(
                    str(audit_path), "synthetic-beta31-audit-secret"
                ),
                service="notify.mobile_app_synthetic_fixture",
                timeout_seconds=5,
                addon_identity_resolver=resolver.resolve,
            )
            manager._enqueue(
                "notify",
                "b" * 32,
                "synthetic-opaque-challenge",
                "plan_approval",
                None,
            )
            await manager.process_next()
            audit = [
                json.loads(line)
                for line in audit_path.read_text().splitlines()
            ]

        self.assertEqual(rest.calls, [])
        health = manager.health_snapshot()
        self.assertEqual(health["failed"], 1)
        self.assertEqual(
            health["last_failure_category"], "response_too_large"
        )
        self.assertEqual(
            health["addon_identity_failure_category"],
            "response_too_large",
        )
        self.assertEqual(health["fallback_count"], 0)
        failure = next(
            record
            for record in audit
            if record["event"] == "approval_notification_notify_failed"
        )
        self.assertEqual(
            failure["failure_category"], "response_too_large"
        )
        self.assertFalse(failure["provider_dispatch_occurred"])
        self.assertFalse(failure["approval_authority_changed"])


if __name__ == "__main__":
    unittest.main()
