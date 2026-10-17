"""HAMCP-189 PR C: the gateway audit keeps the refusal reason (R8, L1).

L1: an empty-string tool name is refused before Core and the application like
any other unmapped name, and its audit record and terminal error metric carry
the same bounded unmapped-refusal shape.

R8: a list or dict ``params.name`` reaches the real FastMCP stack, which
rejects it with JSON-RPC -32602.  The audit ``finally`` must not raise on the
unhashable name; exactly one generic request record is written and no Core
authority is acquired.  All data is synthetic and offline.
"""

from __future__ import annotations

import json
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
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    CORE_REQUIREMENT_UNMAPPED,
)
from ha_mcp_engineering.mcp_server import create_mcp_server  # noqa: E402
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
