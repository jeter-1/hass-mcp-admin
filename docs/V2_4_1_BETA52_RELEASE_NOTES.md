# Engineering 2.4.1-beta.52 release notes

Engineering 2.4.1-beta.52 cuts the governance cost of
`get_server_health(check_ha=false)` toward Josh's latency SLO (HAMCP-097):
warm ≤ 1.5 s, and the first call after start ≤ 5 s or within 5 s with
explicit in-progress markers. A call no longer decodes or re-projects an
unchanged terminal record, and k changed records cost k re-projections.
Total cost is still history-dependent: every call still reads and hashes
every record and rebuilds the overlay and snapshot, so warm cost still
grows with retained history. On the largest benchmark fixture the first
call after a change takes 1.74 s, above the 1.5 s warm SLO. Josh accepted
that miss on 2026-10-31; the remaining scaling is HAMCP-259 (Medium). See
"Measured" below. It builds on 2.4.1-beta.51 (HAMCP-252, #331). It adds no tool, input schema,
route, provider, dispatch, persisted format, retention change or Home
Assistant write.

Acceptance: [`V2_4_1_BETA52_ACCEPTANCE.md`](V2_4_1_BETA52_ACCEPTANCE.md).
Contract: [`OBSERVABILITY.md`](../hass_mcp_engineering_beta/OBSERVABILITY.md),
section "2.4.1-beta.52 incremental governance health".
Benchmark: [`docs/evidence/hamcp097-health-benchmark/`](evidence/hamcp097-health-benchmark/).

## Why

The live store holds 501 plans (none active) and 331 task records with
2,422 events. Every health call decoded and re-projected all of them:

- **Warm (beta.48): 3.85 s.** Projection 1.65 s, snapshot 0.78 s, assembly
  0.75 s, overlay 0.59 s.
- **Cold (beta.50): 12.99 s.** `validation_wait` 8.94 s, projection
  1.94 s, snapshot 0.81 s.
- **Cold (beta.51), 9.7 minutes after start: 18.29 s.**

The cold cost is the persisted-record safety detector running over every
plan. The safety-verdict cache is process memory, and nothing filled it
before the first health call.

## The change

- **Byte-identical reuse.** A terminal record's decode, its projection
  outcome and the assembled summary are reused only when the canonical
  path, `(st_ino, st_size, st_mtime_ns)`, SHA-256 of the bytes read on
  this call, and the detector context all match. This is the
  safety-verdict cache key. Every record is still read and hashed on every
  call. Active records are always decoded afresh. The summary is reused
  only before the next lifecycle deadline, and never across a quarantine
  or restoration: the quarantine listings and per-repository quarantine
  generations are part of its key, and an unlistable quarantine directory
  forbids reuse. When a change forces it to be
  reassembled, approval actionability is re-evaluated only for the
  changed plans. A plan that may own a quarantined task file is always
  re-evaluated.
- **Discarded, never trusted.** Every entry is dropped on
  `deep_storage_audit=true`, on `deep_audit_plan_store`, and on any
  storage, governance or unexpected error. Approval, apply and rollback
  read persisted records exactly as before.
- **Startup historical validation.** After strict startup recovery, the
  same scan runs in the background. It fills the verdict cache and the
  reuse entries. It never persists a lifecycle transition: a plan whose
  lifecycle would persist is left to the next health call. Like every
  scan, it still quarantines a corrupt record it meets, and it reports
  that truthfully: `writes` is `corrupt_record_quarantine` and
  `quarantined_records` gives the plan and task counts, otherwise
  `writes` is `none`. `lifecycle_writes` is always `none`.
- **Truthful while in progress.** A health call waits at most 3 s for
  validation. If validation is still running, governance answers:
  - `status: "pending"`;
  - `pending_reason: "historical_validation_in_progress"`;
  - every history-derived field `null` and listed in `pending_fields`.

  Only process-live fields and build-time constants are filled in. A deep
  audit always waits and then rescans everything.
- **Counters.** `plan_store_scaling.incremental_health_index` (hits,
  misses, invalidations, discards, last run, totals) and
  `governance.historical_validation` (state, elapsed, counts, failure
  category, `writes`, `lifecycle_writes`, `quarantined_records`).

## Measured

Source: [`docs/evidence/hamcp097-health-benchmark/`](evidence/hamcp097-health-benchmark/).
It times the governance component, `service.async_health_summary`, on
synthetic offline stores on one loaded, shared host. It is not end-to-end
MCP latency, not CI evidence and not a production measurement. Load
differed between fixtures, so compare before and after within a fixture.

Fixtures:
- **1x**: 501 plans, 424 tasks.
- **2x**: 1,001 plans, 847 tasks.
- **x4cap**: 1,226 plans, 1,024 tasks. That is 2.45 times the 1x plan
  count, not a full 4x store: the real writers stop at the F3 bound of
  1,024 tasks.

Results (ms; median of 10 warm calls, 2 runs otherwise):

| call | 1x | 2x | x4cap |
|---|---|---|---|
| warm, no change, before (beta.51 source) | 2,451 | 4,145 | 4,980 |
| warm, no change, after | 698 | 1,262 | 1,398 |
| first call after start, with startup validation | 3,010 (pending) | 3,007 (pending) | 3,007 (pending) |
| startup validation, launch to complete | 4,745 and 5,246 | 7,537 and 7,749 | 9,034 and 9,509 |
| first call after validation completes | 809 and 828 | 1,208 and 1,285 | 1,503 and 1,521 |
| first call without startup validation, before / after | 5,484 / 5,442 | 8,697 / 8,809 | 10,326 / 10,324 |
| first call after 5 terminal plans change (one run) | 714 | not measured | 1,739 |

- Median warm calls are within 1.5 s at every fixture (the slowest
  single x4cap warm call was 1,674 ms), but they grow with history: the overlay (469, 868, 955 ms) and the snapshot (185, 317,
  364 ms) are paid on every call.
- At x4cap, the first call after a change (1,739 ms: overlay 974,
  snapshot 353, assembly 287) and the first call after validation
  completes (1,503 and 1,521 ms) are above the 1.5 s warm SLO. This is the
  accepted exception; HAMCP-259 tracks it.
- With startup validation, the first call answers `pending` at the 3 s
  wait budget at every fixture. Without it, a cold call costs what it
  cost before.

## Not changed

- The validation itself: the same detector, the same projection checks
  and the same full scan for `deep_storage_audit=true`.
- Authorization sources, approval binding, locks, dispatch, verification
  and rollback.
- Stored records, retention and the health field set. Once validation is
  complete, every field equals the full projection.
- Pre-existing behavior: a health scan that meets a corrupt record
  quarantines it and refuses with `CHANGE_PLAN_STORAGE_ERROR`; the next
  call reports the corruption.

## Rollback

Reinstall 2.4.1-beta.51. Health returns to full per-call projection. The
added fields disappear, and nothing persisted needs migration.
