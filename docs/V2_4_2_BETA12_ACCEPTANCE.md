# Engineering 2.4.2-beta.12 acceptance

**Status: in progress.** This contract covers the 2.9.0.1 hotfix, released as
2.4.2-beta.12. It follows 2.4.2-beta.11 (2.9.0, #481). Its content lands
through item pull requests into the `release/2.9.0.1` integration branch. The
final release pull request completes every section marked **pending** before
it is offered for review.

See also the release notes:
[`V2_4_2_BETA12_RELEASE_NOTES.md`](V2_4_2_BETA12_RELEASE_NOTES.md).

**Release target.** `2.4.2-beta.12`, following 2.4.2-beta.11. The architect's
block of 2026-11-23 names this version for the 2.9.0.1 hotfix path, and the
value is authorized on that path only. "2.9.0.1" names the build, not the
version string.

**Why the version is materialized now.** `main` carries 2.4.2-beta.11 (#481),
and CI validates every pull request against `origin/main` with a
materialized, unstaged release whatever its base
(`scripts/validate_addon_metadata.py`, `.github/workflows/ci.yml`). So
2.4.2-beta.12 is materialized once on `release/2.9.0.1`, at its base (`main`
at 23c5c429), and item pull requests carry no version change of their own.

The release is materialized in all three version authorities, with no staged
declaration left. Nothing is published or deployed.

## Items

Each item is reviewed in its own pull request into `release/2.9.0.1`. An item
that has not landed is not part of this release, and this contract makes no
claim about its behavior.

| Item | Scope | State |
| --- | --- | --- |
| HAMCP-470 | Lifecycle add-on identity read at governed apply on ha-mcp 9.2.0: decode bound, text comparison, error mapping and diagnostic | landed: #483, reviewed head e044b9f7, merged as 71520cba. Cursor r1 READY; Codex r1 CHANGES_REQUIRED, r2 resolved; the architect's evidence run in a separate clone (50 tests OK; M18, M19, M20 each fail; restored OK) |

## 1. Source and offline acceptance

**Pending.** The final release pull request binds each landed item's pull
request, reviewed head, review record and offline evidence, and the final
head SHA, its `validate` run and the single Evidence tier log.

## 2. Release CI

The exact materialized final head's `validate` check passes, including the
release transition. **Pending.**

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow. **Pending.**

## 4. Installed read-only acceptance (separately authorized, after deploy)

This stage runs only after Josh separately authorizes deploying this
release. Every step is a read. Josh runs it through HA Admin. Stages 4 and 5
run in one sitting, and Josh approves each step. HA Admin never requests
add-on options, secrets or the ha-mcp secret path.

1. **Identity.** `server_info` and `get_server_health` report:
   - `2.4.2-beta.12`;
   - the merged release SHA as build SHA;
   - `dirty: false`.
2. **Lifecycle health and the admitted ha-mcp set.** These are the fields of
   beta.11 section 4 step 2, unchanged by this release. The lifecycle
   provider is probed lazily, at plan creation (`planning_evidence`,
   `governance/operational_lifecycle.py`). Until the first lifecycle plan is
   created, `operational_status` is `unknown` and the entry id, observed
   version and `catalog_admission` are not set. So read them in section 5
   step 1, right after the reload plan is created and before it is applied.
   Under `operational_administration.lifecycle_provider`, expected:
   - `operational_status` `available`, provider
     `upstream_operational_lifecycle`;
   - `selected_compatibility_entry_id` `ha-mcp-v9.2.0-9de5bf6c`, and
     `observed_upstream_version` `9.2.0`;
   - `catalog_admission`: `model` `reviewed-dispatch-scoped-catalog-v1`,
     `status` `accepted_unused_tool_drift`, `admitted` `true`,
     `exact_tool_mismatches` `[]`, `exact_tools` including `ha_manage_app`
     and `ha_get_app`, and `unused_tool_drift_count` `1`
     (`ha_config_set_helper`);
   - `catalog_validation.validation_status` stays
     `rejected_catalog_mismatch`, as on beta.11;
   - `lifecycle_addon_text_comparison` is `null`: no add-on identity read has
     run yet.
3. **The other beta.11 section 4 reads** (the read gateway, `result_fields`
   and replaced upstream warnings) are not changed by this release. Their
   expected values are those of beta.11 section 4, as corrected on `main`
   (HAMCP-469). Whether to re-run them on beta.12 is Josh's decision
   [JOSH].

## 5. Installed governed-change acceptance (separately authorized, after deploy)

This stage re-runs beta.11 section 5, in the same order and under the same
general rules: Josh approves each plan at its exact hash; exactly one
provider dispatch per applied plan; never re-apply, redispatch or re-plan to
retry; on a stop condition, record the plan, the task and the response, and
stop the stage. Step 3 holds the only Home Assistant restart.

On beta.11 every lifecycle apply was refused before dispatch, with
`internal_server_error`, because the ha-mcp add-on identity read at apply
failed its `STRUCTURED_V1` decode with ha-mcp `redact_secrets` on
(HAMCP-470). This stage proves the fix on the house as it is: ha-mcp 9.2.0
with `redact_secrets` on.

**Before starting, Josh names** the non-critical add-on for step 2 and the
test automation for step 3 (its id and alias), as on beta.11.

**What the new diagnostic shows.** At apply, F3 prepare reads the ha-mcp
add-on's identity before dispatch (`_provider_identity`,
`f3_runtime/runtime.py`): the installed inventory, then the detail. Every
plan in steps 1 to 4 that goes through that path reads it; direct helper
operations, dashboards and contract-v1 automation plans do not. After each
apply that reads it, `get_server_health` shows
`operational_administration.lifecycle_provider.lifecycle_addon_text_comparison`
as one entry per read, `inventory` and `detail`. Each entry holds only
enums:
- `text_comparison`: `verified_equal` | `skipped` | `mismatch` |
  `not_reached`;
- `text_failure_class`: `none` | `oversize` | `invalid_json` |
  `duplicate_member` | `nonfinite` | `recursion` | `unicode`;
- `text_bytes_bucket`: `<64K` | `64K-128K` | `128K-256K` | `256K-512K` |
  `512K-1M`.

The field holds the most recent add-on read only: it is reset when any
add-on read (`get_addon`) starts. An apply that reads no add-on leaves the
previous value. In step 2 the planning and verification reads of the named
add-on (`governance/operational_lifecycle.py`) also replace it, so after
step 2 it describes the verification read of that add-on, not ha-mcp's
identity; the detail bucket expectation below does not apply to it.

Record the entries after each such apply. Expected, for every one:
- each entry is `verified_equal` with class `none`, or `skipped` with one
  of the six failure classes. With `redact_secrets` on, `skipped` with
  `invalid_json` or `duplicate_member` is an expected outcome, not a
  failure: the decision then comes from `structuredContent` under every
  structured check;
- the inventory bucket is `<64K`. The detail bucket is expected to be
  `128K-256K` (an estimate from the 9.2.0 add-on's translations, about
  228 KB, not a measurement). Another bucket is recorded, not a stop by
  itself;
- no entry is `mismatch` or `not_reached`.

**An identity read that still fails** is refused before dispatch with
`operational_contract_mismatch` (no longer `internal_server_error`), with
`failure_stage` `pre_dispatch`, `provider_dispatch_occurred` `false`,
`fallback` `none` and `required_action`
`refresh_provider_evidence_and_replan`. That is a stop condition for the
step: record the response and the diagnostic, and stop the stage.

### Step 1. Reload automations

1. `create_reload_plan` with `reload_target: "automation"`.
2. **Read the section 4 step 2 fields now,** before approving.
3. Josh approves. `apply_change_plan`, then `get_execution_task`.

**Expected:** as beta.11 step 1 (the task `succeeded_verified`, exactly one
dispatch, the configuration validation `valid` and the reload service
available), plus the diagnostic above.

**Stop if:** as beta.11 step 1; or the apply is refused with
`operational_contract_mismatch`; or an entry is `mismatch` or `not_reached`.

### Step 2. Restart one non-critical add-on

As beta.11 step 2 (`create_addon_restart_plan`, the target class recorded,
`succeeded_verified`, exactly one `ha_manage_app` dispatch, and the
`restart_proof` matching the target class: `other_addon` gives
`provider_acknowledgement`), plus the diagnostic above. The stop conditions
are those of beta.11 step 2 and of step 1 here.

### Step 3. E043 and the created identity, with one Home Assistant restart

As beta.11 step 3, unchanged: the governed create planned and approved, not
applied; one governed restart; readmission `restored`; then the create
applied. Expected values are those of beta.11 step 3: the restart
`succeeded_verified` with exactly one `ha_restart` dispatch and
`restart_dispatch_confirmed`, `authoritative_outage_observed` and
`home_assistant_reconnected` all `true`; one `core_authority_restored`
record with `reason: core_authority_identical` and `plans_preserved: true`;
the create `succeeded_verified` with an exact read-back and a captured
`created_identity`. Record the diagnostic after the restart apply. The stop
conditions are those of beta.11 step 3. Do not restart Home Assistant again
to retry.

### Step 4. Clean up through rollback

As beta.11 step 4: `rollback_change` gives `rollback_plan_created`; Josh
approves; the rollback task is `succeeded_verified` with exactly one
dispatch, and the test automation no longer exists.

PASS for the stage needs every expected value in every step that ran.

## Rollback

**Pending.** The final release pull request states the rollback target and
whether rolling back is safe.
