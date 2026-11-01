"""Offline HAMCP-071 scene configuration fixtures.

A synthetic Home Assistant scene configuration store, entity registry and
state machine behind the real ``ConfigurationResourceGateway`` and
``SceneConfigurationGateway``, so end-to-end tests exercise the shipped
planner, policy, F3 runtime and the one closed ``POST
/api/config/scene/config/{id}`` without any live dependency.

Shapes follow the architect's read-only observation (2026-10-30): config ids
are timestamp-like strings that are not entity_ids, ``metadata`` is ``{}``
and a light member stores its full attribute snapshot.  Every id, name and
entity_id here is synthetic.

The store answers as Core 2026.10 ``config/scene.py`` and ``config/view.py``
do: GET of an absent key is 404; POST validates the closed scene schema
(400 otherwise), replaces the whole entry as ``{id: key, name, entities,
**body}`` (appending an absent key) and then reloads every scene, which
updates the scene's registry ``original_name``/``original_icon`` and its
state attributes.  DELETE, and every scene service, fails the test.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from ha_mcp_engineering.clients.rest import ExpectedHttpStatus
from ha_mcp_engineering.errors import HomeAssistantApiError
from ha_mcp_engineering.governance import scene_configuration as contract

from tests.core_authority_fakes import require_core_authority
from tests.hamcp069a_entity_fixtures import registry_entry
from tests.hamcp069b_fixtures import exposure_snapshot, finding
from tests.hamcp165_fixtures import (
    LabelAdmittingCore,
    LabelServiceTestCase,
    SyntheticLabelWebSocket,
)


REGISTRY_LIST = contract.REGISTRY_LIST_COMMAND
CONFIG_PREFIX = "/config/scene/config/"
SCENE_SCHEMA_KEYS = frozenset({"id", "name", "icon", "entities", "metadata"})
_ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")

LOUNGE = "1700000000001"
LOUNGE_ENTITY = "scene.synthetic_lounge_dim"
LEAVING = "1700000000002"
LEAVING_ENTITY = "scene.synthetic_leaving"
INTEGRATION_ENTITY = "scene.synthetic_presence_simulation"
YAML_ID = "1700000000009"
YAML_ENTITY = "scene.synthetic_yaml_scene"
LAMP = "light.synthetic_lounge_lamp"
STRIP = "light.synthetic_lounge_strip"
GARAGE = "cover.synthetic_garage_door"
FRONT_LOCK = "lock.synthetic_front_door"
FAN = "fan.synthetic_ceiling_fan"
STATE_ONLY = "sensor.synthetic_state_only"
CONSUMER = "synthetic_evening_routine"


def lamp_snapshot(**changes: Any) -> dict[str, Any]:
    """A full stored light attribute snapshot, as the UI scene editor saves."""

    value = {
        "state": "on",
        "brightness": 77,
        "color_mode": "color_temp",
        "color_temp": 370,
        "color_temp_kelvin": 2702,
        "min_mireds": 153,
        "max_mireds": 500,
        "min_color_temp_kelvin": 2000,
        "max_color_temp_kelvin": 6535,
        "supported_color_modes": ["color_temp", "hs"],
        "hs_color": [28.391, 65.659],
        "rgb_color": [255, 167, 87],
        "xy_color": [0.524, 0.388],
        "friendly_name": "Synthetic lounge lamp",
        "supported_features": 40,
    }
    value.update(changes)
    return value


def strip_snapshot(**changes: Any) -> dict[str, Any]:
    value = {"state": "off", "friendly_name": "Synthetic lounge strip",
             "supported_features": 0}
    value.update(changes)
    return value


def lounge_config() -> dict[str, Any]:
    return {
        "id": LOUNGE,
        "name": "Synthetic Lounge dim",
        "icon": "mdi:floor-lamp",
        "entities": {LAMP: lamp_snapshot(), STRIP: strip_snapshot()},
        "metadata": {},
    }


def leaving_config() -> dict[str, Any]:
    return {
        "id": LEAVING,
        "name": "Synthetic leaving",
        "entities": {
            GARAGE: {"state": "closed", "friendly_name": "Synthetic garage door"},
            FRONT_LOCK: {"state": "locked"},
        },
    }


def scene_registry_entry(entity_id: str, config_id: str, config: dict | None,
                         **changes: Any) -> dict[str, Any]:
    object_id = entity_id.split(".", 1)[1]
    value = registry_entry(
        entity_id,
        id=f"scn{object_id.replace('_', '')}"[:32],
        platform="homeassistant",
        unique_id=config_id,
        original_name=(config or {}).get("name"),
        original_icon=(config or {}).get("icon"),
    )
    value.update(changes)
    return value


class SyntheticSceneWebSocket(SyntheticLabelWebSocket):
    """The 069A registry plus ``config/entity_registry/list``."""

    async def _answer(self, message: dict[str, Any]) -> Any:
        kind = message.get("type")
        if isinstance(kind, str) and (
            kind.startswith("scene/") or kind == "call_service"
        ):
            raise AssertionError(f"never-sent command was sent: {kind}")
        if kind == REGISTRY_LIST:
            self.commands.append(deepcopy(message))
            if kind in self.failures:
                raise self.failures[kind]
            return [deepcopy(entry) for entry in self.entries.values()]
        return await super()._answer(message)


class SyntheticSceneRest:
    """Core-faithful scene config view and state machine."""

    def __init__(self, websocket: SyntheticSceneWebSocket) -> None:
        self.websocket = websocket
        # Insertion-ordered list, as scenes.yaml is.
        self.scenes: list[dict[str, Any]] = []
        self.states: dict[str, dict[str, Any]] = {}
        self.requests: list[tuple[str, str]] = []
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.failures: dict[str, Exception] = {}
        # When set, POST answers "ok" but nothing is stored.
        self.ignore_writes = False
        # When set, POST stores the body merged with these keys.
        self.drift: dict[str, Any] = {}
        # When set, the post-write reload fails (Core only logs it).  The
        # loaded scene then keeps its old members; Engineering never observes
        # it (HAMCP-071 option C).
        self.reload_fails = False
        # Each state add gets a fresh ``last_changed`` (core.py async_set_internal).
        self._generation = 0
        # The restored scene state value: the last activation time.
        self.activated_at = "2026-10-01T00:00:00+00:00"
        # Called once after the next config GET (between planning reads).
        self.after_config_read: Any = None

    # -- store helpers --------------------------------------------------
    def config(self, config_id: str) -> dict[str, Any] | None:
        for value in self.scenes:
            if value.get("id") == config_id:
                return value
        return None

    def add_scene(self, entity_id: str, config: dict[str, Any], *,
                  registry: bool = True, **registry_changes: Any) -> None:
        self.scenes.append(deepcopy(config))
        if registry:
            self.websocket.entries[entity_id] = scene_registry_entry(
                entity_id, config["id"], config, **registry_changes
            )
        self._load(entity_id, config)

    def add_member(self, entity_id: str, state: str = "on", *,
                   registry: bool = True, **attributes: Any) -> None:
        if registry:
            self.websocket.entries[entity_id] = registry_entry(
                entity_id,
                original_device_class=attributes.get("device_class"),
            )
        self.states[entity_id] = {
            "entity_id": entity_id, "state": state, "attributes": dict(attributes)
        }

    def set_external(self, config_id: str, *, reload: bool = True,
                     **changes: Any) -> None:
        """Simulate a scene edit saved outside Engineering."""

        value = self.config(config_id)
        for key, item in changes.items():
            if item is None:
                value.pop(key, None)
            else:
                value[key] = deepcopy(item)
        if reload:
            self._reload()

    def posted_bodies(self) -> list[dict[str, Any]]:
        return [body for _, body in self.posts]

    # -- Core model -----------------------------------------------------
    def _scene_entity(self, config_id: str) -> str | None:
        for entry in self.websocket.entries.values():
            if entry.get("platform") == "homeassistant" and entry.get(
                "unique_id"
            ) == config_id and str(entry["entity_id"]).startswith("scene."):
                return entry["entity_id"]
        return None

    def _load(self, entity_id: str, config: dict[str, Any]) -> None:
        entry = self.websocket.entries.get(entity_id)
        if entry is not None:
            entry["original_name"] = config.get("name")
            entry["original_icon"] = config.get("icon")
        self._generation += 1
        self.states[entity_id] = {
            "entity_id": entity_id,
            "state": self.activated_at,
            "last_changed": f"2026-10-01T00:{self._generation // 60:02d}:"
            f"{self._generation % 60:02d}+00:00",
            "attributes": {
                "entity_id": list(config.get("entities", {})),
                "id": config.get("id"),
                "friendly_name": config.get("name"),
                **({"icon": config["icon"]} if "icon" in config else {}),
            },
        }

    def _reload(self) -> None:
        """``scene.reload``: every UI scene is re-added from the store."""

        for value in self.scenes:
            entity_id = self._scene_entity(value.get("id"))
            if entity_id is not None:
                self._load(entity_id, value)

    @staticmethod
    def _invalid(message: str) -> HomeAssistantApiError:
        return HomeAssistantApiError(
            message, details={"status": 400, "provider_response_received": True}
        )

    def _validate(self, body: Any) -> None:
        if type(body) is not dict:
            raise self._invalid("Message malformed")
        if not set(body) <= SCENE_SCHEMA_KEYS or not {"name", "entities"} <= set(body):
            raise self._invalid("Message malformed: extra or missing keys")
        if "icon" in body and (type(body["icon"]) is not str or ":" not in body["icon"]):
            raise self._invalid("Message malformed: icon")
        entities = body["entities"]
        if type(entities) is not dict:
            raise self._invalid("Message malformed: entities")
        for entity_id, value in entities.items():
            if not _ENTITY_ID.fullmatch(str(entity_id)):
                raise self._invalid("Message malformed: entity_id")
            if type(value) is dict and "state" not in value:
                raise self._invalid("Message malformed: state")

    def _post(self, config_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._validate(body)
        if self.ignore_writes:
            return {"result": "ok"}
        # config/scene.py ``_write_value``.
        updated: dict[str, Any] = {"id": config_id}
        for key in ("name", "entities"):
            if key in body:
                updated[key] = deepcopy(body[key])
        updated.update(deepcopy(body))
        updated.update(deepcopy(self.drift))
        for index, value in enumerate(self.scenes):
            if not value.get("id"):
                value["id"] = f"assigned{index}"
            elif value["id"] == config_id:
                self.scenes[index] = updated
                break
        else:
            self.scenes.append(updated)
        if not self.reload_fails:
            self._reload()
        return {"result": "ok"}

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        require_core_authority(self, method, str(path))
        self.requests.append((method, path))
        if path.startswith(CONFIG_PREFIX):
            config_id = path.removeprefix(CONFIG_PREFIX)
            if method == "GET":
                if "GET" in self.failures:
                    raise self.failures["GET"]
                value = deepcopy(self.config(config_id))
                if self.after_config_read is not None:
                    hook, self.after_config_read = self.after_config_read, None
                    hook()
                return ExpectedHttpStatus(404) if value is None else value
            if method == "POST":
                self.posts.append((config_id, deepcopy(kwargs.get("body"))))
                if "POST" in self.failures:
                    raise self.failures["POST"]
                return self._post(config_id, kwargs.get("body"))
            raise AssertionError(f"never-sent scene request: {method} {path}")
        if method == "GET" and path.startswith("/states/"):
            entity_id = path.removeprefix("/states/")
            value = self.states.get(entity_id)
            return ExpectedHttpStatus(404) if value is None else deepcopy(value)
        if "/services/scene/" in path or "/services/" in path:
            raise AssertionError(f"never-sent service call: {method} {path}")
        raise AssertionError(f"unreviewed REST request: {method} {path}")


class SceneAdmittingCore(LabelAdmittingCore):
    """Core double gating only the scene configuration profile."""

    GATED = frozenset({contract.PROFILE})


class SceneServiceTestCase(LabelServiceTestCase):
    """Real service, real F3 runtime, real gateways, synthetic scenes."""

    core_admits = True

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.ws = SyntheticSceneWebSocket()
        self.scene_rest = SyntheticSceneRest(self.ws)
        self.rest = self.scene_rest
        gateway = self.resource_gateway
        gateway.websocket_client = self.ws
        gateway.rest_client = self.scene_rest
        self.exposure = exposure_snapshot(
            findings=(finding(LOUNGE_ENTITY, CONSUMER, relation="action_entity"),)
        )
        self.exposure_reads: list[bool] = []

        # R1-M1: a refreshing read is outside the scene profile; only the
        # cached snapshot reader may be used.
        self.refreshing_reads: list[bool] = []

        async def exposure_reader(*, fenced: bool) -> Any:
            self.refreshing_reads.append(fenced)
            return self.exposure

        def cached_reader() -> Any:
            self.exposure_reads.append("cached")
            return self.exposure

        gateway.availability_exposure_snapshot_reader = exposure_reader
        gateway.scene_configuration.consumer_snapshot_reader = cached_reader
        self.service.scene_configuration_gateway = gateway.scene_configuration
        self.core = SceneAdmittingCore(
            SceneAdmittingCore.GATED if self.core_admits else ()
        )
        # The production composition (governance/runtime.py).
        gateway.scene_configuration.core_runtime = self.core
        self.seed()
        await self.restart_runtime()

    def seed(self) -> None:
        rest = self.scene_rest
        rest.add_member(LAMP, "on", device_class=None)
        rest.add_member(STRIP, "off")
        rest.add_member(GARAGE, "closed", device_class="garage")
        rest.add_member(FRONT_LOCK, "locked")
        rest.add_member(FAN, "off")
        # Present in the state machine only (no registry entry).
        rest.add_member(STATE_ONLY, "12", registry=False)
        rest.add_scene(LOUNGE_ENTITY, lounge_config())
        rest.add_scene(LEAVING_ENTITY, leaving_config())
        # An integration scene: not in the config store, foreign platform.
        self.ws.entries[INTEGRATION_ENTITY] = scene_registry_entry(
            INTEGRATION_ENTITY, "presence_sim_1", None,
            platform="synthetic_integration",
        )
        rest.states[INTEGRATION_ENTITY] = {
            "entity_id": INTEGRATION_ENTITY, "state": "unknown", "attributes": {}
        }
        # A homeassistant-platform scene loaded from YAML outside scenes.yaml.
        self.ws.entries[YAML_ENTITY] = scene_registry_entry(
            YAML_ENTITY, YAML_ID, {"name": "Synthetic YAML scene"}
        )

    async def create(self, **arguments: Any) -> dict[str, Any]:  # type: ignore[override]
        return await self.service.create_scene_configuration_plan(arguments)

    async def approved(self, **arguments: Any) -> dict[str, Any]:  # type: ignore[override]
        created = await self.create(**arguments)
        await self.approve(created)
        return created

    def outcomes(self, task_id: str) -> list[str]:
        return [
            record.normalized_outcome
            for record in self.child_records(task_id)
            if record is not None
        ]

    def writes(self) -> list[tuple[str, dict[str, Any]]]:
        return list(self.scene_rest.posts)

    def assert_never_sent(self) -> None:
        methods = {
            (method, path) for method, path in self.scene_rest.requests
            if not (
                (method == "GET" and (path.startswith(CONFIG_PREFIX)
                                      or path.startswith("/states/")))
                or (method == "POST" and path.startswith(CONFIG_PREFIX))
            )
        }
        self.assertEqual(methods, set())
        self.assertEqual(
            self.ws.command_types()
            - {REGISTRY_LIST, contract.REGISTRY_GET_COMMAND},
            set(),
        )


__all__ = [
    "CONSUMER",
    "FAN",
    "FRONT_LOCK",
    "GARAGE",
    "INTEGRATION_ENTITY",
    "LAMP",
    "LEAVING",
    "LEAVING_ENTITY",
    "LOUNGE",
    "LOUNGE_ENTITY",
    "REGISTRY_LIST",
    "STATE_ONLY",
    "STRIP",
    "SceneAdmittingCore",
    "SceneServiceTestCase",
    "SyntheticSceneRest",
    "SyntheticSceneWebSocket",
    "YAML_ENTITY",
    "YAML_ID",
    "lamp_snapshot",
    "leaving_config",
    "lounge_config",
    "scene_registry_entry",
    "strip_snapshot",
]
