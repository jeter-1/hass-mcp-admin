"""Fixed read contract for the HAMCP-132 configuration-definition report.

The tool issues exactly these reads against Home Assistant Core 2026.10.1,
none of which has a side effect:

- the admitted ha-mcp YAML reader (``ha_config_get_yaml``) through the
  gateway's ``configuration_definitions`` route, which can request only the
  fixed ``configuration.yaml`` paths in ``DEFINITION_YAML_PATHS``;
- REST ``GET /api/states`` and ``GET /api/config`` (``components`` only);
- WebSocket ``config/entity_registry/list`` and ``entity/source``;
- WebSocket ``config_entries/get`` with a ``domain`` from the closed
  integration set or ``ha_mcp_tools``. Core's ``as_json_fragment``
  (``config_entries.py:677-703``) never includes an entry's ``data`` or
  ``options``, so a UI-defined definition is reported as
  ``definition_not_readable_without_flow`` (owner decision (i));
- WebSocket ``manifest/get`` for ``ha_mcp_tools`` (Core's command, not the
  component's).

No ``ha_mcp_tools/*`` component command, options flow, ``.storage`` file,
Supervisor endpoint or other delegated tool is reachable from this package.
The component is not used, now or in a later slice (owner decision,
2026-10-21, ``docs/HAMCP_132_SLICE2_COMPONENT_REVIEW.md`` section 11).
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from ..entity_ids import STRICT_ENTITY_ID
from ..errors import ErrorCode, GovernanceError

REGISTRY_COMMAND = MappingProxyType({"type": "config/entity_registry/list"})
SOURCE_COMMAND = MappingProxyType({"type": "entity/source"})
COMPONENT_DOMAIN = "ha_mcp_tools"
MANIFEST_COMMAND = MappingProxyType({"type": "manifest/get", "integration": COMPONENT_DOMAIN})

CORE_REQUIREMENTS = (
    "core.basic_rest_read", "core.basic_websocket_read", "core.non_device_registry_read",
)
SCHEMA = "configuration-definitions-v1"
READER_PURPOSE = "configuration_definitions"

# The integrations whose YAML definitions this tool can project. Each has a
# positive per-key allowlist in ``schemas.py``.
INTEGRATIONS = (
    "template", "sql", "filter", "derivative", "statistics", "group", "integration",
    "min_max", "history_stats", "threshold", "trend", "utility_meter",
)
MAX_ENTITY_IDS = 50
MAX_DEFINITIONS = 200
MAX_UI_ENTRIES = 50
MAX_OMITTED_ITEMS = 50
# Compact JSON budget for the report inside the 60,000-character envelope.
RESPONSE_BUDGET = 50_000
MAX_TEXT = 1_024

ALLOWED_ARGUMENTS = frozenset({"entity_ids", "integrations"})

YAML_FAILURE_CODES = frozenset({
    "yaml_reader_not_admitted", "yaml_read_failed", "yaml_read_response_identity_invalid",
})

# Withheld in every position, whatever the reader returned (design section
# 6): the reader may leave a sqlite URL visible under a connection key (R11).
WITHHELD_KEYS = frozenset({"db_url"})

LABELLED_KEY_DISCLOSURE = (
    "keys with a label (for example 'template 2:' or 'sensor kitchen:') cannot be addressed by the "
    "YAML reader and are not read"
)
INCLUDE_DISCLOSURE = "!include targets and package contents are disclosed but never followed or read"
PARSE_DISCLOSURE = (
    "configuration.yaml as parsed by the YAML reader (YAML 1.2) on disk, not the configuration Core loaded"
)


class ConfigurationDefinitionsError(GovernanceError):
    """Only a closed reason reaches error and audit serialization."""

    REASONS = frozenset({
        "authority_unavailable", "sources_unavailable", "invalid_arguments",
    })

    def __init__(self, reason: str):
        if reason not in self.REASONS:
            reason = "sources_unavailable"
        code = {
            "authority_unavailable": ErrorCode.PROVIDER_UNAVAILABLE,
            "invalid_arguments": ErrorCode.INVALID_REQUEST,
        }.get(reason, ErrorCode.ANALYSIS_UNAVAILABLE)
        super().__init__(code, details={"reason": reason})
        self.reason = reason


def validate_arguments(arguments: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Validate raw caller arguments before the SDK can coerce them.

    At least one of ``entity_ids`` and ``integrations`` is a non-empty list;
    each is bounded, closed and free of duplicates.
    """

    if not isinstance(arguments, dict) or not set(arguments) <= ALLOWED_ARGUMENTS:
        raise ValueError("Invalid configuration definition arguments.")
    entity_ids = arguments.get("entity_ids", [])
    integrations = arguments.get("integrations", [])
    if (type(entity_ids) is not list or len(entity_ids) > MAX_ENTITY_IDS
            or not all(type(item) is str and STRICT_ENTITY_ID.fullmatch(item) for item in entity_ids)
            or len(set(entity_ids)) != len(entity_ids)):
        raise ValueError("Invalid configuration definition arguments.")
    if (type(integrations) is not list or len(integrations) > len(INTEGRATIONS)
            or not all(type(item) is str and item in INTEGRATIONS for item in integrations)
            or len(set(integrations)) != len(integrations)):
        raise ValueError("Invalid configuration definition arguments.")
    if not entity_ids and not integrations:
        raise ValueError("Invalid configuration definition arguments.")
    return tuple(entity_ids), tuple(integrations)


def entries_payload(domain: str) -> dict:
    """The one ``config_entries/get`` shape: a closed domain filter."""

    if domain not in INTEGRATIONS and domain != COMPONENT_DOMAIN:
        raise ValueError("invalid domain")
    return {"type": "config_entries/get", "domain": domain}
