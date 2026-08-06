"""Two real entities that share one pre-2026.8 device identity."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback


SHARED_IDENTIFIER = ("beta23_device_fixture", "shared-physical-device")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add one isolated in-memory switch for the owning config entry."""

    async_add_entities([Beta23DeviceFixtureSwitch(str(entry.data["slot"]))])


class Beta23DeviceFixtureSwitch(SwitchEntity):
    """An isolated switch used to prove direct device-target expansion."""

    _attr_should_poll = False
    _attr_is_on = False

    def __init__(self, slot: str) -> None:
        self._slot = slot
        self._attr_name = f"Beta 23 Device Fixture {slot.upper()}"
        self._attr_unique_id = f"beta23-device-fixture-{slot}"
        self._attr_device_info = DeviceInfo(
            identifiers={SHARED_IDENTIFIER},
            manufacturer="Beta 23 compatibility contract",
            model="Synthetic shared physical device",
            name="Beta 23 Composite Device Fixture",
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on only this disposable in-memory fixture."""

        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off only this disposable in-memory fixture."""

        self._attr_is_on = False
        self.async_write_ha_state()
