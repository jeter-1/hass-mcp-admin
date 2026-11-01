"""Exact, code-owned scene configuration boundary (HAMCP-071 Part 2, EDIT).

One existing UI-managed scene is the resource, addressed by its exact
config id (the ``id`` key Core stores in ``scenes.yaml``, which is also the
scene entity's registry ``unique_id`` and its ``id`` state attribute; it is
not the entity_id).  The governed state is:

``config_id``, ``entity_id``, ``registry_entry_id`` and ``config`` (the
exact ``GET /api/config/scene/config/<id>`` body).

Core 2026.10 ``config/scene.py`` and ``config/view.py``: GET answers 404 when
no stored entry has the id; POST validates the body with the scene platform
schema, then *replaces* the whole entry as ``{id, name, entities, **body}``
(appending when the id is absent) and schedules a background
``scene.reload``.  Because POST is an upsert, an update binds the existing
entry at planning and re-reads it immediately before the one POST, so an
update never silently creates a scene.

The gateway sends only a closed vocabulary: the config GET, the one config
POST (the full proposed configuration without ``id``), the entity registry
list and get reads, and the state read.  ``scene.turn_on``, ``scene.apply``,
``scene.create``, ``scene.delete``, ``scene.reload`` and the config DELETE
are never sent: saving a scene never activates it.  Every read refuses
before it is sent while the profile is not admitted, and the POST needs the
single-use F3 write permit the configuration adapter installs only after
the durable dispatch intent (HAMCP-071 R1-H2).

Verification scope (HAMCP-071 option C, Josh's decision): verified means
the stored configuration reads back exactly (type-exact canonical JSON) and
the scene's entity registry identity (entity_id, registry entry id,
``homeassistant`` platform, unique_id) is unchanged.  Engineering verifies
the saved scene and its registry identity; it does not verify the scene
Home Assistant has loaded.  No read correlates Core's background reload
with this save, so nothing about the loaded copy is inferred or claimed --
the same scope as the automation family's stored-configuration readback.

There is no fallback provider.  The resource type stays unadmitted until a
signed Core registry sequence pins ``core.scene_configuration_operation``
(the November signing batch, sequence 16).
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
import math
import re
from typing import Any, Iterator

from ..entity_ids import validate_entity_id
from ..errors import AuthorizationError, HomeAssistantApiError
from ..request_context import current_telemetry, mark_provider_send
from .entity_metadata import (
    EntityMetadataProviderError,
    EntityMetadataUpdateErrorResult,
)
from .normalize import stable_hash


RESOURCE_TYPE = "scene_configuration"
ACTION = "update"
CAPABILITY_IDENTITY = "update_scene_configuration_configuration"
CAPABILITY_IDENTITIES = {"update": CAPABILITY_IDENTITY}
PROFILE = "core.scene_configuration_operation"
PROVIDER = "direct_home_assistant_scene_configuration"
PROVIDER_CONTRACT = "direct-ha-exact-scene-configuration-v1"
PROVIDER_OPERATIONS = {"update": "update_scene_configuration"}
REGISTRY_LIST_COMMAND = "config/entity_registry/list"
REGISTRY_GET_COMMAND = "config/entity_registry/get"
ALLOWED_COMMANDS = frozenset({REGISTRY_LIST_COMMAND, REGISTRY_GET_COMMAND})
WRITE_METHOD = "POST"
# Never sent by this boundary: activation, dynamic scenes, deletion, reload.
NEVER_SENT = (
    "DELETE /api/config/scene/config/{id}",
    "scene.apply",
    "scene.create",
    "scene.delete",
    "scene.reload",
    "scene.turn_on",
)
UNAUTHORIZED_REASON = "scene_configuration_write_unauthorized"
# Core answers a malformed body or key with HTTP 400 before it writes.
REJECTED_REASON = "scene_configuration_write_rejected"
# Any other received error to a sent POST is not a non-mutation proof, so the
# dispatch stays uncertain and readback decides.
ERROR_RESULT_REASON = "scene_configuration_write_error_result"
PENDING_ADMISSION_REASON = "pending_signed_registry_admission"
# HAMCP-106 R1-H1: an admitted profile still needs the F3 dispatch window.
F3_CONTEXT_REQUIRED_REASON = "active_f3_execution_required"
# HAMCP-071 R1-H2: the generic Core dispatch authority (installed by every
# Core call window and planning lease) is not a write permit.  The POST
# needs the permit bound to the plan, task, operation, exact target, exact
# command and the recorded dispatch intent.
WRITE_PERMIT_REQUIRED_REASON = "scene_configuration_write_permit_required"
DISPATCH_INTENT = "dispatch_intent_recorded"
# HAMCP-071 option C: what a verified scene result does and does not claim.
VERIFICATION_SCOPE = "stored_configuration_and_registry_identity"
LOADED_STATE = "not_verified"
VERIFICATION_SCOPE_STATEMENT = (
    "Engineering verifies the saved scene and its registry identity; it does "
    "not verify the scene Home Assistant has loaded."
)
CONSUMER_INDEX_NOT_CURRENT = "dependency_index_not_current"
PENDING_ADMISSION_TEXT = (
    "Scene configuration edits are implemented but inactive: pending signed "
    "registry admission (planned: the November Core signing batch, sequence "
    "16). No plan is created and nothing is dispatched."
)

FIELDS = ("name", "icon", "entities")
STATE_KEYS = (
    "config_id",
    "entity_id",
    "registry_entry_id",
    "config",
)
# The closed per-scene keys of Core's homeassistant scene platform schema.
CONFIG_KEYS = frozenset({"id", "name", "icon", "entities", "metadata"})
BODY_KEYS = frozenset({"name", "icon", "entities", "metadata"})
CONFIG_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
REGISTRY_ENTRY_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{1,64}$")
ICON_PATTERN = re.compile(r"^[a-z0-9-]{1,31}:[a-z0-9-]{1,63}$")
MAX_ICON_LENGTH = 64
MAX_NAME_LENGTH = 255
MAX_ENTITIES = 64
MAX_ATTRIBUTES = 64
MAX_KEY_LENGTH = 255
MAX_TEXT_LENGTH = 2048
MAX_LIST_ITEMS = 128
MAX_DEPTH = 4
MAX_CONFIG_BYTES = 65_536
MAX_INTEGER = 2**53
MAX_REGISTRY_LIST_ITEMS = 5_000


@dataclass(frozen=True)
class ScenePermit:
    """One F3 dispatch's authority to send exactly one scene POST."""

    plan_id: str
    task_id: str
    operation_id: str
    config_id: str
    entity_id: str
    arguments_hash: str
    intent: str


class _PermitSlot:
    def __init__(self, permit: ScenePermit) -> None:
        self.permit = permit
        self.consumed = False


_WRITE_PERMIT: ContextVar[_PermitSlot | None] = ContextVar(
    "scene_configuration_write_permit", default=None
)


@contextmanager
def write_permit(permit: ScenePermit) -> Iterator[None]:
    """Install ``permit`` for the one send it authorizes."""

    token = _WRITE_PERMIT.set(_PermitSlot(permit))
    try:
        yield
    finally:
        _WRITE_PERMIT.reset(token)


def verification_scope_fields() -> dict[str, str]:
    """The additive scope fields every scene plan and result carries."""

    return {
        "verification_scope": VERIFICATION_SCOPE,
        "loaded_state": LOADED_STATE,
        "verification_scope_statement": VERIFICATION_SCOPE_STATEMENT,
    }


class SceneConfigurationContractError(ValueError):
    """A scene or a proposed change is outside the reviewed contract."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def provider_evidence() -> dict[str, Any]:
    """Return the immutable, code-owned direct-provider contract."""

    return {
        "provider": PROVIDER,
        "provider_contract_model": PROVIDER_CONTRACT,
        "provider_operations": dict(PROVIDER_OPERATIONS),
        "transport": "home_assistant_rest",
        "readback_transport": "home_assistant_rest_scene_config_get",
        "argument_constraints": {
            "routes": {
                "read": "GET /config/scene/config/{}",
                "write": "POST /config/scene/config/{}",
            },
            "target": "exact_existing_ui_managed_scene_config_id",
            "body": "full_proposed_configuration_without_id",
            "fields": list(FIELDS),
            "unrequested_fields": "sent_exactly_as_stored",
            "never_sent": list(NEVER_SENT),
            "activation": "never",
            "arbitrary_payload_allowed": False,
            "fallback_allowed": False,
        },
        "admission": PENDING_ADMISSION_REASON,
        "fallback": "none",
        "fallback_occurred": False,
    }


def config_id_valid(config_id: Any) -> bool:
    return isinstance(config_id, str) and CONFIG_ID_PATTERN.fullmatch(config_id) is not None


def canonical(value: Any) -> str:
    """Exact JSON form: ``True``, ``1`` and ``1.0`` are three values."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def exactly_equal(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def _json_value_errors(value: Any, depth: int) -> bool:
    """``True`` when ``value`` is outside the bounded JSON value subset."""

    if value is None or type(value) is bool:
        return False
    if type(value) is int:
        return abs(value) > MAX_INTEGER
    if type(value) is float:
        return not math.isfinite(value)
    if type(value) is str:
        return len(value) > MAX_TEXT_LENGTH
    if depth >= MAX_DEPTH:
        return True
    if type(value) is list:
        return len(value) > MAX_LIST_ITEMS or any(
            _json_value_errors(item, depth + 1) for item in value
        )
    if type(value) is dict:
        return len(value) > MAX_ATTRIBUTES or any(
            type(key) is not str
            or not key
            or len(key) > MAX_KEY_LENGTH
            or _json_value_errors(item, depth + 1)
            for key, item in value.items()
        )
    return True


def snapshot_valid(value: Any, *, requested: bool) -> bool:
    """One entity's stored scene value.

    Core accepts a mapping with a string or boolean ``state`` plus attributes,
    or a bare string or boolean state.  A requested value must be the mapping
    form (Core rejects a mapping without ``state``).
    """

    if type(value) is dict:
        state = value.get("state")
        if type(state) is not bool and (
            type(state) is not str or len(state) > MAX_NAME_LENGTH
        ):
            return False
        return not _json_value_errors(value, 1)
    if requested:
        return False
    return type(value) is bool or (
        type(value) is str and len(value) <= MAX_NAME_LENGTH
    )


def requested_name_valid(value: Any) -> bool:
    # Stored exactly: no trim, no case change; blank is refused.
    return isinstance(value, str) and bool(value.strip()) and len(value) <= MAX_NAME_LENGTH


def requested_icon_valid(value: Any) -> bool:
    return value is None or (
        isinstance(value, str)
        and len(value) <= MAX_ICON_LENGTH
        and ICON_PATTERN.fullmatch(value) is not None
    )


def config_errors(config_id: str, config: Any) -> list[str]:
    """Errors in one stored or proposed scene configuration."""

    if type(config) is not dict:
        return ["scene configuration must be an object"]
    errors: list[str] = []
    if not set(config) <= CONFIG_KEYS or not {"id", "name", "entities"} <= set(config):
        return ["scene configuration has unreviewed or missing keys"]
    if config["id"] != config_id or not config_id_valid(config_id):
        errors.append("scene configuration id must match the target")
    name = config["name"]
    if type(name) is not str or len(name) > MAX_NAME_LENGTH:
        errors.append("scene configuration name is invalid")
    if "icon" in config:
        icon = config["icon"]
        # Core ``cv.icon`` requires a colon; a stored icon is read in bounds.
        if type(icon) is not str or ":" not in icon or len(icon) > MAX_NAME_LENGTH:
            errors.append("scene configuration icon is invalid")
    entities = config["entities"]
    if type(entities) is not dict or len(entities) > MAX_ENTITIES:
        errors.append("scene configuration entities are invalid")
    else:
        for entity_id, value in entities.items():
            try:
                validate_entity_id(entity_id)
            except ValueError:
                errors.append("scene configuration entity_id is invalid")
                break
            if not snapshot_valid(value, requested=False):
                errors.append("scene configuration entity value is invalid")
                break
    if "metadata" in config and (
        type(config["metadata"]) is not dict
        or _json_value_errors(config["metadata"], 1)
    ):
        errors.append("scene configuration metadata is invalid")
    if not errors and len(canonical(config).encode("utf-8")) > MAX_CONFIG_BYTES:
        errors.append("scene configuration exceeds the size bound")
    return errors


def state_errors(config_id: str, state: Any) -> list[str]:
    """Errors in one complete governed state (current or proposed)."""

    if type(state) is not dict:
        return ["scene configuration state must be an object"]
    if set(state) != set(STATE_KEYS):
        return ["scene configuration state has unreviewed or missing fields"]
    errors: list[str] = []
    if state["config_id"] != config_id or not config_id_valid(config_id):
        errors.append("scene configuration config_id must match the target")
    entity_id = state["entity_id"]
    try:
        validate_entity_id(entity_id)
        if not entity_id.startswith("scene."):
            raise ValueError
    except ValueError:
        errors.append("scene configuration entity_id is invalid")
    entry_id = state["registry_entry_id"]
    if type(entry_id) is not str or not REGISTRY_ENTRY_ID_PATTERN.fullmatch(entry_id):
        errors.append("scene configuration registry_entry_id is invalid")
    errors.extend(config_errors(config_id, state["config"]))
    return errors


def proposed_errors(config_id: str, proposed: Any) -> list[str]:
    """Errors in a proposed final state; requested values use the strict rules."""

    return state_errors(config_id, proposed)


def normalize_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """The exact binding projection: every state key, compared exactly."""

    if state is None:
        return None
    if type(state) is not dict or set(state) != set(STATE_KEYS):
        raise TypeError("scene configuration state must carry exactly the state keys")
    if state_errors(state["config_id"], state):
        raise TypeError("scene configuration state is outside the reviewed shape")
    # A JSON round trip keeps exact types and drops aliasing to the source.
    return json.loads(canonical({key: state[key] for key in STATE_KEYS}))


def changed_fields(current: dict[str, Any], proposed: dict[str, Any]) -> tuple[str, ...]:
    """Reviewed fields whose proposed configuration differs exactly."""

    before, after = current["config"], proposed["config"]
    return tuple(
        name
        for name in FIELDS
        if (name in before) != (name in after)
        or not exactly_equal(before.get(name), after.get(name))
    )


def write_body(config: dict[str, Any]) -> dict[str, Any]:
    """The POST body: the full configuration without ``id``.

    Core's ``_write_value`` sets the stored ``id`` from the URL key; a body
    ``id`` would override it, so it is never sent.
    """

    return {key: config[key] for key in config if key != "id"}


def write_command(
    target_id: str, current: dict[str, Any] | None, proposed: dict[str, Any]
) -> dict[str, Any]:
    """Build the one closed POST of the full proposed configuration."""

    if current is None:
        raise SceneConfigurationContractError("scene_configuration_current_required")
    if state_errors(target_id, current) or proposed_errors(target_id, proposed):
        raise SceneConfigurationContractError("scene_configuration_state_invalid")
    for key in ("config_id", "entity_id", "registry_entry_id"):
        if current[key] != proposed[key]:
            raise SceneConfigurationContractError("scene_configuration_identity_changed")
    if exactly_equal(current["config"], proposed["config"]):
        raise SceneConfigurationContractError("scene_configuration_no_change")
    if (
        "metadata" in current["config"]
        and not exactly_equal(
            current["config"].get("metadata"), proposed["config"].get("metadata")
        )
    ) or ("metadata" in proposed["config"]) != ("metadata" in current["config"]):
        raise SceneConfigurationContractError("scene_configuration_metadata_changed")
    return {
        "method": WRITE_METHOD,
        "config_id": target_id,
        "body": write_body(proposed["config"]),
    }


def command_errors(command: Any) -> list[str]:
    """Re-check the closed command shape at the provider boundary."""

    if type(command) is not dict or set(command) != {"method", "config_id", "body"}:
        return ["command must carry exactly method, config_id and body"]
    errors: list[str] = []
    if command["method"] != WRITE_METHOD:
        errors.append("command method is outside the closed vocabulary")
    config_id = command["config_id"]
    if not config_id_valid(config_id):
        errors.append("config_id is malformed")
        return errors
    body = command["body"]
    if type(body) is not dict or not set(body) <= BODY_KEYS or "id" in body:
        errors.append("command body carries an unreviewed key")
        return errors
    if config_errors(config_id, {"id": config_id, **body}):
        errors.append("command body is outside the reviewed configuration")
    elif not requested_name_valid(body["name"]) or (
        "icon" in body and not requested_icon_valid(body["icon"])
    ):
        errors.append("command body name or icon is outside the reviewed subset")
    return errors


def _entry_identity(item: Any) -> tuple[Any, Any, Any]:
    if type(item) is not dict:
        raise EntityMetadataProviderError("scene_registry_list_malformed")
    return item.get("entity_id"), item.get("platform"), item.get("unique_id")


class SceneConfigurationGateway:
    """Permit only the reviewed scene reads and the one closed POST."""

    def __init__(self, configuration_gateway: Any) -> None:
        self._configuration = configuration_gateway
        # Set by the governance runtime.  Absent means the profile cannot be
        # shown admitted, so every read and write refuses before it is sent.
        self.core_runtime: Any = None
        # Set by the governance runtime: the dependency snapshot reader that
        # never builds or refreshes (HAMCP-071 R1-M1).
        self.consumer_snapshot_reader: Any = None

    @property
    def rest_client(self) -> Any:
        return self._configuration.rest_client

    @property
    def websocket_client(self) -> Any:
        return self._configuration.websocket_client

    def read_refusal(self) -> str | None:
        """Why no scene read may be sent now (R1-H2): admission withdrawn."""

        core = self.core_runtime
        try:
            admitted = (
                core is not None
                and core.route_status((PROFILE,)).get("available") is True
            )
        except Exception:
            admitted = False
        return None if admitted else PENDING_ADMISSION_REASON

    def _require_read_admission(self) -> None:
        if self.read_refusal() is not None:
            raise SceneConfigurationContractError(PENDING_ADMISSION_REASON)

    def write_refusal(
        self, config_id: str | None = None, command: dict[str, Any] | None = None
    ) -> str | None:
        """Why a write may not be sent now, or ``None`` when it may.

        HAMCP-106 R1-H1: a closed, valid command is not authority.  The write
        needs current Core admission of the scene profile, the request-bound
        dispatch authority, and (HAMCP-071 R1-H2) the unconsumed F3 write
        permit for exactly this target and command.
        """

        if self.read_refusal() is not None:
            return PENDING_ADMISSION_REASON
        slot = _WRITE_PERMIT.get()
        permit = slot.permit if slot is not None else None
        if (
            permit is None
            or slot.consumed
            or permit.intent != DISPATCH_INTENT
            or config_id is None
            or permit.config_id != config_id
            or command is None
            or permit.arguments_hash != stable_hash(command)
        ):
            return WRITE_PERMIT_REQUIRED_REASON
        telemetry = current_telemetry()
        if (
            telemetry is None
            or telemetry.core_dispatch_authorizer is None
            or not telemetry.authorize_core_dispatch()
        ):
            return F3_CONTEXT_REQUIRED_REASON
        return None

    async def _command(self, command: dict[str, Any]) -> Any:
        if command.get("type") not in ALLOWED_COMMANDS:
            raise SceneConfigurationContractError(
                "scene_configuration_command_outside_vocabulary"
            )
        self._require_read_admission()
        return await self.websocket_client.command(dict(command))

    async def read_config(self, config_id: str) -> dict[str, Any] | None:
        """Exact config GET; a 404 is ``None``."""

        from ..clients.paths import rest_path
        from ..clients.rest import ExpectedHttpStatus

        if not config_id_valid(config_id):
            raise ValueError("scene_configuration resource_id must be a config id")
        self._require_read_admission()
        value = await self.rest_client.request(
            "GET",
            rest_path("/config/scene/config/{}", config_id),
            expected_statuses=frozenset({404}),
        )
        if isinstance(value, ExpectedHttpStatus) and value.status == 404:
            return None
        if type(value) is not dict:
            raise EntityMetadataProviderError("scene_config_malformed")
        return value

    async def registry_items(self) -> list[dict[str, Any]]:
        items = await self._command({"type": REGISTRY_LIST_COMMAND})
        if type(items) is not list or len(items) > MAX_REGISTRY_LIST_ITEMS:
            raise EntityMetadataProviderError("scene_registry_list_malformed")
        for item in items:
            _entry_identity(item)
        return items

    @staticmethod
    def registry_entry(
        items: list[dict[str, Any]], config_id: str
    ) -> dict[str, Any] | None:
        """The one scene entry Core's homeassistant platform made for the id.

        A scene entry with that unique_id from any other platform is an
        integration scene and is refused, never edited.
        """

        matches = []
        for item in items:
            entity_id, platform, unique_id = _entry_identity(item)
            if (
                unique_id == config_id
                and isinstance(entity_id, str)
                and entity_id.startswith("scene.")
            ):
                matches.append(item)
        if not matches:
            return None
        if len(matches) != 1:
            raise SceneConfigurationContractError("scene_configuration_target_ambiguous")
        entry = matches[0]
        if entry.get("platform") != "homeassistant":
            raise SceneConfigurationContractError("scene_configuration_not_ui_managed")
        try:
            validate_entity_id(entry.get("entity_id"))
        except ValueError:
            raise EntityMetadataProviderError("scene_registry_entry_malformed") from None
        entry_id = entry.get("id")
        if type(entry_id) is not str or not REGISTRY_ENTRY_ID_PATTERN.fullmatch(entry_id):
            raise EntityMetadataProviderError("scene_registry_entry_malformed")
        return entry

    async def entity_registry_platform(self, entity_id: str) -> str | None:
        """The registry platform of ``entity_id``; ``None`` when unregistered."""

        for item in await self.registry_items():
            if _entry_identity(item)[0] == entity_id:
                platform = item.get("platform")
                return platform if isinstance(platform, str) else ""
        return None

    async def registry_detail(self, entity_id: str) -> dict[str, Any] | None:
        """``config/entity_registry/get``; a 404 is ``None``."""

        validate_entity_id(entity_id)
        try:
            value = await self._command(
                {"type": REGISTRY_GET_COMMAND, "entity_id": entity_id}
            )
        except HomeAssistantApiError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            if details.get("status") == 404:
                return None
            raise
        if type(value) is not dict or value.get("entity_id") != entity_id:
            raise EntityMetadataProviderError("scene_registry_entry_malformed")
        return value

    async def read_entity_state(self, entity_id: str) -> dict[str, Any] | None:
        from ..clients.paths import rest_path
        from ..clients.rest import ExpectedHttpStatus

        validate_entity_id(entity_id)
        self._require_read_admission()
        value = await self.rest_client.request(
            "GET",
            rest_path("/states/{}", entity_id, kinds=("entity_id",)),
            expected_statuses=frozenset({404}),
        )
        if isinstance(value, ExpectedHttpStatus) and value.status == 404:
            return None
        if type(value) is not dict or value.get("entity_id") != entity_id:
            raise EntityMetadataProviderError("scene_entity_state_malformed")
        return value

    async def unknown_entities(self, entity_ids: list[str]) -> list[str]:
        """Requested members present in neither the registry nor the states."""

        registered = {
            _entry_identity(item)[0] for item in await self.registry_items()
        }
        unknown = []
        for entity_id in entity_ids:
            if entity_id in registered:
                continue
            if await self.read_entity_state(entity_id) is None:
                unknown.append(entity_id)
        return unknown

    async def device_class(self, entity_id: str) -> str | None:
        """The effective device class used by the protection classification."""

        from .entity_metadata import effective_device_class

        detail = await self.registry_detail(entity_id)
        if detail is not None:
            value = effective_device_class(detail)
            if value is not None:
                return value
        state = await self.read_entity_state(entity_id)
        attributes = state.get("attributes") if state is not None else None
        value = attributes.get("device_class") if isinstance(attributes, dict) else None
        return value if isinstance(value, str) else None

    async def consumer_evidence(self, entity_id: str) -> dict[str, Any] | None:
        """Automations and scripts that reference the scene, from the index.

        HAMCP-071 R1-M1: only the dependency evidence the index already holds
        is used; nothing is built or refreshed, so no read outside the
        profile's declared vocabulary is sent.  ``None`` means the reader is
        not wired; an absent or no-longer-current snapshot is "unavailable"
        evidence with the ``dependency_index_not_current`` gap, never "no
        consumers".
        """

        from .entity_availability_exposure_planning import _consumer_evidence

        reader = self.consumer_snapshot_reader
        if reader is None:
            return None
        try:
            snapshot = reader()
        except Exception:
            snapshot = None
        evidence = _consumer_evidence(snapshot, entity_id)
        if snapshot is None:
            evidence["evidence_gaps"] = sorted(
                {*evidence["evidence_gaps"], CONSUMER_INDEX_NOT_CURRENT}
            )
        return evidence

    async def read_state(self, config_id: str) -> dict[str, Any] | None:
        """The exact governed state, or ``None`` when no such scene exists."""

        if not config_id_valid(config_id):
            raise ValueError("scene_configuration resource_id must be a config id")
        self._require_read_admission()
        config = await self.read_config(config_id)
        entry = self.registry_entry(await self.registry_items(), config_id)
        if config is None:
            if entry is not None:
                # Loaded by the homeassistant platform from YAML Core's
                # config view does not own (not scenes.yaml).
                raise SceneConfigurationContractError(
                    "scene_configuration_not_ui_managed"
                )
            return None
        if config_errors(config_id, config):
            raise SceneConfigurationContractError(
                "scene_configuration_target_unreviewable"
            )
        if entry is None:
            raise SceneConfigurationContractError("scene_configuration_not_loaded")
        if entry.get("disabled_by") is not None:
            raise SceneConfigurationContractError("scene_configuration_target_disabled")
        # Re-read last: a configuration that changed while the registry
        # identity was read is never bound.  The loaded scene is not read:
        # Engineering does not verify Home Assistant's loaded copy.
        if not exactly_equal(await self.read_config(config_id), config):
            raise EntityMetadataProviderError("scene_configuration_read_unstable")
        return {
            "config_id": config_id,
            "entity_id": entry["entity_id"],
            "registry_entry_id": entry["id"],
            "config": json.loads(canonical(config)),
        }

    async def write(self, config_id: str, command: dict[str, Any]) -> dict[str, Any]:
        """Send the one closed POST built by ``write_command``."""

        from ..clients.paths import UnsafeRequestPathError, rest_path
        from .resources import ConfigurationMutationNotDispatchedError

        operation = PROVIDER_OPERATIONS[ACTION]
        if (
            not config_id_valid(config_id)
            or command_errors(command)
            or command.get("config_id") != config_id
        ):
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": operation,
                    "resource_id": config_id if config_id_valid(config_id) else None,
                    "reason": "closed_payload_violation",
                },
                call_not_sent=True,
            )
        refusal = self.write_refusal(config_id, command)
        if refusal is not None:
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": operation,
                    "resource_id": config_id,
                    "reason": refusal,
                    "reason_code": refusal,
                    "provider_dispatch_occurred": False,
                },
                call_not_sent=True,
            )
        try:
            path = rest_path("/config/scene/config/{}", config_id)
        except UnsafeRequestPathError as exc:
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": operation,
                    "reason": "invalid_identifier",
                    "provider_response_received": False,
                },
                call_not_sent=True,
            ) from exc
        # write_refusal proved an unconsumed permit for exactly this send.
        _WRITE_PERMIT.get().consumed = True  # type: ignore[union-attr]
        try:
            mark_provider_send()  # OD-6(ii): the exact send boundary
            result = await self.rest_client.request(
                WRITE_METHOD, path, body=dict(command["body"])
            )
        except AuthorizationError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": operation,
                    "resource_id": config_id,
                    "reason": UNAUTHORIZED_REASON,
                    "provider_response_received": (
                        details.get("provider_response_received") is True
                    ),
                }
            ) from exc
        except HomeAssistantApiError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            status = details.get("status")
            if details.get("provider_response_received") is True and type(status) is int:
                if 400 <= status < 500:
                    # require_admin and the body/key validation answer before
                    # Core reads or writes the file.
                    raise ConfigurationMutationNotDispatchedError(
                        details={
                            "operation": operation,
                            "resource_id": config_id,
                            "reason": (
                                UNAUTHORIZED_REASON
                                if status in (401, 403)
                                else REJECTED_REASON
                            ),
                            "provider_response_received": True,
                            "provider_status": status,
                        }
                    ) from exc
                raise EntityMetadataUpdateErrorResult(
                    details={
                        "operation": operation,
                        "resource_id": config_id,
                        "reason": ERROR_RESULT_REASON,
                        "provider_response_received": True,
                        "provider_status": status,
                    }
                ) from exc
            raise
        # A received success is not proof of the stored result; the exact
        # readback decides.
        value = result.get("result") if type(result) is dict else None
        return {"core_response": {"result": value if value == "ok" else None}}


__all__ = [
    "ACTION",
    "CAPABILITY_IDENTITIES",
    "CAPABILITY_IDENTITY",
    "FIELDS",
    "NEVER_SENT",
    "PENDING_ADMISSION_REASON",
    "PROFILE",
    "PROVIDER",
    "PROVIDER_CONTRACT",
    "LOADED_STATE",
    "REJECTED_REASON",
    "ScenePermit",
    "RESOURCE_TYPE",
    "STATE_KEYS",
    "SceneConfigurationContractError",
    "SceneConfigurationGateway",
    "UNAUTHORIZED_REASON",
    "VERIFICATION_SCOPE",
    "VERIFICATION_SCOPE_STATEMENT",
    "WRITE_PERMIT_REQUIRED_REASON",
    "canonical",
    "changed_fields",
    "command_errors",
    "config_errors",
    "config_id_valid",
    "exactly_equal",
    "normalize_state",
    "proposed_errors",
    "provider_evidence",
    "requested_icon_valid",
    "requested_name_valid",
    "snapshot_valid",
    "state_errors",
    "verification_scope_fields",
    "write_body",
    "write_command",
    "write_permit",
]
