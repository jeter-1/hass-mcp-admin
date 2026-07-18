"""RC2dev7 exact top-level audit-event filtering regressions."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_beta_v2 import INITIALIZE_REQUEST, SECRET, beta_settings

from ha_mcp_engineering.audit import (
    AUTH_FAILURE_EVENT,
    AUTH_FAILURE_THROTTLED_EVENT,
    RATE_LIMITED_EVENT,
    AuditLogger,
)
from ha_mcp_engineering.observability import METRICS
from ha_mcp_engineering.routing import AuthenticatedMcpGateway
from ha_mcp_engineering.tools import compatibility


def _json_rows(rendered: str) -> list[dict]:
    if rendered in {"No audit log yet.", "No matching entries."}:
        return []
    return [json.loads(line) for line in rendered.splitlines() if line.strip()]


def _provider_snapshot() -> dict:
    routing = METRICS.snapshot()["provider_routing"]
    return {
        "requests": routing["requests_by_provider"],
        "failures": routing["failures_by_provider"],
        "operational_failures": routing["provider_operational_failures"],
        "fallback_attempts": routing["fallback_attempts"],
        "prohibited_fallback_attempts": routing[
            "prohibited_fallback_attempts"
        ],
    }


class AuditToolMcpApp:
    """Minimal MCP responder behind the real authenticated routing gateway."""

    async def __call__(self, scope, receive, send) -> None:
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        request = json.loads(body or b"{}")
        if request.get("method") == "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "serverInfo": {"name": "dev7-audit-fixture", "version": "1"},
            }
        elif request.get("method") == "tools/call":
            params = request.get("params") or {}
            if params.get("name") != "get_audit_log":
                raise AssertionError("fixture accepts only get_audit_log")
            arguments = params.get("arguments") or {}
            rendered = await compatibility.get_audit_log(**arguments)
            result = {
                "content": [{"type": "text", "text": rendered}],
                "isError": False,
            }
        else:
            raise AssertionError("unexpected MCP method")
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": request.get("id"), "result": result}
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"event: message\ndata: " + payload + b"\n\n",
            }
        )


class RoutedAuditClient:
    def __init__(self, gateway):
        self.gateway = gateway

    def request(self, path: str, payload: dict, request_id: str):
        messages: list[dict] = []
        delivered = False
        body = json.dumps(payload).encode("utf-8")

        async def receive() -> dict:
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: dict) -> None:
            messages.append(message)

        asyncio.run(
            self.gateway(
                {
                    "type": "http",
                    "method": "POST",
                    "path": path,
                    "raw_path": path.encode("utf-8"),
                    "query_string": b"",
                    "headers": [
                        (b"accept", b"application/json, text/event-stream"),
                        (b"content-type", b"application/json"),
                        (b"x-request-id", request_id.encode("ascii")),
                    ],
                    "client": ("127.0.0.77", 1),
                },
                receive,
                send,
            )
        )
        start = next(
            item for item in messages if item["type"] == "http.response.start"
        )
        response_body = b"".join(
            item.get("body", b"")
            for item in messages
            if item["type"] == "http.response.body"
        )
        return start["status"], response_body

    def call_audit(self, event: str, request_id: str, *, lines: int = 50) -> str:
        status, response_body = self.request(
            f"/{SECRET}/mcp",
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {
                    "name": "get_audit_log",
                    "arguments": {"event": event, "lines": lines},
                },
            },
            request_id,
        )
        if status != 200:
            raise AssertionError(response_body)
        data_line = next(
            line
            for line in response_body.replace(b"\r", b"").splitlines()
            if line.startswith(b"data: ")
        )
        message = json.loads(data_line.removeprefix(b"data: "))
        self_result = message["result"]
        if self_result.get("isError"):
            raise AssertionError(self_result)
        return self_result["content"][0]["text"]

class ExactTopLevelEventTests(unittest.IsolatedAsyncioTestCase):
    async def _render(self, records: list[object], event: str) -> str:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            path.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"AUDIT_PATH": str(path)}):
                return await compatibility.get_audit_log(lines=50, event=event)

    async def test_exact_top_level_event_matrix(self) -> None:
        records = [
            {"id": "ordinary", "event": AUTH_FAILURE_EVENT},
            {"id": "throttled", "event": AUTH_FAILURE_THROTTLED_EVENT},
            {"id": "general", "event": RATE_LIMITED_EVENT},
            {
                "id": "nested-ordinary",
                "event": "tool_call",
                "context": {"arguments": {"event": AUTH_FAILURE_EVENT}},
            },
            {
                "id": "nested-throttled",
                "event": "tool_call",
                "context": {
                    "arguments": {"event": AUTH_FAILURE_THROTTLED_EVENT}
                },
            },
            {
                "id": "nested-general",
                "event": "tool_call",
                "context": {"arguments": {"event": RATE_LIMITED_EVENT}},
            },
            {
                "id": "message-text",
                "event": "tool_call",
                "message": (
                    "auth_failure auth_failure_throttled rate_limited"
                ),
            },
            {
                "id": "ordinary-nested-throttled",
                "event": AUTH_FAILURE_EVENT,
                "context": {"next_event": AUTH_FAILURE_THROTTLED_EVENT},
            },
            {
                "id": "throttled-nested-ordinary",
                "event": AUTH_FAILURE_THROTTLED_EVENT,
                "context": {"next_event": AUTH_FAILURE_EVENT},
            },
        ]
        expectations = {
            AUTH_FAILURE_EVENT: ["ordinary", "ordinary-nested-throttled"],
            AUTH_FAILURE_THROTTLED_EVENT: [
                "throttled",
                "throttled-nested-ordinary",
            ],
            RATE_LIMITED_EVENT: ["general"],
            "tool_call": [
                "nested-ordinary",
                "nested-throttled",
                "nested-general",
                "message-text",
            ],
        }
        for requested, expected_ids in expectations.items():
            with self.subTest(event=requested):
                rows = _json_rows(await self._render(records, requested))
                self.assertEqual([row["id"] for row in rows], expected_ids)
                self.assertTrue(
                    all(row.get("event") == requested for row in rows)
                )

    async def test_case_sensitive_exact_equality(self) -> None:
        records = [
            {"id": "exact", "event": AUTH_FAILURE_EVENT},
            {"id": "case", "event": "AUTH_FAILURE"},
            {"id": "suffix", "event": "auth_failure_extra"},
            {"id": "prefix", "event": "failure"},
        ]
        rows = _json_rows(
            await self._render(records, AUTH_FAILURE_EVENT)
        )
        self.assertEqual([row["id"] for row in rows], ["exact"])


class MalformedAuditRecordTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_non_object_and_oversized_records_are_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            source = "\n".join(
                [
                    "",
                    "not-json synthetic-malformed-secret",
                    '{"event":"auth_failure"',
                    "[]",
                    '"string"',
                    "42",
                    json.dumps({"id": "missing-event"}),
                    json.dumps({"id": "null-event", "event": None}),
                    json.dumps({"id": "array-event", "event": ["tool_call"]}),
                    json.dumps(
                        {
                            "id": "oversized",
                            "event": AUTH_FAILURE_EVENT,
                            "message": "x" * compatibility.MAX_AUDIT_RECORD_CHARS,
                        }
                    ),
                    json.dumps(
                        {"id": "valid-later", "event": AUTH_FAILURE_EVENT}
                    ),
                ]
            )
            path.write_text(source + "\n", encoding="utf-8")
            with patch.dict(os.environ, {"AUDIT_PATH": str(path)}):
                filtered = await compatibility.get_audit_log(
                    lines=50, event=AUTH_FAILURE_EVENT
                )
                unfiltered = await compatibility.get_audit_log(lines=50)

        self.assertEqual(
            [row["id"] for row in _json_rows(filtered)], ["valid-later"]
        )
        unfiltered_rows = _json_rows(unfiltered)
        self.assertEqual(
            [row["id"] for row in unfiltered_rows],
            ["missing-event", "null-event", "array-event", "valid-later"],
        )
        for forbidden in (
            "synthetic-malformed-secret",
            '"event":"auth_failure"',
            '"id": "oversized"',
        ):
            self.assertNotIn(forbidden, filtered)
            self.assertNotIn(forbidden, unfiltered)

    async def test_recent_matching_tail_is_bounded_and_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            with path.open("w", encoding="utf-8") as stream:
                for index in range(2_000):
                    stream.write(
                        json.dumps(
                            {
                                "index": index,
                                "event": (
                                    AUTH_FAILURE_EVENT
                                    if index % 2 == 0
                                    else "tool_call"
                                ),
                            }
                        )
                        + "\n"
                    )
            with patch.dict(os.environ, {"AUDIT_PATH": str(path)}):
                rendered = await compatibility.get_audit_log(
                    lines=25, event=AUTH_FAILURE_EVENT
                )
        rows = _json_rows(rendered)
        self.assertEqual(len(rows), 25)
        self.assertEqual(
            [row["index"] for row in rows], list(range(1950, 2000, 2))
        )


class AuditRedactionInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_filtered_and_unfiltered_reads_never_emit_secret_markers(self):
        synthetic_secret = "synthetic-audit-read-secret-marker"
        bearer = "synthetic-audit-read-bearer-marker"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            audit = AuditLogger(str(path), synthetic_secret)
            self.assertTrue(
                audit.write(
                    {
                        "event": AUTH_FAILURE_EVENT,
                        "message": f"Authorization: Bearer {bearer}",
                        "arguments": {
                            "event": AUTH_FAILURE_THROTTLED_EVENT,
                            "path": f"/{synthetic_secret}/mcp?token={bearer}",
                        },
                        "exception": f"request failed at /{synthetic_secret}/mcp",
                    }
                )
            )
            with path.open("a", encoding="utf-8") as stream:
                stream.write(f"malformed {synthetic_secret} {bearer}\n")
            with patch.dict(os.environ, {"AUDIT_PATH": str(path)}):
                filtered = await compatibility.get_audit_log(
                    lines=50, event=AUTH_FAILURE_EVENT
                )
                unfiltered = await compatibility.get_audit_log(lines=50)

        for rendered in (filtered, unfiltered):
            self.assertEqual(len(_json_rows(rendered)), 1)
            self.assertNotIn(synthetic_secret, rendered)
            self.assertNotIn(bearer, rendered)
            self.assertIn("[REDACTED:", rendered)
        self.assertEqual(audit.state()["write_failures"], 0)


class RoutedAuditFilteringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.audit_path = Path(cls.tempdir.name) / "audit.jsonl"
        cls.environment = patch.dict(
            os.environ, {"AUDIT_PATH": str(cls.audit_path)}
        )
        cls.environment.__enter__()
        configured = beta_settings(str(cls.audit_path))
        cls.gateway = AuthenticatedMcpGateway(
            AuditToolMcpApp(),
            configured,
            AuditLogger(str(cls.audit_path), SECRET),
        )
        cls.routed = RoutedAuditClient(cls.gateway)
        status, body = cls.routed.request(
            f"/{SECRET}/mcp",
            INITIALIZE_REQUEST,
            "dev7-initialize",
        )
        if status != 200:
            raise AssertionError(body)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.environment.__exit__(None, None, None)
        cls.tempdir.cleanup()

    def setUp(self) -> None:
        METRICS.reset()
        self.audit_path.unlink(missing_ok=True)
        self.gateway.clients.clear()
        self.gateway.auth_failures.clear()

    def tearDown(self) -> None:
        METRICS.reset()

    def test_routed_query_does_not_match_its_prior_self_audit(self) -> None:
        first = self.routed.call_audit(
            AUTH_FAILURE_THROTTLED_EVENT,
            "dev7-filter-first-request",
        )
        second = self.routed.call_audit(
            AUTH_FAILURE_THROTTLED_EVENT,
            "dev7-filter-second-request",
        )
        records = [
            json.loads(line)
            for line in self.audit_path.read_text(encoding="utf-8").splitlines()
        ]

        self.assertIn(first, {"No audit log yet.", "No matching entries."})
        self.assertEqual(second, "No matching entries.")
        first_record = next(
            row
            for row in records
            if row.get("request_id") == "dev7-filter-first-request"
        )
        self.assertEqual(first_record["event"], "tool_call")
        self.assertEqual(first_record["tool_name"], "get_audit_log")
        self.assertEqual(
            first_record["parameters"]["event"],
            AUTH_FAILURE_THROTTLED_EVENT,
        )
        self.assertNotIn("dev7-filter-first-request", second)

    def test_repeated_routed_queries_preserve_exact_security_counts(self):
        audit = AuditLogger(str(self.audit_path), SECRET)
        for index in range(5):
            self.assertTrue(
                audit.write(
                    {
                        "event": AUTH_FAILURE_EVENT,
                        "request_id": f"ordinary-{index}",
                    }
                )
            )
        self.assertTrue(
            audit.write(
                {
                    "event": AUTH_FAILURE_THROTTLED_EVENT,
                    "request_id": "throttled-1",
                }
            )
        )
        self.assertTrue(
            audit.write(
                {"event": RATE_LIMITED_EVENT, "request_id": "general-1"}
            )
        )
        before = _provider_snapshot()
        results = [
            self.routed.call_audit(
                AUTH_FAILURE_THROTTLED_EVENT,
                "repeat-throttled-1",
            ),
            self.routed.call_audit(
                AUTH_FAILURE_THROTTLED_EVENT,
                "repeat-throttled-2",
            ),
            self.routed.call_audit(AUTH_FAILURE_EVENT, "repeat-ordinary"),
            self.routed.call_audit(RATE_LIMITED_EVENT, "repeat-general"),
            self.routed.call_audit("tool_call", "repeat-tool-call"),
        ]
        after = _provider_snapshot()

        self.assertEqual(len(_json_rows(results[0])), 1)
        self.assertEqual(len(_json_rows(results[1])), 1)
        self.assertEqual(len(_json_rows(results[2])), 5)
        self.assertEqual(len(_json_rows(results[3])), 1)
        self.assertTrue(_json_rows(results[4]))
        self.assertTrue(
            all(row["event"] == "tool_call" for row in _json_rows(results[4]))
        )
        self.assertNotIn("repeat-throttled-1", results[1])
        self.assertEqual(before, after)

    def test_real_auth_fixture_remains_five_one_and_no_provider_dispatch(self):
        before = _provider_snapshot()
        ordinary = [
            self.routed.request(
                f"/invalid-{index}/mcp",
                INITIALIZE_REQUEST,
                f"ordinary-{index}",
            )
            for index in range(5)
        ]
        throttled = self.routed.request(
            "/invalid-throttled/mcp",
            INITIALIZE_REQUEST,
            "throttled-1",
        )
        AuditLogger(str(self.audit_path), SECRET).write(
            {"event": RATE_LIMITED_EVENT, "request_id": "general-1"}
        )
        ordinary_rows = _json_rows(
            self.routed.call_audit(AUTH_FAILURE_EVENT, "auth-query")
        )
        throttled_rows = _json_rows(
            self.routed.call_audit(
                AUTH_FAILURE_THROTTLED_EVENT, "throttled-query"
            )
        )
        general_rows = _json_rows(
            self.routed.call_audit(RATE_LIMITED_EVENT, "rate-query")
        )
        after = _provider_snapshot()

        self.assertEqual([item[0] for item in ordinary], [404] * 5)
        self.assertEqual(throttled[0], 429)
        self.assertEqual(len(ordinary_rows), 5)
        self.assertEqual(len(throttled_rows), 1)
        self.assertEqual(len(general_rows), 1)
        self.assertEqual(
            {row["event"] for row in ordinary_rows}, {AUTH_FAILURE_EVENT}
        )
        self.assertEqual(
            {row["event"] for row in throttled_rows},
            {AUTH_FAILURE_THROTTLED_EVENT},
        )
        self.assertEqual(
            {row["event"] for row in general_rows}, {RATE_LIMITED_EVENT}
        )
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
