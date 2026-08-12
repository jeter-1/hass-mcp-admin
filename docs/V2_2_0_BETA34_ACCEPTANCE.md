# Engineering 2.2.0-beta.34 acceptance

This procedure applies only after independent review, merge, protected
promotion, publication, and separately authorized deployment. Source
validation must not access the deployed Home Assistant environment. Do not
reuse old approvals, mutate historical records, or expose raw authority
material.

## Pre-deployment gates

1. Confirm source declaration, promoted metadata, image labels, tag, and
   accepted commit all identify `2.2.0-beta.34`.
2. Confirm stable v1.1.2 is unchanged and the public tool catalog, task and plan
   schemas, approval authority, provider routing, and zero-fallback policy are
   unchanged.
3. Require the complete suite, Full and Evidence gates, promotion-candidate
   validation, exact-image and immutable add-on lanes, declared architecture
   builds, dependency audit, and disposable Home Assistant 2026.7.2,
   2026.8.0, and 2026.8.1 lanes to pass.
4. Confirm exact ha-mcp 8.0.0, 8.1.0, 8.1.1, and 8.2.0 admission remains
   fail-closed and no fallback is present.

## A — Live automation verification acceptance

The desired garage FP300 guard is already installed by the Beta 33 canary. Do
not remove and re-add it to reproduce the old false mismatch.

1. Confirm `server_info`, image/build provenance, and health identify the exact
   accepted Beta 34 runtime.
2. Confirm the exact Home Assistant Core version and exact ha-mcp admission,
   including protocol, reviewed entry, held tools, and zero fallback.
3. Authoritatively read the current garage automation. Confirm the reviewed
   FP300 guard is present and preserve the complete returned configuration.
   This is a read-only check; do not change the garage automation.
4. Construct a fresh, reversible configuration-only canary on a dedicated
   non-garage automation. Exercise the same automation write/readback
   verification path with a nonempty `wait_template` longer than 200 characters
   and no physical-action service or garage target. Keep the template within
   the documented fixed limit, and retain the exact pre-canary configuration
   for governed cleanup.
5. Review the full exact plan and complete the policy-selected approval class
   through authenticated Ingress. Do not infer approval class from these notes
   or reuse Beta 33 authority.
6. Apply once and require exactly one durable provider dispatch.
7. Require a provider response to be received and recorded as bounded evidence.
8. Authoritatively reread the dedicated canary automation and require its exact
   normalized semantics and hashes to match the approved configuration.
9. Require the F3 task to terminalize `succeeded_verified`, with no approximate
   comparison and no provider response treated as readback authority.
10. Repeat the same apply request only to prove idempotent task reuse and zero
    additional dispatches.
11. Require zero fallback throughout. Restore or remove the dedicated canary
    only through a separate fresh governed plan with the required approval.
12. Do not trigger the garage automation, move the garage door, or physically
    actuate any device merely for acceptance.

Any second dispatch, non-authoritative verification, template truncation,
garage mutation, physical actuation, or terminal result other than
`succeeded_verified` blocks acceptance.

## B — Historical projection acceptance

Perform this section read-only after the authorized Beta 34 restart.

1. Confirm governance storage, indexes, and corruption/projection health are
   healthy.
2. Confirm the two previously failing records project through reviewed
   historical compatibility rather than current-policy authority.
3. Require `projection_failure_count` for those records to fall from 2 to 0.
4. Require their `policy_snapshot_mismatches` count to fall from 2 to 0.
5. Require the historical-compatible count to be exactly 2.
6. Require the Beta 32 profile count to be exactly 2, with the persisted
   contract/lifecycle variant retained as bounded evidence where exposed.
7. Require `authorization_effect=none_projection_only` for both records.
8. Verify neither record is eligible for approval, consumption, task creation,
   apply, recovery, reconciliation into authority, rollback, dispatch, or
   redispatch. No provider write may occur.
9. Where the operational procedure can compare hashes without exposing raw
   authority material, verify each persisted source-record hash is unchanged
   before and after projection.
10. Verify no historical migration, rewrite, normalization writeback, or new
    task occurred.

Any newly actionable historical record, persisted-byte change, provider call,
authority effect other than `none_projection_only`, remaining unexplained
projection mismatch, or exposure of raw approval evidence blocks acceptance.

## C — Home Assistant 2026.8.1 compatibility acceptance

1. Require the immutable disposable Home Assistant 2026.8.0 and 2026.8.1
   lanes to capture the same reviewed raw restored-composite shape for the
   migration fixture: two config entries, two split-device ids, zero raw joined
   entities, and `entity_count=0` before adaptation.
2. Require exact 2026.8.0 to report
   `ha-get-device-composite-ha-2026.8-v1` and exact 2026.8.1 to report
   `ha-get-device-composite-ha-2026.8.1-v1` for that shape. Neither release may
   enter the other release's adapter identity.
3. Require both adapted results to preserve the restored composite device id
   and project exactly the two expected entity identities from the reviewed
   split-device registry relationship.
4. Require the previously failing
   `device_registry_migration_and_analysis / upstream_entity_count` scenario to
   pass unchanged on exact 2026.8.1. Do not alter its required count.
5. Require Home Assistant 2026.7.2 to retain its established no-adapter path,
   and prove an unknown future patch release does not inherit 2026.8.1
   behavior.
6. Require missing, malformed, duplicated, ambiguous, or inconsistent split
   evidence to fail as a response-contract error, with no fallback or new
   provider call.
7. Confirm the exact ha-mcp admission matrix, dynamic delegated-read count,
   public tool catalog, approval and governance authority, F3 dispatch,
   dashboard behavior, configuration writes, and stable v1.1.2 are unchanged.

Any permissive `2026.8.x` match, future-version inheritance, weakened entity
count, silent shape repair, fallback, new provider call, or authority change
blocks acceptance.

## Separate operational follow-up

Protected Supervisor options appeared in a private discovery transcript during
the historical investigation. Do not copy that material into acceptance
evidence. Credential rotation is a separate authorized operational task and is
not part of Beta 34 source acceptance, promotion, or deployment.
