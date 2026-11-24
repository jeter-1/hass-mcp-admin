"""Parse one ``get_audit_log`` page (HAMCP-475).

A page is zero or more verbatim JSONL audit records, or one of the two
messages, followed by exactly one ``{"_page": ...}`` trailer line.
"""

import json

AUDIT_EMPTY_MESSAGES = frozenset({"No audit log yet.", "No matching entries."})
_TRAILER_FIELDS = {"returned", "next_cursor", "complete"}
_OPTIONAL_TRAILER_FIELDS = {"omitted_oversized", "skipped_undecodable"}
_MAX_CURSOR_CHARS = 512


def split_audit_page(rendered):
    """``(body, page)``: the text before the trailer and the trailer's ``_page`` object.

    Raises ``AssertionError`` unless the text ends with exactly one well-formed
    trailer line.
    """

    if not rendered.endswith("\n"):
        raise AssertionError(f"audit page does not end with a newline: {rendered[-200:]!r}")
    body, _, trailer = rendered[:-1].rpartition("\n")
    value = json.loads(trailer)
    if set(value) != {"_page"}:
        raise AssertionError(f"last line is not a _page trailer: {trailer!r}")
    page = value["_page"]
    fields = set(page)
    if not _TRAILER_FIELDS <= fields <= _TRAILER_FIELDS | _OPTIONAL_TRAILER_FIELDS:
        raise AssertionError(f"unexpected _page fields: {sorted(fields)}")
    if (
        type(page["returned"]) is not int
        or page["returned"] < 0
        or type(page["complete"]) is not bool
    ):
        raise AssertionError(f"malformed _page trailer: {page!r}")
    cursor = page["next_cursor"]
    if cursor is not None and (
        type(cursor) is not str or not 0 < len(cursor) <= _MAX_CURSOR_CHARS
    ):
        raise AssertionError(f"malformed next_cursor: {page!r}")
    for field in _OPTIONAL_TRAILER_FIELDS & set(page):
        if type(page[field]) is not int or page[field] < 1:
            raise AssertionError(f"malformed {field}: {page!r}")
    if page["complete"] != (page["next_cursor"] is None):
        raise AssertionError(f"complete and next_cursor disagree: {page!r}")
    for line in body.splitlines():
        if line.startswith('{"_page"'):
            raise AssertionError("more than one _page trailer")
    return body, page


def audit_page_message(rendered):
    """The message line of an empty page, or ``None`` when the page has records."""

    body, page = split_audit_page(rendered)
    if body in AUDIT_EMPTY_MESSAGES:
        if page["returned"] != 0:
            raise AssertionError(f"empty page reports records: {page!r}")
        return body
    return None


def audit_page_rows(rendered):
    """The page's records, parsed; ``[]`` for an empty page."""

    body, page = split_audit_page(rendered)
    if body in AUDIT_EMPTY_MESSAGES:
        rows = []
    else:
        rows = [json.loads(line) for line in body.splitlines() if line.strip()]
    if page["returned"] != len(rows):
        raise AssertionError(f"_page returned {page['returned']} but the page has {len(rows)} records")
    return rows
