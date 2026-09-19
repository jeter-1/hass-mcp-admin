"""Fixed fan-only wrapper over a closed set of exact reviewed ha-mcp releases.

This does not admit ha_call_service into the generic read gateway. In particular,
wait=False excludes the component fallback in ha-mcp's waited service path.
"""
from __future__ import annotations

import asyncio
import json
from ..clients.upstream_read import McpReadGatewayTransport
from ..configuration import parse_upstream_dashboard_endpoint
from ..fan.contracts import FanRequest, FanRefusal, FAN_RELEASES, checked_state, digest
from ..request_context import current_telemetry
from ..upstream_tool_policy import load_reviewed_upstream_release_registry, validate_reviewed_release_catalog
from ..version import SERVER_VERSION

ENTRY = "ha-mcp-v8.4.3-d5cea47a"
SOURCE = "eac7a3aa7063432e9af17e7d7726040e909c7b8f"


class FanProvider:
    releases = FAN_RELEASES
    provider_name = "upstream_typed_fan"
    authority_method = "fan_provider_authority_token"
    request_type = FanRequest
    refusal = FanRefusal

    def __init__(self, transport, authority_token):
        self.transport = transport
        self.authority_token = authority_token

    @classmethod
    def configured(cls, settings, gateway):
        endpoint = parse_upstream_dashboard_endpoint(settings.upstream_dashboard_mcp_url)
        transport = McpReadGatewayTransport(
            endpoint.url, timeout_seconds=min(settings.ha_timeout_seconds, 15),
            client_version=SERVER_VERSION,
        ) if endpoint else None
        return cls(transport, getattr(gateway, cls.authority_method))

    def validate_catalog(self, catalog, expected_contract=None):
        registry = load_reviewed_upstream_release_registry()
        release = registry.by_version.get(catalog.server_version)
        selected = self.releases.get(catalog.server_version)
        if (catalog.server_name != "ha-mcp" or selected is None
                or catalog.protocol_version != "2025-03-26" or release is None
                or catalog.catalog_complete is not True
                or release.revoked or release.entry_id != selected[0] or release.source_commit != selected[1]
                or (expected_contract is not None and expected_contract != selected[2])
                or release.provider_disposition("read_gateway") != "admitted"):
            raise self.refusal("fan_provider_identity_unavailable")
        result = validate_reviewed_release_catalog(
            release, observed_server_name=catalog.server_name,
            observed_upstream_version=catalog.server_version,
            observed_protocol_version=catalog.protocol_version, tools=catalog.tools,
        )
        if not result.valid:
            raise self.refusal("fan_provider_contract_mismatch")
        return (catalog.server_version, selected[2], self._authority(catalog.server_version))

    def _authority(self, version):
        # Retain the original callback calling convention for existing users.
        return self.authority_token() if version == "8.4.3" else self.authority_token(version)

    @classmethod
    def decode(cls, raw):
        if not isinstance(raw, dict) or raw.get("isError") is True:
            raise cls.refusal("fan_provider_response_failed")
        if len(json.dumps(raw, ensure_ascii=True, allow_nan=False).encode()) > 60_000:
            raise cls.refusal("fan_provider_response_oversized")
        value = raw.get("structuredContent")
        if not isinstance(value, dict):
            # Both exact releases use structured content. Never parse arbitrary
            # text as a substitute for its reviewed envelope.
            raise cls.refusal("fan_provider_response_malformed")
        return value

    async def _call(self, tool, args, before, *, expected_contract=None):
        if self.transport is None:
            raise self.refusal("fan_provider_unconfigured")
        token = None
        def validate(catalog):
            nonlocal token
            token = self.validate_catalog(catalog, expected_contract)
        async def dispatch():
            if token is None or self._authority(token[0]) != token[2]:
                raise self.refusal("fan_provider_authority_retired")
            await before()
            if self._authority(token[0]) != token[2]:
                raise self.refusal("fan_provider_authority_retired")
        async with asyncio.timeout(20):
            value = await self.transport.execute_read(
                tool, args, timeout_seconds=15,
                catalog_validator=validate, before_dispatch=dispatch,
            )
        if token is None or self._authority(token[0]) != token[2]:
            raise self.refusal("fan_provider_authority_retired")
        telemetry = current_telemetry()
        if telemetry:
            telemetry.audit_context.update(provider=self.provider_name, fallback="none")
            telemetry.record_provider_outcome("complete")
        return self.decode(value.call_result), token[1]

    async def state(self, entity_id, authorize, *, contract=None):
        async def before():
            authorize()
        value, _ = await self._call("ha_get_state", {
            "entity_id": entity_id,
            "fields": ["entity_id", "state", "attributes", "last_updated"],
            "attribute_keys": ["percentage", "supported_features"],
        }, before, expected_contract=contract)
        return checked_state(value.get("data"), entity_id)

    async def services(self, request, authorize, *, contract=None):
        async def before():
            authorize()
        value, selected = await self._call("ha_list_services", {
            "domain": "fan", "limit": 50, "offset": 0, "detail_level": "summary",
        }, before, expected_contract=contract)
        services = value.get("services")
        if (value.get("success") is not True or not isinstance(services, dict)
                or "fan." + request.action not in services):
            raise self.refusal("fan_service_unavailable")
        return selected

    async def dispatch(self, request, before, *, contract=None):
        # Revalidate at the provider boundary: no caller-supplied mapping gets
        # forwarded, even if the caller bypassed the MCP schema.
        request = self.request_type.model_validate(request.model_dump()).checked()
        value, _ = await self._call("ha_call_service", request.arguments(), before, expected_contract=contract)
        if value.get("success") is not True:
            raise self.refusal("fan_action_not_acknowledged")
        return digest({"acknowledged": True})
