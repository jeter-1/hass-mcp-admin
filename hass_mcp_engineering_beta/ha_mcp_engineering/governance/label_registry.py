"""Exact, code-owned Home Assistant label-registry boundary (HAMCP-165).

One label-registry entry is the resource.  Its governed state is the entry
``config/label_registry/list`` returns (Core ``_entry_dict``); the binding
projection keeps ``label_id``, ``name``, ``color``, ``icon`` and
``description`` exact and never binds the timestamps.

The gateway sends only a closed command vocabulary: the list read, and one
``config/label_registry/create`` or ``config/label_registry/update`` per
child.  ``config/label_registry/delete`` is never sent (deletion is
HAMCP-155).  There is no fallback provider and no upstream ``ha-mcp``
dependency.  The resource type stays unadmitted until a signed Core registry
sequence pins ``core.label_registry_operation`` (seq-13 batch).
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Any, Mapping

from ..errors import AuthorizationError, HomeAssistantApiError
from .entity_metadata import (
    ICON_PATTERN,
    MAX_REGISTRY_LIST_ITEMS,
    MAX_TEXT_LENGTH,
    EntityMetadataProviderError,
    EntityMetadataUpdateErrorResult,
)
from .normalize import stable_hash


LABEL_RESOURCE_TYPE = "label_registry_entry"
CREATE_CAPABILITY_IDENTITY = "create_label_registry_entry_configuration"
UPDATE_CAPABILITY_IDENTITY = "update_label_registry_entry_configuration"
CAPABILITY_IDENTITIES = {
    "create": CREATE_CAPABILITY_IDENTITY,
    "update": UPDATE_CAPABILITY_IDENTITY,
}
CORE_CAPABILITY = "core.label_registry_operation"
LABEL_PROVIDER = "direct_home_assistant_label_registry"
LABEL_PROVIDER_CONTRACT = "direct-ha-exact-label-registry-v1"
PROVIDER_OPERATIONS = {
    "create": "create_label_registry_entry",
    "update": "update_label_registry_entry",
}
NORMALIZATION_MODEL = "label_registry_entry_projection_v1"
BINDINGS_KEY = "plan_bindings"
UNAUTHORIZED_REASON = "label_registry_write_unauthorized"
# 069A OD-15 rule: an error result to a sent write is not a non-mutation
# proof, so the dispatch stays uncertain and the readback decides.
ERROR_RESULT_REASON = "label_registry_write_error_result"
GENERATED_ID_MISMATCH_REASON = "generated_label_id_mismatch"
NAMESPACE_CHANGED = "label_namespace_changed"
# Design §0: inactive until the seq-13 signing pins the profile.
PENDING_ADMISSION_REASON = "pending_signed_registry_admission"
PENDING_ADMISSION_TEXT = (
    "Label create, edit and assignment are implemented but inactive: pending "
    "signed registry admission (planned: Core registry sequence 13, the "
    "HAMCP-069B + HAMCP-165 + HAMCP-155 batch). No plan is created and "
    "nothing is dispatched."
)

# The reviewed field vocabulary, in Home Assistant field names.
LABEL_FIELDS = ("name", "color", "icon", "description")
_ENTRY_FIELDS = frozenset(
    {"label_id", "created_at", "modified_at", *LABEL_FIELDS}
)
# Core 2026.10 ``components/config/label_registry.py`` lines 13-40.
THEME_COLORS = (
    "primary",
    "accent",
    "disabled",
    "red",
    "pink",
    "purple",
    "deep-purple",
    "indigo",
    "blue",
    "light-blue",
    "cyan",
    "teal",
    "green",
    "light-green",
    "lime",
    "yellow",
    "amber",
    "orange",
    "deep-orange",
    "brown",
    "light-grey",
    "grey",
    "dark-grey",
    "blue-grey",
    "black",
    "white",
)
# Design §3: an ASCII subset whose Core slug is deterministic.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9 _-]{0,48}[A-Za-z0-9])?$")
COLOR_HEX_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
LABEL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
MAX_DESCRIPTION_LENGTH = 255

READ_COMMANDS = ("config/label_registry/list",)
WRITE_COMMANDS = {
    "create": "config/label_registry/create",
    "update": "config/label_registry/update",
}
ALLOWED_COMMANDS = frozenset({*READ_COMMANDS, *WRITE_COMMANDS.values()})
NEVER_SENT_COMMANDS = ("config/label_registry/delete",)
_BINDING_KEYS = frozenset(
    {"operation", "changed_fields", "namespace_fingerprint"}
)

FIELD_ERROR_CODES = {
    "name": "invalid_label_name",
    "color": "invalid_label_color",
    "icon": "invalid_label_icon",
    "description": "invalid_label_description",
}


class LabelRegistryContractError(ValueError):
    """A proposed label change is outside the closed, reviewed contract."""


def provider_evidence() -> dict[str, Any]:
    """Return the immutable, code-owned direct-provider contract."""

    return {
        "provider": LABEL_PROVIDER,
        "provider_contract_model": LABEL_PROVIDER_CONTRACT,
        "provider_operations": dict(PROVIDER_OPERATIONS),
        "transport": "home_assistant_websocket",
        "readback_transport": "home_assistant_websocket_label_registry_list",
        "argument_constraints": {
            "commands": dict(WRITE_COMMANDS),
            "target": "exact_label_id",
            "fields": list(LABEL_FIELDS),
            "label_id": "exact_core_slug_collision_refused",
            "never_sent": list(NEVER_SENT_COMMANDS),
            "arbitrary_payload_allowed": False,
            "fallback_allowed": False,
        },
        "admission": PENDING_ADMISSION_REASON,
        "fallback": "none",
        "fallback_occurred": False,
    }


def normalize_name(name: str) -> str:
    """Core ``normalize_name``: case folding and every space removed."""

    return name.casefold().replace(" ", "")


def predicted_label_id(name: str) -> str:
    """Core ``slugify`` for a name that already matches ``NAME_PATTERN``.

    For that ASCII subset python-slugify lowercases the text and turns each
    run of spaces, hyphens and underscores into one ``_``.  The pattern
    forbids leading and trailing separators, so nothing is trimmed.
    """

    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        raise LabelRegistryContractError("label name is outside the reviewed subset")
    return re.sub(r"[ _-]+", "_", name).lower()


def field_valid(field: str, value: Any) -> bool:
    """Return whether one written field value is inside the reviewed set."""

    if field == "name":
        return isinstance(value, str) and NAME_PATTERN.fullmatch(value) is not None
    if value is None:
        return True
    if field == "color":
        return isinstance(value, str) and (
            value in THEME_COLORS or COLOR_HEX_PATTERN.fullmatch(value) is not None
        )
    if field == "icon":
        return isinstance(value, str) and ICON_PATTERN.fullmatch(value) is not None
    if field == "description":
        return (
            isinstance(value, str)
            and 1 <= len(value) <= MAX_DESCRIPTION_LENGTH
            and _CONTROL_CHARACTERS.search(value) is None
        )
    return False


def _bounded_optional_text(value: Any) -> bool:
    return value is None or (isinstance(value, str) and len(value) <= MAX_TEXT_LENGTH)


def entry_errors(target_id: str, entry: Any) -> list[str]:
    """Validate one entry read from Home Assistant (any existing name)."""

    if not isinstance(entry, dict):
        return ["label registry entry must be an object"]
    errors: list[str] = []
    if set(entry) - _ENTRY_FIELDS - {BINDINGS_KEY}:
        errors.append("label registry entry carries unreviewed fields")
    if entry.get("label_id") != target_id or not isinstance(target_id, str):
        errors.append("label registry entry identity does not match the target")
    name = entry.get("name")
    if not isinstance(name, str) or not 0 < len(name) <= MAX_TEXT_LENGTH:
        errors.append("label registry entry name is missing or unbounded")
    for field in ("color", "icon", "description"):
        if not _bounded_optional_text(entry.get(field)):
            errors.append(f"label registry entry {field} is unbounded")
    return errors


def bindings_errors(bindings: Any) -> list[str]:
    """Validate the closed plan bindings carried by a proposed entry."""

    if not isinstance(bindings, dict) or set(bindings) != _BINDING_KEYS:
        return ["label plan bindings are missing or malformed"]
    errors: list[str] = []
    if bindings["operation"] not in WRITE_COMMANDS:
        errors.append("label plan bindings operation is invalid")
    changed = bindings["changed_fields"]
    if (
        not isinstance(changed, list)
        or not changed
        or changed != sorted(set(changed))
        or not set(changed) <= set(LABEL_FIELDS)
    ):
        errors.append("label plan bindings changed fields are malformed")
    elif bindings["operation"] == "create" and set(changed) != set(LABEL_FIELDS):
        errors.append("a label create sets every reviewed field")
    fingerprint = bindings["namespace_fingerprint"]
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        errors.append("label plan namespace fingerprint is malformed")
    return errors


def proposed_errors(target_id: str, proposed: Any) -> list[str]:
    """Validate a proposed final entry, including its plan bindings.

    Every changed field must be inside the reviewed write vocabulary; an
    unchanged field keeps whatever Home Assistant already holds.
    """

    if not isinstance(target_id, str) or not LABEL_ID_PATTERN.fullmatch(target_id):
        return ["label_registry_entry resource_id must be a label_id"]
    if not isinstance(proposed, dict) or set(proposed) != {
        "label_id",
        BINDINGS_KEY,
        *LABEL_FIELDS,
    }:
        return ["label proposal must contain exactly the reviewed fields"]
    errors = entry_errors(target_id, proposed)
    errors.extend(bindings_errors(proposed[BINDINGS_KEY]))
    if errors:
        return errors
    bindings = proposed[BINDINGS_KEY]
    for field in bindings["changed_fields"]:
        if not field_valid(field, proposed[field]):
            errors.append(f"{FIELD_ERROR_CODES[field]}: {field} is outside the reviewed format")
    if bindings["operation"] == "create":
        try:
            if predicted_label_id(proposed["name"]) != target_id:
                errors.append(
                    "label create target_id must exactly match the Core slug of name"
                )
        except LabelRegistryContractError:
            pass
    return errors


def normalize_entry(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the binding projection; timestamps and bindings are dropped."""

    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise TypeError("label registry entry must be an object")
    return {
        "label_id": entry.get("label_id"),
        **{field: entry.get(field) for field in LABEL_FIELDS},
    }


def without_bindings(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in entry.items() if key != BINDINGS_KEY}


def namespace_fingerprint(names: dict[str, str]) -> str:
    """Hash the registry's ``label_id`` -> normalized-name namespace."""

    return stable_hash(
        sorted([label_id, normalize_name(name)] for label_id, name in names.items())
    )


def changed_fields(
    current: dict[str, Any] | None, proposed: dict[str, Any]
) -> list[str]:
    if current is None:
        return list(LABEL_FIELDS)
    return [field for field in LABEL_FIELDS if proposed.get(field) != current.get(field)]


def write_command(
    action: str,
    target_id: str,
    current: dict[str, Any] | None,
    proposed: dict[str, Any],
) -> dict[str, Any]:
    """Build the one closed command for a planned child."""

    if action == "create":
        if current is not None:
            raise LabelRegistryContractError("a label create has no current entry")
        return {
            "type": WRITE_COMMANDS["create"],
            **{field: proposed.get(field) for field in LABEL_FIELDS},
        }
    if action == "update":
        if current is None:
            raise LabelRegistryContractError("a label update needs its current entry")
        fields = changed_fields(current, proposed)
        if not fields:
            raise LabelRegistryContractError("a label update must change a field")
        return {
            "type": WRITE_COMMANDS["update"],
            "label_id": target_id,
            **{field: proposed.get(field) for field in fields},
        }
    raise LabelRegistryContractError("label action must be create or update")


def command_errors(action: str, target_id: str, command: Any) -> list[str]:
    """Re-check a command's closed shape immediately before it is sent."""

    if not isinstance(command, dict) or command.get("type") != WRITE_COMMANDS.get(action):
        return ["label command type is outside the closed vocabulary"]
    fields = set(command) - {"type", "label_id"}
    if action == "create":
        if "label_id" in command or fields != set(LABEL_FIELDS):
            return ["label create carries exactly the reviewed fields"]
        if predicted_label_id_or_none(command.get("name")) != target_id:
            return ["label create name does not derive the target label_id"]
    else:
        if command.get("label_id") != target_id or not fields or not fields <= set(
            LABEL_FIELDS
        ):
            return ["label update carries the target and changed fields only"]
    if not all(field_valid(field, command[field]) for field in fields):
        return ["label command field is outside the reviewed format"]
    return []


def predicted_label_id_or_none(name: Any) -> str | None:
    try:
        return predicted_label_id(name)
    except LabelRegistryContractError:
        return None


@dataclass(frozen=True)
class NamespaceSnapshot:
    """One registry list read: its namespace fingerprint and exact entries.

    HAMCP-165 review1 M3: the final pre-dispatch check judges the target and
    the namespace from this one read, so drift it observed is never ignored.
    """

    fingerprint: str
    entries: Mapping[str, dict[str, Any]]


class LabelRegistryGateway:
    """Permit only the reviewed list read and the two closed writes."""

    def __init__(self, websocket_client: Any) -> None:
        self.websocket_client = websocket_client

    async def _send(self, command: dict[str, Any]) -> Any:
        if command.get("type") not in ALLOWED_COMMANDS:
            raise LabelRegistryContractError("command outside the closed vocabulary")
        return await self.websocket_client.command(dict(command))

    async def entries(self) -> dict[str, dict[str, Any]]:
        """Return every registry entry by ``label_id``, bounded and exact."""

        value = await self._send({"type": "config/label_registry/list"})
        if not isinstance(value, list) or len(value) > MAX_REGISTRY_LIST_ITEMS:
            raise EntityMetadataProviderError("label_registry_malformed")
        result: dict[str, dict[str, Any]] = {}
        for item in value:
            label_id = item.get("label_id") if isinstance(item, dict) else None
            if (
                not isinstance(label_id, str)
                or label_id in result
                or entry_errors(label_id, item)
            ):
                raise EntityMetadataProviderError("label_registry_malformed")
            result[label_id] = dict(item)
        return result

    async def read_entry(self, label_id: str) -> dict[str, Any] | None:
        if not isinstance(label_id, str) or not LABEL_ID_PATTERN.fullmatch(label_id):
            raise ValueError("label_registry_entry resource_id must be a label_id")
        return (await self.entries()).get(label_id)

    async def namespace(self) -> NamespaceSnapshot:
        entries = await self.entries()
        return NamespaceSnapshot(
            fingerprint=namespace_fingerprint(
                {label_id: entry["name"] for label_id, entry in entries.items()}
            ),
            entries=MappingProxyType(entries),
        )

    async def write(
        self, action: str, label_id: str, command: dict[str, Any]
    ) -> dict[str, Any]:
        """Send the one closed write built by ``write_command``."""

        from .resources import (
            ConfigurationMutationCompletedUnexpectedlyError,
            ConfigurationMutationNotDispatchedError,
        )

        operation = PROVIDER_OPERATIONS.get(action, "unsupported")
        if command_errors(action, label_id, command):
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": operation,
                    "resource_id": label_id,
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
                    "resource_id": label_id,
                    "reason": UNAUTHORIZED_REASON,
                    "provider_response_received": (
                        details.get("provider_response_received") is True
                    ),
                }
            ) from exc
        except HomeAssistantApiError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            if details.get("provider_response_received") is True:
                # Core's ``invalid_info`` or ``unknown_error`` does not prove
                # the registry is unchanged; only the readback settles it.
                raise EntityMetadataUpdateErrorResult(
                    details={
                        "operation": operation,
                        "resource_id": label_id,
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
        returned = result.get("label_id") if isinstance(result, dict) else None
        if returned != label_id:
            if (
                action == "create"
                and isinstance(returned, str)
                and LABEL_ID_PATTERN.fullmatch(returned)
            ):
                # Core created a label under an id the plan did not bind.
                # The approved target is absent, so readback cannot verify.
                raise ConfigurationMutationCompletedUnexpectedlyError(
                    details={
                        "operation": operation,
                        "resource_id": label_id,
                        "reason": GENERATED_ID_MISMATCH_REASON,
                        "unexpected_resource_id": returned,
                        "orphan_risk": True,
                        "provider_response_received": True,
                    }
                )
            raise EntityMetadataProviderError("label_registry_write_identity")
        return {"core_response": {"label_id": returned}}
