# 2.2.0-beta.2 acceptance contract

Version: `2.2.0-beta.2`

Source baseline:
`4743d4f0aed1442b764693b925dfbaf03b7f2fd8`

This is the source and later operator-controlled runtime acceptance contract
for the F1 recovery-evidence and counter correction. Source review and CI must
not access a live Home Assistant instance, approve a live plan, dispatch an
operation, publish an image, merge, or deploy.

## Immutable boundaries

- Task schema version 1, plan hashes, approval records, provider arguments,
  routing, compatibility entries, dashboard trust, and zero fallback are
  unchanged.
- Expected catalog: 48 Engineering plus 26 delegated, 74 total.
- Stable v1.1.2 is unchanged.
- C1, E1, K1, F2, and other development lanes are absent.

## Provider-response acceptance

Prove a lost-response Engineering self-restart retains one provider attempt
with `response_received=false`, no `response_recorded_at`, and no
`provider_response_recorded` event before and after startup rehydration.
Process-identity and exact readback may complete the same task as
`succeeded_verified`; they must not rewrite response history or invoke the
provider again.

Protect the normal path: a controlled reload and full backup whose provider
call returns must each contain one received provider attempt and exactly one
`provider_response_recorded` event. Duplicate apply must add neither another
response event nor another dispatch.

## Counter acceptance

For a successful full backup followed by one exact duplicate apply, require:

- `apply_attempts=2`;
- `dispatch_attempts=1`;
- `dispatch_successes=1`;
- `verified_successes=1`; and
- `no_blind_redispatch_preventions=1`.

The global durable-task prevention count must increase once. Cancellation
refusal, health recomputation, and task rehydration must not increment apply,
dispatch, or prevention counts. Repeat equivalent assertions for controlled
reload, add-on restart, and Home Assistant restart.

## Validation

Run focused execution-task, operational lifecycle, backup, approval,
observability, catalog, and security tests followed by the complete Evidence
tier. CI must pass disposable Home Assistant contracts, exact-image 7.14.1 and
7.14.2 lanes, stable and Engineering builds, and amd64, arm64, and arm/v7
no-push builds.

No failing test may be skipped, weakened, or converted to an expected failure.

## Later runtime acceptance

Do not execute during source implementation.

1. Verify exact version/build, 48+26=74 tools, reviewed upstream admission, task
   storage health, audit health, and zero fallback.
2. Exercise an authorized Engineering self-restart with one dispatch and
   inspect whether the original provider response was actually received.
3. If response receipt was lost, require successful process-identity/readback
   verification while task and plan response evidence remain false.
4. Apply an authorized harmless operation once, repeat the exact apply, and
   confirm two apply attempts, one dispatch, one prevention, and stable counts
   after health reread and Engineering restart.

Rollback to the accepted `2.2.0-beta.1` artifact preserves `/data` and task
schema v1. Beta 1 may retain the two reporting defects corrected here; rollback
does not authorize redispatch or historical evidence rewriting.
