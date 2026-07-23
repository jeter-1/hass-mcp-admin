"""Fixed Home Assistant configuration-resource adapters for governed changes.

This module deliberately exposes resource operations rather than transport
paths or WebSocket command names.  Callers cannot use it as a general Home
Assistant write transport.
"""

from __future__ import annotations

import math
import re
from typing import Any

from ..clients.rest import ExpectedHttpStatus, HomeAssistantRestClient
from ..clients.websocket import HomeAssistantWebSocketClient
from ..errors import HomeAssistantApiError
from ..sanitization import sanitize_untrusted_data
from .normalize import normalize_automation, stable_hash, structured_diff
from .validation import validate_automation


RESOURCE_NORMALIZATION_VERSION = 1
SUPPORTED_RESOURCE_TYPES = frozenset(
    {"automation", "script", "input_boolean", "input_number"}
)
SUPPORTED_HELPER_TYPES = frozenset({"input_boolean", "input_number"})

_SCRIPT_ID_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyz0123456789_"
)
_HELPER_OBJECT_ID_CHARACTERS = _SCRIPT_ID_CHARACTERS
_SCRIPT_FIELDS = frozenset(
    {
        "alias",
        "description",
        "fields",
        "icon",
        "max",
        "max_exceeded",
        "mode",
        "sequence",
        "trace",
        "variables",
    }
)
_INPUT_BOOLEAN_FIELDS = frozenset({"icon", "initial", "name"})
_INPUT_NUMBER_FIELDS = frozenset(
    {
        "icon",
        "initial",
        "max",
        "min",
        "mode",
        "name",
        "step",
        "unit_of_measurement",
    }
)
_SCRIPT_MODES = frozenset({"single", "restart", "queued", "parallel"})
_RESERVED_SCRIPT_IDS = frozenset({"reload", "toggle", "turn_off", "turn_on"})
_LOG_LEVELS = frozenset(
    {"debug", "info", "warning", "error", "critical", "silent"}
)
_INPUT_NUMBER_MODES = frozenset({"box", "slider"})
_CONFIG_ENDPOINTS = {
    "automation": "/config/automation/config",
    "script": "/config/script/config",
}
_HELPER_ID_FIELDS = {
    "input_boolean": "input_boolean_id",
    "input_number": "input_number_id",
}
_SAFE_HELPER_CREATE_NAME = re.compile(
    r"[A-Za-z0-9]+(?:[ _-]+[A-Za-z0-9]+)*"
)


class ConfigurationMutationNotDispatchedError(HomeAssistantApiError):
    """A bounded resource adapter proved that no mutation was dispatched."""

    mutation_dispatched = False
    mutation_completed = False


class ConfigurationMutationCompletedUnexpectedlyError(
    HomeAssistantApiError
):
    """A bounded resource adapter proved an unintended mutation completed."""

    mutation_dispatched = True
    mutation_completed = True


def validate_resource(
    resource_type: str,
    resource_id: str,
    proposed_config: Any,
    sensitive_values: tuple[str, ...] = (),
) -> tuple[bool, list[str], list[str]]:
    """Validate one supported resource without performing I/O."""

    if resource_type not in SUPPORTED_RESOURCE_TYPES:
        return (
            False,
            ["resource_type is not supported for governed configuration writes"],
            [],
        )

    if resource_type == "automation":
        valid, errors, warnings = validate_automation(
            resource_id, proposed_config
        )
    else:
        errors = _validate_resource_id(resource_type, resource_id)
        warnings: list[str] = []
        if not isinstance(proposed_config, dict):
            errors.append("proposed_config must be an object")
            return False, errors, warnings
        if not _mapping_keys_are_strings(proposed_config):
            errors.append("configuration keys must be strings")
        if resource_type == "script":
            errors.extend(_validate_script(proposed_config))
        elif resource_type == "input_boolean":
            errors.extend(_validate_input_boolean(proposed_config))
        else:
            errors.extend(_validate_input_number(proposed_config))

    if isinstance(proposed_config, dict):
        errors.extend(
            persistence_safety_errors(proposed_config, sensitive_values)
        )
    return not errors, _deduplicate(errors), warnings


def validate_resource_create_identity(
    resource_type: str,
    resource_id: str,
    proposed_config: Any,
) -> list[str]:
    """Fail closed when Home Assistant cannot generate the approved helper ID.

    Home Assistant's storage-helper create commands derive an object ID from
    ``name`` and do not accept an explicit ID.  Dev14 therefore permits only a
    conservative ASCII subset whose generated object ID is deterministic and
    must exactly equal the approved target.
    """

    if resource_type not in SUPPORTED_HELPER_TYPES:
        return []
    try:
        _, object_id = _split_helper_entity_id(resource_type, resource_id)
    except ValueError as exc:
        return [str(exc)]
    if not isinstance(proposed_config, dict):
        return ["helper create configuration must be an object"]
    name = proposed_config.get("name")
    if not isinstance(name, str) or not _SAFE_HELPER_CREATE_NAME.fullmatch(
        name
    ):
        return [
            "helper create name must use only ASCII letters, numbers, "
            "spaces, hyphens, or underscores and cannot start or end with "
            "a separator"
        ]
    generated_object_id = re.sub(r"[ _-]+", "_", name).lower()
    if generated_object_id != object_id:
        return [
            "helper create target_id must exactly match the deterministic "
            "object ID derived from name"
        ]
    return []


def normalize_resource_config(
    resource_type: str, config: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Return the deterministic configuration representation used for binding."""

    _require_supported_type(resource_type)
    if config is None:
        return None
    if not isinstance(config, dict):
        raise TypeError("resource configuration must be an object or null")
    if resource_type == "automation":
        return normalize_automation(config)
    normalized = {
        key: value for key, value in config.items() if key != "id"
    }
    if resource_type == "input_number":
        for key in ("min", "max", "step", "initial"):
            if key in normalized and _is_number(normalized[key]):
                normalized[key] = float(normalized[key])
    return _canonical(normalized)


def resource_fingerprint(
    resource_type: str, config: dict[str, Any] | None
) -> str:
    """Hash an exact normalized resource state, including absence."""

    return stable_hash(normalize_resource_config(resource_type, config))


def structured_resource_diff(
    resource_type: str,
    current: dict[str, Any] | None,
    proposed: dict[str, Any],
) -> dict[str, Any]:
    """Describe a deterministic top-level resource configuration difference."""

    _require_supported_type(resource_type)
    if resource_type == "automation":
        return structured_diff(current, proposed)

    before = normalize_resource_config(resource_type, current) or {}
    after = normalize_resource_config(resource_type, proposed) or {}
    changed: list[dict[str, Any]] = []
    unchanged: list[str] = []
    for key in sorted(set(before) | set(after)):
        if before.get(key) == after.get(key):
            unchanged.append(key)
            continue
        change_type = (
            "added"
            if key not in before
            else "removed"
            if key not in after
            else "modified"
        )
        changed.append(
            {
                "field": key,
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


def resource_identity_matches(
    resource_type: str,
    resource_id: str,
    config: dict[str, Any] | None,
) -> bool:
    """Verify response identity separately from behavioral normalization."""

    if resource_type not in SUPPORTED_RESOURCE_TYPES or not isinstance(
        config, dict
    ):
        return False
    if _validate_resource_id(resource_type, resource_id):
        return False
    returned_id = config.get("id")
    if resource_type in SUPPORTED_HELPER_TYPES:
        _, object_id = _split_helper_entity_id(resource_type, resource_id)
        return returned_id == object_id
    return returned_id is None or str(returned_id) == resource_id


class ConfigurationResourceGateway:
    """Bounded REST and WebSocket adapter for the Dev14 resource allowlist."""

    def __init__(
        self,
        rest_client: HomeAssistantRestClient,
        websocket_client: HomeAssistantWebSocketClient,
    ):
        self.rest_client = rest_client
        self.websocket_client = websocket_client

    async def get(
        self, resource_type: str, resource_id: str
    ) -> dict[str, Any] | None:
        """Read one exact supported resource."""

        self._require_valid_id(resource_type, resource_id)
        if resource_type in _CONFIG_ENDPOINTS:
            value = await self.rest_client.request(
                "GET",
                f"{_CONFIG_ENDPOINTS[resource_type]}/{resource_id}",
                expected_statuses=frozenset({404}),
            )
            if isinstance(value, ExpectedHttpStatus) and value.status == 404:
                return None
            self._require_response_object(
                value, f"{resource_type}_config_read", resource_id
            )
            if not resource_identity_matches(
                resource_type, resource_id, value
            ):
                self._raise_malformed(
                    f"{resource_type}_config_read",
                    resource_id,
                    "identity_mismatch",
                )
            return value

        _, object_id = _split_helper_entity_id(
            resource_type, resource_id
        )
        value = await self.websocket_client.command(
            {"type": f"{resource_type}/list"}
        )
        if not isinstance(value, list):
            self._raise_malformed(
                f"{resource_type}_config_read",
                resource_id,
                "malformed_collection",
            )
        matches = [
            item
            for item in value
            if isinstance(item, dict) and item.get("id") == object_id
        ]
        if not matches:
            return None
        if len(matches) != 1:
            self._raise_malformed(
                f"{resource_type}_config_read",
                resource_id,
                "duplicate_identity",
            )
        if not resource_identity_matches(
            resource_type, resource_id, matches[0]
        ):
            self._raise_malformed(
                f"{resource_type}_config_read",
                resource_id,
                "identity_mismatch",
            )
        return matches[0]

    async def read(
        self, resource_type: str, resource_id: str
    ) -> dict[str, Any] | None:
        """Compact service-facing alias for an exact resource read."""

        return await self.get(resource_type, resource_id)

    async def create(
        self,
        resource_type: str,
        resource_id: str,
        approved_config: dict[str, Any],
    ) -> Any:
        """Create one resource using only its fixed Home Assistant operation."""

        self._require_valid(resource_type, resource_id, approved_config)
        create_identity_errors = validate_resource_create_identity(
            resource_type, resource_id, approved_config
        )
        if create_identity_errors:
            raise ValueError("; ".join(create_identity_errors))
        if resource_type in _CONFIG_ENDPOINTS:
            return await self.rest_client.request(
                "POST",
                f"{_CONFIG_ENDPOINTS[resource_type]}/{resource_id}",
                body=approved_config,
            )

        _, object_id = _split_helper_entity_id(
            resource_type, resource_id
        )
        # Storage-helper create commands generate an ID instead of accepting
        # one.  Recheck both the storage collection and the entity-state
        # namespace immediately before mutation so known storage and YAML
        # collisions stop without creating a suffixed helper.
        try:
            existing = await self.get(resource_type, resource_id)
        except Exception as exc:
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": f"{resource_type}_config_create",
                    "resource_id": resource_id,
                    "reason": "helper_create_preflight_unavailable",
                }
            ) from exc
        if existing is not None:
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": f"{resource_type}_config_create",
                    "resource_id": resource_id,
                    "reason": "target_already_exists",
                }
            )
        try:
            entity_state = await self.rest_client.request(
                "GET",
                f"/states/{resource_id}",
                expected_statuses=frozenset({404}),
            )
        except Exception as exc:
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": f"{resource_type}_config_create",
                    "resource_id": resource_id,
                    "reason": "helper_create_preflight_unavailable",
                }
            ) from exc
        if not (
            isinstance(entity_state, ExpectedHttpStatus)
            and entity_state.status == 404
        ):
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": f"{resource_type}_config_create",
                    "resource_id": resource_id,
                    "reason": "target_entity_id_reserved",
                }
            )
        result = await self.websocket_client.command(
            {"type": f"{resource_type}/create", **approved_config}
        )
        self._require_response_object(
            result, f"{resource_type}_config_create", resource_id
        )
        if result.get("id") != object_id:
            unexpected_id = result.get("id")
            if (
                isinstance(unexpected_id, str)
                and 0 < len(unexpected_id) <= 128
                and all(
                    character in _HELPER_OBJECT_ID_CHARACTERS
                    for character in unexpected_id
                )
            ):
                raise ConfigurationMutationCompletedUnexpectedlyError(
                    details={
                        "operation": f"{resource_type}_config_create",
                        "resource_id": resource_id,
                        "reason": "generated_identity_mismatch",
                        "unexpected_resource_id": (
                            f"{resource_type}.{unexpected_id}"
                        ),
                        "orphan_risk": True,
                    }
                )
            raise HomeAssistantApiError(
                details={
                    "operation": f"{resource_type}_config_create",
                    "resource_id": resource_id,
                    "reason": "generated_identity_mismatch",
                    "unexpected_resource_id": (
                        f"{resource_type}.{unexpected_id}"
                        if isinstance(unexpected_id, str)
                        else "unknown"
                    ),
                    "orphan_risk": True,
                }
            )
        return result

    async def update(
        self,
        resource_type: str,
        resource_id: str,
        approved_config: dict[str, Any],
    ) -> Any:
        """Replace one resource with the exact approved configuration."""

        self._require_valid(resource_type, resource_id, approved_config)
        if resource_type in _CONFIG_ENDPOINTS:
            return await self.rest_client.request(
                "POST",
                f"{_CONFIG_ENDPOINTS[resource_type]}/{resource_id}",
                body=approved_config,
            )

        _, object_id = _split_helper_entity_id(
            resource_type, resource_id
        )
        id_field = _HELPER_ID_FIELDS[resource_type]
        result = await self.websocket_client.command(
            {
                "type": f"{resource_type}/update",
                id_field: object_id,
                **approved_config,
            }
        )
        self._require_response_object(
            result, f"{resource_type}_config_update", resource_id
        )
        if result.get("id") != object_id:
            self._raise_malformed(
                f"{resource_type}_config_update",
                resource_id,
                "identity_mismatch",
            )
        return result

    async def write(
        self,
        action: str,
        resource_type: str,
        resource_id: str,
        approved_config: dict[str, Any],
    ) -> Any:
        """Compact service-facing mutation with a closed action vocabulary."""

        if action == "create":
            return await self.create(
                resource_type, resource_id, approved_config
            )
        if action == "update":
            return await self.update(
                resource_type, resource_id, approved_config
            )
        raise ValueError("action must be create or update")

    async def validate(self) -> Any:
        """Run Home Assistant's bounded full configuration check."""

        return await self.rest_client.request(
            "POST", "/config/core/check_config"
        )

    async def validate_all(self) -> Any:
        """Compact service-facing alias for the full configuration check."""

        return await self.validate()

    @staticmethod
    def _require_response_object(
        value: Any, operation: str, resource_id: str
    ) -> None:
        if not isinstance(value, dict):
            ConfigurationResourceGateway._raise_malformed(
                operation, resource_id, "malformed_response"
            )

    @staticmethod
    def _raise_malformed(
        operation: str, resource_id: str, reason: str
    ) -> None:
        raise HomeAssistantApiError(
            details={
                "operation": operation,
                "resource_id": resource_id,
                "reason": reason,
            }
        )

    @staticmethod
    def _require_valid_id(resource_type: str, resource_id: str) -> None:
        errors = _validate_resource_id(resource_type, resource_id)
        if errors:
            raise ValueError("; ".join(errors))

    @staticmethod
    def _require_valid(
        resource_type: str,
        resource_id: str,
        config: Any,
    ) -> None:
        valid, errors, _ = validate_resource(
            resource_type, resource_id, config
        )
        if not valid:
            raise ValueError("; ".join(errors))


def _require_supported_type(resource_type: str) -> None:
    if resource_type not in SUPPORTED_RESOURCE_TYPES:
        raise ValueError(
            "resource_type is not supported for governed configuration writes"
        )


def _validate_resource_id(
    resource_type: str, resource_id: Any
) -> list[str]:
    if resource_type not in SUPPORTED_RESOURCE_TYPES:
        return [
            "resource_type is not supported for governed configuration writes"
        ]
    if resource_type == "automation":
        valid, errors, _ = validate_automation(
            resource_id, {"trigger": [], "action": []}
        )
        return [
            error
            for error in errors
            if error.startswith("automation_id ")
        ]
    if not isinstance(resource_id, str):
        return ["resource_id must be a string"]
    if resource_type == "script":
        if not _bounded_identifier(
            resource_id, _SCRIPT_ID_CHARACTERS
        ):
            return [
                "script resource_id must contain only lowercase letters, "
                "numbers, or underscores"
            ]
        if resource_id in _RESERVED_SCRIPT_IDS:
            return ["script resource_id is reserved by Home Assistant"]
        return []
    try:
        _split_helper_entity_id(resource_type, resource_id)
    except ValueError as exc:
        return [str(exc)]
    return []


def _split_helper_entity_id(
    helper_type: str, entity_id: str
) -> tuple[str, str]:
    if helper_type not in SUPPORTED_HELPER_TYPES:
        raise ValueError(
            "helper type is not supported for governed configuration writes"
        )
    if not isinstance(entity_id, str):
        raise ValueError("helper resource_id must be a full entity ID")
    domain, separator, object_id = entity_id.partition(".")
    if (
        separator != "."
        or domain != helper_type
        or not _bounded_identifier(
            object_id, _HELPER_OBJECT_ID_CHARACTERS
        )
    ):
        raise ValueError(
            f"{helper_type} resource_id must be a full {helper_type} entity ID"
        )
    return domain, object_id


def _bounded_identifier(
    value: str, allowed_characters: frozenset[str]
) -> bool:
    return bool(
        value
        and len(value) <= 128
        and all(character in allowed_characters for character in value)
    )


def _validate_script(config: dict[str, Any]) -> list[str]:
    errors = _unknown_field_errors(config, _SCRIPT_FIELDS, "script")
    sequence = config.get("sequence")
    if (
        not isinstance(sequence, list)
        or not sequence
        or any(not isinstance(action, dict) for action in sequence)
    ):
        errors.append("script sequence must be a non-empty list of actions")
    mode = config.get("mode", "single")
    if mode not in _SCRIPT_MODES:
        errors.append(
            "script mode must be single, restart, queued, or parallel"
        )
    if "max" in config:
        maximum = config["max"]
        if (
            not _is_number(maximum)
            or not float(maximum).is_integer()
            or not 1 <= maximum <= 1000
        ):
            errors.append("script max must be an integer from 1 to 1000")
        if mode not in {"queued", "parallel"}:
            errors.append(
                "script max is supported only for queued or parallel mode"
            )
    if (
        "max_exceeded" in config
        and config["max_exceeded"] not in _LOG_LEVELS
    ):
        errors.append("script max_exceeded must be a supported log level")
    for field in ("fields", "variables"):
        if field in config and not isinstance(config[field], dict):
            errors.append(f"script {field} must be an object")
    if "trace" in config and not isinstance(config["trace"], dict):
        errors.append("script trace must be an object")
    errors.extend(_validate_optional_text(config, "alias", "script", 255))
    errors.extend(
        _validate_optional_text(config, "description", "script", 4096)
    )
    errors.extend(_validate_optional_icon(config, "script"))
    return errors


def _validate_input_boolean(config: dict[str, Any]) -> list[str]:
    errors = _unknown_field_errors(
        config, _INPUT_BOOLEAN_FIELDS, "input_boolean"
    )
    errors.extend(_validate_required_name(config, "input_boolean"))
    if "initial" in config and not isinstance(config["initial"], bool):
        errors.append("input_boolean initial must be a boolean")
    errors.extend(_validate_optional_icon(config, "input_boolean"))
    return errors


def _validate_input_number(config: dict[str, Any]) -> list[str]:
    errors = _unknown_field_errors(
        config, _INPUT_NUMBER_FIELDS, "input_number"
    )
    errors.extend(_validate_required_name(config, "input_number"))
    for field in ("min", "max", "step"):
        if field not in config or not _is_number(config.get(field)):
            errors.append(f"input_number {field} must be a finite number")
    minimum = config.get("min")
    maximum = config.get("max")
    step = config.get("step")
    if _is_number(minimum) and _is_number(maximum) and minimum >= maximum:
        errors.append("input_number min must be less than max")
    if _is_number(step) and step <= 0:
        errors.append("input_number step must be greater than zero")
    if (
        _is_number(minimum)
        and _is_number(maximum)
        and _is_number(step)
        and step > maximum - minimum
    ):
        errors.append("input_number step must not exceed its range")
    if config.get("mode") not in _INPUT_NUMBER_MODES:
        errors.append("input_number mode must be box or slider")
    if "initial" in config:
        initial = config["initial"]
        if not _is_number(initial):
            errors.append("input_number initial must be a finite number")
        elif (
            _is_number(minimum)
            and _is_number(maximum)
            and not minimum <= initial <= maximum
        ):
            errors.append("input_number initial must be within min and max")
    errors.extend(_validate_optional_icon(config, "input_number"))
    errors.extend(
        _validate_optional_text(
            config,
            "unit_of_measurement",
            "input_number",
            255,
        )
    )
    return errors


def _validate_required_name(
    config: dict[str, Any], resource_type: str
) -> list[str]:
    value = config.get("name")
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 255
    ):
        return [f"{resource_type} name must be a non-empty string"]
    return []


def _validate_optional_icon(
    config: dict[str, Any], resource_type: str
) -> list[str]:
    if "icon" not in config:
        return []
    value = config["icon"]
    if (
        not isinstance(value, str)
        or ":" not in value
        or len(value) > 255
    ):
        return [
            f"{resource_type} icon must be a bounded prefix:name string"
        ]
    return []


def _validate_optional_text(
    config: dict[str, Any],
    field: str,
    resource_type: str,
    maximum_length: int,
) -> list[str]:
    if field not in config:
        return []
    value = config[field]
    if not isinstance(value, str) or len(value) > maximum_length:
        return [f"{resource_type} {field} must be a bounded string"]
    return []


def _unknown_field_errors(
    config: dict[str, Any],
    allowed_fields: frozenset[str],
    resource_type: str,
) -> list[str]:
    unknown = sorted(
        str(field) for field in set(config) - allowed_fields
    )
    if not unknown:
        return []
    return [
        f"{resource_type} config contains unsupported fields: "
        + ", ".join(unknown)
    ]


def persistence_safety_errors(
    value: Any, sensitive_values: tuple[str, ...] = ()
) -> list[str]:
    """Detect secret material without returning or persisting a redacted copy.

    Configuration plans must retain exact operational input for hash binding and
    later application, so redaction is not a safe substitute here.  The shared
    recursive sanitizer is used only as a detector; any redaction or sanitation
    failure makes the value ineligible for plan persistence.
    """

    errors: list[str] = []
    sanitized = sanitize_untrusted_data(
        value,
        known_secrets=sensitive_values,
    )
    if sanitized.failed_closed or sanitized.redaction_applied:
        errors.append(
            "secret-bearing, webhook, or other prohibited sensitive data "
            "cannot be persisted in a change plan"
        )
    if any(
        isinstance(value, str)
        and (
            "/mcp/" in value.lower()
            or (
                "/mcp" in value.lower()
                and value.startswith(("http://", "https://"))
            )
        )
        for value in _values(value)
    ):
        errors.append(
            "authenticated MCP URLs cannot be persisted in a change plan"
        )
    return _deduplicate(errors)


def _mapping_keys_are_strings(value: Any) -> bool:
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _mapping_keys_are_strings(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return all(_mapping_keys_are_strings(item) for item in value)
    return True


def _keys(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def _values(value: Any):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _values(item)


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _canonical(item)
            for key, item in sorted(
                value.items(), key=lambda pair: str(pair[0])
            )
        }
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _summary(value: Any) -> Any:
    if isinstance(value, list):
        return {"type": "list", "count": len(value)}
    if isinstance(value, dict):
        return {
            "type": "object",
            "keys": sorted(value)[:20],
            "key_count": len(value),
        }
    if isinstance(value, str):
        return value[:160] + ("..." if len(value) > 160 else "")
    return value


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
