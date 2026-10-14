# HAMCP-181 / HAMCP-167 — Truthful F3 Outcome Taxonomy for No-Call and No-Dispatch Results (Design Report)

Status: **design only, awaiting owner decisions.** This document changes no
runtime, test behavior, release metadata, workflow, registry or stable-v1
file. It does not authorize implementation. Implementation begins only after
Josh resolves the owner decisions in §8.

- Base: `origin/main` `7370ad25eb98e03347d73e152f38881895477d74` (includes
  merged PR #253 / HAMCP-177, Engineering `2.4.1-beta.8`).
- Register: HAMCP-181 (Problem A) and HAMCP-167 (Problem B). No new rows.
- Out of scope here: PR #254 / HAMCP-180 (separate session), live Home
  Assistant, dispatch, reviewer sessions.
- Inputs: HAMCP-177 author handoff checkpoints 7–10, the HAMCP-177 independent
  review (R2 items 1–9, R3), the HAMCP-175 repair handoff, the 069A design
  (V9 and F12), and source at the base SHA.

Path prefix used below: `R/` = `hass_mcp_engineering_beta/ha_mcp_engineering/`.
Line numbers are at the base SHA. Citations marked *(subagent)* came from a
read-only inventory pass and were spot-checked but not line-by-line verified.

---

## 0. Problem statement

**Problem A (HAMCP-181).** Once F3 commits the durable dispatch intent, every
later outcome is phrased as if a provider call happened. A *proven* no-call
(the provider call was provably never sent) ends as task state
`failed_post_dispatch`. Several secondary surfaces still claim dispatch after
#253 added its display-only no-call fields for the dashboard.

**Problem B (HAMCP-167).** A verified no-dispatch completion (the desired
state was already present, so nothing was sent) reports `succeeded_verified`.
That is indistinguishable from a dispatched success everywhere except entity
metadata, which has the 069A V9 outcome
`desired_state_already_present_not_dispatched`.

The goal is one truthful vocabulary covering three cases:

| Case | Meaning | Proof required |
| --- | --- | --- |
| **(a)** already satisfied, no dispatch | Preflight proved the desired state was already present. No intent was saved and nothing was sent. | The F3 `VERIFIED_NO_DISPATCH` record class (no intent, `desired_state_already_reached`). |
| **(b)** intent saved, proven no-call | The durable intent fence was committed, but local control flow proves the provider call was never sent. | A local, pre-I/O control-flow marker on the `dispatch_result_recorded` event. A remote or inferred signal is never enough. |
| **(c)** intent saved, uncertain | The call may have been sent. | This is the default whenever (b) proof is absent. |

---

## 1. Inventory

### 1.1 Durable model facts that constrain the design

| Fact | Evidence |
| --- | --- |
| The F3 record schema version is 1. The record must contain exactly 24 keys; any unknown key raises. | `R/f3/models.py:13-15`, `:874-885` |
| A durable intent must have `possibly_dispatched=True`. | `R/f3/models.py:582-583` |
| The intent commit sets `possibly_dispatched=True` and `dispatch_count=1` *before any I/O*. The code comment calls this "the durable no-blind-redispatch fence". | `R/f3/persistence.py:584-590` |
| Normalized outcome → task state map (12 keys): `dispatch_failed_confirmed` and `verification_mismatch` → `failed_post_dispatch`; `dispatch_indeterminate` and `observing` → `observing`. | `R/f3/models.py:36-49` |
| Execution class rules: `VERIFIED_NO_DISPATCH` (`:655-673`); `dispatch_failed_confirmed` requires zero observations and zero verifications (`:778-797`). | `R/f3/models.py:629-816` |
| Event keys are exactly `{sequence, event_type, occurred_at, diagnostic_codes}`. Diagnostic codes are free bounded strings (at most 16 items × 96 characters, pattern `^[a-z][a-z0-9_]{0,95}$`). | `R/f3/models.py:24-32`, `:149-158`, `:609-626` |
| A malformed F3 record raises `ExecutionRecordCorrupt`. There is no quarantine: the whole namespace fails and a `storage_read` readiness fault blocks every F3 apply. | `R/f3/persistence.py:133-150`; `R/f3_runtime/runtime.py:2713-2740` |
| Moving post-intent work back to pre-dispatch raises `BlindRedispatchProhibited`. | `R/f3/persistence.py:821-825` (also `:372-374`, `:420-421`, `:569-572`, `:775-778`) |
| The task schema version is 1. State is parsed through the `ExecutionTaskState` enum (strict). `terminal_outcome` is a free string and `verification_summary` is a free dict. Unknown top-level keys are ignored. | `R/governance/task_models.py:14`, `:20-56`, `:266-325` |
| Allowed task transitions have no `DISPATCHING → FAILED_PRE_DISPATCH`. | `R/governance/task_models.py:71-110` |
| Materialization replays `record.update(event.changes)`, and an unknown `changes` key gives "execution-task materialization mismatch". | `R/governance/task_models.py:418-422`, `:496-536` |
| A task load error quarantines the task file. A quarantined `{plan_id}.*.corrupt` makes `get_for_plan` refuse ("plan ownership is quarantined"). | `R/governance/task_storage.py:573-597`, `:639-666` |
| `plan.execution_outcome` is a free string, persisted on contract-v2+ plans. F3 projection writes the task terminal outcome into it. | `R/governance/models.py:453`, `:532`; `R/f3_runtime/runtime.py:2615`, `:2655`, `:2677` |

### 1.2 Paths ending in (a): already satisfied, no dispatch

There is one generic F3 mechanism. Adapter preflight returns
`SUCCEEDED_VERIFIED` with diagnostic `desired_state_already_reached`, and the
executor writes `terminalize_verified_no_dispatch`
(`R/f3/executor.py:470-500` → `R/f3/persistence.py:844-898`, event
`preflight_noop_verified`, class `VERIFIED_NO_DISPATCH`).

| Capability | Producer | Current client outcome |
| --- | --- | --- |
| Entity metadata | `R/f3_configuration/strategies.py:443-455`; `R/f3_configuration/adapter.py:863-912` | **Distinct (V9).** Task `SUCCEEDED_VERIFIED` with `terminal_outcome=desired_state_already_present_not_dispatched` and event `task_completed_without_dispatch`. `verification_summary` gains `outcome_code`, `dispatch_performed:false` and `result_text`. Apply sets `status:already_present` (`R/f3_runtime/runtime.py:2458-2478`, `:2568-2615`, `:2829`; `R/governance/service.py:7170-7179`). |
| Helper state (`set_exact_input_boolean_state`) | `R/f3/operational_strategies.py:833-836` (`already_desired`) → `R/f3/operational_adapter.py:579-600` | `succeeded_verified`, **indistinguishable** from a dispatched success. |
| Fan (`control_fan`) | `R/fan/adapter.py:94-105` (`no_op = fresh and self.desired(...)`) | Receipt `terminal_outcome=succeeded_verified`, `provider_attempt_count=0`, `dispatch_intent_recorded=false`. There is no distinct outcome (`R/fan/service.py:261-293`). The audit event is `fan_preflight_noop_verified` (`R/fan/audit.py:79-87`). |
| Power (`control_power`) | Subclasses fan *(subagent)* | Same as fan. |
| Configuration REST/helper (automation, script, input_*) | `R/f3_configuration/strategies.py:202-208`: the base `already_reached` returns `False` | **There is no (a) path.** If live state already equals the target, preflight refuses as `stale_target_state` / `target_already_exists` → `failed_pre_dispatch` (`R/f3_configuration/adapter.py:684-702`). |
| Dashboard update | No preflight already-reached check | **There is no (a) path.** It gives a stale refusal → `failed_pre_dispatch`. |
| Backup, reload, add-on restart, Core restart | Only input_boolean overrides `already_desired` (`operational_strategies.py:833-836`) | **There is no (a) path, by design.** These are actions, not state targets. |
| Fan/power drift at dispatch time | `R/fan/adapter.py:107-132` re-reads and refuses on drift | `failed_pre_dispatch`, even when the drifted state *equals* the target. |

### 1.3 Paths ending in (b): intent saved, proven no-call

| # | Path | Proof available | What happens today |
| --- | --- | --- | --- |
| b1 | Dashboard setter not sent (provider error with `upstream_dispatch_occurred is False`) | Local transport marker (`R/f3_dashboard/adapter.py:45-77`, `:528-542`) | `dispatch_failed_confirmed` + marker → task `failed_post_dispatch`. #253 adds the display fields `SETTER_NOT_SENT_FIELDS` on primary surfaces only (`R/f3_runtime/runtime.py:153-182`, `:2432-2455`, `:2824-2828`). |
| b2 | Configuration/entity-metadata `ConfigurationMutationNotDispatchedError`, raised before send | Local raise sites `R/governance/resources.py:659`, `:667`, `:681`, `:692`, `:759`, `:890`; `R/governance/entity_metadata.py:573`, `:588`, `:605` | `dispatch_failed_confirmed` with diagnostic `provider_confirmed_no_mutation` → `failed_post_dispatch` **with no client-visible marker** (`R/f3_configuration/adapter.py:475-501`). The audit mislabels it as `provider_response_received` (`R/f3_runtime/runtime.py:705-722`: `mutation_dispatched is False` counts as "response received"). |
| b3 | Dispatch authority retired between intent and revalidation | Executor raises before calling the adapter (`R/f3/executor.py:626-631`) | Any post-intent exception → `dispatch_indeterminate` with `provider_result_uncertain` → readback (`R/f3/executor.py:714-736`). **Proven no-call classed as uncertain.** |
| b4 | Fault injection point `after_durable_intent_before_provider_invocation` | `R/f3/executor.py:620` (test seam) | Same as b3. |
| b5 | A `before_dispatch` callback or auditor raises after the intent commit | Adapter control flow. Config: `R/f3_configuration/adapter.py:453-466` returns `FAILED_PRE_DISPATCH` with `intent=False`, which `_validate_dispatch` rejects after intent (`R/f3/executor.py:846-866`) → indeterminate. Dashboard: `before_dispatch` is outside the try (`R/f3_dashboard/adapter.py:512`). | Indeterminate → readback. |
| b6 | MCP session rotation detected before the write is sent | `R/clients/upstream_read.py:442-447` → `_require_same_session` `:503-520` raises `DashboardTransportError("protocol_error")` | Indeterminate *(subagent: classed as uncertain)*. |
| b7 | Core refusal at the local REST/WS client before the frame is written | *(subagent)* client-side gate | Indeterminate → readback. |
| b8 | Fan/power provider authority re-check after `before_dispatch()` | `R/fan/adapter.py:107-132`: `dispatched=True` is set right after `await before_dispatch()`. Any later exception → `DISPATCH_INDETERMINATE` with `fan_response_uncertain`. | Indeterminate → readback. There is no not-sent marker. |
| b9 | Backup and lifecycle providers | `R/providers/operational_backup.py:213-220` and `R/providers/operational_lifecycle.py:686-693` set `dispatched=True` once the intent callback returns. The operational adapter always returns `may_have_dispatched=True` and count 1 (`R/f3/operational_adapter.py:629-674`, `:719`). | Any failure after the callback is uncertain. The operational wrapper audits `provider_invocation_refused` while F3 records indeterminate *(subagent)*. |

Consequence: for readback-capable capabilities (helper state, fan, power,
configuration), a proven no-call can currently end as **`succeeded_verified`**
when someone else reached the desired state during the observation window.
That claims success for an operation Engineering never sent.

### 1.4 Paths ending in (c): uncertain after intent

- Any post-intent adapter exception that is not covered by a (b) proof
  (`R/f3/executor.py:714-736`). Recovery is readback only:
  `_ReadbackOnlyRecoveryAdapter` (`R/f3_runtime/runtime.py:499-513`) and
  `_recover_claimed` for an existing intent (`R/f3/executor.py:374-410`).
- Observation deadline expiry or exhausted attempts → `manual_review_required`
  (`R/f3/executor.py:1016-1227`). Evidence deadlines: backup 86400 s, reload
  900 s, add-on restart 1800 s, Core restart 1800 s, input_boolean 120 s
  (`R/f3/operational_models.py:181-187`); fan/power 180 s
  (`R/fan/service.py:150-151`).
- Incomplete sequences → `MANUAL_REVIEW_REQUIRED` with `f3_sequence_incomplete`
  (`R/f3_runtime/runtime.py:2502-2711`).

No path in (c) redispatches. This design keeps that true.

### 1.5 Client-visible surfaces that report dispatch

| # | Surface | Location | Defect for (a)/(b) |
| --- | --- | --- | --- |
| S1 | `apply_change_plan` top level | `R/f3_runtime/runtime.py:2812-2840` | `provider_dispatch_occurred=bool(task.provider_attempts)`. #253 overrides it for dashboard b1 only. `status` is `applied` only for SUCCEEDED_VERIFIED. |
| S2 | `apply_change_plan.execution_task` | `_public_task` + `_decorate_task` (`R/f3_runtime/runtime.py:3626-3690`) | Correct only for dashboard b1 and V9. |
| S3 | `apply_change_plan.plan.execution_task` | `R/f3_runtime/runtime.py:2822` → `_public(plan)` | **Uncorrected nested true** (Codex R2-3). |
| S4 | `get_change_plan.execution_task` | `R/governance/service.py:1877-1910` (`_task_is_dispatched = bool(dispatched_at or provider_attempts)`; copies only `verification_summary`) | True with child count 1 and no qualifier (R2-2). |
| S5 | `list_change_plans` | `_summary` `R/governance/service.py:2926-2986` → `_public` (R2-4 cites `:6092`) | Same as S4. |
| S6 | `list_execution_tasks` | `R/governance/service.py:1945-1975` (undecorated `_public_task`) | Flag absent. `provider_attempt_count=1`, `provider_attempts[].dispatch_count=1` and `dispatched_at` are unqualified (R2-5). Filtering by `terminal_outcome` compares raw strings (`:1966-1970`). |
| S7 | `get_execution_task` | `R/governance/service.py:1923-1931` | Decorated; correct only for b1 and V9. |
| S8 | `get_plan_observability` | `R/governance/service.py:5011`, `:5553` | Uses the same legacy projections. |
| S9 | Compact / minimal receipts | `R/models/responses.py:76-100` (`_RECEIPT_BRANCHES` lacks `f3_children` and `lifecycle_events`; `_RECEIPT_FACTS` lacks the #253 fields, `outcome_code` and `result_text`), `:194-283` | Qualifiers dropped under budget; nested true can survive (R2-6). |
| S10 | Duplicate replay | `R/governance/service.py:7127-7180`; `R/errors.py:479` ("already dispatched and will not be repeated") | The wording contradicts the no-call details (R2-7). |
| S11 | Cancellation | `R/f3_runtime/runtime.py:2896-2906`; `R/errors.py:292-296` ("cannot be cancelled after dispatch") | No details, and the wording conflates intent with send (R2-7). |
| S12 | Task-lifecycle audit | `R/governance/service.py:1720-1765` (`:1742` `provider_dispatch_occurred=bool(task.dispatched_at)`) | True on `dispatch_attempted`, `task_failed_post_dispatch` and `duplicate_apply_prevented` for b1 (R2-8). |
| S13 | F3 child audit | `R/f3_runtime/runtime.py:1277-1414` | Correct for dashboard b1 (`:1338-1342`, `:1380-1395`) only. |
| S14 | Config gateway audit | `R/f3_runtime/runtime.py:705-722` | Records `provider_response_received` for a pre-send no-call. |
| S15 | Fan/power receipt and audit | `R/fan/service.py:261-293`; `R/fan/audit.py:79-99` | `provider_attempt_count=record.dispatch_count` (the intent reservation); `dispatched_at: None` always. No `provider_dispatch_occurred` and no outcome code. |
| S16 | `plan.operational.dispatch` | Plan audit derives from `plan.operational.dispatch["dispatched"]` (`R/governance/service.py:2414-2426`) | No F3 writer updates it; strongly indicated **stale** for F3. |
| S17 | Health counts | `R/governance/service.py:~13384-13400` (`failed_post_dispatch` at `:13392`); F3 health `R/f3_runtime/runtime.py:4164-4270` (`_is_terminal_zero_dispatch_parent`) | Counts by coarse state only. |
| S18 | Reconciliation and admin page "Dispatch count" | `R/f3_runtime/runtime.py:3720-3815`; `R/governance/service.py:9060-9122` | Intent count shown as dispatch count. |
| S19 | Rollback admission | Generic: `R/f3_runtime/runtime.py:3356-3360` admits `SUCCEEDED_VERIFIED`, `MANUAL_REVIEW_REQUIRED` and `FAILED_POST_DISPATCH`. Entity metadata: `:3500-3512` refuses `forward_not_dispatched` unless `terminal_outcome == "succeeded_verified"`. | The generic path would offer a rollback plan for a (b) forward that changed nothing. |
| S20 | Nested historic counters | `provider_attempts`, `verification_summary` children, `lifecycle_events` | The durable counts are correct as intent counts but lose their qualifier when extracted or truncated (R2-9). |

**Divergent "was it dispatched" predicates (11):**

1. `_task_is_dispatched` (service `:1877-1879`)
2. `bool(task.dispatched_at)` (service `:1742`; runtime `handle_legacy_apply` `:1444-1471`)
3. `bool(task.provider_attempts)` (runtime `:2812-2840`)
4. The inline duplicate check in `_decorate_task_result` (service `:7474-7495`)
5. `_is_terminal_zero_dispatch_parent` (runtime F3 health)
6. `_setter_call_not_sent_recorded` / `dashboard_setter_not_sent` (runtime `:153-182`, `:2432-2455`)
7. `_entity_metadata_not_dispatched` (runtime `:2458-2478`)
8. Child-level `dispatch_intent` / `dispatch_count` checks (runtime `_cancel_locked` `:2896-2906`)
9. `plan.operational.dispatch` (service `:2414-2426`)
10. The fan receipt (`R/fan/service.py:261-293`)
11. Adapter-level `upstream_dispatch_occurred` / `mutation_dispatched` (dashboard adapter; runtime `:705-722`)

**Semantic split on `dispatched_at`:**

- Governed tasks set `dispatched_at = dispatch_intent.committed_at`, with a deadline of +24 h and a synthetic `dispatch_attempted` event (`_project_dispatch`, `R/f3_runtime/runtime.py:2390-2430`).
- The fan/power receipt always reports `None` ("persisted intent is not independent delivery proof").

The same field name therefore has opposite meanings on two surfaces.

---

## 2. Proposed outcome taxonomy

### 2.1 Phases

The design adds one projected, never-stored field, `dispatch_phase`, derived
by a single function (§5):

| `dispatch_phase` | Case | Definition |
| --- | --- | --- |
| `not_dispatched_pre_intent` | pre-dispatch | No durable intent exists. Covers `created`, `preflight`, `failed_pre_dispatch` and cancelled-before-intent. |
| `not_dispatched_already_present` | **(a)** | An F3 `VERIFIED_NO_DISPATCH` record exists for every child (or the V9 terminal outcome is present). |
| `intent_recorded_not_sent` | **(b)** | Intent exists, the F3 outcome is `dispatch_failed_confirmed`, `provider_response_received` is false, and a proven-no-call marker is on the `dispatch_result_recorded` event. This must hold for *every* intent-bearing child. |
| `intent_recorded_send_uncertain` | **(c)** | Intent exists and neither (b) nor `sent_response_received` is proven. **This is the default for any intent.** |
| `sent_response_received` | sent | Intent exists and `provider_response_received` is true. |

Mixed multi-child plans project the **most conservative** child phase, in this
order:

`sent_response_received` > `intent_recorded_send_uncertain` > `intent_recorded_not_sent` > `not_dispatched_already_present` > `not_dispatched_pre_intent`

#253 already applies the same rule for the dashboard: every intent-bearing
child must carry the marker.

### 2.2 Names and client fields

| Case | Task state (unchanged enum) | `terminal_outcome` / `outcome_code` | Client fields |
| --- | --- | --- | --- |
| (a) | `succeeded_verified` | `desired_state_already_present_not_dispatched` (V9 name, generalized) | `status:"already_present"`, `provider_dispatch_occurred:false`, `dispatch_performed:false`, `changed_by_engineering:false`, `intent_recorded:false`, `result_text`, `dispatch_phase` |
| (b) | `failed_post_dispatch` under T2 (see §4); a new state only under T3 | `failed_after_intent_not_dispatched` (**owner naming decision OD-5**) | `provider_dispatch_occurred:false`, `dispatch_performed:false`, `changed_by_engineering:false`, `intent_recorded:true`, `provider_call_not_sent:true` (generic), `setter_call_not_sent:true` (**dashboard only, kept verbatim from #253**), `attempt_counts_basis`, `result_text`, `dispatch_phase`, `retryable:false` |
| (c) | unchanged (`observing` / `succeeded_verified` / `failed_post_dispatch` / `manual_review_required`) | unchanged | Today's fields plus `dispatch_phase:"intent_recorded_send_uncertain"` and `attempt_counts_basis:"saved_dispatch_intent"`. `provider_dispatch_occurred` keeps its current conservative `true`. |
| sent | unchanged | unchanged | Today's fields plus `dispatch_phase` and `attempt_counts_basis:"saved_dispatch_intent"`. |

**Reuse.** These names are deliberately reused:

- From 069A V9: `outcome_code`, `dispatch_performed`, `result_text`, `status:already_present`, and the outcome string.
- From #253: `provider_dispatch_occurred`, `dispatch_performed`, `setter_call_not_sent`, `changed_by_engineering`, `intent_recorded`, `attempt_counts_basis`, and the value `saved_dispatch_intent_no_setter_call`.

**New fields:** `dispatch_phase`, `provider_call_not_sent`, and the
`attempt_counts_basis` values `saved_dispatch_intent`,
`saved_dispatch_intent_no_provider_call` and `no_dispatch_intent`.

**Rejected names.**

- Relabelling (b) as `failed_pre_dispatch` is rejected. It is false about the
  durable intent, it is prohibited by `BlindRedispatchProhibited`
  (`persistence.py:821-825`), and the task transition table forbids it
  (`task_models.py:71-110`). It would also imply re-apply eligibility.
- Reporting (b) as `succeeded_verified` after a readback is rejected.
  Engineering did not cause the state.

### 2.3 Per-capability matrix (target)

| Capability | (a) | (b) producers in scope | (c) |
| --- | --- | --- | --- |
| Power, fan | Generic no-op → V9 outcome in the receipt (projection; no governance task exists) | b3, b4, b8 (authority re-check before the service call) | unchanged |
| Helper state | Generic no-op → V9 outcome | b3, b4, b5, b7 | unchanged |
| Dashboard | No (a) path. See OD-9 (stale-equals-target). | b1 (exists), b5 (`before_dispatch` outside the try), b6 | unchanged |
| Configuration plans (REST/helper) | No (a) path today. See OD-9. | b2 (add the marker), b3, b4, b5 | unchanged |
| Entity metadata | V9 (exists) | b2, b3, b4, b5 | unchanged |
| Backup, reload, add-on restart, Core restart | **None, by design.** These are actions, not state targets. | b3, b4, b7 when local (*b9 requires a provider-level proof marker; see OD-7*) | unchanged |

### 2.4 Counter semantics

- `dispatch_count`, `provider_attempt_count` and `provider_attempts[].dispatch_count`
  keep their stored values. Each is redefined in documentation as **"durable
  dispatch-intent reservations (0 or 1)"**. It is a fence, not delivery proof
  (`persistence.py:587-590`).
- Every surface that shows a counter also shows `attempt_counts_basis` in the
  same object, so extraction cannot strip the qualifier (R2-9). The basis takes
  one of these values:
  - `no_dispatch_intent` for pre-intent and (a);
  - `saved_dispatch_intent_no_setter_call` for dashboard (b), kept verbatim
    from #253;
  - `saved_dispatch_intent_no_provider_call` for other (b);
  - `saved_dispatch_intent` for (c) and sent.
- `dispatched_at` (**OD-6**). Recommended:
  - Keep the stored value.
  - Add the projected field `dispatch_intent_recorded_at` (always the intent
    time).
  - Project the public `dispatched_at` as `null` for (b).
  - Keep the intent time for (c) and sent, with `attempt_counts_basis`.
  - Leave fan/power `dispatched_at: None` unchanged.
- Health and reconciliation keep their state counts and **add** outcome
  sub-counts: `already_present_not_dispatched` and
  `failed_after_intent_not_dispatched`. "Dispatch count" on the admin page is
  relabelled "Dispatch intents" and gains a per-row phase.

---

## 3. Safety invariants and how each is preserved

| Invariant | Mechanism today | How the change preserves it | Required negative test |
| --- | --- | --- | --- |
| **No blind redispatch, ever** | `BlindRedispatchProhibited` at five persistence sites; readback-only recovery; executor `_recover_claimed` is observe-only | No stored F3 field or state changes under F-A. (b) is still post-intent in storage and the task state is not pre-dispatch. No reader treats `provider_dispatch_occurred:false`, `provider_call_not_sent` or `dispatch_phase` as eligibility. Eligibility code keeps reading `dispatch_intent` / state only. | After (b), re-apply, restart+re-apply, cancel then apply, rollback creation and the reconciliation pass each produce **zero** additional provider calls (call counters on fake transports). |
| **Saved intent never discarded** | Intent written once; terminalize functions append only | No writer clears `dispatch_intent`, `dispatch_count`, `possibly_dispatched`, `provider_attempts` or `dispatched_at`. The projection hides nothing from storage; it only adds qualifiers. Under OD-6 the public `dispatched_at` becomes null for (b), while the intent time is still exposed as `dispatch_intent_recorded_at`. | Byte comparison: a (b) F3 record is identical before and after a projection read. Task file `dispatch_intent` fields are unchanged. |
| **At-most-once dispatch** | Intent reservation `dispatch_count=1` before I/O; exactly one adapter call | Unchanged. The (b) producers only *label* a failure that already happened before the send. They never add a call path. | One test per (b) producer: zero transport calls. For (c) controls, at most one call. |
| **Duplicate suppression** | Claim and locks; DUPLICATE_APPLY_ATTEMPT for terminal tasks (`service.py:7127-7180`) | Unchanged. Only the wording and details change (S10). `retryable:false` stays. | Concurrent duplicate apply for (a), (b) and (c): exactly one execution, and the replay carries the same phase. |
| **Same-ID non-redispatch** | Terminal plan/task → replay result | Unchanged. The replay uses the shared projection. | Same-ID re-apply after (b) on restart: zero calls, phase `intent_recorded_not_sent`. |
| **Proven vs uncertain** (only a PROVEN no-call may claim no dispatch) | #253 predicate requires capability, intent, `dispatch_failed_confirmed`, no response, marker on the right event, and every child | Generalized predicate with the same five conditions. The marker may be produced **only** by local control flow that runs strictly before the first byte is written to the write channel. Missing, conflicting or out-of-order evidence falls to `intent_recorded_send_uncertain` (fail-safe direction). The task `terminal_outcome` is a derived copy. If it disagrees with the F3 record, the F3 record wins and the conservative phase is shown. | Missing marker, marker on the wrong event, response received, mixed children, wrong outcome, and a marker emitted *after* a send-capable await: each gives (c), never false. Each producer has a mutation that moves the marker emission after the send; the test must catch it. |
| **Display false is not retry authority** (#253 rule) | Display-only fields | The projection module has no import path into the eligibility, claim or rollback code. The architecture test asserts this. | Static import test plus behavioral re-apply test. |
| **Cancellation after intent stays refused** | `_cancel_locked` (`runtime.py:2896-2906`) | The refusal is unchanged. The wording becomes "after dispatch intent was recorded", with phase details. | Cancel after (b): refused, zero calls. |

**Behavior change to disclose.** Promoting b3–b8 from indeterminate to
`dispatch_failed_confirmed` removes the readback for those paths, because
`dispatch_failed_confirmed` requires zero observations (`models.py:778-797`).
A helper/fan/power/config operation that today can end `succeeded_verified`
after a proven no-call (someone else reached the state) would instead end as
(b), a failure. This is the truthful outcome, but it changes results
(**OD-8**).

---

## 4. Persisted format and compatibility (owner decisions)

### 4.1 Does any change alter the stored F3 record format?

| Option | Description | Older reader (beta.8 and earlier) | Recommendation |
| --- | --- | --- | --- |
| **F-A** | No F3 format change. (b) stays `dispatch_failed_confirmed`, with `dispatch_count=1` and `possibly_dispatched=True`. `possibly_dispatched` is an intent-time property ("was possibly dispatched at the moment intent was committed") and stays truthful. The proof travels as a diagnostic code on the existing `dispatch_result_recorded` event (`provider_call_not_sent`, plus the existing `setter_call_not_sent` for the dashboard). | Reads normally. Diagnostic codes are free bounded strings (`models.py:149-158`). | **Recommended** |
| F-B | Add a new normalized outcome (e.g. `dispatch_not_sent_confirmed`) or an intent field, with F3 schema v2. | `ExecutionRecordCorrupt`. Namespace-wide, no quarantine, `storage_read` readiness fault, **all F3 applies blocked** on downgrade. This fails closed but leaves the system unavailable. | Not recommended |

### 4.2 Task-level representation

| Option | Stored change | Downgrade | Tradeoff |
| --- | --- | --- | --- |
| T1 | None: projection only | Trivially safe | `terminal_outcome` / `plan.execution_outcome` stay `failed_post_dispatch` / `succeeded_verified`. The S6 `terminal_outcome` filter cannot select (a)/(b). Every reader must recompute from F3 records, and fan/power already do. |
| **T2** | New `terminal_outcome` strings plus `verification_summary` keys (`outcome_code`, `dispatch_performed`, `provider_call_not_sent`, `result_text`). The state enum is unchanged. **No new event `changes` keys and no new task event types** beyond the V9 precedent `task_completed_without_dispatch`. | Expected to be safe. This is the V9/F12 precedent: beta.3 loaded beta.4 V9 records with zero quarantine (069A design, F12 result). It **must be re-proven** with an executed beta.8-reader test. | **Recommended.** Durable, filterable and V9-consistent. The disclosed residual is that the coarse state stays `failed_post_dispatch` for (b). |
| T3 | New task state, e.g. `failed_not_dispatched`, with the transition `DISPATCHING → new` | An older reader's enum rejects it → the task is **quarantined** → that plan's apply refuses ("plan ownership is quarantined") plus an `execution_storage` readiness fault | Truthful coarse state, but it breaks downgrade. It would need expand/contract: ship a tolerant reader first (one beta), then the writer. |

### 4.3 Downgrade / migration rule (OD-3)

Recommended (with F-A + T2), mirroring 069A F12:

1. Downgrade from the release that ships T2 to beta.8 or earlier is permitted
   **only after every execution task is terminal**.
2. Never clear F3 journals, task files or locks.
3. Records written by the new code are read by older code with their new
   strings uninterpreted. Older surfaces then show the legacy (less truthful)
   phrasing. This is a display regression, not a safety regression: it is
   the same fail-safe direction as (c).
4. No migration rewrites historical records. Old (b) records written by beta.8
   (dashboard only) are projected as (b) by the new reader from their existing
   `setter_call_not_sent` marker. Old (a) records are projected as (a) from
   their `VERIFIED_NO_DISPATCH` class. There is **no backfill** of
   `terminal_outcome`.
5. The proof is an executed downgrade test: beta.8 code at the published tag
   (or exact shipped commit) loads task, plan and F3 records written by the
   new writer. It must show zero quarantine, zero `ExecutionRecordCorrupt` and
   apply readiness intact. The test uses committed fixtures generated by the
   shipped writer, with provenance per AGENTS.md "Persisted Compatibility
   Fixtures". No shipped-writer fixture exists today for a `preflight_noop`
   child, a `dispatch_failed_confirmed` child, any dashboard child or a V9
   task, so the fixtures must be generated.

If Josh selects T3 or F-B instead, the rule becomes either forward-only
(downgrade is unsupported once a new record exists) or a two-release
expand/contract, and the PR split gains a reader-first PR.

### 4.4 Other compatibility notes

- **`list_execution_tasks(terminal_outcome=...)`.** Under T2, a client
  filtering on `failed_post_dispatch` no longer sees (b) tasks, and one
  filtering on `succeeded_verified` no longer sees (a) tasks for helper state.
  `state=` filters are unaffected. This is a disclosed public behavior change
  (**OD-10**).
- **`plan.execution_outcome`** receives the new strings. It is a free string
  with a V9 precedent (`runtime.py:2615`). `historical_policy.py:443`, `:654`
  and `:736` compare only `None` / `not_started` / `not_applied`.
- **Rollback (S19).** Entity metadata already refuses `forward_not_dispatched`
  unless the outcome is `succeeded_verified`. The generic rollback
  (`runtime.py:3356-3360`) would offer a rollback plan for a (b) forward. The
  recommendation is to extend the `forward_not_dispatched` refusal to (a) and
  (b) for every capability. This is a behavior change and needs a negative
  test (**OD-11**).
- **Public schemas.** Fields are additive inside existing result objects, and
  no tool registration changes. The catalog stays at 87 (59 registered + 28
  delegated). Error message wording (S10, S11) is client-visible text.

---

## 5. Surface-consistency plan: one shared projection

Proposed new module: `R/f3_runtime/dispatch_facts.py`.

- `dispatch_facts(*, capability, task, f3_records) -> DispatchFacts` (frozen),
  derived **only** from F3 records plus the task's stored outcome. It returns
  `dispatch_phase`, `provider_dispatch_occurred`, `dispatch_performed`,
  `changed_by_engineering`, `intent_recorded`, `provider_call_not_sent`,
  `setter_call_not_sent` (dashboard only), `attempt_counts_basis`,
  `dispatch_intent_recorded_at`, `outcome_code` and `result_text`.
- `dispatch_facts_for_record(record)` covers fan/power, which have no
  governance task.
- `qualify_counters(obj, facts)` stamps `attempt_counts_basis` into every
  object that carries a counter (`provider_attempts[]`, `verification_summary`
  children, `lifecycle_events`).
- The proof predicate generalizes `_setter_call_not_sent_recorded` and
  `_entity_metadata_not_dispatched`. Both become thin wrappers so the #253 and
  V9 outputs are byte-for-byte identical (regression-tested).

**Consumers replaced (all 11 predicates in §1.5):**

| Surface | Change |
| --- | --- |
| S1–S3 apply (top, `execution_task`, nested `plan.execution_task`) | One facts object applied to all three |
| S4/S5 `get_change_plan`, `list_change_plans` | `_task_plan_projection` uses the facts instead of `_task_is_dispatched` |
| S6/S7 task readers | `list_execution_tasks` is decorated like `get_execution_task` |
| S8 observability | The facts are added |
| S9 receipts | Add `dispatch_phase`, `attempt_counts_basis`, the #253 fields, `outcome_code` and `result_text` to `_RECEIPT_FACTS`; include them in `_minimal_receipt`; protect nested counters by co-locating the basis |
| S10 duplicate | Details carry the facts. The message reads "The operational apply already recorded its single dispatch intent and will not be repeated", in a phase-neutral variant. |
| S11 cancel | Details carry the facts. The wording becomes "after dispatch intent was recorded". |
| S12 lifecycle audit | New audit entries derive `provider_dispatch_occurred` from the facts and add `dispatch_phase`. Historical audit records are not rewritten. |
| S13/S14 F3 child and gateway audit | The child audit uses the generic marker. The gateway audits `provider_call_not_sent` instead of `provider_response_received` when `mutation_dispatched is False`. |
| S15 fan/power | The receipt and snapshot audit add the facts. The outcome code is V9 for (a). |
| S16 `plan.operational.dispatch` | Stop using it for F3-backed plans' audit. Derive from the facts (to be confirmed during implementation that it is stale). |
| S17/S18 health, reconciliation, admin | Outcome sub-counts and phase column |
| S19 rollback | Admission reads `dispatch_phase` for refusal only. Refusal can only get stricter. |

**Architecture guard.** A test asserts that the eligibility, claim, rollback
admission (except the stricter refusal) and cancellation modules do not
import `dispatch_facts` for permissive decisions. A grep test asserts that no
remaining direct `bool(task.dispatched_at)` or `bool(task.provider_attempts)`
appears in public projections.

---

## 6. Proposed PR split

All implementation PRs touch the protected path
`hass_mcp_engineering_beta/ha_mcp_engineering/` and therefore need explicit
authorization for that surface. Each PR is a beta version transition
materialized in its own PR via `python scripts/promote_next_release.py --apply`.
That touches `hass_mcp_engineering_beta/config.yaml`, the Engineering
`version.py`, `.release/` declarations, acceptance documents, release notes and
`CHANGELOG`. Catalog count: unchanged.

### PR1: shared dispatch-facts projection (display only, no stored change)

- **Files.**
  - New `R/f3_runtime/dispatch_facts.py`.
  - `R/f3_runtime/runtime.py` (apply, decorate, child audit, gateway audit, cancel details, health).
  - `R/governance/service.py` (plan projection, `list_execution_tasks`, lifecycle audit, duplicate details, observability, health counts).
  - `R/models/responses.py` (receipt facts).
  - `R/errors.py` (wording).
  - `R/fan/service.py`, `R/fan/audit.py` (facts and V9 code for (a)).
- **Scope.** Covers (a) for all generic no-op users as a *projection*, existing (b) (dashboard), and qualifiers for (c). No producer changes.
- **Estimate.** About 500–800 source lines and about 1,000–1,500 test lines.
- **Tests.**
  - The Codex R2 probe, replayed through every public tool, at compact budgets and across a restart.
  - The independent Core-retirement probe plus late-drift variants: zero true dispatch claims for no-call, zero additional setter calls, unchanged sent/uncertain controls.
  - #253 and V9 byte-for-byte regression.
  - Receipt budget tests at 2,000 characters and minimal size.
- **Mutations.**
  - Drop the basis from a nested counter.
  - Revert one surface to its legacy predicate.
  - Make (c) project false.
  - Remove the every-child rule.
  - Each must be caught.
- **Release.** beta.9. No downgrade constraint beyond today's, since nothing new is stored.

### PR2: durable outcome strings plus proven-no-call producers

- **Files.**
  - `R/f3/executor.py` (b3/b4: record `dispatch_failed_confirmed` with `provider_call_not_sent` when the failure provably precedes the adapter call).
  - `R/f3_configuration/adapter.py` (b2 marker; b5 `before_dispatch` failure after intent).
  - `R/f3_dashboard/adapter.py` (b5, b6).
  - `R/fan/adapter.py` (b8: set `dispatched=True` only immediately before the service call).
  - `R/f3/operational_adapter.py`, `R/providers/operational_*.py`, `R/clients/upstream_read.py` (b6/b7/b9 per OD-7).
  - `R/f3_runtime/runtime.py` (`_project_locked`: T2 strings and `verification_summary` keys; rollback refusal).
  - Shipped-writer fixtures and the executed beta.8 downgrade test.
- **Estimate.** About 400–700 source lines and about 1,500–2,500 test lines (one phase-boundary proof per producer).
- **Tests.**
  - Per producer: zero transport calls plus phase (b). A mutation moving the marker after a send-capable await must be caught.
  - Readback-capable controls stay (c) when the failure follows the send.
  - The F12-style executed downgrade test (beta.8 reader).
  - Rollback refusal for (a)/(b).
  - `terminal_outcome` filter behavior.
  - Restart and recovery of each (b) record.
- **Security section.** Required (provider, persistence and dispatch-classification boundaries).
- **Release.** beta.10, with the downgrade rule in the acceptance document.

### PR3 (optional; only if OD-1 selects T3 or OD-2 selects F-B)

- PR3a: a tolerant reader (accepts the new state or outcome, never writes it), released first.
- PR3b: the writer, in a later beta. The downgrade floor is PR3a's release.

### Not in any PR (unless OD-9 says otherwise)

The (a) paths for configuration REST/helper and the dashboard
(stale-equals-target), and the fan/power drift-equals-target case. These
change preflight admission semantics, not just labels.

---

## 7. Findings that contradict the task assumptions

1. **The HAMCP-167 capability list is wrong.**
   - Backup, reload, add-on restart and Core restart have **no**
     already-satisfied/no-dispatch path, and they should not: they are
     actions.
   - Helper state, fan and power **do** have one and currently report
     `succeeded_verified`.
   - Configuration REST/helper and the dashboard have no (a) path. When live
     state already equals the target they refuse as stale
     (`failed_pre_dispatch`).
2. **"Proven no-call reports `failed_post_dispatch`" understates the
   problem.**
   - Only b1 (dashboard) and b2 (config/entity metadata, without a marker)
     reach `dispatch_failed_confirmed`.
   - Most proven no-calls (b3–b9) are recorded as **`dispatch_indeterminate`**
     and go to readback. They can therefore end `observing`,
     `manual_review_required`, `verification_mismatch` or even
     **`succeeded_verified`** for an operation Engineering never sent.
3. **Fixing (b) for b3–b9 is not display-only.**
   - It changes the F3 normalized outcome those paths record (indeterminate →
     confirmed failure).
   - It removes their readback (`models.py:778-797`).
   - It requires producer changes in the executor and adapters, all on the
     protected runtime path.
4. **Audit labels contradict F3.**
   - The config gateway audits `provider_response_received` for a pre-send
     no-call (`runtime.py:705-722`).
   - The operational wrappers audit `provider_invocation_refused` while F3
     records indeterminate *(subagent)*.
5. **`dispatched_at` has opposite semantics.** Governed tasks report the
   intent time and fan/power report `None`. `provider_attempt_count` counts
   intents everywhere.
6. **`plan.operational.dispatch` appears stale for F3 plans.** No F3 writer
   updates it, yet the plan audit reads it (`service.py:2414-2426`). This must
   be confirmed with a test before it is relied on or removed.
7. **Generic rollback admits `FAILED_POST_DISPATCH`.** It would offer a
   rollback plan for a (b) forward that changed nothing (`runtime.py:3356-3360`).
8. **Codex R2-5 correction confirmed.** `list_execution_tasks` *omits*
   `provider_dispatch_occurred`; it does not report it as true.
9. **No shipped-writer compatibility fixtures exist** for the record shapes a
   downgrade test needs (§4.3 item 5).

---

## 8. Owner decisions (Josh)

| ID | Decision | Options | Recommendation |
| --- | --- | --- | --- |
| **OD-1** | Task-level representation of (a)/(b) | T1 projection only / **T2** stored `terminal_outcome` + `verification_summary` keys / T3 new task state | **T2** |
| **OD-2** | F3 record format | **F-A** no change (proof as diagnostic marker) / F-B new outcome + schema v2 | **F-A** |
| **OD-3** | Downgrade rule | **F12-style**: allowed only after all tasks are terminal, never clear journals/locks, proven by an executed beta.8-reader test / forward-only / expand-contract | **F12-style** (valid only with F-A + T2) |
| **OD-4** | Generalize the V9 outcome to fan, power and helper state | Yes, with the same string and fields / a distinct per-capability string | **Yes, same string** |
| **OD-5** | Name for (b) | `failed_after_intent_not_dispatched` / `intent_recorded_not_dispatched` / other | `failed_after_intent_not_dispatched` |
| **OD-6** | Public `dispatched_at` for (b) | **null + new `dispatch_intent_recorded_at`** / keep the intent time + basis qualifier only | **null + new field** |
| **OD-7** | Which proven-no-call producers to promote (b2–b9) | All local pre-send proofs / a subset (e.g. b2, b3, b4, b5 first; b6–b9 later) | **b2–b5 + b8 in PR2.** Include b6, b7 and b9 only where a pre-write proof point is demonstrable per provider; otherwise leave them (c). |
| **OD-8** | Accept the outcome change for promoted producers: no readback, and a failure even if someone else reached the state | Accept / keep readback (requires F-B) | **Accept** |
| **OD-9** | (a) for configuration REST/helper, dashboard and fan/power drift-equals-target | Out of scope / new item | **Out of scope; open a new register item if wanted** |
| **OD-10** | Accept that the `terminal_outcome` filter no longer matches (a)/(b) under legacy strings | Accept + document / also match legacy strings | **Accept + document** |
| **OD-11** | Rollback refuses `forward_not_dispatched` for (a)/(b) on every capability | Yes / keep current | **Yes** |
| **OD-12** | PR split and versions | **PR1 (beta.9) → PR2 (beta.10)** / one combined PR | **Two PRs** |
| **OD-13** | Authorization of the protected surfaces listed in §6 for each PR | Explicit per-PR authorization | Required before implementation |

---

## 9. Non-actions in this design

No runtime, test, release, workflow, registry, schema or stable-v1 change. No
live Home Assistant or deployed MCP access. No dispatch. No reviewer session.
No Ready, merge, tag, publish or deploy. PR #254 / HAMCP-180 was not touched.
