"""Code-owned fan audit projections; no provider payload or approval invention."""
import re

from ..f3.persistence import DurableExecutionRepository
from ..f3_runtime import dispatch_facts as dispatch_projection
from .contracts import OPERATION_PATTERN, PROVIDER, digest

# HAMCP-181 (OD-6(ii)): fan/power publish the shared dispatch facts beside the
# unchanged literal ``dispatched_at: null``.  No stored field records the
# provider send instant, so no send time is ever published.
_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"
)
_FACT_VALUES = {
    "dispatch_phase": frozenset(dispatch_projection.PHASE_RANK),
    "attempt_counts_basis": frozenset({
        dispatch_projection.BASIS_NO_INTENT,
        dispatch_projection.BASIS_SAVED_INTENT,
        dispatch_projection.BASIS_NO_PROVIDER_CALL,
    }),
    "dispatched_at_basis": frozenset({
        dispatch_projection.DISPATCHED_AT_NO_INTENT,
        dispatch_projection.DISPATCHED_AT_CALL_NOT_SENT,
        dispatch_projection.DISPATCHED_AT_SEND_TIME_NOT_RECORDED,
    }),
}
_FACT_TIMES = ("dispatch_intent_recorded_at", "provider_response_recorded_at")


def dispatch_fact_fields(record):
    """The fan/power facts for one execution record (``None`` before one)."""

    facts = dispatch_projection.dispatch_facts_for_record(record)
    value = facts.public_fields(include_dispatched_at=False)
    # The receipt keeps its own reconciliation fields; these are qualifiers.
    for name in ("provider_response_received", "retryable"):
        value.pop(name, None)
    return value


def request_summary(telemetry, *, kind="fan", provider=PROVIDER):
    """Only this typed projection crosses the authenticated gateway audit path."""
    context = telemetry.audit_context
    safe = {"operation_class": "typed_" + kind, "provider": provider, "fallback": "none"}
    for name, pattern in (("task_id", r"[a-f0-9]{32}"),
                          ("operation_id", OPERATION_PATTERN),
                          ("operation_hash", r"[a-f0-9]{64}")):
        value = context.get(name)
        if isinstance(value, str) and re.fullmatch(pattern, value):
            safe[name] = value
    outcome = context.get(kind + "_outcome")
    if isinstance(outcome, str) and outcome in {
        "created", "preflight", "dispatching", "observing", "succeeded_verified",
        "failed_pre_dispatch", "failed_post_dispatch", "manual_review_required",
        "cancelled_pre_dispatch", "request_failed_reconcile_task",
        "preparation_failed_no_task",
    }:
        safe[kind + "_outcome"] = outcome
    for name in ("dispatch_intent_recorded", "provider_response_received", "terminal"):
        if type(context.get(name)) is bool:
            safe[name] = context[name]
    if type(context.get("provider_attempt_count")) is int and context["provider_attempt_count"] in (0, 1):
        safe["provider_attempt_count"] = context["provider_attempt_count"]
    for name, allowed in _FACT_VALUES.items():
        value = context.get(name)
        if isinstance(value, str) and value in allowed:
            safe[name] = value
    for name in _FACT_TIMES:
        value = context.get(name)
        if value is None and name in context:
            safe[name] = None
        elif isinstance(value, str) and _TIMESTAMP.fullmatch(value):
            safe[name] = value
    if type(context.get("provider_dispatch_occurred")) is bool:
        safe["provider_dispatch_occurred"] = context["provider_dispatch_occurred"]
    if "dispatched_at_basis" in safe:
        safe["dispatched_at"] = None  # never a send time (OD-6(ii))
    return safe


class FanExecutionRepository(DurableExecutionRepository):
    """Project after durable persistence, using the existing audit idempotency.

    Execution/lock formats and authority are unchanged. Events can be replayed
    from a receipt after interruption; retained audit IDs deduplicate them.
    Audit failure must never turn a persisted dispatch into retry authority.
    """
    kind = "fan"
    provider_name = PROVIDER

    def __init__(self, root, declaration, audit):
        super().__init__(root)
        self.declaration = declaration
        self.audit = audit
        self.audit_projection_failures = 0

    def _write_unlocked(self, record):
        super()._write_unlocked(record)
        self.project(record)

    def project(self, record):
        if self.audit is None or record is None:
            return
        try:
            prepared = self.declaration(record.execution_identity().task_id)
            if prepared is None or prepared.prepared_operation_hash != record.prepared_operation_hash:
                raise ValueError("fan audit declaration mismatch")
            common = {
                "request_id": record.execution_identity().request_id,
                "task_id": prepared.request.task_id,
                "operation_id": prepared.request.operation_id,
                "operation_hash": prepared.prepared_operation_hash,
                "entity_id": prepared.request.entity_id,
                "provider": self.provider_name, "fallback": "none",
                "authority_kind": "authenticated_connector",
                "operation_class": "typed_" + self.kind + "_lifecycle",
                "redispatch_prohibited": True,
            }
            if prepared.core_binding is not None:
                common.update(core_binding=prepared.core_binding,
                              provider_contract=prepared.provider_contract)
            entries = []
            # Intrinsic event facts only: later state is not backdated onto an
            # earlier event. The separate snapshot labels current durable facts.
            recovering = False
            for event in record.events:
                recovering |= event["event_type"] == "recovery_claimed"
                entries.append({
                    **common, "event": self.kind + "_" + event["event_type"],
                    "audit_event_id": digest({self.kind + "_task": common["task_id"], "event": event}),
                    "timestamp": event["occurred_at"],
                    "event_sequence": event["sequence"],
                    "read_only_recovery": recovering,
                })
            snapshot = {
                **common, "event": self.kind + "_execution_snapshot",
                "timestamp": record.updated_at, "event_sequence": len(record.events),
                "state": record.task_state, "terminal": record.terminal,
                "outcome": record.normalized_outcome,
                "provider_attempt_count": record.dispatch_count,
                "dispatch_intent_recorded": record.dispatch_intent is not None,
                "dispatched_at": None,
                "provider_response_received": record.provider_response_received,
                "verified": True if record.normalized_outcome == "succeeded_verified" else None,
                "read_only_recovery": any(e["event_type"] == "recovery_claimed" for e in record.events),
            }
            # Current durable facts belong on the snapshot only (time-local
            # events are not backdated); existing keys keep their values.
            for name, value in dispatch_fact_fields(record).items():
                snapshot.setdefault(name, value)
            snapshot["audit_event_id"] = digest(snapshot)
            entries.append(snapshot)
            if self.audit.write_batch(entries) != len(entries):
                self.audit_projection_failures += 1
        except Exception:
            # Never export exception strings or interrupt the one-dispatch fence.
            self.audit_projection_failures += 1
