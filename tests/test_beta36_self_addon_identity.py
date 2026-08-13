"""Beta 36 fragmented Supervisor self-info response regression."""

from __future__ import annotations

from collections import deque
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

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


def resolver_with_chunks(
    chunks: list[bytes], *, status: int = 200
) -> tuple[SupervisorSelfAddonIdentityResolver, FragmentedContent]:
    content = FragmentedContent(chunks)
    response = FakeResponse(status, content)
    resolver = SupervisorSelfAddonIdentityResolver(
        base_url="http://supervisor",
        token="synthetic-supervisor-token",
        timeout_seconds=5,
    )
    resolver._test_session = FakeSession(response)  # type: ignore[attr-defined]
    return resolver, content


class Beta36FragmentedSelfInfoTests(unittest.IsolatedAsyncioTestCase):
    async def test_fragmented_live_shaped_response_reads_to_eof(self):
        body = sanitized_live_shaped_payload()
        chunks = [body[:1024], body[1024:16_384], body[16_384:]]
        resolver, content = resolver_with_chunks(chunks)

        with patch.object(
            supervisor_self.aiohttp,
            "ClientSession",
            return_value=resolver._test_session,  # type: ignore[attr-defined]
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
        resolver, content = resolver_with_chunks(chunks)

        with patch.object(
            supervisor_self.aiohttp,
            "ClientSession",
            return_value=resolver._test_session,  # type: ignore[attr-defined]
        ):
            with self.assertRaises(SelfAddonIdentityError) as raised:
                await resolver.resolve()

        self.assertEqual(raised.exception.failure_category, "response_too_large")
        self.assertEqual(
            sum(content.returned_sizes), MAX_SELF_INFO_BYTES + 1
        )


if __name__ == "__main__":
    unittest.main()
