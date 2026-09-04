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
            "ha_mcp_parent_projection",
            "ha_mcp_effective_area_projection",
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


_CORE_2026_9_DECISIONS: dict[str, tuple[CoreAuthorityStatus, str]] = {
    "core.direct_device_registry_read": (
        CoreAuthorityStatus.DENY_ONLY,
        "direct_child_device_adapter_unproven",
    ),
    "core.delegated_device_effective_area": (
        CoreAuthorityStatus.DENY_ONLY,
        "ha_mcp_child_device_contract_unproven",
    ),
    "core.template_semantics": (
        CoreAuthorityStatus.DENY_ONLY,
        "template_semantics_unproven",
    ),
    "core.configuration_validation": (
        CoreAuthorityStatus.DENY_ONLY,
        "probatio_semantics_unproven",
    ),
    "core.dependency_helper_planning": (
        CoreAuthorityStatus.UNAVAILABLE,
        "helper_planning_semantics_unavailable",
    ),
    "core.typed_helper_operation": (
        CoreAuthorityStatus.UNAVAILABLE,
        "typed_helper_semantics_unavailable",
    ),
    "core.f3_mutation_verification": (
        CoreAuthorityStatus.DENY_ONLY,
        "exact_readback_semantics_unproven",
    ),
    "core.governed_configuration_operation": (
        CoreAuthorityStatus.UNAVAILABLE,
        "mutation_core_authority_unavailable",
    ),
}


def compiled_exact_authority(version: str) -> tuple[CoreAuthoritySelection, ...]:
    """Return compiled per-capability decisions for one exact release."""

    release = next(
        (item for item in SUPPORTED_CORE_RELEASES if item[0] == version),
        None,
    )
    if release is None:
        return ()
    source_version, source_commit, image_digest = release
    result: list[CoreAuthoritySelection] = []
    for profile in CORE_CAPABILITY_PROFILES:
        if version == "2026.9.0":
            status, reason = _CORE_2026_9_DECISIONS.get(
                profile.capability_id,
                (CoreAuthorityStatus.POSITIVE, "compiled_exact_release"),
            )
        else:
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
                            CORE_2026_9_AUTHORITY["source_tree"]
                            if version == "2026.9.0"
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
    "CORE_2026_9_AUTHORITY",
    "CORE_CAPABILITY_PROFILES",
    "SUPPORTED_CORE_RELEASES",
    "compiled_exact_authority",
]
