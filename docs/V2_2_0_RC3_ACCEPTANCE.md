# Engineering 2.2.0-rc.3 acceptance

Engineering 2.2.0-rc.3 is the materialized source candidate for RC2-SR-1:
shared dependency builds own authority independently of requesting callers.
The advertised Engineering source is 2.2.0-rc.3,
`.release/next-version` has been consumed, and stable remains 1.1.2.
Materialization is local release preparation; it does not establish publication,
deployment, installed identity, or live acceptance.

## Source and release authority

The release-validation base is published RC2 commit
`634951157869db79cbaffee272f690a1c969b3c0`. The independently reviewed
correction is `f5be5f99cb3373164f7a25814ab9c8e75af53f01`. Bind the complete
PR to the published base and the release-preparation delta to that reviewed
correction. Record the final committed RC3 head in delivery evidence. The three
dependency implementation files and `tests/test_dependency_build_authority.py`
must remain identical to the reviewed correction unless separately scoped and
reviewed.

RC3 preserves RC2's exact compiled Core and ha-mcp contracts; it adds no
admission-policy expansion. Core 2026.8.1 remains the pre-update acceptance
baseline, subject to actual readback. The reviewed Core update targets remain:

| Core | Source commit | OCI index |
| --- | --- | --- |
| `2026.9.0` | `dfb5a9e690daaf204b542896e4b595e61a11a401` | `sha256:372d991e58882a1d8c68c07e9aa3f3b509276e695355f73ccdb03baa70407293` |
| `2026.9.1` | `fc034572d0216a04ed40a07154394908a594dfed` | `sha256:612d76760b544cb40b7ba01387fdac964c59a6a550a50a4d30b4773c822d2918` |

The internal pairing is exact reviewed ha-mcp 8.4.3: source commit
`eac7a3aa7063432e9af17e7d7726040e909c7b8f`, source tree
`ffc545fa7e3ad683737454de0217e2b9f672589e`, OCI index
`sha256:d5cea47a0115e5d161c2b319ee637b1b0a5bcfafe1597cb490299bbbc6329456`.
Use the unchanged [immutable authority fixture](../tests/fixtures/ha_core_2026_9_authority.json),
compiled profiles and semantic registry as source evidence. The dependency
semantic-registry fingerprint remains
`857e344b0f14ee3088e641ff3b1fd10657b9d1266dda54a1707025de18d5dcdc`.
The existing 17 binary-owned capability profiles and previously admitted Core
releases are unchanged. An unlisted patch or live descriptor cannot admit itself.

## RC2-SR-1 functional requirements

Published RC2 could retain the initiating request's telemetry in a shared
dependency task. When that caller returned or cancelled, its closed authority
could refuse subsequent scan reads, preventing automatic refresh from completing.
RC3 gives the manager-owned build its own bounded authority lifetime.

Source tests must exercise the production gateway, runtime, index, provider,
Core coordinator, and REST/WebSocket authorization boundaries with synthetic
network responses and scheduling. Require useful success and the following
failure-preservation evidence:

- Startup prewarm, initial build and foreground refresh succeed. Startup
  connectivity authority closes separately from the shared build's authority.
- Every shared build has fresh telemetry and exact
  `core.dependency_helper_planning` authority, preserving caller attribution
  and correlation without borrowing the completed caller's dispatch permission.
- Authority is consumed before the first provider interaction. The same active
  authority is revalidated before each subsequent read and before publication;
  a retired generation never reacquires authority midway through a scan.
- Concurrent waiters share one build. Soft refresh finishes after its originating
  request returns. Initiator cancellation, including cancellation before the build
  begins, does not cancel a build retained by the manager or another waiter.
- A 300-second cooperative build deadline bounds queued work and the scan.
  Deadline checks refuse later dispatch and publication after expiry.
  Cancellation remains cooperative: already-dispatched reads may finish.
  No remote-abort or unconditional wall-clock termination guarantee is established.
- Shutdown cancels owned work, including a provider read, refuses new builds and
  prevents an already waiting fenced caller from creating replacement work.
- Every exit closes dispatch callbacks and releases unconsumed authority or
  finishes consumed authority exactly once. Check cleanup counts and retained
  leases/commits for success, provider failure, transport timeout, whole-build
  deadline, acquisition refusal, cancellation and shutdown.
- Captured or queued callbacks refuse after completion, cancellation, deadline
  or retirement. No next dispatch occurs after retirement between reads; a
  retired scan cannot publish evidence as current, including after its last read.
- Source epochs and invalidation remain effective. A scan begun before a
  governed post-lock freshness fence cannot satisfy that fence. Completion
  cannot clear a newer invalidation or present invalidated evidence as current.
- Evidence TTLs remain 600 seconds soft and 3600 seconds hard. Valid stale
  evidence remains usable with explicit stale disclosure before hard expiry;
  expired or invalidated evidence is refused when fresh replacement is unavailable.
- An unrelated authorized Core read can still acquire, consume, revalidate and
  finish authority after each failure/cancellation scenario.

Preserve the original diagnostic and earlier source-review/Evidence records as
historical evidence. Do not replace the authorization boundary with a mock, clear
telemetry to exploit a default, extend caller authority, change TTLs or gate
rules, force every refresh to block its caller, retry an uncertain dispatch, or
select a fallback provider.

## Preserved RC2 acceptance

RC3 retains the functional and execution-integrity requirements established by
[RC2 acceptance](V2_2_0_RC2_ACCEPTANCE.md), with shared-build ownership and
recovery limitations clarified here. This version-specific document controls RC3.

Revalidate raw REST rejection of NaN, Infinity and -Infinity across `/config`,
`/states`, `/services` and `check_config`, with the established capability-local
authority consequences. Preserve conservative connection failures and valid
responses, composite-read authority before every observation, capability-local
service timeouts, and bounded freshly authenticated WebSocket replacement with
the same reviewed version and epoch. A timed-out WebSocket is never reused.

Retain R182-1 held-read cancellation cleanup, late/duplicate callback refusal,
and capacity for an unrelated Core read after 64 cancelled canaries.
`ha_get_operation_status` remains held and absent from ordinary registration;
its missing-resource canary reports canonical `resource_not_found`,
`retryable=false`, `promotion_performed=false`, and zero fallback.

Preserve device/effective-area semantics for `ha_get_entity`, `ha_get_device`,
`ha_search`, `ha_get_overview` and `ha_get_entity_exposure`; the older 8.4.1
adapter on Core 2026.9 still withholds only those five reads. Preserve template
dictionary/State/area/device behavior, configuration-validation classifications,
independent trace authority, capability-local state failures, and exact
dependency/helper, F3, lifecycle, dashboard and post-intent readback boundaries.

Public schemas and provider attribution are unchanged. The healthy reviewed
pairing exposes 51 static tools plus 25 delegated reads: 76 total. Admission is
per capability, so a degraded count must be explained rather than treated as
success. Task schema remains 1 and approval authority remains 3. Governance,
permissions, routing, dependencies, workflows, Dockerfiles and stable-v1 are
unchanged. There is no new write, forwarding, provider route or fallback.

## Separate evidence stages

Record each check as PASS, FAIL, NOT RUN or BLOCKED with UTC timestamp, exact
source/deployed identity, sanitized arguments, expected and actual results,
provider, completeness/truncation, verification and recovery status.

| Stage | Required evidence |
| --- | --- |
| Source | Exact clean materialized head, both comparison ranges, unchanged reviewed correction, focused tests and complete Evidence gate against published RC2. |
| CI/build | Exact candidate run/attempt IDs, all required jobs, logs, artifacts and tested revisions; bind synthetic merge parents to the candidate and tested base. Record packaging and architecture evidence separately from source tests. Historical RC2 CI does not validate RC3. |
| Publication | Separately authorized immutable RC3 release commit, OCI index, architecture manifests, labels and provenance. Candidate CI does not prove publication. |
| Installed image | Authorized operator evidence binds the installed architecture manifest to the approved RC3 OCI index and source/build identity. A displayed version alone is insufficient. |
| Public catalog | Fresh client session through the existing Engineering Nabu Casa connector; complete paginated tool names and schemas compared with the release contract. A cached list or health count is insufficient. |
| Live acceptance | Separately authorized corrected-build smoke, automatic refresh, regression and concrete canaries with readback/restoration. Source and CI cannot establish this result. |

Installed architecture/image binding and fresh complete public-catalog capture
remain unresolved operational evidence gaps. Collect them with authorized
existing operator/client facilities; add no runtime capability to obtain them.
An unavailable catalog page is an evidence gap, not a pass.

## Corrected-build smoke on the existing Core

After separately authorized publication/deployment and read-only live testing:

1. Read actual Core, Supervisor, HAOS, Engineering and ha-mcp identities without
   changing them. Resolve this document exactly from the deployed release
   checkout. If Core has already changed, report it and use applicable
   post-update checks; never infer deployed identity from reviewed source.
2. Start a fresh Engineering connector session. Capture `server_info`,
   `get_server_health(check_ha=true)`, `list_capabilities` and the complete
   public catalog. Verify REST/WebSocket identity agreement, Core/provider and
   dashboard authority, storage/audit health and F3 readiness.
3. Perform bounded entity, service-discovery, automation-configuration and
   storage-dashboard reads. Record actual provider, elapsed time, errors,
   completeness and truncation. Require useful results and zero fallback.
4. Record dependency evidence generation/source epoch, build time, age,
   invalidation state and authority counters through existing observable
   health/result evidence. Allow the unchanged soft TTL to elapse naturally.
   Issue a bounded dependency query that starts automatic refresh without
   forcing a foreground refresh. The originating request may return stale
   evidence with disclosure; verify the retained build then completes.
5. Obtain fresh replacement evidence with a newer build/generation and coherent
   source epoch. After requests settle, compare `issued_lease_count`,
   `active_commit_count` and capacity-exhaustion counters with baseline.
   Account for legitimate background work; unexplained persistent retention is
   a failure. Then perform an unrelated successful Core read.

Missing refresh-generation observability must be disclosed, not replaced with a
success claim based only on one successful response. Keep forced timeouts,
malformed responses, network faults and cancellation stress in disposable tests.

## Remaining operational gates

After corrected-build smoke succeeds, resume the bounded regression selection:
entity/device relationships, existing automation configuration and traces,
small read-only templates, one `check_config`, dependency/reliability analysis,
dashboard reread, and one validly shaped missing target followed by an unrelated
successful Core read. Do not trigger an automation to manufacture a trace.

Each canary needs concrete authorization bound to target, arguments, pre-state,
effects, verification and recovery. The held-read canary is evidence only; do
not initiate an operation to manufacture a positive operation ID. For an
existing test input_boolean, inspect consumers, capture original state, approve
the exact opposite-state plan through the authenticated panel, apply once and
verify; use a fresh approved plan to restore and verify the original state.
For an existing dedicated storage dashboard, capture complete configuration
and both fingerprints, exclude concurrent UI edits, approve one supported
cosmetic patch, apply once, verify exact reread and actual-client rendering,
then approve and verify the supported reverse update. Dashboard writes are not
atomic against external editors. Uncertain dispatch requires task/status and
authoritative-state reconciliation before any further mutation.

Outstanding Android approval-navigation acceptance requires a separately
authorized harmless notification/plan rejection, correct authenticated inbox,
notification clearing and no execution task or dispatch. Restart recovery needs
a verified backup, an exact component/restart count and a mutation-free window;
then recheck identity, storage, F3, fresh authority, catalog and useful reads.
Do not reuse pre-restart plans for new dispatch.

Before any Core update, resolve material failures, read Repairs and relevant
logs, review integration/custom-component compatibility and intervening official
release notes, and select one exact reviewed stable target. RC3 retains only
the reviewed 2026.9.0/2026.9.1 authority for this transition; a newer unreviewed
patch stops this path. Verify exact ha-mcp 8.4.3 and the five device-dependent
reads. Establish local and recovery access that survives connector failure.

Under explicit backup authorization, create and verify a fresh full backup:
completion, selected contents (including Engineering/ha-mcp data), size,
timestamp and an available copy outside the HA host. Account for external
database recovery. Josh confirms the emergency kit is available without
revealing its key. An automatic/partial backup setting is insufficient.
Record installation-specific restoration steps, overwritten state and how
restored Core/configuration/database consistency will be verified.

Present exact current/target Core versions, maintenance window, interruption,
verified backup, recovery and post-update acceptance for Josh's separate
authorization. Keep Engineering, ha-mcp, HAOS and unrelated components unchanged
during the Core-only update. Post-update smoke, restart recovery and reversible
helper/dashboard canaries remain required; pre-update acceptance cannot replace
them. Issue separate conclusions for smoke, regression, each canary/restoration,
Core-update readiness and eventual post-update acceptance.

Stop dependent work for identity mismatch, required-authority loss, fallback,
uncertain dispatch ownership, unverified writes, failed restoration, persistent
resource retention or unusable recovery. Continue unaffected read-only
investigation within its authorization.

## Prior artifact and recovery

[Published RC2](https://github.com/jeter-1/hass-mcp-admin/releases/tag/v2.2.0-rc.2)
remains immutable at commit `634951157869db79cbaffee272f690a1c969b3c0`, OCI index
`sha256:50683954d463657557135bfc3779c7fb1d2251a60ca02e7501fbc755dd4f69f0`.
Preserve its tags, images, notes and evidence. RC2 has the known RC2-SR-1
background-refresh defect; returning to it restores that defect.

Before merge, withdrawal can leave/close the draft or revert local release
preparation. After merge, any corrective revert or new release needs its own
scope and must preserve immutable artifacts. After deployment, an authorized
rollback must identify a compatible artifact and verify identity, storage, F3,
provider/Core authority, catalog and bounded reads. Neither RC2 nor RC1 alone
is a complete Core-upgrade recovery procedure. Recovery after a Core update
must account for the exact Core version and configuration/database state and
the verified backup's overwrite effects.

Josh's later Ready action carries the repository's bounded merge/publication
authority. Draft delivery does not invoke it. Deployment, live testing, backup,
restart and Core update remain separately authorized.
