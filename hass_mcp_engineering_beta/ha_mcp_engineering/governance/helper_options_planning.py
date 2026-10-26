"""Closed input for a governed helper options plan (HAMCP-232).

Input is validated before any Home Assistant read: 1-8 changes, each naming
one ``sensor``/``binary_sensor`` entity of a config-entry helper, the fields
to ``set`` and the optional fields to ``clear`` (both from the closed union
of the per-type catalog in ``helper_options``), a reason and an optional
expiration.  The per-type rules (which fields a step has, which are pinned,
which may be cleared, and each value's type) are applied once the entry and
its step are known.  Nothing here reads or dispatches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..entity_ids import validate_entity_id
from . import helper_options as contract
from .entity_metadata_planning import EntityMetadataRefusal

ALLOWED_INPUT_FIELDS = frozenset({"changes", "reason", "expiration_minutes"})
CHANGE_FIELDS = frozenset({"entity_id", "set", "clear"})
REQUIRED_CHANGE_FIELDS = frozenset({"entity_id"})
MAX_CHANGES = 8
MAX_FIELDS_PER_CHANGE = 16
MAX_REASON_LENGTH = 500
INVALID_VALUE = "helper_options_invalid_value"
FIELD_NOT_SUPPORTED = "helper_options_field_not_supported"


class HelperOptionsRefusal(EntityMetadataRefusal):
    """A deterministic refusal with a design reason code; no plan exists."""


Refusal = HelperOptionsRefusal


@dataclass(frozen=True)
class Change:
    entity_id: str
    set_values: tuple[tuple[str, Any], ...]
    clear: tuple[str, ...]

    def set_dict(self) -> dict[str, Any]:
        return dict(self.set_values)


@dataclass(frozen=True)
class Request:
    changes: tuple[Change, ...]
    reason: str
    expiration_minutes: int

    @property
    def entity_ids(self) -> tuple[str, ...]:
        return tuple(change.entity_id for change in self.changes)

    @property
    def lease_target_id(self) -> str:
        return ",".join(sorted(self.entity_ids))


def _change(value: Any) -> Change:
    if (
        not isinstance(value, dict)
        or not all(isinstance(key, str) for key in value)
        or not set(value) <= CHANGE_FIELDS
        or not REQUIRED_CHANGE_FIELDS <= set(value)
    ):
        raise Refusal(INVALID_VALUE, field="changes")
    entity_id = value["entity_id"]
    try:
        validate_entity_id(entity_id)
    except ValueError:
        raise Refusal(INVALID_VALUE, field="entity_id") from None
    if entity_id.split(".", 1)[0] not in contract.ENTITY_DOMAINS:
        raise Refusal("helper_options_entity_domain_not_supported", field="entity_id")
    set_values = value.get("set", {})
    if not isinstance(set_values, dict) or not all(
        isinstance(key, str) for key in set_values
    ):
        raise Refusal(INVALID_VALUE, field="set")
    clear = value.get("clear", [])
    if not isinstance(clear, list) or not all(isinstance(item, str) for item in clear):
        raise Refusal(INVALID_VALUE, field="clear")
    if not set_values and not clear:
        raise Refusal("helper_options_no_change_requested", field="set")
    if len(set_values) + len(clear) > MAX_FIELDS_PER_CHANGE:
        raise Refusal(INVALID_VALUE, field="set")
    if len(set(clear)) != len(clear):
        raise Refusal("helper_options_duplicate_field", field="clear")
    overlap = sorted(set(set_values) & set(clear))
    if overlap:
        raise Refusal("helper_options_set_and_clear_overlap", fields=overlap[:16])
    unknown = sorted((set(set_values) | set(clear)) - contract.EDITABLE_FIELD_NAMES)
    if unknown:
        raise Refusal("helper_options_field_not_editable", fields=unknown[:16])
    return Change(
        entity_id=entity_id,
        set_values=tuple(sorted(set_values.items())),
        clear=tuple(sorted(clear)),
    )


def validate_request(arguments: Any) -> Request:
    """Validate the closed input before any Home Assistant read."""

    if not isinstance(arguments, dict) or not all(
        isinstance(key, str) for key in arguments
    ):
        raise Refusal("helper_options_invalid_request")
    unsupported = sorted(set(arguments) - ALLOWED_INPUT_FIELDS)
    if unsupported:
        raise Refusal(FIELD_NOT_SUPPORTED, fields=unsupported[:16])
    changes = arguments.get("changes")
    if not isinstance(changes, list) or not changes:
        raise Refusal(INVALID_VALUE, field="changes")
    if len(changes) > MAX_CHANGES:
        raise Refusal("helper_options_too_many_targets", limit=MAX_CHANGES)
    validated = tuple(_change(value) for value in changes)
    entity_ids = [change.entity_id for change in validated]
    if len(set(entity_ids)) != len(entity_ids):
        raise Refusal("helper_options_duplicate_target", field="entity_id")
    reason = arguments.get("reason")
    if (
        not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > MAX_REASON_LENGTH
    ):
        raise Refusal(INVALID_VALUE, field="reason")
    expiration = arguments.get("expiration_minutes", 120)
    if type(expiration) is not int or not 5 <= expiration <= 1440:
        raise Refusal(INVALID_VALUE, field="expiration_minutes")
    return Request(validated, reason.strip(), expiration)


__all__ = [
    "ALLOWED_INPUT_FIELDS",
    "CHANGE_FIELDS",
    "Change",
    "FIELD_NOT_SUPPORTED",
    "HelperOptionsRefusal",
    "INVALID_VALUE",
    "MAX_CHANGES",
    "Refusal",
    "Request",
    "validate_request",
]
