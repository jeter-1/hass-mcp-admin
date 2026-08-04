"""Closed value models for runtime-inert F3 operational adapters.

These models intentionally mirror the declaration-only
``f3-operation-adapter-v1`` shapes without importing the repository-level
repository-level declaration package into the Engineering add-on. F3-A consumes the
objects structurally.  No model in this module is a public or persisted plan
or task schema.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any


F3_ADAPTER_CONTRACT_MODEL = "f3-operation-adapter-v1"
OPERATIONAL_ADAPTER_ID = "operational_administration"
OPERATIONAL_PROVIDER_CONTRACT_MODEL = (
    "ha-mcp-operational-tool-descriptor-v2"
)
OPERATIONAL_PLAN_CONTRACT_VERSION = 3

CREATE_FULL_BACKUP = "create_full_backup"
CONTROLLED_RELOAD = "controlled_reload"
RESTART_ADDON = "restart_addon"
RESTART_HOME_ASSISTANT = "restart_home_assistant"

SUPPORTED_OPERATIONS = (
    CREATE_FULL_BACKUP,
    CONTROLLED_RELOAD,
    RESTART_ADDON,
    RESTART_HOME_ASSISTANT,
)

CAPABILITY_IDENTITIES = {
    CREATE_FULL_BACKUP: "create_full_home_assistant_backup",
    CONTROLLED_RELOAD: "reload_home_assistant_configuration_domain",
    RESTART_ADDON: "restart_installed_home_assistant_addon",
    RESTART_HOME_ASSISTANT: "restart_home_assistant_core",
}

TARGET_TYPES = {
    CREATE_FULL_BACKUP: "backup",
    CONTROLLED_RELOAD: "reload_domain",
    RESTART_ADDON: "addon",
    RESTART_HOME_ASSISTANT: "home_assistant",
}

TARGET_CLASSES = {
    CREATE_FULL_BACKUP: "local_full_backup",
    CONTROLLED_RELOAD: "home_assistant_configuration_domain",
    RESTART_ADDON: "installed_home_assistant_addon",
    RESTART_HOME_ASSISTANT: "home_assistant_core",
}

PROVIDER_OPERATIONS = {
    CREATE_FULL_BACKUP: "ha_manage_backup",
    CONTROLLED_RELOAD: "ha_reload_core",
    RESTART_ADDON: "ha_manage_addon",
    RESTART_HOME_ASSISTANT: "ha_restart",
}

VERIFICATION_MODELS = {
    CREATE_FULL_BACKUP: "f3-full-backup-exact-readback-v1",
    CONTROLLED_RELOAD: "f3-controlled-reload-readiness-v1",
    RESTART_ADDON: "f3-addon-restart-exact-readback-v1",
    RESTART_HOME_ASSISTANT: "f3-home-assistant-restart-recovery-v1",
}

EVIDENCE_DEADLINE_CLASSES = {
    CREATE_FULL_BACKUP: "long_backup_evidence",
    CONTROLLED_RELOAD: "short_reload_evidence",
    RESTART_ADDON: "addon_restart_evidence",
    RESTART_HOME_ASSISTANT: "home_assistant_restart_evidence",
}

EXPECTED_EFFECT_CODES = {
    CREATE_FULL_BACKUP: ("full_local_backup_created",),
    CONTROLLED_RELOAD: ("configuration_domain_reloaded",),
    RESTART_ADDON: ("installed_addon_restarted",),
    RESTART_HOME_ASSISTANT: ("home_assistant_core_restarted",),
}

POLICY_EXPECTATIONS = {
    CREATE_FULL_BACKUP: ("standard_admin", "moderate", "indirect"),
    CONTROLLED_RELOAD: ("standard_admin", "moderate", "indirect"),
    RESTART_ADDON: ("elevated_admin", "high", "indirect"),
    RESTART_HOME_ASSISTANT: ("elevated_admin", "high", "indirect"),
}

RELOAD_PROVIDER_TARGETS = {
    "automation": "automations",
    "script": "scripts",
    "input_boolean": "input_booleans",
    "input_number": "input_numbers",
}

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


def canonical_json(value: Any) -> str:
    """Return exact bounded-friendly canonical JSON or fail closed."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def stable_hash(value: Any) -> str:
    encoded = value if isinstance(value, str) else canonical_json(value)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def decoded_object(value: str, *, field_name: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field_name} is invalid") from exc
    if not isinstance(decoded, dict) or canonical_json(decoded) != value:
        raise ValueError(f"{field_name} is not canonical")
    return decoded


def _require_identifier(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field_name} is invalid")


def _require_sha256(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} is invalid")


@dataclass(frozen=True)
class OperationTarget:
    target_type: str
    target_id: str

    def validate(self) -> None:
        _require_identifier(self.target_type, field_name="target_type")
        _require_identifier(self.target_id, field_name="target_id")


@dataclass(frozen=True)
class LockRequest:
    key: str
    scopes: tuple[str, ...]
    mode: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class AdapterCapabilityDescriptor:
    """Shared descriptor consumed structurally by the F3-A executor."""

    adapter_id: str = OPERATIONAL_ADAPTER_ID
    contract_model: str = F3_ADAPTER_CONTRACT_MODEL
    operation_family: str = "operational_administration"
    supported_operations: tuple[str, ...] = SUPPORTED_OPERATIONS
    rollback_supported: bool = False
    readback_recovery_supported: bool = True
    exact_provider_contract_required: bool = True


@dataclass(frozen=True)
class OperationalCapabilityDescriptor:
    capability_id: str
    contract_model: str
    operation: str
    target_class: str
    provider: str
    provider_contract_model: str
    provider_operation: str
    argument_surface: tuple[str, ...]
    verification_contract_model: str
    rollback_supported: bool
    recovery_supported: bool
    evidence_deadline_class: str
    manual_review_hold_model: str
    limitations: tuple[str, ...]

    def validate(self) -> None:
        if self.operation not in SUPPORTED_OPERATIONS:
            raise ValueError("unknown operational capability")
        if self.capability_id != CAPABILITY_IDENTITIES[self.operation]:
            raise ValueError("capability identity is not canonical")
        if self.contract_model != F3_ADAPTER_CONTRACT_MODEL:
            raise ValueError("adapter contract model is invalid")
        if self.target_class != TARGET_CLASSES[self.operation]:
            raise ValueError("target class is invalid")
        if self.provider_operation != PROVIDER_OPERATIONS[self.operation]:
            raise ValueError("provider operation is invalid")
        if self.verification_contract_model != VERIFICATION_MODELS[self.operation]:
            raise ValueError("verification model is invalid")
        if self.rollback_supported or not self.recovery_supported:
            raise ValueError("operational recovery declaration is invalid")


@dataclass(frozen=True)
class OperationalPreparationRequest:
    """Existing approved-plan material supplied after public planning."""

    plan: Any
    expected_plan_hash: str
    task_id: str
    authoritative_provider_slug: str
    provider_identity_evidence_hash: str

    def validate(self) -> None:
        _require_sha256(self.expected_plan_hash, field_name="expected_plan_hash")
        _require_identifier(self.task_id, field_name="task_id")
        if not _SLUG.fullmatch(self.authoritative_provider_slug):
            raise ValueError("authoritative provider slug is invalid")
        _require_sha256(
            self.provider_identity_evidence_hash,
            field_name="provider_identity_evidence_hash",
        )


@dataclass(frozen=True)
class OperationalAuthoritySnapshot:
    """Caller-owned authorization/task/storage facts reread during preflight."""

    plan_id: str
    plan_hash: str
    task_id: str
    active_task_id: str
    operation: str
    target_type: str
    target_id: str
    policy_decision_hash: str
    approval_consumed: bool
    elevated_acknowledgement_consumed: bool
    governance_storage_status: str
    audit_storage_status: str
    execution_task_storage_status: str
    conflicting_execution_active: bool = False


@dataclass(frozen=True)
class PreparedOperationalOperation:
    """Immutable, exact operational plan projection consumed by F3-A."""

    contract_model: str
    adapter_id: str
    operation: str
    target: OperationTarget
    current_state_fingerprint: str
    normalized_proposed_hash: str
    prepared_operation_hash: str
    risk_level: str
    policy_decision_hash: str
    approval_bundle_hash: str
    expected_effects: tuple[str, ...]
    verification_contract_model: str
    verification_contract_hash: str
    rollback_available: bool
    capability_id: str
    target_class: str
    plan_id: str
    plan_hash: str
    task_id: str
    plan_expires_at: str
    requested_name: str
    provider_id: str
    provider_contract_model: str
    provider_operation: str
    provider_arguments_json: str
    provider_arguments_hash: str
    provider_evidence_json: str
    baseline_json: str
    authoritative_provider_slug: str
    provider_identity_evidence_hash: str
    policy_class: str
    risk_delta: str
    physical_consequence: str
    expected_effect_descriptions: tuple[str, ...]
    warnings: tuple[str, ...]
    limitations: tuple[str, ...]
    verification_contract_json: str
    evidence_deadline_class: str
    manual_review_hold_keys: tuple[str, ...]
    manual_review_hold_max_seconds: int

    def validate(self) -> None:
        if self.contract_model != F3_ADAPTER_CONTRACT_MODEL:
            raise ValueError("prepared adapter model is invalid")
        if self.adapter_id != OPERATIONAL_ADAPTER_ID:
            raise ValueError("prepared adapter identity is invalid")
        if self.operation not in SUPPORTED_OPERATIONS:
            raise ValueError("prepared operation is unsupported")
        self.target.validate()
        if self.target.target_type != TARGET_TYPES[self.operation]:
            raise ValueError("prepared target type is invalid")
        if self.capability_id != CAPABILITY_IDENTITIES[self.operation]:
            raise ValueError("prepared capability identity is invalid")
        if self.target_class != TARGET_CLASSES[self.operation]:
            raise ValueError("prepared target class is invalid")
        for name in (
            "current_state_fingerprint",
            "normalized_proposed_hash",
            "prepared_operation_hash",
            "policy_decision_hash",
            "approval_bundle_hash",
            "verification_contract_hash",
            "provider_arguments_hash",
            "provider_identity_evidence_hash",
        ):
            _require_sha256(getattr(self, name), field_name=name)
        _require_identifier(self.plan_id, field_name="plan_id")
        _require_identifier(self.task_id, field_name="task_id")
        if not _SLUG.fullmatch(self.authoritative_provider_slug):
            raise ValueError("prepared provider slug is invalid")
        if self.provider_operation != PROVIDER_OPERATIONS[self.operation]:
            raise ValueError("prepared provider operation is invalid")
        if self.provider_contract_model != OPERATIONAL_PROVIDER_CONTRACT_MODEL:
            raise ValueError("prepared provider contract is invalid")
        if self.verification_contract_model != VERIFICATION_MODELS[self.operation]:
            raise ValueError("prepared verification model is invalid")
        if self.rollback_available:
            raise ValueError("operational rollback is not available")
        decoded_object(self.provider_arguments_json, field_name="provider_arguments")
        decoded_object(self.provider_evidence_json, field_name="provider_evidence")
        decoded_object(self.baseline_json, field_name="baseline")
        decoded_object(
            self.verification_contract_json,
            field_name="verification_contract",
        )
        if stable_hash(self.provider_arguments_json) != self.provider_arguments_hash:
            raise ValueError("provider argument hash is invalid")
        if not self.manual_review_hold_keys:
            raise ValueError("manual-review hold set is empty")
        if not 60 <= self.manual_review_hold_max_seconds <= 86_400:
            raise ValueError("manual-review hold bound is invalid")

    @property
    def provider_arguments(self) -> dict[str, Any]:
        return decoded_object(
            self.provider_arguments_json, field_name="provider_arguments"
        )

    @property
    def provider_evidence(self) -> dict[str, Any]:
        return decoded_object(
            self.provider_evidence_json, field_name="provider_evidence"
        )

    @property
    def baseline(self) -> dict[str, Any]:
        return decoded_object(self.baseline_json, field_name="baseline")


@dataclass(frozen=True)
class PreflightResult:
    eligible: bool
    outcome: str | None
    confirmed_target: OperationTarget | None
    observed_state_fingerprint: str | None
    provider_contract: str | None
    provider_operation: str | None
    provider_arguments_hash: str | None
    evidence_hash: str | None
    diagnostic_codes: tuple[str, ...] = ()
    mismatch_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class DispatchResult:
    outcome: str
    dispatch_intent_recorded: bool
    mutating_invocation_count: int
    may_have_dispatched: bool
    provider_response_received: bool
    provider_operation_id: str | None = None
    response_evidence_hash: str | None = None
    diagnostic_codes: tuple[str, ...] = ()
    provider_backup_id: str | None = None


@dataclass(frozen=True)
class ObservationResult:
    outcome: str
    attempt_count: int
    observation_complete: bool
    provider_reachable: bool | None
    target_reachable: bool | None
    readback_state_fingerprint: str | None
    intended_result_observed: bool | None
    mismatch_fields: tuple[str, ...] = ()
    evidence_hash: str | None = None
    diagnostic_codes: tuple[str, ...] = ()
    verification_status: str = "pending"


@dataclass(frozen=True)
class VerificationResult:
    outcome: str
    attempt_count: int
    verified: bool | None
    resulting_state_fingerprint: str | None
    mismatch_fields: tuple[str, ...] = ()
    evidence_hash: str | None = None
    manual_review_reason_code: str | None = None


def provider_arguments(operation: str, target_id: str, requested_name: str) -> dict[str, Any]:
    if operation == CREATE_FULL_BACKUP:
        return {"scope": "snapshot", "action": "create", "name": requested_name}
    if operation == CONTROLLED_RELOAD:
        try:
            target = RELOAD_PROVIDER_TARGETS[target_id]
        except KeyError as exc:
            raise ValueError("unsupported reload target") from exc
        return {"target": target}
    if operation == RESTART_ADDON:
        if not _SLUG.fullmatch(target_id):
            raise ValueError("add-on slug is invalid")
        return {"slug": target_id, "action": "restart"}
    if operation == RESTART_HOME_ASSISTANT:
        if target_id != "core":
            raise ValueError("Home Assistant target is invalid")
        return {"confirm": True}
    raise ValueError("unknown operational operation")
