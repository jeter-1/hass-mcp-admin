"""Canonical dashboard provider and per-target operational identities."""

from __future__ import annotations

from dataclasses import asdict
import re
from typing import Any, Mapping

from .constants import CANONICAL_URL_PATH, SHA256, UPSTREAM_CONFIG_HASH
from .errors import RawEvidenceError
from .json_codec import engineering_sha256
from .models import DashboardOperationalIdentity, DashboardProviderAuthority


PROVIDER_AUTHORITY_MODEL = "f3-dashboard-provider-authority-v1"
OPERATIONAL_IDENTITY_MODEL = "f3-dashboard-operational-identity-v1"
SESSION_BINDING_MODEL = "fresh-session-exact-catalog-before-each-call-v1"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_PROTOCOL = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_ENTRY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_FAMILY = re.compile(r"^[a-z][a-z0-9_-]{0,95}$")


def _authority_material(authority: DashboardProviderAuthority) -> dict[str, Any]:
    value = asdict(authority)
    value.pop("evidence_hash", None)
    return value


def reviewed_tool_contract_hash(contract: Any) -> str:
    """Hash the complete immutable registry contract without tuple coercion."""

    diagnostic_fields = contract.runtime_contract_field_fingerprints
    if not isinstance(diagnostic_fields, tuple) or any(
        not isinstance(item, tuple)
        or len(item) != 2
        or not all(isinstance(value, str) for value in item)
        for item in diagnostic_fields
    ):
        raise RawEvidenceError("Reviewed dashboard tool contract is malformed")
    material = {
        "input_schema_fingerprint": contract.input_schema_fingerprint,
        "description_fingerprint": contract.description_fingerprint,
        "annotation_fingerprint": contract.annotation_fingerprint,
        "output_contract_fingerprint": contract.output_contract_fingerprint,
        "runtime_contract_fingerprint": contract.runtime_contract_fingerprint,
        "runtime_contract_field_fingerprints": [
            [path, fingerprint] for path, fingerprint in diagnostic_fields
        ],
        "policy_classification": contract.policy_classification,
        "reviewed_automatic_read": contract.reviewed_automatic_read,
        "quarantine_reason": contract.quarantine_reason,
    }
    return engineering_sha256(material)


def build_provider_authority(
    *,
    provider_slug: str,
    server_name: str,
    upstream_version: str,
    protocol_version: str,
    compatibility_entry: str,
    source_commit: str,
    image_index_digest: str,
    contract_family: str,
    dashboard_attestation_fingerprint: str,
    compiled_constraints_fingerprint: str,
    getter_contract_hash: str,
    setter_contract_hash: str,
    catalog_fingerprint: str,
) -> DashboardProviderAuthority:
    """Construct one strict bounded authority; live data never selects logic."""

    base = {
        "model": PROVIDER_AUTHORITY_MODEL,
        "provider_slug": provider_slug,
        "server_name": server_name,
        "upstream_version": upstream_version,
        "protocol_version": protocol_version,
        "compatibility_entry": compatibility_entry,
        "source_commit": source_commit,
        "image_index_digest": image_index_digest,
        "contract_family": contract_family,
        "dashboard_attestation_fingerprint": dashboard_attestation_fingerprint,
        "compiled_constraints_fingerprint": compiled_constraints_fingerprint,
        "getter_contract_hash": getter_contract_hash,
        "setter_contract_hash": setter_contract_hash,
        "catalog_fingerprint": catalog_fingerprint,
        "session_binding_model": SESSION_BINDING_MODEL,
    }
    generation = engineering_sha256(
        {"model": "dashboard-provider-generation-v1", "authority": base}
    )
    authority = DashboardProviderAuthority(
        **base,
        provider_generation=generation,
        evidence_hash="",
    )
    authority = DashboardProviderAuthority(
        **{**asdict(authority), "evidence_hash": engineering_sha256(_authority_material(authority))}
    )
    validate_provider_authority(authority)
    return authority


def validate_provider_authority(authority: Any) -> DashboardProviderAuthority:
    if not isinstance(authority, DashboardProviderAuthority):
        raise RawEvidenceError("Dashboard provider authority is unavailable")
    checks = (
        authority.model == PROVIDER_AUTHORITY_MODEL,
        bool(_SLUG.fullmatch(authority.provider_slug)),
        authority.server_name == "ha-mcp",
        bool(_VERSION.fullmatch(authority.upstream_version)),
        bool(_PROTOCOL.fullmatch(authority.protocol_version)),
        bool(_ENTRY.fullmatch(authority.compatibility_entry)),
        bool(_COMMIT.fullmatch(authority.source_commit)),
        bool(_DIGEST.fullmatch(authority.image_index_digest)),
        bool(_FAMILY.fullmatch(authority.contract_family)),
        authority.session_binding_model == SESSION_BINDING_MODEL,
        all(
            SHA256.fullmatch(value)
            for value in (
                authority.dashboard_attestation_fingerprint,
                authority.compiled_constraints_fingerprint,
                authority.getter_contract_hash,
                authority.setter_contract_hash,
                authority.catalog_fingerprint,
                authority.provider_generation,
                authority.evidence_hash,
            )
        ),
    )
    if not all(checks):
        raise RawEvidenceError("Dashboard provider authority is malformed")
    if authority.evidence_hash != engineering_sha256(_authority_material(authority)):
        raise RawEvidenceError("Dashboard provider authority hash is invalid")
    expected_generation = engineering_sha256(
        {
            "model": "dashboard-provider-generation-v1",
            "authority": {
                key: value
                for key, value in _authority_material(authority).items()
                if key != "provider_generation"
            },
        }
    )
    if authority.provider_generation != expected_generation:
        raise RawEvidenceError("Dashboard provider generation is invalid")
    return authority


def build_operational_identity(
    authority: DashboardProviderAuthority,
    *,
    target_url_path: str,
    storage_mode: str,
    baseline_upstream_config_hash: str,
    baseline_engineering_sha256: str,
) -> DashboardOperationalIdentity:
    validate_provider_authority(authority)
    material = {
        "model": OPERATIONAL_IDENTITY_MODEL,
        "authority": asdict(authority),
        "target_url_path": target_url_path,
        "storage_mode": storage_mode,
        "baseline_upstream_config_hash": baseline_upstream_config_hash,
        "baseline_engineering_sha256": baseline_engineering_sha256,
    }
    identity = DashboardOperationalIdentity(
        model=OPERATIONAL_IDENTITY_MODEL,
        authority=authority,
        target_url_path=target_url_path,
        storage_mode=storage_mode,
        baseline_upstream_config_hash=baseline_upstream_config_hash,
        baseline_engineering_sha256=baseline_engineering_sha256,
        evidence_hash=engineering_sha256(material),
    )
    validate_operational_identity(identity)
    return identity


def validate_operational_identity(identity: Any) -> DashboardOperationalIdentity:
    if not isinstance(identity, DashboardOperationalIdentity):
        raise RawEvidenceError("Dashboard operational identity is unavailable")
    validate_provider_authority(identity.authority)
    if (
        identity.model != OPERATIONAL_IDENTITY_MODEL
        or not CANONICAL_URL_PATH.fullmatch(identity.target_url_path)
        or identity.storage_mode != "storage"
        or not UPSTREAM_CONFIG_HASH.fullmatch(identity.baseline_upstream_config_hash)
        or not SHA256.fullmatch(identity.baseline_engineering_sha256)
        or not SHA256.fullmatch(identity.evidence_hash)
    ):
        raise RawEvidenceError("Dashboard operational identity is malformed")
    material = asdict(identity)
    material.pop("evidence_hash", None)
    if identity.evidence_hash != engineering_sha256(material):
        raise RawEvidenceError("Dashboard operational identity hash is invalid")
    return identity


def provider_authority_from_mapping(value: Mapping[str, Any]) -> DashboardProviderAuthority:
    """Decode only the exact internal projection emitted by the provider."""

    try:
        authority = DashboardProviderAuthority(**dict(value))
    except (TypeError, ValueError):
        raise RawEvidenceError("Dashboard provider authority is malformed") from None
    return validate_provider_authority(authority)


def operational_identity_from_mapping(
    value: Mapping[str, Any],
) -> DashboardOperationalIdentity:
    """Decode a persisted exact per-target identity without repair or coercion."""

    if not isinstance(value, Mapping) or not isinstance(value.get("authority"), Mapping):
        raise RawEvidenceError("Dashboard operational identity is malformed")
    candidate = dict(value)
    candidate["authority"] = provider_authority_from_mapping(candidate["authority"])
    try:
        identity = DashboardOperationalIdentity(**candidate)
    except (TypeError, ValueError):
        raise RawEvidenceError("Dashboard operational identity is malformed") from None
    return validate_operational_identity(identity)


__all__ = [
    "OPERATIONAL_IDENTITY_MODEL",
    "PROVIDER_AUTHORITY_MODEL",
    "SESSION_BINDING_MODEL",
    "build_operational_identity",
    "build_provider_authority",
    "operational_identity_from_mapping",
    "provider_authority_from_mapping",
    "reviewed_tool_contract_hash",
    "validate_operational_identity",
    "validate_provider_authority",
]
