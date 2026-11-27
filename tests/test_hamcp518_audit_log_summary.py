"""HAMCP-518: get_audit_log summary=true counts the whole current audit file.

A summary is derived in one pass over the same whole-row snapshot a page
reads, and writes nothing. These tests prove its counts, that every name and
map it reports is bounded, that it fits the response limit by counting the
tools and events it leaves out, that the default page is unchanged, that a
summary with a cursor or a non-boolean flag is refused, and that the gateway
audits the flag as a bool (``None`` otherwise) and never the cursor.
"""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tests.test_beta_v2  # noqa: F401  (puts the Engineering package on sys.path)
from tests.support.audit_pages import audit_page_rows, split_audit_page

from ha_mcp_engineering.errors import ErrorCode, GovernanceError
from ha_mcp_engineering.tools import compatibility

SUMMARY_FIELDS = {
    "scope", "event_filter", "records", "first_timestamp", "last_timestamp",
    "events", "events_omitted", "tools", "tools_total", "tools_omitted", "calls_omitted",
    "untracked_tool_calls", "skipped_undecodable", "skipped_unparsed",
}


def _call(tool: str, status: str = "success", minute: int = 0) -> dict:
    return {
        "event": "tool_call",
        "tool_name": tool,
        "result_status": status,
        "timestamp": f"2026-11-27T10:{minute:02d}:00Z",
        "parameters": {"marker": "hamcp518-record-content-marker"},
    }


def _write(path: Path, records, mode: str = "w") -> None:
    with path.open(mode, encoding="utf-8") as stream:
        stream.writelines(json.dumps(record, sort_keys=True) + "\n" for record in records)


class _SummaryCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "audit.jsonl"
        environment = patch.dict(os.environ, {"AUDIT_PATH": str(self.path)})
        environment.start()
        self.addCleanup(environment.stop)

    def limit(self, response_size_limit: int) -> None:
        limited = patch.object(
            compatibility,
            "SETTINGS",
            replace(compatibility.SETTINGS, response_size_limit=response_size_limit),
        )
        limited.start()
        self.addCleanup(limited.stop)

    async def summary(self, event: str = "") -> tuple[str, dict]:
        rendered = await compatibility.get_audit_log(event=event, summary=True)
        self.assertTrue(rendered.endswith("\n"))
        self.assertEqual(rendered.count("\n"), 1)
        value = json.loads(rendered)
        self.assertEqual(set(value), {compatibility.AUDIT_SUMMARY_KEY})
        summary = value[compatibility.AUDIT_SUMMARY_KEY]
        self.assertEqual(set(summary), SUMMARY_FIELDS)
        self.assertEqual(summary["scope"], "current_audit_file")
        self.assertLessEqual(len(rendered.encode("utf-8")), compatibility.SETTINGS.response_size_limit)
        return rendered, summary


class SummaryCountTests(_SummaryCase):
    async def test_counts_events_tools_statuses_and_the_covered_span(self):
        records = [
            _call("ha_get_state", minute=1),
            {"event": "auth_failure", "timestamp": "2026-11-27T10:02:00Z"},
            _call("ha_get_state", "error", minute=3),
            _call("get_audit_log", minute=4),
            {"event": "rate_limited", "timestamp": "2026-11-27T10:05:00Z"},
            _call("ha_get_state", minute=6),
        ]
        _write(self.path, records)
        rendered, summary = await self.summary()
        self.assertEqual(summary["records"], 6)
        self.assertIsNone(summary["event_filter"])
        self.assertEqual(summary["first_timestamp"], "2026-11-27T10:01:00Z")
        self.assertEqual(summary["last_timestamp"], "2026-11-27T10:06:00Z")
        self.assertEqual(summary["events"], {"tool_call": 4, "auth_failure": 1, "rate_limited": 1})
        self.assertEqual(summary["tools"], [
            {"tool": "ha_get_state", "calls": 3, "result_status": {"success": 2, "error": 1}},
            {"tool": "get_audit_log", "calls": 1, "result_status": {"success": 1}},
        ])
        self.assertEqual(
            (summary["tools_total"], summary["tools_omitted"], summary["calls_omitted"]), (2, 0, 0)
        )
        self.assertEqual(
            (summary["untracked_tool_calls"], summary["skipped_undecodable"], summary["skipped_unparsed"]),
            (0, 0, 0),
        )
        # Only counts and names leave the file; record content does not.
        self.assertNotIn("hamcp518-record-content-marker", rendered)

    async def test_the_event_filter_counts_only_that_event(self):
        _write(self.path, [
            _call("ha_get_state", minute=1),
            {"event": "auth_failure", "timestamp": "2026-11-27T10:02:00Z"},
            {"event": "auth_failure", "timestamp": "2026-11-27T10:03:00Z"},
        ])
        _, summary = await self.summary("auth_failure")
        self.assertEqual(summary["event_filter"], "auth_failure")
        self.assertEqual(summary["records"], 2)
        self.assertEqual(summary["events"], {"auth_failure": 2})
        self.assertEqual(summary["tools"], [])
        self.assertEqual(summary["first_timestamp"], "2026-11-27T10:02:00Z")
        _, summary = await self.summary("no_such_event")
        self.assertEqual((summary["records"], summary["events"], summary["tools"]), (0, {}, []))

    async def test_a_missing_file_is_an_empty_summary(self):
        _, summary = await self.summary()
        self.assertEqual(summary["records"], 0)
        self.assertEqual(summary["events"], {})
        self.assertEqual(summary["tools"], [])
        self.assertIsNone(summary["first_timestamp"])
        self.assertIsNone(summary["last_timestamp"])

    async def test_rotation_resets_the_summary_to_the_current_file(self):
        _write(self.path, [_call("ha_get_state", minute=minute) for minute in range(5)])
        _, before = await self.summary()
        self.assertEqual(before["records"], 5)
        self.path.replace(self.path.with_name("audit.jsonl.1"))
        _write(self.path, [_call("get_audit_log", minute=30)])
        _, after = await self.summary()
        self.assertEqual(after["records"], 1)
        self.assertEqual(after["tools"], [{"tool": "get_audit_log", "calls": 1, "result_status": {"success": 1}}])

    async def test_undecodable_unparsed_and_partial_rows_are_counted_or_left(self):
        with self.path.open("wb") as stream:
            stream.write(json.dumps(_call("ha_get_state")).encode() + b"\n")
            stream.write(b"\xff\xfe not utf-8\n")
            stream.write(b"not json\n")
            stream.write(b"[1, 2]\n")
            stream.write(b"\n")
            stream.write(b'{"event": "tool_call", "padding": "' + b"p" * (64 * 1024) + b'"}\n')
            stream.write(json.dumps(_call("ha_get_state", "error")).encode() + b"\n")
            # A row still being written: no newline yet, so it is not counted.
            stream.write(json.dumps(_call("half_written")).encode())
        _, summary = await self.summary()
        self.assertEqual(summary["records"], 2)
        self.assertEqual(summary["skipped_undecodable"], 1)
        # not json, a non-object, and the oversized row; the blank row is not a record.
        self.assertEqual(summary["skipped_unparsed"], 3)
        self.assertEqual([item["tool"] for item in summary["tools"]], ["ha_get_state"])

    async def test_a_summary_writes_nothing(self):
        _write(self.path, [_call("ha_get_state")])
        before = self.path.read_bytes()
        stat = self.path.stat()
        await self.summary()
        await self.summary("tool_call")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, stat.st_mtime_ns)
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["audit.jsonl"])


class SummaryBoundTests(_SummaryCase):
    async def test_at_most_200_tools_ordered_by_calls_then_name_the_rest_counted(self):
        records = []
        for index in range(250):
            records.extend(_call(f"tool_{index:03d}") for _ in range(1 + (index % 3)))
        _write(self.path, records)
        _, summary = await self.summary()
        tools = summary["tools"]
        self.assertEqual(len(tools), compatibility.MAX_AUDIT_SUMMARY_TOOLS)
        self.assertEqual(tools, sorted(tools, key=lambda item: (-item["calls"], item["tool"])))
        self.assertEqual(summary["tools_total"], 250)
        self.assertEqual(summary["tools_omitted"], 50)
        kept = sum(item["calls"] for item in tools)
        self.assertEqual(kept + summary["calls_omitted"], len(records))
        self.assertEqual(summary["events"], {"tool_call": len(records)})

    async def test_names_are_truncated_and_missing_names_are_unknown(self):
        long_name = "t" * 500
        _write(self.path, [
            _call(long_name, "s" * 300),
            {"event": "tool_call", "result_status": "success"},
            {"event": "tool_call", "tool_name": 7, "result_status": None},
            {"tool_name": "no_event"},
            {"event": "e" * 300},
        ])
        rendered, summary = await self.summary()
        limit = compatibility.MAX_AUDIT_SUMMARY_NAME_CHARS
        self.assertNotIn("t" * (limit + 1), rendered)
        self.assertNotIn("s" * (limit + 1), rendered)
        self.assertNotIn("e" * (limit + 1), rendered)
        by_tool = {item["tool"]: item for item in summary["tools"]}
        self.assertEqual(set(by_tool), {"t" * limit, "unknown"})
        self.assertEqual(by_tool["t" * limit]["result_status"], {"s" * limit: 1})
        self.assertEqual(by_tool["unknown"]["calls"], 2)
        self.assertEqual(by_tool["unknown"]["result_status"], {"success": 1, "unknown": 1})
        self.assertEqual(summary["events"], {"tool_call": 3, "unknown": 1, "e" * limit: 1})

    async def test_event_and_status_maps_fold_into_other_at_their_maximum(self):
        events = compatibility.MAX_AUDIT_SUMMARY_EVENTS
        statuses = compatibility.MAX_AUDIT_SUMMARY_STATUSES
        records = [{"event": f"event_{index:02d}"} for index in range(events + 10)]
        records += [_call("ha_get_state", f"status_{index:02d}") for index in range(statuses + 5)]
        _write(self.path, records)
        _, summary = await self.summary()
        self.assertLessEqual(len(summary["events"]), events + 1)
        self.assertEqual(sum(summary["events"].values()), len(records))
        self.assertIn("other", summary["events"])
        result_status = summary["tools"][0]["result_status"]
        self.assertEqual(len(result_status), statuses + 1)
        self.assertEqual(result_status["other"], 5)
        self.assertEqual(sum(result_status.values()), statuses + 5)

    async def test_tools_past_the_tracked_maximum_are_counted_not_named(self):
        tracked = 40
        with patch.object(compatibility, "MAX_AUDIT_SUMMARY_TRACKED_TOOLS", tracked):
            _write(self.path, [_call(f"tool_{index:03d}") for index in range(tracked + 7)])
            _write(self.path, [_call("tool_000")], mode="a")
            _, summary = await self.summary()
        self.assertEqual(summary["tools_total"], tracked)
        self.assertEqual(summary["untracked_tool_calls"], 7)
        self.assertEqual(summary["tools"][0], {"tool": "tool_000", "calls": 2, "result_status": {"success": 2}})
        self.assertEqual(summary["events"], {"tool_call": tracked + 8})

    async def test_the_response_limit_drops_the_least_called_tools_and_counts_them(self):
        records = []
        for index in range(60):
            records.extend(_call(f"tool_{index:03d}_{'n' * 100}") for _ in range(60 - index))
        _write(self.path, records)
        _, full = await self.summary()
        self.assertEqual(len(full["tools"]), 60)
        self.limit(4_096)
        rendered, summary = await self.summary()
        self.assertLessEqual(len(rendered.encode("utf-8")), 4_096)
        kept = len(summary["tools"])
        self.assertGreater(kept, 0)
        self.assertLess(kept, 60)
        self.assertEqual(summary["tools"], full["tools"][:kept])
        self.assertEqual(summary["tools_omitted"], 60 - kept)
        self.assertEqual(sum(item["calls"] for item in full["tools"][kept:]), summary["calls_omitted"])
        self.assertEqual(summary["tools_total"], 60)
        self.assertEqual((summary["events"], summary["events_omitted"]), ({"tool_call": len(records)}, 0))

    async def test_a_long_event_and_a_full_events_map_fit_the_smallest_limit(self):
        # Event names that fill the map, each called a different number of
        # times, plus tools; at the smallest allowed limit nothing named fits.
        records = []
        for index in range(compatibility.MAX_AUDIT_SUMMARY_EVENTS + 8):
            records.extend({"event": f"{index:02d}{'e' * 200}"} for _ in range(1 + index % 4))
        records += [_call(f"tool_{index:02d}_{'n' * 100}") for index in range(30)]
        _write(self.path, records)
        _, full = await self.summary()
        self.assertEqual(full["events_omitted"], 0)
        self.limit(1_024)
        for event in ("", "e" * 100_000, "\U0001F600" * 100_000, "\x01" * 100_000):
            with self.subTest(event=event[:1]):
                rendered, summary = await self.summary(event)
                self.assertLessEqual(len(rendered.encode("utf-8")), 1_024)
                self.assertNotIn("hamcp518-record-content-marker", rendered)
                if event:
                    self.assertEqual(summary["records"], 0)
                    filter_ = summary["event_filter"]
                    self.assertTrue(event.startswith(filter_))
                    self.assertLessEqual(len(json.dumps(filter_)), compatibility.MAX_AUDIT_SUMMARY_NAME_CHARS + 2)
                    continue
                self.assertEqual(summary["records"], len(records))
                self.assertEqual(sum(summary["events"].values()), len(records))
                self.assertEqual((summary["tools"], summary["tools_omitted"]), ([], 30))
                self.assertEqual(summary["calls_omitted"], 30)
                self.assertGreater(summary["events_omitted"], 0)
                named = {name: count for name, count in summary["events"].items() if name != "other"}
                # The most-counted named events are kept; the rest are folded.
                full_named = sorted(
                    ((name, count) for name, count in full["events"].items() if name != "other"),
                    key=lambda item: (-item[1], item[0]),
                )
                self.assertEqual(sorted(named.items(), key=lambda item: (-item[1], item[0])),
                                 full_named[:len(named)])
                self.assertEqual(summary["events_omitted"], len(full_named) - len(named))

    async def test_the_carried_strings_are_bounded_as_json(self):
        stamp = "\U0001F600" * 100
        _write(self.path, [{"event": "auth_failure", "timestamp": stamp}])
        self.limit(1_024)
        rendered, summary = await self.summary("\x01" * 500)
        self.assertEqual(summary["records"], 0)
        _, summary = await self.summary()
        for key in ("first_timestamp", "last_timestamp"):
            self.assertTrue(stamp.startswith(summary[key]))
            self.assertLessEqual(len(json.dumps(summary[key])), 64 + 2)


class SummaryRequestTests(_SummaryCase):
    async def test_the_default_page_is_unchanged(self):
        _write(self.path, [_call(f"tool_{index}", minute=index) for index in range(5)])
        default = await compatibility.get_audit_log(lines=3)
        explicit = await compatibility.get_audit_log(lines=3, summary=False)
        self.assertEqual(default, explicit)
        self.assertNotIn(compatibility.AUDIT_SUMMARY_KEY, default)
        self.assertEqual([row["tool_name"] for row in audit_page_rows(default)], ["tool_2", "tool_3", "tool_4"])
        split_audit_page(default)

    async def test_a_summary_with_a_cursor_is_refused(self):
        _write(self.path, [_call("ha_get_state") for _ in range(5)])
        page = split_audit_page(await compatibility.get_audit_log(lines=1))[1]
        cursor = page["next_cursor"]
        self.assertIsNotNone(cursor)
        for value in (cursor, "hamcp518-synthetic-cursor"):
            with self.subTest(cursor=value[:20]):
                with self.assertRaises(GovernanceError) as raised:
                    await compatibility.get_audit_log(cursor=value, summary=True)
                self.assertEqual(raised.exception.code, ErrorCode.VALIDATION_FAILURE)
                self.assertEqual(
                    raised.exception.details, {"field": "cursor", "reason": "not_allowed_with_summary"}
                )

    async def test_a_non_boolean_summary_is_refused_on_the_sdk_path_before_any_read(self):
        # The SDK coerces a declared bool, so prove the refusal on its own
        # argument validation, not by calling the function directly.
        _write(self.path, [_call("ha_get_state")])
        tool = compatibility.mcp._tool_manager.get_tool("get_audit_log")
        self.assertEqual(tool.parameters["properties"]["summary"],
                         {"default": False, "title": "Summary", "type": "boolean"})

        async def run(value):
            return await tool.fn_metadata.call_fn_with_arg_validation(
                tool.fn, tool.is_async, {"summary": value}, None)

        for value in ("yes", "true", 1, "no", "false", 0, None, [True], {"value": True}):
            with self.subTest(value=value):
                with patch.object(compatibility, "_audit_summary", side_effect=AssertionError("summary ran")), \
                        patch.object(compatibility, "open", side_effect=AssertionError("log read"), create=True):
                    with self.assertRaises(GovernanceError) as raised:
                        await run(value)
                self.assertEqual(raised.exception.code, ErrorCode.VALIDATION_FAILURE)
                self.assertEqual(raised.exception.details, {"field": "summary", "reason": "not_a_boolean"})
        self.assertIn(compatibility.AUDIT_SUMMARY_KEY, await run(True))
        self.assertNotIn(compatibility.AUDIT_SUMMARY_KEY, await run(False))


class SummaryGatewayAuditTests(_SummaryCase):
    SECRET = "hamcp518-synthetic-secret-0123456789abcdef"

    def gateway(self, gateway_audit: Path):
        from tests.test_hamcp475_audit_log_paging import GatewayAuditTests

        return GatewayAuditTests.gateway(self, gateway_audit)

    async def call(self, gateway, arguments: dict) -> dict:
        from tests.test_hamcp475_audit_log_paging import GatewayAuditTests

        return await GatewayAuditTests.call(self, gateway, arguments)

    async def test_the_flag_is_audited_as_a_bool_and_the_cursor_never(self):
        _write(self.path, [_call("ha_get_state") for _ in range(5)])
        gateway_audit = self.path.parent / "gateway-audit.jsonl"
        gateway = self.gateway(gateway_audit)
        marker = "hamcp518-synthetic-cursor-marker"
        calls = [
            ({"summary": True}, True),
            ({"summary": False}, False),
            ({}, False),
            ({"summary": True, "cursor": marker}, True),
            ({"summary": "yes"}, None),
        ]
        results = []
        for arguments, _ in calls:
            results.append(await self.call(gateway, {"lines": 5, **arguments}))
        summary = json.loads(results[0]["text"])[compatibility.AUDIT_SUMMARY_KEY]
        self.assertEqual(summary["tools"][0]["tool"], "ha_get_state")
        self.assertEqual(results[3], {"error": ErrorCode.VALIDATION_FAILURE.value})

        stored = gateway_audit.read_text(encoding="utf-8")
        records = [json.loads(line) for line in stored.splitlines()]
        audited = [record["parameters"] for record in records if record.get("tool_name") == "get_audit_log"]
        self.assertEqual(len(audited), len(calls))
        self.assertEqual([item["summary"] for item in audited], [expected for _, expected in calls])
        for item in audited:
            self.assertNotIn("cursor", item)
        self.assertEqual([item["cursor_present"] for item in audited], [False, False, False, True, False])
        self.assertNotIn(marker, stored)
        self.assertNotIn('"yes"', stored)
        self.assertNotIn(self.SECRET, stored)

    async def test_a_non_boolean_flag_on_the_sdk_path_is_a_failure_audited_as_none(self):
        from mcp.server.fastmcp.exceptions import ToolError

        _write(self.path, [_call("ha_get_state")])
        gateway_audit = self.path.parent / "gateway-audit.jsonl"
        gateway = self.gateway(gateway_audit)

        async def app(scope, receive, send):
            # Runs the registered tool through the SDK's own argument
            # validation and renders the result or error as the SDK does.
            body = b""
            while True:
                message = await receive()
                body += message.get("body", b"")
                if not message.get("more_body"):
                    break
            request = json.loads(body)
            try:
                text = await compatibility.mcp._tool_manager.call_tool(
                    "get_audit_log", request["params"]["arguments"])
                result = {"content": [{"type": "text", "text": text}], "isError": False}
            except ToolError as error:
                result = {"content": [{"type": "text", "text": str(error)}], "isError": True}
            payload = {"jsonrpc": "2.0", "id": request["id"], "result": result}
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/event-stream")]})
            await send({"type": "http.response.body",
                        "body": f"event: message\r\ndata: {json.dumps(payload)}\r\n\r\n".encode()})

        gateway.app = app
        token = "hamcp518-synthetic-flag-token"
        sent = [{"summary": token}, {"summary": 1}, {"summary": True}]
        import httpx

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway), base_url="http://127.0.0.1:8100"
        ) as client:
            for index, arguments in enumerate(sent):
                response = await client.post(f"/{self.SECRET}/mcp", json={
                    "jsonrpc": "2.0", "id": f"hamcp518-{index}", "method": "tools/call",
                    "params": {"name": "get_audit_log", "arguments": arguments},
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual('"isError": true' in response.text, index < 2)
        stored = gateway_audit.read_text(encoding="utf-8")
        records = [json.loads(line) for line in stored.splitlines()]
        audited = [record for record in records if record.get("tool_name") == "get_audit_log"]
        self.assertEqual([record["parameters"]["summary"] for record in audited], [None, None, True])
        self.assertEqual([record["result_status"] for record in audited], ["failure", "failure", "success"])
        self.assertNotIn(token, stored)
        for record in audited:
            self.assertNotIn("cursor", record["parameters"])


if __name__ == "__main__":
    unittest.main()
