"""Binary-owned Home Assistant Core capability profiles.

The profiles describe contracts already implemented by Engineering.  A
verified compatibility decision may select these identifiers, but cannot add a
profile, adapter, check, route, or capability.
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
    auto_eligible: bool = True,
) -> CoreCapabilityProfile:
    profile_id = capability_id.replace(".", "_") + "_v1"
    material = {
        "model": "ha-core-binary-capability-contract-v1",
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
        auto_eligible=auto_eligible,
    )


CORE_CAPABILITY_PROFILES: tuple[CoreCapabilityProfile, ...] = (
    _contract(
        "core.basic_rest_read",
        CoreCapabilityClass.BASIC_REST_READ,
        "compiled-core-rest-read-v1",
        (
            "rest_config_mapping",
            "rest_states_list",
            "rest_state_entity_shape",
        ),
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
        "core.entity_service_discovery",
        CoreCapabilityClass.ENTITY_SERVICE_DISCOVERY,
        "compiled-core-entity-service-discovery-v1",
        (
            "rest_states_list",
            "rest_services_list",
            "service_domain_mapping",
        ),
        provider_boundary="direct_ha_api",
        response_contract={
            "states": "bounded_list_of_entity_state_mappings",
            "services": "bounded_list_of_domain_service_mappings",
        },
    ),
    _contract(
        "core.registry_read",
        CoreCapabilityClass.REGISTRY_READ,
        "compiled-core-registry-read-v1",
        (
            "websocket_area_registry_list",
            "websocket_device_registry_list",
            "websocket_entity_registry_list",
        ),
        provider_boundary="direct_ha_api",
        response_contract={
            "area_registry": "bounded_list_of_area_mappings",
            "device_registry": "bounded_list_of_device_mappings",
            "entity_registry": "bounded_list_of_entity_registry_mappings",
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
        "core.typed_helper_operation",
        CoreCapabilityClass.TYPED_HELPER_OPERATION,
        "compiled-core-typed-helper-operation-v1",
        (
            "typed_helper_target_exact",
            "typed_helper_service_contract_exact",
            "typed_helper_readback_contract_exact",
        ),
        provider_boundary="direct_home_assistant_state",
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
        "core.configuration_mutation_action",
        CoreCapabilityClass.CONFIGURATION_MUTATION_ACTION,
        "compiled-core-configuration-mutation-action-v1",
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
        # This generic family cannot be admitted automatically.  Concrete
        # configuration adapters remain independently reviewed capabilities.
        auto_eligible=False,
    ),
)


def compiled_exact_authority(version: str) -> tuple[CoreAuthoritySelection, ...]:
    """Return compiled exact selections for one repository-reviewed release."""

    release = next(
        (item for item in SUPPORTED_CORE_RELEASES if item[0] == version),
        None,
    )
    if release is None:
        return ()
    source_version, source_commit, image_digest = release
    return tuple(
        CoreAuthoritySelection(
            source=CoreAuthoritySource.COMPILED_EXACT,
            status=CoreAuthorityStatus.POSITIVE,
            profile_id=profile.profile_id,
            profile_version=profile.profile_version,
            adapter_id=profile.adapter_id,
            subject_identity=CORE_IDENTITY,
            subject_version=source_version,
            protocol=CORE_PROTOCOL,
            capability_ids=(profile.capability_id,),
            reason_code="compiled_exact_release",
            evidence_fingerprint=fingerprint(
                {
                    "model": "compiled-core-release-authority-v1",
                    "version": source_version,
                    "source_commit": source_commit,
                    "image_digest": image_digest,
                    "profile": profile.to_mapping(),
                }
            ),
        )
        for profile in CORE_CAPABILITY_PROFILES
    )


__all__ = [
    "CORE_CAPABILITY_PROFILES",
    "SUPPORTED_CORE_RELEASES",
    "compiled_exact_authority",
]
