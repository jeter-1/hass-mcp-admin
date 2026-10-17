# HAMCP-189 PR B + C — design note

Scope: R1, R2-telemetry, O9, R8, L1 and L2 from `docs/HAMCP_189_TRIAGE.md`
(draft PR #265, not yet on `main`), under Josh's decisions O1–O9 on
register row HAMCP-189 (O6 amended: PR C rides with PR B in one PR). Base:
`main` at `bdb5af8` (2.4.1-beta.14, PR A merged).

`R/` means `hass_mcp_engineering_beta/ha_mcp_engineering/`.

## 1. Protected files (O8 — final list)

This PR changes exactly these protected files. Any other protected file is
out of scope and would be reported before it is changed.

| File | Item |
| --- | --- |
| `R/f3_runtime/runtime.py` | R1 (rollback planning lease, garage save-boundary check, ordinary rollback passes the save hook), O9 (expired pre-intent deadline, including the no-record case) |
| `R/f3/persistence.py` | O9 (claimless pre-dispatch terminalization and no-record expired materialization primitives) |
| `R/providers/operational_lifecycle.py` | R2-telemetry |
| `R/routing.py` | R8, L1 |
| `R/governance/service.py` | R1 save-boundary hook only (O8 amendment, below) |
| `.release/next-version` | release staging (consumed by the promotion script) |
| `hass_mcp_engineering_beta/config.yaml` | release (written by `promote_next_release.py --apply`) |
| `R/version.py` | release (written by the promotion script) |
| `scripts/validate_addon_metadata.py` | release (written by the promotion script; not a protected path, listed for completeness) |

`R/f3/executor.py` is **not** needed (see O9 below) and is not changed.

**O8 amendment (authorized by Josh after review of `b9b04ec`).** Review of `4357b96` (M1) asks
for the lease check at the save boundary, after every awaited planning read,
for both the ordinary and the garage rollback builders. The garage builder
saves through `runtime.persist_rollback_plan`, which is in scope and now
checks the lease there. The ordinary builder saves inside
`R/governance/service.py` (`_create_configuration_plan`), which is outside
O8. The proposed narrow change is about four lines: a private
`_before_persist: Callable[[], None] | None = None` keyword on
`_create_configuration_plan`, called synchronously just before the plan is
recorded, which the runtime passes as `lease.require_current`. Josh
authorized exactly this change and nothing else in that file. It is made as
three added lines: the keyword, and the guarded call after
`_bind_new_plan_policy` and before either save branch. The default is a
no-op, so the public `create_configuration_plan`, entity-metadata planning
and the garage inverse keep their behavior. Only the ordinary F3 rollback
(`_plan_fresh_rollback`) passes the hook, by calling the private builder
with the same arguments the public wrapper forwarded (`expiration_minutes`
keeps its default of 120). The public schema and tool surface are unchanged.

Non-protected files: new tests (`tests/test_hamcp189_*.py`), the edited
`tests/test_configuration_reverification.py` (flake rider), the
`tests/test_f3_packaging_boundaries.py` allowlist entries for the new O9 tests'
explicit F3 imports, this note, the
beta.13 release-notes correction (L2), the new beta acceptance and release
notes, and the `CHANGELOG.md` and `README.md` entries.

**HAMCP-162 overlap.** None in runtime source. The HAMCP-162 implementation
branch (`agent/HAMCP-162-yaml-reader-impl`) touches the same release files
(`README.md`, `CHANGELOG.md`, `config.yaml`, `version.py`,
`validate_addon_metadata.py`); whichever PR is ready second takes the next
beta and rebases those files. It also edits
`tests/test_hamcp164_core_gate_fail_closed.py`, which this PR imports fixtures
from (`RecordingCore`, `call`, `gateway_for`, `settings`,
`GatewayFailClosedTests.assertUnmappedRefusal`) without editing it. A rename
there would break `tests/test_hamcp189_gateway_audit_reason.py` at import.
`R/ha_core_readmission/routes.py` is a HAMCP-162 file. Its justification text
for `rollback_change` describes the pre-R1 behavior. It is disclosed here and
not edited.

## 2. R1 — rollback planning holds its own Core lease (O2)

**Defect.** `rollback_change` is Core-independent at the gateway because an
existing rollback plan is a local idempotent return. Fresh planning reads
current configuration through `_read_configuration_resource`, with no Core
authority installed.

**Fix.** A private async context manager in `runtime.py`,
`_rollback_planning_lease(core_runtime, requirements, *, target=None)`,
mirrors `_CoreVerificationAdapter.preflight`:

1. `core_runtime.acquire(requirements[, target=...])`. `None` → refuse.
2. `consume(authority)`. `None` → `release(authority)` and refuse.
3. Install `_core_call_window(revalidate)` for the planning body, so every
   planning read runs under a current, revalidated authorizer. A read after
   Core retires is refused by the existing read clients
   (`HomeAssistantUnavailableError`) before any request.
4. `lease.require_current()` before the plan is saved. The original check
   ran when planning returned to the runtime, before the builder's final
   read, so a Core retirement during that last read still saved a plan
   (review of `4357b96`, M1). The garage builder's check now runs inside
   `persist_rollback_plan`, in the same projection transaction as the save,
   after every awaited read (the rollback context carries the lease). The
   ordinary builder saves inside `R/governance/service.py`; under the O8
   amendment (§1) its private `_before_persist` hook runs
   `lease.require_current` after the final read and before the plan is
   recorded or any prior plan is superseded.
   `test_ordinary_retirement_during_final_builder_read_saves_nothing` (an
   `expectedFailure` at `b9b04ec`) now passes. Entity-metadata rollback is
   unaffected: its builder makes no read after the runtime's check.
5. On exit, an exception raised while the lease has retired (for example the
   read client's refusal) is translated into the Core refusal below, so the
   caller sees one truthful reason.
6. `finish(commits)` in `finally`.

The refusal is `PROVIDER_UNAVAILABLE` with details `reason` and
`reason_code` set to `core_capability_unavailable` (an existing reason string),
plus `core_authority_acquired`, `plan_created: false` and
`provider_dispatch_occurred: false`. When the runtime has no Core runtime
(test and embedding compositions), the lease is a no-op.

Placement (acquired **before any rollback planning read**, after every purely
local return):

- `create_rollback_plan`: after the idempotent `rollback.request_id` return,
  covering the entity-metadata branch, the v2 loop, the legacy automation
  branch and plan creation. Requirements: `core.dependency_helper_planning`;
  the entity-metadata branch adds `core.entity_registry_metadata_operation`
  (the same pair `routes.py` maps for `create_entity_metadata_plan`).
- `_create_garage_inverse`: after the `existing` and `cached` returns, before
  the first `_read_configuration_resource`.

The idempotent and cached returns keep working while Core is degraded.

The entity-metadata lease binds the exact target
`{"target_type": "entity_registry_metadata", "target_id": <entity_id>}`, as the
gateway does for `create_entity_metadata_plan`.

**Disclosed behavior change.** A local refusal that the fresh planning path
raises after the lease point (for example an unsupported task state or a
multi-operation source plan) now returns the Core refusal while Core is
degraded, because the lease is acquired first. With Core admitted, those
refusals are unchanged.

## 3. R2-telemetry — count the attempt only after the authorizer passes

`OperationalLifecycleProvider._execute_observed_read` now begins the upstream
attempt inside `before_dispatch`, after `authorize_core_dispatch()` passes.
It finishes the attempt only if it began. A refused read reports
`upstream_request_count == 0`. With no telemetry, behavior is unchanged.

A retained-session transport runs `before_dispatch` in its worker task, which
can run after the caller has stopped waiting (a timeout). The hook then
refuses with `provider_unavailable` (`dispatched=False`) instead of starting
an attempt that nothing would finish. This only narrows dispatch: it affects
reads whose result nobody is waiting for.

One disclosed consequence: a transport failure before `before_dispatch` runs
(for example a catalog or session failure) is no longer counted as an upstream
attempt, which is the truthful count.

## 4. O9 — expired pre-intent deadline

### 4.1 What the source actually does (correction to the triage)

The triage (§3.2a, "R2b") says an expired approval makes recovery retry the
task indefinitely. Offline probes on `bdb5af8` refine that:

- **Configuration plans (contract v1/v2)** are not stuck. Recovery reaches the
  executor, whose preflight rejects the expired plan. The child ends
  `preflight_rejected` with diagnostic `plan_expired`, the task ends
  `failed_pre_dispatch` and the plan ends `FAILED`.
- **Contract-v3 plans (operational, exact helper state, dashboard)** are stuck
  *inside the F3 runtime*. After one pre-intent retry, every recovery sweep
  fails in `_load_prepared` (the operational adapter raises `plan_expired`)
  before the executor runs. The child stays `preflight` with `bounded_retry`
  backoff doubling to 300 s, and the `recovery_child:` readiness fault keeps
  `execution_ready` false, which blocks every new F3 apply.
- **In the production pass**, `_run_f3_recovery_pass` (`R/application.py`) runs
  `reconcile_execution_tasks` right after `recover_once`, every 30 s. It sees an
  undispatched task without valid approval and projects it
  `failed_pre_dispatch` with `pre_dispatch_authority_invalid`. The next
  recovery pass then cancels the orphaned child as `cancelled_pre_dispatch`
  (`orphaned_terminal_parent_recovery`). So production does terminate the
  task, but only through a second reconciler. If that reconciler cannot run
  (the pass aborts on an earlier exception, or the 20-task cap or plan lock
  defers it), the F3 runtime alone never terminates the task. The child record
  then says "cancelled" for a task nobody cancelled.

O9 is therefore implemented as an F3-side deadline. It makes the runtime
terminate the task itself, truthfully, independent of the service reconciler.

### 4.2 Design

In `_recover_active_candidates`, when a recovery attempt for a child raises,
`_terminate_expired_pre_intent(...)` decides whether this is a stuck expired
task that has passed its deadline:

- the child exists, is not terminal, has no dispatch intent,
  `dispatch_count == 0`, and its claim has expired (no live owner);
- the plan loads, its hash matches the child declaration, and no operator
  cancel is recorded;
- its dispatch authority has expired on the durable clock, and the deadline
  has passed:
  `now >= min(plan.expires_at, approval.approval_expires_at)
  + EXPIRED_PRE_INTENT_RETRY_WINDOW_SECONDS` (600 s);
- every related lock record is a regular lock that matches the child's
  recorded tokens. A conflict hold or an unrecorded generation keeps the
  bounded retry for its own reconciler.

Before the deadline, the existing `bounded_retry` path runs unchanged (backoff
5 → 10 → 20 … capped at 300 s). The retries are bounded because the deadline
is anchored on the durable `expires_at`, and backoff only grows. At most 7
expired-family retries fit in the window, even across restarts, and the
tests assert that bound. The window tolerates a wall-clock correction near expiry. A clock
stepped back before `expires_at` returns the task to the normal path. The
hook runs only when the attempt raises: configuration plans, whose executor
preflight rejects the expired plan itself, never reach it.

After the deadline, the runtime:

1. terminalizes the child through a new claimless primitive,
   `DurableExecutionRepository.terminalize_expired_claim_pre_dispatch`. It
   requires the expected claim generation, an expired claim, no dispatch
   intent and `dispatch_count == 0`, and appends the existing
   `pre_dispatch_terminal` event. A live or changed claim raises
   `ExecutionClaimLost`; any intent or dispatch count raises
   `BlindRedispatchProhibited`;
2. releases the child's exact recorded locks through the existing
   `_release_orphaned_child_locks`, the same release the orphan reconciler
   uses for a terminal pre-dispatch child. Conflict holds are excluded above,
   so no hold-release reason is written;
3. projects the plan and task with the existing `_project`, records the
   existing `transition_processed` runtime result, and clears the child's
   readiness fault.

Any exception inside this helper falls back to the unchanged bounded retry.
If a crash lands between steps 1 and 2, the next sweep projects the terminal
child (`failed_pre_dispatch`). The orphan reconciler, which handles a terminal
pre-dispatch child under a terminal parent, then releases the locks.

Outcome strings are the ones configuration plans already produce for the same
cause. **No new stored string:**

| Record | Value | Existing source |
| --- | --- | --- |
| child outcome | `preflight_rejected` | `terminalize_pre_dispatch` outcome set |
| child diagnostic | `plan_expired` | operational adapter and configuration preflight |
| task state / terminal outcome | `failed_pre_dispatch` | `_project` (unchanged) |
| child runtime result | `transition_processed` | settled recovery path |
| plan status | `FAILED` | `_project` (unchanged) |

Approval expiry and plan expiry are the same instant
(`approval_expires_at = plan.expires_at`), so `plan_expired` is truthful for
both.

**Never dispatches.** The primitive refuses any record with a dispatch intent
or dispatch count (`BlindRedispatchProhibited`), and the helper never calls the
executor. A child whose claim is still live keeps retrying until the claim
expires (bounded by the claim lease).

**Unchanged.** Post-intent recovery, readback-only recovery, cancel (PR A's
durable cancel wins because a recorded cancel never reaches this branch), the
service reconciler, and every non-expired failure keep their current
behavior. A task that is not expired keeps retrying, as today. That residual
is outside O9 and is disclosed.

### 4.2a Expired work with no child record (review of `4357b96`, M2)

A task can be saved (state `created`/`preflight`, with an F3 declaration)
before the runtime creates its first child record. If the runtime restarts
in that window and the plan then expires, the original O9 path never runs:
it starts from a child record. Recovery fails in `_load_prepared` on every
sweep and readiness stays false.

`_terminate_expired_pre_intent` now hands the no-record case to
`_terminate_expired_unclaimed`, which works only from the durable
declaration and plan:

- the plan hash matches the declaration; the dispatch-authority deadline
  (the same `expires_at` + 600 s) has passed on the durable clock;
- the child's position in the declaration is valid (every earlier child
  `succeeded_verified`, every later child absent), and no lock record exists
  for it;
- inside `children.public_projection_transaction()` it re-checks that the
  task is still `created`/`preflight` and that no operator cancel is
  recorded, then calls a new primitive,
  `DurableExecutionRepository.materialize_expired_pre_dispatch`. Under the
  repository's exclusive transaction the primitive returns `None` if any
  record now exists (a competing first claim won), and otherwise writes the
  child record directly as terminal `preflight_rejected` with diagnostic
  `plan_expired`, an expired claim (generation 1, `claim_expires_at = now`),
  no intent and `dispatch_count == 0`. Its events are the existing
  `execution_started` and `pre_dispatch_terminal`;
- after the transaction it projects with `_project` and records
  `transition_processed`, so readiness is restored.

No provider is contacted and no dispatch authority is renewed: preparation is
not rerun after expiry. The record format and the strings are the existing
ones (table above). Cancel keeps precedence (a recorded cancel is checked
inside the same transaction), and the first claim keeps precedence (the
primitive never overwrites a record).

**Dashboard declarations were never stuck.** For a dashboard declaration,
the existing executor preflight rejects the expired plan itself
(`preflight_rejected` / `plan_expired`) and the new path is not reached; the
test asserts that.

**Pre-existing, unchanged (disclosed).** On `bdb5af8`, re-applying any
contract-v3 plan that has reached a terminal state after expiry raises the
operational adapter's raw `OperationalAdapterError` (`plan_expired`) instead
of a governed refusal, because `_apply` loads the prepared operation before
its terminal short-circuit; nothing dispatches. Cancelling an expired task
that has no child record fails the same way in
`_load_prepared(cancel_only=True)`. Both are outside this PR's scope.

### 4.3 Tests

`tests/test_hamcp189_expired_pre_intent_deadline.py` uses the real exact
helper-state F3 path. Approval consumption is refused at the Core seam, so the
child records `pre_intent_retry_required` with no intent:

- recovery runs at every eligible instant after expiry. The child terminates
  after 1–7 retries, at or after the deadline, as `preflight_rejected` /
  `plan_expired`. The task is `failed_pre_dispatch` and the plan `FAILED`.
  Nothing is dispatched, locks and leases are settled, readiness is restored,
  and later sweeps change nothing;
- one second before the deadline, the child still retries and readiness is
  still false. At the next eligible instant, it terminates;
- preservation: a failure while the plan is valid keeps `bounded_retry`.
  After preparation recovers, the same task dispatches exactly once;
- the retry bound asserted is the source bound, 7 (tightened from 8 after
  review);
- the deadline survives a runtime restart;
- composition with the service reconciler never dispatches and settles;
- the primitive refuses a live claim, a changed generation, an
  already-terminal child (unchanged record) and a dispatched child
  (`BlindRedispatchProhibited`, unchanged record).

`tests/test_hamcp189_expired_unclaimed_declaration.py` covers the no-record
case (§4.2a):

- operational: restart between declaration and first child, then expiry,
  recovered with the service reconciler absent and the provider down. The
  task ends `failed_pre_dispatch` / `plan_expired`, with zero planning,
  dispatch and verification calls and readiness restored. The terminalization
  step itself makes no provider contact. Also: the in-process case, one
  second before the deadline (bounded retry, readiness false), an operator
  cancel (cancel wins), a cancel recorded inside the projection window
  (cancel wins), a competing first claim that wins (never overwritten) and
  one that loses (finds the terminal record);
- exact helper state: restart then expiry settles with the helper unchanged;
  before the deadline it keeps retrying;
- dashboard: restart then expiry is rejected by the existing preflight
  without reaching the new path, with zero writes.

## 5. R8 and L1 — the gateway audit keeps the refusal reason

`R/routing.py`, in the request `finally`:

- the tool-record branch becomes
  `if isinstance(tool_name, str) and (tool_name or core_unmapped_refused):`.
  A list or dict name no longer reaches the unhashable set lookup. It falls to
  the generic `mcp_request` record (R8). The empty name refused as unmapped
  writes the full `tool_call` unmapped-refusal record (L1);
- error capture and the terminal error count are keyed on the method, not
  on the name's truthiness (review of `4357b96`, L1). The captured
  `tools/call` response is classified whenever there is one, and the count
  is `if rpc_method == "tools/call" and telemetry.error_code:` in `finally`;
  the generic-branch count is skipped for `tools/call`. So every refused
  `tools/call` writes exactly one record and one count: a falsey malformed
  name (`None`, `False`, `0`, `[]`, `{}`) now gets the generic `mcp_request`
  record and its count like any other non-string name, and the raw name is
  never logged.

## 6. L2 — beta.13 release-notes correction

`docs/V2_4_1_BETA13_RELEASE_NOTES.md` gains a dated correction:
- three of the "local state only" tools (`get_execution_task`,
  `cancel_execution_task` and `approve_change_plan`) are not purely local;
- `rollback_change` planning read without Core authority (fixed by R1).

The published `v2.4.1-beta.13` GitHub Release is left as published.

## 7. Riders

- `test_configuration_reverification`: the cleanup-timeout test uses a frozen
  monotonic clock and a 0.5 s budget, so scheduler load cannot exhaust the
  budget before the hung read starts.
- The stale "1.28.1" comments (`R/mcp_server.py`,
  `R/mcp_sdk_compatibility.py`) and the HAMCP-188 wording Lows are not in files
  this PR touches, so they are left as is.

## 8. Invariants

At-most-once dispatch, no blind redispatch, the HAMCP-164 gate, PR A's
durable cancel, and the HAMCP-181 outcome strings are unchanged. Every new
path is pre-dispatch or audit-only. No new write, service call, fallback or
provider route is reachable.

## 9. Release

`2.4.1-beta.15` (first ready, first served; HAMCP-162 PR #267 had not merged).
It was staged in `.release/next-version`, then materialized by one
owner-authorized run of `scripts/promote_next_release.py --apply`, which wrote
`config.yaml`, `version.py` and `validate_addon_metadata.py` and consumed the
declaration. `scripts/codex-context.py` resolves
`docs/V2_4_1_BETA15_ACCEPTANCE.md` and `docs/V2_4_1_BETA15_RELEASE_NOTES.md`
exactly. If HAMCP-162 merges first and takes beta.15, this PR merges `main`,
restages and rematerializes the next beta, and renames both documents.

Validation evidence (focused suites, mutations, CI, and the Full and Evidence
tier logs) is recorded in the PR body for the exact head.
