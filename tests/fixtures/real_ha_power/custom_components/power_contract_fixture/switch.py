"""Synthetic switch with observable service-call accounting."""
from homeassistant.components.switch import SwitchEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([PowerContractSwitch()])


class PowerContractSwitch(SwitchEntity):
    _attr_name = "HAMCP Contract Switch"
    _attr_unique_id = "hamcp-contract-switch"
    _attr_should_poll = False
    _attr_is_on = False

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
