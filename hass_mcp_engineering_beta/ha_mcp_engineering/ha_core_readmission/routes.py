"""Closed Core capability requirements for existing Engineering routes."""

from __future__ import annotations

from typing import Any


DEVICE_DEPENDENT_DELEGATED_TOOLS = frozenset(
    {
        "ha_get_device",
        "ha_get_entity",
        "ha_get_entity_exposure",
        "ha_get_overview",
        "ha_search",
    }
)

CORE_2026_9_RELEASES = frozenset({"2026.9.0", "2026.9.1"})
CORE_2026_9_DEVICE_ADAPTER_RELEASES = frozenset({"8.4.3"})

_F3_DASHBOARD_OPERATIONS = frozenset(
    {("update_existing_dashboard", "update_dashboard")}
)
_F3_TYPED_HELPER_OPERATIONS = frozenset(
    {("set_exact_input_boolean_state", "set_input_boolean_state")}
)
_F3_GOVERNED_CONFIGURATION_OPERATIONS = frozenset(
    {
        ("create_automation_configuration", "create_automation_configuration"),
        ("update_automation_configuration", "update_automation_configuration"),
        ("create_script_configuration", "create_script_configuration"),
        ("update_script_configuration", "update_script_configuration"),
        (
            "create_input_boolean_configuration",
            "create_input_boolean_configuration",
        ),
        (
            "update_input_boolean_configuration",
            "update_input_boolean_configuration",
        ),
        (
            "create_input_number_configuration",
            "create_input_number_configuration",
        ),
        (
            "update_input_number_configuration",
            "update_input_number_configuration",
        ),
        ("create_full_home_assistant_backup", "create_full_backup"),
        (
            "reload_home_assistant_configuration_domain",
            "controlled_reload",
        ),
        ("restart_installed_home_assistant_addon", "restart_addon"),
        ("restart_home_assistant_core", "restart_home_assistant"),
    }
)

DELEGATED_CORE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "ha_config_get_automation": ("core.automation_configuration_read",),
    "ha_config_get_calendar_events": ("core.basic_websocket_read",),
    "ha_config_get_category": ("core.non_device_registry_read",),
    "ha_config_get_label": ("core.non_device_registry_read",),
    "ha_config_get_scene": ("core.basic_websocket_read",),
    "ha_config_get_script": ("core.basic_websocket_read",),
    "ha_config_list_dashboard_resources": ("core.dashboard_configuration_read",),
    "ha_config_list_groups": ("core.basic_websocket_read",),
    "ha_config_list_helpers": ("core.basic_websocket_read",),
    "ha_eval_template": ("core.template_semantics",),
    "ha_get_automation_traces": ("core.automation_configuration_read",),
    "ha_get_blueprint": ("core.basic_websocket_read",),
    "ha_get_device": ("core.delegated_device_effective_area",),
    "ha_get_entity": ("core.delegated_device_effective_area",),
    "ha_get_entity_exposure": ("core.delegated_device_effective_area",),
    "ha_get_hacs_info": ("core.basic_rest_read",),
    "ha_get_history": ("core.basic_rest_read",),
    "ha_get_operation_status": ("core.basic_websocket_read",),
    "ha_get_overview": ("core.delegated_device_effective_area",),
    "ha_get_skill_guide": ("core.basic_rest_read",),
    "ha_get_state": ("core.direct_entity_state_read",),
    "ha_get_todo": ("core.basic_websocket_read",),
    "ha_get_zone": ("core.basic_rest_read",),
    "ha_list_floors_areas": ("core.non_device_registry_read",),
    "ha_list_services": ("core.state_service_discovery",),
    "ha_search": ("core.delegated_device_effective_area",),
}

STATIC_TOOL_CORE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "render_template": ("core.template_semantics",),
    "list_automation_traces": ("core.automation_configuration_read",),
    "get_automation_trace": ("core.automation_configuration_read",),
    "check_config": ("core.configuration_validation",),
    "get_history": ("core.basic_rest_read",),
    "get_logbook": ("core.basic_rest_read",),
    "get_error_log": ("core.basic_websocket_read",),
    "list_automations": ("core.automation_configuration_read",),
    "get_automation_config": ("core.automation_configuration_read",),
    "list_devices": ("core.direct_device_registry_read",),
    "list_entity_registry": ("core.non_device_registry_read",),
    "search_entities": ("core.direct_entity_state_read",),
    "get_entity": ("core.direct_entity_state_read",),
    "search_services": ("core.state_service_discovery",),
    "list_services": ("core.state_service_discovery",),
    "list_areas": ("core.non_device_registry_read",),
    "list_blueprints": ("core.basic_websocket_read",),
    "list_dashboards": ("core.dashboard_configuration_read",),
    "get_dashboard_config": ("core.dashboard_configuration_read",),
    "create_helper_state_plan": ("core.dependency_helper_planning",),
    "create_change_plan": ("core.dependency_helper_planning",),
    "create_configuration_plan": ("core.dependency_helper_planning",),
    "create_dashboard_update_plan": ("core.dashboard_configuration_read",),
    "entity_dependency_analysis": ("core.dependency_helper_planning",),
    "automation_reliability_analysis": ("core.dependency_helper_planning",),
    "change_impact_analysis": ("core.dependency_helper_planning",),
    "configuration_integrity_analysis": ("core.dependency_helper_planning",),
    "incident_correlation": ("core.dependency_helper_planning",),
    "handoff_generation": ("core.dependency_helper_planning",),
}


def delegated_requirements(tool_name: str) -> tuple[str, ...]:
    """Return the complete compiled Core requirement set for one ha-mcp read."""

    return DELEGATED_CORE_REQUIREMENTS.get(tool_name, ())


def delegated_provider_compatibility(
    *,
    tool_name: str,
    core_version: str | None,
    adapter_version: str | None,
) -> tuple[bool, str | None]:
    """Bind Core 2026.9 device routes to a reviewed corrected ha-mcp adapter.

    The Core observation proves only Core's child-device/effective-area
    contract.  The independent upstream admission generation proves the
    selected binary-owned adapter.  Neither surface may manufacture the
    other's authority.
    """

    if (
        tool_name not in DEVICE_DEPENDENT_DELEGATED_TOOLS
        or core_version not in CORE_2026_9_RELEASES
    ):
        return True, None
    if adapter_version in CORE_2026_9_DEVICE_ADAPTER_RELEASES:
        return True, None
    return False, "ha_mcp_child_device_contract_unproven"


def static_tool_requirements(
    tool_name: str,
    arguments: Any = None,
) -> tuple[str, ...]:
    """Return the exact Core requirements for one static tool invocation."""

    if tool_name == "server_info":
        if isinstance(arguments, dict) and arguments.get("check_ha") is False:
            return ()
        return ("core.basic_rest_read",)
    if tool_name == "get_server_health":
        if isinstance(arguments, dict) and arguments.get("check_ha") is False:
            return ()
        return ("core.basic_rest_read", "core.basic_websocket_read")

    return STATIC_TOOL_CORE_REQUIREMENTS.get(tool_name, ())


def f3_requirements(prepared: Any) -> tuple[str, ...]:
    """Map an existing typed F3 operation to its complete Core authority set."""

    raw_capability_ids = {
        value
        for value in (
            getattr(prepared, "capability_id", ""),
            getattr(prepared, "capability_identity", ""),
        )
        if isinstance(value, str) and value
    }
    capability_id = (
        next(iter(raw_capability_ids))
        if len(raw_capability_ids) == 1
        else ""
    )
    operation = str(getattr(prepared, "operation", ""))
    values = {"core.f3_mutation_verification"}
    identity = (capability_id, operation)
    if identity in _F3_DASHBOARD_OPERATIONS:
        values.add("core.dashboard_configuration_read")
    elif identity in _F3_TYPED_HELPER_OPERATIONS:
        values.add("core.typed_helper_operation")
    elif identity in _F3_GOVERNED_CONFIGURATION_OPERATIONS:
        values.add("core.governed_configuration_operation")
    else:
        # There is deliberately no profile for this requirement. Unknown or
        # conflicting identities therefore cannot acquire a Core route lease.
        values.add("core.unsupported_f3_operation")
    return tuple(sorted(values))


__all__ = [
    "CORE_2026_9_DEVICE_ADAPTER_RELEASES",
    "CORE_2026_9_RELEASES",
    "DELEGATED_CORE_REQUIREMENTS",
    "DEVICE_DEPENDENT_DELEGATED_TOOLS",
    "STATIC_TOOL_CORE_REQUIREMENTS",
    "delegated_requirements",
    "delegated_provider_compatibility",
    "f3_requirements",
    "static_tool_requirements",
]
