# Engineering 2.4.1-beta.56 acceptance

This contract covers HAMCP-271. A stranded post-intent F3 child under an
already-terminal parent is settled through one read-only readback, with
its locks released, in every configuration family and in the operational
`set_input_boolean_state` and `set_input_select_option`. It follows
2.4.1-beta.55 and includes it.

See also the release notes:
[`V2_4_1_BETA56_RELEASE_NOTES.md`](V2_4_1_BETA56_RELEASE_NOTES.md).

**Release target.** `2.4.1-beta.56`, following 2.4.1-beta.55 (#337,
HAMCP-227), which includes 2.4.1-beta.54 (#339, HAMCP-263). #337 took
beta.55 first, so this PR merged `main` and restaged to beta.56.
The release is materialized in all three version authorities with no
staged declaration left. Nothing is published or deployed.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind:
- Josh's HAMCP-271 direction. The fix generalizes the HAMCP-263
  mechanism and does not fork it, and it:
  - performs one read-only readback per stranded child;
  - gives each child a truthful terminal outcome with a reason;
  - releases the child's locks;
  - takes F3 out of `recovering`;
  - leaves the parent byte-unchanged;
  - never redispatches;
  - performs one final read past the deadline where the family supports
    one;
  - reuses the HAMCP-263 durable cleanup marker;
  - keeps exact-owner lock release, freeing terminal owners only;
- the non-goals, unchanged:
  - lease durations;
  - backup, reload and restart families;
  - fan and power;
  - governed rollback (HAMCP-135 inverse plans), which keeps its
    conflict-held manual review;
  - HAMCP-253 acknowledgment;
  - new capabilities;
- `2.4.1-beta.56` materialized in all three version authorities before
  Ready, with exact document resolution;
- the PR head SHA, its `validate` run and the Evidence tier log named in
  the PR.

The review needs a security section covering:
- **No new reachability.** The only new provider call is one read-only
  readback per settled child, through the existing durable-intent
  recovery path with `readback_only=True`. Dispatch stays unreachable on
  that path. No write, service call, reload, restart, route, provider,
  fallback or upstream dispatch is added. The configuration `recover`
  past the deadline now reads once where it previously did not read; it
  never dispatches.
- **Eligibility is closed.** `_terminal_parent_readback_family` requires
  three things to agree with one entry of
  `_terminal_parent_readback_operations`:
  - the durable child's adapter;
  - the closed registry's adapter for the declared capability;
  - the plan operation, when known.

  A registry lookup failure is ineligible. Backup, reload, add-on restart
  and Core restart are never eligible.
- **Binding unchanged.** Readback still requires the exact persisted
  provider operation, argument hash, lock hash and lock tokens.
- **Lock release rule.** This is the HAMCP-263 rule, unchanged:
  - only expired, non-conflict-hold locks are released;
  - the owner child must be terminal with a dispatch intent;
  - the lock's exact execution authority must match;
  - an unresolved manual-review owner keeps its declared selective hold
    keys;
  - active leases and live owners are untouched.
- **No terminal record rewritten.** The terminal parent record is never
  rewritten or deleted. No persisted format changes.

Required offline evidence, `tests/test_hamcp271_stranded_children.py`.
The families covered are configuration script update, operational
`set_input_boolean_state` and operational `set_input_select_option`.
- **Repro.** At the beta.54 source the stranded child keeps its expired
  locks and an unrelated F3 apply is refused with `lock_conflict` and
  zero writes. After the fix, one sweep settles the child, F3 is
  `ready`, repeated sweeps are idempotent and the unrelated apply
  succeeds.
- **Outcomes.** A match is verified. A mismatch is a verification
  failure. An unavailable read is indeterminate manual review, with the
  family's reason and diagnostics.
- **Fault injection.** The process is stopped at three points:
  - before any release;
  - after a partial release;
  - after all releases but before the marker clears.

  After a restart and a sweep, every lock is released and the result
  marker is recorded. There is exactly one read and one dispatch in
  total, and the parent bytes are unchanged.
- **Negative controls.**
  - A live owner under an active parent is never released.
  - A family the registry does not list, or whose adapters disagree, is
    never eligible.
  - The parent is never rewritten.
- **Active-recovery final read** at the deadline, for both a verified
  read and an unavailable one.
- **Adapter-level final read** for automation, script, `input_boolean`
  and `input_number` create and update:
  - readable and matching is verified;
  - drift is a mismatch;
  - unreadable is manual review with `post_dispatch_evidence_deadline_expired`;
  - each case makes exactly one read and one dispatch;
  - a contradictory recovery context never reads.
- **Unchanged suites.**
  - `tests/test_hamcp263_dashboard_readback.py` is unchanged and passes.
  - The existing F3, configuration, operational, recovery and readiness
    suites pass. Two existing tests are adjusted for the one-final-read
    contract, and the PR discloses both.
- **Mutation testing** on the changed code in a copied tree, with
  survivors reported.
- **The complete repository suite** in the Evidence tier.

## 2. Release CI

The exact materialized head's `validate` check passes, including the
beta.55 → beta.56 release transition.

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow.

## 4. Installed Engineering acceptance (separately authorized)

Deployment authorizes neither phase. Phase (a) is read-only. Phase (b)
writes and needs Josh's explicit approval before it starts.

### Phase (a): read-only

The architect checks with read-only tools.

1. **Health.** F3 status is not `recovering`.
2. **No stranded children.** Check each terminal F3 parent. None has a
   nonterminal child holding a dispatch intent. No child runtime shows
   `terminal_parent_child_cleanup_pending`. A child settled by this
   release shows `terminal_parent_child_readback_settled`, and its audit
   shows exactly one readback and no dispatch. Do not infer a verified
   outcome from the terminal state alone.
3. **Locks.** No lock is held by a terminal post-intent child, except the
   declared target keys of an unresolved manual-review child.
4. **Parents unchanged.** Terminal parents keep their recorded outcome
   and reason. HAMCP-253 owns their acknowledgment.
5. **Smoke and regression.** The read-only smoke and regression checks
   pass.

### Phase (b): writes, only with Josh's approval

1. **One governed configuration change.** Plan, approve and apply one
   governed automation or configuration change. It is not refused with
   `lock_conflict`, and it reaches `succeeded_verified` through exact
   readback.
2. **Restore.** If the change is not intended to remain, restore it
   through a governed apply and verify the restoration exactly.

If a step fails, record the child runtime, the lock records and the
audit events in HAMCP-271. Whether to roll back is Josh's decision.
