"""One fixed, disposable fixture entry."""
import voluptuous as vol
from homeassistant import config_entries


class PowerContractFlow(config_entries.ConfigFlow, domain="power_contract_fixture"):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=vol.Schema({}))
        await self.async_set_unique_id("synthetic-power-contract")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Synthetic power contract", data={})
