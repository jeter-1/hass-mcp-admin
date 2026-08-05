"""Deterministic F3 outcome projection without persisted-state changes."""

from __future__ import annotations

from dataclasses import dataclass

from ha_mcp_engineering.f3.contracts import NormalizedOperationOutcome


@dataclass(frozen=True)
class ConfigurationOutcomeMapping:
    task_state: str
    terminal: bool
    dispatch_possible: bool
    permitted_recovery: str
    lock_behavior: str
    public_error: str | None


CONFIGURATION_OUTCOME_MAPPING = {
    NormalizedOperationOutcome.PREFLIGHT_REJECTED: ConfigurationOutcomeMapping(
        "failed_pre_dispatch", True, False, "new_governed_attempt", "release",
        "configuration_validation_failed",
    ),
    NormalizedOperationOutcome.LOCK_CONFLICT: ConfigurationOutcomeMapping(
        "failed_pre_dispatch", True, False, "new_governed_attempt", "no_ownership",
        "change_in_progress",
    ),
    NormalizedOperationOutcome.PROVIDER_UNAVAILABLE_PRE_DISPATCH: ConfigurationOutcomeMapping(
        "failed_pre_dispatch", True, False, "new_governed_attempt", "release",
        "configuration_apply_failed",
    ),
    NormalizedOperationOutcome.DISPATCH_FAILED_CONFIRMED: ConfigurationOutcomeMapping(
        "failed_post_dispatch", True, False, "new_governed_decision", "release",
        "configuration_apply_failed",
    ),
    NormalizedOperationOutcome.DISPATCH_INDETERMINATE: ConfigurationOutcomeMapping(
        "observing", False, False, "readback_only", "retain",
        "configuration_apply_failed",
    ),
    NormalizedOperationOutcome.OBSERVING: ConfigurationOutcomeMapping(
        "observing", False, False, "readback_only", "retain", None,
    ),
    NormalizedOperationOutcome.VERIFICATION_MISMATCH: ConfigurationOutcomeMapping(
        "failed_post_dispatch", True, False, "new_governed_decision", "release",
        "configuration_verification_failed",
    ),
    NormalizedOperationOutcome.SUCCEEDED_VERIFIED: ConfigurationOutcomeMapping(
        "succeeded_verified", True, False, "return_existing_result", "release", None,
    ),
    NormalizedOperationOutcome.FAILED_PRE_DISPATCH: ConfigurationOutcomeMapping(
        "failed_pre_dispatch", True, False, "new_governed_attempt", "release",
        "configuration_apply_failed",
    ),
    NormalizedOperationOutcome.FAILED_POST_DISPATCH: ConfigurationOutcomeMapping(
        "failed_post_dispatch", True, False, "new_governed_decision", "release",
        "configuration_apply_failed",
    ),
    NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED: ConfigurationOutcomeMapping(
        "manual_review_required", True, False, "governed_reconciliation", "conflict_hold",
        "configuration_apply_failed",
    ),
    NormalizedOperationOutcome.CANCELLED_PRE_DISPATCH: ConfigurationOutcomeMapping(
        "cancelled_pre_dispatch", True, False, "new_governed_attempt", "release", None,
    ),
}


def outcome_mapping(
    outcome: NormalizedOperationOutcome,
) -> ConfigurationOutcomeMapping:
    try:
        return CONFIGURATION_OUTCOME_MAPPING[outcome]
    except KeyError as exc:  # pragma: no cover - enum is exhaustive
        raise ValueError("configuration outcome is not mapped") from exc
