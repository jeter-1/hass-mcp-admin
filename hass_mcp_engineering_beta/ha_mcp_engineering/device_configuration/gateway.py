"""The direct Home Assistant boundary for device-configuration control (§5.3-§5.4).

Exactly two WebSocket reads, one REST read and one closed ``call_service``
payload per domain. No other command is constructible here; nothing is
forwarded, and there is no ha-mcp delegation or fallback.
"""
from __future__ import annotations

from ..clients.paths import rest_path
from ..errors import (AuthorizationError, EntityNotFoundError, HomeAssistantApiError,
                      HomeAssistantTimeoutError, HomeAssistantUnavailableError)
from ..request_context import mark_provider_send
from .contracts import (PROVIDER, PROVIDER_CONTRACT, READ_COMMANDS, Refusal, digest,
                        service_call)

PROVIDER_UNAVAILABLE = "device_configuration_provider_unavailable"
_TRANSPORT_ERRORS = (AuthorizationError, HomeAssistantApiError, HomeAssistantTimeoutError,
                     HomeAssistantUnavailableError)


def _read_command(command: str, **fields) -> dict:
    if command not in READ_COMMANDS:
        raise Refusal(PROVIDER_UNAVAILABLE)
    return {"type": command, **fields}


class DeviceConfigurationGateway:
    """Permit only the §5.4 reads and the closed per-domain service payloads."""

    def __init__(self, rest_client, websocket_client):
        self.rest_client = rest_client
        self.websocket_client = websocket_client

    def health_snapshot(self) -> dict:
        return {
            "provider": PROVIDER, "provider_contract": PROVIDER_CONTRACT,
            "configured": True, "operational_status": "configured_unprobed",
            "health": "unknown", "fallback": "none", "fallback_count": 0,
            "fallback_policy": "none", "last_failure_category": None,
        }

    async def registry_entry(self, entity_id: str):
        """``config/entity_registry/get``; ``None`` only for Core's not_found."""
        try:
            return await self.websocket_client.command(
                _read_command("config/entity_registry/get", entity_id=entity_id))
        except HomeAssistantApiError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            if details.get("status") == 404:
                return None
            raise Refusal(PROVIDER_UNAVAILABLE) from None
        except _TRANSPORT_ERRORS:
            raise Refusal(PROVIDER_UNAVAILABLE) from None

    async def devices(self):
        try:
            return await self.websocket_client.command(
                _read_command("config/device_registry/list"))
        except _TRANSPORT_ERRORS:
            raise Refusal(PROVIDER_UNAVAILABLE) from None

    async def state(self, entity_id: str):
        try:
            return await self.rest_client.request(
                "GET", rest_path("/states/{}", entity_id, kinds=("entity_id",)))
        except EntityNotFoundError:
            raise Refusal("device_configuration_entity_unavailable") from None
        except _TRANSPORT_ERRORS:
            raise Refusal(PROVIDER_UNAVAILABLE) from None

    async def dispatch(self, entity_id: str, domain: str, value: str, once) -> str:
        """Run ``once`` (final composite check and intent), then send exactly once.

        The payload is built before ``once`` so an unbuildable value never
        reaches the intent boundary. A Core rejection after the send is never
        proof that nothing changed: only an authorization failure the client
        proves happened before the frame was written is ``call_not_sent``.
        """
        payload = service_call(entity_id, domain, value)
        await once()
        mark_provider_send()  # the exact send boundary
        try:
            await self.websocket_client.command(payload)
        except AuthorizationError as exc:
            refusal = Refusal("device_configuration_response_uncertain")
            if exc.call_not_sent is True:
                refusal = Refusal("device_configuration_provider_authority_retired")
                refusal.call_not_sent = True
            raise refusal from None
        except Exception:
            raise Refusal("device_configuration_response_uncertain") from None
        return digest({"provider_contract": PROVIDER_CONTRACT, "domain": domain,
                       "service": payload["service"], "acknowledged": True})
