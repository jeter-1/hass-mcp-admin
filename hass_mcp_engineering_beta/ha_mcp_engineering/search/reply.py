"""The reply of native ``ha_search`` (HAMCP-481 W3, design sections 5 to 7).

Pure assembly over values the engine already read: no I/O. Every string in
the reply passes ``sanitize_untrusted_data`` with one identity registry per
reply. Records are sanitized one at a time, as the response budget admits
them, and the assembled reply is then checked by a second pass, which must
change nothing. A redaction is disclosed as ``filtered``; a sanitizer that
fails closed fails the read. The reply keys are Row 25's admitted names,
``applied``, ``partial_reasons`` and the relay's three counts.

Ported from ha-mcp 9.2.0, commit 51e3aac788482a081807591cd5d58d07dc4e540b:

- ``src/ha_mcp/tools/tools_search.py`` (sha256
  95a418bb72b97b78d0eb1ce365e4bfaa524698e532da91f901d9658784e5ebdc): the
  ``fields`` projection's always-kept names and the combined paging keys;
- ``src/ha_mcp/tools/response_helpers.py`` (sha256
  514071e5194f6370016edf9cc1b7576483012537649ae9ff2c807a911fb2717f): the
  pagination rule, ``has_more = offset + count < total``.

Changes from the source:

- the reply is bounded by a byte budget; a record the budget does not admit
  moves the page's ``next_offset`` back to it and is disclosed as ``paged``,
  and a per-record cap drops list tails, disclosed as ``truncated``;
- the combined ``next_offset`` is the lower of the two surfaces' offsets, so
  no record of either surface is skipped (the source takes the entity
  surface's first);
- ``partial`` is ``bool(partial_reasons)`` (design section 6.1);
- the unknown-``fields`` typo warning is not emitted (not reviewed).

The ported code carries the source's license notice:

    MIT License

    Copyright (c) 2024 Julien

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Mapping

from ..errors import ErrorCode, GovernanceError
from ..sanitization import ReferenceMarkerRegistry, sanitize_untrusted_data

# The reply budget, measured as ``len(json.dumps(reply, ensure_ascii=True))``.
# It stays under the gateway's 52,000-byte check with room for the transport.
RESPONSE_BUDGET_BYTES = 48_000
# One record's budget. A record over it loses list tails until it fits; a
# record with no list left is under it by construction (its strings are
# capped at ``RECORD_STRING_MAX`` characters).
RECORD_BUDGET_BYTES = 14_000
# Headroom for the reasons and paging values the fit adds after it measures.
FIT_MARGIN_BYTES = 1_024
RECORD_STRING_MAX = 256
QUERY_ECHO_MAX = 1_024
DOMAIN_ECHO_MAX = 64
# The relay's count cap (``providers/upstream_reads_9_2.py``); a test binds it.
COUNT_CAP = 999
ENGINEERING_COUNTS = ("fields_withheld", "fields_dropped_by_policy", "upstream_warnings_replaced")

ENTITY_BUCKET = "entities"
CONFIG_BUCKETS = ("automations", "scripts", "scenes", "helpers", "dashboards")
# A record's list fields, in the order the per-record cap shortens them.
_CAPPED_LISTS = ("member_entity_ids", "aliases", "labels")

# ``tools_search.py`` ``_ALWAYS_KEEP_PROJECTION``, plus ``success`` and
# ``warnings``, which the source also keeps, and Engineering's own keys.
ALWAYS_KEPT = frozenset({
    "query", "search_types", "entity_total_matches", "config_total_matches", "errors",
    "partial", "partial_reason", "count", "offset", "limit", "has_more", "next_offset",
    "entity_has_more", "entity_next_offset", "config_has_more", "config_next_offset",
    "by_domain", "state_filter_note", "area_names", "search_type", "domain_filter",
    "area_filter", "message", "success", "warnings", "applied", "partial_reasons",
    *ENGINEERING_COUNTS,
})

# Closed warning texts (design section 6.4). No caller or provider text is
# ever interpolated into one.
WARNING_ENTITY_SKIPPED = (
    "Entity search skipped: an explicit search_types selects configuration-only search; "
    "omit search_types to search entities."
)
WARNING_CONFIG_SKIPPED = (
    "Configuration search skipped: domain, area and state filters select entity search; "
    "remove them or pass search_types to search configurations."
)
WARNING_AMBIGUOUS_FLOOR = (
    "area_filter matches more than one floor by close spelling, so no area was selected; "
    "pass an exact floor or area name or ID."
)
WARNINGS = (WARNING_ENTITY_SKIPPED, WARNING_CONFIG_SKIPPED, WARNING_AMBIGUOUS_FLOOR)

# Closed ``partial_reasons`` vocabulary (design section 6.1).
REASON_ORDER = ("paged", "truncated", "filtered", "not_searched", "source_unavailable")
REASON_CODES = {
    "paged": frozenset({"response_budget", "more_matches"}),
    "truncated": frozenset({"record_strings", "record_lists", "query_echo", "domain_filter_echo"}),
    "filtered": frozenset({"sanitizer_redaction", "config_bodies_withheld"}),
    "not_searched": frozenset({
        "read_failed", "upstream_partial", "graph_unavailable",
        # HAMCP-447: a YAML dashboard is never read.
        "yaml_dashboards_skipped",
        # HAMCP-501 W6 F1: a sub-call bucket's route body match withheld.
        "body_numeric_query", "body_scrub_degraded", "body_scrub_unverified",
    }),
    "source_unavailable": frozenset({
        "automation_subcall_incomplete", "script_subcall_incomplete",
        "scene_subcall_incomplete",
        "helper_subcall_incomplete", "subcall_not_admitted",
        "malformed_state_row", "area_registry", "floor_registry", "label_registry",
        "device_registry", "entity_aliases",
    }),
}
REASON_SEARCH_TYPES = frozenset({
    None, "entity", "config", "automation", "script", "scene", "helper", "dashboard",
})


class SearchError(GovernanceError):
    """A refusal with a closed message; no caller or provider text."""

    CODES = {
        "no_criteria": ErrorCode.VALIDATION_FAILURE,
        "empty_search_types": ErrorCode.VALIDATION_FAILURE,
        "empty_result_fields": ErrorCode.VALIDATION_FAILURE,
        "invalid_argument": ErrorCode.VALIDATION_FAILURE,
        "source_unavailable": ErrorCode.PROVIDER_UNAVAILABLE,
        "inventory_bound_exceeded": ErrorCode.PROVIDER_ERROR,
        "sanitization_failed": ErrorCode.PROVIDER_ERROR,
    }
    MESSAGES = {
        # ha-mcp 9.2.0's texts (``tools_search.py``) for the first three.
        "no_criteria": ("ha_search requires a non-empty query, or one of "
                        "domain_filter / area_filter / state_filter to enumerate."),
        "empty_search_types": ("search_types must be non-empty if provided; "
                               "omit the parameter to use the default types."),
        "empty_result_fields": ("result_fields must contain at least one key; "
                                "omit the parameter for full records."),
        "invalid_argument": "An ha_search argument is not valid; see details.parameter.",
        "source_unavailable": ("A Home Assistant source this search requires is unavailable; "
                               "see details.source."),
        "inventory_bound_exceeded": ("A Home Assistant inventory is longer than native search "
                                     "reads; see details.source and details.observed."),
        "sanitization_failed": "The search reply could not be sanitized, so it is withheld.",
    }
    PARAMETERS = frozenset({
        "query", "search_types", "limit", "offset", "include_config", "exact_match",
        "area_filter", "domain_filter", "group_by_domain", "per_domain_limit", "fields",
        "result_fields", "include_hidden", "state_filter", "config_time_budget",
    })

    def __init__(self, reason: str, *, parameter: str | None = None,
                 source: str | None = None, observed: int | None = None):
        if reason not in self.CODES:
            reason = "sanitization_failed"
        details: dict[str, Any] = {"reason": reason}
        if parameter in self.PARAMETERS:
            details["parameter"] = parameter
        if type(source) is str and source.isidentifier():
            details["source"] = source
        if type(observed) is int and observed >= 0:
            details["observed"] = observed
        super().__init__(self.CODES[reason], self.MESSAGES[reason], details=details)
        self.reason = reason


class Reasons:
    """The ``partial_reasons`` accumulator: one entry per reason, search type
    and code; a null count makes the merged count null."""

    def __init__(self) -> None:
        self._counts: dict[tuple, int | None] = {}

    def add(self, reason: str, search_type: str | None, code: str, count: int | None) -> None:
        """Add one reason. A zero count adds nothing."""

        if (reason not in REASON_CODES or code not in REASON_CODES[reason]
                or search_type not in REASON_SEARCH_TYPES
                or not (count is None or (type(count) is int and count >= 0))):
            raise ValueError("unknown partial reason")
        if count == 0:
            return
        key = (reason, search_type, code)
        if key in self._counts:
            prior = self._counts[key]
            count = None if prior is None or count is None else prior + count
        self._counts[key] = count

    def copy(self) -> "Reasons":
        other = Reasons()
        other._counts = dict(self._counts)
        return other

    def entries(self) -> list[dict]:
        rank = {reason: index for index, reason in enumerate(REASON_ORDER)}
        keys = sorted(self._counts, key=lambda key: (rank[key[0]], key[1] or "", key[2]))
        return [{"reason": reason, "search_type": search_type, "count": self._counts[key],
                 "code": code} for key in keys for reason, search_type, code in (key,)]


@dataclass(frozen=True, slots=True)
class Surface:
    """One searched surface's page, before the budget fit.

    ``records`` are ``(bucket, record, body_read)`` triples in reply order;
    ``positions`` are their absolute positions in the surface's matches;
    ``page_end`` is ``offset`` plus the page's length before any page-only
    filter.
    """

    name: str  # "entity" or "config"
    records: tuple
    positions: tuple
    total: int
    page_end: int


@dataclass(slots=True)
class ReplySpec:
    query: Any
    search_types: tuple
    applied: dict
    offset: int
    limit: int
    entity: Surface | None = None
    config: Surface | None = None
    dashboards: bool = False
    search_type: str | None = None
    domain_filter: str | None = None
    include_config: bool = False
    warnings: tuple = ()
    reasons: Reasons = field(default_factory=Reasons)
    fields: frozenset | None = None
    # Names the source would have produced that Row 25 withholds: at the top
    # level, and per bucket (counted only when the bucket is kept).
    withheld: frozenset = frozenset()
    bucket_withheld: Mapping = field(default_factory=dict)


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=True))


@dataclass(slots=True)
class _Item:
    bucket: str
    record: dict | None  # None: the bucket is projected away
    redacted: int = 0
    truncated: int = 0
    list_drops: int = 0
    body_read: bool = False


class _Cleaner:
    def __init__(self, known_secrets: tuple) -> None:
        self.known_secrets = tuple(known_secrets)
        self.registry = ReferenceMarkerRegistry()

    def __call__(self, value: Any, max_string: int | None):
        result = sanitize_untrusted_data(value, known_secrets=self.known_secrets,
                                         max_string=max_string, registry=self.registry)
        if result.failed_closed:
            raise SearchError("sanitization_failed")
        return result

    def record(self, bucket: str, record: dict, body_read: bool) -> _Item:
        result = self(record, RECORD_STRING_MAX)
        value = result.value
        if type(value) is not dict:
            raise SearchError("sanitization_failed")
        size = _size(value)
        drops = 0
        for key in _CAPPED_LISTS:
            items = value.get(key)
            while size > RECORD_BUDGET_BYTES and type(items) is list and items:
                last = items.pop()
                drops += 1
                size -= _size(last) + (2 if items else 0)
        if size > RECORD_BUDGET_BYTES or _size(value) != size:
            raise SearchError("sanitization_failed")
        return _Item(bucket, value, result.redacted_field_count, result.truncated_field_count,
                     drops, body_read)


def _keep(spec: ReplySpec) -> frozenset | None:
    return None if spec.fields is None else frozenset(spec.fields) | ALWAYS_KEPT


def _paging(surface: Surface, kept: int, reasons: Reasons):
    if kept < len(surface.records):
        next_offset, code = surface.positions[kept], "response_budget"
    elif surface.page_end < surface.total:
        next_offset, code = surface.page_end, "more_matches"
    else:
        return False, None
    # At least one: a page that has more always discloses it.
    reasons.add("paged", surface.name, code, max(surface.total - next_offset, 1))
    return True, next_offset


def _assemble(spec: ReplySpec, kept: dict, echoes: dict, echo_reasons: Reasons,
              keep: frozenset | None) -> dict:
    reasons = spec.reasons.copy()
    for key, count in echo_reasons._counts.items():
        reasons.add(*key, count)
    surfaces = [surface for surface in (spec.entity, spec.config) if surface is not None]
    redacted = echoes["redacted"]
    paging = {}
    buckets: dict[str, list] = {bucket: [] for bucket in (ENTITY_BUCKET, *CONFIG_BUCKETS)}
    # Top-level names count only when the ``fields`` projection keeps them;
    # record-level names count when their bucket is emitted.
    withheld = {name for name in spec.withheld if keep is None or name in keep}
    bodies = 0
    for surface in surfaces:
        items = kept[surface.name]
        truncated = drops = 0
        for item in items:
            if item.record is None:
                continue
            buckets[item.bucket].append(item.record)
            redacted += item.redacted
            truncated += item.truncated
            drops += item.list_drops
            if item.body_read and spec.include_config:
                bodies += 1
            withheld |= set(spec.bucket_withheld.get(item.bucket, ()))
        reasons.add("truncated", surface.name, "record_strings", truncated)
        reasons.add("truncated", surface.name, "record_lists", drops)
        paging[surface.name] = _paging(surface, len(items), reasons)
    if bodies:
        withheld.add("config")
        reasons.add("filtered", "config", "config_bodies_withheld", bodies)
    reasons.add("filtered", None, "sanitizer_redaction", redacted)
    entries = reasons.entries()

    # The 9.2.0 envelope (``response.py`` ``_new_search_response``): the
    # buckets and both totals are always present; ``dashboards`` only when
    # that type is searched; a surface's paging keys only when it ran.
    entity, config = spec.entity, spec.config
    reply: dict[str, Any] = {"success": True, "query": echoes["query"]}
    reply["entities"] = buckets[ENTITY_BUCKET]
    reply["entity_total_matches"] = entity.total if entity is not None else 0
    for bucket in CONFIG_BUCKETS[:4]:
        reply[bucket] = buckets[bucket]
    if spec.dashboards:
        reply["dashboards"] = buckets["dashboards"]
    reply["search_types"] = list(spec.search_types)
    reply["config_total_matches"] = config.total if config is not None else 0
    reply["partial"] = bool(entries)
    reply["errors"] = []
    if spec.warnings:
        reply["warnings"] = list(spec.warnings)
    reply["applied"] = dict(spec.applied)
    if "domain_filter" in reply["applied"]:
        # The effective domain filter is the sanitized echo: none when entity
        # search did not run.
        reply["applied"]["domain_filter"] = echoes["domain_filter"]
    reply["partial_reasons"] = entries
    reply["count"] = sum(len(buckets[bucket]) for bucket in buckets)
    reply["offset"] = spec.offset
    reply["limit"] = spec.limit
    more = [paging[name] for name in paging]
    reply["has_more"] = any(has for has, _ in more)
    offsets = [value for has, value in more if has]
    reply["next_offset"] = min(offsets) if offsets else None
    if entity is not None:
        reply["entity_has_more"], reply["entity_next_offset"] = paging["entity"]
    if config is not None:
        reply["config_has_more"], reply["config_next_offset"] = paging["config"]
    if entity is not None:
        reply["search_type"] = spec.search_type
        # As the source: the echo only when a domain filter is set.
        if echoes["domain_filter"] is not None:
            reply["domain_filter"] = echoes["domain_filter"]
    reply["fields_withheld"] = min(len(withheld), COUNT_CAP)
    reply["fields_dropped_by_policy"] = 0
    reply["upstream_warnings_replaced"] = 0
    if keep is not None:
        reply = {key: value for key, value in reply.items() if key in keep}
    return reply


def build_reply(spec: ReplySpec, *, known_secrets: tuple = (),
                budget: int = RESPONSE_BUDGET_BYTES) -> dict:
    """Fit, page, sanitize and assemble one reply."""

    if type(budget) is not int or budget <= 0:
        raise ValueError("invalid budget")
    for text in spec.warnings:
        if text not in WARNINGS:
            raise ValueError("unknown warning")
    clean = _Cleaner(known_secrets)
    keep = _keep(spec)
    echo_reasons = Reasons()
    echoes: dict[str, Any] = {"query": None, "domain_filter": None, "redacted": 0}
    for key, limit, code in (("query", QUERY_ECHO_MAX, "query_echo"),
                             ("domain_filter", DOMAIN_ECHO_MAX, "domain_filter_echo")):
        value = getattr(spec, key)
        if type(value) is str:
            result = clean(value, limit)
            echoes[key] = result.value
            echoes["redacted"] += result.redacted_field_count
            echo_reasons.add("truncated", None, code, result.truncated_field_count)
        elif value is not None:
            raise ValueError("invalid echo")

    surfaces = [surface for surface in (spec.entity, spec.config) if surface is not None]
    kept: dict[str, list[_Item]] = {surface.name: [] for surface in surfaces}
    used = _size(_assemble(spec, kept, echoes, echo_reasons, keep))
    cursor = {surface.name: 0 for surface in surfaces}
    active = list(surfaces)
    # Round robin, so neither surface starves the other. The first emitted
    # record of each surface is always admitted.
    while active:
        for surface in list(active):
            index = cursor[surface.name]
            if index >= len(surface.records):
                active.remove(surface)
                continue
            bucket, record, body_read = surface.records[index]
            if keep is not None and bucket not in keep:
                kept[surface.name].append(_Item(bucket, None))
                cursor[surface.name] += 1
                continue
            item = clean.record(bucket, record, body_read)
            cost = _size(item.record) + 2
            emitted = any(other.record is not None for other in kept[surface.name])
            if emitted and used + cost + FIT_MARGIN_BYTES > budget:
                active.remove(surface)
                continue
            kept[surface.name].append(item)
            used += cost
            cursor[surface.name] += 1

    reply = _assemble(spec, kept, echoes, echo_reasons, keep)
    while _size(reply) > budget:
        # The margin was not enough: drop the last emitted record of the
        # surface holding more.
        names = sorted(kept, key=lambda name: -sum(1 for item in kept[name] if item.record))
        if not names or not any(item.record for item in kept[names[0]]):
            raise SearchError("sanitization_failed")
        items = kept[names[0]]
        while items and items.pop().record is None:
            pass
        reply = _assemble(spec, kept, echoes, echo_reasons, keep)

    # Every string is now Engineering's constant text or a value a pass above
    # produced. The check pass must change nothing.
    check = clean(reply, None)
    if check.redacted_field_count or check.truncated_field_count or check.value != reply:
        raise SearchError("sanitization_failed")
    return check.value
