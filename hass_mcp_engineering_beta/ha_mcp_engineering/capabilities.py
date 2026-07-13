"""Build and capability metadata for the HA MCP Engineering Server."""

from __future__ import annotations

from typing import Any

from .version import (
    BUILD_SHA,
    BUILD_TIME,
    SCHEMA_VERSION,
    SERVER_ID,
    SERVER_NAME,
    SERVER_VERSION,
)

# Public MCP capability catalog. Status values are intentionally stable so an
# MCP client can reason about the role of this server without scraping prose.
CAPABILITIES: tuple[dict[str, Any], ...] = (
    {"tool": "server_info", "category": "foundation", "status": "native", "risk": "read"},
    {"tool": "list_capabilities", "category": "foundation", "status": "native", "risk": "read"},
    {"tool": "render_template", "category": "evidence", "status": "native", "risk": "read"},
    {"tool": "list_automation_traces", "category": "evidence", "status": "native", "risk": "read"},
    {"tool": "get_automation_trace", "category": "evidence", "status": "native", "risk": "read"},
    {"tool": "get_blueprint", "category": "evidence", "status": "native", "risk": "read"},
    {"tool": "check_config", "category": "verification", "status": "native", "risk": "read"},
    {"tool": "get_audit_log", "category": "observability", "status": "native", "risk": "read"},
    {"tool": "get_history", "category": "evidence", "status": "transitional", "risk": "read"},
    {"tool": "get_logbook", "category": "evidence", "status": "transitional", "risk": "read"},
    {"tool": "get_error_log", "category": "evidence", "status": "transitional", "risk": "read"},
    {"tool": "list_automations", "category": "discovery", "status": "transitional", "risk": "read"},
    {"tool": "get_automation_config", "category": "discovery", "status": "transitional", "risk": "read"},
    {"tool": "list_devices", "category": "discovery", "status": "transitional", "risk": "read"},
    {"tool": "list_entity_registry", "category": "discovery", "status": "transitional", "risk": "read"},
    {"tool": "search_entities", "category": "discovery", "status": "transitional", "risk": "read"},
    {"tool": "upsert_automation", "category": "configuration", "status": "transitional", "risk": "behavioral_write"},
    {"tool": "get_entity", "category": "discovery", "status": "transitional", "routing": "transitional_direct", "provider": "direct_ha_api", "risk": "read"},
    {"tool": "search_services", "category": "discovery", "status": "transitional", "routing": "transitional_direct", "provider": "direct_ha_api", "risk": "read"},
    {"tool": "list_services", "category": "discovery", "status": "transitional", "routing": "transitional_direct", "provider": "direct_ha_api", "risk": "read"},
    {"tool": "list_areas", "category": "discovery", "status": "transitional", "routing": "transitional_direct", "provider": "direct_ha_api", "risk": "read"},
    {"tool": "list_blueprints", "category": "discovery", "status": "transitional", "risk": "read"},
    {"tool": "call_service", "category": "execution", "status": "deprecated", "delegate": "ha-mcp", "risk": "physical_action"},
    {"tool": "delete_automation", "category": "configuration", "status": "deprecated", "delegate": "ha-mcp", "risk": "destructive"},
    {"tool": "reload_domain", "category": "execution", "status": "deprecated", "delegate": "ha-mcp", "risk": "infrastructure"},
)

PLANNED_CAPABILITIES: tuple[dict[str, str], ...] = (
    {"capability": "incident_correlation", "status": "planned", "risk": "analytical"},
    {"capability": "handoff_generation", "status": "planned", "risk": "analytical"},
)

BETA_NATIVE_CAPABILITIES: tuple[dict[str, Any], ...] = (
    {
        "tool": "get_server_health",
        "category": "observability",
        "status": "beta_native",
        "risk": "read",
        "additive": True,
    },
    {"tool": "create_change_plan", "category": "governance", "status": "beta_native", "risk": "behavioral_write", "additive": True},
    {"tool": "get_change_plan", "category": "governance", "status": "beta_native", "risk": "read", "additive": True},
    {"tool": "list_change_plans", "category": "governance", "status": "beta_native", "risk": "read", "additive": True},
    {"tool": "approve_change_plan", "category": "governance", "status": "beta_native", "risk": "behavioral_write", "additive": True},
    {"tool": "apply_change_plan", "category": "governance", "status": "beta_native", "risk": "behavioral_write", "additive": True},
    {"tool": "rollback_change", "category": "governance", "status": "beta_native", "risk": "behavioral_write", "additive": True},
    {"tool": "entity_dependency_analysis", "category": "analysis", "status": "beta_native", "risk": "read", "additive": True},
    {
        "tool": "automation_reliability_analysis",
        "category": "analysis",
        "status": "beta_native",
        "risk": "read",
        "additive": True,
        "routing": "engineering_native",
        "provider": "engineering",
    },
    {
        "tool": "change_impact_analysis",
        "category": "analysis",
        "status": "beta_native",
        "risk": "read",
        "additive": True,
        "routing": "engineering_native",
        "provider": "engineering",
        "policy": "single_entity_change_impact_read",
    },
    {
        "tool": "configuration_integrity_analysis",
        "category": "analysis",
        "status": "beta_native",
        "risk": "read",
        "additive": True,
        "routing": "engineering_native",
        "provider": "engineering",
        "policy": "global_configuration_integrity_read",
    },
)

CAPABILITY_PROVIDER_MATRIX: tuple[dict[str, Any], ...] = (
    {
        "tool": "configuration_integrity_analysis",
        "capability": "configuration_integrity_analysis",
        "required_semantics": "Bounded evidence-backed global detection of missing references, disabled or registry-only targets, orphan registry candidates, and unresolved dynamic references.",
        "standard_ha_mcp_coverage": "unavailable",
        "direct_ha_coverage": "bounded_read_sources",
        "selected_provider": "engineering",
        "completeness": "complete_or_explicitly_incomplete",
        "fallback_policy": "none",
        "security_justification": "Read-only Engineering orchestration over one bounded state inventory, entity-registry inventory, and the shared dependency index.",
    },
    {
        "tool": "change_impact_analysis",
        "capability": "impact_analysis",
        "required_semantics": "Bounded evidence-backed effects of renaming, removing, or disabling one entity.",
        "standard_ha_mcp_coverage": "unavailable",
        "direct_ha_coverage": "bounded_read_sources",
        "selected_provider": "engineering",
        "completeness": "complete_or_explicitly_incomplete",
        "fallback_policy": "none",
        "security_justification": "Read-only Engineering orchestration over the shared dependency index and bounded administrative evidence.",
    },
    {
        "tool": "get_entity",
        "capability": "current_entity_state",
        "required_semantics": "Exact current state and attributes selected by entity_id.",
        "standard_ha_mcp_coverage": "unavailable",
        "direct_ha_coverage": "complete",
        "selected_provider": "direct_ha_api",
        "completeness": "complete",
        "fallback_policy": "none",
        "security_justification": "Read-only exact entity-state endpoint.",
    },
    {
        "tool": "list_areas",
        "capability": "area_lookup",
        "required_semantics": "Complete Home Assistant area registry enumeration.",
        "standard_ha_mcp_coverage": "unavailable",
        "direct_ha_coverage": "complete",
        "selected_provider": "direct_ha_api",
        "completeness": "complete",
        "fallback_policy": "none",
        "security_justification": "Read-only area-registry WebSocket command.",
    },
    {
        "tool": "search_services",
        "capability": "service_discovery",
        "required_semantics": "Bounded search over the Home Assistant service catalog.",
        "standard_ha_mcp_coverage": "unavailable",
        "direct_ha_coverage": "complete",
        "selected_provider": "direct_ha_api",
        "completeness": "complete",
        "fallback_policy": "none",
        "security_justification": "Read-only service-catalog endpoint with an enforced result bound.",
    },
    {
        "tool": "list_services",
        "capability": "service_discovery",
        "required_semantics": "Bounded service catalog enumeration including field schemas.",
        "standard_ha_mcp_coverage": "unavailable",
        "direct_ha_coverage": "complete_with_bound",
        "selected_provider": "direct_ha_api",
        "completeness": "complete_or_explicitly_truncated",
        "fallback_policy": "none",
        "security_justification": "Read-only service-catalog endpoint with a fixed response bound.",
    },
)


def capability_for_tool(tool_name: str | None) -> dict[str, Any]:
    for item in (*CAPABILITIES, *BETA_NATIVE_CAPABILITIES):
        if item["tool"] == tool_name:
            return dict(item)
    return {}


def build_server_metadata(*, ha_url: str, runtime_mode: str, ha_connection: dict[str, Any]) -> dict[str, Any]:
    """Return stable server identity and runtime metadata."""
    return {
        "server": {
            "id": SERVER_ID,
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
            "schema_version": SCHEMA_VERSION,
            "build_sha": BUILD_SHA,
            "build_time": BUILD_TIME,
        },
        "runtime": {
            "mode": runtime_mode,
            "home_assistant_url": ha_url,
            "home_assistant_connection": ha_connection,
        },
        "tool_count": len(CAPABILITIES) + len(BETA_NATIVE_CAPABILITIES),
        "canonical_tool_count": len(CAPABILITIES),
    }


def build_capability_catalog(*, status: str = "", category: str = "") -> dict[str, Any]:
    """Return the public capability catalog with optional exact filters."""
    normalized_status = status.strip().lower()
    normalized_category = category.strip().lower()
    tools = [
        dict(item)
        for item in CAPABILITIES
        if (not normalized_status or item["status"] == normalized_status)
        and (not normalized_category or item["category"] == normalized_category)
    ]
    return {
        "count": len(tools),
        "filters": {
            "status": normalized_status or None,
            "category": normalized_category or None,
        },
        "tools": tools,
        "planned": [dict(item) for item in PLANNED_CAPABILITIES],
        "beta_native": [dict(item) for item in BETA_NATIVE_CAPABILITIES],
        "provider_matrix": [dict(item) for item in CAPABILITY_PROVIDER_MATRIX],
        "registered_count": len(CAPABILITIES) + len(BETA_NATIVE_CAPABILITIES),
        "status_values": ["native", "transitional", "delegated", "deprecated", "beta_native", "planned", "unavailable"],
    }
