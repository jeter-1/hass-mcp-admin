"""HAMCP-366 test-only refusal fixture for the device-configuration lane.

Every entity here is a target the signed build must refuse. None is an
admitted kind: the platform is never ``matter``. Each in-memory entity counts
the service calls that reach it in a ``service_calls`` state attribute, so the
lane can show from Core's own state that no refused request was dispatched.
Never a product path and never installed outside the disposable lane.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

DOMAIN = "hamcp366_refusal_fixture"
PLATFORMS = ("select", "switch", "number")


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
