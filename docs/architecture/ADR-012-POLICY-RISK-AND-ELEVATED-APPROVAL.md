# ADR-012: Policy, risk, and elevated administrator approval

Status: accepted for 2.2.0-beta.6

## Context

The existing governance lifecycle binds one external Home Assistant
administrator approval to an immutable plan hash. It also keeps durable F1
execution tasks, one-dispatch ownership, readback-only reconciliation, stale
state checks, and operation-specific verification authoritative. Earlier
releases treated supported high-risk configuration proposals as categorically
unapprovable and did not distinguish risk magnitude from physical consequence.

F2 needs an explicit policy decision without adding resources, arbitrary
provider access, update or recovery execution, locks, transaction graphs,
compensation, or generalized verification.

## Decision

Every newly created governed plan contains one server-derived immutable policy
snapshot. The closed dimensions are:

- risk delta: `none`, `low`, `moderate`, `high`, or `critical`;
- physical consequence: `none`, `indirect`, `direct`, or `safety_critical`;
- approval policy: `standard_admin`, `elevated_admin`, or `prohibited`.

Risk delta and physical consequence are independent. A direct physical
consequence is not encoded merely as a higher risk score. Each operation is
classified independently, then a multi-operation plan takes the strictest risk
and consequence. Prohibited dominates elevated and standard; elevated
dominates standard. Input order cannot change the aggregate decision.

Policy is evaluated from the normalized immutable plan, never raw caller text.
The server owns the policy version, class, bounded reason codes, required
acknowledgements, subject hash, and policy-decision hash. A caller cannot select
or downgrade them. Unknown, incomplete, unsupported, destructive,
policy-evasive, critical, or unreviewed safety-critical classifications fail
closed as `prohibited`.

The plan hash binds the complete policy snapshot. The policy-decision hash binds
the normalized plan subject and the complete decision. Loading, requesting
approval, and applying revalidate both bindings. A mismatch is never silently
repaired and requires a new plan.

## Administrator actions

Approval authority version 3 keeps Home Assistant Ingress as the private human
authority.

`standard_admin` requires one hash-bound `plan_approval` action.

`elevated_admin` requires two distinct, ordered, durable actions:

1. `plan_approval`;
2. `elevated_risk_acknowledgement`.

The second action is unavailable until the first succeeds. The same
authenticated Home Assistant administrator must complete both actions. The
second action separately acknowledges the displayed policy class, risk delta,
physical consequence, plan hash, and policy-decision hash. It has its own
challenge, CSRF value, timestamp, expiry, and persisted state.

This is deliberately not two-person control and is not principal separation
between the two administrator actions. Existing separation between an MCP
requesting caller and the external Home Assistant administrator remains intact.

`prohibited` creates no actionable approval challenge and cannot be approved,
create an execution task, or reach provider dispatch.

The default plan lifetime is 120 minutes. An approval action is valid for no
more than 60 minutes and never outlives its plan. Rejection, expiry,
supersession, stale state, or a changed policy invalidates an incomplete bundle.

## Dispatch boundary

Immediately before irreversible dispatch, governance revalidates the exact plan
hash, policy snapshot, policy recomputation, policy class, full approval bundle,
same-administrator requirement, expiry, stale-state evidence, and durable task
availability. Only then may the approval bundle be consumed and dispatch be
attempted.

F1 remains authoritative for durable task ownership, append-only events,
one-dispatch enforcement, duplicate-apply prevention, provider-response
truthfulness, startup reconciliation, the 24-hour post-dispatch deadline, and
pre-dispatch-only cancellation. F2 adds bounded approval and policy references
without changing task schema version 1.

## Compatibility

Historical terminal plans and tasks remain readable and unchanged. A pre-F2
plan without a validated policy snapshot cannot receive a new approval or
dispatch; an operator must create a new plan. Authority-version-2 approval
records are not upgraded or treated as authority-version-3 bundles. Missing F2
fields on otherwise valid historical data are legacy evidence, not corruption.

The existing MCP tools and reviewed providers are unchanged. F2 adds no new
resource type, arbitrary service call, Supervisor request, direct Home Assistant
fallback, update, restore, downgrade, safe-mode action, or cleanup write.

## Consequences

Supported high-risk or direct-physical configuration changes may become
eligible for elevated review rather than blanket rejection, but planning and
configuration apply never trigger a future automation action. Safety-critical,
unknown, unsupported, destructive, or evasive proposals remain prohibited.

Audit and health surfaces can explain policy and approval state with bounded
server-owned codes. They must not expose credentials, approval tokens, full
configuration payloads, or raw Home Assistant user identifiers.
