"""Two real entities that share one pre-2026.8 device identity."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
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
    """Add one actual sensor for the owning config entry."""

    async_add_entities([Beta23DeviceFixtureSensor(str(entry.data["slot"]))])


class Beta23DeviceFixtureSensor(SensorEntity):
    """A behavior-free state source backed by a normal entity platform."""

    _attr_should_poll = False
    _attr_native_value = "ready"

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
