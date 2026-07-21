"""Build and capability metadata for the HA MCP Engineering Server."""

from __future__ import annotations

from typing import Any

from .version import (
    BUILD_DIRTY,
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
    {"tool": "search_entities", "category": "discovery", "status": "transitional", "routing": "transitional_direct", "provider": "direct_ha_api", "risk": "read"},
    {
        "tool": "upsert_automation",
        "category": "configuration",
        "status": "transitional",
        "risk": "behavioral_write",
        "operation_class": "governed_redirect",
        "enforcement": "governed_redirect",
        "replacement": "create_change_plan",
        "routing": "prohibited",
        "provider": None,
        "fallback": "none",
        "direct_write_allowed": False,
    },
    {"tool": "get_entity", "category": "discovery", "status": "transitional", "routing": "transitional_direct", "provider": "direct_ha_api", "risk": "read"},
    {"tool": "search_services", "category": "discovery", "status": "transitional", "routing": "transitional_direct", "provider": "direct_ha_api", "risk": "read"},
    {"tool": "list_services", "category": "discovery", "status": "transitional", "routing": "transitional_direct", "provider": "direct_ha_api", "risk": "read"},
    {"tool": "list_areas", "category": "discovery", "status": "transitional", "routing": "transitional_direct", "provider": "direct_ha_api", "risk": "read"},
    {"tool": "list_blueprints", "category": "discovery", "status": "transitional", "risk": "read"},
    {
        "tool": "call_service", "category": "execution", "status": "deprecated",
        "risk": "physical_action", "operation_class": "provider_unavailable",
        "enforcement": "provider_unavailable", "routing": "standard_mcp_preferred",
        "provider": "standard_ha_mcp", "fallback": "none", "direct_write_allowed": False,
    },
    {
        "tool": "delete_automation", "category": "configuration", "status": "deprecated",
        "risk": "destructive", "operation_class": "prohibited", "enforcement": "prohibited",
        "routing": "prohibited", "provider": None, "fallback": "none", "direct_write_allowed": False,
    },
    {
        "tool": "reload_domain", "category": "execution", "status": "deprecated",
        "risk": "infrastructure", "operation_class": "provider_unavailable",
        "enforcement": "provider_unavailable", "routing": "standard_mcp_preferred",
        "provider": "standard_ha_mcp", "fallback": "none", "direct_write_allowed": False,
    },
)

PLANNED_CAPABILITIES: tuple[dict[str, str], ...] = ()

BETA_NATIVE_CAPABILITIES: tuple[dict[str, Any], ...] = (
    {
        "tool": "get_server_health",
        "category": "observability",
        "status": "beta_native",
        "risk": "read",
        "additive": True,
    },
    {
        "tool": "list_dashboards",
        "category": "discovery",
        "status": "beta_native",
        "risk": "read",
        "additive": True,
        "routing": "upstream_dashboard",
        "provider": "upstream_dashboard",
        "policy": "dashboard_inventory_read",
        "fallback": "none",
        "trust_mode": "reviewed_argument_constrained",
        "trust_profile": "ha_mcp_dashboard_read_v2",
    },
    {
        "tool": "get_dashboard_config",
        "category": "evidence",
        "status": "beta_native",
        "risk": "read",
        "additive": True,
        "routing": "upstream_dashboard",
        "provider": "upstream_dashboard",
        "policy": "exact_dashboard_configuration_read",
        "fallback": "none",
        "trust_mode": "reviewed_argument_constrained",
        "trust_profile": "ha_mcp_dashboard_read_v2",
    },
    {"tool": "create_change_plan", "category": "governance", "status": "beta_native", "risk": "behavioral_write", "additive": True, "operation_class": "proposal"},
    {"tool": "get_change_plan", "category": "governance", "status": "beta_native", "risk": "read", "additive": True},
    {"tool": "list_change_plans", "category": "governance", "status": "beta_native", "risk": "read", "additive": True},
    {"tool": "approve_change_plan", "category": "governance", "status": "beta_native", "risk": "behavioral_write", "additive": True, "operation_class": "external_approval_request"},
    {"tool": "apply_change_plan", "category": "governance", "status": "beta_native", "risk": "behavioral_write", "additive": True, "operation_class": "governed_apply"},
    {"tool": "rollback_change", "category": "governance", "status": "beta_native", "risk": "behavioral_write", "additive": True, "operation_class": "governed_rollback"},
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
    {
        "tool": "incident_correlation",
        "category": "analysis",
        "status": "beta_native",
        "risk": "read",
        "additive": True,
        "routing": "engineering_native",
        "provider": "engineering",
        "policy": "bounded_incident_correlation_read",
        "fallback": "none",
    },
    {
        "tool": "handoff_generation",
        "category": "analysis",
        "status": "beta_native",
        "risk": "read",
        "additive": True,
        "routing": "engineering_native",
        "provider": "engineering",
        "policy": "bounded_handoff_generation_read",
        "fallback": "none",
    },
)

CAPABILITY_PROVIDER_MATRIX: tuple[dict[str, Any], ...] = (
    {
        "tool": "list_dashboards",
        "capability": "dashboard_inventory",
        "required_semantics": "Bounded storage-mode dashboard metadata from ha_config_get_dashboard(list_only=true).",
        "standard_ha_mcp_coverage": "unavailable",
        "direct_ha_coverage": "not_used",
        "selected_provider": "upstream_dashboard",
        "completeness": "complete_or_explicitly_truncated",
        "fallback_policy": "none",
        "security_justification": "An exact reviewed release attestation selects the compiled argument-constrained dashboard-read contract family; Engineering can construct only list_only=true with include_screenshot=false, and the mixed upstream tool is not treated as globally read-only.",
        "trust_mode": "reviewed_argument_constrained",
        "trust_profile": "ha_mcp_dashboard_read_v2",
    },
    {
        "tool": "get_dashboard_config",
        "capability": "dashboard_configuration_evidence",
        "required_semantics": "Exact dashboard configuration read by canonical URL path with a verified upstream-compatible optimistic-lock hash, a distinct full Engineering evidence hash, and bounded response handling.",
        "standard_ha_mcp_coverage": "unavailable",
        "direct_ha_coverage": "not_used",
        "selected_provider": "upstream_dashboard",
        "completeness": "complete_or_explicitly_unavailable",
        "fallback_policy": "none",
        "security_justification": "An exact reviewed release attestation selects the compiled argument-constrained dashboard-read contract family; Engineering can construct only an exact URL-path read with include_screenshot=false, while rendering, preferences, mutation, and service calls remain unreachable.",
        "trust_mode": "reviewed_argument_constrained",
        "trust_profile": "ha_mcp_dashboard_read_v2",
    },
    {
        "tool": "handoff_generation",
        "capability": "handoff_generation",
        "required_semantics": "Bounded evidence-backed operational documentation that distinguishes facts, inferences, recommendations, limitations, completed work, and authorization boundaries.",
        "standard_ha_mcp_coverage": "unavailable",
        "direct_ha_coverage": "bounded_read_sources",
        "selected_provider": "engineering",
        "completeness": "complete_or_explicitly_incomplete",
        "fallback_policy": "none",
        "security_justification": "Read-only Engineering orchestration over bounded runtime, governance, dependency, integrity, reliability, and incident evidence; generated documentation is never authorization.",
    },
    {
        "tool": "incident_correlation",
        "capability": "incident_correlation",
        "required_semantics": "Bounded evidence-backed correlation around one entity, one automation, or both with ranked hypotheses and preserved contradictory evidence.",
        "standard_ha_mcp_coverage": "unavailable",
        "direct_ha_coverage": "bounded_read_sources",
        "selected_provider": "engineering",
        "completeness": "complete_or_explicitly_incomplete",
        "fallback_policy": "none",
        "security_justification": "Read-only Engineering orchestration over bounded administrative evidence; no service call, remediation, or write provider is permitted.",
    },
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
        "tool": "search_entities",
        "capability": "broad_entity_search",
        "required_semantics": "Bounded state-machine search by entity_id or friendly_name with an optional exact domain filter.",
        "standard_ha_mcp_coverage": "unavailable",
        "direct_ha_coverage": "complete within the requested bound or explicitly truncated",
        "selected_provider": "direct_ha_api",
        "completeness": "complete_or_explicitly_truncated",
        "fallback_policy": "none",
        "security_justification": "One read-only state inventory with bounded output.",
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

# Process-local entries are populated only after the exact upstream 7.14.1
# catalog has matched the committed read policy.  The existing Engineering
# catalog remains immutable and available when upstream discovery is absent.
_DYNAMIC_UPSTREAM_CAPABILITIES: tuple[dict[str, Any], ...] = ()
_UPSTREAM_READ_GATEWAY_SUMMARY: dict[str, Any] = {
    "configured": False,
    "initialized": False,
    "generic_delegation_available": False,
    "dynamically_exposed_count": 0,
}


def replace_dynamic_upstream_capabilities(
    values: tuple[dict[str, Any], ...], gateway_summary: dict[str, Any]
) -> None:
    """Publish one validated startup snapshot for metadata and audit lookups."""

    global _DYNAMIC_UPSTREAM_CAPABILITIES, _UPSTREAM_READ_GATEWAY_SUMMARY
    _DYNAMIC_UPSTREAM_CAPABILITIES = tuple(dict(item) for item in values)
    _UPSTREAM_READ_GATEWAY_SUMMARY = dict(gateway_summary)


def dynamic_upstream_capabilities() -> tuple[dict[str, Any], ...]:
    return tuple(dict(item) for item in _DYNAMIC_UPSTREAM_CAPABILITIES)


def capability_for_tool(tool_name: str | None) -> dict[str, Any]:
    for item in (
        *CAPABILITIES,
        *BETA_NATIVE_CAPABILITIES,
        *_DYNAMIC_UPSTREAM_CAPABILITIES,
    ):
        if item["tool"] == tool_name:
            value = dict(item)
            value.setdefault("operation_class", "read" if item.get("risk") == "read" else "prohibited")
            return value
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
            "build_dirty": BUILD_DIRTY,
        },
        "runtime": {
            "mode": runtime_mode,
            "home_assistant_url": ha_url,
            "home_assistant_connection": ha_connection,
        },
        "tool_count": (
            len(CAPABILITIES)
            + len(BETA_NATIVE_CAPABILITIES)
            + len(_DYNAMIC_UPSTREAM_CAPABILITIES)
        ),
        "canonical_tool_count": len(CAPABILITIES),
        "engineering_registered_tool_count": (
            len(CAPABILITIES) + len(BETA_NATIVE_CAPABILITIES)
        ),
        "dynamic_upstream_tool_count": len(_DYNAMIC_UPSTREAM_CAPABILITIES),
        "upstream_read_gateway": dict(_UPSTREAM_READ_GATEWAY_SUMMARY),
    }


def build_capability_catalog(*, status: str = "", category: str = "") -> dict[str, Any]:
    """Return the public capability catalog with optional exact filters."""
    normalized_status = status.strip().lower()
    normalized_category = category.strip().lower()
    tools = [
        {**dict(item), "operation_class": item.get("operation_class", "read" if item.get("risk") == "read" else "prohibited")}
        for item in CAPABILITIES
        if (not normalized_status or item["status"] == normalized_status)
        and (not normalized_category or item["category"] == normalized_category)
    ]
    dynamic_tools = [
        dict(item)
        for item in _DYNAMIC_UPSTREAM_CAPABILITIES
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
        "beta_native": [
            {**dict(item), "operation_class": item.get("operation_class", "read" if item.get("risk") == "read" else "prohibited")}
            for item in BETA_NATIVE_CAPABILITIES
        ],
        "dynamic_upstream": dynamic_tools,
        "upstream_read_gateway": dict(_UPSTREAM_READ_GATEWAY_SUMMARY),
        "provider_matrix": [dict(item) for item in CAPABILITY_PROVIDER_MATRIX],
        "registered_count": (
            len(CAPABILITIES)
            + len(BETA_NATIVE_CAPABILITIES)
            + len(_DYNAMIC_UPSTREAM_CAPABILITIES)
        ),
        "engineering_registered_count": (
            len(CAPABILITIES) + len(BETA_NATIVE_CAPABILITIES)
        ),
        "dynamic_upstream_count": len(_DYNAMIC_UPSTREAM_CAPABILITIES),
        "status_values": ["native", "transitional", "delegated", "deprecated", "beta_native", "planned", "unavailable"],
    }
