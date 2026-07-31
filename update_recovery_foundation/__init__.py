"""Runtime-inert update and recovery preflight decision contracts."""

from .models import (
    BackupLocationStatus,
    BackupRequirement,
    BackupStatus,
    CompatibilityStatus,
    EvidenceReference,
    ExpectedDisruption,
    IssueSeverity,
    PostUpdateVerificationProfile,
    PreflightFinding,
    PreflightVerdict,
    RecoveryRequirement,
    StaleBackupDisposition,
    TargetPolicy,
    TargetType,
    UpdatePreflightAssessment,
    UpdatePreflightEvidence,
    UpdateRecoveryPolicy,
    UpdateRiskIssue,
    VersionDirection,
)
from .policy import DEFAULT_UPDATE_RECOVERY_POLICY
from .preflight import evaluate_update_preflight

__all__ = [
    "BackupLocationStatus",
    "BackupRequirement",
    "BackupStatus",
    "CompatibilityStatus",
    "DEFAULT_UPDATE_RECOVERY_POLICY",
    "EvidenceReference",
    "ExpectedDisruption",
    "IssueSeverity",
    "PostUpdateVerificationProfile",
    "PreflightFinding",
    "PreflightVerdict",
    "RecoveryRequirement",
    "StaleBackupDisposition",
    "TargetPolicy",
    "TargetType",
    "UpdatePreflightAssessment",
    "UpdatePreflightEvidence",
    "UpdateRecoveryPolicy",
    "UpdateRiskIssue",
    "VersionDirection",
    "evaluate_update_preflight",
]
