# Engineering 2.4.1-beta.52 acceptance

This contract covers HAMCP-097: `get_server_health(check_ha=false)` latency
against Josh's SLO, with every governance field still exact. It follows
2.4.1-beta.51 and includes it.

See also:
- Release notes:
  [`V2_4_1_BETA52_RELEASE_NOTES.md`](V2_4_1_BETA52_RELEASE_NOTES.md).
- Field contract: `hass_mcp_engineering_beta/OBSERVABILITY.md`, section
  "2.4.1-beta.52 incremental governance health".

**Release target.** `2.4.1-beta.52` is the next free beta after
2.4.1-beta.51 (#331, HAMCP-252). It is materialized in all three version
authorities, and no staged declaration is left. Nothing is published or
deployed. If another release reaches `main` first, this change is restaged
at the next free beta and these documents are renamed.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind:
- Josh's HAMCP-097 direction:
  - SLO: warm ≤ 1.5 s, and cold ≤ 5 s (or within 5 s with explicit
    in-progress markers);
  - cost scales with active work, not retained history;
  - accepted exception (Josh, 2026-10-31): this release does not yet meet
    that direction. Every call still reads and hashes every record and
    rebuilds the overlay and snapshot, so total cost stays
    history-dependent. On the x4cap fixture (1,226 plans and 1,024 tasks,
    2.45 times the 501-plan fixture, not a full 4x store) the first call
    after 5 changes took 1,739 ms, above the 1.5 s warm SLO. The remaining
    scaling is HAMCP-259 (Medium);
  - truthfulness: no count before it is validated;
  - validation and the deep audit are not weakened;
  - authorization keeps reading persisted records;
  - no format migration and no retention change;
- `2.4.1-beta.52` materialized in all three version authorities before
  Ready, with exact document resolution and no staged declaration left over;
- the PR head SHA, its `validate` run and the Evidence tier log named in
  the PR;
- the benchmark under `docs/evidence/hamcp097-health-benchmark/` (a retained
  artifact, not a CI gate). It times the governance component
  `service.async_health_summary` on synthetic offline stores, not
  end-to-end MCP latency. Every scaling statement in these documents
  quotes it; the release notes section "Measured" gives each fixture,
  call and value.

The review needs a security section covering:
- **No new reachability.** No Home Assistant access, write, service call,
  reload, provider, route, fallback or upstream dispatch. The startup
  validation never persists a lifecycle transition. Its only write is the
  pre-existing corrupt-record quarantine, which it reports as
  `writes: "corrupt_record_quarantine"` with plan and task counts.
- **Authorization unchanged.** Approve, apply and rollback never consult
  the reuse entries. A test patches the lookup to fail and proves that.
- **Fail closed.** A reuse entry needs an exact path, stat and SHA-256
  match on bytes read in the same call. Any mismatch, missing stat, deep
  audit, storage or governance error, or unexpected exception discards
  entries or re-decodes. Tampered bytes with an unchanged stat are
  detected by the hash. Any quarantine or restoration changes the
  assembled summary's key (quarantine listings and generations), and an
  unlistable quarantine directory forbids its reuse.
- **Bounded output.** The counters are integers, enumerations and bounded
  error categories. They contain no paths, digests or record content.

Required offline evidence:
- `tests/test_hamcp097_incremental_health.py`. It covers:
  - differential equality with the full projection across create,
    approve, reject, apply success and failure, expiry, rollback,
    projection failure, manual review, corruption, a task and a plan
    quarantine appearing and disappearing, quarantine restoration, tamper
    after restoration, and restart;
  - a quarantine-only change (no tracked record changed) is never served
    from the assembled summary;
  - 0 terminal records decoded on a warm no-change call;
  - exactly k re-projections for k changes, flat at 2x and 4x history;
  - approval actionability re-evaluated only for changed plans, and for
    every plan that may own a quarantined task file, when the quarantine
    listing fails, and on a deep audit;
  - in-progress markers, deep-audit wait, never persisting a lifecycle
    transition, failure and cancellation;
  - a startup that quarantines reports `corrupt_record_quarantine` with
    counts and no identifier or path, and a clean startup reports
    `writes: "none"`;
  - tamper detection, and that authorization never reads the index;
- the existing governance, health-responsiveness, F3, approval and
  rollback suites, unchanged apart from the disclosed reviewed caller set;
- the complete repository suite in the Evidence tier.

## 2. Release CI

The exact materialized head's `validate` check passes.

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow.

## 4. Installed Engineering acceptance (separately authorized, read-only)

The architect runs these after deployment, against the live store (501
plans):

1. **First call after add-on start.** `get_server_health(check_ha=false)`
   answers within 5 s. Either it is complete, or it is
   `historical_validation.state == "in_progress"` with every
   history-derived field `null` and listed in `pending_fields`.
2. **After `historical_validation.state == "complete"`:**
   - the counts are identical to beta.51: `total_plans` 501, the same
     `plans_by_policy_class` mix, `projection_failure_count` 2 and
     `execution_tasks.tasks_manual_review` 1;
   - no `pending_fields`.
3. **Warm call.** Answers in ≤ 1.5 s.
   `plan_store_scaling.hot_paths.governance_health` shows
   `terminal_plan_records_deserialized` 0 and
   `terminal_task_records_deserialized` 0.
4. **Deep audit.** `deep_storage_audit=true` runs a full rescan
   (`incremental_health_index.last_run.mode == "deep_rescan"`). Its
   counts agree with step 2.
5. **Smoke.** `list_change_plans`, `get_change_plan` and
   `list_execution_tasks` behave as on beta.51.

If a step fails, record the phase timings and counters in HAMCP-097.
Whether to roll back is Josh's decision.
