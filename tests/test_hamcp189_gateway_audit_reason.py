"""HAMCP-189 PR C: the gateway audit keeps the refusal reason (R8, L1).

L1: an empty-string tool name is refused before Core and the application like
any other unmapped name, and its audit record and terminal error metric carry
the same bounded unmapped-refusal shape.

R8: a list or dict ``params.name`` reaches the real FastMCP stack, which
rejects it with JSON-RPC -32602.  The audit ``finally`` must not raise on the
unhashable name; exactly one generic request record is written and no Core
authority is acquired.  All data is synthetic and offline.

L1 residual (review of ``1189020``): diagnostic logs for a malformed or falsey
name record only ``non_string_tool_name:<type>``, never the raw value, in both
the application's request logs and the pinned SDK's validation warning.
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering import routing as routing_module  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.logging_config import configure_logging  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    CORE_REQUIREMENT_UNMAPPED,
)
from ha_mcp_engineering.mcp_server import create_mcp_server  # noqa: E402
from ha_mcp_engineering.routing import (  # noqa: E402
    AuthenticatedMcpGateway,
    SdkRequestDiagnosticFilter,
)

from tests.same_thread_asgi_client import SameThreadAsgiTestClient  # noqa: E402
import tests.test_hamcp164_core_gate_fail_closed as gate164  # noqa: E402
from tests.test_hamcp164_core_gate_fail_closed import (  # noqa: E402
    RecordingCore,
    call,
    gateway_for,
    settings,
)
from tests.test_mcp_inbound_security import SECRET, settings_for  # noqa: E402


def audit_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class EmptyNameAuditTests(unittest.IsolatedAsyncioTestCase):
    """L1: the empty name keeps the unmapped-refusal audit shape."""

    assertUnmappedRefusal = gate164.GatewayFailClosedTests.assertUnmappedRefusal

    async def test_empty_name_audit_and_metric_carry_the_unmapped_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            configured = settings()
            path = Path(directory) / "audit.jsonl"
            audit = AuditLogger(str(path), configured.access_secret)
            core = RecordingCore()
            gateway, app = gateway_for(core, audit)
            with patch.object(
                routing_module.METRICS, "record_error"
            ) as record_error:
                body = await call(
                    gateway, "", {"payload": "synthetic-sensitive-value"}
                )
            self.assertUnmappedRefusal(body, app, core)
            records = audit_records(path)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["event"], "tool_call")
        self.assertEqual(record["tool_name"], "")
        self.assertEqual(record["result_status"], "failure")
        self.assertEqual(record["error_code"], "unsupported_operation")
        self.assertEqual(
            record["parameters"],
            {
                "core_route": "unmapped",
                "reason_code": CORE_REQUIREMENT_UNMAPPED,
                "argument_fields": ["payload"],
                "provider_dispatch_occurred": False,
                "upstream_attempted": False,
            },
        )
        self.assertEqual(record["resource_ids"], {})
        self.assertNotIn("synthetic-sensitive-value", json.dumps(records))
        # One terminal public outcome contributes exactly one error count.
        record_error.assert_called_once_with("unsupported_operation")

    async def test_nonempty_unmapped_name_keeps_one_record_and_one_count(self):
        # Preservation: the non-empty path is unchanged by the empty-name fix.
        with tempfile.TemporaryDirectory() as directory:
            configured = settings()
            path = Path(directory) / "audit.jsonl"
            audit = AuditLogger(str(path), configured.access_secret)
            core = RecordingCore()
            gateway, app = gateway_for(core, audit)
            with patch.object(
                routing_module.METRICS, "record_error"
            ) as record_error:
                body = await call(gateway, "synthetic_unmapped_tool", {})
            self.assertUnmappedRefusal(body, app, core)
            records = audit_records(path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["tool_name"], "synthetic_unmapped_tool")
        self.assertEqual(
            records[0]["parameters"]["reason_code"], CORE_REQUIREMENT_UNMAPPED
        )
        record_error.assert_called_once_with("unsupported_operation")


class NonStringNameAuditTests(unittest.TestCase):
    """R8: unhashable names through the real FastMCP stack do not escape."""

    def fixture(self, directory):
        configured = settings_for(directory)
        server = create_mcp_server(configured)
        tool_calls: list[str] = []

        # A Core-independent allowlisted name, so the follow-up call reaches
        # the synthetic tool through the real Core gate without a lease.
        @server.tool(name="list_capabilities")
        async def synthetic_read() -> str:
            tool_calls.append("read")
            return "synthetic-read-ok"

        inner = server.streamable_http_app()
        core = RecordingCore()
        gateway = AuthenticatedMcpGateway(
            inner,
            configured,
            AuditLogger(configured.audit_path, SECRET),
            core_runtime=core,
        )
        client = SameThreadAsgiTestClient(
            gateway, lifespan_app=inner, base_url="http://127.0.0.1:8100",
        )
        return Path(configured.audit_path), client, core, tool_calls

    @staticmethod
    def post(client, name):
        return client.post(
            "/" + SECRET + "/mcp",
            headers={"accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": {}},
            },
        )

    def test_list_and_dict_names_write_exactly_one_generic_record(self):
        for name in (["list_capabilities"], {"name": "list_capabilities"}):
            with self.subTest(name_type=type(name).__name__):
                with tempfile.TemporaryDirectory() as directory:
                    path, client, core, tool_calls = self.fixture(directory)
                    with client:
                        response = self.post(client, name)
                        # The transport survives: the next request succeeds.
                        follow_up = self.post(client, "list_capabilities")
                    records = audit_records(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("-32602", response.text)
                self.assertEqual(follow_up.status_code, 200)
                self.assertIn("synthetic-read-ok", follow_up.text)
                self.assertEqual(tool_calls, ["read"])
                self.assertEqual(core.acquired, [])
                self.assertEqual(client.pending_task_count, 0)
                generic = [
                    item for item in records if item.get("event") == "mcp_request"
                ]
                self.assertEqual(len(generic), 1, records)
                self.assertEqual(generic[0]["mcp_method"], "tools/call")
                self.assertEqual(generic[0]["result_status"], "failure")
                self.assertTrue(generic[0]["error_code"])
                # The unhashable name never becomes a tool record, and only
                # the follow-up call is audited as a tool call.
                tool_records = [
                    item for item in records if item.get("event") == "tool_call"
                ]
                self.assertEqual(
                    [item["tool_name"] for item in tool_records],
                    ["list_capabilities"],
                )

    def test_every_non_string_name_writes_one_record_and_one_count(self):
        # Review L1: falsey malformed names were refused by the SDK but missed
        # both the generic audit record and the error count.  Classification
        # now follows the tools/call method, not the name's truthiness.
        marker = "synthetic-raw-name-marker"
        cases = (
            None, False, 0, [], {},
            True, 1, 1.5, [marker], {"name": marker},
        )
        for name in cases:
            with self.subTest(name=repr(name)):
                with tempfile.TemporaryDirectory() as directory:
                    path, client, core, tool_calls = self.fixture(directory)
                    with client, patch.object(
                        routing_module.METRICS, "record_error"
                    ) as record_error:
                        response = self.post(client, name)
                    records = audit_records(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("-32602", response.text)
                self.assertEqual(tool_calls, [])
                self.assertEqual(core.acquired, [])
                self.assertEqual(len(records), 1, records)
                record = records[0]
                self.assertEqual(record["event"], "mcp_request")
                self.assertEqual(record["mcp_method"], "tools/call")
                self.assertEqual(record["result_status"], "failure")
                self.assertTrue(record["error_code"])
                self.assertNotIn("tool_name", record)
                self.assertNotIn(marker, json.dumps(records))
                record_error.assert_called_once_with(record["error_code"])

    def test_successful_string_name_keeps_one_tool_record_and_no_count(self):
        # Preservation: classifying by method does not count a successful
        # tools/call as an error or add a generic duplicate record.
        with tempfile.TemporaryDirectory() as directory:
            path, client, core, tool_calls = self.fixture(directory)
            with client, patch.object(
                routing_module.METRICS, "record_error"
            ) as record_error:
                response = self.post(client, "list_capabilities")
            records = audit_records(path)
        self.assertEqual(response.status_code, 200)
        self.assertIn("synthetic-read-ok", response.text)
        self.assertEqual(tool_calls, ["read"])
        self.assertEqual(len(records), 1, records)
        self.assertEqual(records[0]["event"], "tool_call")
        self.assertEqual(records[0]["result_status"], "success")
        record_error.assert_not_called()


class RawNameLoggingTests(unittest.TestCase):
    """L1 residual: no raw malformed name in any captured diagnostic log."""

    fixture = NonStringNameAuditTests.fixture
    post = staticmethod(NonStringNameAuditTests.post)
    marker = "synthetic-raw-name-log-marker"

    def capture(self, name):
        """Post one name with production logging; return every log line."""

        app_logger = logging.getLogger("ha_mcp_engineering")
        saved = (app_logger.handlers[:], app_logger.level, app_logger.propagate)
        root = logging.getLogger()
        root_level = root.level
        everything = io.StringIO()
        catch_all = logging.StreamHandler(everything)
        catch_all.setLevel(logging.DEBUG)
        try:
            with tempfile.TemporaryDirectory() as directory:
                path, client, core, tool_calls = self.fixture(directory)
                # Production formatting (JsonFormatter) for application logs,
                # and the root logger at DEBUG for the SDK's own channel.
                configure_logging("INFO")
                application = io.StringIO()
                app_logger.handlers[0].setStream(application)
                root.addHandler(catch_all)
                root.setLevel(logging.DEBUG)
                with client:
                    response = self.post(client, name)
                records = audit_records(path)
        finally:
            root.removeHandler(catch_all)
            root.setLevel(root_level)
            app_logger.handlers[:] = saved[0]
            app_logger.setLevel(saved[1])
            app_logger.propagate = saved[2]
        lines = [
            json.loads(line)
            for line in application.getvalue().splitlines()
            if line.strip()
        ]
        return response, records, core, tool_calls, lines, everything.getvalue()

    def test_malformed_names_log_only_a_bounded_type(self):
        cases = (
            None, False, 0, [], {},
            [self.marker], {"arbitrary": self.marker}, {"name": self.marker},
        )
        for name in cases:
            with self.subTest(name=repr(name)):
                response, records, core, tool_calls, lines, sdk = self.capture(
                    name
                )
                self.assertIn("-32602", response.text)
                self.assertEqual(tool_calls, [])
                self.assertEqual(core.acquired, [])
                self.assertEqual(len(records), 1, records)
                self.assertNotIn("tool_name", records[0])
                expected = f"non_string_tool_name:{type(name).__name__}"
                logged = {
                    line["event"]: line["context"].get("tool") for line in lines
                }
                self.assertEqual(logged["request_started"], expected)
                self.assertEqual(logged["request_completed"], expected)
                application = json.dumps(lines)
                self.assertNotIn(self.marker, application)
                self.assertNotIn(self.marker, sdk)
                self.assertNotIn(self.marker, json.dumps(records))
                # The SDK still reports the refusal, bounded.
                self.assertIn(
                    "Failed to validate request: 30 validation errors for "
                    "ClientRequest; raw request values suppressed",
                    sdk,
                )
                self.assertNotIn("input_value", sdk)
                self.assertNotIn("Message that failed validation: method", sdk)

    def test_string_name_is_still_logged(self):
        # Preservation: a string name keeps its value in the request logs.
        response, records, _core, tool_calls, lines, _sdk = self.capture(
            "list_capabilities"
        )
        self.assertIn("synthetic-read-ok", response.text)
        self.assertEqual(tool_calls, ["read"])
        self.assertEqual(records[0]["tool_name"], "list_capabilities")
        self.assertEqual(
            {line["event"]: line["context"].get("tool") for line in lines},
            {
                "request_started": "list_capabilities",
                "request_completed": "list_capabilities",
            },
        )


class SdkDiagnosticFilterTests(unittest.TestCase):
    """The filter rewrites only the SDK session's raw validation messages."""

    def record(self, message, pathname):
        return logging.LogRecord(
            "root", logging.WARNING, pathname, 1, message, (), None
        )

    def test_sdk_validation_messages_keep_only_a_bounded_summary(self):
        sdk = "/site-packages/mcp/shared/session.py"
        cases = {
            "Failed to validate request: 1 validation error for ClientRequest"
            "\nparams.name\n  Input should be a valid string "
            "[input_value={'x': 'raw-secret-value'}]":
            "Failed to validate request: 1 validation error for "
            "ClientRequest; raw request values suppressed",
            "Message that failed validation: method='tools/call' "
            "params={'name': 'raw-secret-value'}":
            "Message that failed validation: unclassified; raw request "
            "values suppressed",
            "Failed to validate notification: raw-secret-value. Message "
            "was: raw-secret-value":
            "Failed to validate notification: unclassified; raw request "
            "values suppressed",
        }
        for message, expected in cases.items():
            with self.subTest(expected=expected):
                record = self.record(message, sdk)
                self.assertTrue(SdkRequestDiagnosticFilter().filter(record))
                self.assertEqual(record.getMessage(), expected)

    def test_other_records_are_untouched(self):
        message = "Failed to validate request: raw-but-not-the-sdk"
        for pathname in (
            "/app/ha_mcp_engineering/session.py",
            "/site-packages/mcp/server/session.py",
        ):
            record = self.record(message, pathname)
            self.assertTrue(SdkRequestDiagnosticFilter().filter(record))
            self.assertEqual(record.getMessage(), message)
        record = self.record(
            "Read stream closed by client", "/site-packages/mcp/shared/session.py"
        )
        SdkRequestDiagnosticFilter().filter(record)
        self.assertEqual(record.getMessage(), "Read stream closed by client")

    def test_gateway_installs_the_filter_once(self):
        root = logging.getLogger()
        with tempfile.TemporaryDirectory() as first, \
                tempfile.TemporaryDirectory() as second:
            NonStringNameAuditTests().fixture(first)
            NonStringNameAuditTests().fixture(second)
        self.assertEqual(
            sum(isinstance(item, SdkRequestDiagnosticFilter)
                for item in root.filters),
            1,
        )


if __name__ == "__main__":
    unittest.main()
