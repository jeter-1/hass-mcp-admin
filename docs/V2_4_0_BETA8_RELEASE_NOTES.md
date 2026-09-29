# Engineering 2.4.0-beta.8 release notes

Governance health-cache correctness and reduced repeated history work. These
notes assert no publication, deployment or installed acceptance.

Repeated unchanged health reads now reuse the aggregate for the generations of
the captured plan/task history. Previously, rebuilding advanced the plan-store
generation but installed the earlier cache key, causing the next request to scan
history again. F3 health is now evaluated once per public health assembly.

Reuse ends at the next relevant plan/challenge expiry or restart-reconciliation
deadline, on clock rollback, supported storage changes or lifecycle-provider
presence changes. Live storage/error counters, locks, active work and provider/F3
health remain refreshed. Failed history collection remains visible and cannot
produce a reusable healthy result. Observed task changes between history capture
and storage-health refresh reject the mixed aggregate; they cannot certify stale
outcomes under a newer generation. The cache does not grant execution authority.

In a synthetic 12-task source experiment, unchanged health reads reduced plan
deserializations from 12 to zero and task deserializations from 72 to 24. Cold
rebuilds and remaining F3 history scans are still synchronous. These measurements
are not installed latency guarantees, proof of heartbeat starvation, or a remedy
for a particular household authority failure. No worker, retry or fallback was
introduced. See [the performance contract](HEALTH_PERFORMANCE.md).

The healthy catalog remains **80 = 55 static + 25 delegated reads**. Public
descriptors, provider/Core admission, approval and dispatch behavior, persisted
formats, dependencies, workflows, installation options and frozen stable-v1 are
unchanged. Installed acceptance uses the actual Core version admitted by valid
signed applicability for all 19 required profiles, with ha-mcp 8.5.0. Each receipt
records REST/WebSocket identity agreement, the observed Core version, registry
sequence and authority state. Beta.8 adds no separate installed-Core version pin;
unknown or unreviewed versions remain unavailable. Existing CI lane pins remain
reproducible fixtures. Compatible Core updates can use reviewed signed registry
data without a new Engineering release. Alarmo and household configuration changes
are excluded.

No new persistent namespace or migration is added. Preserve current durable
state and the existing supplementary re-verification receipts through rollback;
do not restore stale execution history. Beta.6 expanded-lock reader limitations
remain. A separately approved rollback to the verified beta.7 artifact must retain
current records; this source change does not itself perform recovery.

The [beta.8 acceptance contract](V2_4_0_BETA8_ACCEPTANCE.md) requires exact source,
publication/image binding, fresh catalog, bounded installed reads, cache-work
measurements and final settlement. Earlier beta.7/F028 success is retained without
repeating the supplementary observation. Historical qualifications remain; this
release alone does not authorize or unblock F030/F035 execution.
