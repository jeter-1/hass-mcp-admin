# Engineering 2.2.0-rc.3 release notes

Engineering 2.2.0-rc.3 is the materialized source candidate correcting RC2-SR-1,
the shared dependency background-refresh authority failure. The advertised
Engineering source is 2.2.0-rc.3, `.release/next-version` has been consumed,
and stable remains 1.1.2.

## Shared dependency builds own their authority

In RC2, a shared scan could retain the initiating request's telemetry. After
that request returned or cancelled, its closed authority could refuse later
reads and prevent automatic dependency evidence replacement.

Each RC3 shared build receives fresh telemetry and exact
`core.dependency_helper_planning` authority while retaining caller attribution
and correlation. It consumes authority before provider interaction, revalidates
the same active authority before every later read and before publication, and
releases or finishes resources exactly once on exit. It cannot reacquire a
retired generation midway through a scan.

Startup prewarm, initial builds and foreground refresh remain supported.
Concurrent waiters retain one shared build; a caller can return stale evidence
or cancel while the manager-owned refresh completes. Shutdown still cancels
owned work and prevents waiting callers from spawning replacement work.
Captured or queued callbacks refuse after closure, cancellation, expiry or
retirement, and an invalidated or retired scan cannot publish evidence as current.
Source epochs and governed post-lock freshness fences remain effective.

The build has a 300-second cooperative deadline. Evidence TTLs remain
600 seconds soft and 3600 seconds hard, with unchanged stale disclosure and
refusal after invalidation/hard expiry when fresh evidence is unavailable.
Cancellation does not guarantee remote abortion: already-dispatched reads may
finish, and the deadline is not an unconditional wall-clock termination promise.

## Compatibility and boundaries

RC3 preserves RC2's exact Core compatibility, including the 2026.8.1 baseline
and reviewed 2026.9.0/2026.9.1 targets, and exact internal ha-mcp 8.4.3 pairing.
It preserves capability-local malformed-state/service handling, conservative
connection failures, raw non-finite JSON rejection, per-read authority checks,
fresh bounded WebSocket replacement and R182-1 held-read cancellation cleanup.

Engineering remains the one public endpoint and catalog through Nabu Casa,
selecting admitted ha-mcp or native providers internally. The healthy reviewed
pairing retains 51 static tools plus 25 delegated reads, 76 total.
`ha_get_operation_status` remains held and absent from ordinary registration.
Public schemas, provider contracts/attribution, admission policy, routing,
governance, permissions, dependencies, workflows, Dockerfiles and stable-v1
remain unchanged. Task schema remains 1, approval authority remains 3, and
fallback remains zero. No new write or forwarding surface is introduced.

## Evidence and operational acceptance

The correction was independently reviewed at
`f5be5f99cb3373164f7a25814ab9c8e75af53f01` against published RC2 commit
`634951157869db79cbaffee272f690a1c969b3c0`. That verdict covers the correction;
the RC3 release-preparation delta requires its own focused independent review.

[RC3 acceptance](V2_2_0_RC3_ACCEPTANCE.md) controls this version's source,
CI/build, publication, installed-image, public-catalog and live evidence.
Record actual final-head validation and candidate CI separately; historical
RC2 CI does not validate RC3. Materialization does not prove an image is
published, installed or accepted.

Installed architecture/image binding and a fresh complete public catalog
remain unresolved operational evidence gaps. After an authorized corrected
deployment, smoke-test the existing Core version and demonstrate automatic
refresh across the unchanged soft TTL, fresh replacement evidence, settled
authority counts and an unrelated successful read. Resume the remaining
canaries and pre-Core-update gates only with their required authority.

A fresh verified full backup and off-host copy, emergency/recovery access,
one exact reviewed Core target and separate upgrade authorization remain
required. Post-update smoke, restart recovery and reversible helper/dashboard
canaries remain distinct from pre-update acceptance.

## Immutable prior release and recovery

Published RC2 remains intact at commit
`634951157869db79cbaffee272f690a1c969b3c0`, OCI index
`sha256:50683954d463657557135bfc3779c7fb1d2251a60ca02e7501fbc755dd4f69f0`.
RC2 contains the known refresh defect; returning to RC2 does not resolve it.
RC2/RC1 installation alone is not a complete recovery procedure after a Core
upgrade. Recovery must bind a compatible artifact and the exact Core,
configuration/database and verified-backup state.

Before merge, withdraw the draft or revert local preparation if necessary.
Any later rollback, publication, deployment or live operation requires its
applicable authorization and verification. Leave the delivery PR draft:
Josh's later Ready action carries the repository's standing bounded
merge/publication authority, while deployment remains separate.
