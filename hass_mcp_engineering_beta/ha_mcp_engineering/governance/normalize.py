"""Deterministic, behavior-preserving automation normalization and diffing."""

from __future__ import annotations

from datetime import timedelta
import hashlib
import json
import re
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
# HAMCP-252: the Home Assistant ``sun`` condition's own fields
# (homeassistant/components/sun/condition.py, Core 2026.10.3).  Admitted only
# on a ``condition: sun`` step and compared exactly; the config view stores and
# returns them verbatim.
AUTOMATION_SUN_CONDITION_FIELDS = frozenset(
    {"after", "after_offset", "before", "before_offset"}
)
AUTOMATION_SUN_EVENTS = frozenset({"sunrise", "sunset"})
# HAMCP-252: the sun offsets the verifier admits, a reviewed subset of Core's
# cv.time_period (helpers/config_validation.py, identical in Core 2026.10.1
# and 2026.10.3): time_period_str "[+|-]H:M[:S[.f]]", time_period_seconds
# (a finite number or a decimal string) and time_period_dict (a nonempty
# mapping of these units to finite numbers or decimal strings).  Anything
# else, including forms Python's numeric parsing tolerates (whitespace,
# underscores, exponents, non-ASCII digits, booleans), is refused.
AUTOMATION_SUN_OFFSET_UNITS = frozenset(
    {"days", "hours", "milliseconds", "minutes", "seconds"}
)
_SUN_OFFSET_CLOCK = re.compile(
    r"[+-]?[0-9]+:[0-9]+(?::[0-9]+(?:\.[0-9]+)?)?", re.ASCII
)
_SUN_OFFSET_DECIMAL = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?", re.ASCII)

# HAMCP-252: a refusal's location, reported as bounded diagnostic codes.  The
# path names only structural keys of the walked action tree and list indices;
# a field is named only when it is in this fixed vocabulary of Home Assistant
# script-step and condition field names.  Reporting only: nothing here changes
# what the verifier accepts or refuses.
AUTOMATION_VERIFIER_PATH_PREFIX = "automation_verifier_path_"
# A refusal about the whole configuration is located at the document root
# (JSON pointer ""), never left without a location.
AUTOMATION_VERIFIER_PATH_ROOT = AUTOMATION_VERIFIER_PATH_PREFIX + "root"
AUTOMATION_VERIFIER_FIELD_PREFIX = "automation_verifier_field_"
AUTOMATION_VERIFIER_PATH_TRUNCATED = "automation_verifier_truncated_path"
AUTOMATION_VERIFIER_FIELDS_TRUNCATED = "automation_verifier_truncated_fields"
AUTOMATION_VERIFIER_UNRECOGNIZED_FIELD = "unrecognized_field"
AUTOMATION_VERIFIER_PATH_TOKENS = frozenset(
    {
        "action",
        "actions",
        "choose",
        "default",
        "else",
        "parallel",
        "repeat",
        "sequence",
        "then",
    }
)
AUTOMATION_VERIFIER_KNOWN_FIELDS = frozenset(
    {
        "above",
        "action",
        "actions",
        "after",
        "after_offset",
        "alias",
        "and",
        "area_id",
        "attribute",
        "before",
        "before_offset",
        "below",
        "choose",
        "condition",
        "conditions",
        "continue_on_error",
        "continue_on_timeout",
        "count",
        "data",
        "data_template",
        "default",
        "delay",
        "device_id",
        "domain",
        "else",
        "enabled",
        "entity_id",
        "error",
        "event",
        "event_data",
        "event_data_template",
        "floor_id",
        "for",
        "for_each",
        "id",
        "if",
        "label_id",
        "match",
        "metadata",
        "not",
        "options",
        "or",
        "parallel",
        "repeat",
        "response_variable",
        "scene",
        "sequence",
        "service",
        "set_conversation_response",
        "state",
        "stop",
        "subtype",
        "target",
        "then",
        "timeout",
        "trigger",
        "triggers",
        "type",
        "until",
        "value_template",
        "variables",
        "wait_for_trigger",
        "wait_template",
        "weekday",
        "while",
        "zone",
    }
)
MAX_AUTOMATION_VERIFIER_LOCATION_FIELDS = 4
MAX_AUTOMATION_VERIFIER_CODE_LENGTH = 96
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
        path: tuple[str | int, ...] | None = None,
        fields: Any = (),
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
        # HAMCP-252 (reporting only): where the refusal happened, as
        # structural path tokens, and the offending field names.
        self.path = path
        self.fields = tuple(sorted(str(field) for field in fields))

    def locate(self, path: tuple[str | int, ...]) -> None:
        """Record the innermost location; an outer step never overrides it."""

        if self.path is None:
            self.path = path


AUTOMATION_VERIFIER_DETAIL_PREFIX = "automation_verifier_"


def _verification_refusal(
    config: Any,
) -> AutomationVerificationNormalizationError | None:
    try:
        normalize_automation_for_verification(config)
    except AutomationVerificationNormalizationError as exc:
        return exc
    except (TypeError, ValueError):
        return None
    return None


def automation_verification_detail_code(config: Any) -> str | None:
    """HAMCP-249: name the verifier rule that refused ``config``.

    Reporting only.  Every refusal message in this module is a fixed literal,
    so the returned identifier comes from a closed set and never carries
    configuration content.  ``None`` when the verifier accepts ``config`` or
    refuses it through an exception without a reviewed message.
    """

    exc = _verification_refusal(config)
    if exc is None:
        return None
    slug = str(exc).replace("-", "_").replace(" ", "_")
    code = AUTOMATION_VERIFIER_DETAIL_PREFIX + slug
    if len(code) <= 96 and code.replace("_", "a").isalpha() and code.islower():
        return code
    return None


def _location_path_codes(path: Any) -> tuple[str, ...]:
    if not isinstance(path, tuple) or not path:
        return ()
    tokens: list[str] = []
    for token in path:
        if isinstance(token, bool):
            return ()
        if isinstance(token, int) and token >= 0:
            tokens.append(str(token))
        elif isinstance(token, str) and token in AUTOMATION_VERIFIER_PATH_TOKENS:
            tokens.append(token)
        else:
            return ()
    code = AUTOMATION_VERIFIER_PATH_PREFIX + tokens[0]
    if len(code) > MAX_AUTOMATION_VERIFIER_CODE_LENGTH:
        return ()
    for token in tokens[1:]:
        candidate = f"{code}_{token}"
        if len(candidate) > MAX_AUTOMATION_VERIFIER_CODE_LENGTH:
            return code, AUTOMATION_VERIFIER_PATH_TRUNCATED
        code = candidate
    return (code,)


def _location_field_codes(fields: Any) -> tuple[str, ...]:
    names = sorted(
        {
            field
            if field in AUTOMATION_VERIFIER_KNOWN_FIELDS
            else AUTOMATION_VERIFIER_UNRECOGNIZED_FIELD
            for field in fields
        }
    )
    codes = tuple(
        AUTOMATION_VERIFIER_FIELD_PREFIX + name
        for name in names[:MAX_AUTOMATION_VERIFIER_LOCATION_FIELDS]
    )
    if len(names) > MAX_AUTOMATION_VERIFIER_LOCATION_FIELDS:
        codes += (AUTOMATION_VERIFIER_FIELDS_TRUNCATED,)
    return codes


def automation_verification_location_codes(config: Any) -> tuple[str, ...]:
    """HAMCP-252: where the verifier refused ``config``, as bounded codes.

    Reporting only.  Exactly one path code naming structural keys and list
    indices of the action tree, or the document root for a refusal about the
    whole configuration, plus at most four field codes; a field outside the
    fixed Home Assistant vocabulary reads as ``unrecognized_field``.  No
    configuration value, template or free-form key text is ever emitted.
    Empty only when the verifier accepts ``config``.
    """

    exc = _verification_refusal(config)
    if exc is None:
        return ()
    path = _location_path_codes(exc.path) or (AUTOMATION_VERIFIER_PATH_ROOT,)
    return path + _location_field_codes(exc.fields)


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
            "unsupported automation service action fields",
            fields=set(value) - AUTOMATION_SERVICE_ACTION_FIELDS,
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
            "unsupported automation device action fields",
            fields=set(value) - AUTOMATION_DEVICE_ACTION_FIELDS,
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


def _sun_offset_amount(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) or (
        isinstance(value, str)
        and len(value) <= MAX_AUTOMATION_ACTION_NAME_LENGTH
        and _SUN_OFFSET_DECIMAL.fullmatch(value)
    ):
        # A non-finite amount fails when the caller builds the timedelta.
        return float(value)
    return None


def _valid_sun_offset(value: Any) -> bool:
    """HAMCP-252: whether a sun offset is a reviewed cv.time_period form.

    Checks only; the raw value stays significant to exact comparison.
    """

    try:
        if (
            isinstance(value, str)
            and len(value) <= MAX_AUTOMATION_ACTION_NAME_LENGTH
            and _SUN_OFFSET_CLOCK.fullmatch(value)
        ):
            parts = value.lstrip("+-").split(":")
            offset = timedelta(
                hours=int(parts[0]),
                minutes=int(parts[1]),
                seconds=float(parts[2]) if len(parts) == 3 else 0,
            )
            if value.startswith("-"):
                # Core's time_period_str negates the duration; timedelta's
                # negative bound is narrower, so the negation can overflow.
                offset = -offset
            return True
        if isinstance(value, dict):
            if not value or not set(value) <= AUTOMATION_SUN_OFFSET_UNITS:
                return False
            amounts = {
                unit: _sun_offset_amount(amount)
                for unit, amount in value.items()
            }
            if any(amount is None for amount in amounts.values()):
                return False
            timedelta(**amounts)
            return True
        seconds = _sun_offset_amount(value)
        if seconds is None:
            return False
        timedelta(seconds=seconds)
        return True
    except (OverflowError, ValueError):
        return False


def _validate_simple_action(
    value: dict[str, Any], family: str
) -> None:
    allowed = AUTOMATION_SIMPLE_ACTION_FIELDS[family]
    if family == "condition" and value[family] == "trigger":
        # A trigger condition's id is behavioral, unlike the top-level
        # automation id. Keep its exact scalar/list form, order and values;
        # this admits reviewed syntax without introducing coercion or a new
        # verification equivalence. Do not admit id on other condition types.
        allowed = AUTOMATION_ACTION_STEP_MODIFIERS | {"condition", "id"}
        trigger_ids = value.get("id")
        if not (
            isinstance(trigger_ids, str)
            or (
                isinstance(trigger_ids, list)
                and all(isinstance(item, str) for item in trigger_ids)
            )
        ):
            raise AutomationVerificationNormalizationError(
                "unsupported automation trigger condition ids"
            )
    if family == "condition" and value[family] == "sun":
        # HAMCP-252: Home Assistant accepts the sun condition's fields on a
        # condition step and returns them verbatim on readback, so they are
        # compared exactly.  Not admitted on any other condition type.
        allowed = allowed | AUTOMATION_SUN_CONDITION_FIELDS
    if not set(value).issubset(allowed):
        raise AutomationVerificationNormalizationError(
            "unsupported automation simple action fields",
            fields=set(value) - allowed,
        )
    if family == "condition" and value[family] == "sun":
        # Mirror Home Assistant's cv.sun_event and cv.time_period acceptance
        # without coercing: the raw value stays significant to comparison.
        for field in ("after", "before"):
            if field in value and not (
                isinstance(value[field], str)
                and value[field].lower() in AUTOMATION_SUN_EVENTS
            ):
                raise AutomationVerificationNormalizationError(
                    "invalid automation sun condition", fields=(field,)
                )
        offsets = AUTOMATION_SUN_CONDITION_FIELDS.intersection(value) - {
            "after",
            "before",
        }
        if offsets and not {"after", "before"}.intersection(value):
            # Core's AtLeastOne("before", "after"); a bare sun step stays
            # admitted as before HAMCP-252.
            raise AutomationVerificationNormalizationError(
                "invalid automation sun condition", fields=("after", "before")
            )
        for field in sorted(offsets):
            if not _valid_sun_offset(value[field]):
                raise AutomationVerificationNormalizationError(
                    "invalid automation sun condition offset",
                    fields=(field,),
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
    path: tuple[str | int, ...],
    categories: set[str],
    depth: int,
    allow_sequence_wrapper: bool = False,
) -> list[dict[str, Any]]:
    if depth > MAX_AUTOMATION_ACTION_DEPTH or not isinstance(value, list):
        raise AutomationVerificationNormalizationError(
            "unsupported automation action sequence", path=path
        )
    result: list[dict[str, Any]] = []
    for index, step in enumerate(value):
        step_path = (*path, index)
        if not isinstance(step, dict):
            raise AutomationVerificationNormalizationError(
                "unsupported automation action step", path=step_path
            )
        try:
            result.append(
                _verification_action_step(
                    step,
                    path=step_path,
                    categories=categories,
                    depth=depth,
                    allow_sequence_wrapper=allow_sequence_wrapper,
                )
            )
        except AutomationVerificationNormalizationError as exc:
            exc.locate(step_path)
            raise
    return result


def _verification_action_step(
    step: dict[str, Any],
    *,
    path: tuple[str | int, ...],
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
            fields=set(normalized) - AUTOMATION_ACTION_STEP_MODIFIERS,
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
        choose_fields = AUTOMATION_ACTION_STEP_MODIFIERS | {"choose", "default"}
        if (
            not isinstance(choose, list)
            or not set(normalized).issubset(choose_fields)
        ):
            raise AutomationVerificationNormalizationError(
                "unsupported automation choose action",
                fields=set(normalized) - choose_fields,
            )
        normalized_choices: list[dict[str, Any]] = []
        branch_fields = {"alias", "conditions", "sequence"}
        for index, choice in enumerate(choose):
            choice_path = (*path, "choose", index)
            if (
                not isinstance(choice, dict)
                or set(choice) - branch_fields
                or "conditions" not in choice
                or not isinstance(choice["conditions"], list)
                or "sequence" not in choice
            ):
                raise AutomationVerificationNormalizationError(
                    "unsupported automation choose branch",
                    path=choice_path,
                    fields=(
                        set(choice) - branch_fields
                        if isinstance(choice, dict)
                        else ()
                    ),
                )
            if "alias" in choice and not _bounded_nonempty_string(
                choice["alias"]
            ):
                raise AutomationVerificationNormalizationError(
                    "invalid automation choose branch alias",
                    path=choice_path,
                )
            normalized_choice = {
                key: _canonical(value) for key, value in choice.items()
            }
            normalized_choice["sequence"] = _verification_action_sequence(
                normalized_choice["sequence"],
                path=(*choice_path, "sequence"),
                categories=categories,
                depth=depth + 1,
            )
            normalized_choices.append(_canonical(normalized_choice))
        normalized["choose"] = normalized_choices
        if "default" in normalized:
            normalized["default"] = _verification_action_sequence(
                normalized["default"],
                path=(*path, "default"),
                categories=categories,
                depth=depth + 1,
            )

    if action_family == "if":
        if_fields = AUTOMATION_ACTION_STEP_MODIFIERS | {"if", "then", "else"}
        if (
            not set(normalized).issubset(if_fields)
            or not isinstance(normalized.get("if"), list)
            or "then" not in normalized
        ):
            raise AutomationVerificationNormalizationError(
                "unsupported automation conditional action",
                fields=set(normalized) - if_fields,
            )
        normalized["then"] = _verification_action_sequence(
            normalized["then"],
            path=(*path, "then"),
            categories=categories,
            depth=depth + 1,
        )
        if "else" in normalized:
            normalized["else"] = _verification_action_sequence(
                normalized["else"],
                path=(*path, "else"),
                categories=categories,
                depth=depth + 1,
            )

    repeat = normalized.get("repeat")
    if action_family == "repeat":
        repeat_step_fields = AUTOMATION_ACTION_STEP_MODIFIERS | {"repeat"}
        repeat_fields = {"count", "for_each", "sequence", "until", "while"}
        if (
            not set(normalized).issubset(repeat_step_fields)
            or not isinstance(repeat, dict)
            or set(repeat) - repeat_fields
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
            step_extra = set(normalized) - repeat_step_fields
            raise AutomationVerificationNormalizationError(
                "unsupported automation repeat action",
                path=(
                    (*path, "repeat")
                    if not step_extra and isinstance(repeat, dict)
                    else path
                ),
                fields=(
                    step_extra
                    or (set(repeat) - repeat_fields
                        if isinstance(repeat, dict) else ())
                ),
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
            path=(*path, "repeat", "sequence"),
            categories=categories,
            depth=depth + 1,
        )
        normalized["repeat"] = _canonical(normalized_repeat)

    if action_family == "parallel":
        parallel_fields = AUTOMATION_ACTION_STEP_MODIFIERS | {"parallel"}
        if (
            not set(normalized).issubset(parallel_fields)
            or not isinstance(normalized.get("parallel"), list)
            or not normalized["parallel"]
        ):
            raise AutomationVerificationNormalizationError(
                "unsupported automation parallel action",
                fields=set(normalized) - parallel_fields,
            )
        normalized["parallel"] = _verification_action_sequence(
            normalized["parallel"],
            path=(*path, "parallel"),
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
                "unsupported automation sequence action",
                fields=(
                    set(normalized) - {"alias", "sequence"}
                    if allow_sequence_wrapper
                    else ()
                ),
            )
        normalized["sequence"] = _verification_action_sequence(
            normalized["sequence"],
            path=(*path, "sequence"),
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
    and stale-state fingerprints. It proves candidate comparability before
    dispatch and compares readback after a configuration write (or while
    proving the already-written state). It never changes the dispatched payload.
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
                    "ambiguous top-level automation aliases",
                    fields=(alias, canonical),
                )
    normalized = normalize_automation(config)
    if normalized is None:
        return None, ()
    categories: set[str] = set()
    if "action" in normalized:
        # The location names the root key the caller actually wrote.
        root = "actions" if "actions" in config else "action"
        normalized["action"] = _verification_action_sequence(
            normalized["action"],
            path=(root,),
            categories=categories,
            depth=0,
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
