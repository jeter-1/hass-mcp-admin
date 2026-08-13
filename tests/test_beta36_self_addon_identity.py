"""Beta 36 fragmented Supervisor self-info response regression."""

from __future__ import annotations

from collections import deque
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.governance.approval_notifications import (  # noqa: E402
    ApprovalNotificationManager,
)
from ha_mcp_engineering.governance.operational_lifecycle import (  # noqa: E402
    OperationalLifecycleGateway,
)
from ha_mcp_engineering.providers import supervisor_self  # noqa: E402
from ha_mcp_engineering.providers.supervisor_self import (  # noqa: E402
    MAX_SELF_INFO_BYTES,
    SelfAddonIdentityError,
    SupervisorSelfAddonIdentityResolver,
)


SELF_SLUG = "df26dea6_hass_mcp_engineering_beta"


def sanitized_live_shaped_payload(*, padding_bytes: int = 32_700) -> bytes:
    """Mirror the bounded live envelope without production values."""

    encoded = json.dumps(
        {
            "result": "ok",
            "data": {
                "slug": SELF_SLUG,
                "name": "HA MCP Engineering Server Beta",
                "version": "2.2.0-beta.35",
                "repository": "df26dea6",
                "description": "x" * padding_bytes,
                "options": {
                    "access_secret": "synthetic-beta36-option-secret",
                    "approval_notification_service": (
                        "notify.mobile_app_synthetic_beta36"
                    ),
                },
                # Live Supervisor presents the add-on schema as a list. The
                # identity parser correctly treats it as unrelated untrusted
                # metadata and retains only the exact identity fields above.
                "schema": [
                    {"name": "access_secret", "type": "password"},
                    {
                        "name": "approval_notification_service",
                        "type": "str",
                    },
                ],
                "translations": {
                    "en": {"configuration": "synthetic-private-value"}
                },
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    assert 32 * 1024 < len(encoded) < MAX_SELF_INFO_BYTES
    return encoded


class FragmentedContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = deque((*chunks, b""))
        self.read_sizes: list[int] = []
        self.returned_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        chunk = self._chunks.popleft()
        if size >= 0 and len(chunk) > size:
            self._chunks.appendleft(chunk[size:])
            chunk = chunk[:size]
        self.returned_sizes.append(len(chunk))
        return chunk


class FakeResponse:
    def __init__(self, status: int, content: FragmentedContent) -> None:
        self.status = status
        self.content = content

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def get(self, *_args: object, **_kwargs: object) -> FakeResponse:
        return self.response


class CapturingRestClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    async def request(self, method: str, path: str, body=None):
        self.calls.append((method, path, body))
        return {"context": {"id": "synthetic-beta36-submission"}}


def resolver_with_chunks(
    chunks: list[bytes], *, status: int = 200
) -> tuple[
    SupervisorSelfAddonIdentityResolver,
    FragmentedContent,
    FakeSession,
]:
    content = FragmentedContent(chunks)
    response = FakeResponse(status, content)
    resolver = SupervisorSelfAddonIdentityResolver(
        base_url="http://supervisor",
        token="synthetic-supervisor-token",
        timeout_seconds=5,
    )
    return resolver, content, FakeSession(response)


class Beta36FragmentedSelfInfoTests(unittest.IsolatedAsyncioTestCase):
    async def test_fragmented_live_shaped_response_reads_to_eof(self):
        body = sanitized_live_shaped_payload()
        chunks = [body[:1024], body[1024:16_384], body[16_384:]]
        resolver, content, session = resolver_with_chunks(chunks)

        with patch.object(
            supervisor_self.aiohttp,
            "ClientSession",
            return_value=session,
        ):
            identity = await resolver.resolve()

        self.assertEqual(identity.slug, SELF_SLUG)
        self.assertGreaterEqual(len(content.read_sizes), 4)
        self.assertTrue(
            all(
                1 <= size <= MAX_SELF_INFO_BYTES + 1
                for size in content.read_sizes
            )
        )

    async def test_fragmented_response_above_bound_stays_response_too_large(self):
        body = b"x" * (MAX_SELF_INFO_BYTES + 1)
        chunks = [
            body[index : index + 4096]
            for index in range(0, len(body), 4096)
        ]
        resolver, content, session = resolver_with_chunks(chunks)

        with patch.object(
            supervisor_self.aiohttp,
            "ClientSession",
            return_value=session,
        ):
            with self.assertRaises(SelfAddonIdentityError) as raised:
                await resolver.resolve()

        self.assertEqual(raised.exception.failure_category, "response_too_large")
        self.assertEqual(
            sum(content.returned_sizes), MAX_SELF_INFO_BYTES + 1
        )

    async def test_fragmented_identity_drives_notification_and_clear(self):
        body = sanitized_live_shaped_payload()
        resolver, _, session = resolver_with_chunks(
            [body[:1024], body[1024:]]
        )
        rest = CapturingRestClient()

        with tempfile.TemporaryDirectory() as directory:
            manager = ApprovalNotificationManager(
                rest,
                AuditLogger(
                    str(Path(directory) / "audit.jsonl"),
                    "synthetic-beta36-audit-secret",
                ),
                service="notify.mobile_app_synthetic_beta36",
                timeout_seconds=5,
                addon_identity_resolver=resolver.resolve,
            )
            with patch.object(
                supervisor_self.aiohttp,
                "ClientSession",
                return_value=session,
            ):
                manager._enqueue(
                    "notify",
                    "a" * 32,
                    "synthetic-beta36-opaque-challenge",
                    "plan_approval",
                    "synthetic-beta36-notify",
                )
                await manager.process_next()
                manager._enqueue(
                    "clear",
                    "a" * 32,
                    "synthetic-beta36-opaque-challenge",
                    "plan_approval",
                    "synthetic-beta36-clear",
                )
                await manager.process_next()

            persisted = (Path(directory) / "audit.jsonl").read_text()

        self.assertEqual(len(rest.calls), 2)
        self.assertEqual(
            rest.calls[0][1],
            "/services/notify/mobile_app_synthetic_beta36",
        )
        self.assertEqual(rest.calls[1][2]["message"], "clear_notification")
        health = manager.health_snapshot()
        self.assertEqual(health["submitted"], 1)
        self.assertEqual(health["clear_submitted"], 1)
        self.assertEqual(
            health["addon_identity_status"],
            "verified_supervisor_self_info",
        )
        self.assertEqual(health["fallback_count"], 0)
        self.assertNotIn("synthetic-beta36-option-secret", persisted)
        self.assertNotIn("synthetic-private-value", persisted)

    async def test_fragmented_identity_drives_self_restart_planning_only(self):
        body = sanitized_live_shaped_payload()
        resolver, _, session = resolver_with_chunks(
            [body[:1024], body[1024:]]
        )

        class AddonProvider:
            async def probe(self, _operation: str):
                return SimpleNamespace(
                    as_dict=lambda: {
                        "provider": "synthetic-reviewed-lifecycle"
                    }
                )

            async def get_addon(self, requested_slug: str):
                return {
                    "slug": requested_slug,
                    "name": "HA MCP Engineering Server Beta",
                    "version": "2.2.0-beta.35",
                    "repository": "df26dea6",
                    "state": "started",
                }

        async def configuration_validator():
            raise AssertionError("restart planning must not validate config")

        gateway = OperationalLifecycleGateway(
            AddonProvider(),  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            configuration_validator=configuration_validator,
            runtime_snapshot=lambda: {"server_version": "2.2.0-beta.35"},
            process_instance_id="synthetic-beta36-process",
            self_addon_identity_resolver=resolver.resolve,
        )

        with patch.object(
            supervisor_self.aiohttp,
            "ClientSession",
            return_value=session,
        ):
            evidence = await gateway.planning_evidence(
                "restart_addon", SELF_SLUG
            )

        target = evidence["baseline"]["target_identity"]
        self.assertEqual(target["resolved_slug"], SELF_SLUG)
        self.assertEqual(target["target_class"], "engineering_addon")
        self.assertTrue(target["authoritative_self_match"])
        self.assertEqual(target["identity_source"], "supervisor_self_info")

    async def test_invalid_live_envelope_identity_fields_fail_closed(self):
        valid_data = {
            "slug": SELF_SLUG,
            "name": "HA MCP Engineering Server Beta",
            "version": "2.2.0-beta.35",
            "repository": "df26dea6",
        }
        cases: dict[str, object] = {
            "outer_list": [],
            "data_not_object": {"result": "ok", "data": []},
            "missing_slug": {
                "result": "ok",
                "data": {
                    key: value
                    for key, value in valid_data.items()
                    if key != "slug"
                },
            },
            "empty_slug": {
                "result": "ok",
                "data": {**valid_data, "slug": ""},
            },
            "wrong_slug_type": {
                "result": "ok",
                "data": {**valid_data, "slug": 42},
            },
            "invalid_slug_syntax": {
                "result": "ok",
                "data": {**valid_data, "slug": "../unsafe"},
            },
        }
        for label, payload in cases.items():
            with self.subTest(label=label):

                async def fetch(value=payload):
                    return 200, json.dumps(value).encode()

                resolver = SupervisorSelfAddonIdentityResolver(
                    base_url="http://supervisor",
                    token="synthetic-supervisor-token",
                    timeout_seconds=5,
                    fetcher=fetch,
                )
                with self.assertRaises(SelfAddonIdentityError) as raised:
                    await resolver.resolve()
                self.assertEqual(
                    raised.exception.failure_category,
                    "malformed_response",
                )


if __name__ == "__main__":
    unittest.main()
