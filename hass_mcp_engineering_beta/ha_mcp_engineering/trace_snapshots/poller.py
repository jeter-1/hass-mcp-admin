"""The bounded trace-watch poller (HAMCP-225 decision W1).

Engineering's only periodic Core reader. Each tick owns one exact Core
authority lease for ``core.automation_trace_read``, the same way the
dependency index build does (``dependency/authority.py``): refusal is
installed before acquisition, the lease is consumed and revalidated on every
dispatch, and it is closed when the tick ends or overruns. A tick without
authority sends nothing and is counted against every watch as a skipped
poll. Ticks never overlap.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import logging
import time

from ..logging_config import get_logger, log_event
from ..request_context import begin_request, end_request
from . import contracts as c


LOGGER = get_logger("trace_watch")


@contextmanager
def watch_poll_authority(core_runtime, deadline):
    """Yield ``(generation, version)`` or ``None``; never borrow caller authority."""

    telemetry, token = begin_request()
    telemetry.tool_name = "trace_watch_poll"
    telemetry.caller_id = "trace_watch"
    owner = asyncio.current_task()
    authority = None
    commits = None
    closed = False

    def authorize():
        nonlocal commits, closed
        if closed:
            return False
        if (authority is None or time.monotonic() >= deadline or owner is None
                or owner.cancelling() or owner.done()):
            closed = True
            return False
        if commits is None:
            commits = core_runtime.consume(authority)
        if commits is None or not core_runtime.revalidate(authority, commits):
            closed = True
            return False
        return True

    # Refuse first: no dispatch may exploit the no-authorizer default.
    telemetry.core_dispatch_authorizer = authorize
    try:
        if core_runtime is not None:
            status = core_runtime.route_status(c.REQUIREMENTS)
            if status.get("available") is True:
                authority = core_runtime.acquire(c.REQUIREMENTS)
        yield (None if authority is None else (authority.generation, authority.core_version))
    finally:
        closed = True
        telemetry.core_dispatch_authorizer = lambda: False
        try:
            if commits is not None:
                core_runtime.finish(commits)
            elif authority is not None:
                core_runtime.release(authority)
        finally:
            end_request(token)


class TraceWatchPoller:
    def __init__(self, service, core_runtime, *, interval=c.POLL_SECONDS,
                 tick_seconds=c.TICK_SECONDS):
        self.service, self.core_runtime = service, core_runtime
        self.interval, self.tick_seconds = interval, tick_seconds
        self.task = None
        self.ticks = 0
        self.failed_ticks = 0

    async def tick(self):
        started = time.monotonic()
        deadline = started + self.tick_seconds
        with watch_poll_authority(self.core_runtime, deadline) as authority:
            async with asyncio.timeout(self.tick_seconds):
                # Reads stop at the read share; the rest is the commit reserve.
                await self.service.poll_once(
                    authority=authority, deadline=started + self.tick_seconds * c.READ_SHARE)

    async def run(self):
        while True:
            started = time.monotonic()
            try:
                await self.tick()
                self.ticks += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A busy store, a timed-out tick or a failed audit write is
                # retried at the next interval; no payload is logged.
                self.failed_ticks += 1
                reason = getattr(exc, "reason", None)
                log_event(LOGGER, logging.WARNING, "trace_watch_tick_failed",
                          "Trace watch tick failed; retrying at the next interval.",
                          context={"reason": reason if type(reason) is str else type(exc).__name__})
            await asyncio.sleep(max(1.0, self.interval - (time.monotonic() - started)))

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run(), name="trace-watch-poller")
        return self.task

    async def shutdown(self):
        if self.task is not None and not self.task.done():
            self.task.cancel()
        if self.task is not None:
            await asyncio.gather(self.task, return_exceptions=True)
