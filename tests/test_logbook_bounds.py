"""Synthetic native logbook acquisition, interval, projection and concurrency."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from aiohttp import web
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.clients import logbook
from ha_mcp_engineering.clients.rest import HomeAssistantRestClient
from ha_mcp_engineering.errors import ErrorCode, GovernanceError, HomeAssistantApiError, InvalidRequestError, error_definition
from ha_mcp_engineering.request_context import begin_request, end_request
from ha_mcp_engineering.tools import compatibility
from tests.test_beta_observability import settings


NOW = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
INTERVAL = {"start": "2026-09-26T12:00:00+00:00", "end": NOW.isoformat()}


def entries(count=1, size=20):
    return [{"entity_id": f"sensor.synthetic_{i}", "state": "on", "message": "x"*size}
            for i in range(count)]


class FakeClient:
    def __init__(self, value=None):
        self.value = [] if value is None else value
        self.calls = []

    async def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return json.dumps(self.value)


class ProjectionTests(unittest.TestCase):
    def project(self, records, limit=60000):
        return json.loads(logbook.project(json.dumps(records), INTERVAL, limit, ()))

    def assert_limit(self, call, reason):
        with self.assertRaises(GovernanceError) as error:
            call()
        self.assertEqual(error.exception.code, ErrorCode.LOGBOOK_RESPONSE_LIMIT_EXCEEDED)
        self.assertEqual(error.exception.details["reason"], reason)
        self.assertFalse(error.exception.retryable)

    def test_complete_empty_and_small_preserve_list(self):
        for records in ([], entries()):
            for limit in (1024, 60000):
                self.assertEqual(self.project(records, limit), records)

    def test_useful_partial_retains_whole_ordered_records_and_counts(self):
        records = entries(300, 2048)
        result = self.project(records)
        self.assertTrue(result["truncated"])
        self.assertGreater(result["returned"], 0)
        self.assertEqual(result["entries"], records[:result["returned"]])
        self.assertEqual(result["omitted"]+result["returned"], 300)
        self.assertEqual(result["requested_interval"], INTERVAL)
        self.assertEqual(result["ordering"], "source_order")
        self.assertLessEqual(len(json.dumps(result, separators=(",", ":"))), logbook.logbook_data_limit(60000))

    def test_no_useful_entry_is_limit_failure_not_empty_success(self):
        self.assert_limit(lambda: self.project(entries(1, 100000)), "response_bytes")
        self.assert_limit(lambda: self.project(entries(20, 1000), 1024), "response_bytes")

    def test_record_limit_boundary(self):
        self.assertEqual(len(logbook._decode(json.dumps([{}]*10000))), 10000)
        self.assert_limit(lambda: logbook._decode(json.dumps([{}]*10001)), "record_count")

    def test_depth_and_escaped_string_controls(self):
        for depth in (30, 31):
            raw = '[{"payload":' + '['*depth + '0' + ']'*depth + '}]'
            if depth == 30:
                self.assertEqual(len(logbook._decode(raw)), 1)
            else:
                self.assert_limit(lambda: logbook._decode(raw), "structure_depth")
        value = [{"message": '[{"quoted":"\\\\\\"' * 100}]
        self.assertEqual(logbook._decode(json.dumps(value)), value)

    def test_item_budget_includes_keys_and_malformed_members(self):
        # root + record + key + list + 49,996 scalars = exactly 50,000.
        exact = [{"payload": [None]*49996}]
        self.assertEqual(logbook._decode(json.dumps(exact)), exact)
        self.assert_limit(lambda: logbook._decode(json.dumps([{"payload": [None]*49997}])), "structure_items")
        self.assert_limit(lambda: logbook._decode(json.dumps([[None]*50000])), "structure_items")

    def test_malformed_json_shape_constants_and_duplicates(self):
        for raw in ('{}', 'null', '[0]', '[null]', '[{"x":NaN}]', '[{"x":1,"x":2}]', '[[[', '[{"x":1e999}]'):
            with self.subTest(raw=raw), self.assertRaises(HomeAssistantApiError):
                logbook.project(raw, INTERVAL, 60000, ())

    def test_secret_suppression_before_returned_evidence(self):
        value = [{"message": "token=synthetic-token /api/webhook/secret-webhook-1234567890",
                  "password": "synthetic-password", "nested": {"authorization": "Bearer synthetic"}}]
        result = logbook.project(json.dumps(value), INTERVAL, 60000, ("synthetic-password",))
        for secret in ("synthetic-token", "secret-webhook-1234567890", "synthetic-password", "Bearer synthetic"):
            self.assertNotIn(secret, result)
        self.assertIn("REDACTED", result)


class ReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_limit_ignores_forged_content_length(self):
        class Content:
            async def iter_chunked(self, size):
                yield b"x"*logbook.MAX_BYTES
                yield b"x"
        response = SimpleNamespace(headers={"Content-Length": "1"}, content=Content())
        with self.assertRaises(GovernanceError) as error:
            await logbook.read_body(response)
        self.assertEqual(error.exception.details["reason"], "response_bytes")

    async def test_exact_intervals_filtered_unfiltered_and_one_call(self):
        for hours in (12, 24, 72, 168):
            for entity in ("", "sensor.synthetic"):
                client = FakeClient(entries())
                with patch.object(logbook, "datetime") as clock:
                    clock.now.return_value = NOW
                    result = await logbook.LogbookReader().read(client, hours=hours, entity_id=entity, response_limit=60000)
                self.assertEqual(json.loads(result), entries())
                clock.now.assert_called_once_with(timezone.utc)
                self.assertEqual(len(client.calls), 1)
                args, kwargs = client.calls[0]
                path = urlsplit(args[1])
                self.assertEqual(args[0], "GET")
                self.assertEqual(path.path, "/logbook/"+(NOW-timedelta(hours=hours)).isoformat())
                self.assertEqual(parse_qs(path.query), {"end_time": [NOW.isoformat()], **({"entity": [entity]} if entity else {})})
                self.assertEqual(kwargs, {"raw": True, "logbook_read": True})

    async def test_invalid_inputs_never_acquire_or_dispatch(self):
        client = FakeClient()
        reader = logbook.LogbookReader()
        for hours in (0, -1, 169, float("inf"), float("nan"), 10**1000, -(10**1000), True, None, "12"):
            with self.subTest(hours=hours), self.assertRaises(InvalidRequestError):
                await reader.read(client, hours=hours, entity_id="", response_limit=60000)
        for entity in (None, "light.x&end_time=secret", "a.b,c.d", "a.b?x=1", "light.雪", "a."+"x"*254):
            with self.subTest(entity=entity), self.assertRaises(InvalidRequestError):
                await reader.read(client, hours=12, entity_id=entity, response_limit=60000)
        self.assertEqual(client.calls, [])
        self.assertFalse(reader._active)

    async def test_acquisition_cancellation_releases_capacity(self):
        reader = logbook.LogbookReader()
        waiting = asyncio.Event()
        client = FakeClient()
        async def blocked(*args, **kwargs):
            waiting.set()
            await asyncio.Future()
        with patch.object(client, "request", blocked):
            task = asyncio.create_task(reader.read(client, hours=12, entity_id="", response_limit=60000))
            await waiting.wait()
            with self.assertRaises(GovernanceError) as busy:
                await reader.read(client, hours=12, entity_id="", response_limit=60000)
            self.assertEqual(busy.exception.code, ErrorCode.LOGBOOK_BUSY)
            self.assertTrue(busy.exception.retryable)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(json.loads(await reader.read(client, hours=12, entity_id="", response_limit=60000)), [])

    async def test_cancelled_processing_retains_physical_slot(self):
        reader, client = logbook.LogbookReader(), FakeClient()
        entered, release = threading.Event(), threading.Event()
        def blocked(*args):
            entered.set()
            release.wait(3)
            raise RuntimeError("synthetic-private-exception")
        try:
            with patch.object(logbook, "project", blocked):
                task = asyncio.create_task(reader.read(client, hours=12, entity_id="", response_limit=60000))
                while not entered.is_set():
                    await asyncio.sleep(.001)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                with self.assertRaises(GovernanceError) as busy:
                    await reader.read(client, hours=12, entity_id="", response_limit=60000)
                self.assertEqual(busy.exception.code, ErrorCode.LOGBOOK_BUSY)
                self.assertTrue(busy.exception.retryable)
                self.assertEqual(len(client.calls), 1)
                release.set()
                for _ in range(200):
                    if not reader._active:
                        break
                    await asyncio.sleep(.001)
                self.assertFalse(reader._active)
        finally:
            release.set()
        self.assertEqual(json.loads(await reader.read(client, hours=12, entity_id="", response_limit=60000)), [])

    async def test_registered_concurrent_read_is_retryable_busy_without_another_request(self):
        reader, client = logbook.LogbookReader(), FakeClient(entries())
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked(*args, **kwargs):
            entered.set()
            await release.wait()
            return json.dumps(entries())

        with patch.object(client, "request", AsyncMock(side_effect=blocked)) as request, \
                patch.object(compatibility, "REST_CLIENT", client), \
                patch.object(compatibility, "LOGBOOK", reader):
            first = asyncio.create_task(compatibility.get_logbook(entity_id="sensor.synthetic_a"))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                result = json.loads(await compatibility.get_logbook(entity_id="sensor.synthetic_b"))
                self.assertFalse(result["success"])
                self.assertEqual(result["error_code"], ErrorCode.LOGBOOK_BUSY.value)
                self.assertTrue(result["retryable"])
                self.assertEqual(result["message"], error_definition(ErrorCode.LOGBOOK_BUSY).message)
                self.assertEqual(error_definition(ErrorCode.LOGBOOK_BUSY).http_status, 409)
                self.assertEqual(result["timing"]["home_assistant_request_count"], 0)
                self.assertEqual(request.await_count, 1)
                self.assertFalse(first.done())
            finally:
                release.set()
                completed = json.loads(await first)
            self.assertTrue(completed["success"])
            self.assertEqual(completed["data"], entries())
            self.assertEqual(request.await_count, 1)  # No queued or automatic retry.
            later = json.loads(await compatibility.get_logbook(entity_id="sensor.synthetic_b"))
            self.assertTrue(later["success"])
            self.assertEqual(later["data"], entries())
            self.assertEqual(request.await_count, 2)

    async def test_registered_response_is_useful_and_truthfully_partial(self):
        for size in (0, 20, 2048):
            records = [] if size == 0 else entries(300 if size == 2048 else 1, size)
            with patch.object(compatibility, "REST_CLIENT", FakeClient(records)), patch.object(compatibility, "LOGBOOK", logbook.LogbookReader()):
                result = json.loads(await compatibility.get_logbook(hours=72))
            self.assertTrue(result["success"])
            coverage = result["metadata"]["source_coverage"][0]
            self.assertEqual(coverage["provider"], "direct_ha_api")
            self.assertFalse(coverage["fallback_occurred"])
            if size == 2048:
                self.assertEqual(coverage["completeness"], "partial")
                self.assertEqual(result["data"]["entries"], records[:result["data"]["returned"]])
            else:
                self.assertEqual(coverage["completeness"], "complete")
                self.assertEqual(result["data"], records)

    async def test_minimum_registered_response_and_local_refusal(self):
        for records in ([], entries(), entries(20, 1000)):
            with patch.object(compatibility, "REST_CLIENT", FakeClient(records)), patch.object(compatibility, "LOGBOOK", logbook.LogbookReader()), patch.object(compatibility, "SETTINGS", replace(compatibility.SETTINGS, response_size_limit=1024)):
                raw = await compatibility.get_logbook()
            self.assertLessEqual(len(raw.encode()), 1024)
            result = json.loads(raw)
            if len(records) < 2:
                self.assertTrue(result["success"])
                self.assertEqual(result["data"], records)
            else:
                self.assertFalse(result["success"])
                self.assertEqual(result["error_code"], "logbook_response_limit_exceeded")
                self.assertFalse(result["retryable"])

    async def test_small_budgets_retain_useful_whole_partial_and_coverage(self):
        records = entries(40, 20)
        for limit in (1024, 2048, 60000):
            for request_id in ("synthetic-budget", "r"*128):
                telemetry, token = begin_request(request_id)
                try:
                    with patch.object(compatibility, "REST_CLIENT", FakeClient(records)), patch.object(compatibility, "LOGBOOK", logbook.LogbookReader()), patch.object(compatibility, "SETTINGS", replace(compatibility.SETTINGS, response_size_limit=limit)):
                        raw = await compatibility.get_logbook()
                    result = json.loads(raw)
                    with self.subTest(limit=limit, request_id_length=len(request_id)):
                        self.assertLessEqual(len(raw.encode()), limit)
                        self.assertTrue(result["success"])
                        self.assertEqual(result["request_id"], request_id)
                        coverage = result["metadata"]["source_coverage"][0]
                        self.assertEqual(coverage["provider"], "direct_ha_api")
                        self.assertFalse(coverage["fallback_occurred"])
                        if limit < 60000:
                            data = result["data"]
                            self.assertTrue(data["truncated"])
                            self.assertGreater(data["returned"], 0)
                            self.assertEqual(data["entries"], records[:data["returned"]])
                            self.assertEqual(data["returned"] + data["omitted"], len(records))
                            self.assertEqual(coverage["completeness"], "partial")
                            self.assertIn("start", data["requested_interval"])
                            self.assertIn("end", data["requested_interval"])
                        else:
                            self.assertEqual(result["data"], records)
                            self.assertEqual(coverage["completeness"], "complete")
                finally:
                    end_request(token)

    async def test_minimum_keeps_unkeyed_records_with_long_request_id(self):
        records = [{"message": "x"*180}]
        telemetry, token = begin_request("r"*128)
        try:
            with patch.object(compatibility, "REST_CLIENT", FakeClient(records)), patch.object(compatibility, "LOGBOOK", logbook.LogbookReader()), patch.object(compatibility, "SETTINGS", replace(compatibility.SETTINGS, response_size_limit=1024)):
                raw = await compatibility.get_logbook()
            result = json.loads(raw)
            self.assertLessEqual(len(raw.encode()), 1024)
            self.assertEqual(result["data"], records)
            self.assertTrue(result["success"])
        finally:
            end_request(token)


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = 0
        self.mode = "ok"
        self.release = asyncio.Event()
        app = web.Application()
        async def serve(request):
            self.calls += 1
            if self.mode == "disconnect":
                request.transport.close()
                return web.Response()
            if self.mode == "redirect":
                raise web.HTTPFound("/api/logbook/repeated")
            if self.mode == "broken_length":
                response = web.StreamResponse(headers={"Content-Length": "100"})
                await response.prepare(request)
                await response.write(b"[]")
                request.transport.close()
                return response
            if self.mode == "slow":
                await self.release.wait()
            if self.mode == "encoded":
                return web.Response(body=gzip.compress(b"[]"), headers={"Content-Encoding": "gzip"})
            if self.mode == "utf8":
                return web.Response(body=b"\xff")
            if self.mode == "error":
                return web.Response(status=401, text="synthetic-secret-body")
            if self.mode == "oversized_error":
                return web.Response(status=401, body=b"x"*(logbook.MAX_BYTES+1))
            if self.mode == "encoded_error":
                return web.Response(status=500, body=b"synthetic-secret-body", headers={"Content-Encoding": "gzip"})
            if request.match_info["start"] == "synthetic":
                return web.json_response(entries())
            if self.mode in {"large", "exact"}:
                response = web.StreamResponse()
                await response.prepare(request)
                try:
                    await response.write(b" "*(logbook.MAX_BYTES-2))
                    await response.write(b"[]" + (b" " if self.mode == "large" else b""))
                    await response.write_eof()
                except ConnectionResetError:
                    pass
                return response
            return web.json_response(entries(480, 2048) if self.mode == "dense" else [])
        app.router.add_get("/api/logbook/{start}", serve)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.client = HomeAssistantRestClient(replace(settings("/tmp/synthetic-unused-logbook-audit"), ha_url=f"http://127.0.0.1:{port}"))

    async def asyncTearDown(self):
        self.release.set()
        await self.runner.cleanup()

    async def read(self):
        return await logbook.LogbookReader().read(self.client, hours=72, entity_id="", response_limit=60000)

    async def test_success_exact_byte_limit_and_overflow(self):
        self.assertEqual(await self.read(), "[]")
        self.mode = "exact"
        self.assertEqual(await self.read(), "[]")
        self.mode = "large"
        with self.assertRaises(GovernanceError) as error:
            await self.read()
        self.assertEqual(error.exception.code, ErrorCode.LOGBOOK_RESPONSE_LIMIT_EXCEEDED)
        self.assertEqual(self.calls, 3)

    async def test_acquisition_timeout_does_not_round_up(self):
        from ha_mcp_engineering.clients import rest
        session = rest.aiohttp.ClientSession
        with patch.object(rest.aiohttp, "ClientSession", wraps=session) as factory:
            self.assertEqual(await self.read(), "[]")
        timeout = factory.call_args.kwargs["timeout"]
        self.assertEqual(timeout.total, 30)
        self.assertEqual(timeout.ceil_threshold, float("inf"))

    async def test_disconnect_redirect_encoded_utf8_and_http_have_one_attempt(self):
        for mode in ("disconnect", "redirect", "broken_length", "encoded", "utf8", "error"):
            with self.subTest(mode=mode):
                self.mode = mode
                before = self.calls
                with self.assertRaises(Exception) as error:
                    await self.read()
                self.assertEqual(self.calls, before+1)
                self.assertNotIn("synthetic-secret-body", str(error.exception))

    async def test_received_http_error_precedes_body_processing(self):
        for mode, status in (("oversized_error", 401), ("encoded_error", 500)):
            self.mode = mode
            before = self.calls
            telemetry, token = begin_request("synthetic-http-status")
            try:
                with patch.object(logbook, "read_body", side_effect=AssertionError("error body must not be read")):
                    with self.assertRaises(HomeAssistantApiError) as caught:
                        await self.read()
                self.assertEqual(caught.exception.code, ErrorCode.HA_API_ERROR)
                self.assertEqual(caught.exception.details["status"], status)
                self.assertTrue(caught.exception.details["provider_response_received"])
                self.assertEqual(telemetry.ha_request_count, 1)
                self.assertNotIn("synthetic-secret-body", str(caught.exception))
            finally:
                end_request(token)
            self.assertEqual(self.calls, before+1)

    async def test_timeout_and_cancel_finish_request_accounting(self):
        self.mode = "slow"
        self.client.settings = replace(self.client.settings, ha_timeout_seconds=.03)
        telemetry, token = begin_request("synthetic-logbook-timeout")
        try:
            with self.assertRaises(Exception) as error:
                await self.read()
            self.assertEqual(error.exception.code, ErrorCode.HA_TIMEOUT)
            self.assertEqual(telemetry.ha_request_count, 1)
            self.assertTrue(telemetry.timeout_occurred)
        finally:
            end_request(token)
        self.assertEqual(self.calls, 1)

    async def test_concurrent_heartbeat_and_useful_read(self):
        self.mode = "dense"
        gaps, finished = [], asyncio.Event()
        entered, release = threading.Event(), threading.Event()
        original = logbook.project
        def processing(*args):
            entered.set()
            if not release.wait(3):
                raise AssertionError("synthetic processing barrier timed out")
            return original(*args)
        async def heartbeat():
            previous = time.perf_counter()
            while not finished.is_set():
                await asyncio.sleep(.002)
                now = time.perf_counter()
                gaps.append(now-previous)
                previous = now
        pulse = asyncio.create_task(heartbeat())
        task = None
        try:
            with patch.object(logbook, "project", processing):
                task = asyncio.create_task(self.read())
                while not entered.is_set():
                    await asyncio.sleep(.001)
                useful = await self.client.request("GET", "/logbook/synthetic")
                self.assertEqual(useful, entries())
                self.assertFalse(task.done())
                release.set()
                result = json.loads(await task)
                self.assertTrue(result["truncated"])
        finally:
            release.set()
            finished.set()
            await pulse
            if task is not None and not task.done():
                await task
        self.assertTrue(gaps)
        self.assertLess(max(gaps), .1)

    async def test_authority_refusal_is_before_http(self):
        telemetry, token = begin_request("synthetic-logbook-authority")
        try:
            with patch.object(telemetry, "authorize_core_dispatch", return_value=False):
                with self.assertRaises(Exception):
                    await self.read()
            self.assertEqual(self.calls, 0)
            self.assertEqual(telemetry.ha_request_count, 0)
        finally:
            end_request(token)
