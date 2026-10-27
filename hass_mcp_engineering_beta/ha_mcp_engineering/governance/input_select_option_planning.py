"""Closed input for a governed input_select option plan (HAMCP-145).

Input is validated before any Home Assistant read: one exact
``input_select`` entity, the exact (case-sensitive, unstripped) option to
select, a reason and an optional expiration.  Nothing here reads or
dispatches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .entity_metadata_planning import EntityMetadataRefusal

ALLOWED_INPUT_FIELDS = frozenset({"entity_id", "option", "reason", "expiration_minutes"})
REQUIRED_INPUT_FIELDS = frozenset({"entity_id", "option", "reason"})
ENTITY_ID_PATTERN = re.compile(r"input_select\.[a-z0-9_]{1,114}\Z")
MAX_OPTION_LENGTH = 255
MAX_REASON_LENGTH = 500
INVALID_VALUE = "input_select_option_invalid_value"
FIELD_NOT_SUPPORTED = "input_select_option_field_not_supported"
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class InputSelectOptionRefusal(EntityMetadataRefusal):
    """A deterministic refusal with a design reason code; no plan exists."""


Refusal = InputSelectOptionRefusal


@dataclass(frozen=True)
class Request:
    entity_id: str
    option: str
    reason: str
    expiration_minutes: int

    @property
    def lease_target_id(self) -> str:
        return self.entity_id


def validate_request(arguments: Any) -> Request:
    """Validate the closed input before any Home Assistant read."""

    if not isinstance(arguments, dict) or not all(
        isinstance(key, str) for key in arguments
    ):
        raise Refusal("input_select_option_invalid_request")
    unsupported = sorted(set(arguments) - ALLOWED_INPUT_FIELDS)
    if unsupported:
        raise Refusal(FIELD_NOT_SUPPORTED, fields=unsupported[:16])
    missing = sorted(REQUIRED_INPUT_FIELDS - set(arguments))
    if missing:
        raise Refusal(INVALID_VALUE, field=missing[0])
    entity_id = arguments["entity_id"]
    if not isinstance(entity_id, str) or not ENTITY_ID_PATTERN.fullmatch(entity_id):
        raise Refusal(INVALID_VALUE, field="entity_id")
    option = arguments["option"]
    # The option is compared exactly as Core compares it: case-sensitive and
    # never stripped.  Control characters can never be an option a UI shows.
    if (
        not isinstance(option, str)
        or not 1 <= len(option) <= MAX_OPTION_LENGTH
        or _CONTROL.search(option)
    ):
        raise Refusal(INVALID_VALUE, field="option")
    reason = arguments["reason"]
    if (
        not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > MAX_REASON_LENGTH
    ):
        raise Refusal(INVALID_VALUE, field="reason")
    expiration = arguments.get("expiration_minutes", 120)
    if type(expiration) is not int or not 5 <= expiration <= 1440:
        raise Refusal(INVALID_VALUE, field="expiration_minutes")
    return Request(entity_id, option, reason.strip(), expiration)


__all__ = [
    "ALLOWED_INPUT_FIELDS",
    "ENTITY_ID_PATTERN",
    "FIELD_NOT_SUPPORTED",
    "INVALID_VALUE",
    "InputSelectOptionRefusal",
    "MAX_OPTION_LENGTH",
    "Refusal",
    "Request",
    "validate_request",
]
