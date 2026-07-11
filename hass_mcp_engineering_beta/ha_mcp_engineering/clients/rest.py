"""Home Assistant REST transport."""

import json
from typing import Any

import aiohttp

from ..configuration import Settings
from ..errors import HomeAssistantError


class HomeAssistantRestClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def request(self, method: str, path: str, body: Any = None, raw: bool = False) -> Any:
        headers = {
            "Authorization": f"Bearer {self.settings.ha_token}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method, f"{self.settings.api_url}{path}", headers=headers, json=body
            ) as response:
                text = await response.text()
                if response.status >= 400:
                    raise HomeAssistantError(
                        f"HA API {response.status} on {method} {path}: {text[:500]}"
                    )
                if raw:
                    return text
                try:
                    return json.loads(text) if text else None
                except json.JSONDecodeError:
                    return text
