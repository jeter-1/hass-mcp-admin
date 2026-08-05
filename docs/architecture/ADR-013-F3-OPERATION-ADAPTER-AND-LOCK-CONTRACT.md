# ADR-013: F3 operation-adapter, lock, and dispatch contract

Status: Accepted F3-A core; amended by Beta 17 for canonical packaging and
dashboard execution deferral

## Context

Beta 15 already has governed configuration and operational paths with exact
provider admission, external approval, durable dispatch intent, readback, and
no-blind-redispatch behavior. The paths differ in lock keys, outcome names,
recovery coverage, and rollback availability. F3 must standardize those
boundaries without replacing working operation-specific verification.

This decision inherits rather than supersedes:

- [ADR-007](ADR-007-GOVERNED-OPERATIONAL-ADMINISTRATION.md) for constrained
  operational providers;
- [ADR-008](ADR-008-DURABLE-EXECUTION-TASKS.md) for task schema 1 and durable
  events; and
- ADR-012 (policy, risk, and elevated approval) for policy and external
  approval authority.

F3-0 introduced a declaration-only protocol in
`f3_contracts/operation_adapter.py`. F3-A then shipped the isolated executor,
durable persistence, and lock core. Beta 17 resolves the packaging boundary:
`ha_mcp_engineering.f3.contracts` is the sole runtime definition, and the root
package is only an object-identical compatibility/test facade. Existing plan
and task schemas remain authoritative.

## Decision

### Canonical lifecycle

Every F3 adapter exposes these phases while retaining provider-specific logic:

1. **Planning** identifies one exact target, reads exact current state, creates
   an immutable proposed operation, binds current and proposed hashes, applies
   risk/policy, names approvals and effects, declares rollback capability, and
   defines verification.
2. **Preflight** repeats provider admission, exact operation-contract checks,
   stale-state comparison, configuration validation where required, target
   identity confirmation, lock calculation/acquisition, and dispatch
   eligibility. It performs no provider mutation.
3. **Dispatch** is sequenced by the shared executor. After complete locks and
   final preflight, it invokes the caller-owned idempotent durable
   approval-consumption callback, commits F3 durable dispatch intent, and only
   then permits the adapter to invoke the exact mutating provider operation at
   most once with exact reviewed arguments.
4. **Observation** performs bounded exact readback, records attempts and
   recovery evidence, and never repeats the mutating provider call.
5. **Verification** compares readback to the plan's exact intended result,
   records bounded mismatch fields, and chooses verified success, conclusive
   failure, continued observation, or manual review.
6. **Recovery** rehydrates durable plan/task/intent evidence, reacquires or
   recovers locks, and resumes observation or verification only. Once dispatch
   may have occurred, recovery never returns to dispatch.
7. **Rollback** is a declared capability, not an assumed phase. When available,
   it is prepared as a separate hash-bound governed operation with its own
   stale preflight, authorization, durable intent, one dispatch, and
   verification.

For a configuration plan, “one mutating provider invocation” means at most one
write invocation per prepared operation. It does not collapse an approved
ordered multi-operation plan into one provider call. Admission, stale-state,
identity, and readback observations are non-mutating calls with separate
bounded attempt accounting. F4 retains graph execution and generalized
compensation.

### Canonical shipped declarations

The declaration model in `ha_mcp_engineering.f3.contracts` is
`f3-operation-adapter-v1`. It defines:

- exact operation target identity;
- adapter capability descriptor;
- canonical resource/provider lock request;
- immutable prepared-operation hashes and expected effects;
- bounded preflight, dispatch, observation, and verification results;
- a caller-owned idempotent callback for durable approval consumption;
- a callback that must durably record dispatch intent; and
- explicit prepare, preflight, dispatch, observe, verify, recover, and
  prepare-rollback methods.

The declarations are not serialized and do not add a public operation. No
shipped module depends on repository-root `f3_contracts`; that facade re-exports
the exact canonical class, enum, protocol, and constant objects for historical
tests and specification compatibility.

### Dashboard execution deferral

F3-B's exact source review and deterministic interleaving prove that the
reviewed dashboard hash check and save are separate. An Engineering lock cannot
exclude Home Assistant UI users, integrations, automations, or other clients,
and a final exact reread cannot reveal an external edit already overwritten by
the approved result.

Beta 17 therefore retains dashboard planning, patch compilation, semantic
diff, risk evidence, immutable artifacts, stale-state validation, and exact
verification while accepting no dashboard setter realization. Generated
`python_transform` is rejected, unrestricted full-configuration replacement
is not a workaround, and no public tool or persisted `update_dashboard`
operation is added. Reconsideration requires reviewed atomic compare-and-save,
expected-hash enforcement at the authoritative save boundary, or authoritative
exclusion of all dashboard writers.

### Normalized outcomes

| Normalized outcome | Meaning | Existing task-schema projection |
|---|---|---|
| `preflight_rejected` | Validation, identity, stale, policy, or eligibility failed before dispatch | `failed_pre_dispatch` |
| `lock_conflict` | An incompatible owner already holds a required lock | `failed_pre_dispatch` today; later waiting remains a separate explicit decision |
| `provider_unavailable_pre_dispatch` | Exact provider could not be admitted or reached before durable intent | `failed_pre_dispatch` |
| `dispatch_failed_confirmed` | Provider invocation definitively failed after durable intent | `failed_post_dispatch` |
| `dispatch_indeterminate` | Intent is durable and invocation may have occurred, but response truth is unknown | `observing` |
| `observing` | Readback/recovery is in progress | `observing` |
| `verification_mismatch` | Exact readback conclusively differs from intended result | `failed_post_dispatch` |
| `succeeded_verified` | Exact intended result is verified | `succeeded_verified` |
| `failed_pre_dispatch` | Adapter-neutral terminal pre-dispatch failure | `failed_pre_dispatch` |
| `failed_post_dispatch` | Adapter-neutral terminal post-dispatch failure | `failed_post_dispatch` |
| `manual_review_required` | Durable evidence cannot safely prove success or failure | `manual_review_required` |
| `cancelled_pre_dispatch` | Cancellation completed before any possible dispatch | `cancelled_pre_dispatch` |

These are aliases/projections. F3-0 does not rename `PlanStatus`,
`ExecutionTaskState`, event types, or historical outcomes. A later adapter may
not report a pre-dispatch outcome after durable intent exists.

### Dispatch boundary

Approval remains caller/governance authority. The shared executor owns only
the sequencing responsibility, and an adapter neither grants nor interprets
approval. The executor receives an `ApprovalConsumptionRecorder`, an
idempotent `Callable[[], Awaitable[None]]`, for the exact governed execution.

After execution claim, complete atomic lock acquisition, and successful final
adapter preflight, the executor-created `before_dispatch` callback performs:

1. validate the current fenced lock handle;
2. invoke the caller-owned durable approval-consumption callback;
3. commit F3 durable dispatch intent and reserve `dispatch_count=1`; and
4. return to the adapter, which immediately performs its reviewed mutation.

The approval record and F3 intent are separate durable writes; this ADR does
not claim a single storage transaction. Between their completion there is no
provider probe, mutable policy decision, unrelated await, or adapter-controlled
branch. Approval is never consumed for a lock conflict, lock-storage failure,
cancellation accepted before intent, stale-state rejection, provider-admission
rejection, target-identity rejection, or another failed preflight.

If approval consumption fails, intent remains absent, `dispatch_count` remains
zero, and provider mutation is unreachable. If approval is durably consumed
but intent persistence fails, the same task, plan, operation, and attempt
remain the only F3 execution authority. A reconstruction repeats the same
idempotent approval callback and must not enter legacy execution. Provider
invocation remains zero until intent succeeds. A process loss in this gap has
the same reconstruction rule.

Once intent commits, a crash before bytes reach the provider is nevertheless
treated as possibly dispatched. Provider response loss, timeout, process loss,
or reconnect never permits another mutating call. Resolution is readback only.

Response evidence is bounded, sanitized, and truthful about whether a response
was received. A response is not verification.

### Lock model

Locks coordinate incompatible operations; they never grant authority. Policy,
approval, provider admission, stale-state validation, and exact arguments remain
independent requirements.

Canonical keys are lower-case type plus one canonical identifier, separated by
one colon. Identifiers use the target's already reviewed canonical form; no
fuzzy names or aliases are allowed. Required examples are:

- `automation:<automation_id>`
- `script:<script_id>`
- `helper:<entity_id>`
- `dashboard:<canonical_url_path>`
- `backup:local_full_backup`
- `addon:<slug>`
- `home_assistant:core`
- `reload:<domain>`

The current tuple keys map to these names only when F3-A adopts the contract;
F3-0 does not change keys. Requests are canonicalized before acquisition:

- canonical key, not scope, is the duplicate identity;
- duplicate requests union their sorted unique scope and reason-code evidence;
- exclusive mode dominates shared mode for the same owner and key;
- each key is acquired exactly once;
- final unique keys are acquired in bytewise lexical order; and
- release occurs in reverse acquisition order.

Missing scope/reason evidence, incompatible key canonicalization, and an
unsorted or duplicate canonical set fail before acquisition. This preserves why
an exact add-on restart can request one key both as a mutated resource and as a
provider dependency without self-deadlock or loss of review evidence.

Two scopes exist:

- a **resource** lock protects an exact mutable target; and
- a **provider** lock represents an operation's dependency on the continued
  availability of an exact provider/add-on.

Shared locks express availability dependencies; exclusive locks express a
mutation or outage. Provider locks do not substitute for resource locks.

The frozen conflict rules are:

- two writes to the same canonical resource conflict;
- separate dashboards may be written concurrently;
- a Home Assistant restart holds `home_assistant:core` exclusively, while
  configuration writes and reloads hold it shared, so they conflict with the
  restart;
- an add-on restart holds its `addon:<slug>` dependency exclusively, while
  operations that require that exact add-on hold it shared;
- reload operations hold `reload:<domain>` exclusively;
- the local full-backup operation holds `backup:local_full_backup`
  exclusively; compatibility with unrelated operations remains an
  evidence-gated F3-A decision rather than an assumed conflict; and
- no lock may authorize, widen, or retry an operation.

Current target conflicts fail immediately, so the current effective acquisition
timeout is zero. F3 retains fail-fast as the default. Any future bounded wait
must be explicit, task-visible, cancellable, and separately reviewed before
making reserved `waiting_for_lock` reachable.

At F3-0 the current runtime had no leases. F3-A now provides explicit positive
lease duration and a shorter positive renewal interval, failing closed when
either is invalid or `renewal_interval >= lease_duration`. A lease is bound to
task ID, plan ID, owner/process identity, operation, canonical keys, acquisition
time, expiry, and fencing generation. Adapter-specific production timing and
activation remain separately reviewed integration decisions.

A pre-dispatch terminal outcome releases every lock. A terminal outcome with an
exactly verified target state releases every lock. Post-dispatch indeterminate
work retains or safely reacquires the relevant lock until exact terminal
resolution. If the durable deadline expires or manual review is required while
dispatch truth remains unresolved, the lease transitions to a durable
target-conflict hold rather than making the target eligible for another write.
That hold blocks incompatible operations until separately governed resolution
performs exact readback and records an explicit safe release decision. Deadline
or process loss never authorizes redispatch or silent lock release. Expired or
stale lease recovery validates the owning task, intent, and conflict hold before
takeover and emits bounded audit evidence.

Lock health must count acquisitions, conflicts, renewals, renewal failures,
recovered stale leases, forced manual reviews, and currently active locks. Audit
evidence names bounded canonical keys and task/plan IDs, never arbitrary target
content.

### Observation, verification, and recovery

Readback uses the same exact target identity and an operation-specific
verification contract. Attempt counts and bounded mismatch paths are durable.
Readback may prove an intended result after response loss, but it may not create
a new dispatch lineage.

Recovery behavior is phase-specific:

- before approval consumption: revalidate or terminate pre-dispatch;
- after approval consumption but before durable intent: retain the same F3
  execution identity and idempotently retry approval consumption before a new
  attempt to persist that one intent; provider invocation remains zero;
- after durable intent and before known response: assume possible dispatch and
  observe;
- after response: verify the target, not the response alone;
- during observation/verification: resume within the fixed deadline;
- after deadline or contradictory evidence: fail post-dispatch or require
  manual review according to exact evidence; and
- during rollback: apply the same one-dispatch/no-blind-redispatch rule to the
  separately approved rollback operation.

Provider unavailability before intent is a pre-dispatch failure. Unavailability
after intent preserves possible-dispatch state and retries only readback under a
bounded policy.

### Rollback capability

Each prepared operation declares whether exact rollback is available. When
false, the executor must not infer rollback from a retained snapshot or an
upstream best-effort backup. When true, exact prior state, resulting-state hash,
stale-safe rollback preflight, required authorization, and rollback
verification are mandatory.

F3 does not require every adapter to support rollback. General compensation and
multi-operation rollback graphs remain F4.

## Consequences

- Later branches compile against one shipped stable declaration vocabulary
  while current production routes remain disconnected until F3-D activation.
- Operation-specific admission, arguments, verification, and recovery remain
  visible rather than hidden by a generic dispatcher.
- Durable locks and cross-operation conflicts are implemented and tested in
  F3-A; adapter migration and runtime activation remain later work.
- Persisted schemas remain compatible because normalized outcomes are mapped,
  not serialized over existing states.

## Rejected alternatives

- A generic arbitrary provider-call adapter was rejected because it would widen
  operation and argument authority.
- Treating a response as success was rejected because exact readback is the
  verification boundary.
- Retrying after a lost response was rejected because it violates durable
  single-dispatch guarantees.
- Reusing process-local locks as durable leases was rejected because they have
  no owner, expiry, renewal, or recovery evidence.
- Pulling graph execution and generalized compensation into F3 was rejected as
  F4 scope.

## Explicit unresolved implementation decisions

F3-D must close backup restart reconciliation, configuration readback recovery,
adapter activation, and phase-by-phase process-loss acceptance. Dashboard
execution remains excluded unless the independently reviewed atomicity gate is
resolved. These decisions may not weaken the frozen dispatch, identity,
external-writer, or no-redispatch rules.
