"""Typed change-plan domain models and stable lifecycle values."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
from typing import Any


class PlanStatus(str, Enum):
    DRAFT = "draft"
    VALIDATION_FAILED = "validation_failed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    APPLYING = "applying"
    APPLIED = "applied"
    VERIFICATION_FAILED = "verification_failed"
    FAILED = "failed"
    ROLLBACK_PENDING = "rollback_pending"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_FAILED = "rollback_failed"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class ChangeOperation(str, Enum):
    CREATE_AUTOMATION = "create_automation"
    UPDATE_AUTOMATION = "update_automation"
    CONFIGURATION_PLAN = "configuration_plan"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalState(str, Enum):
    REQUIRED = "required"
    EXTERNAL_PENDING = "external_pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"


class StepExecutionStatus(str, Enum):
    PENDING = "pending"
    APPLYING = "applying"
    APPLIED_VERIFIED = "applied_verified"
    FAILED = "failed"
    VERIFICATION_FAILED = "verification_failed"
    NOT_ATTEMPTED_DEPENDENCY_FAILURE = "not_attempted_dependency_failure"


@dataclass
class ChangeTarget:
    target_type: str
    target_id: str


@dataclass
class ChangeRiskAssessment:
    level: RiskLevel
    reasons: list[str] = field(default_factory=list)
    apply_allowed: bool = True
    evidence: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ChangeApproval:
    state: ApprovalState = ApprovalState.REQUIRED
    authority_version: int = 2
    channel: str | None = None
    approver_principal: str | None = None
    # None means no external approver exists yet, so separation has not been
    # evaluated. False is reserved for a completed evaluation that failed.
    principal_separation_enforced: bool | None = None
    approved_at: str | None = None
    approving_caller_id: str | None = None
    approval_note: str | None = None
    bound_plan_hash: str | None = None
    consumed_at: str | None = None
    approval_kind: str = "apply"
    approval_expires_at: str | None = None
    challenge_id: str | None = None
    challenge_requested_at: str | None = None
    challenge_expires_at: str | None = None
    challenge_plan_version: int | None = None
    challenge_target_type: str | None = None
    challenge_target_id: str | None = None
    challenge_operation: str | None = None
    challenge_risk_level: str | None = None
    request_note: str | None = None
    csrf_digest: str | None = None
    csrf_issued_at: str | None = None


@dataclass
class ChangeSnapshot:
    captured_at: str
    config: dict[str, Any] | None
    fingerprint: str


@dataclass
class ChangeVerification:
    status: str = "not_run"
    checked_at: str | None = None
    desired_fingerprint: str | None = None
    actual_fingerprint: str | None = None
    config_check_status: str | None = None
    mismatch_fields: list[str] = field(default_factory=list)
    duration_ms: float | None = None


@dataclass
class ChangeRollback:
    available: bool = False
    status: str = "unavailable"
    requested_at: str | None = None
    approved_at: str | None = None
    rolled_back_at: str | None = None
    request_id: str | None = None
    expected_current_fingerprint: str | None = None
    failure_code: str | None = None


@dataclass
class ChangeEvent:
    event: str
    timestamp: str
    request_id: str
    caller_id: str
    result_status: str
    error_code: str | None = None
    duration_ms: float | None = None
    operation_id: str | None = None
    operation_order: int | None = None
    resource_type: str | None = None
    resource_id: str | None = None


@dataclass
class ConfigurationOperation:
    operation_id: str
    order: int
    depends_on: list[str]
    resource_type: str
    action: str
    target_id: str
    helper_type: str | None
    proposed_config: dict[str, Any]
    current_config: dict[str, Any] | None
    normalized_proposed_config: dict[str, Any]
    normalized_current_config: dict[str, Any] | None
    current_state_fingerprint: str
    proposed_config_hash: str
    normalization_version: int
    risk: ChangeRiskAssessment
    warnings: list[str] = field(default_factory=list)
    validation_results: dict[str, Any] = field(default_factory=dict)
    dry_run_results: dict[str, Any] = field(default_factory=dict)
    execution_status: StepExecutionStatus = StepExecutionStatus.PENDING
    execution_receipt: dict[str, Any] | None = None
    snapshot: ChangeSnapshot | None = None
    verification: ChangeVerification = field(default_factory=ChangeVerification)
    post_apply_fingerprint: str | None = None
    failure_information: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ConfigurationOperation":
        data = dict(value)
        risk = data["risk"]
        data["risk"] = ChangeRiskAssessment(
            level=RiskLevel(risk["level"]),
            reasons=list(risk.get("reasons", [])),
            apply_allowed=bool(risk.get("apply_allowed", True)),
            evidence=list(risk.get("evidence", [])),
            warnings=list(risk.get("warnings", [])),
        )
        data["execution_status"] = StepExecutionStatus(
            data.get("execution_status", StepExecutionStatus.PENDING.value)
        )
        if data.get("snapshot"):
            data["snapshot"] = ChangeSnapshot(**data["snapshot"])
        data["verification"] = ChangeVerification(**data.get("verification", {}))
        data.setdefault("execution_receipt", None)
        data.setdefault("post_apply_fingerprint", None)
        data.setdefault("failure_information", None)
        return cls(**data)


@dataclass
class ChangePlan:
    plan_id: str
    plan_version: int
    created_at: str
    updated_at: str
    expires_at: str
    status: PlanStatus
    title: str
    description: str
    requested_by: str
    target: ChangeTarget
    operation: ChangeOperation
    proposed_config: dict[str, Any]
    current_config: dict[str, Any] | None
    normalized_proposed_config: dict[str, Any]
    normalized_current_config: dict[str, Any] | None
    current_state_fingerprint: str
    proposed_config_hash: str
    risk: ChangeRiskAssessment
    normalization_version: int = 2
    warnings: list[str] = field(default_factory=list)
    validation_results: dict[str, Any] = field(default_factory=dict)
    dry_run_results: dict[str, Any] = field(default_factory=dict)
    approval: ChangeApproval = field(default_factory=ChangeApproval)
    applied_at: str | None = None
    apply_request_id: str | None = None
    post_apply_fingerprint: str | None = None
    verification: ChangeVerification = field(default_factory=ChangeVerification)
    snapshot: ChangeSnapshot | None = None
    rollback: ChangeRollback = field(default_factory=ChangeRollback)
    failure_information: dict[str, Any] | None = None
    caller_context: dict[str, Any] = field(default_factory=dict)
    events: list[ChangeEvent] = field(default_factory=list)
    contract_version: int = 1
    operations: list[ConfigurationOperation] = field(default_factory=list)
    execution_outcome: str | None = None
    configuration_check_status: str | None = None

    @property
    def target_type(self) -> str:
        return self.target.target_type

    @property
    def target_id(self) -> str:
        return self.target.target_id

    def to_dict(self) -> dict[str, Any]:
        value = json.loads(
            json.dumps(
                asdict(self),
                default=lambda value: value.value if isinstance(value, Enum) else str(value),
            )
        )
        for event in value.get("events", []):
            if not isinstance(event, dict):
                continue
            for key in (
                "operation_id",
                "operation_order",
                "resource_type",
                "resource_id",
            ):
                if event.get(key) is None:
                    event.pop(key, None)
        # Contract-v1 records predate ordered configuration operations. Keep
        # their persisted and public representation byte-for-byte compatible:
        # the additive fields exist only on contract-v2 records.
        if self.contract_version < 2:
            value.pop("contract_version", None)
            value.pop("operations", None)
            value.pop("execution_outcome", None)
            value.pop("configuration_check_status", None)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ChangePlan":
        data = dict(value)
        data["contract_version"] = int(data.get("contract_version", 1))
        data["operations"] = [
            ConfigurationOperation.from_dict(item)
            for item in data.get("operations", [])
        ]
        data.setdefault("execution_outcome", None)
        data.setdefault("configuration_check_status", None)
        # Records written before Beta 24 did not declare their normalization
        # contract. Keep them readable as v1 records, but governance refuses
        # to approve or apply them under the new hash semantics.
        data.setdefault("normalization_version", 1)
        data["status"] = PlanStatus(data["status"])
        data["operation"] = ChangeOperation(data["operation"])
        data["target"] = ChangeTarget(**data["target"])
        risk = data["risk"]
        data["risk"] = ChangeRiskAssessment(
            level=RiskLevel(risk["level"]),
            reasons=list(risk.get("reasons", [])),
            apply_allowed=bool(risk.get("apply_allowed", True)),
            evidence=list(risk.get("evidence", [])),
            warnings=list(risk.get("warnings", [])),
        )
        approval = data.get("approval", {})
        # Beta 24 and earlier approvals were granted by an MCP caller. They
        # remain readable history but never acquire external authority by
        # omission or deserialization.
        approval.setdefault("authority_version", 1)
        approval["state"] = ApprovalState(approval.get("state", "required"))
        if (
            approval.get("principal_separation_enforced") is False
            and not approval.get("approver_principal")
            and approval["state"] in {ApprovalState.REQUIRED, ApprovalState.EXTERNAL_PENDING}
        ):
            approval["principal_separation_enforced"] = None
        data["approval"] = ChangeApproval(**approval)
        data["verification"] = ChangeVerification(**data.get("verification", {}))
        data["rollback"] = ChangeRollback(**data.get("rollback", {}))
        if data.get("snapshot"):
            data["snapshot"] = ChangeSnapshot(**data["snapshot"])
        data["events"] = [ChangeEvent(**item) for item in data.get("events", [])]
        return cls(**data)
