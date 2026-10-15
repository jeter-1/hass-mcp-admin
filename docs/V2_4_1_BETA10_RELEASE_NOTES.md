# Engineering 2.4.1-beta.10 release notes

Engineering 2.4.1-beta.10 makes every public surface tell the same, truthful
story about whether a governed or typed change actually reached a provider
(HAMCP-181 PR1, with HAMCP-167). It builds on beta.9's truthful
entity-registry error results (HAMCP-183). The change is display
only: no stored record, `terminal_outcome` string, task state or producer
changes, and no new write, dispatch or fallback path is reachable.

Before this release, each reader derived "dispatched" on its own, mostly from
whether a dispatch intent had been saved. A dashboard setter that was never
called could therefore read as dispatched on a plan read, a task list or a
compact receipt, a verified F3 reload left `plan.operational.dispatch` at
`dispatched: false`, and counts that are really saved intents were labelled
dispatches.

**One projection.** A single module, `f3_runtime/dispatch_facts.py`, derives
the dispatch facts from the stored F3 child records (or the legacy task fields
for pre-F3 tasks). Every surface reads it: the apply result and its nested
task and plan views, `get_change_plan`, `list_change_plans`,
`get_execution_task`, `list_execution_tasks`, plan observability, compact and
minimal receipts, duplicate-apply and cancellation refusals, the task-lifecycle
and F3 child audits, the configuration gateway audit, F3 health,
reconciliation items, the admin F3 review page, and the typed fan and power
receipts and audits. An architecture test fails if a public projection reads
the legacy predicate directly.

**New fields** (added beside every existing field, which keeps its value):
- `dispatch_phase`, one of `not_dispatched_already_present`,
  `not_dispatched_pre_intent`, `intent_recorded_not_sent`,
  `sent_rejected_no_mutation`, `sent_response_received`,
  `sent_failed_mutation_unproven` and `intent_recorded_send_uncertain`. A
  parent takes its most severe child phase; uncertainty dominates.
- `provider_dispatch_occurred` and, where proven, `changed_by_engineering`,
  `intent_recorded`, `dispatch_performed` and `provider_response_received`.
  `changed_by_engineering` is `null` whenever any child is uncertain or
  unproven.
- `attempt_counts_basis` beside every stored attempt or dispatch counter
  (`saved_dispatch_intent`, `saved_dispatch_intent_no_setter_call`,
  `no_dispatch_intent`), plus `attempt_count_scope` and
  `child_dispatch_phases` on multi-child parents. Compact and minimal receipts
  keep the basis next to any counter they keep.
- `dispatched_at_basis`, `dispatch_intent_recorded_at` and
  `provider_response_recorded_at` (owner decision OD-6(ii)).

**`dispatched_at`.** Fan and power keep publishing `dispatched_at: null`; the
new basis `send_time_not_recorded` says why, and the companion times come from
stored fields only. No field is the provider send time. For governed tasks,
`dispatched_at` keeps its stored value (basis `dispatch_intent_commit_time`),
except a proven not-sent task (the beta.8 dashboard Core-retirement case),
which now publishes `null` with basis `call_not_sent`. The stored value is
unchanged and is published as `dispatch_intent_recorded_at`.

**Sent but rejected.** A provider rejection is projected as
`sent_rejected_no_mutation` (`changed_by_engineering: false`) only with a
provider-specific proof: an automation or script REST 4xx (P1), or an
entity-registry WebSocket `unauthorized` with `provider_permission_denied`
(P3). Every other confirmed failure with a response, including backup,
reload, restarts, helper state and an entity-registry error result, is
`sent_failed_mutation_unproven` and never claims no change. A dispatch result that carries an adapter
uncertainty code stays `intent_recorded_send_uncertain` even when a response
arrived, which covers beta.9's entity-registry error result after send: readback
settles the task state, never the dispatch phase, and `changed_by_engineering`
stays `null`.

**Wording and labels.**
- A duplicate apply after a pre-dispatch refusal no longer says the change
  was "already dispatched" (audit finding F-03). The message follows the
  phase: no intent, intent recorded but not sent, or intent recorded.
- Cancellation refusal says "after dispatch intent was recorded" instead of
  "after dispatch", and names the no-intent case.
- The configuration gateway audit no longer labels a local pre-send refusal
  as a received provider response; it records `provider_result_uncertain`.
- The admin F3 review page shows "Dispatch intents" instead of
  "Dispatch count", plus the phase.
- `plan.operational.dispatch` for F3-backed plans is projected from the F3
  facts (marked with `projection_source`), so a verified F3 reload no longer
  reads as not dispatched. `operational.verification` keeps its stored value;
  `authoritative_verification_field` continues to name
  `execution_task.verification_summary`, which reports the verification.

**Unchanged.** Every `status`, `state`, `terminal_outcome`, stored counter and
stored timestamp keeps its value; the beta.8 dashboard and V9 entity-metadata
responses are value-identical apart from the added fields. Retry, redispatch,
duplicate suppression, claim, cancellation and rollback eligibility still use
the stored-intent predicate, never the projection. Historical audit records
are not rewritten.

**Known limitation (accepted for this release).** The backup, lifecycle and
helper-state gateway audits still label by the provider exception alone:
`provider_response_lost` when it says dispatched and
`provider_invocation_refused` when it does not. Two cases disagree with F3:
a refusal after the intent callback committed is labelled "refused" although
F3 records it as uncertain, and a confirmed failure with a response is
labelled "response lost" although a response arrived. These labels are never
used as retry, redispatch or no-call authority; the F3 record and its
`dispatch_phase` are authoritative. A regression test pins each case next to
its F3 phase. A later release corrects the labels.

Historical records written by beta.8 are read through the projection without
being rewritten; committed fixtures generated by the exact beta.8 writer
(`7370ad2`) prove this.

No tool, input schema, description, routing, signed registry, HOLD, support
window, persisted format, workflow or stable-v1 change is included. The
healthy catalog remains **87 tools: 59 native and 28 delegated reads**.

Downgrade: beta.9 reads every beta.10 record, because nothing new is
persisted. A downgraded server loses the added fields and returns to the
beta.9 wording and labels.

Follow the [beta.10 acceptance contract](V2_4_1_BETA10_ACCEPTANCE.md).
