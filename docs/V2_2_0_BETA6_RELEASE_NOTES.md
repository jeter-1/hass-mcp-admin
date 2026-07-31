# HA MCP Engineering Server 2.2.0-beta.6

Version `2.2.0-beta.6` adds F2 runtime policy, risk, and elevated
administrator-approval semantics to the existing governed change lifecycle.
It does not add another resource, provider, write surface, or execution path.

## Deterministic plan policy

Every newly created governed plan receives one server-derived, immutable
`f2-v1` policy decision. The decision keeps these dimensions distinct:

- risk delta: `none`, `low`, `moderate`, `high`, or `critical`;
- physical consequence: `none`, `indirect`, `direct`, or
  `safety_critical`; and
- policy class: `standard_admin`, `elevated_admin`, or `prohibited`.

Each operation is classified independently. A multi-operation plan uses the
strictest risk, consequence, and policy class in deterministic order.
Prohibited dominates elevated and standard. Unknown or unclassifiable
operations never default to standard.

Full local backup and controlled approved-domain reload are standard. Exact
add-on and Home Assistant restarts are elevated. Already-supported low or
moderate configuration changes without direct physical consequence are
standard; supported high-risk or direct-consequence configuration changes are
elevated. Critical, safety-critical, unsupported, destructive, arbitrary,
policy-evasive, or unknown operations are prohibited.

The normalized operations, target evidence, policy version, risk delta,
physical consequence, policy class, bounded reason codes, required
acknowledgements, and policy-decision hash are bound into the immutable plan
hash. Recomputed policy must match before approval and immediately before
dispatch. A mismatch returns `policy_snapshot_mismatch`; legacy active plans
without F2 authority return `policy_snapshot_required` or
`approval_authority_mismatch` and must be recreated.

## Approval authority version 3

`standard_admin` requires one `plan_approval` from an authenticated Home
Assistant administrator through the existing admin-only Ingress panel.

`elevated_admin` requires two separate, ordered actions:

1. `plan_approval`;
2. `elevated_risk_acknowledgement`.

The second action has a separate challenge, CSRF value, timestamp, expiry, and
persisted record. It is unavailable until plan approval succeeds and must be
completed by the same authenticated administrator. This is deliberately not
two-person approval and is not principal separation between the two actions.
The existing boundary between the MCP caller and the external administrator
remains intact.

Plan lifetime defaults to 120 minutes. Each approval action expires within 60
minutes and never later than its plan. Rejection, expiry, supersession, policy
change, stale state, or malformed persisted authority fails closed.
`prohibited` creates no actionable approval challenge, execution task, or
provider call.

## Dispatch and durable-task boundary

`apply_change_plan` revalidates the immutable plan, policy snapshot, current
policy result, complete approval bundle, same-administrator condition,
expiration, stale-state evidence, and task-storage availability. One durable
F1 task owns the plan before approval consumption can cross the dispatch
boundary. The consumed authority is projected additively into the task while
task schema version remains 1.

Duplicate apply, startup recovery, client interruption, and provider-response
loss retain the existing one-task/no-blind-redispatch behavior. Reconciliation
is readback-only. Operation-specific verification, the 24-hour post-dispatch
deadline, pre-dispatch-only cancellation, and truthful provider-response
evidence remain unchanged.

## Ingress, audit, and health

The Ingress review page displays bounded server-owned plan identity, shortened
hash, policy class, risk delta, physical consequence, reason codes, active
approval action, target, operation, and expiry. Viewing never acknowledges
risk; standard and elevated actions use separate POSTs and one-time CSRF
values.

Audit records add bounded policy, approval-action, authority-version, and
same-principal fields without exposing raw user identifiers, approval tokens,
full proposed configurations, credentials, or provider payloads. Health adds
policy-class, pending-action, consumed-bundle, prohibited-decision, policy
mismatch, principal mismatch, and sequence-failure counters derived from
persisted evidence.

## Scope and compatibility

Beta 6 does not add:

- a resource type, arbitrary service call, Supervisor request, or provider;
- update or recovery execution, restore, downgrade, safe mode, or cleanup;
- F3 shared locks, F4 transaction graphs or compensation, or F5 generalized
  verification;
- a new MCP tool, capability, public execution schema, task schema version, or
  fallback; or
- C1, K1, or E1 runtime authority over policy or execution.

The source catalog remains 25 canonical plus 23 Engineering-native tools, 48
local registered tools. Exact configured admission may add 26 delegated reads
for 74 live tools. Planned tools remain 0. Task schema remains 1. Reviewed
upstream evidence remains `ha-mcp` 7.14.2, protocol `2025-03-26`, entry
`ha-mcp-v7.14.2-7917b2d3`, and catalog fingerprint
`c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`.
Stable v1.1.2 and zero fallback are unchanged.

Rollback to the accepted `2.2.0-beta.5` artifact leaves authority-v3 plans and
tasks byte-preserved but non-actionable to the older runtime. Reinstall Beta 6
to inspect or continue eligible readback. Never recreate an already-dispatched
operation to compensate for a downgrade.
