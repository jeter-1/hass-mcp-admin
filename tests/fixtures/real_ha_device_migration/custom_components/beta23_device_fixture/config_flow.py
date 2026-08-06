"""Config flow for two deterministic fixture config entries."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries


DOMAIN = "beta23_device_fixture"
SLOTS = ("a", "b")


class Beta23DeviceFixtureConfigFlow(
    config_entries.ConfigFlow,
    domain=DOMAIN,
):
    """Create one independently owned side of a shared physical device."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Create one of the two fixed fixture entries."""

        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({vol.Required("slot"): vol.In(SLOTS)}),
            )
        slot = str(user_input["slot"])
        await self.async_set_unique_id(f"{DOMAIN}-{slot}")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Beta 23 device fixture {slot.upper()}",
            data={"slot": slot},
        )
