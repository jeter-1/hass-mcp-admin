"""HAMCP-518: get_audit_log summary=true counts the whole current audit file.

A summary is derived in one pass over the same whole-row snapshot a page
reads, and writes nothing. These tests prove its counts, that every name and
map it reports is bounded, that it fits the response limit by counting the
tools it leaves out, that the default page is unchanged, that a summary with
a cursor is refused, and that the gateway audits the flag as a bool and never
the cursor.
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
    "events", "tools", "tools_total", "tools_omitted", "calls_omitted",
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


if __name__ == "__main__":
    unittest.main()
