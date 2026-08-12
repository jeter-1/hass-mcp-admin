"""Deterministic, behavior-preserving automation normalization and diffing."""

from __future__ import annotations

import hashlib
import json
from typing import Any


ALIASES = {"triggers": "trigger", "conditions": "condition", "actions": "action"}
OPTIONAL_EMPTY = {"condition": [], "variables": {}, "trace": {}}
AUTOMATION_NORMALIZATION_VERSION = 3
AUTOMATION_VERIFICATION_NORMALIZATION_VERSION = 1
AUTOMATION_ACTION_SERVICE_ALIAS = "automation_action_service_alias"
AUTOMATION_VERIFICATION_STRUCTURE = "automation_verification_structure"
UNSUPPORTED_AUTOMATION_ACTION_FAMILY = (
    "unsupported_automation_action_family"
)
MAX_AUTOMATION_ACTION_NAME_LENGTH = 200
MAX_AUTOMATION_TEMPLATE_LENGTH = 60_000
MAX_AUTOMATION_ACTION_DEPTH = 4
AUTOMATION_ACTION_STEP_MODIFIERS = frozenset(
    {"alias", "continue_on_error", "enabled"}
)
AUTOMATION_SERVICE_ACTION_FIELDS = frozenset(
    {
        *AUTOMATION_ACTION_STEP_MODIFIERS,
        "action",
        "data",
        "data_template",
        "metadata",
        "response_variable",
        "service",
        "target",
    }
)
AUTOMATION_SIMPLE_ACTION_DIRECTIVES = frozenset(
    {
        "condition",
        "delay",
        "event",
        "scene",
        "set_conversation_response",
        "stop",
        "variables",
        "wait_for_trigger",
        "wait_template",
    }
)
AUTOMATION_DEVICE_ACTION_DISCRIMINATORS = frozenset(
    {"device_id", "domain", "type"}
)
AUTOMATION_DEVICE_ACTION_FIELDS = frozenset(
    {
        *AUTOMATION_ACTION_STEP_MODIFIERS,
        *AUTOMATION_DEVICE_ACTION_DISCRIMINATORS,
        "entity_id",
        "subtype",
    }
)
AUTOMATION_SIMPLE_ACTION_FIELDS = {
    "condition": frozenset(
        {
            *AUTOMATION_ACTION_STEP_MODIFIERS,
            "above",
            "attribute",
            "below",
            "condition",
            "conditions",
            "entity_id",
            "for",
            "state",
            "value_template",
        }
    ),
    "delay": frozenset({*AUTOMATION_ACTION_STEP_MODIFIERS, "delay"}),
    "event": frozenset(
        {
            *AUTOMATION_ACTION_STEP_MODIFIERS,
            "event",
            "event_data",
            "event_data_template",
        }
    ),
    "scene": frozenset({*AUTOMATION_ACTION_STEP_MODIFIERS, "scene"}),
    "set_conversation_response": frozenset(
        {
            *AUTOMATION_ACTION_STEP_MODIFIERS,
            "set_conversation_response",
        }
    ),
    "stop": frozenset(
        {
            *AUTOMATION_ACTION_STEP_MODIFIERS,
            "error",
            "response_variable",
            "stop",
        }
    ),
    "variables": frozenset(
        {*AUTOMATION_ACTION_STEP_MODIFIERS, "variables"}
    ),
    "wait_for_trigger": frozenset(
        {
            *AUTOMATION_ACTION_STEP_MODIFIERS,
            "continue_on_timeout",
            "timeout",
            "wait_for_trigger",
        }
    ),
    "wait_template": frozenset(
        {
            *AUTOMATION_ACTION_STEP_MODIFIERS,
            "continue_on_timeout",
            "timeout",
            "wait_template",
        }
    ),
}
DIFF_LABELS = {
    "alias": "alias",
    "description": "description",
    "mode": "mode",
    "max": "maximum_runs",
    "trigger": "triggers",
    "condition": "conditions",
    "action": "actions",
    "variables": "variables",
    "trace": "trace_settings",
    "use_blueprint": "blueprint_usage",
}


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def normalize_automation(
    config: dict[str, Any] | None,
    *,
    normalization_version: int | None = None,
) -> dict[str, Any] | None:
    if config is None:
        return None
    version = (
        AUTOMATION_NORMALIZATION_VERSION
        if normalization_version is None
        else normalization_version
    )
    if version not in {1, 2, AUTOMATION_NORMALIZATION_VERSION}:
        raise ValueError("unsupported automation normalization version")
    normalized: dict[str, Any] = {}
    for original_key, value in config.items():
        # Home Assistant injects the top-level automation id on read-back. It
        # identifies the resource addressed by the config endpoint; it is not
        # behavioral automation configuration and must not affect hashes or
        # behavioral diffs. Identity is verified explicitly by governance.
        if original_key == "id" or (
            original_key == "category"
            and version >= AUTOMATION_NORMALIZATION_VERSION
        ):
            continue
        key = ALIASES.get(original_key, original_key)
        if key in normalized and original_key != key:
            # Never silently discard an unknown/duplicate representation.
            normalized[original_key] = _canonical(value)
        else:
            normalized[key] = _canonical(value)
    for key, empty in OPTIONAL_EMPTY.items():
        if normalized.get(key) == empty:
            normalized.pop(key)
    return _canonical(normalized)


class AutomationVerificationNormalizationError(ValueError):
    """The automation readback cannot be compared under reviewed semantics."""

    def __init__(
        self,
        message: str,
        *,
        category: str = AUTOMATION_VERIFICATION_STRUCTURE,
    ) -> None:
        super().__init__(message)
        self.category = (
            category
            if category
            in {
                AUTOMATION_VERIFICATION_STRUCTURE,
                UNSUPPORTED_AUTOMATION_ACTION_FAMILY,
            }
            else AUTOMATION_VERIFICATION_STRUCTURE
        )


def _bounded_nonempty_string(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and value.strip()
        and value == value.strip()
        and len(value) <= MAX_AUTOMATION_ACTION_NAME_LENGTH
    )


def _nonempty_template_string(value: Any) -> bool:
    """Accept a valid template without treating it as an action name.

    Template content remains byte-for-byte significant to the normalized
    verification hash.  The dedicated fixed ceiling remains fail-closed while
    avoiding the unrelated action-name limit that rejected ordinary template
    source before exact comparison could occur.
    """

    return bool(
        isinstance(value, str)
        and value.strip()
        and len(value) <= MAX_AUTOMATION_TEMPLATE_LENGTH
    )


def _validate_action_modifiers(value: dict[str, Any]) -> None:
    if "alias" in value and not _bounded_nonempty_string(value["alias"]):
        raise AutomationVerificationNormalizationError(
            "invalid automation action alias label"
        )
    for field in ("continue_on_error", "enabled"):
        if field in value and not isinstance(value[field], bool):
            raise AutomationVerificationNormalizationError(
                "invalid automation action modifier"
            )


def _validate_mapping_field(value: dict[str, Any], field: str) -> None:
    if field in value and not isinstance(value[field], dict):
        raise AutomationVerificationNormalizationError(
            "invalid automation action mapping field"
        )


def _validate_service_action(value: dict[str, Any]) -> None:
    if not set(value).issubset(AUTOMATION_SERVICE_ACTION_FIELDS):
        raise AutomationVerificationNormalizationError(
            "unsupported automation service action fields"
        )
    for field in ("data", "data_template", "metadata", "target"):
        _validate_mapping_field(value, field)
    if "response_variable" in value and not _bounded_nonempty_string(
        value["response_variable"]
    ):
        raise AutomationVerificationNormalizationError(
            "invalid automation response variable"
        )


def _validate_device_action(value: dict[str, Any]) -> None:
    if (
        not set(value).issubset(AUTOMATION_DEVICE_ACTION_FIELDS)
        or not AUTOMATION_DEVICE_ACTION_DISCRIMINATORS.issubset(value)
    ):
        raise AutomationVerificationNormalizationError(
            "unsupported automation device action fields"
        )
    for field in AUTOMATION_DEVICE_ACTION_DISCRIMINATORS | {
        "entity_id",
        "subtype",
    }:
        if field in value and not _bounded_nonempty_string(value[field]):
            raise AutomationVerificationNormalizationError(
                "invalid automation device action field"
            )


def _valid_time_period(value: Any) -> bool:
    return (
        isinstance(value, (int, float, dict))
        and not isinstance(value, bool)
    ) or _bounded_nonempty_string(value)


def _validate_simple_action(
    value: dict[str, Any], family: str
) -> None:
    allowed = AUTOMATION_SIMPLE_ACTION_FIELDS[family]
    if not set(value).issubset(allowed):
        raise AutomationVerificationNormalizationError(
            "unsupported automation simple action fields"
        )
    primary = value[family]
    if family in {
        "condition",
        "event",
        "scene",
        "set_conversation_response",
        "stop",
    } and not _bounded_nonempty_string(primary):
        raise AutomationVerificationNormalizationError(
            "invalid automation simple action value"
        )
    if family == "wait_template" and not _nonempty_template_string(primary):
        raise AutomationVerificationNormalizationError(
            "invalid automation wait template"
        )
    if family == "delay" and not _valid_time_period(primary):
        raise AutomationVerificationNormalizationError(
            "invalid automation delay action"
        )
    if family == "variables" and not isinstance(primary, dict):
        raise AutomationVerificationNormalizationError(
            "invalid automation variables action"
        )
    if family == "wait_for_trigger" and not isinstance(primary, list):
        raise AutomationVerificationNormalizationError(
            "invalid automation wait trigger action"
        )
    for field in ("event_data", "event_data_template"):
        _validate_mapping_field(value, field)
    if "timeout" in value and not _valid_time_period(value["timeout"]):
        raise AutomationVerificationNormalizationError(
            "invalid automation action timeout"
        )
    for field in ("continue_on_timeout", "error"):
        if field in value and not isinstance(value[field], bool):
            raise AutomationVerificationNormalizationError(
                "invalid automation simple action option"
            )
    if "response_variable" in value and not _bounded_nonempty_string(
        value["response_variable"]
    ):
        raise AutomationVerificationNormalizationError(
            "invalid automation response variable"
        )
    if "conditions" in value and not isinstance(value["conditions"], list):
        raise AutomationVerificationNormalizationError(
            "invalid automation condition collection"
        )


def _verification_action_sequence(
    value: Any,
    *,
    categories: set[str],
    depth: int,
    allow_sequence_wrapper: bool = False,
) -> list[dict[str, Any]]:
    if depth > MAX_AUTOMATION_ACTION_DEPTH or not isinstance(value, list):
        raise AutomationVerificationNormalizationError(
            "unsupported automation action sequence"
        )
    result: list[dict[str, Any]] = []
    for step in value:
        if not isinstance(step, dict):
            raise AutomationVerificationNormalizationError(
                "unsupported automation action step"
            )
        result.append(
            _verification_action_step(
                step,
                categories=categories,
                depth=depth,
                allow_sequence_wrapper=allow_sequence_wrapper,
            )
        )
    return result


def _verification_action_step(
    step: dict[str, Any],
    *,
    categories: set[str],
    depth: int,
    allow_sequence_wrapper: bool,
) -> dict[str, Any]:
    normalized = {key: _canonical(value) for key, value in step.items()}
    _validate_action_modifiers(normalized)
    has_service = "service" in normalized
    has_action = "action" in normalized
    if has_service and has_action:
        raise AutomationVerificationNormalizationError(
            "ambiguous automation action alias"
        )
    action_families = [
        name
        for name, present in (
            ("call", has_service or has_action),
            ("choose", "choose" in normalized),
            ("if", "if" in normalized),
            ("repeat", "repeat" in normalized),
            ("parallel", "parallel" in normalized),
            ("sequence", "sequence" in normalized),
        )
        if present
    ]
    action_families.extend(
        sorted(
            key
            for key in AUTOMATION_SIMPLE_ACTION_DIRECTIVES
            if key in normalized
        )
    )
    if AUTOMATION_DEVICE_ACTION_DISCRIMINATORS.intersection(normalized):
        action_families.append("device")
    if len(action_families) > 1:
        raise AutomationVerificationNormalizationError(
            "ambiguous automation action family"
        )

    if not action_families:
        raise AutomationVerificationNormalizationError(
            "unsupported automation action family",
            category=UNSUPPORTED_AUTOMATION_ACTION_FAMILY,
        )
    action_family = action_families[0]
    if has_service or has_action:
        _validate_service_action(normalized)
        alias_key = "service" if has_service else "action"
        action_name = normalized[alias_key]
        if (
            not isinstance(action_name, str)
            or not action_name.strip()
            or action_name != action_name.strip()
            or len(action_name) > MAX_AUTOMATION_ACTION_NAME_LENGTH
        ):
            raise AutomationVerificationNormalizationError(
                "invalid automation action alias"
            )
        if has_service:
            normalized.pop("service")
            normalized["action"] = action_name
            categories.add(AUTOMATION_ACTION_SERVICE_ALIAS)

    if action_family == "device":
        _validate_device_action(normalized)

    if action_family in AUTOMATION_SIMPLE_ACTION_DIRECTIVES:
        _validate_simple_action(normalized, action_family)

    if "default" in normalized and action_family != "choose":
        raise AutomationVerificationNormalizationError(
            "orphan automation default action"
        )
    if (
        "then" in normalized or "else" in normalized
    ) and action_family != "if":
        raise AutomationVerificationNormalizationError(
            "orphan automation conditional action"
        )

    choose = normalized.get("choose")
    if action_family == "choose":
        if (
            not isinstance(choose, list)
            or not set(normalized).issubset(
                AUTOMATION_ACTION_STEP_MODIFIERS | {"choose", "default"}
            )
        ):
            raise AutomationVerificationNormalizationError(
                "unsupported automation choose action"
            )
        normalized_choices: list[dict[str, Any]] = []
        for choice in choose:
            if (
                not isinstance(choice, dict)
                or set(choice) - {"alias", "conditions", "sequence"}
                or "conditions" not in choice
                or not isinstance(choice["conditions"], list)
                or "sequence" not in choice
            ):
                raise AutomationVerificationNormalizationError(
                    "unsupported automation choose branch"
                )
            if "alias" in choice and not _bounded_nonempty_string(
                choice["alias"]
            ):
                raise AutomationVerificationNormalizationError(
                    "invalid automation choose branch alias"
                )
            normalized_choice = {
                key: _canonical(value) for key, value in choice.items()
            }
            normalized_choice["sequence"] = _verification_action_sequence(
                normalized_choice["sequence"],
                categories=categories,
                depth=depth + 1,
            )
            normalized_choices.append(_canonical(normalized_choice))
        normalized["choose"] = normalized_choices
        if "default" in normalized:
            normalized["default"] = _verification_action_sequence(
                normalized["default"],
                categories=categories,
                depth=depth + 1,
            )

    if action_family == "if":
        if (
            not set(normalized).issubset(
                AUTOMATION_ACTION_STEP_MODIFIERS
                | {"if", "then", "else"}
            )
            or not isinstance(normalized.get("if"), list)
            or "then" not in normalized
        ):
            raise AutomationVerificationNormalizationError(
                "unsupported automation conditional action"
            )
        normalized["then"] = _verification_action_sequence(
            normalized["then"],
            categories=categories,
            depth=depth + 1,
        )
        if "else" in normalized:
            normalized["else"] = _verification_action_sequence(
                normalized["else"],
                categories=categories,
                depth=depth + 1,
            )

    repeat = normalized.get("repeat")
    if action_family == "repeat":
        if (
            not set(normalized).issubset(
                AUTOMATION_ACTION_STEP_MODIFIERS | {"repeat"}
            )
            or not isinstance(repeat, dict)
            or set(repeat)
            - {"count", "for_each", "sequence", "until", "while"}
            or "sequence" not in repeat
            or len(
                {"count", "for_each", "until", "while"}.intersection(
                    repeat
                )
            )
            != 1
            or (
                ("until" in repeat or "while" in repeat)
                and not isinstance(
                    repeat.get("until", repeat.get("while")), list
                )
            )
        ):
            raise AutomationVerificationNormalizationError(
                "unsupported automation repeat action"
            )
        for selector in ("count", "for_each"):
            if selector in repeat:
                selected = repeat[selector]
                if selector == "count":
                    valid_count = (
                        isinstance(selected, int)
                        and not isinstance(selected, bool)
                        and selected > 0
                    ) or _bounded_nonempty_string(selected)
                    if not valid_count:
                        raise AutomationVerificationNormalizationError(
                            "invalid automation repeat count"
                        )
                if selector == "for_each":
                    valid_collection = isinstance(
                        selected, (list, dict)
                    ) or _bounded_nonempty_string(selected)
                    if not valid_collection:
                        raise AutomationVerificationNormalizationError(
                            "invalid automation repeat collection"
                        )
        normalized_repeat = {
            key: _canonical(value) for key, value in repeat.items()
        }
        normalized_repeat["sequence"] = _verification_action_sequence(
            normalized_repeat["sequence"],
            categories=categories,
            depth=depth + 1,
        )
        normalized["repeat"] = _canonical(normalized_repeat)

    if action_family == "parallel":
        if (
            not set(normalized).issubset(
                AUTOMATION_ACTION_STEP_MODIFIERS | {"parallel"}
            )
            or not isinstance(normalized.get("parallel"), list)
            or not normalized["parallel"]
        ):
            raise AutomationVerificationNormalizationError(
                "unsupported automation parallel action"
            )
        normalized["parallel"] = _verification_action_sequence(
            normalized["parallel"],
            categories=categories,
            depth=depth + 1,
            allow_sequence_wrapper=True,
        )
    if action_family == "sequence":
        if (
            not allow_sequence_wrapper
            or set(normalized) - {"alias", "sequence"}
        ):
            raise AutomationVerificationNormalizationError(
                "unsupported automation sequence action"
            )
        normalized["sequence"] = _verification_action_sequence(
            normalized["sequence"],
            categories=categories,
            depth=depth + 1,
        )
    return _canonical(normalized)


def _verification_keys_are_strings(value: Any) -> bool:
    if isinstance(value, dict):
        return all(
            isinstance(key, str)
            and _verification_keys_are_strings(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return all(_verification_keys_are_strings(item) for item in value)
    return True


def normalize_automation_for_verification(
    config: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    """Normalize only reviewed Home Assistant readback equivalences.

    This representation is deliberately separate from immutable plan binding
    and stale-state fingerprints. It is used only after a configuration write
    (or while proving the already-written state) and never changes the payload
    dispatched to Home Assistant.
    """

    if config is not None:
        if not isinstance(config, dict) or not _verification_keys_are_strings(
            config
        ):
            raise AutomationVerificationNormalizationError(
                "unsupported automation mapping keys"
            )
        for alias, canonical in ALIASES.items():
            if alias in config and canonical in config:
                raise AutomationVerificationNormalizationError(
                    "ambiguous top-level automation aliases"
                )
    normalized = normalize_automation(config)
    if normalized is None:
        return None, ()
    categories: set[str] = set()
    if "action" in normalized:
        normalized["action"] = _verification_action_sequence(
            normalized["action"], categories=categories, depth=0
        )
    return _canonical(normalized), tuple(sorted(categories))


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def state_fingerprint(config: dict[str, Any] | None) -> str:
    return stable_hash(normalize_automation(config))


def _summary(value: Any) -> Any:
    if isinstance(value, list):
        return {"type": "list", "count": len(value)}
    if isinstance(value, dict):
        return {"type": "object", "keys": sorted(value)[:20], "key_count": len(value)}
    if isinstance(value, str):
        return value[:160] + ("..." if len(value) > 160 else "")
    return value


def structured_diff(
    current: dict[str, Any] | None, proposed: dict[str, Any]
) -> dict[str, Any]:
    before = normalize_automation(current) or {}
    after = normalize_automation(proposed) or {}
    changed, unchanged = [], []
    for key in sorted(set(before) | set(after)):
        label = DIFF_LABELS.get(key, f"other:{key}")
        if before.get(key) == after.get(key):
            unchanged.append(label)
            continue
        change_type = "added" if key not in before else "removed" if key not in after else "modified"
        changed.append(
            {
                "field": label,
                "change_type": change_type,
                "before": _summary(before.get(key)),
                "after": _summary(after.get(key)),
            }
        )
    return {
        "has_changes": bool(changed),
        "changed_fields": changed,
        "unchanged_fields": unchanged,
        "meaningful_change_count": len(changed),
    }
