"""Fixed Home Assistant Core reads for native ``ha_search`` (HAMCP-481 W2;
HAMCP-501 W6 S1; HAMCP-447 dashboards).

This is the closed table of every Core read native search may issue: the
reads in design section 2.2, plus ``config/entity_registry/get_entries`` for
entity aliases, which ``config/entity_registry/list`` does not carry (added
by the architect on 2026-11-25). Nothing else exists here: no write, service
call, reload, options flow or config flow, and no Supervisor or delegated
call. Under S1 = b+ (approved by Josh as relayed by the architect,
2026-11-26), native search reads no automation, script or scene body: the
``automation/config``, ``script/config`` and scene configuration reads are not
in the table, and ``core.automation_configuration_read`` is not a requirement.

Under J2' option A (as relayed by the architect), the reads use only existing
signed Core profiles, and no profile, model or signed source changes. A read
is ``attested`` when one of those profiles pins its exact command and reply
shape. Every other read uses ``core.basic_rest_read`` or
``core.basic_websocket_read`` for transport authority only. ``core_reads``
projects its reply through the fixed shape below, which fails closed: drift
is a partial or unavailable result, never silent success.

HAMCP-447 (2.11.0 Lane B2, approved by Josh as relayed by the architect,
2026-11-27): dashboards are searched natively. The dashboard list and
configuration reads use ``core.dashboard_configuration_read``, the signed
profile that pins both commands (``websocket_lovelace_dashboards_list`` and
``websocket_lovelace_config_get``). A search needs it only when dashboards
are searched (``REQUIREMENT_ORDER``; ``ha_core_readmission/routes.py``).

Core 2026.11.3 (92da9f154079459b9e547126f9bd9fce726de16a) facts this table
relies on:

- ``config/entity_registry/get_entries`` has no ``require_admin`` and replies
  ``{entity_id: extended_dict | None}``; ``None`` is an unknown entity. Its
  ``aliases`` is a list whose ``None`` element is the computed-name sentinel
  (``helpers/entity_registry.py`` ``_serialize_aliases``).
- ``config/entity_registry/list`` rows carry no ``aliases`` and no
  ``has_entity_name``; ``has_entity_name`` is therefore optional here.
- The six ``input_*/list`` collection commands are not admin-only and reply
  with the stored item dicts.
- ``lovelace/dashboards/list`` is not admin-only. It replies with each
  dashboard whose configuration is set (``lovelace/dashboard.py``
  ``ws_list_item``): a storage row's ``id``, ``url_path``, ``mode``,
  ``title`` and display fields; a YAML row also has its ``filename``. The
  default dashboard Core always holds (``dashboards[None]``) is never
  listed.
- ``lovelace/config`` is not admin-only, and its ``url_path`` is optional. It
  replies with the stored configuration, or ``config_not_found`` for an
  unknown ``url_path`` or a dashboard with no saved configuration. With no
  ``url_path`` it reads the ``lovelace`` dashboard when one is listed, else
  the default (``lovelace/websocket.py``). A YAML dashboard's configuration
  load resolves ``!secret``, so native search never reads one.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType

from ..entity_ids import STRICT_ENTITY_ID
from ..errors import ErrorCode, GovernanceError

# A registry or collection row identity: non-empty, bounded, no control
# character (``ha_core_readmission/device_registry.py`` ``_identifier``).
ROW_ID = re.compile(r"[^\x00-\x1f\x7f]{1,255}\Z")
# A dashboard ``url_path`` native search sends to ``lovelace/config``
# (``governance/dashboard_metadata.py`` ``URL_PATH_PATTERN``; a test binds
# the two). A listed row outside it is not read.
URL_PATH = re.compile(r"[a-z0-9_-]{1,256}\Z")

# ``ha_core_readmission/source.py`` ``MAX_CORE_PROBE_ITEMS``: an inventory
# longer than this fails closed (design section 6.3).
MAX_ITEMS = 20_000
# ``clients/websocket.py`` ``MAX_BATCH_COMMANDS``.
MAX_BATCH_ITEMS = 4_096
# ha-mcp 9.2.0 ``_GET_ENTRIES_CHUNK_SIZE`` (``tools/smart_search/_entities.py:27``).
GET_ENTRIES_CHUNK = 500
# ``governance/dashboard_metadata.py`` ``MAX_DASHBOARDS``: one search reads
# at most this many listed dashboards' configurations and discloses the rest.
MAX_DASHBOARDS = 500
# Configurations per pipelined batch, so at most this many replies are held
# at once; each is matched, then dropped.
CONFIG_CHUNK = 8

BASIC_REST = "core.basic_rest_read"
BASIC_WEBSOCKET = "core.basic_websocket_read"
NON_DEVICE_REGISTRY = "core.non_device_registry_read"
DEVICE_REGISTRY = "core.direct_device_registry_read"
DASHBOARD = "core.dashboard_configuration_read"

# Canonical order. ``core.direct_entity_state_read`` is not used: it attests
# a single-entity read, and the states list is ``rest_states_list`` under
# ``core.basic_rest_read``.
CORE_REQUIREMENTS = (BASIC_REST, BASIC_WEBSOCKET, NON_DEVICE_REGISTRY, DEVICE_REGISTRY)
# HAMCP-447: the dashboard reads' profile follows, required only when
# dashboards are searched.
REQUIREMENT_ORDER = (*CORE_REQUIREMENTS, DASHBOARD)


@dataclass(frozen=True, slots=True)
class CoreRead:
    """One fixed read: its exact command, authority and reply shape."""

    name: str
    transport: str  # "rest" or "websocket"
    # WebSocket: the command type. REST: "GET <route template>", for review
    # only; ``core_reads.rest_route`` builds the route from a literal
    # template (the HAMCP-200 path-safety scan), and a test binds the two.
    command: str
    requirement: str
    attested: bool
    shape: str  # "rows", "related", "entries" or "config"
    key: str | None = None  # the identity field of a row
    argument: re.Pattern[str] | None = None  # the grammar of one argument


def _rows(name: str, command: str, requirement: str, attested: bool, key: str) -> CoreRead:
    return CoreRead(name, "websocket", command, requirement, attested, "rows", key)


INPUT_DOMAINS = (
    "input_boolean", "input_number", "input_select", "input_text", "input_datetime", "input_button",
)

READS = MappingProxyType({
    read.name: read for read in (
        CoreRead("states", "rest", "GET /states", BASIC_REST, True, "rows", "entity_id"),
        _rows("area_registry", "config/area_registry/list", NON_DEVICE_REGISTRY, True, "area_id"),
        _rows("floor_registry", "config/floor_registry/list", NON_DEVICE_REGISTRY, True, "floor_id"),
        _rows("label_registry", "config/label_registry/list", NON_DEVICE_REGISTRY, True, "label_id"),
        _rows("entity_registry", "config/entity_registry/list", NON_DEVICE_REGISTRY, True, "entity_id"),
        _rows("device_registry", "config/device_registry/list", DEVICE_REGISTRY, True, "id"),
        CoreRead("search_related", "websocket", "search/related", BASIC_WEBSOCKET, False,
                 "related", argument=STRICT_ENTITY_ID),
        *(_rows(f"{domain}_list", f"{domain}/list", BASIC_WEBSOCKET, False, "id")
          for domain in INPUT_DOMAINS),
        CoreRead("entity_registry_entries", "websocket", "config/entity_registry/get_entries",
                 BASIC_WEBSOCKET, False, "entries", argument=STRICT_ENTITY_ID),
        _rows("dashboards", "lovelace/dashboards/list", DASHBOARD, True, "url_path"),
        CoreRead("dashboard_config", "websocket", "lovelace/config", DASHBOARD, True, "config",
                 argument=URL_PATH),
    )
})

# Reads dispatched as pipelined ``command_batch`` calls, one payload per item.
BATCH_READS = frozenset({"search_related", "dashboard_config"})

# Field kinds. A required field that is absent, or any field present with the
# wrong kind, is drift. ``OPTIONAL_FIELDS`` may be absent.
STR = "str"
OPTIONAL_STR = "str|None"
STR_LIST = "list[str]"
MAPPING = "mapping"
BOOL = "bool"
OPTIONAL_INT = "int|None"

ROW_FIELDS = MappingProxyType({
    "states": MappingProxyType({"state": STR, "attributes": MAPPING}),
    "area_registry": MappingProxyType({
        "name": STR, "aliases": STR_LIST, "floor_id": OPTIONAL_STR, "labels": STR_LIST,
    }),
    "floor_registry": MappingProxyType({"name": STR, "aliases": STR_LIST, "level": OPTIONAL_INT}),
    "label_registry": MappingProxyType({"name": STR}),
    # Core requires both on every row; the display fields and a YAML row's
    # ``filename`` are dropped.
    "dashboards": MappingProxyType({"mode": STR, "title": STR}),
    "entity_registry": MappingProxyType({
        "name": OPTIONAL_STR, "original_name": OPTIONAL_STR, "has_entity_name": BOOL,
        "platform": STR, "unique_id": STR, "area_id": OPTIONAL_STR, "device_id": OPTIONAL_STR,
        "labels": STR_LIST, "hidden_by": OPTIONAL_STR, "disabled_by": OPTIONAL_STR,
        "entity_category": OPTIONAL_STR, "next_name_part": OPTIONAL_STR,
    }),
    "device_registry": MappingProxyType({
        "name": OPTIONAL_STR, "name_by_user": OPTIONAL_STR, "area_id": OPTIONAL_STR,
        "parent_device_id": OPTIONAL_STR, "labels": STR_LIST, "disabled_by": OPTIONAL_STR,
        "manufacturer": OPTIONAL_STR, "model": OPTIONAL_STR, "next_name_part": OPTIONAL_STR,
    }),
    # Helper rows are kept whole: body search reads every stored field.
    **{f"{domain}_list": MappingProxyType({"name": STR}) for domain in INPUT_DOMAINS},
})
# Core 2026.11 removed ``has_entity_name`` from the registry wire dicts
# (HAMCP-251); a present value must still be a bool. Core 2026.11.3 sends
# ``next_name_part`` (entity_registry.py 419; device_registry.py 784, 908),
# and a child device's row has no manufacturer or model (device_registry.py
# 891-910). A present value must still be of its kind.
OPTIONAL_FIELDS = MappingProxyType({
    "entity_registry": frozenset({"has_entity_name", "next_name_part"}),
    "device_registry": frozenset({"manufacturer", "model", "next_name_part"}),
})
# Rows whose reply is reduced to the fields above; the others keep every field.
WHOLE_ROWS = frozenset(f"{domain}_list" for domain in INPUT_DOMAINS)

# ``search/related`` buckets ha-mcp 9.2.0 models (``tools/smart_search/_graph.py``).
RELATED_TYPES = frozenset({
    "area", "automation", "config_entry", "device", "entity", "floor", "group", "integration",
    "label", "person", "scene", "script",
})
# Core 2026.11.3 ``components/search/__init__.py`` ``ItemType``. A Core bucket
# outside ``RELATED_TYPES`` is ignored, as upstream does; a key Core does not
# define is drift.
CORE_RELATED_TYPES = RELATED_TYPES | {"automation_blueprint", "script_blueprint"}

# Closed result vocabulary.
# S1 b+ removed the per-item ``not_found`` of the configuration reads.
COMPLETE, PARTIAL, UNAVAILABLE = "complete", "partial", "unavailable"
UNAVAILABLE_REASONS = frozenset({
    "permission_failure", "timeout", "home_assistant_unavailable", "command_failed",
    "source_unavailable", "invalid_response", "inventory_bound_exceeded",
})
PARTIAL_REASONS = frozenset({
    "malformed_rows", "duplicate_rows", "field_drift", "item_failures",
})


class CoreReadError(GovernanceError):
    """Only a closed reason reaches error and audit serialization."""

    REASONS = frozenset({"authority_unavailable", "invalid_arguments"})

    def __init__(self, reason: str):
        if reason not in self.REASONS:
            reason = "authority_unavailable"
        code = {
            "authority_unavailable": ErrorCode.PROVIDER_UNAVAILABLE,
            "invalid_arguments": ErrorCode.INVALID_REQUEST,
        }[reason]
        super().__init__(code, details={"reason": reason})
        self.reason = reason


def requirements_for(names) -> tuple[str, ...]:
    """The Core requirements of the named reads, in canonical order."""

    needed = {READS[name].requirement for name in names}
    return tuple(requirement for requirement in REQUIREMENT_ORDER if requirement in needed)


def batch_payload(name: str, item: str) -> dict:
    """The exact payload of one batched read; refuses an unknown item."""

    if name != "search_related" or type(item) is not str:
        raise CoreReadError("invalid_arguments")
    read = READS[name]
    if read.argument.fullmatch(item) is None:
        raise CoreReadError("invalid_arguments")
    return {"type": read.command, "item_type": "entity", "item_id": item}


def entries_payload(entity_ids) -> dict:
    """One ``get_entries`` chunk; refuses a malformed or oversized chunk."""

    if (type(entity_ids) is not tuple or not 1 <= len(entity_ids) <= GET_ENTRIES_CHUNK
            or not all(type(item) is str and STRICT_ENTITY_ID.fullmatch(item) for item in entity_ids)):
        raise CoreReadError("invalid_arguments")
    return {"type": READS["entity_registry_entries"].command, "entity_ids": list(entity_ids)}


def config_payload(url_path) -> dict:
    """One ``lovelace/config`` read: the default dashboard for ``None``, else
    the listed dashboard at ``url_path``. ``force`` is never sent; a
    malformed ``url_path`` is refused."""

    read = READS["dashboard_config"]
    if url_path is None:
        return {"type": read.command}
    if type(url_path) is not str or read.argument.fullmatch(url_path) is None:
        raise CoreReadError("invalid_arguments")
    return {"type": read.command, "url_path": url_path}
