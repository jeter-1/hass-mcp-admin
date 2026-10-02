"""Writer-generated composite-device fixture for disposable HA upgrades."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


PLATFORMS = ("switch", "fan")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one side of the synthetic shared physical device."""

    from homeassistant.const import __version__
    if __version__ == "2026.9.4":
        from .baseline_fixture import register
        register(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the fixture platform."""

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
