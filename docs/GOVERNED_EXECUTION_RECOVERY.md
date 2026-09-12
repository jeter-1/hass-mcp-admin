# Governed execution and bounded response recovery

This behavioral contract describes the local remediation of the RC5 canary
defects. It does not establish publication, deployment or household recovery.
Historical RC5 acceptance and incident receipts remain unchanged.

## Active execution and recovery

An active executor owns pre-dispatch approval consumption and progress.
Recovery defers projection when the executor reports an active ownership
collision. A reused, expired claim is distinct: genuine owner-loss recovery
continues to project progress and terminal outcomes.

Before any child in the sequence has durable dispatch intent, nonterminal
progress must not replace an approved plan with verification-required status.
Terminal outcomes still project normally. After intent, recovery remains
readback-only and cannot redispatch. Approval expiry, principal/hash/policy
binding, current-state and Core authority, and durable ownership checks remain
in force.

The deterministic regression pauses the real governed helper executor at its
Core reconciliation await, runs the real recovery sweep, then resumes the
owner. It requires the original approval to remain valid and exactly one
provider dispatch. Separate controls cover owner loss, cancellation, invalid
approvals, post-intent overlap, terminal parents and settled resources.

## Operational stop

The test helper was last observed ON in the failed RC5 receipt. Offline tests
cannot change or establish its current state. After separate review, release,
deployment and runtime verification, recovery needs current-state reconciliation,
a fresh exact OFF plan, authenticated panel approval, one apply, task inspection
and independent OFF readback. Do not reuse the failed restoration plan or its
approval. Dashboard canaries remain paused.

## Plan verification attribution

For F3-backed plans with a retained task, `get_change_plan` identifies
`execution_task.verification_summary` as its authoritative verification field.
It uses the same parent/child projection as `get_execution_task`, including
terminal zero-dispatch child normalization. A compact plan summary retains this
pointer and its target. Task state and verification remain distinct: created,
pending, failed, or manual-review evidence never becomes verified merely from
an attempt count or a plan's legacy status.

The stored legacy `operational.verification` record is preserved unchanged.
Taskless legacy operational plans continue to identify that record as their
verification authority. These read projections do not change persisted plans,
approvals, tasks, dispatch ownership, or provider behavior. Retrieve the task
and child details for reconciliation; repeated reads cannot authorize a retry.

## Oversized structured responses

Small responses retain their existing JSON representation. Larger responses
first use lossless compact JSON. If that still exceeds the configured bound,
a valid JSON projection retains request/outcome and reconciliation identities,
provider attribution and available dispatch/verification facts. It removes
optional subtrees without modifying the original action result. ASCII escaping
keeps the character limit an upper bound on UTF-8 output bytes as well.

The additive response_completeness object explicitly identifies truncation,
omissions and existing read-only retrieval operations. It does not change the
reported action outcome or grant approval. At small budgets, an explicitly
labelled primary_reconciliation_receipt exposes primary plan/task IDs and hashes
in the data or details object. It is not a complete plan disclosure.
The legacy truncation notice remains inside this JSON object so existing routing
metrics still count bounded output. It is never appended outside the JSON.
The smallest receipts may omit repeated outcome labels and detail-navigation
hints while retaining the exact identities and authoritative task outcome.

Both flat get_execution_task results and nested apply receipts preserve task
state, available attempt accounting, dispatch evidence and bounded verification
outcomes. Attempt counts do not independently prove dispatch. Missing and null
facts remain unknown; they do not become zero, false or verified.

Use get_execution_task for a retained task and get_change_plan with page_size=1
for a plan. Plan detail_sections lists the existing summary, obligation_evidence
and downstream_profiles selections. Follow their returned cursors/fragments
through completion before treating their disclosures as complete. A bounded
receipt is not a replacement for authenticated panel approval. Omitted arbitrary
provider payloads or task event histories are not promised to have a complete
retrieval mechanism. An inadequately small custom budget may prevent complete
detail retrieval; report that gap rather than treating a partial receipt as
complete. No new tool or persistence format is introduced.

Serialization runs after action outcome classification. An encoding problem
must not be mapped to an action failure after a persisted effect. Never repeat
a mutation to recover its response; reconcile the returned plan/task identities.

## Integrity finding coalescing

Identical findings coalesce before analysis caps, totals and pagination. Equality
includes every finding field and its evidence references. Distinct sources,
paths, warnings and excerpts remain visible. A reused dynamic evidence ID does
not overwrite different content; conflicting references receive deterministic
content-qualified IDs. Finding IDs retain their existing source/path meaning
and alone are not a sufficient equality test.

The existing unresolved_dynamic_reference_count and the summary's
unresolved_in_requested_scope_count still count raw references. finding_count
and reported_finding_count count visible coalesced findings. Coverage gaps,
manual-review requirements and cursor continuation semantics remain unchanged.
