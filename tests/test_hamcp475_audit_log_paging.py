"""HAMCP-475: get_audit_log pages fit the response limit and a cursor walks the log.

A client walks from the newest page to the oldest by passing each trailer's
``next_cursor`` until ``complete`` is true. These tests prove the walk returns
every record exactly once, in order, within the limit, and that the cursor is
signed, bound to the file, the event filter and the unchanged bytes before its
offset, and carries no record content.
"""

from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tests.test_beta_v2  # noqa: F401  (puts the Engineering package on sys.path)
from tests.support.audit_pages import audit_page_message, audit_page_rows, split_audit_page

_REAL_FSTAT = os.fstat

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


def _same_shape(index: int, fill: str) -> dict:
    """A different record with exactly the byte length of ``_record(index)``."""

    return {**_record(index), "padding": fill * 120}


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
        # The undecodable row is disclosed once across the walk, on the older
        # page, whose byte range holds it; malformed JSON is not counted.
        self.assertEqual([page.get("skipped_undecodable", 0) for _, _, page in pages], [0, 1])

    async def test_undecodable_rows_are_counted_once_across_a_walk(self):
        with self.path.open("wb") as stream:
            for i in range(30):
                stream.write(json.dumps(_record(i)).encode() + b"\n")
                if i % 7 == 3:
                    stream.write(b'{"event": "tool_call", "bad": "\xff"}\n')
        self.limit(2_048)
        pages = await self.walk(lines=4)
        self.assertGreater(len(pages), 2)
        self.assertEqual(sum(page.get("skipped_undecodable", 0) for _, _, page in pages), 4)
        rows = [row for _, page_rows, _ in reversed(pages) for row in page_rows]
        self.assertEqual([row["index"] for row in rows], list(range(30)))

    async def test_counting_undecodable_rows_costs_linear_work_while_pages_shrink(self):
        """Codex R493-05: a page that shrinks to fit must not rescan the skipped rows.

        The work is counted as line events traced in compatibility.py, not timed.
        A loop iteration is a line event even when it stays on one line, so a
        rescan that only filters (yields nothing) is still counted. Each extra
        non-UTF-8 row may add a fixed amount of work; a per-candidate rescan adds
        work for every row on every one of the ~500 shrink steps (Codex R493-06).
        """

        import sys

        def build(undecodable: int) -> None:
            with self.path.open("wb") as stream:
                stream.write(b'{"bad": "\xff"}\n' * undecodable)
                for i in range(500):
                    stream.write(json.dumps(_record(i, pad=300)).encode() + b"\n")

        async def work(undecodable: int) -> tuple[int, dict]:
            build(undecodable)
            events = 0
            module_file = compatibility.__file__

            def count_lines(frame, event, arg):
                nonlocal events
                if event == "line":
                    events += 1
                return count_lines

            def trace(frame, event, arg):
                # Only frames of compatibility.py, including its generator
                # expressions, are line-traced.
                return count_lines if frame.f_code.co_filename == module_file else None

            previous = sys.gettrace()
            sys.settrace(trace)
            try:
                rendered = await compatibility.get_audit_log(lines=500)
            finally:
                sys.settrace(previous)
            return events, split_audit_page(rendered)[1]

        self.limit(1_024)
        baseline, clean_page = await work(0)
        loaded, loaded_page = await work(2_000)
        # One row fits, so the page shrank through ~500 candidates either way.
        self.assertEqual(clean_page["returned"], 1)
        self.assertEqual(loaded_page["returned"], 1)
        self.assertNotIn("skipped_undecodable", loaded_page)
        self.assertLess(loaded - baseline, 40 * 2_000)
        # The skipped rows all precede the newest page, so the walk reports
        # every one of them exactly once, on its oldest page.
        pages = await self.walk(lines=500)
        self.assertEqual(sum(page.get("skipped_undecodable", 0) for _, _, page in pages), 2_000)
        self.assertEqual(pages[-1][2]["skipped_undecodable"], 2_000)
        for rendered, _, _ in pages:
            self.assert_within_limit(rendered)
        rows = [row for _, page_rows, _ in reversed(pages) for row in page_rows]
        self.assertEqual([row["index"] for row in rows], list(range(500)))

    async def test_an_undecodable_only_log_reports_its_count(self):
        with self.path.open("wb") as stream:
            stream.write(b'{"event": "tool_call", "bad": "\xff"}\n' * 2)
        rendered = await compatibility.get_audit_log()
        self.assertTrue(rendered.startswith("No matching entries.\n"))
        self.assertEqual(split_audit_page(rendered)[1]["skipped_undecodable"], 2)


class SnapshotBoundaryTests(_AuditLogCase):
    async def test_a_row_completed_after_the_snapshot_is_left_for_the_next_walk(self):
        _write(self.path, (_record(i) for i in range(3)))
        tail = json.dumps(_record(3), sort_keys=True) + "\n"
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(tail[:40])

        def fstat_then_finish_the_row(fd):
            # The writer completes the row after the size is captured and
            # before the reader reaches it.
            result = _REAL_FSTAT(fd)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(tail[40:])
            return result

        with patch.object(compatibility.os, "fstat", side_effect=fstat_then_finish_the_row):
            rows = audit_page_rows(await compatibility.get_audit_log(lines=500))
        self.assertEqual([row["index"] for row in rows], [0, 1, 2])
        rows = [row for _, page_rows, _ in reversed(await self.walk()) for row in page_rows]
        self.assertEqual([row["index"] for row in rows], [0, 1, 2, 3])

    async def test_an_unterminated_last_row_is_not_returned(self):
        _write(self.path, (_record(i) for i in range(2)))
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(_record(2), sort_keys=True))
        rows = audit_page_rows(await compatibility.get_audit_log(lines=500))
        self.assertEqual([row["index"] for row in rows], [0, 1])


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
                self.assertEqual(set(payload), {"v", "file", "event", "before", "prefix"})
                self.assertEqual(payload["v"], 1)
                self.assertIsInstance(payload["before"], int)
                self.assertRegex(payload["file"], r"^[0-9a-f]{32}$")
                self.assertRegex(payload["event"], r"^[0-9a-f]{32}$")
                self.assertRegex(payload["prefix"], r"^[0-9a-f]{32}$")
                # The prefix digest is keyed: it is not the plain hash of the
                # bytes before the offset.
                prefix_bytes = self.path.read_bytes()[: payload["before"]]
                self.assertNotEqual(
                    payload["prefix"], hashlib.sha256(prefix_bytes).hexdigest()[:32]
                )
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
                {"before": 0, "event": "0" * 32, "file": "0" * 32, "prefix": "0" * 32, "v": 1},
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

    async def test_signed_cursors_with_mistyped_fields_are_invalid(self):
        cursor = await self._cursor()
        payload = json.loads(
            base64.urlsafe_b64decode(cursor.split(".", 1)[0] + "==")
        )

        def signed(value):
            body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            signature = hmac.new(compatibility._AUDIT_CURSOR_KEY, body, hashlib.sha256).digest()
            return ".".join(
                base64.urlsafe_b64encode(part).decode().rstrip("=") for part in (body, signature)
            )

        # The signing helper reproduces a real cursor, so each refusal below
        # comes from the field check, not the signature.
        self.assertEqual(signed(payload), cursor)
        for name, change in {
            "boolean version": {"v": True},
            "string version": {"v": "1"},
            "numeric file": {"file": 0},
            "null event": {"event": None},
            "numeric prefix": {"prefix": 0},
            "boolean offset": {"before": True},
        }.items():
            with self.subTest(case=name):
                await self.assert_refused(
                    ErrorCode.INVALID_CURSOR, lines=500, cursor=signed({**payload, **change})
                )

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
        with self.subTest(case="rotated twice onto a reused inode"):
            # Production rotation is os.replace onto audit.jsonl.1. A second
            # rotation frees the inode the cursor names, and the filesystem
            # may give it to the new log. Pin the identity to model that reuse:
            # only the prefix digest can then tell the files apart.
            identity = compatibility._audit_file_identity(os.stat(self.path))
            with patch.object(compatibility, "_audit_file_identity", return_value=identity):
                for fill in ("q", "r"):
                    os.replace(self.path, str(self.path) + ".1")
                    # Same row lengths, so every offset still lands on a row
                    # boundary.
                    _write(self.path, (_same_shape(i, fill) for i in range(400)))
                await self.assert_refused(ErrorCode.STALE_CURSOR, lines=500, cursor=cursor)
        cursor = await self._cursor()
        with self.subTest(case="truncated in place"):
            with self.path.open("r+", encoding="utf-8") as stream:
                stream.truncate(10)
            await self.assert_refused(ErrorCode.STALE_CURSOR, lines=500, cursor=cursor)
        cursor = await self._cursor()
        with self.subTest(case="rewritten and regrown in place"):
            # Same file identity, row boundaries and size, but different
            # records before the cursor's offset.
            with self.path.open("r+", encoding="utf-8") as stream:
                stream.truncate(0)
                stream.writelines(
                    json.dumps(_same_shape(i, "q"), sort_keys=True) + "\n" for i in range(420)
                )
            await self.assert_refused(ErrorCode.STALE_CURSOR, lines=500, cursor=cursor)
        cursor = await self._cursor()
        with self.subTest(case="appended"):
            _write(self.path, (_record(i) for i in range(400, 410)), mode="a")
            rendered = await compatibility.get_audit_log(lines=500, cursor=cursor)
            self.assertTrue(audit_page_rows(rendered))
        cursor = await self._cursor()
        with self.subTest(case="removed"):
            self.path.unlink()
            await self.assert_refused(ErrorCode.STALE_CURSOR, lines=500, cursor=cursor)



class PageParserTests(unittest.TestCase):
    """The shared test oracle rejects malformed trailer values."""

    def test_malformed_trailers_are_rejected(self):
        cases = {
            "numeric cursor": {"returned": 1, "next_cursor": 7, "complete": False},
            "empty cursor": {"returned": 1, "next_cursor": "", "complete": False},
            "overlong cursor": {"returned": 1, "next_cursor": "A" * 513, "complete": False},
            "negative returned": {"returned": -1, "next_cursor": None, "complete": True},
            "boolean returned": {"returned": True, "next_cursor": None, "complete": True},
            "zero omitted": {
                "returned": 0, "next_cursor": None, "complete": True, "omitted_oversized": 0
            },
            "negative omitted": {
                "returned": 0, "next_cursor": None, "complete": True, "omitted_oversized": -1
            },
            "unknown field": {"returned": 0, "next_cursor": None, "complete": True, "x": 1},
            "disagreement": {"returned": 0, "next_cursor": None, "complete": False},
        }
        for name, page in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(AssertionError):
                    split_audit_page("No matching entries.\n" + json.dumps({"_page": page}) + "\n")

    def test_well_formed_trailers_are_accepted(self):
        for page in (
            {"returned": 0, "next_cursor": None, "complete": True},
            {"returned": 2, "next_cursor": "abc.def", "complete": False},
            {"returned": 0, "next_cursor": "abc.def", "complete": False, "omitted_oversized": 1},
        ):
            split_audit_page("x\n" + json.dumps({"_page": page}) + "\n")


class GatewayAuditTests(_AuditLogCase):
    """The authenticated route records that a cursor was passed, never the cursor."""

    SECRET = "hamcp475-gateway-access-secret"

    def gateway(self, gateway_audit: Path):
        from ha_mcp_engineering.audit import AuditLogger
        from ha_mcp_engineering.configuration import Settings
        from ha_mcp_engineering.routing import AuthenticatedMcpGateway

        settings = Settings(
            ha_url="http://supervisor/core",
            ha_token="test-token",
            access_secret=self.SECRET,
            port=8100,
            audit_path=str(gateway_audit),
            rate_limit_per_minute=1000,
            rate_limit_burst=1000,
            destructive_services=frozenset(),
            audit_enabled=True,
            audit_max_payload_chars=8192,
            log_level="INFO",
            ha_timeout_seconds=60,
            response_size_limit=60_000,
            redaction_enabled=True,
        )

        async def app(scope, receive, send):
            # Stands in for the SDK: runs the real tool on the forwarded call.
            body = b""
            while True:
                message = await receive()
                body += message.get("body", b"")
                if not message.get("more_body"):
                    break
            arguments = json.loads(body)["params"]["arguments"]
            try:
                result = {"text": await compatibility.get_audit_log(**arguments)}
            except GovernanceError as error:
                result = {"error": error.code.value}
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": json.dumps(result).encode()})

        return AuthenticatedMcpGateway(
            app, settings, AuditLogger(str(gateway_audit), self.SECRET)
        )

    async def call(self, gateway, arguments: dict) -> dict:
        import httpx

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway), base_url="http://127.0.0.1:8100"
        ) as client:
            response = await client.post(
                f"/{self.SECRET}/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": "hamcp475-gateway",
                    "method": "tools/call",
                    "params": {"name": "get_audit_log", "arguments": arguments},
                },
            )
        self.assertEqual(response.status_code, 200)
        return response.json()

    async def test_no_cursor_reaches_the_audit_record_and_a_walk_still_works(self):
        _write(self.path, (_record(i) for i in range(400)))
        self.limit(4_096)
        gateway_audit = self.path.parent / "gateway-audit.jsonl"
        gateway = self.gateway(gateway_audit)

        sent = []
        returned = []
        cursor = ""
        for _ in range(1000):
            arguments = {"lines": 500, "event": ""}
            if cursor:
                arguments["cursor"] = cursor
                sent.append(cursor)
            result = await self.call(gateway, arguments)
            returned.extend(row["index"] for row in audit_page_rows(result["text"]))
            page = split_audit_page(result["text"])[1]
            if page["complete"]:
                break
            cursor = page["next_cursor"]
        self.assertEqual(sorted(returned), list(range(400)))
        self.assertGreater(len(sent), 1)

        valid = sent[0]
        payload, signature = valid.split(".", 1)
        marker = "hamcp475-synthetic-cursor-marker"
        refused = {
            "malformed": (f"{marker}.not-a-cursor", ErrorCode.INVALID_CURSOR),
            "tampered": (f"{payload}.{'A' * len(signature)}", ErrorCode.INVALID_CURSOR),
            "overlong": (marker + "A" * 600, ErrorCode.INVALID_CURSOR),
            "stale": (valid, ErrorCode.STALE_CURSOR),
        }
        for name, (value, code) in refused.items():
            with self.subTest(cursor=name):
                arguments = {"lines": 500, "cursor": value}
                if name == "stale":
                    arguments["event"] = "tool_call"
                result = await self.call(gateway, arguments)
                self.assertEqual(result, {"error": code.value})
                sent.append(value)

        stored = gateway_audit.read_text(encoding="utf-8")
        records = [json.loads(line) for line in stored.splitlines()]
        calls = [record for record in records if record.get("tool_name") == "get_audit_log"]
        self.assertEqual(len(calls), len(sent) + 1)
        for record in calls:
            self.assertNotIn("cursor", record["parameters"])
            self.assertIsInstance(record["parameters"]["cursor_present"], bool)
        self.assertEqual(
            [record["parameters"]["cursor_present"] for record in calls].count(False), 1
        )
        for value in sent:
            self.assertNotIn(value, stored)
            self.assertNotIn(value.split(".", 1)[0], stored)
        self.assertNotIn(marker, stored)
        self.assertNotIn(self.SECRET, stored)


if __name__ == "__main__":
    unittest.main()
