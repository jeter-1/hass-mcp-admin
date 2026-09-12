# Engineering 2.2.0-rc.8 acceptance

RC8 carries the reviewed Core-update-continuity implementation. Published RC7
base: `2a41530e0b3310dd549811002723be238bfe5315`. Reviewed implementation head:
`3bc8084a8a6cf8f7fe512484522a95ce8107b449`, tree
`cadb16a2953a741a60945824ab03e4edc8459363`. Stable remains 1.1.2.

## Release authority and preservation

Materialize RC8 in the original feature pull request before Ready. Author the
single-use `.release/next-version` declaration as `2.2.0-rc.8` plus a newline,
require exact staged resolution of these RC8 documents, inspect the checked-in
promotion preview, and apply with `scripts/promote_next_release.py --apply`.
Require all three authoritative declarations at RC8, consumed staging, and exact
active resolution of this acceptance document. Publication does not create a
second promotion commit or pull request.

Keep the reviewed runtime, contracts, workflows and behavioral regression tests
byte-identical to the reviewed head. Record both comparisons: published RC7 to
final candidate, and reviewed implementation to final candidate. The latter is
release preparation only. Preserve historical documents and failed Evidence
records. The RC7-to-RC8 transition must close the metadata gate against the
actual published RC7 base, without changing validator rules or comparisons.

## Core authority contract

Engineering remains the single public MCP endpoint through Nabu Casa. It
selects reviewed internal ha-mcp providers or native providers as appropriate.
Provider unavailability grants no alternate route or fallback authority.

Compatible exact Core releases can gain authority through independently reviewed,
signed compatibility data referencing existing compiled capability and probe
contracts. Successful observations, version prefixes and patch numbers do not
create trust. Unknown or changed contracts require further review and may require
code changes and a new Engineering release. Data cannot add code, providers,
tools, URLs, arguments or action families.

The opt-in options are `ha_core_release_registry_enabled` (default `false`) and
`ha_core_release_registry_public_key` (default empty). Core uses an independent
Ed25519 trust key, registry identity and cache; ha-mcp trust cannot substitute
for it. The runtime verifies signatures, closed records, exact identities,
contract references, bounded journals, successor/checkpoint history, expiry and
retained denials. A verified denial overrides positive authority, including a
compiled pairing. Denials survive expiry and cache reload. Disabling the registry
also removes its denial source and is not an automatic recovery action.

Authority selection is fenced through publication using the checked selection
token. Changes or expiry during selection/publication cannot leave a stale
positive generation usable. Acquisition, consumption and per-read/dispatch
revalidation retire moved, expired or revoked authority; only reconciliation
can publish a replacement. Existing approval, target/current-state, source-epoch,
single-use commit, durable intent and readback rules remain necessary. Polling
revocation takes effect when verified data arrives; local expiry is checked at
use. This does not establish remote abort or exactly-once remote execution.

Preparation compacts the positive chain to 32 envelopes and retains signed
coverage of distinct denials using the existing policy, without accumulating
redundant signatures of the same denial. The runtime's 64-envelope chain,
eight denial-source and existing byte bounds remain unchanged. Genuine retained
history exhaustion refuses rather than dropping authority evidence. See
[Core release registry](CORE_RELEASE_REGISTRY.md) for the complete operator and
lifecycle contract, including finite network and evidence bounds.

RC8 installation alone does **not** establish production authority for Core
2026.9.2. Production trust-key setup, owner activation and publication of exact
reviewed signed compatibility data are separate authorized operations. The
Core 2026.9.2 disposable lane uses ephemeral test authority, never production
approval. The compiled exact older pairings, including 2026.9.0 and 2026.9.1,
remain unchanged. The 2026.9 child-device profile still requires the reviewed
ha-mcp 8.4.3 adapter.

Preserve the earlier recovery/approval, bounded JSON reconciliation, integrity
pagination/coalescing, held-read accounting, device identifier, conservative
transport and dependency-build corrections. Shared dependency builds retain the
300-second cooperative deadline and 600/3600-second soft/hard evidence TTLs.
The healthy reviewed ha-mcp 8.4.3 pairing requires all 17 Core capabilities and
51 static plus 25 delegated tools, 76 total. `ha_get_operation_status` remains
absent from ordinary registration. No governed restart capability is added.

## Source, review and CI evidence

The focused implementation review resolved both prior findings with no actionable
findings and independently ran 38 regressions plus the two original reproductions
(40 passing tests). Its inspected implementation-head Evidence ran 3,461 tests,
20 skipped, with no test failures/errors: 3,441 non-skipped passes. Only unchanged
RC7 metadata failed (14/15 steps). These are historical implementation-head
results, not final RC8, built-image or installed-system acceptance.

On the clean committed RC8 candidate, run focused release/context checks and
complete Evidence against published RC7. Declare exactly every changed protected
path in the complete PR, including the preserved feature surfaces, version file
and release documentation. Require every applicable step to pass and record
commands, interpreter, counts, skips, base/head/tree and exit status. Keep failed
attempts separate. Prepare bounded independent review of the release-only delta.

Require exact-head CI including full validation, declared architecture builds,
existing disposable pairings, exact upstream/image acceptance and the actual
digest-pinned Core 2026.9.2/ha-mcp 8.4.3 lane. Inspect run/attempt IDs, job results,
logs and artifacts. Bind any synthetic merge checkout to both candidate/base
parents and its tree. Source-level lane assertions are not runtime-lane evidence.
CI test images and ephemeral signatures are not published RC8 artifacts or
production compatibility authority.

## Bounded installed-build acceptance

Publication, deployed image identity, fresh catalog and live behavior each need
their own evidence. Historical PASS results remain attributed to their original
release, Core pairing and observation time. Do not transfer prior installed-image,
catalog, canary or restart evidence to RC8. Keep household details, private paths,
connection secrets and operator records out of public release material.

After separately authorized deployment and read-only testing:

1. Verify actual Engineering product version, clean source/build identity and
   Core, Supervisor, HAOS and ha-mcp versions. Bind the running container/image
   architecture and configuration digest through the correct platform manifest
   to the published RC8 index. Labels or version agreement alone are insufficient.
2. Capture a fresh public session with raw initialize, initialized notification
   and every tools/list page, using the established private authentication
   procedure and zero tools/call during enumeration. Verify complete descriptors,
   schemas, annotations, unique names and cursor continuity against exact RC8
   source/dependencies. Bracket with identity/health. Require 76 tools only when
   the exact pairing and all required authority are admitted; unexplained losses
   fail, and unavailable capture facilities leave a BLOCKED evidence gap.
3. For an unchanged supported older Core pairing, verify its existing compiled
   behavior with the registry disabled. For an enabled installation, bind the
   independently authorized public-key identity and verified compatibility data
   to the selected authority. Require 17 capabilities under the fully supported
   pairing, exact ha-mcp admission, REST/WebSocket identity agreement, dashboard
   authority, healthy storage/audit and F3 readiness. Do not change registry
   settings merely to perform this read-only phase.
4. Exercise useful bounded existing entity/device/effective-area and unrelated
   reads, service discovery, automation configuration and naturally retained
   traces, configuration validation, read-only templates and complete dashboard
   rereads. Retain attribution, coverage, canonical errors and zero fallback.
   Reconcile retained task/child evidence read-only where available; never create
   an action to manufacture a successful task or trace.
5. Verify natural dependency replacement after an ordinary initiating request
   returns. Record valid generation/build/fingerprint/expiry, age past soft expiry
   below hard expiry, issue one bounded `refresh_index=false` query, then observe
   health without queries that start retries. Require completed newer evidence,
   truthful stale/coverage disclosure and settled resources. An unchanged
   fingerprint is allowed. Do not force refresh or alter clocks/TTLs.
6. Finish with a useful unrelated read, identity continuity and settled leases,
   commits, executions, locks and holds. Attribute historical counters and any
   background work; require zero fallback and no unexplained retention.

Prove data-driven admission, unknown/mismatched-contract refusal, expired/bad
signatures, revocation persistence, publication races, cleanup and useful
unaffected authority in disposable tests. Do not inject faults, expire trust,
revoke releases, tamper with caches or provoke races in the household installation.
A separately authorized installed-system continuity test must name the current
and target Core, reviewed signed-data identity, owner trust setup, exact update
scope, verified backup/recovery, interruption and post-update acceptance. RC8
preparation and installation do not authorize that transition. Do not upgrade
Core while its production authority prerequisites remain unestablished.

Helper/dashboard canaries retain exact fixture/current-state and consequence
review, scope authorization, authenticated approval for each fresh plan, one
apply, task inspection, independent readback and verified restoration. Never
reuse failed plans/approvals, blindly retry uncertain execution, or recreate an
already-restored condition. Dashboard saves remain non-atomic against outside
editors. Held reads, notifications/navigation, backups, restarts and Core updates
remain separately bounded; perform none merely because this document exists.

Report PASS, FAIL, BLOCKED or NOT RUN separately for source, CI/build, installed
image, raw catalog, useful reads, authority/continuity, natural refresh, canaries
and final settlement. Stop dependent mutations for identity drift, lost authority,
fallback, ambiguous dispatch, unverified restoration or unexplained retention;
continue unaffected authorized read-only investigation.

## Recovery and remaining decisions

Before delivery, local recovery is reverting the release-preparation commit while
retaining the reviewed feature commits and evidence. Published RC7 remains
immutable; returning to it removes data-driven Core admission and is not a full
Core/configuration/database recovery procedure. Installed recovery must preserve
compatible Core and configuration/database state, usable backups and access that
survives connector failure. Do not clear denial history or disable safeguards to
recover capability.

Leave delivery OPEN/DRAFT without auto-merge. Josh controls Ready and the
repository's protected merge/publication authorization. Production signing,
trust activation, data publication, deployment, live continuity, any Core update
and stable promotion remain separate decisions.
