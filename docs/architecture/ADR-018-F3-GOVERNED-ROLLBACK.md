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

## Amendment: rollback of governed creates (HAMCP-298)

Status: proposed with the HAMCP-329/298 implementation; contract in
`docs/HAMCP_329_298_SCENE_CREATE_AND_CREATE_ROLLBACK_DESIGN.md` §3.2-§3.4.

This amendment narrows the last consequence above for one case. Rollback of a
verified governed create of an automation or script plans deletion of the
created object, and only as a new, separately approved HAMCP-155
configuration delete plan. The delete children keep the unchanged signed
`core.configuration_delete_operation` contract, intent, locks and preflight.
No other deletion rollback becomes reachable.

- Eligibility requires creation-time identity that the forward task captured
  under its own locks right after the create verified. It is kept in a
  hash-bound sidecar store, so the `ExecutionRecord` schema does not change.
  Rollback never substitutes an observation made at rollback time; a missing
  capture is `created_object_identity_not_captured`.
- Each child carries one closed ownership marker in its risk evidence. The
  plan hash binds it through the policy subject, and the delete preflight
  rechecks it under the locks. A changed body, registry entry id or
  `created_at` refuses the child. When only `modified_at` changed (Option E),
  the child is included with a disclosure and an elevated acknowledgement.
- Exclusions are reported per operation, in reverse order. An excluded
  prerequisite blocks every later create. An already-absent object is
  excluded but does not block.
- Plans that mix creates with updates keep the existing update rollback, and
  their creates are excluded. Legacy `CREATE_AUTOMATION` stays
  `unavailable_for_create`. Helper and scene creates stay ineligible until
  their own dormant 2026.11.0 profiles
  (`core.created_helper_delete_operation`, `core.scene_create_delete_operation`)
  are implemented, admitted and signed.
- Ownership is evidence, never proof. A replacement that kept the same
  registry entry and an identical body cannot be distinguished, and this is
  disclosed to the approver.
