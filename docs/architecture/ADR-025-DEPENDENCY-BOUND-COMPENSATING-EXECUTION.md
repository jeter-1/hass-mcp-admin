# ADR-025: Dependency-bound compensating execution (scoped ADR-018 amendment)

## Status

Accepted for HAMCP-295 and HAMCP-109 implementation and independent review in
2.6.0. Scope: execution mode `dependency_bound_compensating_v1` only. Every
other plan keeps ADR-018 and `ordered_stop_on_failure` unchanged.

## Context

ADR-018 makes rollback a separately approved reverse plan and states that
rollback "does not compensate a sequence implicitly", that "the forward
approval is never reused", and that "no generalized compensation ... or
automatic rollback becomes reachable".

Two recorded owner decisions require a different behaviour for one bounded
case:

- HAMCP-109 register Next Action, 2026-11-07 (design-complete scoping):
  "dependent steps verified in order with rollback of earlier steps on later
  failure", with entity rename (HAMCP-295) as its concrete driver.
- Josh direction, register 2026-11-07, confirmed in-session 2026-11-08: the
  HAMCP-295 rename "rewrites references with verified undo"; disclose-only is
  not a deliverable.
- Josh decision, in-session 2026-11-08, item 6: ship in 2.6 with a hard
  cutoff to 3.0 if the contract is not frozen. Item 7: one plan-level
  acknowledgement when any reference is not rewritten. Item 8: `elevated_admin`
  for actuators only (069A E1 pattern).

ADR-022 requires a superseding owner decision to be recorded with its
consequences. The design contract is
[`HAMCP_295_109_ENTITY_RENAME_DEPENDENT_EXECUTION_DESIGN.md`](../HAMCP_295_109_ENTITY_RENAME_DEPENDENT_EXECUTION_DESIGN.md),
sections 3.2 and 3.2.6.

## Decision

Add one execution mode, `dependency_bound_compensating_v1`. The planner
selects it only for an entity rename plan with dependent reference rewrites.
It is not caller-selectable, and it is never applied to an existing plan.

For this mode only, three ADR-018 statements are amended:

1. "Does not compensate a sequence implicitly" becomes: compensation is
   explicit. Every compensation payload is exact, precomputed, shown on the
   approval surface, and bound into the plan hash before approval.
2. "No ... automatic rollback becomes reachable" becomes: after a
   determinate forward failure, compensation of the owned, verified forward
   prefix runs automatically, in reverse order. An indeterminate outcome holds
   and never triggers compensation. Drift found by the full-bundle check stops
   compensation and ends in manual review.
3. "The forward approval is never reused" becomes: compensation children run
   under the forward plan's single consumed approval, because their exact
   payloads were part of that approval's hash. Owner rollback after success
   remains ADR-018 `rollback_change` and still needs its own new approval.

Every other ADR-017 and ADR-018 rule holds unchanged:

- one F3 child per step and at most one dispatch per child, with a durable
  intent;
- per-child Core admission at dispatch (HAMCP-222);
- the complete forward lock union, held through compensation and transferred
  with `transfer_complete`;
- a full-bundle state check before every compensation child;
- readback-only recovery after restart;
- no create-through-delete;
- no operational or dashboard compensation.

## Alternatives considered

- **Keep ADR-018 unchanged and require a manual rollback plan after any
  partial failure.** Rejected: it contradicts the recorded HAMCP-109 decision
  and leaves a renamed entity with broken dependants until a second approval.
- **Generalized compensation for every multi-operation plan.** Rejected: it
  would silently change shipped behaviour and expand scope beyond the decision.

## Consequences

- One additive mode, reachable only through the HAMCP-295 rename planner. Its
  runtime wiring lands with the governance service integration after
  HAMCP-329/298.
- The approval surface for this mode must state the compensation payloads and
  that compensation is automatic in timing. The "non-atomic, no automatic
  rollback" wording stays for every other plan.
- Compensation outcomes are reported per step, and an incomplete compensation
  ends in manual review with preserved state, never in a guessed inverse.
- No new policy class or risk program is introduced. Compensation is
  triggered by execution outcome, not by a consequence label.
