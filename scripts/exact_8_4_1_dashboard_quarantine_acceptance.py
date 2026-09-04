"""Prove exact ha-mcp 8.4.1 dashboard authority is reviewed and bounded.

The legacy filename remains because the protected CI workflow invokes it. The
acceptance now proves the Beta 57 reviewed getter/setter authority. It performs
inventory and configuration reads only; the adjacent immutable-image setter
probe proves the reviewed upstream mutation boundary against a refusing fixture.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.clients.mcp import McpDashboardTransport  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
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
EXPECTED_SOURCE_COMMIT = "701a7c26ac0e2309c7883a627d31873ab1510077"
EXPECTED_IMAGE_INDEX_DIGEST = (
    "sha256:7823b36587a6e62efed271b26f3f72380b49f47364e5385580584e7ab2c60722"
)
EXPECTED_ATTESTATION_FINGERPRINT = (
    "8551a8d62593c3aed07884642374a0511a57968f106bf051893584ef76332952"
)
EXPECTED_CONSTRAINTS_FINGERPRINT = (
    "d064d856d6c10d9e023191b6dd08874030dae3df88ccc3cd954be588a4ffeba0"
)
EXPECTED_DASHBOARD_TOOLS = {
    "ha_config_get_dashboard",
    "ha_config_set_dashboard",
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DashboardAuthorityFailure(RuntimeError):
    """One exact dashboard authority invariant failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DashboardAuthorityFailure(message)


FAILURE_REASON_CODES = {
    "release entry changed": "release_entry_changed",
    "source changed": "source_changed",
    "immutable image authority changed": "image_authority_changed",
    "dashboard attestation is not reviewed": "attestation_not_reviewed",
    "dashboard provider disposition is not admitted": "provider_not_admitted",
    "dashboard attestation fingerprint changed": "attestation_fingerprint_changed",
    "dashboard compiled constraints changed": "constraints_fingerprint_changed",
    "exact 8.4.1 catalog validation failed": "catalog_validation_failed",
    "exact dashboard protocol changed": "protocol_changed",
    "exact dashboard descriptors are missing": "descriptors_missing",
    "dashboard getter classification changed": "getter_classification_changed",
    "dashboard setter classification changed": "setter_classification_changed",
    "exact synthetic dashboard is absent from inventory": "inventory_target_missing",
    "exact dashboard configuration read failed": "configuration_read_failed",
    "dashboard reads did not publish one exact provider authority": "provider_authority_mismatch",
    "dashboard provider authority omitted reviewed release evidence": "provider_authority_incomplete",
    "dashboard provider authority hashes are malformed": "provider_authority_hash_malformed",
    "reviewed dashboard provider accounting changed": "provider_accounting_changed",
    "dashboard provider boundary broadened": "provider_boundary_broadened",
    "planning-only dashboard authority acceptance mutated Home Assistant": "unexpected_mutation",
}


def fixture_stats(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=5) as response:  # noqa: S310 - fixed CI fixture
        value = json.load(response)
    require(isinstance(value, dict), "fixture statistics are malformed")
    return value


def provider_settings(endpoint: str) -> Settings:
    return Settings(
        ha_url="http://127.0.0.1:18123",
        ha_token="synthetic-dashboard-authority-token",
        access_secret="synthetic-dashboard-authority-secret",
        port=0,
        audit_path="synthetic-dashboard-authority-audit.jsonl",
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
    require(release.source_commit == EXPECTED_SOURCE_COMMIT, "source changed")
    require(
        release.image_index_digest == EXPECTED_IMAGE_INDEX_DIGEST,
        "immutable image authority changed",
    )
    require(
        release.dashboard_attestation_status == "reviewed",
        "dashboard attestation is not reviewed",
    )
    require(
        release.provider_disposition("dashboard") == "admitted",
        "dashboard provider disposition is not admitted",
    )
    require(
        release.dashboard_attestation_fingerprint
        == EXPECTED_ATTESTATION_FINGERPRINT,
        "dashboard attestation fingerprint changed",
    )
    require(
        release.dashboard_compiled_constraints_fingerprint
        == EXPECTED_CONSTRAINTS_FINGERPRINT,
        "dashboard compiled constraints changed",
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
    require(
        catalog.protocol_version == EXPECTED_PROTOCOL,
        "exact dashboard protocol changed",
    )
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
        "dashboard getter classification changed",
    )
    require(
        contracts["ha_config_set_dashboard"].policy_classification
        == "persistent_write",
        "dashboard setter classification changed",
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
    inventory = await provider.list_dashboards(limit=20, response_limit=60_000)
    dashboards = inventory.data.get("dashboards")
    require(
        isinstance(dashboards, list)
        and any(
            isinstance(item, dict)
            and item.get("url_path") == "compatibility-fixture"
            for item in dashboards
        ),
        "exact synthetic dashboard is absent from inventory",
    )
    exact = await provider.get_dashboard_config(
        url_path="compatibility-fixture",
        force_reload=True,
        response_limit=60_000,
    )
    require(
        exact.completeness == "complete"
        and exact.data.get("configuration_returned") is True
        and exact.data.get("url_path") == "compatibility-fixture",
        "exact dashboard configuration read failed",
    )
    inventory_authority = inventory.provider_authority
    exact_authority = exact.provider_authority
    require(
        isinstance(inventory_authority, dict)
        and inventory_authority == exact_authority,
        "dashboard reads did not publish one exact provider authority",
    )
    require(
        inventory_authority.get("model") == "f3-dashboard-provider-authority-v1"
        and inventory_authority.get("upstream_version") == EXPECTED_VERSION
        and inventory_authority.get("protocol_version") == EXPECTED_PROTOCOL
        and inventory_authority.get("compatibility_entry") == EXPECTED_ENTRY_ID
        and inventory_authority.get("source_commit") == EXPECTED_SOURCE_COMMIT
        and inventory_authority.get("image_index_digest")
        == EXPECTED_IMAGE_INDEX_DIGEST
        and inventory_authority.get("dashboard_attestation_fingerprint")
        == EXPECTED_ATTESTATION_FINGERPRINT
        and inventory_authority.get("compiled_constraints_fingerprint")
        == EXPECTED_CONSTRAINTS_FINGERPRINT,
        "dashboard provider authority omitted reviewed release evidence",
    )
    require(
        all(
            isinstance(inventory_authority.get(field), str)
            and SHA256.fullmatch(inventory_authority[field])
            for field in (
                "getter_contract_hash",
                "setter_contract_hash",
                "catalog_fingerprint",
                "provider_generation",
                "evidence_hash",
            )
        ),
        "dashboard provider authority hashes are malformed",
    )
    health = provider.health_snapshot()
    require(
        health.get("request_count") == 2
        and health.get("success_count") == 2
        and health.get("last_failure_category") is None
        and health.get("admission_status") == "admitted_builtin_attestation"
        and health.get("release_runtime_contract_match") is True
        and health.get("runtime_policy_state_normalized") is True,
        "reviewed dashboard provider accounting changed",
    )
    require(
        health.get("screenshots_allowed") is False
        and health.get("preference_writes_allowed") is False
        and health.get("governed_dashboard_write_route", {}).get("fallback")
        == "none",
        "dashboard provider boundary broadened",
    )
    after = fixture_stats(fixture_stats_url)
    require(
        before.get("http_mutations") == after.get("http_mutations")
        and before.get("websocket_mutations")
        == after.get("websocket_mutations"),
        "planning-only dashboard authority acceptance mutated Home Assistant",
    )
    return {
        "result": "PASS",
        "model": "ha-mcp-8.4.1-dashboard-authority-v1",
        "entry_id": release.entry_id,
        "version": catalog.server_version,
        "protocol": catalog.protocol_version,
        "catalog_validation": validation.validation_status,
        "dashboard_attestation_status": release.dashboard_attestation_status,
        "dashboard_provider_disposition": release.provider_disposition(
            "dashboard"
        ),
        "reviewed_descriptor_count": len(EXPECTED_DASHBOARD_TOOLS),
        "provider_authority_model": inventory_authority["model"],
        "provider_authority_fingerprint": inventory_authority["evidence_hash"],
        "inventory_read_count": 1,
        "configuration_read_count": 1,
        "provider_dispatch_count": 0,
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
        reason_code = (
            FAILURE_REASON_CODES.get(str(exc), "unknown_authority_failure")
            if isinstance(exc, DashboardAuthorityFailure)
            else "unexpected_exception"
        )
        args.output.write_text(
            json.dumps(
                {
                    "result": "FAIL",
                    "failure_type": type(exc).__name__[:96],
                    "failure_reason_code": reason_code,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        raise SystemExit(
            "exact 8.4.1 dashboard authority acceptance failed"
        ) from None
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("exact 8.4.1 dashboard authority acceptance: PASS")


if __name__ == "__main__":
    main()
