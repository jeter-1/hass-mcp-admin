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

**Revision 2 (corrections after independent design review).** The Codex
design review of head `38b06c1` (verdict: sound after corrections, no
Critical/High) raised four Medium and two Low findings. This revision applies
them:

| Finding | Correction | Where |
| --- | --- | --- |
| M1 (Codex R1) | "Sent but rejected by the provider" is now separate from "proven never sent" in the taxonomy, producer list and audit plan. Only a positive local pre-send proof may claim no dispatch. | §0, §1.3, §2.1–§2.3, §3, §5.1, §5.2 |
| M2 (Codex R2) | Producer ownership is corrected (b2–b9), and each PR lists every file it must touch, including tests and fixtures. | §1.3, §1.5, §6 |
| M3 (Codex R3) | Root cause of the successful fan/power `dispatched_at: null` is documented. The fix moves into PR1 with exact field semantics and tests. | §1.5, §2.4.2, §6 |
| M4 (Codex R4) | The downgrade guarantee is narrowed to the exact tested revision and terminal-only state, with an explicit list of what is not guaranteed. | §4.3 |
| L1 (Codex R5) | Rollback claims are corrected: contract-v2 no-call already refuses, and the concrete gap is the legacy automation branch. | §1.5 S19, §4.4, §7 |
| L2 (Codex R6) | Per-child and parent counters are distinguished. "Byte-identical output" is replaced by an explicit compatibility rule. | §2.4.1, §2.4.3, §5, §6 |
| §8 | The decision table shows the author's and the reviewer's positions side by side where they differ. | §8 |

**Revision 3 (corrections after the focused delta review).** The Codex delta
review of head `95357c7` found M2, M4 and L1 resolved and M1, M3 and L2 only
partly resolved (three Medium and two Low findings, no Critical/High). This
revision applies them:

| Finding | Correction | Where |
| --- | --- | --- |
| D1 (M1 residual) | A confirmed failure with a received response is a **dispatched attempt** and does not by itself prove no mutation. (r) is split. `sent_rejected_no_mutation` requires one of three named, provider-specific proofs (P1–P3, configuration and entity metadata only; P2 withdrawn in revision 4). Every other confirmed failure with a response is `sent_failed_mutation_unproven` and never claims no change. That includes backup, reload, add-on restart, Core restart and helper state. The dashboard structured rejection stays a readback (sent) path. | §0, §1.3, §2.1–§2.3, §5.1, §5.2 |
| D2 (M1/M2) | There is one (b) predicate. The required proof code is `setter_call_not_sent` (dashboard) or `provider_call_not_sent`; `provider_confirmed_no_mutation` and the stage reasons are optional. A new cross-reference table lists, for each producer: case, required proof code, and the emitting file:line. | §2.1, §2.2, §5.2, §5.3 |
| D3 (M3) | The executor callback order is corrected. Under OD-6 (ii), the intent time is never described or published as a dispatch time. No new non-null `dispatched_at` value exists. The two companion times are named for what they are. | §1.5, §2.4.2, §2.4.3, §3, §6 |
| D4 (L2) | The parent counter keeps the #253 `attempt_counts_basis` values. Parent aggregation gets its own separate qualifier, `attempt_count_scope`. §2.4.3 lists exactly which existing values stay unchanged and which change. | §2.4.1, §2.4.3 |
| D5 (L2b) | One parent aggregation rule (a truth table) is used everywhere. A proven positive change wins. Otherwise uncertainty dominates every no-dispatch, no-change or already-present claim. (Positive-wins superseded in revision 4 by full uncertainty dominance.) | §2.1, §2.4.1, §5 |
| Audit label | The PR1 backup/lifecycle (and helper-state) gateway audit-label limitation is accepted per the reviewer. | §5.1 |
| §8 | The architect/reviewer-aligned positions for OD-6, OD-7 and OD-14 are recorded, each **pending Josh's confirmation**. | §8 |

**Revision 4 (corrections after the focused revision-3 review).** The Codex
review of head `f5b6766` found items 2, 3, 6, 7 and 8 resolved, with one
Medium and three Low findings (no Critical/High). Revision 3's D1 statement
"P1–P3" and its D5 rule "a proven positive change wins" are **superseded**
by this revision:

| Finding | Correction | Where |
| --- | --- | --- |
| R3-1 (Medium) | **P2 is removed** (architect decision, pending Josh's confirmation). Codex reproduced, offline on both supported Core revisions (2026.10.0 and 2026.10.1), a registry change followed by an `unknown_error` result, so an entity-registry error result does not prove non-mutation. Every entity-registry error result other than the P3 `unauthorized`/`forbidden` result is now (r-unproven): "dispatched; failed; mutation not proven absent", with `changed_by_engineering:null`. Readback remains the way to learn the actual state. P1 and P3 are the **complete** set of non-mutation proofs. Any future proof requires a reproduced pre-mutation guarantee on every supported Core revision. The Codex probe becomes a required PR2 regression test. The unchanged runtime classification is disclosed (§7 item 11) and left to a new owner decision (OD-15). | §0, §1.3, §2.1–§2.3, §3, §4.3, §5–§8 |
| R3-2 (Low) | One status preservation rule (§2.4.3 item 4) states exactly which `status` values are kept. Every other section refers to it. | §2.1, §2.2, §2.4.3, §6, §8 |
| R3-3 (Low) | Parent aggregation now uses uncertainty dominance: an uncertain child outranks a verified positive. An aggregate (a) requires every child to be (a). Every restatement now refers to the single truth table in §2.1. | §2.1, §2.4.1, §2.4.3, §5 |
| R3-4 (Low) | P1 is limited to the automation/script REST routes (`input_boolean`/`input_number` updates use WebSocket). The helper-state (`:765-769`), backup (`:801-805`) and lifecycle (`:839-843`) gateway audit anchors are corrected, including the swapped PR2 runtime row. | §1.3, §2.1, §2.3, §5.1, §6, §7 |

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

The goal is one truthful vocabulary covering four cases:

| Case | Meaning | Proof required |
| --- | --- | --- |
| **(a)** already satisfied, no dispatch | Preflight proved the desired state was already present. No intent was saved and nothing was sent. | The F3 `VERIFIED_NO_DISPATCH` record class (no intent, `desired_state_already_reached`). |
| **(b)** intent saved, proven never sent | The durable intent fence was committed, but local control flow proves the provider call was never sent. | The single (b) predicate (§2.1): a **positive** proof code (`setter_call_not_sent` or `provider_call_not_sent`) on the `dispatch_result_recorded` event, emitted by the code that owns the send boundary (§5.3). A remote signal, a missing response or an exception type is never enough. |
| **(r)** sent, provider answered with a failure | The provider call was sent and the provider answered with a failure. **This is always a dispatched attempt.** It never claims no dispatch. | `dispatch_failed_confirmed` with `provider_response_received=true`. Two sub-cases: **(r-proven)** `sent_rejected_no_mutation` holds only with one of the two named provider-specific non-mutation proofs, P1 or P3 (§2.1). **(r-unproven)** `sent_failed_mutation_unproven` covers every other case, e.g. backup `backup_failed`, lifecycle `operation_failed` or an entity-registry error result: the attempt failed, but nothing proves the absence of a mutation, so it never claims no change. |
| **(c)** intent saved, uncertain | The call may have been sent. | This is the default whenever neither (b) nor a received response is proven. A lost response is (c), not (r). |

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
| Power (`control_power`) | Subclasses fan: `PowerAdapter(FanAdapter)` (`R/power/adapter.py:41-64`); the service and audit are inherited (`R/power/service.py:17`, `:26`) | Same as fan. |
| Configuration REST/helper (automation, script, input_*) | `R/f3_configuration/strategies.py:202-208`: the base `already_reached` returns `False` | **There is no (a) path.** If live state already equals the target, preflight refuses as `stale_target_state` / `target_already_exists` → `failed_pre_dispatch` (`R/f3_configuration/adapter.py:684-702`). |
| Dashboard update | No preflight already-reached check | **There is no (a) path.** It gives a stale refusal → `failed_pre_dispatch`. |
| Backup, reload, add-on restart, Core restart | Only input_boolean overrides `already_desired` (`operational_strategies.py:833-836`) | **There is no (a) path, by design.** These are actions, not state targets. |
| Fan/power drift at dispatch time | `R/fan/adapter.py:107-132` re-reads and refuses on drift | `failed_pre_dispatch`, even when the drifted state *equals* the target. |

### 1.3 Paths ending in (b) or (r) after the intent commit

Each row names the module that **owns the send boundary**, because only that
module can prove the call was not sent. An exception type, a remote signal or
a missing response is never proof. Rows marked **(r)** were sent and are
dispatched attempts, so they never claim no dispatch. Only the b2-R row has a
provider-specific non-mutation proof (P1 or P3, §2.1). The rows **r-reg**,
**r-op** and **r-dash** are sent failures that have no such proof. Columns "Today emits"
give the F3 outcome and diagnostic on `dispatch_result_recorded` and the
gateway audit label. The emitted codes after the change are in §5.2.

| # | Path | Owner of the send boundary (where proof must come from) | Case | Today emits |
| --- | --- | --- | --- | --- |
| b1 | Dashboard setter not sent (provider error with `upstream_dispatch_occurred is False`) | `R/clients/mcp.py` `McpDashboardTransport` (`:207`, `_run` `:322`). The Core gate at `:372-379` raises `connection_failed` with `tool_call_attempted=False` before `call_tool` at `:381-385`. The adapter projects it at `R/f3_dashboard/adapter.py:53` (`_provider_failure_projection`) and `:516-530`. | (b) | `dispatch_failed_confirmed` + `setter_call_not_sent` → task `failed_post_dispatch`. #253 adds the display fields `SETTER_NOT_SENT_FIELDS` on primary surfaces only (`R/f3_runtime/runtime.py:153-182`, `:2432-2455`, `:2824-2828`). Gateway audit: dashboard audited gateway `R/f3_runtime/runtime.py:600-623`. |
| b2-L | Configuration/entity-metadata local refusal before the mutation is written | `R/governance/resources.py`: `:667` (`target_already_exists`) and `:692` (`target_entity_id_reserved`) fire before any write; `:759` (`closed_payload_violation`) is purely local; `:659` and `:681` fire after a failed preflight **read** (reads may have been sent, the write was not). `R/governance/entity_metadata.py:573` is a local closed-payload refusal. All raise `ConfigurationMutationNotDispatchedError` (`R/governance/resources.py:108-112`, `mutation_dispatched=False`) without `provider_response_received`. | (b) | `dispatch_failed_confirmed` + `provider_confirmed_no_mutation` (`R/f3_configuration/adapter.py:475-501`), with **no** positive pre-send proof code. Gateway audit `provider_response_received`, which is wrong: `_AuditedConfigurationGateway` (`R/f3_runtime/runtime.py:678`, write `:702-721`) treats `mutation_dispatched is False` as "response received". |
| b2-A | Entity-metadata websocket authorization failure **before** the command frame is sent | `R/clients/websocket.py`: `auth_ok` missing at `:246-251`, and the 401/403 handshake at `:288-292`. Both raise `AuthorizationError` (`R/errors.py:632`) without `provider_response_received`; the command frame at `:252` was never written. Surfaced through `R/governance/entity_metadata.py:588`. | (b), only with a not-sent attribute set by `R/clients/websocket.py` | Same as b2-L: `provider_confirmed_no_mutation`, audit `provider_response_received` (wrong). |
| b2-R | Sent, provider answered with a rejection that has a named proof | `R/governance/resources.py:890` (automation/script REST POST sent, 4xx received; proof P1); `R/governance/entity_metadata.py:588` when the command result was `unauthorized`/`forbidden` (`R/clients/websocket.py:262-269`, `provider_response_received=True`; proof P3). | **(r-proven)** | `dispatch_failed_confirmed` + `provider_rejected_mutation` (`R/f3_configuration/adapter.py:478-497`, code at `:494-496`); P3 also carries `provider_permission_denied` (`R/f3_configuration/strategies.py:486-493`). Gateway audit `provider_response_received` (correct). Task `failed_post_dispatch`. |
| r-reg | Entity-registry update answered with any other error result | `R/governance/entity_metadata.py:598-615` (`reason: entity_registry_update_rejected`). `R/clients/websocket.py:270-277` keeps `provider_response_received=True` but discards the Core error code (only `404`/`not_found` maps to a status). | **(r-unproven)** | Same codes as b2-R, `provider_rejected_mutation` **without** `provider_permission_denied`. Not a non-mutation proof: the Codex revision-3 probe showed exact Core 2026.10.0 and 2026.10.1 assigning the registry entry and then returning `unknown_error` (§2.1). Gateway audit `provider_response_received` (correct: it was sent). |
| r-op | Operational and helper-state provider answered with a failure | Backup: `R/f3/operational_strategies.py:239-264` (`permission_failure`, `backup_rejected`, `backup_failed`; the provider maps `SERVICE_CALL_FAILED` to `backup_failed` at `R/providers/operational_backup.py:557-558`). Reload, add-on restart, Core restart: `:394-406`, `:535-547`, `:665-677` (`operation_rejected`, `operation_failed`). Helper state: `:845-866` (`provider_rejected`, raised at `R/governance/helper_state.py:160-166` for any error result). The adapter maps all of these to `DISPATCH_FAILED_CONFIRMED` (`R/f3/operational_adapter.py:707-723`). | **(r-unproven)** | `dispatch_failed_confirmed` + `provider_rejection_confirmed`, response received. **No non-mutation proof**: a failed backup or lifecycle operation, or a failed service call, may already have changed state. Today it is projected like any confirmed failure. |
| r-dash | Dashboard structured provider rejection with a received response | `R/f3_dashboard/adapter.py:53-77`, `:516-530` | sent (readback) | `OBSERVING` + `structured_provider_rejection_received` + `readback_only_recovery`. This is **not** a confirmed failure. It stays a readback path (phase `sent_response_received`) and is never (r-proven). |
| b3 | Dispatch authority retired between the intent commit and revalidation | `R/f3/executor.py`. `before_dispatch` (`:565-631`) runs as a callback **inside** `adapter.dispatch` (`:640-642`); the revalidation raise is at `:621-631`. The adapter owns the send, so proof needs both the executor's raise and the adapter declaring that it had not sent when the callback failed (b5). | (b) | The adapter converts or propagates the callback failure. The result is `dispatch_indeterminate` + `provider_result_uncertain` (`R/f3/executor.py:678-735`, record at `:713-726`, readback at `:727-735`). **A proven no-call is classed as uncertain.** |
| b4 | Fault injection point `after_durable_intent_before_provider_invocation` | `R/f3/executor.py:620` (test seam only). It is **not** a model of process loss: `SimulatedProcessLoss` is a `BaseException` (`:76`) re-raised at `:676-677`. | (b) in tests only | Same as b3. |
| b5 | A `before_dispatch` callback or auditor raises after the intent commit | Each adapter that invokes the callback. Configuration: `R/f3_configuration/adapter.py:453-466` returns `FAILED_PRE_DISPATCH` with `intent=False`, which `_validate_dispatch` (`R/f3/executor.py:643`, `:846-866`) rejects after intent. Dashboard: `before_dispatch` at `R/f3_dashboard/adapter.py:498` is **outside** the `try` that starts at `:499`. Fan/power: `R/fan/adapter.py:109-119` (`once()` sets `dispatched=True` after `before_dispatch`). Helper state: `R/governance/helper_state.py:157` (`await before_dispatch()` outside the `try` at `:158`), through `R/f3/operational_strategies.py:845-866` and `R/f3/operational_adapter.py:677-724`. Backup and lifecycle: the callback runs inside the providers' `persist_dispatch` (`R/providers/operational_backup.py:213-220`, `R/providers/operational_lifecycle.py:686-693`), which are b9 files. | (b); backup/lifecycle only with b9 | Configuration: contract violation → indeterminate. Dashboard: the exception escapes the adapter → executor general except → indeterminate. Fan/power/helper/operational: indeterminate. |
| b6 | MCP session rotation detected before the write is sent | `R/clients/upstream_read.py` `McpReadGatewayTransport` (`:84`, `_run_operation` `:365`): `before_dispatch` at `:430-441`, `_require_same_session` at `:442-447` (definition `:503-519`), then `call_tool` at `:452-456`. Users: fan (`R/providers/upstream_fan.py`), power (via `PowerProvider`, `R/providers/upstream_power.py:6`), backup (`R/providers/operational_backup.py:163`), lifecycle (`R/providers/operational_lifecycle.py:282`) and the read gateway (`R/providers/upstream_read_gateway.py:873`). **The dashboard does not use it.** | (b), only with a not-sent attribute set by `R/clients/upstream_read.py` | `DashboardTransportError("protocol_error")`. Backup/lifecycle keep `dispatched=True` for a post-dispatch transport error (`R/providers/operational_backup.py:244-245`, `:250-254`; lifecycle `:732-745`) → indeterminate. Fan/power → indeterminate. |
| b7 | Core refusal at the local REST/WS client before the request is written | `R/clients/rest.py:77-84` and `R/clients/websocket.py:207-211` (`authorize_core_dispatch()` false → `HomeAssistantUnavailableError`). `HomeAssistantUnavailableError` is raised for other reasons too, so the type alone is **not** proof; the client must set an explicit not-sent attribute. | (b), only with that attribute | Indeterminate → readback. |
| b8 | Fan/power provider authority re-check | `R/providers/upstream_fan.py`. The pre-send check is in `dispatch` at `:94-99` (authority check, `await before()`, recheck). The check at `:126-127` runs **after** the provider exchange and is not pre-send. The F3 wrapper `R/fan/adapter.py:107-132` sets `dispatched=True` after `before_dispatch` (`:109-119`) and maps any later exception to `DISPATCH_INDETERMINATE` + `fan_response_uncertain` (`:126-132`). Power inherits through `PowerAdapter(FanAdapter)` (`R/power/adapter.py:41-64`). | (b) for `:94-99` only; `:126-127` stays (c) | Indeterminate → readback. No not-sent proof code. |
| b9 | Backup and lifecycle providers | `R/providers/operational_backup.py` (`persist_dispatch` `:213-220`) and `R/providers/operational_lifecycle.py` (`:686-693`) set `dispatched=True` once the intent callback returns. `R/f3/operational_adapter.py:677-724` always returns `may_have_dispatched=True` (`:719`); strategies `R/f3/operational_strategies.py:239-264` (FullBackup) and the same pattern at `:386`, `:527`, `:657`, `:845`. | (b) only with per-provider proof | F3: indeterminate. Gateway audit (`R/f3_runtime/runtime.py:801-805` backup, `:839-843` lifecycle): `provider_response_lost` if `exc.dispatched`, otherwise `provider_invocation_refused`. The two records disagree when `exc.dispatched` is false. |

A lost response (sent, then no answer) is (c) in every row.

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
| S13 | F3 child audit | `R/f3_runtime/runtime.py:1277-1414` | Correct for dashboard b1 (`:1338-1342`, `:1380-1395`) only. It has no (r) qualifier. |
| S14 | Provider gateway audits | Configuration: `_AuditedConfigurationGateway` `R/f3_runtime/runtime.py:678`, write `:702-721`. Dashboard: `:600-623`. Helper state: `_AuditedHelperStateGateway` `:765-769`. Backup: `_EvidenceCapturingBackupGateway` `:801-805`. Lifecycle: `_EvidenceCapturingLifecycleGateway` `:839-843`. | Configuration records `provider_response_received` when `provider_response_received` is true **or** `mutation_dispatched is False`, so a b2-L/b2-A pre-send refusal is labelled as a received response. That label is correct only for b2-R (r). Helper state, backup and lifecycle label by `exc.dispatched` alone. |
| S15 | Fan/power receipt, audit and task reads | Receipt `R/fan/service.py:261-291` (`dispatched_at` is the literal `None` at `:281`); outcome audit snapshot `R/fan/audit.py:88-99` (literal `None` at `:95`); request audit `R/fan/service.py:181-186` → `request_summary` whitelist `R/fan/audit.py:8-31` → `R/routing.py:1126-1130`; `get_execution_task` routing for fan/power task ids `R/tools/governance.py:336-345`. Power inherits all of these (`R/power/service.py:17`, `:26`). | `provider_attempt_count=record.dispatch_count` (the intent reservation); `dispatched_at: None` always, even for a sent and verified call (§2.4.2). No `provider_dispatch_occurred` and no outcome code. |
| S16 | `plan.operational.dispatch` | Plan audit derives from `plan.operational.dispatch["dispatched"]` (`R/governance/service.py:2414-2426`) | No F3 writer updates it. **Stale for F3, executed proof:** the reviewer ran a successful synthetic F3 reload (one provider call, `succeeded_verified`) and `plan.operational.dispatch` stayed `dispatched:false`, attempt count 0, no response. |
| S17 | Health counts | `R/governance/service.py:~13384-13400` (`failed_post_dispatch` at `:13392`); F3 health `R/f3_runtime/runtime.py:4164-4270` (`_is_terminal_zero_dispatch_parent`) | Counts by coarse state only. |
| S18 | Reconciliation items and the admin F3 review page | Items: `R/f3_runtime/runtime.py:3720-3815`. Page: `R/approval_web.py` `f3_review` (`:522`, route `/f3/{child_id}` at `:358`) shows "Dispatch count" at `:585`. (`R/governance/service.py:9060-9122` is inside `reconcile_execution_tasks` (`:8986`), a reconciliation operation, not a display.) | Intent count shown as "Dispatch count". |
| S19 | Rollback admission | Contract v3 refused at `R/f3_runtime/runtime.py:3335`. Entity metadata: `:3500-3512` refuses `forward_not_dispatched`. Contract-v2 configuration: per-child verified and fingerprint filter (`:3373-3408`). Legacy automation: `:3423-3436`, no per-child filter. | Only the **legacy automation** branch would offer a rollback plan for a (b) or (r) forward. Contract-v2 no-call already refuses (reviewer-executed). See §4.4. |
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

- Governed tasks store `dispatched_at = dispatch_intent.committed_at` (the intent commit time, **not** a send time), with a deadline of +24 h and a synthetic `dispatch_attempted` event (`_project_dispatch`, `R/f3_runtime/runtime.py:2390-2430`).
- The fan/power receipt always reports `None` ("persisted intent is not independent delivery proof").

The same field name therefore has opposite meanings on two surfaces. No
stored field anywhere records the provider send time (§2.4.2).

---

## 2. Proposed outcome taxonomy

### 2.1 Phases

The design adds one projected, never-stored field, `dispatch_phase`, derived
by a single function (§5). Child phases:

| `dispatch_phase` | Case | Definition |
| --- | --- | --- |
| `not_dispatched_pre_intent` | pre-dispatch | No durable intent exists. Covers `created`, `preflight`, `failed_pre_dispatch` and cancelled-before-intent. |
| `not_dispatched_already_present` | **(a)** | The child's F3 record is of class `VERIFIED_NO_DISPATCH` (or the V9 terminal outcome is present). |
| `intent_recorded_not_sent` | **(b)** | The single (b) predicate below holds. |
| `sent_rejected_no_mutation` | **(r-proven)** | The (r) predicate holds **and** one named non-mutation proof (P1 or P3) holds. |
| `sent_failed_mutation_unproven` | **(r-unproven)** | The (r) predicate holds and no named non-mutation proof holds. A dispatched attempt that failed; a mutation is **not** proven absent. |
| `intent_recorded_send_uncertain` | **(c)** | Intent exists and none of (b), (r) or `sent_response_received` is proven. **This is the default for any intent.** It includes a lost response, a contradictory record, and a configuration `provider_confirmed_no_mutation` record without a proof code. |
| `sent_response_received` | sent | Intent exists, `provider_response_received` is true, and the outcome is not `dispatch_failed_confirmed`. Includes the dashboard structured rejection (`OBSERVING`, readback). |

**The single (b) predicate.** A child is (b) if and only if **all** of these
hold:

1. a durable `dispatch_intent` exists;
2. the F3 outcome is `dispatch_failed_confirmed`;
3. `provider_response_received` is false;
4. the `dispatch_result_recorded` event carries the **required proof code**:
   `setter_call_not_sent` (accepted only for the dashboard capability), or
   `provider_call_not_sent` (any capability);
5. that event carries **no conflicting code**: `provider_rejected_mutation`,
   `provider_rejection_confirmed`, `provider_completed_unexpectedly`,
   `provider_response_indeterminate`, `structured_provider_rejection_received`
   or `readback_only_recovery`.

`provider_confirmed_no_mutation` and the stage reasons
(`dispatch_authority_retired_before_send`, `before_dispatch_failed`,
`fan_provider_authority_retired`) are **optional informational** codes. Their
presence never makes a child (b), and their absence never prevents it. The
proof codes required here are exactly those emitted by the producers in the
§5.3 cross-reference table, and every producer listed there as (b) emits one.
The #253 predicate `_setter_call_not_sent_recorded`
(`R/f3_runtime/runtime.py:168-182`) is the dashboard instance of conditions
1–4. It does not check condition 5 today, which is harmless because the
dashboard writer never emits a conflicting code together with
`setter_call_not_sent` (`R/f3_dashboard/adapter.py:69-73`).

**The (r) predicate.** A child is (r) when a durable intent exists, the F3
outcome is `dispatch_failed_confirmed` and `provider_response_received` is
**true**. (r) is always a dispatched attempt (`provider_dispatch_occurred:true`).
It is (r-proven), `sent_rejected_no_mutation`, only when one of these
provider-specific non-mutation proofs holds; otherwise it is (r-unproven):

| Proof | Provider and code path | Why it proves no mutation | Recorded evidence |
| --- | --- | --- | --- |
| **P1** | Configuration REST write, **automation and script only**: the `_CONFIG_ENDPOINTS` routes (`R/governance/resources.py:95-98`), write at `:872-900`. A `HomeAssistantApiError` with an HTTP 4xx status from that POST is raised as `ConfigurationMutationNotDispatchedError` (`mutation_dispatched=False`, `R/governance/resources.py:108-112`) with `provider_response_received:true` (`:890-900`). `input_boolean`/`input_number` updates go through WebSocket (`:780-789`), not this route, and are **not** P1. | This is the reviewed resource adapter's existing classification: a 4xx rejection of the configuration write does not store the configuration. Codex's revision-3 check of exact Core source, on both supported revisions, found that the configuration view returns malformed-input 400 responses before `_write`. A 5xx status, a timeout or a lost response is **not** P1. | `provider_rejected_mutation` (`R/f3_configuration/adapter.py:494-496`) on a child whose F3 `target.target_type` is `automation` or `script` |
| **P3** | Entity metadata command answered `unauthorized`/`forbidden`: `R/governance/entity_metadata.py:582-596` when the client set `provider_response_received:true` (`R/clients/websocket.py:262-269`). | The authorization refusal is answered before the update is applied (the reviewed classification). Codex's revision-3 check of exact Core source, on both supported revisions, found that `require_admin` checks authorization before it calls the entity-update handler. The pre-command authentication failure is **not** P3; it is b2-A. | `provider_rejected_mutation` **and** `provider_permission_denied` (`R/f3_configuration/strategies.py:486-493`) on the same event, on a child whose `target.target_type` is `entity_registry_metadata` |

**P1 and P3 are the complete set of non-mutation proofs.** P2 (the former
"any entity-registry error result" proof) is withdrawn (architect decision,
pending Josh's confirmation). Codex reproduced, offline on both supported
Core revisions (2026.10.0 `6a811d3`, 2026.10.1 `abe1fdd`), a registry entry
assigned in memory, then a save-scheduling fault returned as `unknown_error`.
Engineering's gateway maps that result to `ConfigurationMutationNotDispatchedError`
with `mutation_dispatched=False`. An entity-registry error result therefore
does **not** prove non-mutation, and the error is never used to infer it.
Readback (a later read of the entity) remains the only way to learn the
actual state.

The projection decides (r-proven) only from:

- the (r) predicate;
- `provider_rejected_mutation` on the `dispatch_result_recorded` event, with
  no conflicting `provider_call_not_sent`, `setter_call_not_sent` or
  `provider_completed_unexpectedly`;
- and either P1 (`target.target_type` is `automation` or `script`), or P3
  (`target.target_type` is `entity_registry_metadata` **and**
  `provider_permission_denied` is on the same event).

An entity-metadata child with `provider_rejected_mutation` but without
`provider_permission_denied` is (r-unproven). That covers every historical
beta.8 entity-registry error record. A child whose target type cannot be read
is (r-unproven).

P1 and P3 restate classifications that Codex re-checked against exact Core
source for both supported revisions. Under ADR-021, PR1's independent review
re-checks them against the Core revisions supported at that time. **Adding
any proof** (restoring a narrowed P2, a new provider, or a new code path)
requires a reproduced pre-mutation guarantee on **every** supported Core
revision, a bounded refusal identity that is persisted and reviewed, its own
P-row, and its own tests and review. A comment or an exception class is not
such a guarantee.

Everything else that matches the (r) predicate is (r-unproven). This
includes:

- every entity-registry update error result other than P3
  (`R/governance/entity_metadata.py:598-615`), whatever the Core error code;
- backup `permission_failure`, `backup_rejected` and `backup_failed`
  (`R/f3/operational_strategies.py:246-263`; `R/providers/operational_backup.py:557-558`);
- reload, add-on restart and Core restart `operation_rejected` and
  `operation_failed` (`:394-406`, `:535-547`, `:665-677`);
- helper state `provider_rejected` (`:855-866`), where an error result from a
  service call does not prove that the service had no effect;
- any future capability that records a confirmed failure with a response.

(r-unproven) never projects `changed_by_engineering:false`, `outcome_code:
provider_rejected_mutation`, or any other wording that claims no change.

The rules are mutually exclusive. A record with `provider_response_received=true`
can never be (b), even if a proof code is present. Such a contradictory record
projects as (c) and is flagged in diagnostics as an evidence conflict.

**Parent aggregation: one rule, used everywhere.** A parent (a governed task,
an apply result, or a plan's `execution_task`) aggregates its children with
this rule and no other. Rules 1–5 and the truth table below are the single
statement of the rule. Every other section refers to them and does not
restate them. **Uncertainty
dominates**: an uncertain child outranks every other child, including a
verified positive. Missing, unreadable, incomplete (`f3_sequence_incomplete`)
or contradictory children count as (c). A parent whose child manifest cannot
be established counts as a single (c) child.

1. **Parent `dispatch_phase`.** Phases are ranked from the most uncertain to
   the least. The highest-ranked phase present wins:

   `intent_recorded_send_uncertain` > `sent_failed_mutation_unproven` >
   `sent_response_received` > `sent_rejected_no_mutation` >
   `intent_recorded_not_sent` > `not_dispatched_pre_intent` >
   `not_dispatched_already_present`

   An uncertain child therefore outranks every child that was sent, proven
   unchanged, proven not sent, or already present. `not_dispatched_pre_intent`
   ranks above (a), so a parent is (a) only when **every** child is (a).
2. **Parent `provider_dispatch_occurred`** is `false` only when every child
   is pre-intent, (a) or (b). Otherwise it is `true`: either something was
   proven sent, or (c) keeps today's conservative `true`.
3. **Parent `changed_by_engineering`**, evaluated in this order:
   - `null` when any child is (c), (r-unproven), sent but not verified, or
     missing/incomplete/contradictory. This holds even when another child was
     sent and verified.
   - Otherwise `true` when any child was sent **and verified**
     (`succeeded_verified` after a received response).
   - Otherwise `false`. Every child is then pre-intent, (a), (b) or
     (r-proven).

   Change is never inferred from a received response alone. The fact that at
   least one child changed is still visible: each child object carries its
   own `changed_by_engineering`, and the parent carries `child_dispatch_phases`
   (§2.4.1). The parent value answers "is the whole plan's effect known?";
   the child values answer "did this child change?".
4. **No-dispatch strings and flags** each require an explicit all-children
   predicate. A max-phase selection alone is not enough, and the
   implementation checks the predicate directly:
   - **(a) predicate:** every child is (a). Only then may the parent carry
     the (a) `terminal_outcome`/`outcome_code` and `dispatch_performed:false`.
     `status` follows the status preservation rule (§2.4.3 item 4).
   - **(b) predicate:** every intent-bearing child is (b), and every other
     child is (a) or pre-intent. Only then may the parent carry the (b)
     `terminal_outcome`, `provider_call_not_sent:true`,
     `setter_call_not_sent:true` (dashboard only) and
     `dispatch_performed:false`.
   - A test asserts that each predicate agrees with the rule 1 phase for
     every truth-table row.
5. **Verified claims.** No new verified or `already_present` claim is
   projected when any child is (c), (r-unproven), incomplete or missing.
   Stored task states and transitions are not recomputed, and nothing here is
   retry authority.

Truth table (two-child examples, rule 1 / rule 2 / rule 3):

| Children | `dispatch_phase` | `provider_dispatch_occurred` | `changed_by_engineering` |
| --- | --- | --- | --- |
| sent+verified, (c) | `intent_recorded_send_uncertain` | true | null |
| sent+verified, (r-unproven) | `sent_failed_mutation_unproven` | true | null |
| sent+verified, missing/incomplete child | `intent_recorded_send_uncertain` | true | null |
| sent+verified, sent+verified | `sent_response_received` | true | true |
| sent+verified, (r-proven) | `sent_response_received` | true | true |
| sent+verified, (b) | `sent_response_received` | true | true |
| sent not verified, (b) | `sent_response_received` | true | null |
| (r-unproven), (b) | `sent_failed_mutation_unproven` | true | null |
| (r-proven), (c) | `intent_recorded_send_uncertain` | true | null |
| (r-proven), (b) | `sent_rejected_no_mutation` | true | false |
| (b), (c) | `intent_recorded_send_uncertain` | true | null |
| (b), missing/incomplete child | `intent_recorded_send_uncertain` | true | null |
| (b), (b) | `intent_recorded_not_sent` | false | false |
| (a), (b) | `intent_recorded_not_sent` | false | false |
| pre-intent, (b) | `intent_recorded_not_sent` | false | false |
| (a), (a) | `not_dispatched_already_present` | false | false |
| (a), failed-pre-dispatch | `not_dispatched_pre_intent` | false | false |
| (a), created/unexecuted | `not_dispatched_pre_intent` | false | false |
| (a), missing/incomplete child | `intent_recorded_send_uncertain` | true | null |
| pre-intent, pre-intent | `not_dispatched_pre_intent` | false | false |

The (a) strings appear only in the `(a), (a)` row. The (b) strings appear only
in the three rows whose phase is `intent_recorded_not_sent`.

#253 already applies the rule 4 (b) predicate for the dashboard: every intent-bearing child
must carry the `setter_call_not_sent` proof code (`R/f3_runtime/runtime.py:2432-2455`).

### 2.2 Names and client fields

| Case | Task state (unchanged enum) | `terminal_outcome` / `outcome_code` | Client fields |
| --- | --- | --- | --- |
| (a) | `succeeded_verified` | `desired_state_already_present_not_dispatched` (V9 name, generalized) | `status` per the status preservation rule (§2.4.3 item 4): unchanged in PR1 (`applied` for generic (a), `already_present` for entity metadata); `provider_dispatch_occurred:false`, `dispatch_performed:false`, `changed_by_engineering:false`, `intent_recorded:false`, `result_text`, `dispatch_phase` |
| (b) | `failed_post_dispatch` under T2 (see §4); a new state only under T3 | `failed_after_intent_not_dispatched` (**owner naming decision OD-5**) | `provider_dispatch_occurred:false`, `dispatch_performed:false`, `changed_by_engineering:false`, `intent_recorded:true`, `provider_call_not_sent:true` (generic), `setter_call_not_sent:true` (**dashboard only, kept verbatim from #253**), `attempt_counts_basis`, `result_text`, `dispatch_phase`, `retryable:false` |
| (r-proven), P1 or P3 only | `failed_post_dispatch` (truthful: the call was sent) | unchanged (`failed_post_dispatch`). Projected `outcome_code:"provider_rejected_mutation"`, reusing the existing configuration diagnostic name. No new stored string (OD-14). | `provider_dispatch_occurred:true`, `dispatch_performed:true`, `provider_response_received:true`, `changed_by_engineering:false`, `intent_recorded:true`, `attempt_counts_basis:"saved_dispatch_intent"`, `result_text` (names the proof: "Home Assistant rejected the request; the change was not applied"), `dispatch_phase:"sent_rejected_no_mutation"`. **Never** `provider_call_not_sent`. |
| (r-unproven), every other (r), including every entity-registry error result other than P3 | `failed_post_dispatch` | unchanged. **No** projected `outcome_code`. | `provider_dispatch_occurred:true`, `dispatch_performed:true`, `provider_response_received:true`, `changed_by_engineering:null`, `intent_recorded:true`, `attempt_counts_basis:"saved_dispatch_intent"`, `result_text` ("the request was sent and failed; it is not proven that nothing changed"), `dispatch_phase:"sent_failed_mutation_unproven"`. Never `provider_call_not_sent`, never `changed_by_engineering:false`, never `outcome_code:"provider_rejected_mutation"`. |
| (c) | unchanged (`observing` / `succeeded_verified` / `failed_post_dispatch` / `manual_review_required`) | unchanged | Today's fields plus `dispatch_phase:"intent_recorded_send_uncertain"` and `attempt_counts_basis:"saved_dispatch_intent"`. `provider_dispatch_occurred` keeps its current conservative `true`. |
| sent | unchanged | unchanged | Today's fields plus `dispatch_phase` and `attempt_counts_basis:"saved_dispatch_intent"`. |

**Reuse.** These names are deliberately reused:

- From 069A V9: `outcome_code`, `dispatch_performed`, `result_text`, the outcome string, and `status:already_present` (only where the status preservation rule, §2.4.3 item 4, allows it).
- From #253: `provider_dispatch_occurred`, `dispatch_performed`, `setter_call_not_sent`, `changed_by_engineering`, `intent_recorded`, `attempt_counts_basis`, and the value `saved_dispatch_intent_no_setter_call`.

**New fields:** `dispatch_phase`, `provider_call_not_sent`,
`dispatch_intent_recorded_at`, `provider_response_recorded_at`,
`dispatched_at_basis`, `attempt_count_scope` and `child_dispatch_phases`
(parents only, §2.4.1), the projected `outcome_code` value
`provider_rejected_mutation` (an existing diagnostic name, (r-proven) only),
and the `attempt_counts_basis` values `saved_dispatch_intent`,
`saved_dispatch_intent_no_provider_call` and `no_dispatch_intent`.

**New F3 diagnostic code** (bounded free string, no format change):
`provider_call_not_sent`. Together with the dashboard's existing
`setter_call_not_sent`, it is one of the two required (b) proof codes in the
single (b) predicate (§2.1). The existing codes keep their meanings, and none
of them is a (b) proof code:

- `provider_rejected_mutation`: a response was received and the
  configuration adapter classified it as a rejection. It is (r-proven) only
  when P1 or P3 holds (§2.1). On an entity-registry error result without
  `provider_permission_denied` it is (r-unproven). It conflicts with (b).
- `provider_rejection_confirmed` (operational and helper-state strategies):
  a response was received and the attempt failed. This is (r-unproven), and
  it conflicts with (b).
- `provider_confirmed_no_mutation`: the mutation was confirmed not applied,
  but no response is recorded. Today it covers genuine local pre-send
  refusals, a pre-command authentication failure, and any generic exception
  carrying `mutation_dispatched is False`
  (`R/f3_configuration/adapter.py:516-546`). The generic branch emits it only
  when `getattr(exc, "mutation_dispatched", None) is False`; otherwise it
  emits `DISPATCH_INDETERMINATE` with `provider_response_indeterminate`. The
  code is **optional informational** in (b). On its own (without a required
  proof code) it projects as (c).

After PR2, a producer whose owning module positively proves the mutation call
was not sent records `provider_call_not_sent`. It may also keep
`provider_confirmed_no_mutation` and a stage reason; neither is required.
`fan_provider_authority_retired` exists today; `dispatch_authority_retired_before_send`
and `before_dispatch_failed` are new PR2 codes. All three use the existing
free, bounded diagnostic channel (§4.3).
`provider_call_not_sent` together with a conflicting code or a received
response is a contradiction and projects as (c).

**Rejected names.**

- Relabelling (b) as `failed_pre_dispatch` is rejected. It is false about the
  durable intent, it is prohibited by `BlindRedispatchProhibited`
  (`persistence.py:821-825`), and the task transition table forbids it
  (`task_models.py:71-110`). It would also imply re-apply eligibility.
- Reporting (b) as `succeeded_verified` after a readback is rejected.
  Engineering did not cause the state.

### 2.3 Per-capability matrix (target)

PR2 producers follow the aligned OD-7 position (§8): b6, b7 and b9 are
excluded from PR2. Backup and lifecycle b3/b4/b5 are excluded with them,
because their proof point lies inside the b9 provider files. Each excluded
producer stays (c) until it is added later with a demonstrated pre-write
proof point.

| Capability | (a) | (b) producers in PR2 (proof owner) | (r-proven) | (r-unproven) | (c) |
| --- | --- | --- | --- | --- | --- |
| Power, fan | Generic no-op → V9 outcome in the receipt (projection; no governance task exists) | b3, b4 (test seam), b5 (`R/fan/adapter.py:109-119`), b8 (`R/providers/upstream_fan.py:94-99` only) | None | None: the fan/power provider has no confirmed-failure result | unchanged; includes the post-exchange check at `:126-127` and every b6 case |
| Helper state | Generic no-op → V9 outcome | b3, b4, b5 (`R/governance/helper_state.py:157`) | None | `provider_rejected` (`R/f3/operational_strategies.py:855-866`) | unchanged; includes b7 |
| Dashboard | No (a) path. See OD-9 (stale-equals-target). | b1 (exists, `R/clients/mcp.py`), b3, b5 (`before_dispatch` at `R/f3_dashboard/adapter.py:498`, outside the try). **Not b6**: the dashboard uses `McpDashboardTransport`, not `McpReadGatewayTransport`. | None. A structured received rejection stays `OBSERVING` with readback (`R/f3_dashboard/adapter.py:516-530`): phase `sent_response_received`. | None | unchanged |
| Configuration plans (REST/helper) | No (a) path today. See OD-9. | b2-L (`R/governance/resources.py`), b3, b4, b5 (`R/f3_configuration/adapter.py:453-466`) | P1: automation/script REST only (`R/governance/resources.py:95-98`, `:872-900`) | None. `input_boolean`/`input_number` WebSocket update errors reach the generic branch (`R/f3_configuration/adapter.py:516-546`) and are (c). | unchanged; includes b7 and the helper WebSocket errors |
| Entity metadata | V9 (exists) | b2-L (`R/governance/entity_metadata.py:573`), b2-A (`R/clients/websocket.py` pre-command auth), b3, b4, b5 | P3 only (`R/governance/entity_metadata.py:582-596`, post-command result) | Every other registry error result (`R/governance/entity_metadata.py:598-615`; Codex revision-3 probe) | unchanged |
| Backup, reload, add-on restart, Core restart | **None, by design.** These are actions, not state targets. | **None in PR2.** b3, b4 and b5 need the provider's not-sent declaration, because the callback runs inside the b9 provider files (`persist_dispatch`). b6, b7 and b9 are excluded (OD-7). | None | `backup_failed`, `backup_rejected`, `permission_failure`, `operation_rejected`, `operation_failed` | unchanged; includes b5, b6, b7, b9 |

### 2.4 Counter and timestamp semantics

#### 2.4.1 Counters: per-child versus parent

There are two kinds of counter, and they must not be described the same way.

| Counter | Scope | Range | Meaning |
| --- | --- | --- | --- |
| F3 record `dispatch_count`; `provider_attempts[].dispatch_count`; fan/power receipt `provider_attempt_count` | One F3 child (one execution record) | 0 or 1 | Durable dispatch-intent reservation for that child. A fence, not delivery proof (`persistence.py:587-590`). |
| Governed task `provider_attempt_count` | Parent task | 0..N | `len(task.provider_attempts)` (`R/governance/service.py:1917`): the **sum** of child reservations. An ordered configuration plan can have several children. The executed integration suite includes a three-child plan. |
| Health, reconciliation and admin counts | Many tasks | 0..N | Counts of tasks by coarse state. |

Rules:

- Every stored and public numeric value keeps its current value. Nothing is
  recomputed, reset or backfilled.
- Every object that shows a counter also shows `attempt_counts_basis` in the
  same object, so extraction cannot strip the qualifier (R2-9).
  - On a **child** object the basis is that child's own basis:
    - `no_dispatch_intent` for pre-intent and (a);
    - `saved_dispatch_intent_no_setter_call` for dashboard (b), kept verbatim
      from #253;
    - `saved_dispatch_intent_no_provider_call` for other (b);
    - `saved_dispatch_intent` for (r-proven), (r-unproven), (c) and sent.
  - On a **parent** object (`apply_change_plan` top level, `execution_task`,
    nested `plan.execution_task`, task readers and receipts),
    `attempt_counts_basis` uses the **same child vocabulary**. Its value is
    the basis of the parent's aggregate `dispatch_phase` (§2.1 rule 1):
    - `no_dispatch_intent` when the aggregate is pre-intent or (a);
    - for (b), `saved_dispatch_intent_no_setter_call` when every
      intent-bearing child is a dashboard b1 child, otherwise
      `saved_dispatch_intent_no_provider_call`;
    - `saved_dispatch_intent` for every other aggregate.

    A one-child or multi-child dashboard parent whose children are all b1
    therefore keeps exactly the #253 value
    `attempt_counts_basis:"saved_dispatch_intent_no_setter_call"`
    (`R/f3_runtime/runtime.py:157-165`, applied at `:2825-2828` and
    `:3633`). The parent basis no longer carries a separate parent-only value.
  - Parent aggregation is shown by **separate, additive** qualifiers, never by
    changing `attempt_counts_basis`:
    - `attempt_count_scope:"parent_sum_of_child_intents"`: the parent counter
      is the sum of child 0/1 reservations;
    - `child_dispatch_phases`: a bounded count by child phase, for example
      `{"sent_response_received": 2, "intent_recorded_not_sent": 1}`, at most
      seven keys (one per phase).
  - The parent's `dispatch_phase`, `provider_dispatch_occurred` and
    `changed_by_engineering` follow the single aggregation rule in §2.1.
- **Historic event facts stay time-local.** A lifecycle event, an audit entry
  or a `provider_attempts[]` element shows only what was known when it was
  written. The final parent phase is **not** stamped onto earlier events.
  `fan/audit.py:74-87` already follows this rule ("later state is not
  backdated onto an earlier event"). The final facts appear only on the
  current-state snapshot object.
- Health and reconciliation keep their state counts and **add** outcome
  sub-counts: `already_present_not_dispatched`,
  `failed_after_intent_not_dispatched`, `sent_rejected_no_mutation` and
  `sent_failed_mutation_unproven`. Each parent is counted once, under its
  aggregate phase from §2.1.
  "Dispatch count" on the admin page (`R/approval_web.py:585`) is relabelled
  "Dispatch intents" and gains a per-row phase.

#### 2.4.2 Timestamps: `dispatched_at` (resolves Codex R3 and D3; OD-6 (ii))

**Root cause of the fan/power `null`.** The fan receipt hard-codes
`"dispatched_at": None` with the comment "persisted intent is not independent
delivery proof" (`R/fan/service.py:281`). The fan snapshot audit hard-codes
`"dispatched_at": None` (`R/fan/audit.py:95`). Power inherits both:
`PowerService` subclasses the fan service (`R/power/service.py:26`), and
`PowerExecutionRepository` subclasses the fan audit repository
(`R/power/service.py:17`). So every fan/power outcome gets `null` from a
literal, including a successful send with a received response. A lost
response, a missing intent or canary timing is not the cause. The `null`
is **truthful**: no stored field records when the provider call was sent.

**Callback order (corrected).** Revision 2 said the intent commit happens
"immediately before the executor enters `adapter.dispatch`". That was wrong.
The actual order is:

1. The executor enters the adapter first:
   `await adapter.dispatch(prepared, preflight, before_dispatch=before_dispatch)`
   (`R/f3/executor.py:640-642`). It passes the `before_dispatch` closure
   defined at `:565-631`.
2. The adapter does its own pre-send work and then invokes that closure at
   its boundary. Examples: configuration `R/f3_configuration/adapter.py:454`;
   dashboard `R/f3_dashboard/adapter.py:498`; fan `R/fan/adapter.py:118`
   inside `once()`, which the provider calls at
   `R/providers/upstream_fan.py:97`; helper state
   `R/governance/helper_state.py:157`; backup/lifecycle inside the providers'
   `persist_dispatch`.
3. Inside the closure the executor commits the durable intent
   (`R/f3/executor.py:605-617`, `now=self.now()`), runs the fault seam
   (`:620`), and revalidates dispatch authority (`:621-631`).
4. After the closure returns, work that is still in-process runs before any
   byte is sent:
   - the audited gateways write `provider_invocation_started`
     (`R/f3_runtime/runtime.py:703`, `:756`, `:794`, `:834`);
   - the fan provider rechecks its authority (`R/providers/upstream_fan.py:98-99`);
   - the client builds and writes the request.
5. The provider call is sent, and its result is recorded later
   (`R/f3/persistence.py:602-640`).

So the intent commit time is at most a **lower bound** on the send time. It
is never the send time, and this design never describes or publishes it as
one.

**Stored evidence available.** An F3 record stores two relevant times and no
others:

- `dispatch_intent.committed_at`: when the durable intent was committed
  (step 3);
- `occurred_at` of the `dispatch_result_recorded` event: when the result was
  durably recorded, after the provider call returned or failed (step 5).

The provider send instant is **not stored** anywhere.

**Field semantics (PR1, OD-6 (ii)).** One function in `dispatch_facts`
produces these fields for governed tasks, F3 children and fan/power receipts:

| Field | Named for | Value |
| --- | --- | --- |
| `dispatched_at` | Existing field. **No new non-null value is produced anywhere.** | Fan/power: `null`, unchanged, until a separately scoped and approved producer stores an exact provider-send timestamp (see below). Governed tasks: the existing stored value is published unchanged, except for (b), which publishes `null` (the call was never sent). |
| `dispatched_at_basis` | What the `dispatched_at` value is | `no_dispatch_intent` (pre-intent, (a)); `call_not_sent` ((b), value `null`); `send_time_not_recorded` (fan/power with an intent, value `null`); `dispatch_intent_commit_time` (governed task with an intent other than (b): the published value is the **intent commit time, not a send time**). |
| `dispatch_intent_recorded_at` | The time the durable dispatch intent was committed | `dispatch_intent.committed_at` whenever an intent exists; otherwise `null`. |
| `provider_response_recorded_at` | The time the provider's response was durably recorded | `occurred_at` of `dispatch_result_recorded` when `provider_response_received=true`; otherwise `null`. It is not a send time. |

Resulting public values:

| Phase | Governed `dispatched_at` | Fan/power `dispatched_at` | `dispatched_at_basis` | `dispatch_intent_recorded_at` | `provider_response_recorded_at` |
| --- | --- | --- | --- | --- | --- |
| pre-intent, (a) | `null` (unchanged) | `null` (unchanged) | `no_dispatch_intent` | `null` | `null` |
| (b) never sent | `null` (**change**: stored intent time today) | `null` | `call_not_sent` | intent commit time | `null` |
| (c) uncertain | stored value, unchanged | `null` | governed `dispatch_intent_commit_time`; fan/power `send_time_not_recorded` | intent commit time | `null` |
| (r-proven), (r-unproven) | stored value, unchanged | `null` | as (c) | intent commit time | result record time |
| sent | stored value, unchanged | `null` (unchanged) | as (c) | intent commit time | result record time |

For a multi-child parent, `dispatch_intent_recorded_at` and
`provider_response_recorded_at` are the earliest non-null child value, or
`null`. Each child shows its own values. The stored task `dispatched_at`
(written by `_project_dispatch` from the first child's intent) is unchanged
in storage, and only the (b) projection hides it.

The projection never reads the clock. It never copies the intent time or the
result-record time into `dispatched_at` for any record that does not already
store it there. Response loss, restart and historical (beta.8-written)
records never invent a time, because every value comes from a stored field.

**Exact send time (deferred, not in PR1).** A non-null fan/power
`dispatched_at` requires a producer that captures the provider send instant
and a place to store it. The F3 record has a closed key set
(`R/f3/models.py:874-885`). Storing a new time would therefore be an F-B
format change or a new task-level field. Either way it needs a separately
scoped PR2-or-later producer and persistence decision, with its own crash
and downgrade proof. Until such a decision is approved and shipped,
`dispatched_at` stays `null` on fan/power. No PR in §6 includes it.

**Tests (PR1).** Each runs through the fan service and through the power
service (to prove the inherited path), and checks the receipt, the snapshot
audit and the ordinary request audit (`fan/service.py:181-186` →
`fan/audit.py:8-31` → `routing.py:1126-1130`) for the same values:

1. Successful send with a received response: `dispatched_at` stays `null`,
   so the existing assertions in `tests/test_typed_fan.py:148`,
   `tests/test_typed_power.py:115` and `tests/test_typed_fan_review.py:306`
   keep passing **unedited**. Basis `send_time_not_recorded`.
   `dispatch_intent_recorded_at` equals the stored `committed_at`.
   `provider_response_recorded_at` equals the `dispatch_result_recorded` time.
   The first is ≤ the second.
2. Response lost (indeterminate, then readback succeeds): `dispatched_at`
   `null` (existing `tests/test_typed_fan_review.py:319`, unedited),
   `provider_response_recorded_at` `null`, and `dispatch_intent_recorded_at`
   set.
3. Preflight no-op (a): all times `null`, basis `no_dispatch_intent`.
4. Proven no-call (b): `dispatched_at` `null`, basis `call_not_sent`. In PR1
   this uses a constructed record. The live producer test follows in PR2
   with b8.
5. **Delayed send:** a deterministic fake clock advances between the intent
   commit and the fake transport's send. The test asserts that the
   transport's recorded send time is strictly later than
   `dispatch_intent_recorded_at`, and earlier than or equal to
   `provider_response_recorded_at`. It also asserts that no published field
   equals the transport's send time, and that `dispatched_at` is not equal to
   `dispatch_intent_recorded_at` on any fan/power surface. This proves the
   intent time is not presented as a send time.
6. Restart: a fresh service instance over the same store returns identical
   values (no clock dependence).
7. Historical record: a record written by the beta.8 writer (the fixture
   from §4.3) projects without error. Its companion times equal its stored
   fields, and `dispatched_at` stays `null` on fan/power.
8. Governed-task equivalents for each phase:
   - (b) publishes `null` with basis `call_not_sent`;
   - every other intent-bearing phase publishes the stored value unchanged,
     with basis `dispatch_intent_commit_time`;
   - a three-child parent whose children differ is included.

Mutations that must be caught:

- set fan/power `dispatched_at` from the intent time;
- set it from the clock;
- set it from the result-record time;
- drop the basis, or label a governed value as a send time;
- publish the intent time as governed `dispatched_at` for (b).

#### 2.4.3 Output compatibility rule (replaces "byte-identical")

Revision 1 promised that the #253 and V9 outputs would stay byte-for-byte
identical. That cannot be true, because the same PR adds fields. Revision 2
also contradicted itself: it added a mandatory parent basis value that would
have changed #253's `attempt_counts_basis` (D4). The contract is now:

1. **Every existing field keeps its name, type and value** on every surface,
   except the changes listed in item 3.
2. **New fields are additive** inside existing result objects. No field is
   removed or renamed. No tool registration changes.
3. **Explicitly approved changes only.** Each one needs its owner decision:
   - governed `dispatched_at` is published as `null` for (b) (OD-6 (ii)).
     No other `dispatched_at` value changes;
   - `provider_dispatch_occurred:false` on the secondary surfaces that show
     `true` today for a proven (b): S3 nested `plan.execution_task`, S4/S5
     plan readers, and the S12 lifecycle audit. In PR1 this applies to the
     dashboard b1 (Codex R2-2/3/8). In PR2 it also covers non-dashboard (b)
     (OD-7);
   - the S10/S11 error wording;
   - under T2 (PR2), the stored `terminal_outcome` strings for (a) and (b)
     (OD-1, OD-4, OD-5, OD-10);
   - under T2 (PR2), the one `status` change the status preservation rule
     (item 4) allows: a helper-state (a) apply whose task stores the V9
     string reports `status:"already_present"` instead of `"applied"`
     (OD-4, OD-10; pending Josh's confirmation).
4. **Existing client-visible values that stay unchanged** (regression-tested
   value by value against the base revision).

   **Status preservation rule.** This is the single statement of which
   `status` values are kept. Every other section refers to it.

   - **PR1 changes no existing `status` value on any surface.** The apply
     result keeps `"applied"` for `SUCCEEDED_VERIFIED` and `"accepted"`
     otherwise (`R/f3_runtime/runtime.py:2814-2815`).
   - `"already_present"` is emitted only where it is emitted today: the
     entity-metadata V9 apply (`R/f3_runtime/runtime.py:2829-2838`) and its
     duplicate replay (`R/governance/service.py:7170-7179`), both keyed on
     `task.terminal_outcome == desired_state_already_present_not_dispatched`.
   - Generic (a) (helper state) keeps `status:"applied"` in PR1. Its (a)
     facts appear only in additive fields: `dispatch_phase`, `outcome_code`,
     `dispatch_performed:false`, `provider_dispatch_occurred:false`,
     `changed_by_engineering:false` and `result_text`.
   - (b), (r-proven), (r-unproven), (c) and sent keep `"accepted"` or
     `"applied"` exactly as today.
   - Fan and power receipts have no `status` field and gain none
     (`R/fan/service.py:261-291`).
   - **PR2 (T2)** makes exactly one `status` change, listed in item 3: a
     helper-state (a) task stores the V9 string, so the existing override
     above reports `"already_present"`. It applies only when the (a)
     all-children predicate (§2.1 rule 4) holds. No other status changes in
     PR2.

   Other values kept:

   | Object | Field | Value kept |
   | --- | --- | --- |
   | `apply_change_plan` top level (`R/f3_runtime/runtime.py:2825`) and `execution_task` (`:2826-2828`, `:3633`) for a dashboard plan whose intent-bearing children are all b1 | every #253 `SETTER_NOT_SENT_FIELDS` entry (`:158-165`) | `provider_dispatch_occurred:false`, `dispatch_performed:false`, `setter_call_not_sent:true`, `changed_by_engineering:false`, `intent_recorded:true`, **`attempt_counts_basis:"saved_dispatch_intent_no_setter_call"`**, for one-child and multi-child parents alike |
   | Entity-metadata (a) apply and task objects | the V9 fields | `status:"already_present"`, `outcome_code`, `dispatch_performed:false`, `result_text`, and `terminal_outcome:"desired_state_already_present_not_dispatched"` |
   | Every surface | numeric counters | `provider_attempt_count`, `provider_attempts[].dispatch_count`, F3 `dispatch_count`, and health/reconciliation state counts |
   | Every surface | `dispatched_at` | Unchanged everywhere except governed (b) (item 3). Fan/power stays `null` |
   | Every surface | `status` | per the status preservation rule above |
   | Every surface | task `state`, `terminal_outcome` (PR1) | unchanged in PR1 |

5. **Objects that gain qualifiers (additive only):**
   - every object with a counter gains `attempt_counts_basis` (§2.4.1) where
     it does not already have one;
   - parent objects gain `attempt_count_scope` and `child_dispatch_phases`;
   - every task, child and receipt gains `dispatch_phase`,
     `dispatched_at_basis`, `dispatch_intent_recorded_at` and
     `provider_response_recorded_at`;
   - (r-proven) gains `outcome_code:"provider_rejected_mutation"`.
6. Under a compact or minimal budget, a counter is never emitted without its
   `attempt_counts_basis`. When the budget forces truncation, the pair is
   dropped together. `attempt_count_scope` is kept with the parent counter
   under the same rule.

Tests:

- zero-, one- and three-child receipts at full, compact (2,000 characters)
  and minimal budgets;
- a one-child dashboard b1 parent and a multi-child dashboard parent whose
  children are all b1: each #253 value, including
  `attempt_counts_basis:"saved_dispatch_intent_no_setter_call"`, is
  identical to the base revision at every budget;
- a mixed multi-child parent (sent, (r-proven), (r-unproven), (b) and (c)):
  its aggregate fields follow the §2.1 truth table at every budget;
- each existing field is compared with its value from the base revision, and
  each new field is checked for presence or absence;
- status preservation rule: a generic helper-state no-op apply (PR1
  `status:"applied"`, PR2 `"already_present"`) next to an entity-metadata V9
  no-op apply (`"already_present"` in both), each asserting `status`, the raw
  task `state`/`terminal_outcome` and the additive fields independently, at
  apply and at duplicate replay.

---

## 3. Safety invariants and how each is preserved

| Invariant | Mechanism today | How the change preserves it | Required negative test |
| --- | --- | --- | --- |
| **No blind redispatch, ever** | `BlindRedispatchProhibited` at five persistence sites; readback-only recovery; executor `_recover_claimed` is observe-only | No stored F3 field or state changes under F-A. (b) is still post-intent in storage and the task state is not pre-dispatch. No reader treats `provider_dispatch_occurred:false`, `provider_call_not_sent` or `dispatch_phase` as eligibility. Eligibility code keeps reading `dispatch_intent` / state only. | After (b), re-apply, restart+re-apply, cancel then apply, rollback creation and the reconciliation pass each produce **zero** additional provider calls (call counters on fake transports). |
| **Saved intent never discarded** | Intent written once; terminalize functions append only | No writer clears `dispatch_intent`, `dispatch_count`, `possibly_dispatched`, `provider_attempts` or `dispatched_at`. The projection hides nothing from storage; it only adds qualifiers. Under OD-6 (ii) the public governed `dispatched_at` is `null` for (b) only. The stored value is untouched, and the intent commit time is exposed under its own name, `dispatch_intent_recorded_at`, never as a send time. | Byte comparison: a (b) F3 record is identical before and after a projection read. Task file `dispatch_intent` fields are unchanged. |
| **At-most-once dispatch** | Intent reservation `dispatch_count=1` before I/O; exactly one adapter call | Unchanged. The (b) producers only *label* a failure that already happened before the send. They never add a call path. | One test per (b) producer: zero transport calls. For (c) controls, at most one call. |
| **Duplicate suppression** | Claim and locks; DUPLICATE_APPLY_ATTEMPT for terminal tasks (`service.py:7127-7180`) | Unchanged. Only the wording and details change (S10). `retryable:false` stays. | Concurrent duplicate apply for (a), (b) and (c): exactly one execution, and the replay carries the same phase. |
| **Same-ID non-redispatch** | Terminal plan/task → replay result | Unchanged. The replay uses the shared projection. | Same-ID re-apply after (b) on restart: zero calls, phase `intent_recorded_not_sent`. |
| **Proven vs uncertain** (only a PROVEN no-call may claim no dispatch) | #253 predicate requires capability, intent, `dispatch_failed_confirmed`, no response, `setter_call_not_sent` on the right event, and every child | The single (b) predicate of §2.1: the same conditions, a required proof code, no conflicting code, and every intent-bearing child. A proof code may be produced **only** by the code that owns the send boundary (§5.3) (the adapter, provider or transport named in §5.3; b6, b7 and b9 are excluded from PR2), and only on a path that runs strictly before the first byte is written to the write channel. A received failure is (r): it keeps response-received attribution and **never** gets a proof code. It claims no mutation only with a named proof, P1 or P3 (§2.1); otherwise it is (r-unproven). An entity-registry error result is (r-unproven). An exception type, a refusal code or a missing response is never proof on its own. Missing, conflicting or out-of-order evidence falls to `intent_recorded_send_uncertain` (fail-safe direction). The task `terminal_outcome` is a derived copy. If it disagrees with the F3 record, the F3 record wins and the conservative phase is shown. | Missing proof code, proof code on the wrong event, `provider_confirmed_no_mutation` or a stage reason without a proof code, a conflicting code, response received (with or without a proof code), mixed children, wrong outcome, and a proof code emitted *after* a send-capable await: each gives (c) or (r), never (b). Backup `backup_failed`, lifecycle `operation_failed`, helper-state `provider_rejected`, an entity-registry error result (including the Codex post-assignment `unknown_error` probe) and a dashboard structured rejection each give (r-unproven) or sent, never `changed_by_engineering:false`. A REST 4xx, a WS unauthorized or error result, and a lost response each must **not** get a proof code. A pre-send and a post-response authority retirement with the same refusal code must give different facts. Each producer has a mutation that moves the proof-code emission after the send; the test must catch it. |
| **Display false is not retry authority** (#253 rule) | Display-only fields | Eligibility, claim, cancellation, replay and reconciliation call sites keep the stored-intent predicate (`_task_is_dispatched` is not replaced globally). Rollback may consult the facts only to refuse. | Behavioral tests at each eligibility call site (re-apply, cancel, replay, reconcile, rollback) for (a), (b), (r-proven), (r-unproven) and (c). A static check is supplementary only, because display and eligibility code share `runtime.py` and `service.py`. |
| **Cancellation after intent stays refused** | `_cancel_locked` (`runtime.py:2896-2906`) | The refusal is unchanged. The wording becomes "after dispatch intent was recorded", with phase details. | Cancel after (b): refused, zero calls. |

**Behavior change to disclose.** Promoting a producer from indeterminate to
`dispatch_failed_confirmed` removes the readback for that path, because
`dispatch_failed_confirmed` returns before observation
(`R/f3/executor.py:662-665`) and requires zero observations
(`models.py:778-797`). A helper/fan/power/config operation that today can end
`succeeded_verified` after a proven no-call (someone else reached the state)
would instead end as (b), a failure. The reviewer's offline executor probe
reproduced today's behavior: an ordinary post-intent pre-call exception gave
zero mutation calls, one observation and `succeeded_verified`. This is the
truthful outcome, but it changes results and lock/reconciliation work
(**OD-8**). It applies only to producers with a positive pre-send proof.
Everything else keeps its readback.

**Process loss and cancellation are excluded.** `SimulatedProcessLoss` is a
`BaseException` (`R/f3/executor.py:76`) that the executor re-raises
(`:676-677`). It leaves durable intent for readback recovery, and the
existing fault-injection test depends on that. A real process loss leaves no
in-memory provenance. Restart recovery must **never** infer a (b) proof code. It
stays (c). Cancellation and other `BaseException`s keep their current
behavior. Only a caught ordinary exception, while its pre-send provenance is
still known in memory, may record (b).

**Enforced at call sites, not by import rules alone.** `_task_is_dispatched`
also controls cancellation, replay and reconciliation
(`R/governance/service.py:1993`, `:7140`, `:7184`, `:7282`, `:9091`). It is
**not** replaced globally by the display facts. Each display call site is
changed individually. Eligibility call sites keep the stored-intent
predicate. Missing or contradictory proof (legacy tasks without F3 children,
incomplete manifests, mixed children) stays conservative. Tests guard the
actual eligibility call sites and their behavior, because a module-import
rule cannot tell display code from eligibility code when both live in
`runtime.py` or `service.py`.

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

Recommended (with F-A + T2). This guarantee is deliberately narrow: it
covers exactly what the PR2 test will execute and nothing more.

**Guaranteed (after PR2's test passes):**

1. **Tested floor: exactly Engineering `2.4.1-beta.8`**, tag `v2.4.1-beta.8`
   (annotated tag object `4913bfd64948442a7a08733db4a93196175d60d1`), commit
   `7370ad25eb98e03347d73e152f38881895477d74`. The reader under test is
   that commit's source, extracted with `git archive`. No other revision is
   covered.
2. **Tested state: terminal-only quiescence.** Downgrade from the release
   that ships T2 to beta.8 is permitted only when **all** of the following
   hold, checked across every execution namespace and owner, not only the
   governance task list:
   - every governance execution task is terminal;
   - every F3 child record of every governed plan is terminal;
   - every standalone fan record (namespace `ordinary-fan-v1`) and power
     record (`ordinary-power-v1`) is terminal. These live outside the
     governance task repository (`R/fan/service.py:34-58`, `:85-113`);
   - no F3 record holds an unexpired claim (no in-flight owner), and no
     execution is held in the service's active set.
3. **Tested behavior, for those records under beta.8:**
   - startup succeeds, and repository health reports `corruption_count: 0`
     with nothing quarantined in any namespace;
   - no `ExecutionRecordCorrupt` and no `storage_read` or `execution_storage`
     readiness fault; F3 apply readiness stays intact;
   - `get_change_plan`, `list_change_plans`, `get_execution_task`,
     `list_execution_tasks` and the fan/power task reads return each record;
   - a duplicate apply of each terminal plan is refused as a duplicate, and a
     restart followed by the reconciliation pass makes **zero** provider calls
     (call counters on fake transports).
4. **Fixtures.** The records under test are written by the **exact PR2
   candidate writer**. The fixture provenance names that candidate's commit
   SHA and the generator command. Because PR2 is not yet shipped, this is the
   candidate writer, not a shipped one; after release the fixtures are kept
   with their provenance unchanged. Historical-format fixtures (beta.8-written
   records, used by the §2.4.2 and PR1 projection tests) are generated by the
   exact beta.8 writer at `7370ad2`, per AGENTS.md "Persisted Compatibility
   Fixtures". The fixture set covers, at minimum: a preflight no-op (a)
   child, a (b) child, an (r-proven) configuration child, an (r-unproven)
   operational child, an (r-unproven) entity-metadata registry-error child
   (`provider_rejected_mutation` without `provider_permission_denied`), a (c) child that ended verified by
   readback, a sent-and-verified child, a dashboard (b) child, a V9 task, a
   T2 (a) task, a T2 (b) task, a three-child mixed parent, and one fan and one
   power record together with its prepared request declaration. No committed fixture with these shapes exists today. The
   existing beta.5 lock-recovery fixture proves a different zero-dispatch
   failure shape and is not reused as evidence.
5. Never clear F3 journals, task files or locks.
6. No migration rewrites historical records. Old (b) records written by
   beta.8 (dashboard only) are projected as (b) by the new reader from their
   existing `setter_call_not_sent` proof code. Old (a) records are projected as
   (a) from their `VERIFIED_NO_DISPATCH` class. Old entity-registry error
   records (`provider_rejected_mutation` without `provider_permission_denied`)
   are projected as (r-unproven), never (r-proven). There is **no backfill** of
   `terminal_outcome`.

**Not guaranteed:**

- Downgrade to any revision other than `7370ad2`, including beta.7, beta.3
  or any earlier release. The reviewer's synthetic schema probes loaded T2
  and F-A record shapes through beta.3 (`872685b`) and beta.7 (`ebd205d`)
  source readers with zero quarantine. That is useful schema evidence, but
  it is not a writer-to-reader test and is not a guarantee. Those releases
  also have their own independent compatibility constraints (for example
  069A F12: beta.3 refuses 069A plans with `policy_snapshot_mismatch`).
- Downgrade while **any** record in any namespace is non-terminal. 069A F12
  showed that an older reader can fault F3 readiness on a non-terminal task.
  That state is not tested here and is not supported.
- **Behavioral equivalence.** Beta.8 loads the records, but it shows its own
  legacy phrasing: (a) as plain `succeeded_verified`, (b) as
  `failed_post_dispatch` with `provider_dispatch_occurred:true` (except the
  dashboard fields #253 already reads), no `dispatch_phase`, and fan/power
  `dispatched_at: null`. Its `terminal_outcome` filter treats the T2 strings
  as unknown values.
- Rollback availability under beta.8 for plans executed by the new release.
  Beta.8 applies its own rollback admission (§4.4).
- Readback, reconciliation or cancellation of records that were non-terminal
  at downgrade time.
- Re-upgrade behavior beyond what the PR2 restart tests cover.

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
- **Rollback (S19).** Revision 1 said the generic rollback would offer a
  rollback plan for any (b) forward because its initial state set
  (`runtime.py:3355-3360`) admits `FAILED_POST_DISPATCH`. That was too broad.
  The actual admission is per branch:
  - **Contract v3:** refused before the state check (`runtime.py:3335`).
  - **Contract-v2 entity metadata:** its own path (`:3350-3355`), which
    already refuses `forward_not_dispatched` unless the outcome is
    `succeeded_verified` (`:3500-3512`).
  - **Contract-v2 configuration:** each child must be `succeeded_verified`
    with a resulting fingerprint that still matches live state
    (`:3373-3408`). A child that was never sent fails that filter, so a
    wholly undispatched forward ends with no operations and
    `ROLLBACK_NOT_AVAILABLE` (`:3437-3438`). The reviewer executed this: a
    genuine configuration adapter pre-send refusal gave task
    `failed_post_dispatch` and rollback `rollback_not_available`.
  - **Legacy automation (contract v1):** `:3423-3436` builds
    `rollback_legacy_automation` from `source_plan.current_config` for any
    task in the admitted state set, with **no per-child or verified-outcome
    filter**. This is the concrete gap: a legacy automation forward that
    ended (b) or (r-proven) would be offered a rollback plan for a change
    that never happened.

  Recommendation (**OD-11**): add a refusal-only check to the legacy branch
  when the forward is **wholly unchanged** (every child (a), (b) or
  (r-proven)). An (r-unproven) child is not proven unchanged, so it keeps
  today's admission.
  Keep contract-v2 and entity-metadata behavior. In a mixed plan, keep
  rollback of the verified prefix: only the unchanged children are excluded,
  exactly as the contract-v2 filter already does. The check may only refuse;
  it never admits anything that is refused today. Tests: legacy (b) and (r-proven)
  refuse; legacy (r-unproven) keeps today's behavior; legacy sent-and-verified still creates a rollback plan; mixed
  verified/no-call contract-v2 plan rolls back only the verified children
  (existing mixed-sequence tests stay green); contract-v2 and entity-metadata
  negative controls are unchanged.
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
  `attempt_count_scope` and `child_dispatch_phases` (parents),
  `dispatch_intent_recorded_at`, `dispatched_at`, `dispatched_at_basis`,
  `provider_response_recorded_at`, `outcome_code` and `result_text`.
- `dispatch_facts_for_record(record)` covers fan/power, which have no
  governance task.
- `qualify_counters(obj, facts)` stamps `attempt_counts_basis` into every
  object that carries a counter (`provider_attempts[]`, `verification_summary`
  children, `lifecycle_events`).
- The proof predicate generalizes `_setter_call_not_sent_recorded` and
  `_entity_metadata_not_dispatched`. Both become thin wrappers. The #253 and
  V9 outputs keep every existing field at its existing value (value-identical
  under §2.4.3); the new additive fields may appear alongside them.
- (r) is decided only from `dispatch_failed_confirmed` plus
  `provider_response_received=true`, and (r-proven) additionally requires a
  named proof, P1 or P3 (§2.1). A proof code on a record whose response was
  received is a contradiction and projects as (c).
- Parents use only the §2.1 aggregation rule and truth table. No surface has
  its own aggregation.

**Consumers replaced (all 11 predicates in §1.5):**

| Surface | Change |
| --- | --- |
| S1–S3 apply (top, `execution_task`, nested `plan.execution_task`) | One facts object applied to all three |
| S4/S5 `get_change_plan`, `list_change_plans` | `_task_plan_projection` uses the facts instead of `_task_is_dispatched`. `_task_is_dispatched` stays for the eligibility call sites (`R/governance/service.py:1993`, `:7140`, `:7184`, `:7282`, `:9091`). |
| S6/S7 task readers | `list_execution_tasks` is decorated like `get_execution_task` |
| S8 observability | The facts are added |
| S9 receipts | Add `dispatch_phase`, `attempt_counts_basis`, the #253 fields, `outcome_code` and `result_text` to `_RECEIPT_FACTS`; include them in `_minimal_receipt`; protect nested counters by co-locating the basis |
| S10 duplicate | Details carry the facts. The message reads "The operational apply already recorded its single dispatch intent and will not be repeated", in a phase-neutral variant. |
| S11 cancel | Details carry the facts. The wording becomes "after dispatch intent was recorded". |
| S12 lifecycle audit | New audit entries derive `provider_dispatch_occurred` from the facts and add `dispatch_phase`. Historical audit records are not rewritten. |
| S13 F3 child audit | Adds `dispatch_phase`. Uses the (b) predicate for (b), and the (r) predicate plus P1 or P3 for (r) (§5.1). |
| S14 gateway audits | Per §5.1: the label follows the exception's proven facts, never `mutation_dispatched is False` alone. |
| S15 fan/power | The receipt, the snapshot audit and the request audit add the facts and the §2.4.2 timestamps. The outcome code is V9 for (a). |
| S16 `plan.operational.dispatch` | Stop using it for F3-backed plans' audit; derive from the facts. Staleness is confirmed by the reviewer's executed probe; PR1 adds its own regression test. |
| S17/S18 health, reconciliation, admin | Outcome sub-counts, a phase column, and "Dispatch count" relabelled "Dispatch intents" (`R/approval_web.py:585`) |
| S19 rollback | Admission reads `dispatch_phase` for refusal only, on the legacy automation branch (§4.4). Refusal can only get stricter. |

### 5.1 Case → recorded codes and audit labels

One row per case. "F3" is what the executor records on
`dispatch_result_recorded`. "Gateway audit" is the provider-gateway audit
written before the F3 record (S14). "Child audit" is the F3 child audit
(S13). "Lifecycle audit" is the task-lifecycle audit (S12).

| Case | F3 outcome + diagnostic codes | `provider_response_received` | Gateway audit label | Child audit | Lifecycle audit `provider_dispatch_occurred` |
| --- | --- | --- | --- | --- | --- |
| (a) | No intent. Event `preflight_noop_verified`, `desired_state_already_reached`, class `VERIFIED_NO_DISPATCH` | false | none (no provider write call) | `dispatch_phase: not_dispatched_already_present` | false |
| (b) dashboard (b1) | `dispatch_failed_confirmed` + required `setter_call_not_sent` (unchanged; optional `provider_confirmed_no_mutation`) | false | unchanged | unchanged #253 fields + `dispatch_phase: intent_recorded_not_sent` | false |
| (b) other (PR2) | `dispatch_failed_confirmed` + required `provider_call_not_sent`; optional `provider_confirmed_no_mutation` and a stage reason (§5.3) | false | PR1: `provider_result_uncertain` (no proof yet). PR2: `provider_call_not_sent`, passed **explicitly** by the producer with the exception, because the gateway audit is written before the F3 record and cannot read it. | `provider_call_not_sent: true`, `dispatch_performed: false`, `dispatch_phase: intent_recorded_not_sent` | false (OD-7) |
| (r-proven) | `dispatch_failed_confirmed` + `provider_rejected_mutation` (P1, or P3 with `provider_permission_denied`). Never a proof code. | **true** | `provider_response_received` (correct today) | `dispatch_phase: sent_rejected_no_mutation`, `provider_dispatch_occurred: true`, `changed_by_engineering: false` | true |
| (r-unproven) | `dispatch_failed_confirmed` + `provider_rejection_confirmed` (backup, reload, add-on/Core restart, helper state); `provider_rejected_mutation` on an entity-registry error result without `provider_permission_denied` (r-reg); or any other confirmed failure with a response. Never a proof code. | **true** | r-reg: `provider_response_received` (correct). Operational/helper state: unchanged. Today's operational and helper-state label is `provider_response_lost` when `exc.dispatched` (see the accepted limitation below). | `dispatch_phase: sent_failed_mutation_unproven`, `provider_dispatch_occurred: true`, `changed_by_engineering: null` | true |
| (c) | `dispatch_indeterminate` + the adapter's uncertainty code (`provider_result_uncertain`, `fan_response_uncertain`, `provider_response_indeterminate`, …), or `dispatch_failed_confirmed` + `provider_confirmed_no_mutation` **without** a proof code | false | `provider_result_uncertain` (configuration); `provider_response_lost` (operational/helper state when `exc.dispatched`) | `dispatch_phase: intent_recorded_send_uncertain` | true (conservative) |
| sent | Existing outcomes (`succeeded_verified`, `verification_mismatch`, `observing` including the dashboard structured rejection, …) | true | `provider_response_received` | `dispatch_phase: sent_response_received` | true |

PR1 gateway-audit rule (configuration, `R/f3_runtime/runtime.py:702-721`):
label `provider_response_received` only when the exception carries
`provider_response_received=True`; otherwise `provider_result_uncertain`. This
removes the current mislabel without claiming (b). PR2 adds the
`provider_call_not_sent` label only where a producer passes positive proof.

**Accepted PR1 limitation: operational gateway audit labels.** The backup,
lifecycle and helper-state gateways (helper state
`_AuditedHelperStateGateway` `R/f3_runtime/runtime.py:765-769`, backup
`_EvidenceCapturingBackupGateway` `:801-805`, lifecycle
`_EvidenceCapturingLifecycleGateway` `:839-843`) label by `exc.dispatched`
alone:
`provider_response_lost` when true and `provider_invocation_refused` when
false. Two cases disagree with F3:

- "Refused" is wrong when the intent callback committed and then failed
  (b5), which F3 records as indeterminate.
- "Response lost" is wrong for a confirmed failure with a response
  ((r-unproven)), because the strategy raises with `dispatched=True`.

The Codex delta review accepted leaving these labels unchanged in PR1, on
three conditions, which this design adopts:

1. The limitation is stated in the PR1 acceptance document and the release
   notes.
2. The gateway audit label is **never** used as retry, redispatch or no-call
   authority. The single (b) predicate reads only the F3 record (§2.1), and
   eligibility keeps the stored-intent predicate (§3).
3. PR1 adds a regression test showing the retained label for each of these
   cases: pre-intent refusal, post-intent callback failure, a confirmed
   failure with a response, and a lost response. Each case also shows the
   (correct) F3 phase alongside the label.

A later PR corrects the labels when the provider passes whether the intent
was committed. The operational b5 and b9 proof points are excluded from PR2
(OD-7), so that correction lands with them:

- committed with proof → `provider_call_not_sent`;
- committed without proof → `provider_result_uncertain`;
- not committed → `provider_invocation_refused` (unchanged);
- confirmed failure with a response → `provider_response_received`.

### 5.2 Producer → emitted codes after PR2

PR2 producers follow the aligned OD-7 position: b6, b7, b9 and the
backup/lifecycle b3/b4/b5 are **excluded** and stay (c). Each is added later
only with a demonstrated pre-write proof point, its own §5.3 row and its own
tests.

| Producer | Case | F3 diagnostic codes (required proof code in **bold**) | Gateway audit | Proof source |
| --- | --- | --- | --- | --- |
| b1 | (b) | **`setter_call_not_sent`**, `provider_confirmed_no_mutation` (unchanged) | unchanged | `R/clients/mcp.py:372-379` |
| b2-L | (b) | **`provider_call_not_sent`**, `provider_confirmed_no_mutation` | `provider_call_not_sent` | Raise site in `R/governance/resources.py` / `R/governance/entity_metadata.py:573` sets a `call_not_sent=True` attribute on `ConfigurationMutationNotDispatchedError` |
| b2-A | (b) | **`provider_call_not_sent`**, `provider_confirmed_no_mutation` | `provider_call_not_sent` | `R/clients/websocket.py` sets the attribute on `AuthorizationError` only at `:246-251` and `:288-292` |
| b2-R | (r-proven) | `provider_rejected_mutation` (unchanged; P3 also `provider_permission_denied`) | `provider_response_received` (unchanged) | Received response plus P1 or P3 |
| r-reg | (r-unproven) | `provider_rejected_mutation` (unchanged), without `provider_permission_denied` | `provider_response_received` (unchanged) | Received response; no non-mutation proof (P2 withdrawn) |
| r-op | (r-unproven) | `provider_rejection_confirmed` (unchanged) | unchanged (accepted limitation, §5.1) | Received response; no non-mutation proof |
| b3 | (b) | **`provider_call_not_sent`**, `dispatch_authority_retired_before_send` | `provider_call_not_sent` | `R/f3/executor.py:621-631` raise, plus the adapter's b5 not-sent declaration (configuration, dashboard, fan/power, helper state only) |
| b4 | (b), tests only | as b3, with the stage reason `before_dispatch_failed` | as b3 | `R/f3/executor.py:620` |
| b5 | (b) | **`provider_call_not_sent`**, `before_dispatch_failed` | `provider_call_not_sent` | Each in-scope adapter, at the point its callback raised |
| b8 | (b) | **`provider_call_not_sent`**, `fan_provider_authority_retired` | (no gateway audit; fan/power audit snapshot) | `R/providers/upstream_fan.py:98-99` only (the recheck after `await before()` at `:97`) |
| b6, b7, b9, backup/lifecycle b3/b4/b5 (excluded) | (c) | unchanged | unchanged | none in PR2 |
| lost response, post-exchange checks | (c) | unchanged | unchanged / `provider_result_uncertain` | none |

All new codes match the existing pattern (`^[a-z][a-z0-9_]{0,95}$`) and the
16-item bound, so beta.8 accepts them (§4.3).

### 5.3 Cross-reference: producer → case → proof code → emitting file:line

This table and the single (b) predicate (§2.1) must agree exactly:

- every producer that yields (b) emits exactly one of the two required proof
  codes;
- each required proof code has at least one producer;
- no other code is required.

"Emitting file:line" is where the code is placed on the
`dispatch_result_recorded` event's diagnostic list. "Declaration" is where
the send-boundary owner establishes the proof.

| Producer | Case | Required proof code | Emitting file:line | Declaration (send-boundary owner) | PR |
| --- | --- | --- | --- | --- | --- |
| b1 dashboard setter not sent | (b) | `setter_call_not_sent` | `R/f3_dashboard/adapter.py:71` (in `_provider_failure_projection`, `:53-77`; returned at `:516-530`) | `R/clients/mcp.py:372-379` (`tool_call_attempted=False`) | exists (#253) |
| b2-L configuration/entity-metadata local refusal | (b) | `provider_call_not_sent` | `R/f3_configuration/adapter.py:475-501` (`ConfigurationMutationNotDispatchedError` branch, codes at `:494-498`) | `R/governance/resources.py:659`, `:667`, `:681`, `:692`, `:759`; `R/governance/entity_metadata.py:573` | PR2 |
| b2-A entity-metadata pre-command auth failure | (b) | `provider_call_not_sent` | `R/f3_configuration/adapter.py:475-501` | `R/clients/websocket.py:246-251`, `:288-292`, passed through `R/governance/entity_metadata.py:588` | PR2 |
| b3 authority retired after intent | (b) | `provider_call_not_sent` | `R/f3/executor.py`, post-intent result handling after `adapter.dispatch` returns (`:640-643`, `_validate_dispatch` `:846-866`), on the adapter's explicit not-sent declaration | raise `R/f3/executor.py:621-631`; declarations as b5 | PR2 |
| b4 fault seam (tests only) | (b) | `provider_call_not_sent` | as b3 | `R/f3/executor.py:620` | PR2 (test seam) |
| b5 callback failure, configuration | (b) | `provider_call_not_sent` | as b3 | `R/f3_configuration/adapter.py:453-466` | PR2 |
| b5 callback failure, dashboard | (b) | `provider_call_not_sent` | as b3 | `R/f3_dashboard/adapter.py:498` | PR2 |
| b5 callback failure, fan/power | (b) | `provider_call_not_sent` | as b3 | `R/fan/adapter.py:110-119` (`once()`, `dispatched` still false) and `:126-128` | PR2 |
| b5 callback failure, helper state | (b) | `provider_call_not_sent` | as b3 | `R/governance/helper_state.py:157` through `R/f3/operational_strategies.py:845-866` and `R/f3/operational_adapter.py:677-724` | PR2 |
| b8 fan/power provider authority recheck | (b) | `provider_call_not_sent` | `R/fan/adapter.py:126-132` (exception branch after `dispatched=True`) | `R/providers/upstream_fan.py:98-99` | PR2 |
| b2-R configuration/entity-metadata rejection | (r-proven) | none (P1 or P3 via `provider_rejected_mutation`; P3 also `provider_permission_denied` from `R/f3_configuration/strategies.py:486-493`) | `R/f3_configuration/adapter.py:494-496` | P1: `R/governance/resources.py:890-900` (automation/script routes `:95-98`); P3: `R/governance/entity_metadata.py:582-596` | exists |
| r-reg entity-registry error result | (r-unproven) | none | `R/f3_configuration/adapter.py:494-496` | `R/governance/entity_metadata.py:598-615` (no non-mutation proof) | exists |
| r-op operational/helper failure with response | (r-unproven) | none | `R/f3/operational_adapter.py:723` (`result.diagnostic_code`), from `R/f3/operational_strategies.py:260`, `:400`, `:541`, `:671`, `:863` | `R/f3/operational_strategies.py:246-263`, `:394-406`, `:535-547`, `:665-677`, `:855-866` | exists |
| b6, b7, b9, backup/lifecycle b3/b4/b5 | (c) | none (excluded) | — | — | later, only with a demonstrated pre-write proof |

PR2 test obligations for this table (D1/D2):

- **Table-driven (b) records**, one per (b) row: zero transport calls and
  phase (b).
- **(b) controls:**
  - required proof code missing (only `provider_confirmed_no_mutation` or a
    stage reason) → (c);
  - response received → (r) or (c);
  - proof code on the wrong event → (c);
  - proof code with each conflicting code → (c);
  - `setter_call_not_sent` on a non-dashboard capability → (c).
- **(r-proven) positives:** an automation or script REST 4xx (P1); a WS
  post-command `unauthorized` (P3). These are the only two.
- **(r-proven) negatives** (each must be (r-unproven) or sent, never
  `changed_by_engineering:false`): backup `backup_failed`; lifecycle
  `operation_failed`; helper state `provider_rejected`; a dashboard
  structured rejection (`OBSERVING`, sent); an exception without a response
  ((c)); an entity-registry error result, both a historical beta.8 record and
  the Codex post-assignment `unknown_error` shape (r-unproven); an
  `input_boolean`/`input_number` WebSocket update error ((c), never P1).

**Architecture guard.** A test asserts that the eligibility, claim, rollback
admission (except the stricter refusal) and cancellation modules do not
import `dispatch_facts` for permissive decisions. A grep test asserts that no
remaining direct `bool(task.dispatched_at)` or `bool(task.provider_attempts)`
appears in public projections. `R/governance/service.py` and `R/routing.py`
must not import `..f3` (`tests/test_f3_packaging_boundaries.py:325-350`), so
`dispatch_facts.py` receives F3 records from `R/f3_runtime/` callers and is
added to the approved import set (`:353-364`) only if it imports `..f3`.

---

## 6. Proposed PR split

All implementation PRs touch the protected path
`hass_mcp_engineering_beta/ha_mcp_engineering/` and protected release
metadata, and therefore need explicit authorization naming those surfaces.
Each PR is a beta version transition materialized in its own PR via
`python scripts/promote_next_release.py --apply`. Catalog count: unchanged.

How the lists were derived: source files from the producer and surface
inventory (§1.3, §1.5); existing test files from a search at the base SHA for
assertions on values each PR changes; release files from the files the
beta.8 materialization commit `373c6b0` touched. A file not listed here that
the implementation turns out to need is a scope change and is reported before
it is edited. Files that only need to keep passing unchanged are not listed.
`hass_mcp_engineering_beta/Dockerfile` copies the whole package and is **not**
edited; the frozen provenance hashes under `tests/fixtures/f3_lock_recovery/`
and `tests/fixtures/configuration_reverification/` are **not** updated.

### PR1: shared dispatch-facts projection (display only, no stored change)

**Scope.** (a) for all generic no-op users as a *projection*; existing (b)
(dashboard b1); (r-proven) and (r-unproven) as projections of existing
records (§2.1); qualifiers for (c); the parent aggregation rule (§2.1); the status
preservation rule (§2.4.3 item 4); the OD-6(ii) timestamp fields (§2.4.2: `dispatched_at_basis` and the companion
times; fan/power `dispatched_at` stays `null`, governed (b) `dispatched_at`
becomes `null`); the configuration gateway-audit mislabel (§5.1); the
accepted operational audit-label limitation and its regression test (§5.1);
counter bases and "Dispatch intents" (§2.4.1). No producer changes and no new
stored value.

**Source (protected).**

| File | Change |
| --- | --- |
| `R/f3_runtime/dispatch_facts.py` (new) | Shared projection (§5) |
| `R/f3_runtime/runtime.py` | Apply (S1–S3), `_decorate_task`, child audit (S13), configuration gateway audit `:702-721` (S14), cancel details (S11), F3 health (S17), reconciliation items (S18) |
| `R/governance/service.py` | `_task_plan_projection` (S4/S5), `list_execution_tasks` decoration (S6), lifecycle audit `:1742` (S12), duplicate details (S10), observability (S8), plan audit from `plan.operational.dispatch` (S16), health sub-counts (S17) |
| `R/models/responses.py` | `_RECEIPT_FACTS`, `_minimal_receipt`, nested counter basis (S9) |
| `R/errors.py` | Wording at `:292-296` and `:479` (S10/S11) |
| `R/approval_web.py` | `:585` "Dispatch count" → "Dispatch intents"; phase row (S18) |
| `R/fan/service.py` | Receipt `:261-291` (facts, `dispatched_at_basis` and companion times; the `dispatched_at` literal `null` is unchanged); request audit context `:181-186` |
| `R/fan/audit.py` | Snapshot `:88-99`: add the basis and companion times beside the unchanged literal `None` at `:95`; `request_summary` whitelist `:8-31` for the new fields |

Under OD-6(ii), `tests/test_typed_fan.py:148`, `tests/test_typed_power.py:115`
and `tests/test_typed_fan_review.py:306`/`:319` keep asserting
`dispatched_at is None` and are **not** edited; they become regression
guards for §2.4.2.

Not edited, verified by test only: `R/power/service.py` and
`R/power/adapter.py` (inherit from fan), `R/routing.py:1126-1130` (forwards
the whitelisted summary unchanged), `R/tools/governance.py:336-345` (routes
fan/power task reads to the fan/power service). If a test shows any of these
needs an edit, that is reported as a scope change.

**Tests: existing files edited** (they assert values PR1 changes):

| File | Assertion that changes |
| --- | --- |
| `tests/test_f3_runtime_integration.py` | Configuration gateway audit label for a pre-send `ConfigurationMutationNotDispatchedError` |
| `tests/test_f3_packaging_boundaries.py` | `:353-364` approved import set, only if `dispatch_facts.py` imports `..f3` |

**Tests: new files.**

| File | Content |
| --- | --- |
| `tests/test_hamcp181_dispatch_facts.py` | Projection unit tests for every phase; the single (b) predicate, table-driven over both proof codes, with the §5.3 controls (proof code missing, response received, wrong event, each conflicting code, `setter_call_not_sent` on a non-dashboard capability); (r-proven) positives automation/script REST 4xx (P1) and WS post-command `unauthorized` (P3) only; (r-proven) negatives `backup_failed`, `operation_failed`, helper `provider_rejected`, entity-registry error result without `provider_permission_denied` (constructed historical shape), `input_*` WebSocket error, dashboard structured rejection, exception without a response; the contradiction rule; every row of the §2.1 parent truth table, including the revision-3 R3-3 permutations (verified + (c), verified + (r-unproven), (a) + failed pre-dispatch, (a) + created, all (a), missing children) and agreement of the (a)/(b) all-children predicates with the phase; counter bases including `attempt_count_scope` and `child_dispatch_phases` |
| `tests/test_hamcp181_surface_consistency.py` | The Codex R2 probe replayed through every public tool (S1–S20) at full, compact (2,000) and minimal budgets and across a restart; the Core-retirement probe and late-drift variants; #253 and V9 value-identical regression (§2.4.3), including the dashboard all-b1 parent basis `saved_dispatch_intent_no_setter_call` at one and three children and at full, compact and minimal budgets; 0-, 1- and 3-child receipts; S16 staleness regression |
| `tests/test_hamcp181_fan_power_dispatched_at.py` | §2.4.2 tests 1–8 through fan **and** power (including the delayed-send test: no field equals or is labelled as the send time), receipt plus snapshot audit plus request audit; governed (b) `dispatched_at` `null` |
| `tests/test_hamcp181_audit_labels.py` | §5.1 PR1 gateway rule: REST 4xx (r-proven) keeps `provider_response_received`; local refusal gets `provider_result_uncertain`; lifecycle audit values per phase; the accepted operational/helper label limitation (pre-intent refusal, post-intent callback failure, confirmed failure with response, lost response), each next to its F3 phase |
| `tests/test_hamcp181_architecture_guard.py` | §5 guard: no permissive import of `dispatch_facts`; no direct legacy predicate in public projections |

**Fixtures (new).** `tests/fixtures/f3_beta8_dispatch_phase/`: generator
script, `README.md`, `provenance.json` (writer commit `7370ad2`, generator
sha256, file sha256s, synthetic statement) and the records it writes, per
AGENTS.md "Persisted Compatibility Fixtures". Shapes: a (a) child, a dashboard
(b) child, an (r-proven) configuration child, an (r-unproven) operational
child, a (c) child verified by readback, a
sent-and-verified child, a V9 task, a three-child mixed parent, and one fan
and one power record with prepared request declarations.

**Support.** `ci/unit-test-shards.json`: weights for the five new modules, if
the shard balancer requires them.

**Release (protected).** `.release/next-version` (created, then consumed by
`--apply`), `docs/V2_4_1_BETA9_RELEASE_NOTES.md` (new),
`docs/V2_4_1_BETA9_ACCEPTANCE.md` (new), `README.md`,
`hass_mcp_engineering_beta/CHANGELOG.md`,
`hass_mcp_engineering_beta/config.yaml`, `R/version.py`,
`scripts/validate_addon_metadata.py`.

**Estimate.** About 550–850 source lines and about 1,200–1,700 test lines.

**Mutations that must be caught.** Drop the basis from a nested counter;
revert one surface to its legacy predicate; make (c) project false; project
(r) as (b); project (r-unproven) as (r-proven) (claim `changed_by_engineering:
false` without P1 or P3); project an entity-metadata
`provider_rejected_mutation` record without `provider_permission_denied` as
(r-proven); let a verified child outrank an uncertain or (r-unproven) child
for parent `changed_by_engineering`; let a max-phase selection stand in for
the (a) predicate; change a generic (a) `status` in PR1; accept `provider_confirmed_no_mutation` alone as (b)
proof; let a parent claim "no dispatch" or "verified" over an uncertain
child; set fan `dispatched_at` from the intent time; use the clock for
`dispatched_at`; restore the `mutation_dispatched is False` audit branch.

**Release.** beta.9. No downgrade constraint beyond today's, since nothing new
is stored.

### PR2: durable outcome strings plus proven-no-call producers

**Scope.** T2 stored strings and `verification_summary` keys; the
`provider_call_not_sent` producers b2-L, b2-A, b3, b5, b8 (b4 as a test seam),
exactly as in §5.3; the PR2 gateway-audit labels for those producers (§5.1);
legacy-branch rollback refusal (§4.4, OD-11); the executed beta.8 downgrade
test (§4.3). b3/b4/b5 apply to configuration, dashboard, fan/power and helper
state only. **Excluded from PR2 (OD-7, aligned, pending Josh's
confirmation):** b6, b7, b9 and the backup/lifecycle b3/b4/b5, whose
not-sent proof would live inside the b9 provider `persist_dispatch`. They stay
(c). Each is added later only with a demonstrated pre-write proof point, its
own §5.3 row, files and tests.

**Source (protected), required.**

| File | Change |
| --- | --- |
| `R/f3/executor.py` | b3/b4: record `dispatch_failed_confirmed` + `provider_call_not_sent` (+ stage reason) when the adapter declares the callback failed before send |
| `R/f3_configuration/adapter.py` | b2-L/b2-A proof code from the exception attribute; b5 at `:453-466` |
| `R/f3_dashboard/adapter.py` | b5: `before_dispatch` at `:498` handled with a not-sent declaration |
| `R/fan/adapter.py` | b5 at `:109-119`; b8 not-sent declaration from the provider |
| `R/providers/upstream_fan.py` | b8: not-sent attribute for the `:94-99` refusal only; `:126-127` unchanged |
| `R/governance/resources.py` | b2-L: `call_not_sent=True` at `:659`, `:667`, `:681`, `:692`, `:759`; not at `:890` |
| `R/governance/entity_metadata.py` | b2-L at `:573`; b2-A passes the client attribute through `:588`; not at `:605` |
| `R/clients/websocket.py` | b2-A: not-sent attribute at `:246-251` and `:288-292` only |
| `R/errors.py` | The `call_not_sent` attribute on `AuthorizationError` (`:632`). (`ConfigurationMutationNotDispatchedError` is defined in `R/governance/resources.py:108-112` and gains it there.) |
| `R/f3/operational_adapter.py` | b5 for helper state only (`:677-724`); backup/lifecycle unchanged |
| `R/f3/operational_strategies.py` | Helper-state strategy `:845-866` passes the not-sent declaration through; backup/lifecycle strategies unchanged |
| `R/governance/helper_state.py` | b5: not-sent declaration when `before_dispatch()` at `:157` raises, before the `:158` try |
| `R/f3_runtime/runtime.py` | `_project_locked`: T2 strings and keys; configuration gateway label (`:702-721`) and helper-state gateway label (`_AuditedHelperStateGateway` `:765-769`) for in-scope producers only; backup (`_EvidenceCapturingBackupGateway` `:801-805`) and lifecycle (`_EvidenceCapturingLifecycleGateway` `:839-843`) labels unchanged and not authorized for edit; legacy rollback refusal (`:3423-3436`) |
| `R/f3_runtime/dispatch_facts.py` | Read the T2 strings |
| `R/governance/service.py` | `terminal_outcome` filter (`:1966-1970`) accepts the T2 strings |
| `R/f3/contracts.py` | **Owner-confirmed addition (Josh, 2026-10-25).** The shared not-sent constants `CALLBACK_FAILED_NOT_SENT_DECLARATION` and `PROVIDER_CALL_NOT_SENT`, used by the executor and the in-scope adapters. Constants only; no behavior. |

**Excluded from PR2 (OD-7 aligned).** No files for b6
(`R/clients/upstream_read.py:442-447`), b7 (`R/clients/rest.py:77-84`,
`R/clients/websocket.py:207-211`) or b9 (`R/providers/operational_backup.py`,
`R/providers/operational_lifecycle.py`) are in PR2 scope. A later PR that
adds one names its pre-write proof point, files and tests for separate
authorization.

**Tests: existing files edited.**

| File | Reason |
| --- | --- |
| `tests/test_dev14_resource_adapters.py` | Asserts `ConfigurationMutationNotDispatchedError` shape at the raise sites |
| `tests/test_dev14_configuration_plans.py` | Asserts the configuration no-mutation outcome |
| `tests/f3_configuration_fixtures.py` | Constructs the exception without the new attribute |
| `tests/test_f3_runtime_integration.py` | Gateway labels, T2 strings |
| `tests/test_hamcp135_governed_rollback.py` | Legacy-branch refusal for an unchanged forward |
| `tests/test_f3_packaging_boundaries.py` | Only if a new cross-package import is introduced |

`tests/test_f3_operational_adapter.py` is edited only if its helper-state
cases assert the b5 outcome PR2 changes; backup/lifecycle cases are not
edited.

**Tests: new files.**

| File | Content |
| --- | --- |
| `tests/test_hamcp181_proven_no_call_producers.py` | Per §5.3 (b) row: zero transport calls, the required proof code on `dispatch_result_recorded`, phase (b); automation/script REST 4xx (P1) and WS post-command `unauthorized` (P3) stay (r-proven); a WS registry error result stays (r-unproven) and gets no proof code; lost response stays (c); pre-send versus post-exchange authority retirement (b8); fan **and** power; backup/lifecycle b5 callback failure stays (c) (exclusion guard) |
| `tests/test_hamcp181_terminal_outcome_t2.py` | T2 strings and keys; `terminal_outcome` filter; restart and recovery of each (b) record; no readback for (b) |
| `tests/test_hamcp181_rollback_unchanged_forward.py` | OD-11 refusal-only on the legacy branch; mixed-plan verified prefix unchanged |
| `tests/test_hamcp181_beta8_downgrade.py` | §4.3 executed downgrade: the `7370ad2` reader from `git archive` over the PR2-candidate fixtures |
| `tests/test_hamcp181_entity_registry_error_unproven.py` | **Required regression for the withdrawn P2 (R3-1).** (1) Engineering end to end: a fake WebSocket whose registry entry changes and which then returns an `unknown_error` result, driven through the real entity-metadata gateway, configuration adapter, F3 executor and the PR1 projection. It asserts `dispatch_phase:"sent_failed_mutation_unproven"`, `changed_by_engineering:null`, `provider_dispatch_occurred:true`, no projected `outcome_code:"provider_rejected_mutation"` and no proof code, on the apply result, the task and every §5 surface. The error is never read as no-mutation. (2) The Codex revision-3 probe reproduced offline on **both** supported Core revisions (2026.10.0 `6a811d3359c7b2076dc9e1cf900843a129c044af`, 2026.10.1 `abe1fdd7cd6b4de1715c87b6fd87d92699514ef9`), from hash-pinned excerpts of `homeassistant/helpers/entity_registry.py` `_async_update_entity`, `homeassistant/components/config/entity_registry.py` `websocket_update_entity` and `homeassistant/components/websocket_api/connection.py` `async_handle_exception`: an update whose save scheduling raises leaves the registry changed and returns `unknown_error`. (3) Controls: post-assignment, save-scheduling, update-event and generic `unknown_error` failures are all (r-unproven); the P3 `unauthorized` result stays (r-proven); a historical beta.8 registry-error record without a reason projects (r-unproven). No test may assert non-mutation from an error result. |

**Fixtures (new).** `tests/fixtures/f3_hamcp181_candidate/`: generator,
`README.md`, `provenance.json` naming the exact PR2 candidate commit, and the
§4.3 item 4 record set. `tests/fixtures/hamcp181_core_entity_registry_excerpts/`:
the Core source excerpts for the required regression test, with
`provenance.json` (both Core commit SHAs, source URLs and line ranges,
SHA-256 of each excerpt) and `README.md`. The test runs offline and checks
the hashes before use.

**Support.** `ci/unit-test-shards.json` weights for the five new modules, if
required.

**Release (protected).** As PR1, for beta.10:
`docs/V2_4_1_BETA10_RELEASE_NOTES.md` and `docs/V2_4_1_BETA10_ACCEPTANCE.md`
(new; the acceptance document carries the §4.3 downgrade rule), plus the same
six existing files.

**Estimate.** About 450–750 source lines (required producers) and about
1,500–2,500 test lines.

**Mutations that must be caught.** Move a proof code after a send-capable
await; mark (r) as not sent; emit `provider_call_not_sent` for a backup or
lifecycle callback failure; mark a lost response as not sent; set the attribute at
`:890` or `:605`; treat `HomeAssistantUnavailableError` as proof without the
attribute; treat an entity-registry error result as no-mutation (emit
`changed_by_engineering:false`, a proof code or the (r-proven) phase for it).

**Security section.** Required (provider, persistence and
dispatch-classification boundaries).

**Release.** beta.10, with the downgrade rule in the acceptance document.

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
   - Only b1 (dashboard) and b2 (config/entity metadata, without a proof code)
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
   - The configuration gateway (`runtime.py:702-721`) audits
     `provider_response_received` when the exception carries a received
     response **or** `mutation_dispatched is False`. The first is correct
     (r); the second mislabels a local pre-send refusal (b2-L/b2-A) as a
     received response.
   - The helper-state (`runtime.py:765-769`), backup (`:801-805`) and
     lifecycle (`:839-843`) gateways label by `exc.dispatched` alone:
     `provider_response_lost` when true, `provider_invocation_refused` when
     false. "Refused" contradicts F3 when the intent callback committed and
     then failed (b5); "response lost" contradicts F3 for a confirmed
     failure with a response. Codex accepted these as a disclosed PR1
     limitation that is never retry or no-call authority (§5.1).
5. **`dispatched_at` has opposite semantics, and neither is a send time.**
   Governed tasks report the intent commit time and fan/power report `None`.
   The intent is committed **before** the provider is called (§2.4.2), and no
   stored field records the send time, so the governed value is not a
   dispatch time. `provider_attempt_count` counts intents everywhere.
10. **A confirmed failure with a response does not prove no mutation.**
    Backup (`backup_failed`, `backup_rejected`, `permission_failure`),
    lifecycle (`operation_rejected`, `operation_failed`) and helper state
    (`provider_rejected`) all record `dispatch_failed_confirmed` with a
    received response (`operational_strategies.py:246-263`, `:394-406`,
    `:535-547`, `:665-677`, `:855-866`), but nothing in the code shows the
    provider made no change. Only the configuration/entity-metadata
    rejections P1 and P3 carry a provider-specific non-mutation proof
    (§2.1). An entity-registry error result does not (item 11).
    The others are (r-unproven), and `changed_by_engineering` is `null`.
11. **The runtime still classifies an entity-registry error result as
    confirmed non-mutation.** `R/governance/entity_metadata.py:598-615`
    raises `ConfigurationMutationNotDispatchedError` (reason
    `entity_registry_update_rejected`) for any error result with a response,
    with a comment that validation runs before storage. The adapter then
    records `dispatch_failed_confirmed` with `provider_mutation_count=0`
    (`R/f3_configuration/adapter.py:475-501`), so no F3 readback runs. The
    Codex revision-3 probe shows Core can change the registry and then
    return `unknown_error`. This design fixes only the projection: the
    record is shown as (r-unproven) with `changed_by_engineering:null`, and
    readback stays the way to learn the actual state. The stored
    classification, the zero mutation count and the source comment are
    unchanged by PR1 and PR2. Whether to change them is OD-15.
6. **`plan.operational.dispatch` is stale for F3 plans.** No F3 writer
   updates it, yet the plan audit reads it (`service.py:2414-2426`). The
   reviewer confirmed this with an executed probe (S16). PR1 still adds its
   own regression test before the plan audit stops using it.
7. **Rollback for an unchanged forward is a legacy-branch gap, not a generic
   one.** Revision 1 overstated this. Contract v3 refuses, entity metadata
   refuses `forward_not_dispatched`, and contract-v2 configuration excludes
   any child that is not verified, so a contract-v2 no-call already gets
   `rollback_not_available` (reviewer-executed). Only the legacy automation
   branch (`runtime.py:3423-3436`) lacks a per-child filter (§4.4).
8. **Codex R2-5 correction confirmed.** `list_execution_tasks` *omits*
   `provider_dispatch_occurred`; it does not report it as true.
9. **No shipped-writer compatibility fixtures exist** for the record shapes a
   downgrade test needs (§4.3 item 5).

---

## 8. Owner decisions (Josh)

Columns: **Author** is this design's recommendation after revision 3.
**Reviewer** is the Codex independent design review's per-decision position
(including the delta review). **Differs?** marks where the two still differ,
or where the reviewer's condition changed the author's text; in those rows
Josh chooses. OD-6, OD-7 and OD-14 carry the positions the author and
reviewer aligned on after the delta review. They are **pending Josh's
confirmation** and are not decisions until he confirms them.

| ID | Decision | Options | Author (revision 4) | Reviewer (Codex) | Differs? |
| --- | --- | --- | --- | --- | --- |
| **OD-1** | Task-level representation of (a)/(b) | T1 projection only / **T2** stored `terminal_outcome` + `verification_summary` keys / T3 new task state | **T2**. (r-proven) and (r-unproven) keep `failed_post_dispatch` and get no new stored string (OD-14). | Agree: free-string outcomes plus existing nested summary fields are sufficient; no new enum. | No |
| **OD-2** | F3 record format | **F-A** no change (proof as diagnostic marker) / F-B new outcome + schema v2 | **F-A**. The only new **proof** code is `provider_call_not_sent`; PR2 also adds two optional informational stage reasons (`dispatch_authority_retired_before_send`, `before_dispatch_failed`). All fit the existing bounded diagnostic list. | Agree: existing bounded diagnostics carry proof without breaking the closed F3 shape. | No |
| **OD-3** | Downgrade rule | **Narrow F12-style** (§4.3) / forward-only / expand-contract | **Narrow F12-style**: floor exactly beta.8 `7370ad2`; terminal-only across all namespaces and owners; executed PR2 candidate-writer → beta.8-reader test; explicit not-guaranteed list. | Agree only after R4. Revision 2 applies R4's conditions (named floor, all namespaces/owners, writer → old reader behavior and readiness, no blanket earlier-release guarantee). | No, after correction |
| **OD-4** | Generalize the V9 outcome to fan, power and helper state | Yes, same string and fields / distinct per-capability string | **Yes, same string.** Helper-state (a) then reports `status:"already_present"` in PR2, the only status change (status preservation rule, §2.4.3 item 4). Fan/power have no `status`. | Agree: helper state, fan and power really have no-op paths; backup/reload/restart do not. | No |
| **OD-5** | Name for (b) | `failed_after_intent_not_dispatched` / `intent_recorded_not_dispatched` / other | `failed_after_intent_not_dispatched` | Agree: distinguishes intent from send and does not imply retry. | No |
| **OD-6** | Public timestamps (§2.4.2) | (i) `dispatched_at` = intent commit time when the send is proven / **(ii)** companion fields only; no new non-null `dispatched_at` / (iii) as (i), but governed (c) keeps today's intent time | **(ii), aligned, pending Josh's confirmation.** Fan/power `dispatched_at` stays `null` until PR2 (or a later PR) stores an exact provider-send timestamp under a separately scoped producer/persistence decision. Governed tasks keep the stored value except (b), which becomes `null`. PR1 adds only `dispatched_at_basis`, `dispatch_intent_recorded_at` and `provider_response_recorded_at`, each named for what it is. No surface describes the intent time as a dispatch or send time. | **(ii)** (delta review): (i)/(iii) would need owner acceptance of a lower-bound proxy, not an actual send time. | **Aligned** (pending Josh's confirmation). Revision 2 recommended (i); this replaces it. |
| **OD-7** | Which proven-no-call producers to promote | All local pre-send proofs / a subset | **Subset, aligned, pending Josh's confirmation:** b2-L, b2-A, b3, b5 and b8 in PR2 (b4 as a test seam only), for configuration, dashboard, fan/power and helper state, exactly per §5.3. **Excluded from PR2:** b6, b7, b9 and the backup/lifecycle b3/b4/b5 (their proof would sit inside the b9 provider). Each excluded producer stays (c) and is added later only with a demonstrated pre-write proof point. b2-R is (r-proven) only under P1 (automation/script REST) or P3; an entity-registry error result is (r-unproven); a lost response stays (c). | Conditionally sound once the proof rules are consistent (delta review); D2 required §2.1 and §5.2 to agree, now enforced by §5.3. | **Aligned** (pending Josh's confirmation). |
| **OD-8** | Accept the outcome change for promoted producers: no readback, and a failure even if someone else reached the state | Accept / keep readback (requires F-B) | **Accept** | Agree: Engineering never invoked the mutation; another actor's state does not establish this operation's success. Preserve readback for uncertainty. | No |
| **OD-9** | (a) for configuration REST/helper, dashboard and fan/power drift-equals-target | Out of scope / new item | **Out of scope; open a new register item if wanted** | Agree: it changes admission semantics, not outcome labelling. | No |
| **OD-10** | Accept that the `terminal_outcome` filter no longer matches (a)/(b) under legacy strings | Accept + document / also match legacy strings | **Accept + document**, including that historical records keep their old raw outcomes (no backfill). | Agree with disclosure: state filters stay available; no backfill. | No |
| **OD-11** | Rollback for a forward that changed nothing | Uniform refusal for a **wholly** unchanged forward (every child (a), (b) or (r-proven); an (r-unproven) child may have changed and keeps today's behavior), preserving rollback of verified children in mixed plans / keep current | **Uniform refusal, narrowed** (§4.4): the concrete change is the legacy automation branch; contract-v2 and contract-v3 already refuse; mixed plans keep rollback of their verified prefix. | Agree, narrowed by R5: preserve verified-child recovery and identify the actual legacy gap. | No, after correction |
| **OD-12** | PR split and versions | **PR1 (beta.9) → PR2 (beta.10)** / one combined PR | **Two PRs.** PR1 now also carries the gateway-audit correction and the fan/power timestamp fix. | Agree: separates projection consistency from changed producer outcomes and writer compatibility. Versions remain subject to actual release sequencing. | No |
| **OD-13** | Authorization of the protected surfaces in §6 | Explicit per-PR authorization of the exact file lists | Required before implementation. The lists in §6 are now complete for the recommended options; optional files are listed separately and need their own approval. | Agree after file-map correction: approving the revision 1 lists would not have authorized all promised work. | No, after correction |
| **OD-14** | Representation of (r), sent but failed | **Projection only** / a stored T2 string | **Projection only, aligned, pending Josh's confirmation.** No new stored string. The projection splits (r) by proof: (r-proven) `sent_rejected_no_mutation` with `changed_by_engineering:false` only for a named provider-specific non-mutation proof (P1 or P3 only, §2.1, with provider and code path; P2 is withdrawn, architect decision pending Josh's confirmation); every other dispatched confirmed failure is (r-unproven) `sent_failed_mutation_unproven`, "dispatched; failed; mutation not proven absent", `changed_by_engineering:null`. Adding a proof later needs a reproduced pre-mutation guarantee on every supported Core revision, its own P-row and tests. | Projection-only recommended, with capability-specific non-mutation proof (delta review D1); P2 not accepted (revision-3 review R3-1). | **Aligned** (pending Josh's confirmation). |
| **OD-15** | The runtime classification of an entity-registry error result (§7 item 11) | Keep the runtime as is and rely on projection-only truth / open a separate scoped item that records the result as indeterminate with readback and corrects the `entity_metadata.py:598-615` comment | **Keep as is in PR1 and PR2; the separate item is recommended.** The projection already reports the truth. A producer change is a runtime and persistence change that needs its own scope, tests and review, and is not part of this design. | Not yet reviewed (new in revision 4). | Pending Josh |

---

## 9. Non-actions in this design

No runtime, test, release, workflow, registry, schema or stable-v1 change. No
live Home Assistant or deployed MCP access. No dispatch. No reviewer session.
No Ready, merge, tag, publish or deploy. PR #254 / HAMCP-180 was not touched.

## 10. PR2 implementation note (2.4.1-beta.37)

This note records where PR2 source differs from revision 4. The decisions
are unchanged.

- **§7 item 11 and OD-15 were overtaken by HAMCP-183** (`494defc1`, on main
  before PR2). An entity-registry error result other than P3 now raises
  `EntityMetadataUpdateErrorResult`. The configuration adapter records it as
  `dispatch_indeterminate` with codes `provider_error_after_send` and
  `provider_response_indeterminate`, and `provider_mutation_count` null.
  Registry readback then decides.

  As a result, a live error result projects
  `intent_recorded_send_uncertain` (c), not the (r-unproven) phase the §6
  test row expected. (c) is stricter than (r-unproven): it never claims
  non-mutation and never publishes `changed_by_engineering:false`.
  `tests/test_hamcp181_entity_registry_error_unproven.py` pins that phase.
  It also checks that the phase is never less uncertain than (r-unproven)
  and that no surface claims non-mutation.

  The design's (r-unproven) expectation still applies to historical records.
  The test proves it on a record written by the shipped beta.8 writer, with
  provenance. PR2 does not change this producer.
- **Residual (c) sites outside the §6 file scope** keep today's
  send-uncertain classification. The safe direction is to overstate that a
  send may have happened. These sites are reported in the PR:
  - the pre-request raises in `governance/configuration_delete.py`, in
    `entity_availability_exposure.py` and in `label_registry.py`;
  - `service.py:633` and `:655`;
  - the backup and lifecycle gateways.
- **OD-6(ii) send time.** The send time is captured in memory at the runtime
  provider-boundary audit (`provider_invocation_started`). It is persisted in
  the task's `verification_summary` child entry as `provider_call_started_at`.
  `dispatched_at` then publishes it with basis `provider_call_start_time`.
  (b) stays null. Records without the key keep their stored basis. There is
  no F3 persisted-format change (OD-2).
- **§4.3 item 3 is not attainable for governed change plans (pre-existing,
  owner decision needed).** `tests/test_hamcp181_beta8_downgrade.py` ran the
  exact beta.8 reader over thirteen PR2-candidate shapes.

  Fan and power records meet item 3 fully. Every reader action makes zero
  provider calls and changes no task or child file.

  The governed plans do not. beta.8 quarantines every change plan written
  since HAMCP-213 (first shipped in 2.4.1-beta.29). Its `ChangePlan.from_dict`
  rejects two things:
  - the `ConfigurationOperation` field `called_scripts`;
  - policy version `f2-v3`.

  The plan file then moves to `quarantine/`, `get_change_plan` answers
  `change_plan_not_found`, and a duplicate apply is refused as not found
  rather than as a duplicate. Execution tasks still load.

  PR2 did not cause this and does not change it. Three `expectedFailure`
  tests keep the item 3 plan requirements visible.
- **Owner decision on the downgrade floor (Josh, 2026-10-25), options (i)
  and (ii).** This supersedes §4.3 item 1 for governed plans.
  - (i) beta.8 is documented as a **fan/power/task-only floor**. Its hiding
    of governed plans written since beta.29 is an accepted pre-existing
    limitation and is not fixed here.
  - (ii) The supported **governed-plan rollback target is exactly
    2.4.1-beta.36** (tag `v2.4.1-beta.36`, tag object
    `a4ffc66ddf60095427a1f9f68f0d2c2121b94c3d`, commit
    `16b724ec0b1d0fa8aae9705df569704f27ad783e`).
    `tests/test_hamcp181_beta36_downgrade.py` runs that commit's reader,
    from `git archive`, over the same PR2-candidate fixtures, under the
    item 2 quiescence. It applies the item 3 checks, adds no-rewrite and
    no-loss checks for plans, tasks, `verification_summary` and terminal
    outcomes, and passes for all thirteen shapes. Beta.36 shows its own
    display, for example `dispatched_at` at the intent commit time, which is
    a §4.3 "not guaranteed" behavioral difference.
- **`f3/contracts.py` (owner-confirmed, 2026-10-25).** It holds the shared
  not-sent constants and is recorded in the §6 PR2 file table as an
  owner-confirmed addition.
