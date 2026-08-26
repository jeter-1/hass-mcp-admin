"""Bounded data models for inert capability-scoped compatibility decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Any, Iterable


MODEL_VERSION = 1
MAX_IDENTIFIER_CHARS = 128
MAX_REASON_CHARS = 96
MAX_PROFILES = 64
MAX_CAPABILITIES_PER_PROFILE = 128
MAX_OBSERVED_CAPABILITIES = 512
MAX_AUTHORITY_DECISIONS = 256
MAX_PROJECTION_BYTES = 32_768

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REASON = re.compile(r"^[a-z][a-z0-9_]{0,95}$")


class CompatibilityModelError(ValueError):
    """A bounded compatibility input failed structural validation."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class UpstreamSurface(str, Enum):
    HOME_ASSISTANT_CORE = "home_assistant_core"
    HA_MCP = "ha_mcp"
    CONFIGURED_TRANSPORT = "configured_transport"


class CapabilityKind(str, Enum):
    ORDINARY_READ = "ordinary_read"
    REGISTRY_READ = "registry_read"
    TEMPLATE_SEMANTICS = "template_semantics"
    CONFIGURATION_SEMANTICS = "configuration_semantics"
    GOVERNED_WRITE = "governed_write"
    ACTION = "action"
    PERSISTENT_WRITE = "persistent_write"
    MIXED = "mixed"
    TRANSPORT = "transport"

    @property
    def write_capable(self) -> bool:
        return self in {
            CapabilityKind.GOVERNED_WRITE,
            CapabilityKind.ACTION,
            CapabilityKind.PERSISTENT_WRITE,
            CapabilityKind.MIXED,
        }


class AdmissionDisposition(str, Enum):
    VERIFYING = "verifying"
    ADMITTED_EXACT = "admitted_exact"
    ADMITTED_COMPATIBLE = "admitted_compatible"
    PARTIAL = "partial"
    QUARANTINED = "quarantined"
    UNAVAILABLE = "unavailable"

    @property
    def admitted(self) -> bool:
        return self in {
            AdmissionDisposition.ADMITTED_EXACT,
            AdmissionDisposition.ADMITTED_COMPATIBLE,
        }


class AuthoritySource(str, Enum):
    COMPILED_EXACT = "compiled_exact"
    SIGNED_REGISTRY = "signed_registry"
    LIVE_OBSERVATION = "live_observation"


class AuthorityStatus(str, Enum):
    POSITIVE = "positive"
    REVOKED = "revoked"
    DENY_ONLY = "deny_only"
    EXPIRED = "expired"
    ROLLBACK = "rollback"
    REPLAY_CONFLICT = "replay_conflict"


@dataclass(frozen=True)
class RegistryRefreshResult:
    """Deterministic sequence/digest decision for one verified registry refresh."""

    status: AuthorityStatus
    sequence: int
    digest: str
    accepted: bool
    idempotent: bool

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or self.sequence < 1:
            raise CompatibilityModelError("registry_refresh_sequence_invalid")
        _digest(self.digest, code="registry_refresh_digest_invalid")


def classify_registry_refresh(
    *,
    current_sequence: int | None,
    current_digest: str | None,
    candidate_sequence: int,
    candidate_digest: str,
) -> RegistryRefreshResult:
    """Reject rollback/conflict while accepting a byte-identical replay."""

    if isinstance(candidate_sequence, bool) or candidate_sequence < 1:
        raise CompatibilityModelError("registry_refresh_sequence_invalid")
    _digest(candidate_digest, code="registry_refresh_digest_invalid")
    if current_sequence is None:
        if current_digest is not None:
            raise CompatibilityModelError("registry_refresh_state_invalid")
        return RegistryRefreshResult(
            status=AuthorityStatus.POSITIVE,
            sequence=candidate_sequence,
            digest=candidate_digest,
            accepted=True,
            idempotent=False,
        )
    if (
        isinstance(current_sequence, bool)
        or current_sequence < 1
        or current_digest is None
    ):
        raise CompatibilityModelError("registry_refresh_state_invalid")
    _digest(current_digest, code="registry_refresh_state_invalid")
    if candidate_sequence < current_sequence:
        return RegistryRefreshResult(
            status=AuthorityStatus.ROLLBACK,
            sequence=candidate_sequence,
            digest=candidate_digest,
            accepted=False,
            idempotent=False,
        )
    if candidate_sequence == current_sequence:
        same = candidate_digest == current_digest
        return RegistryRefreshResult(
            status=(
                AuthorityStatus.POSITIVE
                if same
                else AuthorityStatus.REPLAY_CONFLICT
            ),
            sequence=candidate_sequence,
            digest=candidate_digest,
            accepted=same,
            idempotent=same,
        )
    return RegistryRefreshResult(
        status=AuthorityStatus.POSITIVE,
        sequence=candidate_sequence,
        digest=candidate_digest,
        accepted=True,
        idempotent=False,
    )


def canonical_json(value: Any) -> bytes:
    """Return strict canonical JSON for deterministic internal evidence."""

    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CompatibilityModelError("canonical_value_invalid") from exc
    if len(encoded) > MAX_PROJECTION_BYTES:
        raise CompatibilityModelError("canonical_value_oversized")
    return encoded


def evidence_fingerprint(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def _safe_text(value: Any, *, code: str, limit: int = MAX_IDENTIFIER_CHARS) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise CompatibilityModelError(code)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise CompatibilityModelError(code)
    return value


def _reason(value: Any) -> str:
    text = _safe_text(value, code="reason_code_invalid", limit=MAX_REASON_CHARS)
    if not _REASON.fullmatch(text):
        raise CompatibilityModelError("reason_code_invalid")
    return text


def _digest(value: Any, *, code: str) -> str:
    text = _safe_text(value, code=code, limit=71)
    if not _DIGEST.fullmatch(text):
        raise CompatibilityModelError(code)
    return text


def _unique(items: Iterable[str], *, code: str) -> tuple[str, ...]:
    result = tuple(items)
    if len(result) != len(set(result)):
        raise CompatibilityModelError(code)
    return result


@dataclass(frozen=True)
class CapabilityContract:
    """One executable contract already compiled into the Engineering binary."""

    capability_id: str
    kind: CapabilityKind
    contract_fingerprint: str

    def __post_init__(self) -> None:
        _safe_text(self.capability_id, code="capability_id_invalid")
        _digest(self.contract_fingerprint, code="contract_fingerprint_invalid")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "kind": self.kind.value,
            "contract_fingerprint": self.contract_fingerprint,
        }


@dataclass(frozen=True)
class CapabilityProfile:
    """Binary-known profile and adapter; data authority may only select it."""

    profile_id: str
    profile_version: int
    surface: UpstreamSurface
    adapter_id: str
    expected_identity: str
    supported_protocols: tuple[str, ...]
    capabilities: tuple[CapabilityContract, ...]

    def __post_init__(self) -> None:
        _safe_text(self.profile_id, code="profile_id_invalid")
        _safe_text(self.adapter_id, code="adapter_id_invalid")
        _safe_text(self.expected_identity, code="profile_identity_invalid")
        if isinstance(self.profile_version, bool) or self.profile_version < 1:
            raise CompatibilityModelError("profile_version_invalid")
        if not self.supported_protocols:
            raise CompatibilityModelError("profile_protocols_invalid")
        for protocol in self.supported_protocols:
            _safe_text(protocol, code="profile_protocols_invalid")
        _unique(self.supported_protocols, code="profile_protocols_duplicate")
        if not self.capabilities or len(self.capabilities) > MAX_CAPABILITIES_PER_PROFILE:
            raise CompatibilityModelError("profile_capabilities_invalid")
        _unique(
            (item.capability_id for item in self.capabilities),
            code="profile_capability_duplicate",
        )

    def capability(self, capability_id: str) -> CapabilityContract | None:
        return next(
            (item for item in self.capabilities if item.capability_id == capability_id),
            None,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "surface": self.surface.value,
            "adapter_id": self.adapter_id,
            "expected_identity": self.expected_identity,
            "supported_protocols": sorted(self.supported_protocols),
            "capabilities": [
                item.to_mapping()
                for item in sorted(self.capabilities, key=lambda value: value.capability_id)
            ],
        }


@dataclass(frozen=True)
class ObservedCapability:
    """Sanitized live contract observation; it carries no authority."""

    capability_id: str
    kind: CapabilityKind
    contract_fingerprint: str

    def __post_init__(self) -> None:
        _safe_text(self.capability_id, code="observed_capability_id_invalid")
        _digest(
            self.contract_fingerprint,
            code="observed_contract_fingerprint_invalid",
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "kind": self.kind.value,
            "contract_fingerprint": self.contract_fingerprint,
        }


@dataclass(frozen=True)
class CompatibilityObservation:
    """One complete bounded observation of an upstream surface."""

    surface: UpstreamSurface
    identity: str
    version: str
    protocol_version: str
    session_id: str
    capabilities: tuple[ObservedCapability, ...]
    connected: bool = True
    authenticated: bool = True
    catalog_complete: bool = True
    evidence_reason: str = "observation_complete"
    core_rest_version: str | None = None
    core_websocket_auth_version: str | None = None
    core_websocket_config_version: str | None = None

    def __post_init__(self) -> None:
        _safe_text(self.identity, code="observation_identity_invalid")
        _safe_text(self.version, code="observation_version_invalid")
        _safe_text(self.protocol_version, code="observation_protocol_invalid")
        _safe_text(self.session_id, code="observation_session_invalid")
        _reason(self.evidence_reason)
        if len(self.capabilities) > MAX_OBSERVED_CAPABILITIES:
            raise CompatibilityModelError("observed_catalog_oversized")
        core_versions = (
            self.core_rest_version,
            self.core_websocket_auth_version,
            self.core_websocket_config_version,
        )
        if self.surface is UpstreamSurface.HOME_ASSISTANT_CORE:
            if any(value is None for value in core_versions):
                raise CompatibilityModelError("core_version_evidence_missing")
            for value in core_versions:
                _safe_text(value, code="core_version_evidence_invalid")
        elif any(value is not None for value in core_versions):
            raise CompatibilityModelError("core_version_evidence_unexpected")

    @property
    def fingerprint(self) -> str:
        return evidence_fingerprint(self.to_mapping(include_session=True))

    @property
    def session_fingerprint(self) -> str:
        return evidence_fingerprint({"session_id": self.session_id})

    @property
    def duplicate_capability_ids(self) -> frozenset[str]:
        seen: set[str] = set()
        duplicate: set[str] = set()
        for item in self.capabilities:
            if item.capability_id in seen:
                duplicate.add(item.capability_id)
            seen.add(item.capability_id)
        return frozenset(duplicate)

    @property
    def core_versions_agree(self) -> bool:
        if self.surface is not UpstreamSurface.HOME_ASSISTANT_CORE:
            return True
        return len(
            {
                self.version,
                self.core_rest_version,
                self.core_websocket_auth_version,
                self.core_websocket_config_version,
            }
        ) == 1

    def to_mapping(self, *, include_session: bool) -> dict[str, Any]:
        value: dict[str, Any] = {
            "model_version": MODEL_VERSION,
            "surface": self.surface.value,
            "identity": self.identity,
            "version": self.version,
            "protocol_version": self.protocol_version,
            "capabilities": [
                item.to_mapping()
                for item in sorted(
                    self.capabilities,
                    key=lambda capability: (
                        capability.capability_id,
                        capability.kind.value,
                        capability.contract_fingerprint,
                    ),
                )
            ],
            "connected": self.connected,
            "authenticated": self.authenticated,
            "catalog_complete": self.catalog_complete,
            "evidence_reason": self.evidence_reason,
        }
        if include_session:
            value["session_id"] = self.session_id
        if self.surface is UpstreamSurface.HOME_ASSISTANT_CORE:
            value["core_versions"] = {
                "rest_config": self.core_rest_version,
                "websocket_auth_ok": self.core_websocket_auth_version,
                "websocket_get_config": self.core_websocket_config_version,
            }
        return value


@dataclass(frozen=True)
class AuthorityDecision:
    """Data-only authority selecting a precompiled profile and adapter."""

    source: AuthoritySource
    status: AuthorityStatus
    profile_id: str
    profile_version: int
    adapter_id: str
    subject_identity: str
    subject_version: str
    protocol_version: str
    capability_ids: tuple[str, ...]
    reason_code: str
    registry_sequence: int | None = None
    registry_digest: str | None = None
    expires_at_epoch: int | None = None

    def __post_init__(self) -> None:
        _safe_text(self.profile_id, code="authority_profile_invalid")
        _safe_text(self.adapter_id, code="authority_adapter_invalid")
        _safe_text(self.subject_identity, code="authority_identity_invalid")
        _safe_text(self.subject_version, code="authority_version_invalid")
        _safe_text(self.protocol_version, code="authority_protocol_invalid")
        _reason(self.reason_code)
        if isinstance(self.profile_version, bool) or self.profile_version < 1:
            raise CompatibilityModelError("authority_profile_version_invalid")
        if len(self.capability_ids) > MAX_CAPABILITIES_PER_PROFILE:
            raise CompatibilityModelError("authority_capabilities_oversized")
        for capability_id in self.capability_ids:
            _safe_text(capability_id, code="authority_capability_invalid")
        _unique(self.capability_ids, code="authority_capability_duplicate")
        if self.source is AuthoritySource.SIGNED_REGISTRY:
            if (
                isinstance(self.registry_sequence, bool)
                or not isinstance(self.registry_sequence, int)
                or self.registry_sequence < 1
            ):
                raise CompatibilityModelError("authority_sequence_invalid")
            _digest(self.registry_digest, code="authority_registry_digest_invalid")
        elif any(
            value is not None
            for value in (self.registry_sequence, self.registry_digest, self.expires_at_epoch)
        ):
            raise CompatibilityModelError("authority_registry_fields_unexpected")
        if self.expires_at_epoch is not None and (
            isinstance(self.expires_at_epoch, bool)
            or not isinstance(self.expires_at_epoch, int)
            or self.expires_at_epoch < 0
        ):
            raise CompatibilityModelError("authority_expiry_invalid")

    def matches_observation(self, observation: CompatibilityObservation) -> bool:
        return (
            self.subject_identity == observation.identity
            and self.subject_version == observation.version
            and self.protocol_version == observation.protocol_version
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "source": self.source.value,
            "status": self.status.value,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "adapter_id": self.adapter_id,
            "subject_identity": self.subject_identity,
            "subject_version": self.subject_version,
            "protocol_version": self.protocol_version,
            "capability_ids": sorted(self.capability_ids),
            "reason_code": self.reason_code,
            "registry_sequence": self.registry_sequence,
            "registry_digest": self.registry_digest,
            "expires_at_epoch": self.expires_at_epoch,
        }


@dataclass(frozen=True)
class AuthorityBundle:
    """All bounded positive and deny-only evidence for one reconciliation."""

    evaluated_at_epoch: int
    decisions: tuple[AuthorityDecision, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.evaluated_at_epoch, bool)
            or not isinstance(self.evaluated_at_epoch, int)
            or self.evaluated_at_epoch < 0
        ):
            raise CompatibilityModelError("authority_time_invalid")
        if len(self.decisions) > MAX_AUTHORITY_DECISIONS:
            raise CompatibilityModelError("authority_decisions_oversized")

    @property
    def fingerprint(self) -> str:
        return evidence_fingerprint(self.to_mapping())

    def to_mapping(self) -> dict[str, Any]:
        return {
            "model_version": MODEL_VERSION,
            "evaluated_at_epoch": self.evaluated_at_epoch,
            "decisions": [
                item.to_mapping()
                for item in sorted(
                    self.decisions,
                    key=lambda decision: (
                        decision.profile_id,
                        decision.profile_version,
                        decision.source.value,
                        decision.status.value,
                        decision.subject_version,
                    ),
                )
            ],
        }


@dataclass(frozen=True)
class CapabilityDecision:
    capability_id: str
    profile_id: str | None
    disposition: AdmissionDisposition
    reason_code: str
    authority_source: AuthoritySource | None
    adapter_id: str | None
    contract_fingerprint: str

    def __post_init__(self) -> None:
        _safe_text(self.capability_id, code="decision_capability_invalid")
        if self.profile_id is not None:
            _safe_text(self.profile_id, code="decision_profile_invalid")
        if self.adapter_id is not None:
            _safe_text(self.adapter_id, code="decision_adapter_invalid")
        _reason(self.reason_code)
        _digest(self.contract_fingerprint, code="decision_fingerprint_invalid")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "profile_id": self.profile_id,
            "disposition": self.disposition.value,
            "reason_code": self.reason_code,
            "authority_source": (
                self.authority_source.value if self.authority_source else None
            ),
            "adapter_id": self.adapter_id,
            "contract_fingerprint": self.contract_fingerprint,
        }


@dataclass(frozen=True)
class DecisionGeneration:
    generation: int
    surface: UpstreamSurface
    disposition: AdmissionDisposition
    observation_fingerprint: str
    authority_fingerprint: str
    profile_registry_fingerprint: str
    session_fingerprint: str
    decisions: tuple[CapabilityDecision, ...]

    def __post_init__(self) -> None:
        if isinstance(self.generation, bool) or self.generation < 1:
            raise CompatibilityModelError("decision_generation_invalid")
        for value in (
            self.observation_fingerprint,
            self.authority_fingerprint,
            self.profile_registry_fingerprint,
            self.session_fingerprint,
        ):
            _digest(value, code="decision_evidence_fingerprint_invalid")

    @property
    def admitted_capability_ids(self) -> tuple[str, ...]:
        return tuple(
            item.capability_id for item in self.decisions if item.disposition.admitted
        )

    @property
    def decision_fingerprint(self) -> str:
        return evidence_fingerprint(self.to_mapping())

    def decision_for(self, capability_id: str) -> CapabilityDecision | None:
        return next(
            (item for item in self.decisions if item.capability_id == capability_id),
            None,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "model_version": MODEL_VERSION,
            "generation": self.generation,
            "surface": self.surface.value,
            "disposition": self.disposition.value,
            "observation_fingerprint": self.observation_fingerprint,
            "authority_fingerprint": self.authority_fingerprint,
            "profile_registry_fingerprint": self.profile_registry_fingerprint,
            "session_fingerprint": self.session_fingerprint,
            "decisions": [
                item.to_mapping()
                for item in sorted(self.decisions, key=lambda value: value.capability_id)
            ],
        }


@dataclass(frozen=True)
class ReconciliationResult:
    generation: DecisionGeneration | None
    disposition: AdmissionDisposition
    retired_generation: int | None
    published: bool
    idempotent: bool
    reason_code: str
    events: tuple[str, ...]

    def __post_init__(self) -> None:
        _reason(self.reason_code)
        for event in self.events:
            _reason(event)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "model_version": MODEL_VERSION,
            "generation": self.generation.to_mapping() if self.generation else None,
            "disposition": self.disposition.value,
            "retired_generation": self.retired_generation,
            "published": self.published,
            "idempotent": self.idempotent,
            "reason_code": self.reason_code,
            "events": list(self.events),
        }


@dataclass(frozen=True)
class RouteLease:
    lease_id: str
    generation: int
    capability_id: str
    adapter_id: str
    session_fingerprint: str

    def __post_init__(self) -> None:
        _digest(self.lease_id, code="lease_id_invalid")
        _digest(self.session_fingerprint, code="lease_session_invalid")


@dataclass(frozen=True)
class DispatchCommit:
    lease: RouteLease
    commit_id: str

    def __post_init__(self) -> None:
        _digest(self.commit_id, code="commit_id_invalid")
