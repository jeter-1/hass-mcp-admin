"""HAMCP-431 / HAMCP-524 / HAMCP-389: the trace-watch poller's held Core
connection, the runtime-only skip reasons and recent counts, and failure
snapshot protection with the churn warning.

Offline: a loopback aiohttp WebSocket peer that accepts only a synthetic
token, and the HAMCP-225 harness (fake Core, audit, runtime and clock).
Decisions: release 2.11.0 Lane C GO, 2026-11-27.
"""

from __future__ import annotations

import ast
import asyncio
import contextvars
import dataclasses
import json
from pathlib import Path
import socket
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))

from tests.test_hamcp225_trace_snapshots import (  # noqa: E402
    KNOWN, PACKAGE, VERSION, FakeAudit, FakeCore, FakeRuntime, ServiceCase, all_store_bytes,
)
from ha_mcp_engineering.clients import websocket as websocket_module  # noqa: E402
from ha_mcp_engineering.clients.websocket import HeldWebSocketSession  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    AuthorizationError, ErrorCode, HomeAssistantApiError, HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.trace_snapshots import contracts as c  # noqa: E402
from ha_mcp_engineering.trace_snapshots.poller import TraceWatchPoller  # noqa: E402
from ha_mcp_engineering.trace_snapshots.runtime import TraceSnapshotRuntime  # noqa: E402
from ha_mcp_engineering.trace_snapshots.service import TraceSnapshotService  # noqa: E402


AUTHORITY = (7, VERSION)
TOKEN = "synthetic-no-real-token"
LIST = {"type": "trace/list", "domain": "automation", "item_id": "a1"}
# The stored watch row, unchanged by HAMCP-524 and HAMCP-389.
STORED_WATCH_KEYS = {
    "automation_id", "baseline_done", "caller_id", "captured_count", "created_at_ms",
    "evicted_count", "expires_at_ms", "last_poll_at_ms", "mode", "request_id",
    "seen_run_ids", "skipped_polls", "uncaptured_runs", "watch_id",
}
RUNTIME_ONLY = (b"recent_skip", b"last_skip", b"recent_evicted", b"churning",
                *(reason.encode() for reason in c.SKIP_REASONS))


def result(frame, value=None):
    return {"id": frame["id"], "type": "result", "success": True,
            "result": [] if value is None else value}


def failure(frame, code):
    return {"id": frame["id"], "type": "result", "success": False,
            "error": {"code": code, "message": "synthetic"}}


async def until(condition, seconds=2.0):
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


class Peer:
    """A loopback Core WebSocket API with scripted replies.

    ``script(frame)`` returns the replies to one command frame: dicts are
    sent as JSON, other strings as raw text, ``"close"`` closes the
    connection and ``"hang"`` waits for ``release``. ``None`` answers with a
    success result.
    """

    def __init__(self):
        self.mode = "ok"
        self.heartbeat = None
        self.script = None
        self.connections = 0
        self.tokens = []
        self.frames = []
        self.release = asyncio.Event()

    async def handler(self, request):
        if self.mode == "reject":
            return web.Response(status=401)
        ws = web.WebSocketResponse(heartbeat=self.heartbeat)
        await ws.prepare(request)
        self.connections += 1
        number = self.connections
        try:
            if self.mode == "no_required":
                await ws.send_json({"type": "auth_ok", "ha_version": VERSION})
                async for _ in ws:
                    pass
                return ws
            await ws.send_json({"type": "auth_required", "ha_version": VERSION})
            auth = await ws.receive_json()
            self.tokens.append(auth.get("access_token"))
            if self.mode == "invalid" or auth.get("access_token") != TOKEN:
                await ws.send_json({"type": "auth_invalid", "message": "synthetic"})
                await ws.close()
                return ws
            await ws.send_json({"type": "auth_ok", "ha_version": VERSION})
            async for message in ws:
                if message.type is not aiohttp.WSMsgType.TEXT:
                    break
                frame = json.loads(message.data)
                self.frames.append((number, frame))
                replies = self.script(frame) if self.script is not None else None
                for reply in [result(frame)] if replies is None else replies:
                    if reply == "close":
                        await ws.close()
                        return ws
                    if reply == "hang":
                        await self.release.wait()
                    elif isinstance(reply, str):
                        await ws.send_str(reply)
                    else:
                        await ws.send_json(reply)
        except (ConnectionError, RuntimeError):
            pass
        return ws


def settings_for(url, **changes):
    settings = Settings(ha_url=url, ha_token=TOKEN, access_secret="synthetic-no-real-secret",
                        port=8100, audit_path="unused-synthetic", rate_limit_per_minute=120,
                        rate_limit_burst=20, destructive_services=frozenset(),
                        ha_timeout_seconds=1.0)
    return dataclasses.replace(settings, **changes) if changes else settings


class PeerCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.peer = Peer()
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", self.peer.handler)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self.settings = settings_for(f"http://127.0.0.1:{self.port}")
        self.now = [0.0]
        self.sessions = []
        self.telemetry, self.token = begin_request()
        self.allowed = [True]
        self.telemetry.core_dispatch_authorizer = lambda: self.allowed[0]

    def session(self, settings=None):
        session = HeldWebSocketSession(settings or self.settings, clock=lambda: self.now[0])
        self.sessions.append(session)
        return session

    async def asyncTearDown(self):
        self.peer.release.set()
        for session in self.sessions:
            await session.close("shutdown")
        end_request(self.token)
        await self.runner.cleanup()


class HeldSessionTests(PeerCase):
    async def test_frames_reuse_one_authenticated_connection(self):
        session = self.session()
        self.assertEqual(await session.command(LIST), [])
        self.assertEqual(await session.command(LIST), [])
        self.assertEqual((self.peer.connections, session.opened, session.frames), (1, 1, 2))
        self.assertEqual(self.peer.tokens, [TOKEN])
        self.assertEqual([frame["id"] for _, frame in self.peer.frames], [1, 2])
        self.assertEqual({number for number, _ in self.peer.frames}, {1})
        self.assertEqual(self.telemetry.ha_request_count, 2)
        self.assertEqual(self.telemetry.ha_active_requests, 0)
        self.assertEqual(session.health(), {"open": True, "opened": 1, "frames": 2,
                                            "last_close_reason": None})

    async def test_refused_gate_writes_nothing_and_opens_nothing(self):
        for name, authorizer in (("false", lambda: False), ("none", None),
                                 ("raises", lambda: 1 / 0), ("truthy", lambda: "yes")):
            with self.subTest(authorizer=name):
                session = self.session()
                self.telemetry.core_dispatch_authorizer = authorizer
                self.telemetry.error_code = None
                with self.assertRaises(HomeAssistantUnavailableError):
                    await session.command(LIST)
                self.assertEqual(self.telemetry.error_code, ErrorCode.PROVIDER_UNAVAILABLE.value)
                self.assertEqual((self.peer.connections, session.opened, self.peer.frames),
                                 (0, 0, []))
                self.assertIsNone(session.last_close_reason)

    async def test_no_request_context_refuses_without_the_no_authorizer_default(self):
        session = self.session()
        task = asyncio.get_running_loop().create_task(session.command(LIST),
                                                      context=contextvars.Context())
        with self.assertRaises(HomeAssistantUnavailableError):
            await task
        self.assertEqual((self.peer.connections, self.peer.frames), (0, []))

    async def test_refusal_on_an_open_connection_closes_it_before_writing(self):
        session = self.session()
        await session.command(LIST)
        self.allowed[0] = False
        with self.assertRaises(HomeAssistantUnavailableError):
            await session.command(LIST)
        self.assertEqual(len(self.peer.frames), 1)
        self.assertFalse(session.is_open)
        self.assertEqual(session.last_close_reason, "authority_refused")

    async def test_new_connection_is_authorized_again_before_its_first_frame(self):
        session = self.session()
        answers = iter((True, False))
        self.telemetry.core_dispatch_authorizer = lambda: next(answers)
        with self.assertRaises(HomeAssistantUnavailableError):
            await session.command(LIST)
        self.assertEqual((self.peer.connections, self.peer.frames), (1, []))
        self.assertEqual(session.last_close_reason, "authority_refused")
        self.assertFalse(session.is_open)

    async def test_max_age_closes_before_the_next_frame(self):
        self.assertEqual(websocket_module.HELD_SESSION_MAX_AGE_SECONDS, 600.0)
        session = self.session()
        await session.command(LIST)
        self.now[0] = 599.0
        await session.command(LIST)
        self.assertEqual(session.opened, 1)
        self.now[0] = 600.0
        await session.command(LIST)
        self.assertEqual((session.opened, self.peer.connections), (2, 2))
        self.assertEqual(session.last_close_reason, "max_age")
        self.assertEqual([number for number, _ in self.peer.frames], [1, 1, 2])

    async def test_generation_change_closes_the_connection(self):
        session = self.session()
        session.bind(7)
        await session.command(LIST)
        session.bind(7)
        self.assertTrue(session.is_open)
        session.bind(8)
        self.assertFalse(session.is_open)
        self.assertEqual(session.last_close_reason, "generation_changed")
        await session.command(LIST)
        self.assertEqual(self.peer.connections, 2)

    async def test_authorization_failure_result_closes_and_reopens(self):
        session = self.session()
        for code in ("unauthorized", "forbidden"):
            with self.subTest(code=code):
                self.peer.script = lambda frame, code=code: [failure(frame, code)]
                with self.assertRaises(AuthorizationError) as raised:
                    await session.command(LIST)
                self.assertFalse(raised.exception.call_not_sent)
                self.assertTrue(raised.exception.details["provider_response_received"])
                self.assertFalse(session.is_open)
                self.assertEqual(session.last_close_reason, "authorization_failure")
        self.peer.script = None
        await session.command(LIST)
        self.assertEqual(session.opened, 3)

    async def test_other_error_results_keep_the_connection(self):
        session = self.session()
        self.peer.script = lambda frame: [failure(frame, "not_found")]
        with self.assertRaises(HomeAssistantApiError) as raised:
            await session.command(LIST)
        self.assertEqual(raised.exception.details["status"], 404)
        self.peer.script = lambda frame: [failure(frame, "home_assistant_error")]
        with self.assertRaises(HomeAssistantApiError) as raised:
            await session.command(LIST)
        self.assertNotIn("status", raised.exception.details)
        self.peer.script = None
        await session.command(LIST)
        self.assertEqual((session.opened, self.peer.connections), (1, 1))
        self.assertIsNone(session.last_close_reason)

    async def test_timeout_closes_the_connection(self):
        session = self.session(settings_for(f"http://127.0.0.1:{self.port}",
                                            ha_timeout_seconds=0.5))
        self.peer.script = lambda frame: ["hang"]
        with self.assertRaises(HomeAssistantTimeoutError):
            await session.command(LIST)
        self.assertTrue(self.telemetry.timeout_occurred)
        self.assertFalse(session.is_open)
        self.assertEqual(session.last_close_reason, "timeout")
        self.peer.release.set()
        self.peer.script = None
        await session.command(LIST)
        self.assertEqual(session.opened, 2)

    async def test_cancellation_closes_the_connection(self):
        session = self.session()
        self.peer.script = lambda frame: ["hang"]
        task = asyncio.get_running_loop().create_task(session.command(LIST))
        await until(lambda: self.peer.frames)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(session.is_open)
        self.assertEqual(session.last_close_reason, "cancelled")

    async def test_malformed_frames_close_as_protocol_errors(self):
        replies = {
            "wrong_id": lambda frame: [{**result(frame), "id": frame["id"] + 100}],
            "text_id": lambda frame: [{**result(frame), "id": str(frame["id"])}],
            "not_json": lambda frame: ["{not json"],
            "not_object": lambda frame: ["[1]"],
            "unsolicited_bound": lambda frame: (
                [{"type": "event", "event": {}}] * (websocket_module.HELD_SESSION_UNSOLICITED_FRAMES
                                                    + 1) + [result(frame)]),
        }
        for name, script in replies.items():
            with self.subTest(reply=name):
                session = self.session()
                self.peer.script = script
                with self.assertRaises(HomeAssistantApiError) as raised:
                    await session.command(LIST)
                self.assertEqual(raised.exception.details["reason"], "malformed_response")
                self.assertTrue(raised.exception.details["provider_response_received"])
                self.assertFalse(session.is_open)
                self.assertEqual(session.last_close_reason, "protocol_error")

    async def test_unsolicited_frames_within_the_bound_are_ignored(self):
        session = self.session()
        bound = websocket_module.HELD_SESSION_UNSOLICITED_FRAMES
        self.peer.script = lambda frame: [{"type": "event", "event": {}}] * bound + [
            result(frame, [{"run_id": "r1"}])]
        self.assertEqual(await session.command(LIST), [{"run_id": "r1"}])
        # The count starts again with every frame.
        self.assertEqual(await session.command(LIST), [{"run_id": "r1"}])
        self.assertEqual(session.opened, 1)
        self.assertTrue(session.is_open)

    async def test_handshake_failures_write_no_frame(self):
        cases = (("invalid", AuthorizationError, "authorization_failure"),
                 ("no_required", HomeAssistantApiError, "protocol_error"))
        for mode, error, reason in cases:
            with self.subTest(mode=mode):
                session = self.session()
                self.peer.mode = mode
                with self.assertRaises(error) as raised:
                    await session.command(LIST)
                if error is AuthorizationError:
                    self.assertTrue(raised.exception.call_not_sent)
                self.assertEqual(self.peer.frames, [])
                self.assertFalse(session.is_open)
                self.assertEqual(session.last_close_reason, reason)
        self.assertEqual(self.peer.tokens, [TOKEN])

    async def test_rejected_upgrade_and_refused_connection(self):
        session = self.session()
        self.peer.mode = "reject"
        with self.assertRaises(AuthorizationError) as raised:
            await session.command(LIST)
        self.assertTrue(raised.exception.call_not_sent)
        self.assertEqual(raised.exception.details["status"], 401)
        self.assertEqual(session.last_close_reason, "transport_error")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            closed_port = probe.getsockname()[1]
        session = self.session(settings_for(f"http://127.0.0.1:{closed_port}"))
        with self.assertRaises(HomeAssistantUnavailableError):
            await session.command(LIST)
        self.assertEqual(session.last_close_reason, "transport_error")
        self.assertFalse(session.is_open)

    async def test_core_closing_the_connection(self):
        session = self.session()
        self.peer.script = lambda frame: [result(frame), "close"]
        await session.command(LIST)
        await until(lambda: not session.is_open)
        self.assertEqual(session.last_close_reason, "connection_closed")
        self.peer.script = lambda frame: ["close"]
        with self.assertRaises(HomeAssistantUnavailableError):
            await session.command(LIST)
        self.assertEqual(session.last_close_reason, "connection_closed")
        self.peer.script = None
        await session.command(LIST)
        self.assertEqual(session.opened, 3)

    async def test_idle_connection_answers_core_heartbeats(self):
        # Core's own heartbeat interval is not asserted here; the peer pings
        # every second and drops a connection that misses a pong.
        self.peer.heartbeat = 1.0
        session = self.session()
        await session.command(LIST)
        await asyncio.sleep(2.5)
        self.assertTrue(session.is_open)
        await session.command(LIST)
        self.assertEqual((session.opened, self.peer.connections), (1, 1))

    async def test_close_and_discard_record_the_first_reason_only(self):
        session = self.session()
        await session.command(LIST)
        await session.close("shutdown")
        self.assertEqual(session.health(), {"open": False, "opened": 1, "frames": 1,
                                            "last_close_reason": "shutdown"})
        session.discard("no_active_watch")
        await session.close("shutdown")
        self.assertEqual(session.last_close_reason, "shutdown")
        await session.command(LIST)
        session.discard("not-a-reason")
        self.assertEqual(session.last_close_reason, "transport_error")
        self.assertEqual(websocket_module.HELD_SESSION_CLOSE_REASONS, frozenset({
            "max_age", "generation_changed", "authority_refused", "authorization_failure",
            "timeout", "cancelled", "transport_error", "protocol_error", "connection_closed",
            "no_active_watch", "authority_unavailable", "tick_failed", "shutdown"}))


class FakeSession:
    """Records what the poller asks of its held connection."""

    def __init__(self, core):
        self.core = core
        self.calls = []

    def bind(self, generation):
        self.calls.append(("bind", generation))

    def discard(self, reason):
        self.calls.append(("discard", reason))

    async def close(self, reason):
        self.calls.append(("close", reason))

    async def command(self, payload):
        self.calls.append(("command", payload["type"]))
        return await self.core.command(payload)

    def health(self):
        return {"open": False, "opened": 0, "frames": 0, "last_close_reason": None}


async def never(payload):
    raise AssertionError("the poller must read over its session")


class PollerSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.core = FakeCore()
        self.runtime = FakeRuntime()
        self.fake = FakeSession(self.core)
        self.service = TraceSnapshotService(self.directory.name, never, self.runtime, FakeAudit())
        self.poller = TraceWatchPoller(self.service, self.runtime, session=self.fake)

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def add_watch(self):
        telemetry, token = begin_request()
        telemetry.caller_id = "owner0caller"
        try:
            await self.service.watch(action="add", automation_id="a1")
        finally:
            end_request(token)

    async def test_active_ticks_bind_and_read_over_the_session(self):
        await self.add_watch()
        await self.poller.tick()
        self.core.add("r1", "error", error="synthetic")
        self.runtime.generation = 8
        await self.poller.tick()
        self.assertEqual(self.fake.calls, [
            ("bind", 7), ("command", "trace/list"),
            ("bind", 8), ("command", "trace/list"), ("command", "trace/get")])
        self.assertEqual(len(self.service.store.load_index()["snapshots"]), 1)

    async def test_no_active_watch_closes_the_session(self):
        await self.poller.tick()
        self.assertEqual(self.fake.calls, [("bind", 7), ("discard", "no_active_watch")])

    async def test_no_core_authority_closes_the_session_and_sends_nothing(self):
        await self.add_watch()
        self.runtime.available = False
        await self.poller.tick()
        self.assertEqual(self.fake.calls, [("discard", "authority_unavailable")])

    async def test_failed_tick_closes_the_session(self):
        await self.add_watch()

        async def broken(**kwargs):
            raise RuntimeError("synthetic")

        with patch.object(self.service, "poll_once", broken):
            with self.assertRaises(RuntimeError):
                await self.poller.tick()
        self.assertEqual(self.fake.calls, [("bind", 7), ("discard", "tick_failed")])

    async def test_shutdown_closes_the_session_after_the_task(self):
        await self.add_watch()
        task = self.poller.start()
        await until(lambda: self.poller.ticks == 1)
        await self.poller.shutdown()
        self.assertTrue(task.done())
        self.assertEqual(self.fake.calls[-1], ("close", "shutdown"))

    async def test_manual_capture_never_uses_the_session(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        service = TraceSnapshotService(directory.name, self.core.command, self.runtime, FakeAudit())
        TraceWatchPoller(service, self.runtime, session=self.fake)
        self.core.add("r1")
        telemetry, token = begin_request()
        telemetry.core_dispatch_authorizer = lambda: True
        try:
            value, _ = await service.capture(automation_id="a1", run_id="r1")
        finally:
            end_request(token)
        self.assertEqual(value["runs"][0]["outcome"], "captured")
        self.assertEqual(self.fake.calls, [])

    async def test_runtime_reports_the_session_only_when_configured(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = SimpleNamespace(governance_path=directory.name, access_secret="synthetic-a",
                                   ha_token="synthetic-b")
        runtime = TraceSnapshotRuntime()
        runtime.configure(settings, self.runtime, audit=FakeAudit(), command=self.core.command,
                          watch_session=self.fake)
        self.assertIs(runtime.poller.session, self.fake)
        self.assertEqual(runtime.health()["session"], self.fake.health())
        self.assertEqual(runtime.watch_health()["session"], self.fake.health())
        runtime = TraceSnapshotRuntime()
        runtime.configure(settings, self.runtime, audit=FakeAudit(), command=self.core.command)
        self.assertNotIn("session", runtime.health())

    def test_application_gives_the_poller_a_held_session(self):
        tree = ast.parse((PACKAGE / "application.py").read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == "configure"
                 and isinstance(node.func.value, ast.Name)
                 and node.func.value.id == "TRACE_SNAPSHOTS"]
        self.assertEqual(len(calls), 1)
        keywords = {k.arg: k.value for k in calls[0].keywords}
        session = keywords["watch_session"]
        self.assertEqual(ast.unparse(session), "HeldWebSocketSession(settings)")
        # Manual captures keep the per-frame client.
        self.assertEqual(ast.unparse(keywords["command"]),
                         "HomeAssistantWebSocketClient(settings).command")


class PollerOverHeldSessionTests(PeerCase):
    """The real poller and service over the loopback peer."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        end_request(self.token)
        self.directory = tempfile.TemporaryDirectory()
        self.runtime = FakeRuntime()
        self.service = TraceSnapshotService(self.directory.name, never, self.runtime, FakeAudit())
        self.held = self.session()
        self.poller = TraceWatchPoller(self.service, self.runtime, session=self.held)
        self.telemetry, self.token = begin_request()
        self.telemetry.caller_id = "owner0caller"
        await self.service.watch(action="add", automation_id="a1")

    async def asyncTearDown(self):
        await super().asyncTearDown()
        self.directory.cleanup()

    async def test_ticks_share_one_connection_until_authority_or_watches_end(self):
        await self.poller.tick()
        await self.poller.tick()
        self.assertEqual((self.peer.connections, len(self.peer.frames)), (1, 2))
        self.assertEqual({frame["type"] for _, frame in self.peer.frames}, {"trace/list"})
        self.assertTrue(self.held.is_open)
        self.runtime.generation = 8
        await self.poller.tick()
        self.assertEqual(self.peer.connections, 2)
        self.assertEqual(self.held.last_close_reason, "generation_changed")
        self.runtime.available = False
        await self.poller.tick()
        self.assertFalse(self.held.is_open)
        self.assertEqual(self.held.last_close_reason, "authority_unavailable")
        self.assertEqual(len(self.peer.frames), 3)
        self.runtime.available = True
        await self.poller.tick()
        await self.service.watch(action="remove", automation_id="a1")
        await self.poller.tick()
        self.assertFalse(self.held.is_open)
        self.assertEqual(self.held.last_close_reason, "no_active_watch")
        await self.poller.shutdown()
        self.assertEqual(self.held.last_close_reason, "no_active_watch")

    async def test_authority_lost_mid_tick_refuses_the_next_frame(self):
        await self.poller.tick()
        # The run is listed; Core authority retires before its trace/get.

        def script(frame):
            self.runtime.available = False
            return [result(frame, [{"run_id": "r1", "state": "stopped",
                                    "script_execution": "error", "domain": "automation",
                                    "item_id": "a1", "last_step": "action/0",
                                    "timestamp": {"start": "2026-10-26T10:01:00+00:00",
                                                  "finish": "2026-10-26T10:01:01+00:00"}}])]

        self.peer.script = script
        await self.poller.tick()
        self.assertEqual([frame["type"] for _, frame in self.peer.frames],
                         ["trace/list", "trace/list"])
        self.assertFalse(self.held.is_open)
        self.assertEqual(self.held.last_close_reason, "authority_refused")
        self.assertEqual(self.service.store.load_index()["snapshots"], [])


class SkipCase(ServiceCase):
    async def add_watch(self, automation_id="a1", mode="executed", **kwargs):
        value, _ = await self.service.watch(action="add", automation_id=automation_id,
                                            mode=mode, **kwargs)
        return value

    async def row(self, automation_id="a1"):
        value, _ = await self.service.watch(action="list")
        return next(w for w in value["watches"] if w["automation_id"] == automation_id)

    def stored(self, automation_id="a1"):
        return next(w for w in self.index()["watches"] if w["automation_id"] == automation_id)

    async def poll(self, **kwargs):
        self.clock.now += 60
        return await self.service.poll_once(**{"authority": AUTHORITY, **kwargs})

    def reasons(self, **counts):
        return {reason: counts.get(reason, 0) for reason in c.SKIP_REASONS}


class SkipReasonTests(SkipCase):
    async def assert_skip(self, reason, *, stored=1):
        row = await self.row()
        self.assertEqual(row["recent_skipped_polls"], 1)
        self.assertEqual(row["recent_skip_reasons"], self.reasons(**{reason: 1}))
        self.assertEqual(row["last_skip_reason"], reason)
        self.assertEqual(row["last_skipped_at"], c.utc_text(self.clock.now))
        self.assertEqual(row["skipped_polls"], stored)
        self.assertEqual(self.stored()["skipped_polls"], stored)

    async def test_a_new_watch_has_no_recent_skips(self):
        await self.add_watch()
        row = await self.row()
        self.assertEqual((row["recent_skipped_polls"], row["last_skipped_at"],
                          row["last_skip_reason"], row["recent_evicted_count"], row["churning"]),
                         (0, None, None, 0, False))
        self.assertEqual(row["recent_skip_reasons"], self.reasons())

    async def test_no_core_authority(self):
        await self.add_watch()
        self.assertIs(await self.poll(authority=None), True)
        await self.assert_skip("core_authority_unavailable")

    async def test_tick_time_exhausted(self):
        await self.add_watch()
        await self.poll(deadline=time.monotonic() - 1)
        await self.assert_skip("tick_time_exhausted")

    async def test_authority_retired_after_the_reads(self):
        await self.add_watch()
        await self.poll()
        self.core.add("r1")

        def retire(payload):
            self.runtime.generation += 1

        self.core.on_get = retire
        await self.poll()
        await self.assert_skip("core_authority_retired")

    async def test_authority_retired_during_a_failed_listing(self):
        await self.add_watch()

        async def retired(payload):
            self.runtime.generation += 1
            raise HomeAssistantApiError(details={"status": 500})

        await self.poll(command=retired)
        await self.assert_skip("core_authority_retired")

    async def test_failed_listing(self):
        await self.add_watch()

        async def failing(payload):
            raise HomeAssistantApiError(details={"status": 500})

        await self.poll(command=failing)
        await self.assert_skip("trace_list_failed")

    async def test_deferred_read_counts_after_its_commit(self):
        await self.add_watch()
        await self.poll()
        self.core.add("r1")
        self.core.fail_get["r1"] = HomeAssistantTimeoutError()
        await self.poll()
        await self.assert_skip("trace_read_deferred")
        # The run was not consumed: it is read again and captured.
        del self.core.fail_get["r1"]
        await self.poll()
        self.assertEqual([e["run_id"] for e in self.index()["snapshots"]], ["r1"])
        self.assertEqual((await self.row())["recent_skipped_polls"], 1)

    async def test_deferred_read_is_not_counted_when_its_commit_is_retired(self):
        await self.add_watch()
        await self.poll()
        self.core.add("r1")
        self.core.fail_get["r1"] = HomeAssistantTimeoutError()
        original = self.service._run

        async def observe(function, *args, **kwargs):
            if function == self.service._commit:
                await self.service.lock.acquire()
                try:
                    blocked = asyncio.create_task(original(function, *args, **kwargs))
                    await asyncio.sleep(0)
                    self.runtime.generation += 1
                finally:
                    self.service.lock.release()
                return await blocked
            return await original(function, *args, **kwargs)

        self.clock.now += 60
        with patch.object(self.service, "_run", observe):
            await self.service.poll_once(authority=AUTHORITY)
        self.assertEqual(self.stored()["skipped_polls"], 0)
        self.assertEqual(self.service.recent_skips, {})
        self.assertEqual(self.service.last_skips, {})

    def test_unknown_reason_is_refused(self):
        with self.assertRaises(ValueError):
            self.service._note_skips(["w1"], "not_a_reason", 0)
        self.assertEqual(self.service.recent_skips, {})

    async def test_recent_counts_cover_the_last_hour_only(self):
        await self.add_watch()
        await self.poll(authority=None)
        skipped_at = c.utc_text(self.clock.now)
        self.clock.now += c.RECENT_SECONDS + 1
        row = await self.row()
        self.assertEqual(row["recent_skipped_polls"], 0)
        self.assertEqual(row["recent_skip_reasons"], self.reasons())
        self.assertEqual((row["last_skipped_at"], row["last_skip_reason"]),
                         (skipped_at, "core_authority_unavailable"))
        self.assertEqual(row["skipped_polls"], 1)

    async def test_recent_events_are_bounded(self):
        await self.add_watch()
        with patch.object(c, "RECENT_EVENTS", 3):
            for _ in range(5):
                await self.poll(authority=None)
        row = await self.row()
        self.assertEqual((row["recent_skipped_polls"], row["skipped_polls"]), (3, 5))
        self.assertEqual(len(self.service.recent_skips[row["watch_id"]]), 3)

    async def test_lifetime_count_survives_add_and_resets_on_reinstate(self):
        await self.add_watch(duration_hours=1)
        await self.poll(authority=None)
        await self.add_watch(mode="failures", duration_hours=1)
        row = await self.row()
        self.assertEqual((row["skipped_polls"], row["recent_skipped_polls"]), (1, 1))
        self.clock.now += 2 * 3600
        value, _ = await self.service.watch(action="list")
        self.assertEqual(value["watches"], [])
        value, _ = await self.service.watch(action="renew", automation_id="a1")
        self.assertEqual(value["renewed"][0]["outcome"], "reinstated")
        row = await self.row()
        self.assertEqual((row["skipped_polls"], row["recent_skipped_polls"],
                          row["last_skipped_at"], row["last_skip_reason"]), (0, 0, None, None))


class FailureProtectionTests(SkipCase):
    """HAMCP-389: in executed mode a run that is not a failure never evicts a
    failure snapshot (quota patched to 2; one run captured per poll)."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        patcher = patch.object(c, "WATCH_QUOTA", 2)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def capture(self, run_id, execution="finished", **kwargs):
        self.core.add(run_id, execution, **kwargs)
        await self.poll()

    def held(self):
        return [e["run_id"] for e in sorted(self.index()["snapshots"],
                                            key=lambda e: e["captured_at_ms"])]

    async def watched(self, mode="executed"):
        await self.add_watch(mode=mode)
        await self.poll()

    async def test_only_failures_left_refuses_a_finished_run(self):
        await self.watched()
        await self.capture("f1", "error", error="synthetic")
        await self.capture("f2", "aborted")
        await self.capture("r3")
        self.assertEqual(self.held(), ["f1", "f2"])
        row = self.stored()
        self.assertEqual((row["uncaptured_runs"], row["evicted_count"], row["captured_count"]),
                         (1, 0, 2))
        # The refused run is not read again.
        await self.poll()
        self.assertEqual(self.stored()["uncaptured_runs"], 1)
        self.assertIn("failures_protected", c.OUTCOMES)

    async def test_finished_run_evicts_the_oldest_non_failure(self):
        await self.watched()
        await self.capture("f1", "error")
        await self.capture("r2")
        await self.capture("r3")
        self.assertEqual(self.held(), ["f1", "r3"])
        self.assertEqual(self.stored()["evicted_count"], 1)

    async def test_failure_run_evicts_non_failures_first_then_the_oldest_failure(self):
        await self.watched()
        await self.capture("r1")
        await self.capture("f2", "error")
        await self.capture("f3", "failed_single")
        self.assertEqual(self.held(), ["f2", "f3"])
        await self.capture("f4", "cancelled")
        self.assertEqual(self.held(), ["f3", "f4"])
        self.assertEqual(self.stored()["evicted_count"], 2)

    async def test_failures_mode_is_unchanged(self):
        await self.watched(mode="failures")
        for name in ("f1", "f2", "f3"):
            await self.capture(name, "error")
        self.assertEqual(self.held(), ["f2", "f3"])

    async def test_a_run_whose_only_failure_sign_is_an_error_field_is_not_protected(self):
        await self.watched()
        await self.capture("e1", error="synthetic")
        await self.capture("r2")
        await self.capture("r3")
        self.assertEqual(self.held(), ["r2", "r3"])
        self.assertEqual(self.stored()["uncaptured_runs"], 0)


class ChurnTests(SkipCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        for name, value in (("WATCH_QUOTA", 2), ("CHURN_EVICTIONS", 2)):
            patcher = patch.object(c, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_churning_watch_is_flagged_and_named_in_health(self):
        self.assertEqual(c.CHURN_EVICTIONS, 2)
        await self.add_watch()
        await self.poll()
        for name in ("r1", "r2", "r3"):
            self.core.add(name)
            await self.poll()
        row = await self.row()
        self.assertEqual((row["recent_evicted_count"], row["churning"]), (1, False))
        self.core.add("r4")
        await self.poll()
        row = await self.row()
        self.assertEqual((row["recent_evicted_count"], row["churning"]), (2, True))
        self.assertEqual(row["evicted_count"], 2)
        # Health refreshes at the next store transaction.
        await self.poll()
        health = self.service.watch_health()
        self.assertEqual(health["churning"], [{"automation_id": "a1", "recent_evicted_count": 2}])
        self.assertEqual(health["churning_count"], 1)
        self.clock.now += c.RECENT_SECONDS
        row = await self.row()
        self.assertEqual((row["recent_evicted_count"], row["churning"], row["evicted_count"]),
                         (0, False, 2))
        await self.poll()
        health = self.service.watch_health()
        self.assertEqual((health["churning"], health["churning_count"]), ([], 0))


class NothingPersistedTests(SkipCase):
    async def test_runtime_fields_never_reach_the_store_and_reset_on_restart(self):
        with patch.object(c, "WATCH_QUOTA", 2):
            await self.add_watch()
            await self.poll()
            for name in ("r1", "r2", "r3"):
                self.core.add(name)
                await self.poll()
            await self.poll(authority=None)
            await self.poll(deadline=time.monotonic() - 1)
            row = await self.row()
        self.assertEqual((row["recent_skipped_polls"], row["recent_evicted_count"]), (2, 1))
        stored = all_store_bytes(self.root)
        for needle in RUNTIME_ONLY:
            self.assertNotIn(needle, stored)
        self.assertEqual(set(self.stored()), STORED_WATCH_KEYS)
        restarted = TraceSnapshotService(self.root, self.core.command, self.runtime, self.audit,
                                         known_secrets=(KNOWN,), clock=self.clock)
        value, _ = await restarted.watch(action="list")
        row = value["watches"][0]
        self.assertEqual((row["skipped_polls"], row["evicted_count"]), (2, 1))
        self.assertEqual((row["recent_skipped_polls"], row["recent_evicted_count"],
                          row["last_skipped_at"], row["churning"]), (0, 0, None, False))


class ConstantsTests(unittest.TestCase):
    def test_go_values(self):
        self.assertEqual(c.SKIP_REASONS, ("core_authority_unavailable", "core_authority_retired",
                                          "tick_time_exhausted", "trace_list_failed",
                                          "trace_read_deferred"))
        self.assertEqual((c.RECENT_SECONDS, c.RECENT_EVENTS, c.CHURN_EVICTIONS),
                         (3600, 256, c.WATCH_QUOTA))
        self.assertEqual(c.WATCH_QUOTA, 20)
        self.assertEqual(websocket_module.HELD_SESSION_MAX_AGE_SECONDS, 600.0)


if __name__ == "__main__":
    unittest.main()
