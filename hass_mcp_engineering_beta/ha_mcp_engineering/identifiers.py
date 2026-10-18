"""Tool-boundary identifier grammars for Home Assistant request values (HAMCP-200).

Tools that place a caller-supplied identifier into a Home Assistant request
check it here first, so a malformed or traversal-shaped value is refused as
``invalid_request`` before any provider, session or credential is involved.
The central path guard in ``clients.paths`` remains the backstop for every
request regardless of which tool built it.

Automation configuration IDs deliberately exclude ``.``: the Home Assistant
frontend creates numeric IDs, and no dot is needed for one path segment.
"""

from __future__ import annotations

import re
from typing import Any

from .clients.paths import UnsafeRequestPathError
from .entity_ids import ENTITY_ID_PATTERN, MAX_ENTITY_ID_LENGTH


AUTOMATION_CONFIG_ID_PATTERN = re.compile(r"[A-Za-z0-9_:-]{1,256}\Z")
SCRIPT_ID_PATTERN = re.compile(r"[a-z0-9_]{1,255}\Z")
DOMAIN_PATTERN = re.compile(r"[a-z0-9_]{1,64}\Z")
SERVICE_PATTERN = re.compile(r"[a-z0-9_]{1,128}\Z")


def _require(pattern: re.Pattern[str], value: Any, field: str) -> str:
    if type(value) is not str or not pattern.fullmatch(value):
        raise UnsafeRequestPathError("invalid_identifier", field=field)
    return value


def require_automation_config_id(value: Any, *, field: str = "automation_id") -> str:
    return _require(AUTOMATION_CONFIG_ID_PATTERN, value, field)


def require_script_id(value: Any, *, field: str = "script_id") -> str:
    return _require(SCRIPT_ID_PATTERN, value, field)


def require_domain(value: Any, *, field: str = "domain") -> str:
    return _require(DOMAIN_PATTERN, value, field)


def require_service(value: Any, *, field: str = "service") -> str:
    return _require(SERVICE_PATTERN, value, field)


def require_entity_id(value: Any, *, field: str = "entity_id") -> str:
    if type(value) is not str or len(value) > MAX_ENTITY_ID_LENGTH:
        raise UnsafeRequestPathError("invalid_identifier", field=field)
    return _require(ENTITY_ID_PATTERN, value, field)


__all__ = [
    "AUTOMATION_CONFIG_ID_PATTERN",
    "DOMAIN_PATTERN",
    "SCRIPT_ID_PATTERN",
    "SERVICE_PATTERN",
    "require_automation_config_id",
    "require_domain",
    "require_entity_id",
    "require_script_id",
    "require_service",
]
