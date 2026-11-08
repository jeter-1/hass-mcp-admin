# HAMCP-296 Trace Watch Lifecycle: Design Note (2.6.0 wave 1)

Status: design note only (HAMCP-332 wave 1). No runtime, test, schema, version or protected-path change.
Base: `origin/release/2.6.0` = `7246f2d21693f039504542574569f03b71476489` (2.4.2-beta.7). Citations are at that base,
relative to `hass_mcp_engineering_beta/ha_mcp_engineering/` unless a full path is given.

## 0. Disposition (2026-11-08): historical baseline, reviewed delivery, deferred options

- **Historical baseline.** Sections 1-9 were written before the build, at design base `7246f2d2`. Where they
  conflict with the delivered contract below, the delivered contract wins. Superseded text is kept only under
  "Superseded (historical)" labels.
- **Reviewed delivery.** HAMCP-296 is merged into `release/2.6.0` as
  [PR #401](https://github.com/jeter-1/hass-mcp-admin/pull/401): reviewed head
  `5bf1398cf5cef669b72da94155a88eb962fa1056`, merge commit `1c4c32b3c2da152bd42f25178b077c62c13af7d4`.
  `git merge-base --is-ancestor 5bf1398c origin/release/2.6.0` passes. The capacity, persistence and reset
  decisions merged in #401 stand: Josh direction (register 2026-11-07; confirmed in-session 2026-11-08). This note
  makes no new capacity decision. The delivered contract is in sections 3.5 and 3.6, and the downgrade warning is in
  section 8. Its citations are at `1c4c32b3`, in `trace_snapshots/contracts.py` and `trace_snapshots/store.py`.
- **Not claimed.** This note claims no live acceptance. Live polling-load and storage measurement at 2.6 acceptance
  needs Josh's separate authorization (HAMCP-296 row).
- **Deferred or optional.** No HAMCP-296 option is still open for the merged slice. Script traces stay out of scope
  (O1).

## 1. Register outcome and binding decisions

HAMCP-296 Outcome (register, quoted): "Automation trace watches (HAMCP-225) no longer lapse silently: health or a
tool result warns before expiry and reports lapsed watches, renewal is possible without re-creating each watch, and
slot capacity (10 today, all in use) is either raised with a stated bound or managed by priority. On 2026-11-02 six
watches lapsed overnight unnoticed." Dependency: HAMCP-225 (Completed, live as 2.4.1-beta.39).

Binding decisions:
- Josh, 2026-11-07 brief: HAMCP-296 is a 2.6.0 wave-1 feature; designs first.
- Josh, 2026-10-26 (HAMCP-225 row, Part 2): W1 bounded read-only poller, at most 10 watches, 30 s poll; E1 manual
  capture refused at capacity, E2 per-watch 20-snapshot own-oldest eviction, manual never evicted; bounds B as
  written; W1 addition: watch add/remove needs no change plan or approval, is audited with the caller, is bounded at
  10, and every response returns the current list
  ([HAMCP-225 design](HAMCP_225_226_228_AUDIT_TOOLING_DESIGN.md), lines 26-50).
- Josh, 2026-10-26 (HAMCP-225 row, O1): automation traces only. Script traces are out of scope here.
- Capacity (Q1): decided and merged in #401 (section 3.6). Superseded (historical): at the design base no row or
  prompt had yet chosen between raising the cap and managing it by priority.

## 2. Current state at base

- Bounds: `MAX_WATCHES = 10`, `WATCH_QUOTA = 20`, `WATCH_MAX_HOURS = 168`, `POLL_SECONDS = 30.0`,
  `TICK_SECONDS = 20.0`, `READ_SHARE = 0.8`, actions `add`/`remove`/`list` (`trace_snapshots/contracts.py:34-44`).
  Store bounds: 200 snapshots, 256 KiB each, 32 MiB total, 2 MiB index (`trace_snapshots/contracts.py:20-23`).
- Tool: `set_automation_trace_watch(action, automation_id, mode, duration_hours=24)`
  (`tools/trace_snapshots.py:106-130`), annotations `readOnlyHint=False`, `idempotentHint=False`
  (`tools/trace_snapshots.py:161-165`). Closed raw-argument check: `trace_snapshots/contracts.py:152-170` and
  `routing.py:222`. Audit projection: `routing.py:203-210`.
- TTL: `add` sets `expires_at_ms = now + hours`. An `add` on an already watched automation replaces mode and expiry
  and keeps `watch_id`, counters and snapshots (`trace_snapshots/service.py:822-825`). That is a per-watch renewal
  today, but only while the watch is active, one call per watch, and the caller must restate the mode.
- Slot limit: an `add` at 10 watches is refused `watch_limit` with the list (`trace_snapshots/service.py:826-829`).
  The index validator also rejects more than 10 watches (`trace_snapshots/store.py:123-136`, line 127).
- Expiry: every store transaction sweeps watches whose `expires_at_ms <= now` out of the index
  (`trace_snapshots/service.py:308-329`). The only trace of the lapse is a `trace_watch_expired` audit row written
  with `required=False`, so a failed audit write loses it (`trace_snapshots/service.py:355-363`). A watch whose
  withheld identifier cannot be read after a restart ends the same way (`trace_snapshots/service.py:936-958`). After
  the sweep, nothing in the store records that the watch existed. Its snapshots remain until the 90-day age expiry.
- Persistence: `<governance_path>/trace-snapshots-v1/index.json` plus `records/` (`trace_snapshots/store.py:146-149`).
  The watch row key set is closed (`trace_snapshots/store.py:32-36`, `93-108`). Only `records/` is listed by the
  sweep (`trace_snapshots/store.py:299-311`), so another file in the namespace root is ignored by shipped code.
- Watch output: `status` is `awaiting_first_poll`, `watching` or `paused_core_unavailable`, plus `expires_at`. There
  is no time-left field, warning, or lapsed list (`trace_snapshots/service.py:794-808`, `865-893`).
- Health: `get_server_health` (`tools/compatibility.py:232-310`) returns `HEALTH.snapshot`
  (`health.py:93-200`), which has no trace-watch block. `TraceSnapshotRuntime.health()` exists
  (`trace_snapshots/runtime.py:33-37`: configured, paused, ticks, failed_ticks) but no runtime caller uses it. The
  HAMCP-225 design says "Server health output is unchanged" (design line 582).
- Poller: one `trace/list` per watch per 30 s tick. Reads get 16 s per tick, shared equally with rotation, and
  there is no priority (`trace_snapshots/service.py:895-934`, `trace_snapshots/poller.py:83-108`).

Missing: advance warning, a durable lapsed record, bulk or post-lapse renewal, health visibility, and any slot
policy other than a hard refusal.

## 3. Proposed change

All of it is Engineering-native and local. It adds no Core command, no ha-mcp delegation and no Home Assistant
write. The poller keeps its existing per-tick `core.automation_trace_read` lease.

**3.1 Expiry warning.** Add `EXPIRY_WARNING_HOURS = 24`. A watch is `expiring_soon` when its time left is at most
`min(24 h, half of its current term)`. The term is `expires_at - max(created_at, renewed_at)`, so a 24-hour watch
warns at 12 h and does not warn for its whole life. Each public watch gains `expires_in_seconds` (an integer, never
negative), `expiring_soon` (a boolean) and `renewed_at` (UTC or null). `status` keeps its current three values, so
no consumer of that field breaks. Every watch-tool response, including refusal details, gains a
`lifecycle` block: `{expiring_soon_count, next_expiry_at, lapsed_count, warning_window_hours}`.

**3.2 Lapsed records.** When the sweep removes an expired watch, or `_end_watches` ends one, the same transaction
records `{watch_id, automation_id (stored, screened form), mode, created_at, expired_at, reason: expired |
identifier_unavailable, captured_count, snapshots_held}`. A deliberate `remove` is not a lapse and is not recorded.
Retention: at most 20 records (`LAPSED_MAX`) and at most 7 days (`LAPSED_RETENTION`, the same as the 168 h maximum
term). The oldest record goes first, and records dropped by the count bound are counted (`lapsed_dropped`), the same
pattern as the quarantine stubs. Reinstating a watch, or adding it again, clears its record. A lapse that happens
while the server is down is recorded at the first sweep after restart, with the original `expired_at`. Every watch
response carries `lapsed: [...]` (at most 20). The existing best-effort audit row is kept.

**3.3 Renew.** New action `renew` with `automation_id` (`""` means every active watch), `duration_hours` (1-168,
default 24) and `include_lapsed` (a boolean, default `false`, accepted only with `automation_id: ""`).

- The new expiry is `max(current expiry, now + duration_hours)`. Renew never shortens a watch and never sets an
  expiry later than now + 168 h, the existing maximum. Repeating the same renew is idempotent: it changes nothing,
  or moves expiry forward only by elapsed time, and always stays inside the cap. Mode, `watch_id`, counters, seen
  runs and snapshots are kept. A mode change still uses `add`.
- Lapsed reinstatement (a named `automation_id` whose lapsed record is still retained, or `include_lapsed: true`)
  restores the same `watch_id` and mode with a fresh baseline. The lapse interval is reported as `lapse_gap_seconds`.
  There is no backfill, the same rule as HAMCP-225 M1. Reinstatement needs a free slot. In a bulk renew, lapsed
  records are reinstated most recent first until the slots are full, and each one left over gets a per-watch
  outcome of `watch_limit`.
- Result: `renewed: [{automation_id, outcome: renewed | unchanged | reinstated | watch_limit}]`, plus the full list
  and the `lifecycle` block. A named watch that is neither active nor retained as lapsed is refused
  `watch_not_found`. One `trace_watch_change` audit row (`result_status: "renew"` or `"reinstate"`) is written per
  changed watch before the commit. If any audit write fails, nothing is committed (the existing rule,
  `trace_snapshots/service.py:845-857`). A bulk renew changes at most 10 active watches and reinstates at most 10.
  It runs in one store transaction. `store_busy` stays retryable and changes nothing.
- No new authority: `add` already extends an active watch indefinitely (`trace_snapshots/service.py:822-825`).

**3.4 Health surface.** Add a `trace_watches` block to `get_server_health`: the existing runtime fields (configured,
paused, ticks, failed_ticks), plus `active`, `limit`, `expiring_soon_count`, `next_expiry_at`, `lapsed_count`,
`lapsed_dropped`, `expiring_soon` and `lapsed` (each at most 10 `{automation_id, at}` items, screened by the same
trace exit), and `as_of`. The block comes from an in-memory summary. Every store transaction that loads the index
refreshes it (the 30 s tick, the watch tool and capture), so health does no disk I/O and takes no store lock.
Before the first transaction it reports `status: "not_yet_observed"`; a summary failure degrades only this block
(`status: "unavailable"`) and health still succeeds.

**3.5 Persistence (delivered contract, #401).** Citations here are at `1c4c32b3`. The v1 `index.json` schema does
not change, and the store follows the existing discipline: one `flock`, `O_NOFOLLOW` reads of regular files, mode
0600, and a temporary file plus fsync plus rename (`trace_snapshots/store.py`, module docstring).

- **Legacy index kept within v1 bounds.** `index.json` never exceeds what the shipped 2.4.x/2.5.0 v1 reader accepts:
  10 watches, 200 snapshot entries and 32 MiB of entry bytes (`V1_INDEX_MAX_WATCHES`, `V1_INDEX_MAX_SNAPSHOTS`,
  `V1_INDEX_MAX_BYTES` in `contracts.py`). It holds the first 10 watches in `watch_order`, high priority first, and
  the snapshot entries that `v1_snapshot_ids` selects: manual captures first, then the newest watch captures
  (`keep_order`).
- **Two digest-bound A/B slots for overflow and lifecycle state.** Everything past the v1 bounds is one
  closed-schema sidecar state (model `trace-watch-sidecar-v1`, at most 4 MiB, `SIDECAR_BYTES`). That covers watches
  11-20, the priority and renewal rows, the lapsed records with `lapsed_dropped`, and the snapshot entries the index
  does not keep. The state is written to one of two slot files, `watch-sidecar-a.json` or `watch-sidecar-b.json`,
  and never to the committed one. Each slot carries a generation and the sha256 of the exact `index.json` bytes it
  pairs with. Shipped code opens only `index.json`, `records/` and the lock file, so a slot cannot make the v1 reader
  fail.
- **Single commit point.** `write_index` writes the new state to the uncommitted slot, bound to the new index bytes,
  and then replaces `index.json`. The index rename is the single commit point. When the index bytes do not change,
  the slot rename is the commit point instead. A load uses only a slot bound to the index on disk; of two bound
  slots, the higher generation wins. A failed write therefore leaves the prior index and slot pair, and a slot from
  a failed transaction is never combined with the prior index. If a failure happens after a commit-point rename, the
  outcome is unknown: the store sets `commit_unknown` and refuses every later transaction until a restart reloads it.
- **Damaged copy: reset to empty, never older-copy fallback.** The sidecar state resets to empty in three cases:
  either slot is damaged, or slots exist but none is bound to the index on disk (for example, a downgraded server
  rewrote `index.json`). A damaged slot resets the state even when the other slot is valid and bound. After a
  sidecar-only commit both slots name the same index bytes, so the older slot could revive a state that a completed
  watch change replaced (Codex D1 on #401, `_read_slots`). A reset never blocks the snapshot store: v1 watches and
  v1 snapshot entries stay readable. An I/O error refuses with `store_unavailable` and does not reset. The reset is
  reported as `lifecycle_store: "reset"` in the `lifecycle` block of every watch result and in the
  `get_server_health` `trace_watches` block.
- **What a reset loses.** It loses watches 11-20, the priority and renewal rows, the lapsed records and the
  overflow snapshot entries. Nothing names those record files any more, so the next committed transaction's tidy
  removes them.

Superseded (historical): the pre-build proposal was one 64 KiB `trace-snapshots-v1/watch-lifecycle.json` sidecar,
written before the index and re-recorded idempotently after a failed index write. It also predicted that a
downgrade above ten watches would make the v1 store `store_invalid`. Neither describes the delivered store. The
delivered `index.json` never exceeds the v1 bounds, so the delivered downgrade risk is silent data loss, not an
invalid store (section 8).

**3.6 Slot capacity (delivered contract, #401).** These are the decisions merged in #401 (`contracts.py` at
`1c4c32b3`). This note does not reopen them.

- **Watches and quota.** `MAX_WATCHES = 20` watches with `WATCH_QUOTA = 20` snapshots each. E2 is kept: a watch
  evicts only its own oldest snapshot.
- **Watch reservation.** Watch captures together, whether from active, ended or lapsed watches, never exceed a
  reservation of 400 snapshots and 100 MiB (`WATCH_STORE_SNAPSHOTS = 20 x 20`, `WATCH_STORE_BYTES`). A watch capture
  past the reservation is refused `store_full`, and nothing is evicted for it (refuse at capacity).
- **Overall cap.** `MAX_SNAPSHOTS = 500` and `STORE_BYTES = 128 MiB`. At least 100 snapshots and 28 MiB therefore
  always stay available for manual captures, which are still refused at capacity (E1).
- **Priority.** `priority` is `"high"` or `"normal"`. New watches are `normal`, and the poller reads `high` watches
  first.
- **Explicit displacement.** An `add` at 20 watches is refused `watch_limit` unless it carries `displace: true`.
  With it, the `normal` watch that expires soonest is ended, recorded as lapsed with `reason: "displaced"`, audited
  and returned as `displaced`. A `high` watch is never displaced. If no `normal` watch exists, the add is still
  refused.
- **Core read load.** At most 40 `trace/list` reads a minute at the 30 s poll (20 at the old cap of 10). Each
  watch's share of a tick's 16 s read time is 0.8 s (1.6 s before). Live latency was not measured (section 0).

Superseded (historical): the pre-build options table offered S1 (raise to N), S2 (keep 10 and add priority with
displacement) and S3 (both) as open choices. The merged decision is S3 with N = 20 and the reservation above.

## 4. Public schema and tool-registration impact

- Tool count is unchanged: the action and fields go on the existing tool, and no tool is added. Changed public
  input: `action` gains `renew`, plus `include_lapsed`, `priority` and `displace` (all delivered in #401). Every change needs the
  same closed validation in `trace_snapshots/contracts.py`, `routing.py:222` and the `TraceSnapshotTool` raw check.
  Changed output: additive watch fields plus `lifecycle`, `lapsed` and `renewed`. `get_server_health` gains the
  additive `trace_watches` block.
- Security-sensitive surfaces touched: the public schema, the audit projection (`routing.py:203-210`, extended for
  the new fields with the same withheld-identifier rule), capability metadata, `docs/read_coverage_matrix.json`
  fingerprints, persistence (the two digest-bound sidecar slots, section 3.5), and health output. Unchanged: provider routing (`engineering_native`,
  `fallback: none`), Core independence (`ha_core_readmission/routes.py:411-416`), and the `idempotentHint=False`
  annotation (add/remove are not idempotent).
- 2.5.0 overlap: none in `trace_snapshots/`, `health.py` or `tools/compatibility.py` (#387 touches only a health
  test). If one lands first, 2.5.0 wins the shared file.

## 5. Signing impact

None. No signed Core profile is new or changed. Renew, lapsed records, warnings and the health block are local
store and in-memory work with no Core call. The poller keeps the same `trace/list` and `trace/get` commands under
the already-admitted `core.automation_trace_read`. The cap raised to 20 in #401 changes only how often those
admitted commands are sent, not which commands are admitted.

| Profile id | New or changed (from version X) | Core commands / contract added | Why | Fingerprint impact | Can it batch with other 2.6 profiles |
| --- | --- | --- | --- | --- | --- |
| (none) | Not applicable | None | Local lifecycle state only, with existing admitted reads | None to a signed Core profile. The Engineering coverage-matrix fingerprints change (section 4). | Not applicable |

## 6. Negative reachability and refusal tests

No new Home Assistant write, service call or Core command shape. Tests extend `tests/test_hamcp225_trace_snapshots.py`:

- Renew and health make no Core call. The command mock is untouched by `renew`, `list` and `get_server_health`.
  The poller's command set is still exactly `{trace/list, trace/get}` (the existing negative command-shape check).
- Closed arguments: `renew` with `mode`, with `include_lapsed` plus an `automation_id`, with a non-strict
  `include_lapsed`, or with `duration_hours` of 0, 169, a bool or a string is refused `invalid_arguments` at both the
  transport and the tool. Unknown keys are refused. A withheld-digest `automation_id` input is refused.
- Bounds: renew never sets expiry beyond now + 168 h and never shortens it (fake clock, repeated calls). A bulk renew
  at capacity returns a per-watch `watch_limit` and changes nothing else. A lapsed record past 7 days, or dropped by
  the count bound, gives `watch_not_found`. The sidecar never holds more than 20 records, and the health lists never
  more than 10 items.
- Failure preservation (delivered contract, section 3.5): an audit write failure commits no renewal or
  displacement, and the index and both slots are unchanged. `store_busy` changes nothing and is retryable. A fault
  at every write stage leaves either the prior pair or the committed pair, and a slot from a failed transaction is
  never combined with the prior index. A damaged or unbound slot resets the sidecar state to empty and never loads
  the older slot. The reset is reported as `lifecycle_store: "reset"` in watch results and health, and v1 watches
  and snapshots stay readable. A health summary failure fails only the block. Superseded (historical): the
  pre-build text said a sidecar write failure is re-recorded at the next sweep; the delivered store is transactional
  instead.
- Sanitization: a withheld identifier's lapsed record holds only the digest; a needle planted in the sidecar is withheld.
- Rollback: with 20 watches and more than 200 snapshots, `index.json` still passes the shipped v1 rule (10
  watches, 200 entries, 32 MiB). A fixture generated by the shipped beta.6 code, with recorded provenance, is read
  by the current code. The v1 rule still rejects an eleven-watch index, which is why `index.json` never holds one.
- `displace` never removes a `high` watch, never acts without `displace: true`, and always records and audits the
  displaced watch. A watch capture past the 400-snapshot / 100 MiB reservation is `store_full` and evicts nothing.
- Evidence status: #401 delivered these checks (`tests/test_hamcp296_trace_watch_lifecycle.py` at `1c4c32b3`:
  `CoherentCommitTests`, `OverflowStoreTests`, `StoreTests`, `Beta6FixtureTests`, `PriorityTests`, plus the D1 test
  `test_a_damaged_slot_never_revives_an_older_sidecar_state`). This note cites them and did not run them.

## 7. Acceptance

- Success (offline, fake clock): add three watches. At the warning window, `expiring_soon` and the counts appear in
  the watch result and in health. `renew` with `""` extends all three in one call with one audit row each. Let two
  lapse across a simulated restart: both appear in `lapsed` and in health with their original `expired_at`.
  `renew` with `include_lapsed: true` reinstates them with the same `watch_id` and reports `lapse_gap_seconds`.
- Refusal: section 6. Failure preservation and recovery: all HAMCP-225 suites (snapshot suite, reviewer probes,
  HAMCP-191 sanitizer sites) pass unchanged; the sidecar-reset and index-write-failure paths recover at the next sweep.
- Live acceptance (after deployment, Josh-authorized only): one live renew, then one health read. Not part of review.

## 8. Build slice and dependencies

- Delivered (section 0): #401, merge `1c4c32b3`. Historical build plan: one runtime PR for a 2.6.0 beta. It touches the protected path `hass_mcp_engineering_beta/ha_mcp_engineering/`
  (`trace_snapshots/`, `tools/trace_snapshots.py`, `routing.py`, `health.py` and `application.py` health wiring,
  `capabilities.py`), so the build task must authorize it explicitly. It also touches tests, the regenerated
  `docs/read_coverage_matrix.json`, `docs/READ_COVERAGE_MATRIX.md`, `TOOL_AUDIT.md` and the changelog. Security
  section required (persistence, public schema).
- Dependencies: no signing or unlanded-2.5.0 dependency. Superseded (historical): "Q1 before build" was resolved
  before #401 was built.
- **Downgrade warning (required in the 2.6.0 release notes).** A shipped 2.5.0 (or 2.4.x) server reads only
  `index.json`. It ignores both sidecar slots and deletes the record files of every snapshot entry that `index.json`
  does not name. A downgrade therefore drops watches 11-20 and deletes every overflow snapshot. Priority, renewal
  and lapsed records are lost too. On a later upgrade, slots no longer bound to the rewritten index reset to empty
  and are reported as `lifecycle_store: "reset"`.
- **Before downgrading**, cut back to at most 10 watches and at most 200 snapshots totalling at most 32 MiB.
  Reducing watches alone is not enough. The release notes must carry this pre-downgrade step in those words.
- Rollback: reverting #401 is a code rollback with the same data consequence. Apply the pre-downgrade step first.

## 9. Open questions

None is open for the merged slice. The pre-build questions are resolved as follows.

- **Q1 (capacity): resolved and merged in #401.** S3 with 20 watches x 20 snapshots, the 400-snapshot reservation,
  a 500-snapshot / 128 MiB overall cap, priority, explicit displacement, and refuse at capacity (section 3.6).
- **Q2 (warning window and lapsed retention): as merged in #401.** The values are 24 h / half-term, 20 records and
  7 days. `contracts.py` labels them implementation defaults. Under Josh direction (register 2026-11-07; confirmed
  in-session 2026-11-08) the #401 decisions stand. This note does not reopen them; changing a value would be
  separate scope.
- **Q3 (bulk reinstatement): delivered in #401** as `include_lapsed` on `renew` with `automation_id: ""`, alongside
  named per-watch reinstatement.

## 10. Codex design review 2026-11-08 — disposition

| Finding | Severity | Correction in this note |
| --- | --- | --- |
| F1: obsolete persistence and downgrade contract | Medium | Sections 3.5 and 3.6 now state the merged #401 contract. The old single 64 KiB file, the S1/S2/S3 options and the invalid-old-reader prediction are kept only as "Superseded (historical)". Section 6 states transactional failure preservation, the reported reset and the delivered evidence. Section 8 carries the release-note downgrade warning: at most 10 watches and at most 200 snapshots / 32 MiB, because watches alone are not enough. Section 9 has no build blocker. Proving check: compare against `trace_snapshots/contracts.py` and `store.py` at `1c4c32b3` and the #401 failure, reset and legacy-writer tests. No runtime change or new capacity decision is requested. |
| F2: dated disposition | Low | Section 0 separates the historical baseline from the reviewed delivery (#401, head `5bf1398c`, merge `1c4c32b3`, ancestry checked) and the deferred options. It claims no live acceptance. |
