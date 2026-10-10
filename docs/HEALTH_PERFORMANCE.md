# Governance health cache correctness and responsiveness

## Current cooperative deep-health correction

Public asynchronous health collects each plan, public task, child envelope and
manifest once per call. Plan/task repositories stage their existing navigation
projections on the owner loop, yield between records and publish a repaired
in-memory index only after consistency checks. F3 counts, reconciliation and
inverse lock reporting reuse the same detached records. Fixed namespace bounds
(1,024 F3 parents, 8,192 children, at most eight declared children per manifest)
and the bounded recovery output remain enforced. No durable format changes.

The explicit async diagnostic performs a fresh collection and safety validation
even after an unchanged call; its aggregate rebuild/read counters describe that
work. It does not use a previously healthy aggregate to skip current integrity
inspection. The existing synchronous summary cache and synchronous repository
APIs remain available, with their original default behavior. Historical warm-cache
measurements below describe earlier increments, not current async read counts.

Only detached plan safety validation uses the existing single pure worker. No
repository or mutable runtime goes to a worker. Readers serialize under the
existing service reader coordination; cancellation discards staged collection
and publication. A surviving validation worker must finish and be drained before
a later reader submits another. Repository/thread/file locks never span an await.
Individual file I/O, bounded record decoding, in-memory sorting/aggregation and
lifecycle work remain synchronous; this is not a hard per-request latency bound
or a guarantee against external filesystem/lock contention.

Directory tokens, plan/task and child-writer generations, lock-file identity
and per-file identity/size/timestamp stamps
fence the collection, projection and final overlay. Owner-loop writes invalidate the snapshot even when directory timestamps
coalesce. External replacements are checked through namespace and file stamps. A changed file detected during the final stamp
pass also invalidates it. The fixed `health_snapshot_superseded` refusal does not
latch a global storage fault. Quarantined corruption and genuine failed writes
retain their error/counter and readiness protections. No current scan failure is
replaced with a cached healthy result. Async parent collection errors refuse the
health request instead of projecting an empty task history. Existing synchronous
error projections remain supported.

Owner-loop plan/challenge expiry remains an existing once-only side effect.
After a complete safety and file check, its synchronous persistence section
refreshes only that plan's watched stamp and the plan namespace fence. Concurrent
loop tasks cannot run in that section. These optimistic checks do not claim an
atomic snapshot or compare-and-swap against arbitrary external/in-place writers.
Locks and holds are freshly collected once for F3 and rechecked before reporting;
health neither settles ownership nor performs recovery or provider dispatch.

The existing phase fields retain their names: snapshot includes cooperative plan
and task collection; overlay includes awaited F3 collection/reconciliation plus
live overlays. Each explicit async call now has collection/validation/assembly
work. Worker elapsed remains nested within validation wait. CPU time, total wall
time, maximum loop gaps, GC attribution and structural progress must be measured
separately; no arbitrary sub-100-ms pass threshold is introduced.

Focused tests cover cold navigation repair, current corruption/duplicates,
concurrent plan/task/child/lock movement, cancellation and worker drainage,
legacy parents, orphan holds, inverse retained-lock reporting, no-probe routing,
current-writer approved execution and duplicate suppression. A synthetic public
health fixture with 200 parents and three children each performs 200 parent,
600 envelope and 200 manifest reads, once each, with ticker progress between
reads. Saved run artifacts contain exact timing/CPU/GC measurements. They are
local offline evidence, not installed acceptance or proof that health caused the
historical October disconnect. Routine request readiness still does no history
scan; release, installation and live acceptance are separate gates.

## Earlier cache and worker increments


This increment fixes repeated rebuilding of the governance health aggregate.
It does not change public tool schemas, execution authority, provider routing,
approval policy, persisted formats, or release metadata.

## Cause and correction

In the beta.7 source (`981fc82be5e8660dd97f58b1cea378d1e7e3d97f`),
`health_summary` saved the repository generation captured **before** rebuilding.
The plan history read performed during that rebuild advanced the generation.
The next unchanged request therefore immediately missed the cache. A miss also
computed F3 health both inside the aggregate builder and in the live overlay.

The rebuilt aggregate is now tagged with the generations of its captured plan
and task data. Both repositories' existing navigation checks run before reuse
and at the end of the rebuild. An observed generation change during assembly
fails with the existing storage error category; it cannot certify mixed data as
a current cache. A failed build invalidates reuse. A projection-only index
rebuild cannot retag an older health aggregate.
Task generations are captured immediately after reading task history, before
storage-health metadata can observe a later supported save. A change detected
there rejects the mixed aggregate; a subsequent request rebuilds from current
records rather than retaining stale outcome counts under the newer generation.

Cache reuse is bounded by the earliest future plan or pending challenge expiry,
or restart reconciliation backoff/evidence deadline represented in the
aggregate. A clock rollback also invalidates reuse. Rebuilding uses the existing
lifecycle resolver, preserving once-only durable expiry events; the cache does
not introduce an approval or execution decision. Terminal consumed challenges
do not schedule unnecessary future rebuilds. Lifecycle-provider presence changes
invalidate restart eligibility calculated from the persisted data.

Every public health read independently refreshes storage/error counters,
process locks and active applies, active reconciliation counts, approval
notification status, provider health, and F3 health. The F3 projection runs once
per public health assembly. A failed task-history read remains explicitly an
error even if navigation itself is accessible, and that partial aggregate is
not reusable. Returning a deep copy prevents callers from altering the cache.

## Validation

[`test_governance_health_cache.py`](../tests/test_governance_health_cache.py)
uses synthetic records produced by the current writer and disposable providers.
It covers cold rebuild and repeated hits; plan/approval/task invalidation;
external record replacement; corruption and failed history reads; plan,
challenge and elevated acknowledgement expiry; restart backoff/deadlines;
clock rollback; live locks/providers/errors; generation changes during assembly;
output isolation; one F3 health evaluation; verified synthetic execution and
no second dispatch on duplicate apply.

The existing [plan-store scale tests](../tests/test_beta26_plan_store_scaling.py)
continue to cover 130, 1,000 and 10,000 historical records. Their warm-health
measurement now explicitly rebuilds after an intentional index audit instead of
retagging the old aggregate as if that audit had verified it.

An equally instrumented local 12-task experiment on this host observed:

| Read | Beta.7 plan/task deserializations | Corrected plan/task deserializations |
| --- | --- | --- |
| Cold after persisted changes | 12 / 72 | 12 / 48 |
| First unchanged repeat | 12 / 72 | 0 / 24 |
| Second unchanged repeat | 12 / 72 | 0 / 24 |

Under `cProfile`, unchanged baseline reads took approximately 0.48 seconds and
corrected reads approximately 0.045 seconds. These are small synthetic fixture
measurements, not deployment benchmarks or a performance guarantee. Work counts
are the primary regression criterion; no timing threshold determines correctness.

## Beta.8 limits and the cold-rebuild follow-up

Beta.8 cold rebuilds validate plan/task history synchronously. Existing F3 health
still traverses task and child history on warm calls; the table deliberately
shows its remaining reads. Deadline-triggered rebuilds can also traverse history.
That warm-cache increment did not introduce a worker, background cache, timeout, retry,
fallback, or incremental durable projection.

Navigation detects changes supported by the current repositories, including
atomic replacement and index invalidation. It is not a continuous integrity scan
of every dormant file or an atomic multi-process snapshot. Explicit deep audits
and authoritative record reads retain their existing roles.

Source tests do not prove that event-loop starvation caused a specific Core
connection loss, or that household plans F030/F035 are now executable. Measure
cold and warm installed behavior only under a separately approved deployment
and acceptance contract before deciding the next performance increment.

## Cold-rebuild correction

The asynchronous public `get_server_health` path now validates detached persisted
plan records in one pure worker job per governance service. It runs the same
fail-closed persistence-safety detector used by authoritative reads and saves;
no validation is skipped or cached as execution authority. The worker cannot
read/write repositories, resolve expiry, dispatch a provider, publish a cache,
or write an audit event. Policy projection and lifecycle resolution remain on
the owner event loop, yielding between records. Persisted expiry remains an
existing health-read side effect, with its original once-only semantics.

The health readers serialize preparation. Concurrent requests reuse the completed
aggregate when still valid. Cancellation prevents that request from publishing
or resolving further lifecycle state; its already-running pure validation job
may finish. A subsequent request drains that job before submitting another,
then reads a fresh snapshot. No background task publishes health or grants
authority, and cancellation cannot create an unbounded worker queue.

Plan and task generations are checked after awaits and before publication.
An observed concurrent approval, save, external replacement or task change
refuses the mixed snapshot using the existing storage error categories, without
retrying or overwriting the newer record. The new asynchronous wait/projection
generation checks attach `details.reason=health_snapshot_superseded`, positively
identifying superseded evidence, not a valid health snapshot. The older final
synchronous assembly checks can still return unmarked storage-category errors
for generation races. Absence of this marker alone does not prove disk trouble.
Actual storage and unsafe-record errors retain their existing meanings. A later
ordinary request can rebuild. A failed abandoned worker is drained without transferring its
exception to an unrelated reader; that reader still performs fresh validation,
whose failures propagate. Cancellation continues to propagate.
The aggregate's time origin precedes asynchronous work so an expiry crossed
during projection cannot disappear from cache invalidation. The existing final
synchronous generation checks, live F3/provider overlays, and error behavior
remain in use. The outer health envelope reads current Core/provider state
**after** asynchronous preparation, not before its await.

The tool signature, catalog, approval/dispatch rules, persistence
formats, and fallback policy are unchanged. Startup and internal synchronous
operational snapshots retain the synchronous API. Repository enumeration,
policy/lifecycle work for one record, summary assembly, and the existing F3
health traversal still have synchronous portions; this change is not a hard
latency bound, a worker for all health processing, or an atomic multi-process
snapshot. Total cold-read latency may remain substantial. Its target is allowing
event-loop progress during historical safety validation; its share of installed
cold latency must be measured rather than assumed.

Successful asynchronous reads add fixed numeric `phase_elapsed_ms` fields under
`plan_store_scaling.hot_paths.governance_health`: `reader_wait_ms`,
`abandoned_worker_wait_ms`, `snapshot_ms`, `validation_wait_ms`,
`worker_elapsed_ms`, `projection_ms`, `assembly_ms` and `overlay_ms`.
Snapshot includes navigation and enumeration; assembly includes task-history
aggregation; overlay includes live F3/provider traversal. Worker elapsed is
measured inside the detector and is **nested within** validation wait, which also
includes thread scheduling. Projection includes cooperative yields. These are
wall times, not CPU usage or maximum event-loop blockage; do not sum the nested
worker duration or subtract it to infer owner-loop CPU time. Existing
`last_duration_ms` excludes reader/drain waits and describes governance assembly,
not the entire public request. Final metric copying and outer-envelope work are
outside the phase breakdown. Unused cold phases are zero on warm reads; sync
reads have no async phase object. Diagnostics retain no record content and grant
no authority. Failed reads do not publish a successful phase receipt.

The actual CPU scanner also runs against current-writer synthetic records while
an independent event-loop ticker runs. This complements, rather than replaces,
the controlled wait tests for cancellation/serialization. Retain maximum ticker
gaps and phase measurements in performance evidence; neither a thread boundary
nor a stable sampled authority generation proves a production heartbeat bound.

[`test_governance_async_health.py`](../tests/test_governance_async_health.py)
adds deterministic owner-loop progress and cancellation/overlap checks, actual
concurrent owner approval, local/external record races, task invalidation,
owner-thread expiry, crossed deadlines, storage and unsafe-record refusal,
current authority/F3 state after an await, public no-probe routing, and useful
current-writer synthetic apply/readback with duplicate suppression. These tests
use disposable records and providers. They do not replay F032 or establish
that its production connection loss was caused by event-loop starvation.

After a separately approved release/deployment, installed acceptance must
exercise a legitimate governance-change cache invalidation and its cold read,
not only unchanged warm hits. Capture exact installed identity, cold/warm work
counts and latency, Core authority generations/retirements, connection errors,
provider admission, useful reads and final execution settlement across the
interval. Use a fresh approved plan only under the separate bounded live-test
contract; never replay F032's failed pre-dispatch plan. Stop on authority drift,
uncertain dispatch or provider loss. Source tests do not close that live gate.

## F3 request readiness correction (source candidate)

Routine `/ready`, catalog readiness and authenticated request admission now read
an explicit F3 lifecycle snapshot. They do not call deep health, enumerate F3
history, deserialize child/manifest records, or submit a worker scan. The
snapshot uses a short process-local lock and a fixed set of fault categories;
its cost does not grow with retained terminal history. Catalog reconciliation,
inbound validation, authentication, rate limiting and per-tool provider/Core
authority checks remain in their existing order.

Request admission and F3 execution admission are distinct. Before the first
successful storage validation/recovery, both remain unavailable. Afterwards,
`ready` on `/ready` means the gateway can serve requests; it does not promise
F3 dispatch or healthy storage for every tool. A detected F3 fault returns HTTP
200 with `status=ready_f3_execution_unavailable`, `f3_execution_ready=false`
and `f3_readiness_status=faulted`. Independent authenticated reads remain
reachable; a read requiring corrupt storage still returns its original failure.
Pending initial catalog reconciliation continues to return HTTP 503. The
liveness endpoint is unchanged.

Deep F3 health remains an expensive diagnostic operation. Startup and recovery
of a detected read/pass fault perform full storage validation, including child
envelopes outside the bounded scheduler page, before restoring execution
admission. Child recovery failures remain recorded across backoff and unrelated
passes until that child's authoritative recovery succeeds. A failed durable
write is not cleared by successful reads; it requires repair and runtime
reconstruction. See [the lifecycle contract](F3_RUNTIME_INTEGRATION.md#request-and-execution-readiness-source-candidate).
These checks can still block the event loop and scale with history. This change
removes the request-readiness scan; it does not redesign health or recovery.

`tests/test_f3_readiness.py` uses current repository writers, real Linux file
locks and fsync, and synthetic histories of 0, 12, 200 and 1,023 tasks. Fixture
preparation is measured separately. Operation counts must remain zero for
history scans, manifest reads, child-envelope reads and worker submissions on
repeated serial/concurrent ready and not-ready gateway calls. Responsiveness
receipts include wall time, heartbeat wall gaps, loop-thread CPU gaps and GC
thread attribution, both serially and with a controlled contending thread.
The 250 ms loop-thread CPU guard is a coarse starvation tripwire, not a wall
latency SLA; zero storage work is the primary regression assertion. There is no
new sub-100 ms wall-time requirement. Source evidence does not establish Linux
installed latency, Windows behavior or the cause of F032/disconnects.

The 1,024-manifest allocation limit and retained history are unchanged. This
correction neither prevents exhaustion nor certifies free execution capacity.
Capacity and the lifecycle of settled evidence require a separate persistence
and rollback decision.
