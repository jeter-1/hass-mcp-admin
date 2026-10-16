"""Closed Core capability requirements for existing Engineering routes."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from ..entity_ids import validate_entity_id
from .probe_profiles import (
    CoreProbeProfile, compiled_probe_profile, CHILD_DEVICE_PROBE_PROFILE,
    CHILD_DEVICE_NAME_PARTS_PROBE_PROFILE,
)


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

_F3_ENTITY_METADATA_OPERATIONS = frozenset(
    {
        (
            "update_entity_registry_metadata_configuration",
            "update_entity_registry_metadata_configuration",
        )
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
    "ha_get_automation_traces": ("core.automation_trace_read",),
    "ha_get_blueprint": ("core.basic_websocket_read",),
    "ha_manage_blueprints": ("core.basic_websocket_read",),
    "ha_get_device": ("core.delegated_device_effective_area",),
    "ha_get_entity": ("core.delegated_device_effective_area",),
    "ha_get_entity_exposure": ("core.delegated_device_effective_area",),
    "ha_get_hacs_info": ("core.basic_rest_read",),
    "ha_get_history": ("core.basic_rest_read",),
    "ha_get_operation_status": ("core.basic_websocket_read",),
    "ha_get_overview": ("core.delegated_device_effective_area",),
    "ha_get_skill_guide": ("core.basic_rest_read",),
    # HAMCP-129 (O1): the basic profiles attest neither these exact commands
    # nor Supervisor; Engineering's allowlist projection fails closed on drift.
    "ha_get_system_health": ("core.basic_websocket_read", "core.basic_rest_read"),
    "ha_get_app": ("core.basic_websocket_read",),
    "ha_get_integration": ("core.basic_websocket_read", "core.basic_rest_read"),
    "ha_get_state": ("core.direct_entity_state_read",),
    "ha_get_todo": ("core.basic_websocket_read",),
    "ha_get_zone": ("core.basic_rest_read",),
    "ha_list_floors_areas": ("core.non_device_registry_read",),
    "ha_list_services": ("core.state_service_discovery",),
    "ha_search": ("core.delegated_device_effective_area",),
}

STATIC_TOOL_CORE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "dashboard_integrity_analysis": (
        "core.dashboard_configuration_read", "core.basic_rest_read", "core.non_device_registry_read",
    ),
    "capture_automation_baseline": (
        "core.automation_baseline_metadata_read", "core.basic_rest_read", "core.basic_websocket_read",
        "core.non_device_registry_read", "core.direct_device_registry_read", "core.automation_configuration_read",
    ),
    "get_integration_inspection": (
        "core.basic_websocket_read", "core.integration_inspection_metadata_read",
    ),
    "render_template": ("core.template_semantics",),
    "list_automation_traces": ("core.automation_trace_read",),
    "get_automation_trace": ("core.automation_trace_read",),
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
    "create_entity_metadata_plan": (
        "core.dependency_helper_planning",
        "core.entity_registry_metadata_operation",
    ),
    "create_dashboard_update_plan": ("core.dashboard_configuration_read",),
    "create_backup_plan": ("core.basic_websocket_read",),
    "create_reload_plan": (
        "core.configuration_validation",
        "core.state_service_discovery",
    ),
    "create_addon_restart_plan": ("core.basic_websocket_read",),
    "create_home_assistant_restart_plan": (
        "core.basic_rest_read",
        "core.configuration_validation",
    ),
    "entity_dependency_analysis": ("core.dependency_helper_planning",),
    "automation_reliability_analysis": (
        "core.automation_trace_read",
        "core.dependency_helper_planning",
    ),
    "change_impact_analysis": (
        "core.automation_trace_read",
        "core.dependency_helper_planning",
    ),
    "configuration_integrity_analysis": ("core.dependency_helper_planning",),
    "incident_correlation": ("core.dependency_helper_planning",),
    "handoff_generation": ("core.dependency_helper_planning",),
}

# Tools whose requirements depend only on whether the caller asked for a Home
# Assistant check.  ``check_ha=False`` reports local Engineering state and
# makes no Core call; every other invocation is mapped.
CONDITIONAL_CORE_STATIC_TOOLS = frozenset({"server_info", "get_server_health"})

CORE_INDEPENDENT_LOCAL_STATE = "engineering_local_state"
CORE_INDEPENDENT_SUPERVISOR_ONLY = "supervisor_api_only"
CORE_INDEPENDENT_MOUNTED_SOURCE = "mounted_read_only_source"
CORE_INDEPENDENT_DOWNSTREAM_AUTHORITY = "downstream_core_authority"
CORE_INDEPENDENCE_BASES = frozenset(
    {
        CORE_INDEPENDENT_LOCAL_STATE,
        CORE_INDEPENDENT_SUPERVISOR_ONLY,
        CORE_INDEPENDENT_MOUNTED_SOURCE,
        CORE_INDEPENDENT_DOWNSTREAM_AUTHORITY,
    }
)


@dataclass(frozen=True)
class CoreIndependence:
    """The reviewed reason a static tool acquires no gateway Core lease."""

    basis: str
    justification: str


# The closed, reviewed set of static tools that bypass gateway Core
# acquisition.  Lookup is exact-name only: no prefix, wildcard, or pattern
# matching.  A static tool absent from both this allowlist and the requirement
# map is refused by the gateway; Core independence is never implied by a
# missing entry.  Adding an entry requires a code change and a pinned-table
# test update.
CORE_INDEPENDENT_STATIC_TOOLS: Mapping[str, CoreIndependence] = MappingProxyType(
    {
        "get_core_log_history": CoreIndependence(
            CORE_INDEPENDENT_SUPERVISOR_ONLY,
            "Reads the Core journal through the fixed Supervisor API provider "
            "and must remain available while Core is down; it consumes no "
            "Core API authority.",
        ),
        "get_blueprint": CoreIndependence(
            CORE_INDEPENDENT_MOUNTED_SOURCE,
            "Reads bounded blueprint YAML from the read-only mounted "
            "homeassistant_config directory; it makes no Core API call.",
        ),
        "list_capabilities": CoreIndependence(
            CORE_INDEPENDENT_LOCAL_STATE,
            "Returns the in-process Engineering capability catalog; it makes "
            "no Home Assistant call.",
        ),
        "get_audit_log": CoreIndependence(
            CORE_INDEPENDENT_LOCAL_STATE,
            "Reads the local Engineering audit log; it makes no Home "
            "Assistant call.",
        ),
        "get_change_plan": CoreIndependence(
            CORE_INDEPENDENT_LOCAL_STATE,
            "Reads one persisted Engineering change plan; it makes no Home "
            "Assistant call.",
        ),
        "list_change_plans": CoreIndependence(
            CORE_INDEPENDENT_LOCAL_STATE,
            "Lists persisted Engineering change plans; it makes no Home "
            "Assistant call.",
        ),
        "get_execution_task": CoreIndependence(
            CORE_INDEPENDENT_LOCAL_STATE,
            "Reads one persisted Engineering execution task; a typed fan or "
            "power task reconciles through its own typed Core authority, and "
            "no other path makes a Home Assistant call.",
        ),
        "list_execution_tasks": CoreIndependence(
            CORE_INDEPENDENT_LOCAL_STATE,
            "Lists persisted Engineering execution tasks; it makes no Home "
            "Assistant call.",
        ),
        "cancel_execution_task": CoreIndependence(
            CORE_INDEPENDENT_LOCAL_STATE,
            "Cancels a persisted Engineering execution task and must remain "
            "available while Core is unavailable; it dispatches no Home "
            "Assistant change.",
        ),
        "approve_change_plan": CoreIndependence(
            CORE_INDEPENDENT_LOCAL_STATE,
            "Records the external approval request for a persisted plan hash; "
            "the request itself makes no Home Assistant call. The queued "
            "approval notification is delivered later by its own worker, "
            "outside this request and outside the gateway Core lease.",
        ),
        "apply_change_plan": CoreIndependence(
            CORE_INDEPENDENT_DOWNSTREAM_AUTHORITY,
            "Each governed step acquires its exact F3 Core authority "
            "(f3_requirements) immediately before dispatch; a gateway lease "
            "would not bind the per-step target.",
        ),
        "rollback_change": CoreIndependence(
            CORE_INDEPENDENT_DOWNSTREAM_AUTHORITY,
            "Rollback executes through the governed F3 path, which acquires "
            "its exact Core authority per step before dispatch.",
        ),
        "reverify_configuration_task": CoreIndependence(
            CORE_INDEPENDENT_DOWNSTREAM_AUTHORITY,
            "Read-back verification acquires its exact F3 Core authority "
            "before each observation and holds the result when unavailable.",
        ),
        "control_fan": CoreIndependence(
            CORE_INDEPENDENT_DOWNSTREAM_AUTHORITY,
            "The typed fan provider is configured with the Core runtime and "
            "acquires its own exact-target authority before dispatch.",
        ),
        "control_power": CoreIndependence(
            CORE_INDEPENDENT_DOWNSTREAM_AUTHORITY,
            "The typed power provider is configured with the Core runtime and "
            "acquires its own exact-target authority before dispatch.",
        ),
        "run_held_read_canary": CoreIndependence(
            CORE_INDEPENDENT_DOWNSTREAM_AUTHORITY,
            "The upstream read gateway acquires the held route's delegated "
            "Core requirements before dispatch and refuses when unavailable.",
        ),
    }
)

CORE_ROUTE_MAPPED = "mapped"
CORE_ROUTE_INDEPENDENT = "core_independent"
# A published delegated read: the upstream read gateway acquires its exact
# DELEGATED_CORE_REQUIREMENTS before dispatch and withholds it when Core
# authority is unavailable.
CORE_ROUTE_DELEGATED = "delegated"
CORE_ROUTE_UNMAPPED = "unmapped"
CORE_REQUIREMENT_UNMAPPED = "core_requirement_unmapped"


def _validate_static_core_routes() -> None:
    """Refuse to import an ambiguous or open Core route table."""

    empty = sorted(
        name for name, values in STATIC_TOOL_CORE_REQUIREMENTS.items()
        if not values
    )
    if empty:
        raise RuntimeError(f"Static Core requirements must be non-empty: {empty}")
    overlap = sorted(
        set(CORE_INDEPENDENT_STATIC_TOOLS)
        & (set(STATIC_TOOL_CORE_REQUIREMENTS) | CONDITIONAL_CORE_STATIC_TOOLS)
    )
    if overlap:
        raise RuntimeError(f"Core-independent tools are also mapped: {overlap}")
    for name, independence in CORE_INDEPENDENT_STATIC_TOOLS.items():
        if (
            not name
            or any(character in name for character in "*?[]")
            or independence.basis not in CORE_INDEPENDENCE_BASES
            or not independence.justification
        ):
            raise RuntimeError(f"Invalid Core-independent entry: {name!r}")


_validate_static_core_routes()


def delegated_requirements(tool_name: str) -> tuple[str, ...]:
    """Return the complete compiled Core requirement set for one ha-mcp read."""

    return DELEGATED_CORE_REQUIREMENTS.get(tool_name, ())


def delegated_provider_compatibility(
    *,
    tool_name: str,
    core_version: str | None,
    adapter_version: str | None,
    probe_profile: CoreProbeProfile | None = None,
) -> tuple[bool, str | None]:
    """Bind exact reviewed Core device profiles to corrected ha-mcp adapters.

    The Core observation proves only Core's child-device/effective-area
    contract.  The independent upstream admission generation proves the
    selected binary-owned adapter.  Neither surface may manufacture the
    other's authority.
    """

    probe_profile = probe_profile or compiled_probe_profile(core_version or "")
    if (
        tool_name not in DEVICE_DEPENDENT_DELEGATED_TOOLS
        or probe_profile is None
        or not probe_profile.strict_device_registry
    ):
        return True, None
    # The exact 8.5.0 and 8.6.0 adapters preserve child-device/effective-area semantics.
    # Keep the signed Core profile and its fingerprint byte-for-byte unchanged;
    # this exact provider binding is binary-owned, not new Core authority.
    if adapter_version in {"8.5.0", "8.6.0"} and probe_profile == CHILD_DEVICE_PROBE_PROFILE:
        return True, None
    if (
        adapter_version == "8.6.0"
        and core_version == "2026.10.0"
        and probe_profile == CHILD_DEVICE_NAME_PARTS_PROBE_PROFILE
    ):
        return True, None
    if adapter_version in probe_profile.delegated_device_adapters:
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

    requirements = STATIC_TOOL_CORE_REQUIREMENTS.get(tool_name, ())
    if (
        tool_name == "incident_correlation"
        and isinstance(arguments, dict)
        and isinstance(arguments.get("automation_id"), str)
        and bool(arguments["automation_id"])
    ):
        requirements = (*requirements, "core.automation_trace_read")
    elif (
        tool_name == "handoff_generation"
        and isinstance(arguments, dict)
        and isinstance(arguments.get("automation_ids"), list)
        and bool(arguments["automation_ids"])
        and arguments.get("include_incident_context") is not False
    ):
        requirements = (*requirements, "core.automation_trace_read")

    return tuple(sorted(set(requirements)))


@dataclass(frozen=True)
class StaticCoreRoute:
    """The gateway's Core admission decision for one static tool call."""

    classification: str
    requirements: tuple[str, ...] = ()


def static_tool_core_route(
    tool_name: str,
    arguments: Any = None,
) -> StaticCoreRoute:
    """Classify one static tool call as mapped, Core-independent, or unmapped.

    An unmapped tool is never treated as Core-independent; the gateway refuses
    it before any provider call.
    """

    if tool_name in CORE_INDEPENDENT_STATIC_TOOLS:
        return StaticCoreRoute(CORE_ROUTE_INDEPENDENT)
    requirements = static_tool_requirements(tool_name, arguments)
    if requirements:
        return StaticCoreRoute(CORE_ROUTE_MAPPED, requirements)
    if tool_name in CONDITIONAL_CORE_STATIC_TOOLS:
        return StaticCoreRoute(CORE_ROUTE_INDEPENDENT)
    return StaticCoreRoute(CORE_ROUTE_UNMAPPED)


STATIC_TARGET_INVALID_ENTITY_ID = "entity_metadata_invalid_entity_id"


class StaticToolTargetRefused(ValueError):
    """A targeted static route's arguments name no valid exact target."""

    def __init__(self, reason_code: str, field: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.field = field


def static_tool_target(
    tool_name: str,
    arguments: Any = None,
) -> dict[str, str] | None:
    """Return the exact target a mutation-capable static route must bind.

    A mutation-capable Core profile issues a lease only for one exact target.
    The entity metadata planner binds the same target identity its F3 apply
    identity binds; every other static tool has no target.  An entity_id that
    fails the planner's own validator raises ``StaticToolTargetRefused``, so
    it never reaches Core acquisition or the MCP application.
    """

    if tool_name != "create_entity_metadata_plan":
        return None
    entity_id = arguments.get("entity_id") if isinstance(arguments, dict) else None
    try:
        validate_entity_id(entity_id)
    except ValueError:
        raise StaticToolTargetRefused(
            STATIC_TARGET_INVALID_ENTITY_ID, "entity_id"
        ) from None
    return {"target_type": "entity_registry_metadata", "target_id": entity_id}


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
    elif identity in _F3_ENTITY_METADATA_OPERATIONS:
        # Never the signed governed_configuration_operation contract: that
        # evidence did not cover the entity registry write adapter.
        values.add("core.entity_registry_metadata_operation")
    else:
        # There is deliberately no profile for this requirement. Unknown or
        # conflicting identities therefore cannot acquire a Core route lease.
        values.add("core.unsupported_f3_operation")
    return tuple(sorted(values))


__all__ = [
    "CONDITIONAL_CORE_STATIC_TOOLS",
    "CORE_2026_9_DEVICE_ADAPTER_RELEASES",
    "CORE_2026_9_RELEASES",
    "CORE_INDEPENDENCE_BASES",
    "CORE_INDEPENDENT_STATIC_TOOLS",
    "CORE_REQUIREMENT_UNMAPPED",
    "CORE_ROUTE_DELEGATED",
    "CORE_ROUTE_INDEPENDENT",
    "CORE_ROUTE_MAPPED",
    "CORE_ROUTE_UNMAPPED",
    "CoreIndependence",
    "DELEGATED_CORE_REQUIREMENTS",
    "DEVICE_DEPENDENT_DELEGATED_TOOLS",
    "STATIC_TOOL_CORE_REQUIREMENTS",
    "delegated_requirements",
    "delegated_provider_compatibility",
    "f3_requirements",
    "STATIC_TARGET_INVALID_ENTITY_ID",
    "StaticCoreRoute",
    "StaticToolTargetRefused",
    "static_tool_core_route",
    "static_tool_requirements",
    "static_tool_target",
]
