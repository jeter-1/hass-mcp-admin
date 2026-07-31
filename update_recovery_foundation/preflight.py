"""Runtime-inert deterministic, provider-free preflight evaluator."""

from __future__ import annotations

import hashlib
import json

from .models import (
    BackupLocationStatus,
    BackupRequirement,
    BackupStatus,
    CompatibilityStatus,
    ExpectedDisruption,
    IssueSeverity,
    PreflightFinding,
    PreflightVerdict,
    RecoveryRequirement,
    StaleBackupDisposition,
    TargetPolicy,
    UpdatePreflightAssessment,
    UpdatePreflightEvidence,
    UpdateRecoveryPolicy,
    VersionDirection,
)
from .policy import DEFAULT_UPDATE_RECOVERY_POLICY


def evaluate_update_preflight(
    evidence: UpdatePreflightEvidence,
    policy: UpdateRecoveryPolicy = DEFAULT_UPDATE_RECOVERY_POLICY,
) -> UpdatePreflightAssessment:
    """Assess already-collected evidence without performing any I/O."""

    target_type = evidence.normalized_target_type
    target_policy = policy.for_target(target_type)
    if target_policy is None:
        return _assessment(
            policy=policy,
            evidence=evidence,
            verdict=PreflightVerdict.UNSUPPORTED,
            blockers=(
                _finding(
                    "unsupported_target_class",
                    "target_type",
                    f"Target class {target_type or '<empty>'!r} is not supported by this policy.",
                ),
            ),
            warnings=(),
            unknowns=(),
        )

    blockers: list[PreflightFinding] = []
    warnings: list[PreflightFinding] = []
    unknowns: list[PreflightFinding] = []

    _evaluate_identity_and_versions(evidence, blockers, warnings, unknowns)
    _evaluate_compatibility(evidence, blockers, unknowns)
    _evaluate_current_issues(evidence, blockers, warnings)
    _evaluate_backup(evidence, target_policy, blockers, warnings, unknowns)
    _evaluate_storage(evidence, blockers, unknowns)
    _evaluate_power(evidence, target_policy, blockers, warnings, unknowns)
    _evaluate_recovery(evidence, target_policy, blockers, warnings, unknowns)
    _evaluate_disruption_and_verification(
        evidence,
        target_policy,
        blockers,
        unknowns,
    )

    ordered_blockers = _ordered(blockers)
    ordered_warnings = _ordered(warnings)
    ordered_unknowns = _ordered(unknowns)
    if ordered_blockers:
        verdict = PreflightVerdict.BLOCKED
    elif ordered_unknowns or any(
        item.requires_manual_review for item in ordered_warnings
    ):
        verdict = PreflightVerdict.MANUAL_REVIEW_REQUIRED
    else:
        verdict = PreflightVerdict.READY_FOR_GOVERNED_PLANNING
    return _assessment(
        policy=policy,
        evidence=evidence,
        verdict=verdict,
        blockers=ordered_blockers,
        warnings=ordered_warnings,
        unknowns=ordered_unknowns,
    )


def _evaluate_identity_and_versions(
    evidence: UpdatePreflightEvidence,
    blockers: list[PreflightFinding],
    warnings: list[PreflightFinding],
    unknowns: list[PreflightFinding],
) -> None:
    if not evidence.target_id:
        blockers.append(
            _finding(
                "target_identity_missing",
                "target_id",
                "A bounded target identity is required before planning.",
            )
        )
    if evidence.installed_version is None:
        unknowns.append(
            _finding(
                "installed_version_unknown",
                "installed_version",
                "The installed version is unknown.",
                manual_review=True,
            )
        )
    if evidence.candidate_version is None:
        blockers.append(
            _finding(
                "candidate_version_missing",
                "candidate_version",
                "An authoritative candidate version is required before planning.",
            )
        )
    elif (
        evidence.candidate_version_evidence is None
        or not evidence.candidate_version_evidence.authoritative
    ):
        blockers.append(
            _finding(
                "candidate_version_not_authoritative",
                "candidate_version_evidence",
                "The candidate version is not backed by authoritative evidence.",
                evidence_ids=_evidence_ids(evidence.candidate_version_evidence),
            )
        )
    _evaluate_version_direction(evidence, blockers, warnings, unknowns)


def _evaluate_version_direction(
    evidence: UpdatePreflightEvidence,
    blockers: list[PreflightFinding],
    warnings: list[PreflightFinding],
    unknowns: list[PreflightFinding],
) -> None:
    direction = evidence.version_direction
    installed = evidence.installed_version
    candidate = evidence.candidate_version

    if direction == VersionDirection.UNKNOWN:
        unknowns.append(
            _finding(
                "candidate_version_direction_unknown",
                "version_direction",
                "The candidate version direction is unknown.",
                manual_review=True,
            )
        )
    elif direction == VersionDirection.DOWNGRADE:
        warnings.append(
            _finding(
                "candidate_version_is_downgrade",
                "version_direction",
                "The candidate is a caller-identified downgrade and requires review under "
                "docs/runbooks/DOWNGRADE-VERSUS-BACKUP-RESTORE.md.",
                manual_review=True,
            )
        )

    if candidate is None:
        if direction != VersionDirection.UNKNOWN:
            warnings.append(
                _direction_inconsistency(
                    "A version direction cannot be confirmed without a candidate version."
                )
            )
        return

    if installed is None:
        if direction != VersionDirection.UNKNOWN:
            warnings.append(
                _direction_inconsistency(
                    "A known version direction cannot be confirmed without an installed version."
                )
            )
        return

    versions_match = installed == candidate
    if versions_match:
        blockers.append(
            _finding(
                "candidate_matches_installed_version",
                "candidate_version",
                "The candidate and installed version strings are identical; no update is required.",
            )
        )
        if direction in {
            VersionDirection.UPGRADE,
            VersionDirection.DOWNGRADE,
        }:
            warnings.append(
                _direction_inconsistency(
                    "Equal installed and candidate versions conflict with the claimed direction."
                )
            )
        return

    if direction == VersionDirection.SAME:
        warnings.append(
            _direction_inconsistency(
                "Different installed and candidate versions conflict with a same-version direction."
            )
        )


def _evaluate_compatibility(
    evidence: UpdatePreflightEvidence,
    blockers: list[PreflightFinding],
    unknowns: list[PreflightFinding],
) -> None:
    evidence_ids = tuple(
        item.evidence_id for item in evidence.compatibility_evidence
    )
    if evidence.compatibility_status == CompatibilityStatus.INCOMPATIBLE:
        blockers.append(
            _finding(
                "target_incompatible",
                "compatibility_status",
                "Collected compatibility evidence marks the target as incompatible.",
                evidence_ids=evidence_ids,
            )
        )
    elif evidence.compatibility_status == CompatibilityStatus.UNAVAILABLE:
        blockers.append(
            _finding(
                "compatibility_evidence_unavailable",
                "compatibility_evidence",
                "Required compatibility evidence is unavailable.",
                evidence_ids=evidence_ids,
            )
        )
    elif evidence.compatibility_status == CompatibilityStatus.UNKNOWN:
        unknowns.append(
            _finding(
                "compatibility_unknown",
                "compatibility_status",
                "Compatibility is unknown and requires manual review.",
                evidence_ids=evidence_ids,
                manual_review=True,
            )
        )
    elif not any(item.authoritative for item in evidence.compatibility_evidence):
        blockers.append(
            _finding(
                "authoritative_compatibility_evidence_missing",
                "compatibility_evidence",
                "A compatible status requires at least one authoritative evidence reference.",
                evidence_ids=evidence_ids,
            )
        )


def _evaluate_current_issues(
    evidence: UpdatePreflightEvidence,
    blockers: list[PreflightFinding],
    warnings: list[PreflightFinding],
) -> None:
    for field_name, issues in (
        ("current_repairs", evidence.current_repairs),
        ("current_errors", evidence.current_errors),
    ):
        singular = "repair" if field_name == "current_repairs" else "error"
        for issue in sorted(
            issues,
            key=lambda item: (item.severity.value, item.issue_id, item.summary),
        ):
            if issue.severity == IssueSeverity.CRITICAL:
                blockers.append(
                    _finding(
                        f"critical_{singular}_unresolved",
                        field_name,
                        f"Critical {singular} {issue.issue_id!r} is unresolved.",
                    )
                )
            else:
                warnings.append(
                    _finding(
                        f"{issue.severity.value}_{singular}_present",
                        field_name,
                        f"{issue.severity.value.title()} {singular} {issue.issue_id!r} is present.",
                        manual_review=issue.severity == IssueSeverity.HIGH,
                    )
                )


def _evaluate_backup(
    evidence: UpdatePreflightEvidence,
    target_policy: TargetPolicy,
    blockers: list[PreflightFinding],
    warnings: list[PreflightFinding],
    unknowns: list[PreflightFinding],
) -> None:
    if target_policy.backup_requirement == BackupRequirement.NOT_REQUIRED:
        return
    if evidence.backup_status in {BackupStatus.MISSING, BackupStatus.NOT_REQUIRED}:
        blockers.append(
            _finding(
                "required_backup_missing",
                "backup_status",
                "This target policy requires a backup, but no required backup is available.",
            )
        )
        return
    if evidence.backup_status == BackupStatus.UNAVAILABLE:
        blockers.append(
            _finding(
                "required_backup_unavailable",
                "backup_status",
                "The required backup could not be inspected.",
            )
        )
        return
    if evidence.backup_status == BackupStatus.UNKNOWN:
        blockers.append(
            _finding(
                "required_backup_unverified",
                "backup_status",
                "The required backup status is unknown.",
            )
        )
        return

    if evidence.backup_age_hours is None:
        blockers.append(
            _finding(
                "required_backup_age_missing",
                "backup_age_hours",
                "The age of the required backup is unavailable.",
            )
        )
    else:
        age_exceeds_policy = (
            target_policy.max_backup_age_hours is not None
            and evidence.backup_age_hours > target_policy.max_backup_age_hours
        )
        stale = (
            evidence.backup_status == BackupStatus.STALE
            or age_exceeds_policy
        )
        if stale:
            summary = (
                f"The backup is older than the {target_policy.max_backup_age_hours:g}-hour "
                "policy window."
                if age_exceeds_policy
                else "The backup is marked stale."
            )
            if (
                target_policy.stale_backup_disposition
                == StaleBackupDisposition.BLOCK
            ):
                blockers.append(
                    _finding(
                        "required_backup_stale",
                        "backup_age_hours",
                        summary,
                    )
                )
            else:
                warnings.append(
                    _finding(
                        "stale_backup_requires_manual_review",
                        "backup_age_hours",
                        summary,
                        manual_review=True,
                    )
                )

    if evidence.backup_location_status != BackupLocationStatus.VERIFIED:
        blockers.append(
            _finding(
                "required_backup_location_unverified",
                "backup_location_status",
                "The required backup location is not verified as available.",
            )
        )


def _evaluate_storage(
    evidence: UpdatePreflightEvidence,
    blockers: list[PreflightFinding],
    unknowns: list[PreflightFinding],
) -> None:
    if evidence.required_storage is None:
        unknowns.append(
            _finding(
                "required_storage_unknown",
                "required_storage",
                "Required storage is unknown.",
                manual_review=True,
            )
        )
        return
    if evidence.required_storage == 0:
        return
    if evidence.free_storage is None:
        unknowns.append(
            _finding(
                "free_storage_unknown",
                "free_storage",
                "Available storage is unknown.",
                manual_review=True,
            )
        )
    elif evidence.free_storage < evidence.required_storage:
        blockers.append(
            _finding(
                "insufficient_storage",
                "free_storage",
                "Available storage is below the collected update requirement.",
            )
        )


def _evaluate_power(
    evidence: UpdatePreflightEvidence,
    target_policy: TargetPolicy,
    blockers: list[PreflightFinding],
    warnings: list[PreflightFinding],
    unknowns: list[PreflightFinding],
) -> None:
    if evidence.power_stability_known and evidence.power_stable is False:
        blockers.append(
            _finding(
                "power_unstable",
                "power_stable",
                "Collected evidence reports unstable power.",
            )
        )
    elif (
        target_policy.power_stability_required
        and not evidence.power_stability_known
    ):
        unknowns.append(
            _finding(
                "power_stability_unknown",
                "power_stability_known",
                "This target policy requires known power stability.",
                manual_review=True,
            )
        )
    elif not evidence.power_stability_known:
        warnings.append(
            _finding(
                "power_stability_not_assessed",
                "power_stability_known",
                "Power stability was not assessed for this target.",
            )
        )


def _evaluate_recovery(
    evidence: UpdatePreflightEvidence,
    target_policy: TargetPolicy,
    blockers: list[PreflightFinding],
    warnings: list[PreflightFinding],
    unknowns: list[PreflightFinding],
) -> None:
    if evidence.rollback_available is False:
        warnings.append(
            _finding(
                "rollback_unavailable",
                "rollback_available",
                "A direct rollback path is unavailable.",
            )
        )
    elif evidence.rollback_available is None:
        unknowns.append(
            _finding(
                "rollback_availability_unknown",
                "rollback_available",
                "Direct rollback availability is unknown.",
                manual_review=True,
            )
        )
    if evidence.restore_available is False:
        warnings.append(
            _finding(
                "restore_unavailable",
                "restore_available",
                "A restore path is unavailable.",
            )
        )
    elif evidence.restore_available is None:
        unknowns.append(
            _finding(
                "restore_availability_unknown",
                "restore_available",
                "Restore availability is unknown.",
                manual_review=True,
            )
        )

    viable = evidence.rollback_available is True or evidence.restore_available is True
    availability_known = (
        evidence.rollback_available is not None
        and evidence.restore_available is not None
    )
    if viable or not availability_known:
        return
    if target_policy.recovery_requirement == RecoveryRequirement.REQUIRED:
        blockers.append(
            _finding(
                "viable_recovery_path_missing",
                "rollback_available",
                "Policy requires a viable rollback or restore path, but neither is available.",
            )
        )
    else:
        warnings.append(
            _finding(
                "recovery_path_requires_manual_review",
                "restore_available",
                "Neither rollback nor restore is available for this target.",
                manual_review=True,
            )
        )


def _evaluate_disruption_and_verification(
    evidence: UpdatePreflightEvidence,
    target_policy: TargetPolicy,
    blockers: list[PreflightFinding],
    unknowns: list[PreflightFinding],
) -> None:
    if evidence.expected_disruption == ExpectedDisruption.UNKNOWN:
        unknowns.append(
            _finding(
                "expected_disruption_unknown",
                "expected_disruption",
                "Expected disruption has not been classified.",
                manual_review=True,
            )
        )
    if evidence.post_update_verification_profile is None:
        unknowns.append(
            _finding(
                "verification_profile_missing",
                "post_update_verification_profile",
                "A post-update verification profile has not been selected.",
                manual_review=True,
            )
        )
    elif (
        evidence.post_update_verification_profile
        not in target_policy.verification_profiles
    ):
        blockers.append(
            _finding(
                "verification_profile_not_allowed",
                "post_update_verification_profile",
                "The selected verification profile is not allowed for this target class.",
            )
        )


def _assessment(
    *,
    policy: UpdateRecoveryPolicy,
    evidence: UpdatePreflightEvidence,
    verdict: PreflightVerdict,
    blockers: tuple[PreflightFinding, ...],
    warnings: tuple[PreflightFinding, ...],
    unknowns: tuple[PreflightFinding, ...],
) -> UpdatePreflightAssessment:
    fingerprint_payload = {
        "policy_id": policy.policy_id,
        "target_type": evidence.normalized_target_type,
        "target_id": evidence.target_id,
        "installed_version": evidence.installed_version,
        "candidate_version": evidence.candidate_version,
        "version_direction": evidence.version_direction.value,
        "verdict": verdict.value,
        "blockers": [item.as_dict() for item in blockers],
        "warnings": [item.as_dict() for item in warnings],
        "unknowns": [item.as_dict() for item in unknowns],
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return UpdatePreflightAssessment(
        policy_id=policy.policy_id,
        target_type=evidence.normalized_target_type,
        target_id=evidence.target_id,
        installed_version=evidence.installed_version,
        candidate_version=evidence.candidate_version,
        version_direction=evidence.version_direction,
        verdict=verdict,
        blockers=blockers,
        warnings=warnings,
        unknowns=unknowns,
        assessment_fingerprint=fingerprint,
    )


def _finding(
    code: str,
    field: str,
    summary: str,
    *,
    evidence_ids: tuple[str, ...] = (),
    manual_review: bool = False,
) -> PreflightFinding:
    return PreflightFinding(
        code=code,
        field=field,
        summary=summary,
        evidence_references=evidence_ids,
        requires_manual_review=manual_review,
    )


def _direction_inconsistency(summary: str) -> PreflightFinding:
    return _finding(
        "candidate_version_direction_inconsistent",
        "version_direction",
        summary,
        manual_review=True,
    )


def _evidence_ids(reference: object | None) -> tuple[str, ...]:
    evidence_id = getattr(reference, "evidence_id", None)
    return (str(evidence_id),) if evidence_id else ()


def _ordered(values: list[PreflightFinding]) -> tuple[PreflightFinding, ...]:
    return tuple(
        sorted(
            set(values),
            key=lambda item: (
                item.code,
                item.field,
                item.summary,
                item.evidence_references,
                item.requires_manual_review,
            ),
        )
    )
