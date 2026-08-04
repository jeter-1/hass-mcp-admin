"""Narrow bridge from F3-C1 adapters to the existing fixed gateway."""

from __future__ import annotations

from typing import Any

from ..clients.rest import ExpectedHttpStatus
from ..governance.resources import ConfigurationResourceGateway


class ExistingConfigurationGatewayBridge:
    """Expose only existing reviewed reads, validation, and fixed writes.

    The bridge is not instantiated by current runtime code.  It exists so the
    later F3 integration does not need a generic REST/WebSocket forwarding
    surface.
    """

    provider_admitted = True

    def __init__(self, gateway: ConfigurationResourceGateway) -> None:
        if not isinstance(gateway, ConfigurationResourceGateway):
            raise TypeError("the exact configuration gateway is required")
        self._gateway = gateway

    async def read(
        self, resource_type: str, target_id: str
    ) -> dict[str, Any] | None:
        return await self._gateway.read(resource_type, target_id)

    async def validate_all(self) -> Any:
        return await self._gateway.validate_all()

    async def create_target_absent(
        self, resource_type: str, target_id: str
    ) -> tuple[bool, str]:
        """Repeat the existing storage/YAML collision checks before intent."""

        current = await self._gateway.get(resource_type, target_id)
        if current is not None:
            return False, "target_already_exists"
        if resource_type not in {"input_boolean", "input_number"}:
            return True, "target_absent"
        state = await self._gateway.rest_client.request(
            "GET",
            f"/states/{target_id}",
            expected_statuses=frozenset({404}),
        )
        if isinstance(state, ExpectedHttpStatus) and state.status == 404:
            return True, "target_absent"
        return False, "target_entity_id_reserved"

    async def write(
        self,
        action: str,
        resource_type: str,
        target_id: str,
        proposed_config: dict[str, Any],
    ) -> Any:
        return await self._gateway.write(
            action,
            resource_type,
            target_id,
            proposed_config,
        )
