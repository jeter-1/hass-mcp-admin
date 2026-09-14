# Engineering 2.2.1 acceptance

This contract covers the maintenance release with controlled build inputs and
arm/v7 retirement. Source validation, candidate image execution, publication
verification and installed acceptance are separate evidence gates. Live
operations require separate authorization.

## Exact source and supported installation

Canonical preparation base: `284baa90f5c056c383f1b663ed0f9e68cfdbe026`.
Reviewed implementation: `d47d77a4e55f7606088d3d98d8545b5d81e62dd7`, tree
`a648aac66a981c86ab0e3bdcf37550943024ea39`.
Preserve the reviewed implementation and its independent review; record the
final release head/tree and review the release delta separately.

**2.2.1 supports `linux/amd64` and `linux/arm64` only.** Published 2.2.0 at
`6e71c4f1286568a4e60937a8a56b84a541e31164` retains its three-platform images.
An existing arm/v7 system needs a separately authorized migration to supported
64-bit hardware/OS. Neither this preparation nor an image publication performs
that migration. Preserve historical three-platform verification and evidence.

Keep **HA MCP Engineering Server Beta**, `hass_mcp_engineering_beta`,
`ghcr.io/jeter-1/hass-mcp-engineering-beta`, ports 8100/8110, ingress, options,
defaults, persistent paths and formats. Stable-v1 remains 1.1.2, frozen and
operationally retired. No second installation or internal Beta rename is needed.

## Materialization and candidate validation

1. Fetch and verify actual base/head/tree, versions and clean branch. Reconcile
   drift and conflicting release identities before writes; preserve evidence.
2. Stage exactly `2.2.1` and a newline in `.release/next-version`. Require the
   shipped transition validator to accept `2.2.0 -> 2.2.1`; a refusal blocks
   materialization and must not be bypassed by hand-editing version constants.
3. Require exact staged resolution of this document and
   `docs/V2_2_1_RELEASE_NOTES.md`. Inspect the promotion preview, then apply only
   under release-preparation authority. Require all three version declarations
   at 2.2.1, staging consumed and exact active document resolution.
4. Run affected release/context/metadata/publication checks and final clean-head
   Evidence against the verified canonical base. Declare only exact protected
   files present in the complete PR diff. Require every applicable step,
   including full discovery and the legitimate version transition. Preserve
   failures, commands, interpreter, counts and skips. Do not duplicate a passing
   full suite merely to refresh timestamps.
5. Verify that release preparation preserves the reviewed Dockerfile, locks,
   build-input manifest/helpers, publication verification, CI behavior and
   behavioral regressions. Runtime files remain unchanged except the version
   constant; config changes relative to the reviewed head are version-only.
   Stable-v1, trust data, permissions and historical documents remain unchanged.
6. Complete candidate CI with all existing required families and aggregate
   `validate`. Require actual amd64/arm64 container builds and networkless smoke,
   their exact runtime inventories and source binding in `engineering-build-inputs`.
   Host-only tests or synthetic Docker do not satisfy architecture execution.
7. Retain every existing Core, add-on and exact-provider lane, including the
   actual pinned Core 2026.9.2 / ha-mcp 8.4.3 runtime checks for helper planning,
   approval, verified execution/restoration, refusals and cleanup. Bind run,
   attempt, jobs and artifacts to the candidate; verify synthetic merge parents
   and tree. Skipped, canceled or failed required work is not passing evidence.
8. Review the complete diff and obtain bounded independent release-delta review.
   Preserve the prior implementation verdict without treating it as review of
   later changes. Leave OPEN/DRAFT without auto-merge until the owner decision.

The [build-input contract](BUILD_INPUTS.md) defines the pinned Python base,
wheel-only locks, final-container smoke, inventory/base verification and update
process. Do not change a lock, remove a platform test or relax a refusal to make
validation pass. Existing release guards and exact-source recovery remain.

## Publication verification

Under separate publication authority, bind the actual main merge/source/tree,
three matching version declarations, clean build/time, annotated `v2.2.1` tag,
stable GitHub release flag and both version/commit image tags. Require the exact
published OCI index and both linux/amd64 and linux/arm64 manifests/configurations.
Historical 2.2.0 tags, images, release notes and acceptance records stay intact.

Require final-digest SBOM Python inventories and resolved base provenance to
match the committed declarations. Preserve source/hash/subject checks and all
version-tag absence and guarded recovery rules. Record actual artifacts and
coverage: digest-bound metadata inspection is not independent cryptographic
signer authentication, independent published-digest execution, or byte-for-byte
reproducibility. A build smoke or candidate inventory is not an installed image.

Observe the existing controlled merge/publication handoff. If the workflow-token
merge does not trigger publication, the existing authorized owner-dispatched
entry point uses the actual merge SHA; do not introduce a credential workaround
or rebuild after an ambiguous partial publication.

## Separately authorized installed acceptance

Prior 2.2.0 read-only acceptance remains evidence for that source, pairing and
observation time. Inspect retained artifacts and distinguish owner-reported
facts from independently inspected records. Keep household identifiers, keys,
endpoints, fixture details and private evidence paths out of public documents.

After separately authorized deployment to the existing supported 64-bit
installation, report PASS, FAIL, BLOCKED or NOT RUN for:

1. **Identity/image.** Read actual Engineering/Core/Supervisor/HAOS/ha-mcp
   identities. Bind the running container/architecture/image to 2.2.1's published
   index, platform manifest and configuration digest, with matching clean build
   labels. Displayed version alone is insufficient.
2. **Authority/catalog.** Require REST/WebSocket Core identity agreement, all
   17 Core capabilities, exact reviewed ha-mcp 8.4.3 admission, dashboard
   authority, healthy storage/audit and execution readiness. Capture one fresh
   public Engineering session with raw initialization, initialized notification
   and every tools/list page, zero tools/call, exact cursor continuity and full
   descriptor comparison against 2.2.1 source/dependencies. Require 51 static
   plus 25 delegated tools, 76 unique, and held `ha_get_operation_status` absence.
   Bracket capture with identity/health; a count or cached wrapper list is not
   complete catalog evidence.
3. **Useful behavior.** Run bounded existing entity/state, device/effective-area,
   exposure, service-discovery, automation/retained-trace, template, config-check,
   dashboard and reconciliation-detail reads. Preserve provider attribution,
   completeness and canonical errors. Follow one missing-target read with an
   unrelated successful read. Never trigger actions to manufacture evidence.
4. **Natural refresh.** Use a confirmed fixture and ordinary bounded
   `refresh_index=false` request. Record a valid index, generation/build time and
   expiry. Age naturally beyond the 600-second soft TTL below the 3600-second
   hard TTL; one ordinary query must initiate replacement that completes after
   caller return. Observe health without retry-starting queries, require fresh
   replacement and settlement. Fingerprint equality is acceptable; a timeout
   is incomplete evidence. The 300-second deadline remains cooperative.
5. **Settlement.** Recheck identity, an unrelated useful read and resource/failure
   counters. Attribute background/historical work; require no unexplained active
   execution, claims, locks, holds, leases, commits, new recovery failure or
   fallback. No mutation is needed to prove these build-only read checks.

Core 2026.9.2 remains conditional on separately configured, valid signed Core
authority selecting existing contracts. Installing 2.2.1 creates no trust;
compiled pairings and current denials remain enforced. There is no blanket
future Core/ha-mcp support. Do not change trust or use a fallback to pass a test.

Full Android navigation, cache-only startup during an outage and independent
backup-content verification were not established by earlier acceptance and
remain explicit limitations. Additional canaries, restoration, notifications,
backup, restart and Core updates require their own bounded decisions; no new
mutation canary is required solely for these packaging changes.

## Recovery and disposition

Stop dependent work for identity drift, unavailable authority, fallback,
uncertain execution or unexplained retained resources. Preserve failures and
continue unaffected authorized investigation. Never blindly retry mutations,
reuse a failed approval, disable denials or edit lifecycle/cache state.

Local recovery reverts release-preparation commits while retaining reviewed
implementation. Any later installed recovery must account for compatible Core,
configuration/database state, active execution, usable backups and independent
recovery access. Reverting source does not restore upstream arm/v7 support.

The handoff records exact base/head/tree, reviewed implementation preservation,
commands/counts/skips, Evidence, architecture/runtime CI, review status and
remaining publication/deployment gates. Josh retains Ready; merge/publication
and installed operations require their separate authority.
