"""Exact, code-owned entity enable/disable and assistant-exposure boundary.

HAMCP-069B.  Two sibling resource types share one registry entry as their
resource: ``entity_registry_availability`` (``disabled_by``) and
``entity_assistant_exposure`` (one assistant's stored ``should_expose``).
Their governed state is the complete ``config/entity_registry/get``
``extended_dict``.  The binding projection keeps ``disabled_by`` and every
assistant's stored exposure exact (an absent key is its own value) and
reduces every other registry field to guard hashes, so a concurrent change
is a stale or verification failure rather than silent drift.

The gateway sends only a closed command vocabulary.  The two writes are
``config/entity_registry/update`` carrying only ``disabled_by`` and
``homeassistant/expose_entity`` carrying one assistant and one entity.  It
never calls ``cloud/status`` (its preferences carry the Google secure-devices
PIN), ``homeassistant/expose_entity/list``, ``expose_new_entities/set``, any
Google ``disable_2fa`` setting, or any read that runs Core's default-writing
``async_should_expose``.  There is no fallback provider and no upstream
``ha-mcp`` dependency.  Both types stay unadmitted until a signed Core
registry sequence pins their profiles (OD-2c).
"""

from __future__ import annotations

import copy
import json
from typing import Any

from ..entity_ids import validate_entity_id
from ..errors import AuthorizationError, HomeAssistantApiError
from .entity_metadata import (
    EntityMetadataProviderError,
    EntityMetadataUpdateErrorResult,
    entry_errors as metadata_entry_errors,
)
from .normalize import stable_hash


AVAILABILITY_RESOURCE_TYPE = "entity_registry_availability"
EXPOSURE_RESOURCE_TYPE = "entity_assistant_exposure"
RESOURCE_TYPES = (AVAILABILITY_RESOURCE_TYPE, EXPOSURE_RESOURCE_TYPE)
AVAILABILITY_CAPABILITY_IDENTITY = (
    "update_entity_registry_availability_configuration"
)
EXPOSURE_CAPABILITY_IDENTITY = "update_entity_assistant_exposure_configuration"
CAPABILITY_IDENTITIES = {
    AVAILABILITY_RESOURCE_TYPE: AVAILABILITY_CAPABILITY_IDENTITY,
    EXPOSURE_RESOURCE_TYPE: EXPOSURE_CAPABILITY_IDENTITY,
}
AVAILABILITY_PROVIDER = "direct_home_assistant_entity_registry_availability"
EXPOSURE_PROVIDER = "direct_home_assistant_assistant_exposure"
PROVIDERS = {
    AVAILABILITY_RESOURCE_TYPE: AVAILABILITY_PROVIDER,
    EXPOSURE_RESOURCE_TYPE: EXPOSURE_PROVIDER,
}
AVAILABILITY_PROVIDER_CONTRACT = "direct-ha-exact-entity-registry-availability-v1"
EXPOSURE_PROVIDER_CONTRACT = "direct-ha-exact-assistant-exposure-v1"
AVAILABILITY_PROVIDER_OPERATION = "set_entity_disabled_by"
EXPOSURE_PROVIDER_OPERATION = "set_entity_assistant_exposure"
PROVIDER_OPERATIONS = {
    AVAILABILITY_RESOURCE_TYPE: AVAILABILITY_PROVIDER_OPERATION,
    EXPOSURE_RESOURCE_TYPE: EXPOSURE_PROVIDER_OPERATION,
}
NORMALIZATION_MODEL = "entity_availability_exposure_projection_v1"
BINDINGS_KEY = "plan_bindings"
# Only Alexa's handler writes a computed default as a side effect.
TOLERATED_NEW_KEY_ASSISTANTS = ("cloud.alexa",)
UNAUTHORIZED_REASON = "entity_availability_exposure_update_unauthorized"
ERROR_RESULT_REASON = "entity_availability_exposure_update_error_result"
# OD-2c (i): both resource types stay inactive until a signed Core registry
# sequence pins their profiles.  This is the reason every surface reports.
PENDING_ADMISSION_REASON = "pending_signed_registry_admission"
PENDING_ADMISSION_TEXT = (
    "Entity enable/disable and assistant exposure are implemented but inactive: "
    "pending signed registry admission (planned: Core registry sequence 13 "
    "with the Core 2026.11 admission). No plan is created and nothing is "
    "dispatched."
)

# Core ``KNOWN_ASSISTANTS`` (exposed_entities.py:25).  Every one is observed
# and bound; only the in-scope assistants can be written (OD-3: Alexa out).
KNOWN_ASSISTANTS = ("cloud.alexa", "cloud.google_assistant", "conversation")
WRITABLE_ASSISTANTS = ("cloud.google_assistant", "conversation")
_DISABLED_BY_VALUES = frozenset(
    {None, "user", "integration", "device", "config_entry", "hass"}
)
_WRITABLE_DISABLED_BY = frozenset({None, "user"})
# ``modified_at`` changes with any write, and ``next_name_part`` is derived.
_UNBOUND_FIELDS = frozenset({"modified_at", "next_name_part"})
_IDENTITY_FIELDS = frozenset({"entity_id", "id"})
_PROJECTED_FIELDS = frozenset({"disabled_by", "options"})
MAX_OPTION_DOMAINS = 32
MAX_REGISTRY_LIST_ITEMS = 5_000
MAX_CONFIG_ENTRY_TEXT = 255

# The closed websocket vocabulary.  Every command this gateway sends passes
# through ``_send``, which refuses anything else before I/O.
READ_COMMANDS = (
    "config/entity_registry/get",
    "config/entity_registry/list",
    "config_entries/get_single",
    "homeassistant/expose_new_entities/get",
)
WRITE_COMMANDS = {
    AVAILABILITY_RESOURCE_TYPE: "config/entity_registry/update",
    EXPOSURE_RESOURCE_TYPE: "homeassistant/expose_entity",
}
ALLOWED_COMMANDS = frozenset({*READ_COMMANDS, *WRITE_COMMANDS.values()})
# Never sent: reads that write a computed default, the PIN-bearing cloud
# status, bulk exposure listing, the "expose new entities" setting and every
# cloud entity-settings write (which carries Google ``disable_2fa``).
NEVER_SENT_COMMANDS = (
    "cloud/alexa/entities/update",
    "cloud/google_assistant/entities/update",
    "cloud/status",
    "homeassistant/expose_entity/list",
    "homeassistant/expose_new_entities/set",
)


class AvailabilityExposureContractError(ValueError):
    """A proposed change is outside the closed, reviewed contract."""


def provider_evidence(resource_type: str) -> dict[str, Any]:
    """Return the immutable, code-owned direct-provider contract."""

    if resource_type == AVAILABILITY_RESOURCE_TYPE:
        constraints: dict[str, Any] = {
            "command": WRITE_COMMANDS[resource_type],
            "target": "exact_entity_id",
            "fields": ["disabled_by"],
            "values": ["user", None],
        }
        contract = AVAILABILITY_PROVIDER_CONTRACT
    elif resource_type == EXPOSURE_RESOURCE_TYPE:
        constraints = {
            "command": WRITE_COMMANDS[resource_type],
            "target": "exact_registered_entity_id",
            "assistants": list(WRITABLE_ASSISTANTS),
            "entities_per_command": 1,
            "assistants_per_command": 1,
        }
        contract = EXPOSURE_PROVIDER_CONTRACT
    else:
        raise ValueError("unsupported resource type")
    return {
        "provider": PROVIDERS[resource_type],
        "provider_contract_model": contract,
        "provider_operation": PROVIDER_OPERATIONS[resource_type],
        "transport": "home_assistant_websocket",
        "readback_transport": "home_assistant_websocket_entity_registry_get",
        "argument_constraints": {
            **constraints,
            "never_sent": list(NEVER_SENT_COMMANDS),
            "arbitrary_payload_allowed": False,
            "fallback_allowed": False,
        },
        "admission": PENDING_ADMISSION_REASON,
        "fallback": "none",
        "fallback_occurred": False,
    }


def entry_errors(target_id: str, entry: Any) -> list[str]:
    """Return contract errors for one registry entry state, without I/O."""

    errors = [
        error
        for error in metadata_entry_errors(target_id, entry)
        if not error.startswith("registry entry exceeds")
    ]
    if not isinstance(entry, dict) or errors:
        return errors or ["registry entry must be an object"]
    if entry.get("disabled_by") not in _DISABLED_BY_VALUES:
        errors.append("registry disabled_by is outside the reviewed vocabulary")
    for name in (
        "config_entry_id",
        "entity_category",
        "device_class",
        "original_device_class",
    ):
        value = entry.get(name)
        if value is not None and (
            not isinstance(value, str) or len(value) > MAX_CONFIG_ENTRY_TEXT
        ):
            errors.append(f"registry {name} must be a bounded string or null")
    options = entry.get("options", {})
    if (
        not isinstance(options, dict)
        or len(options) > MAX_OPTION_DOMAINS
        or not all(
            isinstance(key, str) and isinstance(value, dict)
            for key, value in options.items()
        )
    ):
        errors.append("registry options must be a bounded object of objects")
    else:
        for assistant in KNOWN_ASSISTANTS:
            settings = options.get(assistant)
            if (
                isinstance(settings, dict)
                and "should_expose" in settings
                and type(settings["should_expose"]) is not bool
            ):
                errors.append("registry should_expose must be a boolean")
    try:
        encoded = json.dumps(
            {key: value for key, value in entry.items() if key != BINDINGS_KEY},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        errors.append("registry entry must be JSON-compatible")
    else:
        if len(encoded) > 65_536:
            errors.append("registry entry exceeds the reviewed size bound")
    return errors


_OPERATIONS = {
    AVAILABILITY_RESOURCE_TYPE: frozenset({"disable", "enable"}),
    EXPOSURE_RESOURCE_TYPE: frozenset({"expose", "unexpose"}),
}
_BINDING_KEYS = frozenset(
    {
        "operation",
        "assistant",
        "config_entry",
        "protection",
        "consumer_evidence_fingerprint",
    }
)


def bindings_errors(resource_type: str, bindings: Any) -> list[str]:
    """Validate the closed plan bindings carried by a proposed entry."""

    if not isinstance(bindings, dict) or set(bindings) != _BINDING_KEYS:
        return ["entity availability/exposure plan bindings are missing or malformed"]
    errors: list[str] = []
    operation = bindings["operation"]
    if operation not in _OPERATIONS.get(resource_type, ()):
        errors.append("plan bindings operation does not match the resource type")
    assistant = bindings["assistant"]
    if resource_type == EXPOSURE_RESOURCE_TYPE:
        if assistant not in WRITABLE_ASSISTANTS:
            errors.append("plan bindings assistant is outside the reviewed scope")
    elif assistant is not None:
        errors.append("plan bindings assistant applies only to exposure")
    config_entry = bindings["config_entry"]
    if config_entry is not None and (
        not isinstance(config_entry, dict)
        or set(config_entry)
        != {"entry_id", "domain", "supports_unload", "disabled_by", "status"}
    ):
        errors.append("plan bindings config entry evidence is malformed")
    protection = bindings["protection"]
    if (
        not isinstance(protection, dict)
        or set(protection) != {"object_reasons", "dependency_reasons"}
        or not all(
            isinstance(protection[key], list)
            and all(isinstance(item, str) for item in protection[key])
            for key in protection
        )
    ):
        errors.append("plan bindings protection evidence is malformed")
    fingerprint = bindings["consumer_evidence_fingerprint"]
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        errors.append("plan bindings consumer evidence fingerprint is malformed")
    return errors


def proposed_errors(resource_type: str, target_id: str, proposed: Any) -> list[str]:
    """Validate a proposed final entry, including its plan bindings."""

    errors = entry_errors(target_id, proposed)
    if not isinstance(proposed, dict):
        return errors
    errors.extend(bindings_errors(resource_type, proposed.get(BINDINGS_KEY)))
    if errors:
        return errors
    bindings = proposed[BINDINGS_KEY]
    operation = bindings["operation"]
    if resource_type == AVAILABILITY_RESOURCE_TYPE:
        expected = "user" if operation == "disable" else None
        if proposed.get("disabled_by") != expected:
            errors.append("proposed disabled_by does not match the operation")
    else:
        value = exposure_value(proposed, bindings["assistant"])
        if value != {"present": True, "should_expose": operation == "expose"}:
            errors.append("proposed exposure does not match the operation")
    return errors


def exposure_value(entry: dict[str, Any], assistant: str) -> dict[str, Any]:
    """Return one assistant's stored preference; absent is its own value."""

    options = entry.get("options") or {}
    settings = options.get(assistant) if isinstance(options, dict) else None
    if isinstance(settings, dict) and "should_expose" in settings:
        return {"present": True, "should_expose": settings["should_expose"]}
    return {"present": False, "should_expose": None}


def _options_guard(entry: dict[str, Any]) -> str:
    options = copy.deepcopy(entry.get("options") or {})
    if not isinstance(options, dict):
        raise ValueError("registry options are malformed")
    for assistant in KNOWN_ASSISTANTS:
        settings = options.get(assistant)
        if isinstance(settings, dict):
            settings.pop("should_expose", None)
            if not settings:
                options.pop(assistant)
    return stable_hash(options)


def normalize_entry(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the deterministic binding projection of one registry entry."""

    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise TypeError("registry entry must be an object or null")
    guarded = {
        key: value
        for key, value in entry.items()
        if key not in _PROJECTED_FIELDS
        and key not in _UNBOUND_FIELDS
        and key not in _IDENTITY_FIELDS
        and key != BINDINGS_KEY
    }
    return {
        "model": NORMALIZATION_MODEL,
        "entity_id": entry.get("entity_id"),
        "registry_id": entry.get("id"),
        "disabled_by": entry.get("disabled_by"),
        "exposure": {
            assistant: exposure_value(entry, assistant)
            for assistant in KNOWN_ASSISTANTS
        },
        "registry_guard": stable_hash(guarded),
        "options_guard": _options_guard(entry),
    }


def entry_fingerprint(entry: dict[str, Any] | None) -> str:
    return stable_hash(normalize_entry(entry))


def without_bindings(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in entry.items() if key != BINDINGS_KEY}


def tolerated_exposure_keys(
    approved: dict[str, Any], observed: dict[str, Any], target_assistant: str | None
) -> list[str]:
    """Return the non-target Alexa key if it went from absent to present.

    Core's Alexa registry-update handler persists a computed default the first
    time it evaluates an entity, so only that new key is reported and
    tolerated; any other exposure difference is a mismatch.
    """

    tolerated: list[str] = []
    for assistant in TOLERATED_NEW_KEY_ASSISTANTS:
        if assistant == target_assistant:
            continue
        if (
            not approved["exposure"][assistant]["present"]
            and observed["exposure"][assistant]["present"]
        ):
            tolerated.append(assistant)
    return tolerated


def mask_tolerated(
    approved: dict[str, Any], observed: dict[str, Any], target_assistant: str | None
) -> tuple[dict[str, Any], list[str]]:
    """Return ``observed`` with tolerated new exposure keys masked."""

    tolerated = tolerated_exposure_keys(approved, observed, target_assistant)
    if not tolerated:
        return observed, []
    masked = copy.deepcopy(observed)
    for assistant in tolerated:
        masked["exposure"][assistant] = copy.deepcopy(
            approved["exposure"][assistant]
        )
    return masked, tolerated


def update_command(
    resource_type: str,
    entity_id: str,
    current: dict[str, Any] | None,
    proposed: dict[str, Any],
) -> dict[str, Any]:
    """Build the sole websocket write from the closed contract.

    The proposed entry must differ from the current entry in exactly the one
    governed value; anything else is refused before any I/O.
    """

    validate_entity_id(entity_id)
    if current is None or proposed_errors(resource_type, entity_id, proposed):
        raise AvailabilityExposureContractError("proposed change is invalid")
    before = normalize_entry(current) or {}
    after = normalize_entry(without_bindings(proposed)) or {}
    bindings = proposed[BINDINGS_KEY]
    if resource_type == AVAILABILITY_RESOURCE_TYPE:
        value = proposed.get("disabled_by")
        if value not in _WRITABLE_DISABLED_BY or before["disabled_by"] == value:
            raise AvailabilityExposureContractError("disabled_by change is invalid")
        if {**before, "disabled_by": value} != after:
            raise AvailabilityExposureContractError("unplanned registry change")
        return {
            "type": WRITE_COMMANDS[resource_type],
            "entity_id": entity_id,
            "disabled_by": value,
        }
    assistant = bindings["assistant"]
    target = after["exposure"][assistant]
    expected = copy.deepcopy(before)
    expected["exposure"][assistant] = target
    if expected != after or before["exposure"][assistant] == target:
        raise AvailabilityExposureContractError("exposure change is invalid")
    return {
        "type": WRITE_COMMANDS[resource_type],
        "assistants": [assistant],
        "entity_ids": [entity_id],
        "should_expose": target["should_expose"],
    }


def command_errors(resource_type: str, entity_id: str, command: Any) -> list[str]:
    """Re-check a write command against the closed shape, without I/O."""

    if not isinstance(command, dict):
        return ["command must be an object"]
    if resource_type == AVAILABILITY_RESOURCE_TYPE:
        if (
            set(command) != {"type", "entity_id", "disabled_by"}
            or command["type"] != WRITE_COMMANDS[resource_type]
            or command["entity_id"] != entity_id
            or command["disabled_by"] not in _WRITABLE_DISABLED_BY
        ):
            return ["closed_payload_violation"]
        return []
    if resource_type == EXPOSURE_RESOURCE_TYPE:
        if (
            set(command) != {"type", "assistants", "entity_ids", "should_expose"}
            or command["type"] != WRITE_COMMANDS[resource_type]
            or command["entity_ids"] != [entity_id]
            or not isinstance(command["assistants"], list)
            or len(command["assistants"]) != 1
            or command["assistants"][0] not in WRITABLE_ASSISTANTS
            or type(command["should_expose"]) is not bool
        ):
            return ["closed_payload_violation"]
        return []
    return ["unsupported_resource_type"]


def _bounded(value: Any) -> str | None:
    if isinstance(value, str):
        return value[:MAX_CONFIG_ENTRY_TEXT]
    return None


class EntityAvailabilityExposureGateway:
    """Permit only the reviewed reads and the two closed writes."""

    def __init__(self, websocket_client: Any) -> None:
        self.websocket_client = websocket_client

    async def _send(self, command: dict[str, Any]) -> Any:
        if command.get("type") not in ALLOWED_COMMANDS:
            raise AvailabilityExposureContractError("command outside the closed vocabulary")
        return await self.websocket_client.command(dict(command))

    async def read_entry(self, entity_id: str) -> dict[str, Any] | None:
        validate_entity_id(entity_id)
        try:
            value = await self._send(
                {"type": "config/entity_registry/get", "entity_id": entity_id}
            )
        except HomeAssistantApiError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            if details.get("status") == 404:
                return None
            raise
        if not isinstance(value, dict) or entry_errors(entity_id, value):
            raise EntityMetadataProviderError("entity_registry_entry_malformed")
        return value

    async def config_entry(self, entry_id: str) -> dict[str, Any]:
        """Return bounded config-entry evidence; ``status`` says how it was read."""

        if not isinstance(entry_id, str) or not 0 < len(entry_id) <= MAX_CONFIG_ENTRY_TEXT:
            raise ValueError("config entry id is invalid")
        try:
            value = await self._send(
                {"type": "config_entries/get_single", "entry_id": entry_id}
            )
        except AuthorizationError:
            raise
        except HomeAssistantApiError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            status = "not_found" if details.get("status") == 404 else "unavailable"
            return {
                "entry_id": entry_id,
                "domain": None,
                "supports_unload": None,
                "disabled_by": None,
                "status": status,
            }
        entry = value.get("config_entry") if isinstance(value, dict) else None
        if (
            not isinstance(entry, dict)
            or entry.get("entry_id") != entry_id
            or type(entry.get("supports_unload")) is not bool
        ):
            raise EntityMetadataProviderError("config_entry_malformed")
        return {
            "entry_id": entry_id,
            "domain": _bounded(entry.get("domain")),
            "supports_unload": entry["supports_unload"],
            "disabled_by": _bounded(entry.get("disabled_by")),
            "status": "read",
        }

    async def other_entities_in_entry(
        self, entry_id: str, entity_id: str
    ) -> int | None:
        """Count the entry's other registry entities; ``None`` if unavailable."""

        try:
            items = await self._send({"type": "config/entity_registry/list"})
        except AuthorizationError:
            raise
        except HomeAssistantApiError:
            return None
        if not isinstance(items, list) or len(items) > MAX_REGISTRY_LIST_ITEMS:
            return None
        count = 0
        for item in items:
            if not isinstance(item, dict):
                return None
            if (
                item.get("config_entry_id") == entry_id
                and item.get("entity_id") != entity_id
            ):
                count += 1
        return count

    async def expose_new(self, assistant: str) -> bool | None:
        """Read the "expose new entities" setting; it is never written."""

        if assistant not in KNOWN_ASSISTANTS:
            raise ValueError("assistant is invalid")
        try:
            value = await self._send(
                {"type": "homeassistant/expose_new_entities/get", "assistant": assistant}
            )
        except AuthorizationError:
            raise
        except HomeAssistantApiError:
            return None
        if not isinstance(value, dict) or type(value.get("expose_new")) is not bool:
            return None
        return value["expose_new"]

    async def update(
        self, resource_type: str, entity_id: str, command: dict[str, Any]
    ) -> dict[str, Any]:
        """Send the one closed write built by ``update_command``."""

        from .resources import ConfigurationMutationNotDispatchedError

        validate_entity_id(entity_id)
        operation = PROVIDER_OPERATIONS.get(resource_type, "unsupported")
        if command_errors(resource_type, entity_id, command):
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": operation,
                    "resource_id": entity_id,
                    "reason": "closed_payload_violation",
                }
            )
        try:
            result = await self._send(command)
        except AuthorizationError as exc:
            # Raised while authenticating, before the command is sent, or for
            # an ``unauthorized`` error result.  Neither applies the write.
            details = exc.details if isinstance(exc.details, dict) else {}
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": operation,
                    "resource_id": entity_id,
                    "reason": UNAUTHORIZED_REASON,
                    "provider_response_received": (
                        details.get("provider_response_received") is True
                    ),
                }
            ) from exc
        except HomeAssistantApiError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            if details.get("provider_response_received") is True:
                # An error result does not prove that nothing changed; only
                # the registry readback settles it (069A OD-15 rule).
                raise EntityMetadataUpdateErrorResult(
                    details={
                        "operation": operation,
                        "resource_id": entity_id,
                        "reason": ERROR_RESULT_REASON,
                        "provider_response_received": True,
                        **(
                            {"provider_status": details["status"]}
                            if type(details.get("status")) is int
                            else {}
                        ),
                    }
                ) from exc
            raise
        if resource_type == EXPOSURE_RESOURCE_TYPE:
            # ``ws_expose_entity`` answers with an empty result; it is never
            # proof.  The registry readback decides.
            return {"core_response": {}}
        if not isinstance(result, dict) or not isinstance(
            result.get("entity_entry"), dict
        ):
            raise EntityMetadataProviderError("entity_registry_update_malformed")
        if result["entity_entry"].get("entity_id") != entity_id:
            raise EntityMetadataProviderError("entity_registry_update_identity")
        response: dict[str, Any] = {}
        if result.get("require_restart") is True:
            response["require_restart"] = True
        if type(result.get("reload_delay")) is int:
            response["reload_delay"] = result["reload_delay"]
        return {"core_response": response}
