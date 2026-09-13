# Engineering 2.2.0 acceptance

Engineering 2.2.0 is the stable Engineering milestone on the retained technical
Beta installation path. This contract separates candidate validation, retained
RC9 acceptance and later verification of the final published/installed artifact.
Live operations require separate authorization.

## Source and release authority

Canonical preparation base:
`34e5600963666cbd4c5cc33de2138e2d9bd6294a`.
Accepted RC9 runtime/publication source:
`e96cf9337fa7033dce3f1935c055a22a06184def`, tree
`d22d0f19df60777babb292895f969cc824cec8aa`.
The intervening signed Core-registry data publication is preserved unchanged.

Retain `hass_mcp_engineering_beta`, **HA MCP Engineering Server Beta**,
`ghcr.io/jeter-1/hass-mcp-engineering-beta`, MCP port 8100, admin-only approval
ingress on internal port 8110, options/defaults, persistent paths and formats.
No Beta rename, second installation, data move, connection change or v1
migration belongs to 2.2.0. Historical stable-v1 remains 1.1.2, frozen and
operationally retired.

The release delta is limited to versions, these exact documents, current README
guidance and necessary current release-test expectations. Preserve runtime,
public schemas, registration, providers/routes/admission, approvals, ownership,
verification, permissions, dependencies, trust data/configuration, persistence,
TTLs and workflows.

1. Fetch and bind actual base/head/tree, branch and clean worktree. Reconcile
   movement; preserve unrelated checkouts and all historical evidence.
2. Stage exactly `2.2.0` plus newline in `.release/next-version`. Validate
   `2.2.0-rc.9 -> 2.2.0` through the shipped transition utility.
3. Require exact staged resolution of this document and
   `docs/V2_2_0_RELEASE_NOTES.md`. Inspect promotion without `--apply` before
   authorized local materialization.
4. Require all three version authorities at 2.2.0, staging consumed and exact
   active document resolution. Historical documents remain unchanged.
5. Run existing transition, materialization, context, metadata and publication
   tests. Run clean committed-head Evidence against the verified canonical base,
   declaring exactly the changed protected version files. Require all applicable
   steps including metadata and full discovery; preserve failures and actual
   commands, interpreter, counts and skips. Do not duplicate a passing full suite.
6. Complete exact-head CI, including architecture builds and the actual pinned
   Core 2026.9.2/ha-mcp 8.4.3 lane. Require useful helper planning, authenticated
   approval, verified forward/reverse actions, duplicate refusal,
   uncertain-response reconciliation and cleanup, not capability counts alone.
   Bind run/attempt, job/artifacts and checkout to the candidate; verify synthetic
   merge parents and tree. Historical CI remains historical.
7. Review the complete delta and prepare bounded independent review. Self-review
   is not independent approval. Leave OPEN/DRAFT without auto-merge.

Inspect the existing publisher's exact release-note resolution, body construction
and stable flag offline. It supports 2.2.0 on the retained image path; “rc” or
“beta” in a workflow filename does not require a workflow edit. A version-changing
main merge can automatically publish. Owner authorization must cover merge and
that consequence; deployment remains separate.

## Compatibility and execution contracts

The accepted RC9 pairing includes **Core 2026.9.2 / ha-mcp 8.4.3** under
separately configured, valid signed Core authority for the uncompiled Core
release. Installing 2.2.0 alone creates no trust. Existing compiled pairings
remain preserved. Unknown/changed contracts need review and possibly code
changes; no future Core or ha-mcp version is implicitly compatible.

The [Core registry contract](CORE_RELEASE_REGISTRY.md) governs separate Core
trust, signatures, exact entries and compiled references, expiry, retained
denials and retirement. Data grants no new route, transport or mutation
permission. Do not alter trust or publish data to make an acceptance test pass.

The healthy reviewed pairing requires **17 Core capabilities**, **51 static
plus 25 delegated tools, 76 total**, and held `ha_get_operation_status` absence
from ordinary registration. Engineering selects admitted ha-mcp/native providers
inside one public Nabu Casa endpoint, with no fallback or arbitrary forwarding.

Preserve exact pre-dispatch approval, principal/hash/policy/current-state checks,
authority, ownership, durable intent and authoritative readback. Recovery cannot
override an active owner or redispatch after intent. Bounded JSON retains
reconciliation identities, available state/attempt/dispatch/verification facts
and explicit omitted-detail retrieval. Attempt count alone is not dispatch proof.

Each dependency build owns exact authority independently of callers, consumes
before provider interaction and revalidates before later reads. Retired or
invalidated scans cannot publish current evidence. Source fences, post-lock
freshness, semantic applicability and exactly-once local cleanup remain required.
The cooperative deadline is 300 seconds and soft/hard TTLs remain 600/3600 seconds.
No remote-abort guarantee follows from cancellation or a monitoring deadline.

## Retained RC9 evidence and limitations

Preserve original receipts and available supporting artifacts, recording hashes,
source/pairing, observation times and limits in private evidence. Saved-file
consistency checks are offline inspection, not fresh live tests. Keep household
identifiers, endpoints, keys, fixtures, request IDs and private paths out of
public documents and PRs.

| Evidence area | Retained basis and limitation |
| --- | --- |
| Source, CI, publication | RC9 source `e96cf9337fa7033dce3f1935c055a22a06184def`; no transfer to 2.2.0 build/CI identity |
| Installed image and public catalog | Inspected RC9 image chain and complete captures, separately timed for Core 2026.9.1 and 2026.9.2 |
| Signed registry activation | Accepted/current data and pairing continuity; operator configuration confirmation differs from health evidence |
| Core 2026.9.2 reads/semantics | Device/effective-area comparisons, useful unaffected reads and regressions with exact ha-mcp 8.4.3 |
| Natural refresh | New build completed after its initiating ordinary request returned; fresh replacement and settlement |
| Helper/dashboard canaries | Task/child evidence and independent exact restoration; rendering confirmations are operator observations |
| Notification clearing | Operator-reported handset clearing, not complete approval-panel navigation |
| Restart recovery | Fresh-process authority/prewarm, useful reads, restored-state persistence and settlement; operator restart report remains distinct |

Do not rewrite initial failures as uninterrupted success or treat later success
as proof of an earlier failure's cause. Preserve any known legacy verification
projection inconsistency separately from authoritative task/child evidence and
target readback; disagreement between authoritative sources stops dependent work.

Explicit limits: **complete Android navigation was not established**;
**cache-only startup during an outage was not established**; **backup contents
were not independently verified**. Core version agreement is not running Core
OCI attestation. Incomplete consumer/dynamic-reference coverage stays explicit.
Do not substitute an asserted independent PASS for missing underlying artifacts.

These limits neither authorize extra tests nor waive a later operational plan's
requirements. Any actual release blocker must identify its controlling
requirement and missing evidence.

## Final published and installed 2.2.0 verification

Report PASS, FAIL, BLOCKED or NOT RUN separately for these gates. Retained RC9
evidence does not establish their completion for 2.2.0. Preparation executes
none of these live operations.

1. **Publication.** Under separate authority, bind source/tree, matching versions,
   clean build/time, annotated tag, stable release flag, version/commit image
   tags, OCI index, platform manifests/configurations, provenance and SBOM.
   Use actual final publication identity, not a proposed candidate SHA.
2. **Installed identity.** After separately authorized deployment to the same
   add-on, read actual Engineering/Core/Supervisor/HAOS/ha-mcp identities.
   Through approved inspection, bind the running architecture/container/image
   to the published 2.2.0 index, platform manifest and configuration digest.
   Displayed versions/labels alone are insufficient. Preserve data and options.
3. **Fresh raw catalog.** Capture one fresh public Engineering session: raw
   initialize, initialized notification and every tools/list page through final
   termination, with zero tools/call during enumeration. Compare every descriptor,
   schema, annotation and name with exact 2.2.0 source/dependencies. Bracket
   capture with identity/health. Require the 76-tool reviewed catalog and held
   operation-status absence; a count or wrapper inventory is insufficient.
4. **Authority/health.** Require REST/WebSocket Core identity agreement, all
   17 capabilities, exact ha-mcp 8.4.3 admission, dashboard authority, healthy
   audit/storage and F3. For an enabled Core registry, check current accepted
   applicability, cache and denial state while retaining separate trust-identity
   evidence. Do not activate/change trust merely to perform read-only tests.
5. **Useful reads.** Exercise confirmed entity/state and device/effective-area/
   exposure reads, service discovery, an existing automation/naturally retained
   trace, configuration validation, small dictionary/State/device/area templates,
   complete repeated dashboard reads, supported plan/task detail retrieval and
   one canonical missing target followed by an unrelated success. Preserve
   provider attribution, bounded output, uncertainty and zero fallback.
6. **Natural refresh.** Establish a valid snapshot under current authority.
   Age naturally past soft expiry below hard expiry, issue one ordinary bounded
   `refresh_index=false` query, then monitor health without queries that initiate
   retries. Require a newer build completed after caller return, fresh replacement
   and settled resources. An unchanged fingerprint is acceptable. Do not force
   refresh, alter TTLs or manufacture changes. Timeout is incomplete evidence.
7. **Final settlement.** Recheck identity and an unrelated useful read. Attribute
   background activity and historical counters; require no unexplained active
   execution, pending work, children, leases/commits, locks, holds, recovery failure
   or fallback. Read-only reconciliation of retained execution and designated
   fixture state precedes any recovery proposal.

Canaries/restoration remain separately bounded: exact fixtures, fresh state,
consequence disclosures, authenticated approval for each plan, one apply,
task/child inspection and independent readback/restoration. Preserve RC9 results
as RC9; an unperformed GA mutation is NOT RUN. Dashboard tests require no outside
edits and a freshly approved supported inverse plan; saves remain non-atomic.
Never reuse failed plans or blindly retry uncertain execution. Already-restored
targets must not be toggled to recreate a historical incident.

Held-read, notification/navigation, backup, restart and Core-update tests retain
separate scope and prerequisites. Distinguish fresh observations from preserved
evidence and do not silently close an unrun gate. A Core update requires exact
compatibility, backup/recovery, maintenance-window and owner authorization;
no additional update belongs to this milestone preparation.

Stop dependent mutations on identity drift, lost authority, fallback, uncertain
execution, authoritative verification disagreement, failed restoration or
unexplained resource retention. Continue unaffected authorized read-only work.

## Recovery and final disposition

Local recovery reverts release-preparation commits while retaining accepted
implementation and evidence. No household recovery or migration occurs here.

Installed recovery must reconcile current execution/targets, compatible Core,
configuration/database state, usable backups and independent recovery access.
Historical v1 is not a rollback; disabling a denial or editing cache/lifecycle
files, sequence or trust keys is not a recovery procedure.

For separately authorized helper restoration, reconcile the operator-designated
test helper and prior execution. If already OFF, record that without toggling it.
If an OFF transition is required, create a fresh exact plan, obtain authenticated
panel approval, apply once, inspect its task and independently verify OFF.
Never reuse a failed plan or blindly retry an uncertain operation.

The final handoff records base/head/tree, validation/CI, independent-review
status, preservation and remaining operational gates. Leave the PR draft.
The later owner decision must cover merge and automatic publication; deployment
and final installed verification follow separately.
