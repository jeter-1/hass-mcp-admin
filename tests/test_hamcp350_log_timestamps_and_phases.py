"""HAMCP-350 (c) UTC log timestamps and (b1) startup phase timing events."""

import asyncio
from datetime import datetime, timezone
import io
import json
import logging
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.support.process_isolation import restore_after_class

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering import application  # noqa: E402
from ha_mcp_engineering import secret_paths  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.logging_config import (  # noqa: E402
    JsonFormatter,
    TimestampedTextFormatter,
    configure_logging,
    log_event,
    utc_timestamp,
    uvicorn_log_config,
)
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402
from ha_mcp_engineering.sanitization import sanitize_untrusted_data  # noqa: E402

ISO_MS_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")
# 2026-11-09T21:00:36.590Z
CREATED = datetime(2026, 11, 9, 21, 0, 36, 590000, tzinfo=timezone.utc).timestamp()
EXPECTED = "2026-11-09T21:00:36.590Z"


def record(name="mcp.server.lowlevel", msg="sdk line", args=None, **extra):
    item = logging.LogRecord(name, logging.WARNING, __file__, 1, msg, args, None)
    item.created = CREATED
    item.__dict__.update(extra)
    return item


class LogTimestampTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        restore_after_class(cls)

    def test_utc_timestamp_is_iso_8601_utc_with_milliseconds(self):
        self.assertEqual(utc_timestamp(CREATED), EXPECTED)
        self.assertEqual(utc_timestamp(0), "1970-01-01T00:00:00.000Z")

    def test_json_line_carries_the_record_time_and_it_is_not_redacted(self):
        captured = []
        logger = logging.getLogger("ha_mcp_engineering.hamcp350_probe")
        handler = logging.Handler()
        handler.emit = captured.append
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        logger.setLevel(logging.INFO)
        with patch("time.time", return_value=CREATED), patch(
            "time.time_ns", return_value=int(CREATED * 1e9)
        ):
            log_event(
                logger, logging.INFO, "probe", "Probe.",
                context={"token": "hamcp350-synthetic-secret"},
                secret="hamcp350-synthetic-secret",
            )
        payload = json.loads(JsonFormatter().format(captured[0]))
        self.assertEqual(payload["timestamp"], EXPECTED)
        self.assertEqual(payload["event"], "probe")
        self.assertTrue(payload["redaction_applied"])
        self.assertNotIn("hamcp350-synthetic-secret", json.dumps(payload))

    def test_json_timestamp_is_written_after_sanitization(self):
        """HAMCP-358 (7): a known secret equal to the timestamp text leaves it raw.

        The formatter's sanitization pass has no per-call secret; the
        process-wide secret path is the known secret it does see. With that
        secret equal to the record's timestamp text, the pass redacts the
        text in the message and the context. The timestamp stays raw only
        because it is added to the payload after that pass.
        """
        secret_paths.configure(f"http://127.0.0.1:9583/{EXPECTED}/mcp")
        self.addCleanup(secret_paths.reset)
        # The premise: this pass redacts the timestamp text in any field it sees.
        seen = sanitize_untrusted_data({"timestamp": EXPECTED}, max_string=2048)
        self.assertEqual(seen.value, {"timestamp": secret_paths.MARKER})
        captured = []
        logger = logging.getLogger("ha_mcp_engineering.hamcp358_probe")
        handler = logging.Handler()
        handler.emit = captured.append
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        logger.setLevel(logging.INFO)
        with patch("time.time", return_value=CREATED), patch(
            "time.time_ns", return_value=int(CREATED * 1e9)
        ):
            log_event(
                logger, logging.INFO, "probe", f"Probe at {EXPECTED} done",
                context={"seen_at": EXPECTED},
                secret=EXPECTED,
            )
        payload = json.loads(JsonFormatter().format(captured[0]))
        self.assertEqual(payload["timestamp"], EXPECTED)
        self.assertNotIn(EXPECTED, payload["message"])
        self.assertIn("[REDACTED", payload["message"])
        self.assertNotIn(EXPECTED, json.dumps(payload["context"]))
        self.assertIn("[REDACTED", json.dumps(payload["context"]))
        self.assertTrue(payload["redaction_applied"])
        self.assertEqual(json.dumps(payload).count(EXPECTED), 1)

    def test_json_timestamp_follows_record_created_not_the_message(self):
        payload = json.loads(
            JsonFormatter().format(record(msg="at 1999-01-01T00:00:00.000Z"))
        )
        self.assertEqual(payload["timestamp"], EXPECTED)

    def test_root_text_formatter_prefixes_sdk_lines(self):
        line = TimestampedTextFormatter().format(record())
        self.assertEqual(line, f"{EXPECTED} WARNING mcp.server.lowlevel: sdk line")

    def test_uvicorn_default_and_access_lines_start_with_the_timestamp(self):
        config = uvicorn_log_config()
        default = config["formatters"]["default"]
        formatter = default["()"](fmt=default["fmt"], use_colors=False)
        line = formatter.format(record(name="uvicorn.error", msg="Uvicorn running"))
        self.assertEqual(line, f"{EXPECTED} WARNING:  Uvicorn running")
        access = config["formatters"]["access"]
        formatter = access["()"](fmt=access["fmt"], use_colors=False)
        line = formatter.format(
            record(
                name="uvicorn.access",
                msg='%s - "%s %s HTTP/%s" %d',
                args=("127.0.0.1:1", "GET", "/ready", "1.1", 503),
            )
        )
        self.assertTrue(line.startswith(f"{EXPECTED} WARNING:  127.0.0.1:1 - "), line)
        self.assertIn('"GET /ready HTTP/1.1" 503', line)

    def test_uvicorn_config_is_a_fresh_copy(self):
        first = uvicorn_log_config()
        first["formatters"]["default"]["fmt"] = "changed"
        self.assertNotEqual(uvicorn_log_config()["formatters"]["default"]["fmt"], "changed")
        self.assertFalse(first["disable_existing_loggers"])

    def test_configure_logging_replaces_root_handlers_and_keeps_level_and_filters(self):
        root = logging.getLogger()
        sentinel_filter = logging.Filter("kept")
        stale = logging.StreamHandler(io.StringIO())
        root.addHandler(stale)
        root.addFilter(sentinel_filter)
        self.addCleanup(root.removeFilter, sentinel_filter)
        level = root.level
        stream = io.StringIO()
        with patch.object(sys, "stderr", stream):
            configure_logging("INFO")
        self.assertEqual(root.level, level)
        self.assertIn(sentinel_filter, root.filters)
        self.assertEqual(len(root.handlers), 1)
        self.assertNotIn(stale, root.handlers)
        handler = root.handlers[0]
        self.assertIsInstance(handler.formatter, TimestampedTextFormatter)
        self.assertIs(handler.stream, stream)
        logging.getLogger("mcp.server.lowlevel").warning("sdk line")
        self.assertRegex(stream.getvalue(), ISO_MS_UTC.pattern + r" WARNING mcp\.server\.lowlevel: sdk line\n")
        engineering = logging.getLogger("ha_mcp_engineering")
        self.assertFalse(engineering.propagate)
        self.assertIsInstance(engineering.handlers[0].formatter, JsonFormatter)

    def test_listeners_use_the_timestamped_uvicorn_config(self):
        from ha_mcp_engineering import inbound_topology_observer as observer

        with patch.object(observer, "prepare_observation", return_value=None), patch.object(
            observer.uvicorn, "Config"
        ) as config, patch.object(observer.uvicorn, "Server"):
            observer.create_mcp_listener(object(), port=8100, log_level="info")
        self.assertEqual(config.call_args.kwargs["log_config"], uvicorn_log_config())


def phase_events(records):
    return [
        item.safe_context
        for item in records
        if getattr(item, "event", None) == application.STARTUP_PHASE_EVENT
    ]


class StartupPhaseTimingTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        restore_after_class(cls)

    def capture(self):
        return self.assertLogs("ha_mcp_engineering.application", level="INFO")

    def test_phase_outcomes_and_levels(self):
        with self.capture() as logs:
            with application._startup_phase("store_load"):
                pass
            with application._startup_phase("core_boot") as phase:
                phase["outcome"] = "skipped"
                phase["detail"] = "x"
            with self.assertRaises(RuntimeError):
                with application._startup_phase("f3_recovery"):
                    raise RuntimeError("hamcp350-synthetic-exception-text")
            with self.assertRaises(asyncio.CancelledError):
                with application._startup_phase("first_reconcile"):
                    raise asyncio.CancelledError
        events = phase_events(logs.records)
        self.assertEqual(
            [(e["phase"], e["outcome"]) for e in events],
            [("store_load", "completed"), ("core_boot", "skipped"),
             ("f3_recovery", "failed"), ("first_reconcile", "cancelled")],
        )
        self.assertEqual(events[1]["detail"], "x")
        self.assertEqual(
            [r.levelno for r in logs.records],
            [logging.INFO, logging.INFO, logging.WARNING, logging.WARNING],
        )
        for event in events:
            self.assertIsInstance(event["duration_ms"], float)
            self.assertGreaterEqual(event["duration_ms"], 0)
        self.assertNotIn("hamcp350-synthetic-exception-text", repr(events))

    async def test_listener_start_is_timed_and_signals_only_when_listening(self):
        class Listener:
            should_exit = False
            started = False

            async def startup(self, sockets=None):
                self.started = succeed

        for succeed, outcome in ((True, "completed"), (False, "failed")):
            listener, started = Listener(), asyncio.Event()
            with self.capture() as logs:
                self.assertTrue(application._observe_listener_start(listener, started))
                await listener.startup()
            [event] = phase_events(logs.records)
            self.assertEqual((event["phase"], event["outcome"]), ("listener_start", outcome))
            self.assertEqual(started.is_set(), succeed)

    async def test_listener_start_failure_is_logged_and_reraised(self):
        class Listener:
            async def startup(self, sockets=None):
                raise OSError("bind failed")

        listener, started = Listener(), asyncio.Event()
        application._observe_listener_start(listener, started)
        with self.capture() as logs, self.assertRaises(OSError):
            await listener.startup()
        [event] = phase_events(logs.records)
        self.assertEqual(event["outcome"], "failed")
        self.assertFalse(started.is_set())

    def test_listener_without_startup_is_not_observed(self):
        self.assertFalse(application._observe_listener_start(object(), asyncio.Event()))

    async def test_serve_logs_each_startup_phase_once_in_order(self):
        settings = Settings(
            ha_url="http://supervisor/core", ha_token="test-token",
            access_secret="hamcp350-listener-access-secret", port=8100,
            audit_path="/data/audit.jsonl", rate_limit_per_minute=120,
            rate_limit_burst=25, destructive_services=frozenset(),
            ingress_port=8110,
        )
        started_events = []

        class FakeConfig:
            def __init__(self, app, **kwargs):
                self.port = kwargs["port"]

        class FakeServer:
            def __init__(self, config):
                self.config = config
                self.should_exit = False
                self.started = False
                self.install_signal_handlers = lambda: None

            async def startup(self, sockets=None):
                self.started = True

            async def serve(self):
                await self.startup()
                await asyncio.sleep(0)

        async def upstream(_gateway, *, listener_started=None):
            started_events.append(listener_started)
            await asyncio.Event().wait()

        gateway = MagicMock(spec=AuthenticatedMcpGateway)
        core = MagicMock(configured=True)
        core.reconcile_until_initialized = AsyncMock()
        core.supervise = AsyncMock()
        with patch.object(application.uvicorn, "Config", FakeConfig), patch.object(
            application.uvicorn, "Server", FakeServer
        ), patch.object(
            application, "create_mcp_listener",
            side_effect=lambda app, **kwargs: FakeServer(FakeConfig(app, **kwargs)),
        ), patch.object(
            application, "create_application", return_value=gateway
        ), patch.object(
            application, "create_approval_application", return_value=object()
        ), patch.object(
            application, "_run_f3_recovery_pass", new=AsyncMock()
        ), patch.object(
            application, "_supervise_upstream_reconciliation", side_effect=upstream
        ), patch.object(
            application.TRACE_SNAPSHOTS, "start_poller", return_value=None
        ), patch.object(
            application.GOVERNANCE, "start_historical_validation", return_value=None
        ), patch.object(application, "CORE_READMISSION", core        ), self.capture() as logs:
            await asyncio.wait_for(application._serve(settings), timeout=5)
        events = phase_events(logs.records)
        self.assertEqual(
            [(e["phase"], e["outcome"]) for e in events],
            [("store_load", "completed"), ("core_boot", "completed"),
             ("f3_recovery", "completed"), ("listener_start", "completed")],
        )
        [started] = started_events
        self.assertIsInstance(started, asyncio.Event)
        self.assertTrue(started.is_set())


def tearDownModule():
    # HAMCP-288: _serve may install the process-wide secret-path redaction;
    # reset it so it cannot redact another module's data in this process.
    secret_paths.reset()


if __name__ == "__main__":
    unittest.main()
