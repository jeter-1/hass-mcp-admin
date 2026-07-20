"""Disposable HA WebSocket fixture for manual upstream compatibility review.

This process is intentionally synthetic and read-only.  It implements only the
bounded Home Assistant messages required to list and read one dashboard.
"""

from __future__ import annotations

import argparse
import json

from aiohttp import WSMsgType, web


TOKEN = "synthetic-upstream-compatibility-token"
DASHBOARD = {
    "title": "Compatibility Fixture",
    "views": [{"title": "Overview", "path": "overview", "cards": []}],
}


async def api_root(_request: web.Request) -> web.Response:
    return web.json_response({"message": "API running."})


async def api_config(_request: web.Request) -> web.Response:
    return web.json_response(
        {
            "version": "2026.7.2",
            "location_name": "Disposable compatibility fixture",
            "time_zone": "UTC",
            "components": ["lovelace"],
        }
    )


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
        message_type = request_data.get("type")
        if message_type == "lovelace/dashboards/list":
            result = [
                {
                    "id": "compatibility-fixture",
                    "url_path": "compatibility-fixture",
                    "title": "Compatibility Fixture",
                    "icon": "mdi:test-tube",
                    "show_in_sidebar": True,
                    "require_admin": False,
                    "mode": "storage",
                }
            ]
            await ws.send_json({"id": request_id, "type": "result", "success": True, "result": result})
        elif message_type == "lovelace/config":
            await ws.send_json(
                {
                    "id": request_id,
                    "type": "result",
                    "success": True,
                    "result": DASHBOARD,
                }
            )
        else:
            await ws.send_json(
                {
                    "id": request_id,
                    "type": "result",
                    "success": False,
                    "error": {"code": "unknown_command", "message": "Unknown command."},
                }
            )
    return ws


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18123)
    args = parser.parse_args()
    application = web.Application()
    application.router.add_get("/api/", api_root)
    application.router.add_get("/api/config", api_config)
    application.router.add_get("/api/websocket", websocket)
    web.run_app(application, host="127.0.0.1", port=args.port, print=None)


if __name__ == "__main__":
    main()
