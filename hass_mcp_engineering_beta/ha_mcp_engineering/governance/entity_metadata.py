"""Exact, code-owned Home Assistant entity-registry metadata boundary.

HAMCP-069A-1.  One registry entry is the resource.  Its governed state is the
complete ``config/entity_registry/get`` ``extended_dict``; the binding
projection keeps the seven reviewed metadata fields exact and reduces every
other registry field to one guard hash, so a concurrent change to an
untouched field is a stale or verification failure rather than silent drift.

Only ``config/entity_registry/update`` with a closed field allowlist is ever
sent.  ``new_entity_id``, ``disabled_by``, ``device_class``, ``options`` and
``options_domain`` cannot be expressed.  There is no fallback provider and no
upstream ``ha-mcp`` dependency.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..entity_ids import validate_entity_id
from ..errors import AuthorizationError, HomeAssistantApiError
from .normalize import stable_hash


ENTITY_METADATA_RESOURCE_TYPE = "entity_registry_metadata"
ENTITY_METADATA_CAPABILITY_IDENTITY = (
    "update_entity_registry_metadata_configuration"
)
ENTITY_METADATA_PROVIDER = "direct_home_assistant_entity_registry"
ENTITY_METADATA_PROVIDER_CONTRACT = (
    "direct-ha-exact-entity-registry-metadata-v1"
)
ENTITY_METADATA_PROVIDER_OPERATION = "entity_registry_metadata_update"
ENTITY_METADATA_NORMALIZATION_MODEL = "entity_registry_metadata_projection_v1"
ENTITY_METADATA_BINDINGS_KEY = "plan_bindings"
ENTITY_METADATA_UNAUTHORIZED_REASON = "entity_registry_update_unauthorized"
# OD-15 (HAMCP-183): any other error result to a sent update.  It is not a
# non-mutation proof, so the dispatch stays uncertain and readback decides.
ENTITY_METADATA_ERROR_RESULT_REASON = "entity_registry_update_error_result"
# V9 (owner condition 2026-10-12): a plan whose final state was already
# present at preflight is verified but was never dispatched.  It must not read
# as a change Engineering made, in the outcome code, the text or the audit.
ENTITY_METADATA_NOT_DISPATCHED_OUTCOME = (
    "desired_state_already_present_not_dispatched"
)
ENTITY_METADATA_NOT_DISPATCHED_TEXT = (
    "desired state already present; not dispatched. Engineering did not "
    "make this change."
)
ENTITY_METADATA_NOT_DISPATCHED_EVENT = "task_completed_without_dispatch"

# The complete reviewed metadata vocabulary, in Home Assistant field names.
MANAGED_FIELDS = (
    "aliases",
    "area_id",
    "categories",
    "hidden_by",
    "icon",
    "labels",
    "name",
)
# Fields that are never bound: ``modified_at`` changes only when another field
# changes, and ``next_name_part`` (2026.10+) is derived from area and device.
_UNBOUND_FIELDS = frozenset({"modified_at", "next_name_part"})
_IDENTITY_FIELDS = frozenset({"entity_id", "id"})

ICON_PATTERN = re.compile(r"^[a-z0-9-]+:[a-z0-9-]+$")
SLUG_PATTERN = re.compile(r"^[a-z0-9_]+$")
MAX_REFERENCE_ID_LENGTH = 255
MAX_TEXT_LENGTH = 255
# Bounds on the state read from Home Assistant.  An entry beyond them is not
# planned rather than truncated.
MAX_STATE_ALIASES = 32
MAX_STATE_LABELS = 64
MAX_STATE_CATEGORY_SCOPES = 16
MAX_ENTRY_BYTES = 32_768
# Bounds on one registry list read used for reference validation.
MAX_REGISTRY_LIST_ITEMS = 2_000

_HIDDEN_BY_VALUES = frozenset({None, "user", "integration"})


class EntityMetadataStateError(ValueError):
    """A registry entry is outside the reviewed, bounded contract."""


class EntityMetadataProviderError(RuntimeError):
    """Bounded provider failure that never carries Home Assistant content."""

    def __init__(self, category: str) -> None:
        super().__init__("The entity registry provider returned an invalid response.")
        self.category = category


class EntityMetadataUpdateErrorResult(HomeAssistantApiError):
    """Home Assistant answered a sent update with a non-authorization error.

    The update was sent and a response was received, but the error result does
    not prove that the registry is unchanged: Core assigns the new entry before
    it schedules the save and fires the update event, and its WebSocket
    dispatcher turns a later exception into an ``unknown_error`` result.  The
    mutation is therefore unknown (neither ``mutation_dispatched`` flag is
    asserted) and only an authoritative registry readback can settle it.
    """


def entity_metadata_provider_evidence() -> dict[str, Any]:
    """Return the immutable, code-owned direct-provider contract."""

    return {
        "provider": ENTITY_METADATA_PROVIDER,
        "provider_contract_model": ENTITY_METADATA_PROVIDER_CONTRACT,
        "provider_operation": ENTITY_METADATA_PROVIDER_OPERATION,
        "transport": "home_assistant_websocket",
        "readback_transport": "home_assistant_websocket_entity_registry_get",
        "argument_constraints": {
            "command": "config/entity_registry/update",
            "target": "exact_entity_id",
            "fields": list(MANAGED_FIELDS),
            "never_sent": [
                "device_class",
                "disabled_by",
                "new_entity_id",
                "options",
                "options_domain",
            ],
            "arbitrary_payload_allowed": False,
            "fallback_allowed": False,
        },
        "fallback": "none",
        "fallback_occurred": False,
    }


def _optional_text(value: Any, field: str, errors: list[str]) -> None:
    if value is not None and (
        not isinstance(value, str) or len(value) > MAX_TEXT_LENGTH
    ):
        errors.append(f"registry {field} must be a bounded string or null")


def entry_errors(target_id: str, entry: Any) -> list[str]:
    """Return contract errors for one registry entry state, without I/O."""

    errors: list[str] = []
    try:
        validate_entity_id(target_id)
    except ValueError:
        return ["entity_id is invalid"]
    if not isinstance(entry, dict):
        return ["registry entry must be an object"]
    if not all(isinstance(key, str) for key in entry):
        return ["registry entry keys must be strings"]
    if entry.get("entity_id") != target_id:
        errors.append("registry entry entity_id must match the target")
    registry_id = entry.get("id")
    if (
        not isinstance(registry_id, str)
        or not registry_id
        or len(registry_id) > MAX_REFERENCE_ID_LENGTH
    ):
        errors.append("registry entry id must be a bounded string")
    _optional_text(entry.get("name"), "name", errors)
    _optional_text(entry.get("icon"), "icon", errors)
    _optional_text(entry.get("area_id"), "area_id", errors)
    _optional_text(entry.get("device_id"), "device_id", errors)
    if entry.get("hidden_by") not in _HIDDEN_BY_VALUES:
        errors.append("registry hidden_by is outside the reviewed vocabulary")
    aliases = entry.get("aliases", [])
    if not isinstance(aliases, list) or len(aliases) > MAX_STATE_ALIASES:
        errors.append("registry aliases must be a bounded list")
    else:
        for alias in aliases:
            if alias is not None and (
                not isinstance(alias, str) or len(alias) > MAX_TEXT_LENGTH
            ):
                errors.append("registry aliases must be bounded strings or null")
                break
    labels = entry.get("labels", [])
    if not isinstance(labels, list) or len(labels) > MAX_STATE_LABELS:
        errors.append("registry labels must be a bounded list")
    elif not all(
        isinstance(label, str) and 0 < len(label) <= MAX_REFERENCE_ID_LENGTH
        for label in labels
    ):
        errors.append("registry labels must be bounded label ids")
    categories = entry.get("categories", {})
    if (
        not isinstance(categories, dict)
        or len(categories) > MAX_STATE_CATEGORY_SCOPES
    ):
        errors.append("registry categories must be a bounded object")
    elif not all(
        isinstance(scope, str)
        and 0 < len(scope) <= MAX_REFERENCE_ID_LENGTH
        and isinstance(category, str)
        and 0 < len(category) <= MAX_REFERENCE_ID_LENGTH
        for scope, category in categories.items()
    ):
        errors.append("registry categories must map scopes to category ids")
    try:
        encoded = json.dumps(
            {
                key: value
                for key, value in entry.items()
                if key != ENTITY_METADATA_BINDINGS_KEY
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        errors.append("registry entry must be JSON-compatible")
    else:
        if len(encoded) > MAX_ENTRY_BYTES:
            errors.append("registry entry exceeds the reviewed size bound")
    return errors


def proposed_errors(target_id: str, proposed: Any) -> list[str]:
    """Validate a proposed final entry, including its plan bindings."""

    errors = entry_errors(target_id, proposed)
    if not isinstance(proposed, dict):
        return errors
    bindings = proposed.get(ENTITY_METADATA_BINDINGS_KEY)
    if not isinstance(bindings, dict) or set(bindings) != {
        "consumer_evidence_fingerprint",
        "reference_definitions",
    }:
        errors.append("entity metadata plan bindings are missing or malformed")
    name = proposed.get("name")
    if isinstance(name, str) and not name:
        errors.append("registry name must not be empty; use null to revert")
    return errors


def normalize_entry(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the deterministic binding projection of one registry entry."""

    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise TypeError("registry entry must be an object or null")
    labels = entry.get("labels") or []
    categories = entry.get("categories") or {}
    aliases = entry.get("aliases") or []
    if (
        not isinstance(labels, list)
        or not isinstance(categories, dict)
        or not isinstance(aliases, list)
    ):
        raise ValueError("registry entry collections are malformed")
    guarded = {
        key: value
        for key, value in entry.items()
        if key not in MANAGED_FIELDS
        and key not in _UNBOUND_FIELDS
        and key not in _IDENTITY_FIELDS
        and key != ENTITY_METADATA_BINDINGS_KEY
    }
    return {
        "model": ENTITY_METADATA_NORMALIZATION_MODEL,
        "entity_id": entry.get("entity_id"),
        "registry_id": entry.get("id"),
        "name": entry.get("name"),
        "icon": entry.get("icon"),
        "aliases": list(aliases),
        "area_id": entry.get("area_id"),
        "hidden_by": entry.get("hidden_by"),
        "labels": sorted(set(labels)),
        "categories": {key: categories[key] for key in sorted(categories)},
        "registry_guard": stable_hash(guarded),
    }


def entry_fingerprint(entry: dict[str, Any] | None) -> str:
    return stable_hash(normalize_entry(entry))


def touched_fields(
    current: dict[str, Any], final: dict[str, Any]
) -> tuple[str, ...]:
    """Return managed fields whose binding value differs, in fixed order."""

    before = normalize_entry(current) or {}
    after = normalize_entry(final) or {}
    return tuple(field for field in MANAGED_FIELDS if before.get(field) != after.get(field))


def update_fields(
    current: dict[str, Any] | None,
    final: dict[str, Any],
) -> dict[str, Any]:
    """Return the closed update body: touched managed fields only.

    Labels are sent as the complete final set (Home Assistant replaces the
    set).  Categories merge per scope in Home Assistant, so only changed
    scopes are sent, with ``null`` for a removed scope; the full final map is
    what readback verifies.
    """

    if current is None:
        return {}
    before = normalize_entry(current) or {}
    after = normalize_entry(final) or {}
    body: dict[str, Any] = {}
    for field in touched_fields(current, final):
        if field == "categories":
            old = before["categories"]
            new = after["categories"]
            body["categories"] = {
                scope: new.get(scope)
                for scope in sorted(set(old) | set(new))
                if old.get(scope) != new.get(scope)
            }
        elif field in {"labels", "aliases"}:
            body[field] = list(after[field])
        else:
            body[field] = after[field]
    return body


def update_command(
    entity_id: str,
    current: dict[str, Any] | None,
    final: dict[str, Any],
) -> dict[str, Any]:
    """Build the sole websocket command from the closed allowlist."""

    validate_entity_id(entity_id)
    return {
        "type": "config/entity_registry/update",
        "entity_id": entity_id,
        **update_fields(current, final),
    }


def bound_reference_ids(
    current: dict[str, Any], final: dict[str, Any]
) -> dict[str, Any]:
    """Return the label, area and category ids whose definitions are bound."""

    before = normalize_entry(current) or {}
    after = normalize_entry(final) or {}
    touched = set(touched_fields(current, final))
    labels: list[str] = []
    if "labels" in touched:
        labels = sorted(set(before["labels"]) ^ set(after["labels"]))
    areas: list[str] = []
    if "area_id" in touched:
        areas = sorted(
            {
                value
                for value in (before["area_id"], after["area_id"])
                if isinstance(value, str)
            }
        )
    categories: dict[str, list[str]] = {}
    if "categories" in touched:
        old = before["categories"]
        new = after["categories"]
        for scope in sorted(set(old) | set(new)):
            if old.get(scope) != new.get(scope):
                categories[scope] = sorted(
                    {
                        value
                        for value in (old.get(scope), new.get(scope))
                        if isinstance(value, str)
                    }
                )
    return {"labels": labels, "areas": areas, "categories": categories}


def required_final_references(
    current: dict[str, Any], final: dict[str, Any]
) -> dict[str, Any]:
    """Return references that must exist for the final state to be exact."""

    before = normalize_entry(current) or {}
    after = normalize_entry(final) or {}
    touched = set(touched_fields(current, final))
    return {
        "labels": sorted(set(after["labels"]) - set(before["labels"]))
        if "labels" in touched
        else [],
        "areas": [after["area_id"]]
        if "area_id" in touched and isinstance(after["area_id"], str)
        else [],
        "categories": {
            scope: category
            for scope, category in after["categories"].items()
            if "categories" in touched
            and before["categories"].get(scope) != category
        },
    }


def update_command_argument_names(command: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(command, key=lambda item: item.encode("utf-8")))


def area_requires_own_name(
    current: dict[str, Any], final: dict[str, Any]
) -> bool:
    """Return whether HA 2026.10's area/name rule would reject the update.

    Home Assistant 2026.10 rejects an update that touches ``area_id`` or
    ``name`` when the result is device-bound, has its own area and has an
    empty unprefixed name.  ``extended_dict['original_name']`` is already the
    unprefixed original name in 2026.9.4 and 2026.10.x.  The rule is refused
    on every Core so a plan never changes validity across a Core upgrade.
    """

    touched = set(touched_fields(current, final))
    if not touched & {"area_id", "name"}:
        return False
    return bool(
        final.get("area_id") is not None
        and final.get("device_id") is not None
        and final.get("name") is None
        and not final.get("original_name")
    )


def effective_device_class(entry: dict[str, Any]) -> str | None:
    value = entry.get("device_class") or entry.get("original_device_class")
    return value if isinstance(value, str) else None


def _registry_list_items(value: Any, category: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_REGISTRY_LIST_ITEMS:
        raise EntityMetadataProviderError(category)
    if not all(isinstance(item, dict) for item in value):
        raise EntityMetadataProviderError(category)
    return value


def _bounded_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value[:MAX_TEXT_LENGTH]
    return None


class EntityRegistryMetadataGateway:
    """Permit only the reviewed registry reads and the closed update."""

    def __init__(self, rest_client: Any, websocket_client: Any) -> None:
        self.rest_client = rest_client
        self.websocket_client = websocket_client

    async def read_entry(self, entity_id: str) -> dict[str, Any] | None:
        validate_entity_id(entity_id)
        try:
            value = await self.websocket_client.command(
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

    async def state_exists(self, entity_id: str) -> bool:
        from ..clients.rest import ExpectedHttpStatus

        validate_entity_id(entity_id)
        value = await self.rest_client.request(
            "GET",
            f"/states/{entity_id}",
            expected_statuses=frozenset({404}),
        )
        if isinstance(value, ExpectedHttpStatus) and value.status == 404:
            return False
        if not isinstance(value, dict) or value.get("entity_id") != entity_id:
            raise EntityMetadataProviderError("entity_state_malformed")
        return True

    async def labels(self) -> dict[str, dict[str, Any]]:
        items = _registry_list_items(
            await self.websocket_client.command(
                {"type": "config/label_registry/list"}
            ),
            "label_registry_malformed",
        )
        result: dict[str, dict[str, Any]] = {}
        for item in items:
            label_id = item.get("label_id")
            if not isinstance(label_id, str) or label_id in result:
                raise EntityMetadataProviderError("label_registry_malformed")
            result[label_id] = {
                "label_id": label_id,
                "name": _bounded_text(item.get("name")),
            }
        return result

    async def areas(self) -> dict[str, dict[str, Any]]:
        items = _registry_list_items(
            await self.websocket_client.command(
                {"type": "config/area_registry/list"}
            ),
            "area_registry_malformed",
        )
        result: dict[str, dict[str, Any]] = {}
        for item in items:
            area_id = item.get("area_id")
            if not isinstance(area_id, str) or area_id in result:
                raise EntityMetadataProviderError("area_registry_malformed")
            result[area_id] = {
                "area_id": area_id,
                "name": _bounded_text(item.get("name")),
                "floor_id": _bounded_text(item.get("floor_id")),
            }
        return result

    async def categories(self, scope: str) -> dict[str, dict[str, Any]]:
        if not isinstance(scope, str) or not SLUG_PATTERN.fullmatch(scope):
            raise ValueError("category scope is invalid")
        items = _registry_list_items(
            await self.websocket_client.command(
                {"type": "config/category_registry/list", "scope": scope}
            ),
            "category_registry_malformed",
        )
        result: dict[str, dict[str, Any]] = {}
        for item in items:
            category_id = item.get("category_id")
            if not isinstance(category_id, str) or category_id in result:
                raise EntityMetadataProviderError("category_registry_malformed")
            result[category_id] = {
                "category_id": category_id,
                "name": _bounded_text(item.get("name")),
            }
        return result

    async def reference_definitions(
        self, ids: dict[str, Any]
    ) -> dict[str, Any]:
        """Read the exact definitions of the bound references.

        ``None`` records a reference that does not exist.  Registries are read
        only when a reference of that kind is bound.
        """

        labels = ids.get("labels") or []
        areas = ids.get("areas") or []
        categories = ids.get("categories") or {}
        result: dict[str, Any] = {"labels": {}, "areas": {}, "categories": {}}
        if labels:
            registry = await self.labels()
            result["labels"] = {
                label_id: registry.get(label_id) for label_id in sorted(labels)
            }
        if areas:
            registry = await self.areas()
            result["areas"] = {
                area_id: registry.get(area_id) for area_id in sorted(areas)
            }
        for scope in sorted(categories):
            registry = await self.categories(scope)
            result["categories"][scope] = {
                category_id: registry.get(category_id)
                for category_id in sorted(categories[scope])
            }
        return result

    async def update(
        self,
        entity_id: str,
        command: dict[str, Any],
    ) -> dict[str, Any]:
        """Send the one closed update command built by ``update_command``."""

        from .resources import ConfigurationMutationNotDispatchedError

        validate_entity_id(entity_id)
        allowed = {"type", "entity_id", *MANAGED_FIELDS}
        if (
            command.get("type") != "config/entity_registry/update"
            or command.get("entity_id") != entity_id
            or not set(command) <= allowed
            or len(command) <= 2
        ):
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": ENTITY_METADATA_PROVIDER_OPERATION,
                    "resource_id": entity_id,
                    "reason": "closed_payload_violation",
                }
            )
        try:
            result = await self.websocket_client.command(dict(command))
        except AuthorizationError as exc:
            # The websocket client raises this either while authenticating,
            # before the command is sent, or for an ``unauthorized`` error
            # result.  Neither applies the update, so it is a confirmed
            # non-application with its own reason, never a generic failure.
            details = exc.details if isinstance(exc.details, dict) else {}
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": ENTITY_METADATA_PROVIDER_OPERATION,
                    "resource_id": entity_id,
                    "reason": ENTITY_METADATA_UNAUTHORIZED_REASON,
                    "provider_response_received": (
                        details.get("provider_response_received") is True
                    ),
                }
            ) from exc
        except HomeAssistantApiError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            if details.get("provider_response_received") is True:
                # Home Assistant answered the sent update with an error result
                # other than ``unauthorized``/``forbidden``.  That proves only
                # that the command was sent and answered.  It does not prove
                # that nothing changed: Core can assign the registry entry and
                # then return ``unknown_error`` (HAMCP-181 revision-3 probe,
                # Core 2026.10.0 and 2026.10.1).  Engineering's own pre-send
                # refusals (closed payload, unknown references, the 2026.10
                # area/name rule) never reach this point.  The outcome stays
                # uncertain so the F3 registry readback decides it.
                raise EntityMetadataUpdateErrorResult(
                    details={
                        "operation": ENTITY_METADATA_PROVIDER_OPERATION,
                        "resource_id": entity_id,
                        "reason": ENTITY_METADATA_ERROR_RESULT_REASON,
                        "provider_response_received": True,
                        **(
                            {"provider_status": details["status"]}
                            if type(details.get("status")) is int
                            else {}
                        ),
                    }
                ) from exc
            raise
        if not isinstance(result, dict) or not isinstance(
            result.get("entity_entry"), dict
        ):
            raise EntityMetadataProviderError("entity_registry_update_malformed")
        if result["entity_entry"].get("entity_id") != entity_id:
            raise EntityMetadataProviderError("entity_registry_update_identity")
        return result
