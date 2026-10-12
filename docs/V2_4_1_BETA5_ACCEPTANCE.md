# Engineering 2.4.1-beta.5 acceptance

This contract covers the HAMCP-168 defect A correction (A-1 bounded recovery
cursor deferral during deep health, A-3 rate-limited redacted refusal warning)
on top of beta.4. It distinguishes source/disposable acceptance, release CI,
publication and installed Engineering acceptance. This document authorizes
none of the latter operations.

## 1. Source, review and CI

Bind Josh's authorization (2026-10-12: protected runtime paths
`governance/service.py`, `f3_runtime/runtime.py` and `f3_runtime/health_scan.py`
for A-1 and A-3 only), the base `main`, every later main merge into the branch,
and the final reviewed commit and tree. Earlier review does not cover changed
code.

Materialize 2.4.1-beta.5 in all three version authorities through the existing
staging tool. Require exact acceptance/release-note resolution and no remaining
staged declaration. Complete Full/Evidence on clean source and the bounded
independent review, including a security section covering fence integrity
(no real write may be deferred or unobserved), bounded deferral, and log
redaction. Require exact-head CI before release acceptance. Pending or
unavailable jobs are not passing evidence.

Preserve stable v1.1.2, historical persisted fixtures, durable formats,
locks/holds/uncertainty, production journals, probe fingerprints, the 22 Core
capability profiles, every signed registry entry, dependency locks, build
inputs, workflow permissions and the support window. The public catalog stays
84 tools with every beta.4 descriptor unchanged. The public error contract for
health refusals is unchanged.

Offline source evidence must prove, through the shipped service, F3 runtime
and repositories:

- useful success: a deep-health scan spanning one idle sweep succeeds, and
  three consecutive scans that each span a sweep all succeed;
- an idle sweep with no scan still advances the cursor on every cadence;
- refusal preserved: a real child update, a lock race, lock-only movement,
  in-place child and task file movement, a parent task or plan update and an
  orphan-terminalizing sweep during a scan are each refused as superseded;
- boundedness: under continuous health load the fourth consecutive fenced
  sweep advances the cursor (and supersedes the held scan), and an unfenced
  sweep restores the full bound;
- F3 `ExecutionStorageError` and `LockStorageError` still propagate
  unconverted, with `f3_children` / `f3_locks` warning categories, and the
  F3 import-boundary tests pass;
- the fence-holder count returns to zero after success, refusal and
  cancellation;
- one warning per reason per 60 seconds with exactly the bounded fields, the
  correct provenance-derived source category for each fenced source
  (including a failure of each initial fence read), `unknown` when provenance
  is not established, and no path, identifier or exception text;
- delivery isolation: with the log sink held indefinitely, health returns the
  original refusal, the fence holder clears and the recovery heartbeat keeps
  running; repeated refusals keep at most one worker and a bounded queue with
  counted drops; after release, delivery, the rate limit and exception
  containment work; and
- mutation checks: removing the deferral fails the inverted tests, removing
  the rate limit fails the warning test, awaiting the log sink or removing the
  queue bound fails the delivery tests, and dropping the initial-read category
  fails the attribution test.

## 2. Publication and recovery preparation

Publication needs Josh's applicable Ready authorization and the protected merge
handoff. Verify the merged source, v2.4.1-beta.5, immutable version/commit
images, provenance and SBOM with the shipped verifiers. Never invent future
image identities. Retain the known-good beta.4 and beta.3 artifacts and
options for recovery. Nothing new is persisted, so downgrade to beta.4 needs
no migration.

## 3. Installed Engineering

1. Separately authorize installation of the published Engineering artifact and
   bind it to the published index and protected source.
2. Compare all 84 public descriptors, Core signed selection, storage/audit
   health, F3 readiness and zero fallback.
3. Repeat idle `get_server_health(check_ha=false)` calls whose duration exceeds
   the 30-second recovery cadence. A scan that spans at most three idle
   recovery-cursor opportunities, with no real recovery, child, lock, task or
   plan write during it, must succeed. A longer idle scan is intentionally
   refused when the fourth consecutive fenced sweep advances the cursor; that
   bound is part of the design and is not a defect. Any refusal must be
   accompanied by at most one bounded `governance_health_refused` warning per
   reason per minute (best-effort; a stalled log sink may drop it). Scan
   latency is not changed by this release.

No push, CI trigger, signing, publication or installation is authorized by
source completion.
