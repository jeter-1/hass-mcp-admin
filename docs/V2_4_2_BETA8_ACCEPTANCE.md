# Engineering 2.4.2-beta.8 acceptance

**Status: release candidate.** This contract covers the 2.6.0 build,
released as 2.4.2-beta.8. It follows 2.4.2-beta.7 (HAMCP-331, #383). Its
content landed through item pull requests into the `release/2.6.0`
integration branch, and the release pull request (HAMCP-332) carries it to
`main`.

See also the release notes:
[`V2_4_2_BETA8_RELEASE_NOTES.md`](V2_4_2_BETA8_RELEASE_NOTES.md).

**Release target.** `2.4.2-beta.8`, following 2.4.2-beta.7. Josh chose
2.4.2-beta.8 on 2026-11-09 (HAMCP-354). The release transition accepts
`2.4.2-beta.7 -> 2.4.2-beta.8` or `2.4.2-beta.7 -> 2.4.2-rc.1` from the
current base (`scripts/release_transition.py`). It refuses
`2.4.2-beta.7 -> 2.6.0-beta.1`. "2.6.0" names the build, not the version
string.

**Why the version is materialized now.** `main` shipped 2.4.2-beta.7 (#383),
and CI validates every pull request against `origin/main` with a
materialized, unstaged release whatever its base
(`scripts/validate_addon_metadata.py`, `.github/workflows/ci.yml`). So
2.4.2-beta.8 is materialized once on `release/2.6.0`, in the catch-up that
merges `main` through #413 (HAMCP-354), and item pull requests carry no
version change of their own.

The release is materialized in all three version authorities, with no staged
declaration left. Nothing is published or deployed. Registry sequence 18 is
not signed.

The contract has seven stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed read-only acceptance, with one Engineering-store write;
5. write-tool refusal probes;
6. registry sequence 18 activation;
7. live 2.6 capability acceptance.

It authorizes none of the later stages.

## Items

Each item is reviewed in its own pull request into `release/2.6.0`. An item
that has not landed is not part of this release, and this contract makes no
claim about its behavior. Landed items are listed by their pull request; the
final release pull request binds their evidence.

| Item | PR | Scope | State |
| --- | --- | --- | --- |
| HAMCP-149 | #396 | Offline approval-completion fixture (tests and docs only) | landed |
| HAMCP-104 Slice A | #398 | Precise stale-state pre-dispatch refusal reasons | landed |
| HAMCP-294 | #397 | Bounded logbook window and entity/name filter | landed |
| HAMCP-267 | #400 | Integrity/incident/handoff consume the scene/group/dashboard reference store | landed |
| HAMCP-229 | #402 | Bounded automation reliability batch analysis (new tool) | landed |
| HAMCP-296 | #401 | Trace watch lifecycle | landed |
| HAMCP-143 | #412 | Bounded refusal diagnostics for dashboard compiler limits | landed |
| HAMCP-337 / HAMCP-338 | #411 | Entities-card row classification; E031 lock-tile risk fix | landed |
| HAMCP-354 | #416 | Catch-up with `main` through #413; version 2.4.2-beta.8 | landed |
| HAMCP-297, HAMCP-329, HAMCP-295, HAMCP-134, HAMCP-231, HAMCP-332 | #389, #393, #390, #392, #394, #391 | Design and investigation documents only | landed; no runtime claim |
| HAMCP-350 | #418 | Bounded startup readiness, phase timing, UTC log timestamps | landed |
| HAMCP-356 | #422 | `check.ps1` PR-base detection, worktree venv | landed; tooling only |
| HAMCP-348 | #420 | Publication handoff reads complete PR timeline | landed; tooling only |
| HAMCP-351 | #426 | Exact ha-mcp 9.1.2 admitted at 9.1.0 parity | landed; not exercised live (house on 8.5.0) |
| HAMCP-347 | #419 | Three beta.7 document errors corrected | landed; docs only |
| HAMCP-134 | #423 | Dormant security metadata read and sequence 18 lane | landed; live check after sequence 18 |
| HAMCP-264 | #430 | `dependency_index` YAML reader purpose | landed |
| HAMCP-361 | #431 | Test isolation: leaked governance state | landed; tests only |
| HAMCP-297 | #428 | Governed device-configuration entities (Matter) | landed; live check after sequence 18, gated (§7.7) |
| HAMCP-362 | #432 | Test isolation: application state in builders | landed; tests only |
| HAMCP-345 | #429 | Dashboard control classification (valve, group, header toggle, strategy) | landed |
| HAMCP-290 | #421 | `marketplace_read` exact-image lane, evidence group, manifest | landed; live check after sequence 18 |
| HAMCP-329 / HAMCP-298 | #424 | Dormant created-object profiles; governed automation/script create rollback | landed; automation rollback live at deploy, scene/helper after sequence 18 |
| HAMCP-231 | #427 | Dormant Thread dataset and Matter topology read | landed; live check after sequence 18 |
| HAMCP-366 | #434 | Device-configuration signing lane guards | landed |
| HAMCP-358 | #433 | 2.6 batched Lows cleanup | landed |
| HAMCP-364 | #435 | Lane clients bound to their disposable Core; seven sequence 18 lane receipts regenerated | landed; tests and lanes only |
| HAMCP-379 | #436 | Execution-task recovery containment | landed; fixed in this release |
| HAMCP-377 | #437 | Rollback proof lane; receipts 12a, 12b and 12c; CI rollback image 2.4.2-beta.7 | landed; all three receipts pass |
| HAMCP-352 | #415 (on `main`) | Evidence-before-review rule scoped by change type | landed through `1d430d6d`; instructions only |
| — | #395 | Sequence 18 signing plan and expected-values sheet A | folded through `3bd5fe3f`; sheet A refreshed at `41786cc0`; documents only |

Excluded from this release: #425 (HAMCP-295) and #388 (HAMCP-232).

The 2.5.0 build reached `release/2.6.0` through #410 (`release/2.5.0`) and
the HAMCP-354 catch-up with `main` through #413.

## 1. Source and offline acceptance

The release pull request binds, for each landed item:
- its owner decision and scope;
- its item pull request, reviewed head and review record;
- its required offline evidence, success and refusal paths, and negative
  reachability tests;
- a security section for each provider, write, workflow or persistence
  change.

It also binds:
- `2.4.2-beta.8` materialized, with exact document resolution;
- the final head SHA, its `validate` run and the single Evidence tier log
  named in the release pull request;
- the seven sequence 18 lane receipts committed by HAMCP-364 (#435); each
  lane is run again on the signing commit, and a profile whose lane fails
  there is dropped from sequence 18 (Josh's ruling R-a of 2026-11-13);
- the HAMCP-377 rollback proof receipts 12a, 12b and 12c in
  [`evidence/hamcp377/lane/`](evidence/hamcp377/lane/README.md), each a
  passing source-subject receipt, with the limits the release notes list.

The Evidence tier runs once, on the final release head.

## 2. Release CI

The exact materialized final head's `validate` check passes, including the
release transition.

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow.

## 4. Installed read-only acceptance (separately authorized, after deploy)

This stage runs only after Josh separately authorizes the deploy. Every
step is a read, except step 5, which writes only to the Engineering store.
Steps run from a new client session and are compared with the beta.7
baseline captured immediately before deploy.

1. **Identity** [READ]. `server_info` and `get_server_health` report:
   - `2.4.2-beta.8`;
   - build SHA = the merged release SHA (40 hex);
   - `build_dirty` false;
   - `schema_version` `"1"`;
   - the add-on runtime mode, as in the baseline;
   - Home Assistant connected, on Core 2026.11.0.
2. **Item checks** [READ]:
   - `get_logbook` with a bounded window and an entity filter returns only
     that window and entity (HAMCP-294).
   - `automation_reliability_batch_analysis` on two or three automations
     returns bounded results (HAMCP-229).
   - The add-on log shows the HAMCP-350 `startup_phase_timing` events,
     with `readiness_deadline_elapsed` false, as in runbook S7 (architect
     21:05 ruling B2).
   - `get_server_health` shows the `ordinary_device_configuration` block
     with no hold and no task, and Core 2026.11.0 admitted from signed
     sequence 17 with 32 references and zero contract mismatches.
   - `dashboard_integrity_analysis` on one dashboard completes; any
     compiler-limit refusal carries bounded diagnostics (HAMCP-143,
     HAMCP-337/338, HAMCP-345).
   - `get_marketplace_inventory`, `get_security_metadata` and
     `get_thread_matter_topology` each refuse with
     `pending_signed_registry_admission` and send nothing (dormant).
3. **Tool catalog** [READ]. A fresh `tools/list` in a new connector session
   has 114 tools: the 86 Engineering tools declared in the release notes,
   plus the same 28 delegated names as the beta.7 baseline.
   `ha_get_hacs_info` is absent. `server_info` reports:
   - `engineering_registered_tool_count` 86;
   - `canonical_tool_count` 25;
   - `dynamic_upstream_tool_count` 28;
   - `tool_count` 114.

   `upstream_catalog_fingerprint` equals the beta.7 baseline. (The stock
   upstream shape, without the optional YAML reader, is 86 + 27 = 113.)
4. **Provider attribution** [READ]. One delegated `ha_get_state` on a
   harmless sensor is attributed to ha-mcp with no fallback, as in the
   baseline. Engineering-native envelopes carry `provider: engineering`,
   `fallback_occurred: false`.
5. **Trace watch** [WRITES the Engineering store only; covered by Josh's
   single smoke-test approval]. `set_automation_trace_watch` on one
   automation: `add`, then `list` shows the watch; `remove`, then `list`
   shows it gone (HAMCP-296). Nothing is written to Home Assistant.

Values this contract does not fix (the exact health key names in step 2,
the catalog fingerprint, the scene `config_id` form, the helper arguments
and the Eve entity ids in §7) are taken from the beta.7 baseline capture
made immediately before deploy.

PASS needs every check. On a failure, record the response in the release
task and stop. Whether to roll back is Josh's decision.

## 5. Write-tool refusal probes (each separately approved)

Each probe calls a write-capable tool and expects a refusal before any
Home Assistant I/O. Each needs Josh's explicit approval, a harmless target,
and a readback that shows nothing changed. No probe may leave a pending or
approved plan: after each probe, `list_change_plans` shows none. Poll
`get_execution_task` no more often than every 10 seconds.

1. [MUTATES; Josh's explicit approval required] Before sequence 18:
   `create_scene_configuration_plan(action="create", …)` refuses with
   `pending_signed_registry_admission`. `list_change_plans` shows no new
   plan.
2. [MUTATES; Josh's explicit approval required] Before sequence 18:
   `control_device_configuration` on an admitted-kind Eve plug config
   entity refuses with `device_configuration_core_authority_unavailable`.
   The entity state is unchanged, and `get_audit_log` shows no dispatch.
3. [MUTATES; Josh's explicit approval required] After sequence 18 and
   before §7.7: `control_device_configuration` on a non-config `switch`, a
   diagnostic entity and a Tapo detection `select` each refuse (for
   example, `device_configuration_target_kind_not_admitted`). There is no
   dispatch in `get_audit_log`, and the states are unchanged.

## 6. Sequence 18 activation (after Josh signs)

Before Josh signs, the architect recomputes all 38 capability-reference
fingerprints on the `release/2.6.0` to `main` merge commit and compares them
with sheet A of the signing plan:
[`HAMCP_332_2_6_0_SIGNING_PLAN.md`](HAMCP_332_2_6_0_SIGNING_PLAN.md),
"5A.15 Expected-values sheet (for the dry-run comparison)", part
"A. Profiles added" (Josh's ruling R-d of 2026-11-13, §9D). Sheet A holds
the values at `3bd5fe3f`; this contract was written against the plan as of
`41786cc0`. A difference stops signing.

1. [READ] `get_server_health`: Core 2026.11.0 is admitted from signed
   sequence 18 with 38 references and zero contract mismatches. Each of the
   six profiles adds one reference to sequence 17's 32; if R-a dropped a
   profile, the count is one lower for each, and that profile's tool stays
   dormant.
2. [READ] `tools/list` is unchanged from §4.3, so no client refresh is
   needed.

## 7. Live 2.6 capability acceptance

Run in this order: reads 7.1-7.3, then writes 7.4, 7.5 and 7.6, then
device configuration (7.7) last. Stop on the first unexpected status.
Never retry an uncertain write with a new `operation_id`; read
`get_execution_task` instead, no more often than every 10 seconds. Finish
cleanup before any rollback decision.

Panel approval of a plan does not replace Josh's approval of the
acceptance step.

### 7.1 Thread/Matter topology (HAMCP-231) [READ]

1. `get_thread_matter_topology()` returns both sections, each with its own
   status:
   - datasets: id, network name, PAN identifiers, channel, preferred,
     source, created, and no credential material;
   - topology: the cached Matter graph, with LAN identifiers replaced by
     per-response references.
2. `sections=["datasets"]` returns only that section.
3. No MAC or extended address, BSSID, SSID, host name, dataset TLV or
   network key appears. References differ between two calls. Node
   `ha_device_id` values resolve through `list_devices` (spot-check the Eve
   plugs). The audit shows reads only.

### 7.2 Marketplace read (HAMCP-290) [READ]

1. `get_marketplace_inventory()` returns a page sorted by `full_name`, with
   `backend`, `total_matches`, `has_more`, `next_offset` and
   `inventory_fingerprint`.
2. Page through to `has_more` false. The row total equals `total_matches`,
   and the fingerprint is identical on every page.
3. A category filter and a query return subsets.
4. Josh compares the total with the UI by eye. `ha_get_hacs_info` stays
   absent.

### 7.3 Security metadata (HAMCP-134) [READ]

1. `get_security_metadata()` returns every section, with schema
   `security-metadata-v1`. `sections=["users"]` returns only users.
2. Users are pseudonymous. No URL text, token, credential or MFA secret
   appears. Missing fields read `not_provided`.
3. Josh checks the counts against Settings → People. The audit shows reads
   only and no Supervisor call.
4. The output is security-sensitive. Record PASS/FAIL only; keep the
   output out of shared documents and PR text.

### 7.4 Scene create and undo (HAMCP-329/298)

Gate: the HAMCP-377 12c receipt, committed and passing.

1. [READ] Record the pre-state: scene count, no scene named
   `HAMCP 2.6 acceptance scene`, and one harmless `input_boolean` member and
   its state.
2. [MUTATES; Josh's explicit approval required]
   `create_scene_configuration_plan(action="create", …)` with that single
   member at its current state. A plan is returned.
3. [MUTATES; Josh's explicit approval required] Josh approves the exact
   hash in the panel, then `apply_change_plan`. Result:
   `succeeded_verified`, and the scene is not activated.
4. [READ] Readback:
   - stored name and member are as planned;
   - the member state is unchanged;
   - the scene count is the pre-state count plus 1;
   - `created_identity` is `captured`.
5. [MUTATES; Josh's explicit approval required] `rollback_change`, panel
   approval, apply. Result: `succeeded_verified`.
6. [READ] The scene is absent and the count is back to the pre-state. The
   audit shows one create and one delete.

If `created_identity` is not `captured`, stop. Josh deletes the test scene
in the Home Assistant UI.

### 7.5 Created helper delete (HAMCP-298)

Gate: the HAMCP-377 12c receipt, committed and passing.

1. [READ] Record the pre-state: helper count, and that
   `input_boolean.hamcp_2_6_acceptance` does not exist.
2. [MUTATES; Josh's explicit approval required] Create the helper through
   `create_configuration_plan`, with panel approval, then apply. Result:
   `succeeded_verified`.
3. [READ] The helper is present and off, and `created_identity` is
   `captured`. Wait at least 10 s.
4. [MUTATES; Josh's explicit approval required] `rollback_change`, panel
   approval, apply. Result: `succeeded_verified`.
5. [READ] The helper is absent from the list, the states and the registry.
   The count is back to the pre-state, and the audit shows one create and
   one delete.

Do not reference the test helper from any automation, dashboard or scene.

### 7.6 Automation create rollback (HAMCP-298 slice 2)

This step runs on the already-signed delete profile.

1. [READ] Record the automation count and confirm the test alias is
   absent.
2. [MUTATES; Josh's explicit approval required] Create one throwaway
   automation with no trigger that can fire, with panel approval, then
   apply. Result: `succeeded_verified`.
3. [READ] The automation is present as planned.
4. [MUTATES; Josh's explicit approval required] `rollback_change`, panel
   approval, apply. Result: `succeeded_verified`.
5. [READ] The automation is absent, the count is back to the pre-state, and
   the audit shows one create and one delete.

### 7.7 Device configuration on the Garage Camera Smart Plug (HAMCP-297)

Gates: the HAMCP-377 12b receipt, committed and passing, and the HAMCP-379
recovery containment, which ships in this release.

Target: only `select.garage_garage_camera_smart_plug_power_on_behavior`
(Garage Camera Smart Plug), its power-on behavior. Josh chose it on
2026-11-13 (about 19:10 CST) because both Eve plugs named earlier (master
bedroom TV and kitchen) were offline on the deploy day (F063). Power-on
behavior applies only after a mains power loss, and the test never switches
the plug; `"on"` is a safe fallback for a camera if the restore fails. This
section and runbook step 9.8 must not disagree (architect 19:25 ruling 2;
the config-category check and the removal of the old physical-confirmation
step were accepted in the architect's 19:30 note).

Never: the network cabinet plug; the back-porch Aqara sensitivity (that
step was dropped and stays dropped); the offline Eve plugs; the beer fridge
plug.

No plan or panel exists for this tool. Each mutating step needs its own
approval from Josh in chat at Phase 9 time; a blanket approval does not
count (D21).

1. [READ] From the registry, confirm the target is `config` / `select` /
   `matter` / `startup_on_off` and its current option is `"previous"`.
   Record the option list and the plug's main switch state. Anything else:
   stop and tell Josh; do not pick another target.
2. [MUTATES; Josh's explicit approval required, this step only]
   `select_option` from `"previous"` to `"on"`. Result: `succeeded_verified`,
   reading back exactly `"on"`, with an `undo` field. If the result is
   `confirmation_required`, nothing was sent; relay the reason to Josh.
3. [READ] The option reads exactly `"on"`, the main switch equals its
   recorded state, and the task is terminal and verified.
4. [MUTATES; Josh's explicit approval required, this step only] Restore
   with `undo`. Result: `succeeded_verified`, with `undone` true, reading
   back exactly `"previous"`. If `undo` refuses, or ends other than
   `succeeded_verified` with `undone` true: stop and tell Josh. No automatic
   second `select_option` to `"previous"`; that needs its own approval.
5. [READ] Final check:
   - the option reads exactly `"previous"` and the main switch equals its
     recorded state;
   - `ordinary_device_configuration` shows no hold and no task;
   - `list_execution_tasks` shows no non-terminal task.

An uncertain result at any step: poll `get_execution_task` at most every
10 s. Never resend.

Verification is the device's reported value read back through Home
Assistant; actual power-on behavior after a mains power loss is not tested
(architect 19:30 note).

Not live-tested in 2.6: the child-lock switch (step removed by Josh's 19:10
decision) and the number path (`set_value`).

## Rollback

Whether to roll back is Josh's decision. The release notes' Rollback
section governs: restore of the full pre-deploy backup as the primary
recovery, the Engineering-only restore as the secondary, the audit export
before either, and the one-way sequence 18 statement. In-place rollback
to 2.4.2-beta.7 is not supported.

- The full restore is untested here. It also reverts the Home Assistant
  objects created during acceptance.
- The Engineering-only restore does not revert Home Assistant: complete
  the pre-restore list and cleanup in the release notes first.
- After signing: sequence 18 stays signed and is outside the backup.
  2.4.2-beta.7 admits 33 of the 38 references (12a).
- After any restore: refresh clients, and confirm `server_info` reports
  2.4.2-beta.7 and a catalog of 110.
