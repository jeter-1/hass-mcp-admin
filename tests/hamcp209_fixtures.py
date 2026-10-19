"""HAMCP-209 synthetic Home Assistant model for dependency-index source tests.

The model mirrors the pinned Core 2026.10.x behaviour the design note cites:

* REST ``GET /config/automation/config/{id}`` takes one mutation lock and
  re-parses the whole automations file per read (cost grows with the
  automation count); ``POST`` holds the same lock while it writes.
* WebSocket ``automation/config`` returns the loaded ``raw_config`` from
  memory, and ``search/related`` reports ``automation_blueprint`` for a
  blueprint-based automation. Neither takes the lock.

Every value is synthetic. Nothing here contacts a live system.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from copy import deepcopy
import time
from typing import Any


SYNTHETIC_VERSION = "2026.10.1"
BLUEPRINT_PATH = "synthetic/motion_light.yaml"


def blueprint_source() -> dict[str, Any]:
    """Parsed form ``_read_blueprint_with_status`` returns for the blueprint."""

    return {
        "blueprint": {
            "name": "Synthetic motion light",
            "domain": "automation",
            "input": {"motion_entity": {}, "light_target": {}},
        },
        "trigger": [
            {"platform": "state", "entity_id": {"__blueprint_input__": "motion_entity"}}
        ],
        "action": [
            {"service": "light.turn_on", "target": {"__blueprint_input__": "light_target"}}
        ],
    }


def read_blueprint(path):
    if path == BLUEPRINT_PATH:
        return blueprint_source(), None
    return None, "blueprint_source_unavailable"


def representative_automations() -> list[dict[str, Any]]:
    """Plain, templated, dynamic, label, blueprint and id-less automations.

    ``stored`` is the automations.yaml entry the REST route returns; ``loaded``
    is Core's ``raw_config`` (the substituted config for a blueprint).
    """

    plain = {
        "id": "synthetic_plain",
        "alias": "Synthetic plain",
        "trigger": [{"platform": "state", "entity_id": "binary_sensor.synthetic_door"}],
        "action": [
            {"service": "light.turn_on", "target": {"entity_id": "light.synthetic_kitchen"}}
        ],
    }
    templated = {
        "id": "synthetic_templated",
        "alias": "Synthetic templated",
        "trigger": [{"platform": "time_pattern", "minutes": "/5"}],
        "condition": [
            {
                "condition": "template",
                "value_template": "{{ is_state('sensor.synthetic_temperature', 'on') }}",
            }
        ],
        "action": [
            {
                "service": "climate.set_temperature",
                "target": {"entity_id": "climate.synthetic_hall"},
                "data": {"temperature": "{{ states('input_number.synthetic_setpoint') | float }}"},
            }
        ],
    }
    dynamic = {
        "id": "synthetic_dynamic",
        "alias": "Synthetic dynamic",
        "trigger": [{"platform": "state", "entity_id": "input_select.synthetic_mode"}],
        "action": [
            {
                "service": "{{ 'light.turn_' ~ trigger.to_state.state }}",
                "target": {"entity_id": "{{ states('input_text.synthetic_target') }}"},
            }
        ],
    }
    labelled = {
        "id": "synthetic_label",
        "alias": "Synthetic label",
        "trigger": [{"platform": "state", "entity_id": "binary_sensor.synthetic_door"}],
        "action": [{
            "service": "light.turn_off",
            "target": {"entity_id": "{{ label_entities('synthetic_downstairs') }}"},
        }],
    }
    blueprint_stored = {
        "id": "synthetic_blueprint",
        "alias": "Synthetic blueprint",
        "use_blueprint": {
            "path": BLUEPRINT_PATH,
            "input": {
                "motion_entity": "binary_sensor.synthetic_motion",
                "light_target": {"entity_id": "light.synthetic_hall"},
            },
        },
    }
    blueprint_loaded = {
        "id": "synthetic_blueprint",
        "alias": "Synthetic blueprint",
        "trigger": [{"platform": "state", "entity_id": "binary_sensor.synthetic_motion"}],
        "action": [
            {"service": "light.turn_on", "target": {"entity_id": "light.synthetic_hall"}}
        ],
    }
    broken_blueprint = {
        "id": "synthetic_blueprint_missing",
        "alias": "Synthetic missing blueprint",
        "use_blueprint": {
            "path": "synthetic/deleted.yaml",
            "input": {"motion_entity": "binary_sensor.synthetic_motion"},
        },
    }
    return [
        record("automation.synthetic_plain", plain),
        record("automation.synthetic_templated", templated),
        record("automation.synthetic_dynamic", dynamic),
        record("automation.synthetic_label", labelled),
        record(
            "automation.synthetic_blueprint", blueprint_stored,
            loaded=blueprint_loaded, blueprint=BLUEPRINT_PATH,
        ),
        # Core's UnavailableAutomationEntity: validation failed, the
        # blueprint reference is not reported and raw_config is ambiguous.
        record(
            "automation.synthetic_blueprint_missing", broken_blueprint,
            state="unavailable", loaded={"id": "synthetic_blueprint_missing"},
        ),
        # A YAML automation without ``id`` has no ``id`` attribute.
        record(
            "automation.synthetic_without_id", None, automation_id=None,
            loaded={"alias": "Synthetic without id", "trigger": [], "action": []},
        ),
    ]


def generated_automations(count: int) -> list[dict[str, Any]]:
    """``count`` plain automations, each referencing its own synthetic light."""

    items = []
    for index in range(count):
        automation_id = f"synthetic_bulk_{index:04d}"
        items.append(record(
            f"automation.synthetic_bulk_{index:04d}",
            {
                "id": automation_id,
                "alias": f"Synthetic bulk {index}",
                "trigger": [{"platform": "state", "entity_id": f"binary_sensor.synthetic_motion_{index % 7}"}],
                "condition": [
                    {"condition": "template", "value_template": "{{ is_state('sun.sun', 'below_horizon') }}"}
                ],
                "action": [
                    {"service": "light.turn_on", "target": {"entity_id": f"light.synthetic_bulk_{index:04d}"}}
                ],
            },
        ))
    return items


def record(
    entity_id: str,
    stored: dict[str, Any] | None,
    *,
    automation_id: Any = "",
    loaded: dict[str, Any] | None = None,
    blueprint: str | None = None,
    state: str = "on",
) -> dict[str, Any]:
    if automation_id == "":
        automation_id = stored.get("id") if stored else None
    return {
        "entity_id": entity_id,
        "id": automation_id,
        "state": state,
        "stored": deepcopy(stored),
        "loaded": deepcopy(loaded if loaded is not None else stored),
        "blueprint": blueprint,
    }


class SyntheticHomeAssistant:
    """Loaded and stored automation configuration with Core's lock model."""

    def __init__(
        self,
        automations: list[dict[str, Any]],
        *,
        parse_seconds_per_automation: float = 0.0,
        websocket_seconds_per_command: float = 0.0,
    ) -> None:
        self.automations = {item["entity_id"]: item for item in automations}
        # Stored entries without a loaded entity (written, not reloaded yet).
        self.stored_only: dict[str, dict[str, Any]] = {}
        self.parse_seconds_per_automation = parse_seconds_per_automation
        self.websocket_seconds_per_command = websocket_seconds_per_command
        self.mutation_lock = asyncio.Lock()
        self.rest_counts: Counter[str] = Counter()
        self.lock_waits: list[float] = []
        self.batch_calls: list[list[dict[str, Any]]] = []
        self.batch_failure: Exception | None = None
        self.batch_overrides: dict[tuple[str, str], tuple[bool, Any]] = {}
        self.rest = SyntheticRest(self)
        self.websocket = SyntheticWebSocket(self)
        self.websocket_without_batch = SyntheticWebSocketWithoutBatch(self)

    # -- model ------------------------------------------------------------

    def stored_entries(self) -> dict[str, dict[str, Any]]:
        entries = {
            str(item["id"]): item["stored"]
            for item in self.automations.values()
            if item["id"] and item["stored"] is not None
        }
        entries.update(self.stored_only)
        return entries

    def states(self) -> list[dict[str, Any]]:
        targets = sorted({
            "binary_sensor.synthetic_door", "binary_sensor.synthetic_motion",
            "light.synthetic_kitchen", "light.synthetic_hall", "climate.synthetic_hall",
            "sensor.synthetic_temperature", "input_number.synthetic_setpoint",
            "input_select.synthetic_mode", "input_text.synthetic_target",
            "light.synthetic_downstairs_one",
        })
        rows = [
            {"entity_id": entity_id, "state": "on", "attributes": {"friendly_name": entity_id}}
            for entity_id in targets
        ]
        for item in self.automations.values():
            attributes = {"friendly_name": item["entity_id"]}
            if item["id"]:
                attributes["id"] = item["id"]
            rows.append({"entity_id": item["entity_id"], "state": item["state"], "attributes": attributes})
        return rows

    def registry(self) -> list[dict[str, Any]]:
        rows = []
        for row in self.states():
            entity_id = row["entity_id"]
            rows.append({
                "entity_id": entity_id,
                "platform": entity_id.split(".", 1)[0] if entity_id.startswith("automation.") else "synthetic",
                "unique_id": f"unique_{entity_id.replace('.', '_')}",
                "device_id": None,
                "area_id": None,
                "labels": ["synthetic_downstairs"] if entity_id == "light.synthetic_downstairs_one" else [],
                "disabled_by": None,
                "hidden_by": None,
            })
        return rows

    async def parse_file(self) -> None:
        """Core re-parses the whole file for every stored read or write."""

        cost = self.parse_seconds_per_automation * len(self.stored_entries())
        if cost:
            await asyncio.sleep(cost)

    def reload(self, entity_id: str) -> None:
        """What Core's post-write reload does: loaded catches up with stored."""

        item = self.automations[entity_id]
        item["loaded"] = deepcopy(item["stored"])


class SyntheticRest:
    def __init__(self, ha: SyntheticHomeAssistant) -> None:
        self.ha = ha

    async def request(
        self, method, path, body=None, raw=False,
        expected_statuses=frozenset(), *, logbook_read=False,
    ):
        from ha_mcp_engineering.clients.rest import ExpectedHttpStatus
        from ha_mcp_engineering.errors import HomeAssistantApiError

        path = str(path)
        if method == "GET" and path == "/config":
            self.ha.rest_counts["config"] += 1
            return {"version": SYNTHETIC_VERSION}
        if method == "GET" and path == "/states":
            self.ha.rest_counts["states"] += 1
            return deepcopy(self.ha.states())
        prefix = "/config/automation/config/"
        if not path.startswith(prefix):
            raise AssertionError(f"unexpected synthetic route {method} {path}")
        automation_id = path[len(prefix):]
        queued = time.perf_counter()
        async with self.ha.mutation_lock:
            self.ha.lock_waits.append(time.perf_counter() - queued)
            if method == "POST":
                self.ha.rest_counts["automation_config_write"] += 1
                await self.ha.parse_file()
                for item in self.ha.automations.values():
                    if str(item["id"]) == automation_id:
                        item["stored"] = deepcopy(body)
                        break
                else:
                    self.ha.stored_only[automation_id] = deepcopy(body)
                return {"result": "ok"}
            self.ha.rest_counts["automation_config_read"] += 1
            await self.ha.parse_file()
            stored = self.ha.stored_entries().get(automation_id)
        if stored is not None:
            return deepcopy(stored)
        if 404 in expected_statuses:
            return ExpectedHttpStatus(404)
        raise HomeAssistantApiError(details={"status": 404})


class SyntheticWebSocket:
    def __init__(self, ha: SyntheticHomeAssistant) -> None:
        self.ha = ha

    async def command(self, payload):
        kind = payload.get("type")
        if kind == "config/entity_registry/list":
            return deepcopy(self.ha.registry())
        if kind == "config/label_registry/list":
            return [{"label_id": "synthetic_downstairs", "name": "Synthetic downstairs"}]
        raise AssertionError(f"unexpected synthetic command {kind}")

    async def command_batch(self, payloads):
        from ha_mcp_engineering.clients.websocket import batch_command_allowed

        assert all(batch_command_allowed(item) for item in payloads)
        self.ha.batch_calls.append(deepcopy(payloads))
        if self.ha.batch_failure is not None:
            raise self.ha.batch_failure
        if self.ha.websocket_seconds_per_command:
            await asyncio.sleep(self.ha.websocket_seconds_per_command * len(payloads))
        outcomes = []
        for payload in payloads:
            entity_id = payload.get("entity_id") or payload.get("item_id")
            override = self.ha.batch_overrides.get((payload["type"], entity_id))
            if override is not None:
                outcomes.append(deepcopy(override))
                continue
            item = self.ha.automations.get(entity_id)
            if payload["type"] == "automation/config":
                if item is None or item["loaded"] is None:
                    outcomes.append((False, "not_found"))
                else:
                    outcomes.append((True, {"config": deepcopy(item["loaded"])}))
            else:
                related = {}
                if item is not None and item["blueprint"] and item["state"] != "unavailable":
                    related["automation_blueprint"] = [item["blueprint"]]
                outcomes.append((True, related))
        return outcomes


class SyntheticWebSocketWithoutBatch(SyntheticWebSocket):
    """The pre-HAMCP-209 client surface: no pipelined batch."""

    command_batch = None  # type: ignore[assignment]
