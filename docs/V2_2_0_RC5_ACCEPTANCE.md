# Engineering 2.2.0-rc.5 acceptance

RC5 prepares the independently reviewed RC4-DR-1 integration identifier
correction. Final review state requires all three authoritative Engineering
versions to be `2.2.0-rc.5`, with `.release/next-version` consumed. Stable
remains 1.1.2. Materialization, source review and candidate CI do not establish
publication, installed identity or live restoration.

The household Core was last observed as **2026.9.1**, already updated, with
Engineering RC4. This is the corrected-build acceptance path on that
installation, subject to fresh authorized identity readback. No additional
Core update or promotion to stable is part of RC5 preparation or acceptance.

## Source and release authority

Published RC4 / complete-PR release-validation base:
`535bfcc05b35c1c35fb31ecb404d46e12e590cbb`.
Independently reviewed correction:
`bf0312ada55f7611014c6a186ccea72f2faf5c59`.
Record the final RC5 head/tree and keep two comparisons explicit: published RC4
to final head, and reviewed correction to final head. Keep
`ha_core_readmission/device_registry.py` and
`tests/test_rc4_device_identifier_contract.py` byte-identical to the correction.
The release delta changes only authorized version declarations, current release
references, these version-specific documents and necessary current expectations
in `tests/test_rc2_release.py`.

Resolve this document with `python scripts/codex-context.py --format json`.
Require exact staged RC5 document resolution before materialization, then exact
active RC5 acceptance/release-note resolution, three matching declarations and
no unexplained context inconsistency. Use published RC4 as the release-validation
base. Preserve failed unchanged-RC4 metadata evidence; pass the legitimate
RC4-to-RC5 transition without changing validator rules or comparison baselines.

The compiled compatibility authority is unchanged:

| Core | Exact source commit | Reviewed OCI index |
| --- | --- | --- |
| `2026.9.0` | `dfb5a9e690daaf204b542896e4b595e61a11a401` | `sha256:372d991e58882a1d8c68c07e9aa3f3b509276e695355f73ccdb03baa70407293` |
| `2026.9.1` | `fc034572d0216a04ed40a07154394908a594dfed` | `sha256:612d76760b544cb40b7ba01387fdac964c59a6a550a50a4d30b4773c822d2918` |

These retained authorities are not proof of the installed Core image or an
upgrade proposal. Previously admitted releases remain unchanged; an unlisted
patch cannot admit itself. See the
[immutable authority fixture](../tests/fixtures/ha_core_2026_9_authority.json)
and compiled capability profiles.

The intended internal pairing remains exact reviewed ha-mcp 8.4.3, entry
`ha-mcp-v8.4.3-d5cea47a`, source
`eac7a3aa7063432e9af17e7d7726040e909c7b8f`, OCI index
`sha256:d5cea47a0115e5d161c2b319ee637b1b0a5bcfafe1597cb490299bbbc6329456`.
Engineering provides one public endpoint/catalog through Nabu Casa, with
admitted delegated and native providers selected internally. Provider
unavailability supplies no authority for another route or fallback.

## Correction and observed evidence

RC4 reused the 128-character registry-ID/reference predicate for integration
identifier string pairs. RC4-DR-1 separates those contracts: a bounded collection
of two-string pairs preserves its text without truncation, normalization or an
arbitrary small text-length cap. The global 128-character registry-ID/reference
limit, connections validation, pair type/arity, collection and canonical byte
bounds, duplicate/parent/cycle checks and capability-local refusal remain intact.
The exact reviewed Core contract defines these integration identifiers as string
pairs; they are not registry target identities.

Independent source review accepted `bf0312ad` for the bounded correction and
subsequent release preparation, with **356 independently passing tests**, zero
skips/failures/errors. That verdict covers the correction, not the later RC5
release delta or its CI. The review's final remote DNS limitation must be closed
by successful current remote verification before delivery.

A separately authorized one-shot comparison completed at approximately
`2026-09-09T20:46:28Z` on the RC4/Core 2026.9.1 installation. Both exact validators
assessed separate unchanged copies of the same complete fresh 261-record
response. Unchanged RC4 rejected zero-based record 63: an integration identifier
pair contained strings of lengths 4 and 149. The candidate accepted all 261
records and completed whole-registry relationship checks, without modifying
either input. Collection completeness, source binding, identity continuity and
cleanup passed. There were no child records, so this response does not supply
positive live parent/child coverage.

The exact failing predicate is now observed. The narrower historical limitation
remains: this later response does not identify the response that created
authority generation 1. No raw household registry or identifier values were
exported. The comparison ran the candidate transiently; it did not install it or
publish authority. The surrounding RC4 readbacks still reported 14 compatible
and 3 unavailable capabilities, 71 tools and an unbuilt dependency index.
Thus comparison success is not live incident resolution.

Preserve the independent review, original diagnostic, failed launch, successful
comparison receipt and their hashes. The complete-registry receipt SHA256 is
`8d6d765e3a0019c6f5860ba8a6ba87331b225fd9c4f8fa2bd01df7b7942b958c`.
Keep local artifact locations and full evidence inventories in delivery records;
synthetic fixtures must not be represented as captured household records.

## Retained requirements and validation

Prove useful successful behavior and refusal through production probe,
projector/coordinator, gateway, dependency and transport-authorization boundaries
with synthetic network responses. Cover the 4/149 shape, valid controls,
malformed pair types/arity, unchanged connections behavior, invalid registry
IDs/references, later malformed records, duplicates, parents and count/byte
limits. Verify the three affected profiles:
`core.delegated_device_effective_area`, `core.dependency_helper_planning` and
`core.direct_device_registry_read`. Invalid evidence must still withhold only
dependent capabilities while unrelated authorized reads remain useful.

Preserve [RC4's display-text and reconciliation corrections](V2_2_0_RC4_ACCEPTANCE.md),
[RC3's shared-build requirements](V2_2_0_RC3_ACCEPTANCE.md) and
[RC2's earlier corrections](V2_2_0_RC2_ACCEPTANCE.md). In particular:

- Display strings remain separate from target identities. Failed recurring
  supervisor reconciliation retires authority immediately, coalesces redundant
  invalidation and waits the existing 300-second interval. Recovery can take one
  interval plus probe time; this is not a global request-rate limit.
- Dependency builds retain manager-owned authority, shared waiters, prewarm,
  foreground/background success, per-read revalidation, cancellation/shutdown
  cleanup, late-callback refusal and source/post-lock freshness fences. Their
  separate 300-second build deadline is cooperative. Soft/hard TTLs remain
  600/3600 seconds. Already-dispatched reads may finish after caller cancellation;
  no remote-abort guarantee is established.
- Raw REST NaN/Infinity/-Infinity rejection, conservative connection failures,
  valid responses, composite-read authority, capability-local services timeout,
  bounded fresh authenticated WebSocket replacement and R182-1 cleanup remain.

The healthy exact pairing requires **all 17 Core capabilities** and **51 static
tools plus 25 delegated reads, 76 total**. `ha_get_operation_status` remains held
and absent from ordinary registration. Public schemas, provider contracts,
admission policy, attribution, routes, permissions, dependencies, workflows,
Dockerfiles, TTLs and stable-v1 are unchanged. Governance task schema remains 1
and approval authority remains 3. No new write, forwarding or fallback exists.

| Evidence stage | Required record |
| --- | --- |
| Independent source review | Exact correction verdict and 356-test execution; retained separately from release-delta review. |
| Complete-registry comparison | Separate baseline FAIL/candidate PASS, complete count, unchanged inputs, identity/cleanup and historical-attribution limitation. |
| Final candidate | Clean final SHA/tree, both comparisons, unchanged correction hashes, materialized RC5 and exact documents. |
| Local validation | Focused release tests and complete Evidence gate on final committed head against RC4; commands, counts/skips and all applicable steps passing, including metadata. |
| Independent release review | Bounded separate reviewer verifies both ranges, preservation, scope, versions, documentation, security and validation evidence. |
| Candidate CI/build | Run/attempt IDs, tested commits, conclusions, skipped jobs and relevant logs/artifacts; bind synthetic merge parents and tree. Earlier CI is separate. |
| Future publication/installation | Authorized release commit, immutable artifacts/provenance, installed architecture manifest and configuration digest binding, clean labels. |
| Future live restoration | Fresh catalog, restored capabilities/reads, natural dependency refresh, settled resources and separately authorized canaries. |

Full discovery on the final candidate must include
`tests.test_rc4_device_identifier_contract`, `tests.test_rc3_postcore_regressions`,
`tests.test_dependency_build_authority`, `tests.test_beta39_fenced_refresh` and the
retained six-module read/cancellation/Core selection. Record actual execution
separately from inspected historical logs. Forced faults and cancellation stress
belong in disposable tests, not the household installation.

## Corrected-build live acceptance

After separate publication, deployment and bounded read-only authorization:

1. Read actual Engineering, Core, Supervisor, HAOS and ha-mcp identities. Expect
   RC5 and last-observed Core 2026.9.1, subject to readback. Resolve drift before
   dependent work. Bind the running Engineering image to the published RC5 OCI
   index, correct platform manifest and configuration digest, exact source,
   build timestamp and clean labels. A displayed version or old image receipt
   does not establish this installation.
2. Initialize a fresh client through the existing public Engineering Nabu Casa
   endpoint. Capture every raw `tools/list` page and complete descriptors with
   cursor continuity. Compare all public schemas/metadata against exact RC5
   source and dependencies. Require 76 unique tools, no duplicate/missing pages
   and no ordinary `ha_get_operation_status`. Missing image or raw catalog
   facilities is BLOCKED; cached inventory or health counts cannot substitute.
3. Capture `server_info`, `get_server_health(check_ha=true)` and
   `list_capabilities`. Require REST/WebSocket identity agreement, all 17 Core
   capabilities, exact ha-mcp admission, dashboard authority, healthy storage/
   audit and F3 readiness. Explain any withheld capability.
4. Exercise restored `ha_get_device`, `ha_get_entity`, `ha_get_entity_exposure`,
   `ha_get_overview` and `ha_search` on confirmed fixtures with small bounds.
   Compare IDs, device/effective-area and exposure semantics with authoritative
   records. Do not require a parent/child fixture where none exists. Also test
   useful unaffected entity/state, service discovery, automation configuration
   and complete storage-dashboard reads. Preserve provider attribution,
   completeness/truncation, errors and zero fallback.
5. Let prewarm/initial build settle. Record dependency generation/build time,
   freshness, reported TTLs/expiries, source epoch/invalidation, build/failure
   counters and settled authority. Age naturally past soft expiry while below
   hard expiry; do not alter TTLs or invalidate/force a foreground build. Issue
   one ordinary bounded dependency query with `refresh_index=false` and record
   its completion. Explicit stale disclosure is permitted before hard expiry.
6. Observe health about every 15–30 seconds for up to 330 seconds, without
   repeating dependency queries that could hide failure by starting a retry.
   Require the retained build to finish after the initiating request returns
   and publish a newer generation/build timestamp. Then require fresh coherent
   replacement evidence from another ordinary query. An unchanged fingerprint
   is permissible; `refreshed=false` alone does not refute replacement. Missing
   timing or replacement evidence is BLOCKED. Distinguish device-probe/readmission
   failure from dependency-build failure.
7. After work settles, compare leases, commits, capacity exhaustion and fallback
   with baseline, accounting for scheduled background work. Persistent
   unexplained retention fails acceptance. Finish with an unrelated successful
   Core read. Preserve pre-existing issues, including unchanged projection
   failures, separately from new regressions.

Complete representative bounded templates, one configuration validation,
dependency/reliability analysis, exact dashboard rereads and a validly shaped
missing target followed by a successful unrelated read. Use naturally existing
traces; do not trigger an automation to manufacture evidence.

## Canaries, recovery and disposition

Only after identity/image, catalog, authority, useful-read and automatic-refresh
gates pass, prepare exact canary targets, pre-state, arguments, consequences,
consumer-coverage uncertainty, verification and restoration for separate bounded
authorization. Preserve every shipped panel approval: `approve_change_plan`
requests approval; it does not grant it.

The held-read canary requires its exact reviewed compatibility entry and one
synthetic missing operation ID. Require `resource_not_found`, `retryable=false`,
`promotion_performed=false`, unchanged registration, settled resources, zero
fallback and an unrelated successful read. Do not manufacture a positive device
operation. For an existing dedicated test helper, revalidate consumers/state,
approve one opposite-state plan, apply once and verify task plus authoritative
readback; use a fresh approved restoration plan and verify the original state.

For an existing dedicated storage dashboard, capture complete configuration and
both hashes, exclude concurrent edits, approve one supported cosmetic patch,
apply once and verify configuration and actual-client rendering. Approve the
supported inverse plan and verify original configuration, hashes and rendering.
Dashboard writes remain non-atomic against external editors. Uncertain dispatch,
verification or external drift requires task/status and authoritative-state
reconciliation before further mutation; never blindly retry or overwrite edits.

Android navigation retains its separately authorized harmless plan, notification
count, authenticated inbox navigation, rejection/clearing and no dispatch.
Restart recovery requires one exact component/count, no active mutations or
unfinished restoration, usable backup and recovery access. After an authorized
restart verify the same artifact, fresh catalog/authority, storage/F3, prewarm
or initial build, useful reads and settled resources; do not reuse old plans.

Before authorized deployment recovery or restart, verify a full backup, selected
contents including Engineering/ha-mcp data, completion, timestamp, size and an
off-host copy. Account for external databases, emergency-kit availability without
exposing its key, local access surviving connector loss, overwrite consequences
and compatible restored Core/configuration/database state. This document does
not authorize creating backups, restarting, or changing Core.

Published RC4 at `535bfcc05b35c1c35fb31ecb404d46e12e590cbb` remains immutable;
returning to it restores the observed integration identifier defect. RC3 also
has the display/reconciliation defects and RC2 the shared-refresh defect.
Neither old Engineering artifacts nor stable-v1 supply a complete recovery
procedure for the updated installation. Select compatible recovery artifacts
and backup state under separate authority.

Before merge, leave the draft unused or revert only release preparation while
preserving the reviewed correction/history. Stop dependent mutations for identity
drift, missing authority, fallback, uncertain dispatch, failed verification or
restoration, persistent retention or unusable recovery. Continue unaffected
authorized reads. Report PASS, FAIL, BLOCKED or NOT RUN separately for each
evidence stage and live gate, with UTC timestamps, exact identities, sanitized
arguments, request IDs, actual provider, completeness and recovery status.

Leave the delivery PR draft for Josh. His later Ready action may authorize the
controlled merge/publication under repository policy. Deployment and live
acceptance remain separate, and no additional Core update is requested.
