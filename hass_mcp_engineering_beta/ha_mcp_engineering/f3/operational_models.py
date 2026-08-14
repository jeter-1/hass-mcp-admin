"""Closed value models for runtime-inert F3 operational adapters.

Canonical adapter objects come only from :mod:`ha_mcp_engineering.f3.contracts`.
The additional frozen objects below bind existing operational-plan evidence to
one future public-task/child-execution pair without defining a persisted schema
or an independent execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Protocol

from ..governance.helper_state import (
    HELPER_STATE_PROVIDER,
    HELPER_STATE_PROVIDER_CONTRACT,
    HELPER_STATE_PROVIDER_OPERATION,
    HELPER_STATE_PROVIDER_SLUG,
    helper_state_provider_evidence,
    validate_desired_state,
    validate_input_boolean_entity_id,
)
from ..f3_configuration.locks import resource_lock_key

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
OPERATIONAL_PREPARED_AUTHORITY_MODEL = (
    "f3-operational-prepared-authority-v1"
)

CREATE_FULL_BACKUP = "create_full_backup"
CONTROLLED_RELOAD = "controlled_reload"
RESTART_ADDON = "restart_addon"
RESTART_HOME_ASSISTANT = "restart_home_assistant"
SET_INPUT_BOOLEAN_STATE = "set_input_boolean_state"

SUPPORTED_OPERATIONS = (
    CREATE_FULL_BACKUP,
    CONTROLLED_RELOAD,
    RESTART_ADDON,
    RESTART_HOME_ASSISTANT,
    SET_INPUT_BOOLEAN_STATE,
)

CAPABILITY_IDENTITIES = {
    CREATE_FULL_BACKUP: "create_full_home_assistant_backup",
    CONTROLLED_RELOAD: "reload_home_assistant_configuration_domain",
    RESTART_ADDON: "restart_installed_home_assistant_addon",
    RESTART_HOME_ASSISTANT: "restart_home_assistant_core",
    SET_INPUT_BOOLEAN_STATE: "set_exact_input_boolean_state",
}

TARGET_TYPES = {
    CREATE_FULL_BACKUP: "backup",
    CONTROLLED_RELOAD: "reload_domain",
    RESTART_ADDON: "addon",
    RESTART_HOME_ASSISTANT: "home_assistant",
    SET_INPUT_BOOLEAN_STATE: "input_boolean",
}

TARGET_CLASSES = {
    CREATE_FULL_BACKUP: "local_full_backup",
    CONTROLLED_RELOAD: "home_assistant_configuration_domain",
    RESTART_ADDON: "installed_home_assistant_addon",
    RESTART_HOME_ASSISTANT: "home_assistant_core",
    SET_INPUT_BOOLEAN_STATE: "exact_input_boolean_entity",
}

PROVIDER_OPERATIONS = {
    CREATE_FULL_BACKUP: "ha_manage_backup",
    CONTROLLED_RELOAD: "ha_reload_core",
    RESTART_ADDON: "ha_manage_addon",
    RESTART_HOME_ASSISTANT: "ha_restart",
    SET_INPUT_BOOLEAN_STATE: HELPER_STATE_PROVIDER_OPERATION,
}

PROVIDER_IDENTITIES = {
    CREATE_FULL_BACKUP: "upstream_operational_backup",
    CONTROLLED_RELOAD: "upstream_operational_lifecycle",
    RESTART_ADDON: "upstream_operational_lifecycle",
    RESTART_HOME_ASSISTANT: "upstream_operational_lifecycle",
    SET_INPUT_BOOLEAN_STATE: HELPER_STATE_PROVIDER,
}

PROVIDER_CONTRACT_MODELS = {
    CREATE_FULL_BACKUP: OPERATIONAL_PROVIDER_CONTRACT_MODEL,
    CONTROLLED_RELOAD: OPERATIONAL_PROVIDER_CONTRACT_MODEL,
    RESTART_ADDON: OPERATIONAL_PROVIDER_CONTRACT_MODEL,
    RESTART_HOME_ASSISTANT: OPERATIONAL_PROVIDER_CONTRACT_MODEL,
    SET_INPUT_BOOLEAN_STATE: HELPER_STATE_PROVIDER_CONTRACT,
}

VERIFICATION_MODELS = {
    CREATE_FULL_BACKUP: "f3-full-backup-exact-readback-v1",
    CONTROLLED_RELOAD: "f3-controlled-reload-effect-readback-v1",
    RESTART_ADDON: "f3-addon-restart-exact-readback-v1",
    RESTART_HOME_ASSISTANT: "f3-home-assistant-restart-outage-recovery-v1",
    SET_INPUT_BOOLEAN_STATE: "f3-input-boolean-exact-state-readback-v1",
}

EVIDENCE_DEADLINE_CLASSES = {
    CREATE_FULL_BACKUP: "long_backup_evidence",
    CONTROLLED_RELOAD: "short_reload_evidence",
    RESTART_ADDON: "addon_restart_evidence",
    RESTART_HOME_ASSISTANT: "home_assistant_restart_evidence",
    SET_INPUT_BOOLEAN_STATE: "short_helper_state_evidence",
}

EXPECTED_EFFECT_CODES = {
    CREATE_FULL_BACKUP: ("full_local_backup_created",),
    CONTROLLED_RELOAD: ("configuration_domain_reloaded",),
    RESTART_ADDON: ("installed_addon_restarted",),
    RESTART_HOME_ASSISTANT: ("home_assistant_core_restarted",),
    SET_INPUT_BOOLEAN_STATE: ("input_boolean_exact_state_set",),
}

POLICY_EXPECTATIONS = {
    CREATE_FULL_BACKUP: ("standard_admin", "moderate", "indirect"),
    CONTROLLED_RELOAD: ("standard_admin", "moderate", "indirect"),
    RESTART_ADDON: ("elevated_admin", "high", "indirect"),
    RESTART_HOME_ASSISTANT: ("elevated_admin", "high", "indirect"),
    SET_INPUT_BOOLEAN_STATE: ("standard_admin", "low", "none"),
}

RISK_LEVEL_EXPECTATIONS = {
    CREATE_FULL_BACKUP: "medium",
    CONTROLLED_RELOAD: "medium",
    RESTART_ADDON: "high",
    RESTART_HOME_ASSISTANT: "high",
    SET_INPUT_BOOLEAN_STATE: "low",
}


def operational_policy_expectation_is_valid(
    operation: str,
    policy: tuple[str, str, str],
    risk_level: str,
) -> bool:
    """Validate the fixed policy families and dependency-aware helper cases."""

    if operation != SET_INPUT_BOOLEAN_STATE:
        return bool(
            policy == POLICY_EXPECTATIONS[operation]
            and risk_level == RISK_LEVEL_EXPECTATIONS[operation]
        )
    return (policy, risk_level) in {
        (("standard_admin", "low", "none"), "low"),
        (("elevated_admin", "high", "indirect"), "high"),
        (("elevated_admin", "high", "direct"), "high"),
        (("elevated_admin", "high", "safety_critical"), "high"),
    }

EVIDENCE_DEADLINE_SECONDS = {
    CREATE_FULL_BACKUP: 86_400,
    CONTROLLED_RELOAD: 900,
    RESTART_ADDON: 1_800,
    RESTART_HOME_ASSISTANT: 1_800,
    SET_INPUT_BOOLEAN_STATE: 120,
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
        if self.provider != PROVIDER_IDENTITIES[operation]:
            raise ValueError("provider identity is invalid")
        if self.provider_contract_model != PROVIDER_CONTRACT_MODELS[operation]:
            raise ValueError("provider contract is invalid")
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
    prepared_authority_model: str
    prepared_operation_hash: str
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
        validate_prepared_operational_authority(self)

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
        validate_prepared_operational_authority(operation)
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
            expected_deadline = committed + timedelta(
                seconds=operation.evidence_deadline_seconds
            )
            if deadline != expected_deadline:
                raise ValueError(
                    "evidence deadline does not match the prepared operation"
                )
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
    if operation == SET_INPUT_BOOLEAN_STATE:
        validate_input_boolean_entity_id(target_id)
        desired_state = validate_desired_state(requested_name)
        return {
            "domain": "input_boolean",
            "service": "turn_on" if desired_state == "on" else "turn_off",
            "target": {"entity_id": target_id},
        }
    raise ValueError("unknown operational operation")


def operational_escalation_policy(
    operation: str, target_id: str
) -> tuple[tuple[str, ...], int]:
    """Return the exact affected hold and non-releasing evidence threshold."""

    if operation == CREATE_FULL_BACKUP:
        if target_id != "local_full_backup":
            raise ValueError("backup target is invalid")
        keys = ("backup:local_full_backup",)
    elif operation == CONTROLLED_RELOAD:
        if target_id not in RELOAD_PROVIDER_TARGETS:
            raise ValueError("reload target is invalid")
        keys = (f"reload:{target_id}",)
    elif operation == RESTART_ADDON:
        if not _SLUG.fullmatch(target_id):
            raise ValueError("add-on target is invalid")
        keys = (f"addon:{target_id}",)
    elif operation == RESTART_HOME_ASSISTANT:
        if target_id != "core":
            raise ValueError("Home Assistant target is invalid")
        keys = ("home_assistant:core",)
    elif operation == SET_INPUT_BOOLEAN_STATE:
        validate_input_boolean_entity_id(target_id)
        keys = (resource_lock_key("input_boolean", target_id),)
    else:
        raise ValueError("unknown operational hold model")
    return keys, EVIDENCE_DEADLINE_SECONDS[operation]


def operational_prepared_authority_payload(
    operation: PreparedOperationalOperation,
) -> dict[str, Any]:
    """Build the one canonical immutable operational authority payload.

    Mutable provider results and observation, verification, or reconciliation
    counters are deliberately absent.  Both preparation and every later
    integrity check hash this exact function's result.
    """

    return {
        "authority_model": OPERATIONAL_PREPARED_AUTHORITY_MODEL,
        "adapter_contract": operation.contract_model,
        "adapter_id": operation.adapter_id,
        "capability_id": operation.capability_id,
        "operation": operation.operation,
        "target": {
            "target_type": operation.target.target_type,
            "target_id": operation.target.target_id,
            "target_class": operation.target_class,
        },
        "plan": {
            "plan_id": operation.plan_id,
            "plan_hash": operation.plan_hash,
            "plan_contract_version": operation.plan_contract_version,
            "plan_expires_at": operation.plan_expires_at,
            "public_task_id": operation.public_task_id,
            "child_execution_id": operation.child_execution_id,
        },
        "state": {
            "current_state_fingerprint": operation.current_state_fingerprint,
            "normalized_proposed_hash": operation.normalized_proposed_hash,
        },
        "authorization": {
            "risk_level": operation.risk_level,
            "policy_class": operation.policy_class,
            "risk_delta": operation.risk_delta,
            "physical_consequence": operation.physical_consequence,
            "policy_decision_hash": operation.policy_decision_hash,
            "approval_bundle_hash": operation.approval_bundle_hash,
        },
        "provider": {
            "provider_id": operation.provider_id,
            "provider_contract": operation.provider_contract_model,
            "provider_operation": operation.provider_operation,
            "provider_arguments": operation.provider_arguments,
            "provider_arguments_hash": operation.provider_arguments_hash,
            "provider_evidence": operation.provider_evidence,
            "authoritative_provider_slug": (
                operation.authoritative_provider_slug
            ),
            "provider_identity_evidence_hash": (
                operation.provider_identity_evidence_hash
            ),
        },
        "requested_name": operation.requested_name,
        "baseline": operation.baseline,
        "reporting": {
            "expected_effect_codes": list(operation.expected_effects),
            "expected_effect_descriptions": list(
                operation.expected_effect_descriptions
            ),
            "warnings": list(operation.warnings),
            "limitations": list(operation.limitations),
        },
        "verification": {
            "model": operation.verification_contract_model,
            "contract": decoded_object(
                operation.verification_contract_json,
                field_name="verification_contract",
            ),
            "contract_hash": operation.verification_contract_hash,
        },
        "rollback_available": operation.rollback_available,
        "recovery": {
            "evidence_deadline_class": operation.evidence_deadline_class,
            "evidence_deadline_seconds": operation.evidence_deadline_seconds,
            "selective_hold_keys": list(operation.selective_hold_keys),
        },
    }


def recompute_operational_prepared_hash(
    operation: PreparedOperationalOperation,
) -> str:
    """Recompute the canonical prepared authority checksum."""

    return stable_hash(operational_prepared_authority_payload(operation))


def _require_text_tuple(value: Any, *, field_name: str) -> None:
    if not isinstance(value, tuple) or any(
        not isinstance(item, str) for item in value
    ):
        raise ValueError(f"{field_name} is invalid")


def validate_prepared_operational_authority(
    operation: PreparedOperationalOperation,
) -> None:
    """Fail closed unless every immutable operational authority field agrees."""

    if not isinstance(operation, PreparedOperationalOperation):
        raise ValueError("prepared operation type is invalid")
    if operation.operation not in SUPPORTED_OPERATIONS:
        raise ValueError("prepared operation is unsupported")
    expected_operation = operation.operation
    if operation.contract_model != F3_ADAPTER_CONTRACT_MODEL:
        raise ValueError("prepared adapter model is invalid")
    if operation.adapter_id != OPERATIONAL_ADAPTER_ID:
        raise ValueError("prepared adapter identity is invalid")
    if not isinstance(operation.target, OperationTarget):
        raise ValueError("prepared target type is invalid")
    _require_identifier(operation.target.target_type, field_name="target_type")
    _require_identifier(operation.target.target_id, field_name="target_id")
    if operation.target.target_type != TARGET_TYPES[expected_operation]:
        raise ValueError("prepared target type is invalid")
    if operation.capability_id != CAPABILITY_IDENTITIES[expected_operation]:
        raise ValueError("prepared capability identity is invalid")
    if operation.target_class != TARGET_CLASSES[expected_operation]:
        raise ValueError("prepared target class is invalid")
    operational_escalation_policy(
        expected_operation, operation.target.target_id
    )

    for name in (
        "current_state_fingerprint",
        "normalized_proposed_hash",
        "prepared_operation_hash",
        "plan_hash",
        "policy_decision_hash",
        "approval_bundle_hash",
        "verification_contract_hash",
        "provider_arguments_hash",
        "provider_identity_evidence_hash",
    ):
        _require_sha256(getattr(operation, name), field_name=name)
    for name in ("plan_id", "public_task_id", "child_execution_id"):
        _require_identifier(getattr(operation, name), field_name=name)
    if (
        type(operation.plan_contract_version) is not int
        or operation.plan_contract_version != OPERATIONAL_PLAN_CONTRACT_VERSION
    ):
        raise ValueError("operational plan contract is unsupported")
    _parse_aware(operation.plan_expires_at, field_name="plan_expires_at")

    if not operational_policy_expectation_is_valid(
        expected_operation,
        (
            operation.policy_class,
            operation.risk_delta,
            operation.physical_consequence,
        ),
        operation.risk_level,
    ):
        raise ValueError("prepared policy expectation is invalid")

    if operation.provider_id != PROVIDER_IDENTITIES[expected_operation]:
        raise ValueError("prepared provider identity is invalid")
    if operation.provider_contract_model != PROVIDER_CONTRACT_MODELS[
        expected_operation
    ]:
        raise ValueError("prepared provider contract is invalid")
    if operation.provider_operation != PROVIDER_OPERATIONS[expected_operation]:
        raise ValueError("prepared provider operation is invalid")
    if not _SLUG.fullmatch(operation.authoritative_provider_slug):
        raise ValueError("prepared provider slug is invalid")
    if not isinstance(operation.requested_name, str) or len(
        operation.requested_name
    ) > 255:
        raise ValueError("prepared requested name is invalid")

    expected_arguments = provider_arguments(
        expected_operation,
        operation.target.target_id,
        operation.requested_name,
    )
    decoded_arguments = decoded_object(
        operation.provider_arguments_json, field_name="provider_arguments"
    )
    if decoded_arguments != expected_arguments or (
        canonical_json(expected_arguments) != operation.provider_arguments_json
    ):
        raise ValueError("prepared provider arguments are invalid")
    if stable_hash(operation.provider_arguments_json) != (
        operation.provider_arguments_hash
    ):
        raise ValueError("provider argument hash is invalid")
    provider_evidence = decoded_object(
        operation.provider_evidence_json, field_name="provider_evidence"
    )
    if provider_evidence.get("provider") != operation.provider_id:
        raise ValueError("prepared provider evidence is invalid")
    if expected_operation == SET_INPUT_BOOLEAN_STATE and (
        operation.authoritative_provider_slug != HELPER_STATE_PROVIDER_SLUG
        or provider_evidence != helper_state_provider_evidence()
        or operation.provider_identity_evidence_hash
        != stable_hash(provider_evidence)
    ):
        raise ValueError("prepared direct provider evidence is invalid")
    decoded_object(operation.baseline_json, field_name="baseline")

    if operation.expected_effects != EXPECTED_EFFECT_CODES[expected_operation]:
        raise ValueError("prepared effect codes are invalid")
    for name in (
        "expected_effect_descriptions",
        "warnings",
        "limitations",
    ):
        _require_text_tuple(getattr(operation, name), field_name=name)

    if operation.verification_contract_model != VERIFICATION_MODELS[
        expected_operation
    ]:
        raise ValueError("prepared verification model is invalid")
    verification = decoded_object(
        operation.verification_contract_json,
        field_name="verification_contract",
    )
    if (
        verification.get("version") != 1
        or verification.get("no_blind_redispatch") is not True
        or not isinstance(verification.get("required"), list)
        or not verification["required"]
        or (
            "operation" in verification
            and verification["operation"] != expected_operation
        )
    ):
        raise ValueError("prepared verification contract is invalid")
    if stable_hash(operation.verification_contract_json) != (
        operation.verification_contract_hash
    ):
        raise ValueError("verification contract hash is invalid")

    hold_keys, evidence_seconds = operational_escalation_policy(
        expected_operation, operation.target.target_id
    )
    if operation.evidence_deadline_class != EVIDENCE_DEADLINE_CLASSES[
        expected_operation
    ]:
        raise ValueError("evidence deadline class is invalid")
    if (
        type(operation.evidence_deadline_seconds) is not int
        or operation.evidence_deadline_seconds != evidence_seconds
    ):
        raise ValueError("evidence deadline is invalid")
    if operation.selective_hold_keys != hold_keys:
        raise ValueError("manual-review hold selection is invalid")
    if operation.rollback_available is not False:
        raise ValueError("operational rollback is not available")

    if recompute_operational_prepared_hash(operation) != (
        operation.prepared_operation_hash
    ):
        raise ValueError("prepared operation hash is invalid")


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
    "OPERATIONAL_PREPARED_AUTHORITY_MODEL",
    "operational_prepared_authority_payload",
    "recompute_operational_prepared_hash",
    "validate_prepared_operational_authority",
    "operational_escalation_policy",
    "operational_policy_expectation_is_valid",
]
