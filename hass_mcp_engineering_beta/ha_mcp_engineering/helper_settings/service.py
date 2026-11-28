"""Read-only storage helper settings report (HAMCP-519).

One ``CoreReadSession`` under the request's Core lease reads the entity
registry and the selected ``input_*/list`` collections; nothing else is
dispatched. Every page re-reads its sources (PC-1): the cursor is checked
before any read, and the set digest after it.

Untrusted Core text passes the shared sanitizer before it is returned. Each
helper's identity (``helper_key``, ``<domain>/<id>``) is built from its
sanitized id, so the digest, the order and ``omitted_ids`` never carry text
the sanitizer withheld. The rest of a row is projected and sanitized only for
the page's window. Sanitizer counts are kept per helper, so redaction counts
and their partial reasons describe only the rows the page returns.
"""

from __future__ import annotations

import json
import math
from typing import Any, Callable

from .. import paging
from ..incident_logs.cursor import CursorCodec
from ..sanitization import ReferenceMarkerRegistry, sanitize_untrusted_data
from ..search import core_contracts as core
from ..search.core_reads import INVALID
from . import contracts as c


def _compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def _kind_valid(kind: str, value: Any) -> bool:
    if kind == c.NUMBER:
        return type(value) in (int, float) and math.isfinite(value)
    if kind == c.INT:
        return type(value) is int
    if kind == c.BOOL:
        return type(value) is bool
    if kind == c.STR:
        return type(value) is str
    if kind == c.OPTIONAL_STR:
        return value is None or type(value) is str
    if kind == c.STR_LIST:
        return type(value) is list and all(type(item) is str for item in value)
    raise AssertionError(kind)


def _initial(domain: str, row: dict, invalid: list[str]) -> dict | None:
    kind = c.INITIAL_KINDS[domain]
    if kind is None:
        return None
    field = "length" if domain in c.INITIAL_LENGTH_ONLY else "value"
    value = row.get("initial")
    if value is None:
        return {"present": False, field: None}
    if not _kind_valid(kind, value):
        invalid.append("initial")
        return {"present": True, field: None}
    # input_text: the stored value never leaves this function (GO).
    return {"present": True, field: len(value) if field == "length" else value}


def _settings(domain: str, row: dict, invalid: list[str]) -> tuple[dict, bool]:
    settings: dict[str, Any] = {}
    truncated = False
    for name, kind in c.SETTINGS_FIELDS[domain].items():
        value = row[name] if name in row else None
        if name in row and not _kind_valid(kind, value):
            invalid.append(name)
            value = None
        if kind == c.STR_LIST:
            # The list is capped; its full length is reported beside it.
            settings[f"{name}_total"] = len(value) if value is not None else None
            truncated = value is not None and len(value) > c.MAX_OPTIONS
            settings[f"{name}_truncated"] = truncated
            value = list(value[: c.MAX_OPTIONS]) if value is not None else None
        settings[name] = value
    return settings, truncated


def project(domain: str, row: dict) -> tuple[dict, list[str], bool]:
    """The allowlisted fields of one stored helper; unknown keys are ignored."""

    invalid: list[str] = []
    name = row.get("name")
    if name is INVALID or type(name) is not str:
        invalid.append("name")
        name = None
    icon = row.get("icon")
    if not _kind_valid(c.OPTIONAL_STR, icon):
        invalid.append("icon")
        icon = None
    initial = _initial(domain, row, invalid)
    settings, truncated = _settings(domain, row, invalid)
    return {"name": name, "icon": icon, "initial": initial, "settings": settings}, sorted(invalid), truncated


def entity_index(registry) -> dict[tuple[str, str], list[str]]:
    """``(platform, unique_id)`` -> entity ids of helper registry entries.

    A storage helper's registry entry has the helper's domain as platform and
    its collection id as unique id (``helpers/collection.py``).
    """

    index: dict[tuple[str, str], list[str]] = {}
    for entity_id, row in registry.value.items():
        platform, unique_id = row.get("platform"), row.get("unique_id")
        if (type(platform) is not str or platform not in c.DOMAINS or type(unique_id) is not str
                or not entity_id.startswith(f"{platform}.")):
            continue
        index.setdefault((platform, unique_id), []).append(entity_id)
    return index


class _Tally:
    """Sanitizer counts for one helper, so a page reports only its own rows."""

    def __init__(self) -> None:
        self.redacted = 0
        self.truncated = 0
        self.categories: set[str] = set()

    def add(self, other: "_Tally") -> None:
        self.redacted += other.redacted
        self.truncated += other.truncated
        self.categories |= other.categories


class _Cleaner:
    def __init__(self, known_secrets: tuple) -> None:
        self.known_secrets = tuple(known_secrets)
        self.registry = ReferenceMarkerRegistry()

    def __call__(self, value: Any, kind: type, tally: _Tally) -> Any:
        result = sanitize_untrusted_data(value, known_secrets=self.known_secrets,
                                         max_string=c.MAX_TEXT, registry=self.registry)
        if result.failed_closed or type(result.value) is not kind:
            raise c.HelperSettingsError("sanitization_failed")
        tally.redacted += result.redacted_field_count
        tally.truncated += result.truncated_field_count
        tally.categories.update(result.redaction_categories)
        return result.value


async def report(
    request: c.HelperSettingsRequest,
    *,
    session_factory: Callable[[tuple], Any],
    known_secrets: tuple,
    codec: CursorCodec,
    budget: int,
) -> dict:
    """One page of storage helper settings, or a closed ``HelperSettingsError``.

    ``budget`` is the characters the whole reply may take; the page is filled
    to it less this report's own keys.
    """

    fingerprint = codec.fingerprint({"domains": list(request.domains)})
    position = paging.open_cursor(codec, request.cursor, fingerprint)
    try:
        session = session_factory(c.reads_for(request))
        registry = await session.inventory(c.REGISTRY_READ)
        lists = {domain: await session.inventory(c.LIST_READS[domain]) for domain in request.domains}
    except core.CoreReadError:
        raise c.HelperSettingsError("authority_unavailable") from None

    reasons: set[str] = set()
    unavailable = [domain for domain, result in lists.items() if result.status == core.UNAVAILABLE]
    if len(unavailable) == len(lists):
        raise c.HelperSettingsError("sources_unavailable")
    if unavailable:
        reasons.add("source_unavailable")
    for result in (registry, *lists.values()):
        if result.status == core.PARTIAL:
            reasons.update(result.partial)
    if registry.status == core.UNAVAILABLE:
        reasons.add("entity_registry_unavailable")
        index = None
    else:
        index = entity_index(registry)

    clean = _Cleaner(known_secrets)
    stored = [(domain, helper_id, row)
              for domain, result in lists.items() if result.status != core.UNAVAILABLE
              for helper_id, row in result.value.items()]
    # Each id is sanitized on its own so its redaction counts only on the
    # page that returns that helper.
    by_key: dict[str, list] = {}
    for domain, helper_id, row in stored:
        id_tally = _Tally()
        clean_id = clean(helper_id, str, id_tally)
        by_key.setdefault(f"{domain}/{clean_id}", []).append((domain, helper_id, clean_id, row, id_tally))
    entries = []
    for key in sorted(by_key):
        claims = by_key[key]
        if len(claims) > 1:
            # Two ids the sanitizer made equal: never a guess between them.
            reasons.add("duplicate_rows")
            continue
        entries.append((key, *claims[0]))

    digest = paging.set_digest(codec, [entry[0] for entry in entries])
    offset = position.require_set(codec, digest, len(entries))
    window, tallies = [], {}
    for key, domain, helper_id, clean_id, row, id_tally in entries[offset: offset + request.limit]:
        fields, invalid, options_truncated = project(domain, row)
        if invalid:
            reasons.add("field_drift")
        if options_truncated:
            reasons.add("options_truncated")
        if index is None:
            entity_id, status = None, "unresolved"
        else:
            matches = index.get((domain, helper_id), [])
            if len(matches) == 1:
                entity_id, status = matches[0], "resolved"
            elif matches:
                entity_id, status = None, "ambiguous"
            elif registry.status == core.PARTIAL:
                entity_id, status = None, "unresolved"
            else:
                entity_id, status = None, "not_registered"
        field_tally = _Tally()
        fields = clean({**fields, "entity_id": entity_id}, dict, field_tally)
        tallies[key] = (id_tally, field_tally)
        window.append({
            "helper_key": key, "domain": domain, "id": clean_id,
            "entity_id": fields["entity_id"], "entity_id_status": status,
            "name": fields["name"], "icon": fields["icon"], "initial": fields["initial"],
            "settings": fields["settings"], "invalid_fields": invalid,
        })

    def head_for(kept, omitted=()) -> dict:
        # A kept row counts its id and fields; an oversized row omitted from
        # the page shows only its sanitized id in ``omitted_ids``.
        counted = _Tally()
        for key in kept:
            for tally in tallies[key]:
                counted.add(tally)
        for key in omitted:
            counted.add(tallies[key][0])
        page_reasons = set(reasons)
        if counted.redacted:
            page_reasons.add("redacted_by_policy")
        if counted.truncated:
            page_reasons.add("truncated_text")
        page_reasons &= c.PARTIAL_REASONS
        return {
            "schema": c.SCHEMA,
            "completeness": "partial" if page_reasons else "complete",
            "partial_reasons": sorted(page_reasons),
            "domains": list(request.domains),
            "unavailable_domains": unavailable,
            "coverage": c.COVERAGE,
            "redaction": {
                "redacted_field_count": counted.redacted,
                "categories": sorted(counted.categories),
                "truncated_field_count": counted.truncated,
            },
            "sources": [dict(source) for source in session.sources],
        }

    # The page is sized against the whole window's head; the returned head
    # counts only what the page shows, so it is never longer.
    page = paging.fill_page(
        window, codec=codec, query=fingerprint, digest=digest, offset=offset,
        total=len(entries), row_key="helpers", identity="helper_key",
        budget=budget - len(_compact(head_for(tallies))),
    )
    reply = {**head_for([row["helper_key"] for row in page["helpers"]], page.get("omitted_ids", ())), **page}
    if len(_compact(reply)) > budget:
        raise paging.page_exceeds_limit(codec.feature)
    return reply
