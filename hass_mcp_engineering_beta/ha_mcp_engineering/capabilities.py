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
    {"tool": "get_entity", "category": "discovery", "status": "delegated", "delegate": "ha-mcp", "risk": "read"},
    {"tool": "search_services", "category": "discovery", "status": "delegated", "delegate": "ha-mcp", "risk": "read"},
    {"tool": "list_services", "category": "discovery", "status": "delegated", "delegate": "ha-mcp", "risk": "read"},
    {"tool": "list_areas", "category": "discovery", "status": "delegated", "delegate": "ha-mcp", "risk": "read"},
    {"tool": "list_blueprints", "category": "discovery", "status": "transitional", "risk": "read"},
    {"tool": "call_service", "category": "execution", "status": "deprecated", "delegate": "ha-mcp", "risk": "physical_action"},
    {"tool": "delete_automation", "category": "configuration", "status": "deprecated", "delegate": "ha-mcp", "risk": "destructive"},
    {"tool": "reload_domain", "category": "execution", "status": "deprecated", "delegate": "ha-mcp", "risk": "infrastructure"},
)

PLANNED_CAPABILITIES: tuple[dict[str, str], ...] = (
    {"capability": "entity_dependency_analysis", "status": "planned", "risk": "analytical"},
    {"capability": "automation_reliability_analysis", "status": "planned", "risk": "analytical"},
    {"capability": "change_impact_analysis", "status": "planned", "risk": "analytical"},
    {"capability": "incident_correlation", "status": "planned", "risk": "analytical"},
    {"capability": "change_plan_governance", "status": "planned", "risk": "behavioral_write"},
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
        "tool_count": len(CAPABILITIES),
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
        "registered_count": len(CAPABILITIES) + len(BETA_NATIVE_CAPABILITIES),
        "status_values": ["native", "transitional", "delegated", "deprecated", "planned", "unavailable"],
    }
