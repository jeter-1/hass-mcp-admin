# Engineering 2.4.2-beta.10 acceptance

**Status: in progress.** This contract covers the 2.8.0 build, released as
2.4.2-beta.10. It follows 2.4.2-beta.9 (HAMCP-371, #444). Its content lands
through item pull requests into the `release/2.8.0` integration branch. The
final release pull request completes every section marked **pending** before
it is offered for review.

See also the release notes:
[`V2_4_2_BETA10_RELEASE_NOTES.md`](V2_4_2_BETA10_RELEASE_NOTES.md).

**Release target.** `2.4.2-beta.10`, following 2.4.2-beta.9. The architect's
task of 2026-11-16 18:34 CST names this version for the HAMCP-401 Phase 1
release path, and the value is authorized on that path only. "2.8.0" names
the build, not the version string.

**Why the version is materialized now.** `main` carries 2.4.2-beta.9 (#444),
and CI validates every pull request against `origin/main` with a
materialized, unstaged release whatever its base
(`scripts/validate_addon_metadata.py`, `.github/workflows/ci.yml`). So
2.4.2-beta.10 is materialized once on `release/2.8.0`, at its base (`main` at
02b8f013), and item pull requests carry no version change of their own.

The release is materialized in all three version authorities, with no staged
declaration left. Nothing is published or deployed.

The contract has five stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed read-only acceptance;
5. installed governed-change acceptance (I1, E043, E044, E052).

It authorizes none of the later stages.

## Items

Each item is reviewed in its own pull request into `release/2.8.0`. An item
that has not landed is not part of this release, and this contract makes no
claim about its behavior. Landed items are listed by their pull request; the
final release pull request binds their evidence.

| Item | Scope | State |
| --- | --- | --- |
| HAMCP-401 Phase 1, with HAMCP-400 | governed reload, app restart and Home Assistant restart on ha-mcp 9.2.0; the `provider_held` refusal category | in progress on its branch; not landed; evidence pending |
| HAMCP-415 / HAMCP-416 | plans survive an identical Core restart (E043); terminal rollback plans are replanned (E044) | landed: #448, reviewed head 5a39a919, merged as 7aa5e0cf; evidence bound by the final release pull request |
| HAMCP-410 | ha-mcp 9.2.0 key-set maps for bulk `ha_get_state` and exposure listings; emptied collections read `partial` (E042) | landed: #446, reviewed head fe93a18b, merged as bcf42e52; evidence bound by the final release pull request |
| HAMCP-411 bundle (HAMCP-408, 409, 411, 412, 413, 414) | truthfulness fixes, including bounded `get_history` with a continuation (E041) | in progress: #447; not landed; evidence pending |
| HAMCP-417 / HAMCP-419 (optional) | I5, including the automation `initial_state` field (E052) | in progress: #450; not landed; evidence pending |
| HAMCP-418 | an oversize list response returns a first page of whole rows, flagged `partial` (E048) | in progress: #451; not landed; evidence pending |

## 1. Source and offline acceptance

**Pending.** The final release pull request binds, for each landed item:
- its owner decision and scope;
- its item pull request, reviewed head and review record;
- its required offline evidence, success and refusal paths, and negative
  reachability tests;
- a security section for each provider, write, workflow or persistence
  change.

It also binds:
- `2.4.2-beta.10` materialized, with exact document resolution;
- the final head SHA, its `validate` run and the single Evidence tier log
  named in the release pull request.

No item evidence is recorded here yet. The Evidence tier runs once, on the
final release head.

## 2. Release CI

The exact materialized final head's `validate` check passes, including the
release transition. **Pending.**

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow. **Pending.**

## 4. Installed read-only acceptance (separately authorized, after deploy)

This stage runs only after Josh separately authorizes deploying this
release. Every step is a read.

1. **Identity.** `server_info` and `get_server_health` report:
   - `2.4.2-beta.10`;
   - the merged release SHA as build SHA;
   - `dirty: false`.
2. **Item checks.** Run a check only for an item that has landed; the final
   release pull request removes the rows for any item that has not.
   - **Batch state (HAMCP-410, I2).** Call `ha_get_state` with
     `entity_id` set to a list of two existing entities. Expected:
     - `data.states` is an object keyed by both entity IDs, each holding its
       state;
     - `data.fields_withheld` is `0`;
     - `metadata.completeness` is `complete`;
     - no `COLLECTION_WITHHELD` in the engineering notes or warnings.

     Stop and record the response if an entity is missing, a field is
     withheld, or the result is partial.
   - **History against logbook (HAMCP-411, I3).** Pick one entity with a few
     state changes in the last 24 hours. Call `get_history` and `get_logbook`
     for that entity over the same window. Expected:
     - every logbook entry that records a state for the entity has a
       history row with the same state and a `last_changed` within the
       window;
     - history may hold more rows than the logbook (attribute-only
       changes), never fewer state changes;
     - a history result that is not `complete` says so, with `truncated`,
       `has_more` and a `get_history_slices` continuation.

     Stop and record both responses if a logbook state change is missing
     from a history result that reports itself `complete`.
3. **Tool catalog.** A fresh `tools/list` matches the catalog the final
   release pull request declares. **Pending.**

PASS needs every check. If a step fails, record the response in the release
task. Whether to roll back is Josh's decision.

## 5. Installed governed-change acceptance (separately authorized)

This stage writes, reloads and restarts. It runs only after section 4
passes. Each numbered step below is a live change that Josh authorizes
separately. Nothing here authorizes it. Run a step only for an item that has
landed.

**Before starting, Josh names:**
- a dedicated test automation (the test object);
- the add-on to restart in step 2.

Add both to the Deploy step 0 list, together with every plan and task that
acceptance creates.

**General rules for every step:**
- Josh approves each plan externally at its exact hash.
- Every expected result includes exactly one provider dispatch per applied
  plan (`get_execution_task`).
- Never re-apply a plan, redispatch, or create a second plan to retry a
  failed step. On a stop condition, record the plan, the task and the
  response in the release task, and stop the stage.
- Whether to keep each test change or revert it is Josh's decision. A revert
  is its own governed plan.

Run the steps in this order. Step 4 is the only Home Assistant restart in
acceptance.

### Step 1. Reload (HAMCP-401, I1)

1. `create_reload_plan` with `reload_target: "automation"`. Josh approves.
2. `apply_change_plan`, then `get_execution_task`.

**Expected:**
- the task is `succeeded_verified`, with exactly one dispatch;
- verification shows the configuration validation `valid` and the
  automation reload service available.

**Stop if:**
- apply is refused before dispatch (record the category, for example
  `provider_held`, `catalog_mismatch` or `required_tool_missing`);
- verification reports `configuration_validation` or `reload_service` in
  `mismatch_fields`;
- the dispatch count is not one.

### Step 2. Add-on restart (HAMCP-401, I1)

1. `create_addon_restart_plan` with `addon_slug` set to the exact slug of the
   add-on Josh named. Record the plan's target class. Josh approves.
2. `apply_change_plan`, then `get_execution_task` until the task is terminal.

**Expected:**
- the task is `succeeded_verified`, with exactly one dispatch;
- `operational.verification.evidence.restart_proof` matches the target
  class:

  | Target class | `restart_proof` |
  | --- | --- |
  | `engineering_addon` | `process_identity` |
  | `upstream_ha_mcp_addon` | `upstream_readmission` |
  | `other_addon` | `provider_acknowledgement` |

Restarting Engineering itself drops the client connection until it is back.
Restarting the ha-mcp add-on makes delegated reads unavailable until
readmission.

**Stop if:**
- the target class is not the one Josh expected for that add-on;
- the task ends `operational_verification_failed`, or reports
  `restart_evidence` in `mismatch_fields`;
- the proof grade does not match the table;
- the dispatch count is not one.

### Step 3. `initial_state` change (HAMCP-419, E052)

1. Read the test automation with `get_automation_config`.
2. Negative check: `create_change_plan` with `operation: "update_automation"`
   and the current configuration plus `initial_state: "false"` (a string).
   Expected: refused with `automation initial_state must be a boolean`, and
   no plan is created.
3. `create_change_plan` with the current configuration plus a JSON boolean
   `initial_state` (the opposite of the automation's current value if it has
   one, otherwise `true`). Josh approves.
4. `apply_change_plan`, then `get_execution_task`, then
   `get_automation_config`.

**Expected:**
- the task is `succeeded_verified`, with exactly one dispatch and an exact
  read-back;
- `get_automation_config` shows `initial_state` equal to the planned boolean,
  and every other field unchanged.

**Stop if:**
- the boolean plan is refused;
- the read-back differs in any field;
- the string value is accepted.

### Step 4. Home Assistant restart, with E043 (HAMCP-401 I1, HAMCP-415)

This restart serves both the I1 governed restart and the E043 check. The
restart must leave Core authority identical. Do not combine it with a Core,
HACS component or ha-mcp app update, or with an integration change. Any of
those changes the authority, and the E043 plan is then correctly voided.

1. **E043 test plan, before the restart.** `create_configuration_plan`
   updating the test automation's description. Josh approves it at its exact
   hash. Do not apply it.
2. **Governed restart.** `create_home_assistant_restart_plan`. Josh approves.
   `apply_change_plan`, then `get_execution_task` until the task is
   terminal.
3. **Wait for readmission.** `get_server_health` shows the Core readmission
   `recovery_state` as `restored`, with `last_restored_at` later than the
   restart dispatch.
4. **E043 apply, after the restart.** Apply the step 1 plan with
   `apply_change_plan`, then `get_execution_task`.

**Expected for the restart (I1):**
- the task is `succeeded_verified`, with exactly one `ha_restart` dispatch;
- the verification evidence shows `restart_dispatch_confirmed: true`,
  `authoritative_outage_observed: true` and
  `home_assistant_reconnected: true`.

**Expected for E043:**
- the step 4 task is `succeeded_verified`, with exactly one dispatch and an
  exact read-back;
- the Engineering add-on log has one `core_authority_restored` record for
  this restart, with `reason: core_authority_identical` and
  `plans_preserved: true`.

**Stop if:**
- the restart plan is refused while the E043 plan is approved and pending;
- the restart task ends `restart_evidence_window_expired` or
  `operational_verification_failed`, or stays
  `operational_verification_pending` after Core is back and readmitted;
- the E043 plan is refused as stale or voided;
- the record shows `core_authority_changed` (with `changed_components`) or
  `core_plan_window_capacity`;
- any dispatch count is not one.

Do not restart Home Assistant again to retry, and do not retry E043 on
another object.

### Step 5. Rollback replanning (HAMCP-416, E044)

This step uses the change applied in step 4. Calling `rollback_change`
creates a plan and does not write to Home Assistant.

1. `rollback_change` with the step 4 plan ID. Expected:
   - `status: rollback_plan_created`;
   - a `rollback_plan_id` (R1), with `approval_required`.
2. Call `rollback_change` again for the same plan. Expected:
   - `rollback_plan_resolution: existing_live_plan_returned`;
   - the same R1.
3. Josh rejects R1 on the external approval page.
4. `rollback_change` a third time. Expected:
   - `rollback_plan_resolution: fresh_plan_created`;
   - a new `rollback_plan_id` (R2), different from R1;
   - `replaced_rollback_plan_id` equal to R1;
   - `replaced_rollback_plan_status: rejected`.

**Stop if:**
- step 2 creates a new plan;
- step 4 returns R1, or `existing_live_plan_returned`, or any refusal.

Approving and applying R2 reverts the step 4 description change. That is
Josh's decision; otherwise R2 expires unapplied.

PASS for the stage needs every expected value in every step that ran.

## Deploy (Engineering 2.4.2-beta.10 with the ha-mcp 9.2.0 pair)

Deploying is separately authorized by Josh. Nothing here authorizes it. Each
numbered step that changes the house is a live change that Josh authorizes.

`upstream_dashboard_mcp_url` contains the ha-mcp secret path. Read and edit
it only in the Engineering add-on configuration. Never paste it into a chat,
a task, a log or a document. Engineering redacts it from every response.

0. **Prepare.**
   - Export the beta.9 audit (`get_audit_log`).
   - Take a FULL Home Assistant backup in the UI.
   - List every object acceptance creates as it is created.
   - Record, without copying it anywhere, whether `upstream_dashboard_mcp_url`
     ends with `/catalog/full`. Note the ha-mcp version that
     `get_server_health` detects.
1. **Deploy Engineering 2.4.2-beta.10.**
2. **If ha-mcp still runs 8.5.0.** Confirm beta.10 works on 8.5.0 before
   going on: health shows `reviewed_exact` and `admitted_exact`, with no
   `upstream_redirect_refused`. Then:
   1. take a partial backup of the ha-mcp add-on plus the Home Assistant
      configuration folder;
   2. update the ha-mcp add-on to 9.2.0, then the HACS `ha_mcp_tools`
      component to 9.2.0, then restart Core. Server and component deploy as
      a pair; never run one without the other. Confirm the image digest as
      in [beta.9 Deploy step 6](V2_4_2_BETA9_ACCEPTANCE.md).

   If ha-mcp already runs the 9.2.0 pair, skip this step.
3. **Set `upstream_dashboard_mcp_url` for 9.x.** If the value does not yet
   end with `/catalog/full`, append exactly `/catalog/full` to the existing
   value: no trailing slash, encoding or case change. Restart Engineering.
   Engineering never repairs or canonicalizes the value. Without the suffix,
   Engineering refuses the 9.x server after initialize and before any
   `tools/list` (`upstream_catalog_mode_unadmitted`, or
   `upstream_catalog_selector_invalid` for a misspelled selector), and every
   delegated read is unavailable. This happened after the beta.9 upgrade.
4. **Confirm 9.2.0 admission.**
   - `get_server_health` shows `upstream_dashboard.upstream_server_version`
     `9.2.0`, reachable, and the read gateway `admitted_exact`.
   - None of `upstream_catalog_mode_unadmitted`,
     `upstream_catalog_selector_invalid` or `upstream_redirect_refused` is
     present.
   - Confirm 9.2.0 from Engineering's detected upstream version, not the
     store label.
5. **Acceptance:** section 4, then, if Josh authorizes it, section 5.

## Rollback

**ha-mcp pair (9.2.0 back to 8.5.0).** The pair rollback also restores the
URL.
1. Restore the Deploy step 2 add-on backup, redownload component 2.2.1 in
   HACS, and restart Core.
2. Restore `upstream_dashboard_mcp_url` to the value recorded before the 9.x
   change by removing the `/catalog/full` suffix, then restart Engineering.
   - With the suffix left in place, 8.5.0 serves no selector route. It
     answers 404 at initialize, and the delegated surface stays
     unavailable (fail-closed) until the suffix is removed
     ([`HAMCP_319_PHASE2_DESIGN.md`](HAMCP_319_PHASE2_DESIGN.md), the
     rollback row of the URL-form table).
3. Restore the configuration folder only if the HACS redownload fails. It
   reverts every configuration change made since the backup.

If the house was already on the 9.2.0 pair before this deploy (Deploy step
2 skipped), there is no pair rollback for this release. Leave the URL
unchanged.

**Engineering.** **Pending** until every item has landed. The final release
pull request states the rollback target and whether rolling back is safe,
from the storage, provider and option changes that actually landed. Export
`get_audit_log` before any restore. HAMCP-415 and HAMCP-416 change no
persisted format.
