# Beta 31 workstream 2 — ha-mcp 8.2.0 acceptance

This workstream stages exact `ha-mcp` 8.2.0 compatibility independently from
the other Beta 31 workstream. It does not itself publish Beta 31, deploy an
image, change a live add-on, or authorize a dashboard mutation.

## Source and runtime acceptance

Require all of the following at the final exact PR head:

1. The reviewed registry selects only `ha-mcp-v8.2.0-dbcfc0ee` for observed
   server `ha-mcp`, version `8.2.0`, and protocol `2025-03-26`.
2. Exact standalone and add-on OCI indexes/manifests match the review record.
3. Every exact-image capture byte-matches the committed 78-tool fixture.
4. Tool accounting is 25 automatic reads, one held tool
   (`ha_get_operation_status`), and 52 nondelegated tools, with zero missing,
   duplicate, unreviewed, quarantined automatic, or fallback tools.
5. `ha_manage_hacs` remains nondelegated `persistent_write` after its
   `update_information` action is reviewed.
6. `ha_manage_security_policy` is absent from the default runtime catalog and
   has no generic or explicit Engineering route.
7. Exact add-on runtime acceptance passes for amd64 and arm64 identities.
8. Engineering no-push builds pass for every declared Engineering architecture.
9. Disposable Home Assistant 2026.7.2 and 2026.8.0 lanes pass against exact
   8.2.0. Any support-matrix change requires a separate reviewed decision.

## Dashboard acceptance

Require source tests and the refusing-fixture exact-image probe to prove:

- existing `map`: resolver accepts the update target;
- existing `compatibility-fixture`: resolver accepts the hyphenated target;
- new `newdashboard`: resolver rejects before any mutation boundary;
- new `new-dashboard`: resolver reaches supported creation handling;
- the synthetic fixture accepts no mutation;
- exact 8.1.1 still rejects an existing hyphenless target before planning;
- exact 8.2.0 permits a fresh plan for an exact complete `map` preread;
- external approval, one-dispatch maximum, readback-only recovery, no fallback,
  and exact reread verification remain enforced.

The original Beta 28 `map` plan and every earlier canary plan are historical
evidence only. They are never valid inputs to this acceptance.

## Post-deployment canary procedure (not authorized by this PR)

After a separately authorized Beta 31 deployment:

1. Verify live Engineering identity and exact 8.2.0 admission, including the
   complete tool-accounting health record and zero fallback.
2. Read `map` fresh through the exact raw dashboard reader and retain its current
   upstream `config_hash` and Engineering SHA-256 evidence hash.
3. Create a new harmless governed `map` plan bound to that exact preread. Do not
   reuse Beta 28 or any prior plan.
4. Obtain a new external administrator approval for that exact plan.
5. Apply once. Require one upstream setter dispatch maximum and terminal
   `succeeded_verified`.
6. Independently reread the complete `map` configuration and require an exact
   match with the approved full result, including untouched fields.
7. Create fresh plans for a deliberate stale-state rejection canary. Require
   rejection before setter dispatch and do not rebase either plan.
8. If canary-only text requires cleanup, use another fresh exact preread, a new
   governed plan, and new external approval. Never treat cleanup as rollback.

Stop on identity drift, partial/sanitized reads, a stale hash, missing approval,
more than one dispatch, fallback, ambiguous readback, or any result other than
the explicitly expected terminal state.

## Required gates

- focused 8.2.0 admission and dashboard regressions;
- all F3 and upstream registry tests;
- Fast, Full, and clean exact-head Evidence;
- registry regeneration twice with byte-identical clean output;
- dependency, secret, protected-path, architecture, and stable-v1 checks;
- exact-image read-gateway and readmission lanes;
- exact add-on amd64/arm64 runtime lanes;
- disposable Home Assistant 2026.7.2 and 2026.8.0 lanes;
- GitHub exact-head CI green before this draft is considered mergeable.
