"""Home Assistant WebSocket transport."""

from typing import Any

import aiohttp

from ..configuration import Settings
from ..errors import HomeAssistantError


class HomeAssistantWebSocketClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def command(self, payload: dict) -> Any:
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(self.settings.websocket_url) as websocket:
                message = await websocket.receive_json()
                if message.get("type") != "auth_required":
                    raise HomeAssistantError(f"Unexpected WS handshake: {message}")
                await websocket.send_json(
                    {"type": "auth", "access_token": self.settings.ha_token}
                )
                message = await websocket.receive_json()
                if message.get("type") != "auth_ok":
                    raise HomeAssistantError("HA WebSocket authentication failed")
                await websocket.send_json({"id": 1, **payload})
                while True:
                    message = await websocket.receive_json()
                    if message.get("id") == 1 and message.get("type") == "result":
                        if not message.get("success"):
                            raise HomeAssistantError(
                                f"WS command failed: {message.get('error')}"
                            )
                        return message.get("result")
