"""Prove exact ha-mcp 8.4.1 dashboard authority remains quarantined."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.clients.mcp import (  # noqa: E402
    McpDashboardTransport,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    DashboardProviderError,
    ErrorCode,
)
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    UpstreamDashboardProvider,
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


def fixture_stats(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=5) as response:  # noqa: S310 - fixed CI fixture
        value = json.load(response)
    require(isinstance(value, dict), "fixture statistics are malformed")
    return value


def provider_settings(endpoint: str) -> Settings:
    return Settings(
        ha_url="http://127.0.0.1:18123",
        ha_token="synthetic-dashboard-quarantine-token",
        access_secret="synthetic-dashboard-quarantine-secret",
        port=0,
        audit_path="synthetic-dashboard-quarantine-audit.jsonl",
        rate_limit_per_minute=120,
        rate_limit_burst=25,
        destructive_services=frozenset(),
        upstream_dashboard_mcp_url=endpoint,
    )


async def run(endpoint: str, fixture_stats_url: str) -> dict[str, Any]:
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
    catalog = await McpDashboardTransport(
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
    before = fixture_stats(fixture_stats_url)
    provider = UpstreamDashboardProvider()
    provider.configure(
        provider_settings(endpoint),
        transport=McpDashboardTransport(
            endpoint,
            timeout_seconds=30.0,
            client_version=SERVER_VERSION,
        ),
    )
    try:
        await provider.list_dashboards(limit=5, response_limit=60_000)
    except DashboardProviderError as exc:
        require(
            exc.code
            == ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_CONTRACT_MISMATCH,
            "held dashboard provider returned the wrong refusal",
        )
        require(
            exc.details.get("failure_category")
            == "reviewed_contract_mismatch",
            "held dashboard provider lost its exact refusal category",
        )
    else:
        raise DashboardQuarantineFailure(
            "held dashboard provider became actionable"
        )
    health = provider.health_snapshot()
    require(
        health.get("request_count") == 1
        and health.get("success_count") == 0
        and health.get("last_failure_category")
        == "reviewed_contract_mismatch"
        and health.get("last_successful_call_at") is None,
        "held dashboard provider accounting changed",
    )
    after = fixture_stats(fixture_stats_url)
    require(
        before.get("rest_reads") == after.get("rest_reads")
        and before.get("websocket_reads") == after.get("websocket_reads")
        and before.get("http_mutations") == after.get("http_mutations")
        and before.get("websocket_mutations")
        == after.get("websocket_mutations"),
        "held dashboard provider reached Home Assistant",
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
        "provider_attempt_count": health["request_count"],
        "provider_dispatch_count": (
            0 if before.get("websocket_reads") == after.get("websocket_reads")
            else 1
        ),
        "fallback_count": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream-endpoint", required=True)
    parser.add_argument("--fixture-stats-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = asyncio.run(
            asyncio.wait_for(
                run(args.upstream_endpoint, args.fixture_stats_url), 60
            )
        )
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
