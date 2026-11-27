"""PC-1 (HAMCP-425): one continuation contract for list-shaped replies.

A list tool that adopts it sorts its whole filtered match set, returns one
page that fits the response bound, and hands back a signed cursor for the
rest. The cursor is ``incident_logs.cursor.CursorCodec``'s format, unchanged;
this module holds no key and no signature or encoding code. Its payload is
exactly ``{"v": 1, "q": <keyed filter fingerprint>, "o": <offset>,
"s": <keyed set digest>}`` plus the codec's ``f`` and ``x``.

No server snapshot is kept: a continuation re-reads the source, filters,
sorts and recomputes the set digest. Row values may change between pages
(each page reflects its own read); a change of membership or order refuses
the walk as ``STALE_CURSOR`` ``list_continuity_broken``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .errors import ErrorCode, GovernanceError
from .incident_logs.cursor import CursorCodec


CURSOR_VERSION = 1
MAX_CURSOR_CHARS = 2048
# The canonical dispatcher and run_structured wrap a tool's reply in their own
# envelope (summary, warnings, routing metadata, timing, request id) after the
# tool returns. An adopter page is filled to the response limit less this
# reserve, so that envelope never trims a page behind its cursor
# (tests/test_hamcp425_list_cursor.py measures the real overhead).
ENVELOPE_RESERVE_CHARS = 4096
PAYLOAD_FIELDS = frozenset({"v", "q", "o", "s", "f", "x"})
_HEX32 = re.compile(r"[0-9a-f]{32}")
# The longest page_limit_reason, so the reserved envelope is a worst case.
_LONGEST_REASON = "response_size_limit"


def _compact(value: Any) -> str:
    # ensure_ascii (the default) makes the character count bound UTF-8 bytes.
    return json.dumps(value, separators=(",", ":"), default=str)


def _refuse(codec: CursorCodec, code: ErrorCode, reason: str) -> GovernanceError:
    return GovernanceError(code, details={"feature": codec.feature, "reason": reason})


def set_digest(codec: CursorCodec, identities: Sequence[str]) -> str:
    """The keyed digest of the whole ordered match set's identity keys.

    A canonical JSON array (the codec's own rules), so two different
    sequences never share bytes the way a joined string can. Keyed, so a
    cursor holder cannot confirm a guessed set of ids offline.
    """

    data = json.dumps(list(identities), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return codec.digest("set", data.encode("ascii"))


@dataclass(frozen=True)
class ListPosition:
    """A decoded continuation: where the next page starts and what it binds."""

    offset: int
    set_digest: str | None

    def require_set(self, codec: CursorCodec, digest: str, total: int) -> int:
        """The offset, once the re-read match set equals the first page's."""

        if self.set_digest is None:
            return 0
        if digest != self.set_digest:
            raise _refuse(codec, ErrorCode.STALE_CURSOR, "list_continuity_broken")
        if self.offset >= total:
            # A signed cursor is issued only while a row remains.
            raise _refuse(codec, ErrorCode.INVALID_CURSOR, "integrity_or_format_invalid")
        return self.offset


FIRST_PAGE = ListPosition(offset=0, set_digest=None)


def open_cursor(codec: CursorCodec, cursor: str, query: str) -> ListPosition:
    """Decode and check ``cursor`` before any read; "" is the first page.

    The order is fixed: the codec's integrity and expiry checks, then the
    exact payload field set and types, then the filter fingerprint. The set
    digest is checked after the re-read, by ``ListPosition.require_set``.
    """

    if type(cursor) is not str or len(cursor) > MAX_CURSOR_CHARS:
        raise _refuse(codec, ErrorCode.INVALID_CURSOR, "integrity_or_format_invalid")
    if cursor == "":
        return FIRST_PAGE
    payload = codec.decode(cursor)
    if (
        set(payload) != PAYLOAD_FIELDS
        or type(payload["v"]) is not int or payload["v"] != CURSOR_VERSION
        or type(payload["o"]) is not int or payload["o"] < 0
        or not all(type(payload[key]) is str and _HEX32.fullmatch(payload[key]) for key in ("q", "s"))
    ):
        raise _refuse(codec, ErrorCode.INVALID_CURSOR, "integrity_or_format_invalid")
    if payload["q"] != query:
        raise _refuse(codec, ErrorCode.INVALID_CURSOR, "query_mismatch")
    return ListPosition(offset=payload["o"], set_digest=payload["s"])


def identity_keys(rows: Sequence[dict[str, Any]], key: str, malformed: Callable[[], Exception]) -> list[str]:
    """Every row's identity key; a missing or non-string one fails the read."""

    identities = []
    for row in rows:
        value = row.get(key) if isinstance(row, dict) else None
        if type(value) is not str:
            raise malformed()
        identities.append(value)
    return identities


def page_exceeds_limit(feature: str) -> GovernanceError:
    # Never the general projection, which would delete the list.
    return GovernanceError(ErrorCode.INTERNAL_SERVER_ERROR, details={
        "feature": feature, "reason": "page_exceeds_response_limit",
    })


def fill_page(
    window: Sequence[dict[str, Any]],
    *,
    codec: CursorCodec,
    query: str,
    digest: str,
    offset: int,
    total: int,
    row_key: str,
    identity: str,
    budget: int | None,
    legacy_count: bool = True,
    legacy_truncated: bool = True,
) -> dict[str, Any]:
    """The page that starts at ``offset`` of a ``total``-row ordered match set.

    ``window`` is the match set's rows ``[offset:offset+limit]``, already
    projected (the caller builds rows for the slice only). It keeps the
    longest whole-row prefix whose compact rendering, with the envelope and
    a worst-case ``next_cursor`` reserved, fits ``budget`` characters
    (``None``: no size bound). The match set's last row is also kept when the
    exact final page, which carries no cursor, fits. A row too large for any
    page is skipped and named in ``omitted_ids``; every page advances at
    least one row. ``count`` equals
    ``returned`` and ``truncated`` equals ``has_more`` where the tool has them.
    """

    window = list(window)

    def encode(position: int) -> str:
        return codec.encode({"v": CURSOR_VERSION, "q": query, "o": position, "s": digest})

    def reply(kept, omitted, has_more, next_cursor, reason, count=None):
        shown = len(kept) if count is None else count
        value: dict[str, Any] = {}
        if legacy_count:
            value["count"] = shown
        value[row_key] = kept
        value["returned"] = shown
        value["total"] = total
        if legacy_truncated:
            value["truncated"] = has_more
        value.update(has_more=has_more, next_cursor=next_cursor, page_limit_reason=reason)
        if omitted:
            value["omitted_oversized"] = len(omitted) if count is None else count
            value["omitted_ids"] = omitted
        return value

    if budget is None:
        consumed = len(window)
        kept, omitted, stopped = window, [], False
    else:
        # The longest cursor this walk can issue (the offset only grows).
        reserve = "x" * len(encode(total))
        base = len(_compact(reply([], [], True, reserve, _LONGEST_REASON, count=len(window))))
        if base > budget:
            raise page_exceeds_limit(codec.feature)
        # The first skipped row also adds the two omission keys.
        omission_base = (
            len(_compact(reply([], ["x"], True, reserve, _LONGEST_REASON, count=len(window))))
            - base - len(_compact("x"))
        )
        room = budget - base
        kept, omitted, stopped = [], [], False
        # The window's last row is the match set's last row (review A1-OMIT-001).
        final = offset + len(window) == total
        for index, row in enumerate(window):
            size = len(_compact(row))
            cost = size + (1 if kept else 0)
            if cost <= room:
                kept.append(row)
                room -= cost
                continue
            if final and index == len(window) - 1:
                # The final page carries no cursor, so the reserve is not needed.
                if len(_compact(reply(kept + [row], omitted, False, None, None))) <= budget:
                    kept.append(row)
                    continue
                if len(_compact(reply([row], [], False, None, None))) <= budget:
                    # It fits a final page of its own: the next page starts with it.
                    stopped = True
                    break
            if size <= budget - base:
                # It fits a page of its own: the next page starts with it.
                stopped = True
                break
            identity_text = _compact(row[identity])
            skip = len(identity_text) + (1 if omitted else omission_base)
            if skip > room:
                stopped = True
                break
            omitted.append(row[identity])
            room -= skip
        consumed = len(kept) + len(omitted)
        if window and consumed == 0:
            raise page_exceeds_limit(codec.feature)
    has_more = offset + consumed < total
    if not has_more:
        reason = None
    elif stopped:
        reason = "response_size_limit"
    else:
        reason = "limit"
    value = reply(kept, omitted, has_more, encode(offset + consumed) if has_more else None, reason)
    if budget is not None and len(_compact(value)) > budget:
        raise page_exceeds_limit(codec.feature)
    return value
