"""Synthetic-only helpers for the isolated F3-B dashboard tests."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.f3_dashboard.json_codec import upstream_config_hash
from ha_mcp_engineering.f3_dashboard.models import (
    DashboardInventoryRow,
    DashboardPreread,
    DashboardUpdateProposal,
)
from ha_mcp_engineering.f3_dashboard.planning import create_dashboard_update_plan
from ha_mcp_engineering.f3_dashboard.provider import EXACT_CONTRACTS


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
    }[version]
    return DashboardPreread(
        inventory=(DashboardInventoryRow(url_path=url_path, mode=mode),),
        canonical_url_path=url_path,
        configuration=config,
        config_hash=config_hash or upstream_config_hash(config),
        completeness=completeness,
        configuration_returned=configuration_returned,
        sanitized=sanitized,
        truncated=truncated,
        preread_at="2026-08-04T12:00:00+00:00",
        upstream_version=version,
        protocol_version="2025-03-26",
        compatibility_entry=compatibility,
        dashboard_contract_model=contract,
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
        authoritative_provider_slug="hass-mcp-engineering",
        artifact_store=artifact_store,
        now=datetime(2026, 8, 4, 12, 5, tzinfo=timezone.utc),
        plan_id=plan_id,
    )
    return proposal, reader
