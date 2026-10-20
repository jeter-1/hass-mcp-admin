"""HAMCP-212 synthetic loaded and stored script configuration.

Extends the HAMCP-209 model with scripts, mirroring the pinned Core
2026.10.x behaviour the HAMCP-212 design note cites:

* WebSocket ``script/config`` returns the loaded ``raw_config`` from memory
  (the substituted config for a blueprint script); ``ERR_NOT_FOUND`` when no
  script entity is loaded (registry-only, disabled or not reloaded yet).
* ``search/related`` with ``item_type: script`` reports ``script_blueprint``
  and recurses into scripts the script targets, which are listed under
  ``script`` with their blueprints merged in. Like Core there is no cycle guard,
  so a self-calling or cyclic script fails its search per command
  (``unknown_error``).
* The stored read is the delegated ha-mcp 8.5.0 ``ha_config_get_script``
  route: the scripts.yaml entry, with the entity registry ``category``
  injected when one is set. Each read costs a configurable delay, the cost
  the live delegated reads showed.

Every value is synthetic. Nothing here contacts a live system.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from copy import deepcopy
from typing import Any

from tests.hamcp209_fixtures import (
    SyntheticHomeAssistant,
    SyntheticWebSocket,
    SyntheticWebSocketWithoutBatch,
)


SCRIPT_BLUEPRINT_PATH = "synthetic/script_notify.yaml"
SCRIPT_PROVENANCE = "ha-mcp 8.5.0; synthetic reviewed contract; identity inventory direct_ha_api; no fallback"


def script(
    key: str,
    stored: dict[str, Any] | None,
    *,
    loaded: dict[str, Any] | None = None,
    blueprint: str | None = None,
    state: str = "off",
    category: str | None = None,
    loaded_present: bool = True,
) -> dict[str, Any]:
    return {
        "key": key,
        "entity_id": f"script.{key}",
        "state": state,
        "stored": deepcopy(stored),
        "loaded": deepcopy(loaded if loaded is not None else stored) if loaded_present else None,
        "blueprint": blueprint,
        "category": category,
    }


def representative_scripts() -> list[dict[str, Any]]:
    """Plain, templated, dynamic, calling, blueprint, nested, unavailable, categorized."""

    plain = {
        "alias": "Synthetic script plain",
        "sequence": [
            {"action": "light.turn_on", "target": {"entity_id": "light.synthetic_kitchen"}}
        ],
    }
    templated = {
        "alias": "Synthetic script templated",
        "sequence": [
            {"condition": "template", "value_template": "{{ is_state('sensor.synthetic_temperature', 'on') }}"},
            {
                "action": "climate.set_temperature",
                "target": {"entity_id": "climate.synthetic_hall"},
                "data": {"temperature": "{{ states('input_number.synthetic_setpoint') | float }}"},
            },
        ],
    }
    dynamic = {
        "alias": "Synthetic script dynamic",
        "sequence": [
            {
                "action": "{{ 'light.turn_' ~ states('input_select.synthetic_mode') }}",
                "target": {"entity_id": "{{ states('input_text.synthetic_target') }}"},
            }
        ],
    }
    caller = {
        "alias": "Synthetic script caller",
        "sequence": [
            {"action": "script.synthetic_script_plain"},
            {"action": "script.turn_on", "target": {"entity_id": "script.synthetic_script_templated"}},
        ],
    }
    blueprint_stored = {
        "alias": "Synthetic script blueprint",
        "use_blueprint": {
            "path": SCRIPT_BLUEPRINT_PATH,
            "input": {"notify_target": "light.synthetic_hall"},
        },
    }
    blueprint_loaded = {
        "alias": "Synthetic script blueprint",
        "sequence": [
            {"action": "light.turn_on", "target": {"entity_id": "light.synthetic_hall"}}
        ],
    }
    # Not itself a blueprint instance, but it targets one: Core's recursive
    # search reports the nested script's blueprint for it.
    nested_caller = {
        "alias": "Synthetic script nested caller",
        "sequence": [
            {"action": "script.turn_on", "target": {"entity_id": "script.synthetic_script_blueprint"}},
            {"action": "light.turn_off", "target": {"entity_id": "light.synthetic_kitchen"}},
        ],
    }
    broken_blueprint = {
        "alias": "Synthetic script missing blueprint",
        "use_blueprint": {
            "path": "synthetic/deleted_script.yaml",
            "input": {"notify_target": "light.synthetic_hall"},
        },
    }
    categorized = {
        "alias": "Synthetic script categorized",
        "sequence": [
            {"action": "input_boolean.turn_on", "target": {"entity_id": "input_boolean.synthetic_guest"}}
        ],
    }
    return [
        script("synthetic_script_plain", plain),
        script("synthetic_script_templated", templated),
        script("synthetic_script_dynamic", dynamic),
        script("synthetic_script_caller", caller),
        script(
            "synthetic_script_blueprint", blueprint_stored,
            loaded=blueprint_loaded, blueprint=SCRIPT_BLUEPRINT_PATH,
        ),
        script("synthetic_script_nested_caller", nested_caller),
        # Core's UnavailableScriptEntity: validation failed; the blueprint
        # reference is not reported and raw_config is the original.
        script("synthetic_script_unavailable", broken_blueprint, state="unavailable"),
        script("synthetic_script_categorized", categorized, category="synthetic_category"),
    ]


def generated_scripts(count: int) -> list[dict[str, Any]]:
    return [
        script(
            f"synthetic_bulk_script_{index:02d}",
            {
                "alias": f"Synthetic bulk script {index}",
                "sequence": [
                    {"action": "light.toggle", "target": {"entity_id": f"light.synthetic_bulk_{index:04d}"}},
                    {"delay": {"seconds": 1}},
                ],
            },
        )
        for index in range(count)
    ]


class SyntheticScriptHome(SyntheticHomeAssistant):
    """The HAMCP-209 model plus loaded and stored scripts."""

    def __init__(
        self,
        automations: list[dict[str, Any]],
        scripts: list[dict[str, Any]],
        *,
        delegated_seconds_per_read: float = 0.0,
        serialize_delegated_reads: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(automations, **kwargs)
        self.scripts = {item["entity_id"]: item for item in scripts}
        self.stored_only_scripts: dict[str, dict[str, Any]] = {}
        self.delegated_seconds_per_read = delegated_seconds_per_read
        # The live delegated route behaved as mostly serialized (beta.23:
        # 5 reads, 9.3 s cumulative, about 7.6 s of build wall time).
        self.delegated_lock = asyncio.Lock() if serialize_delegated_reads else None
        self.script_reads: Counter[str] = Counter()
        self.script_read_failures: dict[str, str] = {}
        self.websocket = SyntheticScriptWebSocket(self)
        self.websocket_without_batch = SyntheticScriptWebSocketWithoutBatch(self)

    def states(self) -> list[dict[str, Any]]:
        rows = super().states()
        present = {row["entity_id"] for row in rows}
        for entity_id in ("light.synthetic_hall", "input_boolean.synthetic_guest"):
            if entity_id not in present:
                rows.append({"entity_id": entity_id, "state": "on", "attributes": {"friendly_name": entity_id}})
        for item in self.scripts.values():
            if item["state"] is None:
                continue  # registry-only (disabled or not loaded)
            rows.append({
                "entity_id": item["entity_id"],
                "state": item["state"],
                "attributes": {"friendly_name": item["entity_id"]},
            })
        return rows

    def registry(self) -> list[dict[str, Any]]:
        script_entities = set(self.scripts)
        rows = [row for row in super().registry() if row["entity_id"] not in script_entities]
        for item in self.scripts.values():
            rows.append({
                "entity_id": item["entity_id"],
                "platform": "script",
                "unique_id": item["key"],
                "device_id": None,
                "area_id": None,
                "labels": [],
                "categories": {"script": item["category"]} if item["category"] else {},
                "disabled_by": None,
                "hidden_by": None,
            })
        return rows

    def stored_script(self, key: str) -> dict[str, Any] | None:
        for item in self.scripts.values():
            if item["key"] == key:
                return deepcopy(item["stored"])
        return deepcopy(self.stored_only_scripts.get(key))

    def reload_script(self, entity_id: str) -> None:
        item = self.scripts[entity_id]
        item["loaded"] = deepcopy(item["stored"])

    def search_script(self, entity_id: str, path: tuple[str, ...] = ()) -> dict[str, list[str]]:
        """Core's recursive ``_async_search_script`` for the fixture's shapes.

        Like Core (``search/__init__.py:613-614``), there is no cycle guard: a
        script that reaches itself again recurses until ``RecursionError``,
        which Core's WebSocket layer (``websocket_api/connection.py:249-252``)
        returns as a per-command ``unknown_error``. The fixture raises at the
        first repeat instead of exhausting the stack. An unavailable or
        unloaded script references nothing (``UnavailableScriptEntity``
        ``script/__init__.py:503-518``; no entity at all for registry-only).
        """

        if entity_id in path:
            raise RecursionError(f"cyclic script reference: {' -> '.join((*path, entity_id))}")
        item = self.scripts.get(entity_id)
        related: dict[str, set[str]] = {"script_blueprint": set(), "script": set()}
        if item is None or item["loaded"] is None or item["state"] in (None, "unavailable"):
            return {}
        if item["blueprint"]:
            related["script_blueprint"].add(item["blueprint"])
        for step in item["loaded"].get("sequence", []) if isinstance(item["loaded"].get("sequence"), list) else []:
            target = step.get("target", {}).get("entity_id") if isinstance(step, dict) and isinstance(step.get("target"), dict) else None
            if isinstance(target, str) and target.startswith("script."):
                related["script"].add(target)
                nested = self.search_script(target, (*path, entity_id))
                for key, values in nested.items():
                    related.setdefault(key, set()).update(values)
        if not path:
            related["script"].discard(entity_id)  # Core: async_search :114-115
        return {key: sorted(values) for key, values in related.items() if values}


class SyntheticScriptWebSocket(SyntheticWebSocket):
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
            if payload["type"] == "automation/config":
                item = self.ha.automations.get(entity_id)
                if item is None or item["loaded"] is None:
                    outcomes.append((False, "not_found"))
                else:
                    outcomes.append((True, {"config": deepcopy(item["loaded"])}))
            elif payload["type"] == "script/config":
                item = self.ha.scripts.get(entity_id)
                if item is None or item["loaded"] is None or item["state"] is None:
                    outcomes.append((False, "not_found"))
                else:
                    outcomes.append((True, {"config": deepcopy(item["loaded"])}))
            elif payload["item_type"] == "automation":
                item = self.ha.automations.get(entity_id)
                related = {}
                if item is not None and item["blueprint"] and item["state"] != "unavailable":
                    related["automation_blueprint"] = [item["blueprint"]]
                outcomes.append((True, related))
            else:
                try:
                    outcomes.append((True, self.ha.search_script(entity_id)))
                except RecursionError:
                    outcomes.append((False, "unknown_error"))
        return outcomes


class SyntheticScriptWebSocketWithoutBatch(SyntheticScriptWebSocket):
    command_batch = None  # type: ignore[assignment]


class SyntheticScriptReader:
    """The delegated ha-mcp ``ha_config_get_script`` read (stored form)."""

    provenance = SCRIPT_PROVENANCE

    def __init__(self, ha: SyntheticScriptHome) -> None:
        self.ha = ha
        self.active = True
        self.calls: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0

    def current(self) -> bool:
        return self.active

    async def read(self, entity_id: str) -> dict[str, Any]:
        self.calls.append(entity_id)
        self.ha.script_reads[entity_id] += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.ha.delegated_lock is not None:
                async with self.ha.delegated_lock:
                    await asyncio.sleep(self.ha.delegated_seconds_per_read)
            elif self.ha.delegated_seconds_per_read:
                await asyncio.sleep(self.ha.delegated_seconds_per_read)
            else:
                await asyncio.sleep(0)
        finally:
            self.in_flight -= 1
        key = entity_id.removeprefix("script.")
        failure = self.ha.script_read_failures.get(entity_id)
        stored = self.ha.stored_script(key)
        if failure is None and stored is None:
            failure = "resource_not_found"
        if failure is not None:
            return {"success": False, "details": {"failure_category": failure}}
        item = self.ha.scripts.get(entity_id)
        if item is not None and item["category"]:
            stored["category"] = item["category"]
        return {
            "success": True,
            "data": {"success": True, "script_id": key, "config": stored, "config_hash": "synthetic"},
            "metadata": {
                "provider": "upstream_read_gateway", "upstream_version": "8.5.0",
                "completeness": "complete", "fallback_occurred": False,
            },
        }
