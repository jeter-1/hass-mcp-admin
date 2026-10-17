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
| `R/fan/locks.py` | 2 | the shared `FAN_LOCK_TIMING` (30 s) constant, also used by the promotion handle (was 120 s) |
| `R/approval_web.py` | 6 | Ingress admin review page and POST for a typed hold release, copying the F3 reconciliation CSRF/binding pattern |
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

The smallest addition is the existing **Ingress administrator reconciliation
path** that F3 already uses to release a conflict hold:

- `approval_web.py` `/f3/{child_id}` and `/f3/{child_id}/reconcile`;
- the admin principal check (`home_assistant_admin_ingress:<user>`);
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
   - Ingress plus an authenticated HA administrator are required. Otherwise
     403, as for `/f3`.
   - The page is shown only for a terminal `manual_review_required` task
     whose hold is settled: exactly the entity hold, owned by the task.
   - It shows the receipt facts, the hold token, and the last recorded
     readback.
   - It issues a single-use CSRF token bound to: principal, record event
     count, prepared operation hash, and `entity:<id>:<generation>`.
   - A `/typed` list page shows held typed tasks.
2. **Decide (POST `/typed/{kind}/{task_id}/release`).**
   - The form must be urlencoded and within the size bounds.
   - The token must match: principal, event count, prepared hash and hold
     binding. Otherwise 400 (stale or invalid). This POST is the external
     approval.
3. **`FanService.release_hold(task_id, *, record_generation, prepared_hash, hold_binding, authorized_principal)`:**
   1. Refuse unless the principal starts with
      `home_assistant_admin_ingress:`. A direct call without the Ingress
      principal is refused.
   2. Refuse unless the task is not active, is terminal
      `manual_review_required`, the prepared hash matches, the hold is
      settled and matches the binding, and no release has been recorded.
   3. **Fresh readback:** one read through the inner adapter's `observe`
      (`core.read` → `provider.state`). If the read fails, the release is
      refused and nothing changes.
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
| hold release | Ingress admin approval required (403, 400 stale CSRF, direct call refused without the admin principal); readback recorded; hold cleared; audit entries; no dispatch; terminal outcome unchanged; mismatching readback recorded truthfully; interrupted release finished by `reconcile` |
| restart mid-observation | process loss during the inline poll; a replacement service verifies after the claim expires, with no second dispatch |
| F3 default | executor without the flag keeps the pre-read deadline check |

Each mutation must make at least one test fail:

- deadline checked before the final read;
- lease left at 120 s;
- release without approval (principal check removed);
- release dispatches.

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
