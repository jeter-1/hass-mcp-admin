"""Fixed read contract for ``get_helper_settings`` (HAMCP-519).

Release 2.11.0 Lane C GO, 2026-11-27 (Josh's decisions, as relayed by the
architect): the tool is ``get_helper_settings``; an ``input_text`` initial
value is reported as presence and length only; the ``input_boolean``,
``input_number``, ``input_select`` and ``input_datetime`` initial values are
reported in full; the list pages with the PC-1 cursor (``paging``).

Sources, all existing fixed reads in ``search.core_contracts.READS``: the six
``input_*/list`` collection commands (``core.basic_websocket_read``, not
admin-only) and ``config/entity_registry/list``
(``core.non_device_registry_read``) to resolve each helper's entity id. No
write, service call, reload or delegated read is reachable.

Home Assistant Core 2026.11.3 storage fields (``components/input_*/__init__.py``
``STORAGE_FIELDS``) this projection relies on:

- ``input_boolean``: name, initial (bool), icon.
- ``input_button``: name, icon; no initial.
- ``input_number``: name, min, max, initial, step, icon,
  unit_of_measurement, device_class, mode (``box`` or ``slider``).
- ``input_select``: name, options (non-empty unique strings), initial, icon.
- ``input_text``: name, min, max, initial, icon, unit_of_measurement,
  pattern, mode (``text`` or ``password``).
- ``input_datetime``: name, has_date, has_time, icon, initial.

Each ``{domain}/list`` reply holds only the storage collection: a helper
defined in YAML is not in it, so this tool never lists YAML helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ..errors import ErrorCode, GovernanceError
from ..paging import MAX_CURSOR_CHARS
from ..search import core_contracts as core


NAME = "get_helper_settings"
SCHEMA = "helper-settings-v1"
PROFILE = "helper-settings-read-v1"

DOMAINS = core.INPUT_DOMAINS
REGISTRY_READ = "entity_registry"
LIST_READS = MappingProxyType({domain: f"{domain}_list" for domain in DOMAINS})
CORE_REQUIREMENTS = (core.BASIC_WEBSOCKET, core.NON_DEVICE_REGISTRY)

DEFAULT_LIMIT = 100
MAX_LIMIT = 200
# Untrusted text longer than this is shortened by the shared sanitizer.
MAX_TEXT = 1024
# An input_select keeps its first options; the rest are counted.
MAX_OPTIONS = 100

# Field kinds.
NUMBER = "number"
INT = "int"
BOOL = "bool"
STR = "str"
OPTIONAL_STR = "str|None"
STR_LIST = "list[str]"

# The kind of each domain's initial value; ``None``: the domain has none.
INITIAL_KINDS = MappingProxyType({
    "input_boolean": BOOL,
    "input_number": NUMBER,
    "input_select": STR,
    "input_text": STR,
    "input_datetime": STR,
    "input_button": None,
})
# The value is never returned: presence and length only (GO).
INITIAL_LENGTH_ONLY = frozenset({"input_text"})

# The closed per-domain settings allowlist. Any other stored key is ignored.
SETTINGS_FIELDS = MappingProxyType({
    "input_boolean": MappingProxyType({}),
    "input_button": MappingProxyType({}),
    "input_number": MappingProxyType({
        "min": NUMBER, "max": NUMBER, "step": NUMBER, "mode": STR,
        "unit_of_measurement": OPTIONAL_STR, "device_class": OPTIONAL_STR,
    }),
    "input_select": MappingProxyType({"options": STR_LIST}),
    "input_text": MappingProxyType({
        "min": INT, "max": INT, "mode": STR, "pattern": OPTIONAL_STR,
        "unit_of_measurement": OPTIONAL_STR,
    }),
    "input_datetime": MappingProxyType({"has_date": BOOL, "has_time": BOOL}),
})

# Closed reply vocabulary.
PARTIAL_REASONS = frozenset({
    "source_unavailable",      # a selected helper list could not be read
    "entity_registry_unavailable",  # entity ids could not be resolved
    "malformed_rows",          # Core rows without a usable id were dropped
    "duplicate_rows",          # rows claiming one identity were dropped
    "field_drift",             # a stored field had an unexpected kind
    "options_truncated",       # an input_select listed more than MAX_OPTIONS
    "redacted_by_policy",      # the shared sanitizer withheld text
    "truncated_text",          # the shared sanitizer shortened text
})
# ``unresolved``: the registry was unreadable or partial, so no answer is
# given; ``not_registered``: a complete registry has no entry for it.
ENTITY_ID_STATUSES = frozenset({"resolved", "not_registered", "unresolved", "ambiguous"})
COVERAGE = "storage_collections_only"

ALLOWED_ARGUMENTS = frozenset({"domains", "limit", "cursor"})


class HelperSettingsError(GovernanceError):
    """Only a closed reason reaches error and audit serialization."""

    REASONS = frozenset({
        "authority_unavailable", "sources_unavailable", "invalid_arguments", "sanitization_failed",
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


@dataclass(frozen=True, slots=True)
class HelperSettingsRequest:
    domains: tuple[str, ...]  # canonical (DOMAINS) order
    limit: int
    cursor: str


def validate_arguments(arguments: Any) -> HelperSettingsRequest:
    """Validate raw caller arguments before the SDK can coerce them.

    A refused value is never reflected: every refusal is the one closed
    ``invalid_arguments`` reason.
    """

    if type(arguments) is not dict or not set(arguments) <= ALLOWED_ARGUMENTS:
        raise HelperSettingsError("invalid_arguments")
    domains = arguments.get("domains")
    if domains is None:
        selected = DOMAINS
    else:
        if (type(domains) is not list or not 1 <= len(domains) <= len(DOMAINS)
                or not all(type(item) is str and item in DOMAINS for item in domains)
                or len(set(domains)) != len(domains)):
            raise HelperSettingsError("invalid_arguments")
        selected = tuple(domain for domain in DOMAINS if domain in domains)
    limit = arguments.get("limit", DEFAULT_LIMIT)
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise HelperSettingsError("invalid_arguments")
    cursor = arguments.get("cursor", "")
    if type(cursor) is not str or len(cursor) > MAX_CURSOR_CHARS:
        raise HelperSettingsError("invalid_arguments")
    return HelperSettingsRequest(selected, limit, cursor)


def reads_for(request: HelperSettingsRequest) -> tuple[str, ...]:
    return (REGISTRY_READ, *(LIST_READS[domain] for domain in request.domains))
