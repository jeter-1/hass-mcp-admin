# ADR-018: F3 governed configuration rollback

## Status

Accepted for Beta 20 implementation and independent review.

## Context

Leaving the legacy rollback writer active for newly F3-owned configuration
tasks would bypass the F3 lock and intent domain. Disabling rollback would
remove a legitimate recovery capability. A compatibility bridge would retain
two mutation frameworks and duplicate process-loss reasoning.

## Decision

Adopt rollback Option A. `rollback_change` becomes a planner for supported
configuration work: it performs no Home Assistant mutation and creates a new
immutable reverse update plan from exact persisted prior-state evidence. The
new plan receives its own F2 policy classification, external approval,
schema-1 public task, deterministic F3 child, complete locks, final stale-state
preflight, intent, one provider write, and exact restored-state verification.

The rollback plan is allowed only when the forward operation was authoritatively
verified, exact prior configuration exists, and current readback still matches
the forward resulting-state fingerprint. Create-through-delete is prohibited.
Operational and dashboard rollback remain unavailable.

For a partially applied configuration sequence, only verified completed update
operations are included, in safe reverse order. Never-dispatched and unresolved
possibly-dispatched operations are reported as excluded and receive no inferred
reverse action. Historical pre-Beta-20 configuration work can be converted only
from complete immutable legacy evidence; otherwise rollback is unavailable and
a new manually reviewed plan is required.

The forward approval is never reused. Rollback does not execute automatically,
does not compensate a sequence implicitly, and cannot bypass the same exact
resource, reload-domain, and Home Assistant core lock set used by the
corresponding forward update.

## Alternatives considered

A legacy compatibility bridge would preserve the old writer but require two
execution authorities to share fencing, intent, and recovery semantics. It is
rejected because the bounded F3 reverse update uses the accepted configuration
adapter directly. Disabling rollback for new F3 tasks is also rejected because
the accepted evidence is sufficient for a separately governed update and the
product should retain bounded recovery capability.

## Consequences

- Every newly reachable rollback mutation is governed by F3 intent and locks.
- The request phase is safe and non-mutating; a second approval is mandatory.
- Partial rollback preserves operation-by-operation truth.
- Missing or stale evidence fails closed without guessing prior intent.
- No generalized compensation, deletion, operational rollback, or automatic
  rollback becomes reachable.
