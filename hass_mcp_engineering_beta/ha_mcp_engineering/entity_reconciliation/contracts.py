"""Fixed read contract for the HAMCP-132 unregistered-entity report.

The tool issues exactly these Home Assistant Core 2026.10.1 reads, none of
which is registered with ``require_admin`` and none of which has a side
effect:

- REST ``GET /api/states``: every current state object.
- WebSocket ``config/entity_registry/list``: every entity registry entry.
- WebSocket ``entity/source`` (``components/websocket_api/commands.py``):
  ``{entity_id: {"domain": platform}}`` for every entity a platform added,
  registered or not (``helpers/entity.py``). An entity with a state and no
  row was written directly (``POST /api/states`` or ``async_set``).
- WebSocket ``search/related`` with ``item_type: entity``, batched and only
  for listed platform entities, which resolves a registry-less entity's
  config entry through ``entity_sources``.

No other command, REST route, Supervisor endpoint or delegated tool exists in
this package. Core records no file or package for a registry-less entity, so
the definition location is never asserted.
"""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import Any

from ..entity_ids import STRICT_ENTITY_ID
from ..errors import ErrorCode, GovernanceError

STATES_PATH = "/states"
REGISTRY_COMMAND = MappingProxyType({"type": "config/entity_registry/list"})
SOURCE_COMMAND = MappingProxyType({"type": "entity/source"})
SEARCH_TYPE = "search/related"

CORE_REQUIREMENTS = (
    "core.basic_rest_read", "core.basic_websocket_read", "core.non_device_registry_read",
)
SCHEMA = "unregistered-entities-v1"

DEFAULT_LIMIT = 200
MAX_LIMIT = 500
MAX_DOMAINS = 50
# Compact JSON budget for the report inside the 60,000-character envelope.
RESPONSE_BUDGET = 50_000

DOMAIN = re.compile(r"(?!_)(?!.*__)[0-9a-z_]{1,64}(?<!_)\Z", re.ASCII)

ALLOWED_ARGUMENTS = frozenset({"domains", "limit"})


class EntityReconciliationError(GovernanceError):
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


def validate_arguments(arguments: Any) -> tuple[tuple[str, ...] | None, int]:
    """Validate raw caller arguments before the SDK can coerce them."""

    if not isinstance(arguments, dict) or not set(arguments) <= ALLOWED_ARGUMENTS:
        raise ValueError("Invalid unregistered entity arguments.")
    limit = arguments.get("limit", DEFAULT_LIMIT)
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError("Invalid unregistered entity arguments.")
    domains = arguments.get("domains")
    if domains is None:
        return None, limit
    if (type(domains) is not list or not 1 <= len(domains) <= MAX_DOMAINS
            or not all(type(item) is str and DOMAIN.fullmatch(item) for item in domains)
            or len(set(domains)) != len(domains)):
        raise ValueError("Invalid unregistered entity arguments.")
    return tuple(domains), limit


def search_payload(entity_id: str) -> dict:
    if STRICT_ENTITY_ID.fullmatch(entity_id) is None:
        raise ValueError("invalid entity id")
    return {"type": SEARCH_TYPE, "item_type": "entity", "item_id": entity_id}
