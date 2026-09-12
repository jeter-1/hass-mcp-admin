"""Closed evidence collectors selected by compiled or verified Core authority."""

from dataclasses import dataclass

from .models import fingerprint
from .profiles import CORE_CAPABILITY_PROFILES, CORE_2026_9_RELEASE_AUTHORITIES
from .profiles import SUPPORTED_CORE_RELEASES
from ..dependency.semantic_registry import EXPECTED_SEMANTIC_REGISTRY_SHA256


@dataclass(frozen=True)
class CoreProbeProfile:
    profile_id: str
    strict_device_registry: bool
    delegated_device_adapters: tuple[str, ...]

    @property
    def contract_fingerprint(self) -> str:
        return fingerprint({
            "model": "core-probe-profile-v1",
            "profile_id": self.profile_id,
            "strict_device_registry": self.strict_device_registry,
            "delegated_device_adapters": list(self.delegated_device_adapters),
            "capability_contracts": [p.to_mapping() for p in CORE_CAPABILITY_PROFILES],
            "template_registry_sha256": EXPECTED_SEMANTIC_REGISTRY_SHA256,
            "probe_contract": "bounded-two-observation-core-v1",
            "fallback": "none",
        })


LEGACY_PROBE_PROFILE = CoreProbeProfile("core-probes-pre-2026-9-v1", False, ())
CHILD_DEVICE_PROBE_PROFILE = CoreProbeProfile("core-probes-child-devices-v1", True, ("8.4.3",))
CORE_PROBE_PROFILES = (LEGACY_PROBE_PROFILE, CHILD_DEVICE_PROBE_PROFILE)


def compiled_probe_profile(version: str) -> CoreProbeProfile | None:
    if not any(version == release[0] for release in SUPPORTED_CORE_RELEASES):
        return None
    return (
        CHILD_DEVICE_PROBE_PROFILE
        if version in CORE_2026_9_RELEASE_AUTHORITIES
        else LEGACY_PROBE_PROFILE
    )


def known_probe_profile(profile_id: str, digest: str) -> CoreProbeProfile | None:
    return next((p for p in CORE_PROBE_PROFILES if p.profile_id == profile_id
                 and p.contract_fingerprint == digest), None)
