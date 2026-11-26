"""The entity surface of native ``ha_search`` (HAMCP-481 W3, design sections
3.2 to 3.4 and 6.2; HAMCP-501 W6b component-route parity).

Pure over the inventories the engine already read, plus one injected alias
reader (``CoreReadSession.entity_entries``). It reads nothing else, and it
never falls back to the delegated route.

Failure (design section 6.2). A source that a requested filter or a scored
text depends on must be usable, or the search fails with a closed
``SearchError``. A usable source finished with no dropped row and no drifted
field that the search uses. The states read is the exception (G-f):
:func:`state_rows` parses it once for both surfaces, its malformed rows are
left out everywhere, and the engine counts them as one ``source_unavailable``
``malformed_state_row`` reason. A source that only an enrichment field needs
makes that field null, counted as ``source_unavailable``; in a listing, an
unusable device registry makes ``name`` null the same way. A failed alias
read never fails the search (G-h): the entities whose aliases are unknown
are counted as ``source_unavailable`` ``entity_aliases``, are scored without
aliases, and their ``aliases`` field is null. Any inventory over its bound
fails the search (design section 6.3).

ha-mcp 9.2.0's visibility filter is not ported (HAMCP-499). The only hidden
set is the entity registry's ``hidden_by``, and ``include_hidden`` false
removes it.

Live ha-mcp 9.2.0 answers ``ha_search`` on the ``ha_mcp_tools`` component
route, so that route is the parity basis (HAMCP-501 W6b). From ha-mcp
51e3aac788482a081807591cd5d58d07dc4e540b:

- ``custom_components/ha_mcp_tools/websocket_api/search.py`` (sha256
  5f1a1e312884494245f71c4720c5ddd3ac3d4211f9c8257e9be9460be36781a8): the
  domain, hidden, state and area filters before scoring and paging
  (``_search_entities`` 340-392), the registry join (``_registry_enrichment``
  475-527), the scored texts (``_entity_record`` 530-587) and the match types
  (395-472, ported in :mod:`scoring`);
- ``custom_components/ha_mcp_tools/websocket_api/registry.py`` (sha256
  7a40b10626f042de644aa88c6b06342ecfd525b3d7ac3bbf49dd1bbcbc0cb0bc): the
  composed name (``_composed_entity_name`` 456-467), the label names
  (``_label_names`` 362-368) and the effective area (621-631);
- ``custom_components/ha_mcp_tools/search_locations.py`` (sha256
  4567798ee26475efd88e9ed14f9261262e84132d4f453bd6f32789c038c30fd3): the
  area query ladder (``_resolve_area_query`` 172-218, whose area sets equal
  the server's below);
- ``src/ha_mcp/tools/search/component.py`` (sha256
  bf14215be89494d531f0876973ffa2056e18b5813e0446fd2b97652dccaad7d3): the
  record shape, the six keys then ``name`` (``_shape_component_search_response``
  556-640), and the listing labels (``_component_listing_metadata`` 536-553).

The composed ``name`` is Core 2026.11.3's ``async_get_full_entity_name``
(commit 92da9f154079459b9e547126f9bd9fce726de16a,
``homeassistant/helpers/entity_registry.py``, sha256
a16d82bb68ceb680875c9fd8905d7c6f30d0cd39ed7ee7a12ded2c4a9d565088: 663-682,
527-630; ``_entity_next_name_part`` 252-260; the wire ``original_name``,
already unprefixed, ``as_partial_dict`` 393-425), over
``homeassistant/helpers/device_registry.py`` (sha256
b5a1ea7b8c622f9dffb1463b954f7899ab1345e260a53079a2574d94dd647f11: the main
device's ``next_name_part`` 735-738 and its ``parent_device_id`` of None 785;
the child device's 884-889, and its row without manufacturer or model
891-910). :func:`composed_name` derives each ``next_name_part`` from
``area_id``, ``device_id`` and ``parent_device_id``; a wire
``next_name_part``, when present, must equal it, or the registry is unusable.

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
- a registry a filter or a scored text needs fails the search instead of
  degrading with a warning; in the area-only mode the floor registry fails
  it only once the area ladder passes its exact-area step;
- the floor-expansion, area-and-floor and enrichment-incomplete warnings are
  not emitted;
- a state row whose ``friendly_name`` is present but not a string is
  malformed;
- enrichment reads the entity registry list row and the ``get_entries``
  aliases;
- records list their keys in one canonical order.

Changes from the component route:

- the area modes list the registry entities of the matched areas, with or
  without a state row, as 9.2.0's server-side area set does; the component
  iterates the states only;
- an entity is hidden when ``hidden_by`` is not None (the component tests
  its truth; Core sets an enum or None);
- a device registry with a dropped or duplicate row is unusable, so a query
  fails, where the component drops the conflicting identity;
- composite devices are not modelled.

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
    ENTITY_BUCKET,
    Reasons,
    SearchError,
    Surface,
)

RECORD_KEYS = ("entity_id", "friendly_name", "domain", "state", "score", "match_type")
ENRICHMENT_FIELDS = ("area", "floor", "labels", "aliases")
MEMBERSHIP_FIELDS = ("is_group", "member_entity_ids")
# Every record carries it, right after ``match_type`` (component.py 586-602).
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
QUERY_MODES = frozenset({EXACT_MATCH, FUZZY_SEARCH, AREA_FILTERED_QUERY})
# Modes whose state filter applies to the returned page only. None: the
# component route filters by state before scoring and paging (search.py
# 361-372).
PAGE_FILTERED_MODES = frozenset()
AREA_MODES = frozenset({AREA_FILTERED_QUERY, AREA_ONLY})

# The registry fields each read must carry, valid, for a use.
_AREA_LADDER_FIELDS = ("name", "aliases", "floor_id")
_FLOOR_LADDER_FIELDS = ("name", "aliases")
_DEVICE_AREA_FIELDS = ("area_id", "parent_device_id")
_ENTITY_NAME_FIELDS = ("name", "original_name", "area_id", "device_id", "next_name_part")
_DEVICE_NAME_FIELDS = ("name", "name_by_user", "area_id", "parent_device_id", "next_name_part")
_DEVICE_TEXT_FIELDS = (*_DEVICE_NAME_FIELDS, "labels", "manufacturer", "model")
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
    fields = ["hidden_by", *_ENTITY_NAME_FIELDS]
    if mode(request) in QUERY_MODES or "labels" in _requested(request, ENRICHMENT_FIELDS):
        fields.append("labels")
    return tuple(fields)


def reads_for(request: EntityRequest) -> tuple:
    """The inventories one entity search reads, in a fixed order. Aliases are
    read separately, through the alias reader."""

    names = {"states", "entity_registry", "device_registry"}
    current = mode(request)
    if current in QUERY_MODES:
        names |= {"area_registry", "floor_registry", "label_registry"}
    if current in AREA_MODES:
        names |= {"area_registry", "floor_registry"}
    for field_name in _requested(request, ENRICHMENT_FIELDS):
        names |= {source for source, _ in FIELD_SOURCES.get(field_name, ())}
    order = ("states", "entity_registry", "area_registry", "floor_registry",
             "label_registry", "device_registry")
    return tuple(name for name in order if name in names)


def reads_aliases(request: EntityRequest) -> bool:
    """Whether the search reads ``get_entries`` aliases."""

    return mode(request) in QUERY_MODES or "aliases" in _requested(request, ENRICHMENT_FIELDS)


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


# --- The composed name (Core 2026.11.3 ``async_get_full_entity_name``).

def _entity_part(row: Mapping) -> str | None:
    """``entity_registry.py`` ``_entity_next_name_part`` (252-260)."""

    if row["area_id"] is not None:
        return "area"
    return "device" if row["device_id"] is not None else None


def _device_part(row: Mapping) -> str | None:
    """``device_registry.py`` ``next_name_part``: a main device's (735-738),
    whose row sends a ``parent_device_id`` of None (785), or a child's
    (884-889)."""

    if row["area_id"] is not None:
        return "area"
    return None if row["parent_device_id"] is None else "parent_device"


def _parts_agree(rows: Mapping, derive: Callable[[Mapping], str | None]) -> bool:
    """A wire ``next_name_part``, when sent, equals the derived one."""

    return all("next_name_part" not in row or row["next_name_part"] == derive(row)
               for row in rows.values())


def _naming_devices(sources: Mapping, fields: tuple, required: bool) -> Mapping | None:
    """The device rows the composed name reads, or None when they are not
    usable and ``required`` is false."""

    result = source_result(sources, "device_registry")
    if _usable(result, fields) and _parts_agree(result.value, _device_part):
        return result.value
    if required:
        raise SearchError("source_unavailable", source="device_registry")
    return None


def composed_name(entry: Mapping | None, devices: Mapping) -> str:
    """``_async_get_full_entity_name`` (527-630) with the parts parent device,
    device and entity and ``use_next_name_part``; "" when every part is empty.

    The device part applies while the entity's next part is the device; the
    parent part while that device is a child whose next part is its parent,
    and the parent is a main device. The wire ``original_name`` is already
    the unprefixed name (``as_partial_dict`` 421).
    """

    if entry is None:
        return ""
    entity_name = entry["name"] if entry["name"] is not None else entry["original_name"]
    device_name = parent_name = None
    if _entity_part(entry) == "device":
        device = devices.get(entry["device_id"])
        if device is not None:
            device_name = device["name_by_user"] or device["name"]
            if _device_part(device) == "parent_device":
                parent = devices.get(device["parent_device_id"])
                if parent is not None and parent["parent_device_id"] is None:
                    parent_name = parent["name_by_user"] or parent["name"]
    return " ".join(part for part in (parent_name, device_name, entity_name) if part)


# --- The area ladder (``search_locations.py`` ``_resolve_area_query``, and
# ``smart_search/_entities.py``'s, which resolves the same areas).

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
        label_ids: set = set()
        # Labels are read only when requested and their sources were read
        # valid for them; another projection may carry drifted labels.
        if "labels" in self.requested and "labels" not in self.null:
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


class _Joins:
    """``websocket_api/search.py`` ``_registry_enrichment`` and ``_entity_record``
    (475-564): the texts one entity is scored on."""

    def __init__(self, registry: Mapping, devices: Mapping, areas: Mapping, floors: Mapping,
                 labels: Mapping) -> None:
        self.registry = registry
        self.devices = devices
        self.areas = areas
        self.floors = floors
        self.labels = labels
        self.device_areas = _device_areas(devices)

    def texts(self, entity_id: str, name: str, friendly_name: str, aliases: tuple) -> list:
        """The entity id, the name, the friendly name, the aliases, the label
        names, the device texts, then the area and floor names. The bare
        domain is not a text."""

        entry = self.registry.get(entity_id)
        area_id = None
        label_ids: set = set()
        device_texts: list = []
        if entry is not None:
            area_id = effective_entity_area_id(entry, self.device_areas)
            label_ids = set(entry["labels"])
            device_id = entry["device_id"]
            device = self.devices.get(device_id) if device_id else None
            if device is not None:
                label_ids |= set(device["labels"])
                # A child device's row has no manufacturer or model.
                device_texts = [device.get(key) for key in
                                ("name_by_user", "name", "manufacturer", "model")]
        area = self.areas.get(area_id) if area_id else None
        floor_id = area["floor_id"] if area is not None else None
        floor = self.floors.get(floor_id) if floor_id else None
        texts = [entity_id, name, friendly_name, *sorted(aliases),
                 *((self.labels.get(label_id) or {}).get("name") or label_id
                   for label_id in sorted(label_ids)),
                 *(text for text in device_texts if text)]
        if area is not None and area["name"]:
            texts.append(area["name"])
        if floor is not None and floor["name"]:
            texts.append(floor["name"])
        return texts


# --- The search.

def _record(entity_id: str, friendly_name: str, domain: str, state: str, score: int,
            match_type: str, name: str | None) -> dict:
    return {"entity_id": entity_id, "friendly_name": friendly_name, "domain": domain,
            "state": state, "score": score, "match_type": match_type,
            COMPOSED_NAME_FIELD: name}


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
    if not _parts_agree(registry, _entity_part):
        raise SearchError("source_unavailable", source="entity_registry")
    query_mode = current in QUERY_MODES
    devices = _naming_devices(sources, _DEVICE_TEXT_FIELDS if query_mode else _DEVICE_NAME_FIELDS,
                              query_mode)
    hidden = frozenset(entity_id for entity_id, row in registry.items()
                       if row["hidden_by"] is not None)
    denied = frozenset() if request.include_hidden else hidden
    domain = request.domain_filter
    state_filter = request.state_filter
    warnings: list[str] = []
    alias_map: dict | None = None

    def state_of(entity_id: str) -> _State:
        # An area entity with no usable state row is listed from the registry.
        return states.get(entity_id) or _State("unknown", {}, entity_id)

    def kept(entity_id: str, state: str) -> bool:
        # ``_search_entities`` 361-372: the domain, hidden and state filters
        # run before scoring and paging.
        return (entity_id not in denied
                and (domain is None or scoring.entity_domain(entity_id) == domain)
                and (state_filter is None or (state or "").lower() == state_filter))

    def name_of(entity_id: str, friendly_name: str) -> str | None:
        if devices is None:
            return None
        return composed_name(registry.get(entity_id), devices) or friendly_name

    if current in AREA_MODES:
        areas = required_rows(sources, "area_registry", _AREA_LADDER_FIELDS)
        area_devices = required_rows(sources, "device_registry", _DEVICE_AREA_FIELDS)
        matched, ambiguous = resolve_area(
            request.area_filter, areas,
            lambda: required_rows(sources, "floor_registry", _FLOOR_LADDER_FIELDS))
        if ambiguous:
            warnings.append(WARNING_AMBIGUOUS_FLOOR)
        device_areas = _device_areas(area_devices)
        candidates = [entity_id for entity_id, row in registry.items()
                      if entity_id not in malformed_ids and kept(entity_id, state_of(entity_id).state)
                      and effective_entity_area_id(row, device_areas) in matched]
    else:
        candidates = [entity_id for entity_id, row in states.items() if kept(entity_id, row.state)]

    if query_mode:
        query_lower = request.query.strip().lower()
        joins = _Joins(registry, devices,
                       required_rows(sources, "area_registry", ("name", "floor_id")),
                       required_rows(sources, "floor_registry", ("name",)),
                       required_rows(sources, "label_registry", ("name",)))
        alias_map = await _read_aliases(candidates, aliases, reasons)
        records = []
        for entity_id in candidates:
            row = state_of(entity_id)
            name = name_of(entity_id, row.friendly_name)
            known = alias_map.get(entity_id, ())
            tier = scoring.text_tier(query_lower,
                                     joins.texts(entity_id, name, row.friendly_name, known),
                                     fuzzy=not request.exact_match)
            if tier is None:
                continue
            entity_domain = scoring.entity_domain(entity_id)
            match_type = scoring.entity_match_type(
                query_lower, entity_id, (name, row.friendly_name), entity_domain, known,
                exact=request.exact_match)
            records.append(_record(entity_id, row.friendly_name, entity_domain, row.state,
                                   scoring.apply_hidden_penalty(tier, entity_id in hidden),
                                   match_type, name))
    else:
        # ``_component_listing_metadata`` (component.py 536-553).
        label = "area_match" if current == AREA_ONLY else current
        records = []
        for entity_id in candidates:
            row = state_of(entity_id)
            records.append(_record(entity_id, row.friendly_name, scoring.entity_domain(entity_id),
                                   row.state, scoring.apply_hidden_penalty(100, entity_id in hidden),
                                   label, name_of(entity_id, row.friendly_name)))

    ordered = _order(records)
    offset, limit = request.offset, request.limit
    window = ordered[offset:offset + limit]
    page_end = offset + len(window)
    page = [(offset + index, record) for index, record in enumerate(window)]

    enricher = _Enricher(request, sources, registry)
    if "aliases" in enricher.requested and alias_map is None:
        alias_map = await _read_aliases([record["entity_id"] for _, record in page],
                                        aliases, reasons)
    if devices is None and (request.result_fields is None
                            or COMPOSED_NAME_FIELD in request.result_fields):
        # A listing serves a null ``name`` when the device rows are unusable.
        enricher.unusable.add("device_registry")
    enricher.disclose(len(page), reasons)
    membership = _requested(request, MEMBERSHIP_FIELDS)
    keep = None
    if request.result_fields is not None:
        keep = set(request.result_fields)
        if "member_entity_ids" in keep:
            keep.add("is_group")
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
                      len(ordered), page_end)
    return EntityOutcome(surface, current, tuple(warnings))
