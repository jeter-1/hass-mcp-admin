# HA MCP Engineering Server 2.2.0-beta.1

Version `2.2.0-beta.1` is the F1 durable-execution-task foundation. It keeps
the accepted 2.1A operation providers and verification contracts unchanged,
while separating mutable execution/recovery facts from immutable authorization
plans.

## Durable authority separation

- Existing change plans remain immutable, hash-stable authorization objects.
- Existing external approvals remain separately stored, bound to the exact plan
  hash, principal-separated, expiring, and single-use.
- One schema-v1 execution task owns mutable preflight, dispatch, observation,
  verification, error, and terminal evidence for a new execution.
- Task data is not part of the plan hash and cannot change plan authority.
- Historical plans are not backfilled with fabricated tasks or evidence.

The task repository is isolated at `execution-tasks-v1`. One atomic envelope
contains both the current materialized task and its append-only logical event
history. The repository rejects conflicting ownership, event truncation,
illegal transitions, changed immutable identity, inconsistent first-dispatch
deadlines, and event/materialization drift. Corrupt authority fails closed and
cannot open a replacement dispatch opportunity.

## Idempotency and recovery

`apply_change_plan` durably creates or resolves one task for the exact plan and
execution intent before preflight. Existing operation-specific checks and
provider arguments remain unchanged. At the current irreversible boundary,
approval consumption and plan dispatch intent retain their accepted guarantees,
and `dispatch_attempted` is durable before provider invocation.

Duplicate and concurrent callers receive the same task or the existing
`already_applied` result. A client timeout, provider response loss, process
restart, or startup reconstruction cannot create a second task or dispatch.
Dispatched operational tasks resume only through the existing readback
verifier. Pre-dispatch tasks are revalidated and never automatically
dispatched.

The first dispatch sets an immutable maximum post-dispatch deadline exactly 24
hours later. Verification may continue after plan or approval expiry. If no
safe terminal result exists by the deadline, the task becomes
`manual_review_required`; the provider is not retried.

## Public task tools

Three additive Engineering-native tools are included:

- `get_execution_task` reads one exact task and its bounded lifecycle evidence;
- `list_execution_tasks` returns bounded summaries filtered by exact state,
  terminal outcome, or plan ID; and
- `cancel_execution_task` cancels only `created` or `preflight` tasks with no
  dispatch evidence.

Post-dispatch cancellation is explicitly refused and does not stop readback
recovery. Cancellation is not rollback or compensation.

`apply_change_plan` additively returns the task ID, state, and reuse status.
`get_change_plan` additively reports a task reference, or identifies a
historical taskless record as legacy. Existing fields remain present.

## Catalog and compatibility

The static catalog is 48 Engineering tools: 25 canonical and 23
Engineering-native. Complete exact upstream admission adds the unchanged 26
reviewed reads for 74 total.

No upstream compatibility entry, per-tool fingerprint, dashboard attestation,
provider route, provider argument, direct-Home-Assistant exception, approval
authority, or fallback policy changes. Provider acknowledgement is not
redefined as verified success. Existing backup, controlled reload, add-on
restart, Home Assistant restart, and configuration verification remain
operation-specific.

## Operations and observability

`get_server_health` reports task-storage health, record/event counts,
corruption and write counts, active tasks by state, retained terminal outcomes,
duplicate-apply prevention, reconciliation runs, and last bounded task failure.
Audit covers task creation, approval-consumption projection, dispatch,
verification, cancellation, duplicate prevention, manual review, and
completion without approval principals or raw provider payloads.

Completed tasks follow the current 90-day governance retention. Downgrading to
2.1.x leaves the separate task namespace untouched, but 2.1.x cannot display or
resume F1 tasks. Reinstalling 2.2 restores access. Operators must not move task
records into legacy plan namespaces.

## Deferred work

This release does not implement F2 risk policy or approval UI, F3 shared locks,
F4 compensation, F5 generalized verification, comprehensive F6 historical
terminal migration, later F7 lifecycle work, MCP-native Tasks, or any new
cleanup/write surface.
