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
current durable state and cannot block later work.

Terminal child projection is an explicit recovery mode independent of dispatch
intent. A terminal child beneath a nonterminal exact-F3 parent is eligible when
its persisted execution class is either post-intent (`dispatch_count=1`) or
verified no-dispatch (`dispatch_count=0`, no intent, completed preflight, and a
persisted `preflight_noop_verified` proof). A no-intent terminal non-success is
also projection-eligible and preserves the existing aggregate failure
precedence. Dispatch count without matching intent, count above one, or a
no-dispatch success without exact no-op proof fails closed.

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
audit cursor advances only after acknowledgement, so retry after a crash
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
