"""Exact, code-owned Home Assistant entity registry rename boundary.

HAMCP-295 (design ``docs/HAMCP_295_109_ENTITY_RENAME_DEPENDENT_EXECUTION_DESIGN.md``
section 3.1).  One registry entry is the resource.  The only write is
``config/entity_registry/update`` with exactly ``entity_id`` and
``new_entity_id``; no metadata field, ``disabled_by``, ``options`` or any other
key can be expressed in the same message.  There is no fallback provider and
no upstream ``ha-mcp`` dependency.

This module is pure apart from the closed gateway: it validates the request,
binds the planning fingerprint, classifies the final preflight into exactly
one of the three V9 cases, verifies the authoritative readback and builds the
exact forward and compensation commands.  It is not wired into a planner,
tool or F3 adapter here (Phase B, ``.artifacts/pending-wiring.md``).
"""

from __future__ import annotations

from typing import Any

from ..entity_ids import STRICT_ENTITY_ID, validate_entity_id
from ..errors import AuthorizationError, HomeAssistantApiError
from ..request_context import mark_provider_send
from .entity_metadata import (
    ENTITY_METADATA_NOT_DISPATCHED_OUTCOME,
    EntityMetadataProviderError,
    entry_errors,
)
from .normalize import stable_hash


ENTITY_RENAME_RESOURCE_TYPE = "entity_registry_rename"
ENTITY_RENAME_ACTION = "update"
ENTITY_RENAME_CAPABILITY_IDENTITY = "rename_entity_registry_entry"
ENTITY_RENAME_TOOL = "create_entity_rename_plan"
ENTITY_RENAME_PROFILE = "core.entity_registry_rename_operation"
ENTITY_RENAME_PROVIDER = "direct_home_assistant_entity_registry"
ENTITY_RENAME_PROVIDER_CONTRACT = "direct-ha-exact-entity-registry-rename-v1"
ENTITY_RENAME_PROVIDER_OPERATION = "entity_registry_rename"
ENTITY_RENAME_NORMALIZATION_MODEL = "entity_registry_rename_projection_v1"
ENTITY_RENAME_COMMAND = "config/entity_registry/update"
ENTITY_RENAME_READ_COMMAND = "config/entity_registry/get"
ENTITY_RENAME_UNAUTHORIZED_REASON = "entity_registry_rename_unauthorized"
ENTITY_RENAME_ERROR_RESULT_REASON = "entity_registry_rename_error_result"
# V9: the all-no-op outcome reuses the shipped 069A code (design 3.2.9).
ENTITY_RENAME_NOT_DISPATCHED_OUTCOME = ENTITY_METADATA_NOT_DISPATCHED_OUTCOME
ENTITY_RENAME_NOOP_EVENT = "preflight_noop_verified"

# Closed tool input (design 3.1, Tool).  No caller field supplies a rewrite.
REQUEST_FIELDS = frozenset({"entity_id", "new_entity_id", "title", "description"})
REQUIRED_REQUEST_FIELDS = frozenset({"entity_id", "new_entity_id"})
MAX_TITLE_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 2_000

# Refusal codes (design sections 3.1, 6).
REFUSAL_UNKNOWN_FIELD = "entity_rename_field_not_supported"
REFUSAL_MISSING_FIELD = "entity_rename_field_required"
REFUSAL_INVALID_ENTITY_ID = "entity_rename_invalid_entity_id"
REFUSAL_INVALID_NEW_ENTITY_ID = "entity_rename_invalid_new_entity_id"
REFUSAL_CROSS_DOMAIN = "entity_rename_cross_domain"
REFUSAL_SAME_ID = "entity_rename_same_entity_id"
REFUSAL_INVALID_TEXT = "entity_rename_invalid_text"
REFUSAL_SOURCE_NOT_REGISTERED = "entity_rename_source_not_registered"
REFUSAL_SOURCE_CHANGED = "entity_rename_source_changed"
REFUSAL_DESTINATION_OCCUPIED = "entity_rename_destination_occupied"
REFUSAL_CLOSED_PAYLOAD = "closed_payload_violation"

# Preflight cases (design 3.1, Already present (V9) versus source changed).
PREFLIGHT_DISPATCH = "dispatch"
PREFLIGHT_ALREADY_PRESENT = "already_present"

# The registry identity a rename must preserve (design 3.1, readback 1).
IDENTITY_FIELDS = ("id", "unique_id", "platform", "config_entry_id")
# Never bound: ``modified_at`` changes on every update and ``next_name_part``
# (2026.10+) is derived from area and device (069A V2).  ``entity_id`` is the
# renamed field itself.
_UNBOUND_FIELDS = frozenset({"modified_at", "next_name_part"})
_RENAMED_FIELD = "entity_id"

# Locks (design 3.1, Locks).
SHARED_CORE_LOCK = "core:home_assistant"
RENAME_SERIALIZATION_LOCK = "reload:entity_registry_rename"


class EntityRenameRequestError(ValueError):
    """The rename request is outside the closed contract."""

    def __init__(self, code: str, field: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.field = field


class EntityRenameUpdateErrorResult(HomeAssistantApiError):
    """Home Assistant answered a sent rename with a non-authorization error.

    As for 069A OD-15, an error result does not prove that the registry is
    unchanged, so the outcome stays uncertain and readback decides it.
    """


def entity_rename_provider_evidence() -> dict[str, Any]:
    """Return the immutable, code-owned direct-provider contract."""

    return {
        "provider": ENTITY_RENAME_PROVIDER,
        "provider_contract_model": ENTITY_RENAME_PROVIDER_CONTRACT,
        "provider_operation": ENTITY_RENAME_PROVIDER_OPERATION,
        "transport": "home_assistant_websocket",
        "readback_transport": "home_assistant_websocket_entity_registry_get",
        "argument_constraints": {
            "command": ENTITY_RENAME_COMMAND,
            "target": "exact_entity_id",
            "fields": ["new_entity_id"],
            "never_sent": [
                "aliases",
                "area_id",
                "categories",
                "device_class",
                "disabled_by",
                "hidden_by",
                "icon",
                "labels",
                "name",
                "options",
                "options_domain",
            ],
            "arbitrary_payload_allowed": False,
            "fallback_allowed": False,
        },
        "fallback": "none",
        "fallback_occurred": False,
    }


def entity_domain(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


def _valid_strict(entity_id: Any) -> bool:
    if not isinstance(entity_id, str):
        return False
    try:
        validate_entity_id(entity_id)
    except ValueError:
        return False
    return STRICT_ENTITY_ID.fullmatch(entity_id) is not None


def validate_rename_request(arguments: Any) -> dict[str, Any]:
    """Return the normalized closed request or raise ``EntityRenameRequestError``.

    Both ids must satisfy the governed rule and the strict Core subset; the
    new id must keep the domain and differ from the old one.  No pattern or
    glob can match the strict rule, so a wildcard target is refused here.
    """

    if not isinstance(arguments, dict):
        raise EntityRenameRequestError(REFUSAL_MISSING_FIELD)
    unknown = sorted(key for key in arguments if key not in REQUEST_FIELDS)
    if unknown:
        raise EntityRenameRequestError(REFUSAL_UNKNOWN_FIELD, str(unknown[0]))
    for field in sorted(REQUIRED_REQUEST_FIELDS):
        if field not in arguments:
            raise EntityRenameRequestError(REFUSAL_MISSING_FIELD, field)
    old = arguments["entity_id"]
    new = arguments["new_entity_id"]
    if not _valid_strict(old):
        raise EntityRenameRequestError(REFUSAL_INVALID_ENTITY_ID, "entity_id")
    if not _valid_strict(new):
        raise EntityRenameRequestError(
            REFUSAL_INVALID_NEW_ENTITY_ID, "new_entity_id"
        )
    if old == new:
        raise EntityRenameRequestError(REFUSAL_SAME_ID, "new_entity_id")
    if entity_domain(old) != entity_domain(new):
        raise EntityRenameRequestError(REFUSAL_CROSS_DOMAIN, "new_entity_id")
    result: dict[str, Any] = {"entity_id": old, "new_entity_id": new}
    for field, limit in (
        ("title", MAX_TITLE_LENGTH),
        ("description", MAX_DESCRIPTION_LENGTH),
    ):
        if field in arguments:
            value = arguments[field]
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise EntityRenameRequestError(REFUSAL_INVALID_TEXT, field)
            result[field] = value
    return result


def rename_lock_keys(old: str, new: str) -> dict[str, tuple[str, ...]]:
    """Return the exact rename lock set (design 3.1, Locks)."""

    validate_entity_id(old)
    validate_entity_id(new)
    return {
        "exclusive": tuple(
            sorted(
                {
                    f"entity_registry:{old}",
                    f"entity_registry:{new}",
                    RENAME_SERIALIZATION_LOCK,
                }
            )
        ),
        "shared": (SHARED_CORE_LOCK,),
    }


def bound_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Return every bound field of one entry: all but the unbound and the id."""

    return {
        key: value
        for key, value in entry.items()
        if key not in _UNBOUND_FIELDS and key != _RENAMED_FIELD
    }


def identity(entry: dict[str, Any]) -> dict[str, Any]:
    return {field: entry.get(field) for field in IDENTITY_FIELDS}


def entry_contract_errors(entity_id: str, entry: Any) -> list[str]:
    """Return 069A bounded-entry errors plus the rename identity rule."""

    errors = entry_errors(entity_id, entry)
    if errors:
        return errors
    unique_id = entry.get("unique_id")
    if unique_id is not None and not isinstance(unique_id, str):
        errors.append("registry unique_id must be a string or null")
    if not isinstance(entry.get("platform"), str) or not entry.get("platform"):
        errors.append("registry platform must be a non-empty string")
    config_entry_id = entry.get("config_entry_id")
    if config_entry_id is not None and not isinstance(config_entry_id, str):
        errors.append("registry config_entry_id must be a string or null")
    return errors


def planning_binding(
    old: str,
    new: str,
    entry: dict[str, Any],
    *,
    new_registered: bool,
    new_in_states: bool,
    impact_hash: str,
) -> dict[str, Any]:
    """Return the planning binding of design 3.1 (Planning fingerprint).

    Refuses when the entry is outside the contract or ``<new>`` is occupied.
    The binding is the exact entry minus ``modified_at``/``next_name_part``,
    its identity, the absence of ``<new>`` and the impact assessment hash.
    """

    if entry_contract_errors(old, entry):
        raise EntityRenameRequestError(REFUSAL_SOURCE_NOT_REGISTERED, "entity_id")
    if new_registered or new_in_states:
        raise EntityRenameRequestError(REFUSAL_DESTINATION_OCCUPIED, "new_entity_id")
    if not isinstance(impact_hash, str) or not impact_hash:
        raise ValueError("impact_hash is required")
    return {
        "model": ENTITY_RENAME_NORMALIZATION_MODEL,
        "old_entity_id": old,
        "new_entity_id": new,
        "identity": identity(entry),
        "entry": bound_entry(entry),
        "new_absent_registry": True,
        "new_absent_states": True,
        "impact_hash": impact_hash,
    }


def binding_fingerprint(binding: dict[str, Any]) -> str:
    return stable_hash(binding)


def forward_result_fingerprint(binding: dict[str, Any]) -> str:
    """Return the planned post-rename state: same bound entry, at ``<new>``."""

    return stable_hash(
        {
            "model": ENTITY_RENAME_NORMALIZATION_MODEL,
            "entity_id": binding["new_entity_id"],
            "absent_entity_id": binding["old_entity_id"],
            "identity": binding["identity"],
            "entry": binding["entry"],
        }
    )


def preimage_fingerprint(binding: dict[str, Any]) -> str:
    """Return the planned pre-rename state: same bound entry, at ``<old>``."""

    return stable_hash(
        {
            "model": ENTITY_RENAME_NORMALIZATION_MODEL,
            "entity_id": binding["old_entity_id"],
            "absent_entity_id": binding["new_entity_id"],
            "identity": binding["identity"],
            "entry": binding["entry"],
        }
    )


def _entry_matches(binding: dict[str, Any], entry: Any, at: str) -> bool:
    if not isinstance(entry, dict) or entry_contract_errors(at, entry):
        return False
    return (
        identity(entry) == binding["identity"]
        and bound_entry(entry) == binding["entry"]
    )


def classify_preflight(
    binding: dict[str, Any],
    *,
    old_entry: dict[str, Any] | None,
    new_entry: dict[str, Any] | None,
    new_in_states: bool,
) -> str:
    """Return exactly one V9 case for the rename child's final preflight.

    ``PREFLIGHT_DISPATCH``: ``<old>`` equals the binding and ``<new>`` is free
    in the registry and the state machine.  ``PREFLIGHT_ALREADY_PRESENT``:
    ``<old>`` is not found and ``<new>`` carries the planned identity with
    every other bound field equal.  Anything else is a determinate refusal
    code; the already-present case is never inferred from a partial match.
    """

    old = binding["old_entity_id"]
    new = binding["new_entity_id"]
    if old_entry is not None:
        if not _entry_matches(binding, old_entry, old):
            return REFUSAL_SOURCE_CHANGED
        if new_entry is not None or new_in_states:
            return REFUSAL_DESTINATION_OCCUPIED
        return PREFLIGHT_DISPATCH
    # ``<old>`` is gone: only the exact planned result is a no-op.
    if new_entry is None:
        return REFUSAL_SOURCE_CHANGED
    if _entry_matches(binding, new_entry, new):
        return PREFLIGHT_ALREADY_PRESENT
    if identity(new_entry) == binding["identity"]:
        # The planned entry moved to ``<new>`` but another field changed.
        return REFUSAL_SOURCE_CHANGED
    return REFUSAL_DESTINATION_OCCUPIED


def verify_readback(
    binding: dict[str, Any],
    *,
    old_entry: dict[str, Any] | None,
    new_entry: dict[str, Any] | None,
) -> tuple[bool, tuple[str, ...]]:
    """Return whether the authoritative readback proves the rename applied.

    All of: ``get(<new>)`` has the planned identity, every other bound field
    equals preflight, and ``get(<old>)`` is not found.  The state machine is
    never a gate.
    """

    reasons: list[str] = []
    new = binding["new_entity_id"]
    if new_entry is None:
        reasons.append("new_entity_id_not_registered")
    elif not isinstance(new_entry, dict) or entry_contract_errors(new, new_entry):
        reasons.append("new_entry_malformed")
    else:
        if identity(new_entry) != binding["identity"]:
            reasons.append("identity_changed")
        if bound_entry(new_entry) != binding["entry"]:
            reasons.append("entry_fields_changed")
    if old_entry is not None:
        reasons.append("old_entity_id_still_registered")
    return (not reasons, tuple(reasons))


def verify_not_applied(
    binding: dict[str, Any],
    *,
    old_entry: dict[str, Any] | None,
    new_entry: dict[str, Any] | None,
) -> bool:
    """Return whether readback proves the forward rename was NOT applied."""

    return (
        new_entry is None
        and old_entry is not None
        and _entry_matches(binding, old_entry, binding["old_entity_id"])
    )


def compensation_binding(binding: dict[str, Any]) -> dict[str, Any]:
    """Return the precomputed inverse: rename ``<new>`` back to ``<old>``."""

    return {
        **binding,
        "old_entity_id": binding["new_entity_id"],
        "new_entity_id": binding["old_entity_id"],
    }


def rename_command(old: str, new: str) -> dict[str, Any]:
    """Return the only write: exactly ``type``, ``entity_id``, ``new_entity_id``."""

    validate_entity_id(old)
    validate_entity_id(new)
    if old == new or entity_domain(old) != entity_domain(new):
        raise ValueError("rename command must keep the domain and change the id")
    return {"type": ENTITY_RENAME_COMMAND, "entity_id": old, "new_entity_id": new}


def compensation_command(binding: dict[str, Any]) -> dict[str, Any]:
    return rename_command(binding["new_entity_id"], binding["old_entity_id"])


def state_observation(old_in_states: bool, new_in_states: bool) -> dict[str, Any]:
    """Return the bounded, never-verified state-machine observation."""

    if new_in_states and not old_in_states:
        observed = "new_state_present"
    elif not new_in_states and not old_in_states:
        observed = "no_state_present"
    elif old_in_states and not new_in_states:
        observed = "old_state_still_present"
    else:
        observed = "both_states_present"
    return {
        "verification": "observation_not_verified",
        "observed": observed,
        "unverified_consequences": [
            "recorder_history_migration",
            "helper_config_entry_follow_and_reload",
            "re_add_or_in_place_move",
        ],
    }


class EntityRegistryRenameGateway:
    """Permit only the reviewed registry reads and the closed rename."""

    def __init__(self, rest_client: Any, websocket_client: Any) -> None:
        self.rest_client = rest_client
        self.websocket_client = websocket_client

    async def read_entry(self, entity_id: str) -> dict[str, Any] | None:
        validate_entity_id(entity_id)
        try:
            value = await self.websocket_client.command(
                {"type": ENTITY_RENAME_READ_COMMAND, "entity_id": entity_id}
            )
        except HomeAssistantApiError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            if details.get("status") == 404:
                return None
            raise
        if not isinstance(value, dict) or entry_contract_errors(entity_id, value):
            raise EntityMetadataProviderError("entity_registry_entry_malformed")
        return value

    async def state_exists(self, entity_id: str) -> bool:
        from ..clients.paths import rest_path
        from ..clients.rest import ExpectedHttpStatus

        validate_entity_id(entity_id)
        value = await self.rest_client.request(
            "GET",
            rest_path("/states/{}", entity_id, kinds=("entity_id",)),
            expected_statuses=frozenset({404}),
        )
        if isinstance(value, ExpectedHttpStatus) and value.status == 404:
            return False
        if not isinstance(value, dict) or value.get("entity_id") != entity_id:
            raise EntityMetadataProviderError("entity_state_malformed")
        return True

    async def rename(self, command: dict[str, Any]) -> dict[str, Any]:
        """Send the one closed command built by ``rename_command``."""

        from .resources import ConfigurationMutationNotDispatchedError

        old = command.get("entity_id") if isinstance(command, dict) else None
        new = command.get("new_entity_id") if isinstance(command, dict) else None
        exact = (
            isinstance(command, dict)
            and set(command) == {"type", "entity_id", "new_entity_id"}
            and command.get("type") == ENTITY_RENAME_COMMAND
        )
        if exact:
            try:
                exact = rename_command(old, new) == command
            except ValueError:
                exact = False
        if not exact:
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": ENTITY_RENAME_PROVIDER_OPERATION,
                    "resource_id": old if isinstance(old, str) else None,
                    "reason": REFUSAL_CLOSED_PAYLOAD,
                },
                call_not_sent=True,
            )
        try:
            mark_provider_send()  # the exact send boundary
            result = await self.websocket_client.command(dict(command))
        except AuthorizationError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": ENTITY_RENAME_PROVIDER_OPERATION,
                    "resource_id": old,
                    "reason": ENTITY_RENAME_UNAUTHORIZED_REASON,
                    "provider_response_received": (
                        details.get("provider_response_received") is True
                    ),
                },
                call_not_sent=(
                    getattr(exc, "call_not_sent", False) is True
                    and details.get("provider_response_received") is not True
                ),
            ) from exc
        except HomeAssistantApiError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            if details.get("provider_response_received") is True:
                # ``invalid_info`` (destination taken, invalid id, domain) and
                # every other answered error stay uncertain: readback decides.
                raise EntityRenameUpdateErrorResult(
                    details={
                        "operation": ENTITY_RENAME_PROVIDER_OPERATION,
                        "resource_id": old,
                        "reason": ENTITY_RENAME_ERROR_RESULT_REASON,
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
            raise EntityMetadataProviderError("entity_registry_rename_malformed")
        if result["entity_entry"].get("entity_id") != new:
            raise EntityMetadataProviderError("entity_registry_rename_identity")
        return result

