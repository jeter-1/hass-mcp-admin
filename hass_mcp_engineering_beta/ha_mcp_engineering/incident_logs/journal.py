"""Pure parsing of Supervisor's verbose journal text (no I/O, no sanitizing).

Supervisor 2026.09.1 ``journal_verbose_formatter`` writes one text line per
journal entry: ``"{ts} {_HOSTNAME} {SYSLOG_IDENTIFIER}[{_PID}]: {MESSAGE}"``
(``[{_PID}]`` absent without a PID), where ``ts`` is
``YYYY-MM-DD HH:MM:SS.mmm`` truncated to milliseconds: UTC in 2026.09.1,
naive local time from 2026.10.3, optionally followed by ``+HH:MM`` in a later
release. Parsing keeps the wall time only; ``timebasis.resolve_page`` gives
each entry its UTC time from the page's journal realtime. A binary MESSAGE may
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
from datetime import datetime
import re

from . import contracts as c


_PREFIX = re.compile(
    r"(?P<ts>(?P<date>\d{4}-\d{2}-\d{2}) (?P<time>\d{2}:\d{2}:\d{2})\.(?P<ms>\d{3})"
    r"(?P<offset>[+-]\d{2}:\d{2})?) "
    r"(?P<host>[^ ]*) (?P<process>[^ ]+?): "
)
_JOURNAL_CURSOR = re.compile(r"[a-z]=[0-9a-f]{1,64}(?:;[a-z]=[0-9a-f]{1,64}){0,7}\Z")
MAX_JOURNAL_CURSOR = 512

# Fixed level vocabularies; a match is a decision only, never output text.
_LEVEL_FORMS = (
    # Home Assistant Core / Supervisor Python logging.
    re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:[+-]\d{2}:?\d{2}|Z)? (DEBUG|INFO|WARNING|ERROR|CRITICAL|FATAL)\b"),
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
    # Set by ``timebasis.resolve_page``: ``ts_ms`` is the earliest UTC
    # candidate (the merge key) and ``ts_hi`` the latest; they are equal
    # unless the entry is uncertain, when ``candidates`` holds every reading.
    ts_hi: int | None = None
    basis: str = "unresolved"
    zone: str | None = None
    candidates: tuple[int, ...] = ()
    uncertain: str | None = None
    stamp: datetime | None = field(default=None, repr=False)
    stamp_offset: str | None = field(default=None, repr=False)

    @property
    def latest_ms(self) -> int:
        return self.ts_ms if self.ts_hi is None else self.ts_hi

    def possible_in(self, start_ms: int, end_ms: int) -> bool:
        """Some reading of this entry's time lies in ``[start, end)``."""

        return any(start_ms <= value < end_ms for value in self.readings)

    def certain_in(self, start_ms: int, end_ms: int) -> bool:
        return all(start_ms <= value < end_ms for value in self.readings)

    @property
    def readings(self) -> tuple[int, ...]:
        return self.candidates or (self.ts_ms,)


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


def parse_wall_time(date: str, time: str, ms: str) -> datetime:
    """The prefix's wall time, naive: its zone is decided per page, not here."""

    return datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M:%S").replace(microsecond=int(ms) * 1000)


def parse_page(text: str) -> list[RawEntry]:
    """Split complete page text (ending in a newline or empty) into entries."""

    if not text:
        return []
    if not text.endswith("\n"):
        raise JournalShapeError("incomplete_page")
    entries: list[RawEntry] = []
    current: list[str] = []
    stamp: tuple[datetime, str | None] | None = None
    for line in text[:-1].split("\n"):
        match = _PREFIX.match(line)
        if match:
            if current:
                entries.append(_entry(stamp, current))
            current = [line]
            try:
                stamp = (parse_wall_time(match["date"], match["time"], match["ms"]), match["offset"])
            except ValueError:
                raise JournalShapeError("timestamp_invalid") from None
        elif current:
            current.append(line)
        else:
            raise JournalShapeError("page_starts_mid_entry")
    if current:
        entries.append(_entry(stamp, current))
    return entries


def _entry(stamp: tuple[datetime, str | None], lines: list[str]) -> RawEntry:
    # ``ts_ms`` stays unset (0, basis ``unresolved``) until the page's time
    # basis is resolved; nothing reads an unresolved entry's time.
    return RawEntry(0, tuple(lines), "\n".join(lines).encode("utf-8"), stamp=stamp[0],
                    stamp_offset=stamp[1])


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
