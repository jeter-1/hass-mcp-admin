"""Writer-generated composite-device fixture for disposable HA upgrades."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


DOMAIN = "beta23_device_fixture"
PLATFORMS = ("switch", "fan")
BASELINE_KEY = DOMAIN + "/baseline_core_version"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Record the exact CI-supplied baseline Core binding, if any (HAMCP-159).

    Only the Alarmo staging step writes
    ``beta23_device_fixture: {baseline_core_version: <exact version>}``; every
    other lane leaves the baseline fixture unregistered.
    """

    if DOMAIN in config:
        from homeassistant.const import __version__
        if config[DOMAIN] != {"baseline_core_version": __version__}:
            raise ValueError("baseline fixture requires its exact disposable Core revision")
        hass.data[BASELINE_KEY] = __version__
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one side of the synthetic shared physical device."""

    from homeassistant.const import __version__
    if BASELINE_KEY in hass.data and hass.data[BASELINE_KEY] == __version__:
        from .baseline_fixture import register
        register(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the fixture platform."""

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
