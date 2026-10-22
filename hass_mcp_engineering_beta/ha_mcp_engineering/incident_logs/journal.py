"""Pure parsing of Supervisor's verbose journal text (no I/O, no sanitizing).

Supervisor 2026.09.1 ``journal_verbose_formatter`` writes one text line per
journal entry: ``"{ts} {_HOSTNAME} {SYSLOG_IDENTIFIER}[{_PID}]: {MESSAGE}"``
(``[{_PID}]`` absent without a PID), where ``ts`` is UTC
``YYYY-MM-DD HH:MM:SS.mmm`` truncated to milliseconds. A binary MESSAGE may
hold newlines, so one entry can span several text lines; a line that does
not start with the prefix continues the previous entry. A miscount (a
message line that imitates the prefix) is caught by the overlap check of
the next page, which then fails closed.

gatewayd v257.9 ``Range: entries=<cursor>[[:<skip>]:<count>]`` seeks the
cursor, then moves ``skip`` matching entries (``previous_skip(|skip|+1)``
when negative, ``next_skip(skip+1)`` when positive, ``next()`` at 0) and
returns ``count`` entries forward. Supervisor forwards the header verbatim
and returns the first entry's cursor as ``X-First-Cursor``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re

from . import contracts as c


_PREFIX = re.compile(
    r"(?P<ts>(?P<date>\d{4}-\d{2}-\d{2}) (?P<time>\d{2}:\d{2}:\d{2})\.(?P<ms>\d{3})) "
    r"(?P<host>[^ ]*) (?P<process>[^ ]+?): "
)
_JOURNAL_CURSOR = re.compile(r"[a-z]=[0-9a-f]{1,64}(?:;[a-z]=[0-9a-f]{1,64}){0,7}\Z")
MAX_JOURNAL_CURSOR = 512

# Fixed level vocabularies; a match is a decision only, never output text.
_LEVEL_FORMS = (
    # Home Assistant Core / Supervisor Python logging.
    re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:[.,]\d+)? (DEBUG|INFO|WARNING|ERROR|CRITICAL|FATAL)\b"),
    # bashio add-on logging.
    re.compile(r"\[\d{2}:\d{2}:\d{2}\] (TRACE|DEBUG|INFO|NOTICE|WARNING|WARN|ERROR|FATAL)\b"),
    # Python's default and uvicorn formats.
    re.compile(r"(DEBUG|INFO|WARNING|ERROR|CRITICAL)(?::| {2,})"),
)
_LEVEL_NAMES = {
    "TRACE": "debug", "DEBUG": "debug", "INFO": "info", "NOTICE": "info",
    "WARN": "warning", "WARNING": "warning", "ERROR": "error",
    "CRITICAL": "critical", "FATAL": "critical",
}


class JournalShapeError(ValueError):
    """The page text does not have the pinned verbose shape."""


@dataclass(frozen=True)
class RawEntry:
    """One journal entry as fetched: raw text, never returned unsanitized."""

    ts_ms: int
    lines: tuple[str, ...]
    raw: bytes = field(repr=False)


def valid_journal_cursor(value: object) -> str | None:
    """A journal cursor that is safe inside a ``Range`` header, or ``None``."""

    if (
        type(value) is str and 0 < len(value) <= MAX_JOURNAL_CURSOR
        and _JOURNAL_CURSOR.fullmatch(value)
    ):
        return value
    return None


def range_header(cursor: str | None, skip: int, count: int) -> str:
    if cursor is not None and valid_journal_cursor(cursor) is None:
        raise ValueError("unsafe journal cursor")
    if type(skip) is not int or type(count) is not int or count < 1:
        raise ValueError("invalid journal range")
    return f"entries={cursor or ''}:{skip}:{count}"


def parse_ts_ms(date: str, time: str, ms: str) -> int:
    moment = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(moment.timestamp()) * 1000 + int(ms)


def parse_page(text: str) -> list[RawEntry]:
    """Split complete page text (ending in a newline or empty) into entries."""

    if not text:
        return []
    if not text.endswith("\n"):
        raise JournalShapeError("incomplete_page")
    entries: list[RawEntry] = []
    current: list[str] = []
    current_ts = 0
    for line in text[:-1].split("\n"):
        match = _PREFIX.match(line)
        if match:
            if current:
                entries.append(_entry(current_ts, current))
            current = [line]
            try:
                current_ts = parse_ts_ms(match["date"], match["time"], match["ms"])
            except ValueError:
                raise JournalShapeError("timestamp_invalid") from None
        elif current:
            current.append(line)
        else:
            raise JournalShapeError("page_starts_mid_entry")
    if current:
        entries.append(_entry(current_ts, current))
    return entries


def _entry(ts_ms: int, lines: list[str]) -> RawEntry:
    return RawEntry(ts_ms, tuple(lines), "\n".join(lines).encode("utf-8"))


def split_prefix(first_line: str) -> tuple[str | None, str]:
    """``(process, message)`` of a sanitized first line; no process if altered."""

    match = _PREFIX.match(first_line)
    if not match:
        return None, first_line
    return match["process"], first_line[match.end():]


def detect_level(message: str) -> str | None:
    head = message[:160]
    for form in _LEVEL_FORMS:
        match = form.match(head)
        if match:
            return _LEVEL_NAMES[match.group(1)]
    return None


def level_allows(level: str | None, minimum: str | None) -> bool:
    if minimum is None:
        return True
    return level is not None and c.LEVEL_RANK[level] >= c.LEVEL_RANK[minimum]
