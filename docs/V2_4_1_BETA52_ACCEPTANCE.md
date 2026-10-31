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
2.4.1-beta.51 (#331, HAMCP-252). It is staged in `.release/next-version`
and not yet materialized. Pull-request CI fails closed with "Unsupported
Engineering release transition" until it is. Nothing is published or
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
  - truthfulness: no count before it is validated;
  - validation and the deep audit are not weakened;
  - authorization keeps reading persisted records;
  - no format migration and no retention change;
- `2.4.1-beta.52` materialized in all three version authorities before
  Ready, with exact document resolution and no staged declaration left over;
- the PR head SHA, its `validate` run and the Evidence tier log named in
  the PR;
- the benchmark under `docs/evidence/hamcp097-health-benchmark/` (a retained
  artifact, not a CI gate).

The review needs a security section covering:
- **No new reachability.** No Home Assistant access, write, service call,
  reload, provider, route, fallback or upstream dispatch. The startup
  validation is write-free: it never resolves a lifecycle transition that
  would persist.
- **Authorization unchanged.** Approve, apply and rollback never consult
  the reuse entries. A test patches the lookup to fail and proves that.
- **Fail closed.** A reuse entry needs an exact path, stat and SHA-256
  match on bytes read in the same call. Any mismatch, missing stat, deep
  audit, storage or governance error, or unexpected exception discards
  entries or re-decodes. Tampered bytes with an unchanged stat are
  detected by the hash.
- **Bounded output.** The counters are integers, enumerations and bounded
  error categories. They contain no paths, digests or record content.

Required offline evidence:
- `tests/test_hamcp097_incremental_health.py`. It covers:
  - differential equality with the full projection across create,
    approve, reject, apply success and failure, expiry, rollback,
    projection failure, manual review, corruption, quarantine restoration
    and restart;
  - 0 terminal records decoded on a warm no-change call;
  - exactly k re-projections for k changes, flat at 2x and 4x history;
  - approval actionability re-evaluated only for changed plans, and for
    every plan that may own a quarantined task file, when the quarantine
    listing fails, and on a deep audit;
  - in-progress markers, deep-audit wait, never persisting, failure and
    cancellation;
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
