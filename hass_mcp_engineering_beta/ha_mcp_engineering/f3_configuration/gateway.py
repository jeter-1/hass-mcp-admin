"""Narrow bridge from F3-C1 adapters to the existing fixed gateway."""

from __future__ import annotations

from typing import Any

from ..clients.paths import rest_path
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
            rest_path("/states/{}", target_id, kinds=("entity_id",)),
            expected_statuses=frozenset({404}),
        )
        if isinstance(state, ExpectedHttpStatus) and state.status == 404:
            return True, "target_absent"
        return False, "target_entity_id_reserved"

    async def entity_metadata_preflight_evidence(
        self,
        target_id: str,
        current: dict[str, Any],
        proposed: dict[str, Any],
    ) -> tuple[str, ...]:
        """Recheck an entity plan's bound reference and consumer evidence."""

        return await self._gateway.entity_metadata_preflight_evidence(
            target_id, current, proposed
        )

    async def availability_exposure_preflight_evidence(
        self,
        target_id: str,
        current: dict[str, Any],
        proposed: dict[str, Any],
    ) -> tuple[str, ...]:
        """Recheck a 069B child's bound config-entry and consumer evidence."""

        return await self._gateway.availability_exposure_preflight_evidence(
            target_id, current, proposed
        )

    async def dashboard_metadata_preflight_evidence(
        self,
        target_id: str,
        current: dict[str, Any] | None,
        proposed: dict[str, Any],
    ) -> tuple[str, ...]:
        """Recheck a HAMCP-106 dashboard's bound metadata row and content."""

        return await self._gateway.dashboard_metadata_preflight_evidence(
            target_id, current, proposed
        )

    async def scene_configuration_preflight_evidence(
        self,
        target_id: str,
        current: dict[str, Any] | None,
        proposed: dict[str, Any],
    ) -> tuple[str, ...]:
        """Recheck a HAMCP-071 scene's bound configuration and registry entry."""

        return await self._gateway.scene_configuration_preflight_evidence(
            target_id, current, proposed
        )

    def scene_configuration_reload_evidence(
        self, target_id: str, operation_id: str
    ) -> frozenset[str]:
        """HAMCP-071 R2-H1: components a reload after this save loaded."""

        return self._gateway.scene_configuration.reload_evidence(
            target_id, operation_id
        )

    async def label_preflight_evidence(
        self,
        target_id: str,
        current: dict[str, Any] | None,
        proposed: dict[str, Any],
    ) -> tuple[str, ...]:
        """Recheck a HAMCP-165 label child's bound namespace."""

        return await self._gateway.label_preflight_evidence(
            target_id, current, proposed
        )

    # HAMCP-155: the closed delete reads and the one DELETE, routed only to
    # the dedicated delete gateway.

    async def delete_read_config(
        self, resource_type: str, target_id: str
    ) -> dict[str, Any] | None:
        return await self._gateway.configuration_delete.read_config(
            resource_type, target_id
        )

    async def delete_registry_entry(
        self, resource_type: str, target_id: str
    ) -> dict[str, Any] | None:
        return await self._gateway.configuration_delete.registry_entry(
            resource_type, target_id
        )

    async def delete_state_exists(self, entity_id: str) -> bool:
        return await self._gateway.configuration_delete.state_exists(entity_id)

    async def delete_consumer_fingerprint(self, entity_id: str) -> str | None:
        return await self._gateway.configuration_delete.consumer_fingerprint(
            entity_id
        )

    async def delete_configuration(
        self, resource_type: str, target_id: str
    ) -> Any:
        return await self._gateway.configuration_delete.delete(
            resource_type, target_id
        )

    # HAMCP-232: the closed options-flow reads, the held flow, its abort and
    # the one step submit, routed only to the dedicated options gateway.

    async def options_entry(self, entry_id: str) -> Any:
        return await self._gateway.helper_options.entry(entry_id)

    async def options_entry_entities(self, entry_id: str) -> list[str]:
        return await self._gateway.helper_options.entry_entities(entry_id)

    async def options_consumer_fingerprint(
        self, entity_ids: list[str]
    ) -> str | None:
        from ..governance.helper_options import consumer_fingerprint

        evidence = await self._gateway.helper_options.entry_consumer_evidence(
            list(entity_ids)
        )
        return None if evidence is None else consumer_fingerprint(evidence)

    async def options_read_visible(
        self, entry_id: str, *, domain: str, expected_step_id: str
    ) -> tuple[str, dict[str, Any], bool]:
        return await self._gateway.helper_options.read_visible(
            entry_id, domain=domain, expected_step_id=expected_step_id
        )

    async def options_open_held(
        self, entry_id: str, *, domain: str, expected_step_id: str
    ) -> Any:
        return await self._gateway.helper_options.open_held(
            entry_id, domain=domain, expected_step_id=expected_step_id
        )

    async def options_abort(self, flow_id: str) -> bool:
        return await self._gateway.helper_options.abort_flow(flow_id)

    def options_flow_unreleased(self, flow_id: str) -> None:
        self._gateway.helper_options.flow_unreleased(flow_id)

    async def options_submit(self, flow_id: str, submit: dict[str, Any]) -> Any:
        return await self._gateway.helper_options.submit_dispatch(flow_id, submit)

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
