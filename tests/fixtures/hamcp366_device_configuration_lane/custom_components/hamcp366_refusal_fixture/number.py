"""Refusal-fixture numbers (never an admitted kind)."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity
from homeassistant.const import UnitOfTemperature

from .common import FixtureBase


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([
        # T2: an admitted translation key on a platform that is not matter.
        FixtureNumber("t2_number", translation_key="sensitivity_level"),
        # A converter-class number; the lane sets a registry unit option on it.
        FixtureNumber("converter_number", translation_key="sensitivity_level", converter=True),
    ])


class FixtureNumber(FixtureBase, NumberEntity):
    _attr_native_min_value = 1
    _attr_native_max_value = 3
    _attr_native_step = 1
    _attr_native_value = 2

    def __init__(self, key, *, converter=False, **shape) -> None:
        self._shape(key, **shape)
        if converter:
            self._attr_device_class = NumberDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_native_min_value = 5
            self._attr_native_max_value = 35
            self._attr_native_value = 20

    async def async_set_native_value(self, value: float) -> None:
        self._calls += 1
        self._attr_native_value = value
        self.async_write_ha_state()
