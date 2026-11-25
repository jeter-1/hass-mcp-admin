"""The configuration surface of native ``ha_search`` (HAMCP-481 W3, design
sections 2.4 to 2.6, 3.5, 3.6 and 6.1).

Automations, scripts, scenes and storage helpers are searched natively, over
the inventories the engine already read and one injected reader (a
``CoreReadSession``) for configuration bodies and the reference graph.
Dashboards and flow helpers come from the delegated sub-calls
(:mod:`.subcalls`). Nothing here falls back to the delegated route for a
native bucket.

Body reads (G-b). Each type's items are read in chunks of
:data:`CONFIG_CHUNK`. A chunk waits at most the smaller of the type's
remaining time budget and :data:`ITEM_TIMEOUT_SECONDS`. Once the budget is
spent, the rest are counted as ``not_searched`` ``time_budget``. A chunk that
times out on the budget is counted the same way; one that times out on the
per-item timeout, or fails, is counted as ``read_failed``. An item that Core
does not find, or whose key is not a valid read argument, is counted as
``not_readable``. Bodies are read in memory for matching only; a record
carries no body.

Sources. The entity registry is required for scene and helper search: an
unusable registry fails the search. A helper list that cannot be read, its
dropped rows and its rows with an invalid field are counted as
``read_failed``. The reference graph is read once, for an ``entity_id``-shaped
query with automations, scripts or scenes requested; a failed or unreadable
graph is counted as ``not_searched`` ``graph_unavailable``.

Ported from ha-mcp 9.2.0, commit 51e3aac788482a081807591cd5d58d07dc4e540b:

- ``src/ha_mcp/tools/smart_search/_deep.py`` (sha256
  10c57f9fd6186deb31d88329f3d61bdd63bf6cb525fafbf455eda0bfd44482ff): the
  automation, script and storage-helper candidates, scoring and records, the
  helper registry map, and the category-ordered merge and slice;
- ``src/ha_mcp/tools/smart_search/_scenes.py`` (sha256
  3b587de45f4954b779e064bbbb19087d0266d2a069db9c6ac3c055c2844154fa): the
  scene registry walk, the fetch selection and the storage-key resolution;
- ``src/ha_mcp/tools/smart_search/_graph.py`` (sha256
  39580945a782d6b7007d523d05450568d10daa115496a321232e4713a936259b): when the
  graph applies, the graph record and the graph-hit merge;
- ``src/ha_mcp/tools/smart_search/_fetch.py`` (sha256
  8c0452072e2d35b2e2713a3e840e7535a5cc1fab9ae3be349f33f3fed13fd2c3): the
  chunked, budgeted fetch and its graph-confirmed-last order;
- ``src/ha_mcp/config_settings.py`` (sha256
  cf975eee29dc9747ea0ed3ad7e6f97e6d78e674c6100d26c3cfe35e0e5b38b3c): the
  default time budgets, the per-item timeout and the chunk size.

Changes from the source:

- automation and script bodies are read by entity id over the websocket, so
  an item without a unique id is read too;
- a chunk waits the smaller of the remaining budget and the per-item
  timeout, and its failures are named reasons (G-b);
- an unusable entity registry fails scene and helper search instead of
  degrading;
- the helper registry map is keyed by platform and unique id;
- a helper row without a valid ``name`` is ``read_failed`` instead of using
  its id;
- a graph answer with any unreadable bucket, or an id outside its bucket's
  domain, is unavailable; a query the strict entity-id pattern refuses is not
  sent and the graph is unavailable;
- graph references on surfaces the call excluded, and the group and person
  consumers, are not reported;
- dashboard and flow-helper rows come from the delegated sub-calls.

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

import asyncio
from dataclasses import dataclass
import math
import re
import time
from types import MappingProxyType
from typing import Any, Callable, Mapping

from ..entity_ids import STRICT_ENTITY_ID
from . import core_contracts as c
from . import scoring
from . import subcalls
from .core_reads import INVALID, ReadResult
from .entities import StateRows, required_rows, source_result
from .reply import Reasons, SearchError, Surface

CONFIG_TYPES = ("automation", "script", "scene", "helper", "dashboard")
# ``_deep.py`` ``deep_search``: the types searched when none are given.
DEFAULT_SEARCH_TYPES = CONFIG_TYPES[:4]
BUCKET_OF = MappingProxyType({
    "automation": "automations", "script": "scripts", "scene": "scenes",
    "helper": "helpers", "dashboard": "dashboards",
})
# The types the reference graph speaks to (``_graph.py``).
GRAPH_TYPES = ("automation", "script", "scene")
# ``INDIVIDUAL_FETCH_BATCH_SIZE`` and ``INDIVIDUAL_CONFIG_TIMEOUT``.
CONFIG_CHUNK = 10
ITEM_TIMEOUT_SECONDS = 5.0
# The ``*_CONFIG_TIME_BUDGET`` defaults, in seconds.
DEFAULT_TIME_BUDGETS = MappingProxyType({"automation": 30.0, "script": 20.0, "scene": 20.0})
# ``fetch_related_buckets``'s timeout.
GRAPH_TIMEOUT_SECONDS = 5.0
# ``config_time_budget``'s public schema range.
TIME_BUDGET_RANGE = (0.001, 300.0)

# ``_graph.py`` ``_VALID_ENTITY_ID``, unanchored for ``fullmatch``; no
# ``re.ASCII``, as in the source.
_GRAPH_SHAPED = re.compile(r"(?!.+__)(?!_)[\da-z_]+(?<!_)\.(?!_)[\da-z_]+(?<!_)")
_BODY_READS = MappingProxyType({"automation": "automation_config", "script": "script_config"})
_HELPER_LISTS = tuple(f"{domain}_list" for domain in c.INPUT_DOMAINS)


@dataclass(frozen=True, slots=True)
class ConfigRequest:
    """The configuration-search arguments, as the engine normalized them.

    ``query`` is stripped and non-empty; ``search_types`` is the effective
    tuple, unique and in the caller's order.
    """

    query: str
    exact_match: bool
    search_types: tuple
    limit: int
    offset: int
    config_time_budget: float | None = None

    def __post_init__(self) -> None:
        budget = self.config_time_budget
        if (type(self.query) is not str or not self.query or self.query != self.query.strip()
                or type(self.exact_match) is not bool
                or type(self.search_types) is not tuple or not self.search_types
                or len(set(self.search_types)) != len(self.search_types)
                or not set(self.search_types) <= set(CONFIG_TYPES)
                or type(self.limit) is not int or self.limit < 1
                or type(self.offset) is not int or self.offset < 0
                or not (budget is None or (type(budget) in (int, float) and math.isfinite(budget)
                                           and TIME_BUDGET_RANGE[0] <= budget <= TIME_BUDGET_RANGE[1]))):
            raise ValueError("invalid config search")

    @property
    def query_lower(self) -> str:
        # ``deep_search``'s ``query.lower().strip()``.
        return self.query.lower().strip()


@dataclass(frozen=True, slots=True)
class ConfigOutcome:
    surface: Surface
    bucket_withheld: Mapping
    dashboards: bool


def graph_applies(request: ConfigRequest) -> bool:
    """``_graph.py`` ``_graph_applies``."""

    return (_GRAPH_SHAPED.fullmatch(request.query_lower) is not None
            and any(search_type in request.search_types for search_type in GRAPH_TYPES))


def reads_for(request: ConfigRequest) -> tuple:
    """The reads one configuration search needs, in a fixed order. The
    ``rows`` reads are inventories the engine reads first; the others go
    through the reader."""

    types = set(request.search_types)
    names = {"states"}
    if types & {"scene", "helper"}:
        names.add("entity_registry")
    for search_type, name in _BODY_READS.items():
        if search_type in types:
            names.add(name)
    if graph_applies(request):
        names.add("search_related")
    if "scene" in types:
        names.add("scene_config")
    if "helper" in types:
        names.update(_HELPER_LISTS)
    order = ("states", "entity_registry", "automation_config", "script_config",
             "search_related", "scene_config", *_HELPER_LISTS)
    return tuple(name for name in order if name in names)


def _score(entity_id: str, name: str, domain: str, body: Any, request: ConfigRequest,
           name_score: int | None = None) -> tuple | None:
    """``_score_config_entries``: ``(score, match_in_name, match_in_config)``,
    or None when the item does not match."""

    query = request.query_lower
    if name_score is None:
        name_score = scoring.entity_name_score(entity_id, name, domain, query)
    config_score = scoring.config_match_score(body, query, request.exact_match) if body else 0
    total, threshold, in_name = scoring.deep_match_score(
        entity_id, name, name_score, config_score, query, request.exact_match)
    if total < threshold:
        return None
    return total, in_name, config_score >= threshold


async def _read_chunk(search_type: str, reads: list, reader: Any) -> list:
    name = _BODY_READS.get(search_type)
    if name is not None:
        result = await reader.batch(name, tuple(key for _, key in reads))
        if type(result) is not ReadResult or result.status == c.UNAVAILABLE:
            return [None] * len(reads)
        return [result.items.get(key) for _, key in reads]
    outcomes = await asyncio.gather(*(reader.scene_config(key) for _, key in reads),
                                    return_exceptions=True)
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            # An authority change or a refused argument fails the search.
            raise outcome
    return list(outcomes)


async def _read_bodies(search_type: str, items: list, reader: Any, request: ConfigRequest,
                       deprioritize: frozenset, monotonic: Callable[[], float],
                       reasons: Reasons) -> dict:
    """Read the bodies of ``items``, ``(result key, read key)`` pairs, under
    the type's time budget (G-b). Returns the bodies by result key."""

    argument = c.READS[_BODY_READS.get(search_type, "scene_config")].argument
    # ``_individual_fetch_budgeted``: graph-confirmed items last, order kept.
    ordered = ([item for item in items if item[0] not in deprioritize]
               + [item for item in items if item[0] in deprioritize])
    counts = {"time_budget": 0, "read_failed": 0, "not_readable": 0}
    readable = []
    for item in ordered:
        if argument.fullmatch(item[1]) is None:
            counts["not_readable"] += 1
        else:
            readable.append(item)
    budget = request.config_time_budget
    if budget is None:
        budget = DEFAULT_TIME_BUDGETS[search_type]
    bodies: dict[str, dict] = {}
    started = monotonic()
    for start in range(0, len(readable), CONFIG_CHUNK):
        chunk = readable[start:start + CONFIG_CHUNK]
        remaining = budget - (monotonic() - started)
        if remaining <= 0:
            counts["time_budget"] += len(readable) - start
            break
        timeout = min(remaining, ITEM_TIMEOUT_SECONDS)
        try:
            outcomes = await asyncio.wait_for(_read_chunk(search_type, chunk, reader), timeout)
        except (asyncio.TimeoutError, TimeoutError):
            counts["time_budget" if remaining < ITEM_TIMEOUT_SECONDS else "read_failed"] += len(chunk)
            continue
        for (key, _), outcome in zip(chunk, outcomes):
            if type(outcome) is not ReadResult or outcome.status == c.UNAVAILABLE:
                counts["read_failed"] += 1
            elif outcome.status == c.NOT_FOUND:
                counts["not_readable"] += 1
            elif type(outcome.value) is dict:
                bodies[key] = outcome.value
            else:
                counts["read_failed"] += 1
    for code, count in counts.items():
        reasons.add("not_searched", search_type, code, count)
    return bodies


async def _graph(request: ConfigRequest, reader: Any, reasons: Reasons) -> Mapping | None:
    """The reference-graph hits by graph type, or None when the graph does
    not apply or could not be read."""

    if not graph_applies(request):
        return None
    query = request.query_lower
    item = None
    if STRICT_ENTITY_ID.fullmatch(query) is not None:
        try:
            result = await asyncio.wait_for(reader.batch("search_related", (query,)),
                                            GRAPH_TIMEOUT_SECONDS)
        except (asyncio.TimeoutError, TimeoutError):
            result = None
        if type(result) is ReadResult and result.status != c.UNAVAILABLE:
            item = result.items.get(query)
    hits: dict[str, frozenset] = {}
    if (type(item) is ReadResult and item.status in (c.COMPLETE, c.PARTIAL)
            and isinstance(item.value, Mapping)
            and all(ids is not INVALID for ids in item.value.values())):
        for search_type in GRAPH_TYPES:
            ids = item.value.get(search_type, ())
            if not all(STRICT_ENTITY_ID.fullmatch(entity_id) and entity_id.startswith(f"{search_type}.")
                       for entity_id in ids):
                break
            hits[search_type] = frozenset(ids)
        else:
            return MappingProxyType(hits)
    reasons.add("not_searched", "config", "graph_unavailable", None)
    return None


async def _automations_or_scripts(search_type: str, request: ConfigRequest, states: StateRows,
                                  reader: Any, hits: frozenset, monotonic: Callable[[], float],
                                  reasons: Reasons) -> list:
    prefix = f"{search_type}."
    candidates = [(entity_id, row.friendly_name) for entity_id, row in states.rows.items()
                  if entity_id.startswith(prefix)]
    bodies = await _read_bodies(search_type, [(entity_id, entity_id) for entity_id, _ in candidates],
                                reader, request, hits, monotonic, reasons)
    records = []
    for entity_id, friendly_name in candidates:
        body = bodies.get(entity_id)
        scored = _score(entity_id, friendly_name, search_type, body, request)
        if scored is None:
            continue
        record = {"entity_id": entity_id}
        if search_type == "script":
            record["script_id"] = entity_id.removeprefix(prefix)
        record.update({"friendly_name": friendly_name, "score": scored[0],
                       "match_in_name": scored[1], "match_in_config": scored[2],
                       "match_in_references": False})
        records.append((record, bool(body)))
    return records


def _scene_registry(sources: Mapping) -> tuple[dict, set]:
    """``_scenes.py``'s registry walk: slug to storage key, and the storage
    keys of Home Assistant's own scenes."""

    registry = required_rows(sources, "entity_registry", ("platform", "unique_id"))
    slug_map: dict[str, str] = {}
    owned: set[str] = set()
    for entity_id, row in registry.items():
        unique_id = row["unique_id"]
        if not entity_id.startswith("scene.") or not unique_id:
            continue
        slug_map[entity_id.removeprefix("scene.")] = unique_id
        if row["platform"] == "homeassistant":
            owned.add(unique_id)
    return slug_map, owned


async def _scenes(request: ConfigRequest, states: StateRows, slug_map: dict, owned: set,
                  reader: Any, hits: frozenset, monotonic: Callable[[], float],
                  reasons: Reasons) -> list:
    candidates = [(entity_id, row.friendly_name, entity_id.removeprefix("scene."))
                  for entity_id, row in states.rows.items() if entity_id.startswith("scene.")]
    # ``_select_scene_ids_to_fetch``: integration-managed scenes are scored
    # by name only, with no reason (their read is a 404 by design).
    fetch = [(slug, slug_map.get(slug, slug)) for _, _, slug in candidates
             if slug_map.get(slug, slug) in owned]
    bodies = await _read_bodies("scene", fetch, reader, request,
                                frozenset(entity_id.removeprefix("scene.") for entity_id in hits),
                                monotonic, reasons)
    records = []
    for entity_id, friendly_name, slug in candidates:
        body = bodies.get(slug)
        scored = _score(entity_id, friendly_name, "scene", body, request)
        if scored is None:
            continue
        # ``_resolve_scene_storage_id``.
        config_id = body.get("id") if body else None
        scene_id = config_id if type(config_id) is str else slug_map.get(slug, slug)
        records.append(({"entity_id": entity_id, "scene_id": scene_id,
                         "friendly_name": friendly_name, "score": scored[0],
                         "match_in_name": scored[1], "match_in_config": scored[2],
                         "match_in_references": False}, bool(body)))
    return records


def _storage_helpers(request: ConfigRequest, sources: Mapping, reasons: Reasons) -> list:
    """``_deep_search_helpers``'s storage branch, over the read helper lists."""

    registry = required_rows(sources, "entity_registry", ("platform", "unique_id"))
    by_unique_id: dict[tuple, tuple] = {}
    for entity_id, row in registry.items():
        platform, unique_id = row["platform"], row["unique_id"]
        if platform not in c.INPUT_DOMAINS or not unique_id:
            continue
        name, original_name = row.get("name"), row.get("original_name")
        if name is INVALID or original_name is INVALID:
            raise SearchError("source_unavailable", source="entity_registry")
        by_unique_id[(platform, unique_id)] = (entity_id, name or original_name)
    query = request.query_lower
    records = []
    for domain in c.INPUT_DOMAINS:
        result = source_result(sources, f"{domain}_list")
        if result.status not in (c.COMPLETE, c.PARTIAL) or not isinstance(result.value, Mapping):
            reasons.add("not_searched", "helper", "read_failed", None)
            continue
        reasons.add("not_searched", "helper", "read_failed", result.dropped)
        for helper_id, helper in result.value.items():
            if any(value is INVALID for value in helper.values()):
                reasons.add("not_searched", "helper", "read_failed", 1)
                continue
            storage_name = helper["name"]
            registry_id, registry_name = by_unique_id.get((domain, helper_id), (None, None))
            entity_id = registry_id or f"{domain}.{helper_id}"
            display_name = registry_name or storage_name
            name_score = max(scoring.entity_name_score(entity_id, candidate, domain, query)
                             for candidate in {display_name, storage_name})
            # The source scores every storage body, even an empty one.
            scored = _score(entity_id, display_name, domain, helper, request, name_score)
            if scored is None:
                continue
            records.append(({"entity_id": entity_id, "helper_type": domain, "name": display_name,
                             "score": scored[0], "match_in_name": scored[1],
                             "match_in_config": scored[2], "match_in_references": False}, True))
    return records


def _merge_graph(results: dict, hits: Mapping, request: ConfigRequest, states: StateRows,
                 slug_map: dict) -> None:
    """``_graph.py`` ``_merge_graph_hits``, in place."""

    for search_type in GRAPH_TYPES:
        if search_type not in request.search_types or not hits.get(search_type):
            continue
        records = results[search_type]
        by_entity_id = {record["entity_id"]: record for record, _ in records}
        for entity_id in sorted(hits[search_type]):
            existing = by_entity_id.get(entity_id)
            if existing is not None:
                existing["match_in_references"] = True
                continue
            state = states.rows.get(entity_id)
            record = {"entity_id": entity_id,
                      "friendly_name": (state.friendly_name if state else None) or entity_id,
                      "score": scoring.GRAPH_HIT_SCORE, "match_in_name": False,
                      "match_in_config": False, "match_in_references": True}
            if search_type == "script":
                record["script_id"] = entity_id.removeprefix("script.")
            elif search_type == "scene":
                slug = entity_id.removeprefix("scene.")
                record["scene_id"] = slug_map.get(slug) or slug
            records.append((record, False))


def _disclose(outcome: subcalls.SubcallOutcome, reasons: Reasons) -> None:
    search_type = outcome.search_type
    if outcome.status == subcalls.NOT_ADMITTED:
        reasons.add("source_unavailable", search_type, "subcall_not_admitted", None)
    elif outcome.status == subcalls.INCOMPLETE:
        reasons.add("source_unavailable", search_type, f"{search_type}_subcall_incomplete", None)
    if outcome.upstream_partial:
        reasons.add("not_searched", search_type, "upstream_partial", None)


async def search_configs(request: ConfigRequest, *, states: StateRows, sources: Mapping,
                         reader: Any, subcall_readers: Mapping, reasons: Reasons,
                         monotonic: Callable[[], float] = time.monotonic) -> ConfigOutcome:
    """Search the requested configuration types and page them as one surface
    (design section 3.6)."""

    if (type(request) is not ConfigRequest or type(states) is not StateRows
            or type(reasons) is not Reasons or not isinstance(subcall_readers, Mapping)):
        raise ValueError("invalid config search")
    types = request.search_types
    for name in reads_for(request):
        if c.READS[name].shape == "rows":
            source_result(sources, name)
    slug_map: dict[str, str] = {}
    owned: set[str] = set()
    if "scene" in types:
        slug_map, owned = _scene_registry(sources)
    hits = await _graph(request, reader, reasons)
    results: dict[str, list] = {search_type: [] for search_type in CONFIG_TYPES}
    for search_type in ("automation", "script"):
        if search_type in types:
            results[search_type] = await _automations_or_scripts(
                search_type, request, states, reader, (hits or {}).get(search_type, frozenset()),
                monotonic, reasons)
    if "scene" in types:
        results["scene"] = await _scenes(request, states, slug_map, owned, reader,
                                         (hits or {}).get("scene", frozenset()), monotonic, reasons)
    bucket_withheld: dict[str, frozenset] = {}
    dashboard_held = dashboard_total = 0
    for search_type in ("helper", "dashboard"):
        if search_type not in types:
            continue
        if search_type == "helper":
            results["helper"] = _storage_helpers(request, sources, reasons)
        outcome = await subcalls.scan(search_type, subcall_readers.get(search_type),
                                      query=request.query, exact_match=request.exact_match,
                                      needed=request.offset + request.limit, monotonic=monotonic)
        _disclose(outcome, reasons)
        if outcome.withheld_names:
            bucket_withheld[BUCKET_OF[search_type]] = outcome.withheld_names
        results[search_type].extend((row, False) for row in outcome.rows)
        if search_type == "dashboard":
            dashboard_held = len(outcome.rows)
            dashboard_total = max(outcome.total, dashboard_held)
    if hits:
        _merge_graph(results, hits, request, states, slug_map)
    # ``_paginate_and_build_response``: category order, then a stable sort by
    # score, descending.
    tagged = [(BUCKET_OF[search_type], record, body_read)
              for search_type in CONFIG_TYPES for record, body_read in results[search_type]]
    tagged.sort(key=lambda item: -item[1]["score"])
    total = len(tagged) - dashboard_held + dashboard_total
    window = tagged[request.offset:request.offset + request.limit]
    surface = Surface("config", tuple(window),
                      tuple(request.offset + index for index in range(len(window))),
                      total, request.offset + len(window))
    return ConfigOutcome(surface, MappingProxyType(bucket_withheld), "dashboard" in types)
