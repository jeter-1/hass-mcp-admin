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
HOME_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "f3_dashboard"
    / "home_dashboard_existing.json"
)


def load_dashboard() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def load_home_dashboard() -> dict[str, Any]:
    """Load the deterministic Home-shaped existing-dashboard contract."""

    return json.loads(HOME_FIXTURE.read_text(encoding="utf-8"))


def home_dashboard_patch_operations() -> list[dict[str, Any]]:
    """Return the exact Cleaner, Outdoor, and Needs Attention delta."""

    cleaner = {
        "type": "template",
        "content": "Cleaner",
        "entity": "input_boolean.cleaner_mode",
        "icon": "mdi:broom",
    }
    outdoor = {
        "type": "template",
        "content": "Outdoor",
        "entity": "sensor.local_outdoor_temperature",
    }
    needs_attention = {
        "title": "Needs Attention",
        "cards": [
            {"type": "heading", "heading": "Needs Attention"},
            {
                "type": "conditional",
                "conditions": [
                    {
                        "condition": "or",
                        "conditions": [
                            {
                                "condition": "state",
                                "entity": "cover.garage_door",
                                "state": "open",
                            },
                            {
                                "condition": "state",
                                "entity": "lock.front_door",
                                "state": "unlocked",
                            },
                            {
                                "condition": "state",
                                "entity": "binary_sensor.exterior_doors",
                                "state": "on",
                            },
                            {
                                "condition": "and",
                                "conditions": [
                                    {
                                        "condition": "state",
                                        "entity": "input_select.home_mode",
                                        "state": "Away",
                                    },
                                    {
                                        "condition": "state",
                                        "entity": "alarm_control_panel.home",
                                        "state_not": "armed_away",
                                    },
                                ],
                            },
                            {
                                "condition": "state",
                                "entity": "climate.home",
                                "state": "unavailable",
                            },
                            {
                                "condition": "state",
                                "entity": "binary_sensor.garage_presence",
                                "state": "unavailable",
                            },
                            {
                                "condition": "state",
                                "entity": "sensor.local_outdoor_temperature",
                                "state": "unavailable",
                            },
                        ],
                    }
                ],
                "card": {
                    "type": "entities",
                    "title": "Needs Attention",
                    "entities": [
                        "cover.garage_door",
                        "lock.front_door",
                        "binary_sensor.exterior_doors",
                        "alarm_control_panel.home",
                        "climate.home",
                        "binary_sensor.garage_presence",
                        "sensor.local_outdoor_temperature",
                    ],
                },
            },
        ],
    }
    return [
        {
            "operation_id": "insert-cleaner-chip",
            "operation": "add",
            "path": "/views/0/sections/0/cards/1/chips/2",
            "value": cleaner,
        },
        {
            "operation_id": "replace-climate-chip",
            "operation": "replace",
            "path": "/views/0/sections/1/cards/1/chips/2",
            "value": outdoor,
        },
        {
            "operation_id": "remove-prompted-chip",
            "operation": "remove",
            "path": "/views/0/sections/1/cards/1/chips/3",
        },
        {
            "operation_id": "insert-needs-attention",
            "operation": "add",
            "path": "/views/0/sections/0/cards/-",
            "value": needs_attention,
        },
    ]


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
