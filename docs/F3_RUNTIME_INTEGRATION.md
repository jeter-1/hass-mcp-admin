# F3-D runtime integration

The controlling runtime decisions are recorded in
[ADR-016](architecture/ADR-016-F3-RUNTIME-INTEGRATION.md),
[ADR-017](architecture/ADR-017-F3-CHILD-EXECUTION-AND-HOLDS.md), and
[ADR-018](architecture/ADR-018-F3-GOVERNED-ROLLBACK.md).

## Authority and scope

Beta 20 is based directly on merged Beta 19 main
`51943e11cc5290b1bf8db75474982193463044f5`. The exact merged prerequisite
heads are F3-0 `77d8f19b3dc12ec94eef134375ddcbd5baeb2670`, F3-A
`94392e31b2dd1892889ca643e8cabb157085ffc1`, F3-B
`d76badbf2263541c33b07cd366a3ed77bc0902aa`, F3-C1
`f26328c0f95769b3893ee650ce4abcfe976d3397`, and F3-C2
`bcb6d93bfe3010722942ec566f2a17f8d6014e97`. Pull requests 87 through 91
are merged to `main` and their exact-head checks are green.

This integration activates only the eight accepted configuration and four
accepted operational capabilities. It adds no MCP tool, direct writer,
fallback, dynamic adapter, arbitrary provider arguments, protocol, schema, or
release wildcard. Dashboard planning and verification infrastructure remains
shipped but runtime-inert. Issue #92's external-writer atomicity gate remains
unresolved, so Beta 20 registers no dashboard planning tool, setter, execution
capability, operation vocabulary, or generated Python transform.

## Route and writer inventory

All public planning remains with the existing governance service. Approval,
elevated acknowledgement, task inspection, and cancellation retain their
existing public tools and authority. `apply_change_plan` is the only public
execution entry, and covered plans select one immutable authority before any
provider call.

| Public plan route | Persisted operation | Provider mutation | F3 capability | Complete resource locks | Beta 20 routing |
|---|---|---|---|---|---|
| `create_change_plan` | `create_automation` | fixed configuration `write(create, automation, id, config)` | `create_automation_configuration` | `automation:<id>` exclusive; `reload:automation` and core shared | deterministic contract-1 child |
| `create_change_plan` | `update_automation` | fixed configuration `write(update, automation, id, config)` | `update_automation_configuration` | same model | deterministic contract-1 child |
| `create_configuration_plan` | create/update automation | fixed configuration write | `create_automation_configuration` / `update_automation_configuration` | same model | ordered contract-2 child |
| `create_configuration_plan` | create/update script | fixed configuration write | `create_script_configuration` / `update_script_configuration` | `script:<id>` exclusive; `reload:script` and core shared | ordered contract-2 child |
| `create_configuration_plan` | create/update input boolean | fixed configuration write | `create_input_boolean_configuration` / `update_input_boolean_configuration` | `helper:<entity_id>` exclusive; `reload:input_boolean` and core shared | ordered contract-2 child |
| `create_configuration_plan` | create/update input number | fixed configuration write | `create_input_number_configuration` / `update_input_number_configuration` | `helper:<entity_id>` exclusive; `reload:input_number` and core shared | ordered contract-2 child |
| `create_backup_plan` | `create_full_backup` | reviewed `create_full_backup` | `create_full_home_assistant_backup` | backup exclusive; core and exact upstream add-on shared | one operational child |
| `create_reload_plan` | `controlled_reload` | reviewed `dispatch_reload` | `reload_home_assistant_configuration_domain` | reload domain exclusive; core and exact upstream add-on shared | one operational child |
| `create_addon_restart_plan` | `restart_addon` | reviewed `dispatch_addon_restart` | `restart_installed_home_assistant_addon` | add-on exclusive; core and exact upstream add-on shared | one operational child |
| `create_home_assistant_restart_plan` | `restart_home_assistant` | reviewed `dispatch_home_assistant_restart` | `restart_home_assistant_core` | core exclusive; exact upstream add-on shared | one operational child |
| `rollback_change` | separately governed reverse update | same fixed configuration write only after separate approval/apply | corresponding update capability | same target/reload/core set as forward update | Option A; rollback request itself performs no write |

The operational target keys are `backup:local_full_backup`,
`reload:<domain>`, `addon:<slug>`, and `home_assistant:core`. No other provider
mutation is admitted by the F3 registry. Legacy active task recovery remains
read-only. Dashboard reads remain unchanged.

## Closed adapter registry and import boundary

The code-owned registry contains exactly 12 capability entries. Each binds
`f3-operation-adapter-v1`, an exact implementation object, family, plan
projection, target, action, lock model, provider/admission model, verifier,
recovery behavior, rollback declaration, route, historical behavior, protocol
`2025-03-26`, and exact upstream releases 7.14.2 and 8.0.0. Startup rejects a
missing or duplicate capability, model mismatch, unsupported target/action,
route/adapter mismatch, admission mismatch, packaging failure, dashboard
capability, or fallback.

Only `ha_mcp_engineering.f3_runtime` imports and composes F3 execution
internals. C1 and C2 continue to consume the canonical shipped public F3 API.
No runtime module imports root `f3_contracts`; the checkout-only facade remains
test/specification infrastructure. Built-image import closure is tested without
the repository root.

## Execution ownership and durable evidence

One schema-1 public task is the backward-compatible projection of one immutable
`f3-child-execution-v1` manifest containing one to eight deterministic child
declarations. Initialization uses a cross-process journal and atomic file
replacement. It binds plan ID/hash, public task ID, deterministic child/attempt
identity, operation ordinal/dependencies, adapter/capability, target, prepared
hash, complete-lock hash, approval-bundle hash, idempotency key, provider
identity, and selective-hold keys.

The decision is fail-closed:

- an existing legacy task remains legacy authority and is never dispatched by
  F3;
- an existing F3 task remains F3 authority and never falls back;
- no existing authority atomically journals and materializes exactly one F3
  public-task/child sequence; and
- ambiguity, corruption, hash disagreement, incomplete immutable historical
  projection, or a second authority fails before dispatch.

Concurrent processes may join the same child execution. A separate
cross-process projection transaction serializes only schema-1 compatibility
events; it grants no dispatch authority. The F3 claim and durable intent remain
the sole mutation fence. A crash during initialization replays the journal,
creates no provider action merely by reading storage, and later recovery may
resume pre-intent work only while exact authorization remains valid.

Authoritative lifecycle, dispatch count, intent/deadline, locks/fencing,
observations, verification, and outcome remain in the child record. Its bounded
runtime envelope stores scheduling and operation evidence: exact backup/
operation IDs, HA outage/reconnect/readmission booleans, next eligibility,
backoff, hold tokens, and reconciliation authority. Public tasks hold compatible
attempt/child summaries. Audit holds sanitized references, never raw configs,
dashboard bodies, provider payloads, URLs, credentials, or exception messages.

## Lock graph and selective holds

All complete sets are computed before the first mutation. Exclusive modes
conflict; shared modes express availability dependencies:

- same target, duplicate backup/reload/add-on restart prevent concurrent
  mutation of one resource;
- update and rollback use the same exact target lock;
- configuration takes matching reload shared while reload takes it exclusive;
- configuration, reload, backup, and add-on restart take core shared while HA
  restart takes core exclusive;
- provider-dependent operational work takes exact upstream `addon:<slug>`
  shared while restarting that add-on takes it exclusive; and
- unrelated targets/domains/add-ons remain concurrent when accepted sets share
  only compatible dependencies.

Manual review atomically converts the acquired handle into a selective
non-expiring target hold while releasing dependencies. Configuration and
rollback retain their automation/script/helper key; backup retains
`backup:local_full_backup`; reload retains `reload:<domain>`; add-on restart
retains `addon:<slug>`; HA restart retains `home_assistant:core`. Generations do
not change. Failed promotion leaves the complete handle intact, and a crash
after promotion is reconstructed from authoritative lock records.

The existing private authenticated Home Assistant Ingress surface provides the
governed workflow; no MCP tool is added. CSRF, administrator principal,
prepared hash, child generation, and hold generations are bound. Observation
and verification may rerun without dispatch. Release requires exact verified
readback or a recorded administrative decision; unresolved closure retains the
hold. A durable release journal finishes crash-interrupted release without a
provider call. Time never silently releases a hold.

## Dispatch, recovery, and readiness

Every child orders complete atomic locks, final locked preflight, idempotent
approval consumption, schema-1 approval witness, F3 intent with
`dispatch_count=1`, and one fixed provider call. Intent-persistence failure
calls the provider zero times. After intent, cancellation and redispatch are
permanently prohibited.

One coordinator owns F3 recovery and historical Beta 19 read-only
reconciliation. It performs a strict startup sweep before listener creation,
then one 30-second loop. Sweeps use cross-process child claims, deterministic
task/ordinal order, batch 16, a five-second budget, persisted eligibility, and
bounded 5-to-300 or 30-to-300 second exponential backoff. Active recovery
selects at most one eligible child per nonterminal public task per sweep.
Deadline-bearing post-intent children sort first by their immutable evidence
deadline, followed by deterministic task/operation identity. Pre-intent work
follows without receiving any new execution authority.

### Terminal-parent orphan reconciliation

The sweep also enforces a named invariant:

> **Terminal parent + proven zero dispatch => no child remains nonterminal.**

A parent that reaches a terminal state before dispatch used to strand its
children: the sweep skipped every child whose public task was already
terminal, so children left in `preflight` or `not_started` were never
revisited, their hold projections were never cleared, and
`nonterminal_execution_count` never converged.

Orphan work shares the coordinator's existing batch-16 and five-second budget.
Active recovery and historical orphan discovery are deliberately separate.
Active discovery starts from an in-memory nonterminal navigation index already
filtered to exact `f3_child_sequence` authority before applying the reviewed
1,024-public-task bound. Unrelated legacy tasks therefore cannot consume the
F3 result limit. This index and its dedicated cursor are scheduling evidence
only: the coordinator reloads and validates the exact public task, manifest,
declaration, child record, runtime/backoff state, attempt, operation, dispatch
intent, and dispatch count before recovery. A missing or contradictory
authority fails closed. The active cursor makes an ineligible prefix
restart-fair and advances past eligible work only after that work is processed
or placed in the bounded durable checkpoint described below; a removed or
terminal cursor target safely restarts from the bounded F3 set.

Active discovery and recovery share the same five-second envelope. Discovery
therefore persists up to the batch limit of 16 selected priority identities
in `f3-active-recovery-checkpoint-v1` before attempting recovery. Priority
identities comprise nonterminal post-intent readback and terminal child
projection; nonterminal post-intent work retains the first capacity. The
checkpoint contains only public-task, child, operation, ordinal, attempt, and
declaration-hash navigation evidence. It is not an authority index and cannot
authorize execution. On the next sweep checkpointed work is reloaded and
considered before any further namespace scan. Removed, backed-off, replaced,
already-projected, or authority-mismatched entries are skipped according to
current durable state and cannot block later work. Checkpoint composition is
always bounded to 16. If deferred entries fill that bound while newly eligible
priority work is discovered, only the minimum deterministic suffix of deferred
navigation references is evicted. Those children remain reachable from the
authoritative nonterminal index, and the unsettled sweep prevents the active
cursor from advancing as though they had completed.

Terminal child projection is an explicit recovery mode independent of dispatch
intent. A terminal child beneath a nonterminal exact-F3 parent is eligible when
its persisted execution class is either post-intent (`dispatch_count=1`) or
verified no-dispatch (`dispatch_count=0`, no intent, completed preflight, and a
persisted `preflight_noop_verified` proof). A no-intent terminal non-success is
also projection-eligible and preserves the existing aggregate failure
precedence.

The persisted model supplies the one closed execution classification used by
record loading, active recovery, terminal projection, orphan lock settlement,
and expired-lock settlement. No-intent records require count zero, no provider
response, no observation or verification attempts, and no post-dispatch event.
Their terminal outcomes are limited to the five outcomes written by
`terminalize_pre_dispatch`, plus `succeeded_verified` only with exact no-op
proof. Intent-bearing records require count one, nonempty matching intent
locks, and the writer-produced lifecycle grammar: one initial start, a final
ordered `locks_acquired` / `preflight_completed` /
`dispatch_intent_committed` boundary, exactly one matching intent event, and
only post-intent events after that boundary. Post-intent-compatible state and
outcomes are also required; a pre-dispatch-only terminal outcome is
contradictory. Any contradiction is
classified as bounded corrupt storage before projection or lock disposition,
so the public parent, checkpoint, and exact fenced locks remain unresolved for
operator-visible recovery rather than being rewritten as conclusive
pre-dispatch failure.

When the complete authoritative sequence succeeds, active discovery chooses its
last successful child as a scheduling anchor, including all-post-intent,
all-no-dispatch, and mixed sequences. Any later unfinished or terminal-failure
child takes precedence, so an earlier success cannot prematurely terminalize a
multi-operation parent. Every projection candidate is checkpointed first. The
coordinator then reloads the public task, F3 authority, manifest, declaration,
child identity, runtime state, and the complete sequence. Projection invokes
neither child execution nor a provider call; the checkpoint clears only after
the public projection settles durably.

If discovery reaches the deadline immediately after finding one eligible
post-intent child, that child is attempted first on the next sweep. A checkpoint
holding multiple eligible children retains immutable-deadline and deterministic
task/operation/child ordering; batch overflow remains directly reachable on a
later sweep. A crash before checkpoint persistence leaves the active cursor
unchanged. A crash after persistence resumes from the checkpoint. A crash after
a transition but before checkpoint or cursor cleanup revalidates the now-current
record and cannot redispatch. Checkpoint replacement and both cursors use atomic
compare-and-swap, so concurrency conflicts fail without losing authority or
making skipped work unreachable.

The checkpoint and both recovery cursors are nonauthoritative navigation
files. Malformed JSON or an invalid navigation schema is removed under the
repository lock, directory-fsynced, and reported as a bounded diagnostic; the
next bounded sweep restarts discovery from authoritative public tasks,
manifests, and child records. I/O failures still surface as storage failures,
and corruption in any authoritative record continues to fail closed.

Deadline-bearing post-intent candidates receive all available batch and time
capacity before historical cleanup can reserve a transition. If fewer than 16
post-intent transitions are available, historical scanning may receive one
fairness slot ahead of lower-priority pre-intent work; an unused slot returns
to pre-intent recovery. When no pre-intent candidate competes, historical
cleanup may use the remaining batch. Equal post-intent deadlines retain
deterministic task/operation/child ordering. This priority does not alter or
extend an immutable evidence deadline, and no recovery path may redispatch.

A separate durable historical cursor pages at most 1,024 declarations, reads
at most the repository's bounded 1,024 manifest paths, and stops at the shared
deadline. It advances only through declarations safely examined. A candidate
skipped because the batch or time budget is exhausted remains immediately
after the cursor for the next sweep instead of waiting for a namespace
rotation. Both cursor writes use atomic compare-and-swap; a crash or conflict
leaves work eligible. Generic recovery performs no full-namespace declaration
load or second sort after the deadline. The sweep
terminalizes one eligible orphan before releasing anything, which leaves no
window for a concurrent dispatch to begin.
It then releases only lock records whose exact task, plan, operation, attempt,
owner, key, mode, and generation match that child's durable lock evidence.
Both live/expired leases and selective conflict holds are covered. A later or
ambiguous fencing generation fails closed and is never released. The generic
expired-lock pass applies the same complete authority match and therefore
cannot bypass that refusal.

"Proven zero dispatch" is durable, not inferred. The parent must carry no
provider attempt and no dispatch timestamp, and each child must carry no
durable dispatch intent. Because intent is committed *before* the provider is
invoked, a record with no intent provably never dispatched; a crash after the
intent leaves it set, and such a record is deliberately excluded and left for
the post-intent readback path. The storage layer enforces this independently:
cancellation refuses any record holding an intent and records
`dispatch_intent_exists` rather than silently skipping.

Terminalization uses `cancelled_pre_dispatch`, never a success outcome, and
appends evidence rather than overwriting the original parent state, terminal
outcome, or causal error. Already-terminal children remain eligible while an
exact lock, selective-hold token, or cancellation-audit cursor is unsettled.
Physical lock disposition completes before runtime token projections are
cleared. A crash after cancellation, lock release, token cleanup, or audit
delivery therefore converges on a later sweep without redispatch or releasing a
different generation.

The five-second value is a stopping boundary for starting further discovery or
recovery work, not a claim that the operating system can interrupt one atomic
fsync or that an already-authorized external observation can always be
cancelled safely at exactly five seconds. Such an individual operation may
finish after the boundary; the coordinator starts no subsequent transition in
that sweep, preserves the remaining checkpoint, and resumes on a later sweep.
This limitation does not extend immutable evidence deadlines.

A child that never received an execution record has nothing to terminalize and
is projected as `cancelled_pre_dispatch` under such a parent. The public
`f3_children` and schema-1 `verification_summary.children` views are derived
from the same canonical projection, while legacy child identities remain
unchanged. Reconciliation items and health remain recovering until exact lock,
token, and audit settlement completes. Every event replayed from a persisted
child record receives a deterministic SHA-256 identity, independent of event
type or diagnostic classification. Its canonical preimage is model
`f3-persisted-audit-event-v1`, exact child ID, persisted positive event
`sequence`, and the exact validated persisted event. The audit sink preserves
that identity through truncation, serializes append/rotation, checks the exact
identity in retained logs, and fsyncs the append before returning. The durable
audit replay batch scans both retained logs once under the exclusive audit
lock, then reuses that bounded identity set for every event in the batch. The
cursor advances only through the acknowledged prefix, so retry after a crash
between append and cursor persistence does not write a duplicate. This is a
bookkeeping and projection correction; it never dispatches a provider call.

Before intent, exact authority re-enters public preflight, reacquires the
complete set, repeats preflight, and commits intent before mutation. After
intent, the executor transfers only expired fenced locks for observation and
invokes adapter `recover`; dispatch is unreachable. Deadlines are immutable and
inclusive. Ambiguous expiry enters manual review. Terminal pre-intent locks may
be released by exact identity; unresolved post-intent holds never expire.

The production timing profile is lease 120 seconds, renewal 20 seconds, wait
zero, and poll 0.05 seconds. Startup opens and validates governance, audit,
task, child, lock, and ownership state; recovers journals/holds; validates the
registry and provider gateways; initializes services/coordinator; and completes
the startup sweep before listening. MCP readiness requires both existing
catalog readiness and F3 execution readiness. Failure provides no fallback.

Health uses `unavailable`, `degraded`, `recovering`, `ready`, and
`manual_intervention_required`. It reports bounded registry/capability counts
and hash, ownership/store/coordinator state, sweep times, task/outcome/hold
counts, safety counters, timing, dashboard count zero, and fallback zero. Audit
binds public task, child/attempt, plan/operation, capability, sanitized target,
outcome, timestamp, dispatch possibility, and bounded evidence references.

## Rollback, upgrade, downgrade, and compatibility

Rollback Option A creates a separate stale-safe reverse update plan; the
request itself does not mutate. The new plan receives its own approval and F2
decision and uses the same F3 target/reload/core locks, durable intent, one
write, and exact readback. Historical configuration tasks convert only from
complete persisted evidence. Operational and dashboard rollback remain
unavailable.

Historical terminal tasks remain readable. Active legacy tasks remain with
their original read-only reconciler. Final F3 preflight derives the accepted
legacy task's immutable lock graph and rejects a new conflicting target while
that task remains active; legacy work receives no new dispatch authority. An
approved historical taskless plan may
enter F3 only when its immutable contract supplies every projection; otherwise
a new plan is required. Startup never mutates from historical reads alone.

Downgrade to Beta 19 is routine only before any F3 execution. Terminal isolated
records remain retained for audit, but Beta 19 cannot administer them. Any
nonterminal F3 execution or hold requires explicit Beta 20 reconciliation
before downgrade. Records are never automatically deleted and Beta 19 must not
claim, complete, or redispatch unknown F3 work.

Beta 20 preserves protocol `2025-03-26`, stable 1.1.2, task schema 1, plan
contracts 2/3, approval authority, F2 policy, Dashboard v3 reads, exact catalog
admission/lifecycle normalization, `aiohttp==3.14.3`,
`cryptography==50.0.0`, and zero fallback. No public tool is added: 25
canonical plus 23 Engineering-native equals 48 local. Exact 7.14.2 remains
78 advertised, 26 delegated, zero held, and 74 total. Exact 8.0.0 remains 78
advertised, 24 delegated, two held, and 72 total. Held tools remain exactly
`ha_search` and `ha_get_operation_status`.

## Terminal tokenless configuration-lock settlement (source candidate)

For contract-1/2 configuration children only, the immutable declaration identifies
the capability, prepared-operation hash, target, plan/hash, attempt and complete
sequence lock hash. Execution records use the capability name as `operation`;
the declaration's `operation_id` is the plan's step label. Recovery compares the
capability identity for these contracts. Other contract identities are unchanged.

A terminal zero-dispatch parent and child may settle retained tokenless locks
only when the child has no preflight/intent, its claim has expired, and the exact
plan/hash/child membership, adapter, target and attempt agree. Under the child
execution transaction and then the lock-store transaction, recovery rechecks:

- all records for that child are the exact observed snapshot and exact owner;
- the entire immutable key/scope/mode/reason union hashes identically;
- no hold, active lease or renewal exists;
- acquisition is one timestamp within the execution's recorded lifetime with
  consecutive byte-sorted generations.

The existing generations are first written to the child record, preserving the
terminal outcome, and only then removed from the lock store. A binding failure
leaves all locks; a crash or lock write failure after binding leaves normal
exact-token recovery available. A raced replacement, later acquisition, partial
union, different owner, wrong hash, hold or uncertain dispatch leaves the records
untouched. These checks assume the existing trusted local storage boundary;
ordinary hashes neither authenticate an owner nor withstand arbitrary coordinated
storage forgery. No live provider is consulted to construct historical authority.

The durable binding uses the existing terminal event vocabulary and a fixed
`unrecorded_lock_tokens_bound` diagnostic. Existing restart-safe event auditing
and orphan reconciliation supply the audit trail outside transaction callbacks.
Known-active tokenless claims with retained locks are deferred, not cancelled.
Cancellation rechecks the observed claim generation and, for retained tokenless
locks, expiry inside its execution transaction. A raced replacement claim is
not cancelled. Children that never acquired locks retain existing cancellation
behavior when the observed claim is unchanged.

`get_execution_task` child projections and reconciliation items add bounded
`lock_recovery` diagnostics: retained/active/expired counts, token persistence,
claim-expiry deferral, last reconciliation time, fixed failure category, retry
eligibility and manual-intervention requirement. Health adds `recovery_backlog`
with the existing 100-item scan cap, sixteen child details, precision and omission
flags. Cumulative recovery failures remain cumulative. A zero active-lock count
must not be interpreted as zero retained expired locks. These are diagnostic
additions; no tool input, registration, provider route or approval state changes.

A durable token-binding event keeps terminal-child reconciliation pending after
locks are released, including failed and cancelled outcomes. This covers process
loss before the pending runtime marker, partial/failed audit append, and loss
between append, audit cursor, and final reconciliation marker. Existing stable
audit event IDs deduplicate replay. Health retains a recovering backlog until
both audit export and reconciliation complete; zero retained locks alone cannot
close that work. No outcome, approval or dispatch authority is revived.

## Request and execution readiness (source candidate)

F3 now publishes a bounded process-local lifecycle through `readiness_state()`.
It is observation, not a storage cache or execution authority. A lifecycle read
performs no persistence/provider work, consumes no approval, schedules no task,
and cannot repair or redispatch an execution. A short lock synchronizes worker
health observations with event-loop recovery; revision-bound clearing prevents
an older recovery from erasing a newer fault.

| Transition / observation | Request admission after catalog reconciliation | New F3 apply | Clearing evidence |
| --- | --- | --- | --- |
| Constructed; startup validation/recovery pending | Unavailable | Refused | Successful authoritative initial validation/recovery |
| Initial validation/recovery fails or is cancelled | Unavailable | Refused | Successful full validation and a completed recovery pass |
| Initial validation/recovery succeeds | Available | Eligible for existing exact execution checks | No outstanding detected fault |
| Deep health, task projection or reconciliation detects a read fault | Available after prior successful startup | Refused | Recovery revalidates the complete relevant stores, including history beyond its scheduling page |
| Recovery pass throws or is cancelled | Available after prior successful startup | Refused | Full validation and a successful pass that does not exhaust its time budget |
| A specific child recovery fails | Available after prior successful startup | Refused | That child's existing authoritative recovery action succeeds; backoff or an unrelated pass cannot clear it |
| Durable-write/admission storage failure or unexpected apply failure | Available after prior successful startup | Refused | Repair and runtime reconstruction; a read audit cannot prove that a failed write is now durable |
| Observed recovery supervisor exits or is cancelled | Available after prior successful startup | Refused | A live replacement supervisor and the applicable recovery checks |

`/ready.ready` describes request admission, while `f3_execution_ready` describes
F3's lifecycle eligibility for new applies. `f3_readiness_status` is
`initializing`, `ready`, `faulted`, or `unavailable` when the lifecycle cannot be
observed. An HTTP 200 response with `ready_f3_execution_unavailable` expressly
preserves diagnostic access without claiming F3 execution availability. Catalog
initialization remains an independent barrier. Exceptions at composition are
reported as unavailable, without exposing exception text.

Deep health preserves its storage-dependent failures. On a successful health
assembly, `execution_ready` and the additive `readiness` object use the same
lifecycle; `status` indicates unavailability when faulted, preserving the more
actionable `manual_intervention_required` status for retained holds or unresolved
lock authority. `recovery_coordinator_status` also indicates unavailability. Existing store/lock/ownership detail fields retain their respective
read/structural meanings and do not promise write capacity. Deterministic per-plan/provider validation refusals retain their original
failure and scope; they do not by themselves latch a shared execution-store
fault. Fault categories are fixed strings; child IDs, exception text and configuration are not included in
the readiness snapshot.

Existing pre-intent recovery may continue only within its own validated pass,
using the same current plan, approval, target/provider, lock and dispatch
ownership checks. Its internal context does not admit a new public apply or
clear the lifecycle fault by itself. A newly detected general fault, or a new
fault for the child being recovered, retires that pass's admission at the
approval/provider boundaries. An unrelated child's retry does not starve other
already-authorized recovery. Post-intent recovery remains readback-only and
terminal recovery remains projection-only. No fault or cancellation grants a
second dispatch. Admission is checked again before approval consumption and
at the existing provider invocation boundary for a concurrent fault detected
after apply began.

Recovery supervision is observed from the runtime supervisor, or from the
application's existing periodic recovery task when it enters the runtime. The
application still owns startup and shuts down if its supervised task exits;
this correction adds no replacement supervisor or retry loop. Detection is
operation-driven, not a continuous integrity scan of every dormant file.

Persistence, the 1,024-manifest limit, locks, approval policy, historical format
support, retention and all unresolved work are unchanged. Runtime reconstruction
is not automatic repair or permission to restart a deployed system. Capacity
exhaustion can still refuse new execution; it does not revoke established
request admission. Archive/retention/deduplication and safe rollback remain a
separate owner decision.

A known child recovery fault remains eligible after its parent becomes terminal.
The existing bounded historical recovery page retries that exact child's
terminal bookkeeping after its retry deadline, within the same scan, transition
and time limits. It reloads declaration/child/parent identity and sequence state,
requires the persisted terminal projection to agree, finishes child-event audit
and plan projection, and durably completes recovery metadata before clearing the
matching fault revision. Failed validation, audit or metadata writes retain the
fault; a newer revision cannot be cleared by that pass. This path neither
prepares nor executes an operation and never releases selective target holds.
Private read-only reconciliation may terminalize a parent; it does not by itself
clear a previously latched recovery fault. A later exact bounded pass does so.

Terminal fault reconciliation includes the existing verified-no-dispatch and
terminal-pre-dispatch execution classes as well as terminal post-intent records.
A durable dispatch intent is not required to retry exact bookkeeping. Existing
orphan cleanup keeps priority when it still has pending work; otherwise every
selected terminal class passes the same identity, sequence, projection, audit
and fault-revision checks. This does not grant execution or hold-release authority.
