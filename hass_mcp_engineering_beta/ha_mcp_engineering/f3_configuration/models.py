"""Immutable value objects for F3 configuration-adapter conformance.

The current Engineering runtime does not import this package.  These models
are structural implementations of the declaration-only
``f3-operation-adapter-v1`` contract and are intentionally not persisted by
F3-C1.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from f3_contracts.operation_adapter import (
    AdapterCapabilityDescriptor,
    DispatchResult,
    ObservationResult,
    PreparedOperation,
    PreflightResult,
    VerificationResult,
)


F3_CONFIGURATION_ADAPTER_MODEL = "f3-configuration-adapter-v1"
CONFIGURATION_VERIFICATION_CONTRACT_MODEL = (
    "f3-configuration-exact-readback-v1"
)
CONFIGURATION_LOCK_SET_MODEL = "f3-configuration-lock-set-v1"
CONFIGURATION_PROVIDER_CONTRACT = (
    "home-assistant-configuration-resource-gateway-v1"
)

MAX_DIAGNOSTIC_CODES = 16
MAX_MISMATCH_CATEGORIES = 16
MAX_CODE_LENGTH = 96


@dataclass(frozen=True)
class ConfigurationProviderDescriptor:
    """Exact reviewed provider call without retaining configuration content."""

    provider: str
    contract_model: str
    transport: str
    operation: str
    argument_names: tuple[str, ...]
    arguments_hash: str


@dataclass(frozen=True)
class ConfigurationCapabilityDescriptor(AdapterCapabilityDescriptor):
    """Action-specific capability identity consumed by the shared executor."""

    capability_identity: str
    resource_type: str
    action: str
    provider: str
    provider_contract: str
    argument_names: tuple[str, ...]
    validation_contract: str
    verification_contract: str
    lock_set_version: str


@dataclass(frozen=True)
class ConfigurationOperationProposal:
    """Existing immutable plan evidence supplied to adapter preparation.

    Configuration bodies are canonical JSON strings rather than mutable
    dictionaries.  Callers receive a fresh decoded value whenever one is
    needed for validation or provider dispatch.
    """

    plan_id: str
    plan_hash: str
    plan_contract_version: int
    task_id: str
    operation_id: str
    order: int
    depends_on: tuple[str, ...]
    resource_type: str
    action: str
    target_id: str
    current_configuration_json: str | None
    proposed_configuration_json: str
    current_state_fingerprint: str
    proposed_config_hash: str
    normalization_version: int
    risk_level: str
    risk_evidence_hash: str
    policy_class: str
    policy_decision_hash: str
    approval_bundle_hash: str
    plan_expires_at: str
    approval_consumed: bool
    policy_snapshot_valid: bool
    provider_admitted: bool
    rollback_available: bool
    rollback_approval_bundle_hash: str | None = None

    @classmethod
    def from_configs(
        cls,
        *,
        plan_id: str,
        plan_hash: str,
        plan_contract_version: int,
        task_id: str,
        operation_id: str,
        order: int,
        depends_on: tuple[str, ...],
        resource_type: str,
        action: str,
        target_id: str,
        current_config: dict[str, Any] | None,
        proposed_config: dict[str, Any],
        current_state_fingerprint: str,
        proposed_config_hash: str,
        normalization_version: int,
        risk_level: str,
        risk_evidence_hash: str,
        policy_class: str,
        policy_decision_hash: str,
        approval_bundle_hash: str,
        plan_expires_at: str,
        approval_consumed: bool,
        policy_snapshot_valid: bool,
        provider_admitted: bool,
        rollback_available: bool,
        rollback_approval_bundle_hash: str | None = None,
    ) -> "ConfigurationOperationProposal":
        return cls(
            plan_id=plan_id,
            plan_hash=plan_hash,
            plan_contract_version=plan_contract_version,
            task_id=task_id,
            operation_id=operation_id,
            order=order,
            depends_on=tuple(depends_on),
            resource_type=resource_type,
            action=action,
            target_id=target_id,
            current_configuration_json=(
                canonical_json(current_config)
                if current_config is not None
                else None
            ),
            proposed_configuration_json=canonical_json(proposed_config),
            current_state_fingerprint=current_state_fingerprint,
            proposed_config_hash=proposed_config_hash,
            normalization_version=normalization_version,
            risk_level=risk_level,
            risk_evidence_hash=risk_evidence_hash,
            policy_class=policy_class,
            policy_decision_hash=policy_decision_hash,
            approval_bundle_hash=approval_bundle_hash,
            plan_expires_at=plan_expires_at,
            approval_consumed=approval_consumed,
            policy_snapshot_valid=policy_snapshot_valid,
            provider_admitted=provider_admitted,
            rollback_available=rollback_available,
            rollback_approval_bundle_hash=rollback_approval_bundle_hash,
        )

    def current_config(self) -> dict[str, Any] | None:
        return decode_config(self.current_configuration_json)

    def proposed_config(self) -> dict[str, Any]:
        value = decode_config(self.proposed_configuration_json)
        if value is None:  # pragma: no cover - constructor forbids this
            raise ValueError("proposed configuration cannot be null")
        return value


@dataclass(frozen=True)
class PreparedConfigurationOperation(PreparedOperation):
    """F3 prepared operation with all configuration-specific authority."""

    capability_identity: str
    plan_id: str
    plan_hash: str
    plan_contract_version: int
    task_id: str
    operation_id: str
    order: int
    depends_on: tuple[str, ...]
    resource_type: str
    action: str
    current_configuration_json: str | None
    proposed_configuration_json: str
    provider_descriptor: ConfigurationProviderDescriptor
    normalization_version: int
    risk_evidence_hash: str
    policy_class: str
    plan_expires_at: str
    approval_consumed: bool
    policy_snapshot_valid: bool
    provider_admitted: bool
    rollback_approval_bundle_hash: str | None = None

    def current_config(self) -> dict[str, Any] | None:
        return decode_config(self.current_configuration_json)

    def proposed_config(self) -> dict[str, Any]:
        value = decode_config(self.proposed_configuration_json)
        if value is None:  # pragma: no cover - preparation forbids this
            raise ValueError("proposed configuration cannot be null")
        return value


@dataclass(frozen=True)
class ConfigurationPreflightResult(PreflightResult):
    """Preflight evidence without raw configuration or provider content."""

    capability_identity: str | None = None
    configuration_check_status: str | None = None
    target_existence: str | None = None
    lock_set_hash: str | None = None


@dataclass(frozen=True)
class ConfigurationDispatchResult(DispatchResult):
    """Dispatch evidence with an explicit adapter-call counter."""

    adapter_dispatch_count: int = 0
    provider_mutation_count: int | None = None


@dataclass(frozen=True)
class ConfigurationObservationResult(ObservationResult):
    """Exact readback comparison stripped of complete configurations."""

    identity_match: bool | None = None
    resource_exists: bool | None = None
    semantic_match: bool | None = None
    normalization_valid: bool | None = None
    configuration_check_status: str | None = None
    normalized_observed_fingerprint: str | None = None


@dataclass(frozen=True)
class ConfigurationVerificationResult(VerificationResult):
    """Resource-specific terminal comparison and bounded category evidence."""

    configuration_check_status: str | None = None
    identity_match: bool | None = None


def canonical_json(value: dict[str, Any]) -> str:
    """Return immutable canonical JSON for an already accepted configuration."""

    if not isinstance(value, dict):
        raise TypeError("configuration must be an object")
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def decode_config(value: str | None) -> dict[str, Any] | None:
    """Decode one canonical configuration into a fresh mutable value."""

    if value is None:
        return None
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError("configuration JSON must decode to an object")
    if canonical_json(decoded) != value:
        raise ValueError("configuration JSON is not canonical")
    return decoded


def bounded_codes(values: str | tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Canonicalize closed diagnostic categories without retaining messages."""

    if isinstance(values, str):
        values = (values,)
    accepted = {
        value
        for value in values
        if isinstance(value, str)
        and value
        and len(value) <= MAX_CODE_LENGTH
        and value.replace("_", "a").isalnum()
        and value[0].isalpha()
        and value.lower() == value
    }
    return tuple(
        sorted(accepted, key=lambda item: item.encode("utf-8"))[
            :MAX_DIAGNOSTIC_CODES
        ]
    )


def bounded_mismatches(
    values: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    return bounded_codes(values)[:MAX_MISMATCH_CATEGORIES]
