# Engineering 2.4.1-beta.52 release notes

Engineering 2.4.1-beta.52 makes `get_server_health(check_ha=false)` meet
Josh's latency SLO (HAMCP-097): warm ≤ 1.5 s, and the first call after
start ≤ 5 s or within 5 s with explicit in-progress markers. Health cost
now scales with changed records and active work, not retained history. It
builds on 2.4.1-beta.51 (HAMCP-252, #331). It adds no tool, input schema,
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
  only before the next lifecycle deadline. When a change forces it to be
  reassembled, approval actionability is re-evaluated only for the
  changed plans. A plan that may own a quarantined task file is always
  re-evaluated.
- **Discarded, never trusted.** Every entry is dropped on
  `deep_storage_audit=true`, on `deep_audit_plan_store`, and on any
  storage, governance or unexpected error. Approval, apply and rollback
  read persisted records exactly as before.
- **Startup historical validation.** After strict startup recovery, the
  same scan runs in the background. It fills the verdict cache and the
  reuse entries and writes nothing. A plan whose lifecycle would persist
  is left to the next health call.
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
  category).

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
