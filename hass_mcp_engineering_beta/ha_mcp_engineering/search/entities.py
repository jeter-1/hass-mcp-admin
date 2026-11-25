"""The entity surface of native ``ha_search`` (HAMCP-481 W3, design sections
3.2 to 3.4 and 6.2).

Pure over the inventories the engine already read, plus one injected alias
reader (``CoreReadSession.entity_entries``). It reads nothing else, and it
never falls back to the delegated route.

Failure (design section 6.2). A source that a requested filter depends on
must be usable, or the search fails with a closed ``SearchError``. A usable
source finished with no dropped row and no drifted field that the search
uses. The states read is the exception (G-f): :func:`state_rows` parses it
once for both surfaces, its malformed rows are left out everywhere, and the
engine counts them as one ``source_unavailable`` ``malformed_state_row``
reason. A source that only an enrichment field needs makes that field
null, counted as ``source_unavailable``. A failed alias read never fails the
search (G-h): the entities whose aliases are unknown are counted as
``source_unavailable`` ``entity_aliases``, and their ``aliases`` field is
null. Any inventory over its bound fails the search (design section 6.3).

ha-mcp 9.2.0's visibility filter is not ported (HAMCP-499). The only hidden
set is the entity registry's ``hidden_by``, and ``include_hidden`` false
removes it.

Ported from ha-mcp 9.2.0, commit 51e3aac788482a081807591cd5d58d07dc4e540b:

- ``src/ha_mcp/tools/search/entities.py`` (sha256
  650dcd0ea85c63541f6220696767e7db568d097214627496c09d2129762b7500): the
  record, enrichment and membership fields, the exact match, the state
  filter and the enrichment join;
- ``src/ha_mcp/tools/search/modes.py`` (sha256
  e507c41998ce589f800e95a123c9d96cb1f79976e932892430d34e2fab62a1da): the
  area, domain and state listings and the area-plus-query mode;
- ``src/ha_mcp/tools/smart_search/_entities.py`` (sha256
  47d071687160cc7622fd83176284a038de544247a69bc827fd9b1003c553ac80): the
  area and floor ladder, the area entity set and the hidden-member set;
- ``src/ha_mcp/utils/device_registry_semantics.py`` (sha256
  4c6d964d73ddf7992e88c7d290f6fc16e3b59c340917a10c0c3a3de03f814332): the
  effective device and entity areas;
- ``src/ha_mcp/utils/entity_membership.py`` (sha256
  98ff97e7ffbe0cc74a428d8011cf2110d9170cfdbd09574bc8efda7d7da74b15): the
  group member normalization.

Changes from the source:

- there is no fuzzy-to-exact fallback and no suggestion list;
- a registry a filter needs fails the search instead of degrading with a
  warning; the floor registry fails it only once the area ladder passes its
  exact-area step;
- the floor-expansion, area-and-floor and enrichment-incomplete warnings are
  not emitted, and the composed ``name`` is never served (a closed warning
  says so);
- a state row whose ``friendly_name`` is present but not a string is
  malformed;
- enrichment reads the entity registry list row and the ``get_entries``
  aliases;
- in the area-plus-query mode, paging comes from the page before the state
  filter, as in the fuzzy mode (the source counts the filtered page);
- the fuzzy threshold is 60, the source's setting default;
- records list their keys in one canonical order.

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

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping

from . import core_contracts as c
from . import scoring
from .core_reads import INVALID, ReadResult
from .reply import (
    WARNING_AMBIGUOUS_FLOOR,
    WARNING_COMPOSED_NAME,
    WARNING_STATE_PAGE_ONLY,
    ENTITY_BUCKET,
    Reasons,
    SearchError,
    Surface,
)

RECORD_KEYS = ("entity_id", "friendly_name", "domain", "state", "score", "match_type")
ENRICHMENT_FIELDS = ("area", "floor", "labels", "aliases")
MEMBERSHIP_FIELDS = ("is_group", "member_entity_ids")
COMPOSED_NAME_FIELD = "name"
# ``search/entities.py`` ``_ALLOWED_RESULT_FIELDS``.
RESULT_FIELDS = frozenset((*RECORD_KEYS, *ENRICHMENT_FIELDS, *MEMBERSHIP_FIELDS,
                           COMPOSED_NAME_FIELD))

EXACT_MATCH = "exact_match"
FUZZY_SEARCH = "fuzzy_search"
AREA_FILTERED_QUERY = "area_filtered_query"
AREA_ONLY = "area_only"
DOMAIN_LISTING = "domain_listing"
STATE_LISTING = "state_listing"
# Modes whose state filter applies to the returned page only (design
# section 3.4).
PAGE_FILTERED_MODES = frozenset({FUZZY_SEARCH, AREA_FILTERED_QUERY})
AREA_MODES = frozenset({AREA_FILTERED_QUERY, AREA_ONLY})

# The registry fields each read must carry, valid, for a use.
_AREA_LADDER_FIELDS = ("name", "aliases", "floor_id")
_FLOOR_LADDER_FIELDS = ("name", "aliases")
_DEVICE_AREA_FIELDS = ("area_id", "parent_device_id")
# The sources of each enrichment field, and the fields each must carry.
FIELD_SOURCES = {
    "area": (("area_registry", ("name",)), ("device_registry", _DEVICE_AREA_FIELDS)),
    "floor": (("area_registry", ("floor_id",)), ("device_registry", _DEVICE_AREA_FIELDS),
              ("floor_registry", ("name",))),
    "labels": (("device_registry", ("labels",)), ("label_registry", ("name",))),
}
_REGISTRY_REASON = {
    "area_registry": "area_registry", "floor_registry": "floor_registry",
    "label_registry": "label_registry", "device_registry": "device_registry",
}

# ``entity_membership.py`` ``_is_entity_id``.
_MEMBER_ID = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+\Z", re.ASCII)
_MEMBER_KEYS = ("group_entities", "entity_id")

AliasReader = Callable[[tuple], Awaitable[ReadResult]]


@dataclass(frozen=True, slots=True)
class EntityRequest:
    """The entity-search arguments, as the engine normalized them.

    ``query`` is stripped and non-empty, or None; ``domain_filter`` and
    ``state_filter`` are stripped and lowercased; ``area_filter`` is
    stripped. An empty filter is None.
    """

    query: str | None
    exact_match: bool
    area_filter: str | None
    domain_filter: str | None
    state_filter: str | None
    include_hidden: bool
    limit: int
    offset: int
    result_fields: tuple | None = None

    def __post_init__(self) -> None:
        for name, normal in (("query", str.strip), ("area_filter", str.strip),
                             ("domain_filter", lambda text: text.strip().lower()),
                             ("state_filter", lambda text: text.strip().lower())):
            value = getattr(self, name)
            if value is not None and (type(value) is not str or not value
                                      or normal(value) != value):
                raise ValueError(f"invalid {name}")
        if type(self.exact_match) is not bool or type(self.include_hidden) is not bool:
            raise ValueError("invalid flag")
        if (type(self.limit) is not int or self.limit < 1
                or type(self.offset) is not int or self.offset < 0):
            raise ValueError("invalid paging")
        fields = self.result_fields
        if fields is not None and (type(fields) is not tuple or not fields
                                   or not all(type(item) is str and item in RESULT_FIELDS
                                              for item in fields)):
            raise ValueError("invalid result_fields")
        if (self.query is None and self.area_filter is None and self.domain_filter is None
                and self.state_filter is None):
            raise ValueError("no criteria")


@dataclass(frozen=True, slots=True)
class EntityOutcome:
    surface: Surface
    search_type: str
    warnings: tuple = ()


@dataclass(frozen=True, slots=True)
class _State:
    state: str
    attributes: Mapping
    friendly_name: str


def mode(request: EntityRequest) -> str:
    """The 9.2.0 mode selection (``modes.py``)."""

    if request.area_filter is not None:
        return AREA_FILTERED_QUERY if request.query is not None else AREA_ONLY
    if request.query is None:
        return DOMAIN_LISTING if request.domain_filter is not None else STATE_LISTING
    return EXACT_MATCH if request.exact_match else FUZZY_SEARCH


def _requested(request: EntityRequest, names: tuple) -> tuple:
    """The requested names among ``names``, in canonical order. Projecting
    ``member_entity_ids`` keeps ``is_group``."""

    if request.result_fields is None:
        return ()
    wanted = set(request.result_fields)
    if "member_entity_ids" in wanted:
        wanted.add("is_group")
    return tuple(name for name in names if name in wanted)


def _entity_registry_fields(request: EntityRequest) -> tuple:
    fields = ["hidden_by"]
    enrichment = _requested(request, ENRICHMENT_FIELDS)
    if mode(request) in AREA_MODES or set(enrichment) & {"area", "floor", "labels"}:
        fields += ["area_id", "device_id"]
    if "labels" in enrichment:
        fields.append("labels")
    return tuple(fields)


def reads_for(request: EntityRequest) -> tuple:
    """The inventories one entity search reads, in a fixed order. Aliases are
    read separately, through the alias reader."""

    names = {"states", "entity_registry"}
    current = mode(request)
    if current in AREA_MODES:
        names |= {"area_registry", "device_registry", "floor_registry"}
    for field_name in _requested(request, ENRICHMENT_FIELDS):
        names |= {source for source, _ in FIELD_SOURCES.get(field_name, ())}
    order = ("states", "entity_registry", "area_registry", "floor_registry",
             "label_registry", "device_registry")
    return tuple(name for name in order if name in names)


def reads_aliases(request: EntityRequest) -> bool:
    """Whether the search reads ``get_entries`` aliases."""

    return mode(request) in PAGE_FILTERED_MODES or "aliases" in _requested(request, ENRICHMENT_FIELDS)


def source_result(sources: Mapping, name: str) -> ReadResult:
    result = sources.get(name)
    if type(result) is not ReadResult or result.read != name:
        raise SearchError("source_unavailable", source=name)
    if result.reason == "inventory_bound_exceeded":
        raise SearchError("inventory_bound_exceeded", source=name, observed=result.observed)
    return result


def _usable(result: ReadResult, fields: tuple) -> bool:
    """Finished, with no dropped row and no drifted field among ``fields``."""

    if result.status not in (c.COMPLETE, c.PARTIAL) or result.dropped:
        return False
    rows = result.value
    if not isinstance(rows, Mapping):
        return False
    return all(row.get(name) is not INVALID for row in rows.values() for name in fields)


def required_rows(sources: Mapping, name: str, fields: tuple) -> Mapping:
    result = source_result(sources, name)
    if not _usable(result, fields):
        raise SearchError("source_unavailable", source=name)
    return result.value


@dataclass(frozen=True, slots=True)
class StateRows:
    """The usable state rows by entity id, in Core's order; the ids of kept
    rows with an invalid field; and the malformed count (G-f: dropped rows
    plus those kept rows)."""

    rows: Mapping
    malformed: frozenset
    count: int


def state_rows(result: ReadResult) -> StateRows:
    """Parse the states read once; both surfaces share the result."""

    if (type(result) is not ReadResult or result.read != "states"
            or result.status not in (c.COMPLETE, c.PARTIAL) or not isinstance(result.value, Mapping)):
        raise SearchError("source_unavailable", source="states")
    rows: dict[str, _State] = {}
    malformed: set[str] = set()
    for entity_id, row in result.value.items():
        state, attributes = row.get("state"), row.get("attributes")
        name = entity_id if attributes is INVALID else attributes.get("friendly_name", entity_id)
        if state is INVALID or attributes is INVALID or type(name) is not str:
            malformed.add(entity_id)
            continue
        rows[entity_id] = _State(state, attributes, name)
    return StateRows(MappingProxyType(rows), frozenset(malformed), result.dropped + len(malformed))


def effective_device_area_id(device: Mapping, devices: Mapping) -> str | None:
    """``device_registry_semantics.py`` ``effective_device_area_id``."""

    direct = device.get("area_id")
    if direct is not None:
        return direct if type(direct) is str and direct else None
    parent_id = device.get("parent_device_id")
    if type(parent_id) is not str or not parent_id:
        return None
    parent = devices.get(parent_id)
    if parent is None or parent.get("parent_device_id") is not None:
        return None
    area = parent.get("area_id")
    return area if type(area) is str and area else None


def effective_entity_area_id(entity: Mapping, device_areas: Mapping) -> str | None:
    """``device_registry_semantics.py`` ``effective_entity_area_id``."""

    direct = entity.get("area_id")
    if direct is not None:
        return direct if type(direct) is str and direct else None
    device_id = entity.get("device_id")
    if type(device_id) is not str or not device_id:
        return None
    area = device_areas.get(device_id)
    return area if type(area) is str and area else None


def _device_areas(devices: Mapping) -> dict:
    return {device_id: effective_device_area_id(row, devices) for device_id, row in devices.items()}


def member_entity_ids(attributes: Any) -> list | None:
    """``entity_membership.py`` ``normalize_member_entity_ids`` over JSON
    values: the first of ``group_entities`` and ``entity_id`` that is a list
    of entity ids, sorted and unique; None when neither is."""

    if not isinstance(attributes, Mapping):
        return None
    for key in _MEMBER_KEYS:
        raw = attributes.get(key)
        if type(raw) is not list:
            continue
        if all(type(item) is str and _MEMBER_ID.fullmatch(item) for item in raw):
            return sorted(set(raw))
    return None


def _membership(record: dict, attributes: Any, requested: tuple, denied: frozenset) -> None:
    """``search/entities.py`` ``_add_membership_fields``: a group with a
    denied member keeps ``is_group`` and withholds its members."""

    if not requested:
        return
    members = member_entity_ids(attributes)
    redacted = members is not None and bool(denied.intersection(members))
    if "is_group" in requested:
        record["is_group"] = members is not None
    if "member_entity_ids" in requested and members is not None and not redacted:
        record["member_entity_ids"] = members


# --- The area ladder (``smart_search/_entities.py`` ``_resolve_area_query``).

def _exact_registry_ids(rows: Mapping, query_lower: str) -> set:
    return {
        row_id for row_id, row in rows.items()
        if query_lower == row_id.lower() or query_lower == row["name"].lower()
        or any(query_lower == alias.lower() for alias in row["aliases"])
    }


def _fuzzy_registry_ids(rows: Mapping, query_lower: str) -> tuple[set, int]:
    best_ids: set[str] = set()
    best = 0
    for row_id, row in rows.items():
        values = (row_id, row["name"], *row["aliases"])
        score = max((scoring.ratio(query_lower, value.lower()) for value in values if value),
                    default=0)
        if score > best:
            best_ids, best = {row_id}, score
        elif score == best:
            best_ids.add(row_id)
    return (best_ids, best) if best >= scoring.AREA_QUERY_FUZZY_THRESHOLD else (set(), best)


def _fuzzy_area_ids(areas: Mapping, query_lower: str) -> set:
    """``_match_area_ids`` after its exact step: a partial ratio of 80 on the
    name, the id or an alias."""

    return {
        area_id for area_id, row in areas.items()
        if max(scoring.partial_ratio(query_lower, row["name"].lower()),
               scoring.partial_ratio(query_lower, area_id.lower()),
               max((scoring.partial_ratio(query_lower, alias.lower()) for alias in row["aliases"]),
                   default=0)) >= scoring.AREA_QUERY_FUZZY_THRESHOLD
    }


def _floor_areas(areas: Mapping, floor_ids: set) -> set:
    return {area_id for area_id, row in areas.items() if row["floor_id"] in floor_ids}


def resolve_area(area_filter: str, areas: Mapping, floors: Callable[[], Mapping]) -> tuple[set, bool]:
    """The matched area ids, and whether a fuzzy floor match was ambiguous.

    ``floors`` returns the floor registry, or raises when it is not usable;
    it is called only once the exact-area step has not matched.
    """

    query_lower = area_filter.lower().strip()
    exact = _exact_registry_ids(areas, query_lower)
    if exact:
        return exact, False
    floor_rows = floors()
    exact_floors = _exact_registry_ids(floor_rows, query_lower)
    if exact_floors:
        return _floor_areas(areas, exact_floors), False
    fuzzy_floors, floor_score = _fuzzy_registry_ids(floor_rows, query_lower)
    _, area_score = _fuzzy_registry_ids(areas, query_lower)
    if len(fuzzy_floors) > 1 and floor_score > area_score:
        return set(), True
    if len(fuzzy_floors) == 1 and floor_score > area_score:
        return _floor_areas(areas, fuzzy_floors), False
    return _fuzzy_area_ids(areas, query_lower), False


# --- Aliases and enrichment.

async def _read_aliases(entity_ids: list, reader: AliasReader, reasons: Reasons) -> dict:
    """Known aliases by entity id. An entity whose aliases are unknown is
    absent and counted once (G-h); an entity with no registry entry has
    none."""

    if not entity_ids:
        return {}
    result = await reader(tuple(entity_ids))
    if type(result) is not ReadResult or result.read != "entity_registry_entries":
        raise SearchError("source_unavailable", source="entity_registry_entries")
    value = result.value if result.status in (c.COMPLETE, c.PARTIAL) else None
    known: dict[str, tuple] = {}
    for entity_id in entity_ids:
        entry = value.get(entity_id, INVALID) if isinstance(value, Mapping) else INVALID
        if entry is None:
            known[entity_id] = ()
        elif isinstance(entry, Mapping) and type(entry.get("aliases")) is tuple:
            known[entity_id] = entry["aliases"]
    reasons.add("source_unavailable", "entity", "entity_aliases", len(entity_ids) - len(known))
    return known


class _Enricher:
    """``search/entities.py`` ``_entity_enrichment_fields`` over the entity
    registry list row and the ``get_entries`` aliases."""

    def __init__(self, request: EntityRequest, sources: Mapping, registry: Mapping) -> None:
        self.requested = _requested(request, ENRICHMENT_FIELDS)
        self.registry = registry
        self.unusable: set[str] = set()
        self.null: set[str] = set()
        rows: dict[str, Mapping] = {}
        for field_name in self.requested:
            for source, fields in FIELD_SOURCES.get(field_name, ()):
                result = source_result(sources, source)
                if _usable(result, fields):
                    rows[source] = result.value
                else:
                    self.unusable.add(source)
                    self.null.add(field_name)
        self.rows = rows
        devices = rows.get("device_registry")
        self.device_areas = _device_areas(devices) if devices is not None else {}

    def disclose(self, count: int, reasons: Reasons) -> None:
        for source in sorted(self.unusable):
            reasons.add("source_unavailable", "entity", _REGISTRY_REASON[source], count)

    def fields(self, entity_id: str, aliases: Mapping) -> dict:
        entry = self.registry.get(entity_id) or {}
        areas = self.rows.get("area_registry", {})
        floors = self.rows.get("floor_registry", {})
        labels = self.rows.get("label_registry", {})
        devices = self.rows.get("device_registry", {})
        area_id = effective_entity_area_id(entry, self.device_areas)
        area = areas.get(area_id) if area_id else None
        floor_id = area.get("floor_id") if area else None
        floor = floors.get(floor_id) if floor_id else None
        label_ids = set(entry.get("labels") or ())
        device_id = entry.get("device_id")
        device = devices.get(device_id) if device_id else None
        if device:
            label_ids |= set(device.get("labels") or ())
        known = aliases.get(entity_id)
        full = {
            "area": area.get("name") if area else None,
            "floor": floor.get("name") if floor else None,
            "labels": [(labels.get(label_id) or {}).get("name") or label_id
                       for label_id in sorted(label_ids)],
            "aliases": sorted(known) if known is not None else None,
        }
        for field_name in self.null:
            full[field_name] = None
        return {field_name: full[field_name] for field_name in self.requested}


# --- The search.

def _record(entity_id: str, friendly_name: str, domain: str, state: str, score: int,
            match_type: str) -> dict:
    return {"entity_id": entity_id, "friendly_name": friendly_name, "domain": domain,
            "state": state, "score": score, "match_type": match_type}


def _from_match(match: scoring.EntityMatch, state: str) -> dict:
    return _record(match.entity_id, match.friendly_name, match.domain, state, match.score,
                   match.match_type)


def _state_matches(record: dict, state_filter: str) -> bool:
    """``search/entities.py`` ``_state_matches``."""

    return (record.get("state") or "").lower() == state_filter


def _order(records: list) -> list:
    return sorted(records, key=lambda record: (-record["score"], record["entity_id"]))


async def search_entities(request: EntityRequest, sources: Mapping, states: StateRows,
                          aliases: AliasReader, reasons: Reasons) -> EntityOutcome:
    """One entity search over the read inventories. The caller parsed the
    states (:func:`state_rows`) and disclosed their malformed rows."""

    if (type(request) is not EntityRequest or type(states) is not StateRows
            or type(reasons) is not Reasons):
        raise ValueError("invalid entity search")
    current = mode(request)
    for name in reads_for(request):
        source_result(sources, name)
    malformed_ids = states.malformed
    states = states.rows
    registry = required_rows(sources, "entity_registry", _entity_registry_fields(request))
    hidden = frozenset(entity_id for entity_id, row in registry.items()
                       if row["hidden_by"] is not None)
    denied = frozenset() if request.include_hidden else hidden
    domain = request.domain_filter
    state_filter = request.state_filter
    warnings: list[str] = []
    alias_map: dict | None = None

    def in_domain(entity_id: str) -> bool:
        return domain is None or scoring.entity_domain(entity_id) == domain

    def state_of(entity_id: str) -> _State:
        # An area entity with no usable state row is listed from the registry.
        return states.get(entity_id) or _State("unknown", {}, entity_id)

    if current == EXACT_MATCH:
        query_lower = request.query.lower().strip()
        records = []
        for entity_id, row in states.items():
            if entity_id in denied or not in_domain(entity_id):
                continue
            match = scoring.exact_entity_match(
                scoring.ScoringEntity(entity_id, row.friendly_name, (), entity_id in hidden),
                query_lower)
            if match is not None:
                records.append(_from_match(match, row.state))
    elif current == FUZZY_SEARCH:
        candidates = [entity_id for entity_id in states
                      if entity_id not in denied and in_domain(entity_id)]
        alias_map = await _read_aliases(candidates, aliases, reasons)
        corpus = [scoring.ScoringEntity(entity_id, states[entity_id].friendly_name,
                                        alias_map.get(entity_id, ()), entity_id in hidden)
                  for entity_id in candidates]
        records = [_from_match(match, states[match.entity_id].state)
                   for match in scoring.fuzzy_entity_matches(corpus, request.query)]
    elif current in AREA_MODES:
        areas = required_rows(sources, "area_registry", _AREA_LADDER_FIELDS)
        devices = required_rows(sources, "device_registry", _DEVICE_AREA_FIELDS)
        matched, ambiguous = resolve_area(
            request.area_filter, areas,
            lambda: required_rows(sources, "floor_registry", _FLOOR_LADDER_FIELDS))
        if ambiguous:
            warnings.append(WARNING_AMBIGUOUS_FLOOR)
        device_areas = _device_areas(devices)
        members = [entity_id for entity_id, row in registry.items()
                   if entity_id not in denied and entity_id not in malformed_ids
                   and in_domain(entity_id)
                   and effective_entity_area_id(row, device_areas) in matched]
        if current == AREA_ONLY:
            records = [_record(entity_id, state_of(entity_id).friendly_name,
                               scoring.entity_domain(entity_id), state_of(entity_id).state,
                               scoring.apply_hidden_penalty(100, entity_id in hidden), "area_match")
                       for entity_id in members]
        else:
            members.sort()
            alias_map = await _read_aliases(members, aliases, reasons)
            corpus = [scoring.ScoringEntity(entity_id, state_of(entity_id).friendly_name,
                                            alias_map.get(entity_id, ()), entity_id in hidden)
                      for entity_id in members]
            records = [_from_match(match, state_of(match.entity_id).state)
                       for match in scoring.fuzzy_entity_matches(
                           corpus, request.query, scoring.AREA_QUERY_FUZZY_THRESHOLD)]
    elif current == DOMAIN_LISTING:
        records = [_record(entity_id, row.friendly_name, domain, row.state,
                           scoring.apply_hidden_penalty(100, entity_id in hidden), DOMAIN_LISTING)
                   for entity_id, row in states.items()
                   if scoring.entity_domain(entity_id) == domain and entity_id not in denied]
    else:
        records = [_record(entity_id, row.friendly_name, scoring.entity_domain(entity_id),
                           row.state, scoring.apply_hidden_penalty(100, entity_id in hidden),
                           STATE_LISTING)
                   for entity_id, row in states.items()
                   if entity_id not in denied and _state_matches({"state": row.state}, state_filter)]

    offset, limit = request.offset, request.limit
    if current in PAGE_FILTERED_MODES:
        # 9.2.0 pages the matches, then filters the page by state.
        ordered = _order(records)
        window = ordered[offset:offset + limit]
        page_end = offset + len(window)
        page = [(offset + index, record) for index, record in enumerate(window)
                if state_filter is None or _state_matches(record, state_filter)]
        total = len(ordered)
        if state_filter is not None:
            warnings.append(WARNING_STATE_PAGE_ONLY)
    else:
        ordered = _order([record for record in records
                          if state_filter is None or _state_matches(record, state_filter)])
        window = ordered[offset:offset + limit]
        page_end = offset + len(window)
        page = [(offset + index, record) for index, record in enumerate(window)]
        total = len(ordered)

    enricher = _Enricher(request, sources, registry)
    if enricher.requested:
        if "aliases" in enricher.requested and alias_map is None:
            alias_map = await _read_aliases([record["entity_id"] for _, record in page],
                                            aliases, reasons)
        enricher.disclose(len(page), reasons)
    membership = _requested(request, MEMBERSHIP_FIELDS)
    keep = None
    if request.result_fields is not None:
        keep = set(request.result_fields)
        if "member_entity_ids" in keep:
            keep.add("is_group")
        if COMPOSED_NAME_FIELD in keep:
            warnings.append(WARNING_COMPOSED_NAME)
    emitted = []
    for _, record in page:
        entity_id = record["entity_id"]
        if enricher.requested:
            record.update(enricher.fields(entity_id, alias_map or {}))
        _membership(record, state_of(entity_id).attributes, membership, denied)
        if keep is not None:
            record = {key: value for key, value in record.items() if key in keep}
        emitted.append((ENTITY_BUCKET, record, False))
    surface = Surface("entity", tuple(emitted), tuple(position for position, _ in page),
                      total, page_end)
    return EntityOutcome(surface, current, tuple(warnings))
