# Engineering 2.4.0-beta.13 acceptance

This is an acceptance definition, not an executed receipt or deployment approval.
The protected-main preparation base is
`b8669a306e5cb05932bce38b6f36e81eb55a3fb3`; this is a source identity, not
a published or installed beta.13 artifact.
Bind source validation to the final candidate head/tree, publication to the actual
protected merge, and installed checks to the observed artifact. Do not substitute
the implementation commit for a future merge SHA, build time or image digest.

Beta.13 carries forward the [offline baseline comparator](AUTOMATION_AUDIT_BASELINE.md)
and the merged Alarmo responsiveness-test isolation correction. Beta.12 source
merged, but its publication validation failed before the publish job; beta.13
supersedes that unpublished candidate without altering its receipts. No beta.12
installation or beta.12 installed-acceptance receipt is a prerequisite.
It adds no MCP tool, native collection, server persistence or live write path.
Preserve completed beta.11 acceptance and household holds. Local utility
acceptance and installed-release acceptance are separate outcomes; one does not
establish the other.

## 1. Final source and review

Require `2.4.0-beta.13` in add-on config, runtime version and metadata validator;
consumed `.release/next-version`; and exact current acceptance/release-note
resolution from `scripts/codex-context.py`. Retain the comparator
implementation/chronology review and correction evidence,
plus PR #226's separate review of the unchanged measured fixture and child-process
failure/timeout/counting controls. After complete clean-head Full/Evidence,
independently review the release-only delta. Require **15/15 Evidence gates** and record exact
head, environment, test counts and skip reasons.

Require exact-head candidate CI `validate`, including the repository's existing
source, image/build and disposable Core/ha-mcp/Alarmo lanes. Do not substitute a
different revision's CI, omit required jobs, or invent a new release-specific
live scenario. Existing CI fixture pins are test identities, not a new installed
Core version requirement. If CI has not run, source preparation may be complete
but release readiness is pending.

Compare the final diff with the preparation base: only Engineering version
metadata, README/changelog and beta.13 release/acceptance documents may change.
The comparator and timing-test correction are already present in the base.
Preserve all 81 descriptors,
20 Core profile fingerprints, registry bytes/trust roots, provider boundaries,
dependencies, workflows, stable-v1, deployment options and persistent formats.

## 2. Offline functional tests

Run in the verified checkout using the existing validated Python environment:

```sh
python -B -m unittest discover -s tests -p test_automation_audit_baseline.py -v
```

Exact-head full discovery can satisfy this focused command when its module
execution and results are retained. Do not rerun it solely for ceremony.

| Case | Required result |
| --- | --- |
| Same established installation/scope, canonical mappings, readable complete records, continuous authority and exact model | Equal/different hashes produce `UNCHANGED`/`CHANGED` |
| Non-overlapping ordered earlier/later captures and authoritative complete inventory establish absence | `ADDED`/`REMOVED`, with reconciled totals |
| Reversed/overlapping intervals, equal baseline IDs or equal non-null source-artifact digests | Every affected classification is `UNKNOWN`; fixed pair reason codes and both capture intervals appear in the report, with no definitive rename/state annotations |
| Ordered distinct captures, including exact adjacent boundaries and timezone-equivalent ordering | Existing positive `UNCHANGED`/`CHANGED`/`ADDED`/`REMOVED` controls remain valid when all other evidence gates pass |
| Failed read, missing identity, conflicting mapping, partial inventory, unknown drift or incompatible fingerprint | `UNKNOWN` where evidence is insufficient; no false absence |
| Verified rename and definite on/off change | Separate identity/state annotations, without changing the hash contract |
| Legacy October baseline | Original material digest validated; unresolved identity/fingerprint/inventory preserved; self-comparison remains 100 `UNKNOWN` |
| Serialization | Sorted keys and preserved arrays; Unicode escaping and finite numeric types follow the exact model; enabled/provider fields excluded |
| Contradictory inventories, duplicate keys/IDs, nonfinite numbers, malformed types/digests, lone surrogates | Fixed typed refusal without rejected-value disclosure |
| Observation timestamps | Strict timezone-bearing grammar and inclusive capture fences; unavailable/interval-only times remain null; contradictory observations rejected |
| Input and output boundaries | Bounded acquisition before parsing; depth/node/string/record caps; deterministic detail fitting including exact-byte and digit-transition boundaries |
| Privacy and reachability | Sentinel values absent from diagnostics; no network, provider or subprocess collection; only explicit local report output |

Keep positive controls with each refusal family. For truncated reports, all
summary classifications remain accounted for and the exact omitted-detail count
is explicit. Synthetic fixtures establish behavior without household data.

Where the original private October artifact is available, reuse it for the legacy
check after verifying its retained hash; never commit it or upgrade its missing
assurances. If inaccessible, report that private check unavailable and retain
synthetic coverage; do not manufacture a replacement or capture another 100
configurations. A new live collector/baseline needs its own scope and acceptance.

### Responsiveness-test preservation

The existing maximum-inventory test must execute its original 512-sensor /
128-group fixture in a fresh isolated interpreter with default natural GC.
Require its unchanged ten assertions, 100 ms loop-thread CPU and 300 ms wall
limits, count/evidence/snapshot checks and nine provider reads. Retain measured
maximum gaps in the candidate full-suite log. The fixed child must execute once;
child failure and the 30-second timeout must fail the parent without retry.
The child summary must not replace the outer Full/Evidence test count.

Reuse PR #226's reviewed 20-case/eight-worker evidence and real failing-child
control when source equality is verified; do not repeat that campaign merely
for this metadata transition. Final-candidate full discovery still executes the
measured fixture and wrapper regressions. This source-test result does not prove
a production-wide latency bound or repair application-heap GC pauses. See
[the documented measurement boundary](INTEGRATION_INSPECTION.md#responsiveness-regression-environment).

## 3. Publication and installed-image binding

After the owner-authorized Ready/merge/publication path, verify the protected
merge/publication handoff, exact source/tree/version, raw OCI index and both
architecture manifests/configurations, provenance, SBOM and locked inputs with
the existing reviewed verifiers. Bind the running configuration digest through
the observed architecture manifest to that published index after separately
authorized deployment. Use the exact
beta.13 version-transition merge as publication source; do not reuse beta.12's
failed run, attempt marker or unverified artifact identity. Matching labels,
release prose and cached inventory do not close this gate.

Before deployment, preserve compatible recovery material and durable governance,
task, dispatch and supplementary-verification history. No migration is introduced.
Preserve the beta.6 expanded-lock compatibility limit and accepted F027 missing
historical-prestate exception. Downgrading a binary does not undo dispatches or
justify restoring old execution state. Completed F028 re-verification stays
complete; do not invoke or replay it for beta.13 acceptance.

## 4. Bounded installed continuity, if deployed

Use the separately authorized existing Engineering route. This document does not
authorize deployment, configuration edits, plans/approvals, script/device actions,
restarts/reloads, backups/restores, trust changes or private operator access.

1. Capture no-probe `server_info` and health. Require exact beta.13 source/build
   and image binding; REST/WebSocket Core identity agreement; exact ha-mcp 8.5.0
   admission; current valid signed authority; 20/20 applicable profiles for the
   retained Alarmo-enabled pairing; healthy storage/audit/F3; and 81 tools.
   Record actual Core version, registry sequence, generation, counters and
   concurrent work. Use a bounded connectivity probe only to resolve a concrete
   connectivity gap. A version mismatch stops reconciliation rather than silently
   changing the acceptance target.
2. Perform one useful native and one admitted delegated read, preserving provider
   attribution, completeness and no-fallback evidence. Read one retained
   successful plan/task to verify original outcome and dispatch continuity without
   replay. Preserve beta.11 Alarmo identity/activation/inspection receipts; repeat
   an Alarmo read only for a specific newly identified continuity gap, after its
   existing identity and authority prerequisites pass.
3. Capture a fresh, separate public MCP session: initialize, initialized
   notification and every `tools/list` page, with **zero `tools/call`** and all
   **81 complete descriptors** matching final source. Use separate before/after
   no-probe identity/health brackets. Require coherent identity/authority across
   brackets; preserve failure/partial evidence without automatic retry. Private
   endpoint/token remain hidden owner-terminal inputs in reviewed collectors.
   Counts or refreshed connector inventory are not this raw protocol evidence.
4. Retain a dated, render-only observation that the existing approval panel opens.
   Reuse an already supplied beta.13 observation; do not ask for it twice or create
   an approval. Catalog capture does not establish UI rendering.
5. Final no-probe settlement must show no acceptance-created plans/tasks/dispatches,
   pending approvals/challenges, applies/rollbacks, locks/holds, retained leases,
   recovery failures or fallback. Reconcile separately authorized concurrent
   activity from receipts instead of demanding unchanged global counters. Explain
   any authority retirement, provider failure, storage/audit error or unexplained
   latency; stop dependent acceptance on unresolved identity/authority failures.

No dashboard/device write canary or new household inventory is required for this
offline increment. No new registry signing or activation is required. Preserve
existing read/audit correlation and log-retention limits; absence of a retained
entry does not prove absence of activity.

## 5. Closure and recovery

Retain an immutable report/manifest separating source/CI, offline comparison,
publication, installed image, runtime continuity, raw catalog and panel evidence.
State PASS, failed, pending, unavailable or not applicable for each gate. Complete
installed acceptance requires all applicable installed gates; offline success alone
cannot establish it. Missing receipts remain explicit and do not require repeating
already proven checks.

Local utility rollback is removal/reversion of its unpublished source/report;
preserve original baselines and evidence. Published rollback or deployment is a
separate reviewed decision using compatible recovery material. Do not alter prior
manifests, manufacture history or restore execution records merely for acceptance.
