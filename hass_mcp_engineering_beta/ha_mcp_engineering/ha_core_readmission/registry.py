"""Core authority selection over the existing authenticated journal lifecycle."""

from pathlib import Path

from ..ha_mcp_readmission.registry import RegistryBinding, SignedReleaseRegistry
from ..signed_registry.canonical import sha256_digest
from .models import (CORE_IDENTITY, CORE_PROTOCOL, CoreAuthoritySelection,
                     CoreAuthoritySource, CoreAuthorityStatus)
from .profiles import CORE_CAPABILITY_PROFILES, compiled_exact_authority
from .probe_profiles import compiled_probe_profile, known_probe_profile
from .registry_models import CORE_REGISTRY_ID, CORE_REGISTRY_KEY_ID, CoreRegistryEnvelope


CORE_REGISTRY_URL = (
    "https://raw.githubusercontent.com/jeter-1/hass-mcp-admin/"
    "main/upstream-trust/ha-core-release-registry.json"
)
CORE_REGISTRY_CACHE_PATH = Path("/data/ha-core-release-registry-cache.json")
CORE_REGISTRY_BINDING = RegistryBinding(
    registry_id=CORE_REGISTRY_ID,
    key_id=CORE_REGISTRY_KEY_ID,
    url=CORE_REGISTRY_URL,
    envelope_type=CoreRegistryEnvelope,
)


class CoreReleaseRegistry(SignedReleaseRegistry):
    """Select only compiled Core contracts; independent keys, URL and cache."""

    def __init__(self, *, enabled: bool, public_key: str,
                 cache_path: Path = CORE_REGISTRY_CACHE_PATH,
                 fetcher=None, now=None):
        super().__init__(enabled=enabled, public_key=public_key,
                         cache_path=cache_path, fetcher=fetcher, now=now,
                         binding=CORE_REGISTRY_BINDING)

    def collection_token(self) -> str:
        """Fence a two-observation collection against registry/time movement."""
        authority = self.authority()
        return sha256_digest({
            "digest": authority.content_digest,
            "current": authority.positive_authority_current,
            "denied": authority.surface_denied,
            "revocations": [r.to_mapping() for r in authority.revocations],
        })

    def probe_profile_for(self, version: str):
        authority = self.authority()
        if authority.surface_denied or authority.revoked(CORE_IDENTITY, version):
            return None
        compiled = compiled_probe_profile(version)
        if compiled is not None:
            return compiled
        entry = authority.entry_for(CORE_IDENTITY, version)
        return None if entry is None else known_probe_profile(
            entry.probe_profile_id, entry.probe_profile_sha256)

    def selections(self, version: str) -> tuple[CoreAuthoritySelection, ...]:
        authority = self.authority()
        denied = authority.surface_denied or authority.revoked(CORE_IDENTITY, version)
        compiled = compiled_exact_authority(version)
        if not denied and compiled:
            return compiled
        entry = authority.entry_for(CORE_IDENTITY, version)
        probe = self.probe_profile_for(version)
        references = {} if entry is None else {p.capability_id: p for p in entry.capabilities}
        result = []
        for profile in CORE_CAPABILITY_PROFILES:
            reference = references.get(profile.capability_id)
            matches = bool(
                probe is not None and reference is not None
                and reference.profile_id == profile.profile_id
                and reference.profile_version == profile.profile_version
                and reference.adapter_id == profile.adapter_id
                and reference.contract_fingerprint == profile.contract_fingerprint
                and profile.auto_eligible
            )
            if not denied and not matches:
                continue
            result.append(CoreAuthoritySelection(
                source=CoreAuthoritySource.VERIFIED_COMPATIBILITY,
                status=(CoreAuthorityStatus.DENY_ONLY if denied
                        else CoreAuthorityStatus.POSITIVE),
                profile_id=profile.profile_id,
                profile_version=profile.profile_version,
                adapter_id=profile.adapter_id,
                subject_identity=CORE_IDENTITY,
                subject_version=version,
                protocol=CORE_PROTOCOL,
                capability_ids=(profile.capability_id,),
                reason_code=("verified_core_registry_denial" if denied
                             else "verified_core_compatible_release"),
                evidence_fingerprint=sha256_digest({
                    "model": "core-registry-selection-v1", "version": version,
                    "profile": profile.to_mapping(), "denied": bool(denied),
                    "entry": None if entry is None else entry.to_mapping(),
                }),
            ))
        return tuple(result)

    def selection_token(self, version: str) -> str:
        probe = self.probe_profile_for(version)
        return sha256_digest({
            "selections": [s.to_mapping() for s in self.selections(version)],
            "probe": None if probe is None else probe.contract_fingerprint,
        })
