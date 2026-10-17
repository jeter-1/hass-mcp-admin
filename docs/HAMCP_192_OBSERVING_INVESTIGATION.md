# HAMCP-192 — Typed power/fan tasks left "observing" after a slow device report

Status: read-only investigation report. No runtime, test, release, workflow,
registry, or stable-v1 change accompanies this document.

- Base: `origin/main` `ba9db1f3c5e3ad400036bb7a3e4b10a0c4bbab61`
  (`v2.4.1-beta.15`, the deployed Engineering build).
- Compared: `v2.4.1-beta.14` (`bdb5af8`), `v2.4.1-beta.13` (`06d5555`), and
  `v2.3.0` (the first tag that contains typed power).
- Evidence: current source with file:line references, and an offline timeline
  probe run against each tag's own code and synthetic test fixtures (method in
  [Appendix A](#appendix-a--probe-method)). No live Home Assistant or deployed
  MCP endpoint was contacted.

All paths below are relative to
`hass_mcp_engineering_beta/ha_mcp_engineering/` unless stated otherwise. Line
numbers are on `ba9db1f`.

## Summary

- The behaviour observed live on beta.15 is **pre-existing, not a
  regression**. The probe timelines are identical on v2.3.0, beta.13, beta.14
  and beta.15. The timing constants have not changed since fan (2026-09-16) and
  power (2026-09-18) were introduced.
- **The inline verification window is effectively zero.** The service call is
  sent with `wait: False`, then exactly one `ha_get_state` is made. Any device
  that reports more than about 1 s after the provider acknowledgement leaves
  the task `observing`.
- **There is background re-observation**, but in practice it cannot act for
  120 s. The 30 s recovery sweep and every same-ID replay or
  `get_execution_task` are both gated by the **120 s entity-lock lease**. That
  lease stays held under the original, now-finished owner. There is no
  dedicated "replay cooldown": the gate is the lock lease, through an owner
  check in `f3/executor.py:990-994`. This explains why replays at +20 s and
  +95 s did nothing and why resolution arrived at about 2.5 min.
- **While observing, the task holds `entity:<id>` EXCLUSIVE.** It also holds
  `home_assistant:core` SHARED and `addon:ha_mcp` SHARED. A new command on the
  same entity, under a fresh ID, is refused immediately as
  `failed_pre_dispatch` / `lock_conflict`. It is not dispatched and not
  queued. The lock file is shared with the F3 governed runtime, so the two
  SHARED locks can also block F3 plans that need those keys EXCLUSIVE
  (HA restart, ha_mcp add-on restart).
- **The evidence deadline (180 s) and the renewed lease (240 s) are
  misaligned.** If the re-read at +120 s still shows a mismatch, the lease is
  renewed to +240 s, and no re-read can happen between 180 s and 240 s. At
  +240 s the deadline check runs before any read, so the task becomes
  `manual_review_required` **even when the device has been correct since, for
  example, +130 s**. Of the 6 observation attempts allowed, at most 2 can ever
  be used.
- **A manual-review conflict hold on a fan or power entity never releases.**
  No tool, sweep, or operator path clears it. It blocks every later typed
  command on that entity, and every F3 governed acquisition on the same key,
  indefinitely.
- **The state is reported truthfully but without explanation.** "observing" is
  never presented as success. The receipt gives no reason for the wait, no time
  for the next possible re-read, and no observation or verification times. A
  `lock_conflict` refusal does not name the blocking task.

The recommendation (section [Q7](#q7--options-and-recommendation)) is a
combination: a short bounded inline poll, prompt sweep takeover on yield, and
aligned deadline and lease. Add truthful receipt hints if wanted. Keep
superseding commands refused. Six owner decisions are listed in
[Owner decisions](#owner-decisions).

## Context: the live observation

On beta.15, against `light.back_guest_room_lamp`:

- **turn_on task `3e0d8b81…`** returned `observing` after about 9 s. The lamp
  changed about 2 s after the provider response. The task held the entity
  EXCLUSIVE lock for about 2.5 min, until a same-ID replay reconciled it to
  `succeeded_verified` with no redispatch.
- **turn_off task `a2e476a1…`** also returned `observing`; the lamp changed
  about 6 s after the response. Replays at about +20 s and +95 s did not
  re-read.

The offline probe reproduces both timelines exactly (see Q2 and Q4).

## Execution path (shared by fan and power)

1. **Entry.** `PowerService` subclasses `FanService` (`power/service.py`).
   `FanService._control` (`fan/service.py:209-240`) prepares the request. A
   task that already has a durable declaration only reconciles. Otherwise
   execution is shielded and handed to the shared F3 executor that
   `FanService.executor()` builds (`fan/service.py:147-152`):
   - `LockTiming(120, 10, 0)`: a 120 s lock lease, 10 s renewal interval, and
     a wait timeout of 0, so lock conflicts are refused immediately.
   - `ExecutorTiming(180, 120, 6, 6)`: a 180 s post-dispatch evidence
     deadline, a 120 s claim lease, at most 6 observation attempts and at most
     6 verification attempts.
2. **Locks.** The adapter requests `entity:<id>` EXCLUSIVE,
   `home_assistant:core` SHARED, and `addon:ha_mcp` SHARED
   (`fan/adapter.py:84-92`).
3. **Dispatch.** A durable dispatch intent is committed with
   `evidence_deadline = committed_at + 180 s` (`f3/persistence.py:570-584`).
   Then exactly one `ha_call_service` is sent with `"wait": False`
   (`power/contracts.py:49-52`; the reason for `wait=False` is given in
   `providers/upstream_fan.py:1-6`). Each provider exchange runs under
   `asyncio.timeout(20)` (`providers/upstream_fan.py:121-165`).
4. **Inline observation.** `observe` makes a single state read and compares it
   with the desired state (`fan/adapter.py:134-142`; for power,
   `power/contracts.py:70-71`). If they differ, `verify` returns `OBSERVING`
   (`fan/adapter.py:144-150`).
5. **Non-terminal yield.** The executor records `observing`. It **renews the
   lock lease** to now + 120 s (`_renewal_failure_result`,
   `f3/executor.py:1384-1399`). It then **yields the claim**: `yield_claim`
   sets `claim_expires_at = now` and appends `execution_claim_yielded` with
   diagnostic `observation_resume_allowed` (`f3/persistence.py:728-757`).
   The locks remain under the original `owner_id`.

## Q1 — Is there background re-observation? Schedule, deadline, and deadline outcome

**Yes. It is bounded, but gated by the lock lease.**

### Schedule

- `_supervise_f3_recovery` runs one pass at startup, then one every 30 s
  (`application.py:433-440`).
- Each pass calls `f3_runtime.recover_once`, then
  `FAN_OPERATIONS.service.recover_once()`, then
  `POWER_OPERATIONS.service.recover_once()` (`application.py:395-410`).
- Fan and power recovery are skipped when the F3 runtime is not initialized.
  They are also skipped for that pass if `f3_runtime.recover_once` raises:
  the exception is caught and logged once for the whole pass
  (`application.py:416-430`).
- `FanService.recover_once` (`fan/service.py:338-359`):
  - collects non-terminal records, plus manual-review records whose hold is
    not yet settled;
  - takes them in task-ID order from a glob capped at 4096 files, filtered
    before the cap is applied;
  - reconciles at most 8 per pass, inside `asyncio.timeout(45)`.

### What a sweep actually does

`reconcile` (`fan/service.py:303-336`) returns the receipt unchanged unless the
claim has expired (`:325`). After a yield it always has expired. It then calls
`executor.execute` with a fresh `owner_id` (`uuid4`, `:330-331`) and an
approval consumer that always refuses (`:332-335`). This keeps recovery
read-only: no dispatch authority is reachable.

Inside the executor, `_recover_claimed` → `_recovery_handle`
(`f3/executor.py:937-1030`) checks the durable lock tokens:

| Lock state | What `_recovery_handle` does | Result |
|---|---|---|
| Lease still valid, owner differs (always true for a new reconcile) | Returns `None` (`:990-994`) | The claim is yielded again. **No re-read.** |
| Lease expired | Transfers the locks to the new owner via `recover_expired(... TRANSFER_FOR_OBSERVATION ...)` (`:995-1030`) | `_observe_and_verify` runs: **one re-read** |
| Any token is a conflict hold | Returns `None` (`:964-965`) | No re-read |

### Deadline and deadline outcome

- `_observe_and_verify` checks the deadline **before** observing
  (`f3/executor.py:1069-1075`). Once `now >= evidence_deadline`
  (`_deadline_expired`, `:1315-1322`), the task becomes
  `manual_review_required` with reason `post_dispatch_evidence_deadline_expired`.
- `_manual_review` (`:1324-1351`) calls `promote_to_conflict_hold`.
  `FanLockStore` maps that call to `promote_selective_conflict_hold`, which keeps
  only `entity:<id>` EXCLUSIVE as a permanent conflict hold
  (`fan/locks.py:11-18`; `f3/locks.py:795-806`, "Conflict holds never
  expire").
- The deadline is also checked when observation or verification raises
  (`:1124-1134`, `:1146-1156`, `:1231-1240`).

### Effective timeline (probe, t = 0 at the inline read)

- **Device changes at t < 120 s.** It is verified at the first sweep or
  replay at or after **+120 s**.
- **Device still wrong at +120 s.** The re-read records a mismatch and the
  lease is renewed to +240 s. Sweeps between +150 s and +210 s cannot read.
  At **+240 s** the deadline has passed (180 s), so the task becomes
  `manual_review_required` **without a read**. The entity hold then persists
  indefinitely (still present at +3600 s in the probe).
- In practice **only 2 of the 6 allowed observation attempts are reachable**:
  the inline read at t = 0 and one at +120 s.

### Can the hold be released?

**No path releases a fan or power conflict hold.**

- `FanLockStore.reconcile_terminal_hold` (`fan/locks.py:20-48`, "Never release
  a target") only completes an interrupted promotion. `hold_settled`
  (`fan/locks.py:50-60`) is a read-only check.
- The three `release_conflict_hold` callers in `f3_runtime/runtime.py`
  (`:1560`, `:4507`, `:5390`) are all scoped to declared F3 child executions
  (`self.children`). Fan and power never write there; their lock owners have
  `plan_id=None` (`fan/locks.py:36`, `fan/service.py:251`).
- The F3 stale sweep refuses conflict-hold records (`f3/locks.py:956-957`).
- `cancel_execution_task` routes only to governance
  (`tools/governance.py:398-417`).

The only remaining path is out-of-band manual editing of the lock state file,
which no documented procedure covers. Health shows the hold:
`FanService.health` `conflict_hold_count` / `conflict_hold_task_count`
(`fan/service.py:368-380`), surfaced through `get_server_health` as
`ordinary_power` / `ordinary_fan` (`health.py:94-95`).

## Q2 — Replay "cooldown": what and where

**There is no dedicated cooldown constant.** The observed cooldown is the
**entity-lock lease**:

- Set to 120 s in `LockTiming(120, 10, 0)` (`fan/service.py:150`).
- Renewed to now + 120 s at every non-terminal yield
  (`f3/executor.py:1384-1399`).
- Enforced through the owner check in `_recovery_handle`
  (`f3/executor.py:990-994`).

Every same-ID replay (`_control` → `reconcile`) and every `get_execution_task`
on a fan or power task (`tools/governance.py:336-345` → `reconcile`) gets a
new `owner_id`. While the original lease is valid it therefore cannot take
over, and it returns the stored receipt without reading.

**Why +20 s and +95 s did nothing and about +2.5 min worked.** Both early
replays fell inside the 120 s lease that followed the inline read. The
successful replay came after the lease had expired, so `recover_expired`
transferred the locks and one re-read verified the state. The ~150 s
end-to-end delay is the 120 s lease plus sweep or replay phase plus the
inline-call duration.

**Probe evidence, power with the device reporting at +2 s.** Sweep-only runs
with the device at +2 s and at +6 s, and the fan analogue, give the same
pattern.

| t (s) | Event | Re-reads | State | Writes |
|---|---|---|---|---|
| 0 | `control_power` returns | — | observing (claim 0, lease 120, deadline 180) | 1 |
| 20 | same-ID replay | 0 | observing | 1 |
| 95 | same-ID replay | 0 | observing | 1 |
| 119 | same-ID replay | 0 | observing | 1 |
| 120 | same-ID replay | 1 | **succeeded_verified**, all locks released | 1 |

Note a design tension. The yield event says `observation_resume_allowed`
(`f3/persistence.py:747`), but no other owner can resume until the lease
expires. Yielding the claim releases the single-flight claim but not the
lock-lease ownership that recovery needs.

## Q3 — Inline verification window

**It is effectively about 0 s.** There is no polling, waiting, or retry inside
the call:

- `ha_call_service` is sent with `"wait": False` (`power/contracts.py:49-52`).
  This deliberately avoids ha-mcp's waited-service path and its component
  fallback (`providers/upstream_fan.py:1-6`).
- Exactly one `ha_get_state` follows the acknowledgement
  (`fan/adapter.py:134-142`). The window is one provider round trip, roughly
  1 s live.
- One successful control call makes 7 provider exchanges, each a full MCP
  session (probe, all tags):
  `ha_list_services, ha_get_state, ha_list_services, ha_get_state, ha_get_state, ha_call_service, ha_get_state`.
  This accounts for the ~9 s total call time live, but only the last exchange
  comes after dispatch.

**Compared with 2-10 s device latency.** Any cloud, Zigbee-group, Wi-Fi, or
polled integration whose state update lands more than one round trip after the
service acknowledgement will routinely miss the window. Both live tasks (2 s
and 6 s) fall in that range. There is no code constant to tune today; changing
this means adding a bounded poll.

## Q4 — Lock held while observing; what a new command gets

**Held:** `entity:<id>` EXCLUSIVE, `home_assistant:core` SHARED, and
`addon:ha_mcp` SHARED (`fan/adapter.py:84-92`).

- Lease: 120 s from the inline read, renewed by +120 s at each non-terminal
  re-read.
- When verified: all three are released.
- When it becomes manual review: only the entity key is kept, as a permanent
  conflict hold.

**A new command on the same entity under a fresh ID** is refused immediately,
because `wait_timeout` is 0:

- It ends `state=failed_pre_dispatch`, `terminal_outcome=lock_conflict`, with
  0 provider attempts and no write. It makes one prepare read.
- It is not queued. Replaying the refused ID later returns the same terminal
  refusal.
- The user needs another fresh ID after the first task resolves.
- The refusal receipt lists only its own `retained_locks`
  (`fan/service.py:273, 293-295`), so it does not identify the blocking task.

Reproduction (probe, power; device reports at +2 s):

| t (s) | Event | State / outcome | Attempts | Total writes |
|---|---|---|---|---|
| 0 | turn_on returns | observing | 1 | 1 |
| 30 | **new turn_off, fresh ID** | failed_pre_dispatch / **lock_conflict** | 0 | 1 |
| 125 | replay of the refused turn_off ID | still lock_conflict | 0 | 1 |
| 125 | sweep | original → succeeded_verified, locks released | 1 | 1 |
| 125 | another fresh turn_off | succeeded_verified | 1 | 2 |

The same applies after manual review, except the refusal then lasts
indefinitely (probe "never reports" and "reports late at +130 s" rows, Q1).

### Scope beyond the entity

Fan, power, and F3 share one lock state file:
`governance_path/f3-operation-locks-v1/state.json`.

- `FanLockStore` is given the raw `governance_path` (`fan/service.py:56`,
  `:393`; `power/service.py:54`).
- `DurableLockStore` appends `LOCK_NAMESPACE` (`f3/locks.py:41-42, 181`).
- F3's `RuntimeLockStore` uses the same root (`f3_runtime/runtime.py:1233`;
  `governance/runtime.py:182`).

The consequences:

- While observing, the SHARED `home_assistant:core` and `addon:ha_mcp` locks
  conflict with F3 plans that take those keys EXCLUSIVE: HA restart and ha_mcp
  add-on restart (`f3/operational_locks.py:183-245`;
  `f3_runtime/runtime.py:2375-2410`). This is **inferred from source; the
  probe did not exercise the F3 restart path.**
- A permanent fan or power entity hold blocks any F3 governed acquisition on
  `entity:<id>`.
- F3 runtime health counts these holds in `active_conflict_hold_count`
  (`f3_runtime/runtime.py:4639, 4670, 4723`). It does not attribute them, and
  they never appear as F3 reconciliation items (`:1463-1470`, `:4222-4240`).

## Q5 — beta.15 compared with beta.14, beta.13 and v2.3.0

**Pre-existing; not a regression.**

- **Constants.** `LockTiming(120, 10, 0)` and `ExecutorTiming(180, 120, 6, 6)`
  are unchanged since fan was introduced (`6b8abe2`, 2026-09-16) and power
  (`0acbad5`, 2026-09-18). v2.3.0 is the first tag that contains power.
- **beta.13 → beta.14** (`06d5555..bdb5af8`). The only `f3/executor.py`
  change is pre-dispatch authority refusal codes. It does not reach the
  observation, recovery, or lease paths.
- **beta.14 → beta.15** (`bdb5af8..ba9db1f`):
  - adds `terminalize_expired_claim_pre_dispatch` and
    `materialize_expired_pre_dispatch` to `f3/persistence.py`. These are
    called only from the F3 runtime sweep (`f3_runtime/runtime.py:5668`,
    `:5736`) and apply to pre-dispatch records;
  - changes the F3 runtime sweep.
- **Unchanged from beta.13 to beta.15:** `fan/`, `power/`,
  `providers/upstream_fan.py`, `providers/upstream_power.py`,
  `application.py`, and the fan, power and governance tools.
- **Probe.** Run against each tag's own source and fixtures. The output is
  identical across v2.3.0, beta.13, beta.14 and beta.15 once task IDs are
  normalized. v2.3.0 additionally printed one unrelated `pydantic_settings`
  warning.

## Q6 — Truthfulness while observing and at the end

### What is honest

- The tool returns `state=observing`, `terminal=false`, and
  `terminal_outcome=null`, never success (`fan/service.py:282-284`).
- `provider_attempt_count=1`, `redispatch_prohibited=true`, and
  `dispatched_at=null` are stated explicitly (`:285-290`).
- The tool docstrings tell the caller to inspect the task outcome rather than
  outer response success (`tools/power.py:32`, `tools/fan.py:31`).
- The final `succeeded_verified` comes only from an actual re-read that
  matches. It never comes from timing or inference.

### Gaps

1. **No reason or schedule.** The receipt does not say why the task is still
   observing (device not yet reported, as opposed to a read error), or when a
   re-read can next happen. Replays inside the lease look identical to
   replays that read.
2. **No observation or verification times in the receipt.** The receipt's
   only times are `dispatch_intent_recorded_at` and
   `provider_response_recorded_at` (`fan/audit.py:27, 30-38`;
   `f3_runtime/dispatch_facts.py:242-272`). `verification` holds only hashes
   and fingerprints (`f3/executor.py:1178-1189`).
   - Per-event `occurred_at` exists in the execution record and in the audit
     rows (`fan/audit.py:121-129`). That includes the
     `fan_verification_recorded` / power equivalent events.
   - That is server-side write time.
   - **No surface records when the device changed.** The adapter only digests
     the state read. The `last_updated` value read for power is folded into
     the fingerprint, not exposed (`power/contracts.py:58-67`;
     `fan/adapter.py:135-142`).
3. **Delayed verification time.** For the live turn_on, the receipt's
   eventual verification reflects the reconciliation at about +150 s, not the
   device change at about +2 s.
4. **False-negative terminal outcome.** A device that is correct at any point
   after the +120 s re-read ends `manual_review_required`, with a permanent
   hold, because of the deadline-before-read ordering (probe: device reports
   at +130 s → manual review at +240 s). The outcome is truthful about the
   process ("evidence deadline expired"). It is misleading about the device,
   which was already in the requested state.
5. **The receipt's `"complete": True`** (`fan/service.py:296`) refers to the
   receipt's own completeness, not to the task. It appears on observing
   receipts too. This is a minor readability risk.
6. **`list_execution_tasks` does not list fan or power tasks.** It covers
   governance tasks only (`governance/service.py:2102-2140`). An agent can
   find an observing typed task only by ID. Health counts are the only
   aggregate view.

## Q7 — Options and recommendation

Every option below preserves the invariants that matter most:

- one durable dispatch intent per task, with `BlindRedispatchProhibited`
  (`f3/persistence.py` intent guard);
- recovery under an approval consumer that always refuses;
- verification only from a matching read.

None introduces a new write path.

| Option | Change | Safety (at-most-once, no blind redispatch) | Lock semantics | Truthfulness | Files touched (protected: all of `ha_mcp_engineering/`) | Effort |
|---|---|---|---|---|---|---|
| **(a) Bounded inline poll / adaptive window** | After `wait=False` dispatch, poll `ha_get_state` up to N times within a short cap, e.g. reads at +1, +2, +4 and +8 s (≤ about 10 s), inside the existing 20 s per-exchange and shielded execution | Unchanged. Reads only, under the same lock and claim, before the yield | The lock is held a few more seconds inline, but released ~2 min sooner in the common case | Resolves the 2-10 s class inline. The verification time then reflects the real change ±1 poll | `fan/adapter.py` (observe loop) or `f3/executor.py` (a generic inline observation budget). Possibly `ExecutorTiming` (`f3/models.py:418-433`) for a new bounded field. Tests in `tests/test_typed_power.py` and `tests/test_typed_fan.py` | S-M |
| **(b) Bounded background re-observation** | Make the sweep able to re-read promptly after a yield. Either release or shorten the lease on yield to a recovery-specific value (e.g. 15-30 s), or let the sweep take over a yielded-claim lease when the owner has explicitly yielded. Re-read on a fixed backoff until the deadline | Unchanged. The read-only recovery path is already the one used today, just earlier. The fencing generation is still honoured by `recover_expired` | Locks stay held while observing (target protection preserved). Ownership hand-off becomes prompt instead of lease-bound. Must not open a window in which a superseding command can acquire the entity lock between yield and transfer | Verification lands within ~30 s instead of ~150 s. Late devices (+30 s to +180 s) still verify | `f3/executor.py` (`_recovery_handle`, yield path), `f3/locks.py` (yield-aware transfer or short lease), `fan/service.py` (timing). Tests across fan, power and F3 executor | M |
| **(c) Shorter replay cooldown** | Lower `LockTiming` lease (e.g. 30 s) for fan and power only | Unchanged | A shorter lease also shortens protection against a crashed live owner. Renewal every 10 s already exists for the live execution path, so this is acceptable within `LockTiming` bounds (30-3600 s, `f3/models.py:195-220`) | Replays and sweeps re-read after ≤30 s. Still no inline improvement | `fan/service.py:150` (one constant), plus tests | XS |
| **(d) Lock behaviour while observing** | (d1) Allow a **superseding command** for the same entity: mark the observing task `superseded` (non-verified terminal, no hold) and dispatch the new command. (d2) Keep refusal, but name the blocking task and its next re-read time in the refusal receipt | (d1) **No redispatch of the original**, but there is now a second write while the first's effect is unverified. Ordering against a late device report is undefined: the old command can land after the new one. Needs a new terminal outcome and explicit owner acceptance. (d2) Unchanged | (d1) Changes EXCLUSIVE semantics on the target and is a security-architecture change under AGENTS.md. (d2) None | (d1) Must report the superseded task as unverified honestly. (d2) Improves truthfulness | (d1) `f3/executor.py`, `f3/locks.py`, `fan/service.py`, `fan/contracts`, public schemas/receipt, tool docstrings. (d2) `fan/service.py` receipt only | (d1) L, (d2) S |
| **(e) Combination** | (a) + (b) or (c) + deadline/lease alignment + a truthful receipt hint (d2), **without** (d1) | Unchanged | Target stays protected. The lock is released within seconds in the common case and ≤~30 s otherwise | Best. Each case ends with an accurate outcome and timing | Union of (a), (b)/(c) and (d2) | M |

### Deadline and lease alignment (needed by any option that keeps the current deadline)

The 180 s deadline is shorter than the 240 s renewed lease, and the deadline
is checked before reading. Together these make the 3rd to 6th observation
attempts unreachable, and they turn a late-but-correct device into a permanent
manual-review hold.

Any of these corrects it:

- perform a final read at or after the deadline before deciding on manual
  review, by moving the check after a read that is still bounded; or
- choose the lease and re-observation cadence so several reads fit before the
  deadline; or
- extend the deadline for typed power and fan.

Each is a small change in `f3/executor.py` or `fan/service.py`. The first
touches the shared executor, so it needs F3 regression coverage.

### Recommendation

**(e): (a) + (c) as the minimal step, with deadline and lease alignment and
(d2). Do not do (d1).**

1. **(a)** A bounded inline poll, with a total cap of about 10 s, resolves the
   common 2-10 s case inside the call. The live lamp would have returned
   `succeeded_verified` in about 11-15 s with the lock released.
2. **(c)** Lower the fan and power lock lease to 30 s. This is simpler than
   (b) and lets the 30 s sweep pick up stragglers within about 60 s, while
   keeping the live-owner renewal. Prefer **(b)** only if the owner wants the
   yield/hand-off semantics fixed generally for F3 as well.
3. **Alignment.** With a 30 s lease, about 5 sweep reads fit before 180 s.
   Also make the deadline outcome take one final read, so a correct device is
   not left in a permanent hold.
4. **(d2)** Expose `observing_reason`, `next_reobservation_not_before`, and
   the verification event time, and name the blocking task on `lock_conflict`.
   These are additive receipt fields, which is a public-schema change.
5. **Separately (owner decision).** Decide on a governed release procedure for
   a fan or power manual-review hold. None exists today.

**Ordering against HAMCP-155 (governed deletion).** This does **not** need to
land first on technical grounds:

- governed deletion runs through F3 governed plans, not the typed fan and power
  executor;
- the shared-executor changes in (a) and (c) are confined to typed timing;
- the alignment change touches `_observe_and_verify` in the shared executor.

Two interactions to note for the owner:

- A typed task stuck in a permanent hold on an entity blocks any F3
  acquisition of that `entity:<id>` key, including a deletion plan that
  targets the same entity.
- If HAMCP-155 changes `f3/executor.py` deadline or manual-review handling,
  sequence the two to avoid conflicting edits.

Suggested ordering: HAMCP-155 first if it is ready; this work as a follow-up
that rebases its executor change on top of it. Alternatively, land (a) and (c),
which are typed-only and small, before HAMCP-155, and do the alignment change
after it.

## Owner decisions

1. **Remedy scope.** Choose (a)+(c)+alignment+(d2) as recommended, (b) instead
   of (c), or a subset.
2. **Inline poll budget.** Choose the maximum added call latency (proposed:
   ≤ about 10 s), knowing it extends a call that already takes about 9 s.
3. **Superseding commands (d1).** Keep the refusal (recommended), or accept a
   second write while the first is unverified, with a new `superseded`
   outcome. Accepting it is a security-architecture change.
4. **Deadline outcome.** Allow a final read at the deadline, or extend the
   180 s deadline, so a late but correct device verifies instead of entering
   permanent manual review.
5. **Manual-review hold release.** Today a fan or power hold never releases.
   Decide whether an owner-authorized release path should exist, and how it is
   governed.
6. **Sequencing against HAMCP-155.** Decide whether this lands before or after
   it, given the shared `f3/executor.py`.

## Appendix A — probe method

The probe is a scratch script, `.artifacts/hamcp192/probe_observing.py`. It is
git-ignored and **not committed**.

**Setup.** It imports the target checkout's own `PowerService` and `FanService`,
the shared F3 executor, and the durable lock and execution stores. It also uses
that checkout's synthetic fixtures (`tests/test_typed_fan.py` `Clock`, `Core`,
`Transport`; `tests/test_typed_power.py` `PowerTransport`).

**Slow device model.** A slow device is modelled with the fixture's `ack_only`
mode (the service acknowledges, but state does not change). The fixture state
is then flipped manually at a chosen offset on the fake clock, where t = 0 is
the inline readback.

**What it records.** Re-reads (counted from `ha_get_state` calls), writes,
`observation_attempts`, claim expiry, evidence deadline, and each lock's key,
mode, hold flag and lease. Recording happens after every replay, sweep, or new
command.

**Scenarios:**

- exchange list for one call;
- replay-only (device at +2 s);
- sweep-only (+2 s and +6 s);
- superseding command;
- device never reports;
- device reports at +130 s;
- fan analogue.

**Runs:**

| Tag | Source | Python environment |
|---|---|---|
| beta.15 | worktree | venv with mcp 1.30.0 |
| beta.14, beta.13 | `git archive` exports | same venv |
| v2.3.0 | `git archive` export | venv matching its `mcp==1.28.1` pin |

v2.3.0 needs its own venv because its startup SDK compatibility check refuses
a mismatched `mcp`.

**Limits.**

- Wall-clock sweep phase and real provider latency are not modelled.
- Live, t = 0 sits about 1 s after the provider acknowledgement, and the first
  sweep falls 0-30 s after lease expiry. That is consistent with the observed
  ~2.5 min.
- The F3 restart-plan lock interaction in Q4 is inferred from source and was
  not executed.

No unit test suite or CI check was run for this report. It changes
documentation only.
