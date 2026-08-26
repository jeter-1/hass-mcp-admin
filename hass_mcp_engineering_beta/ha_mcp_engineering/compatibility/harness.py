"""Transport-free synthetic update harness for compatibility decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .models import (
    AuthorityBundle,
    AuthorityDecision,
    AuthoritySource,
    AuthorityStatus,
    CapabilityContract,
    CapabilityKind,
    CapabilityProfile,
    CompatibilityModelError,
    CompatibilityObservation,
    ObservedCapability,
    UpstreamSurface,
)


HARNESS_SCHEMA_VERSION = 1
MAX_SCENARIOS = 64


@dataclass(frozen=True)
class OfflineUpdateHarness:
    """Parse synthetic source-shaped evidence without network or filesystem access."""

    profiles: tuple[CapabilityProfile, ...]
    scenarios: Mapping[str, Mapping[str, Any]]
    authority_sets: Mapping[str, Mapping[str, Any]]

    @classmethod
    def from_mapping(cls, value: Any) -> "OfflineUpdateHarness":
        root = _mapping(value, "harness_root_invalid")
        if set(root) != {"schema_version", "profiles", "scenarios", "authority_sets"}:
            raise CompatibilityModelError("harness_fields_invalid")
        if root["schema_version"] != HARNESS_SCHEMA_VERSION:
            raise CompatibilityModelError("harness_schema_unsupported")
        raw_profiles = _list(root["profiles"], "harness_profiles_invalid")
        profiles = tuple(_profile(item) for item in raw_profiles)
        scenarios = _named_mappings(root["scenarios"], "harness_scenarios_invalid")
        authorities = _named_mappings(
            root["authority_sets"], "harness_authorities_invalid"
        )
        if len(scenarios) > MAX_SCENARIOS or len(authorities) > MAX_SCENARIOS:
            raise CompatibilityModelError("harness_scenarios_oversized")
        return cls(profiles=profiles, scenarios=scenarios, authority_sets=authorities)

    def observation(self, scenario_id: str) -> CompatibilityObservation:
        raw = self.scenarios.get(scenario_id)
        if raw is None:
            raise CompatibilityModelError("harness_scenario_unknown")
        surface = _enum(UpstreamSurface, raw.get("surface"), "harness_surface_invalid")
        if surface is UpstreamSurface.HOME_ASSISTANT_CORE:
            return _core_observation(raw)
        if surface is UpstreamSurface.HA_MCP:
            return _ha_mcp_observation(raw)
        return _transport_observation(raw)

    def authority(self, authority_set_id: str) -> AuthorityBundle:
        raw = self.authority_sets.get(authority_set_id)
        if raw is None:
            raise CompatibilityModelError("harness_authority_unknown")
        if set(raw) != {"evaluated_at_epoch", "decisions"}:
            raise CompatibilityModelError("harness_authority_fields_invalid")
        return AuthorityBundle(
            evaluated_at_epoch=raw["evaluated_at_epoch"],
            decisions=tuple(
                _authority_decision(item)
                for item in _list(raw["decisions"], "harness_decisions_invalid")
            ),
        )


def _profile(value: Any) -> CapabilityProfile:
    raw = _mapping(value, "harness_profile_invalid")
    if set(raw) != {
        "profile_id",
        "profile_version",
        "surface",
        "adapter_id",
        "expected_identity",
        "supported_protocols",
        "capabilities",
    }:
        raise CompatibilityModelError("harness_profile_fields_invalid")
    return CapabilityProfile(
        profile_id=raw["profile_id"],
        profile_version=raw["profile_version"],
        surface=_enum(UpstreamSurface, raw["surface"], "harness_surface_invalid"),
        adapter_id=raw["adapter_id"],
        expected_identity=raw["expected_identity"],
        supported_protocols=tuple(
            _list(raw["supported_protocols"], "harness_protocols_invalid")
        ),
        capabilities=tuple(
            _capability_contract(item)
            for item in _list(raw["capabilities"], "harness_capabilities_invalid")
        ),
    )


def _capability_contract(value: Any) -> CapabilityContract:
    raw = _mapping(value, "harness_capability_invalid")
    if set(raw) != {"capability_id", "kind", "contract_fingerprint"}:
        raise CompatibilityModelError("harness_capability_fields_invalid")
    return CapabilityContract(
        capability_id=raw["capability_id"],
        kind=_enum(CapabilityKind, raw["kind"], "harness_kind_invalid"),
        contract_fingerprint=raw["contract_fingerprint"],
    )


def _observed_capabilities(value: Any) -> tuple[ObservedCapability, ...]:
    return tuple(
        ObservedCapability(
            capability_id=raw["capability_id"],
            kind=_enum(CapabilityKind, raw["kind"], "harness_kind_invalid"),
            contract_fingerprint=raw["contract_fingerprint"],
        )
        for item in _list(value, "harness_catalog_invalid")
        for raw in [_exact_mapping(
            item,
            {"capability_id", "kind", "contract_fingerprint"},
            "harness_catalog_entry_invalid",
        )]
    )


def _core_observation(raw: Mapping[str, Any]) -> CompatibilityObservation:
    expected = {
        "surface",
        "session_id",
        "rest_config",
        "websocket_auth_ok",
        "websocket_get_config",
        "capabilities",
        "connected",
        "authenticated",
        "catalog_complete",
        "evidence_reason",
    }
    if set(raw) != expected:
        raise CompatibilityModelError("harness_core_fields_invalid")
    rest = _exact_mapping(raw["rest_config"], {"version"}, "harness_core_rest_invalid")
    auth = _exact_mapping(
        raw["websocket_auth_ok"], {"ha_version"}, "harness_core_auth_invalid"
    )
    config = _exact_mapping(
        raw["websocket_get_config"], {"version"}, "harness_core_config_invalid"
    )
    return CompatibilityObservation(
        surface=UpstreamSurface.HOME_ASSISTANT_CORE,
        identity="home-assistant-core",
        version=rest["version"],
        protocol_version="ha-rest-websocket-v1",
        session_id=raw["session_id"],
        capabilities=_observed_capabilities(raw["capabilities"]),
        connected=raw["connected"],
        authenticated=raw["authenticated"],
        catalog_complete=raw["catalog_complete"],
        evidence_reason=raw["evidence_reason"],
        core_rest_version=rest["version"],
        core_websocket_auth_version=auth["ha_version"],
        core_websocket_config_version=config["version"],
    )


def _ha_mcp_observation(raw: Mapping[str, Any]) -> CompatibilityObservation:
    expected = {
        "surface",
        "session_id",
        "initialize",
        "tools_list_pages",
        "connected",
        "authenticated",
        "catalog_complete",
        "evidence_reason",
    }
    if set(raw) != expected:
        raise CompatibilityModelError("harness_ha_mcp_fields_invalid")
    initialize = _exact_mapping(
        raw["initialize"],
        {"server_name", "server_version", "protocol_version"},
        "harness_initialize_invalid",
    )
    pages = _list(raw["tools_list_pages"], "harness_catalog_pages_invalid")
    catalog: list[Any] = []
    for page in pages:
        page_mapping = _exact_mapping(
            page,
            {"tools", "next_cursor"},
            "harness_catalog_page_invalid",
        )
        catalog.extend(_list(page_mapping["tools"], "harness_catalog_invalid"))
    return CompatibilityObservation(
        surface=UpstreamSurface.HA_MCP,
        identity=initialize["server_name"],
        version=initialize["server_version"],
        protocol_version=initialize["protocol_version"],
        session_id=raw["session_id"],
        capabilities=_observed_capabilities(catalog),
        connected=raw["connected"],
        authenticated=raw["authenticated"],
        catalog_complete=raw["catalog_complete"]
        and (not pages or pages[-1].get("next_cursor") is None),
        evidence_reason=raw["evidence_reason"],
    )


def _transport_observation(raw: Mapping[str, Any]) -> CompatibilityObservation:
    expected = {
        "surface",
        "session_id",
        "configured_contract",
        "connected",
        "authenticated",
        "evidence_reason",
    }
    if set(raw) != expected:
        raise CompatibilityModelError("harness_transport_fields_invalid")
    contract = _exact_mapping(
        raw["configured_contract"],
        {"identity", "version", "protocol_version", "capabilities"},
        "harness_transport_contract_invalid",
    )
    return CompatibilityObservation(
        surface=UpstreamSurface.CONFIGURED_TRANSPORT,
        identity=contract["identity"],
        version=contract["version"],
        protocol_version=contract["protocol_version"],
        session_id=raw["session_id"],
        capabilities=_observed_capabilities(contract["capabilities"]),
        connected=raw["connected"],
        authenticated=raw["authenticated"],
        catalog_complete=True,
        evidence_reason=raw["evidence_reason"],
    )


def _authority_decision(value: Any) -> AuthorityDecision:
    raw = _exact_mapping(
        value,
        {
            "source",
            "status",
            "profile_id",
            "profile_version",
            "adapter_id",
            "subject_identity",
            "subject_version",
            "protocol_version",
            "capability_ids",
            "reason_code",
            "registry_sequence",
            "registry_digest",
            "expires_at_epoch",
        },
        "harness_authority_decision_invalid",
    )
    return AuthorityDecision(
        source=_enum(AuthoritySource, raw["source"], "harness_authority_source_invalid"),
        status=_enum(AuthorityStatus, raw["status"], "harness_authority_status_invalid"),
        profile_id=raw["profile_id"],
        profile_version=raw["profile_version"],
        adapter_id=raw["adapter_id"],
        subject_identity=raw["subject_identity"],
        subject_version=raw["subject_version"],
        protocol_version=raw["protocol_version"],
        capability_ids=tuple(
            _list(raw["capability_ids"], "harness_authority_capabilities_invalid")
        ),
        reason_code=raw["reason_code"],
        registry_sequence=raw["registry_sequence"],
        registry_digest=raw["registry_digest"],
        expires_at_epoch=raw["expires_at_epoch"],
    )


def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CompatibilityModelError(code)
    return value


def _exact_mapping(value: Any, fields: set[str], code: str) -> Mapping[str, Any]:
    result = _mapping(value, code)
    if set(result) != fields:
        raise CompatibilityModelError(code)
    return result


def _list(value: Any, code: str) -> list[Any]:
    if not isinstance(value, list):
        raise CompatibilityModelError(code)
    return value


def _named_mappings(value: Any, code: str) -> Mapping[str, Mapping[str, Any]]:
    root = _mapping(value, code)
    if len(root) > MAX_SCENARIOS:
        raise CompatibilityModelError("harness_scenarios_oversized")
    result: dict[str, Mapping[str, Any]] = {}
    for key, item in root.items():
        if not isinstance(key, str) or not key or len(key) > 128:
            raise CompatibilityModelError(code)
        result[key] = _mapping(item, code)
    return result


def _enum(enum_type, value: Any, code: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise CompatibilityModelError(code) from exc
