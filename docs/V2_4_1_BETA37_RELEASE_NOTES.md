# Engineering 2.4.1-beta.37 release notes

Engineering 2.4.1-beta.37 stores truthful execution outcomes (HAMCP-181 PR2),
projects plan-level verification and rollback availability from them
(HAMCP-214), and adds the HAMCP-193 dashboard governed-path tests. It follows
2.4.1-beta.36 and includes it. It adds no tool, schema, profile, signed
registry, workflow, permission or dependency change, and **no F3 persisted
format change** (OD-2). It activates no new capability.

Acceptance: [`V2_4_1_BETA37_ACCEPTANCE.md`](V2_4_1_BETA37_ACCEPTANCE.md).
Design: [`HAMCP_181_TRUTHFUL_OUTCOMES_DESIGN.md`](HAMCP_181_TRUTHFUL_OUTCOMES_DESIGN.md)
(revision 4, owner decisions in section 8, implementation note in section 10).

## What changed for a client

PR1 (2.4.1-beta.10, #257) made every surface read one shared
dispatch-facts projection. PR2 makes the stored task outcome and the
producers behind it truthful.

- **Proven not sent (b).** When a call is stopped after the dispatch intent
  is committed and the producer can prove the provider call was never sent,
  the task now reads:
  - `task_state: failed_post_dispatch` (unchanged coarse state);
  - `terminal_outcome: failed_after_intent_not_dispatched` (OD-5);
  - `dispatch_phase: intent_recorded_not_sent`;
  - `provider_dispatch_occurred: false` and `dispatched_at: null`.

  Before, the same case read as "may have been sent". The proofs are
  positive only:
  - the shared F3 executor's no-send declaration when an adapter's
    dispatch callback fails before the send (configuration, dashboard, fan
    and helper-state adapters);
  - `call_not_sent` on the reviewed pre-write raise sites
    (`governance/resources.py`, `governance/entity_metadata.py`) and on a
    WebSocket authorization failure raised before the command frame is
    written (`AuthorizationError.call_not_sent`);
  - the upstream fan second-authority refusal.

  `HomeAssistantUnavailableError` alone is never a proof. Backup and
  lifecycle gateways keep their current send-uncertain labels.
- **Already present (a).** A task whose every child verified with no
  dispatch stores `terminal_outcome:
  desired_state_already_present_not_dispatched`, the string entity metadata
  already used.
- **Sent time (OD-6(ii)).** `dispatched_at` is now the instant the audited
  provider gateway began the call (basis `provider_call_start_time`),
  stored in the task's `verification_summary.provider_call_started_at`
  map. It is `null` until a send is proven. Tasks written earlier keep their
  stored basis.
- **No-mutation claims (OD-14).** `changed_by_engineering: false` and
  `sent_rejected_no_mutation` are published only on P1 (a configuration
  REST 4xx answer) or P3 (WebSocket `unauthorized`/`forbidden`). Any other
  error after a send stays unproven or uncertain.
- **Plan verification (HAMCP-214).** A `succeeded_verified` task now shows
  the plan's `verification.status: verified`, copied from the task's
  `verification_summary`, which stays the per-task authority. A
  single-operation plan carries its desired and actual fingerprints and
  `post_apply_fingerprint` equal to the readback; a multi-operation plan
  keeps them per operation and none at plan level. A delete's desired
  fingerprint is absence.
- **Rollback availability (HAMCP-214).** Once the task is final, the plan's
  creation placeholder is replaced by the truthful family rule
  (`available_pending_current_state_check`,
  `unavailable_no_verified_dispatched_change`, `unavailable_for_create`, or
  the label family's `rollback_not_supported`). It is display only:
  `rollback_change` still re-derives admission and reads live state.
- **Unchanged forward (OD-11).** `rollback_change` on a legacy F3 plan whose
  every child is proven unchanged ((a), (b) or r-proven) refuses with
  `rollback_not_available`, reason `forward_unchanged`. An unproven or
  uncertain forward keeps today's admission.
- **Health.** The four signed-registry providers report
  `core_admission: admitted` when the locally admitted signed generation
  admits every listed profile. The read is local only, never a Core call,
  and any failure reports the pending reason. Health never decides
  admission.
- **Compact receipts.** A minimal receipt sheds the duplicate
  `outcome_code` and duplicate task verification status last, so a receipt
  that fits under the PR1 rules keeps every value it had.

**The no-blind-redispatch invariant is unchanged.** An intent is never
discarded and an operation is never redispatched; a duplicate apply of a
terminal plan reuses its terminal authority and refuses.

## Disclosures

- **HAMCP-183 supersedes the live entity-registry error phase.** An
  entity-registry error result other than P3 is recorded as
  `dispatch_indeterminate` (since HAMCP-183, `494defc1`), so it projects
  `intent_recorded_send_uncertain` (c), not the design's (r-unproven). (c)
  is stricter: it never claims non-mutation. Historical beta.8 records of
  that error still project (r-unproven), tested on a record written by the
  exact beta.8 writer.
- **HAMCP-155 residual (c).** A pre-request raise inside
  `governance/configuration_delete.py` still stores send-uncertain (c). The
  same holds for the pre-request raises in
  `governance/entity_availability_exposure.py` and
  `governance/label_registry.py`, `governance/service.py:633` and `:655`, and
  the backup and lifecycle gateways. Those files are outside this release's
  authorized scope. Overstating a possible send is the safe direction.
- **OD-10.** Pre-PR2 records are not rewritten. There is no backfill of
  `terminal_outcome`; old (b) records are projected from their existing
  proof codes, and old (a) records from their verified-no-dispatch class.
- **Downgrade (design section 4.3, owner decision 2026-10-25).** The
  supported governed-plan rollback target is **2.4.1-beta.36**.
  `tests/test_hamcp181_beta36_downgrade.py` runs the exact beta.36 reader
  over beta.37-written records. Plans, tasks, `verification_summary` and
  terminal outcomes are all read without quarantine, rewrite or loss, with
  zero provider calls. Beta.36 shows its own display: `dispatched_at` is the
  intent commit time for a sent task, and the HAMCP-214 projections revert.
  2.4.1-beta.8 is a **fan/power/task-only floor**: those records load with
  zero provider calls, but beta.8 hides **every** governed plan written
  since HAMCP-213 (2.4.1-beta.29), because its `ChangePlan` reader rejects
  the `called_scripts` operation field and policy version `f2-v3`. That
  limitation predates beta.37 and was accepted as is. It is not fixed here.

## Compatibility

- No tool, descriptor or input schema change. The catalog is still **96
  tools, 97 with the optional YAML reader admitted**; static tools 68.
- The 17 signed Core capability profiles, the probe fingerprints, the
  owner-signed Core registry (sequence 12), the signed ha-mcp registry and
  the delete profile's contract fingerprint
  `sha256:5688af5d141b97ca33eadf251ecfbb8f3b1f833024b0ee1acea4973f558f887d`
  are unchanged (recomputed, base and head; see the PR).
- No F3 persisted-format change. Task JSON gains only the new
  `terminal_outcome` values and the `provider_call_started_at` map inside
  the existing `verification_summary` object.
- No workflow, permission, add-on configuration, dependency or stable-v1
  change.

**Protected runtime files.** Josh authorized the HAMCP-181 PR2 file scope
(design section 6). The changed files are listed in the PR. They include
`f3/contracts.py`, which holds the shared not-sent constants and which Josh
confirmed as an addition to the section 6 list (2026-10-25).

## Rollback

Reinstall the 2.4.1-beta.36 artifact. No stored format changes. The
executed beta.36 reader test (see Disclosures) loads every beta.37 record
with zero quarantine, rewrite or provider call. Beta.36 then shows its own
legacy phrasing and timestamps, and keeps the new stored values for a later
re-upgrade.
