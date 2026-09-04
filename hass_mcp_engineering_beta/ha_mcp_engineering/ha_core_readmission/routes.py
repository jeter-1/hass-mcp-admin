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
    "get_blueprint": ("core.basic_websocket_read",),
    "check_config": ("core.configuration_validation",),
    "get_history": ("core.basic_rest_read",),
    "get_logbook": ("core.basic_rest_read",),
    "get_error_log": ("core.basic_rest_read",),
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


def static_tool_requirements(tool_name: str) -> tuple[str, ...]:
    """Return Core requirements without changing static tool registration."""

    return STATIC_TOOL_CORE_REQUIREMENTS.get(tool_name, ())


def f3_requirements(prepared: Any) -> tuple[str, ...]:
    """Map an existing typed F3 operation to its complete Core authority set."""

    capability_id = str(getattr(prepared, "capability_id", ""))
    operation = str(getattr(prepared, "operation", ""))
    values = {"core.f3_mutation_verification"}
    if capability_id == "update_storage_dashboard" or "dashboard" in operation:
        values.add("core.dashboard_configuration_read")
    elif "input_boolean" in operation or "helper" in capability_id:
        values.add("core.typed_helper_operation")
    else:
        values.add("core.governed_configuration_operation")
    return tuple(sorted(values))


__all__ = [
    "DELEGATED_CORE_REQUIREMENTS",
    "DEVICE_DEPENDENT_DELEGATED_TOOLS",
    "STATIC_TOOL_CORE_REQUIREMENTS",
    "delegated_requirements",
    "f3_requirements",
    "static_tool_requirements",
]
