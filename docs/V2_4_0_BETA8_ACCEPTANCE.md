# Engineering 2.4.0-beta.8 acceptance

First bounded governance health-cache correction. Published beta.7 base:
`981fc82be5e8660dd97f58b1cea378d1e7e3d97f`. Independently accepted implementation:
`7da7b7c0e1b70b6c5f538e57f565c2b6bfe54dba`, including the HC-R1 task-generation
correction. Bind release validation/review to the final candidate, publication to
the protected merge/artifact, and installed evidence to the actual running image.
This document defines gates; it does not assert that any have passed or authorize
publication, deployment, restarts, live calls or household changes.

## 1. Source, review and CI

Require all three authoritative versions to equal `2.4.0-beta.8`, consumed
`.release/next-version`, and exact active acceptance/release-note resolution from
`scripts/codex-context.py`. Preserve the accepted implementation review and HC-R1
correction disposition; independently review the release-only delta. Verify that
the accepted health implementation/tests remain unchanged by materialization.
Exclude Alarmo, new tools, authority-policy changes and unrelated features.

Run clean final-head Full/Evidence with the existing locked interpreter and exact
protected-path declarations. Require all gates, including metadata, to pass.
The earlier implementation-only 14/15 receipt does not close this release gate.
Record base/head, commands, counts/skips and environment limitations. The focused
modules below must pass; their results within exact-head full discovery may be
retained instead of a duplicate command, explicitly identified as full-discovery
results. A private disk-backed TMPDIR outside the checkout avoids invalidating
the packaging import-isolation test. Existing synthetic loopback peers require
platform permission; no live endpoint or production credential belongs in tests.

```sh
python -m unittest -v \
  tests.test_governance_health_cache \
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
| Useful cache reuse | A genuine current-writer plan/task change causes a rebuild; two unchanged repeats reuse it with zero terminal plan deserializations. Synthetic approved execution/readback succeeds; duplicate apply does not redispatch. |
| Generation binding | Same-repository and supported external saves, including a real task event appended between list and health metadata, never leave an old aggregate tagged current. Reject an observed mixed build with the existing storage error; the next successful read reflects persisted events and can be reused. |
| Failure visibility | Corruption, failed plan/task history, provider/F3 failure and projection-only index rebuild do not mask failures with stale healthy counts. A failed history aggregate is not reusable. |
| Time and live state | Plan/challenge/elevated-ack expiry, restart backoff/deadlines, clock rollback, provider presence, locks and active work remain truthful without unrelated writes. Existing expiry events remain once-only; provider/F3 health runs once per public assembly. |
| Isolation and limits | Returned dictionaries cannot change cached state. No cache grants approval, dispatch or recovery authority; no added network/provider action, fallback, schema or persistent-format change. |
| Work reduction | Use current-writer synthetic history and identical instrumentation for baseline/candidate. Report cold and warm plan/task work separately; retain nonzero F3 work and synchronous cold costs. Timing is observational, not a correctness threshold. |

Require exact-head CI aggregate `validate` and every required family, including
the existing pinned Core 2026.9.3 / ha-mcp 8.5.0 disposable lane, both architecture
build/input checks, exact-image gateway and add-on runtime lanes. Bind actual
checkout/merge-tree identities to the final PR head and base. Those lanes prove
their declared scenarios, not a household benchmark or an installed-Core version
requirement. Retain the separately reviewed exact-release applicability evidence
for the Core version observed during installed acceptance; a CI fixture version
does not grant compatibility to another release. No fresh Core semantic campaign
is added for unchanged HA behavior with already reviewed applicability.
Leave delivery draft for Josh's Ready decision; deployment remains separately
authorized.

## 2. Publication, installation and recovery identity

After separately authorized publication, verify the protected merge/handoff,
exact source/tree/version, OCI index and both architecture manifests/configurations,
provenance, SBOM and locked build inputs with the shipped verifiers. Bind the
actual running container configuration through its platform manifest to that
verified index using a reviewed, bounded operator receipt when MCP cannot observe
it. Matching version/build labels alone are insufficient. Do not guess digests
or call cached connector inventory a raw catalog capture.

Before authorized deployment, identify a verified beta.7 recovery artifact and
preserve current durable state under the existing operator recovery procedure.
This release adds no format migration or persistent namespace; its cache is
process-local. Keep beta.7 supplementary receipts and beta.6 expanded-lock state.
Beta.5 cannot read expanded token records above sixteen; binary downgrade alone
or restoring older execution records over newer dispatch history is not recovery.
Preserve the accepted F027 missing-original-prestate qualification; later captures
cannot repair it. Any backup, rollback or restart needs its own concrete approval.

## 3. Bounded installed identity and useful reads

After authorized deployment and read-only acceptance authorization:

1. Read `server_info(check_ha=false)` and health. Match beta.8, the protected source,
   build time and clean flag to the installed-image receipt. An old connector label
   alone is not a runtime defect; refresh schemas only if their actual content is
   stale. Existing public inputs/descriptors must remain unchanged.
2. Use at most one ordinary bounded connectivity probe if current evidence needs
   it. Require REST/WebSocket agreement on the actual installed Core version,
   exactly admitted ha-mcp 8.5.0, and accepted/current/cache-valid signed Core
   authority selecting the existing reviewed contracts for that exact version.
   Require all 19 Core profiles compatible, with no unexplained withholding,
   fallback or authority drift. Record the observed Core version, registry
   sequence and authority state in the receipt. Beta.8 adds no separate
   installed-Core version pin: an absent, expired, revoked or inapplicable entry
   cannot pass this gate, and a version-family match cannot substitute for exact
   admission. A later compatible Core update can use separately reviewed signed
   registry data without an Engineering release; changed contracts still require
   the applicable implementation and compatibility review.
3. Record storage/audit/receipt/F3 health, plan/task/event/approval/dispatch counts,
   active work, leases, locks and holds. Identify separately approved concurrent
   activity; a final settled sample does not prove an idle interval.
4. Perform one useful native read and one admitted delegated read of an existing,
   freshly confirmed harmless fixture. Retain provider, completeness, truncation
   and fallback facts. Read one already successful historical plan/task pair for
   status and dispatch-history continuity. Do not call
   `reverify_configuration_task` again, including replay, to repeat beta.7/F028.
5. Inspect dependency state without forced refresh. Allow normal prewarm; at most
   one justified bounded `refresh_index=false` request. Preserve partial coverage.

Retain a fresh separate public `initialize` -> `notifications/initialized` ->
all paginated `tools/list` session with **zero `tools/call`**, plus contemporaneous
runtime brackets from separate sessions. Compare all **80 = 55 static + 25
delegated** descriptors against exact beta.8 source; existing descriptors remain
the beta.7 contracts. Use reviewed release-specific collectors, hidden private
endpoint/token input, finite limits, no retry/fallback and preserved failed
captures. Obtain one timestamped owner render-only observation of the existing
approval panel; do not create or approve anything. Counts/backend configuration
alone do not establish catalog/render gates.

## 4. Installed cache-work measurement

Make one bounded sequence of **three serial** `get_server_health(check_ha=false)`
calls through the existing Engineering connector. The first observed call may
already be warm because startup, another client or an earlier gate populated the
cache; do not call it cold without evidence. Never clear caches, force an index
rebuild, manufacture plans/events, change clocks, restart or trigger a device to
obtain a cold sample. Source fixtures cover cold/expiry/fault behavior.

For each response retain request ID, timestamp, client elapsed time and the
`governance.plan_store_scaling` object, especially:

- `plan_navigation.generation`, `task_navigation.generation` and record counts;
- `health_cache_rebuild_count` and `health_cache_hit_count`;
- `hot_paths.governance_health`: `last_duration_ms`, `records_enumerated`,
  `plan_records_deserialized`, `terminal_plan_records_deserialized`,
  `task_records_deserialized` and `terminal_task_records_deserialized`.

When observed history/authority is unchanged and no relevant lifecycle transition
intervenes, the two repeats must demonstrate cache reuse: no aggregate rebuild,
increasing hit count, and zero plan/terminal-plan deserialization for those calls.
Task reads may remain nonzero because F3 is refreshed. Healthy live F3, provider,
lock and recovery fields must remain visible, with no new failure or fallback.
Concurrent health calls can increment cumulative counters; do not attribute their
increments to this collector. Use per-call work evidence together with counters.

A supported update or expiry can legitimately rebuild. Preserve and explain
such a result as an inconclusive unchanged-workload comparison; do not silently
discard it or keep polling until green. Stop for reconciliation on unexpected
generation/authority changes, unhealthy storage, unexplained outcomes, a timed-out
or incomplete response, or repeated scans under otherwise unchanged evidence.
Do not infer cache success from a fast response or missing fields.

Report installed timings and work counts separately from synthetic measurements.
Reuse an authenticated beta.7 measurement only with its original history/load and
instrumentation caveats; do not downgrade to manufacture a baseline. No latency
promise or heartbeat/authority-cause conclusion follows from these three reads.
If no cold sample was naturally observed, label cold installed latency unmeasured;
do not invent it as a pass or manufacture disruption to obtain it.

## 5. Settlement and closure

Finish with one no-probe health read. Reconcile all relevant counters, active
executions/tasks, pending approvals/challenges, applies/rollbacks, locks/holds,
Core leases/commits, storage/audit errors, recovery failures and fallback against
the baseline. Ordinary read/audit/cache counters and existing lifecycle-expiry
events can change; attribute them and any concurrent owner work explicitly.
Historical counts are not new failures and must not be reset to obtain a pass.

Report source/CI, publication, installed-image binding, identity/authority,
read/history continuity, raw catalog, panel, cache reuse/measurements and settlement
as separate gates. Preserve earlier packages and all failed/inconclusive captures;
write new private evidence with request/timing/hash bindings and a verified manifest.
Missing mandatory evidence remains open. Unobserved installed cold latency is an
explicit measurement limitation, not permission to restart the system.

This release has no live write canary. Completed beta.7/F028 observation and other
earlier acceptance stay closed with their original qualifications. F030/F035
remain separate: successful cache acceptance neither proves their incident cause
nor authorizes fresh plans, approvals, apply/replay or household mutation.
