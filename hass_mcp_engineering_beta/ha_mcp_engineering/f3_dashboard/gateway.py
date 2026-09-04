"""Closed bridge from governed dashboard operations to the exact provider."""

from __future__ import annotations

from typing import Any

from ..providers.upstream_dashboard import UpstreamDashboardProvider
from .errors import RawEvidenceError
from .identity import (
    build_operational_identity,
    provider_authority_from_mapping,
)
from .models import DashboardInventoryRow, DashboardPreread


class DashboardExecutionGateway:
    """Expose exact reads and one fixed setter without broad forwarding."""

    def __init__(self, provider: UpstreamDashboardProvider, *, response_limit: int):
        self.provider = provider
        self.response_limit = response_limit

    async def preread(self, *, url_path: str) -> DashboardPreread:
        inventory = await self.provider.list_dashboards(
            limit=200, response_limit=self.response_limit
        )
        configuration = await self.provider.get_dashboard_config(
            url_path=url_path,
            force_reload=True,
            response_limit=self.response_limit,
        )
        if inventory.completeness != "complete":
            raise RawEvidenceError("Dashboard inventory is incomplete")
        if configuration.completeness != "complete":
            raise RawEvidenceError("Dashboard configuration is incomplete")
        rows = inventory.data.get("dashboards")
        config = configuration.data.get("configuration")
        if not isinstance(rows, list) or not isinstance(config, dict):
            raise RawEvidenceError("Dashboard provider response is malformed")
        metadata = configuration.metadata
        inventory_authority_value = inventory.provider_authority
        configuration_authority_value = configuration.provider_authority
        if (
            not isinstance(inventory_authority_value, dict)
            or not isinstance(configuration_authority_value, dict)
            or inventory_authority_value != configuration_authority_value
        ):
            raise RawEvidenceError(
                "Dashboard getter and setter authority is unavailable or changed"
            )
        provider_authority = provider_authority_from_mapping(
            configuration_authority_value
        )
        server = metadata.get("upstream_server")
        if not isinstance(server, dict):
            raise RawEvidenceError("Dashboard provider identity is unavailable")
        compatibility_entry = metadata.get("attestation_entry_id")
        protocol = metadata.get("mcp_protocol_version")
        version = server.get("version")
        contract_model = metadata.get("contract_family")
        if not all(
            isinstance(value, str) and value
            for value in (
                compatibility_entry,
                protocol,
                version,
                contract_model,
            )
        ):
            raise RawEvidenceError("Dashboard provider contract evidence is incomplete")
        inventory_rows: list[DashboardInventoryRow] = []
        for row in rows:
            if not isinstance(row, dict):
                raise RawEvidenceError("Dashboard inventory row is malformed")
            path = row.get("url_path")
            mode = row.get("mode")
            dashboard_id = row.get("id") or row.get("dashboard_id")
            if not isinstance(path, str) or not isinstance(mode, str):
                raise RawEvidenceError("Dashboard inventory identity is malformed")
            if dashboard_id is not None and not isinstance(dashboard_id, str):
                raise RawEvidenceError("Dashboard inventory ID is malformed")
            inventory_rows.append(
                DashboardInventoryRow(
                    url_path=path,
                    mode=mode,
                    dashboard_id=dashboard_id,
                )
            )
        canonical_path = configuration.data.get("url_path")
        config_hash = configuration.data.get("config_hash")
        engineering_hash = configuration.data.get("engineering_config_hash")
        if (
            not isinstance(canonical_path, str)
            or not isinstance(config_hash, str)
            or not isinstance(engineering_hash, str)
        ):
            raise RawEvidenceError("Dashboard configuration identity is malformed")
        matching_rows = [
            row for row in inventory_rows if row.url_path == canonical_path
        ]
        if len(matching_rows) != 1:
            raise RawEvidenceError("Dashboard inventory identity is absent or ambiguous")
        operational_identity = build_operational_identity(
            provider_authority,
            target_url_path=canonical_path,
            storage_mode=matching_rows[0].mode,
            baseline_upstream_config_hash=config_hash,
            baseline_engineering_sha256=engineering_hash,
        )
        return DashboardPreread(
            inventory=tuple(inventory_rows),
            canonical_url_path=canonical_path,
            configuration=config,
            config_hash=config_hash,
            completeness="complete",
            configuration_returned=True,
            sanitized=False,
            truncated=False,
            preread_at=str(metadata.get("source_timestamp") or ""),
            upstream_version=version,
            protocol_version=protocol,
            compatibility_entry=compatibility_entry,
            dashboard_contract_model=contract_model,
            operational_identity=operational_identity,
        )

    async def best_practice_key(
        self, *, expected_provider_authority_evidence_hash: str
    ) -> str:
        return await self.provider.best_practices_acknowledgement_key(
            expected_provider_authority_evidence_hash=(
                expected_provider_authority_evidence_hash
            )
        )

    async def write(
        self,
        *,
        url_path: str,
        configuration: dict[str, Any],
        config_hash: str,
        best_practice_key: str,
        expected_provider_authority_evidence_hash: str,
    ) -> dict[str, Any]:
        return await self.provider.execute_governed_dashboard_update(
            url_path=url_path,
            configuration=configuration,
            config_hash=config_hash,
            best_practice_key=best_practice_key,
            expected_provider_authority_evidence_hash=(
                expected_provider_authority_evidence_hash
            ),
        )


__all__ = ["DashboardExecutionGateway"]
