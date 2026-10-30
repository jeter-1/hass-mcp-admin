"""Binary-owned Home Assistant Core capability profiles and exact authority.

The compatibility table can select only profiles and adapters compiled into the
Engineering image. Observed version strings and response shapes are evidence;
neither can create authority or widen a route.
"""

from __future__ import annotations

from typing import Any

from .models import (
    CORE_IDENTITY,
    CORE_PROTOCOL,
    CoreAuthoritySelection,
    CoreAuthoritySource,
    CoreAuthorityStatus,
    CoreCapabilityClass,
    CoreCapabilityProfile,
    fingerprint,
)


CORE_2026_9_AUTHORITY: dict[str, Any] = {
    "version": "2026.9.0",
    "tag": "refs/tags/2026.9.0",
    "tag_type": "lightweight_signed_commit",
    "source_commit": "dfb5a9e690daaf204b542896e4b595e61a11a401",
    "source_tree": "47d4178cc071773032fd6446c569dc21caf08f1e",
    "source_archive_sha256": (
        "ca6ee91306eb48f9ef237c08863744636a416fa27fb4ae3010223b22f2af9793"
    ),
    "source_commit_signature": "verified",
    "image_index_digest": (
        "sha256:372d991e58882a1d8c68c07e9aa3f3b509276e695355f73ccdb03baa70407293"
    ),
    "architecture_manifests": {
        "linux/amd64": (
            "sha256:bd39459e8d84fbbbd9a40fdccf7ab029f20d92340263b93db00887c3d5dcdaf6"
        ),
        "linux/arm64": (
            "sha256:2a7eb678f984c9983d36435c12e124f406c4eedd70fb5780d8488b1c777f0d95"
        ),
    },
    "image_version": "2026.9.0",
    "provenance": "slsa_v1_attestations_observed_for_both_manifests",
}

CORE_2026_9_1_AUTHORITY: dict[str, Any] = {
    "version": "2026.9.1",
    "tag": "refs/tags/2026.9.1",
    "tag_type": "lightweight_signed_commit",
    "source_commit": "fc034572d0216a04ed40a07154394908a594dfed",
    "source_tree": "4b2a1cd29e3d85c43d791e03eb82a17244956fec",
    "source_archive_sha256": (
        "27f657b4fbf76980f8451007116b37f09cc1226bb52c4ae6100cd46532120ff2"
    ),
    "source_commit_signature": "verified",
    "image_index_digest": (
        "sha256:612d76760b544cb40b7ba01387fdac964c59a6a550a50a4d30b4773c822d2918"
    ),
    "architecture_manifests": {
        "linux/amd64": (
            "sha256:31076d37e3b7dc9681b32d892aa4413fa866c9a90e5c8a50324b397dce415d17"
        ),
        "linux/arm64": (
            "sha256:134bdc1b5f3d32f201987966134fc6edbba0809c6d5100651b28dc653f443d39"
        ),
    },
    "image_version": "2026.9.1",
    "provenance": "slsa_v1_attestations_observed_for_both_manifests",
}

CORE_2026_9_RELEASE_AUTHORITIES: dict[str, dict[str, Any]] = {
    CORE_2026_9_AUTHORITY["version"]: CORE_2026_9_AUTHORITY,
    CORE_2026_9_1_AUTHORITY["version"]: CORE_2026_9_1_AUTHORITY,
}


SUPPORTED_CORE_RELEASES: tuple[tuple[str, str, str], ...] = (
    (
        "2026.7.2",
        "f9122fb28dd30d3833b3b313924befbc82157f97",
        "sha256:1476924357b46e80735c13e94232ba5c853cac052e9df4bb28d50fa56348097b",
    ),
    (
        "2026.8.0",
        "4a9dce13f61d03960ad5d2710e2af9fd2a78af54",
        "sha256:a21689ef0510df9760ee11bab4d6b2fef3ed5c1a29ed9c3224271597a23729eb",
    ),
    (
        "2026.8.1",
        "53998d7710b4ac280658511c24a2a3e2651f9873",
        "sha256:6340a3de3917a9b19368e767310a96dd090f6a19aca8aeadf87fd1145cec9682",
    ),
    (
        CORE_2026_9_AUTHORITY["version"],
        CORE_2026_9_AUTHORITY["source_commit"],
        CORE_2026_9_AUTHORITY["image_index_digest"],
    ),
    (
        CORE_2026_9_1_AUTHORITY["version"],
        CORE_2026_9_1_AUTHORITY["source_commit"],
        CORE_2026_9_1_AUTHORITY["image_index_digest"],
    ),
)


def _contract(
    capability_id: str,
    capability_class: CoreCapabilityClass,
    adapter_id: str,
    required_checks: tuple[str, ...],
    *,
    provider_boundary: str,
    response_contract: dict[str, Any],
    predispatch: tuple[str, ...] = (),
) -> CoreCapabilityProfile:
    profile_id = capability_id.replace(".", "_") + "_v1"
    material = {
        "model": "ha-core-binary-capability-contract-v2",
        "surface": "home_assistant_core",
        "capability_id": capability_id,
        "capability_class": capability_class.value,
        "profile_id": profile_id,
        "profile_version": 1,
        "adapter_id": adapter_id,
        "required_checks": list(required_checks),
        "provider_boundary": provider_boundary,
        "response_contract": response_contract,
        "predispatch": list(predispatch),
        "fallback": "none",
    }
    return CoreCapabilityProfile(
        capability_id=capability_id,
        capability_class=capability_class,
        profile_id=profile_id,
        profile_version=1,
        adapter_id=adapter_id,
        contract_fingerprint=fingerprint(material),
        required_checks=required_checks,
    )


CORE_CAPABILITY_PROFILES: tuple[CoreCapabilityProfile, ...] = (
    _contract(
        "core.basic_rest_read",
        CoreCapabilityClass.BASIC_REST_READ,
        "compiled-core-rest-read-v1",
        ("rest_config_mapping", "rest_states_list", "rest_state_entity_shape"),
        provider_boundary="direct_ha_api",
        response_contract={
            "config": {"version": "nonempty_string"},
            "states": "bounded_list_of_entity_state_mappings",
        },
    ),
    _contract(
        "core.basic_websocket_read",
        CoreCapabilityClass.BASIC_WEBSOCKET_READ,
        "compiled-core-websocket-read-v1",
        (
            "websocket_auth_ok_version",
            "websocket_get_config_mapping",
            "websocket_result_envelope",
        ),
        provider_boundary="direct_ha_api",
        response_contract={
            "auth_ok": {"ha_version": "nonempty_string"},
            "get_config": {"version": "nonempty_string"},
        },
    ),
    _contract(
        "core.state_service_discovery",
        CoreCapabilityClass.STATE_SERVICE_DISCOVERY,
        "compiled-core-state-service-discovery-v1",
        ("rest_states_list", "rest_services_list", "service_domain_mapping"),
        provider_boundary="direct_ha_api",
        response_contract={
            "states": "bounded_list_of_entity_state_mappings",
            "services": "bounded_list_of_domain_service_mappings",
        },
    ),
    _contract(
        "core.non_device_registry_read",
        CoreCapabilityClass.NON_DEVICE_REGISTRY_READ,
        "compiled-core-non-device-registry-read-v1",
        (
            "websocket_area_registry_list",
            "websocket_floor_registry_list",
            "websocket_label_registry_list",
            "websocket_entity_registry_list",
        ),
        provider_boundary="direct_ha_api",
        response_contract={
            "areas": "bounded_list",
            "floors": "bounded_list",
            "labels": "bounded_list",
            "entities": "bounded_list",
        },
    ),
    _contract(
        "core.direct_device_registry_read",
        CoreCapabilityClass.DIRECT_DEVICE_REGISTRY_READ,
        "compiled-core-direct-device-registry-v1",
        (
            "websocket_device_registry_list",
            "child_device_reduced_shape",
            "parent_reference_integrity",
            "effective_area_inheritance",
        ),
        provider_boundary="direct_ha_api",
        response_contract={
            "regular_devices": "bounded_complete_device_mappings",
            "child_devices": "bounded_reduced_child_mappings",
            "effective_area": "child_then_parent_resolution",
        },
    ),
    _contract(
        "core.delegated_device_effective_area",
        CoreCapabilityClass.DELEGATED_DEVICE_EFFECTIVE_AREA,
        "compiled-core-delegated-device-effective-area-v1",
        (
            "websocket_device_registry_list",
            "child_device_reduced_shape",
            "parent_reference_integrity",
            "effective_area_inheritance",
            "core_device_semantics_for_delegation",
        ),
        provider_boundary="reviewed_ha_mcp_read_gateway_adapter",
        response_contract={
            "devices": "bounded_parent_and_effective_area_projection",
            "entities": "bounded_effective_area_projection",
        },
    ),
    _contract(
        "core.direct_entity_state_read",
        CoreCapabilityClass.DIRECT_ENTITY_STATE_READ,
        "compiled-core-direct-entity-state-read-v1",
        (
            "rest_state_entity_shape",
            "rest_state_json_types",
            "rest_state_not_found_contract",
        ),
        provider_boundary="direct_ha_api",
        response_contract={"state": "bounded_exact_entity_state_mapping"},
    ),
    _contract(
        "core.automation_configuration_read",
        CoreCapabilityClass.AUTOMATION_CONFIGURATION_READ,
        "compiled-core-automation-configuration-read-v1",
        (
            "automation_configuration_command_contract",
            "automation_configuration_mapping",
            "automation_configuration_not_found_contract",
        ),
        provider_boundary="direct_ha_api",
        response_contract={"configuration": "bounded_automation_mapping"},
    ),
    _contract(
        "core.automation_trace_read",
        CoreCapabilityClass.AUTOMATION_TRACE_READ,
        "compiled-core-automation-trace-read-v1",
        (
            "automation_trace_list_command_contract",
            "automation_trace_list_bounded_mapping_sequence",
            "automation_trace_get_command_contract",
            "automation_trace_get_not_found_contract",
        ),
        provider_boundary="direct_ha_api",
        response_contract={
            "trace_list": "bounded_list_of_trace_header_mappings",
            "trace_get": {
                "success": "bounded_trace_mapping",
                "missing": {
                    "code": "not_found",
                    "message": "The trace could not be found",
                },
            },
        },
    ),
    _contract(
        "core.dashboard_configuration_read",
        CoreCapabilityClass.DASHBOARD_CONFIGURATION_READ,
        "compiled-core-dashboard-configuration-read-v1",
        (
            "websocket_lovelace_config_get",
            "websocket_lovelace_dashboards_list",
            "dashboard_mapping_bounded",
        ),
        provider_boundary="direct_ha_api",
        response_contract={
            "dashboards": "bounded_list_of_dashboard_metadata",
            "configuration": "bounded_json_mapping",
        },
    ),
    _contract(
        "core.template_semantics",
        CoreCapabilityClass.TEMPLATE_SEMANTICS,
        "compiled-core-template-semantics-v1",
        (
            "template_source_release_supported",
            "template_registry_digest_exact",
            "jinja_runtime_digest_exact",
        ),
        provider_boundary="engineering_dependency_analysis",
        response_contract={
            "analysis": "offline_binary_owned_semantic_registry",
            "render": "existing_bounded_direct_ha_template_provider",
        },
    ),
    _contract(
        "core.configuration_validation",
        CoreCapabilityClass.CONFIGURATION_VALIDATION,
        "compiled-core-configuration-validation-v1",
        (
            "check_config_endpoint_contract",
            "check_config_result_taxonomy",
            "probatio_semantics_exact",
        ),
        provider_boundary="direct_ha_api",
        response_contract={"validation": "bounded_valid_or_errors_mapping"},
    ),
    _contract(
        "core.dependency_helper_planning",
        CoreCapabilityClass.DEPENDENCY_HELPER_PLANNING,
        "compiled-core-dependency-helper-planning-v1",
        (
            "dependency_index_contract_exact",
            "template_semantics_exact",
            "effective_area_semantics_exact",
        ),
        provider_boundary="engineering_dependency_analysis",
        response_contract={"planning": "read_only_bounded_evidence"},
    ),
    _contract(
        "core.typed_helper_operation",
        CoreCapabilityClass.TYPED_HELPER_OPERATION,
        "compiled-core-typed-helper-operation-v1",
        (
            "typed_helper_target_exact",
            "typed_helper_service_contract_exact",
            "typed_helper_readback_contract_exact",
        ),
        provider_boundary="existing_governed_helper_adapter",
        response_contract={
            "target": "exact_existing_typed_helper",
            "readback": "authoritative_exact_state",
        },
        predispatch=(
            "core_identity",
            "capability_contract",
            "target_fingerprint",
            "session_fingerprint",
            "generation",
        ),
    ),
    _contract(
        "core.f3_mutation_verification",
        CoreCapabilityClass.F3_MUTATION_VERIFICATION,
        "compiled-core-f3-mutation-verification-v1",
        (
            "f3_exact_target_readback",
            "f3_mismatch_taxonomy_exact",
            "f3_no_provider_response_only_success",
        ),
        provider_boundary="f3_operation_adapter_v1",
        response_contract={"verification": "authoritative_exact_readback"},
        predispatch=(
            "core_identity",
            "capability_contract",
            "target_fingerprint",
            "session_fingerprint",
            "generation",
        ),
    ),
    _contract(
        "core.governed_configuration_operation",
        CoreCapabilityClass.GOVERNED_CONFIGURATION_OPERATION,
        "compiled-core-governed-configuration-operation-v1",
        (
            "configuration_target_exact",
            "configuration_provider_contract_exact",
            "configuration_readback_contract_exact",
        ),
        provider_boundary="existing_governed_configuration_adapters",
        response_contract={
            "dispatch": "existing_typed_governed_provider_only",
            "readback": "authoritative_exact_configuration",
        },
        predispatch=(
            "core_identity",
            "capability_contract",
            "target_fingerprint",
            "session_fingerprint",
            "generation",
        ),
    ),
    _contract(
        "core.governance_observability",
        CoreCapabilityClass.GOVERNANCE_OBSERVABILITY,
        "compiled-core-governance-observability-v1",
        (
            "persisted_evidence_no_core_call",
            "bounded_health_projection",
            "bounded_pagination_projection",
        ),
        provider_boundary="engineering_local_persisted_evidence",
        response_contract={"projection": "bounded_local_evidence_only"},
    ),
)


# These references are deliberately separate from the original 17 profiles.
# Existing compiled releases and signed records do not acquire new write
# semantics merely because the executable learned a new capability.
CORE_TYPED_OPERATION_PROFILES = tuple(
    _contract(
        "core.typed_" + family + "_operation",
        CoreCapabilityClass.TYPED_ORDINARY_OPERATION,
        "compiled-core-single-" + family + "-v1",
        ("rest_state_entity_shape", "service_domain_mapping", "websocket_get_config_mapping"),
        provider_boundary="reviewed_ha_mcp_typed_" + family,
        response_contract={
            "domains": domains,
            "actions": actions,
            "target": "one_exact_available_entity",
            "arguments": arguments,
            "readback": readback,
            "provider": "separate_exact_compiled_ha_mcp_contract",
            "dispatch": "at_most_once_wait_false_return_response_false_verbose_false",
            "recovery": "read_only_same_core_version_no_redispatch",
        },
        predispatch=("core_identity", "capability_contract", "target_fingerprint",
                     "session_fingerprint", "generation"),
    )
    for family, domains, actions, arguments, readback in (
        ("fan", ["fan"], ["turn_on", "turn_off", "set_percentage"],
         "strict_optional_percentage_0_100_checked_feature_bits_zero_is_off",
         "exact_state_and_requested_percentage_no_quantization_substitution"),
        ("power", ["light", "switch"], ["turn_on", "turn_off"],
         "empty_service_data_integration_defaults_may_apply", "exact_on_off_state"),
    )
)
from ..integration_inspection.contracts import CORE_METADATA_CONTRACT

CORE_INTEGRATION_INSPECTION_PROFILES = (
    _contract(
        "core.integration_inspection_metadata_read",
        CoreCapabilityClass.INTEGRATION_INSPECTION_METADATA_READ,
        "compiled-core-alarmo-metadata-read-v1",
        ("websocket_auth_ok_version", "websocket_get_config_mapping", "websocket_result_envelope"),
        provider_boundary="direct_ha_api", response_contract=CORE_METADATA_CONTRACT,
        predispatch=("core_identity", "existing_origin_principal", "generation", "current_authority"),
    ),
)
from ..audit_baseline.capture_contracts import CORE_METADATA_CONTRACT as BASELINE_METADATA_CONTRACT

CORE_AUTOMATION_BASELINE_PROFILES = (
    _contract(
        "core.automation_baseline_metadata_read",
        CoreCapabilityClass.AUTOMATION_BASELINE_METADATA_READ,
        "compiled-core-automation-baseline-read-v1",
        ("websocket_auth_ok_version", "websocket_get_config_mapping", "websocket_result_envelope"),
        provider_boundary="direct_ha_api", response_contract=BASELINE_METADATA_CONTRACT,
        predispatch=("core_identity", "existing_origin_principal", "generation", "current_authority"),
    ),
)
# HAMCP-069A-1: a separate mutation-capable contract.  It is never part of
# CORE_CAPABILITY_PROFILES, so no signed probe fingerprint changes, and it is
# dormant until an owner-signed registry reference selects it exactly.  Label
# creation is deliberately absent until its adapter exists (069A-2).
CORE_ENTITY_METADATA_PROFILES = (
    _contract(
        "core.entity_registry_metadata_operation",
        CoreCapabilityClass.ENTITY_REGISTRY_METADATA_OPERATION,
        "compiled-core-entity-registry-metadata-operation-v1",
        (
            "websocket_entity_registry_get",
            "websocket_entity_registry_update_closed_fields",
            "websocket_label_registry_list",
            "websocket_area_registry_list",
            "websocket_category_registry_list_scoped",
            "entity_registry_readback_exact",
        ),
        provider_boundary="direct_ha_api",
        response_contract={
            "target": "one_exact_existing_entity_registry_entry",
            "fields": [
                "aliases", "area_id", "categories", "hidden_by", "icon",
                "labels", "name",
            ],
            "dispatch": "at_most_once_entity_registry_update",
            "readback": "authoritative_exact_entity_registry_entry",
            "recovery": "read_only_no_redispatch",
        },
        predispatch=("core_identity", "capability_contract", "target_fingerprint",
                     "session_fingerprint", "generation"),
    ),
)
# HAMCP-069B: two further runtime-only mutation contracts.  Like 069A they are
# never part of CORE_CAPABILITY_PROFILES and stay dormant until an owner-signed
# registry reference (planned sequence 13) selects each one exactly.  Each
# profile names every read its gateway sends (HAMCP-222 OD-5): the enable
# disclosure counts the config entry's other entities through
# ``config/entity_registry/list``, and the exposure disclosure reads the
# assistant's "expose new entities" setting (never written).
CORE_ENTITY_AVAILABILITY_EXPOSURE_PROFILES = (
    _contract(
        "core.entity_registry_availability_operation",
        CoreCapabilityClass.ENTITY_REGISTRY_AVAILABILITY_OPERATION,
        "compiled-core-entity-registry-availability-operation-v1",
        (
            "websocket_entity_registry_get",
            "websocket_entity_registry_update_disabled_by_user_or_null",
            "websocket_config_entries_get_single",
            "websocket_entity_registry_list",
            "entity_registry_readback_exact",
        ),
        provider_boundary="direct_ha_api",
        response_contract={
            "target": "bounded_exact_existing_entity_registry_entries",
            "fields": ["disabled_by"],
            "values": ["user", None],
            "dispatch": "at_most_once_entity_registry_update_per_entity",
            "readback": "authoritative_exact_entity_registry_entry",
            "recovery": "read_only_no_redispatch",
        },
        predispatch=("core_identity", "capability_contract", "target_fingerprint",
                     "session_fingerprint", "generation"),
    ),
    _contract(
        "core.entity_assistant_exposure_operation",
        CoreCapabilityClass.ENTITY_ASSISTANT_EXPOSURE_OPERATION,
        "compiled-core-entity-assistant-exposure-operation-v1",
        (
            "websocket_entity_registry_get",
            "websocket_expose_entity_single_assistant_single_entity",
            "websocket_expose_new_entities_get",
            "entity_registry_options_readback_exact",
        ),
        provider_boundary="direct_ha_api",
        response_contract={
            "target": "bounded_exact_existing_entity_registry_entries",
            "fields": ["options.<assistant>.should_expose"],
            "assistants": ["cloud.google_assistant", "conversation"],
            "dispatch": "at_most_once_expose_entity_per_entity",
            "readback": "authoritative_exact_entity_registry_options",
            "recovery": "read_only_no_redispatch",
        },
        predispatch=("core_identity", "capability_contract", "target_fingerprint",
                     "session_fingerprint", "generation"),
    ),
)
# HAMCP-165: the label registry create/update contract.  Like 069A/069B it is
# runtime-only and stays dormant until an owner-signed registry reference
# (planned sequence 13) selects it exactly.  ``delete`` is never sent.
CORE_LABEL_REGISTRY_PROFILES = (
    _contract(
        "core.label_registry_operation",
        CoreCapabilityClass.LABEL_REGISTRY_OPERATION,
        "compiled-core-label-registry-operation-v1",
        (
            "websocket_label_registry_list",
            "websocket_label_registry_create_single",
            "websocket_label_registry_update_single",
            "label_registry_readback_exact",
        ),
        provider_boundary="direct_ha_api",
        response_contract={
            "target": "bounded_exact_label_registry_entries",
            "fields": ["name", "color", "icon", "description"],
            "label_id": "exact_core_slug_of_name_no_suffix",
            "dispatch": "at_most_once_label_create_or_update_per_label",
            "never_sent": ["config/label_registry/delete"],
            "readback": "authoritative_exact_label_registry_entry",
            "recovery": "read_only_no_redispatch",
        },
        predispatch=("core_identity", "capability_contract", "target_fingerprint",
                     "session_fingerprint", "generation"),
    ),
)
# HAMCP-155: a runtime-only deletion contract for automation and script
# configuration.  Like 069A/069B it is never part of CORE_CAPABILITY_PROFILES,
# so no signed probe fingerprint changes, and it stays dormant until an
# owner-signed registry reference (planned sequence 13) selects it exactly.
# The generic governed_configuration_operation contract never covers DELETE.
CORE_CONFIGURATION_DELETE_PROFILES = (
    _contract(
        "core.configuration_delete_operation",
        CoreCapabilityClass.CONFIGURATION_DELETE_OPERATION,
        "compiled-core-configuration-delete-operation-v1",
        (
            "rest_config_automation_script_get",
            "rest_config_automation_script_delete_no_body",
            "websocket_entity_registry_list",
            "rest_entity_state_get",
            "config_registry_state_absence_readback",
        ),
        provider_boundary="direct_ha_api",
        response_contract={
            "target": "one_exact_existing_automation_or_script_configuration",
            "resource_types": ["automation", "script"],
            "dispatch": "at_most_once_config_delete_no_body",
            "absent_target": "400_resource_not_found_no_write",
            "readback": "config_404_registry_entry_absent_state_absent",
            "recovery": "read_only_no_redispatch",
        },
        predispatch=("core_identity", "capability_contract", "target_fingerprint",
                     "session_fingerprint", "generation"),
    ),
)
# HAMCP-232/HAMCP-145/script traces (November batch): three runtime-only
# contracts.  Like 069A/069B/165/155 they are never part of
# CORE_CAPABILITY_PROFILES, so no signed probe fingerprint changes, and each
# stays dormant until an owner-signed registry reference (planned sequence
# 15) selects it exactly.  Every read and the one write each gateway sends
# are named; everything in ``never_sent`` is refused before I/O.
CORE_NOVEMBER_BATCH_PROFILES = (
    _contract(
        "core.config_entry_helper_options_operation",
        CoreCapabilityClass.CONFIG_ENTRY_HELPER_OPTIONS_OPERATION,
        "compiled-core-config-entry-helper-options-operation-v1",
        (
            "websocket_entity_registry_get",
            "websocket_entity_registry_list",
            "websocket_config_entries_get_single",
            "rest_options_flow_start_exact_entry",
            "rest_options_flow_abort_exact_flow",
            "rest_options_flow_submit_single_step",
            "rest_entity_state_get",
            "options_flow_suggested_value_readback_exact",
        ),
        provider_boundary="direct_ha_api",
        response_contract={
            "target": "bounded_exact_existing_config_entry_helpers",
            "domains": ["filter", "statistics", "template"],
            "template_types": ["binary_sensor", "sensor"],
            "fields": "closed_per_type_allowlist_pinned_fields_equal_baseline",
            # Review 1 H3: Core replaces the options with the submitted dict.
            "submit": "complete_intended_set_baseline_plus_approved_changes_at_create_and_apply",
            "baseline": "options_flow_open_read_suggested_values_then_abort",
            # Review 1 H2: no exit, a cancelled caller included, leaves a flow open.
            "flow_release": "held_flow_bounded_abort_on_every_exit_404_gone_lost_open_disclosed",
            "dispatch": "at_most_once_options_flow_step_submit_per_config_entry",
            "never_sent": [
                "rest_config_entries_flow_start",
                "rest_config_entries_entry_delete",
                "rest_config_entries_entry_reload",
                "websocket_config_entries_update",
                "websocket_config_entries_disable",
                "websocket_config_entries_subentries",
                "websocket_call_service",
            ],
            "readback": "options_flow_suggested_values_and_modified_at_exact",
            "concurrency": "no_core_compare_and_swap_residual_window_disclosed",
            "recovery": "read_only_no_redispatch",
        },
        predispatch=("core_identity", "capability_contract", "target_fingerprint",
                     "session_fingerprint", "generation"),
    ),
    _contract(
        "core.typed_input_select_option_operation",
        CoreCapabilityClass.TYPED_INPUT_SELECT_OPTION_OPERATION,
        "compiled-core-typed-input-select-option-operation-v1",
        (
            "rest_entity_state_get",
            "websocket_call_service_input_select_select_option_single",
            "input_select_state_readback_exact",
        ),
        provider_boundary="direct_ha_api",
        response_contract={
            "target": "one_exact_existing_input_select",
            "domain": "input_select",
            "service": "select_option",
            "arguments": "option_exact_case_sensitive_member_of_current_options",
            "dispatch": "at_most_once_call_service_input_select_select_option",
            "never_sent": [
                "input_select.reload",
                "input_select.select_first",
                "input_select.select_last",
                "input_select.select_next",
                "input_select.select_previous",
                "input_select.set_options",
            ],
            "readback": "authoritative_exact_state",
            "recovery": "read_only_no_redispatch",
        },
        predispatch=("core_identity", "capability_contract", "target_fingerprint",
                     "session_fingerprint", "generation"),
    ),
    _contract(
        "core.script_trace_read",
        CoreCapabilityClass.SCRIPT_TRACE_READ,
        "compiled-core-script-trace-read-v1",
        (
            "script_trace_list_command_contract",
            "script_trace_list_bounded_mapping_sequence",
            "script_trace_get_command_contract",
            "script_trace_get_not_found_contract",
        ),
        provider_boundary="direct_ha_api",
        response_contract={
            "domain": "script",
            "trace_list": "bounded_list_of_trace_header_mappings",
            "trace_get": {
                "success": "bounded_trace_mapping",
                "missing": {
                    "code": "not_found",
                    "message": "The trace could not be found",
                },
            },
            # Review 1 H1: the one HAMCP-225 trace exit, no screen of its own.
            "sanitization": "hamcp225_trace_exit_shared_credential_screen_then_strict_sanitizer_truncation_last",
            "item_id": "always_exact_script_key_never_list_all",
            "never_sent": [
                "trace/contexts",
                "trace/debug/breakpoint/clear",
                "trace/debug/breakpoint/list",
                "trace/debug/breakpoint/set",
                "trace/debug/breakpoint/subscribe",
                "trace/debug/continue",
                "trace/debug/step",
                "trace/debug/stop",
            ],
            "write": "none",
        },
    ),
    # HAMCP-106 Part 2: governed storage-dashboard metadata.  Josh
    # 2026-10-30: Engineering-native direct Core provider, built inactive and
    # signed with the November batch.  The one write is
    # ``lovelace/dashboards/update`` by ``dashboard_id`` carrying only the
    # requested fields; ``lovelace/dashboards/list`` is the authoritative
    # metadata readback.  Dashboard content is never written; its fingerprint
    # comes from the existing admitted ``get_dashboard_config`` read.
    _contract(
        "core.dashboard_metadata_operation",
        CoreCapabilityClass.DASHBOARD_METADATA_OPERATION,
        "compiled-core-dashboard-metadata-operation-v1",
        (
            "websocket_lovelace_dashboards_list",
            "websocket_lovelace_dashboards_update_by_id",
            "dashboards_list_metadata_readback_exact",
        ),
        provider_boundary="direct_ha_api",
        response_contract={
            "target": "one_exact_existing_storage_dashboard_by_id_not_lovelace",
            "commands": {
                "read": "lovelace/dashboards/list",
                "write": "lovelace/dashboards/update",
            },
            "arguments": {
                "dashboard_id": "exact_id_of_the_one_listed_row_matching_url_path",
                "fields": ["icon", "require_admin", "show_in_sidebar", "title"],
                "title": "non_empty_string_max_255_exact_no_trim",
                "icon": "prefix_colon_name_max_64_or_null_clears",
                "show_in_sidebar": "boolean",
                "require_admin": "boolean",
                "unrequested_fields": "never_sent",
            },
            "dispatch": "at_most_once_lovelace_dashboards_update_per_dashboard",
            "never_sent": [
                "frontend/set_user_data",
                "frontend/update_panel",
                "lovelace/config/delete",
                "lovelace/config/save",
                "lovelace/dashboards/create",
                "lovelace/dashboards/delete",
                "lovelace/resources/create",
                "lovelace/resources/delete",
                "lovelace/resources/update",
            ],
            "readback": "dashboards_list_row_exact_requested_and_unrequested_plus_content_fingerprint_unchanged",
            "content_fingerprint": "existing_admitted_get_dashboard_config_engineering_config_hash",
            "concurrency": "no_core_compare_and_swap_residual_window_disclosed",
            "recovery": "read_only_no_redispatch",
        },
        predispatch=("core_identity", "capability_contract", "target_fingerprint",
                     "session_fingerprint", "generation"),
    ),
)
CORE_RUNTIME_CAPABILITY_PROFILES = (
    CORE_CAPABILITY_PROFILES + CORE_TYPED_OPERATION_PROFILES + CORE_INTEGRATION_INSPECTION_PROFILES
    + CORE_AUTOMATION_BASELINE_PROFILES + CORE_ENTITY_METADATA_PROFILES
    + CORE_ENTITY_AVAILABILITY_EXPOSURE_PROFILES
    + CORE_LABEL_REGISTRY_PROFILES
    + CORE_CONFIGURATION_DELETE_PROFILES
    + CORE_NOVEMBER_BATCH_PROFILES
)


def compiled_exact_authority(version: str) -> tuple[CoreAuthoritySelection, ...]:
    """Return compiled per-capability decisions for one exact release."""

    release = next(
        (item for item in SUPPORTED_CORE_RELEASES if item[0] == version),
        None,
    )
    if release is None:
        return ()
    source_version, source_commit, image_digest = release
    exact_authority = CORE_2026_9_RELEASE_AUTHORITIES.get(version)
    result: list[CoreAuthoritySelection] = []
    for profile in CORE_CAPABILITY_PROFILES:
        status, reason = (
            CoreAuthorityStatus.POSITIVE,
            "compiled_exact_release",
        )
        result.append(
            CoreAuthoritySelection(
                source=CoreAuthoritySource.COMPILED_EXACT,
                status=status,
                profile_id=profile.profile_id,
                profile_version=profile.profile_version,
                adapter_id=profile.adapter_id,
                subject_identity=CORE_IDENTITY,
                subject_version=source_version,
                protocol=CORE_PROTOCOL,
                capability_ids=(profile.capability_id,),
                reason_code=reason,
                evidence_fingerprint=fingerprint(
                    {
                        "model": "compiled-core-release-authority-v2",
                        "version": source_version,
                        "source_commit": source_commit,
                        "image_digest": image_digest,
                        "source_tree": (
                            exact_authority["source_tree"]
                            if exact_authority is not None
                            else None
                        ),
                        "profile": profile.to_mapping(),
                        "status": status.value,
                        "reason_code": reason,
                    }
                ),
            )
        )
    return tuple(result)


__all__ = [
    "CORE_2026_9_1_AUTHORITY",
    "CORE_2026_9_AUTHORITY",
    "CORE_2026_9_RELEASE_AUTHORITIES",
    "CORE_CAPABILITY_PROFILES",
    "CORE_TYPED_OPERATION_PROFILES",
    "CORE_ENTITY_METADATA_PROFILES",
    "CORE_ENTITY_AVAILABILITY_EXPOSURE_PROFILES",
    "CORE_LABEL_REGISTRY_PROFILES",
    "CORE_CONFIGURATION_DELETE_PROFILES",
    "CORE_NOVEMBER_BATCH_PROFILES",
    "CORE_RUNTIME_CAPABILITY_PROFILES",
    "SUPPORTED_CORE_RELEASES",
    "compiled_exact_authority",
]
