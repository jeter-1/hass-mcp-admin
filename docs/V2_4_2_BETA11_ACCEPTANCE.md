# Engineering 2.4.2-beta.11 acceptance

**Status: in progress.** This contract covers the 2.9.0 build, released as
2.4.2-beta.11. It follows 2.4.2-beta.10 (HAMCP-401, #452). Its content lands
through item pull requests into the `release/2.9.0` integration branch. The
final release pull request completes every section marked **pending** before
it is offered for review.

See also the release notes:
[`V2_4_2_BETA11_RELEASE_NOTES.md`](V2_4_2_BETA11_RELEASE_NOTES.md).

**Release target.** `2.4.2-beta.11`, following 2.4.2-beta.10. The architect's
task of 2026-11-18 08:2x CST names this version for the 2.9.0 release path,
and the value is authorized on that path only. "2.9.0" names the build, not
the version string.

**Why the version is materialized now.** `main` carries 2.4.2-beta.10 (#452),
and CI validates every pull request against `origin/main` with a
materialized, unstaged release whatever its base
(`scripts/validate_addon_metadata.py`, `.github/workflows/ci.yml`). So
2.4.2-beta.11 is materialized once on `release/2.9.0`, at its base (`main` at
6449c7b3), and item pull requests carry no version change of their own.

The release is materialized in all three version authorities, with no staged
declaration left. Nothing is published or deployed.

The contract has five stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed read-only acceptance;
5. installed governed-change acceptance (reload, add-on restart, E043 and
   the created identity with one Home Assistant restart).

It authorizes none of the later stages.

## Items

Each item is reviewed in its own pull request into `release/2.9.0`. Every
item has landed; each is listed by its pull request, with its merged head and
merge commit. Section 1 records each review. Times are CST.

| Item | Scope | Landed |
| --- | --- | --- |
| HAMCP-436 (E022) | dispatch-scoped catalog admission for lifecycle and backup: exact contracts for the dispatched tools only, other descriptor drift recorded in health | #453, head 76bb7efc, merged as 1069f18e |
| HAMCP-437 I2a-D | the reviewed ha-mcp registry keeps 9.2.0 only; older releases are refused as unknown | #479, head 2d01a7bf, merged as 6200efbe |
| HAMCP-464 | upstream warnings on five 9.2.0 reads are replaced by a fixed code, counted, and the read reports `partial` | #477, head 901482d1, merged as d7e57954 |
| HAMCP-439 (A1) | full-detail `ha_get_overview` rows keep `entity_id` on 9.2.0 | #463, head 41659769, merged as d4313049 |
| HAMCP-441 (A2) | `ha_search` `result_fields` keys admitted on 9.2.0 (Row 25) | #464, head 695cb53b, merged as 42c4ddc7 |
| HAMCP-437 I2a-T | test suite moved onto ha-mcp 9.2.0 | #459, head 702d58ff, merged as e40c4e38 |
| HAMCP-437 I2b-A0 | CI and test trust root moved to Core registry sequence 18 | #462, head c91c3550, merged as 40938159 |
| HAMCP-437 I2b (PR A), with HAMCP-405 and HAMCP-406 | one-slot CI window on Core 2026.11.0 with ha-mcp 9.2.0; legacy exact-image and add-on lanes retired | #461, head a15de251, merged as 90473757 |
| HAMCP-437 I2b (1/3) | three unrequired workflows and their tooling retired | #460, head 110e79d7, merged as ae1ca5c5 |
| HAMCP-437 I2b (2/3) | pre-window Core bindings, fixtures and the sequence-15 CI steps retired | #465, head 46790572, merged as 9af0d7dc |
| HAMCP-437 I2c-1 | retired files that no workflow calls deleted | #466, head 105d6627, merged as b7396e6d |
| HAMCP-437 I2c-2 (A, B, C) | split test modules, fixtures and the last release-doc readers moved onto 9.2.0 | #474, head 1cb09ac8, merged as e877de5c; #475, head 3a593c92, merged as ff0f4b41; #476, head 8504c01a, merged as 0f1adcb5 |
| HAMCP-437 I2d | historical docs deleted under the keep rule; the sequence-18 signing recorded | #467, head 3dccf47a, merged as ad9881be; follow-up #471, head 921e968a, merged as 04d6aad0 |
| HAMCP-437 I2f-1 (HAMCP-365, 241, 286) | Evidence reuse of CI unit-shard results; a test-order pin | #468, head 12975344, merged as fc005cc0 |
| HAMCP-437 I2f-2 (HAMCP-363) | one loopback-and-route check for every harness send; an offline unit suite | #473, head 3eeb2778, merged as 310c6a0e |
| HAMCP-443 (A, B, C) | Core evidence lanes: shared lane host, CI-mode lane groups, and the lane-groups workflow job proving all 19 groups | #469, head 7bbf290e, merged as d24f0ec3; #472, head da4809a8, merged as b2e5759e; #478, head 3739b2a7, merged as 7c9b8ee0 |

Catch-up merges with `main` (#457, 4ba1e083; #470, f44a147d) carry no item of
their own.

## 1. Source and offline acceptance

Scope and authority come from the architect's 2.9.0 plan
(`release-2.9-rev4.md` and its rev5 steps) and the architect rulings cited
in each pull request. Josh merged each pull request into `release/2.9.0`
between 2026-11-18 and 2026-11-22. Each pull request's body carries its
validation tier, its Security section where one applies, and its success,
refusal and negative-reachability tests. Every item pull request ran the
Fast checks and CI, under the `release/*` exception in `AGENTS.md`.

Two reviewers are used, one per pull request for its whole life:
- **Cursor** (hardened review unit, Grok 4.7, non-Claude): independent
  review. The copies are kept under `~/handoffs/architect/reviews/`.
- **The architect**: reviewed in the architect chat (no GitHub review object)
  and accepted before the merge. Claude self-family review, not independent.
  The plan sets it for I2c-1, I2d, I2f and I6a.

| PR | Reviewer and final round | CI on the merged head |
| --- | --- | --- |
| #453 | Cursor r2 APPROVE at 76bb7efc | 44347813057 success |
| #459 | Cursor r2 APPROVE at 702d58ff | 44517571695 success |
| #462 | Cursor r1 READY_WITH_FOLLOW_UP at c91c3550 | 44629275598 success |
| #463 | Cursor r1 READY at 1f9e7ef0 (see below) | 44738190395 success |
| #464 | Cursor r1 READY at 695cb53b | 44759395774 success |
| #461 | Cursor r2 READY at 3658ecdf (see below) | 44777879399 success |
| #460 | Cursor r2 READY at 110e79d7 | 44805138597 success |
| #465 | Cursor r2 READY at 46790572 | 44825558324 success |
| #466 | architect | 44850727799 success |
| #467 | architect | 44867792491 success |
| #468 | architect | 44910235906 success |
| #471 | architect | 44913567235 success |
| #469 | Cursor r2 CHANGES_REQUIRED at 4b8a4d62 (see below) | 44918750957 success |
| #472 | Cursor r2 READY at da4809a8 | 44974788968 success |
| #474 | Cursor r1 READY at 1cb09ac8 | 44991786225 success |
| #475 | Cursor r1 READY_WITH_FOLLOW_UP at 3a593c92 | 45005412002 success |
| #473 | Cursor r2 CHANGES_REQUIRED at 02d19e11 (see below) | 45024148176 success |
| #476 | Cursor r1 READY at 8504c01a | 45028030453 success |
| #477 | Cursor r2 READY at 901482d1 | 45033449288 success |
| #478 | Cursor r2 READY at 3739b2a7 | 45053917606 success |
| #479 | Cursor r2 READY at 2d01a7bf | 45087231676 success |

**Merged heads that differ from the last reviewed head.** These are the facts
from each pull request and its commits, with no further claim.
- **#463.** The merged head 41659769 is the architect's refresh of the P4
  parity hash line, after CI 44678628905 failed
  `test_hamcp371_p4_parity`. The pull request states that the Cursor r1
  READY stands and that the architect ruled no rereview was needed for the
  regenerated hash line.
- **#461.** The merged head a15de251 holds catch-up merges with
  `release/2.9.0` and one commit after the reviewed head, e5d2f4dd: one
  message-only line in `scripts/core_evidence_workflow.py` that drops the
  stale profile count from the acceptance refusal (round-5 ruling Q3, per the
  commit message). The pull request body does not mention it.
- **#469.** The merged head 7bbf290e is the tests-only correction batch for
  the r2 finding R2-01, which Josh approved and the architect verified.
- **#473.** After r2, 1a4180f9 is the tests-only correction batch for R2-1
  and R2-2, which Josh approved. Then 162fc23b is a catch-up merge, and
  3eeb2778 is a fix under architect ruling 20261122-0512-01, approved by
  Josh. A post-merge Cursor check of 3eeb2778 returned READY_WITH_FOLLOW_UP,
  with one Low.

**Runtime changes.** 25 files under
`hass_mcp_engineering_beta/ha_mcp_engineering/` changed between the version
commit a25de4ce and 6200efbe (+247, −2,136). They come from #453, #463,
#464, #467 (the sequence-18 record), #477 and #479. #453, #463, #471, #477
and #479 each state no public schema or tool registration change. #461, #465,
#466, #468, #469, #473, #474, #475 and #478 each state no runtime change. No
landed item states a persisted-format change.

**Release head.**
- `2.4.2-beta.11` is materialized at a25de4ce, the base of `release/2.9.0`,
  in `config.yaml` and `version.py`. `scripts/codex-context.py` resolves this
  document as the active acceptance document, with resolution `exact`.
- The release-docs pull request (I6a, HAMCP-469) runs the Fast checks and CI
  on its own head. Its reviewer is the architect only (not independent).
- **Evidence tier: pending.** It runs once, on the final release head, after
  Josh's go. The release pull request names that head, its `validate` run
  and the Evidence log.

## 2. Release CI

The exact materialized final head's `validate` check passes, including the
release transition. **Pending.**

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow. **Pending.**

## 4. Installed read-only acceptance (separately authorized, after deploy)

This stage runs only after Josh separately authorizes deploying this
release. Every step is a read. Josh runs it through HA Admin. Stages 4 and 5
run in one sitting, and Josh approves each step.

1. **Identity.** `server_info` and `get_server_health` report:
   - `2.4.2-beta.11`;
   - the merged release SHA as build SHA;
   - `dirty: false`.
2. **Lifecycle health and the admitted ha-mcp set (HAMCP-436, HAMCP-400,
   #479).** In `get_server_health`, under
   `operational_administration.lifecycle_provider`
   (`service.py`, `tests/test_governance_health_cache.py`), expected:
   - `operational_status` is `available` (the lifecycle provider is ready),
     with provider `upstream_operational_lifecycle`;
   - `selected_compatibility_entry_id` is `ha-mcp-v9.2.0-9de5bf6c`
     (HAMCP-400), and `observed_upstream_version` is `9.2.0`;
   - `catalog_admission`:
     - `model` is `reviewed-dispatch-scoped-catalog-v1`;
     - `status` is `accepted_unused_tool_drift`, and `admitted` is `true`;
     - `exact_tool_mismatches` is `[]`;
     - `exact_tools` includes `ha_manage_app` and `ha_get_app`;
     - `unused_tool_drift_count` is `1`, and `unused_tool_drift` lists
       `ha_config_set_helper` with components `input_schema_fingerprint` and
       `runtime_contract_fingerprint`. The drift is recorded, not rejected.
   - `catalog_validation.validation_status` stays
     `rejected_catalog_mismatch`. That is expected: the whole-catalog
     comparison is still recorded; admission is decided by
     `catalog_admission` (`tests/test_hamcp436_dispatch_scoped_catalog.py`).
   - `fallback_count` is `0`, and `fallback_policy` is `none`.
   - The backup provider stays `provider_held` on 9.2.0 (HAMCP-381).
   - The admitted ha-mcp set is 9.2.0 only (#479). Health names no other
     reviewed release.

   Stop and record the health response if the lifecycle provider is not
   `available`, the entry id is missing or different, `catalog_admission`
   is not admitted, or any drift appears on a dispatched tool.
3. **Overview `entity_id` re-check on 9.2.0 (HAMCP-439, #463).** Expected
   results are from `tests/test_hamcp439_overview_entity_id.py`.
   1. `ha_get_overview` with `detail_level: "full"`. Expected:
      - each row under `data.domain_stats.<domain>.entities[]` carries
        `entity_id`, with `friendly_name` and `state`, and `name` when it
        differs from `friendly_name`;
      - keeping `entity_id` adds nothing to `fields_withheld`, and no
        `COLLECTION_WITHHELD` note appears;
      - top-level `device_types` and `service_availability` are not in
        `data`.
   2. `ha_get_overview` at the standard detail level. Expected: rows carry
      `friendly_name` (and `name` when it differs), no `entity_id`; the
      shape is unchanged from beta.10.

   The defect being re-checked: before the fix, full-detail rows lost
   `entity_id` silently, with `fields_withheld` raised by one and no note.
   If upstream returns a warning on this read, the read reports `partial`
   (see step 5). That is expected and is not a failure of this check. Stop
   and record the response if a full-detail row has no `entity_id`.
4. **Search `result_fields` re-check on 9.2.0 (HAMCP-441, #464).** Expected
   results are from `tests/test_hamcp441_search_result_fields.py`.
   1. `ha_search` with `result_fields` set to `entity_id`, `friendly_name`,
      `domain`, `state`, `score`, `match_type`, `area`, `floor`, `labels`,
      `aliases`, `is_group` and `member_entity_ids`. Expected:
      - each record in `data.entities` arrives whole: `area` and `floor` are
        names or `null`, `labels` and `aliases` are lists, `is_group` is a
        boolean, and `member_entity_ids` appears for groups;
      - `fields_withheld` does not grow for these keys, and no
        `COLLECTION_WITHHELD` note appears.
   2. `ha_search` with `result_fields: ["entity_id", "area"]`. Expected:
      records hold exactly `entity_id` and `area`, and `fields_withheld` is
      unchanged.
   3. `ha_search` with `result_fields` naming one key that does not exist.
      Expected:
      - upstream's own warning text (it lists "Available keys") never
        reaches the client: no "Available keys" text in the response;
      - `data.warnings` contains `upstream_warning_replaced`, and
        `upstream_warnings_replaced` goes up by one.

   Stop and record the response if a requested key is withheld, or if
   upstream's warning text appears.
5. **Replaced upstream warnings (HAMCP-464, #477).** This is a
   client-visible change. On `ha_get_overview`, `ha_get_state` (single and
   bulk), `ha_get_todo`, `ha_get_zone` and `ha_list_floors_areas`, an
   upstream warning is never passed through. It is replaced by
   `upstream_warning_replaced`, counted in `upstream_warnings_replaced`, and
   the read reports `partial`. The notes are `UPSTREAM_WARNINGS` and
   `REPLACED_PARTIAL` (for the overview also `VISIBLE_SCOPE` and
   `UPSTREAM_PARTIAL`). Record whether any of these reads reports a
   replaced warning during this sitting; a replaced warning is not a
   failure.
6. **Tool catalog.** Take a fresh `tools/list` and `get_server_health`.
   - **Expected live:** Engineering 86, delegated 28, total 114. This is the
     beta.9 live baseline (architect `get_server_health`, 2026-11-16 22:1x
     CST, with `ha_config_get_yaml` admitted). No landed item states a tool
     registration change.
   - Record the live advertised and reviewed upstream counts as observed.
   - Stop and record `tools/list` if Engineering is not 86 or delegated is
     not 28, unless the house's filesystem flag has changed since beta.9. In
     that case, record which way it changed.
   - **CI reference (lane value):** delegated 27, catalog fingerprint
     `0f1a2c75...`, from run 45087231676, as listed in the release notes
     under "Tool catalog". It is not the live expectation. The cause of 27
     in CI against 28 live is not established. CI's strict-admission case
     records 28 dynamically exposed tools.

PASS needs every check. If a step fails, record the response in the release
task. Whether to roll back is Josh's decision.

## 5. Installed governed-change acceptance (separately authorized)

This stage reloads, restarts and writes. It runs only after section 4
passes. Each numbered step below is a live change that Josh approves
separately. Nothing here authorizes it.

**Before starting, Josh names:**
- the non-critical add-on to restart in step 2;
- the test automation that step 3 creates (its id and alias).

Add both to the Deploy step 0 list, together with every plan and task that
acceptance creates.

**General rules for every step:**
- Josh approves each plan externally at its exact hash.
- Every expected result includes exactly one provider dispatch per applied
  plan (`get_execution_task`).
- Never re-apply a plan, redispatch, or create a second plan to retry a
  failed step. On a stop condition, record the plan, the task and the
  response in the release task, and stop the stage.

Run the steps in this order. Step 3 holds the only Home Assistant restart in
acceptance.

### Step 1. Reload automations (HAMCP-436, HAMCP-401, HAMCP-151)

1. `create_reload_plan` with `reload_target: "automation"`. Josh approves.
2. `apply_change_plan`, then `get_execution_task`.

**Expected:**
- the task is `succeeded_verified`, with exactly one dispatch;
- verification shows the configuration validation `valid` and the
  automation reload service available.

**Stop if:**
- apply is refused before dispatch (record the category, for example
  `provider_held`, `catalog_mismatch`, `required_tool_missing` or
  `upstream_version_mismatch`);
- verification reports `configuration_validation` or `reload_service` in
  `mismatch_fields`;
- the dispatch count is not one.

### Step 2. Restart one non-critical add-on (HAMCP-250)

1. `create_addon_restart_plan` with `addon_slug` set to the exact slug of the
   add-on Josh named. Record the plan's target class. Josh approves.
2. `apply_change_plan`, then `get_execution_task` until the task is terminal.

**Expected:**
- the task is `succeeded_verified`, with exactly one dispatch of
  `ha_manage_app`;
- `operational.verification.evidence.restart_proof` matches the target
  class:

  | Target class | `restart_proof` |
  | --- | --- |
  | `engineering_addon` | `process_identity` |
  | `upstream_ha_mcp_addon` | `upstream_readmission` |
  | `other_addon` | `provider_acknowledgement` |

A non-critical add-on is normally `other_addon`. Restarting Engineering
itself drops the client connection until it is back; restarting the ha-mcp
add-on makes delegated reads unavailable until readmission.

**Stop if:**
- the target class is not the one Josh expected for that add-on;
- the task ends `operational_verification_failed`, or reports
  `restart_evidence` in `mismatch_fields`;
- the proof grade does not match the table;
- the dispatch count is not one.

### Step 3. E043 and the created identity, with one Home Assistant restart (HAMCP-415, 392, 417, 393)

This restart serves both the governed restart and the E043 check. The restart
must leave Core authority identical. Do not combine it with a Core, HACS
component or ha-mcp app update, or with an integration change. Any of those
changes the authority, and the E043 plan is then correctly voided.

1. **Governed create, before the restart.** `create_configuration_plan` with
   one operation: `resource_type: "automation"`, `action: "create"`, the
   test automation's `target_id` and a minimal `proposed_config` (an alias
   and description that name the release, a trigger that will not fire on
   its own, and no action that touches a device). Josh approves it at its
   exact hash. Do not apply it.
2. **Governed restart.** `create_home_assistant_restart_plan`. Josh approves.
   `apply_change_plan`, then `get_execution_task` until the task is
   terminal.
3. **Wait for readmission.** `get_server_health` shows the Core readmission
   `recovery_state` as `restored`, with `last_restored_at` later than the
   restart dispatch.
4. **Apply the create, after the restart.** Apply the step 1 plan with
   `apply_change_plan`, then `get_execution_task`, then `get_change_plan`.

**Expected for the restart:**
- the task is `succeeded_verified`, with exactly one `ha_restart` dispatch;
- the verification evidence shows `restart_dispatch_confirmed: true`,
  `authoritative_outage_observed: true` and
  `home_assistant_reconnected: true`.

**Expected for E043 (HAMCP-415, HAMCP-392):**
- the create task is `succeeded_verified`, with exactly one dispatch and an
  exact read-back;
- the Engineering add-on log has one `core_authority_restored` record for
  this restart, with `reason: core_authority_identical` and
  `plans_preserved: true`.

**Expected for the created identity (HAMCP-417, HAMCP-393):**
- `get_execution_task` shows, for the create child,
  `created_identity_capture` as captured and a `created_identity` with
  `resource_type`, `resource_target`, `storage_object_id`, `entity_id`,
  `registry_entry_id` and `registry_unique_id`;
- `get_change_plan` lists the same identity under
  `execution_task.created_identities`.

**Stop if:**
- the restart plan is refused while the create plan is approved and
  pending;
- the restart task ends `restart_evidence_window_expired` or
  `operational_verification_failed`, or stays
  `operational_verification_pending` after Core is back and readmitted;
- the create plan is refused as stale or voided;
- the record shows `core_authority_changed` (with `changed_components`) or
  `core_plan_window_capacity`;
- the capture is not captured (for example
  `registry_entry_not_observed_within_bound`, `identity_mismatch`,
  `capability_unadmitted` or `read_failed`);
- any dispatch count is not one.

Do not restart Home Assistant again to retry, and do not retry E043 on
another object.

### Step 4. Clean up through rollback

1. `rollback_change` with the step 3 create plan ID. Expected:
   `status: rollback_plan_created`, with a `rollback_plan_id` and
   `approval_required`.
2. Josh approves the rollback plan. `apply_change_plan`, then
   `get_execution_task`.

**Expected:** the rollback task is `succeeded_verified`, with exactly one
dispatch, and the test automation no longer exists
(`get_automation_config` finds nothing).

**Stop if** `rollback_change` is refused (for example
`created_object_identity_not_captured`), and record the response. Removing
the test automation is then Josh's decision, through
`create_configuration_delete_plan`.

PASS for the stage needs every expected value in every step that ran.

## Deploy (Engineering 2.4.2-beta.11 on the ha-mcp 9.2.0 pair)

Deploying is separately authorized by Josh. Nothing here authorizes it. Each
numbered step that changes the house is a live change that Josh authorizes.

**What changes for the operator.**
- **Engineering admits ha-mcp 9.2.0 only** (#479). Every other ha-mcp
  release is refused as an unknown version, before dispatch: lifecycle
  reports `operational_contract_mismatch` and backup
  `backup_provider_unavailable`, with the internal reason
  `upstream_version_mismatch`. No new refusal path is added. **Keep the
  ha-mcp add-on's auto-update off**, so the house stays on 9.2.0 until a
  later release reviews another version.
- **Backup on 9.2.0 stays held** (`provider_held`, HAMCP-381).
- The house already runs the 9.2.0 pair (the ha-mcp 9.2.0 add-on and HACS
  `ha_mcp_tools` 9.2.0, entry `ha-mcp-v9.2.0-9de5bf6c`) and an
  `upstream_dashboard_mcp_url` ending with `/catalog/full`. Neither changes
  here.

`upstream_dashboard_mcp_url` contains the ha-mcp secret path. Never paste it
into a chat, a task, a log or a document. Engineering redacts it from every
response.

0. **Prepare.**
   - Export the beta.10 audit (`get_audit_log`).
   - Take a FULL Home Assistant backup in the UI.
   - Take a Proxmox snapshot of VM 400 (homeassistant; not VM 300, which is
     scriptbox), named for the release. The host admin takes it, with Josh's
     approval.
   - Confirm that the ha-mcp add-on runs 9.2.0, from Engineering's detected
     upstream version, not the store label, and that its auto-update is off.
   - List every object acceptance creates as it is created.
1. **Deploy Engineering 2.4.2-beta.11.**
2. **Confirm 9.2.0 admission.** `get_server_health` shows
   `upstream_dashboard.upstream_server_version` `9.2.0`, reachable, and the
   read gateway `admitted_exact`. If it shows any other version, stop: that
   release is refused (see above).
3. **Acceptance:** section 4, then, if Josh authorizes it, section 5.

## Rollback

There is no in-place rollback. Recovery is restoring the pre-deploy full Home
Assistant backup, or the VM 400 snapshot, both taken in Deploy step 0. That
returns Engineering 2.4.2-beta.10 with ha-mcp 9.2.0.
- Target: tag `v2.4.2-beta.10`, commit
  6449c7b385bbef0ede399ef3b3a334dfd26fea76, image
  `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.4.2-beta.10`, OCI digest
  `sha256:527afb3a346982624090cecdf1e22ceefe445ed0e33f6e838a0d1e0c9dbcc2a3`.
- Export `get_audit_log` before any restore.
- In-place downgrade from beta.11 to beta.10 is not supported and is
  unproven.
- CI no longer runs an in-place rollback lane: #461 retired it (HAMCP-405)
  and allows an empty rollback slot (HAMCP-406, restore-first recovery).
- No landed item states a persisted-format change. That is not a downgrade
  proof.
- **Safety.** Restoring the backup or snapshot returns Engineering, its
  storage and Home Assistant together to the backed-up state. It also
  returns every Home Assistant change made since it was taken. Whether to
  restore, and which, is Josh's decision.
