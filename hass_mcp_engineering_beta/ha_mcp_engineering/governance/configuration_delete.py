"""Closed governed deletion of one UI-managed automation or script (HAMCP-155).

The only write is Home Assistant Core's
``DELETE /api/config/<automation|script>/config/<key>`` with no body.  Core
answers a missing key with HTTP 400 before writing, removes the entry from
the YAML file, and then removes the entity registry entry, which unloads the
entity without a reload.  Verification is proof of absence: the config GET
returns 404, the registry has no entry for ``(domain, domain, key)``, and the
state machine has no state for the captured entity_id.

This module owns the delete intent recorded in a plan and the closed reads
and single DELETE used by planning and the F3 delete adapter.  Nothing else
sends DELETE: the generic configuration ``write`` keeps refusing any action
other than create or update.
"""

from __future__ import annotations

import copy
from typing import Any

from ..errors import AuthorizationError, HomeAssistantApiError
from ..f3.contracts import mark_provider_send
from .normalize import stable_hash

PROFILE = "core.configuration_delete_operation"
PROVIDER = "home_assistant_configuration_delete"
PROVIDER_CONTRACT = "compiled-core-configuration-delete-operation-v1"
PENDING_ADMISSION_REASON = "pending_signed_registry_admission"
ACTION = "delete"
RESOURCE_TYPES = ("automation", "script")
CAPABILITY_IDENTITIES = {
    "automation": "delete_automation_configuration",
    "script": "delete_script_configuration",
}
INTENT_KEY = "configuration_delete"
INTENT_MODEL = "configuration_delete_intent_v1"
CONSUMER_EVIDENCE_MODEL = "configuration_delete_consumer_evidence_v1"
INTENT_FIELDS = frozenset(
    {
        "model",
        "resource_type",
        "target_id",
        "entity_id",
        "registry_entry_id",
        "pre_image_fingerprint",
        "consumer_evidence_fingerprint",
        "dependency_reasons",
        "recreate_eligible",
    }
)
MAX_DEPENDENCY_REASONS = 64
MAX_DEPENDENCY_REASON_LENGTH = 200
EXPECTED_EFFECT = "configuration_resource_deleted"
DELETE_REJECTED_REASON = "configuration_delete_rejected"
# Core removes the registry entry in a fire-and-forget post-write hook, so
# the runtime half of the absence proof is polled within this bound.
ABSENCE_POLL_ATTEMPTS = 6
ABSENCE_POLL_INTERVAL_SECONDS = 0.5
RUNTIME_UNVERIFIED = "config_removed_runtime_unverified"
MAX_REGISTRY_LIST_ITEMS = 5_000
MAX_REGISTRY_ENTRY_ID_LENGTH = 64


class ConfigurationDeleteProviderError(RuntimeError):
    """A closed delete read returned a response outside its contract."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def entity_id_for(resource_type: str, target_id: str, entry: dict[str, Any]) -> str:
    entity_id = entry.get("entity_id")
    if (
        not isinstance(entity_id, str)
        or not entity_id.startswith(f"{resource_type}.")
        or len(entity_id) > 255
    ):
        raise ConfigurationDeleteProviderError("registry_entry_malformed")
    del target_id
    return entity_id


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
    """Return the closed delete intent stored as a delete child's proposal."""

    intent = {
        INTENT_KEY: {
            "model": INTENT_MODEL,
            "resource_type": resource_type,
            "target_id": target_id,
            "entity_id": entity_id,
            "registry_entry_id": registry_entry_id,
            "pre_image_fingerprint": pre_image_fingerprint,
            "consumer_evidence_fingerprint": consumer_evidence_fingerprint,
            "dependency_reasons": list(dependency_reasons),
            "recreate_eligible": recreate_eligible,
        }
    }
    errors = intent_errors(resource_type, target_id, intent)
    if errors:
        raise ValueError("; ".join(errors))
    return intent


def _is_hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def intent_errors(
    resource_type: str, target_id: str, proposed: Any
) -> list[str]:
    """Validate a delete intent exactly; anything else is refused."""

    from .resources import _validate_resource_id as validate_resource_id

    errors: list[str] = []
    if resource_type not in RESOURCE_TYPES:
        return ["delete resource_type must be automation or script"]
    errors.extend(validate_resource_id(resource_type, target_id))
    if not isinstance(proposed, dict) or set(proposed) != {INTENT_KEY}:
        return [*errors, "delete proposal must be exactly one delete intent"]
    intent = proposed[INTENT_KEY]
    if not isinstance(intent, dict) or set(intent) != INTENT_FIELDS:
        return [*errors, "delete intent fields are not the closed set"]
    if intent["model"] != INTENT_MODEL:
        errors.append("delete intent model is unsupported")
    if intent["resource_type"] != resource_type:
        errors.append("delete intent resource_type does not match")
    if intent["target_id"] != target_id:
        errors.append("delete intent target_id does not match")
    entity_id = intent["entity_id"]
    if (
        not isinstance(entity_id, str)
        or not entity_id.startswith(f"{resource_type}.")
        or len(entity_id) > 255
        or entity_id != entity_id.strip()
    ):
        errors.append("delete intent entity_id is invalid")
    entry_id = intent["registry_entry_id"]
    if (
        not isinstance(entry_id, str)
        or not 1 <= len(entry_id) <= MAX_REGISTRY_ENTRY_ID_LENGTH
        or not entry_id.isalnum()
    ):
        errors.append("delete intent registry_entry_id is invalid")
    for name in ("pre_image_fingerprint", "consumer_evidence_fingerprint"):
        if not _is_hash(intent[name]):
            errors.append(f"delete intent {name} is invalid")
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
        # Bound into the proposed hash so policy recomputes the protected
        # dependency basis from the immutable plan alone.
        errors.append("delete intent dependency_reasons are invalid")
    if type(intent["recreate_eligible"]) is not bool:
        errors.append("delete intent recreate_eligible must be a boolean")
    return errors


def intent(proposed: dict[str, Any]) -> dict[str, Any]:
    return proposed[INTENT_KEY]


def normalize_intent(proposed: dict[str, Any] | None) -> dict[str, Any] | None:
    if proposed is None:
        return None
    return copy.deepcopy(proposed)


def consumer_fingerprint(evidence: dict[str, Any]) -> str:
    """Bind the disclosed consumer evidence into the plan; rechecked at preflight."""

    return stable_hash({"model": CONSUMER_EVIDENCE_MODEL, **evidence})


def provider_payload(resource_type: str, target_id: str) -> dict[str, Any]:
    return {
        "method": "DELETE",
        "path": f"/config/{resource_type}/config/{target_id}",
    }


def provider_arguments_hash(resource_type: str, target_id: str) -> str:
    return stable_hash(provider_payload(resource_type, target_id))


class ConfigurationDeleteGateway:
    """The closed reads and single no-body DELETE for one exact target."""

    def __init__(self, configuration_gateway: Any) -> None:
        self._configuration = configuration_gateway

    @property
    def rest_client(self) -> Any:
        return self._configuration.rest_client

    @property
    def websocket_client(self) -> Any:
        return self._configuration.websocket_client

    async def read_config(
        self, resource_type: str, target_id: str
    ) -> dict[str, Any] | None:
        """Exact config GET; a 404 is ``None``."""

        if resource_type not in RESOURCE_TYPES:
            raise ValueError("delete resource_type must be automation or script")
        return await self._configuration.get(resource_type, target_id)

    async def registry_entry(
        self, resource_type: str, target_id: str
    ) -> dict[str, Any] | None:
        """Return the one registry entry Core's delete hook would remove.

        Core resolves ``async_get_entity_id(domain, domain, key)``; this reads
        the bounded registry list and matches the same platform and
        unique_id.  More than one match is outside the contract.
        """

        if resource_type not in RESOURCE_TYPES:
            raise ValueError("delete resource_type must be automation or script")
        items = await self.websocket_client.command(
            {"type": "config/entity_registry/list"}
        )
        if not isinstance(items, list) or len(items) > MAX_REGISTRY_LIST_ITEMS:
            raise ConfigurationDeleteProviderError("registry_list_unavailable")
        matches = []
        for item in items:
            if not isinstance(item, dict):
                raise ConfigurationDeleteProviderError("registry_list_malformed")
            if (
                item.get("platform") == resource_type
                and item.get("unique_id") == target_id
            ):
                matches.append(item)
        if not matches:
            return None
        if len(matches) != 1:
            raise ConfigurationDeleteProviderError("registry_entry_duplicated")
        entry = matches[0]
        entity_id_for(resource_type, target_id, entry)
        entry_id = entry.get("id")
        if (
            not isinstance(entry_id, str)
            or not 1 <= len(entry_id) <= MAX_REGISTRY_ENTRY_ID_LENGTH
            or not entry_id.isalnum()
        ):
            raise ConfigurationDeleteProviderError("registry_entry_malformed")
        return entry

    async def consumer_evidence(self, entity_id: str) -> dict[str, Any] | None:
        """Read the deleted entity's consumers from the fenced dependency index.

        ``None`` means the index reader is not wired, which callers treat as
        unavailable evidence; a failed build is the 069B "unavailable"
        evidence with its gaps, never "no consumers".
        """

        from .entity_availability_exposure_planning import read_consumer_evidence

        reader = self._configuration.availability_exposure_snapshot_reader
        if reader is None:
            return None
        return await read_consumer_evidence(reader, entity_id, fenced=True)

    async def consumer_fingerprint(self, entity_id: str) -> str | None:
        evidence = await self.consumer_evidence(entity_id)
        return None if evidence is None else consumer_fingerprint(evidence)

    async def state_exists(self, entity_id: str) -> bool:
        return await self._configuration.entity_metadata.state_exists(entity_id)

    async def absence(
        self, resource_type: str, target_id: str, entity_id: str
    ) -> dict[str, bool]:
        """Read all three absence facts; every read is asserted, none assumed."""

        config = await self.read_config(resource_type, target_id)
        entry = await self.registry_entry(resource_type, target_id)
        state = await self.state_exists(entity_id)
        return {
            "config_absent": config is None,
            "registry_entry_absent": entry is None,
            "state_absent": state is False,
        }

    async def delete(self, resource_type: str, target_id: str) -> Any:
        """Send the one no-body DELETE; a 4xx is a confirmed non-mutation."""

        from .resources import (
            ConfigurationMutationNotDispatchedError,
            UnsafeRequestPathError,
            _config_route,
        )

        if resource_type not in RESOURCE_TYPES:
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": "configuration_delete",
                    "reason": "unsupported_resource_type",
                    "provider_response_received": False,
                }
            )
        try:
            path = _config_route(resource_type, target_id)
        except UnsafeRequestPathError as exc:
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": f"{resource_type}_configuration_delete",
                    "reason": "invalid_identifier",
                    "provider_response_received": False,
                }
            ) from exc
        # HAMCP-209/HAMCP-212: the dependency index treats a journaled key
        # whose stored read is 404 as reconciled, which is exactly a delete.
        from ..dependency import DEPENDENCY_ANALYSIS

        if resource_type == "automation":
            DEPENDENCY_ANALYSIS.note_automation_write(target_id)
        else:
            DEPENDENCY_ANALYSIS.note_script_write(target_id)
        try:
            mark_provider_send()  # OD-6(ii): the exact send boundary
            return await self.rest_client.request("DELETE", path)
        except AuthorizationError:
            raise
        except HomeAssistantApiError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            status = details.get("status")
            if type(status) is int and 400 <= status < 500:
                raise ConfigurationMutationNotDispatchedError(
                    details={
                        "operation": f"{resource_type}_configuration_delete",
                        "resource_id": target_id,
                        "reason": DELETE_REJECTED_REASON,
                        "provider_status": status,
                        "provider_response_received": True,
                    }
                ) from exc
            raise


def absence_proven(facts: dict[str, bool]) -> bool:
    return bool(
        facts.get("config_absent") is True
        and facts.get("registry_entry_absent") is True
        and facts.get("state_absent") is True
    )


__all__ = [
    "ABSENCE_POLL_ATTEMPTS",
    "ABSENCE_POLL_INTERVAL_SECONDS",
    "ACTION",
    "CAPABILITY_IDENTITIES",
    "CONSUMER_EVIDENCE_MODEL",
    "ConfigurationDeleteGateway",
    "ConfigurationDeleteProviderError",
    "DELETE_REJECTED_REASON",
    "EXPECTED_EFFECT",
    "INTENT_KEY",
    "INTENT_MODEL",
    "PENDING_ADMISSION_REASON",
    "PROFILE",
    "PROVIDER",
    "PROVIDER_CONTRACT",
    "RESOURCE_TYPES",
    "RUNTIME_UNVERIFIED",
    "absence_proven",
    "build_intent",
    "consumer_fingerprint",
    "intent",
    "intent_errors",
    "normalize_intent",
    "provider_arguments_hash",
    "provider_payload",
]
