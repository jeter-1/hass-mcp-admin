# Engineering 2.2.2 acceptance

This release adds private, dormant diagnostic support for issue #62. It does not
implement Host/Origin enforcement. Keep source review, final candidate CI,
publication, installed behavior and an authorized topology capture as distinct
evidence. None of these instructions independently authorizes live operations.

## Source and release authority

Preparation base: `0602d1041dc530821ccdf552259fcd1abcec1045` (2.2.1).
Reviewed implementation: `bbd26e7fad2d73180a0a59733a6e99a4a5b99209`, tree
`6be19be743aa80b97782090abff0b61e6440a2ab`. Preserve the reviewed observer,
application integration, behavioral tests and operator contract byte-for-byte
through release preparation. Record both the complete PR and release-only
comparison, final head/tree and independent review disposition.

Use the shipped tools to validate `2.2.1 -> 2.2.2`. Stage exactly `2.2.2` plus a
newline in `.release/next-version`, require exact staged resolution of this
document and `docs/V2_2_2_RELEASE_NOTES.md`, inspect the promotion preview and
apply only under release-preparation authority. All three version declarations
must become 2.2.2, the declaration must be consumed and active resolution exact.

Run affected release/context/publication and observer tests, then complete
Evidence on the clean committed candidate against the verified preparation
base. Declare only the four changed protected files: application.py, the new
inbound_topology_observer.py, version.py and Engineering config.yaml. Preserve
the earlier unchanged-version failure; the legitimate release transition must
close it without changing gate rules. Record actual commands, counts, skips,
exit statuses and source identities; do not duplicate a passing full suite.

Require every existing CI family and the aggregate `validate`, including both
actual amd64/arm64 builds, network-disabled smoke and runtime inventories, all
exact-image/add-on lanes and the pinned Core 2026.9.2 / ha-mcp 8.4.3 runtime
lane. Inspect helper planning/execution, refusal and cleanup results, not just
packaging or capability counts. Bind runs, attempts, jobs and artifacts to the
candidate. Verify both parents and tree of any synthetic merge checkout.
Skipped or unavailable work is not passing evidence. Obtain a bounded review
of the release delta; the earlier implementation review does not review it.

The existing installation name/slug, image repository, ports, ingress, options,
existing stored formats, dependencies, workflow permissions, signed trust data,
stable-v1 and historical acceptance documents must remain unchanged. Supported
platforms remain linux/amd64 and linux/arm64. Preserve historical arm/v7 artifacts.

## Publication and installed acceptance

Under separate publication authority, verify the actual merge/source/tree,
clean build labels/time, annotated `v2.2.2` tag, stable release classification,
version/commit image tags and their common OCI index. Verify both platform
manifests/configuration digests, source-bound runtime inventories and resolved
base provenance under [BUILD_INPUTS.md](BUILD_INPUTS.md). Metadata/subject hashes
do not establish independent attestation signer authentication, byte-for-byte
reproducibility or independently executing the published digest.

Use the existing guarded publication handoff. A workflow-token merge may need
owner dispatch; never substitute a candidate or synthetic merge SHA for the
actual reviewed release source. Reconcile partial/ambiguous publication before
any retry. Publication does not deploy the add-on.

After separately authorized deployment, record PASS, FAIL, BLOCKED or NOT RUN:

1. **Identity/image:** fresh Engineering/Core/Supervisor/HAOS/ha-mcp identities;
   actual installed container/architecture connected to the published 2.2.2
   index, platform manifest and configuration digest; matching clean labels.
   A displayed version or previous installation receipt alone is insufficient.
2. **Authority/catalog:** REST/WebSocket agreement, all 17 Core capabilities,
   exact reviewed ha-mcp 8.4.3 admission, dashboard authority, storage/audit
   health and F3 readiness. One fresh public catalog session must preserve raw
   initialize/initialized and all tools/list pages, exact cursor continuity and
   every descriptor field against the 2.2.2 reference: 51 static plus 25
   delegated, 76 unique, held operation status absent, zero tools/call. Bracket
   it with identity/admission observations. Counts are not raw catalog proof.
3. **Useful reads:** bounded existing entity/state, device/effective-area,
   exposure, service discovery, automation/retained trace, template, config
   validation, complete dashboard and reconciliation-detail reads. Preserve
   attribution, completeness, canonical errors and zero fallback. Follow one
   missing-target probe with an unrelated valid read. Do not manufacture data
   or execute mutations to demonstrate read behavior.
4. **Dependency refresh:** establish a valid index and its generation/build
   time/expiry with a confirmed fixture and bounded `refresh_index=false`
   request. Age naturally past the 600-second soft TTL below the 3600-second
   hard TTL, issue one ordinary query and observe health until replacement
   completes after caller return. Require fresh replacement and settlement;
   unchanged fingerprint is acceptable. Do not force refresh or start retries
   through repeated dependency queries. The 300-second deadline is cooperative;
   a monitoring timeout is incomplete evidence.
5. **Dormant behavior/settlement:** establish that no observation arm is active
   during ordinary acceptance through the separately approved narrow operator
   facility. Preserve approval ingress and existing behavior. Finish with
   identity, an unrelated read and settled executions/children/claims/locks/
   holds/leases/commits. Attribute historical counters and background work;
   require no new unexplained failures, authority retention or fallback.

Use the same technical Beta installation; no second installation or migration.
Core 2026.9.2 requires separately configured valid signed authority. Neither this
installation nor a successful connection creates trust or overrides denials.
Older compiled pairings remain supported; no future-version blanket claim is
permitted. Historical installed acceptance retains its original source/time.

## Separately authorized topology capture

The exact contract is [INBOUND_TOPOLOGY_CAPTURE.md](INBOUND_TOPOLOGY_CAPTURE.md).
Before requesting one live authorization, bind the published source/image,
current installed target and forwarding components, operator facility, private
tool hashes, new absent output paths, clock/identity bracket, recovery access,
exact arm/session/export/cleanup and any required installation/restart count.
The reviewed source-only launcher refuses live use until those bindings exist.
Do not invent remote commands, add a header endpoint or enable broad logging.

Require one valid source-bound private arm (validity at most 900 seconds), an
exclusive consumed claim, and at most 180 monotonic seconds of capture after
MCP readiness, also limited by absolute expiry. Observe only matching synthetic
request markers. Preserve the receiver's 48-record/131072-byte export bounds,
all per-header/record bounds and the client's existing request/time/size limits.
Use initialize, initialized and bounded tools/list with permitted transport
cleanup; execute zero tools/call and no automatic new session or retry.

Verify raw protocol/catalog, correlation indexes and every HTTP ledger entry,
including GET/DELETE; missing receiver records, parser/proxy refusals or omitted
unsafe values remain explicit gaps. Require independently established identity,
admission and clock/route agreement before declaring the paired evidence
complete. Keep hostnames/addresses private; exclude paths, bodies, credentials,
cookies and session identifiers. The receiver observes parser-produced scope
before Uvicorn proxy rewriting, not raw packets or upstream transformations.
Proxy-supplied values alone never establish trusted-forwarder authority.

Demonstrate recorder retirement and resource cleanup without application
interruption. A crash, failure or partial export is not durable success; file
writes/fsync depend on kernel/storage progress. Do not extend deadlines, erase
consumed claims or automatically reuse attempts. Preserve evidence before any
separately approved exact inactive-arm cleanup. Expiry does not remove compiled
instrumentation. Issue #62 remains open pending an appropriate security policy
and its own implementation, review and deployed-path validation.

## Recovery and remaining limits

Stop dependent work for identity drift, lost authority, fallback, uncertain
execution, unsafe evidence export or unexplained retention. Preserve failure
evidence and continue unaffected authorized reads. Do not bypass denials, edit
cache/lifecycle data, switch providers or blindly repeat operations.

Local release recovery is an ordinary revert of the release-preparation commit,
retaining the reviewed implementation. Any installed recovery must name a
verified compatible prior artifact and independent management access, reconcile
Core/configuration/database/backup state and active work, and preserve partial
evidence. Returning to 2.2.1 retains the existing Host/Origin protection gap.

Full Android navigation, cache-only outage startup and independent backup-content
verification remain unestablished. Helper/dashboard canaries, restoration,
notifications, backups, restart recovery and Core updates retain their own
bounded authorization. Ordinary read acceptance requires no new mutation canary.
Keep household identifiers and private evidence out of public release records.
