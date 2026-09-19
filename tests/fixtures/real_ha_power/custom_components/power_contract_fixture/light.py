"""Synthetic light with observable service-call accounting."""
from homeassistant.components.light import LightEntity, ColorMode


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([PowerContractLight()])


class PowerContractLight(LightEntity):
    _attr_name = "HAMCP Contract Light"
    _attr_unique_id = "hamcp-contract-light"
    _attr_should_poll = False
    _attr_is_on = False
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_color_mode = ColorMode.ONOFF

    def __init__(self):
        self.calls = 0

    @property
    def extra_state_attributes(self):
        return {"synthetic_service_calls": self.calls}

    async def async_turn_on(self, **kwargs):
        self.calls += 1
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.calls += 1
        self._attr_is_on = False
        self.async_write_ha_state()
