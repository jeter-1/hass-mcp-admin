# F3-A durable adapter execution and locking core

## Status and boundary

F3-A implements the internal primitives declared by the
`f3-operation-adapter-v1` contract. The package is deliberately runtime-inert:
no current application, service, provider, adapter, health surface, or MCP tool
imports or instantiates it. Synthetic adapters are the only consumers in this
change.

The core assumes that a caller has already consumed the applicable approval.
Lock ownership is concurrency control, not authorization. A later integration
must still bind an approved plan, policy decision, exact prepared operation,
and execution task before invoking the executor.

## Durable storage primitive

The lock namespace is `f3-operation-locks-v1`. The execution namespace is
`f3-operation-executions-v1`. Both live below a caller-selected durable root;
F3-A does not select or activate a production path.

Each namespace owns a stable `.transaction.lock` inode. Every read, validation,
decision, generation update, and state replacement occurs while holding an
exclusive POSIX `flock` on that inode plus a same-process re-entrant thread
lock. The transaction inode is never replaced. State changes use a same-folder
exclusive temporary file, file `fsync`, `os.replace`, and directory `fsync`.

This closes the cross-process read/read/write/write race:

1. A process takes the stable transaction lock before reading state.
2. No second process can enter the read/decide/write region.
3. The first process validates the complete namespace and computes the entire
   multi-lock result in memory.
4. A failure before replacement leaves the prior complete state authoritative.
5. A successful replacement commits the complete result atomically before the
   transaction lock is released.

Acquiring multiple locks therefore commits all requested locks or none. The
primitive uses only the Python standard library and the POSIX filesystem
contract already available to the Engineering add-on; it adds no database or
dependency. Corrupt data, an unknown schema, a failed read, and a failed write
all fail closed.

The execution repository uses one strict JSON record per task, named by the
SHA-256 of the validated task ID. Its transaction lock serializes claims,
durable dispatch intent, results, cancellation, retention, and recovery across
processes. Records are retained for 90 days by default; cleanup removes only
terminal records older than the configured 1–365 day bound.

## Lock schema and normalization

The namespace schema and each lock record are version 1. A lock record contains:

- canonical key, resource/provider scopes, and shared/exclusive mode;
- owner, task, optional plan, operation, and attempt identities;
- acquisition, last-renewal, and UTC lease-expiration timestamps;
- monotonically allocated generation/fencing token;
- bounded canonical evidence references; and
- an explicit conflict-hold flag.

Keys match
`^[a-z][a-z0-9_]{0,63}:[a-z0-9][a-z0-9_.-]{0,255}$`, contain exactly one
colon, are lower case, and are at most 320 characters. Duplicate requests union
bounded evidence and scopes. Exclusive mode dominates shared mode. The complete
set is sorted by UTF-8 byte value for acquisition and released in reverse
order.

Shared holders may coexist. An exclusive request conflicts with any holder,
and an exclusive holder conflicts with any request. Provider and resource
scopes may be acquired in one atomic set. Provider-specific conflict edges and
lock-set calculation are intentionally deferred to later adapter tracks.

## Lease, renewal, and wait decisions

Callers must provide explicit timing; there are no hidden operational defaults.
Validated bounds are:

| Setting | Bound | F3-A test profile |
| --- | --- | --- |
| Lease | 30–3,600 seconds | 60 seconds |
| Renewal interval | 5–300 seconds and strictly less than the lease | 10 seconds |
| Wait timeout | 0–30 seconds | 0 seconds |
| Wait poll | 0.01–1 second | 0.05 seconds |

Persisted UTC deadlines survive restart. An injectable monotonic clock controls
in-process waits, and an injectable wall clock makes lease and reconstruction
tests deterministic. Waiting observes cancellation and never leaves partial
ownership.

Renewal validates every owner field and generation, then extends all leases in
one transaction. A late, stale, missing, or owner-mismatched renewal fails.
When observation must continue and renewal fails, the executor transitions to
manual review; it does not dispatch again. The unresolved durable record stays
fail closed even if conflict-hold promotion itself cannot be persisted.

## Fencing, recovery, and release

Every acquisition receives a distinct monotonically increasing generation for
each lock. Renew, release, validation, conflict-hold promotion, and stale
recovery require the exact key, generation, owner, task, plan, operation, and
attempt identity. An old handle cannot renew or release a later generation.

Expired records are not silently ignored. They continue to conflict until an
explicit task-aware recovery transaction chooses one of:

- release an exact pre-dispatch attempt;
- transfer the exact post-intent task and attempt to a new owner/generation for
  observation only; or
- create an indefinite conflict hold for unresolved dispatch.

Transfer cannot change task, plan, operation, or attempt identity. It never
grants authorization and never restores dispatch permission. Unexpired leases
survive repository reconstruction. Terminal pre-dispatch and verified outcomes
release their exact fenced locks. Release failures leave durable records for a
later terminal duplicate to settle without redispatch.

`manual_review_required` retains locks as conflict holds because the provider
may have mutated state and continued exclusive observation or operator
reconciliation is required. A later governed reconciliation decision must
explicitly release them; elapsed time alone does not.

## Executor lifecycle

The executor implements:

`planning -> preflight -> dispatch -> observation -> verification -> recovery`

Rollback remains a separate governed capability and is never inferred as a
lifecycle phase.

1. It validates the exact prepared target, adapter capability descriptor,
   contract hashes, verification declaration, rollback declaration, and bounded
   evidence. Prepared data contains no executable forwarding instructions.
2. It claims the durable task. An active duplicate reports the existing task;
   a terminal duplicate returns the terminal result. Plan identity alone never
   reuses a task.
3. It normalizes and atomically acquires the complete lock set, binds the
   fenced generations to the task and attempt, and calls preflight once for
   that execution call.
4. The adapter receives a `before_dispatch` callback. That callback revalidates
   every held generation and commits the durable dispatch intent. A reviewed
   adapter must call it immediately before its single exact provider operation.
5. Dispatch, observation, verification, and recovery accept and persist only
   closed normalized classifications and bounded hashes/codes.
6. Observation, verification, and recovery may repeat within configured
   attempt/deadline bounds. Dispatch may not.

The internal metrics and event interfaces expose closed counters and bounded
classification fields only. They do not retain raw provider responses, lock
owner secrets, credentials, or arbitrary provider messages. Central health and
audit aggregation remain unchanged.

## Durable dispatch boundary and no-blind-redispatch proof

The intent transaction durably records task and plan identity, adapter and
operation identity, target, attempt lineage, request identity, timestamp,
fenced lock tokens, exact provider-operation descriptor, argument hash,
evidence deadline, and `possibly_dispatched=true`.

The same atomic transaction sets `dispatch_count=1` before provider invocation.
This value reserves and consumes the attempt's only mutating invocation. Any
second intent is refused. Therefore:

- failure before intent is pre-dispatch and invokes the provider zero times;
- failure after the state replacement is possibly dispatched even when the
  caller did not receive the persistence response;
- process reconstruction with intent transfers only observation ownership;
- a timeout, crash, disconnect, malformed result, or lost response cannot clear
  intent or decrement the count; and
- neither execution recovery nor adapter recovery receives a dispatch callback.

Once intent exists, the only allowed external actions are read-only observation
and verification. The fault suite proves that reconstructed execution never
increments the durable dispatch count or the synthetic adapter dispatch count.

## Task-state and outcome mapping

F3-A does not change persisted execution-task schema version 1 or rename its
states. Its internal normalized projection is:

| Executor phase | Normalized outcome | Existing task state | Terminal | Dispatch possible | Allowed retry | Lock behavior |
| --- | --- | --- | --- | --- | --- | --- |
| preflight | `preflight_rejected` | `failed_pre_dispatch` | yes | no | new governed attempt | release |
| planning/lock | `lock_conflict` | `failed_pre_dispatch` | yes | no | new governed attempt | no ownership |
| preflight | `provider_unavailable_pre_dispatch` | `failed_pre_dispatch` | yes | no | new governed attempt | release |
| dispatch | `dispatch_failed_confirmed` | `failed_post_dispatch` | yes | no | new governed attempt after review | release |
| dispatch | `dispatch_indeterminate` | `observing` | no | no | observation/recovery | retain/renew |
| observation/verification | `observing` | `observing` | no | no | observation/verification | retain/renew |
| verification | `verification_mismatch` | `failed_post_dispatch` | yes | no | new governed decision | release |
| verification | `succeeded_verified` | `succeeded_verified` | yes | no | return existing result | release |
| planning/preflight | `failed_pre_dispatch` | `failed_pre_dispatch` | yes | no | new governed attempt | release |
| observation/verification | `failed_post_dispatch` | `failed_post_dispatch` | yes | no | new governed decision | release |
| recovery | `manual_review_required` | `manual_review_required` | yes | no | governed reconciliation only | conflict hold |
| planning/wait | `cancelled_pre_dispatch` | `cancelled_pre_dispatch` | yes | no | new governed attempt | release |

Cancellation is accepted only while durable intent is absent. Cancellation
after intent is rejected, does not imply rollback, and leaves observation and
verification responsible for the possibly dispatched operation.

## Process-loss acceptance matrix

The deterministic synthetic harness records durable task state, durable lock
state, dispatch count, mutation count, next allowed operation, observation
requirement, redispatch prohibition, and terminal outcome for every boundary.

| Boundary | Durable effect and permitted continuation |
| --- | --- |
| Before lock acquisition | planning record; no locks; new preflight permitted |
| During multi-lock acquisition | prior state; no partial lock set |
| After locks, before preflight | fenced locks survive; no dispatch intent |
| After preflight, before intent | no dispatch; expired exact locks may be released and preflight repeated |
| During intent persistence | provider count zero; prior durable record remains authoritative |
| Immediately after intent | count consumed; observation/verification only |
| Provider before simulated effect | possibly dispatched; readback determines mismatch |
| Provider after simulated effect | possibly dispatched; readback may verify success |
| After provider response | response receipt durable; observation only |
| During observation | intent and locks survive; observation may resume |
| After observation | prior observation count durable; verification may resume |
| During verification | intent survives; verification may resume |
| After verified result | terminal result survives; later duplicate settles locks only |
| During renewal | original fenced record remains; no generation is silently transferred |
| During release | terminal duplicate releases exact locks without dispatch |

Across the matrix the maximum durable dispatch count, adapter dispatch count,
and simulated mutation count are each one per attempt.

## Known limitations and integration requirements

- `flock` requires a local POSIX filesystem with correct advisory-lock and
  atomic-rename semantics. F3-D must verify the selected add-on data path and
  document recovery if the filesystem contract is unavailable.
- F3-A supplies isolated event/counter snapshots only. F3-D owns bounded central
  health/audit integration, operational runbooks, retention scheduling, and
  governed conflict-hold reconciliation.
- F3-B must calculate dashboard-specific lock sets, implement exact atomic
  compare/save/readback semantics, and integrate the reviewed dashboard-write
  provider without weakening Dashboard v3 reads.
- F3-C1 must wrap configuration adapters in exact prepared operations and define
  their provider/resource lock sets. It must not reinterpret generic lock
  compatibility as provider policy.
- F3-C2 must do the same for backup, controlled reload, add-on restart, and Home
  Assistant restart, including their provider-specific conflict edges.
- F3-B, F3-C1, and F3-C2 must call the durable callback immediately before the
  one reviewed mutation and must prove that every provider path is unreachable
  when intent persistence or lock validation fails.
- Dashboard transformation, dashboard risk classification, backup conflict
  edges, and provider-specific lock matrices remain intentionally unresolved in
  their owning tracks.
