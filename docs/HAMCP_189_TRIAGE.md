# HAMCP-189 — Triage of HAMCP-164 Residuals R1–R8 and Codex Lows L1/L2

Status: triage report only. No source, runtime, policy, registry, workflow,
release or stable-v1 change. Draft; not Ready.

- Base: `origin/main` `06d555523eae8343605c0d3605f3177231d50b25`, the merge
  of PR #263 (HAMCP-164), Engineering `2.4.1-beta.13`.
- Source of the items:
  - R1–R8: `docs/HAMCP_164_CORE_GATE_FAIL_CLOSED_DESIGN.md` §8.
  - L1, L2: the independent Codex review of PR #263 head `34e75f2` (Low
    findings).
- Method: read the source at the base, then run read-only offline probes
  against the in-repo fakes and harnesses. No live Home Assistant, deployed
  endpoint, credential or network was used.

Path prefix: `R/` = `hass_mcp_engineering_beta/ha_mcp_engineering/`. Every
file under `R/` is protected (AGENTS.md "Frozen or Protected Paths"); each fix
below needs explicit authorization for its exact files.

**The permissive default behind most items.**
`RequestTelemetry.authorize_core_dispatch()` (`R/request_context.py:116-124`)
returns `True` when no authorizer is installed. The Core clients check it only
when request telemetry exists:

- REST: `R/clients/rest.py:76-77`
- WebSocket: `R/clients/websocket.py:89`, `:207`
- upstream MCP: `R/clients/mcp.py:375`

The gateway installs an authorizer only for tools with Core requirements
(`R/routing.py:805`, `:887-902`). An allowlisted or delegated tool therefore
runs with no Core check unless its own code installs one.

## 1. Summary

| ID | Finding (one line) | Reachable in production today | Consequence | Severity | Recommendation | PR |
| --- | --- | --- | --- | --- | --- | --- |
| R3b | *New.* A governed step whose preflight finds the desired state already reached is reported `succeeded_verified` without acquiring Core authority. | Yes: any apply under degraded Core whose target already matches. | Misleading "verified" outcome from unadmitted reads. | **Medium** | Fix now | A |
| R5 | A delegated read missing from `DELEGATED_CORE_REQUIREMENTS` would be published and dispatched with no Core lease. | No tool today; one missing map line away. | Unadmitted Core reads through ha-mcp. | **Medium (latent)** | Fix now, coordinated with HAMCP-162 | D |
| R2 | `_provider_identity` ha-mcp reads in apply-prepare and cancel run unauthorized. A v3 operational **cancel fails** when ha-mcp is unavailable or the identity drifts. Recovery can then still dispatch the task. | Yes (ha-mcp outage or identity drift during a pending v3 operational task) | Wrong outcome: a backup, reload or restart the operator tried to cancel can run later. It still needs approval and Core authority. | **Medium** | Fix now (R2-cancel) | A (cancel), B (telemetry) |
| R1 | `rollback_change` reads Core with no authority and creates a rollback plan while Core is degraded. | Yes | Misleading `rollback_plan_created` for a plan that cannot apply; no write. | Low | Batch | B |
| R3 | F3 preflight reads, and the write call itself, run with no client-level Core authorizer. Apply is still gated by acquire/consume/revalidate. | Yes | No unsafe write. Unadmitted reads before refusal. | Low | Fix now (same change as R3b) | A |
| R4 | The approval-notification worker POSTs `/services/notify/mobile_app_*` with no Core lease, readmission check or telemetry. | Yes (when notifications are configured) | Advisory push sent to an unadmitted Core. **Not an authority bypass.** | Low | Accept with disclosure (owner decision) | E (optional) |
| R6 | An unmapped F3 identity is refused as `dispatch_authority_unavailable`, not `core_requirement_unmapped`. | No (registry pins the 15 identities) | Misleading reason code only. | Low | Batch | A |
| R8 | A list- or dict-valued `params.name` raises `TypeError` in the gateway audit `finally`. | Yes, authenticated client only | Lost audit record, ASGI error log, connection closed. No tool runs. | Low | Batch | C |
| L1 | An empty-string tool name is refused correctly but audited as a generic `mcp_request`, without the unmapped reason or metric. | Yes, authenticated client only | Incomplete evidence. | Low | Batch | C |
| L2 | beta.13 release notes say eight allowlisted tools use "Engineering local state only". Three do not. | Documentation | Overstated claim. | Low | Batch (docs) | C |
| R7 | A non-string `tool_name` is left to MCP SDK validation. | Yes | None: the SDK rejects it before any tool lookup. | None (Info) | No action | — |

Not a new finding but recorded for completeness (R3c): a governed
configuration write is not compare-and-set against Home Assistant. An edit made
outside Engineering between preflight and the write is overwritten. This is
inherent to the Home Assistant config API; R3 only widens the window (§3.5).

## 2. The approval-notification worker (R4) — direct answer

**Verdict: benign with respect to authority. It is a gap in the "every Core
call is admitted" invariant, caused by the worker running outside any request.
It is not an approval-authority bypass and not a race on authority.**

Evidence:

1. The worker is started once at process start, after
   `CORE_READMISSION.reconcile_until_initialized()` and before any listener,
   by `asyncio.create_task(notification_manager.run())`
   (`R/application.py:472-485`). The task's copied context has no request
   telemetry (`_REQUEST` is `None`).
2. `_dispatch` posts `POST /services/notify/<service>` through the shared
   REST client (`R/governance/approval_notifications.py:337`, `:388-391`).
   With no telemetry, `rest.py:76-77` skips `authorize_core_dispatch()`.
   No Core lease is taken, readmission is not consulted, and no request
   telemetry records the call.
3. At startup, `reconcile_pending` (`R/application.py:479-481`,
   `approval_notifications.py:189-218`) re-queues a notification for every
   pending review. So pushes are sent even when Core readmission has just been
   refused.
4. **Offline probe** (`r4_probe.py`): the worker is started first, as in
   production. A request is then opened whose Core authorizer refuses
   everything (`authorize_core_dispatch() == False`), and a notification is
   enqueued from inside it. Result: the worker POSTed
   `/services/notify/mobile_app_probe` with `telemetry=False` and
   `would_refuse=False`; counters `submitted=1, failed=0`.

Why it is not an authority bypass:

- The target is fixed by operator configuration. The service must match
  `^notify\.mobile_app_[a-z0-9_]{1,128}$` (`:30-32`, validated in `__init__`
  `:82-83`) and cannot be set by an MCP client.
- The body is fixed text plus a tag and an `/app/<verified add-on slug>`
  navigation link (`:356-381`). It carries no plan content and no per-plan
  authority.
- `status_for` and `health_snapshot` declare `authority: none` and
  `approval_performed: False`. Every audit record says
  `approval_authority_changed: False`.
- Approval is recorded only through the Ingress approval panel against the
  persisted plan hash. Apply then needs its own F3 Core authority.
- The send rate is bounded: a 256-entry queue, one notify per challenge
  (`_notification_attempted`), and clears only on lifecycle events.
- An MCP client can cause a notification only by creating a plan and requesting
  approval, the intended trigger.

The timing aspect is real but harmless. The notification is decoupled from
the request that caused it (`approve_change_plan` is allowlisted and holds no
lease anyway). At send time, Core may be degraded, restarting or unreachable:

- An unreachable or restarting Core fails the POST. It is recorded as
  `provider_unavailable`, `provider_timeout` or `provider_rejected`, with no
  retry.
- A degraded but reachable Core receives the push. This is arguably useful:
  approval is local state and can still be granted, and the apply waits for
  readmission.

**Health already discloses the route** as
`direct_home_assistant_rest_allowlisted_notification` (`:248`), and the
HAMCP-164 allowlist justification for `approve_change_plan` names the
out-of-lease delivery. L2 (§4.3) is the place where this exception was
understated.

**Fix options:**

| Option | Change | Files | Degraded-Core behavior change | Effort |
| --- | --- | --- | --- | --- |
| R4-0 (recommended) | Accept with disclosure. Keep the documented exception. Correct the L2 wording. | docs only | None | trivial |
| R4-1 | Before each POST, the worker consults a Core-admitted predicate injected by the runtime. When not admitted, it records `failed` with a new category such as `core_not_admitted`, with `dispatched=False`. Optionally the POST runs inside its own `begin_request` telemetry so it is counted. | `R/governance/approval_notifications.py`, `R/governance/runtime.py` (wiring) | **Yes**: no pushes while Core is degraded; reconciliation at a degraded startup would also send nothing, and nothing retries. | ~0.5 day + tests |
| R4-2 | A dedicated Core capability profile for the notify service (probe + profile), with a lease per POST. | R4-1 files + `R/ha_core_readmission/profiles.py`, `source.py`, `models.py` (capability class) | Yes, as R4-1 | 1–2 days; **`models.py` overlaps HAMCP-162** |

Tests for R4-1: a worker under refused Core records `core_not_admitted` with
zero POSTs; admitted Core posts once; queue and dedupe behavior is unchanged
(`tests/test_beta30_approval_notifications.py`,
`test_beta32_approval_notifications.py`,
`test_rc1_approval_notification_navigation.py`).

HAMCP-155 does not depend on R4. Deletion plans produce the same notifications
through the same path.

## 3. Items in detail

### 3.1 R1 — `rollback_change` planning reads Core without authority

**What.** `rollback_change` is allowlisted with basis
`downstream_core_authority` (`R/ha_core_readmission/routes.py:269`), so the
gateway takes no lease and installs no authorizer. The call chain is:

1. `R/tools/governance.py:453-465`
2. `R/governance/service.py:12544-12550` (plan lock)
3. `R/f3_runtime/runtime.py:3451-3608` `create_rollback_plan`
4. The live read at `:3520-3525`, through `service._read_configuration_resource`
   (`service.py:3244-3259`, then `R/governance/runtime.py:36-63`, then
   `R/governance/resources.py:557-618`). That read is REST
   `GET /config/{automation|script}/config/{id}`, a WebSocket `<helper>/list`,
   or an entity-registry get.

**No write is reachable.** `create_rollback_plan` never executes and returns
`provider_dispatch_occurred: False`. The legacy `_rollback_locked` write path is
dead in production because the F3 runtime is always configured
(`R/governance/runtime.py:180-191`). An existing rollback plan is returned
locally without any read (`runtime.py:3466-3480`).

**Use of the read.**

- The live fingerprint decides which operations the inverse plan includes, and
  becomes the new plan's baseline `current_state_fingerprint`.
- `proposed_config` comes from the persisted source plan.
- At apply, the plan needs exact-hash external approval, then F3 checks it:
  - acquire (`R/f3/executor.py:540`), consume (`:595`), revalidate (`:626`);
  - the stale-plan cutoff (`R/ha_core_readmission/runtime.py:902-912`);
  - the adapter's `stale_target_state` preflight.

**Impact.**

- Under degraded Core with the process up, planning returns
  `rollback_plan_created` / `approval_required`. That is misleading: apply
  refuses `dispatch_authority_unavailable` before consuming approval. After
  readmission, the new generation makes the plan stale. Because
  `rollback_change` returns the same existing plan, the plan is dead on
  arrival. (This last step is inferred from code, not probed.)
- With Core down, the read fails with a bounded structured error.
- The forward planner `create_configuration_plan` uses the same builder but is
  mapped to `core.dependency_helper_planning` (`routes.py:142-143`), so the
  two paths are inconsistent.
- Telemetry counts the reads correctly.

**Probe** (`probe_r1_r2.py`, pinned MCP 1.30.0):

- P1, real gateway, degraded runtime (Core `2026.9.2`): `rollback_change`
  `route=core_independent`, `authorizer_is_none=True`,
  `ha_request_count=1`, not refused. Control: `server_info(check_ha=true)` was
  refused `CoreCapabilityUnavailable`, with zero requests.
- P2: F3 rollback planning made 2 reads with no authorizer; status
  `rollback_plan_created`; 0 writes.

**Severity: Low.** No unsafe write; the misleading outcome is caught at apply.

**Fix options.**

| Option | Change | Files | Degraded-Core change | Effort |
| --- | --- | --- | --- | --- |
| R1-B (recommended) | An in-tool Core lease in `create_rollback_plan`, after the idempotent-return branch, for `core.dependency_helper_planning` (plus `core.entity_registry_metadata_operation` for entity-metadata sources). Reuse the `R/dependency/authority.py` install/acquire/consume/revalidate/release pattern. | `R/f3_runtime/runtime.py` | **Yes**: fresh rollback planning refuses while Core is not admitted; the existing-plan return keeps working. | ~0.5 day + tests |
| R1-A | Move `rollback_change` from the allowlist into `STATIC_TOOL_CORE_REQUIREMENTS`. | `R/ha_core_readmission/routes.py` + HAMCP-164 table test | Yes, and also refuses the purely local existing-plan return. | small; **overlaps HAMCP-162 (`routes.py`)** |
| R1-C | Document only. | docs | None | trivial |

Tests for R1-B:

- degraded Core refuses a fresh plan with zero HA requests;
- an existing plan is still returned under degraded Core;
- admitted Core creates the plan, and the lease is released.

### 3.2 R2 — `_provider_identity` reads in apply-prepare and cancel

**What.** For contract-v3 operational plans (backup, reload, add-on restart,
Home Assistant restart), the runtime reads the provider identity through
ha-mcp. These are two upstream `ha_get_addon` reads, Supervisor-backed and not
Core REST:

- path: `R/f3_runtime/runtime.py:1529-1537`, then
  `R/providers/operational_lifecycle.py:591-629`
  `authoritative_provider_identity`, then `get_addon` (`:363-520`);
- apply: `_initialize` (`:1735-1750`), then `_prepare` (`:1596-1669`);
- cancel: `_cancel_locked` (`:3028-3044`), then `_load_prepared`, then the live
  re-read and drift check at `:1882-1897`.

`cancel_execution_task` is allowlisted as local state (`routes.py:248-252`),
and `apply_change_plan` as `downstream_core_authority` (`:263`). Neither
installs an authorizer. `_execute_observed_read` (`operational_lifecycle.py:631-670`)
refuses only when an installed authorizer returns `False`.

**No write is reachable.** The identity binds lock keys
(`R/f3/operational_locks.py:197-240`) and declaration attribution. Dispatch is
still gated by `acquire_f3`, consume and revalidate, and by the operational
adapter's preflight checks (`R/f3/operational_adapter.py:436-523`).

**Availability defect (the material part).** `_cancel_locked` has no handling
around `_load_prepared`. A pre-dispatch cancel of a v3 operational task
therefore **fails** in either case:

- ha-mcp is unavailable: `upstream_addon_identity_unavailable`
  (`R/governance/operational_lifecycle.py:917-925`);
- the identity changed: `provider_lock_identity_changed` (`runtime.py:1894-1897`).

This contradicts the allowlist justification ("must remain available while
Core is unavailable"). Strictly, the dependency is on ha-mcp rather than Core
readmission. Configuration (v2), dashboard and helper-state cancels make no
such read.

What a failed cancel leads to: see §3.2a (recovery behavior).

**Telemetry.** The reads are counted in `upstream_request_count`. The counter
increments before the authorizer check (`operational_lifecycle.py:644`), so a
refused read is still counted as an attempt.

**Probe** (`probe_r1_r2.py`):

- P3: `_initialize` made 1 identity read with no authorizer. Cancel made 1
  read, ending `cancelled_pre_dispatch` with `lifecycle_dispatch_count=0`.
- P4: `_execute_observed_read` with no authorizer dispatched
  (`upstream_request_count=1`). With an authorizer returning `False` it was
  refused, but the count was still 1.

**Severity: Medium** (§3.2a). It is Low for unauthorized action, since every path still needs approval and Core authority. It is Medium because a failed cancel is not durable and the operation can still run.

**Fix options.**

| Option | Change | Files | Behavior change | Effort |
| --- | --- | --- | --- | --- |
| R2-cancel (recommended) | On cancel, `_load_prepared` uses the declaration-bound identity and skips the live drift read. Apply keeps the drift check. | `R/f3_runtime/runtime.py` | **Yes**: cancel succeeds while ha-mcp is unavailable, and after identity drift (the cancel dispatches nothing). | ~0.5 day + tests |
| R2-apply | Leave as is (the read is downstream-gated), or wrap `_provider_identity` in an in-tool lease. A gateway Core mapping is wrong: this is not a Core read. | `R/f3_runtime/runtime.py` | No / minor | small |
| R2-telemetry | Count the upstream attempt only after the authorizer passes. | `R/providers/operational_lifecycle.py` (not the HAMCP-162 read gateway) | Telemetry only | small |

Tests:

- cancel with ha-mcp unavailable ends `cancelled_pre_dispatch` with zero
  upstream reads;
- cancel after identity drift succeeds;
- **negative:** apply still refuses on drift with `provider_lock_identity_changed`;
- cancel after dispatch intent is still refused.

#### 3.2a Recovery after a failed cancel

A failed cancel **does not stop the task**. Periodic recovery can later
carry the same pre-intent task through to approval consumption and provider
dispatch. Nobody has to call `apply_change_plan` again.

**The chain** (`R/f3_runtime/runtime.py`):

1. `_cancel_locked` reaches `_load_prepared` (`:3044`) before it writes
   anything. If that raises, no durable change is made and the task stays
   `CREATED`/`PREFLIGHT`.
2. `_active_recovery_candidates` (`:4519`, mode selection `:4681-4698`) selects
   every nonterminal F3 task. `_recover_once` (`:5786-5798`) passes the
   pre-intent ones to `_recover_active_candidates` (`:5264`).
3. Unless the plan carries a historical policy version (`:5318-5332`),
   recovery calls `_load_prepared` (`:5352`), then `_enter_public_preflight`
   (`:5357`), then `_execute_child` with `readback_only=False` (`:5367`).
   This is full execution: claim, locks, preflight, Core acquire, approval
   consumption, intent, dispatch.
4. Existing coverage asserts this forward drive:
   `tests/test_rc5_recovery_approval.py:210`
   `test_owner_loss_before_intent_can_recover_once` runs `recover_once` and
   then expects `succeeded_verified` with `dispatch_count == (1, 1)`.

**The sequence that matters:**

1. ha-mcp is briefly unavailable.
2. The operator calls cancel. Cancel fails with
   `upstream_addon_identity_unavailable`.
3. Recovery's own `_load_prepared` fails too. The child gets a `bounded_retry`
   backoff of 5 s, doubling up to 300 s (`:5409-5427`).
4. ha-mcp returns with the same identity.
5. The next sweep can dispatch the backup, reload, add-on restart or Home
   Assistant restart that the operator tried to cancel. Recovery still needs:
   - a current approval, or one already consumed with a matching reference
     (`_consume_approval` `:2194`);
   - current Core authority;
   - a plan that is not stale against Core.
6. A cancel retried after ha-mcp returns races recovery.

The operator does see the cancel error, but nothing records a cancel request,
so nothing tells recovery to stop.

**Persistent drift** (`provider_lock_identity_changed`) makes both cancel and
recovery fail on every attempt. The task then stays nonterminal, retrying
every ≤300 s, until the identity returns.

**Related observation (R2b; recorded, not triaged as a new item).** The runtime
has no pre-intent deadline or plan maximum age.

- An expired approval does not end the task. The expiry is raised inside
  `before_dispatch`. That goes through `record_pre_intent_retry`
  (`R/f3/executor.py:688-713`, `R/f3/persistence.py:757-786`), which puts the
  task back to preflight.
- Recovery then retries it indefinitely. Each attempt is refused before
  dispatch, so it is safe but unbounded.
- Cancel remains the only way to end it, and only while `_load_prepared`
  succeeds.

Whether to add a pre-intent terminal deadline is a separate owner decision
(O9).

**Effect on R2 severity:** cancel availability becomes a *wrong outcome*. An
operational action the operator tried to cancel can still run. It does not
become an unauthorized one: it still needs Josh's exact-hash approval and
current Core authority. **R2 is Medium** and **R2-cancel joins PR A**.

R2-cancel tests (added to those above):

- with ha-mcp unavailable, cancel durably terminalizes the task, and a later
  `recover_once` dispatches nothing;
- after identity drift, cancel terminalizes and recovery dispatches nothing.

### 3.3 R3 — F3 preflight reads and the write run without a client-level authorizer

**What.** The order inside one governed step:

1. The gateway installs no authorizer for `apply_change_plan` (allowlisted).
2. Durable locks are taken (`R/f3/executor.py:420`), then `adapter.preflight`
   runs (`:468`). The preflight reads all pass the permissive check, and none
   of them is a write:
   - configuration: `POST /config/core/check_config`, validate-only
     (`R/governance/resources.py:860-865`), then `GET /states/{id}`
     (`R/f3_configuration/gateway.py:34-52`) and entity-metadata evidence
     reads (`R/f3_configuration/adapter.py:331`, `:358`);
   - dashboard: a pre-read and a best-practice-key read through ha-mcp
     (`R/f3_dashboard/adapter.py:395`, `:414`);
   - operational: `planning_evidence` reads (`R/f3/operational_strategies.py`).
3. `dispatch_authority.acquire` (`executor.py:540`) runs
   `reconcile_once("mutation_pre_dispatch")` and then `acquire_f3`
   (`R/f3_runtime/runtime.py:236-239`). If either fails, the step is refused
   at `executor.py:550-557`.
4. `before_dispatch` (`:565`) consumes approval (`:585`), consumes authority
   (`:595`), commits intent (`:608`) and revalidates (`:626`).
5. The write is sent, e.g. `R/f3_configuration/adapter.py:479`.

The design note's claim holds: **no authorizer is installed for the dispatch
window**. The authorizer at `R/f3_runtime/runtime.py:599-624` belongs to
`_CoreVerificationAdapter` and covers only `observe` and `recover`. Its
`__getattr__` passes `preflight` and `dispatch` straight through.

- For REST, the gap between revalidate and the send is synchronous.
- The dashboard path waits on the network for an MCP session handshake with no
  recheck.

**Degraded Core.** Preflight reads happen, then `acquire_f3` refuses. The task
ends `failed_pre_dispatch` with `provider_unavailable_pre_dispatch` and no
dispatch intent. Approval is not consumed and the locks are released. This is
truthful, and it is pinned by `tests/test_core_semantic_continuity.py`
(`:337`, `:353`).

**Telemetry.** Preflight reads are counted in request telemetry, not in the
Core lease counters.

**Probe** (`probe_r3_r6.py`):

- A (admitted): preflight runs with no authorizer, then acquire, consume and
  revalidate, then the write runs with no authorizer; result
  `succeeded_verified`.
- B (degraded): preflight runs, then acquire refuses with
  `provider_unavailable_pre_dispatch`; no approval consumed, no locks left.

**Severity: Low.** No write becomes reachable. The step refuses before approval
or dispatch.

### 3.4 R3b — *new*: "already reached" is reported verified without Core authority

**What.** When preflight finds that the desired state is already in place,
`R/f3/executor.py:473-500` terminalizes the step with
`terminalize_verified_no_dispatch` (`succeeded_verified`). It increments
`verification_successes` and `preflight_noop_successes`, and returns
**before** `dispatch_authority.acquire` at `:540`.

The real adapters take this path, e.g. `_preflight_already_reached` in
`R/f3_configuration/adapter.py:359-362` and `:922`. So under degraded Core, a
step can be reported **verified** from reads made with no Core authority.

This contradicts the verification-authority contract that
`_CoreVerificationAdapter` enforces for read-back (`R/f3_runtime/runtime.py:599-624`).

**Probe** (`probe_r3_r6.py`, D): degraded Core (unreviewed `2026.9.2`
fixture), with the target already matching. Result: `succeeded_verified` with
`lease_attempts=0`.

**Impact.**

- No write happens; the result is a verification claim backed by unadmitted
  evidence.
- Under an unsupported Core whose response semantics changed, "already reached"
  could be wrong, and the task would close as verified instead of refusing.
- **Relevance to HAMCP-155:** governed deletion will have an "already absent"
  preflight outcome. On this path, a deletion would be reported verified
  without Core authority.

**Severity: Medium.**

### 3.5 R3 and R3b — fix options (one change)

| Option | Change | Files | Degraded-Core change | Effort |
| --- | --- | --- | --- | --- |
| R3-b (recommended) | Add a `preflight` wrapper to `_CoreVerificationAdapter` with the same reconcile → acquire → consume → revalidate → finish pattern as `_observe_with_authority`. Also install a revalidating authorizer for the `dispatch` call window. | `R/f3_runtime/runtime.py` | **Yes**: degraded Core refuses before any HA read; "already reached" under degraded Core becomes a pre-dispatch refusal instead of `succeeded_verified`; one extra reconcile per step. | ~1 day + tests |
| R3-a | Lease only the dispatch window. Leaves R3b open. | `R/f3_runtime/runtime.py` | Minor | ~0.5 day |
| R3-c | Reorder `acquire` before preflight in the executor. This needs a non-consuming lease check and changes the reviewed consume ordering. | `R/f3/executor.py`, `R/ha_core_readmission/runtime.py`, possibly `coordinator.py` | Yes | larger; **`coordinator.py` may overlap HAMCP-162** |

Tests for R3-b:

- degraded Core with the target already reached gives
  `provider_unavailable_pre_dispatch` with zero HA reads and no
  `verification_successes`;
- admitted Core with the target already reached is still `succeeded_verified`;
- every preflight and dispatch client call sees an installed authorizer;
- revoking authority between revalidate and send gives not-sent or
  indeterminate, settled by read-back;
- existing F3 suites stay unchanged (`test_f3_adapter_core`,
  `test_core_semantic_continuity`, `test_core_update_continuity`,
  `test_ha_core_2026_9_integration`).

**R3c (inherent, disclosed).** The configuration state fingerprint is checked
only in preflight (`R/f3_configuration/adapter.py:358`, `:725-756`), and the
Home Assistant config POST is unconditional (`:434-484`). An edit made outside
Engineering (UI or an integration) inside the window is overwritten, and
read-back still passes because it compares against the proposed config.
Dashboards are protected by `config_hash` (`R/f3_dashboard/adapter.py:503`).
There is no fix within the Home Assistant API. R3-b narrows but does not close
the window. **No action; disclose.**

### 3.6 R4 — see §2.

### 3.7 R5 — an unmapped delegated read is published and dispatched without Core

**What.** `delegated_requirements(name)` returns `()` for an unknown name
(`R/ha_core_readmission/routes.py:335-338`). Every read-gateway site guards
Core with `if self._core_runtime is not None and requirements:`, so an empty
tuple skips Core entirely:

- catalog publication: `R/providers/upstream_read_gateway.py:1200-1201`. No
  `route_status` call, so the tool is published;
- call time: `:3049-3052`. `acquire` is never called, `lease.core_authority`
  stays `None`, and the commit at `:3104` skips `consume`;
- held canary: `:3987` and `:4108`.

The `R/routing.py` gateway deliberately acquires nothing for published
delegated rows (`_gateway_core_route`, `:63-77`), so the read gateway is the
only Core enforcement point for these reads.

The design note's statement that "the script reader fails closed on an empty
requirement set" is true but narrow. The script reader calls `route_status(())`
(`:3209`), which fails closed because of
`available = bool(capability_ids) and …` (`R/ha_core_readmission/runtime.py:638`),
and `coordinator.acquire_routes(())` returns `None` (`coordinator.py:368-374`).
The general publication and call paths never reach those checks.

**Reachability.**

- No admitted read is unmapped today. All ten compiled policy versions
  (7.14.1 through 8.6.0) are fully covered (`probe4`).
- A gap needs a source change. The signed registry binds compiled
  `(policy_resource, sha256)` (`R/ha_mcp_readmission/ha_mcp.py:239`), so it
  cannot add tools at runtime. Two ways it can happen:
  - add an automatic read or held-for-canary entry to a policy JSON without a
    `routes.py` entry;
  - add a new policy version with such an entry.
- HAMCP-162 adding `ha_config_get_yaml` is exactly this shape.

**Probe** (`probe5`): `ha_config_list_dashboard_resources` removed from the map,
with dashboard Core evidence missing.

- Mapped: 27 tools published, 1 withheld.
- Unmapped: 28 published, 0 withheld, and the call was dispatched upstream
  (`FakeTransport` recorded the call).

**Guards are tied to specific versions.**

- `tests/test_ha_core_2026_9_integration.py:2914` checks exact set equality
  for 8.4.3 and 8.5.0 only.
- `tests/test_hamcp129_platform_reads.py:1335` checks a subset for
  `("8.4.3","8.5.0","8.6.0")`.
- The HAMCP-164 test checks the map's own values only.
- CI would catch a gap added to those versions, but not one in a new policy
  version (e.g. 8.7.0) or an older one.

**Severity: Medium (latent).** Nothing is exposed today. But it breaks the
HAMCP-164 rule that a missing mapping never implies Core independence, it is
one line away from happening, and the CI guard depends on the version.

**Fix.**

- Remove `and requirements` at `:1201`, `:3052` and `:4108`, so the runtime is
  always consulted. Both runtime primitives already fail closed on `()`.
- Optionally tag the withheld entry `core_requirement_unmapped`.
- Leave `routes.py` unchanged.
- Tests: iterate every `registry.by_version` policy and assert every
  admitted or held read has a non-empty mapping; plus a runtime test that pops
  one entry and asserts it is withheld and its call is refused with zero
  transport calls.
- Effort: ~1–2 hours plus tests.
- Behavior: none for existing tools. A future unmapped read is withheld instead
  of silently published.

**HAMCP-162 overlap: yes.** Both changes touch
`R/providers/upstream_read_gateway.py`. The hunks are separate, but they are
coupled in meaning: after this fix, a HAMCP-162 tool without a routes entry is
withheld. The order needs coordinating (owner decision O5).

### 3.8 R6 — unmapped F3 identity reason code

**What.**

1. `f3_requirements` (`R/ha_core_readmission/routes.py:479-511`) adds
   `core.unsupported_f3_operation`, which has no profile, for an unknown
   identity.
2. `acquire_routes` then returns `None` (`R/ha_core_readmission/runtime.py:742-747`,
   counted as `lease_failures`).
3. The executor's catch-all at `R/f3/executor.py:540-551` turns that into the
   hard-wired `dispatch_authority_unavailable` (`:555`).

The refusal comes after the preflight reads and `reconcile_once`, but before
approval, intent and dispatch (`probe_r3_r6.py` C).

**Reachability: none today.** `ClosedAdapterRegistry.validate`
(`R/f3_runtime/registry.py:226-241`) pins exactly the 15 capability IDs that
the routes sets map. No test asserts that the registry and the routes sets are
equal; drift would still fail closed, just with this misleading code.

**Impact.** The reason code only. The persisted `pre_dispatch_terminal` event
and the Core counters (`lease_failures+1`) look identical to degraded Core
(probes B and C cannot be told apart).

**Severity: Low.**

**Fix.**

- A `DispatchAuthorityRefused(diagnostic_code)` exception in `R/f3/executor.py`,
  whose code is honored when it is on a closed list.
- `_CoreDispatchAuthorityGuard.acquire` (`R/f3_runtime/runtime.py`) raises it
  with `core_requirement_unmapped` when the requirements contain
  `core.unsupported_f3_operation`.
- It imports the existing constant `routes.CORE_REQUIREMENT_UNMAPPED`
  (`routes.py:304`) read-only; `routes.py` is not edited.
- Tests: executor reason code, plus registry-equals-routes-sets.
- ~25 lines. With R3-b, the check also moves ahead of the preflight reads.

### 3.9 R7 — non-string `tool_name`

The classifier only handles `isinstance(tool_name, str)` (`R/routing.py:737-741`).

**Probe** (`probe2`, real FastMCP stack, MCP 1.30.0): for `3` and `None`, the
SDK returns HTTP 200 with JSON-RPC `-32602`. No tool calls and no Core
acquisitions; no Core or HA call is possible.

Cosmetic side effects:

- `None` writes no audit record, because `if tool_name and response_capture` at
  `:904` skips outcome parsing;
- `3` is audited as `tool_name: 3`.

**Severity: None (Info). No action**, apart from an optional
`rpc_method == "tools/call"` condition at `:904`, folded into PR C if wanted.

### 3.10 R8 — list or dict `params.name` crashes the audit `finally`

**What.** `elif tool_name not in {…}` at `R/routing.py:989` is inside the
`finally` block opened at `:929`. A list passes the truthiness check at `:951`;
the set lookup then raises `TypeError: unhashable`. A dict fails the same way.
This is pre-existing (`b126970` `:883`).

**Probe** (`probe3`, real stack):

- The SDK had already sent HTTP 200 / `-32602` with `more_body: False`.
- No tool ran and no Core was acquired.
- The `TypeError` escapes the gateway. Uvicorn logs "Exception in ASGI
  application" and closes the transport.
- The audit record and the `request_completed` log are lost.
- The next request on the same server succeeds.

**Impact.**

- No side effect, no stuck state, no process crash.
- It needs an authenticated client and is rate-limited.
- The effect is audit evasion for a request that does nothing, plus
  ERROR-level log noise.

**Severity: Low.**

**Fix.**

- At `:951`, use
  `if isinstance(tool_name, str) and (tool_name or core_unmapped_refused):`.
  This also covers `:1252`.
- Non-string names then fall to the generic `mcp_request` record.
- Test: list and dict names through the real FastMCP stack give no exception
  and exactly one audit record.
- ~30 minutes. **No HAMCP-162 overlap.**

## 4. L1 and L2

### 4.1 L1 — empty-string name loses the unmapped-refusal audit shape

`""` is correctly refused before Core and the application (`R/routing.py:744-801`,
`core_unmapped_refused=True`). But the truthiness checks `if tool_name:` at
`:951` and `if tool_name and telemetry.error_code:` at `:935` are false for
`""`.

Probe (`probe1`) shows the two audit records side by side:

- `""` writes
  `{'event':'mcp_request','tool_name':None,'error_code':'unsupported_operation','parameters':None}`.
- A non-empty unmapped name writes `event 'tool_call'` with
  `parameters={'core_route':'unmapped','reason_code':'core_requirement_unmapped','argument_fields':[…]}`.
- `METRICS.record_error` is also skipped for `""`.

**Severity: Low.** The response is correct; only the evidence is incomplete.

**Fix.** The same `:951` hunk as R8, plus `(tool_name or core_unmapped_refused)`
at `:935`. Regression test: extend `test_refusal_is_audited_without_argument_values`
with `""`. Together with R8, ~45 minutes.

### 4.2 L2 — "local state only" overstatement

`docs/V2_4_1_BETA13_RELEASE_NOTES.md:40` says "eight tools on Engineering local
state only". Three of the eight have documented exceptions:

- `get_execution_task`: typed fan and power tasks reconcile through their own
  typed Core authority;
- `cancel_execution_task`: ha-mcp provider-identity reads for v3 operational
  tasks (R2);
- `approve_change_plan`: the queued notification posts to Core outside the
  request (R4).

The beta.13 publication run (`38893124888`, from `06d5555`) has since
completed. The GitHub Release `v2.4.1-beta.13` was published at
2026-10-16T11:33:35Z, and its body contains this wording (checked read-only
with `gh release view`).

**Fix.**

- Amend line 40 in the repository, with a dated HAMCP-189 correction note.
- Carry the correction in the next release's notes.
- Do **not** edit the published GitHub Release (a release mutation is
  prohibited without separate authorization).

Docs only, ~15 minutes.

**Severity: Low.**

## 5. Proposed PR grouping and order

| Order | PR | Items | Files (all protected unless noted) | HAMCP-162 overlap | Before HAMCP-155? |
| --- | --- | --- | --- | --- | --- |
| 1 | **A — F3 authority and cancel** | R3 + R3b (option R3-b), R6, R2-cancel | `R/f3_runtime/runtime.py`, `R/f3/executor.py`, tests, release | None | **Yes, required for R3b.** It is on the governed write path, and HAMCP-155's "already absent" outcome would otherwise be verified without Core authority. R2-cancel is not strictly required (deletion cancel makes no identity read), but it fixes a write-path wrong outcome and edits the same file. |
| 2 | **C — Gateway audit hygiene + L2** | R8, L1, optional R7 `:904`, L2 docs | `R/routing.py`, docs (not protected), tests | None | No; independent, can run in parallel with A |
| 3 | **B — Planning authority and telemetry** | R1 (R1-B), R2-telemetry | `R/f3_runtime/runtime.py`, `R/providers/operational_lifecycle.py`, tests | None | Not required: rollback planning reaches no write. Recommended after A, because both edit `runtime.py`. |
| 4 | **D — Delegated mapping fails closed** | R5 | `R/providers/upstream_read_gateway.py`, tests | **Yes, same file** | No (read path). Land with or immediately after HAMCP-162 Part 2 (owner decision O5). |
| — | E (optional) | R4-1 | `R/governance/approval_notifications.py`, `R/governance/runtime.py` | None | No |

- Each runtime PR (A, B, C, D, E) needs its own Engineering beta release,
  unless PRs are combined.
- HAMCP-162 Part 2 is planned as the next beta, released last. The release order
  across HAMCP-162 and A–D is an owner decision (O6).
- C is the smallest and lowest-risk. A is the only one with an ordering
  requirement.

## 6. Owner decisions (Josh)

- **O1 — R3b / R3-b.** Approve changing degraded-Core apply behavior. While Core
  is not admitted, a governed step refuses before any HA read, and "already
  reached" is no longer reported `succeeded_verified` without Core authority.
  It becomes a pre-dispatch refusal. Recommended: yes, before HAMCP-155.
- **O2 — R1.** Choose R1-B (in-tool lease; fresh rollback planning refuses
  under degraded Core; an existing plan is still returned), R1-A (gateway
  mapping; also refuses the local return; overlaps HAMCP-162), or R1-C
  (document only). Recommended: R1-B.
- **O3 — R2-cancel.** Approve that cancel of an undispatched v3 operational task
  uses the declaration-bound provider identity, with no live drift read. Cancel
  then succeeds while ha-mcp is unavailable or the identity changed. Apply keeps
  the drift check. A cancel then always records a durable terminal state, so
  recovery cannot dispatch the task afterwards (§3.2a). Recommended: yes, in
  PR A.
- **O4 — R4.** Accept the out-of-lease advisory notification with disclosure
  (R4-0), or suppress pushes while Core is not admitted (R4-1). R4-2 is not
  recommended. Recommended: R4-0.
- **O5 — R5 sequencing.** Choose one:
  - (a) HAMCP-162 Part 2 includes the three-line fail-closed change and the
    all-policy coverage test;
  - (b) PR D lands immediately after HAMCP-162;
  - (c) PR D lands first and HAMCP-162 rebases.
  Recommended: (a) or (b), to avoid a cross-session conflict in
  `upstream_read_gateway.py`.
- **O6 — Release bundling.** Decide whether A, B and C ship as separate betas or
  combined, and where they sit relative to HAMCP-162's planned beta.
- **O7 — Accept without code change:** R7 (no action) and R3c (inherent
  configuration race, disclosed).
- **O8 — Authorize the exact protected files** per PR in §5 when each task
  contract is issued.
- **O9 — R2b.** Decide whether pre-intent tasks get a terminal deadline, for
  example ending on approval expiry instead of retrying indefinitely. This is
  out of scope for the R-items. Recommended: open a separate register item;
  not part of A.

## 7. Evidence and validation

Probes are throwaway scripts in the session scratchpad, not committed. They
ran offline against in-repo fakes; the two real-stack probes used the pinned
MCP SDK 1.30.0. (The `/tmp/hamcp069a-py312` environment carries MCP 1.28.1,
which the SDK pin check refuses.)

| Probe | Items | Result |
| --- | --- | --- |
| `r4_probe.py` | R4 | Worker POSTs with no telemetry while an in-request authorizer refuses; `submitted=1`. |
| `probe_r1_r2.py` | R1, R2 | P1–P4 as in §3.1–3.2; "Ran 2 tests OK". |
| source trace + `tests/test_rc5_recovery_approval.py:210` | R2 §3.2a | Recovery drives a pre-intent task to dispatch. A failed cancel leaves no durable record. (Code read; the combined cancel-failure-then-recovery sequence was not run as a probe.) |
| `probe_r3_r6.py` | R3, R3b, R6 | Scenarios A–D as in §3.3–3.8. |
| `probe1.py`–`probe5.py` | L1, R7, R8, R5 | As in §3.7, §3.9–3.10, §4.1. |

Existing tests run (read-only) during triage, all OK:

| Module | Tests |
| --- | --- |
| `tests.test_hamcp164_core_gate_fail_closed` | 20 |
| `tests.test_f3_adapter_core` | 30 |
| `tests.test_core_semantic_continuity` | 23 |
| `tests.test_core_update_continuity` | 30 |
| `tests.test_ha_core_2026_9_integration` | 72 |

Not run: the Full and Evidence tiers. This PR is documentation only.

## 8. Non-actions

- No source, runtime, policy, registry, workflow, release-declaration or
  stable-v1 change.
- No change to the HAMCP-162 branch.
- No live Home Assistant access, no dispatch and no reviewer session.
- No Ready, merge, release or deployment.
- The beta.13 GitHub Release was not touched.
