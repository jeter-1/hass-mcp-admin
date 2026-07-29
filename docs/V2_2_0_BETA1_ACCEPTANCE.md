# 2.2.0-beta.1 acceptance contract

Version: `2.2.0-beta.1`

Source baseline:
`efb560cfede4e5543f6ed47661a0b497a639b46b`

This is the bounded source and later operator-controlled runtime acceptance
contract for F1 durable execution tasks. Source review and CI must not access a
live Home Assistant instance, approve a live plan, dispatch an operation,
publish an image, merge, or deploy.

## Immutable release boundaries

- Stable v1.1.2 source and packaging are unchanged.
- Existing plan hashes, approval records, operation providers, provider
  arguments, compatibility entries, the reviewed 26-read catalog, dashboard
  trust, and zero fallback are unchanged.
- Expected complete catalog: 48 Engineering plus 26 delegated, 74 total.
- MCP-native Tasks negotiation is absent.
- F2-F7 authority is absent.

The review must record the exact final source SHA, clean tree, package version,
image build results, and—after any separately authorized publication—immutable
image index/platform digests and OCI revision. Do not predeclare unpublished
digests.

## Persistence and schema acceptance

Validate:

1. Task schema version 1 round-trips deterministically.
2. The materialized record exactly replays from its ordered events.
3. Immutable task identity, plan ownership, idempotency ownership, append-only
   history, and the first-dispatch deadline cannot change.
4. `execution-tasks-v1` is not enumerated by the legacy plan repositories.
5. Atomic-write interruption leaves the prior envelope intact.
6. Corrupt or contradictory evidence fails closed, reserves plan ownership,
   increments bounded health evidence, and never creates a replacement task.
7. Completed tasks use the configured governance retention, currently 90 days.
8. Task changes do not alter the exact immutable plan hash.

Required event families are `task_created`, `preflight_started`,
`preflight_failed`, `approval_consumed`, `dispatch_attempted`,
`provider_response_recorded`, `verification_started`,
`verification_evidence_updated`, `task_cancelled_pre_dispatch`,
`manual_review_required`, and `task_completed`.

Required current states are `created`, `preflight`, `dispatching`, `observing`,
`verifying`, `succeeded_verified`, `failed_pre_dispatch`,
`failed_post_dispatch`, `manual_review_required`, and
`cancelled_pre_dispatch`. Reserved future states must be rejected.

## Apply and idempotency acceptance

Prove for current configuration and operational plan families:

- unapproved, rejected, expired, stale-hash, and ineligible plans create no
  task or provider dispatch opportunity;
- a valid apply creates one task before preflight;
- concurrent apply calls resolve the same task and at most one dispatch;
- plan dispatch intent, approval consumption, and task dispatch evidence are
  durable before provider invocation;
- response loss returns/reconstructs the same task;
- repeated apply after dispatch is readback-only;
- verified completion returns `already_applied` on another exact apply;
- approval remains consumed after the irreversible boundary; and
- zero fallback remains observable.

## Recovery and deadline acceptance

At startup, validate task/event consistency before task reconciliation. Task
rehydration itself must report zero provider dispatches. Eligible dispatched
operational plans may then enter the existing bounded readback-only reconciler.

Test:

- client disconnect after dispatch followed by terminal readback;
- Engineering process replacement followed by task rehydration;
- provider response loss without redispatch;
- pre-dispatch authority expiry without approval consumption;
- unresolved dispatch at exactly the 24-hour boundary;
- transition to `manual_review_required` after the boundary; and
- no redispatch after the deadline.

Existing operation-specific evidence remains mandatory: provider
acknowledgement alone cannot complete backup, reload, Engineering
`process_identity`, upstream `upstream_readmission`, other-add-on restart, Home
Assistant restart, or configuration verification.

## Cancellation acceptance

`cancel_execution_task` may cancel only a `created` or `preflight` task with no
dispatch event/attempt. It must consume no approval and invoke no provider.
After dispatch it must return
`cancellation_not_permitted_after_dispatch`, keep recovery active, and perform
no compensation or rollback.

## Public compatibility acceptance

Verify exact schemas and annotations for:

- `get_execution_task(task_id)`;
- `list_execution_tasks(state, terminal_outcome, plan_id, limit)`; and
- `cancel_execution_task(task_id)`.

`apply_change_plan` must add task ID/state/reuse evidence without removing
legacy response fields. `get_change_plan` must add only a bounded task
projection. Historical plans remain readable and are identified as legacy
without fabricated tasks.

`server_info`, `list_capabilities`, and `get_server_health` must derive and
report 48 Engineering tools plus the exact admitted delegated count.

## Required validation

Run and record:

- focused task schema/storage/state/fault/idempotency/cancellation/recovery
  tests;
- all governance, approval, operational lifecycle, audit, observability, and
  public schema tests;
- complete Python suite with exact tests and skips;
- compilation, dependency audit, metadata, YAML, PowerShell, protected-path,
  whitespace, and evidence gates;
- compatibility registry regeneration/drift validation;
- disposable Home Assistant contracts;
- exact-image ha-mcp 7.14.1 and 7.14.2 lanes;
- stable-v1 isolation/build;
- Engineering image build; and
- amd64, arm64, and arm/v7 no-push builds.

No failing test may become an expected skip merely to complete F1.

## Later runtime acceptance

Do not execute this sequence during source implementation.

1. Deploy the exact reviewed Engineering image while retaining rollback
   artifacts.
2. Verify version/build, clean provenance, 48+26=74 tools, exact upstream
   admission, governance/audit health, task-storage health, and zero fallback.
3. Create but do not approve a harmless bounded plan; confirm no task or
   dispatch exists during planning/approval request.
4. Externally approve the exact plan hash and apply once.
5. Confirm one task, one provider attempt, additive task reference, existing
   operation-specific verification, and a terminal verified result.
6. Repeat the exact apply and confirm `already_applied` with no dispatch.
7. Exercise pre-dispatch cancellation only in an authorized disposable
   scenario.
8. Exercise a permitted restart/recovery scenario only when separately
   authorized; confirm the task survives process replacement and readback
   cannot redispatch.
9. Confirm audit, health counters, governance storage, and task evidence remain
   bounded and persistent.

Rollback to the accepted 2.1.1-beta.3 build preserves `/data` and leaves F1
task files untouched, but that older build cannot display or resume them.
Re-upgrade to the exact 2.2.0-beta.1 artifact to resume F1 task visibility.
