"""Prove exact ha-mcp 8.4.1 dashboard authority remains quarantined."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.clients.upstream_read import (  # noqa: E402
    McpReadGatewayTransport,
)
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    load_reviewed_upstream_release_registry,
    validate_reviewed_release_catalog,
)
from ha_mcp_engineering.version import SERVER_VERSION  # noqa: E402


EXPECTED_VERSION = "8.4.1"
EXPECTED_ENTRY_ID = "ha-mcp-v8.4.1-7823b365"
EXPECTED_PROTOCOL = "2025-03-26"
EXPECTED_DASHBOARD_TOOLS = {
    "ha_config_get_dashboard",
    "ha_config_set_dashboard",
}


class DashboardQuarantineFailure(RuntimeError):
    """One exact dashboard quarantine invariant failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DashboardQuarantineFailure(message)


async def run(endpoint: str) -> dict[str, Any]:
    release = load_reviewed_upstream_release_registry().by_version[
        EXPECTED_VERSION
    ]
    require(release.entry_id == EXPECTED_ENTRY_ID, "release entry changed")
    require(
        release.dashboard_attestation_status == "quarantined",
        "dashboard attestation is not quarantined",
    )
    require(
        release.provider_disposition("dashboard") == "held",
        "dashboard provider disposition is not held",
    )
    catalog = await McpReadGatewayTransport(
        endpoint,
        timeout_seconds=30.0,
        client_version=SERVER_VERSION,
    ).discover()
    validation = validate_reviewed_release_catalog(
        release,
        observed_server_name=catalog.server_name,
        observed_upstream_version=catalog.server_version,
        observed_protocol_version=catalog.protocol_version,
        tools=catalog.tools,
    )
    require(validation.valid, "exact 8.4.1 catalog validation failed")
    observed_names = {
        item.get("name") for item in catalog.tools if isinstance(item, dict)
    }
    require(
        EXPECTED_DASHBOARD_TOOLS <= observed_names,
        "exact dashboard descriptors are missing",
    )
    contracts = release.tool_contracts_by_name
    require(
        contracts["ha_config_get_dashboard"].policy_classification
        == "mixed_or_requires_wrapper",
        "dashboard read classification changed",
    )
    require(
        contracts["ha_config_set_dashboard"].policy_classification
        == "persistent_write",
        "dashboard write classification changed",
    )
    return {
        "result": "PASS",
        "model": "ha-mcp-8.4.1-dashboard-quarantine-v1",
        "entry_id": release.entry_id,
        "version": catalog.server_version,
        "protocol": catalog.protocol_version,
        "catalog_validation": validation.validation_status,
        "dashboard_attestation_status": (
            release.dashboard_attestation_status
        ),
        "dashboard_provider_disposition": release.provider_disposition(
            "dashboard"
        ),
        "reviewed_descriptor_count": len(EXPECTED_DASHBOARD_TOOLS),
        "provider_dispatch_count": 0,
        "fallback_count": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream-endpoint", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = asyncio.run(asyncio.wait_for(run(args.upstream_endpoint), 60))
    except Exception as exc:
        args.output.write_text(
            json.dumps(
                {
                    "result": "FAIL",
                    "failure_type": type(exc).__name__[:96],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        raise SystemExit(
            "exact 8.4.1 dashboard quarantine acceptance failed"
        ) from None
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("exact 8.4.1 dashboard quarantine acceptance: PASS")


if __name__ == "__main__":
    main()
