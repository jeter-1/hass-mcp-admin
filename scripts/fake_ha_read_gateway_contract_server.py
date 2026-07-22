"""Disposable, read-only HA fixture for the exact-image gateway acceptance.

The fixture implements the bounded REST and WebSocket reads used by the
representative ha-mcp 7.14.1 gateway calls.  Every HTTP mutation is rejected;
the stats endpoint lets CI prove that no mutating request was attempted.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from typing import Any

from aiohttp import WSMsgType, web


TOKEN = "synthetic-read-gateway-token"
NOW = "2026-07-21T12:00:00+00:00"
STATES = [
    {
        "entity_id": "sun.sun",
        "state": "above_horizon",
        "attributes": {"friendly_name": "Sun", "next_rising": NOW},
        "last_changed": NOW,
        "last_reported": NOW,
        "last_updated": NOW,
        "context": {"id": "fixture-context", "parent_id": None, "user_id": None},
    },
    {
        "entity_id": "automation.gateway_fixture",
        "state": "on",
        "attributes": {"friendly_name": "Gateway Fixture", "id": "gateway_fixture"},
        "last_changed": NOW,
        "last_reported": NOW,
        "last_updated": NOW,
        "context": {"id": "fixture-automation-context", "parent_id": None, "user_id": None},
    },
    {
        "entity_id": "automation.gateway_fixture_unreadable",
        "state": "on",
        "attributes": {
            "friendly_name": "Gateway Fixture Unreadable",
            "id": "gateway_fixture_unreadable",
        },
        "last_changed": NOW,
        "last_reported": NOW,
        "last_updated": NOW,
        "context": {
            "id": "fixture-unreadable-context",
            "parent_id": None,
            "user_id": None,
        },
    },
]
ENTITY_REGISTRY = [
    {
        "entity_id": "sun.sun",
        "unique_id": "sun",
        "platform": "sun",
        "name": None,
        "original_name": "Sun",
        "device_id": "fixture-device",
        "area_id": "outside",
        "disabled_by": None,
        "hidden_by": None,
        "labels": [],
        "aliases": [],
    },
    {
        "entity_id": "automation.gateway_fixture",
        "unique_id": "gateway_fixture",
        "platform": "automation",
        "name": None,
        "original_name": "Gateway Fixture",
        "device_id": None,
        "area_id": None,
        "disabled_by": None,
        "hidden_by": None,
        "labels": [],
        "aliases": [],
    },
    {
        "entity_id": "automation.gateway_fixture_unreadable",
        "unique_id": "gateway_fixture_unreadable",
        "platform": "automation",
        "name": None,
        "original_name": "Gateway Fixture Unreadable",
        "device_id": None,
        "area_id": None,
        "disabled_by": None,
        "hidden_by": None,
        "labels": [],
        "aliases": [],
    },
]
DEVICE_REGISTRY = [
    {
        "id": "fixture-device",
        "name": "Fixture Device",
        "name_by_user": None,
        "manufacturer": "Fixture Manufacturer",
        "model": "Read Only",
        "model_id": "fixture-model",
        "area_id": "outside",
        "configuration_url": None,
        "disabled_by": None,
        "entry_type": None,
        "hw_version": "1",
        "sw_version": "1",
        "serial_number": "fixture-serial",
        "identifiers": [["fixture", "device"]],
        "connections": [],
        "labels": [],
        "via_device_id": None,
    }
]
AREAS = [{"area_id": "outside", "name": "Outside", "floor_id": None, "labels": []}]
AUTOMATION = {
    "id": "gateway_fixture",
    "alias": "Gateway Fixture",
    "description": "Synthetic read-only acceptance automation.",
    "mode": "single",
    "triggers": [],
    "conditions": [],
    "actions": [],
}


class FixtureState:
    def __init__(self) -> None:
        self.rest_reads: Counter[str] = Counter()
        self.http_mutations: Counter[str] = Counter()
        self.websocket_reads: Counter[str] = Counter()
        self.websocket_mutations: Counter[str] = Counter()

    def snapshot(self) -> dict[str, Any]:
        return {
            "rest_reads": dict(self.rest_reads),
            "http_mutations": dict(self.http_mutations),
            "websocket_reads": dict(self.websocket_reads),
            "websocket_mutations": dict(self.websocket_mutations),
        }


STATE = FixtureState()


@web.middleware
async def read_only_guard(request: web.Request, handler):
    if request.path.startswith("/__fixture__/"):
        return await handler(request)
    if request.method != "GET":
        STATE.http_mutations[f"{request.method} {request.path}"] += 1
        return web.json_response({"message": "fixture is read-only"}, status=405)
    return await handler(request)


async def api_root(_request: web.Request) -> web.Response:
    STATE.rest_reads["/api/"] += 1
    return web.json_response({"message": "API running."})


async def api_config(_request: web.Request) -> web.Response:
    STATE.rest_reads["/api/config"] += 1
    return web.json_response(
        {
            "version": "2026.7.2",
            "location_name": "Read gateway fixture",
            "time_zone": "UTC",
            "components": ["automation", "history", "sun"],
            "unit_system": {"length": "km", "temperature": "°C"},
        }
    )


async def api_states(_request: web.Request) -> web.Response:
    STATE.rest_reads["/api/states"] += 1
    return web.json_response(STATES)


async def api_state(request: web.Request) -> web.Response:
    entity_id = request.match_info["entity_id"]
    STATE.rest_reads["/api/states/{entity_id}"] += 1
    for item in STATES:
        if item["entity_id"] == entity_id:
            return web.json_response(item)
    return web.json_response({"message": "Entity not found."}, status=404)


async def api_history(request: web.Request) -> web.Response:
    entity_id = request.match_info.get("entity_id") or "sun.sun"
    STATE.rest_reads["/api/history/period"] += 1
    rows = [item for item in STATES if item["entity_id"] == entity_id]
    return web.json_response([rows])


async def api_services(_request: web.Request) -> web.Response:
    STATE.rest_reads["/api/services"] += 1
    return web.json_response(
        {
            "light": {
                "turn_on": {
                    "name": "Turn on",
                    "description": "Synthetic service metadata only.",
                    "fields": {},
                    "target": {"entity": []},
                }
            }
        }
    )


async def api_automation(request: web.Request) -> web.Response:
    STATE.rest_reads["/api/config/automation/config/{id}"] += 1
    if request.match_info["automation_id"] != "gateway_fixture":
        return web.json_response({"message": "Not found."}, status=404)
    return web.json_response(AUTOMATION)


async def fixture_stats(_request: web.Request) -> web.Response:
    return web.json_response(STATE.snapshot())


def _result_for(message_type: str, request_data: dict[str, Any]) -> Any:
    if message_type == "get_states":
        return STATES
    if message_type == "config/entity_registry/list":
        return ENTITY_REGISTRY
    if message_type == "config/entity_registry/get":
        entity_id = request_data.get("entity_id")
        return next((item for item in ENTITY_REGISTRY if item["entity_id"] == entity_id), None)
    if message_type == "config/device_registry/list":
        return DEVICE_REGISTRY
    if message_type == "config/device_registry/get":
        device_id = request_data.get("device_id")
        return next((item for item in DEVICE_REGISTRY if item["id"] == device_id), None)
    if message_type == "config/area_registry/list":
        return AREAS
    if message_type in {"config/floor_registry/list", "config/label_registry/list"}:
        return []
    if message_type in {
        "lovelace/dashboards/list",
        "config/category_registry/list",
        "config/entry_registry/list",
    }:
        return []
    if message_type == "history/history_during_period":
        return {
            entity_id: [
                {
                    "s": next(
                        (
                            state["state"]
                            for state in STATES
                            if state["entity_id"] == entity_id
                        ),
                        "unknown",
                    ),
                    "lu": 1784635200.0,
                    "lc": 1784635200.0,
                }
            ]
            for entity_id in request_data.get("entity_ids", [])
        }
    return None


async def websocket(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    await ws.send_json({"type": "auth_required", "ha_version": "2026.7.2"})
    auth = await ws.receive_json()
    if auth != {"type": "auth", "access_token": TOKEN}:
        await ws.send_json({"type": "auth_invalid", "message": "Invalid auth."})
        await ws.close()
        return ws
    await ws.send_json({"type": "auth_ok", "ha_version": "2026.7.2"})
    async for message in ws:
        if message.type != WSMsgType.TEXT:
            continue
        try:
            request_data = json.loads(message.data)
        except json.JSONDecodeError:
            continue
        request_id = request_data.get("id")
        message_type = str(request_data.get("type", ""))
        lowered = message_type.lower()
        if any(token in lowered for token in ("/update", "/create", "/delete", "call_service", "reload")):
            STATE.websocket_mutations[message_type] += 1
            await ws.send_json(
                {
                    "id": request_id,
                    "type": "result",
                    "success": False,
                    "error": {"code": "read_only_fixture", "message": "Mutation refused."},
                }
            )
            continue
        STATE.websocket_reads[message_type] += 1
        result = _result_for(message_type, request_data)
        if result is None:
            await ws.send_json(
                {
                    "id": request_id,
                    "type": "result",
                    "success": False,
                    "error": {"code": "unknown_command", "message": "Unknown read command."},
                }
            )
        else:
            await ws.send_json(
                {"id": request_id, "type": "result", "success": True, "result": result}
            )
    return ws


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18123)
    args = parser.parse_args()
    application = web.Application(middlewares=[read_only_guard])
    application.router.add_get("/api/", api_root)
    application.router.add_get("/api/config", api_config)
    application.router.add_get("/api/states", api_states)
    application.router.add_get("/api/states/{entity_id}", api_state)
    application.router.add_get("/api/history/period", api_history)
    application.router.add_get("/api/history/period/{entity_id}", api_history)
    application.router.add_get("/api/services", api_services)
    application.router.add_get(
        "/api/config/automation/config/{automation_id}", api_automation
    )
    application.router.add_get("/api/websocket", websocket)
    application.router.add_get("/__fixture__/stats", fixture_stats)
    web.run_app(application, host="127.0.0.1", port=args.port, print=None)


if __name__ == "__main__":
    main()
