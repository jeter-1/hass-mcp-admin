"""Strict, bounded contracts for Home Assistant Core readmission.

The models in this package describe observations and decisions.  They do not
perform Home Assistant calls and an observation never grants authority by
itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Any, Iterable


MODEL_VERSION = 1
SURFACE = "home_assistant_core"
CORE_IDENTITY = "home-assistant-core"
CORE_PROTOCOL = "ha-rest-websocket-v1"
MAX_IDENTIFIER_CHARS = 128
MAX_REASON_CHARS = 96
MAX_CAPABILITIES = 64
MAX_REQUIRED_CHECKS = 32
MAX_AUTHORITY_SELECTIONS = 128
MAX_ISSUED_LEASES = 64
MAX_ACTIVE_COMMITS = 64
MAX_RETIREMENT_HISTORY = 16
MAX_REPORT_BYTES = 32_768
MAX_SAFE_INTEGER = (1 << 53) - 1

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REASON = re.compile(r"^[a-z][a-z0-9_]{0,95}$")
_VERSION = re.compile(r"^[0-9]{4}\.[0-9]{1,2}\.[0-9]{1,3}(?:[a-z0-9.-]{0,48})?$")


class CoreReadmissionError(ValueError):
    """One readmission input failed strict bounded validation."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class CoreCapabilityClass(str, Enum):
    BASIC_REST_READ = "basic_rest_read"
    BASIC_WEBSOCKET_READ = "basic_websocket_read"
    ENTITY_SERVICE_DISCOVERY = "entity_service_discovery"
    REGISTRY_READ = "registry_read"
    DASHBOARD_CONFIGURATION_READ = "dashboard_configuration_read"
    TEMPLATE_SEMANTICS = "template_semantics"
    TYPED_HELPER_OPERATION = "typed_helper_operation"
    CONFIGURATION_MUTATION_ACTION = "configuration_mutation_action"
    UNCLASSIFIED = "unclassified"

    @property
    def mutation_capable(self) -> bool:
        return self in {
            CoreCapabilityClass.TYPED_HELPER_OPERATION,
            CoreCapabilityClass.CONFIGURATION_MUTATION_ACTION,
            CoreCapabilityClass.UNCLASSIFIED,
        }

    @property
    def semantic(self) -> bool:
        return self in {
            CoreCapabilityClass.TEMPLATE_SEMANTICS,
            CoreCapabilityClass.TYPED_HELPER_OPERATION,
            CoreCapabilityClass.CONFIGURATION_MUTATION_ACTION,
        }


class CoreDisposition(str, Enum):
    VERIFYING = "verifying"
    ADMITTED_EXACT = "admitted_exact"
    ADMITTED_COMPATIBLE = "admitted_compatible"
    PARTIAL = "partial"
    QUARANTINED = "quarantined"
    UNAVAILABLE = "unavailable"

    @property
    def admitted(self) -> bool:
        return self in {
            CoreDisposition.ADMITTED_EXACT,
            CoreDisposition.ADMITTED_COMPATIBLE,
        }


class CoreAuthoritySource(str, Enum):
    COMPILED_EXACT = "compiled_exact"
    VERIFIED_COMPATIBILITY = "verified_compatibility"


class CoreAuthorityStatus(str, Enum):
    POSITIVE = "positive"
    REVOKED = "revoked"
    DENY_ONLY = "deny_only"
    EXPIRED = "expired"
    ROLLBACK = "rollback"
    REPLAY_CONFLICT = "replay_conflict"


def canonical_json(value: Any, *, maximum: int = MAX_REPORT_BYTES) -> bytes:
    """Serialize deterministic internal evidence and reject non-finite values."""

    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CoreReadmissionError("canonical_value_invalid") from exc
    if len(encoded) > maximum:
        raise CoreReadmissionError("canonical_value_oversized")
    return encoded


def fingerprint(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def _text(value: Any, *, code: str, limit: int = MAX_IDENTIFIER_CHARS) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise CoreReadmissionError(code)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise CoreReadmissionError(code)
    return value


def _optional_text(
    value: Any,
    *,
    code: str,
    limit: int = MAX_IDENTIFIER_CHARS,
) -> str | None:
    if value is None:
        return None
    return _text(value, code=code, limit=limit)


def _reason(value: Any) -> str:
    text = _text(value, code="reason_code_invalid", limit=MAX_REASON_CHARS)
    if not _REASON.fullmatch(text):
        raise CoreReadmissionError("reason_code_invalid")
    return text


def _digest(value: Any, *, code: str) -> str:
    text = _text(value, code=code, limit=71)
    if not _DIGEST.fullmatch(text):
        raise CoreReadmissionError(code)
    return text


def _version(value: Any, *, code: str) -> str:
    text = _text(value, code=code, limit=64)
    if not _VERSION.fullmatch(text):
        raise CoreReadmissionError(code)
    return text


def _integer(
    value: Any,
    *,
    code: str,
    minimum: int = 0,
    maximum: int = MAX_SAFE_INTEGER,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise CoreReadmissionError(code)
    return value


def _boolean(value: Any, *, code: str) -> bool:
    if type(value) is not bool:
        raise CoreReadmissionError(code)
    return value


def _tuple(
    value: Any,
    *,
    code: str,
    maximum: int,
) -> tuple[Any, ...]:
    if not isinstance(value, tuple) or len(value) > maximum:
        raise CoreReadmissionError(code)
    return value


def _unique(values: Iterable[str], *, code: str) -> tuple[str, ...]:
    result = tuple(values)
    if len(result) != len(set(result)):
        raise CoreReadmissionError(code)
    return result


@dataclass(frozen=True)
class CoreCapabilityProfile:
    """One binary-owned Core capability and its compiled adapter."""

    capability_id: str
    capability_class: CoreCapabilityClass
    profile_id: str
    profile_version: int
    adapter_id: str
    contract_fingerprint: str
    required_checks: tuple[str, ...]
    auto_eligible: bool = True

    def __post_init__(self) -> None:
        _text(self.capability_id, code="profile_capability_invalid")
        if not isinstance(self.capability_class, CoreCapabilityClass):
            raise CoreReadmissionError("profile_class_invalid")
        _text(self.profile_id, code="profile_id_invalid")
        _integer(
            self.profile_version,
            code="profile_version_invalid",
            minimum=1,
        )
        _text(self.adapter_id, code="profile_adapter_invalid")
        _digest(self.contract_fingerprint, code="profile_contract_invalid")
        _tuple(
            self.required_checks,
            code="profile_checks_invalid",
            maximum=MAX_REQUIRED_CHECKS,
        )
        if not self.required_checks:
            raise CoreReadmissionError("profile_checks_invalid")
        for check in self.required_checks:
            _reason(check)
        _unique(self.required_checks, code="profile_check_duplicate")
        _boolean(
            self.auto_eligible,
            code="profile_auto_eligibility_invalid",
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "capability_class": self.capability_class.value,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "adapter_id": self.adapter_id,
            "contract_fingerprint": self.contract_fingerprint,
            "required_checks": list(self.required_checks),
            "auto_eligible": self.auto_eligible,
        }


@dataclass(frozen=True)
class CoreCapabilityEvidence:
    """Bounded results of binary-owned probes; no upstream digest is trusted."""

    capability_id: str
    passed_checks: tuple[str, ...]
    stable: bool = True
    semantic_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _text(self.capability_id, code="evidence_capability_invalid")
        _tuple(
            self.passed_checks,
            code="evidence_checks_invalid",
            maximum=MAX_REQUIRED_CHECKS,
        )
        for check in self.passed_checks:
            _reason(check)
        _unique(self.passed_checks, code="evidence_check_duplicate")
        _boolean(self.stable, code="evidence_stability_invalid")
        if self.semantic_fingerprint is not None:
            _digest(
                self.semantic_fingerprint,
                code="evidence_semantic_fingerprint_invalid",
            )

    def contract_fingerprint_for(
        self,
        profile: CoreCapabilityProfile,
    ) -> str | None:
        """Return the compiled digest only after every reviewed check passed."""

        if self.capability_id != profile.capability_id or not self.stable:
            return None
        if frozenset(self.passed_checks) != frozenset(profile.required_checks):
            return None
        if profile.capability_class.semantic:
            if self.semantic_fingerprint != profile.contract_fingerprint:
                return None
        elif self.semantic_fingerprint is not None:
            return None
        return profile.contract_fingerprint

    def to_mapping(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "passed_checks": sorted(self.passed_checks),
            "stable": self.stable,
            "semantic_fingerprint": self.semantic_fingerprint,
        }


@dataclass(frozen=True)
class CoreObservation:
    """One complete, sanitized Core identity and capability observation."""

    identity: str
    version: str | None
    protocol: str
    rest_version: str | None
    websocket_auth_version: str | None
    websocket_config_version: str | None
    session_fingerprint: str
    capability_evidence: tuple[CoreCapabilityEvidence, ...]
    connected: bool
    authenticated: bool
    complete: bool
    stable: bool
    reason_code: str

    def __post_init__(self) -> None:
        _text(self.identity, code="observation_identity_invalid")
        if self.version is not None:
            _version(self.version, code="observation_version_invalid")
        _text(self.protocol, code="observation_protocol_invalid")
        for value in (
            self.rest_version,
            self.websocket_auth_version,
            self.websocket_config_version,
        ):
            if value is not None:
                _version(value, code="observation_version_evidence_invalid")
        _digest(
            self.session_fingerprint,
            code="observation_session_fingerprint_invalid",
        )
        _tuple(
            self.capability_evidence,
            code="observation_capabilities_invalid",
            maximum=MAX_CAPABILITIES,
        )
        if any(
            not isinstance(item, CoreCapabilityEvidence)
            for item in self.capability_evidence
        ):
            raise CoreReadmissionError("observation_capabilities_invalid")
        _boolean(self.connected, code="observation_connected_invalid")
        _boolean(self.authenticated, code="observation_authenticated_invalid")
        _boolean(self.complete, code="observation_complete_invalid")
        _boolean(self.stable, code="observation_stable_invalid")
        _reason(self.reason_code)

    @property
    def versions_agree(self) -> bool:
        values = (
            self.version,
            self.rest_version,
            self.websocket_auth_version,
            self.websocket_config_version,
        )
        return all(value is not None for value in values) and len(set(values)) == 1

    @property
    def identity_agrees(self) -> bool:
        return self.identity == CORE_IDENTITY and self.versions_agree

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.to_mapping())

    def evidence_for(self, capability_id: str) -> tuple[CoreCapabilityEvidence, ...]:
        return tuple(
            item
            for item in self.capability_evidence
            if item.capability_id == capability_id
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "model_version": MODEL_VERSION,
            "surface": SURFACE,
            "identity": self.identity,
            "version": self.version,
            "protocol": self.protocol,
            "versions": {
                "rest_config": self.rest_version,
                "websocket_auth_ok": self.websocket_auth_version,
                "websocket_get_config": self.websocket_config_version,
            },
            "session_fingerprint": self.session_fingerprint,
            "capability_evidence": [
                item.to_mapping()
                for item in sorted(
                    self.capability_evidence,
                    key=lambda candidate: (
                        candidate.capability_id,
                        candidate.passed_checks,
                    ),
                )
            ],
            "connected": self.connected,
            "authenticated": self.authenticated,
            "complete": self.complete,
            "stable": self.stable,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class CoreAuthoritySelection:
    """Verified authority selecting one existing Core profile and adapter."""

    source: CoreAuthoritySource
    status: CoreAuthorityStatus
    profile_id: str
    profile_version: int
    adapter_id: str
    subject_identity: str
    subject_version: str
    protocol: str
    capability_ids: tuple[str, ...]
    reason_code: str
    evidence_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, CoreAuthoritySource):
            raise CoreReadmissionError("authority_source_invalid")
        if not isinstance(self.status, CoreAuthorityStatus):
            raise CoreReadmissionError("authority_status_invalid")
        _text(self.profile_id, code="authority_profile_invalid")
        _integer(
            self.profile_version,
            code="authority_profile_version_invalid",
            minimum=1,
        )
        _text(self.adapter_id, code="authority_adapter_invalid")
        _text(self.subject_identity, code="authority_identity_invalid")
        _version(self.subject_version, code="authority_version_invalid")
        _text(self.protocol, code="authority_protocol_invalid")
        _tuple(
            self.capability_ids,
            code="authority_capabilities_invalid",
            maximum=MAX_CAPABILITIES,
        )
        if not self.capability_ids:
            raise CoreReadmissionError("authority_capabilities_invalid")
        for capability_id in self.capability_ids:
            _text(capability_id, code="authority_capability_invalid")
        _unique(self.capability_ids, code="authority_capability_duplicate")
        _reason(self.reason_code)
        _digest(
            self.evidence_fingerprint,
            code="authority_evidence_fingerprint_invalid",
        )

    def matches(self, observation: CoreObservation) -> bool:
        return (
            self.subject_identity == observation.identity
            and self.subject_version == observation.version
            and self.protocol == observation.protocol
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
            "protocol": self.protocol,
            "capability_ids": sorted(self.capability_ids),
            "reason_code": self.reason_code,
            "evidence_fingerprint": self.evidence_fingerprint,
        }


@dataclass(frozen=True)
class CoreCapabilityDecision:
    capability_id: str
    capability_class: CoreCapabilityClass
    profile_id: str
    disposition: CoreDisposition
    reason_code: str
    authority_source: CoreAuthoritySource | None
    adapter_id: str | None
    contract_fingerprint: str

    def __post_init__(self) -> None:
        _text(self.capability_id, code="decision_capability_invalid")
        if not isinstance(self.capability_class, CoreCapabilityClass):
            raise CoreReadmissionError("decision_class_invalid")
        _text(self.profile_id, code="decision_profile_invalid")
        if not isinstance(self.disposition, CoreDisposition):
            raise CoreReadmissionError("decision_disposition_invalid")
        _reason(self.reason_code)
        if self.authority_source is not None and not isinstance(
            self.authority_source,
            CoreAuthoritySource,
        ):
            raise CoreReadmissionError("decision_authority_source_invalid")
        if self.adapter_id is not None:
            _text(self.adapter_id, code="decision_adapter_invalid")
        _digest(self.contract_fingerprint, code="decision_contract_invalid")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "capability_class": self.capability_class.value,
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
class CoreDecisionGeneration:
    generation: int
    disposition: CoreDisposition
    observation_fingerprint: str
    authority_fingerprint: str
    profile_registry_fingerprint: str
    session_fingerprint: str
    decisions: tuple[CoreCapabilityDecision, ...]

    def __post_init__(self) -> None:
        _integer(self.generation, code="generation_invalid", minimum=1)
        if not isinstance(self.disposition, CoreDisposition):
            raise CoreReadmissionError("generation_disposition_invalid")
        for value in (
            self.observation_fingerprint,
            self.authority_fingerprint,
            self.profile_registry_fingerprint,
            self.session_fingerprint,
        ):
            _digest(value, code="generation_fingerprint_invalid")
        _tuple(
            self.decisions,
            code="generation_decisions_invalid",
            maximum=MAX_CAPABILITIES,
        )
        if any(
            not isinstance(item, CoreCapabilityDecision)
            for item in self.decisions
        ):
            raise CoreReadmissionError("generation_decisions_invalid")
        _unique(
            (item.capability_id for item in self.decisions),
            code="generation_capability_duplicate",
        )

    def decision_for(self, capability_id: str) -> CoreCapabilityDecision | None:
        return next(
            (item for item in self.decisions if item.capability_id == capability_id),
            None,
        )

    @property
    def decision_fingerprint(self) -> str:
        return fingerprint(self.to_mapping())

    def to_mapping(self) -> dict[str, Any]:
        return {
            "model_version": MODEL_VERSION,
            "surface": SURFACE,
            "generation": self.generation,
            "disposition": self.disposition.value,
            "observation_fingerprint": self.observation_fingerprint,
            "authority_fingerprint": self.authority_fingerprint,
            "profile_registry_fingerprint": self.profile_registry_fingerprint,
            "session_fingerprint": self.session_fingerprint,
            "decisions": [
                item.to_mapping()
                for item in sorted(
                    self.decisions,
                    key=lambda decision: decision.capability_id,
                )
            ],
        }


@dataclass(frozen=True)
class CoreReconciliationResult:
    generation: CoreDecisionGeneration | None
    disposition: CoreDisposition
    previous_generation: int | None
    published: bool
    idempotent: bool
    reason_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, CoreDisposition):
            raise CoreReadmissionError("result_disposition_invalid")
        if self.previous_generation is not None:
            _integer(
                self.previous_generation,
                code="result_previous_generation_invalid",
                minimum=1,
            )
        _boolean(self.published, code="result_published_invalid")
        _boolean(self.idempotent, code="result_idempotent_invalid")
        _reason(self.reason_code)


@dataclass(frozen=True)
class CoreRouteLease:
    lease_id: str
    surface: str
    capability_id: str
    adapter_id: str
    profile_id: str
    generation: int
    session_fingerprint: str
    observation_fingerprint: str
    target_fingerprint: str | None

    def __post_init__(self) -> None:
        _digest(self.lease_id, code="lease_id_invalid")
        if self.surface != SURFACE:
            raise CoreReadmissionError("lease_surface_invalid")
        _text(self.capability_id, code="lease_capability_invalid")
        _text(self.adapter_id, code="lease_adapter_invalid")
        _text(self.profile_id, code="lease_profile_invalid")
        _integer(self.generation, code="lease_generation_invalid", minimum=1)
        _digest(self.session_fingerprint, code="lease_session_invalid")
        _digest(self.observation_fingerprint, code="lease_observation_invalid")
        if self.target_fingerprint is not None:
            _digest(self.target_fingerprint, code="lease_target_invalid")


@dataclass(frozen=True)
class CoreDispatchCommit:
    commit_id: str
    lease: CoreRouteLease

    def __post_init__(self) -> None:
        _digest(self.commit_id, code="commit_id_invalid")
        if not isinstance(self.lease, CoreRouteLease):
            raise CoreReadmissionError("commit_lease_invalid")


__all__ = [
    "CORE_IDENTITY",
    "CORE_PROTOCOL",
    "MAX_ACTIVE_COMMITS",
    "MAX_AUTHORITY_SELECTIONS",
    "MAX_CAPABILITIES",
    "MAX_ISSUED_LEASES",
    "MAX_REPORT_BYTES",
    "MAX_RETIREMENT_HISTORY",
    "MODEL_VERSION",
    "SURFACE",
    "CoreAuthoritySelection",
    "CoreAuthoritySource",
    "CoreAuthorityStatus",
    "CoreCapabilityClass",
    "CoreCapabilityDecision",
    "CoreCapabilityEvidence",
    "CoreCapabilityProfile",
    "CoreDecisionGeneration",
    "CoreDispatchCommit",
    "CoreDisposition",
    "CoreObservation",
    "CoreReadmissionError",
    "CoreReconciliationResult",
    "CoreRouteLease",
    "canonical_json",
    "fingerprint",
]
