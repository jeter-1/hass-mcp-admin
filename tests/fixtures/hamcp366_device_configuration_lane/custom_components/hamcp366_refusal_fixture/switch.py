"""Refusal-fixture switches (never an admitted kind)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity

from .common import FixtureBase


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([
        # T2: an admitted translation key on a platform that is not matter.
        FixtureSwitch("t2_switch", translation_key="child_lock"),
        FixtureSwitch("plain_switch", category=None),
        FixtureSwitch("disabled_switch", translation_key="child_lock", enabled=False),
    ])


class FixtureSwitch(FixtureBase, SwitchEntity):
    _attr_is_on = False

    def __init__(self, key, **shape) -> None:
        self._shape(key, **shape)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._calls += 1
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._calls += 1
        self._attr_is_on = False
        self.async_write_ha_state()
