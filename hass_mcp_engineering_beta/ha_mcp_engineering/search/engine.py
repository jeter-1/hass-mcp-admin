"""The native ``ha_search`` engine (HAMCP-481 W3, design sections 3.1, 4,
5.1 and 6.2).

One call validates and normalizes the 15 public arguments as ha-mcp 9.2.0
does, picks the searched surfaces (section 3.1), reads the inventories they
need through one read session, runs entity search and then configuration
search, and assembles the reply (:func:`reply.build_reply`).

The engine reaches Home Assistant only through the injected session factory,
which a later step wires to ``CoreReadSession``, and the delegated sub-calls
only through the injected page readers (:mod:`subcalls`). It imports no
client, flow or service module. Every refusal and every read failure is a
closed ``SearchError`` (a ``CoreReadError`` becomes ``source_unavailable``):
there is no fallback to the delegated route and no empty success.

``fields_withheld`` counts the names 9.2.0 would have returned that Row 25
withholds and the ``fields`` projection keeps: ``by_domain`` when
``group_by_domain`` is set and entity search ran; ``area_filter`` in both area
modes; ``area_names`` in the area-only mode, and ``message`` when that mode
matched nothing; and the record names the configuration surface withholds.
A withheld key's own children (the domain keys of ``by_domain``) and
``partial_reason`` are not counted.

Ported from ha-mcp 9.2.0, commit 51e3aac788482a081807591cd5d58d07dc4e540b:

- ``src/ha_mcp/tools/tools_search.py`` (sha256
  95a418bb72b97b78d0eb1ce365e4bfaa524698e532da91f901d9658784e5ebdc): the
  argument order, the normalization and the no-criteria refusal;
- ``src/ha_mcp/tools/coercion.py`` (sha256
  2cf514c9e06a2d47e0ef6fb9b3e767d698ba5008de4fcc4bc5feec06a280bd70):
  ``parse_string_list_param``;
- ``src/ha_mcp/tools/search/response.py`` (sha256
  fe776978341246e12a2ddcfd8a7ec3f66f8bbee5712d9697d4b8edc6d43af07c): the
  eligibility rule and the ``search_types`` check;
- ``src/ha_mcp/tools/search/entities.py`` (sha256
  650dcd0ea85c63541f6220696767e7db568d097214627496c09d2129762b7500): the
  ``result_fields`` check;
- ``src/ha_mcp/tools/search/legacy.py`` (sha256
  d202c015e78cc1493f737f3489f460028229aece88adc55efb952fb1f11d48a1): the
  entity filters' normalization.

Changes from the source:

- the schema's type checks run here, in argument-name order, before the
  source's own checks; a value must have the schema's JSON type exactly
  (no string-to-number or number-to-boolean coercion), and an unknown
  argument name is refused;
- a refusal names the parameter, never the value;
- a ``search_types`` value listed twice is searched once; ``search_types``
  echoes the caller's list;
- ``applied`` records what the call did (design section 5.1).

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
import json
import math
import time
from typing import Any, Callable, Mapping

from . import configs, entities
from . import core_contracts as c
from .reply import (
    WARNING_CONFIG_SKIPPED,
    WARNING_ENTITY_SKIPPED,
    Reasons,
    ReplySpec,
    SearchError,
    build_reply,
)

# ``PUBLIC_SCHEMAS["ha_search"]``: the 15 names, in the order they are checked.
PARAMETERS = tuple(sorted(SearchError.PARAMETERS))
DEFAULTS = {
    "area_filter": None, "config_time_budget": None, "domain_filter": None,
    "exact_match": True, "fields": None, "group_by_domain": False, "include_config": False,
    "include_hidden": True, "limit": 10, "offset": 0, "per_domain_limit": None,
    "query": None, "result_fields": None, "search_types": None, "state_filter": None,
}
_STRINGS = frozenset({"area_filter", "domain_filter", "query", "state_filter"})
_STRING_LISTS = frozenset({"fields", "result_fields", "search_types"})
_FLAGS = frozenset({"exact_match", "group_by_domain", "include_config", "include_hidden"})
_BUDGET_RANGE = configs.TIME_BUDGET_RANGE

APPLIED_KEYS = (
    "search_types", "domain_filter", "area_filter_set", "state_filter_set", "exact_match",
    "include_hidden", "limit", "offset", "entity_search_ran", "config_search_ran",
)

SessionFactory = Callable[[tuple], Any]


def _schema_valid(name: str, value: Any) -> bool:
    if name in _STRINGS:
        return value is None or type(value) is str
    if name in _STRING_LISTS:
        return (value is None or type(value) is str
                or (type(value) is list and all(type(item) is str for item in value)))
    if name in _FLAGS:
        return type(value) is bool
    if name == "limit":
        return type(value) is int and value >= 1
    if name == "offset":
        return type(value) is int and value >= 0
    if name == "per_domain_limit":
        return value is None or type(value) is int
    # config_time_budget
    return value is None or (type(value) in (int, float) and math.isfinite(value)
                             and _BUDGET_RANGE[0] <= value <= _BUDGET_RANGE[1])


def parse_string_list(value: Any, *, allow_csv: bool) -> list | None:
    """``coercion.py`` ``parse_string_list_param``. Raises ``ValueError``."""

    if value is None:
        return None
    if type(value) is list:
        if all(type(item) is str for item in value):
            return list(value)
        raise ValueError("not a list of strings")
    if type(value) is not str:
        raise ValueError("not a string or list")
    if not value.strip().startswith("[") and allow_csv:
        return [item.strip() for item in value.split(",") if item.strip()]
    try:
        parsed = json.loads(value)
    except (ValueError, RecursionError) as exc:
        raise ValueError("not a JSON array") from exc
    if type(parsed) is not list or not all(type(item) is str for item in parsed):
        raise ValueError("not a JSON array of strings")
    return parsed


@dataclass(frozen=True, slots=True)
class SearchCall:
    """One validated call: the arguments with defaults, the parsed lists, the
    normalized texts and the surfaces that run."""

    arguments: Mapping
    search_types: list | None
    fields: list | None
    result_fields: list | None
    query_text: str
    domain_filter: str | None
    area_filter: str | None
    state_filter: str | None
    entity_runs: bool
    config_runs: bool
    config_skipped: bool

    @property
    def explicit(self) -> bool:
        return self.search_types is not None

    @property
    def effective_types(self) -> tuple:
        if self.search_types is None:
            return configs.DEFAULT_SEARCH_TYPES
        return tuple(dict.fromkeys(self.search_types))


def parse_call(arguments: Any) -> SearchCall:
    """Validate and normalize the arguments, in 9.2.0's order."""

    if not isinstance(arguments, Mapping):
        raise SearchError("invalid_argument")
    if not all(type(name) is str and name in DEFAULTS for name in arguments):
        raise SearchError("invalid_argument")
    values = {**DEFAULTS, **arguments}
    for name in PARAMETERS:
        if not _schema_valid(name, values[name]):
            raise SearchError("invalid_argument", parameter=name)

    try:
        search_types = parse_string_list(values["search_types"], allow_csv=False)
    except ValueError:
        raise SearchError("invalid_argument", parameter="search_types") from None
    if search_types is not None:
        if not search_types:
            raise SearchError("empty_search_types", parameter="search_types")
        if not all(item in configs.CONFIG_TYPES for item in search_types):
            raise SearchError("invalid_argument", parameter="search_types")
    try:
        fields = parse_string_list(values["fields"], allow_csv=True)
    except ValueError:
        raise SearchError("invalid_argument", parameter="fields") from None
    try:
        result_fields = parse_string_list(values["result_fields"], allow_csv=True)
    except ValueError:
        raise SearchError("invalid_argument", parameter="result_fields") from None
    if result_fields is not None:
        if not result_fields:
            raise SearchError("empty_result_fields", parameter="result_fields")
        if not all(item in entities.RESULT_FIELDS for item in result_fields):
            raise SearchError("invalid_argument", parameter="result_fields")

    query_text = (values["query"] or "").strip()
    domain_text = (values["domain_filter"] or "").strip()
    area_text = (values["area_filter"] or "").strip()
    state_text = (values["state_filter"] or "").strip()
    explicit = search_types is not None
    # ``_compute_eligibility``.
    intent = bool(domain_text or area_text or state_text)
    entity_runs = bool(query_text or intent) and not explicit
    config_runs = bool(query_text) and (explicit or not intent)
    if not entity_runs and not config_runs:
        raise SearchError("no_criteria", parameter="query")
    return SearchCall(
        arguments=values,
        search_types=search_types,
        fields=fields,
        result_fields=result_fields,
        query_text=query_text,
        # ``legacy.py``: the domain and state filters are lowercased, and an
        # empty filter is no filter.
        domain_filter=domain_text.lower() or None,
        area_filter=area_text or None,
        state_filter=state_text.lower() or None,
        entity_runs=entity_runs,
        config_runs=config_runs,
        config_skipped=bool(query_text) and intent and not explicit,
    )


def entity_request(call: SearchCall) -> entities.EntityRequest | None:
    if not call.entity_runs:
        return None
    values = call.arguments
    return entities.EntityRequest(
        query=call.query_text or None,
        exact_match=values["exact_match"],
        area_filter=call.area_filter,
        domain_filter=call.domain_filter,
        state_filter=call.state_filter,
        include_hidden=values["include_hidden"],
        limit=values["limit"],
        offset=values["offset"],
        result_fields=tuple(call.result_fields) if call.result_fields is not None else None,
    )


def config_request(call: SearchCall) -> configs.ConfigRequest | None:
    if not call.config_runs:
        return None
    values = call.arguments
    return configs.ConfigRequest(
        query=call.query_text,
        exact_match=values["exact_match"],
        search_types=call.effective_types,
        limit=values["limit"],
        offset=values["offset"],
        config_time_budget=values["config_time_budget"],
    )


def reads_for(entity: entities.EntityRequest | None,
              config: configs.ConfigRequest | None) -> tuple:
    """Every read of the call, in ``READS`` order."""

    names: set[str] = set()
    if entity is not None:
        names.update(entities.reads_for(entity))
        if entities.reads_aliases(entity):
            names.add("entity_registry_entries")
    if config is not None:
        names.update(configs.reads_for(config))
    return tuple(name for name in c.READS if name in names)


def applied(call: SearchCall) -> dict:
    """Design section 5.1: what the call did."""

    values = call.arguments
    return {
        "search_types": list(call.effective_types),
        # Only entity search applies the domain filter; ``build_reply`` puts
        # the sanitized echo here.
        "domain_filter": call.domain_filter if call.entity_runs else None,
        "area_filter_set": call.area_filter is not None,
        "state_filter_set": call.state_filter is not None,
        "exact_match": values["exact_match"],
        "include_hidden": values["include_hidden"],
        "limit": values["limit"],
        "offset": values["offset"],
        "entity_search_ran": call.entity_runs,
        "config_search_ran": call.config_runs,
    }


def withheld_names(call: SearchCall, outcome: entities.EntityOutcome | None) -> frozenset:
    """The top-level names 9.2.0 would have returned that Row 25 withholds
    (the rule in the module docstring)."""

    if outcome is None:
        return frozenset()
    names = set()
    if call.arguments["group_by_domain"]:
        names.add("by_domain")
    if outcome.search_type in entities.AREA_MODES:
        names.add("area_filter")
    if outcome.search_type == entities.AREA_ONLY:
        names.add("area_names")
        if outcome.surface.total == 0:
            names.add("message")
    return frozenset(names)


async def search(arguments: Any, *, session_factory: SessionFactory,
                 subcall_readers: Mapping | None = None, known_secrets: tuple = (),
                 monotonic: Callable[[], float] = time.monotonic) -> dict:
    """One native ``ha_search`` call. Returns the reply or raises a closed
    ``SearchError``."""

    call = parse_call(arguments)
    entity = entity_request(call)
    config = config_request(call)
    reasons = Reasons()
    warnings: list[str] = []
    if call.explicit:
        warnings.append(WARNING_ENTITY_SKIPPED)
    if call.config_skipped:
        warnings.append(WARNING_CONFIG_SKIPPED)
    try:
        session = session_factory(reads_for(entity, config))
        sources: dict = {}
        for name in reads_for(entity, config):
            if c.READS[name].shape == "rows":
                sources[name] = await session.inventory(name)
        entities.source_result(sources, "states")
        states = entities.state_rows(sources["states"])
        reasons.add("source_unavailable", None, "malformed_state_row", states.count)
        entity_outcome = None
        if entity is not None:
            entity_outcome = await entities.search_entities(
                entity, sources, states, session.entity_entries, reasons)
            warnings.extend(entity_outcome.warnings)
        config_outcome = None
        if config is not None:
            config_outcome = await configs.search_configs(
                config, states=states, sources=sources, reader=session,
                subcall_readers=subcall_readers or {}, reasons=reasons, monotonic=monotonic)
    except c.CoreReadError as exc:
        source = "core_authority" if exc.reason == "authority_unavailable" else "core_reads"
        raise SearchError("source_unavailable", source=source) from None

    values = call.arguments
    spec = ReplySpec(
        query=values["query"],
        search_types=tuple(call.search_types if call.explicit else configs.DEFAULT_SEARCH_TYPES),
        applied=applied(call),
        offset=values["offset"],
        limit=values["limit"],
        entity=entity_outcome.surface if entity_outcome is not None else None,
        config=config_outcome.surface if config_outcome is not None else None,
        dashboards=config_outcome.dashboards if config_outcome is not None else False,
        search_type=entity_outcome.search_type if entity_outcome is not None else None,
        domain_filter=call.domain_filter if entity_outcome is not None else None,
        include_config=values["include_config"],
        warnings=tuple(warnings),
        reasons=reasons,
        fields=frozenset(call.fields) if call.fields is not None else None,
        withheld=withheld_names(call, entity_outcome),
        bucket_withheld=config_outcome.bucket_withheld if config_outcome is not None else {},
    )
    return build_reply(spec, known_secrets=known_secrets)
