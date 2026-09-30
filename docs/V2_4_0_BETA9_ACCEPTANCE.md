# Engineering 2.4.0-beta.9 acceptance

Bounded cold governance-health responsiveness correction. Published beta.8 base:
`e4d59bb1a5e41998c64cc9b73997a5cc845579f4`. Independently accepted implementation:
`8701a118936b59d4e26cb10b330c20a05d65f98f`. Bind final validation and release-delta
review to the exact candidate, publication to its protected merge/artifact, and
installed evidence to the running image. This contract defines gates; it does
not authorize publication, deployment, live access, plans, approvals or writes.

## 1. Final source, review and CI

Require all three authoritative declarations to equal `2.4.0-beta.9`, consumed
`.release/next-version`, and exact active acceptance/release-note resolution from
`scripts/codex-context.py`. Preserve the implementation review and verify its
seven source/test/performance-document files are unchanged during materialization.
Obtain a focused independent review of the release-only delta. No Alarmo,
authority-policy, provider, schema, stored-format or workflow expansion belongs
in this release.

Retain the original implementation/release reviews and separately review the
post-review race-attribution, abandoned-job isolation and numeric phase-diagnostic
delta. Its final-head validation supersedes earlier validation for readiness;
earlier receipts remain historical evidence, not tests of the corrected head.

Run clean final-head Full/Evidence with the existing locked interpreter and all
exact protected-path declarations. Require every gate, including metadata, to
pass. The implementation-only 14/15 receipt is not final release validation.
Record base/head, counts/skips and environment restrictions. The focused modules
below must pass; exact-head full-discovery results may satisfy this requirement
without duplicating execution, explicitly labeled as such.

```sh
python -m unittest -v \
  tests.test_governance_async_health \
  tests.test_governance_health_cache \
  tests.test_beta34_historical_policy_projection \
  tests.test_beta26_plan_store_scaling \
  tests.test_beta26_expiry_lifecycle \
  tests.test_beta11_restart_reconciliation \
  tests.test_f2_policy_approval \
  tests.test_f3_runtime_integration \
  tests.test_ha_core_capability_auto_readmission \
  tests.test_core_semantic_continuity
```

| Case | Required source proof |
| --- | --- |
| Useful cold and warm behavior | Current-writer plan/approval/task changes invalidate the aggregate; the next build reflects them, then unchanged repeats reuse it with zero terminal-plan deserialization. Synthetic approved execution/readback succeeds, and duplicate apply does not redispatch. |
| Event-loop progress | Both a controlled cold safety scan and the actual CPU scanner on current-writer synthetic records allow an independent owner-loop coroutine to progress. Same detector and bounded one-in-flight worker; no repository/provider/audit access from the worker. Do not substitute a wall-time threshold for this proof. |
| Races and freshness | Concurrent external owner approval survives. Local/external record replacement and task-generation changes refuse mixed snapshots with `health_snapshot_superseded`, distinct from true storage errors; later ordinary reads can rebuild. Crossed deadlines, rollback of the clock and owner-thread once-only expiry remain correct. |
| Cancellation and overlap | Concurrent readers serialize. Cancellation during worker/projection/drain cannot publish stale health, perform background lifecycle writes or queue unlimited jobs. An abandoned unexpected exception cannot poison a fresh reader; fresh-validation failures still propagate. |
| Failure and compatibility | Unsafe/corrupt records, failed history, provider/F3 unavailability and live locks remain visible. Current Core/provider state is sampled after the await. Historical projection compatibility has no authorization effect. No new fallback, retry, dispatch or weakened stale-plan check. |
| Honest measurements | Separate cold/warm work counts, total time, fixed numeric phase wall times and observed owner-loop ticker gaps. Warm skipped phases are zero; sync reads do not inherit async timings. Repository enumeration, per-record projection, final assembly and F3 still have synchronous portions; synthetic fixtures prove no household latency or heartbeat outcome. |

Use disposable synthetic records and permitted local socket wakeups/peers only;
no production stores or endpoints. Require exact-head CI `validate` and every
required family: source, both architecture packaging/input checks, configured
disposable Core contracts, exact-image gateway and exact add-on runtime lanes.
Bind actual PR/base/head and synthetic merge checkout identities. Core lane pins
remain fixtures; retain reviewed applicability for the installed Core version.
No new Core semantic campaign is required for unchanged HA behavior. Leave the
PR draft for Josh's Ready decision; installation is separately authorized.

## 2. Publication, installed image and recovery

After authorized publication, verify protected merge/handoff, source/tree/version,
OCI index, both architecture manifests/configurations, provenance, SBOM and locked
build inputs with the shipped verifiers. Bind the actual running configuration
digest through its platform manifest to that index using the reviewed operator
route where MCP cannot observe it. Version/source labels alone are insufficient.
Derive final source/build/digests from publication; do not invent them in advance.

Before deployment, identify a verified beta.8 recovery artifact and preserve
current durable state through the approved operator procedure. No new format or
namespace is added. Preserve beta.7 supplementary receipts and beta.6 expanded
lock records; beta.5 cannot read token unions above sixteen. Restoring old execution
history over later dispatch records is not rollback. Preserve the accepted F027
missing-original-prestate qualification; a new capture cannot reconstruct it.
Backup, restart, rollback and deployment each retain their approval boundaries.

## 3. Bounded installed identity and continuity

After authorized deployment and read-only acceptance authorization:

1. Read `server_info(check_ha=false)` and `get_server_health(check_ha=false)`.
   Bind beta.9, source/build/clean flag to the installed-image receipt. Record
   cache work/counts, plan/task generations, authority generation/retirements,
   connections, request IDs and timings. A stale connector label is not itself
   a runtime defect; require actual descriptor agreement.
2. Use at most one ordinary bounded connectivity probe if needed. Require
   REST/WebSocket agreement, exact ha-mcp 8.5.0 admission, current/cache-valid
   accepted signed registry and all 19 applicable Core profiles. Record the
   observed Core version and registry sequence. No added installed-Core pin;
   absent, expired, revoked or inapplicable authority cannot pass. Immediate
   readmission alone does not establish uninterrupted authority lifetime.
3. Record governance/task/audit/F3/receipt health, pending approvals/challenges,
   execution/dispatch/event counts, leases, active work, locks, holds and recovery.
   Account for concurrent authorized work instead of assuming the interval idle.
4. Perform one useful native and one admitted delegated read of an existing
   freshly confirmed harmless fixture. Preserve provider/completeness/truncation
   and fallback facts. Read one successful historical plan/task pair for exact
   outcome and dispatch-history continuity. Do not repeat completed beta.7/F028
   supplementary verification, including replay. Inspect dependency state only
   if needed; do not force refresh or restart earlier acceptance campaigns.
5. Capture a fresh public Nabu Casa raw catalog in a separate session:
   initialize, initialized notification, every tools/list page, zero tools/call.
   Require all 80 exact descriptors (55 static + 25 delegated), bounded capture
   and contemporaneous beta.9 no-probe identity/health brackets in separate
   sessions. Keep endpoint/token input private. Internal counts or refreshed
   connector schemas do not substitute for raw enumeration. Preserve failed or
   drifting captures without automatic retry.
6. Retain an owner render-only observation of the existing approval panel;
   create no approval for that check. Configuration is not proof of rendering.

## 4. Required governance-change cold-rebuild gate

This is additional to warm-cache continuity. Warm-only acceptance cannot close
the F032 boundary. Prepare the exact live test and obtain its separate approval
before any proposal/approval transition. Prefer an already intended, freshly
inspected bounded operation; alternatively use an exact proposal-only fixture
with explicit expiry/cleanup. Do not clear caches, edit stores, manufacture
history, restart/reload services or replay any failed/stale household plan.
Internal synchronous operational snapshots are outside this async-health gate.

1. Read one warm no-probe health baseline. Record per-call history work plus
   cache hits/rebuilds, generations, authority/connection/provider and live F3
   state. Identify active concurrent work.
2. Observe one separately approved supported governance change. To close the
   approval-to-dispatch blocker, that change must include a **fresh external
   owner approval** bound to the intended plan and current authority, after a
   health baseline for the pre-approval state. Plan creation alone is useful
   partial cold-path evidence, not approval-interval acceptance.
3. Read cold health once, then an unchanged warm health once. Require a rebuild
   that reflects the new approved state and a subsequent cache hit with stable
   plan/task generations and zero terminal-plan deserialization, unless an
   explicitly explained concurrent lifecycle event intervened. Retain actual
   total cold timing; it need not be shorter to establish responsiveness.
   Retain `hot_paths.governance_health.phase_elapsed_ms` for both reads. Compare
   snapshot, worker/validation wait, projection, assembly and overlay durations;
   worker elapsed is nested in validation wait, and projection includes yields.
   These are wall times, not owner-loop CPU or a heartbeat-latency bound. Retain
   the real-scanner synthetic ticker evidence separately from installed evidence.
4. Across the baseline, approval, cold/warm reads and useful readback, require
   uninterrupted admission: no authority generation change/retirement, observed
   disconnect/provider_unavailable, unexplained catalog withdrawal or fallback.
   Healthy live F3/locks/recovery must remain visible. Retain available bounded
   heartbeat/connection evidence; missing transport detail remains a limitation,
   and stable sampled authority is not proof of every heartbeat.
5. Verify the fresh plan's fingerprint/provider/authority binding and external
   approval remain valid. Execute only if Josh separately authorized that exact
   operation. Require one dispatch and exact readback under its existing contract;
   any restoration needs its own bound proposal and approval. Without an apply,
   report approval-interval continuity only, not dispatch success. F032/F035
   remain separately governed household tasks, never implicit release canaries.

Stop dependent work on authority drift, provider loss, mixed/failed/incomplete
health, unexpected generations or counters, uncertain dispatch or readback
mismatch. Preserve timing/reason evidence; no automatic retry, new approval loop,
authority relaxation or transport-cause inference. An expected concurrent change
can make a comparison inconclusive; it cannot be silently discarded until green.
`health_snapshot_superseded` identifies that race without asserting disk damage;
it does not turn a refused mixed snapshot into a pass or authorize automatic retry.

## 5. Settlement and reporting

End with one no-probe health read and reconcile active executions/tasks,
approvals/challenges, applies/rollbacks, locks/holds, Core leases/commits,
storage/audit errors, recovery failures and fallback. Explain ordinary audit/cache
activity, existing expiry effects and separately authorized work. A final settled
sample does not prove the entire interval was idle.

Report source/CI, publication, installed-image binding, authority, useful/history
reads, catalog, panel, cold/warm behavior, approval-interval continuity, any
separately authorized execution, and settlement independently. Missing mandatory
evidence remains open; proposal-only or warm-only tests cannot close the cold
approval interval. Preserve all predecessor evidence and failed attempts, saving
new private receipts with exact identities, request IDs, timings and a verified
integrity manifest. Source success is not a claim of installed acceptance or
proof that health starvation caused the earlier outages.
