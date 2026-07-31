"""Typed change-plan domain models and stable lifecycle values."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import re
from typing import Any


class PlanStatus(str, Enum):
    DRAFT = "draft"
    VALIDATION_FAILED = "validation_failed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    APPLYING = "applying"
    VERIFICATION_REQUIRED = "verification_required"
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
    CREATE_FULL_BACKUP = "create_full_backup"
    CONTROLLED_RELOAD = "controlled_reload"
    RESTART_ADDON = "restart_addon"
    RESTART_HOME_ASSISTANT = "restart_home_assistant"


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


class ApprovalPolicyClass(str, Enum):
    STANDARD_ADMIN = "standard_admin"
    ELEVATED_ADMIN = "elevated_admin"
    PROHIBITED = "prohibited"


class RiskDelta(str, Enum):
    NONE = "none"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class PhysicalConsequence(str, Enum):
    NONE = "none"
    INDIRECT = "indirect"
    DIRECT = "direct"
    SAFETY_CRITICAL = "safety_critical"


class ApprovalActionKind(str, Enum):
    PLAN_APPROVAL = "plan_approval"
    ELEVATED_RISK_ACKNOWLEDGEMENT = (
        "elevated_risk_acknowledgement"
    )


_POLICY_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{0,95}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class ChangePolicyDecision:
    """Immutable, canonical policy authority bound to one plan snapshot."""

    policy_version: str
    policy_class: ApprovalPolicyClass
    risk_delta: RiskDelta
    physical_consequence: PhysicalConsequence
    reason_codes: tuple[str, ...]
    required_acknowledgements: tuple[ApprovalActionKind, ...]
    policy_subject_hash: str
    policy_decision_hash: str

    def __post_init__(self) -> None:
        if self.policy_version != "f2-v1":
            raise ValueError("unsupported policy version")
        if (
            not self.reason_codes
            or tuple(sorted(set(self.reason_codes))) != self.reason_codes
            or any(
                not _POLICY_REASON_CODE.fullmatch(code)
                for code in self.reason_codes
            )
        ):
            raise ValueError("invalid policy reason codes")
        if len(self.reason_codes) > 32:
            raise ValueError("policy reason-code limit exceeded")
        acknowledgement_values = tuple(
            value.value for value in self.required_acknowledgements
        )
        if len(set(acknowledgement_values)) != len(
            acknowledgement_values
        ):
            raise ValueError("duplicate policy acknowledgement")
        if not _SHA256.fullmatch(self.policy_subject_hash):
            raise ValueError("invalid policy subject hash")
        if not _SHA256.fullmatch(self.policy_decision_hash):
            raise ValueError("invalid policy decision hash")

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "policy_class": self.policy_class.value,
            "risk_delta": self.risk_delta.value,
            "physical_consequence": self.physical_consequence.value,
            "reason_codes": list(self.reason_codes),
            "required_acknowledgements": [
                value.value for value in self.required_acknowledgements
            ],
            "policy_subject_hash": self.policy_subject_hash,
            "policy_decision_hash": self.policy_decision_hash,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ChangePolicyDecision":
        if not isinstance(value, dict):
            raise ValueError("invalid policy decision")
        expected = {
            "policy_version",
            "policy_class",
            "risk_delta",
            "physical_consequence",
            "reason_codes",
            "required_acknowledgements",
            "policy_subject_hash",
            "policy_decision_hash",
        }
        if set(value) != expected:
            raise ValueError("invalid policy decision fields")
        reasons = value["reason_codes"]
        acknowledgements = value["required_acknowledgements"]
        if not isinstance(reasons, list) or not isinstance(
            acknowledgements, list
        ):
            raise ValueError("invalid policy decision collections")
        return cls(
            policy_version=str(value["policy_version"]),
            policy_class=ApprovalPolicyClass(value["policy_class"]),
            risk_delta=RiskDelta(value["risk_delta"]),
            physical_consequence=PhysicalConsequence(
                value["physical_consequence"]
            ),
            reason_codes=tuple(str(item) for item in reasons),
            required_acknowledgements=tuple(
                ApprovalActionKind(item) for item in acknowledgements
            ),
            policy_subject_hash=str(value["policy_subject_hash"]),
            policy_decision_hash=str(value["policy_decision_hash"]),
        )


@dataclass
class ApprovalActionRecord:
    """One separately actionable, hash-bound administrator decision."""

    kind: ApprovalActionKind
    state: ApprovalState = ApprovalState.REQUIRED
    challenge_id: str | None = None
    challenge_requested_at: str | None = None
    challenge_expires_at: str | None = None
    granted_at: str | None = None
    approver_principal: str | None = None
    consumed_at: str | None = None
    csrf_digest: str | None = None
    csrf_issued_at: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ApprovalActionRecord":
        data = dict(value)
        data["kind"] = ApprovalActionKind(data["kind"])
        data["state"] = ApprovalState(
            data.get("state", ApprovalState.REQUIRED.value)
        )
        return cls(**data)


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
    authority_version: int = 3
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
    policy_decision_hash: str | None = None
    policy_class: str | None = None
    bundle_state: str | None = None
    same_principal_confirmed: bool | None = None
    elevated_risk_acknowledgement: ApprovalActionRecord | None = None


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
class RecoveryVerification:
    """Bounded operation verification that can resume without redispatch."""

    contract_version: int = 1
    status: str = "not_run"
    attempt_count: int = 0
    checked_at: str | None = None
    operation_completed: bool | None = None
    inventory_readable: bool | None = None
    archive_integrity_validated: bool = False
    mismatch_fields: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class OperationalPlanDetails:
    """Versioned, operation-specific administrative plan evidence."""

    schema_version: int
    family: str
    operation: str
    requested_name: str
    provider: str
    provider_capability_evidence: dict[str, Any]
    expected_effects: list[str]
    preconditions: list[str]
    verification_contract: dict[str, Any]
    baseline: dict[str, Any]
    dispatch: dict[str, Any] = field(default_factory=dict)
    verification: RecoveryVerification = field(
        default_factory=RecoveryVerification
    )
    final_outcome: str | None = None
    limitations: list[str] = field(default_factory=list)
    rollback_available: bool = False

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "OperationalPlanDetails":
        data = dict(value)
        data["verification"] = RecoveryVerification(
            **data.get("verification", {})
        )
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
    policy_decision: ChangePolicyDecision | None = None
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
    plan_family: str = "configuration_change"
    operational: OperationalPlanDetails | None = None

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
        if self.policy_decision is None:
            value.pop("policy_decision", None)
        approval = value.get("approval")
        if isinstance(approval, dict):
            for key in (
                "policy_decision_hash",
                "policy_class",
                "bundle_state",
                "same_principal_confirmed",
                "elevated_risk_acknowledgement",
            ):
                if approval.get(key) is None:
                    approval.pop(key, None)
        # Contract-v1 records predate ordered configuration operations. Keep
        # their persisted and public representation byte-for-byte compatible:
        # the additive fields exist only on contract-v2 records.
        if self.contract_version < 2:
            value.pop("contract_version", None)
            value.pop("operations", None)
            value.pop("execution_outcome", None)
            value.pop("configuration_check_status", None)
            value.pop("plan_family", None)
            value.pop("operational", None)
        elif self.contract_version == 2:
            # Contract-v2 records predate operational administration. Preserve
            # their exact persisted representation.
            value.pop("plan_family", None)
            value.pop("operational", None)
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
        data.setdefault("plan_family", "configuration_change")
        data["policy_decision"] = (
            ChangePolicyDecision.from_dict(data["policy_decision"])
            if isinstance(data.get("policy_decision"), dict)
            else None
        )
        data["operational"] = (
            OperationalPlanDetails.from_dict(data["operational"])
            if isinstance(data.get("operational"), dict)
            else None
        )
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
        approval["elevated_risk_acknowledgement"] = (
            ApprovalActionRecord.from_dict(
                approval["elevated_risk_acknowledgement"]
            )
            if isinstance(
                approval.get("elevated_risk_acknowledgement"), dict
            )
            else None
        )
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
