"""RC2dev6 authentication-throttle audit contract regressions."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_beta_observability import SECRET, settings

from ha_mcp_engineering.audit import (
    AUTH_FAILURE_EVENT,
    AUTH_FAILURE_THROTTLED_EVENT,
    RATE_LIMITED_EVENT,
    AuditLogger,
)
from ha_mcp_engineering.observability import METRICS
from ha_mcp_engineering.routing import AuthenticatedMcpGateway
from ha_mcp_engineering.tools import compatibility


class FakeClock:
    def __init__(self) -> None:
        self.value = 1_000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class RecordingMcpApp:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope, receive, send) -> None:
        self.calls += 1
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})


async def asgi_request(
    gateway: AuthenticatedMcpGateway,
    path: str,
    *,
    client: str = "127.0.0.61",
    headers: tuple[tuple[bytes, bytes], ...] = (),
    query_string: bytes = b"",
    body: bytes = b"{}",
) -> tuple[int, bytes, dict[bytes, bytes]]:
    messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    await gateway(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": query_string,
            "headers": list(headers),
            "client": (client, 1),
        },
        receive,
        send,
    )
    start = next(item for item in messages if item["type"] == "http.response.start")
    response = b"".join(
        item.get("body", b"")
        for item in messages
        if item["type"] == "http.response.body"
    )
    return start["status"], response, dict(start.get("headers", []))


def read_audit(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def provider_counters() -> dict:
    routing = METRICS.snapshot()["provider_routing"]
    return {
        "requests": routing["requests_by_provider"],
        "failures": routing["failures_by_provider"],
        "operational_failures": routing["provider_operational_failures"],
        "fallback_attempts": routing["fallback_attempts"],
        "prohibited_fallback_attempts": routing["prohibited_fallback_attempts"],
    }


class AuthenticationAuditContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        METRICS.reset()

    async def test_ordinary_throttled_and_recovered_auth_are_distinct(self) -> None:
        clock = FakeClock()
        app = RecordingMcpApp()
        header_secret = b"synthetic-invalid-bearer-marker"
        query_secret = b"synthetic-query-secret-marker"
        body_secret = b"synthetic-body-secret-marker"
        candidate_path_secret = b"incorrect-access-path"

        with tempfile.TemporaryDirectory() as directory, patch(
            "ha_mcp_engineering.routing.time.monotonic", clock
        ):
            audit_path = Path(directory) / "audit.jsonl"
            audit = AuditLogger(str(audit_path), SECRET)
            gateway = AuthenticatedMcpGateway(app, settings(str(audit_path)), audit)
            before = provider_counters()

            ordinary = [
                await asgi_request(gateway, "/mcp"),
                await asgi_request(
                    gateway,
                    "/mcp",
                    headers=((b"authorization", b"Token malformed-marker"),),
                ),
                await asgi_request(
                    gateway,
                    "/mcp",
                    headers=((b"authorization", b"Bearer " + header_secret),),
                ),
                await asgi_request(gateway, "/incorrect-access-path/mcp"),
                await asgi_request(gateway, f"/malformed/{SECRET}/mcp"),
            ]
            throttled = await asgi_request(
                gateway,
                "/throttled/mcp",
                headers=((b"authorization", b"Bearer " + header_secret),),
                query_string=b"access_token=" + query_secret,
                body=b'{"access_token":"' + body_secret + b'"}',
            )

            self.assertEqual([item[0] for item in ordinary], [404] * 5)
            self.assertEqual(throttled[0], 429)
            self.assertEqual(throttled[1], b"too many requests")
            self.assertNotIn(b"retry-after", throttled[2])
            self.assertEqual(app.calls, 0)
            self.assertEqual(provider_counters(), before)

            initial_records = read_audit(audit_path)
            self.assertEqual(len(initial_records), 6)
            self.assertEqual(
                [record["event"] for record in initial_records[:5]],
                [AUTH_FAILURE_EVENT] * 5,
            )
            self.assertEqual(
                [record["error_code"] for record in initial_records[:5]],
                ["authentication_failure"] * 5,
            )
            self.assertEqual(
                initial_records[5]["event"], AUTH_FAILURE_THROTTLED_EVENT
            )
            self.assertEqual(
                initial_records[5]["error_code"], "rate_limit_exceeded"
            )
            self.assertEqual(
                len({record["request_id"] for record in initial_records}), 6
            )
            self.assertTrue(
                all(record["result_status"] == "rejected" for record in initial_records)
            )
            self.assertTrue(
                all(record["authenticated"] is False for record in initial_records)
            )

            serialized = audit_path.read_bytes()
            for marker in (
                SECRET.encode(),
                header_secret,
                query_secret,
                body_secret,
                candidate_path_secret,
                b"authorization",
            ):
                self.assertNotIn(marker, serialized)
                self.assertNotIn(marker, b"".join(item[1] for item in ordinary))
                self.assertNotIn(marker, throttled[1])
            self.assertEqual(audit.state()["write_failures"], 0)

            clock.advance(121.0)
            valid = await asgi_request(gateway, f"/{SECRET}/mcp")
            recovered_invalid = await asgi_request(gateway, "/mcp")
            self.assertEqual(valid[0], 204)
            self.assertEqual(recovered_invalid[0], 404)
            self.assertEqual(app.calls, 1)
            self.assertEqual(read_audit(audit_path)[-1]["event"], AUTH_FAILURE_EVENT)
            self.assertEqual(provider_counters(), before)

    async def test_authenticated_rate_limit_remains_a_separate_event(self) -> None:
        clock = FakeClock()
        app = RecordingMcpApp()

        with tempfile.TemporaryDirectory() as directory, patch(
            "ha_mcp_engineering.routing.time.monotonic", clock
        ):
            audit_path = Path(directory) / "audit.jsonl"
            audit = AuditLogger(str(audit_path), SECRET)
            configured = settings(
                str(audit_path), rate_limit_per_minute=60, rate_limit_burst=1
            )
            gateway = AuthenticatedMcpGateway(app, configured, audit)
            before = provider_counters()

            accepted = await asgi_request(gateway, f"/{SECRET}/mcp")
            limited = await asgi_request(gateway, f"/{SECRET}/mcp")
            self.assertEqual(accepted[0], 204)
            self.assertEqual(limited[0], 429)
            self.assertNotIn(b"retry-after", limited[2])
            self.assertEqual(
                json.loads(limited[1])["error_code"], "rate_limit_exceeded"
            )
            self.assertEqual(app.calls, 1)
            self.assertEqual(provider_counters(), before)

            records = read_audit(audit_path)
            self.assertEqual([record["event"] for record in records], [RATE_LIMITED_EVENT])
            self.assertNotIn(AUTH_FAILURE_EVENT, audit_path.read_text(encoding="utf-8"))
            self.assertNotIn(
                AUTH_FAILURE_THROTTLED_EVENT,
                audit_path.read_text(encoding="utf-8"),
            )

            clock.advance(1.1)
            recovered = await asgi_request(gateway, f"/{SECRET}/mcp")
            self.assertEqual(recovered[0], 204)
            self.assertEqual(app.calls, 2)
            self.assertEqual(provider_counters(), before)


class AuditFilteringAndSanitizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_filters_are_exact_and_separately_queryable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            audit = AuditLogger(str(audit_path), SECRET)
            for event in (
                AUTH_FAILURE_EVENT,
                AUTH_FAILURE_THROTTLED_EVENT,
                RATE_LIMITED_EVENT,
            ):
                self.assertTrue(audit.write({"event": event, "result_status": "rejected"}))

            with patch.dict(os.environ, {"AUDIT_PATH": str(audit_path)}):
                for event in (
                    AUTH_FAILURE_EVENT,
                    AUTH_FAILURE_THROTTLED_EVENT,
                    RATE_LIMITED_EVENT,
                ):
                    rendered = await compatibility.get_audit_log(lines=10, event=event)
                    rows = [json.loads(line) for line in rendered.splitlines()]
                    self.assertEqual([row["event"] for row in rows], [event])

    def test_auth_audit_payloads_redact_nested_secrets_and_fail_closed(self) -> None:
        bearer = "synthetic-bearer-token-marker"
        query = "synthetic-query-token-marker"

        class BrokenValue:
            def __str__(self) -> str:
                raise RuntimeError("must not escape")

        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            audit = AuditLogger(str(audit_path), SECRET, max_payload_chars=512)
            for event in (AUTH_FAILURE_EVENT, AUTH_FAILURE_THROTTLED_EVENT):
                self.assertTrue(
                    audit.write(
                        {
                            "event": event,
                            "path": f"/malformed/{SECRET}/mcp?token={query}",
                            "headers": {"Authorization": f"Bearer {bearer}"},
                            "body": json.dumps({"access_token": bearer}),
                            "exception": {
                                "message": f"Authorization: Bearer {bearer}",
                                "nested": BrokenValue(),
                            },
                            "padding": "x" * 2_000,
                        }
                    )
                )

            serialized = audit_path.read_text(encoding="utf-8")
            for marker in (SECRET, bearer, query, "must not escape"):
                self.assertNotIn(marker, serialized)
            self.assertIn("payload_truncated", serialized)
            self.assertEqual(audit.state()["write_failures"], 0)


class CanonicalAuditEventNameTests(unittest.TestCase):
    def test_event_names_are_stable_and_shared(self) -> None:
        self.assertEqual(AUTH_FAILURE_EVENT, "auth_failure")
        self.assertEqual(AUTH_FAILURE_THROTTLED_EVENT, "auth_failure_throttled")
        self.assertEqual(RATE_LIMITED_EVENT, "rate_limited")


if __name__ == "__main__":
    unittest.main()
