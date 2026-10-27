"""Literal input_select state-trigger values (HAMCP-145, design OD-11).

A diagnostic side channel for per-option trigger disclosure.  It records,
for each automation state trigger on an exact ``input_select`` entity, the
literal ``to``/``from``/``not_to``/``not_from`` option values.  It is never
part of the dependency snapshot fingerprint and never decides whether a
dependency exists: the obligation ledger remains the only dependency
authority.  A template, a non-string value, an ``attribute`` trigger, or a
bound that clips evidence marks the record incomplete, which a reader must
disclose as "per-option effect unknown", never as "does not fire".

A trigger with ``enabled: false``, or any trigger of an automation whose
state is ``off``, is recorded as disabled and disclosed as "would not fire
while disabled", never as firing (review M2).  A non-literal ``enabled``
value makes the enablement, and so the effect, unknown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .extraction import _bounded, valid_entity_id

DOMAIN = "input_select"
MAX_TRIGGERS_PER_AUTOMATION = 32
MAX_VALUES_PER_FIELD = 64
MAX_ENTITIES_PER_TRIGGER = 32
MAX_VALUE_CHARS = 255
_FIELDS = (("to", "to"), ("from", "from"), ("not_to", "not_to"), ("not_from", "not_from"))
ENABLED = "enabled"
DISABLED = "disabled"
UNKNOWN = "unknown"
DISABLED_EFFECT = "disabled"
DISABLED_EFFECT_MEANING = "would not fire while disabled"


@dataclass(frozen=True)
class StateTriggerValues:
    source_id: str
    source_entity_id: str | None
    config_path: str
    entity_ids: tuple[str, ...]
    # ``None`` means the trigger does not constrain that side.
    to: tuple[str, ...] | None
    from_: tuple[str, ...] | None
    not_to: tuple[str, ...]
    not_from: tuple[str, ...]
    complete: bool
    reason_codes: tuple[str, ...] = ()
    enablement: str = ENABLED


def _values(raw: Any, *, secret: str) -> tuple[tuple[str, ...] | None, bool, str | None]:
    """Return (values, complete, reason).  ``None`` values: unconstrained."""

    if raw is None:
        return None, True, None
    items = raw if isinstance(raw, list) else [raw]
    if len(items) > MAX_VALUES_PER_FIELD:
        return (), False, "trigger_value_limit_exceeded"
    values: list[str] = []
    for item in items:
        if not isinstance(item, str):
            return (), False, "trigger_value_not_literal"
        if "{{" in item or "{%" in item:
            return (), False, "trigger_value_template"
        bounded = _bounded(item, limit=MAX_VALUE_CHARS, secret=secret)
        if bounded != item:
            # A redacted or clipped value cannot be compared to an option.
            return (), False, "trigger_value_not_retained"
        values.append(item)
    return tuple(sorted(set(values))), True, None


def _enablement(trigger: dict[str, Any], automation_disabled: bool) -> tuple[str, str | None]:
    if automation_disabled:
        return DISABLED, "automation_disabled"
    enabled = trigger.get("enabled", True)
    if enabled is True:
        return ENABLED, None
    if enabled is False:
        return DISABLED, "trigger_disabled"
    # A template or any other value: Core decides at setup, the reader cannot.
    return UNKNOWN, "trigger_enablement_not_literal"


def extract_state_trigger_values(
    config: Any,
    *,
    source_id: str,
    source_entity_id: str | None,
    secret: str,
    source_state: Any = None,
) -> tuple[StateTriggerValues, ...]:
    """Extract literal option values of input_select state triggers.

    ``source_state`` is the automation entity's state; ``off`` disables
    every trigger it has.
    """

    if not isinstance(config, dict):
        return ()
    automation_disabled = source_state == "off"
    records: list[StateTriggerValues] = []
    for key in ("triggers", "trigger"):
        triggers = config.get(key)
        if triggers is None:
            continue
        triggers = triggers if isinstance(triggers, list) else [triggers]
        for position, trigger in enumerate(triggers):
            if not isinstance(trigger, dict):
                continue
            if trigger.get("platform", trigger.get("trigger")) != "state":
                continue
            raw_entities = trigger.get("entity_id")
            raw_entities = raw_entities if isinstance(raw_entities, list) else [raw_entities]
            entities = tuple(sorted({
                item for item in raw_entities[:MAX_ENTITIES_PER_TRIGGER]
                if isinstance(item, str)
                and item.startswith(DOMAIN + ".")
                and valid_entity_id(item)
            }))
            if not entities:
                continue
            enablement, enablement_reason = _enablement(trigger, automation_disabled)
            reasons: list[str] = [enablement_reason] if enablement_reason else []
            complete = len(raw_entities) <= MAX_ENTITIES_PER_TRIGGER
            if not complete:
                reasons.append("trigger_entity_limit_exceeded")
            if trigger.get("attribute") is not None:
                # An attribute trigger does not observe the selected option.
                complete = False
                reasons.append("attribute_trigger")
            parsed: dict[str, tuple[str, ...] | None] = {}
            for name, field_key in _FIELDS:
                values, ok, reason = _values(trigger.get(field_key), secret=secret)
                parsed[name] = values
                if not ok:
                    complete = False
                    reasons.append(str(reason))
            if len(records) >= MAX_TRIGGERS_PER_AUTOMATION:
                records.append(StateTriggerValues(
                    source_id=source_id, source_entity_id=source_entity_id,
                    config_path=f"{key}[{position}]", entity_ids=entities,
                    to=(), from_=(), not_to=(), not_from=(),
                    complete=False, reason_codes=("trigger_record_limit_exceeded",),
                    enablement=DISABLED if automation_disabled else UNKNOWN,
                ))
                return tuple(records)
            records.append(StateTriggerValues(
                source_id=source_id,
                source_entity_id=source_entity_id,
                config_path=f"{key}[{position}]",
                entity_ids=entities,
                to=parsed["to"],
                from_=parsed["from"],
                not_to=parsed["not_to"] or (),
                not_from=parsed["not_from"] or (),
                complete=complete,
                reason_codes=tuple(sorted(set(reasons))),
                enablement=enablement,
            ))
    return tuple(records)


def option_effect(
    record: StateTriggerValues, *, current: str, option: str
) -> str:
    """Classify one ``current -> option`` transition.

    ``disabled`` (would not fire while disabled), ``unknown``, ``fires`` or
    ``does_not_fire``.  A disabled trigger is never classified as firing.
    """

    if record.enablement == DISABLED:
        return DISABLED_EFFECT
    if record.enablement != ENABLED or not record.complete:
        return "unknown"
    to_match = (record.to is None or option in record.to) and option not in record.not_to
    from_match = (
        (record.from_ is None or current in record.from_)
        and current not in record.not_from
    )
    return "fires" if to_match and from_match else "does_not_fire"


__all__ = [
    "DISABLED_EFFECT",
    "DISABLED_EFFECT_MEANING",
    "MAX_TRIGGERS_PER_AUTOMATION",
    "StateTriggerValues",
    "extract_state_trigger_values",
    "option_effect",
]
