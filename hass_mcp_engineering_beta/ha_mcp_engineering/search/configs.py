"""The configuration surface of native ``ha_search`` (HAMCP-481 W3, design
sections 2.4 to 2.6, 3.5, 3.6 and 6.1; HAMCP-501 W6 S1).

Storage helpers are searched natively, over the inventories the engine
already read, and so are dashboards (HAMCP-447, :mod:`.dashboards`).
Automations, scripts, scenes and flow helpers come from the delegated
sub-calls (:mod:`.subcalls`). Under S1 = b+ (approved by
Josh as relayed by the architect, 2026-11-26), automation, script and scene
body matching is the delegated route's own, and native search reads no
automation, script or scene body: the one injected reader (a
``CoreReadSession``) serves the reference graph only. Nothing here falls back
to the delegated route for a native bucket.

Sources. The entity registry is required for scene and helper search: an
unusable registry fails the search. A helper list that cannot be read, its
dropped rows and its rows with an invalid field are counted as
``read_failed``. The reference graph is read once, for an ``entity_id``-shaped
query with automations, scripts or scenes requested; a failed or unreadable
graph is counted as ``not_searched`` ``graph_unavailable``. A sub-call that is
not admitted or did not finish is ``source_unavailable`` for its own bucket,
and an upstream ``partial`` page is ``not_searched`` ``upstream_partial`` for
its own bucket; the other buckets are unaffected. The dashboard reads are
counted by :mod:`.dashboards`.

Body matching (HAMCP-501 W6 F1). A delegated row keeps the route's body match
only when the page shows that it was matched against the scrubbed corpus.
For the automation, script, scene and helper sub-calls, a query the A'+
trigger names (:func:`.subcalls.body_match_unsafe`, decided by Josh as
relayed by the architect, 2026-11-26), or a scan whose pages or bracketing
helper probes are not ``clean``, keeps only the component name tier of each
row and the graph references: ``match_in_config`` is False, a row with no
name match is dropped, and the bucket is ``not_searched`` with
``body_numeric_query``, ``body_scrub_degraded`` or ``body_scrub_unverified``.
Native storage helpers and dashboards are unaffected.

Ported from ha-mcp 9.2.0, commit 51e3aac788482a081807591cd5d58d07dc4e540b:

- ``src/ha_mcp/tools/smart_search/_deep.py`` (sha256
  10c57f9fd6186deb31d88329f3d61bdd63bf6cb525fafbf455eda0bfd44482ff): the
  storage-helper candidates, scoring and records, the helper registry map,
  and the category-ordered merge and slice;
- ``src/ha_mcp/tools/smart_search/_scenes.py`` (sha256
  3b587de45f4954b779e064bbbb19087d0266d2a069db9c6ac3c055c2844154fa): the
  scene registry walk and the storage-key resolution;
- ``src/ha_mcp/tools/smart_search/_graph.py`` (sha256
  39580945a782d6b7007d523d05450568d10daa115496a321232e4713a936259b): when the
  graph applies, the graph record and the graph-hit merge.

Changes from the source:

- automation, script and scene rows come from the delegated sub-calls (S1);
  a script or scene id the route leaves empty is filled from the entity id,
  a scene's through the registry's storage key;
- ``config_time_budget`` is validated but not forwarded: each sub-call scan
  has its own deadline (:data:`.subcalls.SCAN_DEADLINE_SECONDS`);
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
- flow-helper rows come from the delegated sub-calls, and dashboards from
  :mod:`.dashboards`.

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
from . import dashboards
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
# ``fetch_related_buckets``'s timeout.
GRAPH_TIMEOUT_SECONDS = 5.0
# ``config_time_budget``'s public schema range. The value is validated, not
# forwarded (S1).
TIME_BUDGET_RANGE = (0.001, 300.0)

# ``_graph.py`` ``_VALID_ENTITY_ID``, unanchored for ``fullmatch``; no
# ``re.ASCII``, as in the source.
_GRAPH_SHAPED = re.compile(r"(?!.+__)(?!_)[\da-z_]+(?<!_)\.(?!_)[\da-z_]+(?<!_)")
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
    if graph_applies(request):
        names.add("search_related")
    if "helper" in types:
        names.update(_HELPER_LISTS)
    if "dashboard" in types:
        names.update(("dashboards", "dashboard_config"))
    order = ("states", "entity_registry", "search_related", *_HELPER_LISTS,
             "dashboards", "dashboard_config")
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


def _scene_registry(sources: Mapping) -> dict:
    """``_scenes.py``'s registry walk: slug to storage key."""

    registry = required_rows(sources, "entity_registry", ("platform", "unique_id"))
    slug_map: dict[str, str] = {}
    for entity_id, row in registry.items():
        unique_id = row["unique_id"]
        if not entity_id.startswith("scene.") or not unique_id:
            continue
        slug_map[entity_id.removeprefix("scene.")] = unique_id
    return slug_map


def _entity_record(search_type: str, row: dict, slug_map: Mapping) -> dict:
    """A copy of an automation, script or scene sub-call row, with the id the
    route left empty filled: a script's from its entity id, a scene's by
    ``_resolve_scene_storage_id``'s registry fallback."""

    record = dict(row)
    slug = record["entity_id"].removeprefix(f"{search_type}.")
    if search_type == "script" and record.get("script_id") is None:
        record["script_id"] = slug
    elif search_type == "scene" and record.get("scene_id") is None:
        record["scene_id"] = slug_map.get(slug) or slug
    # The component route does not report references; the native graph
    # merge may set it.
    record.setdefault("match_in_references", False)
    return record


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


# ``search_score.py``'s ``FUZZY_THRESHOLD`` (``constants.py``).
ROUTE_FUZZY_THRESHOLD = 70
# The names Row 25 withholds from a reviewed flow-helper row (HAMCP-447
# admits its ``entry_id``); a collection helper, automation, script or scene
# row withholds none.
_FLOW_HELPER_WITHHELD = frozenset({"options_withheld"})


def _route_name_tier(query_lower: str, texts: tuple, *, exact: bool) -> int | None:
    """ha-mcp 9.2.0's component name tier: ``_name_tier`` in
    ``custom_components/ha_mcp_tools/websocket_api/search_score.py`` (sha256
    ba034030b149091fba1e2777ac6c0c4d9c442fa33148b7d92441cba3feca43de), over
    a row's entity id and name only."""

    query_norm = "" if exact else " ".join(scoring.tokenize(query_lower))
    best = 0
    for text in texts:
        if not text:
            continue
        text_lower = str(text).lower()
        if query_lower in text_lower:
            return 100
        if not exact:
            if query_norm and query_norm in " ".join(scoring.tokenize(text_lower)):
                return 100
            best = max(best, scoring.ratio(query_lower, text_lower))
    if not exact and best >= ROUTE_FUZZY_THRESHOLD:
        return best
    return None


def _name_only(row: dict, request: ConfigRequest) -> dict | None:
    """A sub-call row with the route's body match removed: the name tier's
    score, or None when the row's entity id and name do not match. The graph
    merge may add a dropped row back as a reference."""

    score = _route_name_tier(request.query_lower,
                             (row.get("entity_id"), row.get("friendly_name") or row.get("name")),
                             exact=request.exact_match)
    if score is None:
        return None
    return {**row, "score": score, "match_in_name": True, "match_in_config": False}


def _kept_withheld(search_type: str, rows: tuple) -> frozenset:
    """The withheld names a bucket discloses under a body code: its kept rows'
    only, so a dropped row leaves no trace (``options_withheld`` is counted
    for any kept flow-helper row)."""

    if search_type == "helper" and any("entity_id" not in row for row in rows):
        return _FLOW_HELPER_WITHHELD
    return frozenset()


def _body_code(unsafe_query: bool, scrub: str) -> str | None:
    if unsafe_query:
        return "body_numeric_query"
    if scrub == subcalls.SCRUB_DEGRADED:
        return "body_scrub_degraded"
    if scrub != subcalls.SCRUB_CLEAN:
        return "body_scrub_unverified"
    return None


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
    slug_map = _scene_registry(sources) if "scene" in types else {}
    hits = await _graph(request, reader, reasons)
    results: dict[str, list] = {search_type: [] for search_type in CONFIG_TYPES}
    bucket_withheld: dict[str, frozenset] = {}
    # HAMCP-501 W6 F1: under the A'+ trigger no probe is read; otherwise a
    # helper probe before and after the scans brackets the automation, script
    # and scene pages, which carry no degraded flag of their own.
    unsafe_query = subcalls.body_match_unsafe(request.query, request.exact_match)
    probed = not unsafe_query and any(t in types for t in subcalls.ENTITY_TYPES)
    probes = []
    if probed:
        probes.append(await subcalls.probe_scrub(subcall_readers.get("helper"),
                                                 query=request.query,
                                                 exact_match=request.exact_match))
    outcomes: dict[str, subcalls.SubcallOutcome] = {}
    # One scan per requested sub-call type, in category order, one after
    # another; each scan has its own deadline. Dashboards are native.
    for search_type in CONFIG_TYPES:
        if search_type not in types or search_type == "dashboard":
            continue
        if search_type == "helper":
            results["helper"] = _storage_helpers(request, sources, reasons)
        outcome = await subcalls.scan(search_type, subcall_readers.get(search_type),
                                      query=request.query, exact_match=request.exact_match,
                                      monotonic=monotonic)
        _disclose(outcome, reasons)
        outcomes[search_type] = outcome
    if probed:
        probes.append(await subcalls.probe_scrub(subcall_readers.get("helper"),
                                                 query=request.query,
                                                 exact_match=request.exact_match))
    code = _body_code(unsafe_query, subcalls.worst_scrub(
        *probes, *(outcome.scrub for search_type, outcome in outcomes.items()
                   if search_type in subcalls.BODY_TYPES)))
    if "dashboard" in types:
        # HAMCP-447: over the list the engine read (``source_result`` checked
        # it above) and the reader's configuration reads.
        results["dashboard"] = await dashboards.search_dashboards(
            request.query_lower, request.exact_match, sources["dashboards"], reader,
            reasons, monotonic=monotonic)
    for search_type, outcome in outcomes.items():
        rows, withheld = outcome.rows, outcome.withheld_names
        if (code is not None and search_type in subcalls.BODY_TYPES
                and outcome.status != subcalls.NOT_ADMITTED):
            reasons.add("not_searched", search_type, code, None)
            rows = tuple(kept for kept in (_name_only(row, request) for row in rows)
                         if kept is not None)
            withheld = _kept_withheld(search_type, rows)
        if withheld:
            bucket_withheld[BUCKET_OF[search_type]] = withheld
        if search_type in subcalls.ENTITY_TYPES:
            # The route scored the body; ``include_config`` is never
            # forwarded, so the reply discloses each body as withheld.
            results[search_type].extend((_entity_record(search_type, row, slug_map), True)
                                        for row in rows)
        else:
            results[search_type].extend((row, False) for row in rows)
    if hits:
        _merge_graph(results, hits, request, states, slug_map)
    # ``_paginate_and_build_response``: category order, then a stable sort by
    # score, descending.
    tagged = [(BUCKET_OF[search_type], record, body_read)
              for search_type in CONFIG_TYPES for record, body_read in results[search_type]]
    tagged.sort(key=lambda item: -item[1]["score"])
    total = len(tagged)
    window = tagged[request.offset:request.offset + request.limit]
    surface = Surface("config", tuple(window),
                      tuple(request.offset + index for index in range(len(window))),
                      total, request.offset + len(window))
    return ConfigOutcome(surface, MappingProxyType(bucket_withheld), "dashboard" in types)
