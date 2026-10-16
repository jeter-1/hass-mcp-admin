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
| `R/f3_runtime/runtime.py` | R1 (rollback planning lease), O9 (expired pre-intent deadline) |
| `R/f3/persistence.py` | O9 (claimless pre-dispatch terminalization helper) |
| `R/providers/operational_lifecycle.py` | R2-telemetry |
| `R/routing.py` | R8, L1 |
| `.release/next-version` | release staging (consumed by the promotion script) |
| `hass_mcp_engineering_beta/config.yaml` | release (written by `promote_next_release.py --apply`) |
| `R/version.py` | release (written by the promotion script) |
| `scripts/validate_addon_metadata.py` | release (written by the promotion script; not a protected path, listed for completeness) |

`R/f3/executor.py` is **not** needed (see O9 below) and is not changed.

Non-protected files: new tests (`tests/test_hamcp189_*.py`), the edited
`tests/test_configuration_reverification.py` (flake rider), this note, the
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
4. `lease.require_current()` immediately before the rollback plan is
   persisted. A retired lease refuses, so no plan is created from reads whose
   authority lapsed.
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

In `_recover_active_candidates`, when a recovery attempt for a child fails,
`_expired_pre_intent_terminal(...)` decides whether this is a stuck expired
task that has passed its deadline:

- the child exists, is not terminal, has no dispatch intent and
  `dispatch_count == 0`;
- the plan loads, and its dispatch authority has expired on the durable
  clock: `now >= min(plan.expires_at, approval.approval_expires_at)`;
- the deadline has passed:
  `now >= expiry + EXPIRED_PRE_INTENT_RETRY_WINDOW_SECONDS` (600 s).

Before the deadline the existing `bounded_retry` path runs unchanged (backoff
5 → 10 → 20 … capped at 300 s). The retries are bounded because the deadline
is anchored on the durable `expires_at`, and backoff only grows: at most 7
expired-family retries fit in the window, across restarts. The window
tolerates a wall-clock correction near expiry. A clock stepped back before
`expires_at` returns the task to the normal path.

After the deadline the runtime:

1. terminalizes the child through a new claimless primitive,
   `DurableExecutionRepository.terminalize_expired_claim_pre_dispatch`. It
   requires an expired claim, the expected claim generation, no dispatch
   intent and `dispatch_count == 0`, and appends the existing
   `pre_dispatch_terminal` event;
2. projects the plan and task with the existing `_project`, and clears the
   child's readiness fault through the normal settled path.

Outcome strings are the ones configuration plans already produce for the same
cause. **No new stored string:**

| Record | Value | Existing source |
| --- | --- | --- |
| child outcome | `preflight_rejected` | `terminalize_pre_dispatch` outcome set |
| child diagnostic | `plan_expired` | operational adapter and configuration preflight |
| task state / terminal outcome | `failed_pre_dispatch` | `_project` (unchanged) |
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

## 5. R8 and L1 — the gateway audit keeps the refusal reason

`R/routing.py`, in the request `finally`:

- the tool-record branch becomes
  `if isinstance(tool_name, str) and (tool_name or core_unmapped_refused):`.
  A list or dict name no longer reaches the unhashable set lookup. It falls to
  the generic `mcp_request` record (R8). The empty name refused as unmapped
  writes the full `tool_call` unmapped-refusal record (L1);
- the terminal error metric becomes
  `if (tool_name or core_unmapped_refused) and telemetry.error_code:` (L1),
  and the generic-branch metric is skipped for that case, so one terminal
  outcome is counted once.

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
