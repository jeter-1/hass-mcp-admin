"""Core-specific signed data; it cannot contain routes, arguments or code.

The envelope, signatures and durable journal use the shared registry lifecycle.
Entries carry Core source/probe contracts, never ha-mcp tool descriptors.
"""

from dataclasses import dataclass
import re
from typing import Any

from ..signed_registry.models import (
    RegistryEnvelope, RegistryErrorCode as Code, RegistryValidationError,
    ReleaseRevocation, _exact_mapping, _identifier, _digest,
)
from .models import CORE_IDENTITY, MAX_SAFE_INTEGER


CORE_REGISTRY_ID = "ha-core-reviewed-releases"
CORE_REGISTRY_KEY_ID = "ha-core-release-registry-v1"
_VERSION = re.compile(r"^(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


def _match(value: Any, pattern: re.Pattern, code: Code) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise RegistryValidationError(code)
    return value


@dataclass(frozen=True)
class CoreProfileReference:
    capability_id: str
    profile_id: str
    profile_version: int
    adapter_id: str
    contract_fingerprint: str

    @classmethod
    def from_mapping(cls, value: Any) -> "CoreProfileReference":
        value = _exact_mapping(value, {
            "capability_id", "profile_id", "profile_version", "adapter_id",
            "contract_fingerprint",
        }, Code.ENTRY_EVIDENCE_INVALID)
        version = value["profile_version"]
        if type(version) is not int or not 1 <= version <= MAX_SAFE_INTEGER:
            raise RegistryValidationError(Code.ENTRY_EVIDENCE_INVALID)
        return cls(
            **{key: _identifier(value[key], code=Code.ENTRY_EVIDENCE_INVALID)
               for key in ("capability_id", "profile_id", "adapter_id")},
            profile_version=version,
            contract_fingerprint=_digest(value["contract_fingerprint"], code=Code.ENTRY_EVIDENCE_INVALID),
        )

    def to_mapping(self) -> dict[str, Any]:
        return dict(vars(self))


@dataclass(frozen=True)
class CoreReleaseEntry:
    entry_id: str
    server_name: str
    version: str
    source_commit: str
    source_tree: str
    source_archive_sha256: str
    image_index_digest: str
    architecture_manifests: tuple[tuple[str, str], ...]
    evidence_sha256: str
    probe_profile_id: str
    probe_profile_sha256: str
    capabilities: tuple[CoreProfileReference, ...]

    @classmethod
    def from_mapping(cls, value: Any) -> "CoreReleaseEntry":
        value = _exact_mapping(value, set(cls.__dataclass_fields__), Code.ENTRY_FIELDS_INVALID)
        if value["server_name"] != CORE_IDENTITY:
            raise RegistryValidationError(Code.ENTRY_IDENTITY_INVALID)
        manifests = _exact_mapping(value["architecture_manifests"],
                                   {"linux/amd64", "linux/arm64"}, Code.ENTRY_IMAGE_INVALID)
        raw_profiles = value["capabilities"]
        if not isinstance(raw_profiles, list) or not 1 <= len(raw_profiles) <= 64:
            raise RegistryValidationError(Code.ENTRY_EVIDENCE_INVALID)
        profiles = tuple(CoreProfileReference.from_mapping(item) for item in raw_profiles)
        if len({p.capability_id for p in profiles}) != len(profiles):
            raise RegistryValidationError(Code.ENTRY_EVIDENCE_INVALID)
        return cls(
            entry_id=_identifier(value["entry_id"], code=Code.ENTRY_IDENTITY_INVALID),
            server_name=CORE_IDENTITY,
            version=_match(value["version"], _VERSION, Code.ENTRY_IDENTITY_INVALID),
            source_commit=_match(value["source_commit"], _SHA, Code.ENTRY_PROVENANCE_INVALID),
            source_tree=_match(value["source_tree"], _SHA, Code.ENTRY_PROVENANCE_INVALID),
            source_archive_sha256=_match(value["source_archive_sha256"], _HASH, Code.ENTRY_PROVENANCE_INVALID),
            image_index_digest=_digest(value["image_index_digest"], code=Code.ENTRY_IMAGE_INVALID),
            architecture_manifests=tuple(sorted(
                (platform, _digest(digest, code=Code.ENTRY_IMAGE_INVALID))
                for platform, digest in manifests.items())),
            evidence_sha256=_match(value["evidence_sha256"], _HASH, Code.ENTRY_EVIDENCE_INVALID),
            probe_profile_id=_identifier(value["probe_profile_id"], code=Code.ENTRY_EVIDENCE_INVALID),
            probe_profile_sha256=_digest(value["probe_profile_sha256"], code=Code.ENTRY_EVIDENCE_INVALID),
            capabilities=profiles,
        )

    @property
    def release_identity(self) -> tuple[str, str]:
        return self.server_name, self.version

    def to_mapping(self) -> dict[str, Any]:
        return {**vars(self), "architecture_manifests": dict(self.architecture_manifests),
                "capabilities": [p.to_mapping() for p in self.capabilities]}


class CoreRegistryEnvelope(RegistryEnvelope):
    """Same authenticated envelope mechanics, distinct closed Core vocabulary."""

    @classmethod
    def _parse_entry(cls, value: Any) -> CoreReleaseEntry:
        return CoreReleaseEntry.from_mapping(value)

    @classmethod
    def _parse_revocation(cls, value: Any) -> ReleaseRevocation:
        result = ReleaseRevocation.from_mapping(value)
        if result.server_name != CORE_IDENTITY:
            raise RegistryValidationError(Code.REVOCATION_IDENTITY_INVALID)
        return result

    @classmethod
    def from_mapping(cls, value: Any) -> "CoreRegistryEnvelope":
        envelope = super().from_mapping(value)
        if envelope.registry_id != CORE_REGISTRY_ID:
            raise RegistryValidationError(Code.REGISTRY_ID_MISMATCH)
        if envelope.key_id != CORE_REGISTRY_KEY_ID:
            raise RegistryValidationError(Code.UNKNOWN_KEY)
        if envelope.sequence > MAX_SAFE_INTEGER:
            raise RegistryValidationError(Code.SEQUENCE_INVALID)
        return envelope
