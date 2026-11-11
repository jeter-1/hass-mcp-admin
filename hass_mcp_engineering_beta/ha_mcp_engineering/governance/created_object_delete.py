"""Closed deletion of one Engineering-created scene or storage helper (HAMCP-298).

Design: ``docs/HAMCP_329_298_SCENE_CREATE_AND_CREATE_ROLLBACK_DESIGN.md``
§3.4.3 and §3.5.  The only writes are exactly these, each sent at most once
and only from a created-object rollback child:

- ``scene_configuration``: ``DELETE /api/config/scene/config/<config_id>``
  with no body, under the single-use ``paths.scene_delete_permit`` for that
  one config id (``core.scene_create_delete_operation``);
- ``input_boolean`` / ``input_number``: WS ``{"type": "<type>/delete",
  "<type>_id": "<storage object id>"}`` built by the closed command builder
  below (``core.created_helper_delete_operation``).

The delete argument is the storage object id, never the full entity id; the
state readback uses the actual registry entity id, never the resource target.
The HAMCP-155 automation/script gateway is unchanged and never sends these.
"""

from __future__ import annotations

import copy
from typing import Any

from ..errors import AuthorizationError, HomeAssistantApiError
from ..request_context import current_telemetry, mark_provider_send
from . import created_object_identity as identity_model
from .configuration_delete import (
    ABSENCE_POLL_ATTEMPTS,
    ABSENCE_POLL_INTERVAL_SECONDS,
    DELETE_REJECTED_REASON,
    EXPECTED_EFFECT,
    MAX_DEPENDENCY_REASON_LENGTH,
    MAX_DEPENDENCY_REASONS,
    MAX_REGISTRY_ENTRY_ID_LENGTH,
    MAX_REGISTRY_LIST_ITEMS,
    RUNTIME_UNVERIFIED,
    ConfigurationDeleteProviderError,
    absence_proven,
    consumer_fingerprint,
)

ACTION = "delete"
SCENE_PROFILE = "core.scene_create_delete_operation"
HELPER_PROFILE = "core.created_helper_delete_operation"
PENDING_ADMISSION_REASON = "pending_signed_registry_admission"
F3_CONTEXT_REQUIRED_REASON = "active_f3_execution_required"
RESOURCE_TYPES = ("input_boolean", "input_number", "scene_configuration")
HELPER_TYPES = ("input_boolean", "input_number")
PROFILES = {
    "input_boolean": HELPER_PROFILE,
    "input_number": HELPER_PROFILE,
    "scene_configuration": SCENE_PROFILE,
}
PROVIDER = "home_assistant_created_object_delete"
PROVIDER_CONTRACTS = {
    "input_boolean": "compiled-core-created-helper-delete-operation-v1",
    "input_number": "compiled-core-created-helper-delete-operation-v1",
    "scene_configuration": "compiled-core-scene-create-delete-operation-v1",
}
CAPABILITY_IDENTITIES = {
    "input_boolean": "delete_created_input_boolean",
    "input_number": "delete_created_input_number",
    "scene_configuration": "delete_created_scene_configuration",
}
CAPTURE_CAPABILITY_IDENTITIES = {
    "input_boolean": "capture_created_input_boolean_identity",
    "input_number": "capture_created_input_number_identity",
    "scene_configuration": "capture_created_scene_identity",
}
INTENT_KEY = "created_object_delete"
INTENT_MODEL = "created_object_delete_intent_v1"
INTENT_FIELDS = frozenset(
    {
        "model",
        "resource_type",
        "target_id",
        "storage_object_id",
        "registry_unique_id",
        "registry_entry_id",
        "entity_id",
        "delete_argument",
        "pre_image_fingerprint",
        "consumer_evidence_fingerprint",
        "dependency_reasons",
        "recreate_eligible",
    }
)
# The only helper delete commands this build can produce (§3.5).
HELPER_DELETE_COMMANDS = frozenset({"input_boolean/delete", "input_number/delete"})
HELPER_NOT_FOUND_REASON = DELETE_REJECTED_REASON

def profile_for(resource_type: str) -> str:
    return PROFILES[resource_type]


def build_intent(
    *,
    resource_type: str,
    target_id: str,
    entity_id: str,
    registry_entry_id: str,
    pre_image_fingerprint: str,
    consumer_evidence_fingerprint: str,
    dependency_reasons: list[str],
    recreate_eligible: bool,
) -> dict[str, Any]:
    """Return the closed intent stored as a created-object delete proposal."""

    storage_id = identity_model.storage_object_id_for(resource_type, target_id)
    value = {
        INTENT_KEY: {
            "model": INTENT_MODEL,
            "resource_type": resource_type,
            "target_id": target_id,
            "storage_object_id": storage_id,
            "registry_unique_id": storage_id,
            "registry_entry_id": registry_entry_id,
            "entity_id": entity_id,
            "delete_argument": identity_model.delete_argument(resource_type, target_id),
            "pre_image_fingerprint": pre_image_fingerprint,
            "consumer_evidence_fingerprint": consumer_evidence_fingerprint,
            "dependency_reasons": list(dependency_reasons),
            "recreate_eligible": recreate_eligible,
        }
    }
    errors = intent_errors(resource_type, target_id, value)
    if errors:
        raise ValueError("; ".join(errors))
    return value


def _is_hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _target_errors(resource_type: str, target_id: Any) -> list[str]:
    from .resources import _validate_resource_id as validate_resource_id

    if resource_type == "scene_configuration":
        from .scene_configuration import config_id_valid

        return [] if config_id_valid(target_id) else ["created delete target is invalid"]
    return list(validate_resource_id(resource_type, target_id))


def intent_errors(resource_type: str, target_id: str, proposed: Any) -> list[str]:
    """Validate a created-object delete intent exactly; anything else is refused."""

    if resource_type not in RESOURCE_TYPES:
        return ["created delete resource_type is unsupported"]
    errors = _target_errors(resource_type, target_id)
    if not isinstance(proposed, dict) or set(proposed) != {INTENT_KEY}:
        return [*errors, "created delete proposal must be exactly one intent"]
    intent = proposed[INTENT_KEY]
    if not isinstance(intent, dict) or set(intent) != INTENT_FIELDS:
        return [*errors, "created delete intent fields are not the closed set"]
    if intent["model"] != INTENT_MODEL:
        errors.append("created delete intent model is unsupported")
    if intent["resource_type"] != resource_type:
        errors.append("created delete intent resource_type does not match")
    if intent["target_id"] != target_id:
        errors.append("created delete intent target_id does not match")
    try:
        storage_id = identity_model.storage_object_id_for(resource_type, target_id)
        argument = identity_model.delete_argument(resource_type, target_id)
    except identity_model.IdentityMismatch:
        return [*errors, "created delete target has no storage identity"]
    # §3.2.2: object id == storage id == registry unique_id == delete argument.
    if (
        intent["storage_object_id"] != storage_id
        or intent["registry_unique_id"] != storage_id
    ):
        errors.append("created delete intent storage identity does not correspond")
    if intent["delete_argument"] != argument:
        errors.append("created delete intent delete_argument does not correspond")
    domain = identity_model.REGISTRY[resource_type][0]
    entity_id = intent["entity_id"]
    if (
        not isinstance(entity_id, str)
        or not entity_id.startswith(f"{domain}.")
        or len(entity_id) > 255
        or entity_id != entity_id.strip()
        or len(entity_id) <= len(domain) + 1
    ):
        errors.append("created delete intent entity_id is invalid")
    entry_id = intent["registry_entry_id"]
    if (
        not isinstance(entry_id, str)
        or not 1 <= len(entry_id) <= MAX_REGISTRY_ENTRY_ID_LENGTH
        or not entry_id.isalnum()
    ):
        errors.append("created delete intent registry_entry_id is invalid")
    for name in ("pre_image_fingerprint", "consumer_evidence_fingerprint"):
        if not _is_hash(intent[name]):
            errors.append(f"created delete intent {name} is invalid")
    reasons = intent["dependency_reasons"]
    if (
        not isinstance(reasons, list)
        or len(reasons) > MAX_DEPENDENCY_REASONS
        or any(
            not isinstance(item, str)
            or not 1 <= len(item) <= MAX_DEPENDENCY_REASON_LENGTH
            for item in reasons
        )
        or reasons != sorted(set(reasons))
    ):
        errors.append("created delete intent dependency_reasons are invalid")
    if type(intent["recreate_eligible"]) is not bool:
        errors.append("created delete intent recreate_eligible must be a boolean")
    return errors


def is_intent(proposed: Any) -> bool:
    return isinstance(proposed, dict) and set(proposed) == {INTENT_KEY}


def intent(proposed: dict[str, Any]) -> dict[str, Any]:
    return proposed[INTENT_KEY]


def normalize_intent(proposed: dict[str, Any] | None) -> dict[str, Any] | None:
    if proposed is None:
        return None
    return copy.deepcopy(proposed)


def provider_payload(resource_type: str, target_id: str) -> dict[str, Any]:
    """The exact delete argument (helper WS command or scene REST route)."""

    return identity_model.delete_argument(resource_type, target_id)


def helper_delete_command(resource_type: str, target_id: str) -> dict[str, str]:
    """The closed helper delete builder: only the two reviewed commands."""

    if resource_type not in HELPER_TYPES:
        raise ValueError("created helper delete type is unsupported")
    command = identity_model.delete_argument(resource_type, target_id)
    if (
        command.get("type") not in HELPER_DELETE_COMMANDS
        or set(command) != {"type", f"{resource_type}_id"}
        or command["type"] != f"{resource_type}/delete"
    ):
        raise ValueError("created helper delete command is outside the vocabulary")
    return command


class CreatedObjectDeleteGateway:
    """The closed reads and single delete of one Engineering-created object.

    Every read and the write first prove current Core admission of the
    type's own profile.  ``core_runtime`` is set by the governance runtime;
    absent, every call refuses before it is sent.
    """

    def __init__(self, configuration_gateway: Any) -> None:
        self._configuration = configuration_gateway
        self.core_runtime: Any = None

    @property
    def rest_client(self) -> Any:
        return self._configuration.rest_client

    @property
    def websocket_client(self) -> Any:
        return self._configuration.websocket_client

    def admission_refusal(self, resource_type: str) -> str | None:
        if resource_type not in RESOURCE_TYPES:
            return "unsupported_resource_type"
        core = self.core_runtime
        try:
            admitted = (
                core is not None
                and core.route_status((PROFILES[resource_type],)).get("available")
                is True
            )
        except Exception:
            admitted = False
        return None if admitted else PENDING_ADMISSION_REASON

    def _require(self, resource_type: str) -> None:
        refusal = self.admission_refusal(resource_type)
        if refusal is not None:
            raise ConfigurationDeleteProviderError(refusal)

    async def read_config(
        self, resource_type: str, target_id: str
    ) -> dict[str, Any] | None:
        """The stored body: scene config GET, or the helper's storage item."""

        self._require(resource_type)
        if resource_type == "scene_configuration":
            from .scene_configuration import config_id_valid

            if not config_id_valid(target_id):
                raise ValueError("scene_configuration target must be a config id")
            return await self._configuration.scene_create.read_config(target_id)
        identity_model.storage_object_id_for(resource_type, target_id)
        return await self._configuration.get(resource_type, target_id)

    async def registry_entry(
        self, resource_type: str, target_id: str
    ) -> dict[str, Any] | None:
        """The one ``(domain, platform, storage id)`` registry entry, or ``None``.

        A scene entry with the config id from any other platform, or more
        than one candidate, is outside the contract.
        """

        self._require(resource_type)
        storage_id = identity_model.storage_object_id_for(resource_type, target_id)
        domain, platform = identity_model.REGISTRY[resource_type]
        items = await self.websocket_client.command(
            {"type": "config/entity_registry/list"}
        )
        if not isinstance(items, list) or len(items) > MAX_REGISTRY_LIST_ITEMS:
            raise ConfigurationDeleteProviderError("registry_list_unavailable")
        matches = []
        for item in items:
            if not isinstance(item, dict):
                raise ConfigurationDeleteProviderError("registry_list_malformed")
            entity_id = item.get("entity_id")
            if (
                item.get("unique_id") == storage_id
                and isinstance(entity_id, str)
                and entity_id.startswith(f"{domain}.")
                and (resource_type == "scene_configuration" or item.get("platform") == platform)
            ):
                matches.append(item)
        if not matches:
            return None
        if len(matches) != 1 or matches[0].get("platform") != platform:
            raise ConfigurationDeleteProviderError("registry_entry_duplicated")
        entry = matches[0]
        entry_id = entry.get("id")
        if (
            not isinstance(entry_id, str)
            or not 1 <= len(entry_id) <= MAX_REGISTRY_ENTRY_ID_LENGTH
            or not entry_id.isalnum()
            or len(entry["entity_id"]) > 255
        ):
            raise ConfigurationDeleteProviderError("registry_entry_malformed")
        return entry

    async def consumer_evidence(self, entity_id: str) -> dict[str, Any] | None:
        from .entity_availability_exposure_planning import read_consumer_evidence

        reader = self._configuration.availability_exposure_snapshot_reader
        if reader is None:
            return None
        return await read_consumer_evidence(reader, entity_id, fenced=True)

    async def consumer_fingerprint(self, entity_id: str) -> str | None:
        evidence = await self.consumer_evidence(entity_id)
        return None if evidence is None else consumer_fingerprint(evidence)

    async def state_exists(self, resource_type: str, entity_id: str) -> bool:
        self._require(resource_type)
        return await self._configuration.entity_metadata.state_exists(entity_id)

    async def absence(
        self, resource_type: str, target_id: str, entity_id: str
    ) -> dict[str, bool]:
        """Read all three absence facts by storage id and actual entity id."""

        config = await self.read_config(resource_type, target_id)
        entry = await self.registry_entry(resource_type, target_id)
        state = await self.state_exists(resource_type, entity_id)
        return {
            "config_absent": config is None,
            "registry_entry_absent": entry is None,
            "state_absent": state is False,
        }

    async def delete(self, resource_type: str, target_id: str) -> Any:
        """Send the one delete; a refusal or not-found is a non-mutation."""

        from .resources import ConfigurationMutationNotDispatchedError

        operation = f"{resource_type}_created_object_delete"
        if resource_type not in RESOURCE_TYPES:
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": "created_object_delete",
                    "reason": "unsupported_resource_type",
                    "provider_response_received": False,
                },
                call_not_sent=True,
            )
        refusal = self.admission_refusal(resource_type)
        if refusal is None:
            # The delete is sent only inside an active F3 dispatch whose
            # request-bound Core authority is current (as the scene POST).
            telemetry = current_telemetry()
            if (
                telemetry is None
                or telemetry.core_dispatch_authorizer is None
                or not telemetry.authorize_core_dispatch()
            ):
                refusal = F3_CONTEXT_REQUIRED_REASON
        if refusal is not None:
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": operation,
                    "resource_id": target_id,
                    "reason": refusal,
                    "provider_dispatch_occurred": False,
                },
                call_not_sent=True,
            )
        try:
            if resource_type == "scene_configuration":
                return await self._delete_scene(operation, target_id)
            return await self._delete_helper(operation, resource_type, target_id)
        except AuthorizationError:
            raise
        except HomeAssistantApiError as exc:
            if isinstance(exc, ConfigurationMutationNotDispatchedError):
                raise
            details = exc.details if isinstance(exc.details, dict) else {}
            status = details.get("status")
            code = details.get("code")
            if (type(status) is int and 400 <= status < 500) or code == "not_found":
                raise ConfigurationMutationNotDispatchedError(
                    details={
                        "operation": operation,
                        "resource_id": target_id,
                        "reason": DELETE_REJECTED_REASON,
                        "provider_status": status if type(status) is int else 404,
                        "provider_response_received": True,
                    }
                ) from exc
            raise

    async def _delete_scene(self, operation: str, config_id: str) -> Any:
        from ..clients import paths
        from .resources import ConfigurationMutationNotDispatchedError
        from .scene_configuration import config_id_valid

        if not config_id_valid(config_id):
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": operation,
                    "reason": "invalid_identifier",
                    "provider_response_received": False,
                },
                call_not_sent=True,
            )
        try:
            route = paths.rest_path("/config/scene/config/{}", config_id)
        except paths.UnsafeRequestPathError as exc:
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": operation,
                    "reason": "invalid_identifier",
                    "provider_response_received": False,
                },
                call_not_sent=True,
            ) from exc
        with paths.scene_delete_permit(config_id):
            mark_provider_send()  # OD-6(ii): the exact send boundary
            return await self.rest_client.request("DELETE", route)

    async def _delete_helper(
        self, operation: str, resource_type: str, target_id: str
    ) -> Any:
        from .resources import ConfigurationMutationNotDispatchedError

        try:
            command = helper_delete_command(resource_type, target_id)
        except (ValueError, identity_model.IdentityMismatch) as exc:
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": operation,
                    "reason": "invalid_identifier",
                    "provider_response_received": False,
                },
                call_not_sent=True,
            ) from exc
        mark_provider_send()  # OD-6(ii): the exact send boundary
        return await self.websocket_client.command(dict(command))


__all__ = [
    "ABSENCE_POLL_ATTEMPTS",
    "ABSENCE_POLL_INTERVAL_SECONDS",
    "ACTION",
    "CAPABILITY_IDENTITIES",
    "CAPTURE_CAPABILITY_IDENTITIES",
    "CreatedObjectDeleteGateway",
    "DELETE_REJECTED_REASON",
    "EXPECTED_EFFECT",
    "F3_CONTEXT_REQUIRED_REASON",
    "HELPER_DELETE_COMMANDS",
    "HELPER_PROFILE",
    "INTENT_KEY",
    "INTENT_MODEL",
    "PENDING_ADMISSION_REASON",
    "PROFILES",
    "PROVIDER",
    "PROVIDER_CONTRACTS",
    "RESOURCE_TYPES",
    "RUNTIME_UNVERIFIED",
    "SCENE_PROFILE",
    "absence_proven",
    "build_intent",
    "helper_delete_command",
    "intent",
    "intent_errors",
    "is_intent",
    "normalize_intent",
    "profile_for",
    "provider_payload",
]
