# HAMCP-189 PR A — Core authority before "already satisfied", R6, durable cancel

Status: design note, written before any protected-path change (owner decision
O8). Authoritative inputs: `docs/HAMCP_189_TRIAGE.md` (PR #265 head
`60112a8fcff5ec1a65f51e2bbab2d7a5e2e32c96`) and Josh's 2026-10-16 decisions
O1–O9 on register row HAMCP-189. Base: `origin/main`
`06d555523eae8343605c0d3605f3177231d50b25` (Engineering `2.4.1-beta.13`).

`R/` below means `hass_mcp_engineering_beta/ha_mcp_engineering/`.

## 1. Final protected-file list (O8)

Runtime changes are limited to these two protected files:

| File | Items | Why |
| --- | --- | --- |
| `R/f3/executor.py` | R6, R3b | Adds `DispatchAuthorityRefused` and a closed reason-code set. Its preflight and dispatch-authority handlers report the refusal's code instead of the generic `preflight_failed`. The early return for an already-satisfied preflight (≈473-501) is reached only after the wrapper below has validated Core authority. The executor does not change that branch itself. |
| `R/f3_runtime/runtime.py` | R3/R3b, R6, R2 | Adds a `_CoreVerificationAdapter.preflight` lease wrapper and a dispatch-window authorizer. Changes `_CoreDispatchAuthorityGuard` so it raises the R6 refusal on unmapped operations and remembers its authority. Rewrites the order in which `_cancel_locked` records a cancel. Adds a `cancel_only` mode to `_load_prepared`. Adds a recorded-cancel check to `_recover_active_candidates`. Adds a cancelled-child branch to `_project_locked`. |

Release materialization for the next beta adds the usual protected release
surfaces under the existing `promote_next_release.py` flow
(`hass_mcp_engineering_beta/config.yaml`, `hass_mcp_engineering_beta/Dockerfile`,
`.release/`). Those changes come from the promotion script and are listed
separately in the PR when they are applied. No other protected path is
changed.

Read-only imports, not edited:
- `R/ha_core_readmission/routes.py`: `f3_requirements`, `CORE_REQUIREMENT_UNMAPPED`.
- `R/ha_core_readmission/runtime.py`: acquire/consume/revalidate/finish/release.

Explicitly not changed:
- `R/governance/service.py`, `R/tools/governance.py`,
  `R/providers/operational_lifecycle.py`, `R/providers/upstream_read_gateway.py`,
  `R/ha_core_readmission/coordinator.py`, `R/routing.py`.
- `hass_mcp_admin/` (stable v1), `.github/workflows/`, `.coderabbit.yaml`, the
  signed registry, HOLD and support windows.

New non-protected files:
- `tests/test_hamcp189_authority_cancel.py`
- this note
- `docs/HAMCP_189_PR_A_ACCEPTANCE.md`

HAMCP-162 overlap: none. HAMCP-162 Part 2 touches `upstream_read_gateway.py`,
`coordinator.py`, `models.py` and `routes.py`. This PR edits none of them and
only imports two existing `routes.py` names.

## 2. R3/R3b — Core authority before any preflight read (O1)

Defect (triage §3.5). `SharedOperationExecutor.execute` calls
`adapter.preflight` before `dispatch_authority.acquire`. The preflight reads
(GET states, config reads, dashboard pre-reads, operational planning evidence)
therefore run with no Core lease. When those reads show the target is already
reached, executor.py:473-501 terminalizes `succeeded_verified` and increments
`verification_successes`, all with `lease_attempts == 0` under degraded Core
(triage probe D).

Fix (triage option R3-b): `_CoreVerificationAdapter` gets a `preflight` method
that runs before the inner adapter's preflight:

1. Calls `reconcile_once("mutation_pre_dispatch")`, which is an existing
   trigger in `_AUDIT_TRIGGERS`.
2. Unmapped operation (`core.unsupported_f3_operation` among
   `f3_requirements(prepared)`) → raise
   `DispatchAuthorityRefused("core_requirement_unmapped")`.
3. Calls `acquire_f3(prepared, plan_created_at=plan.created_at)`. If it returns
   None → raise `DispatchAuthorityRefused("dispatch_authority_unavailable")`.
4. Calls `consume(authority)`. If it returns None → `release` and raise as in
   step 3.
5. Installs a request-telemetry authorizer that chains the prior one and
   revalidates this authority. All REST, websocket, dashboard-MCP and
   operational observed-read clients already enforce
   `authorize_core_dispatch()`. A read made after authority is retired is
   therefore refused at the client before it is sent.
6. Runs the inner preflight, then revalidates. If authority was retired
   during the reads, the preflight raises the refusal, so no "already
   satisfied" conclusion can stand without current authority.
7. In `finally`: sets the authorizer to `lambda: False`, calls
   `finish(commits)`, and restores the prior authorizer or ends the request.
   This is the same shape as the existing `_observe_with_authority`.

The executor handles `DispatchAuthorityRefused` before its generic
`except Exception`. It terminalizes `provider_unavailable_pre_dispatch` with
the refusal's code from a closed set. The existing taxonomy is kept: that
outcome is pre-dispatch and retryable by a new plan, there is no approval
consumption, and there is no verified outcome.

Dispatch window: `_CoreVerificationAdapter.dispatch` installs an authorizer
for the length of the inner `dispatch` call. The authorizer allows a client
call only after the executor's `before_dispatch` has consumed the guard's
authority, and only while `revalidate(authority, commits)` holds.

- Configuration, dashboard and helper adapters make their HA call after
  `before_dispatch`, so the authorizer closes the gap between the executor's
  revalidate and the send.
- The operational `_execute` path does not consult telemetry. There, the
  executor's synchronous revalidate immediately before `call_tool` remains the
  boundary. This is disclosed and unchanged.

HAMCP-181 interplay: the outcome strings, the `already_reached` diagnostics
and the projections are unchanged. Only the ordering changes. When Core is
admitted, an already-reached target still returns `succeeded_verified` with
the same codes.

Fan and power are not on this path. They use their own executor with
`FanCoreAuthority`, and their preflight reads go through `core.read`, so they
are already authorized.

R3c (config stale-state race) is inherent to the HA config API. It is
disclosed only (triage §3.5).

## 3. R6 — exact reason for an unmapped operation (triage §3.8)

- `executor.py`: `class DispatchAuthorityRefused(OperationExecutorError)` with
  a `diagnostic_code`, and a closed set:
  `{"dispatch_authority_unavailable", "core_requirement_unmapped"}`.
  - A code outside the set collapses to `dispatch_authority_unavailable`.
  - The refusal is honored both in the preflight handler and around
    `dispatch_authority.acquire`.
- `runtime.py`: `_CoreDispatchAuthorityGuard.acquire` raises
  `DispatchAuthorityRefused(CORE_REQUIREMENT_UNMAPPED)` when
  `f3_requirements(prepared)` contains `core.unsupported_f3_operation`. It does
  this before any lease attempt.

With R3 the preflight wrapper refuses first. Both sites use the same code.

Test: the registry operation sets equal the four `routes.py` F3 sets. This
proves the path is unreachable today and fails if they drift.

## 4. R2 — durable, provider-independent cancel (O3)

Defect (triage §3.2): `_cancel_locked` calls `_load_prepared`, which re-reads
the live ha-mcp provider identity for v3 operational tasks without an intent.
An ha-mcp outage or identity drift therefore fails the cancel before anything
is recorded. A later recovery pass can then dispatch the task.

Fix:

1. **Record before contact.** `_cancel_locked` refuses exactly as today
   (state, intent, refusal wording). It then finds the first unresolved child.
   - If that child has a nonterminal record, `children.cancel(...)` runs
     immediately. There is no `_load_prepared` and no provider contact.
   - If no record exists yet, `_load_prepared(plan, task, cancel_only=True)`
     builds the claim. It uses the declaration-bound provider identity
     (`provider_dependency_key` slug plus the stored
     `provider_identity_evidence_hash`) and skips the live drift read.
   - The child cancel event carries the bounded diagnostic
     `operator_cancel_requested`.
   - The public `task_cancelled_pre_dispatch` event follows as before.
2. **Apply and recovery keep the live drift check.** `cancel_only` defaults
   to False, so `provider_lock_identity_changed` still refuses apply.
3. **Recovery honors a recorded cancel.** A new `_cancel_recorded(task_id)`
   is true when the public task is `cancelled_pre_dispatch` or any child
   carries the operator-cancel event. `_recover_active_candidates` checks it
   twice: before `_load_prepared`, and again after every await, immediately
   before `_enter_public_preflight`/`_execute_child`. On true, the task is
   projected only; it is never executed. Executor-level protection remains:
   a terminal child cannot be claimed or given an intent.
4. **Projection.** In `_project_locked`, a failed, intent-free child that
   carries the operator-cancel event projects `task_cancelled_pre_dispatch`
   (`cancelled_pre_dispatch`). It does not project `preflight_failed`.
   This covers:
   - restart between the child cancel and the public event;
   - the recovery race in which the public task is already cancelled. Before
     this change, the race raised a projection storage error.
5. **Truthful result.** The cancel result adds `provider_contacted: false`.
   This is an additive field, and no tool output schema is pinned. The cancel
   path never contacts a provider.

Residuals, disclosed and not fixed here:
- **R2b/O9.** Cancelling an expired plan whose child record was never created
  still refuses with `plan_expired`, because `_prepare` checks expiry. No
  dispatch is possible: an expired plan cannot be approved or consumed.
- **R2-telemetry.** Stays in PR B, per the triage grouping.

## 5. Invariants

These are unchanged and covered by the existing suites plus the new tests:
- at-most-once dispatch;
- no blind redispatch;
- saved intent is never discarded;
- a cancel after intent is refused;
- the approval-consumption ordering;
- the HAMCP-164 Core gate and the HAMCP-181 outcome strings.

## 6. Tests and mutations

New: `tests/test_hamcp189_authority_cancel.py`.

| Mutation | Must fail |
| --- | --- |
| Remove the preflight lease wrapper | degraded-Core already-reached refusal tests |
| Recovery ignores the recorded cancel, and the projection branch is removed | cancel-then-recover tests |
| Cancel performs the live provider identity read before recording | cancel-during-outage tests |

## 7. Release

- Next beta at materialization time. It is the first to be ready, and is
  served together with HAMCP-162.
- PR C (R8, L1, L2) is a separate small PR, to be merged first or in the same
  release (O6).
