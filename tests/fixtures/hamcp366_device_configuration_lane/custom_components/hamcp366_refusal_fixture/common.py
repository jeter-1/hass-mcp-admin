"""Shared shape of the refusal-fixture entities."""

from __future__ import annotations

from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo

DOMAIN = "hamcp366_refusal_fixture"
CATEGORIES = {"config": EntityCategory.CONFIG, "diagnostic": EntityCategory.DIAGNOSTIC, None: None}


class FixtureBase:
    """Registry facts under test: category, translation key, device, enabled, available."""

    _attr_should_poll = False

    def _shape(self, key, *, category="config", translation_key=None, device=True,
               enabled=True, available=True):
        self._calls = 0
        self._attr_name = "HAMCP366 " + key.replace("_", " ")
        self._attr_unique_id = "hamcp366-refusal-" + key
        self._attr_entity_category = CATEGORIES[category]
        self._attr_translation_key = translation_key
        self._attr_entity_registry_enabled_default = enabled
        self._attr_available = available
        if device:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, "refusal-device")},
                manufacturer="HAMCP-366 lane",
                model="Refusal fixture",
                name="HAMCP366 Refusal Fixture",
            )

    @property
    def extra_state_attributes(self):
        return {"service_calls": self._calls}
