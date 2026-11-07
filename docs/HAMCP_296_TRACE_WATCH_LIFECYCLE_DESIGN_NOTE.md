# HAMCP-296 Trace Watch Lifecycle: Design Note (2.6.0 wave 1)

Status: design note only (HAMCP-332 wave 1). No runtime, test, schema, version or protected-path change.
Base: `origin/release/2.6.0` = `7246f2d21693f039504542574569f03b71476489` (2.4.2-beta.7). Citations are at that base,
relative to `hass_mcp_engineering_beta/ha_mcp_engineering/` unless a full path is given.

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
- Not decided by any row or prompt: raise the cap versus manage it by priority (section 9, Q1).

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

**3.5 Persistence (rollback-safe).** The v1 index format does not change. Lapsed records, `renewed_at` and any
priority field live in a sidecar file, `trace-snapshots-v1/watch-lifecycle.json`. It is written under the same
`flock`, with the same `O_NOFOLLOW`, 0600, fsync and rename discipline, a closed-schema validator and a 64 KiB
bound. Write order: sidecar first, then index. If the index write fails, the next sweep re-records the lapse
idempotently by `(watch_id, expired_at)`. A missing sidecar reads as empty. A damaged one is replaced by an empty
one, reported as `lifecycle_store: "reset"`, and never blocks the snapshot store. Shipped 2.4.x/2.5.0 code ignores
the sidecar (section 2), so a downgrade keeps working while the cap stays at 10.

**3.6 Slot capacity (Q1).** The outcome needs a raise with a stated bound, priority, or both. Facts at base:

| Option | Core read load | Store effect | Persistence and rollback | Schema |
| --- | --- | --- | --- | --- |
| S1: raise to N (for example 20) | 2N `trace/list` a minute (40 at N=20, 20 today), plus `trace/get` per matching run. The read share per watch per tick falls from 1.6 s to 16/N s (0.8 s at 20). Live `trace/list` latency is unknown, with no live measurement allowed. | The quota sum 20N exceeds `MAX_SNAPSHOTS = 200` (exactly 10 x 20 today). Either raise `MAX_SNAPSHOTS` (bytes stay capped at 32 MiB, and a live trace is about 8 KB) or lower `WATCH_QUOTA`. Otherwise full watches crowd out manual captures (E1 `store_full`). The index grows by about 13 KB a watch (100 seen run IDs), well inside 2 MiB. | The v1 validator rejects more than 10 watches (`trace_snapshots/store.py:127`). A downgrade with more than 10 active watches makes the whole trace store `store_invalid` until watches are cut to 10. | `bounded_poller.max_watches` metadata (`capabilities.py:152-160`, `881-889`) and the coverage matrix fingerprints change. |
| S2: keep 10, add priority | Unchanged. Priority orders the tick, so `high` watches are polled first when time runs short. | Unchanged. | New sidecar field only; rollback-safe. | `priority: "high" or "normal"` on add and renew. `add` at capacity with `displace: true` replaces the lowest-priority, soonest-expiring `normal` watch, which is recorded as lapsed with `reason: displaced`. It is never silent, and a `high` watch is never displaced. |
| S3: S1 and S2 | As S1 | As S1 | As S1 | As S1 and S2 |

## 4. Public schema and tool-registration impact

- Tool count is unchanged: the action and fields go on the existing tool, and no tool is added. Changed public
  input: `action` gains `renew`, plus `include_lapsed`, and with S2 `priority` and `displace`. Every change needs the
  same closed validation in `trace_snapshots/contracts.py`, `routing.py:222` and the `TraceSnapshotTool` raw check.
  Changed output: additive watch fields plus `lifecycle`, `lapsed` and `renewed`. `get_server_health` gains the
  additive `trace_watches` block.
- Security-sensitive surfaces touched: the public schema, the audit projection (`routing.py:203-210`, extended for
  the new fields with the same withheld-identifier rule), capability metadata, `docs/read_coverage_matrix.json`
  fingerprints, persistence (the sidecar), and health output. Unchanged: provider routing (`engineering_native`,
  `fallback: none`), Core independence (`ha_core_readmission/routes.py:411-416`), and the `idempotentHint=False`
  annotation (add/remove are not idempotent).
- 2.5.0 overlap: none in `trace_snapshots/`, `health.py` or `tools/compatibility.py` (#387 touches only a health
  test). If one lands first, 2.5.0 wins the shared file.

## 5. Signing impact

None. No signed Core profile is new or changed. Renew, lapsed records, warnings and the health block are local
store and in-memory work with no Core call. The poller keeps the same `trace/list` and `trace/get` commands under
the already-admitted `core.automation_trace_read`. Raising the cap (S1) changes only how often those admitted
commands are sent, not which commands are admitted.

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
- Failure preservation: an audit write failure commits no renewal, and the watch list and sidecar are unchanged.
  `store_busy` changes nothing and is retryable. A sidecar write failure leaves the index unchanged and the lapse is
  re-recorded at the next sweep. A damaged sidecar is reset, and snapshots and watches stay readable. A health
  summary failure fails only the block.
- Sanitization: a withheld identifier's lapsed record holds only the digest; a needle planted in the sidecar is withheld.
- Rollback: an index written by the new code is accepted by the unchanged v1 `validate_index` (cap 10). With S1,
  a test proves an index with more than 10 watches is rejected by the v1 rule, which documents the downgrade step.
- With S2: `displace` never removes a `high` watch or acts without `displace: true`, and always records and audits it.

## 7. Acceptance

- Success (offline, fake clock): add three watches. At the warning window, `expiring_soon` and the counts appear in
  the watch result and in health. `renew` with `""` extends all three in one call with one audit row each. Let two
  lapse across a simulated restart: both appear in `lapsed` and in health with their original `expired_at`.
  `renew` with `include_lapsed: true` reinstates them with the same `watch_id` and reports `lapse_gap_seconds`.
- Refusal: section 6. Failure preservation and recovery: all HAMCP-225 suites (snapshot suite, reviewer probes,
  HAMCP-191 sanitizer sites) pass unchanged; the sidecar-reset and index-write-failure paths recover at the next sweep.
- Live acceptance (after deployment, Josh-authorized only): one live renew, then one health read. Not part of review.

## 8. Build slice and dependencies

- One runtime PR for a 2.6.0 beta. It touches the protected path `hass_mcp_engineering_beta/ha_mcp_engineering/`
  (`trace_snapshots/`, `tools/trace_snapshots.py`, `routing.py`, `health.py` and `application.py` health wiring,
  `capabilities.py`), so the build task must authorize it explicitly. It also touches tests, the regenerated
  `docs/read_coverage_matrix.json`, `docs/READ_COVERAGE_MATRIX.md`, `TOOL_AUDIT.md` and the changelog. Security
  section required (persistence, public schema).
- Dependencies: Q1 before build (it fixes schema and bounds); no signing or unlanded-2.5.0 dependency. Lane order per
  the row: after HAMCP-277 (Josh's ranking, 2026-11-03).
- Rollback: revert the PR. Shipped code ignores the sidecar. With S1, cut the watch list to 10 or fewer before
  downgrading, or the v1 reader reports `store_invalid`. That step goes in the release notes.

## 9. Open questions — pending Josh

- **Q1 (capacity), pending Josh:** S1 raise the cap (state N, and whether `MAX_SNAPSHOTS` is raised to 20N or
  `WATCH_QUOTA` is cut), S2 keep 10 with priority and explicit displacement, or S3 both. The row allows either and
  decides neither. Costs are in section 3.6.
- **Q2, pending Josh:** confirm the 24 h / half-term warning window and the 20-record / 7-day lapsed retention, or
  give other values. These are the author's proposed defaults, not recorded decisions.
- **Q3, pending Josh:** is bulk reinstatement (`include_lapsed`) wanted, or only a named per-watch reinstatement?
  Both meet "without re-creating each watch". The bulk form is the one that fits the 2026-11-02 six-watch lapse.
