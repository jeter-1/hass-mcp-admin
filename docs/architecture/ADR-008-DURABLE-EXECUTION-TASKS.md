# ADR-008: Durable execution tasks

Status: Accepted for `2.2.0-beta.1`

## Context

An immutable change plan answers what may be done. Its exact plan hash is the
authority bound to an external approval. Execution is different: it accumulates
mutable facts such as preflight progress, provider attempts, readback evidence,
and the terminal result. Keeping those facts only in the plan lifecycle makes
client reconnects and process recovery harder to describe without weakening the
plan's authorization boundary.

F1 separates three records:

- the existing immutable, hash-stable change plan owns intent, risk, target,
  provider constraints, and required verification;
- the existing external approval remains bound to that exact plan hash and is
  consumed only at the established irreversible dispatch boundary; and
- one durable execution task owns mutable execution and recovery state.

No MCP-native Tasks protocol capability is advertised in F1. The internal task
record is authoritative and a later protocol adapter may project it without
changing this persistence contract.

## Decision

### Identity and cardinality

Every F1 task has a random opaque `task_id`, schema version `1`, the exact
`plan_id` and `plan_hash`, and a deterministic idempotency key derived from the
plan and execution intent. The task repository enforces one authoritative task
per plan and idempotency key. Duplicate or concurrent apply requests reuse that
task. Only that task may cross the plan's dispatch boundary.

Mutable task data is never an input to the plan hash. Historical plans are not
backfilled with fabricated tasks or execution evidence.

### Persistence

Tasks live under the versioned `execution-tasks-v1` governance namespace. Each
task file is one atomically replaced JSON envelope containing:

- the materialized task record; and
- its ordered, append-only logical lifecycle event sequence.

Keeping both views in one envelope makes an event append and materialization
update one crash-safe filesystem replacement using the existing flush, `fsync`,
and `os.replace` convention. The repository rejects truncated event histories,
invalid transitions, duplicate ownership, changed immutable identity fields,
and materialized/event contradictions. A contradiction discovered while a
validated task is being reconciled becomes `manual_review_required`. A record
that cannot first pass schema/event/materialization validation is quarantined,
counted, and reported as explicit task-storage corruption. Its plan ownership
remains reserved, so neither case can be silently repaired into a new task.

The exact `2.1.x` plan repository does not enumerate this namespace. A downgrade
therefore leaves task files untouched, although that older runtime cannot
display or resume them.

### State machine

| State | Allowed initiator | Guard | Persisted event | Terminal |
|---|---|---|---|---|
| `created` | apply | exact eligible plan, exact hash, valid external approval, no existing task | `task_created` | no |
| `preflight` | apply | `created`; current plan and approval remain valid; an already-desired configuration may complete here without dispatch | `preflight_started` | no |
| `dispatching` | governed apply | preflight passed; approval consumption and plan dispatch intent use the existing boundary | `approval_consumed`, `dispatch_attempted` | no |
| `observing` | apply/reconciler | a dispatch may have occurred and operation-specific readback is pending | `provider_response_recorded`, `verification_evidence_updated` | no |
| `verifying` | apply/reconciler | dispatched task has operation-specific evidence available to check | `verification_started`, `verification_evidence_updated` | no |
| `succeeded_verified` | verifier | the existing operation-specific verification contract passed | `task_completed` | yes |
| `failed_pre_dispatch` | apply | preflight failed before any dispatch attempt | `preflight_failed`, `task_completed` | yes |
| `failed_post_dispatch` | verifier | dispatch occurred and the existing verification contract conclusively failed | `task_completed` | yes |
| `manual_review_required` | repository/reconciler | contradictory irreversible history, indeterminate result, or 24-hour post-dispatch deadline elapsed | `manual_review_required` | yes |
| `cancelled_pre_dispatch` | task cancellation tool | no dispatch event or provider attempt exists | `task_cancelled_pre_dispatch` | yes |

The schema reserves names for `waiting_for_lock`, `compensating`,
`partial_application`, `compensated`, and `superseded`, but F1 rejects
transitions to them. They do not authorize F2-F4 behavior.

Illegal transitions fail deterministically. A terminal task cannot reopen.

### Dispatch and recovery

The task is durable before preflight begins. Immediately before the existing
provider invocation, the task records `dispatch_attempted` and the existing plan
path persists its dispatch intent and consumes approval. A confirmed dispatch
sets an immutable maximum post-dispatch deadline to exactly 24 hours after the
first dispatch timestamp.

After dispatch:

- plan or approval expiry does not stop readback-only verification;
- client timeout or MCP disconnect does not create a new task;
- repeated apply and startup reconciliation never issue another provider
  action;
- an attempt without a complete provider response remains indeterminate and
  uses only the operation's current readback contract; and
- a nonterminal task becomes `manual_review_required` when its immutable
  24-hour deadline passes.

Pre-dispatch tasks are revalidated after startup but are not automatically
dispatched. Expired or invalid authority fails closed. Existing operational
plan reconciliation remains the only action-specific recovery engine and stays
readback-only.

### Legacy projection

`apply_change_plan` and `get_change_plan` add a bounded task reference for F1
executions. Existing plan fields remain a compatibility projection of the
current task and operation-specific lifecycle. A plan without a task is
reported as a legacy record and remains readable. No historical outcome is
invented.

### Cancellation

`cancel_execution_task` can transition only `created` or `preflight` tasks that
have no dispatch evidence. It neither consumes approval nor invokes a provider.
After `dispatch_attempted`, cancellation returns
`cancellation_not_permitted_after_dispatch`; verification and recovery
continue. Cancellation is not rollback or compensation.

### Security boundary

Task records contain bounded, redacted summaries and references, not raw
approval principals, credentials, or provider payloads. They do not add a
provider route, provider argument, generic service call, fallback, rollback,
compensation, or new Home Assistant write surface. Current operation-specific
verification remains the only source of verified success.

## Consequences

F1 adds `get_execution_task`, `list_execution_tasks`, and
`cancel_execution_task`. With exact upstream admission the expected catalog is
48 Engineering tools plus 26 delegated reads, 74 total.

Operators can inspect execution independently of the initiating MCP response,
and one task can complete after a client or Engineering process disconnect.
Manual review is explicit when irreversible evidence is corrupt or cannot be
resolved within 24 hours. Cross-task locks, compensation, generalized
verification, elevated approval UX, new cleanup writes, and MCP-native Tasks
remain future work.
