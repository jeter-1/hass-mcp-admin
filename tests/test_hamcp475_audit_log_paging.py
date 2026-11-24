"""HAMCP-475: get_audit_log pages fit the response limit and a cursor walks the log.

A client walks from the newest page to the oldest by passing each trailer's
``next_cursor`` until ``complete`` is true. These tests prove the walk returns
every record exactly once, in order, within the limit, and that the cursor is
signed, bound to the file and the event filter, and carries no record content.
"""

from __future__ import annotations

import base64
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tests.test_beta_v2  # noqa: F401  (puts the Engineering package on sys.path)
from tests.support.audit_pages import audit_page_message, audit_page_rows, split_audit_page

from ha_mcp_engineering.errors import ErrorCode, GovernanceError
from ha_mcp_engineering.tools import compatibility

EVENTS = ("tool_call", "auth_failure", "auth_failure_throttled", "rate_limited")


def _record(index: int, pad: int = 120) -> dict:
    return {
        "event": EVENTS[index % len(EVENTS)],
        "index": index,
        "marker": f"record-content-marker-{index:05d}",
        "padding": "p" * pad,
    }


def _write(path: Path, records, mode: str = "w") -> list[str]:
    lines = [json.dumps(record, sort_keys=True) + "\n" for record in records]
    with path.open(mode, encoding="utf-8") as stream:
        stream.writelines(lines)
    return lines


class _AuditLogCase(unittest.IsolatedAsyncioTestCase):
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

    async def walk(self, *, lines: int = 500, event: str = "", max_pages: int = 1000):
        """Every page of one walk, newest first, as ``(rendered, rows, page)``."""

        pages = []
        cursor = ""
        for _ in range(max_pages):
            rendered = await compatibility.get_audit_log(lines=lines, event=event, cursor=cursor)
            rows = audit_page_rows(rendered)
            _, page = split_audit_page(rendered)
            pages.append((rendered, rows, page))
            if page["complete"]:
                return pages
            cursor = page["next_cursor"]
        self.fail("walk did not complete")

    def assert_within_limit(self, rendered: str) -> None:
        limit = compatibility.SETTINGS.response_size_limit
        self.assertLessEqual(len(rendered.encode("utf-8")), limit)
        self.assertLessEqual(len(rendered), limit)


class FullWalkTests(_AuditLogCase):
    async def test_a_walk_returns_every_record_exactly_once_in_order_within_the_limit(self):
        written = _write(self.path, (_record(i) for i in range(1_500)))
        for limit, lines in ((60_000, 500), (60_000, 50), (4_096, 500), (1_024, 7)):
            with self.subTest(response_size_limit=limit, lines=lines):
                self.limit(limit)
                pages = await self.walk(lines=lines)
                returned = []
                for rendered, rows, page in reversed(pages):
                    self.assert_within_limit(rendered)
                    self.assertLessEqual(len(rows), lines)
                    self.assertGreater(len(rows), 0)
                    returned.extend(rows)
                self.assertEqual([row["index"] for row in returned], list(range(1_500)))
                # Each record is returned verbatim, byte for byte.
                body = "".join(
                    split_audit_page(rendered)[0] + "\n" for rendered, _, _ in reversed(pages)
                )
                self.assertEqual(body, "".join(written))
                # Every page but the oldest is full: the page holds `lines` records
                # or the next older record would not have fit.
                for rendered, rows, page in pages[:-1]:
                    self.assertFalse(page["complete"])
                    if len(rows) < lines:
                        next_record = written[returned.index(rows[0]) - 1]
                        self.assertGreater(
                            len(rendered.encode("utf-8")) + len(next_record.encode("utf-8")),
                            limit,
                        )
                self.assertTrue(pages[-1][2]["complete"])
                self.assertIsNone(pages[-1][2]["next_cursor"])

    async def test_each_event_walk_returns_exactly_that_events_records(self):
        _write(self.path, (_record(i) for i in range(800)))
        self.limit(8_192)
        covered = []
        for event in EVENTS:
            with self.subTest(event=event):
                pages = await self.walk(lines=500, event=event)
                rows = [row for _, page_rows, _ in reversed(pages) for row in page_rows]
                expected = [i for i in range(800) if EVENTS[i % len(EVENTS)] == event]
                self.assertEqual([row["index"] for row in rows], expected)
                self.assertEqual({row["event"] for row in rows}, {event})
                covered.extend(row["index"] for row in rows)
        self.assertEqual(sorted(covered), list(range(800)))

    async def test_records_appended_during_a_walk_are_not_returned_by_it(self):
        _write(self.path, (_record(i) for i in range(300)))
        self.limit(4_096)
        first = await compatibility.get_audit_log(lines=500)
        rows = audit_page_rows(first)
        _, page = split_audit_page(first)
        _write(self.path, (_record(i) for i in range(300, 320)), mode="a")
        cursor = page["next_cursor"]
        while cursor:
            rendered = await compatibility.get_audit_log(lines=500, cursor=cursor)
            rows = audit_page_rows(rendered) + rows
            cursor = split_audit_page(rendered)[1]["next_cursor"]
        self.assertEqual([row["index"] for row in rows], list(range(300)))
        self.limit(60_000)
        newest = audit_page_rows(await compatibility.get_audit_log(lines=20))
        self.assertEqual([row["index"] for row in newest], list(range(300, 320)))


class CompatibilityTests(_AuditLogCase):
    async def test_a_call_without_a_cursor_returns_the_newest_records_and_a_trailer(self):
        written = _write(self.path, (_record(i) for i in range(120)))
        rendered = await compatibility.get_audit_log()
        body, page = split_audit_page(rendered)
        self.assertEqual(body + "\n", "".join(written[-50:]))
        self.assertEqual(page["returned"], 50)
        self.assertFalse(page["complete"])
        self.assertIsInstance(page["next_cursor"], str)
        self.assertEqual(rendered.count('{"_page"'), 1)
        self.assertTrue(rendered.splitlines()[-1].startswith('{"_page"'))

    async def test_a_whole_log_that_fits_is_one_complete_page(self):
        _write(self.path, (_record(i) for i in range(10)))
        rendered = await compatibility.get_audit_log(lines=50)
        _, page = split_audit_page(rendered)
        self.assertEqual(page, {"returned": 10, "next_cursor": None, "complete": True})

    async def test_empty_pages_keep_their_messages_and_end_with_a_complete_trailer(self):
        missing = await compatibility.get_audit_log()
        self.assertEqual(audit_page_message(missing), "No audit log yet.")
        self.assertEqual(
            split_audit_page(missing)[1], {"returned": 0, "next_cursor": None, "complete": True}
        )
        _write(self.path, [_record(0)])
        unmatched = await compatibility.get_audit_log(event="rate_limited")
        self.assertEqual(audit_page_message(unmatched), "No matching entries.")
        self.assertEqual(
            split_audit_page(unmatched)[1], {"returned": 0, "next_cursor": None, "complete": True}
        )

    async def test_a_page_shrinks_to_fit_large_records(self):
        _write(self.path, (_record(i, pad=7_000) for i in range(40)))
        rendered = await compatibility.get_audit_log(lines=500)
        rows = audit_page_rows(rendered)
        self.assert_within_limit(rendered)
        self.assertLess(len(rows), 40)
        self.assertEqual([row["index"] for row in rows], list(range(40 - len(rows), 40)))
        self.assertFalse(split_audit_page(rendered)[1]["complete"])

    async def test_undecodable_and_malformed_lines_are_skipped_on_every_page(self):
        with self.path.open("wb") as stream:
            stream.write(json.dumps(_record(0)).encode() + b"\n")
            stream.write(b'{"event": "tool_call", "bad": "\xff\xfe"}\n')
            stream.write(b"not json\n")
            stream.write(json.dumps(_record(4)).encode() + b"\n")
        self.limit(1_024)
        pages = await self.walk(lines=1)
        rows = [row for _, page_rows, _ in reversed(pages) for row in page_rows]
        self.assertEqual([row["index"] for row in rows], [0, 4])
        self.assertEqual(len(pages), 2)


class OversizedRecordTests(_AuditLogCase):
    async def test_a_record_larger_than_the_limit_is_skipped_whole_and_disclosed(self):
        records = [_record(0), _record(1, pad=2_000), _record(2)]
        _write(self.path, records)
        self.limit(1_024)
        pages = await self.walk(lines=1)
        rows = [row for _, page_rows, _ in reversed(pages) for row in page_rows]
        self.assertEqual([row["index"] for row in rows], [0, 2])
        oversized = [page for _, _, page in pages if page.get("omitted_oversized")]
        self.assertEqual(len(oversized), 1)
        self.assertEqual(oversized[0]["returned"], 0)
        self.assertEqual(oversized[0]["omitted_oversized"], 1)
        for rendered, _, _ in pages:
            self.assert_within_limit(rendered)
            self.assertNotIn("record-content-marker-00001", rendered)
        # The key appears only when a record was skipped.
        self.assertTrue(
            all("omitted_oversized" not in page for _, rows, page in pages if rows)
        )


class CursorContentTests(_AuditLogCase):
    async def test_the_cursor_carries_no_record_content(self):
        _write(self.path, (_record(i) for i in range(400)))
        self.limit(4_096)
        for event in ("", "auth_failure"):
            pages = await self.walk(lines=500, event=event)
            cursors = [page["next_cursor"] for _, _, page in pages if page["next_cursor"]]
            self.assertTrue(cursors)
            for cursor in cursors:
                payload_part = cursor.split(".", 1)[0]
                payload = json.loads(
                    base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4))
                )
                self.assertEqual(set(payload), {"v", "file", "event", "before"})
                self.assertEqual(payload["v"], 1)
                self.assertIsInstance(payload["before"], int)
                self.assertRegex(payload["file"], r"^[0-9a-f]{32}$")
                self.assertRegex(payload["event"], r"^[0-9a-f]{32}$")
                decoded = json.dumps(payload) + cursor
                for forbidden in ("record-content-marker", "padding", "tool_call", str(self.path)):
                    self.assertNotIn(forbidden, decoded)
                if event:
                    self.assertNotIn(event, decoded)
                self.assertLessEqual(len(cursor), compatibility.MAX_AUDIT_CURSOR_CHARS)


class CursorRefusalTests(_AuditLogCase):
    async def _cursor(self, event: str = "") -> str:
        _write(self.path, (_record(i) for i in range(400)))
        self.limit(4_096)
        rendered = await compatibility.get_audit_log(lines=500, event=event)
        cursor = split_audit_page(rendered)[1]["next_cursor"]
        self.assertIsInstance(cursor, str)
        return cursor

    async def assert_refused(self, code: ErrorCode, **arguments) -> None:
        with self.assertRaises(GovernanceError) as raised:
            await compatibility.get_audit_log(**arguments)
        self.assertEqual(raised.exception.code, code)

    async def test_malformed_tampered_and_foreign_cursors_are_invalid(self):
        cursor = await self._cursor()
        payload, signature = cursor.split(".", 1)
        flipped = "A" if signature[0] != "A" else "B"
        other_payload = base64.urlsafe_b64encode(
            json.dumps(
                {"before": 0, "event": "0" * 32, "file": "0" * 32, "v": 1},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).decode().rstrip("=")
        cases = {
            "garbage": "not-a-cursor",
            "no signature": payload,
            "empty signature": payload + ".",
            "tampered signature": payload + "." + flipped + signature[1:],
            "swapped payload": other_payload + "." + signature,
            "padded": payload + "==." + signature,
            "overlong": "A" * (compatibility.MAX_AUDIT_CURSOR_CHARS + 1),
        }
        for name, value in cases.items():
            with self.subTest(case=name):
                await self.assert_refused(ErrorCode.INVALID_CURSOR, lines=500, cursor=value)
        with self.subTest(case="another process's key"):
            with patch.object(compatibility, "_AUDIT_CURSOR_KEY", b"k" * 32):
                await self.assert_refused(ErrorCode.INVALID_CURSOR, lines=500, cursor=cursor)

    async def test_an_invalid_cursor_is_refused_before_the_log_is_read(self):
        with patch.object(compatibility, "open", side_effect=AssertionError("read"), create=True):
            with patch.object(compatibility.os.path, "exists", side_effect=AssertionError("stat")):
                await self.assert_refused(ErrorCode.INVALID_CURSOR, cursor="not-a-cursor")

    async def test_a_changed_event_filter_is_stale(self):
        cursor = await self._cursor(event="tool_call")
        for event in ("", "auth_failure", "Tool_call"):
            with self.subTest(event=event):
                await self.assert_refused(
                    ErrorCode.STALE_CURSOR, lines=500, event=event, cursor=cursor
                )
        rendered = await compatibility.get_audit_log(lines=500, event="tool_call", cursor=cursor)
        self.assertTrue(audit_page_rows(rendered))

    async def test_a_changed_page_size_is_allowed(self):
        cursor = await self._cursor()
        rendered = await compatibility.get_audit_log(lines=3, cursor=cursor)
        self.assertEqual(len(audit_page_rows(rendered)), 3)

    async def test_rotation_truncation_and_removal_make_a_cursor_stale(self):
        cursor = await self._cursor()
        with self.subTest(case="rotated"):
            rotated = self.path.with_name("audit.jsonl.new")
            _write(rotated, (_record(i) for i in range(400)))
            os.replace(self.path, str(self.path) + ".1")
            os.replace(rotated, self.path)
            await self.assert_refused(ErrorCode.STALE_CURSOR, lines=500, cursor=cursor)
        cursor = await self._cursor()
        with self.subTest(case="truncated in place"):
            with self.path.open("r+", encoding="utf-8") as stream:
                stream.truncate(10)
            await self.assert_refused(ErrorCode.STALE_CURSOR, lines=500, cursor=cursor)
        cursor = await self._cursor()
        with self.subTest(case="removed"):
            self.path.unlink()
            await self.assert_refused(ErrorCode.STALE_CURSOR, lines=500, cursor=cursor)


if __name__ == "__main__":
    unittest.main()
