# Engineering 2.4.1-beta.54 acceptance

This contract covers HAMCP-263: a dispatched governed dashboard update
reaches a truthful terminal outcome through an actual readback, and a
child of an already-terminal parent is settled with its locks released.
It follows 2.4.1-beta.53 and includes it.

See also the release notes:
[`V2_4_1_BETA54_RELEASE_NOTES.md`](V2_4_1_BETA54_RELEASE_NOTES.md).

**Release target.** `2.4.1-beta.54`, following 2.4.1-beta.53 (#333,
HAMCP-071), which merged first; this branch merged `main` and kept
beta.54. It is materialized in all three version authorities with no
staged declaration left. Nothing is published or deployed.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind:
- Josh's HAMCP-263 direction:
  - truthful terminal outcome through one actual readback;
  - no redispatch, ever;
  - at-most-once dispatch and approval binding unchanged;
  - the terminal parent d63e3a87 is not changed (HAMCP-253 owns its
    acknowledgment);
  - other families' gaps are reported, not fixed;
- `2.4.1-beta.54` materialized in all three version authorities before
  Ready, with exact document resolution;
- the PR head SHA, its `validate` run and the Evidence tier log named in
  the PR.

The review needs a security section covering:
- **No new reachability.** The only new provider call is one read-only
  dashboard readback per settled child, through the existing durable
  intent recovery path with `readback_only=True`. Dispatch stays
  unreachable on that path. No write, service call, reload, route,
  provider, fallback or upstream dispatch is added.
- **Binding unchanged.** Readback still requires the exact persisted
  provider operation, argument hash, lock hash and lock tokens. Fix A adds
  a class constant only; the prepared-operation hash is unchanged.
- **Lock release rule.** Only expired, non-conflict-hold locks whose owner
  child is terminal with a dispatch intent and whose exact execution
  authority matches are released. An unresolved manual-review owner keeps
  its declared selective hold keys. Active leases are untouched.
- **Crash-safe cleanup (R1).** Before the readback can terminalize a
  child of a terminal parent, recovery durably marks it
  `terminal_parent_child_cleanup_pending` in the existing
  `reconciliation_result` runtime field (no format change). The marker
  clears only after the child's last exact-owner lock is released. A
  later sweep, including the first after a restart, finishes the release
  without another read, dispatch or parent rewrite. A marked child counts
  as pending reconciliation and never takes the terminal-fault parent
  projection. A conflict hold, a foreign owner, a changed mode or
  generation, a nonterminal child, a non-terminal or non-F3 parent, or a
  non-dashboard adapter refuses the release and keeps the marker.
- **No terminal record rewritten.** The terminal parent record and
  terminal child records are never rewritten or deleted.

Required offline evidence, `tests/test_hamcp263_dashboard_readback.py`:
- the Step 1 regression: retained expired locks block an unrelated
  governed apply, and settle;
- end to end: transient readback then verified; mismatch is a
  verification failure; unavailable is bounded indeterminate manual
  review; restart during observation never redispatches;
- the sweep leaves an F3 dispatched task pending, bounded by
  `maximum_post_dispatch_deadline`;
- the live shape: the child is terminal (verified, mismatch or
  indeterminate), its locks are released, the parent is unchanged, F3 is
  not `recovering`, and an unrelated apply succeeds. A binding failure
  retries without any read. A restart does not redispatch. A non-dashboard
  child is neither read nor released;
- exactly one dashboard write on every path;
- lock release for terminal verified and mismatch owners only after their
  lease expires, and target-key retention for an unresolved manual-review
  owner;
- (R1) a process stop after the child is terminal but before any release,
  after one of three releases, and after all releases but before the
  marker clears, plus a transient release failure: after a restart and a
  sweep, all three locks are released, F3 is not `recovering`, the parent
  bytes are unchanged and nothing is read again or redispatched. Repeated
  sweeps and restarts are idempotent and an unrelated apply then
  succeeds. Negative controls cover the refusals listed above and an
  ordinary manual-review hold;
- the existing F3, dashboard, execution-task, recovery and readiness
  suites, unchanged;
- mutation testing on the changed code in a copied tree;
- the complete repository suite in the Evidence tier.

## 2. Release CI

The exact materialized head's `validate` check passes, including the
beta.53 → beta.54 release transition.

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow.

## 4. Installed Engineering acceptance (separately authorized)

Deployment authorizes neither phase. Phase (a) is read-only. Phase (b)
writes and needs Josh's explicit approval before it starts.

### Phase (a): read-only

The architect checks with read-only tools. `main-operations` is the
offline test fixture, not a verified live target: use the dashboard
target and lock keys recorded in child 0ee87ea8's persisted intent.

1. **Child 0ee87ea8.** Within a few recovery passes it is terminal:
   - `succeeded_verified` if its target dashboard exactly matches the
     approved configuration (seeing the change land earlier does not
     prove today's configuration still matches);
   - verification failure on a mismatch;
   - `manual_review_required` with `observation_evidence_exhausted` if
     the readback is unavailable.
   Its runtime shows `terminal_parent_child_readback_settled`, never
   `terminal_parent_child_cleanup_pending`. The audit log shows exactly
   one readback and no dispatch. Do not infer a verified outcome from the
   terminal state alone.
2. **Locks.** The three locks the child retained (its dashboard target
   key, `home_assistant:core` and `addon:ha_mcp`, as persisted) are gone.
3. **Parent d63e3a87.** It is unchanged: `manual_review_required`,
   `dispatched_task_has_no_readback_reconciler`.
4. **Health.** F3 status is no longer `recovering`.
5. **Smoke and regression.** The read-only smoke and regression checks
   pass.

### Phase (b): writes, only with Josh's approval

1. **Fresh dashboard round trip.** Plan, approve and apply one governed
   update to the `engineering-acceptance` dashboard. It is not refused
   with `lock_conflict`; it reaches `succeeded_verified` through exact
   readback; then restore the dashboard through a governed apply and
   verify the restoration exactly.
2. **Remaining seq-15 write acceptances,** each as its own approved
   governed write.

If a step fails, record the child runtime, the lock records and the audit
events in HAMCP-263. Whether to roll back is Josh's decision.
