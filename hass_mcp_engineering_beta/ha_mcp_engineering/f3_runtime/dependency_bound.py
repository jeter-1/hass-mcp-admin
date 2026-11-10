"""Dependency-bound compensating execution (HAMCP-109, design section 3.2).

Pure decision logic for ``dependency_bound_compensating_v1`` plans:

* per-child ownership classes derived only from the durable child
  declaration and execution record (3.2.2a);
* the three-condition compensation rule and the durable
  ``compensation_decided`` record (3.2.3, 3.2.8);
* the compensation sequencer: decision persisted before the first
  compensation intent, complete lock transfer, a full-bundle drift check
  before every compensation child, one attempt each, reverse order over
  ``owned_verified`` steps only (3.2.5, 3.2.7);
* terminal outcomes and accounting (3.2.9).

Nothing here dispatches by itself.  The sequencer calls injected hooks the
runtime supplies (persist, lock transfer, bundle check, one governed F3
child per compensation step); it never retries, never compensates on an
unknown outcome, and never recomputes a persisted decision with a different
set.  Parent projections (``completed_operation_ids``, ``terminal_outcome``)
are never an input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Callable, Iterable, Mapping, Sequence


OWNED_VERIFIED = "owned_verified"
ALREADY_PRESENT_VERIFIED = "already_present_verified"
NOT_APPLIED_DETERMINATE = "not_applied_determinate"
INDETERMINATE = "indeterminate"
NOT_ATTEMPTED = "not_attempted"
OWNERSHIP_CLASSES = (
    OWNED_VERIFIED,
    ALREADY_PRESENT_VERIFIED,
    NOT_APPLIED_DETERMINATE,
    INDETERMINATE,
    NOT_ATTEMPTED,
)
DETERMINATE_CLASSES = frozenset(
    {OWNED_VERIFIED, ALREADY_PRESENT_VERIFIED, NOT_APPLIED_DETERMINATE}
)
DEPENDENCY_SATISFYING_CLASSES = frozenset({OWNED_VERIFIED, ALREADY_PRESENT_VERIFIED})

OUTCOME_SUCCEEDED = "succeeded_verified"
OUTCOME_ALREADY_PRESENT = "desired_state_already_present_not_dispatched"
OUTCOME_FAILED_NO_WRITES = "failed_no_writes"
OUTCOME_COMPENSATED = "failed_compensated_verified"
OUTCOME_COMPENSATION_INCOMPLETE = "failed_compensation_incomplete"
OUTCOME_HELD_INDETERMINATE = "held_indeterminate"
TERMINAL_OUTCOMES = frozenset(
    {
        OUTCOME_SUCCEEDED,
        OUTCOME_ALREADY_PRESENT,
        OUTCOME_FAILED_NO_WRITES,
        OUTCOME_COMPENSATED,
        OUTCOME_COMPENSATION_INCOMPLETE,
        OUTCOME_HELD_INDETERMINATE,
    }
)

TASK_STATE_SUCCEEDED = "succeeded_verified"
TASK_STATE_FAILED_PRE_DISPATCH = "failed_pre_dispatch"
TASK_STATE_FAILED_POST_DISPATCH = "failed_post_dispatch"
TASK_STATE_MANUAL_REVIEW = "manual_review_required"

COMPENSATION_DRIFT = "compensation_drift"
COMPENSATION_LOCKS_LOST = "compensation_locks_lost"
COMPENSATION_DECISION_MISMATCH = "compensation_decision_mismatch"
COMPENSATION_STEP_FAILED = "compensation_step_failed"
COMPENSATION_STEP_INDETERMINATE = "compensation_step_indeterminate"
EXCLUDED_NOT_DISPATCHED = "not_dispatched_by_this_plan"

PHASE_FORWARD = "forward"
PHASE_COMPENSATING = "compensating"
PHASE_TERMINAL = "terminal"

DECISION_MODEL = "hamcp109_compensation_decided_v1"
NOOP_EVENT = "preflight_noop_verified"
_NOT_APPLIED_POST_INTENT_OUTCOMES = frozenset({"dispatch_failed_confirmed"})

# Execution-record classes (``f3/models.py`` ``execution_class``).  Kept as
# literals: ``f3_runtime`` reads records duck-typed (packaging boundary, as
# ``dispatch_facts``).
_CLASS_PRE_INTENT = "pre_intent"
_CLASS_TERMINAL_PRE_DISPATCH = "terminal_pre_dispatch"
_CLASS_VERIFIED_NO_DISPATCH = "verified_no_dispatch"
_CLASS_TERMINAL_POST_INTENT = "terminal_post_intent"


class CompensationDecisionMismatchError(RuntimeError):
    code = COMPENSATION_DECISION_MISMATCH


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class PlanBinding:
    plan_id: str
    plan_hash: str
    public_task_id: str
    approval_bundle_hash: str


@dataclass(frozen=True)
class StepContract:
    """The plan-time facts one child must prove (all hash-bound)."""

    operation_id: str
    ordinal: int
    dependency_ids: tuple[str, ...]
    target_type: str
    target_id: str
    capability_id: str
    prepared_operation_hash: str
    provider_arguments_hash: str
    planned_result_fingerprint: str
    compensation_prepared_operation_hash: str | None = None


@dataclass(frozen=True)
class Readback:
    """Authoritative readback for a child whose record alone cannot decide."""

    forward_result_present: bool = False
    not_applied_proven: bool = False


@dataclass(frozen=True)
class ChildClass:
    operation_id: str
    ownership: str
    reason: str
    child_id: str | None = None
    record_hash: str | None = None
    dispatched: bool = False

    @property
    def changed_by_engineering(self) -> bool:
        return self.ownership == OWNED_VERIFIED


def _record_dict(record: Any) -> dict[str, Any]:
    to_dict = getattr(record, "to_dict", None)
    return to_dict() if callable(to_dict) else dict(record)


def record_hash(record: Any) -> str:
    return canonical_hash(_record_dict(record))


def _declaration_matches(
    declaration: Mapping[str, Any], step: StepContract, plan: PlanBinding
) -> bool:
    expected = {
        "plan_id": plan.plan_id,
        "plan_hash": plan.plan_hash,
        "public_task_id": plan.public_task_id,
        "approval_bundle_hash": plan.approval_bundle_hash,
        "operation_id": step.operation_id,
        "operation_ordinal": step.ordinal,
        "operation_dependency_ids": list(step.dependency_ids),
        "target_type": step.target_type,
        "target_id": step.target_id,
        "capability_id": step.capability_id,
        "prepared_operation_hash": step.prepared_operation_hash,
    }
    if any(declaration.get(key) != value for key, value in expected.items()):
        return False
    digest = declaration.get("declaration_hash")
    if digest is not None:
        base = {key: value for key, value in declaration.items() if key != "declaration_hash"}
        if digest != canonical_hash(base):
            return False
    return all(isinstance(declaration.get(key), str) for key in ("child_id", "request_id", "attempt_id"))


def _record_matches(record: Any, declaration: Mapping[str, Any]) -> bool:
    identity = getattr(record, "identity", None)
    target = getattr(record, "target", None)
    return (
        isinstance(identity, Mapping)
        and identity.get("plan_id") == declaration["plan_id"]
        and identity.get("task_id") == declaration["child_id"]
        and identity.get("request_id") == declaration["request_id"]
        and identity.get("attempt_id") == declaration["attempt_id"]
        and isinstance(target, Mapping)
        and target.get("target_type") == declaration["target_type"]
        and target.get("target_id") == declaration["target_id"]
        and getattr(record, "prepared_operation_hash", None)
        == declaration["prepared_operation_hash"]
    )


def classify_child(
    *,
    plan: PlanBinding,
    step: StepContract,
    declaration: Mapping[str, Any] | None,
    record: Any | None,
    readback: Readback | None = None,
) -> ChildClass:
    """Return the section 3.2.2a class for one child (forward or compensation).

    Only the declaration, the durable record and, for a record that cannot
    decide alone, the authoritative readback are read.  Any contradiction is
    ``indeterminate``: it never grants compensation or rollback authority.
    """

    readback = readback or Readback()
    if record is None:
        return ChildClass(step.operation_id, NOT_ATTEMPTED, "no_record")

    def held(reason: str) -> ChildClass:
        return ChildClass(
            step.operation_id,
            INDETERMINATE,
            reason,
            child_id=None if declaration is None else declaration.get("child_id"),
            dispatched=getattr(record, "dispatch_intent", None) is not None,
        )

    if declaration is None or not _declaration_matches(declaration, step, plan):
        return held("declaration_not_bound_to_plan")
    if not _record_matches(record, declaration):
        return held("record_identity_mismatch")
    try:
        record.validate()
        execution_class = record.execution_class()
    except Exception:
        return held("record_invalid")
    child_id = declaration["child_id"]
    digest = record_hash(record)
    evidence = getattr(record, "evidence", None) or {}
    fingerprint = evidence.get("resulting_state_fingerprint")
    intent = record.dispatch_intent

    def result(ownership: str, reason: str, dispatched: bool) -> ChildClass:
        return ChildClass(step.operation_id, ownership, reason, child_id, digest, dispatched)

    if intent is None:
        if record.dispatch_count != 0:
            return held("no_intent_dispatch_count")
        if execution_class == _CLASS_VERIFIED_NO_DISPATCH:
            events = {item.get("event_type") for item in record.events}
            if NOOP_EVENT in events and fingerprint == step.planned_result_fingerprint:
                return result(ALREADY_PRESENT_VERIFIED, NOOP_EVENT, False)
            return held("noop_result_mismatch")
        if execution_class == _CLASS_TERMINAL_PRE_DISPATCH:
            return result(NOT_APPLIED_DETERMINATE, "refused_before_intent", False)
        if execution_class == _CLASS_PRE_INTENT:
            # Recovery readback never creates write ownership.
            if readback.forward_result_present:
                return result(ALREADY_PRESENT_VERIFIED, "recovered_no_intent_present", False)
            return result(NOT_APPLIED_DETERMINATE, "recovered_no_intent", False)
        return held("no_intent_class_unknown")
    if (
        not isinstance(intent, Mapping)
        or intent.get("provider_arguments_hash") != step.provider_arguments_hash
    ):
        return held("provider_arguments_mismatch")
    if record.dispatch_count != 1:
        return held("dispatch_count_not_one")
    if execution_class != _CLASS_TERMINAL_POST_INTENT or not record.terminal:
        return held("post_intent_not_terminal")
    if record.normalized_outcome == "succeeded_verified":
        if fingerprint == step.planned_result_fingerprint:
            return result(OWNED_VERIFIED, "dispatched_verified", True)
        return held("forward_result_mismatch")
    if (
        record.normalized_outcome in _NOT_APPLIED_POST_INTENT_OUTCOMES
        and readback.not_applied_proven
        and not readback.forward_result_present
    ):
        return result(NOT_APPLIED_DETERMINATE, "dispatched_not_applied_verified", True)
    return held("post_intent_outcome_unproven")


@dataclass
class ForwardAssessment:
    """Result of evaluating the forward classes (3.2.3, 3.2.4, 3.2.9)."""

    outcome: str | None  # a terminal outcome, "compensate" or None (in progress)
    classes: list[ChildClass]
    failed: ChildClass | None = None
    compensation_steps: list[ChildClass] = field(default_factory=list)
    excluded: list[ChildClass] = field(default_factory=list)


COMPENSATE = "compensate"


def assess_forward(classes: Sequence[ChildClass]) -> ForwardAssessment:
    """Apply the section 3.2.3 rule to the forward classes, in declaration order."""

    ordered = list(classes)
    if any(item.ownership == INDETERMINATE for item in ordered):
        return ForwardAssessment(OUTCOME_HELD_INDETERMINATE, ordered)
    failed = next(
        (item for item in ordered if item.ownership == NOT_APPLIED_DETERMINATE), None
    )
    owned = [item for item in ordered if item.ownership == OWNED_VERIFIED]
    noops = [item for item in ordered if item.ownership == ALREADY_PRESENT_VERIFIED]
    if failed is None:
        if any(item.ownership == NOT_ATTEMPTED for item in ordered):
            return ForwardAssessment(None, ordered)
        outcome = OUTCOME_SUCCEEDED if owned else OUTCOME_ALREADY_PRESENT
        return ForwardAssessment(outcome, ordered)
    if not owned:
        return ForwardAssessment(OUTCOME_FAILED_NO_WRITES, ordered, failed, [], noops)
    return ForwardAssessment(COMPENSATE, ordered, failed, list(reversed(owned)), noops)


def build_compensation_decision(
    *,
    plan: PlanBinding,
    sequence_hash: str,
    assessment: ForwardAssessment,
    compensation_hashes: Mapping[str, str],
    reason: str,
) -> dict[str, Any]:
    """Return the durable ``compensation_decided`` authority record."""

    if assessment.outcome != COMPENSATE or assessment.failed is None:
        raise ValueError("compensation is not decided for this assessment")
    entries = []
    for item in assessment.compensation_steps:
        if item.ownership != OWNED_VERIFIED or item.child_id is None or item.record_hash is None:
            raise ValueError("only owned verified children are compensated")
        prepared = compensation_hashes.get(item.operation_id)
        if not isinstance(prepared, str) or len(prepared) != 64:
            raise ValueError("compensation step is not precomputed")
        entries.append(
            {
                "operation_id": item.operation_id,
                "forward_child_id": item.child_id,
                "forward_record_hash": item.record_hash,
                "compensation_prepared_operation_hash": prepared,
            }
        )
    decision = {
        "model": DECISION_MODEL,
        "plan_id": plan.plan_id,
        "plan_hash": plan.plan_hash,
        "task_id": plan.public_task_id,
        "sequence_hash": sequence_hash,
        "failed_operation_id": assessment.failed.operation_id,
        "failed_child_id": assessment.failed.child_id,
        "reason": reason,
        "compensation_set": entries,
        "excluded_from_compensation": [
            {"operation_id": item.operation_id, "reason": EXCLUDED_NOT_DISPATCHED}
            for item in assessment.excluded
        ],
    }
    decision["decision_hash"] = canonical_hash(decision)
    return decision


def verify_decision_unchanged(persisted: Mapping[str, Any], rederived: Mapping[str, Any]) -> None:
    """Idempotent re-entry: a persisted decision is never recomputed (3.2.8)."""

    base = {key: value for key, value in persisted.items() if key != "decision_hash"}
    if (
        persisted.get("decision_hash") != canonical_hash(base)
        or dict(persisted) != dict(rederived)
    ):
        raise CompensationDecisionMismatchError(COMPENSATION_DECISION_MISMATCH)


@dataclass
class CompensationHooks:
    """Runtime-supplied effects.  Each is called at most as documented.

    ``persist_decision(decision)``: atomic durable write; called once,
    before any compensation intent, and only when no decision is persisted.
    ``transfer_locks()``: ``f3/locks.py`` ``transfer_complete`` of the
    complete owner set; ``False`` stops with ``compensation_locks_lost``.
    ``bundle_check(entry)``: the full-bundle state check (HAMCP-135) before
    each compensation child; ``False`` stops with ``compensation_drift``.
    ``run_child(entry)``: one governed F3 compensation child (own intent,
    Core admission, at most one dispatch, readback); returns its class.
    ``existing_child(entry)``: the class of an already-recorded compensation
    child on re-entry (``None`` when none); a recorded child is never re-sent.
    """

    persist_decision: Callable[[dict[str, Any]], None]
    transfer_locks: Callable[[], bool]
    bundle_check: Callable[[Mapping[str, Any]], bool]
    run_child: Callable[[Mapping[str, Any]], ChildClass]
    existing_child: Callable[[Mapping[str, Any]], ChildClass | None] = lambda _entry: None


@dataclass
class CompensationResult:
    outcome: str
    stop_reason: str | None
    compensated: list[str]
    step_states: list[dict[str, Any]]
    child_runs: int


def run_compensation(
    decision: Mapping[str, Any],
    hooks: CompensationHooks,
    *,
    persisted: Mapping[str, Any] | None = None,
) -> CompensationResult:
    """Run the decided compensation set once, in its persisted order."""

    if persisted is not None:
        verify_decision_unchanged(persisted, decision)
    else:
        hooks.persist_decision(dict(decision))
    entries = list(decision["compensation_set"])
    states: list[dict[str, Any]] = []
    compensated: list[str] = []
    runs = 0

    def stop(reason: str, outcome: str = OUTCOME_COMPENSATION_INCOMPLETE) -> CompensationResult:
        for entry in entries[len(states):]:
            states.append({"operation_id": entry["operation_id"], "state": NOT_ATTEMPTED})
        return CompensationResult(outcome, reason, compensated, states, runs)

    if not hooks.transfer_locks():
        return stop(COMPENSATION_LOCKS_LOST)
    for entry in entries:
        existing = hooks.existing_child(entry)
        if existing is not None:
            if existing.ownership == OWNED_VERIFIED:
                compensated.append(entry["operation_id"])
                states.append({"operation_id": entry["operation_id"], "state": OWNED_VERIFIED})
                continue
            states.append({"operation_id": entry["operation_id"], "state": existing.ownership})
            return stop(
                COMPENSATION_STEP_INDETERMINATE
                if existing.ownership in {INDETERMINATE, NOT_ATTEMPTED}
                else COMPENSATION_STEP_FAILED
            )
        if not hooks.bundle_check(entry):
            return stop(COMPENSATION_DRIFT)
        runs += 1
        child = hooks.run_child(entry)
        states.append({"operation_id": entry["operation_id"], "state": child.ownership})
        if child.ownership != OWNED_VERIFIED:
            return stop(
                COMPENSATION_STEP_INDETERMINATE
                if child.ownership == INDETERMINATE
                else COMPENSATION_STEP_FAILED
            )
        compensated.append(entry["operation_id"])
    return CompensationResult(OUTCOME_COMPENSATED, None, compensated, states, runs)


_OUTCOME_TASK_STATE = {
    OUTCOME_SUCCEEDED: TASK_STATE_SUCCEEDED,
    OUTCOME_ALREADY_PRESENT: TASK_STATE_SUCCEEDED,
    OUTCOME_COMPENSATED: TASK_STATE_FAILED_POST_DISPATCH,
    OUTCOME_COMPENSATION_INCOMPLETE: TASK_STATE_MANUAL_REVIEW,
    OUTCOME_HELD_INDETERMINATE: TASK_STATE_MANUAL_REVIEW,
}


def terminal_projection(
    outcome: str,
    classes: Iterable[ChildClass],
    *,
    compensated: Iterable[str] = (),
    stop_reason: str | None = None,
) -> dict[str, Any]:
    """Return the section 3.2.9 terminal outcome, task state and accounting.

    Projection only: none of these keys is ever an authority input.
    """

    if outcome not in TERMINAL_OUTCOMES:
        raise ValueError("terminal outcome is unknown")
    ordered = list(classes)
    if outcome == OUTCOME_FAILED_NO_WRITES:
        failed = next(item for item in ordered if item.ownership == NOT_APPLIED_DETERMINATE)
        task_state = (
            TASK_STATE_FAILED_POST_DISPATCH if failed.dispatched else TASK_STATE_FAILED_PRE_DISPATCH
        )
    else:
        task_state = _OUTCOME_TASK_STATE[outcome]

    def ids(ownership: str) -> list[str]:
        return [item.operation_id for item in ordered if item.ownership == ownership]

    noops = ids(ALREADY_PRESENT_VERIFIED)
    if outcome == OUTCOME_SUCCEEDED:
        status = "applied"
    elif outcome == OUTCOME_ALREADY_PRESENT:
        status = "already_present"
    else:
        status = "failed" if task_state != TASK_STATE_MANUAL_REVIEW else "manual_review_required"
    projection: dict[str, Any] = {
        "terminal_outcome": outcome,
        "task_state": task_state,
        "status": status,
        "owned_operation_ids": ids(OWNED_VERIFIED),
        "noop_operation_ids": noops,
        "compensated_operation_ids": list(compensated),
        "failed_operation_ids": ids(NOT_APPLIED_DETERMINATE),
        "unresolved_operation_ids": ids(INDETERMINATE),
        "not_attempted_operation_ids": ids(NOT_ATTEMPTED),
        "steps": [
            {
                "operation_id": item.operation_id,
                "class": item.ownership,
                "changed_by_engineering": item.changed_by_engineering,
                "provider_dispatch_occurred": item.dispatched,
                "reason": item.reason,
            }
            for item in ordered
        ],
    }
    if outcome in {OUTCOME_COMPENSATED, OUTCOME_COMPENSATION_INCOMPLETE, OUTCOME_FAILED_NO_WRITES}:
        projection["excluded_from_compensation"] = [
            {"operation_id": operation_id, "reason": EXCLUDED_NOT_DISPATCHED}
            for operation_id in noops
        ]
    if stop_reason is not None:
        projection["stop_reason"] = stop_reason
    return projection


def dependencies_satisfied(
    step: StepContract, classes: Mapping[str, ChildClass]
) -> bool:
    """A step runs only after every dependency is owned or already present."""

    return all(
        dependency in classes
        and classes[dependency].ownership in DEPENDENCY_SATISFYING_CLASSES
        for dependency in step.dependency_ids
    )


__all__ = [
    "ALREADY_PRESENT_VERIFIED",
    "COMPENSATE",
    "COMPENSATION_DECISION_MISMATCH",
    "COMPENSATION_DRIFT",
    "COMPENSATION_LOCKS_LOST",
    "ChildClass",
    "CompensationDecisionMismatchError",
    "CompensationHooks",
    "CompensationResult",
    "ForwardAssessment",
    "INDETERMINATE",
    "NOT_APPLIED_DETERMINATE",
    "NOT_ATTEMPTED",
    "OWNED_VERIFIED",
    "PlanBinding",
    "Readback",
    "StepContract",
    "assess_forward",
    "build_compensation_decision",
    "classify_child",
    "dependencies_satisfied",
    "record_hash",
    "run_compensation",
    "terminal_projection",
    "verify_decision_unchanged",
]
