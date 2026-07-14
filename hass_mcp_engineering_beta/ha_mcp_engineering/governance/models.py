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
    principal_separation_enforced: bool = False
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

    @property
    def target_type(self) -> str:
        return self.target.target_type

    @property
    def target_id(self) -> str:
        return self.target.target_id

    def to_dict(self) -> dict[str, Any]:
        return json.loads(
            json.dumps(
                asdict(self),
                default=lambda value: value.value if isinstance(value, Enum) else str(value),
            )
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ChangePlan":
        data = dict(value)
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
        data["approval"] = ChangeApproval(**approval)
        data["verification"] = ChangeVerification(**data.get("verification", {}))
        data["rollback"] = ChangeRollback(**data.get("rollback", {}))
        if data.get("snapshot"):
            data["snapshot"] = ChangeSnapshot(**data["snapshot"])
        data["events"] = [ChangeEvent(**item) for item in data.get("events", [])]
        return cls(**data)
