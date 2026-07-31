"""Explicit proposed policy for the runtime-inert preflight model."""

from .models import (
    BackupRequirement,
    PostUpdateVerificationProfile,
    RecoveryRequirement,
    StaleBackupDisposition,
    TargetPolicy,
    TargetType,
    UpdateRecoveryPolicy,
)


def _target_policy(
    target_type: TargetType,
    profile: PostUpdateVerificationProfile,
    *,
    backup_required: bool = True,
    max_backup_age_hours: float | None = 24,
    stale_backup_disposition: StaleBackupDisposition = StaleBackupDisposition.BLOCK,
    recovery_requirement: RecoveryRequirement = RecoveryRequirement.REQUIRED,
    power_stability_required: bool = False,
) -> TargetPolicy:
    return TargetPolicy(
        target_type=target_type,
        backup_requirement=(
            BackupRequirement.REQUIRED
            if backup_required
            else BackupRequirement.NOT_REQUIRED
        ),
        max_backup_age_hours=max_backup_age_hours if backup_required else None,
        stale_backup_disposition=stale_backup_disposition,
        recovery_requirement=recovery_requirement,
        power_stability_required=power_stability_required,
        verification_profiles=(profile,),
    )


DEFAULT_UPDATE_RECOVERY_POLICY = UpdateRecoveryPolicy(
    policy_id="update-recovery-preflight-v1",
    target_policies=(
        _target_policy(
            TargetType.HOME_ASSISTANT_CORE,
            PostUpdateVerificationProfile.HOME_ASSISTANT_CORE,
        ),
        _target_policy(
            TargetType.SUPERVISOR,
            PostUpdateVerificationProfile.SUPERVISOR,
        ),
        _target_policy(
            TargetType.HOME_ASSISTANT_OS,
            PostUpdateVerificationProfile.HOME_ASSISTANT_OS,
            power_stability_required=True,
        ),
        _target_policy(
            TargetType.ADDON_APP,
            PostUpdateVerificationProfile.ADDON_APP,
            max_backup_age_hours=72,
            stale_backup_disposition=StaleBackupDisposition.MANUAL_REVIEW,
            recovery_requirement=RecoveryRequirement.MANUAL_REVIEW_IF_UNAVAILABLE,
        ),
        _target_policy(
            TargetType.HACS_INTEGRATION,
            PostUpdateVerificationProfile.HACS,
            max_backup_age_hours=72,
            stale_backup_disposition=StaleBackupDisposition.MANUAL_REVIEW,
            recovery_requirement=RecoveryRequirement.MANUAL_REVIEW_IF_UNAVAILABLE,
        ),
        _target_policy(
            TargetType.HACS_FRONTEND_COMPONENT,
            PostUpdateVerificationProfile.HACS,
            max_backup_age_hours=72,
            stale_backup_disposition=StaleBackupDisposition.MANUAL_REVIEW,
            recovery_requirement=RecoveryRequirement.MANUAL_REVIEW_IF_UNAVAILABLE,
        ),
        _target_policy(
            TargetType.ENGINEERING_MCP_SERVER,
            PostUpdateVerificationProfile.ENGINEERING_MCP_SERVER,
        ),
        _target_policy(
            TargetType.UPSTREAM_HA_MCP,
            PostUpdateVerificationProfile.UPSTREAM_HA_MCP,
        ),
        _target_policy(
            TargetType.FIRMWARE_UPDATE_ENTITY,
            PostUpdateVerificationProfile.FIRMWARE,
            backup_required=False,
            max_backup_age_hours=None,
            power_stability_required=True,
        ),
    ),
)
