"""The dashboard and flow-helper sub-calls of native ``ha_search`` (HAMCP-481
W3, design sections 2.4 to 2.6).

Dashboards and flow helpers stay on the delegated ha-mcp route. This module
does not reach that route itself: the caller injects one page reader per
sub-call type, and a later step wires each reader to the existing admitted
delegated ``ha_search`` route, whose Row 25 projection every page passes
through. An absent reader means ha-mcp is not admitted, and no page is sent.

Each page has fixed arguments (:func:`page_arguments`). No other argument and
no other ``search_types`` value is ever forwarded, and nothing here falls back
to the delegated route for a native bucket.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from typing import Any, Awaitable, Callable

from . import core_contracts as c

# Rows per page. A page of this many projected rows of the largest helper row
# shape stays under the gateway's 52,000-byte check (design section 2.6); a
# test pins it. That shape is a 9.2.0 storage-helper row with a 64-character
# entity_id and name (about 270 bytes): 200 rows would exceed the check.
PAGE_SIZE = 150
# The whole-scan deadline of one sub-call, in seconds (design section 2.6).
SCAN_DEADLINE_SECONDS = 60.0
# The row bound of one scan (``MAX_CORE_PROBE_ITEMS``, design section 6.3).
MAX_SCAN_ROWS = c.MAX_ITEMS

SUBCALL_TYPES = ("dashboard", "helper")
BUCKETS = {"dashboard": "dashboards", "helper": "helpers"}
# Native serves these helper types; their sub-call rows are dropped.
STORAGE_HELPER_TYPES = frozenset(c.INPUT_DOMAINS)

# Row 25 admitted configuration-record keys and their kinds.
_ROW_KINDS = {
    "entity_id": str, "friendly_name": str, "script_id": str, "scene_id": str,
    "helper_type": str, "name": str, "match_in_name": bool, "match_in_config": bool,
    "match_in_references": bool,
}

COMPLETE = "complete"
INCOMPLETE = "incomplete"
NOT_ADMITTED = "not_admitted"


@dataclass(frozen=True, slots=True)
class PageResult:
    """One projected page, as the injected reader returns it.

    ``rows`` are the page's ``dashboards`` or ``helpers`` records after the
    Row 25 projection; ``withheld_names`` are the names that projection
    withheld from the page.
    """

    rows: tuple
    partial: bool
    has_more: bool
    config_total_matches: int
    withheld_names: frozenset = frozenset()


@dataclass(frozen=True, slots=True)
class SubcallOutcome:
    """A finished scan.

    ``total`` is what the scan adds to ``config_total_matches``: the first
    page's total for dashboards, and the flow-helper rows held for helpers.
    """

    search_type: str
    status: str
    rows: tuple = ()
    total: int = 0
    upstream_partial: bool = False
    withheld_names: frozenset = frozenset()
    pages: int = 0


PageReader = Callable[[dict], Awaitable[Any]]


def page_arguments(search_type: str, query: str, exact_match: bool, page: int) -> dict:
    """The fixed arguments of page ``page`` (design sections 2.4 and 2.5)."""

    if search_type not in SUBCALL_TYPES or type(query) is not str or type(exact_match) is not bool:
        raise ValueError("invalid sub-call arguments")
    return {
        "query": query,
        "search_types": [search_type],
        "exact_match": exact_match,
        "include_config": False,
        "offset": page * PAGE_SIZE,
        "limit": PAGE_SIZE,
    }


def _row_valid(search_type: str, row: Any) -> bool:
    if type(row) is not dict or not set(row) <= set(_ROW_KINDS) | {"score"}:
        return False
    score = row.get("score")
    if type(score) is not int:
        return False
    for key, value in row.items():
        if key != "score" and type(value) is not _ROW_KINDS[key]:
            return False
    return search_type != "helper" or "helper_type" in row


def _page_valid(search_type: str, page: Any) -> bool:
    return (
        type(page) is PageResult
        and type(page.rows) is tuple
        and type(page.partial) is bool
        and type(page.has_more) is bool
        and type(page.config_total_matches) is int
        and page.config_total_matches >= 0
        and len(page.rows) <= PAGE_SIZE
        and type(page.withheld_names) is frozenset
        and all(type(name) is str for name in page.withheld_names)
        and all(_row_valid(search_type, row) for row in page.rows)
        # A page that claims more rows but carries none cannot advance.
        and (page.rows or not page.has_more)
    )


async def scan(
    search_type: str,
    reader: PageReader | None,
    *,
    query: str,
    exact_match: bool,
    needed: int,
    monotonic: Callable[[], float] = time.monotonic,
    deadline_seconds: float = SCAN_DEADLINE_SECONDS,
) -> SubcallOutcome:
    """Page one sub-call until a design section 2.6 stop condition.

    The dashboard scan also stops once it holds ``needed`` rows (the caller's
    ``offset + limit``). A failed page, the row bound and the deadline make
    the scan incomplete; the rows already held are kept.
    """

    if search_type not in SUBCALL_TYPES:
        raise ValueError("unknown sub-call")
    if reader is None:
        return SubcallOutcome(search_type, NOT_ADMITTED)
    rows: list[dict] = []
    first_total: int | None = None
    upstream_partial = False
    withheld: set[str] = set()
    scanned = 0
    pages = 0
    started = monotonic()

    def finished(status: str) -> SubcallOutcome:
        if search_type == "dashboard":
            total = first_total if first_total is not None else 0
        else:
            total = len(rows)
        return SubcallOutcome(search_type, status, tuple(rows), total, upstream_partial,
                              frozenset(withheld), pages)

    while True:
        remaining = deadline_seconds - (monotonic() - started)
        if remaining <= 0 or scanned >= MAX_SCAN_ROWS:
            return finished(INCOMPLETE)
        arguments = page_arguments(search_type, query, exact_match, pages)
        pages += 1
        try:
            page = await asyncio.wait_for(reader(arguments), timeout=remaining)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A gateway rejection, a timeout or any other failure: the page
            # failed. Its text never reaches the reply.
            return finished(INCOMPLETE)
        if not _page_valid(search_type, page):
            return finished(INCOMPLETE)
        if first_total is None:
            first_total = page.config_total_matches
        elif page.config_total_matches != first_total:
            # The upstream snapshot moved under the scan.
            return finished(INCOMPLETE)
        upstream_partial = upstream_partial or page.partial
        withheld |= page.withheld_names
        scanned += len(page.rows)
        if search_type == "helper":
            rows.extend(dict(row) for row in page.rows
                        if row["helper_type"] not in STORAGE_HELPER_TYPES)
        else:
            rows.extend(dict(row) for row in page.rows)
        if not page.has_more:
            return finished(COMPLETE)
        if search_type == "dashboard" and len(rows) >= needed:
            return finished(COMPLETE)
