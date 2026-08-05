"""Dashboard-specific stale preflight and exact reread verification."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .constants import MAX_DIFF_MISMATCH_PATHS, VERIFICATION_MODEL
from .errors import RawEvidenceError, VerificationError
from .json_codec import engineering_sha256, strict_json_equal
from .models import (
    AtomicityStatus,
    DashboardPreflight,
    DashboardPreread,
    DashboardUpdateProposal,
    DashboardVerification,
    VerificationOutcome,
)
from .observability import DashboardWriteObservability
from .patch import mismatch_paths
from .raw_evidence import build_raw_dashboard_evidence


def _timestamp(value: str) -> datetime:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise VerificationError("Proposal expiration is malformed") from exc
    if parsed.tzinfo is None:
        raise VerificationError("Proposal expiration lacks a timezone")
    return parsed.astimezone(timezone.utc)


def assess_dashboard_preflight(
    proposal: DashboardUpdateProposal,
    current: DashboardPreread,
    *,
    now: datetime,
    approval_bundle_validated_by_caller_layer: bool,
    acquired_lock_keys: tuple[str, ...],
    fencing_validated_by_f3a: bool,
) -> DashboardPreflight:
    """Fail closed without claiming F3-A lock ownership or authorization."""

    if now.tzinfo is None:
        raise VerificationError("Preflight time must include a timezone")
    codes: list[str] = []
    expired = now.astimezone(timezone.utc) >= _timestamp(proposal.expires_at)
    if expired:
        codes.append("approved_plan_expired")
    if not approval_bundle_validated_by_caller_layer:
        codes.append("approval_bundle_not_validated_by_caller_layer")
    complete_locks = tuple(sorted(set(acquired_lock_keys))) == proposal.lock_keys
    if not complete_locks:
        codes.append("complete_lock_set_not_present")
    if not fencing_validated_by_f3a:
        codes.append("f3a_fencing_not_validated")
    try:
        evidence = build_raw_dashboard_evidence(
            current, requested_url_path=proposal.target_id
        )
    except RawEvidenceError:
        return DashboardPreflight(
            eligible=False,
            stale=True,
            plan_expired=expired,
            approval_bundle_validated=approval_bundle_validated_by_caller_layer,
            complete_lock_keys_present=complete_locks,
            fencing_validated=fencing_validated_by_f3a,
            atomicity_validated=False,
            observed_upstream_config_hash=None,
            observed_engineering_sha256=None,
            diagnostic_codes=tuple((*codes, "exact_current_preread_rejected")),
        )
    stale = (
        evidence.upstream_config_hash != proposal.raw_evidence.upstream_config_hash
        or evidence.engineering_config_sha256
        != proposal.raw_evidence.engineering_config_sha256
    )
    if stale:
        codes.append("stale_dashboard_state")
    atomicity = proposal.atomicity.status in {
        AtomicityStatus.PROVEN_ATOMIC,
        AtomicityStatus.AUTHORITATIVE_WRITER_EXCLUSION,
    }
    if not atomicity:
        codes.append("atomicity_gate_rejected")
    eligible = all(
        (
            not expired,
            approval_bundle_validated_by_caller_layer,
            complete_locks,
            fencing_validated_by_f3a,
            not stale,
            atomicity,
            proposal.executable,
        )
    )
    if not proposal.executable:
        codes.append("proposal_is_planning_only")
    return DashboardPreflight(
        eligible=eligible,
        stale=stale,
        plan_expired=expired,
        approval_bundle_validated=approval_bundle_validated_by_caller_layer,
        complete_lock_keys_present=complete_locks,
        fencing_validated=fencing_validated_by_f3a,
        atomicity_validated=atomicity,
        observed_upstream_config_hash=evidence.upstream_config_hash,
        observed_engineering_sha256=evidence.engineering_config_sha256,
        diagnostic_codes=tuple(codes),
    )


def verify_dashboard_observation(
    proposal: DashboardUpdateProposal,
    observed: DashboardPreread | None,
    *,
    authoritative_no_write: bool = False,
    observability: DashboardWriteObservability | None = None,
) -> DashboardVerification:
    """Resolve exact readback; provider claims alone are intentionally absent."""

    metrics = observability or DashboardWriteObservability()
    metrics.record("verification.rereads", target=proposal.target_id)
    if observed is None:
        outcome = (
            VerificationOutcome.FAILED_CONFIRMED_NO_WRITE
            if authoritative_no_write
            else VerificationOutcome.MANUAL_REVIEW_REQUIRED
        )
        counter = (
            "verification.ambiguous_outcomes"
            if not authoritative_no_write
            else "verification.mismatch_outcomes"
        )
        metrics.record(counter, target=proposal.target_id)
        if outcome is VerificationOutcome.MANUAL_REVIEW_REQUIRED:
            metrics.record("verification.manual_review_transitions", target=proposal.target_id)
        projection = {
            "model": VERIFICATION_MODEL,
            "outcome": outcome.value,
            "target": proposal.target_id,
            "observation_complete": False,
            "authoritative_no_write": authoritative_no_write,
        }
        return DashboardVerification(
            model=VERIFICATION_MODEL,
            outcome=outcome,
            verified=False if authoritative_no_write else None,
            canonical_url_path=proposal.target_id,
            resulting_upstream_config_hash=None,
            resulting_engineering_sha256=None,
            mismatch_paths=(),
            untouched_fields_preserved=None,
            observation_complete=False,
            diagnostic_codes=(
                "authoritative_no_write" if authoritative_no_write else "readback_unavailable",
            ),
            evidence_sha256=engineering_sha256(projection),
        )
    try:
        evidence = build_raw_dashboard_evidence(
            observed, requested_url_path=proposal.target_id
        )
    except RawEvidenceError:
        metrics.record("verification.ambiguous_outcomes", target=proposal.target_id)
        metrics.record("verification.manual_review_transitions", target=proposal.target_id)
        projection = {
            "model": VERIFICATION_MODEL,
            "outcome": VerificationOutcome.MANUAL_REVIEW_REQUIRED.value,
            "target": proposal.target_id,
            "observation_complete": False,
            "diagnostic": "exact_readback_rejected",
        }
        return DashboardVerification(
            model=VERIFICATION_MODEL,
            outcome=VerificationOutcome.MANUAL_REVIEW_REQUIRED,
            verified=None,
            canonical_url_path=proposal.target_id,
            resulting_upstream_config_hash=None,
            resulting_engineering_sha256=None,
            mismatch_paths=(),
            untouched_fields_preserved=None,
            observation_complete=False,
            diagnostic_codes=("exact_readback_rejected",),
            evidence_sha256=engineering_sha256(projection),
        )

    expected = proposal.compilation.resulting_configuration
    observed_config = evidence.configuration
    if strict_json_equal(observed_config, expected) and (
        evidence.engineering_config_sha256 == proposal.compilation.resulting_sha256
        and evidence.upstream_config_hash
        == proposal.compilation.resulting_upstream_config_hash
    ):
        metrics.record("verification.exact_matches", target=proposal.target_id)
        projection = {
            "model": VERIFICATION_MODEL,
            "outcome": VerificationOutcome.SUCCEEDED_VERIFIED.value,
            "target": proposal.target_id,
            "resulting_upstream_config_hash": evidence.upstream_config_hash,
            "resulting_engineering_sha256": evidence.engineering_config_sha256,
        }
        return DashboardVerification(
            model=VERIFICATION_MODEL,
            outcome=VerificationOutcome.SUCCEEDED_VERIFIED,
            verified=True,
            canonical_url_path=proposal.target_id,
            resulting_upstream_config_hash=evidence.upstream_config_hash,
            resulting_engineering_sha256=evidence.engineering_config_sha256,
            mismatch_paths=(),
            untouched_fields_preserved=True,
            observation_complete=True,
            diagnostic_codes=("exact_full_configuration_match",),
            evidence_sha256=engineering_sha256(projection),
        )

    paths = mismatch_paths(
        expected,
        observed_config,
        limit=MAX_DIFF_MISMATCH_PATHS,
    )
    declared = tuple(operation.path for operation in proposal.compilation.operations)
    untouched = all(
        any(
            path == allowed
            or path.startswith(allowed + "/")
            or allowed.startswith(path + "/")
            for allowed in declared
        )
        for path in paths
    )
    if not untouched:
        metrics.record(
            "verification.untouched_field_preservation_failures",
            target=proposal.target_id,
        )
    metrics.record("verification.mismatch_outcomes", target=proposal.target_id)
    projection = {
        "model": VERIFICATION_MODEL,
        "outcome": VerificationOutcome.VERIFICATION_MISMATCH.value,
        "target": proposal.target_id,
        "mismatch_paths": list(paths),
        "untouched_fields_preserved": untouched,
        "resulting_upstream_config_hash": evidence.upstream_config_hash,
        "resulting_engineering_sha256": evidence.engineering_config_sha256,
    }
    return DashboardVerification(
        model=VERIFICATION_MODEL,
        outcome=VerificationOutcome.VERIFICATION_MISMATCH,
        verified=False,
        canonical_url_path=proposal.target_id,
        resulting_upstream_config_hash=evidence.upstream_config_hash,
        resulting_engineering_sha256=evidence.engineering_config_sha256,
        mismatch_paths=paths,
        untouched_fields_preserved=untouched,
        observation_complete=True,
        diagnostic_codes=("full_configuration_mismatch",),
        evidence_sha256=engineering_sha256(projection),
    )
