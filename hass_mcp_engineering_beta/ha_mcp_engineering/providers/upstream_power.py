"""One light/switch target, fixed ON/OFF arguments, reviewed upstream only."""
from .upstream_fan import FanProvider
from ..power.contracts import POWER_RELEASES, PROVIDER, PowerRequest, PowerRefusal, checked_state


class PowerProvider(FanProvider):
    releases = POWER_RELEASES
    provider_name = PROVIDER
    authority_method = "power_provider_authority_token"
    request_type = PowerRequest
    refusal = PowerRefusal

    async def state(self, entity_id, authorize, *, contract=None):
        async def before():
            authorize()
        value, _ = await self._call("ha_get_state", {
            "entity_id": entity_id,
            "fields": ["entity_id", "state", "last_updated"],
        }, before, expected_contract=contract)
        return checked_state(value.get("data"), entity_id)

    async def services(self, request, authorize, *, contract=None):
        request = PowerRequest.model_validate(request.model_dump()).checked()
        async def before():
            authorize()
        value, selected = await self._call("ha_list_services", {
            "domain": request.domain, "limit": 50, "offset": 0, "detail_level": "summary",
        }, before, expected_contract=contract)
        services = value.get("services")
        if (value.get("success") is not True or not isinstance(services, dict)
                or request.domain + "." + request.action not in services):
            raise PowerRefusal("power_service_unavailable")
        return selected
