"""Planning-only acceptance for exact immutable ha-mcp add-on runtimes.

CI starts the reviewed add-on image and a deterministic synthetic Home
Assistant fixture before invoking this script.  The acceptance executes the
real MCP initialize/tools-list surface, automatic-read admission, Dashboard
v3 reads, and governed backup/lifecycle planning.  It never approves or
applies a plan and requires every provider dispatch counter to remain zero.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
import tempfile
from typing import Any
from urllib.request import urlopen

from mcp.server.fastmcp import FastMCP


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.clients.mcp import McpDashboardTransport  # noqa: E402
from ha_mcp_engineering.clients.rest import HomeAssistantRestClient  # noqa: E402
from ha_mcp_engineering.clients.upstream_read import (  # noqa: E402
    McpReadGatewayTransport,
)
from ha_mcp_engineering.clients.websocket import (  # noqa: E402
    HomeAssistantWebSocketClient,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.governance.operational import (  # noqa: E402
    BackupAdministrationGateway,
)
from ha_mcp_engineering.governance.operational_lifecycle import (  # noqa: E402
    OperationalLifecycleGateway,
    UPSTREAM_PROVIDER_CONTRACT_FIELDS,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.providers.operational_backup import (  # noqa: E402
    ReviewedOperationalBackupProvider,
)
from ha_mcp_engineering.providers.operational_lifecycle import (  # noqa: E402
    ReviewedOperationalLifecycleProvider,
)
from ha_mcp_engineering.providers.supervisor_self import (  # noqa: E402
    SupervisorSelfAddonIdentity,
)
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    UpstreamDashboardProvider,
)
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from ha_mcp_engineering.tools import registered_tools  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    REVIEWED_NORMALIZED_CATALOG_FINGERPRINT_MODEL_V1,
    catalog_fingerprint,
    load_reviewed_upstream_release_registry,
    validate_reviewed_release_catalog,
)
from ha_mcp_engineering.version import SERVER_VERSION  # noqa: E402


EXPECTED_UPSTREAM_VERSION = "8.0.0"
EXPECTED_PROTOCOL = "2025-03-26"
EXPECTED_ENTRY_ID = "ha-mcp-v8.0.0-d65630f6"
EXPECTED_RAW_CATALOG_FINGERPRINT = (
    "c61b0959e766f3900300dd4dd69a6d799fc113186d91983f21be69f1bc6b8768"
)
EXPECTED_NORMALIZED_CATALOG_FINGERPRINT = (
    "3bad86b86400807ceddf68805cf4ed86d1243f201104e18ed8d3c15e560a1d53"
)
EXPECTED_TOOL_COUNT = 78
EXPECTED_AUTOMATIC_READ_COUNT = 24
EXPECTED_HELD_TOOLS = {"ha_get_operation_status", "ha_search"}
EXPECTED_DASHBOARD_RUNTIME_FINGERPRINT = (
    "fb7f3789c8c020d8636a96b85a207635e94eefe9e0944c8814de59aba17e532e"
)
EXPECTED_LIFECYCLE_ADDON_RESPONSE_MODEL = (
    "ha-mcp-lifecycle-addon-structured-content-v1"
)
EXPECTED_LIFECYCLE_ADDON_RESPONSE_ENVELOPE = (
    "mcp-direct-structured-content-v1"
)
EXPECTED_SOURCE_DERIVED_MINIMUM_DETAIL_BYTES = 71_986
EXPECTED_ADDON_DETAIL_PROFILE = "live-8.0.0"
ACCEPTANCE_TIMEOUT_SECONDS = 180

EXACT_ADDON_PROFILES = {
    "8.0.0": {
        "entry_id": "ha-mcp-v8.0.0-d65630f6",
        "raw_catalog_fingerprint": (
            "c61b0959e766f3900300dd4dd69a6d799fc113186d91983f21be69f1bc6b8768"
        ),
        "normalized_catalog_fingerprint": (
            "3bad86b86400807ceddf68805cf4ed86d1243f201104e18ed8d3c15e560a1d53"
        ),
        "dashboard_runtime_fingerprint": (
            "fb7f3789c8c020d8636a96b85a207635e94eefe9e0944c8814de59aba17e532e"
        ),
        "addon_detail_profile": "live-8.0.0",
    },
    "8.1.0": {
        "entry_id": "ha-mcp-v8.1.0-4c07e625",
        "raw_catalog_fingerprint": (
            "6b5cd123cc60ff6668c2ff4dd1f9cedbe6a7a21fe43fe00471cd46611d4406d7"
        ),
        "normalized_catalog_fingerprint": (
            "5ec7b1f4a4c2ffabb2acc14c73a230f08a5f94908b6f27e57cb6739d662f03d7"
        ),
        "dashboard_runtime_fingerprint": (
            "fb7f3789c8c020d8636a96b85a207635e94eefe9e0944c8814de59aba17e532e"
        ),
        "addon_detail_profile": "live-8.1.0",
    },
    "8.1.1": {
        "entry_id": "ha-mcp-v8.1.1-e1d76a6e",
        "raw_catalog_fingerprint": (
            "6b5cd123cc60ff6668c2ff4dd1f9cedbe6a7a21fe43fe00471cd46611d4406d7"
        ),
        "normalized_catalog_fingerprint": (
            "d652dc34b263d325d3b074dda436646d132b7e05018011934fea9d4460bc29f4"
        ),
        "dashboard_runtime_fingerprint": (
            "fb7f3789c8c020d8636a96b85a207635e94eefe9e0944c8814de59aba17e532e"
        ),
        "addon_detail_profile": "live-8.1.1",
    },
}


def _select_exact_addon_profile(version: str) -> None:
    """Select one closed exact-release acceptance profile."""

    profile = EXACT_ADDON_PROFILES.get(version)
    if profile is None:
        raise AcceptanceFailure("unsupported exact add-on acceptance profile")
    global EXPECTED_UPSTREAM_VERSION
    global EXPECTED_ENTRY_ID
    global EXPECTED_RAW_CATALOG_FINGERPRINT
    global EXPECTED_NORMALIZED_CATALOG_FINGERPRINT
    global EXPECTED_DASHBOARD_RUNTIME_FINGERPRINT
    global EXPECTED_ADDON_DETAIL_PROFILE
    EXPECTED_UPSTREAM_VERSION = version
    EXPECTED_ENTRY_ID = str(profile["entry_id"])
    EXPECTED_RAW_CATALOG_FINGERPRINT = str(
        profile["raw_catalog_fingerprint"]
    )
    EXPECTED_NORMALIZED_CATALOG_FINGERPRINT = str(
        profile["normalized_catalog_fingerprint"]
    )
    EXPECTED_DASHBOARD_RUNTIME_FINGERPRINT = str(
        profile["dashboard_runtime_fingerprint"]
    )
    EXPECTED_ADDON_DETAIL_PROFILE = str(profile["addon_detail_profile"])


class AcceptanceFailure(RuntimeError):
    """Bounded acceptance failure without untrusted runtime content."""


class _UnusedConfigurationGateway:
    """Unused configuration boundary required by the governance service."""


class _LifecycleEnvelopeRecordingTransport(McpReadGatewayTransport):
    """Record bounded envelope facts without retaining add-on response data."""

    detail_text_bytes: int | None = None
    detail_text_item_count: int | None = None
    detail_structured_content_present: bool = False

    async def execute_read(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        **kwargs: Any,
    ):
        observed = await super().execute_read(
            tool_name,
            arguments,
            **kwargs,
        )
        if tool_name == "ha_get_addon" and "slug" in arguments:
            result = observed.call_result
            content = result.get("content")
            self.detail_text_item_count = (
                len(content) if isinstance(content, list) else None
            )
            text = (
                content[0].get("text")
                if isinstance(content, list)
                and len(content) == 1
                and isinstance(content[0], dict)
                and content[0].get("type") == "text"
                else None
            )
            self.detail_text_bytes = (
                len(text.encode("utf-8"))
                if isinstance(text, str)
                else None
            )
            self.detail_structured_content_present = isinstance(
                result.get("structuredContent"),
                dict,
            )
        return observed


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceFailure(message)


def fixture_stats(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=5) as response:  # noqa: S310 - fixed CI fixture URL
        value = json.load(response)
    require(isinstance(value, dict), "fixture stats were not an object")
    return value


def _settings(args: argparse.Namespace, audit_path: Path) -> Settings:
    return Settings(
        ha_url=args.ha_url,
        ha_token=args.ha_token,
        access_secret="synthetic-addon-runtime-engineering-secret",
        port=0,
        audit_path=str(audit_path),
        rate_limit_per_minute=120,
        rate_limit_burst=25,
        destructive_services=frozenset(),
        response_size_limit=100_000,
        upstream_dashboard_mcp_url=args.configured_upstream_endpoint,
    )


def _runtime_snapshot(
    *, observed_catalog_fingerprint: str
) -> dict[str, Any]:
    return {
        "server_version": SERVER_VERSION,
        "build_sha": "0" * 40,
        "registered_tool_count": 72,
        "engineering_tool_count": 48,
        "delegated_tool_count": 24,
        "governance_storage_status": "healthy",
        "governance_plan_count": 0,
        "audit_storage_status": "healthy",
        "audit_write_failures": 0,
        "dependency_index_state": "valid",
        "dependency_prewarm_state": "complete",
        "upstream_version": EXPECTED_UPSTREAM_VERSION,
        "upstream_protocol": EXPECTED_PROTOCOL,
        "upstream_catalog_fingerprint": observed_catalog_fingerprint,
        "upstream_admission_status": "admitted_exact",
        "fallback_count": 0,
    }


async def _automatic_read_acceptance(
    settings: Settings,
    transport: McpReadGatewayTransport,
) -> dict[str, Any]:
    gateway = UpstreamReadGateway()
    gateway.configure(settings, transport=transport)
    server = FastMCP("exact-addon-runtime-acceptance")
    health = await gateway.initialize(server)

    require(health.get("admission_status") == "admitted_exact", "automatic reads were not admitted")
    require(health.get("selected_compatibility_entry_id") == EXPECTED_ENTRY_ID, "automatic reads selected the wrong release")
    require(health.get("observed_advertised_tool_count") == EXPECTED_TOOL_COUNT, "automatic-read catalog count changed")
    require(health.get("dynamically_exposed_count") == EXPECTED_AUTOMATIC_READ_COUNT, "automatic-read exposure count changed")
    require(set(health.get("held_tools") or []) == EXPECTED_HELD_TOOLS, "held-tool set changed")
    require(health.get("fallback_count") == 0, "automatic-read fallback occurred")

    published = registered_tools(server)
    require(len(published.values()) == EXPECTED_AUTOMATIC_READ_COUNT, "dynamic registry count changed")
    require(not EXPECTED_HELD_TOOLS.intersection(published), "held tool became callable")
    state_tool = published.get("ha_get_state")
    require(state_tool is not None, "representative automatic read was not exposed")
    response = json.loads(await state_tool.run({"entity_id": "sun.sun"}))
    require(response.get("success") is True, "representative automatic read failed")
    metadata = response.get("metadata") or {}
    require(metadata.get("provider") == "upstream_read_gateway", "automatic read used the wrong provider")
    require(metadata.get("upstream_version") == EXPECTED_UPSTREAM_VERSION, "automatic read used the wrong release")
    require(metadata.get("fallback") == "none", "automatic read used fallback")
    return {
        "admission_status": health.get("admission_status"),
        "compatibility_entry_id": health.get("selected_compatibility_entry_id"),
        "advertised_tool_count": health.get("observed_advertised_tool_count"),
        "dynamic_tool_count": health.get("dynamically_exposed_count"),
        "held_tools": sorted(EXPECTED_HELD_TOOLS),
        "representative_read": "ha_get_state",
        "fallback_count": health.get("fallback_count"),
    }


async def _dashboard_acceptance(
    settings: Settings,
    endpoint: str,
) -> dict[str, Any]:
    provider = UpstreamDashboardProvider()
    provider.configure(
        settings,
        transport=McpDashboardTransport(
            endpoint,
            timeout_seconds=30.0,
            client_version=SERVER_VERSION,
        ),
    )
    inventory = await provider.list_dashboards(
        limit=20,
        response_limit=settings.response_size_limit,
    )
    dashboards = inventory.data.get("dashboards")
    require(
        isinstance(dashboards, list)
        and any(
            isinstance(item, dict)
            and item.get("url_path") == "compatibility-fixture"
            for item in dashboards
        ),
        "dashboard inventory did not return the exact synthetic dashboard",
    )
    exact = await provider.get_dashboard_config(
        url_path="compatibility-fixture",
        force_reload=True,
        response_limit=settings.response_size_limit,
    )
    require(exact.data.get("configuration_returned") is True, "exact dashboard configuration was not returned")
    require(exact.data.get("url_path") == "compatibility-fixture", "dashboard path was not exact")

    health = provider.health_snapshot()
    require(health.get("contract_family") == "ha_mcp_dashboard_read_v3", "dashboard contract family changed")
    require(health.get("admission_status") == "admitted_builtin_attestation", "dashboard descriptor was not admitted")
    require(health.get("runtime_policy_state_normalized") is True, "dashboard policy projection was not applied")
    require(health.get("release_runtime_contract_match") is True, "dashboard release runtime contract did not match")
    require(health.get("observed_release_runtime_contract_fingerprint") == EXPECTED_DASHBOARD_RUNTIME_FINGERPRINT, "dashboard release runtime fingerprint changed")
    require(health.get("screenshots_allowed") is False, "dashboard screenshots became reachable")
    require(health.get("preference_writes_allowed") is False, "dashboard preference writes became reachable")
    return {
        "contract_family": health.get("contract_family"),
        "admission_status": health.get("admission_status"),
        "runtime_policy_state_normalized": health.get(
            "runtime_policy_state_normalized"
        ),
        "release_runtime_contract_fingerprint": health.get(
            "observed_release_runtime_contract_fingerprint"
        ),
        "inventory_count": inventory.data.get("count"),
        "exact_config_returned": exact.data.get("configuration_returned"),
        "screenshots_allowed": health.get("screenshots_allowed"),
        "preference_writes_allowed": health.get("preference_writes_allowed"),
    }


async def _planning_acceptance(
    settings: Settings,
    endpoint: str,
    root: Path,
    observed_catalog_fingerprint: str,
) -> dict[str, Any]:
    websocket = HomeAssistantWebSocketClient(settings)
    rest = HomeAssistantRestClient(settings)
    backup_provider = ReviewedOperationalBackupProvider()
    backup_provider.configure(
        settings,
        transport=McpReadGatewayTransport(
            endpoint,
            timeout_seconds=30.0,
            client_version=SERVER_VERSION,
        ),
    )
    lifecycle_transport = _LifecycleEnvelopeRecordingTransport(
        endpoint,
        timeout_seconds=30.0,
        client_version=SERVER_VERSION,
    )
    lifecycle_provider = ReviewedOperationalLifecycleProvider()
    lifecycle_provider.configure(
        settings,
        transport=lifecycle_transport,
    )

    async def configuration_validator() -> dict[str, Any]:
        return {"result": "valid", "errors": None}

    async def self_identity() -> SupervisorSelfAddonIdentity:
        return SupervisorSelfAddonIdentity(
            slug="df26dea6_hass_mcp_engineering_beta",
            name="HA MCP Engineering Server Beta",
            version=SERVER_VERSION,
            repository="df26dea6",
        )

    lifecycle_gateway = OperationalLifecycleGateway(
        lifecycle_provider,
        rest,
        websocket,
        configuration_validator=configuration_validator,
        runtime_snapshot=lambda: _runtime_snapshot(
            observed_catalog_fingerprint=observed_catalog_fingerprint
        ),
        process_instance_id="exact-addon-runtime-planning",
        self_addon_identity_resolver=self_identity,
        sensitive_values=(settings.access_secret, settings.ha_token),
    )
    repository = ChangePlanRepository(root / "plans")
    service = ChangeGovernanceService(
        repository,
        _UnusedConfigurationGateway(),
        AuditLogger(str(root / "audit.jsonl"), settings.access_secret),
        operational_gateway=BackupAdministrationGateway(
            backup_provider,
            websocket,
        ),
        lifecycle_gateway=lifecycle_gateway,
    )

    proposals = {
        "backup": await service.create_backup_plan(
            backup_name="Exact add-on runtime planning only",
            expiration_minutes=5,
        ),
        "reload": await service.create_reload_plan(
            reload_target="automation",
            expiration_minutes=5,
        ),
        "addon_restart": await service.create_addon_restart_plan(
            addon_slug="abcdef12_ha_mcp",
            expiration_minutes=5,
        ),
        "home_assistant_restart": (
            await service.create_home_assistant_restart_plan(
                expiration_minutes=5,
            )
        ),
    }
    for name, proposal in proposals.items():
        require(proposal.get("proposal_only") is True, f"{name} was not proposal-only")
        require(proposal.get("provider_dispatch_occurred") is False, f"{name} dispatched during planning")

    persisted = repository.list()
    require(len(persisted) == 4, "planning did not persist exactly four proposals")
    addon_plan = next(
        (
            plan
            for plan in persisted
            if plan.operation.value == "restart_addon"
        ),
        None,
    )
    require(addon_plan is not None and addon_plan.operational is not None, "add-on restart plan was not persisted")
    binding = addon_plan.operational.baseline.get(
        "upstream_addon_identity"
    )
    require(
        isinstance(binding, dict)
        and binding.get("slug") == "abcdef12_ha_mcp"
        and binding.get("endpoint_host") == "abcdef12-ha-mcp"
        and binding.get("installed_version") == EXPECTED_UPSTREAM_VERSION,
        "authoritative upstream add-on binding was incomplete",
    )
    provider_contract = binding.get("provider_contract") or {}
    require(
        all(field in provider_contract for field in UPSTREAM_PROVIDER_CONTRACT_FIELDS),
        "authoritative add-on binding omitted provider contract evidence",
    )

    backup_health = backup_provider.health_snapshot()
    lifecycle_health = lifecycle_provider.health_snapshot()
    backup_dispatch_count = backup_health.get("dispatch_count")
    lifecycle_dispatch_counts = lifecycle_health.get("dispatch_counts") or {}
    require(backup_dispatch_count == 0, "backup planning dispatched a backup")
    require(sum(lifecycle_dispatch_counts.values()) == 0, "lifecycle planning dispatched an action")
    require(backup_health.get("fallback_count") == 0, "backup fallback occurred")
    require(lifecycle_health.get("fallback_count") == 0, "lifecycle fallback occurred")
    require(backup_health.get("selected_compatibility_entry_id") == EXPECTED_ENTRY_ID, "backup selected the wrong release")
    require(lifecycle_health.get("selected_compatibility_entry_id") == EXPECTED_ENTRY_ID, "lifecycle selected the wrong release")
    require(
        lifecycle_health.get(
            "lifecycle_addon_response_contract_model"
        )
        == EXPECTED_LIFECYCLE_ADDON_RESPONSE_MODEL,
        "lifecycle selected the wrong add-on response model",
    )
    require(
        lifecycle_health.get(
            "lifecycle_addon_response_envelope_variant"
        )
        == EXPECTED_LIFECYCLE_ADDON_RESPONSE_ENVELOPE,
        "lifecycle selected the wrong add-on response envelope",
    )
    require(
        lifecycle_health.get("lifecycle_addon_response_diagnostics")
        is None,
        "lifecycle retained response-contract failure diagnostics",
    )
    require(
        lifecycle_transport.detail_text_item_count == 1,
        "immutable add-on detail did not emit one text item",
    )
    require(
        lifecycle_transport.detail_text_bytes is not None
        and lifecycle_transport.detail_text_bytes
        >= EXPECTED_SOURCE_DERIVED_MINIMUM_DETAIL_BYTES,
        "immutable add-on detail text did not cross the Beta 14 bound",
    )
    require(
        lifecycle_transport.detail_structured_content_present,
        "immutable add-on detail omitted direct structured content",
    )
    return {
        "persisted_plan_count": len(persisted),
        "proposal_operations": sorted(
            plan.operation.value for plan in persisted
        ),
        "authoritative_addon_binding": {
            "slug": binding.get("slug"),
            "endpoint_host": binding.get("endpoint_host"),
            "installed_version": binding.get("installed_version"),
        },
        "backup_dispatch_count": backup_dispatch_count,
        "lifecycle_dispatch_counts": lifecycle_dispatch_counts,
        "backup_fallback_count": backup_health.get("fallback_count"),
        "lifecycle_fallback_count": lifecycle_health.get("fallback_count"),
        "lifecycle_addon_response_contract_model": (
            lifecycle_health.get(
                "lifecycle_addon_response_contract_model"
            )
        ),
        "lifecycle_addon_response_envelope_variant": (
            lifecycle_health.get(
                "lifecycle_addon_response_envelope_variant"
            )
        ),
        "immutable_addon_detail_text_bytes": (
            lifecycle_transport.detail_text_bytes
        ),
        "immutable_addon_detail_text_item_count": (
            lifecycle_transport.detail_text_item_count
        ),
        "immutable_addon_detail_structured_content_present": (
            lifecycle_transport.detail_structured_content_present
        ),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    before = fixture_stats(args.fixture_stats_url)
    transport = McpReadGatewayTransport(
        args.upstream_endpoint,
        timeout_seconds=30.0,
        client_version=SERVER_VERSION,
    )
    catalog = await transport.discover()
    require(catalog.server_name == "ha-mcp", "upstream server name changed")
    require(catalog.server_version == EXPECTED_UPSTREAM_VERSION, "upstream version changed")
    require(catalog.protocol_version == EXPECTED_PROTOCOL, "upstream protocol changed")
    require(len(catalog.tools) == EXPECTED_TOOL_COUNT, "upstream tool count changed")
    raw_fingerprint = catalog_fingerprint(list(catalog.tools))
    require(raw_fingerprint == EXPECTED_RAW_CATALOG_FINGERPRINT, "immutable add-on catalog fingerprint changed")

    release = load_reviewed_upstream_release_registry().by_version[
        EXPECTED_UPSTREAM_VERSION
    ]
    validation = validate_reviewed_release_catalog(
        release,
        observed_server_name=catalog.server_name,
        observed_upstream_version=catalog.server_version,
        observed_protocol_version=catalog.protocol_version,
        tools=catalog.tools,
    )
    require(validation.valid, "shared exact-release catalog validation failed")
    require(validation.reviewed_accounted_count == EXPECTED_TOOL_COUNT, "shared catalog accounting changed")
    require(validation.aggregate_fingerprint_model == REVIEWED_NORMALIZED_CATALOG_FINGERPRINT_MODEL_V1, "normalized catalog model changed")
    require(validation.normalized_catalog_fingerprint == validation.expected_normalized_catalog_fingerprint, "normalized catalog identity changed")
    require(
        validation.normalized_catalog_fingerprint
        == EXPECTED_NORMALIZED_CATALOG_FINGERPRINT,
        "exact normalized catalog fingerprint changed",
    )

    with tempfile.TemporaryDirectory(
        prefix="exact-addon-runtime-acceptance-"
    ) as directory:
        root = Path(directory)
        settings = _settings(args, root / "settings-audit.jsonl")
        automatic = await _automatic_read_acceptance(
            settings,
            McpReadGatewayTransport(
                args.upstream_endpoint,
                timeout_seconds=30.0,
                client_version=SERVER_VERSION,
            ),
        )
        dashboard = await _dashboard_acceptance(
            settings,
            args.upstream_endpoint,
        )
        planning = await _planning_acceptance(
            settings,
            args.upstream_endpoint,
            root,
            raw_fingerprint,
        )

    after = fixture_stats(args.fixture_stats_url)
    require(
        after.get("addon_detail_profile") == EXPECTED_ADDON_DETAIL_PROFILE,
        "fixture did not use the live-equivalent add-on detail profile",
    )
    require(
        after.get("addon_detail_payload_bytes")
        == EXPECTED_SOURCE_DERIVED_MINIMUM_DETAIL_BYTES,
        "live-equivalent add-on detail byte cardinality changed",
    )
    require(not after.get("http_mutations"), "an HTTP mutation reached the synthetic fixture")
    require(not after.get("websocket_mutations"), "an unreviewed WebSocket mutation reached the synthetic fixture")
    require(not after.get("operational_backup_creates"), "backup planning created a backup")
    require(before.get("operational_backup_creates") == after.get("operational_backup_creates"), "backup mutation count changed")
    return {
        "result": "PASS",
        "artifact": {
            "image_index_digest": args.expected_image_index_digest,
            "image_manifest_digest": args.expected_image_manifest_digest,
        },
        "initialize": {
            "server_name": catalog.server_name,
            "server_version": catalog.server_version,
            "protocol_version": catalog.protocol_version,
        },
        "catalog": {
            "observed_tool_count": len(catalog.tools),
            "observed_raw_catalog_fingerprint": raw_fingerprint,
            "reviewed_raw_catalog_fingerprint": (
                validation.reviewed_standalone_raw_catalog_fingerprint
            ),
            "aggregate_fingerprint_model": (
                validation.aggregate_fingerprint_model
            ),
            "normalized_catalog_fingerprint": (
                validation.normalized_catalog_fingerprint
            ),
            "reviewed_accounted_count": (
                validation.reviewed_accounted_count
            ),
            "validation_status": validation.validation_status,
        },
        "automatic_reads": automatic,
        "dashboard": dashboard,
        "planning": planning,
        "fixture": {
            "http_mutation_count": sum(
                (after.get("http_mutations") or {}).values()
            ),
            "websocket_mutation_count": sum(
                (after.get("websocket_mutations") or {}).values()
            ),
            "backup_create_count": len(
                after.get("operational_backup_creates") or []
            ),
            "addon_detail_profile": after.get("addon_detail_profile"),
            "addon_detail_payload_bytes": after.get(
                "addon_detail_payload_bytes"
            ),
        },
        "fallback_count": 0,
    }


def main() -> None:
    for logger_name in ("mcp.client.streamable_http", "httpx", "httpcore"):
        logger = logging.getLogger(logger_name)
        logger.disabled = True
        logger.propagate = False
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream-endpoint", required=True)
    parser.add_argument("--configured-upstream-endpoint", required=True)
    parser.add_argument("--fixture-stats-url", required=True)
    parser.add_argument("--ha-url", required=True)
    parser.add_argument("--ha-token", required=True)
    parser.add_argument("--expected-image-index-digest", required=True)
    parser.add_argument("--expected-image-manifest-digest", required=True)
    parser.add_argument(
        "--expected-upstream-version",
        choices=tuple(sorted(EXACT_ADDON_PROFILES)),
        default="8.0.0",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    _select_exact_addon_profile(args.expected_upstream_version)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = asyncio.run(
            asyncio.wait_for(
                run(args), timeout=ACCEPTANCE_TIMEOUT_SECONDS
            )
        )
    except Exception as exc:
        failure = {
            "result": "FAIL",
            "failure_type": type(exc).__name__[:128],
        }
        args.output.write_text(
            json.dumps(failure, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        raise SystemExit(
            "exact add-on runtime acceptance failed; see bounded evidence"
        ) from None
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "exact add-on runtime acceptance: PASS "
        f"({result['catalog']['observed_tool_count']} advertised, "
        f"{result['automatic_reads']['dynamic_tool_count']} delegated, "
        f"{result['planning']['persisted_plan_count']} proposals, "
        "zero dispatch)"
    )


if __name__ == "__main__":
    main()
