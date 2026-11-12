"""One fixed config entry; the flow takes no input."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries

DOMAIN = "hamcp366_refusal_fixture"


class Hamcp366RefusalFixtureConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="HAMCP-366 refusal fixture", data={})
