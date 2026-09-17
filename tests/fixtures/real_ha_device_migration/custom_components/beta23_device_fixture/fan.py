"""In-memory fan for the disposable, digest-pinned ordinary-action lane only."""
from homeassistant.components.fan import FanEntity, FanEntityFeature


async def async_setup_entry(hass, entry, async_add_entities):
    if entry.data['slot'] == 'a':
        async_add_entities([SyntheticFan()])


class SyntheticFan(FanEntity):
    _attr_name = 'HAMCP Contract Fan'
    _attr_unique_id = 'hamcp-contract-fan'
    _attr_should_poll = False
    _attr_percentage = 0
    _attr_supported_features = FanEntityFeature.SET_SPEED | FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF

    async def async_turn_on(self, percentage=None, preset_mode=None, **kwargs):
        self._attr_percentage = percentage if percentage is not None else 50
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._attr_percentage = 0
        self.async_write_ha_state()

    async def async_set_percentage(self, percentage):
        self._attr_percentage = percentage
        self.async_write_ha_state()
