# Engineering 2.2.3 acceptance

This contract separates release validation from authorized installed-path
acceptance. Engineering 2.2.3 preserves the technical Beta installation identity
and adds received Host/Origin enforcement. Deployment, configuration changes,
observer arming, restarts and household mutations require separate authorization.

## Source and final-candidate validation

- Published 2.2.2 / complete-PR base:
  `b1459b5fd6b69111c3f5c76d2c4939c2dd1fd2c5`.
- Independently reviewed implementation:
  `6ee5bf81301343b6e7c1a2f823128a4c27df19b4`.
- Reviewed tree: `74d52513a152b2abbea46defb992d00ef949bea0`.

Preserve the reviewed runtime and behavioral regressions. The independent review
reported no actionable findings: 455 focused tests run, 454 passed, one expected
staging-related skip, plus nine independent probes passed. Baseline reproductions
and candidate results remain separate evidence; they are not installed tests or
results for a later release head.

Validate the exact 2.2.2 to 2.2.3 transition with the shipped release tools. Stage
only `2.2.3` and a newline, resolve both matching documents exactly, inspect the
dry run, then materialize under release-preparation authorization. Require all
three version authorities at 2.2.3 and the staging declaration consumed. Preserve
historical release documents and validation failures. The newer-deployed-version
test must still refuse downgrade before its integrity check.

Run relevant release/context/publication and inbound regressions, then the complete
Evidence gate on the clean committed candidate against the published 2.2.2 base.
Declare the exact changed protected files, not consumed staging or broad unused
paths. Every applicable step, including metadata and full discovery, must pass.
Record command, interpreter, base/head/tree, counts, skips, failures and logs.

Require the final candidate's complete CI and `validate` aggregate, both supported
architecture builds, source-bound runtime inventories, network-disabled image
smoke, exact-image gateway/add-on lanes and actual pinned Core 2026.9.2 / ha-mcp
8.4.3 runtime lane. Bind run, attempt, jobs and artifacts to the actual checkout;
for a synthetic merge verify both parents and its tree relationship to the
candidate. Skipped steps are not executed evidence.

Review both the complete PR and release-only delta from the reviewed implementation.
Carry forward the completed implementation review and obtain a bounded release
review. Keep delivery draft until the owner exercises the repository's Ready
authority. Do not substitute another actor or manufacture approval.

## Publication and preservation

Publication must use the actual authorized merge commit, not the candidate or CI
synthetic merge merely because their trees match. Verify the annotated `v2.2.3`
tag, published non-prerelease GitHub Release, exact release-note input and appended
build identity, and version/commit image tags resolving to one OCI index. Bind
linux/amd64 and linux/arm64 manifests/configuration digests to version, source,
build timestamp and clean labels. Verify the existing controlled-input inventory,
base provenance and SBOM checks; state their cryptographic and reproducibility
limits accurately. Preserve partial publication and use exact recovery identities.

The existing guarded owner publication handoff may be needed after a workflow-token
merge. Do not dispatch duplicate publication, overwrite artifacts or weaken its
guards. Preview the exact release-note payload: consumer prose must not retain
obsolete draft-delivery instructions or private operational details.

Require unchanged directory/slug/name, image repository, ports, ingress identity,
existing options, stored formats, dependencies, workflows, trust data and frozen
stable-v1. The reviewed additions are `mcp_allowed_hosts` and
`mcp_allowed_origins`; they do not grant provider or approval authority. Preserve
tool schemas/registration, provider routing/admission, authorization, verification,
cleanup and zero fallback. Keep the separately armed observer dormant by default.
Historical arm/v7 verification remains historical; new images target the two
supported 64-bit architectures.

## Prepare installed-path verification separately

Before an authorized update, reconcile the exact received Host authorities and
present browser Origins for each intended MCP path. Do not derive inbound trust
from outbound Supervisor/Core URLs, public-host suffixes or forwarding headers.
A loopback Host is not evidence of a loopback peer. An explicit host list replaces
the defaults, and exact ports matter. Native absent-Origin clients are supported;
browser origins require explicit HTTP(S) entries. This is not CORS configuration.
See [INBOUND_SECURITY.md](INBOUND_SECURITY.md) for the complete parsing contract.

Prepare a concrete deployment/configuration proposal with exact option values,
interruption window and independent management recovery. Invalid options can
prevent both listeners from starting. Do not infer deployment, observer arming,
restart, Core update or trust activation authority from release preparation.

Use a finite, separately authorized request ledger identifying client, listener,
forwarding path, expected outcomes and identity brackets. Keep concrete endpoint,
address, authentication and household details private. Ordinary protocol capture
may initialize, enumerate the complete catalog and clean up one session. Negative
header probes must execute zero tools/call requests. No automatic retries or
alternate routes may broaden the approved scope.

## Installed acceptance gates

Record PASS, FAIL, BLOCKED or NOT RUN separately, with UTC times, safe request IDs,
arguments, provider attribution, completeness and limitations.

1. **Identity and image.** Read current Engineering/Core/Supervisor/HAOS/ha-mcp
   identities through the approved Engineering interface. Match installed 2.2.3
   source/build labels and the running container's architecture/image through the
   published index, platform manifest and configuration digest. Reusing an older
   installed-image receipt is insufficient.
2. **Authority and catalog.** Require REST/WebSocket identity agreement, all 17
   required Core capabilities, exact admitted ha-mcp 8.4.3, healthy dashboard
   authority, F3, storage and audit. Core 2026.9.2 still needs separately configured
   valid signed authority. Capture a fresh raw initialization, initialized
   notification and every tools/list page through final termination, with complete
   descriptors matching the exact 2.2.3 reference: 51 static plus 25 delegated,
   76 unique tools, held operation-status tool absent. Execute zero tools/call
   requests in capture and bracket it with identity/health observations.
3. **Intended paths.** Verify actual native clients, configured aliases and any
   supported browser path; distinguish absent from present Origin. Check stateful
   session continuity and cleanup. Verify authenticated approval-ingress access
   separately without creating plans, notifications or mutations for this check.
4. **Negative boundary.** Execute only the approved finite hostile/malformed
   header cases. Require gateway 400 for malformed/missing/duplicate Host, 421
   for a valid unlisted Host, 403 for unacceptable Origin and 431 for an excessive
   header-entry envelope, subject to parser precedence and actual transport
   handling. Attribute an upstream refusal separately from an Engineering
   refusal. Require bounded diagnostics and no SDK/provider dispatch, request-body
   processing or authority acquisition on gateway denial. Follow with a successful
   admitted read. If a proxy replaces a hostile Host, downstream acceptance does
   not prove that the original Host was evaluated.
5. **Useful behavior.** Use existing fixtures for native/delegated reads, device
   and effective-area semantics, entity exposure, search/overview, bounded service
   discovery, automation configuration and naturally retained trace, templates,
   validation and complete dashboard/configuration readback. Retain truthful
   completeness and canonical errors; a missing-target probe must be followed by
   a valid unrelated read. Do not manufacture fixtures, trigger automations or
   invoke services. Supported governed detail reads must preserve reconciliation
   identities, state and available dispatch/verification evidence.
6. **Natural dependency refresh.** Establish a valid index under current Core
   authority with a bounded ordinary dependency request: standard detail,
   include_indirect=false, limit=10, refresh_index=false. Record generation,
   build time, fingerprint and advertised 600/3600-second soft/hard expiry.
   Age naturally past soft expiry while below hard expiry, issue one ordinary
   request and record its return time. Observe health without repeat dependency
   calls that could start retries. Require background replacement after caller
   return, fresh evidence and settled resources. An unchanged fingerprint is
   acceptable. Monitoring timeout is incomplete evidence, not proof work stopped.
   Preserve the existing cooperative 300-second build deadline and its limits;
   do not force refresh, change clocks/TTLs or claim remote-abort guarantees.
7. **Dormancy and settlement.** Where the approved inspection facility permits,
   verify observer dormancy without arming it. Finish with identity/health and an
   unrelated successful read. Account for plans/tasks/events, pending approvals,
   executions/children, claims, locks, holds, leases, commits and failure/fallback
   counters against baseline. Attribute historical failures and background work;
   require no unexplained retained resources, new failures or fallback.

Receiver enforcement cannot reconstruct duplicate fields, Origin or other facts
discarded upstream. A native capture does not establish hostile browser-Origin
rejection. Missing route or receiver evidence stays BLOCKED; do not close issue
#62 solely from source tests, health counts or a successful catalog capture.

## Recovery and remaining boundaries

Stop dependent operations on identity/admission drift, fallback, uncertain
execution or persistent resource retention. Preserve the failure and continue
unaffected authorized reads. Do not bypass refusals or widen configuration to make
a test pass.

Local release-preparation recovery is an ordinary revert of its release commit,
retaining the reviewed implementation. Installed recovery needs separately
authorized exact artifact/options and compatible Core, configuration, database,
backup and management access. Returning to 2.2.2 restores the known protection
gap. Never blindly retry an uncertain write or reuse failed approvals.

Historical acceptance remains bound to its original source, pairing and time.
Complete Android navigation, cache-only startup during an outage and independent
backup-content verification were not established by that evidence. Dashboard,
helper and held-read canaries, navigation/notifications, backups, restarts and
Core updates retain their own bounded authorization. No household mutation canary
is required merely to test header refusal. Publication and successful source
validation do not establish installed-system acceptance.
