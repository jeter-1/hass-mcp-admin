"""The configuration sub-calls of native ``ha_search`` (HAMCP-481 W3, design
sections 2.4 to 2.6; HAMCP-501 W6 S1).

Automations, scripts, scenes and flow helpers come from the delegated
ha-mcp route; dashboards are native (HAMCP-447, :mod:`.dashboards`).
Under S1 = b+ (approved by Josh as relayed by the architect, 2026-11-26),
automation, script and scene body matching is the
route's own: on the component route ha-mcp scrubs resolved ``!secret`` values
before it scores a body, and on the legacy route a secret-bearing file fails
that item closed (a name-only row, and the page is ``partial``). Native search
reads no automation, script or scene body itself.

Each page also carries its scrub state (HAMCP-501 W6 F1): whether it shows
that its bodies were matched scrubbed. ha-mcp 9.2.0's scrub leaves a numeric
body leaf in the match corpus, and a degraded scrub leaves every leaf in it,
so the caller keeps a body match only for a query outside
:func:`body_match_unsafe` and a scan whose pages, and the helper probes
(:func:`probe_scrub`) that bracket it, are ``clean``.

This module does not reach that route itself: the caller injects one page
reader per sub-call type, wired to the admitted internal-only delegated
``ha_search`` route, whose Row 25 projection every page passes through. An
absent reader means ha-mcp is not admitted, and no page is sent.

Each page has fixed arguments (:func:`page_arguments`) at a size of the
page-size ladder (``PAGE_SIZE_LADDER``). No other argument and no other
``search_types`` value is ever forwarded, and nothing here falls back to the
delegated route for a native bucket.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from types import MappingProxyType
from typing import Any, Awaitable, Callable

from ..entity_ids import STRICT_ENTITY_ID
from . import core_contracts as c
from . import scoring

# The whole-scan deadline of one sub-call, in seconds (design section 2.6).
SCAN_DEADLINE_SECONDS = 60.0
# The row bound of one scan (``MAX_CORE_PROBE_ITEMS``, design section 6.3).
MAX_SCAN_ROWS = c.MAX_ITEMS

SUBCALL_TYPES = ("automation", "script", "scene", "helper")
BUCKETS = {
    "automation": "automations", "script": "scripts", "scene": "scenes",
    "helper": "helpers",
}
# Native serves these helper types; their sub-call rows are dropped.
STORAGE_HELPER_TYPES = frozenset(c.INPUT_DOMAINS)
# The types whose every row names its entity: ``entity_id`` is required, is a
# strict entity id of the type's own domain, and occurs once in a scan.
ENTITY_TYPES = ("automation", "script", "scene")

# The page-size ladder (design section 2.6; HAMCP-501 W6 S1, an architect
# decision). A scan reads pages of the first size. A page that fails only
# because its reply exceeds the size bound (``PageTooLarge``) is read again,
# the same span, in pages of the next size, and the scan returns to the larger
# size once that span is read. Each size divides the one above it, so every
# offset is a multiple of its page's size. A row too large for a page of one
# makes its scan incomplete, for its own bucket only.
PAGE_SIZE_LADDER = (150, 30, 6, 1)
# The page calls of one scan. A whole scan in pages of 150 is at most 134
# calls, and one row read again down to a page of one adds at most 16 (the
# span's 1 + 5 + 5 + 6 calls in place of 1): the bound holds ten such spans.
MAX_PAGE_CALLS = 300

# The worst case, pinned by tests as documentation. Two bounds hold a page.
# The gateway refuses one whose data, UTF-8 encoded, plus an 8,000-byte margin
# exceeds Row 25's 60,000 bytes (``upstream_read_gateway.py``), and it renders
# the page as at most 60,000 bytes of ASCII-escaped JSON
# (``models/responses.py``): a page either bound refuses is too large. An
# escaped string is never shorter than its UTF-8 bytes, so the budget is
# counted escaped: the limit less the 8,000-byte margin (which also holds the
# reply around the data), a page's data envelope with a 1,024-character echoed
# query of four-byte characters (at most 13,000 bytes) and 2,000 bytes for its
# warnings and errors.
PAGE_BUDGET_BYTES = 60_000 - 8_000 - 13_000 - 2_000
# The escaped bytes of each type's largest Row 25 row (``_ROW_KEYS``), with
# its comma, rounded up to ten: every name or id string at
# ``reply.RECORD_STRING_MAX`` (256) four-byte characters, 12 bytes each when
# escaped, a 255-character entity id (four-byte for a helper, whose entity id
# is not checked as strict), a 21-character score (9.2.0's are at most 100)
# and every flag false. A test pins each figure. A page of a type's largest
# rows fits at ``PAGE_BUDGET_BYTES // ROW_BYTES`` rows (10 automation, 5
# script, 5 scene or 2 helper rows; a helper row's ``entry_id`` since
# HAMCP-447). The largest ladder size within that is 6 automation, 1 script,
# 1 scene or 1 helper row; the budget is conservative, so a scan of such
# rows steps down until a page fits, at that size or a larger one. One such
# row fits a page of one.
ROW_BYTES = MappingProxyType({
    "automation": 3_470, "script": 6_560, "scene": 6_560, "helper": 12_440,
})

# Row 25 admitted configuration-record keys and their kinds.
_ROW_KINDS = {
    "entity_id": str, "friendly_name": str, "script_id": str, "scene_id": str,
    "helper_type": str, "name": str, "match_in_name": bool, "match_in_config": bool,
    "match_in_references": bool, "entry_id": str,
}
# ha-mcp 9.2.0's component route sets these from the item's unique id or
# configuration ``id``, which may be absent (``search_config.py``
# ``_extract_config``): ``None`` is a valid value, filled natively.
_NULLABLE = frozenset({"script_id", "scene_id"})
# The keys a row of each type may carry; any other admitted key is drift. The
# row bounds above hold for these keys only.
_ROW_KEYS = MappingProxyType({
    "automation": frozenset({"entity_id", "friendly_name", "score", "match_in_name",
                             "match_in_config", "match_in_references"}),
    "script": frozenset({"entity_id", "script_id", "friendly_name", "score", "match_in_name",
                         "match_in_config", "match_in_references"}),
    "scene": frozenset({"entity_id", "scene_id", "friendly_name", "score", "match_in_name",
                        "match_in_config", "match_in_references"}),
    # A 9.2.0 storage-helper or flow-helper row: Row 25 withholds a storage
    # helper's config and a flow helper's ``options_withheld``, and admits a
    # flow helper's ``entry_id`` (HAMCP-447).
    "helper": frozenset({"entity_id", "entry_id", "helper_type", "name", "score",
                         "match_in_name", "match_in_config", "match_in_references"}),
})

COMPLETE = "complete"
INCOMPLETE = "incomplete"
NOT_ADMITTED = "not_admitted"

# HAMCP-501 W6 F1: a page's scrub state, as the gateway reads it from the raw
# page (``upstream_reads_9_2_keys.page_scrub_state``; the same values).
SCRUB_CLEAN = "clean"
SCRUB_DEGRADED = "degraded"
SCRUB_UNVERIFIED = "unverified"
SCRUB_STATES = (SCRUB_CLEAN, SCRUB_DEGRADED, SCRUB_UNVERIFIED)
_SCRUB_ORDER = MappingProxyType({SCRUB_CLEAN: 0, SCRUB_UNVERIFIED: 1, SCRUB_DEGRADED: 2})
# The sub-calls whose rows the route matched by body against the scrubbed
# corpus: a dashboard is not on ha-mcp's scrub surface (disclosed).
BODY_TYPES = ("automation", "script", "scene", "helper")
# A probe is one helper page of one row: only a helper search reads the scrub
# with its degraded flag (``search.py`` ``_search_prep``).
PROBE_SIZE = 1
PROBE_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True, slots=True)
class PageResult:
    """One projected page, as the injected reader returns it.

    ``rows`` are the page's records of its one bucket after the Row 25
    projection; ``withheld_names`` are the names that projection
    withheld from the page; ``scrub`` is the page's scrub state, unverified
    unless the reader read it.
    """

    rows: tuple
    partial: bool
    has_more: bool
    config_total_matches: int
    withheld_names: frozenset = frozenset()
    scrub: str = SCRUB_UNVERIFIED


@dataclass(frozen=True, slots=True)
class SubcallOutcome:
    """A finished scan.

    ``total`` is what the scan adds to ``config_total_matches``: the rows
    it holds (a helper scan holds its flow-helper rows only). ``scrub`` is the worst
    scrub state of the scan's valid pages.
    """

    search_type: str
    status: str
    rows: tuple = ()
    total: int = 0
    upstream_partial: bool = False
    withheld_names: frozenset = frozenset()
    pages: int = 0
    scrub: str = SCRUB_CLEAN


class PageTooLarge(Exception):
    """A page failed only because its reply exceeded the size bound.

    The reader raises it from the gateway's structural flag, never from a
    page's text or from another failure. Only it makes a scan read the
    page's span again at the next ladder size.
    """


PageReader = Callable[[dict], Awaitable[Any]]


def page_arguments(search_type: str, query: str, exact_match: bool, page: int,
                   size: int) -> dict:
    """The fixed arguments of page ``page`` of ``size`` rows (design sections
    2.4 and 2.5); ``size`` is a ``PAGE_SIZE_LADDER`` size."""

    if (search_type not in SUBCALL_TYPES or type(query) is not str
            or type(exact_match) is not bool or type(page) is not int or page < 0
            or type(size) is not int or size not in PAGE_SIZE_LADDER):
        raise ValueError("invalid sub-call arguments")
    return {
        "query": query,
        "search_types": [search_type],
        "exact_match": exact_match,
        "include_config": False,
        "offset": page * size,
        "limit": size,
    }


def _row_valid(search_type: str, row: Any) -> bool:
    if type(row) is not dict or not set(row) <= _ROW_KEYS[search_type]:
        return False
    score = row.get("score")
    if type(score) is not int:
        return False
    for key, value in row.items():
        if key == "score" or (value is None and key in _NULLABLE):
            continue
        if type(value) is not _ROW_KINDS[key]:
            return False
    if search_type in ENTITY_TYPES:
        entity_id = row.get("entity_id")
        return (type(entity_id) is str
                and STRICT_ENTITY_ID.fullmatch(entity_id) is not None
                and entity_id.startswith(f"{search_type}."))
    return search_type != "helper" or "helper_type" in row


def _page_valid(search_type: str, page: Any, size: int) -> bool:
    return (
        type(page) is PageResult
        and type(page.rows) is tuple
        and type(page.partial) is bool
        and type(page.has_more) is bool
        and type(page.config_total_matches) is int
        and page.config_total_matches >= 0
        and len(page.rows) <= size
        and type(page.withheld_names) is frozenset
        and all(type(name) is str for name in page.withheld_names)
        and type(page.scrub) is str
        and page.scrub in SCRUB_STATES
        and all(_row_valid(search_type, row) for row in page.rows)
        # A page that claims more rows is full, so the next offset neither
        # skips nor repeats a row.
        and (len(page.rows) == size or not page.has_more)
    )


async def scan(
    search_type: str,
    reader: PageReader | None,
    *,
    query: str,
    exact_match: bool,
    monotonic: Callable[[], float] = time.monotonic,
    deadline_seconds: float = SCAN_DEADLINE_SECONDS,
) -> SubcallOutcome:
    """Page one sub-call until a design section 2.6 stop condition.

    Every type is scanned whole. Pages follow
    ``PAGE_SIZE_LADDER``: a page too large for the reply bound is read again in
    smaller pages, which neither skip nor repeat a row. A failed page, a row
    too large for a page of one, the row bound, the call bound, the deadline
    and an entity id seen twice in one scan make the scan incomplete; the rows
    already held are kept. An incomplete scan affects its own bucket only.
    """

    if search_type not in SUBCALL_TYPES:
        raise ValueError("unknown sub-call")
    if reader is None:
        return SubcallOutcome(search_type, NOT_ADMITTED)
    rows: list[dict] = []
    first_total: int | None = None
    upstream_partial = False
    withheld: set[str] = set()
    worst = SCRUB_CLEAN
    seen: set[str] = set()
    scanned = 0
    calls = 0
    offset = 0
    level = 0
    # The end offset of each span being read again in smaller pages.
    span_ends: list[int] = []
    started = monotonic()

    def finished(status: str) -> SubcallOutcome:
        return SubcallOutcome(search_type, status, tuple(rows), len(rows), upstream_partial,
                              frozenset(withheld), calls, worst)

    while True:
        remaining = deadline_seconds - (monotonic() - started)
        if remaining <= 0 or scanned >= MAX_SCAN_ROWS or calls >= MAX_PAGE_CALLS:
            return finished(INCOMPLETE)
        size = PAGE_SIZE_LADDER[level]
        arguments = page_arguments(search_type, query, exact_match, offset // size, size)
        calls += 1
        try:
            page = await asyncio.wait_for(reader(arguments), timeout=remaining)
        except asyncio.CancelledError:
            raise
        except PageTooLarge:
            if level == len(PAGE_SIZE_LADDER) - 1:
                # One row too large for a page of its own.
                return finished(INCOMPLETE)
            span_ends.append(offset + size)
            level += 1
            continue
        except Exception:
            # A gateway rejection, a timeout or any other failure: the page
            # failed, with no retry. Its text never reaches the reply.
            return finished(INCOMPLETE)
        if not _page_valid(search_type, page, size):
            return finished(INCOMPLETE)
        if first_total is None:
            first_total = page.config_total_matches
        elif page.config_total_matches != first_total:
            # The upstream snapshot moved under the scan.
            return finished(INCOMPLETE)
        page_ids = [row["entity_id"] for row in page.rows if "entity_id" in row]
        if len(set(page_ids)) != len(page_ids) or not seen.isdisjoint(page_ids):
            # A repeated row: the upstream order moved under the scan.
            return finished(INCOMPLETE)
        seen.update(page_ids)
        upstream_partial = upstream_partial or page.partial
        withheld |= page.withheld_names
        worst = worst_scrub(worst, page.scrub)
        scanned += len(page.rows)
        if search_type == "helper":
            rows.extend(dict(row) for row in page.rows
                        if row["helper_type"] not in STORAGE_HELPER_TYPES)
        else:
            rows.extend(dict(row) for row in page.rows)
        if not page.has_more:
            return finished(COMPLETE)
        # The page is full (``_page_valid``), so the next starts after it.
        offset += size
        while span_ends and offset == span_ends[-1]:
            # The span is read again: back to the larger size.
            span_ends.pop()
            level -= 1


def worst_scrub(*states: str) -> str:
    """The worst of ``states``: degraded, then unverified, then clean. A value
    that is not a scrub state is unverified."""

    worst = SCRUB_CLEAN
    for state in states:
        if type(state) is not str or state not in _SCRUB_ORDER:
            state = SCRUB_UNVERIFIED
        if _SCRUB_ORDER[state] > _SCRUB_ORDER[worst]:
            worst = state
    return worst


async def probe_scrub(reader: PageReader | None, *, query: str, exact_match: bool,
                      timeout_seconds: float = PROBE_TIMEOUT_SECONDS) -> str:
    """The scrub state of one helper probe page (HAMCP-501 W6 F1).

    ha-mcp 9.2.0 reports a degraded scrub only on a helper search, and loads
    its secrets for each call, so the caller reads one probe before and one
    after the automation, script and scene scans (the race between them is
    disclosed). The probe's rows are never used. No reader, a failed, timed
    out or invalid page is unverified.
    """

    if reader is None:
        return SCRUB_UNVERIFIED
    arguments = page_arguments("helper", query, exact_match, 0, PROBE_SIZE)
    try:
        page = await asyncio.wait_for(reader(arguments), timeout=timeout_seconds)
    except asyncio.CancelledError:
        raise
    except Exception:
        return SCRUB_UNVERIFIED
    return page.scrub if _page_valid("helper", page, PROBE_SIZE) else SCRUB_UNVERIFIED


# The A'+ trigger (decided by Josh as relayed by the architect, 2026-11-26).
# ha-mcp 9.2.0 scores a numeric body leaf by its ``str()`` with no scrub
# (``search_score.py`` ``_leaf_exact_score`` and ``_collect_string_leaves``),
# and ``str()`` of a YAML number, date or time uses these characters (after
# ``lower()``); the "inf" and "nan" forms are narrowed out (disclosed).
_NUMERIC_CHARS = frozenset("0123456789.+-e:t ")


def body_match_unsafe(query: str, exact_match: bool) -> bool:
    """Whether a route body match could confirm an unscrubbed numeric secret.

    Exact mode: the stripped, lowercased query is non-empty and every
    character is a number's. Fuzzy mode: that, or any query token holds a
    digit (the ratio of a digit-free token to a number's tokens stays under
    the fuzzy threshold, "inf" and "nan" aside).
    """

    q = query.strip().lower()
    if not q:
        return False
    if all(char in _NUMERIC_CHARS for char in q):
        return True
    return not exact_match and any(
        any("0" <= char <= "9" for char in token) for token in scoring.tokenize(q)
    )
