"""One bounded journal page through a fixed Supervisor route (HAMCP-200).

Only ``GET`` is issued, only to a ``Route`` built from ``READABLE_ROUTES``
by ``rest_path``, with the add-on credential. No redirect, retry, proxy,
decompression or fallback. The body is never sanitized here: it is raw
evidence handed to the service's single exit point.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
from typing import Callable

import aiohttp

from ..clients.paths import Route, request_url, rest_path
from ..observability import METRICS
from ..request_context import current_telemetry
from . import contracts as c
from .journal import JournalShapeError, RawEntry, parse_page, range_header, valid_journal_cursor
from .timebasis import PageBasis, TimeBasisMismatch, Zone, resolve_page


SUPERVISOR_BASE_URL = "http://supervisor"
PROVIDER_ID = "supervisor_incident_logs"
# Supervisor selects the verbose formatter with ``verbose`` and strips ANSI
# CSI colours with ``no_colors`` (api/host.py advanced_logs_handler).
QUERY = (("verbose", None), ("no_colors", None))


class PageFailure(Exception):
    """A closed per-source failure reason; never carries provider text."""

    def __init__(self, reason: str, status: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status = status


@dataclass(frozen=True)
class Page:
    entries: tuple[RawEntry, ...]
    first_cursor: str | None
    downloaded_bytes: int
    download_capped: bool
    incomplete_entry_dropped: bool
    requested: int
    basis: PageBasis = PageBasis(True, ())


@dataclass
class Budget:
    """Per-call request, byte and deadline ceilings shared by all sources."""

    requests: int = 0
    downloaded: int = 0
    deadline: float = field(default_factory=lambda: time.monotonic() + c.CALL_DEADLINE_SECONDS)

    def available(self, source_requests: int) -> bool:
        return (
            self.requests < c.MAX_REQUESTS_PER_CALL
            and source_requests < c.MAX_REQUESTS_PER_SOURCE
            and self.downloaded < c.MAX_CALL_DOWNLOAD_BYTES
            and time.monotonic() < self.deadline - 1.0
        )


def route_for(source_key: str) -> Route:
    # Literal templates only (HAMCP-200 registry check); nothing derived
    # from caller input ever becomes a path segment here.
    if source_key == c.SOURCE_CORE:
        return rest_path("/core/logs", query=QUERY)
    if source_key == c.SOURCE_SELF:
        return rest_path("/addons/self/logs", query=QUERY)
    raise ValueError("source is not readable")


def status_reason(status: int) -> str:
    if status in (401, 403):
        return "authority_refused"
    if status == 404:
        return "route_or_addon_unavailable"
    if status == 400:
        return "request_rejected"
    return "supervisor_http_error"


class JournalTransport:
    """Fetch ``count`` entries at ``(cursor, skip)`` from one readable source."""

    def __init__(self, *, token_source: Callable[[], str], timeout_seconds: float,
                 base_url: str | None = None):
        self.token_source = token_source
        self._timeout = max(1.0, min(float(timeout_seconds), c.REQUEST_DEADLINE_SECONDS))
        self._base_url = base_url

    async def page(self, source_key: str, *, cursor: str | None, skip: int, count: int,
                   budget: Budget, zone: Zone) -> Page:
        route = route_for(source_key)
        url = request_url(self._base_url or SUPERVISOR_BASE_URL, route)
        token = self.token_source()
        if not token:
            raise PageFailure("authority_refused")
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "text/plain",
            "Accept-Encoding": "identity",
            "Range": range_header(cursor, skip, count),
        }

        async def no_transport_retry(request, handler):
            # One wire attempt: aiohttp may otherwise retry an idempotent GET.
            try:
                return await handler(request)
            except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError) as exc:
                raise PageFailure("transport_failed") from exc

        telemetry = current_telemetry()
        started = time.perf_counter()
        if telemetry:
            telemetry.begin_ha_attempt(started)
        budget.requests += 1
        timed_out = False
        outcome = "failed"
        remaining = max(1.0, min(self._timeout, budget.deadline - time.monotonic()))
        try:
            async with asyncio.timeout(remaining):
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=remaining),
                    auto_decompress=False, trust_env=False, read_bufsize=c.CHUNK_BYTES,
                    middlewares=(no_transport_retry,),
                ) as session:
                    async with session.get(url, headers=headers, allow_redirects=False) as response:
                        if response.status != 200:
                            raise PageFailure(status_reason(response.status), response.status)
                        if (
                            response.content_type != "text/plain"
                            or response.headers.get("Content-Encoding", "identity").lower() != "identity"
                            or (response.charset or "utf-8").lower() not in {"utf-8", "utf8"}
                        ):
                            raise PageFailure("representation_invalid", response.status)
                        first = response.headers.get("X-First-Cursor")
                        cap = min(c.MAX_DOWNLOAD_BYTES, max(0, c.MAX_CALL_DOWNLOAD_BYTES - budget.downloaded))
                        body = bytearray()
                        while len(body) < cap:
                            chunk = await response.content.read(min(c.CHUNK_BYTES, cap - len(body)))
                            if not chunk:
                                break
                            body.extend(chunk)
                        budget.downloaded += len(body)
                        page = build_page(bytes(body), first, capped=len(body) >= cap, requested=count,
                                          zone=zone)
                        outcome = "complete"
                        return page
        except PageFailure:
            raise
        except (asyncio.TimeoutError, TimeoutError) as exc:
            timed_out = True
            raise PageFailure("timeout") from exc
        except (aiohttp.ClientError, OSError) as exc:
            raise PageFailure("transport_failed") from exc
        finally:
            finished = time.perf_counter()
            duration = round((finished - started) * 1000, 3)
            METRICS.record_ha(duration, timeout=timed_out)
            if telemetry:
                telemetry.ha_duration_ms += duration
                telemetry.finish_ha_attempt(finished)
                telemetry.timeout_occurred = telemetry.timeout_occurred or timed_out
                telemetry.endpoint_categories.add("incident_logs")
            METRICS.record_provider_result(PROVIDER_ID, outcome, dispatched=True)


def build_page(body: bytes, first_cursor: str | None, *, capped: bool, requested: int,
               zone: Zone) -> Page:
    """Parse a raw body into complete, time-resolved entries; an entry the cap cut is dropped."""

    dropped = False
    if capped or (body and not body.endswith(b"\n")):
        body = body[: body.rfind(b"\n") + 1]
        dropped = True
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise PageFailure("encoding_invalid") from None
    try:
        entries = parse_page(text)
    except JournalShapeError:
        raise PageFailure("representation_invalid") from None
    if dropped and entries:
        # The last parsed entry may have lost continuation lines to the cap.
        entries = entries[:-1]
    if entries and valid_journal_cursor(first_cursor) is None:
        raise PageFailure("cursor_header_invalid")
    try:
        resolved, basis = resolve_page(entries, first_cursor, zone)
    except TimeBasisMismatch as exc:
        raise PageFailure(str(exc)) from None
    return Page(resolved, first_cursor if entries else None, len(body),
                capped, dropped, requested, basis)
