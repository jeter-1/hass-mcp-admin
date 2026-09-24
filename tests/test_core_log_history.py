"""Offline HTTP and MCP contract tests. Every peer and credential is synthetic."""

import asyncio
from dataclasses import replace
import gzip
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from aiohttp import web
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.configuration import Settings
from ha_mcp_engineering.errors import GovernanceError
from ha_mcp_engineering.providers.core_logs import (
    CoreLogReader, MAX_DOWNLOAD_BYTES, MAX_EVIDENCE_BYTES, validate_arguments,
)
from ha_mcp_engineering.request_context import begin_request, end_request
from ha_mcp_engineering.tools import core_logs, get_registered_server, registered_tools
from ha_mcp_engineering.providers.routing import core_log_policy_allows_read, routing_for_tool
from ha_mcp_engineering.providers import core_logs as provider_module
from ha_mcp_engineering.ha_core_readmission.routes import static_tool_requirements


class CoreLogHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.response = web.Response(
            text="2026-09-01 01:02:03 ERROR synthetic retained event\n  traceback detail\n",
            content_type="text/plain",
        )
        self.handler = None
        async def serve(request):
            self.calls.append((request.method, request.path, dict(request.headers)))
            if self.handler:
                return await self.handler(request)
            return web.Response(status=self.response.status, body=self.response.body,
                                headers=dict(self.response.headers))
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", serve)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.settings = Settings(
            ha_url=f"http://127.0.0.1:{port}",
            ha_token="synthetic-core-log-token",
            access_secret="synthetic-core-log-access-secret",
            port=8100, audit_path="unused-synthetic-audit.jsonl",
            rate_limit_per_minute=120, rate_limit_burst=20,
            destructive_services=frozenset(), ha_timeout_seconds=1.0,
        )
        self.reader = CoreLogReader(self.settings)

        self.url_patch = patch.object(provider_module, "SUPERVISOR_URL", f"http://127.0.0.1:{port}/core/logs")
        self.token_patch = patch.dict(os.environ, {"SUPERVISOR_TOKEN": "synthetic-supervisor-token"})
        self.url_patch.start()
        self.token_patch.start()
        self.addCleanup(self.url_patch.stop)
        self.addCleanup(self.token_patch.stop)

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def result(self, **arguments):
        with patch.object(core_logs, "READER", self.reader), patch.object(
            core_logs, "SETTINGS", self.settings
        ):
            return json.loads(await core_logs.get_core_log_history(**arguments))

    async def test_retained_older_event_and_multiline_order(self):
        result = await self.result(limit=20, offset=100)
        self.assertTrue(result["success"])
        data = result["data"]
        self.assertIn("2026-09-01 01:02:03", data["log"])
        self.assertTrue(data["log"].endswith("  traceback detail\n"))
        self.assertEqual(data["returned_lines"], 2)
        self.assertEqual(data["suggested_older_offset"], 120)
        self.assertEqual(data["provider"], "supervisor_core_logs")
        self.assertFalse(data["fallback_occurred"])
        self.assertEqual(data["window_completeness"], "unknown")
        self.assertEqual(data["retention_coverage"], "unknown")
        self.assertIsNone(data["has_more"])
        self.assertEqual(result["metadata"]["completeness"], "partial")
        self.assertEqual(len(self.calls), 1)
        method, path, headers = self.calls[0]
        self.assertEqual((method, path), ("GET", "/core/logs"))
        self.assertEqual(headers["Authorization"], "Bearer synthetic-supervisor-token")
        self.assertEqual(headers["Range"], "entries=:-119:20")
        self.assertEqual(headers["Accept-Encoding"], "identity")

    async def test_secret_redaction_before_export(self):
        self.response = web.Response(text=(
            "Authorization: Bearer synthetic-new-token\n"
            "password=synthetic-password\n"
            "synthetic-supervisor-token\n"
            "token=" + self.settings.ha_token + "\n"
            + self.settings.access_secret + "\n"
            "https://example.invalid/api/webhook/synthetic-hook-secret\n"
        ), content_type="text/plain")
        result = await self.result()
        output = json.dumps(result)
        for secret in ("synthetic-supervisor-token", "synthetic-new-token", "synthetic-password",
                       self.settings.ha_token, self.settings.access_secret,
                       "synthetic-hook-secret"):
            self.assertNotIn(secret, output)
        self.assertTrue(result["data"]["redaction_applied"])

    async def test_oversized_download_is_bounded_and_incomplete(self):
        self.response = web.Response(body=b"synthetic log line\n" * 50_000,
                                     content_type="text/plain")
        result = await self.result()
        data = result["data"]
        self.assertEqual(data["downloaded_bytes"], MAX_DOWNLOAD_BYTES)
        self.assertFalse(data["download_complete"])
        self.assertIn("download_byte_limit", data["truncation_reasons"])
        self.assertTrue(data["truncated"])
        self.assertIsNone(data["suggested_older_offset"])
        self.assertLessEqual(len(json.dumps(data["log"])), MAX_EVIDENCE_BYTES)

    async def test_export_cap_preserves_complete_sanitized_lines(self):
        self.response = web.Response(text="synthetic-safe-line\n" * 5_000,
                                     content_type="text/plain")
        data = (await self.result())["data"]
        self.assertTrue(data["download_complete"])
        self.assertIn("export_byte_limit", data["truncation_reasons"])
        self.assertTrue(data["log"].endswith("\n"))
        self.assertLessEqual(len(json.dumps(data["log"])), MAX_EVIDENCE_BYTES)

    async def test_partial_final_credential_and_utf8_are_omitted(self):
        self.response = web.Response(body=b"safe complete line\npassword=partial-secret\xe2\x82",
                                     content_type="text/plain")
        result = await self.result()
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["log"], "safe complete line\n")
        self.assertNotIn("partial-secret", json.dumps(result))
        self.assertIn("incomplete_final_line", result["data"]["truncation_reasons"])

    async def test_invalid_utf8_fails_without_returning_body(self):
        self.response = web.Response(body=b"private-synthetic-text\xff\n",
                                     content_type="text/plain")
        result = await self.result()
        self.assertFalse(result["success"])
        self.assertEqual(result["details"]["reason"], "core_log_encoding_invalid")
        self.assertNotIn("private-synthetic-text", json.dumps(result))

    async def test_compressed_response_is_rejected_without_decompression(self):
        self.response = web.Response(body=gzip.compress(b"synthetic-text\n" * 100_000),
            headers={"Content-Encoding": "gzip"}, content_type="text/plain")
        result = await self.result()
        self.assertFalse(result["success"])
        self.assertEqual(result["details"]["reason"], "core_log_representation_invalid")

    async def test_http_errors_redirects_and_types_do_not_retry_or_leak(self):
        for status, expected in (
            (301, "home_assistant_api_error"), (302, "home_assistant_api_error"),
            (401, "authorization_failure"), (403, "authorization_failure"),
            (404, "unsupported_operation"), (429, "home_assistant_api_error"),
            (500, "home_assistant_api_error"), (503, "home_assistant_api_error"),
        ):
            with self.subTest(status=status):
                self.calls.clear()
                self.response = web.Response(status=status, text="private-synthetic-error",
                    headers={"Location": self.settings.ha_url + "/forbidden"})
                result = await self.result()
                self.assertFalse(result["success"])
                self.assertEqual(result["error_code"], expected)
                self.assertNotIn("private-synthetic-error", json.dumps(result))
                self.assertEqual(len(self.calls), 1)
        self.response = web.json_response({"logs": "private-synthetic-log"})
        self.assertEqual((await self.result())["details"]["reason"],
                         "core_log_representation_invalid")

    async def test_timeout_during_body_and_no_retry(self):
        async def slow(request):
            response = web.StreamResponse(headers={"Content-Type": "text/plain"})
            await response.prepare(request)
            await response.write(b"private-synthetic-incomplete")
            await asyncio.sleep(0.06)
            return response
        self.handler = slow
        self.reader = CoreLogReader(replace(self.settings, ha_timeout_seconds=0.01))
        result = await self.result()
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "home_assistant_timeout")
        self.assertNotIn("private-synthetic", json.dumps(result))
        self.assertEqual(len(self.calls), 1)

    async def test_cancelled_read_does_not_retry(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        async def blocked(request):
            entered.set()
            await release.wait()
            return web.Response(text="synthetic\n")
        self.handler = blocked
        task = asyncio.create_task(self.reader.read_window(limit=100, offset=0))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        release.set()
        self.assertEqual(len(self.calls), 1)

    async def test_authenticated_supervisor_read_survives_core_authority_outage(self):
        from ha_mcp_engineering.audit import AuditLogger
        from ha_mcp_engineering.routing import AuthenticatedMcpGateway
        unavailable_core = SimpleNamespace(acquire=Mock(return_value=None))
        async def app(scope, receive, send):
            rpc = json.loads((await receive())["body"])
            tool = registered_tools(get_registered_server())[rpc["params"]["name"]]
            rendered = await tool.run(rpc["params"]["arguments"])
            payload = {"jsonrpc": "2.0", "id": rpc["id"], "result": {
                "content": [{"type": "text", "text": rendered}], "isError": False}}
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": json.dumps(payload).encode()})
        gateway = AuthenticatedMcpGateway(app, self.settings,
            AuditLogger("unused", self.settings.access_secret, enabled=False),
            core_runtime=unavailable_core)
        with patch.object(core_logs, "READER", self.reader):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway),
                                        base_url="http://127.0.0.1:8100") as client:
                rpc = {"jsonrpc": "2.0", "id": "core-log-outage", "method": "tools/call",
                       "params": {"name": "get_core_log_history", "arguments": {}}}
                response = await client.post(f"/{self.settings.access_secret}/mcp", json=rpc)
                result = json.loads(response.json()["result"]["content"][0]["text"])
                self.assertTrue(result["success"])
                self.assertIn("retained event", result["data"]["log"])
                unavailable_core.acquire.assert_not_called()
                self.assertEqual(len(self.calls), 1)
                rpc["params"]["name"] = "get_error_log"
                refused = await client.post(f"/{self.settings.access_secret}/mcp", json=rpc)
                self.assertIn("CoreCapabilityUnavailable", refused.text)
                unavailable_core.acquire.assert_called_once()
                self.assertEqual(len(self.calls), 1)

    async def test_removed_direct_policy_denies_read(self):
        with patch.object(core_logs, "core_log_policy_allows_read", return_value=False):
            result = await self.result()
        self.assertEqual(result["error_code"], "provider_prohibited")
        self.assertEqual(self.calls, [])

    async def test_empty_window_never_claims_all_clear_or_terminal(self):
        self.response = web.Response(text="", content_type="text/plain")
        data = (await self.result())["data"]
        self.assertEqual(data["log"], "")
        self.assertIsNone(data["has_more"])
        self.assertIsNone(data["suggested_older_offset"])
        self.assertEqual(data["completeness"], "partial")
        self.assertEqual(data["retention_coverage"], "unknown")

    async def test_repeated_content_is_detectable_without_auto_paging(self):
        first = (await self.result(offset=0))["data"]
        second = (await self.result(offset=100))["data"]
        self.assertEqual(first["sanitized_content_sha256"], second["sanitized_content_sha256"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(second["pagination_consistency"], "not_snapshot_bound")

    async def test_missing_timestamps_remain_verbatim_and_unknown(self):
        self.response = web.Response(text="timestamp unavailable\n", content_type="text/plain")
        data = (await self.result(offset=10_000))["data"]
        self.assertEqual(data["log"], "timestamp unavailable\n")
        self.assertIsNone(data["suggested_older_offset"])
        self.assertNotIn("window_start", data)

    async def test_small_response_budget_is_valid_json(self):
        self.settings = replace(self.settings, response_size_limit=4096)
        self.reader = CoreLogReader(self.settings)
        self.response = web.Response(text="synthetic " * 1_000 + "\n", content_type="text/plain")
        result = await self.result()
        self.assertLessEqual(len(json.dumps(result, separators=(",", ":"))), 4096)
        self.assertTrue(result["success"])

    async def test_raw_mcp_arguments_reject_unknowns_coercion_and_secrets(self):
        tool = registered_tools(get_registered_server())["get_core_log_history"]
        cases = [
            {"source": "supervisor"}, {"slug": "other-addon"},
            {"path": "/forbidden"}, {"headers": {"Authorization": "synthetic-secret"}},
            {"url": "http://example.invalid/private"}, {"follow": True},
            {"limit": True}, {"limit": 1}, {"limit": 201}, {"limit": "10"},
            {"limit": 2.0}, {"offset": False}, {"offset": "2"},
            {"offset": -1}, {"offset": 10001}, {"offset": None},
        ]
        for arguments in cases:
            with self.subTest(arguments=list(arguments)):
                result = json.loads(await tool.run(arguments))
                self.assertFalse(result["success"])
                self.assertEqual(result["error_code"], "invalid_request")
                self.assertNotIn("synthetic-secret", json.dumps(result))
        self.assertEqual(self.calls, [])

    async def test_connection_drop_before_headers_does_not_trigger_aiohttp_retry(self):
        async def disconnected(request):
            request.transport.close()
            return web.Response(text="synthetic never delivered")
        self.handler = disconnected
        result = await self.result()
        self.assertEqual(result["error_code"], "home_assistant_unavailable")
        self.assertEqual(len(self.calls), 1)

    async def test_no_supervisor_context_never_uses_core_token(self):
        with patch.dict(os.environ, {"SUPERVISOR_TOKEN": ""}):
            result = await self.result()
        self.assertEqual(result["details"]["reason"], "supervisor_context_unavailable")
        self.assertEqual(self.calls, [])

    async def test_concurrent_read_is_refused_and_cancellation_releases_slot(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked(request):
            entered.set()
            await release.wait()
            return web.Response(text="synthetic\n", content_type="text/plain")
        self.handler = blocked
        task = asyncio.create_task(self.reader.read_window(limit=2, offset=0))
        await entered.wait()
        result = await self.result()
        self.assertEqual(result["details"]["reason"], "core_log_read_in_progress")
        self.assertEqual(len(self.calls), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        release.set()
        self.handler = None
        self.assertTrue((await self.result())["success"])
        self.assertEqual(len(self.calls), 2)

    async def test_provider_metrics_and_audit_attribution(self):
        from ha_mcp_engineering.observability import METRICS
        before = METRICS.provider_requests["supervisor_core_logs"]
        telemetry, token = begin_request("synthetic-core-log-audit")
        try:
            result = await self.result()
            self.assertEqual(telemetry.provider_dispatch_count, 1)
            self.assertEqual(telemetry.provider_partial_count, 1)
            self.assertEqual(telemetry.audit_context["provider"], "supervisor_core_logs")
            self.assertEqual(result["timing"]["home_assistant_request_count"], 1)
            self.assertNotIn("retained event", json.dumps(telemetry.audit_context))
            self.assertEqual(METRICS.provider_requests["supervisor_core_logs"], before + 1)
            with patch.dict(os.environ, {"SUPERVISOR_TOKEN": ""}):
                await self.result()
            self.assertEqual(METRICS.provider_requests["supervisor_core_logs"], before + 1)
            self.response = web.Response(status=403, text="private synthetic body")
            denied = await self.result()
            self.assertFalse(denied["success"])
            self.assertEqual(telemetry.provider_failure_count, 1)
            self.assertEqual(METRICS.provider_requests["supervisor_core_logs"], before + 2)
        finally:
            end_request(token)

    async def test_registered_positive_and_metadata(self):
        tool = registered_tools(get_registered_server())["get_core_log_history"]
        with patch.object(core_logs, "READER", self.reader):
            result = json.loads(await tool.run({"limit": 2, "offset": 0}))
        self.assertTrue(result["success"])
        self.assertFalse(tool.parameters["additionalProperties"])
        self.assertTrue(tool.annotations.readOnlyHint)
        self.assertFalse(tool.annotations.destructiveHint)
        self.assertTrue(core_log_policy_allows_read())
        self.assertEqual(routing_for_tool(tool.name).preferred_provider, "supervisor_core_logs")
        self.assertEqual(static_tool_requirements(tool.name), ())


if __name__ == "__main__":
    unittest.main()
