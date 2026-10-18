# Engineering 2.4.1-beta.17 acceptance

This contract covers HAMCP-171: the health safety verdict cache (D1), the
per-job scan memo (D5) and the recovery sweep's parent reads. It builds on
beta.16 (HAMCP-162), beta.15 (HAMCP-189 PR B + C) and beta.14 (HAMCP-189
PR A).

It separates four stages:
1. source and disposable acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

This document authorizes none of the later stages.

## 1. Source, review and CI

Bind the following:
- the investigation `docs/HAMCP_171_HEALTH_PERFORMANCE_INVESTIGATION.md`
  (PR #276, merged at `7e223a0`);
- Josh's 2026-10-18 decisions D1–D6 on HAMCP-171;
- the exact rule in `docs/HEALTH_PERFORMANCE.md` ("Health safety verdict
  cache (HAMCP-171)");
- the original base `main` (2.4.1-beta.16 at `119be1b`), every later `main`
  merge into the branch, and the final reviewed commit and tree.

Earlier review does not cover changed code.

Materialize 2.4.1-beta.17 in all three version authorities through the
existing staging tool. Require exact acceptance and release-note resolution,
with no staged declaration left over. Complete Full/Evidence on clean source
and the bounded independent review.

The review needs a security section covering:
- fail-closed health: every call still reads, parses and fence-checks every
  record, and validates every record without a matching verdict; tampered,
  corrupt and partially written records are detected with and without the
  cache and on a deep re-scan;
- the cache key: canonical filename, `st_ino`, `st_size`, `st_mtime_ns` from
  `fstat` of the parsed handle (size equal to the bytes read), SHA-256 of
  the parsed bytes and the detector context;
- invalidation: a whole-cache discard on any failed scan, storage error,
  quarantine, unexpected worker failure (including an abandoned job's drained
  failure) and the internal `deep_audit_plan_store`; verdicts recorded only
  after the worker validates the whole pending set; nothing recorded by a
  cancelled call;
- process memory only: nothing persisted, empty at start, and no
  authoritative read, save, approval or execution path consulting the cache;
- bounded reporting with no digest, path or key material;
- the one public schema change: `get_server_health` gains the optional
  boolean `deep_storage_audit` (default `false`). Every other descriptor is
  unchanged and the catalog stays at 88. The three F2 snapshot pins are
  updated for that descriptor only;
- the sweep parent-read rule, with at-most-once dispatch, no blind
  redispatch, HAMCP-189 local-first expiry and durable cancel unchanged;
- zero new write, reload, restart, deletion, service-call, fallback or
  forwarding reachability.

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Preserve the following:
- stable v1.1.2;
- historical persisted fixtures and durable formats (D6: no stored-format
  change);
- probe fingerprints, the Core capability profiles, every owner-signed Core
  registry entry and the signed ha-mcp registry;
- the 8.6.0 HOLD and the support window;
- the reviewed policy JSON and release registry JSON;
- dependency locks, build inputs, pinned runners and workflow permissions.

### Offline source evidence

Run these against the exact released tree under Python 3.12 (mcp 1.30.0):
- `tests/test_hamcp171_health_verdict_cache.py`;
- `tests/test_hamcp171_sweep_parent_reads.py`;
- `tests/test_governance_async_health.py`;
- `tests/test_f3_orphan_child_recovery.py`.

They must prove each of the following:
- hits on unchanged records, with truthful counters;
- misses on a same-size, same-mtime content change (the hash catches it),
  and on inode, size and mtime changes, a new record, a deleted record
  (pruned), a stat failure and a detector-context change;
- tamper, corruption and partial writes detected with and without the
  cache;
- the deep re-scan bypasses and repopulates the cache, through the public
  tool as well as the service;
- whole-cache invalidation on worker failure, on an abandoned job's
  failure and on the internal deep audit, and no verdicts from a cancelled
  call;
- restart starts empty, and nothing is persisted;
- memo equivalence, its refusal of mismatched secrets, and a fresh memo per
  job;
- no parent read for terminal dispatched children, while orphan cleanup and
  terminal fault projection still read the parent and converge;
- a corrupt parent the sweep does not read is still surfaced by health, with
  no gateway call;
- the owner loop stays responsive while the real CPU scanner runs.

Run `docs/evidence/hamcp171-mutations/run_mutations.py`. Every mutation must
fail at least one test, including the brief's three:
- the cache ignores the content hash;
- the cache survives a record change;
- the sweep skips a needed parent read.

### Measured effect

These are offline numbers from `docs/evidence/hamcp171-measurements/` (a
synthetic store; see its README), not production. Both trees ran back to
back on one host. The store has 435 plans, 240 of them applied, with a mean
plan file of 37,024 bytes.

| Health call | Wall | Validation wait | Verdicts |
| --- | --- | --- | --- |
| beta.16 base, three calls | 23.0 / 23.1 / 24.0 s | 21.6 / 21.7 / 22.5 s | none (`health_cache_hit_count` 0) |
| head, first call (cold, memo only) | 5.5 s | 4.0 s | 0 hits / 435 misses |
| head, second and third calls | 1.54 / 1.55 s | 1.5 ms | 435 hits / 0 misses |
| head, `deep_storage_audit=true` | 5.6 s | 4.0 s | `deep_rescan`, 0 / 435 |
| head, next plain call | 1.57 s | 1.9 ms | 435 / 0 |

`health_cache_hit_count` went 0, 1, 2, 2, 3 across the five head calls. The
other phases (snapshot about 0.45 s, projection 0.23 s, assembly 0.55 s and
overlay 0.21 s) are unchanged.

**Event-loop responsiveness during a health call.** A 1-byte loopback echo
client and a 1 ms ticker ran on the owner loop during each call. When idle,
the echo round trip had a median of 0.03 ms and a p99 of 0.07 ms.

| During the call | Echo median | Echo p99 | Echo throughput | Longest loop gap |
| --- | --- | --- | --- | --- |
| beta.16 base | 0.16–0.18 ms | 20.6–22.7 ms | about 175/s for 23 s | 0.51–0.58 s |
| head, cold or deep | 0.05–0.07 ms | 15.6–20.5 ms | about 490/s for 5.5 s | 0.53–0.59 s |
| head, warm | 0.035–0.042 ms | 2.7–2.8 ms | about 1,570/s for 1.5 s | 0.56–0.58 s |

Other requests stay responsive. The 22 s window of GIL contention from the
validation worker shrinks to about 4 s on a cold or deep call and to none on
a warm call. The longest single stall is unchanged at about 0.5 s. It comes
from the existing synchronous snapshot and assembly phases on the loop, not
from the worker, and this release does not change those phases.

**Recovery sweep (five periodic passes).**

| | Parent `task_repository.get` per pass | Pass time and loop stall |
| --- | --- | --- |
| beta.16 base | 240, 239, 239, 239, 239 | 0.205–0.245 s |
| head | 0, 0, 0, 0, 0 | 0.122–0.124 s |

**Projected production effect.** Live beta.16 took 36.5 s, of which 33.8 s
was validation wait. Validation is about 1.54 times slower live than on this
store (33.8 s against 21.9 s); the other phases take about 2.7 s live. The
projection is:
- the first health call after a restart, and every deep re-scan: about
  2.7 s + 1.54 × 4.0 s ≈ 9 s;
- following calls with unchanged records: about 3 s, plus fresh validation
  of only the records that changed;
- the sweep's 239 parent reads per 30 s (about 8 records per second, most
  of the live 7/s task deserialization rate) fall to zero for terminal,
  dispatched, fault-free children.

Health itself still deserializes every task once per call, so subtract
those reads in the live task-read check.

## 2. Publication and recovery preparation

Publication needs Josh's applicable Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.17;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.16, beta.15 and beta.14
artifacts and the known-good beta.12 and beta.11 artifacts and options for
recovery.

No new persisted format is introduced, and beta.16 reads every beta.17
record.

## 3. Installed Engineering

All installed checks below are read-only unless a step names Josh's explicit
authorization. Record timings, counters and modes only, never record
content.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to the published
   image index digest, the v2.4.1-beta.17 tag and the protected `main` merge
   commit. A source version alone does not prove the installed image.
2. **Live catalog 88.** Enumerate the live catalog with a fresh protocol
   enumeration: exactly 88 tools (59 native, 29 delegated).
   `get_server_health` must show the reviewed schema with the optional
   `deep_storage_audit` boolean. Every other descriptor must match beta.16.
   Also compare Core signed selection, storage and audit health, F3
   readiness and zero fallback.
3. **Two consecutive health calls.** After the add-on has started, call
   `get_server_health(check_ha=false)` twice in a row. Record each call's
   wall time and `governance.plan_store_scaling.safety_verdict_cache`:
   - the first call: `last_run.mode: cached`; misses up to the plan count
     (all records validated fresh after a restart);
   - the second call: `last_run.mode: cached`, `hits > 0` (close to the
     plan count), few or no misses, and much faster than the first (the
     beta.16 baseline is 36.5 s with 33.8 s of validation wait);
   - `health_cache_hit_count` increases by one on the second call;
   - `scope: process_memory_only` and `persisted: false`;
   - storage health stays healthy, with the same plan and task counts on
     both calls.
4. **Deep re-scan.** Call `get_server_health(check_ha=false,
   deep_storage_audit=true)`. It must report `last_run.mode: deep_rescan`,
   `hits: 0`, misses equal to the plan count and `totals.deep_rescans`
   increased by one, at full validation time (comparable to the first call
   in step 3). A following plain call must again report hits.
5. **Task-read rate.** Record
   `governance.plan_store_scaling.task_navigation.records_deserialized` (and
   `terminal_records_deserialized`) from `get_server_health(check_ha=false)`,
   wait about 10 minutes with no other client activity, and record them
   again. The rate must drop sharply from the beta.16 baseline of about 7
   task records per second, which came from the 30 s sweep's parent reads.
   Subtract the reads made by the two health calls themselves.
6. **Recovery and canaries unchanged.**
   - F3 readiness reports no new recovery fault and no orphaned child.
   - Repeat the beta.16 read-only checks: one delegated read, one native read
     and `get_core_log_history`, each exactly as on beta.16.
   - Power, fan, dashboard and entity-metadata capabilities must report the
     same admission as beta.16.
   - With Josh's explicit authorization only, repeat the beta.6 power canary
     and the beta.8 dashboard canary on the entity and test dashboard Josh
     chooses. The outcomes must match beta.16.

   No published tool may return `CoreRequirementUnmapped`. Any occurrence is
   a stop condition.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
