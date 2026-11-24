# Engineering 2.4.2-beta.12 acceptance

**Status: in progress.** This contract covers the 2.9.0.1 hotfix, released as
2.4.2-beta.12. It follows 2.4.2-beta.11 (2.9.0, #481). Its content lands
through item pull requests into the `release/2.9.0.1` integration branch.
Sections 1, 4 and 5, Deploy and Rollback are complete on this branch, so the
Evidence tier runs on a head that nothing changes afterwards. What stays
**pending** is recorded only after release: the Evidence tier result
(section 1), release CI (section 2) and publication (section 3). A small docs
pull request to `main` records them, as #482 did for 2.4.2-beta.11.

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

Scope and authority come from the architect's block of 2026-11-23 (HAMCP-470
hotfix). Josh merges each pull request into `release/2.9.0.1`. Each pull
request's body carries its validation tier and, for #483, its Security
section and its success, refusal and negative-reachability tests. Every pull
request ran the Fast checks and CI, under the `release/*` exception in
`AGENTS.md`.

Reviewers, one set per pull request for its whole life:
- **Cursor** (hardened review unit, Grok 4.7, non-Claude): the recorded
  independent reviewer for #483.
- **Codex** (`gpt-6-astra`, high, read-only sandbox, non-Claude): the
  security review for #483.
- **The architect**: reviewed in the architect chat (no GitHub review
  object). Claude self-family review, not independent. It is the only
  reviewer of the docs pull requests.

The review copies are kept under `~/handoffs/architect/reviews/`.

| PR | Scope | Reviewer and final round | CI on the merged head |
| --- | --- | --- | --- |
| #483 | HAMCP-470 runtime fix and tests | Cursor r1 READY at e2a96b3c; Codex r1 CHANGES_REQUIRED at e2a96b3c, r2 at e044b9f7 (see below) | 45282990469 success |
| #484 | release notes Items and Current state; acceptance sections 4 and 5 | architect, at f35fe3e9 | 45288986587 success |

The final docs pull request (tool catalog, Deploy and Rollback) is bound
here after release, with the Evidence tier result.

**Merged head that differs from the last Cursor-reviewed head.**
- **#483.** The merged head e044b9f7 is the correction batch for Codex r1's
  two Medium findings, SEC-483-01 (the renamed-key diagnostic path) and
  SEC-483-02 (exponent overflow). Cursor made no delta review, per the
  architect's batch block. Codex r2 found both resolved, with no new
  finding; its verdict was BLOCKED only because its read-only sandbox could
  not run tests or mutations. The architect closed that gap with its own
  run in a separate clone: 50 tests OK; mutations M18, M19 and M20 each
  fail, and the restored tree is OK.

**Runtime changes.** Between `main` at 23c5c429 and `release/2.9.0.1` at
71520cba, five files under `hass_mcp_engineering_beta/` changed (+296, −25):
- the version commit ed2560b7: `config.yaml` (version), `version.py` and
  `CHANGELOG.md`;
- #483: `providers/operational_lifecycle.py` and `f3_runtime/runtime.py`.

#483 states no public input schema, tool registration or persisted-format
change, and no new write, dispatch path or fallback.

**Release head.**
- `2.4.2-beta.12` is materialized at ed2560b7, the base of
  `release/2.9.0.1`, in `config.yaml` and `version.py`.
  `scripts/codex-context.py` resolves this document as the active acceptance
  document, with resolution `exact`.
- The docs pull requests run the Fast checks and CI on their own heads.
  Their reviewer is the architect only (not independent).
- **Evidence tier: pending.** It runs once, on the final release head, after
  Josh's go. Nothing on this branch changes after it. Its result is recorded
  after release by the docs pull request to `main`.

## 2. Release CI

The exact materialized final head's `validate` check passes, including the
release transition. **Pending.** Recorded after release by a small docs pull
request to `main`, as #482 did for 2.4.2-beta.11.

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow. **Pending.** Recorded after release by the same docs
pull request to `main`.

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
3. **Read sanity check.** Josh decided on a short read check on beta.12,
   not the full beta.11 set (relayed by the architect, 2026-11-23). This
   release changes no read-gateway path. The expected values are from
   `tests/test_hamcp439_overview_entity_id.py` and
   `tests/test_hamcp441_search_result_fields.py`.
   1. One `ha_get_overview` with `detail_level: "full"`. Expected:
      - each row under `data.domain_stats.<domain>.entities[]` carries
        `entity_id`, with `friendly_name` and `state`, and `name` when it
        differs from `friendly_name`;
      - keeping `entity_id` adds nothing to `fields_withheld`, and no
        `COLLECTION_WITHHELD` note appears.

      If upstream returns a warning on this read, the warning is replaced
      by `upstream_warning_replaced` and the read reports `partial`. That
      is expected and is not a failure of this check.
   2. One `ha_search` with `result_fields` set to `entity_id`,
      `friendly_name`, `domain`, `state`, `score`, `match_type`, `area`,
      `floor`, `labels`, `aliases`, `is_group` and `member_entity_ids`.
      Expected:
      - each record in `data.entities` arrives whole: `area` and `floor`
        are names or `null`, `labels` and `aliases` are lists, `is_group`
        is a boolean, and `member_entity_ids` appears for groups;
      - `fields_withheld` does not grow for these keys, and no
        `COLLECTION_WITHHELD` note appears;
      - no upstream warning or validation text reaches the client.

   Stop and record the response if a full-detail row has no `entity_id`, if
   a requested `result_fields` key is withheld, or if upstream warning or
   validation text appears.

   Not re-run on beta.12 (beta.11 section 4, as corrected on `main` by
   HAMCP-469): the standard-detail overview (step 3.2), the other
   `result_fields` reads, with two keys and with an unknown key (steps 4.2
   and 4.3), the replaced-warning survey (step 5) and the live tool catalog
   (step 6).

PASS needs every check. If a step fails, record the response in the release
task. Whether to roll back is Josh's decision.

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

## Deploy (Engineering 2.4.2-beta.12 on the ha-mcp 9.2.0 pair)

Deploying is separately authorized by Josh. Nothing here authorizes it. Each
numbered step that changes the house is a live change that Josh authorizes.

**What changes for the operator.** Nothing in the ha-mcp pair or the
admitted set changes from 2.4.2-beta.11:
- **Engineering admits ha-mcp 9.2.0 only**, as on beta.11 (#479). Every
  other ha-mcp release is refused as an unknown version, before dispatch.
  **Keep the ha-mcp add-on's auto-update off**, so the house stays on 9.2.0.
- **Backup on 9.2.0 stays held** (`provider_held`, HAMCP-381).
- The house runs the 9.2.0 pair (the ha-mcp 9.2.0 add-on and HACS
  `ha_mcp_tools` 9.2.0, entry `ha-mcp-v9.2.0-9de5bf6c`) and an
  `upstream_dashboard_mcp_url` ending with `/catalog/full`. Neither changes
  here.

`upstream_dashboard_mcp_url` contains the ha-mcp secret path. Never paste it
into a chat, a task, a log or a document. Engineering redacts it from every
response.

0. **Prepare.**
   - Export the beta.11 audit (`get_audit_log`).
   - Take a FULL Home Assistant backup in the UI.
   - Take a Proxmox snapshot of VM 400 (homeassistant; not VM 300, which is
     scriptbox), named for the release. The host admin takes it, with Josh's
     approval.
   - Confirm that the ha-mcp add-on runs 9.2.0, from Engineering's detected
     upstream version, not the store label, and that its auto-update is off.
   - List every object acceptance creates as it is created.
1. **Deploy Engineering 2.4.2-beta.12.**
2. **Confirm 9.2.0 admission.** `get_server_health` shows
   `upstream_dashboard.upstream_server_version` `9.2.0`, reachable, and the
   read gateway `admitted_exact`. If it shows any other version, stop: that
   release is refused (see above).
3. **Acceptance:** section 4, then, if Josh authorizes it, section 5.

## Rollback

There is no in-place rollback. Recovery is restoring the pre-deploy full Home
Assistant backup, or the VM 400 snapshot, both taken in Deploy step 0. That
returns Engineering 2.4.2-beta.11 with ha-mcp 9.2.0.
- Target: tag `v2.4.2-beta.11`, commit
  23c5c4297fc0360f2cf46e62a384bbebd82d3015, image
  `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.4.2-beta.11`, OCI digest
  `sha256:e5e46d52a6db1bc4e02e207603d21dab57c7e6a03cc1030d03d9193199a294fd`
  (2.4.2-beta.11 acceptance, section 3, on `main`).
- Restoring returns the beta.11 defect: governed lifecycle applies are
  refused before dispatch (HAMCP-470).
- Export `get_audit_log` before any restore.
- In-place downgrade from beta.12 to beta.11 is not supported and is
  unproven.
- CI runs no in-place rollback lane: #461 retired it (HAMCP-405) and allows
  an empty rollback slot (HAMCP-406, restore-first recovery).
- No landed item states a persisted-format change. That is not a downgrade
  proof.
- **Safety.** Restoring the backup or snapshot returns Engineering, its
  storage and Home Assistant together to the backed-up state. It also
  reverts every Home Assistant change made since it was taken, including
  anything section 5 created or changed. Whether to restore, and which, is
  Josh's decision.
