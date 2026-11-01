"""HAMCP-181 PR1: one shared, display-only dispatch-facts projection.

Every public surface that says whether a provider call was dispatched derives
its answer from this module.  The projection reads only stored F3 execution
records and the stored public task.  It never reads the clock, never writes,
and is never dispatch, retry, recovery, cancellation, claim or rollback
authority: eligibility keeps its own stored-intent predicates.

Records are passed in duck-typed by ``f3_runtime`` callers; this module does
not import the ``f3`` package (packaging boundary).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from ..governance.entity_metadata import (
    ENTITY_METADATA_CAPABILITY_IDENTITY,
    ENTITY_METADATA_NOT_DISPATCHED_OUTCOME,
    ENTITY_METADATA_NOT_DISPATCHED_TEXT,
)

# Child and parent phases, ranked from the most uncertain to the least
# (design section 2.1 rule 1).  The highest-ranked phase present wins.
PHASE_SEND_UNCERTAIN = "intent_recorded_send_uncertain"
PHASE_SENT_FAILED_MUTATION_UNPROVEN = "sent_failed_mutation_unproven"
PHASE_SENT_RESPONSE_RECEIVED = "sent_response_received"
PHASE_SENT_REJECTED_NO_MUTATION = "sent_rejected_no_mutation"
PHASE_INTENT_RECORDED_NOT_SENT = "intent_recorded_not_sent"
PHASE_PRE_INTENT = "not_dispatched_pre_intent"
PHASE_ALREADY_PRESENT = "not_dispatched_already_present"
PHASE_RANK: tuple[str, ...] = (
    PHASE_SEND_UNCERTAIN,
    PHASE_SENT_FAILED_MUTATION_UNPROVEN,
    PHASE_SENT_RESPONSE_RECEIVED,
    PHASE_SENT_REJECTED_NO_MUTATION,
    PHASE_INTENT_RECORDED_NOT_SENT,
    PHASE_PRE_INTENT,
    PHASE_ALREADY_PRESENT,
)
_RANK = {phase: index for index, phase in enumerate(PHASE_RANK)}
NO_INTENT_PHASES = frozenset({PHASE_PRE_INTENT, PHASE_ALREADY_PRESENT})
NOT_SENT_PHASES = NO_INTENT_PHASES | {PHASE_INTENT_RECORDED_NOT_SENT}
# Design section 2.2: only these phases add the outcome detail fields
# (``changed_by_engineering``, ``intent_recorded``, ``dispatch_performed`` and
# ``provider_response_received``).  Sent, send-uncertain and pre-intent
# objects keep today's fields plus the phase and the counter basis.
DETAIL_PHASES = frozenset({
    PHASE_ALREADY_PRESENT,
    PHASE_INTENT_RECORDED_NOT_SENT,
    PHASE_SENT_REJECTED_NO_MUTATION,
    PHASE_SENT_FAILED_MUTATION_UNPROVEN,
})
SENT_PHASES = frozenset(
    {
        PHASE_SENT_FAILED_MUTATION_UNPROVEN,
        PHASE_SENT_RESPONSE_RECEIVED,
        PHASE_SENT_REJECTED_NO_MUTATION,
    }
)

# Counter bases (section 2.4.1).  The no-setter value is kept verbatim from
# HAMCP-177 (#253).
BASIS_NO_INTENT = "no_dispatch_intent"
BASIS_SAVED_INTENT = "saved_dispatch_intent"
BASIS_NO_SETTER_CALL = "saved_dispatch_intent_no_setter_call"
BASIS_NO_PROVIDER_CALL = "saved_dispatch_intent_no_provider_call"
ATTEMPT_COUNT_SCOPE_PARENT = "parent_sum_of_child_intents"

# dispatched_at bases (section 2.4.2, OD-6(ii)).
DISPATCHED_AT_NO_INTENT = "no_dispatch_intent"
DISPATCHED_AT_CALL_NOT_SENT = "call_not_sent"
DISPATCHED_AT_SEND_TIME_NOT_RECORDED = "send_time_not_recorded"
DISPATCHED_AT_INTENT_COMMIT_TIME = "dispatch_intent_commit_time"
# HAMCP-181 PR2 (OD-6(ii)): the exact send time, captured at the provider send
# boundary (immediately before the provider client issues the one mutating
# request, never at gateway entry), stored by the runtime in the task verification summary
# map ``SEND_TIME_SUMMARY_KEY`` (child id -> time or ``null``) beside
# ``children``, whose entries keep their shipped shape.  Published as
# ``dispatched_at`` only for a child whose send is proven (a sent phase).  A
# child listed in the map marks a PR2-written task: without a stored send time
# its ``dispatched_at`` is ``null``, never the intent commit time.
DISPATCHED_AT_PROVIDER_CALL_START = "provider_call_start_time"
SEND_TIME_SUMMARY_KEY = "provider_call_started_at"
# A parent (task, apply result or receipt) states the scope of its one
# ``dispatched_at``: the earliest stored send time among its sent children.
DISPATCHED_AT_SCOPE_PARENT = "earliest_child_send_time"

# HAMCP-181 PR2 (T2, OD-5): the stored terminal outcome for (b).
NOT_SENT_OUTCOME = "failed_after_intent_not_dispatched"

# Recorded F3 diagnostic codes read by the predicates.
DASHBOARD_CAPABILITY_ID = "update_existing_dashboard"
SETTER_CALL_NOT_SENT = "setter_call_not_sent"
PROVIDER_CALL_NOT_SENT = "provider_call_not_sent"
PROVIDER_REJECTED_MUTATION = "provider_rejected_mutation"
PROVIDER_PERMISSION_DENIED = "provider_permission_denied"
DISPATCH_RESULT_EVENT = "dispatch_result_recorded"
_B_CONFLICTING_CODES = frozenset(
    {
        PROVIDER_REJECTED_MUTATION,
        "provider_rejection_confirmed",
        # HAMCP-145: Core answered the select_option send; readback decides.
        "provider_rejection_readback_decides",
        "provider_completed_unexpectedly",
        "provider_response_indeterminate",
        "structured_provider_rejection_received",
        "readback_only_recovery",
    }
)
# Design section 3 (c): the adapters' uncertainty codes on a dispatch result.
_C_UNCERTAINTY_CODES = frozenset(
    {
        "provider_result_uncertain",
        "fan_response_uncertain",
        "provider_response_indeterminate",
    }
)
_R_PROVEN_CONFLICTING_CODES = frozenset(
    {
        PROVIDER_CALL_NOT_SENT,
        SETTER_CALL_NOT_SENT,
        "provider_completed_unexpectedly",
    }
)
# P1: configuration REST write, automation and script only.
_P1_TARGET_TYPES = frozenset({"automation", "script"})
# P3: entity metadata command answered unauthorized/forbidden.
# HAMCP-069B: both 069B writes are ``require_admin`` before their handler
# (Core 2026.10.1), so an unauthorized result proves no mutation there too.
_P3_TARGET_TYPES = frozenset(
    {
        "entity_registry_metadata",
        "entity_registry_availability",
        "entity_assistant_exposure",
        # HAMCP-165: label create and update are ``require_admin`` too.
        "label_registry_entry",
        # HAMCP-106: ``lovelace/dashboards/update`` is ``require_admin``
        # before its handler (Core 2026.10 helpers/collection.py).
        "dashboard_metadata",
        # HAMCP-071: ``/api/config/scene/config/<id>`` is ``require_admin``
        # before its handler (Core 2026.10 config/view.py).
        "scene_configuration",
    }
)
# V9 applies to every code-owned entity registry write identity.
ALREADY_PRESENT_CAPABILITY_IDENTITIES = frozenset(
    {
        ENTITY_METADATA_CAPABILITY_IDENTITY,
        "update_entity_registry_availability_configuration",
        "update_entity_assistant_exposure_configuration",
        "update_label_registry_entry_configuration",
        # HAMCP-106: a dashboard already showing the planned metadata.
        "update_dashboard_metadata_configuration",
        # HAMCP-071: a scene already storing the planned configuration.
        "update_scene_configuration_configuration",
    }
)
_VERIFIED_NO_DISPATCH_CLASS = "verified_no_dispatch"

ALREADY_PRESENT_OUTCOME = ENTITY_METADATA_NOT_DISPATCHED_OUTCOME
ALREADY_PRESENT_TEXT = ENTITY_METADATA_NOT_DISPATCHED_TEXT
NOT_SENT_TEXT = (
    "a dispatch intent was recorded but the provider call was not sent; "
    "Engineering did not make this change."
)
REJECTED_NO_MUTATION_TEXT = (
    "Home Assistant rejected the request; the change was not applied"
)
FAILED_MUTATION_UNPROVEN_TEXT = (
    "the request was sent and failed; it is not proven that nothing changed"
)


@dataclass(frozen=True)
class ChildFacts:
    """The projected facts of one F3 child (one execution record)."""

    child_id: str | None
    capability: str | None
    phase: str
    verified: bool = False
    setter_proof: bool = False
    dispatch_count: int = 0
    dispatch_intent_recorded_at: str | None = None
    provider_response_recorded_at: str | None = None
    evidence_conflict: bool = False
    send_time_captured: bool = False
    provider_call_started_at: str | None = None

    @property
    def attempt_counts_basis(self) -> str:
        if self.phase in NO_INTENT_PHASES:
            return BASIS_NO_INTENT
        if self.phase == PHASE_INTENT_RECORDED_NOT_SENT:
            return BASIS_NO_SETTER_CALL if self.setter_proof else BASIS_NO_PROVIDER_CALL
        return BASIS_SAVED_INTENT

    @property
    def changed_by_engineering(self) -> bool | None:
        if self.phase in NOT_SENT_PHASES or self.phase == PHASE_SENT_REJECTED_NO_MUTATION:
            return False
        if self.phase == PHASE_SENT_RESPONSE_RECEIVED and self.verified:
            return True
        return None

    def public_fields(self) -> dict[str, Any]:
        """Additive per-child qualifiers placed beside a child counter."""

        value: dict[str, Any] = {
            "dispatch_phase": self.phase,
            "attempt_counts_basis": self.attempt_counts_basis,
        }
        if self.phase in DETAIL_PHASES:
            value["changed_by_engineering"] = self.changed_by_engineering
        value["dispatched_at_basis"] = _governed_child_basis(self)
        value["dispatch_intent_recorded_at"] = self.dispatch_intent_recorded_at
        value["provider_response_recorded_at"] = self.provider_response_recorded_at
        return value


@dataclass(frozen=True)
class DispatchFacts:
    """The projected facts of a parent task, an apply result or a receipt."""

    dispatch_phase: str
    provider_dispatch_occurred: bool
    changed_by_engineering: bool | None
    intent_recorded: bool
    attempt_counts_basis: str
    dispatched_at: str | None
    dispatched_at_basis: str
    dispatch_intent_recorded_at: str | None
    provider_response_recorded_at: str | None
    already_present: bool = False
    not_sent: bool = False
    setter_not_sent: bool = False
    is_parent: bool = False
    children: tuple[ChildFacts, ...] = ()
    child_dispatch_phases: Mapping[str, int] = field(default_factory=dict)

    @property
    def dispatch_performed(self) -> bool | None:
        if self.dispatch_phase in NOT_SENT_PHASES:
            return False
        if self.dispatch_phase in SENT_PHASES:
            return True
        return None

    @property
    def outcome_code(self) -> str | None:
        if self.already_present:
            return ALREADY_PRESENT_OUTCOME
        if self.not_sent:
            return NOT_SENT_OUTCOME
        if self.dispatch_phase == PHASE_SENT_REJECTED_NO_MUTATION:
            return PROVIDER_REJECTED_MUTATION
        return None

    @property
    def result_text(self) -> str | None:
        if self.already_present:
            return ALREADY_PRESENT_TEXT
        if self.not_sent:
            return NOT_SENT_TEXT
        if self.dispatch_phase == PHASE_SENT_REJECTED_NO_MUTATION:
            return REJECTED_NO_MUTATION_TEXT
        if self.dispatch_phase == PHASE_SENT_FAILED_MUTATION_UNPROVEN:
            return FAILED_MUTATION_UNPROVEN_TEXT
        return None

    def child_basis(self, child_id: Any) -> str | None:
        for child in self.children:
            if child.child_id is not None and child.child_id == child_id:
                return child.attempt_counts_basis
        return None

    def child(self, child_id: Any) -> ChildFacts | None:
        for child in self.children:
            if child.child_id is not None and child.child_id == child_id:
                return child
        return None

    def public_fields(self, *, include_dispatched_at: bool = True) -> dict[str, Any]:
        """The client fields for this phase (design section 2.2).

        Fields absent for a phase are omitted, never invented.  ``status`` is
        never produced here (status preservation rule, section 2.4.3 item 4).
        """

        value: dict[str, Any] = {
            "dispatch_phase": self.dispatch_phase,
            "provider_dispatch_occurred": self.provider_dispatch_occurred,
            "attempt_counts_basis": self.attempt_counts_basis,
        }
        if self.dispatch_phase in DETAIL_PHASES:
            value["changed_by_engineering"] = self.changed_by_engineering
            value["intent_recorded"] = self.intent_recorded
            if self.dispatch_performed is not None:
                value["dispatch_performed"] = self.dispatch_performed
            if self.dispatch_phase in SENT_PHASES:
                value["provider_response_received"] = True
        if self.not_sent:
            if self.setter_not_sent:
                value[SETTER_CALL_NOT_SENT] = True
            else:
                value[PROVIDER_CALL_NOT_SENT] = True
            value["retryable"] = False
        if self.outcome_code is not None:
            value["outcome_code"] = self.outcome_code
        if self.result_text is not None:
            value["result_text"] = self.result_text
        if self.is_parent:
            value["attempt_count_scope"] = ATTEMPT_COUNT_SCOPE_PARENT
            value["child_dispatch_phases"] = dict(self.child_dispatch_phases)
        if include_dispatched_at:
            value["dispatched_at"] = self.dispatched_at
        value["dispatched_at_basis"] = self.dispatched_at_basis
        if self.is_parent:
            value["dispatched_at_scope"] = DISPATCHED_AT_SCOPE_PARENT
        value["dispatch_intent_recorded_at"] = self.dispatch_intent_recorded_at
        value["provider_response_recorded_at"] = self.provider_response_recorded_at
        return value


def _codes(event: Mapping[str, Any]) -> frozenset[str]:
    raw = event.get("diagnostic_codes") or ()
    if not isinstance(raw, (list, tuple)):
        return frozenset()
    return frozenset(item for item in raw if isinstance(item, str))


def _dispatch_result_event(record: Any) -> Mapping[str, Any] | None:
    found = None
    for event in getattr(record, "events", ()) or ():
        if isinstance(event, Mapping) and event.get("event_type") == DISPATCH_RESULT_EVENT:
            found = event
    return found


def _target_type(record: Any) -> str | None:
    target = getattr(record, "target", None)
    if isinstance(target, Mapping):
        value = target.get("target_type")
        return value if isinstance(value, str) else None
    return None


def record_already_present(record: Any) -> bool:
    """Case (a): a verified no-dispatch record (class VERIFIED_NO_DISPATCH)."""

    if record is None or getattr(record, "dispatch_intent", None) is not None:
        return False
    if getattr(record, "dispatch_count", None) != 0:
        return False
    if not any(
        isinstance(event, Mapping) and event.get("event_type") == "preflight_noop_verified"
        for event in getattr(record, "events", ()) or ()
    ):
        return False
    try:
        return record.execution_class() == _VERIFIED_NO_DISPATCH_CLASS
    except Exception:
        return False


def child_facts(
    record: Any,
    *,
    capability: str | None = None,
    child_id: str | None = None,
    missing: bool = False,
) -> ChildFacts:
    """Project one child.  Every intent defaults to (c) unless proven."""

    if record is None:
        return ChildFacts(
            child_id=child_id,
            capability=capability,
            phase=PHASE_SEND_UNCERTAIN if missing else PHASE_PRE_INTENT,
            evidence_conflict=missing,
        )
    capability = capability or getattr(record, "operation", None)
    intent = getattr(record, "dispatch_intent", None)
    dispatch_count = getattr(record, "dispatch_count", 0)
    if intent is None:
        if record_already_present(record):
            phase = PHASE_ALREADY_PRESENT
            conflict = False
        else:
            try:
                record.execution_class()
                phase, conflict = PHASE_PRE_INTENT, False
            except Exception:
                phase, conflict = PHASE_SEND_UNCERTAIN, True
        return ChildFacts(
            child_id=child_id,
            capability=capability,
            phase=phase,
            dispatch_count=dispatch_count if isinstance(dispatch_count, int) else 0,
            evidence_conflict=conflict,
        )
    committed_at = intent.get("committed_at") if isinstance(intent, Mapping) else None
    committed_at = committed_at if isinstance(committed_at, str) else None
    response = getattr(record, "provider_response_received", False) is True
    outcome = getattr(record, "normalized_outcome", None)
    result_event = _dispatch_result_event(record)
    codes = _codes(result_event) if result_event is not None else frozenset()
    response_recorded_at = None
    if response and result_event is not None:
        occurred = result_event.get("occurred_at")
        response_recorded_at = occurred if isinstance(occurred, str) else None
    base = {
        "child_id": child_id,
        "capability": capability,
        "dispatch_count": dispatch_count if isinstance(dispatch_count, int) else 0,
        "dispatch_intent_recorded_at": committed_at,
        "provider_response_recorded_at": response_recorded_at,
    }
    try:
        record.execution_class()
    except Exception:
        return ChildFacts(phase=PHASE_SEND_UNCERTAIN, evidence_conflict=True, **base)
    is_dashboard = capability == DASHBOARD_CAPABILITY_ID
    setter_code = SETTER_CALL_NOT_SENT in codes
    provider_code = PROVIDER_CALL_NOT_SENT in codes
    if setter_code and not is_dashboard:
        # setter_call_not_sent is accepted only for the dashboard capability.
        return ChildFacts(phase=PHASE_SEND_UNCERTAIN, evidence_conflict=True, **base)
    if (setter_code or provider_code) and response:
        # A proof code with a received response is a contradiction.
        return ChildFacts(phase=PHASE_SEND_UNCERTAIN, evidence_conflict=True, **base)
    if outcome == "dispatch_failed_confirmed" and not response:
        if (setter_code or provider_code) and not (codes & _B_CONFLICTING_CODES):
            return ChildFacts(
                phase=PHASE_INTENT_RECORDED_NOT_SENT,
                setter_proof=setter_code,
                **base,
            )
        if setter_code or provider_code:
            return ChildFacts(phase=PHASE_SEND_UNCERTAIN, evidence_conflict=True, **base)
        return ChildFacts(phase=PHASE_SEND_UNCERTAIN, **base)
    if outcome == "dispatch_failed_confirmed" and response:
        target_type = _target_type(record)
        proven = (
            PROVIDER_REJECTED_MUTATION in codes
            and not (codes & _R_PROVEN_CONFLICTING_CODES)
            and (
                target_type in _P1_TARGET_TYPES
                or (
                    target_type in _P3_TARGET_TYPES
                    and PROVIDER_PERMISSION_DENIED in codes
                )
            )
        )
        return ChildFacts(
            phase=(
                PHASE_SENT_REJECTED_NO_MUTATION
                if proven
                else PHASE_SENT_FAILED_MUTATION_UNPROVEN
            ),
            **base,
        )
    if codes & _C_UNCERTAINTY_CODES:
        # (c) stays send-uncertain even when a response arrived, e.g. the
        # OD-15 entity-registry error result after send (HAMCP-183); readback
        # never upgrades the dispatch phase.
        return ChildFacts(phase=PHASE_SEND_UNCERTAIN, **base)
    if response:
        return ChildFacts(
            phase=PHASE_SENT_RESPONSE_RECEIVED,
            verified=outcome == "succeeded_verified",
            **base,
        )
    return ChildFacts(phase=PHASE_SEND_UNCERTAIN, **base)


def is_dashboard_setter_not_sent(capability: Any, record: Any) -> bool:
    """HAMCP-177 #253 predicate as a thin wrapper over the (b) predicate."""

    if capability != DASHBOARD_CAPABILITY_ID or record is None:
        return False
    facts = child_facts(record, capability=capability)
    return facts.phase == PHASE_INTENT_RECORDED_NOT_SENT and facts.setter_proof


def is_entity_metadata_already_present(
    declarations: Sequence[Mapping[str, Any]], records: Sequence[Any]
) -> bool:
    """V9 predicate as a thin wrapper over the (a) predicate."""

    if not declarations or len(declarations) != len(records):
        return False
    return all(
        declaration["capability_id"] in ALREADY_PRESENT_CAPABILITY_IDENTITIES
        and record_already_present(record)
        for declaration, record in zip(declarations, records, strict=True)
    )


def _parse(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _earliest(values: Iterable[str | None]) -> str | None:
    present = [item for item in values if isinstance(item, str)]
    if not present:
        return None
    parsed = [(_parse(item), item) for item in present]
    if all(stamp is not None for stamp, _ in parsed):
        try:
            return min(parsed, key=lambda pair: pair[0])[1]
        except TypeError:
            pass
    return min(present)


def _governed_child_basis(child: ChildFacts) -> str:
    if child.phase in NO_INTENT_PHASES:
        return DISPATCHED_AT_NO_INTENT
    if child.phase == PHASE_INTENT_RECORDED_NOT_SENT:
        return DISPATCHED_AT_CALL_NOT_SENT
    if child.send_time_captured:
        return (
            DISPATCHED_AT_PROVIDER_CALL_START
            if child.phase in SENT_PHASES
            and child.provider_call_started_at is not None
            else DISPATCHED_AT_SEND_TIME_NOT_RECORDED
        )
    if child.dispatch_intent_recorded_at is None:
        return DISPATCHED_AT_SEND_TIME_NOT_RECORDED
    return DISPATCHED_AT_INTENT_COMMIT_TIME


@dataclass(frozen=True)
class Aggregate:
    phase: str
    provider_dispatch_occurred: bool
    changed_by_engineering: bool | None
    all_already_present: bool
    all_not_sent: bool
    setter_not_sent: bool


def aggregate(children: Sequence[ChildFacts]) -> Aggregate:
    """The single parent aggregation rule (section 2.1 rules 1-5)."""

    if not children:
        children = (ChildFacts(None, None, PHASE_SEND_UNCERTAIN, evidence_conflict=True),)
    phases = [child.phase for child in children]
    phase = min(phases, key=lambda item: _RANK[item])
    provider_dispatch_occurred = not all(item in NOT_SENT_PHASES for item in phases)
    if any(
        child.phase in {PHASE_SEND_UNCERTAIN, PHASE_SENT_FAILED_MUTATION_UNPROVEN}
        or (child.phase == PHASE_SENT_RESPONSE_RECEIVED and not child.verified)
        for child in children
    ):
        changed: bool | None = None
    elif any(
        child.phase == PHASE_SENT_RESPONSE_RECEIVED and child.verified
        for child in children
    ):
        changed = True
    else:
        changed = False
    # Rule 4: the no-dispatch predicates are checked directly over every
    # child, never inferred from the max-phase selection alone.
    all_already_present = all(item == PHASE_ALREADY_PRESENT for item in phases)
    intent_bearing = [child for child in children if child.phase not in NO_INTENT_PHASES]
    all_not_sent = bool(intent_bearing) and all(
        child.phase == PHASE_INTENT_RECORDED_NOT_SENT for child in intent_bearing
    )
    if (phase == PHASE_ALREADY_PRESENT) != all_already_present or (
        phase == PHASE_INTENT_RECORDED_NOT_SENT
    ) != all_not_sent:
        # Defensive: a disagreement can only come from an unknown phase.
        return Aggregate(PHASE_SEND_UNCERTAIN, True, None, False, False, False)
    return Aggregate(
        phase=phase,
        provider_dispatch_occurred=provider_dispatch_occurred,
        changed_by_engineering=changed,
        all_already_present=all_already_present,
        all_not_sent=all_not_sent,
        setter_not_sent=all_not_sent
        and all(child.setter_proof for child in intent_bearing),
    )


def _parent_basis(agg: Aggregate) -> str:
    if agg.phase in NO_INTENT_PHASES:
        return BASIS_NO_INTENT
    if agg.phase == PHASE_INTENT_RECORDED_NOT_SENT:
        return BASIS_NO_SETTER_CALL if agg.setter_not_sent else BASIS_NO_PROVIDER_CALL
    return BASIS_SAVED_INTENT


def _phase_counts(children: Sequence[ChildFacts]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for phase in PHASE_RANK:
        count = sum(1 for child in children if child.phase == phase)
        if count:
            counts[phase] = count
    return counts


def dispatch_facts(
    *,
    task: Any,
    children: Sequence[ChildFacts] | None,
) -> DispatchFacts:
    """Parent facts for a governed F3 task.

    ``children`` is ``None`` when the child manifest cannot be established;
    the parent then counts as a single (c) child.
    """

    established = children is not None and len(children) > 0
    resolved: tuple[ChildFacts, ...] = (
        _with_send_times(tuple(children), task)
        if established
        else (ChildFacts(None, None, PHASE_SEND_UNCERTAIN, evidence_conflict=True),)
    )
    agg = aggregate(resolved)
    stored_outcome = getattr(task, "terminal_outcome", None)
    if (stored_outcome == NOT_SENT_OUTCOME and not agg.all_not_sent) or (
        stored_outcome == ALREADY_PRESENT_OUTCOME and not agg.all_already_present
    ):
        # A stored no-dispatch outcome that the child authority does not
        # reproduce is an evidence conflict: never claim the stronger fact.
        agg = Aggregate(PHASE_SEND_UNCERTAIN, True, None, False, False, False)
    stored_dispatched_at = getattr(task, "dispatched_at", None)
    send_times = [
        child.provider_call_started_at
        for child in resolved
        if child.phase in SENT_PHASES and child.provider_call_started_at is not None
    ]
    if agg.phase in NO_INTENT_PHASES:
        dispatched_at = stored_dispatched_at
        basis = DISPATCHED_AT_NO_INTENT
    elif agg.phase == PHASE_INTENT_RECORDED_NOT_SENT:
        dispatched_at = None
        basis = DISPATCHED_AT_CALL_NOT_SENT
    elif any(child.send_time_captured for child in resolved):
        # PR2-written task (OD-6(ii)): the exact stored send time of the
        # earliest proven send, or ``null`` until one is stored.
        dispatched_at = _earliest(send_times)
        basis = (
            DISPATCHED_AT_PROVIDER_CALL_START
            if dispatched_at is not None
            else DISPATCHED_AT_SEND_TIME_NOT_RECORDED
        )
    else:
        dispatched_at = stored_dispatched_at
        basis = (
            DISPATCHED_AT_INTENT_COMMIT_TIME
            if dispatched_at is not None
            else DISPATCHED_AT_SEND_TIME_NOT_RECORDED
        )
    return DispatchFacts(
        dispatch_phase=agg.phase,
        provider_dispatch_occurred=agg.provider_dispatch_occurred,
        changed_by_engineering=agg.changed_by_engineering,
        intent_recorded=agg.phase not in NO_INTENT_PHASES,
        attempt_counts_basis=_parent_basis(agg),
        dispatched_at=dispatched_at,
        dispatched_at_basis=basis,
        dispatch_intent_recorded_at=_earliest(
            child.dispatch_intent_recorded_at for child in resolved
        ),
        provider_response_recorded_at=_earliest(
            child.provider_response_recorded_at for child in resolved
        ),
        already_present=agg.all_already_present,
        not_sent=agg.all_not_sent,
        setter_not_sent=agg.setter_not_sent,
        is_parent=True,
        children=resolved if established else (),
        child_dispatch_phases=_phase_counts(resolved),
    )


def _with_send_times(
    children: tuple[ChildFacts, ...], task: Any
) -> tuple[ChildFacts, ...]:
    """Attach the stored per-child send times from the task summary."""

    summary = getattr(task, "verification_summary", None)
    entries = summary.get(SEND_TIME_SUMMARY_KEY) if isinstance(summary, Mapping) else None
    if not isinstance(entries, Mapping):
        return children
    stored: dict[str, Any] = {
        child_id: value
        for child_id, value in entries.items()
        if isinstance(child_id, str)
    }
    if not stored:
        return children
    return tuple(
        replace(
            child,
            send_time_captured=True,
            provider_call_started_at=(
                stored[child.child_id]
                if isinstance(stored[child.child_id], str)
                else None
            ),
        )
        if child.child_id in stored
        else child
        for child in children
    )


def legacy_dispatch_facts(task: Any) -> DispatchFacts:
    """Facts for a task without F3 child authority.

    Keeps the stored-intent value of ``_task_is_dispatched``: any recorded
    dispatch is conservatively (c); nothing else is inferred.
    """

    # A stored (b) outcome without child authority stays (c).
    dispatched = bool(
        getattr(task, "dispatched_at", None)
        or getattr(task, "provider_attempts", None)
        or getattr(task, "terminal_outcome", None) == NOT_SENT_OUTCOME
    )
    if dispatched:
        phase = PHASE_SEND_UNCERTAIN
    elif getattr(task, "terminal_outcome", None) == ALREADY_PRESENT_OUTCOME:
        phase = PHASE_ALREADY_PRESENT
    else:
        phase = PHASE_PRE_INTENT
    child = ChildFacts(None, None, phase)
    agg = aggregate((child,))
    stored = getattr(task, "dispatched_at", None)
    return DispatchFacts(
        dispatch_phase=agg.phase,
        provider_dispatch_occurred=agg.provider_dispatch_occurred,
        changed_by_engineering=agg.changed_by_engineering,
        intent_recorded=dispatched,
        attempt_counts_basis=_parent_basis(agg),
        dispatched_at=stored,
        dispatched_at_basis=(
            DISPATCHED_AT_NO_INTENT
            if not dispatched
            else DISPATCHED_AT_INTENT_COMMIT_TIME
            if stored is not None
            else DISPATCHED_AT_SEND_TIME_NOT_RECORDED
        ),
        dispatch_intent_recorded_at=None,
        provider_response_recorded_at=None,
        already_present=agg.all_already_present,
    )


def dispatch_facts_for_record(record: Any, *, capability: str | None = None) -> DispatchFacts:
    """Facts for a fan/power execution record (no governance task).

    ``dispatched_at`` stays ``None`` (OD-6(ii)): no stored field records the
    provider send instant, and the intent commit time is never a send time.
    """

    child = child_facts(record, capability=capability)
    agg = aggregate((child,))
    if child.phase in NO_INTENT_PHASES:
        basis = DISPATCHED_AT_NO_INTENT
    elif child.phase == PHASE_INTENT_RECORDED_NOT_SENT:
        basis = DISPATCHED_AT_CALL_NOT_SENT
    else:
        basis = DISPATCHED_AT_SEND_TIME_NOT_RECORDED
    return DispatchFacts(
        dispatch_phase=agg.phase,
        provider_dispatch_occurred=agg.provider_dispatch_occurred,
        changed_by_engineering=agg.changed_by_engineering,
        intent_recorded=child.phase not in NO_INTENT_PHASES,
        attempt_counts_basis=child.attempt_counts_basis,
        dispatched_at=None,
        dispatched_at_basis=basis,
        dispatch_intent_recorded_at=child.dispatch_intent_recorded_at,
        provider_response_recorded_at=child.provider_response_recorded_at,
        already_present=agg.all_already_present,
        not_sent=agg.all_not_sent,
        setter_not_sent=agg.setter_not_sent,
        children=(child,),
    )


def qualify_counters(value: dict[str, Any], facts: DispatchFacts) -> dict[str, Any]:
    """Co-locate ``attempt_counts_basis`` with every nested counter.

    Historic elements keep their own child's basis; the parent's final phase
    is not stamped onto earlier events.
    """

    def basis_for(item: Mapping[str, Any]) -> str:
        child_basis = facts.child_basis(item.get("child_execution_id"))
        if child_basis is not None:
            return child_basis
        count = item.get("dispatch_count")
        return BASIS_NO_INTENT if count == 0 else BASIS_SAVED_INTENT

    def stamp_attempts(attempts: Any) -> Any:
        if not isinstance(attempts, list):
            return attempts
        return [
            {**item, "attempt_counts_basis": basis_for(item)}
            if isinstance(item, Mapping)
            else item
            for item in attempts
        ]

    def stamp_summary(summary: Any) -> Any:
        if not (isinstance(summary, Mapping) and isinstance(summary.get("children"), list)):
            return summary
        return {
            **summary,
            "children": [
                {**item, "attempt_counts_basis": basis_for(item)}
                if isinstance(item, Mapping) and "dispatch_count" in item
                else item
                for item in summary["children"]
            ],
        }

    def stamp_changes(changes: Mapping[str, Any]) -> dict[str, Any]:
        stamped = dict(changes)
        if "provider_attempts" in stamped:
            stamped["provider_attempts"] = stamp_attempts(stamped["provider_attempts"])
        if "verification_summary" in stamped:
            stamped["verification_summary"] = stamp_summary(stamped["verification_summary"])
        return stamped

    if "provider_attempts" in value:
        value["provider_attempts"] = stamp_attempts(value["provider_attempts"])
    if "verification_summary" in value:
        value["verification_summary"] = stamp_summary(value["verification_summary"])
    # S20: historic lifecycle events keep their counters next to a basis too.
    for key in ("lifecycle_events", "events"):
        events = value.get(key)
        if not isinstance(events, list):
            continue
        value[key] = [
            {**event, "changes": stamp_changes(event["changes"])}
            if isinstance(event, Mapping) and isinstance(event.get("changes"), Mapping)
            else event
            for event in events
        ]
    children = value.get("f3_children")
    if isinstance(children, list):
        value["f3_children"] = [
            {**item, **child.public_fields()}
            if isinstance(item, Mapping)
            and (child := facts.child(item.get("child_execution_id"))) is not None
            else item
            for item in children
        ]
    return value


# Design section 2.4.1: health and reconciliation keep their state counts and
# add these outcome sub-counts, each parent counted once under its aggregate
# phase.  Keys are fixed so the health shape does not depend on the data.
OUTCOME_COUNT_KEYS: dict[str, str] = {
    PHASE_ALREADY_PRESENT: "already_present_not_dispatched",
    PHASE_INTENT_RECORDED_NOT_SENT: "failed_after_intent_not_dispatched",
    PHASE_SENT_REJECTED_NO_MUTATION: "sent_rejected_no_mutation",
    PHASE_SENT_FAILED_MUTATION_UNPROVEN: "sent_failed_mutation_unproven",
}


def outcome_counts(facts: Iterable[DispatchFacts]) -> dict[str, int]:
    """Bounded outcome sub-counts over parent facts (one count per parent)."""

    counts = {key: 0 for key in OUTCOME_COUNT_KEYS.values()}
    for item in facts:
        key = OUTCOME_COUNT_KEYS.get(item.dispatch_phase)
        if key is not None:
            counts[key] += 1
    return counts
