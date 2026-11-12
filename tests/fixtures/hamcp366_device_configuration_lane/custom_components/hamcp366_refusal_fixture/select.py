"""Refusal-fixture selects (never an admitted kind)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity

from .common import FixtureBase


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([
        # T2: an admitted translation key on a platform that is not matter.
        FixtureSelect("t2_select", translation_key="startup_on_off"),
        FixtureSelect("diagnostic_select", category="diagnostic", translation_key="startup_on_off"),
        FixtureSelect("no_device_select", translation_key="startup_on_off", device=False),
        FixtureSelect("unavailable_select", translation_key="startup_on_off", available=False),
    ])


class FixtureSelect(FixtureBase, SelectEntity):
    _attr_options = ["on", "off", "toggle", "previous"]
    _attr_current_option = "off"

    def __init__(self, key, **shape) -> None:
        self._shape(key, **shape)

    async def async_select_option(self, option: str) -> None:
        self._calls += 1
        self._attr_current_option = option
        self.async_write_ha_state()
