# Engineering 2.4.1-beta.10 acceptance

This contract covers HAMCP-181 PR1 (with HAMCP-167): the shared, display-only
dispatch-facts projection and truthful display surfaces, plus audit rider F-03,
on top of beta.9 (HAMCP-183). It separates four stages: source and disposable
acceptance, release CI, publication, and installed Engineering acceptance.
This document authorizes none of the later stages.

## 1. Source, review and CI

Bind the following:
- the design `docs/HAMCP_181_TRUTHFUL_OUTCOMES_DESIGN.md` revision 4 (merged
  through #255 at `3c9cb0f`);
- Josh's owner decisions of 2026-10-14 on HAMCP-181: P2 withdrawn (P1 and P3
  are the only non-mutation proofs), OD-6(ii), OD-7 exclusions, OD-14
  projection-only, OD-13 (only the design section 6 PR1 files are
  authorized) and the F-03 audit rider;
- the base `main` (2.4.1-beta.9 at `690a00b`), and every later main merge into
  the branch;
- the final reviewed commit and tree.

Earlier review does not cover changed code.

Materialize 2.4.1-beta.10 in all three version authorities through the existing
staging tool. Require exact acceptance and release-note resolution, with no
staged declaration left over. Complete Full/Evidence on clean source and the
bounded independent review. The review needs a security section covering:
- zero new write, dispatch, service-call, fallback or forwarding reachability;
- retry, redispatch, duplicate, claim, cancellation and rollback eligibility
  still reading the stored-intent predicate, never the projection or an audit
  label;
- the (b) predicate reading only the F3 record and its required proof code;
- the (r-proven) rule accepting only P1 and P3;
- no stored-format, `terminal_outcome`, task-state or producer change.

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Preserve the following:
- stable v1.1.2;
- historical persisted fixtures and durable formats, including the frozen
  `f3_lock_recovery` and `configuration_reverification` hashes;
- every stored `status`, `state`, `terminal_outcome`, counter and timestamp;
- the beta.8 dashboard (#253), V9 entity-metadata and beta.9 (HAMCP-183)
  entity-registry error-result responses, value for value, apart from the
  added fields;
- locks, holds and uncertainty;
- probe fingerprints, the Core capability profiles, every owner-signed Core
  registry entry and the signed ha-mcp registry;
- the 8.6.0 HOLD and the support window;
- dependency locks, build inputs, pinned runners and workflow permissions.

The public catalog stays at **87 tools (59 native, 28 delegated)**, and every
beta.9 descriptor is unchanged.

Offline source evidence must prove each of the following with the shipped
governance, F3 runtime, fan and power services and synthetic providers.

**One fact set on every surface.** For the dashboard Core-retirement probe,
its late-drift variant, an operational send-uncertain task and 0-, 1- and
3-child parents, the apply result, nested task and plan views,
`get_change_plan`, `list_change_plans`, `get_execution_task`,
`list_execution_tasks`, observability, duplicate and cancel refusals, the
lifecycle and child audits, F3 health, reconciliation items and the admin
page report the same `dispatch_phase` and `provider_dispatch_occurred`, at
full, compact (2,000) and minimal budgets and across a restart.

**Phase rules.** Every phase, every row of the parent truth table in both
orders, the single (b) predicate with its controls, (r-proven) positives
P1 and P3 only, and the (r-proven) negatives (backup, reload, helper state,
entity-registry error without `provider_permission_denied`, `input_*`
WebSocket error, dashboard structured rejection, exception without a
response). A dispatch result with an adapter uncertainty code and a received
response, including the beta.9 entity-registry error result after send, stays
`intent_recorded_send_uncertain` through readback, with
`changed_by_engineering: null`.

**Timestamps.** Fan and power keep `dispatched_at: null` with
`send_time_not_recorded` and stored companion times through the receipt, the
snapshot audit and the request audit, including a delayed-send test in which
no field equals or is labelled the send time. Governed (b) publishes `null`
with `call_not_sent`; every other intent phase keeps the stored value with
`dispatch_intent_commit_time`.

**Historical records.** Records written by the exact beta.8 writer
(`tests/fixtures/f3_beta8_dispatch_phase/`, provenance `7370ad2`) project to
the expected phase on every reader and stay byte-identical after startup
recovery and reads.

**S16.** After a verified F3 reload, `plan.operational.dispatch` reports the
dispatch. `operational.verification` keeps its stored value and
`authoritative_verification_field` still names
`execution_task.verification_summary` (RC6 contract).

**Accepted limitation.** The backup, lifecycle and helper-state gateway
audits keep labelling by the provider exception alone, so a post-intent
callback failure reads `provider_invocation_refused` and a confirmed failure
with a response reads `provider_response_lost`. A regression test pins the
label next to the correct F3 phase for a pre-intent refusal, a post-intent
callback failure, a confirmed failure with a response and a lost response.
The label is never retry, redispatch or no-call authority. A later release
corrects it.

**Mutation checks.** Each of the following fails a test:
- reverting any surface to the legacy predicate;
- dropping a counter basis, or shedding a counter without its basis;
- accepting an unproven rejection or a P2 shape as (r-proven);
- reading a missing child or an unestablished manifest as not dispatched;
- claiming a verified parent over an uncertain child;
- publishing an intent or clock time as the fan/power send time;
- publishing the intent time as governed (b) `dispatched_at`;
- reading a responded uncertainty code as sent;
- reverting the F-03 duplicate wording or the cancel wording.

## 2. Publication and recovery preparation

Publication needs Josh's applicable Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.10;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the known-good beta.9 and beta.8
artifacts and options for recovery. Nothing new is persisted, so downgrade to
beta.9 needs no migration.

## 3. Installed Engineering

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to the published
   image index digest, the v2.4.1-beta.10 tag and the protected `main` merge
   commit. A source version alone does not prove the installed image.
2. Compare all 87 public descriptors, Core signed selection, storage and audit
   health, F3 readiness, upstream admission and zero fallback.
3. **Power canary.** On an entity Josh chooses, with Josh's explicit
   authorization of each physical action, run `turn_off`, then `turn_on`, or
   restore the recorded pre-canary state. Each step must report exactly one
   provider attempt and `succeeded_verified` by readback. Each receipt and
   `get_execution_task` must show `dispatched_at: null`,
   `dispatched_at_basis: send_time_not_recorded`, a non-null
   `dispatch_intent_recorded_at`, a `provider_response_recorded_at` that is
   not earlier than it, `dispatch_phase: sent_response_received` and
   `attempt_counts_basis: saved_dispatch_intent`. Leave the entity in its
   recorded pre-canary state.
4. **Dashboard canary.** On a test dashboard Josh chooses, with Josh's
   explicit authorization of each write, repeat the beta.8 canary: record
   the configuration, plan, approve and apply a bounded change, verify by
   readback, and restore through a second governed plan. In addition:
   - `get_change_plan` for each plan must show the F3 facts in its
     `operational.dispatch` block (`projection_source: f3_dispatch_facts`,
     `dispatched: true`), not the stale `dispatched: false`, and
     `execution_task.verification_summary` (the
     `authoritative_verification_field`) must report the verification.
   - The apply result, `get_execution_task`, `list_execution_tasks` and
     `get_change_plan` must all report `dispatch_phase:
     sent_response_received` and `provider_dispatch_occurred: true`.
   - Replaying an apply must not dispatch again, and its refusal must read
     "already recorded its single dispatch intent".
5. **Helper-state no-op display.** On a helper Josh chooses, plan and apply,
   with Josh's authorization, a governed state change to the value the
   helper already has. The apply must make no provider call and report
   `dispatch_phase: not_dispatched_already_present`,
   `provider_dispatch_occurred: false`, `changed_by_engineering: false` and
   `attempt_counts_basis: no_dispatch_intent`. Its `status` and
   `terminal_outcome` must be unchanged from beta.9 (`status: applied`); the
   helper `already_present` status is a later release.

The accepted gateway audit-label limitation above applies to the installed
server. Do not use those labels to judge whether a backup, reload, restart or
helper-state call was sent; read `dispatch_phase`.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
