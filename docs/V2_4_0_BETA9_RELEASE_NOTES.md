# Engineering 2.4.0-beta.9 release notes

Cold governance-health responsiveness after legitimate plan, approval or task
changes. These notes assert no publication, deployment or installed acceptance.

Beta.8 corrected repeated unchanged cache misses. A real governance change still
requires a cold rebuild. Beta.9 moves the expensive persisted-plan safety scan
off the public health request's event loop into one pure validation worker per
governance service. The same fail-closed detector remains in use; its detached
inputs grant no repository, audit, provider or execution access. Policy and
lifecycle projection remain on the owner loop, yielding between records.

Concurrent health readers serialize preparation. Cancellation cannot publish a
background cache or resolve further lifecycle state for the canceled request;
the next reader drains any unfinished pure validation before starting again.
Observed plan/task changes reject a mixed snapshot without overwriting an owner
approval. The new async wait/projection generation checks attach
`details.reason=health_snapshot_superseded`. Older final-assembly generation races
may retain unmarked storage errors; absence of the marker does not prove disk
failure. A failed abandoned job does not poison the next reader;
fresh validation and caller cancellation still propagate their own failures.
Crossed expiry deadlines and clock rollback still invalidate reuse.
Live Core/provider state is sampled after asynchronous preparation, and warm
health reuse retains beta.8's checks. See [the performance contract](HEALTH_PERFORMANCE.md).

Repository enumeration, individual projections, final assembly and F3 traversal
still include synchronous work. Total cold latency may remain substantial.
Internal operational planning/restart-verification snapshots and startup retain
the synchronous health API. They are outside this public async correction;
restart/reload is not the governance change for its acceptance gate. Successful
async health adds bounded numeric phase wall times under
`hot_paths.governance_health.phase_elapsed_ms`, separating worker validation
from snapshot/projection/assembly/overlay work without claiming CPU attribution.
Offline tests establish event-loop progress and race/failure preservation; they
do not establish that health processing caused the reported production
connection losses or that those losses are resolved. Installed acceptance must
exercise a genuine governance-change cold rebuild, not only unchanged warm reads.

The healthy catalog remains **80 = 55 static + 25 delegated reads**. Public
descriptors, approval/dispatch and stale-state rules, provider/Core admission,
stored formats, dependencies, workflows, installation options and frozen stable-v1
are unchanged. Existing health-read expiry effects retain their once-only owner
loop semantics. No retry, fallback, authority-lifetime extension or new persistent
namespace is introduced. Alarmo and household configuration changes are excluded.

Installed acceptance records the actual Core version admitted by valid signed
applicability for all 19 required profiles, with ha-mcp 8.5.0. There is no new Core
version pin. Existing CI lane versions are reproducible fixtures, not alternate
compatibility authority. Compatible Core updates retain the reviewed signed
registry route without requiring an Engineering release.

Preserve current durable records and supplementary verification receipts for a
separately approved rollback to the verified beta.8 artifact. Binary rollback
does not undo dispatch history; restoring stale execution records is not recovery.
Inherited beta.6 expanded-lock reader limitations and the accepted F027 missing
original-prestate qualification remain. Completed beta.7/F028 verification must
not be repeated merely for this release.

The [beta.9 acceptance contract](V2_4_0_BETA9_ACCEPTANCE.md) separates source/CI,
publication/image identity, raw catalog, read continuity, cold/warm health and
approval-to-dispatch continuity. F032/F035 remain held until installed continuity
is evidenced; old failed or stale plans and approvals must not be replayed.
