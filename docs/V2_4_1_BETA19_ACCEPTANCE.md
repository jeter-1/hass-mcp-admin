# Engineering 2.4.1-beta.19 acceptance

This contract covers HAMCP-192: typed fan and power tasks verify a slow
device inline, re-read promptly, decide the deadline with a final read, name a
blocking task, and offer a governed hold release that is audited exactly
once. It builds on 2.4.1-beta.18 (HAMCP-171). Release notes:
[`V2_4_1_BETA19_RELEASE_NOTES.md`](V2_4_1_BETA19_RELEASE_NOTES.md).

**Release target.** This change goes into the next Engineering beta.
- This PR was implemented on `2.4.1-beta.15`. HAMCP-162 (beta.16, PR #267)
  and HAMCP-200 (beta.17, PR #274) merged to `main` first.
- HAMCP-171 then took beta.18 (merged first). This PR merged `main` with
  merge commits (four documentation conflicts in the beta.18 merge, all
  resolved to `main`'s content) and was restaged as `2.4.1-beta.19` by one
  owner-authorized run of `scripts/promote_next_release.py --apply`, which
  consumed the declaration. The beta.16, beta.17 and beta.18 release notes
  and acceptance documents are byte-identical to `main`.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- `2.4.1-beta.19` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

### Offline proof (`tests/test_hamcp192_observing_fix.py`, 41 tests)

The tests use a deterministic fake clock and fake sleep, a synthetic slow
transport and the real gateway, provider, signed Core authority, shared F3
executor, stores, locks and audit.

- `InlineVerificationTests` (2): a device that reports at +3 s or +8 s is
  `succeeded_verified` inline with the lock released; the poll makes at most
  7 reads in about 10 s.
- `LeaseAndSweepTests` (2): a device that reports at +12 s yields
  `observing`, and the first sweep after the 30 s lease reads and verifies
  it; fan and power use the same 30 s lease.
- `SlowDeviceTests` (1): a device that reports at +130 s is verified by a
  sweep, with no hold.
- `DeadlineFinalReadTests` (2): a device that reports just after the
  deadline is verified by the final read; without the opt-in the shared
  executor keeps its pre-read deadline check (F3 unchanged).
- `NeverMatchingTests` (1): a device that never matches gets one more read
  after the deadline, then manual review with
  `post_dispatch_evidence_deadline_expired` and an entity hold.
- `BlockingTaskTests` (2): the `lock_conflict` receipt names the observing
  blocker (task, state, `lease_status`, expected release, deadline) and a
  manual-review hold.
- `ExpiredBlockerTests` (3): an expired, unreconciled lease is still named
  (`expired_pending_recovery`, no expected release; Codex's reproduction);
  an active shared holder does not block and is not named, but an expired
  one is; the lock check and the hints call the one shared predicate.
- `HoldReleaseServiceTests` (8): a release without the Ingress
  principal is refused; a release records a readback, clears only the hold,
  writes the audit entry and never dispatches (the service-call counter is
  unchanged) and the outcome stays `manual_review_required`; a matching and
  a differing readback are each reported truthfully; a failed readback or a
  stale binding changes nothing; an interrupted release is finished by
  recovery without provider access; the review returns the requested and
  freshly read state side by side; a state change after review refuses the
  release and changes nothing; an invalid sidecar fails closed.
- `HoldReleaseExactlyOnceTests` (15), with the real audit logger: a crash
  after the decision, after the hold is cleared, after `released_at`, after
  the audit write and after `audit_recorded`, each followed by a restart
  with a replacement service, leaves exactly one audit entry with the stable
  ID and a settled hold; a failed audit write is retried by recovery; two
  concurrent finishers write one entry; without an audit target the release
  still settles; `audit_recorded` without `released_at` is rejected as
  corrupt. Truthful audit status (Codex M3): Codex's probe through the real
  Ingress pages with auditing disabled gives 200, a result page and receipt
  saying `audit_recorded: false` with `audit_disabled`, and no entry; after
  re-enabling and a restart, recovery backfills exactly one entry and sets
  the flag. While disabled, sweeps and a same-ID replay neither retry,
  rewrite the sidecar nor count failures, and the disabled logger is never
  asked to write. Re-enabling backfills one entry for each of two releases;
  repeated sweeps, a restart and a replay add none. A failed write gives
  `audit_write_failed`, stays settled if auditing is then disabled, and is
  backfilled once re-enabled. An inconsistent or unknown reason is corrupt.
- `HoldReleaseIngressTests` (3): the Ingress release needs the Ingress
  principal (403 with no read otherwise) and a fresh single-use binding (400 when
  stale); the review page shows the requested and freshly read values per
  field with `same`/`DIFFERENT` and a verdict before the form, and the
  result page repeats it for the recorded readback; a failed review read is
  a 503 page with no form.
- `RestartTests` (1): process loss during the inline poll; a replacement
  service verifies after the claim expires, with no second dispatch.
- `PowerInheritanceTests` (1): power verifies a slow light inline with the
  same lease.

The existing typed fan/power, HAMCP-175, HAMCP-181, HAMCP-200 path-safety,
F3 recovery and approval-page suites pass unchanged except for the lease
value and the injected poll sleep.

### Mutation proof

Each mutation was applied to a disposable copy of the source and the suite
above was run against it. Each one must fail:
1. The deadline checked before the final read.
2. The lease left at 120 s.
3. A release without approval (the principal check removed).
4. A release that dispatches.
5. The review page omitting the side-by-side comparison.
6. A non-idempotent audit event ID (a fresh random ID per write).
7. Release completion not serialized.
8. `audit_recorded` set without a confirmed audit write.
9. Blocker hints computed with a separate filter instead of the shared
   predicate.
10. The lock check no longer using the shared predicate.
11. Recovery treating an unaudited release as settled (Codex's delta-review
    mutation).
12. `audit_recorded` set to `true` with auditing disabled, so no entry is
    persisted (Codex M3).
13. The audit logger's enabled flag not checked (Codex M3).

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.19;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.18 artifacts and the
known-good earlier artifacts for recovery.

The only new stored file is the additive hold-release sidecar. Beta.18
reads every other record this change writes and ignores the sidecar
directory. Rollback is reinstalling the beta.18 artifact (see the release
notes for the effect on a released hold).

## 3. Installed Engineering

All installed checks below are read-only unless a step names Josh's explicit
authorization.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.19 tag;
   - the protected `main` merge commit that contains this PR.

   A source version alone does not prove the installed image.
2. **Catalog unchanged.** Compare the public descriptors with beta.18: the
   same tool count, input schemas and descriptions. Also compare:
   - Core signed selection;
   - storage and audit health;
   - F3 readiness;
   - zero fallback.
3. **Health: no retained holds (read-only).** Call `get_server_health`.
   For both `ordinary_power` and `ordinary_fan`, require:
   - `configured: true`;
   - `conflict_hold_count: 0` and `conflict_hold_task_count: 0`;
   - `retained_lock_count: 0`;
   - `nonterminal_tasks: 0` and `active_executions: 0`;
   - `fallback_count: 0`;
   - `recovery_failures` recorded (expected `0`).

   Run it before the canary and again about 40 s after it. A nonzero hold
   count before the canary is not a failure of this release: record the
   task, do not start the canary on that entity, and follow step 5.
4. **Live power canary on the slow-reporting lamp (needs Josh's explicit
   authorization for these two physical actions).** The target is
   `light.back_guest_room_lamp`, which reported 2–6 s late on beta.15.
   1. Read its state with `get_entity`. Choose the action that changes it
      (`turn_on` if `off`, `turn_off` if `on`).
   2. Call `control_power` with that `entity_id`, `action` and a new
      `operation_id`.
   3. **Pass:** the first response itself has `state:
      succeeded_verified` and `terminal: true`, with
      `provider_attempt_count: 1`, `observation_status.observation_attempts:
      1`, a non-null `observation_status.verified_at`, no
      `observation_status.reason`, and an empty `retained_locks`. The task
      must never report `observing`. Record the call's elapsed time
      (expected about the lamp's reporting delay, at most about 10 s plus
      one read).
   4. Call `control_power` with the reverse action and a new
      `operation_id` to restore the original state. The same pass criteria
      apply.
   5. Call `get_execution_task` for both task IDs and confirm
      `state: succeeded_verified`, `provider_attempt_count: 1` and
      `redispatch_prohibited: true`.
   6. Repeat step 3: no retained holds or locks.

   **Fail:** any `observing` response, a second dispatch, or a retained
   lock or hold after the call. Stop and report the receipts; do not retry
   with a new ID.
5. **Exercising the hold release safely, if a hold ever appears.** This
   step runs only when step 3 or a receipt shows a `manual_review_required`
   task with an entity hold. It never dispatches, and it is the only way to
   release such a hold.
   1. Read the task with `get_execution_task`. Its `hold_release.procedure`
      is `home_assistant_admin_ingress_typed_hold_release`. Note
      `terminal_outcome`, `verification` and `retained_locks`.
   2. As a Home Assistant administrator, open the add-on's Ingress panel
      (the page itself does not yet check the administrator role; that is
      HAMCP-205)
      and go to `/typed`. Only held typed tasks are listed; open the task
      (`/typed/power/<task_id>` or `/typed/fan/<task_id>`).
   3. Read the **requested state** and **freshly read state** table. The
      page made one read-only state read to build it. If any row says
      `DIFFERENT`, the device is not in the requested state: check the
      device physically before releasing. Releasing does not change the
      device.
   4. Select release. The decision is refused with no change if the
      device's state changed after the review (409, state changed), if the
      page was stale or the token reused (400), or if the read fails (409).
      Reopen the review page and decide again.
   5. On success, the result page shows the recorded readback compared with
      the request, `Dispatch: none`, and `manual_review_required` as the
      outcome. Confirm with `get_execution_task`: `hold_release.released:
      true`, `outcome` `hold_released_state_matches` or
      `hold_released_state_differs`, `dispatch: "none"`, and an empty
      `retained_locks`, `hold_release.audit_recorded: true` and a
      `hold_release_id`. Confirm in `get_audit_log` exactly one
      `<kind>_hold_release_action` entry for the task, carrying that
      `hold_release_id`. If auditing is disabled on the installed add-on,
      the result page instead says "Audit entry: not recorded
      (audit_disabled …)", the receipt shows `audit_recorded: false` with
      `audit_unrecorded_reason: audit_disabled`, and no entry exists until
      auditing is enabled again, when recovery backfills it.
   6. Repeat step 3: the hold counts are `0`.

   To prove the release path without a real hold, rely on the offline
   proof (§1). Do not create a hold deliberately on a live device.
6. **Deadline final read, blocker naming (including expired leases) and
   interrupted-release recovery with exactly-once audit** are proven offline
   only (§1).

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
