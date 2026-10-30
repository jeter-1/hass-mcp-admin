"""Fixed-vocabulary diagnosis of governance plans that fail projection.

HAMCP-198/HAMCP-120: health counted projection failures without naming them,
so a permanently failing record could not be retrieved or classified. This
module names the failing check only. It returns one category from a fixed
vocabulary and never echoes record contents. It is diagnosis, not
compatibility: it never admits a plan, changes a projection outcome, or grants
approval, task, or dispatch authority.
"""

from __future__ import annotations

import hmac

from ..errors import ErrorCode
from .historical_policy import (
    historical_policy_projection_has_only_approval_mismatch,
    persisted_policy_snapshot_integrity_matches,
)
from .models import ChangePlan
from .normalize import stable_hash
from .policy import (
    POLICY_VERSION,
    persisted_historical_policy_snapshot_matches,
    policy_snapshot_matches,
    policy_subject_payload,
)


PROJECTION_FAILURE_CATEGORY_MODEL = "hamcp198-projection-failure-category-v1"
MAX_HEALTH_PROJECTION_FAILURES = 8
PROJECTION_FAILURE_CATEGORIES = (
    # The stored subject hash does not reproduce from the immutable plan
    # subject: corruption, or a subject derivation that changed after writing.
    "policy_subject_hash_not_reproducible",
    # The stored decision hash does not reproduce from the stored decision.
    "policy_decision_hash_not_reproducible",
    # Approval does not carry the stored decision's hash or class.
    "approval_policy_binding_mismatch",
    # Exact reviewed historical decision; only the approval bundle differs.
    "historical_profile_approval_mismatch",
    # Current policy version, but current evaluation differs from the record.
    "current_policy_decision_drift",
    # Superseded f2-v1/f2-v2 decision that its own evaluator does not
    # reproduce, and that matches no reviewed historical profile. (A record
    # with any other policy version fails to load and never reaches here.)
    "superseded_policy_decision_drift",
    # The snapshot reproduces, but a later binding (for example the
    # safety-critical projection) does not.
    "policy_snapshot_binding_mismatch",
    "approval_authority_version_mismatch",
    "approval_principal_mismatch",
    "approval_sequence_inconsistent",
    # The record could not be examined (malformed or unhashable values).
    "category_unavailable",
)
_APPROVAL_CATEGORIES = {
    ErrorCode.APPROVAL_AUTHORITY_MISMATCH: (
        "approval_authority_version_mismatch"
    ),
    ErrorCode.APPROVAL_PRINCIPAL_MISMATCH: "approval_principal_mismatch",
    ErrorCode.APPROVAL_SEQUENCE_FAILURE: "approval_sequence_inconsistent",
}


def _category(plan: ChangePlan, error_code: ErrorCode) -> str:
    decision = plan.policy_decision
    if decision is None:
        return "category_unavailable"
    if not persisted_policy_snapshot_integrity_matches(plan):
        if not hmac.compare_digest(
            decision.policy_subject_hash,
            stable_hash(policy_subject_payload(plan)),
        ):
            return "policy_subject_hash_not_reproducible"
        return "policy_decision_hash_not_reproducible"
    approval = plan.approval
    if (
        approval.policy_decision_hash != decision.policy_decision_hash
        or approval.policy_class != decision.policy_class.value
    ):
        return "approval_policy_binding_mismatch"
    if historical_policy_projection_has_only_approval_mismatch(plan):
        return "historical_profile_approval_mismatch"
    if error_code in _APPROVAL_CATEGORIES:
        return _APPROVAL_CATEGORIES[error_code]
    if error_code is not ErrorCode.POLICY_SNAPSHOT_MISMATCH:
        return "category_unavailable"
    if decision.policy_version == POLICY_VERSION:
        if not policy_snapshot_matches(plan):
            return "current_policy_decision_drift"
    elif not persisted_historical_policy_snapshot_matches(plan):
        return "superseded_policy_decision_drift"
    return "policy_snapshot_binding_mismatch"


def projection_failure_category(
    plan: ChangePlan, error_code: ErrorCode
) -> str:
    """Return the first failing check for ``plan`` as a fixed category."""

    try:
        return _category(plan, error_code)
    except Exception:
        # Diagnosis must never turn a bounded projection failure into a
        # health failure, and never echoes why the record was unreadable.
        return "category_unavailable"


def projection_failure_identification(
    failures: list[tuple[ChangePlan, ErrorCode]],
) -> tuple[list[dict[str, str]], bool]:
    """Return at most eight ``{plan_id, category}`` rows and a truncation flag.

    Rows are ordered by plan ID so repeated health builds are stable. Only the
    listed records are examined, so the cost is bounded by the row limit.
    """

    ordered = sorted(failures, key=lambda item: item[0].plan_id)
    rows = [
        {
            "plan_id": plan.plan_id,
            "category": projection_failure_category(plan, error_code),
        }
        for plan, error_code in ordered[:MAX_HEALTH_PROJECTION_FAILURES]
    ]
    return rows, len(ordered) > MAX_HEALTH_PROJECTION_FAILURES
