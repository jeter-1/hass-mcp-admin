# HAMCP-192 — Observing-task fix: design note

Scope: Josh's 2026-10-17 decisions (1)–(6) on register row HAMCP-192, applied
to the defects in `docs/HAMCP_192_OBSERVING_INVESTIGATION.md` (PR #270).
Base: `main` at `e4ac184` (2.4.1-beta.15).

`R/` means `hass_mcp_engineering_beta/ha_mcp_engineering/`. Line numbers refer
to `e4ac184`.

## 1. Protected files (final list)

This PR changes exactly these protected files. Any other protected file is out
of scope and would be reported before it is changed.

| File | Decision | Item |
| --- | --- | --- |
| `R/fan/service.py` | 1, 2, 3, 4, 5, 6 | inline poll wrapper; 30 s lease; opt into the final read; receipt hints and blocker naming; governed hold release |
| `R/f3/executor.py` | 3 | opt-in `final_read_at_deadline` flag (default off), used only by typed fan/power |
| `R/f3/locks.py` | 4 (amendment 3) | one shared `holder_blocks` predicate, used by acquisition and by the receipt's blocker naming; behaviour-identical refactor |
| `R/fan/locks.py` | 2 | the shared `FAN_LOCK_TIMING` (30 s) constant, also used by the promotion handle (was 120 s) |
| `R/approval_web.py` | 6 | Ingress review page and POST for a typed hold release, copying the F3 reconciliation CSRF/binding pattern (trusted Ingress and identity-presence checks only; the administrator-role check is deferred to HAMCP-205, amendment 3 H1) |
| `.release/next-version` | release | consumed by the promotion script |
| `hass_mcp_engineering_beta/config.yaml` | release | written by `promote_next_release.py --apply` |
| `R/version.py` | release | written by the promotion script |
| `scripts/validate_addon_metadata.py` | release | written by the promotion script (not protected; listed for completeness) |

Compared with the expected list in the task:

- `R/power/service.py` is **not** changed. `PowerService` subclasses
  `FanService` and inherits every change.
- No capability, tool, registry, routing or plan file is changed, because the
  hold release adds **no MCP tool and no plan type** (§7).
- `R/approval_web.py` is the approval file for the hold release, which the
  task's category "the relevant runtime/recovery and tool/plan files for the
  hold release" covers. It is named here before any change.
- **Amendment 1 (before implementation of decision 6):** `R/fan/audit.py` is
  no longer changed, and `R/fan/locks.py` changes only for decision 2. See
  §7 "Stored-format disclosure" for the reason.
- `R/fan/adapter.py` is **not** changed. The inline poll lives in
  `R/fan/service.py` as an adapter wrapper (§3).

Non-protected files: this note; a new test module
`tests/test_hamcp192_observing_fix.py`; existing typed fan/power tests updated
for the 30 s lease and the injected poll sleep; `docs/TYPED_FAN_CONTROL.md` and
`docs/TYPED_POWER_CONTROL.md`; the new beta release notes and acceptance
document; `CHANGELOG.md` and `README.md`.

## 2. Invariants preserved

- **At most one dispatch.** No new code path reaches `adapter.dispatch` or a
  provider service call. The inline poll and the release readback use only
  the same `core.read` → `provider.state` read the current single observation
  uses.
- **No blind redispatch.** `redispatch_prohibited` is unchanged. Recovery still
  consumes approval through the `fan_recovery_is_read_only` refusal.
- **Same-ID replay is reconciliation only.** `_control` → `reconcile` is
  unchanged.
- **HAMCP-189.** Local-first expiry and durable cancel are unchanged.
  `reconcile`'s ownerless-cancel claim keeps `ExecutorTiming(180, 120, 6, 6)`.
- **HAMCP-164 gate.** `FanCoreAuthority` remains the dispatch authority. The
  read path is the same as today's.
- **HAMCP-181.** The outcome strings and `dispatch_fact_fields` are unchanged.
  `dispatched_at` stays the literal `null`.
- **F3 governed executions** keep the current deadline semantics. The executor
  change is opt-in, and F3 never sets it.

## 3. Decision 1 — bounded inline poll (`R/fan/service.py`)

- `FanService.__init__` wraps `self.adapter` in `_InlineObservationAdapter`.
  The wrapper delegates every attribute to the real adapter except `observe`.
  The executor calls `observe` **only** on the inline path; recovery calls
  `recover`, which delegates to the inner adapter's single read. Sweeps
  therefore stay one read per pass.
- The poll reads at 0, 1, 2, 4, 6 and 8 s, then at 10 s. The sleeps are
  `(1, 1, 2, 2, 2, 2)`, and a sleep is skipped when the elapsed time on the
  injected clock plus that delay would exceed 10 s. That limits the poll to
  at most 7 reads and about 10 s plus one read's latency. It returns as soon
  as a read matches.
- A read exception propagates exactly as it does today. The executor's
  existing exception path then records `observation_failed` and yields.
- It counts as **one** observation attempt (the existing
  `observation_attempts` counter). `ObservationResult.attempt_count` reports
  the number of reads.
- `FanService` gains a `sleep=asyncio.sleep` keyword, so tests can drive a
  fake clock.
- The lease renewer runs during the inline observation
  (`f3/executor.py:663-773`), so a 10 s poll is covered by renewal every 10 s
  on a 30 s lease.

## 4. Decision 2 — 30 s lease (`R/fan/service.py`, `R/fan/locks.py`)

- `FanService.executor()` sets `LockTiming(30, 10, 0)`, changed from
  `(120, 10, 0)`. Renewal stays every 10 s, so a live owner survives two
  missed renewals. `LockTiming.validate` accepts it (lease ≥ 30, renewal <
  lease).
- `FanLockStore.reconcile_terminal_hold` builds its promotion handle with the
  same `LockTiming(30, 10, 0)`. The value is not used for renewal there; this
  only keeps one lease value.
- The **claim** lease stays 120 s (`ExecutorTiming(180, 120, 6, 6)`). A
  shorter claim could expire during a slow live inline execution and fence
  the live owner. The claim is yielded (`claim_expires_at = now`) whenever an
  execution leaves the task observing, so it does not delay sweeps.

## 5. Decision 3 — aligned deadline with one final read (`R/f3/executor.py`, `R/fan/service.py`)

### Executor change

`SharedOperationExecutor(..., final_read_at_deadline=False)` is a new keyword.
`FanService.executor()` passes `True`. With the flag on:

1. **No deadline check before the read.** The check at
   `f3/executor.py:1069-1075` is skipped.
2. The read happens as usual.
3. **Match → verified**, even after the deadline.
4. **Mismatch** (a non-terminal `observing` verification): if the deadline
   has passed, the task goes to manual review with reason
   `post_dispatch_evidence_deadline_expired` (the existing reason string).
   Otherwise it yields as today.
5. A read or verification exception, or an incomplete observation, still
   decides at the deadline through the existing checks (`:1123-1171`,
   `:1227-1256`). The read was attempted first.

With the flag off, behavior is byte-for-byte as today.

### Timeline

Times are measured from the committed dispatch intent, t = 0. The evidence
deadline is t + 180 s, unchanged.

| Step | When | Why |
| --- | --- | --- |
| Inline poll | 0 – ~10 s | decision 1 |
| Inline yield; last renewal | ≤ ~10 s | renewer every 10 s |
| Lease expiry E | ≤ ~40 s | 30 s lease |
| 1st sweep read | E … E + 30 s + pass ≤ ~70 s | the loop sleeps 30 s **after** each pass (`application.py:433-440`), and a read needs an expired lease (`f3/executor.py:990-1030`) |
| Each later sweep read | ≤ previous + 30 s + pass | the recovery owner's transferred lease (30 s) has always expired by the next pass, which starts at least 30 s after the previous pass ended |
| Reads before the deadline | inline + ≤ 70, ≤ 100, ≤ 130, ≤ 160 s | 1 + 4 reads |
| Final read | first pass at or after 180 s | decides verified or manual review |

- The deadline therefore permits the allowed `max_observation_attempts = 6`:
  1 inline, about 4 sweep reads, and 1 final read.
- The sweep cadence makes this an upper bound on the gaps between reads, not
  an exact schedule. A same-ID replay may add a read.
- The investigation's "device reports at +130 s" case is read by the sweep
  between about 130 and 160 s, or at the final read, and is verified.

## 6. Decisions 4 and 5 — blocker naming and receipt hints (`R/fan/service.py`)

Every value is **derived when the receipt is built**, from the existing
execution record and the live lock records. No stored field is added.

### Decision 4: `blocking_tasks` (present when `terminal_outcome == "lock_conflict"`)

- A new command on an entity held by an observing task is still refused with
  `lock_conflict`. There is no superseding and no queueing.
- The receipt now names the current holders of the keys this task requested
  (`entity:<id>` exclusive; shared keys only where an exclusive holder
  exists). It lists at most 4.

| Field | Content |
| --- | --- |
| `task_id` | the holder's task ID |
| `lock_key` | the conflicting key |
| `state` | the holder's execution state when it is a task in this typed namespace (e.g. `observing`); otherwise `conflict_hold` or `lease_held` |
| `conflict_hold` | bool |
| `expected_release_at` | for a lease: the lease expiry, the earliest time the holder can be re-read and released if the device matches. For a hold: `null` |
| `decision_deadline` | for a typed holder: its evidence deadline. At or after it, one final read decides verified (released) or manual review (hold) |
| `release` | `"verified_readback_or_deadline_final_read"` for an observing lease; `"governed_hold_release_required"` for a hold |

- `blocking_tasks_observed_at` labels when this was computed. The list is the
  *current* holders: on a later read the blocker may already be gone, and the
  list is then empty.

### Decision 5: `observation_status` (present once dispatch intent exists)

| Field | Source |
| --- | --- |
| `reason` (non-terminal only) | the latest relevant event: `device_state_not_yet_matching` (`exact_readback_not_verified`), `observation_read_failed` (`observation_failed`), `verification_failed`, `dispatch_response_uncertain` (dispatch result `*_response_uncertain` with no observation yet), `awaiting_first_observation` |
| `next_reobservation_not_before` (non-terminal only) | the later of this task's own entity lease expiry and the claim expiry |
| `reobservation` | `"background_sweep_every_30s"` |
| `evidence_deadline` | `dispatch_intent.evidence_deadline` |
| `final_read_at_deadline` | `true` |
| `observation_attempts` | `record.observation_attempts` |
| `last_observed_at` | the latest `observation_recorded` event |
| `verified_at` | the `verification_recorded` event with `exact_readback_verified`, otherwise `null` |

The receipt has no strict output schema (`run_structured`). These are additive
public receipt fields.

## 7. Decision 6 — governed hold release

### Path chosen: an operation on the existing external-approval path, with no MCP tool and no plan type

The smallest addition is the existing **Ingress reconciliation path** that F3
already uses to release a conflict hold:

- `approval_web.py` `/f3/{child_id}` and `/f3/{child_id}/reconcile`;
- the Ingress principal check (`home_assistant_admin_ingress:<user>`). This is
  a principal label built from the trusted Ingress peer and a well-formed
  user ID; despite its name it does **not** prove the Home Assistant
  administrator role (amendment 3, H1, deferred to HAMCP-205);
- a single-use CSRF token bound to principal, record generation, prepared
  hash and `key:generation` hold binding;
- `F3Runtime.reconcile_child` → `release_hold_after_verified_resolution`.

The typed release copies that pattern for fan and power tasks.

A new plan type was rejected:

- governed `apply` always consumes approval and dispatches;
- it would touch about 15 source files plus tool counts pinned in about 30
  tests;
- it would collide with HAMCP-162's capability/tool-count change.

A new MCP tool was rejected because an approval decided in the client is not
external approval.

### Flow

1. **Review (GET `/typed/{kind}/{task_id}`, kind ∈ {fan, power}).**
   - Ingress plus the authenticated Ingress principal are required, through
     the same identity check as `/f3`. Otherwise 403. That check does not
     verify the Home Assistant administrator role on any approval page; the
     system-wide role check is deferred to HAMCP-205 (amendment 3, H1).
   - The page is shown only for a terminal `manual_review_required` task
     whose hold is settled: exactly the entity hold, owned by the task.
   - It shows the receipt facts, the hold token, and the last recorded
     readback.
   - **Amendment 2:** it also performs one fresh, read-only readback and
     shows the REQUESTED state and the FRESHLY READ state side by side,
     field by field (`state`; plus `percentage` for fan), with a per-field
     `same`/`DIFFERENT` column and an explicit verdict line, above the
     release button. If that read fails, the page is a 503 with no form.
   - It issues a single-use CSRF token bound to: principal, record event
     count, prepared operation hash, `entity:<id>:<generation>`, and
     (amendment 2) the reviewed readback hash.
   - A `/typed` list page shows held typed tasks.
2. **Decide (POST `/typed/{kind}/{task_id}/release`).**
   - The form must be urlencoded and within the size bounds.
   - The token must match: principal, event count, prepared hash and hold
     binding. Otherwise 400 (stale or invalid). This POST is the external
     approval.
3. **`FanService.release_hold(task_id, *, record_generation, prepared_hash, hold_binding, reviewed_readback_hash, authorized_principal)`:**
   1. Refuse unless the principal starts with
      `home_assistant_admin_ingress:`. A direct call without the Ingress
      principal is refused. The prefix records the Ingress identity; it is
      not an administrator-role check (deferred to HAMCP-205).
   2. Refuse unless the task is not active, is terminal
      `manual_review_required`, the prepared hash matches, the hold is
      settled and matches the binding, and no release has been recorded.
   3. **Fresh readback:** one read through the inner adapter's own
      observation read (`core.read` → `provider.state`). If the read fails,
      the release is refused and nothing changes. **Amendment 2:** if its
      hash differs from the reviewed readback hash, the device moved after
      review; the release is refused (`<kind>_hold_release_state_changed`)
      and nothing changes. The reviewer reviews again.
   4. Under the declaration transaction, re-check the reviewed facts, then
      atomically write the release sidecar
      `<namespace>/hold-releases/<task_id>.json` with `released_at: null`:
      prepared hash, record event count, hold key and generation, authority
      hash (sha256 of the principal), readback hash, match flag and
      `authorized_at`. A second decision finds the sidecar and is refused.
   5. `release_conflict_hold(owner=<rebuilt from the record identity>,
      tokens=(entity token,), reason_code="owner_authorized_release")`. This
      is the same fenced primitive F3 uses. It requires the exact key,
      generation, owner, and a conflict hold.
   6. Rewrite the sidecar with `released_at`.
   7. Write one audit entry `<kind>_hold_release_action`: authority hash,
      readback hash, match flag, hold key/generation, outcome,
      `dispatch: "none"`.
4. **Truthful outcome.**
   - The task's `terminal_outcome` **stays `manual_review_required`**. It is
     never relabeled verified.
   - The result and receipt say `hold_released_state_matches` or
     `hold_released_state_differs`, with the readback time and match flag.
5. **No dispatch.** No call reaches `adapter.dispatch`, `provider.dispatch`
   or a service call. A test asserts that the transport's service-call
   counter is unchanged.
6. **Never automatic.**
   - Neither recovery nor `recover_once` ever *starts* a release.
   - **Interrupted release:** the sidecar is durable with
     `released_at: null`, but the process died before it was completed.
     `reconcile` then finishes it without the executor, adapter or provider:
     it releases only the exact hold still owned by the task, then records
     `released_at`. This mirrors F3
     `_finish_pending_hold_releases`, and completes an already approved
     release; it does not create one.

### Design choice for Josh

- Release is allowed whether the fresh readback matches or not. The mismatch
  is recorded truthfully.
- The F3 precedent instead refuses unless the readback verifies. For a fan or
  power device that never reports the requested state, that would make the
  hold permanent again.
- If Josh prefers verified-only, it is a one-line refusal.

### Stored-format disclosure

- **Amendment 1.** The original plan (two new event types in the execution
  record) is not possible without a stored-format change: `f3/models.py`
  validates post-intent records against a closed event-type set, so new
  event types would need a change to that shared protected file, and older
  code would refuse to read a released record after a rollback.
- **Chosen instead: one additive sidecar file per released task**,
  `<namespace>/hold-releases/<task_id>.json` (model
  `typed-hold-release-v1`, closed field set, ≤ 4 KiB, strictly validated;
  an invalid sidecar fails closed: no release, the hold stays, and recovery
  counts a failure).
- **No existing stored format changes**: declaration, execution record, lock
  record and audit formats are unchanged. No field is added to any of them.
- **Rollback note:** pre-HAMCP-192 code ignores the sidecar directory. For a
  released task it finds no owned hold, so its recovery counts one
  `recovery_failures` per sweep for that task. It never dispatches.

### Amendment 2 — requested and fresh state side by side (Josh, 2026-10-18)

- **Requirement (Josh):** the hold-release review page shows the REQUESTED
  state and the FRESHLY READ state side by side, so any difference is
  unmistakable before approval. Josh also confirmed that release is allowed
  when the fresh read differs (`hold_released_state_differs`, the task stays
  `manual_review_required`).
- **Implementation:** `FanService.hold_release_review` returns the review
  facts plus one fresh readback through the same read path as observation,
  as `(field, requested, freshly read)` rows. The requested values come from
  the prepared request with the adapter's own `desired` semantics (`off`
  for `turn_off` or percentage 0; percentage only for fan; `any` when the
  request did not fix it). The result page repeats the comparison for the
  recorded readback.
- **Binding:** the CSRF binding carries the reviewed readback hash, and
  `release_hold` refuses if its own fresh readback differs. The decision
  therefore covers exactly the state that was shown. This adds a refusal
  only; it adds no write, dispatch or stored field.
- **Read reachability:** the review GET now performs one provider read
  (`ha_get_state`) after `_require_admin`. Despite its name, `_require_admin`
  checks only the trusted Ingress peer and path and a well-formed user ID. A
  request without Ingress or without a user ID gets 403 and causes no read
  (tested). An authenticated non-administrator Ingress user is **not**
  refused and does cause the read (Codex H1, deferred by Josh to HAMCP-205,
  amendment 3).
- **Files:** `R/fan/service.py`, `R/approval_web.py` (both already on the
  list). No new file.

### Amendment 3 — exactly-once release audit and one blocks predicate (Codex M1, M2; Josh, 2026-10-18)

**M1 — the release is recorded and audited exactly once.**

- The sidecar gains one closed field, `audit_recorded` (bool). The
  sidecar format never shipped (beta.18 went to HAMCP-171), so this is not a
  stored-format change for any released build.
- **Stable release ID:** `hold_release_id` is a digest over the authorized
  decision fields only (task, prepared hash, record generation, hold key and
  generation, authority hash, readback hash and match, `authorized_at`), not
  over `released_at` or `audit_recorded`. The audit entry's
  `audit_event_id` is a digest of that release ID under the model
  `typed-hold-release-audit-v1`, so every retry writes the same ID.
- **Idempotent write:** the production `AuditLogger` already deduplicates by
  `audit_event_id` under its file lock, across the log and its rotation. A
  repeated write of the same entry is therefore a no-op, not a second entry.
- **One completion path:** `release_hold` and the recovery sweep's
  `reconcile` both call `_finish_hold_release`, which runs under the
  service's declarations lock and is resumable from each step:
  1. the decision is recorded (sidecar, `released_at = null`,
     `audit_recorded = false`);
  2. the hold is cleared (with the original fencing) and `released_at` is
     written;
  3. the audit entry is written; only a confirmed write of exactly one entry
     sets `audit_recorded = true`. A failed or partial write counts
     `audit_projection_failures` and leaves the step for recovery to retry.
- `audit_recorded = true` with `released_at = null` is rejected as corrupt
  (fail closed).
- A release counts as settled only once it is released, owns nothing, and is
  audited (or auditing is disabled; amendment 4).
- The receipt and status add `hold_release_id` and `audit_recorded`.

**M2 — one "currently blocks" predicate.**

- `DurableLockStore.holder_blocks(holder, request_mode, now)` in
  `R/f3/locks.py` is the one acquisition predicate. `acquire_once` uses it,
  and so does the receipt's `blocking_tasks`, so a hint can never disagree
  with the refusal it explains. `transfer_complete` keeps its own
  equivalent inline check; this PR does not change it.
- **Expired-lease semantics (unchanged, now documented):** a holder blocks
  when it is a conflict hold, when either side is exclusive, or when its
  lease has expired. Expiry alone never frees a key, even between two shared
  holders: only task-aware stale recovery releases, transfers or holds it.
- The receipt now shows expired, unreconciled holders that it previously
  omitted (Codex's reproduction). Each entry has a `lease_status` of
  `active`, `expired_pending_recovery` or `conflict_hold`. For an expired
  lease, `expected_release_at` is `null`, `lease_expired_at` gives the
  expiry, and `release` is `"recovery_sweep_reconciles_expired_lease"`.
  Requested shared keys are listed only when the predicate says they block.

### Amendment 4 — truthful `audit_recorded` and backfill (Codex M3, 2026-10-18)

The production `AuditLogger` returns the batch length **without writing**
when it is disabled (the supported `audit_enabled: false` option). Amendment
3 treated that return as a confirmed write, so a release with auditing off
reported and persisted `audit_recorded = true` without any entry, and later
enabling auditing never projected it. The shared logger's return convention
is unchanged (other callers rely on it); the hold release no longer consults
it while the logger is disabled.

- The sidecar gains one more closed completion field,
  `audit_unrecorded_reason`: `null`, `audit_disabled` or
  `audit_write_failed`. Like `audit_recorded`, it is excluded from the
  release ID, so the audit event ID is unchanged. The sidecar format has not
  shipped, so this is not a stored-format change for any released build.
- `audit_recorded = true` only after an **enabled** logger confirms exactly
  one entry (written, or a retained entry with the same event ID). It then
  clears the reason.
- Auditing disabled (or no audit logger): the release completes,
  `audit_recorded` stays `false`, the reason is `audit_disabled`, and the
  release counts as settled, so there is no retry loop or failure count
  while auditing is off.
- An enabled logger that does not confirm the write: `audit_recorded`
  stays `false`, the reason is `audit_write_failed`,
  `audit_projection_failures` counts it, and recovery retries it.
- The reason is the last attempt's result. A write-failed release that is
  settled because auditing was then disabled keeps `audit_write_failed`;
  neither reason ever claims an entry.
- **Backfill:** once an enabled logger is configured (after the add-on
  option is turned on and the add-on restarts, or in place), every release
  with `audit_recorded = false` is no longer settled. The recovery sweep
  finishes it through the same `_finish_hold_release` routine and writes the
  missing entry with the event ID derived from the release ID and the
  original `released_at` timestamp, then sets `audit_recorded = true`. The
  sweep handles at most 8 candidates per 30 s pass, so a large backlog takes
  several passes.
- The sidecar rejects a reason together with `audit_recorded = true`, an
  unknown reason, and a reason without `released_at`.
- The receipt's `hold_release`, the `release_hold` result and the Ingress
  result page show `audit_recorded` and `audit_unrecorded_reason`. The page
  says "Audit entry: recorded" only when the flag is true; otherwise it says
  "not recorded" with the reason.
- **Retention limit (Codex delta residual, Low):** "exactly once" means one
  entry within the audit logger's retained log (the live file and its one
  `.1` rotation, 5 MiB each). If the process dies after the entry is
  written but before `audit_recorded` is persisted, and enough other audit
  traffic then rotates that entry out of both files before recovery runs,
  recovery writes the same event ID and timestamp again. No hold is released
  twice and nothing is dispatched; external archives should deduplicate by
  `audit_event_id`. The nominal 30 s sweep does not bound recovery during
  downtime, repeated failures or a backlog.

**H1 (approval-page administrator role) — deferred to HAMCP-205: the /typed
pages share the existing pages' identity check; the system-wide
administrator check lands in HAMCP-205.** No approval page checks the Home
Assistant administrator role today, so the gap is not specific to the typed
pages and is fixed once, for every approval page, in HAMCP-205. Practical
exposure is near zero, because the one non-administrator account cannot see
the panel. This PR adds no administrator check and does not change the plan
or F3 pages.

## 8. Tests and mutations

New tests are in `tests/test_hamcp192_observing_fix.py`. They use a
deterministic fake clock and a fake sleep, and offline fixtures.

| Case | Expectation |
| --- | --- |
| device reports at +3 s / +8 s | inline `succeeded_verified`, lock released, reads ≤ 7 |
| device reports at +12 s | inline `observing`; the first sweep after the 30 s lease reads and verifies |
| device reports at +130 s | verified by a sweep; no hold |
| device reports just after the deadline | verified by the final read (would be manual review without it) |
| device never matches | `observing` until the deadline; the first sweep after it does one more read, then manual review with `post_dispatch_evidence_deadline_expired` and an entity hold |
| lock conflict | the second task's receipt names the blocker: task ID, state `observing`, expected release time and deadline |
| hold release | Ingress approval with a user identity required (403 without Ingress or a user ID, 400 stale CSRF, direct call refused without the Ingress principal; the administrator role is not checked, HAMCP-205); readback recorded; hold cleared; audit entries; no dispatch; terminal outcome unchanged; mismatching readback recorded truthfully; interrupted release finished by `reconcile` |
| restart mid-observation | process loss during the inline poll; a replacement service verifies after the claim expires, with no second dispatch |
| F3 default | executor without the flag keeps the pre-read deadline check |
| side-by-side review (amendment 2) | the review page and the release result show requested vs freshly read values per field with `same`/`DIFFERENT` and a verdict, before the form; a state change after review refuses the release (409) and changes nothing; a failed review read gives 503 with no form; no read without Ingress and a user ID |
| crash points (amendment 3, M1) | a crash after the decision, after the hold is cleared, after `released_at`, after the audit write and after `audit_recorded`, then a restart with a replacement service: exactly one audit entry with the stable ID, and a consistent, settled hold state |
| audit failure (amendment 3, M1) | a failed audit write leaves `audit_recorded = false`; recovery retries and settles with one entry |
| concurrent finishers (amendment 3, M1) | two threads finishing the same release write one audit entry |
| audit disabled (amendment 4, M3) | Codex's probe through the real Ingress pages: 200, the page and receipt say `audit_recorded = false` with `audit_disabled`, no entry exists; re-enabled with a restart, recovery backfills exactly one entry and sets the flag; while disabled, sweeps and a same-ID replay neither retry, rewrite nor count failures; a disabled logger's `write_batch` is never consulted |
| backfill (amendment 4, M3) | two releases with auditing off; enabling it lets the next sweep write one entry for each (event ID from the release ID, timestamp = `released_at`); repeated sweeps, a restart and a same-ID replay add none |
| write failure (amendment 4, M3) | a failed write gives `false` with `audit_write_failed`; disabled before the retry it stays settled with no new failures; re-enabled, recovery backfills one entry |
| sidecar consistency (amendment 4, M3) | a reason with `audit_recorded = true`, an unknown reason, or a reason without `released_at` is rejected as corrupt |
| expired blocker (amendment 3, M2) | an expired, unreconciled lease is named (`expired_pending_recovery`); an active shared holder is not named; both call sites use the one predicate |

Each mutation must make at least one test fail:

- deadline checked before the final read;
- lease left at 120 s;
- release without approval (principal check removed);
- release dispatches;
- (amendment 2) the review page omits the side-by-side comparison.
- (amendment 3, M1) a non-idempotent audit ID (a fresh random ID per write);
- (amendment 3, M1) release completion not serialized;
- (amendment 3, M1) `audit_recorded` set without a confirmed write;
- (amendment 3, M2) hints computed with a separate filter;
- (amendment 3, M2) the lock check stops using the shared predicate;
- (Codex delta review) recovery treats an unaudited release as settled;
- (amendment 4, M3) `audit_recorded = true` set with auditing disabled, so no
  entry is persisted;
- (amendment 4, M3) the logger's enabled flag is not checked.

## 9. HAMCP-162 overlap

- HAMCP-162 (PR #267) does not change `R/f3/executor.py`, `R/fan/` or
  `R/power/`.
- This PR changes no capability or tool file, so the tool counts are
  unaffected.
- The overlap is the release files (`CHANGELOG.md`, `README.md`,
  `config.yaml`, `version.py`, `validate_addon_metadata.py`,
  `.release/next-version`).
- Per the task, `origin/main` is merged (a merge commit, no rebase) after
  HAMCP-162 lands and before the release is materialized. This PR then takes
  the next beta.

### Merge record (2026-10-18)

- `origin/main` at `020bfd8` (HAMCP-162 beta.16 #267, HAMCP-171 report #276,
  HAMCP-200 beta.17 #274) was merged with a merge commit. **There were no textual
  conflicts.** Main changed none of this PR's files (`R/f3/executor.py`,
  `R/fan/`, `R/power/`, `R/approval_web.py` and the typed test modules are
  untouched on main since the base `e4ac184`).
- **HAMCP-200 typed `Route`:** this PR adds or touches **no** Engineering
  REST or WebSocket call. Every state read in the inline poll, the sweep,
  the deadline final read, the hold-release review read and the release
  readback goes through the reviewed delegated provider tool `ha_get_state`
  (`providers/upstream_fan.py`/`upstream_power.py` → `core.read`), which
  builds no Home Assistant URL. There is therefore nothing to convert to
  `rest_path`. The `Route(...)` entries in `R/approval_web.py` are Starlette
  Ingress routes for this add-on's own pages, not Home Assistant routes.
- HAMCP-171's runtime PR (#277, beta.18 planned) was still an open draft at
  merge time; its recovery-sweep change is not in this branch.

### Merge record 2 (2026-10-18)

- `origin/main` at `51d63fc` (HAMCP-171 beta.18) was merged with a merge
  commit (`1d22d87`). Four documentation conflicts, all resolved to main's
  content: `README.md`, `hass_mcp_engineering_beta/CHANGELOG.md`, and the
  add/add conflicts `docs/V2_4_1_BETA18_RELEASE_NOTES.md` and
  `docs/V2_4_1_BETA18_ACCEPTANCE.md`. beta.18 belongs to HAMCP-171, so this
  PR's release text moved to beta.19.
- `origin/main` at `8a55103` (HAMCP-202 #275: workflows, scripts and tests
  only, no version change) was merged with a merge commit (`ae7caf7`). No
  conflicts; no file in common with this PR.
- This PR is then restaged as `2.4.1-beta.19` with
  `promote_next_release.py --apply`. The beta.18 files are byte-identical to
  `main`.
