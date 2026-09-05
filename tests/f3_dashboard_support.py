"""Synthetic-only helpers for the isolated F3-B dashboard tests."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.f3_dashboard.identity import (  # noqa: E402
    build_operational_identity,
    build_provider_authority,
    reviewed_tool_contract_hash,
)
from ha_mcp_engineering.f3_dashboard.json_codec import (  # noqa: E402
    engineering_sha256,
    upstream_config_hash,
)
from ha_mcp_engineering.f3_dashboard.models import (
    DashboardInventoryRow,
    DashboardPreread,
    DashboardUpdateProposal,
)
from ha_mcp_engineering.f3_dashboard.planning import create_dashboard_update_plan
from ha_mcp_engineering.f3_dashboard.provider import EXACT_CONTRACTS
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    load_reviewed_upstream_release_registry,
)


FIXTURE = Path(__file__).parent / "fixtures" / "f3_dashboard" / "storage_dashboard.json"


def load_dashboard() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def make_preread(
    configuration: dict[str, Any] | None = None,
    *,
    url_path: str = "synthetic-dashboard",
    version: str = "8.1.1",
    mode: str = "storage",
    completeness: str = "complete",
    configuration_returned: bool = True,
    sanitized: bool = False,
    truncated: bool = False,
    config_hash: str | None = None,
) -> DashboardPreread:
    config = deepcopy(configuration if configuration is not None else load_dashboard())
    contract = (
        "ha_mcp_dashboard_read_v2"
        if version == "7.14.2"
        else "ha_mcp_dashboard_read_v3"
    )
    compatibility = {
        "7.14.2": "ha-mcp-v7.14.2-7917b2d3",
        "8.0.0": "ha-mcp-v8.0.0-d65630f6",
        "8.1.1": "ha-mcp-v8.1.1-e1d76a6e",
        "8.2.0": "ha-mcp-v8.2.0-dbcfc0ee",
        "8.4.1": "ha-mcp-v8.4.1-7823b365",
        "8.4.3": "ha-mcp-v8.4.3-d5cea47a",
    }[version]
    release = load_reviewed_upstream_release_registry().by_version[version]
    getter = release.tool_contracts_by_name["ha_config_get_dashboard"]
    setter = EXACT_CONTRACTS[version]
    authority = build_provider_authority(
        provider_slug="ha_mcp",
        server_name="ha-mcp",
        upstream_version=version,
        protocol_version="2025-03-26",
        compatibility_entry=compatibility,
        source_commit=release.source_commit,
        image_index_digest=release.image_index_digest,
        contract_family=contract,
        dashboard_attestation_fingerprint=(
            release.dashboard_attestation_fingerprint or "0" * 64
        ),
        compiled_constraints_fingerprint=(
            release.dashboard_compiled_constraints_fingerprint or "0" * 64
        ),
        getter_contract_hash=reviewed_tool_contract_hash(getter),
        setter_contract_hash=engineering_sha256(asdict(setter)),
        catalog_fingerprint=release.catalog_fingerprint,
    )
    upstream_hash = config_hash or upstream_config_hash(config)
    operational_identity = build_operational_identity(
        authority,
        target_url_path=url_path,
        storage_mode="storage",
        baseline_upstream_config_hash=upstream_hash,
        baseline_engineering_sha256=engineering_sha256(config),
    )
    return DashboardPreread(
        inventory=(DashboardInventoryRow(url_path=url_path, mode=mode),),
        canonical_url_path=url_path,
        configuration=config,
        config_hash=upstream_hash,
        completeness=completeness,
        configuration_returned=configuration_returned,
        sanitized=sanitized,
        truncated=truncated,
        preread_at="2026-08-04T12:00:00+00:00",
        upstream_version=version,
        protocol_version="2025-03-26",
        compatibility_entry=compatibility,
        dashboard_contract_model=contract,
        operational_identity=operational_identity,
    )


class FakeExactReader:
    def __init__(self, preread: DashboardPreread) -> None:
        self.value = preread
        self.preread_count = 0
        self.mutation_count = 0

    async def preread(self, *, url_path: str) -> DashboardPreread:
        self.preread_count += 1
        return self.value


async def make_proposal(
    *,
    preread: DashboardPreread | None = None,
    operations: list[dict[str, Any]] | None = None,
    artifact_store: Any = None,
    url_path: str = "synthetic-dashboard",
    plan_id: str = "plan000000000001",
) -> tuple[DashboardUpdateProposal, FakeExactReader]:
    exact = preread or make_preread(url_path=url_path)
    reader = FakeExactReader(exact)
    proposal = await create_dashboard_update_plan(
        reader=reader,
        url_path=url_path,
        operations=operations
        or [
            {
                "operation_id": "rename-title",
                "operation": "replace",
                "path": "/title",
                "value": "Synthetic F3 dashboard updated",
            }
        ],
        title="Synthetic dashboard update",
        description="Offline fixture-only plan.",
        expiration_minutes=30,
        requested_by="test.operator",
        provider_evidence=EXACT_CONTRACTS[exact.upstream_version],
        authoritative_provider_slug="ha_mcp",
        artifact_store=artifact_store,
        now=datetime(2026, 8, 4, 12, 5, tzinfo=timezone.utc),
        plan_id=plan_id,
    )
    return proposal, reader
