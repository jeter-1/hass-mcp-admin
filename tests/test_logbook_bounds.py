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
from urllib.parse import parse_qs, quote, unquote, urlsplit

from aiohttp import web
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.clients import logbook
from ha_mcp_engineering.clients.rest import HomeAssistantRestClient
from ha_mcp_engineering.clients.paths import rest_path
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
                # HAMCP-200: the start timestamp is one percent-encoded segment.
                start = (NOW-timedelta(hours=hours)).isoformat()
                self.assertEqual(path.path, "/logbook/"+quote(start, safe=""))
                self.assertEqual(unquote(path.path), "/logbook/"+start)
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
                # The detached worker clears the slot from its done callback.
                # Wait for that event under a generous hang guard instead of
                # a fixed wall-clock poll budget.
                async with asyncio.timeout(10):
                    while reader._active:
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
        self.arrived = asyncio.Event()
        app = web.Application()
        self.queries = []
        async def serve(request):
            self.calls += 1
            self.queries.append(parse_qs(request.query_string))
            self.arrived.set()
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
        # Keep a long configured deadline and expire aiohttp's own total
        # timeout timer only once the request has reached the peer. A short
        # real deadline let a descheduled runner time out while connecting,
        # before any request was sent. 20 s is below MAX_SECONDS, so the
        # recorded total also proves the configured value is honored.
        from aiohttp import client as aiohttp_client
        loop = asyncio.get_running_loop()
        deadlines = []

        class DeadlineRecorder(aiohttp_client.TimeoutHandle):
            def start(self):
                handle = super().start()
                deadlines.append((self._timeout, self._ceil_threshold, self, handle))
                return handle

        self.mode = "slow"
        self.client.settings = replace(self.client.settings, ha_timeout_seconds=20)
        telemetry, token = begin_request("synthetic-logbook-timeout")
        try:
            with patch.object(aiohttp_client, "TimeoutHandle", DeadlineRecorder):
                task = asyncio.create_task(self.read())
                async with asyncio.timeout(10):
                    await self.arrived.wait()
                self.assertEqual(self.calls, 1)
                self.assertEqual([entry[:2] for entry in deadlines], [(20, float("inf"))])
                _, _, timer, handle = deadlines[0]
                self.assertFalse(handle.cancelled())
                self.assertGreater(handle.when(), loop.time())
                self.assertFalse(task.done())
                # Fire the same aiohttp deadline callback now.
                handle.cancel()
                loop.call_at(loop.time(), timer)
                with self.assertRaises(Exception) as error:
                    async with asyncio.timeout(10):
                        await task
            self.assertEqual(error.exception.code, ErrorCode.HA_TIMEOUT)
            self.assertEqual(telemetry.ha_request_count, 1)
            self.assertTrue(telemetry.timeout_occurred)
        finally:
            end_request(token)
        self.assertEqual(self.calls, 1)

    async def test_concurrent_heartbeat_and_useful_read(self):
        import gc
        self.mode = "dense"
        gaps, cpu_gaps, finished = [], [], asyncio.Event()
        entered, release = threading.Event(), threading.Event()
        loop_thread, processing_threads = threading.get_ident(), []
        original = logbook.project
        def processing(*args):
            processing_threads.append(threading.get_ident())
            entered.set()
            if not release.wait(3):
                raise AssertionError("synthetic processing barrier timed out")
            return original(*args)
        async def heartbeat():
            previous, previous_cpu = time.perf_counter(), time.thread_time()
            while not finished.is_set():
                await asyncio.sleep(.002)
                now, now_cpu = time.perf_counter(), time.thread_time()
                gaps.append(now-previous)
                cpu_gaps.append(now_cpu-previous_cpu)
                previous, previous_cpu = now, now_cpu
        # Measure event-loop-thread CPU between heartbeats rather than wall
        # gaps, which host scheduling can stretch. Hold collection off only
        # for the timed window so earlier suite state cannot be attributed.
        gc.collect()
        gc.disable()
        pulse = asyncio.create_task(heartbeat())
        task = None
        try:
            with patch.object(logbook, "project", processing):
                task = asyncio.create_task(self.read())
                while not entered.is_set():
                    await asyncio.sleep(.001)
                useful = await self.client.request("GET", rest_path("/logbook/{}", "synthetic"))
                self.assertEqual(useful, entries())
                self.assertFalse(task.done())
                release.set()
                result = json.loads(await task)
                self.assertTrue(result["truncated"])
        finally:
            release.set()
            finished.set()
            try:
                await pulse
                if task is not None and not task.done():
                    await task
            finally:
                gc.enable()
        # Projection ran on a worker thread, never on the event-loop thread,
        # and the loop thread stayed within its CPU budget between heartbeats.
        self.assertEqual(len(processing_threads), 1)
        self.assertNotEqual(processing_threads[0], loop_thread)
        self.assertTrue(cpu_gaps)
        self.assertLess(max(cpu_gaps), .1,
                        f"loop-thread CPU gap {max(cpu_gaps):.4f}s; wall gap {max(gaps):.4f}s (diagnostic)")

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


    async def test_hamcp294_windowed_filter_uses_one_guarded_logbook_get(self):
        start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        result = json.loads(await logbook.LogbookReader().read(
            self.client, hours=None, entity_id="", response_limit=60000,
            start_time=start, entity_ids=["sensor.synthetic_a", "light.synthetic_b"]))
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.queries[0]["entity"], ["sensor.synthetic_a,light.synthetic_b"])
        self.assertEqual(result["entries"], [])
        self.assertFalse(result["truncated"])

    async def test_hamcp294_guard_still_refuses_non_logbook_shapes(self):
        start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        for method, path, kwargs in (
                ("GET", rest_path("/states"), {"raw": True}),
                ("GET", rest_path("/logbook/{}", start, kinds=("timestamp",)), {"raw": False}),
                ("POST", rest_path("/logbook/{}", start, kinds=("timestamp",)), {"raw": True})):
            with self.subTest(method=method, path=path), self.assertRaises(ValueError):
                await self.client.request(method, path, logbook_read=True, **kwargs)
        self.assertEqual(self.calls, 0)


# HAMCP-294: bounded window, entity/name filter and continuation.
class Clock(datetime):
    """A fixed ``now``; ``fromisoformat`` and arithmetic stay real."""

    @classmethod
    def now(cls, tz=None):
        return NOW


def at(**delta):
    return (NOW - timedelta(**delta)).isoformat()


def none_filter():
    return {"kind": "none", "entity_ids": [], "name": None, "match_basis": None, "matched_count": None}


def window_context(start="2026-09-29T00:00:00Z", end="2026-09-29T12:00:00Z"):
    window = logbook.resolve_window(NOW, None, start, end)
    return {"window": window, "requested_interval": window["requested_interval"],
            "effective_interval": window["effective_interval"], "filter": none_filter(),
            "completeness_notes": list(logbook.BASE_NOTES)}


def state(entity_id, **attributes):
    return {"entity_id": entity_id, "state": "on", "attributes": attributes}


class StatesClient:
    """States inventory for non-logbook GETs; a raw logbook body otherwise."""

    def __init__(self, states=(), value=None):
        self.states = list(states) if isinstance(states, (list, tuple)) else states
        self.value = [] if value is None else value
        self.calls = []

    async def request(self, method, path, body=None, raw=False, expected_statuses=frozenset(), *, logbook_read=False):
        self.calls.append((method, path, raw, logbook_read))
        if logbook_read:
            return json.dumps(self.value)
        return self.states


class WindowRefusalTests(unittest.IsolatedAsyncioTestCase):
    async def refuse(self, **kwargs):
        client, reader = StatesClient([state("light.synthetic")]), logbook.LogbookReader()
        arguments = {"hours": None, "entity_id": "", "response_limit": 60000, **kwargs}
        with patch.object(logbook, "datetime", Clock), self.assertRaises(InvalidRequestError):
            await reader.read(client, **arguments)
        self.assertEqual(client.calls, [])
        self.assertFalse(reader._active)

    async def test_timestamps_are_strict_aware_and_ascii(self):
        for value in ("2026-09-29T10:00:00", "2026-09-29", "2026-09-29T10:00Z", "yesterday", "",
                      "2026-09-29T10:00:00+0000", "2026-13-29T10:00:00Z", "2026-09-29T25:00:00Z",
                      "２０２６-09-29T10:00:00Z", "2026-09-29T10:00:00.1234567Z",
                      "2026-09-29T10:00:00Z ", 1759140000, ["2026-09-29T10:00:00Z"]):
            for field in ("start_time", "end_time"):
                with self.subTest(field=field, value=value):
                    await self.refuse(**{field: value})

    async def test_interval_order_span_and_future_start(self):
        cases = (
            {"start_time": at(hours=1), "end_time": at(hours=1)},
            {"start_time": at(hours=1), "end_time": at(hours=2)},
            {"start_time": at(hours=168, microseconds=1), "end_time": NOW.isoformat()},
            {"start_time": at(hours=200)},
            {"start_time": NOW.isoformat()},
            {"start_time": (NOW + timedelta(hours=1)).isoformat(), "end_time": (NOW + timedelta(hours=2)).isoformat()},
            {"hours": 12, "start_time": at(hours=1)},
            {"hours": 0, "end_time": at(hours=1)},
            {"hours": 169, "end_time": at(hours=1)},
            {"hours": 10**1000, "end_time": at(hours=1)},
            {"hours": float("nan"), "entity_ids": ["light.synthetic"]},
            {"hours": True, "entity_ids": ["light.synthetic"]},
            {"hours": "12", "entity_ids": ["light.synthetic"]},
            {"end_time": "0001-01-01T00:00:00+00:00"},
        )
        for case in cases:
            with self.subTest(case=case):
                await self.refuse(**case)

    async def test_exactly_168_hours_and_now_end_are_allowed(self):
        client = StatesClient()
        with patch.object(logbook, "datetime", Clock):
            result = json.loads(await logbook.LogbookReader().read(
                client, hours=None, entity_id="", response_limit=60000,
                start_time=at(hours=168), end_time=NOW.isoformat()))
        self.assertFalse(result["effective_interval"]["end_clamped"])
        self.assertEqual(len(client.calls), 1)

    async def test_one_filter_and_filter_bounds(self):
        long_ids = [f"sensor.{c}" + "x"*200 for c in "abc"]
        cases = (
            {"entity_id": "light.synthetic", "entity_ids": ["light.other"]},
            {"entity_id": "light.synthetic", "name": "kitchen"},
            {"entity_ids": ["light.synthetic"], "name": "kitchen"},
            {"entity_ids": []},
            {"entity_ids": [f"light.synthetic_{i}" for i in range(11)]},
            {"entity_ids": ["light.synthetic", "light.synthetic"]},
            {"entity_ids": ["light.synthetic", "light.x&end_time=secret"]},
            {"entity_ids": ["a.b,c.d"]},
            {"entity_ids": ["light.雪"]},
            {"entity_ids": [None]},
            {"entity_ids": "light.synthetic"},
            {"entity_ids": {"light.synthetic": 1}},
            {"entity_ids": long_ids},
            {"name": ""},
            {"name": "   "},
            {"name": "x"*101},
            {"name": 123},
            {"name": ["kitchen"]},
            {"entity_id": None, "start_time": at(hours=1)},
            {"entity_id": "a.b,c.d", "start_time": at(hours=1)},
        )
        for case in cases:
            with self.subTest(case=case):
                await self.refuse(**case)

    async def test_ten_ids_and_512_joined_characters_are_allowed(self):
        ids = [f"sensor.{chr(97+i)}" + "x"*(42 + 3*(i == 9)) for i in range(10)]
        self.assertEqual(len(",".join(ids)), 512)
        self.assertEqual(logbook.resolve_filter("", ids, None)["entity_ids"], ids)
        self.assertEqual(logbook.resolve_filter("", None, "x"*100)["kind"], "name")
        for over in (ids[:-1] + [ids[-1] + "x"], ids + ["sensor.k"]):
            with self.assertRaises(InvalidRequestError):
                logbook.resolve_filter("", over, None)


class WindowReadTests(unittest.IsolatedAsyncioTestCase):
    async def read(self, client, **kwargs):
        arguments = {"hours": None, "entity_id": "", "response_limit": 60000, **kwargs}
        reader = logbook.LogbookReader()
        with patch.object(logbook, "datetime", Clock):
            raw = await reader.read(client, **arguments)
        self.assertFalse(reader._active)
        return raw

    def logbook_call(self, client):
        method, path, raw, logbook_read = client.calls[-1]
        self.assertEqual((method, raw, logbook_read), ("GET", True, True))
        split = urlsplit(path)
        return unquote(split.path), parse_qs(split.query)

    async def test_windowed_entity_ids_exact_request_and_object(self):
        client = StatesClient(value=entries(2))
        start = "2026-09-29T08:30:00+02:00"
        result = json.loads(await self.read(client, start_time=start,
                                            entity_ids=["sensor.synthetic_0", "light.synthetic_1"]))
        self.assertEqual(len(client.calls), 1)
        path, query = self.logbook_call(client)
        self.assertEqual(path, "/logbook/2026-09-29T06:30:00+00:00")
        self.assertEqual(query, {"end_time": [NOW.isoformat()],
                                 "entity": ["sensor.synthetic_0,light.synthetic_1"]})
        self.assertEqual(result, {
            "entries": entries(2), "truncated": False, "returned": 2, "omitted": 0,
            "requested_interval": {"start": "2026-09-29T06:30:00+00:00", "end": NOW.isoformat()},
            "effective_interval": {"start": "2026-09-29T06:30:00+00:00", "end": NOW.isoformat(),
                                   "end_clamped": False},
            "interval_bounds": "exclusive", "ordering": "source_order",
            "filter": {"kind": "entity_ids", "entity_ids": ["sensor.synthetic_0", "light.synthetic_1"],
                       "name": None, "match_basis": None, "matched_count": None},
            "continuation": None, "completeness_notes": list(logbook.BASE_NOTES),
            "required_action": None})

    async def test_default_hours_end_time_only_and_unfiltered(self):
        client = StatesClient()
        result = json.loads(await self.read(client, end_time=at(hours=1)))
        path, query = self.logbook_call(client)
        self.assertEqual(path, "/logbook/" + at(hours=13))
        self.assertEqual(query, {"end_time": [at(hours=1)]})
        self.assertEqual(result["filter"]["kind"], "none")
        client = StatesClient()
        result = json.loads(await self.read(client, hours=24, entity_id="light.synthetic", end_time=at(hours=1)))
        path, query = self.logbook_call(client)
        self.assertEqual(path, "/logbook/" + at(hours=25))
        self.assertEqual(query["entity"], ["light.synthetic"])
        self.assertEqual(result["filter"]["kind"], "entity_id")

    async def test_future_end_is_clamped_and_reported(self):
        client = StatesClient()
        future = (NOW + timedelta(hours=3)).isoformat()
        result = json.loads(await self.read(client, start_time=at(hours=2), end_time=future))
        _, query = self.logbook_call(client)
        self.assertEqual(query["end_time"], [NOW.isoformat()])
        self.assertEqual(result["requested_interval"], {"start": at(hours=2), "end": future})
        self.assertEqual(result["effective_interval"], {"start": at(hours=2), "end": NOW.isoformat(),
                                                        "end_clamped": True})

    async def test_continuous_entities_are_disclosed(self):
        for kwargs, states in (
                ({"entity_ids": ["counter.synthetic"]}, ()),
                ({"name": "power"}, [state("sensor.synthetic_power", unit_of_measurement="W")]),
                ({"name": "energy"}, [state("sensor.synthetic_energy", state_class="total")])):
            with self.subTest(kwargs=kwargs):
                result = json.loads(await self.read(StatesClient(states), **kwargs))
                self.assertIn("continuous_entities_produce_no_rows", result["completeness_notes"])
        result = json.loads(await self.read(StatesClient(), entity_ids=["light.synthetic"]))
        self.assertEqual(result["completeness_notes"], list(logbook.BASE_NOTES))

    async def test_name_matches_casefold_id_or_friendly_name_sorted(self):
        states = [state("light.synthetic_b", friendly_name="Kitchen Lamp"),
                  state("switch.kitchen_fan"), state("light.synthetic_a", friendly_name="KITCHEN ceiling"),
                  state("light.hall", friendly_name="Hall"), "not-a-dict", {"entity_id": "Bad.ID"},
                  {"entity_id": "light.kitchen_odd", "attributes": "malformed"}, {"attributes": {}}]
        client = StatesClient(states, entries(1))
        result = json.loads(await self.read(client, name=" kitchen "))
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0][:2], ("GET", rest_path("/states")))
        self.assertEqual(client.calls[0][3], False)
        ids = ["light.kitchen_odd", "light.synthetic_a", "light.synthetic_b", "switch.kitchen_fan"]
        _, query = self.logbook_call(client)
        self.assertEqual(query["entity"], [",".join(ids)])
        self.assertEqual(result["filter"], {"kind": "name", "entity_ids": ids, "name": " kitchen ",
                                            "match_basis": logbook.NAME_MATCH_BASIS, "matched_count": 4})
        self.assertIn("name_resolved_from_current_states", result["completeness_notes"])

    async def test_exactly_ten_matches_send_one_logbook_get(self):
        states = [state(f"light.synthetic_{i}") for i in range(10)]
        client = StatesClient(states)
        result = json.loads(await self.read(client, name="synthetic"))
        self.assertEqual(len(client.calls), 2)
        _, query = self.logbook_call(client)
        self.assertEqual(query["entity"][0].split(","), sorted(s["entity_id"] for s in states))
        self.assertEqual(result["filter"]["matched_count"], 10)

    async def assert_name_refusal(self, states, reason, count):
        client, reader = StatesClient(states), logbook.LogbookReader()
        with patch.object(logbook, "datetime", Clock), self.assertRaises(InvalidRequestError) as error:
            await reader.read(client, hours=None, entity_id="", response_limit=60000, name="synthetic")
        self.assertEqual(len(client.calls), 1)  # The states read only; no logbook GET.
        self.assertFalse(client.calls[0][3])
        self.assertFalse(reader._active)
        self.assertEqual(error.exception.details["reason"], reason)
        self.assertEqual(error.exception.details["match_count"], count)
        return error.exception.details

    async def test_zero_too_many_and_over_bound_matches_refuse_before_logbook(self):
        await self.assert_name_refusal([state("light.other")], "name_matched_no_entity", 0)
        await self.assert_name_refusal([], "name_matched_no_entity", 0)
        states = [state(f"light.synthetic_{i:02}") for i in range(11)]
        details = await self.assert_name_refusal(states, "name_matched_too_many", 11)
        self.assertEqual(details["matched_entity_ids"], sorted(s["entity_id"] for s in states)[:10])
        states = [state(f"sensor.synthetic_{c}" + "x"*200) for c in "abc"]
        details = await self.assert_name_refusal(states, "name_matches_exceed_filter_bound", 3)
        self.assertEqual(len(details["matched_entity_ids"]), 3)

    async def test_malformed_states_body_fails_closed(self):
        for body in ({"entity_id": "light.synthetic"}, None, "[]", 7):
            client, reader = StatesClient(body), logbook.LogbookReader()
            with self.subTest(body=body), patch.object(logbook, "datetime", Clock), \
                    self.assertRaises(HomeAssistantApiError) as error:
                await reader.read(client, hours=None, entity_id="", response_limit=60000, name="synthetic")
            self.assertEqual(error.exception.details["endpoint_category"], "states_inventory")
            self.assertEqual(len(client.calls), 1)
            self.assertFalse(reader._active)

    async def test_secret_in_name_is_redacted_not_echoed(self):
        states = [state("light.synthetic", friendly_name="synthetic-password lamp")]
        client = StatesClient(states)
        with patch.object(logbook, "datetime", Clock):
            raw = await logbook.LogbookReader().read(
                client, hours=None, entity_id="", response_limit=60000,
                name="synthetic-password", secrets=("synthetic-password",))
        self.assertNotIn("synthetic-password", raw)
        self.assertEqual(json.loads(raw)["filter"]["entity_ids"], ["light.synthetic"])

    async def test_states_failure_releases_slot(self):
        reader = logbook.LogbookReader()
        client = StatesClient()
        async def failing(*args, **kwargs):
            raise HomeAssistantApiError(details={"status": 500})
        with patch.object(client, "request", failing), patch.object(logbook, "datetime", Clock), \
                self.assertRaises(HomeAssistantApiError):
            await reader.read(client, hours=None, entity_id="", response_limit=60000, name="synthetic")
        self.assertFalse(reader._active)

    async def test_legacy_shape_unchanged_without_new_arguments(self):
        client = FakeClient(entries())
        with patch.object(logbook, "datetime", Clock):
            raw = await logbook.LogbookReader().read(client, hours=12, entity_id="", response_limit=60000)
        self.assertEqual(raw, json.dumps(entries(), separators=(",", ":")))


class WindowProjectionTests(unittest.TestCase):
    def records(self, count, size, offsets):
        return [{"entity_id": f"sensor.synthetic_{i}", "when": at(minutes=offsets(i)), "message": "x"*size}
                for i in range(count)]

    def test_continuation_starts_before_earliest_omitted_unordered(self):
        records = self.records(300, 2048, lambda i: (i*37) % 600 + 1)
        result = json.loads(logbook.project_window(json.dumps(records), window_context(), 60000, ()))
        returned = result["returned"]
        self.assertTrue(result["truncated"])
        self.assertGreater(returned, 0)
        self.assertEqual(result["entries"], records[:returned])
        self.assertEqual(returned + result["omitted"], 300)
        earliest = min(datetime.fromisoformat(r["when"]) for r in records[returned:])
        self.assertLess(earliest, min(datetime.fromisoformat(r["when"]) for r in records[:returned]))
        self.assertEqual(result["continuation"], {
            "start_time": (earliest - timedelta(microseconds=1)).isoformat(),
            "end_time": NOW.isoformat(), "overlap_possible": True, "reason": None})
        self.assertEqual(result["required_action"], logbook.NARROW_WINDOW)
        self.assertLessEqual(len(json.dumps(result, separators=(",", ":"))), logbook.logbook_data_limit(60000))

    def test_offset_when_values_are_normalized(self):
        records = self.records(300, 2048, lambda i: i + 1)
        for record in records:
            record["when"] = datetime.fromisoformat(record["when"]).astimezone(
                timezone(timedelta(hours=-5))).isoformat()
        result = json.loads(logbook.project_window(json.dumps(records), window_context(), 60000, ()))
        earliest = datetime.fromisoformat(records[-1]["when"]).astimezone(timezone.utc)
        self.assertEqual(result["continuation"]["start_time"],
                         (earliest - timedelta(microseconds=1)).isoformat())

    def test_unusable_or_outside_when_has_null_continuation_with_reason(self):
        for mutate, reason in (
                (lambda r: r.pop("when"), "omitted_when_unavailable"),
                (lambda r: r.update(when="2026-09-29T10:00:00"), "omitted_when_unavailable"),
                (lambda r: r.update(when="not-a-time"), "omitted_when_unavailable"),
                (lambda r: r.update(when=1759140000), "omitted_when_unavailable"),
                (lambda r: r.update(when="2026-09-29T10:00:00+00:00" + " "*100), "omitted_when_unavailable"),
                (lambda r: r.update(when="2026-09-20T10:00:00+00:00"), "omitted_when_outside_interval"),
                (lambda r: r.update(when="2026-09-29T00:00:00+00:00"), "omitted_when_outside_interval")):
            records = self.records(300, 2048, lambda i: i + 1)
            mutate(records[-1])
            with self.subTest(reason=reason, when=records[-1].get("when")):
                result = json.loads(logbook.project_window(json.dumps(records), window_context(), 60000, ()))
                self.assertTrue(result["truncated"])
                self.assertEqual(result["continuation"], {"start_time": None, "end_time": None,
                                                          "overlap_possible": True, "reason": reason})

    def test_complete_empty_limit_and_malformed(self):
        for records in ([], entries(3)):
            result = json.loads(logbook.project_window(json.dumps(records), window_context(), 2048, ()))
            self.assertEqual((result["entries"], result["truncated"], result["continuation"],
                              result["required_action"]), (records, False, None, None))
        # At a 1024-byte response limit the window object's metadata leaves
        # room for an empty result only: a record fails closed, never an
        # empty success.
        self.assertEqual(json.loads(logbook.project_window("[]", window_context(), 1024, ()))["entries"], [])
        for records, limit in ((entries(1, 100000), 60000), (entries(20, 1500), 2048),
                               (entries(1), 1024)):
            with self.assertRaises(GovernanceError) as error:
                logbook.project_window(json.dumps(records), window_context(), limit, ())
            self.assertEqual(error.exception.details["reason"], "response_bytes")
        for raw in ('{}', '[0]', '[{"x":NaN}]', '[{"x":1,"x":2}]'):
            with self.subTest(raw=raw), self.assertRaises(HomeAssistantApiError):
                logbook.project_window(raw, window_context(), 60000, ())

    def test_sizing_is_exact_at_small_budgets(self):
        records = entries(40, 20)
        for limit in (1500, 2048, 4096):
            raw = logbook.project_window(json.dumps(records), window_context(), limit, ())
            self.assertLessEqual(len(raw), logbook.logbook_data_limit(limit))
            result = json.loads(raw)
            self.assertTrue(result["truncated"])
            # One more whole record would not have fit.
            bigger = dict(result, entries=records[:result["returned"]+1])
            self.assertGreater(len(json.dumps(bigger, separators=(",", ":"))), logbook.logbook_data_limit(limit) - 4)

    def test_secret_suppression_in_window_entries(self):
        value = [{"message": "token=synthetic-token", "password": "synthetic-password"}]
        raw = logbook.project_window(json.dumps(value), window_context(), 60000, ("synthetic-password",))
        for secret in ("synthetic-token", "synthetic-password"):
            self.assertNotIn(secret, raw)


class WindowRegisteredTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, client, **kwargs):
        with patch.object(compatibility, "REST_CLIENT", client), \
                patch.object(compatibility, "LOGBOOK", logbook.LogbookReader()):
            return json.loads(await compatibility.get_logbook(**kwargs))

    async def test_registered_windowed_completeness_and_object(self):
        start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        for records, completeness in ((entries(2), "complete"), (entries(300, 2048), "partial")):
            result = await self.call(StatesClient(value=records), start_time=start,
                                     entity_ids=["sensor.synthetic_0"])
            with self.subTest(completeness=completeness):
                self.assertTrue(result["success"])
                coverage = result["metadata"]["source_coverage"][0]
                self.assertEqual(coverage["provider"], "direct_ha_api")
                self.assertFalse(coverage["fallback_occurred"])
                self.assertEqual(coverage["completeness"], completeness)
                self.assertEqual(result["data"]["entries"], records[:result["data"]["returned"]])
                self.assertEqual(result["data"]["filter"]["kind"], "entity_ids")

    async def test_registered_name_mode_and_refusal_envelopes(self):
        client = StatesClient([state("light.synthetic", friendly_name="Porch")], entries(1))
        result = await self.call(client, name="porch")
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["filter"]["entity_ids"], ["light.synthetic"])
        client = StatesClient([state("light.synthetic")])
        result = await self.call(client, name="porch")
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], ErrorCode.INVALID_REQUEST.value)
        self.assertIn("name_matched_no_entity", json.dumps(result))
        self.assertEqual(len(client.calls), 1)
        start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        for kwargs in ({"hours": 12, "start_time": start}, {"entity_id": "light.a", "name": "porch"},
                       {"start_time": "2026-09-29T10:00:00"}):
            client = StatesClient()
            result = await self.call(client, **kwargs)
            with self.subTest(kwargs=kwargs):
                self.assertFalse(result["success"])
                self.assertEqual(result["error_code"], ErrorCode.INVALID_REQUEST.value)
                self.assertEqual(client.calls, [])

    async def test_registered_default_call_keeps_legacy_list(self):
        result = await self.call(FakeClient(entries()))
        self.assertTrue(result["success"])
        self.assertEqual(result["data"], entries())
