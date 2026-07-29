"""Runtime-inert immutable update and recovery preflight contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, TypeVar


EnumValue = TypeVar("EnumValue", bound=Enum)


class TargetType(str, Enum):
    """Future update target classes recognized by the decision model."""

    HOME_ASSISTANT_CORE = "home_assistant_core"
    SUPERVISOR = "supervisor"
    HOME_ASSISTANT_OS = "home_assistant_os"
    ADDON_APP = "addon_app"
    HACS_INTEGRATION = "hacs_integration"
    HACS_FRONTEND_COMPONENT = "hacs_frontend_component"
    ENGINEERING_MCP_SERVER = "engineering_mcp_server"
    UPSTREAM_HA_MCP = "upstream_ha_mcp"
    FIRMWARE_UPDATE_ENTITY = "firmware_update_entity"


class CompatibilityStatus(str, Enum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


class BackupStatus(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    NOT_REQUIRED = "not_required"


class BackupLocationStatus(str, Enum):
    VERIFIED = "verified"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class BackupRequirement(str, Enum):
    REQUIRED = "required"
    NOT_REQUIRED = "not_required"


class StaleBackupDisposition(str, Enum):
    BLOCK = "block"
    MANUAL_REVIEW = "manual_review"


class RecoveryRequirement(str, Enum):
    REQUIRED = "required"
    MANUAL_REVIEW_IF_UNAVAILABLE = "manual_review_if_unavailable"


class IssueSeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ExpectedDisruption(str, Enum):
    NONE = "none"
    RELOAD = "reload"
    RESTART = "restart"
    REBOOT = "reboot"
    TEMPORARY_OFFLINE = "temporary_offline"
    UNKNOWN = "unknown"


class PostUpdateVerificationProfile(str, Enum):
    HOME_ASSISTANT_CORE = "home_assistant_core"
    SUPERVISOR = "supervisor"
    HOME_ASSISTANT_OS = "home_assistant_os"
    ADDON_APP = "addon_app"
    HACS = "hacs"
    ENGINEERING_MCP_SERVER = "engineering_mcp_server"
    UPSTREAM_HA_MCP = "upstream_ha_mcp"
    FIRMWARE = "firmware"


class PreflightVerdict(str, Enum):
    READY_FOR_GOVERNED_PLANNING = "ready_for_governed_planning"
    BLOCKED = "blocked"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, order=True)
class EvidenceReference:
    """Bounded reference to already-collected evidence."""

    evidence_id: str
    source: str
    summary: str
    authoritative: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _required_text(self.evidence_id, 128))
        object.__setattr__(self, "source", _required_text(self.source, 128))
        object.__setattr__(self, "summary", _required_text(self.summary, 400))
        _require_bool(self.authoritative, field_name="authoritative")

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source": self.source,
            "summary": self.summary,
            "authoritative": self.authoritative,
        }


@dataclass(frozen=True, order=True)
class UpdateRiskIssue:
    """One already-observed repair or error relevant to an update."""

    issue_id: str
    severity: IssueSeverity
    summary: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "issue_id", _required_text(self.issue_id, 128))
        object.__setattr__(
            self,
            "severity",
            _coerce_enum(IssueSeverity, self.severity, field_name="severity"),
        )
        object.__setattr__(self, "summary", _required_text(self.summary, 400))


@dataclass(frozen=True)
class TargetPolicy:
    """Explicit policy for one supported target class."""

    target_type: TargetType
    backup_requirement: BackupRequirement
    max_backup_age_hours: float | None
    stale_backup_disposition: StaleBackupDisposition
    recovery_requirement: RecoveryRequirement
    power_stability_required: bool
    verification_profiles: tuple[PostUpdateVerificationProfile, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "target_type",
            _coerce_enum(TargetType, self.target_type, field_name="target_type"),
        )
        object.__setattr__(
            self,
            "backup_requirement",
            _coerce_enum(
                BackupRequirement,
                self.backup_requirement,
                field_name="backup_requirement",
            ),
        )
        object.__setattr__(
            self,
            "stale_backup_disposition",
            _coerce_enum(
                StaleBackupDisposition,
                self.stale_backup_disposition,
                field_name="stale_backup_disposition",
            ),
        )
        object.__setattr__(
            self,
            "recovery_requirement",
            _coerce_enum(
                RecoveryRequirement,
                self.recovery_requirement,
                field_name="recovery_requirement",
            ),
        )
        _require_bool(
            self.power_stability_required,
            field_name="power_stability_required",
        )
        if self.max_backup_age_hours is not None:
            object.__setattr__(
                self,
                "max_backup_age_hours",
                _non_negative_number(
                    self.max_backup_age_hours,
                    field_name="max_backup_age_hours",
                    allow_zero=False,
                ),
            )
        profiles = tuple(
            sorted(
                {
                    _coerce_enum(
                        PostUpdateVerificationProfile,
                        item,
                        field_name="verification_profiles",
                    )
                    for item in self.verification_profiles
                },
                key=lambda item: item.value,
            )
        )
        if not profiles:
            raise ValueError("verification_profiles must not be empty")
        object.__setattr__(self, "verification_profiles", profiles)


@dataclass(frozen=True)
class UpdateRecoveryPolicy:
    """Closed, deterministic collection of target policies."""

    policy_id: str
    target_policies: tuple[TargetPolicy, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _required_text(self.policy_id, 128))
        policies = tuple(self.target_policies)
        if any(not isinstance(item, TargetPolicy) for item in policies):
            raise TypeError("target_policies must contain TargetPolicy values")
        ordered = tuple(
            sorted(policies, key=lambda item: item.target_type.value)
        )
        target_types = [item.target_type for item in ordered]
        if len(target_types) != len(set(target_types)):
            raise ValueError("target_policies contains a duplicate target_type")
        object.__setattr__(self, "target_policies", ordered)

    def for_target(self, target_type: str) -> TargetPolicy | None:
        return next(
            (
                item
                for item in self.target_policies
                if item.target_type.value == target_type
            ),
            None,
        )


@dataclass(frozen=True)
class UpdatePreflightEvidence:
    """Already-collected evidence consumed by the pure evaluator."""

    target_type: TargetType | str
    target_id: str
    installed_version: str | None
    candidate_version: str | None
    compatibility_status: CompatibilityStatus
    candidate_version_evidence: EvidenceReference | None = None
    compatibility_evidence: tuple[EvidenceReference, ...] = ()
    current_repairs: tuple[UpdateRiskIssue, ...] = ()
    current_errors: tuple[UpdateRiskIssue, ...] = ()
    backup_status: BackupStatus = BackupStatus.UNKNOWN
    backup_age_hours: float | None = None
    backup_location_status: BackupLocationStatus = BackupLocationStatus.UNKNOWN
    free_storage: float | None = None
    required_storage: float | None = None
    power_stability_known: bool = False
    power_stable: bool | None = None
    rollback_available: bool | None = None
    restore_available: bool | None = None
    expected_disruption: ExpectedDisruption = ExpectedDisruption.UNKNOWN
    post_update_verification_profile: PostUpdateVerificationProfile | None = None

    def __post_init__(self) -> None:
        if isinstance(self.target_type, TargetType):
            normalized_target_type: TargetType | str = self.target_type
        else:
            normalized_target_type = (
                _optional_text(self.target_type, 128) or ""
            ).lower()
        object.__setattr__(self, "target_type", normalized_target_type)
        object.__setattr__(
            self,
            "target_id",
            _optional_text(self.target_id, 256) or "",
        )
        object.__setattr__(
            self,
            "installed_version",
            _optional_text(self.installed_version, 128),
        )
        object.__setattr__(
            self,
            "candidate_version",
            _optional_text(self.candidate_version, 128),
        )
        object.__setattr__(
            self,
            "compatibility_status",
            _coerce_enum(
                CompatibilityStatus,
                self.compatibility_status,
                field_name="compatibility_status",
            ),
        )
        if (
            self.candidate_version_evidence is not None
            and not isinstance(self.candidate_version_evidence, EvidenceReference)
        ):
            raise TypeError(
                "candidate_version_evidence must be an EvidenceReference or None"
            )
        compatibility_evidence = tuple(self.compatibility_evidence)
        if any(
            not isinstance(item, EvidenceReference)
            for item in compatibility_evidence
        ):
            raise TypeError(
                "compatibility_evidence must contain EvidenceReference values"
            )
        object.__setattr__(
            self,
            "compatibility_evidence",
            compatibility_evidence,
        )
        current_repairs = tuple(self.current_repairs)
        current_errors = tuple(self.current_errors)
        if any(not isinstance(item, UpdateRiskIssue) for item in current_repairs):
            raise TypeError("current_repairs must contain UpdateRiskIssue values")
        if any(not isinstance(item, UpdateRiskIssue) for item in current_errors):
            raise TypeError("current_errors must contain UpdateRiskIssue values")
        object.__setattr__(self, "current_repairs", current_repairs)
        object.__setattr__(self, "current_errors", current_errors)
        object.__setattr__(
            self,
            "backup_status",
            _coerce_enum(BackupStatus, self.backup_status, field_name="backup_status"),
        )
        object.__setattr__(
            self,
            "backup_location_status",
            _coerce_enum(
                BackupLocationStatus,
                self.backup_location_status,
                field_name="backup_location_status",
            ),
        )
        if self.backup_age_hours is not None:
            object.__setattr__(
                self,
                "backup_age_hours",
                _non_negative_number(
                    self.backup_age_hours,
                    field_name="backup_age_hours",
                ),
            )
        if self.free_storage is not None:
            object.__setattr__(
                self,
                "free_storage",
                _non_negative_number(self.free_storage, field_name="free_storage"),
            )
        if self.required_storage is not None:
            object.__setattr__(
                self,
                "required_storage",
                _non_negative_number(
                    self.required_storage,
                    field_name="required_storage",
                ),
            )
        _require_bool(
            self.power_stability_known,
            field_name="power_stability_known",
        )
        _require_optional_bool(self.power_stable, field_name="power_stable")
        _require_optional_bool(
            self.rollback_available,
            field_name="rollback_available",
        )
        _require_optional_bool(
            self.restore_available,
            field_name="restore_available",
        )
        if self.power_stability_known and self.power_stable is None:
            raise ValueError(
                "power_stable must be supplied when power_stability_known is true"
            )
        if not self.power_stability_known and self.power_stable is not None:
            raise ValueError(
                "power_stable must be unknown when power_stability_known is false"
            )
        object.__setattr__(
            self,
            "expected_disruption",
            _coerce_enum(
                ExpectedDisruption,
                self.expected_disruption,
                field_name="expected_disruption",
            ),
        )
        if self.post_update_verification_profile is not None:
            object.__setattr__(
                self,
                "post_update_verification_profile",
                _coerce_enum(
                    PostUpdateVerificationProfile,
                    self.post_update_verification_profile,
                    field_name="post_update_verification_profile",
                ),
            )

    @property
    def normalized_target_type(self) -> str:
        value = (
            self.target_type.value
            if isinstance(self.target_type, TargetType)
            else str(self.target_type)
        )
        return value.strip().lower()


@dataclass(frozen=True, order=True)
class PreflightFinding:
    """One deterministic blocker, warning, or unknown."""

    code: str
    field: str
    summary: str
    evidence_references: tuple[str, ...] = ()
    requires_manual_review: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _required_text(self.code, 128))
        object.__setattr__(self, "field", _required_text(self.field, 128))
        object.__setattr__(self, "summary", _required_text(self.summary, 500))
        object.__setattr__(
            self,
            "evidence_references",
            tuple(
                sorted(
                    {
                        _required_text(item, 128)
                        for item in self.evidence_references
                    }
                )
            ),
        )
        _require_bool(
            self.requires_manual_review,
            field_name="requires_manual_review",
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "field": self.field,
            "summary": self.summary,
            "evidence_references": list(self.evidence_references),
            "requires_manual_review": self.requires_manual_review,
        }


@dataclass(frozen=True)
class UpdatePreflightAssessment:
    """Pure decision result; it carries no executable operation."""

    policy_id: str
    target_type: str
    target_id: str
    installed_version: str | None
    candidate_version: str | None
    verdict: PreflightVerdict
    blockers: tuple[PreflightFinding, ...]
    warnings: tuple[PreflightFinding, ...]
    unknowns: tuple[PreflightFinding, ...]
    assessment_fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "installed_version": self.installed_version,
            "candidate_version": self.candidate_version,
            "verdict": self.verdict.value,
            "blockers": [item.as_dict() for item in self.blockers],
            "warnings": [item.as_dict() for item in self.warnings],
            "unknowns": [item.as_dict() for item in self.unknowns],
            "assessment_fingerprint": self.assessment_fingerprint,
        }


def _required_text(value: object, limit: int) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError("required text must not be empty")
    return normalized[:limit]


def _optional_text(value: object | None, limit: int) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized[:limit] or None


def _non_negative_number(
    value: float,
    *,
    field_name: str,
    allow_zero: bool = True,
) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric, not boolean")
    number = float(value)
    if not math.isfinite(number) or number < 0 or (not allow_zero and number == 0):
        qualifier = "positive" if not allow_zero else "non-negative"
        raise ValueError(f"{field_name} must be a finite {qualifier} number")
    return number


def _coerce_enum(
    enum_type: type[EnumValue],
    value: object,
    *,
    field_name: str,
) -> EnumValue:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        raise ValueError(
            f"{field_name} is not a supported {enum_type.__name__} value"
        ) from exc


def _require_bool(value: object, *, field_name: str) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be boolean")


def _require_optional_bool(value: object | None, *, field_name: str) -> None:
    if value is not None:
        _require_bool(value, field_name=field_name)
