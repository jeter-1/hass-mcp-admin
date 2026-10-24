"""A loopback Supervisor log endpoint with pinned journal-gatewayd semantics.

Synthetic data only. The response shape follows Supervisor 2026.09.1
(40e3ee7640a3c44abe67f1a1397f39c3cd949806) ``APIHost.advanced_logs_handler``
with ``journal_verbose_formatter``: ``text/plain``, one
``"{ts} {hostname} {identifier}[{pid}]: {message}"`` text per entry followed
by a newline, the timestamp ``YYYY-MM-DD HH:MM:SS.mmm`` (truncated), and
``X-First-Cursor`` naming the first returned entry only when there is one.

HAMCP-220: ``render`` selects the prefix time. ``"utc"`` is the pinned
2026.09.1 formatter; ``"local"`` is 2026.10.3 (9493d3a7, #7372):
``datetime.fromtimestamp(realtime / 1e6, tz).replace(tzinfo=None)
.isoformat(sep=" ", timespec="milliseconds")`` in Supervisor's zone;
``"offset"`` appends ``+HH:MM``, as an unreleased Supervisor does. The
cursor has the systemd v257.9 ``sd_journal_get_cursor`` field order
(``sd-journal.c`` L1252-1258), ``t=`` being the realtime in hex microseconds.

The ``Range: entries=<cursor>:<skip>:<count>`` arithmetic follows
systemd v257.9 ``journal-gatewayd``: with a cursor, seek it (a vanished
cursor lands on the next newer entry) and move ``skip`` entries (negative
backwards, clamped at the head; positive forwards, past the tail gives an
empty body); without a cursor a negative skip counts back from the tail
and a non-negative skip forward from the head. ``count`` entries are then
returned forward.

Pinned citations (systemd v257.9 ``src/journal-remote/journal-gatewayd.c``):
the seek is ``sd_journal_seek_cursor`` with a cursor, ``seek_head`` for a
cursorless non-negative skip and ``seek_tail`` for a cursorless negative
skip (L582-590); the first read then moves ``previous_skip(-skip + 1)`` or
``next_skip(skip + 1)`` (L178-183). The first step of either lands on the
sought entry itself (the cursor's entry, or the last entry after
``seek_tail``), so a cursorless ``skip = -k`` returns from the entry ``k``
before the last: ``entries=:-1:2`` is the last two entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo

from aiohttp import web


HOSTNAME = "synthetic-host"
_RANGE = re.compile(r"entries=([^:]*):(-?\d+):(\d+)\Z")


def ms(text: str) -> int:
    moment = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


_SEQNUM_ID = "0123456789abcdef" * 2
_BOOT_ID = "feedface" * 4


@dataclass
class Entry:
    seq: int
    ts_ms: int
    message: str
    identifier: str = "homeassistant"
    pid: int | None = 77
    # Sub-millisecond part of the realtime, which the text truncates.
    sub_us: int = 437

    @property
    def realtime_us(self) -> int:
        return self.ts_ms * 1000 + self.sub_us

    def cursor_text(self, realtime: bool = True) -> str:
        # Synthetic journal-cursor shape; the IDs are assembled, not literal.
        fields = [f"s={_SEQNUM_ID}", f"i={self.seq:x}", f"b={_BOOT_ID}", f"m={self.seq * 1000:x}"]
        if realtime:
            fields.append(f"t={self.realtime_us:x}")
        fields.append(f"x={(self.seq * 2654435761) & 0xffffffffffff:x}")
        return ";".join(fields)

    @property
    def cursor(self) -> str:
        return self.cursor_text()

    def text(self, render: str = "utc", zone: str = "UTC") -> str:
        tz = timezone.utc if render == "utc" else ZoneInfo(zone)
        moment = datetime.fromtimestamp(self.realtime_us / 1e6, tz)
        stamp = moment.replace(tzinfo=None).isoformat(sep=" ", timespec="milliseconds")
        if render == "offset":
            offset = moment.strftime("%z")
            stamp += f"{offset[:3]}:{offset[3:]}"
        ident = self.identifier if self.pid is None else f"{self.identifier}[{self.pid}]"
        return f"{stamp} {HOSTNAME} {ident}: {self.message}"


@dataclass
class Journal:
    entries: list[Entry] = field(default_factory=list)
    next_seq: int = 1

    def add(self, ts_ms: int, message: str, **kwargs) -> Entry:
        entry = Entry(self.next_seq, ts_ms, message, **kwargs)
        self.next_seq += 1
        self.entries.append(entry)
        return entry

    def vacuum(self, count: int) -> None:
        del self.entries[:count]

    def select(self, cursor: str, skip: int, count: int) -> list[Entry]:
        entries = self.entries
        if not entries:
            return []
        if cursor:
            seq = int(re.search(r"i=([0-9a-f]+)", cursor).group(1), 16)
            position = next((i for i, e in enumerate(entries) if e.seq >= seq), len(entries) - 1)
            if skip < 0:
                position = max(0, position + skip)
            elif skip > 0:
                position += skip
        elif skip < 0:
            # seek_tail, then previous_skip(-skip + 1): the first step lands
            # on the last entry, so ``entries=:-1:2`` is the last two.
            position = max(0, len(entries) - 1 + skip)
        else:
            position = skip
        return entries[position:position + count]


class SupervisorSim:
    """``/core/logs`` and ``/addons/self/logs`` over separate journals."""

    def __init__(self, token: str, render: str = "utc", zone: str = "UTC"):
        self.token = token
        self.render = render
        # Supervisor's own time zone, which may differ from the add-on's TZ.
        self.zone = zone
        self.cursor_realtime = True
        # Journals whose X-First-Cursor carries no realtime (HAMCP-220 M1).
        self.no_realtime_paths: set[str] = set()
        self.journals = {"/core/logs": Journal(), "/addons/self/logs": Journal()}
        self.calls: list[tuple[str, str, dict]] = []
        self.status: dict[str, int] = {}
        self.content_type = "text/plain"
        self.truncate_after: int | None = None
        self.mutate_before: dict[int, callable] = {}

    def journal(self, path: str = "/core/logs") -> Journal:
        return self.journals[path]

    async def handle(self, request: web.Request) -> web.StreamResponse:
        self.calls.append((request.method, request.path_qs, dict(request.headers)))
        hook = self.mutate_before.pop(len(self.calls), None)
        if hook:
            hook(self)
        if request.method != "GET":
            return web.Response(status=405)
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return web.Response(status=401)
        if request.path in self.status:
            return web.Response(status=self.status[request.path], text="synthetic refusal")
        if request.path not in self.journals:
            return web.Response(status=403, text="no role")
        match = _RANGE.fullmatch(request.headers.get("Range", ""))
        if not match:
            return web.Response(status=400)
        selected = self.journal(request.path).select(match[1], int(match[2]), int(match[3]))
        body = "".join(entry.text(self.render, self.zone) + "\n" for entry in selected).encode("utf-8")
        if self.truncate_after is not None:
            body = body[: self.truncate_after]
        realtime = self.cursor_realtime and request.path not in self.no_realtime_paths
        headers = {"X-First-Cursor": selected[0].cursor_text(realtime)} if selected else {}
        return web.Response(body=body, headers=headers, content_type=self.content_type,
                            charset="utf-8")


async def start(sim: SupervisorSim):
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", sim.handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"
