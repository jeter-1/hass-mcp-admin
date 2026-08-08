# ADR-019: Bounded plan-store navigation and recovery

Status: proposed for `2.2.0-beta.26`

## Context

Beta 25 reloaded and validated every retained plan before applying a list
limit. The same projection path supplied external-approval inventory,
individual Ingress review, periodic recovery, and governance health. With
about 130 retained plans, deployed `list_change_plans(limit=1)` took about
6.35 seconds without Home Assistant or upstream work. Recovery repeated a
similar traversal approximately every 30 seconds.

Persisted plan, approval, task, policy, sequence, and F3 records remain the
only authorization and execution authority. A performance projection must
never replace their integrity checks.

## Decision

Engineering builds process-local navigation indexes from authoritative
storage at startup:

- ordered plan metadata and bounded status pages;
- nonterminal plan IDs;
- active external-approval challenge IDs;
- recoverable operational-plan IDs;
- nonterminal task IDs; and
- direct task ownership maps for task, plan, and idempotency identifiers.

The indexes contain only metadata needed to locate work. Any approval,
execution, rollback, or recovery action reloads the exact persisted plan and
task and runs the existing integrity, projection, policy, sequence, approval,
and F3 checks. A stale or forged index entry can cause only a failed lookup or
rebuild; it cannot grant authority.

`list_change_plans` requests bounded navigation pages and loads records until
the requested result limit is satisfied. Approval inventory loads only active
challenge candidates. Ingress detail loads the requested plan directly.
Recovery loads only nonterminal tasks and recoverable plans. F3 and task-store
health use incrementally maintained metadata aggregates. Governance health
uses a generation-bound aggregate: repeated polling does not reproject
terminal history, while a storage-generation change invalidates the aggregate
and makes the next rebuild visible in metrics.

## Derived-state lifecycle

The repositories own their derived state.

1. Startup enumerates and validates authoritative files, quarantines malformed
   records under the existing rules, and builds every index and aggregate.
2. A normal transition writes and fsyncs the authoritative record first, then
   updates all affected navigation sets, ordering keys, counts, signatures,
   and generation values while holding the repository lock.
3. Structural count/signature disagreement or an observed storage-directory
   generation change invalidates navigation and triggers a full rebuild.
4. Temporary files are not indexed. A crash before replacement leaves the
   prior authority; a crash after replacement is repaired by restart rebuild
   or later invalidation. Task writes retain the existing cross-process file
   lock, append-only checks, and authoritative uniqueness fallback.
5. Cleanup deletes only eligible terminal authority and removes its derived
   entry. No migration, summary file, or index can discard or reinterpret a
   historical plan.

Filesystem generation signals and indexes are performance hints, not trust
signals. Direct lookups reload the exact file. A cache miss on task ownership
falls back to authoritative enumeration before a new task can be created, so
concurrent duplicate apply and forged ownership remain fail closed.

## Integrity and reconciliation

Historical records remain readable through normal paginated list/detail
operations. Full historical validation occurs during startup and through the
deliberate `deep_audit_plan_store` reconciliation path; it is no longer paid
by every list, inbox, health poll, or recovery sweep. A historical record is
also validated whenever it is requested or becomes authority-relevant.

Corrupt authoritative records retain the existing quarantine and error
semantics. Corrupt derived active sets are detected by count and identifier
signatures and rebuilt. Approval expiry is resolved from the reloaded record
and atomically removes the plan from active approval navigation.

## Observability

Governance health reports bounded, non-sensitive counters for:

- records enumerated and plan/task records deserialized;
- terminal plan/task records touched;
- recovery candidates examined;
- index generation, rebuild, update, and invalidation counts;
- health/projection cache rebuild and hit counts; and
- last measured latency for each affected hot path.

No plan payload, challenge secret, principal, Home Assistant secret, or
unbounded identifier collection is included.

## Consequences

Startup and an explicit deep audit remain proportional to retained history by
design. A generation-invalidated governance health aggregate is rebuilt once;
subsequent polls are bounded and expose whether that rebuild occurred. Normal
list, approval, and recovery work is proportional to the requested page or
active work rather than terminal retention.

No public tool schema, approval authority, F3 authority, provider route,
compatibility admission, fallback policy, or persisted record format changes.
