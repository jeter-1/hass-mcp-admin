"""One helper target, closed per-domain services and data, reviewed upstream only.

ha_call_service forwards ``data`` unchanged in every reviewed release, so the
request model alone decides the exact service and data keys (see
docs/TYPED_HELPER_CONTROL.md). wait=False excludes the component fallback.
"""
from .upstream_fan import FanProvider
from ..helper_value.contracts import (ATTRIBUTE_KEYS, HELPER_RELEASES, HELPER_SEMANTIC_CONTRACTS, PROVIDER,
                                      HelperRefusal, HelperRequest, checked_state)


class HelperProvider(FanProvider):
    releases = HELPER_RELEASES
    semantic_contracts = HELPER_SEMANTIC_CONTRACTS
    provider_name = PROVIDER
    authority_method = "helper_value_provider_authority_token"
    request_type = HelperRequest
    refusal = HelperRefusal

    async def state(self, entity_id, authorize, *, contract=None):
        async def before():
            authorize()
        keys = list(ATTRIBUTE_KEYS[entity_id.split(".", 1)[0]])
        args = {"entity_id": entity_id, "fields": ["entity_id", "state", "last_updated"]}
        if keys:
            args = {"entity_id": entity_id, "fields": ["entity_id", "state", "attributes", "last_updated"],
                    "attribute_keys": keys}
        value, _ = await self._call("ha_get_state", args, before, expected_contract=contract)
        return checked_state(value.get("data"), entity_id)

    async def services(self, request, authorize, *, contract=None, signed_core=False):
        request = HelperRequest.model_validate(request.model_dump()).checked()
        async def before():
            authorize()
        value, selected = await self._call("ha_list_services", {
            "domain": request.domain, "limit": 50, "offset": 0, "detail_level": "summary",
        }, before, expected_contract=contract, signed_core=signed_core)
        services = value.get("services")
        if (value.get("success") is not True or not isinstance(services, dict)
                or request.domain + "." + request.action not in services):
            raise HelperRefusal("helper_service_unavailable")
        return selected
