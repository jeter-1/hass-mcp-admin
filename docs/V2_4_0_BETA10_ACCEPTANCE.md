# Engineering 2.4.0-beta.10 acceptance

Bounded logbook and response-processing correction on merged beta.9 base
`fc7b22f10dd5c854976372d965182060947bed23`. The prior accepted logbook implementation
is `0b07c1ee624a56e30558843636ed14d9bedba77c`; the accepted integration candidate
is `0578e3fed7934ad4f2df3aa503fd4368da4e2982`. Bind the busy-classification and
release delta, final tests and independent review to the final candidate head.
This contract defines acceptance gates. Publication, deployment, production
access, plans/approvals and device operations require separate authorization.

## 1. Source, review and CI

Require the add-on, runtime and metadata-validator declarations to agree on
`2.4.0-beta.10`, consumed `.release/next-version`, and exact active document
resolution from `scripts/codex-context.py`. Preserve prior implementation,
correction and integration reviews. Independently review the final F1/F2 and
release-document delta, including the F3/F4 compatibility disclosures.

Run final clean-head Full/Evidence with the existing locked environment and every
exact protected-path declaration. All gates, including metadata, must pass;
the earlier 14/15 result remains historical, not a waiver. Record base/head,
counts/skips and environment. Required focused coverage follows; exact-head full
discovery may satisfy it without repeating tests, labeled accordingly:

```sh
python -m unittest -v \
  tests.test_logbook_bounds \
  tests.test_bounded_response_processing \
  tests.test_rc5_bounded_responses \
  tests.test_rc6_integrity_response_budget \
  tests.test_beta_observability \
  tests.test_change_impact_analysis \
  tests.test_governance_async_health \
  tests.test_governance_health_cache
```

| Case | Required proof |
| --- | --- |
| Useful interval | One clock capture and explicit start/end for filtered and unfiltered 12/24/72/168-hour requests; valid small and empty responses preserve list form. |
| Useful partial | Nonempty large response retains whole sanitized records in source order, exact received-body counts, interval and partial provider attribution; useful success at 1024/2048/60000 response budgets and long request IDs. |
| Busy classification | A second public call during acquisition returns `logbook_busy`, retryable true, HTTP classification 409 and preserved safe message, with zero additional provider/HTTP attempts. The first finishes usefully; a later explicit call succeeds. No queued/automatic request. Canceled processing retains the busy slot until physical completion. |
| Invalid/refused inputs | Nonfinite/zero/negative/excessive hours, huge direct integers and malformed or overlong entity IDs refuse before HTTP; unavailable Core authority still refuses before dispatch. |
| Resource and wire bounds | Streamed 1 MiB, record/item/depth boundaries including rejected members; malformed/duplicate-key/nonfinite/encoded bodies refuse. Received HTTP failures preserve status before body processing; timeout, disconnect, redirect and cancellation do not retry or fall back. |
| No useful record | Nonempty oversized input that cannot preserve one useful record returns the typed non-retryable limit error; it is not an empty successful truncation. |
| Formatter and concurrency | Bounded deterministic processing, valid JSON and byte limits, fixed-count omission work, safe text, useful independent-read progress and single physical worker; preserve action identities/outcomes/dispatch/verification and read-only reconciliation across response budgets. |
| Health continuity | Beta.9 asynchronous health, race reasons, warm reuse and phase measurements remain correct with the shared formatter. No authority-lifetime, governance, storage or provider change. |

Require green exact-head CI `validate` and every required family, including source,
both architecture packaging/build-input checks, configured Core contracts,
exact-image gateway and exact add-on runtime lanes. Bind the actual PR/base/head
and synthetic merge checkout. A green configured Core 2026.9.3 lane is not the
next gate's assembled Core 2026.9.4 logbook proof.

## 2. Disposable exact-Core interval integration

Retain the eight source-derived Core 2026.9.4 method checks as synthetic evidence.
Before release readiness, exercise the actual assembled logbook endpoint in a
disposable exact Core 2026.9.4 environment, bound to immutable source/image and
the candidate's native reader. Use synthetic recorder/history data only. Prove
filtered and unfiltered sparse intervals including events inside 24 hours,
between 24 and 72 hours, between 72 and 168 hours, and outside the requested
window; explicit end_time must include/exclude the correct events. Verify useful
registered responses and provider/interval attribution, with no live endpoint,
service/device operations, hidden retry or fallback. Use bounded deadlines and
cleanup; no production export or credential is required.

If the approved environment cannot run this gate, report it unverified and keep
release readiness blocked; do not substitute AST-only tests or change Docker
permissions/workflows to manufacture access. Core fixtures are evidence, not
new installed-version pins or authority grants.

## 3. Publication, running image and recovery

After separate publication authorization, verify the protected merge/handoff,
source/tree/version, OCI index and both architecture manifests/configurations,
provenance, SBOM and locked build inputs. Bind the running container/configuration
digest through verified manifest bytes to the published index. Version/SHA labels
or MCP inventory alone do not close image identity. Record actual build time and
digest from publication; do not invent them in this contract.

Before separate deployment authorization, preserve current durable execution and
supplementary verification records and identify a verified compatible rollback
artifact. No new storage format/migration is introduced. An old binary or stale
execution-state restoration cannot undo dispatch. Preserve beta.6 expanded-lock
reader limits, the accepted F027 historical qualification and completed F028
verification; none are repeated or erased by this release.

## 4. Bounded installed reads and catalog

1. Discover the current schemas. Read `server_info(check_ha=false)` and
   `get_server_health(check_ha=false)`; use one bounded connectivity probe only if
   needed. Verify exact installed beta.10 source/build and image binding,
   REST/WebSocket identity agreement, exact ha-mcp 8.5.0 admission, valid current
   signed Core authority with 19 compatible profiles, healthy storage/audit/F3,
   and 80 tools. Preserve observed authority drift and explain concurrent work.
2. Use one established native entity fixture and one admitted delegated state
   read. Require useful attribution, coverage and no fallback. Read one retained
   successful plan/task pair to verify original outcome and dispatch history;
   never invoke re-verification or replay as an acceptance shortcut.
3. Make one approved small `get_logbook` request, normally one hour and one
   verified entity with naturally retained activity. Record request interval,
   timing, provider, complete/partial status, bounds and any omissions. Do not
   repeat the old broad 72-hour request, manufacture events or deliberately
   overload production. An honest empty result is valid API behavior but does
   not by itself prove the useful nonempty-read gate; select a supported fixture
   from existing evidence, or leave that gate explicitly open. Busy, timeout or
   refusal is not a pass and does not authorize automatic retry.
4. Capture a separate fresh public Nabu Casa session containing initialize,
   initialized notification and every tools/list page, with zero tools/call.
   Compare all **80 exact descriptors (55 static + 25 delegated)** generated
   offline from final beta.10 source. `get_logbook`'s description intentionally
   differs from beta.9; arguments/annotations/registration and the other 79
   descriptors remain unchanged. Use separate no-probe identity/health brackets,
   hidden endpoint/token input, finite limits and retained failed attempts.
   Inventory counts or client refresh alone cannot establish this gate.
5. Retain a dated owner render-only observation of the existing approval panel;
   create or approve nothing for that observation.
6. Finish with no-probe health, reconciling tasks/executions, locks/holds,
   approvals/challenges, applies/rollbacks, Core leases/commits, storage/audit
   errors, recovery and fallback. Explain background audit/cache or separately
   authorized work. A settled final sample does not prove the interval was idle.

Stop dependent work on identity drift, lost authority/provider, unexplained
catalog withdrawal, incomplete health, new storage/audit errors or uncertain
execution. Preserve evidence without another approval loop, retry or restart.
Do not claim the historical stall's cause is proven from one small useful read.

## 5. Acceptance boundaries

Report source/CI, assembled Core integration, publication/image identity, useful
reads, raw catalog, panel and settlement separately. Save new private receipts
with exact identities, request IDs, timings and verified manifests, preserving
earlier packages. Unobserved mandatory gates remain open.

Beta.9's governance-change/approval-interval gate remains separately applicable
if it has not been closed. Follow that contract only with its specific live
authorization; do not create a plan or approval implicitly for beta.10 logbook
acceptance. No warm-read observation here establishes cold approval-to-dispatch
continuity or releases stale F032/F035 plans for replay. Alarmo, ordering changes,
F5 disconnect diagnostics and household changes remain outside scope.
