"""Closed value models for runtime-inert F3 operational adapters.

Canonical adapter objects come only from :mod:`ha_mcp_engineering.f3.contracts`.
The additional frozen objects below bind existing operational-plan evidence to
one future public-task/child-execution pair without defining a persisted schema
or an independent execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Protocol

from ha_mcp_engineering.f3.contracts import (
    AdapterCapabilityDescriptor,
    DispatchResult,
    F3_ADAPTER_CONTRACT_MODEL,
    LockMode,
    LockRequest,
    LockScope,
    NormalizedOperationOutcome,
    ObservationResult,
    OperationTarget,
    PreflightResult,
    PreparedOperation,
    RecoveryContext,
    VerificationResult,
)


OPERATIONAL_ADAPTER_ID = "operational_administration"
OPERATIONAL_PROVIDER_CONTRACT_MODEL = (
    "ha-mcp-operational-tool-descriptor-v2"
)
OPERATIONAL_PLAN_CONTRACT_VERSION = 3
OPERATIONAL_EVIDENCE_PROJECTION_MODEL = (
    "f3-authoritative-operational-child-evidence-v1"
)

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
    CONTROLLED_RELOAD: "f3-controlled-reload-effect-readback-v1",
    RESTART_ADDON: "f3-addon-restart-exact-readback-v1",
    RESTART_HOME_ASSISTANT: "f3-home-assistant-restart-outage-recovery-v1",
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
_EVIDENCE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,95}$")


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


def _parse_aware(value: str, *, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} is not timezone-aware")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class OperationalCapabilityDescriptor(AdapterCapabilityDescriptor):
    """Exact operation-specific binding layered on the canonical descriptor."""

    capability_id: str
    target_class: str
    provider: str
    provider_contract_model: str
    provider_operation: str
    argument_surface: tuple[str, ...]
    verification_contract_model: str
    recovery_supported: bool
    evidence_deadline_class: str
    manual_review_hold_model: str
    limitations: tuple[str, ...]

    def validate(self) -> None:
        if len(self.supported_operations) != 1:
            raise ValueError("operational capability must bind one operation")
        operation = self.supported_operations[0]
        if operation not in SUPPORTED_OPERATIONS:
            raise ValueError("unknown operational capability")
        if self.capability_id != CAPABILITY_IDENTITIES[operation]:
            raise ValueError("capability identity is not canonical")
        if self.contract_model != F3_ADAPTER_CONTRACT_MODEL:
            raise ValueError("adapter contract model is invalid")
        if self.adapter_id != OPERATIONAL_ADAPTER_ID:
            raise ValueError("adapter identity is invalid")
        if self.target_class != TARGET_CLASSES[operation]:
            raise ValueError("target class is invalid")
        if self.provider_operation != PROVIDER_OPERATIONS[operation]:
            raise ValueError("provider operation is invalid")
        if self.verification_contract_model != VERIFICATION_MODELS[operation]:
            raise ValueError("verification model is invalid")
        if self.rollback_supported or not self.recovery_supported:
            raise ValueError("operational recovery declaration is invalid")

    @property
    def operation(self) -> str:
        return self.supported_operations[0]


@dataclass(frozen=True)
class OperationalPreparationRequest:
    """Existing immutable approved-plan material supplied before F3 execution."""

    plan: Any
    expected_plan_hash: str
    public_task_id: str
    child_execution_id: str
    authoritative_provider_slug: str
    provider_identity_evidence_hash: str

    def validate(self) -> None:
        _require_sha256(self.expected_plan_hash, field_name="expected_plan_hash")
        _require_identifier(self.public_task_id, field_name="public_task_id")
        _require_identifier(self.child_execution_id, field_name="child_execution_id")
        if not _SLUG.fullmatch(self.authoritative_provider_slug):
            raise ValueError("authoritative provider slug is invalid")
        _require_sha256(
            self.provider_identity_evidence_hash,
            field_name="provider_identity_evidence_hash",
        )


@dataclass(frozen=True)
class OperationalAuthoritySnapshot:
    """Caller-owned plan, child, authorization, and storage evidence.

    This snapshot deliberately contains no ``approval_consumed`` field.
    Consumption is the shared executor caller's idempotent callback and occurs
    only after this final locked preflight succeeds.
    """

    plan_id: str
    plan_hash: str
    public_task_id: str
    child_execution_id: str
    active_child_execution_id: str
    operation: str
    target_type: str
    target_id: str
    policy_decision_hash: str
    approval_bundle_hash: str
    authorization_evidence_status: str
    elevated_acknowledgement_bound: bool
    governance_storage_status: str
    audit_storage_status: str
    execution_task_storage_status: str
    f3_execution_storage_status: str
    f3_lock_storage_status: str
    restart_reconciliation_compatible: bool = True


@dataclass(frozen=True)
class PreparedOperationalOperation(PreparedOperation):
    """Canonical prepared operation plus immutable operational-plan evidence."""

    capability_id: str
    target_class: str
    plan_id: str
    plan_hash: str
    plan_contract_version: int
    public_task_id: str
    child_execution_id: str
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
    evidence_deadline_seconds: int
    selective_hold_keys: tuple[str, ...]

    def validate(self) -> None:
        if self.contract_model != F3_ADAPTER_CONTRACT_MODEL:
            raise ValueError("prepared adapter model is invalid")
        if self.adapter_id != OPERATIONAL_ADAPTER_ID:
            raise ValueError("prepared adapter identity is invalid")
        if self.operation not in SUPPORTED_OPERATIONS:
            raise ValueError("prepared operation is unsupported")
        _require_identifier(self.target.target_type, field_name="target_type")
        _require_identifier(self.target.target_id, field_name="target_id")
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
        for name in (
            "plan_id",
            "public_task_id",
            "child_execution_id",
        ):
            _require_identifier(getattr(self, name), field_name=name)
        if self.plan_contract_version != OPERATIONAL_PLAN_CONTRACT_VERSION:
            raise ValueError("operational plan contract is unsupported")
        _parse_aware(self.plan_expires_at, field_name="plan_expires_at")
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
        if len(self.selective_hold_keys) != 1:
            raise ValueError("exactly one affected-resource hold is required")
        if not 60 <= self.evidence_deadline_seconds <= 86_400:
            raise ValueError("evidence deadline is invalid")

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
class OperationalEvidenceProjection:
    """Read-only bounded view of one authoritative durable F3 child record.

    F3-C2 supplies only this type and reader boundary.  F3-D must map it from
    the canonical child record and its operation-evidence namespace; JSONL,
    audit events, and provider output can never create execution authority.
    """

    source_model: str
    public_task_id: str
    child_execution_id: str
    plan_id: str
    dispatch_intent_recorded: bool
    dispatch_count: int
    intent_committed_at: str | None
    evidence_deadline: str | None
    provider_response_received: bool
    provider_operation_id: str | None = None
    provider_backup_id: str | None = None
    outage_observed: bool = False
    reconnect_observed: bool = False
    provider_readmission_observed: bool = False
    observation_attempt_count: int = 0
    verification_attempt_count: int = 0
    restart_backoff_attempt_count: int = 0
    next_eligible_observation_at: str | None = None
    manual_review_reason_code: str | None = None
    selective_hold_keys: tuple[str, ...] = ()
    jsonl_authoritative: bool = False

    def validate(self, operation: PreparedOperationalOperation) -> None:
        if self.source_model != OPERATIONAL_EVIDENCE_PROJECTION_MODEL:
            raise ValueError("operational evidence source is not authoritative")
        if (
            self.public_task_id != operation.public_task_id
            or self.child_execution_id != operation.child_execution_id
            or self.plan_id != operation.plan_id
        ):
            raise ValueError("operational evidence identity is inconsistent")
        if self.dispatch_count not in {0, 1}:
            raise ValueError("operational dispatch count is invalid")
        if self.dispatch_intent_recorded:
            if self.dispatch_count != 1:
                raise ValueError("durable intent must reserve one dispatch")
            committed = _parse_aware(
                self.intent_committed_at, field_name="intent_committed_at"
            )
            deadline = _parse_aware(
                self.evidence_deadline, field_name="evidence_deadline"
            )
            if deadline <= committed:
                raise ValueError("evidence deadline does not follow intent")
        elif any(
            value is not None
            for value in (self.intent_committed_at, self.evidence_deadline)
        ) or self.dispatch_count:
            raise ValueError("pre-intent evidence contradicts dispatch state")
        for name in (
            "observation_attempt_count",
            "verification_attempt_count",
            "restart_backoff_attempt_count",
        ):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= 32:
                raise ValueError(f"{name} is invalid")
        if self.next_eligible_observation_at is not None:
            _parse_aware(
                self.next_eligible_observation_at,
                field_name="next_eligible_observation_at",
            )
        if self.manual_review_reason_code is not None and not _EVIDENCE_CODE.fullmatch(
            self.manual_review_reason_code
        ):
            raise ValueError("manual-review reason is invalid")
        if self.selective_hold_keys not in {(), operation.selective_hold_keys}:
            raise ValueError("manual-review hold selection is invalid")
        if self.jsonl_authoritative:
            raise ValueError("JSONL evidence cannot be authoritative")


class OperationalEvidenceReader(Protocol):
    """Read-only port that F3-D must back with the authoritative child record."""

    def read(
        self, operation: PreparedOperationalOperation
    ) -> OperationalEvidenceProjection: ...


def provider_arguments(
    operation: str, target_id: str, requested_name: str
) -> dict[str, Any]:
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


__all__ = [
    "AdapterCapabilityDescriptor",
    "DispatchResult",
    "F3_ADAPTER_CONTRACT_MODEL",
    "LockMode",
    "LockRequest",
    "LockScope",
    "NormalizedOperationOutcome",
    "ObservationResult",
    "OperationTarget",
    "PreflightResult",
    "PreparedOperation",
    "RecoveryContext",
    "VerificationResult",
    "OperationalCapabilityDescriptor",
    "OperationalPreparationRequest",
    "OperationalAuthoritySnapshot",
    "PreparedOperationalOperation",
    "OperationalEvidenceProjection",
    "OperationalEvidenceReader",
]
