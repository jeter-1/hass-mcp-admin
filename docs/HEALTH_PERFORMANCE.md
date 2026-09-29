# Governance health cache correctness and responsiveness

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
retrying or overwriting the newer record. A later ordinary request can rebuild.
The aggregate's time origin precedes asynchronous work so an expiry crossed
during projection cannot disappear from cache invalidation. The existing final
synchronous generation checks, live F3/provider overlays, and error behavior
remain in use. The outer health envelope reads current Core/provider state
**after** asynchronous preparation, not before its await.

The tool signature, output fields, catalog, approval/dispatch rules, persistence
formats, and fallback policy are unchanged. Startup and internal synchronous
operational snapshots retain the synchronous API. Repository enumeration,
policy/lifecycle work for one record, summary assembly, and the existing F3
health traversal still have synchronous portions; this change is not a hard
latency bound, a worker for all health processing, or an atomic multi-process
snapshot. Total cold-read latency may remain substantial. Its measured target
is allowing event-loop progress during the dominant historical safety scan.

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
