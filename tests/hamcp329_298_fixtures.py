"""Offline HAMCP-329/298 created-object fixtures.

A synthetic Home Assistant store for the three created-object kinds the
design adds: UI scenes (``/config/scene/config/<id>`` GET, POST and DELETE)
and the ``input_boolean`` / ``input_number`` storage collections
(``<domain>/list``, ``create``, ``update`` and ``delete``), on top of the
HAMCP-155 automation/script store.  The model follows Core 2026.11.0:

* a scene POST stores ``{"id": <config id>, ...body}``; the reload that
  follows registers ``scene.<slug of name>`` under platform
  ``homeassistant`` with ``unique_id == <config id>``;
* a scene DELETE of a missing key answers HTTP 400 without writing;
  otherwise the key is removed and the post-write hook removes the
  registry entry and the state;
* a helper ``create`` generates the item id from the name, registers
  ``<domain>.<id>`` under platform ``<domain>`` with ``unique_id == <id>``;
* a helper ``delete`` of an unknown id answers ``not_found``; otherwise the
  item, its registry entry and its state are removed.

Every value is synthetic.  Nothing here contacts Home Assistant.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import sys
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.clients.rest import ExpectedHttpStatus  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    GovernanceError,
    HomeAssistantApiError,
)
from ha_mcp_engineering.governance import (  # noqa: E402
    created_object_delete,
    created_object_identity as identity_model,
    scene_configuration as scene_contract,
)

from tests.core_authority_fakes import require_core_authority  # noqa: E402
from tests.hamcp069a_entity_fixtures import registry_entry  # noqa: E402
import tests.hamcp069b_fixtures as fixtures069b  # noqa: E402
from tests.hamcp069b_fixtures import (  # noqa: E402
    PROFILES_069B,
    SyntheticExposureWebSocket,
)
import tests.hamcp155_fixtures as fixtures155  # noqa: E402
from tests.hamcp155_fixtures import (  # noqa: E402
    AdmittingDeleteCore,
    ConfigurationDeleteServiceTestCase,
    PROFILES_155,
    SyntheticConfigurationRest,
    automation_config,
    registry_id,
    script_config,
)


SCENE_PREFIX = "/config/scene/config/"
HELPER_DOMAINS = ("input_boolean", "input_number")
SCENE_PROFILE = created_object_delete.SCENE_PROFILE
HELPER_PROFILE = created_object_delete.HELPER_PROFILE
PROFILES_329_298 = frozenset({SCENE_PROFILE, HELPER_PROFILE})
SCENE_UPDATE_PROFILE = scene_contract.PROFILE
CREATED_AT = 0.5
LAMP = "light.synthetic_lamp"
STRIP = "light.synthetic_strip"
EVENING = "synthetic_evening"
EVENING_ENTITY = "scene.synthetic_evening"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def scene_request(config_id: str = EVENING, **changes: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "action": "create",
        "config_id": config_id,
        "name": "Synthetic evening",
        "entities": {LAMP: {"state": "on"}, STRIP: {"state": "off"}},
    }
    value.update(changes)
    return value


def boolean_config(name: str = "HVAC override") -> dict[str, Any]:
    return {"name": name, "icon": "mdi:toggle-switch"}


def number_config(name: str = "HVAC target") -> dict[str, Any]:
    return {
        "name": name,
        "min": 16,
        "max": 30,
        "step": 0.5,
        "mode": "slider",
        "unit_of_measurement": "°C",
    }


def helper_operation(
    domain: str,
    config: dict[str, Any],
    *,
    operation_id: str = "create_helper",
    action: str = "create",
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "resource_type": "helper",
        "helper_type": domain,
        "action": action,
        "target_id": f"{domain}.{slug(config['name'])}",
        "depends_on": list(depends_on or []),
        "proposed_config": deepcopy(config),
    }


class SyntheticCreatedObjectWebSocket(SyntheticExposureWebSocket):
    """The 069B vocabulary plus the two helper storage collections."""

    def __init__(self) -> None:
        super().__init__()
        self.helpers: dict[str, dict[str, dict[str, Any]]] = {
            domain: {} for domain in HELPER_DOMAINS
        }
        # The REST double owns the state machine; set by the test case.
        self.states: set[str] = set()
        # When False, a helper create never registers its entity.
        self.create_registers_entity = True
        # When True, a helper delete answers ok but removes nothing.
        self.ignore_helper_deletes = False
        # Called with (domain, object_id) just before a helper delete answers.
        self.before_helper_delete: Any = None
        # Called with (domain, object_id) after a helper delete removed it.
        self.after_helper_delete: Any = None
        # When set, a helper create answers with this id instead of its slug.
        self.generated_id: str | None = None

    def helper_commands(self, suffix: str) -> list[dict[str, Any]]:
        return [
            command
            for command in self.commands
            if str(command.get("type")).split("/")[0] in HELPER_DOMAINS
            and str(command.get("type")).endswith(f"/{suffix}")
        ]

    def register_helper(self, domain: str, object_id: str) -> str:
        entity_id = f"{domain}.{object_id}"
        self.entries[entity_id] = registry_entry(
            entity_id,
            platform=domain,
            unique_id=object_id,
            id=registry_id(f"{domain}{object_id}"),
            created_at=CREATED_AT,
        )
        self.states.add(entity_id)
        return entity_id

    def add_helper(self, domain: str, config: dict[str, Any]) -> str:
        """A helper that exists without any governed create (a user's own)."""

        object_id = slug(config["name"])
        self.helpers[domain][object_id] = {"id": object_id, **deepcopy(config)}
        return self.register_helper(domain, object_id)

    def _helper_entry_key(self, domain: str, object_id: str) -> str | None:
        for key, entry in self.entries.items():
            if entry.get("platform") == domain and entry.get("unique_id") == object_id:
                return key
        return None

    async def _answer(self, message: dict[str, Any]) -> Any:
        kind = str(message.get("type"))
        domain, _, verb = kind.partition("/")
        if domain not in HELPER_DOMAINS:
            return await super()._answer(message)
        message = deepcopy(message)
        self.commands.append(message)
        if kind in self.failures:
            raise self.failures[kind]
        store = self.helpers[domain]
        id_field = f"{domain}_id"
        if verb == "list" and set(message) == {"type"}:
            return [deepcopy(item) for item in store.values()]
        if verb == "create":
            config = {key: value for key, value in message.items() if key != "type"}
            object_id = self.generated_id or slug(config["name"])
            store[object_id] = {"id": object_id, **config}
            if self.create_registers_entity:
                self.register_helper(domain, object_id)
            return deepcopy(store[object_id])
        if verb == "update":
            object_id = message[id_field]
            if object_id not in store:
                raise HomeAssistantApiError(
                    details={"code": "not_found", "provider_response_received": True}
                )
            store[object_id] = {
                "id": object_id,
                **{
                    key: value
                    for key, value in message.items()
                    if key not in {"type", id_field}
                },
            }
            return deepcopy(store[object_id])
        if verb == "delete":
            if set(message) != {"type", id_field}:
                raise AssertionError(f"unreviewed helper delete shape: {message}")
            object_id = message[id_field]
            if self.before_helper_delete is not None:
                self.before_helper_delete(domain, object_id)
            if object_id not in store:
                raise HomeAssistantApiError(
                    details={"code": "not_found", "provider_response_received": True}
                )
            if self.ignore_helper_deletes:
                return None
            del store[object_id]
            key = self._helper_entry_key(domain, object_id)
            if key is not None:
                del self.entries[key]
                self.states.discard(key)
            if self.after_helper_delete is not None:
                self.after_helper_delete(domain, object_id)
            # ``StorageCollectionWebsocket.ws_delete_item`` answers no result.
            return None
        raise AssertionError(f"unreviewed helper command: {message}")


class SyntheticCreatedObjectRest(SyntheticConfigurationRest):
    """The 155 automation/script store plus the UI scene config view."""

    def __init__(self, websocket: SyntheticCreatedObjectWebSocket) -> None:
        super().__init__(websocket)
        self.scenes: dict[str, dict[str, Any]] = {}
        websocket.states = self.states
        # When False, a scene POST stores the body but no entity registers.
        self.scene_post_registers_entity = True
        # When set, the entity registers only once this many scene config
        # GETs have been answered after the POST (a slow reload).
        self.scene_registers_after_gets: int | None = None
        self._scene_gets_since_post = 0
        self._pending_scene: tuple[str, dict[str, Any]] | None = None
        # When True, a scene POST answers ok but stores nothing.
        self.ignore_scene_posts = False
        # Merged into the stored scene body by a POST (Core drift).
        self.scene_drift: dict[str, Any] = {}
        # When set, a scene POST raises this instead of answering.
        self.scene_post_failure: Exception | None = None
        # Called with the config id just before a scene POST is answered.
        self.before_scene_post: Any = None
        self.scene_posts: list[tuple[str, dict[str, Any]]] = []

    # -- helpers --------------------------------------------------------
    def scene_deletes(self) -> list[tuple[str, str]]:
        return [
            item
            for item in self.requests
            if item[0] == "DELETE" and item[1].startswith(SCENE_PREFIX)
        ]

    def scene_entity(self, config_id: str) -> str | None:
        for key, entry in self.websocket.entries.items():
            if (
                entry.get("platform") == "homeassistant"
                and entry.get("unique_id") == config_id
                and key.startswith("scene.")
            ):
                return key
        return None

    def register_scene(self, config_id: str, config: dict[str, Any]) -> str:
        entity_id = f"scene.{slug(str(config.get('name')))}"
        self.websocket.entries[entity_id] = registry_entry(
            entity_id,
            platform="homeassistant",
            unique_id=config_id,
            id=registry_id(f"scene{config_id}"),
            original_name=config.get("name"),
            created_at=CREATED_AT,
        )
        self.states.add(entity_id)
        return entity_id

    def add_scene(self, config_id: str, config: dict[str, Any]) -> str:
        """A scene that exists without any governed create (a user's own)."""

        self.scenes[config_id] = {"id": config_id, **deepcopy(config)}
        return self.register_scene(config_id, config)

    def add_member(self, entity_id: str) -> None:
        self.websocket.entries[entity_id] = registry_entry(entity_id)
        self.states.add(entity_id)

    # -- Core model -----------------------------------------------------
    async def request(self, method: str, path: Any, **kwargs: Any) -> Any:
        path = str(path)
        if not path.startswith(SCENE_PREFIX):
            return await super().request(method, path, **kwargs)
        require_core_authority(self, method, path)
        self.requests.append((method, path))
        config_id = path.removeprefix(SCENE_PREFIX)
        if method == "GET":
            key = ("scene_configuration", config_id)
            self.config_gets[key] = self.config_gets.get(key, 0) + 1
            value = (
                deepcopy(self.scenes[config_id])
                if config_id in self.scenes
                else ExpectedHttpStatus(404)
            )
            if self._pending_scene is not None:
                self._scene_gets_since_post += 1
                if self._scene_gets_since_post >= (self.scene_registers_after_gets or 0):
                    pending_id, pending = self._pending_scene
                    self._pending_scene = None
                    self.register_scene(pending_id, pending)
            if self.after_config_get is not None:
                self.after_config_get(
                    "scene_configuration", config_id, self.config_gets[key]
                )
            return value
        if method == "POST":
            body = deepcopy(kwargs["body"])
            self.scene_posts.append((config_id, deepcopy(body)))
            if self.before_scene_post is not None:
                self.before_scene_post(config_id)
            if self.scene_post_failure is not None:
                raise self.scene_post_failure
            if self.ignore_scene_posts:
                return {"result": "ok"}
            # config/scene.py ``_write_value``: the key is stored as "id".
            stored = {"id": config_id, **body, **deepcopy(self.scene_drift)}
            self.scenes[config_id] = stored
            if self.scene_entity(config_id) is None and self.scene_post_registers_entity:
                if self.scene_registers_after_gets:
                    self._pending_scene = (config_id, stored)
                    self._scene_gets_since_post = 0
                else:
                    self.register_scene(config_id, stored)
            return {"result": "ok"}
        if method == "DELETE":
            if kwargs.get("body") is not None:
                raise AssertionError("a scene DELETE carries no body")
            if self.before_delete is not None:
                self.before_delete()
            if self.delete_failure is not None:
                raise self.delete_failure
            if config_id not in self.scenes:
                raise HomeAssistantApiError(
                    details={"status": 400, "provider_response_received": True}
                )
            if self.ignore_deletes:
                return {"result": "ok"}
            del self.scenes[config_id]
            if self.delete_hook_removes_entry:
                entity_id = self.scene_entity(config_id)
                if entity_id is not None:
                    del self.websocket.entries[entity_id]
                    self.states.discard(entity_id)
            if self.after_delete is not None:
                self.after_delete("scene_configuration", config_id)
            return {"result": "ok"}
        raise AssertionError(f"unreviewed scene request: {method} {path}")


class CreatedObjectCore(AdmittingDeleteCore):
    """Core double gating 069B, 155, scene update and the two new profiles."""

    GATED = PROFILES_069B | PROFILES_155 | PROFILES_329_298 | {SCENE_UPDATE_PROFILE}

    def __init__(self, admitted: Any = None) -> None:
        super().__init__(self.GATED if admitted is None else admitted)

    def withdraw(self, *profiles: str) -> None:
        self.admitted -= set(profiles or PROFILES_329_298)

    def admit(self, *profiles: str) -> None:
        self.admitted |= set(profiles or PROFILES_329_298)

    def _admits(self, requirements: Any) -> bool:
        return set(requirements) & self.GATED <= self.admitted


def _with_created_at(original: Any) -> Any:
    def build(entity_id: str, **changes: Any) -> dict[str, Any]:
        changes.setdefault("created_at", CREATED_AT)
        return original(entity_id, **changes)

    return build


class CreatedObjectServiceTestCase(ConfigurationDeleteServiceTestCase):
    """Real service, real F3 runtime, real gateways, synthetic Core store."""

    admitted_profiles: Any = None

    async def asyncSetUp(self) -> None:
        # The inherited set-up builds the gateway, service and runtime around
        # whatever doubles these names resolve to, so the created-object
        # doubles are the only clients any sub-gateway ever holds.
        for owner, name, value in (
            (fixtures155, "registry_entry", _with_created_at(fixtures155.registry_entry)),
            (fixtures069b, "SyntheticExposureWebSocket", SyntheticCreatedObjectWebSocket),
            (fixtures155, "SyntheticConfigurationRest", SyntheticCreatedObjectRest),
            (
                fixtures155,
                "AdmittingDeleteCore",
                lambda *_: CreatedObjectCore(self.admitted_profiles),
            ),
        ):
            patcher = mock.patch.object(owner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.capture_sleeps: list[float] = []

        async def no_capture_sleep(seconds: float) -> None:
            self.capture_sleeps.append(seconds)

        sleeper = mock.patch.object(identity_model.asyncio, "sleep", no_capture_sleep)
        sleeper.start()
        self.addCleanup(sleeper.stop)
        await super().asyncSetUp()
        gateway = self.resource_gateway
        self.rest = self.config_rest
        # The production composition (governance/runtime.py).
        gateway.scene_configuration.core_runtime = self.core
        gateway.scene_create.core_runtime = self.core
        gateway.created_object_delete.core_runtime = self.core
        self.service.scene_configuration_gateway = gateway.scene_configuration
        self.service.scene_create_gateway = gateway.scene_create
        self.service.created_object_delete_gateway = gateway.created_object_delete
        self.config_rest.add_member(LAMP)
        self.config_rest.add_member(STRIP)

    async def restart_runtime(self) -> None:
        await super().restart_runtime()

        async def no_sleep(seconds: float) -> None:
            self.poll_sleeps.append(seconds)

        identities = set(created_object_delete.CAPABILITY_IDENTITIES.values()) | {
            scene_contract.CREATE_CAPABILITY_IDENTITY
        }
        for identity in identities:
            self.runtime.registry.adapter(identity)._sleep = no_sleep

    # -- plans ----------------------------------------------------------
    async def scene_plan(self, config_id: str = EVENING, **changes: Any) -> dict[str, Any]:
        return await self.service.create_scene_configuration_plan(
            scene_request(config_id, **changes)
        )

    async def configuration_plan(self, *operations: dict[str, Any]) -> dict[str, Any]:
        return await self.service.create_configuration_plan(
            title="Synthetic create",
            description="Synthetic governed create",
            operations=list(operations),
        )

    async def applied(self, created: dict[str, Any]) -> dict[str, Any]:
        await self.approve(created)
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        return created

    async def created_scene(self, config_id: str = EVENING, **changes: Any) -> dict[str, Any]:
        return await self.applied(await self.scene_plan(config_id, **changes))

    async def created_helper(
        self, domain: str, config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        config = config or (
            boolean_config() if domain == "input_boolean" else number_config()
        )
        return await self.applied(
            await self.configuration_plan(helper_operation(domain, config))
        )

    async def apply_rollback(self, rollback: dict[str, Any]) -> dict[str, Any]:
        """Approve and apply the reverse plan a ``rollback_change`` returned."""

        plan = self.service.get_plan(rollback["rollback_plan_id"])
        await self.approve(plan)
        return await self.apply(plan)

    async def refused(self, plan_id: str) -> GovernanceError:
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(plan_id)
        return raised.exception

    # -- inspection -----------------------------------------------------
    def identity_values(self, plan_id: str) -> list[dict[str, Any] | None]:
        task = self.service.task_repository.get_for_plan(plan_id)
        return [
            self.runtime.created_identities.get(item["child_id"])
            for item in self.runtime.children.declarations_for_task(task.task_id)
        ]

    def operations(self, plan_id: str) -> list[Any]:
        return list(self.service._load(plan_id).operations)

    def task_id(self, plan_id: str) -> str:
        return self.service.task_repository.get_for_plan(plan_id).task_id

    def rename_entity(self, entity_id: str, new_entity_id: str | None = None) -> str:
        """A user rename: new name (and optionally entity id), same entry."""

        entry = self.ws.entries.pop(entity_id)
        entry["name"] = "Renamed by the user"
        entry["modified_at"] = float(entry["modified_at"]) + 1
        target = new_entity_id or entity_id
        entry["entity_id"] = target
        self.ws.entries[target] = entry
        if target != entity_id and entity_id in self.config_rest.states:
            self.config_rest.states.discard(entity_id)
            self.config_rest.states.add(target)
        return target

    def writes(self) -> list[Any]:
        """Every provider write sent: REST POST/DELETE and helper mutations."""

        return self.config_rest.writes() + [
            command
            for suffix in ("create", "update", "delete")
            for command in self.ws.helper_commands(suffix)
        ]


__all__ = [
    "CREATED_AT",
    "CreatedObjectCore",
    "CreatedObjectServiceTestCase",
    "EVENING",
    "EVENING_ENTITY",
    "HELPER_DOMAINS",
    "HELPER_PROFILE",
    "LAMP",
    "PROFILES_329_298",
    "SCENE_PREFIX",
    "SCENE_PROFILE",
    "SCENE_UPDATE_PROFILE",
    "STRIP",
    "SyntheticCreatedObjectRest",
    "SyntheticCreatedObjectWebSocket",
    "automation_config",
    "boolean_config",
    "helper_operation",
    "number_config",
    "registry_id",
    "scene_request",
    "script_config",
    "slug",
]
