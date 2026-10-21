"""Fixed read contract for the HAMCP-132 recorder settings report.

Sources (design ``docs/HAMCP_132_DEFINITION_VISIBILITY_DESIGN.md`` section 3A,
verified against Home Assistant Core 2026.10.1):

- A1: the admitted ``ha_config_get_yaml`` reader, through the gateway's fixed
  ``yaml_config_reader`` route, for ``recorder`` and
  ``homeassistant.packages`` (and ``homeassistant`` only to classify a
  not-found packages path). The reader parses ``configuration.yaml`` on disk;
  it never follows ``!include`` and never reads packages.
- A2: Core's ``CONFIG_SCHEMA`` defaults, compiled here from the pin.
- A3: WebSocket ``recorder/info`` (not admin; ``unknown_command`` when the
  recorder is not set up).
- A4: WebSocket ``recorder/entity_options/get`` (admin, read-only), one
  command per entity through the pipelined batch allowlist.

``db_url`` and every unrecognized recorder key are never copied into output:
the projection is a positive allowlist of the keys below.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from ..entity_ids import STRICT_ENTITY_ID
from ..errors import ErrorCode, GovernanceError


INFO_COMMAND = MappingProxyType({"type": "recorder/info"})
ENTITY_OPTIONS_TYPE = "recorder/entity_options/get"

YAML_RECORDER_PATH = "recorder"
YAML_PACKAGES_PATH = "homeassistant.packages"
YAML_CORE_PATH = "homeassistant"

CORE_REQUIREMENTS = ("core.basic_rest_read", "core.basic_websocket_read")
SCHEMA = "recorder-settings-v1"

MAX_ENTITY_IDS = 50
MAX_FILTER_ITEMS = 500
MAX_FILTER_TEXT = 255
RESPONSE_BUDGET = 50_000

# Recorder CONFIG_SCHEMA (components/recorder/__init__.py:100-133 at the pin):
# key -> (validator, Core default). ``positive_int`` is Coerce(int) min 0.
SCALAR_SETTINGS = MappingProxyType({
    "auto_purge": ("boolean", True),
    "auto_repack": ("boolean", True),
    "purge_keep_days": ("int_min_1", 10),
    "commit_interval": ("int_min_0", 5),
    "db_max_retries": ("int_min_0", 10),
    "db_retry_wait": ("int_min_0", 3),
})
# Deprecated: reported only when written; their schema defaults are never
# presented as effective settings.
DEPRECATED_SETTINGS = MappingProxyType({
    "purge_interval": "int_min_0",
    "db_integrity_check": "boolean",
})
DB_URL_KEY = "db_url"
FILTER_KEYS = ("include", "exclude")
FILTER_LIST_KEYS = ("domains", "entity_globs", "entities")
EVENT_TYPES_KEY = "event_types"
RECOGNIZED_KEYS = frozenset(SCALAR_SETTINGS) | frozenset(DEPRECATED_SETTINGS) | {
    DB_URL_KEY, *FILTER_KEYS,
}

# The reader returns parsed values, never spellings. Values whose YAML 1.1
# reading can differ are reported ``parser_dependent`` (projection.py); these
# spelling cannot be recognized from the parsed value at all.
YAML_NUMERIC_SPELLING_LIMITATION = (
    "not observable: a 0o-prefixed integer is a number under YAML 1.2 but a string "
    "under YAML 1.1; every integer is reported as if it were not spelled that way"
)

# recorder/info reply keys (basic_websocket_api.py:21-54) and their JSON types.
RUNTIME_FIELDS = MappingProxyType({
    "backlog": (int, type(None)),
    "max_backlog": (int,),
    "migration_in_progress": (bool,),
    "migration_is_live": (bool,),
    "recording": (bool,),
    "thread_running": (bool,),
    "db_in_default_location": (bool,),
})

# Closed reader failure codes that may be named in a source reason.
YAML_FAILURE_CODES = frozenset({
    "yaml_file_not_found", "yaml_parse_error",
    "yaml_upstream_validation_or_component_unavailable", "yaml_read_failed",
    "yaml_read_response_identity_invalid",
})

ALLOWED_ARGUMENTS = frozenset({"entity_ids"})

class RecorderSettingsError(GovernanceError):
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


def validate_arguments(arguments: Any) -> tuple[str, ...]:
    """Validate raw caller arguments before the SDK can coerce them."""

    if not isinstance(arguments, dict) or not set(arguments) <= ALLOWED_ARGUMENTS:
        raise ValueError("Invalid recorder settings arguments.")
    entity_ids = arguments.get("entity_ids", [])
    if type(entity_ids) is not list or len(entity_ids) > MAX_ENTITY_IDS:
        raise ValueError("Invalid recorder settings arguments.")
    if not all(type(item) is str and STRICT_ENTITY_ID.fullmatch(item) for item in entity_ids):
        raise ValueError("Invalid recorder settings arguments.")
    if len(set(entity_ids)) != len(entity_ids):
        raise ValueError("Invalid recorder settings arguments.")
    return tuple(entity_ids)
