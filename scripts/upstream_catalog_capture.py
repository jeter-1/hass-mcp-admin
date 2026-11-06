"""Offline-testable, bounded catalog capture for admission evidence (not authority).

Callers supply the session and own exact image, transport, protocol and secret
handling. Canonical JSON describes descriptors, not raw HTTP transport bytes.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Awaitable, Callable


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


async def capture_catalog(fetch_page: Callable[[str | None], Awaitable[dict]], *,
                          max_pages: int = 64, max_tools: int = 2048,
                          max_bytes: int = 8 * 1024 * 1024) -> dict:
    """Capture every page or refuse; never silently truncate or deduplicate.

    The first request has no cursor. A subsequent empty page, duplicate tool,
    repeated/empty/invalid cursor or malformed descriptor is not complete evidence.
    An empty first and final page is a valid empty catalog.
    """
    if min(max_pages, max_tools, max_bytes) < 1:
        raise ValueError("invalid capture bounds")
    cursor = None
    cursors: set[str] = set()
    names: set[str] = set()
    tools: list[dict] = []
    pages: list[dict] = []
    byte_count = 0
    for _ in range(max_pages):
        page = await fetch_page(cursor)
        if not isinstance(page, dict) or not isinstance(page.get("tools"), list):
            raise ValueError("missing tools page")
        byte_count += len(canonical_bytes(page))
        if byte_count > max_bytes:
            raise ValueError("oversized catalog")
        page = json.loads(canonical_bytes(page))  # retain an independent evidence snapshot
        if cursor is not None and not page["tools"]:
            raise ValueError("empty continuation page")
        for tool in page["tools"]:
            if (not isinstance(tool, dict) or not isinstance(tool.get("name"), str)
                    or not tool["name"] or not isinstance(tool.get("inputSchema"), dict)):
                raise ValueError("malformed descriptor")
            if tool["name"] in names:
                raise ValueError("duplicate tool")
            names.add(tool["name"])
            tools.append(tool)
        if len(tools) > max_tools:
            raise ValueError("too many tools")
        pages.append(page)
        following = page.get("nextCursor")
        if following is None:
            ordered = sorted(tools, key=lambda item: item["name"])
            return {"pages": pages, "tools": ordered, "count": len(ordered),
                    "catalog_sha256": fingerprint(ordered),
                    "descriptor_sha256": {item["name"]: fingerprint(item) for item in ordered}}
        if not isinstance(following, str) or not following or following in cursors:
            raise ValueError("invalid or looping cursor")
        cursors.add(following)
        cursor = following
    raise ValueError("too many pages")


def assert_repeatable(first: dict, second: dict) -> None:
    """Require byte equality including page order/boundaries, not only names."""
    if canonical_bytes(first) != canonical_bytes(second):
        raise ValueError("catalog changed between captures")


def reject_secrets(payload: bytes, forbidden: list[str]) -> None:
    """Refuse publication if any supplied disposable secret occurs literally."""
    if any(secret and secret.encode() in payload for secret in forbidden):
        raise ValueError("secret detected; payload not written")
