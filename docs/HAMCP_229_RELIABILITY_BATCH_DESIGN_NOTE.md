# HAMCP-229 Reliability Batch Analysis: Design Note (2.6.0 wave 1)

Register: HAMCP-229 (audit HAAC-E005), under HAMCP-332 wave 1. Base:
`origin/release/2.6.0` `7246f2d21693f039504542574569f03b71476489`
(Engineering `2.4.2-beta.7`, `hass_mcp_engineering_beta/config.yaml:2`).
This is a docs-only note. It changes no runtime, schema, test, or protected file.
`E` = `hass_mcp_engineering_beta/ha_mcp_engineering/`.

## 0. Disposition (2026-11-08): historical baseline, reviewed delivery, deferred options

- **Historical baseline.** Sections 1-9 were written before the build, at design base `7246f2d2`. Where they
  conflict with the delivery below, the delivery wins.
- **Reviewed delivery.** [PR #402](https://github.com/jeter-1/hass-mcp-admin/pull/402) is merged into
  `release/2.6.0`. Reviewed head `504107150759b0cd243edc7f455a281f0a00b734`, merge commit `57400bdc`.
  `git merge-base --is-ancestor 50410715 origin/release/2.6.0` passes. The existing single tool
  `automation_reliability_analysis` is unchanged, and per-item partiality is still reported truthfully.
- **Implemented defaults** (citations at `1c4c32b3`). `E/reliability/batch.py:9` labels these "implementation
  defaults pending Josh (design note Q4)".
  - A **new tool**, `automation_reliability_batch_analysis(automation_ids, lookback_hours, trace_limit)`
    (`E/tools/analysis.py:201-205`).
  - **Explicit list only.** `automation_ids` takes 1-20 internal automation IDs, as returned by `list_automations`;
    automation entity IDs are not accepted.
  - **Duplicates** collapse in request order and are reported.
  - **Window and traces:** `lookback_hours` 1-720 (default 168); `trace_limit` 1-10 (default 5).
  - **Bounds:** `MAX_BATCH_ITEMS = 20`, `BATCH_SECONDS = 45`, `ITEM_CONCURRENCY = 2` and
    `MAX_BATCH_RESPONSE_CHARS = 60000` (`E/reliability/batch.py:29-38`).
  - **Continuation:** stateless `remaining_automation_ids`; there is no server-side cursor.
- **Not claimed.** No live acceptance.
- **Deferred or optional.** Tier selectors, `query` or "all loaded" selectors, entity-ID input and resume cursors
  would each be separate scope (section 9).

## 1. Register outcome and binding decisions

> Reliability analysis can run over a bounded set of automations (e.g. a tier or
> a list) in one call, with per-item results and truthful partial coverage.

Notes: "Audit HAAC-E005: 113 separate calls to cover the inventory." Effort:
Runtime PR. Dependencies: none.

Binding: Josh's 2026-11-07 brief selects HAMCP-229 for 2.6.0 wave 1. The row
records no other decision. Root `AGENTS.md` keeps public schemas, fail-closed
routing, bounded output and no-fallback in force. Everything else below is a
proposal or is listed in section 9.

## 2. Current state at the base

- **Tool.** `automation_reliability_analysis(automation_id, lookback_hours 1-720,
  trace_limit 1-50, detail_level, limit 1-100, cursor)` (`E/tools/analysis.py:125-182`).
  It takes one **internal** automation ID. `automation.<x>` entity IDs are refused
  by the grammar `[A-Za-z0-9_:-]{1,128}` (`E/reliability/service.py:29-31,103-112`).
  Success sets `result_status` to `partial` or `success`. Any exception becomes a
  `FailureResponse` (`analysis.py:171-182`). Unlike `entity_dependency_analysis`
  (`analysis.py:88-90`), it does not apply the final-envelope screen.
- **Time bound.** One `asyncio.wait_for` around the provider fetch, using
  `timeout_seconds` clamped to 1-120 s (`service.py:84-86,146-162`). The timeout
  comes from `ha_timeout_seconds`, default 60 (`E/application.py:334-340`,
  `E/configuration.py:41`). A timeout raises `PROVIDER_TIMEOUT` (`service.py:167-170`).
  Not-found is re-raised (`service.py:163-166`). A failed or partial-unusable trace
  source with no independent finding raises `ANALYSIS_UNAVAILABLE`/`PROVIDER_TIMEOUT`
  (`service.py:191-206`).
- **Per-automation reads** (`E/reliability/provider.py`), all read-only:
  - REST `GET /config/automation/config/{id}` (`:123-124`).
  - WS `config/entity_registry/list`, the **whole registry**, once per analysis (`:150-151`).
  - REST `GET /states/{automation entity}` (`:172-179`).
  - A local blueprint read when `use_blueprint` is set (`:233-234`).
  - Up to `MAX_REFERENCED_ENTITIES = 100` REST `GET /states/{id}` reads at
    concurrency 5, maximum 10 (`:34,58,290-307`).
  - WS `trace/list` with `domain: automation` (`E/trace_normalization.py:92`), then
    up to `trace_limit` `trace/get` (`provider.py:375-378`).
  - WS `system_log/list`, the **whole log**, with at most 20 matches kept (`:35,485`).
- **Output budget.**
  - Findings pages hold at most `MAX_FINDINGS = 100`. Evidence lists stop at 100.
    Cursor continuation uses an in-memory snapshot store (16 entries, 300 s TTL,
    `service.py:32-35,55-80,386-415`).
  - The envelope uses `response_size_limit`, default 60 000 chars
    (`configuration.py:42`). Over the limit, `dump_json_with_truncation` cuts the
    payload after the result was built (`E/models/responses.py:492-507`).
- **Routing and authority.**
  - Engineering-native capability `RELIABILITY_ANALYSIS` (`E/providers/routing.py:223`).
  - Direct-read policy `single_automation_reliability_read`, `writes_allowed: none`,
    `fallback_policy: none` (`routing.py:307-314`, echoed at `service.py:372-385`).
  - Core route requirements `core.automation_trace_read` and
    `core.dependency_helper_planning` (`E/ha_core_readmission/routes.py:324-327`).
  - Catalog entry: `E/capabilities.py:757-765`. Health reports
    `"scope": "single_automation"` (`E/reliability/runtime.py:26-27`).
- **Selectors in source.**
  - No source defines a "tier": a grep of `E/` finds only an approval-tier comment
    (`E/tools/governance.py:1413`).
  - Existing automation selectors: (a) an explicit internal-ID list (precedent
    `handoff_generation.automation_ids`, at most 20, `analysis.py:530`); (b) the
    `list_automations` substring `query` over entity_id/friendly_name from
    `GET /api/states` (`E/tools/compatibility.py:618-632`, route
    `core.automation_configuration_read`, `routes.py:259`); (c) the baseline
    inventory scope `loaded_automation_entities-v1` (`E/audit_baseline/capture_contracts.py:13`).
  - No read-side label, area or category selector exists for automations.
- **Bounded-batch precedent.** `capture_automation_baseline` uses
  `COLLECTION_SECONDS = 45`, `READ_SECONDS = 5`, `CONCURRENCY = 4`,
  `MAX_RECORDS = 1000` (`capture_contracts.py:26,35-37`). It stops scheduling
  before the bound and marks unscheduled records `timed_out`
  (`capture_provider.py:241,304`).
- **Missing.** No multi-automation reliability entry point exists. The audit made
  113 single calls (register Notes).

## 3. Proposed change

**Shape (recommended, Q1).** A new read-only tool
`automation_reliability_batch_analysis`. It leaves the existing tool's schema and
behaviour byte-for-byte unchanged. Each item runs through the unchanged
`AutomationReliabilityAnalysisService` evidence path (provider `fetch`, rules,
lifecycle). There is no new Home Assistant read path.

**Inputs.**
- `automation_ids: list[str]`, 1 to `MAX_BATCH_ITEMS` (proposed 20, Q4). Each item
  uses the existing grammar (`service.py:31`).
- Optional selector, only if Josh approves Q3: `query: str`, the same substring
  semantics as `list_automations`. A selector is used either instead of
  `automation_ids` or together with it, as Q3 decides.
- `tier` is **not** proposed. No source defines it (Q2).
- `lookback_hours` (1-720, default 168), shared by all items.
- `trace_limit` (1-10, default 5, lower than single to fit the time budget).
- No `detail_level`, `limit` or `cursor`: items are always summary digests.

**Validation (whole-request, before any HA read).**
- `InvalidRequestError` with the offending indexes for: a grammar violation
  (including dotted entity IDs, unless Q5 decides otherwise), an empty list, more
  than `MAX_BATCH_ITEMS`, or an out-of-range bound.
- Exact duplicates are collapsed to the first occurrence and reported in
  `request.duplicates_removed`. They are not refused.

**Execution.**
- One analysis instant is taken for the whole batch, so every item uses the same
  lookback window.
- Items are scheduled **in request order**. For a selector, the order is entity_id
  ascending, as capture sorts (`capture_provider.py:253`).
- Item concurrency is 2. Inside each item the provider's own concurrency of 5
  still applies, so at most 10 HA reads are in flight. That matches the
  provider's existing maximum (`provider.py:58`).
- **Time budget** `BATCH_SECONDS` (proposed 45 s, Q4):
  - No item starts once the remaining budget is under a reserve (proposed 5 s).
  - Each started item runs under `min(service timeout, remaining budget)`.
- One process-wide in-flight batch is allowed. A concurrent call gets a retryable
  refusal before any read. The error code comes from the existing `ErrorCode` set
  at build time.

**Per-item status** (one row per requested ID, in schedule order):

| `status` | `reason` (closed set) | Meaning |
|---|---|---|
| `analyzed` | `null` | Evidence collected. `result_status` is `success` or `partial`, plus the item's `overall_assessment` |
| `failed` | `automation_not_found`, `provider_timeout`, `analysis_unavailable`, `time_budget_exceeded`, `internal_error` | The item started and produced no usable analysis. These map from the existing single-item failure paths (`service.py:163-206`) |
| `skipped_budget` | `time_budget`, `output_budget` | Never started (time), or analyzed but its digest did not fit (output). No HA read for `time_budget` |

**Item digest** (summary only, built from the existing single-item `data` keys at
`service.py:307-371`):
- `target`;
- `overall_assessment`, `result_status`;
- `finding_counts_by_severity`, `unique_root_cause_count`;
- at most 3 root-cause summaries;
- `lifecycle_analysis.hazards_detected` and `coverage_complete`;
- per-source completeness (`source_type` to `completeness`);
- `configuration_fingerprint`, `evidence_fingerprint`.

The digest has no findings pages and no evidence payloads. Drill-down uses the
single tool.

**Output budget.**
- The batch response is limited to `min(60 000, response_size_limit)`, as baseline
  capture does.
- Each digest is serialized and charged against a budget *before* the envelope is
  built.
- A digest that would overflow, and every digest after it, becomes
  `skipped_budget/output_budget`. Each keeps its `automation_id`, so no item
  disappears.
- The tool asserts the final envelope was not cut by `dump_json_with_truncation`.
  A cut envelope is a defect.

**Truthful completeness** (`data.coverage`):
- Counts: `requested`, `deduplicated`, `analyzed_complete`, `analyzed_partial`,
  `failed`, `skipped_budget`.
- `remaining_automation_ids`: the ordered `skipped_budget` and
  `failed/time_budget_exceeded` IDs, ready to resubmit. No server-side state is
  kept (Q6).
- `batch_result_status`:
  - `success` only when every item is `analyzed` with `result_status: success`;
  - `failed` when no item was analyzed;
  - `partial` otherwise.
- Telemetry `completeness` follows the same rule.
- A selector reports `matched_total`. Matches beyond `MAX_BATCH_ITEMS` are counted
  in `selector_unselected` and never presented as analyzed.
- Request-level failures still return `FailureResponse`. These are validation, an
  unconfigured service, a Core lease refusal, and selector resolution failure.
  Per-item failures never abort other items.

**Screening.** The aggregated envelope passes `_final_screen`
(`analysis.py:34-50`) before return, because many automations' friendly names now
share one response.

**Routing.**
- Engineering-native, capability `RELIABILITY_ANALYSIS`.
- New direct-read policy `bounded_automation_set_reliability_read` (`writes_allowed:
  none`, `fallback_policy: none`). Its scope reads "at most N automations, each
  under `single_automation_reliability_read` bounds".
- No `ha-mcp` delegation and no fallback.

**Core commands.** None new. The batch issues exactly the single tool's command
shapes, repeated per item. The Q3 `query` selector, if approved, adds only REST
`GET /api/states`, the same read `list_automations` performs today. Confirmed: no
new Core command, service call, or write.

## 4. Public schema / tool-registration impact

| Surface | Change |
|---|---|
| Static tools | 81 to 82 (`ENGINEERING_STATIC_TOOL_COUNT`, `E/tools/registry.py:240`). At least 9 test files assert 81 at the base |
| Existing `automation_reliability_analysis` schema/output | Unchanged |
| New input/output schema | New tool only (section 3) |
| Registration files (all protected) | `E/tools/analysis.py` (`ANALYSIS_TOOLS`, `:605-612`), `E/tools/registry.py`, `E/capabilities.py`, `E/providers/routing.py` (capability map and policy), `E/ha_core_readmission/routes.py` (`STATIC_TOOL_CORE_REQUIREMENTS`), `E/observability.py`/`E/health.py` (batch metrics, `runtime.health()` scope), `E/reliability/` (new `batch.py`) |
| Persisted formats | None (stateless) |
| Stable v1 | Untouched |

The security-sensitive surfaces are public schema, tool registration, routing
policy, and bounded output. They change only additively.

## 5. Signing impact

**None.** No signed Core profile is new or changed.
- The batch reuses `core.automation_trace_read` and
  `core.dependency_helper_planning` unchanged (`routes.py:324-327`).
- The optional Q3 selector adds only the existing signed
  `core.automation_configuration_read` to the new tool's route, as
  `list_automations` already uses it (`routes.py:259`).
- Tool-to-profile routes live in code, not in the signed registry.
  `docs/CORE_RELEASE_REGISTRY.md` states registry data cannot add a tool.
- No command, argument or field is added to any profile, so no fingerprint moves.

| Profile id | New or changed (from version X) | Core commands / contract added | Why | Fingerprint impact | Can it batch with other 2.6 profiles |
|---|---|---|---|---|---|
| none | n/a | none | Reuses existing signed profiles and command shapes unchanged | None | n/a (nothing to sign) |

## 6. Negative reachability and refusal tests

There is no new write, service, reload, restart or delete path. Tests, offline,
with synthetic loopback fixtures:
1. **Command-shape allowlist.** Across a 20-item batch, the only HA requests are
   the single tool's shapes (section 2), plus `GET /api/states` only when `query`
   is used. Assert no `call_service`, no `/api/services/*`, no `automation/*`
   trigger or reload, and no `ha-mcp` dispatch. Metadata shows
   `fallback_occurred: false`.
2. **Whole-request refusal with zero HA reads.** Covers: a dotted entity ID, an
   empty list, 21 IDs, `trace_limit` 11, an unconfigured service, a Core lease
   refusal, and a concurrent second batch.
3. **Per-item isolation.**
   - One `AutomationNotFoundError` and one provider timeout: neighbours still
     `analyzed`, and the batch is `partial`.
   - All items failed: the batch is `failed`.
4. **Time budget.** With a fake clock, items after the deadline are
   `skipped_budget/time_budget` with no reads issued for them. A started item cut
   by the budget is `failed/time_budget_exceeded`. `remaining_automation_ids`
   preserves order.
5. **Output budget.** Oversized digests become `skipped_budget/output_budget`.
   Every requested ID is present exactly once. `dump_json_with_truncation`
   reports `truncated: false`.
6. **Ordering and dedupe** are deterministic. Duplicates are reported.
7. **Screening.** A synthetic secret in a friendly name is withheld by the final
   screen.
8. **Single-tool continuity.** The existing tool's schema, output and failure
   tests stay byte-identical.

## 7. Acceptance

- **Success.** 20 healthy synthetic automations give 20 `analyzed/success` digests
  and `batch_result_status: success` in one call. The 113-item audit inventory
  needs 6 calls instead of 113 at the proposed maximum.
- **Refusal.** Section 6 items 2 and 8.
- **Failure preservation.** Single-item failure codes map one-to-one to item
  reasons. Request-level failures keep the existing `FailureResponse` shape.
- **Verification and recovery.** Resubmitting `remaining_automation_ids` covers the
  rest. Each digest's `evidence_fingerprint` matches a single-tool run made at the
  same instant with the same bounds.

## 8. Build slice and dependencies

- Delivered as #402 (section 0). Historical build plan: one runtime PR (register Effort). No signing dependency and no persisted format.
- It shares registration files with in-flight 2.5.0 work (HAMCP-319 admission;
  HAMCP-290 Marketplace route; possibly HAMCP-288 if it touches blueprint reads).
  2.5.0 wins shared files, so build after those merge into `release/2.6.0`.
- The build task must name the protected runtime paths listed in section 4.
- Rollback: revert the PR. Nothing is stored and the single tool is unaffected.

## 9. Open questions

None blocks the merged slice. Superseded (historical) as build gates, answered by the #402 implementation (section 0):
- **Q1** New tool. Implemented as `automation_reliability_batch_analysis`; the existing schema is unchanged.
- **Q3** Explicit list only.
- **Q4** 20 items, 45 s, concurrency 2, `trace_limit` at most 10. The source labels these "implementation
  defaults pending Josh". Confirming or changing them is optional; changing them would be separate runtime scope.
- **Q5** Internal IDs only, as in the single tool.
- **Q6** Stateless `remaining_automation_ids`.

Optional future scope (pending Josh; not needed for the merged slice):
- **Q2** "Tier" has no source definition. A tier selector, and a `query` or "all loaded automations" selector, would
  each be a separate scope choice.
- Entity-ID input or a server-side resume cursor would each be a separate scope choice.

## 10. Codex design review 2026-11-08 — disposition

| Finding | Severity | Correction in this note |
| --- | --- | --- |
| F2: delivered slice and implemented defaults not recorded | Low | Section 0 records the #402 delivery (reviewed head `50410715`, merge `57400bdc`, ancestry checked) and the implemented defaults. Section 8 marks the build delivered. Section 9 turns Q1 and Q3-Q6 from build gates into recorded implementation, and keeps the tier, selector and cursor choices as optional separate scope. No live acceptance is claimed. Proving check: reconcile with `register get HAMCP-229` and `E/reliability/batch.py` / `E/tools/analysis.py:201-205` at `1c4c32b3`. |
