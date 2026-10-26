"""Build and capability metadata for the HA MCP Engineering Server."""

from __future__ import annotations

from .power.contracts import POWER_CONTRACTS, POWER_SEMANTIC_CONTRACTS

from typing import Any

from .fan.contracts import CORE_VERSION, FAN_CONTRACTS, FAN_RELEASES, FAN_SEMANTIC_CONTRACTS

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

# HAMCP-181 PR2 review 1 (M2): a governed tool's static entry states only its
# policy requirement.  Its current admission comes from the governance
# service's admission snapshot (the one health projects); without one the
# catalog says it was not evaluated, never a static "pending" as current.
# The static entries spell the value as a literal: repository tooling reads
# this catalog with ``ast.literal_eval``.
CORE_ADMISSION_NOT_EVALUATED = "not_evaluated"

BETA_NATIVE_CAPABILITIES: tuple[dict[str, Any], ...] = (
    {
        "tool": "dashboard_integrity_analysis", "category": "analysis",
        "status": "beta_native", "risk": "read", "additive": True,
        "operation_class": "read", "routing": "engineering_native", "provider": "engineering",
        "data_providers": ["upstream_dashboard", "direct_ha_api"], "fallback": "none",
        "policy": "dashboard-integrity-v1", "direct_write_allowed": False,
    },
    {
        "tool": "reverify_configuration_task", "category": "verification",
        "status": "beta_native", "risk": "engineering_evidence_write", "additive": True,
        "operation_class": "supplementary_configuration_verification",
        "routing": "engineering_native", "provider": "engineering", "fallback": "none",
        "home_assistant_provider": "direct_ha_api", "home_assistant_access": "read_and_validate",
        "policy": "exact_retained_configuration_readback_v1",
        "external_approval_required": False, "direct_write_allowed": False,
        "dispatch_allowed": False, "durable_evidence_write": True, "non_atomic": True,
    },
    {
        "tool": "capture_automation_baseline", "category": "evidence",
        "status": "beta_native", "risk": "read", "additive": True,
        "operation_class": "read", "routing": "engineering_native",
        "provider": "engineering", "data_providers": ["direct_ha_api"],
        "fallback": "none", "policy": "loaded_automation_entities-v1", "direct_write_allowed": False,
    },
    {
        "tool": "get_integration_inspection", "category": "evidence",
        "status": "beta_native", "risk": "read", "additive": True,
        "operation_class": "read", "routing": "engineering_native",
        "provider": "engineering", "data_providers": ["direct_ha_api"],
        "fallback": "none", "policy": "alarmo_allowlist_v1", "direct_write_allowed": False,
    },
    {
        "tool": "get_backup_inventory", "category": "evidence",
        "status": "beta_native", "risk": "read", "additive": True,
        "operation_class": "read", "routing": "engineering_native",
        "provider": "engineering", "data_providers": ["direct_ha_api"],
        "fallback": "none", "policy": "backup_inventory_read_v1", "direct_write_allowed": False,
    },
    {
        "tool": "get_recorder_settings", "category": "evidence",
        "status": "beta_native", "risk": "read", "additive": True,
        "operation_class": "read", "routing": "engineering_native",
        "provider": "engineering", "data_providers": ["direct_ha_api", "upstream_read_gateway"],
        "fallback": "none", "policy": "recorder_settings_read_v1", "direct_write_allowed": False,
    },
    {
        "tool": "get_unregistered_entities", "category": "evidence",
        "status": "beta_native", "risk": "read", "additive": True,
        "operation_class": "read", "routing": "engineering_native",
        "provider": "engineering", "data_providers": ["direct_ha_api"],
        "fallback": "none", "policy": "unregistered_entities_read_v1", "direct_write_allowed": False,
    },
    {
        "tool": "get_configuration_definitions", "category": "evidence",
        "status": "beta_native", "risk": "read", "additive": True,
        "operation_class": "read", "routing": "engineering_native",
        "provider": "engineering", "data_providers": ["direct_ha_api", "upstream_read_gateway"],
        "fallback": "none", "policy": "configuration_definitions_read_v1", "direct_write_allowed": False,
    },
    {
        "tool": "get_core_log_history", "category": "evidence",
        "status": "beta_native", "risk": "read", "additive": True,
        "operation_class": "read", "routing": "engineering_native",
        "provider": "supervisor_core_logs", "fallback": "none",
        "policy": "bounded_core_journal_read_v1", "direct_write_allowed": False,
        "source": "core_journal", "transport": "supervisor_api",
        "retention_coverage": "unknown",
    },
    {
        "tool": "get_incident_logs", "category": "evidence",
        "status": "beta_native", "risk": "read", "additive": True,
        "operation_class": "read", "routing": "engineering_native",
        "provider": "supervisor_incident_logs", "fallback": "none",
        "policy": "incident_window_journal_read_v1", "direct_write_allowed": False,
        "source": "supervisor_journal", "transport": "supervisor_api",
        "retention_coverage": "reported_per_source",
    },
    {
        "tool": "get_history_slices", "category": "evidence",
        "status": "beta_native", "risk": "read", "additive": True,
        "operation_class": "read", "routing": "engineering_native",
        "provider": "engineering", "data_providers": ["upstream_read_gateway"],
        "fallback": "none", "policy": "history_slices_read_v1", "direct_write_allowed": False,
    },
    {
        "tool": "control_power", "category": "ordinary_runtime_action",
        "status": "beta_native", "risk": "physical_action", "additive": True,
        "operation_class": "typed_power", "routing": "engineering_native",
        "provider": "upstream_typed_power", "fallback": "none",
        "policy": "authenticated_exact_light_switch_operation",
        "external_approval_required": False, "direct_write_allowed": False,
        "verification": "exact_ha_on_off_state",
    },
    {
        "tool": "control_fan", "category": "ordinary_runtime_action",
        "status": "beta_native", "risk": "physical_action", "additive": True,
        "operation_class": "typed_fan", "routing": "engineering_native",
        "provider": "upstream_typed_fan", "fallback": "none",
        "policy": "authenticated_exact_fan_operation",
        "external_approval_required": False, "direct_write_allowed": False,
        "verification": "exact_ha_state_and_percentage",
    },
    {
        "tool": "run_held_read_canary",
        "category": "verification",
        "status": "beta_native",
        "risk": "read",
        "additive": True,
        "operation_class": "held_read_canary",
        "routing": "engineering_native",
        "provider": "upstream_read_gateway",
        "policy": "exact_reviewed_held_read_evidence_only",
        "fallback": "none",
        "promotion_performed": False,
        "direct_write_allowed": False,
    },
    {
        "tool": "create_backup_plan",
        "category": "operational_administration",
        "status": "beta_native",
        "risk": "infrastructure_write",
        "additive": True,
        "operation_class": "proposal",
        "routing": "engineering_native",
        "provider": "upstream_operational_backup",
        "policy": "governed_full_backup_create_proposal",
        "fallback": "none",
        "external_approval_required": True,
        "rollback_available": False,
        "direct_write_allowed": False,
    },
    {
        "tool": "create_reload_plan",
        "category": "operational_administration",
        "status": "beta_native",
        "risk": "infrastructure_write",
        "additive": True,
        "operation_class": "proposal",
        "routing": "engineering_native",
        "provider": "upstream_operational_lifecycle",
        "policy": "governed_controlled_reload_proposal",
        "fallback": "none",
        "external_approval_required": True,
        "rollback_available": False,
        "direct_write_allowed": False,
        "target_allowlist": [
            "automation",
            "script",
            "input_boolean",
            "input_number",
        ],
    },
    {
        "tool": "create_addon_restart_plan",
        "category": "operational_administration",
        "status": "beta_native",
        "risk": "high_risk_infrastructure_action",
        "additive": True,
        "operation_class": "proposal",
        "routing": "engineering_native",
        "provider": "upstream_operational_lifecycle",
        "policy": "governed_exact_addon_restart_proposal",
        "fallback": "none",
        "external_approval_required": True,
        "rollback_available": False,
        "direct_write_allowed": False,
    },
    {
        "tool": "create_helper_state_plan",
        "category": "governed_runtime_action",
        "status": "beta_native",
        "risk": "low_risk_virtual_state_write",
        "additive": True,
        "operation_class": "proposal",
        "routing": "engineering_native",
        "provider": "direct_home_assistant_state",
        "provider_contract": "direct-ha-exact-input-boolean-v1",
        "policy": "governed_exact_input_boolean_state_proposal",
        "fallback": "none",
        "fallback_occurred": False,
        "external_approval_required": True,
        "readback_required": True,
        "recovery": "exact_readback_only_no_redispatch",
        "rollback_available": False,
        "reverse_plan_available": True,
        "direct_write_allowed": False,
        "apply_tool": "apply_change_plan",
        "target_domain": "input_boolean",
        "desired_states": ["off", "on"],
    },
    {
        "tool": "create_entity_metadata_plan",
        "category": "governed_configuration",
        "status": "beta_native",
        "risk": "entity_registry_metadata_write",
        "additive": True,
        "operation_class": "proposal",
        "routing": "engineering_native",
        "provider": "direct_home_assistant_entity_registry",
        "policy": "governed_entity_registry_metadata_proposal",
        "core_capability": "core.entity_registry_metadata_operation",
        "core_admission_requirement": "signed_core_registry_admission",
        "core_admission": "not_evaluated",
        "fallback": "none",
        "fallback_occurred": False,
        "external_approval_required": True,
        "elevated_acknowledgement_when_classified": True,
        "readback_required": True,
        "recovery": "exact_readback_only_no_redispatch",
        "rollback_available": True,
        "direct_write_allowed": False,
        "apply_tool": "apply_change_plan",
        "fields": [
            "add_labels", "aliases", "area_id", "categories", "hidden",
            "icon", "name", "remove_labels",
        ],
        "label_creation": False,
    },
    {
        "tool": "create_entity_availability_exposure_plan",
        "category": "governed_configuration",
        "status": "beta_native",
        "risk": "entity_registry_availability_and_assistant_exposure_write",
        "additive": True,
        "operation_class": "proposal",
        "routing": "engineering_native",
        "provider": "direct_home_assistant_entity_registry",
        "policy": "governed_entity_availability_exposure_proposal",
        "resource_types": [
            "entity_registry_availability", "entity_assistant_exposure",
        ],
        "core_capabilities": [
            "core.entity_registry_availability_operation",
            "core.entity_assistant_exposure_operation",
        ],
        "core_admission_requirement": "signed_core_registry_admission",
        "core_admission": "not_evaluated",
        "fallback": "none",
        "fallback_occurred": False,
        "external_approval_required": True,
        "elevated_acknowledgement_when_classified": True,
        "readback_required": True,
        "recovery": "exact_readback_only_no_redispatch",
        "rollback_available": True,
        "direct_write_allowed": False,
        "apply_tool": "apply_change_plan",
        "operations": ["disable", "enable", "expose", "unexpose"],
        "writable_assistants": ["cloud.google_assistant", "conversation"],
        "max_entities_per_plan": 8,
        "execution": "ordered_non_atomic_stop_on_first_failure",
    },
    {
        "tool": "create_label_plan",
        "category": "governed_configuration",
        "status": "beta_native",
        "risk": "label_registry_metadata_write",
        "additive": True,
        "operation_class": "proposal",
        "routing": "engineering_native",
        "provider": "direct_home_assistant_label_registry",
        "policy": "governed_label_registry_proposal",
        "resource_types": ["label_registry_entry", "entity_registry_metadata"],
        "core_capabilities": [
            "core.label_registry_operation",
            "core.entity_registry_metadata_operation",
        ],
        "core_admission_requirement": "signed_core_registry_admission",
        "core_admission": "not_evaluated",
        "fallback": "none",
        "fallback_occurred": False,
        "external_approval_required": True,
        "elevated_acknowledgement_when_classified": True,
        "readback_required": True,
        "recovery": "exact_readback_only_no_redispatch",
        "rollback_available": False,
        "direct_write_allowed": False,
        "apply_tool": "apply_change_plan",
        "operations": ["create", "update"],
        "fields": ["color", "description", "icon", "name"],
        "assignment": "entity_registry_metadata_add_labels",
        "label_deletion": False,
        "max_children_per_plan": 8,
        "execution": "ordered_non_atomic_stop_on_first_failure",
    },
    {
        "tool": "create_configuration_delete_plan",
        "category": "governed_configuration",
        "status": "beta_native",
        "risk": "automation_and_script_configuration_delete",
        "additive": True,
        "operation_class": "proposal",
        "routing": "engineering_native",
        "provider": "direct_home_assistant_configuration",
        "policy": "governed_configuration_delete_proposal",
        "resource_types": ["automation", "script"],
        "core_capabilities": ["core.configuration_delete_operation"],
        "core_admission_requirement": "signed_core_registry_admission",
        "core_admission": "not_evaluated",
        "fallback": "none",
        "fallback_occurred": False,
        "external_approval_required": True,
        "elevated_acknowledgement_when_classified": True,
        "dependency_evidence_disclosed": True,
        "readback_required": True,
        "recovery": "exact_readback_only_no_redispatch",
        "rollback_available": True,
        "rollback": "recreate_exact_pre_image",
        "direct_write_allowed": False,
        "apply_tool": "apply_change_plan",
        "max_deletions_per_plan": 8,
        "execution": "ordered_non_atomic_stop_on_first_failure",
    },
    {
        "tool": "create_home_assistant_restart_plan",
        "category": "operational_administration",
        "status": "beta_native",
        "risk": "high_risk_infrastructure_action",
        "additive": True,
        "operation_class": "proposal",
        "routing": "engineering_native",
        "provider": "upstream_operational_lifecycle",
        "policy": "governed_home_assistant_restart_proposal",
        "fallback": "none",
        "external_approval_required": True,
        "rollback_available": False,
        "direct_write_allowed": False,
    },
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
    {
        "tool": "create_configuration_plan",
        "category": "governance",
        "status": "beta_native",
        "risk": "behavioral_write",
        "additive": True,
        "operation_class": "proposal",
        "routing": "engineering_native",
        "provider": "engineering",
        "policy": "bounded_ordered_configuration_plan_proposal",
        "fallback": "none",
        "supported_resources": [
            "automation",
            "script",
            "input_boolean",
            "input_number",
        ],
        "direct_write_allowed": False,
    },
    {
        "tool": "create_dashboard_update_plan",
        "category": "governance",
        "status": "beta_native",
        "risk": "persistent_configuration_write",
        "additive": True,
        "operation_class": "proposal",
        "routing": "engineering_native",
        "provider": "upstream_dashboard",
        "policy": "bounded_existing_dashboard_update_non_atomic_v1",
        "fallback": "none",
        "external_approval_required": True,
        "direct_write_allowed": False,
        "non_atomic": True,
    },
    {"tool": "get_change_plan", "category": "governance", "status": "beta_native", "risk": "read", "additive": True},
    {"tool": "list_change_plans", "category": "governance", "status": "beta_native", "risk": "read", "additive": True},
    {
        "tool": "get_execution_task",
        "category": "execution_task",
        "status": "beta_native",
        "risk": "read",
        "additive": True,
        "operation_class": "durable_task_read",
        "routing": "engineering_native",
        "provider": "engineering",
        "fallback": "none",
    },
    {
        "tool": "list_execution_tasks",
        "category": "execution_task",
        "status": "beta_native",
        "risk": "read",
        "additive": True,
        "operation_class": "durable_task_list",
        "routing": "engineering_native",
        "provider": "engineering",
        "fallback": "none",
    },
    {
        "tool": "cancel_execution_task",
        "category": "execution_task",
        "status": "beta_native",
        "risk": "governance_write",
        "additive": True,
        "operation_class": "pre_dispatch_cancellation",
        "routing": "engineering_native",
        "provider": "engineering",
        "fallback": "none",
        "dispatch_allowed": False,
        "compensation_allowed": False,
    },
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


def _platform_read_profile(kind: str) -> str:
    # Mirrors providers.upstream_platform_reads.ADAPTER_BINDINGS (test-bound);
    # importing it here would be circular through providers/__init__.
    return ", ".join(f"ha-mcp-{v}-platform-{kind}-v1" for v in ("8.4.3", "8.5.0", "8.6.0"))


CAPABILITY_PROVIDER_MATRIX: tuple[dict[str, Any], ...] = (
    {
        "tool": "dashboard_integrity_analysis", "capability": "dashboard_integrity_analysis",
        "required_semantics": "One exact dashboard, bounded literal references, potential configured controls and frozen sanitized pages.",
        "standard_ha_mcp_coverage": "exact_dashboard_read_only", "direct_ha_coverage": "states_and_entity_registry_only",
        "selected_provider": "engineering", "fallback_policy": "none", "completeness": "explicit_per_dimension_gaps",
        "security_justification": "Three existing Core profiles plus admitted upstream identity; no refresh, retry, writes or dashboard fallback.",
        "trust_mode": "reviewed_native_read", "trust_profile": "dashboard-integrity-v1",
    },
    {
        "tool": "reverify_configuration_task", "capability": "configuration_reverification",
        "required_semantics": "Exact retained task/plan binding and supplementary dated verification receipts without redispatch.",
        "standard_ha_mcp_coverage": "unavailable", "direct_ha_coverage": "fixed_configuration_reads_and_validation",
        "selected_provider": "engineering", "fallback_policy": "none",
        "completeness": "all_approved_objects_or_explicit_unresolved_result",
        "security_justification": "No caller configuration or provider; original execution stays immutable; current signed Core read authority and exact locks required.",
        "trust_mode": "reviewed_native_verification", "trust_profile": "exact_retained_configuration_readback_v1",
    },
    {
        "tool": "capture_automation_baseline", "capability": "automation_baseline_capture",
        "required_semantics": "Full returned configuration hashes with canonical IDs and non-atomic loaded-scope coverage.",
        "standard_ha_mcp_coverage": "semantically_unsuitable", "direct_ha_coverage": "reviewed_fixed_reads",
        "selected_provider": "engineering", "fallback_policy": "none",
        "completeness": "loaded_scope_only_with_explicit_omissions",
        "security_justification": "Administrator visibility, verified registry-lineage identity, fixed reads and frozen sanitized exports.",
        "trust_mode": "reviewed_native_read", "trust_profile": "automation-baseline-read-v1",
    },
    {
        "tool": "get_integration_inspection", "capability": "integration_inspection",
        "required_semantics": "Configured Alarmo membership and modes; allowlisted non-atomic evidence, no observed protection claim.",
        "standard_ha_mcp_coverage": "semantically_unsuitable", "direct_ha_coverage": "reviewed_fixed_reads",
        "selected_provider": "engineering", "fallback_policy": "none",
        "completeness": "bounded_allowlisted_configuration_only",
        "security_justification": "Fixed native commands; separate Core applicability and Alarmo profile; no flows, writes or fallback.",
        "trust_mode": "reviewed_native_read", "trust_profile": "alarmo-configuration-read-v1",
    },
    {
        "tool": "get_backup_inventory", "capability": "backup_inventory",
        "required_semantics": "Backups across every Core backup agent, automatic-backup status and stated-threshold health facts; partial when any source, location or field is unreadable.",
        "standard_ha_mcp_coverage": "semantically_unsuitable", "direct_ha_coverage": "reviewed_fixed_reads",
        "selected_provider": "engineering", "fallback_policy": "none",
        "completeness": "partial_with_reasons_when_any_location_or_field_unreadable",
        "security_justification": "Two fixed admin Core WebSocket reads; allowlist projection; encryption key reduced to a flag; no create, restore, delete, download, settings or fallback.",
        "trust_mode": "reviewed_native_read", "trust_profile": "backup-inventory-read-v1",
    },
    {
        "tool": "get_recorder_settings", "capability": "recorder_settings",
        "required_semantics": "Each recorder setting as configured, Core default or unknown with a reason; the filter and its Core case; runtime status; Core's per-entity recording answer beside the computed filter with drift flagged; partial when any source is unreadable.",
        "standard_ha_mcp_coverage": "semantically_unsuitable", "direct_ha_coverage": "reviewed_fixed_reads",
        "selected_provider": "engineering", "fallback_policy": "none",
        "completeness": "partial_with_reasons_when_any_source_unreadable_or_default_unprovable",
        "security_justification": "Fixed reads: the admitted YAML reader at recorder and homeassistant paths, recorder/info and batched admin recorder/entity_options/get; allowlist projection; db_url and credentials never returned; no settings, purge or fallback.",
        "trust_mode": "reviewed_native_read", "trust_profile": "recorder-settings-read-v1",
    },
    {
        "tool": "get_unregistered_entities", "capability": "unregistered_entities",
        "required_semantics": "Entities with a state and no registry entry, the creating platform or a directly written state, and the config entry when exactly one resolves; definition location never asserted; partial when any source is unreadable.",
        "standard_ha_mcp_coverage": "semantically_unsuitable", "direct_ha_coverage": "reviewed_fixed_reads",
        "selected_provider": "engineering", "fallback_policy": "none",
        "completeness": "partial_with_reasons_when_any_source_unreadable_or_limit_reached",
        "security_justification": "Fixed non-admin reads: GET /api/states, config/entity_registry/list, entity/source and batched search/related; no registry change or fallback.",
        "trust_mode": "reviewed_native_read", "trust_profile": "unregistered-entities-read-v1",
    },
    {
        "tool": "get_configuration_definitions", "capability": "configuration_definitions",
        "required_semantics": "Allowlisted YAML definitions of template, SQL and helper entities with the match method and confidence; UI entries reported as not readable without a flow; per-entity unavailability diagnosis; partial when any source is unreadable.",
        "standard_ha_mcp_coverage": "semantically_unsuitable", "direct_ha_coverage": "reviewed_fixed_reads",
        "selected_provider": "engineering", "fallback_policy": "none",
        "completeness": "partial_with_reasons_when_any_source_unreadable_or_definition_unaddressable",
        "security_justification": "Fixed reads: the admitted YAML reader at fixed definition paths, GET /api/states (availability only), GET /api/config (components), config/entity_registry/list, entity/source, config_entries/get for a closed domain set and manifest/get ha_mcp_tools; allowlist projection; db_url, credentials and secret references never returned; no component command, flow, reload or fallback.",
        "trust_mode": "reviewed_native_read", "trust_profile": "configuration-definitions-read-v1",
    },
    {
        "tool": "get_core_log_history", "capability": "core_log_history",
        "required_semantics": "One bounded retained Core journal window with explicit source, sanitization and unknown retention coverage.",
        "standard_ha_mcp_coverage": "semantically_unsuitable",
        "direct_ha_coverage": "not_used",
        "selected_provider": "supervisor_core_logs", "fallback_policy": "none",
        "completeness": "partial_retention_and_window_completeness_unknown",
        "security_justification": "Fixed Supervisor GET /core/logs only; owner-approved homeassistant role is broader at the process boundary, but no caller-selected endpoint or write is exposed.",
        "trust_mode": "reviewed_native_read",
        "trust_profile": "bounded_core_journal_read_v1",
    },
    {
        "tool": "get_incident_logs", "capability": "incident_logs",
        "required_semantics": "One bounded time window merged across readable journal sources with per-source coverage, retention edge, gaps, filter skips, a signed continuity-checked cursor and every unreadable source reported with its exact reason.",
        "standard_ha_mcp_coverage": "semantically_unsuitable",
        "direct_ha_coverage": "not_used",
        "selected_provider": "supervisor_incident_logs", "fallback_policy": "none",
        "completeness": "partial_with_reasons_when_any_source_unavailable_gapped_or_limited",
        "security_justification": "Fixed Supervisor GET /core/logs and /addons/self/logs only, admitted for role homeassistant; supervisor, host, host:<category> and addon:<slug> are read from the host journal the Supervisor mounts read-only with journald: true, through fixed libsystemd symbol and field allowlists, never a Supervisor route that needs role manager; journald off or unreadable is reported unavailable with its reason; a credential-looking journal message is withheld whole; strict sanitizer at one exit.",
        "trust_mode": "reviewed_native_read",
        "trust_profile": "incident_window_journal_read_v1",
    },
    {
        "tool": "get_history_slices", "capability": "history_slices",
        "required_semantics": "One entity's state history over a bounded window in fixed time slices with per-page coverage, a signed cursor that re-checks the last state read and each slice-start state, and stale refusal when earlier history changed.",
        "standard_ha_mcp_coverage": "reviewed_argument_constrained", "direct_ha_coverage": "not_used",
        "selected_provider": "engineering", "fallback_policy": "none",
        "completeness": "partial_with_reasons_when_limited_redacted_or_upstream_failed",
        "security_justification": "Only the admitted ha_get_history route with Engineering-built absolute bounds, ascending minimal order and bounded offset paging; gateway sanitization plus the strict sanitizer at one exit; versions 8.5.0/8.6.0 only; no direct Home Assistant fallback.",
        "trust_mode": "reviewed_native_read", "trust_profile": "history-slices-read-v1",
    },
    {
        "tool": "control_power", "capability": "typed_power_execution",
        "required_semantics": "One exact light/switch ON/OFF action under connector authority, durable ownership and independent HA state verification.",
        "standard_ha_mcp_coverage": "reviewed_argument_constrained", "direct_ha_coverage": "not_used",
        "selected_provider": "upstream_typed_power", "fallback_policy": "none",
        "completeness": "reported_state_only_physical_feedback_not_verified",
        "security_justification": "Separate compiled power contracts fix target, ON/OFF service and empty data. No generic services or uncertain-action replay.",
        "trust_mode": "reviewed_argument_constrained",
        "trust_profile": ", ".join((*POWER_CONTRACTS, *POWER_SEMANTIC_CONTRACTS.values())),
        "core_applicability": "explicit_signed_core.typed_power_operation_or_exact_legacy_core_2026.9.2",
    },
    {
        "tool": "control_fan",
        "capability": "typed_fan_execution",
        "required_semantics": "One exact fan action under authenticated connector authority, durable ownership and independent state/percentage verification.",
        "standard_ha_mcp_coverage": "reviewed_argument_constrained",
        "direct_ha_coverage": "not_used",
        "selected_provider": "upstream_typed_fan",
        "completeness": "reported_state_only_physical_feedback_not_verified",
        "fallback_policy": "none",
        "security_justification": (
            f"Signed Core operation applicability or legacy Core {CORE_VERSION}/ha-mcp "
            f"{', '.join(FAN_RELEASES)} fan contracts fix target, service and arguments; "
            "the execution receipt identifies the selected exact contract. Generic "
            "services remain closed and uncertain dispatch is reconciled without replay."
        ),
        "trust_mode": "reviewed_argument_constrained",
        "trust_profile": ", ".join((*FAN_CONTRACTS, *FAN_SEMANTIC_CONTRACTS.values())),
        "core_applicability": "explicit_signed_core.typed_fan_operation_or_exact_legacy_core_2026.9.2",
    },
    {
        "tool": "ha_get_system_health",
        "capability": "platform_health",
        "required_semantics": "Allowlisted platform baseline plus enum-selected repairs, radio-network, dead-entity and theme sections with fixed error codes.",
        "standard_ha_mcp_coverage": "reviewed_argument_constrained",
        "direct_ha_coverage": "not_used",
        "selected_provider": "upstream_platform_reads",
        "completeness": "projected_allowlist_partial_disclosed",
        "fallback_policy": "none",
        "security_justification": "Engineering builds include only from the section enum; diagnostics, config_check and zha_network_full are never sent. The baseline starts Core cloud/Supervisor reachability probes (open world, disclosed). Positive allowlist before sanitization.",
        "trust_mode": "reviewed_argument_constrained",
        "trust_profile": _platform_read_profile("health"),
        "core_applicability": "basic_websocket_and_rest_profiles_commands_unattested",
    },
    {
        "tool": "ha_get_app",
        "capability": "installed_app_inventory",
        "required_semantics": "Installed add-on listing with Engineering-side slug filtering and optional stats for at most 32 started add-ons.",
        "standard_ha_mcp_coverage": "reviewed_argument_constrained",
        "direct_ha_coverage": "not_used",
        "selected_provider": "upstream_platform_reads",
        "completeness": "projected_allowlist_partial_disclosed",
        "fallback_policy": "none",
        "security_justification": "Engineering sends only source=installed and include_stats; slug is never sent, so add-on info and options are never requested. A stats-free pre-check refuses stats for more than 32 started add-ons; it is a sampled bound, because upstream re-lists before its stats fan-out. Supervisor is not attested by any Core profile.",
        "trust_mode": "reviewed_argument_constrained",
        "trust_profile": _platform_read_profile("apps"),
        "core_applicability": "basic_websocket_profile_command_and_supervisor_unattested",
    },
    {
        "tool": "ha_get_integration",
        "capability": "config_entry_status",
        "required_semantics": "Config-entry list with Engineering-side domain, query, state and entry_id filtering over a 200-entry upstream window.",
        "standard_ha_mcp_coverage": "reviewed_argument_constrained",
        "direct_ha_coverage": "not_used",
        "selected_provider": "upstream_platform_reads",
        "completeness": "projected_allowlist_partial_disclosed",
        "fallback_policy": "none",
        "security_justification": "Engineering forces list mode with include_options=false and exact_match=true; domain, entry_id and detail mode are never sent, so no options flow starts. E-mail-like title text is masked; host names may remain.",
        "trust_mode": "reviewed_argument_constrained",
        "trust_profile": _platform_read_profile("integrations"),
        "core_applicability": "basic_websocket_and_rest_profiles_commands_unattested",
    },
    {
        "tool": "ha_config_get_yaml",
        "capability": "yaml_configuration_read",
        "required_semantics": "One mapping-key path of configuration.yaml on disk, redacted, with !secret/!env_var as typed reference-name markers and a YAML 1.2 parse disclosure.",
        "standard_ha_mcp_coverage": "reviewed_argument_constrained",
        "direct_ha_coverage": "not_used",
        "selected_provider": "ha_mcp_delegated",
        "completeness": "best_effort_redaction_partial_disclosed",
        "fallback_policy": "none",
        "security_justification": "Engineering sends only file=configuration.yaml with include_parsed=true and include_content=false; every other file and every credential-naming path is refused before dispatch. Optional per-version binding (8.4.3, 8.5.0) is never default-admitted and is excluded from signed selections. Engineering makes no write or service call; the one accepted dependency (K1) is that the first call may make the component call POST ha_mcp_tools/get_caller_token and persist the admin caller token.",
        "trust_mode": "reviewed_argument_constrained",
        "trust_profile": "ha-mcp-8.4.3-yaml-fragment-read-v1, ha-mcp-8.5.0-yaml-fragment-read-v1",
        "dependencies": "ha_mcp_tools custom component; ha_mcp_filesystem_tools_flag",
        "core_applicability": "core.basic_rest_read + core.basic_websocket_read, component routes unattested",
        "writes": "none",
    },
    {
        "tool": "list_dashboards",
        "capability": "dashboard_inventory",
        "required_semantics": "Bounded storage-mode dashboard metadata from ha_config_get_dashboard(list_only=true).",
        "standard_ha_mcp_coverage": "unavailable",
        "direct_ha_coverage": "not_used",
        "selected_provider": "upstream_dashboard",
        "completeness": "complete_or_explicitly_truncated",
        "fallback_policy": "none",
        "security_justification": "The exact compiled dashboard-read contract family, with any exact release attestation authoritative when present, permits Engineering to construct only list_only=true with include_screenshot=false; the mixed upstream tool is not treated as globally read-only.",
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
        "security_justification": "The exact compiled dashboard-read contract family, with any exact release attestation authoritative when present, permits Engineering to construct only an exact URL-path read with include_screenshot=false; rendering, preferences, mutation, and service calls remain unreachable.",
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

# Process-local entries contain the independently matched subset from the
# latest successful upstream catalog evaluation. The observed version is
# evidence rather than a global admission gate. The static Engineering catalog
# remains available when discovery is absent or individual reads quarantine.
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
    """Publish one validated dynamic snapshot for metadata and audit lookups."""

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


def _with_current_admission(
    item: dict[str, Any], admission: dict[str, Any] | None
) -> dict[str, Any]:
    if "core_admission_requirement" in item and admission is not None:
        current = admission["tools"].get(item["tool"])
        if current is not None:
            item["core_admission"] = current
    return item


def build_capability_catalog(
    *,
    status: str = "",
    category: str = "",
    core_admission: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the public capability catalog with optional exact filters.

    ``core_admission`` is the governance service's admission snapshot; the
    governed tools' current admission is projected from it.
    """
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
            _with_current_admission(
                {**dict(item), "operation_class": item.get("operation_class", "read" if item.get("risk") == "read" else "prohibited")},
                core_admission,
            )
            for item in BETA_NATIVE_CAPABILITIES
        ],
        "core_admission": (
            {
                "status": core_admission["status"],
                "generation": core_admission["generation"],
            }
            if core_admission is not None
            else {"status": CORE_ADMISSION_NOT_EVALUATED, "generation": None}
        ),
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
